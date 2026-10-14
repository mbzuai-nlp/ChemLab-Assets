"""CHEM-157 J.A. Woollam alpha-SE spectroscopic ellipsometer (2015 specification: rotating compensator ellipsometer with CCD detection,
380-900 nm at 180 wavelengths, angles of incidence 65 / 70 / 75 deg off the sample and 90 deg straight through, fast / standard / long
acquisition of 3 / 10 / 30 s, beam about 2 mm, samples 5-200 mm up to 16 mm thick, vacuum chuck, automated Z alignment, optional 500 uL
liquid cell at 70 deg): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * power and lamp: mains switch (green push button), software initialisation after power-up, quartz tungsten halogen lamp with a warm-up
    of its intensity (the intensity drifting during a compensator cycle biases the Fourier analysis until it has settled), lamp hours and
    end of life;
  * mechanics: angle of incidence changed by hand with the spring-loaded AOI plunger (the source and detector arms turn together about the
    measurement point; 65 / 70 / 75 / 90 deg positions whose machined angles differ from the nominal ones by tenths of a degree, a hidden
    truth), motorized Z stage for the sample platform (auto alignment on the reflected beam, fixed or prompted height, lowered below the
    beam for straight-through), sample vacuum switch and chuck (external vacuum supply), collision guard between a raised or large sample and
    the arms;
  * optics of the sample (truth): isotropic multilayer stacks (transfer matrix, exact Fresnel coefficients) of the template library materials
    (optical_constants_library.json: measured n, k from refractiveindex.info, CC0) or dispersion models (Cauchy with Urbach tail, Sellmeier,
    Tauc-Lorentz, Lorentz oscillators, Drude, Bruggeman effective medium for roughness and mixtures), incoherent backside reflections of
    transparent substrates that reach the detector aperture, thickness non-uniformity over the beam footprint, spectral bandwidth of a CCD
    pixel and the beam divergence (all four depolarize), ambient air or the liquid of the optional liquid cell;
  * instrument: unpolarised QTH spectrum, fixed calcite polarizer, sample, step-scan rotating compensator (spectroscopic retardance), fixed
    analyzer, spectrometer with a CCD (quantum efficiency, full well, shot and read noise, room light as an offset); the detected intensity at
    every compensator step is simulated and Fourier analysed with the CALIBRATED instrument parameters, so errors of the calibration
    (angle of incidence, polarizer / analyzer azimuths, compensator retardance) propagate like on the real instrument; Psi (0-90 deg), Delta
    (0-360 deg), N / C / S, depolarization, reflectance relative to a straight-through baseline, 11 normalised Mueller-matrix elements (MM
    modes, three polarizer azimuths: engineering assumption);
  * calibration: straight-through calibration of the polarizer / analyzer azimuths and the compensator retardance, and the angle of incidence
    of the current position from the calibration wafer (thermal oxide on silicon); straight-through check against the accuracy specification;
  * analysis (CompleteEASE-like): model library and custom models, Levenberg-Marquardt regression of N, C, S with the J.A. Woollam MSE
    (1000 sqrt(sum (dN^2 + dC^2 + dS^2) / (3 n - m))), thickness pre-fit (global search), 90 % confidence limits and correlations, n / k at
    632.8 nm, pseudo optical constants, uniqueness scans (MSE versus a parameter with the others refitted);
  * fault injection (evaluator): lamp failure, vacuum leak, Z-stage stall, AOI plunger not seated;
  * ledgers: electrical energy, lamp on-time and hours, stage travel, AOI changes, vacuum time, liquid volumes (added, drained, in the cell).
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
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


def obj(properties, optional=()):
    return dict(type='object', properties=properties, required=[k for k in properties if k not in optional], additionalProperties=False)


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


def enum_number(values, unit='deg'):
    return {'type': 'number', 'x-unit': unit, 'enum': list(values)}


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


# ---------------------------------------------------------------- identity, paths
PROTOCOL_VERSION = '1.0.0'
SLUG = 'ja_alphase'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-157'
ROOT = Path(__file__).resolve().parents[2]
LIBRARY_PATH = ROOT / 'assets/expansion_batch04/t3_analytical/ja_alphase/optical_constants_library.json'

# ---------------------------------------------------------------- documented instrument (2015 specification sheet; workbook row 162)
BODY_DIMENSIONS_M = (0.4826, 0.3048, 0.3048)      # W 19 x D 12 x H 12 in (p. 5); the front-view drawing gives 20 in [52 cm] with the arm caps
OVERALL_WIDTH_M = 0.520                            # front-view drawing, straight-through pose: the caps at the arm ends stand out 1.9 cm per side
MASS_KG = 17.69                                    # 39 lb, not including the computer (p. 5)
SPECTRAL_RANGE_NM = (380.0, 900.0)                 # 180 wavelengths (p. 3); pixel resolution 0.01 eV
N_WAVELENGTHS = 180
AOI_POSITIONS_DEG = (65.0, 70.0, 75.0, 90.0)       # 65 / 70 / 75 off sample, 90 straight through (p. 3)
AOI_LABELS = ['65', '70', '75', '90']             # position names of the AOI plunger (nominal angle in degrees as a label; values in the API are rad)
MODE_TIME_S = {'fast': 3.0, 'standard': 10.0, 'long': 30.0, 'mm': 10.0, 'mm_long': 30.0}   # complete spectrum (p. 3; SOP: M.M. / M.M. Long modes)
BEAM_DIAMETER_M = 0.002                            # approximately 2 mm (p. 3; the user-facility SOPs quote about 3 mm)
SAMPLE_SIZE_RANGE_M = (0.005, 0.200)               # 5 mm up to 200 mm (p. 3)
SUBSTRATE_MAX_THICKNESS_M = 0.016                  # maximum substrate thickness (p. 3)
MAINS_RANGE_V = (100.0, 240.0)                     # 100/240 VAC, 47-63 Hz, < 1 A (p. 5)
MAINS_CURRENT_MAX_A = 1.0
LIQUID_CELL_VOLUME_M3 = 500e-9                     # optional liquid cell: 500 uL, 70 deg, glass slides and 1 or 2 in wafers (p. 4)
LIQUID_CELL_AOI_DEG = 70.0
ACCURACY_PSI_DEG, ACCURACY_DELTA_DEG = 0.1, 0.2    # straight-through empty beam, 98 % of the wavelengths, 10 s (p. 3)
REPEATABILITY_2NM_SIO2_M = 0.01e-9                 # 30 repeated 10 s measurements of 2 nm SiO2 / Si at 70 deg, 1 sigma (p. 3)

# ---------------------------------------------------------------- geometry shared with the 3D asset (asset frame: origin at the bench projection of the body centre,
# +X right, +Y back, +Z up; proportions measured on the front / side drawings of p. 5 at 16.95 px/cm and 13.4 px/cm)
BASE_TOP_Z_M = 0.056                               # rubber feet 36 mm + base plate 20 mm
MEASURE_POINT_M = (0.0, -0.0144, 0.134)            # beam crossing point on the sample surface: 13.8 cm behind the front edge, 7.8 cm above the base plate
ARM_PORT_R_M = (0.089, 0.107)                      # beam port of each arm, distance from the measurement point along the arm axis
ARM_BODY_R_M = (0.107, 0.237)                      # arm housing
ARM_CAP_R_M = (0.237, 0.260)                       # lamp cap (source) / fibre lock (detector) at the outer end
ARM_HALF_HEIGHT_M = 0.037
PORT_HALF_M = 0.012
ARM_Y_RANGE_M = (-0.080, 0.040)
BACK_PLATE_Y_M = (0.0506, 0.0806)                  # front panel of the rear housing (the AOI scale is printed on it)
PLATFORM_X_M = (-0.0765, 0.0765)                   # sample platform on the Z stage (15.3 cm wide; photo proportions)
PLATFORM_Y_M = (-0.140, 0.030)                     # front edge 12 mm behind the base front; rear edge on the Z carriage in front of the guide
PLATFORM_T_M = 0.013
Z_GUIDE_FRONT_Y_M = 0.040                          # front face of the Z guide (its top is above the platform: a sample must end 2 mm in front of it); 54.4 mm behind
                                                   # the measurement point so that a 4 in wafer lies centred on the stage (brochure photo 'alpha-SE with 4 in. wafer')
STAGE_Z_RANGE_M = (MEASURE_POINT_M[2] - 0.020, MEASURE_POINT_M[2])   # platform top height (engineering 20 mm travel; drawing: 14.8 mm below the beam for straight-through)
STAGE_Z_REF_M = MEASURE_POINT_M[2] - 0.0148        # joint zero of the Z stage: the pose of the drawing
STAGE_Z_SPEED_M_S = 0.002                          # engineering
ARM_SPEED_RAD_S = math.radians(10.0)               # hand-guided arm change (engineering)
CLEARANCE_M = 0.002                                # minimum clearance of a sample to the arms / ports (collision guard)
ARM_CAP_HALF_M = 0.016                             # radius of the larger end cap (detector arm) - the guard uses it for both arms

# ---------------------------------------------------------------- engineering values of the process model (not published)
HC_EV_NM = 1239.841984
BOOT_S = 8.0                                       # electronics and compensator homing after power-up
LAMP_WARMUP_TAU_S = 90.0                           # QTH intensity settles with this time constant after the lamp is switched on
LAMP_WARMUP_DEFICIT = 0.25                         # relative intensity deficit right after switch-on
LAMP_RATED_S = 2000.0 * 3600.0                     # QTH rated life (2000 h)
LAMP_END_OF_LIFE_LOSS = 0.30                       # relative intensity loss at the rated life
LAMP_POWER_W = 20.0
ELECTRONICS_POWER_W = 18.0
LAMP_TEMPERATURE_K = 3100.0
POLARIZER_AZIMUTH_DEG = 45.0
ANALYZER_AZIMUTH_DEG = 45.0
POLARIZER_MM_AZIMUTHS_DEG = (0.0, 45.0, 90.0)      # MM modes (assumption: the polarizer is stepped to three azimuths)
COMPENSATOR_STEPS = 16                             # step-scan positions per revolution
COMPENSATOR_DELTA0_DEG, COMPENSATOR_LAMBDA0_NM = 90.0, 600.0   # retardance delta(l) = delta0 (l0 / l)
CCD_FULL_WELL_E = 1.0e6                            # effective full well of a fully vertically binned spectrometer column
CCD_READ_NOISE_E = 15.0
CCD_TARGET_FILL = 0.7                              # auto exposure: brightest pixel at 70 % of the full well
STEP_OVERHEAD_S = 0.010                            # compensator step and CCD read-out per position
SOURCE_PHOTON_RATE = 1.3e8                         # photo-electrons / s in the brightest spectrometer column for m11 = 1 (photon-limited noise: tuned so that
                                                   # 2 nm SiO2 / Si at 70 deg, 10 s repeats to <= 0.01 nm and the straight-through beam meets 0.1 / 0.2 deg)
BALANCE_FLOOR, BALANCE_WIDTH_NM = 0.15, 180.0         # colour-balancing filter: transmission 1 at 380 nm falling to 0.15 in the red / near infrared
ROOM_LIGHT_FRACTION = 0.002                        # room light at the CCD relative to the lamp (RCE rejects it except for its shot noise)
BEAM_DIVERGENCE_RAD = math.radians(0.15)           # 1 sigma angular spread of the collimated beam
DETECTOR_APERTURE_SIGMA_M = 0.0012                 # lateral acceptance of the detector aperture for displaced beams (1 sigma)
ALIGN_WINDOW_M = {'standard': 0.003, 'robust': None}    # search half-window about the previous height; robust searches the full travel
ALIGN_TIME_S = {'none': 0.5, 'standard': 4.0, 'robust': 14.0, 'fixed_height': 1.0}
ALIGN_PRECISION_M = 3e-6
ALIGN_MIN_SIGNAL = 0.01                            # minimum relative reflected intensity the alignment detector can lock on
ALIGN_MAX_LOSS = 0.2                               # a height error losing more than this fraction of the reflected beam fails the measurement
FIT_TIME_S = 1.5                                   # model fit after the acquisition (workstation)
VACUUM_HOLD_PA = 60.0e3                            # absolute pressure of the supply below which the chuck holds the sample
TRUE_AOI_SIGMA_DEG = 0.15                          # machined angles differ from the nominal by tenths of a degree (hardware manual)
FACTORY_AOI_ERROR_DEG = 0.01                       # residual error of the factory calibration of each angle
TRUE_AZIMUTH_SIGMA_DEG = 0.08                      # polarizer / analyzer mounting error before calibration
TRUE_RETARDANCE_SIGMA_DEG = 0.6                    # compensator retardance error before calibration
FACTORY_AZIMUTH_ERROR_DEG, FACTORY_RETARDANCE_ERROR_DEG = 0.01, 0.05
MAX_RECORDS = 200
MIN_REFLECTED_SIGNAL = 1e-4                        # detected intensity relative to the straight-through beam of a new lamp below which no data are taken
QUAD_TOL = 1e-5                                    # depolarization averages: refine until N, C, S change by less than this between levels (truth; noise ~1e-3)
QUAD_TOL_MODEL = 2e-5                              # the same for the analysis models
QUAD_BUDGET = 1.0e8                                # work cap (nodes x wavelengths x (layers + 1)) of one averaged spectrum of the truth; each block of
                                                   # 20 wavelengths may use twice its share (a few seconds; beyond it the measurement fails: SIMULATION_LIMIT)
QUAD_BUDGET_MODEL = 1.0e7                          # the same for one model evaluation inside a fit (about 0.5 s; beyond it the fit is flagged)
PHASE_Z, BAND_Z = 6.0, 5.0                         # truncation |z| <= Z of the normal variables (phase perturbations / pixel band)
QUAD_CHUNK = 1_500_000                             # elements per vectorized stack evaluation (memory)
NONUNIFORMITY_MAX = 0.1                            # relative thickness non-uniformity inside the beam footprint (1 sigma) the sample model accepts
LIBRARY_EXTRAPOLATION_NM = 25.0                    # pixel-band nodes reach 18 nm beyond the 380-900 nm tables: the end intervals continue linearly


# ---------------------------------------------------------------- spectral grid of the CCD spectrometer (equal steps in photon energy)
E_GRID_EV = np.linspace(HC_EV_NM / SPECTRAL_RANGE_NM[1], HC_EV_NM / SPECTRAL_RANGE_NM[0], N_WAVELENGTHS)
WAVELENGTHS_NM = np.sort(HC_EV_NM / E_GRID_EV)
PIXEL_WIDTH_EV = float(E_GRID_EV[1] - E_GRID_EV[0])
BANDWIDTH_SIGMA_EV = PIXEL_WIDTH_EV / 2.355        # FWHM of one pixel (engineering)


def wavelengths_nm():
    return WAVELENGTHS_NM.copy()


# ---------------------------------------------------------------- template library (optical constants)
@functools.lru_cache(maxsize=1)
def library():
    lib = json.loads(LIBRARY_PATH.read_text())
    return dict(meta={k: v for k, v in lib.items() if k != 'substances'}, subs={s['substance_id']: s for s in lib['substances']},
                grid=np.asarray(lib['grid']['wavelength_nm'], float))


def substance(sid):
    s = library()['subs'].get(sid)
    if s is None:
        raise ActionRejected('UNKNOWN_MATERIAL')
    return s


def _table_linear(g, y, lam):
    """Linear interpolation of a table, continued linearly with the end intervals outside it."""
    v = np.interp(lam, g, y)
    lo, hi = lam < g[0], lam > g[-1]
    if lo.any():
        v = np.where(lo, y[0] + (lam - g[0]) * (y[1] - y[0]) / (g[1] - g[0]), v)
    if hi.any():
        v = np.where(hi, y[-1] + (lam - g[-1]) * (y[-1] - y[-2]) / (g[-1] - g[-2]), v)
    return v


@functools.lru_cache(maxsize=512)
def _library_nk(sid, lam_key):
    s = substance(sid)
    g = library()['grid']
    lam = np.asarray(lam_key, float)
    if lam.min() < g[0] - LIBRARY_EXTRAPOLATION_NM - 1e-9 or lam.max() > g[-1] + LIBRARY_EXTRAPOLATION_NM + 1e-9:
        raise ActionRejected('OUT_OF_RANGE')
    n = np.maximum(_table_linear(g, np.asarray(s['n'], float), lam), 1e-3)
    k = np.maximum(_table_linear(g, np.asarray(s['k'], float), lam), 0.0)
    return n + 1j * k


def library_index(sid, lam_nm):
    """Complex refractive index N = n + i k of a library material on lam_nm (linear interpolation of the 2 nm table; up to 25 nm beyond the
    380-900 nm range - only the pixel-band nodes of the edge pixels go there - the end intervals continue linearly, n >= 0.001, k >= 0)."""
    lam = np.asarray(lam_nm, float)
    out = _library_nk(sid, tuple(np.round(lam.ravel(), 9)))
    return out.reshape(lam.shape) if lam.ndim > 1 else out


# ---------------------------------------------------------------- dispersion models (energies in eV, wavelengths in nm; eps = N^2)
def cauchy_index(lam_nm, A, B, C=0.0, k_amp=0.0, k_exp=1.5, band_edge_ev=3.26):
    """Cauchy n = A + B / l^2 + C / l^4 (l in m, B in m^2, C in m^4; CompleteEASE quotes B in um^2 = 1e-12 m^2 and C in um^4 = 1e-24 m^4) with
    the Urbach absorption tail k = k_amp exp(k_exp (E - band edge)) (energies in eV)."""
    lm = np.asarray(lam_nm, float) * 1e-9
    E = HC_EV_NM / np.asarray(lam_nm, float)
    return (A + B / lm ** 2 + C / lm ** 4) + 1j * (k_amp * np.exp(k_exp * (E - band_edge_ev)))


def sellmeier_index(lam_nm, A, B, lam0_m, E_ir=0.0):
    """n^2 = A + B l^2 / (l^2 - l0^2) - E l^2 (l and l0 in m, E in 1/m^2), transparent."""
    lm = np.asarray(lam_nm, float) * 1e-9
    return np.sqrt(A + B * lm ** 2 / (lm ** 2 - lam0_m ** 2) - E_ir * lm ** 2).astype(complex)


def lorentz_eps(E, eps_inf, oscillators):
    """eps = eps_inf + sum A B E0 / (E0^2 - E^2 - i E B) (A dimensionless, E0 and B in eV)."""
    E = np.asarray(E, float)
    eps = np.full(E.shape, eps_inf, complex)
    for A, E0, B in oscillators:
        eps = eps + A * B * E0 / (E0 ** 2 - E ** 2 - 1j * E * B)
    return eps


def drude_eps(E, eps_inf, Ep, gamma):
    E = np.asarray(E, float)
    return eps_inf - Ep ** 2 / (E ** 2 + 1j * gamma * E)


_TL_XI = np.concatenate([np.linspace(0.02, 12.0, 3000), np.geomspace(12.02, 400.0, 1200)])


def _tl_eps2(x, Eg, A, E0, C):
    x = np.asarray(x, float)
    v = A * E0 * C * (x - Eg) ** 2 / ((x ** 2 - E0 ** 2) ** 2 + C ** 2 * x ** 2) / x
    return np.where(x > Eg, v, 0.0)


def tauc_lorentz_eps(E, eps_inf, Eg, A, E0, C):
    """Jellison-Modine Tauc-Lorentz: eps2 = A E0 C (E - Eg)^2 / ((E^2 - E0^2)^2 + C^2 E^2) / E above Eg; eps1 from the closed-form Kramers-Kronig
    integral of Jellison and Modine (1996, with the erratum) for C < 2 E0, otherwise (overdamped) the numerical principal value with the
    singularity subtracted and its analytic part over the integration range added back."""
    E = np.asarray(E, float)
    e2 = _tl_eps2(E, Eg, A, E0, C)
    a2 = 4.0 * E0 ** 2 - C ** 2
    if a2 > 1e-9:
        Ex = np.where(np.abs(E - Eg) < 1e-9, Eg + 1e-9, E)
        al = math.sqrt(a2)
        g2 = E0 ** 2 - C ** 2 / 2.0
        z4 = (Ex ** 2 - g2) ** 2 + a2 * C ** 2 / 4.0
        a_ln = (Eg ** 2 - E0 ** 2) * Ex ** 2 + Eg ** 2 * C ** 2 - E0 ** 2 * (E0 ** 2 + 3.0 * Eg ** 2)
        a_at = (Ex ** 2 - E0 ** 2) * (E0 ** 2 + Eg ** 2) + Eg ** 2 * C ** 2
        eps1 = (eps_inf
                + A * C * a_ln / (2.0 * math.pi * z4 * al * E0) * math.log((E0 ** 2 + Eg ** 2 + al * Eg) / (E0 ** 2 + Eg ** 2 - al * Eg))
                - A * a_at / (math.pi * z4 * E0) * (math.pi - math.atan((2.0 * Eg + al) / C) + math.atan((al - 2.0 * Eg) / C))
                + 4.0 * A * E0 * Eg * (Ex ** 2 - g2) / (math.pi * z4 * al) * (math.atan((al + 2.0 * Eg) / C) + math.atan((al - 2.0 * Eg) / C))
                - A * E0 * C * (Ex ** 2 + Eg ** 2) / (math.pi * z4 * Ex) * np.log(np.abs(Ex - Eg) / (Ex + Eg))
                + 2.0 * A * E0 * C * Eg / (math.pi * z4) * np.log(np.abs(Ex - Eg) * (Ex + Eg) / math.sqrt((E0 ** 2 - Eg ** 2) ** 2 + Eg ** 2 * C ** 2)))
        return eps1 + 1j * e2
    xi = _TL_XI
    lo, hi = float(xi[0]), float(xi[-1])
    f_xi = xi * _tl_eps2(xi, Eg, A, E0, C)
    fE = E * e2
    den = xi[None, :] ** 2 - E[:, None] ** 2
    integrand = np.where(np.abs(den) > 1e-12, (f_xi[None, :] - fE[:, None]) / np.where(np.abs(den) > 1e-12, den, 1.0), 0.0)
    pv_const = (np.log(np.abs((hi - E) / (hi + E))) - np.log(np.abs((E - lo) / (E + lo)))) / (2.0 * E)   # PV of 1 / (x^2 - E^2) on [lo, hi]
    eps1 = eps_inf + (2.0 / math.pi) * (np.trapezoid(integrand, xi, axis=1) + fE * pv_const)
    return eps1 + 1j * e2


def bruggeman(eps_a, eps_b, f_b):
    """Bruggeman effective medium of two components (volume fraction f_b of b): 2 eps^2 - b eps - eps_a eps_b = 0, physical root."""
    f_a = 1.0 - f_b
    b = f_a * (2 * eps_a - eps_b) + f_b * (2 * eps_b - eps_a)
    root = np.sqrt(b * b + 8 * eps_a * eps_b)
    e1, e2 = (b + root) / 4.0, (b - root) / 4.0
    lin = f_a * eps_a + f_b * eps_b
    pick = np.where(np.imag(e1) >= -1e-12, e1, e2)
    pick = np.where((np.imag(e1) >= -1e-12) & (np.imag(e2) >= -1e-12), np.where(np.abs(e1 - lin) <= np.abs(e2 - lin), e1, e2), pick)
    return pick


def material_index(spec, lam_nm):
    """Complex index of a material specification on lam_nm: {'library': id} | {'cauchy': {...}} | {'sellmeier': {...}} |
    {'tauc_lorentz': {...}} | {'lorentz': {...}} | {'drude': {...}} | {'ema': {'a': id, 'b': id or 'void', 'fraction_b': f}} | 'void'."""
    lam = np.atleast_1d(np.asarray(lam_nm, float))
    if spec == 'void':
        return np.ones(lam.shape, complex)
    (kind, p), = spec.items()
    E = HC_EV_NM / lam
    if kind == 'library':
        return library_index(p, lam)
    if kind == 'cauchy':
        return cauchy_index(lam, p['A'], p['B'], p.get('C', 0.0), p.get('k_amp', 0.0), p.get('k_exp', 1.5), p.get('band_edge_ev', 3.26))
    if kind == 'sellmeier':
        return sellmeier_index(lam, p['A'], p['B'], p['lam0_m'], p.get('E_ir', 0.0))
    if kind == 'tauc_lorentz':
        eps = tauc_lorentz_eps(E, p['eps_inf'], p['Eg_ev'], p['A'], p['E0_ev'], p['C_ev'])
    elif kind == 'lorentz':
        eps = lorentz_eps(E, p['eps_inf'], [(o['A'], o['E0_ev'], o['B_ev']) for o in p['oscillators']])
    elif kind == 'drude':
        eps = drude_eps(E, p['eps_inf'], p['Ep_ev'], p['gamma_ev'])
    elif kind == 'drude_lorentz':                     # analysis models of metals (Drude + one interband oscillator); not a sample material
        eps = drude_eps(E, p['eps_inf'], p['Ep_ev'], p['gamma_ev']) + lorentz_eps(E, 0.0, [(p['osc_A'], p['osc_E0_ev'], p['osc_B_ev'])])
    elif kind == 'ema':
        ea = material_index({'library': p['a']} if p['a'] != 'void' else 'void', lam) ** 2
        eb = material_index({'library': p['b']} if p['b'] != 'void' else 'void', lam) ** 2
        eps = bruggeman(ea, eb, p['fraction_b'])
    else:
        raise ActionRejected('UNKNOWN_MATERIAL')
    N = np.sqrt(eps)
    return np.where(np.imag(N) < 0, -N, N)


# ---------------------------------------------------------------- optics of an isotropic multilayer (transfer matrix, exact Fresnel)
def _q(N, kx):
    q = np.sqrt(N * N - kx * kx + 0j)
    return np.where(np.imag(q) < -1e-15, -q, np.where((np.abs(np.imag(q)) <= 1e-15) & (np.real(q) < 0), -q, q))


def stack_rps(lam_nm, N0, layers, Ns, theta0):
    """Reflection coefficients (r_p, r_s) of ambient N0 / layers [(N_j, d_j m), top first] / substrate Ns at the angle of incidence theta0
    (rad, in the ambient); arrays broadcast over wavelengths (and thickness samples).  Convention: N = n + i k, r_p = (N_t^2 q_i - N_i^2 q_t) /
    (N_t^2 q_i + N_i^2 q_t) (r_p = -r_s at normal incidence, Delta = 180 deg there)."""
    lam_m = np.asarray(lam_nm, float) * 1e-9
    N0 = np.real(N0) + 0j                   # the ambient is transparent (a liquid's tiny absorption would make kx complex and the branch of q ambiguous)
    kx = N0 * np.sin(theta0)
    media = [N0] + [N for N, _ in layers] + [Ns]
    qs = [_q(N, kx) for N in media]

    def iface(i, j):
        Ni, Nj, qi, qj = media[i], media[j], qs[i], qs[j]
        return (Nj * Nj * qi - Ni * Ni * qj) / (Nj * Nj * qi + Ni * Ni * qj), (qi - qj) / (qi + qj)
    rp, rs = iface(len(media) - 2, len(media) - 1)
    for j in range(len(layers), 0, -1):
        d = layers[j - 1][1]
        ph = np.exp(2j * (2 * math.pi / lam_m) * qs[j] * d)
        a_p, a_s = iface(j - 1, j)
        rp = (a_p + rp * ph) / (1 + a_p * rp * ph)
        rs = (a_s + rs * ph) / (1 + a_s * rs * ph)
    return rp, rs


def rncs(rp, rs):
    """(R, N, C, S) of a reflection: R = (|rp|^2 + |rs|^2) / 2, N = cos 2Psi, C = sin 2Psi cos Delta, S = sin 2Psi sin Delta.  Delta follows
    the Nebraska convention of J.A. Woollam (rho = tan Psi exp(i Delta) with Delta decreasing from 180 deg as a thin film grows on silicon):
    with the exp(-i w t), N = n + i k convention of stack_rps that is the conjugate phase, so S = -2 Im(rp rs*) / (|rp|^2 + |rs|^2)."""
    ap, as_ = np.abs(rp) ** 2, np.abs(rs) ** 2
    tot = ap + as_
    x = rp * np.conj(rs)
    return tot / 2.0, (as_ - ap) / tot, 2 * np.real(x) / tot, -2 * np.imag(x) / tot


def psi_delta(N, C, S):
    """Psi in [0, 90] deg and Delta in [0, 360) deg of (possibly depolarized) N, C, S."""
    psi = 0.5 * np.degrees(np.arctan2(np.hypot(C, S), N))
    delta = np.degrees(np.arctan2(S, C)) % 360.0
    return psi, delta


def ncs_of(psi_deg, delta_deg):
    p, d = np.radians(psi_deg), np.radians(delta_deg)
    return np.cos(2 * p), np.sin(2 * p) * np.cos(d), np.sin(2 * p) * np.sin(d)


def mueller_isotropic(N, C, S):
    """Normalised Mueller matrix of an isotropic (possibly depolarizing) reflection: [[1, -N, 0, 0], [-N, 1, 0, 0], [0, 0, C, S], [0, 0, -S, C]]."""
    N, C, S = np.broadcast_arrays(np.asarray(N, float), np.asarray(C, float), np.asarray(S, float))
    M = np.zeros(N.shape + (4, 4))
    M[..., 0, 0] = M[..., 1, 1] = 1.0
    M[..., 0, 1] = M[..., 1, 0] = -N
    M[..., 2, 2] = M[..., 3, 3] = C
    M[..., 2, 3] = S
    M[..., 3, 2] = -S
    return M


# ---------------------------------------------------------------- characteristic-matrix (Abeles) stack: reflection and transmission from both sides
def _admittance(N, q, pol):
    return q if pol == 's' else N * N / q


def _stack_rt(lam_nm, N_in, layers, N_out, kx, pol):
    """Amplitude reflection r, amplitude transmission t (tangential fields) and power transmission T of the stack (N_in / layers / N_out, layers
    listed from the incidence side) for one polarization; admittances eta_s = q, eta_p = N^2 / q with q = sqrt(N^2 - kx^2); p reflection in the
    convention of stack_rps.  For a path that starts and ends in the same medium the product of the t of its passes carries the correct power
    and the p-s phase of the transmissions (the admittance factors cancel)."""
    lam_m = np.asarray(lam_nm, float) * 1e-9
    q_in, q_out = _q(N_in, kx), _q(N_out, kx)
    e_in, e_out = _admittance(N_in, q_in, pol), _admittance(N_out, q_out, pol)
    B = np.ones(np.broadcast(lam_m, kx).shape, complex)
    Cc = e_out * np.ones_like(B)
    for N, d in reversed(layers):
        q = _q(N, kx)
        e = _admittance(N, q, pol)
        dl = (2 * math.pi / lam_m) * q * d
        c_, s_ = np.cos(dl), np.sin(dl)
        B, Cc = c_ * B - 1j * s_ / e * Cc, -1j * e * s_ * B + c_ * Cc
    den = e_in * B + Cc
    r = (e_in * B - Cc) / den
    t = 2.0 * e_in / den
    T = 4 * np.real(e_in) * np.real(e_out) / np.abs(den) ** 2
    if pol == 'p':
        r = -r                                               # field-reversal convention of stack_rps (r_p = -r_s at normal incidence)
    return r, t, np.clip(T, 0.0, None)


def _pair(p, s, f=1.0):
    """(a, b, c, d) = (m11, m12, m33, m34) of the isotropic Mueller matrix of an interaction with amplitudes (p, s) and power factor f (the
    S sign follows rncs)."""
    x = p * np.conj(s)
    ap, as_ = np.abs(p) ** 2, np.abs(s) ** 2
    return f * (ap + as_) / 2, f * (ap - as_) / 2, f * np.real(x), -f * np.imag(x)


def _add(m1, m2, w=1.0):
    return tuple(x + w * y for x, y in zip(m1, m2))


def _scale(m, w):
    return tuple(w * x for x in m)


# ---------------------------------------------------------------- sample definitions
MATERIAL_IDS_NOTE = 'library substance ids (optical_constants_library.json)'


def material_schema(depth=0):
    lib_ids = sorted(library()['subs'])
    lib = obj({'library': enum(lib_ids)})
    cauchy = obj({'cauchy': obj({'A': number('1', 1.0, 4.0), 'B': number('m2', -5e-13, 5e-13), 'C': number('m4', -1e-25, 1e-25), 'k_amp': number('1', 0.0, 10.0),
                                 'k_exp': number('1/eV', 0.0, 20.0), 'band_edge_ev': number('eV', 0.5, 10.0)}, optional=('C', 'k_amp', 'k_exp', 'band_edge_ev'))})
    sell = obj({'sellmeier': obj({'A': number('1', 1.0, 10.0), 'B': number('1', 0.0, 20.0), 'lam0_m': number('m', 0.0, 370e-9), 'E_ir': number('1/m2', 0.0, 1e12)},
                                 optional=('E_ir',))})
    tl = obj({'tauc_lorentz': obj({'eps_inf': number('1', 0.5, 10.0), 'Eg_ev': number('eV', 0.1, 8.0), 'A': number('eV', 0.1, 500.0), 'E0_ev': number('eV', 0.5, 15.0),
                                   'C_ev': number('eV', 0.05, 20.0)})})
    osc = obj({'A': number('1', 0.0, 500.0), 'E0_ev': number('eV', 0.1, 20.0), 'B_ev': number('eV', 0.01, 20.0)})
    lor = obj({'lorentz': obj({'eps_inf': number('1', 0.5, 20.0), 'oscillators': array(osc, 1, 6)})})
    dru = obj({'drude': obj({'eps_inf': number('1', 0.5, 20.0), 'Ep_ev': number('eV', 0.0, 30.0), 'gamma_ev': number('eV', 0.001, 10.0)})})
    ema = obj({'ema': obj({'a': enum(lib_ids + ['void']), 'b': enum(lib_ids + ['void']), 'fraction_b': number('1', 0.0, 1.0)})})
    return {'anyOf': [lib, cauchy, sell, tl, lor, dru, ema]}


def layer_schema():
    return obj({'material': material_schema(), 'thickness_m': number('m', 0.0, 20e-6), 'nonuniformity': number('1', 0.0, NONUNIFORMITY_MAX)},
               optional=('nonuniformity',))


def sample_schema():
    return obj({
        'sample_id': dict(TEXT, pattern='[a-z0-9_]{1,32}'),
        'kind': enum(['film_stack', 'calibration_wafer']),
        'shape': enum(['disc', 'rectangle']),
        'size_m': array(number('m', SAMPLE_SIZE_RANGE_M[0] * 0.5, 0.30), 2, 2),
        'substrate_thickness_m': number('m', 1e-4, 0.030),
        'substrate': material_schema(),
        'layers': array(layer_schema(), 0, 8),
        'roughness_m': number('m', 0.0, 200e-9),
        'backside': enum(['polished', 'rough'])})


def sample_ok(s):
    """Checks beyond the schema: size and thickness inside the instrument's sample range."""
    w, l = s['size_m']
    if min(w, l) < SAMPLE_SIZE_RANGE_M[0] - 1e-12 or max(w, l) > SAMPLE_SIZE_RANGE_M[1] + 1e-12:
        raise ActionRejected('SAMPLE_SIZE_OUT_OF_RANGE')
    if s['substrate_thickness_m'] + sum(L['thickness_m'] for L in s['layers']) > SUBSTRATE_MAX_THICKNESS_M + 1e-12:
        raise ActionRejected('SAMPLE_TOO_THICK')
    if s['shape'] == 'disc' and abs(w - l) > 1e-9:
        raise ActionRejected('INVALID_ARGUMENT')


def sample_height_m(s):
    return s['substrate_thickness_m'] + sum(L['thickness_m'] for L in s['layers'])


def sample_radius_m(s):
    """Radius of the circle enclosing the sample outline (collision guard)."""
    w, l = s['size_m']
    return w / 2 if s['shape'] == 'disc' else math.hypot(w, l) / 2


def point_on_sample(s, dx, dy, margin=0.0):
    """Is the point (dx, dy) relative to the sample centre inside the outline (with a margin)?"""
    w, l = s['size_m']
    if s['shape'] == 'disc':
        return math.hypot(dx, dy) <= w / 2 - margin
    return abs(dx) <= w / 2 - margin and abs(dy) <= l / 2 - margin


CALIBRATION_WAFER = dict(sample_id='cal_wafer', kind='calibration_wafer', shape='disc', size_m=[0.050, 0.050], substrate_thickness_m=0.000525,
                         substrate={'library': 'si'}, layers=[dict(material={'library': 'sio2'}, thickness_m=25.0e-9)], roughness_m=0.0, backside='rough')


# ---------------------------------------------------------------- truth optics of a placed sample (all four depolarization channels)
def _trapezoid(m, Z):
    """m trapezoid nodes over |z| <= Z of a standard normal variable with weights summing to 1 (m = 1: the centre only)."""
    if m <= 1:
        return np.zeros(1), np.ones(1)
    z = np.linspace(-Z, Z, m)
    w = np.exp(-0.5 * z * z)
    return z, w / w.sum()


def _gauss_hermite(n):
    z, w = np.polynomial.hermite_e.hermegauss(n)
    return z, w / w.sum()


SMALL_SPREAD_RAD = 0.05                            # below this phase spread the Airy harmonics are smooth in y: Gauss-Hermite 3, 5, 7 ... nodes
NEGLIGIBLE_SPREAD_RAD = 3e-4                       # below this the perturbation changes N, C, S by less than about 1e-6 (skipped)


def stack_media(sample, lam_nm, ambient_N):
    """(N0, layers [(N, d, sigma_rel)], Ns) of a sample on lam_nm (roughness as a Bruggeman 50 % void layer of the top material)."""
    layers = [(material_index(L['material'], lam_nm), L['thickness_m'], L.get('nonuniformity', 0.0)) for L in sample['layers']]
    Ns = material_index(sample['substrate'], lam_nm)
    if sample['roughness_m'] > 0:
        top = layers[0][0] if layers else Ns
        rough = np.sqrt(bruggeman(top * top, np.ones_like(top), 0.5))
        layers = [(rough, sample['roughness_m'], 0.0)] + layers
    return np.real(ambient_N) + 0j, layers, Ns                          # transparent ambient (see stack_rps)


INCOHERENT_SPREAD_RAD = 8.0                        # a layer whose phase the non-uniformity spreads by this much is incoherent (first harmonic damped by e^-32)
FAST_SPREAD_RAD = 16.0                             # one layer spread at least this much, every other layer 16 times less: its phase is averaged uniformly
BLOCK_PIXELS = 20                                  # wavelength blocks refined independently (the needed nodes change with the wavelength)
BLOCK_SPREAD_RAD = 1.0                             # spectra whose phase spreads stay below this are averaged in one block (little work, less overhead)


def _layer_spreads(layers, lam, N0, theta0, divergence_rad):
    """Per layer and wavelength: phase change per unit normal variable of the thickness non-uniformity (sigma_j Re phi_j), of the beam divergence
    (sigma_theta dRe phi_j / dtheta) and the attenuation change of the non-uniformity (sigma_j Im phi_j); phi_j = 4 pi q_j d_j / lambda."""
    lam_m = lam * 1e-9
    n0 = np.real(np.broadcast_to(N0, lam.shape))
    kx = n0 * math.sin(theta0)
    nu, dv, ab = [], [], []
    for N, d, sg in layers:
        q = _q(np.broadcast_to(N, lam.shape), kx)
        nu.append(np.abs(sg * 4 * math.pi * np.real(q) * d / lam_m))
        ab.append(np.abs(sg * 4 * math.pi * np.imag(q) * d / lam_m))
        dv.append(np.abs(4 * math.pi * d * np.real(-(n0 ** 2) * math.sin(theta0) * math.cos(theta0) / q) / lam_m) * divergence_rad)
    return np.array(nu).reshape(len(layers), lam.size), np.array(dv).reshape(len(layers), lam.size), np.array(ab).reshape(len(layers), lam.size)


def phase_spreads(layers, lam_nm, N0, theta0, divergence_rad):
    """Largest total phase change sum_j |d phi_j| over the wavelengths per unit normal variable of the thickness non-uniformity and of the beam
    divergence, the latter without the layers the non-uniformity already makes incoherent; (0, 0) without layers."""
    lam = np.atleast_1d(np.asarray(lam_nm, float))
    if not layers:
        return 0.0, 0.0
    nu, dv, _ = _layer_spreads(layers, lam, N0, theta0, divergence_rad)
    return float(nu.sum(axis=0).max()), float(np.where(nu >= INCOHERENT_SPREAD_RAD, 0.0, dv).sum(axis=0).max())


def _first_level(spread):
    return 3 if spread <= SMALL_SPREAD_RAD else 2 * math.ceil(0.95 * (3.0 * spread + 6.0)) + 1


def depolarized_average(lam_nm, theta0, media_at, layers0, N0_centre, bandwidth_ev, divergence_rad, backside_d=None, tol=QUAD_TOL, budget=QUAD_BUDGET,
                        info=None):
    """Isotropic Mueller elements (a, b, c, d) = (m11, m12, m33, m34) averaged incoherently over the thickness non-uniformity (thickness
    d_j (1 + sigma_j z), one z common to all layers), the beam divergence (angle theta0 + sigma_theta z') and the pixel band (photon energy
    E + sigma_E z_b; every band node at its true wavelength with the optical constants there), each exactly - interference phase and Fresnel
    coefficients together - plus the incoherent backside beams of a polished substrate of thickness backside_d.  Blocks of 20 wavelengths are
    refined independently when a phase spread exceeds 1 rad.  Each dimension is a quadrature refined until N, C, S change by less than tol: Gauss-Hermite 3, 5, 7 ... nodes while its
    phase spread is small, otherwise a trapezoid on |z| <= 6 (band |z_b| <= 5: the kinks of the 2 nm optical-constant tables) refined m -> 2 m - 1.
    When one layer's phase is spread by at least 16 rad and every other layer's 16 times less, that layer is incoherent: its phase is averaged
    uniformly over one period (M equispaced phase offsets, M -> 2 M) at each node of a coarse non-uniformity rule that follows the slow parts (the
    other layers, its attenuation) - the brute-force rule would have to resolve every Airy harmonic of a many-fringe spread.  The work nodes x
    wavelengths x (layers + 1) of a block stays below its share of budget; info['capped'] = True when the cap stopped a refinement (the result is
    then not converged: the instrument refuses to deliver it, the model flags it).  media_at(lam (B, n)) returns (N0, [(N, d, sigma)], Ns) there."""
    lam = np.atleast_1d(np.asarray(lam_nm, float))
    tot = [np.zeros(lam.size) for _ in range(4)]
    capped, levels = False, []
    step = lam.size
    averaged = divergence_rad > 0 or any(sg > 0 for _, _, sg in layers0)
    if layers0 and averaged:                                                 # many-node averages: blocks of wavelengths refined separately
        nu, dv, _ = _layer_spreads(layers0, lam, N0_centre, theta0, divergence_rad)
        if max(float(nu.sum(axis=0).max()), float(dv.sum(axis=0).max())) > BLOCK_SPREAD_RAD:
            step = BLOCK_PIXELS
    for b0 in range(0, lam.size, step):
        sl = slice(b0, min(lam.size, b0 + step))
        part, cap_b, lv = _average_block(lam[sl], theta0, media_at, [(np.broadcast_to(N, lam.shape)[sl], d, sg) for N, d, sg in layers0],
                                         np.broadcast_to(N0_centre, lam.shape)[sl], bandwidth_ev, divergence_rad, backside_d, tol,
                                         budget * (sl.stop - sl.start) / lam.size * 2.0)
        for t, x in zip(tot, part):
            t[sl] = x
        capped = capped or cap_b
        levels.append(lv)
    if info is not None:
        info.update(capped=capped, levels={k: max(lv.get(k, 0) for lv in levels) for k in {k for lv in levels for k in lv}}, block_levels=levels)
    return tuple(tot)


def _average_block(lam, theta0, media_at, layers0, N0_centre, bandwidth_ev, divergence_rad, backside_d, tol, budget):
    L = len(layers0)
    nu = dv = ab = np.zeros((L, lam.size))
    if L and (divergence_rad > 0 or any(sg > 0 for _, _, sg in layers0)):   # otherwise no phase is spread (plain model evaluations)
        nu, dv, ab = _layer_spreads(layers0, lam, N0_centre, theta0, divergence_rad)
    fast = None
    if L:
        j = int(np.argmax(nu.min(axis=1)))
        others = np.delete(nu, j, axis=0)
        if nu[j].min() >= FAST_SPREAD_RAD and (others.size == 0 or np.all(others.sum(axis=0) * FAST_SPREAD_RAD <= nu[j])):
            fast = j
    s_dv = float(np.where(nu >= INCOHERENT_SPREAD_RAD, 0.0, dv).sum(axis=0).max()) if L else 0.0
    dims = []                                                               # (name, rule, first level); rule: gh | trap | uniform
    if fast is not None:
        slow = float((np.delete(nu, fast, axis=0).sum(axis=0) + ab[fast]).max())
        dims.append(('phase', 'uniform', 8))
        dims.append(('nonuniformity', 'gh' if slow <= SMALL_SPREAD_RAD else 'trap', _first_level(slow)))
    elif L and nu.sum(axis=0).max() > NEGLIGIBLE_SPREAD_RAD:
        s_nu = float(nu.sum(axis=0).max())
        dims.append(('nonuniformity', 'gh' if s_nu <= SMALL_SPREAD_RAD else 'trap', _first_level(s_nu)))
    if divergence_rad > 0:                                                   # the Fresnel coefficients change with the angle even without layers
        dims.append(('divergence', 'gh' if s_dv <= SMALL_SPREAD_RAD else 'trap', _first_level(s_dv)))
    if bandwidth_ev > 0:
        dims.append(('bandwidth', 'trap', 9))
    if not dims:                                                            # nothing to average: one exact evaluation (most model evaluations)
        N0b, layb, Nsb = media_at(lam[None, :])
        lay = [(N, d) for N, d, _ in layb]
        m = _pair(*stack_rps(lam[None, :], N0b, lay, Nsb, theta0))
        if backside_d is not None:
            m = _add(m, _backside(lam[None, :], N0b, lay, Nsb, np.real(N0b) * math.sin(theta0), backside_d, theta0))
        return tuple(np.broadcast_to(x, (1, lam.size))[0] for x in m), False, {}
    E = HC_EV_NM / lam
    work = lam.size * (L + 1) * (3 if backside_d is not None else 1)
    band = {}

    def band_nodes(m):
        if m not in band:
            zb, wb = _trapezoid(m, BAND_Z)
            lam_b = HC_EV_NM / (E[None, :] + zb[:, None] * bandwidth_ev) if m > 1 else lam[None, :]
            band[m] = (wb, lam_b, media_at(lam_b))
        return band[m]

    def nodes(rule, m):
        if rule == 'gh':
            return _gauss_hermite(m)
        if rule == 'uniform':
            return 2 * math.pi * np.arange(m) / m, np.full(m, 1.0 / m)
        return _trapezoid(m, PHASE_Z)

    def evaluate(lv):
        get = {name: nodes(rule, m) for (name, rule, _), m in zip(dims, lv)}
        zu, wu = get.get('nonuniformity', (np.zeros(1), np.ones(1)))
        ps, wp = get.get('phase', (np.zeros(1), np.ones(1)))
        za, wa = get.get('divergence', (np.zeros(1), np.ones(1)))
        wb, lam_b, (N0b, layb, Nsb) = band_nodes(next((m for (name, _, _), m in zip(dims, lv) if name == 'bandwidth'), 1))
        th = (theta0 + za * divergence_rad)[None, :, None, None]             # (1, A, 1, 1)
        lb, n0, ns = lam_b[None, None], N0b[None, None], Nsb[None, None]     # (1, 1, B, n)
        kx = np.real(n0) * np.sin(th)
        Z = np.repeat(zu, len(ps))                                          # thickness nodes x phase offsets of the incoherent layer
        P = np.tile(ps, len(zu))
        W = np.repeat(wu, len(ps)) * np.tile(wp, len(zu))
        per_fringe = None
        if fast is not None:                                                # thickness of one fringe of the incoherent layer at every angle / band node
            per_fringe = lb * 1e-9 / (4 * math.pi * np.maximum(np.real(_q(layb[fast][0][None, None], kx)), 1e-9))     # m per rad of phase
        chunk = max(1, int(QUAD_CHUNK // (len(za) * lam_b.size)))
        tot = [np.zeros(lam.size) for _ in range(4)]
        for i0 in range(0, len(Z), chunk):
            zc, pc, wc = Z[i0:i0 + chunk], P[i0:i0 + chunk], W[i0:i0 + chunk]
            lay = []
            for j, (N, d, sg) in enumerate(layb):
                dj = d * np.clip(1.0 + sg * zc, 0.0, None)[:, None, None, None]
                if j == fast:
                    dj = dj + pc[:, None, None, None] * per_fringe
                lay.append((N[None, None], dj))
            rp, rs = stack_rps(lb, n0, lay, ns, th)
            m = _pair(rp, rs)
            if backside_d is not None:
                m = _add(m, _backside(lb, n0, lay, ns, kx, backside_d, th))
            wgt = wc[:, None, None, None] * wa[None, :, None, None] * wb[None, None, :, None]
            tot = [t + np.sum(np.broadcast_to(x, wgt.shape[:3] + (lam.size,)) * wgt, axis=(0, 1, 2)) for t, x in zip(tot, m)]
        return tuple(tot)

    def ncs(t):
        r = normalised(t)
        return np.stack([r['N'], r['C'], r['S']])

    levels = [m for _, _, m in dims]
    capped = False
    while levels and work * math.prod(levels) > budget:                     # start inside the budget (the largest dimension first)
        i = int(np.argmax(levels))
        if levels[i] <= 3:
            break
        levels[i] = max(3, (levels[i] - 1) // 2 + 1)
        capped = True
    cur = evaluate(levels)
    cur_ncs = ncs(cur)
    for i in range(len(dims)):
        while True:
            nxt = list(levels)
            nxt[i] = levels[i] + 2 if dims[i][1] == 'gh' else 2 * levels[i] if dims[i][1] == 'uniform' else 2 * levels[i] - 1
            if work * math.prod(nxt) > budget:
                capped = True
                break
            new = evaluate(nxt)
            new_ncs = ncs(new)
            err = float(np.max(np.abs(new_ncs - cur_ncs)))
            levels, cur, cur_ncs = nxt, new, new_ncs
            if err < tol:
                break
    return cur, capped, {name: m for (name, _, _), m in zip(dims, levels)}


def sample_mueller(sample, lam_nm, theta0, ambient_N=None, backside=True, bandwidth_ev=None, divergence_rad=None, nonuniform=True, tol=QUAD_TOL,
                   budget=QUAD_BUDGET, info=None):
    """Normalised (N, C, S), reflectance R = m11 and depolarization of a sample at the angle of incidence theta0 (rad) on lam_nm, averaged
    incoherently over the pixel band, the thickness non-uniformity inside the beam footprint (one relative deviation common to all layers) and the
    beam divergence (depolarized_average), plus the incoherent backside reflections of a transparent polished substrate that overlap the detector
    aperture (first three).  Returns dict(R, N, C, S, depol); a non-reflecting sample (R = 0) gives N = C = S = 0 and depolarization 1."""
    lam = np.atleast_1d(np.asarray(lam_nm, float))
    bw = BANDWIDTH_SIGMA_EV if bandwidth_ev is None else bandwidth_ev
    dv = BEAM_DIVERGENCE_RAD if divergence_rad is None else divergence_rad
    amb0 = np.ones(lam.shape, complex) if ambient_N is None else np.asarray(ambient_N) * np.ones(lam.shape)
    N00, layers0, _ = stack_media(sample, lam, amb0)
    if not nonuniform:
        layers0 = [(N, d, 0.0) for N, d, _ in layers0]

    def media_at(lam_b):
        flat = lam_b.ravel()
        amb = np.ones(flat.shape, complex) if ambient_N is None else np.interp(flat, lam, np.real(amb0)) + 0j
        N0, layers, Ns = stack_media(sample, flat, amb)
        return N0.reshape(lam_b.shape), [(N.reshape(lam_b.shape), d, sg) for N, d, sg in layers], Ns.reshape(lam_b.shape)
    tot = depolarized_average(lam, theta0, media_at, layers0, N00, bw, dv, sample['substrate_thickness_m'] if backside and sample['backside'] == 'polished' else None,
                              tol, budget, info)
    return normalised(tot)


def normalised(tot):
    """(R, N, C, S, depolarization) of an averaged isotropic Mueller matrix (a, b, c, d); a vanishing m11 gives N = C = S = 0, depolarization 1."""
    a, b, c, d = (np.asarray(x, float) for x in tot)
    ok = a > 1e-30
    a_ = np.where(ok, a, 1.0)
    N_, C_, S_ = np.where(ok, -b / a_, 0.0), np.where(ok, c / a_, 0.0), np.where(ok, d / a_, 0.0)
    depol = np.where(ok, 1.0 - np.sqrt(N_ ** 2 + C_ ** 2 + S_ ** 2), 1.0)
    return dict(R=np.where(ok, a, 0.0), N=N_, C=C_, S=S_, depol=np.clip(depol, 0.0, 1.0))


def _backside(lam_nm, N0, layers, Ns, kx, d_sub, theta0, orders=3):
    """Incoherent backside beams of a transparent substrate: down through the films (amplitude t), one pass through the substrate (absorption),
    reflection at the polished back face into air, up through the films (t); higher orders bounce at the underside of the film stack.  Each beam
    is coherent in itself: its Mueller matrix is that of the Jones product t_up r_back (r_under r_back)^(j-1) t_down (including the p-s phase of
    the film transmissions); the beams add incoherently, each weighted by its overlap with the detector aperture (lateral offset
    2 d tan(theta_s) cos(theta0) per round trip)."""
    lam_m = np.asarray(lam_nm, float) * 1e-9
    air = np.ones_like(Ns)
    qs = _q(Ns, kx)
    cos_s = np.real(qs / Ns)
    sin_s = np.sqrt(np.clip(1 - cos_s ** 2, 0.0, None))
    alpha = 4 * math.pi * np.imag(qs) / lam_m                              # intensity attenuation per unit depth (normal to the surface)
    A = np.exp(-alpha * d_sub)                                            # one pass (depth d along the normal)
    if np.all(A < 1e-9):
        return (0.0, 0.0, 0.0, 0.0)
    _, td_p, _ = _stack_rt(lam_nm, N0, layers, Ns, kx, 'p')
    _, td_s, _ = _stack_rt(lam_nm, N0, layers, Ns, kx, 's')
    ru_p, tu_p, _ = _stack_rt(lam_nm, Ns, list(reversed(layers)), N0, kx, 'p')
    ru_s, tu_s, _ = _stack_rt(lam_nm, Ns, list(reversed(layers)), N0, kx, 's')
    rb_p, _, _ = _stack_rt(lam_nm, Ns, [], air, kx, 'p')
    rb_s, _, _ = _stack_rt(lam_nm, Ns, [], air, kx, 's')
    shift = 2 * d_sub * np.where(cos_s > 1e-6, sin_s / np.maximum(cos_s, 1e-6), 1e6) * np.cos(theta0)
    out = (0.0, 0.0, 0.0, 0.0)
    inner_p, inner_s = rb_p, rb_s                                         # r_back (r_under r_back)^(j-1)
    for j in range(1, orders + 1):
        f = np.exp(-(j * shift) ** 2 / (2 * DETECTOR_APERTURE_SIGMA_M ** 2)) * A ** (2 * j)
        out = _add(out, _pair(tu_p * inner_p * td_p, tu_s * inner_s * td_s, f))
        inner_p, inner_s = inner_p * ru_p * rb_p, inner_s * ru_s * rb_s
    return out


# ---------------------------------------------------------------- instrument: source, rotating compensator, analyzer, spectrometer
def lamp_flux_rel(lam_nm, temperature_k=LAMP_TEMPERATURE_K):
    """Relative detected photon flux per CCD pixel of the QTH lamp through the optics: Planck photon radiance x pixel width in wavelength
    (equal-energy pixels: d lambda = lambda^2 dE / hc) x silicon CCD quantum efficiency x a colour-balancing filter that attenuates the red
    so that the blue end is not starved (both engineering curves), normalised to its maximum."""
    lam = np.asarray(lam_nm, float)
    lm = lam * 1e-9
    c2 = 1.438776877e-2
    photons = 1.0 / (lm ** 4 * (np.exp(c2 / (lm * temperature_k)) - 1.0))       # photon spectral radiance ~ 2c / l^4 / (e^x - 1)
    dlam = lam ** 2 * PIXEL_WIDTH_EV / HC_EV_NM
    qe = 0.25 + 0.40 * np.exp(-((lam - 620.0) / 230.0) ** 2)
    balance = BALANCE_FLOOR + (1.0 - BALANCE_FLOOR) * np.exp(-((lam - 380.0) / BALANCE_WIDTH_NM) ** 2)
    f = photons * dlam * qe * balance
    return f / f.max()


def retardance_nominal(lam_nm):
    return np.radians(COMPENSATOR_DELTA0_DEG) * COMPENSATOR_LAMBDA0_NM / np.asarray(lam_nm, float)


def compensator_angles(n=COMPENSATOR_STEPS):
    return 2 * math.pi * np.arange(n) / n


def rce_design(C, delta, A):
    """Design matrix G[l, k, i]: intensity at compensator azimuth C_k for unit Stokes component i of the light leaving the sample, through the
    compensator (retardance delta_l) and the analyzer at A (both rad)."""
    c2, s2 = np.cos(2 * C)[None, :], np.sin(2 * C)[None, :]
    cd, sd = np.cos(delta)[:, None], np.sin(delta)[:, None]
    ca, sa = math.cos(2 * A), math.sin(2 * A)
    g0 = np.full(np.broadcast(c2, cd).shape, 0.5)
    g1 = 0.5 * (ca * (c2 * c2 + s2 * s2 * cd) + sa * c2 * s2 * (1 - cd))
    g2 = 0.5 * (ca * c2 * s2 * (1 - cd) + sa * (s2 * s2 + c2 * c2 * cd))
    g3 = 0.5 * (-ca * s2 * sd + sa * c2 * sd)
    return np.stack([g0, g1, g2, g3], -1)


def sample_stokes(abcd, P):
    """Stokes vector (per unit source) leaving an isotropic sample (a, b, c, d) lit through the polarizer at P: 1/2 [a + b cos 2P, b + a cos 2P,
    c sin 2P, -d sin 2P]."""
    a, b, c, d = abcd
    cp, sp = math.cos(2 * P), math.sin(2 * P)
    return 0.5 * np.stack([a + b * cp, b + a * cp, c * sp, -d * sp], -1)


def invert_polarizer(stokes, P):
    """(a, b, c, d) of the sample from the Stokes vector leaving it (inverse of sample_stokes for the polarizer azimuth P)."""
    s0, s1, s2, s3 = (stokes[..., i] for i in range(4))
    cp, sp = math.cos(2 * P), math.sin(2 * P)
    den = 1.0 - cp * cp
    a = 2 * (s0 - s1 * cp) / den
    b = 2 * (s1 - s0 * cp) / den
    return a, b, 2 * s2 / sp, -2 * s3 / sp


def acquisition_plan(peak_rate_e_s, mode_time_s):
    """Auto exposure: exposure per compensator step that fills the brightest pixel to CCD_TARGET_FILL of the full well (at most the whole mode
    time), and the number of complete compensator cycles averaged in the mode time."""
    t_exp = min(CCD_TARGET_FILL * CCD_FULL_WELL_E / max(peak_rate_e_s, 1e-9), mode_time_s / COMPENSATOR_STEPS - STEP_OVERHEAD_S)
    t_exp = max(t_exp, 1e-4)
    cycles = max(1, int(mode_time_s // (COMPENSATOR_STEPS * (t_exp + STEP_OVERHEAD_S))))
    return t_exp, cycles


def rce_acquire(abcd_list, polarizers, inst_true, rate_e_s, mode_time_s, rng, drift_per_s=0.0, room_light=ROOM_LIGHT_FRACTION):
    """Simulated step-scan acquisition: detected photo-electrons at every compensator step (summed over the averaged cycles) for the sample's
    (a, b, c, d) per wavelength at each polarizer azimuth (rad, instrument frame), with the TRUE instrument (polarizer offset, analyzer
    azimuth, retardance); shot noise of signal and room light (the room light is measured with the shutter closed and subtracted), read
    noise; the intensity may drift linearly (relative per s) through a compensator cycle.  Returns (counts per polarizer, plan)."""
    C = compensator_angles()
    Gt = rce_design(C, inst_true['delta'], inst_true['A'])
    peak = float(np.max(rate_e_s * max(float(np.max(np.abs(x[0]))) for x in abcd_list)))
    t_exp, cycles = acquisition_plan(peak, mode_time_s / max(1, len(polarizers)))
    out = []
    for abcd, P in zip(abcd_list, polarizers):
        S = sample_stokes(abcd, P + inst_true['P_offset'])
        I = np.einsum('lki,li->lk', Gt, S)
        ramp = 1.0 + drift_per_s * (np.arange(len(C)) - (len(C) - 1) / 2) * (t_exp + STEP_OVERHEAD_S)
        counts = rate_e_s[:, None] * I * ramp[None, :] * t_exp * cycles
        ambient = room_light * rate_e_s[:, None] * 0.25 * t_exp * cycles * np.ones_like(counts)
        var = counts + 2 * ambient + 2 * cycles * CCD_READ_NOISE_E ** 2
        out.append(counts + rng.normal(0.0, 1.0, counts.shape) * np.sqrt(np.clip(var, 1e-12, None)))
    return out, dict(exposure_s=t_exp, cycles=cycles, peak_counts_per_exposure=float(peak * t_exp), full_well_e=CCD_FULL_WELL_E)


def rce_reduce(counts, P, inst_cal, rate_e_s, plan):
    """Fourier analysis (least squares over the compensator steps) with the CALIBRATED model and inversion of the polarizer: (a, b, c, d)."""
    Gc = rce_design(compensator_angles(), inst_cal['delta'], inst_cal['A'])
    s_hat = np.einsum('lik,lk->li', np.linalg.pinv(Gc), counts) / (rate_e_s[:, None] * plan['exposure_s'] * plan['cycles'])
    return invert_polarizer(s_hat, P + inst_cal['P_offset'])


def rce_measure(abcd_list, polarizers, inst_true, inst_cal, rate_e_s, mode_time_s, rng, drift_per_s=0.0, room_light=ROOM_LIGHT_FRACTION):
    counts, plan = rce_acquire(abcd_list, polarizers, inst_true, rate_e_s, mode_time_s, rng, drift_per_s, room_light)
    return [rce_reduce(c, P, inst_cal, rate_e_s, plan) for c, P in zip(counts, polarizers)], plan


def ncs_of_abcd(abcd):
    a, b, c, d = abcd
    N, C_, S = -b / a, c / a, d / a
    return N, C_, S, np.clip(1.0 - np.sqrt(N * N + C_ * C_ + S * S), -1.0, 1.0)


def retardance_model(lam_nm, e_scale, e_slope):
    """Compensator retardance with two calibration parameters: delta = delta_nominal (1 + e_scale) + e_slope (l - l0) / 300 nm (rad)."""
    lam = np.asarray(lam_nm, float)
    return retardance_nominal(lam) * (1.0 + e_scale) + e_slope * (lam - COMPENSATOR_LAMBDA0_NM) / 300.0


def fit_calibration(counts, plan, rate_e_s, lam_nm, P_nominal, model_ncs, x0, iterations=12):
    """Calibration on a reference sample measured in reflection: Gauss-Newton on x = (polarizer offset, analyzer azimuth, retardance scale,
    retardance slope, angle of incidence, reference film thickness): the counts reduced with the trial instrument must equal the model N, C, S of
    the reference at the trial angle and thickness (model_ncs(theta, d) -> (N, C, S)).  Returns (x, rms residual)."""
    x = np.asarray(x0, float).copy()
    steps = np.array([1e-5, 1e-5, 1e-5, 1e-5, 1e-6, 1e-12])

    def resid(v):
        inst = dict(P_offset=v[0], A=v[1], delta=retardance_model(lam_nm, v[2], v[3]))
        N, C, S, _ = ncs_of_abcd(rce_reduce(counts, P_nominal, inst, rate_e_s, plan))
        Nm, Cm, Sm = model_ncs(v[4], v[5])
        return np.concatenate([N - Nm, C - Cm, S - Sm])
    lam_ = 1e-3
    r0 = resid(x)
    for _ in range(iterations):
        J = np.stack([(resid(x + np.eye(6)[i] * steps[i]) - r0) / steps[i] for i in range(6)], 1)
        scale = np.sqrt((J * J).sum(0)) + 1e-30
        Js = J / scale
        H = Js.T @ Js
        g_ = Js.T @ r0
        dx = -np.linalg.solve(H + lam_ * np.diag(np.diag(H)), g_) / scale
        r1 = resid(x + dx)
        if (r1 @ r1) < (r0 @ r0):
            x, r0, lam_ = x + dx, r1, max(lam_ / 3, 1e-6)
        else:
            lam_ *= 5
    return x, float(np.sqrt(np.mean(r0 ** 2)))


# ---------------------------------------------------------------- capabilities and state slots
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


MAX_SAMPLES = 12
HEIGHT_REF = '资产坐标系（原点为台面投影中心、+Z向上）中样品平台顶面的高度；为参考高度0.1192 m时Z台关节为0'
OFFSET_REF = '样品中心相对测量点(0, −0.0144 m)在资产坐标系X/Y上的偏移'
ARM_REF = '绕过测量点(0, −0.0144, 0.134) m的资产Y轴；0为直通位（两臂水平共线），源臂绕+Y、检测臂绕−Y转到同一仰角'
ANGLE_REF = '入射角＝入射光束与样品面法线的夹角（按位置名65/70/75/90）'


def ref(schema, text):
    return dict(schema, **{'x-reference-conditions': text})


FAULTS = ['lamp_failure', 'vacuum_leak', 'stage_stall', 'plunger_not_seated']
FAULT_ERROR = {'lamp_failure': 'LAMP_FAILURE', 'stage_stall': 'STAGE_FAULT'}
MODES = list(MODE_TIME_S)
ALIGNMENTS = ['none', 'standard', 'robust', 'fixed_height']
LIQUIDS = ['water', 'ethanol']
PLANT_EXAMPLE = dict(mains_voltage_v=230.0, vacuum_supply_pa=20.0e3, room_k=295.15, noise_seed=1, calibration='factory', lamp_age_s=150.0 * 3600.0)
PLANT = obj(dict(mains_voltage_v=number('V', 0.0, 264.0), vacuum_supply_pa=ref(number('Pa', 0.0, 101325.0), '绝对压力'), room_k=number('K', 283.15, 308.15),
                 noise_seed=integer(0, 2 ** 32 - 1),
                 calibration=enum(['factory', 'nominal']), lamp_age_s=number('s', 0.0, 5000.0 * 3600.0)))
SAMPLE = sample_schema()
SAMPLE_EXAMPLE = dict(sample_id='pmma_on_si', kind='film_stack', shape='disc', size_m=[0.0508, 0.0508], substrate_thickness_m=0.000275,
                      substrate={'library': 'si'}, layers=[dict(material={'library': 'pmma'}, thickness_m=120e-9, nonuniformity=0.002),
                                                            dict(material={'library': 'sio2'}, thickness_m=1.8e-9)],
                      roughness_m=0.0, backside='rough')
OPERATION = obj(dict(operation_id=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
SPECTRUM = array(number('1'), 0, N_WAVELENGTHS)
SPEC_NULL = nullable(SPECTRUM)
SPECTRUM_RAD = array(number('rad'), 0, N_WAVELENGTHS)
SPEC_RAD = nullable(SPECTRUM_RAD)
MM_KEYS = ['m12', 'm13', 'm21', 'm22', 'm23', 'm31', 'm32', 'm33', 'm41', 'm42', 'm43']
PARAM_UNITS = {   # one unit per analysis-model parameter name (SI; eV / 1/eV for dispersion-model energies); models.list returns it per model
    'oxide.thickness_m': 'm', 'film.thickness_m': 'm', 'interface.thickness_m': 'm', 'rough.thickness_m': 'm', 'substrate.thickness_m': 'm',
    'film.nonuniformity': '1', 'film.A': '1', 'film.B': 'm2', 'film.C': 'm4', 'film.k_amp': '1', 'film.k_exp': '1/eV', 'film.band_edge_ev': 'eV',
    'film.eps_inf': '1', 'film.Eg_ev': 'eV', 'film.A_ev': 'eV', 'film.E0_ev': 'eV', 'film.C_ev': 'eV',
    'substrate.eps_inf': '1', 'substrate.Ep_ev': 'eV', 'substrate.gamma_ev': 'eV', 'substrate.osc_A': '1', 'substrate.osc_E0_ev': 'eV',
    'substrate.osc_B_ev': 'eV', 'substrate.backside_polished': '1', 'options.bandwidth': '1', 'options.divergence': '1'}
SCAN_PARAMETERS = sorted(k for k, u in PARAM_UNITS.items() if u == 'm')      # the uniqueness scan runs over a thickness


def _param_value(u):
    return obj(dict(value=number(u), ci90=nullable(number(u)), fitted=BOOL, unit=enum([u])))


PARAMS_OUT = {'type': 'object', 'properties': {k: _param_value(u) for k, u in PARAM_UNITS.items()}, 'required': [], 'additionalProperties': False}
FIT_BLOCK = obj(dict(model_id=TEXT, source_records=array(TEXT, 1, 4), parameters=PARAMS_OUT, mse=number('1'),
                     iterations=INT, converged=BOOL, prefit=nullable(obj(dict(parameter=TEXT, grid_min=number('m'), grid_max=number('m'), best=number('m'), points=INT))),
                     nk_632_8nm={'type': 'object', 'additionalProperties': obj(dict(n=number('1'), k=number('1')))},
                     correlation=array(array(number('1', -1.0, 1.0), 0, 16), 0, 16), fitted_parameters=array(TEXT, 0, 16),
                     residual_rms=array(number('1'), 1, 4), primary_parameter=nullable(TEXT), warnings=array(TEXT, 0, 32)))
CAL_BLOCK = obj(dict(position=enum(AOI_LABELS), aoi_rad=number('rad'), polarizer_offset_rad=number('rad'), analyzer_rad=number('rad'),
                     retardance_scale=number('1'), retardance_slope_rad=number('rad'), reference_thickness_m=number('m'), rms_residual=number('1')))
RECORD = obj(dict(record_id=TEXT, kind=enum(['se', 'mm', 'straight_through', 'calibration', 'fit']), status=STATUS, started_s=number('s'),
                  finished_s=nullable(number('s')), reason=nullable(TEXT), sample_id=nullable(TEXT), aoi_rad=nullable(number('rad')),
                  aoi_position=nullable(enum(AOI_LABELS)), mode=nullable(TEXT), alignment=nullable(TEXT), stage_z_m=nullable(number('m')), ambient=TEXT,
                  wavelengths_m=array(number('m'), 0, N_WAVELENGTHS), psi_rad=SPEC_RAD, delta_rad=SPEC_RAD, N=SPEC_NULL, C=SPEC_NULL, S=SPEC_NULL,
                  depolarization=SPEC_NULL, reflectance=SPEC_NULL, mueller=nullable({'type': 'object', 'additionalProperties': SPECTRUM}),
                  model_psi_rad=SPEC_RAD, model_delta_rad=SPEC_RAD,
                  model_curves=nullable(array(obj(dict(record_id=TEXT, model_psi_rad=SPECTRUM_RAD, model_delta_rad=SPECTRUM_RAD)), 1, 4)),
                  exposure=nullable(obj(dict(exposure_s=number('s'), cycles=INT, peak_counts_per_exposure=number('1'), full_well_e=number('1')))),
                  calibration=nullable(CAL_BLOCK), fit=nullable(FIT_BLOCK), warnings=array(TEXT, 0, 16),
                  template_source=obj(dict(library=TEXT, substances=array(TEXT, 0, 32))), note_zh=TEXT))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'lamp', 'vacuum', 'aoi', 'stage', 'measurement', 'calibration', 'sample', 'liquid_cell', 'error', 'fault']),
                 code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['electrical_energy_j', 'lamp_on_s', 'vacuum_on_s', 'stage_travel_m', 'arm_travel_rad', 'aoi_changes', 'measurements',
               'compensator_steps', 'liquid_added_m3', 'liquid_drained_m3', 'liquid_in_cell_m3', 'liquid_residual_m3']
LEDGER = obj({k: number('J' if k.endswith('_j') else 's' if k.endswith('_s') else 'm3' if k.endswith('_m3') else 'm' if k.endswith('_m') else 'rad' if k.endswith('_rad')
                        else '1') for k in LEDGER_KEYS})
PARAMS = {'type': 'object', 'properties': {k: number(u) for k, u in PARAM_UNITS.items()}, 'required': [], 'additionalProperties': False}
FIT_IN = obj(dict(record_ids=array(TEXT, 1, 4), model_id=TEXT, parameters=PARAMS, fit=array(TEXT, 0, 12), thickness_prefit=BOOL))
FIT_EXAMPLE = dict(record_ids=['SE0001'], model_id='cauchy_on_si', parameters={'film.thickness_m': 100e-9}, fit=['film.thickness_m', 'film.A', 'film.B'],
                   thickness_prefit=True)
UNIQ_OUT = obj(dict(parameter=enum(SCAN_PARAMETERS), values=array(number('m'), 1, 41), mse=array(number('1'), 1, 41), best_value=number('m'),
                    local_minima=array(number('m'), 0, 41)))
cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['base_plate', 'power_inlet', 'vacuum_inlet'],
    '宿主声明实验室条件（电源电压、真空源绝对压力、室温、噪声种子、仪器校准状态：出厂校准或名义值、灯已用小时数）', 'evaluator', 'configuration')
cap('sample.provide', SAMPLE, obj(dict(sample_id=TEXT, location=TEXT)), SAMPLE_EXAMPLE, ['sample_platform'],
    '宿主在实验台上提供一个样品（平面基底上的多层膜：模板库材料或色散模型、厚度、厚度不均匀度、表面粗糙度；基底厚度与背面抛光/粗糙；'
    '或厂家标定片：Si上约25 nm热氧化层）', 'evaluator', 'configuration')
cap('sample.place', obj(dict(sample_id=dict(TEXT, pattern='[a-z0-9_]{1,32}'), offset_x_m=ref(number('m', -0.10, 0.10), OFFSET_REF),
                             offset_y_m=ref(number('m', -0.10, 0.10), OFFSET_REF))),
    obj(dict(sample_id=TEXT, offset_x_m=number('m'), offset_y_m=number('m'), covers_vacuum_holes=BOOL, beam_on_sample=BOOL)),
    dict(sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0), ['sample_platform', 'vacuum_holes'],
    '把实验台上的样品放到样品平台上（宿主/机器人抽象；偏移为样品中心相对测量点）', 'agent', 'robot_skill')
cap('sample.remove', obj(dict(sample_id=dict(TEXT, pattern='[a-z0-9_]{1,32}'))), obj(dict(sample_id=TEXT)), dict(sample_id='pmma_on_si'), ['sample_platform'],
    '关真空后把样品从平台取回实验台（宿主/机器人抽象）', 'agent', 'robot_skill')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL, state=TEXT)), dict(enabled=True), ['button_power', 'rear_housing'],
    '系统电源（上电后电子单元初始化与补偿器回零约8 s；灯随电源点亮并预热）')
cap('power.press_button', obj({}), obj(dict(enabled=BOOL, state=TEXT)), {}, ['button_power'], '按绿色电源键（按一次切换通断；与power.set同一过程）')
cap('vacuum.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['switch_vacuum', 'sample_platform', 'vacuum_holes'],
    '样品真空开关（吸住盖住两个真空小孔的样品；仪器没有真空表，是否吸住不回报）')
cap('vacuum.press_switch', obj({}), obj(dict(on=BOOL)), {}, ['switch_vacuum'], '拨一次样品真空开关（切换）')
cap('aoi.set', obj(dict(position=enum(AOI_LABELS))), obj(dict(position=enum(AOI_LABELS), angle_rad=number('rad'), duration_s=number('s'))), dict(position='70'),
    ['source_arm', 'detector_arm', 'source_plunger', 'detector_plunger'],
    '手动改变入射角：拉出弹簧插销，把两臂移到位置65/70/75（反射）或90（直通）并让插销卡入（位置名为名义角度的度数；返回名义角的rad值；操作者/机器人动作）', 'agent',
    'robot_skill')
cap('stage.move_z', obj(dict(z_m=ref(number('m', STAGE_Z_RANGE_M[0], STAGE_Z_RANGE_M[1]), HEIGHT_REF))), obj(dict(target_m=number('m'), duration_s=number('s'))),
    dict(z_m=STAGE_Z_REF_M), ['sample_stage', 'z_drive'], '电动Z台把样品平台移到给定高度（测量时的自动对准也用它）')
cap('liquid_cell.install', obj(dict(sample_id=dict(TEXT, pattern='[a-z0-9_]{1,32}'))), obj(dict(sample_id=TEXT)), dict(sample_id='pmma_on_si'), ['sample_platform'],
    '把样品装进选配液池（500 µL；载玻片或1–2英寸晶圆）并放到样品平台上（入射角须为70°；液池无场景几何）', 'agent', 'robot_skill')
cap('liquid_cell.remove', obj({}), obj(dict(sample_id=nullable(TEXT))), {}, ['sample_platform'], '取下液池（先排空），样品回到实验台', 'agent', 'robot_skill')
cap('liquid_cell.fill', obj(dict(liquid_id=enum(LIQUIDS), volume_m3=number('m3', 1e-9, LIQUID_CELL_VOLUME_M3))),
    obj(dict(liquid_id=TEXT, volume_m3=number('m3'))), dict(liquid_id='water', volume_m3=450e-9), ['sample_platform'], '向液池注入液体（总量不超过500 µL）', 'agent',
    'robot_skill')
cap('liquid_cell.drain', obj({}), obj(dict(drained_m3=number('m3'))), {}, ['sample_platform'], '排空液池', 'agent', 'robot_skill')
cap('measure', obj(dict(mode=enum(MODES), alignment=enum(ALIGNMENTS), fixed_height_m=nullable(ref(number('m', STAGE_Z_RANGE_M[0], STAGE_Z_RANGE_M[1]), HEIGHT_REF)),
                        model_id=nullable(TEXT))), OPERATION, dict(mode='standard', alignment='standard', fixed_height_m=None, model_id='cauchy_on_si'),
    ['source_housing', 'detector_housing', 'sample_stage', 'monitor_screen'],
    '测量：对准（Z台找反射光）→按模式采集全光谱（快3 s、标准10 s、长30 s；MM模式测11个归一化Mueller矩阵元）→可选立即用模型拟合；90°直通位测空光束')
cap('calibration.run', obj({}), OPERATION, {}, ['source_housing', 'detector_housing', 'sample_stage'],
    '仪器标定：在当前入射角位置用标定片（Si上热氧化层）做长模式测量，拟合起偏器偏置、检偏器方位、补偿器延迟（两参数）、该位置的实际入射角与氧化层厚度')
cap('analysis.fit', FIT_IN, obj(dict(record_id=TEXT, mse=number('1'), parameters=PARAMS_OUT,
                                     nk_632_8nm={'type': 'object', 'additionalProperties': obj(dict(n=number('1'), k=number('1')))}, converged=BOOL)),
    FIT_EXAMPLE, ['monitor_screen'], '模型拟合：Levenberg-Marquardt回归N/C/S（可同时拟合多个入射角的记录；可选厚度预拟合全局搜索），给出MSE、90 %置信区间、相关矩阵与632.8 nm的n/k',
    'agent', 'analysis')
cap('analysis.uniqueness', obj(dict(record_ids=array(TEXT, 1, 4), model_id=TEXT, parameters=PARAMS, fit=array(TEXT, 0, 12), scan_parameter=enum(SCAN_PARAMETERS),
                                    values=array(number('m'), 2, 41))), UNIQ_OUT,
    dict(record_ids=['SE0001'], model_id='cauchy_on_si', parameters={'film.thickness_m': 100e-9}, fit=['film.A', 'film.B'], scan_parameter='film.thickness_m',
         values=[50e-9, 75e-9, 100e-9, 125e-9, 150e-9]), ['monitor_screen'], '唯一性检查：固定一个厚度参数扫描、其余参数重新拟合，给出MSE曲线与局部极小（只扫描厚度，即CompleteEASE最常用的厚度唯一性检查）', 'agent',
    'analysis')
cap('data.pseudo', obj(dict(record_id=TEXT)), obj(dict(record_id=TEXT, wavelengths_m=array(number('m'), 0, N_WAVELENGTHS), n=SPECTRUM, k=SPECTRUM, eps1=SPECTRUM, eps2=SPECTRUM)),
    dict(record_id='SE0001'), ['monitor_screen'], '赝光学常数：把Ψ/Δ当作无膜层基底直接反演<ε>、<n>、<k>', 'agent', 'analysis')
cap('models.list', obj({}), obj(dict(models={'type': 'object', 'additionalProperties': obj(dict(title_zh=TEXT, parameters=array(TEXT, 0, 16), default_fit=array(TEXT, 0, 12),
                                                                                          parameter_units={'type': 'object', 'additionalProperties': TEXT}))})),
    {}, ['monitor_screen'], '列出模型库（CompleteEASE式常用模型）与其参数及各参数单位', 'agent', 'query')
cap('measurement.result', obj(dict(record_id=TEXT)), RECORD, dict(record_id='SE0001'), ['monitor_screen'], '读取记录（测量数据、直通、标定或拟合结果）', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, MAX_RECORDS))), {}, ['monitor_screen'], '列出记录', 'agent', 'query')
cap('records.delete', obj(dict(record_id=TEXT)), obj(dict(record_id=TEXT, remaining=INT)), dict(record_id='SE0001'), ['monitor_screen'],
    '删除一条记录（进行中的操作的记录不能删除）', 'agent', 'configuration')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, 500), dropped=INT)), dict(since_s=0.0), ['monitor_screen'], '读取事件与报警',
    'agent', 'query')
cap('system.acknowledge', obj({}), obj(dict(cleared=nullable(TEXT))), {}, ['monitor_screen'], '确认已排除原因的锁存错误（灯故障已更换、Z台故障已排除）')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 8))), dict(fault='lamp_failure', active=True), ['base_plate'],
    '注入或清除故障（评测者）：灯损坏、真空泄漏、Z台卡滞、入射角插销未卡入', 'evaluator', 'configuration')
cap('ledger.read', obj({}), LEDGER, {}, ['base_plate'], '使用与守恒账本（电能、灯时、行程、测量数、液池体积）', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, library_sha256=TEXT, substances=INT, license=TEXT)), {}, ['monitor_screen'], '模板库来源', 'agent', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['base_plate'], '宿主推进仿真时间', 'evaluator', 'simulation')
UNSUPPORTED = {
    'hardware.connect': '没有真实alpha-SE硬件或CompleteEASE软件接口',
    'robot.handle_sample': '机器人接触放样（镊子夹取、盖住真空孔）未建模；sample.place为宿主抽象',
    'focus_probes.install': '聚焦探头（光斑约0.3 mm）是选件，不在本配置',
    'transmission.measure_sample': '90°直通位透射测样需要竖直样品架（选件），本配置只测空光束',
    'stage.map_xy': '本配置没有XY平移台（不能自动逐点扫描）',
    'heat_stage.set': '加热台是选件，不在本配置',
    'qcm_d.measure': 'QCM-D组合附件是选件，不在本配置',
    'export.dataset': 'CompleteEASE数据文件（.SE）导出未建模',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['base_plate'], _why, 'agent', 'device_command')


SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


CAL_TABLE = obj(dict(status=enum(['factory', 'nominal', 'calibrated']), aoi_rad={'type': 'object', 'additionalProperties': number('rad')},
                     polarizer_offset_rad=number('rad'), analyzer_rad=number('rad'), retardance_scale=number('1'), retardance_slope_rad=number('rad'),
                     calibrated_s=nullable(number('s'))))
slot('clock.time_s', number('s'), 's', 'derived', 'base_plate', '仿真时钟')
slot('plant.configured', BOOL, '1', 'setpoint', 'base_plate', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'button_power', '系统电源')
slot('system.state', enum(['off', 'booting', 'ready']), '1', 'derived', 'monitor_screen', '系统状态（断电/初始化中/就绪）')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'monitor_screen', '锁存的错误代码')
slot('lamp.on', BOOL, '1', 'derived', 'source_housing', 'QTH灯亮（随电源）')
slot('lamp.warmed_up', BOOL, '1', 'derived', 'source_housing', '灯强度已稳定（剩余漂移<1 %）')
slot('lamp.on_time_s', number('s', 0.0), 's', 'derived', 'source_housing', '灯累计点亮时间')
slot('vacuum.switch_on', BOOL, '1', 'setpoint', 'switch_vacuum', '样品真空开关')
slot('vacuum.pressure_pa', number('Pa', 0.0, 101325.0), 'Pa', 'process_truth', 'vacuum_holes',
     '吸盘真空绝对压力（过程真值：alpha-SE没有真空表；样品盖住两个小孔时降到真空源压力，泄漏或没盖住时回升）', 'evaluator', '绝对压力')
slot('sample.bench_ids', array(TEXT, 0, MAX_SAMPLES), '1', 'derived', 'sample_platform', '实验台上的样品')
slot('sample.on_stage', nullable(obj(dict(sample_id=TEXT, offset_x_m=number('m'), offset_y_m=number('m')))), '1', 'derived', 'sample_platform', '平台上的样品及偏移',
     reference=OFFSET_REF)
slot('sample.held', BOOL, '1', 'process_truth', 'sample_platform', '样品被真空吸住（过程真值：仪器没有传感器，代理只能看到真空开关位置）', 'evaluator')
slot('aoi.position', nullable(enum(AOI_LABELS)), '1', 'derived', 'source_plunger', '插销卡入的入射角位置名（65/70/75/90，名义角度的度数；移动中或未卡入为null）')
slot('aoi.arm_elevation_rad', number('rad'), 'rad', 'derived', 'source_arm', '两臂相对直通位的仰角=90°−入射角（场景臂关节镜像）', reference=ARM_REF)
slot('aoi.moving', BOOL, '1', 'derived', 'source_arm', '两臂在移动（手动换角度）')
slot('stage.z_m', number('m'), 'm', 'derived', 'sample_stage', '样品平台顶面高度（场景Z台关节镜像）', reference=HEIGHT_REF)
slot('stage.z_target_m', number('m'), 'm', 'derived', 'sample_stage', 'Z台目标高度', reference=HEIGHT_REF)
slot('stage.moving', BOOL, '1', 'derived', 'sample_stage', 'Z台在移动')
slot('stage.last_aligned_z_m', nullable(number('m')), 'm', 'derived', 'sample_stage', '最近一次对准找到的平台高度', reference=HEIGHT_REF)
slot('liquid_cell.installed', BOOL, '1', 'derived', 'sample_platform', '液池已装在平台上')
slot('liquid_cell.liquid_id', nullable(TEXT), '1', 'derived', 'sample_platform', '液池中的液体')
slot('liquid_cell.volume_m3', number('m3', 0.0), 'm3', 'derived', 'sample_platform', '液池中的液体体积', reference='室温下的液体体积')
slot('measurement.id', nullable(TEXT), '1', 'derived', 'monitor_screen', '进行中的记录ID')
slot('measurement.kind', nullable(TEXT), '1', 'derived', 'monitor_screen', '进行中的操作种类')
slot('measurement.phase', nullable(enum(['aligning', 'acquiring', 'fitting'])), '1', 'derived', 'monitor_screen', '进行中操作的阶段')
slot('measurement.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'monitor_screen', '进行中操作的进度')
slot('measurement.remaining_s', nullable(number('s', 0.0)), 's', 'derived', 'monitor_screen', '进行中操作的剩余时间')
slot('measurement.started_by_other_agent', BOOL, '1', 'derived', 'monitor_screen', '进行中的操作属于另一个agent')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'monitor_screen', '最近完成的记录ID（agent只看到自己或宿主创建的记录）')
slot('result.last_thickness_m', nullable(number('m')), 'm', 'derived', 'monitor_screen', '最近一次拟合的第一层（最上层膜）厚度')
slot('result.last_mse', nullable(number('1')), '1', 'derived', 'monitor_screen', '最近一次拟合的MSE')
slot('calibration.table', CAL_TABLE, '1', 'derived', 'detector_housing', '仪器校准表（各位置入射角、起偏器偏置、检偏器方位、补偿器延迟参数）', reference=ANGLE_REF)
slot('calibration.baseline', obj(dict(valid=BOOL, recorded_s=nullable(number('s')))), '1', 'derived', 'detector_housing', '直通光强基线（反射率%R的参考）')
slot('truth.aoi_rad', {'type': 'object', 'additionalProperties': number('rad')}, 'rad', 'process_truth', 'source_arm', '各位置的实际加工入射角（按位置名）', 'evaluator',
     ANGLE_REF)
slot('truth.instrument', obj(dict(polarizer_offset_rad=number('rad'), analyzer_rad=number('rad'), retardance_scale=number('1'), retardance_slope_rad=number('rad'))),
     '1', 'process_truth', 'detector_housing', '仪器偏振元件的真实参数', 'evaluator')
slot('truth.samples', {'type': 'object'}, '1', 'process_truth', 'sample_platform', '样品真实定义（膜层、厚度、不均匀度、粗糙度）', 'evaluator')
slot('truth.faults', array(TEXT, 0, 8), '1', 'process_truth', 'base_plate', '注入的故障', 'evaluator')
slot('truth.lamp_intensity_rel', number('1'), '1', 'process_truth', 'source_housing', '灯的真实相对强度', 'evaluator')


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


MAX_EVENTS = 500
MAX_STEP_S = 366 * 86400.0                                             # host step bound (sim.step accepts one day per call)
PURE_ANALYSIS = {'analysis.uniqueness', 'data.pseudo'}                # analysis without a record: no state change
CELL_SAMPLE_MAX_M = (0.0762, 0.0508)              # glass slides (1 x 3 in) and 1 or 2 in wafers fit the liquid cell
CELL_SAMPLE_MAX_T_M = 0.0015
CELL_MIN_IMMERSED_M3 = 400e-9                     # the beam path is immersed from this filling on (engineering)
CELL_SURFACE_OFFSET_M = 0.0015                    # the sample surface sits this much above the platform inside the cell (engineering)
CELL_WINDOW_TRANSMISSION = 0.93                   # fused-silica windows at normal incidence, per pass
DEFAULT_WAFER_T_M = 0.000525


ARM_BOXES = ((ARM_PORT_R_M, PORT_HALF_M), (ARM_BODY_R_M, ARM_HALF_HEIGHT_M), (ARM_CAP_R_M, ARM_CAP_HALF_M))   # (a range, half height) in the arm frame


def arm_polygons(elevation_rad):
    """The port, housing and end-cap boxes of both arms as the collision proxies of the asset: rectangles in the arm frame (a outward from the
    measurement point, h up) rotated by the arm elevation about the Y axis through the measurement point; source arm on -X, detector arm on +X.
    Returns their corner lists in the x-z plane of the asset frame."""
    c, s = math.cos(elevation_rad), math.sin(elevation_rad)
    out = []
    for (a0, a1), hh in ARM_BOXES:
        for side in (-1.0, 1.0):
            out.append([(MEASURE_POINT_M[0] + side * (a * c - h * s), MEASURE_POINT_M[2] + a * s + h * c) for a, h in ((a0, -hh), (a1, -hh), (a1, hh), (a0, hh))])
    return out


def arm_lowest_z(elevation_rad, x0, x1):
    """Lowest height of the two arms over the x-interval [x0, x1] of the asset frame: every edge of every proxy rectangle clipped to the slab
    (the minimum of the height over a convex polygon and a slab lies on one of these clipped edge ends - exact, including the beam-side faces of
    the ports that lean over the stage when the arms are raised); inf when no box is above the interval."""
    best = math.inf
    for poly in arm_polygons(elevation_rad):
        for i in range(len(poly)):
            (xa, za), (xb, zb) = poly[i], poly[(i + 1) % len(poly)]
            lo, hi = max(min(xa, xb), x0), min(max(xa, xb), x1)
            if lo > hi:
                continue
            if abs(xb - xa) < 1e-15:
                best = min(best, za, zb)
            else:
                best = min(best, za + (zb - za) * (lo - xa) / (xb - xa), za + (zb - za) * (hi - xa) / (xb - xa))
    return best


class JaAlphaseRuntime:
    """alpha-SE: power and lamp, vacuum chuck, manual angle of incidence, motorized Z stage, optional liquid cell, rotating compensator
    measurements (SE, Mueller matrix, straight through), instrument calibration and CompleteEASE-like analysis (SI units)."""

    def __init__(self, instance_id='alpha_se_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.revision = 0
        self.plant = None
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.error = None
        self.faults = set()
        self.lamp_on_since = None
        self.lamp_on_time_s = 0.0
        self.vacuum_on = False
        self.samples = {}
        self.on_stage = None
        self.cell = None                                          # dict(sample_id, liquid_id, volume_m3) while the liquid cell is installed
        self.arm_el = math.radians(90.0 - 70.0)                   # arm elevation above the straight-through pose (joint)
        self.arm_target = self.arm_el
        self.aoi_pos = 70.0                                       # seated nominal position (None while moving / plunger not seated)
        self.z, self.z_target = STAGE_Z_REF_M, STAGE_Z_REF_M
        self.last_aligned_z = None
        self.records = {}
        self.seq = dict(se=0, mm=0, st=0, ca=0, ft=0, op=0, rng=0)
        self.op = None
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.truth = dict(aoi_deg={p: p for p in AOI_POSITIONS_DEG}, P_offset=0.0, A=math.radians(ANALYZER_AZIMUTH_DEG), e_scale=0.0, e_slope=0.0)
        self.cal = self._nominal_cal()
        self.baseline = None                                      # dict(a_raw, t): straight-through intensity for %R
        self.last_fit = None
        self.used_substances = set()
        self._principals, self._requests, self._owners = {}, {}, {}
        self._arm_seat_at = None                                  # time the AOI plunger seats (an AOI change is in progress while not None)
        self._arm_move_from = 0.0
        self._arm_speed = 0.0
        self._arm_nominal = 70.0
        self._align_speed = 0.0

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
        return JaAlphaseSession(self, name)

    def _event(self, kind, code, message, rid=None, t=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t if t is None else t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _seed(self):
        return self.plant['noise_seed'] if self.plant else 0

    def _rng(self):
        self.seq['rng'] += 1
        return np.random.default_rng([self._seed(), self.seq['rng']])

    @staticmethod
    def _nominal_cal():
        return dict(status='nominal', aoi_rad={f'{p:.0f}': math.radians(p) for p in AOI_POSITIONS_DEG}, polarizer_offset_rad=0.0, analyzer_rad=math.radians(ANALYZER_AZIMUTH_DEG),
                    retardance_scale=0.0, retardance_slope_rad=0.0, calibrated_s=None)

    def _inst_true(self, lam):
        tr = self.truth
        return dict(P_offset=tr['P_offset'], A=tr['A'], delta=retardance_model(lam, tr['e_scale'], tr['e_slope']))

    def _inst_cal(self, lam):
        c = self.cal
        return dict(P_offset=c['polarizer_offset_rad'], A=c['analyzer_rad'], delta=retardance_model(lam, c['retardance_scale'], c['retardance_slope_rad']))

    def _lamp_rel(self, t=None):
        """Relative lamp intensity (1 = new lamp, settled): warm-up after switch-on and ageing with the lamp hours; 0 when off or failed."""
        t = self.t if t is None else t
        if self.lamp_on_since is None or 'lamp_failure' in self.faults:
            return 0.0
        age = 1.0 - LAMP_END_OF_LIFE_LOSS * min(1.5, self.lamp_on_time_s / LAMP_RATED_S)
        return age * (1.0 - LAMP_WARMUP_DEFICIT * math.exp(-(t - self.lamp_on_since) / LAMP_WARMUP_TAU_S))

    def _lamp_drift(self, t=None):
        """Relative rate of change of the lamp intensity (1/s)."""
        t = self.t if t is None else t
        if self.lamp_on_since is None or 'lamp_failure' in self.faults:
            return 0.0
        e = LAMP_WARMUP_DEFICIT * math.exp(-(t - self.lamp_on_since) / LAMP_WARMUP_TAU_S)
        return e / LAMP_WARMUP_TAU_S / (1.0 - e)

    def _lamp_warm(self):
        return self._lamp_rel() > 0 and LAMP_WARMUP_DEFICIT * math.exp(-(self.t - self.lamp_on_since) / LAMP_WARMUP_TAU_S) < 0.01

    def _stage_moving(self):
        return abs(self.z - self.z_target) > 1e-12

    def _arm_moving(self):
        return abs(self.arm_el - self.arm_target) > 1e-12

    def _placed(self):
        """(sample, surface height above the platform) of what lies on the platform: a sample, or the liquid cell with its sample."""
        if self.cell is not None:
            return self.samples[self.cell['sample_id']], CELL_SURFACE_OFFSET_M + sample_height_m(self.samples[self.cell['sample_id']])
        if self.on_stage is not None:
            s = self.samples[self.on_stage]
            return s, sample_height_m(s)
        return None, 0.0

    def _covers_holes(self, s):
        return all(point_on_sample(s, hx - (MEASURE_POINT_M[0] + s['offset_x_m']), hy - (MEASURE_POINT_M[1] + s['offset_y_m']), 0.0007)
                   for hx, hy in [(MEASURE_POINT_M[0] - 0.002, MEASURE_POINT_M[1]), (MEASURE_POINT_M[0] + 0.002, MEASURE_POINT_M[1])])

    def _held(self):
        if self.cell is not None:
            return self.vacuum_on and self._vacuum_ok()             # the cell's base covers the holes
        if self.on_stage is None:
            return False
        return self.vacuum_on and self._vacuum_ok() and self._covers_holes(self.samples[self.on_stage])

    def _vacuum_ok(self):
        return self.plant is not None and self.plant['vacuum_supply_pa'] < VACUUM_HOLD_PA and 'vacuum_leak' not in self.faults

    def _chuck_pressure(self):
        if not (self.vacuum_on and self.plant):
            return 101325.0
        covered = self.cell is not None or (self.on_stage is not None and self._covers_holes(self.samples[self.on_stage]))
        if 'vacuum_leak' in self.faults or not covered:
            return min(101325.0, max(self.plant['vacuum_supply_pa'], 85.0e3))
        return self.plant['vacuum_supply_pa']

    def _sample_extent(self, s, height, z_platform):
        ox, oy = s['offset_x_m'], s['offset_y_m']
        w, l = s['size_m']
        if s['shape'] == 'disc':
            rx = ry = w / 2
        else:
            rx, ry = w / 2, l / 2
        cx, cy = MEASURE_POINT_M[0] + ox, MEASURE_POINT_M[1] + oy
        return (cx - rx, cx + rx), (cy - ry, cy + ry), z_platform + height

    def _clear(self, elevation, z_platform, s=None, height=None, offset=None):
        """Collision guard: a sample (or the liquid cell) on the platform stays below the arms and in front of the Z guide."""
        if s is None:
            s, height = self._placed()
            if s is None:
                return True
        if offset is not None:
            s = dict(s, offset_x_m=offset[0], offset_y_m=offset[1])
        (x0, x1), (y0, y1), ztop = self._sample_extent(s, height, z_platform)
        if y1 > Z_GUIDE_FRONT_Y_M - CLEARANCE_M:                    # the sample may overhang the front of the base, not the Z guide behind it
            return False
        return arm_lowest_z(elevation, x0 - CLEARANCE_M, x1 + CLEARANCE_M) > ztop + CLEARANCE_M      # 2 mm above and beside the sample

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        o = self.op
        prog = rem = phase = None
        if o is not None:
            prog = min(1.0, max(0.0, (self.t - o['t0']) / o['duration_s'])) if o['duration_s'] > 0 else 0.0
            rem = max(0.0, o['t0'] + o['duration_s'] - self.t)
            phase = o['phases'][o['i']][0] if 0 <= o['i'] < len(o['phases']) else None
        s_on = self.on_stage or (self.cell['sample_id'] if self.cell else None)
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.error_code': self.error, 'lamp.on': self._lamp_rel() > 0.0, 'lamp.warmed_up': self._lamp_warm(),
            'lamp.on_time_s': self.lamp_on_time_s, 'vacuum.switch_on': self.vacuum_on, 'vacuum.pressure_pa': self._chuck_pressure(),
            'sample.bench_ids': sorted(k for k, s in self.samples.items() if s['location'] == 'bench'),
            'sample.on_stage': (dict(sample_id=s_on, offset_x_m=self.samples[s_on]['offset_x_m'], offset_y_m=self.samples[s_on]['offset_y_m']) if s_on else None),
            'sample.held': self._held(), 'aoi.position': None if self.aoi_pos is None else f'{self.aoi_pos:.0f}', 'aoi.arm_elevation_rad': self.arm_el, 'aoi.moving': self._arm_seat_at is not None,
            'stage.z_m': self.z, 'stage.z_target_m': self.z_target, 'stage.moving': self._stage_moving(), 'stage.last_aligned_z_m': self.last_aligned_z,
            'liquid_cell.installed': self.cell is not None, 'liquid_cell.liquid_id': self.cell['liquid_id'] if self.cell else None,
            'liquid_cell.volume_m3': self.cell['volume_m3'] if self.cell else 0.0,
            'measurement.id': o['record_id'] if o else None, 'measurement.kind': o['kind'] if o else None, 'measurement.phase': phase,
            'measurement.progress': prog, 'measurement.remaining_s': rem, 'measurement.started_by_other_agent': False,
            'result.last_record_id': self._last_record(), 'result.last_thickness_m': self.last_fit['thickness_m'] if self.last_fit else None,
            'result.last_mse': self.last_fit['mse'] if self.last_fit else None, 'calibration.table': copy.deepcopy(self.cal),
            'calibration.baseline': dict(valid=self.baseline is not None, recorded_s=self.baseline['t'] if self.baseline else None),
            'truth.aoi_rad': {f'{k:.0f}': math.radians(v) for k, v in self.truth['aoi_deg'].items()},
            'truth.instrument': dict(polarizer_offset_rad=self.truth['P_offset'], analyzer_rad=self.truth['A'], retardance_scale=self.truth['e_scale'],
                                     retardance_slope_rad=self.truth['e_slope']),
            'truth.samples': self._truth_samples(), 'truth.faults': sorted(self.faults), 'truth.lamp_intensity_rel': self._lamp_rel(),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _last_record(self, keep=None):
        done = [k for k, r in self.records.items() if r['status'] == 'completed' and (keep is None or keep(k))]
        return max(done, key=lambda k: (self.records[k]['finished_s'] or 0.0, k), default=None)

    def _truth_samples(self):
        return {k: {kk: copy.deepcopy(vv) for kk, vv in s.items()} for k, s in self.samples.items()}

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

    def _need_still(self):
        if self._stage_moving() or self._arm_seat_at is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')

    # ---------------------------------------------------------------- setup, samples, power, vacuum, faults
    def _cmd_plant_configure(self, mains_voltage_v, vacuum_supply_pa, room_k, noise_seed, calibration, lamp_age_s):
        if self.plant is not None and (self.power or self.op is not None):
            raise ActionRejected('POWER_ON')
        r = np.random.default_rng([int(noise_seed), 7157])             # before any change (the schema bounds the seed to 0 ... 2^32 - 1)
        self.plant = dict(mains_voltage_v=mains_voltage_v, vacuum_supply_pa=vacuum_supply_pa, room_k=room_k, noise_seed=noise_seed, calibration=calibration,
                          lamp_age_s=lamp_age_s)
        self.truth = dict(aoi_deg={p: (p + float(r.normal(0.0, TRUE_AOI_SIGMA_DEG)) if p < 90 else 90.0) for p in AOI_POSITIONS_DEG},
                          P_offset=math.radians(float(r.normal(0.0, TRUE_AZIMUTH_SIGMA_DEG))),
                          A=math.radians(ANALYZER_AZIMUTH_DEG + float(r.normal(0.0, TRUE_AZIMUTH_SIGMA_DEG))),
                          e_scale=float(r.normal(0.0, math.radians(TRUE_RETARDANCE_SIGMA_DEG) / math.radians(COMPENSATOR_DELTA0_DEG))),
                          e_slope=math.radians(float(r.normal(0.0, TRUE_RETARDANCE_SIGMA_DEG))))
        if calibration == 'factory':
            c = self._nominal_cal()
            c.update(status='factory', aoi_rad={f'{p:.0f}': math.radians((self.truth['aoi_deg'][p] + float(r.normal(0.0, FACTORY_AOI_ERROR_DEG))) if p < 90 else 90.0)
                                                  for p in AOI_POSITIONS_DEG},
                     polarizer_offset_rad=self.truth['P_offset'] + math.radians(float(r.normal(0.0, FACTORY_AZIMUTH_ERROR_DEG))),
                     analyzer_rad=self.truth['A'] + math.radians(float(r.normal(0.0, FACTORY_AZIMUTH_ERROR_DEG))),
                     retardance_scale=self.truth['e_scale'] + float(r.normal(0.0, math.radians(FACTORY_RETARDANCE_ERROR_DEG) / math.radians(COMPENSATOR_DELTA0_DEG))),
                     retardance_slope_rad=self.truth['e_slope'] + math.radians(float(r.normal(0.0, FACTORY_RETARDANCE_ERROR_DEG))))
            self.cal = c
        else:
            self.cal = self._nominal_cal()
        self.lamp_on_time_s = lamp_age_s
        self.baseline = None
        self._event('power', 'PLANT_CONFIGURED', f'实验室条件已声明：电源{mains_voltage_v:.0f} V，校准{calibration}')   # the vacuum source is not reported (no gauge)
        return dict(plant=dict(self.plant))

    def _cmd_sample_provide(self, **s):
        self._need_plant()
        sample_ok(s)
        if s['sample_id'] in self.samples:
            raise ActionRejected('DUPLICATE_SAMPLE')
        if len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_SAMPLES')
        for m in [s['substrate']] + [L['material'] for L in s['layers']]:
            _check_material(m)
        rec = copy.deepcopy(s)
        rec.update(location='bench', offset_x_m=0.0, offset_y_m=0.0)
        self.samples[s['sample_id']] = rec
        self._event('sample', 'SAMPLE_PROVIDED', f'样品{s["sample_id"]}放在实验台上')
        return dict(sample_id=s['sample_id'], location='bench')

    def _cmd_sample_place(self, sample_id, offset_x_m, offset_y_m):
        self._need_plant()
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if self.on_stage is not None or self.cell is not None:
            raise ActionRejected('STAGE_OCCUPIED')
        self._need_idle()
        self._need_still()
        if not point_on_sample(s, -offset_x_m, -offset_y_m, 0.0):
            raise ActionRejected('NOT_ON_PLATFORM')                # the measurement point must lie on the sample
        cx, cy = MEASURE_POINT_M[0] + offset_x_m, MEASURE_POINT_M[1] + offset_y_m
        if not (PLATFORM_X_M[0] <= cx <= PLATFORM_X_M[1] and PLATFORM_Y_M[0] <= cy <= PLATFORM_Y_M[1]):
            raise ActionRejected('NOT_ON_PLATFORM')
        if not self._clear(self.arm_el, self.z, s, sample_height_m(s), (offset_x_m, offset_y_m)):
            raise ActionRejected('COLLISION_RISK')
        s['location'], s['offset_x_m'], s['offset_y_m'] = 'stage', offset_x_m, offset_y_m
        self.on_stage = sample_id
        self._event('sample', 'SAMPLE_PLACED', f'样品{sample_id}放到样品平台（中心相对测量点{offset_x_m * 1e3:.1f}, {offset_y_m * 1e3:.1f} mm）')
        return dict(sample_id=sample_id, offset_x_m=offset_x_m, offset_y_m=offset_y_m, covers_vacuum_holes=self._covers_holes(s),
                    beam_on_sample=point_on_sample(s, -offset_x_m, -offset_y_m, BEAM_DIAMETER_M / 2 / math.cos(math.radians(75.0))))

    def _cmd_sample_remove(self, sample_id):
        self._need_plant()
        if self.on_stage != sample_id:
            raise ActionRejected('SAMPLE_NOT_ON_STAGE')
        self._need_idle()
        self._need_still()
        if self._held():
            raise ActionRejected('SAMPLE_HELD')
        s = self.samples[sample_id]
        s['location'], s['offset_x_m'], s['offset_y_m'] = 'bench', 0.0, 0.0
        self.on_stage = None
        self._event('sample', 'SAMPLE_REMOVED', f'样品{sample_id}取回实验台')
        return dict(sample_id=sample_id)

    def _power_on(self):
        if not (MAINS_RANGE_V[0] * 0.9 <= self.plant['mains_voltage_v'] <= MAINS_RANGE_V[1] * 1.1):
            raise ActionRejected('MAINS_OUT_OF_RANGE')
        self.power = True
        self.state, self.state_t0 = 'booting', self.t
        self.lamp_on_since = self.t if 'lamp_failure' not in self.faults else None
        self._event('power', 'POWER_ON', '通电：电子单元初始化、补偿器回零；灯点亮并开始预热')
        self._latch_faults()

    def _hardware_phase(self):
        """An operation is aligning or acquiring (the instrument hardware is in use); the fitting phase runs on the workstation computer."""
        return self.op is not None and self.op['phases'][self.op['i']][0] in ('aligning', 'acquiring')

    def _power_off(self):
        if self._hardware_phase():
            self._fail_op('POWER_OFF')
        self.power = False
        self.state = 'off'
        self.lamp_on_since = None
        self.z_target = self.z                                     # the Z stage stops where it is (it keeps its position)
        self._event('power', 'POWER_OFF', '断电')

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            self._power_on()
        elif not enabled and self.power:
            self._power_off()
        return dict(enabled=self.power, state=self.state)

    def _cmd_power_press_button(self):
        self._need_plant()
        return self._cmd_power_set(not self.power)

    def _cmd_vacuum_set(self, on):
        self._need_plant()
        if not on and self.op is not None:
            raise ActionRejected('MEASUREMENT_RUNNING')
        self.vacuum_on = on
        self._event('vacuum', 'VACUUM_ON' if on else 'VACUUM_OFF', '样品真空开' if on else '样品真空关')
        return dict(on=self.vacuum_on)

    def _cmd_vacuum_press_switch(self):
        return self._cmd_vacuum_set(not self.vacuum_on)

    def _latch_faults(self):
        for f in ('lamp_failure', 'stage_stall'):
            if f in self.faults and self.error is None and self.power:
                self._raise_error(FAULT_ERROR[f])

    def _raise_error(self, code):
        if self.error is None:
            self.error = code
            self._event('error', code, ERROR_ZH.get(code, code))

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        was_active, was_held = fault in self.faults, self._held()
        if active:
            self.faults.add(fault)
            self._event('fault', 'FAULT_' + fault.upper(), f'注入故障：{fault}')
            if fault == 'lamp_failure':
                self.lamp_on_since = None
                if self._hardware_phase():                           # the workstation fit of acquired data is not affected
                    self._fail_op('LAMP_FAILURE')
            if fault == 'stage_stall' and self._stage_moving():
                self.z_target = self.z
                if self.op is not None:
                    self._fail_op('STAGE_FAULT')
            if fault == 'vacuum_leak' and was_held and self._hardware_phase() and not self.op['straight']:
                self.records[self.op['record_id']]['warnings'].append('vacuum_lost')     # a held sample comes loose; the measurement goes on
            if fault == 'plunger_not_seated':
                self.aoi_pos = None                                  # the arms are no longer locked
                if self._hardware_phase():
                    self._fail_op('AOI_NOT_SEATED')
            self._latch_faults()
        elif was_active:                                               # clearing a fault that is not active changes nothing
            self.faults.discard(fault)
            self._event('fault', 'FAULT_CLEARED_' + fault.upper(), f'故障清除：{fault}')
            if fault == 'lamp_failure':
                self.lamp_on_time_s = 0.0                            # a new lamp (the ledger keeps the total lamp time)
                if self.power:
                    self.lamp_on_since = self.t                      # the new lamp warms up again
        return dict(faults=sorted(self.faults))

    def _cmd_system_acknowledge(self):
        self._need_plant()
        if self.error is None:
            return dict(cleared=None)
        cause = {'LAMP_FAILURE': 'lamp_failure', 'STAGE_FAULT': 'stage_stall'}.get(self.error)
        if cause in self.faults:
            raise ActionRejected('CAUSE_PERSISTS')
        code, self.error = self.error, None
        self._event('error', 'ACKNOWLEDGED', f'已确认{code}')
        self._latch_faults()
        return dict(cleared=code)

    # ---------------------------------------------------------------- mechanics: angle of incidence, Z stage
    def _cmd_aoi_set(self, position):
        angle_deg = float(position)
        self._need_plant()
        self._need_idle()
        self._need_still()
        if self.cell is not None and angle_deg != LIQUID_CELL_AOI_DEG:
            raise ActionRejected('LIQUID_CELL_INSTALLED')
        target = math.radians(90.0 - angle_deg)
        if self.aoi_pos == angle_deg and abs(target - self.arm_el) < 1e-12:
            return dict(position=position, angle_rad=math.radians(angle_deg), duration_s=0.0)       # already seated there
        if not all(self._clear(e, self.z) for e in np.linspace(min(target, self.arm_el), max(target, self.arm_el), 7)):
            raise ActionRejected('COLLISION_RISK')
        self.arm_target = target
        dur = abs(target - self.arm_el) / ARM_SPEED_RAD_S + 2.0
        self._arm_seat_at = self.t + dur
        self._arm_speed = abs(target - self.arm_el) / max(dur - 2.0, 1e-9) if abs(target - self.arm_el) > 1e-12 else 0.0
        self._arm_move_from = self.t + 1.0                        # 1 s to pull the plunger, 1 s to let it seat
        self._arm_nominal = angle_deg
        self.aoi_pos = None
        self._event('aoi', 'AOI_CHANGE', f'拉出插销，两臂移到{angle_deg:.0f}°')
        return dict(position=position, angle_rad=math.radians(angle_deg), duration_s=dur)

    def _cmd_stage_move_z(self, z_m):
        self._need_ready()
        self._need_idle()
        if self._arm_seat_at is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')              # the arms are being turned by hand
        if 'stage_stall' in self.faults:
            raise ActionRejected('STAGE_FAULT')
        if not self._clear(self.arm_el, z_m) or not self._clear(self.arm_el, self.z):
            raise ActionRejected('COLLISION_RISK')
        self.z_target = z_m
        return dict(target_m=z_m, duration_s=abs(z_m - self.z) / STAGE_Z_SPEED_M_S)

    # ---------------------------------------------------------------- liquid cell (accessory; no scene geometry)
    def _cmd_liquid_cell_install(self, sample_id):
        self._need_plant()
        self._need_idle()
        self._need_still()
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if self.on_stage is not None or self.cell is not None:
            raise ActionRejected('STAGE_OCCUPIED')
        if self.aoi_pos != LIQUID_CELL_AOI_DEG:
            raise ActionRejected('AOI_NOT_70')
        w, l = sorted(s['size_m'], reverse=True)
        if w > CELL_SAMPLE_MAX_M[0] + 1e-9 or l > CELL_SAMPLE_MAX_M[1] + 1e-9 or sample_height_m(s) > CELL_SAMPLE_MAX_T_M:
            raise ActionRejected('SAMPLE_DOES_NOT_FIT_CELL')
        cs = dict(s, offset_x_m=0.0, offset_y_m=0.0, size_m=[0.060, 0.060], shape='disc')
        if not self._clear(self.arm_el, self.z, cs, CELL_SURFACE_OFFSET_M + 0.004):
            raise ActionRejected('COLLISION_RISK')
        s['location'], s['offset_x_m'], s['offset_y_m'] = 'cell', 0.0, 0.0
        self.cell = dict(sample_id=sample_id, liquid_id=None, volume_m3=0.0)
        self._event('liquid_cell', 'CELL_INSTALLED', f'液池（含样品{sample_id}）装到平台上')
        return dict(sample_id=sample_id)

    def _cmd_liquid_cell_fill(self, liquid_id, volume_m3):
        self._need_plant()
        self._need_idle()
        if self.cell is None:
            raise ActionRejected('NO_LIQUID_CELL')
        if self.cell['liquid_id'] not in (None, liquid_id) and self.cell['volume_m3'] > 0:
            raise ActionRejected('DIFFERENT_LIQUID')
        if self.cell['volume_m3'] + volume_m3 > LIQUID_CELL_VOLUME_M3 + 1e-15:
            raise ActionRejected('CELL_OVERFLOW')
        self.cell['liquid_id'] = liquid_id
        self.cell['volume_m3'] += volume_m3
        self.led['liquid_added_m3'].add(volume_m3)
        self.used_substances.add(liquid_id)
        self._event('liquid_cell', 'CELL_FILLED', f'液池注入{liquid_id} {volume_m3 * 1e9:.0f} µL')
        return dict(liquid_id=liquid_id, volume_m3=self.cell['volume_m3'])

    def _cmd_liquid_cell_drain(self):
        self._need_plant()
        self._need_idle()
        if self.cell is None:
            raise ActionRejected('NO_LIQUID_CELL')
        v = self.cell['volume_m3']
        self.led['liquid_drained_m3'].add(v)
        self.cell['volume_m3'], self.cell['liquid_id'] = 0.0, None
        self._event('liquid_cell', 'CELL_DRAINED', f'液池排空{v * 1e9:.0f} µL')
        return dict(drained_m3=v)

    def _cmd_liquid_cell_remove(self):
        self._need_plant()
        self._need_idle()
        self._need_still()
        if self.cell is None:
            raise ActionRejected('NO_LIQUID_CELL')
        if self.cell['volume_m3'] > 0:
            raise ActionRejected('CELL_NOT_EMPTY')
        if self._held():
            raise ActionRejected('SAMPLE_HELD')
        sid = self.cell['sample_id']
        self.samples[sid]['location'] = 'bench'
        self.cell = None
        self._event('liquid_cell', 'CELL_REMOVED', f'液池取下，样品{sid}回到实验台')
        return dict(sample_id=sid)

    # ---------------------------------------------------------------- operations
    def _new_op(self, kind, rid):
        self.seq['op'] += 1
        oid = f'op{self.seq["op"]:05d}'
        self.operations[oid] = dict(operation_id=oid, kind=kind, phase='running', record_id=rid, started_s=self.t, reason=None)
        return oid

    def _new_record(self, prefix, kind, **fields):
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        self.seq[prefix.lower()] += 1
        rid = f'{prefix}{self.seq[prefix.lower()]:04d}'
        rec = dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, reason=None, sample_id=None, aoi_rad=None, aoi_position=None,
                   mode=None, alignment=None, stage_z_m=None, ambient='air', wavelengths_m=[], psi_rad=None, delta_rad=None, N=None, C=None, S=None,
                   depolarization=None, reflectance=None, mueller=None, model_psi_rad=None, model_delta_rad=None, model_curves=None, exposure=None, calibration=None,
                   fit=None,
                   warnings=[], template_source=dict(library=LIBRARY_PATH.name, substances=[]), note_zh='')
        rec.update(fields)
        self.records[rid] = rec
        return rid

    def _alignment_plan(self, alignment, fixed_height_m, s, height):
        """(target platform height, duration, failure code or None, height error of the surface) of an alignment."""
        mz = MEASURE_POINT_M[2]
        if alignment == 'none':
            return self.z, ALIGN_TIME_S['none'], None, (self.z + height) - mz if s is not None else 0.0
        if alignment == 'fixed_height':
            z = fixed_height_m
            return z, ALIGN_TIME_S['fixed_height'] + abs(z - self.z) / STAGE_Z_SPEED_M_S, None, (z + height) - mz
        need = mz - height                                        # platform height that puts the surface at the beam crossing
        if not (STAGE_Z_RANGE_M[0] - 1e-12 <= need <= STAGE_Z_RANGE_M[1] + 1e-12):
            return self.z, ALIGN_TIME_S[alignment], 'ALIGNMENT_FAILED', None
        if alignment == 'standard':
            ref = self.last_aligned_z if self.last_aligned_z is not None else mz - DEFAULT_WAFER_T_M
            if abs(need - ref) > ALIGN_WINDOW_M['standard']:
                return self.z, ALIGN_TIME_S['standard'] + 2 * ALIGN_WINDOW_M['standard'] / STAGE_Z_SPEED_M_S, 'ALIGNMENT_FAILED', None
            dur = ALIGN_TIME_S['standard'] + abs(need - self.z) / STAGE_Z_SPEED_M_S
        else:
            dur = ALIGN_TIME_S['robust'] + (STAGE_Z_RANGE_M[1] - STAGE_Z_RANGE_M[0]) / STAGE_Z_SPEED_M_S + abs(need - self.z) / STAGE_Z_SPEED_M_S
        return need, dur, None, 0.0

    def _measure_checks(self, kind):
        self._need_ready()
        self._need_idle()
        self._need_still()
        if self.aoi_pos is None:
            raise ActionRejected('AOI_NOT_SEATED')
        if self._lamp_rel() <= 0.0:
            raise ActionRejected('LAMP_OFF')

    def _cmd_measure(self, mode, alignment, fixed_height_m, model_id):
        self._measure_checks('measure')
        if (alignment == 'fixed_height') != (fixed_height_m is not None):
            raise ActionRejected('INVALID_ARGUMENT')
        if model_id is not None:
            get_model(model_id)                                       # UNKNOWN_MODEL
        s, height = self._placed()
        straight = self.aoi_pos == 90.0
        if straight:
            if alignment != 'none':
                raise ActionRejected('ALIGNMENT_NOT_POSSIBLE_STRAIGHT_THROUGH')
            if mode in ('mm', 'mm_long'):
                raise ActionRejected('MODE_NOT_AVAILABLE')
            if model_id is not None:
                raise ActionRejected('NO_SAMPLE_IN_BEAM')
        else:
            if s is None:
                raise ActionRejected('NO_SAMPLE')
            if 'stage_stall' in self.faults and alignment in ('standard', 'robust', 'fixed_height'):
                raise ActionRejected('STAGE_FAULT')
            if self.cell is not None and 0 < self.cell['volume_m3'] < CELL_MIN_IMMERSED_M3:
                raise ActionRejected('LIQUID_LEVEL_LOW')
        z_t, dur_align, fail, dz = self._alignment_plan(alignment, fixed_height_m, s if not straight else None, height)
        if not self._clear(self.arm_el, z_t):
            raise ActionRejected('COLLISION_RISK')
        kind = 'straight_through' if straight else ('mm' if mode.startswith('mm') else 'se')
        prefix = {'straight_through': 'ST', 'mm': 'MM', 'se': 'SE'}[kind]
        rid = self._new_record(prefix, kind, sample_id=s['sample_id'] if (s is not None and not straight) else None, mode=mode, alignment=alignment,
                               aoi_position=f'{self.aoi_pos:.0f}', aoi_rad=float(self.cal['aoi_rad'][f'{self.aoi_pos:.0f}']),
                               ambient=(self.cell['liquid_id'] if self.cell and self.cell['volume_m3'] >= CELL_MIN_IMMERSED_M3 else 'air'))
        phases = [('aligning', dur_align), ('acquiring', MODE_TIME_S[mode])] + ([('fitting', FIT_TIME_S)] if model_id else [])
        oid = self._new_op('measure', rid)
        self._start_op(oid, 'measure', rid, phases, dict(mode=mode, alignment=alignment, model_id=model_id, z_align=z_t, align_fail=fail, dz=dz, straight=straight,
                                                          position=self.aoi_pos))
        self._event('measurement', 'MEASURE_START', f'{rid}：{mode}模式，对准{alignment}', rid=rid)
        return dict(operation_id=oid)

    def _cmd_calibration_run(self):
        self._measure_checks('calibration')
        s, height = self._placed()
        if self.aoi_pos == 90.0:
            raise ActionRejected('ALIGNMENT_NOT_POSSIBLE_STRAIGHT_THROUGH')
        if s is None or s['kind'] != 'calibration_wafer' or self.cell is not None:
            raise ActionRejected('CALIBRATION_WAFER_REQUIRED')
        if 'stage_stall' in self.faults:
            raise ActionRejected('STAGE_FAULT')
        z_t, dur_align, fail, dz = self._alignment_plan('robust', None, s, height)
        if not self._clear(self.arm_el, z_t):
            raise ActionRejected('COLLISION_RISK')
        rid = self._new_record('CA', 'calibration', sample_id=s['sample_id'], mode='long', alignment='robust', aoi_position=f'{self.aoi_pos:.0f}',
                               aoi_rad=float(self.cal['aoi_rad'][f'{self.aoi_pos:.0f}']))
        oid = self._new_op('calibration', rid)
        self._start_op(oid, 'calibration', rid, [('aligning', dur_align), ('acquiring', MODE_TIME_S['long']), ('fitting', 2 * FIT_TIME_S)],
                       dict(mode='long', alignment='robust', model_id=None, z_align=z_t, align_fail=fail, dz=dz, straight=False, position=self.aoi_pos))
        self._event('calibration', 'CALIBRATION_START', f'{rid}：{self.aoi_pos:.0f}°位置标定', rid=rid)
        return dict(operation_id=oid)

    def _align_signal(self, s):
        """Reflected intensity at 600 nm relative to the straight-through beam of a new lamp (what the alignment detector locks on)."""
        theta = math.radians(self.truth['aoi_deg'][self.aoi_pos])
        amb = self._ambient_index(np.array([600.0]))
        R = float(sample_mueller(s, [600.0], theta, amb, backside=False, bandwidth_ev=0.0, divergence_rad=0.0, nonuniform=False)['R'][0])
        return R * self._lamp_rel() * (CELL_WINDOW_TRANSMISSION ** 2 if self.cell is not None else 1.0)

    def _start_op(self, oid, kind, rid, phases, extra):
        if extra['align_fail'] is None and extra['alignment'] in ('standard', 'robust'):
            z = extra['z_align'] + float(self._rng().normal(0.0, ALIGN_PRECISION_M))       # residual height error of the alignment
            extra['z_align'] = min(max(z, STAGE_Z_RANGE_M[0]), STAGE_Z_RANGE_M[1])
        ends, t = [], self.t
        for _, d in phases:
            t += d
            ends.append(t)
        self.op = dict(op=oid, kind=kind, record_id=rid, phases=phases, ends=ends, i=0, t0=self.t, duration_s=ends[-1] - self.t, z0=self.z, **extra)
        if phases[0][0] == 'aligning' and extra['align_fail'] is None:
            self.z_target = extra['z_align']
            self._align_speed = abs(extra['z_align'] - self.z) / max(phases[0][1] - ALIGN_TIME_S.get(extra['alignment'], 0.5), 1e-9) if abs(extra['z_align'] - self.z) > 1e-12 else 0.0

    @staticmethod
    def _clear_data(rec):
        """A failed or cancelled operation leaves no measurement data in its record."""
        rec.update(wavelengths_m=[], psi_rad=None, delta_rad=None, N=None, C=None, S=None, depolarization=None, reflectance=None, mueller=None,
                   model_psi_rad=None, model_delta_rad=None, model_curves=None, exposure=None, calibration=None, fit=None)
        rec.pop('_counts', None)
        rec.pop('_plan', None)

    def _fail_op(self, code):
        o = self.op
        if o is None:
            return
        rec = self.records[o['record_id']]
        self._clear_data(rec)
        rec.update(status='failed', finished_s=self.t, reason=code)
        self.operations[o['op']].update(phase='failed', reason=code)
        self.z_target = self.z
        self.op = None
        self._event('measurement', 'MEASURE_FAILED', f'{rec["record_id"]}失败：{ERROR_ZH.get(code, code)}', rid=rec['record_id'])

    def _cancel_op(self, entry):
        if self.op is None or self.op['op'] != entry['operation_id']:
            raise ActionRejected('NOT_RUNNING')
        rec = self.records[entry['record_id']]
        self._clear_data(rec)
        rec.update(status='cancelled', finished_s=self.t, reason='CANCELLED')
        entry.update(phase='cancelled', reason='CANCELLED')
        self.z_target = self.z
        self.op = None
        self._event('measurement', 'MEASURE_CANCELLED', f'{rec["record_id"]}已取消（平台停在当前高度，没有数据）', rid=rec['record_id'])

    def _finish_phase(self):
        o = self.op
        name = o['phases'][o['i']][0]
        rec = self.records[o['record_id']]
        if name == 'aligning':
            if o['align_fail'] is not None:
                self._fail_op(o['align_fail'])
                return
            s, height = self._placed()
            if o['alignment'] in ('standard', 'robust') and s is not None and self._align_signal(s) < ALIGN_MIN_SIGNAL:
                self._fail_op('ALIGNMENT_FAILED')                    # too little reflected light for the alignment detector
                return
            self.z = self.z_target = o['z_align']
            if o['alignment'] in ('standard', 'robust') and s is not None:
                self.last_aligned_z = self.z
            rec['stage_z_m'] = self.z
        elif name == 'acquiring':
            try:
                self._acquire(o, rec)
            except ActionRejected as exc:
                self._fail_op(str(exc))
                return
            self.led['measurements'].add(1.0)
            if o['kind'] == 'measure' and not o['model_id']:
                self._complete(o, rec)
                return
        elif name == 'fitting':
            if o['kind'] == 'calibration':
                try:
                    self._calibrate(o, rec)
                except (ActionRejected, ArithmeticError, ValueError, np.linalg.LinAlgError) as exc:   # the table stays unchanged
                    rec['warnings'].append('calibration_fit_failed:' + (str(exc) if isinstance(exc, ActionRejected) else type(exc).__name__))
            else:
                try:
                    fit = run_fit([rec], o['model_id'], {}, get_model(o['model_id']).default_fit, True)
                except ActionRejected as exc:
                    rec['warnings'].append('fit_failed:' + str(exc))
                    fit = None
                except (ArithmeticError, ValueError, np.linalg.LinAlgError) as exc:     # defensive: step() must never raise
                    rec['warnings'].append('fit_failed:' + type(exc).__name__)
                    fit = None
                if fit is not None:
                    rec['fit'] = fit['fit']
                    rec['model_psi_rad'], rec['model_delta_rad'] = fit['model_psi_rad'][0], fit['model_delta_rad'][0]
                    self._set_last_fit(fit['fit'], rec['record_id'])
            self._complete(o, rec)
            return
        o['i'] += 1

    def _complete(self, o, rec):
        rec.update(status='completed', finished_s=self.t)
        self.operations[o['op']].update(phase='completed')
        self.op = None
        self._event('measurement', 'MEASURE_DONE', f'{rec["record_id"]}完成', rid=rec['record_id'])

    def _set_last_fit(self, fit, rid):
        pp = fit['primary_parameter']
        self.last_fit = dict(thickness_m=fit['parameters'][pp]['value'] if pp else None, mse=fit['mse'], record_id=rid)

    # ---------------------------------------------------------------- the physics of one acquisition
    def _ambient_index(self, lam):
        if self.cell is not None and self.cell['volume_m3'] >= CELL_MIN_IMMERSED_M3:
            return library_index(self.cell['liquid_id'], lam)
        return None

    def _acquire(self, o, rec):
        lam = wavelengths_nm()
        t_mid = self.t - MODE_TIME_S[o['mode']] / 2
        lamp = self._lamp_rel(t_mid)
        if lamp <= 0.0:
            raise ActionRejected('LAMP_FAILURE')
        rate_nominal = lamp_flux_rel(lam) * SOURCE_PHOTON_RATE
        flux = rate_nominal * lamp
        warnings = []
        s, height = self._placed()
        if o['straight']:
            if s is not None and self.z + height > MEASURE_POINT_M[2] - BEAM_DIAMETER_M / 2 - 0.0005:
                raise ActionRejected('BEAM_BLOCKED')
            one = np.ones_like(lam)
            abcd = (one, 0 * one, one, 0 * one)
        else:
            theta = math.radians(self.truth['aoi_deg'][o['position']])
            ambient = self._ambient_index(lam)
            quad = {}
            tr = sample_mueller(s, lam, theta, ambient, info=quad)
            if quad.get('capped'):                                        # an unconverged depolarization average is not delivered as data
                raise ActionRejected('SIMULATION_LIMIT')
            abcd = (tr['R'], -tr['N'] * tr['R'], tr['C'] * tr['R'], tr['S'] * tr['R'])
            dz = (self.z + height) - MEASURE_POINT_M[2]
            f_height = math.exp(-(2 * dz * math.sin(theta)) ** 2 / (2 * DETECTOR_APERTURE_SIGMA_M ** 2))
            if abs(dz) > 20e-6:
                warnings.append(f'height_offset_um:{dz * 1e6:.0f}')
            if f_height < 1.0 - ALIGN_MAX_LOSS:
                raise ActionRejected('NO_SIGNAL')
            foot = BEAM_DIAMETER_M / 2 / math.cos(theta)
            on = point_on_sample(s, -s['offset_x_m'], -s['offset_y_m'], 0.0)
            full = point_on_sample(s, -s['offset_x_m'] - foot, -s['offset_y_m'], 0.0) and point_on_sample(s, -s['offset_x_m'] + foot, -s['offset_y_m'], 0.0)
            if not on:
                raise ActionRejected('NO_SAMPLE_AT_BEAM')
            f_foot = 1.0 if full else 0.6
            if not full:
                warnings.append('beam_overfills_sample')
            f_cell = CELL_WINDOW_TRANSMISSION ** 2 if self.cell is not None else 1.0
            if float(np.max(tr['R'])) * lamp * f_height * f_foot * f_cell < MIN_REFLECTED_SIGNAL:
                raise ActionRejected('NO_SIGNAL')                    # (nearly) non-reflecting sample: nothing above the detection floor
            if not self._held():
                warnings.append('sample_not_held')                   # the sample lies loose on the platform (vacuum off, weak or leaking)
            flux = flux * f_height * f_foot * f_cell
            self.used_substances.update(_material_ids(s))
            rec['template_source'] = dict(library=LIBRARY_PATH.name, substances=sorted(_material_ids(s) | ({self.cell['liquid_id']} if self.cell and self.cell['liquid_id'] else set())))
        if not self._lamp_warm():
            warnings.append('lamp_not_warmed_up')
        if self.cal['status'] == 'nominal':
            warnings.append('instrument_not_calibrated')
        pols = [math.radians(p) for p in POLARIZER_MM_AZIMUTHS_DEG] if o['mode'].startswith('mm') else [math.radians(POLARIZER_AZIMUTH_DEG)]
        counts, plan = rce_acquire([abcd] * len(pols), pols, self._inst_true(lam), flux, MODE_TIME_S[o['mode']], self._rng(), self._lamp_drift(t_mid))
        if plan['peak_counts_per_exposure'] < 0.02 * CCD_FULL_WELL_E:
            warnings.append('low_signal')
        self.led['compensator_steps'].add(COMPENSATOR_STEPS * plan['cycles'] * len(pols))
        cal = self._inst_cal(lam)
        rec['exposure'] = plan
        rec['wavelengths_m'] = [float(v) * 1e-9 for v in lam]
        if o['mode'].startswith('mm'):
            M = mueller_from_stokes(counts, pols, cal, rate_nominal, plan)
            rec['mueller'] = {k: [float(v) for v in M[k]] for k in MM_KEYS}
            N_, C_, S_ = -M['m12'], M['m33'], -M['m43']
            a_raw = M['_m11']
        else:
            a, b, c, d = rce_reduce(counts[0], pols[0], cal, rate_nominal, plan)
            N_, C_, S_, _ = ncs_of_abcd((a, b, c, d))
            a_raw = a
        psi, delta = psi_delta(N_, C_, S_)
        rec.update(N=[float(v) for v in N_], C=[float(v) for v in C_], S=[float(v) for v in S_], psi_rad=[math.radians(float(v)) for v in psi],
                   delta_rad=[math.radians(float(v)) for v in delta],
                   depolarization=[float(v) for v in np.clip(1.0 - np.sqrt(N_ ** 2 + C_ ** 2 + S_ ** 2), 0.0, 1.0)])
        if o['straight']:
            self.baseline = dict(a_raw=np.asarray(a_raw, float), t=self.t)
            rec['note_zh'] = '直通空光束：理想值Ψ=45°、Δ=0°；同时记录反射率基线'
        elif self.baseline is not None:
            rec['reflectance'] = [float(v) for v in np.asarray(a_raw) / self.baseline['a_raw'] / (CELL_WINDOW_TRANSMISSION ** 2 if self.cell is not None else 1.0)]
        rec['warnings'] = list(dict.fromkeys(rec['warnings'] + warnings))   # keeps warnings raised earlier in the operation (vacuum_lost)
        rec['aoi_rad'] = float(self.cal['aoi_rad'][f'{o["position"]:.0f}'])
        rec['_counts'] = [c.copy() for c in counts]
        rec['_plan'] = plan

    def _calibrate(self, o, rec):
        lam = wavelengths_nm()
        s, _ = self._placed()
        pos = o['position']                                           # where the data were taken (the plunger may have come out since)
        nsi, nox, one = library_index('si', lam), library_index('sio2', lam), np.ones(lam.shape, complex)

        def model_ncs(th, d):
            rp, rs = stack_rps(lam, one, [(nox, d)], nsi, th)
            _, N, C, S = rncs(rp, rs)
            return N, C, S
        c = self.cal
        x0 = [c['polarizer_offset_rad'], c['analyzer_rad'], c['retardance_scale'], c['retardance_slope_rad'], c['aoi_rad'][f'{pos:.0f}'], 25e-9]
        x, rms = fit_calibration(rec['_counts'][0], rec['_plan'], lamp_flux_rel(lam) * SOURCE_PHOTON_RATE, lam, math.radians(POLARIZER_AZIMUTH_DEG), model_ncs, x0,
                                 iterations=25)
        if not (rms < 0.01 and abs(math.degrees(x[4]) - pos) < 1.5 and 1e-9 < x[5] < 200e-9):
            rec['warnings'].append('calibration_fit_rejected')
            rec['calibration'] = dict(position=f'{pos:.0f}', aoi_rad=float(x[4]), polarizer_offset_rad=float(x[0]), analyzer_rad=float(x[1]),
                                      retardance_scale=float(x[2]), retardance_slope_rad=float(x[3]), reference_thickness_m=float(x[5]), rms_residual=rms)
            return
        c.update(status='calibrated', polarizer_offset_rad=float(x[0]), analyzer_rad=float(x[1]), retardance_scale=float(x[2]), retardance_slope_rad=float(x[3]),
                 calibrated_s=self.t)
        c['aoi_rad'][f'{pos:.0f}'] = float(x[4])
        rec['calibration'] = dict(position=f'{pos:.0f}', aoi_rad=float(x[4]), polarizer_offset_rad=float(x[0]), analyzer_rad=float(x[1]),
                                  retardance_scale=float(x[2]), retardance_slope_rad=float(x[3]), reference_thickness_m=float(x[5]), rms_residual=rms)
        rec['aoi_rad'] = float(x[4])
        self._event('calibration', 'CALIBRATED', f'{pos:.0f}°位置实际入射角{math.degrees(x[4]):.3f}°，标定片氧化层{x[5] * 1e9:.2f} nm', rid=rec['record_id'])

    # ---------------------------------------------------------------- records, events, ledger
    def _cmd_records_list(self):
        return dict(records=sorted(self.records))

    def _cmd_records_delete(self, record_id):
        if record_id not in self.records:
            raise ActionRejected('UNKNOWN_RECORD')
        if self.op is not None and self.op['record_id'] == record_id:
            raise ActionRejected('RECORD_IN_USE')
        del self.records[record_id]
        self._event('measurement', 'RECORD_DELETED', f'删除记录{record_id}')
        return dict(record_id=record_id, remaining=len(self.records))

    def _out(self, rec):
        return {k: copy.deepcopy(v) for k, v in rec.items() if not k.startswith('_')}

    def _cmd_measurement_result(self, record_id):
        rec = self.records.get(record_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._out(rec)

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def ledger(self):
        out = {k: v.value() for k, v in self.led.items()}
        out['liquid_in_cell_m3'] = self.cell['volume_m3'] if self.cell else 0.0
        out['liquid_residual_m3'] = out['liquid_added_m3'] - out['liquid_drained_m3'] - out['liquid_in_cell_m3']
        return out

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        return dict(library=str(LIBRARY_PATH.relative_to(ROOT)), library_sha256=hashlib.sha256(LIBRARY_PATH.read_bytes()).hexdigest(),
                    substances=len(library()['subs']), license=library()['meta']['license'])

    def _cmd_models_list(self):
        return dict(models=models_catalog())

    # ---------------------------------------------------------------- analysis (workstation; no simulated time)
    def _fit_records(self, record_ids):
        if len(set(record_ids)) != len(record_ids):
            raise ActionRejected('INVALID_ARGUMENT')
        recs = []
        for rid in record_ids:
            r = self.records.get(rid)
            if r is None:
                raise ActionRejected('UNKNOWN_RECORD')
            if r['status'] != 'completed' or r['kind'] not in ('se', 'mm') or r['N'] is None:
                raise ActionRejected('RECORD_NOT_SUITABLE')
            recs.append(r)
        return recs

    def _cmd_analysis_fit(self, record_ids, model_id, parameters, fit, thickness_prefit):
        recs = self._fit_records(record_ids)
        out = run_fit(recs, model_id, parameters, fit, thickness_prefit)
        sids = {r['sample_id'] for r in recs}
        rid = self._new_record('FT', 'fit', sample_id=sids.pop() if len(sids) == 1 else None, ambient=recs[0]['ambient'],
                               wavelengths_m=list(recs[0]['wavelengths_m']), fit=out['fit'],
                               model_curves=[dict(record_id=r['record_id'], model_psi_rad=ps, model_delta_rad=dl)
                                             for r, ps, dl in zip(recs, out['model_psi_rad'], out['model_delta_rad'])],
                               template_source=dict(library=LIBRARY_PATH.name, substances=out['library_ids']),
                               note_zh=f'模型{model_id}拟合{"、".join(record_ids)}')
        self.records[rid].update(status='completed', finished_s=self.t)
        self._set_last_fit(out['fit'], rid)
        self._event('measurement', 'FIT_DONE', f'{rid}：{model_id}，MSE {out["fit"]["mse"]:.3f}', rid=rid)
        f = out['fit']
        return dict(record_id=rid, mse=f['mse'], parameters=copy.deepcopy(f['parameters']), nk_632_8nm=copy.deepcopy(f['nk_632_8nm']), converged=f['converged'])

    def _cmd_analysis_uniqueness(self, record_ids, model_id, parameters, fit, scan_parameter, values):
        return run_uniqueness(self._fit_records(record_ids), model_id, parameters, fit, scan_parameter, values)

    def _cmd_data_pseudo(self, record_id):
        return pseudo_constants(self._fit_records([record_id])[0])

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    # ---------------------------------------------------------------- time
    def step(self, dt_s):
        if not (isinstance(dt_s, (int, float)) and math.isfinite(dt_s) and 0 <= dt_s <= MAX_STEP_S and math.isfinite(self.t + dt_s)):
            raise ActionRejected('INVALID_ARGUMENT')
        if dt_s > 0:
            self.revision += 1                                         # a new state version (clock, motions, operations)
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
        if self._arm_seat_at is not None:
            if self.t < self._arm_move_from:
                c.append(self._arm_move_from - self.t)
            elif self._arm_moving() and self._arm_speed > 0:
                c.append(abs(self.arm_target - self.arm_el) / self._arm_speed)
            c.append(self._arm_seat_at - self.t)
        if self.power and self._stage_moving() and 'stage_stall' not in self.faults:
            v = self._stage_speed()
            if v > 0:
                c.append(abs(self.z_target - self.z) / v)
        o = self.op
        if o is not None and 0 <= o['i'] < len(o['ends']):
            c.append(o['ends'][o['i']] - self.t)
        return max(0.0, min(c))

    def _stage_speed(self):
        o = self.op
        if o is not None and o['phases'][o['i']][0] == 'aligning' and self._align_speed > 0:
            return min(STAGE_Z_SPEED_M_S, self._align_speed)
        return STAGE_Z_SPEED_M_S

    def _advance(self, h):
        if h <= 0:
            return
        if self.power:
            p = LAMP_POWER_W if self._lamp_rel() > 0 else 0.0
            self.led['electrical_energy_j'].add((p + ELECTRONICS_POWER_W) * h)
            if self._lamp_rel() > 0:
                self.led['lamp_on_s'].add(h)
                self.lamp_on_time_s += h
            if self._stage_moving() and 'stage_stall' not in self.faults:
                v = self._stage_speed()
                step = min(abs(self.z_target - self.z), v * h)
                new = self.z_target if abs(self.z_target - self.z) <= step + 1e-12 else self.z + math.copysign(step, self.z_target - self.z)
                self.led['stage_travel_m'].add(abs(new - self.z))
                self.z = new
        if self.vacuum_on:
            self.led['vacuum_on_s'].add(h)
        if self._arm_moving() and self.t + 1e-12 >= self._arm_move_from:
            step = min(abs(self.arm_target - self.arm_el), self._arm_speed * h)
            new = self.arm_target if abs(self.arm_target - self.arm_el) <= step + 1e-12 else self.arm_el + math.copysign(step, self.arm_target - self.arm_el)
            self.led['arm_travel_rad'].add(abs(new - self.arm_el))
            self.arm_el = new

    def _handle_events(self):
        for _ in range(1000):
            changed = False
            if self.state == 'booting' and self.t >= self.state_t0 + BOOT_S - 1e-9:
                self.state = 'ready'
                self._event('power', 'READY', '初始化完成')
                changed = True
            if self._arm_seat_at is not None and self.t >= self._arm_seat_at - 1e-9:
                self.arm_el = self.arm_target
                self._arm_seat_at = None
                if 'plunger_not_seated' not in self.faults:
                    self.aoi_pos = float(self._arm_nominal)
                    self.led['aoi_changes'].add(1.0)
                    self._event('aoi', 'AOI_SEATED', f'插销卡入{self._arm_nominal:.0f}°')
                else:
                    self._event('aoi', 'AOI_NOT_SEATED', '插销没有卡入（两臂位置不确定）')
                changed = True
            if abs(self.z - self.z_target) <= 1e-12 and self.z != self.z_target:
                self.z = self.z_target
            o = self.op
            if o is not None and 0 <= o['i'] < len(o['ends']) and self.t >= o['ends'][o['i']] - 1e-9:
                try:
                    self._finish_phase()
                except Exception as exc:                                 # noqa: BLE001 - a defect must not wedge the host clock
                    self._event('fault', 'DEVICE_ERROR', f'{type(exc).__name__}: {exc}'[:300], rid=o['record_id'])
                    if self.op is o:
                        self._fail_op(str(exc) if isinstance(exc, ActionRejected) else 'DEVICE_ERROR')
                changed = True
            if not changed:
                return




def _check_material(m):
    """Library ids exist; a dispersion model must be physical over the instrument range (380-900 nm on the 2 nm library grid, which also covers the
    band edges): finite, n > 0 and Im(eps) >= 0 (no optical gain) - the schema bounds of the model parameters alone allow unphysical curves."""
    (kind, p), = m.items()
    if kind == 'library':
        substance(p)
        return
    if kind == 'ema':
        for x in (p['a'], p['b']):
            if x != 'void':
                substance(x)
        return
    if not material_physical(m, library()['grid']):
        raise ActionRejected('UNPHYSICAL_MATERIAL')


def material_physical(spec, lam_nm):
    with np.errstate(all='ignore'):
        N = material_index(spec, lam_nm)
    return bool(np.all(np.isfinite(N)) and np.all(np.real(N) > 0.0) and np.all(np.imag(N * N) >= -1e-12))


def _material_ids(s):
    out = set()
    for m in [s['substrate']] + [L['material'] for L in s['layers']]:
        (kind, p), = m.items()
        if kind == 'library':
            out.add(p)
        elif kind == 'ema':
            out.update(x for x in (p['a'], p['b']) if x != 'void')
    return out


def mueller_from_stokes(counts, pols, inst_cal, rate_nominal, plan):
    """First three columns of the Mueller matrix from the Stokes vectors leaving the sample at three polarizer azimuths: M[:, 0] + M[:, 1]
    cos 2P + M[:, 2] sin 2P = 2 S(P); normalised by m11 (11 elements)."""
    Gc = rce_design(compensator_angles(), inst_cal['delta'], inst_cal['A'])
    pinv = np.linalg.pinv(Gc)
    S = [np.einsum('lik,lk->li', pinv, c) / (rate_nominal[:, None] * plan['exposure_s'] * plan['cycles']) for c in counts]
    P = [p + inst_cal['P_offset'] for p in pols]
    A_ = np.array([[1.0, math.cos(2 * p), math.sin(2 * p)] for p in P])
    Ainv = np.linalg.inv(A_)
    cols = np.einsum('ij,jlr->ilr', Ainv, np.stack([2 * s_ for s_ in S]))    # (3 columns, L, 4 rows)
    m11 = cols[0, :, 0]
    out = {'_m11': m11}
    for r in range(4):
        for cix in range(3):
            if r == 0 and cix == 0:
                continue
            out[f'm{r + 1}{cix + 1}'] = cols[cix, :, r] / m11
    return out


# ---------------------------------------------------------------- analysis: CompleteEASE-like models, regression, uniqueness, pseudo optical constants
NK_REF_NM = 632.8
FIT_MAX_ITER = 60
FIT_REL_TOL = 1e-8                                   # stop when an accepted step lowers the residual sum of squares by less than this fraction
PREFIT_POINTS = 300
PREFIT_MAX_M = 2.0e-6                                # thickness pre-fit grid 0 < d <= max(2 um, 2 x start value)
PREFIT_CANDIDATES = 3
CI90_FACTOR = 1.645


class _P:
    """Parameter of an analysis model: default value, unit, bounds, fittable; thicknesses regress in nm, Cauchy B / C in 1e-15 m^2 / 1e-27 m^4 internally."""
    __slots__ = ('default', 'unit', 'lo', 'hi', 'fittable', 'scale')

    def __init__(self, default, unit, lo, hi, fittable=True):
        self.default, self.unit, self.lo, self.hi, self.fittable = float(default), unit, float(lo), float(hi), fittable
        self.scale = {'m': 1e-9, 'm2': 1e-15, 'm4': 1e-27}.get(unit, 1.0)


def _thick(default):
    return _P(default, 'm', 0.0, 20e-6)


def _cauchy_params(pre, A=1.50, B=5e-15, k_amp=0.0):
    return {f'{pre}.A': _P(A, '1', 1.0, 4.0), f'{pre}.B': _P(B, 'm2', -5e-13, 5e-13), f'{pre}.C': _P(0.0, 'm4', -1e-25, 1e-25),
            f'{pre}.k_amp': _P(k_amp, '1', 0.0, 10.0), f'{pre}.k_exp': _P(1.5, '1/eV', 0.0, 20.0), f'{pre}.band_edge_ev': _P(3.26, 'eV', 0.5, 10.0, fittable=False)}


def _cauchy_spec(pre):
    return lambda p: {'cauchy': dict(A=p[f'{pre}.A'], B=p[f'{pre}.B'], C=p[f'{pre}.C'], k_amp=p[f'{pre}.k_amp'], k_exp=p[f'{pre}.k_exp'],
                                     band_edge_ev=p[f'{pre}.band_edge_ev'])}


def _tl_params(pre):
    return {f'{pre}.eps_inf': _P(1.0, '1', 0.5, 10.0), f'{pre}.Eg_ev': _P(3.0, 'eV', 0.1, 8.0), f'{pre}.A_ev': _P(150.0, 'eV', 0.1, 500.0),
            f'{pre}.E0_ev': _P(6.5, 'eV', 0.5, 15.0), f'{pre}.C_ev': _P(3.0, 'eV', 0.05, 20.0)}


def _tl_spec(pre):
    return lambda p: {'tauc_lorentz': dict(eps_inf=p[f'{pre}.eps_inf'], Eg_ev=p[f'{pre}.Eg_ev'], A=p[f'{pre}.A_ev'], E0_ev=p[f'{pre}.E0_ev'], C_ev=p[f'{pre}.C_ev'])}


def _dl_params(pre):
    return {f'{pre}.eps_inf': _P(1.0, '1', 0.5, 20.0), f'{pre}.Ep_ev': _P(9.0, 'eV', 0.0, 30.0), f'{pre}.gamma_ev': _P(0.07, 'eV', 0.001, 10.0),
            f'{pre}.osc_A': _P(5.0, '1', 0.0, 500.0), f'{pre}.osc_E0_ev': _P(2.8, 'eV', 0.1, 20.0), f'{pre}.osc_B_ev': _P(1.0, 'eV', 0.01, 20.0)}


def _dl_spec(pre):
    return lambda p: {'drude_lorentz': dict(eps_inf=p[f'{pre}.eps_inf'], Ep_ev=p[f'{pre}.Ep_ev'], gamma_ev=p[f'{pre}.gamma_ev'], osc_A=p[f'{pre}.osc_A'],
                                            osc_E0_ev=p[f'{pre}.osc_E0_ev'], osc_B_ev=p[f'{pre}.osc_B_ev'])}


def _lib(sid):
    return lambda p: {'library': sid}


ROUGH_PARAMS = {'rough.thickness_m': _P(0.0, 'm', 0.0, 200e-9)}
OPTION_PARAMS = {'options.bandwidth': _P(0.0, '1', 0.0, 1.0, fittable=False), 'options.divergence': _P(0.0, '1', 0.0, 1.0, fittable=False)}   # 1: include in the model
SUBSTRATE_PARAMS = {'substrate.thickness_m': _P(1e-3, 'm', 1e-4, 0.030, fittable=False), 'substrate.backside_polished': _P(0.0, '1', 0.0, 1.0, fittable=False)}


class Model:
    """Analysis model: layers (name, material specification of the parameters, thickness parameter, non-uniformity parameter or None; top
    first), substrate, surface roughness (Bruggeman 50 % void layer of the top material), for transparent substrates the incoherent backside
    reflection of a polished back face (the J.A. Woollam 'backside correction'), and the options to include the pixel bandwidth and the beam
    divergence of the instrument (options.bandwidth / options.divergence = 1, as for thick films in CompleteEASE)."""

    def __init__(self, model_id, title_zh, params, default_fit, primary, layers, substrate, backside=False):
        self.model_id, self.title_zh, self.default_fit, self.primary, self.layers, self.substrate = model_id, title_zh, list(default_fit), primary, layers, substrate
        self.params = dict(params)
        self.params.update(ROUGH_PARAMS)
        self.params.update(OPTION_PARAMS)
        if backside:
            self.params.update(SUBSTRATE_PARAMS)

    def defaults(self):
        return {k: v.default for k, v in self.params.items()}

    def media(self, p, lam, cache):
        lam = np.asarray(lam, float)

        def idx(spec):
            key = (json.dumps(spec, sort_keys=True), lam.shape, lam.tobytes())      # the optical constants depend on the wavelengths too
            if key not in cache:
                if len(cache) >= MODEL_CACHE_MAX:
                    cache.pop(next(iter(cache)))
                cache[key] = material_index(spec, lam.ravel()).reshape(lam.shape)
            return cache[key]
        layers = [(idx(fn(p)), p[tp], p[nu] if nu else 0.0) for _, fn, tp, nu in self.layers]
        Ns = idx(self.substrate(p))
        if p['rough.thickness_m'] > 0:
            top = layers[0][0] if layers else Ns
            layers = [(np.sqrt(bruggeman(top * top, np.ones_like(top), 0.5)), p['rough.thickness_m'], 0.0)] + layers
        return layers, Ns

    def library_ids(self, p):
        out = set()
        for spec in [self.substrate(p)] + [fn(p) for _, fn, _, _ in self.layers]:
            (kind, v), = spec.items()
            if kind == 'library':
                out.add(v)
        return sorted(out)

    def nk_632(self, p):
        out = {}
        for name, fn, _, _ in self.layers:
            N = complex(material_index(fn(p), [NK_REF_NM])[0])
            out[name] = dict(n=N.real, k=N.imag)
        N = complex(material_index(self.substrate(p), [NK_REF_NM])[0])
        out['substrate'] = dict(n=N.real, k=N.imag)
        return out


def _static_models():
    si = _lib('si')
    M = {}
    M['si_native_oxide'] = Model('si_native_oxide', '硅片+自然氧化层（SiO2与Si取模板库）', {'oxide.thickness_m': _thick(2e-9)}, ['oxide.thickness_m'],
                                 'oxide.thickness_m', [('oxide', _lib('sio2'), 'oxide.thickness_m', None)], si)
    M['sio2_on_si'] = Model('sio2_on_si', '硅上热氧化层（SiO2与Si取模板库）', {'film.thickness_m': _thick(100e-9), 'film.nonuniformity': _P(0.0, '1', 0.0, NONUNIFORMITY_MAX)},
                            ['film.thickness_m'], 'film.thickness_m', [('film', _lib('sio2'), 'film.thickness_m', 'film.nonuniformity')], si)
    cauchy = dict(_cauchy_params('film'), **{'film.thickness_m': _thick(100e-9), 'film.nonuniformity': _P(0.0, '1', 0.0, NONUNIFORMITY_MAX), 'interface.thickness_m': _thick(0.0)})
    M['cauchy_on_si'] = Model('cauchy_on_si', '硅上透明膜（Cauchy色散；可加SiO2界面层）', cauchy, ['film.thickness_m', 'film.A', 'film.B'], 'film.thickness_m',
                              [('film', _cauchy_spec('film'), 'film.thickness_m', 'film.nonuniformity'), ('interface', _lib('sio2'), 'interface.thickness_m', None)], si)
    urb = dict(cauchy)
    urb['film.k_amp'] = _P(0.001, '1', 0.0, 10.0)
    M['cauchy_urbach_on_si'] = Model('cauchy_urbach_on_si', '硅上弱吸收膜（Cauchy + Urbach吸收尾）', urb,
                                     ['film.thickness_m', 'film.A', 'film.B', 'film.k_amp', 'film.k_exp'], 'film.thickness_m',
                                     [('film', _cauchy_spec('film'), 'film.thickness_m', 'film.nonuniformity'), ('interface', _lib('sio2'), 'interface.thickness_m', None)], si)
    tl = dict(_tl_params('film'), **{'film.thickness_m': _thick(100e-9), 'interface.thickness_m': _thick(0.0)})
    M['tauc_lorentz_on_si'] = Model('tauc_lorentz_on_si', '硅上非晶/吸收膜（Tauc-Lorentz）', tl, ['film.thickness_m', 'film.A_ev', 'film.E0_ev', 'film.C_ev', 'film.Eg_ev'],
                                    'film.thickness_m', [('film', _tl_spec('film'), 'film.thickness_m', None), ('interface', _lib('sio2'), 'interface.thickness_m', None)], si)
    M['drude_lorentz_bulk'] = Model('drude_lorentz_bulk', '不透明金属块体（Drude + 一个Lorentz带间振子）', _dl_params('substrate'),
                                    ['substrate.Ep_ev', 'substrate.gamma_ev', 'substrate.osc_A', 'substrate.osc_E0_ev', 'substrate.osc_B_ev'], None, [],
                                    _dl_spec('substrate'))
    M['cauchy_on_glass'] = _cauchy_on('soda_lime', 'cauchy_on_glass')
    return M


def _cauchy_on(sub, model_id=None):
    return Model(model_id or f'cauchy_on:{sub}', f'{substance(sub)["name_zh"]}上透明膜（Cauchy；透明基底可加背面反射）',
                 dict(_cauchy_params('film'), **{'film.thickness_m': _thick(100e-9), 'film.nonuniformity': _P(0.0, '1', 0.0, NONUNIFORMITY_MAX)}),
                 ['film.thickness_m', 'film.A', 'film.B'], 'film.thickness_m', [('film', _cauchy_spec('film'), 'film.thickness_m', 'film.nonuniformity')], _lib(sub),
                 backside=True)


MODEL_TEMPLATES = {
    'library_film:{film}@{substrate}': '模板库材料膜层（光学常数固定为库值）在模板库基底上：拟合厚度',
    'library_bulk:{substrate}': '模板库材料块体（可拟合表面粗糙层）',
    'cauchy_on:{substrate}': 'Cauchy透明膜在模板库基底上',
}


@functools.lru_cache(maxsize=256)
def get_model(model_id):
    if not isinstance(model_id, str):
        raise ActionRejected('UNKNOWN_MODEL')
    static = _static_models()
    if model_id in static:
        return static[model_id]
    subs = library()['subs']
    kind, _, rest = model_id.partition(':')
    if kind == 'library_film' and '@' in rest:
        film, sub = rest.split('@', 1)
        if film in subs and sub in subs:
            return Model(model_id, f'{subs[film]["name_zh"]}膜（库值）在{subs[sub]["name_zh"]}上',
                         {'film.thickness_m': _thick(100e-9), 'film.nonuniformity': _P(0.0, '1', 0.0, NONUNIFORMITY_MAX)}, ['film.thickness_m'], 'film.thickness_m',
                         [('film', _lib(film), 'film.thickness_m', 'film.nonuniformity')], _lib(sub), backside=True)
    if kind == 'library_bulk' and rest in subs:
        return Model(model_id, f'{subs[rest]["name_zh"]}块体（库值）', {}, ['rough.thickness_m'], None, [], _lib(rest), backside=True)
    if kind == 'cauchy_on' and rest in subs:
        return _cauchy_on(rest)
    raise ActionRejected('UNKNOWN_MODEL')


def models_catalog():
    out = {k: dict(title_zh=m.title_zh, parameters=sorted(m.params), default_fit=list(m.default_fit), parameter_units={n: m.params[n].unit for n in sorted(m.params)})
           for k, m in _static_models().items()}
    for k, v in MODEL_TEMPLATES.items():
        m = get_model(k.format(film='sio2', substrate='si') if 'film' in k else k.format(substrate='n_bk7'))
        out[k] = dict(title_zh=v + '（示例参数；{}内填模板库物质ID）', parameters=sorted(m.params), default_fit=list(m.default_fit),
                      parameter_units={n: m.params[n].unit for n in sorted(m.params)})
    return out


def _rec_data(rec):
    lam = np.asarray(rec['wavelengths_m'], float) * 1e9
    amb = np.ones(lam.shape, complex) if rec['ambient'] == 'air' else np.real(library_index(rec['ambient'], lam)) + 0j    # transparent ambient
    return dict(record_id=rec['record_id'], lam=lam, theta=rec['aoi_rad'], amb=amb, N=np.asarray(rec['N'], float), C=np.asarray(rec['C'], float),
                S=np.asarray(rec['S'], float))


def _shared_caches(data):
    """One material cache per distinct wavelength grid (records of the same spectrometer share it)."""
    pool = {}
    return [pool.setdefault(d['lam'].tobytes(), {}) for d in data]


class _Unphysical(Exception):
    pass


MODEL_CACHE_MAX = 256                                # optical-constant arrays kept per record set (the pixel-band levels add wavelength grids)


def _physical(N):
    return bool(np.all(np.isfinite(N)) and np.all(np.real(N) > 0.0) and np.all(np.imag(N * N) >= -1e-12))


def model_ncs(mdl, p, d, cache, info=None):
    """Model N, C, S of one data set (wavelengths, angle, ambient) for the parameters p with the same depolarized_average as the simulated
    instrument: exact stack reflection averaged over the thickness non-uniformity (when a layer has one), over the pixel bandwidth (options.bandwidth
    = 1: every band node at its own wavelength and optical constants) and over the beam divergence (options.divergence = 1), plus the incoherent
    backside beams of a polished transparent substrate; refinement tolerance QUAD_TOL_MODEL, work cap QUAD_BUDGET_MODEL (info['capped']).  An
    unphysical material (n <= 0, gain) at any node raises _Unphysical (the regression refuses the step)."""
    lam = d['lam']
    N0, th = d['amb'], d['theta']
    layers, Ns = mdl.media(p, lam, cache)
    if not all(_physical(N) for N in [Ns] + [x[0] for x in layers]):
        raise _Unphysical()
    bw = BANDWIDTH_SIGMA_EV if p.get('options.bandwidth', 0.0) >= 0.5 else 0.0
    dv = BEAM_DIVERGENCE_RAD if p.get('options.divergence', 0.0) >= 0.5 else 0.0
    polished = p.get('substrate.backside_polished', 0.0) >= 0.5

    def media_at(lam_b):
        if lam_b.shape[0] == 1 and np.array_equal(lam_b[0], lam):
            lay_b, Ns_b = [(N[None], d_, sg) for N, d_, sg in layers], Ns[None]
        else:
            lay_b, Ns_b = mdl.media(p, lam_b, cache)
            if not all(_physical(N) for N in [Ns_b] + [x[0] for x in lay_b]):
                raise _Unphysical()
        N0_b = np.interp(lam_b.ravel(), lam, np.real(N0)).reshape(lam_b.shape) + 0j
        return N0_b, lay_b, Ns_b
    tot = depolarized_average(lam, th, media_at, layers, N0, bw, dv, p['substrate.thickness_m'] if polished else None, QUAD_TOL_MODEL, QUAD_BUDGET_MODEL, info)
    r = normalised(tot)
    return r['N'], r['C'], r['S']


def _residuals(mdl, p, data, caches):
    out = []
    try:
        for d, cache in zip(data, caches):
            N, C, S = model_ncs(mdl, p, d, cache)
            out += [d['N'] - N, d['C'] - C, d['S'] - S]
    except _Unphysical:
        return np.full(sum(3 * d['lam'].size for d in data), np.inf)
    r = np.concatenate(out)
    return r if np.all(np.isfinite(r)) else np.full(r.shape, np.inf)


def _lm(mdl, p0, names, data, caches, max_iter=FIT_MAX_ITER):
    """Levenberg-Marquardt (Marquardt diagonal scaling, box bounds by projection; a step into an unphysical material is refused) of the named
    parameters; returns (parameters, residuals, Jacobian at the solution in internal units, iterations, converged)."""
    P = mdl.params
    p = dict(p0)
    if not names:
        return p, _residuals(mdl, p, data, caches), None, 0, True
    sc = np.array([P[n].scale for n in names])
    lo, hi = np.array([P[n].lo for n in names]) / sc, np.array([P[n].hi for n in names]) / sc
    x = np.clip(np.array([p0[n] for n in names]) / sc, lo, hi)

    def res(v):
        q = dict(p0)
        q.update({n: float(vi * si) for n, vi, si in zip(names, v, sc)})
        return _residuals(mdl, q, data, caches)

    def jac(v, r):
        J = np.zeros((r.size, len(names)))
        for j in range(len(names)):
            h = 1e-4 * max(abs(v[j]), 1.0)
            for step_ in (h, -h):
                vp = v.copy()
                vp[j] = v[j] + step_
                if not (lo[j] <= vp[j] <= hi[j]):
                    continue
                rp_ = res(vp)
                if np.all(np.isfinite(rp_)):
                    J[:, j] = (rp_ - r) / step_
                    break
        return J
    r = res(x)
    cost = float(r @ r)
    if not math.isfinite(cost):
        return p, r, None, 0, False
    lam_, converged, it = 1e-2, False, 0
    for it in range(1, max_iter + 1):
        J = jac(x, r)
        g, H = J.T @ r, J.T @ J
        D = np.diag(H).copy()
        dmax = max(float(D.max()), 1e-300)
        D = np.where(D > 1e-12 * dmax, D, dmax * 1e-12)
        improved, rel = False, 0.0
        while lam_ < 1e12:
            try:
                dx = -np.linalg.solve(H + lam_ * np.diag(D), g)
            except np.linalg.LinAlgError:
                lam_ *= 10
                continue
            xn = np.clip(x + dx, lo, hi)
            rn = res(xn)
            cn = float(rn @ rn)
            if cn < cost:
                rel = (cost - cn) / max(cost, 1e-300)
                x, r, cost = xn, rn, cn
                lam_ = max(lam_ / 5, 1e-9)
                improved = True
                break
            lam_ *= 4
        if not improved or rel < FIT_REL_TOL:
            converged = True
            break
    p.update({n: float(vi * si) for n, vi, si in zip(names, x, sc)})
    return p, r, jac(x, r), it, converged


def _covariance(J, sc, s2, names):
    """Linearised covariance (s^2 (J^T J)^-1 from the SVD of the internal-unit Jacobian) and the parameters that the data do not determine
    (their direction lies in the numerical null space of J: no sensitivity or exactly compensated by others)."""
    _, sv, Vt = np.linalg.svd(J, full_matrices=False)
    keep = sv > 1e-9 * (sv[0] if sv.size and sv[0] > 0 else 1.0)
    V = Vt.T
    cov = (V[:, keep] / sv[keep] ** 2) @ V[:, keep].T * s2 * np.outer(sc, sc)
    null = V[:, ~keep]
    undetermined = [n for i, n in enumerate(names) if null.shape[1] and float(np.sum(null[i] ** 2)) > 1e-6]
    return cov, undetermined


def _check_params(mdl, parameters, fit):
    for k, v in parameters.items():
        P = mdl.params.get(k)
        if P is None:
            raise ActionRejected('UNKNOWN_PARAMETER')
        if not (P.lo - 1e-15 <= v <= P.hi + 1e-15):
            raise ActionRejected('PARAMETER_OUT_OF_RANGE')
    if len(set(fit)) != len(fit):
        raise ActionRejected('INVALID_ARGUMENT')
    for k in fit:
        if k not in mdl.params:
            raise ActionRejected('UNKNOWN_PARAMETER')
        if not mdl.params[k].fittable:
            raise ActionRejected('PARAMETER_NOT_FITTABLE')


UNIQUENESS_MAX_ITER = 20


def _prepare(recs, mdl, parameters, fit):
    _check_params(mdl, parameters, fit)
    p0 = mdl.defaults()
    p0.update({k: float(v) for k, v in parameters.items()})
    data = [_rec_data(r) for r in recs]
    if not all(np.all(np.isfinite(d[k])) for d in data for k in ('N', 'C', 'S')) or not all(math.isfinite(d['theta']) for d in data):
        raise ActionRejected('FIT_FAILED')                                 # data that cannot be regressed
    return p0, data, _shared_caches(data)


def run_fit(recs, model_id, parameters, fit, thickness_prefit):
    """Regression of the model on the N, C, S of one to four records (for example the same sample at several angles).  MSE = 1000 sqrt(sum
    (dN^2 + dC^2 + dS^2) / (3 n - m)) (J.A. Woollam, unweighted N / C / S); 90 % confidence limits 1.645 sqrt(diag(s^2 (J^T J)^-1)) with
    s^2 = sum r^2 / (3 n - m) from the SVD of the Jacobian - a parameter the data do not determine gets no confidence limit and a warning;
    correlations from the same covariance.  With the thickness pre-fit the primary thickness is first searched on a grid inside its bounds
    (other parameters at their start values) and the best local minima are refined.  A start point with an unphysical material is rejected
    (UNPHYSICAL_MATERIAL), data or models that cannot be evaluated give FIT_FAILED."""
    mdl = get_model(model_id)
    p0, data, caches = _prepare(recs, mdl, parameters, fit)
    n3 = sum(3 * d['lam'].size for d in data)
    m = len(fit)
    if n3 - m <= 0:
        raise ActionRejected('INVALID_ARGUMENT')
    if not np.all(np.isfinite(_residuals(mdl, p0, data, caches))):
        raise ActionRejected('UNPHYSICAL_MATERIAL')
    prefit = None
    try:
        if thickness_prefit and mdl.primary in fit:
            tp = mdl.primary
            hi = mdl.params[tp].hi
            grid = np.linspace(0.0, min(hi, max(PREFIT_MAX_M, 2 * p0[tp])), PREFIT_POINTS + 1)[1:]
            costs = []
            for v in grid:
                q = dict(p0)
                q[tp] = float(v)
                r = _residuals(mdl, q, data, caches)
                costs.append(float(r @ r))
            costs = np.array(costs)
            fin = np.isfinite(costs)
            mins = [i for i in range(len(grid)) if fin[i] and (i == 0 or not fin[i - 1] or costs[i] <= costs[i - 1])
                    and (i == len(grid) - 1 or not fin[i + 1] or costs[i] <= costs[i + 1])]
            best = None
            for i in sorted(mins, key=lambda i: costs[i])[:PREFIT_CANDIDATES]:
                q = dict(p0)
                q[tp] = float(grid[i])
                out = _lm(mdl, q, fit, data, caches)
                c = float(out[1] @ out[1])
                if math.isfinite(c) and (best is None or c < best[0]):
                    best = (c, out, float(grid[i]))
            if best is None:
                raise ActionRejected('FIT_FAILED')
            _, (p, r, J, it, conv), start = best
            prefit = dict(parameter=tp, grid_min=float(grid[0]), grid_max=float(grid[-1]), best=start, points=int(len(grid)))
        else:
            p, r, J, it, conv = _lm(mdl, p0, fit, data, caches)
        cost = float(r @ r)
        if not math.isfinite(cost) or (m and J is None):
            raise ActionRejected('FIT_FAILED')
        dof = n3 - m
        s2 = cost / dof
        ci, corr, warnings = {}, [], []
        if m:
            sc = np.array([mdl.params[n].scale for n in fit])
            cov, undetermined = _covariance(J, sc, s2, fit)
            sd = np.sqrt(np.clip(np.diag(cov), 0.0, None))
            ci = {n: (None if n in undetermined else float(CI90_FACTOR * sd[i])) for i, n in enumerate(fit)}
            warnings += ['parameter_not_determined:' + n for n in undetermined]
            det = np.array([n not in undetermined for n in fit])
            den = np.outer(sd, sd)
            C = np.where((den > 0) & np.outer(det, det), cov / np.where(den > 0, den, 1.0), 0.0)
            np.fill_diagonal(C, 1.0)
            corr = [[float(min(1.0, max(-1.0, v))) for v in row] for row in C]
        params_out = {k: dict(value=float(p[k]), ci90=ci.get(k), fitted=k in fit, unit=P.unit) for k, P in mdl.params.items()}
        curves_psi, curves_delta, rms, capped = [], [], [], False
        for d, cache in zip(data, caches):
            quad = {}
            N, C_, S = model_ncs(mdl, p, d, cache, quad)
            capped = capped or bool(quad.get('capped'))
            ps, dl = psi_delta(N, C_, S)
            curves_psi.append([math.radians(float(v)) for v in ps])
            curves_delta.append([math.radians(float(v)) for v in dl])
            rr = np.concatenate([d['N'] - N, d['C'] - C_, d['S'] - S])
            rms.append(float(np.sqrt(np.mean(rr * rr))))
        nk = mdl.nk_632(p)
    except (np.linalg.LinAlgError, _Unphysical, FloatingPointError, ValueError):
        raise ActionRejected('FIT_FAILED')
    if not conv:
        warnings.append('not_converged')
    if capped:
        warnings.append('model_average_capped')                    # the work cap stopped a refinement of the model's depolarization average
    block = dict(model_id=model_id, source_records=[d['record_id'] for d in data], parameters=params_out, mse=float(1000.0 * math.sqrt(s2)), iterations=int(it),
                 converged=bool(conv), prefit=prefit, nk_632_8nm=nk, correlation=corr, fitted_parameters=list(fit), residual_rms=rms,
                 primary_parameter=mdl.primary, warnings=warnings)
    return dict(fit=block, model_psi_rad=curves_psi, model_delta_rad=curves_delta, library_ids=mdl.library_ids(p))


def run_uniqueness(recs, model_id, parameters, fit, scan_parameter, values):
    """MSE versus a fixed parameter with the other fit parameters refitted at every value (from the start values and continued from the
    previous value; the better of the two; at most 20 iterations per fit)."""
    mdl = get_model(model_id)
    P = mdl.params.get(scan_parameter)
    if P is None:
        raise ActionRejected('UNKNOWN_PARAMETER')
    if any(not (P.lo - 1e-15 <= v <= P.hi + 1e-15) for v in values):
        raise ActionRejected('PARAMETER_OUT_OF_RANGE')
    start, data, caches = _prepare(recs, mdl, parameters, fit)
    names = [n for n in fit if n != scan_parameter]
    n3 = sum(3 * d['lam'].size for d in data)
    mse, prev = [], None
    try:
        for v in values:
            best = None
            for p0 in ([start] if prev is None else [start, prev]):  # from the start values and continued from the previous scan value
                q = dict(p0)
                q[scan_parameter] = float(v)
                p, r, _, _, _ = _lm(mdl, q, names, data, caches, max_iter=UNIQUENESS_MAX_ITER)
                c = float(r @ r)
                if math.isfinite(c) and (best is None or c < best[0]):
                    best = (c, p)
            if best is None:
                raise ActionRejected('UNPHYSICAL_MATERIAL')          # no physical model at this scan value
            prev = best[1]
            mse.append(float(1000.0 * math.sqrt(best[0] / (n3 - len(names)))))
    except (np.linalg.LinAlgError, _Unphysical, FloatingPointError, ValueError):
        raise ActionRejected('FIT_FAILED')
    minima = [float(values[i]) for i in range(1, len(values) - 1) if mse[i] < mse[i - 1] and mse[i] < mse[i + 1]]
    i = int(np.argmin(mse))
    return dict(parameter=scan_parameter, values=[float(v) for v in values], mse=mse, best_value=float(values[i]), local_minima=minima)


def pseudo_constants(rec):
    """Pseudo dielectric function of the data taken as a bare substrate: <eps> = eps_a sin^2(theta) [1 + tan^2(theta) ((1 - rho) / (1 + rho))^2]
    with rho = tan(Psi) exp(-i Delta) (Delta in the J.A. Woollam convention; the exp(-i w t) phase of the transfer-matrix code)."""
    lam = np.asarray(rec['wavelengths_m'], float) * 1e9
    th = rec['aoi_rad']
    eps_a = (np.ones(lam.shape, complex) if rec['ambient'] == 'air' else np.real(library_index(rec['ambient'], lam)) + 0j) ** 2
    rho = np.tan(np.asarray(rec['psi_rad'], float)) * np.exp(-1j * np.asarray(rec['delta_rad'], float))
    eps = eps_a * math.sin(th) ** 2 * (1.0 + math.tan(th) ** 2 * ((1.0 - rho) / (1.0 + rho)) ** 2)
    N = np.sqrt(eps)                                                  # principal root: <n> >= 0 (<k> < 0 is possible for films)
    return dict(record_id=rec['record_id'], wavelengths_m=[float(v) * 1e-9 for v in lam], n=[float(v) for v in N.real], k=[float(v) for v in N.imag],
                eps1=[float(v) for v in eps.real], eps2=[float(v) for v in eps.imag])


# ---------------------------------------------------------------- session: principals, request idempotency, agent filtering
ASYNC = {'measure', 'calibration.run'}
_DATA_GUARDED = {'measurement.result', 'records.delete', 'data.pseudo'}
_RECORD_ARG = {'measurement.result': 'record_id', 'records.delete': 'record_id', 'data.pseudo': 'record_id'}
_RECORD_LIST_ARG = {'analysis.fit': 'record_ids', 'analysis.uniqueness': 'record_ids'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED}
_TRUTH_KINDS = {'se', 'mm', 'calibration', 'straight_through'}      # records whose template_source lists the true sample materials
_TRUTH_WARNINGS = {'sample_not_held', 'vacuum_lost', 'depolarization_average_capped'}   # evaluator knowledge: the chuck has no gauge; simulation notes


class JaAlphaseSession:
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
        meta = copy.deepcopy(export_ja_alphase_contract_metadata())
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
            if rec.get('kind') in _TRUTH_KINDS:
                rec['template_source'] = dict(library=rec['template_source']['library'], substances=[])
            if 'warnings' in rec:
                rec['warnings'] = [w for w in rec['warnings'] if w not in _TRUTH_WARNINGS]
        return rec

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
            if h.last_fit is not None and self._foreign(h.last_fit.get('record_id')):
                values['result.last_thickness_m'] = values['result.last_mse'] = None    # another agent's fit
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
            owner = h._owners.get(rid)
            if capability_id == 'records.delete' and owner is not None and owner != self._principal:
                raise ActionRejected('ACCESS_DENIED')                  # an agent deletes only its own records and those of the host
        if capability_id in _RECORD_LIST_ARG and any(self._foreign(r) for r in arguments.get(_RECORD_LIST_ARG[capability_id], [])):
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
                    result = self._redact(result)
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
                rid = h.operations[operation].get('record_id')
                if rid:
                    h._owners[rid] = self._principal
            if capability_id == 'analysis.fit':
                h._owners[result['record_id']] = self._principal
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=operation,
                            status='accepted' if operation else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                            result=copy.deepcopy(result), observations={}, error=None)
            h._requests[key] = (digest, copy.deepcopy(response))
            return response
        except ActionRejected as exc:
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None,
                            status='unsupported' if str(exc) == 'UNSUPPORTED' else 'rejected', state_revision=h.revision,
                            clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                            error=dict(code=str(exc), message=ERROR_ZH.get(str(exc), str(exc)), retryable=False,
                                       side_effects='unknown' if str(exc) == 'DEVICE_ERROR' else 'none'))
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
        if h._owners.get(operation_id) != self._principal and self._role != 'evaluator':
            raise ActionRejected('ACCESS_DENIED')                       # evaluators observe every operation
        if op.get('record_id') in h.records:
            res = self._redact(h._out(h.records[op['record_id']]))
        else:
            res = {k: v for k, v in op.items() if k in ('phase', 'reason')}
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


# ---------------------------------------------------------------- contract metadata
ERROR_ZH = {
    'SIMULATION_LIMIT': '样品的退偏平均在计算上限内不收敛（很厚且很不均匀的多层膜），测量以failed结束、不给出光谱（仿真边界，不是仪器故障）',
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'DEVICE_ERROR': '仿真内部错误（调用或操作以失败结束，状态可能已部分改变；详情在评测者可见的fault事件中）', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（评测槽、另一个agent的记录或操作）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_ON': '通电或有操作时不能重新声明实验室条件', 'POWER_OFF': '系统未通电', 'NOT_READY': '系统初始化中',
    'ERROR_PENDING': '有未确认的错误', 'CAUSE_PERSISTS': '错误原因仍存在，不能确认', 'MAINS_OUT_OF_RANGE': '电源电压超出100–240 VAC ±10 %',
    'SAMPLE_SIZE_OUT_OF_RANGE': '样品尺寸超出5–200 mm', 'SAMPLE_TOO_THICK': '样品总厚超过16 mm', 'DUPLICATE_SAMPLE': '样品ID已存在',
    'TOO_MANY_SAMPLES': '样品数量超过上限（12）', 'UNKNOWN_MATERIAL': '模板库中没有该材料', 'OUT_OF_RANGE': '波长超出模板库范围',
    'UNKNOWN_SAMPLE': '样品不存在', 'SAMPLE_NOT_ON_BENCH': '样品不在实验台上', 'STAGE_OCCUPIED': '平台上已有样品或液池',
    'NOT_ON_PLATFORM': '测量点不在样品上，或样品中心不在样品平台上', 'COLLISION_RISK': '样品（或液池）会碰到光源臂/探测臂或Z导轨（碰撞保护，留2 mm）',
    'SAMPLE_NOT_ON_STAGE': '该样品不在平台上', 'SAMPLE_HELD': '样品被真空吸住（先关真空）', 'MEASUREMENT_RUNNING': '有测量或标定在进行',
    'MOTION_IN_PROGRESS': 'Z台在移动或正在换入射角', 'LIQUID_CELL_INSTALLED': '装着液池时入射角只能是70°', 'STAGE_FAULT': 'Z台故障（卡滞）',
    'AOI_NOT_70': '装液池前入射角插销须卡在70°', 'SAMPLE_DOES_NOT_FIT_CELL': '液池只放载玻片（3×1英寸）或1–2英寸晶圆（厚≤1.5 mm）',
    'NO_LIQUID_CELL': '没有装液池', 'DIFFERENT_LIQUID': '液池中已有另一种液体（先排空）', 'CELL_OVERFLOW': '液体总量超过液池500 µL',
    'CELL_NOT_EMPTY': '液池未排空', 'LIQUID_LEVEL_LOW': '液量不足400 µL，光路没有完全浸没（排空测空气或加满）', 'AOI_NOT_SEATED': '入射角插销没有卡入',
    'LAMP_OFF': '灯不亮', 'UNKNOWN_MODEL': '模型不存在', 'ALIGNMENT_NOT_POSSIBLE_STRAIGHT_THROUGH': '90°直通位没有反射光，只能不对准（none）测空光束',
    'MODE_NOT_AVAILABLE': '90°直通位不能用Mueller矩阵模式', 'NO_SAMPLE_IN_BEAM': '直通测量光路中没有样品，不能拟合模型', 'NO_SAMPLE': '平台上没有样品',
    'UNPHYSICAL_MATERIAL': '色散模型在380–900 nm内不物理（n≤0、增益Im(ε)<0或不是有限值）', 'FIT_FAILED': '拟合无法计算（数据或模型给出非有限值）',
    'RECORDS_FULL': '记录数已达上限（200）', 'CALIBRATION_WAFER_REQUIRED': '标定需要把标定片直接放在平台上',
    'ALIGNMENT_FAILED': '自动对准失败（反射光太弱、高度超出搜索窗口或Z行程）', 'NO_SIGNAL': '探测器收不到足够的反射光（样品高度偏离光束交点，或样品几乎不反射）',
    'BEAM_BLOCKED': '直通光束被样品挡住（先降低Z台）', 'NO_SAMPLE_AT_BEAM': '光束不在样品上', 'LAMP_FAILURE': '灯损坏', 'CANCELLED': '操作被取消',
    'UNKNOWN_RECORD': '记录不存在', 'RECORD_IN_USE': '记录属于进行中的操作', 'RECORD_NOT_SUITABLE': '记录种类或状态不适用（须为已完成的SE或MM测量）',
    'UNKNOWN_PARAMETER': '模型没有该参数', 'PARAMETER_OUT_OF_RANGE': '参数超出模型范围', 'PARAMETER_NOT_FITTABLE': '该参数只能固定（不能拟合）',
    'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作不在进行中',
}
EFFECTS = {
    'plant.configure': ['calibration.baseline', 'calibration.table', 'lamp.on_time_s', 'plant.configured', 'sample.held', 'truth.aoi_rad', 'truth.instrument',
                        'vacuum.pressure_pa'],
    'sample.provide': ['sample.bench_ids', 'truth.samples'],
    'sample.place': ['sample.bench_ids', 'sample.held', 'sample.on_stage', 'truth.samples', 'vacuum.pressure_pa'],
    'sample.remove': ['sample.bench_ids', 'sample.on_stage', 'truth.samples', 'vacuum.pressure_pa'],
    'power.set': ['lamp.on', 'lamp.warmed_up', 'measurement.id', 'measurement.kind', 'measurement.phase', 'measurement.progress', 'measurement.remaining_s',
        'power.enabled', 'stage.moving', 'stage.z_target_m', 'system.error_code', 'system.state', 'truth.lamp_intensity_rel'],
    'power.press_button': ['lamp.on', 'lamp.warmed_up', 'measurement.id', 'measurement.kind', 'measurement.phase', 'measurement.progress', 'measurement.remaining_s',
        'power.enabled', 'stage.moving', 'stage.z_target_m', 'system.error_code', 'system.state', 'truth.lamp_intensity_rel'],
    'vacuum.set': ['sample.held', 'vacuum.pressure_pa', 'vacuum.switch_on'],
    'vacuum.press_switch': ['sample.held', 'vacuum.pressure_pa', 'vacuum.switch_on'],
    'aoi.set': ['aoi.moving', 'aoi.position'],
    'stage.move_z': ['stage.moving', 'stage.z_target_m'],
    'liquid_cell.install': ['liquid_cell.installed', 'sample.bench_ids', 'sample.held', 'sample.on_stage', 'truth.samples', 'vacuum.pressure_pa'],
    'liquid_cell.remove': ['liquid_cell.installed', 'sample.bench_ids', 'sample.on_stage', 'truth.samples', 'vacuum.pressure_pa'],
    'liquid_cell.fill': ['liquid_cell.liquid_id', 'liquid_cell.volume_m3'],
    'liquid_cell.drain': ['liquid_cell.liquid_id', 'liquid_cell.volume_m3'],
    'measure': ['measurement.id', 'measurement.kind', 'measurement.phase', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent',
        'stage.moving', 'stage.z_target_m'],
    'calibration.run': ['measurement.id', 'measurement.kind', 'measurement.phase', 'measurement.progress', 'measurement.remaining_s',
        'measurement.started_by_other_agent', 'stage.moving', 'stage.z_target_m'],
    'analysis.fit': ['result.last_mse', 'result.last_record_id', 'result.last_thickness_m'],
    'analysis.uniqueness': [],
    'data.pseudo': [],
    'records.delete': ['result.last_record_id'],
    'system.acknowledge': ['system.error_code'],
    'fault.inject': ['aoi.position', 'lamp.on', 'lamp.on_time_s', 'lamp.warmed_up', 'measurement.id', 'measurement.kind', 'measurement.phase', 'measurement.progress',
        'measurement.remaining_s', 'sample.held', 'stage.moving', 'stage.z_target_m', 'system.error_code', 'truth.faults', 'truth.lamp_intensity_rel',
        'vacuum.pressure_pa'],
}
EFFECTS['sim.step'] = ['clock.time_s']
ON_STEP = {
    'power.set': ['calibration.baseline', 'calibration.table', 'lamp.on_time_s', 'lamp.warmed_up', 'measurement.id', 'measurement.kind', 'measurement.phase',
        'measurement.progress', 'measurement.remaining_s', 'result.last_mse', 'result.last_record_id', 'result.last_thickness_m', 'stage.last_aligned_z_m',
        'stage.moving', 'stage.z_m', 'system.state', 'truth.lamp_intensity_rel'],
    'power.press_button': ['calibration.baseline', 'calibration.table', 'lamp.on_time_s', 'lamp.warmed_up', 'measurement.id', 'measurement.kind', 'measurement.phase',
        'measurement.progress', 'measurement.remaining_s', 'result.last_mse', 'result.last_record_id', 'result.last_thickness_m', 'stage.last_aligned_z_m',
        'stage.moving', 'stage.z_m', 'system.state', 'truth.lamp_intensity_rel'],
    'aoi.set': ['aoi.arm_elevation_rad', 'aoi.moving', 'aoi.position'],
    'stage.move_z': ['stage.moving', 'stage.z_m'],
    'measure': ['calibration.baseline', 'measurement.id', 'measurement.kind', 'measurement.phase', 'measurement.progress', 'measurement.remaining_s',
        'measurement.started_by_other_agent', 'result.last_mse', 'result.last_record_id', 'result.last_thickness_m', 'stage.last_aligned_z_m', 'stage.moving',
        'stage.z_m'],
    'calibration.run': ['calibration.table', 'measurement.id', 'measurement.kind', 'measurement.phase', 'measurement.progress', 'measurement.remaining_s',
        'measurement.started_by_other_agent', 'result.last_record_id', 'stage.last_aligned_z_m', 'stage.moving', 'stage.z_m'],
    'analysis.fit': ['result.last_record_id'],
    'records.delete': ['result.last_record_id'],
    'fault.inject': ['aoi.position', 'calibration.baseline', 'calibration.table', 'lamp.on_time_s', 'lamp.warmed_up', 'measurement.id', 'measurement.kind',
        'measurement.phase', 'measurement.progress', 'measurement.remaining_s', 'result.last_mse', 'result.last_record_id', 'result.last_thickness_m',
        'stage.last_aligned_z_m', 'stage.moving', 'stage.z_m', 'truth.lamp_intensity_rel'],
    'sim.step': ['aoi.arm_elevation_rad', 'aoi.moving', 'aoi.position', 'calibration.baseline', 'calibration.table', 'lamp.on_time_s', 'lamp.warmed_up',
        'measurement.id', 'measurement.kind', 'measurement.phase', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent',
        'result.last_mse', 'result.last_record_id', 'result.last_thickness_m', 'stage.last_aligned_z_m', 'stage.moving', 'stage.z_m', 'system.state',
        'truth.lamp_intensity_rel'],
}
ON_STEP_ZH = {
    'power.set': '通电8 s后初始化完成（就绪）；灯按90 s时间常数预热、累计灯时与电能；断电时Z台停在当时高度', 'power.press_button': '同power.set',
    'aoi.set': '1 s拔出插销、两臂以10°/s转到目标位置、1 s插销卡入（卡入后入射角位置才有效）', 'stage.move_z': 'Z台以2 mm/s移到目标高度',
    'measure': '对准（Z台移到找到的高度）→采集→可选拟合，完成后写入记录与最近结果', 'calibration.run': 'robust对准→长模式采集→拟合，接受时写入校准表',
    'sim.step': '推进灯预热与灯时、两臂与Z台运动、测量阶段与电能/行程账', 'fault.inject': '故障对进行中的操作与运动的影响（清除灯故障＝换新灯：灯时归零、重新预热）',
    'plant.configure': '重新声明后隐藏的仪器误差与校准表改变，之后的测量随之变化',
    'records.delete': '删除本身没有后续演化；之后完成的测量写入新的最新记录号（有无删除都一样；按有/无调用的对比列为步进效应）',
    'analysis.fit': '拟合本身没有后续演化；之后完成的测量写入新的最新记录号（按有/无调用的对比列为步进效应）',
    'vacuum.set': '真空开关决定样品是否被吸住（仪器不回报；吸住与否是评测者可见的过程真值）', 'vacuum.press_switch': '同vacuum.set',
}
_R = ['power.enabled', 'system.state', 'system.error_code']
_STILL = ['measurement.id', 'stage.moving', 'aoi.moving']
PRECONDITIONS = {
    'plant.configure': ('断电且没有操作时才可（重新）声明；新的噪声种子重新抽取隐藏的入射角加工误差与偏振元件误差，并按calibration重置校准表', ['power.enabled', 'measurement.id']),
    'sample.provide': ('已声明实验室条件；样品ID未用过、样品数≤12；尺寸5–200 mm、总厚≤16 mm；材料在模板库中；色散模型在380–900 nm内物理（n>0、无增益）',
                       ['plant.configured', 'sample.bench_ids']),
    'sample.place': ('样品在实验台上、平台空（没有样品或液池）；没有测量；Z台静止、没有换角；测量点落在样品上、样品中心在平台范围内；样品顶面与两臂/端口留2 mm、后缘在Z导轨前2 mm',
                     ['sample.bench_ids', 'sample.on_stage', 'liquid_cell.installed', 'stage.z_m', 'aoi.arm_elevation_rad'] + _STILL),
    'sample.remove': ('样品在平台上；没有测量；静止；样品没有被真空吸住（先关真空开关；是否吸住是评测者可见的过程真值）',
                      ['sample.on_stage', 'vacuum.switch_on', 'sample.held'] + _STILL),
    'power.set': ('已声明实验室条件；上电需电源电压在100–240 VAC ±10 %内；上电时仍存在的故障（灯、Z台）锁存为错误', ['plant.configured', 'power.enabled']),
    'power.press_button': ('同power.set（按一次切换通断）', ['plant.configured', 'power.enabled']),
    'vacuum.set': ('已声明实验室条件（真空开关为机械阀，不需通电）；测量或标定进行中不能关真空', ['plant.configured', 'measurement.id']),
    'vacuum.press_switch': ('同vacuum.set（拨一次切换）', ['plant.configured', 'vacuum.switch_on', 'measurement.id']),
    'aoi.set': ('已声明实验室条件（手动操作，不需通电）；没有测量；Z台静止且没有进行中的换角；装液池时只能70°；换角途中两臂与样品留2 mm',
                ['plant.configured', 'liquid_cell.installed', 'sample.on_stage', 'stage.z_m', 'aoi.position'] + _STILL),
    'stage.move_z': ('已就绪、无错误；没有测量；没有进行中的换角；目标高度与当前高度下样品与两臂留2 mm', _R + ['measurement.id', 'aoi.moving', 'sample.on_stage',
                                                                                   'aoi.arm_elevation_rad']),
    'liquid_cell.install': ('样品在实验台上、不超过3×2英寸且厚≤1.5 mm；平台空；入射角插销卡在70°；没有测量；静止',
                            ['sample.bench_ids', 'sample.on_stage', 'liquid_cell.installed', 'aoi.position'] + _STILL),
    'liquid_cell.remove': ('液池已装且已排空；液池没有被真空吸住（先关真空开关）；没有测量；静止',
                           ['liquid_cell.installed', 'liquid_cell.volume_m3', 'vacuum.switch_on', 'sample.held'] + _STILL),
    'liquid_cell.fill': ('液池已装；液体与池中已有液体相同；总量≤500 µL；没有测量', ['liquid_cell.installed', 'liquid_cell.liquid_id', 'liquid_cell.volume_m3', 'measurement.id']),
    'liquid_cell.drain': ('液池已装；没有测量', ['liquid_cell.installed', 'measurement.id']),
    'measure': ('已就绪、无错误；没有测量；Z台静止、没有换角、插销已卡入；灯亮；反射位（65/70/75°）：平台上有样品或液池、液池液量为0或≥400 µL、'
                '对准目标在Z行程内且不碰臂（真空不是前提：样品平放在平台上；未吸住只记在评测者可见的记录警告sample_not_held中）；90°直通位：只能不对准（none）、不能用MM模式或带模型，样品须低于光束',
                _R + _STILL + ['aoi.position', 'lamp.on', 'sample.on_stage', 'sample.held', 'liquid_cell.installed', 'liquid_cell.volume_m3', 'stage.z_m',
                               'stage.last_aligned_z_m']),
    'calibration.run': ('同反射测量；平台上直接放着标定片（不在液池中）；不在90°位', _R + _STILL + ['aoi.position', 'lamp.on', 'sample.on_stage', 'sample.held']),
    'analysis.fit': ('记录存在、已完成且为SE或MM测量（1–4条，不重复）；参数名属于模型且在范围内；拟合参数可拟合；初值下的模型物理', ['result.last_record_id']),
    'analysis.uniqueness': ('同analysis.fit；扫描参数是模型中的一个厚度参数（m）、扫描值在范围内', ['result.last_record_id']),
    'data.pseudo': ('记录存在、已完成且为SE或MM测量', ['result.last_record_id']),
    'records.delete': ('记录存在且不属于进行中的操作；agent只能删除自己或宿主的记录', ['measurement.id', 'result.last_record_id']),
    'system.acknowledge': ('已声明实验室条件；错误原因已排除（换灯或Z台恢复＝故障已清除）', ['system.error_code']),
    'fault.inject': ('已声明实验室条件', ['plant.configured']),
    'measurement.result': ('记录存在', ['result.last_record_id']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE', 'DEVICE_ERROR']
_RE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING']
_MEAS_ERR = _RE + ['MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'AOI_NOT_SEATED', 'LAMP_OFF', 'RECORDS_FULL', 'COLLISION_RISK']
_MEAS_FAIL = ['POWER_OFF', 'LAMP_FAILURE', 'STAGE_FAULT', 'ALIGNMENT_FAILED', 'AOI_NOT_SEATED', 'CANCELLED', 'DEVICE_ERROR', 'SIMULATION_LIMIT']
_FIT_ERR = ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'UNKNOWN_MODEL', 'UNKNOWN_PARAMETER', 'PARAMETER_OUT_OF_RANGE', 'PARAMETER_NOT_FITTABLE', 'UNPHYSICAL_MATERIAL',
            'FIT_FAILED']
CAP_ERRORS = {
    'plant.configure': ['POWER_ON'],
    'sample.provide': ['PLANT_REQUIRED', 'DUPLICATE_SAMPLE', 'TOO_MANY_SAMPLES', 'SAMPLE_SIZE_OUT_OF_RANGE', 'SAMPLE_TOO_THICK', 'UNKNOWN_MATERIAL',
                       'UNPHYSICAL_MATERIAL'],
    'sample.place': ['PLANT_REQUIRED', 'UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'STAGE_OCCUPIED', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'NOT_ON_PLATFORM',
                     'COLLISION_RISK'],
    'sample.remove': ['PLANT_REQUIRED', 'SAMPLE_NOT_ON_STAGE', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'SAMPLE_HELD'],
    'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_button': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'vacuum.set': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING'], 'vacuum.press_switch': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING'],
    'aoi.set': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'LIQUID_CELL_INSTALLED', 'COLLISION_RISK'],
    'stage.move_z': _RE + ['MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'STAGE_FAULT', 'COLLISION_RISK'],
    'liquid_cell.install': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'STAGE_OCCUPIED', 'AOI_NOT_70',
                            'SAMPLE_DOES_NOT_FIT_CELL', 'COLLISION_RISK'],
    'liquid_cell.remove': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'NO_LIQUID_CELL', 'CELL_NOT_EMPTY', 'SAMPLE_HELD'],
    'liquid_cell.fill': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'NO_LIQUID_CELL', 'DIFFERENT_LIQUID', 'CELL_OVERFLOW'],
    'liquid_cell.drain': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'NO_LIQUID_CELL'],
    'measure': _MEAS_ERR + ['UNKNOWN_MODEL', 'ALIGNMENT_NOT_POSSIBLE_STRAIGHT_THROUGH', 'MODE_NOT_AVAILABLE', 'NO_SAMPLE_IN_BEAM', 'NO_SAMPLE', 'LIQUID_LEVEL_LOW']
               + _MEAS_FAIL + ['NO_SIGNAL', 'BEAM_BLOCKED'],
    'calibration.run': _MEAS_ERR + ['ALIGNMENT_NOT_POSSIBLE_STRAIGHT_THROUGH', 'CALIBRATION_WAFER_REQUIRED'] + _MEAS_FAIL + ['NO_SIGNAL'],
    'analysis.fit': _FIT_ERR + ['RECORDS_FULL'], 'analysis.uniqueness': list(_FIT_ERR), 'data.pseudo': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE'],
    'records.delete': ['UNKNOWN_RECORD', 'RECORD_IN_USE'], 'measurement.result': ['UNKNOWN_RECORD'],
    'system.acknowledge': ['PLANT_REQUIRED', 'CAUSE_PERSISTS'], 'fault.inject': ['PLANT_REQUIRED'],
}
_PARTIAL = {'POWER_OFF', 'LAMP_FAILURE', 'STAGE_FAULT', 'ALIGNMENT_FAILED', 'AOI_NOT_SEATED', 'CANCELLED', 'NO_SIGNAL', 'BEAM_BLOCKED', 'SIMULATION_LIMIT'}
CAP_ERROR_ZH = {('measure', 'POWER_OFF'): '未通电；或对准/采集中断电（记录以failed结束，没有数据；拟合阶段在工作站上照常完成）',
                ('measure', 'STAGE_FAULT'): '对准中Z台卡滞（记录以failed结束，Z台停在当时高度）',
                ('measure', 'LAMP_FAILURE'): '对准/采集中灯损坏（记录以failed结束，锁存LAMP_FAILURE；拟合阶段不受影响）',
                ('measure', 'AOI_NOT_SEATED'): '插销未卡入；或对准/采集中插销脱出（记录以failed结束）',
                ('measure', 'ALIGNMENT_FAILED'): '对准失败：反射光太弱、需要的高度超出standard的±3 mm搜索窗口或超出Z行程（记录以failed结束）',
                ('measure', 'NO_SIGNAL'): '不对准或固定高度时样品表面偏离光束交点太多（反射光有20 %以上落在探测器光阑外），或样品反射的光低于探测下限（新灯直通光强的1e-4）；'
                                          '记录以failed结束',
                ('measure', 'BEAM_BLOCKED'): '直通测量时平台上的样品高出光束下沿（记录以failed结束）',
                ('calibration.run', 'POWER_OFF'): '未通电；或对准/采集中断电（校准表不变）', ('calibration.run', 'LAMP_FAILURE'): '对准/采集中灯损坏（校准表不变）'}
MODEL_NONE_ZH = {
    'plant.configure': '实验室条件没有可见模型', 'sample.provide': '样品在实验台上，场景中没有样品几何', 'sample.place': '样品在场景中没有几何（运行层状态）',
    'sample.remove': '样品在场景中没有几何', 'fault.inject': '注入故障本身没有可见变化', 'system.acknowledge': '只清除错误状态',
    'vacuum.set': '真空开关的拨动由按键绑定表达（vacuum.press_switch），状态本身没有镜像', 'vacuum.press_switch': '开关按键在Matterix中为弹簧关节（按压触发），状态没有镜像',
    'liquid_cell.install': '液池是选件，场景中没有液池几何', 'liquid_cell.remove': '液池没有几何', 'liquid_cell.fill': '液池没有几何', 'liquid_cell.drain': '液池没有几何',
    'analysis.uniqueness': '工作站计算，没有可见变化', 'data.pseudo': '工作站计算，没有可见变化', 'records.delete': '只删除工作站上的记录，仪器与显示没有变化',
}
SCENE_MIRRORED = {'aoi.arm_elevation_rad', 'stage.z_m', 'power.enabled', 'lamp.on', 'result.last_thickness_m', 'result.last_mse', 'measurement.remaining_s'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节（两臂、Z台）、显示器读数或指示灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {
    'measure': ('对准（none 0.5 s；standard 4 s加移动；robust 14 s加全行程扫描；fixed_height 1 s加移动）→采集（快3 s、标准10 s、长30 s；MM 10/30 s）→可选拟合1.5 s，'
                '完成后写入记录（90°直通时同时更新%R基线）',
                '取消或失败（对准/采集中断电、灯损坏、插销脱出、Z台卡滞、对准失败、无信号、光束被挡）时记录以cancelled/failed结束、清空全部数据；Z台停在当时高度；'
                '已完成对准的高度保留；拟合阶段在工作站上进行，断电、灯损坏与真空泄漏都不影响它；测量中真空泄漏只在评测者可见的记录警告里加vacuum_lost（仪器没有真空表）'),
    'calibration.run': ('robust对准→长模式30 s采集→拟合3 s，拟合被接受时写入校准表（该位置实际入射角、起偏器偏置、检偏器方位、补偿器延迟两参数）',
                        '取消或失败时校准表不变、记录没有数据；拟合残差>0.01、入射角偏离名义>1.5°或标定片氧化层不在1–200 nm时记录完成但带calibration_fit_rejected'
                        '警告，校准表不变'),
}
RESOURCES = {'measure': ['lamp', 'polarization_optics', 'spectrometer', 'z_stage', 'host_clock'],
             'calibration.run': ['lamp', 'polarization_optics', 'spectrometer', 'z_stage', 'host_clock'], 'aoi.set': ['arms', 'aoi_plunger'], 'stage.move_z': ['z_stage'],
             'power.set': ['mains'], 'power.press_button': ['mains'], 'vacuum.set': ['vacuum_supply'], 'vacuum.press_switch': ['vacuum_supply'],
             'plant.configure': ['room', 'mains', 'vacuum_supply'], 'sample.provide': ['bench'], 'sample.place': ['bench', 'sample_platform'],
             'sample.remove': ['bench', 'sample_platform'], 'liquid_cell.install': ['liquid_cell', 'sample_platform'], 'liquid_cell.remove': ['liquid_cell', 'sample_platform'],
             'liquid_cell.fill': ['liquid_cell'], 'liquid_cell.drain': ['liquid_cell'], 'analysis.fit': ['workstation'], 'analysis.uniqueness': ['workstation'],
             'data.pseudo': ['workstation'], 'records.delete': ['workstation'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock'], 'system.acknowledge': ['controller']}
OBSERVES = {
    'power.set': ['power.enabled', 'system.state', 'lamp.on'], 'power.press_button': ['power.enabled', 'system.state', 'lamp.on'], 'system.acknowledge': ['system.error_code'],
    'vacuum.set': ['vacuum.switch_on'], 'vacuum.press_switch': ['vacuum.switch_on'],      # the chuck pressure / hold are evaluator truth (no gauge)
    'aoi.set': ['aoi.moving', 'aoi.position', 'aoi.arm_elevation_rad'], 'stage.move_z': ['stage.z_m', 'stage.moving'],
    'sample.place': ['sample.on_stage'], 'sample.remove': ['sample.bench_ids'], 'liquid_cell.install': ['liquid_cell.installed'],
    'liquid_cell.fill': ['liquid_cell.volume_m3', 'liquid_cell.liquid_id'], 'liquid_cell.drain': ['liquid_cell.volume_m3'], 'liquid_cell.remove': ['liquid_cell.installed'],
    'measure': ['measurement.id', 'measurement.phase', 'measurement.progress', 'result.last_record_id', 'result.last_thickness_m', 'result.last_mse'],
    'calibration.run': ['measurement.id', 'measurement.phase', 'calibration.table'], 'analysis.fit': ['result.last_thickness_m', 'result.last_mse'],
    'records.delete': ['result.last_record_id'],
}
_SPEC = 'manufacturer_primary'
_BRO = 'manufacturer_brochure'
_PROD = 'manufacturer_product_page'
_SOP = 'facility_sops'
_MAN = 'hardware_manual_excerpt'
_TUT = 'jaw_tutorial'
_HAN = 'hansen_2020'
_RII = 'refractiveindex_info'
_PHYS = 'physics_literature'
_SIM = 'simulation_assumption'
_WB = 'user_workbook'
CAP_SOURCES = {
    'plant.configure': [_SPEC, _SIM], 'sample.provide': [_SPEC, _WB, _RII, _SIM], 'sample.place': [_SPEC, _SOP, _SIM], 'sample.remove': [_SOP, _SIM],
    'power.set': [_SPEC, _SOP, _SIM], 'power.press_button': [_SPEC, _SOP, _SIM], 'vacuum.set': [_SOP, _SIM], 'vacuum.press_switch': [_SOP, _SIM],
    'aoi.set': [_SPEC, _WB, _SOP, _MAN, _SIM], 'stage.move_z': [_SPEC, _SOP, _SIM], 'liquid_cell.install': [_SPEC, _SIM], 'liquid_cell.remove': [_SIM],
    'liquid_cell.fill': [_SPEC, _RII, _SIM], 'liquid_cell.drain': [_SIM], 'measure': [_SPEC, _WB, _SOP, _BRO, _PHYS, _RII, _SIM],
    'calibration.run': [_SPEC, _SOP, _MAN, _TUT, _PHYS, _SIM], 'analysis.fit': [_TUT, _HAN, _SOP, _PHYS, _RII], 'analysis.uniqueness': [_TUT, _PHYS],
    'data.pseudo': [_TUT, _PHYS], 'models.list': [_TUT, _SOP, _RII], 'measurement.result': [_WB, _SPEC], 'records.list': [_SIM], 'records.delete': [_SIM],
    'events.read': [_SIM], 'system.acknowledge': [_SIM], 'fault.inject': [_SIM], 'ledger.read': [_SIM], 'provenance.read': [_RII], 'sim.step': [_SIM],
    **{k: [_SPEC, _BRO, _PROD] for k in UNSUPPORTED}}
PARAM_ZH = {'mains_voltage_v': '供电电压（厂家100/240 VAC）', 'vacuum_supply_pa': '外接真空源绝对压力（低于60 kPa才吸得住样品，工程阈值）',
            'room_k': '实验室室温（记录用；光学常数取库中室温值）', 'noise_seed': '噪声种子（决定隐藏的入射角加工误差、偏振元件误差与测量噪声）',
            'calibration': '仪器校准状态：factory为出厂校准（残差很小），nominal为名义值（未校准，误差按隐藏真值）', 'lamp_age_s': '灯已点亮的累计时间（老化使强度下降）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.alpha_se_rce.limitations。'
CAP_LIMITS = {
    'sample.provide': ['样品为平面基底上最多8层各向同性薄膜：材料取模板库测量值（refractiveindex.info，84种）或色散模型（Cauchy+Urbach、Sellmeier、Tauc-Lorentz、Lorentz、Drude、'
                       'Bruggeman有效介质；色散模型须在380–900 nm内物理：n>0、Im(ε)≥0）；每层可有相对厚度不均匀度（各层同一相对偏差的高斯分布，1σ≤0.1）；表面粗糙度为顶层材料与'
                       '50 %空隙的Bruggeman层；各向异性、梯度层与图形化样品未建模；样品在场景中没有几何。'],
    'sample.place': ['宿主/机器人抽象：偏移为样品中心相对测量点；两个真空小孔（测量点两侧±2 mm，孔位为假设）被样品盖住才吸得住（约5.4 mm以下的样品吸不住，仍可测量，'
                     '评测者可见的记录警告为sample_not_held；仪器没有真空表，agent只看到真空开关）；镊子夹取与接触未建模。'
                     '按厂家图纸比例与宣传册照片建的几何中测量点在Z导轨前面54.4 mm（导轨顶高于平台），样品后缘须在导轨前2 mm：4英寸晶圆可居中放置，'
                     '直径大于约105 mm的样品须向前偏移（200 mm晶圆中心至少在测量点前47.6 mm，可伸出底座前沿），测别处需转动样品重新放置。'],
    'aoi.set': ['手动换角的时间（1 s拔插销、两臂10°/s、1 s卡入）为工程值；各位置的实际加工角度与名义值差约0.15°（1σ，隐藏真值），由标定测出；'
                '插销未卡入（故障）时位置无效。'],
    'stage.move_z': ['Z行程20 mm（平台顶面距光束交点0–20 mm下方，工程值；厂家图纸给出参考位14.8 mm）、速度2 mm/s为工程值；碰撞保护检查样品包络与两臂端口/外壳/端盖'
                     '（资产碰撞代理的端口、外壳与端盖矩形随臂转动后精确计算，含端口朝光束一侧的端面；样品包络上方与两侧各留2 mm，比斜切的外观外壳保守）及Z导轨；换角进行中不能移动Z台。'],
    'liquid_cell.install': ['选配500 µL液池只用于70°（厂家）；样品放入液池后表面高于平台1.5 mm（工程值）；窗口为垂直于光束的熔石英（每次通过透过率0.93，工程值），不改变偏振；'
                            '液池没有场景几何。'],
    'liquid_cell.fill': ['液量≥400 µL时光路完全浸没（工程阈值），此时环境介质为该液体（模板库：水Hale、乙醇Sani）；0<液量<400 µL时拒绝测量。'],
    'measure': ['旋转补偿器（步进16位）+CCD：逐步模拟探测强度（QTH灯3100 K普朗克谱×CCD量子效率、光子散粒噪声、读出噪声、室内杂散光）再用校准后的仪器参数傅里叶分析，'
                '校准误差（入射角、起偏器/检偏器方位、补偿器延迟）如真机一样传递到Ψ/Δ；样品真值含厚度不均匀、像元带宽与光束发散引起的退偏及透明基底背面反射；'
                '光斑2 mm，入射面内伸长1/cosθ；自动对准的搜索动作不模拟（Z台直接移到找到的高度，残余高度误差3 µm）；MM模式假设起偏器步进到0/45/90°测前三列；'
                '%R相对最近一次90°直通基线（灯漂移会带入误差）；透明抛光基底的背面光束为各自相干的琼斯积（含膜层透射的p-s相位），按与探测器光阑的重叠加权后非相干相加；'
                '退偏平均：厚度不均匀（各层同一相对偏差）、光束发散（入射角节点）与像元带宽（每个节点在其真实波长上，谱两端外推到库范围外最多25 nm）三维都精确计算'
                '（干涉相位与菲涅耳系数一起变）；每20个波长一块、每维逐级加密（相位展宽小时Gauss-Hermite 3、5、7…点，否则梯形法m→2m−1）到N、C、S变化<1e-5；'
                '某层被不均匀度展宽≥16 rad且其余层小16倍时，该层按非相干层处理（在其一个干涉周期内等距取M个相位平均，M→2M），不必解析每个Airy谐波；'
                '计算量上限1e8（节点×波长×层数，每块可用其份额的2倍，约数秒）内不收敛时测量以SIMULATION_LIMIT失败、不给出光谱（多层很厚且很不均匀的膜）；'
                '厚度不均匀度上限0.1（光斑内1σ）；反射光低于新灯直通光强的1e-4时不取数据（NO_SIGNAL）；真空不是测量前提（未吸住只记在评测者可见的记录警告sample_not_held中）。'],
    'calibration.run': ['在当前位置用标定片（Si上约25 nm热氧化层）长模式测量，联合拟合起偏器偏置、检偏器方位、补偿器延迟（比例与斜率两参数）、该位置入射角与氧化层厚度；'
                        '厂家的直通+标定片两步标定合并为一步（工程简化）；残差>0.01、入射角偏离名义>1.5°或拟合的氧化层不在1–200 nm时不采用。'],
    'analysis.fit': ['Levenberg-Marquardt回归N/C/S（J.A. Woollam未加权MSE）；模型默认不含像元带宽与光束发散（真值含）：约1.5 µm以下的膜MSE通常为1–5，更厚的膜应设'
                     'options.bandwidth=1（及options.divergence=1），否则MSE偏高、参数偏差；90 %置信区间与相关矩阵按Jacobian的SVD线性化协方差，数据不能确定的参数没有置信区间'
                     '并带parameter_not_determined警告；拒绝步进到不物理的材料（n≤0或增益）；厚度预拟合在参数范围内0–2 µm（或2倍初值）300点网格上找最好的3个局部极小再'
                     '精修；模型与仿真仪器用同一退偏平均（容差2e-5，每次模型计算的计算量上限1e7，超限时拟合带model_average_capped警告，结果不可信）；开退偏选项的厚膜拟合'
                     '每次模型计算约0.01–0.5 s；拟合唯一性不由仪器参数保证，需用analysis.uniqueness检查。'],
    'analysis.uniqueness': ['每个扫描值上其余拟合参数分别从初值和上一个扫描值的结果出发拟合（每次最多20次迭代），取较好者；局部极小只按相邻点判断；只扫描厚度参数；'
                            '不开退偏选项时最大规模（41个值×4条记录的Tauc-Lorentz模型）约需半分钟，开options.bandwidth/options.divergence或拟合厚膜不均匀度时每次模型计算'
                            '可到约0.5 s（计算量上限1e7），整个扫描可达数十分钟（同步调用，没有进度与取消）。'],
    'data.pseudo': ['把数据当作无膜层的块体基底直接反演（取主值平方根，<n>≥0）；有膜层时结果为赝光学常数，不是材料本身的n/k，<k>可以为负。'],
    'records.delete': ['删除记录及其数据；进行中操作的记录不能删除；agent只能删除自己或宿主创建的记录。'],
    'measurement.result': ['SE/MM/直通/标定记录的template_source.substances列出样品真实材料，只对评测者可见（agent为空列表）。'],
    'plant.configure': ['真空源绝对压力低于60 kPa才吸得住样品（工程阈值）；灯老化：额定2000 h时强度下降30 %（工程值）；重新声明时%R直通基线失效（灯已用时间与校准可能改变），'
                        '需重新测90°直通。'],
    'power.set': ['上电后8 s初始化（工程值）；QTH灯随电源点亮，强度按90 s时间常数从75 %升到稳态（工程值），未稳定时测量带lamp_not_warmed_up警告，漂移使傅里叶分析产生偏差。'],
}
LIMITATIONS = [
    '选定配置：alpha-SE 2015规格（380–900 nm、180个波长、65/70/75°与90°直通、手动换角、电动Z对准、真空吸盘、选配500 µL液池）；聚焦探头、XY平移台、加热台、QCM-D、'
    '机器人放样与CompleteEASE文件导出为不支持的选件或接口。',
    '厂家公布：光谱范围与波长数、入射角位置、采集时间、光斑约2 mm、样品5–200 mm与16 mm厚、外形19×12×12英寸与39磅、电源、液池；隐藏的加工角度误差、偏振元件误差、'
    '灯的谱形/预热/老化、CCD满阱与噪声、Z行程与速度、对准时间为工程假设。',
    '光学：各向同性多层膜精确传输矩阵（Airy/Abelès），Woollam（Nebraska）Δ约定；未建模各向异性/双折射、梯度层、散射、样品弯曲与倾斜、偏振元件消光比与补偿器残余二向色性。',
    '准确度与重复性：模拟在标定后达到厂家指标（直通Ψ=45±0.1°、Δ=0±0.2°；2 nm SiO2/Si标准模式1σ约0.01 nm），这是按指标选取噪声与误差参数的结果，不是独立验证。',
    '没有真实alpha-SE硬件与CompleteEASE软件接口；机器人放样、换角与液池操作没有接触验证。',
]


def _sampling_text(k):
    if k in ('clock.time_s', 'aoi.arm_elevation_rad', 'stage.z_m', 'measurement.progress', 'measurement.remaining_s', 'lamp.on_time_s', 'truth.lamp_intensity_rel',
             'lamp.warmed_up'):
        return 'Instantaneous value at the read time: constant-speed motions, the lamp warm-up and operation phases are integrated exactly and cut at every event.'
    return 'Changes when a command is accepted or at an event of the operation (phase ends, record completion); no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or k in _RECORD_LIST_ARG or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    return '；另一个agent的测量或标定进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）' if k in _CONTROL_GUARDED else ''


def export_ja_alphase_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'JaAlphaseSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.ja_alphase.JaAlphaseSession.invoke',
                                              invocation=f'session.invoke(instance_id="alpha_se_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['measurement.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='JaAlphaseRuntime.invoke / JaAlphaseSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate',
                                     description_zh='设定、目标、状态标志或立即改变的派生量；运动、灯预热与操作进度只随step推进') for s in EFFECTS.get(k, [])],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置、分析或查询；运动由step推进',
                                completion_state_ids=(['measurement.id', 'measurement.progress', 'result.last_record_id'] if k == 'measure'
                                                      else ['measurement.id', 'calibration.table'] if k == 'calibration.run' else []),
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=(EXEC_TEXT[k][1] if k in EXEC_TEXT else
                                                    ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果（运动目标），被拒时状态不变'))),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH.get(e, e)),
                              side_effects='unknown' if e == 'DEVICE_ERROR' else 'partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'spectral_range_m': ([x * 1e-9 for x in SPECTRAL_RANGE_NM], 'm', '光谱范围380–900 nm（厂家、调研表）', [_SPEC, _WB]),
            'wavelength_count': (N_WAVELENGTHS, '1', '180个波长（厂家、调研表）', [_SPEC, _WB]),
            'aoi_positions_rad': ([math.radians(x) for x in AOI_POSITIONS_DEG], 'rad', '入射角65/70/75°，90°直通（厂家、调研表）', [_SPEC, _WB]),
            'acquisition_time_s': (dict(MODE_TIME_S), 's', '全谱采集快3 s、标准10 s、长30 s（厂家、调研表；MM模式时间见用户设施SOP）', [_SPEC, _WB, _SOP]),
            'beam_diameter_m': (BEAM_DIAMETER_M, 'm', '光束直径约2 mm（厂家、调研表；用户设施SOP写约3 mm）', [_SPEC, _WB, _SOP]),
            'sample_size_range_m': (list(SAMPLE_SIZE_RANGE_M), 'm', '样品平面尺寸5–200 mm（厂家、调研表）', [_SPEC, _WB]),
            'substrate_max_thickness_m': (SUBSTRATE_MAX_THICKNESS_M, 'm', '基底最大厚度16 mm（厂家、调研表）', [_SPEC, _WB]),
            'body_dimensions_m': (list(BODY_DIMENSIONS_M), 'm', '外形19×12×12英寸，宽×深×高（厂家、调研表）', [_SPEC, _WB]),
            'mass_kg': (MASS_KG, 'kg', '39磅，不含计算机（厂家、调研表）', [_SPEC, _WB]),
            'mains_voltage_v': (list(MAINS_RANGE_V), 'V', '电源100/240 VAC、47–63 Hz、<1 A（厂家）', [_SPEC]),
            'liquid_cell_volume_m3': (LIQUID_CELL_VOLUME_M3, 'm3', '选配液池500 µL，70°（厂家）', [_SPEC]),
            'accuracy_straight_through_rad': ([math.radians(ACCURACY_PSI_DEG), math.radians(ACCURACY_DELTA_DEG)], 'rad', '直通空气测量准确度Ψ=45°±0.1°、Δ=0°±0.2°（厂家）', [_SPEC]),
            'repeatability_2nm_sio2_m': (REPEATABILITY_2NM_SIO2_M, 'm', '2 nm SiO2/Si 70°标准模式30次测量1σ 0.01 nm（厂家）', [_SPEC])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    for name, (val, unit, meaning) in {
            'boot_s': (BOOT_S, 's', '上电初始化'), 'lamp_warmup_tau_s': (LAMP_WARMUP_TAU_S, 's', '灯预热时间常数'), 'lamp_warmup_deficit': (LAMP_WARMUP_DEFICIT, '1', '刚点亮时的强度亏缺'),
            'lamp_rated_s': (LAMP_RATED_S, 's', '灯额定寿命（2000 h）'), 'lamp_end_of_life_loss': (LAMP_END_OF_LIFE_LOSS, '1', '额定寿命时的强度下降'),
            'lamp_power_w': (LAMP_POWER_W, 'W', '灯功率'), 'electronics_power_w': (ELECTRONICS_POWER_W, 'W', '电子单元功率'), 'lamp_temperature_k': (LAMP_TEMPERATURE_K, 'K', 'QTH灯丝色温'),
            'compensator_steps': (COMPENSATOR_STEPS, '1', '补偿器每周步进位置数'), 'compensator_retardance_600nm_rad': (math.radians(COMPENSATOR_DELTA0_DEG), 'rad', '600 nm处补偿器延迟（名义，按1/λ变化）'),
            'ccd_full_well_e': (CCD_FULL_WELL_E, '1', 'CCD列满阱电子数'), 'ccd_read_noise_e': (CCD_READ_NOISE_E, '1', '读出噪声（电子）'),
            'source_photon_rate_e_s': (SOURCE_PHOTON_RATE, '1/s', '最亮像元的光电子速率（m11=1）'), 'room_light_fraction': (ROOM_LIGHT_FRACTION, '1', '室内杂散光相对灯的比例'),
            'beam_divergence_rad': (BEAM_DIVERGENCE_RAD, 'rad', '光束发散1σ'), 'detector_aperture_sigma_m': (DETECTOR_APERTURE_SIGMA_M, 'm', '探测器光阑对偏移光束的接收宽度1σ'),
            'align_time_s': (dict(ALIGN_TIME_S), 's', '对准固定时间'), 'align_precision_m': (ALIGN_PRECISION_M, 'm', '对准残余高度误差1σ'),
            'align_min_signal': (ALIGN_MIN_SIGNAL, '1', '对准需要的最小相对反射光强'), 'align_max_loss': (ALIGN_MAX_LOSS, '1', '高度偏差允许的最大光强损失'),
            'stage_z_range_m': (list(STAGE_Z_RANGE_M), 'm', 'Z台平台顶面高度范围'), 'stage_z_speed_m_s': (STAGE_Z_SPEED_M_S, 'm/s', 'Z台速度'),
            'arm_speed_rad_s': (ARM_SPEED_RAD_S, 'rad/s', '手动换角两臂速度'), 'vacuum_hold_pa': (VACUUM_HOLD_PA, 'Pa', '吸住样品需要的真空源绝对压力上限'),
            'true_aoi_sigma_rad': (math.radians(TRUE_AOI_SIGMA_DEG), 'rad', '隐藏的各位置加工角度误差1σ'),
            'true_azimuth_sigma_rad': (math.radians(TRUE_AZIMUTH_SIGMA_DEG), 'rad', '隐藏的偏振元件方位误差1σ'),
            'true_retardance_sigma_rad': (math.radians(TRUE_RETARDANCE_SIGMA_DEG), 'rad', '隐藏的补偿器延迟误差1σ'), 'fit_time_s': (FIT_TIME_S, 's', '测量后立即拟合的工作站时间'),
            'liquid_cell_min_immersed_m3': (CELL_MIN_IMMERSED_M3, 'm3', '光路完全浸没需要的液量'), 'cell_window_transmission': (CELL_WINDOW_TRANSMISSION, '1', '液池窗口每次通过透过率')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['source_arm', 'source_housing', 'detector_arm', 'detector_housing', 'source_plunger', 'detector_plunger', 'sample_stage', 'sample_platform', 'z_drive',
                       'vacuum_holes', 'switch_vacuum', 'button_power', 'monitor_screen', 'base_plate'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='alpha-SE旋转补偿器光谱椭偏仪：QTH光源、固定起偏器、样品（多层膜传输矩阵）、步进旋转补偿器、固定检偏器与CCD光谱仪的逐步强度模拟、傅里叶分析与校准、'
                   '手动换角与电动Z对准、真空吸盘、液池、CompleteEASE式模型拟合',
        equations=['Fresnel (N = n + i k): r_s = (q_i - q_t) / (q_i + q_t), r_p = (N_t^2 q_i - N_i^2 q_t) / (N_t^2 q_i + N_i^2 q_t), q = sqrt(N^2 - N_0^2 sin^2 theta)',
                   'Airy recursion: r_(j-1) = (a_(j-1,j) + r_j e^(2 i k0 q_j d_j)) / (1 + a_(j-1,j) r_j e^(2 i k0 q_j d_j))',
                   'NCS: N = cos 2Psi = (|r_s|^2 - |r_p|^2) / (|r_p|^2 + |r_s|^2), C = sin 2Psi cos Delta = 2 Re(r_p r_s*) / (...), S = sin 2Psi sin Delta = -2 Im(r_p r_s*) / (...) (Woollam Delta)',
                   'depolarization = 1 - sqrt(N^2 + C^2 + S^2) from incoherent averages over thickness non-uniformity, pixel bandwidth, beam divergence and backside beams',
                   'RCE intensity: I_k = [1, 0, 0, 0] M_A R(-C_k) M_ret(delta) R(C_k) S_out, S_out = M_sample [1, cos 2P, sin 2P, 0] / 2; least squares over 16 compensator steps',
                   'photon counts: n_k = rate(lambda) I_k t_exp cycles, rate ~ Planck(3100 K) photons x (lambda^2 dE / hc) x QE(lambda); Var = n_k + 2 n_room + 2 cycles sigma_read^2',
                   'calibration: argmin over (P_offset, A, retardance scale, retardance slope, theta, d_ox) of |NCS(counts; instrument) - NCS_model(theta, d_ox)|^2 (SiO2/Si reference)',
                   'MSE = 1000 sqrt(sum_i ((N_E - N_G)^2 + (C_E - C_G)^2 + (S_E - S_G)^2) / (3 n - m)); CI90 = 1.645 sqrt(diag(s^2 (J^T J)^-1))',
                   'pseudo: <eps> = eps_a sin^2 theta [1 + tan^2 theta ((1 - rho) / (1 + rho))^2], rho = tan Psi e^(-i Delta)',
                   'Tauc-Lorentz: eps2 = A E0 C (E - Eg)^2 / ((E^2 - E0^2)^2 + C^2 E^2) / E (E > Eg), eps1 = closed-form Kramers-Kronig of Jellison-Modine (C < 2 E0; numerical '
                   'principal value otherwise); Bruggeman: sum f_i (eps_i - eps) / (eps_i + 2 eps) = 0',
                   'backside beam j: Mueller of the Jones product t_up r_back (r_under r_back)^(j-1) t_down (p and s), weight exp(-(j s)^2 / (2 sigma_ap^2)) A^(2 j), incoherent sum',
                   'depolarization average: <M> = sum over nodes of w M(d_j (1 + sigma_j z), theta + sigma_theta z\', E + sigma_E z_b), every node evaluated exactly; '
                   'Gauss-Hermite 3, 5, 7 ... while the phase spread <= 0.05 rad, else trapezoid on |z| <= 6 (band |z_b| <= 5) refined m -> 2 m - 1, per dimension '
                   'and 20-wavelength block until max |d(N, C, S)| < 1e-5 (model 2e-5); a layer spread >= 16 rad (others 16 x less): uniform phase average '
                   '(1/M) sum_m M(phi_f + 2 pi m / M) at every coarse thickness node; work cap 1e8 (model 1e7); capped truth -> SIMULATION_LIMIT',
                   'height: detector loss factor exp(-(2 dz sin theta)^2 / (2 sigma_ap^2)); backside overlap exp(-(j 2 d tan theta_s cos theta)^2 / (2 sigma_ap^2))'],
        parameters=params, source_ids=[_SIM, _RII, _SPEC, _BRO, _SOP, _TUT, _HAN, _PHYS, _WB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ja_alphase_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'alpha_se_rce': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'alpha_se_rce': mechanism})
