"""CHEM-161 Rigaku Oxford Diffraction XtaLAB Synergy-R with the HyPix-6000HE detector (single-crystal X-ray diffractometer: PhotonJet-R
microfocus rotating-anode source on the MicroMax-007 HF generator with Cu or Mo target, motorised variable divergence slit 1-10 mrad, 4-circle
kappa goniometer (alpha 50 deg) with the telescopic 2theta arm, HyPix-6000HE hybrid photon counting detector (100 um pixels, 77.5 x 80.3 mm,
100 Hz, two 16-bit counters), radiation enclosure with 2 hinged doors and the electromechanical safety system, Oxford Cryostream 1000 low
temperature device, CrysAlisPro-like processing): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * system: control electronics (power button, boot), the safety circuit (doors interlocked with encoded magnets, Doors OK re-arms the circuit
    after the doors were closed, latching emergency stop, the X-ray key switch; with a door open the goniometer moves only while both Motion
    Enable buttons are held; opening a door during a collection enters the security mode: the shutter closes and the collection stops);
  * X-ray generator: vacuum (turbo pump; pump-down after venting), anode spin-up, high-voltage / emission ramps inside 20-60 kV, 10-30 mA,
    1.2 kW, standby, trips on key, emergency stop, cooling water, vacuum and filament faults; filament and anode hours; the shutter;
  * beam: characteristic Cu or Mo K-alpha1 / K-alpha2 lines (NIST energies, DABAX weights), flux ~ I (V - V_K)^1.6 (Green-Cosslett) times the
    divergence acceptance, Gaussian beam at the crystal, air attenuation (XCOM of N, O, Ar), the beam stop and its rod;
  * crystal (crystal_structure_library.json, COD structures): expanded unit cell, structure factors with the Waasmaier-Kirfel f0, Henke f' and
    f'' and isotropic displacement parameters scaled with the temperature, thermal expansion, orientation and centring offset on the goniometer
    head, mosaicity, block shape with numerical absorption (XCOM mu / rho) and illumination by the Gaussian beam (27-point Gauss-Legendre
    quadrature), radiation decay with the dose, twinned / split / polycrystalline / amorphous samples, ice from the humid air on a cold crystal;
  * diffraction (rotation method, shutterless omega scans): Ewald crossings of every reflection, Lorentz 1 / |e . (s0 x s1)|, polarisation,
    rocking width from the mosaic spread and the divergence, spot size from the illuminated volume, divergence and mosaic spread, K-alpha
    doublet splitting, integrated counts I0 r_e^2 lambda^3 V |F|^2 L P A / (V_cell^2 omega_dot), backgrounds (air scattering, cryo-oil halo,
    Compton scattering of the crystal, K fluorescence of the elements whose K edge lies below the source energy with the Bambynek yield, ice and
    powder rings, amorphous halo), detector quantum efficiency (engineering 0.3 mm Si), the lower energy threshold (fluorescence suppression and
    charge sharing), paralysable pixel dead time, Poisson counting without read-out noise;
  * goniometer: omega, kappa, phi, 2theta arm and detector distance at the documented speeds; a collision model of the goniometer head, phi
    housing, kappa block and motor, omega bracket, detector and carriage against the collimator, source, beam stop, Cryostream, camera and the
    support post (moves and scans that would collide are refused, as the control software does);
  * Cryostream: gas temperature 80-400 K ramped at up to 0.1 K/s = 360 K/h (engineering), the crystal following the gas;
  * processing (CrysAlisPro-like, working only on the recorded data): peak hunting, FFT indexing (Steller-Bolotovsky-Rossmann), UB least
    squares, Niggli reduction, lattice symmetry, integration, scaling, Laue-class test and merging (R_int), space-group choice from the
    systematic absences of candidate groups, "What is this?" cell search in the structure library and refinement of the matched model (scale
    and overall B: R1, wR2, GooF), strategy calculation for a target resolution / completeness / redundancy;
  * fault injection (evaluator): chiller failure, vacuum loss, filament failure, empty nitrogen supply, icing;
  * ledgers: electrical energy, X-ray on-time, filament and anode hours, frames, exposure, crystal dose, door openings, emergency stops.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np

ASSET_TYPE_ID = 'instrument.rigaku_xtalabsynergy'
PROTOCOL_VERSION = '1.0.0'
CATALOG_ID = 'CHEM-161'
SLUG = 'rigaku_xtalabsynergy'
INSTANCE_ID = 'rigaku_xtalabsynergy_01'
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t3_analytical' / SLUG
CRYSTAL_LIB_PATH = ASSET_DIR / 'crystal_structure_library.json'
SCATTER_LIB_PATH = ASSET_DIR / 'xray_scattering_library.json'
R_E = 2.8179403262e-15                               # classical electron radius (m)
N_A = 6.02214076e23
HC_EV_M = 1.23984198e-6
EV_J = 1.602176634e-19
PI = math.pi
DEG = PI / 180.0


class ActionRejected(ValueError):
    """Precondition failed; the state was not changed."""


# ---------------------------------------------------------------- documented values (product page, brochure, Rigaku Journal 36(1), user instructions)
BODY_DIMENSIONS_M = (1.300, 0.850, 1.875)             # 1300 (W) x 1875 (H) x 850 (D) mm, 600 kg (core unit)
MASS_KG = 600.0
MAINS_V = (200.0, 230.0)                              # 1 phase, 200-230 V, 20 A
MAINS_A = 20.0
KV_RANGE_V = (20e3, 60e3)                             # 20-60 kV in 1 kV steps
KV_STEP_V = 1e3
MA_RANGE_A = (10e-3, 30e-3)                           # 10-30 mA in 1 mA steps
MA_STEP_A = 1e-3
POWER_MAX_W = 1200.0                                  # MicroMax-007 HF 1.2 kW
FOCUS_M = {'Cu': (70e-6, 70e-6), 'Mo': (150e-6, 100e-6)}  # effective focus at 6 deg take-off (MicroMax-007 HF): Cu 0.07 mm circle, Mo 0.15 x 0.1 mm ellipse
ANODE_RPM = 9000.0
DIVERGENCE_RANGE_RAD = (1e-3, 10e-3)                  # continuously variable 1-10 mrad
PIXEL_M = 100e-6
ACTIVE_M = (0.0775, 0.0803)                           # HyPix-6000HE active area 77.5 x 80.3 mm
NX, NY = 775, 803
FRAME_RATE_MAX_HZ = 100.0
COUNTER_BITS = 16
HDR_BITS = 31
DETECTOR_DIMENSIONS_M = (0.137, 0.143, 0.216)
DETECTOR_MASS_KG = 3.8
KAPPA_ALPHA_RAD = 50.0 * DEG
AXIS_SPEED_RAD_S = dict(omega=10.0 * DEG, kappa=10.0 * DEG, phi=20.0 * DEG, theta=10.0 * DEG)
SCAN_SPEED_MAX_RAD_S = 10.0 * DEG
DIST_MIN_M = 0.032                                    # minimum crystal-to-detector distance (user instructions of the platform)
CRYO_RANGE_K = (80.0, 400.0)                          # Cryostream 1000: 80-400 K, stability 0.1 K
CRYO_STABILITY_K = 0.1
FILAMENT_INTERVAL_S = 90 * 86400.0                    # filament replaced by the user about every three months
ANODE_INTERVAL_S = 365 * 86400.0                      # anode refurbished annually
HIGH_LOW_EXPOSURE_RATIO = 4.0                         # high-angle frames 4x longer than low-angle frames
COMMON_SCAN_WIDTH_RAD = 0.3 * DEG
PRE_EXPERIMENT_TYPICAL_S = (240.0, 900.0)

# ---------------------------------------------------------------- engineering assumptions (not published)
DIST_MAX_M = 0.150
DIST_SPEED_M_S = 0.010
THETA_RANGE_RAD = (-100.0 * DEG, 100.0 * DEG)
OMEGA_RANGE_RAD = (-PI, PI)
KAPPA_RANGE_RAD = (-PI, PI)
PHI_RANGE_RAD = (-2 * PI, 2 * PI)
BOOT_S = 20.0
PUMPDOWN_S = 300.0                                    # after venting (filament change, vacuum fault)
TURBO_S = 30.0                                        # turbo pump run-up when the vacuum was held
SPINUP_S = 30.0                                       # anode spin-up and filament heating before the high voltage
HV_RAMP_V_S = 2000.0
MA_RAMP_A_S = 2e-3
STANDBY = (20e3, 10e-3)
FLUX_REF = {'Cu': dict(flux=2.5e9, kv=40e3, ma=30e-3), 'Mo': dict(flux=8.0e8, kv=50e3, ma=24e-3)}   # photons/s at 10 mrad (K-alpha doublet)
FLUX_EXPONENT = 1.6
BEAM_FWHM_M = (50e-6, 6e-6)                           # FWHM at the crystal = 50 um + 6 um per mrad of divergence
AIR_PATH_IN_M = 0.20                                  # source window to the crystal in air
AIR_SCATTER_PATH_M = 0.032                            # air seen by the detector: collimator exit to the beam stop
AIR_DENSITY_KG_M3 = 1.20
AIR_MASS_FRACTIONS = {'N': 0.7553, 'O': 0.2318, 'Ar': 0.0128}     # dry air (NIST), carbon neglected
OIL_THICKNESS_M = 100e-6                              # cryo-oil and loop in the beam
OIL_ELECTRONS_M3 = 3.09e29                            # paraffin oil 0.9 g/cm3
OIL_HALO = dict(q0=1.0 / 4.6e-10, width=0.05e10, amplitude=1.5)   # amorphous halo of the oil (q = 1/d)
SENSOR_SI_M = 300e-6                                  # sensor thickness not published: engineering 0.3 mm Si
SI_DENSITY = 2330.0
DEAD_TIME_S = 120e-9                                  # paralysable pixel dead time (count-rate capability not published)
THRESHOLD_RANGE = (0.3, 0.9)                          # lower threshold as a fraction of the K-alpha energy
THRESHOLD_DEFAULT = 0.5
CHARGE_SHARE_LOSS = 0.6                               # primary efficiency 1 - 0.6 (thr / E - 0.5) above half the energy
ELECTRONICS_W = 600.0
GENERATOR_EFFICIENCY = 0.75
CRYO_RAMP_MAX_K_S = 0.1                               # 360 K/h
CRYO_TAU_S = 5.0                                      # crystal in the gas stream
CRYO_OFFSET_K = 1.0                                   # crystal a little warmer than the gas
WARM_TAU_S = 30.0                                     # crystal back to room temperature without the stream
ICE_RATE_M3_S = 2e-16                                 # ice grows on a cold crystal while humid air enters (door open)
ICE_CELL_M = (4.497e-10, 7.322e-10)                   # ice Ih near 100 K (hexagonal P63/mmc, O at 4f z = 0.0625)
MOUNT_OFFSET_MAX_M = 0.35e-3                          # loop placed on the magnet: crystal up to 0.35 mm off the centre
CENTER_RESIDUAL_M = 8e-6
CAMERA_QUANT_M = 5e-6
CAMERA_SIGMA_M = 3e-6
CENTER_S = 90.0
MOUNT_S = 60.0
FILAMENT_WORK_S = 1800.0
SHUTTER_S = 0.05
MOUNT_POSE = dict(omega=0.0, kappa=0.0, theta=0.0, distance=0.100)
DECAY_HALF_DOSE_GY = dict(warm=(295.0, 10e6), cold=(100.0, 200e6))   # molecular crystals; ionic crystals do not decay
EXPANSION_PER_K = dict(inorganic=1e-5, molecular=5e-5)
U_ZERO_FRACTION = 0.25
MOSAIC_RANGE_RAD = (1e-4, 0.05)
COLLISION_MARGIN_M = 0.003
KEY_STOP_TOL_RAD = 1e-6                               # float32 joint value of the X-ray key at its 90 deg stop
FAULTS = ['chiller_failure', 'vacuum_loss', 'filament_failure', 'nitrogen_empty', 'icing']
INSTANT_FAULTS = set()

# ---------------------------------------------------------------- simulation limits
MAX_REFLECTIONS = 400_000
D_MIN_SIM_M = 0.60e-10
MAX_RUNS = 40
MAX_FRAMES_PER_RUN = 3600
MAX_TOTAL_FRAMES = 20_000
MAX_OBSERVATIONS = 800_000
MAX_RECORDS = 100
MAX_SAMPLES = 12
MAX_EVENTS = 500
MAX_STEP_S = 7 * 24 * 3600.0
RESULT_VALUES_MAX = 65536
LARGE_RESPONSE_BYTES = 65536
LARGE_RESPONSE_KEEP = 16
PEAKS_MAX = 5000


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


# ---------------------------------------------------------------- libraries
@functools.lru_cache(maxsize=1)
def crystal_lib():
    lib = json.loads(CRYSTAL_LIB_PATH.read_text())
    return {s['substance_id']: s for s in lib['substances']}


@functools.lru_cache(maxsize=1)
def crystal_lib_meta():
    lib = json.loads(CRYSTAL_LIB_PATH.read_text())
    return {k: lib[k] for k in ('library', 'version', 'sources', 'counts')}


@functools.lru_cache(maxsize=1)
def scatter_lib():
    return json.loads(SCATTER_LIB_PATH.read_text())


def library_sha256():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (CRYSTAL_LIB_PATH, SCATTER_LIB_PATH)}


def _rng(*keys):
    return np.random.default_rng([int(k) & 0xFFFFFFFF for k in keys])


def line_data(target):
    L = scatter_lib()['lines'][target]
    w1, w2 = L['Ka1']['weight'], L['Ka2']['weight']
    return dict(lam1=L['Ka1']['wavelength_m'], lam2=L['Ka2']['wavelength_m'], w1=w1 / (w1 + w2), w2=w2 / (w1 + w2), e1=L['Ka1']['energy_ev'], e2=L['Ka2']['energy_ev'],
                key=f'{target}_Ka1')


def element(el):
    return scatter_lib()['elements'][el]


def f0(el, s_a):
    """Waasmaier-Kirfel non-dispersive form factor; s_a = sin(theta)/lambda in 1/Angstrom."""
    p = element(el)['f0_wk']
    s2 = np.asarray(s_a, float) ** 2
    return sum(a * np.exp(-b * s2) for a, b in zip(p['a'], p['b'])) + p['c']


def mu_rho(el, key):
    return element(el)['at_lines'][key]['mu_rho_m2_kg']


def air_mu(key):
    return AIR_DENSITY_KG_M3 * sum(w * mu_rho(el, key) for el, w in AIR_MASS_FRACTIONS.items())


# ---------------------------------------------------------------- crystallography
def cell_matrix(a, b, c, al, be, ga):
    """Direct basis (columns a, b, c) in the crystal Cartesian frame: a along x, b in the xy plane."""
    ca, cb, cg, sg = math.cos(al), math.cos(be), math.cos(ga), math.sin(ga)
    cy = (ca - cb * cg) / sg
    cz = math.sqrt(max(1.0 - cb * cb - cy * cy, 1e-12))
    return np.array([[a, b * cg, c * cb], [0.0, b * sg, c * cy], [0.0, 0.0, c * cz]])


def reciprocal(A):
    return np.linalg.inv(A).T


def cell_params(A):
    a, b, c = (np.linalg.norm(A[:, i]) for i in range(3))
    al = math.acos(np.clip(A[:, 1] @ A[:, 2] / (b * c), -1, 1))
    be = math.acos(np.clip(A[:, 0] @ A[:, 2] / (a * c), -1, 1))
    ga = math.acos(np.clip(A[:, 0] @ A[:, 1] / (a * b), -1, 1))
    return [a, b, c, al, be, ga]


def rot(axis, ang):
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K


def rot_z(ang):
    c, s = np.cos(ang), np.sin(ang)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def rot_z_batch(ang):
    ang = np.asarray(ang, float)
    c, s = np.cos(ang), np.sin(ang)
    R = np.zeros(ang.shape + (3, 3))
    R[..., 0, 0], R[..., 0, 1], R[..., 1, 0], R[..., 1, 1], R[..., 2, 2] = c, -s, s, c, 1.0
    return R


KAPPA_AXIS = np.array([-math.sin(KAPPA_ALPHA_RAD), 0.0, -math.cos(KAPPA_ALPHA_RAD)])   # at omega = 0 (asset frame; beam along +X)


def gonio_matrix(omega, kappa, phi):
    """Crystal rotation of the goniometer: R = Omega(omega) K(kappa) Phi(phi) (omega and phi about +Z, kappa about the tilted kappa axis)."""
    return rot_z(omega) @ rot(KAPPA_AXIS, kappa) @ rot_z(phi)


def random_rotation(rng):
    q = rng.standard_normal(4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _symops(entry):
    return [(np.array(R, float), np.array(t, float)) for R, t in entry['symops']]


def expand_atoms(entry):
    """Unit-cell atoms: each asymmetric-unit atom expanded by the symmetry operations (special positions merged within its orbit); fully occupied
    atoms of the same element that coincide with an atom already placed (atoms listed twice in the CIF) are dropped, as in the library builder."""
    ops = _symops(entry)
    out = []
    full = {}
    for at in entry['atoms']:
        x0 = np.array(at['xyz'], float)
        pos = []
        for R, t in ops:
            x = (R @ x0 + t) % 1.0
            if not any(np.all(np.abs(((x - p) + 0.5) % 1.0 - 0.5) < 1e-3) for p in pos):
                pos.append(x)
        occ = float(at['occupancy'])
        for x in pos:
            if occ >= 0.999:
                placed = full.setdefault(at['element'], [])
                if any(np.all(np.abs(((x - p) + 0.5) % 1.0 - 0.5) < 2e-3) for p in placed):
                    continue
                placed.append(x)
            out.append((at['element'], x, occ, float(at['u_iso_a2'])))
    return out


def centring_vectors(entry):
    return [t % 1.0 for R, t in _symops(entry) if np.allclose(R, np.eye(3)) and np.any(np.abs(((t % 1.0) + 0.5) % 1.0 - 0.5) > 1e-6)]


def is_centrosymmetric(entry):
    return any(np.allclose(R, -np.eye(3)) for R, t in _symops(entry))


def category_class(entry):
    return 'inorganic' if entry['category'] == 'inorganic' else 'molecular'


def formula_content(entry):
    """Atoms per unit cell from the chemical formula times Z (the refined model may lack the hydrogen atoms)."""
    import re
    out = {}
    for tok in entry['formula'].split():
        m = re.fullmatch(r'([A-Z][a-z]?)(\d*(?:\.\d+)?)', tok)
        out[m.group(1)] = out.get(m.group(1), 0.0) + (float(m.group(2)) if m.group(2) else 1.0) * float(entry['Z'])
    return out


@functools.lru_cache(maxsize=64)
def substance_model(sid):
    e = crystal_lib()[sid]
    atoms = expand_atoms(e)
    els = sorted({a[0] for a in atoms})
    idx = np.array([els.index(a[0]) for a in atoms])
    frac = np.array([a[1] for a in atoms])
    occ = np.array([a[2] for a in atoms])
    u = np.array([a[3] for a in atoms])
    cell = [e['cell']['a_m'], e['cell']['b_m'], e['cell']['c_m'], e['cell']['alpha_rad'], e['cell']['beta_rad'], e['cell']['gamma_rad']]
    content = formula_content(e)                                           # the crystal contains its hydrogen even when the model does not
    mass_cell = sum(n * element(el)['atomic_weight'] for el, n in content.items()) * 1e-3 / N_A
    electrons = sum(n * element(el)['Z'] for el, n in content.items())
    return dict(sid=sid, entry=e, els=els, idx=idx, frac=frac, occ=occ, u=u, cell=cell, A=cell_matrix(*cell), content=content, mass_cell=mass_cell,
                electrons_cell=electrons, centrosymmetric=is_centrosymmetric(e), klass=category_class(e), t_lib=float(e['temperature_k'] or 295.0))


def model_at(sid, temperature_k):
    """Cell and displacement parameters of the substance at the crystal temperature (engineering thermal expansion and U(T))."""
    m = substance_model(sid)
    dt = temperature_k - m['t_lib']
    scale = 1.0 + EXPANSION_PER_K[m['klass']] * dt
    A = m['A'] * scale
    u = m['u'] * (U_ZERO_FRACTION + (1.0 - U_ZERO_FRACTION) * temperature_k / m['t_lib'])
    return A, u


def mu_linear(sid, key, A):
    m = substance_model(sid)
    V = abs(np.linalg.det(A))
    rho = m['mass_cell'] / V
    mass = {el: n * element(el)['atomic_weight'] for el, n in m['content'].items()}
    tot = sum(mass.values())
    return rho * sum(mass[el] / tot * mu_rho(el, key) for el in mass), rho


def hkl_sphere(A, d_min, limit=MAX_REFLECTIONS):
    """All reflections with d >= d_min (d_min raised when the count would exceed the simulation limit)."""
    V = abs(np.linalg.det(A))
    n_est = 4.18879 * V / d_min ** 3
    if n_est > limit:
        d_min = (4.18879 * V / limit) ** (1.0 / 3.0)
    B = reciprocal(A)
    hm = [int(np.linalg.norm(A[:, i]) / d_min) + 1 for i in range(3)]
    g = np.stack(np.meshgrid(*[np.arange(-h, h + 1) for h in hm], indexing='ij'), -1).reshape(-1, 3)
    r = g @ B.T
    q = np.einsum('ij,ij->i', r, r)
    keep = (q <= 1.0 / d_min ** 2) & (q > 0)
    return g[keep], d_min


def structure_factors(sid, H, A, u, key, inverted=False):
    """F(h) for the expanded cell with f0 + f' + i f'' (at the K-alpha1 line) and isotropic displacement parameters U (Angstrom^2)."""
    m = substance_model(sid)
    B = reciprocal(A)
    s_a = 0.5 * np.linalg.norm(H @ B.T, axis=1) * 1e-10
    F = np.zeros(len(H), complex)
    frac = -m['frac'] if inverted else m['frac']
    for k, el in enumerate(m['els']):
        sel = m['idx'] == k
        p = element(el)['at_lines'][key]
        fe = f0(el, s_a) + p['f_prime'] + 1j * p['f_double_prime']
        wt = m['occ'][sel]
        for c0 in range(0, len(H), 20000):
            hc = H[c0:c0 + 20000]
            dw = np.exp(-8 * PI ** 2 * np.outer(s_a[c0:c0 + 20000] ** 2, u[sel]))
            ph = np.exp(2j * PI * (hc @ frac[sel].T))
            F[c0:c0 + 20000] += fe[c0:c0 + 20000] * (ph * dw * wt).sum(1)
    return F


@functools.lru_cache(maxsize=4)
def ice_rings(key):
    """Powder rings of ice Ih (hexagonal a 4.497, c 7.322 Angstrom; P63/mmc with O at 4f (1/3, 2/3, 0.0625)): d, multiplicity x |F|^2."""
    a, c = ICE_CELL_M
    A = cell_matrix(a, a, c, PI / 2, PI / 2, 2 * PI / 3)
    B = reciprocal(A)
    zO = 0.0625
    O = np.array([[1 / 3, 2 / 3, zO], [2 / 3, 1 / 3, zO + 0.5], [2 / 3, 1 / 3, -zO], [1 / 3, 2 / 3, 0.5 - zO]])
    g = np.stack(np.meshgrid(*[np.arange(-4, 5)] * 2, np.arange(-6, 7), indexing='ij'), -1).reshape(-1, 3)
    r = np.linalg.norm(g @ B.T, axis=1)
    keep = (r > 0) & (r < 1 / 1.3e-10)
    g, r = g[keep], r[keep]
    s_a = 0.5 * r * 1e-10
    p = element('O')['at_lines'][key]
    fe = (f0('O', s_a) + p['f_prime'] + 1j * p['f_double_prime']) * np.exp(-8 * PI ** 2 * 0.02 * s_a ** 2)
    F = fe * np.exp(2j * PI * g @ O.T).sum(1)
    d = 1.0 / r
    rings = {}
    for di, f2 in zip(np.round(d * 1e13).astype(int), np.abs(F) ** 2):
        rings[di] = rings.get(di, 0.0) + float(f2)
    vol = abs(np.linalg.det(A))
    return [(k * 1e-13, v) for k, v in sorted(rings.items(), reverse=True) if v > 1e-3], vol


GL3 = (np.array([-math.sqrt(3 / 5), 0.0, math.sqrt(3 / 5)]), np.array([5 / 9, 8 / 9, 5 / 9]) / 2.0)
_P = np.array(list(itertools.product(range(3), repeat=3)))
QUAD_X = np.stack([GL3[0][_P[:, 0]], GL3[0][_P[:, 1]], GL3[0][_P[:, 2]]], 1)            # 27 Gauss-Legendre nodes in [-1, 1]^3
QUAD_W = GL3[1][_P[:, 0]] * GL3[1][_P[:, 1]] * GL3[1][_P[:, 2]]                           # weights (sum 1)


def box_exit(p, d, half):
    """Distance from interior points p (N, 3) along unit directions d (M, 3) to the surface of the box |x_i| <= half_i: (M, N)."""
    with np.errstate(divide='ignore', invalid='ignore'):
        t = np.where(np.abs(d[:, None, :]) > 1e-12, (np.sign(d[:, None, :]) * half - p[None, :, :]) / d[:, None, :], np.inf)
    return np.min(t, axis=2)


def bambynek_omega_k(z):
    s_ = 0.0370 + 0.03112 * z + 5.44e-5 * z ** 2 - 1.25e-6 * z ** 3
    return s_ ** 4 / (1.0 + s_ ** 4)


# ---------------------------------------------------------------- instrument geometry (asset frame, relative to the goniometer centre; mirrors interface_definition.py)
GONIO_CENTRE_M = (-0.090, 0.020, 1.300)
DET_FACE_OFFSET_M = 0.008                             # sensor plane 8 mm behind the nose front (engineering)
DET_NOSE = (0.050, 0.0625, 0.0655)                    # depth, half width, half height of the nose
DET_BODY = (0.166, 0.0685, 0.0715)
COLLIMATOR_TIP_GAP_M = 0.020
BEAM_STOP = dict(x=0.012, r=0.0015, length=0.003, rod_r=0.0006, rod_top=0.050)
CRYO_AXIS = np.array([-math.sin(30 * DEG), 0.0, math.cos(30 * DEG)])
CRYO_NOZZLE_GAP_M = 0.012
CAMERA_AXIS = np.array([0.28, -0.12, 0.95]) / np.linalg.norm([0.28, -0.12, 0.95])
_P1 = np.array([math.cos(KAPPA_ALPHA_RAD), 0.0, -math.sin(KAPPA_ALPHA_RAD)])
_RK = np.column_stack([_P1, [0.0, -1.0, 0.0], KAPPA_AXIS])
KAPPA_BLOCK_C = 0.140 * KAPPA_AXIS + 0.030 * _P1


def _frame_along(u):
    u = np.asarray(u, float) / np.linalg.norm(u)
    a = np.array([1.0, 0, 0]) if abs(u[0]) < 0.9 else np.array([0, 1.0, 0])
    x = np.cross(a, u); x /= np.linalg.norm(x)
    return np.column_stack([x, np.cross(u, x), u])


FIXED_CAPSULES = {   # name: (p0, p1, radius) relative to the goniometer centre
    'collimator_tip': ((-0.020, 0, 0), (-0.093, 0, 0), 0.0075), 'collimator_tube': ((-0.093, 0, 0), (-0.183, 0, 0), 0.011),
    'beam_stop': ((0.0105, 0, 0), (0.0135, 0, 0), 0.0015), 'beam_stop_rod': ((0.012, 0, 0.0015), (0.012, 0, 0.050), 0.0006),
    'beam_stop_rod_bend': ((0.012, 0, 0.050), (0.012, 0.030, 0.135), 0.0006),
    'cryostream_nozzle': (tuple(0.012 * CRYO_AXIS), tuple(0.110 * CRYO_AXIS), 0.0055), 'cryostream_collar': (tuple(0.080 * CRYO_AXIS), tuple(0.110 * CRYO_AXIS), 0.0095),
    'cryostream_head': (tuple(0.205 * CRYO_AXIS), tuple(0.450 * CRYO_AXIS), 0.055),
    'camera_objective': (tuple(0.090 * CAMERA_AXIS), tuple(0.150 * CAMERA_AXIS), 0.010), 'camera_body': (tuple(0.150 * CAMERA_AXIS), tuple(0.210 * CAMERA_AXIS), 0.016),
    'source_tower': ((-0.376, 0.0, -0.250), (-0.376, 0.0, -0.140), 0.066),
}
FIXED_BOXES = {      # name: (centre, axes (columns), half sizes)
    'collimator_block': ((-0.214, 0, 0), np.eye(3), (0.031, 0.030, 0.035)), 'source_optics': ((-0.3425, 0, -0.0335), np.eye(3), (0.0975, 0.075, 0.1045)),
    'cryostream_clamp': (tuple(0.150 * CRYO_AXIS), _frame_along(CRYO_AXIS), (0.025, 0.025, 0.040)),
    'camera_head': (tuple(0.235 * CAMERA_AXIS), _frame_along(CAMERA_AXIS), (0.021, 0.020, 0.025)),
    'support_post': ((-0.305, 0.270, -0.035), np.eye(3), (0.055, 0.050, 0.215)), 'support_bar': ((-0.135, 0.0775, 0.1625), np.eye(3), (0.225, 0.0425, 0.0175)),
    'support_link': ((-0.305, 0.170, 0.1625), np.eye(3), (0.055, 0.050, 0.0175)), 'source_stand': ((-0.291, 0.0, -0.194), np.eye(3), (0.029, 0.060, 0.056)),
    'support_foot': ((-0.305, 0.2675, -0.244), np.eye(3), (0.085, 0.0725, 0.006)),
    # X-ray enclosure (inner faces of the URDF shell / door boxes): the front plane of the closed doors, left pillar and right column (y = -0.395 m),
    # the rear wall (y = +0.405 m) and the side walls (x = -0.63 / +0.63 m); the front is modelled closed whatever the door state
    'enclosure_front': ((0.090, -0.430, 0.123), np.eye(3), (0.650, 0.015, 0.422)), 'enclosure_rear': ((0.090, 0.395, 0.123), np.eye(3), (0.650, 0.010, 0.422)),
    'enclosure_left': ((-0.550, -0.005, 0.123), np.eye(3), (0.010, 0.410, 0.422)), 'enclosure_right': ((0.730, -0.005, 0.123), np.eye(3), (0.010, 0.410, 0.422)),
}


def _seg_seg(p1, q1, p2, q2):
    """Shortest distance between segments p1q1 and p2q2 (arrays (P, 3)); Ericson, Real-Time Collision Detection 5.1.9."""
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a = np.einsum('ij,ij->i', d1, d1); e = np.einsum('ij,ij->i', d2, d2); f = np.einsum('ij,ij->i', d2, r)
    c = np.einsum('ij,ij->i', d1, r); b = np.einsum('ij,ij->i', d1, d2)
    den = a * e - b * b
    s = np.where(den > 1e-14, np.clip((b * f - c * e) / np.where(den > 1e-14, den, 1.0), 0, 1), 0.0)
    t = (b * s + f) / np.where(e > 1e-14, e, 1.0)
    s = np.where(t < 0, np.clip(-c / np.where(a > 1e-14, a, 1.0), 0, 1), np.where(t > 1, np.clip((b - c) / np.where(a > 1e-14, a, 1.0), 0, 1), s))
    t = np.clip(t, 0, 1)
    return np.linalg.norm(p1 + d1 * s[:, None] - (p2 + d2 * t[:, None]), axis=1)


def _cap_box(p, q, r, c, R, h):
    """Capsule (segment p-q, radius r) against oriented boxes (centre c, axes R, half sizes h): clearance (P,)."""
    ts = np.linspace(0, 1, 9)
    pts = p[:, None, :] + (q - p)[:, None, :] * ts[None, :, None]
    loc = np.einsum('pki,pij->pkj', pts - c[:, None, :], R)
    h = np.asarray(h, float)
    d = np.linalg.norm(loc - np.clip(loc, -h, h), axis=2)
    return d.min(1) - r


def _box_box(c1, R1, h1, c2, R2, h2):
    """Separating-axis test for oriented boxes: True where they overlap (arrays (P, ...))."""
    h1, h2 = np.asarray(h1, float), np.asarray(h2, float)
    axes = [R1[:, :, i] for i in range(3)] + [R2[:, :, i] for i in range(3)]
    for i in range(3):
        for j in range(3):
            cr = np.cross(R1[:, :, i], R2[:, :, j])
            n = np.linalg.norm(cr, axis=1, keepdims=True)
            axes.append(np.where(n > 1e-9, cr / np.where(n > 1e-9, n, 1.0), R1[:, :, i]))
    sep = np.zeros(len(c1), bool)
    d = c2 - c1
    for ax in axes:
        ra = sum(h1[k] * np.abs(np.einsum('pi,pi->p', R1[:, :, k], ax)) for k in range(3))
        rb = sum(h2[k] * np.abs(np.einsum('pi,pi->p', R2[:, :, k], ax)) for k in range(3))
        sep |= np.abs(np.einsum('pi,pi->p', d, ax)) > ra + rb
    return ~sep


def _rot_axis_batch(axis, ang):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    s, c = np.sin(ang)[:, None, None], np.cos(ang)[:, None, None]
    return np.eye(3)[None] + s * K[None] + (1 - c) * (K @ K)[None]


def moving_primitives(omega, kappa, theta, dist):
    """Collision primitives of the goniometer (omega, kappa) and the detector arm (theta, distance) for P poses."""
    omega, kappa, theta, dist = (np.atleast_1d(np.asarray(v, float)) for v in (omega, kappa, theta, dist))
    Om = rot_z_batch(omega)
    OK = Om @ _rot_axis_batch(KAPPA_AXIS, kappa)
    aphi = OK[:, :, 2]
    kax = np.einsum('pij,j->pi', Om, KAPPA_AXIS)
    caps = {'head_pin': (-0.012 * aphi, -0.024 * aphi, 0.0045), 'goniometer_head': (-0.028 * aphi, -0.058 * aphi, 0.019),
            'phi_shaft': (-0.066 * aphi, -0.081 * aphi, 0.017), 'phi_housing_top': (-0.090 * aphi, -0.100 * aphi, 0.021),
            'phi_housing': (-0.115 * aphi, -0.125 * aphi, 0.030),
            'kappa_motor': (0.200 * kax, 0.262 * kax, 0.047)}                 # bearing flange and motor (capsule end caps reach the motor end cap)
    boxes = {'kappa_block': (np.einsum('pij,j->pi', OK, KAPPA_BLOCK_C), OK @ _RK, (0.050, 0.0625, 0.060)),
             'omega_bracket': (np.einsum('pij,j->pi', Om, np.array([-0.120, 0.0, -0.1915])), Om, (0.060, 0.040, 0.0165))}
    Th = rot_z_batch(theta)
    def arm_box(x_c, y_h, z_c, x_h, z_h):
        c = np.stack([x_c, np.zeros_like(x_c), np.full_like(x_c, z_c)], 1)
        return np.einsum('pij,pj->pi', Th, c), Th, (x_h, y_h, z_h)
    det = {'detector_nose': arm_box(dist - DET_FACE_OFFSET_M + DET_NOSE[0] / 2, DET_NOSE[1], 0.0, DET_NOSE[0] / 2, DET_NOSE[2]),
           'detector_body': arm_box(dist - DET_FACE_OFFSET_M + DET_NOSE[0] + DET_BODY[0] / 2, DET_BODY[1], 0.0, DET_BODY[0] / 2, DET_BODY[2]),
           'detector_carriage': arm_box(dist + 0.223, 0.055, -0.11575, 0.125, 0.04425), 'carriage_front': arm_box(dist + 0.0755, 0.030, -0.0915, 0.0225, 0.020),
           'arm_pedestal': arm_box(np.full_like(dist, 0.275), 0.055, -0.190, 0.080, 0.030), 'arm_plate': arm_box(np.full_like(dist, 0.2375), 0.060, -0.235, 0.1225, 0.015)}
    return caps, boxes, det


def collisions(omega, kappa, theta, dist, margin=COLLISION_MARGIN_M):
    """Pairs of the collision model in contact (clearance below the margin) for P poses: (any (P,), list of pair names per pose)."""
    caps, boxes, det = moving_primitives(omega, kappa, theta, dist)
    P = len(np.atleast_1d(omega))
    hit = np.zeros(P, bool)
    names = [[] for _ in range(P)]

    def mark(mask, name):
        nonlocal hit
        for i in np.nonzero(mask)[0]:
            names[i].append(name)
        hit |= mask
    fixed_caps = {k: (np.tile(np.array(p0, float), (P, 1)), np.tile(np.array(p1, float), (P, 1)), r) for k, (p0, p1, r) in FIXED_CAPSULES.items()}
    fixed_boxes = {k: (np.tile(np.array(c, float), (P, 1)), np.tile(np.asarray(R, float), (P, 1, 1)), h) for k, (c, R, h) in FIXED_BOXES.items()}
    for gk, (p, q, r) in caps.items():                     # goniometer capsules against fixed parts and the detector arm
        for fk, (p2, q2, r2) in fixed_caps.items():
            mark(_seg_seg(p, q, p2, q2) < r + r2 + margin, f'{gk}/{fk}')
        for fk, (c, R, h) in list(fixed_boxes.items()) + list(det.items()):
            mark(_cap_box(p, q, r, c, R, h) < margin, f'{gk}/{fk}')
    for gk, (c1, R1, h1) in boxes.items():
        for fk, (p2, q2, r2) in fixed_caps.items():
            mark(_cap_box(p2, q2, r2, c1, R1, h1) < margin, f'{gk}/{fk}')
        for fk, (c2, R2, h2) in list(fixed_boxes.items()) + list(det.items()):
            mark(_box_box(c1, R1, np.asarray(h1) + margin / 2, c2, R2, np.asarray(h2) + margin / 2), f'{gk}/{fk}')
    for dk, (c1, R1, h1) in det.items():                   # detector arm against the fixed parts
        for fk, (p2, q2, r2) in fixed_caps.items():
            mark(_cap_box(p2, q2, r2, c1, R1, h1) < margin, f'{dk}/{fk}')
        for fk, (c2, R2, h2) in fixed_boxes.items():
            mark(_box_box(c1, R1, np.asarray(h1) + margin / 2, c2, R2, np.asarray(h2) + margin / 2), f'{dk}/{fk}')
    return hit, names


def path_collision(start, target, n=24):
    """First colliding pair along the straight joint-space path from start to target (dicts omega, kappa, theta, distance), or None."""
    t = np.linspace(0.0, 1.0, n + 1)
    pose = {k: start[k] + (target[k] - start[k]) * t for k in ('omega', 'kappa', 'theta', 'distance')}
    hit, names = collisions(pose['omega'], pose['kappa'], pose['theta'], pose['distance'])
    if not hit.any():
        return None
    i = int(np.argmax(hit))
    return dict(fraction=float(t[i]), pairs=names[i][:6])


def plan_route(start, target):
    """Collision-free sequence of straight joint-space legs from start to target, as the control software serialises moves: direct; else kappa to 0,
    omega / phi and 2theta / distance one after the other (both orders), kappa to the target; the same with the detector retracted to the maximum
    distance.  None when every sequence collides."""
    keys = ('omega', 'kappa', 'phi', 'theta', 'distance')
    s, t = {k: float(start[k]) for k in keys}, {k: float(target[k]) for k in keys}
    seqs = [[s, t]]
    for far in (None, DIST_MAX_M):
        a = dict(s, kappa=0.0) if far is None else dict(s, kappa=0.0, distance=far)
        pre = [s, dict(s, distance=far)] if far is not None else [s]
        d_t = t['distance'] if far is None else far
        b1 = dict(a, omega=t['omega'], phi=t['phi'])
        c1 = dict(b1, theta=t['theta'], distance=d_t)
        b2 = dict(a, theta=t['theta'], distance=d_t)
        c2 = dict(b2, omega=t['omega'], phi=t['phi'])
        for mid in ((b1, c1), (b2, c2)):
            tail = [dict(mid[1], kappa=t['kappa'])] + ([t] if far is not None else [])
            seqs.append(pre + [a, mid[0], mid[1]] + tail)
    for seq in seqs:
        legs = [(a, b) for a, b in zip(seq, seq[1:]) if any(abs(a[k] - b[k]) > 1e-12 for k in keys)]
        if all(path_collision(a, b) is None for a, b in legs):
            return legs
    return None


# ---------------------------------------------------------------- detector geometry
def detector_axes(theta):
    n = np.array([math.cos(theta), math.sin(theta), 0.0])
    eu = np.array([-math.sin(theta), math.cos(theta), 0.0])
    return n, eu, np.array([0.0, 0.0, 1.0])


def project(s1, theta, dist):
    """Diffracted unit vectors (N, 3) to detector pixel coordinates (continuous; pixel i spans [i, i+1))."""
    n, eu, ev = detector_axes(theta)
    c = s1 @ n
    ok = c > 1e-6
    t = dist / np.where(ok, c, 1.0)
    p = s1 * t[:, None]
    x = (p @ eu) / PIXEL_M + NX / 2
    y = -(p @ ev) / PIXEL_M + NY / 2
    on = ok & (x >= 0) & (x < NX) & (y >= 0) & (y < NY)
    return x, y, t, c, on


def pixel_rays(theta, dist, xs, ys):
    n, eu, ev = detector_axes(theta)
    u = (np.asarray(xs, float) - NX / 2) * PIXEL_M
    v = -(np.asarray(ys, float) - NY / 2) * PIXEL_M
    p = dist * n + u[..., None] * eu + v[..., None] * ev
    t = np.linalg.norm(p, axis=-1)
    return p / t[..., None], t


def shadowed(s1):
    """Rays blocked by the beam stop (disc of radius 1.5 mm at 12 mm) or its vertical rod."""
    with np.errstate(divide='ignore', invalid='ignore'):
        k = BEAM_STOP['x'] / np.where(s1[..., 0] > 1e-9, s1[..., 0], np.nan)
    y, z = s1[..., 1] * k, s1[..., 2] * k
    disc = (y ** 2 + z ** 2) < (BEAM_STOP['r'] + 0.0003) ** 2
    rod = (np.abs(y) < BEAM_STOP['rod_r'] + 0.0002) & (z > 0) & (z < BEAM_STOP['rod_top'])
    return np.nan_to_num(disc | rod, nan=False).astype(bool) & (s1[..., 0] > 0)


def sensor_qe(target, cos_a, energy_scale=1.0):
    """Quantum efficiency of the (engineering) 0.3 mm silicon sensor at the incidence angle; energy_scale = E / E(K-alpha1) for fluorescence."""
    mu = mu_rho('Si', f'{target}_Ka1') * SI_DENSITY * energy_scale ** -2.8
    return 1.0 - np.exp(-mu * SENSOR_SI_M / np.clip(cos_a, 0.05, 1.0))


def threshold_efficiency(frac):
    """Counting efficiency of primary photons for a lower threshold at frac x E (charge sharing at the pixel borders)."""
    return 1.0 - CHARGE_SHARE_LOSS * max(0.0, frac - 0.5)


def threshold_accept(e_ph, thr_ev):
    return 1.0 / (1.0 + np.exp((thr_ev - e_ph) / (0.03 * e_ph)))


# ---------------------------------------------------------------- source
def source_flux(target, kv, ma, divergence, anode_s=0.0):
    """K-alpha photons/s at the crystal: I (V - V_K)^1.6 scaling from the reference point, divergence acceptance (d / 10 mrad)^2, slow anode wear."""
    if kv <= 0 or ma <= 0:
        return 0.0
    ref = FLUX_REF[target]
    vk = element(target)['k_edge_ev']
    if kv <= vk:
        return 0.0
    f = ref['flux'] * (ma / ref['ma']) * ((kv - vk) / (ref['kv'] - vk)) ** FLUX_EXPONENT
    f *= (divergence / DIVERGENCE_RANGE_RAD[1]) ** 2
    return f * max(0.7, 1.0 - 0.05 * anode_s / 3.6e6)


def ramp_plan(kv0, ma0, kv1, ma1, t0):
    """Set-point change of the generator (engineering controller model): kV and mA ramp one after the other at 2 kV/s and 2 mA/s, first the one
    whose change lowers the load (rising current: the voltage first; falling current: the current first), so V x I stays below the larger of
    the start and end loads (both <= 1.2 kW)."""
    d_kv, d_ma = abs(kv1 - kv0) / HV_RAMP_V_S, abs(ma1 - ma0) / MA_RAMP_A_S
    first = 'kv' if ma1 >= ma0 else 'ma'
    return dict(t0=t0, kv0=kv0, ma0=ma0, kv1=kv1, ma1=ma1, first=first, d_kv=d_kv, d_ma=d_ma, d1=d_kv if first == 'kv' else d_ma, dur=d_kv + d_ma)


def ramp_values(r, t):
    """Tube voltage and current of a ramp plan at time t."""
    tau = t - r['t0']

    def frac(x, d):
        return 1.0 if d <= 0 else min(1.0, max(0.0, x / d))
    if r['first'] == 'kv':
        fk, fm = frac(tau, r['d_kv']), frac(tau - r['d_kv'], r['d_ma'])
    else:
        fm, fk = frac(tau, r['d_ma']), frac(tau - r['d_ma'], r['d_kv'])
    return r['kv0'] + (r['kv1'] - r['kv0']) * fk, r['ma0'] + (r['ma1'] - r['ma0']) * fm


def beam_sigma(divergence, target='Cu'):
    """Gaussian sigma of the beam at the crystal: FWHM = a (effective focus / Cu focus) + b x divergence (engineering; the confocal optics image
    the focus, the Mo focus is 0.15 x 0.1 mm against the Cu 0.07 mm circle)."""
    fx, fy = FOCUS_M[target]
    scale = math.sqrt(fx * fy) / math.sqrt(FOCUS_M['Cu'][0] * FOCUS_M['Cu'][1])
    return (BEAM_FWHM_M[0] * scale + BEAM_FWHM_M[1] * divergence * 1e3) / 2.3548


def electrons_per_kg_air():
    return sum(w * element(el)['Z'] / (element(el)['atomic_weight'] * 1e-3) * N_A for el, w in AIR_MASS_FRACTIONS.items())


def _erf(x):
    x = np.asarray(x, float)
    t = 1.0 / (1.0 + 0.3275911 * np.abs(x))
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return np.sign(x) * y


def ncdf(x):
    return 0.5 * (1.0 + _erf(np.asarray(x, float) / math.sqrt(2.0)))


# ---------------------------------------------------------------- crystal diffraction (rotation method)
@functools.lru_cache(maxsize=16)
def reflection_set(sid, t_key, target, inverted):
    """Reflections (d >= max(0.502 lambda, 0.6 A)), |F|^2 at K-alpha1 and the direct cell of the substance at the crystal temperature (K, rounded)."""
    A, u = model_at(sid, float(t_key))
    ld = line_data(target)
    H, d_min = hkl_sphere(A, max(0.502 * ld['lam1'], D_MIN_SIM_M))
    cv = centring_vectors(substance_model(sid)['entry'])
    if cv:                                                                 # only lattice points of the centred lattice are reflections
        ph = H @ np.array(cv).T
        H = H[np.all(np.abs(ph - np.round(ph)) < 1e-6, axis=1)]
    F = structure_factors(sid, H, A, u, ld['key'], inverted)
    return H, np.abs(F) ** 2, A, d_min


def twin_rotation(A, kind, rng):
    """Twin law in the crystal Cartesian frame: 180 deg about the first low-index direct row that is not a lattice symmetry axis ('twin'), or a
    small random misorientation of a second grain ('split')."""
    if kind == 'split':
        ax = rng.standard_normal(3)
        return rot(ax, 3.0 * DEG)
    Ainv = np.linalg.inv(A)
    for uvw in ((1, 0, 0), (1, 0, 1), (1, 1, 0), (0, 1, 1), (1, 1, 1), (2, 1, 0), (1, 2, 0), (2, 0, 1)):
        R = rot(A @ np.array(uvw, float), PI)
        M = Ainv @ R @ A
        if np.max(np.abs(M - np.round(M))) > 1e-3:
            return R
    return rot(A @ np.array([2.0, 1.0, 3.0]), PI)


def powder_rings(sid, H, F2, A, key, lam):
    """Debye-Scherrer ring families of the substance: (q = 1/d, sum of |F|^2 over the family)."""
    B = reciprocal(A)
    q = np.linalg.norm(H @ B.T, axis=1)
    keys = np.round(q * 1e-7).astype(np.int64)                     # families within 0.001 1/A
    fam = {}
    for kq, f2 in zip(keys, F2):
        fam[kq] = fam.get(kq, 0.0) + float(f2)
    out = sorted(((kq * 1e7, v) for kq, v in fam.items() if kq * 1e7 * lam / 2 < 1.0), key=lambda x: -x[1])[:60]
    return out


def crystal_absorption(cr, mu, s0_loc, s1_loc=None):
    """Mean transmission exp(-mu (d_in + d_out)) over the block crystal (27-point Gauss-Legendre quadrature, uniform weights); directions in the shape frame."""
    half = 0.5 * np.asarray(cr['size'], float)
    p_loc = QUAD_X * half
    path = box_exit(p_loc, -s0_loc, half)
    if s1_loc is not None:
        path = path + box_exit(p_loc, s1_loc, half)
    return (np.exp(-mu * path) * QUAD_W[None, :]).sum(1)


_BEAM_GRID = np.linspace(-3.0, 3.0, 25)


def illuminated_volume(cr, R_c, c_lab, sig_b):
    """Beam-weighted crystal volume  int G(y, z) L(y, z) dy dz  (m; G the normalised Gaussian beam profile, L the chord of the ray along +X through
    the block crystal), on a 25 x 25 grid over +-3 sigma of the beam, for crystal-shape frames R_c (N, 3, 3) and centres c_lab (N, 3)."""
    half = 0.5 * np.asarray(cr['size'], float)
    g = _BEAM_GRID * sig_b
    yy, zz = np.meshgrid(g, g, indexing='ij')
    pts = np.stack([np.zeros(yy.size), yy.ravel(), zz.ravel()], 1)                    # ray origins in the plane x = 0 (lab, relative to the gonio centre)
    w = np.exp(-(pts[:, 1] ** 2 + pts[:, 2] ** 2) / (2 * sig_b ** 2)) / (2 * PI * sig_b ** 2) * (g[1] - g[0]) ** 2
    out = np.zeros(len(R_c))
    for i in range(len(R_c)):
        o = (pts - c_lab[i]) @ R_c[i]                                                    # ray origins in the shape frame
        d = R_c[i].T @ np.array([1.0, 0.0, 0.0])
        with np.errstate(divide='ignore', invalid='ignore'):
            t1 = np.where(np.abs(d) > 1e-12, (-half - o) / d, np.where(np.abs(o) <= half, -np.inf, np.inf))
            t2 = np.where(np.abs(d) > 1e-12, (half - o) / d, np.where(np.abs(o) <= half, np.inf, -np.inf))
        tn = np.max(np.minimum(t1, t2), axis=1)
        tf = np.min(np.maximum(t1, t2), axis=1)
        out[i] = float(np.sum(w * np.clip(tf - tn, 0.0, None)))
    return out


def background_terms(cr, src, run, humidity):
    """Background sources of a run (photons/s/sr before the detector response; see background_rate)."""
    ld = line_data(src['target'])
    key = ld['key']
    flux_in = src['flux'] * math.exp(-air_mu(key) * AIR_PATH_IN_M)
    sig_b = beam_sigma(src['divergence'], src['target'])
    terms = dict(target=src['target'], lam=ld['lam1'], e1=ld['e1'], mu_air=air_mu(key), thr_frac=src['threshold_frac'], thr_ev=src['threshold_frac'] * ld['e1'],
                 air=flux_in * AIR_DENSITY_KG_M3 * electrons_per_kg_air() * AIR_SCATTER_PATH_M, oil=flux_in * OIL_ELECTRONS_M3 * OIL_THICKNESS_M,
                 compton=0.0, amorph=0.0, rings=[], fluo=[], sig2t=math.hypot(src['divergence'] / 2.3548, 2e-4 / run['distance']))
    if cr is None or flux_in <= 0:
        return terms
    sid = cr['sid']
    H, F2, A, _ = reflection_set(sid, int(round(cr['T'])), src['target'], cr['inverted'])
    V = abs(np.linalg.det(A))
    mu, rho = mu_linear(sid, key, A)
    om_mid = 0.5 * (run['omega_start'] + run['omega_end'])
    Rg = gonio_matrix(om_mid, run['kappa'], run['phi'])
    R_c = (Rg @ cr['U'])[None]
    c_lab = (Rg @ cr['offset'])[None]
    s0_loc = (R_c[0].T @ np.array([1.0, 0, 0]))[None]
    ill = float(illuminated_volume(cr, R_c, c_lab, sig_b)[0] * crystal_absorption(cr, mu, s0_loc)[0])   # beam-weighted volume with the incident attenuation
    m = substance_model(sid)
    n_e = m['electrons_cell'] / V
    vol_frac = 1.0 if cr['defect'] != 'amorphous' else 1.0
    terms['compton'] = flux_in * ill * n_e * vol_frac
    if cr['defect'] == 'amorphous':
        terms['amorph'] = flux_in * ill * n_e
    if cr['defect'] == 'polycrystalline':
        lam = ld['lam1']
        lam_fac = ld['w1'] * ld['lam1'] ** 3 + ld['w2'] * ld['lam2'] ** 3
        for q, f2 in powder_rings(sid, H, F2, A, key, lam):
            th = math.asin(min(1.0, q * lam / 2))
            p = flux_in * ill * R_E ** 2 * lam_fac * f2 / (4 * V ** 2 * math.sin(th)) * (1 + math.cos(2 * th) ** 2) / 2
            terms['rings'].append((q, p))
    if cr['ice_m3'] > 0:
        rings, v_ice = ice_rings(key)
        lam = ld['lam1']
        lam_fac = ld['w1'] * ld['lam1'] ** 3 + ld['w2'] * ld['lam2'] ** 3
        ice_ill = flux_in * cr['ice_m3'] / (2 * PI * sig_b ** 2)
        for d, f2 in rings:
            th = math.asin(min(1.0, lam / (2 * d)))
            p = ice_ill * R_E ** 2 * lam_fac * f2 / (4 * v_ice ** 2 * math.sin(th)) * (1 + math.cos(2 * th) ** 2) / 2
            terms['rings'].append((1.0 / d, p))
    # K fluorescence of the elements whose K edge lies below the source energy
    r_eq = (3 * float(np.prod(cr['size'])) / (4 * PI)) ** (1 / 3)
    mass = {el: n * element(el)['atomic_weight'] for el, n in m['content'].items()}
    tot = sum(mass.values())
    for el in mass:
        e_ = element(el)
        if not e_['k_edge_ev'] or e_['k_edge_ev'] >= ld['e1'] or not e_['ka1_ev'] or e_['Z'] < 11:
            continue
        tau = e_['at_lines'][key]['sigma_photo_barn'] * 1e-28 * N_A / (e_['atomic_weight'] * 1e-3)
        mu_ph = rho * mass[el] / tot * tau
        k_frac = (e_['k_jump_ratio'] - 1.0) / e_['k_jump_ratio']
        ef = e_['ka1_ev']
        mu_f = mu * (ld['e1'] / ef) ** 2.8
        emitted = flux_in * ill * mu_ph * k_frac * e_['omega_k'] * math.exp(-0.75 * mu_f * r_eq)
        terms['fluo'].append((ef, emitted / (4 * PI)))
    return terms


def background_rate(bg, theta, dist, xs, ys):
    """Background counts/s per detector pixel at pixel coordinates (xs, ys)."""
    s1, t = pixel_rays(theta, dist, xs, ys)
    cos2t = np.clip(s1[..., 0], -1, 1)
    tth = np.arccos(cos2t)
    q = 2 * np.sin(tth / 2) / bg['lam']
    P = (1 + cos2t ** 2) / 2
    cos_a = dist / t
    om_px = PIXEL_M ** 2 * cos_a / t ** 2
    qe = sensor_qe(bg['target'], cos_a)
    tout = np.exp(-bg['mu_air'] * t)
    oil = OIL_HALO
    el = R_E ** 2 * P * (bg['air'] + bg['oil'] * (1 + oil['amplitude'] * np.exp(-(q - oil['q0']) ** 2 / (2 * oil['width'] ** 2)))
                         + bg['compton'] * (1 - np.exp(-(q / 0.5e10) ** 2))
                         + bg['amorph'] * 2.0 * np.exp(-(q - 1 / 4.2e-10) ** 2 / (2 * (0.06e10) ** 2)))
    rings = np.zeros_like(tth)
    for qr, p in bg['rings']:
        th2 = 2 * math.asin(min(1.0, qr * bg['lam'] / 2))
        rings += p * np.exp(-(tth - th2) ** 2 / (2 * bg['sig2t'] ** 2)) / (math.sqrt(2 * PI) * bg['sig2t']) / (2 * PI * np.maximum(np.sin(tth), 1e-3))
    rate = (el + rings) * om_px * qe * tout * threshold_efficiency(bg['thr_frac'])
    for ef, per_sr in bg['fluo']:
        scale = ef / bg['e1']
        rate = rate + per_sr * om_px * sensor_qe(bg['target'], cos_a, scale) * np.exp(-bg['mu_air'] * scale ** -2.8 * t) * threshold_accept(ef, bg['thr_ev'])
    return np.where(shadowed(s1), 0.0, rate)


def simulate_run(cr, src, run, n_frames, seeds, humidity):
    """Observations of one shutterless omega scan (n_frames of width run['frame_width_rad']): every reflection crossing the Ewald sphere inside the
    scanned range, landing on the sensor outside the beam-stop shadow, with its integrated counts, background, partiality, overload and measured
    (noisy) centroid and intensity."""
    ld = line_data(src['target'])
    key, lam1, lam2 = ld['key'], ld['lam1'], ld['lam2']
    fw, expo = run['frame_width_rad'], run['exposure_s']
    om0 = run['omega_start']
    sgn = 1.0 if run['omega_end'] >= run['omega_start'] else -1.0
    span = n_frames * fw
    theta, dist = run['theta'], run['distance']
    bg = background_terms(cr, src, run, humidity)
    empty = dict(n=0)
    out = dict(bg=bg, n_frames=n_frames, ring_peaks=dict(x=np.zeros(0), y=np.zeros(0), s=np.zeros(0), i=np.zeros(0), sigma=np.zeros(0)))
    rng = _rng(*seeds)
    if cr is None or src['flux'] <= 0 or n_frames <= 0:
        out['obs'] = empty
        return out
    sid = cr['sid']
    H, F2, A, d_min = reflection_set(sid, int(round(cr['T'])), src['target'], cr['inverted'])
    B = reciprocal(A)
    Vc = abs(np.linalg.det(A))
    mu, rho = mu_linear(sid, key, A)
    KP = rot(KAPPA_AXIS, run['kappa']) @ rot_z(run['phi'])
    flux_in = src['flux'] * math.exp(-air_mu(key) * AIR_PATH_IN_M)
    sig_b = beam_sigma(src['divergence'], src['target'])
    eta = cr['mosaic'] / 2.3548
    sig_m = math.hypot(eta, src['divergence'] / 2.3548)
    lam_fac = ld['w1'] * lam1 ** 3 + ld['w2'] * lam2 ** 3
    omega_dot = fw / expo
    decay = 1.0
    if substance_model(sid)['klass'] == 'molecular':
        decay = math.exp(-math.log(2) * cr['dose_gy'] / decay_half_dose(cr['T']))
    thr_eff = threshold_efficiency(src['threshold_frac'])
    doms = [(cr['U'], 1.0 - cr['frac2'], 1)]
    if cr['U2'] is not None and cr['frac2'] > 0:
        doms.append((cr['U2'], cr['frac2'], 2))
    if cr['defect'] in ('polycrystalline', 'amorphous'):
        doms = []
    cols = {k: [] for k in ('h', 'dom', 's', 'x', 'y', 'tth', 'd', 'L', 'P', 'S', 'sig_px', 'sig_om', 'dx2', 'dy2', 'cos_a', 't')}
    ill_s = np.linspace(-3 * fw, span + 3 * fw, max(8, int((span + 6 * fw) / DEG) + 2))          # illumination on a 1 deg grid over the scan
    Rg = np.einsum('nij,jk->nik', rot_z_batch(om0 + sgn * ill_s), KP @ cr['U'])
    cg = np.einsum('nij,j->ni', rot_z_batch(om0 + sgn * ill_s), KP @ cr['offset'])
    ill_v = illuminated_volume(cr, Rg, cg, sig_b)
    for Uk, frac_k, dom in doms:
        v = (KP @ Uk @ B @ H.T).T
        q2 = np.einsum('ij,ij->i', v, v)
        rho_ = np.hypot(v[:, 0], v[:, 1])
        c = -lam1 * q2 / 2
        ok = rho_ > np.abs(c) + 1e-3
        psi = np.arctan2(v[:, 1], v[:, 0])
        acs = np.arccos(np.clip(c / np.where(ok, rho_, 1.0), -1, 1))
        for sol in (1.0, -1.0):
            w = -psi + sol * acs
            s = np.mod(sgn * (w - om0), 2 * PI)
            sel = np.nonzero(ok & (s <= span + 3 * fw))[0]
            if not len(sel):
                continue
            s = s[sel]
            wom = om0 + sgn * s
            Rw = rot_z_batch(wom)
            r_lab = np.einsum('nij,nj->ni', Rw, v[sel])
            k1 = r_lab + np.array([1.0 / lam1, 0, 0])
            s1 = k1 / np.linalg.norm(k1, axis=1, keepdims=True)
            x, y, t, cos_a, on = project(s1, theta, dist)
            L = 1.0 / np.maximum(np.abs(s1[:, 1]), 1e-9)
            keep = on & ~shadowed(s1) & (L < 50.0)
            if not keep.any():
                continue
            idx = sel[keep]
            s, s1, x, y, t, cos_a, L, wom = s[keep], s1[keep], x[keep], y[keep], t[keep], cos_a[keep], L[keep], wom[keep]
            tth = np.arccos(np.clip(s1[:, 0], -1, 1))
            Pp = (1 + s1[:, 0] ** 2) / 2
            Rc = np.einsum('nij,jk->nik', rot_z_batch(wom), KP @ cr['U'])
            s0l = np.einsum('nji,j->ni', Rc, np.array([1.0, 0, 0]))
            s1l = np.einsum('nji,nj->ni', Rc, s1)
            absf = np.concatenate([crystal_absorption(cr, mu, s0l[i:i + 4000], s1l[i:i + 4000]) for i in range(0, len(wom), 4000)])
            Pi = np.interp(s, ill_s, ill_v) * absf
            S = (flux_in * R_E ** 2 * lam_fac * F2[idx] / Vc ** 2 * L * Pp * Pi * frac_k / omega_dot * np.exp(-air_mu(key) * t)
                 * sensor_qe(src['target'], cos_a) * thr_eff * decay)
            d = lam1 / (2 * np.sin(tth / 2))
            sig_ill = 1.0 / math.sqrt(1 / sig_b ** 2 + 12 / float(np.mean(cr['size'])) ** 2)
            sig_px = np.sqrt((sig_ill / cos_a) ** 2 + (t * src['divergence'] / 2.3548) ** 2 + (t * eta) ** 2) / PIXEL_M
            chi = np.arctan2(s1[:, 2], s1[:, 1])
            th2 = 2 * np.arcsin(np.clip(lam2 / (2 * d), -1, 1))
            s12 = np.stack([np.cos(th2), np.sin(th2) * np.cos(chi), np.sin(th2) * np.sin(chi)], 1)
            x2, y2, _, _, _ = project(s12, theta, dist)
            for k_, v_ in (('h', H[idx]), ('dom', np.full(len(idx), dom)), ('s', s), ('x', x), ('y', y), ('tth', tth), ('d', d), ('L', L), ('P', Pp), ('S', S),
                           ('sig_px', np.maximum(sig_px, 0.35)), ('sig_om', np.minimum(sig_m * L, 5 * DEG)), ('dx2', x2 - x), ('dy2', y2 - y), ('cos_a', cos_a), ('t', t)):
                cols[k_].append(v_)
    if not cols['s']:
        out['obs'] = empty
    else:
        o = {k: np.concatenate(v) for k, v in cols.items()}
        n = len(o['s'])
        if n > MAX_OBSERVATIONS:
            keep = np.argsort(-o['S'])[:MAX_OBSERVATIONS]
            o = {k: v[keep] for k, v in o.items()}
            n = MAX_OBSERVATIONS
        o['twin_overlap'] = np.zeros(n, bool)
        if (o['dom'] == 2).any() and (o['dom'] == 1).any():                 # overlapping spots of the two domains are measured together
            from scipy.spatial import cKDTree
            pts = np.stack([o['x'] / (2 * o['sig_px']), o['y'] / (2 * o['sig_px']), o['s'] / (2 * o['sig_om'])], 1)
            d1 = np.nonzero(o['dom'] == 1)[0]
            d2 = np.nonzero(o['dom'] == 2)[0]
            tree = cKDTree(pts[d1])
            dist_, j = tree.query(pts[d2], distance_upper_bound=1.0)
            hit = np.isfinite(dist_)
            np.add.at(o['S'], d1[j[hit]], o['S'][d2[hit]])
            o['twin_overlap'][d1[j[hit]]] = True
            drop = np.zeros(n, bool)
            drop[d2[hit]] = True
            o = {k: v[~drop] for k, v in o.items()}
            n = len(o['s'])
        inside = ncdf((span - o['s']) / o['sig_om']) - ncdf((0.0 - o['s']) / o['sig_om'])
        o['partial'] = inside < 0.95
        o['S_meas_ideal'] = o['S'] * inside
        rate_pk = o['S'] * omega_dot / (math.sqrt(2 * PI) * o['sig_om']) * np.minimum(1.0, 1.0 / (2 * PI * o['sig_px'] ** 2))
        loss = np.exp(-0.5 * rate_pk * DEAD_TIME_S)
        o['overload'] = rate_pk * DEAD_TIME_S > 0.2
        o['S_meas'] = o['S_meas_ideal'] * loss
        bgr = background_rate(bg, theta, dist, o['x'], o['y'])
        n_pk = np.maximum(9.0, PI * (2.5 * o['sig_px']) ** 2)
        n_fr = np.ceil(5 * o['sig_om'] / fw) + 1
        o['bg_px_s'] = bgr
        B_foot = bgr * n_pk * n_fr * expo
        tot = rng.poisson(np.maximum(o['S_meas'] + B_foot, 0.0))
        bge = rng.poisson(np.maximum(4 * B_foot, 0.0)) / 4.0
        o['i_obs'] = tot - bge
        o['sigma'] = np.sqrt(np.maximum(tot + bge / 4.0, 1.0))
        snr = np.sqrt(np.maximum(o['S_meas'], 1.0))
        o['x_obs'] = o['x'] + rng.standard_normal(n) * (o['sig_px'] / snr + 0.05)
        o['y_obs'] = o['y'] + rng.standard_normal(n) * (o['sig_px'] / snr + 0.05)
        o['s_obs'] = o['s'] + rng.standard_normal(n) * (o['sig_om'] / snr + 0.02 * fw)
        o['sig_om_obs'] = o['sig_om'] * np.exp(0.1 * rng.standard_normal(n))
        inside_obs = ncdf((span - o['s_obs']) / o['sig_om_obs']) - ncdf((0.0 - o['s_obs']) / o['sig_om_obs'])
        o['partial_obs'] = inside_obs < 0.95                              # partiality as the integration sees it (measured centroid and width)
        o['n'] = n
        o['frame'] = o['s'] / fw
        out['obs'] = o
    # powder / ice rings seen by the peak search as isolated spots on the rings
    rings = bg['rings']
    if rings:
        tot_p = sum(p for _, p in rings)
        n_pk = int(min(400, 40 * n_frames * min(1.0, tot_p / 1e3)))
        if n_pk > 0:
            qs = np.array([q for q, _ in rings])
            ps = np.array([p for _, p in rings]) / tot_p
            pick = rng.choice(len(qs), size=n_pk, p=ps)
            th2 = 2 * np.arcsin(np.clip(qs[pick] * ld['lam1'] / 2, -1, 1))
            chi = rng.uniform(-PI, PI, n_pk)
            s1 = np.stack([np.cos(th2), np.sin(th2) * np.cos(chi), np.sin(th2) * np.sin(chi)], 1)
            x, y, _, _, on = project(s1, theta, dist)
            on &= ~shadowed(s1)
            ii = rng.gamma(2.0, 400.0, n_pk)
            out['ring_peaks'] = dict(x=x[on], y=y[on], s=rng.uniform(0, span, n_pk)[on], i=ii[on], sigma=np.sqrt(ii[on] + 50.0))
    return out


def decay_half_dose(T):
    (t1, d1), (t2, d2) = DECAY_HALF_DOSE_GY['cold'], DECAY_HALF_DOSE_GY['warm']
    f = min(1.0, max(0.0, (T - t1) / (t2 - t1)))
    return math.exp(math.log(d1) + f * (math.log(d2) - math.log(d1)))


def render_frame(runrec, k, binning, region=None, seeds=(0,)):
    """Counts of frame k of a recorded run (binned, or a full-resolution region [x0, y0, x1, y1]) from the recorded spot and background model with
    Poisson counting; deterministic for the record, run and frame."""
    run, o, bg = runrec['run'], runrec['obs'], runrec['bg']
    fw, expo = run['frame_width_rad'], run['exposure_s']
    if region is None:
        x0, y0, x1, y1 = 0, 0, NX, NY
        b = binning
    else:
        x0, y0, x1, y1 = region
        b = 1
    nx, ny = (x1 - x0 + b - 1) // b, (y1 - y0 + b - 1) // b
    ex = np.minimum(x0 + np.arange(nx + 1) * b, x1).astype(float)
    ey = np.minimum(y0 + np.arange(ny + 1) * b, y1).astype(float)
    cx = 0.5 * (ex[:-1] + ex[1:])
    cy = 0.5 * (ey[:-1] + ey[1:])
    area = np.outer(ey[1:] - ey[:-1], ex[1:] - ex[:-1])
    img = background_rate(bg, run['theta'], run['distance'], cx[None, :], cy[:, None]) * area * expo
    if o.get('n', 0):
        lo, hi = k * fw, (k + 1) * fw
        frac = ncdf((hi - o['s']) / o['sig_om']) - ncdf((lo - o['s']) / o['sig_om'])
        sel = np.nonzero((frac > 1e-5) & (o['x'] + 6 * o['sig_px'] > x0) & (o['x'] - 6 * o['sig_px'] < x1) & (o['y'] + 6 * o['sig_px'] > y0) & (o['y'] - 6 * o['sig_px'] < y1))[0]
        w1 = line_data(bg['target'])['w1']
        for i in sel:
            amp = o['S'][i] * frac[i] * o['S_meas'][i] / max(o['S_meas_ideal'][i], 1e-30)       # share of the frame, count-rate loss
            sg = o['sig_px'][i]
            for wx, ddx, ddy in ((w1, 0.0, 0.0), (1 - w1, o['dx2'][i], o['dy2'][i])):
                xc, yc = o['x'][i] + ddx, o['y'][i] + ddy
                ia = max(0, int(np.searchsorted(ex, xc - 5 * sg)) - 1)
                ib = min(nx, int(np.searchsorted(ex, xc + 5 * sg)) + 1)
                ja = max(0, int(np.searchsorted(ey, yc - 5 * sg)) - 1)
                jb = min(ny, int(np.searchsorted(ey, yc + 5 * sg)) + 1)
                if ia >= ib or ja >= jb:
                    continue
                gx = ncdf((ex[ia + 1:ib + 1] - xc) / sg) - ncdf((ex[ia:ib] - xc) / sg)
                gy = ncdf((ey[ja + 1:jb + 1] - yc) / sg) - ncdf((ey[ja:jb] - yc) / sg)
                img[ja:jb, ia:ib] += amp * wx * np.outer(gy, gx)
    rng = _rng(*seeds, k, b, x0, y0)
    counts = rng.poisson(np.maximum(img, 0.0))
    return counts, dict(x0=x0, y0=y0, x1=x1, y1=y1, binning=b, expected_max=float(img.max()) if img.size else 0.0)


# ---------------------------------------------------------------- processing (CrysAlisPro-like; uses only the recorded data)
def peak_table(runs, thr):
    """Peaks of the recorded runs: measured centroids (pixel x, y and scan position) of spots with I / sigma above thr, plus ring spots."""
    cols = {k: [] for k in ('run', 'x', 'y', 's', 'i', 'sigma')}
    for ri, rr in enumerate(runs):
        o = rr['obs']
        if o.get('n', 0):
            sel = (o['i_obs'] / o['sigma'] > thr) & ~o['partial_obs']
            for k, v in (('x', o['x_obs']), ('y', o['y_obs']), ('s', o['s_obs']), ('i', o['i_obs']), ('sigma', o['sigma'])):
                cols[k].append(v[sel])
            cols['run'].append(np.full(int(sel.sum()), ri))
        rp = rr['ring_peaks']
        if len(rp['x']):
            sel = rp['i'] / rp['sigma'] > thr
            for k in ('x', 'y', 's', 'i', 'sigma'):
                cols[k].append(rp[k][sel])
            cols['run'].append(np.full(int(sel.sum()), ri))
    pk = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in cols.items()}
    pk['run'] = pk['run'].astype(int)
    if len(pk['x']) > PEAKS_MAX:                                           # the strongest peaks
        keep = np.argsort(-pk['i'] / pk['sigma'])[:PEAKS_MAX]
        pk = {k: v[keep] for k, v in pk.items()}
    return pk


def reciprocal_vectors(pk, runs, lam):
    """Reciprocal-lattice vectors (1/m) of the peaks in the goniometer zero frame (omega = kappa = phi = 0)."""
    r0 = np.zeros((len(pk['x']), 3))
    for ri, rr in enumerate(runs):
        sel = pk['run'] == ri
        if not sel.any():
            continue
        run = rr['run']
        s1, _ = pixel_rays(run['theta'], run['distance'], pk['x'][sel], pk['y'][sel])
        r_lab = (s1 - np.array([1.0, 0, 0])) / lam
        sgn = 1.0 if run['omega_end'] >= run['omega_start'] else -1.0
        om = run['omega_start'] + sgn * pk['s'][sel]
        KP = rot(KAPPA_AXIS, run['kappa']) @ rot_z(run['phi'])
        R = np.einsum('nij,jk->nik', rot_z_batch(om), KP)
        r0[sel] = np.einsum('nji,nj->ni', R, r_lab)
    return r0


def _hemisphere(n):
    i = np.arange(n) + 0.5
    z = 1 - i / n
    r = np.sqrt(1 - z * z)
    ph = PI * (1 + math.sqrt(5)) * i
    return np.stack([r * np.cos(ph), r * np.sin(ph), z], 1)


def _refine_vector(u, rr, tol=0.2):
    for _ in range(4):
        n = rr @ u
        ni = np.round(n)
        ok = np.abs(n - ni) < tol
        if ok.sum() < 6:
            break
        u = np.linalg.lstsq(rr[ok], ni[ok], rcond=None)[0]
    return u, float(np.mean(np.abs(rr @ u - np.round(rr @ u)) < 0.12))


def fft_index(r0, max_cell_a=60.0, n_dir=7200):
    """Direct basis (columns, m) from the peaks by 1D FFT indexing (Steller, Bolotovsky and Rossmann, J. Appl. Cryst. 30 (1997) 1036): the
    projections of the reciprocal vectors on a direct-lattice row are integers over its length; the three shortest non-coplanar rows that index
    most peaks form the cell.  None when fewer than 8 peaks or no lattice."""
    r = r0 * 1e-10
    rn = np.linalg.norm(r, axis=1)
    if len(r) < 8:
        return None
    lim = max(0.5, float(np.percentile(rn, 70)))
    rr = r[rn <= lim] if (rn <= lim).sum() >= 8 else r
    rr = rr[np.argsort(np.linalg.norm(rr, axis=1))[:600]]
    dirs = _hemisphere(n_dir)
    p = rr @ dirs.T
    pmax = float(np.abs(p).max()) + 1e-9
    dp = 1.0 / (6 * max_cell_a)
    nb = int(2 ** math.ceil(math.log2(2 * pmax / dp + 2)))
    bins = np.clip(((p + pmax) / dp).astype(int), 0, nb - 1)
    flat = (np.arange(n_dir)[None, :] * nb + bins).ravel()
    hist = np.bincount(flat, minlength=n_dir * nb).reshape(n_dir, nb).astype(float)
    spec = np.abs(np.fft.rfft(hist, axis=1))
    kmin, kmax = max(2, int(math.ceil(2.0 * nb * dp))), min(spec.shape[1] - 2, int(max_cell_a * nb * dp))
    if kmax <= kmin:
        return None
    sub = spec[:, kmin:kmax + 1]
    kb = np.argmax(sub, 1) + kmin
    mb = sub.max(1)
    for m in (6, 5, 4, 3, 2):                                             # fundamental instead of a harmonic
        k2 = np.round(kb / m).astype(int)
        okk = k2 >= kmin
        v2 = np.max(np.stack([spec[np.arange(n_dir), np.clip(k2 + d, 0, spec.shape[1] - 1)] for d in (-1, 0, 1)]), 0)
        take = okk & (v2 >= 0.6 * mb) & (kb % 1 == 0) & (k2 < kb)
        kb = np.where(take, k2, kb)
    L = kb / (nb * dp)
    order = np.argsort(-mb)
    cands = []
    for i in order[:400]:
        u = dirs[i] * L[i]
        if any(abs(abs(float(u @ c)) / (np.linalg.norm(u) * np.linalg.norm(c)) - 1.0) < 1.5e-3 and abs(np.linalg.norm(u) / np.linalg.norm(c) - 1) < 0.05
               for c in cands):
            continue
        cands.append(u)
        if len(cands) >= 40:
            break
    refined = []
    for u in cands:
        u2, sc = _refine_vector(u, rr)
        if np.linalg.norm(u2) > 1.0:
            refined.append((sc, u2))
    if len(refined) < 3:
        return None
    best = max(s for s, _ in refined)
    pool = sorted([u for s, u in refined if s >= 0.8 * best], key=np.linalg.norm)[:15]
    choice, top = None, (-1.0, 0.0)
    for a, b, c in itertools.combinations(range(len(pool)), 3):
        Am = np.column_stack([pool[a], pool[b], pool[c]])
        vol = abs(np.linalg.det(Am))
        if vol < 0.05 * np.prod([np.linalg.norm(pool[i]) for i in (a, b, c)]) or vol < 8.0:
            continue
        h = rr @ Am
        frac = float(np.mean(np.all(np.abs(h - np.round(h)) < 0.12, axis=1)))
        if frac > top[0] + 0.02 or (abs(frac - top[0]) <= 0.02 and vol < top[1]):
            choice, top = Am, (frac, vol)
    if choice is None or top[0] < 0.3:
        return None
    if np.linalg.det(choice) < 0:
        choice = -choice
    return choice * 1e-10


def refine_ub(r0, A, tol=0.12):
    """Least-squares refinement of the direct basis A (columns, m) from the indexed peaks (r = B h, B = A^-T); returns A, the indexed mask, the
    indices and the residual RMS (1/m)."""
    ok = None
    for tol_i in (0.2, 0.15, tol, tol):
        h = r0 @ A
        hr = np.round(h)
        ok = np.all(np.abs(h - hr) < tol_i, axis=1) & np.any(hr != 0, axis=1)
        if ok.sum() < 6:
            break
        Bt = np.linalg.lstsq(hr[ok], r0[ok], rcond=None)[0]
        A = np.linalg.inv(Bt)
    h = r0 @ A
    hr = np.round(h)
    ok = np.all(np.abs(h - hr) < tol, axis=1) & np.any(hr != 0, axis=1)
    res = r0[ok] - hr[ok] @ np.linalg.inv(A)
    rms = float(np.sqrt(np.mean(np.sum(res ** 2, 1)))) if ok.any() else math.inf
    return A, ok, hr.astype(int), rms


def cell_esd(r0, hr, ok, A, n_mc=200, seed=7):
    """Standard uncertainties of the cell (m, rad) by Monte-Carlo propagation of the least-squares covariance of B."""
    H = hr[ok].astype(float)
    R = r0[ok]
    if len(H) < 8:
        return [None] * 6
    Bt = np.linalg.lstsq(H, R, rcond=None)[0]
    res = R - H @ Bt
    s2 = float(np.sum(res ** 2) / max(1, 3 * len(H) - 9))
    cov = s2 * np.linalg.inv(H.T @ H)
    Lc = np.linalg.cholesky(cov + 1e-30 * np.eye(3))
    rng = _rng(seed)
    ps = []
    for _ in range(n_mc):
        Bs = Bt + Lc @ rng.standard_normal((3, 3))
        ps.append(cell_params(np.linalg.inv(Bs)))
    return list(np.std(np.array(ps), 0))


def niggli(A, eps_rel=1e-5, max_iter=200):
    """Niggli-reduced basis (Krivy & Gruber 1976, applied to the basis vectors): returns (A_red, M) with A_red = A @ M, det M = +1."""
    A = np.array(A, float)
    Mt = np.eye(3)
    for _ in range(max_iter):
        G = A.T @ A
        a, b, c = G[0, 0], G[1, 1], G[2, 2]
        xi, eta, zeta = 2 * G[1, 2], 2 * G[0, 2], 2 * G[0, 1]
        eps = eps_rel * (a + b + c) / 3
        M = None
        if a > b + eps or (abs(a - b) <= eps and abs(xi) > abs(eta) + eps):
            M = np.array([[0, -1, 0], [-1, 0, 0], [0, 0, -1]], float)
        elif b > c + eps or (abs(b - c) <= eps and abs(eta) > abs(zeta) + eps):
            M = np.array([[-1, 0, 0], [0, 0, -1], [0, -1, 0]], float)
        if M is not None:
            A, Mt = A @ M, Mt @ M
            continue
        want_pos = xi * eta * zeta > 0 if min(abs(xi), abs(eta), abs(zeta)) > eps else False
        vals = (xi, eta, zeta)
        best = None
        for sa, sb, sc in itertools.product((1, -1), repeat=3):
            nv = (sb * sc * xi, sa * sc * eta, sa * sb * zeta)
            good = all((x > -eps) if want_pos else (x < eps) for x in nv)
            if good and (best is None or sa * sb * sc > 0):
                best = (sa, sb, sc)
                if sa * sb * sc > 0:
                    break
        if best is not None and best != (1, 1, 1):
            D = np.diag(best).astype(float)
            if np.linalg.det(D) < 0:
                D = -D
            A, Mt = A @ D, Mt @ D
            G = A.T @ A
            a, b, c = G[0, 0], G[1, 1], G[2, 2]
            xi, eta, zeta = 2 * G[1, 2], 2 * G[0, 2], 2 * G[0, 1]
        sgn = lambda x: 1.0 if x > 0 else -1.0
        if abs(xi) > b + eps or (abs(xi - b) <= eps and 2 * eta < zeta - eps) or (abs(xi + b) <= eps and zeta < -eps):
            M = np.array([[1, 0, 0], [0, 1, -sgn(xi)], [0, 0, 1]], float)
        elif abs(eta) > a + eps or (abs(eta - a) <= eps and 2 * xi < zeta - eps) or (abs(eta + a) <= eps and zeta < -eps):
            M = np.array([[1, 0, -sgn(eta)], [0, 1, 0], [0, 0, 1]], float)
        elif abs(zeta) > a + eps or (abs(zeta - a) <= eps and 2 * xi < eta - eps) or (abs(zeta + a) <= eps and eta < -eps):
            M = np.array([[1, -sgn(zeta), 0], [0, 1, 0], [0, 0, 1]], float)
        elif xi + eta + zeta + a + b < -eps or (abs(xi + eta + zeta + a + b) <= eps and 2 * (a + eta) + zeta > eps):
            M = np.array([[1, 0, 1], [0, 1, 1], [0, 0, 1]], float)
        if M is None:
            break
        A, Mt = A @ M, Mt @ M
    return A, np.round(Mt).astype(int)


@functools.lru_cache(maxsize=1)
def _unimodular():
    m = np.array(list(itertools.product((-1, 0, 1), repeat=9)), float).reshape(-1, 3, 3)
    d = np.round(np.linalg.det(m))
    return m[np.abs(d) == 1].astype(int)


def _params_from_metric(G):
    L = np.sqrt(np.clip(np.stack([G[..., 0, 0], G[..., 1, 1], G[..., 2, 2]], -1), 1e-60, None))
    al = np.arccos(np.clip(G[..., 1, 2] / (L[..., 1] * L[..., 2]), -1, 1))
    be = np.arccos(np.clip(G[..., 0, 2] / (L[..., 0] * L[..., 2]), -1, 1))
    ga = np.arccos(np.clip(G[..., 0, 1] / (L[..., 0] * L[..., 1]), -1, 1))
    return L, np.stack([al, be, ga], -1)


def metric_ops(A, tol_len=0.015, tol_ang=1.5 * DEG):
    """Point operations of the lattice (holohedry) as integer matrices M in the basis of A: M^T G M = G within the tolerance."""
    G = A.T @ A
    Ms = _unimodular()
    Gp = np.einsum('nji,jk,nkl->nil', Ms, G, Ms)
    L0, a0 = _params_from_metric(G)
    L1, a1 = _params_from_metric(Gp)
    ok = np.all(np.abs(L1 / L0 - 1) <= tol_len, 1) & np.all(np.abs(a1 - a0) <= tol_ang, 1)
    ops = [M for M in Ms[ok]]
    return _closure(ops)


def _closure(ops, cap=48):
    keys = {m.tobytes(): m for m in ops}
    keys.setdefault(np.eye(3, dtype=int).tobytes(), np.eye(3, dtype=int))
    changed = True
    while changed and len(keys) <= cap:
        changed = False
        for a in list(keys.values()):
            for b in list(keys.values()):
                c = a @ b
                if c.tobytes() not in keys and np.max(np.abs(c)) <= 3:
                    keys[c.tobytes()] = c
                    changed = True
    return list(keys.values())


def rotation_order(M):
    R = M if round(np.linalg.det(M)) > 0 else -M
    return {3: 1, -1: 2, 0: 3, 1: 4, 2: 6}.get(int(round(np.trace(R))), 0), R


def _axis_of(R):
    """Primitive integer vector u (basis coordinates) with R u = u."""
    w, v = np.linalg.eig(R.astype(float))
    u = np.real(v[:, np.argmin(np.abs(w - 1))])
    u = u / np.max(np.abs(u))
    for k in range(1, 7):
        cand = np.round(u * k)
        if np.max(np.abs(cand - u * k)) < 1e-3 and np.any(cand):
            g = math.gcd(math.gcd(int(abs(cand[0])), int(abs(cand[1]))), int(abs(cand[2])))
            return (cand / max(g, 1)).astype(int)
    return np.round(u * 6).astype(int)


def _small_vectors(A, n=3):
    g = np.array(list(itertools.product(range(-n, n + 1), repeat=3)))
    g = g[np.any(g != 0, 1)]
    v = g @ A.T
    order = np.argsort(np.linalg.norm(v, axis=1))
    return g[order], v[order]


def crystal_system_of(ops):
    axes = {}
    for M in ops:
        n, R = rotation_order(M)
        if n <= 1:
            continue
        u = _axis_of(R)
        key = tuple(u) if tuple(u) > tuple(-u) else tuple(-u)
        axes[key] = max(axes.get(key, 0), n)
    n3 = sum(1 for v in axes.values() if v in (3, 6))
    if n3 >= 4:
        return 'cubic', axes
    if any(v in (3, 6) for v in axes.values()):
        return 'hexagonal', axes
    if any(v == 4 for v in axes.values()):
        return 'tetragonal', axes
    n2 = sum(1 for v in axes.values() if v == 2)
    if n2 >= 3:
        return 'orthorhombic', axes
    if n2 >= 1:
        return 'monoclinic', axes
    return 'triclinic', axes


def _perp_vectors(A, axis_vec):
    """Small lattice vectors perpendicular to an axis, shortest first: strictly perpendicular (2e-3) when there are at least two non-parallel ones,
    else within the metric tolerance of the lattice operations (0.03; pseudo-symmetric axes are only perpendicular within it)."""
    g, v = _small_vectors(A, 3)
    ad = axis_vec / np.linalg.norm(axis_vec)
    c = np.abs(v @ ad) / np.linalg.norm(v, axis=1)
    for tol in (2e-3, 0.03):
        ok = c < tol
        vv = v[ok]
        if len(vv) >= 2 and any(np.linalg.norm(np.cross(x, vv[0])) > 0.1 * np.linalg.norm(x) * np.linalg.norm(vv[0]) for x in vv[1:]):
            break
    return g[ok], v[ok]


def centring_vectors_of(P):
    Pi = np.linalg.inv(P)
    n = int(round(abs(np.linalg.det(P))))
    pts = set()
    for c in itertools.product(range(-2, 3), repeat=3):
        f = np.mod(Pi @ np.array(c, float), 1.0)
        f = np.where(np.isclose(f, 1.0, atol=1e-6), 0.0, f)
        pts.add(tuple(np.round(f, 4)))
    pts.discard((0.0, 0.0, 0.0))
    return sorted(pts)[:max(0, n - 1)] if n > 1 else []


def classify_centring(vecs):
    s = {tuple(np.round(v, 3)) for v in vecs}
    if not s:
        return 'P'
    if len(s) == 3 and {(0.0, 0.5, 0.5), (0.5, 0.0, 0.5), (0.5, 0.5, 0.0)} <= s:
        return 'F'
    if len(s) == 2 and {(0.6667, 0.3333, 0.3333), (0.3333, 0.6667, 0.6667)} <= {tuple(np.round(v, 4)) for v in vecs}:
        return 'R'
    if len(s) == 2 and {(0.3333, 0.6667, 0.3333), (0.6667, 0.3333, 0.6667)} <= {tuple(np.round(v, 4)) for v in vecs}:
        return 'R_reverse'
    if len(s) == 1:
        v = next(iter(s))
        return {(0.5, 0.5, 0.0): 'C', (0.0, 0.5, 0.5): 'A', (0.5, 0.0, 0.5): 'B', (0.5, 0.5, 0.5): 'I'}.get(v, '?')
    return '?'


class ConventionalCellError(ValueError):
    """The lattice operations could not be turned into a conventional cell (axes not perpendicular within the metric tolerance)."""


def _pick_perpendicular(A_red, cand, n=3, strict=2e-3, loose=0.03):
    """n mutually perpendicular lattice directions from cand (shortest first): strictly perpendicular when possible, else within the metric
    tolerance of the lattice operations (0.03 ~ 1.7 deg, weak-data cells are only that accurate)."""
    cand = sorted(cand, key=lambda u: np.linalg.norm(A_red @ u))
    pick = []
    for tol in (strict, loose):
        pick = []
        for u in cand:
            if all(abs((A_red @ u) @ (A_red @ w)) < tol * np.linalg.norm(A_red @ u) * np.linalg.norm(A_red @ w) for w in pick):
                pick.append(u)
            if len(pick) == n:
                return pick
    return pick


def conventional_cell(A_red, ops):
    """Conventional basis A_conv = A_red P (P integer) of the lattice with the point operations ops (in the reduced basis), its crystal system and
    centring (monoclinic in the b-unique C setting with beta >= 90 deg; hexagonal lattices in hexagonal axes, rhombohedral obverse).  Raises
    ConventionalCellError when the axes cannot be chosen (the callers then fall back to the triclinic cell)."""
    system, axes = crystal_system_of(ops)
    I = np.eye(3, dtype=int)
    if system == 'triclinic':
        return A_red.copy(), I, system, 'P'
    by_order = {}
    for u, n in axes.items():
        by_order.setdefault(n, []).append(np.array(u, int))

    def shortest(u):
        u = np.array(u, int)
        g = math.gcd(math.gcd(abs(int(u[0])), abs(int(u[1]))), abs(int(u[2])))
        return u // max(g, 1)
    if system == 'cubic':
        pick = _pick_perpendicular(A_red, [shortest(u) for u in (by_order.get(4) or by_order.get(2))], strict=1e-3)
        if len(pick) < 3:
            raise ConventionalCellError('cubic axes')
        P = np.column_stack(pick)
    elif system == 'orthorhombic':
        pick = _pick_perpendicular(A_red, [shortest(u) for u in by_order[2]])
        if len(pick) < 3:
            raise ConventionalCellError('orthorhombic axes')
        P = np.column_stack(sorted(pick, key=lambda u: np.linalg.norm(A_red @ u)))
    elif system == 'tetragonal':
        c4 = shortest(by_order[4][0])
        g, v = _perp_vectors(A_red, A_red @ c4)
        if not len(g):
            raise ConventionalCellError('tetragonal plane')
        a = g[0]
        M4 = next(M for M in ops if rotation_order(M)[0] == 4 and np.array_equal(_axis_of(rotation_order(M)[1]), c4) or
                  rotation_order(M)[0] == 4 and np.array_equal(_axis_of(rotation_order(M)[1]), -c4))
        R4 = rotation_order(M4)[1]
        b = R4 @ a
        P = np.column_stack([a, b, c4])
    elif system == 'hexagonal':
        c6 = shortest((by_order.get(6) or by_order.get(3))[0])
        g, v = _perp_vectors(A_red, A_red @ c6)
        if not len(g):
            raise ConventionalCellError('hexagonal plane')
        a = g[0]
        M3 = next(rotation_order(M)[1] for M in ops if rotation_order(M)[0] in (3, 6) and
                  (np.array_equal(_axis_of(rotation_order(M)[1]), c6) or np.array_equal(_axis_of(rotation_order(M)[1]), -c6)))
        if rotation_order(M3)[0] == 6:
            M3 = M3 @ M3
        b = M3 @ a
        va, vb = A_red @ a, A_red @ b
        if va @ vb > 0:                                                   # gamma must be 120 deg
            b = M3 @ b
        P = np.column_stack([a, b, c6])
    else:                                                                 # monoclinic: b along the 2-fold axis, a and c the reduced plane
        u2 = shortest(by_order[2][0])
        g, v = _perp_vectors(A_red, A_red @ u2)
        a = g[0]
        c = next((gg for gg, vv in zip(g[1:], v[1:]) if np.linalg.norm(np.cross(vv, v[0])) > 0.1 * np.linalg.norm(vv) * np.linalg.norm(v[0])), None)
        if c is None:                                                     # no second in-plane vector among the small ones
            raise ConventionalCellError('monoclinic plane')
        for _ in range(20):                                               # 2D Gauss reduction of (a, c)
            va, vc = A_red @ a, A_red @ c
            if np.linalg.norm(vc) < np.linalg.norm(va):
                a, c = c, a
                continue
            m_ = int(round(float(va @ vc) / float(va @ va)))
            if m_ == 0:
                break
            c = c - m_ * a
        P = np.column_stack([a, u2, c])
        va, vc = A_red @ a, A_red @ c
        if va @ vc > 0:                                                   # beta obtuse
            P[:, 2] = -P[:, 2]
    if P.shape != (3, 3) or round(abs(np.linalg.det(P))) == 0:
        raise ConventionalCellError(system + ' basis')
    if round(np.linalg.det(P)) < 0:
        P[:, 1 if system == 'monoclinic' else 2] *= -1
    cen = classify_centring(centring_vectors_of(P))
    if system == 'monoclinic' and cen in ('A', 'I'):                      # to the standard C setting
        T = np.array([[0, 0, 1], [0, -1, 0], [1, 0, 0]]) if cen == 'A' else np.array([[1, 0, 0], [0, 1, 0], [1, 0, 1]]).T
        P = P @ T
        if round(np.linalg.det(P)) < 0:
            P[:, 1] *= -1
        va, vc = A_red @ P[:, 0], A_red @ P[:, 2]
        if va @ vc > 0:
            P[:, 2] = -P[:, 2]
            P[:, 1] = -P[:, 1]
        cen = classify_centring(centring_vectors_of(P))
    if system == 'hexagonal' and cen == 'R_reverse':
        P = P @ np.diag([-1, -1, 1])
        cen = classify_centring(centring_vectors_of(P))
    return A_red @ P, P.astype(int), system, cen


def canonical_keys(H, ops_T):
    """Canonical key of each reflection under the Laue group (ops_T: matrices acting on the indices, h' = W h; Friedel included)."""
    imgs = np.einsum('gij,nj->ngi', np.concatenate([ops_T, -ops_T]), H)
    k = (imgs[..., 0] + 512) * 1048576 + (imgs[..., 1] + 512) * 1024 + (imgs[..., 2] + 512)
    return k.max(1)


LAUE_MIN_PAIRS = 3                                    # a lattice rotation counts as tested from this many reflection pairs
LAUE_ELEM_MAX, LAUE_ELEM_REL = 0.25, 7.0              # a tested rotation with pair R > max(0.25, 7 R_int(P-1)) is excluded outright (every tested true rotation of the calibration passes; their q99 is 6.7)
LAUE_ELEM_CAP, LAUE_ELEM_NOISE_K = 0.6, 1.8           # noise-dominated data (R_int(P-1) > 0.086): the bound is capped at max(0.6, 1.8 x the rotation's own
                                                      # counting-noise R); laue_threshold_calibration.json weak_data (133 datasets): true q99 1.45 / max 1.67 x noise,
                                                      # false min 1.88 / q01 2.18 x noise
LAUE_SUB_FACTOR, LAUE_SUB_ADD = 1.33, 0.01            # a group must not merge worse than 1.33 x an accepted index-2 subgroup + 0.01 (m-3m/m-3, 6/mmm/-3m,
                                                      # 4/mmm/4/m, mmm/2/m); laue_threshold_calibration.json (356 simulated datasets, evaluator oracle): 99.1 % of true, 0.7 % of false groups pass
LAUE_GROUP_FACTOR, LAUE_GROUP_ADD = 2.5, 0.03         # accepted subgroup: R_int(G) <= 2.5 R_int(P-1) + 0.03 (residual absorption, scaling)
LAUE_NOISE_FACTOR, LAUE_NOISE_ADD = 2.0, 0.04         # without enough repeats for R_int(P-1): R_int(G) <= 2 R_sigma + 0.04
ABS_DEG = 4                                           # degree of the empirical absorption surface


def _r_int(Hr, I, sig, ops):
    """R_int of the observations merged in the group of the basis operations ops (Friedel included), or None with too few multiples."""
    W = np.array([np.round(np.linalg.inv(M).T).astype(int) for M in ops])
    keys = canonical_keys(np.asarray(Hr, int), W)
    _, inv = np.unique(keys, return_inverse=True)
    w = 1.0 / np.maximum(sig, 1e-12) ** 2
    mean = np.bincount(inv, w * I) / np.bincount(inv, w)
    multi = np.bincount(inv)[inv] >= 2
    return float(np.sum(np.abs(I[multi] - mean[inv[multi]])) / np.sum(np.abs(I[multi]))) if multi.sum() >= 10 else None


def laue_test(Hr, I, sig, ops):
    """Laue class of the observations among the subgroups of the lattice point group (ops: basis operations of the reduced cell).

    Every lattice rotation is scored by the reflection pairs it relates (P-1 means with I > 2 sigma: pairs, pair R, counting-noise R). A candidate
    subgroup (every subgroup generated by one or two lattice rotations, plus the inversion) is eligible when none of its tested rotations
    (>= LAUE_MIN_PAIRS pairs) disagrees (pair R > max(LAUE_ELEM_MAX, min(LAUE_ELEM_REL R_int(P-1), max(LAUE_ELEM_CAP, LAUE_ELEM_NOISE_K noise))))
    and it is generated by its tested, agreeing rotations;
    it is accepted when the R_int of the observations merged in it stays near the P-1 level (<= LAUE_GROUP_FACTOR R_int(P-1) + LAUE_GROUP_ADD, or
    LAUE_NOISE_FACTOR R_sigma + LAUE_NOISE_ADD when the data have too few repeated measurements for R_int(P-1)) and not much above that of an accepted
    index-2 subgroup (<= LAUE_SUB_FACTOR R_int(H) + LAUE_SUB_ADD).  The highest-order accepted subgroup wins (ties: the lowest R_int), as XPREP /
    CrysAlisPro compare R_int of the candidate Laue classes.  Rotations without enough pairs are never assumed on their own.  Returns the group
    (basis operations, closed) and a report."""
    Hr = np.asarray(Hr, int)
    E = np.eye(3, dtype=int)
    fk = canonical_keys(Hr, np.array([E]))
    uniq, inv = np.unique(fk, return_inverse=True)
    w = 1.0 / np.maximum(sig, 1.0) ** 2
    Im = np.bincount(inv, w * I) / np.bincount(inv, w)
    Sm = 1.0 / np.sqrt(np.bincount(inv, w))
    first = np.zeros(len(uniq), int)
    first[inv[::-1]] = np.arange(len(inv))[::-1]
    Hu = Hr[first]
    lookup = dict(zip(uniq.tolist(), range(len(uniq))))
    cnt = np.bincount(inv)
    multi = cnt[inv] >= 2
    r_ref = float(np.sum(np.abs(I[multi] - Im[inv[multi]])) / np.sum(np.abs(I[multi]))) if multi.sum() >= 10 else None
    pos = Im > 0
    r_sigma = float(np.sum(Sm[pos]) / np.sum(Im[pos])) if pos.any() else 1.0
    key = lambda M: np.round(M).astype(int).tobytes()                                         # noqa: E731
    lat = {key(M): np.round(M).astype(int) for M in ops}
    stats, report = {}, []
    for k, M in lat.items():                                             # pair statistics of every proper lattice rotation (M and -M share them)
        if round(np.linalg.det(M)) <= 0 or np.array_equal(M, E):
            continue
        W = np.round(np.linalg.inv(M).T).astype(int)
        hk = canonical_keys((Hu @ W.T), np.array([E]))
        j = np.array([lookup.get(int(x), -1) for x in hk])
        m = (j >= 0) & (j != np.arange(len(Hu))) & (Im > 2 * Sm)
        n = int(m.sum())
        if n == 0:
            stats[k] = dict(pairs=0, r=None, noise=None)
            continue
        pa, pb = Im[m], Im[j[m]]
        stats[k] = dict(pairs=n, r=float(np.sum(np.abs(pa - pb)) / np.sum((pa + pb) / 2)),
                        noise=float(np.sum(np.sqrt(Sm[m] ** 2 + Sm[j[m]] ** 2)) * 0.8 / np.sum((pa + pb) / 2)))
    props = [lat[k] for k in stats]
    subgroups = {frozenset([key(E)]): [E]}
    for i in range(len(props)):                                          # every subgroup of a finite rotation group is generated by <= 2 elements
        for jj in range(i, len(props)):
            G = _closure([props[i], props[jj]])
            ks = frozenset(key(g) for g in G)
            if ks <= set(lat) | {key(E)} and ks not in subgroups:
                subgroups[ks] = G
    r_p1 = _r_int(Hr, I, sig, [E])
    limit = LAUE_GROUP_FACTOR * r_p1 + LAUE_GROUP_ADD if r_p1 is not None else LAUE_NOISE_FACTOR * r_sigma + LAUE_NOISE_ADD
    elem_max = max(LAUE_ELEM_MAX, min(LAUE_ELEM_REL * r_p1, LAUE_ELEM_CAP)) if r_p1 is not None else LAUE_ELEM_MAX
    for st in stats.values():                                            # per rotation: max(0.25, min(7 R_int(P-1), max(0.6, 1.8 noise)))
        st['bound'] = (max(LAUE_ELEM_MAX, min(LAUE_ELEM_REL * r_p1, max(LAUE_ELEM_CAP, LAUE_ELEM_NOISE_K * (st['noise'] or 0.0))))
                       if r_p1 is not None else LAUE_ELEM_MAX)
    tested_ok = {k for k, st in stats.items() if st['pairs'] >= LAUE_MIN_PAIRS and st['r'] <= st['bound']}
    tested_bad = {k for k, st in stats.items() if st['pairs'] >= LAUE_MIN_PAIRS and st['r'] > st['bound']}
    rows = {}
    for ks, G in subgroups.items():
        rots = [g for g in G if not np.array_equal(g, E)]
        if not rots:
            continue
        # eligible: no tested rotation of the group disagrees and the group is generated by its tested, agreeing rotations (untested rotations only
        # when they are products of tested ones - never assumed on their own)
        gen = [g for g in rots if key(g) in tested_ok]
        eligible = not (ks & tested_bad) and bool(gen) and frozenset(key(g) for g in _closure(gen)) == ks
        L = G + [-g for g in G]
        rg = _r_int(Hr, I, sig, L) if eligible else None
        rows[ks] = dict(candidate_order=len(L), eligible=bool(eligible), r_int=rg, limit=limit, accepted=bool(eligible and rg is not None and rg <= limit),
                        min_pairs=min(stats[key(g)]['pairs'] for g in rots), max_r=max((stats[key(g)]['r'] for g in rots if stats[key(g)]['r'] is not None), default=None),
                        group=L)
    for ks, row in rows.items():                                         # not much worse than an accepted index-2 subgroup
        if not row['accepted']:
            continue
        for ks2, row2 in rows.items():
            if row2['accepted'] and ks2 < ks and len(ks) == 2 * len(ks2) and row['r_int'] > LAUE_SUB_FACTOR * row2['r_int'] + LAUE_SUB_ADD:
                row.update(accepted=False, rejected_against_subgroup=row2['candidate_order'])
                break
    best = ([E, -E], r_p1 if r_p1 is not None else 0.0)
    for row in rows.values():
        if row['accepted'] and (row['candidate_order'] > len(best[0]) or (row['candidate_order'] == len(best[0]) and row['r_int'] < best[1])):
            best = (row['group'], row['r_int'])
    cand_rows = [{k: v for k, v in row.items() if k != 'group'} for row in rows.values()]
    group = best[0]
    final = {key(g) for g in group}
    for k, st in stats.items():
        report.append(dict(pairs=st['pairs'], r=st['r'], noise=st['noise'], bound=st['bound'], friedel_r=r_ref, accepted=k in final))
    applied = [st['bound'] for st in stats.values() if st['pairs'] >= LAUE_MIN_PAIRS and st['r'] is not None]
    report.append(dict(group_check='search', r=None, r_int_p1=r_p1, r_sigma=r_sigma, limit=limit, element_bound_base=elem_max,
                       element_max=max(applied + [elem_max]), candidates=len(cand_rows), chosen_order=len(group),
                       chosen_r_int=best[1], accepted=True, rows=sorted(cand_rows, key=lambda x: -x['candidate_order'])[:40]))
    return group, report


def e2_statistic(I, d):
    """Mean |E^2 - 1| (0.968 centric, 0.736 acentric) from intensities normalised in resolution shells."""
    I = np.maximum(np.asarray(I, float), 0.0)
    if len(I) < 20:
        return None
    s2 = 1.0 / np.asarray(d) ** 2
    edges = np.quantile(s2, np.linspace(0, 1, 9))
    sh = np.clip(np.searchsorted(edges, s2, side='right') - 1, 0, 7)
    mean = np.array([I[sh == k].mean() if (sh == k).any() else 1.0 for k in range(8)])
    E2 = I / np.maximum(mean[sh], 1e-30)
    return float(np.mean(np.abs(E2 - 1)))


# ---------------------------------------------------------------- space groups, database search, refinement
def _family(number):
    return ('triclinic' if number <= 2 else 'monoclinic' if number <= 15 else 'orthorhombic' if number <= 74 else 'tetragonal' if number <= 142
            else 'hexagonal' if number <= 194 else 'cubic')


@functools.lru_cache(maxsize=1)
def space_group_candidates():
    """Candidate space groups (in the settings of the structure library, plus P1): symbol, number, symmetry operations, lattice letter."""
    seen, out = set(), [dict(symbol='P 1', number=1, ops=[(np.eye(3), np.zeros(3))], letter='P', centric=False, freq=1)]
    freq = {}
    for e in crystal_lib().values():
        freq[e['space_group']] = freq.get(e['space_group'], 0) + 1
    for e in crystal_lib().values():
        sym = e['space_group']
        if sym in seen:
            continue
        seen.add(sym)
        letter = sym.split()[0][0]
        out.append(dict(symbol=sym, number=int(e['space_group_number']), ops=_symops(e), letter=letter, centric=is_centrosymmetric(e), freq=freq[sym]))
    return out


ORTHO_PERMS = [np.array(m) for m in ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[0, 1, 0], [0, 0, 1], [1, 0, 0]], [[0, 0, 1], [1, 0, 0], [0, 1, 0]],
                                      [[0, 1, 0], [1, 0, 0], [0, 0, -1]], [[1, 0, 0], [0, 0, 1], [0, -1, 0]], [[0, 0, 1], [0, -1, 0], [1, 0, 0]])]
MONO_SETTINGS = [np.eye(3, dtype=int), np.array([[0, 0, 1], [0, -1, 0], [1, 0, 0]])]


def absent_mask(Hc, ops):
    """Systematically absent reflections for the symmetry operations: h R = h with h . t not an integer."""
    ab = np.zeros(len(Hc), bool)
    for R, t in ops:
        inv = np.all(np.abs(Hc @ R - Hc) < 1e-6, axis=1)
        ph = Hc @ t
        ab |= inv & (np.abs(ph - np.round(ph)) > 1e-3)
    return ab


def laue_of_number(n):
    """Laue class of a space group number (the reduction's names; trigonal -3m1 / -31m both '-3m')."""
    for hi, name in ((2, '-1'), (15, '2/m'), (74, 'mmm'), (88, '4/m'), (142, '4/mmm'), (148, '-3'), (167, '-3m'), (176, '6/m'), (194, '6/mmm'), (206, 'm-3'), (230, 'm-3m')):
        if n <= hi:
            return name
    raise ValueError(n)


def choose_space_group(Hc, I, sig, d, system, centring, laue=None, cosets=None):
    """Space group from the systematic absences of the merged data (conventional setting): candidates of the crystal family, the Laue class of
    the reduction and the lattice letter in each allowed axis setting; valid when at most 2 % of the predicted absences are observed (I / sigma
    > 4) and the predicted absences are weak on average (mean I / sigma < 3); the most specific valid group wins, ties broken by the |E^2 - 1|
    centrosymmetry test (>= 150 reflections) and otherwise by the frequency in the library - such ties are reported as ambiguous."""
    e2 = e2_statistic(I, d)
    centric_like = None if e2 is None or len(I) < 150 else e2 > 0.85            # intensity statistics need enough reflections
    settings = ORTHO_PERMS if system == 'orthorhombic' else MONO_SETTINGS if system == 'monoclinic' else [np.eye(3, dtype=int)]
    if cosets:                                                           # lattice operations outside the Laue group give inequivalent indexings
        settings = [np.asarray(T) @ np.asarray(W) for T in settings for W in cosets]
    rows = []
    isig = I / np.maximum(sig, 1e-9)
    for cand in space_group_candidates():
        if _family(cand['number']) != system or (laue is not None and laue_of_number(cand['number']) != laue):
            continue
        for T in settings:
            Hs = Hc @ np.asarray(T).T
            letter = centring if centring != 'R' else 'R'
            if system in ('orthorhombic', 'monoclinic') and centring in ('A', 'B', 'C', 'I', 'F'):
                cv = [np.mod(np.linalg.inv(np.asarray(T, float)).T @ np.array(v), 1.0) for v in _CENT[centring]]
                letter = classify_centring(cv)
            if letter != cand['letter']:
                continue
            ab = absent_mask(Hs.astype(float), cand['ops'])
            n_ab = int(ab.sum())
            viol = int(np.sum(ab & (isig > 4)))
            ok = viol <= 0.02 * n_ab and (n_ab == 0 or float(isig[ab].mean()) < 3.0)
            rows.append(dict(symbol=cand['symbol'], number=cand['number'], setting=np.asarray(T).tolist(), absent=n_ab, violations=viol, valid=ok,
                             centric=cand['centric'], freq=cand['freq'], mean_absent_i_sigma=float(isig[ab].mean()) if n_ab else None))
    valid, seen = [], set()
    for r in sorted([r for r in rows if r['valid']], key=lambda r: (-r['absent'], -(1 if centric_like is not None and r['centric'] == centric_like else 0), -r['freq'])):
        if r['symbol'] not in seen:
            seen.add(r['symbol'])
            valid.append(r)
    if valid and valid[0]['symbol'].endswith(':1'):                     # two origin choices, same absences: origin choice 2 (inversion centre at the origin, ITA / XPREP)
        alt = next((r for r in valid[1:] if r['number'] == valid[0]['number'] and r['absent'] == valid[0]['absent'] and r['symbol'].endswith(':2')), None)
        if alt is not None:
            valid.remove(alt)
            valid.insert(0, alt)
            valid[1]['origin_alternative'] = True                        # the same group described with the other origin
    for r in valid:                                                      # groups the absences and the E statistics cannot tell from the winner
        r['tie'] = bool(valid) and r['absent'] == valid[0]['absent'] and r['centric'] != valid[0]['centric'] and centric_like is None
    return valid, rows, e2


_CENT = {'P': [], 'C': [(0.5, 0.5, 0.0)], 'A': [(0.0, 0.5, 0.5)], 'B': [(0.5, 0.0, 0.5)], 'I': [(0.5, 0.5, 0.5)],
         'F': [(0.0, 0.5, 0.5), (0.5, 0.0, 0.5), (0.5, 0.5, 0.0)], 'R': [(2 / 3, 1 / 3, 1 / 3), (1 / 3, 2 / 3, 2 / 3)]}


def primitive_basis(entry):
    """Primitive basis of the lattice of a library structure: A_prim = A_conv Q (Q rational, from the centring translations)."""
    cv = centring_vectors(entry)
    if not cv:
        return np.eye(3)
    pts = []
    for n in itertools.product((-1, 0, 1), repeat=3):
        pts.append(np.array(n, float))
        for c in cv:
            pts.append(np.array(n, float) + c)
    A = substance_model(entry['substance_id'])['A']
    V = abs(np.linalg.det(A)) / (len(cv) + 1)
    pts = sorted([p for p in pts if np.linalg.norm(p) > 1e-9], key=lambda p: np.linalg.norm(A @ p))
    for a, b, c in itertools.combinations(range(min(len(pts), 30)), 3):
        Q = np.column_stack([pts[a], pts[b], pts[c]])
        if abs(abs(np.linalg.det(A @ Q)) - V) < 1e-3 * V:
            return Q if np.linalg.det(Q) > 0 else Q @ np.diag([1, 1, -1])
    raise RuntimeError('no primitive basis')


@functools.lru_cache(maxsize=1)
def database_cells():
    """Reduced primitive cells of the structure library ('What is this?' search database)."""
    out = {}
    for sid, e in crystal_lib().items():
        A = substance_model(sid)['A']
        Q = primitive_basis(e)
        Ar, N = niggli(A @ Q)
        out[sid] = dict(G=Ar.T @ Ar, QN=Q @ N, A_red=Ar)
    return out


def match_database(A_red, exclude=(), tol_len=0.025, tol_ang=2.0 * DEG):
    """Library structures whose reduced cell matches the measured reduced cell (lengths within 2.5 %, angles within 2 deg), best first."""
    G = A_red.T @ A_red
    Ms = _unimodular()
    Gp = np.einsum('nji,jk,nkl->nil', Ms, G, Ms)
    L1, a1 = _params_from_metric(Gp)
    out = []
    V = abs(np.linalg.det(A_red))
    for sid, dc in database_cells().items():
        if sid in exclude:
            continue
        Vl = abs(np.linalg.det(dc['A_red']))
        if abs(V / Vl - 1) > 0.06:
            continue
        L0, a0 = _params_from_metric(dc['G'])
        dl = np.max(np.abs(L1 / L0 - 1), 1)
        da = np.max(np.abs(a1 - a0), 1)
        ok = (dl <= tol_len) & (da <= tol_ang)
        if not ok.any():
            continue
        i = int(np.argmin(np.where(ok, dl + da, np.inf)))
        out.append(dict(substance_id=sid, M=Ms[i], length_dev=float(dl[i]), angle_dev_rad=float(da[i]), volume_ratio=float(V / Vl)))
    out.sort(key=lambda m: m['length_dev'] + m['angle_dev_rad'])
    return out


def refine_against(sid, Hl, Fo2, sig, d, target):
    """Refine the scale k and an overall displacement shift dB of the library model against the merged F^2 (library setting indices Hl):
    F^2_calc = k |F|^2 exp(-2 dB s^2) (Friedel average); R1 (F > 4 sigma(F)), wR2, GooF, parameters 2."""
    m = substance_model(sid)
    A = m['A']
    key = f'{target}_Ka1'
    F = structure_factors(sid, Hl, A, m['u'], key)
    Fm = structure_factors(sid, -Hl, A, m['u'], key)
    Fc2 = 0.5 * (np.abs(F) ** 2 + np.abs(Fm) ** 2)
    s2 = (0.5e-10 / np.asarray(d)) ** 2
    w = 1.0 / np.maximum(sig, 1e-9) ** 2                                  # first pass: statistical weights
    best = None
    for dB in np.linspace(-3.0, 3.0, 61):
        g = Fc2 * np.exp(-2 * dB * s2)
        k = float(np.sum(w * Fo2 * g) / np.sum(w * g * g))
        chi = float(np.sum(w * (Fo2 - k * g) ** 2))
        if best is None or chi < best[0]:
            best = (chi, k, dB)
    chi, k, dB = best
    for dB2 in np.linspace(dB - 0.1, dB + 0.1, 21):
        g = Fc2 * np.exp(-2 * dB2 * s2)
        k2 = float(np.sum(w * Fo2 * g) / np.sum(w * g * g))
        c2 = float(np.sum(w * (Fo2 - k2 * g) ** 2))
        if c2 < chi:
            chi, k, dB = c2, k2, dB2
    calc = k * Fc2 * np.exp(-2 * dB * s2)
    Pw = (np.maximum(Fo2, 0.0) + 2 * calc) / 3
    w = 1.0 / (np.maximum(sig, 1e-9) ** 2 + (0.04 * Pw) ** 2)            # SHELXL-type weights w = 1 / (sigma^2 + (0.04 P)^2)
    chi = float(np.sum(w * (Fo2 - calc) ** 2))
    Fo = np.sqrt(np.maximum(Fo2, 0.0))
    Fc = np.sqrt(np.maximum(calc, 0.0))
    strong = Fo2 > 2 * sig
    r1 = float(np.sum(np.abs(Fo[strong] - Fc[strong])) / np.sum(Fo[strong])) if strong.any() else None
    wr2 = float(math.sqrt(np.sum(w * (Fo2 - calc) ** 2) / np.sum(w * Fo2 ** 2)))
    goof = float(math.sqrt(chi / max(1, len(Fo2) - 2)))
    return dict(r1=r1, wr2=wr2, goof=goof, scale=k, delta_b_a2=float(dB), n_reflections=int(len(Fo2)), n_strong=int(strong.sum()), n_parameters=2,
                weighting='w = 1 / (sigma^2(Fo^2) + (0.04 P)^2), P = (Fo^2 + 2 Fc^2) / 3')


def _monomials(u, deg=4):
    """Monomials x^a y^b z^c (1 <= a + b + c <= deg) of unit vectors (N, 3): on the sphere they span the spherical harmonics up to degree deg."""
    cols = [u[:, 0] ** a * u[:, 1] ** b * u[:, 2] ** c for a in range(deg + 1) for b in range(deg + 1 - a) for c in range(deg + 1 - a - b) if a + b + c >= 1]
    return np.stack(cols, 1)


def absorption_correct(H, I, sig, dirs, block, deg=None, ridge=1e-3, iters=8, ops=None):
    """Empirical absorption correction (a spherical-harmonics transmission surface of the incident and diffracted directions in the crystal frame,
    as ABSPACK / SADABS model it), fitted in P-1 only - Friedel pairs and repeated measurements, valid before the Laue class is known:
    ln I = ln J(hkl) + s(block) + sum_k c_k m_k(u_in) + sum_k d_k m_k(u_out), alternating least squares on the I > 3 sigma observations of
    reflections measured at least twice; the direction term is divided out of every observation (block scales are refined later by merge()).
    Applied only when the data determine it (>= 8 observations per coefficient); returns I, sigma and a report."""
    deg = ABS_DEG if deg is None else deg
    W = np.array([np.eye(3, dtype=int)]) if ops is None else np.array([np.round(np.linalg.inv(M).T).astype(int) for M in ops])
    keys = canonical_keys(np.asarray(H, int), W)
    _, inv = np.unique(keys, return_inverse=True)
    cnt = np.bincount(inv)
    use = (I > 3 * sig) & (cnt[inv] >= 2)
    M = np.hstack([_monomials(dirs[:, :3], deg), _monomials(dirs[:, 3:], deg)])
    n_c = M.shape[1]
    report = dict(applied=False, model=f'monomials up to degree {deg} of the incident and diffracted directions (crystal frame), fitted in '
                                 + ('P-1' if ops is None else f'the provisional Laue group (order {len(ops)})'),
                  observations_used=int(use.sum()), coefficients=int(n_c))
    if use.sum() < 8 * n_c:
        report['reason'] = 'too few repeated strong observations'
        return I, sig, report
    y = np.log(I[use])
    w = np.minimum((I[use] / sig[use]) ** 2, 1e4)
    Mu = M[use]
    mean_col = np.average(Mu, axis=0, weights=w)
    Mc = Mu - mean_col
    iu, bu = inv[use], np.unique(block[use], return_inverse=True)[1]
    c = np.zeros(n_c)
    s_b = np.zeros(bu.max() + 1)
    reg = ridge * np.trace((Mc * w[:, None]).T @ Mc) / n_c
    r_before = None
    for it_ in range(iters):
        f = Mc @ c
        g = np.bincount(iu, w * (y - s_b[bu] - f), minlength=inv.max() + 1) / np.maximum(np.bincount(iu, w, minlength=inv.max() + 1), 1e-30)
        s_b = np.bincount(bu, w * (y - g[iu] - f)) / np.maximum(np.bincount(bu, w), 1e-30)
        s_b -= np.average(s_b[bu], weights=w)
        r = y - g[iu] - s_b[bu]
        if r_before is None:
            r_before = float(np.sqrt(np.average((r - f) ** 2, weights=w)))
        c = np.linalg.solve((Mc * w[:, None]).T @ Mc + reg * np.eye(n_c), (Mc * w[:, None]).T @ r)
    f_all = np.clip((M - mean_col) @ c, -math.log(30.0), math.log(30.0))
    r_after = float(np.sqrt(np.average((y - g[iu] - s_b[bu] - f_all[use]) ** 2, weights=w)))
    corr = np.exp(f_all)
    report.update(applied=True, correction_min=float(corr.min()), correction_max=float(corr.max()), rms_log_residual_before=r_before, rms_log_residual_after=r_after)
    return I / corr, sig / corr, report


def lattice_settings(A_red, laue):
    """One lattice operation (reduced basis, row-vector convention h' = h S) per coset of the data's Laue group: the indexings of the same lattice
    that the merged data cannot tell apart (e.g. the two settings of a 4/m crystal in a 4/mmm lattice); inversion-related ones are equivalent."""
    ops = [np.round(np.asarray(M)).astype(int) for M in metric_ops(A_red)]
    L = [np.round(np.asarray(M)).astype(int) for M in laue]
    reps, seen = [], set()
    for S in ops:
        key = min(tuple((sgn * (Lm @ S)).ravel()) for Lm in L for sgn in (1, -1))
        if key not in seen:
            seen.add(key)
            reps.append(S)
    return reps


def flack_estimate(sid, Hl, I, sig, target):
    """Flack parameter of a non-centrosymmetric model from Bijvoet pairs (Parsons, Flack & Wagner 2013 quotients): merge the scaled observations in
    the model point group, Q_obs = (I+ - I-) / (I+ + I-) against Q_calc of the model, Q_obs = (1 - 2x) Q_calc; x, su and the number of pairs.
    Centrosymmetric models: None."""
    e = crystal_lib()[sid]
    if is_centrosymmetric(e):
        return dict(flack_x=None, flack_su=None, bijvoet_pairs=0, flack_method='not applicable (centrosymmetric model)')
    rots = {tuple(np.round(R).astype(int).ravel()): np.round(R).astype(int) for R, t in _symops(e)}
    W = np.array(list(rots.values()))                                   # h' = h R for the point group (row vectors)
    Hl = np.asarray(Hl, int)
    imgs = np.einsum('nj,gjk->ngk', Hl, W)
    code = lambda a: (a[..., 0] + 512) * 1048576 + (a[..., 1] + 512) * 1024 + (a[..., 2] + 512)  # noqa: E731
    key = code(imgs).max(1)
    mate = code(-imgs).max(1)
    w = 1.0 / np.maximum(sig, 1e-12) ** 2
    uk, inv = np.unique(key, return_inverse=True)
    Im = np.bincount(inv, w * I) / np.bincount(inv, w)
    Sm = 1.0 / np.sqrt(np.bincount(inv, w))
    first = np.zeros(len(uk), int)
    first[inv[::-1]] = np.arange(len(inv))[::-1]
    pos = {k: i for i, k in enumerate(uk)}
    mate_u = mate[first]
    pairs = [(i, pos[mk]) for i, mk in enumerate(mate_u) if mk in pos and uk[i] > mk]      # each pair once; centric reflections (key == mate) excluded
    if len(pairs) < 10:
        return dict(flack_x=None, flack_su=None, bijvoet_pairs=len(pairs), flack_method='too few Bijvoet pairs')
    a, b = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
    m = substance_model(sid)
    Hp = Hl[first[a]]
    Fp = np.abs(structure_factors(sid, Hp, m['A'], m['u'], f'{target}_Ka1')) ** 2
    Fm = np.abs(structure_factors(sid, -Hp, m['A'], m['u'], f'{target}_Ka1')) ** 2
    Ip, Imn, sp, sm = Im[a], Im[b], Sm[a], Sm[b]
    ok = (Ip + Imn > 0) & (Fp + Fm > 0) & (Ip > 0) & (Imn > 0)
    if ok.sum() < 10:
        return dict(flack_x=None, flack_su=None, bijvoet_pairs=int(ok.sum()), flack_method='too few Bijvoet pairs')
    Qo = (Ip - Imn)[ok] / (Ip + Imn)[ok]
    sQ = 2.0 * np.sqrt((Imn[ok] * sp[ok]) ** 2 + (Ip[ok] * sm[ok]) ** 2) / (Ip + Imn)[ok] ** 2
    Qc = (Fp - Fm)[ok] / (Fp + Fm)[ok]
    wq = 1.0 / np.maximum(sQ, 1e-9) ** 2
    den = float(np.sum(wq * Qc * Qc))
    if den <= 0:
        return dict(flack_x=None, flack_su=None, bijvoet_pairs=int(ok.sum()), flack_method='no anomalous signal in the model')
    slope = float(np.sum(wq * Qo * Qc) / den)
    chi = float(np.sum(wq * (Qo - slope * Qc) ** 2) / max(1, ok.sum() - 1))
    su = math.sqrt(max(chi, 1.0) / den) / 2.0
    return dict(flack_x=(1.0 - slope) / 2.0, flack_su=su, bijvoet_pairs=int(ok.sum()), flack_method='Parsons quotients of the Bijvoet pairs (Q_obs = (1 - 2x) Q_calc)')


def merge(Hc, I, sig, ops_c, block):
    """Scale (per run and 10 deg block) and merge the observations in the Laue group (ops_c act on the conventional indices): merged indices,
    mean intensities, their sigmas, multiplicities and R_int, R_sigma."""
    keys = canonical_keys(Hc, np.array(ops_c))
    uniq, inv = np.unique(keys, return_inverse=True)
    scale = np.ones(block.max() + 1)
    w = 1.0 / np.maximum(sig, 1.0) ** 2
    strong = I > 3 * sig                                                  # scale factors from the strong reflections only
    for _ in range(6):
        Ic = I / scale[block]
        wc = w * scale[block] ** 2
        mean = np.bincount(inv, wc * Ic) / np.maximum(np.bincount(inv, wc), 1e-30)
        num = np.bincount(block, np.where(strong, w * I * mean[inv], 0.0), minlength=len(scale))
        den = np.bincount(block, np.where(strong, w * mean[inv] ** 2, 0.0), minlength=len(scale))
        scale = np.clip(np.where(den > 0, num / np.maximum(den, 1e-30), 1.0), 0.2, 5.0)
        scale /= np.exp(np.mean(np.log(scale[den > 0]))) if (den > 0).any() else 1.0
    Ic = I / scale[block]
    sc = sig / scale[block]
    wc = 1.0 / np.maximum(sc, 1e-9) ** 2
    n = np.bincount(inv)
    mean = np.bincount(inv, wc * Ic) / np.bincount(inv, wc)
    smean = 1.0 / np.sqrt(np.bincount(inv, wc))
    dev = np.abs(Ic - mean[inv])
    multi = n[inv] >= 2
    r_int = float(np.sum(dev[multi]) / np.sum(np.abs(Ic[multi]))) if multi.any() else None
    r_sig = float(np.sum(smean) / np.sum(np.abs(mean))) if len(mean) else None
    first = np.zeros(len(uniq), int)
    first[inv[::-1]] = np.arange(len(inv))[::-1]
    return dict(keys=uniq, H=Hc[first], I=mean, sigma=smean, n=n, r_int=r_int, r_sigma=r_sig, scales=scale.tolist(), inv=inv, I_obs=Ic, sigma_obs=sc)


def expected_unique(A_conv, P, ops_c, d_min):
    """Number of symmetry-unique reflections of the lattice to d_min (conventional setting)."""
    A_red = A_conv @ np.linalg.inv(P)
    Hr, _ = hkl_sphere(A_red, d_min, limit=2_000_000)
    Hc = Hr @ P
    return len(np.unique(canonical_keys(Hc, np.array(ops_c))))


def free_omega_interval(kappa, theta, dist, step=5 * DEG):
    """Longest collision-free omega interval inside the joint range (sampled every 5 deg, 5 deg kept free at both ends), None when shorter than 20 deg."""
    om = np.arange(-175, 176, 5) * DEG
    hit, _ = collisions(om, np.full(len(om), kappa), np.full(len(om), theta), np.full(len(om), dist))
    best, cur, start = (0, 0), 0, 0
    for i, v in enumerate(~hit):
        if v:
            if cur == 0:
                start = i
            cur += 1
            if cur > best[0]:
                best = (cur, start)
        else:
            cur = 0
    n = best[0]
    if n < 5:
        return None
    a = om[best[1]] + step
    b = om[best[1] + n - 1] - step
    if b - a < 20 * DEG:
        return None
    if b - a > 180 * DEG:
        b = a + 180 * DEG
    return a, b


def run_coverage(r0, lam, run, sig_m=None):
    """Indices (into r0) of the reflections that cross the Ewald sphere inside the omega range of the run and land on the detector; with the
    angular width sig_m (mosaic + divergence, rad) only those recorded fully: the crossing at least 2 L sig_m from both ends of the scan (the
    reduction rejects partials)."""
    KP = rot(KAPPA_AXIS, run['kappa']) @ rot_z(run['phi'])
    v = r0 @ KP.T
    q2 = np.einsum('ij,ij->i', v, v)
    rho_ = np.hypot(v[:, 0], v[:, 1])
    c = -lam * q2 / 2
    ok = rho_ > np.abs(c) + 1e-3
    psi = np.arctan2(v[:, 1], v[:, 0])
    acs = np.arccos(np.clip(c / np.where(ok, rho_, 1.0), -1, 1))
    lo, hi = min(run['omega_start'], run['omega_end']), max(run['omega_start'], run['omega_end'])
    out = []
    for sol in (1.0, -1.0):
        w = -psi + sol * acs
        s = np.mod(w - lo, 2 * PI)
        sel = np.nonzero(ok & (s <= hi - lo))[0]
        if not len(sel):
            continue
        Rw = rot_z_batch(lo + s[sel])
        r_lab = np.einsum('nij,nj->ni', Rw, v[sel])
        k1 = r_lab + np.array([1.0 / lam, 0, 0])
        s1 = k1 / np.linalg.norm(k1, axis=1, keepdims=True)
        x, y, t, cos_a, on = project(s1, run['theta'], run['distance'])
        on &= ~shadowed(s1) & (np.abs(s1[:, 1]) > 0.02)
        if sig_m is not None:
            wv = 2.0 * sig_m / np.maximum(np.abs(s1[:, 1]), 1e-3)            # 2 sigma of the rocking curve (L sig_m)
            on &= (s[sel] >= wv) & ((hi - lo) - s[sel] >= wv)
        out.append(sel[on])
    return np.unique(np.concatenate(out)) if out else np.zeros(0, int)


def plan_strategy(A0, ops_idx, lam, d_target, completeness, redundancy, exp_low, exp_high, fw, dist, max_runs=MAX_RUNS, sig_m=None):
    """Greedy omega-scan strategy from the estimated orientation (A0: direct basis in the goniometer zero frame) and the Laue group: runs at 2theta
    arm, kappa and phi settings with their collision-free omega range; each step adds the run with the most new unique reflections (then the most
    redundancy) until the completeness and redundancy targets or the run limit; high-angle runs (|theta| >= 50 deg) use the long exposure."""
    Hr, _ = hkl_sphere(A0, d_target, limit=300_000)
    keys = canonical_keys(Hr, np.array(ops_idx))
    uk, uinv = np.unique(keys, return_inverse=True)
    r0 = Hr @ reciprocal(A0).T
    cands = []
    for th in (0.0, 30.0, -30.0, 60.0, -60.0, 90.0, -90.0):
        for kap in (0.0, -40.0, 40.0, -80.0, 80.0):
            iv = free_omega_interval(kap * DEG, th * DEG, dist)
            if iv is None:
                continue
            for ph in (0.0, 90.0, 180.0, 270.0):
                run = dict(omega_start=iv[0], omega_end=iv[1], kappa=kap * DEG, phi=ph * DEG, theta=th * DEG, distance=dist)
                cov = np.unique(uinv[run_coverage(r0, lam, run, sig_m)])
                n_fr = int(round((iv[1] - iv[0]) / fw))
                if len(cov) and 1 <= n_fr <= MAX_FRAMES_PER_RUN:
                    cands.append((run, cov, n_fr))
    counts = np.zeros(len(uk), int)
    chosen, hist = [], []
    while len(chosen) < max_runs and cands:
        comp = float(np.mean(counts > 0))
        red = float(np.mean(np.minimum(counts, redundancy)))
        if comp >= completeness and red >= redundancy * completeness:
            break
        best, bi, new_best = 0.0, -1, 0
        for i, (run, cov, n_fr) in enumerate(cands):
            nn = int(np.sum(counts[cov] == 0))
            g = 10.0 * nn + np.sum(counts[cov] < redundancy)
            g /= n_fr
            if g > best:
                best, bi, new_best = g, i, nn
        if bi < 0 or best <= 0:
            break
        if red >= redundancy * comp and new_best < 0.002 * len(uk):                 # only a few blind-region reflections left
            break
        hist.append(comp)
        if len(hist) > 4 and hist[-1] - hist[-4] < 0.003 and red >= redundancy * comp:
            break
        run, cov, n_fr = cands.pop(bi)
        counts[cov] += 1
        run = dict(run, exposure_s=exp_high if abs(run['theta']) >= 50 * DEG else exp_low, frames=n_fr)
        chosen.append(run)
    chosen.sort(key=lambda r: (round(r['theta'], 6), round(r['kappa'], 6), round(r['phi'], 6)))
    pose, feasible = dict(omega=0.0, kappa=0.0, phi=0.0, theta=0.0, distance=dist), []
    for r in chosen:                                                       # every run must be reachable from the previous one
        start = dict(omega=r['omega_start'], kappa=r['kappa'], phi=r['phi'], theta=r['theta'], distance=r['distance'])
        if plan_route(pose, start) is None:
            continue
        feasible.append(r)
        pose = dict(start, omega=r['omega_start'] + r['frames'] * fw)
    chosen = feasible
    counts = np.zeros(len(uk), int)
    for r in chosen:
        run = dict(r, omega_end=r['omega_start'] + r['frames'] * fw)
        counts[np.unique(uinv[run_coverage(r0, lam, run, sig_m)])] += 1
    t = sum(r['frames'] * r['exposure_s'] for r in chosen)
    return dict(runs=chosen, predicted_completeness=float(np.mean(counts > 0)), predicted_redundancy=float(np.mean(counts)), unique_reflections=int(len(uk)),
                collection_time_s=float(t))


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


ID = dict(TEXT, pattern='[a-z0-9_]{1,32}')
ENG_LIMIT = '工程输入限（厂家未公布该范围）：'
SIM_LIMIT = '仿真上限（不是仪器限制）：'
DOC_LIMIT = '厂家公布：'
UI_LIMIT = '同平台用户说明（Synergy-S，奥斯陆大学仪器说明）：'
CRYO_LIMIT = 'Cryostream 1000产品页检索摘要（页面未能缓存）：'
ANGLE_REF = '测角仪角度：ω与φ绕竖直+Z（俯视逆时针为+）、κ绕倾斜50°的κ轴（ω=0时沿(−sin50°, 0, −cos50°)）；参考姿态全为0时φ轴竖直、κ臂指向光源一侧；X射线沿资产+X'
THETA_REF = '2θ臂角θ：绕竖直+Z（俯视逆时针为+），θ=0时探测器正对直射束（沿+X）'
DIST_REF = '晶体到探测器传感器平面的距离（沿2θ臂）'
FRAME_REF = '帧像素坐标（列x、行y，0起；x沿探测器水平轴（θ=0时指向资产+Y），y向下（−Z）；像素100 µm，775×803）'
PIXEL_REGION = nullable(ref(dict(array(integer(0, max(NX, NY)), 4, 4), prefixItems=[integer(0, NX - 1), integer(0, NY - 1), integer(1, NX), integer(1, NY)]),
                            FRAME_REF + '：[x0, y0, x1, y1]，0≤x0<x1≤775、0≤y0<y1≤803，含x0/y0、不含x1/y1'))
SUBSTANCE_IDS = sorted(crystal_lib())
PLANT = obj(dict(room_k=basis(number('K', 288.15, 303.15), ENG_LIMIT + '实验室室温15–30 °C'), humidity=basis(ref(number('1', 0.0, 0.95), '室内相对湿度（0–1）'), ENG_LIMIT + '相对湿度0–95 %（不结露的实验室）'),
                 target=enum(['Cu', 'Mo']), chiller_available=BOOL, nitrogen_available=BOOL, noise_seed=integer(0, 2 ** 32 - 1)))
PLANT_EXAMPLE = dict(room_k=295.15, humidity=0.40, target='Cu', chiller_available=True, nitrogen_available=True, noise_seed=1)
SAMPLE_PROVIDE = obj(dict(sample_id=ID, substance_id=enum(SUBSTANCE_IDS),
                          size_m=array(basis(number('m', 10e-6, 1e-3), ENG_LIMIT + '单晶每边10 µm–1 mm'), 3, 3),
                          mosaicity_rad=basis(ref(number('rad', *MOSAIC_RANGE_RAD), '镶嵌度（晶粒取向分布的FWHM）'), ENG_LIMIT + '0.006°–2.9°'),
                          seed=integer(0, 2 ** 31 - 1), defect=enum(['none', 'twin', 'split', 'polycrystalline', 'amorphous']),
                          twin_fraction=ref(number('1', 0.0, 0.5), '第二畴（孪晶或裂晶）的体积分数'), listed_in_database=BOOL, inverted=BOOL),
                     optional=('defect', 'twin_fraction', 'listed_in_database', 'inverted'))
SAMPLE_EXAMPLE = dict(sample_id='aspirin_01', substance_id='aspirin', size_m=[1.5e-4, 1.0e-4, 0.8e-4], mosaicity_rad=0.008, seed=11)
OPERATION = obj(dict(operation_id=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
POSE_IN = obj(dict(omega_rad=ref(number('rad', *OMEGA_RANGE_RAD), ANGLE_REF), kappa_rad=ref(number('rad', *KAPPA_RANGE_RAD), ANGLE_REF),
                   phi_rad=ref(number('rad', *PHI_RANGE_RAD), ANGLE_REF), theta_rad=basis(ref(number('rad', *THETA_RANGE_RAD), THETA_REF), ENG_LIMIT + '2θ臂±100°'),
                   distance_m=basis(ref(number('m', DIST_MIN_M, DIST_MAX_M), DIST_REF), UI_LIMIT + '最小32 mm；' + ENG_LIMIT + '最大150 mm')))
POSE_EXAMPLE = dict(omega_rad=0.3, kappa_rad=0.0, phi_rad=1.0, theta_rad=0.0, distance_m=0.045)
MOVE_OUT = obj(dict(operation_id=TEXT, target=POSE_IN, duration_s=number('s')))
RUN = obj(dict(omega_start_rad=ref(number('rad', *OMEGA_RANGE_RAD), ANGLE_REF), omega_end_rad=ref(number('rad', *OMEGA_RANGE_RAD), ANGLE_REF),
               kappa_rad=ref(number('rad', *KAPPA_RANGE_RAD), ANGLE_REF), phi_rad=ref(number('rad', *PHI_RANGE_RAD), ANGLE_REF),
               theta_rad=POSE_IN['properties']['theta_rad'], distance_m=POSE_IN['properties']['distance_m'],
               exposure_s=basis(ref(number('s', 1.0 / FRAME_RATE_MAX_HZ, 600.0), '每帧曝光（无快门扫描：帧宽/曝光=扫描速度≤10°/s）'), DOC_LIMIT + '帧频≤100 Hz；' + ENG_LIMIT + '≤600 s/帧')))
FRAME_WIDTH = basis(ref(number('rad', 0.05 * DEG, 5.0 * DEG), '每帧的ω扫描宽度（常用0.3°）'), ENG_LIMIT + '0.05°–5°')
RUN_EXAMPLE = dict(omega_start_rad=-0.5, omega_end_rad=0.5, kappa_rad=0.0, phi_rad=0.0, theta_rad=0.0, distance_m=0.045, exposure_s=1.0)
COLLECT = obj(dict(runs=nullable(array(RUN, 1, MAX_RUNS)), strategy_record_id=nullable(TEXT), frame_width_rad=FRAME_WIDTH))
COLLECT_EXAMPLE = dict(runs=[RUN_EXAMPLE], strategy_record_id=None, frame_width_rad=0.5 * DEG)
PRE = obj(dict(exposure_s=RUN['properties']['exposure_s'], frame_width_rad=FRAME_WIDTH, distance_m=POSE_IN['properties']['distance_m']))
PRE_EXAMPLE = dict(exposure_s=1.0, frame_width_rad=0.5 * DEG, distance_m=0.045)
CELL = obj(dict(a_m=number('m'), b_m=number('m'), c_m=number('m'), alpha_rad=number('rad'), beta_rad=number('rad'), gamma_rad=number('rad'), volume_m3=number('m3')))
CELL_SU = obj(dict(a_m=nullable(number('m')), b_m=nullable(number('m')), c_m=nullable(number('m')), alpha_rad=nullable(number('rad')),
                   beta_rad=nullable(number('rad')), gamma_rad=nullable(number('rad'))))
RECORD_REF = dict(record_id=TEXT)
RESULT = obj(dict(record=obj(dict(record_id=TEXT, kind=enum(['pre_experiment', 'dataset', 'indexing', 'reduction', 'structure', 'strategy']), status=STATUS,
                                  started_s=number('s'), finished_s=nullable(number('s')), parent_ids=array(TEXT, 0, 4), meta={'type': 'object'},
                                  warnings=array(TEXT, 0, 32), template_source={'type': 'object'})), data={'type': 'object'}))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'safety', 'door', 'xray', 'shutter', 'goniometer', 'cryostream', 'crystal', 'collection', 'processing',
                                                'maintenance', 'error', 'fault']), code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['electrical_energy_j', 'xray_on_s', 'filament_s', 'anode_s', 'frames', 'exposure_s', 'crystal_dose_gy', 'door_openings', 'emergency_stops',
               'cryostream_on_s', 'crystals_mounted']
LEDGER = obj({k: number('J' if k.endswith('_j') else 's' if k.endswith('_s') else 'Gy' if k.endswith('_gy') else '1', 0.0) for k in LEDGER_KEYS}, optional=('crystal_dose_gy',))
AGENT_HIDDEN_LEDGER = ('crystal_dose_gy',)                # the absorbed dose follows the hidden flux, beam and crystal: evaluator only

cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['cabinet', 'rack_rear_panel', 'rack_connectors'],
    '宿主声明实验室条件（室温、相对湿度、光源靶材Cu或Mo（安装时确定、运行中不可换）、冷水机与低温装置氮气/干燥气是否可用、噪声种子）；断电、X射线关、无操作且无晶体在测角头上时才可重新声明',
    'evaluator', 'configuration')
cap('sample.provide', SAMPLE_PROVIDE, obj(dict(sample_id=TEXT, location=TEXT)), SAMPLE_EXAMPLE, ['goniometer_head'],
    '宿主在实验台上提供一颗装在晶体环（含冷冻油）上的单晶：物质取结构库（67种COD结构）、三边尺寸、镶嵌度、种子（决定装样取向与偏心）；可选缺陷（孪晶、裂晶、多晶、非晶）、'
    '第二畴体积分数、是否已收录在“What is this?”检索库、绝对构型是否反转', 'evaluator', 'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL, state=TEXT)), dict(enabled=True), ['button_power', 'cabinet'],
    '接通/关闭控制电子（开机约20 s初始化；关机时X射线关闭、快门关闭、运动停止、进行中的操作失败）')
cap('power.press_button', obj({}), obj(dict(enabled=BOOL, state=TEXT)), {}, ['button_power'], '按一下安全面板上的主电源键（切换通断）')
cap('safety.doors_ok', obj({}), obj(dict(armed=BOOL)), {}, ['button_doors_ok', 'safety_panel', 'door_left', 'door_right'],
    '按Doors OK键（用户说明）：两扇门都关好、无急停且控制电子就绪时重新接通安全回路（绿灯亮）；门打开即断开')
cap('safety.emergency_stop', obj({}), obj(dict(latched=BOOL)), {}, ['button_emergency_stop', 'estop_collar'],
    '按下急停（锁定）：立即切断X射线高压、关闭快门、停止全部运动，安全回路断开')
cap('safety.reset_emergency_stop', obj({}), obj(dict(latched=BOOL)), {}, ['button_emergency_stop'], '旋转释放急停（之后仍需关门并按Doors OK）', 'agent', 'robot_skill')
cap('xray.set_key', obj(dict(angle_rad=basis(ref(number('rad', 0.0, PI / 2 + KEY_STOP_TOL_RAD), 'X射线钥匙转角（0 OFF、π/2 ON，顺时针为+）'),
                                            '钥匙行程0–90°（工程）；场景关节以单精度回传，止点处允许超出π/2至多1e-6 rad，按π/2计'))), obj(dict(key_on=BOOL)), dict(angle_rad=PI / 2),
    ['knob_xray_enable', 'xray_lock_housing'], '用手转X射线使能钥匙：转到ON（≥80°）才允许产生X射线；转回OFF立即关闭X射线', 'agent', 'robot_skill')
LW_LIMIT = '厂家规格（LabWrench转载的MicroMax-007 HF规格表）：'
KV = basis(number('V', *KV_RANGE_V), LW_LIMIT + '20–60 kV，1 kV步长')
MA = basis(number('A', *MA_RANGE_A), LW_LIMIT + '10–30 mA，1 mA步长')
XRAY_OUT = obj(dict(state=TEXT, target_voltage_v=number('V'), target_current_a=number('A'), power_w=number('W')))
cap('xray.on', obj(dict(voltage_v=KV, current_a=MA)), XRAY_OUT, dict(voltage_v=40e3, current_a=30e-3), ['anode_head', 'source_tower', 'cabinet'],
    '开启X射线发生器并设定管压/管流（功率≤1.2 kW；需通电、钥匙ON、安全回路接通、无急停、冷水与真空正常）：阳极加速与灯丝预热约30 s后管压与管流先后按2 kV/s、2 mA/s爬升到设定值'
    '（先调使负载下降的量，爬升中功率不超过1.2 kW；工程）；'
    '只写目标，实际值随时间推进')
cap('xray.set', obj(dict(voltage_v=KV, current_a=MA)), XRAY_OUT, dict(voltage_v=50e3, current_a=24e-3), ['anode_head', 'cabinet'],
    'X射线开启时改变管压/管流（先调使负载下降的量，再调另一量，按2 kV/s、2 mA/s爬升，途中功率不超过1.2 kW）')
cap('xray.standby', obj({}), XRAY_OUT, {}, ['anode_head', 'cabinet'], '发生器待机：降到20 kV、10 mA（工程），保护阳极与灯丝')
cap('xray.off', obj({}), obj(dict(state=TEXT)), {}, ['anode_head', 'cabinet'], '立即关闭高压（快门同时关闭）')
cap('shutter.open', obj({}), obj(dict(open=BOOL)), {}, ['source_optics', 'lamp_shutter_open', 'stack_light_red'],
    '打开X射线快门（需X射线开启稳定、安全回路接通）；数据采集会自动开闭快门')
cap('shutter.close', obj({}), obj(dict(open=BOOL)), {}, ['source_optics', 'lamp_shutter_open'], '关闭X射线快门')
cap('optics.set_divergence', obj(dict(divergence_rad=basis(ref(number('rad', *DIVERGENCE_RANGE_RAD), '束散角（全角）'), DOC_LIMIT + '1–10 mrad连续可调'))),
    obj(dict(divergence_rad=number('rad'))), dict(divergence_rad=5e-3), ['source_optics', 'collimator'],
    '电动可变狭缝：设定束散角1–10 mrad（宣传册）；减小束散使衍射斑更小、分辨长轴晶胞，但通量按(d/10 mrad)²下降（工程）；采集中不能改')
cap('detector.set_threshold', obj(dict(threshold_ev=basis(ref(number('eV', 1000.0, 20000.0), '下能量阈值（光子能量低于它不计数）'),
                                                   ENG_LIMIT + '输入1–20 keV；运行层另按0.3–0.9倍Kα1能量拒绝（厂家只写能量阈值抑制荧光）'))), obj(dict(threshold_ev=number('eV'), fraction=number('1'))),
    dict(threshold_ev=7000.0), ['detector'], 'HyPix-6000HE下能量阈值（厂家：能量阈值抑制荧光）：允许0.3–0.9倍Kα1能量（工程）；高于半能量时像素边界电荷分配使计数效率下降（工程）；采集中不能改')
cap('goniometer.move', POSE_IN, MOVE_OUT, POSE_EXAMPLE, ['omega_table', 'kappa_block', 'phi_shaft', 'theta_arm', 'detector_carriage'],
    '测角仪与探测器臂同时移动到目标（ω、θ、κ 10°/s，φ 20°/s，距离10 mm/s工程）；碰撞模型预测途中或终点会碰撞则拒绝；门开时须按住两个Motion Enable键；异步')
cap('goniometer.stop', obj({}), obj(dict(stopped=BOOL)), {}, ['omega_table', 'theta_arm'], '停止进行中的测角仪移动（停在当时位置）')
cap('goniometer.mount_position', obj({}), MOVE_OUT, {}, ['omega_table', 'kappa_block', 'detector_carriage'],
    '移到装样姿态（CrysAlisPro装样菜单：ω=0、κ=0、θ=0、探测器退到100 mm、φ不变）；异步')
cap('motion_enable.hold', obj(dict(held=BOOL)), obj(dict(held=BOOL)), dict(held=True), ['motion_enable_left', 'motion_enable_right'],
    '同时按住（或松开）舱内两个Motion Enable键（用户说明：门开时测角仪与探测器只有在两键被持续按住时才能运动）', 'agent', 'robot_skill')
cap('door.open', obj(dict(door=enum(['left', 'right']), angle_rad=basis(ref(number('rad', 0.2, 100 * DEG), '门的开角'),
                                                                        ENG_LIMIT + '0.2 rad（约11°，更小的开角按门未开处理）至100°（照片估计的铰链行程）'))), obj(dict(door=TEXT, angle_rad=number('rad'))),
    dict(door='left', angle_rad=1.5), ['door_left', 'door_right'], '打开防护舱门（主门左铰链或右门右铰链）：安全回路立即断开、快门关闭；采集中开门进入安全模式', 'agent', 'robot_skill')
cap('door.close', obj(dict(door=enum(['left', 'right']))), obj(dict(door=TEXT, angle_rad=number('rad'))), dict(door='left'), ['door_left', 'door_right'],
    '关上防护舱门（之后按Doors OK重新接通安全回路）', 'agent', 'robot_skill')
cap('crystal.mount', obj(dict(sample_id=ID)), obj(dict(sample_id=TEXT, duration_s=number('s'))), dict(sample_id='aspirin_01'), ['goniometer_head', 'phi_shaft'],
    '装晶体（用户说明）：拧下测角头、把晶体环放到磁铁上、装回；需主门打开、测角仪在装样姿态（κ≈0、探测器≥60 mm）且不在移动；晶体取向随机、偏离中心可达0.35 mm（隐藏）；'
    '有低温气流时晶体被急冷；约60 s（宿主/机器人抽象）', 'agent', 'robot_skill')
cap('crystal.unmount', obj({}), obj(dict(sample_id=TEXT)), {}, ['goniometer_head'], '取下晶体放回实验台（同装样条件）', 'agent', 'robot_skill')
cap('crystal.center', obj({}), OPERATION, {}, ['goniometer_head', 'phi_shaft', 'camera', 'centring_screen'],
    '对中晶体（用户说明）：看对中屏上的十字线、转测角头五个旋钮平移晶体，φ每次转90°检查，共约90 s；需通电（摄像头）、主门打开、按住Motion Enable、在装样姿态；残余偏心约8 µm；异步',
    'agent', 'robot_skill')
cap('maintenance.replace_filament', obj({}), obj(dict(duration_s=number('s'))), {}, ['anode_head'],
    '更换灯丝（厂家：用户约每三个月可自行更换）：需断电、X射线关；真空腔放气，下次开机需抽真空约5 min（工程）；约30 min', 'agent', 'robot_skill')
cap('cryostream.set', obj(dict(temperature_k=basis(number('K', *CRYO_RANGE_K), CRYO_LIMIT + 'Cryostream 1000 80–400 K'),
                               ramp_k_s=basis(ref(number('K/s', 1.0 / 3600.0, CRYO_RAMP_MAX_K_S), '气流温度变化速率'), ENG_LIMIT + '1–360 K/h（2.8e-4–0.1 K/s）'))),
    obj(dict(setpoint_k=number('K'), ramp_k_s=number('K/s'))), dict(temperature_k=100.0, ramp_k_s=0.1), ['cryostream_head', 'cryostream_nozzle'],
    'Cryostream 1000气流温度设定与升降温速率（需氮气/干燥气）：只写目标，气流温度按速率变化、晶体随气流（约5 s）；稳定度0.1 K（厂家）')
cap('cryostream.stop', obj({}), obj(dict(on=BOOL)), {}, ['cryostream_head'], '停止低温气流：晶体回到室温（约30 s）')
cap('camera.view', obj({}), obj(dict(crystal_present=BOOL, offset_m=nullable(array(number('m'), 2, 2)), size_m=nullable(array(number('m'), 3, 3)), colour=nullable(TEXT),
                                     habit=nullable(TEXT), ice_visible=BOOL, lamp_on=BOOL)), {}, ['camera', 'camera_optics', 'centring_screen'],
    '对中摄像头画面（需通电）：晶体是否在环上、晶体中心相对十字线的偏移（摄像头像面两个方向，5 µm量化、约3 µm抖动）、目测尺寸、颜色与外形、是否结霜', 'agent', 'query')
cap('experiment.pre_experiment', PRE, OPERATION, PRE_EXAMPLE, ['detector', 'omega_table', 'kappa_block', 'source_optics'],
    'CrysAlisPro预实验（用户说明：检查单晶质量，大于100 µm的晶体约4 min）：在给定距离、θ=0处做三段20°的ω扫描（κ与φ取碰撞模型允许的设置），完成后自动找峰与指标化（晶胞、指标化比例、镶嵌度与分辨率估计）；'
    '需X射线开启、安全回路接通、晶体已装；异步')
cap('data.collect', COLLECT, OPERATION, COLLECT_EXAMPLE, ['detector', 'omega_table', 'kappa_block', 'phi_shaft', 'theta_arm', 'detector_carriage', 'source_optics'],
    '无快门数据采集：按给定的ω扫描段（或策略记录的段）逐段移动测角仪、开快门、按帧宽/曝光扫描，读出衍射帧；采集中开门进入安全模式（快门关、采集停）；每段先按碰撞模型检查；异步')
cap('strategy.calculate', obj(dict(record_id=TEXT, resolution_m=basis(number('m', 0.4e-10, 5e-10), ENG_LIMIT + '0.4–5 Å'),
                                   completeness=basis(ref(number('1', 0.1, 1.0), '目标完整度'), ENG_LIMIT + '0.1–1'),
                                   redundancy=basis(ref(number('1', 1.0, 12.0), '目标冗余度'), ENG_LIMIT + '1–12'),
                                   exposure_low_s=RUN['properties']['exposure_s'], exposure_high_s=RUN['properties']['exposure_s'], frame_width_rad=FRAME_WIDTH,
                                   distance_m=POSE_IN['properties']['distance_m'])),
    obj(dict(record_id=TEXT, runs=INT, predicted_completeness=number('1'), predicted_redundancy=number('1'), collection_time_s=number('s'))),
    dict(record_id='idx_0001', resolution_m=0.84e-10, completeness=1.0, redundancy=4.0, exposure_low_s=0.5, exposure_high_s=2.0, frame_width_rad=0.5 * DEG, distance_m=0.045),
    ['detector', 'omega_table', 'kappa_block', 'theta_arm'],
    'CrysAlisPro策略：用指标化记录的取向与劳厄群（对指标化所用记录——预实验或数据集——的强度做劳厄检验，赝对称或缺面晶格取较低者；该记录可积分的观测少于20个时以晶格对称为上限），在各θ、κ、φ设置下取碰撞模型允许的ω区间，'
    '只计完整记录的反射（离扫描两端≥2倍摇摆宽度），贪心选段直到达到目标分辨率下的完整度与冗余度；高角段曝光长'
    '（用户说明：高角是低角的4倍）；写一条strategy记录', 'agent', 'analysis')
cap('processing.index', obj(dict(record_id=TEXT, threshold_i_sigma=basis(ref(number('1', 2.0, 100.0), '找峰阈值I/σ'), ENG_LIMIT + '2–100（常用5）'))),
    obj(dict(record_id=TEXT, indexed=BOOL, cell=nullable(CELL), indexation_ratio=nullable(number('1')), crystal_system=nullable(TEXT), centring=nullable(TEXT), peaks=INT)),
    dict(record_id='pre_0001', threshold_i_sigma=5.0), ['detector'],
    '找峰（I/σ阈值）与指标化：一维FFT法找晶格、UB最小二乘、Niggli约化、晶格度量对称与常规晶胞；写一条indexing记录（失败时记录原因：峰太少、无晶格、指标化比例低）', 'agent', 'analysis')
cap('processing.reduce', obj(dict(record_id=TEXT, index_record_id=nullable(TEXT))),
    obj(dict(record_id=TEXT, laue_class=nullable(TEXT), space_group=nullable(TEXT), space_group_ambiguous=BOOL, completeness=nullable(number('1')),
             r_int=nullable(number('1')), resolution_m=nullable(number('m')))), dict(record_id='ds_0001', index_record_id=None), ['detector'],
    '数据还原：用数据集自身（或给定指标化记录）的取向为各观测指标化、积分（去掉部分反射、过载与重叠）、按曝光归一、经验吸收校正（先按P-1拟合，暂定劳厄类高于-1时按该群重拟合）、在晶格点群的子群中检验劳厄群（逐个转动的反射对R加各子群合并的R_int）'
    '并按段/10°块比例合并（R_int、完整度、冗余、I/σ、分辨率）、'
    '按系统消光在该劳厄类内选空间群（消光与强度统计分不开的备选标为不唯一，如F-43m/Fm-3m；符号按结构库的轴设定书写，结构库中两种原点设定都有的空间群（目前只有Fd-3m）按第2原点（对称中心在原点）报告、另一原点列在space_group_origin_alternatives，只有一种原点设定的（如I41/amd :1）按库中的设定报告，space_group_setting给出由所报晶胞指标到该设定的'
    '变换h\'=T h）；写一条reduction记录', 'agent', 'analysis')
cap('processing.what_is_this', obj(dict(record_id=TEXT)),
    obj(dict(record_id=TEXT, matched=BOOL, name=nullable(TEXT), formula=nullable(TEXT), r1=nullable(number('1')), flack_x=nullable(number('1')),
             flack_su=nullable(number('1')))), dict(record_id='red_0001'), ['detector'],
    'CrysAlisPro“What is this?”：用约化晶胞检索结构库（已发表结构，晶胞偏差最小的至多3个）；对reduction记录还用每个匹配的结构模型对数据精修比例因子与整体B（R1、wR2、GooF），'
    '晶格度量允许的各种指标方式都试，按wR2给匹配排序、报告拟合最好的模型（晶胞在误差内重合的不同结构，如Si与闪锌矿ZnS，由强度区分）；非中心对称结构用未合并的Bijvoet对按Parsons商法估计Flack参数（绝对构型）；未收录的新结构报告无匹配；写一条structure记录',
    'agent', 'analysis')
cap('frame.read', obj(dict(record_id=TEXT, run_index=integer(0, MAX_RUNS - 1), frame_index=integer(0, MAX_FRAMES_PER_RUN - 1), binning=dict(integer(1, 16), enum=[1, 2, 4, 8, 16]),
                           region_px=PIXEL_REGION)),
    obj(dict(record_id=TEXT, run_index=INT, frame_index=INT, width=INT, height=INT, binning=INT, x0=INT, y0=INT, counts=array(array(INT, 0, NX), 0, NY),
             exposure_s=number('s'), omega_start_rad=number('rad'), omega_end_rad=number('rad'), max_count=INT)),
    dict(record_id='ds_0001', run_index=0, frame_index=0, binning=4, region_px=None), ['detector'],
    '读一帧衍射图（光子计数，Poisson）：整帧按2–16合并像素，或给定区域以全分辨率读出（最多65536个值）', 'agent', 'query')
cap('measurement.result', obj(dict(record_id=TEXT, max_values=integer(16, RESULT_VALUES_MAX))), RESULT, dict(record_id='red_0001', max_values=4096), ['detector'],
    '读取记录（预实验/数据集的段与帧信息与峰统计、指标化结果、还原统计与合并反射表、结构匹配与精修、策略段）', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, MAX_RECORDS))), {}, ['detector'], '列出记录', 'agent', 'query')
cap('records.delete', obj(dict(record_id=TEXT)), obj(dict(deleted=TEXT)), dict(record_id='ds_0001'), ['detector'], '删除一条记录（进行中的不能删）')
cap('events.read', obj(dict(since_s=basis(number('s', 0.0, 1e9), SIM_LIMIT + '0–1e9 s（仿真时钟）'))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), ['cabinet'], '读事件日志', 'agent', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=LEDGER)), {}, ['cabinet'], '读账本（电能、X射线时间、灯丝与阳极小时数、帧数、曝光、开门、急停、低温时间、装样数；'
    '晶体吸收剂量只给evaluator：它由隐藏的通量、光斑与晶体决定，agent的账本不含该项）',
    'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries={'type': 'object'})), {}, ['detector'], '读取结构库与散射表的来源与sha256', 'agent', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 8))), dict(fault='chiller_failure', active=True), ['cabinet'],
    '评测者注入/清除持续故障（冷水机故障、真空泄漏、灯丝断、氮气耗尽、结霜）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=basis(number('s', 0.0, MAX_STEP_S), SIM_LIMIT + '一次推进0–7天'))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['cabinet'], '宿主推进仿真时间', 'evaluator', 'simulation')
DECLARED = {
    'structure.solve': 'CrysAlisPro AutoChem可用SHELXT/olex2.solve从头解结构（宣传册）；本资产只实现已收录结构的“What is this?”匹配与精修，未实现从头解析',
    'data.export_files': '导出hkl/ins/cif等文件（用户说明：Finalize生成hkl与ins）；本资产只在运行层保留记录（measurement.result），未实现文件读写',
    'absorption.face_indexing': '按晶面指标化的数值吸收校正（用户说明：画晶体外形）；本资产只做经验吸收校正（入射/衍射方向多项式加块比例：先按P-1拟合，暂定劳厄类高于-1时按该群的等效反射重拟合），不做晶面指标的解析校正',
    'experiment.phi_scan': 'φ扫描段（κ几何可用）；本资产只实现ω扫描段',
    'powder.pattern': 'CrysAlisPro粉末模式（把帧积分为粉末图）；本资产未实现',
}
UNSUPPORTED = {
    'hardware.connect': '连接真实XtaLAB Synergy-R与CrysAlisPro不在本仿真资产范围：设计上不提供硬件适配器，也不授权操作真实仪器',
    'source.switch_wavelength': '双波长（Cu/Mo双带阳极）是另一型号XtaLAB Synergy-DW；本配置靶材在安装时确定（plant.configure）',
    'xtalab_flow.load': 'XtaLAB Flow机器人装样系统是选件，不在本配置',
    'detector.hypix_arc': 'HyPix-Arc弧形探测器是另一探测器选项，本配置为HyPix-6000HE',
    'high_pressure.load': '高压附件（金刚石对顶砧）是选件，不在本配置',
    'xtalcheck.screen': 'XtalCheck-S原位筛选是选件，不在本配置',
}
for _k, _why in list(DECLARED.items()) + list(UNSUPPORTED.items()):
    cap(_k, obj({}), obj({}), {}, ['cabinet'], _why, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


CRYSTAL_TRUTH = obj(dict(sample_id=TEXT, substance_id=TEXT, orientation=array(array(number('1'), 3, 3), 3, 3), offset_m=array(number('m'), 3, 3),
                         size_m=array(number('m'), 3, 3), mosaicity_rad=number('rad'), defect=TEXT, twin_fraction=number('1'), inverted=BOOL,
                         listed_in_database=BOOL, temperature_k=number('K'), dose_gy=number('Gy'), ice_m3=number('m3')))
slot('clock.time_s', number('s'), 's', 'derived', 'cabinet', '仿真时钟')
slot('plant.configured', BOOL, '1', 'setpoint', 'cabinet', '已声明实验室条件')
slot('plant.target', nullable(enum(['Cu', 'Mo'])), '1', 'setpoint', 'anode_head', '光源靶材（安装时确定）')
slot('power.enabled', BOOL, '1', 'setpoint', 'button_power', '控制电子通电（主电源键）；电源灯、舱内照明与对中屏随之亮')
slot('system.state', enum(['off', 'booting', 'ready']), '1', 'derived', 'cabinet', '系统状态（断电/初始化中/就绪）')
slot('door.left_angle_rad', number('rad', 0.0, 100 * DEG), 'rad', 'scene', 'door_left', '主门开角（场景关节door_left镜像；0关闭）')
slot('door.right_angle_rad', number('rad', 0.0, 100 * DEG), 'rad', 'scene', 'door_right', '右门开角（场景关节door_right镜像）')
slot('safety.doors_closed', BOOL, '1', 'derived', 'door_left', '两扇门都关着（编码磁铁联锁）')
slot('safety.armed', BOOL, '1', 'derived', 'button_doors_ok', '安全回路接通（Doors OK后；门打开、急停或断电时断开）；Doors OK键绿灯')
slot('safety.estop_latched', BOOL, '1', 'derived', 'button_emergency_stop', '急停锁定（运行层安全回路的锁存：safety.emergency_stop置位、safety.reset_emergency_stop清除；'
     '真实急停按钮机械锁定，场景中的面板键按下后回弹，锁定只在运行层保持）')
slot('motion_enable.held', BOOL, '1', 'derived', 'motion_enable_left', '两个Motion Enable键被按住')
slot('xray.key_angle_rad', number('rad', 0.0, PI / 2), 'rad', 'scene', 'knob_xray_enable', 'X射线钥匙转角（场景关节knob_xray_enable镜像）')
slot('xray.key_on', BOOL, '1', 'derived', 'knob_xray_enable', '钥匙在ON（≥80°）')
slot('xray.state', enum(['off', 'starting', 'ramping', 'on', 'standby']), '1', 'derived', 'anode_head', '发生器状态（关/阳极加速与灯丝预热/管压管流爬升/稳定/待机）')
slot('xray.generating', BOOL, '1', 'derived', 'anode_head', '高压开着（爬升、稳定或待机）；面板X射线灯与舱内橙色灯亮')
slot('xray.voltage_v', nullable(number('V')), 'V', 'derived', 'anode_head', '管压显示值（发生器过程值按0.1 kV取整，未模拟读数噪声；控制电子未就绪时为null）')
slot('xray.current_a', nullable(number('A')), 'A', 'derived', 'anode_head', '管流显示值（发生器过程值按0.1 mA取整，未模拟读数噪声；控制电子未就绪时为null）')
slot('xray.target', obj(dict(voltage_v=number('V'), current_a=number('A'))), '1', 'setpoint', 'anode_head', '管压/管流设定')
slot('xray.vacuum_ready', BOOL, '1', 'derived', 'source_tower', '阳极真空腔就绪（通电后涡轮泵运转；放气后需抽真空）')
slot('shutter.open', BOOL, '1', 'derived', 'source_optics', 'X射线快门打开；面板快门灯与舱内红灯亮')
slot('optics.divergence_rad', number('rad'), 'rad', 'setpoint', 'source_optics', '束散角设定（全角）')
slot('detector.threshold_ev', number('eV'), 'eV', 'setpoint', 'detector', '探测器下能量阈值')
slot('goniometer.omega_rad', number('rad'), 'rad', 'scene', 'omega_table', 'ω角（场景关节omega_table镜像）', reference=ANGLE_REF)
slot('goniometer.kappa_rad', number('rad'), 'rad', 'scene', 'kappa_block', 'κ角（场景关节kappa_block镜像）', reference=ANGLE_REF)
slot('goniometer.phi_rad', number('rad'), 'rad', 'scene', 'phi_shaft', 'φ角（场景关节phi_shaft镜像）', reference=ANGLE_REF)
slot('detector.theta_rad', number('rad'), 'rad', 'scene', 'theta_arm', '2θ臂角（场景关节theta_arm镜像）', reference=THETA_REF)
slot('detector.distance_m', number('m'), 'm', 'scene', 'detector_carriage', '晶体-探测器距离（场景关节detector_carriage镜像：关节值=距离−32 mm）', reference=DIST_REF)
slot('goniometer.moving', BOOL, '1', 'derived', 'omega_table', '测角仪或探测器臂在运动')
slot('cryostream.state', enum(['off', 'ramping', 'stable']), '1', 'derived', 'cryostream_head', '低温气流状态')
slot('cryostream.setpoint_k', nullable(number('K')), 'K', 'setpoint', 'cryostream_head', '气流温度设定')
slot('cryostream.gas_temperature_k', nullable(number('K')), 'K', 'derived', 'cryostream_nozzle', '喷嘴处气流温度显示值（按设定与速率的理想斜坡，0.1 K取整，未模拟稳定度噪声；未开启时为null）')
slot('crystal.mounted_sample_id', nullable(TEXT), '1', 'derived', 'goniometer_head', '测角头上的晶体样品')
slot('sample.bench_ids', array(TEXT, 0, MAX_SAMPLES), '1', 'derived', 'goniometer_head', '实验台上的晶体样品')
slot('operation.id', nullable(TEXT), '1', 'derived', 'cabinet', '进行中的操作（采集为记录ID，移动与对中为操作ID）')
slot('operation.kind', nullable(enum(['move', 'collect', 'center'])), '1', 'derived', 'cabinet', '进行中操作的种类')
slot('operation.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'cabinet', '进行中操作的进度')
slot('operation.remaining_s', nullable(number('s', 0.0)), 's', 'derived', 'cabinet', '进行中操作的剩余时间')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'cabinet', '进行中的操作属于另一个agent')
slot('collection.frames_done', nullable(INT), '1', 'derived', 'detector', '进行中采集已读出的帧数')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'detector', '最近完成的记录ID（agent只看到自己或宿主的记录）')
slot('truth.crystal', nullable(CRYSTAL_TRUTH), '1', 'process_truth', 'goniometer_head', '测角头上晶体的真实定义与状态（物质、取向矩阵、偏心、尺寸、镶嵌度、缺陷、温度、剂量、冰量）', 'evaluator')
slot('truth.flux_ph_s', number('1/s'), '1/s', 'process_truth', 'source_optics', '到达晶体处的Kα光子通量（快门关为0）', 'evaluator')
slot('truth.filament_remaining_s', number('s'), 's', 'process_truth', 'anode_head', '灯丝剩余寿命', 'evaluator')
slot('truth.faults', array(TEXT, 0, 8), '1', 'process_truth', 'cabinet', '注入的持续故障', 'evaluator')
LAMP_STATES = {'power.enabled', 'xray.generating', 'shutter.open', 'safety.armed'}
SCENE_MIRRORED = {'door.left_angle_rad', 'door.right_angle_rad', 'goniometer.omega_rad', 'goniometer.kappa_rad', 'goniometer.phi_rad', 'detector.theta_rad',
                  'detector.distance_m', 'xray.key_angle_rad', 'safety.estop_latched'} | LAMP_STATES


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


PURE_ANALYSIS = {'processing.index', 'processing.reduce', 'processing.what_is_this', 'strategy.calculate'}
ASYNC = {'goniometer.move', 'goniometer.mount_position', 'experiment.pre_experiment', 'data.collect', 'crystal.center'}
AXES = ('omega', 'kappa', 'phi', 'theta', 'distance')
POSE_KEYS = dict(omega='omega_rad', kappa='kappa_rad', phi='phi_rad', theta='theta_rad', distance='distance_m')
DOOR_CLOSED_RAD = 0.5 * DEG
KEY_ON_RAD = 80.0 * DEG


def axis_speed(axis):
    return DIST_SPEED_M_S if axis == 'distance' else AXIS_SPEED_RAD_S[axis]


def move_durations(start, target):
    return {k: abs(target[k] - start[k]) / axis_speed(k) for k in AXES}


class RigakuXtalabsynergyRuntime:
    """Rigaku XtaLAB Synergy-R (HyPix-6000HE): control electronics and safety circuit, doors, X-ray generator and shutter, divergence slit,
    kappa goniometer and detector arm with the collision model, Cryostream, crystal mounting and centring, shutterless data collection, frames,
    CrysAlisPro-like processing (SI units)."""

    def __init__(self, instance_id=INSTANCE_ID):
        self.instance_id = instance_id
        self.t = 0.0
        self.revision = 0
        self._large_requests = {}
        self.plant = None
        self.power = False
        self.sys_state = 'off'
        self.state_t0 = 0.0
        self.doors = dict(left=0.0, right=0.0)
        self.armed = False
        self.estop = False
        self.motion_enable = False
        self.key_angle = 0.0
        self.xr = dict(state='off', t0=0.0, ramp=None, target=(40e3, 30e-3))
        self.vac = dict(state='off', t0=0.0, vented=False)
        self.filament_life_s = FILAMENT_INTERVAL_S
        self.shutter = False
        self.divergence = DIVERGENCE_RANGE_RAD[1]
        self.threshold_frac = THRESHOLD_DEFAULT
        self.pose = dict(omega=0.0, kappa=0.0, phi=0.0, theta=0.0, distance=DIST_MIN_M)
        self.cryo = dict(on=False, setpoint=None, rate=0.0, t0=0.0, T0=None)
        self.samples = {}
        self.crystal = None
        self.records = {}
        self.seq = dict(pre=0, ds=0, idx=0, red=0, str=0, stg=0, op=0, mount=0, view=0)
        self.op = None
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.faults = set()
        self.fault_log = []
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
        return RigakuXtalabsynergySession(self, name)

    def _event(self, kind, code, message, rid=None, t=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t if t is None else t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _seed(self):
        return self.plant['noise_seed'] if self.plant else 0

    def _ready(self):
        return self.power and self.sys_state == 'ready'

    def _doors_closed(self):
        return self.doors['left'] < DOOR_CLOSED_RAD and self.doors['right'] < DOOR_CLOSED_RAD

    def _line(self):
        return line_data(self.plant['target'])

    # ---------------------------------------------------------------- X-ray state
    def _xray_kv_ma(self, t=None):
        t = self.t if t is None else t
        x = self.xr
        if x['state'] not in ('ramping', 'on', 'standby') or x['ramp'] is None:
            return 0.0, 0.0
        return ramp_values(x['ramp'], t)

    def _generating(self):
        return self.xr['state'] in ('ramping', 'on', 'standby')

    def _flux(self, t=None):
        if not self.shutter or self.plant is None:
            return 0.0
        kv, ma = self._xray_kv_ma(t)
        return source_flux(self.plant['target'], kv, ma, self.divergence, self.led['anode_s'].value())

    def _start_ramp(self, kv1, ma1, t=None, origin=None):
        t = self.t if t is None else t
        if origin is not None:
            kv0, ma0 = origin
        else:
            kv0, ma0 = self._xray_kv_ma(t) if self.xr['state'] in ('ramping', 'on', 'standby') else STANDBY
        plan = ramp_plan(kv0, ma0, kv1, ma1, t)
        self.xr.update(state='ramping' if plan['dur'] > 0 else ('standby' if (kv1, ma1) == STANDBY else 'on'), ramp=plan, target=(kv1, ma1))

    def _xray_trip(self, code, message):
        if self.xr['state'] != 'off':
            self.xr.update(state='off', ramp=None)
            self._event('xray', code, message)
        if self.shutter:
            self.shutter = False
            self._event('shutter', 'SHUTTER_CLOSED', '快门关闭')
        if self.op is not None and self.op['kind'] == 'collect':
            self._fail_op('XRAY_OFF')

    def _cryo_gas(self, t=None):
        c = self.cryo
        if not c['on']:
            return None
        t = self.t if t is None else t
        if c['rate'] <= 0:
            return c['setpoint']
        d = c['setpoint'] - c['T0']
        dt = abs(d) / c['rate']
        f = 1.0 if dt <= 0 else min(1.0, (t - c['t0']) / dt)
        return c['T0'] + d * f

    def _cryo_ok(self):
        return self.plant is not None and self.plant['nitrogen_available'] and 'nitrogen_empty' not in self.faults

    # ---------------------------------------------------------------- pose
    def _pose_at(self, t=None):
        t = self.t if t is None else t
        o = self.op
        if o is None:
            return dict(self.pose)
        x = o['extra']
        if o['kind'] == 'move':
            dt = t - o['t0']
            if not x['legs']:
                return dict(x['start'])
            for leg in x['legs']:
                if dt < leg['t0'] + max(leg['dur'].values()):
                    return self._interp(leg['start'], leg['target'], leg['dur'], dt - leg['t0'])
            return dict(x['legs'][-1]['target'])
        if o['kind'] == 'center':
            p = dict(x['start'])
            done = 0.0
            for tt in x['turns']:
                done += min(1.0, max(0.0, (t - tt) / x['turn_s']))
            p['phi'] = x['start']['phi'] + 0.5 * PI * done
            return p
        seg = self._segment_at(o, t)
        if seg is None:
            return dict(x['pose_end']) if x.get('pose_end') else dict(self.pose)
        if seg['kind'] == 'move':
            return self._interp(seg['from'], seg['to'], seg['dur'], t - seg['ta'])
        run = x['runs'][seg['run']]
        p = dict(omega=run['omega_start'], kappa=run['kappa'], phi=run['phi'], theta=run['theta'], distance=run['distance'])
        if seg['kind'] == 'scan':
            sgn = 1.0 if run['omega_end'] >= run['omega_start'] else -1.0
            p['omega'] = run['omega_start'] + sgn * run['frame_width_rad'] / run['exposure_s'] * min(max(0.0, t - seg['ta']), seg['tb'] - seg['ta'])
        return p

    @staticmethod
    def _interp(a, b, dur, dt):
        return {k: a[k] + (b[k] - a[k]) * (1.0 if dur[k] <= 0 else min(1.0, max(0.0, dt / dur[k]))) for k in AXES}

    @staticmethod
    def _segment_at(o, t):
        for seg in o['extra']['segments']:
            if seg['ta'] - 1e-12 <= t < seg['tb'] - 1e-12:
                return seg
        return None

    def _moving(self):
        return self.op is not None and self.op['kind'] in ('move', 'collect', 'center')

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        o = self.op
        prog = rem = None
        if o is not None:
            prog = min(1.0, max(0.0, (self.t - o['t0']) / o['duration_s'])) if o['duration_s'] > 0 else 0.0
            rem = max(0.0, o['t0'] + o['duration_s'] - self.t)
        kv, ma = self._xray_kv_ma()
        pose = self._pose_at()
        gas = self._cryo_gas()
        frames = None
        if o is not None and o['kind'] == 'collect':
            frames = self._frames_done(o)
        cr = self.crystal
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'plant.target': self.plant['target'] if self.plant else None,
            'power.enabled': self.power, 'system.state': self.sys_state,
            'door.left_angle_rad': self.doors['left'], 'door.right_angle_rad': self.doors['right'], 'safety.doors_closed': self._doors_closed(),
            'safety.armed': self.armed, 'safety.estop_latched': self.estop, 'motion_enable.held': self.motion_enable,
            'xray.key_angle_rad': self.key_angle, 'xray.key_on': self.key_angle >= KEY_ON_RAD, 'xray.state': self.xr['state'], 'xray.generating': self._generating(),
            'xray.voltage_v': (round(kv / 100.0) * 100.0) if self._ready() else None, 'xray.current_a': (round(ma * 1e4) / 1e4) if self._ready() else None,
            'xray.target': dict(voltage_v=self.xr['target'][0], current_a=self.xr['target'][1]), 'xray.vacuum_ready': self.vac['state'] == 'ok',
            'shutter.open': self.shutter, 'optics.divergence_rad': self.divergence,
            'detector.threshold_ev': self.threshold_frac * (self._line()['e1'] if self.plant else line_data('Cu')['e1']),
            'goniometer.omega_rad': pose['omega'], 'goniometer.kappa_rad': pose['kappa'], 'goniometer.phi_rad': pose['phi'], 'detector.theta_rad': pose['theta'],
            'detector.distance_m': pose['distance'], 'goniometer.moving': self._moving(),
            'cryostream.state': 'off' if gas is None else ('stable' if abs(gas - self.cryo['setpoint']) <= CRYO_STABILITY_K else 'ramping'),
            'cryostream.setpoint_k': self.cryo['setpoint'] if self.cryo['on'] else None, 'cryostream.gas_temperature_k': None if gas is None else round(gas, 1),
            'crystal.mounted_sample_id': cr['sample_id'] if cr else None,
            'sample.bench_ids': sorted(k for k, s in self.samples.items() if s['location'] == 'bench'),
            'operation.id': (o['record_id'] or o['op']) if o else None, 'operation.kind': o['kind'] if o else None, 'operation.progress': prog,
            'operation.remaining_s': rem, 'operation.started_by_other_agent': False, 'collection.frames_done': frames,
            'result.last_record_id': self._last_record(),
            'truth.crystal': self._crystal_truth(), 'truth.flux_ph_s': self._flux(), 'truth.filament_remaining_s': max(0.0, self.filament_life_s - self.led['filament_s'].value()),
            'truth.faults': sorted(self.faults),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _crystal_truth(self):
        c = self.crystal
        if c is None:
            return None
        return dict(sample_id=c['sample_id'], substance_id=c['sid'], orientation=np.asarray(c['U']).tolist(), offset_m=np.asarray(c['offset']).tolist(),
                    size_m=list(map(float, c['size'])), mosaicity_rad=c['mosaic'], defect=c['defect'], twin_fraction=c['frac2'], inverted=c['inverted'],
                    listed_in_database=c['listed'], temperature_k=c['T'], dose_gy=c['dose_gy'], ice_m3=c['ice_m3'])

    def _last_record(self, keep=None):
        done = [k for k, r in self.records.items() if r['status'] == 'completed' and (keep is None or keep(k))]
        return max(done, key=lambda k: (self.records[k]['finished_s'] or 0.0, k), default=None)

    def _frames_done(self, o):
        n = 0
        x = o['extra']
        for seg in x['segments']:
            if seg['kind'] != 'scan':
                continue
            run = x['runs'][seg['run']]
            if self.t >= seg['tb'] - 1e-12:
                n += run['frames']
            elif self.t > seg['ta']:
                n += int((self.t - seg['ta']) / run['exposure_s'] + 1e-9)
        return n

    # ---------------------------------------------------------------- invoke
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        if capability_id in DECLARED:
            raise ActionRejected('NOT_IMPLEMENTED')
        validate(CAPS[capability_id]['input_schema'], a)
        a = coerce(CAPS[capability_id]['input_schema'], a)
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'simulation'):
            self.revision += 1
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_ready(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')
        if self.sys_state != 'ready':
            raise ActionRejected('NOT_READY')

    def _need_idle(self):
        if self.op is not None:
            raise ActionRejected('OPERATION_RUNNING')

    def _need_motion_allowed(self):
        if self.estop:
            raise ActionRejected('EMERGENCY_STOP')
        if not self._doors_closed() and not self.motion_enable:
            raise ActionRejected('MOTION_ENABLE_REQUIRED')

    # ---------------------------------------------------------------- configuration
    def _cmd_plant_configure(self, room_k, humidity, target, chiller_available, nitrogen_available, noise_seed):
        if self.power or self.op is not None or self.xr['state'] != 'off' or self.crystal is not None:
            raise ActionRejected('POWER_ON')
        self.plant = dict(room_k=room_k, humidity=humidity, target=target, chiller_available=chiller_available, nitrogen_available=nitrogen_available,
                          noise_seed=noise_seed)
        rng = _rng(noise_seed, 17)
        self.filament_life_s = FILAMENT_INTERVAL_S * float(rng.uniform(0.9, 1.1))
        if self.cryo['on'] and not nitrogen_available:
            self.cryo.update(on=False)
        self._event('power', 'PLANT_CONFIGURED', f'实验室条件已声明（靶材{target}）')
        return dict(plant=dict(self.plant))

    def _cmd_sample_provide(self, sample_id, substance_id, size_m, mosaicity_rad, seed, defect='none', twin_fraction=None, listed_in_database=True, inverted=False):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('DUPLICATE_SAMPLE')
        if len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_SAMPLES')
        if twin_fraction is None:
            twin_fraction = 0.35 if defect in ('twin', 'split') else 0.0
        if defect not in ('twin', 'split') and twin_fraction > 0:
            raise ActionRejected('INVALID_ARGUMENT')
        if substance_model(substance_id)['centrosymmetric'] and inverted:
            inverted = False                                                   # a centrosymmetric structure has no absolute configuration
        self.samples[sample_id] = dict(sid=substance_id, size=[float(v) for v in size_m], mosaic=float(mosaicity_rad), seed=int(seed), defect=defect,
                                       frac2=float(twin_fraction), listed=bool(listed_in_database), inverted=bool(inverted), location='bench')
        return dict(sample_id=sample_id, location='bench')

    # ---------------------------------------------------------------- power and safety
    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            self.power, self.sys_state, self.state_t0 = True, 'booting', self.t
            self.vac.update(state='pumping', t0=self.t)
            self._event('power', 'POWER_ON', '控制电子通电，初始化中')
        elif not enabled and self.power:
            self._power_off()
        return dict(enabled=self.power, state=self.sys_state)

    def _power_off(self):
        if self.op is not None:
            self._fail_op('POWER_OFF')
        self._xray_trip('XRAY_OFF', '断电：X射线关闭')
        self.power, self.sys_state = False, 'off'
        self.armed = False
        self.vac.update(state='off')
        self._event('power', 'POWER_OFF', '控制电子断电')

    def _cmd_power_press_button(self):
        return self._cmd_power_set(not self.power)

    def _cmd_safety_doors_ok(self):
        self._need_ready()
        if self.estop:
            raise ActionRejected('EMERGENCY_STOP')
        if not self._doors_closed():
            raise ActionRejected('DOORS_OPEN')
        self.armed = True
        self._event('safety', 'ARMED', '安全回路接通（Doors OK）')
        return dict(armed=True)

    def _cmd_safety_emergency_stop(self):
        self._need_plant()
        if not self.estop:
            self.estop = True
            self.led['emergency_stops'].add(1)
            if self.op is not None:
                self._fail_op('EMERGENCY_STOP')
            self._xray_trip('EMERGENCY_STOP', '急停：X射线高压切断')
            self.armed = False
            self._event('safety', 'EMERGENCY_STOP', '急停按下')
        return dict(latched=True)

    def _cmd_safety_reset_emergency_stop(self):
        self._need_plant()
        self.estop = False
        return dict(latched=False)

    def _cmd_xray_set_key(self, angle_rad):
        self._need_plant()
        self.key_angle = min(float(angle_rad), PI / 2)                   # the float32 joint value at the stop counts as the stop
        if angle_rad < KEY_ON_RAD and self.xr['state'] != 'off':
            self._xray_trip('KEY_OFF', '钥匙转到OFF：X射线关闭')
        return dict(key_on=self.key_angle >= KEY_ON_RAD)

    # ---------------------------------------------------------------- X-ray generator
    def _check_setting(self, voltage_v, current_a):
        if abs(voltage_v / KV_STEP_V - round(voltage_v / KV_STEP_V)) > 1e-6 or abs(current_a / MA_STEP_A - round(current_a / MA_STEP_A)) > 1e-6:
            raise ActionRejected('SETTING_STEP')
        if voltage_v * current_a > POWER_MAX_W + 1e-6:
            raise ActionRejected('POWER_LIMIT')

    def _xray_preconditions(self):
        self._need_ready()
        if self.estop:
            raise ActionRejected('EMERGENCY_STOP')
        if self.key_angle < KEY_ON_RAD:
            raise ActionRejected('KEY_OFF')
        if not self.armed:
            raise ActionRejected('SAFETY_NOT_ARMED')
        if not self.plant['chiller_available'] or 'chiller_failure' in self.faults:
            raise ActionRejected('COOLING_WATER')
        if self.vac['state'] != 'ok' or 'vacuum_loss' in self.faults:
            raise ActionRejected('VACUUM_NOT_READY')
        if 'filament_failure' in self.faults or self.led['filament_s'].value() >= self.filament_life_s:
            raise ActionRejected('FILAMENT_FAILURE')

    def _xout(self):
        return dict(state=self.xr['state'], target_voltage_v=self.xr['target'][0], target_current_a=self.xr['target'][1],
                    power_w=self.xr['target'][0] * self.xr['target'][1])

    def _cmd_xray_on(self, voltage_v, current_a):
        self._check_setting(voltage_v, current_a)
        if self.xr['state'] != 'off':
            raise ActionRejected('XRAY_ALREADY_ON')
        self._xray_preconditions()
        self.xr.update(state='starting', t0=self.t, ramp=None, target=(float(voltage_v), float(current_a)))
        self._event('xray', 'XRAY_STARTING', '阳极加速与灯丝预热')
        return self._xout()

    def _cmd_xray_set(self, voltage_v, current_a):
        self._check_setting(voltage_v, current_a)
        if self.xr['state'] == 'off':
            raise ActionRejected('XRAY_OFF')
        self._need_ready()
        if self.op is not None and self.op['kind'] == 'collect':
            raise ActionRejected('OPERATION_RUNNING')
        if self.xr['state'] == 'starting':
            self.xr['target'] = (float(voltage_v), float(current_a))
        else:
            self._start_ramp(float(voltage_v), float(current_a))
        return self._xout()

    def _cmd_xray_standby(self):
        return self._cmd_xray_set(*STANDBY)

    def _cmd_xray_off(self):
        self._need_plant()
        if self.op is not None and self.op['kind'] == 'collect':
            self._fail_op('XRAY_OFF')
        self._xray_trip('XRAY_OFF', 'X射线关闭')
        return dict(state='off')

    def _cmd_shutter_open(self):
        self._need_ready()
        if self.xr['state'] not in ('on', 'standby'):
            raise ActionRejected('XRAY_NOT_STABLE')
        if not self.armed:
            raise ActionRejected('SAFETY_NOT_ARMED')
        if self.op is not None and self.op['kind'] == 'collect':
            raise ActionRejected('OPERATION_RUNNING')
        self.shutter = True
        self._event('shutter', 'SHUTTER_OPEN', '快门打开')
        return dict(open=True)

    def _cmd_shutter_close(self):
        self._need_plant()
        if self.op is not None and self.op['kind'] == 'collect':
            raise ActionRejected('OPERATION_RUNNING')
        self.shutter = False
        return dict(open=False)

    def _cmd_optics_set_divergence(self, divergence_rad):
        self._need_ready()
        if self.op is not None and self.op['kind'] == 'collect':
            raise ActionRejected('OPERATION_RUNNING')
        self.divergence = float(divergence_rad)
        return dict(divergence_rad=self.divergence)

    def _cmd_detector_set_threshold(self, threshold_ev):
        self._need_ready()
        if self.op is not None and self.op['kind'] == 'collect':
            raise ActionRejected('OPERATION_RUNNING')
        f = threshold_ev / self._line()['e1']
        if not THRESHOLD_RANGE[0] - 1e-9 <= f <= THRESHOLD_RANGE[1] + 1e-9:
            raise ActionRejected('THRESHOLD_OUT_OF_RANGE')
        self.threshold_frac = float(f)
        return dict(threshold_ev=float(threshold_ev), fraction=self.threshold_frac)

    # ---------------------------------------------------------------- goniometer
    def _new_op(self, kind, duration, extra, record_id=None):
        self.seq['op'] += 1
        op_id = f'op_{self.seq["op"]:04d}'
        self.op = dict(op=op_id, kind=kind, t0=self.t, duration_s=float(duration), extra=extra, record_id=record_id)
        self.operations[op_id] = dict(phase='running', kind=kind, record_id=record_id)
        return op_id

    def _check_path(self, start, target):
        if path_collision(start, target) is not None:                  # a refusal changes nothing (no ledger count, no event)
            raise ActionRejected('COLLISION_PREDICTED')

    def _route(self, start, target):
        legs = plan_route(start, target)
        if legs is None:
            raise ActionRejected('COLLISION_PREDICTED')
        return legs

    def _start_move(self, target):
        start = self._pose_at()
        legs, t = [], 0.0
        for a, b in self._route(start, target):
            dur = move_durations(a, b)
            legs.append(dict(start=a, target=b, dur=dur, t0=t))
            t += max(dur.values())
        op_id = self._new_op('move', t, dict(start=start, target=dict(target), legs=legs))
        self._event('goniometer', 'MOVE', '测角仪移动' + (f'（{len(legs)}段避碰路径）' if len(legs) > 1 else ''))
        return op_id, t

    def _cmd_goniometer_move(self, omega_rad, kappa_rad, phi_rad, theta_rad, distance_m):
        self._need_ready()
        self._need_idle()
        self._need_motion_allowed()
        target = dict(omega=omega_rad, kappa=kappa_rad, phi=phi_rad, theta=theta_rad, distance=distance_m)
        op_id, total = self._start_move(target)
        return dict(operation_id=op_id, target=dict(omega_rad=omega_rad, kappa_rad=kappa_rad, phi_rad=phi_rad, theta_rad=theta_rad, distance_m=distance_m),
                    duration_s=total)

    def _cmd_goniometer_mount_position(self):
        self._need_ready()
        self._need_idle()
        self._need_motion_allowed()
        cur = self._pose_at()
        target = dict(MOUNT_POSE, phi=cur['phi'])
        op_id, total = self._start_move(target)
        return dict(operation_id=op_id, target={POSE_KEYS[k]: target[k] for k in AXES}, duration_s=total)

    def _cmd_goniometer_stop(self):
        self._need_plant()
        if self.op is None or self.op['kind'] != 'move':
            raise ActionRejected('NOT_MOVING')
        self._cancel_op(self.operations[self.op['op']])
        return dict(stopped=True)

    def _cmd_motion_enable_hold(self, held):
        self._need_plant()
        self.motion_enable = bool(held)
        if not held and not self._doors_closed() and self.op is not None and self.op['kind'] in ('move', 'center'):
            self._fail_op('MOTION_ENABLE_RELEASED')
        return dict(held=self.motion_enable)

    # ---------------------------------------------------------------- doors
    def _cmd_door_open(self, door, angle_rad):
        self._need_plant()
        was_closed = self.doors[door] < DOOR_CLOSED_RAD
        self.doors[door] = float(angle_rad)
        if was_closed:
            self.led['door_openings'].add(1)
            self._event('door', 'DOOR_OPENED', ('主门' if door == 'left' else '右门') + '打开：安全回路断开')
        if self.armed:
            self.armed = False
        if self.shutter:
            self.shutter = False
            self._event('shutter', 'SHUTTER_CLOSED', '开门：快门关闭')
        if self.op is not None:
            if self.op['kind'] == 'collect':
                self._fail_op('SECURITY_MODE')
            elif self.op['kind'] == 'move' and not self.motion_enable:
                self._fail_op('SECURITY_MODE')
        return dict(door=door, angle_rad=self.doors[door])

    def _cmd_door_close(self, door):
        self._need_plant()
        self.doors[door] = 0.0
        return dict(door=door, angle_rad=0.0)

    # ---------------------------------------------------------------- crystal handling
    def _need_mount_access(self):
        if self.doors['left'] < 45 * DEG:
            raise ActionRejected('DOOR_CLOSED')
        if self.op is not None:
            raise ActionRejected('OPERATION_RUNNING')
        p = self._pose_at()
        if abs(p['kappa']) > 5 * DEG or p['distance'] < 0.060:
            raise ActionRejected('NOT_AT_MOUNT_POSITION')

    def _cmd_crystal_mount(self, sample_id):
        self._need_plant()
        if sample_id not in self.samples:
            raise ActionRejected('UNKNOWN_SAMPLE')
        s = self.samples[sample_id]
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if self.crystal is not None:
            raise ActionRejected('CRYSTAL_MOUNTED')
        self._need_mount_access()
        self.seq['mount'] += 1
        rng = _rng(self._seed(), s['seed'], self.seq['mount'], 23)
        U = random_rotation(rng)
        d = rng.standard_normal(3)
        off = d / np.linalg.norm(d) * MOUNT_OFFSET_MAX_M * float(rng.uniform(0.3, 1.0))
        A = substance_model(s['sid'])['A']
        U2 = None
        if s['defect'] in ('twin', 'split'):
            U2 = U @ twin_rotation(A, s['defect'], rng)
        gas = self._cryo_gas()
        T = self.plant['room_k'] if gas is None else gas + CRYO_OFFSET_K
        self.crystal = dict(sample_id=sample_id, sid=s['sid'], U=U, U2=U2, frac2=s['frac2'], offset=off, size=np.array(s['size']), mosaic=s['mosaic'],
                            defect=s['defect'], inverted=s['inverted'], listed=s['listed'], T=float(T), dose_gy=0.0, ice_m3=0.0)
        s['location'] = 'goniometer'
        self.led['crystals_mounted'].add(1)
        self._event('crystal', 'MOUNTED', '晶体已装到测角头上')
        return dict(sample_id=sample_id, duration_s=MOUNT_S)

    def _cmd_crystal_unmount(self):
        self._need_plant()
        if self.crystal is None:
            raise ActionRejected('NO_CRYSTAL')
        self._need_mount_access()
        sid = self.crystal['sample_id']
        self.samples[sid]['location'] = 'bench'
        self.samples[sid]['dose_gy'] = self.samples[sid].get('dose_gy', 0.0) + self.crystal['dose_gy']
        self.crystal = None
        self._event('crystal', 'UNMOUNTED', '晶体已取下')
        return dict(sample_id=sid)

    def _cmd_crystal_center(self):
        self._need_ready()
        if self.crystal is None:
            raise ActionRejected('NO_CRYSTAL')
        self._need_mount_access()
        if not self.motion_enable:
            raise ActionRejected('MOTION_ENABLE_REQUIRED')
        start = self._pose_at()
        turn_s = 0.5 * PI / AXIS_SPEED_RAD_S['phi']
        turns = [self.t + 20.0, self.t + 40.0, self.t + 60.0, self.t + 80.0]
        op_id = self._new_op('center', CENTER_S, dict(start=start, turns=turns, turn_s=turn_s))
        self._event('crystal', 'CENTERING', '对中晶体')
        return dict(operation_id=op_id)

    def _cmd_maintenance_replace_filament(self):
        self._need_plant()
        if self.power:
            raise ActionRejected('POWER_ON')
        self.led['filament_s'] = _Sum()
        self.faults.discard('filament_failure')
        self.vac['vented'] = True
        rng = _rng(self._seed(), 29, len(self.fault_log))
        self.filament_life_s = FILAMENT_INTERVAL_S * float(rng.uniform(0.9, 1.1))
        self._event('maintenance', 'FILAMENT_REPLACED', '灯丝已更换，真空腔已放气')
        return dict(duration_s=FILAMENT_WORK_S)

    # ---------------------------------------------------------------- Cryostream
    def _cmd_cryostream_set(self, temperature_k, ramp_k_s):
        self._need_plant()
        if not self._cryo_ok():
            raise ActionRejected('NITROGEN_UNAVAILABLE')
        gas = self._cryo_gas()
        T0 = self.plant['room_k'] if gas is None else gas
        self.cryo.update(on=True, setpoint=float(temperature_k), rate=float(ramp_k_s), t0=self.t, T0=float(T0))
        self._event('cryostream', 'SETPOINT', f'低温气流设定{temperature_k:.1f} K')
        return dict(setpoint_k=float(temperature_k), ramp_k_s=float(ramp_k_s))

    def _cmd_cryostream_stop(self):
        self._need_plant()
        self.cryo.update(on=False)
        return dict(on=False)

    # ---------------------------------------------------------------- camera
    def _cmd_camera_view(self):
        self._need_ready()
        self.seq['view'] += 1
        c = self.crystal
        base = dict(crystal_present=c is not None, offset_m=None, size_m=None, colour=None, habit=None, ice_visible=False, lamp_on=True)
        if c is None:
            return base
        p = self._pose_at()
        Rg = gonio_matrix(p['omega'], p['kappa'], p['phi'])
        off = Rg @ c['offset']
        ax = CAMERA_AXIS
        e1 = np.cross(ax, [0.0, 0.0, 1.0]); e1 /= np.linalg.norm(e1)
        e2 = np.cross(ax, e1)
        rng = _rng(self._seed(), 31, self.seq['view'], int(self.t * 1000) & 0xFFFF)
        seen = [off @ e1, off @ e2] + rng.standard_normal(2) * CAMERA_SIGMA_M
        seen = [float(np.round(v / CAMERA_QUANT_M) * CAMERA_QUANT_M) for v in seen]
        size = [float(np.round(v * (1 + 0.05 * rng.standard_normal()) / CAMERA_QUANT_M) * CAMERA_QUANT_M) for v in c['size']]
        e = substance_model(c['sid'])['entry']
        return dict(base, offset_m=seen, size_m=size, colour=e.get('colour'), habit=e.get('habit'), ice_visible=c['ice_m3'] > 2e-15)

    # ---------------------------------------------------------------- data collection
    def _need_collect(self):
        self._need_ready()
        self._need_idle()
        if self.estop:
            raise ActionRejected('EMERGENCY_STOP')
        if self.xr['state'] not in ('on', 'standby'):
            raise ActionRejected('XRAY_NOT_STABLE')
        if not self.armed or not self._doors_closed():
            raise ActionRejected('SAFETY_NOT_ARMED')
        if self.crystal is None:
            raise ActionRejected('NO_CRYSTAL')
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')

    def _plan_collection(self, runs):
        """Segments of a collection: move to each run start (collision-checked), open the shutter, scan; returns (segments, total time)."""
        segs, t, pose = [], self.t, self._pose_at()
        for i, run in enumerate(runs):
            start = dict(omega=run['omega_start'], kappa=run['kappa'], phi=run['phi'], theta=run['theta'], distance=run['distance'])
            end = dict(start, omega=run['omega_end'])
            self._check_path(start, end)
            for a, b in self._route(pose, start):
                dur = move_durations(a, b)
                tm = max(dur.values())
                if tm > 0:
                    segs.append(dict(kind='move', ta=t, tb=t + tm, **{'from': dict(a)}, to=dict(b), dur=dur, run=i))
                    t += tm
            segs.append(dict(kind='shutter', ta=t, tb=t + SHUTTER_S, run=i))
            t += SHUTTER_S
            ts = run['frames'] * run['exposure_s']
            segs.append(dict(kind='scan', ta=t, tb=t + ts, run=i))
            t += ts
            pose = end
        return segs, t - self.t, pose

    def _runs_from(self, runs, frame_width):
        out = []
        total = 0
        for r in runs:
            span = abs(r['omega_end_rad'] - r['omega_start_rad'])
            n = int(round(span / frame_width))
            if n < 1 or abs(n * frame_width - span) > 1e-6 * max(span, 1e-9) + 1e-9 or n > MAX_FRAMES_PER_RUN:
                raise ActionRejected('RUN_NOT_MULTIPLE_OF_FRAME')
            if frame_width / r['exposure_s'] > SCAN_SPEED_MAX_RAD_S + 1e-12:
                raise ActionRejected('SCAN_TOO_FAST')
            total += n
            out.append(dict(omega_start=r['omega_start_rad'], omega_end=r['omega_end_rad'], kappa=r['kappa_rad'], phi=r['phi_rad'], theta=r['theta_rad'],
                            distance=r['distance_m'], exposure_s=r['exposure_s'], frame_width_rad=frame_width, frames=n))
        if total > MAX_TOTAL_FRAMES:
            raise ActionRejected('SIMULATION_LIMIT')
        return out

    def _start_collection(self, kind, runs, meta):
        segs, dur, pose_end = self._plan_collection(runs)
        prefix = 'pre' if kind == 'pre_experiment' else 'ds'
        self.seq[prefix] += 1
        rid = f'{prefix}_{self.seq[prefix]:04d}'
        src = self._source_state()
        self.records[rid] = dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, parent_ids=[], warnings=[],
                                 meta=dict(meta, runs=[self._run_public(r) for r in runs], source=self._source_public(src), frames_total=int(sum(r['frames'] for r in runs))),
                                 runs=[], template_source=self._template_source(), data={})
        op_id = self._new_op('collect', dur, dict(record_id=rid, runs=runs, segments=segs, done=set(), pose_end=pose_end, src=src), record_id=rid)
        self._event('collection', 'STARTED', f'开始采集{rid}（{len(runs)}段）', rid)
        return op_id, rid

    def _source_state(self):
        kv, ma = self._xray_kv_ma()
        return dict(target=self.plant['target'], kv=kv, ma=ma, divergence=self.divergence, threshold_frac=self.threshold_frac)

    @staticmethod
    def _source_public(src):
        return dict(target=src['target'], voltage_v=src['kv'], current_a=src['ma'], divergence_rad=src['divergence'], threshold_fraction=src['threshold_frac'])

    @staticmethod
    def _run_public(r):
        return dict(omega_start_rad=r['omega_start'], omega_end_rad=r['omega_end'], kappa_rad=r['kappa'], phi_rad=r['phi'], theta_rad=r['theta'],
                    distance_m=r['distance'], exposure_s=r['exposure_s'], frame_width_rad=r['frame_width_rad'], frames=r['frames'])

    def _template_source(self):
        c = self.crystal
        e = substance_model(c['sid'])['entry'] if c else None
        return dict(library='crystal_structure_library.json', scattering_library='xray_scattering_library.json',
                    substance_id=e['substance_id'] if e else None, cod_id=e['cod_id'] if e else None, doi=e['doi'] if e else None, evidence='documented')

    def _pre_runs(self, exposure_s, frame_width, distance):
        """Three 20 deg omega scans at theta = 0 with kappa / phi settings the collision model allows at this distance."""
        n = int(round(20 * DEG / frame_width))
        runs = []
        for kap, ph in ((0.0, 0.0), (-40 * DEG, 120 * DEG), (40 * DEG, 240 * DEG), (-80 * DEG, 60 * DEG), (80 * DEG, 180 * DEG), (0.0, 90 * DEG), (0.0, 180 * DEG)):
            iv = free_omega_interval(kap, 0.0, distance)
            if iv is None or iv[1] - iv[0] < n * frame_width:
                continue
            mid = 0.5 * (iv[0] + iv[1])
            om0 = mid - n * frame_width / 2
            runs.append(dict(omega_start=om0, omega_end=om0 + n * frame_width, kappa=kap, phi=ph, theta=0.0, distance=distance, exposure_s=exposure_s,
                             frame_width_rad=frame_width, frames=n))
            if len(runs) == 3:
                break
        return runs

    def _cmd_experiment_pre_experiment(self, exposure_s, frame_width_rad, distance_m):
        self._need_collect()
        if frame_width_rad / exposure_s > SCAN_SPEED_MAX_RAD_S + 1e-12:
            raise ActionRejected('SCAN_TOO_FAST')
        runs = self._pre_runs(exposure_s, frame_width_rad, distance_m)
        if len(runs) < 3:
            raise ActionRejected('COLLISION_PREDICTED')
        op_id, rid = self._start_collection('pre_experiment', runs, dict(exposure_s=exposure_s, frame_width_rad=frame_width_rad, distance_m=distance_m))
        return dict(operation_id=op_id)

    def _cmd_data_collect(self, runs, strategy_record_id, frame_width_rad):
        self._need_collect()
        if (runs is None) == (strategy_record_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if strategy_record_id is not None:
            st = self.records.get(strategy_record_id)
            if st is None:
                raise ActionRejected('UNKNOWN_RECORD')
            if st['kind'] != 'strategy' or st['status'] != 'completed':
                raise ActionRejected('RECORD_NOT_SUITABLE')
            runs = st['data']['runs_public']
            frame_width_rad = st['meta']['frame_width_rad']
        rr = self._runs_from(runs, frame_width_rad)
        op_id, rid = self._start_collection('dataset', rr, dict(frame_width_rad=frame_width_rad, strategy_record_id=strategy_record_id))
        if strategy_record_id:
            self.records[rid]['parent_ids'].append(strategy_record_id)
        return dict(operation_id=op_id)

    def _finish_run(self, o, i, n_frames):
        """Simulate the observations of run i of the collection (n_frames read out) and append them to the record."""
        x = o['extra']
        rec = self.records[x['record_id']]
        run = x['runs'][i]
        if i in x['done'] or n_frames <= 0:
            return
        x['done'].add(i)
        src = dict(x['src'], flux=source_flux(x['src']['target'], x['src']['kv'], x['src']['ma'], x['src']['divergence'], self.led['anode_s'].value()))
        rr = simulate_run(self.crystal, src, run, n_frames, (self._seed(), int(x['record_id'].split('_')[1]), i, 41), self.plant['humidity'])
        rr.update(run=dict(run, frames_recorded=n_frames), t_start=o['t0'], seeds=(self._seed(), int(x['record_id'].split('_')[1]), i, 43))
        rec['runs'].append(rr)
        self.led['frames'].add(n_frames)
        self.led['exposure_s'].add(n_frames * run['exposure_s'])
        ob = rr['obs']
        if ob.get('n', 0) and ob['overload'].any():
            if 'overloaded_reflections' not in rec['warnings']:
                rec['warnings'].append('overloaded_reflections')
        if ob.get('n', 0) and ob['twin_overlap'].any():
            rec.setdefault('_truth_warnings', set()).add('twin_overlap')

    def _complete_collection(self, o, status, code=None):
        x = o['extra']
        rec = self.records[x['record_id']]
        rec['status'] = status
        rec['finished_s'] = self.t
        rec['meta']['frames_recorded'] = int(sum(r['run']['frames_recorded'] for r in rec['runs']))
        rec['meta']['runs_recorded'] = len(rec['runs'])
        if code:
            rec['meta']['stop_reason'] = code
        self.shutter = False
        if status == 'completed' and rec['kind'] == 'pre_experiment':
            try:
                idx = self._index_record(x['record_id'], 5.0)
                ir = idx['data']['result']
                rec['data']['auto_index'] = dict(record_id=idx['record_id'], indexed=ir['indexed'], reason=ir.get('reason'), cell=ir.get('cell'),
                                                 indexation_ratio=ir.get('indexation_ratio'), crystal_system=ir.get('crystal_system'), centring=ir.get('centring'))
                self._owners[idx['record_id']] = self._owners.get(o['op'])
            except ActionRejected as exc:
                rec['data']['auto_index'] = dict(record_id=None, indexed=False, reason=str(exc))
            except Exception as exc:  # noqa: BLE001 - the frames are recorded; the analysis failure is reported, the operation still ends
                rec['data']['auto_index'] = dict(record_id=None, indexed=False, reason='INDEXING_ERROR')
                self._event('fault', 'DEVICE_ERROR', f'auto-index: {type(exc).__name__}: {exc}'[:300])

    # ---------------------------------------------------------------- processing
    def _record(self, record_id, kinds=None, completed=True):
        rec = self.records.get(record_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if kinds is not None and rec['kind'] not in kinds:
            raise ActionRejected('RECORD_NOT_SUITABLE')
        if completed and rec['status'] not in ('completed', 'cancelled', 'failed'):
            raise ActionRejected('RECORD_IN_USE')
        return rec

    def _new_record(self, prefix, kind, parents, meta, result, internal):
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        self.seq[prefix] += 1
        rid = f'{prefix}_{self.seq[prefix]:04d}'
        tpl = copy.deepcopy(self.records[parents[0]]['template_source']) if parents and parents[0] in self.records else self._template_source()
        self.records[rid] = dict(record_id=rid, kind=kind, status='completed', started_s=self.t, finished_s=self.t, parent_ids=list(parents), warnings=[],
                                 meta=meta, runs=[], template_source=tpl, data=dict(result=result), internal=internal)
        return self.records[rid]

    def _index_record(self, record_id, threshold):
        rec = self._record(record_id, ('pre_experiment', 'dataset'))
        runs = rec['runs']
        lam = line_data(rec['meta']['source']['target'])['lam1']
        pk = peak_table(runs, threshold)
        res = dict(indexed=False, peaks=int(len(pk['x'])), cell=None, indexation_ratio=None, crystal_system=None, centring=None)
        internal = dict(target=rec['meta']['source']['target'])
        if len(pk['x']) < 10:
            res['reason'] = 'NO_PEAKS' if len(pk['x']) == 0 else 'TOO_FEW_PEAKS'
            return self._new_record('idx', 'indexing', [record_id], dict(threshold_i_sigma=threshold), res, internal)
        r0 = reciprocal_vectors(pk, runs, lam)
        A = fft_index(r0)
        if A is None:
            res['reason'] = 'NO_LATTICE'
            rings = self._ring_report(runs, lam)
            if rings:
                res['powder_rings_d_m'] = rings
            return self._new_record('idx', 'indexing', [record_id], dict(threshold_i_sigma=threshold), res, internal)
        A, ok, hr, rms = refine_ub(r0, A)
        ratio = float(ok.mean())
        Ar, M = niggli(A)
        ops = metric_ops(Ar)
        unconverted = None
        try:
            Ac, P, system, cen = conventional_cell(Ar, ops)
        except ConventionalCellError:                                   # metric symmetry not expressible in a conventional cell: triclinic (conservative)
            unconverted = len(ops)
            ops = [np.eye(3), -np.eye(3)]
            Ac, P, system, cen = conventional_cell(Ar, ops)
        su = cell_esd(r0, hr, ok, A)
        cp = cell_params(Ac)
        res.update(indexed=ratio >= 0.5, indexation_ratio=ratio, crystal_system=system, centring=cen, lattice_symmetry_order=len(ops),
                   cell=dict(a_m=cp[0], b_m=cp[1], c_m=cp[2], alpha_rad=cp[3], beta_rad=cp[4], gamma_rad=cp[5], volume_m3=float(abs(np.linalg.det(Ac)))),
                   reduced_cell=dict(zip(['a_m', 'b_m', 'c_m', 'alpha_rad', 'beta_rad', 'gamma_rad'], cell_params(Ar))),
                   cell_su_reduced=dict(zip(['a_m', 'b_m', 'c_m', 'alpha_rad', 'beta_rad', 'gamma_rad'], [None if v is None else float(v) for v in su])),
                   residual_rms_per_m=rms, peaks_indexed=int(ok.sum()), ub_matrix_per_m=reciprocal(Ar).tolist(),
                   mosaicity_estimate_rad=self._mosaic_estimate(runs), resolution_estimate_m=self._resolution_estimate(runs, lam))
        if ratio < 0.5:
            res['reason'] = 'LOW_INDEXATION'
        if unconverted is not None:
            res['metric_symmetry_order_unconverted'] = unconverted
        internal.update(A_red=Ar, ops=ops, P=P, system=system, centring=cen, A_conv=Ac)
        out = self._new_record('idx', 'indexing', [record_id], dict(threshold_i_sigma=threshold), res, internal)
        if unconverted is not None:
            out['warnings'].append('lattice_symmetry_not_converted')     # the metric symmetry (order in the result) was reduced to -1
        return out

    @staticmethod
    def _ring_report(runs, lam):
        d = []
        for rr in runs:
            for q, p in rr['bg']['rings'][:8]:
                d.append(float(1.0 / q))
        return sorted(set(round(v, 12) for v in d), reverse=True)[:8]

    @staticmethod
    def _mosaic_estimate(runs):
        """Mosaicity from the measured rocking widths of strong full spots, divided by the Lorentz factor of their measured positions."""
        vals = []
        for rr in runs:
            o = rr['obs']
            if o.get('n', 0):
                sel = (o['i_obs'] / o['sigma'] > 10) & ~o['partial_obs']
                if sel.any():
                    s1, _ = pixel_rays(rr['run']['theta'], rr['run']['distance'], o['x_obs'][sel], o['y_obs'][sel])
                    vals.append(o['sig_om_obs'][sel] * np.maximum(np.abs(s1[:, 1]), 1e-3) * 2.3548)
        return float(np.median(np.concatenate(vals))) if vals else None

    @staticmethod
    def _resolution_estimate(runs, lam):
        """2nd percentile of d of the spots with I / sigma > 3, d from their measured positions (Bragg angle of the pixel ray)."""
        ds = []
        for rr in runs:
            o = rr['obs']
            if o.get('n', 0):
                sel = o['i_obs'] / o['sigma'] > 3
                if sel.any():
                    s1, _ = pixel_rays(rr['run']['theta'], rr['run']['distance'], o['x_obs'][sel], o['y_obs'][sel])
                    tth = np.arccos(np.clip(s1[:, 0], -1.0, 1.0))
                    ds.append(lam / (2.0 * np.maximum(np.sin(tth / 2.0), 1e-9)))
        if not ds:
            return None
        d = np.concatenate(ds)
        return float(np.percentile(d, 2))

    def _cmd_processing_index(self, record_id, threshold_i_sigma):
        rec = self._index_record(record_id, threshold_i_sigma)
        r = rec['data']['result']
        return dict(record_id=rec['record_id'], indexed=r['indexed'], cell=r['cell'], indexation_ratio=r['indexation_ratio'], crystal_system=r['crystal_system'],
                    centring=r['centring'], peaks=r['peaks'])

    def _integrate(self, ds, Ar, target):
        """Integrated, corrected observations of a pre-experiment or dataset record indexed with the reduced basis Ar: indices (reduced basis),
        intensities and sigmas per unit scan time after Lorentz, polarisation, air and sensor corrections, 10 deg scale blocks, the numbers of
        partial, overloaded and unindexed spots (all from the measured centroids and widths) and the incident / diffracted beam directions in the
        crystal frame (for the empirical absorption correction)."""
        lam = line_data(target)['lam1']
        H, I, S, block, dirs = [], [], [], [], []
        n_part = n_over = n_unidx = 0
        for ri, rr in enumerate(ds['runs']):
            o = rr['obs']
            if not o.get('n', 0):
                continue
            run = rr['run']
            # integration boxes are placed at the positions predicted by the refined orientation: a spot is integrated when it lies at the
            # predicted position of an integer index (spots of a second twin domain or of a wrong cell are not)
            pk = dict(run=np.full(o['n'], ri), x=o['x_obs'], y=o['y_obs'], s=o['s_obs'])
            r0 = reciprocal_vectors(pk, ds['runs'], lam)
            h = r0 @ Ar
            hr = np.round(h)
            ok = np.all(np.abs(h - hr) < 0.10, axis=1) & np.any(hr != 0, axis=1)
            good = ok & ~o['partial_obs'] & ~o['overload']
            n_part += int(o['partial_obs'].sum())
            n_over += int(o['overload'].sum())
            n_unidx += int((~ok).sum())
            # Lorentz, polarisation, air path and sensor efficiency from the measured spot position (instrument corrections of the integration)
            s1, tt = pixel_rays(run['theta'], run['distance'], o['x_obs'], o['y_obs'])
            L = 1.0 / np.maximum(np.abs(s1[:, 1]), 1e-3)
            Pp = (1 + s1[:, 0] ** 2) / 2
            corr = L * Pp * np.exp(-air_mu(line_data(target)['key']) * tt) * sensor_qe(target, run['distance'] / tt)
            corr = corr * run['exposure_s'] / run['frame_width_rad']        # counts per unit scan time: runs of different exposure on one scale
            H.append(hr[good].astype(int))
            I.append(o['i_obs'][good] / corr[good])
            S.append(o['sigma'][good] / corr[good])
            blk = (np.floor(o['s_obs'][good] / (10 * DEG))).astype(int) + 100 * ri
            block.append(blk)
            # incident and diffracted beam directions in the crystal (goniometer-zero) frame at the measured scan position
            sgn = 1.0 if run['omega_end'] >= run['omega_start'] else -1.0
            Rg = np.einsum('nij,jk->nik', rot_z_batch(run['omega_start'] + sgn * o['s_obs'][good]), rot(KAPPA_AXIS, run['kappa']) @ rot_z(run['phi']))
            u_in = np.einsum('nji,j->ni', Rg, np.array([1.0, 0.0, 0.0]))
            u_out = np.einsum('nji,nj->ni', Rg, s1[good])
            dirs.append(np.hstack([u_in, u_out]))
        if not H:
            return np.zeros((0, 3), int), np.zeros(0), np.zeros(0), np.zeros(0, int), n_part, n_over, n_unidx, np.zeros((0, 6))
        H, I, S, block, dirs = (np.concatenate(v) for v in (H, I, S, block, dirs))
        return H, I, S, block, n_part, n_over, n_unidx, dirs

    def _cmd_processing_reduce(self, record_id, index_record_id):
        ds = self._record(record_id, ('pre_experiment', 'dataset'))
        if index_record_id is None:
            idx = self._index_record(record_id, 5.0)
        else:
            idx = self._record(index_record_id, ('indexing',))
        ir = idx['data']['result']
        if not ir['indexed'] or 'A_red' not in idx['internal']:
            raise ActionRejected('NOT_INDEXED')
        target = ds['meta']['source']['target']
        Ar = idx['internal']['A_red']
        H, I, S, block, n_part, n_over, n_unidx, dirs = self._integrate(ds, Ar, target)
        if len(H) < 20:
            raise ActionRejected('TOO_FEW_REFLECTIONS')
        I0, S0 = I, S
        I, S, absorption = absorption_correct(H, I0, S0, dirs, block)     # direction-dependent absorption, fitted in P-1 before the symmetry test
        laue0, _ = laue_test(H, I, S, idx['internal']['ops'])
        if len(laue0) > 2:                                                # refit the surface with the equivalences of the provisional Laue group
            I1, S1, absorption1 = absorption_correct(H, I0, S0, dirs, block, ops=laue0)
            if absorption1['applied']:
                I, S, absorption = I1, S1, absorption1
        _, block = np.unique(block, return_inverse=True)
        laue, report = laue_test(H, I, S, idx['internal']['ops'])
        try:
            Ac, P, system, cen = conventional_cell(Ar, laue)
        except ConventionalCellError:                                   # the accepted group has no conventional cell within the tolerance: -1
            report.append(dict(group_check='conventional_cell_failed', r=None, order=len(laue), accepted=False))
            laue = [np.eye(3), -np.eye(3)]
            Ac, P, system, cen = conventional_cell(Ar, laue)
        Pinv = np.linalg.inv(P)
        ops_c = [np.round(np.linalg.inv(Pinv @ M @ P).T).astype(int) for M in laue]
        Hc = (H @ P).astype(int)
        mg = merge(Hc, I, S, ops_c, block)
        Bc = reciprocal(Ac)
        dm = 1.0 / np.linalg.norm(mg['H'] @ Bc.T, axis=1)
        d_min = float(dm.min())
        n_exp = expected_unique(Ac, P, ops_c, d_min)
        comp = min(1.0, len(mg['H']) / max(1, n_exp))
        isig = mg['I'] / mg['sigma']
        edges = np.quantile(1 / dm ** 2, np.linspace(0, 1, 6))
        shells = []
        for k in range(5):
            m_ = (1 / dm ** 2 >= edges[k]) & (1 / dm ** 2 <= edges[k + 1])
            if m_.any():
                shells.append(dict(d_max_m=float(dm[m_].max()), d_min_m=float(dm[m_].min()), unique=int(m_.sum()), mean_i_sigma=float(isig[m_].mean())))
        proper = [M for M in laue if round(np.linalg.det(M)) > 0]
        has6 = any(rotation_order(M)[0] == 6 for M in proper)
        lname = {('triclinic', 1): '-1', ('monoclinic', 2): '2/m', ('orthorhombic', 4): 'mmm', ('tetragonal', 4): '4/m', ('tetragonal', 8): '4/mmm',
                 ('hexagonal', 3): '-3', ('hexagonal', 6): '6/m' if has6 else '-3m', ('hexagonal', 12): '6/mmm', ('cubic', 12): 'm-3',
                 ('cubic', 24): 'm-3m'}.get((system, len(proper)))
        lat_c = [np.linalg.inv(Pinv @ M @ P).T for M in idx['internal']['ops']]      # lattice operations on conventional indices
        lat_c = [np.round(W).astype(int) for W in lat_c if np.allclose(W, np.round(W), atol=1e-6) and abs(abs(np.linalg.det(W)) - 1) < 1e-6]
        cosets, seen_c = [], set()
        for W in lat_c:
            key = min(tuple((L @ W).ravel()) for L in ops_c)
            if key not in seen_c:
                seen_c.add(key)
                cosets.append(W)
        sg_valid, sg_rows, e2 = choose_space_group(mg['H'].astype(float), mg['I'], mg['sigma'], dm, system, cen, lname, cosets)
        ties = [r['symbol'] for r in sg_valid[1:] if r.get('tie')]
        eff = None
        for sh in shells[::-1]:
            if sh['mean_i_sigma'] >= 2.0:
                eff = sh['d_min_m']
                break
        cp = cell_params(Ac)
        res = dict(laue_class=lname, crystal_system=system, centring=cen,
                   cell=dict(a_m=cp[0], b_m=cp[1], c_m=cp[2], alpha_rad=cp[3], beta_rad=cp[4], gamma_rad=cp[5], volume_m3=float(abs(np.linalg.det(Ac)))),
                   observations=int(len(H)), unique=int(len(mg['H'])), expected_unique=int(n_exp), completeness=comp, redundancy=float(len(H) / max(1, len(mg['H']))),
                   r_int=mg['r_int'], r_sigma=mg['r_sigma'], mean_i_sigma=float(isig.mean()), resolution_m=d_min, resolution_i_sigma_2_m=eff, shells=shells,
                   rejected=dict(partial=n_part, overload=n_over, unindexed=n_unidx), absorption_correction=absorption, laue_tests=report, e2_minus_1=e2,
                   space_group=sg_valid[0]['symbol'] if sg_valid else None, space_group_number=sg_valid[0]['number'] if sg_valid else None,
                   space_group_ambiguous=bool(ties), space_group_alternatives=ties,
                   space_group_origin_alternatives=[r['symbol'] for r in sg_valid if r.get('origin_alternative')],
                   space_group_setting=np.asarray(sg_valid[0]['setting']).tolist() if sg_valid else None,
                   space_group_candidates=[dict(symbol=r['symbol'], number=r['number'], absent=r['absent'], violations=r['violations']) for r in sg_valid[:5]])
        internal = dict(H=mg['H'], I=mg['I'], sigma=mg['sigma'], d=dm, A_conv=Ac, P=P, A_red=Ar, ops_c=ops_c, laue=laue, target=target, system=system, centring=cen,
                        H_obs=Hc, I_obs=mg['I_obs'], sigma_obs=mg['sigma_obs'])      # scaled unmerged observations (Bijvoet pairs for the Flack estimate)
        rec = self._new_record('red', 'reduction', [record_id, idx['record_id']], dict(index_record_id=idx['record_id']), res, internal)
        return dict(record_id=rec['record_id'], laue_class=lname, space_group=res['space_group'], space_group_ambiguous=bool(ties), completeness=comp,
                    r_int=mg['r_int'], resolution_m=d_min)

    def _excluded_substances(self):
        """Structures not in the searchable database: the sample marked as unpublished."""
        return {s['sid'] for s in self.samples.values() if not s['listed']}

    def _cmd_processing_what_is_this(self, record_id):
        rec = self._record(record_id, ('indexing', 'reduction'))
        it = rec.get('internal', {})
        if 'A_red' not in it:
            raise ActionRejected('NOT_INDEXED')
        matches = match_database(it['A_red'], exclude=self._excluded_substances())[:3]
        res = dict(matched=bool(matches), matches=[], refinement=None, ranked_by='cell deviation')
        for m in matches:
            e = crystal_lib()[m['substance_id']]
            res['matches'].append(dict(name_en=e['name_en'], name_zh=e['name_zh'], formula=e['formula'], space_group=e['space_group'], cod_id=e['cod_id'], doi=e['doi'],
                                       cell_angstrom_deg=e['cell_angstrom_deg'], length_deviation=m['length_dev'], angle_deviation_rad=m['angle_dev_rad'],
                                       refined_r1=None, refined_wr2=None))
        if matches and rec['kind'] == 'reduction':
            fits = []                                                        # cells of different structures can coincide within the cell error (Si 5.431 / ZnS 5.409 A)
            for i, m in enumerate(matches):
                dc = database_cells()[m['substance_id']]
                best, tried = None, 0
                for S in lattice_settings(it['A_red'], it['laue']):          # every indexing the lattice metric allows (Laue-group cosets)
                    T = np.linalg.inv(it['P']) @ S @ m['M'] @ np.linalg.inv(dc['QN'])
                    if np.max(np.abs(T - np.round(T))) >= 1e-3:
                        continue
                    T = np.round(T).astype(int)
                    if np.linalg.det(it['A_conv']) * np.linalg.det(substance_model(m['substance_id'])['A']) * np.linalg.det(T) < 0:
                        T = -T                                              # keep the hand: a proper rotation between the measured and the model frames
                    tried += 1
                    ref = refine_against(m['substance_id'], (it['H'] @ T).astype(int), it['I'], it['sigma'], it['d'], it['target'])
                    if best is None or ref['wr2'] < best[0]['wr2'] - 1e-12:
                        best = (ref, T)
                if best is not None:
                    res['matches'][i].update(refined_r1=best[0]['r1'], refined_wr2=best[0]['wr2'])
                    fits.append((best[0]['wr2'], i, best[0], best[1], tried))
            if fits:
                _, i, ref, T, tried = min(fits, key=lambda f: (f[0], f[1]))  # the model that fits the intensities best (then the closer cell)
                m = matches[i]
                ref.update(settings_tested=tried, setting_transformation=T.tolist(), models_refined=len(fits),
                           **flack_estimate(m['substance_id'], it['H_obs'] @ T, it['I_obs'], it['sigma_obs'], it['target']))
                res['refinement'] = ref
                order = sorted(range(len(res['matches'])), key=lambda j: (res['matches'][j]['refined_wr2'] is None, res['matches'][j]['refined_wr2'] or 0.0, j))
                res['matches'] = [res['matches'][j] for j in order]
                res['ranked_by'] = 'refined wR2 of the cell matches'
        out = self._new_record('str', 'structure', [record_id], {}, res, {})
        top = res['matches'][0] if res['matches'] else None
        ref = res['refinement'] or {}
        return dict(record_id=out['record_id'], matched=res['matched'], name=top['name_en'] if top else None, formula=top['formula'] if top else None,
                    r1=ref.get('r1'), flack_x=ref.get('flack_x'), flack_su=ref.get('flack_su'))

    def _cmd_strategy_calculate(self, record_id, resolution_m, completeness, redundancy, exposure_low_s, exposure_high_s, frame_width_rad, distance_m):
        self._need_plant()
        idx = self._record(record_id, ('indexing',))
        it = idx['internal']
        if not idx['data']['result']['indexed'] or 'A_red' not in it:
            raise ActionRejected('NOT_INDEXED')
        for e in (exposure_low_s, exposure_high_s):
            if frame_width_rad / e > SCAN_SPEED_MAX_RAD_S + 1e-12:
                raise ActionRejected('SCAN_TOO_FAST')
        lam = line_data(it['target'])['lam1']
        if resolution_m < lam / 2 * 1.0001:
            raise ActionRejected('RESOLUTION_UNREACHABLE')
        laue_ops, laue_from = it['ops'], 'lattice metric (upper bound)'
        parent = self.records.get(idx['parent_ids'][0]) if idx['parent_ids'] else None
        if parent is not None and parent['kind'] in ('pre_experiment', 'dataset'):
            Hq, Iq, Sq, bq, _, _, _, dq = self._integrate(parent, it['A_red'], it['target'])
            if len(Hq) >= 20:
                Iq0, Sq0 = Iq, Sq
                Iq, Sq, _ = absorption_correct(Hq, Iq0, Sq0, dq, bq)
                lq0, _ = laue_test(Hq, Iq, Sq, it['ops'])
                if len(lq0) > 2:
                    Iq1, Sq1, rq1 = absorption_correct(Hq, Iq0, Sq0, dq, bq, ops=lq0)
                    if rq1['applied']:
                        Iq, Sq = Iq1, Sq1
                laue_ops, _ = laue_test(Hq, Iq, Sq, it['ops'])               # pseudo-symmetric or merohedral lattices: the lower Laue class
                laue_from = 'Laue test of the ' + parent['kind'].replace('_', '-') + ' intensities'
        ops_idx = [np.round(np.linalg.inv(M).T).astype(int) for M in laue_ops]
        eta = idx['data']['result'].get('mosaicity_estimate_rad') or 0.5 * DEG                # measured mosaicity of the indexing (else 0.5 deg)
        sig_m = math.hypot(eta / 2.3548, self.divergence / 2.3548)
        plan = plan_strategy(it['A_red'], ops_idx, lam, resolution_m, completeness, redundancy, exposure_low_s, exposure_high_s, frame_width_rad, distance_m,
                             sig_m=sig_m)
        runs_public = [dict(omega_start_rad=float(r['omega_start']), omega_end_rad=float(r['omega_start'] + r['frames'] * frame_width_rad), kappa_rad=float(r['kappa']),
                            phi_rad=float(r['phi']), theta_rad=float(r['theta']), distance_m=float(r['distance']), exposure_s=float(r['exposure_s'])) for r in plan['runs']]
        res = dict(runs=runs_public, predicted_completeness=plan['predicted_completeness'], predicted_redundancy=plan['predicted_redundancy'],
                   unique_reflections=plan['unique_reflections'], collection_time_s=plan['collection_time_s'], laue_order_assumed=len(laue_ops), laue_from=laue_from)
        rec = self._new_record('stg', 'strategy', [record_id], dict(resolution_m=resolution_m, completeness=completeness, redundancy=redundancy,
                                                                     frame_width_rad=frame_width_rad, distance_m=distance_m), res, {})
        rec['data']['runs_public'] = runs_public
        return dict(record_id=rec['record_id'], runs=len(runs_public), predicted_completeness=res['predicted_completeness'],
                    predicted_redundancy=res['predicted_redundancy'], collection_time_s=res['collection_time_s'])

    # ---------------------------------------------------------------- frames and results
    def _cmd_frame_read(self, record_id, run_index, frame_index, binning, region_px):
        rec = self._record(record_id, ('pre_experiment', 'dataset'), completed=False)
        if run_index >= len(rec['runs']):
            raise ActionRejected('UNKNOWN_RUN')
        rr = rec['runs'][run_index]
        if frame_index >= rr['run']['frames_recorded']:
            raise ActionRejected('UNKNOWN_FRAME')
        if region_px is not None:
            x0, y0, x1, y1 = region_px
            if not (0 <= x0 < x1 <= NX and 0 <= y0 < y1 <= NY):
                raise ActionRejected('REGION_OUT_OF_RANGE')
            if (x1 - x0) * (y1 - y0) > RESULT_VALUES_MAX:
                raise ActionRejected('SIMULATION_LIMIT')
        elif ((NX + binning - 1) // binning) * ((NY + binning - 1) // binning) > RESULT_VALUES_MAX:
            raise ActionRejected('SIMULATION_LIMIT')
        counts, info = render_frame(rr, frame_index, binning, region_px, seeds=rr['seeds'])
        run = rr['run']
        sgn = 1.0 if run['omega_end'] >= run['omega_start'] else -1.0
        cap16 = min(int(counts.max()) if counts.size else 0, 2 ** HDR_BITS - 1)
        return dict(record_id=record_id, run_index=run_index, frame_index=frame_index, width=int(counts.shape[1]), height=int(counts.shape[0]), binning=info['binning'],
                    x0=info['x0'], y0=info['y0'], counts=np.minimum(counts, 2 ** HDR_BITS - 1).astype(int).tolist(), exposure_s=run['exposure_s'],
                    omega_start_rad=run['omega_start'] + sgn * frame_index * run['frame_width_rad'],
                    omega_end_rad=run['omega_start'] + sgn * (frame_index + 1) * run['frame_width_rad'], max_count=cap16)

    def _out(self, rec, max_values=4096):
        pub = {k: copy.deepcopy(rec[k]) for k in ('record_id', 'kind', 'status', 'started_s', 'finished_s', 'parent_ids', 'meta', 'warnings', 'template_source')}
        data = {}
        if rec['kind'] in ('pre_experiment', 'dataset'):
            pk = peak_table(rec['runs'], 5.0) if rec['runs'] else None
            data['runs'] = [dict(run_index=i, frames=rr['run']['frames_recorded'],
                                 spots_measured=int(np.sum(rr['obs']['i_obs'] / rr['obs']['sigma'] > 3)) if rr['obs'].get('n', 0) else 0,
                                 strong_spots=int(np.sum(rr['obs']['i_obs'] / rr['obs']['sigma'] > 5)) if rr['obs'].get('n', 0) else 0) for i, rr in enumerate(rec['runs'])]
            if pk is not None and len(pk['x']):
                n = min(len(pk['x']), max_values // 5)
                order = np.argsort(-pk['i'] / pk['sigma'])[:n]
                data['peaks'] = dict(total=int(len(pk['x'])), run=pk['run'][order].tolist(), x_px=pk['x'][order].tolist(), y_px=pk['y'][order].tolist(),
                                     frame=(pk['s'][order] / np.array([rec['runs'][r]['run']['frame_width_rad'] for r in pk['run'][order]])).tolist(),
                                     intensity=pk['i'][order].tolist())
            data.update({k: copy.deepcopy(v) for k, v in rec['data'].items()})
        else:
            data = copy.deepcopy(rec['data'])
            if rec['kind'] == 'reduction':
                it = rec['internal']
                n = min(len(it['H']), max_values // 5)
                order = np.argsort(it['d'])[::-1][:n]
                data['reflections'] = dict(total=int(len(it['H'])), hkl=it['H'][order].tolist(), intensity=it['I'][order].tolist(), sigma=it['sigma'][order].tolist(),
                                           d_m=it['d'][order].tolist())
        return dict(record=json.loads(json.dumps(pub, default=float)), data=json.loads(json.dumps(data, default=_json_default)))

    def _cmd_measurement_result(self, record_id, max_values):
        rec = self._record(record_id, completed=False)
        return self._out(rec, max_values)

    def _cmd_records_list(self):
        return dict(records=sorted(self.records))

    def _cmd_records_delete(self, record_id):
        rec = self._record(record_id, completed=False)
        if rec['status'] == 'running':
            raise ActionRejected('RECORD_IN_USE')
        del self.records[record_id]
        return dict(deleted=record_id)

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def ledger(self):
        return {k: v.value() for k, v in self.led.items()}

    def _cmd_ledger_read(self):
        return dict(ledger=self.ledger())

    def _cmd_provenance_read(self):
        return dict(libraries=dict(crystal_structure_library=dict(crystal_lib_meta(), sha256=library_sha256()[CRYSTAL_LIB_PATH.name]),
                                   xray_scattering_library=dict(sources=scatter_lib()['sources'], sha256=library_sha256()[SCATTER_LIB_PATH.name])))

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        if active:
            self.faults.add(fault)
        else:
            self.faults.discard(fault)
            if fault == 'vacuum_loss' and self.power:
                self.vac.update(state='pumping', t0=self.t, vented=True)
        self.fault_log.append(dict(t=self.t, fault=fault, active=active))
        self._event('fault', 'FAULT_' + ('ON' if active else 'OFF'), fault)
        if active:
            if fault == 'chiller_failure':
                self._xray_trip('COOLING_WATER', '冷却水故障：X射线关闭')
            elif fault == 'vacuum_loss':
                self.vac.update(state='pumping' if self.power else 'off', vented=True)
                self._xray_trip('VACUUM_LOSS', '真空泄漏：X射线关闭')
            elif fault == 'filament_failure':
                self._xray_trip('FILAMENT_FAILURE', '灯丝断：X射线关闭')
            elif fault == 'nitrogen_empty' and self.cryo['on']:
                self.cryo.update(on=False)
                self._event('cryostream', 'NITROGEN_EMPTY', '氮气耗尽：低温气流停止')
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    # ---------------------------------------------------------------- operations
    def _end_op(self, phase, code=None):
        o = self.op
        if o is None:
            return
        if o['kind'] in ('move', 'center', 'collect'):
            self.pose = self._pose_at()
            if o['kind'] == 'center' and phase == 'completed':
                self.pose['phi'] = o['extra']['start']['phi']
        self.operations[o['op']].update(phase=phase, reason=code)
        self.op = None

    def _fail_op(self, code):
        o = self.op
        if o is None:
            return
        if o['kind'] == 'collect':
            self._partial_run(o)
            self._complete_collection(o, 'failed', code)
        self._event('error', code, f'{o["kind"]}中断：{ERROR_ZH.get(code, code)}', o.get('record_id'))
        self._end_op('failed', code)

    def _partial_run(self, o):
        seg = self._segment_at(o, self.t)
        if seg is not None and seg['kind'] == 'scan':
            run = o['extra']['runs'][seg['run']]
            n = int((self.t - seg['ta']) / run['exposure_s'] + 1e-9)
            self._finish_run(o, seg['run'], min(n, run['frames']))

    def _cancel_op(self, entry):
        o = self.op
        if o is None or self.operations.get(o['op']) is not entry:
            raise ActionRejected('NOT_RUNNING')
        if o['kind'] == 'collect':
            self._partial_run(o)
            self._complete_collection(o, 'cancelled', 'CANCELLED')
        self._end_op('cancelled', 'CANCELLED')

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
        if self.power and self.sys_state == 'booting':
            c.append(self.state_t0 + BOOT_S - t)
        if self.power and self.vac['state'] == 'pumping' and 'vacuum_loss' not in self.faults:
            c.append(self.vac['t0'] + (PUMPDOWN_S if self.vac['vented'] else TURBO_S) - t)
        x = self.xr
        if x['state'] == 'starting':
            c.append(x['t0'] + SPINUP_S - t)
        elif x['state'] == 'ramping' and x['ramp'] is not None:
            c.append(x['ramp']['t0'] + x['ramp']['dur'] - t)
            if x['ramp']['t0'] + x['ramp']['d1'] > t + 1e-9:                # end of the first ramp phase (the load is linear within a phase)
                c.append(x['ramp']['t0'] + x['ramp']['d1'] - t)
        if self._generating():
            c.append(self.filament_life_s - self.led['filament_s'].value())
        if self.cryo['on'] and self.cryo['rate'] > 0:
            te = self.cryo['t0'] + abs(self.cryo['setpoint'] - self.cryo['T0']) / self.cryo['rate']
            if te > t + 1e-9:
                c.append(te - t)
        o = self.op
        if o is not None:
            c.append(o['t0'] + o['duration_s'] - t)
            if o['kind'] == 'collect':
                for seg in o['extra']['segments']:
                    if seg['tb'] > t + 1e-9:
                        c.append(seg['tb'] - t)
                        break
        if self.crystal is not None:
            c.append(60.0)                                                  # crystal temperature, ice and dose bookkeeping
        return max(0.0, min(c))

    def _advance(self, h):
        if h <= 0:
            return
        kv, ma = self._xray_kv_ma(self.t + h / 2)
        power = (ELECTRONICS_W if self.power else 0.0) + (kv * ma / GENERATOR_EFFICIENCY if self._generating() else 0.0)
        self.led['electrical_energy_j'].add(power * h)
        if self._generating():
            self.led['xray_on_s'].add(h)
            self.led['filament_s'].add(h)
            self.led['anode_s'].add(h)
        if self.cryo['on']:
            self.led['cryostream_on_s'].add(h)
        c = self.crystal
        if c is not None:
            gas = self._cryo_gas(self.t + h / 2)
            target = (gas + CRYO_OFFSET_K) if gas is not None else self.plant['room_k']
            tau = CRYO_TAU_S if gas is not None else WARM_TAU_S
            c['T'] = target + (c['T'] - target) * math.exp(-h / tau)
            if c['T'] < 250.0 and (not self._doors_closed() or 'icing' in self.faults):
                c['ice_m3'] += ICE_RATE_M3_S * self.plant['humidity'] / 0.4 * h
            if c['T'] > 273.15:
                c['ice_m3'] = 0.0
            if self.shutter:
                fl = self._flux(self.t + h / 2) * math.exp(-air_mu(self._line()['key']) * AIR_PATH_IN_M)
                sb = beam_sigma(self.divergence, self.plant['target'])
                A = substance_model(c['sid'])['A']
                mu, rho = mu_linear(c['sid'], self._line()['key'], A)
                rate = fl / (2 * PI * sb * sb) * (mu / rho) * self._line()['e1'] * EV_J
                c['dose_gy'] += rate * h
                self.led['crystal_dose_gy'].add(rate * h)
        self.t += h

    def _handle_events(self):
        t = self.t
        eps = 1e-9
        if self.power and self.sys_state == 'booting' and t >= self.state_t0 + BOOT_S - eps:
            self.sys_state = 'ready'
            self._event('power', 'READY', '控制电子就绪')
        if self.power and self.vac['state'] == 'pumping' and 'vacuum_loss' not in self.faults and t >= self.vac['t0'] + (PUMPDOWN_S if self.vac['vented'] else TURBO_S) - eps:
            self.vac.update(state='ok', vented=False)
            self._event('xray', 'VACUUM_READY', '阳极真空就绪')
        x = self.xr
        if x['state'] == 'starting' and t >= x['t0'] + SPINUP_S - eps:
            self._start_ramp(*x['target'], t=t, origin=STANDBY)
            self._event('xray', 'HV_ON', '高压开启，爬升中')
        if x['state'] == 'ramping' and x['ramp'] is not None and t >= x['ramp']['t0'] + x['ramp']['dur'] - eps:
            x['state'] = 'standby' if tuple(x['target']) == STANDBY else 'on'
            self._event('xray', 'XRAY_' + x['state'].upper(), '管压管流到达设定')
        if self._generating() and self.led['filament_s'].value() >= self.filament_life_s - eps:
            self.faults.add('filament_failure')
            self._xray_trip('FILAMENT_FAILURE', '灯丝寿命到：X射线关闭')
        if self._generating() and ('chiller_failure' in self.faults or not self.plant['chiller_available']):
            self._xray_trip('COOLING_WATER', '冷却水故障：X射线关闭')
        o = self.op
        if o is not None and o['kind'] == 'collect':
            x2 = o['extra']
            for seg in x2['segments']:
                if seg['kind'] == 'scan' and t >= seg['tb'] - eps and seg['run'] not in x2['done']:
                    self._finish_run(o, seg['run'], x2['runs'][seg['run']]['frames'])
                if seg['kind'] == 'shutter' and seg['ta'] - eps <= t and not self.shutter and t < seg['tb'] + x2['runs'][seg['run']]['frames'] * x2['runs'][seg['run']]['exposure_s']:
                    self.shutter = True
        o = self.op
        if o is not None and t >= o['t0'] + o['duration_s'] - eps:
            if o['kind'] == 'collect':
                self._complete_collection(o, 'completed')
                self._event('collection', 'COMPLETED', f'采集{o["record_id"]}完成', o['record_id'])
            elif o['kind'] == 'center' and self.crystal is not None:
                rng = _rng(self._seed(), 37, int(self.t))
                self.crystal['offset'] = rng.standard_normal(3) * CENTER_RESIDUAL_M
                self._event('crystal', 'CENTERED', '晶体已对中')
            self._end_op('completed')


def _json_default(v):
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (set, tuple)):
        return list(v)
    return str(v)


# ---------------------------------------------------------------- session (identity, access, idempotency)
_RECORD_ARGS = {'measurement.result': ('record_id',), 'frame.read': ('record_id',), 'records.delete': ('record_id',), 'processing.index': ('record_id',),
                'processing.reduce': ('record_id', 'index_record_id'), 'processing.what_is_this': ('record_id',), 'strategy.calculate': ('record_id',),
                'data.collect': ('strategy_record_id',)}
_DATA_GUARDED = set(_RECORD_ARGS)
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED
                    and k not in DECLARED}
_REPLAYABLE_QUERIES = {'measurement.result', 'frame.read', 'records.list', 'events.read', 'provenance.read', 'camera.view', 'ledger.read'}
_CREATES_RECORD = {'processing.index', 'processing.reduce', 'processing.what_is_this', 'strategy.calculate'}


def _response_bytes(result):
    try:
        return len(json.dumps(result, default=float))
    except (TypeError, ValueError):
        return 0


class RigakuXtalabsynergySession:
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
        meta = copy.deepcopy(export_rigaku_xtalabsynergy_contract_metadata())
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
        if self._role == 'agent' and 'template_source' in rec:
            ts = rec['template_source']
            rec['template_source'] = dict(ts, substance_id=None, cod_id=None, doi=None)
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
            values['collection.frames_done'] = None
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
            if capability_id in ('safety.emergency_stop',):
                return                                                  # anyone may press the emergency stop
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED:
            for arg in _RECORD_ARGS[capability_id]:
                rid = arguments.get(arg)
                if rid is not None and self._foreign(rid):
                    raise ActionRejected('ACCESS_DENIED')
            owner = h._owners.get(arguments.get('record_id'))
            if capability_id == 'records.delete' and owner is not None and owner != self._principal:
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
                if digest is None or old != digest:
                    raise ActionRejected('REQUEST_CONFLICT')
                if response is not None:
                    return copy.deepcopy(response)
            if not isinstance(capability_id, str) or capability_id not in CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if capability_id in UNSUPPORTED:
                raise ActionRejected('UNSUPPORTED')
            if capability_id in DECLARED:
                raise ActionRejected('NOT_IMPLEMENTED')
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
                elif capability_id == 'ledger.read':
                    result = dict(ledger={k: v for k, v in result['ledger'].items() if k not in AGENT_HIDDEN_LEDGER})
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
                rid = h.operations[operation].get('record_id')
                if rid:
                    h._owners[rid] = self._principal
            if capability_id in _CREATES_RECORD:
                h._owners[result['record_id']] = self._principal
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=operation,
                            status='accepted' if operation else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                            result=copy.deepcopy(result), observations={}, error=None)
            h._requests[key] = (digest, copy.deepcopy(response))
            if capability_id in _REPLAYABLE_QUERIES and _response_bytes(result) > LARGE_RESPONSE_BYTES:
                big = h._large_requests.setdefault(self._principal, [])
                big.append(key)
                while len(big) > LARGE_RESPONSE_KEEP:
                    old_key = big.pop(0)
                    if old_key in h._requests and old_key != key:
                        h._requests[old_key] = (h._requests[old_key][0], None)     # keep the request digest, drop the large result
            return response
        except ActionRejected as exc:
            code = str(exc)
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None,
                            status='unsupported' if code in ('UNSUPPORTED', 'NOT_IMPLEMENTED') else 'rejected', state_revision=h.revision,
                            clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                            error=dict(code=code, message=CAP_ERROR_ZH.get((capability_id, code), ERROR_ZH.get(code, code)), retryable=False,
                                       side_effects='unknown' if code == 'DEVICE_ERROR' else 'none'))
            if key is not None and digest is not None and code != 'REQUEST_CONFLICT' and (
                    key not in h._requests or (h._requests[key][0] == digest and h._requests[key][1] is None)):
                h._requests[key] = (digest, copy.deepcopy(response))            # a conflict never rebinds the request id
            return response

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        raise ActionRejected('ACCESS_DENIED')                       # settings are written through capabilities

    def poll(self, operation_id):
        h = self._host
        if not isinstance(operation_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        op = h.operations.get(operation_id)
        if op is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        if h._owners.get(operation_id) != self._principal and self._role != 'evaluator':
            raise ActionRejected('ACCESS_DENIED')
        if op.get('record_id') in h.records:
            res = h._out(h.records[op['record_id']], 1024)
            res['record'] = self._redact(res['record'])
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
    'NOT_IMPLEMENTED': '仪器有该功能，本资产未实现', 'ACCESS_DENIED': '无权访问（评测能力或槽、另一个agent的记录或操作）', 'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作不在进行中',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_ON': '通电、X射线开、有操作或测角头上有晶体时不能重新声明实验室条件（或更换灯丝需先断电）',
    'POWER_OFF': '控制电子未通电（或操作中断电）', 'NOT_READY': '控制电子初始化中', 'OPERATION_RUNNING': '有测角仪移动、对中或数据采集在进行',
    'EMERGENCY_STOP': '急停锁定（先释放急停，再关门按Doors OK）', 'MOTION_ENABLE_REQUIRED': '门开着：须同时按住两个Motion Enable键才能运动',
    'DUPLICATE_SAMPLE': '样品ID已存在', 'TOO_MANY_SAMPLES': '样品数量超过上限（12）', 'DOORS_OPEN': '门没有全部关上', 'SETTING_STEP': '管压须为1 kV、管流须为1 mA的整数倍',
    'POWER_LIMIT': '管压×管流超过1.2 kW', 'KEY_OFF': 'X射线钥匙不在ON', 'SAFETY_NOT_ARMED': '安全回路未接通（关好门并按Doors OK）', 'COOLING_WATER': '冷却水不可用（冷水机故障或未接）',
    'VACUUM_NOT_READY': '阳极真空未就绪（抽真空中或真空泄漏）', 'FILAMENT_FAILURE': '灯丝故障或寿命到（需更换灯丝）', 'XRAY_ALREADY_ON': 'X射线已开启（用xray.set改设定）',
    'XRAY_OFF': 'X射线未开启（或采集中X射线关闭）', 'XRAY_NOT_STABLE': 'X射线未在稳定或待机状态', 'THRESHOLD_OUT_OF_RANGE': '阈值超出0.3–0.9倍Kα1能量',
    'COLLISION_PREDICTED': '碰撞模型预测途中或终点会碰撞（测角头/κ臂/探测器与准直管、光源、光束挡、低温头、摄像头或支撑柱）', 'NOT_MOVING': '没有测角仪移动在进行',
    'DOOR_CLOSED': '主门没有打开（装样与对中须打开主门）', 'NOT_AT_MOUNT_POSITION': '测角仪不在装样姿态（κ偏离0超过5°或探测器距离小于60 mm）',
    'UNKNOWN_SAMPLE': '样品不存在', 'SAMPLE_NOT_ON_BENCH': '样品不在实验台上', 'CRYSTAL_MOUNTED': '测角头上已有晶体', 'NO_CRYSTAL': '测角头上没有晶体',
    'NITROGEN_UNAVAILABLE': '低温装置没有氮气/干燥气', 'RECORDS_FULL': '记录已满（100条）：删除记录后再试', 'RUN_NOT_MULTIPLE_OF_FRAME': 'ω扫描范围须为帧宽的整数倍（每段1–3600帧）',
    'SCAN_TOO_FAST': '扫描速度（帧宽/曝光）超过10°/s', 'SIMULATION_LIMIT': '超出仿真上限（仿真边界，不是仪器故障）', 'UNKNOWN_RECORD': '记录不存在',
    'RECORD_NOT_SUITABLE': '记录种类或状态不适用', 'RECORD_IN_USE': '记录属于进行中的操作', 'NOT_INDEXED': '记录没有成功的指标化结果', 'TOO_FEW_REFLECTIONS': '可积分的反射太少（<20）',
    'RESOLUTION_UNREACHABLE': '目标分辨率小于λ/2', 'UNKNOWN_RUN': '扫描段不存在', 'UNKNOWN_FRAME': '帧不存在', 'REGION_OUT_OF_RANGE': '区域超出探测器',
    'SECURITY_MODE': '采集或门开运动中打开了门：进入安全模式（快门关闭、运动停止）', 'MOTION_ENABLE_RELEASED': '门开着时松开了Motion Enable键：运动停止',
    'CANCELLED': '操作被取消', 'VACUUM_LOSS': '真空泄漏',
}
_S = ['operation.id', 'operation.kind', 'operation.progress', 'operation.remaining_s', 'collection.frames_done', 'goniometer.moving']
_POSE = ['goniometer.omega_rad', 'goniometer.kappa_rad', 'goniometer.phi_rad', 'detector.theta_rad', 'detector.distance_m', 'goniometer.moving']
_XR = ['xray.state', 'xray.generating', 'shutter.open', 'xray.voltage_v', 'xray.current_a', 'truth.flux_ph_s']
EFFECTS = {
    'plant.configure': ['plant.configured', 'plant.target', 'cryostream.state', 'cryostream.setpoint_k', 'cryostream.gas_temperature_k', 'detector.threshold_ev',
                        'truth.filament_remaining_s'], 'sample.provide': ['sample.bench_ids'],
    'power.set': ['power.enabled', 'system.state', 'safety.armed', 'xray.vacuum_ready'] + _XR + _S + ['goniometer.moving'],
    'power.press_button': ['power.enabled', 'system.state', 'safety.armed', 'xray.vacuum_ready'] + _XR + _S + ['goniometer.moving'],
    'safety.doors_ok': ['safety.armed'], 'safety.emergency_stop': ['safety.estop_latched', 'safety.armed'] + _XR + _S + ['goniometer.moving'],
    'safety.reset_emergency_stop': ['safety.estop_latched'], 'xray.set_key': ['xray.key_angle_rad', 'xray.key_on'] + _XR + _S,
    'xray.on': ['xray.state', 'xray.target'], 'xray.set': ['xray.state', 'xray.target'], 'xray.standby': ['xray.state', 'xray.target'],
    'xray.off': _XR + _S, 'shutter.open': ['shutter.open', 'truth.flux_ph_s'], 'shutter.close': ['shutter.open', 'truth.flux_ph_s'],
    'optics.set_divergence': ['optics.divergence_rad', 'truth.flux_ph_s'],
    'detector.set_threshold': ['detector.threshold_ev'], 'goniometer.move': _S + ['goniometer.moving'], 'goniometer.mount_position': _S + ['goniometer.moving'],
    'goniometer.stop': _S + ['goniometer.moving'], 'motion_enable.hold': ['motion_enable.held', 'goniometer.moving'] + _S,
    'door.open': ['door.left_angle_rad', 'door.right_angle_rad', 'safety.doors_closed', 'safety.armed', 'shutter.open', 'truth.flux_ph_s'] + _S,
    'door.close': ['door.left_angle_rad', 'door.right_angle_rad', 'safety.doors_closed'], 'crystal.mount': ['crystal.mounted_sample_id', 'sample.bench_ids', 'truth.crystal'],
    'crystal.unmount': ['crystal.mounted_sample_id', 'sample.bench_ids', 'truth.crystal'], 'crystal.center': _S + ['goniometer.moving'],
    'maintenance.replace_filament': ['truth.filament_remaining_s', 'truth.faults'], 'cryostream.set': ['cryostream.setpoint_k', 'cryostream.state', 'cryostream.gas_temperature_k'],
    'cryostream.stop': ['cryostream.state', 'cryostream.setpoint_k', 'cryostream.gas_temperature_k'],
    'experiment.pre_experiment': _S + ['goniometer.moving', 'collection.frames_done'], 'data.collect': _S + ['goniometer.moving', 'collection.frames_done'],
    'strategy.calculate': ['result.last_record_id'], 'processing.index': ['result.last_record_id'], 'processing.reduce': ['result.last_record_id'],
    'processing.what_is_this': ['result.last_record_id'], 'records.delete': ['result.last_record_id'],
    'fault.inject': ['truth.faults', 'xray.vacuum_ready', 'cryostream.state', 'cryostream.setpoint_k', 'cryostream.gas_temperature_k'] + _XR + _S, 'sim.step': ['clock.time_s'],
}
ON_STEP = {
    'power.set': ['system.state', 'xray.vacuum_ready'], 'power.press_button': ['system.state', 'xray.vacuum_ready'],
    'xray.on': ['xray.state', 'xray.generating', 'xray.voltage_v', 'xray.current_a'], 'xray.set': ['xray.state', 'xray.voltage_v', 'xray.current_a'],
    'xray.standby': ['xray.state', 'xray.voltage_v', 'xray.current_a'], 'shutter.open': ['truth.crystal'],
    'goniometer.move': _POSE + _S, 'goniometer.mount_position': _POSE + _S, 'crystal.center': ['goniometer.phi_rad', 'goniometer.moving', 'truth.crystal'] + _S,
    'cryostream.set': ['cryostream.gas_temperature_k', 'cryostream.state', 'truth.crystal'], 'cryostream.stop': ['truth.crystal'],
    'experiment.pre_experiment': _POSE + _S + ['shutter.open', 'collection.frames_done', 'result.last_record_id', 'truth.crystal'],
    'data.collect': _POSE + _S + ['shutter.open', 'collection.frames_done', 'result.last_record_id', 'truth.crystal'],
    'maintenance.replace_filament': ['xray.vacuum_ready'],
    'sim.step': ['clock.time_s', 'system.state', 'xray.state', 'xray.generating', 'xray.voltage_v', 'xray.current_a', 'xray.vacuum_ready', 'shutter.open', 'cryostream.state',
                 'cryostream.gas_temperature_k', 'collection.frames_done', 'result.last_record_id', 'truth.crystal', 'truth.flux_ph_s', 'truth.filament_remaining_s',
                 'truth.faults'] + _POSE + _S,
}
ON_STEP_ZH = {
    'power.set': '通电后约20 s就绪；涡轮泵约30 s（放气后约5 min）后真空就绪', 'power.press_button': '同power.set',
    'xray.on': '约30 s阳极加速与灯丝预热后开高压，从20 kV/10 mA按2 kV/s、2 mA/s爬升到设定', 'xray.set': '按爬升速率变到新设定', 'xray.standby': '按爬升速率降到待机',
    'shutter.open': '快门开着时晶体累积剂量', 'goniometer.move': '各轴按额定速度同时运动', 'goniometer.mount_position': '同goniometer.move',
    'crystal.center': '四次90° φ转动与旋钮平移；结束时残余偏心约8 µm', 'cryostream.set': '气流温度按速率变化，晶体约5 s跟随', 'cryostream.stop': '晶体约30 s回到室温',
    'experiment.pre_experiment': '逐段移动、开快门、扫描；每段结束写入观测，全部结束后自动指标化', 'data.collect': '逐段移动、开快门、扫描；每段结束写入观测',
    'maintenance.replace_filament': '下次开机需抽真空约5 min', 'sim.step': '推进时钟与全部过程',
}
_R = ['power.enabled', 'system.state']
PRECONDITIONS = {
    'sample.provide': ('已声明实验室条件', ['plant.configured']),
    'power.set': ('已声明实验室条件', ['plant.configured']), 'power.press_button': ('已声明实验室条件', ['plant.configured']),
    'safety.doors_ok': ('控制电子就绪、无急停、两扇门都关着', _R + ['safety.estop_latched', 'safety.doors_closed']),
    'xray.on': ('X射线关、控制电子就绪、无急停、钥匙ON、安全回路接通、冷却水与真空正常、灯丝正常、管压管流步长与1.2 kW功率限制',
                _R + ['xray.state', 'safety.estop_latched', 'xray.key_on', 'safety.armed', 'xray.vacuum_ready']),
    'xray.set': ('X射线开启、控制电子就绪、不在采集中、步长与功率限制', _R + ['xray.state', 'operation.kind']), 'xray.standby': ('同xray.set', _R + ['xray.state']),
    'shutter.open': ('X射线稳定或待机、安全回路接通、不在采集中', _R + ['xray.state', 'safety.armed', 'operation.kind']),
    'shutter.close': ('不在采集中（采集自动开闭快门）', ['operation.kind']), 'optics.set_divergence': ('控制电子就绪、不在采集中', _R + ['operation.kind']),
    'detector.set_threshold': ('控制电子就绪、不在采集中、阈值0.3–0.9倍Kα1能量', _R + ['operation.kind']),
    'goniometer.move': ('控制电子就绪、无操作、无急停；门开着时按住Motion Enable；碰撞模型允许整条路径', _R + ['operation.id', 'safety.estop_latched', 'safety.doors_closed',
                                                                                                'motion_enable.held']),
    'goniometer.mount_position': ('同goniometer.move', _R + ['operation.id', 'safety.estop_latched', 'safety.doors_closed', 'motion_enable.held']),
    'goniometer.stop': ('有测角仪移动在进行', ['operation.kind']),
    'crystal.mount': ('样品在实验台上、测角头空、主门开≥45°、无操作、装样姿态（κ≈0、距离≥60 mm）', ['sample.bench_ids', 'crystal.mounted_sample_id', 'door.left_angle_rad',
                                                                                    'operation.id', 'goniometer.kappa_rad', 'detector.distance_m']),
    'crystal.unmount': ('测角头上有晶体、主门开、无操作、装样姿态', ['crystal.mounted_sample_id', 'door.left_angle_rad', 'operation.id', 'goniometer.kappa_rad', 'detector.distance_m']),
    'crystal.center': ('控制电子就绪（摄像头）、有晶体、主门开、按住Motion Enable、装样姿态、无操作', _R + ['crystal.mounted_sample_id', 'door.left_angle_rad', 'motion_enable.held',
                                                                                          'goniometer.kappa_rad', 'detector.distance_m', 'operation.id']),
    'maintenance.replace_filament': ('断电', ['power.enabled']), 'cryostream.set': ('有氮气/干燥气', ['plant.configured']),
    'camera.view': ('控制电子就绪', _R),
    'experiment.pre_experiment': ('控制电子就绪、无操作、无急停、X射线稳定、安全回路接通、有晶体；三段扫描都在碰撞模型允许范围内',
                                  _R + ['operation.id', 'safety.estop_latched', 'xray.state', 'safety.armed', 'crystal.mounted_sample_id']),
    'data.collect': ('同pre_experiment；段宽为帧宽整数倍、扫描速度≤10°/s、总帧数≤20000；给定runs或strategy记录之一', _R + ['operation.id', 'safety.estop_latched', 'xray.state',
                                                                                                'safety.armed', 'crystal.mounted_sample_id']),
    'strategy.calculate': ('指标化记录成功', ['result.last_record_id']), 'processing.index': ('预实验或数据集记录已结束', ['result.last_record_id']),
    'processing.reduce': ('数据集已结束且能指标化', ['result.last_record_id']), 'processing.what_is_this': ('指标化或还原记录', ['result.last_record_id']),
    'frame.read': ('记录存在、段与帧已读出', ['result.last_record_id']), 'records.delete': ('记录不在进行中', ['operation.id']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE', 'DEVICE_ERROR']
_RE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY']
_COLL = _RE + ['OPERATION_RUNNING', 'EMERGENCY_STOP', 'XRAY_NOT_STABLE', 'SAFETY_NOT_ARMED', 'NO_CRYSTAL', 'RECORDS_FULL', 'COLLISION_PREDICTED', 'SCAN_TOO_FAST',
               'SECURITY_MODE', 'XRAY_OFF', 'CANCELLED']
CAP_ERRORS = {
    'plant.configure': ['POWER_ON'], 'sample.provide': ['PLANT_REQUIRED', 'DUPLICATE_SAMPLE', 'TOO_MANY_SAMPLES'], 'power.set': ['PLANT_REQUIRED'],
    'power.press_button': ['PLANT_REQUIRED'], 'safety.doors_ok': _RE + ['EMERGENCY_STOP', 'DOORS_OPEN'], 'safety.emergency_stop': ['PLANT_REQUIRED'],
    'safety.reset_emergency_stop': ['PLANT_REQUIRED'], 'xray.set_key': ['PLANT_REQUIRED'],
    'xray.on': _RE + ['SETTING_STEP', 'POWER_LIMIT', 'XRAY_ALREADY_ON', 'EMERGENCY_STOP', 'KEY_OFF', 'SAFETY_NOT_ARMED', 'COOLING_WATER', 'VACUUM_NOT_READY', 'FILAMENT_FAILURE'],
    'xray.set': _RE + ['SETTING_STEP', 'POWER_LIMIT', 'XRAY_OFF', 'OPERATION_RUNNING'], 'xray.standby': _RE + ['XRAY_OFF', 'OPERATION_RUNNING'], 'xray.off': ['PLANT_REQUIRED'],
    'shutter.open': _RE + ['XRAY_NOT_STABLE', 'SAFETY_NOT_ARMED', 'OPERATION_RUNNING'], 'shutter.close': ['PLANT_REQUIRED', 'OPERATION_RUNNING'],
    'optics.set_divergence': _RE + ['OPERATION_RUNNING'], 'detector.set_threshold': _RE + ['OPERATION_RUNNING', 'THRESHOLD_OUT_OF_RANGE'],
    'goniometer.move': _RE + ['OPERATION_RUNNING', 'EMERGENCY_STOP', 'MOTION_ENABLE_REQUIRED', 'COLLISION_PREDICTED', 'SECURITY_MODE', 'MOTION_ENABLE_RELEASED', 'CANCELLED'],
    'goniometer.mount_position': _RE + ['OPERATION_RUNNING', 'EMERGENCY_STOP', 'MOTION_ENABLE_REQUIRED', 'COLLISION_PREDICTED', 'SECURITY_MODE', 'MOTION_ENABLE_RELEASED',
                                        'CANCELLED'],
    'goniometer.stop': ['PLANT_REQUIRED', 'NOT_MOVING'], 'motion_enable.hold': ['PLANT_REQUIRED'], 'door.open': ['PLANT_REQUIRED'], 'door.close': ['PLANT_REQUIRED'],
    'crystal.mount': ['PLANT_REQUIRED', 'UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'CRYSTAL_MOUNTED', 'DOOR_CLOSED', 'OPERATION_RUNNING', 'NOT_AT_MOUNT_POSITION'],
    'crystal.unmount': ['PLANT_REQUIRED', 'NO_CRYSTAL', 'DOOR_CLOSED', 'OPERATION_RUNNING', 'NOT_AT_MOUNT_POSITION'],
    'crystal.center': _RE + ['NO_CRYSTAL', 'DOOR_CLOSED', 'OPERATION_RUNNING', 'NOT_AT_MOUNT_POSITION', 'MOTION_ENABLE_REQUIRED', 'MOTION_ENABLE_RELEASED', 'EMERGENCY_STOP',
                             'CANCELLED'],
    'maintenance.replace_filament': ['PLANT_REQUIRED', 'POWER_ON'], 'cryostream.set': ['PLANT_REQUIRED', 'NITROGEN_UNAVAILABLE'], 'cryostream.stop': ['PLANT_REQUIRED'],
    'camera.view': _RE, 'experiment.pre_experiment': _COLL, 'data.collect': _COLL + ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'RUN_NOT_MULTIPLE_OF_FRAME', 'SIMULATION_LIMIT'],
    'strategy.calculate': ['PLANT_REQUIRED', 'UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'RECORD_IN_USE', 'NOT_INDEXED', 'SCAN_TOO_FAST', 'RESOLUTION_UNREACHABLE', 'RECORDS_FULL'],
    'processing.index': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'RECORD_IN_USE', 'RECORDS_FULL'],
    'processing.reduce': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'RECORD_IN_USE', 'NOT_INDEXED', 'TOO_FEW_REFLECTIONS', 'RECORDS_FULL'],
    'processing.what_is_this': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'RECORD_IN_USE', 'NOT_INDEXED', 'RECORDS_FULL'],
    'frame.read': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'UNKNOWN_RUN', 'UNKNOWN_FRAME', 'REGION_OUT_OF_RANGE', 'SIMULATION_LIMIT'],
    'measurement.result': ['UNKNOWN_RECORD'], 'records.delete': ['UNKNOWN_RECORD', 'RECORD_IN_USE'], 'fault.inject': ['PLANT_REQUIRED'],
}
_PARTIAL = {'POWER_OFF', 'EMERGENCY_STOP', 'SECURITY_MODE', 'XRAY_OFF', 'CANCELLED', 'MOTION_ENABLE_RELEASED'}
CAP_ERROR_ZH = {
    ('data.collect', 'SIMULATION_LIMIT'): '总帧数超过仿真上限20000', ('frame.read', 'SIMULATION_LIMIT'): '读出的值超过65536（整帧须合并≥4，或给更小的区域）',
    ('data.collect', 'SECURITY_MODE'): '采集中开门：安全模式（快门关、采集停，记录以failed结束，保留已读出的帧）',
    ('experiment.pre_experiment', 'SECURITY_MODE'): '预实验中开门：安全模式（记录以failed结束，保留已读出的帧，不自动指标化）',
    ('data.collect', 'CANCELLED'): '取消：保留已读出的帧，记录以cancelled结束', ('experiment.pre_experiment', 'CANCELLED'): '取消：保留已读出的帧，不自动指标化',
    ('goniometer.move', 'CANCELLED'): '取消（goniometer.stop）：停在当时位置', ('crystal.center', 'CANCELLED'): '取消：对中未完成，偏心不变',
    ('experiment.pre_experiment', 'COLLISION_PREDICTED'): '在该距离下碰撞模型找不到三段可用的κ/φ设置（增大距离）',
}
MODEL_NONE_ZH = {
    'plant.configure': '实验室条件没有可见模型', 'sample.provide': '晶体在实验台上，场景中没有晶体几何', 'crystal.mount': '晶体与晶体环很小（测角头针尖为接口点crystal_position）',
    'crystal.unmount': '同crystal.mount', 'maintenance.replace_filament': '灯丝在阳极罩内不可见', 'cryostream.set': '低温气流不可见', 'cryostream.stop': '同cryostream.set',
    'optics.set_divergence': '狭缝在光学盒内不可见', 'detector.set_threshold': '探测器设置没有可见变化', 'xray.set': '管压管流没有可见变化（X射线灯随状态亮灭）',
    'strategy.calculate': '软件计算', 'processing.index': '软件计算', 'processing.reduce': '软件计算', 'processing.what_is_this': '软件计算', 'records.delete': '只删除软件中的记录',
    'fault.inject': '注入故障本身没有可见变化', 'motion_enable.hold': '按键被按住没有关节', 'camera.view': '只读取摄像头画面',
}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持或未实现：没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节（两扇门、ω/κ/φ、2θ臂与探测器滑架、X射线钥匙、急停按钮）与指示灯（电源、X射线、快门、Doors OK、舱内警示灯、'
                                   '照明与对中屏）（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型' + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {
    'goniometer.move': ('各轴按额定速度同时运动，最慢的轴到达目标时完成', '取消（goniometer.stop）、急停、断电、门开且未按住Motion Enable或松开Motion Enable时停在当时位置（完成的部分保留）'),
    'goniometer.mount_position': ('同goniometer.move', '同goniometer.move'),
    'crystal.center': ('约90 s后完成：晶体残余偏心约8 µm，φ回到原角', '取消、松开Motion Enable、急停或断电时对中未完成，偏心不变'),
    'experiment.pre_experiment': ('三段扫描全部读出、自动指标化后记录completed', '取消、开门（安全模式）、X射线关闭、急停或断电时保留已读出的帧（当前段按已读出的帧），不自动指标化'),
    'data.collect': ('全部段读出后记录completed', '取消、开门（安全模式）、X射线关闭、急停或断电时保留已读出的帧（当前段按已读出的帧）'),
}
RESOURCES = {'goniometer.move': ['goniometer', 'detector_arm', 'host_clock'], 'goniometer.mount_position': ['goniometer', 'detector_arm', 'host_clock'],
             'goniometer.stop': ['goniometer'], 'crystal.center': ['goniometer', 'camera', 'operator_hands', 'host_clock'],
             'experiment.pre_experiment': ['goniometer', 'detector_arm', 'source', 'shutter', 'detector', 'host_clock'],
             'data.collect': ['goniometer', 'detector_arm', 'source', 'shutter', 'detector', 'host_clock'], 'power.set': ['mains', 'electronics'],
             'power.press_button': ['mains', 'electronics'], 'safety.doors_ok': ['safety_circuit'], 'safety.emergency_stop': ['safety_circuit'],
             'safety.reset_emergency_stop': ['safety_circuit'], 'xray.set_key': ['safety_circuit'], 'xray.on': ['generator', 'chiller', 'vacuum'], 'xray.set': ['generator'],
             'xray.standby': ['generator'], 'xray.off': ['generator'], 'shutter.open': ['shutter'], 'shutter.close': ['shutter'], 'optics.set_divergence': ['slit'],
             'detector.set_threshold': ['detector'], 'motion_enable.hold': ['operator_hands'], 'door.open': ['doors'], 'door.close': ['doors'],
             'crystal.mount': ['goniometer_head', 'operator_hands', 'bench'], 'crystal.unmount': ['goniometer_head', 'operator_hands', 'bench'],
             'maintenance.replace_filament': ['generator', 'service'], 'cryostream.set': ['cryostream', 'nitrogen'], 'cryostream.stop': ['cryostream'], 'camera.view': ['camera'],
             'strategy.calculate': ['computer'], 'processing.index': ['computer'], 'processing.reduce': ['computer'], 'processing.what_is_this': ['computer', 'database'],
             'frame.read': ['computer'], 'measurement.result': ['computer'], 'records.list': ['computer'], 'records.delete': ['computer'], 'events.read': ['computer'],
             'ledger.read': ['computer'], 'provenance.read': ['computer'], 'plant.configure': ['room'], 'sample.provide': ['bench'], 'fault.inject': ['evaluator'],
             'sim.step': ['host_clock']}
OBSERVES = {
    'power.set': ['power.enabled', 'system.state'], 'power.press_button': ['power.enabled', 'system.state'], 'safety.doors_ok': ['safety.armed'],
    'safety.emergency_stop': ['safety.estop_latched', 'xray.state'], 'safety.reset_emergency_stop': ['safety.estop_latched'], 'xray.set_key': ['xray.key_on', 'xray.state'],
    'xray.on': ['xray.state', 'xray.voltage_v', 'xray.current_a'], 'xray.set': ['xray.state', 'xray.voltage_v', 'xray.current_a'], 'xray.standby': ['xray.state'],
    'xray.off': ['xray.state'], 'shutter.open': ['shutter.open'], 'shutter.close': ['shutter.open'], 'optics.set_divergence': ['optics.divergence_rad'],
    'detector.set_threshold': ['detector.threshold_ev'], 'goniometer.move': _POSE + ['operation.id'], 'goniometer.mount_position': _POSE + ['operation.id'],
    'goniometer.stop': ['goniometer.moving'], 'motion_enable.hold': ['motion_enable.held'], 'door.open': ['door.left_angle_rad', 'door.right_angle_rad', 'safety.armed'],
    'door.close': ['door.left_angle_rad', 'door.right_angle_rad', 'safety.doors_closed'], 'crystal.mount': ['crystal.mounted_sample_id'],
    'crystal.unmount': ['crystal.mounted_sample_id', 'sample.bench_ids'], 'crystal.center': ['operation.id', 'goniometer.phi_rad'],
    'cryostream.set': ['cryostream.state', 'cryostream.gas_temperature_k'], 'cryostream.stop': ['cryostream.state'],
    'experiment.pre_experiment': ['operation.id', 'collection.frames_done', 'result.last_record_id'], 'data.collect': ['operation.id', 'collection.frames_done', 'result.last_record_id'],
    'strategy.calculate': ['result.last_record_id'], 'processing.index': ['result.last_record_id'], 'processing.reduce': ['result.last_record_id'],
    'processing.what_is_this': ['result.last_record_id'], 'records.delete': ['result.last_record_id'],
}
_WB = 'user_workbook'
_BRO = 'manufacturer_primary'
_PP = 'manufacturer_product_page'
_DET = 'manufacturer_detector_page'
_SRC = 'manufacturer_source_page'
_LW = 'catalogue_labwrench_micromax'
_JRN = 'manufacturer_journal'
_APP = 'manufacturer_app_note'
_UI = 'user_instructions'
_CRY = 'manufacturer_cryostream'
_DB = 'structure_database'
_SCT = 'scattering_tables'
_SIM = 'simulation_assumption'
CAP_SOURCES = {
    'plant.configure': [_PP, _BRO, _SIM], 'sample.provide': [_DB, _WB, _SIM], 'power.set': [_PP, _SIM], 'power.press_button': [_JRN, _SIM], 'safety.doors_ok': [_UI, _JRN],
    'safety.emergency_stop': [_JRN, _SIM], 'safety.reset_emergency_stop': [_SIM], 'xray.set_key': [_JRN, _SIM], 'xray.on': [_PP, _BRO, _SRC, _LW, _SIM],
    'xray.set': [_LW, _SRC, _SIM], 'xray.standby': [_SRC, _SIM], 'xray.off': [_PP, _SIM], 'shutter.open': [_JRN, _SIM], 'shutter.close': [_JRN, _SIM],
    'optics.set_divergence': [_BRO, _PP, _WB, _SIM], 'detector.set_threshold': [_BRO, _JRN, _DET, _SIM], 'goniometer.move': [_JRN, _PP, _UI, _SIM],
    'goniometer.mount_position': [_UI, _SIM], 'goniometer.stop': [_SIM], 'motion_enable.hold': [_UI], 'door.open': [_JRN, _UI], 'door.close': [_JRN, _UI],
    'crystal.mount': [_UI, _WB, _SIM], 'crystal.unmount': [_UI, _SIM], 'crystal.center': [_UI, _SIM], 'maintenance.replace_filament': [_JRN, _PP, _SIM],
    'cryostream.set': [_CRY, _PP, _WB, _SIM], 'cryostream.stop': [_CRY, _SIM], 'camera.view': [_UI, _DB, _SIM],
    'experiment.pre_experiment': [_UI, _BRO, _JRN, _DB, _SCT, _SIM], 'data.collect': [_BRO, _JRN, _PP, _DET, _UI, _WB, _DB, _SCT, _SIM],
    'strategy.calculate': [_BRO, _UI, _SIM], 'processing.index': [_UI, _BRO, _SIM], 'processing.reduce': [_UI, _BRO, _SIM], 'processing.what_is_this': [_BRO, _PP, _DB, _SCT, _SIM],
    'frame.read': [_DET, _BRO, _JRN, _SIM], 'measurement.result': [_WB, _SIM], 'records.list': [_SIM], 'records.delete': [_SIM], 'events.read': [_SIM], 'ledger.read': [_SIM],
    'provenance.read': [_DB, _SCT], 'fault.inject': [_SIM], 'sim.step': [_SIM],
    **{k: [_BRO, _PP] for k in list(DECLARED) + list(UNSUPPORTED)}}
PARAM_ZH = {'room_k': '室温（晶体不开低温时的温度）', 'humidity': '相对湿度（冷晶体开门时结霜的速率）', 'target': '光源靶材（Cu或Mo，决定波长、通量与反常散射）',
            'chiller_available': '冷水机是否可用（X射线的前提）', 'nitrogen_available': '低温装置的氮气/干燥气是否可用', 'noise_seed': '噪声种子（灯丝寿命、装样取向与偏心、计数噪声）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.xtalab_synergy_r.limitations。'
CAP_LIMITS = {
    'xray.on': ['管压管流范围、步长与1.2 kW（厂家）；30 s加速/预热与2 kV/s、2 mA/s爬升、待机20 kV/10 mA为工程值；通量按I (V−V_K)^1.6相对参考点（Cu 40 kV/30 mA 2.5e9 ph/s，'
                'Mo 50 kV/24 mA 8e8 ph/s，10 mrad；工程值，厂家只说明为微焦点封闭管的十倍）。'],
    'optics.set_divergence': ['1–10 mrad连续可调（厂家）；通量∝(d/10 mrad)²、晶体处光斑FWHM=50 µm+6 µm/mrad为工程值。'],
    'detector.set_threshold': ['阈值范围0.3–0.9倍Kα1、电荷分配损失1−0.6(thr/E−0.5)、荧光接受的3 %能量分辨为工程值；传感器厚度未公布（按0.3 mm硅计算量子效率）。'],
    'goniometer.move': ['各轴速度（厂家）与距离10 mm/s（工程）；碰撞模型为测角头、φ座、κ臂与电机、ω支架、探测器与滑架对准直管、光源、光束挡、低温头、摄像头与支撑柱的胶囊与长方体，'
                        '3 mm余量（工程）；加速度与编码器误差未建模。'],
    'crystal.mount': ['装样后取向随机、偏心0.1–0.35 mm（工程）；晶体环与冷冻油只作为背景来源。'],
    'crystal.center': ['对中时间90 s与残余偏心8 µm为工程值；摄像头读数5 µm量化、3 µm抖动（工程）。'],
    'cryostream.set': ['80–400 K、0.1 K稳定度（厂家产品页检索摘要，页面未缓存）；升降温速率上限0.1 K/s（360 K/h）、晶体跟随时间常数5 s与1 K温差为工程值；冷晶体在开门时按湿度结霜（工程速率）。'],
    'experiment.pre_experiment': ['三段20°扫描（θ=0，κ/φ按碰撞模型选）后自动找峰（I/σ>5）与指标化；是CrysAlisPro预实验的简化，不含自动曝光选择；自动指标化出现分析错误时auto_index记reason=INDEXING_ERROR并写fault事件，采集照常结束。',
                                  '约化晶胞很小的无机晶体用Cu时θ=0的三段落下的斑点常少于指标化所需的10个：ZnO、NaCl、CaF₂、Si、ZnS、MgO、LiF、刚玉、锐钛矿、金红石'
                                  '（约化晶胞≤85 Å³）多报TOO_FEW_PEAKS（距离较远时NO_PEAKS）；KCl、CsCl、SrTiO₃、NaNO₃在门限附近，随取向时而直接指标化；方解石、石英（约化晶胞约'
                                  '113–123 Å³）通常可直接指标化；延长曝光不增加斑点数（几何限制）。可用data.collect在2θ≈±60°的无碰撞ω区间各扫30°后对该数据集processing.index'
                                  '（上述各种除MgO、LiF外都已验证可指标化；MgO、LiF这类约化晶胞<20 Å³的晶体仍峰太少，应改用Mo靶）。'],
    'data.collect': ['衍射：旋转法Ewald交会、洛伦兹与偏振、晶块吸收与高斯光束照明（27点求积）、镶嵌度与束散决定的摇摆宽度与斑点大小、Kα双线、空气衰减、传感器量子效率、阈值、'
                     '可瘫痪死时间0.12 µs（工程）；结构因子来自COD结构与DABAX散射表；消光、热漫散射的峰形、Kβ（多层膜滤除）未建模；观测的积分强度为按期望值的Poisson抽样，帧图像按同一模型另行抽样。'],
    'strategy.calculate': ['贪心策略只在θ∈{0, ±30°, ±60°, ±90°}、κ∈{0, ±40°, ±80°}、φ∈{0, 90°, 180°, 270°}的组合中选段；劳厄群取指标化所用记录（预实验或数据集）强度的劳厄检验结果'
                           '（只用已由反射对检验的转动，反射对少时取较低的劳厄类，预测完整度偏保守）；该记录可积分的观测少于20个时（小晶胞用Cu的预实验常见）按晶格度量对称（上限）预测，缺面劳厄类（如六方晶格中'
                           '−3m与6/mmm）的完整度会偏高（石英Cu：按6/mmm预测，实得随取向约0.6–0.95）；结果的laue_from与laue_order_assumed说明所用对称；只计完整记录的反射（离扫描两端≥2倍摇摆宽度）。'],
    'processing.index': ['FFT指标化（Steller等1997）在低分辨率峰上找晶格，指标化容差0.12；晶格对称容差1.5 %、1.5°；孪晶只指标主畴；晶格度量对称在该容差内换不成惯用晶胞时按三斜晶胞报告（保守；记录warnings含lattice_symmetry_not_converted，结果给出原度量对称阶metric_symmetry_order_unconverted）。'],
    'processing.reduce': ['积分使用观测表（按测得质心与摇摆宽度去掉部分反射、过载与未指标化的斑点）；先按P-1（Friedel对与重复测量）拟合经验吸收面（晶体坐标系入射与衍射方向的'
                          '4阶多项式加10°块比例，每个系数不足8个强重复观测时不做）做初步劳厄检验，暂定劳厄类高于-1时按该群的等效反射'
                          '重拟合（数据足够时采用；absorption_correction.model注明拟合所用的群），不做晶面指标的解析吸收校正；劳厄检验：在晶格点群的各子群（由≤2个晶格转动生成，加反演）中，只考虑其转动都由≥3个反射对检验、'
                          '反射对R ≤ max(0.25, min(7×R_int(P-1), max(0.6, 1.8×该转动的计数噪声R)))的子群（未检验的转动只作为已检验转动的乘积出现，不单独假设），接受合并R_int ≤ 2.5×R_int(P-1)+0.03'
                          '（重复测量不足时 ≤ 2×R_σ+0.04）且不比已接受的指数2子群差（R_int(G) ≤ 1.33×R_int(H)+0.01）者，取阶最高者（同阶取R_int最小）；阈值按仿真数据标定'
                          '（calibrate_laue_thresholds.py → laue_threshold_calibration.json：356个数据集，评测者按真实取向判定各转动真伪；真转动的反射对R/R_int(P-1)的99 %分位6.7；'
                          '相对指数2子群的R_int比值，真群99 %分位1.30、赝对称群1 %分位1.36；按现阈值真群99.1 %、赝对称群0.7 %通过，还原的劳厄类与结构库一致356/356），'
                          '不是厂家算法的复现；噪声主导的数据（标定记录的weak_data：176个0.04 mm弱晶体数据集，162个可指标化且劳厄类都与结构库一致；'
                          '其中133个噪声主导，平均I/σ中位1.6）真转动的反射对R为其计数噪声R的中位1.14倍（99 %分位1.45、最大1.67）、假转动最小1.88倍（1 %分位2.18），'
                          '上限封顶后无关强度不再被当作对称；真转动若超出上限会得到较低的劳厄类（标定中未出现）；劳厄群在容差内换不成惯用晶胞时按-1处理；空间群只在结构库出现的32种设定加P1'
                          '（共33种）中、于该劳厄类内按系统消光选取（晶格中不属于劳厄群的指标方式都检验）。',
                          '吸收极强（μ·d ≳ 5，如I₂、SnI₄的0.1 mm晶体用Cu）时经验吸收面不足以校正，劳厄类与空间群仍可得到，但与已发表模型的R1可达0.1以上；'
                          '应改用Mo靶或更小的晶体（晶面指标的解析校正未实现）。'],
    'processing.what_is_this': ['检索库为本资产的结构库（67种COD结构）；精修只调比例因子与整体B（不精修原子坐标与各向异性位移），R1反映数据与已发表模型的一致性；晶胞偏差最小的至多3个匹配各自在晶格度量允许的各种指标方式下精修、'
                                '按wR2排序并报告最好者（晶胞相同、强度也几乎相同的结构无法区分；库外的相近结构不会出现在匹配中）。Flack参数用Parsons商法（按模型点群分开合并Bijvoet对，用经验吸收校正后的观测；强吸收μd≳1时残余偏差可达约0.1–0.2）；手性空间群（点群只含真转动）的Flack值区分对映体'
                                '（反转构型≈1）；含非真操作的非中心对称点群（如-43m、-4、mm2）中反转结构等于转动后的同一结构，Flack值只表示按所报指标变换放置的模型相对数据的手性。'],
    'frame.read': ['帧图像由记录的斑点与背景模型按Poisson重新抽样（与积分强度独立抽样）；像素单点扩散（厂家）。'],
    'camera.view': ['只报告晶体偏心、目测尺寸、颜色外形与是否结霜；不是视频图像。'],
    'measurement.result': ['记录的template_source中substance_id、cod_id与doi只对评测者可见（agent为null）。'],
    'plant.configure': ['靶材在安装时确定，运行中不能换（双波长为另一型号）；重新声明需断电、X射线关、无晶体。'],
}
LIMITATIONS = [
    '选定配置：Rigaku XtaLAB Synergy-R（PhotonJet-R/MicroMax-007 HF转靶，Cu或Mo靶；1–10 mrad电动狭缝；κ测角仪；HyPix-6000HE；两扇铰链前门与电气安全回路；Oxford Cryostream 1000）；'
    '从头解结构、文件导出、晶面吸收校正、φ扫描、粉末模式为已声明未实现；双波长、Flow机器人、HyPix-Arc、高压附件、XtalCheck为不支持的选件或其它型号。',
    '厂家公布：外形与质量、电源、1.2 kW功率上限、束散范围、像素与有效面积、帧频、计数器位数、探测器尺寸与质量、κ角50°、各轴速度、灯丝与阳极维护周期、预实验时间与策略规则；'
    '厂家规格的LabWrench转载：管压管流范围与步长、Cu/Mo有效焦斑与阳极转速；同平台用户说明（Synergy-S）：最小距离32 mm；Cryostream产品页检索摘要（未缓存）：80–400 K与0.1 K稳定度；通量、束斑、传感器厚度、死时间、阈值效率、爬升与抽真空时间、最大距离与θ范围、碰撞几何、偏心、热膨胀与位移参数的温度关系、辐射衰减、'
    '结霜速率为工程假设。',
    '衍射模型为运动学近似：不含消光、多重衍射、热漫散射峰形与Kβ；吸收按晶块求积；孪晶只模拟两个畴，多晶与非晶只作为环/晕背景与假峰。',
    '处理为CrysAlisPro的简化：指标化、还原、劳厄群与空间群、结构库检索与两参数精修；不能从头解新结构。',
    '没有真实XtaLAB Synergy-R硬件或CrysAlisPro接口；装样、对中、开关门、钥匙、Motion Enable与换灯丝为宿主/机器人抽象，没有接触验证。',
]


def _sampling_text(k):
    if k in ('clock.time_s', 'goniometer.omega_rad', 'goniometer.kappa_rad', 'goniometer.phi_rad', 'detector.theta_rad', 'detector.distance_m', 'operation.progress',
             'operation.remaining_s', 'xray.voltage_v', 'xray.current_a', 'cryostream.gas_temperature_k', 'truth.crystal', 'truth.flux_ph_s'):
        return 'Instantaneous value at the read time: motions, ramps and the crystal temperature are integrated exactly and cut at every event.'
    if k == 'collection.frames_done':
        return 'Frames read out so far (integer count of completed exposures of the current and finished runs).'
    return 'Changes when a command is accepted or at an event of the process; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    if k in DECLARED:
        return ['NOT_IMPLEMENTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    return '；另一个agent的操作进行中时，agent调用被拒（ACCESS_DENIED；急停除外）' if k in _CONTROL_GUARDED else ''


def _documented_parameters():
    return {
        'body_dimensions_m': (list(BODY_DIMENSIONS_M), 'm', '1300 (W) × 850 (D) × 1875 (H) mm、600 kg（core unit）', [_PP]),
        'mains': (dict(voltage_v=list(MAINS_V), current_a=MAINS_A), '1', '1相200–230 V、20 A（电压V、电流A）', [_PP]),
        'voltage_range_v': (list(KV_RANGE_V), 'V', '管压20–60 kV，1 kV步长（LabWrench转载的MicroMax-007 HF规格表）', [_LW]),
        'current_range_a': (list(MA_RANGE_A), 'A', '管流10–30 mA，1 mA步长（LabWrench转载的MicroMax-007 HF规格表）', [_LW]),
        'power_max_w': (POWER_MAX_W, 'W', 'MicroMax-007 HF最大1.2 kW（Rigaku MicroMax页、期刊与LabWrench）', [_SRC, _JRN, _LW]), 'focal_spot_m': ({k: list(v) for k, v in FOCUS_M.items()}, 'm', '6°出射角有效焦斑：Cu Ø0.07 mm，Mo 0.15 × 0.1 mm（MicroMax-007 HF规格）', [_LW, _SRC]),
        'anode_rotation_s': (ANODE_RPM / 60.0, '1/s', '阳极转速9000 rpm（150 1/s）', [_LW]), 'divergence_range_rad': (list(DIVERGENCE_RANGE_RAD), 'rad', '束散1–10 mrad连续可调', [_BRO, _PP, _WB]),
        'pixel_m': (PIXEL_M, 'm', 'HyPix-6000HE像素100 µm', [_BRO, _DET, _WB]), 'active_area_m': (list(ACTIVE_M), 'm', '有效面积77.5 × 80.3 mm', [_JRN]),
        'frame_rate_max_hz': (FRAME_RATE_MAX_HZ, 'Hz', '帧频最高100 Hz', [_BRO, _DET, _WB]), 'counter_bits': (COUNTER_BITS, '1', '每像素两个16位计数器（交替零死时间）', [_BRO, _WB]),
        'hdr_bits': (HDR_BITS, '1', '合并为31位高动态范围', [_PP]), 'detector_dimensions_m': (list(DETECTOR_DIMENSIONS_M), 'm', '137 × 143 × 216 mm、3.8 kg', [_DET]),
        'kappa_alpha_rad': (KAPPA_ALPHA_RAD, 'rad', 'κ轴与φ轴夹角50°', [_JRN]), 'axis_speeds_rad_s': (dict(AXIS_SPEED_RAD_S), 'rad/s', 'ω、θ、κ 10°/s，φ 20°/s', [_JRN]),
        'scan_speed_max_rad_s': (SCAN_SPEED_MAX_RAD_S, 'rad/s', '扫描速度最高10°/s', [_PP]), 'distance_min_m': (DIST_MIN_M, 'm', '最小晶体-探测器距离32 mm', [_UI]),
        'cryostream_range_k': (list(CRYO_RANGE_K), 'K', 'Cryostream 1000 80–400 K、0.1 K稳定度', [_CRY]),
        'filament_interval_s': (FILAMENT_INTERVAL_S, 's', '灯丝约每三个月由用户更换', [_JRN]), 'anode_interval_s': (ANODE_INTERVAL_S, 's', '阳极每年翻新', [_JRN, _PP]),
        'high_low_exposure_ratio': (HIGH_LOW_EXPOSURE_RATIO, '1', '高角帧曝光为低角的4倍', [_UI]), 'common_scan_width_rad': (COMMON_SCAN_WIDTH_RAD, 'rad', '常用扫描宽度0.3°', [_UI]),
        'pre_experiment_typical_s': (list(PRE_EXPERIMENT_TYPICAL_S), 's', '预实验约4 min（>100 µm的晶体）到15 min', [_UI]),
    }


def _engineering_parameters():
    return {
        'distance_max_m': (DIST_MAX_M, 'm', '最大晶体-探测器距离'), 'distance_speed_m_s': (DIST_SPEED_M_S, 'm/s', '探测器滑架速度'),
        'theta_range_rad': (list(THETA_RANGE_RAD), 'rad', '2θ臂角范围'), 'boot_s': (BOOT_S, 's', '控制电子初始化'), 'pumpdown_s': (PUMPDOWN_S, 's', '放气后抽真空'),
        'turbo_s': (TURBO_S, 's', '通电后涡轮泵运转到真空就绪'), 'spinup_s': (SPINUP_S, 's', '阳极加速与灯丝预热'), 'hv_ramp_v_s': (HV_RAMP_V_S, 'V/s', '管压爬升速率'),
        'ma_ramp_a_s': (MA_RAMP_A_S, 'A/s', '管流爬升速率'), 'standby': (dict(voltage_v=STANDBY[0], current_a=STANDBY[1]), '1', '待机管压（V）与管流（A）'),
        'flux_reference': ({k: dict(v) for k, v in FLUX_REF.items()}, '1', '参考通量（ph/s）与参考管压（V）、管流（A），10 mrad'),
        'flux_exponent': (FLUX_EXPONENT, '1', '特征线强度∝I(V−V_K)^n的指数'), 'beam_fwhm_m': (list(BEAM_FWHM_M), 'm', '晶体处光斑FWHM=a×（有效焦斑/Cu焦斑）+b×束散（mrad）'),
        'air_path_in_m': (AIR_PATH_IN_M, 'm', '入射束空气路径'), 'air_scatter_path_m': (AIR_SCATTER_PATH_M, 'm', '探测器看到的空气散射路径'),
        'oil_thickness_m': (OIL_THICKNESS_M, 'm', '束中冷冻油与晶体环厚度'), 'sensor_si_m': (SENSOR_SI_M, 'm', '探测器传感器硅厚度（未公布）'),
        'dead_time_s': (DEAD_TIME_S, 's', '像素可瘫痪死时间'), 'threshold_default': (THRESHOLD_DEFAULT, '1', '默认阈值（Kα1能量的分数）'),
        'charge_share_loss': (CHARGE_SHARE_LOSS, '1', '阈值高于半能量时的计数效率损失系数'), 'cryo_ramp_max_k_s': (CRYO_RAMP_MAX_K_S, 'K/s', '低温升降温速率上限（360 K/h）'),
        'cryo_tau_s': (CRYO_TAU_S, 's', '晶体跟随气流的时间常数'), 'ice_rate_m3_s': (ICE_RATE_M3_S, 'm3/s', '开门时冷晶体上结霜速率（湿度0.4）'),
        'mount_offset_max_m': (MOUNT_OFFSET_MAX_M, 'm', '装样后晶体最大偏心'), 'center_residual_m': (CENTER_RESIDUAL_M, 'm', '对中后的残余偏心'),
        'decay_half_dose_gy': (dict(warm_295k=DECAY_HALF_DOSE_GY['warm'][1], cold_100k=DECAY_HALF_DOSE_GY['cold'][1]), 'Gy', '分子晶体强度减半的剂量'),
        'expansion_per_k': (dict(EXPANSION_PER_K), '1/K', '线膨胀系数（离子晶体/分子晶体）'), 'u_zero_fraction': (U_ZERO_FRACTION, '1', '位移参数U(T)的零点部分'),
        'collision_margin_m': (COLLISION_MARGIN_M, 'm', '碰撞模型余量'), 'det_face_offset_m': (DET_FACE_OFFSET_M, 'm', '传感器在探测器前窗后的距离'),
    }


def engineering_parameters():
    return {k: dict(value=v, unit=u, meaning_zh=m, evidence='assumption') for k, (v, u, m) in _engineering_parameters().items()}


def export_rigaku_xtalabsynergy_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'RigakuXtalabsynergySession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=[_SIM])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED or k in DECLARED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if k in UNSUPPORTED else 'declared' if k in DECLARED else 'implemented',
                 implementation={layer: ('declared' if k in DECLARED and layer == 'process' else 'absent') if un or layer in ('scene', 'robot', 'hardware') else 'implemented'
                                 for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.rigaku_xtalabsynergy.RigakuXtalabsynergySession.invoke',
                                              invocation=f'session.invoke(instance_id="{INSTANCE_ID}",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='RigakuXtalabsynergyRuntime.invoke / RigakuXtalabsynergySession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate',
                                     description_zh=SLOTS[s]['meaning'] + ('：随宿主推进的时间演化' if k == 'sim.step' else '：调用时立即改变（设定、目标、状态标志或派生量）'))
                                for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置、分析或查询；运动与爬升由step推进',
                                completion_state_ids=(['operation.id', 'result.last_record_id'] if k in ('experiment.pre_experiment', 'data.collect') else ['operation.id'] if async_ else []),
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=(EXEC_TEXT[k][1] if k in EXEC_TEXT else
                                                    ('不支持或未实现：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变'))),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH.get(e, e)),
                              side_effects='unknown' if e == 'DEVICE_ERROR' else 'partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED.get(k) or DECLARED.get(k)] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=SLOTS[s]['meaning'] + '：' + (ON_STEP_ZH.get(k) or '随step演化'))
                                   for s in dict.fromkeys(ON_STEP[k]) if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in _documented_parameters().items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    for name, (val, unit, meaning) in _engineering_parameters().items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['cabinet', 'safety_panel', 'button_power', 'button_emergency_stop', 'button_doors_ok', 'knob_xray_enable', 'door_left', 'door_right', 'enclosure_shell',
                       'source_tower', 'anode_head', 'source_optics', 'collimator', 'omega_table', 'kappa_block', 'phi_shaft', 'goniometer_head', 'theta_arm',
                       'detector_carriage', 'detector', 'beam_stop', 'cryostream_head', 'cryostream_nozzle', 'camera', 'centring_screen', 'motion_enable_left', 'motion_enable_right'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='XtaLAB Synergy-R单晶衍射仪：电气安全回路与门、X射线发生器与快门、束散狭缝、κ测角仪与探测器臂（碰撞模型）、低温气流、装样与对中、旋转法无快门采集的运动学衍射'
                   '（COD结构、DABAX散射表）、HyPix-6000HE光子计数、CrysAlisPro式指标化/还原/空间群/结构库检索与精修、策略',
        equations=['counts = Phi T_air r_e^2 (w1 lambda1^3 + w2 lambda2^3) |F|^2 / V_cell^2 L P / omega_dot sum_p W_p G_beam(p) exp(-mu (d_in + d_out)) V_x '
                   'exp(-mu_air t) QE eps_thr exp(-ln2 D / D_half)',
                   'F(h) = sum_j occ_j (f0_j(s) + f\'_j + i f\'\'_j) exp(-8 pi^2 U_j(T) s^2) exp(2 pi i h . x_j), U(T) = U_lib (0.25 + 0.75 T / T_lib)',
                   'Ewald: omega = -psi +- acos(-lambda |v|^2 / (2 rho)), v = K(kappa) Phi(phi) U B h; L = 1 / |s1_y|; P = (1 + cos^2 2theta) / 2',
                   'sigma_omega = L sqrt((eta / 2.355)^2 + (div / 2.355)^2); spot sigma^2 = (sigma_ill / cos a)^2 + (t div / 2.355)^2 + (t eta / 2.355)^2',
                   'Phi = Phi_ref (I / I_ref) ((V - V_K) / (V_ref - V_K))^1.6 (div / 10 mrad)^2; measured = true exp(-n tau / 2) (paralysable dead time)',
                   'fluorescence per sr = I0 V mu_ph,el (r - 1) / r omega_K exp(-0.75 mu_f r_eq) / 4 pi, omega_K = S^4 / (1 + S^4) (Bambynek)',
                   'indexing: 1D FFT of projections on 7200 directions; UB least squares; Niggli reduction; lattice ops M^T G M = G',
                   'refinement: min sum w (Fo^2 - k Fc^2 exp(-2 dB s^2))^2; R1 = sum ||Fo| - |Fc|| / sum |Fo|'],
        parameters=params, source_ids=[_SIM, _WB, _BRO, _PP, _DET, _SRC, _LW, _JRN, _APP, _UI, _CRY, _DB, _SCT], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_rigaku_xtalabsynergy_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'xtalab_synergy_r': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'xtalab_synergy_r': mechanism})
