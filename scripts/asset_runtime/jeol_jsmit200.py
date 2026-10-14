"""CHEM-151 JEOL JSM-IT200 LA (InTouchScope scanning electron microscope with W hairpin gun, TMP + RP vacuum, low vacuum 10-100 Pa,
SE (Everhart-Thornley) and BSE (BED) detectors and the integrated JED-2300 DrySD EDS): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time; instrument cycle 1 s):
  * vacuum: one lumped volume (chamber cavity of the geometry + column), rotary pump (roughing and foreline, >= 100 L/min, JSM-IT300
    manual of the same family), turbo-molecular pump with spin-up, crossover and full speed, outgassing that decays after venting
    (air with humidity or dry N2), wet specimens, a door that is not closed (leak), vent valve; low vacuum by the RP through an orifice
    against a PI-controlled needle valve (10-100 Pa) with the gun kept in high vacuum behind the pressure-limiting aperture; Pirani,
    Penning and LV gauges; EVAC / VENT / LV sequences with the panel lamps (blinking while running); 10-min evacuation limit;
  * gun: W hairpin filament heated to saturation by the automatic filament adjustment (about 10 s), emission, filament life (Weibull,
    engineering) and burnout; accelerating voltage 0.5-30 kV, probe current 1 pA - 0.3 uA (catalogue);
  * optics: probe diameter from brightness, spherical and chromatic aberration and diffraction at the optimum aperture angle with
    effective constants fitted to the catalogue resolutions (outputs/.../optics_fit.json), focus and defocus blur, astigmatism,
    direct magnification x5-300,000 on the 128 mm print, LV gas scattering (unscattered fraction, skirt);
  * signals: BSE (Hunger-Kuechler eta(Z, E0), Castaing mass-fraction rule, Arnal tilt law) collected by the annular BED (solid angle
    from the working distance, energy-weighted, amplifier noise), SE (Lin-Joy universal curve of the total yield with material-class
    parameters, secant tilt law, SE3) collected by the E-T detector (high vacuum only), shot noise, negative charging of uncoated
    insulators in high vacuum with one surface potential per phase driven by I (1 - eta - delta) at the actual incidence towards the
    second crossover (no charging in LV or when coated), microstructure from a seeded Voronoi phase map; images report grey-level
    regions, not phase names;
  * EDS: thick-target characteristic generation (Bethe ionisation cross-section, Joy-Luo stopping power, Myklebust backscatter loss,
    Elam fluorescence yields and line intensities), Philibert-Duncumb-Heinrich absorption with Chantler attenuation, detector efficiency
    (polyimide window, Al coating, Si grid, Si dead layer, 0.45 mm Si), solid angle and take-off angle from the modelled detector
    position, Kramers continuum, Fano-limited resolution (127 eV at Mn Ka, catalogue <= 130 eV), Si escape peaks, paralysable dead
    time, Poisson counts, Duane-Hunt limit estimated from the spectrum; standardless quantification with a Green-Cosslett-type generation
    term and the full Philibert absorption at the nominal take-off angle (the model difference is the method bias) and automatic peak
    identification;
  * stage X / Y (motor) and Z / T / R (manual knobs), tilt about the holder top (the beam meets a tilted specimen h tan t from its
    centre), swept-path collision limits against the pole piece, draw-out door (opens only at atmospheric pressure), specimen loading on
    the 32 mm holder; charge, gas, X-ray and filament ledgers.
"""
from __future__ import annotations

import bisect
import copy
import functools
import json
import math
import random
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = '1.0.0'
SLUG = 'jeol_jsmit200'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-151'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t3_analytical' / SLUG
LIBRARY_PATH = ASSET / 'sem_eds_library.json'
LIBRARY_REL = str(LIBRARY_PATH.relative_to(ROOT))

E_CHARGE = 1.602176634e-19
N_AVOGADRO = 6.02214076e23
K_BOLTZMANN = 1.380649e-23
GAS_CONSTANT = 8.314462618
P_ATM = 101325.0
CYCLE_S = 1.0
CYCLE_NS = 1_000_000_000
# ---------------------------------------------------------------- documented (JEOL catalogue JSM-IT200 p.17-18 unless noted)
KV_RANGE_V = (500.0, 30000.0)
KV_FINE_MAX_V, KV_FINE_STEP_V, KV_COARSE_STEP_V = 3000.0, 100.0, 1000.0     # JSM-IT300 manual (same family): 100 V steps to 3 kV, 1 kV above
PROBE_CURRENT_RANGE_A = (1e-12, 3e-7)                                        # 1 pA to 0.3 uA (1 uA only with the MP-30060 option, not fitted)
MAG_RANGE = (5.0, 300000.0)                                                  # direct magnification on a 128 mm x 96 mm print
PRINT_WIDTH_M = 0.128
LV_RANGE_PA = (10.0, 100.0)
RESOLUTION_HV_M = {30000.0: 3.0e-9, 3000.0: 8.0e-9, 1000.0: 15.0e-9}
RESOLUTION_LV_M = 4.0e-9                                                     # LV, BED, 30 kV
WD_RANGE_M = (0.005, 0.048)                                                  # stage Z 5-48 mm
STAGE_X_RANGE_M = (-0.040, 0.040)                                            # X 80 mm
STAGE_Y_RANGE_M = (-0.020, 0.020)                                            # Y 40 mm
TILT_RANGE_RAD = (math.radians(-10.0), math.radians(90.0))
ROT_RANGE_RAD = (0.0, 2 * math.pi)
Z_LIFT_RANGE_M = (0.0, WD_RANGE_M[1] - WD_RANGE_M[0])
SPECIMEN_MAX_DIAMETER_M, SPECIMEN_MAX_HEIGHT_M = 0.150, 0.048
PIXEL_FORMATS = {'320x240': (320, 240), '640x480': (640, 480), '1280x960': (1280, 960), '2560x1920': (2560, 1920), '5120x3840': (5120, 3840)}
MAINS_NOMINAL_V, MAINS_TOL = 100.0, 0.10                                     # single-phase AC 100 V +-10 %, 1.5 kVA
ROOM_RANGE_K = (288.15, 300.15)                                              # 15-27 C
HUMIDITY_MAX = 0.60
EDS_AREA_M2 = 25e-6                                                          # DrySD 25 mm2
EDS_FWHM_MN_SPEC_EV = 130.0
EDS_Z_RANGE = (4, 92)                                                        # Be to U
EVAC_LIMIT_S = 600.0                                                         # JSM-IT300 manual: error if evacuation not complete within 10 min
RP_SPEED_M3_S = 100e-3 / 60.0                                                # JSM-IT300 manual: RP pumping speed >= 100 L/min
HOLDER_RADIUS_M, HOLDER_HEIGHT_M = 0.016, 0.012                              # Iowa State CIF: 32 mm multipurpose mount (height engineering)
# ---------------------------------------------------------------- engineering values (see LIMITATIONS and mechanisms)
V_SYSTEM_M3 = 0.0106                   # chamber cavity of the geometry (0.18 x 0.25 x 0.218 m less stage, ~9.1 L) + column (~1.5 L)
RP_EFF_M3_S = 0.50 * RP_SPEED_M3_S     # through the roughing hose, valve and chamber port (engineering)
RP_ULTIMATE_PA = 2.0                   # rotary-pump ultimate pressure at the chamber (back-streaming load S_rp P_u)
TMP_SPEED_M3_S = 0.050                 # 67 L/s class TMP behind its valve
TMP_SPINUP_S = 60.0
CROSSOVER_PA = 20.0
HV_READY_PA = 5.0e-3                   # gun operation allowed below this (W gun)
GUN_TRIP_PA = 3.0e-2                   # HT trips above this
VENT_READY_PA = 9.5e4
VENT_CONDUCTANCE_M3_S = 4.0e-4
DOOR_GAP_CONDUCTANCE_M3_S = 1.0e-3     # door not closed
LEAK_CLOSED_PA_M3_S = 1.0e-7
OUTGAS_AIR_PA_M3_S = 1.0e-4            # after venting with room air at 0 % RH; x (1 + 2 RH)
OUTGAS_N2_PA_M3_S = 3.0e-5             # after venting with dry nitrogen
OUTGAS_TC_S = 60.0                     # Q(t) = Q0 tc / (tc + t)
WET_OUTGAS_PA_M3_S = 2.0e-3            # wet (hydrated) specimen, decaying with its accumulated exposure to the pumped chamber (WET_TAU_S)
WET_TAU_S = 1800.0
LV_ORIFICE_M3_S = 2.0e-4               # RP through the LV orifice
LV_VALVE_MAX_M3_S = 2.0e-5             # needle-valve conductance fully open (to room pressure)
LV_KP, LV_TI_S = 0.02, 8.0
PLA_CONDUCTANCE_M3_S = 2.0e-6          # pressure-limiting aperture between chamber and column in LV
GUN_BASE_PA = 2.0e-4
SOFTWARE_BOOT_S = 60.0
SATURATION_S = 10.0                    # Iowa State CIF guide: about 10 s after "Observation" the image forms
EMISSION_SAT_A = 1.0e-4
FILAMENT_LIFE_H = 300.0                # W hairpin mean life at saturation (Iowa State guide: filaments changed "every few hundred hours"; engineering)
FILAMENT_LIFE_S = FILAMENT_LIFE_H * 3600.0
FILAMENT_WEIBULL_K = 3.0
STAGE_XY_SPEED_M_S = 0.003             # motor
STAGE_Z_SPEED_M_S = 0.002              # hand on the Z knob
STAGE_TILT_SPEED = math.radians(10.0)  # hand on the T knob, rad/s
STAGE_ROT_SPEED = math.radians(30.0)
DOOR_SPEED_M_S = 0.10
DOOR_TRAVEL_M = 0.200
POLE_CLEARANCE_M = 0.004               # specimen top must stay this far below the pole piece (BED hangs 3.5 mm below it)
BED_R_IN_M, BED_R_OUT_M, BED_DROP_M = 0.004, 0.013, 0.0035
EDS_TIP_R_M = 0.040 * math.cos(math.radians(35.0))       # detector position of the geometry: 40 mm from the WD-10 analysis point at 35 deg
EDS_TIP_DZ_M = 0.040 * math.sin(math.radians(35.0)) - 0.010   # tip height above the pole-piece face
ANALYTICAL_WD_M = 0.010
# optics (effective constants fitted to the catalogue resolutions by derive_optics_parameters.py)
BETA_R = 1.0e9 / 30000.0               # W reduced brightness A m^-2 sr^-1 V^-1
ENERGY_SPREAD_EV = 1.5
LENS_GAP_M = 0.002
C_S_COEF = 0.9315669301523783          # C_s = c_s (WD + gap)
C_C_COEF = 0.1320663205093771
K_RES = 0.6                            # quoted resolution = 0.6 d_p
BED_RES_FACTOR = 1.430569028448803     # LV 4.0 nm (IT300 manual: 30 kV, WD 5 mm, 10 Pa, BSE) / modelled 30 kV resolution at WD 5 mm
VIBRATION_M = 1.0e-9
# signals
SURFACE_CAPACITANCE_F = 1.0e-13        # capacitance of a charging insulating region to ground (engineering)
MIRROR_EV = 50.0                       # landing energy below which the charged surface reflects the beam (mirror)
DH_END_MARGIN_EV = 300.0               # the Duane-Hunt fit needs the continuum end inside the recorded 0-20.48 keV range
SE_COLLECTION = 0.45
SE3_FRACTION = 0.10
BED_DEAD_LAYER_EV = 500.0
BED_AMP_NOISE_PAIRS = 2000.0           # rms e-h pairs for a 1 us dwell (scales with dwell^-0.5)
GAS_SIGMA30_M2 = 1.0e-21               # total scattering cross-section of air at 30 kV (order of magnitude after Danilatos)
# EDS detector (engineering)
EDS_PROCESS_TIMES = {'T1': dict(tau_s=0.4e-6, noise_ev=95.0), 'T2': dict(tau_s=0.8e-6, noise_ev=75.0), 'T3': dict(tau_s=1.6e-6, noise_ev=60.0),
                     'T4': dict(tau_s=3.2e-6, noise_ev=49.3)}
FANO, PAIR_EV = 0.115, 3.64
EDS_CHANNEL_EV, EDS_CHANNELS = 10.0, 2048
WINDOW = dict(kapton_nm=300.0, al_nm=50.0, grid_open=0.77, dead_si_nm=30.0, si_um=450.0)
KRAMERS_K = 2.0e-9                     # continuum photons per electron per eV into 4 pi (x Zbar (E0-E)/E)
MAX_RECORDS, MAX_EVENTS = 200, 1000
EDS_MAX_DEAD_TIME = 0.90             # the analysis refuses to start above 90 % dead time (reduce the probe current)
THUMB_W, THUMB_H = 32, 24


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
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- template library (NIST SRD 126, xraydb Elam / Chantler, Hephaestus)
_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        raw = json.loads(LIBRARY_PATH.read_text())
        els = raw['elements']
        for e in els.values():
            if e.get('attenuation'):
                e['_lnE'] = np.log(np.asarray(e['attenuation']['energy_eV'], float))
                e['_lnMu'] = np.log(np.asarray(e['attenuation']['mu_rho_cm2_g'], float))
        _LIB = dict(meta={k: v for k, v in raw.items() if k not in ('elements', 'substances')}, elements=els,
                    by_z={e['Z']: s for s, e in els.items()}, substances={s['substance_id']: s for s in raw['substances']})
    return _LIB


def library():
    """Deep copy of the substance templates (by id) and element symbols of the SEM/EDS library."""
    lib = _lib()
    return dict(meta=copy.deepcopy(lib['meta']), substances=copy.deepcopy(lib['substances']), elements=sorted(lib['elements']))


def element(sym):
    return _lib()['elements'][sym]


def substance(sid):
    return _lib()['substances'][sid]


@functools.lru_cache(maxsize=200000)
def mu_rho(sym, e_ev):
    """Mass attenuation coefficient (cm2/g) of an element, log-log interpolation of the Chantler table (xraydb)."""
    e = element(sym)
    if '_lnE' not in e:
        return 0.0
    x = math.log(max(e_ev, 50.0))
    lnE, lnMu = e['_lnE'], e['_lnMu']
    i = int(np.searchsorted(lnE, x, side='right'))
    i = min(max(i, 1), len(lnE) - 1)
    x0, x1 = lnE[i - 1], lnE[i]
    if x1 <= x0:
        return float(math.exp(lnMu[i]))
    return float(math.exp(lnMu[i - 1] + (lnMu[i] - lnMu[i - 1]) * (x - x0) / (x1 - x0)))


def mu_rho_mix(comp, e_ev):
    return sum(w * mu_rho(s, e_ev) for s, w in comp.items())


def mean_z(comp):
    return sum(w * element(s)['Z'] for s, w in comp.items())


def mean_a(comp):
    return sum(w * element(s)['molar_mass_g_mol'] for s, w in comp.items())


def zoa(comp):
    return sum(w * element(s)['z_over_a'] for s, w in comp.items())


def mean_j_kev(comp):
    """Mass-and-Z/A weighted mean ionisation energy (keV) from the NIST SRD 126 I values."""
    num = sum(w * element(s)['z_over_a'] * math.log(element(s)['mean_excitation_eV']) for s, w in comp.items())
    return math.exp(num / zoa(comp)) / 1000.0


# ---------------------------------------------------------------- electron optics
def wavelength_m(v):
    return 1.22639e-9 / math.sqrt(v * (1.0 + 9.78476e-7 * v))


def probe(v, i_a, wd_m):
    """Probe diameter (m) and aperture semi-angle (rad) at the optimum angle (golden-section search on log alpha)."""
    cs, cc, beta = C_S_COEF * (wd_m + LENS_GAP_M), C_C_COEF * (wd_m + LENS_GAP_M), BETA_R * v
    lam = wavelength_m(v)

    def tot(la):
        a = 10.0 ** la
        return math.sqrt(4 * i_a / (math.pi ** 2 * beta * a * a) + (0.5 * cs * a ** 3) ** 2 + (cc * a * ENERGY_SPREAD_EV / v) ** 2 + (1.22 * lam / a) ** 2)
    lo, hi = -4.0, -1.3
    g = (math.sqrt(5) - 1) / 2
    x1, x2 = hi - g * (hi - lo), lo + g * (hi - lo)
    f1, f2 = tot(x1), tot(x2)
    for _ in range(80):
        if f1 < f2:
            hi, x2, f2 = x2, x1, f1
            x1 = hi - g * (hi - lo); f1 = tot(x1)
        else:
            lo, x1, f1 = x1, x2, f2
            x2 = lo + g * (hi - lo); f2 = tot(x2)
    la = (lo + hi) / 2
    return tot(la), 10.0 ** la


# ---------------------------------------------------------------- electron-specimen interaction
def eta_hk(z, e_kev):
    """Hunger & Kuechler (1979) backscatter coefficient of an element at normal incidence."""
    lz = math.log(z)
    m = 0.1382 - 0.9211 / math.sqrt(z)
    c = 0.1904 - 0.2236 * lz + 0.1292 * lz ** 2 - 0.01491 * lz ** 3
    return max(0.0, min(0.95, c * max(e_kev, 0.1) ** m))


def eta(comp, e_kev, theta=0.0):
    """Castaing mass-fraction rule on Hunger-Kuechler with the Arnal tilt law eta(theta) = (1 + cos theta)^-(9/sqrt Z) (ratio to theta = 0)."""
    e0 = sum(w * eta_hk(element(s)['Z'], e_kev) for s, w in comp.items())
    zb = max(mean_z(comp), 1.0)
    p = 9.0 / math.sqrt(zb)
    th = min(abs(theta), math.radians(85.0))
    return min(0.95, e0 * (2.0 ** p) / (1.0 + math.cos(th)) ** p)


def delta_lin_joy(dm, em_kev, e_kev):
    """Lin & Joy (2005) universal secondary-electron yield curve."""
    r = max(e_kev, 1e-3) / em_kev
    return dm * 1.28 * r ** -0.67 * (1.0 - math.exp(-1.614 * r ** 1.67))


def se_yield(sub, comp, e_kev, theta=0.0):
    """Total secondary-electron yield (SE1 + SE2): the Lin-Joy curve with the material's measured-type parameters already describes the
    total yield of the bulk material, so no separate SE2 term is added; secant law for the incidence angle."""
    se = sub['se_parameters']
    return delta_lin_joy(se['delta_m'], se['E_m_keV'], e_kev) / max(math.cos(min(abs(theta), math.radians(80.0))), 0.17)


def sigma_total(sub, e_kev, theta=0.0):
    comp = sub['composition_mass_fraction']
    return eta(comp, e_kev, theta) + se_yield(sub, comp, e_kev, theta)


_E2_CACHE = {}


def second_crossover_kev(sub, theta=0.0):
    """Landing energy (keV) above which the total yield eta + delta falls below 1 at incidence `theta` (negative charging of an
    insulator); None if the total yield never exceeds 1; 30 keV if it is still above 1 there.  Cached per substance and 0.01 rad
    (module cache, the library templates stay untouched)."""
    key = (sub['substance_id'], round(abs(theta), 2))
    if key in _E2_CACHE:
        return _E2_CACHE[key]
    th = key[1]
    em = sub['se_parameters']['E_m_keV']
    if sigma_total(sub, em, th) <= 1.0:
        e2 = None
    elif sigma_total(sub, 30.0, th) > 1.0:
        e2 = 30.0
    else:
        lo, hi = em, 30.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if sigma_total(sub, mid, th) > 1.0 else (lo, mid)
        e2 = 0.5 * (lo + hi)
    _E2_CACHE[key] = e2
    return e2


def ko_range_m(comp, rho, e_kev):
    """Kanaya-Okayama electron range (m)."""
    zb, ab = mean_z(comp), mean_a(comp)
    return 0.0276 * ab * e_kev ** 1.67 / (zb ** 0.889 * rho) * 1e-6


# ---------------------------------------------------------------- X-ray generation (true model)
SHELL_PARAMS = {'K': (2, 0.35), 'L1': (2, 0.25), 'L2': (2, 0.25), 'L3': (4, 0.25), 'M1': (2, 0.25), 'M2': (2, 0.25), 'M3': (4, 0.25), 'M4': (4, 0.25), 'M5': (6, 0.25)}
_GL_X, _GL_W = np.polynomial.legendre.leggauss(32)


def _stopping(e_kev, zoa_m, j_kev):
    """Joy & Luo (1989) modified Bethe stopping power, keV cm2/g (positive)."""
    return 78500.0 * zoa_m / e_kev * math.log(1.166 * (e_kev + 0.85 * j_kev) / j_kev)


def _myklebust_r(zb, u0, e0_kev):
    return max(0.3, min(1.0, 1.0 - 0.0081517 * zb + 3.613e-5 * zb * zb + 0.009583 * zb * math.exp(-u0) + 0.001141 * e0_kev))


def ionisations_per_electron(comp, sym, shell, e0_kev):
    """Thick-target ionisations of `shell` of element `sym` per incident electron (Bethe cross-section over the Joy-Luo slowing down)."""
    lev = element(sym)['levels'].get(shell)
    w = comp.get(sym, 0.0)
    if lev is None or w <= 0.0:
        return 0.0
    ec = lev['edge_eV'] / 1000.0
    if ec <= 0.0 or e0_kev <= ec * 1.001:
        return 0.0
    n_s, b_s = SHELL_PARAMS.get(shell, (2, 0.25))
    zoa_m, j = zoa(comp), mean_j_kev(comp)
    u0 = e0_kev / ec
    # substitute E = Ec U, U in [1, U0]
    us = 1.0 + (u0 - 1.0) * (_GL_X + 1.0) / 2.0
    tot = 0.0
    for u, wt in zip(us, _GL_W):
        q = 6.51e-20 * n_s * b_s * math.log(u) / (u * ec * ec)
        tot += wt * q / _stopping(ec * u, zoa_m, j) * ec
    tot *= (u0 - 1.0) / 2.0
    a = element(sym)['molar_mass_g_mol']
    return N_AVOGADRO * w / a * tot * _myklebust_r(mean_z(comp), u0, e0_kev)


def philibert_f(chi, e0_kev, ec_kev, a_bar, z_bar, full=True):
    if chi <= 0.0:
        return 1.0
    ec = min(ec_kev, e0_kev * 0.999)
    sigma = 4.5e5 / (e0_kev ** 1.65 - ec ** 1.65)
    h = 1.2 * a_bar / (z_bar * z_bar) if full else 0.0
    return 1.0 / ((1.0 + chi / sigma) * (1.0 + h / (1.0 + h) * chi / sigma))


# ---------------------------------------------------------------- EDS detector
KAPTON = {'C': 0.691127, 'H': 0.026363, 'N': 0.073270, 'O': 0.209235}   # C22H10N2O5 mass fractions (NIST SRD 126 Table 2 "Kapton Polyimide Film")


@functools.lru_cache(maxsize=10000)
def detector_efficiency(e_ev):
    """Transmission of the polyimide window + Al coating (x open fraction of the Si grid, which is opaque below ~8 keV), Si dead layer,
    times absorption in 0.45 mm Si."""
    if e_ev < 60.0:
        return 0.0
    t_win = math.exp(-mu_rho_mix(KAPTON, e_ev) * 1.42 * WINDOW['kapton_nm'] * 1e-7 - mu_rho('Al', e_ev) * 2.70 * WINDOW['al_nm'] * 1e-7)
    grid = WINDOW['grid_open'] + (1.0 - WINDOW['grid_open']) * math.exp(-mu_rho('Si', e_ev) * 2.33 * 0.038)    # 380 um Si grid bars
    dead = math.exp(-mu_rho('Si', e_ev) * 2.33 * WINDOW['dead_si_nm'] * 1e-7)
    absorb = 1.0 - math.exp(-mu_rho('Si', e_ev) * 2.33 * WINDOW['si_um'] * 1e-4)
    return t_win * grid * dead * absorb


def fwhm_ev(e_ev, noise_ev):
    return math.sqrt(noise_ev ** 2 + 5.5452 * FANO * PAIR_EV * max(e_ev, 0.0))


def duane_hunt_limit(counts, noise_ev=60.0, e0_ev=None):
    """Duane-Hunt limit (eV) estimated from a spectrum: near its end the continuum per channel follows N(E) = (a + b E) eff(E) / E
    (Kramers), i.e. it falls linearly to zero at E_DH = -a/b.  The fit window runs from 0.6 of the last occupied channel (or from two FWHM
    above the last significant characteristic peak there) to the end; Poisson maximum likelihood by iteratively reweighted least squares
    with 3-sigma clipping of residual peaks; never above the set beam energy.  None when the spectrum is truncated (E0 within 300 eV of
    the 20.48 keV end of the recorded range) or the fit window holds fewer than 1000 counts.  An uncharged specimen gives about E0, a charged insulator its
    landing energy (statistical error about 30-60 eV at 1 nA / 30 s, a few hundred eV for a few 1e4 counts)."""
    y = np.asarray(counts, float)
    nz = np.nonzero(y > 0)[0]
    if len(nz) < 20 or y.sum() < 2000 or (e0_ev is not None and e0_ev > EDS_CHANNELS * EDS_CHANNEL_EV - DH_END_MARGIN_EV):
        return None
    c_last = int(nz[-1])
    f, var = _tophat(y, noise_ev)
    sig = f / np.sqrt(var)
    lo = int(0.6 * c_last)
    pk = [c for c in range(max(lo, 1), c_last) if sig[c] >= 5.0 and f[c] >= f[c - 1] and f[c] > f[c + 1]]
    if pk:
        start = max(pk) + int(2.0 * fwhm_ev((max(pk) + 0.5) * EDS_CHANNEL_EV, noise_ev) / EDS_CHANNEL_EV)
        if c_last - start >= 0.08 * c_last:
            lo = start
    sel = np.arange(lo, c_last + 1)
    e = (sel + 0.5) * EDS_CHANNEL_EV
    g = np.array([max(detector_efficiency(x), 1e-3) for x in e]) / e
    yy = y[sel]
    if yy.sum() < 1000.0:
        return None
    A = np.vstack([g, g * e]).T
    mask = np.ones(len(sel), bool)
    mu = np.maximum(yy, 1.0)
    coef = None
    for _ in range(40):
        w = 1.0 / np.sqrt(np.maximum(mu, 0.5))
        c2, *_ = np.linalg.lstsq(A[mask] * w[mask, None], yy[mask] * w[mask], rcond=None)
        mu = np.maximum(A @ c2, 0.5)
        new = (yy - mu) / np.sqrt(mu) < 3.0
        done = coef is not None and np.allclose(c2, coef, rtol=1e-10, atol=0.0) and bool((new == mask).all())
        coef, mask = c2, new
        if done:
            break
    if coef[1] >= 0.0:
        return None
    e_dh = -coef[0] / coef[1]
    return float(round(min(e_dh, e0_ev) if e0_ev is not None else e_dh, 0))


def eds_geometry(wd_m, tilt_rad):
    """Take-off angle (rad) and solid angle (sr) of the SDD for a specimen point at working distance `wd_m`; the tilt towards the door is
    perpendicular to the detector azimuth, so sin(psi_eff) = sin(psi) cos(tilt)."""
    dz = EDS_TIP_DZ_M + wd_m
    d = math.hypot(EDS_TIP_R_M, dz)
    psi = math.atan2(dz, EDS_TIP_R_M)
    psi_eff = math.asin(max(1e-3, math.sin(psi) * math.cos(tilt_rad)))
    return psi_eff, EDS_AREA_M2 / (d * d)


_SI_KA_EV = 1739.98
_SI_K_EDGE_EV = 1838.9


def escape_fraction(e_ev):
    """Reed & Ware (1972) Si escape probability for a photon absorbed in the SDD."""
    if e_ev <= _SI_K_EDGE_EV:
        return 0.0
    si = element('Si')['levels']['K']
    r = si['jump_ratio']
    mu_i, mu_k = mu_rho('Si', e_ev), mu_rho('Si', _SI_KA_EV)
    return max(0.0, 0.5 * si['fluorescence_yield'] * (1.0 - 1.0 / r) * (1.0 - mu_k / mu_i * math.log(1.0 + mu_i / mu_k)))


def _gauss_channels(center_ev, fwhm, area, out):
    """Add a Gaussian of `area` counts integrated over the 10 eV channels."""
    s = fwhm / 2.3548
    lo = max(0, int((center_ev - 5 * s) / EDS_CHANNEL_EV))
    hi = min(EDS_CHANNELS, int((center_ev + 5 * s) / EDS_CHANNEL_EV) + 2)
    if hi <= lo:
        return
    edges = np.arange(lo, hi + 1) * EDS_CHANNEL_EV
    cdf = 0.5 * (1.0 + np.array([math.erf((x - center_ev) / (s * math.sqrt(2))) for x in edges]))
    out[lo:hi] += area * np.diff(cdf)


def _lines_of(sym, e0_ev, min_rel=0.0):
    """Lines of K, L and M vacancies above 100 eV whose vacancy level is excited (N-shell emission is not modelled)."""
    e = element(sym)
    return [ln for ln in e['lines'] if ln['energy_eV'] >= 100.0 and ln['relative_intensity'] > min_rel and ln['initial'] in SHELL_PARAMS
            and e['levels'][ln['initial']]['edge_eV'] < e0_ev]


def spectrum_rates(comp, e0_ev, psi_rad, omega_sr, noise_ev, coating=None):
    """Expected detected photons per incident electron per channel (numpy array, 2048 x 10 eV) and the generated / emitted photon totals.
    coating = (comp, mass thickness g/cm2) of a thin conductive coating on top (thin-film generation at E0, absorbs the substrate lines)."""
    e0 = e0_ev / 1000.0
    ab, zb = mean_a(comp), mean_z(comp)
    csc = 1.0 / math.sin(psi_rad)
    rates = np.zeros(EDS_CHANNELS)
    gen = emitted = 0.0

    def coat_t(e_ev):
        if not coating:
            return 1.0
        return math.exp(-mu_rho_mix(coating[0], e_ev) * coating[1] * csc)
    for sym in comp:
        if element(sym)['Z'] < EDS_Z_RANGE[0]:
            continue
        shells = {}
        for ln in _lines_of(sym, e0_ev):
            shells.setdefault(ln['initial'], []).append(ln)
        for shell, lines in shells.items():
            lev = element(sym)['levels'][shell]
            n_ion = ionisations_per_electron(comp, sym, shell, e0)
            if n_ion <= 0.0:
                continue
            for ln in lines:
                n = n_ion * lev['fluorescence_yield'] * ln['relative_intensity']
                gen += n
                f = philibert_f(mu_rho_mix(comp, ln['energy_eV']) * csc, e0, lev['edge_eV'] / 1000.0, ab, zb)
                em = n * f * coat_t(ln['energy_eV'])
                emitted += em
                det = em * omega_sr / (4 * math.pi) * detector_efficiency(ln['energy_eV'])
                fw = fwhm_ev(ln['energy_eV'], noise_ev)
                pe = escape_fraction(ln['energy_eV'])
                _gauss_channels(ln['energy_eV'], fw, det * (1.0 - pe), rates)
                if pe > 0.0:
                    _gauss_channels(ln['energy_eV'] - _SI_KA_EV, fwhm_ev(ln['energy_eV'] - _SI_KA_EV, noise_ev), det * pe, rates)
    if coating:
        ccomp, mt = coating
        for sym, w in ccomp.items():
            if element(sym)['Z'] < EDS_Z_RANGE[0]:
                continue
            for ln in _lines_of(sym, e0_ev):
                lev = element(sym)['levels'][ln['initial']]
                ec = lev['edge_eV'] / 1000.0
                n_s, b_s = SHELL_PARAMS.get(ln['initial'], (2, 0.25))
                u = e0 / ec
                q = 6.51e-20 * n_s * b_s * math.log(u) / (u * ec * ec)
                n = N_AVOGADRO * w * mt / element(sym)['molar_mass_g_mol'] * q * lev['fluorescence_yield'] * ln['relative_intensity'] * (1.0 + eta(comp, e0))
                gen += n
                emitted += n
                det = n * omega_sr / (4 * math.pi) * detector_efficiency(ln['energy_eV'])
                _gauss_channels(ln['energy_eV'], fwhm_ev(ln['energy_eV'], noise_ev), det, rates)
    # Kramers continuum with absorption (edges) and window
    centers = (np.arange(EDS_CHANNELS) + 0.5) * EDS_CHANNEL_EV
    for c, e in enumerate(centers):
        if e >= e0_ev or e < 60.0:
            continue
        n = KRAMERS_K * zb * (e0_ev - e) / e * EDS_CHANNEL_EV
        f = philibert_f(mu_rho_mix(comp, e) * csc, e0, e / 1000.0, ab, zb)
        rates[c] += n * f * coat_t(e) * omega_sr / (4 * math.pi) * detector_efficiency(e)
    return rates, gen, emitted


# ---------------------------------------------------------------- standardless quantification (simpler model = method bias)
def _family(shell):
    return shell[0]


def _analytical_family(sym, e0_ev):
    lev = element(sym)['levels']
    for fam, shells in (('K', ['K']), ('L', ['L3', 'L2', 'L1']), ('M', ['M5', 'M4'])):
        edge = min((lev[s]['edge_eV'] for s in shells if s in lev), default=None)
        lines = [ln for ln in _lines_of(sym, e0_ev) if _family(ln['initial']) == fam]
        if edge and e0_ev / edge >= 1.5 and lines and max(ln['energy_eV'] for ln in lines) < EDS_CHANNELS * EDS_CHANNEL_EV - 200 and \
                sum(ln['relative_intensity'] for ln in lines if ln['energy_eV'] > 150.0) > 0.3:
            return fam
    return None


def _quant_model(comp, sym, fam, e0_ev, psi_rad, noise_ev):
    """Green-Cosslett thick-target intensity per unit concentration for the line family (quantification model), and its channel shape."""
    e0 = e0_ev / 1000.0
    ab, zb = mean_a(comp), mean_z(comp)
    zoa_m, j = zoa(comp), mean_j_kev(comp)
    csc = 1.0 / math.sin(psi_rad)
    shape = np.zeros(EDS_CHANNELS)
    total = 0.0
    for ln in _lines_of(sym, e0_ev):
        if _family(ln['initial']) != fam:
            continue
        lev = element(sym)['levels'][ln['initial']]
        ec = lev['edge_eV'] / 1000.0
        u0 = e0 / ec
        n_s, b_s = SHELL_PARAMS.get(ln['initial'], (2, 0.25))
        gc = n_s * b_s * (u0 - 1.0) ** 1.67 / (zoa_m * math.log(1.166 * 0.5 * (e0 + ec) / j))
        r_q = 1.0 - 0.4 * eta(comp, e0) * (1.0 - 1.0 / u0)
        f = philibert_f(mu_rho_mix(comp, ln['energy_eV']) * csc, e0, ec, ab, zb)
        v = gc / element(sym)['molar_mass_g_mol'] * lev['fluorescence_yield'] * ln['relative_intensity'] * r_q * f * detector_efficiency(ln['energy_eV'])
        total += v
        pe = escape_fraction(ln['energy_eV'])
        _gauss_channels(ln['energy_eV'], fwhm_ev(ln['energy_eV'], noise_ev), v * (1.0 - pe), shape)
        if pe > 0.0:
            _gauss_channels(ln['energy_eV'] - _SI_KA_EV, fwhm_ev(ln['energy_eV'] - _SI_KA_EV, noise_ev), v * pe, shape)
    return total, shape


def _background_basis(e0_ev, comp, psi_rad=math.radians(35.0)):
    """Continuum basis for the fit: Kramers shape and a linear term, both through the detector efficiency and (when a composition
    estimate is given) the Philibert absorption of the estimate, so the absorption edges of the matrix appear in the model."""
    centers = (np.arange(EDS_CHANNELS) + 0.5) * EDS_CHANNEL_EV
    b1 = np.zeros(EDS_CHANNELS)
    b2 = np.zeros(EDS_CHANNELS)
    if comp:
        ab_, zb_, csc = mean_a(comp), mean_z(comp), 1.0 / math.sin(psi_rad)
    for c, e in enumerate(centers):
        if 150.0 <= e < e0_ev:
            eff = detector_efficiency(e)
            ab = philibert_f(mu_rho_mix(comp, e) * csc, e0_ev / 1000.0, e / 1000.0, ab_, zb_) if comp else 1.0
            b1[c] = (e0_ev - e) / e * eff * ab
            b2[c] = eff * ab * (e0_ev - e) / e0_ev
    return b1, b2


def _nnls_fit(A, y, w):
    """Weighted least squares with non-negativity by active-set elimination (small problems)."""
    active = list(range(A.shape[1]))
    coef = np.zeros(A.shape[1])
    for _ in range(A.shape[1] + 1):
        Aw = A[:, active] * w[:, None]
        sol, *_ = np.linalg.lstsq(Aw, y * w, rcond=None)
        neg = [a for a, s in zip(active, sol) if s < 0]
        if not neg:
            coef[:] = 0.0
            coef[active] = sol
            cov = np.linalg.pinv(Aw.T @ Aw)
            var = np.zeros(A.shape[1])
            var[active] = np.diag(cov)
            return coef, var
        worst = min(zip(sol, active))[1]
        active.remove(worst)
    return coef, np.zeros(A.shape[1])


WINDOW_ARTIFACT_EV = (200.0, 265.0)


def _line_weight(sym, ln):
    """Approximate emitted weight of a line for identification: intensity within its vacancy level x fluorescence yield x shell occupancy
    x detector efficiency."""
    lev = element(sym)['levels'][ln['initial']]
    return ln['relative_intensity'] * lev['fluorescence_yield'] * SHELL_PARAMS.get(ln['initial'], (2, 0.25))[0] * detector_efficiency(ln['energy_eV'])


def _tophat(y, noise_ev, var_y=None):
    """Top-hat digital filter (central lobe one FWHM wide, side lobes half as wide on each side): removes slowly varying continuum and
    gives the net peak height per channel with its variance (Poisson counts by default, or the given per-channel variance)."""
    n = len(y)
    v = np.asarray(y if var_y is None else var_y, float)
    csum = np.concatenate([[0.0], np.cumsum(y)])
    vsum = np.concatenate([[0.0], np.cumsum(v)])
    f = np.zeros(n)
    var = np.ones(n)
    for c in range(n):
        m = max(1, int(round(0.5 * fwhm_ev((c + 0.5) * EDS_CHANNEL_EV, noise_ev) / EDS_CHANNEL_EV)))
        a0, a1 = c - m, c + m + 1
        l0, r1 = a0 - m, a1 + m
        if l0 < 0 or r1 > n:
            continue
        nc, ns = a1 - a0, 2 * m
        f[c] = (csum[a1] - csum[a0]) / nc - ((csum[a0] - csum[l0]) + (csum[r1] - csum[a1])) / ns
        var[c] = (vsum[a1] - vsum[a0]) / nc ** 2 + ((vsum[a0] - vsum[l0]) + (vsum[r1] - vsum[a1])) / ns ** 2 + 1e-12
    return f, var


def _efficiency_array():
    return np.array([max(detector_efficiency((c + 0.5) * EDS_CHANNEL_EV), 0.02) for c in range(EDS_CHANNELS)])


def _family_lines(sym, fam, e0_ev):
    lines = [ln for ln in _lines_of(sym, e0_ev) if _family(ln['initial']) == fam and 150.0 <= ln['energy_eV'] <= min(0.98 * e0_ev, EDS_CHANNELS * EDS_CHANNEL_EV - 100)]
    if not lines:
        return [], None, 0.0
    edge = min(element(sym)['levels'][ln['initial']]['edge_eV'] for ln in lines)
    return lines, max(lines, key=lambda ln: _line_weight(sym, ln)), e0_ev / edge


def _strong_lines(sym, e0_ev):
    out = []
    for fam in ('K', 'L', 'M'):
        lines, main, u0 = _family_lines(sym, fam, e0_ev)
        if main is not None:
            out += [ln['energy_eV'] for ln in lines if _line_weight(sym, ln) >= 0.02 * _line_weight(sym, main)]
    return out


def _peaks(f, sig, y, noise_ev, lo_c, hi_c, min_sig=8.0, min_rel=0.03, artifacts=True):
    out = []
    for c in range(lo_c + 1, hi_c):
        if sig[c] >= min_sig and f[c] >= f[c - 1] and f[c] > f[c + 1] and f[c] >= min_rel * max(y[c], 1.0):
            k = max(1, int(round(fwhm_ev((c + 0.5) * EDS_CHANNEL_EV, noise_ev) / EDS_CHANNEL_EV)))
            if c - k < 0 or c + k >= len(f):
                continue
            left, right = f[c - k] / f[c], f[c + k] / f[c]
            e = (c + 0.5) * EDS_CHANNEL_EV
            if artifacts and WINDOW_ARTIFACT_EV[0] < e < WINDOW_ARTIFACT_EV[1]:
                continue                                                  # transmission maximum of the polyimide window below its C K edge
            # Gaussian-like: negative side lobes (a continuum absorption edge gives one lobe near +1 and the other near -1); an unresolved
            # multiplet (Au Ma + Mb) lifts one lobe a little; a resolved neighbour line lifts it more: accept a doublet when that side
            # holds another local maximum
            if -3.0 <= min(left, right) <= -0.1 and max(left, right) <= 0.3 and (min(left, right) <= -0.3 or max(left, right) <= -0.1):
                out.append((e, f[c]))
            elif -3.0 <= min(left, right) <= -0.3:
                side = range(c + 1, min(len(f) - 1, c + 2 * k + 1)) if right > left else range(max(1, c - 2 * k), c)
                if any(f[j] >= f[j - 1] and f[j] > f[j + 1] and f[j] >= 0.2 * f[c] for j in side):
                    out.append((e, f[c]))
            elif sig[c] >= 20.0 and abs(left - right) <= 0.35 and max(left, right) <= 0.3:
                out.append((e, f[c]))                                     # a strong, symmetric but broad multiplet (e.g. an L family at T1)
    return sorted(out, key=lambda p: -p[1])


def _family_template(sym, fam, e0_ev, noise_ev):
    """Efficiency-corrected channel pattern of one line family of the pure element (relative heights incl. its own absorption,
    e.g. Cr Lb above the L3 edge)."""
    tmpl = np.zeros(EDS_CHANNELS)
    lines, main, u0 = _family_lines(sym, fam, e0_ev)
    el = element(sym)
    csc = 1.0 / math.sin(math.radians(35.0))
    for ln in lines:
        f_abs = philibert_f(mu_rho(sym, ln['energy_eV']) * csc, e0_ev / 1000.0, el['levels'][ln['initial']]['edge_eV'] / 1000.0, el['molar_mass_g_mol'], el['Z'])
        _gauss_channels(ln['energy_eV'], fwhm_ev(ln['energy_eV'], noise_ev), _line_weight(sym, ln) * f_abs / max(detector_efficiency(ln['energy_eV']), 0.02), tmpl)
    return tmpl


def _local_chi2(z, vz, sym, fam, e_pk, e0_ev, noise_ev, others=()):
    """Chi-square of a local fit (family pattern + the families of the elements already identified + linear background) to the
    efficiency-corrected spectrum around a peak."""
    fw = fwhm_ev(e_pk, noise_ev)
    c0 = max(0, int((e_pk - 2.5 * fw) / EDS_CHANNEL_EV))
    c1 = min(len(z), int((e_pk + 2.5 * fw) / EDS_CHANNEL_EV) + 1)
    if c1 - c0 < 5:
        return 0.0
    tmpl = _family_template(sym, fam, e0_ev, noise_ev)
    extra = []
    for s2, f2 in others:
        t2 = _family_template(s2, f2, e0_ev, noise_ev)[c0:c1]
        if t2.max() > 1e-9 * max(tmpl.max(), 1e-30):
            extra.append(t2)
    e = (np.arange(c0, c1) + 0.5) * EDS_CHANNEL_EV
    A = np.vstack([tmpl[c0:c1], np.ones(c1 - c0), (e - e_pk) / fw] + extra).T
    w = 1.0 / np.sqrt(vz[c0:c1])
    sol, *_ = np.linalg.lstsq(A * w[:, None], z[c0:c1] * w, rcond=None)
    if sol[0] <= 0:
        return float(np.sum(((z[c0:c1] - A[:, 1:] @ np.linalg.lstsq(A[:, 1:] * w[:, None], z[c0:c1] * w, rcond=None)[0]) * w) ** 2))
    return float(np.sum(((z[c0:c1] - A @ sol) * w) ** 2))


def _fit_elements(y, elements, e0_ev, noise_ev, psi_rad=math.radians(35.0)):
    comp = {e: 1.0 / len(elements) for e in elements}
    cols = []
    for e in elements:
        for fam in ('K', 'L', 'M'):
            tot, shape = _quant_model(comp, e, fam, e0_ev, psi_rad, noise_ev)
            if tot > 0.0 and shape.sum() > 0.0:
                cols.append(shape / shape.sum())
    b1, b2 = _background_basis(e0_ev, comp, psi_rad)
    A = np.vstack(cols + [b1, b2]).T
    lo_c, hi_c = int(150 / EDS_CHANNEL_EV), min(EDS_CHANNELS, int(e0_ev / EDS_CHANNEL_EV))
    coef, _ = _nnls_fit(A[lo_c:hi_c], y[lo_c:hi_c], 1.0 / np.sqrt(y[lo_c:hi_c] + 1.0))
    return A @ coef


@functools.lru_cache(maxsize=4096)
def _family_main_intensity(sym, fam, e0_ev):
    """Emitted intensity per electron of the main line of a family for the pure element (ionisation x fluorescence yield x line
    intensity x self-absorption at 35 deg); used to bound the expected heights of an identified element's other line families."""
    lines, main, u0 = _family_lines(sym, fam, e0_ev)
    if main is None:
        return 0.0
    el = element(sym)
    lev = el['levels'][main['initial']]
    n = ionisations_per_electron({sym: 1.0}, sym, main['initial'], e0_ev / 1000.0)
    f_abs = philibert_f(mu_rho(sym, main['energy_eV']) / math.sin(math.radians(35.0)), e0_ev / 1000.0, lev['edge_eV'] / 1000.0, el['molar_mass_g_mol'], el['Z'])
    return n * lev['fluorescence_yield'] * main['relative_intensity'] * f_abs


# noble gases and elements without stable isotopes do not occur in SEM specimens: never proposed by the automatic identification
ID_EXCLUDED = {'He', 'Ne', 'Ar', 'Kr', 'Xe', 'Rn', 'Tc', 'Pm', 'Po', 'At', 'Fr', 'Ra', 'Ac', 'Pa',     # Ar (implanted by ion milling or sputtering) must be given explicitly
               'Be', 'B'}               # Be K 108 eV, B K 183 eV: inside the window-transmission structure below the C K edge (give them explicitly)
ID_MIN_BASE_SCORE = -2.0          # a candidate may miss at most one expected line family before the local shape is compared


def identify_elements(counts, e0_ev, noise_ev, max_elements=12, trace=None):
    """Automatic peak identification in up to three passes on the efficiency-corrected spectrum: Gaussian-like top-hat peaks (>= 8 sigma
    and >= 3 % of the local counts; doublets accepted) are taken strongest first by detected counts (the K and L lines of the heavier
    elements then explain their own low-energy L / M lines, which the efficiency correction inflates) and assigned to the element whose line family has its
    strongest line within half a FWHM and whose other strong lines (same family, expected heights from the line weights, and the main
    lines of its other excited families) are present and not already claimed; candidates that miss more than one expected family are
    dropped, the rest are ranked by that score and by the local shape of the family pattern (pure-element self-absorption included).
    Noble gases and unstable elements are never proposed.  A peak is explained by an identified element only if that element's expected
    line height there is at least half the peak; a continuum absorption edge of an identified element explains a peak no higher than
    about 0.6 of the local continuum.  After each pass all identified elements are fitted and the next pass searches the residual
    (e.g. S Ka hidden under Pb Ma).  Searches from 200 eV: B K (183 eV) and Be K (108 eV) lie in the window-transmission structure below the
    C K edge and are never proposed automatically (give them in elements=[...])."""
    y = np.asarray(counts, float)
    eff = _efficiency_array()
    z = y / eff
    vz = np.maximum(y, 1.0) / eff ** 2
    f_raw, var = _tophat(z, noise_ev, vz)
    sig_raw = f_raw / np.sqrt(var)
    lo_c, hi_c = int(200 / EDS_CHANNEL_EV), min(EDS_CHANNELS - 2, int(0.98 * e0_ev / EDS_CHANNEL_EV))

    def window(e_ev):
        c = int(e_ev / EDS_CHANNEL_EV)
        k = max(1, int(round(0.25 * fwhm_ev(e_ev, noise_ev) / EDS_CHANNEL_EV)))
        return slice(max(c - k, 0), min(c + k + 1, len(f_raw)))

    def height(arr, e_ev):
        return float(np.max(arr[window(e_ev)])) if 0 <= int(e_ev / EDS_CHANNEL_EV) < len(arr) else 0.0

    def is_claimed(e_ev, h_obs, claims):
        """Explained when an identified element's expected height there reaches half the peak, or the line heights of several identified
        elements together do (a blend, e.g. Cs and I M lines; each element counted once, absorption-edge artefacts never summed)."""
        fw_ = fwhm_ev(e_ev, noise_ev)
        near = [(ch, sym_, kind) for ce, ch, sym_, kind in claims if abs(e_ev - ce) <= 0.5 * fw_]
        if not near:
            return False
        if max(ch for ch, _, _ in near) >= 0.5 * h_obs:
            return True
        per_el = {}
        for ch, sym_, kind in near:
            if kind == 'line':
                per_el[sym_] = max(per_el.get(sym_, 0.0), ch)
        return sum(per_el.values()) >= 0.5 * h_obs

    def local_level(e_ev):
        c = int(e_ev / EDS_CHANNEL_EV)
        k = max(2, int(2 * fwhm_ev(e_ev, noise_ev) / EDS_CHANNEL_EV))
        seg = z[max(c - k, 0):max(c, 1)]
        return float(np.median(seg)) if len(seg) else 0.0

    found_fam = {}                                      # element -> (family it was identified by, its measured main-line height)

    def element_claims(sym, scale_of):
        """Positions and expected heights the element explains; the other families' heights are the measured ones but never more than
        twice the theoretical ratio to the identifying family (a neighbour's peak must not be taken as, e.g., the weak Ca Ll)."""
        out = []
        fam_id, h_id = found_fam.get(sym, (None, None))
        i_id = _family_main_intensity(sym, fam_id, e0_ev) if fam_id else 0.0
        for lev, v in element(sym)['levels'].items():               # absorption edges of an identified element shape the continuum
            if lev in ('K', 'L3', 'M5', 'M4') and 250.0 < v['edge_eV'] < 0.9 * e0_ev:
                fw_ = fwhm_ev(v['edge_eV'], noise_ev)
                h_edge = 0.3 * local_level(v['edge_eV'])
                for de in (0.25, 0.5, 0.75, 1.0):
                    out.append((v['edge_eV'] - de * fw_, h_edge, sym, 'edge'))
        for fam in ('K', 'L', 'M'):
            lines, main, u0 = _family_lines(sym, fam, e0_ev)
            if main is None or main['energy_eV'] < 250.0:
                continue
            h_main = scale_of(fam, main)
            if fam_id and fam != fam_id and i_id > 0.0:
                _, m_id, _ = _family_lines(sym, fam_id, e0_ev)
                ratio = _family_main_intensity(sym, fam, e0_ev) / i_id * fwhm_ev(m_id['energy_eV'], noise_ev) / fwhm_ev(main['energy_eV'], noise_ev)
                h_main = min(h_main, 2.0 * h_id * ratio)
            if h_main <= 0.0:
                continue
            wm, fm = _line_weight(sym, main), fwhm_ev(main['energy_eV'], noise_ev)
            for ln in lines:
                if ln['energy_eV'] < 250.0:
                    continue
                h = h_main * _line_weight(sym, ln) / wm * fm / fwhm_ev(ln['energy_eV'], noise_ev)
                out.append((ln['energy_eV'], h, sym, 'line'))
                if ln['energy_eV'] > _SI_K_EDGE_EV:
                    e_esc = ln['energy_eV'] - _SI_KA_EV
                    pe = escape_fraction(ln['energy_eV'])
                    out.append((e_esc, h * pe / (1.0 - pe) * fwhm_ev(ln['energy_eV'], noise_ev) / fwhm_ev(e_esc, noise_ev)
                                * detector_efficiency(ln['energy_eV']) / max(detector_efficiency(e_esc), 0.02), sym, 'escape'))
        return out
    elems = [s_ for s_, e in _lib()['elements'].items() if EDS_Z_RANGE[0] <= e['Z'] <= EDS_Z_RANGE[1] and e['lines'] and s_ not in ID_EXCLUDED]
    found = []
    resid = z
    f_model = np.zeros_like(z)
    for _ in range(3):
        first = resid is z
        f, _ = _tophat(resid, noise_ev, vz)
        peaks = _peaks(f, f / np.sqrt(var), z, noise_ev, lo_c, hi_c, 8.0 if first else 15.0, 0.03 if first else 0.08, artifacts=True)
        if not first:                                   # a hidden element must be a substantial part of the fitted signal at its position
            peaks = [(e, h) for e, h in peaks if h >= 0.15 * max(height(f_model, e), 0.0)]
        f_cnt, _ = _tophat(resid * eff, noise_ev)        # rank by detected counts: the efficiency correction inflates low-energy L / M lines
        peaks.sort(key=lambda p_: -float(f_cnt[min(int(p_[0] / EDS_CHANNEL_EV), len(f_cnt) - 1)]))
        claims = []
        for sym_ in found:
            fam_, _ = found_fam[sym_]
            _, m_, _ = _family_lines(sym_, fam_, e0_ev)
            found_fam[sym_] = (fam_, height(f_model, m_['energy_eV']))
            claims += element_claims(sym_, lambda fam, main: height(f_model, main['energy_eV']))
        raw_peaks = [e for e, _ in _peaks(f_raw, sig_raw, z, noise_ev, lo_c, hi_c, 5.0, 0.0, artifacts=False)]
        new = []
        for e_pk, h_pk in peaks:
            if len(found) + len(new) >= max_elements:
                break
            if is_claimed(e_pk, h_pk, claims):
                continue
            fw = fwhm_ev(e_pk, noise_ev)
            cands = []
            for sym in elems:
                if sym in found or sym in new:
                    continue
                for rank, fam in enumerate(('K', 'L', 'M')):
                    lines, main, u0 = _family_lines(sym, fam, e0_ev)
                    if main is None or u0 < 1.1 or abs(main['energy_eV'] - e_pk) > 0.5 * fw:
                        continue
                    wm = _line_weight(sym, main)
                    fm = fwhm_ev(main['energy_eV'], noise_ev)
                    expect = [(ln, h_pk * _line_weight(sym, ln) / wm * fm / fwhm_ev(ln['energy_eV'], noise_ev)) for ln in lines
                              if _line_weight(sym, ln) >= 0.25 * wm and abs(ln['energy_eV'] - main['energy_eV']) > 2.0 * fwhm_ev(ln['energy_eV'], noise_ev)]
                    weak = []
                    i_fam = _family_main_intensity(sym, fam, e0_ev)
                    for fam2 in ('K', 'L', 'M'):
                        if fam2 != fam:
                            _, m2, u2 = _family_lines(sym, fam2, e0_ev)
                            if m2 is not None and u2 >= 1.1 and m2['energy_eV'] > 250.0:
                                # expected only when its predicted height (pure-element ratio) stands 10 sigma above the local noise:
                                # the M lines of 4d elements next to their L lines are usually too weak to demand
                                c2 = min(int(m2['energy_eV'] / EDS_CHANNEL_EV), len(var) - 1)
                                pred = h_pk * _family_main_intensity(sym, fam2, e0_ev) / i_fam * fm / fwhm_ev(m2['energy_eV'], noise_ev) if i_fam > 0.0 else 0.0
                                (expect if (u2 >= 1.5 and pred >= 10.0 * math.sqrt(var[c2])) else weak).append((m2, None))

                    def ok(ln, h):
                        obs = height(f_raw, ln['energy_eV'])
                        if is_claimed(ln['energy_eV'], obs, claims):
                            return False
                        if h is None:                     # another family's main line: a detected peak must be there
                            return any(abs(e_ - ln['energy_eV']) <= 0.5 * fwhm_ev(ln['energy_eV'], noise_ev) for e_ in raw_peaks)
                        return obs >= 0.3 * h
                    hits = sum(1 for ln, h in expect if ok(ln, h))
                    bonus = sum(1 for ln, h in weak if ok(ln, h))
                    score = 2.0 * (hits + bonus) - 1.5 * (len(expect) - hits) - 0.5 * rank - abs(main['energy_eV'] - e_pk) / fw
                    # intensity consistency: the peak must not be far stronger than this family can be next to the element's other
                    # detected family (pure-element ratio, a factor 10 for matrix absorption): a weak K L family cannot explain a C K peak
                    for m2, _ in [x for x in expect + weak if x[1] is None]:
                        if not ok(m2, None):
                            continue
                        fam2 = _family(m2['initial'])
                        i2 = _family_main_intensity(sym, fam2, e0_ev)
                        h2 = height(f_raw, m2['energy_eV'])
                        if i2 > 0.0 and h2 > 0.0 and h_pk > 10.0 * (i_fam / i2) * (fwhm_ev(m2['energy_eV'], noise_ev) / fm) * h2:
                            score -= 4.0
                    if score < ID_MIN_BASE_SCORE:
                        continue
                    others = [(s2, f2) for s2 in found + new for f2 in ('K', 'L', 'M') if _family_lines(s2, f2, e0_ev)[1] is not None]
                    cands.append([score, sym, fam, main, _local_chi2(resid, vz, sym, fam, e_pk, e0_ev, noise_ev, others)])
            if not cands:
                continue
            c_best = min(c[4] for c in cands)            # local shape: the family pattern (e.g. Zr La+Lb doublet vs P Ka) must fit the peak
            n_w = max(10.0, 5.0 * fw / EDS_CHANNEL_EV)
            for c in cands:
                c[0] -= min(4.0, 2.0 * (c[4] - c_best) / max(c_best, n_w))
            best = tuple(max(cands, key=lambda c: c[0])[:4])
            _, sym, fam_m, main_m = best
            new.append(sym)
            found_fam[sym] = (fam_m, h_pk)
            if trace is not None:
                trace.append(dict(pass_first=first, peak_ev=e_pk, element=sym, score=best[0]))
            claims += element_claims(sym, lambda fam, main: h_pk if fam == fam_m else height(f_raw, main['energy_eV']))
        if not new:
            break
        found += new
        model = _fit_elements(y, found, e0_ev, noise_ev)
        resid = (y - model) / eff
        f_model, _ = _tophat(model / eff, noise_ev, vz)
    return found


def quantify(counts, e0_ev, psi_rad, noise_ev, elements):
    """Standardless ZAF: family areas by weighted least squares (Gaussian templates + continuum), then iterate C_i ~ area_i / P_i(C)."""
    y = np.asarray(counts, float)
    elements = [s for s in elements if s in _lib()['elements'] and element(s)['Z'] >= EDS_Z_RANGE[0]]
    fams = {s: _analytical_family(s, e0_ev) for s in elements}
    usable = [s for s in elements if fams[s]]
    if not usable:
        raise ActionRejected('QUANT_FAILED')
    comp = {s: 1.0 / len(usable) for s in usable}
    lo_c, hi_c = int(150 / EDS_CHANNEL_EV), min(EDS_CHANNELS, int(e0_ev / EDS_CHANNEL_EV))
    w = 1.0 / np.sqrt(y + 1.0)
    for _ in range(8):
        cols, names = [], []
        for s in usable:
            for fam in ('K', 'L', 'M'):
                tot, shape = _quant_model(comp, s, fam, e0_ev, psi_rad, noise_ev)
                if tot > 0.0 and shape.sum() > 0.0:
                    cols.append(shape / shape.sum()); names.append((s, fam))
        b1, b2 = _background_basis(e0_ev, comp, psi_rad)
        A = np.vstack(cols + [b1, b2]).T
        coef, var = _nnls_fit(A[lo_c:hi_c], y[lo_c:hi_c], w[lo_c:hi_c])
        areas = {nm: (c, math.sqrt(max(v, 0.0))) for nm, c, v in zip(names, coef, var)}
        new = {}
        for s in usable:
            a, _ = areas.get((s, fams[s]), (0.0, 0.0))
            tot, _ = _quant_model(comp, s, fams[s], e0_ev, psi_rad, noise_ev)
            new[s] = max(a, 0.0) / tot if tot > 0 else 0.0
        norm = sum(new.values())
        if norm <= 0:
            raise ActionRejected('QUANT_FAILED')
        new = {s: max(v / norm, 1e-6) for s, v in new.items()}
        if max(abs(new[s] - comp[s]) for s in usable) < 1e-6:
            comp = new
            break
        comp = new
    bgfit = A[:, -2:] @ coef[-2:]
    out = []
    atoms = {s: comp[s] / element(s)['molar_mass_g_mol'] for s in usable}
    at_norm = sum(atoms.values())
    for s in usable:
        a, sa = areas.get((s, fams[s]), (0.0, 0.0))
        lines = [ln for ln in _lines_of(s, e0_ev) if _family(ln['initial']) == fams[s]]
        main = max(lines, key=lambda ln: ln['relative_intensity'])
        fw = fwhm_ev(main['energy_eV'], noise_ev)
        c0, c1 = int((main['energy_eV'] - fw) / EDS_CHANNEL_EV), int((main['energy_eV'] + fw) / EDS_CHANNEL_EV) + 1
        bkg = float(np.sum(bgfit[max(c0, 0):max(c1, 1)]))
        rel = math.sqrt(a + 2.0 * bkg) / a if a > 0 else None
        mdl = 3.0 * math.sqrt(bkg) / a * comp[s] if a > 0 else None
        out.append(dict(element=s, line_family=fams[s], mass_fraction=comp[s], atom_fraction=atoms[s] / at_norm, mass_fraction_sigma=comp[s] * rel if rel else None,
                        net_counts=a, background_counts=bkg, detection_limit_mass_fraction=mdl))
    return out


# ---------------------------------------------------------------- microstructure (seeded Voronoi phase map)
def _hash01(*ints):
    h = 1469598103934665603
    for v in ints:
        h ^= (int(v) & 0xFFFFFFFFFFFFFFFF)
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 29
    return ((h * 2685821657736338717) & 0xFFFFFFFFFFFFFFFF) / 2.0 ** 64


def phase_at(spec, u, v):
    """Index of the phase at specimen coordinates (u, v) (m): nearest jittered cell centre on a grid of the mean grain size."""
    g = spec['_grain']
    i0, j0 = math.floor(u / g), math.floor(v / g)
    best, bi = 1e30, 0
    for i in (i0 - 1, i0, i0 + 1):
        for j in (j0 - 1, j0, j0 + 1):
            cx = (i + _hash01(i, j, spec['_seed'], 1)) * g
            cy = (j + _hash01(i, j, spec['_seed'], 2)) * g
            d = (cx - u) ** 2 + (cy - v) ** 2
            if d < best:
                best, bi = d, (i, j)
    r = _hash01(bi[0], bi[1], spec['_seed'], 3)
    acc = 0.0
    for k, ph in enumerate(spec['phases']):
        acc += ph['area_fraction']
        if r < acc:
            return k
    return len(spec['phases']) - 1


def local_tilt(spec, u, v):
    """Local surface slope (rad) and its azimuth for rough or particle specimens (per grain; flat polished = 0)."""
    topo = spec['topography']
    if topo == 'flat_polished':
        return 0.0, 0.0
    g = spec['_grain']
    i, j = math.floor(u / g), math.floor(v / g)
    amp = math.radians(35.0 if topo == 'rough_fracture' else 25.0)
    return amp * _hash01(i, j, spec['_seed'], 4), 2 * math.pi * _hash01(i, j, spec['_seed'], 5)


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c) if isinstance(c, (list, tuple)) else [c], function_zh=meaning,
                   visibility=visibility, kind=kind, effects=[])


PLANT_EXAMPLE = dict(room_k=295.15, relative_humidity=0.45, mains_voltage_v=100.0, vent_gas='dry_nitrogen', noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 308.15), relative_humidity=number('1', 0.0, 1.0), mains_voltage_v=number('V', 0.0, 140.0),
                 vent_gas=enum(['room_air', 'dry_nitrogen']), noise_seed=INT))
PHASE_ITEM = obj(dict(substance_id=TEXT, area_fraction=number('1', 0.0, 1.0), grain_size_m=number('m', 1e-8, 1e-2)))
SPECIMEN = obj(dict(specimen_id=dict(type='string', minLength=1, maxLength=40), phases=array(PHASE_ITEM, 1, 6), diameter_m=number('m', 1e-4, 0.3),
                    height_m=number('m', 1e-4, 0.1), topography=enum(['flat_polished', 'particles', 'rough_fracture']),
                    coating=enum(['none', 'carbon', 'gold', 'platinum']), coating_thickness_m=number('m', 0.0, 1e-7), wet=BOOL))
SPECIMEN_EXAMPLE = dict(specimen_id='fe_si_polished', phases=[dict(substance_id='iron', area_fraction=0.7, grain_size_m=2e-5),
                                                             dict(substance_id='silicon', area_fraction=0.3, grain_size_m=2e-5)],
                        diameter_m=0.012, height_m=0.005, topography='flat_polished', coating='none', coating_thickness_m=0.0, wet=False)
OPERATION = obj(dict(operation_id=TEXT))
REGION = obj(dict(region_id=TEXT, area_fraction_in_view=number('1', 0.0, 1.0), mean_grey=number('1', 0.0, 255.0), snr=number('1', 0.0), charging=BOOL))
IMAGE = obj(dict(image_id=TEXT, status=enum(['running', 'completed', 'failed', 'cancelled']), detector=TEXT, accelerating_voltage_v=number('V'),
                 probe_current_a=number('A'), magnification=number('1'), pixels=TEXT, dwell_s=number('s'), frame_time_s=number('s'),
                 field_of_view_m=array(number('m'), 2, 2), pixel_size_m=nullable(number('m')), working_distance_m=nullable(number('m')),
                 probe_diameter_m=nullable(number('m')), effective_resolution_m=nullable(number('m')), vacuum_mode=enum(['HV', 'LV']),
                 chamber_pressure_pa=nullable(number('Pa')), regions=array(REGION, 0, 8), contrast=nullable(number('1')), charging_artifacts=BOOL,
                 saturated_fraction=nullable(number('1')), thumbnail=array(array(integer(0, 255), THUMB_W, THUMB_W), 0, THUMB_H),
                 error_code=nullable(TEXT), started_s=number('s'), finished_s=nullable(number('s')), template_source=obj(dict(library=TEXT, substances=array(TEXT, 0, 8)))))
SPECTRUM = obj(dict(spectrum_id=TEXT, status=enum(['running', 'completed', 'failed', 'cancelled']), mode=enum(['point', 'area']),
                    accelerating_voltage_v=number('V'), duane_hunt_limit_ev=nullable(number('eV')), probe_current_a=number('A'), live_time_s=number('s'),
                    real_time_s=nullable(number('s')), process_time=TEXT, dead_time_fraction=nullable(number('1')), input_count_rate_cps=nullable(number('1/s')),
                    output_count_rate_cps=nullable(number('1/s')), takeoff_angle_rad=nullable(number('rad')), solid_angle_sr=nullable(number('sr')),
                    working_distance_m=nullable(number('m')), channel_width_ev=number('eV'), counts=array(integer(0, 2 ** 31 - 1), 0, EDS_CHANNELS),
                    total_counts=INT, fwhm_mn_ka_ev=number('eV'), error_code=nullable(TEXT), started_s=number('s'), finished_s=nullable(number('s')),
                    template_source=obj(dict(library=TEXT, substances=array(TEXT, 0, 8)))))
QUANT_ITEM = obj(dict(element=TEXT, line_family=enum(['K', 'L', 'M']), mass_fraction=number('1', 0.0, 1.0), atom_fraction=number('1', 0.0, 1.0),
                      mass_fraction_sigma=nullable(number('1')), net_counts=number('1'), background_counts=number('1'),
                      detection_limit_mass_fraction=nullable(number('1'))))
QUANT = obj(dict(spectrum_id=TEXT, method=TEXT, elements_identified=array(TEXT, 0, 16), results=array(QUANT_ITEM, 0, 16), total_before_normalisation=nullable(number('1'))))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'vacuum', 'gun', 'stage', 'door', 'specimen', 'acquisition', 'warning', 'error', 'maintenance']),
                 code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['beam_charge_c', 'backscattered_charge_c', 'secondary_charge_c', 'absorbed_charge_c', 'stored_charge_c', 'charge_residual_c',
               'beam_energy_j', 'gas_admitted_mol', 'gas_pumped_mol', 'gas_in_volume_mol', 'gas_residual_mol', 'xray_generated', 'xray_detected',
               'filament_on_time_s', 'electrons_on_specimen']
LEDGER = obj({k: number('C' if k.endswith('_c') else 'J' if k.endswith('_j') else 'mol' if k.endswith('_mol') else 's' if k.endswith('_s') else '1')
              for k in LEDGER_KEYS})

cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['cabinet_body', 'rear_panel', 'vent_valve'],
    '宿主声明实验室条件：室温（安装要求15–27°C）、相对湿度（≤60%）、供电电压（单相AC 100V±10%，1.5kVA）、放气气体（室内空气或干燥氮气）与噪声随机数种子（只在首次声明时生效）',
    'evaluator', 'configuration')
cap('specimen.provide', SPECIMEN, obj(dict(specimen_id=TEXT, provided=BOOL)), SPECIMEN_EXAMPLE, 'specimen_holder',
    '宿主在实验台上放入一个样品：由模板库物质组成的相（面积分数、晶粒尺寸）、直径、高度、形貌（抛光平面/颗粒/粗糙断口）、导电镀膜（碳、金、铂及厚度）与是否含水；'
    '接受厂家上限（直径150mm、高48mm），但高于44mm的样品在Z最低时也离极靴不足4mm，推不进样品室', 'evaluator',
    'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), 'key_main_power',
    'MAIN POWER钥匙开关：转到START（开）启动主机与计算机，软件约60s加载后可操作；转到OFF（关）断电——应先在软件中关机（system.shutdown）；断电时电子枪高压立即关闭、泵停转，样品室在真空下缓慢漏气')
cap('power.key_toggle', obj({}), obj(dict(enabled=BOOL)), {}, 'key_main_power', 'MAIN POWER钥匙开关按压（Matterix弹簧键）：切换通电状态，语义同power.set')
cap('system.shutdown', obj({}), obj(dict(shutdown=BOOL)), {}, ['monitor', 'control_electronics'], '软件关机（采集进行中时被拒，须先结束或取消采集）：关闭电子枪高压与灯丝、保存记录；之后才应把钥匙转到OFF')
cap('error.acknowledge', obj({}), obj(dict(acknowledged=TEXT)), {}, 'monitor', '确认当前错误（抽气超时、灯丝烧断、高压跳闸等），仪器回到可操作状态')
cap('vacuum.evacuate', obj({}), obj(dict(state=TEXT)), {}, ['button_evac', 'rotary_pump', 'turbo_molecular_pump', 'vacuum_valve_block'],
    'EVAC开关：关闭放气阀，机械泵粗抽样品室至约20Pa后切换涡轮分子泵抽高真空（LV模式时抽到设定低真空）；抽气中EVAC灯闪烁、完成后常亮；10min未完成报错（JSM-IT300手册）')
cap('vacuum.vent', obj({}), obj(dict(state=TEXT)), {}, ['button_vent', 'vent_valve', 'turbo_molecular_pump'],
    'VENT开关：关闭电子枪高压，停止涡轮分子泵抽样品室，打开放气阀引入宿主声明的放气气体；放气中VENT灯闪烁、达到大气压后常亮；样品台X自动回到交换位；Y也回0，'
    '但倾斜时若Y回位会使样品升到极靴下4mm以内则Y不动（事件Y_RETURN_SKIPPED）')
cap('vacuum.toggle_lv', obj({}), obj(dict(mode=enum(['HV', 'LV']))), {}, ['button_lv', 'lv_valve'],
    'LV开关：在高真空（HV）与低真空（LV，10–100Pa，由针阀维持设定压力）之间切换；观察中先自动关闭电子枪高压（事件HT_OFF）；样品室在真空下时立即转入对应抽气；'
    'LV抽气完成后LV灯常亮；LV下只能用BED成像（SED自动切换到BED）')
cap('vacuum.set_lv_pressure', obj(dict(pressure_pa=number('Pa', LV_RANGE_PA[0], LV_RANGE_PA[1]))), obj(dict(pressure_pa=number('Pa'))), dict(pressure_pa=50.0),
    ['lv_valve', 'monitor'], '设定低真空样品室压力10–100Pa（厂家LV压力范围）；只写设定值，实际压力随step由针阀调节')
cap('chamber.draw_out', obj({}), obj(dict(target_m=number('m'))), {}, ['chamber_door', 'stage_base'],
    '拉出样品室门（抽拉式换样，样品台随门出舱）：须已放气到大气压；门在真空下被大气压压住拉不开', 'agent', 'robot_skill')
cap('chamber.draw_in', obj({}), obj(dict(target_m=number('m'))), {}, ['chamber_door', 'stage_base'],
    '推回并关好样品室门：样品（或样品座）最高点须在极靴下至少4mm（背散射探测器挂在极靴下3.5mm，即离BED至少0.5mm）；按Z、倾斜与Y从当前位置到目标的整个扫掠范围严格检查，'
    '否则拒绝以免碰撞（先用Z旋钮降低样品台）；推回后样品台视为在舱内，装卸样品被拒', 'agent', 'robot_skill')
cap('specimen.load', obj(dict(specimen_id=TEXT)), obj(dict(specimen_id=TEXT, loaded=BOOL)), dict(specimen_id='fe_si_polished'), ['specimen_holder', 'stage_rot'],
    '把实验台上的样品装到样品座上（门须完全拉出）；样品直径≤150mm、高度≤48mm（厂家）', 'agent', 'robot_skill')
cap('specimen.unload', obj({}), obj(dict(specimen_id=TEXT)), {}, ['specimen_holder', 'stage_rot'], '从样品座取下样品放回实验台（门须完全拉出）', 'agent', 'robot_skill')
cap('stage.move_xy', obj(dict(x_m=number('m', STAGE_X_RANGE_M[0], STAGE_X_RANGE_M[1]), y_m=number('m', STAGE_Y_RANGE_M[0], STAGE_Y_RANGE_M[1]))),
    obj(dict(x_m=number('m'), y_m=number('m'))), dict(x_m=0.002, y_m=-0.001), ['stage_x', 'stage_y'],
    '电机驱动样品台X（80mm）/Y（40mm）移到目标位置（目录称优中心台；约3mm/s）；Y沿倾斜后的摇篮移动，倾斜时样品随Y升降：扫掠范围内最高点会进入极靴下4mm以内且高于当前位置时拒绝；'
    '只写目标，位置随step推进；须门关闭')
cap('stage.set_z', obj(dict(z_lift_m=number('m', Z_LIFT_RANGE_M[0], Z_LIFT_RANGE_M[1]))), obj(dict(z_lift_m=number('m'))), dict(z_lift_m=0.038),
    ['knob_stage_z', 'stage_z'], 'Z旋钮（手动）：升降样品台，升降0对应样品座顶面WD 48mm，0.043m对应WD 5mm；门完全拉出（且未下令推回）时不检查碰撞；样品台在舱内时，'
    '按当前到目标的Z与正在进行的倾斜/Y运动的扫掠范围检查，最高点会进入极靴下4mm以内且高于当前位置则拒绝（降低总是允许）；只写目标，随step推进')
cap('stage.set_tilt', obj(dict(tilt_rad=number('rad', TILT_RANGE_RAD[0], TILT_RANGE_RAD[1]))), obj(dict(tilt_rad=number('rad'))), dict(tilt_rad=0.35),
    ['knob_stage_tilt', 'stage_tilt'], 'T旋钮（手动）：倾斜−10°～90°（绕过样品座顶面的X轴，正值使样品朝门倾斜）；倾斜时样品边缘升高：样品台在舱内时按倾斜区间（含边缘最高的中间角度）'
    '与Z/Y扫掠范围检查，最高点会进入极靴下4mm以内且高于当前位置则拒绝（门完全拉出时不检查）；电子束落在倾斜样品顶面上距中心h·tanθ处（超出样品则落到样品座）；只写目标，随step推进')
cap('stage.rotate', obj(dict(rotation_rad=number('rad', ROT_RANGE_RAD[0], ROT_RANGE_RAD[1]))), obj(dict(rotation_rad=number('rad'))), dict(rotation_rad=1.0),
    ['door_center_cap', 'stage_rot'], 'R（手动）：样品旋转0–360°；只写目标，随step推进')
cap('beam.set_voltage', obj(dict(voltage_v=number('V', KV_RANGE_V[0], KV_RANGE_V[1]))), obj(dict(voltage_v=number('V'))), dict(voltage_v=15000.0), ['electron_gun', 'hv_power_supply'],
    '加速电压0.5–30kV（厂家）；设定按同系列手册取整到0.1kV（≤3kV）或1kV（>3kV）；高压开着时立即生效，换电压后需重新聚焦/消像散')
cap('beam.set_probe_current', obj(dict(current_a=number('A', PROBE_CURRENT_RANGE_A[0], PROBE_CURRENT_RANGE_A[1]))), obj(dict(current_a=number('A'))), dict(current_a=1e-10),
    ['condenser_lens', 'objective_lens'], '探针电流1pA–0.3μA（厂家）：聚光镜与光阑决定束流，束流越大束斑越大；只写设定值')
cap('beam.observation_on', obj({}), obj(dict(ht_on=BOOL)), {}, ['electron_gun', 'hv_power_supply', 'gun_alignment'],
    '开始观察（Observation）：加高压、灯丝自动加热到饱和并自动对中（约10s后形成图像）；须通电、真空就绪（高真空或低真空，真空就绪即门已关闭）、灯丝未烧断')
cap('beam.observation_off', obj({}), obj(dict(ht_on=BOOL)), {}, ['electron_gun', 'hv_power_supply'], '停止观察：关闭高压与灯丝加热（进行中的采集以BEAM_OFF失败）')
cap('gun.replace_filament', obj({}), obj(dict(filament_serial=INT)), {}, ['electron_gun', 'gun_cap'],
    '更换钨灯丝（预对中灯丝）：须放气到大气压（放气时高压已自动关闭）；新灯丝寿命为工程值（Weibull分布，平均300h）', 'agent', 'robot_skill')
cap('scan.set_magnification', obj(dict(magnification=number('1', MAG_RANGE[0], MAG_RANGE[1]))), obj(dict(magnification=number('1'), field_width_m=number('m'))),
    dict(magnification=2000.0), ['scan_coil_section', 'monitor'], '直接放大倍数×5–300,000（以128×96mm打印为准，厂家）：视场宽=0.128m/倍数')
cap('optics.autofocus', obj({}), obj(dict(focus_wd_m=number('m'))), {}, ['objective_lens', 'monitor'],
    '自动聚焦（AF）：须有束流与可成像的样品；把物镜聚焦到束斑所在的表面（误差约为景深的1/8，1σ）；屏上WD显示聚焦值')
cap('optics.set_focus', obj(dict(focus_wd_m=number('m', 0.001, 0.060))), obj(dict(focus_wd_m=number('m'))), dict(focus_wd_m=0.010), ['objective_lens', 'monitor'],
    '手动聚焦：设定物镜聚焦的工作距离；与样品实际距离之差造成离焦模糊（2αΔz）')
cap('optics.auto_stigmate', obj({}), obj(dict(astigmatism_m=number('m'))), {}, ['scan_coil_section', 'monitor'], '自动消像散（AS）：须有束流；把像散模糊降到约1nm')
cap('optics.auto_contrast_brightness', obj({}), obj(dict(gain=number('1'), offset=number('1'))), {}, ['monitor', 'bse_detector', 'sed_detector'],
    '自动衬度/亮度（ACB）：按当前视场实时帧（640×480、10µs）的灰度直方图——信号范围加减2.5σ像素噪声——设定探测器增益与偏置，使图像灰度落在20–235；'
    '没有信号衬度的视场（如平整样品的TOPO像）被拉伸到噪声宽度，增益有限')
cap('detector.select', obj(dict(detector=enum(['SED', 'BED_COMPO', 'BED_TOPO', 'BED_SHADOW']))), obj(dict(detector=TEXT)), dict(detector='BED_COMPO'),
    ['sed_detector', 'bse_detector'], '选择成像探测器：SED（E-T二次电子，只能高真空）或BED的成分（COMPO）、形貌（TOPO）、阴影（Shadow）像')
cap('image.acquire', obj(dict(pixels=enum(list(PIXEL_FORMATS)), dwell_s=number('s', 1e-7, 1e-2))), OPERATION, dict(pixels='640x480', dwell_s=2e-5),
    ['monitor', 'sed_detector', 'bse_detector', 'scan_coil_section'],
    '采集一帧图像（异步）：像素320×240–5120×3840（厂家），帧时间=像素数×驻留时间；返回按灰度分割的区域（按平均灰度排序，不给出物质名；平均灰度之差小于噪声与相内形貌纹理所定阈值的相并为一区）、'
    '各区平均灰度与信噪比、衬度（TOPO差分信号无定义，为null）、有效分辨率、显示的WD、荷电标记与32×24缩略图；束流或真空条件在采集中改变则失败')
cap('image.result', obj(dict(image_id=TEXT)), IMAGE, dict(image_id='img-1'), 'monitor', '读取图像记录（本调用者可见）', 'agent', 'query')
cap('eds.acquire', obj(dict(mode=enum(['point', 'area']), live_time_s=number('s', 1.0, 600.0), process_time=enum(list(EDS_PROCESS_TIMES)))), OPERATION,
    dict(mode='point', live_time_s=30.0, process_time='T3'), ['eds_finger', 'eds_detector_housing', 'monitor'],
    'EDS采谱（异步）：点分析（束斑处，含X射线激发体积内的相混合）或面分析（当前视场）；活时间1–600s，处理时间T1–T4（越长分辨率越好、死时间越大）；预计死时间超过90%时拒绝'
    '（降低探针电流）；结果为0–20.48keV的2048道计数谱、由谱估计的Duane–Hunt限（荷电时低于加速电压），以及按显示WD与倾斜读数计算的名义出射角/立体角')
cap('eds.result', obj(dict(spectrum_id=TEXT)), SPECTRUM, dict(spectrum_id='eds-1'), 'monitor', '读取EDS谱记录（本调用者可见）', 'agent', 'query')
cap('eds.quantify', obj(dict(spectrum_id=TEXT, elements=array(TEXT, 0, 16))), QUANT, dict(spectrum_id='eds-1', elements=[]), 'monitor',
    '无标样ZAF定量（厂家：standardless ZAF）：elements为空时自动识峰（B、Be与惰性气体不自动识别，可在elements中给出）；最小二乘拟合峰面积与连续谱，按记录的名义出射角迭代ZAF并'
    '归一化；返回质量分数与原子分数（0–1）、计数误差与检出限；自动识峰在5kV、T1/T2或10nA高计数等条件下可能多报或漏报元素，应核对后在elements中给出', 'agent', 'analysis')
cap('records.list', obj({}), obj(dict(images=array(TEXT, 0, MAX_RECORDS), spectra=array(TEXT, 0, MAX_RECORDS))), {}, 'monitor', '本调用者的图像与谱记录列表', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), 'monitor', '读取事件日志', 'agent', 'query')
cap('ledger.read', obj({}), LEDGER, {}, 'cabinet_body', '受限：电荷账（入射、背散射、二次电子、吸收、样品存储电荷）、束流能量、气体物料账（进入、抽走、体积内）、X射线产生/探测光子数、灯丝工作时间（s）',
    'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, substances=array(TEXT, 0, 100))), {}, 'specimen_holder', '受限：本实例用过的模板库记录', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), 'cabinet_body',
    '唯一宿主时间推进（纳秒整数时钟；1s仪器周期在全局时间网格上执行）', 'evaluator', 'simulation')
UNSUPPORTED = {
    'eds.map': 'EDS元素面分布（最高4096×3072）与线扫描未实现：样品虽有Voronoi相图，但未实现逐像素采谱与元素图的生成和记录格式',
    'stage.navigation_camera': '样品台导航系统SNS（光学相机拍样品座全景）是选配件，本配置未装',
    'chamber.scope': '样品室监视相机（Chamber Scope）是选配件，本配置未装',
    'image.montage': '拼图（Montage，目录列为标准功能）与3D重构（选配）未实现',
    'detector.lsed': '低真空二次电子探测器LSED是选配件，本配置未装（LV下用BED）',
    'stage.motor_zt': 'Z/T/R电机驱动（5轴电机台）是选配件；本配置X/Y电机，Z/T/R手动',
    'hardware.connect': '无真实JEOL主机、PC或EDS FIS通信',
    'robot.handle_specimen': '无机器人装样、拉门、拧旋钮的接触验证',
    'accuracy.certification': '无厂家分辨率/定量准确度的验收测量',
}
UNSUPPORTED_COMPONENTS = {'eds.map': ['eds_finger', 'monitor'], 'stage.navigation_camera': ['chamber_door'], 'chamber.scope': ['chamber_body'],
                          'image.montage': ['monitor'], 'detector.lsed': ['chamber_body'], 'stage.motor_zt': ['knob_stage_z', 'knob_stage_tilt'],
                          'hardware.connect': ['rear_panel'], 'robot.handle_specimen': ['specimen_holder', 'chamber_door'],
                          'accuracy.certification': ['objective_lens', 'eds_finger']}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, UNSUPPORTED_COMPONENTS[_k], _v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


STATES = ['off', 'booting', 'ready', 'shutdown']
VAC_STATES = ['vented', 'venting', 'roughing', 'hv_pumping', 'hv_ready', 'lv_pumping', 'lv_ready', 'idle']
LAMP_MODES = ['off', 'blinking', 'on']
ABS = '绝对压力'
slot('clock.time_s', number('s'), 's', 'derived', 'cabinet_body', '宿主时钟；read不推进')
slot('plant.configured', BOOL, '1', 'derived', 'cabinet_body', '实验室条件已声明')
slot('power.enabled', BOOL, '1', 'derived', 'key_main_power', '整机通电（钥匙在ON/START）')
slot('system.state', enum(STATES), '1', 'derived', 'monitor', '软件状态：off/booting（约60s加载）/ready/shutdown（已软件关机，等待钥匙OFF）')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'monitor', '待确认的错误代码（EVAC_TIMEOUT、FILAMENT_BURNOUT、HT_TRIP、MAINS_FAULT）；无为null')
slot('vacuum.mode', enum(['HV', 'LV']), '1', 'setpoint', 'button_lv', '真空模式：HV高真空或LV低真空')
slot('vacuum.state', enum(VAC_STATES), '1', 'derived', 'vacuum_valve_block',
     '真空系统状态：vented已放气/venting放气中/roughing粗抽/hv_pumping涡轮泵抽高真空/hv_ready高真空就绪/lv_pumping与lv_ready低真空/idle断电后停泵')
slot('vacuum.lv_setpoint_pa', number('Pa'), 'Pa', 'setpoint', 'lv_valve', '低真空样品室压力设定（10–100Pa）', reference=ABS)
slot('vacuum.chamber_pressure_indicated_pa', nullable(number('Pa')), 'Pa', 'sensor', 'vacuum_valve_block',
     '样品室皮拉尼真空计示值（有效范围约0.5Pa–大气压，低于0.5Pa显示0.5）；断电为null', reference=ABS)
slot('vacuum.gun_pressure_indicated_pa', nullable(number('Pa')), 'Pa', 'sensor', 'electron_gun', '电子枪/镜筒潘宁真空计示值（1e-6–1e-1Pa，高于0.1Pa显示0.1）；断电为null', reference=ABS)
slot('vacuum.lv_pressure_indicated_pa', nullable(number('Pa')), 'Pa', 'sensor', 'lv_valve', 'LV模式样品室压力示值（0.5%相对噪声，工程值；传感器类型厂家未公开）；非LV模式或断电为null（屏上Pa读数为空）',
     reference=ABS)
slot('vacuum.evacuation_elapsed_s', nullable(number('s')), 's', 'derived', 'vacuum_valve_block', '本次抽气已进行时间；未在抽气为null')
for _lamp, _zh in (('vent', 'VENT（放气中闪烁，大气压时常亮）'), ('evac', 'EVAC（抽气中闪烁，完成常亮）'), ('lv', 'LV（LV抽气中闪烁，LV就绪常亮）')):
    slot(f'panel.{_lamp}_lamp', BOOL, '1', 'derived', f'button_{_lamp}', f'{_zh}开关灯此刻是否点亮（闪烁按1Hz、半周期亮）；断电为false')
    slot(f'panel.{_lamp}_lamp_mode', enum(LAMP_MODES), '1', 'derived', f'button_{_lamp}', f'{_zh}开关灯模式off/blinking/on')
slot('chamber.door_state', enum(['closed', 'open', 'moving']), '1', 'derived', 'chamber_door', '样品室门状态（完全关闭/完全拉出/移动中）')
slot('specimen.loaded_id', nullable(TEXT), '1', 'derived', 'specimen_holder', '样品座上的样品ID；无为null')
slot('specimen.bench_ids', array(TEXT, 0, 20), '1', 'derived', 'specimen_holder', '实验台上可装的样品ID')
slot('stage.x_indicated_m', number('m'), 'm', 'sensor', 'stage_x', 'X电机编码器读数（0.1µm分辨率）')
slot('stage.y_indicated_m', number('m'), 'm', 'sensor', 'stage_y', 'Y电机编码器读数（0.1µm分辨率）')
slot('stage.z_indicated_m', number('m'), 'm', 'sensor', 'knob_stage_z', 'Z旋钮刻度读数（升降量，0.1mm分辨率）')
slot('stage.tilt_indicated_rad', number('rad'), 'rad', 'sensor', 'knob_stage_tilt', 'T旋钮刻度读数（0.5°分辨率）')
slot('stage.rotation_indicated_rad', number('rad'), 'rad', 'sensor', 'door_center_cap', 'R刻度读数（1°分辨率）')
slot('stage.moving', BOOL, '1', 'derived', 'stage_x', '样品台任一轴仍在向目标移动')
slot('beam.accelerating_voltage_v', number('V'), 'V', 'setpoint', 'electron_gun', '加速电压设定（屏上kV读数）')
slot('beam.probe_current_setpoint_a', number('A'), 'A', 'setpoint', 'condenser_lens', '探针电流设定')
slot('beam.ht_on', BOOL, '1', 'derived', 'hv_power_supply', '电子枪高压开着（观察中）')
slot('beam.filament_state', enum(['off', 'heating', 'saturated', 'burned_out']), '1', 'derived', 'electron_gun', '灯丝状态：off/heating（自动加热中）/saturated/burned_out')
slot('beam.emission_indicated_a', nullable(number('A')), 'A', 'sensor', 'hv_power_supply', '发射电流表读数；断电或高压关为null')
slot('scan.magnification', number('1'), '1', 'setpoint', 'scan_coil_section', '直接放大倍数（屏上×读数）')
slot('scan.field_width_m', number('m'), 'm', 'derived', 'scan_coil_section', '视场宽度=0.128m/倍数')
slot('optics.focus_wd_m', number('m'), 'm', 'setpoint', 'objective_lens', '物镜聚焦的工作距离（屏上WD读数；聚焦后接近样品实际WD）')
slot('optics.astigmatism_corrected', BOOL, '1', 'derived', 'scan_coil_section', '当前电压/灯丝下已做过自动消像散')
slot('optics.acb_done', BOOL, '1', 'derived', 'monitor', '当前条件下已做过自动衬度/亮度')
slot('detector.selected', enum(['SED', 'BED_COMPO', 'BED_TOPO', 'BED_SHADOW']), '1', 'setpoint', 'bse_detector', '当前成像探测器')
slot('acquisition.id', nullable(TEXT), '1', 'derived', 'monitor', '进行中的采集（图像或EDS）ID；无或属于另一个agent时为null')
slot('acquisition.kind', nullable(enum(['image', 'eds'])), '1', 'derived', 'monitor', '进行中采集的种类；无为null')
slot('acquisition.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'monitor', '进行中采集的进度（0–1）；无为null')
slot('acquisition.started_by_other_agent', BOOL, '1', 'derived', 'monitor', '进行中的采集属于另一个agent（按调用者计算）')
slot('result.last_image_id', nullable(TEXT), '1', 'derived', 'monitor', '本调用者最近完成的图像ID')
slot('result.last_spectrum_id', nullable(TEXT), '1', 'derived', 'monitor', '本调用者最近完成的EDS谱ID')
# process truth (evaluator): scene-joint mirrors and hidden physics
slot('chamber.door_position_m', number('m', 0.0, DOOR_TRAVEL_M), 'm', 'process_truth', 'chamber_door', '门拉出量（0关闭，0.20m完全拉出；过程权威，镜像到门关节）', 'evaluator',
     '沿−Y（朝操作者），关闭为0。')
slot('stage.x_m', number('m', STAGE_X_RANGE_M[0], STAGE_X_RANGE_M[1]), 'm', 'process_truth', 'stage_x', '样品台X位置（过程权威，镜像到X关节）', 'evaluator', '沿+X，交换位为0。')
slot('stage.y_m', number('m', STAGE_Y_RANGE_M[0], STAGE_Y_RANGE_M[1]), 'm', 'process_truth', 'stage_y', '样品台Y位置（沿倾斜后的Y；镜像到Y关节）', 'evaluator', '沿倾斜摇篮的+Y，交换位为0。')
slot('stage.z_lift_m', number('m', Z_LIFT_RANGE_M[0], Z_LIFT_RANGE_M[1]), 'm', 'process_truth', 'stage_z', '样品台Z升降量（0=样品座顶面WD 48mm；镜像到Z关节与Z旋钮）', 'evaluator',
     '沿+Z，升降0时样品座顶面在极靴下48mm。')
slot('stage.tilt_rad', number('rad', TILT_RANGE_RAD[0], TILT_RANGE_RAD[1]), 'rad', 'process_truth', 'stage_tilt', '样品台倾斜角（镜像到倾斜关节与T旋钮）', 'evaluator',
     '绕+X；正值使样品表面法线转向门（−Y）。')
slot('stage.rotation_rad', number('rad', ROT_RANGE_RAD[0], ROT_RANGE_RAD[1]), 'rad', 'process_truth', 'stage_rot', '样品旋转角（镜像到旋转关节）', 'evaluator', '绕样品座法线，俯视逆时针为正。')
for _k, _u, _c, _m in [('truth.chamber_pressure_pa', 'Pa', 'chamber_body', '样品室真实绝对压力'), ('truth.gun_pressure_pa', 'Pa', 'electron_gun', '电子枪/镜筒真实绝对压力'),
                       ('truth.gas_load_pa_m3_s', 'Pa*m^3/s', 'chamber_body', '当前气体负荷（漏气+放气+含水样品+针阀进气）'),
                       ('truth.tmp_speed_fraction', '1', 'turbo_molecular_pump', '涡轮分子泵转速（额定的分数）'),
                       ('truth.filament_on_time_s', 's', 'electron_gun', '当前灯丝已工作时间（加热斜坡按发射线性积分）'),
                       ('truth.filament_life_s', 's', 'electron_gun', '当前灯丝的（随机，Weibull）寿命；平均300h=1.08×10⁶s'),
                       ('truth.emission_a', 'A', 'electron_gun', '真实发射电流'), ('truth.probe_current_a', 'A', 'objective_lens', '真实到达样品的探针电流'),
                       ('truth.probe_diameter_m', 'm', 'objective_lens', '当前条件下的探针直径（最优孔径角）'),
                       ('truth.working_distance_m', 'm', 'specimen_holder', '极靴到束斑处样品表面的真实工作距离'),
                       ('truth.defocus_m', 'm', 'objective_lens', '聚焦WD与真实WD之差'), ('truth.astigmatism_m', 'm', 'scan_coil_section', '像散造成的等效模糊'),
                       ('truth.specimen_charge_c', 'C', 'specimen_holder', '绝缘样品表面累积的电荷')]:
    slot(_k, number(_u), _u, 'process_truth', _c, _m, 'evaluator', ABS if _u == 'Pa' else None)
slot('truth.specimens', {'type': 'object', 'additionalProperties': {'type': 'object'}}, '1', 'process_truth', 'specimen_holder', '各样品的真实组成、形貌、镀膜与位置', 'evaluator')


class _Sum:
    """Neumaier compensated sum (ledger)."""
    __slots__ = ('s', 'c')

    def __init__(self):
        self.s, self.c = 0.0, 0.0

    def add(self, x):
        t = self.s + x
        self.c += (self.s - t) + x if abs(self.s) >= abs(x) else (x - t) + self.s
        self.s = t

    @property
    def value(self):
        return self.s + self.c


LEDGER_FLOWS = ['beam_charge_c', 'backscattered_charge_c', 'secondary_charge_c', 'absorbed_charge_c', 'beam_energy_j', 'gas_admitted_mol', 'gas_pumped_mol',
                'xray_generated', 'xray_detected', 'filament_on_time_s', 'electrons_on_specimen']
COATINGS = {'carbon': {'C': 1.0}, 'gold': {'Au': 1.0}, 'platinum': {'Pt': 1.0}}
COATING_RHO = {'carbon': 2.0, 'gold': 19.3, 'platinum': 21.45}
HOLDER_SUBSTANCE = 'aluminium'


def quantize_kv(v):
    step = KV_FINE_STEP_V if v <= KV_FINE_MAX_V + 1e-9 else KV_COARSE_STEP_V
    return round(round(v / step) * step, 6)


class JeolJsmit200Runtime:
    """JSM-IT200 LA with the JED-2300 EDS: vacuum, gun, optics, signals, EDS, stage, door and specimen handling (SI units)."""

    def __init__(self, instance_id='jsmit200_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.rng = random.Random(0)
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.shutdown_done = True
        self.error = None
        # vacuum (delivered vented, door closed)
        self.vac_mode = 'HV'
        self.vac_state = 'vented'
        self.p_ch = P_ATM
        self.tmp_on = False
        self.tmp_speed = 0.0
        self.pump_t0 = None
        self.evac_t0 = None
        self.evac_timeout_raised = False
        self.lv_set = 50.0
        self.lv_valve = 0.0
        self.lv_int = 0.0
        self.lv_ok_s = 0.0
        self.outgas_q0 = OUTGAS_AIR_PA_M3_S
        self.readings = None
        # door and stage (reference pose)
        self.door = 0.0
        self.door_target = 0.0
        self.stage = dict(x=0.0, y=0.0, z=0.0, tilt=0.0, rot=0.0)
        self.stage_target = dict(self.stage)
        # gun and optics
        self.kv = 15000.0
        self.ip_set = 1e-10
        self.ht = False
        self.heat = 0.0
        self.fil_serial = 1
        self.fil_life_s = FILAMENT_LIFE_S
        self.fil_used_s = 0.0
        self.fil_burned = False
        self.mag = 100.0
        self.focus_wd = WD_RANGE_M[1]
        self.astig = 30e-9
        self.astig_ok = False
        self.acb = None
        self.detector = 'SED'
        self.v_surface = {}           # negative surface potential (V) of each charging phase of the loaded insulator (phase index -> V)
        # specimens, records, operations
        self.specimens = {}
        self.loaded = None
        self.images, self.spectra, self.quants = {}, {}, {}
        self.img_seq = self.eds_seq = self.op_seq = 0
        self.acq = None
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.used_substances = set()
        self.led = {k: _Sum() for k in LEDGER_FLOWS}
        self._n0 = None
        self._principals, self._requests, self._owners = {}, {}, {}
        self._last = {}

    # ---------------------------------------------------------------- basics
    @property
    def time_s(self):
        return self.time_ns / 1e9

    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator') or name in self._principals:
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return JeolJsmit200Session(self, name)

    def _event(self, kind, code, message, rid=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.time_s, kind=kind, code=code, message_zh=message, _rid=rid))

    def _room_k(self):
        return self.plant['room_k'] if self.plant else 293.15

    def _gas_mol(self):
        return self.p_ch * V_SYSTEM_M3 / (GAS_CONSTANT * self._room_k())

    # ---------------------------------------------------------------- derived quantities
    def _door_state(self):
        if self.door <= 1e-9:
            return 'closed'
        if self.door >= DOOR_TRAVEL_M - 1e-9:
            return 'open'
        return 'moving'

    def _vac_ready(self):
        return self.vac_state in ('hv_ready', 'lv_ready')

    def _p_gun(self):
        if self.vac_mode == 'LV' and self.vac_state in ('lv_pumping', 'lv_ready'):
            return GUN_BASE_PA + self.p_ch * PLA_CONDUCTANCE_M3_S / (TMP_SPEED_M3_S * max(self.tmp_speed, 0.05))
        return self.p_ch

    def _beam_ready(self):
        return self.ht and self.heat >= 1.0 - 1e-9 and not self.fil_burned

    def _probe_current(self):
        """Current reaching the specimen: the set point, limited by the emission while the filament heats up."""
        if not self.ht or self.fil_burned:
            return 0.0
        return self.ip_set * min(1.0, self.heat) ** 2

    def _spec(self):
        return self.specimens.get(self.loaded) if self.loaded else None

    def _spec_height(self):
        s = self._spec()
        return s['height_m'] if s else 0.0

    def _spec_radius(self):
        s = self._spec()
        return s['diameter_m'] / 2 if s else HOLDER_RADIUS_M

    def _top_z_rel(self, z, tilt, y=0.0):
        """Highest point of the specimen (or holder) above the WD-48 plane (m) for lift z, tilt and Y carriage offset: the Y carriage
        moves along the tilted cradle (vertical component y sin t), the top rim of a disc of height h and radius r reaches
        h cos t + r |sin t| above its centre."""
        h, r = self._spec_height(), max(self._spec_radius(), HOLDER_RADIUS_M)
        return z + y * math.sin(tilt) + h * math.cos(tilt) + r * abs(math.sin(tilt))

    def _path_collision(self, z_to, tilt_to, y_to=None, strict=False):
        """The Z and T hand motions and the Y motor run independently towards their targets: the highest rim point over the whole swept
        box between the present position and the targets (max over Z, the tilt interval incl. the rim maximum at atan(r/h), and both Y
        ends) must stay 4 mm below the pole piece.  A motion that does not lift the rim above its present height is always allowed (the
        remedy from a too-high pose, e.g. after a taller specimen was mounted), and with the door fully drawn out there is no pole piece
        above the stage; strict (drawing the door in) requires the whole box below the limit."""
        if not strict and self._stage_outside():
            return False
        y_to = self.stage['y'] if y_to is None else y_to
        lo, hi = sorted((self.stage['tilt'], tilt_to))
        h, r = self._spec_height(), max(self._spec_radius(), HOLDER_RADIUS_M)
        phi = math.atan2(r, max(h, 1e-12))
        tilts = [lo + (hi - lo) * i / 90.0 for i in range(91)] + [t for t in (phi, -phi, 0.0) if lo < t < hi]
        z_max = max(self.stage['z'], z_to)
        top = max(self._top_z_rel(z_max, t, y) for t in tilts for y in (self.stage['y'], y_to))
        limit = WD_RANGE_M[1] - POLE_CLEARANCE_M + 1e-12
        if strict:
            return top > limit
        now = self._top_z_rel(self.stage['z'], self.stage['tilt'], self.stage['y'])
        return top > limit and top > now + 1e-12

    def _stage_outside(self):
        """The stage is out of the chamber only while the door is fully drawn out and not commanded back in."""
        return self.door >= DOOR_TRAVEL_M - 1e-9 and self.door_target >= DOOR_TRAVEL_M - 1e-9

    def _hit(self):
        """Where the vertical beam meets the specimen: (u, v, on, t_m).  The tilt axis (X) passes through the holder top on the column
        axis; the tilted top face of a specimen of height h is met h/cos t above that point, h tan t along the tilted surface from the
        face centre (shifted by the X / Y carriages and turned by R).  When that point lies outside the disc (or the stage is tilted
        so far that the top face is edge-on) the beam passes the specimen and lands on the holder plane (t = 0); the specimen side wall
        is not modelled."""
        x, y, r, th = self.stage['x'], self.stage['y'], self.stage['rot'], self.stage['tilt']
        spec = self._spec()
        h = spec['height_m'] if spec else 0.0
        c_t = math.cos(th)
        d, t = (h * math.tan(th), h / c_t) if c_t > 1e-6 else (math.inf, math.inf)
        u, v = -x, (d - y) if math.isfinite(d) else math.inf
        on = spec is not None and math.isfinite(v)
        if on:
            c, s = math.cos(-r), math.sin(-r)
            u, v = c * u - s * v, s * u + c * v
            on = math.hypot(u, v) <= spec['diameter_m'] / 2
        if not on:
            u, v = -x, -y
            c, s = math.cos(-r), math.sin(-r)
            u, v = c * u - s * v, s * u + c * v
            t = 0.0
        return u, v, on, t

    def _wd_true(self):
        """Distance from the pole-piece face to the point the beam lands on (top face of the specimen or the holder plane)."""
        return WD_RANGE_M[1] - self.stage['z'] - self._hit()[3]

    def _beam_point(self):
        """Beam position in specimen coordinates (m) and whether it lands on the specimen top face."""
        u, v, on, _ = self._hit()
        return u, v, on

    def _phase_sub(self, spec, u, v):
        if spec is None:
            return substance(HOLDER_SUBSTANCE), 0.0, 0.0, None
        k = phase_at(spec, u, v)
        th, az = local_tilt(spec, u, v)
        return substance(spec['phases'][k]['substance_id']), th, az, k

    def _charging_phase(self, sub, spec):
        if spec is None or spec['coating'] != 'none' or self.vac_mode == 'LV':
            return False
        return sub['electrical_class'] not in ('conductor', 'semiconductor')

    def _landing_ev(self, sub, spec, k=None):
        """Landing energy (eV) of the beam on phase `k`: E0 minus the negative surface potential that phase has built up (uncoated
        insulators in high vacuum; see _beam_step)."""
        if spec is None or k is None or not self._charging_phase(sub, spec):
            return self.kv
        return max(0.0, self.kv - self.v_surface.get(k, 0.0))

    def _v_equilibrium(self, sub, theta):
        """Surface potential (V) at which the total yield of the phase is 1 at this incidence (landing energy = E2(theta)); 0 when the
        yield stays above 1 up to E0."""
        e2 = second_crossover_kev(sub, theta)
        if e2 is None:
            return max(0.0, self.kv - 100.0)                   # total yield never above 1: charges until the beam is nearly stopped
        return max(0.0, self.kv - e2 * 1000.0)

    # ---------------------------------------------------------------- read
    def _lamp(self, which):
        if not self.power:
            return 'off'
        vs = self.vac_state
        if which == 'vent':
            return 'on' if vs == 'vented' else 'blinking' if vs == 'venting' else 'off'
        if which == 'evac':
            return 'on' if vs in ('hv_ready', 'lv_ready') else 'blinking' if vs in ('roughing', 'hv_pumping', 'lv_pumping') else 'off'
        return 'on' if vs == 'lv_ready' else 'blinking' if vs == 'lv_pumping' else 'off'

    def _lit(self, mode):
        return mode == 'on' or (mode == 'blinking' and int(math.floor(self.time_s * 2.0)) % 2 == 0)

    def _filament_state(self):
        if self.fil_burned:
            return 'burned_out'
        if not self.ht:
            return 'off'
        return 'saturated' if self.heat >= 1.0 - 1e-9 else 'heating'

    def read(self, visibility='agent'):
        m = self.readings if self.power else None
        acq = self.acq
        lamp = {w: self._lamp(w) for w in ('vent', 'evac', 'lv')}
        dp = probe(self.kv, max(self._probe_current(), 1e-13), max(self.focus_wd, 1e-3))
        values = {
            'clock.time_s': self.time_s, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.error_code': self.error, 'vacuum.mode': self.vac_mode, 'vacuum.state': self.vac_state, 'vacuum.lv_setpoint_pa': self.lv_set,
            'vacuum.chamber_pressure_indicated_pa': m['pirani'] if m else None, 'vacuum.gun_pressure_indicated_pa': m['penning'] if m else None,
            'vacuum.lv_pressure_indicated_pa': m['lv'] if m else None,
            'vacuum.evacuation_elapsed_s': (self.time_s - self.evac_t0) if self.evac_t0 is not None else None,
            'panel.vent_lamp': self._lit(lamp['vent']), 'panel.vent_lamp_mode': lamp['vent'], 'panel.evac_lamp': self._lit(lamp['evac']),
            'panel.evac_lamp_mode': lamp['evac'], 'panel.lv_lamp': self._lit(lamp['lv']), 'panel.lv_lamp_mode': lamp['lv'],
            'chamber.door_state': self._door_state(), 'specimen.loaded_id': self.loaded,
            'specimen.bench_ids': sorted(k for k, s in self.specimens.items() if s['location'] == 'bench'),
            'stage.x_indicated_m': round(self.stage['x'], 7), 'stage.y_indicated_m': round(self.stage['y'], 7),
            'stage.z_indicated_m': round(self.stage['z'], 4), 'stage.tilt_indicated_rad': round(self.stage['tilt'] / math.radians(0.5)) * math.radians(0.5),
            'stage.rotation_indicated_rad': round(self.stage['rot'] / math.radians(1.0)) * math.radians(1.0),
            'stage.moving': any(abs(self.stage[k] - self.stage_target[k]) > 1e-12 for k in self.stage),
            'beam.accelerating_voltage_v': self.kv, 'beam.probe_current_setpoint_a': self.ip_set, 'beam.ht_on': self.ht, 'beam.filament_state': self._filament_state(),
            'beam.emission_indicated_a': m['emission'] if m else None, 'scan.magnification': self.mag, 'scan.field_width_m': PRINT_WIDTH_M / self.mag,
            'optics.focus_wd_m': self.focus_wd, 'optics.astigmatism_corrected': self.astig_ok, 'optics.acb_done': self.acb is not None,
            'detector.selected': self.detector, 'acquisition.id': acq['record_id'] if acq else None, 'acquisition.kind': acq['kind'] if acq else None,
            'acquisition.progress': min(1.0, (self.time_s - acq['t0']) / acq['duration']) if acq else None, 'acquisition.started_by_other_agent': False,
            'result.last_image_id': None, 'result.last_spectrum_id': None,
            'chamber.door_position_m': self.door, 'stage.x_m': self.stage['x'], 'stage.y_m': self.stage['y'], 'stage.z_lift_m': self.stage['z'],
            'stage.tilt_rad': self.stage['tilt'], 'stage.rotation_rad': self.stage['rot'],
            'truth.chamber_pressure_pa': self.p_ch, 'truth.gun_pressure_pa': self._p_gun(), 'truth.gas_load_pa_m3_s': self._gas_load_now(),
            'truth.tmp_speed_fraction': self.tmp_speed, 'truth.filament_on_time_s': self.fil_used_s, 'truth.filament_life_s': self.fil_life_s,
            'truth.emission_a': EMISSION_SAT_A * self.heat if (self.ht and not self.fil_burned) else 0.0, 'truth.probe_current_a': self._probe_current(),
            'truth.probe_diameter_m': dp[0], 'truth.working_distance_m': self._wd_true(), 'truth.defocus_m': self.focus_wd - self._wd_true(),
            'truth.astigmatism_m': self.astig, 'truth.specimen_charge_c': SURFACE_CAPACITANCE_F * sum(self.v_surface.values()),
            'truth.specimens': {k: dict(location=s['location'], phases=copy.deepcopy(s['phases']), diameter_m=s['diameter_m'], height_m=s['height_m'],
                                        topography=s['topography'], coating=s['coating'], coating_thickness_m=s['coating_thickness_m'], wet=s['wet'])
                                for k, s in self.specimens.items()},
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    # ---------------------------------------------------------------- vacuum model
    def _outgas(self):
        if self.pump_t0 is None:
            return 0.0
        t = max(0.0, self.time_s - self.pump_t0)
        q = self.outgas_q0 * OUTGAS_TC_S / (OUTGAS_TC_S + t)
        if self._wet_exposed():
            q += WET_OUTGAS_PA_M3_S * math.exp(-self._spec()['_wet_exposed_s'] / WET_TAU_S)
        return q

    def _wet_exposed(self):
        """A wet specimen releases water (and dries) only while it is inside the closed chamber and the chamber is not being vented or at
        atmosphere: drawn out, the stage and specimen sit in room air; the water it has lost stays lost (venting does not re-wet it)."""
        s = self._spec()
        return s is not None and s['wet'] and self.door <= 1e-9 and self.pump_t0 is not None and self.vac_state not in ('vented', 'venting')

    def _vac_coeffs(self):
        """Linear gas balance dP/dt = (Q0 + K (P_atm - P) - S P) / V for the present valve / pump configuration."""
        q0 = LEAK_CLOSED_PA_M3_S + self._outgas()
        k = 0.0 if self.door <= 1e-9 else DOOR_GAP_CONDUCTANCE_M3_S
        vs = self.vac_state
        s = 0.0
        if vs == 'venting' or vs == 'vented':
            k += VENT_CONDUCTANCE_M3_S
        elif vs == 'roughing':
            s = RP_EFF_M3_S
            q0 += RP_EFF_M3_S * RP_ULTIMATE_PA
        elif vs in ('hv_pumping', 'hv_ready'):
            s = TMP_SPEED_M3_S * self.tmp_speed + (RP_EFF_M3_S if self.tmp_speed < 0.05 else 0.0)
        elif vs in ('lv_pumping', 'lv_ready'):
            s = LV_ORIFICE_M3_S
            k += LV_VALVE_MAX_M3_S * self.lv_valve
        return q0, k, s

    def _gas_load_now(self):
        q0, k, s = self._vac_coeffs()
        return q0 + k * (P_ATM - self.p_ch)

    def _vacuum_step(self, dt):
        q0, k, s = self._vac_coeffs()
        rt = GAS_CONSTANT * self._room_k()
        lam = (s + k) / V_SYSTEM_M3
        p0 = self.p_ch
        if lam > 0:
            pss = (q0 + k * P_ATM) / (s + k)
            e = math.exp(-lam * dt)
            p1 = pss + (p0 - pss) * e
            integ = pss * dt + (p0 - pss) * (1.0 - e) / lam
        else:
            p1 = p0 + q0 * dt / V_SYSTEM_M3
            integ = 0.5 * (p0 + p1) * dt
        self.led['gas_admitted_mol'].add((q0 * dt + k * (P_ATM * dt - integ)) / rt)
        self.led['gas_pumped_mol'].add(s * integ / rt)
        self.p_ch = max(p1, 1e-9)

    # ---------------------------------------------------------------- charge / beam bookkeeping
    def _discharge(self):
        """Release the stored surface charge (to ground through the holder when the specimen is handled, or to the gas ions in LV)."""
        stored = SURFACE_CAPACITANCE_F * sum(self.v_surface.values())
        if stored > 0.0:
            self.led['absorbed_charge_c'].add(stored)
        self.v_surface = {}

    def _beam_on_specimen(self):
        return self._beam_ready() and self.door <= 1e-9 and self._vac_ready()

    def _dose_points(self):
        """Where the electrons go: the stationary spot during an EDS point analysis, otherwise the scanned field of view (6 x 4 grid).
        Each entry: (substance, on the specimen, phase index, local slope, slope azimuth, weight)."""
        spec = self._spec()
        u0, v0, on0 = self._beam_point()
        acq = self.acq
        if acq is not None and acq['kind'] == 'eds' and acq.get('mode') == 'point':
            sub, th, az, k = self._phase_sub(spec if on0 else None, u0, v0)
            return [(sub, on0, k, th, az, 1.0)]
        fw = PRINT_WIDTH_M / self.mag
        c, s = math.cos(-self.stage['rot']), math.sin(-self.stage['rot'])
        cos_t = max(math.cos(self.stage['tilt']), 0.05)
        pts = []
        for i in range(6):
            for j in range(4):
                xs, ys = ((i + 0.5) / 6 - 0.5) * fw, ((j + 0.5) / 4 - 0.5) * 0.75 * fw / cos_t
                u, v = u0 + c * xs - s * ys, v0 + s * xs + c * ys
                on = spec is not None and math.hypot(u, v) <= spec['diameter_m'] / 2
                sub, th, az, k = self._phase_sub(spec if on else None, u, v)
                pts.append((sub, on, k, th, az, 1.0 / 24.0))
        return pts

    def _beam_step(self, dt):
        """Beam charge bookkeeping and charging over one cycle (20 sub-steps).  Each uncoated insulating phase of the loaded specimen
        carries its own negative surface potential V_k (capacitance SURFACE_CAPACITANCE_F); electrons land with E0 - V_k and the net
        deposited current I (1 - eta - delta) at the actual incidence (stage tilt with the local slope) charges V_k towards the
        potential at which the total yield is 1 (landing energy = E2 at that incidence); a phase whose total yield is above 1 at its
        landing energy discharges towards that equilibrium."""
        spec = self._spec()
        if not self._beam_on_specimen():
            if self.vac_mode == 'LV' or spec is None or spec['coating'] != 'none':
                self._discharge()
            return
        if self.vac_mode == 'LV' or spec is None or spec['coating'] != 'none':
            self._discharge()
        ip = self._probe_current()
        if ip <= 0.0:
            return
        points = self._dose_points()
        n_sub = 20
        h = dt / n_sub
        for _ in range(n_sub):
            for sub, on, k, th_l, az_l, w in points:
                comp = sub['composition_mass_fraction']
                theta = self._incidence(th_l, az_l)[0]
                charging = on and k is not None and self._charging_phase(sub, spec)
                el = self._landing_ev(sub, spec if on else None, k)
                if charging and el <= MIRROR_EV:
                    # the surface is charged beyond the beam energy (kV lowered after charging): the beam is reflected (mirror),
                    # nothing lands, the potential stays
                    q = ip * h * w
                    self.led['beam_charge_c'].add(q)
                    self.led['electrons_on_specimen'].add(q / E_CHARGE)
                    self.led['backscattered_charge_c'].add(q)
                    continue
                e_kev = el / 1000.0
                et = eta(comp, e_kev, theta)
                de = se_yield(sub, comp, e_kev, theta)
                q = ip * h * w
                self.led['beam_charge_c'].add(q)
                self.led['electrons_on_specimen'].add(q / E_CHARGE)
                self.led['beam_energy_j'].add(q * el)
                self.led['backscattered_charge_c'].add(q * et)
                self.led['secondary_charge_c'].add(q * de)
                deposited = q * (1.0 - et - de)                  # negative when more electrons leave than arrive
                stored = 0.0
                if charging:
                    v0 = self.v_surface.get(k, 0.0)
                    v_eq = self._v_equilibrium(sub, theta)
                    dv = deposited / SURFACE_CAPACITANCE_F
                    # the potential only moves in the direction of the deposited charge and stops at the equilibrium
                    v1 = min(v0 + dv, max(v_eq, v0)) if dv >= 0.0 else max(v0 + dv, min(v_eq, v0), 0.0)
                    self.v_surface[k] = v1
                    stored = (v1 - v0) * SURFACE_CAPACITANCE_F
                self.led['absorbed_charge_c'].add(deposited - stored)

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):   # sim.step counts its own revision when time advances
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

    def _need_beam(self):
        if not self._vac_ready():                               # a ready vacuum implies the draw-out door is closed
            raise ActionRejected('VACUUM_NOT_READY')
        if not self._beam_ready():
            raise ActionRejected('BEAM_NOT_READY')

    def _cmd_plant_configure(self, room_k, relative_humidity, mains_voltage_v, vent_gas, noise_seed):
        first = self.plant is None
        if not first:
            noise_seed = self.plant['noise_seed']
            self.p_ch *= room_k / self.plant['room_k']           # the enclosed gas follows the room temperature at constant volume
        self.plant = dict(room_k=room_k, relative_humidity=relative_humidity, mains_voltage_v=mains_voltage_v, vent_gas=vent_gas, noise_seed=noise_seed)
        if first:
            self.rng = random.Random(noise_seed)
            self.fil_life_s = self._draw_life()
            self.outgas_q0 = self._outgas_q0()
            self._n0 = self._gas_mol()
        if not ROOM_RANGE_K[0] <= room_k <= ROOM_RANGE_K[1]:
            self._event('warning', 'ROOM_OUT_OF_SPEC', '室温超出安装要求15–27°C')
        if relative_humidity > HUMIDITY_MAX:
            self._event('warning', 'HUMIDITY_OUT_OF_SPEC', '相对湿度超过安装要求60%（放气后水汽放气增加）')
        return dict(configured=True)

    def _draw_life(self):
        u = max(1e-12, 1.0 - self.rng.random())
        return FILAMENT_LIFE_S / math.gamma(1.0 + 1.0 / FILAMENT_WEIBULL_K) * (-math.log(u)) ** (1.0 / FILAMENT_WEIBULL_K)

    def _outgas_q0(self):
        if self.plant and self.plant['vent_gas'] == 'dry_nitrogen':
            return OUTGAS_N2_PA_M3_S
        rh = self.plant['relative_humidity'] if self.plant else 0.5
        return OUTGAS_AIR_PA_M3_S * (1.0 + 2.0 * rh)

    def _cmd_specimen_provide(self, specimen_id, phases, diameter_m, height_m, topography, coating, coating_thickness_m, wet):
        self._need_plant()
        if specimen_id in self.specimens or len(self.specimens) >= 20:
            raise ActionRejected('SPECIMEN_EXISTS')
        if any(p['substance_id'] not in _lib()['substances'] for p in phases):
            raise ActionRejected('UNKNOWN_SUBSTANCE')
        if abs(sum(p['area_fraction'] for p in phases) - 1.0) > 1e-6 or any(p['area_fraction'] <= 0.0 for p in phases):
            raise ActionRejected('AREA_FRACTIONS_INVALID')
        if (coating == 'none') != (coating_thickness_m == 0.0):
            raise ActionRejected('COATING_INVALID')
        if diameter_m > SPECIMEN_MAX_DIAMETER_M + 1e-12 or height_m > SPECIMEN_MAX_HEIGHT_M + 1e-12:
            raise ActionRejected('SPECIMEN_TOO_LARGE')
        grain = sum(p['area_fraction'] * p['grain_size_m'] for p in phases)
        seed = int(_hash01(sum(map(ord, specimen_id)) * 7919 + len(specimen_id), self.plant['noise_seed']) * 2 ** 31)
        self.specimens[specimen_id] = dict(phases=copy.deepcopy(phases), diameter_m=diameter_m, height_m=height_m, topography=topography, coating=coating,
                                           coating_thickness_m=coating_thickness_m, wet=wet, location='bench', _grain=grain, _seed=seed, _wet_exposed_s=0.0)
        self.used_substances.update(p['substance_id'] for p in phases)
        return dict(specimen_id=specimen_id, provided=True)

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            if abs(self.plant['mains_voltage_v'] - MAINS_NOMINAL_V) > MAINS_TOL * MAINS_NOMINAL_V + 1e-9:
                raise ActionRejected('MAINS_OUT_OF_RANGE')
            self.power = True
            self.state, self.state_t0 = 'booting', self.time_s
            self.shutdown_done = False
            self.error = None
            if self.vac_state == 'idle':                          # the chamber was left under vacuum: start-up pumping
                self._start_evac()
            self._event('power', 'POWER_ON', '钥匙转到START：主机上电，软件加载约60s')
        elif not enabled and self.power:
            self._power_off()
        return dict(enabled=self.power)

    def _cmd_power_key_toggle(self):
        return self._cmd_power_set(not self.power)

    def _power_off(self):
        if not self.shutdown_done:
            self._event('warning', 'UNSAFE_POWER_OFF', '未先软件关机就断电：电子枪高压与泵立即停止')
        self._stop_acq('failed', 'POWER_OFF')
        self.power = False
        self.state = 'off'
        self.ht = False
        self.tmp_on = False
        self.evac_t0 = None
        if not (self.vac_state == 'vented' and self.p_ch >= VENT_READY_PA):
            self.vac_state = 'idle'                               # valves close without power: a chamber under vacuum only leaks slowly
        self.readings = None
        self._event('power', 'POWER_OFF', '钥匙转到OFF：主机断电')

    def _cmd_system_shutdown(self):
        self._need_ready(allow_error=True)
        self._need_idle()
        self.ht = False
        self.state = 'shutdown'
        self.shutdown_done = True
        self._event('power', 'SHUTDOWN', '软件关机：高压关闭、记录已保存；可把钥匙转到OFF')
        return dict(shutdown=True)

    def _cmd_error_acknowledge(self):
        self._need_power()
        if self.error is None:
            raise ActionRejected('NO_ERROR')
        code, self.error = self.error, None
        return dict(acknowledged=code)

    def _start_evac(self):
        self.vac_state = 'roughing'
        self.tmp_on = True
        self.evac_t0 = self.time_s
        self.evac_timeout_raised = False
        if self.pump_t0 is None:
            self.pump_t0 = self.time_s

    def _vent_complete(self):
        """The chamber has reached atmospheric pressure through the vent valve: the only way to the 'vented' state."""
        self.vac_state = 'vented'
        self.pump_t0 = None
        self.outgas_q0 = self._outgas_q0()
        self._event('vacuum', 'VENT_COMPLETE', '样品室已放气到大气压')

    def _at_atmosphere(self):
        return self.vac_state == 'vented' and self.p_ch >= VENT_READY_PA

    def _cmd_vacuum_evacuate(self):
        self._need_ready()
        self._need_idle()
        if self.vac_state in ('vented', 'venting', 'idle'):
            self._start_evac()
            self._event('vacuum', 'EVAC', 'EVAC：放气阀关闭，机械泵粗抽，涡轮分子泵启动')
        return dict(state=self.vac_state)

    def _cmd_vacuum_vent(self):
        self._need_ready()
        self._need_idle()
        if self.ht:
            self.ht = False
            self._event('gun', 'HT_OFF', '放气前自动关闭电子枪高压')
        if self.vac_state not in ('vented', 'venting'):
            self.vac_state = 'venting'
            self.evac_t0 = None
            self.tmp_on = False
            gas = '干燥氮气' if self.plant['vent_gas'] == 'dry_nitrogen' else '室内空气'
            self.stage_target['x'] = 0.0
            if self._path_collision(self.stage_target['z'], self.stage_target['tilt'], 0.0):
                # on a tilted stage the Y return would lift the specimen rim towards the pole piece: X returns, Y stays
                self._event('vacuum', 'VENT', f'VENT：放气（{gas}），样品台X回到交换位')
                self._event('warning', 'Y_RETURN_SKIPPED', '样品台倾斜时Y回位会使样品升到极靴附近：Y未自动回位，请先降低Z或减小倾斜后用stage.move_xy回位')
            else:
                self.stage_target['y'] = 0.0
                self._event('vacuum', 'VENT', f'VENT：放气（{gas}），样品台X/Y回到交换位')
        return dict(state=self.vac_state)

    def _cmd_vacuum_toggle_lv(self):
        self._need_ready()
        self._need_idle()
        if self.ht:                                             # the mode change runs with the beam off (the column pressure changes)
            self.ht = False
            self._event('gun', 'HT_OFF', '切换真空模式：先关闭电子枪高压，达到新的真空后再开始观察')
        self.vac_mode = 'LV' if self.vac_mode == 'HV' else 'HV'
        if self.vac_mode == 'LV':
            if self.detector == 'SED':
                self.detector = 'BED_COMPO'
                self._event('warning', 'DETECTOR_SWITCHED', 'LV模式不能用SED，已切换到BED（COMPO）')
            if self.vac_state in ('hv_pumping', 'hv_ready'):
                self.vac_state = 'lv_pumping'
                self.lv_ok_s = 0.0
                self.lv_int = 0.0
                self.evac_t0 = self.time_s
        else:
            if self.vac_state in ('lv_pumping', 'lv_ready'):
                self.vac_state = 'hv_pumping'
                self.lv_valve = 0.0
                self.evac_t0 = self.time_s
        self.acb = None
        self._event('vacuum', 'MODE_' + self.vac_mode, '真空模式切换到' + ('低真空' if self.vac_mode == 'LV' else '高真空'))
        return dict(mode=self.vac_mode)

    def _cmd_vacuum_set_lv_pressure(self, pressure_pa):
        self._need_power()
        self.lv_set = float(pressure_pa)
        return dict(pressure_pa=self.lv_set)

    def _cmd_chamber_draw_out(self):
        self._need_plant()
        self._need_idle()
        if not self._at_atmosphere():
            raise ActionRejected('CHAMBER_UNDER_VACUUM')
        self.door_target = DOOR_TRAVEL_M
        return dict(target_m=self.door_target)

    def _cmd_chamber_draw_in(self):
        self._need_plant()
        self._need_idle()
        if self._path_collision(self.stage_target['z'], self.stage_target['tilt'], self.stage_target['y'], strict=True):
            raise ActionRejected('STAGE_COLLISION_RISK')
        self.door_target = 0.0
        return dict(target_m=self.door_target)

    def _cmd_specimen_load(self, specimen_id):
        self._need_plant()
        if not self._stage_outside():                          # fully drawn out and not being pushed back in
            raise ActionRejected('DOOR_NOT_OPEN')
        if specimen_id not in self.specimens:
            raise ActionRejected('UNKNOWN_SPECIMEN')
        if self.specimens[specimen_id]['location'] != 'bench':
            raise ActionRejected('SPECIMEN_NOT_ON_BENCH')
        if self.loaded is not None:
            raise ActionRejected('HOLDER_OCCUPIED')
        self.specimens[specimen_id]['location'] = 'holder'
        self.loaded = specimen_id
        self._discharge()
        self.acb = None
        self._event('specimen', 'LOAD', f'样品{specimen_id}装到样品座')
        return dict(specimen_id=specimen_id, loaded=True)

    def _cmd_specimen_unload(self):
        self._need_plant()
        if not self._stage_outside():
            raise ActionRejected('DOOR_NOT_OPEN')
        if self.loaded is None:
            raise ActionRejected('NO_SPECIMEN')
        sid, self.loaded = self.loaded, None
        self.specimens[sid]['location'] = 'bench'
        self._discharge()
        self.acb = None
        self._event('specimen', 'UNLOAD', f'样品{sid}取下')
        return dict(specimen_id=sid)

    def _cmd_stage_move_xy(self, x_m, y_m):
        self._need_ready()
        self._need_idle()
        if self.door > 1e-9:
            raise ActionRejected('DOOR_OPEN')
        if self._path_collision(self.stage_target['z'], self.stage_target['tilt'], float(y_m)):
            raise ActionRejected('STAGE_COLLISION_RISK')
        self.stage_target['x'], self.stage_target['y'] = float(x_m), float(y_m)
        return dict(x_m=self.stage_target['x'], y_m=self.stage_target['y'])

    def _cmd_stage_set_z(self, z_lift_m):
        self._need_plant()
        self._need_idle()
        if self._path_collision(z_lift_m, self.stage_target['tilt'], self.stage_target['y']):
            raise ActionRejected('STAGE_COLLISION_RISK')
        self.stage_target['z'] = float(z_lift_m)
        return dict(z_lift_m=self.stage_target['z'])

    def _cmd_stage_set_tilt(self, tilt_rad):
        self._need_plant()
        self._need_idle()
        if self._path_collision(self.stage_target['z'], tilt_rad, self.stage_target['y']):
            raise ActionRejected('STAGE_COLLISION_RISK')
        self.stage_target['tilt'] = float(tilt_rad)
        self.acb = None
        return dict(tilt_rad=self.stage_target['tilt'])

    def _cmd_stage_rotate(self, rotation_rad):
        self._need_plant()
        self._need_idle()
        self.stage_target['rot'] = float(rotation_rad)
        return dict(rotation_rad=self.stage_target['rot'])

    def _cmd_beam_set_voltage(self, voltage_v):
        self._need_ready()
        self._need_idle()
        v = quantize_kv(voltage_v)
        if v != self.kv:
            self.kv = v
            self.acb = None
            if self.ht:
                self.astig, self.astig_ok = self.rng.uniform(10e-9, 40e-9), False
        return dict(voltage_v=self.kv)

    def _cmd_beam_set_probe_current(self, current_a):
        self._need_ready()
        self._need_idle()
        self.ip_set = float(current_a)
        self.acb = None
        return dict(current_a=self.ip_set)

    def _cmd_beam_observation_on(self):
        self._need_ready()
        if not self._vac_ready():
            raise ActionRejected('VACUUM_NOT_READY')
        if self.fil_burned:
            raise ActionRejected('FILAMENT_BURNED_OUT')
        if not self.ht:
            self.ht = True
            self._event('gun', 'HT_ON', f'开始观察：{self.kv / 1000:.1f}kV，灯丝自动加热到饱和并自动对中')
        return dict(ht_on=True)

    def _cmd_beam_observation_off(self):
        self._need_power()
        if self.ht:
            self.ht = False
            self._stop_acq('failed', 'BEAM_OFF')
            self._event('gun', 'HT_OFF', '停止观察：高压关闭')
        return dict(ht_on=False)

    def _cmd_gun_replace_filament(self):
        self._need_plant()
        if not self._at_atmosphere():
            raise ActionRejected('VENT_REQUIRED')
        self.fil_serial += 1
        self.fil_life_s = self._draw_life()
        self.fil_used_s = 0.0
        self.fil_burned = False
        self.heat = 0.0
        self.astig, self.astig_ok = self.rng.uniform(15e-9, 50e-9), False
        if self.error == 'FILAMENT_BURNOUT':
            self.error = None
        self._event('maintenance', 'FILAMENT_REPLACED', f'更换钨灯丝（第{self.fil_serial}根）')
        return dict(filament_serial=self.fil_serial)

    def _cmd_scan_set_magnification(self, magnification):
        self._need_ready()
        self._need_idle()
        self.mag = float(magnification)
        return dict(magnification=self.mag, field_width_m=PRINT_WIDTH_M / self.mag)

    def _cmd_optics_autofocus(self):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        wd = self._wd_true()
        d, a = probe(self.kv, max(self._probe_current(), 1e-13), max(wd, 1e-3))
        dof = max(d, PRINT_WIDTH_M / self.mag / 1000.0) / a
        self.focus_wd = min(0.060, max(0.001, wd + self.rng.gauss(0.0, dof / 8.0)))
        return dict(focus_wd_m=self.focus_wd)

    def _cmd_optics_set_focus(self, focus_wd_m):
        self._need_ready()
        self._need_idle()
        self.focus_wd = float(focus_wd_m)
        return dict(focus_wd_m=self.focus_wd)

    def _cmd_optics_auto_stigmate(self):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        self.astig, self.astig_ok = 1e-9, True
        return dict(astigmatism_m=self.astig)

    def _cmd_optics_auto_contrast_brightness(self):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        # the grey range spans the histogram of the live frame (640 x 480, 10 us): the noise-free signal range widened by +-2.5 sigma of the
        # pixel noise (about the 0.6 % / 99.4 % tails), so a frame without signal contrast (flat specimen in TOPO) is stretched to its
        # noise, never to an unbounded gain
        fr = self._frame_signals(dwell=1e-5, nx=640, ny=480, noise=False)
        sig = fr['pixel_signal']
        sigma = {r['key']: r['sigma'] for r in fr['regions']}
        s_px = np.array([[sigma.get(fr['keys'][j, i], 0.0) for i in range(THUMB_W)] for j in range(THUMB_H)])
        lo, hi = float(np.min(sig - 2.5 * s_px)), float(np.max(sig + 2.5 * s_px))
        if hi - lo < 1e-9 * max(abs(hi), abs(lo), 1e-30):
            lo, hi = 0.0, 2.0 * max(abs(hi), 1e-30)
        span = hi - lo
        lo -= 0.05 * span; hi += 0.05 * span
        gain = 215.0 / (hi - lo)
        self.acb = dict(gain=gain, offset=20.0 - gain * lo, detector=self.detector)
        return dict(gain=gain, offset=self.acb['offset'])

    def _cmd_detector_select(self, detector):
        self._need_ready()
        self._need_idle()
        if detector == 'SED' and self.vac_mode == 'LV':
            raise ActionRejected('DETECTOR_NOT_AVAILABLE')
        if detector != self.detector:
            self.detector = detector
            self.acb = None
        return dict(detector=self.detector)

    def _new_acq(self, kind, duration, **data):
        self.op_seq += 1
        if kind == 'image':
            self.img_seq += 1
            rid = f'img-{self.img_seq}'
        else:
            self.eds_seq += 1
            rid = f'eds-{self.eds_seq}'
        op = dict(operation_id=f'{kind}-op-{self.op_seq}', kind=kind, record_id=rid, phase='running', t0=self.time_s, duration=duration, **data)
        self.operations[op['operation_id']] = op
        self.acq = op
        return op

    def _cmd_image_acquire(self, pixels, dwell_s):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        if len(self.images) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        nx, ny = PIXEL_FORMATS[pixels]
        op = self._new_acq('image', nx * ny * dwell_s, pixels=pixels, dwell_s=dwell_s)
        self.images[op['record_id']] = dict(image_id=op['record_id'], status='running', detector=self.detector, accelerating_voltage_v=self.kv,
                                            probe_current_a=self.ip_set, magnification=self.mag, pixels=pixels, dwell_s=dwell_s, frame_time_s=op['duration'],
                                            started_s=self.time_s, finished_s=None, error_code=None, _op=op['operation_id'])
        self._event('acquisition', 'IMAGE_START', f'开始采集图像{op["record_id"]}（{pixels}，帧时间{op["duration"]:.1f}s）', op['record_id'])
        return dict(operation_id=op['operation_id'])

    def _cmd_image_result(self, image_id):
        rec = self.images.get(image_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._image_out(rec)

    def _cmd_eds_acquire(self, mode, live_time_s, process_time):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        if len(self.spectra) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        pre = self._eds_expected(mode, process_time)
        if pre['live_fraction'] < 1.0 - EDS_MAX_DEAD_TIME:
            raise ActionRejected('COUNT_RATE_TOO_HIGH')
        real = live_time_s / pre['live_fraction']
        op = self._new_acq('eds', real, mode=mode, live_time_s=live_time_s, process_time=process_time)
        self.spectra[op['record_id']] = dict(spectrum_id=op['record_id'], status='running', mode=mode, accelerating_voltage_v=self.kv, probe_current_a=self.ip_set,
                                             live_time_s=live_time_s, process_time=process_time, started_s=self.time_s, finished_s=None, error_code=None,
                                             _op=op['operation_id'])
        self._event('acquisition', 'EDS_START', f'开始EDS采谱{op["record_id"]}（{mode}，活时间{live_time_s:.0f}s，预计实时间{real:.1f}s）', op['record_id'])
        return dict(operation_id=op['operation_id'])

    def _cmd_eds_result(self, spectrum_id):
        rec = self.spectra.get(spectrum_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._spectrum_out(rec)

    def _cmd_eds_quantify(self, spectrum_id, elements):
        rec = self.spectra.get(spectrum_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if rec['status'] != 'completed':
            raise ActionRejected('NOT_COMPLETED')
        if any(e not in _lib()['elements'] or element(e)['Z'] < EDS_Z_RANGE[0] for e in elements):
            raise ActionRejected('UNKNOWN_ELEMENT')
        noise = EDS_PROCESS_TIMES[rec['process_time']]['noise_ev']
        counts = np.asarray(rec['counts'], float)
        found = list(dict.fromkeys(elements)) if elements else identify_elements(counts, rec['accelerating_voltage_v'], noise)
        if not found:
            raise ActionRejected('NO_PEAKS')
        res = quantify(counts, rec['accelerating_voltage_v'], rec['takeoff_angle_rad'], noise, found)
        out = dict(spectrum_id=spectrum_id, method='standardless ZAF (Green-Cosslett generation, Philibert absorption, family areas by weighted least squares, '
                                                   'normalised to 100 wt%)',
                   elements_identified=list(found), results=res, total_before_normalisation=None)
        self.quants[spectrum_id] = copy.deepcopy(out)
        return out

    def _cmd_records_list(self):
        return dict(images=sorted(self.images, key=lambda k: int(k.split('-')[1])), spectra=sorted(self.spectra, key=lambda k: int(k.split('-')[1])))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        return dict(library=LIBRARY_REL, substances=sorted(self.used_substances))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.time_s)

    def ledger(self):
        led = {k: v.value for k, v in self.led.items()}
        stored = SURFACE_CAPACITANCE_F * sum(self.v_surface.values())
        led['stored_charge_c'] = stored
        led['charge_residual_c'] = led['beam_charge_c'] - led['backscattered_charge_c'] - led['secondary_charge_c'] - led['absorbed_charge_c'] - stored
        n = self._gas_mol()
        led['gas_in_volume_mol'] = n
        led['gas_residual_mol'] = (n - (self._n0 if self._n0 is not None else n)) - (led['gas_admitted_mol'] - led['gas_pumped_mol'])
        return led

    # ---------------------------------------------------------------- image formation
    def _incidence(self, th_l, az_l):
        """Incidence angle between the beam and the local surface normal (stage tilt about X combined with the local slope) and the
        x-slope seen by the two BED halves."""
        nx = math.sin(th_l) * math.cos(az_l)
        ny = math.sin(th_l) * math.sin(az_l)
        nz = math.cos(th_l)
        t = self.stage['tilt']
        nz2 = ny * math.sin(t) + nz * math.cos(t)
        return math.acos(max(-1.0, min(1.0, nz2))), nx

    def _yield(self, sub, spec, on, th_l, az_l, k=None):
        """Detector output per incident electron for one surface element (SED: collected electrons; BED: e-h pairs), the BSE count per
        electron (for noise) and the charging flag."""
        comp = sub['composition_mass_fraction']
        el = self._landing_ev(sub, spec if on else None, k if on else None)
        theta, slope_x = self._incidence(th_l, az_l)
        charging = on and self._charging_phase(sub, spec) and el < self.kv - 1e-6
        if charging and el <= MIRROR_EV:                       # mirror: the reflected beam returns up the axis; only SE3 from the chamber
            return (SE3_FRACTION, 0.0, 1.0, True) if self.detector == 'SED' else (0.0, 0.0, 1.0, True)
        e_kev = el / 1000.0
        if self.detector == 'SED':
            y = SE_COLLECTION * se_yield(sub, comp, e_kev, theta) + SE3_FRACTION * eta(comp, e_kev, theta)
            if charging:
                y *= 1.0 + 3.0 * (1.0 - el / self.kv)
            return y, 0.0, 1.0, charging
        h = max(self._wd_true() - BED_DROP_M, 1e-4)
        f_col = math.sin(math.atan(BED_R_OUT_M / h)) ** 2 - math.sin(math.atan(BED_R_IN_M / h)) ** 2
        et = eta(comp, e_kev, theta)
        k_e = 0.6 + 0.25 * min(mean_z(comp), 80.0) / 80.0
        g = max(0.0, k_e * el - BED_DEAD_LAYER_EV) / PAIR_EV
        n_bse = et * f_col
        if self.detector == 'BED_COMPO':
            return n_bse * g, n_bse, g, charging
        if self.detector == 'BED_TOPO':
            return n_bse * g * slope_x, n_bse, g, charging
        return 0.5 * n_bse * g * (1.0 + slope_x), n_bse, g, charging

    def _frame_signals(self, dwell, nx, ny, noise=True):
        spec = self._spec()
        fw = PRINT_WIDTH_M / self.mag
        fh = 0.75 * fw
        u0, v0, _ = self._beam_point()
        c, s = math.cos(-self.stage['rot']), math.sin(-self.stage['rot'])
        cos_t = max(math.cos(self.stage['tilt']), 0.05)
        ip = self._probe_current()
        rate = ip / E_CHARGE
        # LV skirt: only the unscattered fraction forms the probe, the rest spreads over the field of view
        f_u = 1.0
        if self.vac_mode == 'LV':
            n_gas = self.p_ch / (K_BOLTZMANN * self._room_k())
            f_u = math.exp(-GAS_SIGMA30_M2 * (30000.0 / self.kv) ** 0.8 * n_gas * max(self._wd_true(), 1e-4))
        sig = np.zeros((THUMB_H, THUMB_W))
        keys = np.empty((THUMB_H, THUMB_W), dtype=object)
        pure = np.zeros((THUMB_H, THUMB_W), dtype=bool)           # all four sub-samples of the thumbnail pixel on the same phase
        stats = {}
        for j in range(THUMB_H):
            for i in range(THUMB_W):
                acc = 0.0
                first = None
                same = True
                for di, dj in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
                    xs = ((i + di) / THUMB_W - 0.5) * fw
                    ys = (0.5 - (j + dj) / THUMB_H) * fh / cos_t
                    u, v = u0 + c * xs - s * ys, v0 + s * xs + c * ys
                    on = spec is not None and math.hypot(u, v) <= spec['diameter_m'] / 2
                    sub, th_l, az_l, k = self._phase_sub(spec if on else None, u, v)
                    key = k if on else 'holder'
                    y, n_bse, g, charging = self._yield(sub, spec, on, th_l, az_l, k)
                    acc += y / 4.0
                    st = stats.setdefault(key, dict(sub=sub, n=0, y=0.0, n_bse=0.0, g=g, charging=False))
                    st['n'] += 1; st['y'] += y; st['n_bse'] += n_bse; st['charging'] |= charging
                    keys[j, i] = key
                    if first is None:
                        first = key
                    elif key != first:
                        same = False
                pure[j, i] = same
                sig[j, i] = acc * rate
        mean_sig = float(sig.mean())
        sig = f_u * sig + (1.0 - f_u) * mean_sig
        # blur: probe (at the focus plane) with defocus, astigmatism and vibration against the thumbnail pixel
        wd_f = max(self.focus_wd, 1e-3)
        d_p, alpha = probe(self.kv, max(ip, 1e-13), wd_f)
        blur = math.sqrt(d_p ** 2 + (2.0 * alpha * abs(self.focus_wd - self._wd_true())) ** 2 + self.astig ** 2 + VIBRATION_M ** 2)
        k_bed = BED_RES_FACTOR if self.detector != 'SED' else 1.0
        px_full = fw / nx
        eff_res = max(K_RES * blur * k_bed, 2.0 * px_full)
        sigma_px = blur * k_bed / 2.3548 / (fw / THUMB_W)
        if sigma_px > 0.3:
            r = int(min(12, math.ceil(3 * sigma_px)))
            ker = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma_px) ** 2)
            ker /= ker.sum()
            pad = np.pad(sig, r, mode='edge')
            sig = np.apply_along_axis(lambda m: np.convolve(m, ker, mode='valid'), 1, pad)
            sig = np.apply_along_axis(lambda m: np.convolve(m, ker, mode='valid'), 0, sig)
        # per-region noise and signal-to-noise of one full-resolution pixel (Poisson + detector amplifier). The shot variance counts every
        # detected carrier: SED the collected electrons (DQE 0.8); BED the pairs of both halves, so the TOPO difference signal A - B carries
        # the noise of the sum A + B (a flat specimen still gives a noisy TOPO frame). In LV the scattered skirt (1 - f_u) adds the frame-mean
        # shot variance. sigma is in pixel_signal units (per second); SNR = signal / sigma, the TOPO signal being the sum signal.
        n_e = ip * dwell / E_CHARGE
        amp_var = 0.0 if self.detector == 'SED' else (BED_AMP_NOISE_PAIRS * math.sqrt(1e-6 / dwell)) ** 2
        n_tot = 4.0 * THUMB_W * THUMB_H
        per = {}
        for key, st in stats.items():
            y = st['y'] / st['n']
            if self.detector == 'SED':
                shot, ref = n_e * max(y, 0.0) / 0.8, y
            else:
                nb = st['n_bse'] / st['n']
                shot, ref = n_e * nb * st['g'] ** 2 * 1.25, (nb * st['g'] if self.detector == 'BED_TOPO' else y)
            per[key] = dict(y=y, shot=shot, ref=ref, f=st['n'] / n_tot)
        shot_mean = sum(q['f'] * q['shot'] for q in per.values())
        ref_mean = sum(q['f'] * q['ref'] for q in per.values())
        regions = []
        for key, st in stats.items():
            q = per[key]
            var = f_u * q['shot'] + (1.0 - f_u) * shot_mean + amp_var
            ref = f_u * q['ref'] + (1.0 - f_u) * ref_mean
            snr = n_e * abs(ref) / math.sqrt(var) if var > 0 else 0.0
            # within-phase texture: robust spread (1.4826 x median absolute deviation) of the phase's noise-free thumbnail pixels after the
            # skirt and the blur (pure pixels, all four sub-samples on the phase; mixed pixels when the phase has fewer than 8 pure ones)
            m = (keys == key)
            vals = sig[m & pure] if int(np.count_nonzero(m & pure)) >= 8 else sig[m]
            tex = 1.4826 * float(np.median(np.abs(vals - np.median(vals)))) if vals.size >= 2 else 0.0
            regions.append(dict(key=key, substance_id=st['sub']['substance_id'], fraction=q['f'], y=q['y'], snr=snr, charging=st['charging'],
                                sigma=math.sqrt(var) / dwell, texture=tex))
        return dict(pixel_signal=sig, keys=keys, regions=regions, fov=(fw, fh), px=px_full, probe_d=d_p, eff_res=eff_res, f_u=f_u, n_avg=(nx * ny) / (THUMB_W * THUMB_H))

    def _image_complete(self, rec, op):
        nx, ny = PIXEL_FORMATS[op['pixels']]
        fr = self._frame_signals(op['dwell_s'], nx, ny)
        acb = self.acb if (self.acb and self.acb['detector'] == self.detector) else dict(gain=255.0 / (0.3 * 1e-10 / E_CHARGE), offset=0.0)
        sig = fr['pixel_signal']
        sigma_px = {r['key']: r['sigma'] for r in fr['regions']}
        grey = np.zeros_like(sig)
        for j in range(THUMB_H):
            for i in range(THUMB_W):
                noisy = sig[j, i] + self.rng.gauss(0.0, sigma_px.get(fr['keys'][j, i], 0.0) / math.sqrt(fr['n_avg']))
                grey[j, i] = acb['offset'] + acb['gain'] * noisy
        thumb = np.clip(np.rint(grey), 0, 255).astype(int)
        # grey-level segmentation of the frame: regions are what the image shows (ranked by mean grey), not named phases. Two phases join
        # one region (complete linkage over all members: no chaining) when their mean greys differ by less than
        # sqrt((3 s_noise)^2 + (2 s_texture)^2) of either: s_noise is the thumbnail-pixel noise (full-resolution pixel noise over
        # sqrt(n_avg)), s_texture the spread of the phase's own pixels from its surface slopes (rough / particle specimens; two
        # equal-width distributions closer than 2 sigma give a single-peaked histogram). Identical phases always merge. The phases behind
        # the regions, their templates and yields stay evaluator truth (truth.specimens, provenance.read)
        rate = self._probe_current() / E_CHARGE
        # in LV only the unscattered fraction f_u forms the image; the scattered skirt adds the frame mean everywhere (as in the thumbnail)
        f_u = fr['f_u']
        y_mean = sum(r['fraction'] * r['y'] for r in fr['regions']) / max(sum(r['fraction'] for r in fr['regions']), 1e-30)
        gain = abs(acb['gain'])
        rows = []
        for r in fr['regions']:
            y_s = f_u * r['y'] + (1.0 - f_u) * y_mean
            g_mean = float(np.clip(acb['offset'] + acb['gain'] * y_s * rate, 0.0, 255.0))
            s_noise = gain * r['sigma'] / math.sqrt(max(fr['n_avg'], 1.0))
            s_tex = gain * r['texture']
            t = max(math.hypot(3.0 * s_noise, 2.0 * s_tex), 1.5)
            rows.append(dict(g=g_mean, t=t, f=r['fraction'], snr=r['snr'], y=y_s, charging=r['charging'], members=[]))
        rows.sort(key=lambda x: -x['g'])
        clusters = []
        for x in rows:
            c = clusters[-1] if clusters else None
            if c is not None and all(abs(x['g'] - m['g']) < max(x['t'], m['t']) for m in c['members']):
                w = c['f'] + x['f']
                for key in ('g', 'snr', 'y'):
                    c[key] = (c[key] * c['f'] + x[key] * x['f']) / w
                c['f'] = w
                c['charging'] = c['charging'] or x['charging']
                c['members'].append(dict(g=x['g'], t=x['t']))
            else:
                clusters.append(dict(x, members=[dict(g=x['g'], t=x['t'])]))
        clusters = sorted(clusters, key=lambda c: -c['f'])[:8]
        clusters.sort(key=lambda c: -c['g'])
        regions = [dict(region_id=f'region_{i + 1}', area_fraction_in_view=c['f'], mean_grey=c['g'], snr=c['snr'], charging=c['charging'])
                   for i, c in enumerate(clusters)]
        ys = [c['y'] for c in clusters]
        # contrast between the brightest and darkest region signals; not defined for the signed TOPO difference signal (None)
        if self.detector == 'BED_TOPO':
            contrast = None
        else:
            contrast = (max(ys) - min(ys)) / max(abs(max(ys)), 1e-30) if len(ys) > 1 else 0.0
        # resolution measured on the frame (edge sharpness): about 10 % uncertainty, two significant digits, never below two pixels
        eff = max(fr['eff_res'] * (1.0 + 0.1 * self.rng.gauss(0.0, 1.0)), 2.0 * fr['px'])
        rec.update(status='completed', finished_s=self.time_s, field_of_view_m=[fr['fov'][0], fr['fov'][1]], pixel_size_m=fr['px'], working_distance_m=self.focus_wd,
                   probe_diameter_m=None, effective_resolution_m=float(f'{eff:.2g}'), vacuum_mode=self.vac_mode,
                   chamber_pressure_pa=self.readings['pirani'] if self.readings else None, regions=regions, contrast=contrast,
                   charging_artifacts=any(r['charging'] for r in regions), saturated_fraction=float(np.mean((thumb <= 0) | (thumb >= 255))),
                   thumbnail=thumb.tolist(), template_source=dict(library=LIBRARY_REL, substances=[]))

    def _image_out(self, rec):
        out = {k: copy.deepcopy(v) for k, v in rec.items() if not k.startswith('_')}
        for k, v in dict(field_of_view_m=[PRINT_WIDTH_M / rec['magnification'], 0.75 * PRINT_WIDTH_M / rec['magnification']], pixel_size_m=None, working_distance_m=None,
                         probe_diameter_m=None, effective_resolution_m=None, vacuum_mode=self.vac_mode, chamber_pressure_pa=None, regions=[], contrast=None,
                         charging_artifacts=False, saturated_fraction=None, thumbnail=[], template_source=dict(library=LIBRARY_REL, substances=[])).items():
            out.setdefault(k, v)
        return out

    # ---------------------------------------------------------------- EDS
    def _eds_weights(self, mode):
        """Mixture of surface elements (phase key, substance, weight) that the electrons reach: point = X-ray excitation volume around the
        beam (half the Kanaya-Okayama range), area = the field of view; in LV the scattered skirt reaches the field of view."""
        spec = self._spec()
        u0, v0, on0 = self._beam_point()
        c, s = math.cos(-self.stage['rot']), math.sin(-self.stage['rot'])
        pts = []
        if mode == 'point':
            sub0 = self._phase_sub(spec if on0 else None, u0, v0)[0]
            r_x = 0.5 * ko_range_m(sub0['composition_mass_fraction'], sub0['density_g_cm3'], self.kv / 1000.0)
            pts = [(u0, v0, 1.0)] + [(u0 + rr * math.cos(a), v0 + rr * math.sin(a), 1.0) for rr in (0.5 * r_x, r_x) for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)]
        fw = PRINT_WIDTH_M / self.mag
        area = []
        for i in range(12):
            for j in range(9):
                xs, ys = ((i + 0.5) / 12 - 0.5) * fw, ((j + 0.5) / 9 - 0.5) * 0.75 * fw / max(math.cos(self.stage['tilt']), 0.05)
                area.append((u0 + c * xs - s * ys, v0 + s * xs + c * ys, 1.0))
        f_u = 1.0
        if self.vac_mode == 'LV':
            n_gas = self.p_ch / (K_BOLTZMANN * self._room_k())
            f_u = math.exp(-GAS_SIGMA30_M2 * (30000.0 / self.kv) ** 0.8 * n_gas * max(self._wd_true(), 1e-4))
        mix = {}

        def add(points, weight):
            for u, v, _ in points:
                on = spec is not None and math.hypot(u, v) <= spec['diameter_m'] / 2
                sub, _, _, k = self._phase_sub(spec if on else None, u, v)
                key = k if on else 'holder'
                ent = mix.setdefault(key, dict(sub=sub, on=on, w=0.0))
                ent['w'] += weight / len(points)
        if mode == 'point':
            add(pts, f_u)
            if f_u < 1.0:
                add(area, 1.0 - f_u)
        else:
            add(area, 1.0)
        return mix

    def _eds_expected(self, mode, process_time):
        spec = self._spec()
        pt = EDS_PROCESS_TIMES[process_time]
        wd = max(self._wd_true(), 1e-4)
        psi, omega = eds_geometry(wd, self.stage['tilt'])
        rates = np.zeros(EDS_CHANNELS)
        gen = 0.0
        subs = set()
        coat = None
        if spec is not None and spec['coating'] != 'none':
            coat = (COATINGS[spec['coating']], COATING_RHO[spec['coating']] * spec['coating_thickness_m'] * 100.0)
        e_beam = self.kv
        for key, ent in self._eds_weights(mode).items():
            sub = ent['sub']
            el = max(self._landing_ev(sub, spec if ent['on'] else None, key if key != 'holder' else None), 1.0)
            if key != 'holder' and ent is not None and el < e_beam:
                e_beam = min(e_beam, el)
            r, g, _ = _spectrum_cached(sub['substance_id'], round(el, 3), round(psi, 6), round(omega, 9), pt['noise_ev'],
                                       (spec['coating'], coat[1]) if (coat and ent['on']) else None)
            rates += ent['w'] * r
            gen += ent['w'] * g
            subs.add(sub['substance_id'])
        ip = self._probe_current()
        icr = float(rates.sum() * ip / E_CHARGE)
        live = math.exp(-icr * pt['tau_s'])
        return dict(rates=rates, gen=gen, icr=icr, live_fraction=max(live, 1e-6), psi=psi, omega=omega, wd=wd, subs=sorted(subs), e_landing=e_beam)

    def _eds_complete(self, rec, op):
        ex = self._eds_expected(op['mode'], op['process_time'])
        ip = self._probe_current()
        lam = ex['rates'] * ip / E_CHARGE * op['live_time_s']
        nrng = np.random.default_rng(self.rng.getrandbits(63))
        counts = nrng.poisson(np.maximum(lam, 0.0)).astype(int)
        real = op['live_time_s'] / ex['live_fraction']
        self.led['xray_generated'].add(ex['gen'] * ip / E_CHARGE * real)
        self.led['xray_detected'].add(float(counts.sum()))
        noise = EDS_PROCESS_TIMES[op['process_time']]['noise_ev']
        # the analysis software knows the displayed WD (objective focus) and the indicated tilt, not the true geometry
        tilt_ind = round(self.stage['tilt'] / math.radians(0.5)) * math.radians(0.5)
        psi_n, omega_n = eds_geometry(max(self.focus_wd, 1e-4), tilt_ind)
        rec.update(status='completed', finished_s=self.time_s, duane_hunt_limit_ev=duane_hunt_limit(counts, noise, self.kv), real_time_s=real,
                   dead_time_fraction=1.0 - ex['live_fraction'], input_count_rate_cps=ex['icr'], output_count_rate_cps=ex['icr'] * ex['live_fraction'],
                   takeoff_angle_rad=psi_n, solid_angle_sr=omega_n, working_distance_m=self.focus_wd, channel_width_ev=EDS_CHANNEL_EV, counts=counts.tolist(),
                   total_counts=int(counts.sum()), fwhm_mn_ka_ev=fwhm_ev(5898.75, noise), template_source=dict(library=LIBRARY_REL, substances=[]))

    def _spectrum_out(self, rec):
        out = {k: copy.deepcopy(v) for k, v in rec.items() if not k.startswith('_')}
        noise = EDS_PROCESS_TIMES[rec['process_time']]['noise_ev']
        for k, v in dict(duane_hunt_limit_ev=None, real_time_s=None, dead_time_fraction=None, input_count_rate_cps=None, output_count_rate_cps=None,
                         takeoff_angle_rad=None, solid_angle_sr=None, working_distance_m=None, channel_width_ev=EDS_CHANNEL_EV, counts=[], total_counts=0,
                         fwhm_mn_ka_ev=fwhm_ev(5898.75, noise), template_source=dict(library=LIBRARY_REL, substances=[])).items():
            out.setdefault(k, v)
        return out

    # ---------------------------------------------------------------- acquisitions
    def _stop_acq(self, status, code):
        op = self.acq
        if op is None:
            return
        rec = (self.images if op['kind'] == 'image' else self.spectra)[op['record_id']]
        rec.update(status=status, finished_s=self.time_s, error_code=code)
        op['phase'], op['reason'] = status, code
        self.acq = None
        self._event('acquisition', 'ACQ_' + status.upper(), f'采集{op["record_id"]}{"被取消" if status == "cancelled" else "失败"}（{code}）', op['record_id'])

    def _cancel_op(self, op):
        self._stop_acq('cancelled', 'USER_CANCEL')

    def _check_acq(self):
        op = self.acq
        if op is None:
            return
        if not self._beam_ready():
            self._stop_acq('failed', 'BEAM_OFF')
        elif not self._vac_ready():
            self._stop_acq('failed', 'VACUUM_LOST')
        elif self.time_s >= op['t0'] + op['duration'] - 1e-9:
            rec = (self.images if op['kind'] == 'image' else self.spectra)[op['record_id']]
            (self._image_complete if op['kind'] == 'image' else self._eds_complete)(rec, op)
            op['phase'] = 'completed'
            self.acq = None
            self._event('acquisition', 'ACQ_COMPLETED', f'采集{op["record_id"]}完成', op['record_id'])

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
            self._integrate((nxt - self.time_ns) / 1e9)
            self.time_ns = nxt
            if nxt % CYCLE_NS == 0:
                self._tick()
        self.revision += 1

    def _move(self, dt):
        def toward(cur, tgt, speed):
            d = speed * dt
            return min(tgt, cur + d) if cur < tgt else max(tgt, cur - d)
        self.door = toward(self.door, self.door_target, DOOR_SPEED_M_S)
        if self.power:
            self.stage['x'] = toward(self.stage['x'], self.stage_target['x'], STAGE_XY_SPEED_M_S)
            self.stage['y'] = toward(self.stage['y'], self.stage_target['y'], STAGE_XY_SPEED_M_S)
        self.stage['z'] = toward(self.stage['z'], self.stage_target['z'], STAGE_Z_SPEED_M_S)
        self.stage['tilt'] = toward(self.stage['tilt'], self.stage_target['tilt'], STAGE_TILT_SPEED)
        self.stage['rot'] = toward(self.stage['rot'], self.stage_target['rot'], STAGE_ROT_SPEED)

    def _integrate(self, dt):
        """Continuous motions between the cycles (door and stage move smoothly at any host step; exact for any chopping)."""
        self._move(dt)

    def _physics(self, dt):
        """Vacuum, pumps, filament and beam charge over one instrument cycle with the coefficients at the cycle start (the result does not
        depend on how the host chops its steps)."""
        self._vacuum_step(dt)
        if self._wet_exposed():
            self._spec()['_wet_exposed_s'] += dt
        if self.tmp_on and self.power:
            self.tmp_speed = min(1.0, self.tmp_speed + dt / TMP_SPINUP_S)
        else:
            self.tmp_speed = max(0.0, self.tmp_speed - dt / 30.0)
        self._beam_step(dt)
        if self.ht and self.power and not self.fil_burned:
            h0 = self.heat
            h1 = min(1.0, h0 + dt / SATURATION_S)
            tau = min(dt, (1.0 - h0) * SATURATION_S)
            on_s = (h0 + h1) / 2.0 * tau + (dt - tau)                  # exact integral of the linear heating ramp
            self.heat = h1
            self.fil_used_s += on_s
            self.led['filament_on_time_s'].add(on_s)
        else:
            self.heat = max(0.0, self.heat - dt / 5.0)

    def _tick(self):
        self._physics(CYCLE_S)
        if not self.power:
            self.readings = None
            return
        t = self.time_s
        if abs(self.plant['mains_voltage_v'] - MAINS_NOMINAL_V) > MAINS_TOL * MAINS_NOMINAL_V + 1e-9:
            self._power_off()
            self.error = 'MAINS_FAULT'
            self._event('error', 'MAINS_FAULT', '供电电压超出AC 100V±10%：主机断路')
            return
        if self.state == 'booting' and t - self.state_t0 >= SOFTWARE_BOOT_S - 1e-9:
            self.state = 'ready'
            self._event('power', 'SOFTWARE_READY', '软件已启动')
        vs = self.vac_state
        if vs == 'roughing' and self.p_ch < CROSSOVER_PA and self.tmp_speed >= 1.0 - 1e-9:
            self.vac_state = 'hv_pumping' if self.vac_mode == 'HV' else 'lv_pumping'
            self.lv_ok_s, self.lv_int = 0.0, 0.0
        elif vs == 'hv_pumping' and self._p_gun() < HV_READY_PA:
            self.vac_state = 'hv_ready'
            self._event('vacuum', 'EVAC_COMPLETE', f'高真空就绪（抽气{t - (self.evac_t0 or t):.0f}s）')
            self.evac_t0 = None
        elif vs == 'hv_ready' and self._p_gun() > 2.0 * HV_READY_PA:
            self.vac_state = 'hv_pumping'
            self.evac_t0 = t
        if self.vac_state in ('lv_pumping', 'lv_ready'):
            err = (self.lv_set - self.p_ch) / self.lv_set
            self.lv_int = min(1.0, max(0.0, self.lv_int + err * CYCLE_S / LV_TI_S * LV_KP * 5.0))
            self.lv_valve = min(1.0, max(0.0, LV_KP * err + self.lv_int))
            ok = abs(err) < 0.05
            self.lv_ok_s = self.lv_ok_s + CYCLE_S if ok else 0.0
            if self.vac_state == 'lv_pumping' and self.lv_ok_s >= 10.0 and self._p_gun() < HV_READY_PA:
                self.vac_state = 'lv_ready'
                self._event('vacuum', 'LV_READY', f'低真空就绪（{self.lv_set:.0f}Pa）')
                self.evac_t0 = None
            elif self.vac_state == 'lv_ready' and abs(err) > 0.2:
                self.vac_state = 'lv_pumping'
        if self.evac_t0 is not None and not self._vac_ready() and t - self.evac_t0 > EVAC_LIMIT_S and not self.evac_timeout_raised:
            self.error = 'EVAC_TIMEOUT'
            self.evac_timeout_raised = True
            self._event('error', 'EVAC_TIMEOUT', '抽气10min未完成（门未关严、漏气或含水样品）')
        if self.vac_state == 'venting' and self.p_ch >= VENT_READY_PA:
            self._vent_complete()
        if self.ht and self._p_gun() > GUN_TRIP_PA:
            self.ht = False
            self.error = 'HT_TRIP'
            self._event('error', 'HT_TRIP', '电子枪真空变差，高压跳闸')
        if self.ht and self.fil_used_s >= self.fil_life_s:
            self.fil_burned = True
            self.ht = False
            self.error = 'FILAMENT_BURNOUT'
            self._event('error', 'FILAMENT_BURNOUT', '钨灯丝烧断：放气后更换灯丝')
        r = self.rng
        lv_on = self.vac_mode == 'LV' and self.vac_state in ('lv_pumping', 'lv_ready')
        self.readings = dict(pirani=min(1.2e5, max(0.5, self.p_ch * (1.0 + r.gauss(0.0, 0.03)))),
                             penning=min(0.1, max(1e-6, self._p_gun() * (1.0 + r.gauss(0.0, 0.10)))),
                             lv=self.p_ch * (1.0 + r.gauss(0.0, 0.005)) if lv_on else None,
                             emission=EMISSION_SAT_A * self.heat * (1.0 + r.gauss(0.0, 0.01)) if (self.ht and not self.fil_burned) else None)
        self._check_acq()


@functools.lru_cache(maxsize=512)
def _spectrum_cached(substance_id, e_landing_ev, psi, omega, noise_ev, coating_key):
    sub = substance(substance_id)
    coat = (COATINGS[coating_key[0]], coating_key[1]) if coating_key else None
    rates, gen, emitted = spectrum_rates(sub['composition_mass_fraction'], e_landing_ev, psi, omega, noise_ev, coat)
    rates.setflags(write=False)
    return rates, gen, emitted


# ---------------------------------------------------------------- host session: identity filter, request binding, ownership
_CONTROL_GUARDED = {'power.set', 'power.key_toggle', 'system.shutdown', 'error.acknowledge', 'vacuum.evacuate', 'vacuum.vent', 'vacuum.toggle_lv',
                    'vacuum.set_lv_pressure', 'chamber.draw_out', 'chamber.draw_in', 'specimen.load', 'specimen.unload', 'stage.move_xy', 'stage.set_z',
                    'stage.set_tilt', 'stage.rotate', 'beam.set_voltage', 'beam.set_probe_current', 'beam.observation_on', 'beam.observation_off',
                    'gun.replace_filament', 'scan.set_magnification', 'optics.autofocus', 'optics.set_focus', 'optics.auto_stigmate',
                    'optics.auto_contrast_brightness', 'detector.select', 'image.acquire', 'eds.acquire'}
_DATA_GUARDED = {'image.result', 'eds.result', 'eds.quantify'}
ASYNC = {'image.acquire', 'eds.acquire'}


class JeolJsmit200Session:
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
        meta = copy.deepcopy(export_jeol_jsmit200_contract_metadata())
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

    def _own_last(self, table):
        done = [k for k, r in table.items() if r['status'] == 'completed' and self._host._owners.get(k) == self._principal]
        return max(done, key=lambda k: table[k]['finished_s'], default=None)

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        foreign = self._role == 'agent' and self._foreign_acq()
        values['acquisition.started_by_other_agent'] = foreign
        if foreign:
            values['acquisition.id'] = None
        values['result.last_image_id'] = self._own_last(h.images)
        values['result.last_spectrum_id'] = self._own_last(h.spectra)
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
            if capability_id in ('power.set', 'power.key_toggle') and not h.power:
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED:
            rid = arguments.get('image_id') or arguments.get('spectrum_id')
            if (rid in h.images or rid in h.spectra) and self._foreign(rid):     # unknown ids are reported by the runtime (UNKNOWN_RECORD)
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
                result = dict(images=[r for r in result['images'] if not self._foreign(r)], spectra=[r for r in result['spectra'] if not self._foreign(r)])
            if capability_id == 'events.read' and self._role == 'agent':
                result = dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in h.events
                                      if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_rid'))], dropped=h.events_dropped)
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
                h._owners[h.operations[operation]['record_id']] = self._principal
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
        rec = (h.images if op['kind'] == 'image' else h.spectra)[op['record_id']]
        return dict(operation_id=operation_id, status=op['phase'], result=h._image_out(rec) if op['kind'] == 'image' else h._spectrum_out(rec))

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
_LAMPS = ['panel.vent_lamp', 'panel.vent_lamp_mode', 'panel.evac_lamp', 'panel.evac_lamp_mode', 'panel.lv_lamp', 'panel.lv_lamp_mode']
_GAUGES = ['vacuum.chamber_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa', 'vacuum.lv_pressure_indicated_pa']
_ACQ = ['acquisition.id', 'acquisition.kind', 'acquisition.progress']
_BEAM_TRUTH = ['truth.emission_a', 'truth.probe_current_a', 'truth.probe_diameter_m']
_POWER_EFFECTS = ['power.enabled', 'system.state', 'system.error_code', 'vacuum.state', 'vacuum.evacuation_elapsed_s', 'beam.ht_on', 'beam.filament_state',
                  'beam.emission_indicated_a', 'truth.gas_load_pa_m3_s', 'truth.gun_pressure_pa'] + _BEAM_TRUTH + _LAMPS + _GAUGES + _ACQ
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.filament_life_s', 'truth.gas_load_pa_m3_s', 'truth.chamber_pressure_pa', 'truth.gun_pressure_pa'],
    'specimen.provide': ['specimen.bench_ids', 'truth.specimens'],
    'power.set': _POWER_EFFECTS, 'power.key_toggle': _POWER_EFFECTS, 'system.shutdown': ['system.state', 'beam.ht_on', 'beam.filament_state'] + _BEAM_TRUTH,
    'error.acknowledge': ['system.error_code'],
    'vacuum.evacuate': ['vacuum.state', 'vacuum.evacuation_elapsed_s', 'truth.gas_load_pa_m3_s', 'truth.gun_pressure_pa'] + _LAMPS,
    'vacuum.vent': ['vacuum.state', 'vacuum.evacuation_elapsed_s', 'beam.ht_on', 'beam.filament_state', 'stage.moving', 'truth.gas_load_pa_m3_s',
                    'truth.gun_pressure_pa'] + _BEAM_TRUTH + _LAMPS,
    'vacuum.toggle_lv': ['vacuum.mode', 'vacuum.state', 'vacuum.evacuation_elapsed_s', 'detector.selected', 'optics.acb_done', 'beam.ht_on', 'beam.filament_state',
                         'truth.gun_pressure_pa', 'truth.gas_load_pa_m3_s'] + _BEAM_TRUTH + _LAMPS + _GAUGES,
    'vacuum.set_lv_pressure': ['vacuum.lv_setpoint_pa'], 'chamber.draw_out': ['chamber.door_state'], 'chamber.draw_in': ['chamber.door_state'],
    'specimen.load': ['specimen.loaded_id', 'specimen.bench_ids', 'optics.acb_done', 'truth.specimens', 'truth.specimen_charge_c', 'truth.working_distance_m',
                      'truth.defocus_m'],
    'specimen.unload': ['specimen.loaded_id', 'specimen.bench_ids', 'optics.acb_done', 'truth.specimens', 'truth.specimen_charge_c', 'truth.working_distance_m',
                        'truth.defocus_m'],
    'stage.move_xy': ['stage.moving'], 'stage.set_z': ['stage.moving'], 'stage.set_tilt': ['stage.moving', 'optics.acb_done'], 'stage.rotate': ['stage.moving'],
    'beam.set_voltage': ['beam.accelerating_voltage_v', 'optics.acb_done', 'optics.astigmatism_corrected', 'truth.astigmatism_m', 'truth.probe_diameter_m'],
    'beam.set_probe_current': ['beam.probe_current_setpoint_a', 'optics.acb_done', 'truth.probe_current_a', 'truth.probe_diameter_m'],
    'beam.observation_on': ['beam.ht_on', 'beam.filament_state'] + _BEAM_TRUTH,
    'beam.observation_off': ['beam.ht_on', 'beam.filament_state'] + _BEAM_TRUTH + _ACQ,
    'gun.replace_filament': ['beam.filament_state', 'optics.astigmatism_corrected', 'system.error_code', 'truth.filament_on_time_s', 'truth.filament_life_s',
                             'truth.astigmatism_m'],
    'scan.set_magnification': ['scan.magnification', 'scan.field_width_m'], 'optics.autofocus': ['optics.focus_wd_m', 'truth.defocus_m', 'truth.probe_diameter_m'],
    'optics.set_focus': ['optics.focus_wd_m', 'truth.defocus_m', 'truth.probe_diameter_m'], 'optics.auto_stigmate': ['optics.astigmatism_corrected', 'truth.astigmatism_m'],
    'optics.auto_contrast_brightness': ['optics.acb_done'], 'detector.select': ['detector.selected', 'optics.acb_done'],
    'image.acquire': list(_ACQ), 'image.result': [], 'eds.acquire': list(_ACQ), 'eds.result': [], 'eds.quantify': [], 'records.list': [], 'events.read': [],
    'ledger.read': [], 'provenance.read': [], 'sim.step': ['clock.time_s'],
}
for _k, _v in EFFECTS.items():
    CAPS[_k]['effects'] = list(_v)
_TIMED = {'power.set', 'power.key_toggle', 'vacuum.evacuate', 'vacuum.vent', 'vacuum.toggle_lv', 'vacuum.set_lv_pressure', 'chamber.draw_out', 'chamber.draw_in',
          'stage.move_xy', 'stage.set_z', 'stage.set_tilt', 'stage.rotate', 'beam.observation_on', 'image.acquire', 'eds.acquire', 'sim.step'}
# _TIMED: calls that start a time-driven process of their own.  ON_STEP: what each call changes later, when the host clock advances (on_step
# effects) - the process a timed call starts and the consequences of other calls on running processes (beam off, landing energy, mains trip);
# set points never change on a step
_AX = {'x': ['stage.x_m', 'stage.x_indicated_m'], 'y': ['stage.y_m', 'stage.y_indicated_m'], 'z': ['stage.z_lift_m', 'stage.z_indicated_m'],
       'tilt': ['stage.tilt_rad', 'stage.tilt_indicated_rad'], 'rot': ['stage.rotation_rad', 'stage.rotation_indicated_rad']}
_MOTION = ['stage.moving', 'truth.working_distance_m', 'truth.defocus_m']
_P = ['truth.chamber_pressure_pa', 'truth.gun_pressure_pa', 'truth.gas_load_pa_m3_s']
_VENT_LAMPS, _EVAC_LAMPS, _LV_LAMPS = _LAMPS[0:2], _LAMPS[2:4], _LAMPS[4:6]
_HT_OFF_STEP = ['truth.filament_on_time_s', 'beam.emission_indicated_a']       # a call that switches the beam off stops the filament clock
_CHARGE = ['truth.specimen_charge_c']                                           # where, how hard and how long the beam lands changes the charging
_POWER_STEP = ['system.state', 'system.error_code', 'vacuum.state', 'vacuum.evacuation_elapsed_s', 'truth.tmp_speed_fraction', 'truth.filament_on_time_s'] \
    + _P + _LAMPS + _GAUGES + _CHARGE + _AX['x'] + _AX['y'] + _MOTION
ON_STEP = {
    'plant.configure': ['power.enabled', 'beam.ht_on', 'beam.filament_state'] + _POWER_STEP + _HT_OFF_STEP + _BEAM_TRUTH + _ACQ,
    'power.set': _POWER_STEP, 'power.key_toggle': _POWER_STEP,
    'system.shutdown': _HT_OFF_STEP + _CHARGE,
    'vacuum.evacuate': ['vacuum.state', 'vacuum.evacuation_elapsed_s', 'system.error_code', 'truth.tmp_speed_fraction'] + _P + _LAMPS + _GAUGES,
    'vacuum.vent': ['vacuum.state', 'truth.tmp_speed_fraction'] + _P + _LAMPS + _GAUGES + _HT_OFF_STEP + _AX['x'] + _AX['y'] + _MOTION + _CHARGE,
    'vacuum.toggle_lv': ['vacuum.state', 'vacuum.evacuation_elapsed_s', 'system.error_code'] + _P + _EVAC_LAMPS + _LV_LAMPS + _GAUGES + _HT_OFF_STEP + _CHARGE,
    'vacuum.set_lv_pressure': ['vacuum.state'] + _P + _EVAC_LAMPS + _LV_LAMPS + _GAUGES + _ACQ,
    'chamber.draw_out': ['chamber.door_position_m', 'chamber.door_state', 'vacuum.chamber_pressure_indicated_pa'] + _P,
    'chamber.draw_in': ['chamber.door_position_m', 'chamber.door_state', 'vacuum.state', 'vacuum.evacuation_elapsed_s', 'system.error_code'] + _P + _EVAC_LAMPS
                       + _LV_LAMPS + _GAUGES,
    'stage.move_xy': _AX['x'] + _AX['y'] + _MOTION + _CHARGE, 'stage.set_z': _AX['z'] + _MOTION, 'stage.set_tilt': _AX['tilt'] + _MOTION + _CHARGE,
    'stage.rotate': _AX['rot'] + _MOTION + _CHARGE,
    'beam.set_voltage': _CHARGE, 'beam.set_probe_current': _CHARGE, 'scan.set_magnification': _CHARGE,
    'beam.observation_on': ['beam.ht_on', 'beam.filament_state', 'beam.emission_indicated_a', 'system.error_code', 'truth.filament_on_time_s'] + _BEAM_TRUTH + _CHARGE,
    'beam.observation_off': _HT_OFF_STEP + _CHARGE,
    'image.acquire': _ACQ + ['result.last_image_id', 'acquisition.started_by_other_agent'],
    'eds.acquire': _ACQ + ['result.last_spectrum_id', 'acquisition.started_by_other_agent'] + _CHARGE,
}
ON_STEP = {k: list(dict.fromkeys(v)) for k, v in ON_STEP.items()}
ON_STEP['sim.step'] = ['clock.time_s'] + sorted(set(sum(ON_STEP.values(), [])) | set(_POWER_EFFECTS) - {'clock.time_s'})
ON_STEP_ZH = {
    'plant.configure': '重新声明的条件在下一仪器周期起作用：供电超出AC 100V±10%时主机断路（高压、泵与X/Y电机停止，采集失败，灯丝不再计时）；室温、湿度与放气气体改变之后的抽气与放气过程',
    'power.set': '开机：约60s软件加载；样品室留在真空时自动启动抽气；断电后阀门关闭，样品室缓慢漏气，涡轮泵减速，束流停止（灯丝工作时间不再累计、样品不再荷电），X/Y电机停在断电时的位置（Z/T/R为手动轴）',
    'system.shutdown': '高压关闭后灯丝工作时间停止累计、发射读数在下一周期消失，绝缘样品不再荷电',
    'vacuum.evacuate': '粗抽、涡轮泵升速与高真空（或LV）抽气，含10min超时', 'vacuum.toggle_lv': '切换后按新模式抽气或针阀调压；高压已关，灯丝工作时间停止累计、发射读数在下一周期消失；LV下绝缘样品电荷经气体离子释放',
    'vacuum.set_lv_pressure': '针阀PI调到新压力：偏差超过20%时LV状态回到lv_pumping（EVAC/LV灯闪烁），运行中的采集以VACUUM_LOST失败；10–100Pa内电子枪真空保持在跳闸限以下',
    'vacuum.vent': '放气到大气压；高压已关，灯丝工作时间停止累计、发射读数在下一周期消失；X（与不抬高样品时的Y）回到换样位置，Y移动在倾斜时改变工作距离；'
                   '束流停止后样品不再荷电',
    'chamber.draw_out': '抽拉门按速度移动到拉出位置；门缝使样品室与室内空气相通（放气状态下压力停在大气压附近）',
    'chamber.draw_in': '抽拉门按速度移动到关闭位置；关严前门缝仍与室内空气相通；已按EVAC时关严后抽气才能完成（不再因门未关超时），含水样品关门后开始放气并逐渐变干',
    'stage.move_xy': 'X/Y电机按速度移动；倾斜时Y移动改变真实工作距离与离焦；束流开着时视场移到别的相，绝缘样品的荷电随之变化',
    'stage.set_z': 'Z手动轴按速度移动，改变真实工作距离与离焦',
    'stage.set_tilt': '倾斜按速度变化，改变束斑处的工作距离与离焦；入射角改变绝缘样品的荷电', 'stage.rotate': '旋转按速度变化，束斑落点随之移动，绝缘样品的荷电随之变化',
    'beam.set_voltage': '束流开着时，新的着陆能量改变绝缘样品的荷电过程', 'beam.set_probe_current': '束流开着时，新的探针电流改变绝缘样品的荷电速率',
    'scan.set_magnification': '束流开着时，扫描视场改变受照的相与电荷分布',
    'beam.observation_on': '灯丝加热到饱和、发射与探针电流上升、灯丝工作时间累计；束流照射使未镀膜绝缘样品荷电；灯丝烧断时高压关闭并记错误',
    'beam.observation_off': '高压关闭后灯丝工作时间停止累计、发射读数在下一周期消失，绝缘样品不再荷电',
    'image.acquire': '帧时间内进度推进，结束时生成图像记录', 'eds.acquire': 'EDS实时间内进度推进，结束时生成谱记录；点分析时束斑固定在一点照射',
    'sim.step': '统一宿主时钟推进全部过程：真空、泵、灯丝、电荷、门与样品台运动、采集，以及供电异常时的断路',
}
ON_STEP_ZH['power.key_toggle'] = ON_STEP_ZH['power.set']
# how each slot is sampled when read
_SAMPLING_CONTINUOUS = {'clock.time_s', 'vacuum.evacuation_elapsed_s', 'panel.vent_lamp', 'panel.evac_lamp', 'panel.lv_lamp', 'chamber.door_state',
                        'chamber.door_position_m', 'stage.moving', 'acquisition.progress', 'truth.working_distance_m', 'truth.defocus_m', 'truth.gas_load_pa_m3_s'} \
    | set(sum(_AX.values(), []))
_SAMPLING_READINGS = {'vacuum.chamber_pressure_indicated_pa': 'Pirani, 3 % relative noise', 'vacuum.gun_pressure_indicated_pa': 'Penning, 10 % relative noise',
                      'vacuum.lv_pressure_indicated_pa': 'LV pressure, 0.5 % relative noise', 'beam.emission_indicated_a': 'emission meter, 1 % relative noise'}
_SAMPLING_CYCLE = {'system.state', 'system.error_code', 'power.enabled', 'vacuum.state', 'panel.vent_lamp_mode', 'panel.evac_lamp_mode', 'panel.lv_lamp_mode',
                   'beam.ht_on', 'beam.filament_state', 'acquisition.id', 'acquisition.kind', 'acquisition.started_by_other_agent', 'result.last_image_id',
                   'result.last_spectrum_id', 'truth.chamber_pressure_pa', 'truth.gun_pressure_pa', 'truth.tmp_speed_fraction', 'truth.filament_on_time_s',
                   'truth.emission_a', 'truth.probe_current_a', 'truth.probe_diameter_m', 'truth.specimen_charge_c'}
_SAMPLING_COMMAND = set(SLOTS) - _SAMPLING_CONTINUOUS - set(_SAMPLING_READINGS) - _SAMPLING_CYCLE


def _sampling_text(k):
    if k in _SAMPLING_CONTINUOUS:
        return ('Instantaneous value at the read time: door and stage motions are integrated continuously between the 1 s instrument cycles (encoder readings '
                'rounded to their display resolution); lamp blinking, elapsed times, outgassing and acquisition progress follow the simulation clock.')
    if k in _SAMPLING_READINGS:
        return f'Reading sampled at the latest 1 s instrument cycle and held until the next one ({_SAMPLING_READINGS[k]}); null while unpowered or not applicable.'
    if k in _SAMPLING_CYCLE:
        return ('Process state advanced on the 1 s instrument cycle (vacuum, pumps, filament, charge, acquisition completion, faults); commands that set it take '
                'effect at once.')
    return 'Changes only when a command is accepted (set point, configuration or bookkeeping); no time dynamics.'
_READY = ['power.enabled', 'system.state', 'system.error_code', 'acquisition.id']
_BEAM = _READY + ['vacuum.state', 'beam.ht_on', 'beam.filament_state']
PRECONDITIONS = {
    'plant.configure': ('无；随机数种子只在首次声明时生效', []),
    'specimen.provide': ('已声明实验室条件；ID不重复（最多20个）；物质在模板库中；面积分数之和为1；镀膜与厚度一致（无镀膜厚度为0）；直径≤150mm、高度≤48mm（厂家）',
                         ['plant.configured', 'specimen.bench_ids']),
    'power.set': ('已声明实验室条件；开机时供电须在AC 100V±10%内（厂家安装要求）', ['plant.configured', 'power.enabled']),
    'power.key_toggle': ('已声明实验室条件；开机时供电须在AC 100V±10%内', ['plant.configured', 'power.enabled']),
    'system.shutdown': ('通电、软件已启动、无采集', ['power.enabled', 'system.state', 'acquisition.id']),
    'error.acknowledge': ('通电且有待确认错误', ['power.enabled', 'system.error_code']),
    'vacuum.evacuate': ('通电、软件已启动、无待确认错误与采集；已在抽气或就绪时不重复启动', _READY + ['vacuum.state']),
    'vacuum.vent': ('通电、软件已启动、无待确认错误与采集；高压开着时自动关闭', _READY + ['vacuum.state', 'beam.ht_on']),
    'vacuum.toggle_lv': ('通电、软件已启动、无待确认错误与采集（观察中会先关闭高压）', _READY + ['vacuum.mode', 'vacuum.state', 'detector.selected', 'beam.ht_on']),
    'vacuum.set_lv_pressure': ('通电', ['power.enabled']),
    'chamber.draw_out': ('样品室已放气到大气压（vacuum.state=vented）、无采集', ['vacuum.state', 'acquisition.id', 'chamber.door_state']),
    'chamber.draw_in': ('Z、倾斜与Y从当前到目标的扫掠范围内样品（或样品座）最高点都在极靴下4mm以外、无采集', ['stage.z_indicated_m', 'stage.tilt_indicated_rad', 'specimen.loaded_id',
                                                                                   'acquisition.id']),
    'specimen.load': ('门完全拉出且未下令推回；样品在实验台上；样品座空着', ['chamber.door_state', 'specimen.bench_ids', 'specimen.loaded_id']),
    'specimen.unload': ('门完全拉出且未下令推回；样品座上有样品', ['chamber.door_state', 'specimen.loaded_id']),
    'stage.move_xy': ('通电、软件已启动、无待确认错误与采集；门关闭；Y运动扫掠范围内样品最高点不进入极靴下4mm以内或不高于当前位置',
                      _READY + ['chamber.door_state', 'stage.y_indicated_m', 'stage.z_indicated_m', 'stage.tilt_indicated_rad', 'specimen.loaded_id']),
    'stage.set_z': ('无采集；门完全拉出且未下令推回，或Z从当前到目标（与正在进行的倾斜/Y运动）的扫掠范围内样品最高点不进入极靴下4mm以内或不高于当前位置（降低总是允许）',
                    ['stage.z_indicated_m', 'stage.tilt_indicated_rad', 'stage.y_indicated_m', 'specimen.loaded_id', 'acquisition.id']),
    'stage.set_tilt': ('无采集；门完全拉出且未下令推回，或倾斜从当前到目标（含边缘最高的中间角度，与正在进行的Z/Y运动）的扫掠范围内样品最高点不进入极靴下4mm以内或不高于当前位置',
                       ['stage.z_indicated_m', 'stage.tilt_indicated_rad', 'stage.y_indicated_m', 'specimen.loaded_id', 'acquisition.id']),
    'stage.rotate': ('无采集', ['acquisition.id']),
    'beam.set_voltage': ('通电、软件已启动、无待确认错误与采集', list(_READY)), 'beam.set_probe_current': ('通电、软件已启动、无待确认错误与采集', list(_READY)),
    'beam.observation_on': ('通电、软件已启动、无待确认错误；真空就绪（高真空或低真空；真空就绪即门已关闭）；灯丝未烧断', _READY + ['vacuum.state', 'beam.filament_state']),
    'beam.observation_off': ('通电', ['power.enabled']),
    'gun.replace_filament': ('样品室已放气到大气压（放气时高压已关闭）', ['vacuum.state', 'vacuum.chamber_pressure_indicated_pa']),
    'scan.set_magnification': ('通电、软件已启动、无待确认错误与采集', list(_READY)),
    'optics.autofocus': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和（观察中）', list(_BEAM)),
    'optics.set_focus': ('通电、软件已启动、无待确认错误与采集', list(_READY)),
    'optics.auto_stigmate': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和', list(_BEAM)),
    'optics.auto_contrast_brightness': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和', list(_BEAM)),
    'detector.select': ('通电、软件已启动、无待确认错误与采集；LV模式不能选SED', _READY + ['vacuum.mode']),
    'image.acquire': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和；记录未满200条', list(_BEAM)),
    'eds.acquire': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和；预计死时间不超过90%；记录未满200条', _BEAM + ['beam.probe_current_setpoint_a']),
    'image.result': ('记录存在且不属于另一个agent', []), 'eds.result': ('记录存在且不属于另一个agent', []),
    'eds.quantify': ('谱存在、已完成且不属于另一个agent；给定元素须在库中且Z≥4；自动识峰须找到显著峰', []),
    'records.list': ('无', []), 'events.read': ('无', []), 'ledger.read': ('无', []), 'provenance.read': ('无', []), 'sim.step': ('无', []),
}
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入结构、类型或范围', 'ACCESS_DENIED': '该角色无权调用，或当前采集/记录属于另一个agent', 'REQUEST_CONFLICT': '同一request_id已用于不同的请求',
    'UNKNOWN_INSTANCE': '实例ID不存在', 'UNSUPPORTED': '本配置不提供该功能（理由见function_zh）', 'UNKNOWN_CAPABILITY': '能力ID不存在', 'UNKNOWN_OPERATION': '操作ID不存在',
    'NOT_RUNNING': '操作不在进行中', 'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_OFF': '未通电（或断电中止了采集）', 'NOT_READY': '软件加载中或已软件关机',
    'ERROR_PENDING': '有待确认的错误（先确认）', 'NO_ERROR': '没有待确认的错误', 'MAINS_OUT_OF_RANGE': '供电电压超出AC 100V±10%（厂家安装要求）',
    'ACQUISITION_RUNNING': '有图像或EDS采集正在进行', 'VACUUM_NOT_READY': '真空未就绪（高真空或低真空）', 'DOOR_OPEN': '样品室门没有关好',
    'BEAM_NOT_READY': '电子束未就绪：未开始观察或灯丝仍在加热', 'CHAMBER_UNDER_VACUUM': '样品室在真空下，门被大气压压住，须先放气', 'DOOR_NOT_OPEN': '门未完全拉出（或已下令推回）',
    'STAGE_COLLISION_RISK': '运动会使样品最高点进入极靴下4mm以内（离下挂3.5mm的背散射探测器不足0.5mm）并高于当前位置（先降低Z或减小倾斜）', 'SPECIMEN_EXISTS': '样品ID已存在或实验台已有20个样品',
    'UNKNOWN_SUBSTANCE': '物质不在SEM/EDS模板库中', 'AREA_FRACTIONS_INVALID': '各相面积分数须为正且之和为1', 'COATING_INVALID': '镀膜种类与厚度不一致',
    'SPECIMEN_TOO_LARGE': '样品超过直径150mm或高度48mm（厂家）', 'UNKNOWN_SPECIMEN': '样品ID不存在', 'SPECIMEN_NOT_ON_BENCH': '样品不在实验台上',
    'HOLDER_OCCUPIED': '样品座上已有样品', 'NO_SPECIMEN': '样品座上没有样品', 'FILAMENT_BURNED_OUT': '灯丝已烧断（放气后更换）', 'VENT_REQUIRED': '须先放气到大气压',
    'HT_ON': '电子枪高压开着', 'DETECTOR_NOT_AVAILABLE': 'LV模式不能用SED（E-T探测器高压栅会放电），请用BED', 'RECORDS_FULL': '记录已满（200条）',
    'UNKNOWN_RECORD': '图像或谱记录不存在', 'NOT_COMPLETED': '谱尚未完成', 'UNKNOWN_ELEMENT': '元素不在库中或低于Be（EDS范围Be–U）', 'NO_PEAKS': '自动识峰没有找到显著峰',
    'QUANT_FAILED': '没有可定量的谱线族（过压比不足或超出能量范围）', 'USER_CANCEL': '采集被取消', 'BEAM_OFF': '采集中电子束关闭（停止观察、灯丝烧断或高压跳闸）',
    'VACUUM_LOST': '采集中真空失去就绪', 'EVAC_TIMEOUT': '抽气10min未完成（JSM-IT300手册同系列）', 'FILAMENT_BURNOUT': '钨灯丝烧断',
    'HT_TRIP': '电子枪真空变差，高压跳闸', 'MAINS_FAULT': '供电电压异常，主机断路',
    'COUNT_RATE_TOO_HIGH': 'EDS死时间会超过90%（输入计数率过高），请降低探针电流',
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_R = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING']
_B = _R + ['VACUUM_NOT_READY', 'BEAM_NOT_READY']            # vacuum ready implies the door is closed
CAP_ERRORS = {
    'plant.configure': [], 'specimen.provide': ['PLANT_REQUIRED', 'SPECIMEN_EXISTS', 'UNKNOWN_SUBSTANCE', 'AREA_FRACTIONS_INVALID', 'COATING_INVALID', 'SPECIMEN_TOO_LARGE'],
    'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.key_toggle': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'system.shutdown': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ACQUISITION_RUNNING'], 'error.acknowledge': ['PLANT_REQUIRED', 'POWER_OFF', 'NO_ERROR'],
    'vacuum.evacuate': list(_R), 'vacuum.vent': list(_R), 'vacuum.toggle_lv': list(_R), 'vacuum.set_lv_pressure': ['PLANT_REQUIRED', 'POWER_OFF'],
    'chamber.draw_out': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING', 'CHAMBER_UNDER_VACUUM'], 'chamber.draw_in': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING', 'STAGE_COLLISION_RISK'],
    'specimen.load': ['PLANT_REQUIRED', 'DOOR_NOT_OPEN', 'UNKNOWN_SPECIMEN', 'SPECIMEN_NOT_ON_BENCH', 'HOLDER_OCCUPIED'],
    'specimen.unload': ['PLANT_REQUIRED', 'DOOR_NOT_OPEN', 'NO_SPECIMEN'], 'stage.move_xy': _R + ['DOOR_OPEN', 'STAGE_COLLISION_RISK'],
    'stage.set_z': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING', 'STAGE_COLLISION_RISK'], 'stage.set_tilt': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING', 'STAGE_COLLISION_RISK'],
    'stage.rotate': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING'], 'beam.set_voltage': list(_R), 'beam.set_probe_current': list(_R),
    'beam.observation_on': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'VACUUM_NOT_READY', 'FILAMENT_BURNED_OUT'],
    'beam.observation_off': ['PLANT_REQUIRED', 'POWER_OFF'], 'gun.replace_filament': ['PLANT_REQUIRED', 'VENT_REQUIRED'],
    'scan.set_magnification': list(_R), 'optics.autofocus': list(_B), 'optics.set_focus': list(_R),
    'optics.auto_stigmate': list(_B), 'optics.auto_contrast_brightness': list(_B),
    'detector.select': _R + ['DETECTOR_NOT_AVAILABLE'],
    'image.acquire': _B + ['RECORDS_FULL', 'USER_CANCEL', 'BEAM_OFF', 'VACUUM_LOST', 'POWER_OFF'],
    'eds.acquire': _B + ['RECORDS_FULL', 'COUNT_RATE_TOO_HIGH', 'USER_CANCEL', 'BEAM_OFF', 'VACUUM_LOST', 'POWER_OFF'],
    'image.result': ['UNKNOWN_RECORD'], 'eds.result': ['UNKNOWN_RECORD'], 'eds.quantify': ['UNKNOWN_RECORD', 'NOT_COMPLETED', 'UNKNOWN_ELEMENT', 'NO_PEAKS', 'QUANT_FAILED'],
}
_PARTIAL = {'USER_CANCEL', 'BEAM_OFF', 'VACUUM_LOST', 'POWER_OFF'}
CAP_ERROR_ZH = {('chamber.draw_in', 'STAGE_COLLISION_RISK'): '推回后（或推回途中Z/T/Y到达目标时）样品最高点会进入极靴下4mm以内：推门按整个扫掠范围严格判定（先降低Z或减小倾斜）'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件（室温、湿度、供电、放气气体、噪声种子），没有可见模型变化',
    'specimen.provide': '只在过程层实验台上增加样品；场景没有样品几何（样品位由接口点specimen_surface表示）',
    'system.shutdown': '软件关机只关闭高压与灯丝并记录状态；这些状态没有场景显示（kV/倍数/WD行显示设定值，Pa读数与开关灯随真空与供电），没有可见模型变化', 'error.acknowledge': '只清除错误状态，没有可见模型变化',
    'vacuum.set_lv_pressure': '只写LV压力设定；屏上Pa读数随step显示实际压力', 'specimen.load': '样品只进入过程层；场景没有样品几何',
    'specimen.unload': '样品只离开过程层样品座；场景没有样品几何', 'beam.set_probe_current': '只写探针电流设定，没有可见模型变化',
    'beam.observation_on': '高压与灯丝状态没有可见几何；图像与读数随step变化', 'beam.observation_off': '同上，没有可见几何变化',
    'gun.replace_filament': '过程层更换灯丝；场景中枪罩不打开（没有维护动画）', 'optics.auto_stigmate': '只改像散状态，没有可见模型变化',
    'optics.auto_contrast_brightness': '只改探测器增益/偏置，没有可见模型变化', 'detector.select': '只改成像探测器，没有可见模型变化',
    'image.acquire': '采集只在过程层生成图像记录；屏幕贴图不随图像内容变化', 'image.result': '只读记录', 'eds.acquire': '采谱只在过程层生成谱记录，没有可见模型变化',
    'eds.result': '只读记录', 'eds.quantify': '分析计算，没有可见模型变化', 'records.list': '只读列表', 'events.read': '只读事件', 'ledger.read': '只读账目（evaluator）',
    'provenance.read': '只读来源记录（evaluator）',
}
_GUARDED = _CONTROL_GUARDED | _DATA_GUARDED
RESOURCES = {'image.acquire': ['electron_beam', 'scan_generator', 'detectors', 'stage', 'host_clock'], 'eds.acquire': ['electron_beam', 'eds_detector', 'stage', 'host_clock'],
             'vacuum.evacuate': ['vacuum_system'], 'vacuum.vent': ['vacuum_system', 'electron_gun', 'stage'], 'vacuum.toggle_lv': ['vacuum_system', 'detectors'],
             'vacuum.set_lv_pressure': ['vacuum_system'], 'chamber.draw_out': ['chamber_door', 'stage'], 'chamber.draw_in': ['chamber_door', 'stage'],
             'specimen.load': ['specimen_holder'], 'specimen.unload': ['specimen_holder'], 'stage.move_xy': ['stage'], 'stage.set_z': ['stage'], 'stage.set_tilt': ['stage'],
             'stage.rotate': ['stage'], 'beam.set_voltage': ['electron_gun'], 'beam.set_probe_current': ['electron_gun'], 'beam.observation_on': ['electron_gun'],
             'beam.observation_off': ['electron_gun'], 'gun.replace_filament': ['electron_gun'], 'scan.set_magnification': ['scan_generator'],
             'optics.autofocus': ['electron_beam', 'objective_lens'], 'optics.set_focus': ['objective_lens'], 'optics.auto_stigmate': ['electron_beam', 'objective_lens'],
             'optics.auto_contrast_brightness': ['electron_beam', 'detectors'], 'detector.select': ['detectors'], 'power.set': ['mains', 'vacuum_system', 'electron_gun'],
             'power.key_toggle': ['mains', 'vacuum_system', 'electron_gun'], 'system.shutdown': ['electron_gun'], 'plant.configure': ['room', 'mains', 'vent_gas'],
             'specimen.provide': ['bench'], 'sim.step': ['host_clock'], 'eds.quantify': ['records'], 'image.result': ['records'], 'eds.result': ['records']}
_ROBOT = '实体操作为宿主/机器人技能的过程层抽象，没有接触或机器人验证；门与样品台关节由过程状态镜像，样品没有几何。'
CAP_LIMITS = {
    'plant.configure': ['宿主声明的实验室条件；供电波动、振动、杂散磁场与室温漂移对图像的影响不建模（供电只按±10%判断开机与断路）。'],
    'specimen.provide': ['样品用相面积分数与晶粒尺寸的Voronoi相图表示；形貌只分抛光平面/颗粒/粗糙断口三类（每个晶粒一个随机倾斜）；含水只影响放气量：只在样品处于关门且未放气的样品室中时放出水汽，放气量按累计暴露时间指数衰减（τ=30min，工程值），门拉出或放气时不放气也不变干，放气后不会重新变湿。'],
    'power.set': ['软件加载60s为工程值；未软件关机就断电只记警告，记录保留。'],
    'power.key_toggle': ['MAIN POWER为三位钥匙开关，本资产在Matterix中按一次按压切换的弹簧键处理。'],
    'vacuum.evacuate': ['真空为单体积集总模型：机械泵有效抽速、涡轮泵抽速与起转时间、放气与漏率均为工程值；抽气约2min（就绪时间主要由粗抽与涡轮泵起转决定；干燥氮气放气后放气量更小，'
                        '达到的枪真空更好但就绪时间几乎不变），10min超时依同系列手册。'],
    'vacuum.vent': ['放气时间约1–2min（放气阀流导工程值）；X回交换位，Y在不抬高样品到极靴附近时回位（否则留在原处并记Y_RETURN_SKIPPED），Z/T/R为手动轴不自动回位。'],
    'vacuum.toggle_lv': ['LV：机械泵经孔板抽样品室、针阀PI控制进气，电子枪经压差光阑保持高真空；各流导为工程值；切换模式时自动关闭高压（工程选择），故电子枪真空不会越过跳闸限，'
                         'HT_TRIP联锁在公开操作序列中通常不触发。'],
    'chamber.draw_out': [_ROBOT, '门拉出行程0.20m为工程值；断电时阀门关闭，未到大气压的样品室不能开门。'],
    'chamber.draw_in': [_ROBOT, '碰撞判据：Z、倾斜与Y扫掠范围内样品（或样品座）顶部边缘离极靴至少4mm（离下挂3.5mm的BED至少0.5mm）；未建模样品侧面与EDS/SED探头的碰撞。'],
    'specimen.load': [_ROBOT], 'specimen.unload': [_ROBOT],
    'stage.move_xy': ['X/Y电机速度3mm/s为工程值；到位精度按编码器0.1µm显示，未建模背隙；Y沿倾斜摇篮移动并计入碰撞扫掠检查。'],
    'stage.set_z': ['Z为手动旋钮的单圈抽象（Matterix中旋钮转角与升降双向联动），手转速度2mm/s为工程值；碰撞按扫掠范围保守检查（Z与T运动独立推进）。'],
    'stage.set_tilt': ['倾斜轴取过样品座顶面中心的X轴（样品顶面不在倾斜轴上，高度h的样品倾斜后束斑落在顶面距中心h·tanθ处，工作距离=48mm−升降−h/cosθ）；'
                       '束斑落出顶面时按落到样品座平面处理，未建模样品侧壁；倾斜方向朝门为工程选择；手转速度10°/s为工程值。'],
    'stage.rotate': ['R为手动旋转，速度30°/s为工程值。'],
    'beam.set_voltage': ['电压步进按同系列JSM-IT300手册（≤3kV 0.1kV、>3kV 1kV）；换电压后像散随机重置（工程）。'],
    'beam.set_probe_current': ['探针电流直接取设定值（聚光镜/光阑的具体档位未建模）；束斑按最优孔径角计算。'],
    'beam.observation_on': ['灯丝10s到饱和（设施手册约10s成像）；发射电流100µA与寿命均值300h（Weibull，设施手册“每几百小时”更换）为工程值；自动对中视为瞬时完成。'],
    'gun.replace_filament': [_ROBOT, '新灯丝寿命按Weibull(k=3，均值300h)抽样（工程值）。'],
    'scan.set_magnification': ['放大倍数按128mm打印宽度定义（厂家）；显示器上的“显示倍数”（×14–839,724）未单独建模。'],
    'optics.autofocus': ['自动聚焦误差为景深的1/8（1σ，工程值）；不模拟聚焦失败。'],
    'optics.auto_contrast_brightness': ['按当前视场实时帧的直方图（信号范围±2.5σ噪声）映射到灰度20–235；换条件后需重做，否则图像可能过暗或饱和；放大器增益上限未建模（由噪声宽度限定）。'],
    'image.acquire': ['图像只返回按灰度分割的区域统计与32×24缩略图（不给出物质名与每电子产额）；信号模型（SE/BSE产额、收集效率、噪声、荷电、LV散射）为文献经验式加工程参数，见mechanisms。',
                      '区域分割只比较各相平均灰度与阈值√((3σ噪声)²+(2σ纹理)²)，不做空间连通分析；粗糙/颗粒样品的相内形貌纹理会使成分相近的相并为一区；TOPO差分信号的衬度为null。'],
    'eds.acquire': ['谱为物理模型生成的泊松计数（Bethe电离截面、Joy–Luo阻止本领、Philibert吸收、Kramers连续谱、Fano分辨率、逃逸峰、死时间）；荧光校正、和峰与不完全电荷收集拖尾未建模；'
                    'Duane–Hunt限由谱尾端连续谱的泊松最大似然直线外推估计（1nA、30s约±30–60eV；计数只有几万时约±几百eV；不超过加速电压；加速电压超过约20.2keV（谱尾被0–20.48keV截断）'
                    '或尾端计数不足1000时为null）；记录的出射角/立体角按显示WD与倾斜读数计算，'
                    '与真实几何不一致时定量出现相应偏差。'],
    'eds.quantify': ['无标样ZAF：峰族面积加权最小二乘，生成项取Green–Cosslett型(U0−1)^1.67/[(Z/A)·ln(1.166(E0+Ec)/2J)]、背散射修正1−0.4η(1−1/U0)与完整Philibert吸收（含h项），'
                     '迭代并归一化；与生成模型（Bethe积分、Myklebust背散射）的差异即方法偏差（库中物质15/20kV给出全部元素时中位约5%相对：重元素二元体系约±3%；氧化物与硅酸盐中'
                     'O偏低10–40%、金属偏高至约15%（硅酸盐中的K约+25%）；B、N、Cl等轻元素与含H有机物可偏差20–35%）。自动识峰在参考条件（15/20kV、T3、1nA、60s）下对库中75种物质'
                     '只漏判碳基体中约7wt%的N；多报或漏报元素的谱约占：5kV约10%（重元素低能M线与N/O/F/Ti重叠、Ti L/O K、Pb M/S K）、T1约7%（宽多重峰）、T2与约10nA高计数约3–4%'
                     '（弱线拟合残差）；不提议惰性气体（含Ar）、不稳定元素、B与Be。'],
    'sim.step': ['唯一的宿主时间推进；逻辑、读数与采集在1s仪器周期上执行，真空、运动、灯丝与电荷连续推进。'],
}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.jsmit200_sem_eds.limitations。'


def _foreign_acq_text(k):
    if k not in _CONTROL_GUARDED:
        return ''
    return '；另一个agent的采集进行中时，agent调用被拒（ACCESS_DENIED' + ('；未通电时开机除外' if k in ('power.set', 'power.key_toggle') else '') + '）'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    access = ['ACCESS_DENIED'] if (CAPS[k]['visibility'] != 'agent' or k in _GUARDED) else []
    return list(dict.fromkeys(_COMMON_ERRORS + access + CAP_ERRORS.get(k, [])))


OBSERVES = {
    'plant.configure': ['plant.configured'], 'specimen.provide': ['specimen.bench_ids'], 'power.set': ['power.enabled', 'system.state'],
    'power.key_toggle': ['power.enabled', 'system.state'], 'system.shutdown': ['system.state'], 'error.acknowledge': ['system.error_code'],
    'vacuum.evacuate': ['vacuum.state', 'vacuum.chamber_pressure_indicated_pa', 'panel.evac_lamp_mode'],
    'vacuum.vent': ['vacuum.state', 'vacuum.chamber_pressure_indicated_pa', 'panel.vent_lamp_mode'],
    'vacuum.toggle_lv': ['vacuum.mode', 'vacuum.state', 'vacuum.lv_pressure_indicated_pa', 'panel.lv_lamp_mode'], 'vacuum.set_lv_pressure': ['vacuum.lv_setpoint_pa'],
    'chamber.draw_out': ['chamber.door_state'], 'chamber.draw_in': ['chamber.door_state'], 'specimen.load': ['specimen.loaded_id'], 'specimen.unload': ['specimen.loaded_id'],
    'stage.move_xy': ['stage.x_indicated_m', 'stage.y_indicated_m', 'stage.moving'], 'stage.set_z': ['stage.z_indicated_m', 'stage.moving'],
    'stage.set_tilt': ['stage.tilt_indicated_rad', 'stage.moving'], 'stage.rotate': ['stage.rotation_indicated_rad', 'stage.moving'],
    'beam.set_voltage': ['beam.accelerating_voltage_v'], 'beam.set_probe_current': ['beam.probe_current_setpoint_a'],
    'beam.observation_on': ['beam.ht_on', 'beam.filament_state', 'beam.emission_indicated_a'], 'beam.observation_off': ['beam.ht_on'],
    'gun.replace_filament': ['beam.filament_state'], 'scan.set_magnification': ['scan.magnification', 'scan.field_width_m'], 'optics.autofocus': ['optics.focus_wd_m'],
    'optics.set_focus': ['optics.focus_wd_m'], 'optics.auto_stigmate': ['optics.astigmatism_corrected'], 'optics.auto_contrast_brightness': ['optics.acb_done'],
    'detector.select': ['detector.selected'], 'image.acquire': ['acquisition.id', 'acquisition.progress', 'result.last_image_id'], 'image.result': ['result.last_image_id'],
    'eds.acquire': ['acquisition.id', 'acquisition.progress', 'result.last_spectrum_id'], 'eds.result': ['result.last_spectrum_id'], 'eds.quantify': [],
    'records.list': [], 'events.read': ['system.error_code'], 'ledger.read': [], 'provenance.read': [], 'sim.step': ['clock.time_s'],
}
_CAT, _PROD, _MAN, _IA, _PNNL, _WB = 'manufacturer_catalogue', 'manufacturer_product_page', 'related_manual_jsm_it300', 'facility_guide_iastate', 'facility_pnnl', 'user_workbook'
_LIBS, _FIT, _PHYS, _SIM = 'sem_eds_library', 'optics_fit', 'physics_literature', 'simulation_assumption'
_XRAY = ['nist_srd126_tab1', 'nist_srd126_tab2', 'nist_srd128', 'xraydb']          # the raw data behind the library's X-ray lines and attenuation
_AUDIT = 'template_validation'
CAP_SOURCES = {
    'plant.configure': [_CAT, _SIM], 'specimen.provide': [_CAT, _IA, _LIBS, 'xraydb_materials', _AUDIT, _SIM], 'power.set': [_MAN, _CAT], 'power.key_toggle': [_MAN], 'system.shutdown': [_MAN, _SIM],
    'error.acknowledge': [_MAN, _SIM], 'vacuum.evacuate': [_CAT, _MAN, _IA, _SIM], 'vacuum.vent': [_MAN, _IA, _SIM], 'vacuum.toggle_lv': [_CAT, _MAN, _SIM],
    'vacuum.set_lv_pressure': [_CAT], 'chamber.draw_out': [_CAT, _IA], 'chamber.draw_in': [_CAT, _IA], 'specimen.load': [_CAT, _IA], 'specimen.unload': [_CAT, _IA],
    'stage.move_xy': [_CAT], 'stage.set_z': [_CAT, _IA], 'stage.set_tilt': [_CAT], 'stage.rotate': [_CAT], 'beam.set_voltage': [_CAT, _MAN],
    'beam.set_probe_current': [_CAT, _FIT], 'beam.observation_on': [_CAT, _IA, _SIM], 'beam.observation_off': [_IA], 'gun.replace_filament': [_CAT, _IA, _SIM],
    'scan.set_magnification': [_CAT], 'optics.autofocus': [_CAT, _FIT, _SIM], 'optics.set_focus': [_CAT, _FIT], 'optics.auto_stigmate': [_CAT, _SIM],
    'optics.auto_contrast_brightness': [_CAT, _SIM], 'detector.select': [_CAT, _MAN, _PNNL], 'image.acquire': [_CAT, _WB, _LIBS, _FIT, _PHYS, _SIM],
    'image.result': [_CAT], 'eds.acquire': [_CAT, _PROD, _PNNL, _WB, _LIBS] + _XRAY + [_AUDIT, _PHYS, _SIM], 'eds.result': [_CAT],
    'eds.quantify': [_CAT, _LIBS] + _XRAY + [_AUDIT, _PHYS],
    'records.list': [_SIM], 'events.read': [_SIM], 'ledger.read': ['runtime_code'], 'provenance.read': [_LIBS, _AUDIT], 'sim.step': ['runtime_code'],
    'eds.map': [_CAT], 'stage.navigation_camera': [_CAT, _PROD], 'chamber.scope': [_CAT], 'image.montage': [_CAT], 'detector.lsed': [_CAT], 'stage.motor_zt': [_CAT],
    'hardware.connect': [_CAT], 'robot.handle_specimen': [_SIM], 'accuracy.certification': [_CAT, _WB]}
PARAM_ZH = {
    'room_k': '实验室室温（安装要求15–27°C）', 'relative_humidity': '相对湿度（安装要求≤60%；影响放气后水汽放气）', 'mains_voltage_v': '供电电压（单相AC 100V±10%）',
    'vent_gas': '放气气体（室内空气或干燥氮气）', 'noise_seed': '随机数种子（真空计噪声、灯丝寿命、自动聚焦误差、图像与EDS计数噪声）',
    'specimen_id': '样品ID', 'phases': '样品各相（模板库物质、面积分数、晶粒尺寸）', 'diameter_m': '样品直径', 'height_m': '样品高度',
    'topography': '样品形貌类别', 'coating': '导电镀膜种类', 'coating_thickness_m': '镀膜厚度', 'wet': '样品是否含水',
}
LIMITATIONS = [
    '选定JSM-IT200 LA（钨灯丝、TMP+RP全自动真空、LV 10–100Pa、SED+BED、集成JED-2300 DrySD 25mm² EDS、X/Y电机台）；SNS导航相机、Chamber Scope、LSED、5轴电机台、选配操作面板（Operation Panel）与3D为选配未装（面板操作经软件与控制台按键）；拼图（目录列为标准功能）、EDS面分布与线扫描未实现。',
    '真空为单体积集总模型（体积按几何样品室空腔与镜筒估计），机械泵有效抽速、涡轮泵抽速与起转、放气量（空气/氮气/含水）、漏率、放气阀与LV孔板/针阀流导均为工程值；气体物料账逐步精确闭合。',
    '束斑按亮度、球差、色差与衍射在最优孔径角下的平方和计算；有效Cs/Cc由derive_optics_parameters.py按厂家分辨率（30kV 3.0nm、3kV 8.0nm、1kV 15nm，LV 4.0nm）拟合，β、ΔE、测试束流与边缘分辨率系数为固定假设，拟合不唯一。',
    '背散射系数用Hunger–Küchler元素式与Castaing质量分数规则、Arnal倾斜律；二次电子用Lin–Joy通用曲线（其材料类别参数描述块体的总二次电子产额，不再另加SE2项）与正割倾斜律、'
    'SED收集效率0.45；BED按工作距离计算立体角、能量加权与放大器噪声；这些是经验式与工程值，不是JSM-IT200实测。',
    '未镀膜绝缘体在高真空下每个相有各自的负表面电位（电容1e-13F，工程值）：着陆能量=E0−V，净沉积电流I(1−η−δ)在实际入射角（样品台倾斜加局部坡度）下使V趋向总产额为1的平衡'
    '（着陆能量=该入射角的E2，倾斜越大E2越高、荷电越少）；电位超过束能时束被反射（镜面），降压后不再着陆；观察时剂量分布在扫描视场、EDS点分析时集中在束斑；LV下气体离子中和、'
    '导电镀膜导走电荷，不荷电。二次电子参数按材料类别给出，E2与常引值只在量级上一致（石英约3.8keV对约3keV、蓝宝石3.8对4.2、PVC 1.65对1.65、PTFE 1.5对1.85、Kapton 1.5对约0.4keV），'
    '个别聚合物的荷电阈值偏高。'
    'LV气体散射按空气截面数量级估计的未散射分数形成裙边背景。',
    'EDS：特征X射线按Bethe电离截面在Joy–Luo减速过程上积分、Myklebust背散射损失、Elam荧光产额与谱线强度、Philibert–Duncumb–Heinrich吸收（Chantler衰减系数）、探测器效率（聚酰亚胺窗、Al膜、Si网格、死层、0.45mm Si）、几何立体角与出射角（35°为工程假设）、Kramers连续谱、Fano分辨率（Mn Kα 127eV，厂家≤130eV）、Si逃逸峰、可瘫痪死时间；二次荧光、和峰、拖尾与L/M壳层Coster–Kronig跃迁未建模。',
    '无标样ZAF定量的生成项为Green–Cosslett型(U0−1)^1.67/[(Z/A)·ln(1.166(E0+Ec)/2J)]、背散射修正1−0.4η(1−1/U0)、吸收为完整Philibert式（含h项），峰族面积由高斯模板加连续谱加权最小二乘'
    '得到并按记录的名义出射角迭代归一化；与生成模型的差异是方法偏差（中位约5%相对；重元素二元体系约±3%，氧化物与硅酸盐中O偏低10–40%、金属偏高至约15%（硅酸盐中K约+25%），'
    '轻元素与有机物可达20–35%）。自动识峰按探测计数由强到弱贪心匹配（谱线族模式、其他谱线族按理论强度可检出时须有峰、纯元素理论强度比一致性与局部形状），不提议惰性气体（含Ar）、'
    '不稳定元素、B与Be；参考条件（15/20kV、T3、1nA）下只漏判碳基体中约7wt%的N，多报或漏报的谱在5kV约10%、T1约7%、T2与约10nA高计数约3–4%（重元素低能M线、Ti L/O K、'
    'Pb M/S K、宽多重峰与弱线残差）。',
    '样品微结构为种子化Voronoi相图（相面积分数与晶粒尺寸），形貌三类；图像只输出按灰度分割的区域统计与32×24缩略图（不给物质名），不输出全分辨率图像；点分析按Kanaya–Okayama射程一半的'
    '激发体积混合周围相；倾斜样品的束斑落在顶面距中心h·tanθ处，落出顶面按落到样品座处理，未建模样品侧壁。',
    '样品台X/Y电机与Z/T/R手动的速度、门拉出行程0.20m、碰撞间隙4mm、Z/T旋钮单圈抽象为工程值；换样流程（放气、拉门、装样、按样品高度设Z、推门、抽气、观察）按设施手册；实体操作没有接触或机器人验证。',
    'agent只看到示值、设定、图像/谱记录与事件；真实压力、气体负荷、灯丝寿命与已工作时间、探针电流与直径、真实工作距离、离焦、像散、样品电荷（着陆能量只能由谱的Duane–Hunt限估计）与样品真实'
    '组成只对evaluator可见；图像与谱记录中的WD为显示值、区域不带物质名、模板来源不列物质；记录按调用者隔离（evaluator或宿主创建的记录对所有agent可读，Matterix宿主以evaluator身份调用运行层）。',
    '没有真实硬件、JEOL固件、SEM Center/SMILE VIEW软件或厂家验收数据。',
]


def export_jeol_jsmit200_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'JeolJsmit200Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.jeol_jsmit200.JeolJsmit200Session.invoke',
                                              invocation=f'session.invoke(instance_id="jsmit200_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_acq_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['acquisition.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='JeolJsmit200Runtime.invoke / JeolJsmit200Session._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定、目标或状态标志；压力、位置、灯丝与采集进度只随step推进')
                                for s in v['effects']],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=dict(mode='none', description_zh='不支持：不实现，没有模型变化' if un else MODEL_NONE_ZH.get(k, '状态绑定到场景显示/关节（见合同model_bindings）'), binding_ids=[]),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion='采集结束（完成、失败或取消）：帧时间或EDS实时间到达后记录状态确定' if async_ else '原子设定、配置或查询，实际物理状态由step推进',
                                completion_state_ids=['acquisition.id', 'acquisition.progress', 'result.last_image_id' if k == 'image.acquire' else 'result.last_spectrum_id']
                                if async_ else [],
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('取消或失败（停止观察、灯丝烧断、高压跳闸、真空失去、断电）时记录以cancelled/failed结束，不产生图像/谱；'
                                                    '已照射的电子与电荷仍计入账目（样品已受照）。') if async_ else
                                                   ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH[e]), side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH[k]) for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in v['effects'])]      # a slot set at once may also evolve on the step
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'kv_min_v': (KV_RANGE_V[0], 'V', '最低加速电压0.5kV', _CAT), 'kv_max_v': (KV_RANGE_V[1], 'V', '最高加速电压30kV', _CAT),
            'probe_current_min_a': (PROBE_CURRENT_RANGE_A[0], 'A', '最小探针电流1pA', _CAT), 'probe_current_max_a': (PROBE_CURRENT_RANGE_A[1], 'A', '最大探针电流0.3µA', _CAT),
            'mag_min': (MAG_RANGE[0], '1', '最小直接放大倍数×5', _CAT), 'mag_max': (MAG_RANGE[1], '1', '最大直接放大倍数×300,000', _CAT),
            'print_width_m': (PRINT_WIDTH_M, 'm', '放大倍数定义用打印宽度128mm', _CAT), 'lv_min_pa': (LV_RANGE_PA[0], 'Pa', 'LV最低压力10Pa', _CAT),
            'lv_max_pa': (LV_RANGE_PA[1], 'Pa', 'LV最高压力100Pa', _CAT), 'resolution_30kv_m': (RESOLUTION_HV_M[30000.0], 'm', '分辨率3.0nm（30kV，HV）', _CAT),
            'resolution_3kv_m': (RESOLUTION_HV_M[3000.0], 'm', '分辨率8.0nm（3kV）', _CAT), 'resolution_1kv_m': (RESOLUTION_HV_M[1000.0], 'm', '分辨率15nm（1kV）', _CAT),
            'resolution_lv_m': (RESOLUTION_LV_M, 'm', 'LV分辨率4.0nm（30kV，BED）', _CAT), 'wd_min_m': (WD_RANGE_M[0], 'm', 'Z（WD）下限5mm', _CAT),
            'wd_max_m': (WD_RANGE_M[1], 'm', 'Z（WD）上限48mm', _CAT), 'stage_x_travel_m': (0.080, 'm', 'X行程80mm', _CAT), 'stage_y_travel_m': (0.040, 'm', 'Y行程40mm', _CAT),
            'tilt_min_rad': (TILT_RANGE_RAD[0], 'rad', '倾斜下限−10°', _CAT), 'tilt_max_rad': (TILT_RANGE_RAD[1], 'rad', '倾斜上限90°', _CAT),
            'specimen_max_diameter_m': (SPECIMEN_MAX_DIAMETER_M, 'm', '样品最大直径150mm', _CAT), 'specimen_max_height_m': (SPECIMEN_MAX_HEIGHT_M, 'm', '样品最大高度48mm', _CAT),
            'eds_area_m2': (EDS_AREA_M2, 'm^2', 'DrySD有效面积25mm²', _CAT), 'eds_fwhm_mn_spec_ev': (EDS_FWHM_MN_SPEC_EV, 'eV', 'EDS分辨率≤130eV（目录未注明谱线，按惯例为Mn Kα）', _CAT),
            'mains_nominal_v': (MAINS_NOMINAL_V, 'V', '单相AC 100V', _CAT), 'evac_limit_s': (EVAC_LIMIT_S, 's', '抽气10min未完成报错', _MAN),
            'rp_speed_m3_s': (RP_SPEED_M3_S, 'm^3/s', '机械泵抽速≥100L/min', _MAN), 'holder_radius_m': (HOLDER_RADIUS_M, 'm', '32mm多用途样品座', _IA),
            'saturation_s': (SATURATION_S, 's', '开始观察约10s成像', _IA)}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=[src], uncertainty=None)
    for name, (val, unit, meaning) in {
            'c_s_coef': (C_S_COEF, '1', 'Cs=c_s(WD+2mm)，按分辨率拟合'), 'c_c_coef': (C_C_COEF, '1', 'Cc=c_c(WD+2mm)，按分辨率拟合'),
            'bed_resolution_factor': (BED_RES_FACTOR, '1', 'BED/LV分辨率系数（LV 4.0nm/同条件WD 5mm下模型分辨率）')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_FIT, _CAT, _MAN],
                            uncertainty='按厂家分辨率拟合的等效常数（derive_optics_parameters.py）：拟合不唯一（β–Cs、ΔE–Cc互相替代），不是实测透镜数据')
    for name, (val, unit, meaning) in {
            'v_system_m3': (V_SYSTEM_M3, 'm^3', '抽气体积（几何样品室空腔+镜筒）'), 'rp_eff_m3_s': (RP_EFF_M3_S, 'm^3/s', '机械泵经管路的有效抽速'),
            'tmp_speed_m3_s': (TMP_SPEED_M3_S, 'm^3/s', '涡轮分子泵有效抽速'), 'tmp_spinup_s': (TMP_SPINUP_S, 's', '涡轮泵起转时间'),
            'crossover_pa': (CROSSOVER_PA, 'Pa', '粗抽切换压力'), 'hv_ready_pa': (HV_READY_PA, 'Pa', '高真空就绪压力'), 'gun_trip_pa': (GUN_TRIP_PA, 'Pa', '高压跳闸压力'),
            'outgas_air_pa_m3_s': (OUTGAS_AIR_PA_M3_S, 'Pa*m^3/s', '空气放气后放气量（×(1+2RH)）'), 'outgas_n2_pa_m3_s': (OUTGAS_N2_PA_M3_S, 'Pa*m^3/s', '氮气放气后放气量'),
            'wet_outgas_pa_m3_s': (WET_OUTGAS_PA_M3_S, 'Pa*m^3/s', '含水样品放气量'), 'vent_conductance_m3_s': (VENT_CONDUCTANCE_M3_S, 'm^3/s', '放气阀流导'),
            'lv_orifice_m3_s': (LV_ORIFICE_M3_S, 'm^3/s', 'LV孔板抽速'), 'pla_conductance_m3_s': (PLA_CONDUCTANCE_M3_S, 'm^3/s', '压差光阑流导'),
            'emission_sat_a': (EMISSION_SAT_A, 'A', '饱和发射电流'), 'filament_life_s': (FILAMENT_LIFE_S, 's', '钨灯丝平均寿命300h（=1.08×10⁶s）'),
            'beta_reduced': (BETA_R, 'A/(m^2 sr V)', '钨灯丝约化亮度'), 'energy_spread_ev': (ENERGY_SPREAD_EV, 'eV', '钨灯丝能量展宽'),
            'surface_capacitance_f': (SURFACE_CAPACITANCE_F, 'F', '绝缘相荷电区域对地电容'), 'se_collection': (SE_COLLECTION, '1', 'SED收集效率'), 'bed_dead_layer_ev': (BED_DEAD_LAYER_EV, 'eV', 'BED死层能量损失'),
            'gas_sigma30_m2': (GAS_SIGMA30_M2, 'm^2', '30kV空气散射截面'), 'eds_takeoff_rad': (math.radians(35.0), 'rad', 'EDS出射角35°（WD 10mm；工程假设）'),
            'kramers_k': (KRAMERS_K, '1/eV', 'Kramers常数'), 'eds_fwhm_mn_model_ev': (fwhm_ev(5898.75, EDS_PROCESS_TIMES['T4']['noise_ev']), 'eV', '模型Mn Kα分辨率（T4）'),
            'door_travel_m': (DOOR_TRAVEL_M, 'm', '门拉出行程'), 'pole_clearance_m': (POLE_CLEARANCE_M, 'm', '样品与极靴最小间隙')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['electron_gun', 'hv_power_supply', 'condenser_lens', 'objective_lens', 'scan_coil_section', 'pole_piece', 'chamber_body', 'turbo_molecular_pump',
                       'rotary_pump', 'vacuum_valve_block', 'vent_valve', 'lv_valve', 'sed_detector', 'bse_detector', 'eds_finger', 'eds_detector_housing', 'stage_z',
                       'stage_tilt', 'stage_x', 'stage_y', 'stage_rot', 'specimen_holder', 'chamber_door'],
        state_ids=list(slots), meaning_zh='JSM-IT200 LA扫描电镜：真空（粗抽/涡轮泵/LV针阀/放气）、钨灯丝电子枪、探针光学、SE/BSE信号与噪声、荷电与LV散射、EDS谱生成与无标样ZAF定量、样品台与换样',
        implementation='lumped',
        equations=['V dP/dt = Q_leak + Q_outgas(t) + K (P_atm - P) - S P  (exact exponential step; K = door gap / vent / needle valve, S = RP, TMP or LV orifice)',
                   'd_p^2 = 4 I/(pi^2 beta a^2) + (C_s a^3/2)^2 + (C_c a dE/E0)^2 + (1.22 lambda/a)^2 at the optimum a ; C_s = c_s (WD + g) ; resolution = 0.6 d_p',
                   'eta = sum_i w_i C(Z_i) E^m(Z_i) (Hunger-Kuechler) x (2/(1+cos theta))^(9/sqrt Zbar) ; delta = 1.28 delta_m r^-0.67 (1 - exp(-1.614 r^1.67))/cos theta (total SE yield, r = E/E_m)',
                   'charging (uncoated insulator, HV, per phase k): E_land = E0 - V_k ; C dV_k/dt = I (1 - eta(E_land, theta) - delta(E_land, theta)) towards V_eq = E0 - E2(theta), sigma(E2) = 1',
                   'tilted specimen (height h, tilt t about X through the holder top): beam meets the top face h tan t from its centre, WD = 48 mm - z - h/cos t',
                   'BED: n_BSE = eta (sin^2 th_out - sin^2 th_in), gain = (k_E E - E_dl)/3.64 eV ; SED: y = 0.45 delta + 0.1 eta ; SNR from Poisson + amplifier noise',
                   'n_ion = N_A w/A int_{Ec}^{E0} Q(E)/(dE/drho s) dE x R ; Q = 6.51e-20 n_s b_s ln U/(U Ec^2) ; dE/drho s = 78500 (Z/A)/E ln(1.166 (E + 0.85 J)/J)',
                   'I_det = N_e n_ion omega p f(chi) eps(E) Omega/(4 pi) ; f = 1/((1 + chi/sigma)(1 + h chi/(sigma (1+h)))) ; N_cont = k Zbar (E0 - E)/E',
                   'FWHM^2 = noise^2 + 5.545 F eps E ; live = exp(-ICR tau) ; counts ~ Poisson ; Duane-Hunt: N(E) E / eps(E) ~ k (E_DH - E) near the end, extrapolated',
                   'quant: C_i = norm( A_i / P_i(C) ), P_i = omega p n_s b_s (U0-1)^1.67 / (A_i (Z/A) ln(1.166 (E0 + Ec)/(2 J))) (1 - 0.4 eta (1 - 1/U0)) f_Philibert(h) eps'],
        parameters=params, source_ids=[_SIM, _FIT, _LIBS, _CAT, _MAN, _IA, _PHYS], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_jsmit200_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'jsmit200_sem_eds': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'specimen.provide': (SPECIMEN, SPECIMEN_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'jsmit200_sem_eds': mechanism})
