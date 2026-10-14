"""CHEM-153 Anton Paar SAXSpace (SAXS/WAXS: Cu sealed tube, multilayer optics, Kratky block line/point collimation, TCStage on the StageMaster
YZ stage, MYTHEN2 R 1K on a 2theta arm, semitransparent beam stop, ASX autosampler with the 1 mm quartz flow cell): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time; instrument cycle 1 s):
  * power: main power (control PC, SAXSdrive start-up, detector, stage controller, vacuum pump and the recirculating chiller); X-ray generator
    behind the key switch and the emergency stop: X-RAY ON ramps the tube voltage and current to their set points, X-RAY OFF, STANDBY
    acknowledges a latched fault; interlocks: key, emergency stop, cooling-water flow of the chiller (HV trips when it stops); warning lamp;
  * vacuum of the beam path and sample chamber (one lumped volume, rotary-vane pump, leak, venting); the chamber door can only be opened
    when vented and closes the radiation enclosure (door open = shutter closed); air in the beam path attenuates and scatters;
  * X-ray beam: flux of the Cu Ka line (multilayer optics, Kb removed) ~ I (V/V_K - 1)^1.63; line (20 mm) or point collimation; the shutter
    opens only during an acquisition with the generator at its set points, the door closed and the detector safe;
  * sample: the TCStage (Peltier -30...150 C; extended stage -150...600 C) with a dual holder (capillary seat and the fixed 1 mm quartz
    flow cell 8 mm apart in Y, selected by the YZ stage; TrueFocus alignment finds the capillary axis from the transmission); sample
    temperature follows the set point (ramp limit, first-order settling), aqueous samples freeze below -10 C and burst above 95 C,
    proteins aggregate above 60 C; holders: 1 mm quartz capillary (60 uL), u-Cell (10 uL), PasteCell, solid holder (20 mm slit plate);
  * scattering: dSigma/dOmega of the sample from the template library (SASBDB measured curves scaled to absolute units per mg/mL, NIST SRM
    3600 certified curve, water from its compressibility, silver behenate and silicon Bragg peaks, NIST gold nanoparticles as Schulz spheres,
    user-parameter models) with Percus-Yevick structure factors, solvent displacement, capillary quartz walls, air; Ka1/Ka2 doublet;
  * detector: 1280 strips x 50 um x 8 mm at the sample-to-detector distance (true SDD and zero position differ from nominal by an unknown
    calibration error), slit-length smearing (beam length (x) strip length trapezoid), beam-width smearing, polarisation, flat-module solid
    angles, primary beam through the semitransparent beam stop, parasitic collimation scattering, dead time and the 1e6 /s/strip limit,
    Poisson counts; the 2theta arm moves the module up to 60 deg (TrueSWAXS);
  * data reduction and analysis: normalisation by the transmitted primary beam, transmission-weighted background subtraction, absolute scale
    from water or glassy carbon, regularised (Tikhonov) slit desmearing of line-collimation data, Guinier (Rg, I0), molecular weight, peak
    positions (d spacings);
    zero-angle and q-scale (silver behenate) calibration;
  * ASX autosampler: 192 wells, cooled tray, gantry and needle, injection into the flow cell with carry-over, cleaning;
  * fault injection (evaluator): vacuum leak, aged tube, misaligned beam stop, bubble in the capillary, chiller failure;
  * ledgers: photons (incident, absorbed, stopped, detected), liquid volume and solute mass (bench, wells, capillary, flow cell, waste),
    electrical energy of the tube.
"""
from __future__ import annotations

import copy
import functools
import json
import math
import random
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = '1.0.0'
SLUG = 'antonpaar_saxspace'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-153'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t3_analytical' / SLUG
LIBRARY_PATH = ASSET / 'saxs_library.json'
LIBRARY_REL = str(LIBRARY_PATH.relative_to(ROOT))

K_BOLTZMANN = 1.380649e-23
N_AVOGADRO = 6.02214076e23
R_E_M = 2.8179403262e-15                          # classical electron radius
P_ATM = 101325.0
HC_EV_M = 1.23984198e-6
CYCLE_S = 1.0
CYCLE_NS = 1_000_000_000
MAX_EVENTS = 500
# ---------------------------------------------------------------- documented values (see CAP_SOURCES / parameters for the source of each)
Q_MIN_SPEC_PER_M = 0.03e9                          # AZoM / Anton Paar specifications: accessible q 0.03-40.7 nm^-1
Q_MAX_SPEC_PER_M = 40.7e9
SWAXS_MAX_2THETA_RAD = math.radians(60.0)          # TrueSWAXS up to 60 deg 2theta
TCSTAGE_RANGE_K = (243.15, 423.15)                 # SickKids SBC: TCStage Peltier -30 to 150 C
EXTENDED_STAGE_RANGE_K = (123.15, 873.15)          # AZoM: temperature-controlled stages -150 to 600 C (by stage)
TEMP_ACCURACY_K = 0.1                              # AZoM: +-0.1 C
HOT_STAGE_RAMP_K_S = 10.0 / 60.0                   # PMC11957403 (J. Appl. Cryst. 2025): hot stage heating rate 10 C/min
ASX_WELLS = 192                                    # AZoM / SickKids: up to 192 liquid samples
ASX_TRAY_MIN_K = 277.15                            # SickKids: autosampler temperature controlled RT to 4 C
CAPILLARY_D_M = 1.0e-3                             # SickKids: 1 mm quartz capillary (60 uL); Allec et al. 2015: 1 mm OD, 0.01 mm wall
CAPILLARY_WALL_M = 1.0e-5
CAPILLARY_VOLUME_M3 = 60e-9
MU_CELL_VOLUME_M3 = 10e-9                          # SickKids: u-Cell 10 uL
LINE_LENGTH_M = 0.020                              # Allec et al. 2015 (beam length 2 cm); PMC11957403 (slit length 20 mm)
VACUUM_READY_PA = 300.0                            # Allec et al. 2015: beam path below 3 mbar
SDD_NOMINAL_M = 0.317                              # PMC11957403, PMC8900854, SASBDB SAXSpace entries
KV_DEFAULT_V = 40000.0                             # PMC11957403, PMC8900854, PMC10548465: 40 kV and 50 mA
MA_DEFAULT_A = 0.050
STRIPS = 1280                                      # DECTRIS MYTHEN2 R 1K: 1280 strips x 50 um, 8 mm (450 um Si)
STRIP_PITCH_M = 50e-6
STRIP_LENGTH_M = 8e-3
SENSOR_THICKNESS_M = 450e-6
MAX_RATE_PER_STRIP = 1.0e6                         # DECTRIS: count rate 10^6 counts/s per strip
COUNTER_MAX = 2 ** 24 - 1                          # DECTRIS: 24-bit dynamic range
FRAME_RATE_HZ = 100.0                              # DECTRIS: frame rate 100 Hz
MODULE_STRIP_OFFSETS_M = (-0.002, 0.062)           # interface definition: strip span measured from the arm's radial line
ARM_MAX_RAD = SWAXS_MAX_2THETA_RAD - math.atan(MODULE_STRIP_OFFSETS_M[1] / SDD_NOMINAL_M)      # 48.9 deg: far edge of the module at 60 deg
# ---------------------------------------------------------------- engineering values (evidence = assumption; see parameters)
FLUX_LINE_REF = 1.2e9                              # photons/s on the sample, line collimation, Cu tube at 40 kV / 50 mA (calibrated on SASDK46 error bars)
POINT_FLUX_FRACTION = 0.02                         # point / line flux
CHAR_EXPONENT = 1.63                               # characteristic line intensity ~ I (V/V_K - 1)^1.63 (empirical law)
CU_K_EDGE_EV = 8979.0
KV_MAX_V = 40000.0                                 # Cu sealed tube as operated in the published SAXSpace methods (rated power not published)
MA_MAX_A = 0.050
HV_RAMP_V_S = 2000.0
MA_RAMP_A_S = 5.0e-3
BOOT_S = 60.0
CHILLER_START_S = 30.0
POINT_SPOT_LENGTH_M = 0.3e-3
BEAM_TOP_M = 0.12e-3                               # block-collimated beam width at the detector: flat top 0.12 mm with 0.04 mm linear edges (FWHM 0.16 mm)
BEAM_EDGE_M = 0.04e-3
BEAM_HALF_M = BEAM_TOP_M / 2 + BEAM_EDGE_M
BEAMSTOP_HALF_M = 0.15e-3                          # semitransparent stop; data are used from 0.2 mm off the zero (q ~ 0.026 nm^-1, spec 0.03)
BEAMSTOP_T = 1.0e-3
MASK_HALF_M = 0.20e-3
SDD_SD_M = 0.5e-3                                  # mechanical tolerance of the true SDD (drawn per instrument)
ZERO_SD_M = 0.05e-3                                # beam position on the module at arm 0 (drawn per instrument)
ZERO_SEARCH_M = 3.0 * ZERO_SD_M                    # the attenuated primary peak is searched within the 3-sigma zero uncertainty of the hint,
ZERO_SEARCH_WIDE_M = 1.0e-3                        # then within 1 mm (true zero beyond 3 sigma before the first zero calibration)
BEAM_EDGE_RATIO = 0.25                             # beam-shaped: a strip between beam edge and stop edge, on both sides, below 1/4 of the peak strip
BEAM_ASYMMETRY_MAX = 0.3                           # and symmetric: |n(c+d) - n(c-d)| <= 0.3 (n(c+d) + n(c-d)) at d = 25, 50, 75 um
V_VAC_M3 = 0.012
S_PUMP_M3_S = 0.4e-3
P_ULT_PA = 30.0
LEAK_PA_M3_S = 1.0e-4
FAULT_LEAK_PA_M3_S = 0.2
VENT_TAU_S = 8.0
DOOR_OPEN_MIN_PA = 0.9 * P_ATM
AIR_PATH_M = 0.60
AIR_SCATTER_LENGTH_M = 0.10
STAGE_TAU_S = 20.0
STAGE_RAMP_MAX_K_S = HOT_STAGE_RAMP_K_S
ARM_SPEED_RAD_S = math.radians(5.0)
STAGE_SPEED_M_S = 1.0e-3
STAGE_TRAVEL_M = 0.010
FLOW_CELL_Y_M = -0.008                             # stage Y that brings the flow cell into the beam (interface definition)
CAP_MOUNT_SD_M = 0.15e-3
FLOW_CELL_OFFSET_SD_M = 0.03e-3
ALIGN_S = 60.0
ASX_INJECT_M3 = 30e-9
ASX_CARRY_OVER = 0.01
ASX_CARRY_OVER_CLEAN = 1.0e-4
ASX_TRAY_TAU_S = 300.0
ASX_GANTRY_SPEED_M_S = 0.10
DEAD_TIME_S = 1.0e-7
OVEREXPOSURE_RATE = 2.0e7
PARASITIC_LINE = 5.0e-3                            # sr^-1 equivalent d dSigma/dOmega at q = 0.05 nm^-1 (Kratky block, line)
PARASITIC_POINT = 2.0e-3
FREEZE_K = 263.15
MELT_K = 273.15
BURST_K = 368.15
AGG_T_K = 333.15
AGG_RATE_S = 1.0 / 600.0
AGG_MASS_RATIO = 50.0
AGG_RG_NM = 25.0
PROTEIN_VBAR_ML_MG = 0.73e-3
DRHO_M_PROTEIN_M_KG = 2.2e11                      # 2.2e10 cm/g (protein electron density 0.44 e/A^3, vbar 0.73 cm^3/g)
MAX_RECORDS = 200
MAX_SAMPLES = 40
ARM_POSITIONS_MAX = 8
SCAN_SETTLE_S = 2.0
LOG_BIN = 0.012


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
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- template library
_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        raw = json.loads(LIBRARY_PATH.read_text())
        t = {}
        for k, v in raw['templates'].items():
            v = dict(v)
            if v['kind'] == 'curve':
                q = np.asarray(v['q_nm_inv'], float)
                s = np.asarray(v['shape'], float)
                v['_q'], v['_ls'] = q, np.log(np.clip(s, 1e-12, None))
                n = min(12, len(q) - 2)
                sl, _ = np.polyfit(np.log(q[-n:]), v['_ls'][-n:], 1)
                v['_tail'] = float(min(-1.0, max(-4.0, sl)))
                a = v['abs_scale']
                v['_i0c'] = float(a['i0_per_c_cm_per_mg_ml'])
                v['_vbar_ml_mg'] = (a.get('vbar_cm3_g') or (1.0 / a['particle']['density_g_cm3'] if a.get('particle') else 0.73)) * 1e-3
                v['_rg_nm'] = v['rg_template_nm'] or v['rg_nm'] or (v['dmax_nm'] / 3.0 if v.get('dmax_nm') else 3.0)
                v['_protein'] = v['category'] in ('protein', 'complex') and v['tier'] != 'material'
            elif v['kind'] == 'certified_curve':
                v['_lq'] = np.log(np.asarray(v['q_nm_inv'], float))
                v['_li'] = np.log(np.asarray(v['dsdo_cm'], float))
            t[k] = v
        _LIB = dict(meta={k: v for k, v in raw.items() if k not in ('templates', 'materials')}, templates=t, materials=raw['materials'])
    return _LIB


def library():
    return _lib()


def template(tid):
    t = _lib()['templates'].get(tid)
    if t is None:
        raise ActionRejected('UNKNOWN_TEMPLATE')
    return t


def material(name):
    m = _lib()['materials'].get(name)
    if m is None:
        raise ActionRejected('UNKNOWN_MATERIAL')
    return m


@functools.lru_cache(maxsize=None)
def ka_lines():
    """(lambda Ka1, lambda Ka2, weights) of Cu from the library (xraydb/Elam line energies); Kb is removed by the multilayer optics."""
    c = _lib()['meta']['constants']['cu_lines_ev']
    return HC_EV_M / c['CuKa1'], HC_EV_M / c['CuKa2'], (2.0 / 3.0, 1.0 / 3.0)


def mean_wavelength_m():
    l1, l2, (w1, w2) = ka_lines()
    return w1 * l1 + w2 * l2


# ---------------------------------------------------------------- water, glass, air (dSigma/dOmega in 1/cm, q in 1/nm)
def water_density_g_cm3(t_c):
    num = 999.83952 + 16.945176 * t_c - 7.9870401e-3 * t_c ** 2 - 46.170461e-6 * t_c ** 3 + 105.56302e-9 * t_c ** 4 - 280.54253e-12 * t_c ** 5
    return num / (1.0 + 16.87985e-3 * t_c) / 1000.0


def water_kappa_per_pa(t_c):
    num = 50.88496e-8 + 6.163813e-9 * t_c + 1.459187e-11 * t_c ** 2 + 20.08438e-14 * t_c ** 3 - 58.47727e-17 * t_c ** 4 + 410.4110e-20 * t_c ** 5
    return num / (1.0 + 19.67348e-3 * t_c) / 1000.0


def water_forward_cm(temp_k):
    t = min(100.0, max(0.0, temp_k - 273.15))
    ne = water_density_g_cm3(t) / 18.01528 * N_AVOGADRO * 10.0 * 1e6          # electrons / m^3
    return (ne * R_E_M) ** 2 * K_BOLTZMANN * (t + 273.15) * water_kappa_per_pa(t) / 100.0


def water_cm(q, temp_k):
    h = _lib()['templates']['water']['waxs_halo']
    s = h['fwhm_nm_inv'] / 2.3548
    return water_forward_cm(temp_k) * (1.0 + (h['peak_over_forward'] - 1.0) * np.exp(-0.5 * ((q - h['q_peak_nm_inv']) / s) ** 2))


ICE_PEAKS_NM = [(16.06, 1.0), (17.08, 0.55), (18.19, 0.75), (23.6, 0.25), (27.9, 0.30), (30.6, 0.20)]     # ice Ih (a ~ 0.452 nm, c ~ 0.736 nm), relative: assumption


def ice_cm(q):
    out = np.full_like(q, 0.012)
    for qp, rel in ICE_PEAKS_NM:
        out += 2.0 * rel * np.exp(-0.5 * ((q - qp) / 0.12) ** 2)
    return out + 1e-6 * np.clip(q, 0.05, None) ** -4


def glass_cm(q):
    """Vitreous silica of the capillary walls: first sharp diffraction peak near 15.5 nm^-1 (assumption for the absolute level)."""
    return 0.02 + 0.45 * np.exp(-0.5 * ((q - 15.5) / 1.5) ** 2)


def kapton_cm(q):
    return 0.01 + 0.20 * np.exp(-0.5 * ((q - 12.0) / 2.5) ** 2)


def air_cm(q, p_pa, temp_k):
    n = p_pa / (K_BOLTZMANN * temp_k) * 1e-6                  # molecules / cm^3
    f = 1.0 / (1.0 + (q / 18.0) ** 2) ** 2
    return n * (R_E_M * 100.0) ** 2 * (209.9 * f + 14.4 * (1.0 - f))


def mu_air_per_m(p_pa, temp_k):
    return material('air')['mu_cu_ka_per_cm'] * 100.0 * (p_pa / P_ATM) * (293.15 / temp_k)


# ---------------------------------------------------------------- particle models
def s_py(q_nm, r_nm, phi):
    """Percus-Yevick hard-sphere structure factor (Kinning & Thomas 1984)."""
    if phi <= 1e-6:
        return np.ones_like(q_nm)
    a = (1 + 2 * phi) ** 2 / (1 - phi) ** 4
    b = -6 * phi * (1 + phi / 2) ** 2 / (1 - phi) ** 4
    g = phi * a / 2
    x = np.maximum(2 * q_nm * r_nm, 1e-3)
    sx, cx = np.sin(x), np.cos(x)
    G = a * (sx - x * cx) / x ** 2 + b * (2 * x * sx + (2 - x ** 2) * cx - 2) / x ** 3 + g * (-x ** 4 * cx + 4 * ((3 * x ** 2 - 6) * cx + (x ** 3 - 6 * x) * sx + 6)) / x ** 5
    s = 1.0 / (1.0 + 24 * phi * G / x)
    s0 = (1 - phi) ** 4 / (1 + 2 * phi) ** 2
    return np.where(x < 2e-2, s0, np.clip(s, 1e-3, None))


def sphere_amp(x):
    x = np.maximum(x, 1e-6)
    return np.where(x < 1e-3, 1.0 - x * x / 10.0, 3.0 * (np.sin(x) - x * np.cos(x)) / x ** 3)


def schulz_spheres_cm(q_nm, r_nm, rel_sigma, phi, drho_cm2):
    z = 1.0 / rel_sigma ** 2 - 1.0
    r = np.linspace(max(0.1 * r_nm, r_nm * (1 - 5 * rel_sigma)), r_nm * (1 + 6 * rel_sigma), 72)
    w = np.exp(z * np.log(r / r_nm) - (z + 1) * r / r_nm)
    w /= w.sum()
    v = 4.0 / 3.0 * math.pi * (r * 1e-7) ** 3                     # cm^3
    amp = sphere_amp(np.outer(q_nm, r))
    return phi * drho_cm2 ** 2 * (amp ** 2 * (w * v * v)).sum(1) / (w * v).sum()


def _peak_set(q_nm, peaks, width_nm, invariant_cm4):
    """Bragg/ordering peaks carrying a given share of the Porod invariant Q = int q^2 I dq (q in 1/cm)."""
    out = np.zeros_like(q_nm)
    tot = sum(w for _, w in peaks) or 1.0
    s = width_nm / 2.3548
    for qp, w in peaks:
        qc = qp * 1e7
        h = invariant_cm4 * (w / tot) / (qc * qc * (s * 1e7) * math.sqrt(2 * math.pi))
        out += h * np.exp(-0.5 * ((q_nm - qp) / s) ** 2)
    return out


def lamellar_cm(q_nm, p):
    d = p['d_spacing_nm']
    delta = p['layer_thickness_nm']
    peaks = []
    for n in range(1, 9):
        qn = 2 * math.pi * n / d
        f = math.sin(qn * delta / 2) / (qn * delta / 2)
        peaks.append((qn, f * f / (1.0 + 0.15 * n * n)))
    width = 2 * math.pi * 0.9 / (p['domain_layers'] * d)
    q_inv = 2 * math.pi ** 2 * p['volume_fraction'] * (1 - p['volume_fraction']) * (p['contrast_1e10_cm2'] * 1e10) ** 2
    return _peak_set(q_nm, peaks, width, q_inv)


def hexagonal_cm(q_nm, p):
    a = p['lattice_a_nm']
    r = p['pore_radius_nm']
    peaks = []
    for (h, k), m in {(1, 0): 6, (1, 1): 6, (2, 0): 6, (2, 1): 12, (3, 0): 6, (2, 2): 6}.items():
        qhk = 4 * math.pi * math.sqrt(h * h + h * k + k * k) / (math.sqrt(3) * a)
        x = qhk * r
        j1 = _bessel_j1(x)
        peaks.append((qhk, m * (2 * j1 / x) ** 2 / qhk))
    width = 2 * math.pi * 0.9 / (p['domain_cells'] * a)
    phi = p['porosity']
    q_inv = 2 * math.pi ** 2 * phi * (1 - phi) * (p['contrast_1e10_cm2'] * 1e10) ** 2
    return _peak_set(q_nm, peaks, width, q_inv)


def _bessel_j1(x):
    """J1 by its series / asymptotic form (enough for form factors)."""
    if x < 8.0:
        s, term, k = 0.0, x / 2, 0
        while abs(term) > 1e-12 and k < 60:
            s += term
            k += 1
            term *= -(x * x / 4) / (k * (k + 1))
        return s
    return math.sqrt(2 / (math.pi * x)) * math.cos(x - 3 * math.pi / 4)


def powder_peaks_cm(q_nm, peaks, crystallite_nm, scale_cm):
    """Powder Bragg peaks (q, relative intensity) with the Scherrer width of the crystallites; absolute peak height scale_cm (assumption)."""
    out = np.zeros_like(q_nm)
    s = 2 * math.pi * 0.94 / crystallite_nm / 2.3548
    for qp, rel in peaks:
        out += scale_cm * rel * np.exp(-0.5 * ((q_nm - qp) / s) ** 2)
    return out


# ---------------------------------------------------------------- template evaluation: dSigma/dOmega (1/cm) of one component
def curve_shape(t, q_nm):
    q = np.asarray(q_nm, float)
    grid, ls = t['_q'], t['_ls']
    out = np.exp(np.interp(q, grid, ls))
    hi = q > grid[-1]
    if hi.any():
        out[hi] = math.exp(ls[-1]) * (q[hi] / grid[-1]) ** t['_tail']
    return out


def srm3600_cm(t, q_nm):
    lq = np.log(np.clip(q_nm, 1e-4, None))
    x, y = t['_lq'], t['_li']
    out = np.interp(lq, x, y)
    lo, hi = lq < x[0], lq > x[-1]
    if lo.any():
        s = np.polyfit(x[:6], y[:6], 1)[0]
        out[lo] = y[0] + s * (lq[lo] - x[0])
    if hi.any():
        s = np.polyfit(x[-6:], y[-6:], 1)[0]
        out[hi] = y[-1] + s * (lq[hi] - x[-1])
    return np.exp(out)


def component_cm(comp, q_nm, temp_k, phi_total):
    """dSigma/dOmega (1/cm) of one component of a sample at q (1/nm): template x concentration x structure factor."""
    kind = comp['kind']
    if kind == 'curve':
        t = template(comp['template_id'])
        c = comp['concentration_mg_ml']
        i = c * t['_i0c'] * curve_shape(t, q_nm)
        phi = c * t['_vbar_ml_mg']
        if phi > 1e-4:
            i = i * s_py(q_nm, math.sqrt(5.0 / 3.0) * t['_rg_nm'], min(0.45, phi))
        a = comp.get('aggregated_fraction', 0.0)
        if a > 0.0:
            rg = AGG_RG_NM
            i = i * (1.0 - a) + c * a * t['_i0c'] * AGG_MASS_RATIO * np.exp(-(q_nm * rg) ** 2 / 3.0) / (1.0 + (q_nm * rg) ** 4 / 9.0)
        return i
    if kind == 'sphere_model':
        t = template(comp['template_id'])
        au = t['particle']
        phi = comp['concentration_mg_ml'] * 1e-3 / au['density_g_cm3']
        drho = (au['sld_cu_ka_1e10_cm2'] - material('water')['sld_cu_ka_1e10_cm2']) * 1e10
        return schulz_spheres_cm(q_nm, t['mean_diameter_nm'] / 2, t['schulz_rel_sigma'], phi, drho)
    if kind == 'certified_curve':
        return srm3600_cm(template(comp['template_id']), q_nm)
    if kind == 'lamellar_powder':
        t = template(comp['template_id'])
        return powder_peaks_cm(q_nm, [(p['q_nm_inv'], p['rel_intensity']) for p in t['peaks']], t['domain_size_nm'], t['i001_peak_cm']) + 0.05
    if kind == 'crystal_powder':
        t = template(comp['template_id'])
        return powder_peaks_cm(q_nm, [(r['q_nm_inv'], r['rel_intensity']) for r in t['reflections']], t['crystallite_nm'], t['strongest_peak_cm']) + 0.03
    if kind == 'model_spheres':
        p = comp['parameters']
        m = material(p['material'])
        drho = (m['sld_cu_ka_1e10_cm2'] - (material(p['matrix'])['sld_cu_ka_1e10_cm2'] if p['matrix'] != 'vacuum' else 0.0)) * 1e10
        i = schulz_spheres_cm(q_nm, p['radius_nm'], p['rel_sigma'], p['volume_fraction'], drho)
        return i * s_py(q_nm, p['radius_nm'], min(0.45, p['volume_fraction'])) if p['volume_fraction'] > 0.01 else i
    if kind == 'model_lamellar':
        return lamellar_cm(q_nm, comp['parameters'])
    if kind == 'model_hexagonal':
        return hexagonal_cm(q_nm, comp['parameters'])
    raise ActionRejected('UNKNOWN_TEMPLATE')


# ---------------------------------------------------------------- detector geometry and smearing
STRIP_S = MODULE_STRIP_OFFSETS_M[0] + (np.arange(STRIPS) + 0.5) * STRIP_PITCH_M          # strip centres along the module from the arm's radial line
_GL_X, _GL_W = np.polynomial.legendre.leggauss(10)


def _trapezoid_nodes(n=9):
    x = np.linspace(-BEAM_HALF_M, BEAM_HALF_M, n)
    w = np.clip((BEAM_HALF_M - np.abs(x)) / BEAM_EDGE_M, 0.0, 1.0)
    w[0] = w[-1] = 0.0
    x, w = x[1:-1], w[1:-1]
    return x, w / w.sum()


_BW_X, _BW_W = _trapezoid_nodes(11)


def beam_fraction(lo, hi, centre):
    """Fraction of the trapezoidal beam profile (flat top BEAM_TOP_M, linear edges BEAM_EDGE_M, centred at centre) between lo and hi (m)."""
    a, e = BEAM_TOP_M / 2, BEAM_EDGE_M

    def F(x):
        v = np.asarray(x, float) - centre
        return np.select([v <= -a - e, v <= -a, v <= a, v <= a + e],
                         [0.0, (v + a + e) ** 2 / (2 * e), e / 2 + (v + a), e / 2 + 2 * a + e / 2 - (a + e - v) ** 2 / (2 * e)], 2 * a + e) / (2 * a + e)
    return F(hi) - F(lo)


def length_kernel(collimation):
    """Nodes (m, >= 0) and weights of the slit-length weighting W = beam length profile (x) strip acceptance, int W = strip length (8 mm).
    Line: 20 mm beam, point: 0.3 mm spot; the 1D strips always integrate 8 mm along their length."""
    lb = LINE_LENGTH_M if collimation == 'line' else POINT_SPOT_LENGTH_M
    ld = STRIP_LENGTH_M
    a, b = abs(lb - ld) / 2.0, (lb + ld) / 2.0
    nodes, weights = [], []
    for lo, hi in ((0.0, a), (a, b)):
        if hi - lo < 1e-12:
            continue
        x = lo + (hi - lo) * (_GL_X + 1.0) / 2.0
        w = _GL_W * (hi - lo) / 2.0
        wx = np.where(x <= a, min(lb, ld) / lb, (b - x) / lb)                 # trapezoid height: overlap length / beam length
        nodes.append(x)
        weights.append(2.0 * w * wx)                                            # symmetric: both signs of the offset
    x, w = np.concatenate(nodes), np.concatenate(weights)
    return x, w * (ld / w.sum())


def strip_geometry(arm_rad, sdd_m, zero_m):
    """True in-plane angle (rad), distance to the sample (m) and cos(incidence) of every strip."""
    ds = STRIP_S - zero_m
    phi = np.arctan2(ds, sdd_m)
    return arm_rad + phi, np.hypot(sdd_m, ds), np.cos(phi)


def primary_peak(counts, zero_hint_m):
    """Centre and integral of the attenuated primary beam behind the semitransparent stop, from the counts themselves.  Every local maximum is a
    candidate; it is refined by the count centroid of the strips wholly behind a stop centred there (half-width minus half a strip) until the
    centroid is a fixed point, and accepted only if (a) the fixed point lies within the 3-sigma zero uncertainty (0.15 mm) of the hint - or, when
    nothing qualifies there (an instrument whose true zero lies further from nominal before its first zero calibration), within 1 mm; (b) the
    strongest window strip lies inside the beam half-width; (c) it is beam-shaped: on BOTH sides, between the beam edge (0.1 mm) and the stop edge
    (0.15 mm), a strip holds less than a quarter of the peak strip (the 'moat' of attenuated scatter behind the stop), and the profile is symmetric
    about the centre within 30 % at 25, 50 and 75 um (the attenuated beam is a symmetric trapezoid; a maximum of unattenuated scattering at the stop
    edge - even a steeply falling one - has the 1e-3 stop on one side only).  The largest accepted window wins; (None, 0) when none."""
    n = np.asarray(counts, float)
    half = BEAMSTOP_HALF_M - STRIP_PITCH_M / 2.0
    fixed = {}
    for j in np.where(np.abs(STRIP_S - zero_hint_m) <= ZERO_SEARCH_WIDE_M)[0]:
        if n[j] <= 0 or (j > 0 and n[j] < n[j - 1]) or (j < STRIPS - 1 and n[j] < n[j + 1]):
            continue
        c, converged = STRIP_S[j], False
        for _ in range(12):
            m = np.abs(STRIP_S - c) <= half
            if n[m].sum() <= 0:
                break
            c_new = float((n[m] * STRIP_S[m]).sum() / n[m].sum())
            converged = abs(c_new - c) < 0.5e-6
            c = c_new
            if converged:
                break
        if not converged:
            continue
        m = np.abs(STRIP_S - c) <= half
        peak = n[m].max()
        if abs(STRIP_S[m][int(np.argmax(n[m]))] - c) > BEAM_HALF_M:
            continue
        moat = True                                               # behind the stop but beyond the beam, on BOTH sides: only attenuated scatter
        for side in (-1.0, 1.0):
            band = (side * (STRIP_S - c) >= BEAM_HALF_M - 0.01e-3) & (side * (STRIP_S - c) <= BEAMSTOP_HALF_M + 0.01e-3)
            if not band.any() or n[band].min() > BEAM_EDGE_RATIO * peak:
                moat = False
        # the attenuated beam is a symmetric trapezoid; a maximum of unattenuated scattering at the stop edge has the 1e-3 stop on one side only
        for d in (0.025e-3, 0.05e-3, 0.075e-3):
            a, b = np.interp(c + d, STRIP_S, n), np.interp(c - d, STRIP_S, n)
            if abs(a - b) > BEAM_ASYMMETRY_MAX * max(a + b, 1.0):
                moat = False
        if moat:
            fixed[round(c, 7)] = (c, float(n[m].sum()))
    for reach in (ZERO_SEARCH_M, ZERO_SEARCH_WIDE_M):
        ok = [v for v in fixed.values() if abs(v[0] - zero_hint_m) <= reach]
        if ok:
            return max(ok, key=lambda v: v[1])
    return None, 0.0


def indicated_axes(arm_rad, sdd_cal_m, zero_cal_m):
    tt, _, _ = strip_geometry(arm_rad, sdd_cal_m, zero_cal_m)
    lam = mean_wavelength_m()
    return tt, 4.0 * math.pi / lam * np.sin(np.abs(tt) / 2.0)


def polarisation(two_theta):
    return 0.5 * (1.0 + np.cos(two_theta) ** 2)


def detector_efficiency():
    si = material('silicon')
    return 1.0 - math.exp(-si['mu_cu_ka_per_cm'] * 100.0 * SENSOR_THICKNESS_M)


def expected_rates(scatter_fn, arm_rad, sdd_m, zero_m, collimation, flux):
    """Expected scattered count rate (1/s) of every strip.
    scatter_fn(q_nm) -> sum over scatterers of (attenuation-weighted) thickness (cm) x dSigma/dOmega (1/cm) (per sr).
    Integrates the slit-length offsets (out-of-plane angle psi, cos 2theta_eff = cos 2theta cos psi), the beam width (trapezoid, in plane), the
    Ka1/Ka2 doublet, polarisation and flat-module solid angles; detector efficiency of 450 um Si at Cu Ka."""
    tt, r, cosi = strip_geometry(arm_rad, sdd_m, zero_m)
    lx, lw = length_kernel(collimation)
    (l1, l2, (w1, w2)) = ka_lines()
    acc = np.zeros(STRIPS)
    for gx, gw in zip(_BW_X, _BW_W):
        tt_w = arm_rad + np.arctan2(STRIP_S + gx - zero_m, sdd_m)
        c2 = np.cos(tt_w)[:, None] * np.cos(np.arctan2(lx[None, :], r[:, None]))
        teff = np.arccos(np.clip(c2, -1.0, 1.0))
        val = np.zeros_like(teff)
        for lam, wl in ((l1, w1), (l2, w2)):
            q_nm = 4.0 * math.pi / lam * np.sin(teff / 2.0) * 1e-9
            val += wl * scatter_fn(q_nm.ravel()).reshape(teff.shape)
        acc += gw * (val * polarisation(teff) * lw[None, :]).sum(1)
    return flux * detector_efficiency() * STRIP_PITCH_M * cosi ** 3 / sdd_m ** 2 * acc


def direct_rates(arm_rad, zero_m, collimation, flux_transmitted, stop_centre_m):
    """Count rate of the transmitted primary beam on each strip (arm at 0): trapezoidal profile centred at the true zero, the part of the beam
    length seen by the 8 mm strips, attenuated 1e-3 where the semitransparent stop (centred on the beam, shifted by a fault) covers the strip."""
    if abs(arm_rad) > 1e-9:
        return np.zeros(STRIPS)
    capture = STRIP_LENGTH_M / (LINE_LENGTH_M if collimation == 'line' else STRIP_LENGTH_M)
    frac = beam_fraction(STRIP_S - STRIP_PITCH_M / 2, STRIP_S + STRIP_PITCH_M / 2, zero_m)
    stop = np.where(np.abs(STRIP_S - stop_centre_m) < BEAMSTOP_HALF_M, BEAMSTOP_T, 1.0)
    return flux_transmitted * detector_efficiency() * capture * frac * stop


def parasitic_fn(collimation):
    amp = PARASITIC_LINE if collimation == 'line' else PARASITIC_POINT
    return lambda q_nm: amp * (np.clip(q_nm, 0.02, None) / 0.05) ** -3.0 * np.exp(-q_nm / 1.5)


def smear_curve(q_nm, i_true_fn, collimation, sdd_m=SDD_NOMINAL_M):
    """Slit-length smeared intensity at q (small-angle form): int W(dy) I(sqrt(q^2 + t^2)) / int W, t = (4 pi / lambda) sin(atan(dy/sdd)/2)."""
    lx, lw = length_kernel(collimation)
    lam = mean_wavelength_m()
    t = 4.0 * math.pi / lam * np.sin(np.arctan2(lx, sdd_m) / 2.0) * 1e-9
    qq = np.sqrt(np.asarray(q_nm)[:, None] ** 2 + t[None, :] ** 2)
    return (i_true_fn(qq.ravel()).reshape(qq.shape) * lw[None, :]).sum(1) / lw.sum()


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c) if isinstance(c, (list, tuple)) else [c], function_zh=meaning,
                   visibility=visibility, kind=kind, effects=[])


HOLDERS = ['capillary_1mm', 'mu_cell', 'paste_cell', 'solid_holder']
HOLDER_PATH_M = {'capillary_1mm': CAPILLARY_D_M, 'mu_cell': 1.0e-3, 'paste_cell': 1.0e-3, 'solid_holder': None, 'flow_cell': CAPILLARY_D_M}
HOLDER_VOLUME_M3 = {'capillary_1mm': CAPILLARY_VOLUME_M3, 'mu_cell': MU_CELL_VOLUME_M3, 'paste_cell': None, 'solid_holder': None}
STAGE_TYPES = ['tcstage_peltier', 'tcstage_extended']
FORMS = ['solution', 'dispersion', 'solid', 'powder']
MODELS = ['model_spheres', 'model_lamellar', 'model_hexagonal']
FAULTS = ['vacuum_leak', 'tube_aged', 'beamstop_misaligned', 'capillary_bubble', 'chiller_failure']
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 308.15), mains_voltage_v=number('V', 0.0, 260.0), noise_seed=INT))
COMPONENT = obj(dict(template_id=TEXT, concentration_kg_m3=nullable(number('kg/m^3', 0.0, 500.0)), model_parameters=nullable({'type': 'object'})))
SAMPLE = obj(dict(sample_id=dict(type='string', minLength=1, maxLength=40), form=enum(FORMS), solvent=enum(['water', 'none']),
                  volume_m3=number('m^3', 0.0, 2.0e-6), thickness_m=nullable(number('m', 1.0e-5, 5.0e-3)), components=array(COMPONENT, 0, 4)))
SAMPLE_EXAMPLE = dict(sample_id='lysozyme_5', form='solution', solvent='water', volume_m3=200e-9, thickness_m=None,
                      components=[dict(template_id='sasbdb_sasdrm8_lysozyme_c', concentration_kg_m3=5.0, model_parameters=None)])
OPERATION = obj(dict(operation_id=TEXT))
SOURCE = obj(dict(library=TEXT, note_zh=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
SEGMENT = obj(dict(arm_angle_rad=number('rad'), counts=array(integer(0, COUNTER_MAX * 1000), STRIPS, STRIPS), two_theta_rad=array(number('rad'), STRIPS, STRIPS),
                   q_per_m=array(number('1/m', 0.0), STRIPS, STRIPS)))
FRAME = obj(dict(record_id=TEXT, status=STATUS, kind=enum(['saxs', 'swaxs']), collimation=enum(['line', 'point']), exposure_s=number('s'), frames=integer(1, 1000),
                 sample_position=enum(['capillary', 'flow_cell', 'between']), holder=nullable(TEXT), sample_label=nullable(TEXT),
                 temperature_indicated_k=nullable(number('K')), pressure_indicated_pa=nullable(number('Pa', 0.0)), voltage_v=number('V', 0.0),
                 current_a=number('A', 0.0), segments=array(SEGMENT, 0, ARM_POSITIONS_MAX), primary_beam_counts=nullable(number('1', 0.0)),
                 saturated_strips=INT, sdd_indicated_m=number('m'), zero_indicated_m=number('m'), error_code=nullable(TEXT), started_s=number('s'),
                 finished_s=nullable(number('s')), template_source=SOURCE))
REDUCED = obj(dict(reduced_id=TEXT, record_id=TEXT, background_record_id=nullable(TEXT), calibration_id=nullable(TEXT), absolute=BOOL, desmeared=BOOL,
                   thickness_m=number('m', 0.0), q_per_m=array(number('1/m', 0.0), 0, 600), intensity=array(number('1/m'), 0, 600),
                   sigma=array(number('1/m', 0.0), 0, 600), intensity_unit=enum(['1/m', 'relative']), transmission_ratio=nullable(number('1', 0.0)),
                   q_min_valid_per_m=number('1/m', 0.0), notes_zh=TEXT))
CALIB = obj(dict(calibration_id=TEXT, standard=enum(['water', 'glassy_carbon']), record_id=TEXT, background_record_id=nullable(TEXT), factor=number('1'),
                 relative_uncertainty=number('1', 0.0), temperature_k=nullable(number('K')), q_range_per_m=array(number('1/m', 0.0), 2, 2)))
PEAK = obj(dict(q_per_m=number('1/m', 0.0), d_spacing_m=number('m', 0.0), fwhm_per_m=number('1/m', 0.0), height=number('1'), prominence_sigma=number('1', 0.0)))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'generator', 'vacuum', 'stage', 'sample', 'autosampler', 'acquisition', 'calibration', 'warning', 'error',
                                                'fault']), code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['photons_incident', 'photons_attenuated_air', 'photons_attenuated_windows', 'photons_attenuated_sample', 'photons_direct_stopped',
               'photons_direct_off_detector', 'photons_direct_undetected', 'photons_direct_detected', 'photons_scattered_detected', 'photons_residual', 'liquid_provided_m3', 'liquid_on_bench_m3', 'liquid_in_holder_m3',
               'liquid_in_wells_m3', 'liquid_in_flow_cell_m3', 'liquid_waste_m3', 'liquid_residual_m3', 'solute_provided_kg', 'solute_on_bench_kg',
               'solute_in_holder_kg', 'solute_in_wells_kg', 'solute_in_flow_cell_kg', 'solute_waste_kg', 'solute_residual_kg', 'tube_energy_j', 'tube_on_time_s']
LEDGER = obj({k: number('1' if k.startswith('photons') else 'm^3' if k.endswith('_m3') else 'kg' if k.endswith('_kg') else 'J' if k.endswith('_j') else 's')
              for k in LEDGER_KEYS})
WELL = dict(type='string', pattern='^[AB][A-H](0[1-9]|1[0-2])$', maxLength=4)

cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['generator_cabinet', 'chiller_cabinet'],
    '宿主声明实验室条件：室温、供电电压（额定值未公开，按230V±10%工程判断）与噪声随机数种子（只在首次声明时生效；决定仪器的真实样品到探测器距离与零点误差、毛细管安装偏差与计数噪声）',
    'evaluator', 'configuration')
cap('sample.provide', SAMPLE, obj(dict(sample_id=TEXT)), SAMPLE_EXAMPLE, ['capillary_holder'],
    '宿主在实验台上放入一个样品：溶液/分散液（水或缓冲液，模板库物质与浓度kg/m³=mg/mL）、固体（厚度）或粉末；模板=SASBDB实测曲线、NIST玻璃碳、山嵛酸银、硅粉、NIST金纳米颗粒'
    '或用户参数模型（球、层状、六方孔）', 'evaluator', 'configuration')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT))), dict(fault='vacuum_leak', active=True), ['sample_chamber'],
    '故障注入（评估者）：真空泄漏、光管老化（通量×0.6）、束挡偏移0.5mm（主束直射探测器）、毛细管气泡（束内光程×0.4）、冷却水机停机', 'evaluator', 'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['electronics_rack', 'chiller_cabinet'],
    '主电源：接通后约60s SAXSdrive启动，真空泵、冷却水机（约30s建立流量）、探测器与样品台控制器上电；断开时高压关闭、快门关闭、泵停止（光路缓慢漏气）')
cap('system.acknowledge', obj({}), obj(dict(acknowledged=TEXT)), {}, 'monitor',
    '确认并清除系统级错误（MAINS_FAULT：供电恢复后；SAMPLE_BURST：破裂的样品架已取出后）')
cap('generator.set_key', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['hv_enable_selector', 'key_lock_body'],
    '发生器钥匙开关：I位允许开高压，0位禁止并立即关高压（钥匙由管理员保管）', kind='robot_skill')
cap('generator.xray_on', obj({}), obj(dict(xray_on=BOOL)), {}, ['button_xray_on', 'generator_module'],
    'X-RAY ON键：须通电、钥匙在I、急停释放、无待确认故障、冷却水流量正常；高压按2kV/s、管电流按5mA/s升到设定值（工程），警示灯亮')
cap('generator.xray_off', obj({}), obj(dict(xray_on=BOOL)), {}, ['button_xray_off', 'generator_module'], 'X-RAY OFF键：关闭高压（快门随之关闭，运行中的采集失败）')
cap('generator.acknowledge', obj({}), obj(dict(acknowledged=TEXT)), {}, ['button_standby', 'generator_module'],
    'STANDBY键：确认并清除发生器待确认故障（急停须已释放；冷却水不足须已恢复）')
cap('generator.emergency_stop', obj({}), obj(dict(latched=BOOL)), {}, ['button_emergency_stop', 'generator_module'], '急停按钮：立即切断高压、关闭快门并自锁')
cap('generator.release_emergency_stop', obj({}), obj(dict(latched=BOOL)), {}, ['button_emergency_stop'], '旋转释放急停（之后须按STANDBY确认）', kind='robot_skill')
cap('generator.set_power', obj(dict(voltage_v=number('V', 20000.0, KV_MAX_V), current_a=number('A', 0.005, MA_MAX_A))),
    obj(dict(voltage_v=number('V'), current_a=number('A'))), dict(voltage_v=KV_DEFAULT_V, current_a=MA_DEFAULT_A), ['generator_module', 'xray_tube'],
    '设定管电压20–40kV与管电流5–50mA（Cu密封管；已发表SAXSpace方法均为40kV/50mA；额定功率未公开）；高压开着时按斜率升降')
cap('vacuum.set_pump', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['vacuum_hose', 'chiller_cabinet'],
    '抽真空（泵阀开、放气阀关）或放气（放气阀开）：光路与样品室为一个体积；门开着时不能抽；就绪压力<3mbar（Allec等2015）')
cap('chamber.set_door', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['door_sample_chamber', 'sample_chamber'],
    '开/关样品室前门：只在放气后（>0.9atm）能打开；门开着时快门联锁关闭', kind='robot_skill')
cap('stage.mount', obj(dict(stage_type=enum(STAGE_TYPES))), obj(dict(stage_type=TEXT)), dict(stage_type='tcstage_peltier'), ['stage_base', 'tc_stage'],
    '更换插拔式样品台：TCStage帕尔贴（−30…150°C，设施页）或扩展温控台（−150…600°C，厂家范围）；StageMaster自动识别；须门开、样品架空、台温在室温±5K', kind='robot_skill')
cap('sample.load', obj(dict(sample_id=nullable(TEXT), holder=enum(HOLDERS))), obj(dict(sample_id=nullable(TEXT), holder=TEXT)),
    dict(sample_id='lysozyme_5', holder='capillary_1mm'),
    ['capillary_holder', 'door_sample_chamber'],
    '把实验台上的样品装入样品架并放到毛细管座：1mm石英毛细管（液体，取60µL）、µ-Cell（液体，10µL）、PasteCell（粉末/膏体）、固体样品架（膜/板）；sample_id为null时放入空的1mm石英毛细管'
    '（水标定与空管背景用）；须门开、无采集、样品架空',
    kind='robot_skill')
cap('sample.unload', obj({}), obj(dict(sample_id=nullable(TEXT))), {}, ['capillary_holder', 'door_sample_chamber'],
    '取出毛细管座上的样品架（液体进废液，固体/粉末回实验台）；须门开、无采集', kind='robot_skill')
cap('stage.set_temperature', obj(dict(temperature_k=number('K', EXTENDED_STAGE_RANGE_K[0], EXTENDED_STAGE_RANGE_K[1]))), obj(dict(temperature_k=number('K'))),
    dict(temperature_k=298.15), ['tc_stage'], '设定样品台温度（TCStage帕尔贴−30…150°C；扩展台−150…600°C）；实际温度按≤10°C/min斜率与20s时间常数逼近，控温±0.1°C（厂家）')
cap('stage.move', obj(dict(y_m=number('m', -STAGE_TRAVEL_M, STAGE_TRAVEL_M), z_m=number('m', -STAGE_TRAVEL_M, STAGE_TRAVEL_M))), obj(dict(y_m=number('m'), z_m=number('m'))),
    dict(y_m=0.0, z_m=0.0), ['stage_y', 'stage_z'], 'StageMaster YZ台移动到目标（±10mm，1mm/s，工程值）：只写目标，位置随step推进；Y=0为毛细管位、Y=−8mm为流通池位')
cap('stage.select_position', obj(dict(position=enum(['capillary', 'flow_cell']))), obj(dict(y_m=number('m'))), dict(position='flow_cell'), ['stage_y'],
    '把毛细管位（Y=0）或流通池位（Y=−8mm）移入光束（Z归零）')
cap('stage.auto_align', obj({}), OPERATION, {}, ['stage_y', 'stage_z', 'detector_arm'],
    'TrueFocus自动对准（异步，约60s）：开快门扫描Y，按透过率最小找到毛细管轴并把Y移到那里；须X射线就绪、门关、真空就绪、探测器在SAXS位')
cap('collimation.set_mode', obj(dict(mode=enum(['line', 'point']))), obj(dict(mode=TEXT)), dict(mode='line'), ['collimation_block'],
    'Kratky块准直切换线准直（20mm线束，通量高、有狭缝长度模糊）或点准直（通量约为线准直的2%，工程值；设施页：线准直可切换为点准直）；须无采集')
cap('detector.move_arm', obj(dict(angle_rad=number('rad', 0.0, ARM_MAX_RAD))), obj(dict(angle_rad=number('rad'))), dict(angle_rad=0.0), ['detector_arm', 'arc_rail'],
    '探测器2θ臂转到目标角（0=SAXS位；最大48.9°时模块外缘到60°，TrueSWAXS）；只写目标，按5°/s转动（工程）')
cap('acquisition.start', obj(dict(exposure_s=number('s', 0.1, 7200.0), frames=integer(1, 1000))), OPERATION, dict(exposure_s=600.0, frames=3),
    ['detector_arm', 'shutter_housing', 'xray_tube'],
    '在当前臂位采集（异步）：总曝光=帧数×单帧曝光，快门只在曝光期间打开；须X射线在设定值、门关、真空就绪（<3mbar）或已放气（空气中测量）、探测器臂静止；返回1280条带计数、'
    '按当前标定换算的2θ与q、主束计数；不给样品真实组成')
cap('acquisition.swaxs_scan', obj(dict(arm_angles_rad=array(number('rad', 0.0, ARM_MAX_RAD), 1, ARM_POSITIONS_MAX), exposure_s=number('s', 0.1, 3600.0))), OPERATION,
    dict(arm_angles_rad=[0.0, 0.18, 0.36, 0.54, 0.72, 0.85], exposure_s=120.0), ['detector_arm', 'shutter_housing'],
    'TrueSWAXS扫描（异步）：臂依次转到各角度（5°/s，到位稳定2s）并采集，覆盖到60°2θ；返回各段计数与2θ/q')
cap('acquisition.result', obj(dict(record_id=TEXT)), FRAME, dict(record_id='sx-1'), 'monitor', '读取采集记录（本调用者可见）', 'agent', 'query')
cap('calibration.zero_angle', obj(dict(record_id=TEXT)), obj(dict(zero_m=number('m'), shift_m=number('m'))), dict(record_id='sx-1'), ['beamstop', 'detector_arm'],
    '零角标定：用一条SAXS位记录里经半透明束挡衰减的主束峰位定零点，更新之后记录的2θ/q换算', 'agent', 'configuration')
cap('calibration.q_scale', obj(dict(record_id=TEXT, calibrant=enum(['silver_behenate']))), obj(dict(sdd_m=number('m'), peaks_used=INT, residual_per_m=number('1/m'))),
    dict(record_id='sx-2', calibrant='silver_behenate'), ['detector_arm'],
    'q标定：用山嵛酸银（d001=5.838nm）记录的各级峰位拟合样品到探测器距离，更新之后记录的q换算', 'agent', 'configuration')
cap('calibration.absolute', obj(dict(record_id=TEXT, background_record_id=nullable(TEXT), standard=enum(['water', 'glassy_carbon']), thickness_m=number('m', 1e-5, 5e-3))),
    CALIB, dict(record_id='sx-3', background_record_id='sx-4', standard='water', thickness_m=1.0e-3), 'monitor',
    '绝对强度标定：水（dΣ/dΩ=(n_e r_e)²k_BTκ_T，扣空毛细管，q 1–3nm⁻¹）或NIST SRM 3600玻璃碳（1.055mm，认证曲线0.08–2.5nm⁻¹，按仪器狭缝核模糊后比较）；返回标定因子', 'agent', 'analysis')
cap('data.reduce', obj(dict(record_id=TEXT, background_record_id=nullable(TEXT), calibration_id=nullable(TEXT), thickness_m=number('m', 1e-5, 5e-3), desmear=BOOL)),
    REDUCED, dict(record_id='sx-5', background_record_id='sx-6', calibration_id='cal-1', thickness_m=1.0e-3, desmear=True), 'monitor',
    '数据处理：按各自透过主束归一、校正立体角与偏振、对数分箱（≤600点，泊松误差）、扣背景、除厚度、乘绝对标定因子（此时单位为1/m）、可做正则化去模糊（线准直：20mm线束×8mm条带核；点准直：8mm条带核）', 'agent', 'analysis')
cap('data.result', obj(dict(reduced_id=TEXT)), REDUCED, dict(reduced_id='red-1'), 'monitor', '读取处理后的曲线（本调用者可见）', 'agent', 'query')
cap('analysis.guinier', obj(dict(reduced_id=TEXT, qrg_max=number('1', 0.5, 1.5))),
    obj(dict(rg_m=number('m', 0.0), rg_sigma_m=number('m', 0.0), i0=number('1/m'), i0_sigma=number('1/m', 0.0), q_min_per_m=number('1/m'), q_max_per_m=number('1/m'), points=INT,
             reduced_chi2=number('1', 0.0))), dict(reduced_id='red-1', qrg_max=1.3), 'monitor', 'Guinier分析：自动选取q·Rg≤qrg_max的低q段拟合lnI–q²，给出回转半径Rg与I(0)', 'agent', 'analysis')
cap('analysis.molecular_weight', obj(dict(reduced_id=TEXT, concentration_kg_m3=number('kg/m^3', 0.01, 500.0), drho_m_m_kg=number('m/kg', 1e9, 1e12))),
    obj(dict(molecular_weight_kg_mol=number('kg/mol', 0.0), i0_per_c=number('m^2/kg'))), dict(reduced_id='red-1', concentration_kg_m3=5.0, drho_m_m_kg=DRHO_M_PROTEIN_M_KG),
    'monitor', '由绝对强度的I(0)/c求分子量：M=I(0)/c·N_A/(Δρ_M)²（蛋白默认Δρ_M=2.2×10¹¹m/kg）；须绝对标定且已去模糊的曲线（线、点准直都有条带长度模糊）', 'agent', 'analysis')
cap('analysis.peaks', obj(dict(reduced_id=TEXT)), obj(dict(peaks=array(PEAK, 0, 16))), dict(reduced_id='red-1'), 'monitor',
    '峰分析：找出高于5σ的散射峰，给出q、d=2π/q与半高宽（层状/六方有序、晶体峰）', 'agent', 'analysis')
cap('asx.set_door', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['door_autosampler'], '开/关自动进样器前门（放样前后）；门开着时不能进样', kind='robot_skill')
cap('asx.load_well', obj(dict(sample_id=TEXT, well=WELL, volume_m3=number('m^3', 1e-8, 1e-6))), obj(dict(well=TEXT, volume_m3=number('m^3'))),
    dict(sample_id='lysozyme_5', well='AA01', volume_m3=100e-9), ['asx_tray', 'door_autosampler'],
    '把实验台上的液体样品移入样品盘孔位（两块96孔板AA01–BH12，共192孔）；须进样器门开', kind='robot_skill')
cap('asx.unload_well', obj(dict(well=WELL)), obj(dict(well=TEXT)), dict(well='AA01'), ['asx_tray'], '取走孔位中的剩余样品（进废液）；须进样器门开', kind='robot_skill')
cap('asx.set_tray_temperature', obj(dict(temperature_k=number('K', ASX_TRAY_MIN_K, 308.15))), obj(dict(temperature_k=number('K'))), dict(temperature_k=283.15),
    ['asx_tray'], '设定样品盘温度（4°C至室温，设施页）；按300s时间常数逼近（工程）')
cap('asx.inject', obj(dict(well=WELL)), OPERATION, dict(well='AA01'), ['asx_gantry_x', 'asx_gantry_y', 'asx_needle_z', 'flow_cell'],
    '自动进样（异步，约33s）：针移到孔位吸取30µL（工程）并推入1mm石英流通池；上一样品残留1%（未清洗）或0.01%（清洗后）混入（工程）；须通电、进样器门关、无采集')
cap('asx.clean', obj({}), OPERATION, {}, ['flow_cell', 'asx_needle_z'], '流通池与针清洗（异步，约110s）：水/清洗液冲洗并吹干，流通池变空')
cap('records.list', obj({}), obj(dict(records=array(TEXT), reduced=array(TEXT), calibrations=array(TEXT))), {}, 'monitor', '列出本调用者可见的记录', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT), dropped=INT)), dict(since_s=0.0), 'monitor', '读取事件与错误日志', 'agent', 'query')
cap('ledger.read', obj({}), LEDGER, {}, 'generator_cabinet', '受限：光子账（入射、空气/窗/样品衰减（按各自光学厚度分摊）、主束被束挡吸收、落在模块外、穿过传感器未探测、被探测；散射探测为衰减的一部分）、液体体积与溶质质量账、光管电能与开机时间', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, templates=array(TEXT))), {}, 'monitor', '受限：本实例用到的模板', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), 'electronics_rack', '推进统一宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'detector.eiger_2d': '2D EIGER R探测器为选配（厂家规格列出），本配置为MYTHEN2 R 1K一维探测器。',
    'stage.gisaxs': 'GI-SAXS掠入射样品台为选配，本资产未实现掠入射几何。',
    'stage.humidity': '湿度样品台为选配，本资产未实现。', 'stage.tensile': '拉伸样品台为选配，本资产未实现。',
    'source.change_anode': '更换Mo靶密封管（厂家：Cu或Mo）需要维修服务；本配置固定为Cu管。',
    'analysis.pddf_modelling': '间接傅里叶变换p(r)、从头建模与形状分析（厂家PCG软件）在本资产未实现（只实现Guinier、分子量与峰位）。',
    'hardware.connect': '没有真实SAXSpace、SAXSdrive或探测器的通讯接口。',
    'robot.contact_handling': '机器人夹持毛细管、样品架与钥匙的接触/力控没有实现与验证（装卸为过程层技能，由宿主或场景代理）。',
    'accuracy.certification': '没有厂家q范围、分辨率与强度的验收或标定证书。',
}
UNSUPPORTED_COMPONENTS = {'detector.eiger_2d': ['detector_arm'], 'stage.gisaxs': ['tc_stage'], 'stage.humidity': ['tc_stage'], 'stage.tensile': ['tc_stage'],
                          'source.change_anode': ['xray_tube'], 'analysis.pddf_modelling': ['monitor'], 'hardware.connect': ['electronics_rack'],
                          'robot.contact_handling': ['capillary_holder'], 'accuracy.certification': ['collimation_block']}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, UNSUPPORTED_COMPONENTS[_k], '不支持：' + _v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


ABS = '绝对压力'
SYS_STATES = ['off', 'booting', 'ready']
GEN_FAULTS = ['EMERGENCY_STOP', 'COOLING_WATER_LOW']
slot('clock.time_s', number('s', 0.0), 's', 'derived', 'electronics_rack', '宿主统一仿真时钟')
slot('plant.configured', BOOL, '1', 'derived', 'electronics_rack', '宿主是否已声明实验室条件')
slot('power.enabled', BOOL, '1', 'derived', 'electronics_rack', '主电源是否接通')
slot('system.state', enum(SYS_STATES), '1', 'derived', 'electronics_rack', 'SAXSdrive状态：off/booting（约60s）/ready')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'monitor', '系统级错误（MAINS_FAULT、SAMPLE_BURST）；无为null')
slot('chiller.flow_ok', BOOL, '1', 'sensor', 'chiller_cabinet', '光管冷却水流量开关：通电约30s后建立流量（冷却水机故障时为false）')
slot('generator.key_on', BOOL, '1', 'derived', 'hv_enable_selector', '钥匙开关是否在I位')
slot('generator.emergency_stop_latched', BOOL, '1', 'derived', 'button_emergency_stop', '急停是否按下自锁')
slot('generator.fault_code', nullable(enum(GEN_FAULTS)), '1', 'derived', 'generator_module', '发生器待确认故障（EMERGENCY_STOP、COOLING_WATER_LOW）；无为null')
slot('generator.xray_on', BOOL, '1', 'derived', 'generator_module', '高压是否开启（含升压过程）')
slot('generator.voltage_setpoint_v', number('V', 20000.0, KV_MAX_V), 'V', 'setpoint', 'generator_module', '管电压设定值')
slot('generator.current_setpoint_a', number('A', 0.005, MA_MAX_A), 'A', 'setpoint', 'generator_module', '管电流设定值')
slot('generator.voltage_indicated_v', nullable(number('V', 0.0)), 'V', 'sensor', 'kv_display_digits', '发生器面板管电压读数（升压中逐渐变化）；断电为null')
slot('generator.current_indicated_a', nullable(number('A', 0.0)), 'A', 'sensor', 'ma_display_digits', '发生器面板管电流读数；断电为null')
slot('shutter.open', BOOL, '1', 'derived', 'shutter_housing', '快门是否打开（只在采集曝光与自动对准时，且联锁满足）')
slot('panel.warning_lamp', BOOL, '1', 'derived', 'warning_lamp', '警示灯杆顶灯（高压开启）')
slot('panel.xray_on_lamp', BOOL, '1', 'derived', 'lamp_xray_on', 'X-RAY ON红灯（高压开启）')
slot('panel.standby_lamp', BOOL, '1', 'derived', 'lamp_standby', 'STANDBY黄灯（有待确认故障或急停自锁）')
slot('panel.xray_off_lamp', BOOL, '1', 'derived', 'lamp_xray_off', 'X-RAY OFF绿灯（通电、高压关、无故障）')
slot('panel.shutter_lamp', BOOL, '1', 'derived', 'lamp_shutter', '光源塔上的快门开指示灯')
slot('vacuum.pump_on', BOOL, '1', 'setpoint', 'vacuum_hose', '抽真空（true）或放气（false）')
slot('vacuum.pressure_indicated_pa', nullable(number('Pa', 0.0)), 'Pa', 'sensor', 'sample_chamber', '光路/样品室真空计示值（5%相对噪声）；断电为null', reference=ABS)
slot('vacuum.ready', BOOL, '1', 'derived', 'sample_chamber', '真空是否就绪（<3mbar，Allec等2015）')
slot('chamber.door_open', BOOL, '1', 'derived', 'door_sample_chamber', '样品室门是否打开')
slot('stage.type', enum(STAGE_TYPES), '1', 'derived', 'tc_stage', '当前样品台（StageMaster识别）')
slot('stage.temperature_setpoint_k', number('K', EXTENDED_STAGE_RANGE_K[0], EXTENDED_STAGE_RANGE_K[1]), 'K', 'setpoint', 'tc_stage', '样品台温度设定值')
slot('stage.temperature_indicated_k', nullable(number('K', 0.0)), 'K', 'sensor', 'temperature_digits', '样品台温度读数（0.01K分辨，0.03K噪声）；断电为null')
slot('stage.y_indicated_m', nullable(number('m')), 'm', 'sensor', 'stage_y', 'YZ台Y读数（1µm分辨）；断电为null')
slot('stage.z_indicated_m', nullable(number('m')), 'm', 'sensor', 'stage_z', 'YZ台Z读数')
slot('stage.moving', BOOL, '1', 'derived', 'stage_y', 'YZ台是否在运动')
slot('holder.type', nullable(enum(HOLDERS)), '1', 'derived', 'capillary_holder', '毛细管座上的样品架类型；无为null')
slot('holder.sample_id', nullable(TEXT), '1', 'derived', 'capillary_holder', '毛细管座上样品的标签；无为null')
slot('holder.broken', BOOL, '1', 'derived', 'capillary_holder', '毛细管/样品架是否已破裂（加热沸腾）')
slot('sample.bench_ids', array(TEXT), '1', 'derived', 'table_top', '实验台上的样品ID')
slot('collimation.mode', enum(['line', 'point']), '1', 'setpoint', 'collimation_block', '准直方式')
slot('detector.arm_target_rad', number('rad', 0.0, ARM_MAX_RAD), 'rad', 'derived', 'detector_arm',
     '探测器臂控制器当前目标角（detector.move_arm写入；SWAXS扫描按程序逐段改写；采集停止或断电时取当前角）')
slot('detector.arm_indicated_rad', nullable(number('rad')), 'rad', 'sensor', 'detector_arm', '探测器臂角度读数（0.001°分辨）；断电为null')
slot('detector.arm_moving', BOOL, '1', 'derived', 'detector_arm', '探测器臂是否在转动')
slot('detector.total_counts', nullable(number('1', 0.0)), '1', 'sensor', 'counts_digits', '当前采集段已累计的探测器计数（期望值取整）；无采集为null')
slot('calibration.sdd_m', number('m', 0.0), 'm', 'derived', 'detector_arm', '当前用于2θ/q换算的样品到探测器距离（标定前为名义0.317m）')
slot('calibration.zero_m', number('m'), 'm', 'derived', 'beamstop', '当前用于换算的主束在模块上的零点位置（标定前为0）')
slot('acquisition.id', nullable(TEXT), '1', 'derived', 'detector_arm', '正在进行的采集/对准记录ID')
slot('acquisition.kind', nullable(enum(['saxs', 'swaxs', 'align'])), '1', 'derived', 'detector_arm', '正在进行的操作类型')
slot('acquisition.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'detector_arm', '进度（0–1，按已完成曝光时间）')
slot('acquisition.remaining_s', nullable(number('s', 0.0)), 's', 'derived', 'exposure_digits', '剩余曝光时间（不含臂转动与稳定）')
slot('acquisition.started_by_other_agent', BOOL, '1', 'derived', 'detector_arm', '当前采集是否由另一个代理发起（其控制权受保护）')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'monitor', '本调用者最近完成的采集记录ID')
slot('result.last_reduced_id', nullable(TEXT), '1', 'derived', 'monitor', '本调用者最近的处理曲线ID')
slot('asx.door_open', BOOL, '1', 'derived', 'door_autosampler', '自动进样器前门是否打开')
slot('asx.tray_temperature_setpoint_k', number('K', ASX_TRAY_MIN_K, 308.15), 'K', 'setpoint', 'asx_tray', '样品盘温度设定值')
slot('asx.tray_temperature_indicated_k', nullable(number('K', 0.0)), 'K', 'sensor', 'asx_tray', '样品盘温度读数；断电为null')
slot('asx.wells', {'type': 'object'}, '1', 'derived', 'asx_tray', '各孔位的样品标签与剩余体积（m³）')
slot('asx.busy', nullable(enum(['inject', 'clean'])), '1', 'derived', 'asx_gantry_x', '自动进样器正在进行的操作')
slot('asx.flow_cell_state', enum(['empty', 'filled', 'rinsed']), '1', 'derived', 'flow_cell', '流通池状态：empty（空：未进样或正在冲洗）/filled（有样品）/rinsed（冲洗完成）')
slot('asx.flow_cell_label', nullable(TEXT), '1', 'derived', 'flow_cell', '流通池中最近注入样品的孔位标签；无为null')
for _k, _u, _c, _m in [('chamber.door_angle_rad', 'rad', 'door_sample_chamber', '样品室门开角（镜像到门关节；0=关）'),
                       ('asx.door_angle_rad', 'rad', 'door_autosampler', '进样器门开角（镜像到门关节）'),
                       ('detector.arm_angle_rad', 'rad', 'detector_arm', '探测器臂真实角度（镜像到2θ臂关节）'),
                       ('stage.y_m', 'm', 'stage_y', 'YZ台Y真值（镜像到Y关节）'), ('stage.z_m', 'm', 'stage_z', 'YZ台Z真值（镜像到Z关节）'),
                       ('asx.gantry_x_m', 'm', 'asx_gantry_x', '进样臂X位置（镜像）'), ('asx.gantry_y_m', 'm', 'asx_gantry_y', '进样臂Y位置（镜像）'),
                       ('asx.needle_z_m', 'm', 'asx_needle_z', '进样针下降量（镜像）'), ('generator.key_angle_rad', 'rad', 'hv_enable_selector', '钥匙转角（0=0位，90°=I位）'),
                       ('truth.pressure_pa', 'Pa', 'sample_chamber', '光路真实压力'), ('truth.stage_temperature_k', 'K', 'tc_stage', '样品台（样品）真实温度'),
                       ('truth.tray_temperature_k', 'K', 'asx_tray', '样品盘真实温度'), ('truth.voltage_v', 'V', 'xray_tube', '真实管电压'),
                       ('truth.current_a', 'A', 'xray_tube', '真实管电流'), ('truth.flux_photons_s', '1/s', 'optics_housing', '样品处Cu Kα光子通量'),
                       ('truth.transmission', '1', 'capillary_holder', '光束中样品（含窗与空气）的真实透过率'), ('truth.sdd_m', 'm', 'detector_arm', '真实样品到探测器距离'),
                       ('truth.zero_m', 'm', 'beamstop', '主束在模块上的真实零点'), ('truth.capillary_offset_m', 'm', 'capillary_holder', '已装毛细管轴相对名义座孔的横向偏差')]:
    slot(_k, number(_u), _u, 'process_truth', _c, _m, 'evaluator', ABS if _u == 'Pa' else None)
slot('truth.samples', {'type': 'object'}, '1', 'process_truth', 'capillary_holder', '全部样品（实验台、样品架、孔位、流通池）的真实组成、浓度、状态（冻结、聚集、破裂）', 'evaluator')
slot('truth.faults', array(TEXT), '1', 'process_truth', 'sample_chamber', '当前注入的故障', 'evaluator')


class _Sum:
    """Kahan-compensated accumulator (exact ledgers over many small steps)."""

    def __init__(self):
        self.s = 0.0
        self.c = 0.0

    def add(self, x):
        y = x - self.c
        t = self.s + y
        self.c = (t - self.s) - y
        self.s = t

    @property
    def value(self):
        return self.s + self.c


LEDGER_FLOWS = ['photons_incident', 'photons_attenuated_air', 'photons_attenuated_windows', 'photons_attenuated_sample', 'photons_direct_stopped',
                'photons_direct_off_detector', 'photons_direct_undetected',
                'photons_direct_detected', 'photons_scattered_detected', 'liquid_provided_m3', 'liquid_waste_m3', 'solute_provided_kg', 'solute_waste_kg',
                'tube_energy_j', 'tube_on_time_s']
DOOR_OPEN_RAD = math.radians(105.0)
ASX_DOOR_OPEN_RAD = math.radians(95.0)
KEY_ON_RAD = math.pi / 2
FC_VOLUME_M3 = 15e-9
ASX_WELL_XY = dict(x0=0.020, dx=0.0171, y0=0.030, dy=0.0135, plate_dy=0.140)    # gantry coordinates of well A01 of plate A and the pitches (engineering)
ASX_FC_XY = (0.230, 0.290)                                                        # gantry coordinates of the injection port (engineering)
ASX_NEEDLE_DOWN_M = 0.055
ASX_Z_SPEED_M_S = 0.02
ASX_ASPIRATE_S = 8.0
ASX_DISPENSE_S = 8.0
ASX_RINSE_S = 100.0


def well_xy(well):
    plate, row, col = well[0], 'ABCDEFGH'.index(well[1]), int(well[2:]) - 1
    return (ASX_WELL_XY['x0'] + col * ASX_WELL_XY['dx'], ASX_WELL_XY['y0'] + row * ASX_WELL_XY['dy'] + (ASX_WELL_XY['plate_dy'] if plate == 'B' else 0.0))


def _content_mass(c):
    return sum(v * c['volume_m3'] for v in c['conc'].values()) if c and c.get('liquid') else 0.0


def _mix(a, b, fa):
    """Mix content b with a fraction fa of content a (volume-weighted concentrations and model-sphere volume fractions); volumes set by the caller."""
    conc = {k: fa * a['conc'].get(k, 0.0) + (1 - fa) * b['conc'].get(k, 0.0) for k in set(a['conc']) | set(b['conc'])}
    comps = {}
    for src, f in ((a, fa), (b, 1.0 - fa)):
        for c in src['components']:
            c = copy.deepcopy(c)
            if c['kind'] == 'model_spheres':
                phi = c['parameters']['volume_fraction'] * f
                if c['key'] in comps:
                    phi += comps[c['key']]['parameters']['volume_fraction']
                c['parameters']['volume_fraction'] = phi
            comps[c['key']] = c
    comps = {k: c for k, c in comps.items() if c['kind'] != 'model_spheres' or c['parameters']['volume_fraction'] > 0.0}
    return dict(b, conc=conc, components=list(comps.values()))


def _model_check(tid, p):
    """Validate the parameters of a user model component (SI) and return them in the internal units (nm, 1e10 cm^-2)."""
    if not isinstance(p, dict):
        raise ActionRejected('MODEL_PARAMETERS_INVALID')

    def num(k, lo, hi):
        v = p.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
            raise ActionRejected('MODEL_PARAMETERS_INVALID')
        return float(v)
    if tid == 'model_spheres':
        if set(p) != {'radius_m', 'rel_sigma', 'volume_fraction', 'material', 'matrix'}:
            raise ActionRejected('MODEL_PARAMETERS_INVALID')
        if p['material'] not in _lib()['materials'] or not (p['matrix'] == 'vacuum' or p['matrix'] in _lib()['materials']):
            raise ActionRejected('UNKNOWN_MATERIAL')
        return dict(radius_nm=num('radius_m', 0.5e-9, 100e-9) * 1e9, rel_sigma=num('rel_sigma', 0.02, 0.5), volume_fraction=num('volume_fraction', 1e-6, 0.45),
                    material=p['material'], matrix=p['matrix'])
    if tid == 'model_lamellar':
        if set(p) != {'d_spacing_m', 'layer_thickness_m', 'domain_layers', 'volume_fraction', 'contrast_per_m2'}:
            raise ActionRejected('MODEL_PARAMETERS_INVALID')
        d = num('d_spacing_m', 1e-9, 100e-9)
        return dict(d_spacing_nm=d * 1e9, layer_thickness_nm=num('layer_thickness_m', 0.2e-9, d) * 1e9, domain_layers=num('domain_layers', 2, 200),
                    volume_fraction=num('volume_fraction', 0.05, 0.95), contrast_1e10_cm2=num('contrast_per_m2', 1e11, 5e15) / 1e14)
    if tid == 'model_hexagonal':
        if set(p) != {'lattice_a_m', 'pore_radius_m', 'domain_cells', 'porosity', 'contrast_per_m2'}:
            raise ActionRejected('MODEL_PARAMETERS_INVALID')
        a = num('lattice_a_m', 2e-9, 50e-9)
        return dict(lattice_a_nm=a * 1e9, pore_radius_nm=num('pore_radius_m', 0.5e-9, 0.49 * a) * 1e9, domain_cells=num('domain_cells', 2, 200),
                    porosity=num('porosity', 0.05, 0.9), contrast_1e10_cm2=num('contrast_per_m2', 1e11, 5e15) / 1e14)
    raise ActionRejected('UNKNOWN_TEMPLATE')


def _well_ok(well):
    return len(well) == 4 and well[0] in 'AB' and well[1] in 'ABCDEFGH' and well[2:].isdigit() and 1 <= int(well[2:]) <= 12


class AntonpaarSaxspaceRuntime:
    """SAXSpace: power, generator and interlocks, vacuum, TCStage, holders, collimation, detector arm, acquisitions, reduction, ASX (SI units)."""

    def __init__(self, instance_id='saxspace_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.rng = random.Random(0)                     # gauge noise (fixed draws per cycle)
        self.rng_acq = random.Random(1)                 # counting statistics of the acquisitions
        self.rng_mount = random.Random(2)               # capillary mounting offsets, TrueFocus residuals
        self.rng_cal = random.Random(3)                 # instrument calibration errors (true SDD, zero position, flow-cell offset)
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.error = None
        self.power_t0 = None
        # generator
        self.key_on = False
        self.estop = False
        self.gen_fault = None
        self.xray_on = False
        self.kv_set, self.ma_set = KV_DEFAULT_V, MA_DEFAULT_A
        self.kv, self.ma = 0.0, 0.0
        # vacuum and chamber
        self.p = P_ATM
        self.pump_on = False
        self.door_open = False
        # stage, holders, beam geometry
        self.stage_type = 'tcstage_peltier'
        self.t_set = 293.15
        self.t_stage = 295.15
        self.stage = dict(y=0.0, z=0.0)
        self.stage_target = dict(y=0.0, z=0.0)
        self.holder = None
        self.content = None                              # content of the holder in the capillary seat
        self.cap_offset = 0.0
        self.holder_broken = False
        self.collimation = 'line'
        self.arm = 0.0
        self.arm_target = 0.0
        self.sdd_true, self.zero_true, self.fc_offset = SDD_NOMINAL_M, 0.0, 0.0
        self.sdd_cal, self.zero_cal = SDD_NOMINAL_M, 0.0
        # samples, autosampler
        self.samples = {}
        self.wells = {}
        self.asx_door = False
        self.tray_set = 293.15
        self.tray_t = 295.15
        self.gantry = dict(x=0.0, y=0.0, z=0.0)
        self.gantry_target = dict(x=0.0, y=0.0, z=0.0)
        self.asx_op = None
        self.flow_cell = None
        self.fc_state = 'empty'
        self.fc_label = None
        # records and operations
        self.records, self.reduced, self.calibrations = {}, {}, {}
        self.seq = dict(sx=0, red=0, cal=0, op=0, al=0)
        self.acq = None
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.used_templates = set()
        self.faults = set()
        self.led = {k: _Sum() for k in LEDGER_FLOWS}
        self.readings = None
        self._rate_cache = {}
        self._open_since = None
        self._flush_t = None
        self._t_now = None
        self.fc_residue = {}
        self.fc_model_residue = []
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
        return AntonpaarSaxspaceSession(self, name)

    def _event(self, kind, code, message, rid=None, t=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.time_s if t is None else t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _room_k(self):
        return self.plant['room_k'] if self.plant else 295.15

    def _chiller_ok(self):
        return bool(self.power and self.power_t0 is not None and self.time_s - self.power_t0 >= CHILLER_START_S - 1e-9 and 'chiller_failure' not in self.faults)

    def _gen_ready(self):
        return self.xray_on and abs(self.kv - self.kv_set) < 0.5 and abs(self.ma - self.ma_set) < 1e-7

    def _flux(self):
        if not (self.power and self.xray_on and self.kv > CU_K_EDGE_EV):
            return 0.0
        f = FLUX_LINE_REF * (self.ma / MA_DEFAULT_A) * ((self.kv / CU_K_EDGE_EV - 1.0) / (KV_DEFAULT_V / CU_K_EDGE_EV - 1.0)) ** CHAR_EXPONENT
        if self.collimation == 'point':
            f *= POINT_FLUX_FRACTION
        if 'tube_aged' in self.faults:
            f *= 0.6
        return f

    def _vac_ready(self):
        return self.p < VACUUM_READY_PA

    def _stage_moving(self):
        return any(abs(self.stage[k] - self.stage_target[k]) > 1e-12 for k in self.stage)

    def _arm_moving(self):
        return abs(self.arm - self.arm_target) > 1e-12

    def _t_range(self):
        return TCSTAGE_RANGE_K if self.stage_type == 'tcstage_peltier' else EXTENDED_STAGE_RANGE_K

    def _lamps(self):
        return dict(warning=self.xray_on, xray_on=self.xray_on, standby=bool(self.power and (self.gen_fault is not None or self.estop)),
                    xray_off=bool(self.power and not self.xray_on and self.gen_fault is None and not self.estop), shutter=self._shutter_open())

    def _shutter_open(self):
        return self._open_since is not None

    def _beam_position(self):
        """Which holder position the beam passes (stage Y relative to the capillary and flow-cell axes)."""
        y = self.stage['y']
        if abs(y - self.cap_offset) < CAPILLARY_D_M:
            return 'capillary'
        if abs(y - (FLOW_CELL_Y_M + self.fc_offset)) < CAPILLARY_D_M:
            return 'flow_cell'
        return 'between'

    # ---------------------------------------------------------------- contents
    def _content_of(self, s, volume=None):
        c = dict(label=s['sample_id'], form=s['form'], solvent=s['solvent'], liquid=s['form'] in ('solution', 'dispersion'), thickness_m=s['thickness_m'],
                 volume_m3=s['volume_m3'] if volume is None else volume, components=copy.deepcopy(s['components']), conc=dict(s['conc']),
                 frozen=False, agg={k: 0.0 for k in s['conc']}, burst=False)
        return c

    def _beam_content(self):
        pos = self._beam_position()
        if pos == 'capillary' and self.content is not None and not self.holder_broken:
            return pos, self.holder, self.content
        if pos == 'flow_cell' and self.flow_cell is not None:
            return pos, 'flow_cell', self.flow_cell
        return pos, None, None

    def _mu_content(self, c):
        """Linear attenuation coefficient (1/m) of a content at Cu Ka."""
        if c is None or c['form'] == 'empty':
            return 0.0
        if c['liquid']:
            mu = material('water')['mu_cu_ka_per_cm'] * 100.0
            phi = sum(c['conc'].get(k['key'], 0.0) * (template(k['template_id'])['_vbar_ml_mg'] if k['kind'] == 'curve' else 1e-3 / 19.32)
                      for k in c['components'] if k['kind'] in ('curve', 'sphere_model'))
            extra = 0.0
            for k in c['components']:
                if k['kind'] == 'sphere_model':
                    extra += c['conc'].get(k['key'], 0.0) * 1e-3 / 19.32 * material('gold')['mu_cu_ka_per_cm'] * 100.0
            return mu * (1.0 - min(0.5, phi)) + material('protein (average)')['mu_cu_ka_per_cm'] * 100.0 * min(0.5, phi) + extra
        k = c['components'][0]
        if k['kind'] == 'certified_curve':
            return template(k['template_id'])['material']['mu_cu_ka_per_cm'] * 100.0
        if k['kind'] in ('lamellar_powder', 'crystal_powder'):
            return template(k['template_id'])['material']['mu_cu_ka_per_cm'] * 100.0 * 0.5          # powder packing fraction 0.5 (assumption)
        if k['kind'] == 'model_spheres':
            p = k['parameters']
            mm = material(p['matrix'])['mu_cu_ka_per_cm'] * 100.0 if p['matrix'] != 'vacuum' else 0.0
            return p['volume_fraction'] * material(p['material'])['mu_cu_ka_per_cm'] * 100.0 + (1 - p['volume_fraction']) * mm
        return material('silica')['mu_cu_ka_per_cm'] * 100.0 * 0.5

    def _geometry(self):
        """Beam-averaged sample path, window path and transmissions for the content in the beam (Gauss-Hermite over the beam width)."""
        pos, holder, c = self._beam_content()
        t_air = math.exp(-mu_air_per_m(self.p, self._room_k()) * AIR_PATH_M)
        if c is None:
            return dict(pos=pos, holder=holder, content=None, t_air=t_air, t_total=t_air, scat_path_m=0.0, win_path_m=0.0, win='none', t_sample=1.0,
                        att_sample=0.0, att_window=0.0)
        mu = self._mu_content(c)
        if holder in ('capillary_1mm', 'flow_cell', 'mu_cell'):
            axis = self.cap_offset if holder != 'flow_cell' else FLOW_CELL_Y_M + self.fc_offset
            r = CAPILLARY_D_M / 2.0 - CAPILLARY_WALL_M
            mu_w = material('silica')['mu_cu_ka_per_cm'] * 100.0
            T = sp = wp = att_s = att_w = 0.0
            for x, w in zip(_BW_X, _BW_W):
                y = self.stage['y'] + x - axis
                d = 2.0 * math.sqrt(max(0.0, r * r - y * y))
                if holder == 'capillary_1mm' and 'capillary_bubble' in self.faults:
                    d *= 0.4
                ro = r + CAPILLARY_WALL_M
                wall = 2.0 * (math.sqrt(max(0.0, ro * ro - y * y)) - math.sqrt(max(0.0, r * r - y * y)))
                a_s, a_w = mu * d, mu_w * wall
                tj = math.exp(-a_s - a_w)
                T += w * tj
                sp += w * d * tj
                wp += w * wall * tj
                if a_s + a_w > 0.0:                               # absorbed / scattered share of each medium from its optical depth
                    att_s += w * (1.0 - tj) * a_s / (a_s + a_w)
                    att_w += w * (1.0 - tj) * a_w / (a_s + a_w)
            win = 'silica'
        else:
            d = c['thickness_m'] if holder == 'solid_holder' else HOLDER_PATH_M[holder]
            wall = 2 * 25e-6 if holder == 'paste_cell' else 0.0
            mu_w = material('kapton')['mu_cu_ka_per_cm'] * 100.0 if wall else 0.0
            a_s, a_w = mu * d, mu_w * wall
            T = math.exp(-a_s - a_w)
            sp, wp = d * T, wall * T
            att_s = (1.0 - T) * a_s / (a_s + a_w) if a_s + a_w > 0.0 else 0.0
            att_w = (1.0 - T) * a_w / (a_s + a_w) if a_s + a_w > 0.0 else 0.0
            win = 'kapton' if wall else 'none'
        return dict(pos=pos, holder=holder, content=c, t_air=t_air, t_total=T * t_air, t_sample=T, scat_path_m=sp * t_air, win_path_m=wp * t_air, win=win, mu=mu,
                    att_sample=att_s, att_window=att_w)

    def _scatter_fn(self, g):
        """d x dSigma/dOmega (per sr, dimensionless) of everything in the beam except the attenuation (applied through the paths)."""
        c = g['content']
        temp = self.t_stage
        p, room = self.p, self._room_k()
        par = parasitic_fn(self.collimation)
        air_len_cm = AIR_SCATTER_LENGTH_M * 100.0

        def fn(q):
            out = (air_cm(q, p, room) * air_len_cm * g['t_air'] + par(q)) * g['t_sample']      # air around and collimator upstream of the sample
            if g['win_path_m'] > 0:
                out = out + (glass_cm(q) if g['win'] == 'silica' else kapton_cm(q)) * g['win_path_m'] * 100.0
            if c is not None and g['scat_path_m'] > 0:
                out = out + self._content_cm(c, q, temp) * g['scat_path_m'] * 100.0
            return out
        return fn

    def _content_cm(self, c, q, temp):
        out = np.zeros_like(q)
        phi_tot = 0.0
        for k in c['components']:
            comp = dict(k)
            if c['liquid']:
                comp['concentration_mg_ml'] = c['conc'].get(k['key'], 0.0)
                comp['aggregated_fraction'] = c['agg'].get(k['key'], 0.0)
                if k['kind'] == 'curve':
                    phi_tot += comp['concentration_mg_ml'] * template(k['template_id'])['_vbar_ml_mg']
                elif k['kind'] == 'sphere_model':
                    phi_tot += comp['concentration_mg_ml'] * 1e-3 / 19.32
            out = out + component_cm(comp, q, temp, phi_tot)
        if c['liquid'] and c['solvent'] == 'water':
            solv = ice_cm(q) if c['frozen'] else water_cm(q, temp)
            out = out + solv * (1.0 - min(0.5, phi_tot))
        return out

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        m = self.readings if self.power else None
        acq = self.acq
        pos, holder, c = self._beam_content()
        wells = {w: dict(label=v['label'], volume_m3=v['volume_m3']) for w, v in sorted(self.wells.items())}
        lam = self._lamps()
        rem = None
        if acq is not None and acq['kind'] != 'align':
            rem = max(0.0, acq['exposure_total'] - acq['open_done'] - self._open_elapsed())
        g = self._geometry()
        values = {
            'clock.time_s': self.time_s, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.error_code': self.error, 'chiller.flow_ok': self._chiller_ok(), 'generator.key_on': self.key_on,
            'generator.emergency_stop_latched': self.estop, 'generator.fault_code': self.gen_fault, 'generator.xray_on': self.xray_on,
            'generator.voltage_setpoint_v': self.kv_set, 'generator.current_setpoint_a': self.ma_set,
            'generator.voltage_indicated_v': m['kv'] if m else None, 'generator.current_indicated_a': m['ma'] if m else None,
            'shutter.open': self._shutter_open(), 'panel.warning_lamp': lam['warning'], 'panel.xray_on_lamp': lam['xray_on'],
            'panel.standby_lamp': lam['standby'], 'panel.xray_off_lamp': lam['xray_off'], 'panel.shutter_lamp': lam['shutter'],
            'vacuum.pump_on': self.pump_on, 'vacuum.pressure_indicated_pa': m['p'] if m else None, 'vacuum.ready': self._vac_ready(),
            'chamber.door_open': self.door_open, 'stage.type': self.stage_type, 'stage.temperature_setpoint_k': self.t_set,
            'stage.temperature_indicated_k': m['t'] if m else None,
            'stage.y_indicated_m': round(self.stage['y'], 6) if self.power else None, 'stage.z_indicated_m': round(self.stage['z'], 6) if self.power else None,
            'stage.moving': self._stage_moving(), 'holder.type': self.holder, 'holder.sample_id': self.content['label'] if self.content else None,
            'holder.broken': self.holder_broken, 'sample.bench_ids': sorted(k for k, s in self.samples.items() if s['bench_volume_m3'] > 0 or not s['liquid']
                                                                             and s['location'] == 'bench'),
            'collimation.mode': self.collimation, 'detector.arm_target_rad': self.arm_target,
            'detector.arm_indicated_rad': round(self.arm / math.radians(0.001)) * math.radians(0.001) if self.power else None,
            'detector.arm_moving': self._arm_moving(), 'detector.total_counts': self._counts_now() if (acq and acq['kind'] != 'align') else None,
            'calibration.sdd_m': self.sdd_cal, 'calibration.zero_m': self.zero_cal,
            'acquisition.id': acq['record_id'] if acq else None, 'acquisition.kind': acq['kind'] if acq else None,
            'acquisition.progress': self._progress() if acq else None, 'acquisition.remaining_s': rem, 'acquisition.started_by_other_agent': False,
            'result.last_record_id': None, 'result.last_reduced_id': None,
            'asx.door_open': self.asx_door, 'asx.tray_temperature_setpoint_k': self.tray_set, 'asx.tray_temperature_indicated_k': m['tray'] if m else None,
            'asx.wells': wells, 'asx.busy': self.asx_op['kind'] if self.asx_op else None, 'asx.flow_cell_state': self.fc_state, 'asx.flow_cell_label': self.fc_label,
            'chamber.door_angle_rad': DOOR_OPEN_RAD if self.door_open else 0.0, 'asx.door_angle_rad': ASX_DOOR_OPEN_RAD if self.asx_door else 0.0,
            'detector.arm_angle_rad': self.arm, 'stage.y_m': self.stage['y'], 'stage.z_m': self.stage['z'], 'asx.gantry_x_m': self.gantry['x'],
            'asx.gantry_y_m': self.gantry['y'], 'asx.needle_z_m': self.gantry['z'], 'generator.key_angle_rad': KEY_ON_RAD if self.key_on else 0.0,
            'truth.pressure_pa': self.p, 'truth.stage_temperature_k': self.t_stage, 'truth.tray_temperature_k': self.tray_t, 'truth.voltage_v': self.kv,
            'truth.current_a': self.ma, 'truth.flux_photons_s': self._flux(), 'truth.transmission': g['t_total'], 'truth.sdd_m': self.sdd_true,
            'truth.zero_m': self.zero_true, 'truth.capillary_offset_m': self.cap_offset,
            'truth.samples': dict(bench={k: dict(form=s['form'], solvent=s['solvent'], components=copy.deepcopy(s['components']), conc=dict(s['conc']),
                                                 volume_m3=s['bench_volume_m3'], thickness_m=s['thickness_m'], location=s['location']) for k, s in self.samples.items()},
                                  holder=self._truth_content(self.content), flow_cell=self._truth_content(self.flow_cell),
                                  wells={w: self._truth_content(v) for w, v in self.wells.items()}),
            'truth.faults': sorted(self.faults),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _counts_now(self):
        """Expected counts of the running segment at this instant: flushed counts + the open time since the last flush at the present rates (a flush at a
        command therefore does not change the reading)."""
        a = self.acq
        extra = 0.0
        if self._open_since is not None and self._flush_t is not None and self.time_s > self._flush_t:
            sc, dr, _ = self._rates(self.arm)
            extra = float((sc + dr).sum()) * (self.time_s - self._flush_t)
        return float(round(a['counts_so_far'] + extra))

    @staticmethod
    def _truth_content(c):
        if c is None:
            return None
        return dict(label=c['label'], form=c['form'], solvent=c['solvent'], volume_m3=c['volume_m3'], thickness_m=c['thickness_m'],
                    conc=dict(c['conc']), templates=[k['template_id'] for k in c['components']], frozen=c['frozen'], aggregated=dict(c['agg']), burst=c['burst'])

    def _open_elapsed(self):
        return (self.time_s - self._open_since) if self._open_since is not None else 0.0

    def _progress(self):
        a = self.acq
        if a['kind'] == 'align':
            return min(1.0, (self.time_s - a['t0']) / ALIGN_S)
        return min(1.0, (a['open_done'] + self._open_elapsed()) / a['exposure_total'])

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
            self._flush()                                   # counts accumulated so far are integrated with the state before the command
        out = fn(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):
            self._check_overexposure()                      # e.g. a beam stop pushed off the beam during an exposure
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

    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed):
        first = self.plant is None
        if not first:
            noise_seed = self.plant['noise_seed']
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, noise_seed=noise_seed)
        if first:
            for i, r in enumerate((self.rng, self.rng_acq, self.rng_mount, self.rng_cal)):
                r.seed(noise_seed * 7919 + i)
            self.sdd_true = SDD_NOMINAL_M + self.rng_cal.gauss(0.0, SDD_SD_M)
            self.zero_true = self.rng_cal.gauss(0.0, ZERO_SD_M)
            self.fc_offset = self.rng_cal.gauss(0.0, FLOW_CELL_OFFSET_SD_M)
            self.t_stage = room_k
            self.tray_t = room_k
        if self.power and abs(mains_voltage_v - 230.0) > 23.0 + 1e-9:
            self._power_off()
            self.error = 'MAINS_FAULT'
            self._event('error', 'MAINS_FAULT', '供电电压超出230V±10%：系统断电')
        return dict(configured=True)

    def _cmd_sample_provide(self, sample_id, form, solvent, volume_m3, thickness_m, components):
        self._need_plant()
        if sample_id in self.samples or len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('SAMPLE_EXISTS')
        liquid = form in ('solution', 'dispersion')
        if liquid != (solvent == 'water') or (liquid and (volume_m3 <= 0 or thickness_m is not None)) or (not liquid and (thickness_m is None or volume_m3 != 0.0)):
            raise ActionRejected('SAMPLE_FORM_INVALID')
        comps, conc, keys = [], {}, set()
        for i, k in enumerate(components):
            tid = k['template_id']
            if tid in MODELS:
                if k['model_parameters'] is None or (liquid and tid != 'model_spheres'):
                    raise ActionRejected('MODEL_PARAMETERS_INVALID')
                params = _model_check(tid, k['model_parameters'])
                if liquid and (params['matrix'] != 'water' or k['concentration_kg_m3'] is not None):
                    raise ActionRejected('MODEL_PARAMETERS_INVALID')
                kind = {'model_spheres': 'model_spheres', 'model_lamellar': 'model_lamellar', 'model_hexagonal': 'model_hexagonal'}[tid]
                key = f'{tid}#{sample_id}#{i}'
                comps.append(dict(key=key, template_id=tid, kind=kind, parameters=params))
                continue
            t = template(tid)
            kind = t['kind']
            if k['model_parameters'] is not None:
                raise ActionRejected('MODEL_PARAMETERS_INVALID')
            if kind in ('curve', 'sphere_model'):
                if not liquid or k['concentration_kg_m3'] is None or k['concentration_kg_m3'] <= 0:
                    raise ActionRejected('CONCENTRATION_REQUIRED')
                conc[tid] = k['concentration_kg_m3']
            elif kind == 'certified_curve':
                if liquid or abs(thickness_m - t['thickness_mm'] * 1e-3) > 1e-6 or len(components) != 1:
                    raise ActionRejected('SAMPLE_FORM_INVALID')
            elif kind in ('lamellar_powder', 'crystal_powder'):
                if form != 'powder' or len(components) != 1:
                    raise ActionRejected('SAMPLE_FORM_INVALID')
            elif kind == 'liquid':
                raise ActionRejected('SAMPLE_FORM_INVALID')          # water is the solvent of a liquid sample, not a component
            if tid in keys:
                raise ActionRejected('SAMPLE_FORM_INVALID')
            keys.add(tid)
            comps.append(dict(key=tid, template_id=tid, kind=kind, parameters=None))
        if not liquid and len(comps) != 1:
            raise ActionRejected('SAMPLE_FORM_INVALID')
        if liquid and any(c['kind'] not in ('curve', 'sphere_model', 'model_spheres') for c in comps):
            raise ActionRejected('SAMPLE_FORM_INVALID')
        self.samples[sample_id] = dict(sample_id=sample_id, form=form, solvent=solvent, liquid=liquid, volume_m3=volume_m3, bench_volume_m3=volume_m3,
                                       thickness_m=thickness_m, components=comps, conc=conc, location='bench')
        self.used_templates |= {c['template_id'] for c in comps}
        if liquid:
            self.led['liquid_provided_m3'].add(volume_m3)
            self.led['solute_provided_kg'].add(sum(conc.values()) * volume_m3)
        return dict(sample_id=sample_id)

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        self._rate_cache.clear()
        self._event('fault', 'FAULT_' + fault.upper(), f'评估者{"注入" if active else "清除"}故障{fault}')
        return dict(faults=sorted(self.faults))

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            if abs(self.plant['mains_voltage_v'] - 230.0) > 23.0 + 1e-9:
                raise ActionRejected('MAINS_OUT_OF_RANGE')
            self.power, self.state, self.state_t0, self.power_t0 = True, 'booting', self.time_s, self.time_s
            if self.error == 'MAINS_FAULT':
                self.error = None
            if self.estop and self.gen_fault is None:
                self.gen_fault = 'EMERGENCY_STOP'            # the generator controller finds the emergency-stop chain open at power-up
            self._event('power', 'POWER_ON', '主电源接通，SAXSdrive启动中')
        elif not enabled and self.power:
            self._power_off()
            self._event('power', 'POWER_OFF', '主电源断开')
        return dict(enabled=self.power)

    def _power_off(self):
        self._stop_acq('failed', 'POWER_OFF')
        self._stop_asx('POWER_OFF')
        self.power, self.state, self.power_t0 = False, 'off', None
        self.xray_on, self.kv, self.ma = False, 0.0, 0.0
        self.readings = None
        self.stage_target = dict(self.stage)
        self.arm_target = self.arm
        self.gantry_target = dict(self.gantry)

    def _cmd_system_acknowledge(self):
        self._need_power()
        if self.error is None:
            raise ActionRejected('NO_ERROR')
        if self.error == 'SAMPLE_BURST' and self.holder_broken:
            raise ActionRejected('HOLDER_BROKEN')
        code, self.error = self.error, None
        self._event('power', 'ERROR_ACKNOWLEDGED', f'错误{code}已确认')
        return dict(acknowledged=code)

    def _need_gen_power(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _cmd_generator_set_key(self, on):
        self._need_plant()
        self.key_on = on
        if not on and self.xray_on:
            self._xray_off('KEY_OFF')
        return dict(on=on)

    def _xray_off(self, code):
        if self.acq is not None:
            self._stop_acq('failed', 'XRAY_OFF')
        self.xray_on, self.kv, self.ma = False, 0.0, 0.0
        self._event('generator', 'XRAY_OFF', f'高压关闭（{code}）')

    def _cmd_generator_xray_on(self):
        self._need_ready()
        if not self.key_on:
            raise ActionRejected('KEY_OFF')
        if self.estop:
            raise ActionRejected('EMERGENCY_STOP')
        if self.gen_fault is not None:
            raise ActionRejected('GENERATOR_FAULT')
        if not self._chiller_ok():
            raise ActionRejected('COOLING_WATER_LOW')
        if not self.xray_on:
            self.xray_on = True
            self._event('generator', 'XRAY_ON', '高压开启，按2kV/s、5mA/s升到设定值')
        return dict(xray_on=True)

    def _cmd_generator_xray_off(self):
        self._need_gen_power()
        if self.xray_on:
            self._xray_off('BUTTON')
        return dict(xray_on=False)

    def _cmd_generator_acknowledge(self):
        self._need_gen_power()
        if self.gen_fault is None:
            raise ActionRejected('NO_FAULT')
        if self.estop:
            raise ActionRejected('EMERGENCY_STOP')
        if self.gen_fault == 'COOLING_WATER_LOW' and not self._chiller_ok():
            raise ActionRejected('COOLING_WATER_LOW')
        code, self.gen_fault = self.gen_fault, None
        self._event('generator', 'FAULT_ACKNOWLEDGED', f'故障{code}已确认')
        return dict(acknowledged=code)

    def _cmd_generator_emergency_stop(self):
        self._need_plant()
        self.estop = True
        if self.xray_on:
            self._xray_off('EMERGENCY_STOP')
        if self.power:
            self.gen_fault = 'EMERGENCY_STOP'
        self._event('generator', 'EMERGENCY_STOP', '急停按下：高压切断并自锁')
        return dict(latched=True)

    def _cmd_generator_release_emergency_stop(self):
        self._need_plant()
        if not self.estop:
            raise ActionRejected('NOT_LATCHED')
        self.estop = False
        return dict(latched=False)

    def _cmd_generator_set_power(self, voltage_v, current_a):
        self._need_ready()
        self.kv_set, self.ma_set = voltage_v, current_a
        return dict(voltage_v=voltage_v, current_a=current_a)

    def _cmd_vacuum_set_pump(self, on):
        self._need_power()
        if on and self.door_open:
            raise ActionRejected('DOOR_OPEN')
        self.pump_on = on
        self._event('vacuum', 'PUMP_ON' if on else 'VENT', '开始抽真空' if on else '开始放气')
        return dict(on=on)

    def _cmd_chamber_set_door(self, open):
        self._need_plant()
        if open and not self.door_open:
            if self.p < DOOR_OPEN_MIN_PA:
                raise ActionRejected('CHAMBER_UNDER_VACUUM')
            if self.pump_on and self.power:
                raise ActionRejected('PUMP_ON')
            if self._shutter_open() or (self.acq is not None):
                self._stop_acq('failed', 'INTERLOCK_DOOR')
            self.door_open = True
            self.p = P_ATM
        elif not open:
            self.door_open = False
        return dict(open=self.door_open)

    def _need_door_open(self):
        if not self.door_open:
            raise ActionRejected('DOOR_CLOSED')

    def _cmd_stage_mount(self, stage_type):
        self._need_plant()
        self._need_door_open()
        self._need_idle()
        if self.content is not None or self.holder_broken:
            raise ActionRejected('HOLDER_OCCUPIED')
        if abs(self.t_stage - self._room_k()) > 5.0:
            raise ActionRejected('STAGE_NOT_AMBIENT')
        self.stage_type = stage_type
        lo, hi = self._t_range()
        self.t_set = min(hi, max(lo, self.t_set))
        self._event('stage', 'STAGE_MOUNTED', f'StageMaster识别样品台{stage_type}')
        return dict(stage_type=stage_type)

    def _cmd_sample_load(self, sample_id, holder):
        self._need_plant()
        self._need_door_open()
        self._need_idle()
        if self.content is not None or self.holder_broken:
            raise ActionRejected('HOLDER_OCCUPIED')
        if sample_id is None:
            if holder not in ('capillary_1mm', 'mu_cell'):
                raise ActionRejected('HOLDER_UNSUITABLE')
            self.content = dict(label=None, form='empty', solvent='none', liquid=False, thickness_m=None, volume_m3=0.0, components=[], conc={}, frozen=False,
                                agg={}, burst=False)
            self.holder = holder
            self.cap_offset = self.rng_mount.gauss(0.0, CAP_MOUNT_SD_M)
            self._rate_cache.clear()
            self._event('sample', 'SAMPLE_LOADED', f'空{holder}装入')
            return dict(sample_id=None, holder=holder)
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['liquid']:
            if holder not in ('capillary_1mm', 'mu_cell'):
                raise ActionRejected('HOLDER_UNSUITABLE')
            need = HOLDER_VOLUME_M3[holder]
            if s['bench_volume_m3'] < need - 1e-15:
                raise ActionRejected('INSUFFICIENT_VOLUME')
            s['bench_volume_m3'] -= need
            self.content = self._content_of(s, need)
        else:
            if s['location'] != 'bench':
                raise ActionRejected('SAMPLE_NOT_ON_BENCH')
            if (s['form'] == 'powder') != (holder == 'paste_cell') or (s['form'] == 'solid' and holder != 'solid_holder'):
                raise ActionRejected('HOLDER_UNSUITABLE')
            s['location'] = 'holder'
            self.content = self._content_of(s, 0.0)
        self.holder = holder
        self.cap_offset = self.rng_mount.gauss(0.0, CAP_MOUNT_SD_M) if holder in ('capillary_1mm', 'mu_cell') else 0.0
        self._rate_cache.clear()
        self._event('sample', 'SAMPLE_LOADED', f'样品{sample_id}装入{holder}')
        return dict(sample_id=sample_id, holder=holder)

    def _cmd_sample_unload(self):
        self._need_plant()
        self._need_door_open()
        self._need_idle()
        sid = None
        if self.content is not None:
            sid = self.content['label']
            if self.content['liquid']:
                self.led['liquid_waste_m3'].add(self.content['volume_m3'])
                self.led['solute_waste_kg'].add(_content_mass(self.content))
            elif sid is not None:
                self.samples[sid]['location'] = 'bench'
        elif not self.holder_broken and self.holder is None:
            raise ActionRejected('HOLDER_EMPTY')
        self.content, self.holder, self.holder_broken, self.cap_offset = None, None, False, 0.0
        self._rate_cache.clear()
        return dict(sample_id=sid)

    def _cmd_stage_set_temperature(self, temperature_k):
        self._need_ready()
        lo, hi = self._t_range()
        if not lo - 1e-9 <= temperature_k <= hi + 1e-9:
            raise ActionRejected('TEMPERATURE_OUT_OF_RANGE')
        self.t_set = temperature_k
        return dict(temperature_k=temperature_k)

    def _need_stage(self):
        self._need_ready()
        self._need_idle()

    def _cmd_stage_move(self, y_m, z_m):
        self._need_stage()
        self.stage_target = dict(y=y_m, z=z_m)
        return dict(y_m=y_m, z_m=z_m)

    def _cmd_stage_select_position(self, position):
        self._need_stage()
        y = 0.0 if position == 'capillary' else FLOW_CELL_Y_M
        self.stage_target = dict(y=y, z=0.0)
        return dict(y_m=y)

    def _need_beam_ready(self):
        self._need_ready()
        self._need_idle()
        if self.door_open:
            raise ActionRejected('DOOR_OPEN')
        if not self._gen_ready():
            raise ActionRejected('XRAY_NOT_READY')
        if not (self._vac_ready() or self.p > DOOR_OPEN_MIN_PA):
            raise ActionRejected('VACUUM_NOT_READY')
        if self._stage_moving() or self._arm_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')

    def _new_op(self, kind, rid, **data):
        self.seq['op'] += 1
        op_id = f'op-{self.seq["op"]}'
        op = dict(operation_id=op_id, kind=kind, record_id=rid, phase='running', **data)
        self.operations[op_id] = op
        return op

    def _cmd_stage_auto_align(self):
        self._need_beam_ready()
        if self.arm != 0.0:
            raise ActionRejected('ARM_NOT_AT_SAXS')
        pos, holder, c = self._beam_content()
        self.seq['al'] += 1
        rid = f'align-{self.seq["al"]}'
        op = self._new_op('align', rid)
        self.acq = dict(kind='align', record_id=rid, t0=self.time_s, op=op['operation_id'], target=self._align_target(), exposure_total=ALIGN_S, open_done=0.0,
                        counts_so_far=0.0)
        self._event('stage', 'ALIGN_STARTED', 'TrueFocus自动对准开始')
        self._open_shutter()
        return dict(operation_id=op['operation_id'])

    def _align_target(self):
        """The capillary / flow-cell axis near the current Y (transmission minimum), with a 10 um residual; None when no cylinder is found."""
        y = self.stage['y']
        if self.content is not None and self.holder in ('capillary_1mm', 'mu_cell') and abs(y - self.cap_offset) < 2.5e-3:
            return self.cap_offset + self.rng_mount.gauss(0.0, 10e-6)
        if abs(y - (FLOW_CELL_Y_M + self.fc_offset)) < 2.5e-3:                         # the quartz flow cell (walls) is always there
            return FLOW_CELL_Y_M + self.fc_offset + self.rng_mount.gauss(0.0, 10e-6)
        return None

    def _cmd_collimation_set_mode(self, mode):
        self._need_ready()
        self._need_idle()
        self.collimation = mode
        self._rate_cache.clear()
        return dict(mode=mode)

    def _cmd_detector_move_arm(self, angle_rad):
        self._need_ready()
        self._need_idle()
        self.arm_target = angle_rad
        return dict(angle_rad=angle_rad)

    def _source(self, c):
        """Template library of the record; which templates (and how many) are in the beam is evaluator knowledge (provenance.read, truth)."""
        return dict(library=LIBRARY_REL, note_zh='散射由模板库计算；样品组成与所用模板只对评估者可见（provenance.read）')

    def _cmd_acquisition_start(self, exposure_s, frames):
        self._need_beam_ready()
        return self._start_acq('saxs', [self.arm], exposure_s * frames, frames, exposure_s)

    def _cmd_acquisition_swaxs_scan(self, arm_angles_rad, exposure_s):
        self._need_beam_ready()
        return self._start_acq('swaxs', list(arm_angles_rad), exposure_s * len(arm_angles_rad), 1, exposure_s)

    def _start_acq(self, kind, arms, total, frames, seg_exposure):
        self.seq['sx'] += 1
        rid = f'sx-{self.seq["sx"]}'
        g = self._geometry()
        rec = dict(record_id=rid, status='running', kind=kind, collimation=self.collimation, exposure_s=seg_exposure if kind == 'swaxs' else seg_exposure * frames,
                   frames=frames, sample_position=g['pos'], holder=g['holder'], sample_label=g['content']['label'] if g['content'] else None,
                   temperature_indicated_k=None, pressure_indicated_pa=None, voltage_v=self.kv, current_a=self.ma, segments=[], primary_beam_counts=None,
                   saturated_strips=0, sdd_indicated_m=self.sdd_cal, zero_indicated_m=self.zero_cal, error_code=None, started_s=self.time_s, finished_s=None,
                   template_source=self._source(g['content']), _temps=[], _press=[], _truth=self._truth_content(g['content']))
        self.records[rid] = rec
        op = self._new_op('acquisition', rid)
        seg_t = seg_exposure if kind == 'swaxs' else seg_exposure * frames
        self.acq = dict(kind=kind, record_id=rid, t0=self.time_s, op=op['operation_id'], plan=[dict(arm=a, exposure=seg_t) for a in arms], index=0,
                        phase='moving', settle_until=None, exposure_total=total, open_done=0.0, seg_open=0.0, lam=np.zeros(STRIPS), lam_direct=np.zeros(STRIPS),
                        max_rate=0.0, counts_so_far=0.0)
        self.arm_target = arms[0]
        self._event('acquisition', 'ACQ_STARTED', f'采集{rid}开始（{kind}，{len(arms)}段）', rid)
        self._advance_acq_phase()
        return dict(operation_id=op['operation_id'])

    def _cmd_acquisition_result(self, record_id):
        if record_id not in self.records:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._out(self.records[record_id])

    @staticmethod
    def _out(rec):
        return {k: copy.deepcopy(v) for k, v in rec.items() if not k.startswith('_')}

    # ---------------------------------------------------------------- acquisition engine
    def _rates(self, arm):
        """(scattered, direct) expected count rates per strip for the present state, cached on a quantised state key (pressure in 2.3 % steps of
        log10 p, stage temperature 0.1 K, stage position 1 um, aggregated fractions 0.001, arm 1 nrad).  The rates are computed ON the quantised
        state, so a cached value is a function of its key alone: which call filled the cache (a read of detector.total_counts, a flush) cannot
        change later counts."""
        armq = round(arm, 9)
        pq = 10.0 ** round(math.log10(max(self.p, 1e-3)), 2)
        tq = round(self.t_stage, 1)
        stq = dict(y=round(self.stage['y'], 6), z=round(self.stage['z'], 6))
        saved = (self.p, self.t_stage, self.stage)
        self.p, self.t_stage, self.stage = pq, tq, stq
        try:
            c = self._beam_content()[2]
            aggq = None if c is None else {k: round(v, 3) for k, v in c['agg'].items()}
            key = (self.collimation, armq, self._beam_position(), self._beam_content()[1], id(c), c and c['frozen'], c and c['burst'],
                   None if aggq is None else tuple(sorted(aggq.items())), None if c is None else tuple(sorted(c['conc'].items())), pq, tq, self._room_k(),
                   self.kv, self.ma, tuple(sorted(self.faults)),
                   stq['y'], stq['z'])
            hit = self._rate_cache.get(key)
            if hit is not None:
                return hit
            agg0 = None
            if c is not None:
                agg0, c['agg'] = c['agg'], aggq
            try:
                g = self._geometry()
                flux = self._flux()
                sc = expected_rates(self._scatter_fn(g), armq, self.sdd_true, self.zero_true, self.collimation, flux)
            finally:
                if c is not None:
                    c['agg'] = agg0
            stop_centre = self.zero_true + (0.5e-3 if 'beamstop_misaligned' in self.faults else 0.0)      # the stop is aligned on the beam at installation
            dr = direct_rates(armq, self.zero_true, self.collimation, flux * g['t_total'], stop_centre)
            if abs(armq) < 1e-9:                                  # the semitransparent stop attenuates the scattered photons on the strips it covers too
                sc = np.where(np.abs(STRIP_S - stop_centre) < BEAMSTOP_HALF_M, sc * BEAMSTOP_T, sc)
            if len(self._rate_cache) > 64:
                self._rate_cache.clear()
            self._rate_cache[key] = (sc, dr, g)
            return sc, dr, g
        finally:
            self.p, self.t_stage, self.stage = saved

    def _flush(self):
        """Integrate the counts and photons of the open-shutter time since the last flush with the state at this instant."""
        a = self.acq
        if a is None or self._open_since is None:
            return
        t = self.time_s
        dt = t - self._flush_t
        if dt <= 0.0:
            return
        self._flush_t = t
        flux = self._flux()
        arm = self.arm
        sc, dr, g = self._rates(arm)
        n_inc = flux * dt
        self.led['photons_incident'].add(n_inc)
        n1 = n_inc * g['t_air']
        self.led['photons_attenuated_air'].add(n_inc - n1)
        self.led['photons_attenuated_windows'].add(n1 * g['att_window'])
        self.led['photons_attenuated_sample'].add(n1 * g['att_sample'])
        n2 = n1 * g['t_sample']                                # transmitted primary beam
        split = self._direct_split(arm)
        self.led['photons_direct_off_detector'].add(n2 * split['off_detector'])
        self.led['photons_direct_stopped'].add(n2 * split['stopped'])
        self.led['photons_direct_undetected'].add(n2 * split['undetected'])
        self.led['photons_direct_detected'].add(float(dr.sum()) * dt)
        if a['kind'] == 'align':
            return
        a['lam'] += sc * dt
        a['lam_direct'] += dr * dt
        a['counts_so_far'] += float((sc + dr).sum()) * dt
        self.led['photons_scattered_detected'].add(float(sc.sum()) * dt)
        a['max_rate'] = max(a['max_rate'], float((sc + dr).max()))

    def _direct_split(self, arm):
        """Where the transmitted primary beam goes, from the beam / module / stop geometry (fractions of it): off the module (beam length beyond the
        8 mm strips, beam width beyond the module, or the module moved away on the arm), absorbed in the semitransparent stop, through the sensor
        undetected; the detected rest is what direct_rates counts (the ledger residual checks that both agree)."""
        if abs(arm) > 1e-9:
            return dict(off_detector=1.0, stopped=0.0, undetected=0.0)
        capture = STRIP_LENGTH_M / (LINE_LENGTH_M if self.collimation == 'line' else STRIP_LENGTH_M)
        frac = beam_fraction(STRIP_S - STRIP_PITCH_M / 2, STRIP_S + STRIP_PITCH_M / 2, self.zero_true)
        stop_centre = self.zero_true + (0.5e-3 if 'beamstop_misaligned' in self.faults else 0.0)
        stop = np.where(np.abs(STRIP_S - stop_centre) < BEAMSTOP_HALF_M, BEAMSTOP_T, 1.0)
        on = float(frac.sum())
        through = float((frac * stop).sum())
        eff = detector_efficiency()
        return dict(off_detector=1.0 - capture * on, stopped=capture * (on - through), undetected=capture * through * (1.0 - eff))

    def _check_overexposure(self):
        """Detector protection: the shutter closes as soon as a strip rate exceeds the threshold (checked when the shutter opens, after every
        state-changing command and at every instrument cycle)."""
        a = self.acq
        if a is None or self._open_since is None:
            return
        sc, dr, _ = self._rates(self.arm)
        if float((sc + dr).max()) > OVEREXPOSURE_RATE:
            self._stop_acq('failed', 'DETECTOR_OVEREXPOSURE')
            self._event('error', 'DETECTOR_OVEREXPOSURE', '探测器条带计数率超过保护阈值：快门立即关闭（检查束挡）')

    def _open_shutter(self):
        self._open_since = self.time_s
        self._flush_t = self.time_s
        self._check_overexposure()

    def _close_shutter(self):
        self._flush()
        if self.acq is not None and self._open_since is not None:
            self.acq['open_done'] += self.time_s - self._open_since
            self.acq['seg_open'] = self.acq.get('seg_open', 0.0) + self.time_s - self._open_since
        self._open_since = None
        self._flush_t = None

    def _advance_acq_phase(self):
        a = self.acq
        if a is None or a['kind'] == 'align':
            return
        seg = a['plan'][a['index']]
        if a['phase'] == 'moving' and not self._arm_moving() and abs(self.arm - seg['arm']) < 1e-12:
            if a['kind'] == 'saxs':
                a['phase'] = 'exposing'
                a['seg_open'] = 0.0
                self._open_shutter()
            else:
                a['phase'] = 'settling'
                a['settle_until'] = self.time_s + SCAN_SETTLE_S
        elif a['phase'] == 'settling' and self.time_s >= a['settle_until'] - 1e-12:
            a['phase'] = 'exposing'
            a['seg_open'] = 0.0
            self._open_shutter()

    def _segment_done(self):
        a = self.acq
        rec = self.records[a['record_id']]
        seg = a['plan'][a['index']]
        texp = seg['exposure']
        self._close_shutter()
        lam = a['lam'] + a['lam_direct']
        rate = lam / texp
        mean = lam / (1.0 + rate * DEAD_TIME_S)
        gen = np.random.default_rng(self.rng_acq.getrandbits(64))
        counts = gen.poisson(np.clip(mean, 0.0, None)).astype(np.int64)
        tt, q = indicated_axes(seg['arm'], rec['sdd_indicated_m'], rec['zero_indicated_m'])
        rec['segments'].append(dict(arm_angle_rad=seg['arm'], counts=[int(x) for x in counts], two_theta_rad=[float(x) for x in tt], q_per_m=[float(x) for x in q]))
        rec['saturated_strips'] = max(rec['saturated_strips'], int((rate > MAX_RATE_PER_STRIP).sum()))
        if abs(seg['arm']) < 1e-12:
            pc = primary_peak(counts, rec['zero_indicated_m'])[1]                              # attenuated primary peak behind the stop
            rec['primary_beam_counts'] = pc if pc > 0 else None
        a['index'] += 1
        a['lam'], a['lam_direct'], a['counts_so_far'] = np.zeros(STRIPS), np.zeros(STRIPS), 0.0
        if a['index'] >= len(a['plan']):
            rec['status'], rec['finished_s'] = 'completed', self.time_s
            rec['temperature_indicated_k'] = float(np.mean(rec['_temps'])) if rec['_temps'] else (self.readings['t'] if self.readings else None)
            rec['pressure_indicated_pa'] = float(np.mean(rec['_press'])) if rec['_press'] else (self.readings['p'] if self.readings else None)
            self.operations[a['op']]['phase'] = 'completed'
            self.acq = None
            self._event('acquisition', 'ACQ_COMPLETED', f'采集{rec["record_id"]}完成', rec['record_id'])
        else:
            a['phase'] = 'moving'
            self.arm_target = a['plan'][a['index']]['arm']
            self._advance_acq_phase()

    def _stop_acq(self, status, code):
        a = self.acq
        if a is None:
            return
        if self._open_since is not None:
            self._close_shutter()
        if a['kind'] != 'align':
            rec = self.records[a['record_id']]
            rec.update(status=status, finished_s=self.time_s, error_code=code)
        self.operations[a['op']].update(phase=status, reason=code)
        self.acq = None
        self.arm_target = self.arm
        self._event('acquisition', 'ACQ_' + status.upper(), f'{a["record_id"]}{"被取消" if status == "cancelled" else "失败"}（{code}）', a['record_id'])

    def _cancel_op(self, op):
        if op['kind'] in ('inject', 'clean'):
            self._stop_asx('USER_CANCEL', cancelled=True)
        else:
            self._stop_acq('cancelled', 'USER_CANCEL')

    def _align_done(self):
        a = self.acq
        self._close_shutter()
        target = a['target']
        self.operations[a['op']]['phase'] = 'completed'
        self.acq = None
        if target is None:
            self.operations[a['op']]['reason'] = 'NO_CAPILLARY_FOUND'
            self._event('stage', 'ALIGN_NO_TARGET', 'TrueFocus没有在±2.5mm内找到毛细管/流通池（透过率无极小）')
        else:
            self.stage_target['y'] = max(-STAGE_TRAVEL_M, min(STAGE_TRAVEL_M, target))
            self._event('stage', 'ALIGN_DONE', 'TrueFocus完成：Y移到透过率极小处')

    # ---------------------------------------------------------------- calibration, reduction, analysis
    def _rec(self, rid):
        r = self.records.get(rid)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if r['status'] != 'completed':
            raise ActionRejected('RECORD_NOT_COMPLETED')
        return r

    def _cmd_calibration_zero_angle(self, record_id):
        r = self._rec(record_id)
        seg = next((s for s in r['segments'] if abs(s['arm_angle_rad']) < 1e-12), None)
        if seg is None:
            raise ActionRejected('NO_PRIMARY_BEAM')
        z, total = primary_peak(seg['counts'], r['zero_indicated_m'])
        if z is None or total < 100:
            raise ActionRejected('NO_PRIMARY_BEAM')
        shift = z - self.zero_cal
        self.zero_cal = z
        self.revision += 1
        self._event('calibration', 'ZERO_CALIBRATED', f'零点标定：{z * 1e6:.1f}µm')
        return dict(zero_m=z, shift_m=shift)

    def _ring_profile(self, pos, sdd, two_theta, sigma, collimation):
        """Profile of a Debye ring at in-plane module positions pos for a trial SDD: slit-length offsets (cos 2theta_s = cos 2theta / cos psi),
        beam-width and intrinsic Gaussian broadening sigma (m)."""
        lx, lw = length_kernel(collimation)
        r0 = sdd / math.cos(two_theta)
        psi = np.arctan2(lx, r0)
        c = np.clip(math.cos(two_theta) / np.cos(psi), -1.0, 1.0)
        s_k = sdd * np.tan(np.arccos(c))
        prof = (lw[None, :] * np.exp(-0.5 * ((pos[:, None] - s_k[None, :]) / sigma) ** 2)).sum(1)
        return prof / max(prof.max(), 1e-300)

    def _cmd_calibration_q_scale(self, record_id, calibrant):
        r = self._rec(record_id)
        seg = next((s for s in r['segments'] if abs(s['arm_angle_rad']) < 1e-12), None)
        if seg is None:
            raise ActionRejected('NO_PRIMARY_BEAM')
        n = np.asarray(seg['counts'], float)
        pos = STRIP_S - self.zero_cal
        l1, l2, (w1, w2) = ka_lines()
        d001 = template('silver_behenate')['d001_nm'] * 1e-9
        est, res = [], []
        for order in range(1, 8):
            tts = [2 * math.asin(order * lam / (2 * d001)) for lam in (l1, l2)]
            s_exp = self.sdd_cal * math.tan(tts[0])
            win = np.where((pos > s_exp * 0.80) & (pos < s_exp * 1.08))[0]
            if len(win) < 20 or s_exp > STRIP_S[-1] * 0.95:
                continue
            y, x = n[win], pos[win]
            if y.max() - np.median(y) < 10 * math.sqrt(max(np.median(y), 1.0)):
                continue
            def chi2(sdd, sig):
                m = w1 * self._ring_profile(x, sdd, tts[0], sig, r['collimation']) + w2 * self._ring_profile(x, sdd, tts[1], sig, r['collimation'])
                A = np.vstack([m, np.ones_like(m)]).T
                coef, *_ = np.linalg.lstsq(A, y, rcond=None)
                return float(np.sum((A @ coef - y) ** 2 / np.maximum(y, 1.0))) if coef[0] > 0 else math.inf
            best = None
            coarse = np.linspace(self.sdd_cal * 0.98, self.sdd_cal * 1.02, 161)       # 79 um steps over +-2 %
            for sdd in coarse:
                for sig in (0.03e-3, 0.06e-3, 0.1e-3, 0.15e-3, 0.2e-3, 0.3e-3, 0.45e-3):
                    chi = chi2(sdd, sig)
                    if best is None or chi < best[0]:
                        best = (chi, sdd, sig)
            if best is None or not math.isfinite(best[0]):
                continue
            step = coarse[1] - coarse[0]
            fine = np.linspace(best[1] - step, best[1] + step, 41)                  # 4 um steps, then the vertex of a parabola through the minimum
            chis = np.array([chi2(sd, best[2]) for sd in fine])
            k = int(np.argmin(chis))
            sdd_best = float(fine[k])
            if 0 < k < len(fine) - 1 and np.all(np.isfinite(chis[k - 1:k + 2])):
                den = chis[k - 1] - 2 * chis[k] + chis[k + 1]
                if den > 0:
                    sdd_best += 0.5 * (chis[k - 1] - chis[k + 1]) / den * (fine[1] - fine[0])
            est.append(sdd_best)
            res.append(float(chis[k]) / len(win))
        if len(est) < 2:
            raise ActionRejected('CALIBRANT_NOT_FOUND')
        sdd = float(np.median(est))
        self.sdd_cal = sdd
        self.revision += 1
        self._event('calibration', 'Q_SCALE_CALIBRATED', f'q标定：样品到探测器距离{sdd * 1000:.2f}mm（{len(est)}级峰）')
        lam = mean_wavelength_m()
        return dict(sdd_m=sdd, peaks_used=len(est), residual_per_m=float(np.std(est) / sdd * 4 * math.pi / lam * math.sin(math.asin(lam / (2 * d001)))))

    def _norm(self, r):
        """Per-strip q (1/m, re-computed with the present zero / SDD calibration), counts, the expected counts per unit normalised intensity
        (exposure x transmitted primary-beam rate x flat-module solid angle cos^3 x polarisation, both from the calibrated geometry) and the
        validity mask (all segments)."""
        seg0 = next((s for s in r['segments'] if abs(s['arm_angle_rad']) < 1e-12), None)
        prim_counts = primary_peak(seg0['counts'], self.zero_cal)[1] if seg0 is not None else 0.0
        if seg0 is None or prim_counts <= 0:
            raise ActionRejected('NO_PRIMARY_BEAM')
        t0 = r['exposure_s']
        prim = prim_counts / BEAMSTOP_T / t0                                       # transmitted primary beam rate seen through the stop
        qs, ns, es, ok = [], [], [], []
        for sgm in r['segments']:
            tt, q = indicated_axes(sgm['arm_angle_rad'], self.sdd_cal, self.zero_cal)
            _, _, cosi = strip_geometry(sgm['arm_angle_rad'], self.sdd_cal, self.zero_cal)
            m = tt > 0
            if abs(sgm['arm_angle_rad']) < 1e-12:
                m &= np.abs(STRIP_S - self.zero_cal) > MASK_HALF_M
            qs.append(q)
            ns.append(np.asarray(sgm['counts'], float))
            es.append(t0 * prim * cosi ** 3 * polarisation(tt))
            ok.append(m)
        return np.concatenate(qs), np.concatenate(ns), np.concatenate(es), np.concatenate(ok), prim

    @staticmethod
    def _log_bin(q_nm, n, e, edges):
        """Logarithmic bins of strips: I = sum(counts) / sum(e) (the unbiased Poisson estimate for strips of different exposure, solid angle
        and polarisation; inverse-variance weights from the counts themselves would pull a bin towards its low-count strips), sigma =
        sqrt(max(sum counts, 1)) / sum(e), q = e-weighted mean.  Returns the bin indices present and the three arrays."""
        idx = np.digitize(q_nm, edges)
        keys = np.unique(idx)
        out = []
        for k in keys:
            m = idx == k
            se, sn = float(e[m].sum()), float(n[m].sum())
            out.append((float(np.sum(q_nm[m] * e[m]) / se), sn / se, math.sqrt(max(sn, 1.0)) / se))
        a = np.array(out)
        return keys, a[:, 0], a[:, 1], a[:, 2]

    def _curve(self, r, b):
        """Binned normalised curve of record r (1/sr-equivalent relative units), minus the binned curve of background b on the same bins
        (each normalised by its own transmitted primary beam); returns q (1/nm), I, sigma, the primary-beam rates."""
        q, n, e, ok, prim = self._norm(r)
        mask = ok & (q > 0)
        primb = None
        if b is not None:
            if b['collimation'] != r['collimation'] or [x['arm_angle_rad'] for x in b['segments']] != [x['arm_angle_rad'] for x in r['segments']]:
                raise ActionRejected('BACKGROUND_MISMATCH')
            _, nb, eb, okb, primb = self._norm(b)
            mask &= okb
        q_nm = q[mask] * 1e-9
        edges = np.exp(np.arange(math.log(q_nm.min()), math.log(q_nm.max()) + LOG_BIN, LOG_BIN))
        keys, qq, ii, ss = self._log_bin(q_nm, n[mask], e[mask], edges)
        if b is not None:
            kb, _, ib, sb = self._log_bin(q_nm, nb[mask], eb[mask], edges)
            assert np.array_equal(keys, kb)
            ii, ss = ii - ib, np.hypot(ss, sb)
        return qq, ii, ss, prim, primb

    def _cmd_calibration_absolute(self, record_id, background_record_id, standard, thickness_m):
        r = self._rec(record_id)
        if background_record_id is None and standard == 'water':
            raise ActionRejected('BACKGROUND_REQUIRED')
        q_nm, i, s, prim, _ = self._curve(r, None if background_record_id is None else self._rec(background_record_id))
        if standard == 'water':
            temp = r['temperature_indicated_k'] or 293.15
            m = (q_nm >= 1.0) & (q_nm <= 3.0)
            if m.sum() < 20:
                raise ActionRejected('STANDARD_RANGE_NOT_COVERED')
            ref = water_forward_cm(temp) * 100.0 * np.ones(m.sum())
            u_std = 0.01
        else:
            temp = r['temperature_indicated_k']
            m = (q_nm >= 0.08) & (q_nm <= 2.5)
            if m.sum() < 20:
                raise ActionRejected('STANDARD_RANGE_NOT_COVERED')
            gc = template('nist_srm3600_glassy_carbon')
            ref = smear_curve(q_nm[m], lambda qq: srm3600_cm(gc, qq), r['collimation']) * 100.0
            u_std = 0.0625 / 2
        meas = i[m] / thickness_m
        if np.any(meas <= 0):
            raise ActionRejected('STANDARD_SIGNAL_INVALID')
        ratio = ref / meas
        f = float(np.median(ratio))
        rel = float(math.hypot(np.std(ratio) / f / math.sqrt(m.sum()), u_std))
        self.seq['cal'] += 1
        cid = f'cal-{self.seq["cal"]}'
        self.calibrations[cid] = dict(calibration_id=cid, standard=standard, record_id=record_id, background_record_id=background_record_id, factor=f,
                                      relative_uncertainty=rel, temperature_k=temp, q_range_per_m=[float(q_nm[m].min() * 1e9), float(q_nm[m].max() * 1e9)],
                                      _collimation=r['collimation'])
        return self._out(self.calibrations[cid])

    @staticmethod
    def _smear_matrix(q_nm, collimation, sdd, tail):
        """Linear slit-smearing operator on the grid q (linear interpolation between grid points, power-law continuation q^tail beyond q_max)."""
        lx, lw = length_kernel(collimation)
        lam = mean_wavelength_m()
        t = 4.0 * math.pi / lam * np.sin(np.arctan2(lx, sdd) / 2.0) * 1e-9
        w = lw / lw.sum()
        n = len(q_nm)
        K = np.zeros((n, n))
        for i, qi in enumerate(q_nm):
            for tk, wk in zip(t, w):
                qq = math.sqrt(qi * qi + tk * tk)
                if qq >= q_nm[-1]:
                    K[i, -1] += wk * (qq / q_nm[-1]) ** tail
                    continue
                j = int(np.searchsorted(q_nm, qq) - 1)
                j = max(0, j)
                f = (qq - q_nm[j]) / (q_nm[j + 1] - q_nm[j])
                K[i, j] += wk * (1.0 - f)
                K[i, j + 1] += wk * f
        return K

    def _desmear(self, q_nm, i_s, sig, collimation, sdd):
        """Regularised slit desmearing (Tikhonov, as in the indirect methods): minimise sum((K d - s)/sigma)^2 + lambda |D2 (d/|s_ref|)|^2, lambda chosen
        so that chi^2/N = 1 (discrepancy principle); errors from the covariance of the solution."""
        pos = i_s > 0
        tail = float(np.clip(np.polyfit(np.log(q_nm[pos][-15:]), np.log(i_s[pos][-15:]), 1)[0], -4.0, -1.0)) if pos[-15:].sum() >= 8 else -4.0
        K = self._smear_matrix(q_nm, collimation, sdd, tail)
        n = len(q_nm)
        A = K / sig[:, None]
        bb = i_s / sig
        ref = np.maximum(np.abs(np.convolve(np.pad(i_s, 3, mode='edge'), np.ones(7) / 7, mode='valid')), 1e-3 * np.max(np.abs(i_s)))
        D = np.zeros((n - 2, n))
        for j in range(n - 2):
            D[j, j:j + 3] = np.array([1.0, -2.0, 1.0]) / ref[j + 1]
        AtA, Atb, DtD = A.T @ A, A.T @ bb, D.T @ D
        scale = np.trace(AtA) / max(np.trace(DtD), 1e-300)

        def solve(lam):
            M = AtA + lam * scale * DtD
            d = np.linalg.solve(M, Atb)
            chi = float(np.sum((A @ d - bb) ** 2)) / n
            return d, chi, M
        lo, hi = -12.0, 4.0
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            _, chi, _ = solve(10 ** mid)
            if chi > 1.0:
                hi = mid
            else:
                lo = mid
        d, chi, M = solve(10 ** lo)
        cov = np.linalg.inv(M) @ AtA @ np.linalg.inv(M)
        return d, np.sqrt(np.clip(np.diag(cov), 0.0, None))

    def _cmd_data_reduce(self, record_id, background_record_id, calibration_id, thickness_m, desmear):
        r = self._rec(record_id)
        qb_, ib_, sb_, prim, primb = self._curve(r, None if background_record_id is None else self._rec(background_record_id))
        tr = None
        notes = ['按透过主束归一，按标定几何校正平板模块立体角（cos³）与偏振', '对数分箱（计数求和÷期望计数）']
        if background_record_id is not None:
            tr = float(prim / primb) if primb > 0 else None
            notes.append('扣背景（各自按透过主束归一）')
        ib_, sb_ = ib_ / thickness_m, sb_ / thickness_m
        absolute = False
        if calibration_id is not None:
            cal = self.calibrations.get(calibration_id)
            if cal is None:
                raise ActionRejected('UNKNOWN_CALIBRATION')
            if cal['_collimation'] != r['collimation']:
                raise ActionRejected('CALIBRATION_MISMATCH')
            ib_, sb_ = ib_ * cal['factor'], sb_ * cal['factor']
            absolute = True
            notes.append(f'绝对标定（{cal["standard"]}）')
        if desmear:
            ib_, sb_ = self._desmear(qb_, ib_, sb_, r['collimation'], self.sdd_cal)
            notes.append('正则化去狭缝模糊（仪器长度核；曲率罚项按χ²/N=1选取，误差取自解的协方差）')
        if len(qb_) > 600:
            keep = np.linspace(0, len(qb_) - 1, 600).astype(int)
            qb_, ib_, sb_ = qb_[keep], ib_[keep], sb_[keep]
        self.seq['red'] += 1
        rid = f'red-{self.seq["red"]}'
        self.reduced[rid] = dict(reduced_id=rid, record_id=record_id, background_record_id=background_record_id, calibration_id=calibration_id, absolute=absolute,
                                 desmeared=bool(desmear), thickness_m=thickness_m, q_per_m=[float(x * 1e9) for x in qb_], intensity=[float(x) for x in ib_],
                                 sigma=[float(x) for x in sb_], intensity_unit='1/m' if absolute else 'relative', transmission_ratio=tr,
                                 q_min_valid_per_m=float(qb_[0] * 1e9), notes_zh='；'.join(notes + [f'q按当前标定换算（零点{self.zero_cal * 1e6:.1f}µm，'
                                                                                                  f'样品到探测器{self.sdd_cal * 1000:.2f}mm）']))
        return self._out(self.reduced[rid])

    def _cmd_data_result(self, reduced_id):
        r = self.reduced.get(reduced_id)
        if r is None:
            raise ActionRejected('UNKNOWN_REDUCED')
        return self._out(r)

    def _red(self, rid):
        r = self.reduced.get(rid)
        if r is None:
            raise ActionRejected('UNKNOWN_REDUCED')
        return np.asarray(r['q_per_m']), np.asarray(r['intensity']), np.asarray(r['sigma']), r

    def _cmd_analysis_guinier(self, reduced_id, qrg_max):
        q, i, s, r = self._red(reduced_id)
        pos = i > 0
        q, i, s = q[pos], i[pos], s[pos]
        if len(q) < 8:
            raise ActionRejected('GUINIER_FAILED')
        sm = np.convolve(np.pad(i, 2, mode='edge'), np.ones(5) / 5, mode='valid')
        i_lo = float(np.median(sm[:5]))
        below = np.where(sm < i_lo * math.exp(-1.0))[0]
        if len(below) == 0:
            raise ActionRejected('GUINIER_FAILED')
        rg = math.sqrt(3.0) / q[below[0]]
        for _ in range(20):
            m = q * rg <= qrg_max
            if m.sum() < 5:
                raise ActionRejected('GUINIER_FAILED')
            x, y, w = q[m] ** 2, np.log(i[m]), i[m] / s[m]
            a, b = np.polyfit(x, y, 1, w=w)
            if a >= 0:
                raise ActionRejected('GUINIER_FAILED')
            rg_new = math.sqrt(-3 * a)
            done = abs(rg_new - rg) < 1e-6 * rg
            rg = rg_new
            if done:
                break
        m = q * rg <= qrg_max
        if m.sum() < 5:
            raise ActionRejected('GUINIER_FAILED')
        x, y, w = q[m] ** 2, np.log(i[m]), i[m] / s[m]
        (a, b), cov = np.polyfit(x, y, 1, w=w, cov='unscaled')
        if a >= 0:
            raise ActionRejected('GUINIER_FAILED')
        res = (y - (a * x + b)) * w
        chi2 = float(np.sum(res ** 2) / max(1, m.sum() - 2))
        rg = math.sqrt(-3 * a)
        rg_sig = 1.5 / rg * math.sqrt(cov[0, 0] * max(chi2, 1.0))
        i0 = math.exp(b)
        return dict(rg_m=rg, rg_sigma_m=rg_sig, i0=i0, i0_sigma=i0 * math.sqrt(cov[1, 1] * max(chi2, 1.0)), q_min_per_m=float(q[m].min()),
                    q_max_per_m=float(q[m].max()), points=int(m.sum()), reduced_chi2=chi2)

    def _cmd_analysis_molecular_weight(self, reduced_id, concentration_kg_m3, drho_m_m_kg):
        _, _, _, r = self._red(reduced_id)
        if not r['absolute']:
            raise ActionRejected('ABSOLUTE_SCALE_REQUIRED')
        if not r['desmeared']:
            raise ActionRejected('DESMEAR_REQUIRED')          # smearing lowers I(0): line ~30 % (protein), point (8 mm strips) ~6-20 %
        g = self._cmd_analysis_guinier(reduced_id, 1.3)
        i0c = g['i0'] / concentration_kg_m3
        return dict(molecular_weight_kg_mol=i0c * N_AVOGADRO / drho_m_m_kg ** 2, i0_per_c=i0c)

    def _cmd_analysis_peaks(self, reduced_id):
        q, i, s, r = self._red(reduced_id)
        if len(q) < 12:
            return dict(peaks=[])
        li = np.log(np.clip(i, np.max(np.abs(i)) * 1e-9 + 1e-300, None))
        k = 15
        pad = np.pad(li, k, mode='edge')
        base = np.exp(np.array([np.percentile(pad[j:j + 2 * k + 1], 20) for j in range(len(li))]))
        peaks = []
        for j in range(2, len(q) - 2):
            if i[j] >= i[j - 1] and i[j] >= i[j + 1] and i[j] >= i[j - 2] and i[j] >= i[j + 2]:
                prom = (i[j] - base[j]) / max(s[j], 1e-30)
                if prom < 5:
                    continue
                half = base[j] + (i[j] - base[j]) / 2
                lo = j
                while lo > 0 and i[lo] > half:
                    lo -= 1
                hi = j
                while hi < len(q) - 1 and i[hi] > half:
                    hi += 1
                if q[hi] - q[lo] > 0.3 * q[j]:
                    continue                                   # broad bumps and kinks of slit-smeared tails are not peaks
                # sub-bin position: vertex of the parabola through the three bins around the maximum (in ln q, the bin spacing)
                x0, x1, x2 = math.log(q[j - 1]), math.log(q[j]), math.log(q[j + 1])
                y0, y1, y2 = i[j - 1], i[j], i[j + 1]
                den = (x0 - x1) * (x0 - x2) * (x1 - x2)
                a = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / den if den else 0.0
                b = (x2 * x2 * (y0 - y1) + x1 * x1 * (y2 - y0) + x0 * x0 * (y1 - y2)) / den if den else 0.0
                xv = -b / (2 * a) if a < 0 else x1
                qp = math.exp(min(max(xv, x0), x2))
                peaks.append(dict(q_per_m=float(qp), d_spacing_m=float(2 * math.pi / qp), fwhm_per_m=float(q[hi] - q[lo]), height=float(i[j] - base[j]),
                                  prominence_sigma=float(prom)))
        hmax = max([p['height'] for p in peaks], default=0.0)
        peaks = [p for p in peaks if p['height'] >= 0.02 * hmax]
        peaks.sort(key=lambda p: -p['prominence_sigma'])
        return dict(peaks=sorted(peaks[:16], key=lambda p: p['q_per_m']))

    # ---------------------------------------------------------------- autosampler
    def _need_asx(self):
        self._need_ready()
        if self.asx_door:
            raise ActionRejected('ASX_DOOR_OPEN')
        if self.asx_op is not None:
            raise ActionRejected('ASX_BUSY')
        self._need_idle()

    def _cmd_asx_set_door(self, open):
        self._need_plant()
        if open and self.asx_op is not None:
            raise ActionRejected('ASX_BUSY')
        self.asx_door = open
        return dict(open=open)

    def _cmd_asx_load_well(self, sample_id, well, volume_m3):
        self._need_plant()
        if not _well_ok(well):
            raise ActionRejected('INVALID_ARGUMENT')
        if not self.asx_door:
            raise ActionRejected('ASX_DOOR_CLOSED')
        if well in self.wells:
            raise ActionRejected('WELL_OCCUPIED')
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if not s['liquid']:
            raise ActionRejected('HOLDER_UNSUITABLE')
        if s['bench_volume_m3'] < volume_m3 - 1e-15:
            raise ActionRejected('INSUFFICIENT_VOLUME')
        s['bench_volume_m3'] -= volume_m3
        self.wells[well] = self._content_of(s, volume_m3)
        self.wells[well]['label'] = well
        self.wells[well]['sample_label'] = sample_id
        return dict(well=well, volume_m3=volume_m3)

    def _cmd_asx_unload_well(self, well):
        self._need_plant()
        if not _well_ok(well):
            raise ActionRejected('INVALID_ARGUMENT')
        if not self.asx_door:
            raise ActionRejected('ASX_DOOR_CLOSED')
        c = self.wells.pop(well, None)
        if c is None:
            raise ActionRejected('WELL_EMPTY')
        self.led['liquid_waste_m3'].add(c['volume_m3'])
        self.led['solute_waste_kg'].add(_content_mass(c))
        return dict(well=well)

    def _cmd_asx_set_tray_temperature(self, temperature_k):
        self._need_ready()
        self.tray_set = temperature_k
        return dict(temperature_k=temperature_k)

    def _asx_plan(self, kind, well=None):
        down = ASX_NEEDLE_DOWN_M
        home = dict(x=0.0, y=0.0, z=0.0)
        px, py = ASX_FC_XY
        if kind == 'inject':
            wx, wy = well_xy(well)
            return [('move', dict(x=wx, y=wy, z=0.0)), ('move', dict(x=wx, y=wy, z=down)), ('dwell', ASX_ASPIRATE_S, 'aspirate'),
                    ('move', dict(x=wx, y=wy, z=0.0)), ('move', dict(x=px, y=py, z=0.0)), ('move', dict(x=px, y=py, z=down)),
                    ('dwell', ASX_DISPENSE_S, 'dispense'), ('move', dict(x=px, y=py, z=0.0)), ('move', home)]
        return [('move', dict(x=px, y=py, z=0.0)), ('move', dict(x=px, y=py, z=down)), ('dwell', ASX_RINSE_S, 'rinse'), ('move', dict(x=px, y=py, z=0.0)),
                ('move', home)]

    def _cmd_asx_inject(self, well):
        if not _well_ok(well):
            raise ActionRejected('INVALID_ARGUMENT')
        self._need_asx()
        c = self.wells.get(well)
        if c is None:
            raise ActionRejected('WELL_EMPTY')
        if c['volume_m3'] < ASX_INJECT_M3 - 1e-15:
            raise ActionRejected('INSUFFICIENT_VOLUME')
        return self._start_asx('inject', well)

    def _cmd_asx_clean(self):
        self._need_asx()
        return self._start_asx('clean', None)

    def _start_asx(self, kind, well):
        op = self._new_op(kind, None, well=well)
        self.asx_op = dict(kind=kind, op=op['operation_id'], plan=self._asx_plan(kind, well), index=-1, dwell_until=None, well=well, sample=None)
        self._event('autosampler', 'ASX_' + kind.upper(), f'自动进样器{"进样" if kind == "inject" else "清洗"}开始' + (f'（{well}）' if well else ''))
        self._asx_next()
        return dict(operation_id=op['operation_id'])

    def _asx_next(self):
        o = self.asx_op
        o['index'] += 1
        if o['index'] >= len(o['plan']):
            self.operations[o['op']]['phase'] = 'completed'
            self.asx_op = None
            self._event('autosampler', 'ASX_DONE', '自动进样器操作完成')
            return
        ph = o['plan'][o['index']]
        if ph[0] == 'move':
            self.gantry_target = dict(ph[1])
        else:
            o['dwell_until'] = self.time_s + ph[1]
            start = getattr(self, '_asx_' + ph[2] + '_start', None)
            if start is not None:
                start()

    def _asx_phase_done(self):
        o = self.asx_op
        ph = o['plan'][o['index']]
        if ph[0] == 'dwell':
            getattr(self, '_asx_' + ph[2])()
        self._asx_next()

    def _asx_aspirate(self):
        o = self.asx_op
        c = self.wells[o['well']]
        take = dict(c, volume_m3=ASX_INJECT_M3, conc=dict(c['conc']), agg=dict(c['agg']), components=copy.deepcopy(c['components']))
        c['volume_m3'] -= ASX_INJECT_M3
        if c['volume_m3'] <= 1e-15:
            del self.wells[o['well']]
        o['sample'] = take

    def _asx_dispense(self):
        o = self.asx_op
        new = o['sample']
        prev = self.flow_cell
        m_before = _content_mass(prev) + _content_mass(new) + sum(self.fc_residue.values())
        r = ASX_CARRY_OVER if prev is not None else 0.0
        mixed = _mix(prev, new, r) if prev is not None else dict(new, conc=dict(new['conc']))
        for k, m in self.fc_residue.items():
            mixed['conc'][k] = mixed['conc'].get(k, 0.0) + m / FC_VOLUME_M3
            if k not in {c['key'] for c in mixed['components']}:
                mixed['components'].append(dict(key=k, template_id=k, kind=template(k)['kind'], parameters=None))
        mixed['components'] = copy.deepcopy(mixed['components'])
        for r in self.fc_model_residue:
            same = next((c for c in mixed['components'] if c['key'] == r['key']), None)
            if same is not None:
                same['parameters']['volume_fraction'] += r['parameters']['volume_fraction']
            else:
                mixed['components'].append(copy.deepcopy(r))
        self.fc_model_residue = []
        mixed.update(volume_m3=FC_VOLUME_M3, label=o['well'], frozen=False, burst=False, agg={k: 0.0 for k in mixed['conc']})
        self.fc_residue = {}
        self.fc_model_residue = []
        v_waste = (prev['volume_m3'] if prev else 0.0) + new['volume_m3'] - FC_VOLUME_M3
        self.flow_cell = mixed
        self.led['liquid_waste_m3'].add(v_waste)
        self.led['solute_waste_kg'].add(m_before - _content_mass(mixed))
        self.fc_state, self.fc_label = 'filled', o['well']
        self._rate_cache.clear()

    def _asx_rinse_start(self):
        """The wash liquid displaces the flow-cell content when the rinse starts (to waste; a 1e-4 residue of the solutes stays on the walls)."""
        prev = self.flow_cell
        if prev is not None:
            kept = 0.0
            for k, cval in prev['conc'].items():
                m = cval * prev['volume_m3'] * ASX_CARRY_OVER_CLEAN
                self.fc_residue[k] = self.fc_residue.get(k, 0.0) + m
                kept += m
            self.led['liquid_waste_m3'].add(prev['volume_m3'])
            self.led['solute_waste_kg'].add(_content_mass(prev) - kept)
            for k in prev['components']:                     # model particles leave the same 1e-4 residue (volume fraction in the next filling)
                if k['kind'] == 'model_spheres':
                    r = copy.deepcopy(k)
                    r['parameters']['volume_fraction'] *= ASX_CARRY_OVER_CLEAN
                    self.fc_model_residue.append(r)
        self.flow_cell = None
        self.fc_state, self.fc_label = 'empty', None
        self._rate_cache.clear()

    def _asx_rinse(self):
        """End of the rinse dwell: the flow cell is clean and dry."""
        self.fc_state = 'rinsed'

    def _stop_asx(self, code, cancelled=False):
        o = self.asx_op
        if o is None:
            return
        if o.get('sample') is not None:                        # liquid in the needle goes to waste
            self.led['liquid_waste_m3'].add(o['sample']['volume_m3'])
            self.led['solute_waste_kg'].add(_content_mass(o['sample']))
        self.operations[o['op']].update(phase='cancelled' if cancelled else 'failed', reason=code)
        self.asx_op = None
        self.gantry_target = dict(self.gantry)
        self._event('autosampler', 'ASX_STOPPED', f'自动进样器操作中止（{code}）')

    # ---------------------------------------------------------------- queries
    def _cmd_records_list(self):
        return dict(records=sorted(self.records), reduced=sorted(self.reduced), calibrations=sorted(self.calibrations))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def ledger(self):
        v = {k: self.led[k].value for k in LEDGER_FLOWS}
        liq = [s for s in self.samples.values() if s['liquid']]
        bench_v = sum(s['bench_volume_m3'] for s in liq)
        bench_m = sum(sum(s['conc'].values()) * s['bench_volume_m3'] for s in liq)
        hold = self.content if (self.content is not None and self.content['liquid']) else None
        wells_v = sum(c['volume_m3'] for c in self.wells.values())
        wells_m = sum(_content_mass(c) for c in self.wells.values())
        needle = self.asx_op['sample'] if (self.asx_op and self.asx_op.get('sample') is not None) else None
        fc_v = (self.flow_cell['volume_m3'] if self.flow_cell else 0.0) + (needle['volume_m3'] if needle else 0.0)
        fc_m = _content_mass(self.flow_cell) + sum(self.fc_residue.values()) + (_content_mass(needle) if needle else 0.0)
        out = dict(photons_incident=v['photons_incident'], photons_attenuated_air=v['photons_attenuated_air'],
                   photons_attenuated_windows=v['photons_attenuated_windows'], photons_attenuated_sample=v['photons_attenuated_sample'],
                   photons_direct_stopped=v['photons_direct_stopped'], photons_direct_off_detector=v['photons_direct_off_detector'],
                   photons_direct_undetected=v['photons_direct_undetected'], photons_direct_detected=v['photons_direct_detected'],
                   photons_scattered_detected=v['photons_scattered_detected'],
                   photons_residual=v['photons_incident'] - v['photons_attenuated_air'] - v['photons_attenuated_windows'] - v['photons_attenuated_sample']
                   - v['photons_direct_stopped'] - v['photons_direct_off_detector'] - v['photons_direct_undetected'] - v['photons_direct_detected'],
                   liquid_provided_m3=v['liquid_provided_m3'], liquid_on_bench_m3=bench_v, liquid_in_holder_m3=hold['volume_m3'] if hold else 0.0,
                   liquid_in_wells_m3=wells_v, liquid_in_flow_cell_m3=fc_v, liquid_waste_m3=v['liquid_waste_m3'],
                   solute_provided_kg=v['solute_provided_kg'], solute_on_bench_kg=bench_m, solute_in_holder_kg=_content_mass(hold) if hold else 0.0,
                   solute_in_wells_kg=wells_m, solute_in_flow_cell_kg=fc_m, solute_waste_kg=v['solute_waste_kg'], tube_energy_j=v['tube_energy_j'],
                   tube_on_time_s=v['tube_on_time_s'])
        out['liquid_residual_m3'] = out['liquid_provided_m3'] - bench_v - out['liquid_in_holder_m3'] - wells_v - fc_v - out['liquid_waste_m3']
        out['solute_residual_kg'] = out['solute_provided_kg'] - bench_m - out['solute_in_holder_kg'] - wells_m - fc_m - out['solute_waste_kg']
        return {k: float(v) for k, v in out.items()}

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        return dict(library=LIBRARY_REL, templates=sorted(self.used_templates))

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
            if self._arm_moving():
                cands.append(abs(self.arm_target - self.arm) / ARM_SPEED_RAD_S)
            if any(abs(self.gantry[k] - self.gantry_target[k]) > 1e-12 for k in self.gantry):
                cands.append(max(abs(self.gantry[k] - self.gantry_target[k]) / (ASX_Z_SPEED_M_S if k == 'z' else ASX_GANTRY_SPEED_M_S) for k in self.gantry))
            if self.xray_on:
                if abs(self.kv - self.kv_set) > 1e-9:
                    cands.append(abs(self.kv_set - self.kv) / HV_RAMP_V_S)
                if abs(self.ma - self.ma_set) > 1e-12:
                    cands.append(abs(self.ma_set - self.ma) / MA_RAMP_A_S)
        a = self.acq
        if a is not None:
            if a['kind'] == 'align':
                cands.append(a['t0'] + ALIGN_S - t)
            elif a['phase'] == 'settling':
                cands.append(a['settle_until'] - t)
            elif a['phase'] == 'exposing' and self._open_since is not None:
                cands.append(self._open_since + (a['plan'][a['index']]['exposure'] - a['seg_open']) - t)
        o = self.asx_op
        if o is not None and o['plan'][o['index']][0] == 'dwell':
            cands.append(o['dwell_until'] - t)
        return max(0.0, min(cands)) if cands else math.inf

    def _handle_events(self):
        a = self.acq
        t = self.time_s
        if a is not None:
            if a['kind'] == 'align':
                if t >= a['t0'] + ALIGN_S - 1e-9:
                    self._align_done()
            elif a['phase'] in ('moving', 'settling'):
                self._advance_acq_phase()
            elif a['phase'] == 'exposing' and self._open_since is not None:
                if a['seg_open'] + (t - self._open_since) >= a['plan'][a['index']]['exposure'] - 1e-9:
                    self._segment_done()
        o = self.asx_op
        if o is not None:
            ph = o['plan'][o['index']]
            if ph[0] == 'move' and all(abs(self.gantry[k] - self.gantry_target[k]) <= 1e-12 for k in self.gantry):
                self._asx_phase_done()
            elif ph[0] == 'dwell' and t >= o['dwell_until'] - 1e-9:
                self._asx_phase_done()

    def _integrate(self, ns0, ns1):
        """Continuous motions, ramps, vacuum and temperatures between the 1 s cycles, cut at every event time (exact for any chopping)."""
        t, t_end = ns0 / 1e9, ns1 / 1e9
        guard = 0
        while guard < 100000:
            guard += 1
            self._t_now = t
            self._handle_events()
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
        room = self._room_k()
        if self.power:
            self.arm = self._toward(self.arm, self.arm_target, ARM_SPEED_RAD_S * dt)
            for k in self.stage:
                self.stage[k] = self._toward(self.stage[k], self.stage_target[k], STAGE_SPEED_M_S * dt)
            for k in self.gantry:
                self.gantry[k] = self._toward(self.gantry[k], self.gantry_target[k], (ASX_Z_SPEED_M_S if k == 'z' else ASX_GANTRY_SPEED_M_S) * dt)
            if self.xray_on:
                v0, i0 = self.kv, self.ma
                self.kv = self._toward(self.kv, self.kv_set, HV_RAMP_V_S * dt)
                self.ma = self._toward(self.ma, self.ma_set, MA_RAMP_A_S * dt)
                av, ai = (self.kv - v0) / dt, (self.ma - i0) / dt
                self.led['tube_energy_j'].add(v0 * i0 * dt + (v0 * ai + i0 * av) * dt * dt / 2.0 + av * ai * dt ** 3 / 3.0)
                self.led['tube_on_time_s'].add(dt)
        # vacuum: pump (valve open), vent (vent valve open) or closed valves without power (leak)
        q_leak = LEAK_PA_M3_S + (FAULT_LEAK_PA_M3_S if 'vacuum_leak' in self.faults else 0.0)
        if self.door_open:
            self.p = P_ATM
        elif self.power and self.pump_on:
            p_eq = P_ULT_PA + q_leak / S_PUMP_M3_S
            self.p = p_eq + (self.p - p_eq) * math.exp(-S_PUMP_M3_S / V_VAC_M3 * dt)
        elif self.power:
            self.p = P_ATM - (P_ATM - self.p) * math.exp(-dt / VENT_TAU_S)
        else:
            self.p = min(P_ATM, self.p + q_leak / V_VAC_M3 * dt)
        # stage temperature: ramp-limited first order to the set point (powered) or relaxation to the room (unpowered)
        if self.power:
            target, tau, rmax = self.t_set, STAGE_TAU_S, STAGE_RAMP_MAX_K_S
        else:
            target, tau, rmax = room, 300.0, math.inf
        d = target - self.t_stage
        rem = dt
        if abs(d) > tau * rmax:
            tl = (abs(d) - tau * rmax) / rmax
            if tl >= rem:
                self.t_stage += math.copysign(rmax * rem, d)
                rem = 0.0
            else:
                self.t_stage += math.copysign(rmax * tl, d)
                rem -= tl
        if rem > 0:
            self.t_stage = target + (self.t_stage - target) * math.exp(-rem / tau)
        tray_target = self.tray_set if self.power else room
        self.tray_t = tray_target + (self.tray_t - tray_target) * math.exp(-dt / ASX_TRAY_TAU_S)

    def _tick(self):
        self._flush()
        self._slow_physics(CYCLE_S)
        if not self.power:
            self.readings = None
            return
        t = self.time_s
        if self.state == 'booting' and t - self.state_t0 >= BOOT_S - 1e-9:
            self.state = 'ready'
            self._event('power', 'SOFTWARE_READY', 'SAXSdrive已启动')
        if self.xray_on and not self._chiller_ok():
            self._xray_off('COOLING_WATER_LOW')
            self.gen_fault = 'COOLING_WATER_LOW'
            self._event('error', 'COOLING_WATER_LOW', '光管冷却水流量不足：高压关闭')
        z = [self.rng.gauss(0.0, 1.0) for _ in range(4)]
        self.readings = dict(kv=round(self.kv / 100.0) * 100.0 if self.xray_on else 0.0, ma=round(self.ma * 1e4) / 1e4 if self.xray_on else 0.0,
                             p=max(1.0, self.p * (1.0 + 0.05 * z[0])), t=round(self.t_stage + 0.03 * z[1], 2), tray=round(self.tray_t + 0.1 * z[2], 1))
        a = self.acq
        if a is not None and a['kind'] != 'align' and self._open_since is not None:
            rec = self.records[a['record_id']]
            rec['_temps'].append(self.readings['t'])
            rec['_press'].append(self.readings['p'])
        self._check_overexposure()

    def _slow_physics(self, dt):
        """Cycle-based sample state: freezing and melting of aqueous samples, boiling burst above 95 C, thermal aggregation of proteins above 60 C."""
        for where, c in (('holder', self.content), ('flow_cell', self.flow_cell)):
            if c is None or not c['liquid'] or c['burst']:
                continue
            T = self.t_stage
            if T < FREEZE_K and not c['frozen']:
                c['frozen'] = True
                self._event('sample', 'SAMPLE_FROZEN', f'{c["label"]}在{T - 273.15:.1f}°C冻结（过冷极限−10°C）')
                self._rate_cache.clear()
            elif T > MELT_K and c['frozen']:
                c['frozen'] = False
                self._rate_cache.clear()
            if T > BURST_K:
                c['burst'] = True
                n_mol = c['volume_m3'] * 1000.0 / 0.018
                if not self.door_open:
                    self.p = min(P_ATM, self.p + n_mol * 8.314462618 * T / V_VAC_M3)
                self.led['liquid_waste_m3'].add(c['volume_m3'])
                self.led['solute_waste_kg'].add(_content_mass(c))
                if where == 'holder':
                    self.content, self.holder_broken = None, True
                else:
                    self.flow_cell, self.fc_state, self.fc_label = None, 'empty', None
                self.error = 'SAMPLE_BURST'
                self._rate_cache.clear()
                self._event('error', 'SAMPLE_BURST', f'{c["label"]}加热到{T - 273.15:.0f}°C沸腾，{"毛细管" if where == "holder" else "流通池"}破裂，样品进入样品室')
                continue
            if T > AGG_T_K:
                k = AGG_RATE_S * math.exp((T - AGG_T_K) / 5.0)
                for comp in c['components']:
                    if comp['kind'] == 'curve' and template(comp['template_id'])['_protein']:
                        a0 = c['agg'].get(comp['key'], 0.0)
                        c['agg'][comp['key']] = 1.0 - (1.0 - a0) * math.exp(-k * dt)


# ---------------------------------------------------------------- host session: identity filter, request binding, ownership
ASYNC = {'acquisition.start', 'acquisition.swaxs_scan', 'stage.auto_align', 'asx.inject', 'asx.clean'}
_DATA_GUARDED = {'acquisition.result', 'data.result'}
_HIDDEN_EVENT_CODES = {'SAMPLE_FROZEN'}                  # hidden sample state: agents see its consequences in the data only
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill', 'configuration') and k not in UNSUPPORTED}
_RECORD_ARG = {'acquisition.result': 'record_id', 'data.result': 'reduced_id'}
_ANALYSIS_RECORD_ARGS = {'calibration.zero_angle': ['record_id'], 'calibration.q_scale': ['record_id'], 'calibration.absolute': ['record_id', 'background_record_id'],
                         'data.reduce': ['record_id', 'background_record_id'], 'analysis.guinier': ['reduced_id'], 'analysis.molecular_weight': ['reduced_id'],
                         'analysis.peaks': ['reduced_id']}


class AntonpaarSaxspaceSession:
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
        meta = copy.deepcopy(export_antonpaar_saxspace_contract_metadata())
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
        acq = self._host.acq
        return acq is not None and self._foreign(acq['record_id'])

    def _own_last(self, table, key):
        done = [k for k, r in table.items() if (r.get('status', 'completed') == 'completed') and self._host._owners.get(k) == self._principal]
        return max(done, key=lambda k: (table[k].get(key) or 0.0, k), default=None)

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        foreign = self._role == 'agent' and self._foreign_acq()
        values['acquisition.started_by_other_agent'] = foreign
        if foreign:
            values['acquisition.id'] = None
        values['result.last_record_id'] = self._own_last(h.records, 'finished_s')
        values['result.last_reduced_id'] = self._own_last(h.reduced, '_t')
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
            if capability_id in ('power.set', 'generator.emergency_stop') and (not h.power or capability_id == 'generator.emergency_stop'):
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED:
            rid = arguments.get(_RECORD_ARG[capability_id])
            if self._foreign(rid):
                raise ActionRejected('ACCESS_DENIED')              # unknown ids are reported by the runtime
        for arg in _ANALYSIS_RECORD_ARGS.get(capability_id, []):
            if arguments.get(arg) is not None and self._foreign(arguments[arg]):
                raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'data.reduce' and arguments.get('calibration_id') and self._foreign(arguments['calibration_id']):
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
                result = {k: [r for r in v if not self._foreign(r)] for k, v in result.items()}
            if capability_id == 'events.read' and self._role == 'agent':         # no injected faults, no hidden sample state (frozen) for agents
                result = dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in h.events
                                      if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_rid')) and e['kind'] != 'fault'
                                      and e['code'] not in _HIDDEN_EVENT_CODES], dropped=h.events_dropped)
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
                rid = h.operations[operation].get('record_id')
                if rid:
                    h._owners[rid] = self._principal
            for idk in ('reduced_id', 'calibration_id'):
                if capability_id in ('data.reduce', 'calibration.absolute') and result.get(idk):
                    h._owners[result[idk]] = self._principal
                    if idk == 'reduced_id':
                        h.reduced[result[idk]]['_t'] = h.time_s + len(h.reduced) * 1e-9
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


# ---------------------------------------------------------------- contract metadata
# immediate effects of an accepted call and the slots whose evolution over the following step differs (effect discovery over the scenario states of
# tests/test_asset_antonpaar_saxspace.py, noise-free and noisy runs)
EFFECTS = {
    'plant.configure': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'asx.busy', 'asx.tray_temperature_indicated_k',
        'chiller.flow_ok', 'detector.arm_indicated_rad', 'detector.total_counts', 'generator.current_indicated_a', 'generator.voltage_indicated_v', 'generator.xray_on',
        'panel.shutter_lamp', 'panel.standby_lamp', 'panel.warning_lamp', 'panel.xray_off_lamp', 'panel.xray_on_lamp', 'plant.configured', 'power.enabled',
        'shutter.open', 'stage.temperature_indicated_k', 'stage.y_indicated_m', 'stage.z_indicated_m', 'system.error_code', 'system.state', 'truth.current_a',
        'truth.flux_photons_s', 'truth.sdd_m', 'truth.stage_temperature_k', 'truth.transmission', 'truth.tray_temperature_k', 'truth.voltage_v', 'truth.zero_m',
        'vacuum.pressure_indicated_pa'],
    'sample.provide': ['sample.bench_ids', 'truth.samples'],
    'fault.inject': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'chiller.flow_ok', 'detector.total_counts',
        'panel.shutter_lamp', 'shutter.open', 'truth.faults', 'truth.flux_photons_s', 'truth.transmission'],
    'power.set': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'asx.busy', 'asx.tray_temperature_indicated_k',
        'chiller.flow_ok', 'detector.arm_indicated_rad', 'detector.total_counts', 'generator.current_indicated_a', 'generator.fault_code',
        'generator.voltage_indicated_v', 'generator.xray_on', 'panel.shutter_lamp', 'panel.standby_lamp', 'panel.warning_lamp', 'panel.xray_off_lamp',
        'panel.xray_on_lamp', 'power.enabled', 'shutter.open', 'stage.temperature_indicated_k', 'stage.y_indicated_m', 'stage.z_indicated_m', 'system.error_code',
        'system.state', 'truth.current_a', 'truth.flux_photons_s', 'truth.voltage_v', 'vacuum.pressure_indicated_pa'],
    'system.acknowledge': ['system.error_code'],
    'generator.set_key': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.total_counts', 'generator.key_angle_rad',
        'generator.key_on', 'generator.xray_on', 'panel.shutter_lamp', 'panel.warning_lamp', 'panel.xray_off_lamp', 'panel.xray_on_lamp', 'shutter.open',
        'truth.current_a', 'truth.flux_photons_s', 'truth.voltage_v'],
    'generator.xray_on': ['generator.xray_on', 'panel.warning_lamp', 'panel.xray_off_lamp', 'panel.xray_on_lamp'],
    'generator.xray_off': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.total_counts', 'generator.xray_on',
        'panel.shutter_lamp', 'panel.warning_lamp', 'panel.xray_off_lamp', 'panel.xray_on_lamp', 'shutter.open', 'truth.current_a', 'truth.flux_photons_s',
        'truth.voltage_v'],
    'generator.acknowledge': ['generator.fault_code', 'panel.standby_lamp', 'panel.xray_off_lamp'],
    'generator.emergency_stop': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.total_counts',
        'generator.emergency_stop_latched', 'generator.fault_code', 'generator.xray_on', 'panel.shutter_lamp', 'panel.standby_lamp', 'panel.warning_lamp',
        'panel.xray_off_lamp', 'panel.xray_on_lamp', 'shutter.open', 'truth.current_a', 'truth.flux_photons_s', 'truth.voltage_v'],
    'generator.release_emergency_stop': ['generator.emergency_stop_latched'],
    'generator.set_power': ['generator.current_setpoint_a', 'generator.voltage_setpoint_v'],
    'vacuum.set_pump': ['vacuum.pump_on'],
    'chamber.set_door': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'chamber.door_angle_rad', 'chamber.door_open',
        'detector.total_counts', 'panel.shutter_lamp', 'shutter.open', 'truth.pressure_pa', 'truth.transmission'],
    'stage.mount': ['stage.temperature_setpoint_k', 'stage.type'],
    'sample.load': ['holder.sample_id', 'holder.type', 'sample.bench_ids', 'truth.capillary_offset_m', 'truth.samples', 'truth.transmission'],
    'sample.unload': ['holder.broken', 'holder.sample_id', 'holder.type', 'sample.bench_ids', 'truth.capillary_offset_m', 'truth.samples', 'truth.transmission'],
    'stage.set_temperature': ['stage.temperature_setpoint_k'],
    'stage.move': ['stage.moving'],
    'stage.select_position': ['stage.moving'],
    'stage.auto_align': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'panel.shutter_lamp', 'shutter.open'],
    'collimation.set_mode': ['collimation.mode', 'truth.flux_photons_s'],
    'detector.move_arm': ['detector.arm_moving', 'detector.arm_target_rad'],
    'acquisition.start': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.total_counts', 'panel.shutter_lamp',
        'shutter.open'],
    'acquisition.swaxs_scan': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.total_counts'],
    'calibration.zero_angle': ['calibration.zero_m'],
    'calibration.q_scale': ['calibration.sdd_m'],
    'asx.set_door': ['asx.door_angle_rad', 'asx.door_open'],
    'asx.load_well': ['asx.wells', 'truth.samples'],
    'asx.unload_well': ['asx.wells', 'truth.samples'],
    'asx.set_tray_temperature': ['asx.tray_temperature_setpoint_k'],
    'asx.inject': ['asx.busy'],
    'asx.clean': ['asx.busy'],
}
EFFECTS['sim.step'] = ['clock.time_s']
EFFECTS['data.reduce'] = ['result.last_reduced_id']      # session slot of the caller
ON_STEP = {
    'plant.configure': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'asx.busy', 'asx.flow_cell_label', 'asx.gantry_x_m',
        'asx.gantry_y_m', 'asx.needle_z_m', 'asx.tray_temperature_indicated_k', 'asx.wells', 'chiller.flow_ok', 'detector.arm_angle_rad', 'detector.arm_indicated_rad',
        'detector.arm_moving', 'detector.arm_target_rad', 'detector.total_counts', 'generator.current_indicated_a', 'generator.voltage_indicated_v',
        'panel.shutter_lamp', 'shutter.open', 'stage.temperature_indicated_k', 'stage.y_indicated_m', 'stage.y_m', 'system.state', 'truth.current_a',
        'truth.flux_photons_s', 'truth.pressure_pa', 'truth.samples', 'truth.stage_temperature_k', 'truth.transmission', 'truth.tray_temperature_k', 'truth.voltage_v',
        'vacuum.pressure_indicated_pa', 'vacuum.ready'],
    'fault.inject': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'chiller.flow_ok', 'detector.arm_angle_rad',
        'detector.arm_indicated_rad', 'detector.arm_moving', 'detector.arm_target_rad', 'detector.total_counts', 'generator.current_indicated_a', 'generator.fault_code',
        'generator.voltage_indicated_v', 'generator.xray_on', 'panel.shutter_lamp', 'panel.standby_lamp', 'panel.warning_lamp', 'panel.xray_on_lamp', 'shutter.open',
        'stage.y_indicated_m', 'stage.y_m', 'truth.current_a', 'truth.flux_photons_s', 'truth.pressure_pa', 'truth.transmission', 'truth.voltage_v',
        'vacuum.pressure_indicated_pa', 'vacuum.ready'],
    'power.set': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'asx.busy', 'asx.flow_cell_label', 'asx.gantry_x_m',
        'asx.gantry_y_m', 'asx.needle_z_m', 'asx.tray_temperature_indicated_k', 'asx.wells', 'chiller.flow_ok', 'detector.arm_angle_rad', 'detector.arm_indicated_rad',
        'detector.arm_moving', 'detector.arm_target_rad', 'detector.total_counts', 'generator.current_indicated_a', 'generator.voltage_indicated_v',
        'panel.shutter_lamp', 'shutter.open', 'stage.temperature_indicated_k', 'stage.y_indicated_m', 'stage.y_m', 'system.state', 'truth.current_a',
        'truth.flux_photons_s', 'truth.pressure_pa', 'truth.samples', 'truth.stage_temperature_k', 'truth.transmission', 'truth.tray_temperature_k', 'truth.voltage_v',
        'vacuum.pressure_indicated_pa', 'vacuum.ready'],
    'generator.set_key': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.arm_angle_rad',
        'detector.arm_indicated_rad', 'detector.arm_moving', 'detector.arm_target_rad', 'detector.total_counts', 'generator.current_indicated_a',
        'generator.voltage_indicated_v', 'panel.shutter_lamp', 'shutter.open', 'stage.y_indicated_m', 'stage.y_m', 'truth.current_a', 'truth.flux_photons_s',
        'truth.transmission', 'truth.voltage_v'],
    'generator.xray_on': ['generator.current_indicated_a', 'generator.voltage_indicated_v', 'truth.current_a', 'truth.flux_photons_s', 'truth.voltage_v'],
    'generator.xray_off': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.arm_angle_rad',
        'detector.arm_indicated_rad', 'detector.arm_moving', 'detector.arm_target_rad', 'detector.total_counts', 'generator.current_indicated_a',
        'generator.voltage_indicated_v', 'panel.shutter_lamp', 'shutter.open', 'stage.y_indicated_m', 'stage.y_m', 'truth.current_a', 'truth.flux_photons_s',
        'truth.transmission', 'truth.voltage_v'],
    'generator.emergency_stop': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.arm_angle_rad',
        'detector.arm_indicated_rad', 'detector.arm_moving', 'detector.arm_target_rad', 'detector.total_counts', 'generator.current_indicated_a',
        'generator.voltage_indicated_v', 'panel.shutter_lamp', 'shutter.open', 'stage.y_indicated_m', 'stage.y_m', 'truth.current_a', 'truth.flux_photons_s',
        'truth.transmission', 'truth.voltage_v'],
    'generator.set_power': ['detector.total_counts', 'generator.current_indicated_a', 'generator.voltage_indicated_v', 'truth.current_a', 'truth.flux_photons_s',
        'truth.voltage_v'],
    'vacuum.set_pump': ['detector.total_counts', 'truth.pressure_pa', 'truth.transmission', 'vacuum.pressure_indicated_pa', 'vacuum.ready'],
    'chamber.set_door': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'detector.total_counts', 'panel.shutter_lamp',
        'shutter.open', 'truth.pressure_pa', 'truth.transmission', 'vacuum.pressure_indicated_pa'],
    'stage.mount': ['detector.total_counts', 'stage.temperature_indicated_k', 'truth.stage_temperature_k'],
    'sample.load': ['holder.broken', 'holder.sample_id', 'system.error_code', 'truth.samples', 'truth.transmission'],
    'stage.set_temperature': ['detector.total_counts', 'stage.temperature_indicated_k', 'truth.stage_temperature_k'],
    'stage.move': ['stage.moving', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'truth.transmission'],
    'stage.select_position': ['stage.moving', 'stage.y_indicated_m', 'stage.y_m', 'truth.transmission'],
    'stage.auto_align': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'panel.shutter_lamp', 'shutter.open', 'stage.y_indicated_m', 'stage.y_m',
        'truth.transmission'],
    'collimation.set_mode': ['truth.flux_photons_s'],
    'detector.move_arm': ['detector.arm_angle_rad', 'detector.arm_indicated_rad', 'detector.arm_moving'],
    'acquisition.start': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent',
        'detector.total_counts', 'panel.shutter_lamp', 'result.last_record_id', 'shutter.open'],
    'acquisition.swaxs_scan': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent',
        'detector.arm_angle_rad', 'detector.arm_indicated_rad', 'detector.arm_target_rad', 'detector.total_counts', 'panel.shutter_lamp', 'result.last_record_id',
        'shutter.open'],
    'asx.set_tray_temperature': ['asx.tray_temperature_indicated_k', 'truth.tray_temperature_k'],
    'asx.inject': ['asx.busy', 'asx.flow_cell_label', 'asx.gantry_x_m', 'asx.gantry_y_m', 'asx.needle_z_m', 'asx.wells', 'truth.samples'],
    'asx.clean': ['asx.busy', 'asx.flow_cell_label', 'asx.flow_cell_state', 'asx.gantry_x_m', 'asx.gantry_y_m', 'asx.needle_z_m', 'truth.samples'],
    'sim.step': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.remaining_s', 'acquisition.started_by_other_agent', 'asx.busy',
        'asx.flow_cell_label', 'asx.flow_cell_state', 'asx.gantry_x_m', 'asx.gantry_y_m', 'asx.needle_z_m', 'asx.tray_temperature_indicated_k', 'asx.wells',
        'chiller.flow_ok', 'detector.arm_angle_rad', 'detector.arm_indicated_rad', 'detector.arm_moving', 'detector.arm_target_rad', 'detector.total_counts',
        'generator.current_indicated_a', 'generator.fault_code', 'generator.voltage_indicated_v', 'generator.xray_on', 'holder.broken', 'holder.sample_id',
        'panel.shutter_lamp', 'panel.standby_lamp', 'panel.warning_lamp', 'panel.xray_on_lamp', 'result.last_record_id', 'shutter.open', 'stage.moving',
        'stage.temperature_indicated_k', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'system.error_code', 'system.state', 'truth.current_a',
        'truth.flux_photons_s', 'truth.pressure_pa', 'truth.samples', 'truth.stage_temperature_k', 'truth.transmission', 'truth.tray_temperature_k', 'truth.voltage_v',
        'vacuum.pressure_indicated_pa', 'vacuum.ready'],
}

for _k, _v in EFFECTS.items():
    CAPS[_k]['effects'] = list(_v)
ON_STEP_ZH = {
    'plant.configure': '重新声明的条件在下一仪器周期起作用：室温改变断电时样品台与进样盘温度的弛豫目标；供电超出230V±10%时整机断电（高压、泵、电机与控温停止，运行中的采集以POWER_OFF失败，'
                       '样品室经漏率缓慢回升）',
    'sample.provide': '新样品只在台面上，之后不随时间变化',
    'fault.inject': '注入或清除的故障在之后的周期起作用：vacuum_leak使抽气平衡压力升到真空就绪限以上；tube_aged使光通量下降；beamstop_misaligned使主束偏离束挡、透过计数升高；'
                    'capillary_bubble减少毛细管内的光程；chiller_failure使冷却水流量不足，高压在下一周期跳闸并锁存COOLING_WATER_LOW',
    'power.set': '开机：约60s后SAXSdrive就绪，约30s后冷水机建立流量；样品台与进样盘按设定值控温，泵按上次状态抽气，YZ台、WAXS臂与龙门架恢复驱动；断电：高压、泵与电机停止，'
                 '样品室经漏率缓慢回升，样品台与进样盘向室温弛豫，运行中的采集以POWER_OFF失败',
    'system.acknowledge': '确认后不再阻止依赖“无系统错误”的命令，过程量照常演化',
    'generator.set_key': '钥匙关闭时高压立即切断，之后电压电流读数为零、光管不再累计电能；钥匙打开本身不改变过程，需再按X-RAY ON',
    'generator.xray_on': '高压按2kV/s、电流按5mA/s升到设定值，光管电能与开启时间累计，警示灯与X-RAY ON灯亮；升到设定值后才可开快门；冷却水不足时在下一周期跳闸',
    'generator.xray_off': '高压关闭后电压电流读数为零、光管不再累计电能，运行中的采集以XRAY_OFF失败',
    'generator.acknowledge': '确认锁存故障后STANDBY灯熄灭，可再次开高压；过程量照常演化',
    'generator.emergency_stop': '高压切断并自锁，电压电流读数为零，运行中的采集以XRAY_OFF失败；释放急停并按STANDBY确认前不能开高压',
    'generator.release_emergency_stop': '急停复位本身不改变过程，仍需按STANDBY确认锁存故障',
    'generator.set_power': '高压开着时电压与电流按2kV/s、5mA/s移到新设定值，光通量与光管电能随之变化；高压关着时只在下次开启时生效',
    'vacuum.set_pump': '开泵：样品室按V·dp/dt=−S(p−p_ult)+Q抽气（时间常数约30s，从大气压到300Pa约3min），压力读数与真空就绪随之变化；关泵：放气阀打开，压力按8s时间常数回到大气压',
    'chamber.set_door': '开门时样品室处于大气压、快门保持关闭；关门后压力保持大气压直到开泵',
    'stage.mount': '换台后设定值被限制在新台的温度范围内，样品台温度随之向新设定值变化',
    'sample.load': '装入的液体样品随样品台温度演化：低于−10°C冻结、高于95°C沸腾使毛细管破裂（样品进入样品室、压力上升、记SAMPLE_BURST）、蛋白高于60°C逐渐聚集',
    'sample.unload': '取出的样品不再随样品台温度演化',
    'stage.set_temperature': '样品台温度按10°C/min斜率上限与20s时间常数趋近设定值，读数随之变化；水样低于−10°C冻结、高于95°C沸腾使毛细管破裂（压力上升、记错误），蛋白高于60°C逐渐聚集',
    'stage.move': 'YZ台按1mm/s移动到目标位置，移动中stage.moving为真；位置决定光束落在样品的哪一段',
    'stage.select_position': 'YZ台按1mm/s移到毛细管位（Y=0）或流通池位（Y=−8mm），移动中stage.moving为真',
    'stage.auto_align': '快门打开60s做透过率扫描（光子计入账本），结束时YZ台移到找到的毛细管/流通池轴线；找不到圆柱时对准失败、位置不变',
    'collimation.set_mode': '新准直方式改变之后曝光的通量、狭缝长度模糊与寄生散射；空闲时没有随时间变化的量',
    'detector.move_arm': 'WAXS臂按5°/s转到目标角，移动中detector.arm_moving为真，臂角读数随之变化',
    'acquisition.start': '快门打开按帧曝光：进度、剩余时间与总计数随时间增长，光子账累计；曝光结束关快门并生成记录；采集中开门、高压关闭、过曝保护或断电时记录以failed结束并保留已采部分',
    'acquisition.swaxs_scan': '依次把WAXS臂转到各角度、稳定2s后开快门曝光：进度、剩余时间与总计数随时间增长；每段结束写入分段计数，全部完成后生成记录；中断时保留已完成的段',
    'calibration.zero_angle': '新零点只用于之后的采集记录与数据处理，没有随时间变化的量',
    'calibration.q_scale': '新样品-探测器距离只用于之后的采集记录与数据处理，没有随时间变化的量',
    'asx.set_door': '进样器门开着时不能进样或清洗；门本身没有随时间变化的量',
    'asx.load_well': '孔中样品留在进样盘上，等待进样',
    'asx.unload_well': '取出的孔不再参与进样',
    'asx.set_tray_temperature': '进样盘温度按300s时间常数趋近设定值（4°C至室温），读数随之变化',
    'asx.inject': '龙门架按0.1m/s移到样品孔，针以0.02m/s下降，吸样8s，移到进样口注入流通池8s后回原位；上一样品按1%残留混入；完成后流通池内容、标签与孔中剩余体积更新',
    'asx.clean': '龙门架移到进样口冲洗100s，流通池残留降到1×10⁻⁴后回原位；冲洗期间流通池为空',
    'sim.step': '统一宿主时钟推进全部过程：高压斜坡与冷却水联锁、真空、样品台与进样盘温度、样品冻结/破裂/聚集、YZ台/WAXS臂/龙门架运动、快门与采集计数、自动进样与清洗，以及供电异常时的断路',
}
_SAMPLING_READINGS = {'generator.voltage_indicated_v': 'kV meter (0.1 kV)', 'generator.current_indicated_a': 'mA meter (0.1 mA)',
                      'vacuum.pressure_indicated_pa': 'Pirani gauge, 5 % relative noise', 'stage.temperature_indicated_k': 'stage sensor, 0.03 K noise, 0.01 K resolution',
                      'asx.tray_temperature_indicated_k': 'tray sensor, 0.1 K noise'}
_SAMPLING_CONTINUOUS = {'clock.time_s', 'detector.arm_angle_rad', 'detector.arm_indicated_rad', 'detector.arm_moving', 'stage.y_m', 'stage.z_m', 'stage.y_indicated_m',
                        'stage.z_indicated_m', 'stage.moving', 'asx.gantry_x_m', 'asx.gantry_y_m', 'asx.needle_z_m', 'truth.pressure_pa', 'truth.stage_temperature_k',
                        'truth.tray_temperature_k', 'truth.voltage_v', 'truth.current_a', 'truth.flux_photons_s', 'truth.transmission', 'acquisition.progress',
                        'acquisition.remaining_s', 'vacuum.ready', 'shutter.open', 'panel.shutter_lamp', 'detector.total_counts'}
_SAMPLING_CYCLE = {'system.state', 'system.error_code', 'power.enabled', 'chiller.flow_ok', 'generator.xray_on', 'generator.fault_code', 'panel.warning_lamp',
                   'panel.xray_on_lamp', 'panel.standby_lamp', 'panel.xray_off_lamp', 'acquisition.id', 'acquisition.kind', 'acquisition.started_by_other_agent',
                   'result.last_record_id', 'result.last_reduced_id', 'truth.samples', 'holder.broken', 'asx.busy', 'asx.wells', 'asx.flow_cell_state',
                   'asx.flow_cell_label', 'sample.bench_ids'}


def _sampling_text(k):
    if k in _SAMPLING_CONTINUOUS:
        return ('Instantaneous value at the read time: motions (detector arm, YZ stage, autosampler gantry), generator ramps, vacuum and temperatures are '
                'integrated continuously between the 1 s instrument cycles and cut at every event (arm arrival, end of exposure, settle, dwell); counts are '
                'integrated with the state at each cycle and command.')
    if k in _SAMPLING_READINGS:
        return f'Reading sampled at the latest 1 s instrument cycle and held until the next one ({_SAMPLING_READINGS[k]}); null while unpowered.'
    if k in _SAMPLING_CYCLE:
        return ('Process or record state changed by commands, by events of the continuous integration (acquisition segments, autosampler phases) or on the 1 s '
                'cycle (start-up, cooling-water trip, freezing, boiling burst, protein aggregation).')
    return 'Changes only when a command is accepted (set point, configuration or bookkeeping) or, for the joint mirrors, with the command at once; no time dynamics.'


_R = ['power.enabled', 'system.state', 'system.error_code']
_BEAM = _R + ['acquisition.id', 'chamber.door_open', 'generator.xray_on', 'generator.voltage_indicated_v', 'generator.current_indicated_a', 'vacuum.ready',
              'vacuum.pressure_indicated_pa', 'stage.moving', 'detector.arm_moving']
PRECONDITIONS = {
    'plant.configure': ('无；随机数种子只在首次声明时生效', []),
    'sample.provide': ('已声明实验室条件；ID不重复（最多40个）；溶液/分散液须为水（缓冲液）且有体积、无厚度；固体/粉末须有厚度、体积为0且只有一个组分；SASBDB曲线与金纳米颗粒须给浓度；'
                       '玻璃碳厚度须为证书1.055mm；山嵛酸银与硅粉须为粉末；模型组分参数须在范围内', ['plant.configured', 'sample.bench_ids']),
    'fault.inject': ('已声明实验室条件', ['plant.configured']),
    'power.set': ('已声明实验室条件；开机时供电须在230V±10%内（工程判断，额定值未公开）', ['plant.configured', 'power.enabled']),
    'system.acknowledge': ('通电且有系统级错误；SAMPLE_BURST须先取出破裂的样品架', ['power.enabled', 'system.error_code', 'holder.broken']),
    'generator.set_key': ('已声明实验室条件', ['plant.configured']),
    'generator.xray_on': ('通电、SAXSdrive已启动、无系统错误；钥匙在I、急停释放、无发生器故障、冷却水流量正常', _R + ['generator.key_on', 'generator.emergency_stop_latched',
                                                                                       'generator.fault_code', 'chiller.flow_ok']),
    'generator.xray_off': ('通电', ['power.enabled']), 'generator.acknowledge': ('通电；有发生器故障；急停已释放；冷却水故障须已恢复',
                                                                                ['power.enabled', 'generator.fault_code', 'generator.emergency_stop_latched', 'chiller.flow_ok']),
    'generator.emergency_stop': ('已声明实验室条件（任何时候可按）', ['plant.configured']),
    'generator.release_emergency_stop': ('急停处于自锁', ['generator.emergency_stop_latched']),
    'generator.set_power': ('通电、SAXSdrive已启动、无系统错误', list(_R)),
    'vacuum.set_pump': ('通电；抽真空时门须关闭', ['power.enabled', 'chamber.door_open']),
    'chamber.set_door': ('已声明实验室条件；开门时须已放气（>0.9atm）且泵不在抽（门开时若在空气中测量，采集以INTERLOCK_DOOR失败）',
                         ['plant.configured', 'vacuum.pressure_indicated_pa', 'vacuum.pump_on', 'acquisition.id']),
    'stage.mount': ('已声明实验室条件；门开；无采集；样品架空且未破裂；台温在室温±5K', ['plant.configured', 'chamber.door_open', 'acquisition.id', 'holder.type', 'holder.broken',
                                                                    'stage.temperature_indicated_k']),
    'sample.load': ('已声明实验室条件；门开；无采集；样品架空且未破裂；液体只能装毛细管/µ-Cell且实验台剩余体积≥60µL/10µL；粉末只能装PasteCell、固体只能装固体样品架；null=空毛细管',
                    ['plant.configured', 'chamber.door_open', 'acquisition.id', 'holder.type', 'holder.broken', 'sample.bench_ids']),
    'sample.unload': ('已声明实验室条件；门开；无采集；座上有样品架（或破裂的样品架）', ['plant.configured', 'chamber.door_open', 'acquisition.id', 'holder.type', 'holder.broken']),
    'stage.set_temperature': ('通电、SAXSdrive已启动、无系统错误；温度在当前样品台范围内（TCStage −30…150°C，扩展台−150…600°C）', _R + ['stage.type']),
    'stage.move': ('通电、SAXSdrive已启动、无系统错误；无采集', _R + ['acquisition.id']),
    'stage.select_position': ('通电、SAXSdrive已启动、无系统错误；无采集', _R + ['acquisition.id']),
    'stage.auto_align': ('同采集的光束前提；探测器臂在SAXS位（0）', _BEAM + ['detector.arm_indicated_rad']),
    'collimation.set_mode': ('通电、SAXSdrive已启动、无系统错误；无采集', _R + ['acquisition.id']),
    'detector.move_arm': ('通电、SAXSdrive已启动、无系统错误；无采集', _R + ['acquisition.id']),
    'acquisition.start': ('通电、SAXSdrive已启动、无系统错误与采集；门关；X射线在设定值；真空就绪（<3mbar）或已放气（空气中）；YZ台与探测器臂静止；记录未满200条', list(_BEAM)),
    'acquisition.swaxs_scan': ('同单次采集', list(_BEAM)),
    'acquisition.result': ('记录存在且不属于另一个agent', []), 'calibration.zero_angle': ('记录完成且含SAXS位主束', []),
    'calibration.q_scale': ('记录完成、含SAXS位段且至少两级山嵛酸银峰可拟合', []),
    'calibration.absolute': ('记录完成、含SAXS位主束；水标定须给空毛细管背景且覆盖q 1–3nm⁻¹；玻璃碳须覆盖0.08–2.5nm⁻¹；背景与样品准直方式和段数一致', []),
    'data.reduce': ('记录完成、含SAXS位主束；背景与样品准直和臂位一致；标定存在且准直方式一致；去模糊只用于线准直', []),
    'data.result': ('处理曲线存在且不属于另一个agent', []), 'analysis.guinier': ('处理曲线存在；低q段有足够的正强度点', []),
    'analysis.molecular_weight': ('处理曲线为绝对强度、已去模糊且可做Guinier', []), 'analysis.peaks': ('处理曲线存在', []),
    'asx.set_door': ('已声明实验室条件；关门随时可以，开门时自动进样器不在工作', ['plant.configured', 'asx.busy']),
    'asx.load_well': ('已声明实验室条件；进样器门开；孔位空；实验台上的液体样品剩余体积足够', ['plant.configured', 'asx.door_open', 'asx.wells', 'sample.bench_ids']),
    'asx.unload_well': ('已声明实验室条件；进样器门开；孔位有样品', ['plant.configured', 'asx.door_open', 'asx.wells']),
    'asx.set_tray_temperature': ('通电、SAXSdrive已启动、无系统错误', list(_R)),
    'asx.inject': ('通电、SAXSdrive已启动、无系统错误与采集；进样器门关且不在工作；孔位样品≥30µL', _R + ['acquisition.id', 'asx.door_open', 'asx.busy', 'asx.wells']),
    'asx.clean': ('通电、SAXSdrive已启动、无系统错误与采集；进样器门关且不在工作', _R + ['acquisition.id', 'asx.door_open', 'asx.busy']),
    'records.list': ('无', []), 'events.read': ('无', []), 'ledger.read': ('无', []), 'provenance.read': ('无', []), 'sim.step': ('无', []),
}
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入结构、类型或范围', 'ACCESS_DENIED': '该角色无权调用，或当前采集/记录属于另一个agent', 'REQUEST_CONFLICT': '同一request_id已用于不同的请求',
    'UNKNOWN_INSTANCE': '实例ID不存在', 'UNSUPPORTED': '本配置不提供该功能（理由见function_zh）', 'UNKNOWN_CAPABILITY': '能力ID不存在', 'UNKNOWN_OPERATION': '操作ID不存在',
    'NOT_RUNNING': '操作不在进行中', 'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_OFF': '未通电（或断电中止了采集/进样）', 'NOT_READY': 'SAXSdrive仍在启动',
    'ERROR_PENDING': '有待确认的系统错误（先确认）', 'NO_ERROR': '没有待确认的系统错误', 'HOLDER_BROKEN': '破裂的样品架还在座上（先取出）',
    'MAINS_OUT_OF_RANGE': '供电电压超出230V±10%', 'MAINS_FAULT': '供电电压异常，系统断电', 'ACQUISITION_RUNNING': '有采集或自动对准正在进行',
    'SAMPLE_EXISTS': '样品ID已存在或实验台已有40个样品', 'SAMPLE_FORM_INVALID': '样品形态、溶剂、体积、厚度与组分不一致', 'MODEL_PARAMETERS_INVALID': '模型组分参数缺失或超出范围',
    'UNKNOWN_TEMPLATE': '模板不在SAXS模板库中', 'UNKNOWN_MATERIAL': '材料不在模板库材料表中', 'CONCENTRATION_REQUIRED': '溶液组分须给大于0的浓度',
    'KEY_OFF': '钥匙开关在0位', 'EMERGENCY_STOP': '急停自锁（先旋转释放并按STANDBY确认）', 'GENERATOR_FAULT': '有待确认的发生器故障（按STANDBY）',
    'COOLING_WATER_LOW': '光管冷却水流量不足（冷却水机未就绪或故障）', 'NO_FAULT': '没有待确认的发生器故障', 'NOT_LATCHED': '急停没有自锁',
    'DOOR_OPEN': '样品室门开着', 'CHAMBER_UNDER_VACUUM': '样品室在真空下，门打不开（先放气）', 'PUMP_ON': '泵正在抽气（先放气）', 'DOOR_CLOSED': '样品室门关着（先放气开门）',
    'HOLDER_OCCUPIED': '毛细管座上已有样品架', 'STAGE_NOT_AMBIENT': '样品台温度未回到室温±5K', 'UNKNOWN_SAMPLE': '样品ID不存在', 'HOLDER_UNSUITABLE': '样品形态与样品架不匹配',
    'INSUFFICIENT_VOLUME': '剩余体积不足', 'SAMPLE_NOT_ON_BENCH': '固体/粉末样品不在实验台上', 'HOLDER_EMPTY': '座上没有样品架',
    'TEMPERATURE_OUT_OF_RANGE': '温度超出当前样品台范围', 'XRAY_NOT_READY': 'X射线未开或未到设定值', 'VACUUM_NOT_READY': '光路既未抽到<3mbar也未放气（抽/放气进行中）',
    'MOTION_IN_PROGRESS': 'YZ台或探测器臂仍在运动', 'RECORDS_FULL': '记录已满（200条）', 'ARM_NOT_AT_SAXS': '探测器臂不在SAXS位（自动对准需要主束）',
    'UNKNOWN_RECORD': '采集记录不存在', 'RECORD_NOT_COMPLETED': '采集记录未完成', 'NO_PRIMARY_BEAM': '记录里找不到可用的衰减主束（没有SAXS位段，或提示零点±1mm内没有束形的衰减主束峰；不能归一或定零点）',
    'CALIBRANT_NOT_FOUND': '记录中可拟合的山嵛酸银峰少于两级', 'BACKGROUND_MISMATCH': '背景与样品的准直或臂位不一致', 'BACKGROUND_REQUIRED': '水标定需要空毛细管背景',
    'STANDARD_RANGE_NOT_COVERED': '标定所需q范围没有足够的有效点', 'STANDARD_SIGNAL_INVALID': '标准样扣背景后的信号不为正', 'UNKNOWN_CALIBRATION': '标定不存在',
    'CALIBRATION_MISMATCH': '标定与记录的准直方式不同', 'UNKNOWN_REDUCED': '处理曲线不存在', 'GUINIER_FAILED': '低q段没有可拟合的Guinier区',
    'ABSOLUTE_SCALE_REQUIRED': '分子量需要绝对强度（先做绝对标定）', 'DESMEAR_REQUIRED': '须用去模糊的曲线（条带长度模糊使I(0)偏低：线准直约30%，点准直约6–20%）', 'ASX_DOOR_OPEN': '自动进样器门开着', 'ASX_BUSY': '自动进样器正在工作', 'ASX_DOOR_CLOSED': '自动进样器门关着',
    'WELL_OCCUPIED': '孔位已有样品', 'WELL_EMPTY': '孔位没有样品', 'USER_CANCEL': '操作被取消', 'XRAY_OFF': '采集中高压关闭（按键、钥匙、急停或冷却水不足）',
    'INTERLOCK_DOOR': '采集中打开了样品室门（快门联锁关闭）', 'DETECTOR_OVEREXPOSURE': '条带计数率超过保护阈值（主束直射探测器，检查束挡），快门关闭',
    'NO_CAPILLARY_FOUND': 'TrueFocus在±2.5mm内没有找到毛细管/流通池', 'SAMPLE_BURST': '样品加热沸腾，毛细管/流通池破裂',
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_RE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING']
_BEAM_ERR = _RE + ['ACQUISITION_RUNNING', 'DOOR_OPEN', 'XRAY_NOT_READY', 'VACUUM_NOT_READY', 'MOTION_IN_PROGRESS', 'RECORDS_FULL']
CAP_ERRORS = {
    'plant.configure': [], 'sample.provide': ['PLANT_REQUIRED', 'SAMPLE_EXISTS', 'SAMPLE_FORM_INVALID', 'MODEL_PARAMETERS_INVALID', 'UNKNOWN_TEMPLATE', 'UNKNOWN_MATERIAL',
                                              'CONCENTRATION_REQUIRED'],
    'fault.inject': ['PLANT_REQUIRED'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'system.acknowledge': ['PLANT_REQUIRED', 'POWER_OFF', 'NO_ERROR', 'HOLDER_BROKEN'],
    'generator.set_key': ['PLANT_REQUIRED'], 'generator.xray_on': _RE + ['KEY_OFF', 'EMERGENCY_STOP', 'GENERATOR_FAULT', 'COOLING_WATER_LOW'],
    'generator.xray_off': ['PLANT_REQUIRED', 'POWER_OFF'], 'generator.acknowledge': ['PLANT_REQUIRED', 'POWER_OFF', 'NO_FAULT', 'EMERGENCY_STOP', 'COOLING_WATER_LOW'],
    'generator.emergency_stop': ['PLANT_REQUIRED'], 'generator.release_emergency_stop': ['PLANT_REQUIRED', 'NOT_LATCHED'], 'generator.set_power': list(_RE),
    'vacuum.set_pump': ['PLANT_REQUIRED', 'POWER_OFF', 'DOOR_OPEN'], 'chamber.set_door': ['PLANT_REQUIRED', 'CHAMBER_UNDER_VACUUM', 'PUMP_ON'],
    'stage.mount': ['PLANT_REQUIRED', 'DOOR_CLOSED', 'ACQUISITION_RUNNING', 'HOLDER_OCCUPIED', 'STAGE_NOT_AMBIENT'],
    'sample.load': ['PLANT_REQUIRED', 'DOOR_CLOSED', 'ACQUISITION_RUNNING', 'HOLDER_OCCUPIED', 'UNKNOWN_SAMPLE', 'HOLDER_UNSUITABLE', 'INSUFFICIENT_VOLUME',
                    'SAMPLE_NOT_ON_BENCH'],
    'sample.unload': ['PLANT_REQUIRED', 'DOOR_CLOSED', 'ACQUISITION_RUNNING', 'HOLDER_EMPTY'], 'stage.set_temperature': _RE + ['TEMPERATURE_OUT_OF_RANGE'],
    'stage.move': _RE + ['ACQUISITION_RUNNING'], 'stage.select_position': _RE + ['ACQUISITION_RUNNING'],
    'stage.auto_align': _BEAM_ERR + ['ARM_NOT_AT_SAXS', 'USER_CANCEL', 'XRAY_OFF', 'INTERLOCK_DOOR', 'DETECTOR_OVEREXPOSURE', 'NO_CAPILLARY_FOUND'],
    'collimation.set_mode': _RE + ['ACQUISITION_RUNNING'], 'detector.move_arm': _RE + ['ACQUISITION_RUNNING'],
    'acquisition.start': _BEAM_ERR + ['USER_CANCEL', 'XRAY_OFF', 'INTERLOCK_DOOR', 'DETECTOR_OVEREXPOSURE'],
    'acquisition.swaxs_scan': _BEAM_ERR + ['USER_CANCEL', 'XRAY_OFF', 'INTERLOCK_DOOR', 'DETECTOR_OVEREXPOSURE'],
    'acquisition.result': ['UNKNOWN_RECORD'], 'calibration.zero_angle': ['UNKNOWN_RECORD', 'RECORD_NOT_COMPLETED', 'NO_PRIMARY_BEAM'],
    'calibration.q_scale': ['UNKNOWN_RECORD', 'RECORD_NOT_COMPLETED', 'NO_PRIMARY_BEAM', 'CALIBRANT_NOT_FOUND'],
    'calibration.absolute': ['UNKNOWN_RECORD', 'RECORD_NOT_COMPLETED', 'NO_PRIMARY_BEAM', 'BACKGROUND_MISMATCH', 'BACKGROUND_REQUIRED', 'STANDARD_RANGE_NOT_COVERED',
                             'STANDARD_SIGNAL_INVALID'],
    'data.reduce': ['UNKNOWN_RECORD', 'RECORD_NOT_COMPLETED', 'NO_PRIMARY_BEAM', 'BACKGROUND_MISMATCH', 'UNKNOWN_CALIBRATION', 'CALIBRATION_MISMATCH'],
    'data.result': ['UNKNOWN_REDUCED'], 'analysis.guinier': ['UNKNOWN_REDUCED', 'GUINIER_FAILED'],
    'analysis.molecular_weight': ['UNKNOWN_REDUCED', 'ABSOLUTE_SCALE_REQUIRED', 'DESMEAR_REQUIRED', 'GUINIER_FAILED'], 'analysis.peaks': ['UNKNOWN_REDUCED'],
    'asx.set_door': ['PLANT_REQUIRED', 'ASX_BUSY'], 'asx.load_well': ['PLANT_REQUIRED', 'ASX_DOOR_CLOSED', 'WELL_OCCUPIED', 'UNKNOWN_SAMPLE', 'HOLDER_UNSUITABLE',
                                                                     'INSUFFICIENT_VOLUME'],
    'asx.unload_well': ['PLANT_REQUIRED', 'ASX_DOOR_CLOSED', 'WELL_EMPTY'], 'asx.set_tray_temperature': list(_RE),
    'asx.inject': _RE + ['ASX_DOOR_OPEN', 'ASX_BUSY', 'ACQUISITION_RUNNING', 'WELL_EMPTY', 'INSUFFICIENT_VOLUME', 'USER_CANCEL'],
    'asx.clean': _RE + ['ASX_DOOR_OPEN', 'ASX_BUSY', 'ACQUISITION_RUNNING', 'USER_CANCEL'],
}
_PARTIAL = {'USER_CANCEL', 'XRAY_OFF', 'INTERLOCK_DOOR', 'DETECTOR_OVEREXPOSURE', 'POWER_OFF'}
CAP_ERROR_ZH = {('acquisition.start', 'POWER_OFF'): '未通电；或采集中断电（记录以failed结束）', ('acquisition.swaxs_scan', 'POWER_OFF'): '未通电；或扫描中断电（记录以failed结束）',
                ('asx.inject', 'POWER_OFF'): '未通电；或进样中断电（针内液体计入废液）'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件，没有可见模型变化', 'sample.provide': '只在过程层实验台上增加样品；场景没有样品几何（样品位由接口点capillary_seat/asx_tray表示）',
    'fault.inject': '评估者故障注入，没有可见模型变化', 'system.acknowledge': '只清除错误状态', 'generator.set_power': '设定值；面板kV/mA读数随step显示升降中的实际值',
    'stage.mount': '过程层更换样品台；场景中TCStage几何不变', 'sample.load': '样品只进入过程层样品架；场景的毛细管座不显示毛细管', 'sample.unload': '场景没有样品架几何',
    'stage.set_temperature': '温度没有可见几何；显示器温度读数随step变化', 'collimation.set_mode': '准直方式在Kratky块内部，没有可见几何',
    'acquisition.result': '只读记录', 'calibration.zero_angle': '标定只改换算', 'calibration.q_scale': '标定只改换算', 'calibration.absolute': '分析计算', 'data.reduce': '分析计算',
    'data.result': '只读', 'analysis.guinier': '分析计算', 'analysis.molecular_weight': '分析计算', 'analysis.peaks': '分析计算', 'asx.load_well': '场景样品盘不显示样品孔内容',
    'asx.unload_well': '场景样品盘不显示样品孔内容', 'asx.set_tray_temperature': '温度没有可见几何', 'records.list': '只读列表', 'events.read': '只读事件',
    'ledger.read': '只读账目（evaluator）', 'provenance.read': '只读来源记录（evaluator）',
}
# states that the Matterix binding mirrors into the scene (joints, panel readouts, monitor readouts and lamps; interface_definition)
SCENE_MIRRORED = {'chamber.door_angle_rad', 'asx.door_angle_rad', 'detector.arm_angle_rad', 'stage.y_m', 'stage.z_m', 'asx.gantry_x_m', 'asx.gantry_y_m', 'asx.needle_z_m',
                  'generator.key_angle_rad', 'generator.voltage_indicated_v', 'generator.current_indicated_a', 'acquisition.remaining_s', 'vacuum.pressure_indicated_pa',
                  'stage.temperature_indicated_k', 'detector.total_counts', 'panel.warning_lamp', 'panel.xray_on_lamp', 'panel.standby_lamp', 'panel.xray_off_lamp',
                  'panel.shutter_lamp'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节、面板/显示器读数或指示灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


_GUARDED = _CONTROL_GUARDED | _DATA_GUARDED | set(_ANALYSIS_RECORD_ARGS)
RESOURCES = {'acquisition.start': ['xray_beam', 'detector', 'shutter', 'host_clock'], 'acquisition.swaxs_scan': ['xray_beam', 'detector', 'detector_arm', 'shutter', 'host_clock'],
             'stage.auto_align': ['xray_beam', 'stage', 'shutter', 'host_clock'], 'asx.inject': ['autosampler', 'flow_cell', 'host_clock'],
             'asx.clean': ['autosampler', 'flow_cell', 'host_clock'], 'power.set': ['mains', 'vacuum_pump', 'chiller'],
             'generator.xray_on': ['generator', 'xray_tube', 'chiller'], 'generator.xray_off': ['generator'], 'generator.set_power': ['generator'],
             'generator.set_key': ['generator'], 'generator.acknowledge': ['generator'], 'generator.emergency_stop': ['generator', 'shutter'],
             'generator.release_emergency_stop': ['generator'], 'vacuum.set_pump': ['vacuum_pump', 'sample_chamber'], 'chamber.set_door': ['sample_chamber'],
             'stage.mount': ['stage'], 'sample.load': ['sample_holder', 'bench'], 'sample.unload': ['sample_holder', 'bench'], 'stage.set_temperature': ['stage'],
             'stage.move': ['stage'], 'stage.select_position': ['stage'], 'collimation.set_mode': ['collimation_block'], 'detector.move_arm': ['detector_arm'],
             'asx.set_door': ['autosampler'], 'asx.load_well': ['autosampler', 'bench'], 'asx.unload_well': ['autosampler'], 'asx.set_tray_temperature': ['autosampler'],
             'plant.configure': ['room', 'mains'], 'sample.provide': ['bench'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock'], 'system.acknowledge': ['saxsdrive']}
_ROBOT = '装卸、开关门、转钥匙与释放急停为宿主/机器人技能的过程层抽象，没有接触、力控或机器人验证；门、钥匙与进样器关节由过程状态镜像。'
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.saxspace_saxs.limitations。'


def _foreign_acq_text(k):
    if k not in _CONTROL_GUARDED:
        return ''
    return '；另一个agent的采集进行中时，agent调用被拒（ACCESS_DENIED；急停与未通电时开机除外）'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    access = ['ACCESS_DENIED'] if (CAPS[k]['visibility'] != 'agent' or k in _GUARDED) else []
    return list(dict.fromkeys(_COMMON_ERRORS + access + CAP_ERRORS.get(k, [])))


OBSERVES = {
    'plant.configure': ['plant.configured'], 'sample.provide': ['sample.bench_ids'], 'fault.inject': ['truth.faults'],
    'power.set': ['power.enabled', 'system.state', 'chiller.flow_ok'], 'system.acknowledge': ['system.error_code'],
    'generator.set_key': ['generator.key_on', 'generator.xray_on'], 'generator.xray_on': ['generator.xray_on', 'generator.voltage_indicated_v', 'generator.current_indicated_a',
                                                                                          'panel.warning_lamp'],
    'generator.xray_off': ['generator.xray_on', 'panel.xray_off_lamp'], 'generator.acknowledge': ['generator.fault_code', 'panel.standby_lamp'],
    'generator.emergency_stop': ['generator.emergency_stop_latched', 'generator.xray_on', 'generator.fault_code'],
    'generator.release_emergency_stop': ['generator.emergency_stop_latched'], 'generator.set_power': ['generator.voltage_setpoint_v', 'generator.current_setpoint_a',
                                                                                                      'generator.voltage_indicated_v', 'generator.current_indicated_a'],
    'vacuum.set_pump': ['vacuum.pump_on', 'vacuum.pressure_indicated_pa', 'vacuum.ready'], 'chamber.set_door': ['chamber.door_open'], 'stage.mount': ['stage.type'],
    'sample.load': ['holder.type', 'holder.sample_id', 'sample.bench_ids'], 'sample.unload': ['holder.type', 'holder.sample_id', 'holder.broken'],
    'stage.set_temperature': ['stage.temperature_setpoint_k', 'stage.temperature_indicated_k'], 'stage.move': ['stage.y_indicated_m', 'stage.z_indicated_m', 'stage.moving'],
    'stage.select_position': ['stage.y_indicated_m', 'stage.moving'], 'stage.auto_align': ['acquisition.kind', 'acquisition.progress', 'stage.y_indicated_m'],
    'collimation.set_mode': ['collimation.mode'], 'detector.move_arm': ['detector.arm_target_rad', 'detector.arm_indicated_rad', 'detector.arm_moving'],
    'acquisition.start': ['acquisition.id', 'acquisition.progress', 'acquisition.remaining_s', 'detector.total_counts', 'shutter.open', 'result.last_record_id'],
    'acquisition.swaxs_scan': ['acquisition.id', 'acquisition.progress', 'detector.arm_indicated_rad', 'result.last_record_id'], 'acquisition.result': [],
    'calibration.zero_angle': ['calibration.zero_m'], 'calibration.q_scale': ['calibration.sdd_m'], 'calibration.absolute': [], 'data.reduce': ['result.last_reduced_id'],
    'data.result': [], 'analysis.guinier': [], 'analysis.molecular_weight': [], 'analysis.peaks': [], 'asx.set_door': ['asx.door_open'],
    'asx.load_well': ['asx.wells', 'sample.bench_ids'], 'asx.unload_well': ['asx.wells'], 'asx.set_tray_temperature': ['asx.tray_temperature_setpoint_k',
                                                                                                                         'asx.tray_temperature_indicated_k'],
    'asx.inject': ['asx.busy', 'asx.flow_cell_state', 'asx.flow_cell_label', 'asx.wells'], 'asx.clean': ['asx.busy', 'asx.flow_cell_state'],
    'records.list': [], 'events.read': [], 'ledger.read': [], 'provenance.read': [], 'sim.step': ['clock.time_s'],
}
_WB, _AZOM, _SICK, _DECT, _ALLEC = 'user_workbook', 'supplier_azom', 'facility_sickkids', 'dectris_mythen2_r', 'allec2015_scirep'
_PU, _TH, _CUBO = 'epmc_pmc11957403', 'epmc_pmc8900854', 'epmc_pmc10548465'
_SAS, _LIBS, _AUDIT, _SRM, _RM, _KELL, _ORTH, _AGBE, _XRAYDB = ('sasbdb', 'saxs_library', 'library_validation', 'nist_srm3600', 'nist_gold_rms', 'jones_harris1992',
                                                               'orthaber2000', 'pyfai_agbh', 'xraydb')
_PHYS, _SIM = 'physics_literature', 'simulation_assumption'
CAP_SOURCES = {
    'plant.configure': [_SIM], 'sample.provide': [_WB, _LIBS, _SAS, _SRM, _RM, _AGBE, _XRAYDB, _AUDIT, _SIM], 'fault.inject': [_WB, _SIM],
    'power.set': [_AZOM, _SIM], 'system.acknowledge': [_SIM], 'generator.set_key': [_SICK, _SIM], 'generator.xray_on': [_AZOM, _PU, _TH, _CUBO, _SIM],
    'generator.xray_off': [_SICK, _SIM], 'generator.acknowledge': [_SICK, _SIM], 'generator.emergency_stop': [_SICK, _SIM], 'generator.release_emergency_stop': [_SICK, _SIM],
    'generator.set_power': [_PU, _TH, _CUBO, _AZOM, _SIM], 'vacuum.set_pump': [_ALLEC, _AZOM, _SIM], 'chamber.set_door': [_SICK, _SIM], 'stage.mount': [_AZOM, _SICK, _SIM],
    'sample.load': [_SICK, _ALLEC, _PU, _CUBO, _SIM], 'sample.unload': [_SICK, _SIM], 'stage.set_temperature': [_WB, _AZOM, _SICK, _PU, _SIM],
    'stage.move': [_AZOM, _SIM], 'stage.select_position': [_SICK, _SIM], 'stage.auto_align': [_AZOM, _SIM], 'collimation.set_mode': [_AZOM, _SICK, _ALLEC, _SIM],
    'detector.move_arm': [_WB, _AZOM, _DECT, _SIM], 'acquisition.start': [_WB, _AZOM, _DECT, _ALLEC, _PU, _TH, _CUBO, _SAS, _LIBS, _XRAYDB, _PHYS, _SIM],
    'acquisition.swaxs_scan': [_WB, _AZOM, _DECT, _LIBS, _PHYS, _SIM], 'acquisition.result': [_SIM], 'calibration.zero_angle': [_AZOM, _SIM],
    'calibration.q_scale': [_AGBE, _PHYS, _SIM], 'calibration.absolute': [_ORTH, _KELL, _SRM, _PHYS, _SIM], 'data.reduce': [_PU, _ALLEC, _PHYS, _SIM],
    'data.result': [_SIM], 'analysis.guinier': [_WB, _AZOM, _PHYS], 'analysis.molecular_weight': [_PHYS, _SIM], 'analysis.peaks': [_WB, _PHYS],
    'asx.set_door': [_AZOM, _SIM], 'asx.load_well': [_AZOM, _SICK, _SIM], 'asx.unload_well': [_SIM], 'asx.set_tray_temperature': [_SICK, _SIM],
    'asx.inject': [_AZOM, _SICK, _SIM], 'asx.clean': [_AZOM, _SIM], 'records.list': [_SIM], 'events.read': [_SIM], 'ledger.read': [_SIM], 'provenance.read': [_LIBS],
    'sim.step': [_SIM]}
for _k in UNSUPPORTED:
    CAP_SOURCES[_k] = [_AZOM, _WB] if _k not in ('hardware.connect', 'robot.contact_handling') else [_SIM]
PARAM_ZH = {'room_k': '实验室室温', 'mains_voltage_v': '供电电压（额定值未公开，按230V±10%工程判断）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）',
            'sample_id': '样品标签', 'form': '样品形态', 'solvent': '溶剂', 'volume_m3': '液体体积', 'thickness_m': '固体/粉末厚度', 'components': '组分列表'}
CAP_LIMITS = {
    'plant.configure': ['供电电压只按230V±10%判断开机与断路（额定值未公开，工程判断）；室温只影响空气散射、样品台/样品盘初始温度；本机仪器误差（真实样品到探测器距离±0.5mm、零点±0.05mm、'
                        '流通池偏差±0.03mm，1σ）由种子决定。'],
    'sample.provide': ['SASBDB曲线的绝对强度按I(0)/c=M(Δρ_M)²/N_A由分子量与序列电子数/偏比容推导（大分子），胶体按Porod不变量或通用衬度推导，见模板abs_scale；溶剂只实现水（缓冲盐不影响散射）；'
                       '浓度效应：Percus–Yevick硬球结构因子与溶剂置换；金纳米颗粒的Schulz宽度σ/R=0.10为假设；层状与六方孔模型为用户参数的合成模型（synthetic）。'],
    'fault.inject': ['故障幅度（泄漏0.2Pa·m³/s、通量×0.6、束挡偏0.5mm、气泡使光程×0.4）为工程值。'],
    'power.set': ['SAXSdrive启动60s、冷却水机建立流量30s为工程值；断电时阀门关闭、光路按1×10⁻⁴Pa·m³/s漏气。'],
    'generator.xray_on': ['管电压2kV/s、管电流5mA/s升降为工程值；Cu Kα通量∝I(V/V_K−1)^1.63（经验律），40kV/50mA线准直1.2×10⁹光子/s为工程值（按SASDK46误差棒量级校准）；'
                          '没有光管老练（conditioning）与寿命模型（故障注入可使通量×0.6）。'],
    'generator.set_power': ['Cu管设定上限40kV/50mA取已发表SAXSpace方法的工作点；Primux 3000额定功率未公开（null）。'],
    'vacuum.set_pump': ['光路与样品室为一个12L体积，旋片泵有效抽速0.4L/s、极限30Pa、放气时间常数8s为工程值；就绪阈值3mbar来自Allec等2015；空气散射按0.10m有效长度、吸收按0.60m光路计算。'],
    'chamber.set_door': [_ROBOT], 'stage.mount': [_ROBOT, '扩展温控台的液氮/加热功率与温度动态没有单独建模（与TCStage同一斜率与时间常数）。'],
    'sample.load': [_ROBOT, '毛细管装入时的横向安装偏差1σ=0.15mm（工程）决定需不需要TrueFocus对准；µ-Cell光程1mm、PasteCell光程1mm与2×25µm聚酰亚胺窗为工程值；粉末堆积分数0.5为假设。'],
    'sample.unload': [_ROBOT], 'stage.set_temperature': ['样品温度=台温（无梯度）；斜率上限10°C/min取热台文献值，时间常数20s为工程值；低于−10°C的水样冻结（冰Ih峰为近似），高于95°C的水样沸腾破裂，'
                                                         '蛋白高于60°C按一级速率聚集（聚集体Rg 25nm、质量50倍，均为假设）。'],
    'stage.auto_align': ['TrueFocus抽象为按透过率极小找毛细管/流通池轴（残差10µm），耗时60s，快门在对准期间打开（计入光子账），不产生记录。'],
    'collimation.set_mode': ['点准直通量为线准直的2%、光斑长度0.3mm为工程值；两种准直的束宽都按平顶0.12mm＋0.04mm边沿（工程），寄生散射按q⁻³（Kratky块“无散射”准直的残余，工程）。'],
    'detector.move_arm': ['2θ臂（半径=样品到探测器距离）为工程结构；臂角0.001°分辨，5°/s。'],
    'acquisition.start': ['计数：狭缝长度（20mm线束×8mm条带梯形核）与束宽模糊、Kα1/Kα2双线、偏振、平面模块立体角、450µm Si探测效率（xraydb）、半透明束挡（透过1×10⁻³）、死时间100ns与'
                          '每条带10⁶/s上限、泊松噪声；样品在光束中的程长按束宽上的圆柱截线平均；水、石英壁与空气的散射按模板与近似式。宽角吸收的角度依赖与样品荧光未建模。'],
    'acquisition.swaxs_scan': ['臂到位稳定2s为工程值；宽角段没有主束归一（沿用SAXS段的主束）；拼接由data.reduce完成。'],
    'calibration.zero_angle': ['零点=衰减主束峰的计数重心：各局部最大条带在完全位于束挡后的条带（±0.125mm）内迭代求重心到收敛，收敛点须在提示零点±0.15mm（零点不确定度3σ）内、找不到时在±1mm内；'
                               '只接受“束形”的峰（两侧在束边0.1mm与挡边0.15mm之间都有低于峰值1/4的条带，且在±25/50/75µm处左右对称到30%以内），束挡边外的散射峰一侧被挡板压低、不对称，不会被当成主束；与标定状态无关；'
                               '找不到束形峰时拒绝（NO_PRIMARY_BEAM）。'], 'calibration.q_scale': ['对每级山嵛酸银峰拟合“狭缝模糊的德拜环×高斯本征宽度”模型（含Kα双线）：79µm粗网格、4µm细网格与抛物线顶点求样品到探测器距离，取各级中位数；'
                             '模型测试中残余偏差约−0.03%…−0.05%（零点重心误差在低级峰上被放大）。'],
    'calibration.absolute': ['水：dΣ/dΩ=(n_e r_e)²k_BTκ_T（Kell 1975密度与压缩系数，Jones & Harris 1992转载），对数分箱后q 1–3nm⁻¹各箱比值的中位数；玻璃碳：NIST SRM 3600认证曲线经仪器狭缝核模糊后与1.055mm厚样逐箱比较（q 0.08–2.5nm⁻¹）；'
                             '不确定度=比值散布与标准不确定度合成。'],
    'data.reduce': ['归一到透过主束（半透明束挡计数/1×10⁻³），按标定几何校正平板模块立体角（cos³）与偏振；对数分箱Δlnq=0.012（箱内计数和÷期望计数，泊松无偏；误差√N），'
                    '扣背景（同一分箱），除厚度，乘标定因子；去模糊为正则化反演（仪器梯形狭缝核，二阶差分Tikhonov正则，λ按χ²/N=1的偏差原则选取，误差取解的协方差）；'
                    'q按当前零点与距离标定重新换算；超过600箱时等间隔抽取。'],
    'analysis.guinier': ['自动Guinier：由强度降到1/e处估初值，迭代选取q·Rg≤qrg_max；不做聚集判断。'],
    'analysis.molecular_weight': ['M=I(0)/c·N_A/(Δρ_M)²，Δρ_M由调用者给出（蛋白默认2.2×10¹¹m/kg）；须绝对标定且已去模糊（线、点准直都有条带长度模糊）；浓度误差与偏比容误差不在结果里。'],
    'analysis.peaks': ['峰=高于局部20%分位基线5σ、半高宽<0.3q且高度≥最强峰2%的局部极大，峰位取三点抛物线顶点（亚分箱）；对数分箱Δlnq=0.012使窄的宽角峰位精度约±0.5%；'
                       '线准直未去模糊的数据峰位偏低约1%（先去模糊或用点准直）。'],
    'asx.load_well': [_ROBOT], 'asx.unload_well': [_ROBOT], 'asx.set_door': [_ROBOT],
    'asx.inject': ['进样针XY 0.1m/s、Z 0.02m/s，吸样/注样各8s、注入30µL、流通池容积15µL、未清洗残留1%均为工程值；孔位坐标为工程布局。'],
    'asx.clean': ['清洗100s，清洗后残留为原样的0.01%（以干燥溶质计入流通池账），均为工程值。'],
}
LIMITATIONS = [
    '选定配置：Cu密封管（Primux 3000，Cu或Mo中的Cu）40kV/50mA、全真空多层膜光学、Kratky块线准直（20mm）可切换点准直、TCStage（−30…150°C）与扩展温控台、StageMaster YZ台、'
    'MYTHEN2 R 1K（1280×50µm）装于2θ臂（样品到探测器0.317m）、半透明束挡、ASX自动进样器（192孔、1mm石英流通池）；2D EIGER R、湿度/拉伸/GI-SAXS台与Mo管未装。',
    '强度链路：通量×透过率×程长×dΣ/dΩ×立体角×效率，狭缝长度与束宽模糊、Kα双线、偏振、平面模块几何、主束经半透明束挡；背景=空气（按压力）、石英/聚酰亚胺窗、寄生散射；'
    '所有吸收系数与散射长度密度来自xraydb（Chantler/Elam）与模板库材料表。',
    '模板：SASBDB实测曲线（145种物质的形状为实测，绝对强度为推导或假设），NIST SRM 3600（认证），水（压缩系数推导＋宽角峰假设），山嵛酸银与硅（d间距/晶格为文献或CODATA，强度为推导/假设），'
    'NIST金纳米颗粒（参考直径＋Schulz分布假设），层状/六方孔/球模型（用户参数，synthetic）。',
    '样品状态：冻结（−10°C）、沸腾破裂（95°C）、蛋白热聚集（60°C）为简化规则（阈值与速率为假设）；辐照损伤、溶剂蒸发与毛细管内气泡形成（除故障注入外）未建模。',
    '仪器误差：真实样品到探测器距离与零点有未知偏差（标定前q轴有系统误差），流通池与毛细管安装有横向偏差（对准前强度偏低）；读数噪声为工程值。',
    '没有真实SAXSpace、SAXSdrive、SAXSquant或探测器的硬件接口；机器人装卸与门/钥匙操作没有接触验证。',
]


def export_antonpaar_saxspace_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'AntonpaarSaxspaceSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        last = {'acquisition.start': 'result.last_record_id', 'acquisition.swaxs_scan': 'result.last_record_id'}.get(k)
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.antonpaar_saxspace.AntonpaarSaxspaceSession.invoke',
                                              invocation=f'session.invoke(instance_id="saxspace_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_acq_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['acquisition.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='AntonpaarSaxspaceRuntime.invoke / AntonpaarSaxspaceSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate',
                                     description_zh='设定、目标、状态标志或立即改变的派生量；压力、温度、位置、计数与操作进度只随step推进') for s in v['effects']],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=('操作结束（完成、失败或取消）：曝光时间（扫描为各段）累计到达、臂到位与稳定、对准或进样/清洗阶段完成后确定' if async_ else
                                                      '原子设定、配置、分析或查询，实际物理状态由step推进'),
                                completion_state_ids=(['acquisition.id', 'acquisition.progress'] + ([last] if last else []) if k in ('acquisition.start', 'acquisition.swaxs_scan', 'stage.auto_align')
                                                      else ['asx.busy', 'asx.flow_cell_state'] if async_ else []),
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('取消或失败（高压关闭、开门、过曝保护、断电）时记录以cancelled/failed结束，已完成的段保留在记录里，未完成段的计数丢弃；已入射的光子仍计入光子账。'
                                                    if k.startswith('acquisition') else
                                                    '取消或失败时样品台停在当时位置，快门关闭。' if k == 'stage.auto_align' else
                                                    '取消或失败时针内液体计入废液，已注入流通池的内容保留，龙门架停在当时位置。' if async_ else
                                                    ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                                ),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH[e]), side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH[k]) for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in v['effects'])]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'q_min_per_m': (Q_MIN_SPEC_PER_M, '1/m', '可测q下限0.03nm⁻¹（厂家规格）', _AZOM), 'q_max_per_m': (Q_MAX_SPEC_PER_M, '1/m', '可测q上限40.7nm⁻¹（厂家规格）', _AZOM),
            'swaxs_max_two_theta_rad': (SWAXS_MAX_2THETA_RAD, 'rad', 'TrueSWAXS到60° 2θ（厂家）', _AZOM),
            'tcstage_min_k': (TCSTAGE_RANGE_K[0], 'K', 'TCStage帕尔贴下限−30°C（设施页）', _SICK), 'tcstage_max_k': (TCSTAGE_RANGE_K[1], 'K', 'TCStage上限150°C（设施页）', _SICK),
            'stage_min_k': (EXTENDED_STAGE_RANGE_K[0], 'K', '温控样品台下限−150°C（厂家，按样品台）', _AZOM),
            'stage_max_k': (EXTENDED_STAGE_RANGE_K[1], 'K', '温控样品台上限600°C（厂家，按样品台）', _AZOM),
            'temperature_accuracy_k': (TEMP_ACCURACY_K, 'K', '控温±0.1°C（厂家）', _AZOM), 'hot_stage_ramp_k_s': (HOT_STAGE_RAMP_K_S, 'K/s', '热台升温速率10°C/min（J. Appl. Cryst. 2025方法）', _PU),
            'asx_wells': (ASX_WELLS, '1', '自动进样最多192个液体样品（厂家/设施页）', _AZOM), 'asx_tray_min_k': (ASX_TRAY_MIN_K, 'K', '样品盘可冷却到4°C（设施页）', _SICK),
            'capillary_diameter_m': (CAPILLARY_D_M, 'm', '1mm石英毛细管（设施页；Allec等2015）', _SICK), 'capillary_wall_m': (CAPILLARY_WALL_M, 'm', '毛细管壁厚0.01mm（Allec等2015）', _ALLEC),
            'capillary_volume_m3': (CAPILLARY_VOLUME_M3, 'm^3', '毛细管装样60µL（设施页）', _SICK), 'mu_cell_volume_m3': (MU_CELL_VOLUME_M3, 'm^3', 'µ-Cell 10µL（设施页）', _SICK),
            'line_length_m': (LINE_LENGTH_M, 'm', '线束长度2cm（Allec等2015；J. Appl. Cryst. 2025样品架狭缝长20mm）', _ALLEC),
            'vacuum_ready_pa': (VACUUM_READY_PA, 'Pa', '光路真空低于3mbar（Allec等2015）', _ALLEC), 'sdd_nominal_m': (SDD_NOMINAL_M, 'm', '样品到探测器317mm（多篇方法与SASBDB）', _PU),
            'tube_voltage_v': (KV_DEFAULT_V, 'V', '管电压40kV（已发表方法）', _PU), 'tube_current_a': (MA_DEFAULT_A, 'A', '管电流50mA（已发表方法）', _PU),
            'strips': (STRIPS, '1', 'MYTHEN2 R 1K 1280条', _DECT), 'strip_pitch_m': (STRIP_PITCH_M, 'm', '条宽50µm', _DECT), 'strip_length_m': (STRIP_LENGTH_M, 'm', '条长8mm（450µm传感器）', _DECT),
            'sensor_thickness_m': (SENSOR_THICKNESS_M, 'm', '传感器厚450µm', _DECT), 'max_rate_per_strip': (MAX_RATE_PER_STRIP, '1/s', '每条带10⁶计数/s', _DECT),
            'counter_bits': (24, '1', '24位动态范围', _DECT), 'frame_rate_hz': (FRAME_RATE_HZ, '1/s', '帧率100Hz', _DECT)}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=[src], uncertainty=None)
    for name, (val, unit, meaning) in {
            'flux_line_ref_photons_s': (FLUX_LINE_REF, '1/s', '线准直40kV/50mA样品处Cu Kα通量（按SASDK46误差棒量级校准）'), 'point_flux_fraction': (POINT_FLUX_FRACTION, '1', '点/线准直通量比'),
            'char_exponent': (CHAR_EXPONENT, '1', '特征线强度经验指数'), 'hv_ramp_v_s': (HV_RAMP_V_S, 'V/s', '管电压升降速率'), 'ma_ramp_a_s': (MA_RAMP_A_S, 'A/s', '管电流升降速率'),
            'boot_s': (BOOT_S, 's', 'SAXSdrive启动时间'), 'chiller_start_s': (CHILLER_START_S, 's', '冷却水建立流量时间'), 'beam_top_m': (BEAM_TOP_M, 'm', '束宽平顶'),
            'beam_edge_m': (BEAM_EDGE_M, 'm', '束宽线性边沿'), 'beamstop_half_m': (BEAMSTOP_HALF_M, 'm', '半透明束挡半宽'), 'beamstop_transmission': (BEAMSTOP_T, '1', '束挡透过率'),
            'sdd_sd_m': (SDD_SD_M, 'm', '真实样品到探测器距离偏差（1σ）'), 'zero_sd_m': (ZERO_SD_M, 'm', '零点偏差（1σ）'), 'v_vacuum_m3': (V_VAC_M3, 'm^3', '光路与样品室体积'),
            's_pump_m3_s': (S_PUMP_M3_S, 'm^3/s', '旋片泵有效抽速'), 'p_ultimate_pa': (P_ULT_PA, 'Pa', '极限压力'), 'leak_pa_m3_s': (LEAK_PA_M3_S, 'Pa*m^3/s', '漏率'),
            'vent_tau_s': (VENT_TAU_S, 's', '放气时间常数'), 'air_scatter_length_m': (AIR_SCATTER_LENGTH_M, 'm', '空气散射有效长度'), 'air_path_m': (AIR_PATH_M, 'm', '空气吸收光路'),
            'stage_tau_s': (STAGE_TAU_S, 's', '样品台温度时间常数'), 'arm_speed_rad_s': (ARM_SPEED_RAD_S, 'rad/s', '探测器臂转速'), 'stage_speed_m_s': (STAGE_SPEED_M_S, 'm/s', 'YZ台速度'),
            'cap_mount_sd_m': (CAP_MOUNT_SD_M, 'm', '毛细管横向安装偏差（1σ）'), 'align_s': (ALIGN_S, 's', 'TrueFocus对准时间'), 'asx_inject_m3': (ASX_INJECT_M3, 'm^3', '每次注入体积'),
            'fc_volume_m3': (FC_VOLUME_M3, 'm^3', '流通池容积'), 'asx_carry_over': (ASX_CARRY_OVER, '1', '未清洗残留'), 'asx_carry_over_clean': (ASX_CARRY_OVER_CLEAN, '1', '清洗后残留'),
            'dead_time_s': (DEAD_TIME_S, 's', '探测器死时间（非瘫痪型）'), 'overexposure_rate': (OVEREXPOSURE_RATE, '1/s', '过曝保护阈值'),
            'parasitic_line': (PARASITIC_LINE, '1/sr', '线准直寄生散射（q=0.05nm⁻¹）'), 'freeze_k': (FREEZE_K, 'K', '水样冻结温度（过冷极限）'), 'burst_k': (BURST_K, 'K', '水样沸腾破裂温度'),
            'agg_t_k': (AGG_T_K, 'K', '蛋白聚集起始温度'), 'agg_rate_s': (AGG_RATE_S, '1/s', '聚集速率（60°C）')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['xray_tube', 'generator_module', 'shutter_housing', 'optics_housing', 'collimation_block', 'sample_chamber', 'stage_base', 'stage_y', 'stage_z', 'tc_stage',
                       'capillary_holder', 'flow_cell', 'detector_housing', 'detector_arm', 'beamstop', 'arc_rail', 'autosampler', 'asx_tray', 'asx_gantry_x', 'asx_gantry_y',
                       'asx_needle_z', 'chiller_cabinet', 'electronics_rack'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='SAXSpace小/广角X射线散射：发生器与联锁、真空、准直与光束、样品台温度与样品状态、散射模板、探测器计数、标定、数据处理、自动进样',
        equations=['Phi = Phi_ref (I/50 mA) ((V/V_K - 1)/(40 kV/V_K - 1))^1.63 (x 0.02 point collimation)',
                   'counts_i = t eps Phi p cos^3(phi_i)/SDD^2 int W(dy) [T_total sum_j d_j dSigma_j/dOmega(q_eff)] P(2theta) ddy + primary x T_stop ; '
                   'cos 2theta_eff = cos 2theta_i cos(atan(dy/r_i)), W = beam length (x) strip length (trapezoid, int W = 8 mm)',
                   'q = 4 pi sin(2theta/2)/lambda (Ka1 : Ka2 = 2 : 1) ; eps = 1 - exp(-mu_Si 450 um)',
                   'dSigma/dOmega(solution) = c I(0)/c shape(q) S_PY(q; sqrt(5/3) Rg, c vbar) + (1 - phi) dSigma/dOmega(water) ; water: (n_e r_e)^2 k_B T kappa_T (Kell 1975)',
                   'spheres: phi drho^2 <V^2 P(qR)>/<V> (Schulz) ; peaks: share of Q = 2 pi^2 phi (1-phi) drho^2 ; powders: Scherrer-broadened Bragg peaks',
                   'capillary path: d(y) = 2 sqrt(R^2 - y^2) averaged over the beam width; T = <exp(-mu d - mu_w w)>',
                   'reduction: I = CF (N/N_prim - N_bg/N_prim,bg)/d ; CF from water or SRM 3600 ; desmearing: min |(K d - s)/sigma|^2 + lambda |D2 d/s_ref|^2, chi^2/N = 1',
                   'Guinier: ln I = ln I0 - q^2 Rg^2/3 (q Rg <= 1.3) ; M = I0/c N_A / drho_M^2',
                   'vacuum: V dp/dt = -S (p - p_ult) + Q ; stage T: ramp-limited first order ; dead time: m = n/(1 + n tau)'],
        parameters=params, source_ids=[_SIM, _LIBS, _SAS, _AZOM, _SICK, _DECT, _ALLEC, _PU, _TH, _CUBO, _SRM, _RM, _KELL, _ORTH, _AGBE, _XRAYDB, _PHYS, _WB],
        limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_saxspace_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'saxspace_saxs': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'sample.provide': (SAMPLE, SAMPLE_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'saxspace_saxs': mechanism})
