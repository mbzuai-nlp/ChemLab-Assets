"""CHEM-156 Bruker X4 POSEIDON benchtop micro-CT / 3D X-ray microscope, reflection-source configuration with the 7 MP flat-panel detector
(essential configuration: standard rotation stage, single detector mount, fixed scan geometry, automated 7-position filter changer):
runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * power and safety: mains switch, boot with homing of the stage and the filter changer, motorized lead-glass door with the X-ray interlock
    and the safety light curtain, emergency stop (latched until released and acknowledged), multi-function status light;
  * X-ray source (tungsten reflection target, sealed): kV / current set points limited to 100 kV and 20 W, ramp and a warm-up program
    after each power-up, focal spot growing with the target power (< 5 um at 4 W), Kramers thick-target bremsstrahlung with the tungsten
    self-absorption of a reflection target, W K and L lines, beryllium exit window, 7-position filter changer and air path (NIST SRD 126
    attenuation coefficients from the template library);
  * geometry: cone beam, fixed source-detector distance, the sample on a rotation stage carried along the beam axis (source-object distance
    = geometric magnification) and lifted vertically; exact constant-speed motion with event cuts; collision guard against the source window,
    the filter changer and the detector cover; hidden rotation-axis offset (geometry calibration and post-alignment);
  * flat-panel detector (CsI:Tl scintillator on a CMOS sensor, 2800 x 2400 pixels): absorbed energy per photon from mu and mu_en, conversion
    gain, compound-Poisson quantum noise, read noise, dark offset and dark current, 16-bit saturation, binning, exposure, frame averaging,
    fixed-pattern gain non-uniformity corrected by the dark / bright field calibration (stale after changing kV, current, filter, binning or
    exposure), optional random detector movement against ring artifacts, injected defective columns;
  * samples: analytic phantoms (cylinder with horizontal layers of library materials or volume mixtures, spherical pores and inclusions,
    planar cracks, optional container tube and mounting pin, calibration phantom with steel balls, tungsten edge for radiographic resolution);
    projections by exact ray tracing (chord lengths per material) and polychromatic Beer-Lambert transmission in energy groups (beam
    hardening follows), focal-spot penumbra and scintillator blur; exact truth (porosity, pores, cracks) for the evaluator;
  * reconstruction: Feldkamp (FDK) cone-beam filtered back-projection (Ram-Lak / Shepp-Logan / Hann), Parker weights for 180 deg + fan scans,
    beam-hardening linearisation for a named material, ring correction, post-alignment (manual or automatic), Gaussian smoothing, volume of
    interest; analysis: porosity (total, open, closed, pore sizes), crack detection (planar components), attenuation statistics, slices;
  * fault injection (evaluator): tube arc, defective detector columns, rotation stage stall, door interlock sensor, light curtain blocked;
  * ledgers: electrical energy into the tube, X-ray energy emitted and where it goes (target self-absorption, window, filter, air, sample
    absorbed and scattered, detector absorbed and passed, shielding), dose to the sample, projections, frames, stage travel.
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
SLUG = 'bruker_x4poseidon'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-156'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t3_analytical' / SLUG
LIBRARY_PATH = ASSET / 'xray_attenuation_library.json'
LIBRARY_REL = str(LIBRARY_PATH.relative_to(ROOT))

# ---------------------------------------------------------------- documented (Bruker X4 POSEIDON product page, brochure, configurator)
XRAY_KV_MAX_V = 100.0e3                          # reflection source: up to 100 kV
XRAY_POWER_MAX_W = 20.0                          # reflection source: 20 W
SPOT_AT_4W_M = 5.0e-6                            # reflection source: < 5 um spot at 4 W
DETECTOR_PIXELS = (2800, 2400)                   # 7 MP flat panel, 2800 x 2400 pixels
SAMPLE_MAX_DIAMETER_M = 0.110                    # object size up to 110 mm in diameter
SAMPLE_MAX_HEIGHT_M = 0.300                      # object size up to 300 mm in height
FILTER_POSITIONS = 7                             # automated 7-position filter changer (standard with both sources; configurator)
DETAIL_DETECTABILITY_M = 5.0e-6                  # reflection source: detail detectability down to 5 um (configurator)
BODY_DIMENSIONS_M = (1.250, 0.604, 0.690)        # W 1250 x D 604 x H 690 mm (specification table)
MASS_KG = 350.0                                  # weight 350 kg
MAINS_V = (100.0, 240.0)                         # 100 - 240 V AC, 50 - 60 Hz, 3 A max
MAINS_A_MAX = 3.0

# ---------------------------------------------------------------- engineering values (not published; used for the process model)
XRAY_KV_MIN_V = 20.0e3
XRAY_CURRENT_MAX_A = 0.5e-3                      # tube current limit besides the 20 W power limit
KV_RAMP_V_S = 10.0e3                             # high-voltage ramp
CURRENT_RAMP_A_S = 100.0e-6                      # emission-current ramp
WARMUP_S = 180.0                                 # conditioning program after every power-up of the tube (first enable after a boot)
SPOT_GROWTH_EXP = 1.0                            # focal spot = 5 um x max(1, P / 4 W)^exp (constant power density above 4 W)
TAKEOFF_RAD = math.radians(20.0)                 # take-off angle of the reflection target
BE_WINDOW_M = 0.25e-3                            # beryllium exit window
KRAMERS_K_PER_V = 1.1e-9                         # bremsstrahlung efficiency eta = K Z V (thick target)
K_LINE_FRACTION_100KV = 0.08                     # W K lines: fraction of the emitted X-ray energy at 100 kV, scaling (U - 1)^1.63
L_LINE_FRACTION_50KV = 0.04                      # W L lines: fraction at 50 kV, scaling (U_L - 1)^1.63
W_K_LINES_KEV = ((59.32, 0.46), (57.98, 0.27), (67.24, 0.19), (69.10, 0.08))     # energy (keV), relative intensity
W_L_LINES_KEV = ((8.40, 0.55), (9.67, 0.35), (11.29, 0.10))
W_K_EDGE_KEV, W_L3_EDGE_KEV = 69.525, 10.207
CONE_HALF_ANGLE_RAD = math.radians(20.0)         # emission cone of the source used for the shielding share of the ledger
SDD_M = 0.450                                    # source-detector distance (fixed scan geometry)
SOD_RANGE_M = (0.015, 0.395)                     # source-object distance (sample carriage on the beam rail)
CHUCK_Z_RANGE_M = (0.160, 0.399)                 # chuck top height above the bench (sample lift; limited by the rotation stage below it)
ENCLOSURE_X_M = 0.310                            # enclosure centre in the asset frame (the workstation stands on its left)
SOURCE_XYZ_M = (ENCLOSURE_X_M - 0.180, 0.020, 0.400)   # focal spot in the asset frame (beam along +X at this height)
WINDOW_OFFSET_M = 0.008                          # exit window face from the focal spot
FILTER_OFFSET_M = 0.010                          # filter plane from the focal spot
FILTER_CLEAR_M = 0.014                           # front face of the filter changer from the focal spot (collision guard)
DETECTOR_COVER_M = 0.012                         # detector entrance cover in front of the active area (collision guard)
COLLISION_MARGIN_M = 0.002
SOURCE_KEEPOUT_DZ = (-0.050, 0.050)              # source head block (the filter slide moves laterally at beam height): nothing closer than FILTER_CLEAR
DETECTOR_KEEPOUT_DZ = (-0.090, 0.090)            # detector housing below / above the beam
CHUCK_R_M, CHUCK_H_M = 0.012, 0.015              # brass chuck below the chuck top
STAGE_R_M, STAGE_H_M = 0.0566, 0.050             # 80 x 80 mm rotation-stage housing and platter below the chuck (circumscribed radius, conservative)
CHAMBER_CEILING_M = 0.640                        # inner ceiling height of the enclosure above the bench
PIXEL_PITCH_M = 49.5e-6                          # flat-panel pixel pitch (2800 x 49.5 um = 138.6 mm)
CSI_THICKNESS_M = 0.20e-3                        # CsI:Tl scintillator
ADU_PER_KEV = 0.8                                # conversion gain (energy deposited in the scintillator -> ADU)
READ_NOISE_ADU = 4.0
DARK_OFFSET_ADU = 100.0
DARK_CURRENT_ADU_S = 10.0
FULL_SCALE_ADU = 65535.0
DETECTOR_PSF_PX = 0.8                            # scintillator light spread, sigma in detector pixels
EXPOSURE_RANGE_S = (0.02, 10.0)
AVERAGING_RANGE = (1, 16)
BINNINGS = (1, 2, 4)
FRAME_OVERHEAD_S = 0.03                          # read-out per frame
STEP_OVERHEAD_S = 0.20                           # rotation step and settle per projection (step and shoot)
ROTATION_SPEED_RAD_S = math.radians(60.0)
CARRIAGE_SPEED_M_S = 0.020
LIFT_SPEED_M_S = 0.010
DOOR_TRAVEL_M = 0.400                            # the door slides to the left behind the front panel (direction: photo estimate)
DOOR_SPEED_M_S = 0.100
FILTER_PITCH_M = 0.012                           # lateral filter slide pitch between positions (slide moves along -Y at beam height)
FILTER_S_PER_STEP = 0.5
BOOT_S = 60.0
FLAT_FIELD_FRAMES = 20                           # bright frames (and as many dark frames) of the calibration
AXIS_OFFSET_SD_M = 40.0e-6                       # hidden lateral offset of the rotation axis (1 sigma, at the axis)
RECON_RATE_VOXEL_PROJ_S = 5.0e10                 # reconstruction throughput of the workstation (voxel x projection per s)
RECON_OVERHEAD_S = 5.0
SIM_DETECTOR = (175, 150)                        # simulated detector grid: 16 x 16 native pixels per simulated pixel
SIM_MAX_PROJECTIONS = 200                        # simulated projections (evenly spaced subset of the nominal ones, dose-equivalent)
ENERGY_GROUPS = 24
RECON_GRIDS = (48, 64, 80, 96, 128)
MAX_RECORDS = 60
MAX_EVENTS = 400
MAX_SAMPLES = 12
MAX_PORES = 400
MAX_CRACKS = 6
MAX_INCLUSIONS = 12
FILTERS = [  # position: id, label, layers (library material id, thickness m)
    dict(position=0, id='none', label='无滤片', layers=[]),
    dict(position=1, id='al_0p25', label='Al 0.25 mm', layers=[('al', 0.25e-3)]),
    dict(position=2, id='al_0p5', label='Al 0.5 mm', layers=[('al', 0.5e-3)]),
    dict(position=3, id='al_1p0', label='Al 1.0 mm', layers=[('al', 1.0e-3)]),
    dict(position=4, id='cu_0p1', label='Cu 0.1 mm', layers=[('cu', 0.1e-3)]),
    dict(position=5, id='cu_0p25', label='Cu 0.25 mm', layers=[('cu', 0.25e-3)]),
    dict(position=6, id='al_1p0_cu_0p1', label='Al 1.0 mm + Cu 0.1 mm', layers=[('al', 1.0e-3), ('cu', 0.1e-3)]),
]
LIBRARY_MATERIALS_USED = ['w', 'be', 'al', 'cu', 'nist_air', 'nist_cesium', 'fe', 'nist_pmma']
KEV = 1.602176634e-16                            # J per keV


def filter_of(position):
    return FILTERS[position]


# ---------------------------------------------------------------- template library (NIST SRD 126 mass attenuation coefficients)
_LIB = [None]


def library():
    if _LIB[0] is None:
        raw = LIBRARY_PATH.read_bytes()
        d = json.loads(raw)
        d['subs'] = {s['substance_id']: s for s in d['substances']}
        d['sha256'] = hashlib.sha256(raw).hexdigest()
        _LIB[0] = d
    return _LIB[0]


def substance(sid):
    s = library()['subs'].get(sid)
    if s is None:
        raise ActionRejected('UNKNOWN_SUBSTANCE')
    return s


@functools.lru_cache(maxsize=None)
def _segments(sid):
    """Edge-free segments of a tabulated material: (log E, log mu/rho, log mu_en/rho) per segment (energies in keV, coefficients in m2/kg)."""
    s = substance(sid)
    e = np.array(s['energy_MeV']) * 1e3
    mu = np.array(s['mu_rho_cm2_g']) * 0.1
    men = np.array(s['mu_en_rho_cm2_g']) * 0.1
    cut = np.where(np.diff(e) <= 0)[0] + 1
    return [(np.log(e[i]), np.log(mu[i]), np.log(men[i]), e[i[0]], e[i[-1]]) for i in np.split(np.arange(len(e)), cut)]


def mass_coefficients(sid, e_kev):
    """mu/rho and mu_en/rho (m2/kg) at the photon energies e_kev: log-log interpolation inside the edge-free segment containing each energy
    (an energy exactly at an edge takes the value above the edge)."""
    e_kev = np.atleast_1d(np.asarray(e_kev, float))
    mu = np.empty_like(e_kev)
    men = np.empty_like(e_kev)
    segs = _segments(sid)
    for k, (le, lm, ln, lo, hi) in enumerate(segs):
        last = k == len(segs) - 1
        m = (e_kev >= lo) & ((e_kev < segs[k + 1][3]) if not last else np.ones_like(e_kev, bool))
        if k == 0:
            m |= e_kev < lo
        if m.any():
            x = np.log(np.clip(e_kev[m], 1e-3, None))
            mu[m] = np.exp(np.interp(x, le, lm))
            men[m] = np.exp(np.interp(x, le, ln))
    return mu, men


def density(sid):
    return float(substance(sid)['density_kg_m3'])


def linear_mu(sid, e_kev, rho=None):
    """Linear attenuation and energy-absorption coefficients (1/m) of a library material at its tabulated density (or rho)."""
    mu, men = mass_coefficients(sid, e_kev)
    r = density(sid) if rho is None else rho
    return mu * r, men * r


def mixture_mu(parts, e_kev):
    """Linear coefficients of a volume mixture [(material id, volume fraction)], each constituent at its tabulated density."""
    mu = np.zeros(np.atleast_1d(e_kev).shape)
    men = np.zeros_like(mu)
    for sid, f in parts:
        a, b = linear_mu(sid, e_kev)
        mu += f * a
        men += f * b
    return mu, men


def mixture_density(parts):
    return sum(f * density(sid) for sid, f in parts)


# ---------------------------------------------------------------- source spectrum, filters, detector response
E_GRID_KEV = np.arange(1.0, 100.5, 0.5)          # fine grid of the spectrum model


def focal_spot_m(power_w):
    return SPOT_AT_4W_M * max(1.0, power_w / 4.0) ** SPOT_GROWTH_EXP


def kanaya_range_m(e0_kev):
    """Kanaya-Okayama electron range in tungsten (A = 183.84, Z = 74, rho = 19.3 g/cm3)."""
    return 0.0276 * 183.84 * e0_kev ** 1.67 / (74 ** 0.889 * 19.3) * 1e-6


@functools.lru_cache(maxsize=256)
def emitted_spectrum(kv_v):
    """Photon energy spectrum emitted per electron per steradian at the anode, after the tungsten self-absorption of the reflection target,
    before the window: (E keV grid, photons / (sr keV electron)).  Kramers intensity I(E) ~ Z (E0 - E) normalised so that the energy emitted
    into 4 pi equals eta V with eta = K Z V (V in volts); characteristic W K and L lines added as fractions of the emitted energy."""
    e0 = kv_v / 1e3
    e = E_GRID_KEV[E_GRID_KEV < e0]
    de = 0.5
    intensity = np.clip(e0 - e, 0.0, None)                      # energy per keV (arbitrary units)
    eta = KRAMERS_K_PER_V * 74 * kv_v
    energy_per_electron_j = eta * kv_v * 1.602176634e-19         # emitted X-ray energy per electron (J)
    brems_frac = 1.0
    lines = []
    if e0 > W_K_EDGE_KEV:
        fk = K_LINE_FRACTION_100KV * ((e0 / W_K_EDGE_KEV - 1) / (100.0 / W_K_EDGE_KEV - 1)) ** 1.63
        lines += [(E, fk * w) for E, w in W_K_LINES_KEV]
        brems_frac -= fk
    if e0 > W_L3_EDGE_KEV:
        fl = L_LINE_FRACTION_50KV * ((e0 / W_L3_EDGE_KEV - 1) / (50.0 / W_L3_EDGE_KEV - 1)) ** 1.63
        fl = min(fl, 0.08)
        inc = [(E, fl * w) for E, w in W_L_LINES_KEV if E < e0]         # a line above the tube voltage is not excited
        lines += inc
        brems_frac -= sum(f for _, f in inc)
    photons = np.zeros_like(e)
    ib = intensity * de
    photons += ib / ib.sum() * brems_frac * energy_per_electron_j / (e * KEV)            # bremsstrahlung photons per bin
    for E, f in lines:
        j = int(np.argmin(np.abs(e - E)))
        photons[j] += f * energy_per_electron_j / (e[j] * KEV)                            # line in its 0.5 keV bin: energy kept exact
    photons /= (4 * math.pi) * de                                                        # per steradian and keV
    depth = kanaya_range_m(e0) / 3.0 / math.sin(TAKEOFF_RAD)                            # mean emission depth along the exit path
    mu_w, _ = linear_mu('w', e)
    return e, photons * np.exp(-mu_w * depth)


def unabsorbed_spectrum(kv_v):
    """Same spectrum without the target self-absorption (for the energy ledger)."""
    e0 = kv_v / 1e3
    e, ph = emitted_spectrum(kv_v)
    depth = kanaya_range_m(e0) / 3.0 / math.sin(TAKEOFF_RAD)
    mu_w, _ = linear_mu('w', e)
    return e, ph * np.exp(mu_w * depth)


@functools.lru_cache(maxsize=512)
def beam_spectrum(kv_v, filter_position):
    """Photons / (sr keV electron) behind the beryllium window and the selected filter, before the air path."""
    e, ph = emitted_spectrum(kv_v)
    mu_be, _ = linear_mu('be', e)
    t = np.exp(-mu_be * BE_WINDOW_M)
    for sid, th in filter_of(filter_position)['layers']:
        mu, _ = linear_mu(sid, e)
        t = t * np.exp(-mu * th)
    return e, ph * t


def air_transmission(e_kev, length_m):
    mu, _ = linear_mu('nist_air', e_kev)
    return np.exp(-mu * length_m)


@functools.lru_cache(maxsize=None)
def detector_response(e_key):
    """Per incident photon of energy E: probability of interaction in the CsI layer and the mean energy deposited (keV) = E (1 - e^-mu t)
    mu_en / mu (the local-deposition share of the interaction; fluorescence and Compton escape leave)."""
    e = np.array(e_key)
    mu, men = linear_mu('nist_cesium', e)
    p = 1.0 - np.exp(-mu * CSI_THICKNESS_M)
    return p, e * p * men / mu


def energy_groups(e, w, edges_kev=(), n=ENERGY_GROUPS):
    """Group the fine spectrum into n groups of equal weight (weight w = detected energy per bin), forcing boundaries at the given edges;
    returns (group index per bin, group count)."""
    c = np.cumsum(w)
    c = c / c[-1] if c[-1] > 0 else np.linspace(0, 1, len(w))
    b = set(np.searchsorted(c, np.linspace(0, 1, n + 1)[1:-1]).tolist())
    for ed in edges_kev:
        j = int(np.searchsorted(e, ed))
        if 0 < j < len(e):
            b.add(j)
    b = sorted(x for x in b if 0 < x < len(e))
    idx = np.zeros(len(e), int)
    for k, j in enumerate(b):
        idx[j:] = k + 1
    return idx, len(b) + 1


# ---------------------------------------------------------------- sample phantoms (sample frame: origin at the bottom centre of the sample
# body on its own axis, +Z up; a mounting pin stands below it on the chuck)
SAMPLE_KINDS = ['cylinder', 'calibration_phantom', 'edge_phantom']
CAL_BALLS = 8                                    # steel balls of the geometry calibration phantom (helix in a PMMA cylinder)
CAL_BALL_RADIUS_M = 0.5e-3
EDGE_THICKNESS_M = 0.2e-3                        # tungsten plate of the edge phantom (beam along its thickness at angle 0)


def _layer_bounds(s):
    z = [0.0]
    for L in s['layers']:
        z.append(z[-1] + L['thickness_m'])
    return z


def _materials(s):
    """All library materials of a sample (matrix constituents, inclusions, container, pin, phantom materials)."""
    ids = []
    if s['kind'] == 'cylinder':
        for L in s['layers']:
            ids += [m['substance_id'] for m in L['materials']]
        ids += [i['substance_id'] for i in s['inclusions']]
    elif s['kind'] == 'calibration_phantom':
        ids += ['nist_pmma', 'fe']
    else:
        ids += ['w']
    if s['container']:
        ids.append(s['container']['substance_id'])
    if s['pin']:
        ids.append(s['pin']['substance_id'])
    return list(dict.fromkeys(ids))


def generate_pores(s):
    """Deterministic non-overlapping spherical pores (log-normal radii) fully inside the given layer of a cylinder sample; raises
    INVALID_SAMPLE when they cannot be placed."""
    p = s['pores']
    if s['kind'] != 'cylinder' or p['count'] == 0:
        return []
    zb = _layer_bounds(s)
    lay = p['layer']
    if lay >= len(s['layers']):
        raise ActionRejected('INVALID_SAMPLE')
    z0, z1 = zb[lay], zb[lay + 1]
    R = s['diameter_m'] / 2
    rng = np.random.default_rng(int(hashlib.sha256(f"{s['sample_id']}|{p['seed']}".encode()).hexdigest()[:12], 16))
    radii = np.sort(p['median_radius_m'] * np.exp(p['sigma_ln'] * rng.standard_normal(p['count'])))[::-1]
    out = []
    for r in radii:
        if 2 * r >= min(R, (z1 - z0) / 2):
            raise ActionRejected('INVALID_SAMPLE')
        for _ in range(4000):
            rho = (R - r - 1e-6) * math.sqrt(rng.random())
            ph = 2 * math.pi * rng.random()
            c = np.array([rho * math.cos(ph), rho * math.sin(ph), z0 + r + 1e-6 + (z1 - z0 - 2 * r - 2e-6) * rng.random()])
            if all(np.linalg.norm(c - q[0]) > r + q[1] + 1e-6 for q in out) and not _hits_inclusion(s, c, r):
                out.append((c, float(r)))
                break
        else:
            raise ActionRejected('INVALID_SAMPLE')              # too dense for non-overlapping pores
    return [dict(center_m=[float(v) for v in c], radius_m=r) for c, r in out]


def _hits_inclusion(s, c, r):
    return any(np.linalg.norm(c - np.array(i['center_m'])) < r + i['radius_m'] + 1e-6 for i in s['inclusions'])


def calibration_balls(s):
    """Steel balls of the geometry phantom on a helix (radius 0.35 D, one turn over 0.8 H)."""
    R, H = s['diameter_m'] / 2, s['height_m']
    return [dict(center_m=[0.35 * 2 * R * math.cos(2 * math.pi * k / CAL_BALLS), 0.35 * 2 * R * math.sin(2 * math.pi * k / CAL_BALLS),
                           0.1 * H + 0.8 * H * k / (CAL_BALLS - 1)], radius_m=CAL_BALL_RADIUS_M) for k in range(CAL_BALLS)]


def sample_truth(s):
    """Exact geometric truth of a phantom: volumes per material, porosity per layer and of the whole body, pores and cracks."""
    R = s['diameter_m'] / 2
    zb = _layer_bounds(s) if s['kind'] == 'cylinder' else [0.0, s['height_m']]
    body = math.pi * R * R * s['height_m'] if s['kind'] != 'edge_phantom' else s['diameter_m'] * EDGE_THICKNESS_M * s['height_m']
    pores = s.get('_pores', [])
    pv = sum(4 / 3 * math.pi * p['radius_m'] ** 3 for p in pores)
    cv = sum(math.pi * c['radius_m'] ** 2 * c['aperture_m'] for c in s['cracks']) if s['kind'] == 'cylinder' else 0.0
    iv = sum(4 / 3 * math.pi * i['radius_m'] ** 3 for i in s['inclusions']) if s['kind'] == 'cylinder' else 0.0
    layers = []
    if s['kind'] == 'cylinder':
        for k in range(len(s['layers'])):
            v = math.pi * R * R * (zb[k + 1] - zb[k])
            pk = sum(4 / 3 * math.pi * p['radius_m'] ** 3 for p in pores if zb[k] <= p['center_m'][2] < zb[k + 1])
            ck = sum(math.pi * c['radius_m'] ** 2 * c['aperture_m'] for c in s['cracks'] if zb[k] <= c['center_m'][2] < zb[k + 1])
            layers.append(dict(layer=k, volume_m3=v, pore_volume_m3=pk, crack_volume_m3=ck, porosity=(pk + ck) / v))
    return dict(body_volume_m3=body, pore_volume_m3=pv, crack_volume_m3=cv, inclusion_volume_m3=iv, porosity=(pv + cv) / body if body else 0.0,
                pore_count=len(pores), pore_equivalent_diameters_m=sorted(2 * p['radius_m'] for p in pores),
                cracks=[dict(area_m2=math.pi * c['radius_m'] ** 2, aperture_m=c['aperture_m'], radius_m=c['radius_m'], normal=list(c['normal']),
                             center_m=list(c['center_m'])) for c in s['cracks']], layers=layers)


def sample_height_total(s):
    return s['height_m'] + (s['pin']['length_m'] if s['pin'] else 0.0)


def sample_radius_total(s):
    """Radius of the rotation envelope about the sample axis (container included)."""
    if s['kind'] == 'edge_phantom':
        return math.hypot(s['diameter_m'], EDGE_THICKNESS_M / 2)
    return s['diameter_m'] / 2 + (s['container']['wall_m'] if s['container'] else 0.0)


def sample_mass_kg(s):
    """Mass of the sample body, consistent with the ray-traced geometry: layer matrices minus pores, cracks and inclusions, plus the
    inclusions at their own densities (container and pin are not part of the sample)."""
    R = s['diameter_m'] / 2
    m = 0.0
    if s['kind'] == 'cylinder':
        zb = _layer_bounds(s)
        rho = [mixture_density([(x['substance_id'], x['volume_fraction']) for x in L['materials']]) for L in s['layers']]

        def layer_of(z):
            return next((k for k in range(len(s['layers'])) if zb[k] <= z < zb[k + 1]), len(s['layers']) - 1)
        for k in range(len(s['layers'])):
            m += math.pi * R * R * (zb[k + 1] - zb[k]) * rho[k]
        for p in s.get('_pores', []):
            m -= 4 / 3 * math.pi * p['radius_m'] ** 3 * rho[s['pores']['layer']]
        for c in s['cracks']:
            m -= math.pi * c['radius_m'] ** 2 * c['aperture_m'] * rho[layer_of(c['center_m'][2])]
        for i in s['inclusions']:
            m += 4 / 3 * math.pi * i['radius_m'] ** 3 * (density(i['substance_id']) - rho[layer_of(i['center_m'][2])])
    elif s['kind'] == 'calibration_phantom':
        m = math.pi * R * R * s['height_m'] * density('nist_pmma') + CAL_BALLS * 4 / 3 * math.pi * CAL_BALL_RADIUS_M ** 3 * (density('fe') - density('nist_pmma'))
    else:
        m = s['diameter_m'] * EDGE_THICKNESS_M * s['height_m'] * density('w')
    return m


# ---------------------------------------------------------------- ray tracing (rays from the focal spot; lab frame: +X along the beam, +Y
# across (asset +Y), +Z up, origin at the focal spot)
def _slab(S, D, lo, hi, axis):
    """Parameter interval of the rays inside lo <= x_axis <= hi."""
    d = D[:, axis]
    s = S[axis] if S.ndim == 1 else S[:, axis]
    with np.errstate(divide='ignore', invalid='ignore'):
        t1 = (lo - s) / d
        t2 = (hi - s) / d
    tl = np.where(np.abs(d) < 1e-15, np.where((s >= lo) & (s <= hi), -np.inf, np.inf), np.minimum(t1, t2))
    th = np.where(np.abs(d) < 1e-15, np.where((s >= lo) & (s <= hi), np.inf, -np.inf), np.maximum(t1, t2))
    return tl, th


def _cyl(S, D, R, cx=0.0, cy=0.0):
    """Parameter interval of the rays inside the infinite vertical cylinder of radius R about (cx, cy)."""
    sx, sy = S[0] - cx, S[1] - cy
    a = D[:, 0] ** 2 + D[:, 1] ** 2
    b = 2 * (sx * D[:, 0] + sy * D[:, 1])
    c = sx * sx + sy * sy - R * R
    disc = b * b - 4 * a * c
    ok = (disc > 0) & (a > 1e-30)
    sq = np.sqrt(np.where(ok, disc, 0.0))
    with np.errstate(divide='ignore', invalid='ignore'):
        t1 = np.where(ok, (-b - sq) / (2 * a), np.inf)
        t2 = np.where(ok, (-b + sq) / (2 * a), -np.inf)
    return t1, t2


def _len(tl, th):
    return np.clip(th - tl, 0.0, None)


def _add(out, sid, L):
    out[sid] = out[sid] + L if sid in out else L.copy()


def body_paths(s, S, D):
    """Path lengths (m) of unit-direction rays S + t D (sample frame; S one point, D (n, 3)) through the materials of the sample body
    (layer matrices, pores, cracks, inclusions; phantom materials): {material id: array(n)}."""
    n = D.shape[0]
    out = {}
    R = s['diameter_m'] / 2
    if s['kind'] == 'edge_phantom':
        tl = np.full(n, -np.inf); th = np.full(n, np.inf)
        for ax, lo, hi in ((0, -EDGE_THICKNESS_M / 2, EDGE_THICKNESS_M / 2), (1, 0.0, s['diameter_m']), (2, 0.0, s['height_m'])):
            a, b = _slab(S, D, lo, hi, ax)
            tl, th = np.maximum(tl, a), np.minimum(th, b)
        _add(out, 'w', _len(tl, th))
        return out
    c1, c2 = _cyl(S, D, R)
    if s['kind'] == 'calibration_phantom':
        zl, zh = _slab(S, D, 0.0, s['height_m'], 2)
        body = _len(np.maximum(c1, zl), np.minimum(c2, zh))
        balls = sum_spheres(S, D, calibration_balls(s))
        _add(out, 'nist_pmma', np.clip(body - balls, 0.0, None))
        _add(out, 'fe', balls)
        return out
    zb = _layer_bounds(s)
    lay_pores = {}
    for p in s.get('_pores', []):
        lay_pores.setdefault(s['pores']['layer'], []).append(p)
    for k, L in enumerate(s['layers']):
        zl, zh = _slab(S, D, zb[k], zb[k + 1], 2)
        chord = _len(np.maximum(c1, zl), np.minimum(c2, zh))
        if not chord.any():
            continue
        void = sum_spheres(S, D, lay_pores.get(k, []))
        inc_paths = {}
        for i in [i for i in s['inclusions'] if zb[k] <= i['center_m'][2] < zb[k + 1]]:
            inc_paths.setdefault(i['substance_id'], np.zeros(n))
            inc_paths[i['substance_id']] += sum_spheres(S, D, [i])
        crack = np.zeros(n)
        for c in s['cracks']:
            if zb[k] <= c['center_m'][2] < zb[k + 1]:
                crack += disc_path(S, D, c)
        matrix = np.clip(chord - void - crack - sum(inc_paths.values(), np.zeros(n)), 0.0, None)
        for m in L['materials']:
            _add(out, m['substance_id'], matrix * m['volume_fraction'])
        for sid, v in inc_paths.items():
            _add(out, sid, v)
    return out


def container_walls(s, S, D):
    """Path lengths through the container wall (open coaxial tube over the body height) before and after the inside of the tube along each
    ray: ({material: near}, {material: far}); a ray that misses the inside has all of its wall path in 'near'.  A ray leaving the inside
    through the open top or bottom has no far part."""
    if not s['container'] or s['kind'] == 'edge_phantom':
        return {}, {}
    R = s['diameter_m'] / 2
    zl, zh = _slab(S, D, 0.0, s['height_m'], 2)
    o1, o2 = _cyl(S, D, R + s['container']['wall_m'])
    c1, c2 = _cyl(S, D, R)
    ol, oh = np.maximum(o1, zl), np.minimum(o2, zh)
    il, ih = np.maximum(c1, zl), np.minimum(c2, zh)
    inside = ih > il
    with np.errstate(invalid='ignore'):                              # rays missing a cylinder carry +-inf parameters
        near = np.where(inside, np.clip(il - ol, 0.0, None), _len(ol, oh))
        far = np.where(inside, np.clip(oh - ih, 0.0, None), 0.0)
    near, far = np.where(np.isfinite(near), near, 0.0), np.where(np.isfinite(far), far, 0.0)
    sid = s['container']['substance_id']
    return {sid: near}, {sid: far}


def container_paths(s, S, D):
    """Path lengths through the container wall around the body (coaxial tube over the body height)."""
    near, far = container_walls(s, S, D)
    return {sid: near[sid] + far[sid] for sid in near}


def pin_paths(s, S, D):
    """Path lengths through the mounting pin below the body."""
    out = {}
    if s['pin']:
        zl, zh = _slab(S, D, -s['pin']['length_m'], 0.0, 2)
        p1, p2 = _cyl(S, D, s['pin']['diameter_m'] / 2)
        _add(out, s['pin']['substance_id'], _len(np.maximum(p1, zl), np.minimum(p2, zh)))
    return out


def holder_paths(s, S, D):
    """Path lengths through the sample holder: the container around the body and the mounting pin below it."""
    out = container_paths(s, S, D)
    for sid, L in pin_paths(s, S, D).items():
        _add(out, sid, L)
    return out


HARDWARE_MATERIALS = ('cu', 'al')                  # brass chuck (modelled as copper) and the aluminium rotation-stage housing with its platter
STAGE_HOUSING_R_M = 0.0451                         # 80 x 80 mm housing as the area-equivalent cylinder (it does not turn with the platter)


def hardware_paths(S, D, ax, ay, z_top):
    """Path lengths through the stage hardware below the sample (sample frame): the chuck (r 12 mm, 15 mm high, modelled as copper) whose top
    is at z_top on the rotation axis (ax, ay), and below it the rotation-stage housing with the platter (aluminium, 50 mm high)."""
    out = {}
    for sid, r, z0, z1 in (('cu', CHUCK_R_M, z_top - CHUCK_H_M, z_top), ('al', STAGE_HOUSING_R_M, z_top - CHUCK_H_M - STAGE_H_M, z_top - CHUCK_H_M)):
        zl, zh = _slab(S, D, z0, z1, 2)
        c1, c2 = _cyl(S, D, r, ax, ay)
        L = _len(np.maximum(c1, zl), np.minimum(c2, zh))
        if L.any():
            _add(out, sid, L)
    return out


def sample_paths(s, S, D):
    """Path lengths through every material of the sample body and its holder: {material id: array(n)}."""
    out = body_paths(s, S, D)
    for sid, L in holder_paths(s, S, D).items():
        _add(out, sid, L)
    return out


def sum_spheres(S, D, spheres):
    """Sum of the chord lengths of the rays through the given spheres (exact; only rays within the sphere's angular footprint evaluated)."""
    n = D.shape[0]
    tot = np.zeros(n)
    if not spheres:
        return tot
    if n * len(spheres) <= 2_000_000:                  # small ray sets (energy ledger): all spheres at once, same formula
        C = np.array([sp['center_m'] for sp in spheres], float)
        r = np.array([sp['radius_m'] for sp in spheres], float)
        v = C - S
        dist = np.linalg.norm(v, axis=1)
        ok = dist > r
        if not ok.any():
            return tot
        v, dist, r = v[ok], dist[ok], r[ok]
        cosmax = np.sqrt(np.clip(1 - (r / dist) ** 2, 0.0, None))
        tc = D @ v.T                                               # (rays, spheres)
        inside = tc / dist > cosmax - 1e-12
        chord = 2 * np.sqrt(np.clip(r * r - (dist * dist - tc * tc), 0.0, None))
        return (chord * inside).sum(1)
    for sp in spheres:
        C = np.asarray(sp['center_m'], float)
        r = sp['radius_m']
        v = C - S
        dist = np.linalg.norm(v)
        if dist <= r:
            continue
        cosmax = math.sqrt(max(0.0, 1 - (r / dist) ** 2))
        cand = np.where(D @ (v / dist) > cosmax - 1e-12)[0]
        if not len(cand):
            continue
        tc = D[cand] @ v
        d2 = dist * dist - tc * tc
        tot[cand] += 2 * np.sqrt(np.clip(r * r - d2, 0.0, None))
    return tot


def disc_path(S, D, c):
    """Path of the rays through a planar crack (disc of radius R and aperture a): a / |n.d| where the ray crosses the disc, at most 2 R."""
    nrm = np.asarray(c['normal'], float)
    nrm = nrm / np.linalg.norm(nrm)
    C = np.asarray(c['center_m'], float)
    nd = D @ nrm
    with np.errstate(divide='ignore', invalid='ignore'):
        t = ((C - S) @ nrm) / nd
    P = S + t[:, None] * D
    inside = (np.abs(nd) > 1e-9) & (np.einsum('ij,ij->i', P - C, P - C) <= c['radius_m'] ** 2)
    return np.where(inside, np.minimum(c['aperture_m'] / np.maximum(np.abs(nd), 1e-12), 2 * c['radius_m']), 0.0)


# ---------------------------------------------------------------- capabilities and state slots
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SAMPLE_ID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.-]+$')
SUB_ID = dict(type='string', minLength=1, maxLength=40)
XYZ = array(number('m', -0.2, 0.2), 3, 3)
FAULTS = ['xray_arc', 'detector_defects', 'rotation_stall', 'door_sensor', 'light_curtain_blocked']
FAULT_ERROR = {'xray_arc': 'XRAY_ARC', 'rotation_stall': 'STAGE_FAULT', 'door_sensor': 'INTERLOCK_FAULT'}
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, relative_humidity=0.45, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 308.15), mains_voltage_v=number('V', 0.0, 264.0), relative_humidity=number('1', 0.0, 1.0), noise_seed=INT))
MIX = array(obj(dict(substance_id=SUB_ID, volume_fraction=number('1', 0.0, 1.0))), 1, 4)
LAYER = obj(dict(thickness_m=number('m', 1.0e-5, SAMPLE_MAX_HEIGHT_M), materials=MIX))
PORES = obj(dict(count=integer(0, MAX_PORES), median_radius_m=number('m', 1.0e-6, 0.01), sigma_ln=number('1', 0.0, 1.5), layer=integer(0, 5), seed=INT))
CRACK = obj(dict(center_m=XYZ, normal=array(number('1', -1.0, 1.0), 3, 3), radius_m=number('m', 1.0e-4, 0.05), aperture_m=number('m', 1.0e-6, 1.0e-3)))
INCLUSION = obj(dict(substance_id=SUB_ID, center_m=XYZ, radius_m=number('m', 1.0e-6, 0.02)))
CONTAINER = nullable(obj(dict(substance_id=SUB_ID, wall_m=number('m', 1.0e-5, 5.0e-3))))
PIN = nullable(obj(dict(substance_id=SUB_ID, diameter_m=number('m', 5.0e-4, 0.02), length_m=number('m', 1.0e-3, 0.1))))
SAMPLE = obj(dict(sample_id=SAMPLE_ID, kind=enum(SAMPLE_KINDS), diameter_m=number('m', 5.0e-4, SAMPLE_MAX_DIAMETER_M),
                  height_m=number('m', 5.0e-4, SAMPLE_MAX_HEIGHT_M), layers=array(LAYER, 0, 6), pores=PORES, cracks=array(CRACK, 0, MAX_CRACKS),
                  inclusions=array(INCLUSION, 0, MAX_INCLUSIONS), container=CONTAINER, pin=PIN))
SAMPLE_EXAMPLE = dict(sample_id='alumina_pellet', kind='cylinder', diameter_m=0.006, height_m=0.006,
                      layers=[dict(thickness_m=0.006, materials=[dict(substance_id='alumina', volume_fraction=1.0)])],
                      pores=dict(count=120, median_radius_m=0.12e-3, sigma_ln=0.35, layer=0, seed=7),
                      cracks=[dict(center_m=[0.0, 0.0, 0.003], normal=[1.0, 0.0, 0.0], radius_m=0.0018, aperture_m=30e-6)], inclusions=[], container=None,
                      pin=dict(substance_id='al', diameter_m=0.003, length_m=0.035))
OPERATION = obj(dict(operation_id=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
RANGES = ['180', '360']
SCAN = obj(dict(range_deg=enum(RANGES), step_rad=number('rad', math.radians(0.05), math.radians(2.0)), exposure_s=number('s', EXPOSURE_RANGE_S[0], EXPOSURE_RANGE_S[1]),
                averaging=integer(AVERAGING_RANGE[0], AVERAGING_RANGE[1]), binning=enum(['1x1', '2x2', '4x4']), random_movement=BOOL))
SCAN_EXAMPLE = dict(range_deg='180', step_rad=math.radians(0.4), exposure_s=0.3, averaging=2, binning='2x2', random_movement=True)
FILTER_NAMES = ['none', 'hann', 'shepp_logan', 'ram_lak']
VOI = nullable(obj(dict(center_m=XYZ, size_m=array(number('m', 1.0e-4, 0.2), 3, 3))))
RECON = obj(dict(record_id=TEXT, grid=enum([str(g) for g in RECON_GRIDS]), filter=enum(FILTER_NAMES[1:]), beam_hardening=number('1', 0.0, 1.0),
                 bh_material=nullable(SUB_ID), ring_correction=number('1', 0.0, 1.0), smoothing_vox=number('1', 0.0, 3.0), post_alignment=nullable(number('m', -0.005, 0.005)),
                 auto_alignment=BOOL, voi=VOI))
RECON_EXAMPLE = dict(record_id='PJ0001', grid='80', filter='shepp_logan', beam_hardening=0.5, bh_material='alumina', ring_correction=0.5, smoothing_vox=0.0,
                     post_alignment=None, auto_alignment=True, voi=None)
PREVIEW = array(array(nullable(number('1')), 1, 128), 1, 128)
XSET = obj(dict(voltage_v=number('V'), current_a=number('A'), power_w=number('W'), filter=TEXT, focal_spot_m=number('m')))
RECORD = obj(dict(record_id=TEXT, kind=enum(['flat_field', 'geometry_calibration', 'radiograph', 'projections', 'volume']), status=STATUS, started_s=number('s'),
                  finished_s=nullable(number('s')), reason=nullable(TEXT), sample_id=nullable(TEXT), xray=nullable(XSET), scan=nullable(SCAN),
                  sod_m=nullable(number('m')), sdd_m=number('m'), magnification=nullable(number('1')), voxel_size_nominal_m=nullable(number('m')),
                  projections_nominal=INT, projections_simulated=INT, sim_detector=array(INT, 2, 2), sim_pixel_m=nullable(number('m')), transmission=nullable(obj(dict(min=number('1'), mean=number('1'),
                                                                                                                                   max=number('1')))),
                  saturated_fraction=nullable(number('1', 0.0, 1.0)), dose_gy=nullable(number('Gy')), duration_s=number('s'), preview=nullable(PREVIEW),
                  preview_unit=nullable(TEXT), source_record_id=nullable(TEXT), recon=nullable(obj({k: v for k, v in RECON['properties'].items() if k != 'record_id'})),
                  volume=nullable(obj(dict(shape=array(INT, 3, 3), voxel_m=number('m'), origin_m=XYZ, mu_min_1_m=number('1/m'), mu_max_1_m=number('1/m'),
                                           histogram=array(INT, 0, 128), histogram_edges_1_m=array(number('1/m'), 0, 129), axis_offset_used_m=number('m')))),
                  flat_field=nullable(obj(dict(frames=INT, mean_bright_adu=number('1'), dark_adu=number('1'), key=TEXT))),
                  geometry=nullable(obj(dict(axis_offset_m=number('m'), residual_m=number('m'), balls=INT))), warnings=array(TEXT, 0, 16),
                  template_source=obj(dict(library=TEXT, substances=array(TEXT, 0, 32))), note_zh=TEXT))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'door', 'xray', 'stage', 'filter', 'measurement', 'calibration', 'sample', 'error', 'fault', 'safety']),
                 code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['xray_on_s', 'electrical_energy_j', 'xray_emitted_j', 'target_absorbed_j', 'window_absorbed_j', 'filter_absorbed_j', 'air_absorbed_j',
               'sample_absorbed_j', 'holder_absorbed_j', 'sample_scattered_j', 'detector_absorbed_j', 'detector_passed_j', 'shielding_j', 'energy_residual_j',
               'sample_dose_gy',
               'projections', 'frames', 'rotation_rad', 'carriage_travel_m', 'lift_travel_m', 'door_cycles']
LEDGER = obj({k: number('J' if k.endswith('_j') else 's' if k.endswith('_s') else 'm' if k.endswith('_m') else 'rad' if k.endswith('_rad') else 'Gy' if k.endswith('_gy')
                        else '1') for k in LEDGER_KEYS})
REGION = nullable(obj(dict(center_m=XYZ, radius_m=number('m', 1.0e-4, 0.06), z_min_m=number('m', -0.2, 0.2), z_max_m=number('m', -0.2, 0.2))))
POROSITY_OUT = obj(dict(record_id=TEXT, threshold_1_m=number('1/m'), method=TEXT, envelope_voxels=INT, pore_voxels=INT, porosity=number('1', 0.0, 1.0),
                        open_porosity=number('1', 0.0, 1.0), closed_porosity=number('1', 0.0, 1.0), pore_count=INT,
                        equivalent_diameter_m=obj(dict(mean=nullable(number('m')), d10=nullable(number('m')), d50=nullable(number('m')), d90=nullable(number('m')),
                                                       max=nullable(number('m')))), voxel_m=number('m'), note_zh=TEXT))
CRACK_OUT = obj(dict(record_id=TEXT, threshold_1_m=number('1/m'), cracks=array(obj(dict(area_m2=number('m2'), aperture_m=number('m'), length_m=number('m'),
                                                                                         normal=array(number('1'), 3, 3), center_m=XYZ, voxels=INT)), 0, 32),
                     crack_count=INT, total_area_m2=number('m2'), note_zh=TEXT))
cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['enclosure_base'], '宿主声明实验室条件（室温、电源电压、相对湿度、噪声种子）', 'evaluator', 'configuration')
cap('sample.provide', SAMPLE, obj(dict(sample_id=TEXT, location=TEXT, mass_kg=number('kg'))), SAMPLE_EXAMPLE, ['sample_chuck'],
    '宿主在实验台上提供一个样品（圆柱体分层材料/体积混合物、球形孔、平面裂纹、夹杂、样品管、样品针；几何标定体模；钨刃边体模；材料来自X射线衰减模板库）', 'evaluator',
    'configuration')
cap('sample.place', obj(dict(sample_id=SAMPLE_ID, offset_x_m=number('m', -0.005, 0.005), offset_y_m=number('m', -0.005, 0.005))),
    obj(dict(sample_id=TEXT, offset_x_m=number('m'), offset_y_m=number('m'))), dict(sample_id='alumina_pellet', offset_x_m=0.0, offset_y_m=0.0),
    ['sample_chuck', 'sample_turntable', 'door_glass'], '门打开时把实验台上的样品装到旋转台样品夹上（宿主/机器人抽象；样品轴相对旋转轴的偏移）', 'agent', 'robot_skill')
cap('sample.remove', obj(dict(sample_id=SAMPLE_ID)), obj(dict(sample_id=TEXT)), dict(sample_id='alumina_pellet'), ['sample_chuck', 'door_glass'],
    '门打开时把样品从样品夹取回实验台（宿主/机器人抽象）', 'agent', 'robot_skill')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL, state=TEXT)), dict(enabled=True), ['button_mains', 'enclosure_base'],
    '系统电源（上电后60 s启动；各轴为绝对位置，保持断电时的位置，不回原点）')
cap('power.press_button', obj({}), obj(dict(enabled=BOOL, state=TEXT)), {}, ['button_mains', 'enclosure_base'],
    '按右侧电源开关（按一次切换通断；与power.set同一上电/断电过程）')
cap('emergency.stop', obj({}), obj(dict(stopped=BOOL)), {}, ['button_emergency_stop'], '按下正面红色安全按钮：立即关闭X射线、停止所有运动与门，锁存直到释放并确认')
cap('emergency.release', obj({}), obj(dict(stopped=BOOL)), {}, ['button_emergency_stop'], '旋转释放安全按钮（之后用system.acknowledge确认错误）')
cap('system.acknowledge', obj({}), obj(dict(cleared=nullable(TEXT))), {}, ['touchscreen'], '确认已排除原因的锁存错误')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 8))), dict(fault='xray_arc', active=True), ['enclosure_base'],
    '注入或清除故障（评测者）：X射线管打火、探测器坏列、旋转台卡滞、门联锁传感器故障、安全光幕被挡', 'evaluator', 'configuration')
cap('door.open', obj({}), obj(dict(target_m=number('m'), duration_s=number('s'))), {}, ['door_glass'], '打开电动铅玻璃门（X射线开启时门锁住）')
cap('door.close', obj({}), obj(dict(target_m=number('m'), duration_s=number('s'))), {}, ['door_glass'], '关闭电动铅玻璃门（安全光幕被挡时停止并重新打开）')
cap('xray.set', obj(dict(voltage_v=number('V', XRAY_KV_MIN_V, XRAY_KV_MAX_V), current_a=number('A', 1.0e-6, XRAY_CURRENT_MAX_A))),
    obj(dict(voltage_v=number('V'), current_a=number('A'), power_w=number('W'), focal_spot_m=number('m'))), dict(voltage_v=60.0e3, current_a=150.0e-6),
    ['source_housing', 'source_head'], 'X射线管电压与电流设定（功率不超过20 W；束流开启时按斜率变化）', 'agent', 'configuration')
cap('xray.enable', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL, warmup_s=number('s'), ramp_s=number('s'))), dict(enabled=True), ['source_housing', 'source_head'],
    '开关X射线（门关闭、联锁正常时；每次上电后首次开启先运行预热程序）')
cap('filter.select', obj(dict(position=integer(0, FILTER_POSITIONS - 1))), obj(dict(filter=TEXT, duration_s=number('s'))), dict(position=3),
    ['filter_changer', 'filter_slide'], '电动7位滤片转换器选择滤片（无、Al 0.25/0.5/1.0 mm、Cu 0.1/0.25 mm、Al 1 mm+Cu 0.1 mm）')
cap('stage.move', obj(dict(sod_m=number('m', SOD_RANGE_M[0], SOD_RANGE_M[1]), chuck_z_m=number('m', CHUCK_Z_RANGE_M[0], CHUCK_Z_RANGE_M[1]))),
    obj(dict(target=array(number('m'), 2, 2), duration_s=number('s'), magnification=number('1'))), dict(sod_m=0.045, chuck_z_m=0.362),
    ['sample_carriage', 'sample_lift'], '样品滑台沿光束轴移动（源-物距=几何放大）并升降样品夹（碰撞保护：源窗口、滤片转换器、探测器盖板、腔顶）')
cap('stage.rotate', obj(dict(angle_rad=number('rad', 0.0, 2 * math.pi))), obj(dict(target_rad=number('rad'), duration_s=number('s'))), dict(angle_rad=math.pi / 2),
    ['sample_turntable', 'sample_lift'], '旋转台转到给定角度（沿最短方向；电机在升降支架上的旋转台壳体内）')
cap('stage.go_to', obj(dict(position=enum(['load', 'center']))), obj(dict(target=array(number('m'), 2, 2), duration_s=number('s'))), dict(position='load'),
    ['sample_carriage', 'sample_lift'], '样品滑台移到命名位置（门口装样位、中间位）')
cap('scan.configure', SCAN, obj(dict(settings=SCAN, projections=INT, duration_s=number('s'))), SCAN_EXAMPLE, ['touchscreen'],
    '扫描设置：角度范围180°(+锥角)/360°、旋转步长、曝光、帧平均、像素合并、随机移动（抑制环状伪影）', 'agent', 'configuration')
cap('calibration.flat_field', obj({}), OPERATION, {}, ['detector_housing', 'source_head'],
    '探测器暗场与亮场校正（当前电压、电流、滤片、合并与曝光；光束中不能有样品）')
cap('calibration.geometry', obj({}), OPERATION, {}, ['sample_turntable', 'detector_housing'], '几何标定：用钢球标定体模做36投影360°扫描，测出旋转轴横向偏移')
cap('xray.radiograph', obj(dict(roi=nullable(obj(dict(center_u_m=number('m', -0.07, 0.07), center_v_m=number('m', -0.06, 0.06), size_px=integer(16, 256)))))),
    OPERATION, dict(roi=None), ['detector_housing', 'source_head', 'sample_turntable'], '单张投影（透视）；可选探测器局部区域以合并后像素分辨率成像')
cap('scan.start', obj({}), OPERATION, {}, ['source_head', 'sample_turntable', 'detector_housing', 'filter_slide'], '按扫描设置采集旋转投影序列')
cap('recon.start', RECON, OPERATION, RECON_EXAMPLE, ['workstation_monitor'],
    'FDK锥束重建（滤波器、束硬化校正、环状伪影校正、平滑、后对准（手动或自动）、感兴趣体积）', 'agent', 'analysis')
cap('measurement.result', obj(dict(record_id=TEXT)), RECORD, dict(record_id='PJ0001'), ['workstation_monitor'], '读取记录（校正、透视、投影序列或体数据摘要）', 'agent', 'query')
cap('data.porosity', obj(dict(record_id=TEXT, region=REGION, threshold_1_m=nullable(number('1/m', 0.0, 1.0e6)), min_pore_voxels=integer(1, 1000))), POROSITY_OUT,
    dict(record_id='VO0001', region=None, threshold_1_m=None, min_pore_voxels=2), ['workstation_monitor'],
    '孔隙率：样品包络内低衰减体素（阈值Otsu或给定）、连通孔（开孔/闭孔）、孔数与等效直径分布', 'agent', 'analysis')
cap('data.cracks', obj(dict(record_id=TEXT, region=REGION, threshold_1_m=nullable(number('1/m', 0.0, 1.0e6)), min_voxels=integer(3, 10000))), CRACK_OUT,
    dict(record_id='VO0001', region=None, threshold_1_m=None, min_voxels=20), ['workstation_monitor'],
    '裂纹：低衰减连通体中的平面状部分（主成分最小/最大尺度比），面积、开口与方向', 'agent', 'analysis')
cap('data.attenuation', obj(dict(record_id=TEXT, region=REGION)),
    obj(dict(record_id=TEXT, mean_1_m=number('1/m'), std_1_m=number('1/m'), median_1_m=number('1/m'), p05_1_m=number('1/m'), p95_1_m=number('1/m'), voxels=INT)),
    dict(record_id='VO0001', region=None), ['workstation_monitor'], '区域内线衰减系数统计（均值、标准差、分位数）', 'agent', 'analysis')
cap('data.slice', obj(dict(record_id=TEXT, axis=enum(['x', 'y', 'z']), position_m=number('m', -0.2, 0.2), size_px=integer(16, 128))),
    obj(dict(record_id=TEXT, axis=TEXT, position_m=number('m'), pixel_m=number('m'), mu_1_m=PREVIEW)), dict(record_id='VO0001', axis='z', position_m=0.003, size_px=64),
    ['workstation_monitor'], '取体数据的一张切片（线衰减系数，重采样到给定像素数）', 'agent', 'analysis')
cap('data.edge_resolution', obj(dict(record_id=TEXT)), obj(dict(record_id=TEXT, width_10_90_m=nullable(number('m')), width_detector_px=nullable(number('1')),
                                                                edge_angle_rad=nullable(number('rad')), note_zh=TEXT)), dict(record_id='RG0001'), ['workstation_monitor'],
    '透视刃边法分辨率：刃边扩展函数10–90 %宽度（换算到样品平面）', 'agent', 'analysis')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, 200))), {}, ['workstation_monitor'], '列出记录', 'agent', 'query')
cap('records.delete', obj(dict(record_id=TEXT)), obj(dict(record_id=TEXT, remaining=INT)), dict(record_id='RG0001'), ['workstation_monitor'],
    '删除一条记录及其缓存数据（记录数上限60；进行中的操作或正被重建的投影记录不能删除）', 'agent', 'configuration')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, 500), dropped=INT)), dict(since_s=0.0), ['touchscreen'], '读取事件与报警',
    'agent', 'query')
cap('ledger.read', obj({}), LEDGER, {}, ['enclosure_base'], '能量与使用账本（X射线能量去向、样品剂量、投影与帧数、行程）', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, library_sha256=TEXT, substances=INT, license=TEXT)), {}, ['workstation_monitor'], '模板库来源', 'agent', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['enclosure_base'], '宿主推进仿真时间', 'evaluator', 'simulation')
UNSUPPORTED = {
    'hardware.connect': '没有真实X4 POSEIDON硬件或3DxSUITE软件接口',
    'robot.handle_sample': '机器人接触装样（抓取、插入样品夹）未建模；sample.place为宿主抽象',
    'sample_changer.load': '15位样品转换器是选件，不在本配置',
    'stage.micropositioning': '带微定位的高级旋转台是选件（本配置为标准旋转台）',
    'detector.select_scmos': '16 MP sCMOS探测器与Multi-Vision双探测器是选件，不在本配置',
    'source.transmission': '透射式X射线源（110 kV、16 W、<2 µm@2 W）是另一种源配置',
    'geometry.best_scan': 'Best-Scan-Geometry（探测器连续移动）是选件（本配置为固定扫描几何）',
    'scan.helical': '高级扫描模式（螺旋、HART+）是选件',
    'stage.in_situ': '原位材料试验台（压缩4400 N、拉伸440 N）与加热/冷却台是选件',
    'export.dataset': '3DxSUITE数据集导出（切片图像、网格、CAD比对）与相位恢复算法未建模',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['enclosure_base'], _why, 'agent', 'device_command')


SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


XY = array(number('m'), 2, 2)
slot('clock.time_s', number('s'), 's', 'derived', 'enclosure_base', '仿真时钟')
slot('plant.configured', BOOL, '1', 'setpoint', 'enclosure_base', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'button_mains', '系统电源')
slot('system.state', enum(['off', 'booting', 'ready']), '1', 'derived', 'touchscreen', '系统状态（断电/启动中/就绪）')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'touchscreen', '锁存的错误代码')
slot('emergency.stopped', BOOL, '1', 'sensor', 'button_emergency_stop', '安全按钮被按下（锁存）')
slot('door.position_m', number('m'), 'm', 'derived', 'door_glass', '铅玻璃门打开量（0为关闭，向左滑开；场景门关节镜像）')
slot('door.target_m', number('m'), 'm', 'derived', 'door_glass', '门的目标位置')
slot('door.moving', BOOL, '1', 'derived', 'door_glass', '门在移动')
slot('door.closed', BOOL, '1', 'sensor', 'door_glass', '门关闭（联锁开关）')
slot('light_curtain.blocked', BOOL, '1', 'sensor', 'door_frame', '门口安全光幕被挡')
slot('interlock.ok', BOOL, '1', 'derived', 'door_frame', 'X射线联锁满足（门关闭、联锁传感器正常、安全按钮未按下）')
slot('xray.voltage_set_v', number('V'), 'V', 'setpoint', 'source_head', '管电压设定')
slot('xray.current_set_a', number('A'), 'A', 'setpoint', 'source_head', '管电流设定')
slot('xray.enabled', BOOL, '1', 'setpoint', 'source_head', 'X射线开启命令')
slot('xray.voltage_v', number('V'), 'V', 'sensor', 'source_head', '实际管电压（斜坡）')
slot('xray.current_a', number('A'), 'A', 'sensor', 'source_head', '实际管电流（斜坡）')
slot('xray.power_w', number('W'), 'W', 'derived', 'source_head', '靶功率=电压×电流')
slot('xray.on', BOOL, '1', 'derived', 'source_head', 'X射线正在发射')
slot('xray.ready', BOOL, '1', 'derived', 'source_head', 'X射线稳定在设定值且已预热')
slot('xray.warmup_remaining_s', number('s', 0.0), 's', 'derived', 'source_head', '预热剩余时间')
slot('xray.focal_spot_m', number('m'), 'm', 'derived', 'source_head', '焦点尺寸（随靶功率增大）')
slot('filter.position', integer(0, FILTER_POSITIONS - 1), '1', 'setpoint', 'filter_slide', '滤片位置')
slot('filter.label', TEXT, '1', 'derived', 'filter_slide', '滤片名称')
slot('filter.slide_m', number('m'), 'm', 'derived', 'filter_slide', '滤片滑块位置（场景滑块关节镜像）')
slot('filter.moving', BOOL, '1', 'derived', 'filter_slide', '滤片转换器在移动')
slot('stage.sod_m', number('m'), 'm', 'derived', 'sample_carriage', '源-物距（焦点到旋转轴；场景滑台关节镜像）')
slot('stage.sod_target_m', number('m'), 'm', 'derived', 'sample_carriage', '源-物距目标')
slot('stage.chuck_z_m', number('m'), 'm', 'derived', 'sample_lift', '样品夹顶面高度（台面坐标；场景升降关节镜像）')
slot('stage.chuck_z_target_m', number('m'), 'm', 'derived', 'sample_lift', '样品夹高度目标')
slot('stage.rotation_rad', number('rad'), 'rad', 'derived', 'sample_turntable', '旋转台角度（场景旋转关节镜像）')
slot('stage.rotation_target_rad', number('rad'), 'rad', 'derived', 'sample_turntable', '旋转台目标角度')
slot('stage.moving', BOOL, '1', 'derived', 'sample_carriage', '滑台、升降或旋转台在移动')
slot('stage.position_label', nullable(TEXT), '1', 'derived', 'sample_carriage', '命名位置（到位后）')
slot('geometry.magnification', number('1'), '1', 'derived', 'sample_carriage', '几何放大倍数=源-探距/源-物距')
slot('geometry.voxel_size_m', number('m'), 'm', 'derived', 'detector_housing', '名义体素尺寸=像素间距×合并/放大')
slot('geometry.fov_m', XY, 'm', 'derived', 'detector_housing', '旋转轴处的视场宽×高')
slot('scan.settings', SCAN, '1', 'setpoint', 'touchscreen', '扫描设置')
slot('scan.projections', INT, '1', 'derived', 'touchscreen', '扫描投影数（名义）')
slot('scan.duration_s', number('s'), 's', 'derived', 'touchscreen', '扫描时间估计')
slot('calibration.flat_field', obj(dict(valid=BOOL, key=nullable(TEXT), calibrated_s=nullable(number('s')))), '1', 'derived', 'detector_housing',
     '暗场/亮场校正是否与当前电压、电流、滤片、合并与曝光一致')
slot('calibration.geometry', obj(dict(axis_offset_m=nullable(number('m')), calibrated_s=nullable(number('s')))), '1', 'derived', 'sample_turntable',
     '几何标定测得的旋转轴横向偏移')
slot('measurement.id', nullable(TEXT), '1', 'derived', 'touchscreen', '进行中的记录ID')
slot('measurement.kind', nullable(TEXT), '1', 'derived', 'touchscreen', '进行中的操作种类')
slot('measurement.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'touchscreen', '进行中操作的进度')
slot('measurement.remaining_s', nullable(number('s', 0.0)), 's', 'derived', 'touchscreen', '进行中操作的剩余时间')
slot('measurement.started_by_other_agent', BOOL, '1', 'derived', 'touchscreen', '进行中的操作属于另一个agent')
slot('recon.id', nullable(TEXT), '1', 'derived', 'workstation_monitor', '工作站上进行中的重建记录ID（与仪器采集并行）')
slot('recon.remaining_s', nullable(number('s', 0.0)), 's', 'derived', 'workstation_monitor', '重建剩余时间')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'workstation_monitor', '最近完成的记录ID（agent只看到自己或宿主创建的记录）')
slot('result.last_porosity', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'workstation_monitor', '最近一次孔隙率分析的结果')
slot('sample.bench_ids', array(TEXT, 0, MAX_SAMPLES), '1', 'derived', 'sample_chuck', '实验台上的样品')
slot('sample.on_stage', nullable(obj(dict(sample_id=TEXT, offset_x_m=number('m'), offset_y_m=number('m')))), '1', 'derived', 'sample_chuck', '样品夹上的样品及偏移')
slot('panel.status_green', BOOL, '1', 'derived', 'lamp_status_green', '状态灯绿（通电、无错误、X射线关）')
slot('panel.status_orange', BOOL, '1', 'derived', 'lamp_status_orange', '状态灯橙（X射线开启）')
slot('panel.status_red', BOOL, '1', 'derived', 'lamp_status_red', '状态灯红（错误或安全按钮按下）')
slot('truth.axis_offset_m', number('m'), 'm', 'process_truth', 'sample_turntable', '旋转轴真实横向偏移', 'evaluator')
slot('truth.samples', {'type': 'object'}, '1', 'process_truth', 'sample_chuck', '样品真实定义（含生成的孔）与真实孔隙率、裂纹', 'evaluator')
slot('truth.faults', array(TEXT, 0, 8), '1', 'process_truth', 'enclosure_base', '注入的故障', 'evaluator')
slot('truth.detector_defect_columns', array(INT, 0, 16), '1', 'process_truth', 'detector_housing', '注入的坏列（模拟探测器列号）', 'evaluator')


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
    """Linear move towards the target; arrives exactly (a remainder of rounding size snaps to the target)."""
    d = target - pos
    step = speed * dt
    return target if abs(d) <= step + 1e-12 else pos + math.copysign(step, d)


def _rot_delta(a, b):
    """Shortest signed rotation from a to b (rad)."""
    return (b - a + math.pi) % (2 * math.pi) - math.pi


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


def _segment_hits_box(p, q, box):
    """Does the segment p->q pass through the inside of the open axis-aligned rectangle ((x0, x1), (y0, y1)) (bounds may be infinite)?"""
    t0, t1 = 0.0, 1.0
    for a, d, (lo, hi) in ((p[0], q[0] - p[0], box[0]), (p[1], q[1] - p[1], box[1])):
        if abs(d) < 1e-15:
            if not (lo < a < hi):
                return False
            continue
        ta, tb = (lo - a) / d, (hi - a) / d
        if ta > tb:
            ta, tb = tb, ta
        t0, t1 = max(t0, ta), min(t1, tb)
        if t0 >= t1:
            return False
    return t0 < t1


FAULT_PRIORITY = ('door_sensor', 'xray_arc', 'rotation_stall')     # order of the latched errors after power-up / acknowledgement
FLAT_MAX_SIGMA = 0.10                            # a bright field whose binned-pixel relative error exceeds 10 % is not a usable calibration
GEO_FIT_MAX_RESIDUAL_PX = 2.0                    # ball-track fit accepted up to 2 window pixels (at the axis) rms, and at least ...
GEO_FIT_MIN_LIMIT_M = 5.0e-6                     # ... 5 um
RATE_ROT_CELLS = 32                              # energy-rate cells: the beam's energy flow is evaluated at the cell centre of the pose
RATE_ROT_CELL_RAD = 2 * math.pi / RATE_ROT_CELLS
RATE_POS_CELL_M = 0.25e-3
RATE_KV_CELL_V = 1.0e3
LEDGER_FINE_RAYS = 1024                          # about this many rays over the projection of the sample body (energy ledger)
LEDGER_RING_CELL_M = 0.008                       # coarse ledger cells (detector plane) between the active area and the emission cone
ENERGY_SINKS = ('target_absorbed_j', 'window_absorbed_j', 'filter_absorbed_j', 'air_absorbed_j', 'sample_absorbed_j', 'holder_absorbed_j', 'sample_scattered_j',
                'detector_absorbed_j', 'detector_passed_j', 'shielding_j')


def _to_boundary(x, w, cell):
    """Time until x moving at rate w (!= 0) crosses the next boundary of cells of width `cell` centred on integer multiples (boundaries at
    (k + 1/2) cell)."""
    if w > 0:
        b = (math.floor(x / cell - 0.5) + 1.5) * cell
    else:
        b = (math.ceil(x / cell - 0.5) - 0.5) * cell
    if (b - x) / w < 1e-9:                       # x sits on a boundary within rounding: the next one
        b += math.copysign(cell, w)
    return (b - x) / w



@functools.lru_cache(maxsize=None)
def _mu_grid(sid, n):
    """Linear coefficients (1/m) of a library material on the first n energies of the fine spectrum grid (every tube voltage uses a prefix)."""
    return linear_mu(sid, E_GRID_KEV[:n])


def _cells(u0, u1, v0, v1, nu, nv):
    """Centres and sizes of an (nu x nv) grid of cells over the rectangle [u0, u1] x [v0, v1] of the detector plane."""
    pu, pv = (u1 - u0) / nu, (v1 - v0) / nv
    U, V = np.meshgrid(u0 + (np.arange(nu) + 0.5) * pu, v1 - (np.arange(nv) + 0.5) * pv)
    return U.ravel(), V.ravel(), np.full(U.size, pu), np.full(U.size, pv)


def _cell_rays(U, V, PU, PV):
    """Rays from the focal spot to cell centres of the detector plane: (unit directions, solid angles sr, focal-spot-to-plane distances,
    cell on the active area)."""
    P = np.stack([np.full(U.size, SDD_M), U, V], -1)
    r = np.linalg.norm(P, axis=1)
    hu, hv = DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 2, DETECTOR_PIXELS[1] * PIXEL_PITCH_M / 2
    return P / r[:, None], PU * PV * SDD_M / r ** 3, r, (np.abs(U) < hu) & (np.abs(V) < hv)


@functools.lru_cache(maxsize=1)
def _ledger_grid():
    """Coarse cells of the energy ledger over the emission cone: 35 x 30 on the active area and about 8 mm (detector plane) between it and
    the cone, so that no cell straddles the edge of the active area; a cell belongs to the cone when its centre does.  Returns the cell
    centres and sizes (U, V, PU, PV) and their rays (directions, solid angles, distances, on the active area)."""
    c = SDD_M * math.tan(CONE_HALF_ANGLE_RAD)
    hu, hv = DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 2, DETECTOR_PIXELS[1] * PIXEL_PITCH_M / 2
    nu, nv = max(1, math.ceil((c - hu) / LEDGER_RING_CELL_M)), max(1, math.ceil((c - hv) / LEDGER_RING_CELL_M))
    blocks = [_cells(u0, u1, v0, v1, a, b) for u0, u1, a in ((-c, -hu, nu), (-hu, hu, 35), (hu, c, nu))
              for v0, v1, b in ((-c, -hv, nv), (-hv, hv, 30), (hv, c, nv))]
    U, V, PU, PV = (np.concatenate([bk[k] for bk in blocks]) for k in range(4))
    keep = U * U + V * V <= c * c
    U, V, PU, PV = U[keep], V[keep], PU[keep], PV[keep]
    return (U, V, PU, PV) + _cell_rays(U, V, PU, PV)


@functools.lru_cache(maxsize=256)
def _free_rays(kv_v, filter_position):
    """Energy per electron (J) of every coarse ledger ray that meets no object: absorbed in the air, deposited in and passing through the
    detector (rays on the active area), reaching the shielding (rays beside it)."""
    _, omega, r, det = _ledger_grid()[4:]
    e, ph = beam_spectrum(kv_v, filter_position)
    mu_air, _ = _mu_grid('nist_air', len(e))
    inc = ph * np.exp(-np.multiply.outer(r, mu_air))
    _, edep = detector_response(tuple(e))
    f = 0.5 * KEV
    return (f * omega * (float(ph @ e) - inc @ e), np.where(det, f * omega * (inc @ edep), 0.0),
            np.where(det, f * omega * (inc @ (e - edep)), 0.0), np.where(det, 0.0, f * omega * (inc @ e)))

DOOR_X_RANGE_M = (ENCLOSURE_X_M - 0.085, ENCLOSURE_X_M + 0.270)   # door opening in the asset frame (the chuck is reachable when the axis lies inside)
LOAD_POSITION = dict(sod=0.250, cz=0.250)
CENTER_POSITION = dict(sod=0.200, cz=0.300)


class BrukerX4PoseidonRuntime:
    """X4 POSEIDON (reflection source, flat panel, fixed geometry, standard rotation stage): power and safety, door, X-ray source, filter
    changer, sample carriage / lift / rotation, detector calibration, radiography, CT scans, FDK reconstruction and analysis (SI units)."""

    def __init__(self, instance_id='x4_poseidon_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.revision = 0
        self.plant = None
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.error = None
        self.estop = False
        self.faults = set()
        self.door, self.door_target = 0.0, 0.0
        self.xv_set, self.xi_set = 60.0e3, 150.0e-6
        self.x_enabled = False
        self.xv, self.xi = 0.0, 0.0
        self.warm_needed = True                                  # first enable after every boot runs the warm-up program
        self.warm_v, self.warm_i = 0.0, 0.0                      # end point of the running warm-up program (set point at its start)
        self.warm_end = None
        self.warm_from = None
        self.filter_pos, self.slide, self.slide_target = 0, 0.0, 0.0
        self.sod, self.sod_target = LOAD_POSITION['sod'], LOAD_POSITION['sod']
        self.cz, self.cz_target = LOAD_POSITION['cz'], LOAD_POSITION['cz']
        self.rot, self.rot_target = 0.0, 0.0
        self.position_label = 'load'
        self.scan_settings = dict(SCAN_EXAMPLE)
        self.flat = None                                         # dict(key, t, bright, dark, frames) of the last dark / bright calibration
        self.geo_cal = dict(axis_offset_m=None, calibrated_s=None)
        self.samples = {}
        self.on_stage = None
        self.records = {}
        self._data = {}                                          # record id -> simulated arrays (projections, volumes, images)
        self.seq = dict(ff=0, gc=0, rg=0, pj=0, vo=0, op=0)
        self.op = None                                           # acquisition operation (flat field, geometry calibration, radiograph, scan)
        self.recon = None                                        # reconstruction on the workstation (independent of the instrument)
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.truth = dict(axis_offset=0.0, defects=[])
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.last_porosity = None
        self.last_porosity_rid = None
        self.used_substances = set()
        self._rate_cache = {}
        self._principals, self._requests, self._owners = {}, {}, {}
        self._porosity_owner = None                              # principal of the last porosity analysis (the session sets it)

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
        return BrukerX4PoseidonSession(self, name)

    def _event(self, kind, code, message, rid=None, t=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t if t is None else t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _seed(self):
        return self.plant['noise_seed'] if self.plant else 0

    def _binning(self):
        return int(self.scan_settings['binning'][0])

    def _magnification(self, sod=None):
        return SDD_M / (self.sod if sod is None else sod)

    def _voxel_nominal(self, sod=None):
        return PIXEL_PITCH_M * self._binning() / self._magnification(sod)

    def _fov(self, sod=None):
        m = self._magnification(sod)
        return [DETECTOR_PIXELS[0] * PIXEL_PITCH_M / m, DETECTOR_PIXELS[1] * PIXEL_PITCH_M / m]

    def _door_closed(self):
        return self.door <= 1e-9 and self.door_target <= 1e-9

    def _door_moving(self):
        return abs(self.door - self.door_target) > 1e-12

    def _interlock_ok(self):
        return self._door_closed() and 'door_sensor' not in self.faults and not self.estop

    def _xray_on(self):
        return self.power and self.xv > 0.0 and self.xi > 0.0

    def _xray_ready(self):
        return (self._xray_on() and self.x_enabled and self.warm_end is None and abs(self.xv - self.xv_set) < 1e-6 and abs(self.xi - self.xi_set) < 1e-12)

    def _stage_moving(self):
        return abs(self.sod - self.sod_target) > 1e-12 or abs(self.cz - self.cz_target) > 1e-12 or abs(_rot_delta(self.rot, self.rot_target)) > 1e-12

    def _filter_moving(self):
        return abs(self.slide - self.slide_target) > 1e-12

    def _flat_key(self):
        s = self.scan_settings
        return f"{self.xv_set:.1f}|{self.xi_set * 1e9:.1f}|{self.filter_pos}|{s['binning']}|{s['exposure_s']:.6g}"

    def _flat_valid(self):
        return self.flat is not None and self.flat['key'] == self._flat_key() and self.flat.get('usable', True)

    def _nominal_projections(self, s=None):
        s = s or self.scan_settings
        span = self._scan_span(s)
        return int(math.floor(span / s['step_rad'] + 1e-9)) + (0 if s['range_deg'] == '360' else 1)

    def _scan_span(self, s=None):
        s = s or self.scan_settings
        if s['range_deg'] == '360':
            return 2 * math.pi
        half_fan = math.atan(DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 2 / SDD_M)
        return math.pi + 2 * half_fan                            # 180 deg + fan angle (short scan)

    def _projection_time(self, s=None):
        s = s or self.scan_settings
        return s['averaging'] * (s['exposure_s'] + FRAME_OVERHEAD_S) + STEP_OVERHEAD_S

    def _scan_duration(self, s=None):
        return self._nominal_projections(s) * self._projection_time(s)

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        o = self.op
        prog = rem = None
        if o is not None:
            prog = min(1.0, max(0.0, (self.t - o['t0']) / o['duration_s'])) if o['duration_s'] > 0 else 0.0
            rem = max(0.0, o['t0'] + o['duration_s'] - self.t)
        err = self.error is not None or self.estop
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.error_code': self.error, 'emergency.stopped': self.estop, 'door.position_m': self.door, 'door.target_m': self.door_target,
            'door.moving': self._door_moving(), 'door.closed': self._door_closed(), 'light_curtain.blocked': 'light_curtain_blocked' in self.faults,
            'interlock.ok': self._interlock_ok(), 'xray.voltage_set_v': self.xv_set, 'xray.current_set_a': self.xi_set, 'xray.enabled': self.x_enabled,
            'xray.voltage_v': self.xv, 'xray.current_a': self.xi, 'xray.power_w': self.xv * self.xi, 'xray.on': self._xray_on(), 'xray.ready': self._xray_ready(),
            'xray.warmup_remaining_s': max(0.0, self.warm_end - self.t) if self.warm_end is not None else 0.0,
            'xray.focal_spot_m': focal_spot_m(self.xv * self.xi if self._xray_on() else self.xv_set * self.xi_set),
            'filter.position': self.filter_pos, 'filter.label': filter_of(self.filter_pos)['label'], 'filter.slide_m': self.slide, 'filter.moving': self._filter_moving(),
            'stage.sod_m': self.sod, 'stage.sod_target_m': self.sod_target, 'stage.chuck_z_m': self.cz, 'stage.chuck_z_target_m': self.cz_target,
            'stage.rotation_rad': self.rot, 'stage.rotation_target_rad': self.rot_target, 'stage.moving': self._stage_moving(), 'stage.position_label': self.position_label,
            'geometry.magnification': self._magnification(), 'geometry.voxel_size_m': self._voxel_nominal(), 'geometry.fov_m': self._fov(),
            'scan.settings': dict(self.scan_settings), 'scan.projections': self._nominal_projections(), 'scan.duration_s': self._scan_duration(),
            'calibration.flat_field': dict(valid=self._flat_valid(), key=self.flat['key'] if self.flat else None, calibrated_s=self.flat['t'] if self.flat else None),
            'calibration.geometry': dict(self.geo_cal), 'measurement.id': o['record_id'] if o else None, 'measurement.kind': o['kind'] if o else None,
            'measurement.progress': prog, 'measurement.remaining_s': rem, 'measurement.started_by_other_agent': False,
            'recon.id': self.recon['record_id'] if self.recon else None,
            'recon.remaining_s': max(0.0, self.recon['t0'] + self.recon['duration_s'] - self.t) if self.recon else None,
            'result.last_record_id': self._last_record(), 'result.last_porosity': self.last_porosity,
            'sample.bench_ids': sorted(k for k, s in self.samples.items() if s['location'] == 'bench'),
            'sample.on_stage': (dict(sample_id=self.on_stage, offset_x_m=self.samples[self.on_stage]['offset_x_m'], offset_y_m=self.samples[self.on_stage]['offset_y_m'])
                                if self.on_stage else None),
            'panel.status_green': self.power and not err and not self._xray_on(), 'panel.status_orange': self.power and self._xray_on(),
            'panel.status_red': self.power and err,
            'truth.axis_offset_m': self.truth['axis_offset'], 'truth.samples': self._truth_samples(), 'truth.faults': sorted(self.faults),
            'truth.detector_defect_columns': list(self.truth['defects']),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _last_record(self, keep=None):
        done = [k for k, r in self.records.items() if r['status'] == 'completed' and (keep is None or keep(k))]
        return max(done, key=lambda k: (self.records[k]['finished_s'] or 0.0, k), default=None)

    def _truth_samples(self):
        out = {}
        for k, s in self.samples.items():
            d = {kk: copy.deepcopy(vv) for kk, vv in s.items() if kk != '_pores'}
            d['pores_generated'] = copy.deepcopy(s.get('_pores', []))
            d['truth'] = sample_truth(s)
            out[k] = d
        return out

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        a = coerce(CAPS[capability_id]['input_schema'], a)             # integral floats of (nested) integer fields -> int
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
        if self.estop:
            raise ActionRejected('EMERGENCY_STOP')
        if self.error is not None and not allow_error:
            raise ActionRejected('ERROR_PENDING')

    def _need_idle(self):
        if self.op is not None:
            raise ActionRejected('MEASUREMENT_RUNNING')

    def _need_still(self):
        if self._stage_moving() or self._filter_moving() or self._door_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')

    # ---------------------------------------------------------------- setup, samples, power, safety, faults
    def _cmd_plant_configure(self, room_k, mains_voltage_v, relative_humidity, noise_seed):
        first = self.plant is None
        if not first and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, relative_humidity=relative_humidity, noise_seed=noise_seed)
        if first:
            rng = random.Random(noise_seed * 7919 + 17)
            self.truth['axis_offset'] = rng.gauss(0.0, AXIS_OFFSET_SD_M)
        if self.power and not (MAINS_V[0] * 0.9 <= mains_voltage_v <= MAINS_V[1] * 1.1):
            self._power_off('MAINS_FAULT')
        return dict(plant=dict(self.plant))

    def _cmd_sample_provide(self, sample_id, kind, diameter_m, height_m, layers, pores, cracks, inclusions, container, pin):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('SAMPLE_EXISTS')
        if len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_SAMPLES')
        s = dict(sample_id=sample_id, kind=kind, diameter_m=diameter_m, height_m=height_m, layers=copy.deepcopy(layers), pores=copy.deepcopy(pores),
                 cracks=copy.deepcopy(cracks), inclusions=copy.deepcopy(inclusions), container=copy.deepcopy(container), pin=copy.deepcopy(pin),
                 location='bench', offset_x_m=0.0, offset_y_m=0.0)
        R = diameter_m / 2
        if kind == 'cylinder':
            if not layers or abs(sum(L['thickness_m'] for L in layers) - height_m) > 1e-9:
                raise ActionRejected('INVALID_SAMPLE')
            for L in layers:
                if abs(sum(m['volume_fraction'] for m in L['materials']) - 1.0) > 1e-6:
                    raise ActionRejected('INVALID_SAMPLE')
            zb = _layer_bounds(s)
            for c in cracks:
                nrm = np.asarray(c['normal'], float)
                if np.linalg.norm(nrm) < 0.5 or math.hypot(c['center_m'][0], c['center_m'][1]) + c['radius_m'] > R or not any(
                        zb[k] + c['radius_m'] <= c['center_m'][2] <= zb[k + 1] - c['radius_m'] for k in range(len(layers))):
                    raise ActionRejected('INVALID_SAMPLE')
            for i in inclusions:
                if math.hypot(i['center_m'][0], i['center_m'][1]) + i['radius_m'] > R or not any(
                        zb[k] + i['radius_m'] <= i['center_m'][2] <= zb[k + 1] - i['radius_m'] for k in range(len(layers))):
                    raise ActionRejected('INVALID_SAMPLE')
        elif layers or pores['count'] or cracks or inclusions:
            raise ActionRejected('INVALID_SAMPLE')                  # phantoms have fixed materials and no pores / cracks / inclusions
        for sid in _materials(s):
            substance(sid)
        s['_pores'] = generate_pores(s)
        self.samples[sample_id] = s
        self.used_substances.update(_materials(s))
        self._event('sample', 'SAMPLE_PROVIDED', f'样品{sample_id}放在实验台上')
        return dict(sample_id=sample_id, location='bench', mass_kg=sample_mass_kg(s))

    def _envelope(self, s, ox, oy):
        return sample_radius_total(s) + math.hypot(ox, oy)

    def _geometry_ok(self, sod, cz, sample_id=None, ox=None, oy=None):
        sid = self.on_stage if sample_id is None else sample_id
        if sid is None:
            return self._clear(sod, cz, None)
        s = self.samples[sid]
        ox = s['offset_x_m'] if ox is None else ox
        oy = s['offset_y_m'] if oy is None else oy
        return self._clear(sod, cz, s, ox, oy)

    def _clear(self, sod, cz, s, ox=0.0, oy=0.0):
        """Keep-out check of the moving column (sample body, pin, chuck, rotation-stage housing; each a vertical cylinder about the rotation axis)
        against the source head / filter slide and the detector housing where their heights overlap, and against the chamber ceiling."""
        bz = SOURCE_XYZ_M[2]
        segs = [(CHUCK_R_M, cz - CHUCK_H_M, cz), (STAGE_R_M, cz - CHUCK_H_M - STAGE_H_M, cz - CHUCK_H_M)]
        top = cz
        if s is not None:
            e = math.hypot(ox, oy)
            z0 = cz
            if s['pin']:
                segs.append((s['pin']['diameter_m'] / 2 + e, cz, cz + s['pin']['length_m']))
                z0 = cz + s['pin']['length_m']
            segs.append((sample_radius_total(s) + e, z0, z0 + s['height_m']))
            top = z0 + s['height_m']
        for r, za, zb_ in segs:
            if za < bz + SOURCE_KEEPOUT_DZ[1] and zb_ > bz + SOURCE_KEEPOUT_DZ[0] and sod - r < FILTER_CLEAR_M + COLLISION_MARGIN_M:
                return False
            if za < bz + DETECTOR_KEEPOUT_DZ[1] and zb_ > bz + DETECTOR_KEEPOUT_DZ[0] and SDD_M - DETECTOR_COVER_M - sod - r < COLLISION_MARGIN_M:
                return False
        return top <= CHAMBER_CEILING_M - COLLISION_MARGIN_M

    def _accessible(self):
        x_axis = SOURCE_XYZ_M[0] + self.sod
        return DOOR_X_RANGE_M[0] + 0.02 <= x_axis <= DOOR_X_RANGE_M[1] - 0.02

    def _cmd_sample_place(self, sample_id, offset_x_m, offset_y_m):
        self._need_plant()
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if self.on_stage is not None:
            raise ActionRejected('STAGE_OCCUPIED')
        self._need_idle()
        if self.door < DOOR_TRAVEL_M - 1e-9 or self._door_moving():
            raise ActionRejected('DOOR_CLOSED')
        if self._stage_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        if not self._accessible():
            raise ActionRejected('NOT_ACCESSIBLE')
        if not self._geometry_ok(self.sod, self.cz, sample_id, offset_x_m, offset_y_m):
            raise ActionRejected('COLLISION_RISK')
        s['location'], s['offset_x_m'], s['offset_y_m'] = 'stage', offset_x_m, offset_y_m
        self.on_stage = sample_id
        self._rate_cache.clear()
        self._event('sample', 'SAMPLE_PLACED', f'样品{sample_id}装到样品夹（偏移{offset_x_m * 1e3:.2f}, {offset_y_m * 1e3:.2f} mm）')
        return dict(sample_id=sample_id, offset_x_m=offset_x_m, offset_y_m=offset_y_m)

    def _cmd_sample_remove(self, sample_id):
        self._need_plant()
        if self.on_stage != sample_id:
            raise ActionRejected('SAMPLE_NOT_ON_STAGE')
        self._need_idle()
        if self.door < DOOR_TRAVEL_M - 1e-9 or self._door_moving():
            raise ActionRejected('DOOR_CLOSED')
        if self._stage_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        if not self._accessible():
            raise ActionRejected('NOT_ACCESSIBLE')
        s = self.samples[sample_id]
        s['location'], s['offset_x_m'], s['offset_y_m'] = 'bench', 0.0, 0.0
        self.on_stage = None
        self._rate_cache.clear()
        self._event('sample', 'SAMPLE_REMOVED', f'样品{sample_id}取回实验台')
        return dict(sample_id=sample_id)

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        if active:
            self.faults.add(fault)
            code = FAULT_ERROR.get(fault)
            if code and self.power:
                self._raise_error(code)
            if fault == 'detector_defects' and not self.truth['defects']:
                g = random.Random(self._seed() * 31 + 3)
                self.truth['defects'] = sorted(g.sample(range(20, SIM_DETECTOR[0] - 20), 3))
            if fault == 'xray_arc':
                self._beam_off('XRAY_ARC')
            if fault == 'rotation_stall':
                self.rot_target = self.rot
                if self.op is not None and self.op['kind'] in ('projections', 'geometry_calibration'):
                    self._fail_op('STAGE_FAULT')
            if fault == 'door_sensor':
                self._beam_off('INTERLOCK_FAULT')
            if fault == 'light_curtain_blocked' and self.door_target < self.door - 1e-12:
                self.door_target = DOOR_TRAVEL_M                    # closing door stops and reopens
                self._event('safety', 'LIGHT_CURTAIN', '安全光幕被挡：门停止关闭并重新打开')
        else:
            self.faults.discard(fault)
            if fault == 'detector_defects':
                self.truth['defects'] = []
        self._event('fault', 'FAULT_' + ('ON' if active else 'OFF'), fault)
        return dict(faults=sorted(self.faults))

    def _raise_error(self, code):
        if self.error is None:
            self.error = code
            self._event('error', code, ERROR_ZH.get(code, code))

    def _beam_off(self, code=None):
        """Switch the X-ray off at once (interlock, arc, emergency stop); a running exposure fails."""
        was_on = self._xray_on() or self.x_enabled
        self.x_enabled = False
        self.xv = self.xi = 0.0
        self.warm_end = None
        if self.op is not None and self.op['kind'] in ('flat_field', 'geometry_calibration', 'radiograph', 'projections'):
            self._fail_op(code or 'XRAY_OFF')
        if was_on:
            self._event('xray', 'XRAY_OFF', 'X射线关闭' + (f'（{code}）' if code else ''))

    def _cmd_power_press_button(self):
        return self._cmd_power_set(not self.power)

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            if not (MAINS_V[0] * 0.9 <= self.plant['mains_voltage_v'] <= MAINS_V[1] * 1.1):
                raise ActionRejected('MAINS_OUT_OF_RANGE')
            self.power, self.state, self.state_t0 = True, 'booting', self.t
            self.warm_needed = True
            if self.error == 'MAINS_FAULT':
                self.error = None
            self._latch_persisting_causes()
            self._event('power', 'POWER_ON', '系统通电，启动（各轴保持断电时的位置）')
        elif not enabled and self.power:
            self._power_off(None)
        return dict(enabled=self.power, state=self.state)

    def _power_off(self, code):
        self._beam_off('POWER_OFF')
        if self.op is not None:
            self._fail_op('POWER_OFF')
        self.power, self.state = False, 'off'
        self.sod_target, self.cz_target, self.rot_target = self.sod, self.cz, self.rot
        self.door_target = self.door
        self.slide_target = self.slide
        if code:
            if self.error is None:
                self.error = code
            self._event('error', code, ERROR_ZH.get(code, code))
        self._event('power', 'POWER_OFF', '系统断电：X射线关闭，各轴停止')

    def _cmd_emergency_stop(self):
        self._need_plant()
        if not self.estop:
            self.estop = True
            self._beam_off('EMERGENCY_STOP')
            if self.op is not None:
                self._fail_op('EMERGENCY_STOP')
            self.sod_target, self.cz_target, self.rot_target = self.sod, self.cz, self.rot
            self.door_target = self.door
            self.slide_target = self.slide
            if self.power:
                self._raise_error('EMERGENCY_STOP')
            self._event('safety', 'EMERGENCY_STOP', '安全按钮按下：X射线关闭，所有运动停止')
        return dict(stopped=True)

    def _cmd_emergency_release(self):
        self._need_plant()
        if self.estop:
            self.estop = False
            self._event('safety', 'EMERGENCY_RELEASED', '安全按钮已释放（需确认错误）')
        return dict(stopped=False)

    def _cmd_system_acknowledge(self):
        self._need_power()
        if self.error is None:
            return dict(cleared=None)
        cause = {v: k for k, v in FAULT_ERROR.items()}.get(self.error)
        if cause in self.faults or (self.error == 'EMERGENCY_STOP' and self.estop) or \
                (self.error == 'MAINS_FAULT' and not (MAINS_V[0] * 0.9 <= self.plant['mains_voltage_v'] <= MAINS_V[1] * 1.1)):
            raise ActionRejected('CAUSE_PERSISTS')
        cleared, self.error = self.error, None
        self._event('error', 'ACKNOWLEDGED', f'确认错误{cleared}')
        if self.power:
            self._latch_persisting_causes()                             # another cause still present latches its own error
        return dict(cleared=cleared)

    # ---------------------------------------------------------------- door, X-ray, filter, stage
    def _cmd_door_open(self):
        self._need_ready()
        self._need_idle()
        if self._xray_on() or self.x_enabled:
            raise ActionRejected('XRAY_ON')
        self.door_target = DOOR_TRAVEL_M
        return dict(target_m=DOOR_TRAVEL_M, duration_s=abs(DOOR_TRAVEL_M - self.door) / DOOR_SPEED_M_S)

    def _cmd_door_close(self):
        self._need_ready()
        if 'light_curtain_blocked' in self.faults:
            raise ActionRejected('LIGHT_CURTAIN_BLOCKED')
        self.door_target = 0.0
        return dict(target_m=0.0, duration_s=self.door / DOOR_SPEED_M_S)

    def _cmd_xray_set(self, voltage_v, current_a):
        self._need_plant()
        if voltage_v * current_a > XRAY_POWER_MAX_W + 1e-9:
            raise ActionRejected('POWER_LIMIT')
        if self.op is not None and self.op['kind'] in ('flat_field', 'geometry_calibration', 'radiograph', 'projections'):
            raise ActionRejected('MEASUREMENT_RUNNING')
        self.xv_set, self.xi_set = voltage_v, current_a
        return dict(voltage_v=voltage_v, current_a=current_a, power_w=voltage_v * current_a, focal_spot_m=focal_spot_m(voltage_v * current_a))

    def _cmd_xray_enable(self, enabled):
        if not enabled:
            self._need_plant()
            if self.op is not None and self.op['kind'] in ('flat_field', 'geometry_calibration', 'radiograph', 'projections'):
                raise ActionRejected('MEASUREMENT_RUNNING')
            self._beam_off(None)
            return dict(enabled=False, warmup_s=0.0, ramp_s=0.0)
        self._need_ready()
        if not self._door_closed():
            raise ActionRejected('DOOR_OPEN')
        if not self._interlock_ok():
            raise ActionRejected('INTERLOCK_OPEN')
        if 'xray_arc' in self.faults:
            raise ActionRejected('XRAY_ARC')
        if self.x_enabled:
            return dict(enabled=True, warmup_s=max(0.0, self.warm_end - self.t) if self.warm_end else 0.0, ramp_s=self._ramp_time())
        self.x_enabled = True
        warm = 0.0
        if self.warm_needed:
            self.warm_from, self.warm_end = self.t, self.t + WARMUP_S
            self.warm_v, self.warm_i = self.xv_set, self.xi_set
            warm = WARMUP_S
            self._event('xray', 'WARMUP', f'X射线预热程序{WARMUP_S:.0f} s')
        self._event('xray', 'XRAY_ON', 'X射线开启')
        self._rate_cache.clear()
        return dict(enabled=True, warmup_s=warm, ramp_s=0.0 if warm else self._ramp_time())

    def _ramp_time(self):
        return max(abs(self.xv_set - self.xv) / KV_RAMP_V_S, abs(self.xi_set - self.xi) / CURRENT_RAMP_A_S)

    def _cmd_filter_select(self, position):
        self._need_ready()
        self._need_idle()
        self.filter_pos = position
        self.slide_target = position * FILTER_PITCH_M
        self._rate_cache.clear()
        return dict(filter=filter_of(position)['label'], duration_s=abs(self.slide_target - self.slide) / (FILTER_PITCH_M / FILTER_S_PER_STEP))

    def _stage_checks(self):
        self._need_ready()
        if 'rotation_stall' in self.faults:
            raise ActionRejected('STAGE_FAULT')
        self._need_idle()

    def _latch_persisting_causes(self):
        """Latch the error of the highest-priority cause that is still present (fixed order: safety button, interlock sensor, tube arc,
        rotation stall)."""
        if self.estop:
            self._raise_error('EMERGENCY_STOP')
        for f in FAULT_PRIORITY:
            if f in self.faults and FAULT_ERROR.get(f):
                self._raise_error(FAULT_ERROR[f])

    def _forbidden_boxes(self, s, ox, oy):
        """Open rectangles in the (SOD, chuck height) plane that the moving column must not enter (source head / filter slide and detector
        housing for each of its cylinders: stage housing, chuck, pin, sample) and the highest allowed chuck height (ceiling)."""
        bz = SOURCE_XYZ_M[2]
        segs = [(CHUCK_R_M, -CHUCK_H_M, 0.0), (STAGE_R_M, -CHUCK_H_M - STAGE_H_M, -CHUCK_H_M)]
        top = 0.0
        if s is not None:
            ecc = math.hypot(ox, oy)
            z0 = 0.0
            if s['pin']:
                segs.append((s['pin']['diameter_m'] / 2 + ecc, 0.0, s['pin']['length_m']))
                z0 = s['pin']['length_m']
            segs.append((sample_radius_total(s) + ecc, z0, z0 + s['height_m']))
            top = z0 + s['height_m']
        boxes = []
        for r, a, b in segs:
            boxes.append(((-math.inf, FILTER_CLEAR_M + COLLISION_MARGIN_M + r), (bz + SOURCE_KEEPOUT_DZ[0] - b, bz + SOURCE_KEEPOUT_DZ[1] - a)))
            boxes.append(((SDD_M - DETECTOR_COVER_M - COLLISION_MARGIN_M - r, math.inf), (bz + DETECTOR_KEEPOUT_DZ[0] - b, bz + DETECTOR_KEEPOUT_DZ[1] - a)))
        return boxes, CHAMBER_CEILING_M - COLLISION_MARGIN_M - top

    def _path_ok(self, sod1, cz1):
        """Swept-path keep-out check: carriage and lift start together at their own speeds, so the column follows two straight segments in
        the (SOD, chuck height) plane; neither may cross a forbidden rectangle."""
        s = self.samples[self.on_stage] if self.on_stage else None
        boxes, cz_max = self._forbidden_boxes(s, s['offset_x_m'] if s else 0.0, s['offset_y_m'] if s else 0.0)
        s0, c0 = self.sod, self.cz
        ts, tz = abs(sod1 - s0) / CARRIAGE_SPEED_M_S, abs(cz1 - c0) / LIFT_SPEED_M_S
        if ts <= tz:                                    # the carriage arrives first: the corner lies exactly on its target
            mid = (sod1, c0 + math.copysign(LIFT_SPEED_M_S * ts, cz1 - c0) if tz > 0 else c0)
        else:
            mid = (s0 + math.copysign(CARRIAGE_SPEED_M_S * tz, sod1 - s0), cz1)
        pts = [(s0, c0), mid, (sod1, cz1)]
        if max(p[1] for p in pts) > cz_max + 1e-12:
            return False
        return not any(_segment_hits_box(pts[k], pts[k + 1], box) for k in range(2) for box in boxes)

    def _recon_region(self, data, axis):
        """Reconstructable region of an acquisition window (as in _fdk): radius about the axis and the height range at that radius, less the
        +-200 um the automatic alignment may shift the axis."""
        w, sod = data['window'], data['sod']
        us = (w['u0'] + (np.arange(w['nu']) + 0.5) * w['pitch'] - axis * SDD_M / sod) * sod / SDD_M
        vs = (w['v1'] - (np.arange(w['nv']) + 0.5) * w['pitch']) * sod / SDD_M
        fov_r = min(abs(us.min()), abs(us.max())) * sod / math.hypot(sod, abs(us).max()) - 200e-6
        return fov_r, float(vs.min()) * (sod - fov_r) / sod, float(vs.max()) * (sod - fov_r) / sod

    def _move_stage(self, sod, cz, label=None):
        if not self._geometry_ok(sod, cz) or not self._path_ok(sod, cz):
            raise ActionRejected('COLLISION_RISK')
        self.sod_target, self.cz_target = sod, cz
        self.position_label = label
        d = max(abs(sod - self.sod) / CARRIAGE_SPEED_M_S, abs(cz - self.cz) / LIFT_SPEED_M_S)
        return dict(target=[sod, cz], duration_s=d, magnification=SDD_M / sod)

    def _cmd_stage_move(self, sod_m, chuck_z_m):
        self._stage_checks()
        out = self._move_stage(sod_m, chuck_z_m)
        return out

    def _cmd_stage_go_to(self, position):
        self._stage_checks()
        p = LOAD_POSITION if position == 'load' else CENTER_POSITION
        out = self._move_stage(p['sod'], p['cz'], position)
        return dict(target=out['target'], duration_s=out['duration_s'])

    def _cmd_stage_rotate(self, angle_rad):
        self._stage_checks()
        self.rot_target = angle_rad % (2 * math.pi)
        return dict(target_rad=self.rot_target, duration_s=abs(_rot_delta(self.rot, self.rot_target)) / ROTATION_SPEED_RAD_S)

    def _cmd_scan_configure(self, range_deg, step_rad, exposure_s, averaging, binning, random_movement):
        self._need_plant()
        self._need_idle()
        s = dict(range_deg=range_deg, step_rad=step_rad, exposure_s=exposure_s, averaging=averaging, binning=binning, random_movement=random_movement)
        n = self._nominal_projections(s)
        self.scan_settings = s
        return dict(settings=dict(s), projections=n, duration_s=self._scan_duration(s))

    # ---------------------------------------------------------------- operations
    def _new_op(self, kind, rid=None):
        self.seq['op'] += 1
        oid = f'op{self.seq["op"]:05d}'
        self.operations[oid] = dict(operation_id=oid, kind=kind, phase='running', record_id=rid, started_s=self.t, reason=None)
        return oid

    def _new_record_id(self, prefix, key):
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        self.seq[key] += 1
        return f'{prefix}{self.seq[key]:04d}'

    def _acq_checks(self, need_sample=True, need_flat=True):
        self._need_ready()
        self._need_idle()
        self._need_still()
        if not self._door_closed():
            raise ActionRejected('DOOR_OPEN')
        if not self._xray_ready():
            raise ActionRejected('XRAY_NOT_READY')
        if 'rotation_stall' in self.faults:
            raise ActionRejected('STAGE_FAULT')
        if need_sample and self.on_stage is None:
            raise ActionRejected('NO_SAMPLE')
        if need_flat and not self._flat_valid():
            raise ActionRejected('FLAT_FIELD_REQUIRED')

    def _xsnap(self):
        return dict(voltage_v=self.xv_set, current_a=self.xi_set, power_w=self.xv_set * self.xi_set, filter=filter_of(self.filter_pos)['label'],
                    focal_spot_m=focal_spot_m(self.xv_set * self.xi_set))

    def _record_stub(self, rid, kind, ctx):
        r = dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, reason=None, sample_id=ctx.get('sample_id'),
                 xray=self._xsnap() if kind != 'volume' else None, scan=dict(ctx['scan']) if ctx.get('scan') else None, sod_m=ctx.get('sod'), sdd_m=SDD_M,
                 magnification=SDD_M / ctx['sod'] if ctx.get('sod') else None,
                 voxel_size_nominal_m=PIXEL_PITCH_M * int(ctx['scan']['binning'][0]) * ctx['sod'] / SDD_M if ctx.get('scan') and ctx.get('sod') else None,
                 projections_nominal=ctx.get('n_nom', 0), projections_simulated=ctx.get('n_sim', 0), sim_detector=list(SIM_DETECTOR), sim_pixel_m=None, transmission=None,
                 saturated_fraction=None, dose_gy=None, duration_s=0.0, preview=None, preview_unit=None, source_record_id=ctx.get('source'), recon=None, volume=None,
                 flat_field=None, geometry=None, warnings=[], template_source=dict(library=LIBRARY_REL, substances=sorted(ctx.get('materials', []))), note_zh='')
        self.records[rid] = r
        return r

    def _start_op(self, kind, record_id, items, ctx):
        oid = self._new_op(kind, record_id)
        t, ends = self.t, []
        for it in items:
            t += it[1]
            ends.append(t)
        self.op = dict(kind=kind, op=oid, record_id=record_id, t0=self.t, duration_s=ends[-1] - self.t if ends else 0.0, items=items, ends=ends, i=-1,
                       phase=None, ctx=ctx)
        self._next_item()
        return dict(operation_id=oid)

    def _next_item(self):
        o = self.op
        while o is not None:
            o['i'] += 1
            if o['i'] >= len(o['items']):
                return
            it = o['items'][o['i']]
            o['phase'] = it[0]
            if it[0] == 'rotate':                                # scan rotation driven by the phase clock (a full turn returns to the start)
                o['rot0'], o['rot_amount'], o['rot_t0'], o['rot_dur'] = self.rot, it[2], self.t, it[1]
            elif it[0] == 'call':
                getattr(self, it[2])(*it[3:])
                if self.op is not o:
                    return
            if self.t < o['ends'][o['i']] - 1e-12:
                return

    def _ctx(self, kind, materials=None):
        s = self.samples.get(self.on_stage) if self.on_stage else None
        return dict(kind=kind, sample_id=self.on_stage, sod=self.sod, cz=self.cz, rot0=self.rot, scan=dict(self.scan_settings), kv=self.xv_set, ia=self.xi_set,
                    filter=self.filter_pos, seed=self._op_seed(kind), axis=self.truth['axis_offset'], defects=list(self.truth['defects']),
                    materials=_materials(s) if s else [], flat=self.flat, offset=(s['offset_x_m'], s['offset_y_m']) if s else (0.0, 0.0),
                    axis_known=self.geo_cal['axis_offset_m'])

    def _op_seed(self, tag):
        self.seq[tag] = self.seq.get(tag, 0) + 1
        return int(hashlib.sha256(f'{self._seed()}|{tag}|{self.seq[tag]}'.encode()).hexdigest()[:12], 16)

    def _cmd_calibration_flat_field(self):
        self._acq_checks(need_sample=False, need_flat=False)
        if self._anything_in_field():
            raise ActionRejected('SAMPLE_IN_BEAM')
        rid = self._new_record_id('FF', 'ff')
        ctx = self._ctx('flat_field')
        self._record_stub(rid, 'flat_field', ctx)
        tf = FLAT_FIELD_FRAMES * (self.scan_settings['exposure_s'] + FRAME_OVERHEAD_S)
        items = [('dark', tf), ('bright', tf), ('call', 0.0, '_finish_flat_field')]
        return self._start_op('flat_field', rid, items, ctx)

    def _cyl_on_detector(self, r, zb, zt, full=False):
        """Does a vertical cylinder of radius r about the rotation axis between heights zb..zt above the beam project onto the detector
        (full=True: completely)?"""
        half_u, half_v = DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 2, DETECTOR_PIXELS[1] * PIXEL_PITCH_M / 2
        sod = self.sod
        near, far = sod - r, sod + r
        uc = SDD_M * self.truth['axis_offset'] / sod
        uh = SDD_M * r / math.sqrt(max(sod * sod - r * r, 1e-12))
        vs = [SDD_M * z / d for z in (zb, zt) for d in (near, far)]
        if full:
            return abs(uc) + uh <= half_u and -half_v <= min(vs) and max(vs) <= half_v
        return abs(uc) - uh < half_u and max(vs) > -half_v and min(vs) < half_v

    def _sample_in_field(self):
        """Does the sample body (envelope cylinder, container included; not the mounting pin) project onto the detector?"""
        s = self.samples[self.on_stage]
        zb = self.cz - SOURCE_XYZ_M[2] + (s['pin']['length_m'] if s['pin'] else 0.0)
        return self._cyl_on_detector(self._envelope(s, s['offset_x_m'], s['offset_y_m']), zb, zb + s['height_m'])

    def _anything_in_field(self):
        """Sample, mounting pin, chuck or rotation-stage housing in the beam (a flat field must see the empty beam)."""
        z = self.cz - SOURCE_XYZ_M[2]
        if self._cyl_on_detector(STAGE_HOUSING_R_M, z - CHUCK_H_M - STAGE_H_M, z):
            return True
        if self.on_stage is None:
            return False
        s = self.samples[self.on_stage]
        return self._cyl_on_detector(self._envelope(s, s['offset_x_m'], s['offset_y_m']), z, z + sample_height_total(s))

    def _cmd_calibration_geometry(self):
        self._acq_checks(need_sample=True)
        s = self.samples[self.on_stage]
        if s['kind'] != 'calibration_phantom':
            raise ActionRejected('PHANTOM_REQUIRED')
        zb = self.cz - SOURCE_XYZ_M[2] + (s['pin']['length_m'] if s['pin'] else 0.0)
        if not self._cyl_on_detector(self._envelope(s, s['offset_x_m'], s['offset_y_m']), zb, zb + s['height_m'], full=True):
            raise ActionRejected('PHANTOM_NOT_IN_FIELD')                  # every ball must stay on the detector at every angle
        rid = self._new_record_id('GC', 'gc')
        ctx = self._ctx('geometry_calibration')
        ctx['n_nom'] = ctx['n_sim'] = 36
        self._record_stub(rid, 'geometry_calibration', ctx)
        tp = self._projection_time()
        items = [('rotate', 36 * tp, 2 * math.pi), ('call', 0.0, '_finish_geometry_calibration')]
        return self._start_op('geometry_calibration', rid, items, ctx)

    def _cmd_xray_radiograph(self, roi):
        self._acq_checks(need_sample=False)
        if roi is not None:
            half = roi['size_px'] / 2 * PIXEL_PITCH_M * self._binning()
            if abs(roi['center_u_m']) + half > DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 2 + 1e-12 or \
                    abs(roi['center_v_m']) + half > DETECTOR_PIXELS[1] * PIXEL_PITCH_M / 2 + 1e-12:
                raise ActionRejected('ROI_OUTSIDE_DETECTOR')
        rid = self._new_record_id('RG', 'rg')
        ctx = self._ctx('radiograph')
        ctx['roi'] = copy.deepcopy(roi)
        ctx['n_nom'] = ctx['n_sim'] = 1
        self._record_stub(rid, 'radiograph', ctx)
        s = self.scan_settings
        items = [('expose', s['averaging'] * (s['exposure_s'] + FRAME_OVERHEAD_S)), ('call', 0.0, '_finish_radiograph')]
        return self._start_op('radiograph', rid, items, ctx)

    def _cmd_scan_start(self):
        self._acq_checks(need_sample=True)
        if not self._sample_in_field():
            raise ActionRejected('SAMPLE_NOT_IN_FIELD')
        rid = self._new_record_id('PJ', 'pj')
        ctx = self._ctx('projections')
        n = self._nominal_projections()
        ctx['n_nom'] = n
        ctx['n_sim'] = min(n, SIM_MAX_PROJECTIONS)
        ctx['span'] = self._scan_span()
        ctx['dose0'] = self.samples[self.on_stage].get('dose_gy', 0.0)
        self._record_stub(rid, 'projections', ctx)
        items = [('rotate', n * self._projection_time(), ctx['span']), ('call', 0.0, '_finish_scan')]
        return self._start_op('projections', rid, items, ctx)

    def _cmd_records_delete(self, record_id):
        self._need_plant()
        r = self.records.get(record_id)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if r['status'] == 'running' or (self.recon is not None and self.recon['ctx']['source'] == record_id):
            raise ActionRejected('RECORD_IN_USE')
        del self.records[record_id]
        self._data.pop(record_id, None)
        if self.last_porosity_rid == record_id:
            self.last_porosity = self.last_porosity_rid = None
        self._event('measurement', 'RECORD_DELETED', f'记录{record_id}已删除', rid=record_id)
        return dict(record_id=record_id, remaining=len(self.records))

    def _cmd_recon_start(self, record_id, grid, filter, beam_hardening, bh_material, ring_correction, smoothing_vox, post_alignment, auto_alignment, voi):
        self._need_plant()
        src = self.records.get(record_id)
        if src is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if src['kind'] != 'projections' or src['status'] != 'completed':
            raise ActionRejected('RECORD_NOT_SUITABLE')
        if bh_material is not None:
            substance(bh_material)
        if self.recon is not None:
            raise ActionRejected('RECON_BUSY')
        data = self._data.get(record_id)
        if data is None:
            raise ActionRejected('DATA_NOT_CACHED')
        voi_partial = False
        if voi is not None:
            fov_r, z_lo, z_hi = self._recon_region(data, self.geo_cal['axis_offset_m'] or 0.0)
            cx, cy, cz_ = voi['center_m']
            sx, sy, sz = voi['size_m']
            if math.hypot(cx, cy) > fov_r or not z_lo <= cz_ <= z_hi:
                raise ActionRejected('INVALID_VOI')                      # the centre must lie in the reconstructable region
            voi_partial = math.hypot(abs(cx) + sx / 2, abs(cy) + sy / 2) > fov_r + 1e-12 or cz_ - sz / 2 < z_lo or cz_ + sz / 2 > z_hi
        rid = self._new_record_id('VO', 'vo')
        n = int(grid)
        ctx = dict(kind='volume', source=record_id, sample_id=src['sample_id'], sod=src['sod_m'], scan=src['scan'],
                   recon=dict(grid=grid, filter=filter, beam_hardening=beam_hardening, bh_material=bh_material, ring_correction=ring_correction,
                              smoothing_vox=smoothing_vox, post_alignment=post_alignment, auto_alignment=auto_alignment, voi=copy.deepcopy(voi)),
                   n_nom=src['projections_nominal'], n_sim=src['projections_simulated'], materials=src['template_source']['substances'],
                   axis_cal=self.geo_cal['axis_offset_m'], voi_partial=voi_partial)
        r = self._record_stub(rid, 'volume', ctx)
        r['recon'] = dict(ctx['recon'])
        r['xray'] = copy.deepcopy(src['xray'])
        b = int(src['scan']['binning'][0])
        frac = 1.0 if voi is None else min(1.0, float(np.prod(voi['size_m'])) / (self._fov(src['sod_m'])[0] ** 2 * self._fov(src['sod_m'])[1]))
        t = (DETECTOR_PIXELS[0] / b) ** 2 * (DETECTOR_PIXELS[1] / b) * frac * src['projections_nominal'] / RECON_RATE_VOXEL_PROJ_S + RECON_OVERHEAD_S
        oid = self._new_op('recon', rid)
        self.recon = dict(kind='recon', op=oid, record_id=rid, t0=self.t, duration_s=t, ctx=ctx)
        return dict(operation_id=oid)

    def _complete(self, o, r, dur=None):
        r.update(status='completed', finished_s=self.t, duration_s=self.t - r['started_s'] if dur is None else dur)
        self.operations[o['op']].update(phase='completed', finished_s=self.t)
        self._event('measurement', 'OP_DONE', f'操作{o["op"]}完成，记录{r["record_id"]}', rid=r['record_id'])
        if self.recon is o:
            self.recon = None
        else:
            self.op = None

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
        self.rot_target = self.rot

    def _cancel_op(self, op):
        if self.recon is not None and self.recon['op'] == op['operation_id']:
            o = self.recon
            self.operations[o['op']].update(phase='cancelled', finished_s=self.t, reason='USER_CANCEL')
            r = self.records[o['record_id']]
            r.update(status='cancelled', finished_s=self.t, reason='USER_CANCEL', duration_s=self.t - r['started_s'])
            self._event('measurement', 'OP_CANCELLED', f'重建{o["op"]}取消', rid=o['record_id'])
            self.recon = None
            return
        o = self.op
        if o is None or o['op'] != op['operation_id']:
            raise ActionRejected('NOT_RUNNING')
        self.operations[o['op']].update(phase='cancelled', finished_s=self.t, reason='USER_CANCEL')
        if o['record_id'] in self.records:
            r = self.records[o['record_id']]
            r.update(status='cancelled', finished_s=self.t, reason='USER_CANCEL', duration_s=self.t - r['started_s'])
        self._event('measurement', 'OP_CANCELLED', f'操作{o["op"]}取消', rid=o['record_id'])
        self.op = None
        self.rot_target = self.rot

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

    def _xray_targets(self):
        """Voltage and current the tube is driven towards now (warm-up program: linear from 0 to the set point over WARMUP_S)."""
        if not (self.power and self.x_enabled and self._interlock_ok()):
            return 0.0, 0.0
        if self.warm_end is not None:
            f = min(1.0, max(0.0, (self.t - self.warm_from) / WARMUP_S))
            return self.warm_v * f, self.warm_i * f
        return self.xv_set, self.xi_set

    def _next_event_dt(self):
        c = [math.inf]
        if self.state == 'booting':
            c.append(self.state_t0 + BOOT_S - self.t)
        if self.power:
            if self._door_moving():
                c.append(abs(self.door_target - self.door) / DOOR_SPEED_M_S)
            if self._filter_moving():
                c.append(abs(self.slide_target - self.slide) / (FILTER_PITCH_M / FILTER_S_PER_STEP))
            if abs(self.sod - self.sod_target) > 1e-12:
                c.append(abs(self.sod_target - self.sod) / CARRIAGE_SPEED_M_S)
            if abs(self.cz - self.cz_target) > 1e-12:
                c.append(abs(self.cz_target - self.cz) / LIFT_SPEED_M_S)
            if not (self.op and self.op.get('phase') == 'rotate') and abs(_rot_delta(self.rot, self.rot_target)) > 1e-12:
                c.append(abs(_rot_delta(self.rot, self.rot_target)) / ROTATION_SPEED_RAD_S)
            if self.warm_end is not None:
                c.append(self.warm_end - self.t)
            elif self.x_enabled:
                tv, ti = self._xray_targets()
                if abs(self.xv - tv) > 1e-6:
                    c.append(abs(tv - self.xv) / KV_RAMP_V_S)
                if abs(self.xi - ti) > 1e-12:
                    c.append(abs(ti - self.xi) / CURRENT_RAMP_A_S)
        o = self.op
        if o is not None and 0 <= o['i'] < len(o['ends']):
            c.append(o['ends'][o['i']] - self.t)
        if self.recon is not None:
            c.append(self.recon['t0'] + self.recon['duration_s'] - self.t)
        if self._beam_live():
            c += self._cell_boundary_dt()
        return max(0.0, min(c))

    def _beam_live(self):
        """The tube emits now or starts to within the next sub-step (ramping up from zero)."""
        return self.power and (self._xray_on() or (self.x_enabled and self._interlock_ok()))

    def _cell_boundary_dt(self):
        """Times until a moving quantity crosses the next boundary of the energy-rate cells (rotation 2 pi / 32, carriage and lift 0.25 mm,
        filter slide one position pitch, voltage 1 kV): every sub-step with the beam on then lies inside one cell, so the booked energy does not
        depend on the host steps."""
        c = []
        o = self.op
        if o is not None and o.get('phase') == 'rotate' and o['rot_dur'] > 0:
            w = o['rot_amount'] / o['rot_dur']
        else:
            d = _rot_delta(self.rot, self.rot_target)
            w = math.copysign(ROTATION_SPEED_RAD_S, d) if abs(d) > 1e-12 else 0.0
        if w:
            c.append(_to_boundary(self.rot, w, RATE_ROT_CELL_RAD))
        for x, tgt, v in ((self.sod, self.sod_target, CARRIAGE_SPEED_M_S), (self.cz, self.cz_target, LIFT_SPEED_M_S)):
            if abs(tgt - x) > 1e-12:
                c.append(_to_boundary(x, math.copysign(v, tgt - x), RATE_POS_CELL_M))
        if abs(self.slide_target - self.slide) > 1e-12:
            c.append(_to_boundary(self.slide, math.copysign(FILTER_PITCH_M / FILTER_S_PER_STEP, self.slide_target - self.slide), FILTER_PITCH_M))
        if self.warm_end is not None:
            rate = self.warm_v / WARMUP_S
        else:
            tv, _ = self._xray_targets()
            rate = math.copysign(KV_RAMP_V_S, tv - self.xv) if abs(tv - self.xv) > 1e-6 else 0.0
        if rate:
            c.append(_to_boundary(self.xv, rate, RATE_KV_CELL_V))
        return c

    def _advance(self, h):
        if h <= 0:
            return
        if self.power:
            v0, i0, rot0, sod0, cz0, sl0 = self.xv, self.xi, self.rot, self.sod, self.cz, self.slide
            was = self.door != self.door_target
            self.door = _axis_move(self.door, self.door_target, DOOR_SPEED_M_S, h)
            if was and self.door == self.door_target:
                self.led['door_cycles'].add(0.5)
                self._event('door', 'DOOR_' + ('OPEN' if self.door > 0 else 'CLOSED'), '门已打开' if self.door > 0 else '门已关闭', t=self.t + h)
            self.slide = _axis_move(self.slide, self.slide_target, FILTER_PITCH_M / FILTER_S_PER_STEP, h)
            new = _axis_move(self.sod, self.sod_target, CARRIAGE_SPEED_M_S, h)
            self.led['carriage_travel_m'].add(abs(new - self.sod)); self.sod = new
            new = _axis_move(self.cz, self.cz_target, LIFT_SPEED_M_S, h)
            self.led['lift_travel_m'].add(abs(new - self.cz)); self.cz = new
            o = self.op
            if o is not None and o.get('phase') == 'rotate':
                f = min(1.0, (self.t + h - o['rot_t0']) / o['rot_dur']) if o['rot_dur'] > 0 else 1.0
                new = (o['rot0'] + o['rot_amount'] * f) % (2 * math.pi)
                self.led['rotation_rad'].add(abs(o['rot_amount']) * h / o['rot_dur'] if o['rot_dur'] > 0 else 0.0)
                self.rot = self.rot_target = new
            else:
                d = _rot_delta(self.rot, self.rot_target)
                step = min(abs(d), ROTATION_SPEED_RAD_S * h)
                self.led['rotation_rad'].add(step)
                self.rot = (self.rot + math.copysign(step, d)) % (2 * math.pi) if abs(d) > step else self.rot_target
            if self.warm_end is not None or self.x_enabled:
                tv, ti = self._xray_targets()
                if self.warm_end is not None:
                    f = min(1.0, max(0.0, (self.t + h - self.warm_from) / WARMUP_S))
                    self.xv, self.xi = self.warm_v * f, self.warm_i * f
                else:
                    self.xv = _axis_move(self.xv, tv, KV_RAMP_V_S, h)
                    self.xi = _axis_move(self.xi, ti, CURRENT_RAMP_A_S, h)
            if (v0 > 0.0 and i0 > 0.0) or self._xray_on():
                self.led['xray_on_s'].add(h)
                pose = dict(xv=0.5 * (v0 + self.xv), rot=(rot0 + 0.5 * _rot_delta(rot0, self.rot)) % (2 * math.pi), sod=0.5 * (sod0 + self.sod),
                            cz=0.5 * (cz0 + self.cz), slide=0.5 * (sl0 + self.slide))
                self._book_energy(h, v0, i0, self.xv, self.xi, pose)

    def _handle_events(self):
        for _ in range(10000):
            changed = False
            if self.state == 'booting' and self.t >= self.state_t0 + BOOT_S - 1e-9:
                self.state = 'ready'
                self._event('power', 'READY', '启动完成（各轴为绝对位置，保持断电时的位置）')
                changed = True
            if self.power:
                if abs(self.slide - self.slide_target) <= 1e-12:
                    self.slide = self.slide_target
                for a, b in (('sod', 'sod_target'), ('cz', 'cz_target')):
                    if abs(getattr(self, a) - getattr(self, b)) <= 1e-12:
                        setattr(self, a, getattr(self, b))
                if self.warm_end is not None and self.t >= self.warm_end - 1e-9:
                    self.warm_end = self.warm_from = None
                    self.warm_needed = False
                    self.xv, self.xi = self.warm_v, self.warm_i            # the program's end point; a newer set point follows with the ramp
                    self._event('xray', 'WARMUP_DONE', '预热完成')
                    changed = True
            o = self.op
            if o is not None and 0 <= o['i'] < len(o['ends']) and self.t >= o['ends'][o['i']] - 1e-9:
                it = o['items'][o['i']]
                if it[0] == 'rotate':
                    self.rot = self.rot_target = (o['rot0'] + o['rot_amount']) % (2 * math.pi)
                self._next_item()
                changed = True
            rc = self.recon
            if rc is not None and self.t >= rc['t0'] + rc['duration_s'] - 1e-9:
                self._finish_recon(rc)
                changed = True
            if not changed:
                return

    # ---------------------------------------------------------------- energy ledger
    def _to_sample(self, s, D, sod, cz, rot, axis, ox, oy):
        c, sn = math.cos(-rot), math.sin(-rot)
        Rm = np.array([[c, -sn, 0.0], [sn, c, 0.0], [0.0, 0.0, 1.0]])
        zb = cz - SOURCE_XYZ_M[2] + (s['pin']['length_m'] if s['pin'] else 0.0)
        S = Rm @ np.array([-sod, -axis, 0.0]) - np.array([ox, oy, zb])
        return S, D @ Rm.T

    def _frame(self, s, D, sod, cz, rot, axis):
        """Sample frame of a pose (origin at the bottom centre of the sample body on its own axis; without a sample at the chuck top on the
        rotation axis): (S, directions, rotation axis x, y and chuck-top z in that frame)."""
        if s is not None:
            S, Ds = self._to_sample(s, D, sod, cz, rot, axis, s['offset_x_m'], s['offset_y_m'])
            return S, Ds, -s['offset_x_m'], -s['offset_y_m'], -(s['pin']['length_m'] if s['pin'] else 0.0)
        c, sn = math.cos(-rot), math.sin(-rot)
        Rm = np.array([[c, -sn, 0.0], [sn, c, 0.0], [0.0, 0.0, 1.0]])
        S = Rm @ np.array([-sod, -axis, 0.0]) - np.array([0.0, 0.0, cz - SOURCE_XYZ_M[2]])
        return S, D @ Rm.T, 0.0, 0.0, 0.0

    def _book_energy(self, h, v0, i0, v1, i1, pose):
        """Energy of a sub-step inside one rate cell: the sinks scale with V^2 I (Kramers efficiency eta = K Z V times the power V I), the
        electrical energy with V I; V and I are linear in time within a sub-step (ramps and the warm-up program), so both integrals are exact."""
        dv, di = v1 - v0, i1 - i0
        vi = h * (v0 * i0 + 0.5 * (v0 * di + i0 * dv) + dv * di / 3.0)
        v2i = h * (v0 * v0 * i0 + 0.5 * (v0 * v0 * di + 2.0 * v0 * i0 * dv) + (2.0 * v0 * dv * di + i0 * dv * dv) / 3.0 + 0.25 * dv * dv * di)
        base, kv = self._base_rates(pose)
        f = v2i / (1.602176634e-19 * kv * kv)
        amt = {k: v * f for k, v in base.items()}
        amt['electrical_energy_j'] = vi
        amt['xray_emitted_j'] = KRAMERS_K_PER_V * 74 * v2i
        amt['energy_residual_j'] = amt['xray_emitted_j'] - sum(amt[k] for k in ENERGY_SINKS)
        for k, v in amt.items():
            self.led[k].add(v)
        if self.on_stage is not None:
            s = self.samples[self.on_stage]
            dose = amt['sample_absorbed_j'] / max(sample_mass_kg(s), 1e-12)          # energy absorbed in the sample body only
            s['dose_gy'] = s.get('dose_gy', 0.0) + dose
            self.led['sample_dose_gy'].add(dose)

    def _beam_filter(self, slide=None):
        """Filter position in the beam: the one nearest the slide (the spectrum changes while the slide passes, not at the command)."""
        return min(FILTER_POSITIONS - 1, max(0, int(round((self.slide if slide is None else slide) / FILTER_PITCH_M))))

    def _rate_key(self, pose):
        """Cell of the pose: voltage (1 kV, at least 2 kV since the spectrum grid starts at 1 keV), filter in the beam, sample and its offsets,
        carriage and lift (0.25 mm), rotation (2 pi / 32)."""
        s = self.samples.get(self.on_stage) if self.on_stage else None
        return (max(2, int(round(pose['xv'] / RATE_KV_CELL_V))), self._beam_filter(pose.get('slide')), self.on_stage, (s['offset_x_m'], s['offset_y_m']) if s else None,
                int(round(pose['sod'] / RATE_POS_CELL_M)), int(round(pose['cz'] / RATE_POS_CELL_M)),
                int(round(pose['rot'] / RATE_ROT_CELL_RAD)) % RATE_ROT_CELLS)

    def _object_box(self, s, sod, cz, rot):
        """Rectangle of the detector plane holding the projection of the sample body with its container at the rotation angle of the rate
        cell (the cell's energy flow is evaluated at that pose): the tangents of the enclosing vertical cylinder about the sample's own axis
        and its height seen from the near and far side, clipped to the emission cone (the beam irradiates the sample beside the active area
        too); None when it misses the cone."""
        c = SDD_M * math.tan(CONE_HALF_ANGLE_RAD)
        rt_ = sample_radius_total(s)
        ox, oy = s['offset_x_m'], s['offset_y_m']
        xc = sod + ox * math.cos(rot) - oy * math.sin(rot)                   # sample axis in the source frame (beam along x)
        yc = self.truth['axis_offset'] + ox * math.sin(rot) + oy * math.cos(rot)
        rho = math.hypot(xc, yc)
        if rho <= rt_ or xc - rt_ <= 0.0:
            return -c, c, -c, c
        th, al = math.atan2(yc, xc), math.asin(rt_ / rho)
        us = [SDD_M * math.tan(th - al), SDD_M * math.tan(th + al)]
        z0 = cz - SOURCE_XYZ_M[2] + (s['pin']['length_m'] if s['pin'] else 0.0)
        vv = [SDD_M * z / (xc + sg * rt_) for z in (z0, z0 + s['height_m']) for sg in (-1, 1)]
        pu = 0.02 * (us[1] - us[0]) + 2 * PIXEL_PITCH_M
        pv = 0.02 * (max(vv) - min(vv)) + 2 * PIXEL_PITCH_M
        u0, u1 = max(us[0] - pu, -c), min(us[1] + pu, c)
        v0, v1 = max(min(vv) - pv, -c), min(max(vv) + pv, c)
        if u0 >= u1 or v0 >= v1:
            return None
        return u0, u1, v0, v1

    def _ray_energy(self, e, ph_f, omega, r, det, up, mount, near, body, far):
        """Energy flow per electron (J) of a ray set through the objects in their order along each ray: the mount (stage housing, chuck and
        pin, stacked below the sample: first on rays going up, last on rays going down), the container wall before the inside of the tube,
        the sample body, the wall after it.  Each object absorbs the mu_en / mu share of the energy it removes from the beam reaching it;
        returns air, holder (mount and container), body, scattered, detector deposited and passed (rays on the active area) and shielding
        (rays beside it).  mount / near / body / far: {material: path lengths}."""
        f, n = 0.5 * KEV, len(e)
        mu_air, _ = _mu_grid('nist_air', n)
        inc = ph_f * np.exp(-np.multiply.outer(r, mu_air))                   # (rays, E) photons at the objects / detector plane
        W = inc * e
        out = dict(air=f * (float(omega.sum()) * float(ph_f @ e) - float(omega @ W.sum(1))), holder=0.0, body=0.0, scattered=0.0)

        def seg(paths):
            if not paths:
                return None
            mats = sorted(paths)
            L = np.stack([paths[m] for m in mats], 1)
            mt = L @ np.stack([_mu_grid(m, n)[0] for m in mats])
            return mt, L @ np.stack([_mu_grid(m, n)[1] for m in mats]), np.exp(-mt)
        segs = [('holder', seg(mount)), ('holder', seg(near)), ('body', seg(body)), ('holder', seg(far))]
        T = [x[2] if x is not None else None for _, x in segs]

        def prod(ks):
            p = None
            for k in ks:
                if T[k] is not None:
                    p = T[k] if p is None else p * T[k]
            return 1.0 if p is None else p
        before = [(prod([]), prod([1, 2, 3])), (prod([0]), prod([])), (prod([0, 1]), prod([1])), (prod([0, 1, 2]), prod([1, 2]))]   # (up, down)
        u = up[:, None]
        for k, (g, x) in enumerate(segs):
            if x is None:
                continue
            mt, me, Tk = x
            bu, bd = before[k]
            R = W * (1.0 - Tk) * (np.where(u, bu, bd) if (np.ndim(bu) or np.ndim(bd)) else 1.0)
            A = np.divide(R * me, mt, out=np.zeros_like(R), where=mt > 0)
            ra, aa = f * float(omega @ R.sum(1)), f * float(omega @ A.sum(1))
            out[g] += aa
            out['scattered'] += ra - aa
        at = inc * prod([0, 1, 2, 3])
        _, edep = detector_response(tuple(e))
        wd, ws = omega * det, omega * ~det
        out['dep'] = f * float(wd @ (at @ edep))
        out['passed'] = f * float(wd @ (at @ (e - edep)))
        out['shield'] = f * float(ws @ (at @ e))
        return out

    def _base_rates(self, pose):
        """Energy flow per electron (J) at the centre of the rate cell of the pose: the target absorbs the back half of the emission and the
        self-absorbed part of the front half (take-off path of the reflection target), the window and the filter their shares of the emission
        cone; the rays of the cone split into air, the sample body (absorbed: its dose), the holder and stage hardware (pin, container, chuck,
        housing), scattered, detector deposited and passed (rays on the active area) and shielding (rays beside it); the rest of the front
        half goes to the shielding.  The coarse cells of the cone keep one ray each (rays meeting no object from a per-spectrum cache); the
        cells overlapping the projection box of the sample body (at the cell's angle) are cut at the box edges, the parts inside it split into
        sub-cells of a fine pitch (about LEDGER_FINE_RAYS rays over the box, at least 16 across either side), the parts beside it keep one
        ray; the pieces tile the cone, so every share is a sum of positive ray terms, and along each ray the objects absorb in their order
        (_ray_energy); returns (rates, cell voltage)."""
        key = self._rate_key(pose)
        kv = key[0] * RATE_KV_CELL_V
        base = self._rate_cache.get(key)
        if base is not None:
            return base, kv
        sod, cz, rot = key[4] * RATE_POS_CELL_M, key[5] * RATE_POS_CELL_M, key[6] * RATE_ROT_CELL_RAD
        fpos = key[1]
        s = self.samples.get(self.on_stage) if self.on_stage else None
        axis = self.truth['axis_offset']
        e, ph_un = unabsorbed_spectrum(kv)
        _, ph_self = emitted_spectrum(kv)
        ph_win = ph_self * np.exp(-_mu_grid('be', len(e))[0] * BE_WINDOW_M)
        _, ph_f = beam_spectrum(kv, fpos)
        E = lambda ph: float((ph * e).sum() * 0.5 * KEV)                      # J per sr per electron
        omega_cone = 2 * math.pi * (1 - math.cos(CONE_HALF_ANGLE_RAD))

        def objects(D, with_body):
            """(mount, near wall, body, far wall) paths of rays D; the sample frame keeps the vertical, so D[:, 2] > 0 is a ray going up."""
            S, Ds, ax, ay, zt = self._frame(s, D, sod, cz, rot, axis)
            mount, near, bod, far = dict(hardware_paths(S, Ds, ax, ay, zt)), {}, {}, {}
            if s is not None:
                for sid, L in pin_paths(s, S, Ds).items():
                    _add(mount, sid, L)
                near, far = container_walls(s, S, Ds)
                if with_body:
                    bod = body_paths(s, S, Ds)
            return mount, near, bod, far
        U, V, PU, PV, D, omega, r, det = _ledger_grid()
        box = self._object_box(s, sod, cz, rot) if s is not None else None
        sel = np.zeros(len(U), bool)
        if box is not None:
            u0, u1, v0, v1 = box
            sel = (U + PU / 2 > u0) & (U - PU / 2 < u1) & (V + PV / 2 > v0) & (V - PV / 2 < v1)
        tot = dict(air=0.0, dep=0.0, passed=0.0, shield=0.0, scattered=0.0, holder=0.0, body=0.0)
        c = np.flatnonzero(~sel)
        mount, near, _, far = objects(D[c], False)
        hit = np.zeros(len(c), bool)
        for L in list(mount.values()) + list(near.values()) + list(far.values()):
            hit |= L > 0
        for k, a in zip(('air', 'dep', 'passed', 'shield'), _free_rays(kv, fpos)):
            tot[k] += float(a[c[~hit]].sum())
        if hit.any():
            ch = c[hit]
            o = self._ray_energy(e, ph_f, omega[ch], r[ch], det[ch], D[ch, 2] > 0, *({k: v[hit] for k, v in g.items()} for g in (mount, near, {}, far)))
            for k, v in o.items():
                tot[k] += v
        w_used = float(omega[c].sum())
        if sel.any():
            u0, u1, v0, v1 = box
            nb = max(16, int(round(math.sqrt(LEDGER_FINE_RAYS * (u1 - u0) / (v1 - v0)))))        # at least 16 rays across either side
            pu_f, pv_f = (u1 - u0) / nb, (v1 - v0) / max(16, int(round(LEDGER_FINE_RAYS / nb)))
            a0, a1, b0, b1 = U[sel] - PU[sel] / 2, U[sel] + PU[sel] / 2, V[sel] - PV[sel] / 2, V[sel] + PV[sel] / 2
            ue = (a0, np.maximum(a0, u0), np.minimum(a1, u1), a1)             # each cell cut at the box edges into 3 x 3 pieces
            ve = (b0, np.maximum(b0, v0), np.minimum(b1, v1), b1)
            pieces = []
            for iu in range(3):
                for iv in range(3):
                    lu, hu_, lv, hv_ = ue[iu], ue[iu + 1], ve[iv], ve[iv + 1]
                    ok = (hu_ - lu > 1e-12) & (hv_ - lv > 1e-12)
                    if iu == 1 and iv == 1:                                     # inside the box: sub-cells of about the fine pitch
                        nu_ = np.maximum(1, np.ceil((hu_ - lu) / pu_f - 1e-9)).astype(int)
                        nv_ = np.maximum(1, np.ceil((hv_ - lv) / pv_f - 1e-9)).astype(int)
                    else:                                                       # beside the box (no body at any angle): one ray
                        nu_ = nv_ = np.ones(len(lu), int)
                    pieces.append([x[ok] for x in (lu, hu_, lv, hv_, nu_, nv_)])
            lu, hu_, lv, hv_, nu_, nv_ = (np.concatenate([p[k] for p in pieces]) for k in range(6))
            cnt = nu_ * nv_
            idx = np.repeat(np.arange(len(cnt)), cnt)
            j = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
            wu, wv = (hu_ - lu) / nu_, (hv_ - lv) / nv_
            Df, omf, rf, detf = _cell_rays(lu[idx] + (j % nu_[idx] + 0.5) * wu[idx], lv[idx] + (j // nu_[idx] + 0.5) * wv[idx], wu[idx], wv[idx])
            o = self._ray_energy(e, ph_f, omf, rf, detf, Df[:, 2] > 0, *objects(Df, True))
            for kk, v in o.items():
                tot[kk] += v
            w_used += float(omf.sum())
        out = dict(xray_emitted_j=4 * math.pi * E(ph_un), target_absorbed_j=2 * math.pi * E(ph_un) + 2 * math.pi * (E(ph_un) - E(ph_self)),
                   window_absorbed_j=omega_cone * (E(ph_self) - E(ph_win)), filter_absorbed_j=omega_cone * (E(ph_win) - E(ph_f)),
                   shielding_j=(2 * math.pi - omega_cone) * E(ph_self) + (omega_cone - w_used) * E(ph_f) + tot['shield'],
                   air_absorbed_j=tot['air'], sample_absorbed_j=tot['body'], holder_absorbed_j=tot['holder'],
                   sample_scattered_j=tot['scattered'], detector_absorbed_j=tot['dep'], detector_passed_j=tot['passed'])
        out = {k: float(v) for k, v in out.items()}
        if len(self._rate_cache) > 512:
            self._rate_cache.clear()
        self._rate_cache[key] = out
        return out, kv

    def _energy_rates(self):
        """Instantaneous energy flow (J/s) at the actual voltage, current and pose (for inspection; the ledger integrates _book_energy)."""
        pose = dict(xv=self.xv, rot=self.rot, sod=self.sod, cz=self.cz)
        base, kv = self._base_rates(pose)
        f = self.xv * self.xv * self.xi / (1.602176634e-19 * kv * kv)
        rates = {k: v * f for k, v in base.items()}
        rates['electrical_energy_j'] = self.xv * self.xi
        rates['xray_emitted_j'] = KRAMERS_K_PER_V * 74 * self.xv * self.xv * self.xi
        rates['energy_residual_j'] = rates['xray_emitted_j'] - sum(rates[k] for k in ENERGY_SINKS)
        return rates

    # ---------------------------------------------------------------- detector simulation
    def _groups(self, kv, fpos, materials):
        """Energy groups of the detected spectrum at the detector (central-ray air path): photons per sr per electron per group, mean deposited
        energy per incident photon (keV), second moment of the deposited energy (keV2 per incident photon) and group-mean linear attenuation
        coefficients of the materials (weighted by the detected energy)."""
        e, ph = beam_spectrum(kv, fpos)
        de = 0.5
        ph = ph * air_transmission(e, SDD_M) * de
        p, edep = detector_response(tuple(e))
        mu_c, men_c = linear_mu('nist_cesium', e)
        eps_int = e * men_c / mu_c
        w = ph * edep
        edges = [33.169, 35.985]
        for sid in materials:
            edges += [ed['energy_MeV'] * 1e3 for ed in substance(sid)['edges'] if ed['label'].endswith(' K') and 5.0 < ed['energy_MeV'] * 1e3 < kv / 1e3]
        idx, G = energy_groups(e, w, edges)
        n = np.array([ph[idx == g].sum() for g in range(G)])
        keep = n > 0
        eps = np.array([(ph * edep)[idx == g].sum() for g in range(G)])
        m2 = np.array([(ph * p * eps_int ** 2)[idx == g].sum() for g in range(G)])
        mus = {}
        for sid in materials:
            mu, _ = linear_mu(sid, e)
            mus[sid] = np.array([(w * mu)[idx == g].sum() / max(w[idx == g].sum(), 1e-300) for g in range(G)])[keep]
        return dict(n=n[keep], eps=eps[keep] / n[keep], m2=m2[keep] / n[keep], mu=mus, mean_e=float((w * e).sum() / w.sum()))

    def _window(self, ctx, s, roi=None):
        """Detector region simulated for an acquisition: the ROI of a radiograph (binned native pixels), the whole detector for a radiograph
        without sample, else the projected envelope of the sample over all angles (nominal geometry, calibrated axis offset, margins for the
        unknown offset), sampled with at most SIM_DETECTOR pixels but not finer than one binned native pixel."""
        b = int(ctx['scan']['binning'][0])
        nat = PIXEL_PITCH_M * b
        hu, hv = DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 2, DETECTOR_PIXELS[1] * PIXEL_PITCH_M / 2
        if roi is not None:
            n = roi['size_px']
            return dict(u0=roi['center_u_m'] - n / 2 * nat, v1=roi['center_v_m'] + n / 2 * nat, pitch=nat, nu=n, nv=n, binning=b)
        if s is None or ctx.get('full_window'):
            u0, u1, v0, v1 = -hu, hu, -hv, hv
        else:
            r = self._envelope(s, *ctx['offset'])
            sod = ctx['sod']
            a = ctx.get('axis_known') or 0.0
            uh = SDD_M * r / math.sqrt(max(sod * sod - r * r, 1e-12))
            uc = SDD_M * a / sod
            mu_ = 0.06 * uh + 4 * AXIS_OFFSET_SD_M * SDD_M / sod + 2 * nat
            u0, u1 = uc - uh - mu_, uc + uh + mu_
            zb = ctx['cz'] - SOURCE_XYZ_M[2] + (s['pin']['length_m'] if s['pin'] else 0.0)         # sample body only (not the mounting pin)
            zt = zb + s['height_m']
            vv = [SDD_M * z / (sod + sg * r) for z in (zb, zt) for sg in (-1, 1)]
            mv = 0.04 * (max(vv) - min(vv)) + 2 * nat
            v0, v1 = min(vv) - mv, max(vv) + mv
            u0, u1, v0, v1 = max(u0, -hu), min(u1, hu), max(v0, -hv), min(v1, hv)
        p = max(math.sqrt((u1 - u0) * (v1 - v0) / (SIM_DETECTOR[0] * SIM_DETECTOR[1])), (u1 - u0) / 400, (v1 - v0) / 400, nat)   # pixel budget
        return dict(u0=u0, v1=v1, pitch=p, nu=max(2, int(math.ceil((u1 - u0) / p - 1e-9))), nv=max(2, int(math.ceil((v1 - v0) / p - 1e-9))), binning=b)

    def _window_rays(self, w):
        u = w['u0'] + (np.arange(w['nu']) + 0.5) * w['pitch']
        v = w['v1'] - (np.arange(w['nv']) + 0.5) * w['pitch']
        U, V = np.meshgrid(u, v)
        P = np.stack([np.full(U.size, SDD_M), U.ravel(), V.ravel()], -1)
        r = np.linalg.norm(P, axis=1)
        return P / r[:, None], w['pitch'] ** 2 * (SDD_M / r) ** 3 / SDD_M ** 2, u, v

    def _flat_residual(self, flat, w):
        """Residual fixed pattern after the dark / bright correction on the window grid: the bright-field noise of each window pixel (the
        per-pixel gain itself is divided out), a field fixed for that calibration; dead columns of the injected defect fault."""
        n_px = (w['pitch'] / (PIXEL_PITCH_M * w['binning'])) ** 2
        sig = flat['sigma_binned'] / math.sqrt(n_px)
        key = int(hashlib.sha256(f"{flat['seed']}|{w['u0']:.9f}|{w['v1']:.9f}|{w['pitch']:.12f}|{w['nu']}|{w['nv']}".encode()).hexdigest()[:12], 16)
        xi = np.random.default_rng(key).standard_normal((w['nv'], w['nu']))
        return 1.0 / (1.0 + sig * xi)

    def _dead_columns(self, ctx, w):
        """Window columns that fall into the injected dead detector columns (native column bands of 16 pixels)."""
        if not ctx['defects']:
            return np.zeros(w['nu'], bool)
        u = w['u0'] + (np.arange(w['nu']) + 0.5) * w['pitch']
        band = DETECTOR_PIXELS[0] / SIM_DETECTOR[0] * PIXEL_PITCH_M
        col = np.floor((u + DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 2) / band).astype(int)
        return np.isin(col, ctx['defects'])

    def _simulate(self, ctx, s, angles, shifts, frames, t_exp, w, rng=None, with_sample=True):
        """Normalised transmission images of the given rotation angles on the window w: exact paths, energy groups, focal-spot penumbra and
        scintillator blur, compound-Poisson quantum noise, read noise, 16-bit saturation, residual fixed pattern after the flat-field correction
        (random detector movement shifts it by 'shifts' window columns), dead columns."""
        from scipy.ndimage import gaussian_filter
        mats = list(dict.fromkeys((_materials(s) if (s is not None and with_sample) else []) + list(HARDWARE_MATERIALS)))
        grp = self._groups(ctx['kv'], ctx['filter'], mats)
        electrons = ctx['ia'] / 1.602176634e-19
        D, omega, _, _ = self._window_rays(w)
        n_bin = (w['pitch'] / (PIXEL_PITCH_M * w['binning'])) ** 2        # binned native pixels per simulated pixel
        spot = focal_spot_m(ctx['kv'] * ctx['ia'])
        M = SDD_M / ctx['sod']
        sigma_px = math.hypot(spot / 2.355 * (M - 1) / w['pitch'], DETECTOR_PSF_PX * PIXEL_PITCH_M / w['pitch'])
        flat = ctx['flat']
        resid0 = self._flat_residual(flat, w)
        dead = self._dead_columns(ctx, w)
        out, sat = [], 0.0
        for k, beta in enumerate(angles):
            if s is not None and with_sample:
                S, Ds = self._to_sample(s, D, ctx['sod'], ctx['cz'], beta, ctx['axis'], ctx['offset'][0], ctx['offset'][1])
                paths = sample_paths(s, S, Ds)
                ax, ay, zt = -ctx['offset'][0], -ctx['offset'][1], -(s['pin']['length_m'] if s['pin'] else 0.0)
            else:
                S, Ds, ax, ay, zt = self._frame(None, D, ctx['sod'], ctx['cz'], beta, ctx['axis'])
                paths = {}
            for sid, L in hardware_paths(S, Ds, ax, ay, zt).items():          # brass chuck and rotation-stage housing below the sample
                _add(paths, sid, L)
            att = np.zeros((D.shape[0], len(grp['n'])))
            for sid, L in paths.items():
                att += np.outer(L, grp['mu'][sid])
            T = np.exp(-att)
            ph = electrons * t_exp * frames * omega[:, None] * grp['n'][None, :]      # incident photons per group and pixel
            mean_kev = (ph * T * grp['eps'][None, :]).sum(1)
            var_kev2 = (ph * T * grp['m2'][None, :]).sum(1)
            mean0 = (ph * grp['eps'][None, :]).sum(1)
            img = mean_kev.reshape(w['nv'], w['nu'])
            if sigma_px > 0.05:
                img = gaussian_filter(img, sigma_px, mode='nearest')
            sig = ADU_PER_KEV * img.ravel()
            var = ADU_PER_KEV ** 2 * var_kev2 + READ_NOISE_ADU ** 2 * n_bin * frames
            full = (FULL_SCALE_ADU - DARK_OFFSET_ADU - DARK_CURRENT_ADU_S * t_exp) * n_bin * frames
            meas = sig + np.sqrt(var) * rng.standard_normal(sig.shape)
            sat += float(np.mean(sig > full))
            meas = np.minimum(meas, full)
            bright = np.minimum(ADU_PER_KEV * mean0, full)                 # the bright field clips at full scale too
            t = (meas / np.maximum(bright, 1e-30)).reshape(w['nv'], w['nu'])
            sh = shifts[k] if shifts is not None else 0
            t = t * np.roll(resid0, sh, axis=1)
            if dead.any():
                dk = np.roll(dead, sh)
                if flat['defects']:                                      # dead columns in the bright field too: gain divided out, noise stays
                    t[:, dk] = t[:, dk] + 0.15 * rng.standard_normal((w['nv'], int(dk.sum())))
                else:
                    t[:, dk] = t[:, dk] * 0.02
            out.append(t)
        return out, sat / max(len(angles), 1), grp

    # ---------------------------------------------------------------- operation finishers
    def _finish_flat_field(self):
        o = self.op
        ctx = o['ctx']
        r = self.records[o['record_id']]
        b = int(ctx['scan']['binning'][0])
        grp = self._groups(ctx['kv'], ctx['filter'], [])
        electrons = ctx['ia'] / 1.602176634e-19
        omega = (PIXEL_PITCH_M * b) ** 2 / SDD_M ** 2                    # one binned pixel on the central ray
        texp = ctx['scan']['exposure_s']
        m_b = ADU_PER_KEV * electrons * texp * omega * float((grp['n'] * grp['eps']).sum())          # ADU per binned pixel per frame
        v_b = ADU_PER_KEV ** 2 * electrons * texp * omega * float((grp['n'] * grp['m2']).sum())
        nf = FLAT_FIELD_FRAMES
        sigma_binned = math.sqrt(v_b * nf + READ_NOISE_ADU ** 2 * nf * 2) / (m_b * nf)            # relative bright-field error of one binned pixel
        level = m_b + DARK_OFFSET_ADU + DARK_CURRENT_ADU_S * texp
        usable = sigma_binned <= FLAT_MAX_SIGMA
        if level > FULL_SCALE_ADU:
            r['warnings'].append('亮场饱和：降低电流或曝光（亮区的透过率在满量程截止）')
        if not usable:
            r['warnings'].append(f'亮场信号不足：单个合并像素相对误差{sigma_binned * 100:.1f} %（上限{FLAT_MAX_SIGMA * 100:.0f} %），校正无效；提高电流、曝光或帧平均，或减少滤片')
        self.led['frames'].add(2 * nf)
        self.flat = dict(key=self._flat_key_of(ctx), t=self.t, seed=ctx['seed'], sigma_binned=sigma_binned, defects=bool(ctx['defects']), frames=nf,
                         level_adu=level, usable=usable)
        r['flat_field'] = dict(frames=nf, mean_bright_adu=float(min(level, FULL_SCALE_ADU)), dark_adu=DARK_OFFSET_ADU + DARK_CURRENT_ADU_S * texp, key=self.flat['key'])
        _, omega_px, _, _ = self._window_rays(dict(u0=-DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 2, v1=DETECTOR_PIXELS[1] * PIXEL_PITCH_M / 2,
                                                   pitch=DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 88, nu=88, nv=75, binning=b))
        prof = (omega_px / omega_px.max()).reshape(75, 88) * min(level, FULL_SCALE_ADU) / FULL_SCALE_ADU
        r['preview'] = [[float(x) for x in row] for row in prof]
        r['preview_unit'] = 'fraction of full scale (bright field, cos^3 fall-off)'
        r['note_zh'] = f'暗场与亮场各{nf}帧；亮场中心{level:.0f} ADU（满量程65535）；单个合并像素亮场相对误差{sigma_binned * 100:.2f} %'
        self._complete(o, r)

    def _flat_key_of(self, ctx):
        s = ctx['scan']
        return f"{ctx['kv']:.1f}|{ctx['ia'] * 1e9:.1f}|{ctx['filter']}|{s['binning']}|{s['exposure_s']:.6g}"

    def _finish_radiograph(self):
        o = self.op
        ctx = o['ctx']
        r = self.records[o['record_id']]
        s = self.samples.get(ctx['sample_id']) if ctx['sample_id'] else None
        rng = np.random.default_rng(ctx['seed'])
        sc = ctx['scan']
        w = self._window(dict(ctx, full_window=True), s, ctx['roi'])
        self.led['projections'].add(1); self.led['frames'].add(sc['averaging'])
        imgs, sat, grp = self._simulate(ctx, s, [ctx['rot0']], None, sc['averaging'], sc['exposure_s'], w, rng=rng)
        t = imgs[0]
        self._data[r['record_id']] = dict(image=t.astype(np.float32), roi=copy.deepcopy(ctx['roi']), sod=ctx['sod'], binning=int(sc['binning'][0]), window=w)
        r['sim_detector'], r['sim_pixel_m'] = [w['nu'], w['nv']], w['pitch']
        r.update(transmission=dict(min=float(t.min()), mean=float(t.mean()), max=float(t.max())), saturated_fraction=sat)
        step = max(1, int(math.ceil(max(t.shape) / 88)))
        r['preview'] = [[float(x) for x in row] for row in t[::step, ::step][:128, :128]]
        r['preview_unit'] = 'transmission I/I0'
        r['note_zh'] = f'透视（平均探测能量{grp["mean_e"]:.1f} keV）' + ('，局部区域' if ctx['roi'] else '')
        self._evict()
        self._complete(o, r)

    def _finish_geometry_calibration(self):
        o = self.op
        ctx = o['ctx']
        r = self.records[o['record_id']]
        s = self.samples[ctx['sample_id']]
        rng = np.random.default_rng(ctx['seed'])
        angles = [ctx['rot0'] + 2 * math.pi * k / 36 for k in range(36)]
        sc = ctx['scan']
        w = self._window(ctx, s)
        self.led['projections'].add(36); self.led['frames'].add(36 * sc['averaging'])
        imgs, sat, grp = self._simulate(ctx, s, angles, None, sc['averaging'], sc['exposure_s'], w, rng=rng)
        r['sim_detector'], r['sim_pixel_m'] = [w['nu'], w['nv']], w['pitch']
        est = self._fit_axis(imgs, angles, ctx['sod'], w)
        if est is None:
            r['warnings'].append('标定球识别失败')
            r['note_zh'] = '钢球识别失败：检查标定体模是否完整在视场内'
            self._complete(o, r)
            return
        a, resid, n_balls = est
        r['geometry'] = dict(axis_offset_m=a, residual_m=resid, balls=n_balls)
        limit = max(GEO_FIT_MAX_RESIDUAL_PX * w['pitch'] * ctx['sod'] / SDD_M, GEO_FIT_MIN_LIMIT_M)
        if resid > limit or n_balls < CAL_BALLS - 1:
            r['warnings'].append(f'钢球轨迹拟合残差{resid * 1e6:.1f} µm超过{limit * 1e6:.1f} µm或识别的钢球不足（{n_balls}个）：标定未采用')
            r['note_zh'] = f'拟合失败：旋转轴横向偏移{a * 1e6:.1f} µm不可信（残差{resid * 1e6:.1f} µm），保留原标定'
            self._complete(o, r)
            return
        self.geo_cal = dict(axis_offset_m=a, calibrated_s=self.t)
        r['note_zh'] = f'旋转轴横向偏移{a * 1e6:.1f} µm（{n_balls}个钢球，拟合残差{resid * 1e6:.2f} µm）'
        self._complete(o, r)

    def _fit_axis(self, imgs, angles, sod, w):
        """Track the steel balls in the 36 projections (blobs of high line integral) and fit the exact cone-beam trajectories
        u = SDD (a + r sin(b + phi)) / (SOD + r cos(b + phi)) jointly for the axis offset a."""
        from scipy import ndimage, optimize
        pu = pv = w['pitch']
        ball_px = 2 * CAL_BALL_RADIUS_M * SDD_M / sod / pu                 # projected ball diameter in window pixels
        se = max(9, 2 * int(round(2 * ball_px)) + 1)                     # top-hat element: about four ball diameters
        blobs = []
        for k, t in enumerate(imgs):
            q = -np.log(np.clip(t, 1e-6, None))
            d = ndimage.white_tophat(q, size=se)                          # balls stand out of the PMMA cylinder background
            lab, n = ndimage.label(d > 0.5 * max(d.max(), 1e-9))
            found = []
            for j in range(1, n + 1):
                wt = np.where(lab == j, d, 0.0)
                if wt.sum() <= 0 or (lab == j).sum() < 2:
                    continue
                cy, cx = ndimage.center_of_mass(wt)
                found.append((w['u0'] + (cx + 0.5) * pu, w['v1'] - (cy + 0.5) * pv))
            blobs.append(found)
        seed = max(range(len(blobs)), key=lambda k: len(blobs[k]))
        if len(blobs[seed]) < 3:
            return None
        means = sorted(v for _, v in blobs[seed])
        tol = 0.45 * min(np.diff(means)) if len(means) > 1 else 10 * pv
        tracks = {i: [] for i in range(len(means))}
        for k, found in enumerate(blobs):                                  # nearest track in v (cone-beam magnification makes v wobble)
            for u, v in found:
                i = int(np.argmin([abs(v - m) for m in means]))
                if abs(v - means[i]) < tol:
                    tracks[i].append((angles[k], u))
        balls = [np.array(v) for v in tracks.values() if len(v) >= 24]
        if len(balls) < 3:
            return None
        M = SDD_M / sod

        def res(p):
            a = p[0]
            out = []
            for i, bl in enumerate(balls):
                rr, ph = p[1 + 2 * i], p[2 + 2 * i]
                out.append(SDD_M * (a + rr * np.sin(bl[:, 0] + ph)) / (sod + rr * np.cos(bl[:, 0] + ph)) - bl[:, 1])
            return np.concatenate(out)
        p0 = [float(np.mean([bl[:, 1].mean() for bl in balls]) / M)]
        for bl in balls:
            amp = (bl[:, 1].max() - bl[:, 1].min()) / 2 / M
            k = int(np.argmax(bl[:, 1]))
            p0 += [max(amp, 1e-4), math.pi / 2 - bl[k, 0]]
        sol = optimize.least_squares(res, p0, method='lm')
        rms = float(np.sqrt(np.mean(sol.fun ** 2)))
        return float(sol.x[0]), rms / M, len(balls)

    def _finish_scan(self):
        o = self.op
        ctx = o['ctx']
        r = self.records[o['record_id']]
        s = self.samples[ctx['sample_id']]
        rng = np.random.default_rng(ctx['seed'])
        sc = ctx['scan']
        n_sim, n_nom = ctx['n_sim'], ctx['n_nom']
        full = sc['range_deg'] == '360'
        angles = [ctx['rot0'] + ctx['span'] * k / (n_sim if full else max(n_sim - 1, 1)) for k in range(n_sim)]
        shifts = [int(x) for x in rng.integers(-4, 5, n_sim)] if sc['random_movement'] else None
        frames = sc['averaging'] * n_nom / n_sim                        # dose-equivalent aggregation of the skipped projections
        self.led['projections'].add(n_nom); self.led['frames'].add(n_nom * sc['averaging'])
        w = self._window(ctx, s)
        imgs, sat, grp = self._simulate(ctx, s, angles, shifts, frames, sc['exposure_s'], w, rng=rng)
        q = np.stack([-np.log(np.clip(t, 1e-6, None)) for t in imgs]).astype(np.float32)
        self._data[r['record_id']] = dict(q=q, angles=np.array(angles) - ctx['rot0'], full=full, sod=ctx['sod'], span=ctx['span'], grp=grp,
                                          materials=_materials(s), kv=ctx['kv'], filter=ctx['filter'], window=w)
        r['sim_detector'], r['sim_pixel_m'] = [w['nu'], w['nv']], w['pitch']
        tt = np.stack(imgs)
        r.update(transmission=dict(min=float(tt.min()), mean=float(tt.mean()), max=float(tt.max())), saturated_fraction=sat,
                 dose_gy=float(s.get('dose_gy', 0.0) - ctx.get('dose0', 0.0)))
        st = max(1, int(math.ceil(max(imgs[0].shape) / 96)))
        r['preview'] = [[float(x) for x in row] for row in imgs[0][::st, ::st][:128, :128]]
        r['preview_unit'] = 'transmission I/I0 (first projection, acquisition window)'
        if tt.min() < 0.02:
            r['warnings'].append('部分射线透过率低于2 %：提高电压或加滤片')
        if sat > 0.001:
            r['warnings'].append('探测器饱和：降低电流或曝光')
        r['note_zh'] = f'{n_nom}个名义投影（模拟{n_sim}个，剂量等效合并）；平均探测能量{grp["mean_e"]:.1f} keV'
        self._evict()
        self._complete(o, r)

    def _evict(self):
        keep = sorted((k for k in self._data if self.records[k]['kind'] in ('projections', 'radiograph')), key=lambda k: self.records[k]['started_s'])
        for k in keep[:-8]:
            del self._data[k]

    # ---------------------------------------------------------------- reconstruction
    def _finish_recon(self, rc):
        ctx = rc['ctx']
        r = self.records[rc['record_id']]
        data = self._data.get(ctx['source'])
        if data is None:
            self.operations[rc['op']].update(phase='failed', finished_s=self.t, reason='DATA_EVICTED')
            r.update(status='failed', finished_s=self.t, reason='DATA_EVICTED', duration_s=self.t - r['started_s'])
            self._event('measurement', 'OP_FAILED', f'重建{rc["op"]}失败：投影数据已被清除', rid=r['record_id'])
            self.recon = None
            return
        rs = ctx['recon']
        q = data['q'].astype(np.float64)
        sod = data['sod']
        M = SDD_M / sod
        w = data['window']
        warnings = []
        if rs['beam_hardening'] > 0 and rs['bh_material']:
            q = self._bh_linearise(q, data['grp'], data['kv'], data['filter'], rs['bh_material'], rs['beam_hardening'])
        if rs['ring_correction'] > 0:
            from scipy.ndimage import median_filter
            m = q.mean(axis=0)
            q = q - rs['ring_correction'] * (m - median_filter(m, size=(1, 9)))[None, :, :]
        axis = ctx['axis_cal'] if ctx['axis_cal'] is not None else 0.0
        if rs['post_alignment'] is not None:
            axis += rs['post_alignment']
        elif rs['auto_alignment']:
            d_axis = self._auto_align(q, data['angles'], data['full'], M, w, axis, sod, data['span'])
            if d_axis is None:
                warnings.append('中心平面（光束高度）没有可用的样品数据：自动对准未执行，使用几何标定值或0')
            else:
                axis += d_axis
        if ctx['axis_cal'] is None and rs['post_alignment'] is None and not rs['auto_alignment']:
            warnings.append('未做几何标定也未后对准：旋转轴偏移会造成伪影')
        if ctx.get('voi_partial'):
            warnings.append('感兴趣体积部分超出采集窗口可重建的区域：该部分体素不完整或为0')
        vol, origin, voxel = self._fdk(q, data['angles'], data['full'], data['span'], sod, w, axis, int(rs['grid']), rs['filter'], rs['voi'])
        if rs['smoothing_vox'] > 0:
            from scipy.ndimage import gaussian_filter
            vol = gaussian_filter(vol, rs['smoothing_vox'])
        vol = vol.astype(np.float32)
        self._data[r['record_id']] = dict(volume=vol, origin=origin, voxel=voxel, sod=sod, sample_id=ctx['sample_id'])
        hist, edges = np.histogram(vol, bins=64)
        r['volume'] = dict(shape=list(vol.shape), voxel_m=voxel, origin_m=[float(x) for x in origin], mu_min_1_m=float(vol.min()), mu_max_1_m=float(vol.max()),
                           histogram=[int(x) for x in hist], histogram_edges_1_m=[float(x) for x in edges], axis_offset_used_m=float(axis))
        zc = vol.shape[0] // 2
        stepv = max(1, int(math.ceil(max(vol.shape[1:]) / 96)))
        r['preview'] = [[float(x) for x in row] for row in vol[zc, ::stepv, ::stepv][:128, :128]]
        r['preview_unit'] = 'linear attenuation coefficient 1/m (central axial slice)'
        r['warnings'] = warnings
        r['note_zh'] = f'FDK重建{vol.shape[2]}×{vol.shape[1]}×{vol.shape[0]}体素（模拟体素{voxel * 1e6:.1f} µm；名义体素{r["voxel_size_nominal_m"] * 1e6:.2f} µm）'
        o = rc
        self._complete(o, r)

    def _bh_linearise(self, q, grp, kv, fpos, material, strength):
        """Beam-hardening linearisation for one material: the polychromatic line integral q_poly(L) of the scan spectrum (detected-energy
        weights of the energy groups) is mapped to mu(E_eff) L with E_eff the detected mean energy of the unattenuated beam (a monochromatic
        equivalent); strength 0..1 blends the corrected and the measured line integrals."""
        g = grp if material in grp['mu'] else self._groups(kv, fpos, [material])
        mu = g['mu'][material]
        w = g['n'] * g['eps'] / (g['n'] * g['eps']).sum()
        mu_ref = float(linear_mu(material, [g['mean_e']])[0][0])
        L = np.linspace(0.0, 30.0 / max(float((w * mu).sum()), 1e-9), 400)
        qp = -np.log((w[None, :] * np.exp(-np.outer(L, mu))).sum(1))
        lin = np.interp(q, qp, L, right=L[-1]) * mu_ref
        return q + strength * (lin - q)

    def _auto_align(self, q, angles, full, M, w, axis0, sod=None, span=None):
        """Residual axis offset from the consistency of conjugate rays in the central plane (the detector row at the beam height is an exact
        fan-beam sinogram): a ray of view angle b1 and lab angle t1 = atan(u1 / SDD) lies at distance s = SOD sin t - a cos t from the axis
        and is measured again, reversed, at b2 = b1 - pi + 2 (alpha - t1), t2 = 2 alpha - t1 with alpha = atan(a / SOD).  The candidate a
        (+-200 um around the starting offset, 5 um grid, parabolic vertex) that minimises the robust mean squared difference of all conjugate
        pairs inside the scanned range wins; short scans use their redundant end region, full scans every ray."""
        sod = SDD_M / M if sod is None else sod
        span = (2 * math.pi if full else math.pi) if span is None else span
        nv = q.shape[1]
        vs = w['v1'] - (np.arange(nv) + 0.5) * w['pitch']
        j = int(np.argmin(np.abs(vs)))
        if abs(vs[j]) > w['pitch']:
            return None                                                  # the acquisition window does not reach the central plane
        row = q[:, j, :].astype(np.float64)
        if float(np.mean(row > 0.05 * max(float(row.max()), 1e-9))) < 0.05:
            return None                                                  # no sample in the central plane
        us = w['u0'] + (np.arange(w['nu']) + 0.5) * w['pitch']
        b = np.asarray(angles, float)
        n_p, nu = row.shape
        db = span / n_p if full else span / max(n_p - 1, 1)
        B1, T1 = np.meshgrid(b, np.arctan(us / SDD_M), indexing='ij')
        body = row > 0.02 * max(float(row.max()), 1e-9)

        def pairs(a):
            al = math.atan2(a, sod)
            B2 = B1 - math.pi + 2 * (al - T1)
            B2 = np.mod(B2, 2 * math.pi)
            fk = B2 / db
            fi = (SDD_M * np.tan(2 * al - T1) - us[0]) / w['pitch']
            k0, i0 = np.floor(fk).astype(int), np.floor(fi).astype(int)
            ok = (i0 >= 0) & (i0 < nu - 1) & ((k0 >= 0) & (k0 < n_p) if full else (k0 >= 0) & (k0 < n_p - 1))
            k0 = np.clip(k0, 0, n_p - 1)
            k1 = np.mod(k0 + 1, n_p) if full else np.clip(k0 + 1, 0, n_p - 1)
            i0 = np.clip(i0, 0, nu - 2)
            ak, ai = fk - np.floor(fk), np.clip(fi - i0, 0.0, 1.0)
            p2 = (1 - ak) * ((1 - ai) * row[k0, i0] + ai * row[k0, i0 + 1]) + ak * ((1 - ai) * row[k1, i0] + ai * row[k1, i0 + 1])
            sel = ok & (body | (p2 > 0.02 * max(float(row.max()), 1e-9)))
            return row[sel] - p2[sel]
        d0 = pairs(axis0)
        if d0.size < 20:
            return None                                                  # no conjugate rays in the scanned range (keep the starting offset)
        c2 = (10.0 * max(float(np.median(np.abs(d0))), 1e-6)) ** 2          # robust: clip outliers (dead columns, noise spikes)

        def err(a):
            d = pairs(a)
            return float(np.mean(np.minimum(d * d, c2))) if d.size else math.inf
        cand = axis0 + np.linspace(-200e-6, 200e-6, 81)
        e = [err(a) for a in cand]
        k = int(np.argmin(e))
        best = cand[k]
        if 0 < k < len(cand) - 1:
            dd = e[k - 1] - 2 * e[k] + e[k + 1]
            if dd > 0:
                best = cand[k] + 0.5 * (e[k - 1] - e[k + 1]) / dd * (cand[1] - cand[0])
        return float(best - axis0)

    def _fdk(self, q, angles, full, span, sod, w, axis, n, filt, voi):
        """Feldkamp-Davis-Kress reconstruction of the projections q (line integrals) on an n-grid (whole field of view or a volume of
        interest); returns (volume [z, y, x] in 1/m, origin of voxel (0, 0, 0) in the rotating frame (axis at the beam height), voxel size)."""
        n_p, nv, nu = q.shape
        pu = pv = w['pitch']
        us = (w['u0'] + (np.arange(nu) + 0.5) * pu - axis * SDD_M / sod) * sod / SDD_M          # virtual detector at the axis, axis-centred
        vs = (w['v1'] - (np.arange(nv) + 0.5) * pv) * sod / SDD_M
        dus = pu * sod / SDD_M
        U, V = np.meshgrid(us, vs)
        pre = sod / np.sqrt(sod ** 2 + U ** 2 + V ** 2)
        npad = int(2 ** math.ceil(math.log2(2 * nu)))
        k = np.arange(npad)
        k = np.where(k <= npad // 2, k, k - npad)
        h = np.zeros(npad)
        h[0] = 1.0 / (4 * dus * dus)
        odd = (k % 2) != 0
        h[odd] = -1.0 / (np.pi ** 2 * k[odd] ** 2 * dus * dus)
        H = np.real(np.fft.fft(h)) * dus
        f = np.abs(np.fft.fftfreq(npad, d=dus))
        fmax = 0.5 / dus
        if filt == 'shepp_logan':
            H = H * np.sinc(f / (2 * fmax))
        elif filt == 'hann':
            H = H * 0.5 * (1 + np.cos(np.pi * f / fmax))
        gamma_m = math.atan(DETECTOR_PIXELS[0] * PIXEL_PITCH_M / 2 / SDD_M)          # fan half-angle of the whole detector (Parker range)
        fov_r = min(abs(us.min()), abs(us.max())) * sod / math.hypot(sod, abs(us).max())
        if voi is None:
            size = [2 * fov_r, 2 * fov_r, (vs.max() - vs.min()) * (sod - fov_r) / sod]
            center = [0.0, 0.0, 0.5 * (vs.max() + vs.min()) * (sod - fov_r) / sod]
        else:
            size, center = list(voi['size_m']), [voi['center_m'][0], voi['center_m'][1], voi['center_m'][2]]
        voxel = max(size) / n
        dims = [max(2, int(round(sz / voxel))) for sz in size]              # x, y, z counts
        xs = center[0] + (np.arange(dims[0]) + 0.5 - dims[0] / 2) * voxel
        ys = center[1] + (np.arange(dims[1]) + 0.5 - dims[1] / 2) * voxel
        zs = center[2] + (np.arange(dims[2]) + 0.5 - dims[2] / 2) * voxel
        Z, Y, X = np.meshgrid(zs, ys, xs, indexing='ij')
        vol = np.zeros(X.shape)
        d_beta = span / (n_p if full else max(n_p - 1, 1))
        if not full:
            gp = -np.arctan(us / sod)                                    # Parker fan angle: the stage turns so that (b, g) and (b + pi - 2 g, -g) coincide

            def parker(bb):
                wp = np.ones_like(gp)
                wp = np.where(bb < 2 * (gamma_m - gp), np.sin(np.pi / 4 * bb / np.maximum(gamma_m - gp, 1e-12)) ** 2, wp)
                wp = np.where(bb > np.pi - 2 * gp, np.sin(np.pi / 4 * np.clip(np.pi + 2 * gamma_m - bb, 0.0, None) / np.maximum(gamma_m + gp, 1e-12)) ** 2, wp)
                return 2.0 * wp
        for kk in range(n_p):
            beta = angles[kk]
            proj = q[kk] * pre
            if not full:
                proj = proj * parker(beta)[None, :]
            pad = np.zeros((nv, npad))
            pad[:, :nu] = proj
            filt_p = np.real(np.fft.ifft(np.fft.fft(pad, axis=1) * H[None, :], axis=1))[:, :nu]
            cb, sb = math.cos(beta), math.sin(beta)
            xr = X * cb - Y * sb
            yr = X * sb + Y * cb
            Uu = sod + xr
            up = sod * yr / Uu
            vp = sod * Z / Uu
            fi = (up - us[0]) / dus
            fj = (vs[0] - vp) / (dus * pv / pu)
            i0 = np.floor(fi).astype(int)
            j0 = np.floor(fj).astype(int)
            ok = (i0 >= 0) & (i0 < nu - 1) & (j0 >= 0) & (j0 < nv - 1)
            i0c, j0c = np.clip(i0, 0, nu - 2), np.clip(j0, 0, nv - 2)
            ai, aj = fi - i0c, fj - j0c
            val = ((1 - aj) * ((1 - ai) * filt_p[j0c, i0c] + ai * filt_p[j0c, i0c + 1]) + aj * ((1 - ai) * filt_p[j0c + 1, i0c] + ai * filt_p[j0c + 1, i0c + 1]))
            w = (sod / Uu) ** 2
            vol += np.where(ok, w * val, 0.0)
        vol *= 0.5 * d_beta
        origin = [float(xs[0]), float(ys[0]), float(zs[0])]
        return vol, origin, voxel

    # ---------------------------------------------------------------- analysis
    def _vol(self, rid):
        r = self.records.get(rid)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if r['kind'] != 'volume' or r['status'] != 'completed':
            raise ActionRejected('RECORD_NOT_SUITABLE')
        return r, self._data[rid]

    @staticmethod
    def _grids(d):
        nz, ny, nx = d['volume'].shape
        x0, y0, z0 = d['origin']
        v = d['voxel']
        return x0 + np.arange(nx) * v, y0 + np.arange(ny) * v, z0 + np.arange(nz) * v

    def _region_mask(self, d, region):
        if region is None:
            return None
        if region['z_max_m'] <= region['z_min_m']:
            raise ActionRejected('INVALID_REGION')
        xs, ys, zs = self._grids(d)
        Z, Y, X = np.meshgrid(zs, ys, xs, indexing='ij')
        return ((X - region['center_m'][0]) ** 2 + (Y - region['center_m'][1]) ** 2 <= region['radius_m'] ** 2) & (Z >= region['z_min_m']) & (Z <= region['z_max_m'])

    @staticmethod
    def _otsu(x, bins=256):
        hist, edges = np.histogram(x, bins=bins)
        c = (edges[:-1] + edges[1:]) / 2
        w0 = np.cumsum(hist)
        w1 = w0[-1] - w0
        m0 = np.cumsum(hist * c) / np.maximum(w0, 1)
        m1 = ((hist * c).sum() - np.cumsum(hist * c)) / np.maximum(w1, 1)
        k = int(np.argmax(w0 * w1 * (m0 - m1) ** 2))
        return float(c[k])

    def _segment(self, d, region, threshold):
        """Sample envelope (Otsu between air and material, holes filled slice by slice and in 3D, one voxel eroded against the partial-volume
        shell, clipped to the region) and the pore threshold (ISO 50: midway between the air and the matrix levels, unless given)."""
        from scipy import ndimage
        vol = d['volume']
        t_env = self._otsu(vol.ravel())
        solid = vol > t_env
        env = np.stack([ndimage.binary_fill_holes(sl) for sl in solid])
        env = ndimage.binary_fill_holes(env)
        env = ndimage.binary_erosion(env, iterations=1)
        mask = self._region_mask(d, region)
        if mask is not None:
            env &= mask
        outside = ~ndimage.binary_dilation(solid, iterations=2)
        mu_air = float(np.median(vol[outside])) if outside.any() else 0.0
        mu_mat = float(np.median(vol[env])) if env.any() else t_env
        t = threshold if threshold is not None else 0.5 * (mu_air + mu_mat)
        return env, t, mu_air, mu_mat

    def _cmd_data_porosity(self, record_id, region, threshold_1_m, min_pore_voxels):
        from scipy import ndimage
        r, d = self._vol(record_id)
        env, t, mu_air, mu_mat = self._segment(d, region, threshold_1_m)
        n_env = int(env.sum())
        v3 = d['voxel'] ** 3
        empty = dict(mean=None, d10=None, d50=None, d90=None, max=None)
        if n_env < 27:
            return dict(record_id=record_id, threshold_1_m=t, method='iso50' if threshold_1_m is None else 'manual', envelope_voxels=n_env, pore_voxels=0, porosity=0.0,
                        open_porosity=0.0, closed_porosity=0.0, pore_count=0, equivalent_diameter_m=empty, voxel_m=d['voxel'], note_zh='区域内没有足够的样品体素')
        pores = env & (d['volume'] < t)
        lab, n = ndimage.label(pores, structure=np.ones((3, 3, 3)))
        sizes = ndimage.sum(pores, lab, np.arange(1, n + 1)) if n else np.array([])
        keep = np.where(sizes >= min_pore_voxels)[0] + 1
        boundary = env & ~ndimage.binary_erosion(env)
        open_ids = set(np.unique(lab[boundary]).tolist()) - {0}
        vox = sizes[keep - 1] if len(keep) else np.array([])
        n_open = float(sum(sizes[i - 1] for i in keep if i in open_ids))
        eqd = np.sort((6 * vox * v3 / math.pi) ** (1 / 3)) if len(vox) else np.array([])
        por = float(vox.sum() / n_env)
        stats = (dict(mean=float(eqd.mean()), d10=float(np.percentile(eqd, 10)), d50=float(np.percentile(eqd, 50)), d90=float(np.percentile(eqd, 90)), max=float(eqd.max()))
                 if len(eqd) else empty)
        self.last_porosity, self.last_porosity_rid = por, record_id
        self._porosity_owner = None
        self.revision += 1
        return dict(record_id=record_id, threshold_1_m=t, method='iso50' if threshold_1_m is None else 'manual', envelope_voxels=n_env, pore_voxels=int(vox.sum()),
                    porosity=por, open_porosity=n_open / n_env, closed_porosity=por - n_open / n_env, pore_count=int(len(keep)), equivalent_diameter_m=stats,
                    voxel_m=d['voxel'], note_zh=f'阈值{t:.1f} 1/m（空气{mu_air:.1f}、基体{mu_mat:.1f}）；体素{d["voxel"] * 1e6:.1f} µm，小于约2体素的孔无法分辨')

    def _cmd_data_cracks(self, record_id, region, threshold_1_m, min_voxels):
        from scipy import ndimage
        r, d = self._vol(record_id)
        env, t, mu_air, mu_mat = self._segment(d, region, threshold_1_m)
        xs, ys, zs = self._grids(d)
        low = env & (d['volume'] < t)
        lab, n = ndimage.label(low, structure=np.ones((3, 3, 3)))
        out = []
        for j in range(1, n + 1):
            idx = np.argwhere(lab == j)
            if len(idx) < min_voxels:
                continue
            P = np.column_stack([xs[idx[:, 2]], ys[idx[:, 1]], zs[idx[:, 0]]])
            c = P.mean(0)
            w, vec = np.linalg.eigh(np.cov((P - c).T) + np.eye(3) * (d['voxel'] ** 2 / 12))
            l3, l2, l1 = w
            if math.sqrt(l3 / l1) < 0.3 and math.sqrt(l2 / l1) > 0.35:
                R1, R2 = 2 * math.sqrt(l1), 2 * math.sqrt(l2)
                area = math.pi * R1 * R2
                out.append(dict(area_m2=area, aperture_m=len(idx) * d['voxel'] ** 3 / area, length_m=2 * R1, normal=[float(x) for x in vec[:, 0]],
                                center_m=[float(x) for x in c], voxels=int(len(idx))))
        out.sort(key=lambda x: -x['area_m2'])
        return dict(record_id=record_id, threshold_1_m=t, cracks=out[:32], crack_count=len(out), total_area_m2=float(sum(x['area_m2'] for x in out)),
                    note_zh=f'平面状低衰减连通体（厚/长<0.3，宽/长>0.35）；开口受体素{d["voxel"] * 1e6:.1f} µm限制（部分体积）')

    def _cmd_data_attenuation(self, record_id, region):
        r, d = self._vol(record_id)
        if region is None:
            env, _, _, _ = self._segment(d, None, None)
            m = env
        else:
            m = self._region_mask(d, region)
            if not m.any():
                raise ActionRejected('INVALID_REGION')                   # the region holds no voxel of the volume
        x = d['volume'][m] if m.any() else d['volume'].ravel()
        return dict(record_id=record_id, mean_1_m=float(x.mean()), std_1_m=float(x.std()), median_1_m=float(np.median(x)), p05_1_m=float(np.percentile(x, 5)),
                    p95_1_m=float(np.percentile(x, 95)), voxels=int(x.size))

    def _cmd_data_slice(self, record_id, axis, position_m, size_px):
        from scipy.ndimage import map_coordinates
        r, d = self._vol(record_id)
        vol = d['volume']
        xs, ys, zs = self._grids(d)
        g = dict(x=xs, y=ys, z=zs)
        if not g[axis][0] - 0.5 * d['voxel'] <= position_m <= g[axis][-1] + 0.5 * d['voxel']:
            raise ActionRejected('OUT_OF_VOLUME')
        other = [a for a in 'xyz' if a != axis]
        a0, a1 = g[other[0]], g[other[1]]
        ext = max(a0[-1] - a0[0], a1[-1] - a1[0]) + d['voxel']
        pix = ext / size_px
        c0, c1 = (a0[0] + a0[-1]) / 2, (a1[0] + a1[-1]) / 2
        p0 = c0 + (np.arange(size_px) + 0.5 - size_px / 2) * pix
        p1 = c1 + (np.arange(size_px) + 0.5 - size_px / 2) * pix
        B, A = np.meshgrid(p1, p0, indexing='ij')
        coord = {axis: np.full(A.shape, position_m), other[0]: A, other[1]: B}
        idx = [(coord[k] - g[k][0]) / d['voxel'] for k in ('z', 'y', 'x')]
        img = map_coordinates(vol, idx, order=1, mode='constant', cval=0.0)
        return dict(record_id=record_id, axis=axis, position_m=position_m, pixel_m=pix, mu_1_m=[[float(v) for v in row] for row in img])

    def _cmd_data_edge_resolution(self, record_id):
        r = self.records.get(record_id)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if r['kind'] != 'radiograph' or r['status'] != 'completed' or record_id not in self._data:
            raise ActionRejected('RECORD_NOT_SUITABLE')
        d = self._data[record_id]
        t = d['image'].astype(float)
        q = -np.log(np.clip(t, 1e-6, None))
        rows, cols = q.shape
        pitch = PIXEL_PITCH_M * d['binning'] if d['roi'] else DETECTOR_PIXELS[0] * PIXEL_PITCH_M / SIM_DETECTOR[0]
        M = SDD_M / d['sod']
        lo, hi = np.percentile(q, 5), np.percentile(q, 95)
        if hi - lo < 0.05:
            return dict(record_id=record_id, width_10_90_m=None, width_detector_px=None, edge_angle_rad=None, note_zh='图像中没有足够对比度的刃边')
        mid = 0.5 * (lo + hi)
        xe, ye = [], []
        for i in range(rows):
            p = q[i]
            s = np.where(np.diff(np.sign(p - mid)) != 0)[0]
            if len(s) == 1:
                j = s[0]
                xe.append(j + (mid - p[j]) / (p[j + 1] - p[j])); ye.append(i)
        if len(xe) < rows // 3:
            return dict(record_id=record_id, width_10_90_m=None, width_detector_px=None, edge_angle_rad=None, note_zh='找不到单一直刃边（每行应只穿越一次）')
        k, b0 = np.polyfit(ye, xe, 1)
        Y, X = np.mgrid[0:rows, 0:cols]
        dist = (X - (k * Y + b0)) / math.sqrt(1 + k * k)
        order = np.argsort(dist.ravel())
        ds, qs = dist.ravel()[order], q.ravel()[order]
        bins = np.arange(-12, 12.25, 0.25)
        prof = np.array([qs[(ds >= a) & (ds < a + 0.25)].mean() if np.any((ds >= a) & (ds < a + 0.25)) else np.nan for a in bins[:-1]])
        xc = bins[:-1] + 0.125
        ok = np.isfinite(prof)
        xc, prof = xc[ok], prof[ok]
        a, bnd = prof[:8].mean(), prof[-8:].mean()
        f = (prof - a) / (bnd - a)
        if f[-1] < f[0]:
            f = 1 - f
        f = np.maximum.accumulate(np.clip(f, 0, 1))
        x10, x90 = float(np.interp(0.1, f, xc)), float(np.interp(0.9, f, xc))
        w_px = x90 - x10
        return dict(record_id=record_id, width_10_90_m=w_px * pitch / M, width_detector_px=w_px, edge_angle_rad=float(math.atan(k)),
                    note_zh=f'刃边扩展函数10–90 %宽度{w_px:.2f}像素（探测器像素{pitch * 1e6:.1f} µm，放大{M:.2f}）')

    # ---------------------------------------------------------------- queries
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
        out = {k: v.value() for k, v in self.led.items()}
        return out

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        lib = library()
        return dict(library=LIBRARY_REL, library_sha256=lib['sha256'], substances=len(lib['substances']), license=lib['license_zh'])


ASYNC = {'calibration.flat_field', 'calibration.geometry', 'xray.radiograph', 'scan.start', 'recon.start'}
ACQUISITION = {'calibration.flat_field', 'calibration.geometry', 'xray.radiograph', 'scan.start'}
_DATA_GUARDED = {'measurement.result', 'records.delete'}
_RECORD_ARG = {'measurement.result': 'record_id', 'records.delete': 'record_id'}
_ANALYSIS_RECORD_ARGS = {'recon.start': ['record_id'], 'data.porosity': ['record_id'], 'data.cracks': ['record_id'], 'data.attenuation': ['record_id'],
                         'data.slice': ['record_id'], 'data.edge_resolution': ['record_id']}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED}


class BrukerX4PoseidonSession:
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
        meta = copy.deepcopy(export_bruker_x4poseidon_contract_metadata())
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
            if h.recon is not None and self._foreign(h.recon['op']):
                values['recon.id'] = None
            values['result.last_record_id'] = h._last_record(lambda k: not self._foreign(k))
            if self._foreign(h.last_porosity_rid) or (h._porosity_owner not in (None, self._principal) and h._principals.get(h._porosity_owner) == 'agent'):
                values['result.last_porosity'] = None                    # another agent's analysis (or an analysis of its volume)
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
            if capability_id == 'emergency.stop':                    # the safety button always works
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED:
            rid = arguments.get(_RECORD_ARG[capability_id])
            if self._foreign(rid):
                raise ActionRejected('ACCESS_DENIED')
            owner = h._owners.get(rid)
            if capability_id == 'records.delete' and owner is not None and owner != self._principal:
                raise ActionRejected('ACCESS_DENIED')                  # an agent deletes only its own records and those of the host
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
                    result = dict(result, template_source=dict(library=result['template_source']['library'], substances=[]), dose_gy=None)
            if capability_id == 'data.porosity':
                h._porosity_owner = self._principal
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
        if h._owners.get(operation_id) != self._principal and self._role != 'evaluator':
            raise ActionRejected('ACCESS_DENIED')                       # evaluators observe every operation
        if op.get('record_id') in h.records:
            res = h._out(h.records[op['record_id']])
            if self._role == 'agent':
                res['template_source'] = dict(library=res['template_source']['library'], substances=[])
                res['dose_gy'] = None                                   # absorbed dose follows the true sample mass: evaluator only
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


ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（评测槽、另一个agent的记录或操作）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '系统未通电', 'NOT_READY': '系统启动中',
    'ERROR_PENDING': '有未确认的错误', 'EMERGENCY_STOP': '安全按钮被按下（锁存）', 'CAUSE_PERSISTS': '错误原因仍存在，不能确认',
    'MAINS_OUT_OF_RANGE': '电源电压超出100–240 VAC ±10 %', 'MAINS_FAULT': '供电电压超出范围，系统断电', 'UNKNOWN_SUBSTANCE': '模板库中没有该材料',
    'SAMPLE_EXISTS': '样品ID已存在', 'TOO_MANY_SAMPLES': '样品数量超过上限',
    'INVALID_SAMPLE': '样品定义不自洽（层厚之和须等于高度、体积分数之和为1、裂纹/夹杂须在所在层与圆柱内、孔过密无法不重叠放置、体模不能带层/孔/裂纹/夹杂）',
    'UNKNOWN_SAMPLE': '样品不存在', 'SAMPLE_NOT_ON_BENCH': '样品不在实验台上', 'STAGE_OCCUPIED': '样品夹上已有样品', 'DOOR_CLOSED': '门没有完全打开',
    'NOT_ACCESSIBLE': '样品夹不在门口范围（先stage.go_to load）', 'COLLISION_RISK': '目标位置或移动路径上样品、样品针、样品夹或旋转台会碰到滤片转换器/源头部、探测器外壳或腔顶（碰撞保护）',
    'SAMPLE_NOT_ON_STAGE': '该样品不在样品夹上', 'MEASUREMENT_RUNNING': '有采集操作进行中', 'MOTION_IN_PROGRESS': '门、滤片或样品台在移动',
    'XRAY_ON': 'X射线开启时门锁住', 'LIGHT_CURTAIN_BLOCKED': '门口安全光幕被挡，门不能关闭', 'POWER_LIMIT': '靶功率超过20 W',
    'DOOR_OPEN': '门没有关闭', 'INTERLOCK_OPEN': 'X射线联锁未满足（门、联锁传感器或安全按钮）', 'XRAY_ARC': 'X射线管打火',
    'INTERLOCK_FAULT': '门联锁传感器故障', 'STAGE_FAULT': '旋转台故障（卡滞）',
    'RECORDS_FULL': '记录数已达上限', 'XRAY_NOT_READY': 'X射线未开启、未预热完成或未稳定在设定值', 'NO_SAMPLE': '样品夹上没有样品',
    'FLAT_FIELD_REQUIRED': '当前电压、电流、滤片、合并与曝光没有有效的暗场/亮场校正', 'SAMPLE_IN_BEAM': '亮场校正时样品、样品针、样品夹或旋转台在光束中（先降低或移开）',
    'PHANTOM_REQUIRED': '几何标定需要钢球标定体模', 'SAMPLE_NOT_IN_FIELD': '样品不在探测器视场内', 'UNKNOWN_RECORD': '记录不存在',
    'RECORD_NOT_SUITABLE': '记录种类或状态不适用', 'RECON_BUSY': '工作站正在进行另一个重建', 'INVALID_VOI': '感兴趣体积的中心不在采集窗口可重建的区域内',
    'INVALID_REGION': '分析区域无效（z上限须大于下限，区域须含体数据的体素）', 'OUT_OF_VOLUME': '切片位置不在体数据内', 'NOT_RUNNING': '操作不在进行中',
    'ROI_OUTSIDE_DETECTOR': '局部区域超出探测器有效面', 'PHANTOM_NOT_IN_FIELD': '钢球标定体模须在所有角度完整位于视场内（降低放大或调整高度）',
    'DATA_NOT_CACHED': '投影数据已不在缓存中（只保留最近8组投影/透视）', 'RECORD_IN_USE': '记录正在使用（进行中的操作或正被重建的投影）',
    'UNKNOWN_OPERATION': '操作不存在', 'USER_CANCEL': '操作被取消', 'XRAY_OFF': '采集中X射线被关闭', 'DATA_EVICTED': '投影数据已从缓存中清除（只保留最近8组投影/透视）',
}
EFFECTS = {
    'plant.configure': ['door.closed', 'door.moving', 'door.target_m', 'filter.moving', 'interlock.ok', 'measurement.id', 'measurement.kind', 'measurement.progress',
        'measurement.remaining_s', 'panel.status_green', 'panel.status_orange', 'panel.status_red', 'plant.configured', 'power.enabled', 'stage.chuck_z_target_m',
        'stage.moving', 'stage.rotation_target_rad', 'stage.sod_target_m', 'system.error_code', 'system.state', 'truth.axis_offset_m', 'xray.current_a', 'xray.enabled',
        'xray.focal_spot_m', 'xray.on', 'xray.power_w', 'xray.ready', 'xray.voltage_v', 'xray.warmup_remaining_s'],
    'sample.provide': ['sample.bench_ids', 'truth.samples'],
    'sample.place': ['sample.bench_ids', 'sample.on_stage', 'truth.samples'],
    'sample.remove': ['sample.bench_ids', 'sample.on_stage', 'truth.samples'],
    'power.set': ['door.closed', 'door.moving', 'door.target_m', 'filter.moving', 'interlock.ok', 'measurement.id', 'measurement.kind', 'measurement.progress',
        'measurement.remaining_s', 'panel.status_green', 'panel.status_orange', 'panel.status_red', 'power.enabled', 'stage.chuck_z_target_m', 'stage.moving',
        'stage.rotation_target_rad', 'stage.sod_target_m', 'system.error_code', 'system.state', 'xray.current_a', 'xray.enabled', 'xray.focal_spot_m', 'xray.on',
        'xray.power_w', 'xray.ready', 'xray.voltage_v', 'xray.warmup_remaining_s'],
    'power.press_button': ['door.closed', 'door.moving', 'door.target_m', 'filter.moving', 'interlock.ok', 'measurement.id', 'measurement.kind', 'measurement.progress',
        'measurement.remaining_s', 'panel.status_green', 'panel.status_orange', 'panel.status_red', 'power.enabled', 'stage.chuck_z_target_m', 'stage.moving',
        'stage.rotation_target_rad', 'stage.sod_target_m', 'system.error_code', 'system.state', 'xray.current_a', 'xray.enabled', 'xray.focal_spot_m', 'xray.on',
        'xray.power_w', 'xray.ready', 'xray.voltage_v', 'xray.warmup_remaining_s'],
    'emergency.stop': ['door.closed', 'door.moving', 'door.target_m', 'emergency.stopped', 'filter.moving', 'interlock.ok', 'measurement.id', 'measurement.kind',
        'measurement.progress', 'measurement.remaining_s', 'panel.status_green', 'panel.status_orange', 'panel.status_red', 'stage.chuck_z_target_m', 'stage.moving',
        'stage.rotation_target_rad', 'stage.sod_target_m', 'system.error_code', 'xray.current_a', 'xray.enabled', 'xray.focal_spot_m', 'xray.on', 'xray.power_w',
        'xray.ready', 'xray.voltage_v', 'xray.warmup_remaining_s'],
    'emergency.release': ['emergency.stopped', 'interlock.ok'],
    'system.acknowledge': ['panel.status_green', 'panel.status_red', 'system.error_code'],
    'fault.inject': ['door.target_m', 'interlock.ok', 'light_curtain.blocked', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s',
        'panel.status_green', 'panel.status_orange', 'panel.status_red', 'stage.moving', 'stage.rotation_target_rad', 'system.error_code',
        'truth.detector_defect_columns', 'truth.faults', 'xray.current_a', 'xray.enabled', 'xray.focal_spot_m', 'xray.on', 'xray.power_w', 'xray.ready',
        'xray.voltage_v', 'xray.warmup_remaining_s'],
    'door.open': ['door.closed', 'door.moving', 'door.target_m', 'interlock.ok'],
    'door.close': ['door.closed', 'door.moving', 'door.target_m', 'interlock.ok'],
    'xray.set': ['calibration.flat_field', 'xray.current_set_a', 'xray.focal_spot_m', 'xray.ready', 'xray.voltage_set_v'],
    'xray.enable': ['panel.status_green', 'panel.status_orange', 'xray.current_a', 'xray.enabled', 'xray.focal_spot_m', 'xray.on', 'xray.power_w', 'xray.ready',
        'xray.voltage_v', 'xray.warmup_remaining_s'],
    'filter.select': ['calibration.flat_field', 'filter.label', 'filter.moving', 'filter.position'],
    'stage.move': ['stage.chuck_z_target_m', 'stage.moving', 'stage.position_label', 'stage.sod_target_m'],
    'stage.rotate': ['stage.moving', 'stage.rotation_target_rad'],
    'stage.go_to': ['stage.chuck_z_target_m', 'stage.moving', 'stage.position_label', 'stage.sod_target_m'],
    'scan.configure': ['calibration.flat_field', 'geometry.voxel_size_m', 'scan.duration_s', 'scan.projections', 'scan.settings'],
    'calibration.flat_field': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent'],
    'calibration.geometry': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent'],
    'xray.radiograph': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent'],
    'scan.start': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent'],
    'recon.start': ['recon.id', 'recon.remaining_s'],
    'data.porosity': ['result.last_porosity'],
    'data.cracks': [],
    'data.attenuation': [],
    'data.slice': [],
    'data.edge_resolution': [],
    'records.delete': ['result.last_porosity', 'result.last_record_id'],
}
EFFECTS['sim.step'] = ['clock.time_s']
ON_STEP = {
    'plant.configure': ['calibration.flat_field', 'calibration.geometry', 'door.closed', 'door.moving', 'door.position_m', 'filter.moving', 'filter.slide_m',
        'geometry.fov_m', 'geometry.magnification', 'geometry.voxel_size_m', 'interlock.ok', 'measurement.id', 'measurement.kind', 'measurement.progress',
        'measurement.remaining_s', 'panel.status_green', 'panel.status_orange', 'result.last_record_id', 'stage.chuck_z_m', 'stage.moving', 'stage.rotation_rad',
        'stage.rotation_target_rad', 'stage.sod_m', 'system.state', 'truth.samples', 'xray.current_a', 'xray.focal_spot_m', 'xray.on', 'xray.power_w', 'xray.ready',
        'xray.voltage_v', 'xray.warmup_remaining_s'],
    'power.set': ['calibration.flat_field', 'calibration.geometry', 'door.closed', 'door.moving', 'door.position_m', 'filter.moving', 'filter.slide_m', 'geometry.fov_m',
        'geometry.magnification', 'geometry.voxel_size_m', 'interlock.ok', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s',
        'panel.status_green', 'panel.status_orange', 'result.last_record_id', 'stage.chuck_z_m', 'stage.moving', 'stage.rotation_rad', 'stage.rotation_target_rad',
        'stage.sod_m', 'system.state', 'truth.samples', 'xray.current_a', 'xray.focal_spot_m', 'xray.on', 'xray.power_w', 'xray.ready', 'xray.voltage_v',
        'xray.warmup_remaining_s'],
    'power.press_button': ['calibration.flat_field', 'calibration.geometry', 'door.closed', 'door.moving', 'door.position_m', 'filter.moving', 'filter.slide_m',
        'geometry.fov_m', 'geometry.magnification', 'geometry.voxel_size_m', 'interlock.ok', 'measurement.id', 'measurement.kind', 'measurement.progress',
        'measurement.remaining_s', 'panel.status_green', 'panel.status_orange', 'result.last_record_id', 'stage.chuck_z_m', 'stage.moving', 'stage.rotation_rad',
        'stage.rotation_target_rad', 'stage.sod_m', 'system.state', 'truth.samples', 'xray.current_a', 'xray.focal_spot_m', 'xray.on', 'xray.power_w', 'xray.ready',
        'xray.voltage_v', 'xray.warmup_remaining_s'],
    'emergency.stop': ['calibration.flat_field', 'calibration.geometry', 'door.closed', 'door.moving', 'door.position_m', 'filter.moving', 'filter.slide_m',
        'geometry.fov_m', 'geometry.magnification', 'geometry.voxel_size_m', 'interlock.ok', 'measurement.id', 'measurement.kind', 'measurement.progress',
        'measurement.remaining_s', 'panel.status_green', 'panel.status_orange', 'result.last_record_id', 'stage.chuck_z_m', 'stage.moving', 'stage.rotation_rad',
        'stage.rotation_target_rad', 'stage.sod_m', 'truth.samples', 'xray.current_a', 'xray.focal_spot_m', 'xray.on', 'xray.power_w', 'xray.ready', 'xray.voltage_v',
        'xray.warmup_remaining_s'],
    'fault.inject': ['calibration.flat_field', 'calibration.geometry', 'door.closed', 'door.moving', 'door.position_m', 'interlock.ok', 'measurement.id',
        'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'panel.status_green', 'panel.status_orange', 'result.last_record_id', 'stage.moving',
        'stage.rotation_rad', 'stage.rotation_target_rad', 'truth.samples', 'xray.current_a', 'xray.focal_spot_m', 'xray.on', 'xray.power_w', 'xray.ready',
        'xray.voltage_v', 'xray.warmup_remaining_s'],
    'door.open': ['door.closed', 'door.moving', 'door.position_m', 'interlock.ok'],
    'door.close': ['door.closed', 'door.moving', 'door.position_m', 'interlock.ok'],
    'xray.set': ['truth.samples', 'xray.current_a', 'xray.focal_spot_m', 'xray.power_w', 'xray.ready', 'xray.voltage_v'],
    'xray.enable': ['panel.status_green', 'panel.status_orange', 'truth.samples', 'xray.current_a', 'xray.focal_spot_m', 'xray.on', 'xray.power_w', 'xray.ready',
        'xray.voltage_v', 'xray.warmup_remaining_s'],
    'filter.select': ['filter.moving', 'filter.slide_m', 'truth.samples'],
    'stage.move': ['geometry.fov_m', 'geometry.magnification', 'geometry.voxel_size_m', 'stage.chuck_z_m', 'stage.moving', 'stage.sod_m', 'truth.samples'],
    'stage.rotate': ['stage.moving', 'stage.rotation_rad', 'truth.samples'],
    'stage.go_to': ['geometry.fov_m', 'geometry.magnification', 'geometry.voxel_size_m', 'stage.chuck_z_m', 'stage.moving', 'stage.sod_m', 'truth.samples'],
    'scan.configure': ['geometry.voxel_size_m'],
    'calibration.flat_field': ['calibration.flat_field', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s',
        'measurement.started_by_other_agent', 'result.last_record_id'],
    'calibration.geometry': ['calibration.geometry', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s',
        'measurement.started_by_other_agent', 'result.last_record_id', 'stage.rotation_rad', 'stage.rotation_target_rad', 'truth.samples'],
    'xray.radiograph': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent',
        'result.last_record_id'],
    'scan.start': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent',
        'result.last_record_id', 'stage.rotation_rad', 'stage.rotation_target_rad', 'truth.samples'],
    'recon.start': ['recon.id', 'recon.remaining_s', 'result.last_record_id'],
    'records.delete': ['result.last_record_id'],
    'sim.step': ['calibration.flat_field', 'calibration.geometry', 'door.closed', 'door.moving', 'door.position_m', 'filter.moving', 'filter.slide_m', 'geometry.fov_m',
        'geometry.magnification', 'geometry.voxel_size_m', 'interlock.ok', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s',
        'measurement.started_by_other_agent', 'panel.status_green', 'panel.status_orange', 'recon.id', 'recon.remaining_s', 'result.last_record_id', 'stage.chuck_z_m',
        'stage.moving', 'stage.rotation_rad', 'stage.rotation_target_rad', 'stage.sod_m', 'system.state', 'truth.samples', 'xray.current_a', 'xray.focal_spot_m',
        'xray.on', 'xray.power_w', 'xray.ready', 'xray.voltage_v', 'xray.warmup_remaining_s'],
}
ON_STEP_ZH = {
    'power.set': '启动60 s后进入就绪（各轴保持位置）；断电时各轴停止', 'power.press_button': '同power.set', 'door.open': '门以0.1 m/s滑开', 'door.close': '门以0.1 m/s关闭',
    'xray.enable': '电压与电流按斜率（或上电后首次开启的180 s预热程序，升到开启时的设定值）升到设定值，X射线能量计入账本',
    'xray.set': '束流开启时电压与电流按斜率变化到新设定值（预热中程序照旧，结束后再按斜率变化）',
    'filter.select': '滤片滑块每档0.5 s（光束能谱随滑块经过的滤片变化）', 'stage.move': '滑台20 mm/s、升降10 mm/s', 'stage.go_to': '同stage.move', 'stage.rotate': '旋转60°/s',
    'calibration.flat_field': '暗场与亮场各20帧后写入校正', 'calibration.geometry': '36个投影、旋转一周后写入标定', 'xray.radiograph': '曝光×帧平均后写入透视记录',
    'scan.start': '按投影时间连续旋转，完成后写入投影记录', 'recon.start': '按工作站重建时间完成后写入体数据', 'sim.step': '推进全部运动、X射线斜坡、操作阶段与能量账',
    'emergency.stop': '已停止（无后续演化）', 'fault.inject': '故障对进行中的操作与运动的影响', 'records.delete': '删除本身没有后续演化；之后完成的采集或重建写入新的最新记录号（有无删除都一样；按有/无调用的对比列为步进效应）',
}
_R = ['power.enabled', 'system.state', 'system.error_code', 'emergency.stopped']
_ACQ = _R + ['measurement.id', 'door.closed', 'xray.ready', 'stage.moving', 'filter.moving', 'door.moving']
PRECONDITIONS = {
    'plant.configure': ('噪声种子只能在首次声明时设定', ['plant.configured']),
    'sample.provide': ('已声明实验室条件；样品ID未用过；材料在模板库中；几何自洽（层厚之和等于高度、体积分数之和为1、裂纹/夹杂在所在层内）', ['plant.configured', 'sample.bench_ids']),
    'sample.place': ('样品在实验台上、样品夹空；门完全打开且不在移动；无采集操作；样品台静止并在门口范围；样品包络与滤片转换器、探测器盖板、腔顶留有2 mm',
                     ['sample.bench_ids', 'sample.on_stage', 'door.position_m', 'door.moving', 'measurement.id', 'stage.moving', 'stage.sod_m', 'stage.chuck_z_m']),
    'sample.remove': ('样品在样品夹上；门完全打开；无采集操作；样品台静止并在门口范围', ['sample.on_stage', 'door.position_m', 'measurement.id', 'stage.moving', 'stage.sod_m']),
    'power.set': ('已声明实验室条件；上电需电源电压在100–240 VAC ±10 %内；上电时仍存在的原因（安全按钮锁存、故障）按优先级锁存错误', ['plant.configured']),
    'power.press_button': ('同power.set（按一次切换通断）', ['plant.configured', 'power.enabled']),
    'emergency.stop': ('已声明实验室条件；任何时候可按', ['plant.configured']), 'emergency.release': ('已声明实验室条件', ['plant.configured', 'emergency.stopped']),
    'system.acknowledge': ('已通电；错误原因已排除（故障已清除、安全按钮已释放、电源恢复）', ['power.enabled', 'system.error_code', 'emergency.stopped']),
    'fault.inject': ('已声明实验室条件', ['plant.configured']),
    'door.open': ('已就绪、无错误、安全按钮未按下；无采集操作；X射线关闭（门锁）', _R + ['measurement.id', 'xray.on', 'xray.enabled']),
    'door.close': ('已就绪、无错误、安全按钮未按下；安全光幕未被挡', _R + ['light_curtain.blocked']),
    'xray.set': ('已声明实验室条件；电压20–100 kV、电流≤0.5 mA且功率≤20 W；无采集操作', ['plant.configured', 'measurement.id']),
    'xray.enable': ('开启：已就绪、无错误、安全按钮未按下、门关闭、联锁正常、无打火；关闭：无采集操作', _R + ['door.closed', 'interlock.ok', 'measurement.id']),
    'filter.select': ('已就绪、无错误；无采集操作', _R + ['measurement.id']),
    'stage.move': ('已就绪、无错误；旋转台无故障；无采集操作；目标位置与滑台/升降同时运动的路径上样品、样品针、样品夹与旋转台都不进入禁入区', _R + ['measurement.id', 'sample.on_stage', 'stage.sod_m', 'stage.chuck_z_m']),
    'stage.go_to': ('已就绪、无错误；旋转台无故障；无采集操作；从当前位置到装样位或中心位、滑台（20 mm/s）与升降（10 mm/s）同时运动的路径上样品、样品针、样品夹与旋转台'
                    '都不进入禁入区（路径从当前位置开始，贴近禁入区时先用stage.move分步移开）', _R + ['measurement.id', 'sample.on_stage', 'stage.sod_m', 'stage.chuck_z_m']), 'stage.rotate': ('已就绪、无错误；旋转台无故障；无采集操作', _R + ['measurement.id']),
    'scan.configure': ('已声明实验室条件；无采集操作', ['plant.configured', 'measurement.id']),
    'calibration.flat_field': ('已就绪、无错误；无采集操作与运动；门关闭；X射线就绪；样品、样品针、样品夹与旋转台都不在光束中', _ACQ + ['sample.on_stage', 'stage.sod_m', 'stage.chuck_z_m']),
    'calibration.geometry': ('同扫描；样品夹上为钢球标定体模且在所有角度完整位于视场内；有效的暗场/亮场校正', _ACQ + ['sample.on_stage', 'calibration.flat_field', 'stage.sod_m', 'stage.chuck_z_m']),
    'xray.radiograph': ('已就绪、无错误；无采集操作与运动；门关闭；X射线就绪；有效的暗场/亮场校正；局部区域在探测器有效面内', _ACQ + ['calibration.flat_field']),
    'scan.start': ('同透视；样品夹上有样品且样品本体在视场内', _ACQ + ['sample.on_stage', 'calibration.flat_field', 'stage.sod_m', 'stage.chuck_z_m']),
    'recon.start': ('投影记录已完成且投影数据仍在缓存；工作站没有另一个重建；感兴趣体积的中心在采集窗口可重建的区域内（留出自动对准±200 µm；超出部分给出警告）', ['recon.id']),
    'records.delete': ('记录存在且不在使用中（进行中的操作、正被重建的投影记录）；agent只能删除自己或宿主的记录', ['result.last_record_id', 'recon.id', 'measurement.id']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_RE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'EMERGENCY_STOP', 'ERROR_PENDING']
_ACQ_ERR = _RE + ['MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'DOOR_OPEN', 'XRAY_NOT_READY', 'STAGE_FAULT', 'RECORDS_FULL']
_ACQ_FAIL = ['POWER_OFF', 'EMERGENCY_STOP', 'XRAY_ARC', 'INTERLOCK_FAULT', 'USER_CANCEL']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'sample.provide': ['PLANT_REQUIRED', 'SAMPLE_EXISTS', 'TOO_MANY_SAMPLES', 'INVALID_SAMPLE', 'UNKNOWN_SUBSTANCE'],
    'sample.place': ['PLANT_REQUIRED', 'UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'STAGE_OCCUPIED', 'MEASUREMENT_RUNNING', 'DOOR_CLOSED', 'MOTION_IN_PROGRESS',
                     'NOT_ACCESSIBLE', 'COLLISION_RISK'],
    'sample.remove': ['PLANT_REQUIRED', 'SAMPLE_NOT_ON_STAGE', 'MEASUREMENT_RUNNING', 'DOOR_CLOSED', 'MOTION_IN_PROGRESS', 'NOT_ACCESSIBLE'],
    'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_button': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'emergency.stop': ['PLANT_REQUIRED'], 'emergency.release': ['PLANT_REQUIRED'], 'system.acknowledge': ['PLANT_REQUIRED', 'POWER_OFF', 'CAUSE_PERSISTS'],
    'fault.inject': ['PLANT_REQUIRED'], 'door.open': _RE + ['MEASUREMENT_RUNNING', 'XRAY_ON'], 'door.close': _RE + ['LIGHT_CURTAIN_BLOCKED'],
    'xray.set': ['PLANT_REQUIRED', 'POWER_LIMIT', 'MEASUREMENT_RUNNING'],
    'xray.enable': _RE + ['DOOR_OPEN', 'INTERLOCK_OPEN', 'XRAY_ARC', 'MEASUREMENT_RUNNING'], 'filter.select': _RE + ['MEASUREMENT_RUNNING'],
    'stage.move': _RE + ['STAGE_FAULT', 'MEASUREMENT_RUNNING', 'COLLISION_RISK'], 'stage.go_to': _RE + ['STAGE_FAULT', 'MEASUREMENT_RUNNING', 'COLLISION_RISK'],
    'stage.rotate': _RE + ['STAGE_FAULT', 'MEASUREMENT_RUNNING'], 'scan.configure': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING'],
    'calibration.flat_field': _ACQ_ERR + ['SAMPLE_IN_BEAM'] + _ACQ_FAIL,
    'calibration.geometry': _ACQ_ERR + ['NO_SAMPLE', 'FLAT_FIELD_REQUIRED', 'PHANTOM_REQUIRED', 'PHANTOM_NOT_IN_FIELD'] + _ACQ_FAIL,
    'xray.radiograph': _ACQ_ERR + ['FLAT_FIELD_REQUIRED', 'ROI_OUTSIDE_DETECTOR'] + _ACQ_FAIL,
    'scan.start': _ACQ_ERR + ['NO_SAMPLE', 'FLAT_FIELD_REQUIRED', 'SAMPLE_NOT_IN_FIELD'] + _ACQ_FAIL,
    'recon.start': ['PLANT_REQUIRED', 'UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'UNKNOWN_SUBSTANCE', 'RECON_BUSY', 'DATA_NOT_CACHED', 'INVALID_VOI', 'RECORDS_FULL',
                    'DATA_EVICTED', 'USER_CANCEL'],
    'records.delete': ['PLANT_REQUIRED', 'UNKNOWN_RECORD', 'RECORD_IN_USE'],
    'measurement.result': ['UNKNOWN_RECORD'], 'data.porosity': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'INVALID_REGION'],
    'data.cracks': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'INVALID_REGION'], 'data.attenuation': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'INVALID_REGION'],
    'data.slice': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'OUT_OF_VOLUME'], 'data.edge_resolution': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE'],
}
_PARTIAL = {'USER_CANCEL', 'POWER_OFF', 'EMERGENCY_STOP', 'XRAY_ARC', 'INTERLOCK_FAULT', 'DATA_EVICTED'}
CAP_ERROR_ZH = {('scan.start', 'POWER_OFF'): '未通电；或扫描中断电（记录以failed结束，不保留部分投影）',
                ('scan.start', 'STAGE_FAULT'): '旋转台故障；或扫描中旋转台卡滞（记录以failed结束）',
                ('scan.start', 'XRAY_ARC'): '扫描中X射线管打火：X射线关闭，记录以failed结束',
                ('recon.start', 'DATA_EVICTED'): '重建完成前投影数据已从缓存清除：记录以failed结束'}
MODEL_NONE_ZH = {
    'plant.configure': '实验室条件没有可见模型', 'sample.provide': '样品在实验台上，场景中没有样品几何', 'sample.place': '样品在场景中没有几何（运行层状态）',
    'sample.remove': '样品在场景中没有几何', 'fault.inject': '注入故障本身没有可见变化', 'system.acknowledge': '只清除错误状态（状态灯随之变化）',
    'xray.set': '只写设定（束流开启时电压电流读数随之变化）', 'scan.configure': '只写设置', 'recon.start': '工作站计算，仪器没有可见变化',
    'emergency.release': '只释放锁存（错误确认后状态灯变化）', 'records.delete': '只删除工作站上的记录，仪器与显示没有变化',
}
SCENE_MIRRORED = {'door.position_m', 'filter.slide_m', 'stage.sod_m', 'stage.chuck_z_m', 'stage.rotation_rad', 'xray.voltage_v', 'xray.current_a',
                  'measurement.remaining_s', 'result.last_porosity', 'panel.status_green', 'panel.status_orange', 'panel.status_red'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节、触摸屏读数或状态灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {   # asynchronous capabilities: completion criterion, partial failure
    'calibration.flat_field': ('暗场与亮场各20帧（每帧曝光+0.03 s读出）完成后写入校正与记录', '取消或失败（断电、安全按钮、打火、联锁）时不写入校正，旧校正保持；没有运动。'),
    'calibration.geometry': ('旋转一周36个投影完成后拟合钢球轨迹并写入旋转轴偏移', '取消或失败（断电、安全按钮、打火、联锁、旋转台卡滞）时不写入标定；旋转台停在当时角度。'),
    'xray.radiograph': ('曝光×帧平均完成后写入透视记录', '取消或失败时不写入图像；没有运动。'),
    'scan.start': ('按投影时间旋转完扫描范围后写入投影记录（投影在完成时计算）', '取消或失败（断电、安全按钮、打火、联锁、旋转台卡滞）时记录以cancelled/failed结束，不保留部分投影；旋转台停在当时角度。'),
    'recon.start': ('按工作站重建时间完成后计算体数据并写入记录', '取消时不写入体数据；源投影记录不变；投影数据已被清除时以failed结束。'),
}
RESOURCES = {'calibration.flat_field': ['xray_source', 'detector', 'host_clock'], 'calibration.geometry': ['xray_source', 'detector', 'rotation_stage', 'host_clock'],
             'xray.radiograph': ['xray_source', 'detector', 'host_clock'], 'scan.start': ['xray_source', 'detector', 'rotation_stage', 'host_clock'],
             'recon.start': ['workstation', 'host_clock'], 'door.open': ['door'], 'door.close': ['door'], 'xray.set': ['xray_source'], 'xray.enable': ['xray_source'],
             'filter.select': ['filter_changer'], 'stage.move': ['sample_carriage', 'sample_lift'], 'stage.go_to': ['sample_carriage', 'sample_lift'],
             'stage.rotate': ['rotation_stage'], 'power.set': ['mains'], 'power.press_button': ['mains'], 'emergency.stop': ['safety_circuit'],
             'emergency.release': ['safety_circuit'], 'plant.configure': ['room', 'mains'], 'sample.provide': ['bench'], 'sample.place': ['bench', 'sample_chuck'],
             'sample.remove': ['bench', 'sample_chuck'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock'], 'system.acknowledge': ['controller'],
             'scan.configure': ['controller'], 'records.delete': ['workstation']}
OBSERVES = {
    'power.set': ['power.enabled', 'system.state', 'panel.status_green'], 'power.press_button': ['power.enabled', 'system.state', 'panel.status_green'],
    'emergency.stop': ['emergency.stopped', 'panel.status_red'], 'emergency.release': ['emergency.stopped'], 'system.acknowledge': ['system.error_code'],
    'door.open': ['door.position_m', 'door.closed'], 'door.close': ['door.position_m', 'door.closed', 'light_curtain.blocked'],
    'xray.set': ['xray.voltage_set_v', 'xray.current_set_a', 'xray.focal_spot_m'], 'xray.enable': ['xray.on', 'xray.ready', 'xray.warmup_remaining_s', 'panel.status_orange'],
    'filter.select': ['filter.position', 'filter.label'], 'stage.move': ['stage.sod_m', 'stage.chuck_z_m', 'geometry.magnification', 'geometry.voxel_size_m'],
    'stage.go_to': ['stage.position_label', 'stage.sod_m'], 'stage.rotate': ['stage.rotation_rad'], 'scan.configure': ['scan.settings', 'scan.projections', 'scan.duration_s'],
    'calibration.flat_field': ['measurement.id', 'calibration.flat_field'], 'calibration.geometry': ['measurement.id', 'calibration.geometry'],
    'xray.radiograph': ['measurement.id', 'result.last_record_id'], 'scan.start': ['measurement.id', 'measurement.progress', 'result.last_record_id'],
    'recon.start': ['recon.id', 'recon.remaining_s', 'result.last_record_id'], 'data.porosity': ['result.last_porosity'],
    'sample.place': ['sample.on_stage'], 'sample.remove': ['sample.bench_ids'], 'records.delete': ['result.last_record_id', 'result.last_porosity'],
}
_SPEC = 'manufacturer_primary'
_BRO = 'manufacturer_brochures'
_BRL = 'manufacturer_brochures'
_CFG = 'manufacturer_configurator'
_PHOTO = 'product_photos'
_NIST = 'nist_srd126'
_PUB = 'pubchem_densities'
_LIBS = 'xray_attenuation_library'
_PHYS = 'physics_literature'
_SIM = 'simulation_assumption'
_WB = 'user_workbook'
CAP_SOURCES = {
    'plant.configure': [_SPEC, _SIM], 'sample.provide': [_LIBS, _NIST, _PUB, _WB, _SPEC], 'sample.place': [_SPEC, _BRO, _SIM], 'sample.remove': [_SIM],
    'power.set': [_SPEC, _SIM], 'power.press_button': [_SPEC, _PHOTO, _SIM], 'emergency.stop': [_BRO, _PHOTO, _SIM], 'emergency.release': [_SIM],
    'system.acknowledge': [_SIM], 'fault.inject': [_SIM], 'door.open': [_BRO, _PHOTO, _SIM], 'door.close': [_BRO, _SIM], 'xray.set': [_SPEC, _BRO, _WB],
    'xray.enable': [_SPEC, _BRO, _SIM, _PHYS], 'filter.select': [_BRO, _CFG, _SIM], 'stage.move': [_BRO, _SPEC, _SIM], 'stage.go_to': [_SIM],
    'stage.rotate': [_BRO, _CFG, _SIM], 'scan.configure': [_SPEC, _WB, _SIM], 'calibration.flat_field': [_SPEC, _SIM, _PHYS],
    'calibration.geometry': [_BRO, _SIM, _PHYS], 'xray.radiograph': [_SPEC, _WB, _LIBS, _NIST, _PHYS], 'scan.start': [_SPEC, _WB, _BRO, _LIBS, _NIST, _PHYS, _SIM],
    'recon.start': [_BRO, _WB, _PHYS], 'measurement.result': [_WB], 'data.porosity': [_WB, _PHYS], 'data.cracks': [_WB, _PHYS], 'data.attenuation': [_PHYS],
    'data.slice': [_WB], 'data.edge_resolution': [_BRO, _CFG, _PHYS], 'records.list': [_SIM], 'records.delete': [_SIM], 'events.read': [_SIM],
    'ledger.read': [_SIM, _PHYS, _NIST],
    'provenance.read': [_LIBS, _NIST, _PUB], 'sim.step': [_SIM], **{k: [_SPEC, _BRO, _CFG] for k in UNSUPPORTED}}
PARAM_ZH = {'room_k': '实验室室温（记录用；X射线计算的空气密度取NIST表列值，与室温无关）', 'mains_voltage_v': '供电电压（厂家100–240 VAC）',
            'relative_humidity': '相对湿度（记录用；不影响X射线计算）', 'noise_seed': '噪声种子（首次声明后固定）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.x4_poseidon_ct.limitations。'
CAP_LIMITS = {
    'sample.provide': ['样品为解析体模：竖直圆柱（最多6层，每层为模板库材料的体积混合，各组分按表列密度）、球形孔（对数正态半径、互不重叠、在指定层内）、平面圆盘裂纹、球形夹杂、'
                       '样品管与样品针；另有钢球几何标定体模与钨刃边体模。多孔材料的孔径小于模拟体素时看不到；样品在场景中没有几何。'],
    'sample.place': ['宿主/机器人抽象：门完全打开、样品夹在门口范围时装样；样品轴相对旋转轴的偏移≤5 mm；机器人接触与夹持未建模。'],
    'xray.set': ['管电压20–100 kV（下限为工程值）、电流≤0.5 mA（工程值）且功率≤20 W（厂家）；焦点=5 µm×max(1, P/4 W)（厂家给出4 W时<5 µm，增长规律为工程假设）。'],
    'xray.enable': ['每次上电后首次开启运行180 s预热程序（工程值），之后电压10 kV/s、电流100 µA/s斜坡（工程值）；门、联锁传感器与安全按钮任一不满足即立即关闭。'
                    '钨反射靶谱：Kramers厚靶轫致辐射（效率K·Z·V，K=1.1e-9 /V）、Kanaya-Okayama射程1/3深度按20°出射角的自吸收、W K与L线（能量份额为工程值）、'
                    '0.25 mm铍窗；衰减系数来自NIST SRD 126模板库。'],
    'filter.select': ['7位滤片转换器为厂家标准配置；各位置的滤片（无、Al 0.25/0.5/1.0 mm、Cu 0.1/0.25 mm、Al 1.0 mm+Cu 0.1 mm）为工程选择。'],
    'stage.move': ['源-探距固定0.450 m、源-物距0.015–0.395 m、光束高度0.400 m、样品夹高度0.160–0.399 m与速度为工程值（厂家未公布）；碰撞保护：样品本体、样品针、样品夹与旋转台外壳（各为绕旋转轴的竖直圆柱）在高度重叠处不得进入源头部/滤片滑块与探测器外壳的禁入区，并低于腔顶；目标位置与路径都检查（滑台20 mm/s与升降10 mm/s同时启动，路径为（源-物距，高度）平面上的两段直线），路径穿过禁入区时拒绝（COLLISION_RISK），须分两步移动；'
                   '300 mm高的样品放得进样品腔（腔顶0.640 m），升降行程加低放大视场可覆盖到距其底部约290 mm。'],
    'door.open': ['门的运动方向（向左滑入前面板后）为照片估计，行程0.40 m、速度0.1 m/s为工程值；X射线开启时门锁住。'],
    'door.close': ['安全光幕被挡时门不能关闭，关闭中被挡则停止并重新打开（工程逻辑，按厂家“安全光幕”功能）。'],
    'scan.configure': ['扫描范围180°(+锥角，Parker加权)或360°；步长0.05–2°；曝光0.02–10 s；帧平均1–16；合并1×1/2×2/4×4；随机移动（探测器每投影随机平移±4模拟列）。'],
    'calibration.flat_field': ['暗场与亮场各20帧（工程值）；校正对应电压、电流、滤片、合并与曝光，任一改变即失效；亮场噪声使固定图案残留（环状伪影来源之一）；'
                               '亮场饱和时亮区透过率在满量程截止；单个合并像素亮场相对误差超过10 %时校正无效（信号不足）；像素增益不均由亮场除去，不单独模拟。'],
    'calibration.geometry': ['钢球体模36投影360°扫描；按精确锥束轨迹u=SDD(a+r sin(β+φ))/(SOD+r cos(β+φ))联合最小二乘求旋转轴横向偏移a；源-物距与源-探距视为已知；'
                             '体模须在所有角度完整位于视场内（否则拒绝）；拟合残差超过2个窗口像素（旋转轴处，至少5 µm）或识别的钢球少于7个时不采用。'],
    'xray.radiograph': ['整幅为175×150模拟像素（每模拟像素16×16原生像素）；局部区域以合并后原生像素成像（≤256×256，须在探测器有效面内）；平场校正后的固定图案残留按窗口像素计。'],
    'scan.start': ['采集窗口=样品本体在所有角度的投影包络（按名义几何、已标定的轴偏移，加6 %及未知轴偏移的余量），最多175×150模拟像素且不细于一个合并后的原生像素；'
                   '投影最多模拟200个（均匀子集），光子数按名义投影数与帧数剂量等效合并；连续旋转近似步进采集；'
                   '能量分24组（强制K边分界）；复合泊松量子噪声、读出噪声、16位饱和、焦点半影与闪烁体模糊；未建模散射、相位衬度、探测器余辉、射线管漂移与热漂移。'],
    'recon.start': ['FDK滤波反投影（Ram-Lak/Shepp-Logan/Hann），180°+锥角扫描用Parker权重；束硬化线性化：按指定材料与扫描能谱的多色衰减曲线把线积分映射为μ(E_eff)·L'
                    '（E_eff=未衰减束的探测能量加权平均能量）；环状伪影校正=减去投影平均列剖面的高频部分；自动后对准=中心平面共轭射线一致性（±200 µm内5 µm网格加抛物线顶点），'
                    '中心平面（光束高度）没有样品数据时不执行并给出警告；手动后对准为给定值，否则用几何标定值；180°+锥角短扫描的Parker权重按本旋转方向的共轭射线（β+π−2γ）在滤波前加权；'
                    '投影数据须仍在缓存中（只保留最近8组投影/透视）；感兴趣体积的中心须在采集窗口可重建的区域内（超出部分给出警告）；重建网格48–128（模拟体素远大于名义体素）；'
                    '工作站重建时间按名义体素×投影数/5e10每秒估计（工程值）。'],
    'data.porosity': ['包络=Otsu阈值实体区逐层与三维填孔后腐蚀1体素；孔阈值默认ISO50（空气与基体中值的中点）；26连通；开孔=接触包络边界；等效直径=(6V/π)^(1/3)；小于约2体素的孔无法分辨。'],
    'data.cracks': ['低衰减连通体主成分分析：厚/长<0.3且宽/长>0.35判为裂纹；面积=π·R1·R2（R=2√λ）、开口=体积/面积、长度=2R1；开口小于体素时部分体积使对比度下降，可能检测不到或开口偏大。'],
    'data.edge_resolution': ['刃边法：逐行找刃边位置并直线拟合，按到刃边距离的0.25像素分箱平均得刃边扩展函数，10–90 %宽度按探测器像素与放大换算到样品平面。'],
    'ledger.read': ['能量流在旋转2π/32、滑台与升降0.25 mm、电压1 kV、滤片位置（按滑块经过的位置）的格点中心计算（每个格内恒定，跨格时步进在格边界切分），电压与电流的斜坡与'
                    '预热按V²·I精确积分，账本与宿主步长无关；去向：靶（向靶内一侧的半球全部、出射半球沿出射路径的自吸收）、铍窗与滤片（有用发射锥内）、空气、样品本体吸收（μen/μ；'
                    '剂量=样品本体吸收÷本体质量）、样品架与台架吸收（样品针、样品管、样品夹、旋转台）、散射、探测器沉积/穿透、屏蔽（出射半球其余部分与锥内探测器以外的射线），残差为舍入误差；'
                    '发射锥（半角20°）内的射线全部计入（样品伸出探测器视野的部分同样受照）：锥用格子铺满（探测器上35×30格、其外约8 mm格，格不跨探测器边缘），'
                    '与样品本体（含样品管）在该格转角下的投影框相交的格沿框边切开，框内按细步距再分（框上约1000条射线，任一方向至少16条），各去向都是正的射线项之和；'
                    '每条射线按穿过的先后吸收：样品台壳体、样品夹与样品针（位于样品下方：向上的射线先经过，向下的射线最后经过）、样品管在管内之前的壁、样品本体、管内之后的壁（从开口端离开的射线没有这一段）。'],
    'records.delete': ['删除记录及其缓存数据；进行中的操作或正被重建的投影记录不能删除；agent只能删除自己或宿主创建的记录；删除后该记录的结果不再可查。'],
    'measurement.result': ['记录中的样品剂量dose_gy取决于样品真实质量与组成，只对评测者可见（agent为null）。'],
}
LIMITATIONS = [
    '选定配置：X4 POSEIDON基本配置（反射式X射线源、7 MP平板探测器、标准旋转台、单探测器支架、固定扫描几何、7位滤片转换器）；透射源、sCMOS、Multi-Vision、Best-Scan-Geometry、'
    '样品转换器、原位试验台与高级扫描模式为不支持的选件。',
    '源-探距、源-物距范围、光束高度、像素间距、闪烁体、增益与噪声、斜坡与预热、门与各轴的速度、滤片组成为工程假设（厂家未公布）；电压、功率、焦点、探测器像素数、样品尺寸、'
    '外形与质量为厂家值。',
    'X射线物理：NIST SRD 126衰减系数（模板库145种物质）、多组能量Beer-Lambert、闪烁体能量沉积与复合泊松噪声；未建模康普顿散射到探测器、相位衬度、射线管漂移、探测器余辉。',
    '模拟网格：探测器175×150、投影≤200、重建网格≤128，名义体素只在记录中给出；分辨率与噪声按剂量等效合并近似。',
    '台架：黄铜样品夹按铜（8.96 g/cm³）、旋转台壳体与转盘按等面积铝圆柱（半径45.1 mm、高50 mm）参与衰减；室温与湿度只作记录，空气密度取NIST表列值。',
    '上电后各轴保持断电时的位置（绝对位置，工程假设；厂家未说明回原点行为）。',
    '没有真实X4 POSEIDON硬件与3DxSUITE软件接口；机器人装样没有接触验证。',
]


def _sampling_text(k):
    if k in ('door.position_m', 'filter.slide_m', 'stage.sod_m', 'stage.chuck_z_m', 'stage.rotation_rad', 'xray.voltage_v', 'xray.current_a', 'xray.power_w',
             'clock.time_s', 'measurement.progress', 'measurement.remaining_s', 'recon.remaining_s', 'xray.warmup_remaining_s'):
        return 'Instantaneous value at the read time: constant-speed motions, voltage / current ramps and operation phases are integrated exactly and cut at every event.'
    return 'Changes when a command is accepted or at an event of the operation (phase ends, record completion); no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or k in _ANALYSIS_RECORD_ARGS or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    if k == 'emergency.stop':
        return '；安全按钮不受另一个agent的操作限制'
    return '；另一个agent的采集操作进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）' if k in _CONTROL_GUARDED else ''


def export_bruker_x4poseidon_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'BrukerX4PoseidonSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.bruker_x4poseidon.BrukerX4PoseidonSession.invoke',
                                              invocation=f'session.invoke(instance_id="x4_poseidon_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['measurement.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='BrukerX4PoseidonRuntime.invoke / BrukerX4PoseidonSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate',
                                     description_zh='设定、目标、状态标志或立即改变的派生量；位置、斜坡与操作进度只随step推进') for s in EFFECTS.get(k, [])],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置、分析或查询；运动由step推进',
                                completion_state_ids=(['measurement.id', 'measurement.progress', 'result.last_record_id'] if k in ACQUISITION
                                                      else ['recon.id', 'result.last_record_id'] if k == 'recon.start' else []),
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=(EXEC_TEXT[k][1] if k in EXEC_TEXT else
                                                    ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果（运动目标），被拒时状态不变'))),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH.get(e, e)), side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'xray_kv_max_v': (XRAY_KV_MAX_V, 'V', '反射源最高100 kV（厂家、调研表）', [_SPEC, _BRO, _WB]),
            'xray_power_max_w': (XRAY_POWER_MAX_W, 'W', '反射源20 W（厂家、调研表）', [_SPEC, _BRO, _WB]),
            'spot_at_4w_m': (SPOT_AT_4W_M, 'm', '反射源焦点<5 µm@4 W（厂家、调研表；取上限）', [_SPEC, _WB]),
            'detector_pixels': (list(DETECTOR_PIXELS), '1', '7 MP平板探测器2800×2400像素（厂家、调研表）', [_SPEC, _WB, _CFG]),
            'sample_max_diameter_m': (SAMPLE_MAX_DIAMETER_M, 'm', '样品直径最大110 mm（厂家、调研表）', [_SPEC, _WB]),
            'sample_max_height_m': (SAMPLE_MAX_HEIGHT_M, 'm', '样品高度最大300 mm（厂家、调研表）', [_SPEC, _WB]),
            'filter_positions': (FILTER_POSITIONS, '1', '自动7位滤片转换器为标准配置（厂家配置器）', [_CFG]),
            'detail_detectability_m': (DETAIL_DETECTABILITY_M, 'm', '反射源细节可检测度低至5 µm（厂家配置器）', [_CFG]),
            'body_dimensions_m': (list(BODY_DIMENSIONS_M), 'm', '外形W 1250×D 604×H 690 mm（厂家规格表、调研表）', [_SPEC, _WB]),
            'mass_kg': (MASS_KG, 'kg', '重量350 kg（厂家、调研表）', [_SPEC, _BRO, _WB]),
            'mains_voltage_v': (list(MAINS_V), 'V', '电源100–240 VAC 50–60 Hz（厂家）', [_SPEC]), 'mains_current_max_a': (MAINS_A_MAX, 'A', '最大3 A（厂家）', [_SPEC])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    for name, (val, unit, meaning) in {
            'xray_kv_min_v': (XRAY_KV_MIN_V, 'V', '管电压下限'), 'xray_current_max_a': (XRAY_CURRENT_MAX_A, 'A', '管电流上限（另受20 W限制）'),
            'kv_ramp_v_s': (KV_RAMP_V_S, 'V/s', '电压斜坡'), 'current_ramp_a_s': (CURRENT_RAMP_A_S, 'A/s', '电流斜坡'), 'warmup_s': (WARMUP_S, 's', '上电后首次开启的预热程序'),
            'spot_growth_exp': (SPOT_GROWTH_EXP, '1', '焦点随功率增长的指数（4 W以上）'), 'takeoff_rad': (TAKEOFF_RAD, 'rad', '反射靶出射角'),
            'be_window_m': (BE_WINDOW_M, 'm', '铍窗厚度'), 'kramers_k_per_v': (KRAMERS_K_PER_V, '1/V', 'Kramers效率常数（η=K·Z·V；文献值）'),
            'k_line_fraction_100kv': (K_LINE_FRACTION_100KV, '1', '100 kV时W K线能量份额'), 'l_line_fraction_50kv': (L_LINE_FRACTION_50KV, '1', '50 kV时W L线能量份额'),
            'cone_half_angle_rad': (CONE_HALF_ANGLE_RAD, 'rad', '源发射锥半角（账本中屏蔽份额）'), 'sdd_m': (SDD_M, 'm', '源-探距（固定扫描几何）'),
            'sod_range_m': (list(SOD_RANGE_M), 'm', '源-物距范围'), 'chuck_z_range_m': (list(CHUCK_Z_RANGE_M), 'm', '样品夹高度范围'),
            'source_xyz_m': (list(SOURCE_XYZ_M), 'm', '焦点在资产坐标中的位置（光束沿+X）'), 'filter_clear_m': (FILTER_CLEAR_M, 'm', '滤片转换器前表面到焦点距离'),
            'detector_cover_m': (DETECTOR_COVER_M, 'm', '探测器盖板到有效面距离'), 'chamber_ceiling_m': (CHAMBER_CEILING_M, 'm', '腔顶高度'),
            'source_keepout_dz_m': (list(SOURCE_KEEPOUT_DZ), 'm', '源头部与滤片滑块禁入区相对光束的高度范围'),
            'detector_keepout_dz_m': (list(DETECTOR_KEEPOUT_DZ), 'm', '探测器外壳禁入区相对光束的高度范围'),
            'chuck_r_m': (CHUCK_R_M, 'm', '样品夹半径'), 'stage_r_m': (STAGE_R_M, 'm', '旋转台壳体外接圆半径（保守包络）'),
            'pixel_pitch_m': (PIXEL_PITCH_M, 'm', '平板像素间距'), 'csi_thickness_m': (CSI_THICKNESS_M, 'm', 'CsI:Tl闪烁体厚度'),
            'adu_per_j': (ADU_PER_KEV / KEV, '1/J', '闪烁体沉积能量到ADU的转换增益（0.8 ADU/keV）'), 'read_noise_adu': (READ_NOISE_ADU, '1', '读出噪声（ADU）'),
            'dark_offset_adu': (DARK_OFFSET_ADU, '1', '暗电平（ADU）'), 'dark_current_adu_s': (DARK_CURRENT_ADU_S, '1/s', '暗电流（ADU/s）'),
            'flat_max_sigma': (FLAT_MAX_SIGMA, '1', '可用亮场的合并像素相对误差上限'), 'detector_psf_px': (DETECTOR_PSF_PX, '1', '闪烁体光扩散σ（原生像素）'),
            'step_overhead_s': (STEP_OVERHEAD_S, 's', '每投影旋转与稳定时间'), 'frame_overhead_s': (FRAME_OVERHEAD_S, 's', '每帧读出'),
            'rotation_speed_rad_s': (ROTATION_SPEED_RAD_S, 'rad/s', '旋转台定位速度'), 'carriage_speed_m_s': (CARRIAGE_SPEED_M_S, 'm/s', '样品滑台速度'),
            'lift_speed_m_s': (LIFT_SPEED_M_S, 'm/s', '升降速度'), 'door_travel_m': (DOOR_TRAVEL_M, 'm', '门行程'), 'door_speed_m_s': (DOOR_SPEED_M_S, 'm/s', '门速度'),
            'filter_s_per_step': (FILTER_S_PER_STEP, 's', '滤片每档时间'), 'boot_s': (BOOT_S, 's', '启动与回原点时间'),
            'flat_field_frames': (FLAT_FIELD_FRAMES, '1', '暗场/亮场帧数'), 'axis_offset_sd_m': (AXIS_OFFSET_SD_M, 'm', '隐藏旋转轴横向偏移（1σ）'),
            'recon_rate_voxel_proj_s': (RECON_RATE_VOXEL_PROJ_S, '1/s', '工作站重建吞吐（体素×投影/秒）'), 'sim_detector_px': (list(SIM_DETECTOR), '1', '模拟探测器网格'),
            'sim_max_projections': (SIM_MAX_PROJECTIONS, '1', '模拟投影数上限'), 'energy_groups': (ENERGY_GROUPS, '1', '能量分组数')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['source_housing', 'source_head', 'source_window', 'filter_changer', 'filter_slide', 'sample_carriage', 'sample_lift', 'sample_turntable',
                       'sample_chuck', 'detector_housing', 'detector_window', 'door_glass', 'button_emergency_stop', 'enclosure_base', 'workstation_monitor'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='X4 POSEIDON台式显微CT：钨反射源谱、滤片、锥束几何与样品旋转、平板探测器响应与噪声、暗场/亮场与几何标定、FDK重建、孔隙率与裂纹分析、安全联锁与能量账',
        equations=['Kramers: I(E) dE ~ Z i (E0 - E) dE, eta = K Z V (K = 1.1e-9 /V); self-absorption exp(-mu_W(E) R_KO(E0) / (3 sin(takeoff)))',
                   'beam: N(E) = N0(E) exp(-mu_Be t_Be - sum_f mu_f t_f - mu_air SDD / cos(alpha)); omega_pixel = A cos^3(alpha) / SDD^2',
                   'transmission: T_g = exp(-sum_m mu_m,g L_m) (energy groups, mu weighted by the detected energy); L_m exact chord lengths of the phantom',
                   'detector: E_dep(E) = E (1 - exp(-mu_CsI t)) mu_en/mu; S = G sum_g n_g T_g <E_dep>_g; Var = G^2 sum_g n_g T_g <p eps^2>_g + sigma_read^2 n_reads',
                   'flat field: t = (S - D) / (F - D); q = -ln t; magnification M = SDD/SOD; voxel = pitch x binning / M; penumbra sigma = spot/2.355 (M - 1)',
                   'FDK: f(x) = 1/2 sum_beta (SOD/U)^2 [ (SOD/sqrt(SOD^2+u^2+v^2) q) * h_ramp ](u(x), v(x)) d_beta, U = SOD + x cos b - y sin b; Parker w for pi + 2 gamma_m',
                   'Parker (short scan pi + 2 gamma_m, this stage: conjugate (b, g) ~ (b + pi - 2 g, -g)): w(b, g) with g -> -g applied to the projections before the ramp filter',
                   'beam hardening: q -> q + s (mu_m(E_eff) L(q) - q), L(q) inverse of q_poly(L) = -ln sum_g w_g exp(-mu_m,g L), E_eff = detected-energy mean of the unattenuated beam; porosity = N_pore / N_envelope (ISO 50 threshold)',
                   'geometry calibration: u_i(b) = SDD (a + r_i sin(b + phi_i)) / (SOD + r_i cos(b + phi_i)), joint least squares over the tracked steel balls for the axis offset a',
                   'auto alignment (central row): a* = argmin_a mean min((p(b, u) - p(b - pi + 2 (alpha - t), SDD tan(2 alpha - t)))^2, c^2), alpha = atan(a / SOD), t = atan(u / SDD)',
                   'energy: E_emitted = 4 pi E_un = target (2 pi E_un + 2 pi (E_un - E_self)) + window + filter (cone) + air + sample body absorbed (mu_en/mu) + holder / stage absorbed + scattered + detector deposited + passed + shielding',
                   'ledger per sub-step inside one rate cell: E_sink = base_sink(cell) int V^2 I dt / (e V_c^2), E_electrical = int V I dt (V, I linear in the sub-step); dose = E_sample_body / m_body'],
        parameters=params, source_ids=[_SIM, _LIBS, _NIST, _PUB, _SPEC, _BRO, _CFG, _PHYS, _WB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_x4poseidon_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'x4_poseidon_ct': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'x4_poseidon_ct': mechanism})
