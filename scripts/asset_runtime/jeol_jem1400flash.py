"""CHEM-152 JEOL JEM-1400Flash HR (120 kV transmission electron microscope, W hairpin gun, HR objective pole piece, TMP vacuum, side-entry
goniometer with a specimen airlock, Matataki Flash sCMOS bottom camera): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time; instrument cycle 1 s):
  * power and vacuum: MAIN POWER switch, TEM Center start-up, column pumped by the TMP (lumped volume, outgassing, gun behind a
    differential aperture), specimen airlock pre-evacuated by the rotary pump through the PUMP/AIR lever (Evac Ready lamp), the gas of the
    airlock entering the column when the holder is turned in; HT trip on gun vacuum; cooling-water and mains interlocks;
  * gun: HT 10-120 kV ramped by the HT supply (dark current through the 1.818 GOhm divider: 44 uA at 80 kV and 66 uA at 120 kV, facility
    procedures), W hairpin filament heated to saturation, emission set by the bias, filament life (Weibull) and burnout;
  * illumination: spot size (condenser 1), brightness (condenser 2: illuminated-area diameter about the crossover), condenser aperture;
    current and current density at the specimen, screen current density (TEM Center reading = specimen current density x transmission / M^2);
  * imaging: LOW MAG (x10-x1,000) and MAG (x200-x1,500,000) steps, objective aperture (out / 4 sizes), standard focus, displayed and true
    defocus (grid height and stage Z against the eucentric plane), auto focus; the specimen is a 3 mm mesh grid with a support film and
    up to four phases (particles, films, sections) of library substances; bright-field transmission = exp(-sum n sigma_el(>alpha) t)
    (Lenz-Wentzel screened elastic cross-section outside the objective-aperture angle) x (1 - Bragg scattering outside the aperture)
    (kinematic / two-beam intensities with extinction distances from the library structure factors, per-particle orientations, the
    holder tilt, the path thickness of tilted films t / cos(tilt) and of spheroidal particles, the 18 um grid-bar walls); camera dose,
    Poisson noise, 16-bit counts, grey-level regions (classes = observable composition at each point, overlaps included, merged when the
    noise cannot separate them; no phase names), lattice fringes of axial illumination visible when the reflection passes the aperture, is
    excited and survives the temporal-coherence envelope and the spatial-coherence envelope of the illumination convergence at the local
    defocus (the catalogue 0.14 nm needs tilted achromatic illumination, not modelled), Thon-ring frequencies of amorphous films (defocus, Cs,
    temporal and spatial coherence envelopes); the selected-area aperture limits the field in MAG and DIFF;
  * diffraction: selected-area aperture (out / 4 sizes), camera-length steps with an instrument calibration error, rings of polycrystalline
    phases, spots of single crystals near their zone axis, diffuse halos of amorphous material, central beam;
  * beam damage (radiolysis) from the electron fluence accumulated at every point of the specimen along the actual stage path (swept beam
    footprints): mass loss of organic films, stains and resins (the uranyl stain keeps its uranium) and fading of the crystallinity of
    beam-sensitive crystals (Egerton characteristic doses, scaled with 1/beta^2 to the beam energy);
  * specimen exchange: grid in the holder on the bench, holder to the airlock stop, pre-evacuation, turn-in to the column (only when the
    airlock is evacuated, filament off), withdrawal, venting, removal; standard quick-change holder (+-20 deg with the HR pole piece) and
    the optional high-tilt holder (+-70 deg); goniometer X / Y / Z and tilt with stage neutral; fluorescent screen (F1) and camera;
  * limitless-panorama montage (stage-driven tiles); charge (beam dose) and gas ledgers.
"""
from __future__ import annotations

import copy
import functools
import json
import math
import random
from pathlib import Path

import numpy as np

THUMB = 32
PROTOCOL_VERSION = '1.0.0'
SLUG = 'jeol_jem1400flash'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-152'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t3_analytical' / SLUG
LIBRARY_PATH = ASSET / 'tem_library.json'
LIBRARY_REL = str(LIBRARY_PATH.relative_to(ROOT))

E_CHARGE = 1.602176634e-19
M_E = 9.1093837015e-31
C_LIGHT = 299792458.0
H_PLANCK = 6.62607015e-34
A0 = 5.29177210903e-11
N_AVOGADRO = 6.02214076e23
K_BOLTZMANN = 1.380649e-23
GAS_CONSTANT = 8.314462618
P_ATM = 101325.0
CYCLE_S = 1.0
CYCLE_NS = 1_000_000_000
MAX_EVENTS = 500
# ---------------------------------------------------------------- documented (JEOL JEM-1400Flash brochure / product page; JEM-1400Plus brochure of the same platform)
KV_RANGE_V = (10000.0, 120000.0)                   # Flash: 10 to 120 kV
KV_STEP_V = 100.0 / 3.0                            # Plus: minimum step 33 V (related model)
LATTICE_RESOLUTION_M = 0.14e-9                     # Flash HR: TEM resolution 0.14 nm (lattice image)
MAG_MAX = 1.5e6                                    # Flash HR: x10 to x1,500,000
LOW_MAG_STEPS = [10, 12, 15, 20, 25, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250, 300, 400, 500, 600, 800, 1000]   # Plus LOW MAG x10-1,000
MAG_STEPS = [200, 250, 300, 400, 500, 600, 800, 1000, 1200, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 8000, 10000, 12000, 15000, 20000, 25000,
             30000, 40000, 50000, 60000, 80000, 100000, 120000, 150000, 200000, 250000, 300000, 400000, 500000, 600000, 800000, 1000000, 1200000, 1500000]
TILT_LIMITS_RAD = {'quick_change': math.radians(20.0), 'high_tilt': math.radians(70.0)}   # ETH HR JEM-1400Plus: quick change +-20 deg; brochure: EM-11230HTH +-70 deg
CAMERA_PIXELS = 2048                               # Matataki Flash: 2048 x 2048, effective pixel 21.7 um, readout noise 0.8 e, 30 fps
CAMERA_PIXEL_M = 21.7e-6
CAMERA_READ_NOISE_E = 0.8
CAMERA_FPS = 30.0
MAINS_NOMINAL_V = 200.0                            # Plus: single phase AC 200 V +-? (5 kVA); tolerance of the JEOL installation sheets not given -> +-10 % engineering
MAINS_TOL = 0.10
COOLING_FLOW_MIN_M3_S = 5.0 / 60000.0                       # Plus: water flow 5 L/min
ROOM_RANGE_K = (288.15, 298.15)                    # Plus: room 15-25 C
DARK_CURRENT_R_OHM = 80000.0 / 44e-6               # facility procedures: 44 uA at 80 kV, 66 uA at 120 kV -> 1.818 GOhm HT divider
METER_DARK_NOISE_A = 0.1e-6                       # beam-current meter fluctuation (engineering; the facility reads the dark current to +-2 uA between sessions)
METER_BEAM_NOISE_A = 0.05e-6                      # extra fluctuation of the emission part on the same meter (engineering)
# ---------------------------------------------------------------- engineering (not published for this model)
CAMERA_LENGTHS_M = [0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.80, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]
CAMERA_LENGTH_ERROR_SD = 0.02                      # nominal vs. calibrated camera length (per instrument, drawn from the noise seed)
ADU_PER_ELECTRON = 8.0                             # 16-bit camera counts per incident electron (scintillator + sCMOS gain)
ADU_MAX = 65535
CAMERA_DQE = 0.5
CAMERA_READOUT_S = 0.2
OBJ_FOCAL_M = 2.0e-3
CS_M = 1.0e-3                                      # HR pole piece spherical aberration (typical 120 kV HR value)
CC_M = 1.2e-3
ENERGY_SPREAD_J = 2.0 * 1.602176634e-19            # W hairpin: 2 eV FWHM (the focus spread uses the rms = FWHM / 2.355)
OL_APERTURES_M = [120e-6, 60e-6, 40e-6, 20e-6]     # objective aperture diameters (positions 1-4); 0 = out
OL_OUT_ANGLE_RAD = 0.06                            # effective cut-off by the lens bore with the objective aperture out
SA_APERTURES_M = [100e-6, 50e-6, 20e-6, 10e-6]     # selected-area aperture diameters (positions 1-4); 0 = out
SA_MAGNIFICATION = 30.0                            # objective magnification at the selected-area plane
CL_APERTURE_M = 100e-6
SPOT_FACTORS = [1.0, 0.5, 0.25, 0.12, 0.06]        # condenser-1 spot sizes 1-5: relative current
GUN_TRANSMISSION = 3.0e-4                          # specimen current / emission at spot 1 with the condenser aperture in (about 2 nA)
CL_OUT_FACTOR = 3.0
ILLUM_D_MIN_M = 0.3e-6                             # crossover diameter at spot 1 (scales with the spot current^0.5)
ILLUM_D_MAX_M = 200e-6
EMISSION_BIAS0_A = 20e-6                           # emission at saturation: 20 uA exp(-bias/33) (bias 40 -> 6 uA, facility: 5-7 uA above the dark current)
BIAS_SCALE = 33.0
HEAT_S = 20.0                                      # W hairpin to saturation after FILAMENT ON
FILAMENT_LIFE_H = 150.0
FILAMENT_LIFE_S = FILAMENT_LIFE_H * 3600.0
FILAMENT_WEIBULL_K = 3.0
HT_RAMP_V_S = 1000.0
SOFTWARE_BOOT_S = 90.0
V_COLUMN_M3 = 0.020
S_TMP_M3_S = 0.050
TMP_SPINUP_S = 180.0
Q_COLUMN_PA_M3_S = 1.0e-6                          # column outgassing + permeation (base about 2e-5 Pa)
COL_READY_PA = 1.0e-4
GUN_FACTOR = 0.3                                   # gun pressure / column pressure across the differential aperture
GUN_TRIP_PA = 3.0e-4
V_AIRLOCK_M3 = 5.0e-6
AIRLOCK_TAU_S = 4.0                                # rotary-pump pre-evacuation time constant of the airlock
AIRLOCK_VENT_TAU_S = 1.0
AIRLOCK_READY_PA = 5.0
AIRLOCK_ULT_PA = 0.5
P_START_IDLE_PA = 1.0e-2                           # column left under vacuum while unpowered (slow leak)
LEAK_IDLE_PA_M3_S = 2.0e-7
STAGE_XY_RANGE_M = (-1.0e-3, 1.0e-3)
STAGE_Z_RANGE_M = (-0.2e-3, 0.2e-3)
STAGE_XY_SPEED = 50e-6
STAGE_Z_SPEED = 10e-6
STAGE_TILT_SPEED = math.radians(2.0)
HOLDER_STROKE_M = dict(column=0.0, airlock=0.042, removed=0.360)
HOLDER_SPEED_M_S = 0.030                           # guided turn-in / withdrawal by hand
GRID_Z_SD_M = 5e-6                                 # height of the mounted grid against the eucentric plane
B_DEFAULT_A2 = 0.5
AUTOFOCUS_SD_M = 30e-9
DEFOCUS_RANGE_M = (-20e-6, 20e-6)
MONTAGE_SETTLE_S = 1.0
GRID_MESH = {200: dict(pitch=125e-6, bar=35e-6), 300: dict(pitch=83e-6, bar=25e-6), 400: dict(pitch=62e-6, bar=20e-6)}
GRID_RADIUS_M = 1.45e-3                            # open area of a 3.05 mm grid (the rim is solid)
GRID_THICKNESS_M = 18e-6


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


# ---------------------------------------------------------------- template library (COD structures, xraydb Waasmaier-Kirfel form factors, NIST SRD 126)
_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        raw = json.loads(LIBRARY_PATH.read_text())
        subs = {}
        for s in raw['substances']:
            s = dict(s)
            cr = s.get('crystal')
            if cr:
                a, b, c, al, be, ga = cr['cell_A']
                cr['_A'] = _cell_matrix(a, b, c, al, be, ga) * 1e-10          # rows: direct lattice vectors (m)
                cr['_B'] = np.linalg.inv(cr['_A']).T                           # rows: reciprocal vectors (1/m, no 2 pi)
                cr['_rot'] = [np.array(M, int) for M in cr['reciprocal_rotations']]
                refl = []
                for h, k, l, d_nm, F, m in cr['reflections']:
                    eq = {tuple(int(v) for v in np.array([h, k, l]) @ M) for M in cr['_rot']}
                    refl.append(dict(hkl=(h, k, l), d=d_nm * 1e-9, F=F * 1e-10, m=m, eq=sorted(eq)))
                cr['_refl'] = refl
                cr['_vol'] = cr['volume_A3'] * 1e-30
            subs[s['substance_id']] = s
        _LIB = dict(meta={k: v for k, v in raw.items() if k not in ('elements', 'substances')}, elements=raw['elements'], substances=subs)
    return _LIB


def _cell_matrix(a, b, c, al, be, ga):
    al, be, ga = map(math.radians, (al, be, ga))
    cx = c * math.cos(be)
    cy = c * (math.cos(al) - math.cos(be) * math.cos(ga)) / math.sin(ga)
    return np.array([[a, 0.0, 0.0], [b * math.cos(ga), b * math.sin(ga), 0.0], [cx, cy, math.sqrt(max(c * c - cx * cx - cy * cy, 0.0))]])


def library():
    lib = _lib()
    out = {}
    for sid, s in lib['substances'].items():
        out[sid] = {k: copy.deepcopy(v) for k, v in s.items() if k != 'crystal'}
        if s.get('crystal'):
            out[sid]['crystal'] = {k: copy.deepcopy(v) for k, v in s['crystal'].items() if not k.startswith('_')}
    return dict(meta=copy.deepcopy(lib['meta']), substances=out, elements=sorted(lib['elements']))


def substance(sid):
    s = _lib()['substances'].get(sid)
    if s is None:
        raise ActionRejected('UNKNOWN_SUBSTANCE')
    return s


def element(sym):
    return _lib()['elements'][sym]


# ---------------------------------------------------------------- electron optics and scattering
def wavelength_m(v):
    """Relativistic electron wavelength for accelerating voltage v (V)."""
    e = E_CHARGE * v
    return H_PLANCK / math.sqrt(2.0 * M_E * e * (1.0 + e / (2.0 * M_E * C_LIGHT ** 2)))


def gamma_of(v):
    return 1.0 + E_CHARGE * v / (M_E * C_LIGHT ** 2)


def beta2_of(v):
    g = gamma_of(v)
    return 1.0 - 1.0 / (g * g)


@functools.lru_cache(maxsize=4096)
def sigma_el_outside(z, v, alpha):
    """Lenz-Wentzel screened elastic cross-section (m^2) for scattering outside the semi-angle alpha (rad):
    sigma(>alpha) = gamma^2 Z^2 lambda^4 / (4 pi^3 a0^2) / (alpha^2 + theta0^2), theta0 = lambda Z^(1/3) / (2 pi a0)."""
    lam, g = wavelength_m(v), gamma_of(v)
    th0 = lam * z ** (1.0 / 3.0) / (2.0 * math.pi * A0)
    return g * g * z * z * lam ** 4 / (4.0 * math.pi ** 3 * A0 * A0) / (alpha * alpha + th0 * th0)


@functools.lru_cache(maxsize=512)
def atom_densities(sid):
    """Number density (1/m^3) of each element of a library substance."""
    s = substance(sid)
    rho = s['density_g_cm3'] * 1000.0
    return tuple((e, rho * w / (element(e)['molar_mass_g_mol'] * 1e-3) * N_AVOGADRO) for e, w in s['composition_mass_fraction'].items())


@functools.lru_cache(maxsize=4096)
def mu_el_outside(sid, v, alpha):
    """Elastic attenuation coefficient (1/m) for scattering outside the objective-aperture semi-angle alpha."""
    return sum(n * sigma_el_outside(element(e)['Z'], v, alpha) for e, n in atom_densities(sid))


@functools.lru_cache(maxsize=4096)
def mu_el_retained(sid, v, alpha):
    """Attenuation (1/m) of the part of a substance that radiolysis leaves in place (damage.retained_mass_fraction: element -> mass fraction)."""
    s = substance(sid)
    ret = s['damage'].get('retained_mass_fraction') or {}
    rho = s['density_g_cm3'] * 1000.0
    return sum(rho * w / (element(e)['molar_mass_g_mol'] * 1e-3) * N_AVOGADRO * sigma_el_outside(element(e)['Z'], v, alpha) for e, w in ret.items())


def mu_damaged(sid, v, alpha, m):
    """Attenuation (1/m of geometric thickness) after mass loss to the mass multiplier m (scalar or array): the whole substance thins uniformly, or,
    when the library lists retained elements (uranyl stain: U and the uranyl O), only the volatile part is lost (m = r + (1 - r) f)."""
    dmg = substance(sid)['damage']
    mu = mu_el_outside(sid, v, alpha)
    if not dmg.get('retained_mass_fraction'):
        return mu * np.asarray(m, float)
    r = dmg['residual_fraction']
    mr = mu_el_retained(sid, v, alpha)
    return mr + (mu - mr) * (np.asarray(m, float) - r) / (1.0 - r)


def extinction_distance(cr, r, v):
    """Two-beam extinction distance (m): xi_g = pi V_c cos(theta_B) / (lambda gamma |F_g|) (F in metres, non-relativistic in the library)."""
    lam = wavelength_m(v)
    return math.pi * cr['_vol'] / (lam * gamma_of(v) * max(r['F'], 1e-30))


def two_beam_intensity(t, s, xi):
    """Diffracted-beam intensity of one reflection (two-beam, absorption-free): (pi/xi)^2 sin^2(pi t s_eff) / (pi s_eff)^2."""
    se = math.sqrt(s * s + 1.0 / (xi * xi))
    return (math.pi / xi) ** 2 * math.sin(math.pi * t * se) ** 2 / (math.pi * se) ** 2


def chi(k, dz, v):
    """Phase shift of the objective lens (rad) at spatial frequency k (1/m): chi = pi lambda dz k^2 - pi/2 Cs lambda^3 k^4 (dz > 0 underfocus)."""
    lam = wavelength_m(v)
    return math.pi * lam * dz * k * k - 0.5 * math.pi * CS_M * lam ** 3 * k ** 4


def focus_spread_m(v):
    """Chromatic focus spread (m, rms): Cc sigma_E / (e V), sigma_E = FWHM / (2 sqrt(2 ln 2))."""
    return CC_M * ENERGY_SPREAD_J / (2.0 * math.sqrt(2.0 * math.log(2.0))) / (E_CHARGE * v)


def temporal_envelope(k, v):
    lam = wavelength_m(v)
    return math.exp(-0.5 * (math.pi * lam * focus_spread_m(v)) ** 2 * k ** 4)


def spatial_envelope(k, dz, v, conv):
    """Partial spatial coherence (Frank): exp(-(pi conv)^2 (dz k - Cs lambda^2 k^3)^2) for the illumination convergence semi-angle conv (rad)."""
    lam = wavelength_m(v)
    return math.exp(-(math.pi * conv) ** 2 * (dz * k - CS_M * lam * lam * k ** 3) ** 2)


def thon_zeros(dz, v, k_max, n_max=6):
    """Spatial frequencies (1/m) of the first CTF zeros sin(chi) = 0 below k_max."""
    lam = wavelength_m(v)
    out = []
    a, b = 0.5 * math.pi * CS_M * lam ** 3, math.pi * lam * dz
    for n in range(0, 60):
        for target in ((0.0,) if n == 0 else (n * math.pi, -n * math.pi)):
            # chi(k) = b k^2 - a k^4 = target  ->  a u^2 - b u + target = 0, u = k^2
            disc = b * b - 4.0 * a * target
            if disc < 0:
                continue
            for u in ((b - math.sqrt(disc)) / (2 * a), (b + math.sqrt(disc)) / (2 * a)):
                if u > 0:
                    k = math.sqrt(u)
                    if k <= k_max:
                        out.append(k)
    out = sorted(set(round(k, 3) for k in out))
    return out[:n_max]


def damage_scale(dmg, v):
    """Characteristic dose (C/m^2) at the beam energy: radiolysis cross-section scales with 1/beta^2 (Egerton)."""
    key = 'de_c_cm2' if dmg['mode'] == 'mass_loss' else 'dc_c_cm2'
    d_ref = dmg[key] * 1e4
    v_ref = dmg['e_ref_kev'] * 1e3
    return d_ref * beta2_of(v) / beta2_of(v_ref)


def _hash01(*ints):
    x = 0x9E3779B97F4A7C15
    for i in ints:
        x ^= (int(i) & 0xFFFFFFFFFFFFFFFF) + 0x9E3779B97F4A7C15 + ((x << 6) & 0xFFFFFFFFFFFFFFFF) + (x >> 2)
        x &= 0xFFFFFFFFFFFFFFFF
        x = (x * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 31
    return (x & 0xFFFFFFFFFFFF) / float(0x1000000000000)


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c) if isinstance(c, (list, tuple)) else [c], function_zh=meaning,
                   visibility=visibility, kind=kind, effects=[])


MAX_RECORDS = 200
MAX_SPECIMENS = 20
TILE_PX = 16
HOLDERS = ['quick_change', 'high_tilt']
SUPPORT_FILMS = ['none', 'amorphous_carbon', 'formvar', 'silicon_nitride_amorphous']
FORMS = ['particles', 'film', 'section']
CRYSTALLINITY = ['amorphous', 'polycrystalline', 'single_crystal']
PLANT_EXAMPLE = dict(room_k=295.15, relative_humidity=0.45, mains_voltage_v=200.0, cooling_water_m3_s=5.0 / 60000.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 308.15), relative_humidity=number('1', 0.0, 1.0), mains_voltage_v=number('V', 0.0, 260.0),
                 cooling_water_m3_s=number('m^3/s', 0.0, 20.0 / 60000.0), noise_seed=INT))
PHASE_ITEM = obj(dict(substance_id=TEXT, form=enum(FORMS), size_m=number('m', 1e-9, 5e-6), thickness_m=number('m', 1e-9, 5e-7),
                      coverage=number('1', 0.0, 1.0), crystallinity=enum(CRYSTALLINITY), zone_axis=nullable(array(integer(-6, 6), 3, 3)),
                      stain_mass_fraction=number('1', 0.0, 0.5)))
SPECIMEN = obj(dict(specimen_id=dict(type='string', minLength=1, maxLength=40), grid_mesh=enum(['200', '300', '400']), grid_material=enum(['copper', 'nickel', 'gold']),
                    support_film=enum(SUPPORT_FILMS), support_thickness_m=number('m', 0.0, 1e-7), hole_fraction=number('1', 0.0, 0.6),
                    phases=array(PHASE_ITEM, 0, 4)))
SPECIMEN_EXAMPLE = dict(specimen_id='au_np_on_carbon', grid_mesh='200', grid_material='copper', support_film='amorphous_carbon', support_thickness_m=1.0e-8,
                        hole_fraction=0.0, phases=[dict(substance_id='gold', form='particles', size_m=2.0e-8, thickness_m=2.0e-8, coverage=0.15,
                                                        crystallinity='single_crystal', zone_axis=None, stain_mass_fraction=0.0)])
OPERATION = obj(dict(operation_id=TEXT))
SOURCE = obj(dict(library=TEXT, substances=array(TEXT, 0, 8)))
REGION = obj(dict(region_id=TEXT, area_fraction_in_view=number('1', 0.0, 1.0), mean_grey=number('1', 0.0, 255.0), mean_counts=number('1', 0.0, ADU_MAX),
                  snr=number('1', 0.0), lattice_spacings_m=array(number('m', 0.0), 0, 8)))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
IMAGE = obj(dict(image_id=TEXT, status=STATUS, mode=enum(['LOW_MAG', 'MAG']), accelerating_voltage_v=number('V'), magnification=number('1'),
                 exposure_s=number('s'), binning=integer(1, 4), pixel_size_m=number('m'), field_of_view_m=number('m'), defocus_display_m=number('m'),
                 objective_aperture_position=integer(0, 4), tilt_rad=number('rad'), dose_estimate_e_per_m2=nullable(number('1/m^2')), regions=array(REGION, 0, 8),
                 thon_ring_frequencies_per_m=array(number('1/m'), 0, 8), saturated_fraction=nullable(number('1', 0.0, 1.0)),
                 thumbnail=array(array(integer(0, 255), THUMB, THUMB), 0, THUMB), error_code=nullable(TEXT), started_s=number('s'),
                 finished_s=nullable(number('s')), template_source=SOURCE))
RING = obj(dict(radius_m=number('m', 0.0), radius_px=number('1', 0.0), d_spacing_display_m=number('m', 0.0), relative_intensity=number('1', 0.0, 1.0),
                width_px=number('1', 0.0)))
SPOT = obj(dict(x_m=number('m'), y_m=number('m'), relative_intensity=number('1', 0.0, 1.0)))
PATTERN = obj(dict(pattern_id=TEXT, status=STATUS, accelerating_voltage_v=number('V'), camera_length_display_m=number('m'), exposure_s=number('s'),
                   binning=integer(1, 4), selected_area_diameter_m=nullable(number('m')), tilt_rad=number('rad'), rings=array(RING, 0, 40),
                   spots=array(SPOT, 0, 80), halos=array(RING, 0, 6), central_beam_saturated=nullable(BOOL), error_code=nullable(TEXT),
                   started_s=number('s'), finished_s=nullable(number('s')), template_source=SOURCE))
MONTAGE = obj(dict(montage_id=TEXT, status=STATUS, rows=integer(1, 8), cols=integer(1, 8), overlap=number('1'), magnification=number('1'),
                   exposure_s=number('s'), binning=integer(1, 4), tile_field_of_view_m=number('m'), tile_step_m=number('m'),
                   mosaic=array(array(integer(0, 255), 0, 8 * TILE_PX), 0, 8 * TILE_PX), regions=array(REGION, 0, 8), error_code=nullable(TEXT),
                   started_s=number('s'), finished_s=nullable(number('s')), template_source=SOURCE))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'vacuum', 'gun', 'stage', 'holder', 'specimen', 'optics', 'acquisition', 'warning', 'error',
                                                'maintenance']), code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['beam_charge_c', 'gun_condenser_stopped_charge_c', 'specimen_plane_charge_c', 'grid_stopped_charge_c', 'objective_aperture_stopped_charge_c',
               'selected_area_aperture_stopped_charge_c', 'detector_charge_c', 'charge_residual_c', 'gas_admitted_mol', 'gas_pumped_mol', 'gas_released_mol', 'gas_in_volume_mol', 'gas_residual_mol',
               'filament_on_time_s', 'airlock_cycles']
LEDGER = obj({k: number('C' if k.endswith('_c') else 'mol' if k.endswith('_mol') else 's' if k.endswith('_s') else '1') for k in LEDGER_KEYS})

cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['power_supply_console', 'rear_console'],
    '宿主声明实验室条件：室温（安装要求15–25°C）、相对湿度（≤60%）、供电电压（单相AC 200V，5kVA）、冷却水流量（5L/min=8.33×10⁻⁵m³/s）（同平台JEM-1400Plus安装要求）与噪声随机数种子'
    '（只在首次声明时生效）', 'evaluator', 'configuration')
cap('specimen.provide', SPECIMEN, obj(dict(specimen_id=TEXT)), SPECIMEN_EXAMPLE, ['holder_tip'],
    '宿主在实验台上放入一个载网样品：3mm铜/镍/金网（200/300/400目）、支持膜（无/无定形碳/Formvar/无定形氮化硅，可带孔）与最多4个相（模板库物质；颗粒/薄膜/切片；'
    '尺寸、厚度、覆盖率；非晶/多晶/单晶及晶带轴；非晶相的铀染色质量分数）', 'evaluator', 'configuration')
cap('holder.load_grid', obj(dict(holder_id=enum(HOLDERS), specimen_id=TEXT)), obj(dict(holder_id=TEXT, specimen_id=TEXT)),
    dict(holder_id='quick_change', specimen_id='au_np_on_carbon'), ['holder_tip', 'holder_tilt'],
    '在实验台上用镊子把载网装进样品杆尖端（标准快换杆或选配高倾转杆；样品杆配置参照厂家与同平台JEM-1400Plus资料）；样品杆须在实验台上且空着', kind='robot_skill')
cap('holder.unload_grid', obj(dict(holder_id=enum(HOLDERS))), obj(dict(holder_id=TEXT, specimen_id=TEXT)), dict(holder_id='quick_change'), ['holder_tip'],
    '从实验台上的样品杆卸下载网放回实验台（样品杆配置参照同平台JEM-1400Plus资料）', kind='robot_skill')
cap('holder.insert_to_airlock', obj(dict(holder_id=enum(HOLDERS))), obj(dict(holder_id=TEXT, position=TEXT)), dict(holder_id='quick_change'),
    ['specimen_holder', 'goniometer'], '样品杆导销对准测角台开口推到底（气锁止点，传感器咔哒）：O形圈把换样气锁封住；须测角台空着、灯丝关、样品台在中性位（设施规程）', kind='robot_skill')
cap('airlock.set_pump', obj(dict(pump=BOOL)), obj(dict(pump=BOOL)), dict(pump=True), ['airlock_lever', 'airlock_box'],
    '换样气锁PUMP/AIR拨杆：PUMP由机械泵预抽气锁（抽好后Evac Ready绿灯亮），AIR使气锁放气；样品杆不在气锁止点时只改拨杆位置；阀门动作需要通电')
cap('holder.insert', obj({}), obj(dict(position=TEXT)), {}, ['specimen_holder', 'goniometer'],
    '顺时针转两次把样品杆从气锁送入镜筒（载网到物镜极靴间隙）：只在气锁已抽好（Evac Ready）、灯丝关、通电时允许；气锁残余气体进入镜筒', kind='robot_skill')
cap('holder.withdraw', obj({}), obj(dict(position=TEXT)), {}, ['specimen_holder', 'goniometer'],
    '拉出到第一止点逆时针转、再短拉逆时针转回到气锁止点；须灯丝关、样品台在中性位（设施规程），物镜光阑插着时记警告', kind='robot_skill')
cap('holder.remove', obj({}), obj(dict(holder_id=TEXT)), {}, ['specimen_holder', 'goniometer'], '气锁放气（AIR）后把样品杆完全拔出放回实验台', kind='robot_skill')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['switch_main_power', 'power_supply_console'],
    '主电源（供电要求参照同平台JEM-1400Plus安装资料）：接通后约90s TEM Center加载、镜筒由涡轮分子泵抽真空；断开时高压与灯丝随之关闭、真空阀关闭')
cap('power.key_toggle', obj({}), obj(dict(enabled=BOOL)), {}, ['switch_main_power'], '电源柜MAIN POWER开关按一下：断电时接通、通电时断开（电源柜按同平台JEM-1400Plus资料）')
cap('error.acknowledge', obj({}), obj(dict(acknowledged=TEXT)), {}, 'monitor', '确认并清除待处理的错误代码（高压跳闸、灯丝烧断、供电异常、冷却水不足；供电与冷却水要求参照同平台JEM-1400Plus安装资料）')
cap('ht.set_voltage', obj(dict(voltage_v=number('V', KV_RANGE_V[0], KV_RANGE_V[1]))), obj(dict(voltage_v=number('V'))), dict(voltage_v=120000.0),
    ['gun_chamber', 'rear_console'], '设定加速电压10–120kV（按33V最小步进取整，同平台规格）；高压开着时按1kV/s升降（工程）')
cap('ht.set_on', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['gun_chamber', 'rear_console'],
    'TEM Center的HT ON/OFF：开须真空就绪、冷却水足够（5L/min=8.33×10⁻⁵m³/s，同平台JEM-1400Plus安装要求）；开后按1kV/s升到设定值，暗电流=HT/1.818GΩ（设施：80kV 44µA、120kV 66µA）；'
    '关高压时灯丝随之关闭')
cap('filament.set_on', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['gun_chamber'],
    '灯丝开/关：开须高压已到设定值、真空就绪、灯丝未烧断、样品杆不在插拔中；开后约20s加热到饱和，发射电流由偏压决定')
cap('filament.set_bias', obj(dict(bias=number('1', 0.0, 100.0))), obj(dict(bias=number('1'))), dict(bias=40.0), ['gun_chamber'],
    '设定偏压档（0–100）：饱和发射≈20µA·exp(−偏压/33)（偏压40约6µA；设施规程：束流读数比暗电流高5–7µA，常用起始偏压40）')
cap('gun.replace_filament', obj({}), obj(dict(filament_serial=INT)), {}, ['gun_chamber'], '维护：更换钨灯丝（须高压关）；新灯丝寿命按Weibull分布随机')
cap('optics.set_mode', obj(dict(mode=enum(['LOW_MAG', 'MAG', 'DIFF']))), obj(dict(mode=TEXT)), dict(mode='MAG'), ['objective_lens', 'intermediate_lens', 'projector_lens'],
    '选择LOW MAG（×10–×1,000，物镜关）、MAG（×200–×1,500,000）或选区衍射DIFF（只能从MAG进入）；各模式保留各自的倍率/相机长度档（LOW MAG范围与MAG下限为同平台JEM-1400Plus数据）')
cap('optics.toggle_diffraction', obj({}), obj(dict(mode=TEXT)), {}, ['button_diff', 'intermediate_lens'], 'DIFF键：MAG与选区衍射之间切换（LOW MAG下被拒）')
cap('optics.set_magnification', obj(dict(magnification=number('1', 10.0, MAG_MAX))), obj(dict(magnification=number('1'), mode=TEXT)), dict(magnification=30000.0),
    ['intermediate_lens', 'projector_lens'], '设定放大倍数：取当前成像模式最近的档位（LOW MAG ×10–×1,000与MAG下限×200为同平台JEM-1400Plus数据，MAG上限×1,500,000为Flash HR）；衍射模式下被拒（用相机长度）')
cap('optics.set_camera_length', obj(dict(camera_length_m=number('m', 0.05, 3.5))), obj(dict(camera_length_m=number('m'))), dict(camera_length_m=0.5),
    ['intermediate_lens', 'projector_lens'], '设定衍射相机长度：取最近档位（0.08–3.0m，工程档位）；显示值与实际值有仪器标定误差')
cap('optics.set_magnification_step_fraction', obj(dict(fraction=number('1', 0.0, 1.0))),
    obj(dict(fraction=number('1'), magnification=nullable(number('1')), camera_length_m=nullable(number('m')))), dict(fraction=0.5),
    ['knob_magnification'], 'MAGNIFICATION旋钮：位置（0–1）按当前模式的档位表取最近档（成像为倍率，档位范围部分为同平台JEM-1400Plus数据；衍射为相机长度）；旋钮停在手松开的位置（返回的fraction即旋钮位置）')
cap('optics.set_brightness', obj(dict(value=number('1', 0.0, 1.0))), obj(dict(value=number('1'))), dict(value=0.8),
    ['knob_brightness', 'condenser_lens'], 'BRIGHTNESS旋钮（第二聚光镜）：0.5为会聚到交叉点，向两侧展开；照明区直径=交叉点直径+(200µm−交叉点)·|2值−1|（工程）；LOW MAG下约20倍宽')
cap('optics.set_spot_size', obj(dict(spot=integer(1, 5))), obj(dict(spot=INT)), dict(spot=1), ['upper_column'],
    '束斑号1–5（第一聚光镜）：号越大束流越小、交叉点越小（相对束流1、0.5、0.25、0.12、0.06，工程）')
cap('optics.set_defocus', obj(dict(defocus_m=number('m', DEFOCUS_RANGE_M[0], DEFOCUS_RANGE_M[1]))), obj(dict(defocus_m=number('m'))), dict(defocus_m=1.0e-6),
    ['knob_focus', 'objective_lens'], 'FOCUS旋钮：设定相对标准聚焦的离焦显示值（±20µm，正值为欠焦）；真实离焦还取决于载网高度、样品台Z与倾转')
cap('optics.standard_focus', obj({}), obj(dict(defocus_m=number('m'))), {}, ['button_standard_focus', 'objective_lens'],
    'STD FOCUS键：物镜回到当前电压下对准优中心面的标准电流，离焦显示归零')
cap('optics.auto_focus', obj({}), obj(dict(defocus_m=number('m'))), {}, ['objective_lens', 'scmos_camera'],
    '自动聚焦（相机）：须有束流、荧光屏抬起、MAG模式、视场里有样品结构；把真实离焦调到约0（误差30nm，1σ，工程），离焦显示随之改变')
cap('aperture.set_condenser', obj(dict(inserted=BOOL)), obj(dict(inserted=BOOL)), dict(inserted=True), ['cl_aperture_lever', 'cl_aperture_drive'],
    '聚光镜光阑拨杆：拨到左插入（100µm，工程）、拨到右退出（退出时样品束流约为插入时3倍，工程）')
cap('aperture.set_objective', obj(dict(position=integer(0, 4))), obj(dict(position=INT, diameter_m=nullable(number('m')))), dict(position=2),
    ['ol_aperture_dial', 'ol_aperture_drive'], '物镜光阑拨盘：0=退出（红点），1–4=120/60/40/20µm（工程）；收集半角=直径/(2×2mm焦距)，决定明场像的散射与衍射衬度；'
    'LOW MAG下插入会挡住大部分视场')
cap('aperture.set_selected_area', obj(dict(position=integer(0, 4))), obj(dict(position=INT, selected_diameter_m=nullable(number('m')))), dict(position=2),
    ['sa_aperture_dial', 'sa_aperture_drive'], '选区光阑拨盘：0=退出，1–4=100/50/20/10µm（工程）；样品上的选区直径=孔径/30')
cap('screen.set', obj(dict(up=BOOL)), obj(dict(up=BOOL)), dict(up=True), ['viewing_screen'], '抬起（相机采集）或放下（荧光屏观察、测电流密度）大荧光屏，约0.5s到位')
cap('screen.toggle', obj({}), obj(dict(up=BOOL)), {}, ['button_screen_up', 'viewing_screen'], 'F1键：切换荧光屏抬起/放下')
cap('stage.move_xy', obj(dict(x_m=number('m', STAGE_XY_RANGE_M[0], STAGE_XY_RANGE_M[1]), y_m=number('m', STAGE_XY_RANGE_M[0], STAGE_XY_RANGE_M[1]))),
    obj(dict(x_m=number('m'), y_m=number('m'))), dict(x_m=1e-4, y_m=-5e-5), ['goniometer', 'trackball_unit'],
    '测角台X/Y移动到目标（±1mm，工程；轨迹球或TEM Center）：只写目标，位置随step推进（50µm/s，工程）；须样品杆在镜筒内')
cap('stage.set_z', obj(dict(z_m=number('m', STAGE_Z_RANGE_M[0], STAGE_Z_RANGE_M[1]))), obj(dict(z_m=number('m'))), dict(z_m=0.0), ['goniometer'],
    '测角台Z（±0.2mm，工程；Z-up/Z-down）：把样品调到优中心高度（倾转时像不移、标准聚焦即正焦）；10µm/s')
cap('stage.set_tilt', obj(dict(tilt_rad=number('rad', -math.radians(70.0), math.radians(70.0)))), obj(dict(tilt_rad=number('rad'))), dict(tilt_rad=math.radians(10.0)),
    ['goniometer', 'holder_tilt'], '倾转Tilt-X（绕样品杆轴）：快换杆±20°（HR极靴，ETH设施数据；同平台JEM-1400Plus目录HC配置为±25°，见冲突记录）、高倾转杆±70°（厂家）；2°/s（工程）；'
    '须样品杆在镜筒内')
cap('stage.neutral', obj({}), obj(dict(neutral=BOOL)), {}, ['goniometer', 'button_exchange_holder'], '样品台回中性位（X/Y/Z与倾转归零）——插拔样品杆前的必要步骤（设施规程）')
cap('image.acquire', obj(dict(exposure_s=number('s', 0.001, 60.0), binning=integer(1, 4))), OPERATION, dict(exposure_s=1.0, binning=1), ['scmos_camera', 'viewing_screen'],
    '相机采集一帧图像（异步，曝光+0.2s读出）：须束流、荧光屏抬起、成像模式、样品台静止；返回按灰度分割的区域（不给物质名）、计数与信噪比、可见晶格条纹间距、'
    '无定形膜的Thon环频率、32×32缩略图')
cap('image.result', obj(dict(image_id=TEXT)), IMAGE, dict(image_id='img-1'), 'monitor', '读取图像记录（本调用者可见）', 'agent', 'query')
cap('diffraction.acquire', obj(dict(exposure_s=number('s', 0.001, 60.0), binning=integer(1, 4))), OPERATION, dict(exposure_s=0.5, binning=1),
    ['scmos_camera', 'sa_aperture_dial'], '相机采集选区衍射花样（异步）：须衍射模式、荧光屏抬起；返回环（半径与按显示相机长度换算的d值、相对强度、宽度）、单晶斑点、非晶漫散射环与中心束是否饱和；'
    '不给物质名')
cap('diffraction.result', obj(dict(pattern_id=TEXT)), PATTERN, dict(pattern_id='dp-1'), 'monitor', '读取衍射记录（本调用者可见）', 'agent', 'query')
cap('montage.acquire', obj(dict(rows=integer(1, 8), cols=integer(1, 8), overlap=number('1', 0.0, 0.5), exposure_s=number('s', 0.001, 10.0), binning=integer(1, 4))),
    OPERATION, dict(rows=3, cols=3, overlap=0.1, exposure_s=0.5, binning=2), ['scmos_camera', 'goniometer'],
    'Limitless Panorama拼图（异步）：样品台按行列（蛇形）移动逐帧采集，每帧=移动+1s稳定+曝光+读出，结束后回到起点；返回拼图缩略图与区域统计')
cap('montage.result', obj(dict(montage_id=TEXT)), MONTAGE, dict(montage_id='llp-1'), 'monitor', '读取拼图记录（本调用者可见）', 'agent', 'query')
cap('camera.auto_exposure', obj({}), obj(dict(exposure_s=number('s'), mean_counts_per_s=number('1/s'))), {}, ['scmos_camera'],
    '自动曝光建议：按当前视场的平均计数率给出约1万计数所需曝光时间（0.001–60s；不改变状态）', 'agent', 'analysis')
cap('records.list', obj({}), obj(dict(images=array(TEXT), patterns=array(TEXT), montages=array(TEXT))), {}, 'monitor', '列出本调用者可见的记录', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT), dropped=INT)), dict(since_s=0.0), 'monitor', '读取事件与错误日志', 'agent', 'query')
cap('ledger.read', obj({}), LEDGER, {}, 'power_supply_console', '受限：电子电荷账（发射、枪与聚光镜光阑、载网、物镜光阑（含LOW MAG视场限制）、选区光阑、探测器）、'
    '气体物料账、灯丝工作时间与气锁循环次数',
    'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, substances=array(TEXT))), {}, 'monitor', '受限：本实例用到的模板库物质', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), 'power_supply_console', '推进统一宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'stem.acquire': 'STEM扫描像观察装置为选配（同平台需另购），本配置未装。',
    'eds.acquire': 'EDS能谱仪为选配（选配清单参照同平台JEM-1400Plus资料），本配置未装。',
    'tomography.acquire_tilt_series': '三维重构（倾转系列采集与重构软件）为选配（同平台JEM-1400Plus资料），本资产未实现。',
    'mds.set_mode': '最小剂量系统（MDS：Search/Focus/Photo三种照明模式，参照同平台JEM-1400Plus资料与设施规程）在本资产未实现（剂量由照明与曝光直接控制）。',
    'picture_overlay.load': 'Picture Overlay需要外部光学显微镜图像与载网标定，本资产没有光镜图像来源。',
    'holder.double_tilt': '双倾杆EM-31630（Tilt-Y ±9°，同平台JEM-1400Plus资料）为选配，本配置未装。',
    'holder.quartet': '四样品杆EM-01070SQH为选配（厂家与同平台JEM-1400Plus资料），本配置未装。',
    'optics.alignment': '电子枪、聚光镜、电压中心与物镜光阑对中以及消像散在本资产按始终对好处理（旋钮SHIFT/MULTI FUNCTION未接线）。',
    'hardware.connect': '没有真实JEM-1400Flash、TEM Center或相机的通讯接口。',
    'robot.handle_holder': '机器人夹持样品杆插拔的接触/力控没有实现与验证（插拔为过程层技能，由宿主或场景代理）。',
    'accuracy.certification': '没有厂家分辨率、倍率与相机长度的验收或标定证书。',
}
UNSUPPORTED_COMPONENTS = {'stem.acquire': ['scmos_camera'], 'eds.acquire': ['objective_lens'], 'tomography.acquire_tilt_series': ['holder_tilt'],
                          'mds.set_mode': ['left_panel'], 'picture_overlay.load': ['monitor'], 'holder.double_tilt': ['goniometer'], 'holder.quartet': ['goniometer'],
                          'optics.alignment': ['knob_shift_x', 'knob_shift_y', 'knob_multifunction_x', 'knob_multifunction_y'], 'hardware.connect': ['pc_tower'],
                          'robot.handle_holder': ['specimen_holder'], 'accuracy.certification': ['objective_lens']}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, UNSUPPORTED_COMPONENTS[_k], '不支持：' + _v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


STATES = ['off', 'booting', 'ready']
VAC_STATES = ['idle', 'pumping', 'ready']
AIRLOCK_STATES = ['open', 'vented', 'venting', 'pumping', 'evacuated', 'idle', 'column']
HOLDER_POS = ['bench', 'entering', 'airlock', 'turning_in', 'column', 'withdrawing', 'removing']
ABS = '绝对压力'
slot('clock.time_s', number('s', 0.0), 's', 'derived', 'power_supply_console', '宿主统一仿真时钟')
slot('plant.configured', BOOL, '1', 'derived', 'power_supply_console', '宿主是否已声明实验室条件')
slot('power.enabled', BOOL, '1', 'derived', 'switch_main_power', '主电源是否接通')
slot('system.state', enum(STATES), '1', 'derived', 'pc_tower', 'TEM Center状态：off/booting（约90s）/ready')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'monitor', '待确认的错误代码（HT_TRIP、FILAMENT_BURNOUT、MAINS_FAULT、COOLING_WATER_LOW）；无为null')
slot('vacuum.state', enum(VAC_STATES), '1', 'derived', 'turbo_molecular_pump', '镜筒真空：idle（断电，阀门关闭）/pumping/ready（镜筒与电子枪都低于就绪压力）')
slot('vacuum.column_pressure_indicated_pa', nullable(number('Pa', 0.0)), 'Pa', 'sensor', 'specimen_chamber', '镜筒（样品室）真空计示值（10%相对噪声）；断电为null', reference=ABS)
slot('vacuum.gun_pressure_indicated_pa', nullable(number('Pa', 0.0)), 'Pa', 'sensor', 'gun_chamber', '电子枪真空计示值（10%相对噪声）；断电为null', reference=ABS)
slot('airlock.pump', BOOL, '1', 'setpoint', 'airlock_lever', '换样气锁拨杆：true=PUMP，false=AIR')
slot('airlock.state', enum(AIRLOCK_STATES), '1', 'derived', 'airlock_box',
     '气锁状态：open（止点处无样品杆，气锁未形成）/vented/venting/pumping/evacuated（Evac Ready）/idle（断电）/column（样品杆在镜筒内或正在转入转出）')
slot('airlock.pressure_indicated_pa', nullable(number('Pa', 0.0)), 'Pa', 'sensor', 'airlock_box', '气锁压力示值（10%相对噪声）；断电或气锁未形成为null', reference=ABS)
slot('panel.airlock_lamp', BOOL, '1', 'derived', 'airlock_lamp', '气锁拨杆上方的Evac Ready绿灯是否点亮')
slot('panel.power_lamp', BOOL, '1', 'derived', 'power_lamp', '电源柜电源指示灯是否点亮')
slot('panel.beam_lamp', BOOL, '1', 'derived', 'beam_lamp', '束流指示灯（高压开、灯丝开且有发射）是否点亮')
slot('holder.position', enum(HOLDER_POS), '1', 'derived', 'specimen_holder',
     '测角台中的样品杆位置：bench（无杆）/entering/airlock（气锁止点）/turning_in/column/withdrawing/removing')
slot('holder.in_goniometer', nullable(enum(HOLDERS)), '1', 'derived', 'goniometer', '插在测角台中的样品杆；无为null')
slot('holder.grids', obj({h: nullable(TEXT) for h in HOLDERS}), '1', 'derived', 'holder_tip', '各样品杆上装的载网样品ID（null=空）')
slot('specimen.bench_ids', array(TEXT), '1', 'derived', 'desk_top', '实验台上的载网样品ID')
slot('ht.on', BOOL, '1', 'derived', 'gun_chamber', '高压是否开启')
slot('ht.voltage_setpoint_v', number('V', KV_RANGE_V[0], KV_RANGE_V[1]), 'V', 'setpoint', 'gun_chamber', '加速电压设定值')
slot('ht.voltage_indicated_v', nullable(number('V', 0.0)), 'V', 'sensor', 'gun_chamber', '当前加速电压示值（升压中逐渐变化）；高压关为null')
slot('gun.dark_current_indicated_a', nullable(number('A', 0.0)), 'A', 'sensor', 'rear_console', '高压暗电流示值（HT/1.818GΩ，读数波动0.1µA）；高压关为null')
slot('gun.beam_current_indicated_a', nullable(number('A', 0.0)), 'A', 'sensor', 'rear_console', '束流读数=同一周期的暗电流示值+发射电流（设施规程的beam current，另加0.05µA波动）；高压关为null')
slot('filament.on', BOOL, '1', 'derived', 'gun_chamber', '灯丝开关状态（由命令设定；关高压、高压跳闸、冷却水不足、灯丝烧断与断电时联锁关断）')
slot('filament.state', enum(['off', 'heating', 'saturated', 'burned_out']), '1', 'derived', 'gun_chamber', '灯丝状态')
slot('filament.bias', number('1', 0.0, 100.0), '1', 'setpoint', 'gun_chamber', '偏压档（0–100）')
slot('optics.mode', enum(['LOW_MAG', 'MAG', 'DIFF']), '1', 'setpoint', 'intermediate_lens', '当前模式')
slot('optics.magnification', number('1', 10.0, MAG_MAX), '1', 'setpoint', 'projector_lens', '当前成像模式的放大倍数档（衍射模式下为返回MAG时的倍率）')
slot('optics.camera_length_m', number('m', 0.05, 3.5), 'm', 'setpoint', 'projector_lens', '衍射相机长度显示值（与实际值有标定误差）')
slot('optics.magnification_step_fraction', number('1', 0.0, 1.0), '1', 'derived', 'knob_magnification',
     'MAGNIFICATION旋钮位置（0–1，无止点编码器抽象为单圈）：手转时停在手松开的位置并选最近档；其他命令改变档位时旋钮转到该档的位置')
slot('optics.brightness', number('1', 0.0, 1.0), '1', 'setpoint', 'knob_brightness', 'BRIGHTNESS值（0.5为交叉点）')
slot('optics.spot_size', integer(1, 5), '1', 'setpoint', 'upper_column', '束斑号')
slot('optics.defocus_display_m', number('m', DEFOCUS_RANGE_M[0], DEFOCUS_RANGE_M[1]), 'm', 'setpoint', 'knob_focus', '离焦显示值（相对标准聚焦，正值欠焦）')
slot('aperture.condenser_inserted', BOOL, '1', 'setpoint', 'cl_aperture_lever', '聚光镜光阑是否插入')
slot('aperture.objective_position', integer(0, 4), '1', 'setpoint', 'ol_aperture_dial', '物镜光阑档（0=退出）')
slot('aperture.selected_area_position', integer(0, 4), '1', 'setpoint', 'sa_aperture_dial', '选区光阑档（0=退出）')
slot('screen.up', BOOL, '1', 'setpoint', 'viewing_screen', '大荧光屏抬起指令（true=抬起供相机采集）')
slot('screen.moving', BOOL, '1', 'derived', 'viewing_screen', '荧光屏是否仍在翻转（约0.5s）')
slot('stage.x_indicated_m', nullable(number('m')), 'm', 'sensor', 'goniometer', '测角台X读数（0.1µm分辨）；断电为null')
slot('stage.y_indicated_m', nullable(number('m')), 'm', 'sensor', 'goniometer', '测角台Y读数')
slot('stage.z_indicated_m', nullable(number('m')), 'm', 'sensor', 'goniometer', '测角台Z读数')
slot('stage.tilt_indicated_rad', nullable(number('rad')), 'rad', 'sensor', 'holder_tilt', '倾转读数（0.1°分辨）')
slot('stage.moving', BOOL, '1', 'derived', 'goniometer', '测角台是否在运动')
slot('illumination.screen_current_density_a_m2', nullable(number('A/m^2', 0.0)), 'A/m^2', 'sensor', 'viewing_screen',
     '荧光屏电流密度示值（曝光参考）：样品处电流密度×视场中心透过率/M²（3%噪声）；须荧光屏放下、成像模式、有束流，否则为null')
slot('display.mag_or_camera_length_text', nullable(dict(type='string', maxLength=7)), '1', 'derived', 'monitor',
     '屏上倍率栏显示的文字（图像模式为放大倍数的整数，衍射模式为以mm计的相机长度整数；文本，不是数值量）；断电为null')
slot('acquisition.id', nullable(TEXT), '1', 'derived', 'scmos_camera', '正在进行的采集记录ID')
slot('acquisition.kind', nullable(enum(['image', 'diffraction', 'montage'])), '1', 'derived', 'scmos_camera', '正在进行的采集类型')
slot('acquisition.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'scmos_camera', '采集进度（0–1）')
slot('acquisition.started_by_other_agent', BOOL, '1', 'derived', 'scmos_camera', '当前采集是否由另一个代理发起（其控制权受保护）')
slot('result.last_image_id', nullable(TEXT), '1', 'derived', 'monitor', '本调用者最近完成的图像ID')
slot('result.last_pattern_id', nullable(TEXT), '1', 'derived', 'monitor', '本调用者最近完成的衍射ID')
slot('result.last_montage_id', nullable(TEXT), '1', 'derived', 'monitor', '本调用者最近完成的拼图ID')
slot('holder.withdrawal_m', number('m', 0.0, HOLDER_STROKE_M['removed']), 'm', 'process_truth', 'specimen_holder',
     '样品杆相对完全插入位置的拔出量（0=镜筒、0.042m=气锁止点、0.36m=取出；镜像到样品杆插拔关节）', 'evaluator', '沿+X（测角台轴）')
slot('stage.x_m', number('m'), 'm', 'process_truth', 'goniometer', '测角台X真值（视场中心的样品坐标）', 'evaluator', '样品坐标系，X沿样品杆轴')
slot('stage.y_m', number('m'), 'm', 'process_truth', 'goniometer', '测角台Y真值', 'evaluator', '样品坐标系')
slot('stage.z_m', number('m'), 'm', 'process_truth', 'goniometer', '测角台Z真值（正值向上，朝电子枪）', 'evaluator', '光轴方向')
slot('stage.tilt_rad', number('rad'), 'rad', 'process_truth', 'holder_tilt', '倾转真值（镜像到样品杆倾转关节）', 'evaluator', '绕+X（杆轴）')
slot('screen.angle_rad', number('rad', 0.0, 1.0), 'rad', 'process_truth', 'viewing_screen', '荧光屏翻起角（0=放下、40°=抬起；镜像到荧光屏关节）', 'evaluator')
slot('airlock.lever_rad', number('rad', 0.0, 1.0), 'rad', 'process_truth', 'airlock_lever', '气锁拨杆转角（0=AIR、40°=PUMP；镜像到拨杆关节）', 'evaluator')
slot('aperture.cl_lever_rad', number('rad', -2.0, 0.0), 'rad', 'process_truth', 'cl_aperture_lever', '聚光镜光阑拨杆转角（0=退出、−60°=插入）', 'evaluator')
slot('aperture.ol_dial_rad', number('rad', 0.0, 5.0), 'rad', 'process_truth', 'ol_aperture_dial', '物镜光阑拨盘转角（档×60°）', 'evaluator')
slot('aperture.sa_dial_rad', number('rad', 0.0, 5.0), 'rad', 'process_truth', 'sa_aperture_dial', '选区光阑拨盘转角（档×60°）', 'evaluator')
for _k, _u, _c, _m in [('truth.column_pressure_pa', 'Pa', 'specimen_chamber', '镜筒真实压力'), ('truth.gun_pressure_pa', 'Pa', 'gun_chamber', '电子枪室真实压力'),
                       ('truth.airlock_pressure_pa', 'Pa', 'airlock_box', '气锁真实压力（气锁未形成时为室内大气压，样品杆在镜筒内时为镜筒压力）'),
                       ('truth.tmp_speed_fraction', '1', 'turbo_molecular_pump', '涡轮分子泵转速（额定的分数）'),
                       ('truth.filament_on_time_s', 's', 'gun_chamber', '当前灯丝的等效工作时间（按发射加速磨损）'),
                       ('truth.filament_life_s', 's', 'gun_chamber', '当前灯丝的（随机，Weibull）寿命'), ('truth.emission_a', 'A', 'gun_chamber', '真实发射电流'),
                       ('truth.specimen_current_a', 'A', 'objective_lens', '到达样品面的束流'), ('truth.current_density_a_m2', 'A/m^2', 'objective_lens', '样品处电流密度（垂直于束）'),
                       ('truth.illuminated_diameter_m', 'm', 'condenser_lens', '样品处照明区直径'), ('truth.defocus_m', 'm', 'objective_lens', '视场中心的真实离焦（正值欠焦）'),
                       ('truth.specimen_height_m', 'm', 'holder_tip', '视场中心样品点相对优中心面的高度（载网安装偏差+Z，倾转时÷cos）'),
                       ('truth.camera_length_m', 'm', 'projector_lens', '真实相机长度'),
                       ('truth.view_center_dose_c_m2', 'C/m^2', 'holder_tip', '视场中心样品点累积的电子注量（沿束方向穿过该点的电荷/面积，损伤按它计算）')]:
    slot(_k, number(_u), _u, 'process_truth', _c, _m, 'evaluator', ABS if _u == 'Pa' else None)
slot('truth.specimens', {'type': 'object'}, '1', 'process_truth', 'holder_tip', '全部载网样品的真实组成、形态、位置、安装偏差与剂量事件数', 'evaluator')


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


LEDGER_FLOWS = ['beam_charge_c', 'gun_condenser_stopped_charge_c', 'specimen_plane_charge_c', 'grid_stopped_charge_c', 'objective_aperture_stopped_charge_c',
                'selected_area_aperture_stopped_charge_c', 'detector_charge_c', 'gas_admitted_mol', 'gas_pumped_mol', 'gas_released_mol', 'filament_on_time_s', 'airlock_cycles']


def _nearest(steps, v):
    return min(range(len(steps)), key=lambda i: abs(math.log(steps[i]) - math.log(v)))


# ---------------------------------------------------------------- specimen structure: grid, support film, phases, dose and damage
HOLE_D_M = 1.2e-6                  # holey support film: mean hole diameter (engineering)
HOLE_SPREAD = 0.15                 # hole diameters uniform within +-15 %
FILM_PATCH_M = 5e-6                # islands of a partially covering film
SECTION_PATCH_M = 150e-6           # section ribbons
STAIN_TEXTURE_M = 0.4e-6           # stain-density variation inside a stained layer (membranes, granules)
BEND_M = 3e-6                      # length scale of the bending of a single-crystal film (bend contours)
BEND_CELL_M = BEND_M / 4.0
BEND_RAD = math.radians(1.0)
SIZE_SPREAD = 0.10                 # particle diameters: 1 sigma, clipped at 3 sigma
PARTICLE_COVERAGE_MAX = 0.45       # particles on a jittered lattice never overlap below this projected coverage
HOLE_FRACTION_MAX = 0.6
POLY_GRAINS = 4                    # a polycrystalline particle consists of 4 x 4 crystallites
N_ORIENT_AVG = 48                  # orientations of the sub-resolution averages
IMAGING_D_MIN_M = 0.1e-9           # reflections that enter the image contrast (diffraction patterns use the whole library list)
LOW_MAG_ILLUM_FACTOR = 20.0        # LOW MAG: the condenser mini-lens is off and the illumination is about 20 x wider
DOSE_REF_V = 100000.0              # damage doses are stored as 100 kV-equivalent (radiolysis cross-section ~ 1/beta^2)
MAX_DOSE_EVENTS = 20000
DAMAGE_Q = 1000                    # damage multipliers in steps of 0.001 (images, diffraction)
VIEW_DAMAGE_Q = 50                 # the charge-ledger / screen-reading fractions follow the damage in steps of 0.02
DEPOSIT_SUBSTEPS = 10              # the beam path of a cycle is re-sampled at 10 fixed times plus its exact corners
STAIN_ID = 'uranyl_acetate_stain'


def _mix64(x):
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def _u01(*keys):
    """Deterministic uniform numbers in [0, 1) for integer keys (scalars or arrays; splitmix64 chain)."""
    arrs = [np.atleast_1d(np.asarray(k)).astype(np.int64) for k in keys]
    shape = np.broadcast_shapes(*[a.shape for a in arrs])
    with np.errstate(over='ignore'):
        h = np.full(shape, 0x9E3779B97F4A7C15, dtype=np.uint64)
        for a in arrs:
            h = _mix64(h ^ (np.broadcast_to(a, shape).astype(np.uint64) + np.uint64(0x632BE59BD9B4E019)))
    return (h >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def _normal(*keys):
    """Deterministic standard normal numbers (Box-Muller on two hashed uniforms)."""
    u1 = np.maximum(_u01(*keys, 7001), 1e-300)
    u2 = _u01(*keys, 7002)
    return np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * math.pi * u2)


def _value_noise(U, V, scale, seed):
    """Smooth value noise in [0, 1) with feature size `scale` (m)."""
    x, y = np.asarray(U, float) / scale, np.asarray(V, float) / scale
    i, j = np.floor(x), np.floor(y)
    fx, fy = x - i, y - j
    sx, sy = fx * fx * (3.0 - 2.0 * fx), fy * fy * (3.0 - 2.0 * fy)
    i, j = i.astype(np.int64), j.astype(np.int64)
    n00, n10 = _u01(i, j, seed), _u01(i + 1, j, seed)
    n01, n11 = _u01(i, j + 1, seed), _u01(i + 1, j + 1, seed)
    return (n00 * (1 - sx) + n10 * sx) * (1 - sy) + (n01 * (1 - sx) + n11 * sx) * sy


def _noise_quantiles():
    rng = np.random.default_rng(20260926)
    return np.sort(_value_noise(rng.uniform(0, 400, 200000), rng.uniform(0, 400, 200000), 1.0, 99991))


_NOISE_Q = _noise_quantiles()


def _noise_threshold(fraction):
    """Noise level below which the given area fraction lies (the value-noise distribution is not uniform)."""
    if fraction <= 0.0:
        return -1.0
    if fraction >= 1.0:
        return 2.0
    return float(_NOISE_Q[min(len(_NOISE_Q) - 1, int(fraction * len(_NOISE_Q)))])


def _bar_cover(a, w, p, delta):
    """Fraction of the interval [a - delta/2, a + delta/2] covered by bars [n p, n p + w) (box-filtered grid bars)."""
    def F(x):
        n = np.floor(x / p)
        return n * w + np.minimum(x - n * p, w)
    return np.clip((F(a + delta / 2) - F(a - delta / 2)) / delta, 0.0, 1.0)


def _rotations(u1, u2, u3):
    """Uniform random rotations (Shoemake) for arrays of uniforms: (n, 3, 3)."""
    a, b = np.sqrt(1 - u1), np.sqrt(u1)
    x, y = a * np.sin(2 * np.pi * u2), a * np.cos(2 * np.pi * u2)
    z, w = b * np.sin(2 * np.pi * u3), b * np.cos(2 * np.pi * u3)
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                     np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                     np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], -2)


def _rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def _rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _zone_rotation(cr, uvw, psi):
    """Rotation (crystal Cartesian -> specimen) that puts the direct-lattice direction [uvw] along the specimen normal, turned by psi."""
    d = np.asarray(uvw, float) @ cr['_A']
    d = d / np.linalg.norm(d)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(d, z)
    s, c = float(np.linalg.norm(v)), float(d @ z)
    if s < 1e-12:
        R = np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    else:
        K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]) / s
        R = np.eye(3) + s * K + (1 - c) * (K @ K)
    cz, sz = math.cos(psi), math.sin(psi)
    return np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]]) @ R


_ORIENTS = _rotations(*[((np.arange(N_ORIENT_AVG) + 0.5) * g) % 1.0 for g in (0.6180339887498949, 0.7548776662466927, 0.5698402909980532)])


@functools.lru_cache(maxsize=256)
def _reflections(sid, d_min):
    """All reflections of a crystalline substance with d >= d_min: g vectors (crystal Cartesian, 1/m, rows), |F| (m), d (m)."""
    cr = substance(sid)['crystal']
    G, F, D = [], [], []
    for r in cr['_refl']:
        if r['d'] < d_min:
            continue
        for hkl in r['eq']:
            G.append(np.array(hkl, float) @ cr['_B'])
            F.append(r['F'])
            D.append(r['d'])
    G, F, D = np.array(G, float).reshape(-1, 3), np.array(F, float), np.array(D, float)
    for a in (G, F, D):
        a.setflags(write=False)
    return G, F, D


@functools.lru_cache(maxsize=512)
def _xi(sid, d_min, v):
    """Two-beam extinction distances (m) of the reflections at voltage v."""
    cr = substance(sid)['crystal']
    _, F, _ = _reflections(sid, d_min)
    out = math.pi * cr['_vol'] / (wavelength_m(v) * gamma_of(v) * np.maximum(F, 1e-30))
    out.setflags(write=False)
    return out


@functools.lru_cache(maxsize=512)
def _mean_z_b(sid):
    """Number-weighted mean of Z^(1/3) and of the Debye-Waller B (A^2) of a substance."""
    n = dict(atom_densities(sid))
    tot = sum(n.values())
    z3 = sum(c * element(e)['Z'] ** (1.0 / 3.0) for e, c in n.items()) / tot
    cr = substance(sid).get('crystal') or {}
    b = sum(c * (float(np.mean(cr.get('debye_waller_A2', {}).get(e) or [B_DEFAULT_A2]))) for e, c in n.items()) / tot
    return z3, b


@functools.lru_cache(maxsize=2048)
def diffuse_fraction(sid, v, alpha):
    """Fraction of the atomic (Lenz-Wentzel) elastic scattering outside alpha that a crystal scatters diffusely (thermal diffuse
    scattering): 1 - exp(-2 B s^2) at s = max(alpha, theta0) / (2 lambda); the rest is concentrated in the Bragg reflections."""
    z3, b = _mean_z_b(sid)
    lam = wavelength_m(v)
    th0 = lam * z3 / (2.0 * math.pi * A0)
    s_a = max(alpha, th0) / (2.0 * lam) * 1e-10            # 1/A
    return 1.0 - math.exp(-2.0 * b * s_a * s_a)


def bragg(G, xi, R, t, beam, v, alpha):
    """Two-beam intensities of all reflections for crystals with rotations R (n, 3, 3) and beam path thicknesses t (n,) at the beam direction
    `beam` (unit vector in the specimen frame).  Returns (D_all, D_in, I, g): the diffracted fraction and the part inside the objective
    aperture per crystal after the many-beam depletion D -> 1 - exp(-D), the reflections' intensities (n, m) and g in the specimen frame."""
    lam = wavelength_m(v)
    g = np.einsum('nij,mj->nmi', R, G)
    s = -np.einsum('nmi,i->nm', g, np.asarray(beam, float)) - 0.5 * lam * np.einsum('mi,mi->m', G, G)[None, :]
    se = np.sqrt(s * s + 1.0 / (xi * xi)[None, :])
    I = (math.pi / xi[None, :]) ** 2 * np.sin(math.pi * np.asarray(t, float)[:, None] * se) ** 2 / (math.pi * se) ** 2
    inside = (lam * np.sqrt(np.einsum('mi,mi->m', G, G)) < alpha)[None, :]
    D = I.sum(axis=1)
    Din = (I * inside).sum(axis=1)
    scale = np.where(D > 1e-12, -np.expm1(-D) / np.maximum(D, 1e-12), 1.0)
    return D * scale, Din * scale, I, g


def _crystal_factors(sid, R, t, fc, beam, v, alpha, d_min=IMAGING_D_MIN_M):
    """Bright-field factor and unscattered fraction of crystals (rotations R, path thicknesses t, remaining crystallinity fc)."""
    G, _, _ = _reflections(sid, d_min)
    xi = _xi(sid, d_min, v)
    f0 = diffuse_fraction(sid, v, alpha)
    mu_o, mu_t = mu_el_outside(sid, v, alpha), mu_el_outside(sid, v, 0.0)
    bf = np.empty(len(t))
    di = np.empty(len(t))
    for c in range(0, len(t), 256):
        sl = slice(c, c + 256)
        dall, din, _, _ = bragg(G, xi, R[sl], t[sl], beam, v, alpha)
        dall, din = dall * fc[sl], din * fc[sl]
        fd = f0 + (1.0 - f0) * (1.0 - fc[sl])
        bf[sl] = np.exp(-fd * mu_o * t[sl]) * (1.0 - dall + din)
        di[sl] = np.exp(-fd * mu_t * t[sl]) * (1.0 - dall)
    return bf, di


@functools.lru_cache(maxsize=8192)
def _crystal_average(sid, t_q, v_q, alpha_q, fc_q, profile):
    """Orientation-averaged bright-field factor and unscattered fraction of one crystal below the sampling resolution: 48 fixed orientations
    x (particles) a spherical thickness profile.  t_q: path thickness in 0.1 nm, v_q: voltage in 10 V, alpha_q: semi-angle in 10 urad,
    fc_q: remaining crystallinity in 1/50."""
    t, v, alpha, fc = t_q * 1e-10, v_q * 10.0, alpha_q * 1e-5, fc_q / 50.0
    ts = t * np.sqrt(1.0 - (np.arange(6) + 0.5) / 6.0) if profile == 'sphere' else np.array([t])
    R = np.concatenate([_ORIENTS] * len(ts))
    tt = np.repeat(ts, N_ORIENT_AVG)
    bf, di = _crystal_factors(sid, R, tt, np.full(len(tt), fc), np.array([0.0, 0.0, -1.0]), v, alpha)
    return float(np.mean(bf)), float(np.mean(di))


def damage_multipliers(sid, fluence100, q=None):
    """(mass multiplier, remaining crystallinity) of a substance after the 100 kV-equivalent electron fluence (C/m^2 through the material along the
    beam, array), quantised to 1/DAMAGE_Q so that the specimen response is a function of a discrete damage state (exact caching)."""
    q = DAMAGE_Q if q is None else q
    dmg = substance(sid)['damage']
    one = np.ones_like(np.asarray(fluence100, float))
    if dmg['mode'] == 'mass_loss':
        de = dmg['de_c_cm2'] * 1e4 * beta2_of(DOSE_REF_V) / beta2_of(dmg['e_ref_kev'] * 1e3)
        r = dmg['residual_fraction']
        return np.maximum(r, np.round((r + (1.0 - r) * np.exp(-np.asarray(fluence100, float) / de)) * q) / q), one
    if dmg['mode'] == 'crystallinity':
        dc = dmg['dc_c_cm2'] * 1e4 * beta2_of(DOSE_REF_V) / beta2_of(dmg['e_ref_kev'] * 1e3)
        return one, np.round(np.exp(-np.asarray(fluence100, float) / dc) * q) / q
    return one, one


def dose_at(spec, U, V):
    """Accumulated electron fluence at specimen points (C/m^2 through each point along the beam; and its 100 kV equivalent).

    Dose events are beam footprints swept at constant speed along straight stage paths, rows [x0, y0, x1, y1, r, cos t, F, F100] in specimen
    coordinates (a stationary exposure has x1 = x0, y1 = y0); in the beam-normal frame (v scaled by cos t) the footprint is a disc of radius r and
    a point receives F x (time inside the moving disc) / (event duration)."""
    U, V = np.asarray(U, float), np.asarray(V, float)
    D = np.zeros(U.shape)
    D100 = np.zeros(U.shape)
    if not spec['_dose'] or U.size == 0:
        return D, D100
    E = np.asarray(spec['_dose'], float)
    ry = E[:, 4] / E[:, 5]
    keep = ((np.maximum(E[:, 0], E[:, 2]) + E[:, 4] >= U.min()) & (np.minimum(E[:, 0], E[:, 2]) - E[:, 4] <= U.max())
            & (np.maximum(E[:, 1], E[:, 3]) + ry >= V.min()) & (np.minimum(E[:, 1], E[:, 3]) - ry <= V.max()))
    E = E[keep]
    for c in range(0, len(E), 256):
        e = E[c:c + 256]
        ct, r = e[:, 5:6], e[:, 4:5]
        px, py = U[None, :] - e[:, 0:1], (V[None, :] - e[:, 1:2]) * ct
        dx, dy = e[:, 2:3] - e[:, 0:1], (e[:, 3:4] - e[:, 1:2]) * ct
        L = np.sqrt(dx * dx + dy * dy)
        moving = L > 1e-9 * r
        Ls = np.where(moving, L, 1.0)
        sa = (px * dx + py * dy) / Ls
        w = np.sqrt(np.maximum(r * r - (px * px + py * py - sa * sa), 0.0))
        frac = np.where(moving, np.clip(np.minimum(sa + w, L) - np.maximum(sa - w, 0.0), 0.0, None) / Ls, (px * px + py * py < r * r).astype(float))
        D += e[:, 6] @ frac
        D100 += e[:, 7] @ frac
    return D, D100


def damage_key(spec, fluence100, q=None):
    """Discrete damage state of the specimen at sample points (quantised multipliers of every damageable substance present)."""
    if not spec['_damageable']:
        return None
    sids = ({spec['support_film'], STAIN_ID} | {p['substance_id'] for p in spec['phases']}) - {'none'}
    parts = []
    for sid in sorted(sids):
        if substance(sid)['damage']['mode'] == 'none':
            continue
        m, fc = damage_multipliers(sid, fluence100, q)
        parts.append(np.round(m * 1e6).astype(np.int64).tobytes() + np.round(fc * 1e6).astype(np.int64).tobytes())
    return hash(tuple(parts))


def _phase_seed(spec, k):
    return int(spec['_seed']) * 131 + 1009 * (k + 1)


def particle_pitch(p):
    """Lattice pitch (m) of the particle cells that gives the requested projected coverage."""
    return 0.5 * p['size_m'] * math.sqrt(math.pi * (1.0 + SIZE_SPREAD ** 2) / max(p['coverage'], 1e-9))


def _stained_mu(sid, w, v, alpha, m_sub, m_stain):
    """Attenuation (1/m) of a substance holding the stain mass fraction w (volume-additive densities), after mass loss of both parts."""
    rs, ru = substance(sid)['density_g_cm3'], substance(STAIN_ID)['density_g_cm3']
    rho = 1.0 / ((1.0 - w) / rs + w / ru)
    return rho * ((1.0 - w) * mu_damaged(sid, v, alpha, m_sub) / rs + w * mu_damaged(STAIN_ID, v, alpha, m_stain) / ru)


def _cell_particle(i, j, p, sk):
    """Particle of lattice cell (i, j) (jittered lattice, one particle per cell, footprints never overlapping): centre and radius."""
    pitch = particle_pitch(p)
    rmax = 0.5 * p['size_m'] * (1.0 + 3.0 * SIZE_SPREAD)
    room = max(pitch - 2.0 * rmax, 0.0)
    cu = i * pitch + rmax + _u01(i, j, sk, 1) * room
    cv = j * pitch + rmax + _u01(i, j, sk, 2) * room
    rp = 0.5 * p['size_m'] * (1.0 + SIZE_SPREAD * np.clip(_normal(i, j, sk, 3), -3.0, 3.0))
    return cu, cv, rp


def _particle_cells(U, V, p, sk):
    """Particle of the cell of each point: cell i, j, centre, radius."""
    pitch = particle_pitch(p)
    i, j = np.floor(U / pitch).astype(np.int64), np.floor(V / pitch).astype(np.int64)
    cu, cv, rp = _cell_particle(i, j, p, sk)
    return i, j, cu, cv, rp


def spheroid_projection(a, q, tilt):
    """A particle lying on the film is a spheroid of radius a (in the film) and half-thickness q a (normal to the film).  Seen along the beam at
    holder tilt t its outline is an ellipse with semi-axes a and b' = a sqrt(cos^2 t + q^2 sin^2 t) (the latter along the tilt direction in the
    beam-normal plane) and its path thickness through the centre is 2 L0, L0 = q a / sqrt(q^2 sin^2 t + cos^2 t).  A sphere (q = 1) looks the same at
    any tilt; a platelet (q << 1) thins and foreshortens like a film."""
    st, ct = math.sin(tilt), math.cos(tilt)
    return a * math.sqrt(ct * ct + q * q * st * st), q * a / math.sqrt(q * q * st * st + ct * ct)


def _particle_at(U, V, p, sk, tilt):
    """Particle whose tilted projection covers each point (the thickest of the particles of the point's cell and its neighbours along the tilt
    direction): cell i, j, centre u, v, local path thickness (m)."""
    pitch = particle_pitch(p)
    q = p['thickness_m'] / p['size_m']
    ct = math.cos(tilt)
    i, j0 = np.floor(U / pitch).astype(np.int64), np.floor(V / pitch).astype(np.int64)
    best_t, best_j = np.zeros(U.shape), j0.copy()
    best_u, best_v = np.zeros(U.shape), np.zeros(U.shape)
    for dj in (0, -1, 1):
        j = j0 + dj
        cu, cv, rp = _cell_particle(i, j, p, sk)
        bp, l0 = spheroid_projection(1.0, q, tilt)
        rho2 = ((U - cu) / rp) ** 2 + ((V - cv) * ct / (rp * bp)) ** 2
        tl = np.where(rho2 < 1.0, 2.0 * l0 * rp * np.sqrt(np.clip(1.0 - rho2, 0.0, 1.0)), 0.0)
        better = tl > best_t
        best_t = np.where(better, tl, best_t)
        best_j = np.where(better, j, best_j)
        best_u, best_v = np.where(better, cu, best_u), np.where(better, cv, best_v)
    return i, best_j, best_u, best_v, best_t


def sample_specimen(spec, U, V, delta, v, alpha, tilt, field_stop=None, want_units=False, fluence=None, damage_q=None):
    """Electron transmission of a mounted grid at specimen-frame points U, V (m) sampled with spacing delta (m).

    v: accelerating voltage (V); alpha: collection semi-angle (objective aperture or lens bore, rad); tilt: holder tilt (rad);
    field_stop: None, one or a list of (centre u, centre v, radius in the beam-normal plane) discs limiting the field (LOW MAG objective aperture,
    selected-area aperture); fluence: precomputed dose_at(spec, U, V).
    Returns dict(T: bright-field transmission into alpha, direct: unscattered fraction, open: grid open fraction, label: 0 opaque / 1 vacuum /
    1 + composition code (bit k: phase k, bit 4: support film), amorph_t: amorphous path thickness (m), fluence / fluence100: accumulated
    electron fluence (C/m^2) and, with want_units, the resolved crystal units)."""
    U, V = np.asarray(U, float), np.asarray(V, float)
    n = U.shape[0]
    path = 1.0 / max(math.cos(tilt), 1e-6)
    beam = np.array([0.0, -math.sin(tilt), -math.cos(tilt)])           # beam direction in the specimen frame
    dose, dose100 = dose_at(spec, U, V) if fluence is None else fluence
    seed = int(spec['_seed'])
    # grid bars and rim (box-filtered), LOW MAG field stop
    mesh = GRID_MESH[int(spec['grid_mesh'])]
    phi, oa, ob = spec['_mount']['phi'], spec['_mount']['off_a'], spec['_mount']['off_b']
    a = math.cos(phi) * U + math.sin(phi) * V + oa
    b = -math.sin(phi) * U + math.cos(phi) * V + ob
    wall = GRID_THICKNESS_M * abs(math.tan(tilt))                      # the bar walls widen the projected bars along the tilt direction (V)
    opn = (1.0 - _bar_cover(a, mesh['bar'] + wall * abs(math.sin(phi)), mesh['pitch'], delta)) \
        * (1.0 - _bar_cover(b, mesh['bar'] + wall * abs(math.cos(phi)), mesh['pitch'], delta))
    opn = opn * np.clip((GRID_RADIUS_M - np.hypot(U, V)) / delta + 0.5, 0.0, 1.0)
    stops = [] if field_stop is None else [field_stop] if isinstance(field_stop, tuple) else list(field_stop)
    for cu, cv, rad in stops:
        opn = opn * np.clip((rad - np.hypot(U - cu, (V - cv) / path)) / delta + 0.5, 0.0, 1.0)
    T = opn.copy()
    direct = opn.copy()
    amorph = np.zeros(n)
    label = np.where(opn < 0.5, 0, 1)
    # support film: holes resolved (point membership) or averaged with the fluctuation of the hole count in the sample area
    sup = spec['support_film']
    cs = np.zeros(n)
    if sup != 'none' and spec['support_thickness_m'] > 0.0:
        hf = spec['hole_fraction']
        if hf <= 0.0:
            cs[:] = 1.0
        else:
            ah = 0.5 * HOLE_D_M * math.sqrt(math.pi * (1.0 + HOLE_SPREAD ** 2 / 3.0) / hf)
            rmax = 0.5 * HOLE_D_M * (1.0 + HOLE_SPREAD)
            if HOLE_D_M >= 2.0 * delta:
                i, j = np.floor(U / ah).astype(np.int64), np.floor(V / ah).astype(np.int64)
                room = max(ah - 2.0 * rmax, 0.0)
                cu = i * ah + rmax + _u01(i, j, seed, 11) * room
                cv = j * ah + rmax + _u01(i, j, seed, 12) * room
                rh = 0.5 * HOLE_D_M * (1.0 + HOLE_SPREAD * (2.0 * _u01(i, j, seed, 13) - 1.0))
                cs = (np.hypot(U - cu, V - cv) >= rh).astype(float)
            else:
                qi, qj = np.floor(U / delta).astype(np.int64), np.floor(V / delta).astype(np.int64)
                lam_h = (delta / ah) ** 2
                nh = np.maximum(0.0, lam_h + math.sqrt(lam_h) * _normal(qi, qj, seed, 14))
                cs = np.clip(1.0 - nh * math.pi * (0.5 * HOLE_D_M) ** 2 * (1.0 + HOLE_SPREAD ** 2 / 3.0) / delta ** 2, 0.0, 1.0)
        m_s, _ = damage_multipliers(sup, dose100, damage_q)
        tg = spec['support_thickness_m'] * path
        T *= (1.0 - cs) + cs * np.exp(-mu_damaged(sup, v, alpha, m_s) * tg)
        direct *= (1.0 - cs) + cs * np.exp(-mu_damaged(sup, v, 0.0, m_s) * tg)
        amorph += cs * tg * m_s
    carrier = cs if sup != 'none' else np.ones(n)            # particles rest on the support film (none over holes)
    m_u, _ = damage_multipliers(STAIN_ID, dose100, damage_q)
    crys = []                                                  # resolved crystals: (point indices, phase, unit keys, path thickness, crystallinity)
    comp = np.zeros(n, dtype=np.int64)                         # bit k: phase k present; bit 4: support film (observable composition)
    covs = []
    for k, p in enumerate(spec['phases']):
        sid = p['substance_id']
        sub = substance(sid)
        sk = _phase_seed(spec, k)
        m_k, fc_k = damage_multipliers(sid, dose100, damage_q)
        crystalline = sub.get('crystal') is not None and p['crystallinity'] != 'amorphous'
        w = p['stain_mass_fraction']
        if p['form'] == 'particles':
            if p['size_m'] >= 2.0 * delta:
                i, j, cu, cv, tl0 = _particle_at(U, V, p, sk, tilt)
                inside = (tl0 > 0.0) & (carrier >= 0.5)
                cov = inside.astype(float)
                tloc = np.where(inside, tl0, 0.0) * m_k
                if crystalline:
                    idx = np.nonzero(inside)[0]
                    if len(idx):
                        if p['crystallinity'] == 'polycrystalline':
                            gs = p['size_m'] / POLY_GRAINS
                            gi = np.floor((U[idx] - cu[idx]) / gs).astype(np.int64)
                            gj = np.floor((V[idx] - cv[idx]) / gs).astype(np.int64)
                        else:
                            gi = gj = np.zeros(len(idx), dtype=np.int64)
                        crys.append((idx, k, np.stack([i[idx], j[idx], gi, gj], 1), tloc[idx], fc_k[idx]))
                    T_k = D_k = np.ones(n)
                else:
                    mu_o = (_stained_mu(sid, w, v, alpha, m_k, m_u) if w > 0 else mu_damaged(sid, v, alpha, m_k)) / m_k
                    mu_0 = (_stained_mu(sid, w, v, 0.0, m_k, m_u) if w > 0 else mu_damaged(sid, v, 0.0, m_k)) / m_k
                    T_k, D_k = np.exp(-mu_o * tloc), np.exp(-mu_0 * tloc)
                    amorph += tloc
            else:
                qi, qj = np.floor(U / delta).astype(np.int64), np.floor(V / delta).astype(np.int64)
                area = math.pi * (0.5 * p['size_m']) ** 2 * (1.0 + SIZE_SPREAD ** 2)            # footprint on the film
                bp, l0 = spheroid_projection(0.5 * p['size_m'], p['thickness_m'] / p['size_m'], tilt)
                lam_p = p['coverage'] * delta * delta / (area * math.cos(tilt))                # the film area seen in a view cell grows as 1 / cos t
                npart = np.maximum(0.0, lam_p + math.sqrt(lam_p) * _normal(qi, qj, sk, 4))
                cov = np.clip(npart * area * bp / (0.5 * p['size_m']) / delta ** 2, 0.0, 1.0) * carrier
                tq = 2.0 * l0
                if crystalline:
                    bf, dd = np.empty(n), np.empty(n)
                    keys = np.stack([np.round(m_k * tq / 1e-10), np.round(fc_k * 50)], 1).astype(np.int64)
                    for key in np.unique(keys, axis=0):
                        sel = np.all(keys == key, axis=1)
                        bf[sel], dd[sel] = _crystal_average(sid, int(key[0]), int(round(v / 10.0)), int(round(alpha * 1e5)), int(key[1]), 'sphere')
                else:
                    tm = (2.0 / 3.0) * tq * m_k
                    mu_o = (_stained_mu(sid, w, v, alpha, m_k, m_u) if w > 0 else mu_damaged(sid, v, alpha, m_k)) / m_k
                    mu_0 = (_stained_mu(sid, w, v, 0.0, m_k, m_u) if w > 0 else mu_damaged(sid, v, 0.0, m_k)) / m_k
                    bf, dd = np.exp(-mu_o * tm), np.exp(-mu_0 * tm)
                    amorph += cov * tm
                T_k, D_k = 1.0 - cov * (1.0 - bf), 1.0 - cov * (1.0 - dd)
        else:
            patch = FILM_PATCH_M if p['form'] == 'film' else SECTION_PATCH_M
            if p['coverage'] >= 1.0:
                cov = np.ones(n)
            elif patch >= 2.0 * delta:
                cov = (_value_noise(U, V, patch, sk + 5) < _noise_threshold(p['coverage'])).astype(float)
            else:
                cov = np.full(n, p['coverage'])
            tl = p['thickness_m'] * m_k * path
            if crystalline:
                grain = p['size_m']
                if p['crystallinity'] == 'single_crystal' or grain >= 2.0 * delta:
                    idx = np.nonzero(cov >= 0.5)[0]
                    if len(idx):
                        key = np.zeros((len(idx), 4), dtype=np.int64)
                        if p['crystallinity'] == 'single_crystal':
                            key[:, 2] = np.floor(U[idx] / BEND_CELL_M).astype(np.int64)      # the bending varies smoothly: 0.75 um cells
                            key[:, 3] = np.floor(V[idx] / BEND_CELL_M).astype(np.int64)
                        else:
                            gi0, gj0 = np.floor(U[idx] / grain).astype(np.int64), np.floor(V[idx] / grain).astype(np.int64)
                            best = np.full(len(idx), np.inf)
                            for di in (-1, 0, 1):
                                for dj in (-1, 0, 1):
                                    ci, cj = gi0 + di, gj0 + dj
                                    dist = np.hypot(U[idx] - (ci + _u01(ci, cj, sk, 7)) * grain, V[idx] - (cj + _u01(ci, cj, sk, 8)) * grain)
                                    better = dist < best
                                    best = np.where(better, dist, best)
                                    key[better, 0], key[better, 1] = ci[better], cj[better]
                        crys.append((idx, k, key, np.full(len(idx), 1.0) * tl[idx], fc_k[idx]))
                    T_k = D_k = np.ones(n)
                else:
                    bf, dd = np.empty(n), np.empty(n)
                    keys = np.stack([np.round(tl / 1e-10), np.round(fc_k * 50)], 1).astype(np.int64)
                    for key in np.unique(keys, axis=0):
                        sel = np.all(keys == key, axis=1)
                        bf[sel], dd[sel] = _crystal_average(sid, int(key[0]), int(round(v / 10.0)), int(round(alpha * 1e5)), int(key[1]), 'slab')
                    T_k, D_k = 1.0 - cov * (1.0 - bf), 1.0 - cov * (1.0 - dd)
            else:
                if w > 0:
                    wl = np.clip(w * (0.3 + 1.4 * _value_noise(U, V, STAIN_TEXTURE_M, sk + 6)), 0.0, 0.95)
                    t0 = cov * p['thickness_m'] * path
                    T_k = np.exp(-_stained_mu(sid, wl, v, alpha, m_k, m_u) * t0)
                    D_k = np.exp(-_stained_mu(sid, wl, v, 0.0, m_k, m_u) * t0)
                else:
                    t0 = cov * p['thickness_m'] * path
                    T_k = np.exp(-mu_damaged(sid, v, alpha, m_k) * t0)
                    D_k = np.exp(-mu_damaged(sid, v, 0.0, m_k) * t0)
                amorph += cov * tl
        T = T * T_k
        direct = direct * D_k
        covs.append(cov * opn)
        comp |= ((cov >= 0.5) & (opn >= 0.5)).astype(np.int64) << k
    # resolved crystals: dynamical two-beam scattering with the orientation of each particle, crystallite, grain or bent-film cell
    unit_idx = np.full(n, -1, dtype=np.int64)
    units = []
    for idx, k, key, tpath, fc in crys:
        p = spec['phases'][k]
        sid = p['substance_id']
        ukeys, inv = np.unique(key, axis=0, return_inverse=True)
        inv = np.asarray(inv).reshape(-1)
        R = _unit_rotations(spec, k, p, substance(sid)['crystal'], ukeys, _phase_seed(spec, k))
        uq, back = np.unique(np.stack([inv.astype(float), tpath, fc], 1), axis=0, return_inverse=True)
        back = np.asarray(back).reshape(-1)
        bf_u, di_u = _crystal_factors(sid, R[uq[:, 0].astype(np.int64)], uq[:, 1], uq[:, 2], beam, v, alpha)
        bf, di = bf_u[back], di_u[back]
        T[idx] *= bf
        direct[idx] *= di
        if want_units:
            base = len(units)
            for u_i, uk in enumerate(ukeys):
                m = inv == u_i
                units.append(dict(phase=k, key=tuple(int(x) for x in uk), R=R[u_i], points=idx[m], t=float(np.mean(tpath[m])),
                                  fc=float(np.mean(fc[m]))))
            unit_idx[idx] = base + inv
    comp |= ((cs >= 0.5) & (opn >= 0.5)).astype(np.int64) << 4 if sup != 'none' else 0
    label = np.where(opn < 0.5, 0, 1 + comp)                   # 0 opaque, 1 vacuum, 1 + composition code otherwise (overlaps are their own class)
    out = dict(T=np.clip(T, 0.0, 1.0), direct=np.clip(direct, 0.0, 1.0), open=opn, label=label, amorph_t=amorph, fluence=dose, fluence100=dose100,
               support_cov=cs * opn, phase_cov=covs)
    if want_units:
        out.update(unit_idx=unit_idx, units=units)
    return out


def _unit_rotations(spec, k, p, cr, ukeys, sk):
    """Crystal -> specimen rotations of the crystal units (particles / crystallites / grains / bent-film cells)."""
    m = len(ukeys)
    if p['crystallinity'] == 'single_crystal' and p['form'] != 'particles':
        uvw = p['zone_axis']
        R0 = _rotations(_u01(sk, 21), _u01(sk, 22), _u01(sk, 23))[0] if uvw is None else _zone_rotation(cr, uvw, 2.0 * math.pi * float(_u01(sk, 24)[0]))
        cu = (ukeys[:, 2] + 0.5) * BEND_CELL_M
        cv = (ukeys[:, 3] + 0.5) * BEND_CELL_M
        bx = BEND_RAD * (2.0 * _value_noise(cu, cv, BEND_M, sk + 31) - 1.0)
        by = BEND_RAD * (2.0 * _value_noise(cu, cv, BEND_M, sk + 32) - 1.0)
        return np.stack([_rot_x(ax) @ _rot_y(ay) @ R0 for ax, ay in zip(bx, by)]) if m else np.zeros((0, 3, 3))
    if p['zone_axis'] is not None and p['crystallinity'] == 'single_crystal':
        psi = 2.0 * math.pi * _u01(ukeys[:, 0], ukeys[:, 1], sk, 25)
        return np.stack([_zone_rotation(cr, p['zone_axis'], float(a)) for a in psi]) if m else np.zeros((0, 3, 3))
    keys = [ukeys[:, 0], ukeys[:, 1], ukeys[:, 2], ukeys[:, 3], sk]
    return _rotations(_u01(*keys, 26), _u01(*keys, 27), _u01(*keys, 28))


# ---------------------------------------------------------------- runtime
SCREEN_UP_ANGLE_RAD = math.radians(40.0)          # geometry: the large screen lifted by 40 deg (interface definition)
SCREEN_TIME_S = 0.5
AIRLOCK_LEVER_PUMP_RAD = math.radians(40.0)
CL_LEVER_IN_RAD = math.radians(-60.0)
DIAL_STEP_RAD = math.radians(60.0)
V_GUN_M3 = 0.002                                  # gun chamber pumped through the column (differential aperture)
C_GUN_M3_S = 1.0e-4
Q_GUN_PA_M3_S = 2.0e-9                            # gun outgassing (x3 with the hot filament)
GUN_HOT_FACTOR = 3.0
GUN_READY_PA = 1.5e-4
Q_HOLDER_PA_M3_S = 2.0e-6                         # outgassing of a freshly inserted holder and grid, decaying with 60 s
HOLDER_OUTGAS_TAU_S = 60.0
TMP_SPINDOWN_S = 120.0
FIL_WEAR_REF_A = 6.0e-6                           # filament wear rate (emission / 6 uA)^2 relative to the nominal life
CONVERGENCE_CROSSOVER_RAD = 2.0e-3                # illumination convergence semi-angle at the crossover (spread beam: x d0/d, >= 10 urad)
CONVERGENCE_MIN_RAD = 1.0e-5
CAMERA_PSF_PX = 1.5
N_RING = 400                                      # more crystals than this in the diffracting area give continuous rings
STAGE_NEUTRAL_TOL_M = 0.5e-6                      # facility procedures: coordinates close to 0.0 (1.0 already not acceptable)
STAGE_NEUTRAL_TOL_RAD = math.radians(0.5)
THON_MIN_AMORPH_M = 3e-9                          # Thon rings need an amorphous layer of at least 3 nm and a dose of at least 1 e/A^2 in the frame
THON_MIN_DOSE_E_M2 = 1.0e20
FRINGE_MIN_I = 0.05
THON_MIN_ENVELOPE = 0.01                          # Thon rings listed where temporal x spatial coherence envelope >= 1 %
SEG_MIN_PX = 9.0                                  # a segmentation averages at least a 3 x 3-pixel patch
SAMPLES = 64                                      # image sampling grid (64 x 64 points, thumbnail 32 x 32)
LEDGER_SAMPLES = 12


def quantize_kv(v):
    return min(KV_RANGE_V[1], max(KV_RANGE_V[0], round(v / KV_STEP_V) * KV_STEP_V))


def _lin2(A, b, x0, dt):
    """Exact solution of x' = A x + b over dt for a 2 x 2 matrix similar to a symmetric one (real eigenvalues): x(dt) and the integral of x."""
    w, P = np.linalg.eig(A)
    w, P = w.real, P.real
    Pi = np.linalg.inv(P)
    y0, c = Pi @ x0, Pi @ b
    y, iy = np.empty(2), np.empty(2)
    for n_, lam in enumerate(w):
        z = lam * dt
        p1 = dt if abs(z) < 1e-12 else math.expm1(z) / lam
        p2 = dt * dt / 2.0 if abs(z) < 1e-6 else (math.expm1(z) - z) / (lam * lam)
        y[n_] = math.exp(z) * y0[n_] + p1 * c[n_]
        iy[n_] = p1 * y0[n_] + p2 * c[n_]
    return P @ y, P @ iy


class JeolJem1400flashRuntime:
    """JEM-1400Flash HR: power, vacuum, airlock, gun, illumination, imaging, diffraction, camera, stage, holders and specimens (SI units)."""

    def __init__(self, instance_id='jem1400flash_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.rng = random.Random(0)                     # gauge / meter noise (fixed draws per cycle) and the camera-length calibration
        self.rng_acq = random.Random(1)                 # camera shot noise of the acquisitions (separate stream)
        self.rng_life = random.Random(2)                # filament lives
        self.rng_mount = random.Random(3)               # grid mounting in the holder
        self.rng_af = random.Random(4)                  # auto-focus error
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.error = None
        # vacuum: delivered with the column left under vacuum and the valves closed (idle)
        self.p_col = P_START_IDLE_PA
        self.p_gun = P_START_IDLE_PA
        self.tmp_speed = 0.0
        self.vac_state = 'idle'
        self.p_al = P_ATM
        self.lever_pump = False
        self.holder_t0 = None                          # insertion time of the holder into the column (outgassing)
        self.readings = None
        # gun
        self.ht_on = False
        self.kv_set = 120000.0
        self.kv_act = 0.0
        self.fil_on = False
        self.heat = 0.0
        self.bias = 40.0
        self.fil_serial = 1
        self.fil_life_s = FILAMENT_LIFE_S
        self.fil_used_s = 0.0
        self.fil_burned = False
        # optics (left by the last user at x30,000 with the apertures out, facility procedures)
        self.mode = 'MAG'
        self.mag_idx = {'LOW_MAG': LOW_MAG_STEPS.index(100), 'MAG': MAG_STEPS.index(30000)}
        self.cl_idx = CAMERA_LENGTHS_M.index(0.5)
        self.mag_knob = self.mag_idx['MAG'] / (len(MAG_STEPS) - 1)      # MAGNIFICATION knob position (0-1, endless encoder abstracted to one turn)
        self.cl_err = 0.0
        self.brightness = 0.8
        self.spot = 1
        self.dz_display = 0.0
        self.cond_in = False
        self.ol_pos = 0
        self.sa_pos = 0
        self.screen_up = False
        self.screen_angle = 0.0
        # stage and holders
        self.stage = dict(x=0.0, y=0.0, z=0.0, tilt=0.0)
        self.stage_target = dict(self.stage)
        self.holder_in = None
        self.holder_pos = 'bench'
        self.withdrawal = HOLDER_STROKE_M['removed']
        self.withdrawal_target = self.withdrawal
        self.grids = {h: None for h in HOLDERS}
        # specimens, records, operations
        self.specimens = {}
        self.images, self.patterns, self.montages = {}, {}, {}
        self.img_seq = self.dp_seq = self.llp_seq = self.op_seq = 0
        self.acq = None
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.used_substances = set()
        self.led = {k: _Sum() for k in LEDGER_FLOWS}
        self._n0 = None
        self._frac_cache = None
        self._knots = []                                # [time, corner, stage targets, stage x/y/z/tilt] at every motion piece of the cycle
        self._fluence_cache = None                      # fluence at the view-fraction sample points (incremental while the geometry is unchanged)
        self._principals, self._requests, self._owners = {}, {}, {}

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
        return JeolJem1400flashSession(self, name)

    def _event(self, kind, code, message, rid=None, t=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.time_s if t is None else t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _room_k(self):
        return self.plant['room_k'] if self.plant else 295.15

    def _airlock_sealed(self):
        return self.holder_pos == 'airlock'

    def _gas_mol(self):
        rt = GAS_CONSTANT * self._room_k()
        n = (self.p_col * V_COLUMN_M3 + self.p_gun * V_GUN_M3) / rt
        if self._airlock_sealed():
            n += self.p_al * V_AIRLOCK_M3 / rt
        return n

    def _spec(self):
        """The grid in the column (None when no holder or an empty holder is in the column)."""
        if self.holder_pos != 'column' or self.holder_in is None:
            return None
        sid = self.grids[self.holder_in]
        return self.specimens[sid] if sid else None

    def _tilt_limit(self):
        return TILT_LIMITS_RAD.get(self.holder_in, TILT_LIMITS_RAD['quick_change'])

    def _geometry(self):
        """View centre on the specimen (u, v), tilt and the specimen height on the optical axis above the eucentric plane."""
        spec = self._spec()
        h = (spec['_mount']['h'] if spec else 0.0) + self.stage['z']
        t = self.stage['tilt']
        return self.stage['x'], self.stage['y'] + h * math.tan(t), t, h / math.cos(t)

    def _defocus_true(self):
        return self.dz_display - self._geometry()[3]

    def _camera_length_true(self):
        return CAMERA_LENGTHS_M[self.cl_idx] * (1.0 + self.cl_err)

    def _mag(self, mode=None):
        mode = mode or (self.mode if self.mode != 'DIFF' else 'MAG')
        return float((LOW_MAG_STEPS if mode == 'LOW_MAG' else MAG_STEPS)[self.mag_idx[mode]])

    def _steps(self):
        return CAMERA_LENGTHS_M if self.mode == 'DIFF' else LOW_MAG_STEPS if self.mode == 'LOW_MAG' else MAG_STEPS

    def _step_index(self):
        return self.cl_idx if self.mode == 'DIFF' else self.mag_idx[self.mode]

    def _knob_to_step(self):
        """A command that selects a step (mode, magnification, camera length) puts the MAGNIFICATION knob on that step's detent."""
        self.mag_knob = self._step_index() / (len(self._steps()) - 1)

    def _alpha(self):
        """Collection semi-angle behind the specimen: objective aperture (MAG, DIFF) or the lens bore; LOW MAG: lens bore."""
        if self.mode != 'LOW_MAG' and self.ol_pos > 0:
            return OL_APERTURES_M[self.ol_pos - 1] / (2.0 * OBJ_FOCAL_M)
        return OL_OUT_ANGLE_RAD

    def _field_stop(self):
        """LOW MAG with the objective aperture inserted: the aperture limits the field to a disc of its diameter at the specimen."""
        if self.mode == 'LOW_MAG' and self.ol_pos > 0:
            cu, cv, _, _ = self._geometry()
            return (cu, cv, 0.5 * OL_APERTURES_M[self.ol_pos - 1])
        return None

    def _sa_stop(self):
        """MAG and DIFF with the selected-area aperture inserted: the aperture in the first image plane passes only a disc of its diameter / 30 at
        the specimen (the selected area); LOW MAG is not limited by it."""
        if self.mode in ('MAG', 'DIFF') and self.sa_pos > 0:
            cu, cv, _, _ = self._geometry()
            return (cu, cv, 0.5 * SA_APERTURES_M[self.sa_pos - 1] / SA_MAGNIFICATION)
        return None

    def _emission(self):
        if not (self.power and self.ht_on and self.kv_act > 0.0 and self.fil_on and not self.fil_burned):
            return 0.0
        return EMISSION_BIAS0_A * math.exp(-self.bias / BIAS_SCALE) * self.heat

    def _specimen_current(self):
        return self._emission() * GUN_TRANSMISSION * SPOT_FACTORS[self.spot - 1] * (1.0 if self.cond_in else CL_OUT_FACTOR)

    def _illum_d(self):
        d0 = ILLUM_D_MIN_M * math.sqrt(SPOT_FACTORS[self.spot - 1])
        d = d0 + (ILLUM_D_MAX_M - d0) * abs(2.0 * self.brightness - 1.0)
        return d * (LOW_MAG_ILLUM_FACTOR if self.mode == 'LOW_MAG' else 1.0)

    def _convergence(self):
        """Illumination convergence semi-angle (rad): largest at the crossover, falling as the beam is spread."""
        d0 = ILLUM_D_MIN_M * math.sqrt(SPOT_FACTORS[self.spot - 1])
        return max(CONVERGENCE_MIN_RAD, CONVERGENCE_CROSSOVER_RAD * d0 / self._illum_d())

    def _current_density(self):
        d = self._illum_d()
        return self._specimen_current() / (math.pi * d * d / 4.0)

    def _beam_on(self):
        return self._emission() > 0.0

    def _beam_ready(self):
        return self._beam_on() and self.heat >= 1.0 - 1e-9 and abs(self.kv_act - self.kv_set) < 0.5

    def _filament_state(self):
        if self.fil_burned:
            return 'burned_out'
        if not (self.fil_on and self.ht_on and self.power):
            return 'off'
        return 'saturated' if self.heat >= 1.0 - 1e-9 else 'heating'

    def _stage_moving(self):
        return any(abs(self.stage[k] - self.stage_target[k]) > 1e-15 for k in self.stage)

    def _screen_raised(self):
        return self.screen_up and self.screen_angle >= SCREEN_UP_ANGLE_RAD - 1e-12

    def _airlock_state(self):
        if self.holder_pos in ('turning_in', 'column', 'withdrawing'):
            return 'column'
        if not self._airlock_sealed():
            return 'open'
        if not self.power:
            return 'idle'                                   # no gauge, lamp or valve without power
        if self.lever_pump:
            return 'evacuated' if self.p_al < AIRLOCK_READY_PA else 'pumping'
        return 'vented' if self.p_al > 0.9 * P_ATM else 'venting'

    def _tilt_display(self):
        """Tilt as the goniometer reports it (0.1 deg resolution)."""
        return round(self.stage['tilt'] / math.radians(0.1)) * math.radians(0.1)

    def _stage_neutral(self):
        return (all(abs(self.stage_target[k]) <= STAGE_NEUTRAL_TOL_M and abs(self.stage[k]) <= STAGE_NEUTRAL_TOL_M for k in ('x', 'y', 'z'))
                and abs(self.stage['tilt']) <= STAGE_NEUTRAL_TOL_RAD and abs(self.stage_target['tilt']) <= STAGE_NEUTRAL_TOL_RAD)

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        m = self.readings if self.power else None
        acq = self.acq
        cu, cv, t, h_axis = self._geometry()
        spec = self._spec()
        dose_c = float(dose_at(spec, np.array([cu]), np.array([cv]))[0][0]) if spec else 0.0          # fluence through the view-centre point
        mode = self.mode
        disp_mag = None
        if self.power:
            disp_mag = CAMERA_LENGTHS_M[self.cl_idx] * 1000.0 if mode == 'DIFF' else self._mag()
        values = {
            'clock.time_s': self.time_s, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.error_code': self.error, 'vacuum.state': self.vac_state,
            'vacuum.column_pressure_indicated_pa': m['col'] if m else None, 'vacuum.gun_pressure_indicated_pa': m['gun'] if m else None,
            'airlock.pump': self.lever_pump, 'airlock.state': self._airlock_state(), 'airlock.pressure_indicated_pa': m['al'] if m else None,
            'panel.airlock_lamp': bool(self.power and self._airlock_state() == 'evacuated'), 'panel.power_lamp': self.power,
            'panel.beam_lamp': self._beam_on(),
            'holder.position': self.holder_pos, 'holder.in_goniometer': self.holder_in, 'holder.grids': dict(self.grids),
            'specimen.bench_ids': sorted(k for k, s in self.specimens.items() if s['location'] == 'bench'),
            'ht.on': self.ht_on, 'ht.voltage_setpoint_v': self.kv_set, 'ht.voltage_indicated_v': m['kv'] if m else None,
            'gun.dark_current_indicated_a': m['dark'] if m else None, 'gun.beam_current_indicated_a': m['beam'] if m else None,
            'filament.on': self.fil_on, 'filament.state': self._filament_state(), 'filament.bias': self.bias,
            'optics.mode': mode, 'optics.magnification': self._mag(), 'optics.camera_length_m': CAMERA_LENGTHS_M[self.cl_idx],
            'optics.magnification_step_fraction': self.mag_knob, 'optics.brightness': self.brightness,
            'optics.spot_size': self.spot, 'optics.defocus_display_m': self.dz_display,
            'aperture.condenser_inserted': self.cond_in, 'aperture.objective_position': self.ol_pos, 'aperture.selected_area_position': self.sa_pos,
            'screen.up': self.screen_up, 'screen.moving': self.power and abs(self.screen_angle - (SCREEN_UP_ANGLE_RAD if self.screen_up else 0.0)) > 1e-12,
            'stage.x_indicated_m': round(self.stage['x'], 7) if self.power else None, 'stage.y_indicated_m': round(self.stage['y'], 7) if self.power else None,
            'stage.z_indicated_m': round(self.stage['z'], 7) if self.power else None,
            'stage.tilt_indicated_rad': self._tilt_display() if self.power else None,
            'stage.moving': self._stage_moving(),
            'illumination.screen_current_density_a_m2': m['screen'] if m else None,
            'display.mag_or_camera_length_text': None if disp_mag is None else str(int(round(disp_mag))),
            'acquisition.id': acq['record_id'] if acq else None, 'acquisition.kind': acq['kind'] if acq else None,
            'acquisition.progress': min(1.0, (self.time_s - acq['t0']) / acq['duration']) if acq else None, 'acquisition.started_by_other_agent': False,
            'result.last_image_id': None, 'result.last_pattern_id': None, 'result.last_montage_id': None,
            'holder.withdrawal_m': self.withdrawal, 'stage.x_m': self.stage['x'], 'stage.y_m': self.stage['y'], 'stage.z_m': self.stage['z'],
            'stage.tilt_rad': self.stage['tilt'], 'screen.angle_rad': self.screen_angle,
            'airlock.lever_rad': AIRLOCK_LEVER_PUMP_RAD if self.lever_pump else 0.0, 'aperture.cl_lever_rad': CL_LEVER_IN_RAD if self.cond_in else 0.0,
            'aperture.ol_dial_rad': self.ol_pos * DIAL_STEP_RAD, 'aperture.sa_dial_rad': self.sa_pos * DIAL_STEP_RAD,
            'truth.column_pressure_pa': self.p_col, 'truth.gun_pressure_pa': self.p_gun,
            'truth.airlock_pressure_pa': self.p_al if self._airlock_sealed() else (self.p_col if self.holder_pos in ('turning_in', 'column', 'withdrawing') else P_ATM),
            'truth.tmp_speed_fraction': self.tmp_speed, 'truth.filament_on_time_s': self.fil_used_s, 'truth.filament_life_s': self.fil_life_s,
            'truth.emission_a': self._emission(), 'truth.specimen_current_a': self._specimen_current(), 'truth.current_density_a_m2': self._current_density(),
            'truth.illuminated_diameter_m': self._illum_d(), 'truth.defocus_m': self._defocus_true(), 'truth.specimen_height_m': h_axis,
            'truth.camera_length_m': self._camera_length_true(), 'truth.view_center_dose_c_m2': dose_c,
            'truth.specimens': {k: dict(location=s['location'], grid_mesh=s['grid_mesh'], grid_material=s['grid_material'], support_film=s['support_film'],
                                        support_thickness_m=s['support_thickness_m'], hole_fraction=s['hole_fraction'], phases=copy.deepcopy(s['phases']),
                                        mount=copy.deepcopy(s['_mount']), dose_events=len(s['_dose'])) for k, s in self.specimens.items()},
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

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
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):     # sim.step counts its own revision when time advances
            self.revision += 1
            self._frac_cache = None
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

    def _need_holder_still(self):
        if self.holder_pos in ('entering', 'turning_in', 'withdrawing', 'removing'):
            raise ActionRejected('HOLDER_MOVING')

    def _cmd_plant_configure(self, room_k, relative_humidity, mains_voltage_v, cooling_water_m3_s, noise_seed):
        first = self.plant is None
        if not first:
            noise_seed = self.plant['noise_seed']
            f = room_k / self.plant['room_k']              # the enclosed gas follows the room temperature at constant volume
            self.p_col *= f
            self.p_gun *= f
            if self._airlock_sealed():
                self.p_al *= f
            elif self.holder_pos in ('bench', 'entering', 'removing'):
                self.p_al = P_ATM
        self.plant = dict(room_k=room_k, relative_humidity=relative_humidity, mains_voltage_v=mains_voltage_v, cooling_water_m3_s=cooling_water_m3_s,
                          noise_seed=noise_seed)
        if first:
            self.rng = random.Random(noise_seed)
            self.rng_acq = random.Random(noise_seed * 7919 + 17)
            self.rng_life = random.Random(noise_seed * 104729 + 3)
            self.rng_mount = random.Random(noise_seed * 1299709 + 5)
            self.rng_af = random.Random(noise_seed * 15485863 + 7)
            self.fil_life_s = self._draw_life()
            self.cl_err = max(-0.06, min(0.06, self.rng.gauss(0.0, CAMERA_LENGTH_ERROR_SD)))
            self._n0 = self._gas_mol()
        if not ROOM_RANGE_K[0] <= room_k <= ROOM_RANGE_K[1]:
            self._event('warning', 'ROOM_OUT_OF_SPEC', '室温超出安装要求15–25°C（同平台）')
        if relative_humidity > 0.60:
            self._event('warning', 'HUMIDITY_OUT_OF_SPEC', '相对湿度超过安装要求60%（同平台）')
        if cooling_water_m3_s < COOLING_FLOW_MIN_M3_S - 1e-15:
            self._event('warning', 'COOLING_WATER_LOW', '冷却水流量低于5L/min（8.33×10⁻⁵m³/s）：高压不能开启，已开的高压在下一周期关闭')
        return dict(configured=True)

    def _draw_life(self):
        u = max(1e-12, 1.0 - self.rng_life.random())
        return FILAMENT_LIFE_S / math.gamma(1.0 + 1.0 / FILAMENT_WEIBULL_K) * (-math.log(u)) ** (1.0 / FILAMENT_WEIBULL_K)

    def _cmd_specimen_provide(self, specimen_id, grid_mesh, grid_material, support_film, support_thickness_m, hole_fraction, phases):
        self._need_plant()
        if specimen_id in self.specimens or len(self.specimens) >= MAX_SPECIMENS:
            raise ActionRejected('SPECIMEN_EXISTS')
        if (support_film == 'none') != (support_thickness_m == 0.0) or (support_film == 'none' and hole_fraction > 0.0):
            raise ActionRejected('SUPPORT_INVALID')
        lib = _lib()['substances']
        for p in phases:
            if p['substance_id'] not in lib:
                raise ActionRejected('UNKNOWN_SUBSTANCE')
        for p in phases:
            sub = lib[p['substance_id']]
            crystalline = sub.get('crystal') is not None
            if not crystalline and p['crystallinity'] != 'amorphous':
                raise ActionRejected('CRYSTALLINITY_INVALID')
            if p['zone_axis'] is not None and (p['crystallinity'] != 'single_crystal' or not any(p['zone_axis'])):
                raise ActionRejected('ZONE_AXIS_INVALID')
            if p['stain_mass_fraction'] > 0.0 and p['crystallinity'] != 'amorphous':
                raise ActionRejected('STAIN_INVALID')
            if p['form'] == 'particles':
                if p['coverage'] > PARTICLE_COVERAGE_MAX:
                    raise ActionRejected('COVERAGE_TOO_HIGH')
                if support_film == 'none':
                    raise ActionRejected('SUPPORT_REQUIRED')
            if p['coverage'] <= 0.0:
                raise ActionRejected('COVERAGE_INVALID')
        seed = int(_hash01(sum(ord(c) * (i + 1) for i, c in enumerate(specimen_id)), len(specimen_id), self.plant['noise_seed']) * 2 ** 31)
        self.specimens[specimen_id] = dict(grid_mesh=grid_mesh, grid_material=grid_material, support_film=support_film, support_thickness_m=support_thickness_m,
                                           hole_fraction=hole_fraction, phases=copy.deepcopy(phases), location='bench', _seed=seed,
                                           _mount=dict(h=0.0, phi=0.0, off_a=0.0, off_b=0.0), _dose=[], _dose_new=[], _dose_epoch=0,
                                           _damageable=any(substance(x)['damage']['mode'] != 'none'
                                                           for x in [p['substance_id'] for p in phases] + ([support_film] if support_film != 'none' else [])
                                                           + ([STAIN_ID] if any(p['stain_mass_fraction'] > 0.0 for p in phases) else [])))
        self.used_substances.update(p['substance_id'] for p in phases)
        if support_film != 'none':
            self.used_substances.add(support_film)
        if any(p['stain_mass_fraction'] > 0.0 for p in phases):
            self.used_substances.add(STAIN_ID)
        return dict(specimen_id=specimen_id)

    # ---- holders and airlock (robot skills of the host; process-level abstraction)
    def _cmd_holder_load_grid(self, holder_id, specimen_id):
        self._need_plant()
        if self.holder_in == holder_id:
            raise ActionRejected('HOLDER_IN_GONIOMETER')
        if specimen_id not in self.specimens:
            raise ActionRejected('UNKNOWN_SPECIMEN')
        if self.specimens[specimen_id]['location'] != 'bench':
            raise ActionRejected('SPECIMEN_NOT_ON_BENCH')
        if self.grids[holder_id] is not None:
            raise ActionRejected('HOLDER_OCCUPIED')
        s = self.specimens[specimen_id]
        mesh = GRID_MESH[int(s['grid_mesh'])]
        s['location'] = holder_id
        s['_mount'] = dict(h=self.rng_mount.gauss(0.0, GRID_Z_SD_M), phi=self.rng_mount.uniform(0.0, math.pi / 2.0),
                           off_a=self.rng_mount.uniform(0.0, mesh['pitch']), off_b=self.rng_mount.uniform(0.0, mesh['pitch']))
        self.grids[holder_id] = specimen_id
        self._event('specimen', 'GRID_LOADED', f'载网{specimen_id}装入{holder_id}样品杆', None)
        return dict(holder_id=holder_id, specimen_id=specimen_id)

    def _cmd_holder_unload_grid(self, holder_id):
        self._need_plant()
        if self.holder_in == holder_id:
            raise ActionRejected('HOLDER_IN_GONIOMETER')
        sid = self.grids[holder_id]
        if sid is None:
            raise ActionRejected('HOLDER_EMPTY')
        self.grids[holder_id] = None
        self.specimens[sid]['location'] = 'bench'
        self._event('specimen', 'GRID_UNLOADED', f'载网{sid}从{holder_id}样品杆卸下', None)
        return dict(holder_id=holder_id, specimen_id=sid)

    def _cmd_holder_insert_to_airlock(self, holder_id):
        self._need_plant()
        self._need_idle()
        if self.holder_in is not None:
            raise ActionRejected('GONIOMETER_OCCUPIED')
        if self.fil_on:
            raise ActionRejected('FILAMENT_ON')
        if not self._stage_neutral():
            raise ActionRejected('STAGE_NOT_NEUTRAL')
        self.holder_in = holder_id
        self.holder_pos = 'entering'
        self.withdrawal_target = HOLDER_STROKE_M['airlock']
        if self.ol_pos > 0:
            self._event('warning', 'OL_APERTURE_INSERTED', '插杆时物镜光阑没有退出（设施规程要求退出并记录）')
        self._event('holder', 'HOLDER_ENTERING', f'{holder_id}样品杆对准导销推向气锁止点')
        return dict(holder_id=holder_id, position='entering')

    def _cmd_airlock_set_pump(self, pump):
        self._need_plant()
        self.lever_pump = pump
        return dict(pump=pump)

    def _cmd_holder_insert(self):
        self._need_power()
        self._need_idle()
        if self.holder_pos != 'airlock':
            raise ActionRejected('HOLDER_NOT_AT_AIRLOCK')
        if self.fil_on:
            raise ActionRejected('FILAMENT_ON')
        if not (self.lever_pump and self.p_al < AIRLOCK_READY_PA):
            raise ActionRejected('AIRLOCK_NOT_EVACUATED')
        # the valve to the column opens: the airlock gas joins the column
        self.p_col += self.p_al * V_AIRLOCK_M3 / V_COLUMN_M3
        self.p_al = self.p_col
        self.holder_pos = 'turning_in'
        self.withdrawal_target = 0.0
        self._event('holder', 'HOLDER_TURNING_IN', '样品杆顺时针转入，气锁残余气体进入镜筒')
        return dict(position='turning_in')

    def _cmd_holder_withdraw(self):
        self._need_plant()
        self._need_idle()
        if self.holder_pos != 'column':
            raise ActionRejected('HOLDER_NOT_IN_COLUMN')
        if self.fil_on:
            raise ActionRejected('FILAMENT_ON')
        if not self._stage_neutral():
            raise ActionRejected('STAGE_NOT_NEUTRAL')
        if self.ol_pos > 0:
            self._event('warning', 'OL_APERTURE_INSERTED', '拔杆时物镜光阑没有退出（设施规程要求先退出）')
        self.holder_pos = 'withdrawing'
        self.withdrawal_target = HOLDER_STROKE_M['airlock']
        self.holder_t0 = None
        self._event('holder', 'HOLDER_WITHDRAWING', '样品杆拉回气锁止点')
        return dict(position='withdrawing')

    def _cmd_holder_remove(self):
        self._need_plant()
        self._need_idle()
        if self.holder_pos != 'airlock':
            raise ActionRejected('HOLDER_NOT_AT_AIRLOCK')
        if not (not self.lever_pump and self.p_al > 0.9 * P_ATM):
            raise ActionRejected('AIRLOCK_NOT_VENTED')
        # the O-ring leaves the airlock: its gas returns to the room
        self.led['gas_released_mol'].add(self.p_al * V_AIRLOCK_M3 / (GAS_CONSTANT * self._room_k()))
        self.p_al = P_ATM
        self.holder_pos = 'removing'
        self.withdrawal_target = HOLDER_STROKE_M['removed']
        self.led['airlock_cycles'].add(1.0)
        self._event('holder', 'HOLDER_REMOVING', '气锁已放气，样品杆拔出')
        return dict(holder_id=self.holder_in)

    # ---- power, errors
    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            if abs(self.plant['mains_voltage_v'] - MAINS_NOMINAL_V) > MAINS_TOL * MAINS_NOMINAL_V + 1e-9:
                raise ActionRejected('MAINS_OUT_OF_RANGE')
            self.power = True
            self.state, self.state_t0 = 'booting', self.time_s
            self.error = None
            self.vac_state = 'pumping'
            self._event('power', 'POWER_ON', 'MAIN POWER接通：TEM Center加载约90s，涡轮分子泵起转抽真空')
        elif not enabled and self.power:
            self._power_off()
        return dict(enabled=self.power)

    def _cmd_power_key_toggle(self):
        return self._cmd_power_set(not self.power)

    def _power_off(self):
        self._stop_acq('failed', 'POWER_OFF')
        self.stage_target = dict(self.stage)             # the stage controller loses its move: the stage stays where the power went off
        self.power = False
        self.state = 'off'
        self.ht_on = False
        self.kv_act = 0.0
        self.fil_on = False
        self.vac_state = 'idle'
        self.readings = None
        self._event('power', 'POWER_OFF', 'MAIN POWER断开：高压、灯丝与泵停止，真空阀关闭')

    def _cmd_error_acknowledge(self):
        self._need_power()
        if self.error is None:
            raise ActionRejected('NO_ERROR')
        code, self.error = self.error, None
        return dict(acknowledged=code)

    # ---- gun
    def _cmd_ht_set_voltage(self, voltage_v):
        self._need_ready()
        self._need_idle()
        self.kv_set = quantize_kv(voltage_v)
        return dict(voltage_v=self.kv_set)

    def _cmd_ht_set_on(self, on):
        self._need_ready(allow_error=not on)
        if on and not self.ht_on:
            self._need_idle()
            if self.vac_state != 'ready':
                raise ActionRejected('VACUUM_NOT_READY')
            if self.plant['cooling_water_m3_s'] < COOLING_FLOW_MIN_M3_S - 1e-15:
                raise ActionRejected('COOLING_WATER_LOW')
            self.ht_on = True
            self._event('gun', 'HT_ON', f'高压开启，按1kV/s升到{self.kv_set / 1000:.1f}kV')
        elif not on and self.ht_on:
            self.ht_on = False
            self.kv_act = 0.0
            self.fil_on = False
            self._event('gun', 'HT_OFF', '高压关闭（灯丝随之关闭）')
        return dict(on=self.ht_on)

    def _cmd_filament_set_on(self, on):
        self._need_ready(allow_error=not on)
        if on and not self.fil_on:
            self._need_idle()
            if self.fil_burned:
                raise ActionRejected('FILAMENT_BURNED_OUT')
            if not self.ht_on or abs(self.kv_act - self.kv_set) >= 0.5:
                raise ActionRejected('HT_NOT_READY')
            if self.vac_state != 'ready':
                raise ActionRejected('VACUUM_NOT_READY')
            self._need_holder_still()
            self.fil_on = True
            self._event('gun', 'FILAMENT_ON', '灯丝开启，约20s加热到饱和')
        elif not on and self.fil_on:
            self.fil_on = False
            self._event('gun', 'FILAMENT_OFF', '灯丝关闭')
        return dict(on=self.fil_on)

    def _cmd_filament_set_bias(self, bias):
        self._need_ready()
        self._need_idle()
        self.bias = float(bias)
        return dict(bias=self.bias)

    def _cmd_gun_replace_filament(self):
        self._need_plant()
        if self.ht_on:
            raise ActionRejected('HT_ON')
        self.fil_serial += 1
        self.fil_burned = False
        self.fil_on = False
        self.heat = 0.0
        self.fil_used_s = 0.0
        self.fil_life_s = self._draw_life()
        if self.error == 'FILAMENT_BURNOUT':
            self.error = None
        self._event('maintenance', 'FILAMENT_REPLACED', f'更换钨灯丝（序号{self.fil_serial}）')
        return dict(filament_serial=self.fil_serial)

    # ---- optics
    def _cmd_optics_set_mode(self, mode):
        self._need_ready()
        self._need_idle()
        if mode == 'DIFF' and self.mode != 'MAG' and self.mode != 'DIFF':
            raise ActionRejected('NOT_IN_MAG_MODE')
        self.mode = mode
        self._knob_to_step()
        return dict(mode=self.mode)

    def _cmd_optics_toggle_diffraction(self):
        self._need_ready()
        self._need_idle()
        if self.mode == 'LOW_MAG':
            raise ActionRejected('NOT_IN_MAG_MODE')
        self.mode = 'MAG' if self.mode == 'DIFF' else 'DIFF'
        self._knob_to_step()
        return dict(mode=self.mode)

    def _cmd_optics_set_magnification(self, magnification):
        self._need_ready()
        self._need_idle()
        if self.mode == 'DIFF':
            raise ActionRejected('DIFF_MODE')
        self.mag_idx[self.mode] = _nearest(LOW_MAG_STEPS if self.mode == 'LOW_MAG' else MAG_STEPS, magnification)
        self._knob_to_step()
        return dict(magnification=self._mag(), mode=self.mode)

    def _cmd_optics_set_camera_length(self, camera_length_m):
        self._need_ready()
        self._need_idle()
        self.cl_idx = _nearest(CAMERA_LENGTHS_M, camera_length_m)
        if self.mode == 'DIFF':
            self._knob_to_step()
        return dict(camera_length_m=CAMERA_LENGTHS_M[self.cl_idx])

    def _cmd_optics_set_magnification_step_fraction(self, fraction):
        self._need_ready()
        self._need_idle()
        n = len(self._steps())
        i = int(round(fraction * (n - 1)))
        self.mag_knob = float(fraction)                                 # the knob stays where the hand left it; the nearest step is selected
        if self.mode == 'DIFF':
            self.cl_idx = i
            return dict(fraction=self.mag_knob, magnification=None, camera_length_m=CAMERA_LENGTHS_M[i])
        self.mag_idx[self.mode] = i
        return dict(fraction=self.mag_knob, magnification=self._mag(), camera_length_m=None)

    def _cmd_optics_set_brightness(self, value):
        self._need_ready()
        self._need_idle()
        self.brightness = float(value)
        return dict(value=self.brightness)

    def _cmd_optics_set_spot_size(self, spot):
        self._need_ready()
        self._need_idle()
        self.spot = int(spot)
        return dict(spot=self.spot)

    def _cmd_optics_set_defocus(self, defocus_m):
        self._need_ready()
        self._need_idle()
        self.dz_display = float(defocus_m)
        return dict(defocus_m=self.dz_display)

    def _cmd_optics_standard_focus(self):
        self._need_ready()
        self._need_idle()
        self.dz_display = 0.0
        return dict(defocus_m=0.0)

    def _cmd_optics_auto_focus(self):
        self._need_ready()
        self._need_idle()
        self._need_camera_view()
        if self.mode != 'MAG':
            raise ActionRejected('NOT_IN_MAG_MODE')
        f = self._view_fractions()
        if f['structure'] < 0.02:
            raise ActionRejected('AUTOFOCUS_FAILED')
        h = self._geometry()[3]
        if not DEFOCUS_RANGE_M[0] + 5.0 * AUTOFOCUS_SD_M <= h <= DEFOCUS_RANGE_M[1] - 5.0 * AUTOFOCUS_SD_M:
            raise ActionRejected('AUTOFOCUS_FAILED')                 # the focus search range: decided before any draw (a refusal changes nothing)
        self.dz_display = min(DEFOCUS_RANGE_M[1], max(DEFOCUS_RANGE_M[0], h + self.rng_af.gauss(0.0, AUTOFOCUS_SD_M)))
        return dict(defocus_m=self.dz_display)

    def _need_camera_view(self):
        """The camera sees the beam: beam ready, screen raised, a grid in the column."""
        if not self._beam_ready():
            raise ActionRejected('BEAM_NOT_READY')
        if not self._screen_raised():
            raise ActionRejected('SCREEN_DOWN')

    # ---- apertures and screen
    def _cmd_aperture_set_condenser(self, inserted):
        self._need_plant()
        self._need_idle()
        self.cond_in = inserted
        return dict(inserted=inserted)

    def _cmd_aperture_set_objective(self, position):
        self._need_plant()
        self._need_idle()
        self.ol_pos = int(position)
        return dict(position=self.ol_pos, diameter_m=OL_APERTURES_M[self.ol_pos - 1] if self.ol_pos else None)

    def _cmd_aperture_set_selected_area(self, position):
        self._need_plant()
        self._need_idle()
        self.sa_pos = int(position)
        return dict(position=self.sa_pos, selected_diameter_m=SA_APERTURES_M[self.sa_pos - 1] / SA_MAGNIFICATION if self.sa_pos else None)

    def _cmd_screen_set(self, up):
        self._need_ready()
        self._need_idle()
        self.screen_up = up
        return dict(up=up)

    def _cmd_screen_toggle(self):
        return self._cmd_screen_set(not self.screen_up)

    # ---- stage
    def _need_stage(self):
        self._need_ready()
        self._need_idle()
        self._need_holder_still()
        if self.holder_pos != 'column':
            raise ActionRejected('HOLDER_NOT_IN_COLUMN')

    def _cmd_stage_move_xy(self, x_m, y_m):
        self._need_stage()
        self.stage_target['x'], self.stage_target['y'] = float(x_m), float(y_m)
        return dict(x_m=float(x_m), y_m=float(y_m))

    def _cmd_stage_set_z(self, z_m):
        self._need_stage()
        self.stage_target['z'] = float(z_m)
        return dict(z_m=float(z_m))

    def _cmd_stage_set_tilt(self, tilt_rad):
        self._need_stage()
        if abs(tilt_rad) > self._tilt_limit() + 1e-12:
            raise ActionRejected('TILT_OUT_OF_RANGE')
        self.stage_target['tilt'] = float(tilt_rad)
        return dict(tilt_rad=float(tilt_rad))

    def _cmd_stage_neutral(self):
        self._need_ready()
        self._need_idle()
        self._need_holder_still()
        for k in self.stage_target:
            self.stage_target[k] = 0.0
        return dict(neutral=True)

    # ---- acquisitions
    def _new_acq(self, kind, duration, **data):
        self.op_seq += 1
        if kind == 'image':
            self.img_seq += 1
            rid = f'img-{self.img_seq}'
        elif kind == 'diffraction':
            self.dp_seq += 1
            rid = f'dp-{self.dp_seq}'
        else:
            self.llp_seq += 1
            rid = f'llp-{self.llp_seq}'
        op = dict(operation_id=f'{kind}-op-{self.op_seq}', kind=kind, record_id=rid, phase='running', t0=self.time_s, duration=duration, **data)
        self.operations[op['operation_id']] = op
        self.acq = op
        return op

    def _table(self, kind):
        return self.images if kind == 'image' else self.patterns if kind == 'diffraction' else self.montages

    def _need_acquire(self, table):
        self._need_ready()
        self._need_idle()
        self._need_holder_still()
        self._need_camera_view()
        if self._stage_moving():
            raise ActionRejected('STAGE_MOVING')
        if len(table) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')

    def _source(self, spec):
        """Template source of a record: the library only; which substances the specimen holds is hidden truth (provenance.read, evaluator)."""
        return dict(library=LIBRARY_REL, substances=[])

    def _cmd_image_acquire(self, exposure_s, binning):
        self._need_acquire(self.images)
        if self.mode == 'DIFF':
            raise ActionRejected('DIFF_MODE')
        op = self._new_acq('image', exposure_s + CAMERA_READOUT_S, exposure_s=exposure_s, binning=binning)
        M = self._mag()
        self.images[op['record_id']] = dict(image_id=op['record_id'], status='running', mode=self.mode, accelerating_voltage_v=self.kv_act, magnification=M,
                                            exposure_s=exposure_s, binning=binning, pixel_size_m=CAMERA_PIXEL_M * binning / M,
                                            field_of_view_m=CAMERA_PIXELS * CAMERA_PIXEL_M / M, defocus_display_m=self.dz_display,
                                            objective_aperture_position=self.ol_pos, tilt_rad=self._tilt_display(), dose_estimate_e_per_m2=None, regions=[],
                                            thon_ring_frequencies_per_m=[], saturated_fraction=None, thumbnail=[], error_code=None, started_s=self.time_s,
                                            finished_s=None, template_source=dict(library=LIBRARY_REL, substances=[]), _op=op['operation_id'])
        self._event('acquisition', 'IMAGE_START', f'开始采集图像{op["record_id"]}（曝光{exposure_s:g}s，binning {binning}）', op['record_id'])
        return dict(operation_id=op['operation_id'])

    def _cmd_image_result(self, image_id):
        rec = self.images.get(image_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._out(rec)

    def _cmd_diffraction_acquire(self, exposure_s, binning):
        self._need_acquire(self.patterns)
        if self.mode != 'DIFF':
            raise ActionRejected('NOT_IN_DIFF_MODE')
        op = self._new_acq('diffraction', exposure_s + CAMERA_READOUT_S, exposure_s=exposure_s, binning=binning)
        self.patterns[op['record_id']] = dict(pattern_id=op['record_id'], status='running', accelerating_voltage_v=self.kv_act,
                                              camera_length_display_m=CAMERA_LENGTHS_M[self.cl_idx], exposure_s=exposure_s, binning=binning,
                                              selected_area_diameter_m=SA_APERTURES_M[self.sa_pos - 1] / SA_MAGNIFICATION if self.sa_pos else None,
                                              tilt_rad=self._tilt_display(), rings=[], spots=[], halos=[], central_beam_saturated=None, error_code=None,
                                              started_s=self.time_s, finished_s=None, template_source=dict(library=LIBRARY_REL, substances=[]),
                                              _op=op['operation_id'])
        self._event('acquisition', 'DIFF_START', f'开始采集衍射{op["record_id"]}（相机长度{CAMERA_LENGTHS_M[self.cl_idx]:g}m，曝光{exposure_s:g}s）', op['record_id'])
        return dict(operation_id=op['operation_id'])

    def _cmd_diffraction_result(self, pattern_id):
        rec = self.patterns.get(pattern_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._out(rec)

    def _cmd_montage_acquire(self, rows, cols, overlap, exposure_s, binning):
        self._need_acquire(self.montages)
        if self.mode == 'DIFF':
            raise ActionRejected('DIFF_MODE')
        if self.holder_pos != 'column':
            raise ActionRejected('HOLDER_NOT_IN_COLUMN')
        M = self._mag()
        fov = CAMERA_PIXELS * CAMERA_PIXEL_M / M
        step = fov * (1.0 - overlap)
        x0, y0 = self.stage['x'], self.stage['y']
        tiles = []
        for r in range(rows):
            order = range(cols) if r % 2 == 0 else range(cols - 1, -1, -1)
            for c in order:
                tiles.append((r, c, x0 + (c - (cols - 1) / 2.0) * step, y0 + (r - (rows - 1) / 2.0) * step))
        if any(not (STAGE_XY_RANGE_M[0] <= tx <= STAGE_XY_RANGE_M[1] and STAGE_XY_RANGE_M[0] <= ty <= STAGE_XY_RANGE_M[1]) for _, _, tx, ty in tiles):
            raise ActionRejected('MONTAGE_OUT_OF_RANGE')
        sched, t, px, py = [], 0.0, x0, y0
        for r, c, tx, ty in tiles:
            move = max(abs(tx - px), abs(ty - py)) / STAGE_XY_SPEED
            sched.append(dict(r=r, c=c, x=tx, y=ty, t_move=t, t_exp=t + move + MONTAGE_SETTLE_S))
            t += move + MONTAGE_SETTLE_S + exposure_s + CAMERA_READOUT_S
            px, py = tx, ty
        op = self._new_acq('montage', t, rows=rows, cols=cols, overlap=overlap, exposure_s=exposure_s, binning=binning, schedule=sched, home=(x0, y0))
        self.montages[op['record_id']] = dict(montage_id=op['record_id'], status='running', rows=rows, cols=cols, overlap=overlap, magnification=M,
                                              exposure_s=exposure_s, binning=binning, tile_field_of_view_m=fov, tile_step_m=step, mosaic=[], regions=[],
                                              error_code=None, started_s=self.time_s, finished_s=None,
                                              template_source=dict(library=LIBRARY_REL, substances=[]), _op=op['operation_id'])
        self._event('acquisition', 'MONTAGE_START', f'开始拼图{op["record_id"]}（{rows}×{cols}，预计{t:.0f}s）', op['record_id'])
        return dict(operation_id=op['operation_id'])

    def _cmd_montage_result(self, montage_id):
        rec = self.montages.get(montage_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._out(rec)

    def _out(self, rec):
        return {k: copy.deepcopy(v) for k, v in rec.items() if not k.startswith('_')}

    def _cmd_camera_auto_exposure(self):
        self._need_ready()
        self._need_camera_view()
        if self.mode == 'DIFF':
            raise ActionRejected('DIFF_MODE')
        M = self._mag()
        f = self._view_fractions()
        p = CAMERA_PIXEL_M / M
        rate = self._current_density() / E_CHARGE * p * p * f['T_view'] * ADU_PER_ELECTRON
        if rate <= 0.0:
            raise ActionRejected('NO_SIGNAL')
        return dict(exposure_s=min(60.0, max(0.001, 10000.0 / rate)), mean_counts_per_s=rate)

    def _cmd_records_list(self):
        key = lambda k: int(k.split('-')[1])
        return dict(images=sorted(self.images, key=key), patterns=sorted(self.patterns, key=key), montages=sorted(self.montages, key=key))

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
        led['charge_residual_c'] = (led['beam_charge_c'] - led['gun_condenser_stopped_charge_c'] - led['grid_stopped_charge_c']
                                    - led['objective_aperture_stopped_charge_c'] - led['selected_area_aperture_stopped_charge_c'] - led['detector_charge_c'])
        n = self._gas_mol()
        led['gas_in_volume_mol'] = n
        led['gas_residual_mol'] = (n - (self._n0 if self._n0 is not None else n)) - (led['gas_admitted_mol'] - led['gas_pumped_mol'] - led['gas_released_mol'])
        return {k: led[k] for k in LEDGER_KEYS}

    # ---------------------------------------------------------------- beam on the specimen: fractions for the ledger and the screen reading
    def _view_fractions(self):
        """Averages over the illuminated disc for the charge ledger (grid-open fraction; bright-field fraction behind the objective aperture or
        its LOW MAG field stop; the part passing the selected-area aperture) and over the field of view (bright-field transmission incl.
        illumination and field stops, and the fraction showing specimen structure).  Stops are discs centred on the view centre: the parts
        behind them are sampled inside the smaller disc and weighted by its area.  Cached per optical state and the damage state in steps of
        0.02 (VIEW_DAMAGE_Q); the fluence at the sample points is updated incrementally while the geometry stays the same."""
        spec = self._spec()
        cu, cv, t, _ = self._geometry()
        r_ill = self._illum_d() / 2.0
        M = self._mag()
        fov = CAMERA_PIXELS * CAMERA_PIXEL_M / M
        alpha = self._alpha()
        oa, sa = self._field_stop(), self._sa_stop()
        r_oa = min(r_ill, oa[2]) if oa else r_ill                            # disc passing the objective-aperture field stop
        r_all = min(r_oa, sa[2]) if sa else r_oa                              # disc passing every stop
        half = min(fov / 2.0, r_all) if (oa or sa) else fov / 2.0             # view square that can receive electrons
        n = LEDGER_SAMPLES
        g = ((np.arange(n) + 0.5) / n) * 2.0 - 1.0
        X, Y = np.meshgrid(g, g)
        inside = (X * X + Y * Y <= 1.0).ravel()
        cos_t = math.cos(t)
        discs = {}
        for key_, r in (('ill', r_ill), ('oa', r_oa), ('all', r_all)):
            if r not in discs:
                discs[r] = ((X.ravel()[inside]) * r, (Y.ravel()[inside]) * r)
        Xv, Yv = X.ravel() * half, Y.ravel() * half
        geo = (id(spec), spec and spec['_seed'], round(cu, 10), round(cv, 10), round(t, 9), round(r_ill, 12), round(self.kv_act, 3), round(alpha, 9), self.mode,
               M, oa, sa)
        Fs, dkey = {}, None
        if spec is not None and spec['_damageable']:
            pts = dict({r: (cu + xy[0], cv + xy[1] / cos_t) for r, xy in discs.items()}, view=(cu + Xv, cv + Yv / cos_t))
            inc = self._fluence_cache
            if inc is not None and inc['geo'] == geo and inc['spec'] is spec and inc['epoch'] == spec['_dose_epoch']:
                Fs = inc['F']                                          # same sample points: add only the fluence deposited since
                if spec['_dose_new']:
                    new = dict(_dose=spec['_dose_new'])
                    for k_, (u_, v_) in pts.items():
                        a_, b_ = dose_at(new, u_, v_)
                        Fs[k_] = (Fs[k_][0] + a_, Fs[k_][1] + b_)
            else:
                Fs = {k_: dose_at(spec, u_, v_) for k_, (u_, v_) in pts.items()}
            spec['_dose_new'] = []
            self._fluence_cache = dict(geo=geo, spec=spec, epoch=spec['_dose_epoch'], F=Fs)
            dkey = tuple(damage_key(spec, f[1], VIEW_DAMAGE_Q) for f in Fs.values())
        key = geo + (dkey,)
        if self._frac_cache and self._frac_cache[0] == key:
            return self._frac_cache[1]
        rv = np.hypot(Xv, Yv)
        mask_v = (rv <= r_ill) & ((rv <= r_all) if (oa or sa) else True)
        area_v = (2.0 * half / fov) ** 2
        if spec is None:
            T = {r: 1.0 for r in discs}
            res = dict(open=1.0, structure=0.0, T_view=float(area_v * np.mean(mask_v)))
        else:
            v = max(self.kv_act, KV_RANGE_V[0])
            T, opens = {}, {}
            for r, (xd, yd) in discs.items():
                d = sample_specimen(spec, cu + xd, cv + yd / cos_t, 2.0 * r / n, v, alpha, t, fluence=Fs.get(r), damage_q=VIEW_DAMAGE_Q)
                T[r], opens[r] = float(np.mean(d['T'])), float(np.mean(d['open']))
            e = sample_specimen(spec, cu + Xv, cv + Yv / cos_t, 2.0 * half / n, v, alpha, t, fluence=Fs.get('view'), damage_q=VIEW_DAMAGE_Q)
            res = dict(open=opens[r_ill], T_view=float(area_v * np.mean(e['T'] * mask_v)),
                       structure=float(area_v * np.mean((e['label'] >= 2) * mask_v)))
        res['T'] = (r_oa / r_ill) ** 2 * T[r_oa]
        res['T_sa'] = (r_all / r_ill) ** 2 * T[r_all]
        self._frac_cache = (key, res)
        return res

    # ---------------------------------------------------------------- image formation
    def _render_field(self, spec, cu, cv, M, binning, exposure, ns, rng):
        """Camera image of the field of view centred at (cu, cv): counts, electrons per output pixel, labels and the sampler output."""
        t = self.stage['tilt']
        v = self.kv_act
        fov = CAMERA_PIXELS * CAMERA_PIXEL_M / M
        p_out = CAMERA_PIXEL_M * binning / M
        g = (np.arange(ns) + 0.5) / ns * fov - fov / 2.0
        X, Y = np.meshgrid(g, g)
        X, Y = X.ravel(), Y.ravel()
        r_ill = self._illum_d() / 2.0
        lit = (X * X + Y * Y <= r_ill * r_ill).astype(float)
        J = self._current_density()
        fs = [(cu, cv, st[2]) for st in (self._field_stop(), self._sa_stop()) if st is not None]
        if spec is None:
            T = np.ones(len(X))
            for st in fs:
                T = T * (X * X + Y * Y <= st[2] ** 2)
            out = dict(T=T, label=np.where(T > 0.5, 1, 0).astype(np.int64), amorph_t=np.zeros(len(X)), units=[], unit_idx=np.full(len(X), -1), open=T)
        else:
            out = sample_specimen(spec, cu + X, cv + Y / math.cos(t), fov / ns, v, self._alpha(), t, field_stop=fs, want_units=True)
        ne = J / E_CHARGE * p_out * p_out * exposure * out['T'] * lit
        counts = np.minimum(ne * ADU_PER_ELECTRON, ADU_MAX)
        label = np.where((lit < 0.5) | (out['label'] == 0), 0, out['label'])
        return dict(counts=counts, ne=ne, label=label, out=out, lit=lit, p_out=p_out, fov=fov, J=J, Y=Y)

    @staticmethod
    def _chords(maps):
        """Mean chord (4 x area / perimeter, in sampling steps) of every label over 2-D label maps: the size over which a segmentation can average."""
        area, perim, size = {}, {}, 1
        for L in maps:
            size = max(size, *L.shape)
            for lab in np.unique(L):
                m = L == lab
                area[int(lab)] = area.get(int(lab), 0) + int(m.sum())
                perim[int(lab)] = perim.get(int(lab), 0) + int((m[:, 1:] != m[:, :-1]).sum() + (m[1:, :] != m[:-1, :]).sum())
        return {lab: (4.0 * area[lab] / perim[lab] if perim[lab] else float(size)) for lab in area}

    def _regions(self, f, grey_of, binning, fringes=None, maps=None, px_per_sample=1.0):
        """Grey-level regions: the sampler classes (opaque, vacuum, support, phases) are merged by complete linkage while their mean counts differ by
        less than 3 x the noise of the class means, each averaged over its features (N = (mean chord in output pixels)^2, 9 to 1e6 pixels): classes
        that a segmentation of the noisy frame cannot separate form one region, with the merged area fraction, counts, SNR and fringe spacings."""
        chords = self._chords(maps) if maps else {}
        cls = []
        for lab in np.unique(f['label']):
            m = f['label'] == lab
            ne = float(np.mean(f['ne'][m]))
            npx = min(1e6, max(SEG_MIN_PX, (chords.get(int(lab), 1.0) * px_per_sample) ** 2))
            cls.append(dict(lab=int(lab), n=int(m.sum()), area=float(np.mean(m)), ne=ne, counts=float(np.mean(f['counts'][m])),
                            var=(ne / CAMERA_DQE + (CAMERA_READ_NOISE_E * binning) ** 2) * ADU_PER_ELECTRON ** 2 / npx))

        def far(a, b):
            return abs(a['counts'] - b['counts']) / max(3.0 * math.sqrt(a['var'] + b['var']), 1e-12)
        groups = [[c] for c in sorted(cls, key=lambda c: -c['counts'])]
        while len(groups) > 1:
            best = None
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    d = max(far(a, b) for a in groups[i] for b in groups[j])
                    if d < 1.0 and (best is None or d < best[0]):
                        best = (d, i, j)
            if best is None:
                break
            groups[best[1]] += groups.pop(best[2])
        regs = []
        for gp in groups:
            n = sum(c['n'] for c in gp)
            ne = sum(c['ne'] * c['n'] for c in gp) / n
            sig = math.sqrt(ne / CAMERA_DQE + (CAMERA_READ_NOISE_E * binning) ** 2)
            spacings = list(dict.fromkeys(d for c in sorted(gp, key=lambda c: -c['n']) for d in (fringes or {}).get(c['lab'], [])))[:8]
            regs.append(dict(area_fraction_in_view=sum(c['area'] for c in gp), mean_counts=sum(c['counts'] * c['n'] for c in gp) / n,
                             snr=ne / sig if sig > 0 else 0.0, lattice_spacings_m=spacings))
        regs.sort(key=lambda r: (-r['mean_counts'], -r['area_fraction_in_view']))
        regs = [r for r in regs if r['area_fraction_in_view'] >= 0.005][:8]
        for i, r in enumerate(regs):
            r['region_id'] = f'region-{i + 1}'
            r['mean_grey'] = grey_of(r['mean_counts'])
        return [dict(region_id=r['region_id'], area_fraction_in_view=r['area_fraction_in_view'], mean_grey=r['mean_grey'], mean_counts=r['mean_counts'],
                     snr=r['snr'], lattice_spacings_m=r['lattice_spacings_m']) for r in regs]

    @staticmethod
    def _contrast(values):
        lo, hi = float(np.percentile(values, 0.5)), float(np.percentile(values, 99.5))
        if hi - lo < 1e-9:
            return lambda c: 0.0 if hi <= 0.0 else 128.0
        return lambda c: float(min(255.0, max(0.0, 255.0 * (c - lo) / (hi - lo))))

    def _fringes(self, spec, f):
        """Lattice spacings visible per label (axial illumination): two-beam fringes of reflections that pass the objective aperture together with
        the direct beam, whose excitation x remaining crystallinity x temporal-coherence envelope (Cc, energy spread) x spatial-coherence envelope
        (illumination convergence and the local true defocus of the crystal) is at least 0.05, above the Nyquist limit of the output pixel and not
        below the catalogue lattice resolution (0.14 nm, reached only with tilted achromatic illumination, which is not modelled).  The
        objective-lens phase shift only moves two-beam fringes, it does not remove them."""
        out = {}
        units = f['out'].get('units') or []
        if not units or self.mode != 'MAG':
            return out
        v = self.kv_act
        lam = wavelength_m(v)
        alpha = self._alpha()
        beam = np.array([0.0, -math.sin(self.stage['tilt']), -math.cos(self.stage['tilt'])])
        d_lim = max(LATTICE_RESOLUTION_M, 2.0 * f['p_out'])
        conv = self._convergence()
        dz0, tan_t = self._defocus_true(), math.tan(self.stage['tilt'])
        counts = {}
        for u in units:
            pts = u['points']
            lit = f['lit'][pts] > 0.5
            if lit.sum() < 3 or u['fc'] < 0.3:
                continue
            sid = spec['phases'][u['phase']]['substance_id']
            G, _, D = _reflections(sid, IMAGING_D_MIN_M)
            xi = _xi(sid, IMAGING_D_MIN_M, v)
            _, _, I, _ = bragg(G, xi, u['R'][None], np.array([u['t']]), beam, v, alpha)
            gl = 1.0 / D
            dz = dz0 - float(np.mean(f['Y'][pts])) * tan_t                         # the tilted specimen rises along the tilt direction
            es = np.exp(-(math.pi * conv) ** 2 * (dz * gl - CS_M * lam * lam * gl ** 3) ** 2)
            et = np.exp(-0.5 * (math.pi * lam * focus_spread_m(v)) ** 2 * gl ** 4)
            ok = (I[0] * u['fc'] * es * et >= FRINGE_MIN_I) & (lam * gl < alpha) & (D >= d_lim)
            for d in np.unique(np.round(D[ok], 12)):
                lab = int(f['label'][pts[lit][0]])
                counts.setdefault(lab, {}).setdefault(float(d), 0)
                counts[lab][float(d)] += 1
        for lab, dd in counts.items():
            out[lab] = [d for d, _ in sorted(dd.items(), key=lambda kv: (-kv[1], -kv[0]))][:8]
        return out

    def _thon(self, f, dz_true, exposure):
        if self.mode != 'MAG':
            return []
        lit = f['lit'] > 0.5
        if not lit.any():
            return []
        am = float(np.mean(f['out']['amorph_t'][lit]))
        if am < THON_MIN_AMORPH_M or f['J'] * exposure / E_CHARGE < THON_MIN_DOSE_E_M2:
            return []
        v = self.kv_act
        lam = wavelength_m(v)
        conv = self._convergence()
        k_max = min(self._alpha() / lam, 1.0 / (2.0 * f['p_out']), 1.0 / LATTICE_RESOLUTION_M)
        return [float(k) for k in thon_zeros(dz_true, v, k_max, 60)
                if temporal_envelope(k, v) * spatial_envelope(k, dz_true, v, conv) >= THON_MIN_ENVELOPE][:8]

    def _image_complete(self, rec, op):
        spec = self._spec()
        cu, cv, _, _ = self._geometry()
        rng = np.random.default_rng(self.rng_acq.getrandbits(32))
        f = self._render_field(spec, cu, cv, rec['magnification'], op['binning'], op['exposure_s'], SAMPLES, rng)
        dz = self._defocus_true()
        # thumbnail: 2 x 2 blocks, shot and read noise of the camera pixels averaged in each block
        c = f['counts'].reshape(SAMPLES, SAMPLES)
        ne = f['ne'].reshape(SAMPLES, SAMPLES)
        blk = c.reshape(THUMB, SAMPLES // THUMB, THUMB, SAMPLES // THUMB).mean(axis=(1, 3))
        ne_b = ne.reshape(THUMB, SAMPLES // THUMB, THUMB, SAMPLES // THUMB).mean(axis=(1, 3))
        npx = (CAMERA_PIXELS / op['binning'] / THUMB) ** 2
        sig = np.sqrt(ne_b / CAMERA_DQE + (CAMERA_READ_NOISE_E * op['binning']) ** 2) * ADU_PER_ELECTRON / math.sqrt(npx)
        blk = np.clip(blk + rng.standard_normal(blk.shape) * sig, 0.0, ADU_MAX)
        grey = self._contrast(blk)
        fr = self._fringes(spec, f) if spec is not None else {}
        rec.update(regions=self._regions(f, grey, op['binning'], fr, [f['label'].reshape(SAMPLES, SAMPLES)], CAMERA_PIXELS / op['binning'] / SAMPLES),
                   thumbnail=[[int(round(grey(x))) for x in row] for row in blk],
                   thon_ring_frequencies_per_m=self._thon(f, dz, op['exposure_s']) if spec is not None else [],
                   saturated_fraction=float(np.mean(f['counts'] >= ADU_MAX)),
                   dose_estimate_e_per_m2=float(np.percentile(blk, 99.0)) / ADU_PER_ELECTRON / f['p_out'] ** 2,
                   template_source=self._source(spec))

    def _montage_complete(self, rec, op):
        spec = self._spec()
        rng = np.random.default_rng(self.rng_acq.getrandbits(32))
        rows, cols = op['rows'], op['cols']
        mosaic = np.zeros((rows * TILE_PX, cols * TILE_PX))
        allf = []
        h = (spec['_mount']['h'] if spec else 0.0) + self.stage['z']
        for s in op['schedule']:
            f = self._render_field(spec, s['x'], s['y'] + h * math.tan(self.stage['tilt']), rec['magnification'], op['binning'], op['exposure_s'], TILE_PX, rng)
            npx = (CAMERA_PIXELS / op['binning'] / TILE_PX) ** 2
            c = f['counts'].reshape(TILE_PX, TILE_PX)
            ne = f['ne'].reshape(TILE_PX, TILE_PX)
            sig = np.sqrt(ne / CAMERA_DQE + (CAMERA_READ_NOISE_E * op['binning']) ** 2) * ADU_PER_ELECTRON / math.sqrt(npx)
            mosaic[s['r'] * TILE_PX:(s['r'] + 1) * TILE_PX, s['c'] * TILE_PX:(s['c'] + 1) * TILE_PX] = np.clip(c + rng.standard_normal(c.shape) * sig, 0, ADU_MAX)
            allf.append(f)
        grey = self._contrast(mosaic)
        merged = dict(label=np.concatenate([f['label'] for f in allf]), ne=np.concatenate([f['ne'] for f in allf]),
                      counts=np.concatenate([f['counts'] for f in allf]))
        rec.update(mosaic=[[int(round(grey(x))) for x in row] for row in mosaic],
                   regions=self._regions(merged, grey, op['binning'], None, [x['label'].reshape(TILE_PX, TILE_PX) for x in allf], CAMERA_PIXELS / op['binning'] / TILE_PX),
                   template_source=self._source(spec))

    # ---------------------------------------------------------------- diffraction
    def _pattern_complete(self, rec, op):
        spec = self._spec()
        v = self.kv_act
        lam = wavelength_m(v)
        L = self._camera_length_true()
        L_disp = CAMERA_LENGTHS_M[self.cl_idx]
        t = self.stage['tilt']
        path = 1.0 / math.cos(t)
        cu, cv, _, _ = self._geometry()
        r_ill = self._illum_d() / 2.0
        r_area = min(r_ill, SA_APERTURES_M[self.sa_pos - 1] / SA_MAGNIFICATION / 2.0) if self.sa_pos else r_ill
        I_area = self._specimen_current() * (r_area / r_ill) ** 2
        alpha = self._alpha()
        px = CAMERA_PIXEL_M * op['binning']
        det = CAMERA_PIXELS * CAMERA_PIXEL_M / 2.0
        conv = self._convergence()
        rings, spots, halos = [], [], []

        def radius(d):
            s = lam / (2.0 * d)
            return L * math.tan(2.0 * math.asin(min(s, 1.0))) if s < 1.0 else math.inf

        def d_disp(R):
            return lam / (2.0 * math.sin(0.5 * math.atan(R / L_disp)))

        def visible(d):
            two_theta = 2.0 * math.asin(min(1.0, lam / (2.0 * d)))
            return two_theta < alpha and radius(d) < det

        ns = 40
        g = ((np.arange(ns) + 0.5) / ns) * 2.0 - 1.0
        X, Y = np.meshgrid(g, g)
        ins = (X * X + Y * Y <= 1.0).ravel()
        Xa, Ya = X.ravel()[ins] * r_area, Y.ravel()[ins] * r_area
        direct = 1.0
        if spec is not None:
            o = sample_specimen(spec, cu + Xa, cv + Ya / math.cos(t), 2.0 * r_area / ns, v, alpha, t)
            direct = float(np.mean(o['direct']))
            area = math.pi * r_area * r_area / math.cos(t)                  # specimen area inside the diffracting disc
            beam = np.array([0.0, -math.sin(t), -math.cos(t)])
            m_s = float(np.mean(damage_multipliers(spec['support_film'], o['fluence100'])[0])) if spec['support_film'] != 'none' else 1.0
            fsup = float(np.mean(o['support_cov']))
            if spec['support_film'] != 'none' and fsup > 0:
                amp = fsup * -math.expm1(-float(mu_damaged(spec['support_film'], v, 0.0, m_s)) * spec['support_thickness_m'] * path)
                for d in substance(spec['support_film'])['amorphous_halo_d_nm'] or []:
                    halos.append((d * 1e-9, amp / len(substance(spec['support_film'])['amorphous_halo_d_nm']), None))
            for k, p in enumerate(spec['phases']):
                sid = p['substance_id']
                sub = substance(sid)
                fk = float(np.mean(o['phase_cov'][k]))
                if fk <= 0.0:
                    continue
                m_k, fc_k = damage_multipliers(sid, o['fluence100'])
                mk, fck = float(np.mean(m_k)), float(np.mean(fc_k))
                if p['form'] == 'particles':
                    bp, l0 = spheroid_projection(0.5 * p['size_m'], p['thickness_m'] / p['size_m'], t)
                    tg = (2.0 / 3.0) * 2.0 * l0                                  # mean path through the projected spheroid
                else:
                    tg = p['thickness_m'] * path
                tk = tg * mk
                crystalline = sub.get('crystal') is not None and p['crystallinity'] != 'amorphous'
                el = -math.expm1(-float(mu_damaged(sid, v, 0.0, mk)) * tg)
                if not crystalline or fck < 0.999:
                    hd = sub['amorphous_halo_d_nm'] or ([0.3] if crystalline else [])
                    amp = fk * el * (1.0 if not crystalline else (1.0 - fck))
                    for d in hd:
                        halos.append((d * 1e-9, amp / len(hd), None))
                if not crystalline:
                    continue
                cr = sub['crystal']
                if p['form'] == 'particles':
                    n_units = fk * r_area * r_area / (0.5 * p['size_m'] * bp) * (POLY_GRAINS ** 2 if p['crystallinity'] == 'polycrystalline' else 1)
                    usize = p['size_m'] / (POLY_GRAINS if p['crystallinity'] == 'polycrystalline' else 1)
                elif p['crystallinity'] == 'single_crystal':
                    n_units, usize = 1.0, math.sqrt(area)
                else:
                    n_units, usize = fk * area / p['size_m'] ** 2, p['size_m']
                width = math.sqrt(CAMERA_PSF_PX ** 2 + (L * lam / usize / px) ** 2 + (2.0 * L * conv / px) ** 2)
                if n_units > N_RING:
                    G, F, D = _reflections(sid, 0.0)
                    xi = _xi(sid, 0.0, v)
                    Ik = (math.pi / xi) ** 2 * tk / (2.0 * (1.0 / D)) * fck
                    tot = float(np.sum(Ik))
                    sc = -math.expm1(-tot) / tot if tot > 1e-12 else 1.0
                    fam = {}
                    for dd, ii in zip(D, Ik):
                        fam[round(float(dd), 13)] = fam.get(round(float(dd), 13), 0.0) + float(ii) * sc * fk
                    for dd, ii in fam.items():
                        if visible(dd):
                            rings.append((dd, ii, width))
                else:
                    ukeys, weights, tt = self._diffracting_units(spec, k, p, cu, cv, r_area, t)
                    if len(ukeys) == 0:
                        continue
                    R = _unit_rotations(spec, k, p, cr, ukeys, _phase_seed(spec, k))
                    G, F, D = _reflections(sid, 0.0)
                    xi = _xi(sid, 0.0, v)
                    tu = tt * mk
                    _, _, I, gs = bragg(G, xi, R, tu, beam, v, math.inf)
                    tot = I.sum(axis=1)
                    sc = np.where(tot > 1e-12, -np.expm1(-tot) / np.maximum(tot, 1e-12), 1.0)
                    rot = _rot_x(t)
                    for n_ in range(len(ukeys)):
                        gl = gs[n_] @ rot.T
                        amp = I[n_] * sc[n_] * weights[n_] * fck
                        keep = amp > 1e-6
                        for gv, a, dd in zip(gl[keep], amp[keep], D[keep]):
                            if not visible(dd):
                                continue
                            rr = radius(dd)
                            nxy = math.hypot(gv[0], gv[1])
                            if nxy <= 0:
                                continue
                            spots.append((rr * gv[0] / nxy, rr * gv[1] / nxy, float(a)))
        # central beam and normalisation
        n_direct = I_area * direct * op['exposure_s'] / E_CHARGE
        r_c = max(2.0, L * conv / px)                                    # central disc radius (camera pixels)
        central_sat = bool(n_direct * ADU_PER_ELECTRON / (math.pi * r_c * r_c) >= ADU_MAX)
        merged = {}
        for x, y, a in spots:
            key = (round(x / (0.5 * px)), round(y / (0.5 * px)))
            if key in merged:
                merged[key] = (merged[key][0], merged[key][1], merged[key][2] + a)
            else:
                merged[key] = (x, y, a)
        spots = sorted(merged.values(), key=lambda s: -s[2])[:80]
        top = max([a for _, a, _ in rings] + [a for _, _, a in spots] + [a for _, a, _ in halos] + [1e-30])
        rec['rings'] = [dict(radius_m=radius(d), radius_px=radius(d) / px, d_spacing_display_m=d_disp(radius(d)), relative_intensity=min(1.0, a / top),
                             width_px=w) for d, a, w in sorted(rings, key=lambda r: -r[0]) if a / top >= 1e-3][:40]
        rec['spots'] = [dict(x_m=x, y_m=y, relative_intensity=min(1.0, a / top)) for x, y, a in spots if a / top >= 1e-3]
        hl = {}
        for d, a, _ in halos:
            hl[round(d, 12)] = hl.get(round(d, 12), 0.0) + a
        rec['halos'] = [dict(radius_m=radius(d), radius_px=radius(d) / px, d_spacing_display_m=d_disp(radius(d)), relative_intensity=min(1.0, a / top),
                             width_px=0.25 * radius(d) / px) for d, a in sorted(hl.items(), key=lambda r: -r[0]) if visible(d) and a / top >= 1e-3][:6]
        rec['central_beam_saturated'] = central_sat
        rec['template_source'] = self._source(spec)

    def _diffracting_units(self, spec, k, p, cu, cv, r_area, t):
        """Crystal units (keys as in the image sampler) whose centres lie in the diffracting disc, their weights (fraction of the disc area) and
        their mean projected thickness (m)."""
        sk = _phase_seed(spec, k)
        cos_t = math.cos(t)
        area = math.pi * r_area * r_area / cos_t
        ry = r_area / cos_t
        if p['form'] == 'particles':
            pitch = particle_pitch(p)
            i0, i1 = int(math.floor((cu - r_area) / pitch)), int(math.floor((cu + r_area) / pitch))
            j0, j1 = int(math.floor((cv - ry) / pitch)), int(math.floor((cv + ry) / pitch))
            if (i1 - i0 + 1) * (j1 - j0 + 1) > 4 * N_RING + 16:
                return np.zeros((0, 4), dtype=np.int64), np.zeros(0), np.zeros(0)
            I, J = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
            U0, V0 = (I.ravel() + 0.5) * pitch, (J.ravel() + 0.5) * pitch
            i, j, pu, pv, rp = _particle_cells(U0, V0, p, sk)
            inside = ((pu - cu) / r_area) ** 2 + ((pv - cv) * cos_t / r_area) ** 2 <= 1.0
            if not inside.any():
                return np.zeros((0, 4), dtype=np.int64), np.zeros(0), np.zeros(0)
            o = sample_specimen(spec, pu[inside], pv[inside], 1e-9, max(self.kv_act, KV_RANGE_V[0]), OL_OUT_ANGLE_RAD, t)
            present = o['phase_cov'][k] >= 0.5
            pi_, pj_, rr = i[inside][present], j[inside][present], rp[inside][present]
            bp, l0 = spheroid_projection(1.0, p['thickness_m'] / p['size_m'], t)
            share = bp * rr * rr / (r_area * r_area)                           # projected outline pi rr (bp rr) / view area of the disc pi r_area^2
            if p['crystallinity'] == 'polycrystalline':
                keys, w, tt = [], [], []
                for a_, b_, r_, s_ in zip(pi_, pj_, rr, share):
                    for gi in range(-POLY_GRAINS // 2, POLY_GRAINS // 2):
                        for gj in range(-POLY_GRAINS // 2, POLY_GRAINS // 2):
                            keys.append((a_, b_, gi, gj))
                            w.append(s_ / POLY_GRAINS ** 2)
                            tt.append((2.0 / 3.0) * 2.0 * l0 * r_)
                return np.array(keys, dtype=np.int64).reshape(-1, 4), np.array(w), np.array(tt)
            keys = np.stack([pi_, pj_, np.zeros_like(pi_), np.zeros_like(pi_)], 1).astype(np.int64)
            return keys, share, (2.0 / 3.0) * 2.0 * l0 * rr
        if p['crystallinity'] == 'single_crystal':
            key = np.array([[0, 0, int(math.floor(cu / BEND_CELL_M)), int(math.floor(cv / BEND_CELL_M))]], dtype=np.int64)
            o = sample_specimen(spec, np.array([cu]), np.array([cv]), 1e-9, max(self.kv_act, KV_RANGE_V[0]), OL_OUT_ANGLE_RAD, t)
            fk = float(o['phase_cov'][k][0])
            return (key, np.array([1.0]), np.array([p['thickness_m'] / cos_t])) if fk >= 0.5 else (np.zeros((0, 4), dtype=np.int64), np.zeros(0), np.zeros(0))
        grain = p['size_m']
        i0, i1 = int(math.floor((cu - r_area) / grain)), int(math.floor((cu + r_area) / grain))
        j0, j1 = int(math.floor((cv - ry) / grain)), int(math.floor((cv + ry) / grain))
        if (i1 - i0 + 1) * (j1 - j0 + 1) > 4 * N_RING + 16:
            return np.zeros((0, 4), dtype=np.int64), np.zeros(0), np.zeros(0)
        I, J = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        I, J = I.ravel(), J.ravel()
        gu, gv = (I + _u01(I, J, sk, 7)) * grain, (J + _u01(I, J, sk, 8)) * grain
        inside = ((gu - cu) / r_area) ** 2 + ((gv - cv) * cos_t / r_area) ** 2 <= 1.0
        if not inside.any():
            return np.zeros((0, 4), dtype=np.int64), np.zeros(0), np.zeros(0)
        o = sample_specimen(spec, gu[inside], gv[inside], 1e-9, max(self.kv_act, KV_RANGE_V[0]), OL_OUT_ANGLE_RAD, t)
        present = o['phase_cov'][k] >= 0.5
        keys = np.stack([I[inside][present], J[inside][present], np.zeros(int(present.sum())), np.zeros(int(present.sum()))], 1).astype(np.int64)
        return keys, np.full(len(keys), grain * grain / area), np.full(len(keys), p['thickness_m'] / cos_t)

    # ---------------------------------------------------------------- acquisitions: stop / cancel / completion
    def _stop_acq(self, status, code):
        op = self.acq
        if op is None:
            return
        rec = self._table(op['kind'])[op['record_id']]
        rec.update(status=status, finished_s=self.time_s, error_code=code)
        op['phase'], op['reason'] = status, code
        self.acq = None
        if op['kind'] == 'montage':
            self.stage_target['x'], self.stage_target['y'] = op['home']
        self._event('acquisition', 'ACQ_' + status.upper(), f'采集{op["record_id"]}{"被取消" if status == "cancelled" else "失败"}（{code}）', op['record_id'])

    def _cancel_op(self, op):
        self._stop_acq('cancelled', 'USER_CANCEL')
        self._frac_cache = None

    def _check_acq(self):
        op = self.acq
        if op is None:
            return
        if not self._beam_ready():
            self._stop_acq('failed', 'BEAM_OFF')
        elif self.time_s >= op['t0'] + op['duration'] - 1e-9:
            rec = self._table(op['kind'])[op['record_id']]
            {'image': self._image_complete, 'diffraction': self._pattern_complete, 'montage': self._montage_complete}[op['kind']](rec, op)
            rec.update(status='completed', finished_s=self.time_s)
            op['phase'] = 'completed'
            self.acq = None
            if op['kind'] == 'montage':
                self.stage_target['x'], self.stage_target['y'] = op['home']
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
            self._integrate(self.time_ns, nxt)
            self.time_ns = nxt
            if nxt % CYCLE_NS == 0:
                self._tick()
        self.revision += 1

    def _montage_switches(self, t0, t1):
        op = self.acq
        if op is None or op['kind'] != 'montage':
            return []
        return sorted({op['t0'] + s['t_move'] for s in op['schedule'] if t0 < op['t0'] + s['t_move'] < t1})

    def _montage_target(self, t):
        op = self.acq
        cur = None
        for s in op['schedule']:
            if op['t0'] + s['t_move'] <= t + 1e-12:
                cur = s
        return (cur['x'], cur['y']) if cur else None

    def _integrate(self, ns0, ns1):
        """Continuous motions between the 1 s cycles (stage, holder, screen, HT ramp): exact for any chopping of the host step."""
        t0, t1 = ns0 / 1e9, ns1 / 1e9
        cuts = [t0] + self._montage_switches(t0, t1) + [t1]
        for a, b in zip(cuts[:-1], cuts[1:]):
            if self.acq is not None and self.acq['kind'] == 'montage':
                tgt = self._montage_target(a)
                if tgt:
                    self.stage_target['x'], self.stage_target['y'] = tgt
            self._move(a, b - a)

    def _move(self, t0, dt):
        """Continuous motions over [t0, t0 + dt], split at every stage-axis and holder arrival so that the corners of the beam path and the
        holder arrival time are exact for any chopping of the host step."""
        cuts = {dt}
        if self.power:
            for k, sp in (('x', STAGE_XY_SPEED), ('y', STAGE_XY_SPEED), ('z', STAGE_Z_SPEED), ('tilt', STAGE_TILT_SPEED)):
                d = abs(self.stage_target[k] - self.stage[k])
                if 0.0 < d < sp * dt:
                    cuts.add(d / sp)
        dh = abs(self.withdrawal_target - self.withdrawal)
        if 0.0 < dh < HOLDER_SPEED_M_S * dt:
            cuts.add(dh / HOLDER_SPEED_M_S)
        done = 0.0
        for c in sorted(cuts):
            if c > done:
                self._move_piece(t0 + done, c - done)
                done = c
        if dt <= 0.0 and not self._knots:
            self._knot(t0)

    def _knot(self, t, corner=False):
        """Stage axes (x, y, z, tilt) at time t: piecewise linear in time between the corners, so they can be re-sampled exactly."""
        st = (self.stage['x'], self.stage['y'], self.stage['z'], self.stage['tilt'])
        tg = (self.stage_target['x'], self.stage_target['y'], self.stage_target['z'], self.stage_target['tilt'])
        if self._knots and self._knots[-1][2] != tg:
            self._knots[-1][1] = True                               # a command changed the targets here: a corner of the path
        if self._knots and t <= self._knots[-1][0]:
            self._knots[-1] = [t, corner or self._knots[-1][1], tg, st]
        else:
            self._knots.append([t, corner, tg, st])

    def _move_piece(self, t0, dt):
        def toward(cur, tgt, speed):
            d = speed * dt
            if abs(tgt - cur) <= d * (1.0 + 1e-12):
                return tgt
            return cur + d if cur < tgt else cur - d
        if not self._knots:
            self._knot(t0)
        before = dict(self.stage)
        if self.power:
            self.stage['x'] = toward(self.stage['x'], self.stage_target['x'], STAGE_XY_SPEED)
            self.stage['y'] = toward(self.stage['y'], self.stage_target['y'], STAGE_XY_SPEED)
            self.stage['z'] = toward(self.stage['z'], self.stage_target['z'], STAGE_Z_SPEED)
            self.stage['tilt'] = toward(self.stage['tilt'], self.stage_target['tilt'], STAGE_TILT_SPEED)
            self.screen_angle = toward(self.screen_angle, SCREEN_UP_ANGLE_RAD if self.screen_up else 0.0, SCREEN_UP_ANGLE_RAD / SCREEN_TIME_S)
            if self.ht_on:
                self.kv_act = toward(self.kv_act, self.kv_set, HT_RAMP_V_S)
        if abs(self.withdrawal - self.withdrawal_target) > 0.0:
            self.withdrawal = toward(self.withdrawal, self.withdrawal_target, HOLDER_SPEED_M_S)
            if self.withdrawal == self.withdrawal_target:
                self._holder_arrived(t0 + dt)
        arrived = any(before[k] != self.stage[k] and self.stage[k] == self.stage_target[k] for k in self.stage)
        self._knot(t0 + dt, arrived)

    def _holder_arrived(self, t_arr):
        rt = GAS_CONSTANT * self._room_k()
        pos = self.holder_pos
        if pos == 'entering':
            self.holder_pos = 'airlock'
            self.p_al = P_ATM                                  # the O-ring seals room air into the airlock
            self.led['gas_admitted_mol'].add(P_ATM * V_AIRLOCK_M3 / rt)
            self._event('holder', 'HOLDER_AT_AIRLOCK', '样品杆到达气锁止点，气锁封闭', t=t_arr)
        elif pos == 'turning_in':
            self.holder_pos = 'column'
            self.holder_t0 = t_arr
            self._frac_cache = None
            self._event('holder', 'HOLDER_IN_COLUMN', '样品杆完全插入，载网在物镜极靴间隙中', t=t_arr)
        elif pos == 'withdrawing':
            self.holder_pos = 'airlock'
            self.p_al = self.p_col                             # the airlock re-forms with column gas
            self.p_col -= self.p_col * V_AIRLOCK_M3 / V_COLUMN_M3
            self._frac_cache = None
            self._event('holder', 'HOLDER_AT_AIRLOCK', '样品杆回到气锁止点', t=t_arr)
        elif pos == 'removing':
            self.holder_pos = 'bench'
            self._event('holder', 'HOLDER_REMOVED', f'{self.holder_in}样品杆已取出放回实验台', t=t_arr)
            self.holder_in = None

    def _holder_outgas(self):
        if self.holder_t0 is None or self.holder_pos != 'column':
            return 0.0
        return Q_HOLDER_PA_M3_S * math.exp(-(self.time_s - self.holder_t0) / HOLDER_OUTGAS_TAU_S)

    def _vacuum_step(self, dt):
        rt = GAS_CONSTANT * self._room_k()
        S = S_TMP_M3_S * self.tmp_speed if self.power else 0.0
        q_col = Q_COLUMN_PA_M3_S + (0.0 if self.power else LEAK_IDLE_PA_M3_S) + self._holder_outgas()
        q_gun = Q_GUN_PA_M3_S * (GUN_HOT_FACTOR if self._emission() > 0.0 else 1.0)
        A = np.array([[-(S + C_GUN_M3_S) / V_COLUMN_M3, C_GUN_M3_S / V_COLUMN_M3], [C_GUN_M3_S / V_GUN_M3, -C_GUN_M3_S / V_GUN_M3]])
        b = np.array([q_col / V_COLUMN_M3, q_gun / V_GUN_M3])
        x1, ix = _lin2(A, b, np.array([self.p_col, self.p_gun]), dt)
        self.p_col, self.p_gun = float(max(x1[0], 0.0)), float(max(x1[1], 0.0))
        self.led['gas_admitted_mol'].add((q_col + q_gun) * dt / rt)
        self.led['gas_pumped_mol'].add(S * float(ix[0]) / rt)
        if self._airlock_sealed() and self.power:
            p0 = self.p_al
            if self.lever_pump:
                p1 = AIRLOCK_ULT_PA + (p0 - AIRLOCK_ULT_PA) * math.exp(-dt / AIRLOCK_TAU_S)
            else:
                p1 = P_ATM + (p0 - P_ATM) * math.exp(-dt / AIRLOCK_VENT_TAU_S)
            dn = (p1 - p0) * V_AIRLOCK_M3 / rt
            if dn < 0.0:
                self.led['gas_pumped_mol'].add(-dn)
            else:
                self.led['gas_admitted_mol'].add(dn)
            self.p_al = p1

    def _physics(self, dt):
        """Vacuum, pumps, filament, dose and charge over one instrument cycle with the coefficients at the cycle start (the result does not
        depend on how the host chops its steps)."""
        self._vacuum_step(dt)
        if self.power:
            self.tmp_speed = min(1.0, self.tmp_speed + dt / TMP_SPINUP_S)
        else:
            self.tmp_speed = max(0.0, self.tmp_speed - dt / TMP_SPINDOWN_S)
        em = self._emission()
        if em > 0.0:
            self._beam_step(dt, em)
        if self.fil_on and self.ht_on and self.power and not self.fil_burned and self.kv_act > 0.0:
            h0 = self.heat
            h1 = min(1.0, h0 + dt / HEAT_S)
            tau = min(dt, (1.0 - h0) * HEAT_S)
            wear_h2 = (h1 ** 3 - h0 ** 3) * HEAT_S / 3.0 + (dt - tau) * 1.0          # exact integral of h^2 over the linear heating ramp
            i_sat = EMISSION_BIAS0_A * math.exp(-self.bias / BIAS_SCALE)
            self.fil_used_s += (i_sat / FIL_WEAR_REF_A) ** 2 * wear_h2
            self.led['filament_on_time_s'].add(dt)
            self.heat = h1
        else:
            self.heat = max(0.0, self.heat - dt / 5.0)

    def _beam_step(self, dt, em):
        i_s = self._specimen_current()
        self.led['beam_charge_c'].add(em * dt)
        self.led['gun_condenser_stopped_charge_c'].add((em - i_s) * dt)
        self.led['specimen_plane_charge_c'].add(i_s * dt)
        f = self._view_fractions()
        self.led['grid_stopped_charge_c'].add(i_s * (1.0 - f['open']) * dt)
        self.led['objective_aperture_stopped_charge_c'].add(i_s * (f['open'] - f['T']) * dt)
        self.led['selected_area_aperture_stopped_charge_c'].add(i_s * (f['T'] - f['T_sa']) * dt)
        self.led['detector_charge_c'].add(i_s * f['T_sa'] * dt)
        spec = self._spec()
        if spec is not None:
            self._deposit(spec, self._current_density(), dt)

    def _deposit(self, spec, J, dt):
        """Electron fluence of the cycle along the beam path.  The stage axes x, y, z and tilt are linear in time between the corners (an axis
        arrives, a command changes the targets), which are knots for any host chopping; they are re-sampled at DEPOSIT_SUBSTEPS fixed times plus
        the corners and mapped to the view centre (x, y + h tan t) - so the result does not depend on how the host chopped the cycle.  Pieces with
        any displacement are moving; consecutive moving pieces form straight constant-speed runs (path within 5 % of the beam radius of the chord,
        speed and cos(tilt) within 1 %, same direction, ending at every corner); stationary time becomes a disc.  Each run is a swept-disc dose
        event (merged with the previous event when it continues it in the same direction at the same fluence per length)."""
        r = self._illum_d() / 2.0
        kn = [k for k in self._knots]
        if len(kn) < 2 or kn[-1][0] <= kn[0][0]:
            st = (self.stage['x'], self.stage['y'], self.stage['z'], self.stage['tilt'])
            kn = [[self.time_s - dt, False, None, st], [self.time_s, False, None, st]]
        t0, t1 = kn[0][0], kn[-1][0]
        corners = {k[0] for k in kn[1:-1] if k[1]}
        times = sorted(set([t0 + (t1 - t0) * i / DEPOSIT_SUBSTEPS for i in range(DEPOSIT_SUBSTEPS + 1)]) | corners)
        tk = np.array([k[0] for k in kn])
        ax = [np.interp(times, tk, [k[3][i] for k in kn]) for i in range(4)]   # the axes are exact at any time (linear between corners)
        h0 = spec['_mount']['h']
        X = ax[0]
        Y = ax[1] + (h0 + ax[2]) * np.tan(ax[3])                            # view centre on the specimen (as _geometry)
        C = np.cos(ax[3])
        scale = dt / (t1 - t0)                                       # the knots cover the cycle; guard against a partial first cycle
        k100 = beta2_of(DOSE_REF_V) / beta2_of(max(self.kv_act, 1.0))
        tol = 0.05 * r
        pts = list(zip(times, X, Y, C))
        pieces = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1) if pts[i + 1][0] > pts[i][0]]

        def moving(p):
            a_, b_ = p
            return math.hypot(b_[1] - a_[1], b_[2] - a_[2]) > 1e-15

        def speed(p):
            a_, b_ = p
            return math.hypot(b_[1] - a_[1], (b_[2] - a_[2]) * 0.5 * (a_[3] + b_[3])) / (b_[0] - a_[0])
        i = 0
        while i < len(pieces):
            if not moving(pieces[i]):
                j = i
                while j + 1 < len(pieces) and not moving(pieces[j + 1]):
                    j += 1
                a_, b_ = pieces[i][0], pieces[j][1]
                tau = b_[0] - a_[0]
                ct = sum((q[1][0] - q[0][0]) * 0.5 * (q[0][3] + q[1][3]) for q in pieces[i:j + 1]) / tau
                self._add_event(spec, [a_[1], a_[2], a_[1], a_[2], r, ct, J * tau * scale, J * tau * scale * k100])
                i = j + 1
                continue
            j = i
            v0 = speed(pieces[i])
            c0 = 0.5 * (pieces[i][0][3] + pieces[i][1][3])
            while j + 1 < len(pieces) and moving(pieces[j + 1]):
                if pieces[j + 1][0][0] in corners:                       # an axis arrived or a command changed the targets: the run ends
                    break
                a_, c1 = pieces[i][0], pieces[j + 1][1]
                ex, ey = c1[1] - a_[1], (c1[2] - a_[2]) * c0
                L = math.hypot(ex, ey)
                q0, q1 = pieces[j + 1]
                if L <= 0.0 or (q1[1] - q0[1]) * ex + (q1[2] - q0[2]) * c0 * ey <= 0.0:   # never fold a reversal onto the chord
                    break
                off = max(abs((q[1][1] - a_[1]) * ey - (q[1][2] - a_[2]) * c0 * ex) / L for q in pieces[i:j + 1])
                if off > tol or abs(speed(pieces[j + 1]) - v0) > 0.01 * v0 or abs(0.5 * (pieces[j + 1][0][3] + pieces[j + 1][1][3]) - c0) > 0.01 * c0:
                    break
                j += 1
            a_, b_ = pieces[i][0], pieces[j][1]
            tau = b_[0] - a_[0]
            ct = sum((q[1][0] - q[0][0]) * 0.5 * (q[0][3] + q[1][3]) for q in pieces[i:j + 1]) / tau
            self._add_event(spec, [a_[1], a_[2], b_[1], b_[2], r, ct, J * tau * scale, J * tau * scale * k100])
            i = j + 1

    @staticmethod
    def _add_event(spec, e):
        if spec['_damageable']:
            spec['_dose_new'].append(list(map(float, e)))               # the increment, for the incremental fluence at the sample points
        ev = spec['_dose']
        if ev:
            p = ev[-1]
            same = abs(p[4] - e[4]) <= 1e-12 * e[4] and abs(p[5] - e[5]) <= 1e-12
            if same and p[0] == p[2] and p[1] == p[3] and e[0] == e[2] and e[1] == e[3] and abs(p[0] - e[0]) <= 1e-3 * e[4] \
                    and abs(p[1] - e[1]) * e[5] <= 1e-3 * e[4]:
                p[6] += e[6]
                p[7] += e[7]
                return
            if same and not (p[0] == p[2] and p[1] == p[3]) and not (e[0] == e[2] and e[1] == e[3]):
                dpx, dpy = p[2] - p[0], (p[3] - p[1]) * e[5]
                dex, dey = e[2] - e[0], (e[3] - e[1]) * e[5]
                lp, le = math.hypot(dpx, dpy), math.hypot(dex, dey)
                if math.hypot(e[0] - p[2], (e[1] - p[3]) * e[5]) <= 1e-3 * e[4] and abs(dpx * dey - dpy * dex) <= 1e-6 * lp * le \
                        and dpx * dex + dpy * dey > 0 and abs(p[6] / lp - e[6] / le) <= 1e-6 * p[6] / lp:
                    p[2], p[3] = e[2], e[3]
                    p[6] += e[6]
                    p[7] += e[7]
                    return
        if len(ev) >= MAX_DOSE_EVENTS:
            ev.pop(0)
            spec['_dose_epoch'] += 1                                    # history dropped: sample-point fluences must be recomputed
        ev.append(list(map(float, e)))

    def _tick(self):
        self._physics(CYCLE_S)
        self._knots = [[k[0], False, k[2], k[3]] for k in self._knots[-1:]]
        if not self.power:
            self.readings = None
            return
        t = self.time_s
        if abs(self.plant['mains_voltage_v'] - MAINS_NOMINAL_V) > MAINS_TOL * MAINS_NOMINAL_V + 1e-9:
            self._power_off()
            self.error = 'MAINS_FAULT'
            self._event('error', 'MAINS_FAULT', '供电电压超出AC 200V±10%：主机断路')
            return
        if self.state == 'booting' and t - self.state_t0 >= SOFTWARE_BOOT_S - 1e-9:
            self.state = 'ready'
            self._event('power', 'SOFTWARE_READY', 'TEM Center已启动')
        ready_now = self.tmp_speed >= 1.0 - 1e-9 and self.p_col < COL_READY_PA and self.p_gun < GUN_READY_PA
        if self.vac_state == 'ready':
            if self.p_col > 2.0 * COL_READY_PA or self.p_gun > 2.0 * GUN_READY_PA or self.tmp_speed < 1.0 - 1e-9:
                self.vac_state = 'pumping'
        elif ready_now:
            self.vac_state = 'ready'
            self._event('vacuum', 'VACUUM_READY', '镜筒与电子枪真空就绪')
        if self.ht_on and self.plant['cooling_water_m3_s'] < COOLING_FLOW_MIN_M3_S - 1e-15:
            self.ht_on, self.kv_act, self.fil_on = False, 0.0, False
            self.error = 'COOLING_WATER_LOW'
            self._event('error', 'COOLING_WATER_LOW', '冷却水流量不足：高压与灯丝关闭')
        if self.ht_on and self.p_gun > GUN_TRIP_PA:
            self.ht_on, self.kv_act, self.fil_on = False, 0.0, False
            self.error = 'HT_TRIP'
            self._event('error', 'HT_TRIP', '电子枪真空变差，高压跳闸')
        if self.fil_on and self.fil_used_s >= self.fil_life_s:
            self.fil_burned, self.fil_on = True, False
            self.error = 'FILAMENT_BURNOUT'
            self._event('error', 'FILAMENT_BURNOUT', '钨灯丝烧断：高压关闭后更换灯丝')
        z = [self.rng.gauss(0.0, 1.0) for _ in range(6)]            # fixed number of draws per cycle: readings never shift each other's noise
        dark = max(0.0, self.kv_act / DARK_CURRENT_R_OHM + METER_DARK_NOISE_A * z[3]) if self.ht_on else None
        screen = None
        if self._beam_on() and not self._screen_raised() and self.screen_angle <= 1e-12 and self.mode != 'DIFF':
            M = self._mag()
            screen = max(0.0, self._current_density() * self._view_fractions()['T_view'] / (M * M) * (1.0 + 0.03 * z[5]))
        self.readings = dict(col=max(1e-7, self.p_col * (1.0 + 0.10 * z[0])), gun=max(1e-7, self.p_gun * (1.0 + 0.10 * z[1])),
                             al=max(0.1, self.p_al * (1.0 + 0.10 * z[2])) if self._airlock_sealed() else None,
                             kv=self.kv_act if self.ht_on else None,
                             dark=dark, beam=max(0.0, dark + self._emission() + METER_BEAM_NOISE_A * z[4]) if self.ht_on else None,
                             screen=screen)
        self._check_acq()


# ---------------------------------------------------------------- host session: identity filter, request binding, ownership
ASYNC = {'image.acquire', 'diffraction.acquire', 'montage.acquire'}
_DATA_GUARDED = {'image.result', 'diffraction.result', 'montage.result'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED}
_RECORD_ARG = {'image.result': 'image_id', 'diffraction.result': 'pattern_id', 'montage.result': 'montage_id'}


class JeolJem1400flashSession:
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
        meta = copy.deepcopy(export_jeol_jem1400flash_contract_metadata())
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
        values['result.last_pattern_id'] = self._own_last(h.patterns)
        values['result.last_montage_id'] = self._own_last(h.montages)
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
            rid = arguments.get(_RECORD_ARG[capability_id])
            if rid in h._table({'image.result': 'image', 'diffraction.result': 'diffraction', 'montage.result': 'montage'}[capability_id]) and self._foreign(rid):
                raise ActionRejected('ACCESS_DENIED')              # unknown ids are reported by the runtime (UNKNOWN_RECORD)

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
        return dict(operation_id=operation_id, status=op['phase'], result=h._out(h._table(op['kind'])[op['record_id']]))

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
# immediate effects of an accepted call (union over the scenario states of tests/test_asset_jeol_jem1400flash.py, noise-free and noisy runs)
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.airlock_pressure_pa', 'truth.camera_length_m', 'truth.column_pressure_pa', 'truth.filament_life_s',
        'truth.gun_pressure_pa'],
    'specimen.provide': ['specimen.bench_ids', 'truth.specimens'],
    'holder.load_grid': ['holder.grids', 'specimen.bench_ids', 'truth.specimens'],
    'holder.unload_grid': ['holder.grids', 'specimen.bench_ids', 'truth.specimens'],
    'holder.insert_to_airlock': ['holder.in_goniometer', 'holder.position'],
    'airlock.set_pump': ['airlock.lever_rad', 'airlock.pump', 'airlock.state', 'panel.airlock_lamp'],
    'holder.insert': ['airlock.state', 'holder.position', 'panel.airlock_lamp', 'truth.airlock_pressure_pa', 'truth.column_pressure_pa'],
    'holder.withdraw': ['holder.position', 'truth.defocus_m', 'truth.specimen_height_m', 'truth.view_center_dose_c_m2'],
    'holder.remove': ['airlock.state', 'holder.position'],
    'power.set': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'airlock.pressure_indicated_pa', 'airlock.state', 'display.mag_or_camera_length_text',
        'filament.on', 'filament.state', 'gun.beam_current_indicated_a', 'gun.dark_current_indicated_a', 'ht.on', 'ht.voltage_indicated_v',
        'illumination.screen_current_density_a_m2', 'panel.airlock_lamp', 'panel.beam_lamp', 'panel.power_lamp', 'power.enabled', 'screen.moving', 'stage.moving',
        'stage.tilt_indicated_rad', 'stage.x_indicated_m', 'stage.y_indicated_m', 'stage.z_indicated_m', 'system.state', 'truth.current_density_a_m2',
        'truth.emission_a', 'truth.specimen_current_a', 'vacuum.column_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa', 'vacuum.state'],
    'power.key_toggle': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'airlock.pressure_indicated_pa', 'airlock.state',
        'display.mag_or_camera_length_text', 'filament.on', 'filament.state', 'gun.beam_current_indicated_a', 'gun.dark_current_indicated_a', 'ht.on',
        'ht.voltage_indicated_v', 'illumination.screen_current_density_a_m2', 'panel.airlock_lamp', 'panel.beam_lamp', 'panel.power_lamp', 'power.enabled',
        'screen.moving', 'stage.moving', 'stage.tilt_indicated_rad', 'stage.x_indicated_m', 'stage.y_indicated_m', 'stage.z_indicated_m', 'system.state',
        'truth.current_density_a_m2', 'truth.emission_a', 'truth.specimen_current_a', 'vacuum.column_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa',
        'vacuum.state'],
    'error.acknowledge': ['system.error_code'],
    'ht.set_voltage': ['ht.voltage_setpoint_v'],
    'ht.set_on': ['filament.on', 'filament.state', 'ht.on', 'panel.beam_lamp', 'truth.current_density_a_m2', 'truth.emission_a', 'truth.specimen_current_a'],
    'filament.set_on': ['filament.on', 'filament.state', 'panel.beam_lamp', 'truth.current_density_a_m2', 'truth.emission_a', 'truth.specimen_current_a'],
    'filament.set_bias': ['filament.bias', 'truth.current_density_a_m2', 'truth.emission_a', 'truth.specimen_current_a'],
    'gun.replace_filament': ['filament.state', 'system.error_code', 'truth.filament_life_s', 'truth.filament_on_time_s'],
    'optics.set_mode': ['display.mag_or_camera_length_text', 'optics.magnification', 'optics.magnification_step_fraction', 'optics.mode', 'truth.current_density_a_m2',
        'truth.illuminated_diameter_m'],
    'optics.toggle_diffraction': ['display.mag_or_camera_length_text', 'optics.magnification_step_fraction', 'optics.mode'],
    'optics.set_magnification': ['display.mag_or_camera_length_text', 'optics.magnification', 'optics.magnification_step_fraction'],
    'optics.set_camera_length': ['display.mag_or_camera_length_text', 'optics.camera_length_m', 'optics.magnification_step_fraction', 'truth.camera_length_m'],
    'optics.set_magnification_step_fraction': ['display.mag_or_camera_length_text', 'optics.camera_length_m', 'optics.magnification',
        'optics.magnification_step_fraction', 'truth.camera_length_m'],
    'optics.set_brightness': ['optics.brightness', 'truth.current_density_a_m2', 'truth.illuminated_diameter_m'],
    'optics.set_spot_size': ['optics.spot_size', 'truth.current_density_a_m2', 'truth.illuminated_diameter_m', 'truth.specimen_current_a'],
    'optics.set_defocus': ['optics.defocus_display_m', 'truth.defocus_m'],
    'optics.standard_focus': ['optics.defocus_display_m', 'truth.defocus_m'],
    'optics.auto_focus': ['optics.defocus_display_m', 'truth.defocus_m'],
    'aperture.set_condenser': ['aperture.cl_lever_rad', 'aperture.condenser_inserted', 'truth.current_density_a_m2', 'truth.specimen_current_a'],
    'aperture.set_objective': ['aperture.objective_position', 'aperture.ol_dial_rad'],
    'aperture.set_selected_area': ['aperture.sa_dial_rad', 'aperture.selected_area_position'],
    'screen.set': ['screen.moving', 'screen.up'],
    'screen.toggle': ['screen.moving', 'screen.up'],
    'stage.move_xy': ['stage.moving'],
    'stage.set_z': ['stage.moving'],
    'stage.set_tilt': ['stage.moving'],
    'stage.neutral': ['stage.moving'],
    'image.acquire': ['acquisition.id', 'acquisition.kind', 'acquisition.progress'],
    'diffraction.acquire': ['acquisition.id', 'acquisition.kind', 'acquisition.progress'],
    'montage.acquire': ['acquisition.id', 'acquisition.kind', 'acquisition.progress'],
}
EFFECTS['sim.step'] = ['clock.time_s']
EFFECTS['holder.remove'] = sorted(set(EFFECTS['holder.remove']) | {'truth.airlock_pressure_pa'})   # a vented airlock (> 0.9 atm) opens to exactly 1 atm
# on_step effects: slots whose change during the following host steps differs with and without the call (same scenarios; the session adds result.* and acquisition.started_by_other_agent)
ON_STEP = {
    'plant.configure': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'airlock.pressure_indicated_pa', 'airlock.state',
        'display.mag_or_camera_length_text', 'filament.on', 'filament.state', 'gun.beam_current_indicated_a', 'gun.dark_current_indicated_a', 'ht.on',
        'ht.voltage_indicated_v', 'illumination.screen_current_density_a_m2', 'panel.airlock_lamp', 'panel.beam_lamp', 'panel.power_lamp', 'power.enabled',
        'stage.moving', 'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m',
        'system.error_code', 'system.state', 'truth.airlock_pressure_pa', 'truth.column_pressure_pa', 'truth.current_density_a_m2', 'truth.defocus_m',
        'truth.emission_a', 'truth.filament_on_time_s', 'truth.gun_pressure_pa', 'truth.specimen_current_a', 'truth.specimen_height_m', 'truth.specimens',
        'truth.tmp_speed_fraction', 'truth.view_center_dose_c_m2', 'vacuum.column_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa', 'vacuum.state'],
    'holder.insert_to_airlock': ['airlock.pressure_indicated_pa', 'airlock.state', 'holder.position', 'holder.withdrawal_m'],
    'airlock.set_pump': ['airlock.pressure_indicated_pa', 'airlock.state', 'panel.airlock_lamp', 'truth.airlock_pressure_pa'],
    'holder.insert': ['airlock.pressure_indicated_pa', 'holder.position', 'holder.withdrawal_m', 'truth.airlock_pressure_pa', 'truth.column_pressure_pa',
        'truth.defocus_m', 'truth.gun_pressure_pa', 'truth.specimen_height_m', 'truth.view_center_dose_c_m2', 'vacuum.column_pressure_indicated_pa',
        'vacuum.gun_pressure_indicated_pa'],
    'holder.withdraw': ['airlock.pressure_indicated_pa', 'airlock.state', 'holder.position', 'holder.withdrawal_m', 'panel.airlock_lamp', 'truth.airlock_pressure_pa',
        'truth.column_pressure_pa', 'truth.gun_pressure_pa', 'vacuum.column_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa'],
    'holder.remove': ['airlock.pressure_indicated_pa', 'holder.in_goniometer', 'holder.position', 'holder.withdrawal_m'],
    'power.set': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'airlock.pressure_indicated_pa', 'airlock.state', 'filament.state',
        'gun.beam_current_indicated_a', 'gun.dark_current_indicated_a', 'ht.voltage_indicated_v', 'illumination.screen_current_density_a_m2', 'panel.airlock_lamp',
        'screen.angle_rad', 'screen.moving', 'stage.moving', 'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m',
        'stage.y_m', 'system.state', 'truth.airlock_pressure_pa', 'truth.column_pressure_pa', 'truth.current_density_a_m2', 'truth.defocus_m', 'truth.emission_a',
        'truth.filament_on_time_s', 'truth.gun_pressure_pa', 'truth.specimen_current_a', 'truth.specimen_height_m', 'truth.specimens', 'truth.tmp_speed_fraction',
        'truth.view_center_dose_c_m2', 'vacuum.column_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa', 'vacuum.state'],
    'power.key_toggle': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'airlock.pressure_indicated_pa', 'airlock.state', 'filament.state',
        'gun.beam_current_indicated_a', 'gun.dark_current_indicated_a', 'ht.voltage_indicated_v', 'illumination.screen_current_density_a_m2', 'panel.airlock_lamp',
        'screen.angle_rad', 'screen.moving', 'stage.moving', 'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m',
        'stage.y_m', 'system.state', 'truth.airlock_pressure_pa', 'truth.column_pressure_pa', 'truth.current_density_a_m2', 'truth.defocus_m', 'truth.emission_a',
        'truth.filament_on_time_s', 'truth.gun_pressure_pa', 'truth.specimen_current_a', 'truth.specimen_height_m', 'truth.specimens', 'truth.tmp_speed_fraction',
        'truth.view_center_dose_c_m2', 'vacuum.column_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa', 'vacuum.state'],
    'ht.set_voltage': ['gun.beam_current_indicated_a', 'gun.dark_current_indicated_a', 'ht.voltage_indicated_v', 'illumination.screen_current_density_a_m2'],
    'ht.set_on': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'filament.state', 'gun.beam_current_indicated_a', 'gun.dark_current_indicated_a',
        'ht.voltage_indicated_v', 'illumination.screen_current_density_a_m2', 'stage.moving', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m',
        'truth.airlock_pressure_pa', 'truth.column_pressure_pa', 'truth.current_density_a_m2', 'truth.emission_a', 'truth.filament_on_time_s', 'truth.gun_pressure_pa',
        'truth.specimen_current_a', 'truth.specimens', 'truth.view_center_dose_c_m2', 'vacuum.column_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa'],
    'filament.set_on': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'filament.state', 'gun.beam_current_indicated_a',
        'illumination.screen_current_density_a_m2', 'panel.beam_lamp', 'stage.moving', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m',
        'truth.airlock_pressure_pa', 'truth.column_pressure_pa', 'truth.current_density_a_m2', 'truth.emission_a', 'truth.filament_on_time_s', 'truth.gun_pressure_pa',
        'truth.specimen_current_a', 'truth.specimens', 'truth.view_center_dose_c_m2', 'vacuum.column_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa'],
    'filament.set_bias': ['gun.beam_current_indicated_a', 'illumination.screen_current_density_a_m2', 'truth.current_density_a_m2', 'truth.emission_a',
        'truth.filament_on_time_s', 'truth.specimen_current_a', 'truth.specimens', 'truth.view_center_dose_c_m2'],
    'optics.set_mode': ['illumination.screen_current_density_a_m2', 'truth.current_density_a_m2', 'truth.specimens', 'truth.view_center_dose_c_m2'],
    'optics.toggle_diffraction': ['illumination.screen_current_density_a_m2'],
    'optics.set_magnification': ['illumination.screen_current_density_a_m2'],
    'optics.set_magnification_step_fraction': ['illumination.screen_current_density_a_m2'],
    'optics.set_brightness': ['illumination.screen_current_density_a_m2', 'truth.current_density_a_m2', 'truth.specimens', 'truth.view_center_dose_c_m2'],
    'optics.set_spot_size': ['illumination.screen_current_density_a_m2', 'truth.current_density_a_m2', 'truth.specimen_current_a', 'truth.specimens',
        'truth.view_center_dose_c_m2'],
    'aperture.set_condenser': ['illumination.screen_current_density_a_m2', 'truth.current_density_a_m2', 'truth.specimen_current_a', 'truth.specimens',
        'truth.view_center_dose_c_m2'],
    'aperture.set_objective': ['illumination.screen_current_density_a_m2'],
    'aperture.set_selected_area': ['illumination.screen_current_density_a_m2'],
    'screen.set': ['illumination.screen_current_density_a_m2', 'screen.angle_rad', 'screen.moving'],
    'screen.toggle': ['illumination.screen_current_density_a_m2', 'screen.angle_rad', 'screen.moving'],
    'stage.move_xy': ['illumination.screen_current_density_a_m2', 'stage.moving', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m',
        'truth.specimens', 'truth.view_center_dose_c_m2'],
    'stage.set_z': ['illumination.screen_current_density_a_m2', 'stage.moving', 'stage.z_indicated_m', 'stage.z_m', 'truth.defocus_m', 'truth.specimen_height_m',
        'truth.specimens', 'truth.view_center_dose_c_m2'],
    'stage.set_tilt': ['illumination.screen_current_density_a_m2', 'stage.moving', 'stage.tilt_indicated_rad', 'stage.tilt_rad', 'truth.defocus_m',
        'truth.specimen_height_m', 'truth.specimens', 'truth.view_center_dose_c_m2'],
    'stage.neutral': ['illumination.screen_current_density_a_m2', 'stage.moving', 'stage.tilt_indicated_rad', 'stage.tilt_rad', 'stage.x_indicated_m', 'stage.x_m',
        'stage.y_indicated_m', 'stage.y_m', 'stage.z_indicated_m', 'stage.z_m', 'truth.defocus_m', 'truth.specimen_height_m', 'truth.specimens',
        'truth.view_center_dose_c_m2'],
    'image.acquire': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.started_by_other_agent', 'result.last_image_id'],
    'diffraction.acquire': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.started_by_other_agent', 'result.last_pattern_id'],
    'montage.acquire': ['acquisition.id', 'acquisition.kind', 'acquisition.progress', 'acquisition.started_by_other_agent', 'result.last_montage_id', 'stage.moving',
        'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'truth.specimens', 'truth.view_center_dose_c_m2'],
}

for _k, _v in EFFECTS.items():
    CAPS[_k]['effects'] = list(_v)
ON_STEP = {k: list(dict.fromkeys(v)) for k, v in ON_STEP.items()}
ON_STEP['sim.step'] = ['clock.time_s'] + sorted(set(sum(ON_STEP.values(), [])) | {s for k, v in EFFECTS.items() if k != 'sim.step' for s in v}
                                                 - {'clock.time_s', 'plant.configured', 'specimen.bench_ids', 'holder.grids', 'airlock.pump', 'airlock.lever_rad',
                                                    'ht.voltage_setpoint_v', 'filament.bias', 'optics.mode', 'optics.magnification', 'optics.camera_length_m',
                                                    'optics.magnification_step_fraction', 'optics.brightness', 'optics.spot_size', 'optics.defocus_display_m',
                                                    'aperture.condenser_inserted', 'aperture.objective_position', 'aperture.selected_area_position',
                                                    'aperture.cl_lever_rad', 'aperture.ol_dial_rad', 'aperture.sa_dial_rad', 'screen.up', 'truth.illuminated_diameter_m',
                                                    'truth.camera_length_m', 'truth.filament_life_s', 'display.mag_or_camera_length_text'})
_BEAM_STEP_ZH = ('束流开着时照射改变样品受照剂量（剂量事件）与辐照损伤、荧光屏电流密度读数随之变化')
ON_STEP_ZH = {
    'plant.configure': '重新声明的条件在下一仪器周期起作用：供电超出AC 200V±10%（同平台JEM-1400Plus安装要求）时主机断路（高压、灯丝、泵停止，采集失败，样品台停在当时位置）；冷却水不足时高压与灯丝关闭；'
                       '室温改变之后的气体物料状态',
    'specimen.provide': '无后续过程（样品只在实验台上）',
    'holder.load_grid': '无后续过程（载网只在实验台上的样品杆里）',
    'holder.unload_grid': '无后续过程',
    'holder.insert_to_airlock': '样品杆按约30mm/s推到气锁止点（约10.6s），到达后O形圈封住室内空气，气锁形成（拨杆已在PUMP且通电时随即开始预抽）',
    'airlock.set_pump': '样品杆在气锁止点且通电时：PUMP由机械泵预抽（时间常数4s，约40s降到5Pa，Evac Ready灯亮），AIR放气到大气压（时间常数1s）',
    'holder.insert': '样品杆按约30mm/s转入镜筒（约1.4s），到达后载网在物镜极靴间隙；气锁残余气体与样品杆放气使镜筒与电子枪压力短暂上升后被涡轮分子泵抽走',
    'holder.withdraw': '样品杆约1.4s回到气锁止点；气锁以镜筒压力重新形成，拨杆在PUMP时继续由机械泵保持',
    'holder.remove': '样品杆约10.6s完全拔出，到达后回到实验台（测角台空）',
    'power.set': '开机：约90s软件加载（off→booting→ready）、涡轮分子泵约180s升速、镜筒与电子枪抽到就绪压力（vacuum.state就绪），荧光屏继续翻到指令位置；'
                 '断电：真空阀关闭，镜筒缓慢漏气，涡轮泵减速，读数与气锁状态消失（idle），样品台与翻转中的荧光屏停在当时位置',
    'error.acknowledge': '无后续过程',
    'ht.set_voltage': '高压开着时按1kV/s升降到新设定值，暗电流与束流读数、屏上kV随之变化；样品散射随电压改变，荧光屏电流密度读数随之变化',
    'ht.set_on': '开：按1kV/s升到设定值，暗电流读数随之上升；关：高压与灯丝立即关闭，束流停止（灯丝不再计时、样品不再受照、运行中的采集在下一周期以BEAM_OFF失败）',
    'filament.set_on': '开：约20s加热到饱和、发射与束流读数上升、灯丝磨损计时、电子枪放气增加；' + _BEAM_STEP_ZH + '；关：束流停止，运行中的采集以BEAM_OFF失败',
    'filament.set_bias': '新偏压改变饱和发射电流、束流读数、灯丝磨损速率与样品受照剂量（样品台移动时剂量扫迹按新的单位长度注量另起一段）',
    'gun.replace_filament': '无后续过程',
    'optics.set_mode': '模式改变照明区（LOW MAG约20倍宽）、荧光屏电流密度读数（衍射模式不读）与之后的剂量分布',
    'optics.toggle_diffraction': '荧光屏电流密度读数在衍射模式下为null，回到MAG后恢复',
    'optics.set_magnification': '倍率改变荧光屏电流密度读数（∝1/M²）',
    'optics.set_camera_length': '无后续过程（衍射相机长度在下次采集时使用）',
    'optics.set_magnification_step_fraction': '倍率档改变荧光屏电流密度读数',
    'optics.set_brightness': '照明区大小改变样品处电流密度、之后的剂量分布与荧光屏电流密度读数',
    'optics.set_spot_size': '束斑号改变样品电流、电流密度、剂量与读数',
    'optics.set_defocus': '无后续过程（离焦在下次采集时使用）', 'optics.standard_focus': '无后续过程', 'optics.auto_focus': '无后续过程',
    'aperture.set_condenser': '光阑改变样品电流、电流密度、剂量与荧光屏读数（样品台移动时剂量扫迹按新的单位长度注量另起一段）',
    'aperture.set_objective': '物镜光阑改变明场透过率（荧光屏读数）；LOW MAG下挡住大部分视场',
    'aperture.set_selected_area': 'MAG与衍射模式下选区光阑限制视场，荧光屏电流密度读数随之变化；选区在下次衍射采集时使用',
    'screen.set': '荧光屏约0.5s翻到位；放下后才有荧光屏电流密度读数，抬起到位后相机才能采集',
    'screen.toggle': '荧光屏约0.5s翻到位；放下后才有荧光屏电流密度读数，抬起到位后相机才能采集',
    'stage.move_xy': 'X/Y按50µm/s移动；束流开着时视场与剂量落点随之移动',
    'stage.set_z': 'Z按10µm/s移动，改变视场中心的样品高度与真实离焦（倾转时还移动视场与剂量落点，荧光屏电流密度读数随视场透过率变化）',
    'stage.set_tilt': '倾转按2°/s变化，改变样品高度引起的视场偏移、剂量落点、路径厚度与栅条侧壁遮挡，荧光屏电流密度读数随视场透过率变化',
    'stage.neutral': 'X/Y/Z与倾转按各自速度回零；视场与剂量落点随之移动，荧光屏电流密度读数随视场透过率变化',
    'image.acquire': '曝光+0.2s读出后在下一仪器周期生成图像记录（束流关闭或断电时以failed结束）',
    'diffraction.acquire': '曝光+0.2s读出后在下一仪器周期生成衍射记录（束流关闭或断电时以failed结束）',
    'montage.acquire': '样品台按蛇形路径逐帧移动、稳定、曝光、读出，结束后回到起点并生成拼图记录；束流照射沿路径分布',
    'sim.step': '统一宿主时钟推进全部过程：真空与气锁、泵、高压升降、灯丝加热与磨损、束流剂量与损伤、样品台/样品杆/荧光屏运动、采集完成与故障',
}
ON_STEP_ZH['power.key_toggle'] = ON_STEP_ZH['power.set']
# how each slot is sampled when read
_SAMPLING_READINGS = {'vacuum.column_pressure_indicated_pa': 'column gauge, 10 % relative noise', 'vacuum.gun_pressure_indicated_pa': 'gun gauge, 10 % relative noise',
                      'airlock.pressure_indicated_pa': 'airlock gauge, 10 % relative noise (null when the airlock is not formed)',
                      'ht.voltage_indicated_v': 'HT meter (the ramping voltage at the cycle)', 'gun.dark_current_indicated_a': 'dark-current meter, 0.1 uA fluctuation', 'gun.beam_current_indicated_a': 'beam-current meter = the same cycle dark reading + emission, 0.05 uA extra fluctuation',
                      'illumination.screen_current_density_a_m2': 'screen current meter, 3 % relative noise',
                      }
_SAMPLING_CONTINUOUS = {'clock.time_s', 'stage.x_m', 'stage.y_m', 'stage.z_m', 'stage.tilt_rad', 'stage.x_indicated_m', 'stage.y_indicated_m', 'stage.z_indicated_m',
                        'stage.tilt_indicated_rad', 'stage.moving', 'screen.angle_rad', 'screen.moving', 'holder.withdrawal_m', 'holder.position',
                        'holder.in_goniometer', 'acquisition.progress', 'truth.defocus_m', 'truth.specimen_height_m'}
_SAMPLING_MIXED = {'airlock.state': 'the holder position changes continuously, the airlock pressure on the 1 s cycle',
                   'truth.airlock_pressure_pa': 'pressure on the 1 s cycle; the airlock forms, merges or opens when the holder arrives (continuous)',
                   'truth.column_pressure_pa': 'pressure on the 1 s cycle; the airlock gas joins at turn-in and splits off when the holder arrives back (continuous)',
                   'truth.view_center_dose_c_m2': 'the view centre moves continuously with the stage; dose is deposited on the 1 s cycle',
                   'panel.airlock_lamp': 'follows airlock.state'}
_SAMPLING_CYCLE = {'system.state', 'system.error_code', 'power.enabled', 'vacuum.state', 'ht.on', 'filament.on', 'filament.state', 'panel.beam_lamp',
                   'panel.power_lamp', 'acquisition.id', 'acquisition.kind', 'acquisition.started_by_other_agent', 'result.last_image_id', 'result.last_pattern_id',
                   'result.last_montage_id', 'truth.gun_pressure_pa', 'truth.tmp_speed_fraction', 'truth.filament_on_time_s', 'truth.emission_a',
                   'truth.specimen_current_a', 'truth.current_density_a_m2', 'truth.specimens'}
_SAMPLING_COMMAND = set(SLOTS) - _SAMPLING_CONTINUOUS - set(_SAMPLING_READINGS) - _SAMPLING_CYCLE - set(_SAMPLING_MIXED)


def _sampling_text(k):
    if k in _SAMPLING_CONTINUOUS:
        return ('Instantaneous value at the read time: stage, holder and screen motions and the HT ramp are integrated continuously between the 1 s instrument '
                'cycles (encoder readings rounded to their display resolution); acquisition progress follows the simulation clock.')
    if k in _SAMPLING_READINGS:
        return f'Reading sampled at the latest 1 s instrument cycle and held until the next one ({_SAMPLING_READINGS[k]}); null while unpowered or not applicable.'
    if k in _SAMPLING_MIXED:
        return f'Instantaneous value at the read time ({_SAMPLING_MIXED[k]}).'
    if k in _SAMPLING_CYCLE:
        return ('Process state advanced on the 1 s instrument cycle (vacuum, pumps, filament heating and wear, dose, acquisition completion, faults); commands '
                'that set it take effect at once.')
    return 'Changes only when a command is accepted (set point, configuration or bookkeeping) or, for the joint mirrors, with the command at once; no time dynamics.'


_R = ['power.enabled', 'system.state', 'system.error_code', 'acquisition.id']
_CAM = _R + ['filament.state', 'ht.voltage_indicated_v', 'screen.up', 'screen.moving', 'holder.position']
PRECONDITIONS = {
    'plant.configure': ('无；随机数种子只在首次声明时生效', []),
    'specimen.provide': ('已声明实验室条件；ID不重复（最多20个）；支持膜为none时厚度为0且无孔，否则厚度>0；物质在模板库中；无晶体结构的物质只能为非晶；晶带轴只用于单晶且不全为0；'
                         '染色只用于非晶相；颗粒覆盖率≤0.45且须有支持膜；覆盖率>0', ['plant.configured', 'specimen.bench_ids']),
    'holder.load_grid': ('已声明实验室条件；该样品杆不在测角台中且空着；载网在实验台上', ['plant.configured', 'holder.in_goniometer', 'holder.grids', 'specimen.bench_ids']),
    'holder.unload_grid': ('已声明实验室条件；该样品杆不在测角台中且装有载网', ['plant.configured', 'holder.in_goniometer', 'holder.grids']),
    'holder.insert_to_airlock': ('已声明实验室条件；无采集；测角台空着；灯丝关；样品台在中性位（X/Y/Z目标与位置在±0.5µm、倾转在±0.5°内，设施规程）',
                                 ['plant.configured', 'acquisition.id', 'holder.in_goniometer', 'filament.on', 'stage.x_indicated_m', 'stage.y_indicated_m',
                                  'stage.z_indicated_m', 'stage.tilt_indicated_rad', 'stage.moving']),
    'airlock.set_pump': ('已声明实验室条件（拨杆随时可拨；抽/放气需要样品杆在气锁止点且通电）', ['plant.configured', 'holder.position', 'power.enabled']),
    'holder.insert': ('已声明实验室条件、通电；无采集；样品杆在气锁止点；灯丝关；拨杆在PUMP且气锁压力<5Pa（Evac Ready）',
                      ['plant.configured', 'power.enabled', 'acquisition.id', 'holder.position', 'filament.on', 'airlock.pump', 'airlock.state', 'panel.airlock_lamp']),
    'holder.withdraw': ('已声明实验室条件；无采集；样品杆在镜筒内；灯丝关；样品台在中性位', ['plant.configured', 'acquisition.id', 'holder.position', 'filament.on',
                                                                  'stage.x_indicated_m', 'stage.y_indicated_m', 'stage.z_indicated_m', 'stage.tilt_indicated_rad',
                                                                  'stage.moving']),
    'holder.remove': ('已声明实验室条件；无采集；样品杆在气锁止点；拨杆在AIR且气锁压力>0.9atm（已放气）', ['plant.configured', 'acquisition.id', 'holder.position',
                                                                                  'airlock.pump', 'airlock.state']),
    'power.set': ('已声明实验室条件；开机时供电须在AC 200V±10%内（同平台JEM-1400Plus安装要求）', ['plant.configured', 'power.enabled']),
    'power.key_toggle': ('已声明实验室条件；开机时供电须在AC 200V±10%内（同平台JEM-1400Plus安装要求）', ['plant.configured', 'power.enabled']),
    'error.acknowledge': ('通电且有待确认错误', ['power.enabled', 'system.error_code']),
    'ht.set_voltage': ('通电、软件已启动、无待确认错误与采集', list(_R)),
    'ht.set_on': ('通电、软件已启动；开高压时还须无待确认错误与采集、镜筒真空就绪、冷却水≥5L/min（8.33×10⁻⁵m³/s，同平台JEM-1400Plus安装要求）（关高压总是允许）', _R + ['vacuum.state', 'ht.on']),
    'filament.set_on': ('通电、软件已启动；开灯丝时还须无待确认错误与采集、灯丝未烧断、高压已开且到设定值、真空就绪、样品杆不在插拔中（关灯丝总是允许）',
                        _R + ['filament.state', 'ht.on', 'ht.voltage_indicated_v', 'ht.voltage_setpoint_v', 'vacuum.state', 'holder.position']),
    'filament.set_bias': ('通电、软件已启动、无待确认错误与采集', list(_R)),
    'gun.replace_filament': ('已声明实验室条件；高压关', ['plant.configured', 'ht.on']),
    'optics.set_mode': ('通电、软件已启动、无待确认错误与采集；DIFF只能从MAG（或已在DIFF）进入', _R + ['optics.mode']),
    'optics.toggle_diffraction': ('通电、软件已启动、无待确认错误与采集；不在LOW MAG', _R + ['optics.mode']),
    'optics.set_magnification': ('通电、软件已启动、无待确认错误与采集；不在衍射模式', _R + ['optics.mode']),
    'optics.set_camera_length': ('通电、软件已启动、无待确认错误与采集', list(_R)),
    'optics.set_magnification_step_fraction': ('通电、软件已启动、无待确认错误与采集', _R + ['optics.mode']),
    'optics.set_brightness': ('通电、软件已启动、无待确认错误与采集', list(_R)),
    'optics.set_spot_size': ('通电、软件已启动、无待确认错误与采集', list(_R)),
    'optics.set_defocus': ('通电、软件已启动、无待确认错误与采集', list(_R)),
    'optics.standard_focus': ('通电、软件已启动、无待确认错误与采集', list(_R)),
    'optics.auto_focus': ('通电、软件已启动、无待确认错误与采集；灯丝饱和且高压到设定值；荧光屏抬起到位；MAG模式；视场里至少2%有样品结构（支持膜或样品相）；'
                          '所需离焦显示在±20µm内', _R + ['filament.state', 'screen.up', 'screen.moving', 'optics.mode', 'holder.position']),
    'aperture.set_condenser': ('已声明实验室条件；无采集', ['plant.configured', 'acquisition.id']),
    'aperture.set_objective': ('已声明实验室条件；无采集', ['plant.configured', 'acquisition.id']),
    'aperture.set_selected_area': ('已声明实验室条件；无采集', ['plant.configured', 'acquisition.id']),
    'screen.set': ('通电、软件已启动、无待确认错误与采集', list(_R)),
    'screen.toggle': ('通电、软件已启动、无待确认错误与采集', list(_R)),
    'stage.move_xy': ('通电、软件已启动、无待确认错误与采集；样品杆在镜筒内且不在插拔中', _R + ['holder.position']),
    'stage.set_z': ('通电、软件已启动、无待确认错误与采集；样品杆在镜筒内且不在插拔中', _R + ['holder.position']),
    'stage.set_tilt': ('通电、软件已启动、无待确认错误与采集；样品杆在镜筒内且不在插拔中；|倾转|不超过该样品杆的极限（快换杆20°、高倾转杆70°）',
                       _R + ['holder.position', 'holder.in_goniometer']),
    'stage.neutral': ('通电、软件已启动、无待确认错误与采集；样品杆不在插拔中', _R + ['holder.position']),
    'image.acquire': ('通电、软件已启动、无待确认错误与采集；样品杆不在插拔中；灯丝饱和且高压到设定值；荧光屏抬起到位；样品台静止；记录未满200条；成像模式（LOW MAG或MAG）',
                      _CAM + ['stage.moving', 'optics.mode']),
    'diffraction.acquire': ('同图像采集，但须在衍射模式', _CAM + ['stage.moving', 'optics.mode']),
    'montage.acquire': ('同图像采集（成像模式）；样品杆在镜筒内；全部拼图帧中心在样品台±1mm行程内', _CAM + ['stage.moving', 'optics.mode', 'stage.x_indicated_m',
                                                                                    'stage.y_indicated_m']),
    'image.result': ('记录存在且不属于另一个agent', []), 'diffraction.result': ('记录存在且不属于另一个agent', []),
    'montage.result': ('记录存在且不属于另一个agent', []),
    'camera.auto_exposure': ('通电、软件已启动、无待确认错误；灯丝饱和且高压到设定值；荧光屏抬起到位；成像模式；视场有信号', _R[:3] + ['filament.state', 'screen.up', 'optics.mode']),
    'records.list': ('无', []), 'events.read': ('无', []), 'ledger.read': ('无', []), 'provenance.read': ('无', []), 'sim.step': ('无', []),
}
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入结构、类型或范围', 'ACCESS_DENIED': '该角色无权调用，或当前采集/记录属于另一个agent', 'REQUEST_CONFLICT': '同一request_id已用于不同的请求',
    'UNKNOWN_INSTANCE': '实例ID不存在', 'UNSUPPORTED': '本配置不提供该功能（理由见function_zh）', 'UNKNOWN_CAPABILITY': '能力ID不存在', 'UNKNOWN_OPERATION': '操作ID不存在',
    'NOT_RUNNING': '操作不在进行中', 'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_OFF': '未通电（或断电中止了采集）', 'NOT_READY': 'TEM Center仍在加载',
    'ERROR_PENDING': '有待确认的错误（先确认）', 'NO_ERROR': '没有待确认的错误', 'MAINS_OUT_OF_RANGE': '供电电压超出AC 200V±10%（同平台安装要求）',
    'ACQUISITION_RUNNING': '有图像、衍射或拼图采集正在进行', 'VACUUM_NOT_READY': '镜筒或电子枪真空未就绪', 'COOLING_WATER_LOW': '冷却水流量低于5L/min（8.33×10⁻⁵m³/s，同平台JEM-1400Plus安装要求）',
    'HT_NOT_READY': '高压未开或尚未升到设定值', 'HT_ON': '高压开着（先关高压）', 'FILAMENT_BURNED_OUT': '灯丝已烧断（关高压后更换）', 'FILAMENT_ON': '灯丝开着（插拔样品杆前先关灯丝）',
    'HOLDER_MOVING': '样品杆正在推入、转入、拉回或拔出', 'BEAM_NOT_READY': '束流未就绪：灯丝未饱和或高压未到设定值', 'SCREEN_DOWN': '荧光屏没有抬起到位（F1抬起约0.5s）',
    'STAGE_MOVING': '样品台仍在运动', 'RECORDS_FULL': '记录已满（200条）', 'DIFF_MODE': '当前为衍射模式（先回到成像模式）', 'NOT_IN_DIFF_MODE': '当前不是衍射模式',
    'NOT_IN_MAG_MODE': '须在MAG模式（LOW MAG下不能进入选区衍射或自动聚焦）', 'AUTOFOCUS_FAILED': '自动聚焦失败：视场里没有足够的样品结构或所需离焦超出±20µm',
    'NO_SIGNAL': '视场里没有电子信号（光阑挡住或样品不透）', 'TILT_OUT_OF_RANGE': '倾转超出该样品杆的极限（快换杆±20°、高倾转杆±70°）',
    'HOLDER_NOT_IN_COLUMN': '样品杆不在镜筒内', 'HOLDER_NOT_AT_AIRLOCK': '样品杆不在气锁止点', 'HOLDER_IN_GONIOMETER': '该样品杆在测角台中（先取出）',
    'HOLDER_OCCUPIED': '样品杆上已有载网', 'HOLDER_EMPTY': '样品杆上没有载网', 'GONIOMETER_OCCUPIED': '测角台中已有样品杆',
    'STAGE_NOT_NEUTRAL': '样品台不在中性位（设施规程：插拔前必须Stage Neutral）', 'AIRLOCK_NOT_EVACUATED': '气锁没有抽好（拨杆须在PUMP且Evac Ready）',
    'AIRLOCK_NOT_VENTED': '气锁没有放气（拨杆须在AIR且回到大气压），真空把样品杆吸住', 'SPECIMEN_EXISTS': '样品ID已存在或实验台已有20个样品',
    'UNKNOWN_SUBSTANCE': '物质不在TEM模板库中', 'UNKNOWN_SPECIMEN': '样品ID不存在', 'SPECIMEN_NOT_ON_BENCH': '样品不在实验台上',
    'SUPPORT_INVALID': '支持膜与厚度/孔洞不一致（无膜时厚度为0且无孔，有膜时厚度>0）', 'SUPPORT_REQUIRED': '颗粒相需要支持膜',
    'CRYSTALLINITY_INVALID': '该物质在模板库中没有晶体结构，只能为非晶', 'ZONE_AXIS_INVALID': '晶带轴只用于单晶且不能为[000]', 'STAIN_INVALID': '染色只用于非晶相',
    'COVERAGE_TOO_HIGH': '颗粒投影覆盖率超过0.45（颗粒在格点上不重叠的上限）', 'COVERAGE_INVALID': '覆盖率须大于0',
    'MONTAGE_OUT_OF_RANGE': '拼图帧超出样品台±1mm行程', 'UNKNOWN_RECORD': '图像、衍射或拼图记录不存在', 'USER_CANCEL': '采集被取消',
    'BEAM_OFF': '采集中束流关闭（关灯丝或高压、灯丝烧断、高压跳闸、冷却水不足）', 'HT_TRIP': '电子枪真空变差，高压跳闸', 'FILAMENT_BURNOUT': '钨灯丝烧断',
    'MAINS_FAULT': '供电电压异常，主机断路',
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_RE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING']
_ACQ_ERR = _RE + ['HOLDER_MOVING', 'BEAM_NOT_READY', 'SCREEN_DOWN', 'STAGE_MOVING', 'RECORDS_FULL']
CAP_ERRORS = {
    'plant.configure': [],
    'specimen.provide': ['PLANT_REQUIRED', 'SPECIMEN_EXISTS', 'SUPPORT_INVALID', 'UNKNOWN_SUBSTANCE', 'CRYSTALLINITY_INVALID', 'ZONE_AXIS_INVALID', 'STAIN_INVALID',
                         'COVERAGE_TOO_HIGH', 'SUPPORT_REQUIRED', 'COVERAGE_INVALID'],
    'holder.load_grid': ['PLANT_REQUIRED', 'HOLDER_IN_GONIOMETER', 'UNKNOWN_SPECIMEN', 'SPECIMEN_NOT_ON_BENCH', 'HOLDER_OCCUPIED'],
    'holder.unload_grid': ['PLANT_REQUIRED', 'HOLDER_IN_GONIOMETER', 'HOLDER_EMPTY'],
    'holder.insert_to_airlock': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING', 'GONIOMETER_OCCUPIED', 'FILAMENT_ON', 'STAGE_NOT_NEUTRAL'],
    'airlock.set_pump': ['PLANT_REQUIRED'],
    'holder.insert': ['PLANT_REQUIRED', 'POWER_OFF', 'ACQUISITION_RUNNING', 'HOLDER_NOT_AT_AIRLOCK', 'FILAMENT_ON', 'AIRLOCK_NOT_EVACUATED'],
    'holder.withdraw': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING', 'HOLDER_NOT_IN_COLUMN', 'FILAMENT_ON', 'STAGE_NOT_NEUTRAL'],
    'holder.remove': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING', 'HOLDER_NOT_AT_AIRLOCK', 'AIRLOCK_NOT_VENTED'],
    'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.key_toggle': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'error.acknowledge': ['PLANT_REQUIRED', 'POWER_OFF', 'NO_ERROR'],
    'ht.set_voltage': list(_RE), 'ht.set_on': _RE + ['VACUUM_NOT_READY', 'COOLING_WATER_LOW'],
    'filament.set_on': _RE + ['FILAMENT_BURNED_OUT', 'HT_NOT_READY', 'VACUUM_NOT_READY', 'HOLDER_MOVING'], 'filament.set_bias': list(_RE),
    'gun.replace_filament': ['PLANT_REQUIRED', 'HT_ON'],
    'optics.set_mode': _RE + ['NOT_IN_MAG_MODE'], 'optics.toggle_diffraction': _RE + ['NOT_IN_MAG_MODE'], 'optics.set_magnification': _RE + ['DIFF_MODE'],
    'optics.set_camera_length': list(_RE), 'optics.set_magnification_step_fraction': list(_RE), 'optics.set_brightness': list(_RE),
    'optics.set_spot_size': list(_RE), 'optics.set_defocus': list(_RE), 'optics.standard_focus': list(_RE),
    'optics.auto_focus': _RE + ['BEAM_NOT_READY', 'SCREEN_DOWN', 'NOT_IN_MAG_MODE', 'AUTOFOCUS_FAILED'],
    'aperture.set_condenser': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING'], 'aperture.set_objective': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING'],
    'aperture.set_selected_area': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING'], 'screen.set': list(_RE), 'screen.toggle': list(_RE),
    'stage.move_xy': _RE + ['HOLDER_MOVING', 'HOLDER_NOT_IN_COLUMN'], 'stage.set_z': _RE + ['HOLDER_MOVING', 'HOLDER_NOT_IN_COLUMN'],
    'stage.set_tilt': _RE + ['HOLDER_MOVING', 'HOLDER_NOT_IN_COLUMN', 'TILT_OUT_OF_RANGE'], 'stage.neutral': _RE + ['HOLDER_MOVING'],
    'image.acquire': _ACQ_ERR + ['DIFF_MODE', 'USER_CANCEL', 'BEAM_OFF'],
    'diffraction.acquire': _ACQ_ERR + ['NOT_IN_DIFF_MODE', 'USER_CANCEL', 'BEAM_OFF'],
    'montage.acquire': _ACQ_ERR + ['DIFF_MODE', 'HOLDER_NOT_IN_COLUMN', 'MONTAGE_OUT_OF_RANGE', 'USER_CANCEL', 'BEAM_OFF'],
    'image.result': ['UNKNOWN_RECORD'], 'diffraction.result': ['UNKNOWN_RECORD'], 'montage.result': ['UNKNOWN_RECORD'],
    'camera.auto_exposure': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'BEAM_NOT_READY', 'SCREEN_DOWN', 'DIFF_MODE', 'NO_SIGNAL'],
}
_PARTIAL = {'USER_CANCEL', 'BEAM_OFF', 'POWER_OFF'}
CAP_ERROR_ZH = {('image.acquire', 'POWER_OFF'): '未通电；或采集中断电（记录以failed结束）', ('diffraction.acquire', 'POWER_OFF'): '未通电；或采集中断电（记录以failed结束）',
                ('montage.acquire', 'POWER_OFF'): '未通电；或采集中断电（记录以failed结束，样品台停在断电位置）'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件（室温、湿度、供电、冷却水、噪声种子），没有可见模型变化',
    'specimen.provide': '只在过程层实验台上增加载网样品；场景没有载网几何（样品位由接口点grid_seat表示）',
    'holder.load_grid': '载网只进入过程层样品杆；场景的样品杆尖端不显示载网', 'holder.unload_grid': '载网只离开过程层样品杆；场景没有载网几何',
    'error.acknowledge': '只清除错误状态，没有可见模型变化', 'ht.set_voltage': '设定值；屏上kV读数（高压表示值）随step显示升降中的实际电压',
    'filament.set_bias': '偏压没有可见几何；束流读数随step变化', 'gun.replace_filament': '过程层更换灯丝；场景中电子枪不打开（没有维护动画）',
    'optics.set_camera_length': '屏上读数只在衍射模式下显示相机长度（mm）', 'optics.set_spot_size': '束斑号没有可见几何（左面板旋钮之外的按键未建模）',
    'optics.auto_focus': '自动聚焦只改物镜离焦；FOCUS旋钮随离焦显示值转动（见合同model_bindings）',
    'image.acquire': '采集只在过程层生成图像记录；显示器贴图不随图像内容变化', 'image.result': '只读记录', 'diffraction.acquire': '采集只在过程层生成衍射记录',
    'diffraction.result': '只读记录', 'montage.acquire': '拼图只在过程层生成记录；样品台X/Y为微米级运动，没有可见几何', 'montage.result': '只读记录',
    'camera.auto_exposure': '分析计算，没有可见模型变化', 'records.list': '只读列表', 'events.read': '只读事件', 'ledger.read': '只读账目（evaluator）',
    'provenance.read': '只读来源记录（evaluator）', 'stage.move_xy': 'X/Y为±1mm内的微米级运动，场景没有对应关节', 'stage.set_z': 'Z为±0.2mm内的微米级运动，场景没有对应关节',
}
# states that the Matterix binding mirrors into the scene (joints, knob angles, monitor readings and lamps; interface_definition)
SCENE_MIRRORED = {'holder.withdrawal_m', 'stage.tilt_rad', 'screen.angle_rad', 'airlock.lever_rad', 'aperture.cl_lever_rad', 'aperture.ol_dial_rad',
                  'aperture.sa_dial_rad', 'optics.brightness', 'optics.magnification_step_fraction', 'optics.defocus_display_m', 'ht.voltage_indicated_v',
                  'display.mag_or_camera_length_text', 'stage.tilt_indicated_rad', 'illumination.screen_current_density_a_m2', 'panel.airlock_lamp',
                  'panel.power_lamp', 'panel.beam_lamp'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节、显示器读数或指示灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


_GUARDED = _CONTROL_GUARDED | _DATA_GUARDED
RESOURCES = {'image.acquire': ['electron_beam', 'camera', 'stage', 'host_clock'], 'diffraction.acquire': ['electron_beam', 'camera', 'host_clock'],
             'montage.acquire': ['electron_beam', 'camera', 'stage', 'host_clock'], 'holder.insert_to_airlock': ['goniometer', 'holder'],
             'holder.insert': ['goniometer', 'holder', 'airlock', 'vacuum_system'], 'holder.withdraw': ['goniometer', 'holder', 'airlock'],
             'holder.remove': ['goniometer', 'holder', 'airlock'], 'airlock.set_pump': ['airlock', 'rotary_pump'], 'holder.load_grid': ['bench', 'holder'],
             'holder.unload_grid': ['bench', 'holder'], 'power.set': ['mains', 'vacuum_system', 'electron_gun'], 'power.key_toggle': ['mains', 'vacuum_system', 'electron_gun'],
             'ht.set_voltage': ['electron_gun'], 'ht.set_on': ['electron_gun', 'cooling_water'], 'filament.set_on': ['electron_gun'], 'filament.set_bias': ['electron_gun'],
             'gun.replace_filament': ['electron_gun'], 'optics.set_mode': ['lenses'], 'optics.toggle_diffraction': ['lenses'], 'optics.set_magnification': ['lenses'],
             'optics.set_camera_length': ['lenses'], 'optics.set_magnification_step_fraction': ['lenses'], 'optics.set_brightness': ['condenser_lens'],
             'optics.set_spot_size': ['condenser_lens'], 'optics.set_defocus': ['objective_lens'], 'optics.standard_focus': ['objective_lens'],
             'optics.auto_focus': ['objective_lens', 'camera', 'electron_beam'], 'aperture.set_condenser': ['condenser_aperture'],
             'aperture.set_objective': ['objective_aperture'], 'aperture.set_selected_area': ['selected_area_aperture'], 'screen.set': ['viewing_screen'],
             'screen.toggle': ['viewing_screen'], 'stage.move_xy': ['stage'], 'stage.set_z': ['stage'], 'stage.set_tilt': ['stage'], 'stage.neutral': ['stage'],
             'plant.configure': ['room', 'mains', 'cooling_water'], 'specimen.provide': ['bench'], 'sim.step': ['host_clock'], 'camera.auto_exposure': ['camera'],
             'image.result': ['records'], 'diffraction.result': ['records'], 'montage.result': ['records'], 'error.acknowledge': ['tem_center']}
_ROBOT = ('插拔与装卸为宿主/机器人技能的过程层抽象，没有接触、力控或机器人验证；样品杆插拔关节由过程状态镜像（推入、转入、拉回、拔出按约30mm/s），场景没有载网几何。')
CAP_LIMITS = {
    'plant.configure': ['宿主声明的实验室条件；室温只影响气体状态并给出越限警告，冷却水只按≥5L/min（8.33×10⁻⁵m³/s，同平台JEM-1400Plus安装要求）判断高压能否开启（不模拟透镜温升与漂移），供电只按±10%判断开机与断路；'
                        '室温波动（≤1°C/h）、杂散磁场与振动对图像的影响不建模。'],
    'specimen.provide': ['载网为3mm铜/镍/金网：目数只决定网格节距与栅条宽度（200目125/35µm、300目83/25µm、400目62/20µm，工程值），栅条完全不透；支持膜孔洞按直径1.2µm±15%的'
                         '格点随机孔（工程）；薄膜相按5µm岛状、切片按150µm条带覆盖；颗粒为抖动格点上平放的旋转椭球（膜面内直径size±10%、法向厚度thickness，'
                         'thickness=size时为球），多晶颗粒由4×4晶粒组成；单晶膜按1°弯曲（3µm尺度）'
                         '产生弯曲消光轮廓；染色切片的染色质量分数在0.4µm尺度上0.3–1.7倍起伏（膜、颗粒状结构的抽象）；这些形貌都是统计抽象，不是具体生物或材料样品。'],
    'holder.load_grid': [_ROBOT, '载网装入时随机决定安装高度（相对优中心面，1σ=5µm，工程）与网格方向；快换杆不能倾转超过±20°（HR极靴，ETH设施数据；JEM-1400Plus目录为HC ±25°）。'],
    'holder.unload_grid': [_ROBOT],
    'holder.insert_to_airlock': [_ROBOT, '设施规程要求的物镜光阑退出只记警告，不阻止插杆；不建模挡板插头（blanking plug）。'],
    'airlock.set_pump': ['气锁为5cm³单体积，机械泵预抽时间常数4s（约40s到5Pa）、放气时间常数1s为工程值；Evac Ready阈值5Pa为工程值（设施规程只写绿灯亮）。'],
    'holder.insert': [_ROBOT, '转入时气锁残余气体全部进入镜筒（瞬时），样品杆与载网带入的放气量按2×10⁻⁶Pa·m³/s、60s衰减（工程）。'],
    'holder.withdraw': [_ROBOT], 'holder.remove': [_ROBOT],
    'ht.set_on': ['高压以1kV/s升降为工程值；暗电流按设施数据（80kV 44µA、120kV 66µA）换算为1.818GΩ分压器；未建模电压稳定度与高压老化（conditioning）。'],
    'filament.set_on': ['钨灯丝：饱和发射20µA·exp(−偏压/33)与20s加热、150h平均寿命（Weibull k=3）及磨损速率∝(发射/6µA)²均为工程值；电子枪对中按始终对好处理。'],
    'gun.replace_filament': ['只重置灯丝寿命与磨损；不模拟电子枪放气、重新抽气与灯丝对中（实际需数小时）。'],
    'optics.set_brightness': ['照明：交叉点直径0.3µm×√(相对束流)、最大展开200µm、LOW MAG约20倍宽、会聚半角在交叉点2mrad并随展开反比下降（≥10µrad）；枪到样品的透过率3×10⁻⁴、'
                              '束斑相对束流与聚光镜光阑退出时3倍均为工程值；束始终对中（SHIFT旋钮未接线）。'],
    'optics.set_defocus': ['真实离焦=显示值−视场中心样品高度（载网安装偏差+Z，倾转时÷cos）；像散按始终消好处理。'],
    'optics.auto_focus': ['自动聚焦为理想算法：残余误差按30nm（1σ，工程）随机；不消耗剂量、没有耗时。'],
    'aperture.set_objective': ['物镜光阑120/60/40/20µm与物镜焦距2mm（收集半角30/15/10/5mrad）、光阑退出时的有效截止60mrad为工程值；光阑按始终对中处理；'
                               'LOW MAG下插入时视场被限制在直径等于光阑孔径的圆内（工程抽象）。'],
    'aperture.set_selected_area': ['选区光阑100/50/20/10µm与选区放大30倍为工程值；MAG与衍射模式下它限制视场/选区（被挡住的电子计入选区光阑电荷账），LOW MAG下不起作用；'
                                   '不计球差引起的选区误差。'],
    'screen.set': ['荧光屏抬起约0.5s为工程值；荧光屏电流密度读数=样品处电流密度×视场透过率/M²（相机放大倍数与屏上倍数取相同，工程）。'],
    'stage.move_xy': ['X/Y行程±1mm、速度50µm/s、读数0.1µm为工程值；样品台没有回差、漂移与定位误差。'],
    'stage.set_z': ['Z行程±0.2mm、速度10µm/s为工程值。'],
    'stage.set_tilt': ['倾转速度2°/s为工程值；倾转轴过优中心面，样品面高于轴h时视场偏移h·tan(倾转)；薄膜的路径厚度÷cos(倾转)、样品面投影在倾转方向缩短cos(倾转)，'
                       '颗粒按旋转椭球投影（球不变）；载网栅条18µm厚，倾转时侧壁使栅条投影加宽18µm·tan(倾转)。'],
    'image.acquire': ['图像由64×64采样点计算（输出32×32缩略图与区域统计，不输出2048×2048全图）：明场透过率=exp(−Σnσ_el(>α)t)（Lenz–Wentzel屏蔽散射截面，按物镜光阑收集半角α）'
                      '×晶体的两束动力学衍射（模板库结构因子的消光距离、每个颗粒/晶粒的取向、倾转与路径厚度；多束消耗按1−exp(−ΣI_g)，散射到光阑外的晶体散射只计热漫散射分数'
                      '1−exp(−2Bs²)）；小于2个采样间隔的颗粒、晶粒与孔按取向平均与泊松计数起伏处理；相机：21.7µm像素、每电子8计数、DQE 0.5、读出噪声0.8e、16位饱和；'
                      '区域：采样类别为每点可观测的组成（暗区、真空，或存在的相与支持膜的组合，重叠处自成一类）；按各自特征尺寸（类别在标签图上的平均弦长4×面积/周长，'
                      '换算为像素后平方，9至10⁶像素）平均后的计数噪声，'
                      '两类平均计数之差小于合成噪声的3倍时按完全连接合并，再按平均计数排序，不给物质名；'
                      '晶格条纹只按轴向照明的两束条件判断（激发强度×剩余结晶度×时间相干包络×照明会聚角与局部真实离焦的空间相干包络≥0.05、衍射束在光阑内、≥2倍像素），'
                      '不做高分辨像计算；目录晶格分辨率0.14nm需要倾斜（消色差）照明，本资产不模拟（轴向照明的信息极限约0.2–0.3nm）；'
                      'Thon环按CTF零点（含χ=0根；Cs 1.0mm、Cc 1.2mm、能量展宽2eV FWHM）给出时间×空间相干包络≥1%、光阑与Nyquist截止以内的频率，要求≥3nm非晶层与≥1e/Å²剂量；'
                      '选区光阑在MAG下限制视场（样品上直径=孔径/30）；图像剂量估计=视场最亮1%处的计数/(每电子8计数×像素面积)（有真空或孔时等于入射剂量，否则偏低）；'
                      '像不随倍率旋转（EM2020应用说明：×10–×1,500,000无像旋转），LOW MAG物镜关闭、MAG用物镜光阑决定衬度（同一说明）；'
                      '不建模相机增益参考、畸变、漂移、荷电与污染。'],
    'diffraction.acquire': ['衍射：选区（或照明区）内的晶体单元超过400个时给出连续环（运动学取向平均强度m(π/ξ)²t/(2g)并按1−exp(−ΣI)饱和），否则逐个晶体给出斑点（两束强度、'
                            '按倾转后的衍射矢量投影）；环宽由相机点扩展、尺寸展宽与照明会聚合成；非晶物质给出模板库的漫散射环（位置为工程假设，宽度取半径的25%）；'
                            '相对强度为积分强度相对最强特征；中心束饱和按中心斑计数判断；显示d值按显示相机长度换算，含±2%（1σ）的仪器标定误差（首次声明条件时随机，工程），'
                            '需要用已知标样校准；不计动力学多重衍射、菊池线与二次衍射。'],
    'montage.acquire': ['拼图帧用16×16采样点计算；帧间重叠不做拼接配准；拼图期间的剂量按样品台实际路径累计，帧图像按完成时刻的损伤状态计算。'],
    'camera.auto_exposure': ['按视场平均透过率与电流密度估算，不考虑饱和像素。'],
    'sim.step': ['唯一的宿主时间推进；逻辑、读数、真空、灯丝、剂量与采集完成在1s仪器周期上执行，样品台、样品杆、荧光屏与高压升降连续推进。'],
}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.jem1400flash_tem.limitations。'


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
    'plant.configure': ['plant.configured'], 'specimen.provide': ['specimen.bench_ids'], 'holder.load_grid': ['holder.grids', 'specimen.bench_ids'],
    'holder.unload_grid': ['holder.grids', 'specimen.bench_ids'], 'holder.insert_to_airlock': ['holder.position', 'holder.in_goniometer', 'airlock.state'],
    'airlock.set_pump': ['airlock.pump', 'airlock.state', 'airlock.pressure_indicated_pa', 'panel.airlock_lamp'],
    'holder.insert': ['holder.position', 'vacuum.column_pressure_indicated_pa', 'vacuum.state'], 'holder.withdraw': ['holder.position', 'airlock.state'],
    'holder.remove': ['holder.position', 'holder.in_goniometer'], 'power.set': ['power.enabled', 'system.state', 'vacuum.state'],
    'power.key_toggle': ['power.enabled', 'system.state'], 'error.acknowledge': ['system.error_code'], 'ht.set_voltage': ['ht.voltage_setpoint_v', 'ht.voltage_indicated_v'],
    'ht.set_on': ['ht.on', 'ht.voltage_indicated_v', 'gun.dark_current_indicated_a'], 'filament.set_on': ['filament.state', 'gun.beam_current_indicated_a'],
    'filament.set_bias': ['filament.bias', 'gun.beam_current_indicated_a'], 'gun.replace_filament': ['filament.state'], 'optics.set_mode': ['optics.mode'],
    'optics.toggle_diffraction': ['optics.mode'], 'optics.set_magnification': ['optics.magnification', 'display.mag_or_camera_length_text'],
    'optics.set_camera_length': ['optics.camera_length_m'], 'optics.set_magnification_step_fraction': ['optics.magnification_step_fraction'],
    'optics.set_brightness': ['optics.brightness', 'illumination.screen_current_density_a_m2'], 'optics.set_spot_size': ['optics.spot_size'],
    'optics.set_defocus': ['optics.defocus_display_m'], 'optics.standard_focus': ['optics.defocus_display_m'], 'optics.auto_focus': ['optics.defocus_display_m'],
    'aperture.set_condenser': ['aperture.condenser_inserted'], 'aperture.set_objective': ['aperture.objective_position'],
    'aperture.set_selected_area': ['aperture.selected_area_position'], 'screen.set': ['screen.up', 'screen.moving'], 'screen.toggle': ['screen.up', 'screen.moving'],
    'stage.move_xy': ['stage.x_indicated_m', 'stage.y_indicated_m', 'stage.moving'], 'stage.set_z': ['stage.z_indicated_m', 'stage.moving'],
    'stage.set_tilt': ['stage.tilt_indicated_rad', 'stage.moving'], 'stage.neutral': ['stage.x_indicated_m', 'stage.y_indicated_m', 'stage.z_indicated_m',
                                                                                  'stage.tilt_indicated_rad', 'stage.moving'],
    'image.acquire': ['acquisition.id', 'acquisition.progress', 'result.last_image_id'], 'image.result': ['result.last_image_id'],
    'diffraction.acquire': ['acquisition.id', 'acquisition.progress', 'result.last_pattern_id'], 'diffraction.result': ['result.last_pattern_id'],
    'montage.acquire': ['acquisition.id', 'acquisition.progress', 'result.last_montage_id'], 'montage.result': ['result.last_montage_id'],
    'camera.auto_exposure': [], 'records.list': [], 'events.read': ['system.error_code'], 'ledger.read': [], 'provenance.read': [], 'sim.step': ['clock.time_s'],
}
_WB, _PROD, _IMG, _FLASH, _PLUS, _GUIDE, _EM2020 = ('user_workbook', 'manufacturer_product_page', 'manufacturer_product_image', 'manufacturer_brochure_flash',
                                                    'related_brochure_plus', 'manufacturer_products_guide', 'manufacturer_em2020')
_EMORY, _VIB, _ILAB, _EMBL, _ETH = 'facility_emory', 'facility_vib', 'facility_ilab_airlock', 'facility_embl', 'facility_ethz'
_LIBS, _AUDIT, _PHYS, _SIM = 'tem_library', 'template_validation', 'physics_literature', 'simulation_assumption'
_DMG = ['egerton2004', 'egerton2013']
_STRUCT = ['cod_structures', 'xraydb', 'nist_srd126_tab1', 'nist_srd126_tab2', 'pubchem_density', 'formvar_refs']
CAP_SOURCES = {
    'plant.configure': [_PLUS, _SIM], 'specimen.provide': [_WB, _LIBS] + _STRUCT + _DMG + [_AUDIT, _SIM], 'holder.load_grid': [_PLUS, _FLASH, _SIM],
    'holder.unload_grid': [_PLUS, _SIM], 'holder.insert_to_airlock': [_EMORY, _VIB, _ILAB, _SIM], 'airlock.set_pump': [_EMORY, _VIB, _SIM],
    'holder.insert': [_EMORY, _VIB, _SIM], 'holder.withdraw': [_EMORY, _VIB, _SIM], 'holder.remove': [_EMORY, _VIB, _SIM], 'power.set': [_PLUS, _SIM],
    'power.key_toggle': [_PLUS, _SIM], 'error.acknowledge': [_PLUS, _SIM], 'ht.set_voltage': [_FLASH, _PROD, _PLUS, _SIM], 'ht.set_on': [_EMORY, _VIB, _PLUS, _SIM],
    'filament.set_on': [_VIB, _EMORY, _SIM], 'filament.set_bias': [_VIB, _SIM], 'gun.replace_filament': [_VIB, _SIM],
    'optics.set_mode': [_PLUS, _FLASH, _EM2020, _VIB], 'optics.toggle_diffraction': [_VIB, _EMORY], 'optics.set_magnification': [_FLASH, _PLUS, _EM2020],
    'optics.set_camera_length': [_SIM], 'optics.set_magnification_step_fraction': [_FLASH, _PLUS, _SIM], 'optics.set_brightness': [_VIB, _EMORY, _SIM],
    'optics.set_spot_size': [_VIB, _EMORY, _SIM], 'optics.set_defocus': [_VIB, _PHYS, _SIM], 'optics.standard_focus': [_VIB, _EMORY],
    'optics.auto_focus': [_FLASH, _SIM], 'aperture.set_condenser': [_VIB, _EMORY, _SIM], 'aperture.set_objective': [_VIB, _EMORY, _EM2020, _PHYS, _SIM],
    'aperture.set_selected_area': [_SIM], 'screen.set': [_VIB, _EMORY, _SIM], 'screen.toggle': [_VIB, _EMORY], 'stage.move_xy': [_VIB, _SIM],
    'stage.set_z': [_EMORY, _VIB, _SIM], 'stage.set_tilt': [_FLASH, _ETH, _PLUS, _SIM], 'stage.neutral': [_EMORY, _VIB],
    'image.acquire': [_FLASH, _PROD, _EM2020, _WB, _LIBS] + _STRUCT + _DMG + [_AUDIT, _PHYS, _SIM], 'image.result': [_FLASH],
    'diffraction.acquire': [_WB, _LIBS] + _STRUCT + [_AUDIT, _PHYS, _SIM], 'diffraction.result': [_WB],
    'montage.acquire': [_FLASH, _EMBL, _SIM], 'montage.result': [_FLASH], 'camera.auto_exposure': [_EMORY, _SIM],
    'records.list': [_SIM], 'events.read': [_SIM], 'ledger.read': ['runtime_code'], 'provenance.read': [_LIBS, _AUDIT], 'sim.step': ['runtime_code'],
    'stem.acquire': [_PLUS], 'eds.acquire': [_PLUS], 'tomography.acquire_tilt_series': [_PLUS], 'mds.set_mode': [_PLUS, _VIB], 'picture_overlay.load': [_FLASH],
    'holder.double_tilt': [_PLUS], 'holder.quartet': [_FLASH, _PLUS], 'optics.alignment': [_EMORY, _VIB], 'hardware.connect': [_PROD],
    'robot.handle_holder': [_SIM], 'accuracy.certification': [_FLASH, _WB]}
PARAM_ZH = {
    'room_k': '实验室室温（同平台JEM-1400Plus安装要求15–25°C）', 'relative_humidity': '相对湿度（同平台JEM-1400Plus安装要求≤60%）',
    'mains_voltage_v': '供电电压（单相AC 200V±10%，同平台JEM-1400Plus安装要求）',
    'cooling_water_m3_s': '冷却水流量（同平台JEM-1400Plus要求5L/min=8.33×10⁻⁵m³/s）',
    'noise_seed': '随机数种子（真空计与电流表噪声、灯丝寿命、相机长度标定误差、载网安装、自动聚焦误差与相机计数噪声各用独立的随机流）',
    'specimen_id': '载网样品ID', 'grid_mesh': '载网目数', 'grid_material': '载网材料（只记录，栅条一律不透）', 'support_film': '支持膜物质',
    'support_thickness_m': '支持膜厚度', 'hole_fraction': '支持膜孔洞面积分数', 'phases': '样品各相（模板库物质、形态、尺寸、厚度、覆盖率、结晶性、晶带轴、染色质量分数）',
}
LIMITATIONS = [
    '选定JEM-1400Flash HR（钨灯丝、10–120kV、HR物镜极靴、TMP真空、侧插测角台与换样气锁、Matataki Flash sCMOS底插相机2048×2048）；STEM、EDS、断层重构、MDS、Picture Overlay、'
    '双倾杆与四样品杆为选配未装；LaB6电子枪未装。仪器外形与质量取同平台JEM-1400Plus的厂家安装资料，不是Flash自身数据。',
    '真空为镜筒（20L，涡轮分子泵50L/s，放气1×10⁻⁶Pa·m³/s）、电子枪室（2L，经1×10⁻⁴m³/s流导与镜筒相通）与气锁（5cm³）三体积的线性模型，按仪器周期精确指数积分；'
    '体积、抽速、放气与就绪/跳闸阈值均为工程值；气体物料账逐步精确闭合。',
    '电子光学：相对论波长、Lenz–Wentzel屏蔽弹性散射截面（按收集半角）、两束动力学衍射（消光距离由模板库结构因子得到，Mott–Bethe换算的中性原子散射因子）、'
    '热漫散射分数1−exp(−2Bs²)、CTF、时间相干包络与照明会聚角的空间相干包络（Cs 1.0mm、Cc 1.2mm、ΔE 2eV FWHM为HR极靴与钨灯丝的典型值，工程）；没有做多片层（multislice）或Bloch波计算，'
    '不计吸收系数、菊池线与相干像的相位衬度细节；不建模非弹性散射（假设非弹性散射电子全部通过物镜光阑、不造成色差模糊）。',
    '辐照损伤：每个剂量事件记录照射圆沿样品台实际路径扫过的轨迹与电子注量（样品台各轴在拐点之间随时间线性变化，每个仪器周期在10个固定时刻与各轴到位/'
    '指令改变的拐点处对各轴重采样后换算为视场中心，按5%束半径与1%速度/cos(倾转)容差合成匀速直线扫迹与静止停留，与宿主步长无关；倾转时弯曲的轨迹在0.1s内按弦近似）'
    '（沿束方向穿过样品的电荷/面积，倾转不改变每个分子受到的注量）；有机膜、树脂与Formvar的质量损失按Egerton特征剂量（按1/β²换算到当前电压）与剩余质量分数（假设），'
    '醋酸铀染色只失去醋酸根与结晶水（铀与铀酰氧保留，假设）；碱金属卤化物、碳酸盐与磷灰石的结晶度按临界剂量（假设）衰减；损伤乘子按0.001量化；'
    '≤120kV不发生撞击位移；不建模温升、污染、荷电与冰。',
    '样品形貌为统计抽象（格点随机孔、岛状薄膜、条带切片、抖动格点上的旋转椭球颗粒（球形颗粒倾转时投影不变，扁平颗粒随倾转变薄变长）、晶粒Voronoi、单晶膜弯曲、染色起伏；'
    '18µm厚的载网栅条倾转时侧壁遮挡视场）；图像只输出按灰度可分性合并的区域统计（噪声下无法区分的类别合并为一个区域）、晶格条纹间距、Thon环频率与'
    '32×32缩略图，不输出全分辨率图像；衍射输出环/斑点/漫散射环的位置与相对积分强度；每个载网最多保留20000个剂量事件（同一位置的静止照射与同速共线的连续扫迹合并），超出时丢弃最早的事件。',
    '换样流程（中性位、关灯丝、插到气锁止点、PUMP、Evac Ready、两次转入、拉回、AIR放气、拔出）按设施规程；实体操作没有接触或机器人验证；'
    '样品杆插拔、倾转、荧光屏、气锁拨杆与光阑拨杆/拨盘由过程状态镜像到场景关节。',
    'agent只看到示值、设定、记录与事件；真实压力、灯丝寿命与磨损、发射与样品电流、照明直径、真实离焦、样品高度、真实相机长度、剂量与样品真实组成只对evaluator可见'
    '（记录不给物质名，只给模板库路径；图像剂量为相机计数的估计值，倾转为0.1°读数）；'
    '记录按调用者隔离（evaluator或宿主创建的记录对所有agent可读，Matterix宿主以evaluator身份调用运行层）。',
    '没有真实硬件、JEOL固件、TEM Center或相机软件，没有厂家验收数据；TEM Center中的对中、消像散、MDS与自动化功能未实现。',
]


def export_jeol_jem1400flash_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'JeolJem1400flashSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        last = {'image.acquire': 'result.last_image_id', 'diffraction.acquire': 'result.last_pattern_id', 'montage.acquire': 'result.last_montage_id'}.get(k)
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.jeol_jem1400flash.JeolJem1400flashSession.invoke',
                                              invocation=f'session.invoke(instance_id="jem1400flash_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_acq_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['acquisition.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='JeolJem1400flashRuntime.invoke / JeolJem1400flashSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate',
                                     description_zh='设定、目标、状态标志或立即改变的派生量；压力、位置、灯丝、剂量与采集进度只随step推进') for s in v['effects']],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion='采集结束（完成、失败或取消）：曝光+读出（拼图为全部帧）到达后在仪器周期上确定记录状态' if async_ else
                                                     '原子设定、配置或查询，实际物理状态由step推进',
                                completion_state_ids=['acquisition.id', 'acquisition.progress', last] if async_ else [],
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('取消或失败（束流关闭、灯丝烧断、高压跳闸、冷却水不足、断电）时记录以cancelled/failed结束，不产生图像/衍射/拼图结果；'
                                                    '已照射的剂量与电子电荷仍计入样品与账目，拼图时样品台回到起点（断电时停在断电位置）。') if async_ else
                                                   ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH[e]), side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH[k]) for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in v['effects'])]      # a slot set at once may also evolve on the step
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'kv_min_v': (KV_RANGE_V[0], 'V', '最低加速电压10kV', _FLASH), 'kv_max_v': (KV_RANGE_V[1], 'V', '最高加速电压120kV', _FLASH),
            'kv_step_v': (KV_STEP_V, 'V', '加速电压最小步进33V（同平台JEM-1400Plus）', _PLUS),
            'lattice_resolution_m': (LATTICE_RESOLUTION_M, 'm', 'HR透射分辨率（晶格像）0.14nm', _FLASH), 'mag_min': (10.0, '1', '最低放大倍数×10', _FLASH),
            'mag_max': (MAG_MAX, '1', 'HR最高放大倍数×1,500,000', _FLASH), 'low_mag_max': (1000.0, '1', 'LOW MAG上限×1,000（同平台）', _PLUS),
            'mag_mode_min': (200.0, '1', 'MAG模式下限×200（同平台）', _PLUS), 'tilt_high_tilt_rad': (TILT_LIMITS_RAD['high_tilt'], 'rad', '选配高倾转杆EM-11230HTH ±70°', _FLASH),
            'tilt_quick_change_rad': (TILT_LIMITS_RAD['quick_change'], 'rad', '标准快换杆在HR极靴下±20°（ETH ScopeM设施）', _ETH),
            'camera_pixels': (CAMERA_PIXELS, '1', 'Matataki Flash 2048×2048像素', _FLASH), 'camera_pixel_m': (CAMERA_PIXEL_M, 'm', '有效像素21.7µm', _FLASH),
            'camera_read_noise_e': (CAMERA_READ_NOISE_E, '1', '读出噪声0.8电子', _FLASH), 'camera_fps': (CAMERA_FPS, '1/s', '帧率30fps（不合并）', _FLASH),
            'mains_nominal_v': (MAINS_NOMINAL_V, 'V', '单相AC 200V、5kVA（同平台安装要求）', _PLUS),
            'cooling_flow_min_m3_s': (COOLING_FLOW_MIN_M3_S, 'm^3/s', '冷却水5L/min（8.33×10⁻⁵m³/s，同平台安装要求）', _PLUS),
            'room_min_k': (ROOM_RANGE_K[0], 'K', '室温下限15°C（同平台）', _PLUS), 'room_max_k': (ROOM_RANGE_K[1], 'K', '室温上限25°C（同平台）', _PLUS),
            'dark_current_resistance_ohm': (DARK_CURRENT_R_OHM, 'ohm', '暗电流等效分压电阻：80kV时44µA、120kV时66µA（Emory设施）', _EMORY),
            'bias_typical': (40.0, '1', '常用起始偏压40，束流读数比暗电流高5–7µA（VIB设施）', _VIB)}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=[src], uncertainty=None)
    for name, (val, unit, meaning) in {
            'v_column_m3': (V_COLUMN_M3, 'm^3', '镜筒抽气体积'), 's_tmp_m3_s': (S_TMP_M3_S, 'm^3/s', '涡轮分子泵有效抽速'), 'tmp_spinup_s': (TMP_SPINUP_S, 's', '涡轮泵起转时间'),
            'q_column_pa_m3_s': (Q_COLUMN_PA_M3_S, 'Pa*m^3/s', '镜筒放气量'), 'v_gun_m3': (V_GUN_M3, 'm^3', '电子枪室体积'), 'c_gun_m3_s': (C_GUN_M3_S, 'm^3/s', '枪室到镜筒流导'),
            'col_ready_pa': (COL_READY_PA, 'Pa', '镜筒就绪压力'), 'gun_ready_pa': (GUN_READY_PA, 'Pa', '电子枪就绪压力'), 'gun_trip_pa': (GUN_TRIP_PA, 'Pa', '高压跳闸压力'),
            'v_airlock_m3': (V_AIRLOCK_M3, 'm^3', '换样气锁体积'), 'airlock_tau_s': (AIRLOCK_TAU_S, 's', '气锁预抽时间常数'), 'airlock_ready_pa': (AIRLOCK_READY_PA, 'Pa', 'Evac Ready阈值'),
            'q_holder_pa_m3_s': (Q_HOLDER_PA_M3_S, 'Pa*m^3/s', '新插入样品杆的放气量（60s衰减）'), 'emission_bias0_a': (EMISSION_BIAS0_A, 'A', '偏压0时的饱和发射'),
            'bias_scale': (BIAS_SCALE, '1', '发射随偏压的指数尺度'), 'heat_s': (HEAT_S, 's', '灯丝加热到饱和的时间'), 'filament_life_s': (FILAMENT_LIFE_S, 's', '钨灯丝平均寿命150h'),
            'gun_transmission': (GUN_TRANSMISSION, '1', '束斑1、聚光镜光阑插入时样品电流/发射电流'), 'illum_d_min_m': (ILLUM_D_MIN_M, 'm', '束斑1交叉点直径'),
            'illum_d_max_m': (ILLUM_D_MAX_M, 'm', '最大展开照明直径'), 'cs_m': (CS_M, 'm', 'HR物镜球差系数'), 'cc_m': (CC_M, 'm', '物镜色差系数'),
            'energy_spread_j': (ENERGY_SPREAD_J, 'J', '钨灯丝能量展宽（2eV FWHM；焦距展宽取rms=FWHM/2.355）'), 'obj_focal_m': (OBJ_FOCAL_M, 'm', '物镜焦距（光阑孔径→收集半角）'),
            'sa_magnification': (SA_MAGNIFICATION, '1', '选区光阑平面的物镜放大倍数'), 'adu_per_electron': (ADU_PER_ELECTRON, '1', '相机每个入射电子的计数'),
            'camera_dqe': (CAMERA_DQE, '1', '相机探测量子效率'), 'camera_length_error_sd': (CAMERA_LENGTH_ERROR_SD, '1', '相机长度显示值与实际值的标定误差（1σ）'),
            'grid_z_sd_m': (GRID_Z_SD_M, 'm', '载网安装高度偏差（1σ）'), 'stage_xy_speed_m_s': (STAGE_XY_SPEED, 'm/s', '样品台X/Y速度'),
            'holder_speed_m_s': (HOLDER_SPEED_M_S, 'm/s', '样品杆推入/拉出速度'), 'ht_ramp_v_s': (HT_RAMP_V_S, 'V/s', '高压升降速率'),
            'software_boot_s': (SOFTWARE_BOOT_S, 's', 'TEM Center加载时间'), 'hole_d_m': (HOLE_D_M, 'm', '支持膜孔洞平均直径'), 'bend_rad': (BEND_RAD, 'rad', '单晶膜弯曲幅度'),
            'dose_ref_v': (DOSE_REF_V, 'V', '剂量换算参考电压（损伤截面∝1/β²）'),
            'meter_dark_noise_a': (METER_DARK_NOISE_A, 'A', '束流表暗电流读数波动（1σ）'), 'meter_beam_noise_a': (METER_BEAM_NOISE_A, 'A', '束流读数中发射部分的附加波动（1σ）')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['gun_chamber', 'upper_column', 'condenser_lens', 'condenser_mini_lens', 'objective_lens', 'specimen_chamber', 'intermediate_lens', 'projector_lens',
                       'camera_chamber', 'viewing_screen', 'scmos_camera', 'goniometer', 'specimen_holder', 'holder_tilt', 'holder_tip', 'airlock_box', 'airlock_lever',
                       'cl_aperture_drive', 'ol_aperture_drive', 'sa_aperture_drive', 'turbo_molecular_pump', 'rotary_pump', 'power_supply_console', 'rear_console'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='JEM-1400Flash HR透射电镜：真空与换样气锁、钨灯丝电子枪与高压、照明、明场成像（散射与衍射衬度、相机计数）、选区衍射、辐照损伤、样品台与样品杆'
                   '（部分规格与安装常数为同平台JEM-1400Plus数据，见各参数的来源）',
        equations=['lambda = h / sqrt(2 m e V (1 + e V / (2 m c^2))) ; gamma = 1 + e V / (m c^2)',
                   'sigma_el(>alpha) = gamma^2 Z^2 lambda^4 / (4 pi^3 a0^2) / (alpha^2 + theta0^2), theta0 = lambda Z^(1/3) / (2 pi a0) (Lenz-Wentzel)',
                   'T_amorphous = exp(- sum_i n_i sigma_el,i(>alpha) t / cos(tilt)) ; crystals: exp(- f_TDS mu_el(>alpha) t) (1 - D_all + D_in), f_TDS = 1 - exp(-2 B s^2)',
                   'I_g = (pi/xi_g)^2 sin^2(pi t s_eff) / (pi s_eff)^2, s_eff = sqrt(s^2 + 1/xi_g^2), xi_g = pi V_c / (lambda gamma |F_g|), s_g = -(u.g) - lambda |g|^2 / 2 ; '
                   'D = 1 - exp(-sum I_g)',
                   'rings: <I_hkl> = m (pi/xi)^2 t / (2 |g|) ; R = L tan(2 asin(lambda / 2d)) ; d_display = lambda / (2 sin(atan(R / L_display) / 2))',
                   'chi(k) = pi lambda dz k^2 - pi/2 Cs lambda^3 k^4 (dz > 0 underfocus) ; Thon zeros sin(chi) = 0 (n >= 0) ; '
                   'E_t = exp(-(pi lambda Delta)^2 k^4 / 2), Delta = Cc (dE_FWHM / 2.355) / (e V) ; E_s = exp(-(pi conv)^2 (dz k - Cs lambda^2 k^3)^2) ; '
                   'fringes (axial illumination): I_g fc E_t(1/d) E_s(1/d, local dz) >= 0.05',
                   'spheroid particle (radius a, half-thickness q a): outline semi-axes a, a sqrt(cos^2 t + q^2 sin^2 t), central path 2 q a / sqrt(q^2 sin^2 t + cos^2 t) ; '
                   'grid bars widened by 18 um tan(t) along the tilt direction',
                   'fluence of a swept footprint: F x |[s - w, s + w] & [0, L]| / L, w = sqrt(r^2 - h^2) (beam-normal frame) ; damage from the fluence',
                   'counts = J/e p^2 tau T x 8 ADU/e (<= 65535) ; SNR = N / sqrt(N/DQE + (0.8 b)^2) ; screen J_s = J T_view / M^2',
                   'dz_true = dz_display - h / cos(tilt), view centre v = y + h tan(tilt), h = grid mount offset + z',
                   'I_em = 20 uA exp(-bias/33) heat ; I_dark = V_HT / 1.818 GOhm ; meter: dark = I_dark + 0.1 uA z1, beam = dark + I_em + 0.05 uA z2 ; I_spec = I_em 3e-4 f_spot (x3 without the condenser aperture) ; J = 4 I_spec / (pi d_ill^2)',
                   'mass loss m = r + (1 - r) exp(-D/De), crystallinity exp(-D/Dc) ; De, Dc scaled with beta^2 (Egerton 2004), D the 100 kV-equivalent dose',
                   'column/gun: V dp/dt = Q - S p + C (p_other - p) (exact 2 x 2 exponential per cycle) ; airlock: p -> p_ult or P_atm with tau 4 s / 1 s'],
        parameters=params, source_ids=[_SIM, _LIBS, _FLASH, _PLUS, _EMORY, _VIB, _ETH, _PHYS] + _DMG + _STRUCT, limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_jem1400flash_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'jem1400flash_tem': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'specimen.provide': (SPECIMEN, SPECIMEN_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'jem1400flash_tem': mechanism})
