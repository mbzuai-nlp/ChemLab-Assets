"""CHEM-154 Kratos / Shimadzu AXIS Supra+ imaging X-ray photoelectron spectrometer (monochromated Al Ka on a 500 mm Rowland circle, 165 mm
hemispherical + spherical mirror analyser, 128-channel delay-line detector, electron-only charge neutraliser, Minibeam 4 Ar+ ion gun,
Flexi-lock with a 3-holder magazine, 5-axis stage with the Ag / Au / Cu / grid / Faraday-cup reference samples): runtime (SI units; photon,
kinetic and binding energies in eV as the spectroscopic convention of the instrument, listed in the contract as the only non-SI unit).

Physics owned by the host clock (``step`` is the only entry that advances time; instrument cycle 1 s):
  * power: main power (ESCApe workstation, electronics, turbo and rotary pumps, water chiller); boot; power loss closes the gate valve, stops the
    X-ray source, ion gun and neutraliser, and lets the chambers rise;
  * vacuum: analysis chamber (mu-metal, turbo + rotary pump) and Flexi-lock (turbo + rotary pump) as lumped volumes with outgassing, sample gas
    loads, argon from the ion gun, venting with dry nitrogen, transfer bursts; the lid opens only at atmosphere; transfer only below the
    transfer pressure; the X-ray source only below its interlock pressure and with chiller flow;
  * sample handling: samples from the XPS template library (layers on a substrate, adventitious carbon on air-exposed surfaces, lateral
    patterns for imaging) mounted on 75 x 32 mm holders (dual height / combination), magazine of 3 holders, automated pump cycle with an optical
    image of each holder, automated transfer (magazine elevator, gate valve, transfer arm) to the 5-axis stage (X, Y, Z, tilt, rotation), stage
    moves with 2 um accuracy (related AXIS Supra value, Lakehead facility page), auto height (focus), reference samples Ag, Au, Cu, grid and Faraday cup on the stage;
  * X-ray source: monochromated Al Ka (anode HV and emission ramps, 600 W maximum), water cooling interlock, flux proportional to the power;
  * photoemission: every core level of every element and chemical state in the analysed area (aperture: slot 700 x 300 um or 110 / 55 / 27 /
    15 um) and depth (layered samples, IMFP attenuation), Yeh-Lindau cross sections, asymmetry parameters at the 60 deg source-analyser angle,
    TPP-2M inelastic mean free paths, Doniach-Sunjic / Voigt line shapes with natural widths, instrument broadening (X-ray line, pass energy),
    material and charging broadening, spin-orbit doublets, shake-up satellites, X-ray-excited Auger lines, plasmons, Tougaard universal inelastic
    background (depth-dependent), secondary electrons, analyser transmission and focus, delay-line detector dead time, Poisson counts;
  * charging: surface potential of insulators from photoemission, the neutraliser electron current and leakage (positive charging and
    broadening without the neutraliser, a small negative shift with it), differential charging broadening;
  * parallel imaging (spherical mirror analyser): element/chemical-state maps at 400 / 200 / 80 um fields of view with 1 um ultimate resolution;
  * Minibeam 4 Ar+ sputtering (500 eV - 4 keV): Yamamura-Tawara yields, sputter rates from the Faraday-cup current density, layer-by-layer
    removal with depth resolution broadening, ion mixing, preferential oxygen loss of oxides, argon implantation; depth profiles;
  * energy-scale calibration per ISO 15472 on the Au, Ag and Cu reference samples (after sputter cleaning); the instrument has an unknown
    linear scale error until calibrated;
  * data processing (ESCApe-like): peak identification, Shirley-background quantification with sensitivity factors, chemical-state curve
    fitting, charge correction to C 1s 284.8 eV, depth-profile tables;
  * fault injection (evaluator): chiller failure, analysis-chamber leak, Flexi-lock leak, X-ray filament failure, neutraliser filament failure;
  * ledgers: sample material sputtered (atoms), argon (admitted, pumped, implanted), nitrogen vent gas, charge (photo-emitted, neutraliser,
    leakage, stored), X-ray anode electrical energy, photons on the sample.
"""
from __future__ import annotations

import contextlib
import copy
import functools
import json
import math
import random
from pathlib import Path

import numpy as np
from scipy.special import voigt_profile

PROTOCOL_VERSION = '1.0.0'
SLUG = 'kratos_axissupra'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-154'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t3_analytical' / SLUG
LIBRARY_PATH = ASSET / 'xps_library.json'
LIBRARY_REL = str(LIBRARY_PATH.relative_to(ROOT))

N_AVOGADRO = 6.02214076e23
K_BOLTZMANN = 1.380649e-23
E_CHARGE = 1.602176634e-19
P_ATM = 101325.0
CYCLE_S = 1.0
CYCLE_NS = 1_000_000_000
MAX_EVENTS = 500
MAX_RECORDS = 200
MAX_SAMPLES = 40
# ---------------------------------------------------------------- documented (Shimadzu JA specification page = workbook primary source; Kratos brochure MO450A)
HV_EV = 1486.6                                    # Al Ka (library photon energy; Lakehead lists 1486.7 eV for the related AXIS Supra)
XRAY_MAX_W = 600.0                                # モノクロメーターX線源 最大出力 600 W
ROWLAND_DIAMETER_M = 0.500                        # Rowland径 500 mm
HSA_RADIUS_M = 0.165                              # 中心軌道半径 165 mm
DLD_CHANNELS = 128                                # ディレイラインディテクター (128 ch)
APERTURES = {'slot': (700e-6, 300e-6), 'd110': 110e-6, 'd55': 55e-6, 'd27': 27e-6, 'd15': 15e-6}      # 分析径 Slot (700x300 um), 110, 55, 27, 15 um
IMAGING_FOV_M = {'fov400': 400e-6, 'fov200': 200e-6, 'fov80': 80e-6}                               # imaging fields of view ca. 400 / 200 / 80 um
IMAGING_BEST_RESOLUTION_M = 1.0e-6                # ultimate spatial resolution of parallel imaging 1 um
ION_ENERGY_RANGE_EV = (500.0, 4000.0)             # Minibeam 4: 500 - 4,000 V
HOLDER_SIZE_M = (0.075, 0.032)                    # sample holder 75 x 32 mm (2400 mm2 per holder, brochure)
HOLDER_SLOTS = 3                                  # 同時排気ホルダー数 3 枚; up to 3 sample holders (brochure)
DUAL_HEIGHT_MAX_THICKNESS_M = 0.019               # maximum sample thickness 19 mm (dual height holder, brochure)
REFERENCE_SAMPLES = ['Ag', 'Au', 'Cu', 'grid', 'faraday_cup']                                      # 標準試料（試料ステージ上に常備）
MASS_MAIN_UNIT_KG = 1200.0
MAINS_V, MAINS_A = 200.0, 50.0                    # 単相 AC200V、50A
# related model (Lakehead University facility page, AXIS Supra): acceptance performance and stage accuracy
AG_SPEC_CPS, AG_SPEC_FWHM_EV = 760e3, 0.48        # > 760 kcps @ 0.48 eV FWHM (Ag 3d5/2)
PET_SPEC_CPS, PET_SPEC_FWHM_EV = 44e3, 0.68       # > 44 kcps @ 0.68 eV FWHM on insulators (PET C 1s)
STAGE_ACCURACY_M = 2.0e-6                         # manipulator accuracy 2 um (X, Y, Z)
# ---------------------------------------------------------------- engineering values (assumption; see mechanism parameters)
ANODE_HV_V = 15000.0                              # anode voltage (15 kV typical for monochromated Al Ka sources)
EMISSION_MAX_A = XRAY_MAX_W / ANODE_HV_V          # 40 mA at 600 W
HV_RAMP_V_S = 1000.0
EMISSION_RAMP_A_S = 5.0e-3
SPEC_POWER_W = 600.0                              # acceptance specifications assumed at the maximum power
XRAY_LINE_FWHM_EV = 0.18                          # monochromated Al Ka line width at the sample (with the analyser term fitted to Ag 3d5/2 0.48 eV)
XRAY_SPOT_HALF_M = (0.50e-3, 0.25e-3)             # flat-top illumination semi-axes (the ellipse covers the 700 x 300 um slot) with soft edges
XRAY_SPOT_EDGE_M = 40e-6
SOURCE_ANALYSER_ANGLE_RAD = math.radians(60.0)    # angle between the X-ray beam and the analyser axis (interface definition)
PASS_ENERGIES_EV = [5.0, 10.0, 20.0, 40.0, 80.0, 160.0]
ANALYSER_RES_PER_PE = {'slot': 0.0090, 'd110': 0.0085, 'd55': 0.0082, 'd27': 0.0080, 'd15': 0.0080}   # Gaussian FWHM / pass energy
TRANSMISSION_PE_EXP = 1.5                         # count rate ~ PE^1.5 at fixed lens mode
TRANSMISSION_KE_EXP = -0.6                        # ~ KE^-0.6 (retarding lens)
LENS_MODES = {'hybrid': 1.0, 'electrostatic': 0.30}
FOCUS_SIGMA_M = {'slot': 0.30e-3, 'd110': 0.12e-3, 'd55': 0.08e-3, 'd27': 0.06e-3, 'd15': 0.05e-3}
DEAD_TIME_S = 30e-9                               # DLD dead time (non-paralysable)
DARK_CPS_PER_EV = 2.0
SNAPSHOT_WINDOW_PER_PE = 0.18                     # snapshot energy window = 0.18 x PE over the 128 channels
IMAGE_PIXELS = 128
BOOT_S = 120.0
CHILLER_START_S = 30.0
# vacuum
V_SAC_M3, V_FL_M3 = 0.030, 0.0020
S_SAC_M3_S, S_FL_M3_S = 0.30, 0.060               # effective turbo pumping speeds
S_ROUGH_M3_S = 0.0015                             # rotary pump on the Flexi-lock below the turbo crossover
TURBO_CROSSOVER_PA = 20.0
P_SAC_BASE_PA = 3.0e-7                            # ~3e-9 mbar base pressure
Q_SAC_OUTGAS = V_SAC_M3 * 0.0 + S_SAC_M3_S * P_SAC_BASE_PA
P_FL_BASE_PA = 2.0e-5
Q_FL_OUTGAS = S_FL_M3_S * P_FL_BASE_PA
FL_TRANSFER_MAX_PA = 5.0e-5                       # transfer allowed below 5e-7 mbar
XRAY_MAX_PA = 1.0e-5                              # X-ray source interlock
ION_GAS_PA_M3_S = 1.2e-6                          # argon load of the differentially pumped gun in the analysis chamber
VENT_TAU_S = 12.0
LID_OPEN_MIN_PA = 0.95 * P_ATM
LEAK_FAULT_PA_M3_S = 5.0e-5
SAMPLE_OUTGAS_PA_M3_S = {'polymer': 2.0e-6, 'carbonate': 5.0e-7, 'hydroxide': 8.0e-7, 'powder': 5.0e-7}
OUTGAS_TAU_S = 3600.0
# motions and handling
STAGE_SPEED_M_S = 2.0e-3
TILT_SPEED_RAD_S = math.radians(5.0)
ROT_SPEED_RAD_S = math.radians(10.0)
STAGE_TRAVEL = dict(x=(-0.050, 0.050), y=(-0.020, 0.020), z=(-0.020, 0.012), tilt=(0.0, math.radians(80.0)), rotation=(-math.pi, math.pi))
LENS_CLEARANCE_M = 1.0e-3                          # a sample surface more than 1 mm above the analysis plane would hit the lens cone
MAGAZINE_PITCH_M = 0.022
MAGAZINE_SPEED_M_S = 0.010
TRANSFER_STROKE_M = 0.6581                          # Flexi-lock chamber centre -> analysis point along X (interface_definition)
TRANSFER_SPEED_M_S = 0.050
GATE_TRAVEL_M = 0.085
GATE_SPEED_M_S = 0.030
LID_OPEN_RAD = math.radians(100.0)
CAMERA_S = 15.0                                    # optical image of a holder during the pump cycle
AUTO_HEIGHT_S = 60.0
CELL_S = 0.05                                      # absolute time grid on which continuously changing inputs of the charging and count integration are held
# charging and neutraliser
NEUT_FILAMENT_MIN_A = 0.30
NEUT_CURRENT_PER_A = 2.0e-6                        # electron current at the sample per ampere of filament current above threshold
ELECTRON_YIELD_PER_PHOTON = 0.02
SLOW_FRACTION = 0.85
SLOW_KT_EV = 2.0
NEUT_SPREAD_EV = 0.35
CHARGE_TAU_S = 3.0
DIFF_CHARGE_FRACTION = 0.03                        # differential-charging FWHM / |surface potential|
DIFF_CHARGE_FLOOR_EV = 0.05
R_LEAK_OHM = {'conductor': 1.0e2, 'semiconductor': 1.0e7, 'insulator': 5.0e11}
# ion gun and sputtering
ION_CURRENT_4KEV_A = 2.0e-6
ION_CURRENT_EXP = 0.8
ION_RASTER_RANGE_M = (1.0e-3, 5.0e-3)
AR_IMPLANT_FRACTION = 0.02
MIX_LENGTH_1KEV_M = 0.5e-9                         # interface broadening sigma at 1 keV (4 keV: FWHM ~3 nm)
ROUGHNESS_FRACTION = 0.03
OXIDE_REDUCTION_MAX = 0.25
# instrument calibration errors (drawn per instrument)
SCALE_OFFSET_SD_EV = 0.25
SCALE_GAIN_SD = 1.5e-4
ADVENTITIOUS_T_M = (0.6e-9, 1.4e-9)
LOG_TINY = 1e-300


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


# ---------------------------------------------------------------- template library
_LIBRARY_CACHE = None


def _lib():
    global _LIBRARY_CACHE
    if _LIBRARY_CACHE is None:
        lib = json.loads(LIBRARY_PATH.read_text())
        lib['_subs'] = {s['substance_id']: s for s in lib['substances']}
        _LIBRARY_CACHE = lib
    return _LIBRARY_CACHE


def library():
    return _lib()


def substance(sid):
    s = _lib()['_subs'].get(sid)
    if s is None:
        raise ActionRejected('UNKNOWN_TEMPLATE')
    return s


def element(el):
    e = _lib()['elements'].get(el)
    if e is None:
        raise ActionRejected('UNKNOWN_TEMPLATE')
    return e


# ---------------------------------------------------------------- spectroscopy physics
def angular_factor(beta, gamma=SOURCE_ANALYSER_ANGLE_RAD):
    """Dipole angular distribution for unpolarised X-rays: 1 + beta/2 (3/2 sin^2 gamma - 1)."""
    return 1.0 + 0.5 * beta * (1.5 * math.sin(gamma) ** 2 - 1.0)


def tpp2m_nm(ke_ev, rho, molar_mass, nv, eg):
    """TPP-2M inelastic mean free path (nm) (Tanuma, Powell & Penn 1994)."""
    E = max(ke_ev, 50.0)
    U = nv * rho / molar_mass
    Ep = 28.8 * math.sqrt(U)
    beta = -0.10 + 0.944 / math.sqrt(Ep ** 2 + eg ** 2) + 0.069 * rho ** 0.1
    gam = 0.191 / math.sqrt(rho)
    C = 1.97 - 0.91 * U
    D = 53.4 - 20.8 * U
    return E / (Ep ** 2 * (beta * math.log(gam * E) - C / E + D / E ** 2)) / 10.0


def yamamura_yield(E, Z2, M2, Us, Q, W, s, Z1=18, M1=39.948):
    """Yamamura & Tawara (1995) empirical sputtering yield at normal incidence (atoms/ion), NIFS-DATA-23 eqs. (15)-(22)."""
    eps = 0.03255 / (Z1 * Z2 * math.sqrt(Z1 ** (2 / 3) + Z2 ** (2 / 3))) * M2 / (M1 + M2) * E
    sn = 3.441 * math.sqrt(eps) * math.log(eps + 2.718) / (1 + 6.355 * math.sqrt(eps) + eps * (6.882 * math.sqrt(eps) - 1.708))
    Sn = 84.78 * Z1 * Z2 / math.sqrt(Z1 ** (2 / 3) + Z2 ** (2 / 3)) * M1 / (M1 + M2) * sn
    ke = 0.079 * (M1 + M2) ** 1.5 / (M1 ** 1.5 * M2 ** 0.5) * Z1 ** (2 / 3) * Z2 ** 0.5 / (Z1 ** (2 / 3) + Z2 ** (2 / 3)) ** 0.75
    Gam = W / (1 + (M1 / 7) ** 3)
    r = M2 / M1
    alpha = 0.249 * r ** 0.56 + 0.0035 * r ** 1.5 if M1 <= M2 else 0.0875 * r ** -0.15 + 0.165 * r
    g = 4 * M1 * M2 / (M1 + M2) ** 2
    eth = Us * (6.7 / g if M1 >= M2 else (1 + 5.7 * (M1 / M2)) / g)
    if E <= eth:
        return 0.0
    return 0.042 * Q * alpha / Us * Sn / (1 + Gam * ke * eps ** 0.3) * (1 - math.sqrt(eth / E)) ** s


TOUGAARD_B, TOUGAARD_C = 2866.0, 1643.0            # universal inelastic cross section lambda K(T) = B T / (C + T^2)^2 (eV^2)


def tougaard_kernel(dE, n):
    T = np.arange(n) * dE
    return TOUGAARD_B * T / (TOUGAARD_C + T * T) ** 2 * dE


@functools.lru_cache(maxsize=4096)
def _line_kernel(width_l, fwhm_g, alpha, dE, half_n):
    """Area-normalised line shape sampled on a symmetric grid (Doniach-Sunjic tail towards higher binding energy convolved with a Gaussian;
    alpha = 0 gives a Voigt profile)."""
    x = (np.arange(-half_n, half_n + 1) * dE).astype(float)
    sig = max(fwhm_g, 1e-4) / 2.3548200450309493
    gam = max(width_l, 1e-4) / 2.0
    if alpha <= 1e-6:
        y = voigt_profile(x, sig, gam)
    else:
        e = -x                                         # BE axis: the metallic tail extends to higher binding energy (x > 0)
        ds = math.gamma(1 - alpha) * np.cos(math.pi * alpha / 2 + (1 - alpha) * np.arctan2(-x, gam)) / (x * x + gam * gam) ** ((1 - alpha) / 2)
        ds = np.maximum(ds, 0.0)
        g = np.exp(-0.5 * (x / sig) ** 2)
        y = np.convolve(ds, g / g.sum(), mode='same')
        del e
    s = y.sum() * dE
    return tuple((y / s).tolist()) if s > 0 else tuple(np.zeros_like(x).tolist())


def line_profile(grid_be, centre, width_l, fwhm_g, alpha):
    """Normalised line shape (1/eV) on an increasing binding-energy grid (uniform spacing)."""
    dE = float(grid_be[1] - grid_be[0])
    span = 12.0 * (fwhm_g + width_l) + (40.0 * width_l if alpha > 1e-6 else 0.0) + 2.0
    half_n = int(min(4000, max(8, span / dE)))
    k = np.array(_line_kernel(round(width_l, 4), round(fwhm_g, 4), round(alpha, 3), round(dE, 6), half_n))
    pos = (centre - grid_be[0]) / dE
    i0 = int(math.floor(pos))
    frac = pos - i0
    out = np.zeros_like(grid_be)
    for shift, wgt in ((i0, 1.0 - frac), (i0 + 1, frac)):
        lo, hi = shift - half_n, shift + half_n + 1
        a, b_ = max(lo, 0), min(hi, len(grid_be))
        if a < b_:
            out[a:b_] += wgt * k[a - lo:b_ - lo]
    return out


def inelastic_background(grid_be, primary, coeffs):
    """Inelastic loss intensity of a primary spectrum: sum_n c_n (lambda K)^(x n) (x) F with the Tougaard universal cross section and loss-order
    weights c_n = <(z/L)^n / n!> over the emission depth distribution (c_n = 1 for a homogeneous emitter, which is Tougaard's j = F + lambda K (x) j);
    FFT convolutions on a uniform increasing binding-energy grid (losses shift to higher BE)."""
    from scipy.signal import fftconvolve
    dE = float(grid_be[1] - grid_be[0])
    n = len(grid_be)
    K = tougaard_kernel(dE, n)
    out = np.zeros_like(primary)
    term = primary
    for c in coeffs:
        term = fftconvolve(term, K)[:n]
        out += c * term
    return out


def tail_first_order(grid_be, be_peak, area, c1):
    """First-order Tougaard tail of a peak lying below the grid: area x c1 x lambda K(E - BE_peak)."""
    T = np.maximum(np.asarray(grid_be) - be_peak, 0.0)
    return area * c1 * TOUGAARD_B * T / (TOUGAARD_C + T * T) ** 2


# ---------------------------------------------------------------- sample model helpers
def composition_atoms(sub):
    """Atom density (1/m^3) of each element of a library substance."""
    n_fu = sub['density_g_cm3'] * 1e6 / sub['molar_mass_g_mol'] * N_AVOGADRO     # formula units per m^3
    return {el: n_fu * c for el, c in sub['composition'].items()}


def atoms_per_m3(sub):
    return sum(composition_atoms(sub).values())


def imfp_nm(sub, ke):
    eg = sub['band_gap_ev'] if sub['band_gap_ev'] is not None else 0.0
    return tpp2m_nm(ke, sub['density_g_cm3'], sub['molar_mass_g_mol'], sub['valence_electrons'], eg)


def sputter_yield(sub, energy_ev):
    s = sub['sputter']
    return yamamura_yield(energy_ev, s['z2_avg'], s['m2_avg'], s['us_ev'], s['q'], s['w'], s['s'])


def ion_current_a(energy_ev):
    return ION_CURRENT_4KEV_A * (energy_ev / 4000.0) ** ION_CURRENT_EXP


def aperture_points(aperture, n=64):
    """Uniform sample points (m) over an aperture footprint (slot rectangle or disc), deterministic."""
    if aperture == 'slot':
        w, h = APERTURES['slot']
        k = int(math.sqrt(n))
        xs = (np.arange(k) + 0.5) / k - 0.5
        return np.array([(x * w, y * h) for x in xs for y in xs])
    d = APERTURES[aperture]
    pts = []
    k = int(math.sqrt(n) * 1.3)
    for i in range(k):
        for j in range(k):
            x, y = ((i + 0.5) / k - 0.5) * d, ((j + 0.5) / k - 0.5) * d
            if x * x + y * y <= (d / 2) ** 2:
                pts.append((x, y))
    return np.array(pts)


def illumination(x, y):
    """Relative X-ray intensity of the monochromated spot at (x, y) from the analysis point (flat top with soft edges)."""
    ax, ay = XRAY_SPOT_HALF_M
    r = np.sqrt((np.asarray(x) / ax) ** 2 + (np.asarray(y) / ay) ** 2)
    edge = XRAY_SPOT_EDGE_M / min(ax, ay)
    return 1.0 / (1.0 + np.exp((r - 1.0) / (edge / 2.0)))


# ---------------------------------------------------------------- capability registry
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c) if isinstance(c, (list, tuple)) else [c], function_zh=meaning,
                   visibility=visibility, kind=kind, effects=[])


FORMS = ['solid', 'film_on_substrate', 'powder_pellet', 'foil']
PATTERNS = ['uniform', 'grid', 'islands', 'stripes']
HOLDER_TYPES = ['dual_height', 'combination']
FAULTS = ['chiller_failure', 'sac_leak', 'flexilock_leak', 'xray_filament_failure', 'neutraliser_filament_failure']
POSITIONS = ['transfer'] + ['reference:' + r for r in REFERENCE_SAMPLES]
SAMPLE_ID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.-]+$')
HOLDER_ID = enum(['H1', 'H2', 'H3'])
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=200.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 288.15, 303.15), mains_voltage_v=number('V', 0.0, 240.0), noise_seed=INT))
LAYER = obj(dict(substance_id=TEXT, thickness_m=number('m', 1.0e-10, 1.0e-5)))
PATTERN = obj(dict(kind=enum(PATTERNS), feature_substance_id=nullable(TEXT), feature_thickness_m=nullable(number('m', 1.0e-10, 1.0e-5)),
                   pitch_m=nullable(number('m', 2.0e-6, 1.0e-3)), width_m=nullable(number('m', 5.0e-7, 1.0e-3)), coverage=nullable(number('1', 0.01, 0.9))))
SAMPLE = obj(dict(sample_id=SAMPLE_ID, form=enum(FORMS), substrate_substance_id=TEXT, layers=array(LAYER, 0, 5), pattern=PATTERN,
                  lateral_size_m=number('m', 2.0e-3, 0.032), thickness_m=number('m', 1.0e-4, 0.019), air_exposed=BOOL))
SAMPLE_EXAMPLE = dict(sample_id='sio2_on_si', form='film_on_substrate', substrate_substance_id='si', layers=[dict(substance_id='sio2', thickness_m=5.0e-9)],
                      pattern=dict(kind='uniform', feature_substance_id=None, feature_thickness_m=None, pitch_m=None, width_m=None, coverage=None),
                      lateral_size_m=0.010, thickness_m=5.25e-4, air_exposed=True)
REGION = obj(dict(name=dict(type='string', minLength=1, maxLength=24, pattern='^[A-Za-z0-9_ ./+-]+$'), start_be_ev=number('eV', -5.0, 1400.0),
                  end_be_ev=number('eV', -5.0, 1400.0), step_ev=number('eV', 0.01, 2.0), dwell_s=number('s', 0.01, 10.0), sweeps=integer(1, 200)))
REGION_EXAMPLE = dict(name='C 1s', start_be_ev=280.0, end_be_ev=296.0, step_ev=0.1, dwell_s=0.1, sweeps=2)
SURVEY_EXAMPLE = dict(name='survey', start_be_ev=0.0, end_be_ev=1200.0, step_ev=1.0, dwell_s=0.1, sweeps=1)
OPERATION = obj(dict(operation_id=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
SPECTRUM = obj(dict(name=TEXT, binding_energy_ev=array(number('eV'), 1, 20000), counts=array(number('1', 0.0), 1, 20000), counts_per_second=array(number('1/s', 0.0), 1, 20000),
                    step_ev=number('eV'), dwell_s=number('s'), sweeps=INT))
SETTINGS = obj(dict(aperture=TEXT, lens_mode=TEXT, pass_energy_ev=number('eV'), xray_power_w=number('W'), neutraliser_on=BOOL, tilt_rad=number('rad'),
                    stage_x_m=number('m'), stage_y_m=number('m'), stage_z_m=number('m'), rotation_rad=number('rad'), energy_calibration_id=nullable(TEXT)))
RECORD = obj(dict(record_id=TEXT, kind=enum(['spectrum', 'image', 'depth_profile', 'calibration', 'holder_image']), status=STATUS, started_s=number('s'),
                  finished_s=nullable(number('s')), position_label=nullable(TEXT), settings=nullable(SETTINGS), spectra=array(SPECTRUM, 0, 64),
                  image=nullable({'type': 'object'}), levels=nullable(array({'type': 'object'}, 0, 400)), note_zh=TEXT, reason=nullable(TEXT)))
PEAK = obj(dict(binding_energy_ev=number('eV'), height_cps=number('1/s'), assignment=nullable(TEXT), candidates=array(TEXT, 0, 8)))
QUANT = obj(dict(region=TEXT, element=TEXT, line=TEXT, area_cps_ev=number('1/s*eV'), sensitivity=number('1'), atomic_percent=number('1', 0.0, 100.0)))
COMP = obj(dict(label=dict(type='string', minLength=1, maxLength=32), be_ev=number('eV', -5.0, 1400.0), fwhm_ev=number('eV', 0.2, 5.0),
                fix_position=BOOL))
COMP_OUT = obj(dict(label=TEXT, be_ev=number('eV'), fwhm_ev=number('eV'), area_cps_ev=number('1/s*eV'), percent=number('1', 0.0, 100.0)))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'vacuum', 'flexilock', 'transfer', 'stage', 'xray', 'neutraliser', 'ion_gun', 'acquisition', 'calibration',
                                                'sample', 'warning', 'error', 'fault']), code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['photons_on_sample', 'sputtered_atoms', 'sputter_removed_atoms_geometric', 'sputter_residual_atoms', 'argon_admitted_mol',
               'argon_pumped_mol', 'argon_implanted_mol', 'argon_released_mol', 'argon_in_chambers_mol', 'argon_residual_mol', 'nitrogen_admitted_mol',
               'nitrogen_pumped_mol', 'nitrogen_released_mol', 'nitrogen_in_chambers_mol', 'nitrogen_residual_mol', 'charge_photoemitted_c', 'charge_neutraliser_c',
               'charge_leaked_c', 'charge_stored_c', 'charge_residual_c', 'anode_energy_j', 'xray_on_time_s']
LEDGER = obj({k: number('1' if k in ('photons_on_sample', 'sputtered_atoms', 'sputter_removed_atoms_geometric', 'sputter_residual_atoms') else
                        'mol' if k.endswith('_mol') else 'C' if k.endswith('_c') else 'J' if k.endswith('_j') else 's') for k in LEDGER_KEYS})
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['rear_module'], '声明宿主实验室条件（室温、供电电压、噪声种子；种子只在首次声明时生效）',
    'agent', 'configuration')
cap('sample.provide', SAMPLE, obj(dict(sample_id=TEXT, location=TEXT)), SAMPLE_EXAMPLE, ['white_base'],
    '在实验台上准备一个真空相容固体/薄膜样品（XPS模板库物质：衬底、最多5层膜、可选横向图案；暴露空气的表面带不定碳污染层）', 'agent', 'configuration')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT))), dict(fault='chiller_failure', active=True), ['chiller'],
    '评估者注入/清除故障', 'evaluator', 'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL, state=TEXT)), dict(enabled=True), ['dark_cabinets', 'rear_module'],
    '主机上电/断电（ESCApe工作站、电子单元、涡轮泵与前级泵、冷水机）', 'agent', 'device_command')
cap('system.acknowledge', obj({}), obj(dict(cleared=nullable(TEXT))), {}, ['dark_cabinets'], '确认并清除已锁存的错误（原因已排除时）')
cap('flexilock.vent', obj({}), obj(dict(mode=TEXT)), {}, ['flexilock_chamber'], 'Flexi-lock放气到大气压（干燥氮气）；闸板阀须关闭')
cap('flexilock.pump', obj({}), obj(dict(mode=TEXT)), {}, ['flexilock_chamber', 'flexilock_camera', 'fl_turbo_pump'],
    'Flexi-lock自动抽气循环：前级泵粗抽后涡轮泵抽高真空；抽气中给每个样品架拍光学图像（厂家功能）；盖须关闭')
cap('flexilock.set_lid', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['flexilock_lid'], '打开/关闭Flexi-lock盖（只在导入室为大气压时可开）',
    'agent', 'robot_skill')
cap('holder.mount_sample', obj(dict(holder_id=HOLDER_ID, sample_id=SAMPLE_ID, x_m=number('m', -0.0375, 0.0375), y_m=number('m', -0.016, 0.016))),
    obj(dict(holder_id=TEXT, sample_id=TEXT, x_m=number('m'), y_m=number('m'))), dict(holder_id='H1', sample_id='sio2_on_si', x_m=-0.020, y_m=0.0), ['magazine'],
    '把实验台上的样品装到实验台上的样品架（75×32 mm）指定位置（样品不得超出样品架或相互重叠）', 'agent', 'robot_skill')
cap('holder.unmount_sample', obj(dict(holder_id=HOLDER_ID, sample_id=SAMPLE_ID)), obj(dict(sample_id=TEXT, location=TEXT)), dict(holder_id='H1', sample_id='sio2_on_si'),
    ['magazine'], '从实验台上的样品架取下样品放回实验台', 'agent', 'robot_skill')
cap('flexilock.load_holder', obj(dict(holder_id=HOLDER_ID, slot=integer(1, HOLDER_SLOTS))), obj(dict(holder_id=TEXT, slot=INT)), dict(holder_id='H1', slot=1),
    ['magazine', 'flexilock_lid'], '把实验台上的样品架放入样品架仓第n槽（盖须打开）', 'agent', 'robot_skill')
cap('flexilock.unload_holder', obj(dict(slot=integer(1, HOLDER_SLOTS))), obj(dict(holder_id=TEXT, slot=INT)), dict(slot=1), ['magazine', 'flexilock_lid'],
    '从样品架仓第n槽取出样品架放到实验台（盖须打开）', 'agent', 'robot_skill')
cap('transfer.to_stage', obj(dict(slot=integer(1, HOLDER_SLOTS))), OPERATION, dict(slot=1), ['transfer_arm', 'gate_valve_plate', 'magazine', 'stage_azimuth'],
    '自动传送：样品台回传送位、样品架仓升降到第n槽、开闸板阀、传送叉把样品架送到样品台、退回并关阀（Flexi-lock须抽到传送压力以下）')
cap('transfer.to_magazine', obj(dict(slot=integer(1, HOLDER_SLOTS))), OPERATION, dict(slot=1), ['transfer_arm', 'gate_valve_plate', 'magazine', 'stage_azimuth'],
    '自动传送：把样品台上的样品架送回样品架仓第n槽（该槽须空）')
cap('transfer.recover', obj({}), OPERATION, {}, ['transfer_arm', 'gate_valve_plate', 'magazine', 'stage_azimuth'],
    '传送中断恢复：断电或取消使传送叉停在闸板阀通道中（闸板被挡住保持打开，锁存TRANSFER_INTERRUPTED）后，从中断处按原方向完成该传送、关闭闸板并清除错误')
cap('stage.move', obj(dict(x_m=number('m', *STAGE_TRAVEL['x']), y_m=number('m', *STAGE_TRAVEL['y']), z_m=number('m', *STAGE_TRAVEL['z']),
                           tilt_rad=number('rad', *STAGE_TRAVEL['tilt']), rotation_rad=number('rad', *STAGE_TRAVEL['rotation']))),
    obj(dict(target=obj(dict(x_m=number('m'), y_m=number('m'), z_m=number('m'), tilt_rad=number('rad'), rotation_rad=number('rad'))))),
    dict(x_m=0.020, y_m=0.0, z_m=-5.25e-4, tilt_rad=0.0, rotation_rad=0.0), ['stage_x', 'stage_y', 'stage_z', 'stage_tilt', 'stage_azimuth'],
    '五轴样品台移动到目标（X、Y、Z、倾角、转角；电动；到位精度2 µm按相关型号AXIS Supra的Lakehead设施页）；样品表面高于分析面1 mm以上的目标被拒（防撞透镜）')
cap('stage.go_to', obj(dict(position=enum(POSITIONS))), obj(dict(position=TEXT, target=obj(dict(x_m=number('m'), y_m=number('m'), z_m=number('m'),
                                                                                                    tilt_rad=number('rad'), rotation_rad=number('rad'))))),
    dict(position='reference:Au'), ['stage_x', 'stage_y', 'stage_z', 'reference_block'], '样品台移动到命名位置：传送位或台上参考样品（Ag、Au、Cu、栅网、法拉第杯）')
cap('stage.auto_height', obj({}), OPERATION, {}, ['stage_z', 'lens_column'], '自动高度：以计数率对Z扫描，把样品表面放到分析器焦面（需X射线开启）')
cap('xray.set_source', obj(dict(on=BOOL, emission_a=number('A', 0.0, EMISSION_MAX_A))), obj(dict(on=BOOL, power_setpoint_w=number('W'))),
    dict(on=True, emission_a=0.015), ['xray_source', 'mono_crystal'], '单色Al Kα源开/关与发射电流设定（阳极15 kV，最大600 W；需分析室真空与冷却水）')
cap('analyser.configure', obj(dict(lens_mode=enum(list(LENS_MODES)), aperture=enum(list(APERTURES)), pass_energy_ev=dict(type='number', enum=PASS_ENERGIES_EV, **{'x-unit': 'eV'}))),
    obj(dict(lens_mode=TEXT, aperture=TEXT, pass_energy_ev=number('eV'))), dict(lens_mode='hybrid', aperture='slot', pass_energy_ev=20.0),
    ['lens_column', 'hemispherical_analyser', 'magnetic_lens'], '设定透镜模式（混合/静电）、分析区域（slot 700×300 µm或110/55/27/15 µm）与通能（5、10、20、40、80、160 eV）')
cap('neutraliser.set', obj(dict(on=BOOL, filament_current_a=number('A', 0.0, 2.5), charge_balance_v=number('V', 0.0, 10.0), filament_bias_v=number('V', 0.0, 5.0))),
    obj(dict(on=BOOL)), dict(on=True, filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0), ['magnetic_lens'],
    '电子中和器开关与参数（灯丝电流、电荷平衡电压、灯丝偏压）：低能电子沿磁透镜场到达样品补偿正电荷')
cap('ion_gun.set', obj(dict(on=BOOL, energy_ev=number('eV', *ION_ENERGY_RANGE_EV), raster_m=number('m', *ION_RASTER_RANGE_M))),
    obj(dict(on=BOOL, energy_ev=number('eV'), raster_m=number('m'))), dict(on=True, energy_ev=4000.0, raster_m=0.003), ['ion_gun'],
    'Minibeam 4离子枪待机/关闭：离子能量500–4000 eV、扫描光栅边长；压电阀自动通入氩气')
cap('ion_gun.etch', obj(dict(duration_s=number('s', 1.0, 7200.0))), OPERATION, dict(duration_s=60.0), ['ion_gun', 'stage_azimuth'],
    '离子束在分析位光栅扫描溅射指定时间（样品清洁或剥层）')
cap('ion_gun.measure_current', obj({}), obj(dict(current_a=number('A'), current_density_a_m2=number('A/m^2'))), {}, ['reference_block', 'ion_gun'],
    '用台上法拉第杯测离子束电流（样品台须在法拉第杯位置，离子枪待机）')
cap('acquisition.spectrum', obj(dict(regions=array(REGION, 1, 16), mode=enum(['scanned', 'snapshot']))), OPERATION,
    dict(regions=[SURVEY_EXAMPLE, REGION_EXAMPLE], mode='scanned'), ['hemispherical_analyser', 'delay_line_detector', 'xray_source'],
    '采集XPS谱（全谱/窄扫；扫描或快照模式；按当前分析器设定）')
cap('acquisition.image', obj(dict(be_ev=number('eV', 0.0, 1400.0), fov=enum(list(IMAGING_FOV_M)), pass_energy_ev=dict(type='number', enum=[80.0, 160.0], **{'x-unit': 'eV'}), duration_s=number('s', 1.0, 3600.0))),
    OPERATION, dict(be_ev=932.6, fov='fov400', pass_energy_ev=160.0, duration_s=120.0), ['spherical_mirror_analyser', 'delay_line_detector', 'xray_source'],
    '平行成像：在给定结合能采集光电子像（视场400/200/80 µm，最高分辨率1 µm）')
cap('acquisition.result', obj(dict(record_id=TEXT)), RECORD, dict(record_id='SP0001'), ['delay_line_detector'], '读取采集记录（谱、像、深度剖析、标定）', 'agent', 'query')
cap('depth_profile.start', obj(dict(levels=integer(1, 200), etch_s=number('s', 1.0, 3600.0), regions=array(REGION, 1, 8))), OPERATION,
    dict(levels=10, etch_s=30.0, regions=[dict(name='Ta 4f', start_be_ev=20.0, end_be_ev=34.0, step_ev=0.1, dwell_s=0.05, sweeps=1),
                                          dict(name='O 1s', start_be_ev=526.0, end_be_ev=538.0, step_ev=0.1, dwell_s=0.05, sweeps=1)]),
    ['ion_gun', 'hemispherical_analyser', 'delay_line_detector'], '深度剖析：先采集一次，再按“溅射→采集”循环n级（离子枪须待机、X射线开启）')
cap('calibration.energy_scale', obj({}), OPERATION, {}, ['reference_block', 'hemispherical_analyser', 'ion_gun'],
    '能量标尺标定（ISO 15472）：依次溅射清洁台上Au、Ag、Cu参考样品并测Au 4f7/2、Ag 3d5/2、Cu 2p3/2，求线性修正')
cap('data.identify', obj(dict(record_id=TEXT, region=TEXT, shift_ev=number('eV', -50.0, 50.0))), obj(dict(peaks=array(PEAK, 0, 200), elements=array(TEXT, 0, 60))),
    dict(record_id='SP0001', region='survey', shift_ev=0.0), ['hemispherical_analyser'], '自动识峰：在全谱中找峰并按元素谱线库给出元素', 'agent', 'analysis')
cap('data.charge_correct', obj(dict(record_id=TEXT, region=TEXT, reference_be_ev=number('eV', 280.0, 290.0))), obj(dict(shift_ev=number('eV'), measured_be_ev=number('eV'))),
    dict(record_id='SP0001', region='C 1s', reference_be_ev=284.8), ['hemispherical_analyser'], '荷电校正：把C 1s烃类峰对到参考结合能，给出整体位移', 'agent', 'analysis')
cap('data.quantify', obj(dict(record_id=TEXT, regions=array(TEXT, 1, 16), shift_ev=number('eV', -50.0, 50.0))), obj(dict(results=array(QUANT, 0, 16))),
    dict(record_id='SP0001', regions=['C 1s'], shift_ev=0.0), ['hemispherical_analyser'], '定量：Shirley背景积分峰面积÷灵敏度因子，给出原子百分比', 'agent', 'analysis')
cap('data.fit', obj(dict(record_id=TEXT, region=TEXT, components=array(COMP, 1, 8), shift_ev=number('eV', -50.0, 50.0))),
    obj(dict(components=array(COMP_OUT, 0, 8), residual_rms=number('1'))),
    dict(record_id='SP0001', region='C 1s', components=[dict(label='C-C', be_ev=284.8, fwhm_ev=1.0, fix_position=False),
                                                        dict(label='C-O', be_ev=286.4, fwhm_ev=1.0, fix_position=False)], shift_ev=0.0),
    ['hemispherical_analyser'], '化学态拟合：Shirley背景+Voigt分量最小二乘拟合，给出各分量位置、半高宽与比例', 'agent', 'analysis')
cap('data.depth_profile', obj(dict(record_id=TEXT, shift_ev=number('eV', -50.0, 50.0))), obj(dict(etch_time_s=array(number('s'), 0, 401), elements=array(TEXT, 0, 16),
                                                                                                 atomic_percent=array(array(number('1', 0.0, 100.0), 0, 16), 0, 401))),
    dict(record_id='DP0001', shift_ev=0.0), ['hemispherical_analyser'], '深度剖析数据表：各级各元素原子百分比随溅射时间', 'agent', 'analysis')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, MAX_RECORDS), calibrations=array(TEXT, 0, 100))), {}, ['dark_cabinets'], '列出采集记录与标定', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), ['dark_cabinets'],
    '读取事件（agent看不到故障注入与隐藏样品状态）', 'agent', 'query')
cap('ledger.read', obj({}), LEDGER, {}, ['rear_module'], '读取守恒账目（样品原子、氩、氮、电荷、阳极电能、光子）', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, substances=array(TEXT, 0, 200))), {}, ['dark_cabinets'], '读取所用模板来源', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['dark_cabinets'], '宿主推进仿真时间', 'evaluator', 'simulation')
UNSUPPORTED = {
    'source.ag_la': '选配双阳极Al/Ag单色源未装（标准构成只有Al阳极）；Ag Lα激发不可用。',
    'ion_gun.gas_cluster': 'Minibeam 6气体团簇离子源（Arn+，20 keV）与He+离子散射（ISS）为另一配置，未装。',
    'ups.he_lamp': 'He紫外灯（UPS）为选配，未装。',
    'aes.field_emission': '场发射电子源（AES/SAM/SEM）为选配，未装。',
    'holder.heat_cool': '加热/冷却样品架为选配，未建模。',
    'imaging.stitch': '多视场拼接成像（Illinois设施页：拼接到10×10 mm）未实现。',
    'calibration.transmission': '分析器传输函数标定未实现（运行层用固定的传输函数）。',
    'hardware.connect': '没有真实AXIS Supra+或ESCApe的硬件/软件接口。',
    'robot.handle_holder': '机器人装卸样品架、开盖为过程层抽象，没有接触或机器人验证。',
    'accuracy.certification': '没有计量认证；性能指标来自厂家与相关型号设施页。',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, {'source.ag_la': 'xray_source', 'ion_gun.gas_cluster': 'ion_gun', 'ups.he_lamp': 'analysis_chamber', 'aes.field_emission': 'analysis_chamber',
                                   'holder.heat_cool': 'magazine', 'imaging.stitch': 'spherical_mirror_analyser', 'calibration.transmission': 'hemispherical_analyser',
                                   'hardware.connect': 'dark_cabinets', 'robot.handle_holder': 'flexilock_lid', 'accuracy.certification': 'dark_cabinets'}[_k], _why)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


ABS = '绝对压力'
LOG_ABS = '绝对压力的常用对数：无量纲值log10(p/100 Pa)，即以mbar计的绝压取log10'
STAGE_PART = {'x': 'stage_x', 'y': 'stage_y', 'z': 'stage_z', 'tilt': 'stage_tilt', 'rotation': 'stage_azimuth'}   # geometry part of each stage axis
slot('clock.time_s', number('s'), 's', 'derived', 'dark_cabinets', '仿真时钟')
slot('plant.configured', BOOL, '1', 'setpoint', 'rear_module', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'dark_cabinets', '主机电源')
slot('system.state', enum(['off', 'booting', 'ready']), '1', 'derived', 'dark_cabinets', '系统状态（断电/启动中/就绪）')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'dark_cabinets', '锁存的错误代码')
slot('chiller.flow_ok', BOOL, '1', 'sensor', 'chiller', '冷却水流量正常')
slot('vacuum.sac_pressure_indicated_pa', nullable(number('Pa', 0.0)), 'Pa', 'sensor', 'analysis_chamber', '分析室真空计读数', reference=ABS)
slot('vacuum.fl_pressure_indicated_pa', nullable(number('Pa', 0.0)), 'Pa', 'sensor', 'flexilock_chamber', 'Flexi-lock真空计读数', reference=ABS)
slot('vacuum.sac_log10_pressure_mbar', nullable(number('1')), '1', 'derived', 'analysis_chamber', '分析室压力的log10(mbar)（显示器读数）', reference=LOG_ABS)
slot('vacuum.fl_log10_pressure_mbar', nullable(number('1')), '1', 'derived', 'flexilock_chamber', 'Flexi-lock压力的log10(mbar)（显示器读数）', reference=LOG_ABS)
slot('flexilock.mode', enum(['vented', 'venting', 'pumping', 'pumped', 'closed']), '1', 'derived', 'flexilock_chamber', 'Flexi-lock状态（大气/放气中/抽气中/已抽好/隔离）')
slot('flexilock.lid_open', BOOL, '1', 'derived', 'flexilock_lid', 'Flexi-lock盖已打开')
slot('flexilock.magazine', {'type': 'object'}, '1', 'derived', 'magazine', '样品架仓各槽中的样品架（槽号→样品架ID或null）')
slot('holders', {'type': 'object'}, '1', 'derived', 'magazine', '三个样品架的位置（实验台/仓槽/样品台/传送中）与所装样品（样品ID与样品架坐标）')
slot('holder.images', {'type': 'object'}, '1', 'derived', 'flexilock_camera', '抽气循环中拍到的样品架光学图像记录ID（样品架ID→记录ID）')
slot('transfer.busy', nullable(TEXT), '1', 'derived', 'transfer_arm', '传送操作进行中（to_stage/to_magazine）')
slot('stage.holder_id', nullable(TEXT), '1', 'derived', 'stage_azimuth', '样品台上的样品架')
for _ax, _u in (('x', 'm'), ('y', 'm'), ('z', 'm'), ('tilt', 'rad'), ('rotation', 'rad')):
    slot(f'stage.{_ax}_indicated_{_u}', nullable(number(_u)), _u, 'sensor', STAGE_PART[_ax], f'样品台{_ax}轴编码器读数（线性轴精度2 µm按相关型号AXIS Supra设施页；角度分辨0.01°为工程值）')
slot('stage.moving', BOOL, '1', 'derived', 'stage_x', '样品台在运动')
slot('stage.position_label', nullable(TEXT), '1', 'derived', 'stage_x', '最近一次命名定位的名称（移动后清空）')
slot('xray.on', BOOL, '1', 'derived', 'xray_source', 'X射线源开启（xray.set_source开/关；真空联锁、冷却水不足或灯丝故障跳闸时自动关闭）')
slot('xray.emission_setpoint_a', number('A'), 'A', 'setpoint', 'xray_source', '发射电流设定')
slot('xray.power_indicated_w', nullable(number('W', 0.0)), 'W', 'sensor', 'xray_source', 'X射线功率读数（阳极高压×发射电流）')
slot('xray.fault_code', nullable(TEXT), '1', 'derived', 'xray_source', 'X射线源联锁跳闸原因')
slot('analyser.lens_mode', enum(list(LENS_MODES)), '1', 'setpoint', 'lens_column', '透镜模式')
slot('analyser.aperture', enum(list(APERTURES)), '1', 'derived', 'lens_column', '分析区域（光阑；analyser.configure设定，能量标尺标定运行期间临时为slot，结束后恢复）')
slot('analyser.pass_energy_ev', number('eV'), 'eV', 'derived', 'hemispherical_analyser', '通能（analyser.configure设定，能量标尺标定运行期间临时为10 eV，结束后恢复）')
slot('neutraliser.on', BOOL, '1', 'derived', 'magnetic_lens', '电子中和器开启（neutraliser.set开/关；灯丝故障时自动关闭）')
slot('neutraliser.settings', {'type': 'object'}, '1', 'setpoint', 'magnetic_lens', '中和器灯丝电流、电荷平衡电压、灯丝偏压设定')
slot('ion_gun.on', BOOL, '1', 'setpoint', 'ion_gun', '离子枪待机（高压与氩气开）')
slot('ion_gun.energy_ev', number('eV'), 'eV', 'setpoint', 'ion_gun', '离子能量设定')
slot('ion_gun.raster_m', number('m'), 'm', 'setpoint', 'ion_gun', '扫描光栅边长设定')
slot('ion_gun.beam_on', BOOL, '1', 'derived', 'ion_gun', '正在溅射（离子束扫描中）')
slot('detector.count_rate_cps', nullable(number('1/s', 0.0)), '1/s', 'sensor', 'delay_line_detector', '探测器总计数率（1 s周期）')
slot('acquisition.id', nullable(TEXT), '1', 'derived', 'delay_line_detector', '进行中的操作记录ID')
slot('acquisition.kind', nullable(TEXT), '1', 'derived', 'delay_line_detector', '进行中的操作种类')
slot('acquisition.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'delay_line_detector', '进行中的操作进度')
slot('acquisition.remaining_s', nullable(number('s', 0.0)), 's', 'derived', 'delay_line_detector', '进行中的操作剩余时间')
slot('acquisition.started_by_other_agent', BOOL, '1', 'derived', 'delay_line_detector', '进行中的操作属于另一个agent')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'delay_line_detector', '本agent最近完成的记录')
slot('calibration.energy_scale', {'type': 'object'}, '1', 'derived', 'hemispherical_analyser', '当前能量标尺修正（偏移、增益、标定记录与时间）')
slot('sample.bench_ids', array(TEXT), '1', 'derived', 'white_base', '实验台上（未装样品架）的样品')
slot('panel.power_lamp', BOOL, '1', 'derived', 'lamp_power', '电源指示灯')
slot('panel.xray_lamp', BOOL, '1', 'derived', 'lamp_xray', 'X射线指示灯')
slot('panel.ion_gun_lamp', BOOL, '1', 'derived', 'lamp_ion_gun', '离子枪指示灯')
# joint mirrors (continuous process states the Matterix binding reads)
slot('flexilock.lid_angle_rad', number('rad'), 'rad', 'derived', 'flexilock_lid', 'Flexi-lock盖开角（关节镜像）')
slot('flexilock.magazine_z_m', number('m'), 'm', 'derived', 'magazine', '样品架仓升降位置（关节镜像）')
slot('transfer.arm_m', number('m'), 'm', 'derived', 'transfer_arm', '传送叉行程（关节镜像）')
slot('transfer.gate_open_m', number('m'), 'm', 'derived', 'gate_valve_plate', '闸板开度（关节镜像）')
for _ax, _u in (('x', 'm'), ('y', 'm'), ('z', 'm'), ('tilt', 'rad'), ('rotation', 'rad')):
    slot(f'stage.{_ax}_{_u}', number(_u), _u, 'derived', STAGE_PART[_ax], f'样品台{_ax}轴位置（关节镜像）')
# evaluator truth
for _k, _u, _c, _m in [('truth.sac_pressure_pa', 'Pa', 'analysis_chamber', '分析室真实压力'), ('truth.fl_pressure_pa', 'Pa', 'flexilock_chamber', 'Flexi-lock真实压力'),
                       ('truth.surface_potential_v', 'V', 'stage_azimuth', '分析位样品表面真实电位（荷电）'), ('truth.xray_power_w', 'W', 'xray_source', 'X射线真实功率'),
                       ('truth.ion_current_a', 'A', 'ion_gun', '离子束真实电流'), ('truth.focus_offset_m', 'm', 'stage_z', '分析位样品表面相对焦面的真实偏差')]:
    slot(_k, nullable(number(_u)), _u, 'process_truth', _c, _m, 'evaluator', ABS if _u == 'Pa' else None)
slot('truth.scale_error', {'type': 'object'}, '1', 'process_truth', 'hemispherical_analyser', '仪器能量标尺的真实误差（偏移、增益）', 'evaluator')
slot('truth.samples', {'type': 'object'}, '1', 'process_truth', 'white_base', '全部样品的真实结构（层、图案、污染层、已溅射深度、表面改性）', 'evaluator')
slot('truth.faults', array(TEXT), '1', 'process_truth', 'rear_module', '当前注入的故障', 'evaluator')


class _Sum:
    """Neumaier-compensated accumulator (exact ledgers over many small steps)."""

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

    @property
    def value(self):
        return self.s + self.c


LEDGER_FLOWS = ['photons_on_sample', 'sputtered_atoms', 'argon_admitted_mol', 'argon_pumped_mol', 'argon_implanted_mol', 'argon_released_mol',
                'nitrogen_admitted_mol', 'nitrogen_pumped_mol', 'nitrogen_released_mol', 'charge_photoemitted_c', 'charge_neutraliser_c', 'charge_leaked_c',
                'anode_energy_j', 'xray_on_time_s']


# ---------------------------------------------------------------- sample / reference geometry (holder frame, m)
REF_BLOCK_X = -0.047                                   # reference block beyond the holder end (interface definition / geometry)
REF_Y = {'Ag': -0.016, 'Au': -0.008, 'Cu': 0.000, 'grid': 0.008, 'faraday_cup': 0.016}
REF_RADIUS = 0.0028
GRID_PITCH_M, GRID_BAR_M = 63.5e-6, 20e-6              # reference grid: 400-mesh copper grid on silicon (assumption)
HOLDER_SURFACE = [('cr2o3', 2.0e-9), ('fe2o3', 1.0e-9)]  # stainless-steel holder surface over iron (assumption)
REDUCIBLE_OXIDES = {'tio2', 'ta2o5', 'cuo', 'nio', 'fe2o3', 'fe3o4', 'co3o4', 'moo3', 'wo3', 'v2o5', 'nb2o5', 'sno2', 'in2o3', 'ceo2', 'mno2', 'pdo',
                    'ag2o', 'ruo2', 'coo', 'mn2o3'}
METAL_OF = {'tio2': 'ti', 'ta2o5': 'ta', 'cuo': 'cu', 'nio': 'ni', 'fe2o3': 'fe', 'fe3o4': 'fe', 'co3o4': 'co', 'moo3': 'mo', 'wo3': 'w', 'v2o5': 'v',
            'nb2o5': 'nb', 'sno2': 'sn', 'in2o3': 'in', 'ceo2': None, 'mno2': 'mn', 'pdo': 'pd', 'ag2o': 'ag', 'ruo2': 'ru', 'coo': 'co', 'mn2o3': 'mn'}
AR_2P32_EV = 241.9                                      # implanted argon Ar 2p3/2 (literature-typical; assumption)
C_ADV = 'adventitious'



def p_exact(p0, q0, amp, s, v, t):
    """Exact solution of V dp/dt = q0 + amp exp(-t / OUTGAS_TAU_S) - s p at time t (constant pumping speed and gas load, decaying outgassing)."""
    b = 1.0 / OUTGAS_TAU_S
    if s <= 0.0:
        return p0 + q0 * t / v - amp / v * math.expm1(-b * t) / b
    a = s / v
    p = q0 / s + (p0 - q0 / s) * math.exp(-a * t)
    if amp:
        p += amp / v * ((math.expm1(-b * t) - math.expm1(-a * t)) / (a - b) if abs(a - b) > 1e-9 * a else t * math.exp(-a * t))
    return p


def crossing_time(p0, q0, amp, s, v, x, tmax):
    """Time in (0, tmax] at which the pumped-down pressure p_exact(...) falls to x (bracketed Newton)."""
    lo, hi = 0.0, tmax
    t = 0.5 * tmax
    for _ in range(200):
        f = p_exact(p0, q0, amp, s, v, t) - x
        if f > 0.0:
            lo = t
        else:
            hi = t
        fp = (q0 + amp * math.exp(-t / OUTGAS_TAU_S) - s * (f + x)) / v
        tn = t - f / fp if fp < 0.0 else 0.5 * (lo + hi)
        if not lo <= tn <= hi:
            tn = 0.5 * (lo + hi)
        if abs(tn - t) <= 1e-15 * max(1.0, t):
            return min(max(tn, 0.0), tmax)
        t = tn
    return min(max(t, 0.0), tmax)


SAMPLE_CAPACITANCE_F = 10e-12                           # capacitance of a sample surface to ground (assumption)
_GL_X, _GL_W = (list(v) for v in np.polynomial.legendre.leggauss(8))


def _sig(x):
    return 1.0 / (1.0 + math.exp(-x)) if x > -700.0 else math.exp(x)


_EQ_CACHE = {}


def charge_equilibrium(R, iph, inn, e_e):
    """Equilibrium surface potential V* of C dV/dt = F(V) (bracketed Newton from fixed starting points, so every caller gets the same bits)."""
    key = (R, iph, inn, e_e)
    vs = _EQ_CACHE.get(key)
    if vs is not None:
        return vs
    fs, kt, sp = SLOW_FRACTION, SLOW_KT_EV, NEUT_SPREAD_EV

    def F(v):
        esc = 1.0 if v <= 0.0 else math.exp(-v / kt)
        land = _sig((v + e_e) / sp) if inn > 0.0 else 0.0
        return iph * ((1.0 - fs) + fs * esc) - inn * land - v / R

    def dF(v):
        d = -1.0 / R
        if v > 0.0:
            d -= iph * fs * math.exp(-v / kt) / kt
        if inn > 0.0:
            g = _sig((v + e_e) / sp)
            d -= inn * g * (1.0 - g) / sp
        return d
    lo, hi = -1.0, 1.0
    while F(lo) <= 0.0:
        lo = 2.0 * lo - 1.0
    while F(hi) >= 0.0:
        hi = 2.0 * hi + 1.0
    vs = 0.0
    for _ in range(400):
        f = F(vs)
        if f > 0.0:
            lo = vs
        elif f < 0.0:
            hi = vs
        else:
            break
        vn = vs - f / dF(vs)
        if not lo < vn < hi:
            vn = 0.5 * (lo + hi)
        if abs(vn - vs) <= 1e-15 * max(1.0, abs(vs)):
            vs = vn
            break
        vs = vn
    if len(_EQ_CACHE) > 4096:
        _EQ_CACHE.clear()
    _EQ_CACHE[key] = vs
    return vs


def charge_flow(v0, dt, C, R, iph, inn, e_e):
    """Exact flow of the scalar autonomous ODE C dV/dt = F(V) with F(V) = iph ((1 - f_s) + f_s esc(V)) - inn land(V) - V/R (esc: slow photoelectrons
    escape a positive surface with exp(-V / kT); land: neutraliser electrons land with a logistic acceptance around -E_e).  F decreases strictly,
    so V moves monotonically towards the unique equilibrium V*; with w = ln|V - V*| the elapsed time is t = integral of C (V - V*) / (-F(V)) dw,
    a smooth integrand (-> C / |F'(V*)| at V*), integrated with 8-point Gauss-Legendre panels (width 0.25 in w, split at the escape kink V = 0)
    and inverted by Newton.  The charges photoemitted, delivered by the neutraliser and leaked are integrated along the same flow.  Returns
    (V(dt), q_ph, q_n, q_leak)."""
    fs, kt, sp = SLOW_FRACTION, SLOW_KT_EV, NEUT_SPREAD_EV

    def esc(v):
        return 1.0 if v <= 0.0 else math.exp(-v / kt)

    def i_ph(v):
        return iph * ((1.0 - fs) + fs * esc(v))

    def i_n(v):
        return inn * _sig((v + e_e) / sp) if inn > 0.0 else 0.0

    def F(v):
        return i_ph(v) - i_n(v) - v / R

    def dF(v):
        d = -1.0 / R
        if v > 0.0:
            d -= iph * fs * esc(v) / kt
        if inn > 0.0:
            g = _sig((v + e_e) / sp)
            d -= inn * g * (1.0 - g) / sp
        return d

    vs = charge_equilibrium(R, iph, inn, e_e)

    def minus_dF(delta):
        """-(F(V* + delta) - F(V*)) without cancellation (F(V*) = 0)."""
        u = vs + delta
        if u > 0.0 and vs > 0.0:
            de = esc(vs) * math.expm1(-delta / kt)
        elif u <= 0.0 and vs <= 0.0:
            de = 0.0
        else:
            de = esc(u) - esc(vs)
        dl = 0.0
        if inn > 0.0:
            a_, b_ = (u + e_e) / sp, (vs + e_e) / sp
            dl = (0.5 * math.sinh(0.5 * delta / sp) / (math.cosh(0.5 * a_) * math.cosh(0.5 * b_)) if max(abs(a_), abs(b_)) < 600.0
                  else _sig(a_) - _sig(b_))
        return -(iph * fs * de - inn * dl - delta / R)

    sgn = 1.0 if v0 > vs else -1.0
    tol = 1e-12 * max(1.0, abs(vs))

    def tau(w):
        d = sgn * math.exp(w)
        return C * d / minus_dF(d)

    def panel(wa, wb, ledger=True):
        h = 0.5 * (wb - wa)
        m = 0.5 * (wb + wa)
        t = qp = qn = ql = 0.0
        for x, wt in zip(_GL_X, _GL_W):
            w = m + h * x
            d = sgn * math.exp(w)
            tw = wt * h * C * d / minus_dF(d)
            t += tw
            if ledger:
                u = vs + d
                qp += i_ph(u) * tw
                qn += i_n(u) * tw
                ql += u / R * tw
        return t, qp, qn, ql

    q = [0.0, 0.0, 0.0]
    t_acc = 0.0
    if abs(v0 - vs) <= tol:
        w = -math.inf
    else:
        w = math.log(abs(v0 - vs))
    w_cut = math.log(tol)
    kink = math.log(abs(vs)) if vs != 0.0 and v0 != 0.0 and (v0 > 0.0) != (vs > 0.0) else None
    while True:
        if w <= w_cut:                                                   # at the equilibrium for the rest of the interval
            rem = dt - t_acc
            q[0] += i_ph(vs) * rem
            q[1] += i_n(vs) * rem
            q[2] += vs / R * rem
            return vs, q[0], q[1], q[2]
        wa = max(w - 0.25, w_cut)
        if kink is not None and wa < kink < w:
            wa = kink
        t, qp, qn, ql = panel(wa, w)
        if t_acc + t >= dt:
            rem = dt - t_acc
            w1 = w - (w - wa) * rem / t
            for _ in range(60):
                g = panel(w1, w, False)[0] - rem
                w1n = min(w, max(wa, w1 + g / tau(w1)))
                if abs(w1n - w1) <= 1e-14 * max(1.0, abs(w1)):
                    w1 = w1n
                    break
                w1 = w1n
            t, qp, qn, ql = panel(w1, w)
            q[0] += qp
            q[1] += qn
            q[2] += ql
            return vs + sgn * math.exp(w1), q[0], q[1], q[2]
        t_acc += t
        q[0] += qp
        q[1] += qn
        q[2] += ql
        w = wa


class KratosAxissupraRuntime:
    """AXIS Supra+: power, vacuum, Flexi-lock, holders, transfer, 5-axis stage, X-ray source, analyser, neutraliser, ion gun, acquisitions,
    calibration and data processing (SI units, energies in eV)."""

    def __init__(self, instance_id='axis_supra_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.rng = random.Random(0)                      # gauge noise (fixed draws per cycle)
        self.rng_acq = random.Random(1)                  # counting statistics
        self.rng_mount = random.Random(2)                # camera position errors, stage encoder noise
        self.rng_cal = random.Random(3)                  # instrument energy-scale error
        self.rng_sample = random.Random(4)               # contamination thickness of air-exposed samples
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.error = None
        self.power_t0 = None
        # vacuum
        self.p_sac = P_SAC_BASE_PA
        self.p_fl = P_ATM
        self.gas = dict(sac_ar=0.0, sac_n2=0.0, fl_n2=0.0, fl_ar=0.0)   # partial pressures (Pa) of the ledgered gases
        self.fl_mode = 'vented'
        self.lid_open = False
        # holders, magazine, transfer
        self.holders = {h: dict(type='combination' if h == 'H3' else 'dual_height', location='bench', samples={}) for h in ('H1', 'H2', 'H3')}   # H3: combination holder
        self.magazine = {1: None, 2: None, 3: None}
        self.mag_z, self.mag_target = 0.0, 0.0
        self.arm, self.arm_target = 0.0, 0.0
        self.gate, self.gate_target = 0.0, 0.0
        self.images = {}
        # stage
        self.stage = dict(x=0.0, y=0.0, z=0.0, tilt=0.0, rotation=0.0)
        self.stage_target = dict(self.stage)
        self.stage_holder = None
        self.position_label = None
        self.stage_err = dict(x=0.0, y=0.0, z=0.0)
        # X-ray source, analyser, neutraliser, ion gun
        self.xray_on = False
        self.emission_set = 0.015
        self.hv, self.emission = 0.0, 0.0
        self.xray_fault = None
        self.lens_mode, self.aperture, self.pe = 'hybrid', 'slot', 20.0
        self.neut_on = False
        self.neut = dict(filament_current_a=0.4, charge_balance_v=3.3, filament_bias_v=1.0)
        self.ion_on = False
        self.ion_energy, self.ion_raster = 4000.0, 0.003
        self.etching = False
        # samples and charging
        self.samples = {}
        self.v_surface = {}
        # energy scale: hidden instrument error and the user calibration
        self.scale_true = dict(offset_ev=0.0, gain=0.0)
        self.scale_cal = dict(offset_ev=0.0, gain=0.0, calibration_id=None, calibrated_s=None)
        # records and operations
        self.records, self.calibrations = {}, {}
        self.seq = dict(sp=0, im=0, dp=0, ca=0, hi=0, op=0)
        self.acq = None
        self.transfer_op = None
        self.transfer_stranded = None                   # a transfer interrupted with the fork in the valve (resumed by transfer.recover)
        self._cell = None                               # inputs held over the current CELL_S grid cell (see _record_cell)
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.used_substances = set()
        self.faults = set()
        self.led = {k: _Sum() for k in LEDGER_FLOWS}
        self.readings = None
        self._rate_cache = {}
        self._t_now = None
        self._principals, self._requests, self._owners = {}, {}, {}

    # ---------------------------------------------------------------- basics
    @property
    def time_s(self):
        return self._t_now if self._t_now is not None else self.time_ns / 1e9

    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator') or name in self._principals:
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return KratosAxissupraSession(self, name)

    def _event(self, kind, code, message, rid=None, t=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.time_s if t is None else t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _room_k(self):
        return self.plant['room_k'] if self.plant else 295.15

    def _chiller_ok(self):
        return bool(self.power and self.power_t0 is not None and self.time_s - self.power_t0 >= CHILLER_START_S - 1e-9 and 'chiller_failure' not in self.faults)

    def _xray_power(self):
        return self.hv * self.emission if self.xray_on else 0.0

    def _xray_at_setpoint(self):
        return self.xray_on and abs(self.hv - ANODE_HV_V) < 1e-6 and abs(self.emission - self.emission_set) < 1e-9 and self.emission_set > 0

    def _stage_moving(self):
        return any(abs(self.stage[k] - self.stage_target[k]) > 1e-12 for k in self.stage)

    def _transfer_busy(self):
        return self.transfer_op['kind'] if self.transfer_op else None

    def _ion_current(self):
        return ion_current_a(self.ion_energy) if (self.ion_on and self.power) else 0.0

    def _lamps(self):
        return dict(power=self.power, xray=bool(self.xray_on and self.hv > 0), ion=bool(self.etching))

    def _log10_mbar(self, p):
        return None if p is None else round(math.log10(max(p, 1e-12) / 100.0), 1)

    def _fl_mode_now(self):
        if self.fl_mode in ('venting', 'pumping'):
            return self.fl_mode
        return self.fl_mode

    # ---------------------------------------------------------------- analysed spot
    def _analysed_target(self):
        """What is under the analyser: ('sample', sid, (xs, ys) in the sample frame) / ('reference', name, (dx, dy)) / ('holder', hid, None) / ('none', ...)."""
        if self.stage_holder is None:
            return ('none', None, None)
        x, y = -self.stage['x'], -self.stage['y']                       # analysis point in the (unrotated) stage frame
        for name, yy in REF_Y.items():
            if (x - REF_BLOCK_X) ** 2 + (y - yy) ** 2 <= REF_RADIUS ** 2:
                return ('reference', name, (x - REF_BLOCK_X, y - yy))
        c, s = math.cos(-self.stage['rotation']), math.sin(-self.stage['rotation'])
        hx, hy = c * x - s * y, s * x + c * y                           # holder frame (the holder rotates with the stage)
        h = self.holders[self.stage_holder]
        for sid, (sx, sy) in h['samples'].items():
            half = self.samples[sid]['lateral_size_m'] / 2.0
            if abs(hx - sx) <= half and abs(hy - sy) <= half:
                return ('sample', sid, (hx - sx, hy - sy))
        if abs(hx) <= HOLDER_SIZE_M[0] / 2 and abs(hy) <= HOLDER_SIZE_M[1] / 2:
            return ('holder', self.stage_holder, (hx, hy))
        return ('none', None, None)

    def _surface_height(self, target=None):
        kind, key, _ = target or self._analysed_target()
        if kind == 'sample':
            return self.samples[key]['thickness_m']
        return 0.0

    def _focus_offset(self):
        kind, key, _ = self._analysed_target()
        if kind == 'none':
            return None
        return self._surface_height() + self.stage['z']

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        m = self.readings if self.power else None
        acq = self.acq
        lam = self._lamps()
        rem = prog = None
        if acq is not None:
            prog = self._progress()
            rem = max(0.0, acq['duration_s'] - (self.time_s - acq['t0'])) if acq.get('duration_s') is not None else None
        h_view = {h: dict(type=v['type'], location=v['location'], samples={s: dict(x_m=p[0], y_m=p[1]) for s, p in sorted(v['samples'].items())})
                  for h, v in self.holders.items()}
        values = {
            'clock.time_s': self.time_s, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.error_code': self.error, 'chiller.flow_ok': self._chiller_ok(),
            'vacuum.sac_pressure_indicated_pa': m['p_sac'] if m else None, 'vacuum.fl_pressure_indicated_pa': m['p_fl'] if m else None,
            'vacuum.sac_log10_pressure_mbar': self._log10_mbar(m['p_sac']) if m else None, 'vacuum.fl_log10_pressure_mbar': self._log10_mbar(m['p_fl']) if m else None,
            'flexilock.mode': self._fl_mode_now(), 'flexilock.lid_open': self.lid_open,
            'flexilock.magazine': {str(k): v for k, v in self.magazine.items()}, 'holders': h_view, 'holder.images': dict(self.images),
            'transfer.busy': self._transfer_busy(), 'stage.holder_id': self.stage_holder,
            'stage.x_indicated_m': m['x'] if m else None, 'stage.y_indicated_m': m['y'] if m else None, 'stage.z_indicated_m': m['z'] if m else None,
            'stage.tilt_indicated_rad': m['tilt'] if m else None, 'stage.rotation_indicated_rad': m['rotation'] if m else None,
            'stage.moving': self._stage_moving(), 'stage.position_label': self.position_label,
            'xray.on': self.xray_on, 'xray.emission_setpoint_a': self.emission_set, 'xray.power_indicated_w': m['power'] if m else None,
            'xray.fault_code': self.xray_fault, 'analyser.lens_mode': self.lens_mode, 'analyser.aperture': self.aperture, 'analyser.pass_energy_ev': self.pe,
            'neutraliser.on': self.neut_on, 'neutraliser.settings': dict(self.neut), 'ion_gun.on': self.ion_on, 'ion_gun.energy_ev': self.ion_energy,
            'ion_gun.raster_m': self.ion_raster, 'ion_gun.beam_on': self.etching, 'detector.count_rate_cps': m['rate'] if m else None,
            'acquisition.id': acq['record_id'] if acq else None, 'acquisition.kind': acq['kind'] if acq else None, 'acquisition.progress': prog,
            'acquisition.remaining_s': rem, 'acquisition.started_by_other_agent': False, 'result.last_record_id': None,
            'calibration.energy_scale': dict(offset_ev=self.scale_cal['offset_ev'], gain=self.scale_cal['gain'], calibration_id=self.scale_cal['calibration_id'],
                                             calibrated_s=self.scale_cal['calibrated_s']),
            'sample.bench_ids': sorted(k for k, s in self.samples.items() if s['location'] == 'bench'),
            'panel.power_lamp': lam['power'], 'panel.xray_lamp': lam['xray'], 'panel.ion_gun_lamp': lam['ion'],
            'flexilock.lid_angle_rad': LID_OPEN_RAD if self.lid_open else 0.0, 'flexilock.magazine_z_m': self.mag_z, 'transfer.arm_m': self.arm,
            'transfer.gate_open_m': self.gate, 'stage.x_m': self.stage['x'], 'stage.y_m': self.stage['y'], 'stage.z_m': self.stage['z'],
            'stage.tilt_rad': self.stage['tilt'], 'stage.rotation_rad': self.stage['rotation'],
            'truth.sac_pressure_pa': self.p_sac, 'truth.fl_pressure_pa': self.p_fl, 'truth.surface_potential_v': self._v_analysed(),
            'truth.xray_power_w': self._xray_power(), 'truth.ion_current_a': self._ion_current() if self.etching else 0.0, 'truth.focus_offset_m': self._focus_offset(),
            'truth.scale_error': dict(self.scale_true), 'truth.samples': self._truth_samples(), 'truth.faults': sorted(self.faults),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _truth_samples(self):
        out = {}
        for sid, s in self.samples.items():
            out[sid] = dict(form=s['form'], substrate=s['substrate'], layers=copy.deepcopy(s['layers']), pattern=copy.deepcopy(s['pattern']),
                            contamination_m=s['contamination_m'], craters=[{k: (copy.deepcopy(v)) for k, v in c.items()} for c in s['craters']],
                            location=s['location'], surface_potential_v=self.v_surface.get(sid, 0.0))
        return out

    def _v_analysed(self):
        kind, key, _ = self._analysed_target()
        return self.v_surface.get(key, 0.0) if kind == 'sample' else 0.0

    def _progress(self):
        a = self.acq
        if a.get('duration_s'):
            return min(1.0, (self.time_s - a['t0']) / a['duration_s'])
        return None

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
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):
            self._flush()                                  # counts accumulated so far are integrated with the state before the command
        out = fn(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):
            self._rate_cache.clear()
            self._record_cell()                                  # a command changes the held inputs at its own time
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
        if self.acq is not None:
            raise ActionRejected('ACQUISITION_RUNNING')

    def _need_no_transfer(self):
        if self.transfer_op is not None:
            raise ActionRejected('TRANSFER_RUNNING')

    def _need_not_stranded(self):
        if self.transfer_stranded is not None:                 # the fork still sits in the gate valve (whatever the error slot holds now)
            raise ActionRejected('TRANSFER_INTERRUPTED')

    def _relatch(self):
        if self.transfer_stranded is not None and self.error is None:
            self.error = 'TRANSFER_INTERRUPTED'

    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed):
        first = self.plant is None
        if not first:
            noise_seed = self.plant['noise_seed']
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, noise_seed=noise_seed)
        if first:
            for i, r in enumerate((self.rng, self.rng_acq, self.rng_mount, self.rng_cal, self.rng_sample)):
                r.seed(noise_seed * 7919 + i)
            self.scale_true = dict(offset_ev=self.rng_cal.gauss(0.0, SCALE_OFFSET_SD_EV), gain=self.rng_cal.gauss(0.0, SCALE_GAIN_SD))
            for ref in ('Ag', 'Au', 'Cu', 'grid'):                   # reference samples: in UHV since installation, carbon-covered until sputter-cleaned
                self._add_sample('ref:' + ref, 'solid', {'Ag': 'ag', 'Au': 'au', 'Cu': 'cu', 'grid': 'si'}[ref], [],
                                 dict(kind='grid', feature_substance_id='cu', feature_thickness_m=2.0e-6, pitch_m=GRID_PITCH_M, width_m=GRID_BAR_M, coverage=None)
                                 if ref == 'grid' else dict(kind='uniform', feature_substance_id=None, feature_thickness_m=None, pitch_m=None, width_m=None,
                                                            coverage=None), 2 * REF_RADIUS, 0.0, True, location='reference')
        if self.power and abs(mains_voltage_v - MAINS_V) > 0.1 * MAINS_V + 1e-9:
            self._power_off()
            self.error = 'MAINS_FAULT'
            self._event('error', 'MAINS_FAULT', '供电电压超出AC 200 V±10%：系统断电')
        return dict(configured=True)

    def _add_sample(self, sid, form, substrate, layers, pattern, size, thickness, air, location='bench'):
        t_c = self.rng_sample.uniform(*ADVENTITIOUS_T_M) if air else 0.0
        self.samples[sid] = dict(form=form, substrate=substrate, layers=[dict(x) for x in layers], pattern=dict(pattern), lateral_size_m=size, thickness_m=thickness,
                                 air_exposed=air, contamination0_m=t_c, contamination_m=t_c, craters=[], location=location)
        self.v_surface[sid] = 0.0
        self.used_substances.update([substrate] + [x['substance_id'] for x in layers] + ([pattern['feature_substance_id']] if pattern.get('feature_substance_id') else []))


    def _cmd_sample_provide(self, sample_id, form, substrate_substance_id, layers, pattern, lateral_size_m, thickness_m, air_exposed):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('DUPLICATE_SAMPLE')
        if len([k for k in self.samples if not k.startswith('ref:')]) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_SAMPLES')
        for sid in [substrate_substance_id] + [x['substance_id'] for x in layers] + ([pattern['feature_substance_id']] if pattern['feature_substance_id'] else []):
            substance(sid)
        if pattern['kind'] == 'uniform':
            if any(pattern[k] is not None for k in ('feature_substance_id', 'feature_thickness_m', 'pitch_m', 'width_m', 'coverage')):
                raise ActionRejected('INVALID_PATTERN')
        else:
            if pattern['feature_substance_id'] is None or pattern['feature_thickness_m'] is None or pattern['width_m'] is None:
                raise ActionRejected('INVALID_PATTERN')
            if pattern['kind'] in ('grid', 'stripes') and (pattern['pitch_m'] is None or pattern['width_m'] >= pattern['pitch_m'] or pattern['coverage'] is not None):
                raise ActionRejected('INVALID_PATTERN')
            if pattern['kind'] == 'islands' and (pattern['coverage'] is None or pattern['pitch_m'] is not None):
                raise ActionRejected('INVALID_PATTERN')
        if sum(x['thickness_m'] for x in layers) > 0.5 * thickness_m:
            raise ActionRejected('INVALID_ARGUMENT')
        if form == 'film_on_substrate' and not layers:
            raise ActionRejected('INVALID_ARGUMENT')
        self._add_sample(sample_id, form, substrate_substance_id, layers, pattern, lateral_size_m, thickness_m, air_exposed)
        self._event('sample', 'SAMPLE_PROVIDED', f'样品{sample_id}放到实验台')
        return dict(sample_id=sample_id, location='bench')

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        self._event('fault', 'FAULT_' + fault.upper(), f'故障注入：{fault}={active}')
        return dict(faults=sorted(self.faults))

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            if abs(self.plant['mains_voltage_v'] - MAINS_V) > 0.1 * MAINS_V + 1e-9:
                raise ActionRejected('MAINS_OUT_OF_RANGE')
            self.power, self.state, self.state_t0, self.power_t0 = True, 'booting', self.time_s, self.time_s
            if self.error == 'MAINS_FAULT':
                self.error = None
            self._relatch()
            self._event('power', 'POWER_ON', '主机上电：ESCApe与电子单元启动，涡轮泵与前级泵运行，冷水机启动')
        elif not enabled and self.power:
            self._power_off()
            self._event('power', 'POWER_OFF', '主机断电')
        return dict(enabled=self.power, state=self.state)

    def _power_off(self):
        if self.acq is not None:
            self._stop_acq('failed', 'POWER_OFF')
        if self.transfer_op is not None:
            self._stop_transfer('POWER_OFF')
        self.power, self.state, self.power_t0 = False, 'off', None
        self.xray_on, self.hv, self.emission = False, 0.0, 0.0
        self.neut_on = False
        self.ion_on = False
        self.etching = False
        self.gate_target = 0.0 if self.arm <= 1e-9 else self.gate    # the pneumatic gate valve closes without power unless the extended fork blocks it
        self.stage_target = dict(self.stage)
        self.mag_target, self.arm_target = self.mag_z, self.arm
        if self.fl_mode in ('pumping', 'pumped'):
            self.fl_mode = 'closed'
        self.readings = None

    def _cmd_system_acknowledge(self):
        self._need_power()
        e = self.error
        if e is None:
            return dict(cleared=None)
        if e == 'MAINS_FAULT' and abs(self.plant['mains_voltage_v'] - MAINS_V) > 0.1 * MAINS_V:
            raise ActionRejected('CAUSE_PERSISTS')
        if e == 'COOLING_WATER_LOW' and not self._chiller_ok():
            raise ActionRejected('CAUSE_PERSISTS')
        if e == 'TRANSFER_INTERRUPTED' and self.transfer_stranded is not None:
            raise ActionRejected('CAUSE_PERSISTS')                 # the fork is still in the valve: transfer.recover first
        self.error = None
        if self.xray_fault is not None and (self.xray_fault != 'COOLING_WATER_LOW' or self._chiller_ok()):
            self.xray_fault = None
        self._event('power', 'ERROR_CLEARED', f'已确认{e}')
        self._relatch()
        return dict(cleared=e)

    # ---------------------------------------------------------------- Flexi-lock, holders, magazine
    def _cmd_flexilock_vent(self):
        self._need_ready()
        self._need_no_transfer()
        self._need_not_stranded()
        if self.gate > 1e-9:
            raise ActionRejected('GATE_OPEN')
        if self.fl_mode not in ('vented', 'venting'):
            self.fl_mode = 'venting'
            self._event('flexilock', 'FL_VENTING', 'Flexi-lock放气（干燥氮气）')
        return dict(mode=self.fl_mode)

    def _cmd_flexilock_pump(self):
        self._need_ready()
        self._need_not_stranded()
        if self.lid_open:
            raise ActionRejected('LID_OPEN')
        if self.fl_mode in ('pumping', 'pumped'):
            return dict(mode=self.fl_mode)
        self.fl_mode = 'pumping'
        self._pump_cycle = dict(t0=self.time_s, imaged=set(k for k, v in self.magazine.items() if v is None))
        for hid in self.magazine.values():                              # samples start outgassing into vacuum now
            if hid:
                for sid in self.holders[hid]['samples']:
                    self.samples[sid].setdefault('vacuum_since', self.time_s)
        self._event('flexilock', 'FL_PUMPING', 'Flexi-lock自动抽气循环开始（前级泵粗抽，随后涡轮泵）')
        return dict(mode=self.fl_mode)

    def _cmd_flexilock_set_lid(self, open):
        self._need_plant()
        if open and not self.lid_open:
            if self.p_fl < LID_OPEN_MIN_PA:
                raise ActionRejected('NOT_VENTED')
            self.lid_open = True
            self.fl_mode = 'vented'
            for slot_, hid in self.magazine.items():                   # samples in the magazine meet laboratory air
                if hid:
                    self._air_exposure(hid)
        elif not open and self.lid_open:
            self.lid_open = False
        return dict(open=self.lid_open)

    def _air_exposure(self, hid):
        """Samples meeting laboratory air: the pristine surface regains its adventitious carbon, sputter craters get a new contamination overlayer."""
        for sid in self.holders[hid]['samples']:
            s = self.samples[sid]
            if not s['air_exposed']:
                s['air_exposed'] = True
                s['contamination0_m'] = self.rng_sample.uniform(*ADVENTITIOUS_T_M)
            s['contamination_m'] = max(s['contamination_m'], s['contamination0_m'])
            s.pop('vacuum_since', None)
            for cr in s['craters']:
                cr['recontam_m'] = max(cr['recontam_m'], s['contamination0_m'])
            self.v_surface[sid] = 0.0

    def _cmd_holder_mount_sample(self, holder_id, sample_id, x_m, y_m):
        self._need_plant()
        h = self.holders[holder_id]
        if h['location'] != 'bench':
            raise ActionRejected('HOLDER_NOT_ON_BENCH')
        s = self.samples.get(sample_id)
        if s is None or sample_id.startswith('ref:'):
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        half = s['lateral_size_m'] / 2
        if abs(x_m) + half > HOLDER_SIZE_M[0] / 2 + 1e-12 or abs(y_m) + half > HOLDER_SIZE_M[1] / 2 + 1e-12:
            raise ActionRejected('OUTSIDE_HOLDER')
        if s['thickness_m'] > DUAL_HEIGHT_MAX_THICKNESS_M + 1e-12 or (h['type'] == 'combination' and s['thickness_m'] > 0.007):
            raise ActionRejected('SAMPLE_TOO_THICK')
        for other, (ox, oy) in h['samples'].items():
            oh = self.samples[other]['lateral_size_m'] / 2
            if abs(ox - x_m) < half + oh - 1e-12 and abs(oy - y_m) < half + oh - 1e-12:
                raise ActionRejected('POSITION_OCCUPIED')
        h['samples'][sample_id] = (x_m, y_m)
        s['location'] = f'holder:{holder_id}'
        return dict(holder_id=holder_id, sample_id=sample_id, x_m=x_m, y_m=y_m)

    def _cmd_holder_unmount_sample(self, holder_id, sample_id):
        self._need_plant()
        h = self.holders[holder_id]
        if h['location'] != 'bench':
            raise ActionRejected('HOLDER_NOT_ON_BENCH')
        if sample_id not in h['samples']:
            raise ActionRejected('UNKNOWN_SAMPLE')
        del h['samples'][sample_id]
        self.samples[sample_id]['location'] = 'bench'
        return dict(sample_id=sample_id, location='bench')

    def _cmd_flexilock_load_holder(self, holder_id, slot):
        self._need_plant()
        if not self.lid_open:
            raise ActionRejected('LID_CLOSED')
        if self.holders[holder_id]['location'] != 'bench':
            raise ActionRejected('HOLDER_NOT_ON_BENCH')
        if self.magazine[slot] is not None:
            raise ActionRejected('SLOT_OCCUPIED')
        if self.transfer_op is not None and self.transfer_op['slot'] == slot:
            raise ActionRejected('TRANSFER_RUNNING')
        self.magazine[slot] = holder_id
        self.holders[holder_id]['location'] = f'slot:{slot}'
        self.images.pop(holder_id, None)
        return dict(holder_id=holder_id, slot=slot)

    def _cmd_flexilock_unload_holder(self, slot):
        self._need_plant()
        if not self.lid_open:
            raise ActionRejected('LID_CLOSED')
        hid = self.magazine[slot]
        if hid is None:
            raise ActionRejected('SLOT_EMPTY')
        self.magazine[slot] = None
        self.holders[hid]['location'] = 'bench'
        self._air_exposure(hid)
        return dict(holder_id=hid, slot=slot)

    # ---------------------------------------------------------------- transfer
    def _new_op(self, kind, rid=None, **data):
        self.seq['op'] += 1
        oid = f'OP{self.seq["op"]:05d}'
        self.operations[oid] = dict(operation_id=oid, kind=kind, phase='running', record_id=rid, started_s=self.time_s, reason=None, **data)
        return oid

    def _transfer_pose(self, hid):
        tmax = max([self.samples[s]['thickness_m'] for s in self.holders[hid]['samples']] or [0.0])
        z = max(STAGE_TRAVEL['z'][0], -(tmax + 0.5e-3)) if tmax > LENS_CLEARANCE_M else 0.0
        return dict(x=0.0, y=0.0, z=z, tilt=0.0, rotation=0.0)

    def _cmd_transfer_to_stage(self, slot):
        self._need_ready()
        self._need_idle()
        self._need_no_transfer()
        self._need_not_stranded()
        if self.etching:
            raise ActionRejected('ION_BEAM_ON')
        hid = self.magazine[slot]
        if hid is None:
            raise ActionRejected('SLOT_EMPTY')
        if self.stage_holder is not None:
            raise ActionRejected('STAGE_OCCUPIED')
        if self.lid_open or self.fl_mode != 'pumped' or self.p_fl > FL_TRANSFER_MAX_PA:
            raise ActionRejected('FLEXILOCK_NOT_PUMPED')
        oid = self._new_op('transfer.to_stage', slot=slot, holder_id=hid)
        self.transfer_op = dict(kind='to_stage', slot=slot, holder_id=hid, op=oid, phase='stage', pose=self._transfer_pose(hid))
        self.stage_target = dict(self.transfer_op['pose'])
        self.position_label = 'transfer'
        self._event('transfer', 'TRANSFER_START', f'传送：仓槽{slot}的样品架{hid}→样品台')
        return dict(operation_id=oid)

    def _cmd_transfer_to_magazine(self, slot):
        self._need_ready()
        self._need_idle()
        self._need_no_transfer()
        self._need_not_stranded()
        if self.etching:
            raise ActionRejected('ION_BEAM_ON')
        if self.stage_holder is None:
            raise ActionRejected('STAGE_EMPTY')
        if self.magazine[slot] is not None:
            raise ActionRejected('SLOT_OCCUPIED')
        if self.lid_open or self.fl_mode != 'pumped' or self.p_fl > FL_TRANSFER_MAX_PA:
            raise ActionRejected('FLEXILOCK_NOT_PUMPED')
        hid = self.stage_holder
        oid = self._new_op('transfer.to_magazine', slot=slot, holder_id=hid)
        self.transfer_op = dict(kind='to_magazine', slot=slot, holder_id=hid, op=oid, phase='stage', pose=self._transfer_pose(hid))
        self.stage_target = dict(self.transfer_op['pose'])
        self.position_label = 'transfer'
        self._event('transfer', 'TRANSFER_START', f'传送：样品台上的样品架{hid}→仓槽{slot}')
        return dict(operation_id=oid)

    def _transfer_next(self):
        """Advance the transfer sequence when the current motion has arrived."""
        o = self.transfer_op
        ph = o['phase']
        if ph == 'stage' and not self._stage_moving():
            o['phase'] = 'magazine'
            self.mag_target = (o['slot'] - 1) * MAGAZINE_PITCH_M
        elif ph == 'magazine' and abs(self.mag_z - self.mag_target) <= 1e-12:
            o['phase'] = 'gate_open'
            self.gate_target = GATE_TRAVEL_M
            self._gate_burst()
        elif ph == 'gate_open' and abs(self.gate - self.gate_target) <= 1e-12:
            o['phase'] = 'arm_out'
            self.arm_target = TRANSFER_STROKE_M
            if o['kind'] == 'to_stage':
                self.magazine[o['slot']] = None
                self.holders[o['holder_id']]['location'] = 'arm'
        elif ph == 'arm_out' and abs(self.arm - self.arm_target) <= 1e-12:
            if o['kind'] == 'to_stage':
                self.stage_holder = o['holder_id']
                self.holders[o['holder_id']]['location'] = 'stage'
            else:
                self.stage_holder = None
                self.holders[o['holder_id']]['location'] = 'arm'
            o['phase'] = 'arm_in'
            self.arm_target = 0.0
        elif ph == 'arm_in' and abs(self.arm) <= 1e-12:
            if o['kind'] == 'to_magazine':
                self.magazine[o['slot']] = o['holder_id']
                self.holders[o['holder_id']]['location'] = f'slot:{o["slot"]}'
            o['phase'] = 'gate_close'
            self.gate_target = 0.0
        elif ph == 'gate_close' and self.gate <= 1e-12:
            op = self.operations[o['op']]
            op['phase'] = 'completed'
            op['finished_s'] = self.time_s
            self.transfer_op = None
            self._rate_cache.clear()
            self._event('transfer', 'TRANSFER_DONE', f'传送完成：样品架{o["holder_id"]}')
            if o.get('recovery') and self.error == 'TRANSFER_INTERRUPTED':
                self.error = None
                self._event('transfer', 'TRANSFER_RECOVERED', '被中断的传送已完成，闸板关闭，TRANSFER_INTERRUPTED清除')

    def _gate_burst(self):
        """Opening the gate valve shares the Flexi-lock gas with the analysis chamber (molecular flow is fast compared with the valve stroke)."""
        n_fl = self.p_fl * V_FL_M3
        n_sac = self.p_sac * V_SAC_M3
        p_eq = (n_fl + n_sac) / (V_FL_M3 + V_SAC_M3)
        n2 = self.gas['fl_n2'] * V_FL_M3 + self.gas['sac_n2'] * V_SAC_M3          # tracked species of both chambers (Pa m^3)
        ar = self.gas.get('fl_ar', 0.0) * V_FL_M3 + self.gas['sac_ar'] * V_SAC_M3
        self.p_sac = self.p_fl = p_eq
        tot = (V_FL_M3 + V_SAC_M3)
        self.gas['sac_n2'] = self.gas['fl_n2'] = n2 / tot
        self.gas['sac_ar'] = self.gas['fl_ar'] = ar / tot
        if self.xray_on and self.p_sac > XRAY_MAX_PA:
            self._xray_trip('VACUUM_INTERLOCK', '分析室压力超过X射线源联锁值：X射线关闭')

    def _cmd_transfer_recover(self):
        self._need_ready(allow_error=True)
        self._need_idle()
        self._need_no_transfer()
        if self.transfer_stranded is None:
            raise ActionRejected('NO_INTERRUPTED_TRANSFER')
        o = dict(self.transfer_stranded, recovery=True)
        o['op'] = self._new_op('transfer.recover', slot=o['slot'], holder_id=o['holder_id'], resumed_kind=o['kind'])
        self.transfer_stranded = None
        self.transfer_op = o
        self.stage_target = dict(self.stage)
        self.mag_target = self.mag_z
        self.gate_target = self.gate
        self.arm_target = TRANSFER_STROKE_M if o['phase'] == 'arm_out' else 0.0
        self._event('transfer', 'TRANSFER_RECOVER', f'从中断处继续传送（{o["kind"]}，样品架{o["holder_id"]}，阶段{o["phase"]}）')
        return dict(operation_id=o['op'])

    def _stop_transfer(self, code):
        o = self.transfer_op
        op = self.operations[o['op']]
        op['phase'] = 'failed' if code != 'USER_CANCEL' else 'cancelled'
        op['reason'] = code
        op['finished_s'] = self.time_s
        self.transfer_op = None
        self.stage_target = dict(self.stage)
        self.mag_target, self.arm_target = self.mag_z, self.arm
        if self.arm > 1e-9:                          # the fork stays in the valve: the gate cannot close, the holder may sit on the fork
            self.transfer_stranded = {k: v for k, v in o.items() if k != 'op'}
            self.gate_target = self.gate
            self.error = 'TRANSFER_INTERRUPTED'
        else:
            self.gate_target = 0.0
        self._event('error' if code != 'USER_CANCEL' else 'transfer', 'TRANSFER_' + ('CANCELLED' if code == 'USER_CANCEL' else 'FAILED'),
                    f'传送中止（{code}）', rid=o['op'])

    # ---------------------------------------------------------------- stage
    def _check_stage_target(self, t, hid=None):
        for k, (lo, hi) in STAGE_TRAVEL.items():
            if not lo - 1e-12 <= t[k] <= hi + 1e-12:
                raise ActionRejected('OUT_OF_RANGE')
        # lens clearance at the target analysis point
        saved = dict(self.stage)
        self.stage = dict(t)
        try:
            dz = self._surface_height() + t['z'] if self.stage_holder else None
        finally:
            self.stage = saved
        if dz is not None and dz > LENS_CLEARANCE_M + 1e-12:
            raise ActionRejected('COLLISION_RISK')

    def _need_stage_free(self):
        self._need_ready()
        self._need_idle()
        self._need_no_transfer()
        self._need_not_stranded()

    def _cmd_stage_move(self, x_m, y_m, z_m, tilt_rad, rotation_rad):
        self._need_stage_free()
        t = dict(x=x_m, y=y_m, z=z_m, tilt=tilt_rad, rotation=rotation_rad)
        self._check_stage_target(t)
        self.stage_target = t
        self.position_label = None
        self._stage_error_draw()
        return dict(target=dict(x_m=x_m, y_m=y_m, z_m=z_m, tilt_rad=tilt_rad, rotation_rad=rotation_rad))

    def _stage_error_draw(self):
        self.stage_err = {k: self.rng_mount.gauss(0.0, STAGE_ACCURACY_M / 2.0) for k in ('x', 'y', 'z')}

    def _cmd_stage_go_to(self, position):
        self._need_stage_free()
        if position == 'transfer':
            t = dict(x=0.0, y=0.0, z=0.0, tilt=0.0, rotation=0.0)
        else:
            name = position.split(':', 1)[1]
            t = dict(x=-REF_BLOCK_X, y=-REF_Y[name], z=0.0, tilt=0.0, rotation=0.0)
        self._check_stage_target(t)
        self.stage_target = t
        self.position_label = position
        self._stage_error_draw()
        return dict(position=position, target=dict(x_m=t['x'], y_m=t['y'], z_m=t['z'], tilt_rad=t['tilt'], rotation_rad=t['rotation']))

    def _cmd_stage_auto_height(self):
        self._need_stage_free()
        self._need_beam()
        if self.stage_holder is None:
            raise ActionRejected('STAGE_EMPTY')
        kind, key, _ = self._analysed_target()
        if kind == 'none':
            raise ActionRejected('NOTHING_UNDER_ANALYSER')
        oid = self._new_op('stage.auto_height')
        self.acq = dict(kind='auto_height', record_id=None, op=oid, t0=self.time_s, duration_s=AUTO_HEIGHT_S, target=(kind, key))
        return dict(operation_id=oid)

    def _auto_height_done(self):
        a = self.acq
        kind, key, _ = self._analysed_target()
        h = self._surface_height()
        z = max(STAGE_TRAVEL['z'][0], min(STAGE_TRAVEL['z'][1], -h + self.rng_mount.gauss(0.0, 5e-6)))
        self.stage_target = dict(self.stage, z=z)
        op = self.operations[a['op']]
        op.update(phase='completed', finished_s=self.time_s, z_m=z)
        self.acq = None
        self._event('stage', 'AUTO_HEIGHT_DONE', f'自动高度完成：Z={z * 1e3:.3f} mm')

    # ---------------------------------------------------------------- X-ray, analyser, neutraliser, ion gun
    def _xray_trip(self, code, msg):
        self.xray_on, self.hv, self.emission = False, 0.0, 0.0
        self.xray_fault = code
        self._event('error', code, msg)
        if self.acq is not None and self.acq['kind'] in ('spectrum', 'image', 'depth_profile', 'calibration', 'auto_height'):
            self._stop_acq('failed', 'XRAY_OFF')

    def _cmd_xray_set_source(self, on, emission_a):
        self._need_ready()
        if on:
            if self.p_sac > XRAY_MAX_PA:
                raise ActionRejected('VACUUM_NOT_READY')
            if not self._chiller_ok():
                raise ActionRejected('COOLING_WATER_LOW')
            if 'xray_filament_failure' in self.faults:
                raise ActionRejected('FILAMENT_FAILURE')
            if emission_a * ANODE_HV_V > XRAY_MAX_W + 1e-9 or emission_a <= 0.0:
                raise ActionRejected('INVALID_ARGUMENT')
            if self.acq is not None and self.acq['kind'] in ('spectrum', 'image', 'depth_profile', 'calibration', 'auto_height') and abs(emission_a - self.emission_set) > 1e-12:
                raise ActionRejected('ACQUISITION_RUNNING')
            self.xray_on = True
            self.xray_fault = None
            self.emission_set = emission_a
            self._event('xray', 'XRAY_ON', f'X射线源开启：{ANODE_HV_V / 1000:.0f} kV×{emission_a * 1000:.1f} mA')
        else:
            if self.xray_on:
                self.xray_on, self.hv, self.emission = False, 0.0, 0.0
                self._event('xray', 'XRAY_OFF', 'X射线源关闭')
                if self.acq is not None and self.acq['kind'] in ('spectrum', 'image', 'depth_profile', 'calibration', 'auto_height'):
                    self._stop_acq('failed', 'XRAY_OFF')
            self.emission_set = emission_a if emission_a > 0 else self.emission_set
        return dict(on=self.xray_on, power_setpoint_w=ANODE_HV_V * self.emission_set if self.xray_on else 0.0)

    def _need_beam(self):
        if not self._xray_at_setpoint():
            raise ActionRejected('XRAY_NOT_READY')
        if self.p_sac > XRAY_MAX_PA:
            raise ActionRejected('VACUUM_NOT_READY')

    def _cmd_analyser_configure(self, lens_mode, aperture, pass_energy_ev):
        self._need_ready()
        self._need_idle()
        self.lens_mode, self.aperture, self.pe = lens_mode, aperture, float(pass_energy_ev)
        return dict(lens_mode=lens_mode, aperture=aperture, pass_energy_ev=self.pe)

    def _cmd_neutraliser_set(self, on, filament_current_a, charge_balance_v, filament_bias_v):
        self._need_ready()
        if on and 'neutraliser_filament_failure' in self.faults:
            raise ActionRejected('FILAMENT_FAILURE')
        self.neut_on = on
        self.neut = dict(filament_current_a=filament_current_a, charge_balance_v=charge_balance_v, filament_bias_v=filament_bias_v)
        self._event('neutraliser', 'NEUTRALISER_' + ('ON' if on else 'OFF'), '电子中和器' + ('开启' if on else '关闭'))
        return dict(on=on)

    def _cmd_ion_gun_set(self, on, energy_ev, raster_m):
        self._need_ready()
        if self.etching:
            raise ActionRejected('ION_BEAM_ON')
        if self.acq is not None and self.acq['kind'] in ('depth_profile', 'calibration'):      # the routine owns the gun (energy and raster fixed at its start)
            raise ActionRejected('ACQUISITION_RUNNING')
        self.ion_on = on
        self.ion_energy, self.ion_raster = energy_ev, raster_m
        self._event('ion_gun', 'ION_GUN_' + ('STANDBY' if on else 'OFF'), f'离子枪{"待机" if on else "关闭"}：{energy_ev:.0f} eV、光栅{raster_m * 1e3:.1f} mm')
        return dict(on=on, energy_ev=energy_ev, raster_m=raster_m)

    def _cmd_ion_gun_etch(self, duration_s):
        self._need_ready()
        self._need_idle()
        self._need_no_transfer()
        if not self.ion_on:
            raise ActionRejected('ION_GUN_OFF')
        if self.stage_holder is None:
            raise ActionRejected('STAGE_EMPTY')
        if self._stage_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        oid = self._new_op('ion_gun.etch')
        self.acq = dict(kind='etch', record_id=None, op=oid, t0=self.time_s, duration_s=duration_s)
        self.etching = True
        self._event('ion_gun', 'ETCH_START', f'溅射{duration_s:.0f} s')
        return dict(operation_id=oid)

    def _cmd_ion_gun_measure_current(self):
        self._need_ready()
        if not self.ion_on:
            raise ActionRejected('ION_GUN_OFF')
        kind, key, _ = self._analysed_target()
        if kind != 'reference' or key != 'faraday_cup' or self._stage_moving():
            raise ActionRejected('NOT_ON_FARADAY_CUP')
        i = self._ion_current() * (1.0 + 0.01 * self.rng_mount.gauss(0.0, 1.0))
        return dict(current_a=i, current_density_a_m2=i / self.ion_raster ** 2)

    # ---------------------------------------------------------------- acquisitions
    def _check_regions(self, regions, mode):
        names = [r['name'] for r in regions]
        if len(set(names)) != len(names):
            raise ActionRejected('DUPLICATE_REGION')
        out = []
        for r in regions:
            if r['end_be_ev'] <= r['start_be_ev']:
                raise ActionRejected('INVALID_REGION')
            if mode == 'snapshot':
                w = SNAPSHOT_WINDOW_PER_PE * self.pe
                if r['end_be_ev'] - r['start_be_ev'] > w + 1e-9:
                    raise ActionRejected('REGION_EXCEEDS_SNAPSHOT_WINDOW')
                step = w / DLD_CHANNELS
            else:
                step = r['step_ev']
            n = int(math.floor((r['end_be_ev'] - r['start_be_ev']) / step + 1e-9)) + 1
            if n > 20000:
                raise ActionRejected('INVALID_REGION')
            axis = [round(r['start_be_ev'] + i * step, 6) for i in range(n)]
            dur = (r['dwell_s'] * r['sweeps'] + 0.5) if mode == 'snapshot' else (n * r['dwell_s'] * r['sweeps'] + 1.0)
            out.append(dict(name=r['name'], be=axis, step=step, dwell=r['dwell_s'], sweeps=r['sweeps'], duration=dur, mode=mode,
                            expected=np.zeros(n), counts=None))
        return out

    def _settings(self):
        return dict(aperture=self.aperture, lens_mode=self.lens_mode, pass_energy_ev=self.pe, xray_power_w=self._xray_power(), neutraliser_on=self.neut_on,
                    tilt_rad=self.stage['tilt'], stage_x_m=round(self.stage['x'], 6), stage_y_m=round(self.stage['y'], 6), stage_z_m=round(self.stage['z'], 6),
                    rotation_rad=self.stage['rotation'], energy_calibration_id=self.scale_cal['calibration_id'])

    def _need_acquire(self):
        self._need_ready()
        self._need_idle()
        self._need_no_transfer()
        self._need_beam()
        if self.stage_holder is None:
            raise ActionRejected('STAGE_EMPTY')
        if self._stage_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')

    def _new_record(self, prefix, kind, **data):
        self.seq[prefix.lower()] += 1
        rid = f'{prefix}{self.seq[prefix.lower()]:04d}'
        self.records[rid] = dict(record_id=rid, kind=kind, status='running', started_s=self.time_s, finished_s=None, position_label=self.position_label,
                                 settings=self._settings(), spectra=[], image=None, levels=None, note_zh='', reason=None, **data)
        return rid

    def _cmd_acquisition_spectrum(self, regions, mode):
        self._need_acquire()
        regs = self._check_regions(regions, mode)
        rid = self._new_record('SP', 'spectrum')
        oid = self._new_op('acquisition.spectrum', rid)
        self.acq = dict(kind='spectrum', record_id=rid, op=oid, t0=self.time_s, regions=regs, index=0, region_t0=self.time_s,
                        duration_s=sum(r['duration'] for r in regs), flushed_t=self.time_s)
        self._event('acquisition', 'ACQUISITION_START', f'谱采集{rid}：{len(regs)}个区域、{mode}', rid=rid)
        return dict(operation_id=oid)

    def _cmd_acquisition_image(self, be_ev, fov, pass_energy_ev, duration_s):
        self._need_acquire()
        rid = self._new_record('IM', 'image')
        oid = self._new_op('acquisition.image', rid)
        self.acq = dict(kind='image', record_id=rid, op=oid, t0=self.time_s, duration_s=duration_s, be=be_ev, fov=fov, pe=pass_energy_ev,
                        expected=np.zeros((IMAGE_PIXELS, IMAGE_PIXELS)), flushed_t=self.time_s)
        self._event('acquisition', 'IMAGE_START', f'成像{rid}：{be_ev:.1f} eV、{fov}', rid=rid)
        return dict(operation_id=oid)

    def _cmd_depth_profile_start(self, levels, etch_s, regions):
        self._need_acquire()
        if not self.ion_on:
            raise ActionRejected('ION_GUN_OFF')
        regs = self._check_regions(regions, 'scanned')
        rid = self._new_record('DP', 'depth_profile')
        self.records[rid]['levels'] = []
        oid = self._new_op('depth_profile.start', rid)
        acq_s = sum(r['duration'] for r in regs)
        self.acq = dict(kind='depth_profile', record_id=rid, op=oid, t0=self.time_s, levels=levels, etch_s=etch_s, template=regions, level=0,
                        phase='acquire', regions=self._check_regions(regions, 'scanned'), index=0, region_t0=self.time_s, flushed_t=self.time_s,
                        duration_s=(levels + 1) * acq_s + levels * etch_s, etch_t0=None, etched_s=0.0)
        self._event('acquisition', 'DEPTH_PROFILE_START', f'深度剖析{rid}：{levels}级、每级溅射{etch_s:.0f} s', rid=rid)
        return dict(operation_id=oid)

    def _cmd_calibration_energy_scale(self):
        self._need_acquire()
        if not self.ion_on:
            raise ActionRejected('ION_GUN_OFF')
        rid = self._new_record('CA', 'calibration')
        oid = self._new_op('calibration.energy_scale', rid)
        plan = []
        for ref, (line, be) in (('Au', ('4f7/2', 83.96)), ('Ag', ('3d5/2', 368.21)), ('Cu', ('2p3/2', 932.62))):
            plan += [('move', ref), ('etch', ref), ('measure', ref, line, be)]
        self.acq = dict(kind='calibration', record_id=rid, op=oid, t0=self.time_s, plan=plan, step=0, phase_t0=self.time_s, flushed_t=self.time_s,
                        duration_s=None, results={}, regions=None, index=0, region_t0=self.time_s, saved_pe=self.pe, saved_aperture=self.aperture)
        self._calibration_next()
        self._event('calibration', 'CALIBRATION_START', f'能量标尺标定{rid}（ISO 15472：Au 4f7/2、Ag 3d5/2、Cu 2p3/2）', rid=rid)
        return dict(operation_id=oid)

    def _calibration_next(self):
        a = self.acq
        if a['step'] >= len(a['plan']):
            self._calibration_done()
            return
        st = a['plan'][a['step']]
        a['phase_t0'] = self.time_s
        if st[0] == 'move':
            self.stage_target = dict(x=-REF_BLOCK_X, y=-REF_Y[st[1]], z=0.0, tilt=0.0, rotation=0.0)
            self.position_label = 'reference:' + st[1]
            self._stage_error_draw()
            a['sub'] = 'move'
        elif st[0] == 'etch':
            self.etching = True
            a['sub'] = 'etch'
        else:
            be = st[3]
            self.pe, self.aperture = 10.0, 'slot'
            a['regions'] = self._check_regions([dict(name=f'{st[1]} {st[2]}', start_be_ev=be - 3.0, end_be_ev=be + 3.0, step_ev=0.02, dwell_s=0.1, sweeps=2)],
                                               'scanned')
            a['index'] = 0
            a['region_t0'] = self.time_s
            a['flushed_t'] = self.time_s
            a['sub'] = 'measure'

    def _calibration_done(self):
        a = self.acq
        rec = self.records[a['record_id']]
        refs = {'Au': 83.96, 'Ag': 368.21, 'Cu': 932.62}
        meas = {k: v for k, v in a['results'].items()}
        # measured positions are on the reported scale of the calibration in force; convert them to the raw instrument scale first
        o0, g0 = self.scale_cal['offset_ev'], self.scale_cal['gain']
        raw = {k: v * (1 + g0) + o0 for k, v in meas.items()}
        g = (raw['Cu'] - raw['Au']) / (refs['Cu'] - refs['Au']) - 1.0
        o = raw['Au'] - refs['Au'] * (1 + g)
        resid_ag = (raw['Ag'] - o) / (1 + g) - refs['Ag']
        cid = a['record_id']
        self.scale_cal = dict(offset_ev=o, gain=g, calibration_id=cid, calibrated_s=self.time_s)
        self.calibrations[cid] = dict(offset_ev=o, gain=g, measured_ev=meas, reference_ev=refs, ag_linearity_error_ev=resid_ag)
        rec.update(status='completed', finished_s=self.time_s,
                   note_zh=f'ISO 15472：两点修正（Au 4f7/2、Cu 2p3/2）偏移{o:+.3f} eV、增益{g:+.2e}；Ag 3d5/2线性检查{resid_ag:+.3f} eV')
        rec['calibration'] = dict(offset_ev=o, gain=g, measured_ev=meas, reference_ev=refs, ag_linearity_error_ev=resid_ag)
        self.pe, self.aperture = a['saved_pe'], a['saved_aperture']
        op = self.operations[a['op']]
        op.update(phase='completed', finished_s=self.time_s)
        self.acq = None
        self._rate_cache.clear()
        self._event('calibration', 'CALIBRATION_DONE', rec['note_zh'], rid=cid)

    def _cmd_acquisition_result(self, record_id):
        r = self.records.get(record_id)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._out(r)

    @staticmethod
    def _out(rec):
        r = {k: copy.deepcopy(v) for k, v in rec.items() if not k.startswith('_') and k != 'calibration'}
        if 'calibration' in rec:
            r['note_zh'] = rec['note_zh']
        return {k: r.get(k) for k in RECORD['properties']}

    # ---------------------------------------------------------------- spectroscopy engine
    def _leak_resistance(self, sid, ci=-1):
        """Resistance to ground of a sample column: bulk insulators 5e11 ohm; insulating films on a conducting substrate leak through the film,
        R = 5e11 ohm x min(1, t_insulating / 10 um) (thin oxide films on metals do not charge noticeably); semiconductors 1e7, metals 1e2 ohm."""
        s = self.samples[sid]
        removed = s['craters'][ci]['removed']['base'] if ci >= 0 else 0.0
        top, t_ins = 0.0, 0.0
        for x in s['layers']:
            bottom = top + x['thickness_m']
            left = max(0.0, bottom - max(top, removed))
            if substance(x['substance_id'])['electrical'] == 'insulator':
                t_ins += left
            top = bottom
        sub = substance(s['substrate'])['electrical']
        if sub == 'insulator':
            return R_LEAK_OHM['insulator']
        return R_LEAK_OHM[sub] + R_LEAK_OHM['insulator'] * min(1.0, t_ins / 1.0e-5)

    def _electrical(self, sid, ci=-1):
        r = self._leak_resistance(sid, ci)
        return 'insulator' if r >= 1.0e9 else 'semiconductor' if r >= 1.0e5 else 'conductor'

    def _column(self, sid, feature, ci=-1):
        """Layer stack (top first) of a column type with its removed depth and contamination overlayer: pristine area (ci = -1) or sputter crater ci."""
        s = self.samples[sid]
        cr = s['craters'][ci] if ci >= 0 else None
        t_c = cr['c0_m'] if cr else s['contamination_m']
        stack = [(C_ADV, t_c)] if t_c > 0 else []
        if feature:
            stack.append((s['pattern']['feature_substance_id'], s['pattern']['feature_thickness_m']))
        stack += [(x['substance_id'], x['thickness_m']) for x in s['layers']]
        kf = 'feature' if feature else 'base'
        return stack, s['substrate'], (cr['removed'][kf] if cr else 0.0), (cr['recontam_m'] if cr else 0.0)

    def _holder_column(self):
        return [(C_ADV, 1.0e-9)] + list(HOLDER_SURFACE), 'fe', 0.0, 0.0

    def _pattern_is_feature(self, p, x, y):
        k = p['kind']
        if k == 'uniform':
            return False
        if k == 'grid':
            return (x % p['pitch_m']) < p['width_m'] or (y % p['pitch_m']) < p['width_m']
        if k == 'stripes':
            return (x % p['pitch_m']) < p['width_m']
        # islands: discs of diameter width on a jittered square lattice whose pitch gives the coverage
        pitch = p['width_m'] * math.sqrt(math.pi / 4.0 / p['coverage'])
        i, j = math.floor(x / pitch), math.floor(y / pitch)
        h = (i * 73856093) ^ (j * 19349663)
        jx = ((h % 1000) / 1000.0 - 0.5) * (pitch - p['width_m']) * 0.9
        jy = (((h // 1000) % 1000) / 1000.0 - 0.5) * (pitch - p['width_m']) * 0.9
        cx, cy = (i + 0.5) * pitch + jx, (j + 0.5) * pitch + jy
        return (x - cx) ** 2 + (y - cy) ** 2 <= (p['width_m'] / 2) ** 2

    @staticmethod
    def _crater_at(s, x, y):
        """Latest sputter crater of a sample covering the sample-frame point (x, y), or -1."""
        for i in range(len(s['craters']) - 1, -1, -1):
            c = s['craters'][i]
            if abs(x - c['x']) <= c['half'] and abs(y - c['y']) <= c['half']:
                return i
        return -1

    def _area_columns(self, aperture=None):
        """Weighted column types in the analysed area: {(owner, feature): weight} with the illumination-weighted area fractions, and the area (m^2)."""
        kind, key, pos = self._analysed_target()
        ap = aperture or self.aperture
        pts = aperture_points(ap, 144 if ap == 'slot' else 100)
        area = APERTURES['slot'][0] * APERTURES['slot'][1] if ap == 'slot' else math.pi * (APERTURES[ap] / 2) ** 2
        if kind == 'none':
            return {}, area, (kind, key)
        w = illumination(pts[:, 0], pts[:, 1])
        tilt, rot = self.stage['tilt'], self.stage['rotation']
        cols = {}
        c, s_ = math.cos(-rot), math.sin(-rot)
        for (px, py), wi in zip(pts, w):
            dy = py / max(math.cos(tilt), 0.2)                       # foreshortening along the tilt direction
            if kind == 'reference':
                gx, gy = pos[0] + px, pos[1] + dy
                if gx * gx + gy * gy > REF_RADIUS ** 2:
                    owner = ('refblock', None)
                else:
                    sid = 'ref:' + key
                    owner = ('sample', sid, False if key != 'grid' else self._pattern_is_feature(self.samples[sid]['pattern'], gx, gy),
                             self._crater_at(self.samples[sid], gx, gy)) if key != 'faraday_cup' else ('hole', None)
            else:
                lx, ly = c * px - s_ * dy, s_ * px + c * dy               # offset rotated into the holder frame
                hx0, hy0 = self._analysed_holder_xy()
                hx, hy = hx0 + lx, hy0 + ly
                owner = ('holder', None)
                for sid, (sx, sy) in self.holders[self.stage_holder]['samples'].items():
                    half = self.samples[sid]['lateral_size_m'] / 2
                    if abs(hx - sx) <= half and abs(hy - sy) <= half:
                        owner = ('sample', sid, self._pattern_is_feature(self.samples[sid]['pattern'], hx - sx, hy - sy),
                                 self._crater_at(self.samples[sid], hx - sx, hy - sy))
                        break
            cols[owner] = cols.get(owner, 0.0) + wi
        n = len(pts)
        return {k: v / n for k, v in cols.items()}, area, (kind, key)

    def _centre_crater(self):
        kind, key, pos = self._analysed_target()
        if kind == 'sample':
            return self._crater_at(self.samples[key], pos[0], pos[1])
        if kind == 'reference' and key != 'faraday_cup':
            return self._crater_at(self.samples['ref:' + key], pos[0], pos[1])
        return -1

    def _analysed_holder_xy(self):
        x, y = -self.stage['x'], -self.stage['y']
        c, s = math.cos(-self.stage['rotation']), math.sin(-self.stage['rotation'])
        return c * x - s * y, s * x + c * y

    @staticmethod
    def _adventitious_components():
        adv = library()['adventitious_carbon']
        return adv

    def _slices(self, stack, substrate, removed, sigma, overlayer=0.0):
        """Depth slices (0.1 nm) below the present surface: fraction of each material per slice; a contamination overlayer on top, then the original
        stack shifted by the removed depth and smoothed by the Gaussian mixing / roughness width."""
        from scipy.special import erf
        dz = 0.1e-9
        zmax = 25e-9
        z = (np.arange(int(zmax / dz)) + 0.5) * dz
        zo = z - overlayer + removed                                    # depth in the original stack coordinates
        tops, fr = [], {}
        top = 0.0
        for mat, t in stack:
            tops.append((mat, top, top + t))
            top += t
        tops.append((substrate, top, math.inf))
        sq = max(sigma, 0.05e-9) * math.sqrt(2.0)
        below = z >= overlayer
        for mat, a, b in tops:
            if b != math.inf and b <= removed - 6 * sq:
                continue
            if mat == C_ADV:                                            # adsorbed contamination: preferentially sputtered, not mixed into the solid
                f = ((zo >= a) & (zo < b)).astype(float)
            else:
                f = 0.5 * (erf((zo - a) / sq) - (erf((zo - b) / sq) if b != math.inf else -1.0))
            f = np.clip(f, 0.0, 1.0) * below
            if f.max() > 1e-6:
                fr[mat] = fr.get(mat, 0.0) + f
        if overlayer > 0:
            fr[C_ADV] = fr.get(C_ADV, 0.0) + (~below).astype(float)
        tot = sum(fr.values())
        return {k: v / np.maximum(tot, 1e-12) for k, v in fr.items()}, dz

    def _material(self, mat):
        if mat == C_ADV:
            adv = library()['adventitious_carbon']
            return dict(substance_id=C_ADV, composition={'C': 1.0, 'O': adv['oxygen_per_carbon']}, density_g_cm3=adv['density_g_cm3'],
                        molar_mass_g_mol=12.011 + 15.999 * adv['oxygen_per_carbon'], valence_electrons=4 + 6 * adv['oxygen_per_carbon'], band_gap_ev=5.0,
                        electrical='insulator', class_='contamination', components=[], satellites=[], broadening_class_ev=1.0)
        return substance(mat)

    def _peaks(self, owner):
        """All emission lines (true binding energy, area in counts/s at unit transmission scale, Lorentzian width, Gaussian material width, DS alpha,
        depth factor) of one column type, for the current tilt, removal state and surface potential (without instrument broadening)."""
        if owner[0] == 'sample':
            sid, feat, ci = owner[1], owner[2], owner[3]
            stack, substrate, removed, over = self._column(sid, feat, ci)
            cr = self.samples[sid]['craters'][ci] if ci >= 0 else None
            sigma = math.hypot(cr['mix_m'], ROUGHNESS_FRACTION * removed) if cr else 0.2e-9
            red, imp = (cr['reduction'], cr['implant']) if cr else (0.0, 0.0)
        elif owner[0] == 'holder':
            stack, substrate, removed, over = self._holder_column()
            sigma, red, imp = 0.2e-9, 0.0, 0.0
        else:
            return []
        fr, dz = self._slices(stack, substrate, removed, sigma, over)
        cos_t = max(math.cos(self.stage['tilt']), 0.05)
        z = (np.arange(len(next(iter(fr.values())))) + 0.5) * dz
        alt = z < 2.0 * max(sigma, 0.5e-9)                         # altered layer (reduction, implantation)
        # atom densities (1/m^3) per (material, element, state) along z
        dens = []                                                  # (material, element, state_component or None, n(z) array)
        mats = {m: self._material(m) for m in fr}
        for m, f in fr.items():
            sub = mats[m]
            n_fu = sub['density_g_cm3'] * 1e6 / sub['molar_mass_g_mol'] * N_AVOGADRO
            red_f = red if (m in REDUCIBLE_OXIDES and METAL_OF.get(m)) else 0.0
            for el, cnt in sub['composition'].items():
                if el == 'H':
                    continue
                n = n_fu * cnt * f
                if red_f and el != 'O':
                    dens.append((METAL_OF[m], el, None, n * red_f * alt))
                    n = n * (1.0 - red_f * alt)
                elif red_f and el == 'O':
                    n = n * (1.0 - 0.8 * red_f * alt)
                dens.append((m, el, None, n))
        if imp > 0:
            host = sum(d[3] for d in dens)
            dens.append(('implant', 'Ar', None, imp * host * alt))
        # attenuation: effective 1/lambda along z for each kinetic energy
        lines = []
        for m, el, _, n in dens:
            if not np.any(n > 0):
                continue
            for comp, lv, be_true, width, alpha, sat in self._lines_of(m, el):
                ke = HV_EV - be_true
                if ke < 30.0:
                    continue
                lines.append((m, el, comp, lv, be_true, width, alpha, sat, ke, n))
        if not lines:
            return []
        kes = sorted({round(l[8] / 5.0) * 5.0 for l in lines})
        inv_lam = {}
        for ke in kes:
            il = np.zeros_like(z)
            for m, f in fr.items():
                il += f / (imfp_nm(mats[m], ke) * 1e-9)
            tau = np.concatenate([[0.0], np.cumsum(il * dz)[:-1]]) / cos_t
            inv_lam[ke] = (np.exp(-tau), il)
        out = []
        el_cache = {}
        for m, el, comp, lv, be_true, width, alpha, sat, ke, n in lines:
            att, il = inv_lam[round(ke / 5.0) * 5.0]
            w = n * att * dz
            nw = float(w.sum())
            if nw <= 0:
                continue
            e = el_cache.setdefault(el, element(el))
            lev = e['levels'][lv] if lv in e['levels'] else None
            sigma_mb = lev['sigma_mb'] if lev else None
            beta = lev['beta'] if lev else 2.0
            if sat is not None:
                sigma_mb, beta = sat['sigma_mb'], sat['beta']
            if not sigma_mb:
                continue
            # loss-order weights c_n = <(path/lambda)^n / n!> over the emission depth distribution (path in units of the local IMFP along the exit)
            u = -np.log(np.maximum(att, 1e-300))
            coeffs = tuple(float((w * u ** k).sum() / nw / math.factorial(k)) for k in range(1, 5))
            mat = mats[m] if m in mats else (dict(broadening_class_ev=0.8) if m == 'implant' else substance(m))
            gmat = mat.get('broadening_class_ev') or 0.0
            area = nw * sigma_mb * 1e-22 * angular_factor(beta) * (sat['ratio'] if sat is not None else 1.0)
            out.append(dict(material=m, element=el, component=comp, level=lv, be=be_true, width=width, alpha=alpha, gmat=gmat, ke=ke, area=area,
                            loss=coeffs, kind=sat['kind'] if sat is not None else 'line'))
        return out


    def _lines_of(self, m, el):
        return _LINES_CACHE.setdefault((m, el), self._lines_of_uncached(m, el))

    def _lines_of_uncached(self, m, el):
        """(component label, level, true BE, Lorentzian width, DS alpha, satellite spec) for element el in material m: every core level of the element
        shifted by the chemical shift of the material's principal line; satellites and plasmons of that material; Auger lines."""
        e = element(el)
        out = []
        if m == 'implant':
            so = 2.15
            for lv, be in (('2p3/2', AR_2P32_EV), ('2p1/2', AR_2P32_EV + so)):
                out.append(('Ar implanted', lv, be, 0.12, 0.0, None))
            return out
        if m == C_ADV:
            adv = library()['adventitious_carbon']
            if el == 'C':
                ref = e['levels']['1s']['be_ev']
                for c in adv['components']:
                    shift = c['be_ev'] - ref
                    for lv, v in e['levels'].items():
                        if v['be_ev'] + shift > 15.0 and v['sigma_mb']:
                            out.append((f"C {c['site']}", lv, v['be_ev'] + shift, min(v['width_ev'] or 0.1, 5.0), 0.0,
                                        dict(sigma_mb=v['sigma_mb'], beta=v['beta'], ratio=c['fraction'], kind='line')))
                return out
            shift = 532.0 - e['levels']['1s']['be_ev']
            for lv, v in e['levels'].items():
                if v['be_ev'] + shift > 15.0 and v['sigma_mb']:
                    out.append(('O (adventitious)', lv, v['be_ev'] + shift, min(v['width_ev'] or 0.1, 5.0), 0.0, None))
            return out
        sub = substance(m)
        comps = [c for c in sub['components'] if c['element'] == el]
        tot = sum(c['count'] for c in comps) or 1.0
        metal = sub['class_'] == 'metal'
        alpha = e['ds_asymmetry'] if metal else 0.0
        for c in comps:
            pl = c['line']
            ref = e['levels'][pl]['be_ev'] if pl in e['levels'] else e['reference_be_ev']
            shift = c['be_ev'] - ref
            frac = c['count'] / tot
            dbl = c.get('doublet')                     # spin-orbit splitting measured for this chemical state (paired NIST records)
            for lv, v in e['levels'].items():
                be = c['be_ev'] + dbl['splitting_ev'] if (dbl and lv == dbl['partner']) else v['be_ev'] + shift
                if be <= 15.0 or not v['sigma_mb']:
                    continue
                out.append((f"{el} {c['site']}", lv, be, min(v['width_ev'] or 0.1, 5.0), alpha,
                            dict(sigma_mb=v['sigma_mb'], beta=v['beta'], ratio=frac, kind='line') if len(comps) > 1 else None))
        for sat in sub['satellites']:
            if sat['element'] != el:
                continue
            lv = sat['line']
            v = e['levels'][lv]
            out.append((f"{el} {sat['kind']}", lv, sat['be_ev'], 1.2, 0.0,
                        dict(sigma_mb=v['sigma_mb'], beta=v['beta'], ratio=sat['relative_intensity'], kind='satellite')))
        if metal and e.get('plasmon_ev'):
            pl = e['principal_line']
            v = e['levels'][pl]
            for k, r in ((1, 0.30), (2, 0.08)):
                out.append((f'{el} plasmon {k}', pl, v['be_ev'] + (sub['components'][0]['be_ev'] - v['be_ev']) + k * e['plasmon_ev'], 2.0, 0.0,
                            dict(sigma_mb=v['sigma_mb'], beta=v['beta'], ratio=r, kind='plasmon')))
        for a in e['auger']:
            if not a['excited_by_al_ka']:
                continue
            hole = e['levels'].get(a['initial_hole'])
            if not hole or not hole['sigma_mb']:
                continue
            fy = hole['fluorescence_yield'] or 0.0
            out.append((f"{el} {a['line']} Auger", a['initial_hole'], HV_EV - a['ke_ev'], 3.0, 0.0,
                        dict(sigma_mb=hole['sigma_mb'] * (2 if '/' in a['initial_hole'] else 1) * 3 / 2 if False else hole['sigma_mb'], beta=2.0,
                             ratio=0.5 * (1.0 - fy), kind='auger')))
        return out

    def _instrument_gauss(self, pe=None, aperture=None):
        pe = pe or self.pe
        ap = aperture or self.aperture
        return math.hypot(XRAY_LINE_FWHM_EV, ANALYSER_RES_PER_PE[ap] * pe)

    def _transmission(self, ke, pe=None):
        pe = pe or self.pe
        return LENS_MODES[self.lens_mode] * (pe / 10.0) ** TRANSMISSION_PE_EXP * (ke / 1118.0) ** TRANSMISSION_KE_EXP

    def _focus_factor(self):
        dz = self._focus_offset()
        if dz is None:
            return 0.0
        return math.exp(-0.5 * (dz / FOCUS_SIGMA_M[self.aperture]) ** 2)

    def _flux_scale(self):
        return self._xray_power() / SPEC_POWER_W

    def _spectrum_model(self, be_axis, step, pe=None, aperture=None, noise_free=True):
        """Expected count rate (counts/s per data point) on a reported binding-energy axis for the current state."""
        be_axis = np.asarray(be_axis, float)
        pe = pe or self.pe
        ap = aperture or self.aperture
        cols, area, target = self._area_columns(ap)
        o_t, g_t = self.scale_true['offset_ev'], self.scale_true['gain']
        o_c, g_c = self.scale_cal['offset_ev'], self.scale_cal['gain']
        raw = be_axis * (1 + g_c) + o_c
        be_true = (raw - o_t) / (1 + g_t)                              # true (sample) binding energy seen at each reported point
        lo, hi = float(be_true.min()) - 2.0, float(be_true.max()) + 2.0
        fine_step = min(0.05, step / 4.0) if step < 0.5 else 0.1
        pad = 60.0
        grid = np.arange(lo - pad, hi + fine_step, fine_step)
        dens = np.zeros_like(grid)
        v_surf = round(self._v_analysed(), 2)                          # quantised as in the rate-cache key
        sid = target[1] if target[0] == 'sample' else ('ref:' + target[1] if target[0] == 'reference' else None)
        ch = 0.0
        if target[0] == 'sample' and self._electrical(sid, self._centre_crater()) == 'insulator':
            ch = DIFF_CHARGE_FRACTION * abs(v_surf) + DIFF_CHARGE_FLOOR_EV
        g_inst = self._instrument_gauss(pe, ap)
        flux = self._flux_scale() * self._focus_factor()
        k = self._k_inst()
        w_det = SNAPSHOT_WINDOW_PER_PE * pe
        area_frac = area / (APERTURES['slot'][0] * APERTURES['slot'][1])
        buckets = {}
        for owner, wgt in cols.items():
            v_col = v_surf if (owner[0] == 'sample' and owner[1] == sid) else 0.0
            for p in self._peaks_cached(owner):
                be_app = p['be'] + v_col
                if be_app > hi + 8.0:                                    # peaks above the region add nothing (losses go to higher BE)
                    continue
                amp = k * flux * wgt * area_frac * p['area'] * self._transmission(p['ke'] - v_col, pe) * w_det
                if be_app < grid[0] + 3.0:                              # far below the region: its inelastic tail only (first order)
                    if p['kind'] in ('line', 'satellite'):
                        dens += tail_first_order(grid, be_app, amp, p['loss'][0])
                    continue
                g = math.sqrt(g_inst ** 2 + p['gmat'] ** 2 + ch ** 2) if p['kind'] != 'auger' else math.sqrt(g_inst ** 2 + 4.0 ** 2)
                prim = amp * line_profile(grid, be_app, p['width'], g, p['alpha'])
                if p['kind'] in ('line', 'satellite'):
                    q = tuple(round(c / 0.02) * 0.02 for c in p['loss'])
                    if q not in buckets:
                        buckets[q] = np.zeros_like(grid)
                    buckets[q] += prim
                else:
                    dens += prim
        for q, prim in buckets.items():
            dens += prim + inelastic_background(grid, prim, q)
        # average over each data point's step on the fine grid
        out = np.empty(len(be_axis))
        for i, e in enumerate(be_true):
            a, b = e - step / 2.0, e + step / 2.0
            i0, i1 = int(np.searchsorted(grid, a)), int(np.searchsorted(grid, b))
            seg = dens[max(i0, 0):max(i1, i0 + 1)]
            out[i] = float(seg.mean()) if len(seg) else float(np.interp(e, grid, dens))
        out += DARK_CPS_PER_EV * w_det
        return out / (1.0 + DEAD_TIME_S * out)                          # delay-line detector dead time (non-paralysable)

    def _peaks_cached(self, owner):
        key = (owner, self._owner_state_key(owner), round(self.stage['tilt'], 4))
        v = self._rate_cache.get(('peaks',) + key)
        if v is None:
            v = self._peaks(owner)
            self._rate_cache[('peaks',) + key] = v
        return v

    def _owner_state_key(self, owner):
        if owner[0] != 'sample':
            return None
        s = self.samples[owner[1]]
        if owner[3] >= 0:
            c = s['craters'][owner[3]]
            return (round(c['removed']['feature' if owner[2] else 'base'] * 1e12), round(c['recontam_m'] * 1e12), round(c['mix_m'] * 1e12),
                    round(c['reduction'], 4), round(c['implant'], 5))
        return (round(s['contamination_m'] * 1e12),)

    def _k_inst(self):
        return _K_INST[0] if _K_INST[0] is not None else _calibrate_k_inst()

    # ---------------------------------------------------------------- count accumulation
    def _region_rates(self, reg):
        key = ('region', reg['name'], reg['be'][0], reg['be'][-1], len(reg['be']), reg['step'], reg['mode'], self._state_key())
        r = self._rate_cache.get(key)
        if r is None:
            if reg['mode'] == 'snapshot':
                r = self._spectrum_model(reg['be'], reg['step']) / DLD_CHANNELS          # each channel sees 1/128 of the window
            else:
                r = self._spectrum_model(reg['be'], reg['step'])
            self._rate_cache[key] = r
        return r

    def _state_key(self):
        cols, area, target = self._area_columns()
        return (tuple(sorted((k, round(v, 6)) for k, v in cols.items())), tuple(self._owner_state_key(k) for k in sorted(cols)), round(self._v_analysed(), 2),
                round(self._xray_power(), 1), self.aperture, self.lens_mode, self.pe, round(self.stage['tilt'], 4),
                round(self._focus_offset() or 0.0, 7), round(self.scale_cal['offset_ev'], 6), round(self.scale_cal['gain'], 9))

    def _flush(self):
        """Integrate the expected counts of the running acquisition from the last flush to now with the present state (called at every cycle, every
        command and every event), so readings never change the counts."""
        a = self.acq
        if a is None:
            return
        t = self.time_s
        if a['kind'] in ('spectrum', 'depth_profile', 'calibration') and a.get('regions') and a.get('sub', 'measure') == 'measure' and \
                (a['kind'] != 'depth_profile' or a['phase'] == 'acquire'):
            dt = t - a['flushed_t']
            if dt > 0 and a['index'] < len(a['regions']):
                reg = a['regions'][a['index']]
                eff = (reg['dwell'] * reg['sweeps']) / reg['duration']       # counting time share of the region (scanned and snapshot)
                if self._beam_ok():
                    with self._held_potential():
                        reg['expected'] += self._region_rates(reg) * dt * eff
        elif a['kind'] == 'image':
            dt = t - a['flushed_t']
            if dt > 0 and self._beam_ok():
                with self._held_potential():
                    a['expected'] += self._image_rates(a) * dt
        a['flushed_t'] = t

    @contextlib.contextmanager
    def _held_potential(self):
        """Count rates of a flush interval use the analysed sample's potential held at the start of its grid cell (event or command)."""
        cell = self._cell or {}
        sid = cell.get('sid')
        kind, key, _ = self._analysed_target()
        if sid is None or kind != 'sample' or key != sid:
            yield
            return
        now = self.v_surface.get(sid, 0.0)
        self.v_surface[sid] = cell['v']
        try:
            yield
        finally:
            self.v_surface[sid] = now

    def _beam_ok(self):
        return self._xray_at_setpoint() and not self._stage_moving() and self.stage_holder is not None

    def _count_rate_now(self):
        """Total detector count rate at this instant (reading; no state change)."""
        a = self.acq
        if a is None or not self._beam_ok():
            return 0.0
        if a['kind'] == 'image':
            return float(self._image_rates(a).sum())
        regs = a.get('regions')
        if not regs or a['index'] >= len(regs) or a.get('sub', 'measure') != 'measure' or (a['kind'] == 'depth_profile' and a['phase'] != 'acquire'):
            return 0.0
        r = self._region_rates(regs[a['index']])
        return float(r.max()) if regs[a['index']]['mode'] == 'scanned' else float(r.sum())

    def _finish_region(self):
        a = self.acq
        reg = a['regions'][a['index']]
        lam = reg['expected']
        counts = np.array([self._poisson(x) for x in lam], float)
        reg['counts'] = counts
        a['index'] += 1
        a['region_t0'] = self.time_s

    def _poisson(self, lam):
        if lam <= 0:
            return 0.0
        if lam < 50:
            L, k, p = math.exp(-lam), 0, 1.0
            while True:
                p *= self.rng_acq.random()
                if p <= L:
                    return float(k)
                k += 1
        return float(max(0, round(self.rng_acq.gauss(lam, math.sqrt(lam)))))

    def _spectra_of(self, regs):
        out = []
        for reg in regs:
            if reg['counts'] is None:
                continue
            t = reg['dwell'] * reg['sweeps']
            out.append(dict(name=reg['name'], binding_energy_ev=list(reg['be']), counts=[float(c) for c in reg['counts']],
                            counts_per_second=[float(c) / t for c in reg['counts']], step_ev=reg['step'], dwell_s=reg['dwell'], sweeps=reg['sweeps']))
        return out

    def _stop_acq(self, status, code):
        a = self.acq
        if a is None:
            return
        self._flush()
        op = self.operations[a['op']]
        op.update(phase=status if status != 'completed' else 'completed', finished_s=self.time_s, reason=code)
        rid = a.get('record_id')
        if rid:
            rec = self.records[rid]
            rec.update(status=status, finished_s=self.time_s, reason=code)
            if a['kind'] == 'spectrum':
                rec['spectra'] = self._spectra_of(a['regions'])
            if a['kind'] == 'calibration':
                self.pe, self.aperture = a['saved_pe'], a['saved_aperture']
        self.etching = False
        self.acq = None
        self._rate_cache.clear()
        self._event('acquisition' if code == 'USER_CANCEL' else 'error', 'OPERATION_' + ('CANCELLED' if code == 'USER_CANCEL' else 'FAILED'),
                    f'操作{a["kind"]}{"取消" if code == "USER_CANCEL" else "中止"}（{code}）', rid=rid or a['op'])

    def _cancel_op(self, op):
        if self.acq is not None and self.acq['op'] == op['operation_id']:
            self._stop_acq('cancelled', 'USER_CANCEL')
        elif self.transfer_op is not None and self.transfer_op['op'] == op['operation_id']:
            self._stop_transfer('USER_CANCEL')

    def _acq_advance(self):
        """Region / phase bookkeeping of the running acquisition at event times."""
        a = self.acq
        t = self.time_s
        if a['kind'] == 'spectrum':
            reg = a['regions'][a['index']]
            if t >= a['region_t0'] + reg['duration'] - 1e-9:
                self._finish_region()
                if a['index'] >= len(a['regions']):
                    rec = self.records[a['record_id']]
                    rec.update(status='completed', finished_s=t, spectra=self._spectra_of(a['regions']))
                    self.operations[a['op']].update(phase='completed', finished_s=t)
                    self.acq = None
                    self._event('acquisition', 'ACQUISITION_DONE', f'谱采集{rec["record_id"]}完成', rid=rec['record_id'])
        elif a['kind'] == 'image':
            if t >= a['t0'] + a['duration_s'] - 1e-9:
                img = np.array([[self._poisson(x) for x in row] for row in a['expected']], float)
                rec = self.records[a['record_id']]
                fov = IMAGING_FOV_M[a['fov']]
                rec.update(status='completed', finished_s=t, image=dict(be_ev=a['be'], fov_m=fov, pixels=IMAGE_PIXELS, pixel_size_m=fov / IMAGE_PIXELS,
                                                                       pass_energy_ev=a['pe'], duration_s=a['duration_s'], counts=img.astype(int).tolist(),
                                                                       resolution_m=self._image_resolution(a['fov'])))
                self.operations[a['op']].update(phase='completed', finished_s=t)
                self.acq = None
                self._event('acquisition', 'IMAGE_DONE', f'成像{rec["record_id"]}完成', rid=rec['record_id'])
        elif a['kind'] == 'depth_profile':
            if a['phase'] == 'acquire':
                reg = a['regions'][a['index']]
                if t >= a['region_t0'] + reg['duration'] - 1e-9:
                    self._finish_region()
                    if a['index'] >= len(a['regions']):
                        rec = self.records[a['record_id']]
                        rec['levels'].append(dict(level=a['level'], etch_time_s=a['etched_s'], spectra=self._spectra_of(a['regions'])))
                        if a['level'] >= a['levels']:
                            rec.update(status='completed', finished_s=t)
                            self.operations[a['op']].update(phase='completed', finished_s=t)
                            self.acq = None
                            self._event('acquisition', 'DEPTH_PROFILE_DONE', f'深度剖析{rec["record_id"]}完成', rid=rec['record_id'])
                            return
                        a['phase'] = 'etch'
                        a['etch_t0'] = t
                        self.etching = True
            elif a['phase'] == 'etch':
                if t >= a['etch_t0'] + a['etch_s'] - 1e-9:
                    self.etching = False
                    a['etched_s'] += a['etch_s']
                    a['level'] += 1
                    a['phase'] = 'acquire'
                    a['regions'] = self._check_regions(a['template'], 'scanned')
                    a['index'] = 0
                    a['region_t0'] = t
                    a['flushed_t'] = t
        elif a['kind'] == 'etch':
            if t >= a['t0'] + a['duration_s'] - 1e-9:
                self.etching = False
                self.operations[a['op']].update(phase='completed', finished_s=t)
                self.acq = None
                self._event('ion_gun', 'ETCH_DONE', '溅射完成')
        elif a['kind'] == 'auto_height':
            if t >= a['t0'] + AUTO_HEIGHT_S - 1e-9:
                self._auto_height_done()
        elif a['kind'] == 'calibration':
            sub = a.get('sub')
            if sub == 'move' and not self._stage_moving():
                a['step'] += 1
                self._calibration_next()
            elif sub == 'etch' and t >= a['phase_t0'] + 30.0 - 1e-9:
                self.etching = False
                a['step'] += 1
                self._calibration_next()
            elif sub == 'measure':
                reg = a['regions'][0]
                if a['index'] == 0 and t >= a['region_t0'] + reg['duration'] - 1e-9:
                    self._finish_region()
                    st = a['plan'][a['step']]
                    a['results'][st[1]] = self._peak_position(reg)
                    rec = self.records[a['record_id']]
                    rec['spectra'] = rec['spectra'] + self._spectra_of([reg])
                    a['step'] += 1
                    self._calibration_next()

    def _peak_position(self, reg):
        """Peak position on the reported scale: parabola vertex of the smoothed counts (ISO 15472 uses a similar local fit)."""
        c = np.convolve(reg['counts'], np.ones(5) / 5.0, mode='same')
        i = int(np.argmax(c))
        i = min(max(i, 1), len(c) - 2)
        y0, y1, y2 = c[i - 1], c[i], c[i + 1]
        den = y0 - 2 * y1 + y2
        d = 0.5 * (y0 - y2) / den if den != 0 else 0.0
        return float(reg['be'][i] + d * reg['step'])

    # ---------------------------------------------------------------- imaging model
    def _image_resolution(self, fov):
        px = IMAGING_FOV_M[fov] / IMAGE_PIXELS
        return max(IMAGING_BEST_RESOLUTION_M, 2.0 * px)

    def _image_rates(self, a):
        key = ('image', a['be'], a['fov'], a['pe'], self._state_key())
        r = self._rate_cache.get(key)
        if r is not None:
            return r
        fov = IMAGING_FOV_M[a['fov']]
        n = IMAGE_PIXELS
        px = fov / n
        xs = (np.arange(n) + 0.5) * px - fov / 2
        kind, key0, pos = self._analysed_target()
        img = np.zeros((n, n))
        if kind != 'none':
            window = 0.02 * a['pe']
            # per column type intensity at the imaging energy (spectral density x window)
            dens_of = {}
            tilt, rot = self.stage['tilt'], self.stage['rotation']
            c, s_ = math.cos(-rot), math.sin(-rot)
            hx0, hy0 = self._analysed_holder_xy() if kind != 'reference' else (0.0, 0.0)
            owners = np.empty((n, n), dtype=object)
            for iy, py in enumerate(xs):
                for ix, pxx in enumerate(xs):
                    dy = py / max(math.cos(tilt), 0.2)
                    if kind == 'reference':
                        gx, gy = pos[0] + pxx, pos[1] + dy
                        sid = 'ref:' + key0
                        if key0 == 'faraday_cup' or gx * gx + gy * gy > REF_RADIUS ** 2:
                            owners[iy, ix] = ('refblock', None)
                        else:
                            owners[iy, ix] = ('sample', sid, self._pattern_is_feature(self.samples[sid]['pattern'], gx, gy) if key0 == 'grid' else False,
                                              self._crater_at(self.samples[sid], gx, gy))
                        continue
                    lx, ly = c * pxx - s_ * dy, s_ * pxx + c * dy
                    hx, hy = hx0 + lx, hy0 + ly
                    ow = ('holder', None)
                    for sid, (sx, sy) in self.holders[self.stage_holder]['samples'].items():
                        half = self.samples[sid]['lateral_size_m'] / 2
                        if abs(hx - sx) <= half and abs(hy - sy) <= half:
                            ow = ('sample', sid, self._pattern_is_feature(self.samples[sid]['pattern'], hx - sx, hy - sy),
                                  self._crater_at(self.samples[sid], hx - sx, hy - sy))
                            break
                    owners[iy, ix] = ow
            for ow in set(owners.flat):
                if ow not in dens_of:
                    dens_of[ow] = self._point_intensity(ow, a['be'], window, a['pe'])
            for iy in range(n):
                for ix in range(n):
                    img[iy, ix] = dens_of[owners[iy, ix]]
            X, Y = np.meshgrid(xs, xs)
            img *= illumination(X, Y)
            res = self._image_resolution(a['fov'])
            sig_px = res / 2.3548 / px
            from scipy.ndimage import gaussian_filter
            img = gaussian_filter(img, sig_px, mode='nearest')
            img *= (px * px) / (APERTURES['slot'][0] * APERTURES['slot'][1]) * IMAGING_EFFICIENCY * self._focus_factor()
        self._rate_cache[key] = img
        return img

    def _point_intensity(self, owner, be_rep, window, pe):
        """Count rate (per slot-area equivalent) at a reported binding energy for one column type (spectral density x imaging window)."""
        saved = self.aperture
        o_t, g_t = self.scale_true['offset_ev'], self.scale_true['gain']
        o_c, g_c = self.scale_cal['offset_ev'], self.scale_cal['gain']
        be_true = ((be_rep * (1 + g_c) + o_c) - o_t) / (1 + g_t)
        grid = np.arange(be_true - 70.0, be_true + 5.0, 0.05)
        dens = np.zeros_like(grid)
        v = round(self.v_surface.get(owner[1], 0.0), 2) if owner[0] == 'sample' else 0.0
        g = math.hypot(XRAY_LINE_FWHM_EV, 0.02 * pe)
        k = self._k_inst() * self._flux_scale()
        for p in self._peaks_cached(owner):
            be_app = p['be'] + v
            if be_app < grid[0] or be_app > grid[-1]:
                continue
            prim = k * p['area'] * self._transmission(p['ke'] - v, pe) * line_profile(grid, be_app, p['width'], math.hypot(g, p['gmat']), p['alpha'])
            if p['kind'] in ('line', 'satellite'):
                prim = prim + inelastic_background(grid, prim, p['loss'])
            dens += prim
        self.aperture = saved
        m = (grid >= be_true - window / 2) & (grid <= be_true + window / 2)
        return float(dens[m].sum() * 0.05)

    # ---------------------------------------------------------------- sputtering, charging, vacuum
    def _etch_step(self, dt):
        """Remove material under the ion raster (one crater per raster position, layer by layer for the base and feature columns), book the sputtered
        atoms from the flux, grow mixing, oxide reduction and argon implantation of the altered layer."""
        if not (self.etching and self.ion_on and self.power) or self.stage_holder is None:
            return
        j = self._ion_current() / E_CHARGE / self.ion_raster ** 2                 # ions / m^2 / s
        half = self.ion_raster / 2
        kind, key, pos = self._analysed_target()
        targets = []                                                              # (sid, raster centre in the sample frame, overlap area)
        if kind == 'reference':
            if key != 'faraday_cup':
                targets.append(('ref:' + key, (pos[0], pos[1]), self._overlap(pos[0], pos[1], half, REF_RADIUS)))
        else:
            hx0, hy0 = self._analysed_holder_xy()
            for sid, (sx, sy) in self.holders[self.stage_holder]['samples'].items():
                area = self._overlap(hx0 - sx, hy0 - sy, half, self.samples[sid]['lateral_size_m'] / 2)
                if area > 0:
                    targets.append((sid, (hx0 - sx, hy0 - sy), area))
        R_T = 8.314462618 * self._room_k()
        for sid, (cx, cy), area in targets:
            s = self.samples[sid]
            cr = self._crater_for(s, cx, cy, half)
            cr['area_m2'] = area
            kfs = ['base'] + (['feature'] if s['pattern']['kind'] != 'uniform' else [])
            for kf in kfs:
                frac = self._feature_fraction(s) if kf == 'feature' else (1.0 - self._feature_fraction(s))
                left = dt
                # the contamination overlayer of the crater goes first
                if cr['recontam_m'] > 0:
                    y = self._yield(C_ADV)
                    n_at = atoms_per_m3(self._material(C_ADV))
                    rate = y * j / n_at
                    use = min(left, cr['recontam_m'] / rate) if rate > 0 else 0.0
                    removed_ov = rate * use
                    self.led['sputtered_atoms'].add(removed_ov * n_at * area * frac)
                    cr['overlayer_atoms_removed'] += removed_ov * n_at * area * frac
                    if kf == kfs[-1]:
                        cr['recontam_m'] = max(0.0, cr['recontam_m'] - removed_ov)
                    left -= use
                stack, substrate, _, _ = self._column(sid, kf == 'feature', s['craters'].index(cr))
                depth = cr['removed'][kf]
                top = 0.0
                for mat, t in stack + [(substrate, math.inf)]:
                    if left <= 0:
                        break
                    bottom = top + t
                    if depth >= bottom - 1e-18:
                        top = bottom
                        continue
                    n_at = atoms_per_m3(self._material(mat))
                    rate = self._yield(mat) * j / n_at                                # m/s
                    if rate <= 0:
                        break
                    use = min(left, (bottom - depth) / rate)
                    self.led['sputtered_atoms'].add(rate * use * n_at * area * frac)
                    depth = min(bottom, depth + rate * use) if bottom != math.inf else depth + rate * use
                    left -= use
                    top = bottom
                cr['removed'][kf] = depth
            cr['fluence'] += j * dt
            cr['mix_m'] = max(cr['mix_m'], MIX_LENGTH_1KEV_M * (self.ion_energy / 1000.0) ** 0.7)
            sat = 1.0 - math.exp(-cr['fluence'] / 1.0e19)                          # ions/m^2 to reach the steady altered layer (assumption)
            cr['reduction'] = max(cr['reduction'], OXIDE_REDUCTION_MAX * sat * (self.ion_energy / 4000.0) ** 0.3)
            new_imp = AR_IMPLANT_FRACTION * sat
            if new_imp > cr['implant']:
                host = atoms_per_m3(substance(s['substrate'])) * 2.0 * cr['mix_m'] * area
                d_imp = (new_imp - cr['implant']) * host / N_AVOGADRO                 # mol of argon taken from the chamber gas into the sample
                take = min(d_imp, self.gas['sac_ar'] * V_SAC_M3 / R_T)
                self.led['argon_implanted_mol'].add(take)
                self.gas['sac_ar'] -= take * R_T / V_SAC_M3
                self.p_sac = max(self.p_sac - take * R_T / V_SAC_M3, 0.0)
                cr['implant'] = cr['implant'] + (new_imp - cr['implant']) * (take / d_imp if d_imp > 0 else 0.0)

    @staticmethod
    def _overlap(cx, cy, half, r):
        """Area of the raster square (centre cx, cy, half-size) inside the sample square (half-size r, centred at 0)."""
        ox = max(0.0, min(cx + half, r) - max(cx - half, -r))
        oy = max(0.0, min(cy + half, r) - max(cy - half, -r))
        return ox * oy

    def _crater_for(self, s, cx, cy, half):
        for c in s['craters']:
            if abs(c['x'] - cx) < 1e-7 and abs(c['y'] - cy) < 1e-7 and abs(c['half'] - half) < 1e-9:
                return c
        ci = self._crater_at(s, cx, cy)
        if ci >= 0:                                                   # a new raster inside an existing crater continues from its state
            old = s['craters'][ci]
            c = dict(x=cx, y=cy, half=half, removed=dict(old['removed']), d0=dict(old['removed']), c0_m=old['c0_m'], recontam_m=old['recontam_m'],
                     fluence=old['fluence'], mix_m=old['mix_m'], reduction=old['reduction'], implant=old['implant'], overlayer_atoms_removed=0.0, area_m2=0.0)
        else:
            c = dict(x=cx, y=cy, half=half, removed=dict(base=0.0, feature=0.0), d0=dict(base=0.0, feature=0.0), c0_m=s['contamination_m'], recontam_m=0.0,
                     fluence=0.0, mix_m=0.0, reduction=0.0, implant=0.0, overlayer_atoms_removed=0.0, area_m2=0.0)
        s['craters'].append(c)
        return c

    def _removed_atoms_geometric(self):
        """Atoms removed from every crater, integrated over the layer stack between its initial and present depth (state based; compare with the
        flux-integrated sputtered_atoms ledger)."""
        tot = 0.0
        for sid, s in self.samples.items():
            for ci, c in enumerate(s['craters']):
                kfs = ['base'] + (['feature'] if s['pattern']['kind'] != 'uniform' else [])
                for kf in kfs:
                    frac = self._feature_fraction(s) if kf == 'feature' else (1.0 - self._feature_fraction(s))
                    stack, substrate, _, _ = self._column(sid, kf == 'feature', ci)
                    a, b = c['d0'][kf], c['removed'][kf]
                    top = 0.0
                    for mat, t in stack + [(substrate, math.inf)]:
                        bottom = top + t
                        lo, hi = max(a, top), min(b, bottom)
                        if hi > lo:
                            tot += (hi - lo) * atoms_per_m3(self._material(mat)) * c['area_m2'] * frac
                        top = bottom
                tot += c['overlayer_atoms_removed']
        return tot

    def _feature_fraction(self, s):
        p = s['pattern']
        if p['kind'] == 'uniform':
            return 0.0
        if p['kind'] == 'grid':
            f = p['width_m'] / p['pitch_m']
            return 1.0 - (1.0 - f) ** 2
        if p['kind'] == 'stripes':
            return p['width_m'] / p['pitch_m']
        return p['coverage']

    def _yield(self, mat):
        if mat == C_ADV:
            return 2.0 * sputter_yield(substance('graphite'), self.ion_energy)       # weakly bound contamination (assumption: twice graphite)
        return sputter_yield(substance(mat), self.ion_energy)

    def _charge_step(self, dt, target=None):
        """Surface potential of every sample on the stage: exact flow of C dV/dt = F(V) = I_ph,eff(V) - I_n,land(V) - V/R over the interval
        (the inputs are constant between events), with the charge ledger integrated along the same flow; any host chopping gives the same result."""
        if self.stage_holder is None or dt <= 0.0:
            return
        kind, key, _ = target if target is not None else self._analysed_target()
        cell = self._cell or {}
        phot = cell['phot'] if 'phot' in cell else (self._photon_rate() if self._xray_at_setpoint() or self.xray_on else 0.0)
        i_ph = ELECTRON_YIELD_PER_PHOTON * phot * E_CHARGE
        i_n = NEUT_CURRENT_PER_A * max(0.0, self.neut['filament_current_a'] - NEUT_FILAMENT_MIN_A) if self.neut_on and self.power else 0.0
        e_e = 0.55 * self.neut['charge_balance_v'] + 0.3 * self.neut['filament_bias_v']
        ids = list(self.holders[self.stage_holder]['samples'])
        if kind == 'reference':
            ids.append('ref:' + key)
        for sid in ids:
            under = (kind == 'sample' and key == sid) or (kind == 'reference' and sid == 'ref:' + key)
            ci = self._centre_crater() if under and not sid.startswith('ref:') else -1
            R = R_LEAK_OHM['conductor'] if sid.startswith('ref:') else cell.get('R', {}).get((sid, ci)) or self._leak_resistance(sid, ci)
            C = SAMPLE_CAPACITANCE_F
            v0 = self.v_surface.get(sid, 0.0)
            if under and (i_ph > 0.0 or i_n > 0.0):
                v1, q_ph, q_n, q_l = charge_flow(v0, dt, C, R, i_ph, i_n, e_e)
            else:                                                          # no currents: exact RC discharge
                v1 = v0 * math.exp(-dt / (R * C))
                q_ph, q_n, q_l = 0.0, 0.0, C * (v0 - v1)
            self.v_surface[sid] = v1
            self.led['charge_photoemitted_c'].add(q_ph)
            self.led['charge_neutraliser_c'].add(q_n)
            self.led['charge_leaked_c'].add(q_l)
            self._charge_c = getattr(self, '_charge_c', {})
            self._charge_c[sid] = C * v1

    def _photon_rate(self):
        return PHOTONS_PER_W * self._xray_power()

    def _vacuum_step(self, dt):
        """Lumped chambers, exact between events: V dp/dt = q + q_out exp(-t / tau_out) - S p (sample outgassing decays with one time constant),
        the backing pump hands the Flexi-lock over to the turbo pump at the exact crossover time, the vent valve relaxes it to atmosphere."""
        R_T = 8.314462618 * self._room_k()
        # analysis chamber: turbo + rotary with power; outgassing, leak fault, argon of the ion gun
        q_leak = LEAK_FAULT_PA_M3_S if 'sac_leak' in self.faults else 0.0
        q_ar = ION_GAS_PA_M3_S if (self.ion_on and self.power) else 0.0
        s = S_SAC_M3_S if self.power else 0.0
        self.p_sac = p_exact(self.p_sac, Q_SAC_OUTGAS + q_leak + q_ar, self._sample_outgas('stage'), s, V_SAC_M3, dt)
        n_pumped_ar = self._species(self.gas, 'sac_ar', q_ar, s, V_SAC_M3, dt, R_T)
        self.led['argon_admitted_mol'].add(q_ar * dt / R_T)
        self.led['argon_pumped_mol'].add(n_pumped_ar)
        n2 = self.gas['sac_n2']
        if s > 0 and n2 > 0:
            new = n2 * math.exp(-s / V_SAC_M3 * dt)
            self.led['nitrogen_pumped_mol'].add((n2 - new) * V_SAC_M3 / R_T)
            self.gas['sac_n2'] = new
        # Flexi-lock
        q_fl_leak = LEAK_FAULT_PA_M3_S if 'flexilock_leak' in self.faults else 0.0
        if self.lid_open:
            if self.gas['fl_n2'] > 0:                               # the Flexi-lock nitrogen escapes into the laboratory through the open lid
                self.led['nitrogen_released_mol'].add(self.gas['fl_n2'] * V_FL_M3 / R_T)
                self.gas['fl_n2'] = 0.0
            if self.gas.get('fl_ar', 0.0) > 0:
                self.led['argon_released_mol'].add(self.gas['fl_ar'] * V_FL_M3 / R_T)
                self.gas['fl_ar'] = 0.0
            self.p_fl = P_ATM
            return
        if self.fl_mode in ('venting', 'vented') and self.power:
            # the vent valve stays open to dry nitrogen at atmospheric pressure: exact exponential relaxation to P_ATM
            dp = (P_ATM - self.p_fl) * -math.expm1(-dt / VENT_TAU_S)
            self.p_fl += dp
            self.gas['fl_n2'] += dp
            self.led['nitrogen_admitted_mol'].add(dp * V_FL_M3 / R_T)
            if self.fl_mode == 'venting' and P_ATM - self.p_fl < 0.02 * P_ATM * (1 + 1e-9):
                self.fl_mode = 'vented'
                self._event('flexilock', 'FL_VENTED', 'Flexi-lock已到大气压', t=self.time_s + dt)
            return
        if self.fl_mode in ('pumping', 'pumped') and self.power:
            q0 = Q_FL_OUTGAS + q_fl_leak
            amp = self._sample_outgas('magazine')
            left = dt
            while left > 0.0:
                rough = self.p_fl > TURBO_CROSSOVER_PA
                s_fl = S_ROUGH_M3_S if rough else S_FL_M3_S
                seg = left
                p1 = p_exact(self.p_fl, q0, amp, s_fl, V_FL_M3, seg)
                if rough and p1 <= TURBO_CROSSOVER_PA:                 # hand-over to the turbo pump at the exact crossover time
                    seg = crossing_time(self.p_fl, q0, amp, s_fl, V_FL_M3, TURBO_CROSSOVER_PA, left)
                    p1 = TURBO_CROSSOVER_PA
                for gk, pk in (('fl_n2', 'nitrogen_pumped_mol'), ('fl_ar', 'argon_pumped_mol')):
                    g0 = self.gas.get(gk, 0.0)
                    if g0 > 0:
                        g1 = g0 * math.exp(-s_fl / V_FL_M3 * seg)
                        self.led[pk].add((g0 - g1) * V_FL_M3 / R_T)
                        self.gas[gk] = g1
                self.p_fl = p1
                amp *= math.exp(-seg / OUTGAS_TAU_S)
                left = left - seg if seg < left else 0.0
            if self.fl_mode == 'pumping' and self.p_fl < FL_TRANSFER_MAX_PA * (1 + 1e-9) and all(k in self._pump_cycle['imaged'] for k in (1, 2, 3)):
                self.fl_mode = 'pumped'
                self._event('flexilock', 'FL_PUMPED', 'Flexi-lock已抽到传送压力以下', t=self.time_s + dt)
            return
        # isolated (closed) or unpowered: the gas load accumulates
        self.p_fl = min(P_ATM, p_exact(self.p_fl, Q_FL_OUTGAS + q_fl_leak, self._sample_outgas('magazine'), 0.0, V_FL_M3, dt))

    @staticmethod
    def _species(gas, key, q_species, s, v, dt, R_T):
        """Partial pressure of one tracked species (constant admission, the chamber's pumping speed), exact; returns the moles pumped."""
        g0 = gas[key]
        if s <= 0:
            gas[key] = g0 + q_species / v * dt
            return 0.0
        g_eq = q_species / s
        g1 = g_eq + (g0 - g_eq) * math.exp(-s / v * dt)
        gas[key] = g1
        return (q_species * dt - (g1 - g0) * v) / R_T                 # mol pumped = admitted - accumulated

    def _sample_outgas(self, where):
        tot = 0.0
        hids = [self.stage_holder] if where == 'stage' else [h for h in self.magazine.values() if h]
        for hid in hids:
            if hid is None:
                continue
            for sid in self.holders[hid]['samples']:
                s = self.samples[sid]
                cls = substance(s['substrate'])['class_']
                rate = SAMPLE_OUTGAS_PA_M3_S.get(cls, 0.0) + (SAMPLE_OUTGAS_PA_M3_S['powder'] if s['form'] == 'powder_pellet' else 0.0)
                t_in = self.time_s - s.get('vacuum_since', self.time_s)
                tot += rate * math.exp(-t_in / OUTGAS_TAU_S)
        return tot

    # ---------------------------------------------------------------- Flexi-lock camera during the pump cycle
    def _camera_step(self):
        pc = getattr(self, '_pump_cycle', None)
        if self.fl_mode != 'pumping' or pc is None or self.p_fl > 1000.0:
            return
        for slot_ in (1, 2, 3):
            if slot_ in pc['imaged']:
                continue
            if self.time_s - pc.setdefault('cam_t0', self.time_s) < CAMERA_S * (len(pc['imaged']) - sum(1 for k in (1, 2, 3) if self.magazine[k] is None) + 1):
                return
            hid = self.magazine[slot_]
            pc['imaged'].add(slot_)
            if hid is None:
                continue
            rid = self._new_record('HI', 'holder_image')
            det = []
            for sid, (x, y) in sorted(self.holders[hid]['samples'].items()):
                s = self.samples[sid]
                det.append(dict(sample_id=sid, x_m=round(x + self.rng_mount.gauss(0.0, 5e-5), 5), y_m=round(y + self.rng_mount.gauss(0.0, 5e-5), 5),
                                size_m=round(s['lateral_size_m'] + self.rng_mount.gauss(0.0, 1e-4), 4)))
            rec = self.records[rid]
            rec.update(status='completed', finished_s=self.time_s, position_label=f'slot:{slot_}', settings=None,
                       image=dict(holder_id=hid, detected=det, pixel_size_m=2.0e-4, note_zh='样品架光学图像：识别到的样品轮廓中心与尺寸（±50 µm）'),
                       note_zh=f'抽气循环中拍摄的样品架{hid}光学图像')
            self.images[hid] = rid
            self._event('flexilock', 'HOLDER_IMAGED', f'样品架{hid}已拍照（{len(det)}个样品）', rid=rid)
            return

    # ---------------------------------------------------------------- data processing (ESCApe-like)
    def _rec(self, rid, kinds=('spectrum', 'calibration')):
        r = self.records.get(rid)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if r['kind'] not in kinds:
            raise ActionRejected('WRONG_RECORD_KIND')
        if r['status'] != 'completed':
            raise ActionRejected('RECORD_NOT_COMPLETED')
        return r

    @staticmethod
    def _region(rec, name):
        for s in rec['spectra']:
            if s['name'] == name:
                return s
        raise ActionRejected('UNKNOWN_REGION')

    @staticmethod
    def _shirley(be, y, iters=30):
        """Iterative Shirley background on a binding-energy axis (increasing BE): the background at E is proportional to the peak area at lower BE."""
        be = np.asarray(be, float)
        y = np.asarray(y, float)
        n = len(y)
        k = max(1, min(5, n // 10))
        lo, hi = float(np.mean(y[:k])), float(np.mean(y[-k:]))
        bg = np.full(n, lo)
        for _ in range(iters):
            net = np.clip(y - bg, 0.0, None)
            cum = np.concatenate([[0.0], np.cumsum(0.5 * (net[1:] + net[:-1]) * np.diff(be))])
            tot = cum[-1]
            new = lo + (hi - lo) * (cum / tot if tot > 0 else np.zeros(n))
            if np.max(np.abs(new - bg)) < 1e-6 * max(1.0, abs(hi)):
                bg = new
                break
            bg = new
        return bg

    def _sensitivity(self, el, line, pe, ke):
        """Instrument sensitivity factor (ESCApe-like library, theoretical): cross section x angular factor x IMFP ~ KE^0.7 x transmission."""
        e = element(el)
        lv = e['levels'].get(line)
        if lv is None or not lv['sigma_mb']:
            return None
        return lv['sigma_mb'] * angular_factor(lv['beta']) * (ke / 1000.0) ** 0.7 * (pe / 10.0) ** TRANSMISSION_PE_EXP * (ke / 1118.0) ** TRANSMISSION_KE_EXP

    def _assign(self, be_centre, tol=1.5, principal_only=False):
        """Candidate element lines within tolerance of a peak: the principal line with the chemical-state range of the library substances, and the
        other strong core levels (cross section >= 20 % of the principal line) shifted by the same range; principal lines rank first."""
        cands = []
        for el, e in library()['elements'].items():
            ref = e['reference_be_ev']
            if ref is None:
                continue
            pl = e['principal_line']
            states = [c['be_ev'] for s in library()['substances'] for c in s['components'] if c['element'] == el and c['line'] == pl]
            lo_s, hi_s = min(states + [ref]) - ref, max(states + [ref]) - ref
            sig0 = e['levels'][pl]['sigma_mb'] or 1e-9
            for lv, v in e['levels'].items():
                if v['be_ev'] <= 15.0 or not v['sigma_mb'] or (principal_only and lv != pl):
                    continue
                if lv != pl and v['sigma_mb'] < 0.03 * sig0:
                    continue
                base = v['be_ev'] + (ref - e['levels'][pl]['be_ev'])
                if base + lo_s - tol <= be_centre <= base + hi_s + tol:
                    cands.append((0 if lv == pl else 1, abs(be_centre - base), f'{el} {lv}'))
        return [c for _, _, c in sorted(cands)]

    def _cmd_data_identify(self, record_id, region, shift_ev):
        rec = self._rec(record_id)
        sp = self._region(rec, region)
        be = np.array(sp['binding_energy_ev']) + shift_ev
        y = np.array(sp['counts_per_second'])
        ys = np.convolve(y, np.ones(5) / 5.0, mode='same')
        base = np.convolve(ys, np.ones(61) / 61.0, mode='same')
        noise = np.sqrt(np.maximum(ys, 1.0) / (sp['dwell_s'] * sp['sweeps'] * 5))
        peaks = []
        for i in range(3, len(ys) - 3):
            if ys[i] >= ys[i - 1] and ys[i] >= ys[i + 1] and ys[i] - base[i] > 6 * noise[i] and ys[i] > 1.15 * base[i]:
                if peaks and be[i] - peaks[-1][0] < 3.0:
                    if ys[i] > peaks[-1][1]:
                        peaks[-1] = (be[i], ys[i])
                    continue
                peaks.append((be[i], ys[i]))
        tol = 1.5 + sp['step_ev']
        # pass 1: elements confirmed by a principal line (strongest peaks first); pass 2: every peak prefers a level of a confirmed element
        confirmed = []
        for b_, h in sorted(peaks, key=lambda p: -p[1]):
            if any(c.split()[0] in confirmed for c in self._assign(b_, tol)):
                continue                                            # explained by another level of an element already identified
            c = self._assign(b_, tol, principal_only=True)
            if c:
                el = c[0].split()[0]
                if el not in confirmed:
                    confirmed.append(el)
        out, elements = [], []
        for b_, h in peaks:
            allc = self._assign(b_, tol)
            known = [c for c in allc if c.split()[0] in confirmed]
            pick = known[0] if known else (self._assign(b_, tol, principal_only=True) or [None])[0]
            out.append(dict(binding_energy_ev=float(b_), height_cps=float(h), assignment=pick, candidates=(known + [c for c in allc if c not in known])[:8]))
            if pick and pick.split()[0] in confirmed and pick.split()[0] not in elements:
                elements.append(pick.split()[0])
        return dict(peaks=out[:200], elements=elements[:60])

    def _cmd_data_charge_correct(self, record_id, region, reference_be_ev):
        rec = self._rec(record_id)
        sp = self._region(rec, region)
        be = np.array(sp['binding_energy_ev'])
        y = np.array(sp['counts_per_second'])
        ys = np.convolve(y, np.ones(5) / 5.0, mode='same')
        bg = self._shirley(be, ys)
        net = ys - bg
        # the hydrocarbon component is the lowest-BE major maximum of the C 1s envelope
        thr = 0.5 * net.max()
        idx = [i for i in range(1, len(net) - 1) if net[i] >= net[i - 1] and net[i] >= net[i + 1] and net[i] >= thr]
        if not idx:
            raise ActionRejected('NO_PEAK')
        i = min(idx, key=lambda k: be[k])
        i = min(max(i, 1), len(net) - 2)
        y0, y1, y2 = net[i - 1], net[i], net[i + 1]
        den = y0 - 2 * y1 + y2
        d = 0.5 * (y0 - y2) / den if den != 0 else 0.0
        meas = float(be[i] + d * (be[1] - be[0]))
        return dict(shift_ev=round(reference_be_ev - meas, 4), measured_be_ev=round(meas, 4))

    def _cmd_data_quantify(self, record_id, regions, shift_ev):
        rec = self._rec(record_id)
        pe = rec['settings']['pass_energy_ev'] if rec.get('settings') else self.pe
        rows = []
        for name in regions:
            sp = self._region(rec, name)
            be = np.array(sp['binding_energy_ev']) + shift_ev
            y = np.array(sp['counts_per_second'])
            bg = self._shirley(be, y)
            area = float(np.sum(0.5 * ((y - bg)[1:] + (y - bg)[:-1]) * np.diff(be)))
            c = self._assign(float(be[int(np.argmax(y - bg))]), tol=2.0, principal_only=True) or self._assign(float(be[int(np.argmax(y - bg))]), tol=2.0)
            if not c:
                raise ActionRejected('UNASSIGNED_REGION')
            el, line = c[0].split()
            sens = self._sensitivity(el, line, pe, HV_EV - float(be[int(np.argmax(y - bg))]))
            # a spin-orbit j-component region also contains its partner when the splitting is small; quantify on the whole p/d/f subshell
            e = element(el)
            if '/' in line:
                partner = {'2p3/2': '2p1/2', '3d5/2': '3d3/2', '4f7/2': '4f5/2', '3p3/2': '3p1/2'}.get(line)
                if partner and partner in e['levels']:
                    so = e['levels'][partner]['be_ev'] - e['levels'][line]['be_ev']
                    if so < (be[-1] - be[0]) - 1.0:
                        sens += self._sensitivity(el, partner, pe, HV_EV - float(be[int(np.argmax(y - bg))]) - so) or 0.0
            rows.append(dict(region=name, element=el, line=line, area_cps_ev=max(area, 0.0), sensitivity=sens))
        tot = sum(r['area_cps_ev'] / r['sensitivity'] for r in rows if r['sensitivity'])
        for r in rows:
            r['atomic_percent'] = 100.0 * (r['area_cps_ev'] / r['sensitivity']) / tot if tot > 0 and r['sensitivity'] else 0.0
        return dict(results=rows)

    def _cmd_data_fit(self, record_id, region, components, shift_ev):
        from scipy.optimize import least_squares
        rec = self._rec(record_id)
        sp = self._region(rec, region)
        be = np.array(sp['binding_energy_ev']) + shift_ev
        y = np.array(sp['counts_per_second'])
        bg = self._shirley(be, y)
        net = y - bg
        t = sp['dwell_s'] * sp['sweeps']
        sig = np.sqrt(np.maximum(y * t, 1.0)) / t
        n = len(components)
        x0, lo, hi = [], [], []
        for c in components:
            a0 = max(float(np.interp(c['be_ev'], be, net)), 1.0) * c['fwhm_ev']
            x0 += [c['be_ev'], c['fwhm_ev'], a0]
            lo += [c['be_ev'] - (1e-6 if c['fix_position'] else 2.0), 0.2, 0.0]
            hi += [c['be_ev'] + (1e-6 if c['fix_position'] else 2.0), 5.0, np.inf]

        def model(p):
            m = np.zeros_like(be)
            for k in range(n):
                c, w, a = p[3 * k:3 * k + 3]
                s_ = w / 2.3548 * 0.8
                g_ = w / 2 * 0.3
                m += a * voigt_profile(be - c, s_, g_) / max(voigt_profile(0.0, s_, g_), 1e-12) / w
            return m

        res = least_squares(lambda p: (model(p) - net) / sig, x0, bounds=(lo, hi), max_nfev=400)
        p = res.x
        areas = []
        for k in range(n):
            c, w, a = p[3 * k:3 * k + 3]
            s_ = w / 2.3548 * 0.8
            g_ = w / 2 * 0.3
            areas.append(float(a / max(voigt_profile(0.0, s_, g_), 1e-12) / w))
        tot = sum(areas) or 1.0
        out = [dict(label=components[k]['label'], be_ev=round(float(p[3 * k]), 4), fwhm_ev=round(float(p[3 * k + 1]), 4), area_cps_ev=areas[k],
                    percent=100.0 * areas[k] / tot) for k in range(n)]
        rms = float(np.sqrt(np.mean(res.fun ** 2)))
        return dict(components=out, residual_rms=rms)

    def _cmd_data_depth_profile(self, record_id, shift_ev):
        rec = self._rec(record_id, kinds=('depth_profile',))
        if not rec['levels']:
            raise ActionRejected('RECORD_NOT_COMPLETED')
        names = [s['name'] for s in rec['levels'][0]['spectra']]
        times, table, els = [], [], None
        for lev in rec['levels']:
            q = self._quantify_spectra(lev['spectra'], names, shift_ev, rec['settings']['pass_energy_ev'])
            if els is None:
                els = [r['element'] for r in q]
            times.append(lev['etch_time_s'])
            table.append([r['atomic_percent'] for r in q])
        return dict(etch_time_s=times, elements=els, atomic_percent=table)

    def _quantify_spectra(self, spectra, names, shift, pe):
        fake = dict(spectra=spectra, settings=dict(pass_energy_ev=pe), kind='spectrum', status='completed')
        saved = self.records.get('_tmp')
        self.records['_tmp'] = fake
        try:
            return self._cmd_data_quantify('_tmp', names, shift)['results']
        finally:
            del self.records['_tmp']
            if saved is not None:
                self.records['_tmp'] = saved

    # ---------------------------------------------------------------- records, events, ledger
    def _cmd_records_list(self):
        return dict(records=sorted(k for k in self.records if not k.startswith('_')), calibrations=sorted(self.calibrations))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def ledger(self):
        v = {k: self.led[k].value for k in LEDGER_FLOWS}
        R_T = 8.314462618 * self._room_k()
        geo = self._removed_atoms_geometric()
        ar_in = (self.gas['sac_ar'] * V_SAC_M3 + self.gas.get('fl_ar', 0.0) * V_FL_M3) / R_T
        n2_in = (self.gas['sac_n2'] * V_SAC_M3 + self.gas['fl_n2'] * V_FL_M3) / R_T
        stored = sum(getattr(self, '_charge_c', {}).values())
        out = dict(photons_on_sample=v['photons_on_sample'], sputtered_atoms=v['sputtered_atoms'], sputter_removed_atoms_geometric=geo,
                   sputter_residual_atoms=v['sputtered_atoms'] - geo,
                   argon_admitted_mol=v['argon_admitted_mol'], argon_pumped_mol=v['argon_pumped_mol'], argon_implanted_mol=v['argon_implanted_mol'],
                   argon_released_mol=v['argon_released_mol'], argon_in_chambers_mol=ar_in,
                   argon_residual_mol=v['argon_admitted_mol'] - v['argon_pumped_mol'] - v['argon_implanted_mol'] - v['argon_released_mol'] - ar_in,
                   nitrogen_admitted_mol=v['nitrogen_admitted_mol'], nitrogen_pumped_mol=v['nitrogen_pumped_mol'], nitrogen_released_mol=v['nitrogen_released_mol'],
                   nitrogen_in_chambers_mol=n2_in, nitrogen_residual_mol=v['nitrogen_admitted_mol'] - v['nitrogen_pumped_mol'] - v['nitrogen_released_mol'] - n2_in,
                   charge_photoemitted_c=v['charge_photoemitted_c'], charge_neutraliser_c=v['charge_neutraliser_c'], charge_leaked_c=v['charge_leaked_c'],
                   charge_stored_c=stored, charge_residual_c=v['charge_photoemitted_c'] - v['charge_neutraliser_c'] - v['charge_leaked_c'] - stored,
                   anode_energy_j=v['anode_energy_j'], xray_on_time_s=v['xray_on_time_s'])
        return {k: float(x) for k, x in out.items()}

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        return dict(library=LIBRARY_REL, substances=sorted(self.used_substances))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.time_s)

    # ---------------------------------------------------------------- clock
    def step(self, dt_s):
        validate(number('s', 0.0, 86400.0), dt_s)
        raw = dt_s * 1e9 + self._clock_remainder_ns
        ns = round(raw)
        self._clock_remainder_ns = raw - ns
        if ns <= 0 or self.plant is None:
            if ns > 0:
                self.time_ns += ns
                self.revision += 1
            return
        end = self.time_ns + ns
        while self.time_ns < end:
            nxt = min(end, (self.time_ns // CYCLE_NS + 1) * CYCLE_NS)
            self._integrate(self.time_ns, nxt)
            self.time_ns = nxt
            if nxt % CYCLE_NS == 0:
                self._tick()
        self.revision += 1

    def _next_event_dt(self, t):
        cands = []
        if self.power:
            for k in self.stage:
                if abs(self.stage[k] - self.stage_target[k]) > 1e-12:
                    sp = TILT_SPEED_RAD_S if k == 'tilt' else ROT_SPEED_RAD_S if k == 'rotation' else STAGE_SPEED_M_S
                    cands.append(abs(self.stage[k] - self.stage_target[k]) / sp)
            if abs(self.mag_z - self.mag_target) > 1e-12:
                cands.append(abs(self.mag_z - self.mag_target) / MAGAZINE_SPEED_M_S)
            if abs(self.arm - self.arm_target) > 1e-12:
                cands.append(abs(self.arm - self.arm_target) / TRANSFER_SPEED_M_S)
            if abs(self.gate - self.gate_target) > 1e-12:
                cands.append(abs(self.gate - self.gate_target) / GATE_SPEED_M_S)
            if self.xray_on:
                if abs(self.hv - ANODE_HV_V) > 1e-9:
                    cands.append(abs(ANODE_HV_V - self.hv) / HV_RAMP_V_S)
                elif abs(self.emission - self.emission_set) > 1e-12:
                    cands.append(abs(self.emission_set - self.emission) / EMISSION_RAMP_A_S)
        if self.power and (self.xray_on or self.neut_on) and self.stage_holder is not None and self._stage_moving():
            cands.append(self._target_change_dt())            # photoemission or neutraliser current on the analysed object
        if self._grid_active():
            cands.append((math.floor(t / CELL_S + 1e-9) + 1) * CELL_S - t)
        if self.power and not self.lid_open:                 # Flexi-lock state changes at their exact pressure crossings
            if self.fl_mode == 'venting' and P_ATM - self.p_fl > 0.02 * P_ATM:
                cands.append(VENT_TAU_S * math.log((P_ATM - self.p_fl) / (0.02 * P_ATM)))
            elif self.fl_mode == 'pumping':
                q0 = Q_FL_OUTGAS + (LEAK_FAULT_PA_M3_S if 'flexilock_leak' in self.faults else 0.0)
                amp = self._sample_outgas('magazine')
                if self.p_fl > TURBO_CROSSOVER_PA:
                    if p_exact(self.p_fl, q0, amp, S_ROUGH_M3_S, V_FL_M3, CYCLE_S) <= TURBO_CROSSOVER_PA:
                        cands.append(crossing_time(self.p_fl, q0, amp, S_ROUGH_M3_S, V_FL_M3, TURBO_CROSSOVER_PA, CYCLE_S))
                elif self.p_fl > FL_TRANSFER_MAX_PA and getattr(self, '_pump_cycle', None) and all(k in self._pump_cycle['imaged'] for k in (1, 2, 3)):
                    if p_exact(self.p_fl, q0, amp, S_FL_M3_S, V_FL_M3, CYCLE_S) <= FL_TRANSFER_MAX_PA:
                        cands.append(crossing_time(self.p_fl, q0, amp, S_FL_M3_S, V_FL_M3, FL_TRANSFER_MAX_PA, CYCLE_S))
        a = self.acq
        if a is not None:
            if a['kind'] in ('spectrum', 'depth_profile') and a.get('regions') and (a['kind'] != 'depth_profile' or a['phase'] == 'acquire') \
                    and a['index'] < len(a['regions']):
                cands.append(a['region_t0'] + a['regions'][a['index']]['duration'] - t)
            elif a['kind'] == 'depth_profile' and a['phase'] == 'etch':
                cands.append(a['etch_t0'] + a['etch_s'] - t)
            elif a['kind'] in ('image', 'etch'):
                cands.append(a['t0'] + a['duration_s'] - t)
            elif a['kind'] == 'auto_height':
                cands.append(a['t0'] + AUTO_HEIGHT_S - t)
            elif a['kind'] == 'calibration':
                if a.get('sub') == 'etch':
                    cands.append(a['phase_t0'] + 30.0 - t)
                elif a.get('sub') == 'measure' and a['regions']:
                    cands.append(a['region_t0'] + a['regions'][0]['duration'] - t)
        return max(0.0, min(cands)) if cands else math.inf

    def _record_cell(self):
        """Inputs held until the next grid boundary, event or command while they change continuously: the photon rate on the sample (anode
        ramps), the leak resistances of the samples on the stage (sputtering thins insulating films) and the surface potential of the analysed
        sample (count rates during charging transients).  Recorded only at CELL_S grid boundaries, events and commands, never at host step ends,
        so any host chopping integrates the same piecewise-constant inputs."""
        kind, key, _ = self._analysed_target()
        R = {}
        if self.stage_holder is not None:
            ci = self._centre_crater() if kind == 'sample' else -1
            for sid in self.holders[self.stage_holder]['samples']:
                c_ = ci if (kind == 'sample' and key == sid) else -1
                R[(sid, c_)] = self._leak_resistance(sid, c_)
        self._cell = dict(t=self.time_s, phot=self._photon_rate() if self._xray_at_setpoint() or self.xray_on else 0.0, R=R,
                          sid=key if kind == 'sample' else None, v=self.v_surface.get(key, 0.0) if kind == 'sample' else 0.0)

    def _counting(self):
        a = self.acq
        if a is None:
            return False
        if a['kind'] == 'image':
            return True
        return a['kind'] in ('spectrum', 'depth_profile', 'calibration') and bool(a.get('regions')) and a.get('sub', 'measure') == 'measure' and \
            (a['kind'] != 'depth_profile' or a['phase'] == 'acquire') and a['index'] < len(a['regions'])

    def _analysed_transient(self):
        """The analysed sample's surface potential is still relaxing (it snaps exactly onto its equilibrium at the end of the flow)."""
        kind, key, _ = self._analysed_target()
        if kind != 'sample':
            return False
        cell = self._cell or {}
        phot = cell.get('phot', 0.0)
        i_ph = ELECTRON_YIELD_PER_PHOTON * phot * E_CHARGE
        i_n = NEUT_CURRENT_PER_A * max(0.0, self.neut['filament_current_a'] - NEUT_FILAMENT_MIN_A) if self.neut_on and self.power else 0.0
        e_e = 0.55 * self.neut['charge_balance_v'] + 0.3 * self.neut['filament_bias_v']
        ci = self._centre_crater()
        R = cell.get('R', {}).get((key, ci)) or self._leak_resistance(key, ci)
        v = self.v_surface.get(key, 0.0)
        if i_ph <= 0.0 and i_n <= 0.0:
            return v != 0.0
        return v != charge_equilibrium(R, i_ph, i_n, e_e)

    def _grid_active(self):
        if not self.power or self.stage_holder is None:
            return False
        if self.etching:
            return True
        if self.xray_on and (abs(self.hv - ANODE_HV_V) > 1e-9 or abs(self.emission - self.emission_set) > 1e-12):
            return True
        return self._counting() and self._analysed_transient()

    def _target_change_dt(self):
        """Time until the object under the analysis point changes while the stage moves (sampled every 0.25 mm of travel, refined by bisection to
        ~1 ns), so charging and photon bookkeeping are cut exactly where an edge passes the analysis point."""
        start = dict(self.stage)
        speed = {k: TILT_SPEED_RAD_S if k == 'tilt' else ROT_SPEED_RAD_S if k == 'rotation' else STAGE_SPEED_M_S for k in start}
        horizon = max(abs(self.stage_target[k] - start[k]) / speed[k] for k in start)
        if horizon <= 0.0:
            return math.inf

        def target(tau):
            self.stage = {k: self._toward(start[k], self.stage_target[k], speed[k] * tau) for k in start}
            try:
                kind, key, _ = self._analysed_target()
            finally:
                self.stage = start
            return (kind, key)
        t0 = target(0.0)
        dist = abs(self.stage_target['x'] - start['x']) + abs(self.stage_target['y'] - start['y']) + 0.05 * abs(self.stage_target['rotation'] - start['rotation'])
        n = int(min(2000, max(4, math.ceil(dist / 2.5e-4))))
        prev = 0.0
        for i in range(1, n + 1):
            tau = horizon * i / n
            if target(tau) != t0:
                lo, hi = prev, tau
                for _ in range(80):
                    mid = 0.5 * (lo + hi)
                    if target(mid) == t0:
                        lo = mid
                    else:
                        hi = mid
                    if hi - lo <= 1e-9:
                        break
                return hi
            prev = tau
        return math.inf

    def _handle_events(self):
        for _ in range(8):                                   # phases whose motion has zero length advance at the same instant
            if self.transfer_op is None:
                break
            ph = self.transfer_op['phase']
            self._transfer_next()
            if self.transfer_op is None or self.transfer_op['phase'] == ph:
                break
        if self.acq is not None:
            self._flush()
            self._acq_advance()

    def _integrate(self, ns0, ns1):
        """Continuous motions, ramps, vacuum, sputtering and charging between the 1 s cycles, cut at every event time (exact for any chopping)."""
        t, t_end = ns0 / 1e9, ns1 / 1e9
        guard = 0
        first = True
        while guard < 1000000:
            guard += 1
            self._t_now = t
            self._handle_events()
            at_grid = abs(t / CELL_S - round(t / CELL_S)) < 1e-9
            if at_grid or (not first and t < t_end - 1e-12) or self._cell is None:
                self._record_cell()                           # grid boundaries and events only, never a host step end
            first = False
            if t >= t_end - 1e-12:
                break
            dt = min(t_end - t, self._next_event_dt(t))
            if dt <= 0.0:
                dt = min(t_end - t, 1e-9)
            self._continuous(dt)
            t = t + dt
        self._t_now = None

    @staticmethod
    def _toward(x, target, step):
        return target if abs(target - x) <= step + 1e-15 else x + math.copysign(step, target - x)

    def _continuous(self, dt):
        target0 = self._analysed_target()                  # constant over the interval (cuts where it changes, _target_change_dt)
        if self.power:
            moved = False
            for k in self.stage:
                sp = TILT_SPEED_RAD_S if k == 'tilt' else ROT_SPEED_RAD_S if k == 'rotation' else STAGE_SPEED_M_S
                x0 = self.stage[k]
                self.stage[k] = self._toward(self.stage[k], self.stage_target[k], sp * dt)
                moved = moved or x0 != self.stage[k]
            if moved:
                self._rate_cache.clear()
            self.mag_z = self._toward(self.mag_z, self.mag_target, MAGAZINE_SPEED_M_S * dt)
            self.arm = self._toward(self.arm, self.arm_target, TRANSFER_SPEED_M_S * dt)
            self.gate = self._toward(self.gate, self.gate_target, GATE_SPEED_M_S * dt)
            if self.xray_on:
                v0, i0 = self.hv, self.emission
                if abs(self.hv - ANODE_HV_V) > 1e-9:
                    self.hv = self._toward(self.hv, ANODE_HV_V, HV_RAMP_V_S * dt)
                else:
                    self.emission = self._toward(self.emission, self.emission_set, EMISSION_RAMP_A_S * dt)
                av, ai = (self.hv - v0) / dt, (self.emission - i0) / dt
                self.led['anode_energy_j'].add(v0 * i0 * dt + (v0 * ai + i0 * av) * dt * dt / 2.0 + av * ai * dt ** 3 / 3.0)
                self.led['xray_on_time_s'].add(dt)
                if self.stage_holder is not None and target0[0] != 'none':
                    self.led['photons_on_sample'].add(PHOTONS_PER_W * (v0 * i0 + (v0 * ai + i0 * av) * dt / 2.0) * dt)
                self._rate_cache.clear() if abs(self.hv - v0) + abs(self.emission - i0) > 0 else None
        else:
            self.gate = self._toward(self.gate, self.gate_target, GATE_SPEED_M_S * dt)
        self._vacuum_step(dt)
        self._etch_step(dt)
        self._charge_step(dt, target0)

    def _tick(self):
        self._flush()
        if not self.power:
            self.readings = None
            return
        t = self.time_s
        if self.state == 'booting' and t - self.state_t0 >= BOOT_S - 1e-9:
            self.state = 'ready'
            self._event('power', 'SOFTWARE_READY', 'ESCApe已启动，系统就绪')
        if self.xray_on and not self._chiller_ok():
            self._xray_trip('COOLING_WATER_LOW', '阳极冷却水流量不足：X射线关闭')
            self.error = 'COOLING_WATER_LOW'
        if self.xray_on and self.p_sac > XRAY_MAX_PA:
            self._xray_trip('VACUUM_INTERLOCK', '分析室压力超过X射线源联锁值：X射线关闭')
        if self.xray_on and 'xray_filament_failure' in self.faults:
            self._xray_trip('FILAMENT_FAILURE', 'X射线源灯丝故障：X射线关闭')
        if self.neut_on and 'neutraliser_filament_failure' in self.faults:
            self.neut_on = False
            self._event('error', 'NEUTRALISER_FILAMENT_FAILURE', '中和器灯丝故障：中和器关闭')
        self._camera_step()
        if self.fl_mode == 'pumping' and not self.lid_open and self.p_fl < FL_TRANSFER_MAX_PA * (1 + 1e-9) and all(k in self._pump_cycle['imaged'] for k in (1, 2, 3)):
            self.fl_mode = 'pumped'                               # the last holder photograph completes the cycle below the transfer pressure
            self._event('flexilock', 'FL_PUMPED', 'Flexi-lock已抽到传送压力以下')
        z = [self.rng.gauss(0.0, 1.0) for _ in range(8)]
        rate = self._count_rate_now()
        self.readings = dict(p_sac=max(1e-9, self.p_sac * (1.0 + 0.05 * z[0])), p_fl=max(1e-9, self.p_fl * (1.0 + 0.05 * z[1])),
                             power=round(self._xray_power(), 0) if self.xray_on else 0.0,
                             x=round(self.stage['x'] + self.stage_err['x'], 6), y=round(self.stage['y'] + self.stage_err['y'], 6),
                             z=round(self.stage['z'] + self.stage_err['z'], 6), tilt=round(self.stage['tilt'] / math.radians(0.01)) * math.radians(0.01),
                             rotation=round(self.stage['rotation'] / math.radians(0.01)) * math.radians(0.01),
                             rate=float(round(max(0.0, rate + math.sqrt(max(rate, 0.0)) * z[2]))))


_LINES_CACHE = {}
_K_INST = [None]
PHOTONS_PER_W = 1.5e8                                   # photons/s on the sample per watt of anode power (monochromated; assumption)
IMAGING_EFFICIENCY = 0.10                               # parallel-imaging transmission relative to slot spectroscopy (assumption)
AG_MODEL_CPS = 1.05 * AG_SPEC_CPS                       # the simulated instrument exceeds the acceptance minimum by 5 %


def _calibrate_k_inst():
    """Instrument sensitivity constant: clean Ag, slot, hybrid lens, PE 10 eV, 600 W -> Ag 3d5/2 peak height = 1.05 x the 760 kcps acceptance value
    (the model's line shape then gives the FWHM checked by the validation)."""
    _K_INST[0] = 1.0
    rt = KratosAxissupraRuntime('k_cal')
    rt._cmd_plant_configure(295.15, MAINS_V, 1)
    rt.scale_true = dict(offset_ev=0.0, gain=0.0)
    rt.power, rt.state = True, 'ready'
    rt.stage_holder = 'H1'
    rt.holders['H1']['location'] = 'stage'
    s = rt.samples['ref:Ag']
    s['contamination_m'] = 0.0
    rt.stage = dict(x=-REF_BLOCK_X, y=-REF_Y['Ag'], z=0.0, tilt=0.0, rotation=0.0)
    rt.stage_target = dict(rt.stage)
    rt.xray_on, rt.hv, rt.emission, rt.emission_set = True, ANODE_HV_V, SPEC_POWER_W / ANODE_HV_V, SPEC_POWER_W / ANODE_HV_V
    rt.lens_mode, rt.aperture, rt.pe = 'hybrid', 'slot', 10.0
    axis = np.arange(366.3, 370.3, 0.01)
    r = rt._spectrum_model(axis, 0.01)
    r = r * (1.0 + DEAD_TIME_S * r)                    # height before dead-time loss
    _K_INST[0] = AG_MODEL_CPS / float(r.max() - DARK_CPS_PER_EV * SNAPSHOT_WINDOW_PER_PE * 10.0)
    return _K_INST[0]


# ---------------------------------------------------------------- host session: identity filter, request binding, ownership
ASYNC = {'transfer.to_stage', 'transfer.to_magazine', 'transfer.recover', 'stage.auto_height', 'ion_gun.etch', 'acquisition.spectrum', 'acquisition.image', 'depth_profile.start',
         'calibration.energy_scale'}
_DATA_GUARDED = {'acquisition.result'}
_HIDDEN_EVENT_CODES = set()
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill', 'configuration') and k not in UNSUPPORTED}
_RECORD_ARG = {'acquisition.result': 'record_id'}
_ANALYSIS_RECORD_ARGS = {'data.identify': ['record_id'], 'data.charge_correct': ['record_id'], 'data.quantify': ['record_id'], 'data.fit': ['record_id'],
                         'data.depth_profile': ['record_id']}


class KratosAxissupraSession:
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
        meta = copy.deepcopy(export_kratos_axissupra_contract_metadata())
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

    def _foreign_acq(self):
        h = self._host
        return (h.acq is not None and self._foreign(h.acq['op'])) or (h.transfer_op is not None and self._foreign(h.transfer_op['op']))

    def _own_last(self):
        h = self._host
        done = [k for k, r in h.records.items() if r['status'] == 'completed' and h._owners.get(k) == self._principal]
        return max(done, key=lambda k: (h.records[k]['finished_s'] or 0.0, k), default=None)

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        foreign = self._role == 'agent' and self._foreign_acq()
        values['acquisition.started_by_other_agent'] = foreign
        if foreign:
            values['acquisition.id'] = None
        values['result.last_record_id'] = self._own_last()
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=(h.time_ns // CYCLE_NS) * CYCLE_S if k in _SAMPLING_READINGS else h.time_s,
                                          sample_window_s=CYCLE_S if k in _SAMPLING_READINGS else None) for k in ids})

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        h = self._host
        if capability_id in _CONTROL_GUARDED and self._foreign_acq():
            if capability_id == 'power.set' and not h.power:
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
            if capability_id == 'records.list' and self._role == 'agent':
                result = dict(records=[r for r in result['records'] if not self._foreign(r)], calibrations=result['calibrations'])
            if capability_id == 'events.read' and self._role == 'agent':         # no injected faults for agents
                result = dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in h.events
                                      if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_rid')) and e['kind'] != 'fault'
                                      and e['code'] not in _HIDDEN_EVENT_CODES], dropped=h.events_dropped)
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
        res = h._out(h.records[op['record_id']]) if op.get('record_id') in h.records else dict(phase=op['phase'], reason=op.get('reason'))
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
            h._flush()
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
    'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_OFF': '主机未通电', 'NOT_READY': '系统启动中', 'ERROR_PENDING': '有未确认的错误',
    'MAINS_OUT_OF_RANGE': '供电电压超出AC 200 V±10%', 'CAUSE_PERSISTS': '错误原因仍存在', 'DUPLICATE_SAMPLE': '样品ID已存在',
    'TOO_MANY_SAMPLES': '样品数已达上限40', 'UNKNOWN_TEMPLATE': '模板库中没有该物质', 'INVALID_PATTERN': '图案参数不完整或不一致',
    'GATE_OPEN': '闸板阀未关闭', 'LID_OPEN': 'Flexi-lock盖打开', 'NOT_VENTED': 'Flexi-lock不在大气压，盖不能打开', 'LID_CLOSED': 'Flexi-lock盖未打开',
    'HOLDER_NOT_ON_BENCH': '样品架不在实验台上', 'UNKNOWN_SAMPLE': '样品不存在或不在该样品架上', 'SAMPLE_NOT_ON_BENCH': '样品不在实验台上',
    'OUTSIDE_HOLDER': '样品超出样品架75×32 mm范围', 'SAMPLE_TOO_THICK': '样品厚度超出样品架允许值（双高样品架19 mm，厂家手册；组合样品架7 mm，取自相关型号AXIS Supra的Lakehead设施页与Illinois Supra+设施页，对组合样品架的映射为工程假设）',
    'POSITION_OCCUPIED': '与已装样品重叠', 'SLOT_OCCUPIED': '仓槽已有样品架', 'SLOT_EMPTY': '仓槽为空', 'TRANSFER_RUNNING': '传送进行中',
    'ACQUISITION_RUNNING': '有操作（采集、溅射、标定、自动高度）进行中', 'ION_BEAM_ON': '离子束正在溅射', 'STAGE_OCCUPIED': '样品台上已有样品架',
    'STAGE_EMPTY': '样品台上没有样品架', 'FLEXILOCK_NOT_PUMPED': 'Flexi-lock未抽到传送压力（或盖打开）', 'OUT_OF_RANGE': '超出样品台行程',
    'COLLISION_RISK': '样品表面将高于分析面1 mm以上（会撞透镜）', 'NOTHING_UNDER_ANALYSER': '分析位下没有样品或参考样品',
    'VACUUM_NOT_READY': '分析室压力高于X射线源联锁值', 'COOLING_WATER_LOW': '冷却水流量不足', 'FILAMENT_FAILURE': '灯丝故障',
    'XRAY_NOT_READY': 'X射线源未开启或未到设定功率', 'ION_GUN_OFF': '离子枪未待机', 'MOTION_IN_PROGRESS': '样品台在运动',
    'NOT_ON_FARADAY_CUP': '样品台不在法拉第杯位置', 'RECORDS_FULL': '记录数已达上限200', 'DUPLICATE_REGION': '区域名重复', 'INVALID_REGION': '区域能量范围无效或点数过多',
    'REGION_EXCEEDS_SNAPSHOT_WINDOW': '快照模式的区域宽度超过探测器能量窗（0.18×通能）', 'UNKNOWN_RECORD': '记录不存在', 'WRONG_RECORD_KIND': '记录种类不适用',
    'RECORD_NOT_COMPLETED': '记录未完成', 'UNKNOWN_REGION': '记录中没有该区域', 'NO_PEAK': '区域内找不到峰', 'UNASSIGNED_REGION': '区域的峰无法归属元素谱线',
    'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作已结束', 'USER_CANCEL': '用户取消', 'XRAY_OFF': 'X射线源关闭', 'TRANSFER_INTERRUPTED': '传送中断：传送叉停在闸板阀通道中（样品架可能在传送叉上）；用transfer.recover完成传送', 'NO_INTERRUPTED_TRANSFER': '没有被中断、需要恢复的传送',
    'MAINS_FAULT': '供电故障', 'VACUUM_INTERLOCK': '真空联锁',
}


# ---------------------------------------------------------------- contract metadata
# immediate effects of an accepted call and the slots whose evolution over the following step differs (written by the effect-discovery script over
# the scenario states of tests/test_asset_kratos_axissupra.py, noise-free and noisy runs)
EFFECTS = {
    'plant.configure': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'chiller.flow_ok', 'detector.count_rate_cps',
        'flexilock.mode', 'ion_gun.beam_on', 'ion_gun.on', 'neutraliser.on', 'panel.ion_gun_lamp', 'panel.power_lamp', 'panel.xray_lamp', 'plant.configured',
        'power.enabled', 'stage.moving', 'stage.rotation_indicated_rad', 'stage.tilt_indicated_rad', 'stage.x_indicated_m', 'stage.y_indicated_m', 'stage.z_indicated_m',
        'system.error_code', 'system.state', 'transfer.busy', 'truth.ion_current_a', 'truth.samples', 'truth.scale_error', 'truth.xray_power_w',
        'vacuum.fl_log10_pressure_mbar', 'vacuum.fl_pressure_indicated_pa', 'vacuum.sac_log10_pressure_mbar', 'vacuum.sac_pressure_indicated_pa', 'xray.on',
        'xray.power_indicated_w'],
    'sample.provide': ['sample.bench_ids', 'truth.samples'],
    'fault.inject': ['chiller.flow_ok', 'truth.faults'],
    'power.set': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'chiller.flow_ok', 'detector.count_rate_cps',
        'flexilock.mode', 'ion_gun.beam_on', 'ion_gun.on', 'neutraliser.on', 'panel.ion_gun_lamp', 'panel.power_lamp', 'panel.xray_lamp', 'power.enabled',
        'stage.moving', 'stage.rotation_indicated_rad', 'stage.tilt_indicated_rad', 'stage.x_indicated_m', 'stage.y_indicated_m', 'stage.z_indicated_m',
        'system.error_code', 'system.state', 'transfer.busy', 'truth.ion_current_a', 'truth.xray_power_w', 'vacuum.fl_log10_pressure_mbar',
        'vacuum.fl_pressure_indicated_pa', 'vacuum.sac_log10_pressure_mbar', 'vacuum.sac_pressure_indicated_pa', 'xray.on', 'xray.power_indicated_w'],
    'system.acknowledge': ['system.error_code', 'xray.fault_code'],
    'flexilock.vent': ['flexilock.mode'],
    'flexilock.pump': ['flexilock.mode'],
    'flexilock.set_lid': ['flexilock.lid_angle_rad', 'flexilock.lid_open', 'flexilock.mode'],
    'holder.mount_sample': ['holders', 'sample.bench_ids', 'truth.samples'],
    'holder.unmount_sample': ['holders', 'sample.bench_ids', 'truth.samples'],
    'flexilock.load_holder': ['flexilock.magazine', 'holders'],
    'flexilock.unload_holder': ['flexilock.magazine', 'holders'],
    'transfer.to_stage': ['acquisition.started_by_other_agent', 'stage.moving', 'stage.position_label', 'transfer.busy'],
    'transfer.to_magazine': ['acquisition.started_by_other_agent', 'stage.moving', 'stage.position_label', 'transfer.busy'],
    'transfer.recover': ['acquisition.started_by_other_agent', 'transfer.busy'],
    'stage.move': ['stage.moving', 'stage.position_label'],
    'stage.go_to': ['stage.moving', 'stage.position_label'],
    'stage.auto_height': ['acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent'],
    'xray.set_source': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'ion_gun.beam_on', 'panel.ion_gun_lamp',
        'panel.xray_lamp', 'truth.ion_current_a', 'truth.xray_power_w', 'xray.emission_setpoint_a', 'xray.fault_code', 'xray.on'],
    'analyser.configure': ['analyser.aperture', 'analyser.lens_mode', 'analyser.pass_energy_ev'],
    'neutraliser.set': ['neutraliser.on', 'neutraliser.settings'],
    'ion_gun.set': ['ion_gun.energy_ev', 'ion_gun.on', 'ion_gun.raster_m'],
    'ion_gun.etch': ['acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent', 'ion_gun.beam_on',
        'panel.ion_gun_lamp', 'truth.ion_current_a'],
    'ion_gun.measure_current': [],
    'acquisition.spectrum': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent'],
    'acquisition.image': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent'],
    'depth_profile.start': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent'],
    'calibration.energy_scale': ['acquisition.id', 'acquisition.kind', 'acquisition.started_by_other_agent', 'stage.moving', 'stage.position_label'],
}
EFFECTS['sim.step'] = ['clock.time_s']
ON_STEP = {
    'plant.configure': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'analyser.aperture', 'analyser.pass_energy_ev',
        'calibration.energy_scale', 'chiller.flow_ok', 'detector.count_rate_cps', 'flexilock.magazine', 'flexilock.mode', 'holder.images', 'holders', 'ion_gun.beam_on',
        'panel.ion_gun_lamp', 'stage.holder_id', 'stage.moving', 'stage.position_label', 'stage.rotation_indicated_rad', 'stage.rotation_rad',
        'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m',
        'system.state', 'transfer.arm_m', 'transfer.busy', 'transfer.gate_open_m', 'truth.fl_pressure_pa', 'truth.focus_offset_m', 'truth.ion_current_a',
        'truth.sac_pressure_pa', 'truth.samples', 'truth.surface_potential_v', 'truth.xray_power_w', 'vacuum.fl_log10_pressure_mbar', 'vacuum.fl_pressure_indicated_pa',
        'vacuum.sac_log10_pressure_mbar', 'vacuum.sac_pressure_indicated_pa', 'xray.power_indicated_w'],
    'fault.inject': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'analyser.aperture', 'analyser.pass_energy_ev',
        'calibration.energy_scale', 'chiller.flow_ok', 'detector.count_rate_cps', 'flexilock.mode', 'ion_gun.beam_on', 'neutraliser.on', 'panel.ion_gun_lamp',
        'panel.xray_lamp', 'stage.moving', 'stage.position_label', 'stage.x_indicated_m', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m',
        'system.error_code', 'truth.fl_pressure_pa', 'truth.focus_offset_m', 'truth.ion_current_a', 'truth.sac_pressure_pa', 'truth.samples',
        'truth.surface_potential_v', 'truth.xray_power_w', 'vacuum.fl_log10_pressure_mbar', 'vacuum.fl_pressure_indicated_pa', 'vacuum.sac_log10_pressure_mbar',
        'vacuum.sac_pressure_indicated_pa', 'xray.fault_code', 'xray.on', 'xray.power_indicated_w'],
    'power.set': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'analyser.aperture', 'analyser.pass_energy_ev',
        'calibration.energy_scale', 'chiller.flow_ok', 'detector.count_rate_cps', 'flexilock.magazine', 'flexilock.mode', 'holder.images', 'holders', 'ion_gun.beam_on',
        'panel.ion_gun_lamp', 'stage.holder_id', 'stage.moving', 'stage.position_label', 'stage.rotation_indicated_rad', 'stage.rotation_rad',
        'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m',
        'system.state', 'transfer.arm_m', 'transfer.busy', 'transfer.gate_open_m', 'truth.fl_pressure_pa', 'truth.focus_offset_m', 'truth.ion_current_a',
        'truth.sac_pressure_pa', 'truth.samples', 'truth.surface_potential_v', 'truth.xray_power_w', 'vacuum.fl_log10_pressure_mbar', 'vacuum.fl_pressure_indicated_pa',
        'vacuum.sac_log10_pressure_mbar', 'vacuum.sac_pressure_indicated_pa', 'xray.power_indicated_w'],
    'flexilock.vent': ['flexilock.mode', 'holder.images', 'truth.fl_pressure_pa', 'vacuum.fl_log10_pressure_mbar', 'vacuum.fl_pressure_indicated_pa'],
    'flexilock.pump': ['flexilock.mode', 'holder.images', 'truth.fl_pressure_pa', 'vacuum.fl_log10_pressure_mbar', 'vacuum.fl_pressure_indicated_pa'],
    'flexilock.set_lid': ['flexilock.mode', 'truth.fl_pressure_pa', 'vacuum.fl_pressure_indicated_pa'],
    'transfer.to_stage': ['acquisition.started_by_other_agent', 'flexilock.magazine', 'flexilock.magazine_z_m', 'holders', 'stage.holder_id', 'stage.moving',
        'stage.rotation_indicated_rad', 'stage.rotation_rad', 'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m',
        'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'transfer.arm_m', 'transfer.busy', 'transfer.gate_open_m', 'truth.fl_pressure_pa', 'truth.focus_offset_m',
        'truth.sac_pressure_pa', 'vacuum.fl_log10_pressure_mbar', 'vacuum.fl_pressure_indicated_pa', 'vacuum.sac_log10_pressure_mbar',
        'vacuum.sac_pressure_indicated_pa'],
    'transfer.to_magazine': ['acquisition.started_by_other_agent', 'flexilock.magazine', 'flexilock.magazine_z_m', 'holders', 'stage.holder_id', 'stage.moving',
        'stage.rotation_indicated_rad', 'stage.rotation_rad', 'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m',
        'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'transfer.arm_m', 'transfer.busy', 'transfer.gate_open_m', 'truth.fl_pressure_pa', 'truth.focus_offset_m',
        'truth.sac_pressure_pa', 'truth.samples', 'truth.surface_potential_v', 'vacuum.fl_log10_pressure_mbar', 'vacuum.fl_pressure_indicated_pa',
        'vacuum.sac_log10_pressure_mbar', 'vacuum.sac_pressure_indicated_pa'],
    'transfer.recover': ['acquisition.started_by_other_agent', 'holders', 'stage.holder_id', 'system.error_code', 'transfer.arm_m', 'transfer.busy',
        'transfer.gate_open_m', 'truth.focus_offset_m', 'truth.sac_pressure_pa', 'vacuum.sac_log10_pressure_mbar', 'vacuum.sac_pressure_indicated_pa'],
    'stage.move': ['stage.moving', 'stage.rotation_indicated_rad', 'stage.rotation_rad', 'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m',
        'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'truth.focus_offset_m', 'truth.samples', 'truth.surface_potential_v'],
    'stage.go_to': ['stage.moving', 'stage.rotation_indicated_rad', 'stage.rotation_rad', 'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m',
        'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'truth.focus_offset_m', 'truth.samples', 'truth.surface_potential_v'],
    'stage.auto_height': ['acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent', 'stage.moving',
        'stage.z_indicated_m', 'stage.z_m', 'truth.focus_offset_m'],
    'xray.set_source': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'analyser.aperture', 'analyser.pass_energy_ev',
        'calibration.energy_scale', 'detector.count_rate_cps', 'ion_gun.beam_on', 'panel.ion_gun_lamp', 'panel.xray_lamp', 'stage.moving', 'stage.position_label',
        'stage.x_indicated_m', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'truth.focus_offset_m', 'truth.ion_current_a',
        'truth.sac_pressure_pa', 'truth.samples', 'truth.surface_potential_v', 'truth.xray_power_w', 'vacuum.sac_pressure_indicated_pa', 'xray.power_indicated_w'],
    'neutraliser.set': ['detector.count_rate_cps', 'truth.samples', 'truth.surface_potential_v'],
    'ion_gun.set': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.count_rate_cps', 'panel.xray_lamp',
        'stage.z_indicated_m', 'stage.z_m', 'truth.focus_offset_m', 'truth.sac_pressure_pa', 'truth.samples', 'truth.surface_potential_v', 'truth.xray_power_w',
        'vacuum.sac_log10_pressure_mbar', 'vacuum.sac_pressure_indicated_pa', 'xray.fault_code', 'xray.on', 'xray.power_indicated_w'],
    'ion_gun.etch': ['acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent', 'ion_gun.beam_on',
        'panel.ion_gun_lamp', 'truth.ion_current_a', 'truth.sac_pressure_pa', 'truth.samples', 'truth.surface_potential_v', 'vacuum.sac_pressure_indicated_pa'],
    'acquisition.spectrum': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent',
        'detector.count_rate_cps', 'result.last_record_id'],
    'acquisition.image': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent',
        'detector.count_rate_cps', 'result.last_record_id'],
    'depth_profile.start': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent',
        'detector.count_rate_cps', 'ion_gun.beam_on', 'panel.ion_gun_lamp', 'result.last_record_id', 'truth.ion_current_a', 'truth.sac_pressure_pa', 'truth.samples',
        'truth.surface_potential_v', 'vacuum.sac_pressure_indicated_pa'],
    'calibration.energy_scale': ['acquisition.id', 'acquisition.kind', 'acquisition.started_by_other_agent', 'analyser.aperture', 'analyser.pass_energy_ev',
        'calibration.energy_scale', 'detector.count_rate_cps', 'ion_gun.beam_on', 'panel.ion_gun_lamp', 'result.last_record_id', 'stage.moving', 'stage.position_label',
        'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'truth.focus_offset_m', 'truth.ion_current_a',
        'truth.sac_pressure_pa', 'truth.samples', 'truth.surface_potential_v', 'vacuum.sac_pressure_indicated_pa'],
    'sim.step': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent', 'analyser.aperture',
        'analyser.pass_energy_ev', 'calibration.energy_scale', 'chiller.flow_ok', 'detector.count_rate_cps', 'flexilock.magazine', 'flexilock.magazine_z_m',
        'flexilock.mode', 'holder.images', 'holders', 'ion_gun.beam_on', 'neutraliser.on', 'panel.ion_gun_lamp', 'panel.xray_lamp', 'result.last_record_id',
        'stage.holder_id', 'stage.moving', 'stage.position_label', 'stage.rotation_indicated_rad', 'stage.rotation_rad', 'stage.tilt_indicated_rad', 'stage.tilt_rad',
        'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'system.error_code', 'system.state',
        'transfer.arm_m', 'transfer.busy', 'transfer.gate_open_m', 'truth.fl_pressure_pa', 'truth.focus_offset_m', 'truth.ion_current_a', 'truth.sac_pressure_pa',
        'truth.samples', 'truth.surface_potential_v', 'truth.xray_power_w', 'vacuum.fl_log10_pressure_mbar', 'vacuum.fl_pressure_indicated_pa',
        'vacuum.sac_log10_pressure_mbar', 'vacuum.sac_pressure_indicated_pa', 'xray.fault_code', 'xray.on', 'xray.power_indicated_w'],
}
ON_STEP_ZH = {
    'plant.configure': '供电超出AC 200 V±10%时系统断电：此后泵停、分析室与Flexi-lock压力上升、闸板阀关闭、运动停止、进行中的采集/传送/溅射/标定失败，按断电状态演化',
    'fault.inject': '故障在后续1 s周期起作用：冷却水或灯丝故障使X射线跳闸（进行中的采集失败、标定恢复分析器设定并停止样品台），中和器灯丝故障关闭中和器（绝缘样品重新荷电），'
                    '泄漏故障使对应腔体压力上升（可触发X射线真空联锁；Flexi-lock泄漏时抽气达不到已抽好状态）',
    'power.set': '上电后冷却水30 s建立、ESCApe 120 s启动就绪；断电后泵停、压力上升、闸板阀失电关闭、各轴停止、进行中的操作失败（传送中断时样品架可能停在传送叉上）',
    'flexilock.vent': 'Flexi-lock以12 s时间常数放气到大气压（干燥氮气，到98%时状态变为vented）；被中断的抽气循环不再拍样品架图像',
    'flexilock.pump': '前级泵粗抽到20 Pa后切换涡轮泵（切换时刻精确求解，样品出气按指数衰减精确积分）；压力低于1000 Pa后每15 s给一个仓槽的样品架拍光学图像；'
                      '压力低于5×10⁻⁵ Pa且三槽都拍过时状态变为pumped',
    'flexilock.set_lid': '开盖后导入室保持大气压；在放气尚未到98%时开盖，状态立即为vented，否则由放气过程在后续step中完成',
    'transfer.to_stage': '传送分阶段推进：样品台回传送位→样品架仓升降到该槽→闸板阀打开（两腔气体均分，分析室压力短暂上升）→传送叉伸出、样品架交到样品台→传送叉退回→'
                         '闸板阀关闭，操作完成；样品架到位后分析位下的对象与聚焦偏差改变',
    'transfer.to_magazine': '与to_stage相反的阶段序列；样品架离开分析位后其样品不再受X射线照射，表面电位按RC放电',
    'transfer.recover': '从中断的阶段继续：传送叉按原方向伸出或收回、交接样品架、退回并关闭闸板；完成后清除TRANSFER_INTERRUPTED',
    'stage.move': '五轴按工程速度（2 mm/s、5°/s、10°/s）走到目标；分析位下的样品改变时，聚焦偏差、荷电（表面电位的精确流解）与计数率随之变化',
    'stage.go_to': '同stage.move：移到传送位或台上参考样品（Ag、Au、Cu、栅网、法拉第杯）',
    'stage.auto_height': '60 s计数率-Z扫描结束后把Z移到焦面（残差5 µm）；进行中的操作属于调用者',
    'xray.set_source': '阳极高压以1 kV/s、发射电流以5 mA/s升到设定（功率读数随之变化）；关闭时进行中的采集、成像、深度剖析、标定（恢复分析器设定，样品台不再移往下一参考样品，标定结果不写入）与自动高度失败（正处于溅射阶段时离子束在调用时即停止）；光子照射使绝缘样品荷电',
    'neutraliser.set': '中和电子按电荷平衡与灯丝偏压着陆，绝缘样品表面电位弛豫到新平衡（精确流解），谱峰位置与计数率随之变化',
    'ion_gun.set': '待机时压电阀通入氩气，分析室压力上升（样品出气未衰减时可能触发X射线真空联锁，从而使进行中的操作失败）；离子能量与光栅决定之后的溅射',
    'ion_gun.etch': '离子束光栅扫描溅射：逐层去除、原子混合、氧化物择优失氧与氩注入；注入样品的氩取自分析室气体（分析室压力随之略降）；绝缘膜变薄后漏电阻下降、荷电改变；到时操作结束',
    'acquisition.spectrum': '按区域依次积分计数（每个区域时长=点数×驻留×扫描次数+1 s，快照模式为驻留×次数+0.5 s），完成的区域立即写入记录；探测器计数率读数随当前区域变化；全部完成后记录完成',
    'acquisition.image': '平行成像按设定时长积分，结束后写入128×128像；探测器计数率读数为成像模式的总计数率',
    'depth_profile.start': '先采集一次，再按“溅射→采集”循环n级；溅射阶段离子束开（离子枪灯亮、离子电流流过样品），溅射改变样品各层、分析室压力（注入样品的氩取自腔内气体）与荷电；每级完成后写入该级谱',
    'calibration.energy_scale': '依次移到Au、Ag、Cu参考样品，各溅射30 s后以通能10 eV、slot测谱；完成后写入偏移与增益并恢复分析器原设定（失败时也恢复）',
    'sim.step': '宿主推进时间：上述连续过程（运动与升降、X射线爬升、压力、荷电、溅射、计数与操作进度）在1 s仪器周期之间精确积分并在事件时刻切分；周期上更新读数、联锁与样品架图像',
}
_ENC = 'encoder; the 2 um accuracy is the related AXIS Supra value (Lakehead facility page), not a Supra+ specification'
_SAMPLING_READINGS = {'vacuum.sac_pressure_indicated_pa': 'ion gauge, 5 % relative noise', 'vacuum.fl_pressure_indicated_pa': 'Pirani / ion gauge, 5 % relative noise',
                      'vacuum.sac_log10_pressure_mbar': 'display of the ion-gauge reading (0.1 decade)', 'vacuum.fl_log10_pressure_mbar': 'display of the gauge reading',
                      'xray.power_indicated_w': 'anode HV x emission (1 W)', 'detector.count_rate_cps': 'DLD total count rate with Poisson noise',
                      'stage.x_indicated_m': _ENC, 'stage.y_indicated_m': _ENC, 'stage.z_indicated_m': _ENC,
                      'stage.tilt_indicated_rad': 'encoder (0.01 deg)', 'stage.rotation_indicated_rad': 'encoder (0.01 deg)'}
_SAMPLING_CONTINUOUS = {'clock.time_s', 'stage.x_m', 'stage.y_m', 'stage.z_m', 'stage.tilt_rad', 'stage.rotation_rad', 'stage.moving', 'flexilock.magazine_z_m',
                        'transfer.arm_m', 'transfer.gate_open_m', 'truth.sac_pressure_pa', 'truth.fl_pressure_pa', 'truth.surface_potential_v', 'truth.xray_power_w',
                        'truth.ion_current_a', 'truth.focus_offset_m', 'acquisition.progress', 'acquisition.remaining_s', 'panel.xray_lamp', 'panel.ion_gun_lamp',
                        'ion_gun.beam_on', 'truth.samples'}
_SAMPLING_CYCLE = {'system.state', 'system.error_code', 'power.enabled', 'chiller.flow_ok', 'xray.on', 'xray.fault_code', 'neutraliser.on', 'flexilock.mode',
                   'holder.images', 'acquisition.id', 'acquisition.kind', 'acquisition.started_by_other_agent', 'result.last_record_id', 'transfer.busy',
                   'stage.holder_id', 'holders', 'flexilock.magazine', 'calibration.energy_scale', 'panel.power_lamp', 'stage.position_label'}


def _sampling_text(k):
    if k in _SAMPLING_CONTINUOUS:
        return ('Instantaneous value at the read time: stage, magazine, transfer arm and gate-valve motions, X-ray ramps, chamber pressures, sputtering and '
                'surface charging are integrated continuously between the 1 s instrument cycles and cut at every event (arrivals, ends of regions, etch and '
                'transfer phases); counts are integrated with the state at each cycle, event and command.')
    if k in _SAMPLING_READINGS:
        return f'Reading sampled at the latest 1 s instrument cycle and held until the next one ({_SAMPLING_READINGS[k]}); null while unpowered.'
    if k in _SAMPLING_CYCLE:
        return ('Process or record state changed by commands, by events of the continuous integration (transfer phases, acquisition regions, pump cycle) or on '
                'the 1 s cycle (start-up, interlock trips, holder images).')
    return 'Changes only when a command is accepted (set point, configuration or bookkeeping) or, for the joint mirrors, with the command at once; no time dynamics.'


_R = ['power.enabled', 'system.state', 'system.error_code']
_BEAM = _R + ['acquisition.id', 'transfer.busy', 'xray.on', 'xray.power_indicated_w', 'vacuum.sac_pressure_indicated_pa', 'stage.holder_id', 'stage.moving']
PRECONDITIONS = {
    'plant.configure': ('无；随机数种子只在首次声明时生效；供电超出AC 200 V±10%时正在运行的系统断电', []),
    'sample.provide': ('已声明实验室条件；ID不重复（最多40个）；物质须在XPS模板库中；图案参数须完整一致；膜总厚不超过样品厚度的一半；薄膜样品须有至少一层膜',
                       ['plant.configured', 'sample.bench_ids']),
    'fault.inject': ('已声明实验室条件', ['plant.configured']),
    'power.set': ('已声明实验室条件；开机时供电须在AC 200 V±10%内（厂家：单相AC 200 V、50 A）', ['plant.configured', 'power.enabled']),
    'system.acknowledge': ('已通电；错误原因须已排除（供电恢复、冷却水恢复）', _R),
    'flexilock.vent': ('已就绪；无传送进行；传送叉未滞留在闸板阀通道中；闸板阀关闭', _R + ['transfer.busy', 'flexilock.mode']),
    'flexilock.pump': ('已就绪；传送叉未滞留在闸板阀通道中；盖关闭', _R + ['flexilock.lid_open', 'flexilock.mode']),
    'flexilock.set_lid': ('已声明实验室条件；打开须在导入室为大气压（≥0.95 atm）时', ['flexilock.mode', 'vacuum.fl_pressure_indicated_pa']),
    'holder.mount_sample': ('样品与样品架都在实验台上；样品在75×32 mm内、不重叠；厚度≤19 mm（双高样品架，厂家手册）或≤7 mm（组合样品架；7 mm取自相关型号AXIS Supra的Lakehead设施页与Illinois Supra+设施页，映射为工程假设）', ['holders', 'sample.bench_ids']),
    'holder.unmount_sample': ('样品架在实验台上且装有该样品', ['holders']),
    'flexilock.load_holder': ('盖打开；样品架在实验台上；槽空；该槽无传送', ['flexilock.lid_open', 'flexilock.magazine', 'holders']),
    'flexilock.unload_holder': ('盖打开；槽中有样品架', ['flexilock.lid_open', 'flexilock.magazine']),
    'transfer.to_stage': ('已就绪、无错误；无采集/溅射/传送；传送叉未滞留在闸板阀通道中；槽中有样品架；样品台空；Flexi-lock已抽好（<5×10⁻⁷ mbar）且盖关闭', _R + ['acquisition.id', 'transfer.busy',
                          'flexilock.mode', 'flexilock.magazine', 'stage.holder_id', 'ion_gun.beam_on']),
    'transfer.to_magazine': ('已就绪、无错误；无采集/溅射/传送；传送叉未滞留在闸板阀通道中；样品台上有样品架；目标槽空；Flexi-lock已抽好且盖关闭', _R + ['acquisition.id', 'transfer.busy',
                             'flexilock.mode', 'flexilock.magazine', 'stage.holder_id', 'ion_gun.beam_on']),
    'transfer.recover': ('已通电且就绪；锁存TRANSFER_INTERRUPTED且传送叉停在闸板阀通道中；无采集/溅射/其他传送', _R + ['acquisition.id', 'transfer.busy', 'stage.holder_id',
                         'flexilock.magazine', 'holders']),
    'stage.move': ('已就绪、无错误；无采集/传送；传送叉未滞留在闸板阀通道中；目标在行程内；分析位样品表面不高于分析面1 mm以上', _R + ['acquisition.id', 'transfer.busy', 'stage.holder_id']),
    'stage.go_to': ('已就绪、无错误；无采集/传送；传送叉未滞留在闸板阀通道中；目标位置可达', _R + ['acquisition.id', 'transfer.busy']),
    'stage.auto_height': ('已就绪、无错误；无采集/传送；传送叉未滞留在闸板阀通道中；X射线源在设定功率；样品台上有样品架且分析位有样品或参考样品', _BEAM),
    'xray.set_source': ('已就绪、无错误；开启须分析室压力低于联锁值、冷却水正常、功率≤600 W；采集中不能改发射电流', _R + ['vacuum.sac_pressure_indicated_pa',
                        'chiller.flow_ok', 'acquisition.id']),
    'analyser.configure': ('已就绪、无错误；无采集进行', _R + ['acquisition.id']),
    'neutraliser.set': ('已就绪、无错误', _R),
    'ion_gun.set': ('已就绪、无错误；未在溅射；没有进行中的深度剖析或能量标定（离子枪由该流程控制）', _R + ['ion_gun.beam_on', 'acquisition.kind']),
    'ion_gun.etch': ('已就绪、无错误；无采集/传送；离子枪待机；样品台上有样品架且静止', _R + ['acquisition.id', 'transfer.busy', 'ion_gun.on', 'stage.holder_id', 'stage.moving']),
    'ion_gun.measure_current': ('已就绪、无错误；离子枪待机；样品台停在法拉第杯位置', _R + ['ion_gun.on', 'stage.position_label']),
    'acquisition.spectrum': ('已就绪、无错误；无采集/传送；X射线源在设定功率；分析室真空；样品台上有样品架且静止；区域有效', _BEAM),
    'acquisition.image': ('同谱采集', _BEAM),
    'acquisition.result': ('记录存在', ['result.last_record_id']),
    'depth_profile.start': ('同谱采集，且离子枪待机', _BEAM + ['ion_gun.on']),
    'calibration.energy_scale': ('同谱采集，且离子枪待机（参考样品先溅射清洁）', _BEAM + ['ion_gun.on']),
    'data.identify': ('记录已完成且含该区域', ['result.last_record_id']), 'data.charge_correct': ('记录已完成且含该区域（C 1s）', ['result.last_record_id']),
    'data.quantify': ('记录已完成且含这些区域；各区域的峰可归属元素谱线', ['result.last_record_id']),
    'data.fit': ('记录已完成且含该区域', ['result.last_record_id']), 'data.depth_profile': ('深度剖析记录已完成', ['result.last_record_id']),
    'records.list': ('无', []), 'events.read': ('无', []), 'ledger.read': ('无（evaluator）', []), 'provenance.read': ('无（evaluator）', []),
    'sim.step': ('宿主调用；未声明实验室条件时只推进时钟', ['plant.configured']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_RE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING']
_ACQ_ERR = _RE + ['ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'XRAY_NOT_READY', 'VACUUM_NOT_READY', 'STAGE_EMPTY', 'MOTION_IN_PROGRESS', 'RECORDS_FULL']
CAP_ERRORS = {
    'plant.configure': [], 'sample.provide': ['PLANT_REQUIRED', 'DUPLICATE_SAMPLE', 'TOO_MANY_SAMPLES', 'UNKNOWN_TEMPLATE', 'INVALID_PATTERN'],
    'fault.inject': ['PLANT_REQUIRED'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'system.acknowledge': ['PLANT_REQUIRED', 'POWER_OFF', 'CAUSE_PERSISTS'],
    'flexilock.vent': _RE + ['TRANSFER_RUNNING', 'TRANSFER_INTERRUPTED', 'GATE_OPEN'], 'flexilock.pump': _RE + ['TRANSFER_INTERRUPTED', 'LID_OPEN'], 'flexilock.set_lid': ['PLANT_REQUIRED', 'NOT_VENTED'],
    'holder.mount_sample': ['PLANT_REQUIRED', 'HOLDER_NOT_ON_BENCH', 'UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'OUTSIDE_HOLDER', 'SAMPLE_TOO_THICK', 'POSITION_OCCUPIED'],
    'holder.unmount_sample': ['PLANT_REQUIRED', 'HOLDER_NOT_ON_BENCH', 'UNKNOWN_SAMPLE'],
    'flexilock.load_holder': ['PLANT_REQUIRED', 'LID_CLOSED', 'HOLDER_NOT_ON_BENCH', 'SLOT_OCCUPIED', 'TRANSFER_RUNNING'],
    'flexilock.unload_holder': ['PLANT_REQUIRED', 'LID_CLOSED', 'SLOT_EMPTY'],
    'transfer.to_stage': _RE + ['ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'TRANSFER_INTERRUPTED', 'ION_BEAM_ON', 'SLOT_EMPTY', 'STAGE_OCCUPIED', 'FLEXILOCK_NOT_PUMPED', 'USER_CANCEL', 'POWER_OFF'],
    'transfer.to_magazine': _RE + ['ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'TRANSFER_INTERRUPTED', 'ION_BEAM_ON', 'STAGE_EMPTY', 'SLOT_OCCUPIED', 'FLEXILOCK_NOT_PUMPED', 'USER_CANCEL', 'POWER_OFF'],
    'transfer.recover': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'NO_INTERRUPTED_TRANSFER', 'USER_CANCEL'],
    'stage.move': _RE + ['ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'TRANSFER_INTERRUPTED', 'OUT_OF_RANGE', 'COLLISION_RISK'],
    'stage.go_to': _RE + ['ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'TRANSFER_INTERRUPTED', 'OUT_OF_RANGE', 'COLLISION_RISK'],
    'stage.auto_height': _RE + ['ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'TRANSFER_INTERRUPTED', 'XRAY_NOT_READY', 'VACUUM_NOT_READY', 'STAGE_EMPTY', 'NOTHING_UNDER_ANALYSER', 'USER_CANCEL',
                                'XRAY_OFF'],
    'xray.set_source': _RE + ['VACUUM_NOT_READY', 'COOLING_WATER_LOW', 'FILAMENT_FAILURE', 'ACQUISITION_RUNNING'],
    'analyser.configure': _RE + ['ACQUISITION_RUNNING'], 'neutraliser.set': _RE + ['FILAMENT_FAILURE'], 'ion_gun.set': _RE + ['ION_BEAM_ON', 'ACQUISITION_RUNNING'],
    'ion_gun.etch': _RE + ['ACQUISITION_RUNNING', 'TRANSFER_RUNNING', 'ION_GUN_OFF', 'STAGE_EMPTY', 'MOTION_IN_PROGRESS', 'USER_CANCEL', 'POWER_OFF'],
    'ion_gun.measure_current': _RE + ['ION_GUN_OFF', 'NOT_ON_FARADAY_CUP'],
    'acquisition.spectrum': _ACQ_ERR + ['DUPLICATE_REGION', 'INVALID_REGION', 'REGION_EXCEEDS_SNAPSHOT_WINDOW', 'USER_CANCEL', 'XRAY_OFF', 'POWER_OFF'],
    'acquisition.image': _ACQ_ERR + ['USER_CANCEL', 'XRAY_OFF', 'POWER_OFF'], 'acquisition.result': ['UNKNOWN_RECORD'],
    'depth_profile.start': _ACQ_ERR + ['ION_GUN_OFF', 'DUPLICATE_REGION', 'INVALID_REGION', 'USER_CANCEL', 'XRAY_OFF', 'POWER_OFF'],
    'calibration.energy_scale': _ACQ_ERR + ['ION_GUN_OFF', 'USER_CANCEL', 'XRAY_OFF', 'POWER_OFF'],
    'data.identify': ['UNKNOWN_RECORD', 'WRONG_RECORD_KIND', 'RECORD_NOT_COMPLETED', 'UNKNOWN_REGION'],
    'data.charge_correct': ['UNKNOWN_RECORD', 'WRONG_RECORD_KIND', 'RECORD_NOT_COMPLETED', 'UNKNOWN_REGION', 'NO_PEAK'],
    'data.quantify': ['UNKNOWN_RECORD', 'WRONG_RECORD_KIND', 'RECORD_NOT_COMPLETED', 'UNKNOWN_REGION', 'UNASSIGNED_REGION'],
    'data.fit': ['UNKNOWN_RECORD', 'WRONG_RECORD_KIND', 'RECORD_NOT_COMPLETED', 'UNKNOWN_REGION'],
    'data.depth_profile': ['UNKNOWN_RECORD', 'WRONG_RECORD_KIND', 'RECORD_NOT_COMPLETED', 'UNKNOWN_REGION', 'UNASSIGNED_REGION'],
    'records.list': [], 'events.read': [], 'ledger.read': [], 'provenance.read': [], 'sim.step': [],
}
_PARTIAL = {'USER_CANCEL', 'XRAY_OFF', 'POWER_OFF'}
CAP_ERROR_ZH = {('ion_gun.set', 'ACQUISITION_RUNNING'): '深度剖析或能量标定进行中：离子枪由该流程控制，不能开关或改变能量与光栅（流程开始时已固定）',
                ('acquisition.spectrum', 'POWER_OFF'): '未通电；或采集中断电（已完成区域保留，记录以failed结束）',
                ('acquisition.spectrum', 'XRAY_OFF'): 'X射线在采集中关闭或跳闸（已完成区域保留）',
                ('transfer.to_stage', 'POWER_OFF'): '未通电；或传送中断电（传送叉已进入阀道时闸板被挡住保持打开，样品架可能停在传送叉上：锁存TRANSFER_INTERRUPTED，'
                                                    '用transfer.recover恢复；传送叉未伸出时闸板失电关闭）',
                ('transfer.to_magazine', 'POWER_OFF'): '未通电；或传送中断电（同上）',
                ('transfer.recover', 'POWER_OFF'): '未通电；或恢复中再次断电（再次锁存TRANSFER_INTERRUPTED）'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件，没有可见模型变化', 'sample.provide': '只在过程层实验台上增加样品；场景没有样品几何（样品架是可移动物体，样品架仓与样品台座为接口点）',
    'fault.inject': '评估者故障注入，没有可见模型变化', 'system.acknowledge': '只清除错误状态', 'holder.mount_sample': '样品与样品架只在过程层；场景没有样品架几何',
    'holder.unmount_sample': '同上', 'flexilock.load_holder': '样品架只在过程层（样品架仓为接口点）', 'flexilock.unload_holder': '同上',
    'analyser.configure': '透镜模式、光阑与通能在罩内，没有可见几何', 'neutraliser.set': '中和器在磁透镜内，没有可见几何', 'ion_gun.measure_current': '只读电流',
    'acquisition.result': '只读记录', 'data.identify': '分析计算', 'data.charge_correct': '分析计算', 'data.quantify': '分析计算', 'data.fit': '分析计算',
    'data.depth_profile': '分析计算', 'records.list': '只读列表', 'events.read': '只读事件', 'ledger.read': '只读账目（evaluator）', 'provenance.read': '只读来源记录（evaluator）',
}
# states that the Matterix binding mirrors into the scene (joints, monitor readouts and lamps; interface_definition)
SCENE_MIRRORED = {'flexilock.lid_angle_rad', 'flexilock.magazine_z_m', 'transfer.arm_m', 'transfer.gate_open_m', 'stage.x_m', 'stage.y_m', 'stage.z_m',
                  'stage.tilt_rad', 'stage.rotation_rad', 'vacuum.sac_log10_pressure_mbar', 'vacuum.fl_log10_pressure_mbar', 'xray.power_indicated_w',
                  'detector.count_rate_cps', 'acquisition.remaining_s', 'panel.power_lamp', 'panel.xray_lamp', 'panel.ion_gun_lamp'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节、显示器读数或指示灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


_GUARDED = _CONTROL_GUARDED | _DATA_GUARDED | set(_ANALYSIS_RECORD_ARGS)
RESOURCES = {'acquisition.spectrum': ['xray_source', 'analyser', 'detector', 'stage', 'host_clock'], 'acquisition.image': ['xray_source', 'analyser', 'detector', 'stage', 'host_clock'],
             'depth_profile.start': ['xray_source', 'ion_gun', 'analyser', 'detector', 'stage', 'host_clock'],
             'calibration.energy_scale': ['xray_source', 'ion_gun', 'analyser', 'detector', 'stage', 'reference_samples', 'host_clock'],
             'stage.auto_height': ['stage', 'xray_source', 'detector', 'host_clock'], 'ion_gun.etch': ['ion_gun', 'stage', 'host_clock'],
             'transfer.to_stage': ['transfer_arm', 'gate_valve', 'magazine', 'stage', 'host_clock'], 'transfer.to_magazine': ['transfer_arm', 'gate_valve', 'magazine', 'stage', 'host_clock'],
             'transfer.recover': ['transfer_arm', 'gate_valve', 'magazine', 'stage', 'host_clock'],
             'power.set': ['mains', 'pumps', 'chiller'], 'xray.set_source': ['xray_source', 'chiller'], 'neutraliser.set': ['neutraliser'], 'ion_gun.set': ['ion_gun', 'argon_supply'],
             'ion_gun.measure_current': ['faraday_cup'], 'analyser.configure': ['analyser'], 'stage.move': ['stage'], 'stage.go_to': ['stage'],
             'flexilock.vent': ['flexilock', 'nitrogen_supply'], 'flexilock.pump': ['flexilock', 'pumps', 'camera'], 'flexilock.set_lid': ['flexilock'],
             'flexilock.load_holder': ['flexilock', 'bench'], 'flexilock.unload_holder': ['flexilock', 'bench'], 'holder.mount_sample': ['bench'],
             'holder.unmount_sample': ['bench'], 'plant.configure': ['room', 'mains'], 'sample.provide': ['bench'], 'fault.inject': ['evaluator'],
             'sim.step': ['host_clock'], 'system.acknowledge': ['escape']}
_ROBOT = '装卸样品、样品架与开关盖为宿主/机器人技能的过程层抽象，没有接触、力控或机器人验证；盖关节由过程状态镜像。'
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.axis_supra_xps.limitations。'


def _foreign_acq_text(k):
    if k not in _CONTROL_GUARDED:
        return ''
    return '；另一个agent的采集/传送/溅射进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    access = ['ACCESS_DENIED'] if (CAPS[k]['visibility'] != 'agent' or k in _GUARDED) else []
    return list(dict.fromkeys(_COMMON_ERRORS + access + CAP_ERRORS.get(k, [])))


OBSERVES = {
    'plant.configure': ['plant.configured'], 'sample.provide': ['sample.bench_ids'], 'fault.inject': ['truth.faults'],
    'power.set': ['power.enabled', 'system.state', 'chiller.flow_ok', 'vacuum.sac_pressure_indicated_pa'], 'system.acknowledge': ['system.error_code'],
    'flexilock.vent': ['flexilock.mode', 'vacuum.fl_pressure_indicated_pa'], 'flexilock.pump': ['flexilock.mode', 'vacuum.fl_pressure_indicated_pa', 'holder.images'],
    'flexilock.set_lid': ['flexilock.lid_open'], 'holder.mount_sample': ['holders'], 'holder.unmount_sample': ['holders', 'sample.bench_ids'],
    'flexilock.load_holder': ['flexilock.magazine', 'holders'], 'flexilock.unload_holder': ['flexilock.magazine', 'holders'],
    'transfer.to_stage': ['transfer.busy', 'stage.holder_id', 'flexilock.magazine', 'vacuum.sac_pressure_indicated_pa'],
    'transfer.to_magazine': ['transfer.busy', 'stage.holder_id', 'flexilock.magazine'],
    'transfer.recover': ['transfer.busy', 'stage.holder_id', 'flexilock.magazine', 'system.error_code'],
    'stage.move': ['stage.moving', 'stage.x_indicated_m', 'stage.y_indicated_m', 'stage.z_indicated_m', 'stage.tilt_indicated_rad', 'stage.rotation_indicated_rad'],
    'stage.go_to': ['stage.moving', 'stage.position_label'], 'stage.auto_height': ['acquisition.kind', 'stage.z_indicated_m'],
    'xray.set_source': ['xray.on', 'xray.power_indicated_w', 'xray.fault_code', 'panel.xray_lamp'], 'analyser.configure': ['analyser.lens_mode', 'analyser.aperture',
                                                                                                                            'analyser.pass_energy_ev'],
    'neutraliser.set': ['neutraliser.on', 'neutraliser.settings'], 'ion_gun.set': ['ion_gun.on', 'ion_gun.energy_ev', 'ion_gun.raster_m', 'vacuum.sac_pressure_indicated_pa'],
    'ion_gun.etch': ['ion_gun.beam_on', 'acquisition.progress', 'panel.ion_gun_lamp'], 'ion_gun.measure_current': [],
    'acquisition.spectrum': ['acquisition.id', 'acquisition.progress', 'detector.count_rate_cps', 'result.last_record_id'],
    'acquisition.image': ['acquisition.id', 'acquisition.progress', 'detector.count_rate_cps', 'result.last_record_id'], 'acquisition.result': [],
    'depth_profile.start': ['acquisition.id', 'acquisition.progress', 'ion_gun.beam_on', 'result.last_record_id'],
    'calibration.energy_scale': ['acquisition.id', 'calibration.energy_scale', 'stage.position_label'],
    'data.identify': [], 'data.charge_correct': [], 'data.quantify': [], 'data.fit': [], 'data.depth_profile': [], 'records.list': [], 'events.read': [],
    'ledger.read': [], 'provenance.read': [], 'sim.step': ['clock.time_s'],
}
_JA = 'manufacturer_primary'
_BRO = 'kratos_brochure'
_EN = 'shimadzu_spec_en'
_LAK = 'lakehead_related'
_ILL = 'illinois_facility'
_DRAW = 'installation_drawing'
_LIB = 'xps_library'
_NIST = 'nist_srd20'
_EL = 'elettra_yeh_lindau'
_XR = 'xraydb'
_NIFS = 'nifs_yamamura_tawara'
_COD = 'cod'
_PHYS = 'physics_literature'
_SIM = 'simulation_assumption'
_WB = 'user_workbook'
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({
    'plant.configure': [_JA, _SIM], 'sample.provide': [_LIB, _NIST, _COD, _WB], 'power.set': [_JA, _BRO], 'flexilock.vent': [_JA, _BRO], 'flexilock.pump': [_JA, _BRO],
    'flexilock.set_lid': [_DRAW, _BRO], 'holder.mount_sample': [_BRO, _LAK], 'holder.unmount_sample': [_BRO], 'flexilock.load_holder': [_JA, _BRO], 'flexilock.unload_holder': [_BRO],
    'transfer.to_stage': [_JA, _EN, _BRO], 'transfer.to_magazine': [_JA, _EN, _BRO], 'stage.move': [_JA, _LAK], 'stage.go_to': [_JA], 'stage.auto_height': [_SIM],
    'xray.set_source': [_JA, _BRO], 'analyser.configure': [_JA, _EN], 'neutraliser.set': [_EN, _BRO, _LAK], 'ion_gun.set': [_JA, _BRO, _LAK], 'ion_gun.etch': [_JA, _NIFS, _LIB],
    'ion_gun.measure_current': [_JA], 'acquisition.spectrum': [_JA, _BRO, _LAK, _LIB, _NIST, _EL, _XR, _PHYS], 'acquisition.image': [_EN, _JA, _BRO, _LIB],
    'depth_profile.start': [_BRO, _LAK, _NIFS, _LIB], 'calibration.energy_scale': [_JA, _NIST], 'data.identify': [_BRO, _LIB, _NIST],
    'data.charge_correct': [_NIST], 'data.quantify': [_BRO, _EL, _PHYS], 'data.fit': [_PHYS], 'data.depth_profile': [_BRO, _PHYS],
    'provenance.read': [_LIB], **{k: [_JA, _BRO] + ([_ILL] if k == 'imaging.stitch' else [_LAK] if k in ('source.ag_la', 'ups.he_lamp', 'ion_gun.gas_cluster', 'holder.heat_cool') else [])
                                  for k in UNSUPPORTED}})
PARAM_ZH = {'room_k': '实验室室温', 'mains_voltage_v': '供电电压（厂家：单相AC 200 V）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
CAP_LIMITS = {
    'sample.provide': ['样品=模板库物质的层状结构（衬底+最多5层膜）＋可选横向图案（栅网、岛、条纹）；暴露空气的表面有0.6–1.4 nm不定碳污染层（厚度随机、真值隐藏）；'
                       '样品为刚性块体，粉末压片只作为物质形态标记（不模拟颗粒堆积与孔隙）。'],
    'flexilock.pump': ['前级泵粗抽到20 Pa后转涡轮泵；抽速、腔体积、出气率与传送压力5×10⁻⁷ mbar为工程值；样品架光学图像在压力低于1000 Pa后逐个拍摄（每个15 s），'
                       '识别到的样品中心与尺寸带±50 µm/±0.1 mm误差（工程值）。'],
    'flexilock.vent': ['干燥氮气放气（时间常数12 s，工程值）；开盖后导入室与样品接触实验室空气，样品重新带上不定碳污染层（瞬时近似）。'],
    'transfer.to_stage': ['顺序：样品台回传送位（厚样品时降低Z）、仓升降、开闸板阀（导入室气体与分析室瞬时均压）、传送叉伸出交接、退回、关阀；速度为工程值；'
                          '传送中断电时：传送叉已进入阀道则闸板被挡住保持打开并锁存TRANSFER_INTERRUPTED（transfer.recover从中断处完成传送），否则闸板失电关闭。'],
    'transfer.to_magazine': ['同transfer.to_stage的逆过程。'],
    'transfer.recover': ['恢复＝从中断的阶段按原方向继续传送（闸板保持打开）直到关阀；真实仪器的恢复可能需要维修人员手动操作，这里是过程层的工程抽象。'],
    'stage.move': ['X/Y/Z行程±50/±20/−20…+12 mm、倾角0–80°、转角±180°与速度为工程值；到位误差按相关型号AXIS Supra（Lakehead设施页）2 µm精度随机；倾转近似为绕分析点（真实倾轴位置未公开）；'
                   '分析位样品表面高于焦面1 mm以上的目标被拒。'],
    'stage.auto_height': ['自动高度用60 s完成并把样品表面放到焦面（残差5 µm）；不模拟实际的Z扫描曲线。'],
    'xray.set_source': ['阳极15 kV（工程值；厂家只给最大600 W）；高压与发射电流按工程速率爬升；样品处光子通量∝功率（1.5×10⁸光子/s/W，工程值）；X射线线宽0.18 eV（工程值，'
                        '由相关型号AXIS Supra（Lakehead设施页）Ag 3d5/2 0.48 eV指标反推）；联锁：分析室压力、冷却水、灯丝故障。'],
    'analyser.configure': ['通能集合5–160 eV为常见设置（厂家未列）；分析器高斯展宽≈0.0090×通能（slot；小光阑0.0080–0.0085，工程值，按相关型号AXIS Supra（Lakehead设施页）Ag 3d5/2 0.48 eV校准）；传输∝通能^1.5×KE^−0.6（工程值）；'
                           '小光阑按面积缩放计数率；静电模式传输为混合模式的30%（工程值）。'],
    'neutraliser.set': ['表面电位由光电发射（逃逸的慢电子受正电位抑制）、中和电子（能量≈0.55×电荷平衡+0.3×灯丝偏压，着陆能散0.35 eV）与漏电流平衡决定（单变量自治方程的精确流解：以w=ln|V−V*|为变量的高斯-勒让德积分，任意宿主步长切分结果相同）；'
                        '差分荷电展宽=0.03×|V|+0.05 eV；参数为工程值，按相关型号AXIS Supra（Lakehead设施页）PET C 1s 0.68 eV指标校准。'],
    'ion_gun.set': ['离子电流按2 µA@4 keV×(E/4 keV)^0.8（工程值；厂家未给）；开枪时压电阀通氩，分析室氩负载1.2×10⁻⁶ Pa·m³/s（工程值）。'],
    'ion_gun.etch': ['溅射产额=Yamamura–Tawara公式（NIFS-DATA-23表1参数；化合物用原子平均Z、M、U_s的模型估计），速率=Y·J/n；按坑（光栅）逐层剥离；'
                     '深度分辨=混合长度0.5 nm×(E/1 keV)^0.7与粗糙度3%×深度；氧化物择优失氧（至多25%金属原子还原）与2%氩注入为假设参数；不定碳污染层产额为石墨的2倍（假设）。'],
    'acquisition.spectrum': ['强度=通量×面积×原子密度×σ(Yeh–Lindau)×角因子(β,60°)×深度积分(TPP-2M IMFP)×传输；线形=Doniach–Sunjic/Voigt（自然宽度：Krause&Oliver/Keski-Rahkonen&Krause）'
                             '⊗仪器高斯⊗材料展宽⊗荷电展宽；自旋-轨道双峰按各能级结合能与(2j+1)截面；化学位移对该元素所有能级相同（近似）；背景=Tougaard通用截面（按发射深度加权）；'
                             '俄歇线只含NIST推荐的元素参考线（O、N、F等KLL未建模）；价带（<15 eV）未建模；弹性散射、X射线损伤与光电子衍射未建模。'],
    'acquisition.image': ['平行成像：128×128像素、分辨率=max(1 µm, 2像素)、能量窗=0.02×通能；照明为700×300 µm量级的平顶椭圆（边缘渐弱）；成像传输为谱学的10%（工程值）；'
                          '像素强度只计成像能量以下70 eV内的谱峰及其非弹性本底（更远谱峰的本底尾部略去，远离谱峰的能量处像素计数可为0）。'],
    'depth_profile.start': ['“先采一次，再溅射→采集”n级；溅射期间X射线保持开启；每级采集按模板区域。'],
    'calibration.energy_scale': ['ISO 15472两点修正（Au 4f7/2 83.96 eV、Cu 2p3/2 932.62 eV，Seah等1998经NIST SRD 20引用）并用Ag 3d5/2 368.21 eV检查线性；参考样品先各溅射30 s；'
                                 '峰位用平滑后抛物线顶点；仪器真实标尺误差（偏移σ=0.25 eV、增益σ=1.5×10⁻⁴）为工程值。'],
    'data.identify': ['识峰：平滑后高于局部基线6σ且>1.15×基线的极大，按模板库各元素主谱线及其化学态范围±1.5 eV给候选（最多8个）。'],
    'data.charge_correct': ['以Shirley背景后C 1s包络中最低结合能的主极大（≥半高）作为烃类碳，对到284.8 eV（NIST约定）。'],
    'data.quantify': ['Shirley背景面积÷灵敏度因子（σ×角因子×KE^0.7×传输；j分量区域包含其自旋-轨道伙伴时按整个子壳），不含基体与污染层修正。'],
    'data.fit': ['最小二乘拟合：Shirley背景（固定）+伪Voigt分量（位置±2 eV、FWHM 0.2–5 eV），误差按泊松计数加权。'],
    'data.depth_profile': ['每级用data.quantify的算法；溅射时间轴，不换算深度（深度需要溅射速率标定）。'],
    'holder.mount_sample': [_ROBOT], 'holder.unmount_sample': [_ROBOT], 'flexilock.load_holder': [_ROBOT], 'flexilock.unload_holder': [_ROBOT], 'flexilock.set_lid': [_ROBOT],
}
LIMITATIONS = [
    '选定配置：单色Al Kα（500 mm罗兰圆，最大600 W）、165 mm半球+球面镜分析器、128通道DLD、电子中和器、Minibeam 4单原子Ar⁺离子枪、Flexi-lock 3架仓、五轴样品台与台上Ag/Au/Cu/栅网/法拉第杯；'
    'Ag/Al双阳极、Minibeam 6团簇源与ISS、UPS、AES/SAM、加热冷却样品架与表面科学站未装。',
    '强度链路：光子通量×分析面积×原子密度×光电离截面（Yeh–Lindau 1985）×角分布因子×IMFP衰减（TPP-2M）×分析器传输×聚焦；绝对灵敏度常数按厂家相关型号Ag 3d5/2>760 kcps指标×1.05标定。',
    '模板：化学态结合能为NIST SRD 20记录的中位数（每条带记录号与文献）；元素参考为NIST推荐值（Powell 2012）；能级、自然线宽、荧光产额来自xraydb；密度来自COD晶体结构或xraydb；'
    '线形不对称度、材料展宽、卫星峰强度比与带隙为假设。',
    '荷电：单一表面电位＋差分展宽；粉末颗粒间荷电、电位分布引起的峰形畸变与负电子能谱效应未建模。',
    '溅射：Yamamura–Tawara元素产额；化合物产额、深度分辨、择优溅射与氩注入为模型估计；团簇离子（有机物无损剥层）不支持。',
    '仪器误差：能量标尺偏移与增益（标定前未知）、到位误差2 µm（相关型号AXIS Supra设施页）、自动高度残差；读数噪声为工程值。',
    '没有真实AXIS Supra+、ESCApe或探测器的硬件/软件接口；机器人装卸、开盖没有接触验证。',
]


def export_kratos_axissupra_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'KratosAxissupraSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.kratos_axissupra.KratosAxissupraSession.invoke',
                                              invocation=f'session.invoke(instance_id="axis_supra_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_acq_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['acquisition.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='KratosAxissupraRuntime.invoke / KratosAxissupraSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate',
                                     description_zh='设定、目标、状态标志或立即改变的派生量；压力、位置、电位、计数与操作进度只随step推进') for s in EFFECTS.get(k, [])],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=('操作结束（完成、失败或取消）：区域采集时间、溅射时间、传送各阶段或标定各步完成后确定' if async_ else
                                                      '原子设定、配置、分析或查询，实际物理状态由step推进'),
                                completion_state_ids=(['acquisition.id', 'acquisition.progress', 'result.last_record_id'] if k in ('acquisition.spectrum', 'acquisition.image', 'depth_profile.start',
                                                                                                                                    'calibration.energy_scale')
                                                      else ['transfer.busy', 'stage.holder_id'] if k.startswith('transfer.') else ['acquisition.id'] if async_ else []),
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('取消或失败（X射线关闭或跳闸、断电）时记录以cancelled/failed结束，已完成的区域（深度剖析：已完成的级）保留，进行中的区域丢弃。'
                                                    if k in ('acquisition.spectrum', 'depth_profile.start', 'calibration.energy_scale') else
                                                    '取消或失败时图像丢弃。' if k == 'acquisition.image' else
                                                    '取消或失败时已溅射的深度保留。' if k == 'ion_gun.etch' else
                                                    '取消时样品台停在当时位置。' if k == 'stage.auto_height' else
                                                    '取消或失败时各轴停在当时位置；传送叉在阀道中时闸板保持打开并锁存TRANSFER_INTERRUPTED（transfer.recover从中断处完成传送）。' if k.startswith('transfer.') else
                                                    ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变'))),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH.get(e, e)), side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'photon_energy_ev': (HV_EV, 'eV', 'Al Kα光子能量（单色）：模板库与Yeh–Lindau截面表（Elettra）按1486.6 eV；相关型号AXIS Supra的Lakehead设施页与Illinois Supra+设施页写1486.7 eV，0.1 eV差异由能量标尺标定吸收', [_EL, _LIB, _LAK, _ILL]),
            'xray_max_w': (XRAY_MAX_W, 'W', 'X射线源最大功率600 W（厂家）', _JA), 'rowland_diameter_m': (ROWLAND_DIAMETER_M, 'm', '罗兰圆直径500 mm（厂家）', _JA),
            'hsa_radius_m': (HSA_RADIUS_M, 'm', '半球分析器中心轨道半径165 mm（厂家）', _JA), 'dld_channels': (DLD_CHANNELS, '1', '延迟线探测器128通道（厂家）', _JA),
            'slot_width_m': (APERTURES['slot'][0], 'm', '大面积分析700 µm（厂家）', _JA), 'slot_height_m': (APERTURES['slot'][1], 'm', '大面积分析300 µm（厂家）', _JA),
            'small_spots_m': ([APERTURES[k] for k in ('d110', 'd55', 'd27', 'd15')], 'm', '选区直径110/55/27/15 µm（厂家）', _JA),
            'imaging_fov_m': ([IMAGING_FOV_M[k] for k in ('fov400', 'fov200', 'fov80')], 'm', '成像视场400/200/80 µm（厂家）', _EN),
            'imaging_resolution_m': (IMAGING_BEST_RESOLUTION_M, 'm', '平行成像极限分辨率1 µm（厂家）', _EN),
            'ion_energy_min_ev': (ION_ENERGY_RANGE_EV[0], 'eV', 'Minibeam 4最低500 V（厂家）', _JA), 'ion_energy_max_ev': (ION_ENERGY_RANGE_EV[1], 'eV', 'Minibeam 4最高4000 V（厂家）', _JA),
            'holder_size_m': (list(HOLDER_SIZE_M), 'm', '样品架75×32 mm（厂家手册）', _BRO), 'holder_slots': (HOLDER_SLOTS, '1', '同时抽气样品架3个（厂家）', _JA),
            'max_sample_thickness_m': (DUAL_HEIGHT_MAX_THICKNESS_M, 'm', '双高样品架最大样品厚19 mm（厂家手册）', _BRO),
            'ag_spec_cps': (AG_SPEC_CPS, '1/s', 'Ag 3d5/2>760 kcps@0.48 eV（相关型号AXIS Supra设施页）', _LAK), 'ag_spec_fwhm_ev': (AG_SPEC_FWHM_EV, 'eV', 'Ag 3d5/2半高宽0.48 eV（相关型号）', _LAK),
            'pet_spec_cps': (PET_SPEC_CPS, '1/s', 'PET C 1s>44 kcps@0.68 eV（相关型号）', _LAK), 'pet_spec_fwhm_ev': (PET_SPEC_FWHM_EV, 'eV', 'PET C 1s半高宽0.68 eV（相关型号）', _LAK),
            'stage_accuracy_m': (STAGE_ACCURACY_M, 'm', '五轴台精度2 µm（相关型号设施页）', _LAK), 'mains_voltage_v': (MAINS_V, 'V', '单相AC 200 V（厂家安装数据）', _JA),
            'mains_current_a': (MAINS_A, 'A', '50 A（厂家安装数据）', _JA), 'main_unit_mass_kg': (MASS_MAIN_UNIT_KG, 'kg', '主体1200 kg（厂家安装数据，标准构成）', _JA)}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src) if isinstance(src, list) else [src], uncertainty=None)
    for name, (val, unit, meaning) in {
            'anode_hv_v': (ANODE_HV_V, 'V', '阳极高压15 kV'), 'photons_per_w': (PHOTONS_PER_W, '1/(s*W)', '样品处单色光子通量/阳极功率'),
            'xray_line_fwhm_ev': (XRAY_LINE_FWHM_EV, 'eV', '单色Al Kα线宽'), 'analyser_res_per_pe': (ANALYSER_RES_PER_PE['slot'], '1', '分析器高斯展宽/通能（slot）'),
            'transmission_pe_exp': (TRANSMISSION_PE_EXP, '1', '传输∝通能指数'), 'transmission_ke_exp': (TRANSMISSION_KE_EXP, '1', '传输∝动能指数'),
            'dead_time_s': (DEAD_TIME_S, 's', 'DLD死时间'), 'snapshot_window_per_pe': (SNAPSHOT_WINDOW_PER_PE, '1', '探测器能量窗/通能'),
            'image_pixels': (IMAGE_PIXELS, '1', '成像像素数（每边）'), 'imaging_efficiency': (IMAGING_EFFICIENCY, '1', '成像传输/谱学传输'),
            'v_sac_m3': (V_SAC_M3, 'm^3', '分析室体积'), 'v_fl_m3': (V_FL_M3, 'm^3', 'Flexi-lock体积'), 's_sac_m3_s': (S_SAC_M3_S, 'm^3/s', '分析室有效抽速'),
            's_fl_m3_s': (S_FL_M3_S, 'm^3/s', 'Flexi-lock涡轮泵有效抽速'), 's_rough_m3_s': (S_ROUGH_M3_S, 'm^3/s', 'Flexi-lock前级抽速'),
            'p_sac_base_pa': (P_SAC_BASE_PA, 'Pa', '分析室本底压力'), 'fl_transfer_max_pa': (FL_TRANSFER_MAX_PA, 'Pa', '传送允许压力'), 'xray_max_pa': (XRAY_MAX_PA, 'Pa', 'X射线联锁压力'),
            'ion_gas_pa_m3_s': (ION_GAS_PA_M3_S, 'Pa*m^3/s', '离子枪氩气负载'), 'ion_current_4kev_a': (ION_CURRENT_4KEV_A, 'A', '4 keV离子电流'),
            'neut_current_per_a': (NEUT_CURRENT_PER_A, 'A/A', '中和电子电流/灯丝电流'), 'electron_yield_per_photon': (ELECTRON_YIELD_PER_PHOTON, '1', '总电子产额/光子'),
            'diff_charge_fraction': (DIFF_CHARGE_FRACTION, '1', '差分荷电展宽/|电位|'), 'mix_length_1kev_m': (MIX_LENGTH_1KEV_M, 'm', '1 keV离子混合长度'),
            'roughness_fraction': (ROUGHNESS_FRACTION, '1', '溅射粗糙化/深度'), 'oxide_reduction_max': (OXIDE_REDUCTION_MAX, '1', '氧化物最大还原分数'),
            'ar_implant_fraction': (AR_IMPLANT_FRACTION, '1', '稳态氩注入原子分数'), 'scale_offset_sd_ev': (SCALE_OFFSET_SD_EV, 'eV', '能量标尺偏移（1σ）'),
            'scale_gain_sd': (SCALE_GAIN_SD, '1', '能量标尺增益误差（1σ）'), 'boot_s': (BOOT_S, 's', 'ESCApe与电子单元启动时间')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['xray_source', 'mono_crystal', 'mono_hood', 'analysis_chamber', 'magnetic_lens', 'lens_column', 'hemispherical_analyser', 'spherical_mirror_analyser',
                       'delay_line_detector', 'ion_gun', 'stage_x', 'stage_y', 'stage_z', 'stage_tilt', 'stage_azimuth', 'reference_block', 'flexilock_chamber', 'magazine',
                       'transfer_arm', 'gate_valve_plate', 'sac_turbo_pump', 'fl_turbo_pump', 'chiller', 'flexilock_camera', 'sac_microscope'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='AXIS Supra+成像XPS：真空与样品交换、五轴台、单色Al Kα光电发射（截面、IMFP、线形、背景、荷电）、分析器与DLD计数、平行成像、Ar⁺溅射深度剖析、能量标定与数据处理',
        equations=['I_c = K (P/600 W) f_focus A_frac sum_z n_c(z) exp(-int_0^z dz/(lambda(KE) cos theta)) dz sigma_c(1486.6 eV) (1 + beta/2 (3/2 sin^2 60 deg - 1)) T(KE, PE) W_det',
                   'lambda = TPP-2M(E, rho, M, N_v, E_g) ; T = f_lens (PE/10 eV)^1.5 (KE/1118 eV)^-0.6 ; W_det = 0.18 PE',
                   'profile = DS(alpha, Gamma_L) (x) G(sqrt(0.18^2 + (r_ap PE)^2 + g_mat^2 + g_charge^2)), r_ap = 0.0090 (slot) ... 0.0080 (15 um) ; j = F + d (lambda K (x) j), lambda K = B T/(C + T^2)^2',
                   'BE_reported = ((BE_true + V)(1 + g_true) + o_true - o_cal)/(1 + g_cal)',
                   'C dV/dt = I_ph ((1 - f_slow) + f_slow exp(-V/kT)) - I_n / (1 + exp(-(V + E_e)/s)) - V/R : exact flow t = int C (V - V*)/(-F(V)) d ln|V - V*| (Gauss-Legendre)',
                   'Y(E) = Yamamura-Tawara(Z2, M2, U_s, Q, W, s) ; dz/dt = Y J / n ; sigma_mix = sqrt(l_mix^2 + (0.03 z)^2)',
                   'V dp/dt = Q + Q_out exp(-t/tau_out) - S p, exact (analysis chamber, Flexi-lock; argon and nitrogen tracked; backing -> turbo at 20 Pa at the exact crossing time) ; transfer: pressures equalise when the gate opens',
                   'quantification: x_i = (A_i/S_i)/sum(A_j/S_j), S = sigma L(beta) KE^0.7 T(KE)'],
        parameters=params, source_ids=[_SIM, _LIB, _NIST, _EL, _XR, _NIFS, _COD, _JA, _BRO, _EN, _LAK, _DRAW, _PHYS, _WB],
        limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_axis_supra_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'axis_supra_xps': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'axis_supra_xps': mechanism})
