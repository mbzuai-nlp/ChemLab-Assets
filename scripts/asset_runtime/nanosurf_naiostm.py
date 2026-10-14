"""CHEM-159 Nanosurf NaioSTM scanning tunnelling microscope (air STM for teaching: scan head with the 500 x 500 x 200 nm scanner on a granite
vibration-isolation table with the controller underneath, stick-slip approach motor, magnetic sample holder in a U slot, cut Pt/Ir tip,
current amplifier 0.1-100 nA in 25 pA steps, tip voltage +-10 V with the sample grounded, constant-current and constant-height imaging up to
2048 x 2048 points at up to 10 Hz line rate, current-voltage and current-distance spectroscopy, magnifying cover): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * system: controller power (rear power button, blue power LED) and the Naio control software (initialisation), the Z-controller settings
    (tunnelling-current set point, tip voltage, P- and I-gain) used by the approach, the idle feedback and imaging;
  * mechanics: the sample holder in the U slot of the scan head (inserted, pushed and turned by hand; moved by the stick-slip approach motor:
    Advance / Retract at maximum speed, Home, Withdraw, the automatic approach in steps set by the move speed), the magnifying cover, the tip
    wire under the tip clamp (cut from the Pt/Ir wire, removed, replaced), the sample on its nickel-plated support disc on the magnet;
  * tunnelling (tunneling_library.json): one-dimensional WKB transmission through the barrier of mean height (phi_tip + phi_sample) / 2,
    lowered in laboratory air (adsorbed water: engineering factor of the humidity), energy dependent (trapezoid mean), Fermi-Dirac occupation of
    tip and sample at room temperature, the sample density-of-states model (metal, semimetal, semiconductor gap with the Fermi-level position,
    CDW pseudogap, topological surface cone, insulator) and the transmission of a native surface oxide; normalised to the conductance quantum at
    point contact; the tip is Pt/Ir 90/10 with hidden state after cutting (sharp, double, blunt, unstable; crashed after contact) and apex;
  * approach: the automatic approach cycles (Z scanner fully retracted, one motor step, Z extension over 200 nm looking for the set point): too
    large steps overshoot the Z range and crash the tip; no current (insulator, thick oxide, 0 V) lets the approach run into the surface;
  * imaging (sample_library.json): procedural surfaces (terraces with monatomic and multiple steps, polycrystalline grains with terraces,
    rough films) with the surface atomic lattice and the 1T-TaS2 charge-density-wave superlattice as apparent corrugation (attenuated with the
    apex radius and the gap; averaged over the pixel along the fast axis), double-tip ghost images, telegraph noise of unstable tips,
    contamination; the Z feedback on the logarithm of the current (P + I gains, error saturation at the current floor and the 100 nA amplifier
    limit, Z range 200 nm), vibration of the gap, thermal drift after disturbances with a 30 nm/K loop coefficient taken from the manual's 'several nanometers per 0.1 degC', room temperature
    fluctuations, piezo creep, scanner calibration errors and forward/backward hysteresis; topography from the 16-bit Z output (3.1 pm steps)
    and the tip current (amplifier noise, 16-bit ADC);
  * spectroscopy: I-V curves with the feedback off (or on: the gap follows the set point), I-z curves toward / away from the surface
    (crash below point contact), forward and backward;
  * analysis (Naio / Gwyddion-like): plane / line flattening, roughness, line profiles, step heights, lattice periods from the 2D FFT,
    dI/dV and normalised conductance, apparent barrier height from ln I(z);
  * fault injection (evaluator): dirty approach motor, vibration burst, tip picks up material, thermal disturbance;
  * ledgers: electrical energy (30 W), motor travel and steps, approaches, scanned lines, spectroscopy curves, tips prepared, wire used, tip
    crashes, cleaning pulses, cleaves, holder insertions.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
from pathlib import Path

import numpy as np

ASSET_TYPE_ID = 'instrument.nanosurf_naiostm'
PROTOCOL_VERSION = '1.0.0'
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t3_analytical/nanosurf_naiostm'
TUNNEL_PATH = ASSET_DIR / 'tunneling_library.json'
SAMPLE_LIB_PATH = ASSET_DIR / 'sample_library.json'
KB = 1.380649e-23
E_CH = 1.602176634e-19
ME = 9.1093837015e-31
HBAR = 1.054571817e-34
G0_S = 7.748091729e-5                                # conductance quantum 2 e^2 / h
KAPPA_1EV = math.sqrt(2 * ME * E_CH) / HBAR         # decay constant per sqrt(eV) of barrier (5.123e9 1/m)


class ActionRejected(ValueError):
    pass


# ---------------------------------------------------------------- documented (QD Japan specification page, NaioSTM Operating Instructions 3.7)
XY_RANGE_M = 500e-9                                 # typical scan range 500 x 500 x 200 nm
Z_RANGE_M = 200e-9
XY_DAC_M = 7.6e-12                                  # 'resolution' = maximum range / 16 bit (digital step, not spatial accuracy)
Z_DAC_M = 3.1e-12
CURRENT_RANGE_A = (0.1e-9, 100e-9)                  # current amplifier 0.1-100 nA, set in 25 pA steps
SETPOINT_STEP_A = 25e-12
TIP_VOLTAGE_RANGE_V = (-10.0, 10.0)                 # manual: -10 V to +10 V; the sample is connected to the instrument ground
MAX_POINTS = 2048                                   # imaging up to 2048 x 2048 points (points / line = lines)
SPEC_POINTS_MAX = 65535                             # spectroscopy up to 65535 points (QD Japan page: 65335 - conflict recorded)
LINE_RATE_MAX_HZ = 10.0                             # imaging speed up to 10 Hz (forward + backward line)
TIME_PER_LINE_MIN_S = 1.0 / (2.0 * LINE_RATE_MAX_HZ)
SAMPLE_MAX_DIAMETER_M = 0.010                       # samples max. 10 mm diameter, 3 mm thick
SAMPLE_MAX_THICKNESS_M = 0.003
POWER_W = 30.0                                      # 90-240 V AC, 50/60 Hz, 30 W
BODY_DIMENSIONS_M = (0.204, 0.204, 0.104)           # 204 x 204 x 104 mm, 3.45 kg
MASS_KG = 3.45
CLEANING_PULSE_V = 5.0                              # STM tip cleaning pulse about 5 V, 100 ms
CLEANING_PULSE_S = 0.1
WIRE_LENGTH_M = 0.30                                # Pt/Ir wire 90/10, 0.25 mm, 30 cm in the tool set
TIP_CUT_LENGTH_M = 0.004                            # cut at about 4 mm
MAGNIFICATION = 10.0                                # magnifying cover with 10x magnifier
MANUAL_PUSH_M = 1e-3                                # push the sample holder by hand to within 1 mm of the tip
CONSTANT_HEIGHT_GAINS = dict(i_gain=4.0, p_gain=0.0)   # manual: constant height mode with I-gain 4, P-gain 0
LOOP_M_PER_K = 30e-9                                # manual: 0.1 degC causes 'several nanometers' (3 nm per 0.1 K taken; 20-90 nm/K possible)

# ---------------------------------------------------------------- geometry shared with the 3D asset (interface_definition.py)
HOLDER_TRAVEL_M = (0.0, 0.012)                      # joint holder.position_m = distance of the magnetic end from the tip apex
COVER_LIFT_M = 0.080                                # joint cover.lift_m of a lifted magnifying cover

# ---------------------------------------------------------------- engineering values of the process model (not published)
BOOT_S = 8.0                                        # controller start and Naio software initialisation
MOTOR_SPEED_M_S = 0.25e-3                           # Advance / Retract / Home at maximum speed
STEP_M_PER_PERCENT = 3e-9                           # automatic approach: motor step = 3 nm x move speed (%): 50 % -> 150 nm
MOTOR_MIN_PERCENT = 10.0                            # below this move speed the stick-slip motor does not move
CYCLE_S = 0.02                                      # one approach cycle: retract Z, one step, extend Z over the range
APPROACH_SETTLE_S = 0.5
DIRTY_MOTOR_FACTOR = 0.2                            # dirty guide spheres / motor surfaces: motion x 0.2 (manual 17.3.1-17.3.2)
BEND_M = 20e-6                                      # a crashed tip wire bends back this far before the stick-slip motor stalls
D_CONTACT_M = 0.25e-9                               # point contact: transmission 1 (conductance quantum)
AIR_BARRIER_A, AIR_BARRIER_B = 0.45, 0.35           # apparent barrier in air = (0.45 - 0.35 RH) x mean work function
TIP_MATERIAL = 'pt_ir_90_10'
I_FLOOR_A = 5e-12                                   # current below this is noise: the feedback error saturates (tip too far)
I_SAT_A = 100e-9                                    # amplifier limit: the error saturates (tip too close)
I_NOISE_A = 2e-12                                   # amplifier noise RMS (imaging / spectroscopy bandwidth)
I_REL_NOISE = 0.01
I_ADC_A = 2 * I_SAT_A / 65536                       # 16-bit ADC over +-100 nA
FB_HZ_PER_GAIN = 10.0                               # feedback bandwidth 2 pi x 10 Hz x (I + 0.5 P)
P_WEIGHT = 0.5
CRITICAL_GAIN = 60.0                                # I + 0.5 P above this: the loop oscillates
OSC_M = 0.02e-9                                     # oscillation per unit excess gain ratio
VIBRATION_M = {'quiet': 1.5e-12, 'normal': 4e-12, 'busy': 12e-12}   # gap vibration RMS on the granite table with the rubber feet
COVER_OFF_VIBRATION = 1.8                           # without the magnifying cover: air flow and sound
VIBRATION_BURST_FACTOR = 10.0
VIB_TAU_S = 0.004
DRIFT_TAU_S = 900.0                                 # thermal settling after a disturbance (manual: up to about one hour)
DRIFT_KICK_M = {'power': 40e-9, 'holder': 60e-9, 'cover': 10e-9, 'tip': 20e-9, 'thermal_disturbance': 80e-9, 'sample': 30e-9}
FLUCT_PERIODS_S = (420.0, 780.0, 1380.0)            # room temperature fluctuation components
CREEP_FRACTION = 0.02                               # piezo creep after an offset change
CREEP_TAU_S = 8.0
CAL_SIGMA = 0.03                                    # scanner calibration error (1 sigma, truncated at +-8 %)
HYSTERESIS_FRACTION = 0.004                         # forward / backward shift of the fast axis (fraction of the image size)
R_REF_M = 0.3e-9                                    # apex radius of the reference (library corrugation) tip
CORR_BETA_M = 3e9                                   # atomic corrugation decays as exp(-beta (0.25 nm / period) (R + d - R_ref - d_ref))
CORR_MAX_GAIN = 2.0
CONTAM_BUMP_M = 0.08e-9                             # adsorbate bumps at full contamination (correlation length 3 nm)
PICKUP_PER_LINE = 2e-4                              # tip picks up material per scanned line x (0.1 + contamination)
DROP_PER_LINE = 1e-3                                # and loses it again (manual: continue measuring for a while, 4-5 images)
TIP_PROBS = {'sharp': 0.45, 'double': 0.20, 'blunt': 0.20, 'unstable': 0.15}
TELEGRAPH_RATE_HZ = 15.0
PUSH_MIN_GAP_M = 0.2e-3                             # hand push / insertion / removal keeps the sample 0.2 mm from the tip (judged by eye)
EYE_QUANT_M = 0.1e-3                                # the eye (cover lifted) judges the gap in 0.1 mm steps, with the per-insertion view bias
MAGNIFIER_QUANT_M = 10e-6                           # through the 10x magnifier: 10 um steps
EYE_SIGMA_M = 20e-6                                 # a look varies: additive judgement jitter (eye / magnifier, independent, clipped at 3 sigma)
EYE_TOUCH_M = 200e-6                                # seen closer than this the tip appears to touch (eye / 10x magnifier)
MAGNIFIER_TOUCH_M = 30e-6
MAGNIFIER_SIGMA_M = 2e-6
ROTATE_MIN_GAP_M = 0.3e-3
TIP_OFFSET_M = (0.5e-3, 2.5e-3)                     # the cut tip lies this far from the holder axis (turning the holder moves the spot)
TIP_WORK_S = 60.0                                   # cut and mount a new tip / remove the old one (robot abstraction)
CLEAVE_LOSS_M = 10e-6                               # a tape cleave removes about 10 um
SUPPORT_RQ_M = 8e-9
DEFAULT_ZC = dict(setpoint_a=1.0e-9, tip_voltage_v=0.05, p_gain=1.0, i_gain=15.0)   # software start values
SPEC_MOVE_S = 0.1                                   # move to a spectroscopy position with the feedback on
MAX_SAMPLES = 12
MAX_RECORDS = 200
RECORD_MEMORY_BYTES = 2 * 1024 ** 3
LARGE_RESPONSE_BYTES = 65536
LARGE_RESPONSE_KEEP = 16
MAX_EVENTS = 500
MAX_STEP_S = 7 * 24 * 3600.0
RESULT_VALUES_MAX = 65536
SIM_MAX_POINTS = 2048
SIM_MAX_SPEC_POINTS = 4096                          # simulation limit per curve (the NaioSTM stores up to 65535)
MAX_CURVES = 128
IDLE_CHECK_S = 5.0                                  # engaged: contact checks at least every 5 s (bisected to the crossing)
SETPOINT_GAP_MAX_M = 320e-9                         # set-point gap search range (doubling from 5 nm)
BLOCK_LINES = 16

FAULTS = ['approach_motor_dirty', 'vibration_burst', 'tip_pickup', 'thermal_disturbance']
INSTANT_FAULTS = {'tip_pickup', 'thermal_disturbance'}
CHANNELS = ['topography', 'current']
DIRECTIONS = ['forward', 'backward']
CHANNEL_UNITS = {'topography': 'm', 'current': 'A'}


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
    if k == 'object' and isinstance(v, dict) and 'properties' in s:
        return {key: coerce(s['properties'][key], val) if key in s['properties'] else val for key, val in v.items()}
    if k == 'array' and isinstance(v, list):
        return [coerce(s['items'], x) for x in v]
    return v


# ---------------------------------------------------------------- template libraries
@functools.lru_cache(maxsize=1)
def tunnel_lib():
    lib = json.loads(TUNNEL_PATH.read_text())
    return dict(meta={k: v for k, v in lib.items() if k != 'substances'}, subs={s['substance_id']: s for s in lib['substances']})


@functools.lru_cache(maxsize=1)
def sample_templates():
    lib = json.loads(SAMPLE_LIB_PATH.read_text())
    return dict(meta={k: v for k, v in lib.items() if k != 'templates'}, templates={t['template_id']: t for t in lib['templates']})


def library_sha256():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (TUNNEL_PATH, SAMPLE_LIB_PATH)}


def flat_value(v):
    return v['value'] if isinstance(v, dict) and 'value' in v else v


@functools.lru_cache(maxsize=128)
def material(sid):
    """Tunnelling constants of a library substance: work function (eV), electronic model, band gap, surface lattice, air surface, corrugation."""
    s = tunnel_lib()['subs'].get(sid)
    if s is None:
        raise ActionRejected('UNKNOWN_MATERIAL')
    dos = dict(s['dos']['params'])
    dos['gap_ev'] = flat_value(s['band_gap']) or 0.0
    air = s['air_surface']
    t_ox = flat_value(air.get('oxide_thickness_m'))
    phi_ox = flat_value(air.get('oxide_barrier_ev'))
    surf = s['surface']
    cdw = s.get('cdw')
    return dict(sid=sid, wf=flat_value(s['work_function']), cls=s['electronic_class']['value'], dos_model=s['dos']['model'], dos=dos,
                air=air['state'], t_ox=t_ox or 0.0, phi_ox=phi_ox, lattice=surf['lattice'], a_s=flat_value(surf['a_m']),
                b_s=flat_value(surf.get('b_m')) if surf.get('b_m') else None, step=flat_value(surf['step_height_m']), plane=surf.get('plane'),
                corr=flat_value(s['stm_corrugation_m']) or 0.0,
                cdw=dict(period=flat_value(cdw['period_m']), angle=flat_value(cdw['angle_rad']), corr=flat_value(cdw['corrugation_m'])) if cdw else None,
                insulating=air['state'] == 'insulating' or s['dos']['model'] == 'insulator')


# ---------------------------------------------------------------- tunnelling
def fermi(e_ev, kT_ev):
    return 0.5 * (1.0 - np.tanh(np.asarray(e_ev) / (2.0 * kT_ev)))


def sample_dos(model, p, eps):
    """Normalised sample density of states at energies eps (eV from the sample Fermi level); metal = 1."""
    eps = np.asarray(eps, float)
    if model == 'metal':
        return np.ones_like(eps)
    if model == 'semimetal':
        return p['rho0'] + (1.0 - p['rho0']) * np.minimum(1.0, np.abs(eps) / p['slope_scale_ev'])
    if model in ('semiconductor', 'dirac'):
        eg, f, s = p['gap_ev'], p['fermi_fraction'], p['band_edge_scale_ev']
        ev, ec = -f * eg, (1.0 - f) * eg
        rho = np.minimum(np.sqrt(np.maximum(eps - ec, 0.0) / s) + np.sqrt(np.maximum(ev - eps, 0.0) / s), 1.5) + p.get('gap_state_density', 0.0)
        if model == 'dirac':
            rho = rho + 0.6 * np.minimum(1.0, np.abs(eps - p['dirac_point_ev']) / p['cone_scale_ev'])
        return rho
    if model == 'cdw':
        return 1.0 - p['dip'] * np.exp(-(eps / p['width_ev']) ** 2)
    return np.zeros_like(eps)


def vacuum_barrier_ev(mat):
    """Mean vacuum barrier (phi_tip + phi_sample) / 2 (eV)."""
    phi_t = material(TIP_MATERIAL)['wf']
    phi_s = mat['wf'] if mat['wf'] is not None else phi_t              # insulators: no current anyway (the value is not used)
    return 0.5 * (phi_s + phi_t)


def barrier_ev(mat, humidity):
    """Apparent mean tunnel barrier in laboratory air (eV): the vacuum barrier scaled by the air factor 0.45 - 0.35 RH."""
    return max(0.05, (AIR_BARRIER_A - AIR_BARRIER_B * humidity) * vacuum_barrier_ev(mat))


def oxide_transmission(mat):
    if mat['t_ox'] <= 0.0 or not mat['phi_ox']:
        return 1.0
    return math.exp(-2.0 * KAPPA_1EV * math.sqrt(mat['phi_ox']) * mat['t_ox'])


def tunnel_current(v_tip, gap_m, mat, phi_ev, kT_ev, n=161):
    """Tip current (A, sign of the tip voltage) at the gaps gap_m (array): I = G0 T_ox integral rho_s(e) [f(e - eV_s) - f(e)] exp(-2 (d - d_c)
    kappa(e)) de over the energy window of the sample bias V_s = -V_tip (sample grounded), kappa(e) = 5.123e9 sqrt(phi_air) sqrt((phi_vac + eV_s/2
    - e) / phi_vac) 1/m (the air lowers the decay uniformly; its energy dependence follows the vacuum trapezoid), tip density of states flat
    (Pt/Ir); transmission 1 at the point-contact gap d_c."""
    gap = np.asarray(gap_m, float)
    if mat['insulating'] or v_tip == 0.0:
        return np.zeros_like(gap)
    vs = -v_tip
    lo, hi = min(0.0, vs) - 10.0 * kT_ev, max(0.0, vs) + 10.0 * kT_ev
    eps = np.linspace(lo, hi, n)
    de = eps[1] - eps[0]
    w = (fermi(eps - vs, kT_ev) - fermi(eps, kT_ev)) * sample_dos(mat['dos_model'], mat['dos'], eps)          # > 0 for V_s > 0
    phi_v = vacuum_barrier_ev(mat)
    kap = KAPPA_1EV * math.sqrt(phi_ev) * np.sqrt(np.maximum(phi_v + 0.5 * vs - eps, 0.01) / phi_v)   # air-scaled decay, vacuum energy dependence
    dd = np.maximum(gap - D_CONTACT_M, 0.0)[..., None]
    i_s = G0_S * oxide_transmission(mat) * np.sum(w * np.exp(-2.0 * dd * kap), axis=-1) * de
    return -i_s                                                                                                  # tip current = - sample current


@functools.lru_cache(maxsize=4096)
def _setpoint_gap(sid, v_tip, i_set, phi_ev, kT_ev):
    """Gap (m) at which |I| = I_set, and the local decay 2 kappa_eff = -d ln|I| / dd there; None when the current at point contact stays below
    the set point (insulator, thick oxide, 0 V tip voltage)."""
    mat = material(sid)

    def ln_i(d):
        i = abs(float(tunnel_current(v_tip, np.array([d]), mat, phi_ev, kT_ev)[0]))
        return math.log(i) if i > 0 else -1e9
    target = math.log(i_set)
    if ln_i(D_CONTACT_M) < target:
        return None
    lo, hi = D_CONTACT_M, 5e-9
    while ln_i(hi) > target and hi < SETPOINT_GAP_MAX_M:              # high tip voltages (field-emission side) hold the tip farther away
        lo, hi = hi, 2.0 * hi
    if ln_i(hi) > target:
        h = 0.01e-9
        return hi, max((ln_i(hi - h) - ln_i(hi + h)) / (2 * h), 1e8)   # beyond the search range: the feedback holds the farthest gap considered
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        if ln_i(mid) > target:
            lo = mid
        else:
            hi = mid
    d = 0.5 * (lo + hi)
    h = 0.01e-9
    two_kappa = (ln_i(max(D_CONTACT_M, d - h)) - ln_i(d + h)) / (d + h - max(D_CONTACT_M, d - h))
    return d, max(two_kappa, 1e8)


def setpoint_gap(mat, v_tip, i_set, humidity, room_k):
    return _setpoint_gap(mat['sid'], round(float(v_tip), 9), round(float(i_set), 15), round(barrier_ev(mat, humidity), 9), round(KB * room_k / E_CH, 9))


# ---------------------------------------------------------------- procedural sample surfaces (sample frame: origin at the sample centre, m)
GRAIN_TILE_M = 1e-6
MAX_GRAINS_PER_TILE = 20000
N_MODES = 160


def _rng(*keys):
    return np.random.default_rng([int(k) & 0xFFFFFFFF for k in keys])


def flatten_topography(tp):
    return {k: flat_value(v) for k, v in tp.items()}


class GaussianField:
    """Stationary Gaussian random field with Gaussian correlation exp(-r^2 / lc^2) and RMS 1 (sum of random cosines, deterministic in the seed)."""

    def __init__(self, seed, corr_length_m, n=N_MODES):
        r = _rng(seed, 11)
        kmag = math.sqrt(2.0) / corr_length_m * np.sqrt(-np.log(1.0 - r.random(n)))
        ang = 2 * math.pi * r.random(n)
        self.kx, self.ky = kmag * np.cos(ang), kmag * np.sin(ang)
        self.ph = 2 * math.pi * r.random(n)
        self.a = math.sqrt(2.0 / n)

    def __call__(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        out = np.zeros(np.broadcast(x, y).shape)
        for i in range(0, len(self.kx), 32):
            k = slice(i, i + 32)
            out += np.cos(np.multiply.outer(x, self.kx[k]) + np.multiply.outer(y, self.ky[k]) + self.ph[k]).sum(-1)
        return self.a * out


OVERRIDE_RANGES = {    # physically sensible bounds of the topography parameters (lengths in m; engineering)
    'terrace_width_m': (5e-9, 5e-6), 'direction_rad': (-math.pi, math.pi), 'meander': (0.0, 1.0), 'rq_m': (0.0, 20e-9),
    'grain_size_m': (10e-9, 2e-6), 'groove_depth_m': (0.0, 20e-9), 'dome_height_m': (0.0, 20e-9), 'corr_length_m': (5e-9, 2e-6)}
OVERRIDE_UNITS = {'terrace_width_m': 'm', 'direction_rad': 'rad', 'meander': '1', 'rq_m': 'm', 'grain_size_m': 'm', 'groove_depth_m': 'm', 'dome_height_m': 'm',
                  'corr_length_m': 'm'}
MAX_STEP_SLOPE = 0.05                                 # mean staircase slope of a vicinal surface at most about 3 degrees


def topography_work(tp):
    """Grain count per 1 um tile relative to the simulation limit (> 1: SIMULATION_LIMIT); None for parameters outside their range or a
    staircase steeper than a vicinal surface."""
    for key, (lo, hi) in OVERRIDE_RANGES.items():
        v = tp.get(key)
        if v is not None and not (lo <= float(v) <= hi):
            return None
    if tp['type'] == 'terraces':
        mult = sum(l * w for l, w in zip(tp['step_layers'], tp['step_weights'])) / sum(tp['step_weights'])
        if tp['step_height_m'] * mult / tp['terrace_width_m'] > MAX_STEP_SLOPE:
            return None
    if tp['type'] == 'grains':
        return GRAIN_TILE_M ** 2 / tp['grain_size_m'] ** 2 / MAX_GRAINS_PER_TILE
    return 0.0


def _lattice_vectors(kind, a, b):
    """Shortest reciprocal vectors (at orientation 0) and the pattern normalisation of a 2D surface lattice."""
    if kind == 'hexagonal':
        g = 4 * math.pi / (math.sqrt(3.0) * a)
        return [(g * math.cos(math.pi / 6 + 2 * math.pi * k / 3), g * math.sin(math.pi / 6 + 2 * math.pi * k / 3)) for k in range(3)], (0.5, 1.5)
    if kind == 'centered_rectangular':                                 # cell a x b with a centred atom: primitive (a/2, b/2), (a/2, -b/2)
        b1 = (2 * math.pi / a, 2 * math.pi / b)
        b2 = (2 * math.pi / a, -2 * math.pi / b)
        return [b1, b2, (b1[0] + b2[0], 0.0)], (0.5, 1.5)
    if kind == 'rectangular':
        return [(2 * math.pi / a, 0.0), (0.0, 2 * math.pi / (b or a))], (1.0, 2.0)
    return [], (0.0, 1.0)


class Surface:
    """Apparent height (m, relative to the nominal top surface) of a sample surface: mesoscale topography (terraces, grains, rough) plus the
    surface atomic lattice (and CDW superlattice) as apparent corrugation, contamination bumps and the mounting tilt."""

    def __init__(self, topography, mat_id, seed, tilt, contamination_seed=None):
        tp = self.t = topography
        self.kind = tp['type']
        self.seed = int(seed)
        self.mat = material(mat_id)
        self.tilt = list(tilt)
        self.rough = GaussianField(self.seed * 7 + 1, tp.get('corr_length_m') or 30e-9) if (tp.get('rq_m') or 0.0) > 0 else None
        self.contam_field = GaussianField(self.seed * 7 + 5, 3e-9)
        r = _rng(self.seed, 3)
        self.angle0 = 2 * math.pi * float(r.random())                  # in-plane orientation of the lattice (single crystals)
        a0 = self.mat['a_s'] or 0.0
        self.t0 = (a0 * float(r.random()), a0 * float(r.random()))     # registry of the lattice (a translation within the cell)
        if self.kind == 'terraces':
            self.meander = GaussianField(self.seed * 7 + 4, 4 * tp['terrace_width_m'])
            n = int(math.ceil(0.02 / tp['terrace_width_m'])) + 4        # enough steps for a 10 mm sample (+ margins)
            rs = _rng(self.seed, 41)
            w = np.asarray(tp['step_weights'], float)
            mult = rs.choice(np.asarray(tp['step_layers'], int), size=2 * n + 1, p=w / w.sum())
            self._n0 = n
            self._cum = np.concatenate([[0.0], np.cumsum(mult)]).astype(float)       # cumulative layers over the steps -n..n
            self._mean_mult = float(np.sum(np.asarray(tp['step_layers']) * w) / w.sum())
        self._tiles, self._blocks = {}, {}
        self.lattice = _lattice_vectors(self.mat['lattice'], self.mat['a_s'], self.mat['b_s']) if self.mat['a_s'] and self.mat['corr'] > 0 else ([], (0.0, 1.0))
        cd = self.mat['cdw']
        self.cdw = (_lattice_vectors('hexagonal', cd['period'], None), cd['angle'], cd['corr']) if cd else None

    def mean_slope(self):
        if self.kind == 'terraces':
            g = self.t['step_height_m'] * self._mean_mult / self.t['terrace_width_m']
            d = self.t.get('direction_rad') or 0.0
            return g * math.cos(d), g * math.sin(d)
        return 0.0, 0.0

    def slope(self):
        mx, my = self.mean_slope()
        return self.tilt[0] + mx, self.tilt[1] + my

    def characteristic_height(self):
        tp, k = self.t, self.kind
        base = 4.0 * (tp.get('rq_m') or 0.0)
        if k == 'terraces':
            return base + 6 * max(tp['step_layers']) * tp['step_height_m']
        if k == 'grains':
            return base + tp['dome_height_m'] + tp['groove_depth_m'] + 3 * tp['step_height_m'] * max(1.0, tp['grain_size_m'] / (2 * tp['terrace_width_m']))
        return max(base, 1e-10)

    # ---- grains (tiled Voronoi cells)
    def _tile(self, i, j):
        key = (i, j)
        if key not in self._tiles:
            if len(self._tiles) > 4096:
                self._tiles.clear()
            r = _rng(self.seed, 101, i, j)
            n = int(r.poisson(GRAIN_TILE_M ** 2 / self.t['grain_size_m'] ** 2))
            self._tiles[key] = dict(x=(i + r.random(n)) * GRAIN_TILE_M, y=(j + r.random(n)) * GRAIN_TILE_M, off=r.standard_normal(n),
                                    vic=2 * math.pi * r.random(n), ang=2 * math.pi * r.random(n))
        return self._tiles[key]

    def _block(self, x, y):
        from scipy.spatial import cKDTree
        m = 2 * self.t['grain_size_m']
        i0, i1 = int(math.floor((np.min(x) - m) / GRAIN_TILE_M)), int(math.floor((np.max(x) + m) / GRAIN_TILE_M))
        j0, j1 = int(math.floor((np.min(y) - m) / GRAIN_TILE_M)), int(math.floor((np.max(y) + m) / GRAIN_TILE_M))
        if (i1 - i0 + 1) * (j1 - j0 + 1) > 400:
            raise ActionRejected('SIMULATION_LIMIT')
        key = (i0, i1, j0, j1)
        b = self._blocks.pop(key, None)
        if b is None:
            tiles = [self._tile(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]
            cat = {k: np.concatenate([t[k] for t in tiles]) for k in ('x', 'y', 'off', 'vic', 'ang')}
            b = dict(cat, tree=cKDTree(np.stack([cat['x'], cat['y']], axis=1)) if len(cat['x']) >= 2 else None)
        self._blocks[key] = b
        while len(self._blocks) > 16:
            self._blocks.pop(next(iter(self._blocks)))
        return b

    def evaluate(self, x, y):
        """(mesoscale height m, lattice orientation rad) at the sample-frame points x, y (arrays of one shape)."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        tp = self.t
        z = np.zeros(x.shape)
        ang = np.full(x.shape, self.angle0)
        if self.kind == 'terraces':
            d = tp.get('direction_rad') or 0.0
            u = x * math.cos(d) + y * math.sin(d)
            v = -x * math.sin(d) + y * math.cos(d)
            idx = np.floor(u / tp['terrace_width_m'] + tp.get('meander', 0.3) * self.meander(v, np.zeros_like(v))).astype(int)
            idx = np.clip(idx + self._n0, 0, len(self._cum) - 1)
            z = tp['step_height_m'] * (self._cum[idx] - self._cum[self._n0])
        elif self.kind == 'grains':
            b = self._block(x, y)
            if b['tree'] is not None:
                pts = np.stack([x.ravel(), y.ravel()], axis=1)
                dist, idx = b['tree'].query(pts, k=2)
                d1, d2 = dist[:, 0], dist[:, 1]
                g = idx[:, 0]
                rg = tp['grain_size_m'] / 2
                dome = tp['dome_height_m'] * np.maximum(0.0, 1.0 - (d1 / rg) ** 2)
                groove = -tp['groove_depth_m'] * np.exp(-(((d2 - d1) / 2) / (tp['grain_size_m'] / 16)) ** 2)
                off = 0.3 * tp['groove_depth_m'] * b['off'][g]
                s = ((pts[:, 0] - b['x'][g]) * np.cos(b['vic'][g]) + (pts[:, 1] - b['y'][g]) * np.sin(b['vic'][g]))
                steps = tp['step_height_m'] * np.floor(s / tp['terrace_width_m']) if tp['step_height_m'] > 0 else 0.0
                z = (dome + groove + off + steps).reshape(x.shape)
                ang = b['ang'][g].reshape(x.shape)
        if self.rough is not None:
            z = z + tp['rq_m'] * self.rough(x, y)
        return z + self.tilt[0] * x + self.tilt[1] * y, ang

    def pattern(self, x, y, ang, fast, px, attenuation):
        """Apparent atomic corrugation (m) of the surface lattice (and CDW) at x, y with orientations ang, averaged over the pixel length px along
        the fast scan direction fast (unit vector in the sample frame): sinc(G.fast px / 2) per reciprocal vector."""
        out = np.zeros(np.shape(x))
        xs, ys = np.asarray(x, float) - self.t0[0], np.asarray(y, float) - self.t0[1]
        for (gs, (_lo, span)), a0, corr, att in ([(self.lattice, 0.0, self.mat['corr'], attenuation[0])] +
                                                 ([(self.cdw[0], self.cdw[1], self.cdw[2], attenuation[1])] if self.cdw else [])):
            if not gs or corr <= 0.0 or att <= 0.0:
                continue
            f = np.zeros(np.shape(x))
            ca, sa = np.cos(ang + a0), np.sin(ang + a0)
            for gx0, gy0 in gs:
                gx, gy = gx0 * ca - gy0 * sa, gx0 * sa + gy0 * ca
                avg = np.sinc((gx * fast[0] + gy * fast[1]) * px / (2 * math.pi))
                f = f + avg * np.cos(gx * xs + gy * ys)
            out = out + corr * att * (f / len(gs)) / span               # zero mean; peak-to-peak = corr x att for the unaveraged pattern
        return out

    def contamination(self, x, y, c):
        return CONTAM_BUMP_M * c * np.maximum(self.contam_field(x, y), 0.0) if c > 0 else 0.0


def reference_gap(mat, humidity):
    """Gap of the reference condition of the library corrugations: a metal junction with the same barrier at 1 nA and 0.1 V (the gap a
    semiconductor needs at its own bias is compared with this, so that its corrugation decays with the physical gap)."""
    return D_CONTACT_M + math.log(G0_S * 0.1 / 1e-9) / (2 * KAPPA_1EV * math.sqrt(barrier_ev(mat, humidity)))


def corrugation_attenuation(mat, apex_r, d, d_ref, period):
    """Visibility of a periodic corrugation of the given period for an apex radius and gap relative to the reference tip (R_ref, d_ref)."""
    beta = CORR_BETA_M * (0.25e-9 / period)
    return min(CORR_MAX_GAIN, math.exp(-beta * (apex_r + d - R_REF_M - d_ref)))


# ---------------------------------------------------------------- tip state (hidden truth of a cut Pt/Ir tip)
def draw_tip(rng, kind=None):
    """A freshly cut tip: kind by the engineering probabilities (or given), apex radius, second apex of a double tip, telegraph noise of an
    unstable tip, lateral offset from the holder axis (the torn wire end is not on the axis)."""
    if kind is None:
        kinds = list(TIP_PROBS)
        kind = str(rng.choice(kinds, p=np.array([TIP_PROBS[k] for k in kinds])))
    r_apex = {'sharp': rng.uniform(0.15e-9, 0.4e-9), 'double': rng.uniform(0.2e-9, 0.5e-9), 'blunt': rng.uniform(1.5e-9, 4e-9),
              'unstable': rng.uniform(0.3e-9, 0.8e-9), 'crashed': rng.uniform(3e-9, 15e-9)}[kind]
    second = None
    if kind == 'double':
        sep, th = rng.uniform(0.6e-9, 3e-9), rng.uniform(0, 2 * math.pi)
        second = dict(dx=sep * math.cos(th), dy=sep * math.sin(th), recess=rng.uniform(0.0, 0.15e-9))
    tele = dict(amp=rng.uniform(0.05e-9, 0.2e-9), rate=TELEGRAPH_RATE_HZ) if kind in ('unstable', 'crashed') else None
    geo = {'sharp': (5e-9, 20e-9), 'double': (5e-9, 20e-9), 'blunt': (20e-9, 60e-9), 'unstable': (5e-9, 20e-9), 'crashed': (30e-9, 100e-9)}[kind]
    off_r, off_t = rng.uniform(*TIP_OFFSET_M), rng.uniform(0, 2 * math.pi)
    return dict(kind=kind, apex_r=float(r_apex), geo_r=float(rng.uniform(*geo)), second=second, telegraph=tele, pickup=False,
                offset=[off_r * math.cos(off_t), off_r * math.sin(off_t)], recess=0.0)


PULSE_TRANSITIONS = {   # STM tip cleaning pulse: next kind (engineering probabilities)
    'sharp': {'sharp': 0.8, 'double': 0.1, 'unstable': 0.1}, 'double': {'sharp': 0.35, 'double': 0.45, 'blunt': 0.2},
    'blunt': {'blunt': 0.8, 'sharp': 0.1, 'double': 0.1}, 'unstable': {'sharp': 0.5, 'unstable': 0.3, 'double': 0.2},
    'crashed': {'crashed': 0.9, 'blunt': 0.1}}


# ---------------------------------------------------------------- feedback
def _servo_step(e0, ydot, dt, w, lo, hi):
    """Error after one pixel interval of the first-order feedback with a saturating error signal, exact for a target moving at the constant
    rate ydot (linear range: exponential relaxation; saturated ranges: constant-rate correction; switching instants solved exactly)."""
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


def servo(y, dt, omega, e_lo, e_hi, z_hi):
    """Z feedback along the rows of y (target Z extension, lines x pixels): dz/dt = omega clamp(y - z, e_lo, e_hi), exact between pixels for
    the piecewise-linear target; the scanner saturates at 0 and z_hi; omega = 0 holds the Z extension of the first pixel. Returns (z, e = y - z)."""
    n = y.shape[1]
    z = np.empty_like(y)
    e = np.empty_like(y)
    z[:, 0] = np.clip(y[:, 0], 0.0, z_hi)
    e[:, 0] = y[:, 0] - z[:, 0]
    if omega <= 0.0:
        z[:] = z[:, :1]
        return z, y - z
    for i in range(1, n):
        en = _servo_step(e[:, i - 1], (y[:, i] - y[:, i - 1]) / dt, dt, omega, e_lo, e_hi)
        zi = np.clip(y[:, i] - en, 0.0, z_hi)
        z[:, i] = zi
        e[:, i] = y[:, i] - zi
    return z, e


def _ar1_lines(rngs, n, rho, sigmas):
    from scipy.signal import lfilter
    w = np.stack([r.standard_normal(n + 1) for r in rngs])
    x = lfilter([math.sqrt(1 - rho * rho)], [1.0, -rho], w[:, 1:], axis=1, zi=(rho * w[:, :1]))[0]
    return x * np.asarray(sigmas)[:, None]


def telegraph(rng, n, dt, amp, rate):
    """Two-level apex jumps (0 / amp) of an unstable tip sampled at n pixels of length dt."""
    t_total = n * dt
    k = int(rng.poisson(rate * t_total))
    s0 = int(rng.integers(0, 2))
    if k == 0:
        return np.full(n, amp * s0)
    cuts = np.sort(rng.random(k) * t_total)
    t = (np.arange(n) + 0.5) * dt
    flips = np.searchsorted(cuts, t)
    return amp * ((s0 + flips) % 2)


def dilate_paraboloid(grid, px, py, R, mx, my):
    """Grey dilation of a height grid (rows = slow axis) by the paraboloid tip end t = (x^2 + y^2) / (2 R): separable, exact; the grid carries
    my rows and mx columns of margin on every side, the result has the interior shape."""
    out = grid
    if mx > 0:
        prof = (np.arange(-mx, mx + 1) * px) ** 2 / (2 * R)
        win = np.lib.stride_tricks.sliding_window_view(out, 2 * mx + 1, axis=1)
        out = np.max(win - prof, axis=-1)
    if my > 0:
        prof = (np.arange(-my, my + 1) * py) ** 2 / (2 * R)
        win = np.lib.stride_tricks.sliding_window_view(out, 2 * my + 1, axis=0)
        out = np.max(win - prof, axis=-1)
    return out


DILATION_MAX_PX = 64                                   # tip-end footprint limit in pixels on each side (simulation limit)


# ---------------------------------------------------------------- image processing (Naio / Gwyddion-like)
FLATTEN = ['none', 'plane', 'line_0', 'line_1', 'line_2', 'terraces']


def _plane_fit(z, mask):
    ny, nx = z.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    A = np.column_stack([np.ones(z.size), xx.ravel(), yy.ravel()])
    good = mask.ravel() & np.isfinite(z.ravel())
    coef, *_ = np.linalg.lstsq(A[good], z.ravel()[good], rcond=None)
    return (A @ coef).reshape(z.shape)


def _terrace_level(z):
    """Terrace (facet) levelling: pixels away from step edges (local gradient below 3x its median) are grouped into connected terraces and one
    common plane tilt is fitted with a separate offset per terrace; the tilt is removed (the step heights stay)."""
    from scipy import ndimage
    gy, gx = np.gradient(z)
    g = np.hypot(gx, gy)
    flat = g < 3.0 * np.nanmedian(g) + 1e-300
    lab, n = ndimage.label(ndimage.binary_erosion(flat, iterations=1))
    sizes = ndimage.sum(np.ones_like(z), lab, index=np.arange(1, n + 1)) if n else np.array([])
    keep = [k + 1 for k in range(n) if sizes[k] >= max(30, z.size // 400)]
    if not keep:
        return z - _plane_fit(z, np.ones(z.shape, bool))
    yy, xx = np.mgrid[0:z.shape[0], 0:z.shape[1]]
    rows_x, rows_y, rows_z, cols = [], [], [], []
    for j, k in enumerate(keep):
        sel = lab == k
        rows_x.append(xx[sel]); rows_y.append(yy[sel]); rows_z.append(z[sel]); cols.append(np.full(int(sel.sum()), j))
    X, Y, Z, C = (np.concatenate(v) for v in (rows_x, rows_y, rows_z, cols))
    A = np.zeros((len(Z), 2 + len(keep)))
    A[:, 0], A[:, 1] = X, Y
    A[np.arange(len(Z)), 2 + C] = 1.0
    coef, *_ = np.linalg.lstsq(A, Z, rcond=None)
    return z - (coef[0] * xx + coef[1] * yy) - np.median(coef[2:])


def flatten_image(img, method):
    z = np.asarray(img, float)
    if method == 'none':
        return z.copy()
    if method == 'plane':
        return z - _plane_fit(z, np.ones(z.shape, bool))
    if method == 'terraces':
        return _terrace_level(z)
    x = np.linspace(-1.0, 1.0, z.shape[1])
    V = np.vander(x, int(method[-1]) + 1)
    coef, *_ = np.linalg.lstsq(V, z.T, rcond=None)
    return z - (V @ coef).T


def roughness_stats(z):
    z = np.asarray(z, float)
    z = z[np.isfinite(z)]
    if z.size < 4:
        return None
    d = z - z.mean()
    rq = float(np.sqrt(np.mean(d * d)))
    return dict(ra_m=float(np.mean(np.abs(d))), rq_m=rq, rpv_m=float(z.max() - z.min()), rsk=float(np.mean(d ** 3) / rq ** 3) if rq > 0 else 0.0,
                rku=float(np.mean(d ** 4) / rq ** 4) if rq > 0 else 0.0, mean_m=float(z.mean()), pixels=int(z.size))


def line_profile(img, start, end, points):
    from scipy.ndimage import map_coordinates
    (x0, y0), (x1, y1) = start, end
    t = np.linspace(0.0, 1.0, points)
    return map_coordinates(np.asarray(img, float), [y0 + (y1 - y0) * t, x0 + (x1 - x0) * t], order=1, mode='nearest')


def step_height_histogram(z, bins=256):
    """Two strongest peaks of the smoothed height histogram: their distance and levels."""
    z = np.asarray(z, float).ravel()
    z = z[np.isfinite(z)]
    hist, edges = np.histogram(z, bins=bins)
    k = np.exp(-0.5 * (np.arange(-6, 7) / 2.0) ** 2)
    hs = np.convolve(hist, k / k.sum(), mode='same')
    peaks = [i for i in range(1, bins - 1) if hs[i] >= hs[i - 1] and hs[i] > hs[i + 1] and hs[i] > 0.05 * hs.max()]
    if len(peaks) < 2:
        return None
    lo, hi = sorted(sorted(peaks, key=lambda i: hs[i], reverse=True)[:2])
    centre = 0.5 * (edges[:-1] + edges[1:])
    cut = 0.5 * (centre[lo] + centre[hi])
    low, high = z[z < cut], z[z >= cut]
    return dict(step_m=float(np.median(high) - np.median(low)), lower_level_m=float(np.median(low)), upper_level_m=float(np.median(high)),
                lower_fraction=float(low.size / z.size))


def lattice_fft(img, px, py, max_peaks=6):
    """Periodicities of an image from its 2D power spectrum (plane-subtracted, Hann window): the strongest local maxima away from the centre,
    refined by a parabola; for three peaks about 60 deg apart the hexagonal lattice constant 2 / (sqrt3 |k|)."""
    z = flatten_image(img, 'plane')
    ny, nx = z.shape
    win = np.outer(np.hanning(ny), np.hanning(nx))
    F = np.fft.fftshift(np.abs(np.fft.fft2(z * win)) ** 2)
    ky = np.fft.fftshift(np.fft.fftfreq(ny, py))
    kx = np.fft.fftshift(np.fft.fftfreq(nx, px))
    KX, KY = np.meshgrid(kx, ky)
    kr = np.hypot(KX, KY)
    kmin = 4.0 / min(nx * px, ny * py)
    Fm = np.where(kr > kmin, F, 0.0)
    from scipy.ndimage import maximum_filter
    loc = (Fm == maximum_filter(Fm, size=5)) & (Fm > 0)
    idx = np.argwhere(loc)
    order = np.argsort(-Fm[loc])
    peaks = []
    for j in order:
        iy, ix = idx[j]
        if KY[iy, ix] < 0 or (KY[iy, ix] == 0 and KX[iy, ix] < 0):
            continue                                                       # the spectrum is symmetric: keep one half-plane
        def refine(a, i, n):
            if 0 < i < n - 1:
                c0, c1, c2 = a[i - 1], a[i], a[i + 1]
                den = c0 - 2 * c1 + c2
                return 0.5 * (c0 - c2) / den if den != 0 else 0.0
            return 0.0
        dxi = refine(np.log(F[iy, :] + 1e-300), ix, nx)
        dyi = refine(np.log(F[:, ix] + 1e-300), iy, ny)
        kxv, kyv = KX[iy, ix] + dxi / (nx * px), KY[iy, ix] + dyi / (ny * py)
        k = math.hypot(kxv, kyv)
        peaks.append(dict(period_m=1.0 / k, angle_rad=math.atan2(kyv, kxv), strength=float(Fm[iy, ix] / Fm.max())))
        if len(peaks) >= max_peaks:
            break
    hexa = None
    if len(peaks) >= 3:
        top = peaks[:3]
        angs = sorted((p['angle_rad'] % math.pi) for p in top)
        gaps = [angs[1] - angs[0], angs[2] - angs[1], math.pi - angs[2] + angs[0]]
        if all(abs(g - math.pi / 3) < math.radians(12) for g in gaps):
            hexa = 2.0 / math.sqrt(3.0) * float(np.mean([p['period_m'] for p in top]))
    return peaks, hexa


def downsample(a, max_values):
    a = np.asarray(a, float)
    ny, nx = a.shape
    f = 1
    while math.ceil(ny / f) * math.ceil(nx / f) > max_values:
        f += 1
    if f == 1:
        return a, 1
    b = np.pad(a, ((0, (-ny) % f), (0, (-nx) % f)), mode='edge')
    return b.reshape(b.shape[0] // f, f, b.shape[1] // f, f).mean(axis=(1, 3)), f


def iv_analysis(v, i, threshold):
    from scipy.signal import savgol_filter
    v, i = np.asarray(v, float), np.asarray(i, float)
    order = np.argsort(v)
    v, i = v[order], i[order]
    win = max(7, (len(v) // 20) * 2 + 1)                           # Savitzky-Golay window about 10 % of the curve
    di = savgol_filter(i, min(win, len(v) - (1 - len(v) % 2)), 2, deriv=1, delta=float(np.mean(np.diff(v))))
    iv = np.where(np.abs(v) > 1e-12, i / np.where(np.abs(v) > 1e-12, v, 1.0), di)
    norm = di / np.sqrt(iv ** 2 + (0.02 * np.max(np.abs(iv))) ** 2)
    below = np.abs(i) < threshold
    j0 = int(np.argmin(np.abs(v)))
    gap = None
    if below[j0]:
        lo = j0
        while lo > 0 and below[lo - 1]:
            lo -= 1
        hi = j0
        while hi < len(v) - 1 and below[hi + 1]:
            hi += 1
        if lo > 0 and hi < len(v) - 1:
            gap = dict(v_low=float(v[lo]), v_high=float(v[hi]), width_v=float(v[hi] - v[lo]))
    return dict(voltage_v=v.tolist(), didv_s=di.tolist(), normalized_conductance=norm.tolist(), zero_bias_conductance_s=float(di[j0]), gap=gap)


def iz_analysis(z, i, i_min, i_max):
    """ln|I| against z (m, positive toward the sample): 2 kappa = slope, apparent barrier = (kappa / 5.123e9)^2 eV."""
    z, i = np.asarray(z, float), np.abs(np.asarray(i, float))
    sel = (i >= i_min) & (i <= i_max)
    if sel.sum() < 5:
        return None
    A = np.vstack([np.ones(int(sel.sum())), z[sel]]).T
    (c0, c1), *_ = np.linalg.lstsq(A, np.log(i[sel]), rcond=None)
    pred = c0 + c1 * z[sel]
    y = np.log(i[sel])
    ss = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum((y - pred) ** 2)) / ss if ss > 0 else 0.0
    if c1 <= 0:
        return None
    kappa = c1 / 2.0
    return dict(decay_constant_per_m=float(c1), kappa_per_m=float(kappa), apparent_barrier_ev=float((kappa / KAPPA_1EV) ** 2), points=int(sel.sum()), r2=r2)


# ---------------------------------------------------------------- capabilities
SLUG = 'nanosurf_naiostm'
CATALOG_ID = 'CHEM-159'
INSTANCE_ID = 'nanosurf_naiostm_01'
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


ID = dict(TEXT, pattern='[a-z0-9_]{1,32}')
HOLDER_REF = '样品座关节位置：磁性端（样品钢片背面）到针尖尖端所在平面的距离（0–12 mm，沿隧穿轴＝资产+X）；样品表面到针尖的距离＝该值−钢片、导电胶与样品的厚度'
ANGLE_REF = '样品座绕自身轴转过的角度（从手柄一侧看逆时针为+）；针尖偏离样品座轴线，转动样品座改变针尖对着的样品位置'
SCAN_REF = '扫描中心相对XY扫描器中心的偏移（扫描器X*/Y*，±250 nm）'
ENG_LIMIT = '工程输入限（厂家未公布该范围）：'
SIM_LIMIT = '仿真上限（不是仪器限制）：'
PIXEL_REF = '图像像素坐标（列x、行y，0起；行0为最先扫描的一行）'
TEMPLATE_IDS = sorted(sample_templates()['templates'])
OVERRIDES = {'type': 'object', 'properties': {k: basis(number(u, *OVERRIDE_RANGES[k]), SIM_LIMIT + '程序化形貌参数的有效范围（保持表面有效、计算量有界）')
                                            for k, u in OVERRIDE_UNITS.items()}, 'required': [], 'additionalProperties': False}
PLANT = obj(dict(room_k=basis(number('K', 288.15, 303.15), ENG_LIMIT + '实验室室温15–30 °C'), humidity=ref(number('1', 0.0, 0.95), '室内相对湿度（0–1）'),
                 temperature_stability_k=basis(ref(number('K', 0.0, 1.0), '扫描头处室温波动的均方根（分钟到二十分钟周期）'), ENG_LIMIT + '室温波动均方根≤1 K'),
                 floor_vibration=enum(['quiet', 'normal', 'busy']), noise_seed=integer(0, 2 ** 32 - 1)))
PLANT_EXAMPLE = dict(room_k=295.15, humidity=0.40, temperature_stability_k=0.05, floor_vibration='normal', noise_seed=1)
SAMPLE_PROVIDE = obj(dict(sample_id=ID, template_id=enum(TEMPLATE_IDS), seed=integer(0, 2 ** 31 - 1),
                          tilt_rad=array(basis(number('rad', -0.05, 0.05), ENG_LIMIT + '导电胶粘贴的样品倾斜≤约3°'), 2, 2),
                          contamination=ref(number('1', 0.0, 1.0), '表面污染程度（0新鲜、1严重）'), topography_overrides=OVERRIDES),
                     optional=('topography_overrides', 'contamination'))
SAMPLE_EXAMPLE = dict(sample_id='hopg', template_id='hopg_kit', seed=3, tilt_rad=[0.004, -0.002])
OPERATION = obj(dict(operation_id=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
ZC = obj(dict(setpoint_a=number('A'), tip_voltage_v=number('V'), p_gain=number('1'), i_gain=number('1')))
MOTION = obj(dict(travel_m=ref(nullable(number('m')), '名义行程＝名义速度×驱动时间（Home为null：驱动到行程端点）；不是样品座位置'),
                  duration_s=ref(nullable(number('s')), '电机驱动时间（Home为null）')))
REGION = nullable(ref(array(integer(0, MAX_POINTS), 4, 4), PIXEL_REF + '：[x0, y0, x1, y1]，含x0/y0、不含x1/y1'))
ROUGH = obj(dict(ra_m=number('m'), rq_m=number('m'), rpv_m=number('m'), rsk=number('1'), rku=number('1'), mean_m=number('m'), pixels=INT))
RESULT = obj(dict(record=obj(dict(record_id=TEXT, kind=enum(['image', 'spectroscopy', 'processed']), status=STATUS, started_s=number('s'),
                                  finished_s=nullable(number('s')), meta={'type': 'object'}, warnings=array(TEXT, 0, 32), template_source={'type': 'object'})),
                  data={'type': 'object'}))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'cover', 'holder', 'tip', 'sample', 'motor', 'approach', 'scan', 'spectroscopy', 'error', 'fault']),
                 code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['electrical_energy_j', 'motor_travel_m', 'approach_steps', 'approaches', 'scan_lines', 'spectroscopy_curves', 'tips_prepared', 'wire_used_m',
               'tip_crashes', 'cleaning_pulses', 'cleaves', 'holder_insertions']
LEDGER = obj({k: number('J' if k.endswith('_j') else 'm' if k.endswith('_m') else '1', 0.0) for k in LEDGER_KEYS})
POS2 = array(number('m', -XY_RANGE_M / 2, XY_RANGE_M / 2), 2, 2)
SPEC_COMMON = dict(mod_time_s=basis(ref(number('s', 0.01, 60.0), '一个方向（正向或反向）的调制时间'), ENG_LIMIT + '每个方向10 ms–60 s'),
                   data_points=basis(integer(16, SPEC_POINTS_MAX), '厂家：谱最多65535点（仿真另限每条4096点，见SIMULATION_LIMIT）'),
                   repetitions=basis(integer(1, 16), SIM_LIMIT + '每个位置最多重复16次'),
                   positions_m=ref(array(POS2, 1, 64), SCAN_REF + '；每个位置先在反馈下移过去'))

cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['granite_table', 'isolation_feet'],
    '宿主声明实验室条件（室温、相对湿度、扫描头处的室温波动、地面振动等级、噪声种子）；断电且无操作时才可重新声明', 'evaluator', 'configuration')
cap('sample.provide', SAMPLE_PROVIDE, obj(dict(sample_id=TEXT, template_id=TEXT, location=TEXT)), SAMPLE_EXAMPLE, ['holder_magnet_face'],
    '宿主在实验台上提供一个贴在镀镍磁性钢片上的样品（样品模板库：套件HOPG与金薄膜、Au(111)、铂膜、MoS2/WSe2/MoSe2/NbSe2/1T-TaS2/Bi2Se3/Bi2Te3晶体、ITO、YBCO、'
    '铜箔、铝箔、带氧化层的硅片、空钢片、玻璃、云母；可覆盖形貌参数、给定种子、安装倾角与初始污染）', 'evaluator', 'configuration')
cap('cover.lift', obj({}), obj(dict(lifted=BOOL)), {}, ['magnifying_cover', 'magnifier'], '把放大罩从扫描头上提起放到一边（装卸针尖与样品座前；宿主/机器人抽象）', 'agent', 'robot_skill')
cap('cover.place', obj({}), obj(dict(lifted=BOOL)), {}, ['magnifying_cover', 'magnifier'], '把放大罩罩回扫描头（挡气流与声音、降低热漂移；10×放大镜观察针尖与间隙）', 'agent', 'robot_skill')
cap('tip.remove', obj({}), obj(dict(removed=BOOL, duration_s=number('s'))), {}, ['stm_tip', 'tip_clamp', 'tip_holder'],
    '用尖头镊子从针尖压片下取出旧针尖（放大罩已提起、样品座已取出；约60 s；宿主/机器人抽象）', 'agent', 'robot_skill')
cap('tip.prepare', obj({}), obj(dict(tip_number=INT, wire_remaining_m=number('m'), duration_s=number('s'))), {}, ['stm_tip', 'tip_clamp', 'tip_holder'],
    '用平口钳夹住Pt/Ir丝、剪钳在约4 mm处斜着夹住后拉断（撕断而非剪断）得到针尖，放进针尖座槽中压在压片下（伸出约2–3 mm）；针尖质量为隐藏真值（锋利、双针尖、钝、不稳定）；'
    '放大罩已提起、样品座已取出、针尖座上没有针尖；约60 s；宿主/机器人抽象', 'agent', 'robot_skill')
cap('sample.mount', obj(dict(sample_id=ID)), obj(dict(sample_id=TEXT, stack_thickness_m=number('m'))), dict(sample_id='hopg'), ['sample_holder', 'holder_magnet_face'],
    '用镊子把实验台上的样品（连同钢片）放到样品座的磁性端（样品座在扫描头外）；返回用卡尺量得的钢片＋导电胶＋样品总厚度（0.05 mm分辨）；宿主/机器人抽象', 'agent', 'robot_skill')
cap('sample.unmount', obj({}), obj(dict(sample_id=TEXT)), {}, ['sample_holder', 'holder_magnet_face'], '把样品从样品座磁性端取回实验台（样品座在扫描头外）', 'agent', 'robot_skill')
cap('sample.cleave', obj(dict(sample_id=ID)), obj(dict(sample_id=TEXT, thickness_m=number('m'), cleaves=INT)), dict(sample_id='hopg'), ['holder_magnet_face'],
    '用胶带揭去层状样品的最上层（手册：石墨很脏或不平时可用胶带清洁）：新鲜表面、新的台阶分布、污染清零、厚度减约10 µm；样品须在实验台上、可解理（金膜不能清洁）', 'agent',
    'robot_skill')
cap('holder.insert', obj(dict(position_m=ref(number('m', HOLDER_TRAVEL_M[0], HOLDER_TRAVEL_M[1]), HOLDER_REF), angle_rad=ref(number('rad', -math.pi, math.pi), ANGLE_REF))),
    obj(dict(position_m=number('m'), angle_rad=number('rad'))), dict(position_m=0.0045, angle_rad=0.0), ['sample_holder', 'guide_spheres', 'approach_motor', 'holder_magnet'],
    '把样品座放进U形槽：前端放在两个导向钢球上、后端靠磁铁吸到进针电机上（手册3.4），手推到给定位置（样品表面离针尖须≥0.2 mm，手册建议推到1 mm以内）；放大罩须提起；手的温度带来热漂移；'
    '宿主/机器人抽象', 'agent', 'robot_skill')
cap('holder.push', obj(dict(position_m=ref(number('m', HOLDER_TRAVEL_M[0], HOLDER_TRAVEL_M[1]), HOLDER_REF))), obj(dict(position_m=number('m'))), dict(position_m=0.0040),
    ['sample_holder', 'holder_handle'], '用手在槽中推/拉样品座到给定位置（样品表面离针尖须≥0.2 mm；放大罩提起、未进针、电机不动；手的温度带来热漂移）', 'agent', 'robot_skill')
cap('holder.rotate', obj(dict(angle_rad=ref(number('rad', -math.pi, math.pi), ANGLE_REF))), obj(dict(angle_rad=number('rad'))), dict(angle_rad=0.5), ['sample_holder', 'holder_handle'],
    '用手绕轴转动样品座到给定角度（手册：针尖对着粗糙区时转动样品座换到平整镜面区域）；样品表面离针尖须≥0.3 mm；放大罩提起、未进针', 'agent', 'robot_skill')
cap('holder.remove', obj({}), obj(dict(sample_id=nullable(TEXT))), {}, ['sample_holder', 'holder_handle'],
    '只拿手柄把样品座从U形槽取出放回容器（样品表面离针尖须≥0.2 mm；放大罩提起、未进针、电机不动）', 'agent', 'robot_skill')
cap('maintenance.clean_guides', obj({}), obj(dict(cleaned=BOOL)), {}, ['guide_spheres', 'approach_motor'],
    '用乙醇清洁导向钢球与进针电机表面（手册：手动/自动进针慢或时停时走时的处理）；样品座已取出、放大罩提起', 'agent', 'robot_skill')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL, state=TEXT)), dict(enabled=True), ['button_power', 'led_power', 'controller_housing'],
    '控制器电源（背面电源键；通电时侧面蓝色LED亮，Naio软件初始化约8 s；断电时Z扫描器回零、电机停、进行中的操作失败）')
cap('power.press_button', obj({}), obj(dict(enabled=BOOL, state=TEXT)), {}, ['button_power', 'led_power'], '按一次背面电源键（切换通断；与power.set同一过程）')
cap('z_controller.set', obj(dict(setpoint_a=ref(number('A', CURRENT_RANGE_A[0], CURRENT_RANGE_A[1]), '隧穿电流设定点（绝对值；按25 pA步长取整）'),
                                 tip_voltage_v=ref(number('V', TIP_VOLTAGE_RANGE_V[0], TIP_VOLTAGE_RANGE_V[1]), '针尖电压（样品接地；+为针尖相对样品为正）'),
                                 p_gain=basis(number('1', 0.0, 100.0), ENG_LIMIT + '增益0–100（临界增益约60以上振荡，工程值）'),
                                 i_gain=basis(number('1', 0.0, 100.0), ENG_LIMIT + '增益0–100（临界增益约60以上振荡，工程值）'))),
    obj(dict(settings=ZC)), dict(setpoint_a=1e-9, tip_voltage_v=0.05, p_gain=1.0, i_gain=15.0), ['controller_housing', 'tip_holder'],
    'Z控制器设置：隧穿电流设定点（0.1–100 nA，25 pA步长）、针尖电压（±10 V）、P与I增益（手册：恒高模式I=4、P=0）；进针后立即生效（反馈重新调整间隙），扫描中从下一块生效；'
    '自动进针或谱测量进行中不可改')
cap('approach.advance', obj(dict(duration_s=basis(ref(number('s', 0.05, 120.0), '按住Advance键的时间'), ENG_LIMIT + '按住0.05 s到2 min（覆盖12 mm行程）'))), MOTION, dict(duration_s=2.0), ['approach_motor', 'sample_holder'],
    '按住Advance：进针电机以最大速度（约0.25 mm/s）把样品推向针尖，直到松开；不会因电流停下——推到接触即撞针（样品推弯针尖，评测者可见）；须在放大镜下看着间隙；未进针时才可')
cap('approach.retract', obj(dict(duration_s=basis(ref(number('s', 0.05, 120.0), '按住Retract键的时间'), ENG_LIMIT + '按住0.05 s到2 min（覆盖12 mm行程）'))), MOTION, dict(duration_s=2.0), ['approach_motor', 'sample_holder'],
    '按住Retract：进针电机以最大速度把样品拉离针尖（进针状态下先缩回Z扫描器）')
cap('approach.home', obj({}), MOTION, {}, ['approach_motor', 'sample_holder'], 'Home：把样品退到最远（样品座行程末端），保证自动进针有最大行程；可用approach.stop中止')
cap('approach.stop', obj({}), obj(dict(stopped=BOOL)), {}, ['approach_motor'], 'Stop：停止Advance/Retract/Home/Withdraw的电机运动（返回是否停止了一个运动）')
cap('approach.withdraw', obj(dict(steps=basis(integer(1, 1000), ENG_LIMIT + '一次后退1–1000步'), move_speed_percent=ref(number('1', 1.0, 100.0), 'Move speed（%）：电机步长＝3 nm×该值'))), MOTION,
    dict(steps=10, move_speed_percent=50.0), ['approach_motor', 'sample_holder'], 'Withdraw：Z扫描器缩回、电机后退给定步数（按进针步长），离开隧穿范围')
cap('approach.start', obj(dict(move_speed_percent=ref(number('1', 1.0, 100.0), 'Move speed（%）：电机步长＝3 nm×该值（10 %以下电机不动；步长超过Z量程会冲过设定点撞针）'),
                               max_steps=basis(integer(1, 200000), SIM_LIMIT + '最多200000步（100 %时60 mm，超过12 mm行程）'))), OPERATION, dict(move_speed_percent=50.0, max_steps=20000), ['approach_motor', 'sample_holder', 'tip_holder', 'stm_tip'],
    '自动进针：每个周期Z扫描器全缩回、电机走一步、Z扫描器伸出200 nm寻找设定电流；在Z量程内检测到设定电流即完成（反馈保持设定点，状态灯常绿），否则继续下一步，'
    '直到最大步数；进行中状态灯闪烁')
cap('scan.start', obj(dict(image_size_m=number('m', 1e-9, XY_RANGE_M), time_per_line_s=ref(number('s', TIME_PER_LINE_MIN_S, 100.0), '采一行（一个方向）的时间；正反两行组成一个线周期'),
                           points=integer(16, MAX_POINTS), rotation_rad=ref(number('rad', -math.pi, math.pi), '测量X*方向相对扫描器X轴的旋转'),
                           offset_x_m=ref(number('m', -XY_RANGE_M / 2, XY_RANGE_M / 2), SCAN_REF), offset_y_m=ref(number('m', -XY_RANGE_M / 2, XY_RANGE_M / 2), SCAN_REF),
                           slope_x_rad=basis(ref(number('rad', -0.1, 0.1), '扫描平面X*方向倾斜补偿（Slope X）'), ENG_LIMIT + '斜率补偿±0.1 rad'),
                           slope_y_rad=basis(ref(number('rad', -0.1, 0.1), '扫描平面Y*方向倾斜补偿（Slope Y）'), ENG_LIMIT + '斜率补偿±0.1 rad'))),
    OPERATION, dict(image_size_m=100e-9, time_per_line_s=0.1, points=256, rotation_rad=0.0, offset_x_m=0.0, offset_y_m=0.0, slope_x_rad=0.0, slope_y_rad=0.0),
    ['scan_head_body', 'tip_holder', 'stm_tip', 'holder_magnet_face'],
    '成像（进针后）：正反向逐行扫描（points×points），Z控制器按当前设定跟踪电流；同时记录形貌（Z输出，m）与针尖电流（A）的正反向图；增益高为恒流形貌、低增益（I=4、P=0）为恒高电流像')
cap('spectroscopy.iv', obj(dict(start_v=ref(number('V', -10.0, 10.0), '起始针尖电压'), range_v=ref(number('V', -20.0, 20.0), '正向调制范围（终点＝起点＋范围，须在±10 V内）'),
                                **SPEC_COMMON, keep_z_controller=ref(BOOL, 'true：调制中Z控制器保持电流（记录Z）；false：Z保持调制前的位置'))),
    OPERATION, dict(start_v=-1.0, range_v=2.0, mod_time_s=0.5, data_points=256, repetitions=1, positions_m=[[0.0, 0.0]], keep_z_controller=False),
    ['tip_holder', 'stm_tip', 'holder_magnet_face'], '电流-电压谱（进针后）：在各位置针尖电压从起点扫到终点再扫回（正向、反向），记录针尖电流；默认Z控制器关闭、Z保持在设定点位置')
cap('spectroscopy.iz', obj(dict(start_m=basis(ref(number('m', -50e-9, 5e-9), '相对设定点位置的起始Z偏移（+为靠近样品）'), ENG_LIMIT + '在200 nm Z量程内，靠近样品方向限5 nm'),
                                range_m=basis(ref(number('m', -50e-9, 50e-9), '正向Z调制范围（+为靠近样品：可能压入样品）'), ENG_LIMIT + '±50 nm（Z量程200 nm内）'),
                                **SPEC_COMMON)),
    OPERATION, dict(start_m=-1e-9, range_m=1.5e-9, mod_time_s=0.5, data_points=256, repetitions=1, positions_m=[[0.0, 0.0]]),
    ['tip_holder', 'stm_tip', 'holder_magnet_face'], '电流-距离谱（进针后）：针尖电压不变，Z从起点移过给定范围再移回，记录针尖电流（隧穿电流随距离指数变化）；越过点接触即撞针')
cap('tip.cleaning_pulse', obj({}), obj(dict(voltage_v=number('V'), duration_s=number('s'))), {}, ['stm_tip', 'tip_holder'],
    'STM针尖清洁脉冲（约5 V、100 ms，手册）：去掉针尖拾取的材料，针尖状态随机改变（隐藏真值）；进针时（可在扫描中）')
cap('magnifier.view', obj({}), obj(dict(holder_in_head=BOOL, magnification=number('1'), gap_estimate_m=nullable(number('m')), appears_touching=BOOL, reflection_visible=BOOL,
                                        tip_over=nullable(enum(['sample', 'support', 'holder_face'])))),
    {}, ['magnifier', 'magnifier_lens', 'stm_tip'],
    '通过放大罩上的10×放大镜（罩提起时为肉眼）观察针尖与其在样品中的镜像：给出估计间隙（放大镜10 µm、肉眼0.1 mm分辨，观察角带来系统偏差；小于约30 µm（肉眼0.2 mm）看起来已接触）'
    '与针尖对着的是样品、镀镍钢片还是样品座磁性端（转动样品座可换位置）', 'agent', 'query')
_IMG_IN = dict(record_id=TEXT, channel=enum(CHANNELS), direction=enum(DIRECTIONS))
_TOPO_IN = dict(record_id=TEXT, channel=enum(['topography']), direction=enum(DIRECTIONS))
cap('analysis.flatten', obj(dict(_IMG_IN, method=enum(FLATTEN))), obj(dict(record_id=TEXT, source_record_id=TEXT, stats=nullable(ROUGH))),
    dict(record_id='IMG0001', channel='topography', direction='forward', method='line_1'), ['controller_housing'],
    '图像展平（平面或逐行0–2阶多项式），生成处理后的记录；形貌通道给出粗糙度统计（m），电流通道为null', 'agent', 'analysis')
cap('analysis.roughness', obj(dict(_TOPO_IN, flatten=enum(FLATTEN), region_px=REGION)), ROUGH,
    dict(record_id='IMG0001', channel='topography', direction='forward', flatten='plane', region_px=None), ['controller_housing'], '粗糙度（形貌通道，m）', 'agent', 'analysis')
cap('analysis.line_profile', obj(dict(_IMG_IN, flatten=enum(FLATTEN), start_px=ref(array(number('1', 0, MAX_POINTS), 2, 2), PIXEL_REF),
                                      end_px=ref(array(number('1', 0, MAX_POINTS), 2, 2), PIXEL_REF), points=integer(2, 4096))),
    obj(dict(distance_m=array(number('m'), 2, 4096), values=array(number('1'), 2, 4096), unit=TEXT)),
    dict(record_id='IMG0001', channel='topography', direction='forward', flatten='line_1', start_px=[0, 128], end_px=[255, 128], points=256), ['controller_housing'],
    '线剖面（双线性插值；形貌m、电流A）', 'agent', 'analysis')
cap('analysis.step_height', obj(dict(_TOPO_IN, flatten=enum(FLATTEN), method=enum(['histogram', 'regions']), region_a_px=REGION, region_b_px=REGION)),
    obj(dict(step_m=number('m'), lower_level_m=number('m'), upper_level_m=number('m'), method=TEXT)),
    dict(record_id='IMG0001', channel='topography', direction='forward', flatten='line_1', method='histogram', region_a_px=None, region_b_px=None), ['controller_housing'],
    '台阶高度（形貌通道）：高度直方图两峰之差，或两个区域平均高度之差（B−A）', 'agent', 'analysis')
cap('analysis.lattice', obj(dict(_IMG_IN, region_px=REGION)),
    obj(dict(peaks=array(obj(dict(period_m=number('m'), angle_rad=number('rad'), strength=number('1'))), 0, 6), hexagonal_lattice_m=nullable(number('m')))),
    dict(record_id='IMG0001', channel='topography', direction='forward', region_px=None), ['controller_housing'],
    '二维FFT求周期（去平面、Hann窗；最强的非中心峰，抛物线细化）；三个峰约相隔60°时给出六方晶格常数2/(√3|k|)（按扫描器标定的长度）', 'agent', 'analysis')
cap('analysis.iv', obj(dict(record_id=TEXT, curve_index=integer(0, MAX_CURVES - 1), branch=enum(DIRECTIONS), threshold_a=number('A', 1e-13, 1e-8))),
    obj(dict(voltage_v=array(number('V'), 16, 4096), didv_s=array(number('S'), 16, 4096), normalized_conductance=array(number('1'), 16, 4096),
             zero_bias_conductance_s=number('S'), gap=nullable(obj(dict(v_low=number('V'), v_high=number('V'), width_v=number('V')))))),
    dict(record_id='SPC0001', curve_index=0, branch='forward', threshold_a=10e-12), ['controller_housing'],
    'I-V分析：dI/dV（Savitzky-Golay）、归一化电导(dI/dV)/(I/V)（加展宽）、零偏压电导、|I|低于阈值的电压区间（表观带隙）', 'agent', 'analysis')
cap('analysis.iz', obj(dict(record_id=TEXT, curve_index=integer(0, MAX_CURVES - 1), branch=enum(DIRECTIONS), i_min_a=number('A', 1e-13, 1e-7), i_max_a=number('A', 1e-12, 1e-7))),
    obj(dict(decay_constant_per_m=number('1/m'), kappa_per_m=number('1/m'), apparent_barrier_ev=number('eV'), points=INT, r2=number('1'))),
    dict(record_id='SPC0001', curve_index=0, branch='forward', i_min_a=20e-12, i_max_a=50e-9), ['controller_housing'],
    'I-z分析：ln|I|对z线性拟合（选电流区间），衰减常数2κ与表观势垒高度φ=(ħκ)²/(2m)（eV）', 'agent', 'analysis')
cap('measurement.result', obj(dict(record_id=TEXT, channel=nullable(enum(CHANNELS)), direction=nullable(enum(DIRECTIONS)), region_px=REGION,
                                   max_values=integer(16, RESULT_VALUES_MAX))), RESULT,
    dict(record_id='IMG0001', channel='topography', direction='forward', region_px=None, max_values=65536), ['controller_housing'],
    '读取记录：图像（按区域截取、块平均降采样到不超过max_values个值）、谱曲线或处理后的图', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, MAX_RECORDS))), {}, ['controller_housing'], '列出记录', 'agent', 'query')
cap('records.delete', obj(dict(record_id=TEXT)), obj(dict(record_id=TEXT, remaining=INT)), dict(record_id='IMG0001'), ['controller_housing'],
    '删除一条记录（进行中操作的记录不能删）', 'agent', 'configuration')
cap('events.read', obj(dict(since_s=basis(number('s', 0.0, 1e9), SIM_LIMIT + '仿真时钟上限1e9 s'))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), ['controller_housing'], '读取事件', 'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries={'type': 'object', 'additionalProperties': TEXT}, substances=INT, sample_templates=INT)), {}, ['controller_housing'],
    '模板库来源与校验和', 'agent', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 8))), dict(fault='approach_motor_dirty', active=True), ['granite_table'],
    '注入或清除故障（评测者）：进针电机/导向球脏（运动×0.2）、振动冲击、针尖拾取材料（立即）、热扰动（立即，新的漂移）', 'evaluator', 'configuration')
cap('ledger.read', obj({}), LEDGER, {}, ['granite_table'], '使用与守恒账本（电能、电机行程与步数、进针次数、扫描行数、谱曲线、制针、用丝长度、撞针、清洁脉冲、解理、放样品座）',
    'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['granite_table'], '宿主推进仿真时间', 'evaluator', 'simulation')
DECLARED = {
    'lithography.patterning': 'NaioSTM软件有刻蚀（Patterning）模式，本资产未实现（针尖电压/电流图案写入）',
    'lithography.modification': 'NaioSTM软件有表面修饰（Modification）模式，本资产未实现',
    'export.nid': 'Naio软件标准功能：每个完成的测量自动存为.nid文档，File菜单可打开/另存/打印（手册4.6）；本资产只在运行层保留记录（measurement.result），未实现.nid文件读写',
}
UNSUPPORTED = {
    'spectroscopy.stop_by_value': '按电流值停止的调制（Stop by value）需要Naio Advanced Modes Option（选件），不在本配置',
    'side_view_camera.read': 'NaioSTM Side View Camera是选件，不在本配置',
    'isostage.enable': 'Nanosurf Isostage主动隔振台是选件，不在本配置',
    'afm.scan': 'NaioSTM只做STM（AFM是另一型号NaioAFM）',
    'hardware.connect': '连接真实NaioSTM控制器（USB与Naio软件）不在本仿真资产范围：设计上不提供硬件适配器，也不授权操作真实仪器',
    'sample.heat': '没有样品温控（只模拟室温大气）',
}
for _k, _why in list(DECLARED.items()) + list(UNSUPPORTED.items()):
    cap(_k, obj({}), obj({}), {}, ['controller_housing'], _why, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


TIP_TRUTH = obj(dict(number=INT, kind=enum(['sharp', 'double', 'blunt', 'unstable', 'crashed']), apex_radius_m=number('m'), second_apex=nullable({'type': 'object'}),
                     telegraph=nullable({'type': 'object'}), picked_up=BOOL, offset_m=array(number('m'), 2, 2), recess_m=number('m')))
slot('clock.time_s', number('s'), 's', 'derived', 'granite_table', '仿真时钟')
slot('plant.configured', BOOL, '1', 'setpoint', 'granite_table', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'button_power', '控制器电源（背面电源键）')
slot('power.led_on', BOOL, '1', 'derived', 'led_power', '侧面蓝色电源LED亮（通电）')
slot('system.state', enum(['off', 'booting', 'ready']), '1', 'derived', 'controller_housing', '系统状态（断电/初始化中/就绪）')
slot('cover.lifted', BOOL, '1', 'scene', 'magnifying_cover', '放大罩已提起（机器人/宿主操作的结果，场景中可见）')
slot('cover.lift_m', number('m', 0.0, COVER_LIFT_M), 'm', 'scene', 'magnifying_cover', '放大罩的抬起高度（场景关节magnifying_cover镜像：0罩上，0.08 m提起）')
slot('holder.location', enum(['container', 'head']), '1', 'derived', 'sample_holder', '样品座在容器中/在扫描头U形槽中')
slot('holder.position_m', nullable(number('m', HOLDER_TRAVEL_M[0], HOLDER_TRAVEL_M[1])), 'm', 'scene', 'sample_holder',
     '样品座的真实位置（场景关节sample_holder镜像；不在扫描头中为null）；Naio软件没有位置读数，agent只能用放大镜估计间隙，故只对评测者可见', 'evaluator', reference=HOLDER_REF)
slot('holder.target_m', nullable(number('m')), 'm', 'scene', 'sample_holder',
     '当前电机运动或自动进针将停下的位置（含行程端点、撞针后电机打滑停住、自动进针预计算的终点；只对评测者可见）', 'evaluator', reference=HOLDER_REF)
slot('holder.moving', BOOL, '1', 'derived', 'approach_motor',
     '进针电机正在被驱动（按住Advance/Retract期间、Home到行程端点、Withdraw与自动进针的步进阶段）；撞针后电机打滑时样品座不再前进，但仍显示为驱动中')
slot('holder.angle_rad', number('rad'), 'rad', 'derived', 'sample_holder', '样品座绕轴转角', reference=ANGLE_REF)
slot('holder.sample_id', nullable(TEXT), '1', 'derived', 'holder_magnet_face', '样品座磁性端上的样品')
slot('sample.bench_ids', array(TEXT, 0, MAX_SAMPLES), '1', 'derived', 'holder_magnet_face', '实验台上的样品')
slot('tip.installed', BOOL, '1', 'derived', 'stm_tip', '针尖座上有针尖')
slot('tip.handling', BOOL, '1', 'derived', 'tip_clamp', '正在装/取针尖')
slot('tip.wire_remaining_m', number('m'), 'm', 'derived', 'stm_tip', '工具盒中剩余的Pt/Ir丝长度（开始为30 cm，每个针尖约4 mm）')
slot('z_controller.settings', ZC, '1', 'setpoint', 'controller_housing',
     'Z控制器设置：设定电流（A，取整到25 pA）、针尖电压（V）、P与I增益（无量纲）；字段单位见value_schema各字段的x-unit，槽unit=1仅指整体为结构化值')
slot('approach.state', enum(['retracted', 'approaching', 'engaged']), '1', 'derived', 'approach_motor', '进针状态（engaged＝自动进针完成，Z控制器保持隧穿）')
slot('probe_status.light', enum(['off', 'orange', 'green', 'red', 'blinking']), '1', 'derived', 'controller_housing',
     'Naio软件的探针状态灯：橙＝Z扫描器全伸出仍无设定电流（未接触/失去接触），绿＝在Z量程内保持设定点，红＝Z扫描器全缩回电流仍过大（针尖扎进样品），闪烁＝自动进针中，off＝未就绪')
slot('zscanner.extension_m', nullable(number('m', 0.0, Z_RANGE_M)), 'm', 'sensor', 'scan_head_body',
     'Z控制器输出（名义伸出量：按标称灵敏度换算、16位3.1 pm量化；进针保持时随间隙振动跳动；0全缩回，200 nm全伸出向样品；真实伸出另含隐藏的Z标定误差）；'
     '控制器未就绪（断电或初始化中）时没有读数，为null')
slot('current.reading_a', nullable(number('A')), 'A', 'sensor', 'controller_housing',
     '针尖电流读数（放大器噪声、100 nA饱和）；控制器未就绪（断电或初始化中）时没有读数，为null')
slot('operation.id', nullable(TEXT), '1', 'derived', 'controller_housing', '进行中的操作（记录ID；自动进针为操作ID）')
slot('operation.kind', nullable(enum(['approach', 'scan', 'spectroscopy'])), '1', 'derived', 'controller_housing', '进行中操作的种类')
slot('operation.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'controller_housing', '进行中操作的进度（自动进针按最大步数计）')
slot('operation.remaining_s', nullable(number('s', 0.0)), 's', 'derived', 'controller_housing', '进行中操作的剩余时间上限')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'controller_housing', '进行中的操作属于另一个agent')
slot('scan.lines_completed', nullable(INT), '1', 'derived', 'controller_housing', '进行中扫描已完成的行数（按16行一块更新）')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'controller_housing', '最近完成的记录ID（agent只看到自己或宿主的记录）')
slot('truth.tip', nullable(TIP_TRUTH), '1', 'process_truth', 'stm_tip', '针尖真实状态（种类、尖端半径、第二尖端、电报噪声、拾取材料、偏离样品座轴线、撞针后的回缩）', 'evaluator')
slot('truth.gap_m', nullable(number('m')), 'm', 'process_truth', 'stm_tip', '针尖尖端到其正下方样品表面的真实距离（接触时为点接触间隙0.25 nm；样品座不在扫描头中为null）', 'evaluator')
slot('truth.setpoint_gap_m', nullable(number('m')), 'm', 'process_truth', 'stm_tip', '当前材料与Z控制器设置下达到设定电流的间隙（达不到为null）', 'evaluator')
slot('truth.drift_m', array(number('m'), 3, 3), 'm', 'process_truth', 'scan_head_body', '热漂移累计（x、y：针尖相对样品的横向位移，样品坐标；z：样品表面向针尖的位移）', 'evaluator',
     reference='样品坐标x/y（样品表面内）与隧穿轴z（+为样品靠近针尖）；自声明实验室条件起累计')
slot('truth.samples', {'type': 'object'}, '1', 'process_truth', 'holder_magnet_face', '样品真实定义（模板、材料、形貌参数、种子、倾角、污染、解理次数）', 'evaluator')
slot('truth.calibration', obj(dict(x=number('1'), y=number('1'), z=number('1'))), '1', 'process_truth', 'scan_head_body', '扫描器标定误差（真实位移/标称位移−1）', 'evaluator')
slot('truth.faults', array(TEXT, 0, 8), '1', 'process_truth', 'granite_table', '注入的持续故障', 'evaluator')


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


PURE_ANALYSIS = {'analysis.roughness', 'analysis.line_profile', 'analysis.step_height', 'analysis.lattice', 'analysis.iv', 'analysis.iz'}
ASYNC = {'approach.start', 'scan.start', 'spectroscopy.iv', 'spectroscopy.iz'}
ROBOT = {'cover.lift', 'cover.place', 'tip.remove', 'tip.prepare', 'sample.mount', 'sample.unmount', 'sample.cleave', 'holder.insert', 'holder.push', 'holder.rotate',
         'holder.remove', 'maintenance.clean_guides'}


def _rot(v, th):
    c, s = math.cos(th), math.sin(th)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1])


class NanosurfNaiostmRuntime:
    """Nanosurf NaioSTM: controller and Naio software, magnifying cover, sample holder with the stick-slip approach motor, cut Pt/Ir tip, the
    automatic approach, the Z feedback on the tunnelling current, constant-current / constant-height imaging, I-V and I-z spectroscopy and the
    analysis of the records (SI units)."""

    def __init__(self, instance_id=INSTANCE_ID):
        self.instance_id = instance_id
        self.t = 0.0
        self.revision = 0
        self._large_requests = {}
        self.plant = None
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.faults = set()
        self.fault_log = []
        self.cover_lifted = False
        self.holder = dict(location='container', q=None, drive=None, angle=0.0, sample=None, stop=None, view_bias=1.0)
        self.tip = None                                             # the factory tip: drawn when the plant (noise seed) is declared
        self.tip_installed = True
        self.tip_work = None
        self.wire_m = WIRE_LENGTH_M
        self.tip_count = 1
        self.zc = dict(DEFAULT_ZC)
        self.samples = {}
        self.engaged = None
        self.records = {}
        self.seq = dict(img=0, spc=0, pr=0, op=0, tip=1, sample=0, insert=0, pulse=0)
        self.op = None
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.kicks = []
        self.cal = dict(x=0.0, y=0.0, z=0.0)
        self.creep = None
        self.scan_centre = (0.0, 0.0)
        self._principals, self._requests, self._owners = {}, {}, {}
        self._last_check_t = 0.0
        self._bare = None
        self._crash_keys = set()

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
        return NanosurfNaiostmSession(self, name)

    def _event(self, kind, code, message, rid=None, t=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t if t is None else t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _seed(self):
        return self.plant['noise_seed'] if self.plant else 0

    def _rng(self, *keys):
        return np.random.default_rng([self._seed()] + [int(k) & 0xFFFFFFFF for k in keys])

    def _ready(self):
        return self.power and self.state == 'ready'

    def _kick(self, kind, t=None):
        """A thermal disturbance: the mechanical loop settles by a random displacement of the documented order with time constant 900 s."""
        t = self.t if t is None else t
        r = self._rng(211, len(self.kicks) + 1)
        v = r.standard_normal(3)
        v = v / max(np.linalg.norm(v), 1e-12) * DRIFT_KICK_M[kind]
        self.kicks.append((t, [float(x) for x in v]))

    # ---------------------------------------------------------------- drift, surfaces, gap
    def _drift(self, t=None):
        t = self.t if t is None else t
        if self.plant is None:
            return [0.0, 0.0, 0.0]
        out = [0.0, 0.0, 0.0]
        for tk, v in self.kicks:
            if t > tk:
                f = 1.0 - math.exp(-(t - tk) / DRIFT_TAU_S)
                for i in range(3):
                    out[i] += v[i] * f
        amp = LOOP_M_PER_K * self.plant['temperature_stability_k'] * math.sqrt(2.0 / 3.0)
        if amp > 0:
            r = _rng(self._seed(), 223)
            ph = r.random((3, len(FLUCT_PERIODS_S))) * 2 * math.pi
            for i in range(3):
                out[i] += amp * sum(math.sin(2 * math.pi * t / P + ph[i, j]) for j, P in enumerate(FLUCT_PERIODS_S))
        return out

    def _bare_surface(self):
        if self._bare is None:
            self._bare = Surface(dict(type='rough', rq_m=SUPPORT_RQ_M, corr_length_m=150e-9), 'fe', self._seed() * 3 + 7, [0.0, 0.0])
        return self._bare

    def _tip_point(self, t=None):
        """Point of the sample frame (sample centre = origin) under the tip at the scanner centre, with the lateral drift."""
        off = self.tip['offset'] if self.tip else [0.0, 0.0]
        p = _rot(off, -self.holder['angle'])
        d = self._drift(t)
        return (p[0] + d[0], p[1] + d[1])

    def _site(self, p):
        """(surface, material id, height of its nominal top above the magnetic face) of the point p of the holder face."""
        s = self.samples.get(self.holder['sample'])
        if s is None:
            return self._bare_surface(), 'fe', 0.0
        w, l = s['size_m']
        inside = (p[0] ** 2 + p[1] ** 2 <= (w / 2) ** 2) if s['shape'] == 'disc' else (abs(p[0]) <= w / 2 and abs(p[1]) <= l / 2)
        if inside:
            return s['surface'], s['material'], s['stack_m']
        if p[0] ** 2 + p[1] ** 2 <= (s['support_d_m'] / 2) ** 2:
            return s['support_surface'], 'ni', s['support_t_m']
        return self._bare_surface(), 'fe', 0.0

    def _gap_mech(self, t=None, ext=0.0):
        """Gap (m) between the tip apex and the surface under it for the Z extension ext; None when the holder is not in the head."""
        if self.holder['location'] != 'head' or not self.tip_installed or self.tip is None:
            return None
        p = self._tip_point(t)
        surf, _mid, top = self._site(p)
        h, _ = surf.evaluate(np.array([p[0]]), np.array([p[1]]))
        return self.holder['q'] - top - float(h[0]) - self._drift(t)[2] + self.tip['recess'] - ext

    def _material_here(self, t=None):
        return self._site(self._tip_point(t))[1] if self.holder['location'] == 'head' else None

    def _dset(self, t=None, zc=None, mid=None):
        mid = mid or self._material_here(t)
        if mid is None or self.plant is None:
            return None
        zc = zc or self.zc
        return setpoint_gap(material(mid), zc['tip_voltage_v'], zc['setpoint_a'], self.plant['humidity'], self.plant['room_k'])

    def _zr(self):
        """Real full Z extension: the nominal 200 nm range of the 16-bit output times (1 + hidden Z calibration error)."""
        return Z_RANGE_M * (1.0 + self.cal['z'])

    def _z_output(self, ext_real):
        """What the controller reports for a real Z extension: nominal (real / (1 + cal_z)) on the 3.1 pm grid of the 16-bit output."""
        return float(min(Z_RANGE_M, max(0.0, round(ext_real / (1.0 + self.cal['z']) / Z_DAC_M) * Z_DAC_M)))

    def _vib_rms(self, t=None):
        t = self.t if t is None else t
        return VIBRATION_M[self.plant['floor_vibration']] * (COVER_OFF_VIBRATION if self.cover_lifted else 1.0) * (VIBRATION_BURST_FACTOR if self._burst(t) else 1.0)

    def _z_state(self, t=None):
        """Engaged idle feedback: real Z extension, gap (raw: before the point-contact clamp), status light colour and the true current at time t.
        Without any current at this tip voltage (no set-point gap) the feedback extends the Z scanner fully."""
        G = self._gap_mech(t)
        d = self._dset(t)
        ZR = self._zr()
        if d is None:
            ext, req = ZR, math.inf
        else:
            req = G - d[0]
            ext = min(ZR, max(0.0, req))
        raw = G - ext
        gap = max(raw, D_CONTACT_M)
        if d is not None and req < 0.0:
            status = 'red'                                              # current too high with the Z scanner fully retracted
        elif d is None or req > ZR:
            status = 'orange'                                           # no set-point current within the Z range (0 V: no current at all)
        else:
            status = 'green'
        i = self._true_current(gap, t)
        return dict(ext=ext, gap=gap, raw=raw, status=status, current=i, req=req)

    def _true_current(self, gap, t=None, zc=None):
        mid = self._material_here(t)
        if mid is None or gap is None or self.plant is None:
            return 0.0
        zc = zc or self.zc
        mat = material(mid)
        i = float(tunnel_current(zc['tip_voltage_v'], np.array([max(gap, D_CONTACT_M)]), mat, barrier_ev(mat, self.plant['humidity']),
                                 KB * self.plant['room_k'] / E_CH)[0])
        return float(np.clip(i, -I_SAT_A, I_SAT_A))

    def _holder_moving(self):
        """The approach motor is being driven (a timed / homing move, or the stepping phase of the automatic approach); a stalled holder still counts."""
        h = self.holder
        if h['location'] != 'head':
            return False
        o = self.op
        if o is not None and o['kind'] == 'approach':
            return self.t < o['extra']['drive_end'] - 1e-12
        return h['drive'] is not None

    def _motor_speed(self, drive):
        return drive['v_nom'] * self._motor_factor()

    def _drive_limits(self, drive):
        """Holder positions a motor move can reach: the travel ends and, toward the tip past contact, the stall point of the bent wire."""
        lo, hi = HOLDER_TRAVEL_M
        st = self.holder['stop']
        if drive['dir'] < 0 and st is not None:
            lo = max(lo, st['q_stall'])
        return lo, hi

    def _holder_target(self):
        """Where the running motor move or automatic approach leaves the holder (evaluator view: travel end, stall and approach outcome included)."""
        h = self.holder
        if h['location'] != 'head':
            return None
        o = self.op
        if o is not None and o['kind'] == 'approach':
            return o['extra']['q_end']
        dv = h['drive']
        if dv is None:
            return h['q']
        lo, hi = self._drive_limits(dv)
        if dv['until'] is None:
            return hi if dv['dir'] > 0 else lo
        return min(hi, max(lo, h['q'] + dv['dir'] * self._motor_speed(dv) * max(0.0, dv['until'] - self.t)))

    def _motor_factor(self):
        return DIRTY_MOTOR_FACTOR if 'approach_motor_dirty' in self.faults else 1.0

    def _contact_q(self):
        """Holder position at which the (retracted) tip touches the surface under it."""
        g = self._gap_mech()
        return None if g is None else self.holder['q'] - (g - D_CONTACT_M)

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        o = self.op
        prog = rem = None
        if o is not None and o['kind'] == 'approach':                     # cycles done / max steps and the bound: the outcome is not known in advance
            prog = min(1.0, self._approach_cycles(o) / o['extra']['max_steps'])
            rem = max(0.0, o['t0'] + (o['extra']['max_steps'] + 1) * CYCLE_S + APPROACH_SETTLE_S - self.t)
        elif o is not None:
            prog = min(1.0, max(0.0, (self.t - o['t0']) / o['duration_s'])) if o['duration_s'] > 0 else 0.0
            rem = max(0.0, o['t0'] + o['duration_s'] - self.t)
        ext, light, cur, z_out, i_out = 0.0, 'off', 0.0, None, None      # no readings while the controller is off or initialising
        if self._ready():
            light = 'orange'
            jitter = 0.0
            z_cmd = None
            if o is not None and o['kind'] == 'approach':
                light, ext, z_cmd = 'blinking', self._approach_extension(o), self._approach_command(o)   # real (contact-limited) vs commanded
            elif self.engaged is not None:
                zs = self._z_state()
                ext, light, cur = zs['ext'], zs['status'], zs['current']
                jitter = self._vib_rms()                                   # the feedback follows the gap vibration
            n = self._reading_noise()
            i_out = float(np.clip(cur * (1.0 + I_REL_NOISE * n[0]) + I_NOISE_A * n[1], -I_SAT_A, I_SAT_A))
            z_out = self._z_output((ext if z_cmd is None else z_cmd) + jitter * n[2])
        h = self.holder
        g = self._gap_mech(ext=ext) if self.plant is not None else None
        d = self._dset() if self.holder['location'] == 'head' and self.plant is not None else None
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'power.led_on': self.power,
            'system.state': self.state, 'cover.lifted': self.cover_lifted, 'cover.lift_m': COVER_LIFT_M if self.cover_lifted else 0.0,
            'holder.location': h['location'], 'holder.position_m': h['q'] if h['location'] == 'head' else None,
            'holder.target_m': self._holder_target(), 'holder.moving': self._holder_moving(), 'holder.angle_rad': h['angle'],
            'holder.sample_id': h['sample'], 'sample.bench_ids': sorted(k for k, s in self.samples.items() if s['location'] == 'bench'),
            'tip.installed': self.tip_installed, 'tip.handling': self.tip_work is not None, 'tip.wire_remaining_m': self.wire_m,
            'z_controller.settings': dict(self.zc),
            'approach.state': 'approaching' if o is not None and o['kind'] == 'approach' else ('engaged' if self.engaged is not None else 'retracted'),
            'probe_status.light': light, 'zscanner.extension_m': z_out, 'current.reading_a': i_out,
            'operation.id': (o['record_id'] or o['op']) if o else None, 'operation.kind': o['kind'] if o else None, 'operation.progress': prog,
            'operation.remaining_s': rem, 'operation.started_by_other_agent': False,
            'scan.lines_completed': self.records[o['record_id']]['meta']['lines_completed'] if o is not None and o['kind'] == 'scan' else None,
            'result.last_record_id': self._last_record(),
            'truth.tip': self._tip_truth(), 'truth.gap_m': None if g is None else max(g, D_CONTACT_M), 'truth.setpoint_gap_m': d[0] if d else None,
            'truth.drift_m': self._drift(),
            'truth.samples': self._truth_samples(), 'truth.calibration': dict(self.cal), 'truth.faults': sorted(self.faults),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _reading_noise(self):
        r = _rng(self._seed(), 907, int(round(self.t * 1000.0)))
        return r.standard_normal(3)

    def _tip_truth(self):
        tp = self.tip
        if tp is None or not self.tip_installed:
            return None
        return dict(number=self.tip_count, kind=tp['kind'], apex_radius_m=tp['apex_r'], second_apex=copy.deepcopy(tp['second']), telegraph=copy.deepcopy(tp['telegraph']),
                    picked_up=tp['pickup'], offset_m=list(tp['offset']), recess_m=tp['recess'])

    def _truth_samples(self):
        return {k: {kk: copy.deepcopy(vv) for kk, vv in s.items() if kk not in ('surface', 'support_surface')} for k, s in self.samples.items()}

    def _last_record(self, keep=None):
        done = [k for k, r in self.records.items() if r['status'] == 'completed' and (keep is None or keep(k))]
        return max(done, key=lambda k: (self.records[k]['finished_s'] or 0.0, k), default=None)

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
        if CAPS[capability_id]['kind'] not in ('query', 'simulation') and capability_id not in PURE_ANALYSIS:
            self.revision += 1
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_ready(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')
        if self.state != 'ready':
            raise ActionRejected('NOT_READY')

    def _need_idle(self):
        if self.op is not None:
            raise ActionRejected('MEASUREMENT_RUNNING')

    def _need_retracted(self):
        if self.engaged is not None:
            raise ActionRejected('ENGAGED')

    def _need_still(self):
        if self._holder_moving() or self.tip_work is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')

    def _need_head(self):
        if self.holder['location'] != 'head':
            raise ActionRejected('HOLDER_NOT_IN_HEAD')

    def _need_cover_lifted(self):
        if not self.cover_lifted:
            raise ActionRejected('COVER_ON')

    def _need_tip(self):
        if not self.tip_installed or self.tip_work is not None:
            raise ActionRejected('NO_TIP')

    def _look_noise(self, magnifier, t=None, insertion=None):
        """Judgement jitter of one look at time t at the holder of the given insertion (default: the one in the head); additive, 3-sigma
        clipped; the eye and the 10x magnifier are independent viewers, every insertion is looked at afresh."""
        t = self.t if t is None else t
        k = self.seq['insert'] if insertion is None else insertion
        r = _rng(self._seed(), 43 if magnifier else 47, int(round(t * 1000.0)), k)
        return float(np.clip(r.standard_normal(), -3.0, 3.0)) * (MAGNIFIER_SIGMA_M if magnifier else EYE_SIGMA_M)

    def _seen_gap(self, g, magnifier, insertion=None):
        """What a look sees: the true gap times the view bias of this insertion plus the jitter of the look."""
        return g * self.holder['view_bias'] + self._look_noise(magnifier, insertion=insertion)

    def _eye_gap(self, g, insertion=None):
        """The gap as the operator / robot judges it by eye with the cover lifted - exactly what magnifier.view reports then: 0 (appears to
        touch) when seen closer than 0.2 mm, otherwise the seen gap (view bias and jitter) in 0.1 mm steps."""
        seen = self._seen_gap(g, False, insertion)
        return 0.0 if seen < EYE_TOUCH_M else round(seen / EYE_QUANT_M) * EYE_QUANT_M

    def _hand_gap(self, q):
        """Gap between the tip and the surface under it if the holder stood at q (m); None without a tip."""
        g = self._gap_mech()
        return None if g is None else g + (q - self.holder['q'])

    # ---------------------------------------------------------------- setup
    def _cmd_plant_configure(self, room_k, humidity, temperature_stability_k, floor_vibration, noise_seed):
        if self.power or self.op is not None:
            raise ActionRejected('POWER_ON')
        first = self.plant is None
        self.plant = dict(room_k=room_k, humidity=humidity, temperature_stability_k=temperature_stability_k, floor_vibration=floor_vibration, noise_seed=noise_seed)
        if first:
            r = self._rng(17)
            if self.tip is None and self.tip_installed:
                self.tip = draw_tip(r, 'sharp')                        # manual: the tip used for the factory calibration is installed on delivery
            c = np.clip(r.standard_normal(3) * CAL_SIGMA, -0.08, 0.08)
            self.cal = dict(x=float(c[0]), y=float(c[1]), z=float(c[2]))
            self._bare = None
        return dict(plant=dict(self.plant))

    def _cmd_sample_provide(self, sample_id, template_id, seed, tilt_rad, contamination=0.0, topography_overrides=None):
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
            raise ActionRejected('INVALID_ARGUMENT')
        if work > 1.0:
            raise ActionRejected('SIMULATION_LIMIT')
        sup = tpl['support']
        s = dict(sample_id=sample_id, template_id=template_id, material=tpl['material'], shape=tpl['shape'], size_m=list(flat_value(tpl['size_m'])),
                 thickness_m=float(flat_value(tpl['thickness_m'])), glue_m=float(flat_value(tpl['glue_m'])), support_t_m=float(flat_value(sup['thickness_m'])),
                 support_d_m=float(flat_value(sup['diameter_m'])), cleavable=bool(flat_value(tpl['cleavable'])), contamination_per_h=float(flat_value(tpl['contamination_per_h'])),
                 contamination0=float(contamination), contamination_t0=self.t, topography=topo, seed=seed, tilt_rad=list(tilt_rad), location='bench', cleaves=0)
        s['stack_m'] = s['support_t_m'] + s['glue_m'] + s['thickness_m']
        self._make_surfaces(s)
        self.samples[sample_id] = s
        self._event('sample', 'SAMPLE_PROVIDED', f'样品{sample_id}放在实验台上')
        return dict(sample_id=sample_id, template_id=template_id, location='bench')

    def _make_surfaces(self, s):
        s['surface'] = Surface(s['topography'], s['material'], s['seed'] * 131 + s['cleaves'], s['tilt_rad'])
        s['support_surface'] = Surface(dict(type='rough', rq_m=SUPPORT_RQ_M, corr_length_m=150e-9), 'ni', s['seed'] * 131 + 99, s['tilt_rad'])

    def _contamination(self, s, t=None):
        t = self.t if t is None else t
        return min(1.0, s['contamination0'] + s['contamination_per_h'] * max(0.0, t - s['contamination_t0']) / 3600.0)

    # ---------------------------------------------------------------- robot skills
    def _cmd_cover_lift(self):
        self._need_plant()
        if not self.cover_lifted:
            self.cover_lifted = True
            self._kick('cover')
            self._event('cover', 'COVER_LIFTED', '放大罩已提起')
        return dict(lifted=True)

    def _cmd_cover_place(self):
        self._need_plant()
        if self.cover_lifted:
            self.cover_lifted = False
            self._kick('cover')
            self._event('cover', 'COVER_PLACED', '放大罩已罩上')
        return dict(lifted=False)

    def _need_tip_access(self):
        self._need_plant()
        self._need_cover_lifted()
        if self.holder['location'] == 'head':
            raise ActionRejected('HOLDER_IN_HEAD')
        if self.tip_work is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')

    def _cmd_tip_remove(self):
        self._need_tip_access()
        if not self.tip_installed:
            raise ActionRejected('NO_TIP')
        self.tip_work = dict(until=self.t + TIP_WORK_S, action='remove')
        self.tip_installed = False
        self._event('tip', 'TIP_REMOVING', '用镊子取出旧针尖')
        return dict(removed=True, duration_s=TIP_WORK_S)

    def _cmd_tip_prepare(self):
        self._need_tip_access()
        if self.tip_installed:
            raise ActionRejected('TIP_INSTALLED')
        if self.wire_m < TIP_CUT_LENGTH_M - 1e-12:
            raise ActionRejected('WIRE_EXHAUSTED')
        self.wire_m = max(0.0, self.wire_m - TIP_CUT_LENGTH_M)
        self.led['wire_used_m'].add(TIP_CUT_LENGTH_M)
        self.seq['tip'] += 1
        self.tip_work = dict(until=self.t + TIP_WORK_S, action='prepare', tip=draw_tip(self._rng(19, self.seq['tip'])))
        self._event('tip', 'TIP_CUTTING', '剪切拉断Pt/Ir丝，装到针尖压片下')
        return dict(tip_number=self.seq['tip'], wire_remaining_m=self.wire_m, duration_s=TIP_WORK_S)

    def _cmd_sample_mount(self, sample_id):
        self._need_plant()
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if self.holder['location'] == 'head':
            raise ActionRejected('HOLDER_IN_HEAD')
        if self.holder['sample'] is not None:
            raise ActionRejected('HOLDER_OCCUPIED')
        s['location'] = 'holder'
        self.holder['sample'] = sample_id
        self._event('sample', 'SAMPLE_MOUNTED', f'样品{sample_id}放到样品座磁性端')
        return dict(sample_id=sample_id, stack_thickness_m=round(s['stack_m'] / 0.05e-3) * 0.05e-3)

    def _cmd_sample_unmount(self):
        self._need_plant()
        if self.holder['location'] == 'head':
            raise ActionRejected('HOLDER_IN_HEAD')
        sid = self.holder['sample']
        if sid is None:
            raise ActionRejected('HOLDER_EMPTY')
        self.samples[sid]['location'] = 'bench'
        self.holder['sample'] = None
        self._event('sample', 'SAMPLE_UNMOUNTED', f'样品{sid}取回实验台')
        return dict(sample_id=sid)

    def _cmd_sample_cleave(self, sample_id):
        self._need_plant()
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if not s['cleavable']:
            raise ActionRejected('NOT_CLEAVABLE')
        if s['thickness_m'] - CLEAVE_LOSS_M < 0.05e-3:
            raise ActionRejected('SAMPLE_TOO_THIN')
        s['thickness_m'] -= CLEAVE_LOSS_M
        s['stack_m'] = s['support_t_m'] + s['glue_m'] + s['thickness_m']
        s['cleaves'] += 1
        s['contamination0'], s['contamination_t0'] = 0.0, self.t
        self._make_surfaces(s)
        self.led['cleaves'].add(1.0)
        self._event('sample', 'SAMPLE_CLEAVED', f'样品{sample_id}用胶带解理（第{s["cleaves"]}次）')
        return dict(sample_id=sample_id, thickness_m=s['thickness_m'], cleaves=s['cleaves'])

    def _holder_hand_check(self, q, min_gap):
        g = self._hand_gap(q)
        if g is not None and self._eye_gap(g) < min_gap:
            raise ActionRejected('COLLISION_RISK')

    def _cmd_holder_insert(self, position_m, angle_rad):
        self._need_plant()
        self._need_cover_lifted()
        if self.holder['location'] == 'head':
            raise ActionRejected('HOLDER_IN_HEAD')
        if self.tip_work is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')
        h = self.holder
        saved = dict(h)
        bias = float(np.clip(1.0 + 0.15 * self._rng(29, self.seq['insert'] + 1).standard_normal(), 0.6, 1.4))    # view of this insertion
        h.update(location='head', q=position_m, drive=None, angle=angle_rad, stop=None, view_bias=bias)
        g = self._gap_mech()
        if g is not None and self._eye_gap(g, self.seq['insert'] + 1) < PUSH_MIN_GAP_M:   # the look at this (new) insertion
            h.clear()
            h.update(saved)                                             # a rejected insertion leaves the holder exactly as it was
            raise ActionRejected('COLLISION_RISK')
        self.seq['insert'] += 1
        self._kick('holder')
        self.led['holder_insertions'].add(1.0)
        self._event('holder', 'HOLDER_INSERTED', '样品座放进U形槽（导向钢球与进针电机上）')
        return dict(position_m=position_m, angle_rad=angle_rad)

    def _need_hand_on_holder(self):
        self._need_plant()
        self._need_cover_lifted()
        self._need_head()
        self._need_idle()
        self._need_retracted()
        self._need_still()

    def _cmd_holder_push(self, position_m):
        self._need_hand_on_holder()
        self._holder_hand_check(position_m, PUSH_MIN_GAP_M)
        self.holder['q'] = position_m
        self._kick('holder')
        self._event('holder', 'HOLDER_PUSHED', f'手推样品座到{position_m * 1e3:.3f} mm')
        return dict(position_m=position_m)

    def _cmd_holder_rotate(self, angle_rad):
        self._need_hand_on_holder()
        old = self.holder['angle']
        self.holder['angle'] = angle_rad
        g = self._gap_mech()
        if g is not None and self._eye_gap(g) < ROTATE_MIN_GAP_M:
            self.holder['angle'] = old
            raise ActionRejected('COLLISION_RISK')
        self._kick('holder')
        self._event('holder', 'HOLDER_ROTATED', '转动样品座')
        return dict(angle_rad=angle_rad)

    def _cmd_holder_remove(self):
        self._need_hand_on_holder()
        g = self._gap_mech()
        if g is not None and self._eye_gap(g) < PUSH_MIN_GAP_M:
            raise ActionRejected('COLLISION_RISK')
        self.holder.update(location='container', q=None, drive=None, stop=None)
        self._event('holder', 'HOLDER_REMOVED', '样品座取出放回容器')
        return dict(sample_id=self.holder['sample'])

    def _cmd_maintenance_clean_guides(self):
        self._need_plant()
        self._need_cover_lifted()
        if self.holder['location'] == 'head':
            raise ActionRejected('HOLDER_IN_HEAD')
        self.faults.discard('approach_motor_dirty')
        self._event('holder', 'GUIDES_CLEANED', '用乙醇清洁导向钢球与进针电机表面')
        return dict(cleaned=True)

    # ---------------------------------------------------------------- power, Z-controller
    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            self.power, self.state, self.state_t0 = True, 'booting', self.t
            self._kick('power')
            self._event('power', 'POWER_ON', '控制器上电，蓝色LED亮，Naio软件初始化')
        elif not enabled and self.power:
            if self.op is not None:
                self._fail_op('POWER_OFF')
            self._disengage('断电：Z扫描器回零')
            self.holder['drive'], self.holder['stop'] = None, None
            self.power, self.state = False, 'off'
            self._event('power', 'POWER_OFF', '控制器断电')
        return dict(enabled=self.power, state=self.state)

    def _cmd_power_press_button(self):
        return self._cmd_power_set(not self.power)

    def _disengage(self, why):
        if self.engaged is not None:
            self.engaged = None
            self._event('approach', 'TIP_RETRACTED', why)

    def _cmd_z_controller_set(self, setpoint_a, tip_voltage_v, p_gain, i_gain):
        self._need_ready()
        if self.op is not None and self.op['kind'] != 'scan':
            raise ActionRejected('MEASUREMENT_RUNNING')
        sp = max(CURRENT_RANGE_A[0], min(CURRENT_RANGE_A[1], round(setpoint_a / SETPOINT_STEP_A) * SETPOINT_STEP_A))
        self.zc = dict(setpoint_a=sp, tip_voltage_v=float(tip_voltage_v), p_gain=float(p_gain), i_gain=float(i_gain))
        if self.op is not None:
            self.op['extra']['zc_log'].append((self.t, dict(self.zc)))
        elif self.engaged is not None:
            self._idle_contact_check()                                       # the feedback follows the new setting at once (e.g. 0 V: full extension)
        return dict(settings=dict(self.zc))

    # ---------------------------------------------------------------- approach motor
    def _need_motor(self):
        self._need_ready()
        self._need_head()
        self._need_idle()
        if self.tip_work is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')

    def _start_drive(self, direction, v_nom, duration):
        """Drive the stick-slip motor at the nominal speed v_nom (x 0.2 while the guides are dirty, from the moment they are) for `duration` s
        (None: until the travel end).  Toward the tip past the contact point the tip is crashed and its wire bends back until the motor stalls
        BEND_M past contact; the holder then stays while the motor is still driven.  Nothing of this is reported to the caller."""
        h = self.holder
        h['stop'] = None
        qc = self._contact_q()
        if direction < 0 and qc is not None:
            h['stop'] = dict(q_crash=qc, q_bend=qc, q_stall=min(h['q'], qc - BEND_M), recess0=self.tip['recess'], kind='motor')
        h['drive'] = dict(dir=direction, v_nom=v_nom, until=None if duration is None else self.t + duration)

    def _need_motor_still(self):
        if self._holder_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')                  # Stop the running motor move first

    def _cmd_approach_advance(self, duration_s):
        self._need_motor()
        self._need_motor_still()
        self._need_retracted()
        self._start_drive(-1, MOTOR_SPEED_M_S, duration_s)
        return dict(travel_m=MOTOR_SPEED_M_S * duration_s, duration_s=duration_s)      # the button time and the nominal travel, not a position

    def _cmd_approach_retract(self, duration_s):
        self._need_motor()
        self._need_motor_still()
        self._disengage('Retract：Z扫描器缩回，电机后退')
        self._start_drive(+1, MOTOR_SPEED_M_S, duration_s)
        return dict(travel_m=MOTOR_SPEED_M_S * duration_s, duration_s=duration_s)

    def _cmd_approach_home(self):
        self._need_motor()
        self._need_motor_still()
        self._disengage('Home：Z扫描器缩回，电机退到最远')
        self._start_drive(+1, MOTOR_SPEED_M_S, None)
        return dict(travel_m=None, duration_s=None)                                    # runs to the end of travel (watch holder.moving)

    def _cmd_approach_stop(self):
        self._need_ready()
        self._need_idle()                                               # a running automatic approach is stopped by cancelling its operation
        if self.holder['location'] != 'head' or self.holder['drive'] is None:
            return dict(stopped=False)
        self.holder['drive'], self.holder['stop'] = None, None
        self._event('motor', 'MOTOR_STOPPED', '电机停止')
        return dict(stopped=True)

    @staticmethod
    def _step_nominal(move_speed_percent):
        return 0.0 if move_speed_percent < MOTOR_MIN_PERCENT else STEP_M_PER_PERCENT * move_speed_percent

    def _cmd_approach_withdraw(self, steps, move_speed_percent):
        self._need_motor()
        self._need_motor_still()
        self._disengage('Withdraw：Z扫描器缩回，电机后退')
        step = self._step_nominal(move_speed_percent)
        if step > 0:
            self.led['approach_steps'].add(float(steps))                  # only executed motor steps are booked (below 10 % the motor does not move)
        self._start_drive(+1, step / CYCLE_S, steps * CYCLE_S)
        return dict(travel_m=step * steps, duration_s=steps * CYCLE_S)

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
        s = self.samples.get(self.holder['sample'])
        self.records[rid] = dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, meta=meta, warnings=[], data=data,
                                 template_source=dict(library=TUNNEL_PATH.name, sample_library=SAMPLE_LIB_PATH.name,
                                                      template_id=s['template_id'] if s else None, substance=self._material_here()))
        return rid

    def _record_bytes(self):
        total = 0
        for rec in self.records.values():
            for v in (rec.get('data') or {}).values():
                if isinstance(v, dict):
                    total += sum(a.nbytes for a in v.values() if isinstance(a, np.ndarray))
                elif isinstance(v, np.ndarray):
                    total += v.nbytes
                elif isinstance(v, list):
                    total += sum(sum(x.nbytes for x in c.values() if isinstance(x, np.ndarray)) for c in v if isinstance(c, dict))
        return total

    def _cmd_approach_start(self, move_speed_percent, max_steps):
        self._need_motor()
        self._need_retracted()
        self._need_tip()
        if self._holder_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        x = dict(move_speed_percent=move_speed_percent, max_steps=max_steps, j0=0, steps_before=0)
        self._plan_approach(x, self.t, self.holder['q'], max_steps)
        oid = self._new_op('approach', None, x['end'] - self.t, x)
        self.led['approaches'].add(1.0)
        self._event('approach', 'APPROACH_STARTED', '自动进针开始', rid=oid)
        return dict(operation_id=oid)

    def _plan_approach(self, x, t_plan, q0, n_max):
        """Outcome of the automatic approach from the cycle starting at t_plan with the holder at q0 (at most n_max further steps; the step
        = 3 nm x move speed x motor factor, 0 below 10 %).  Cycle j sweeps the Z scanner over its real range after j steps; the first cycle
        whose sweep reaches the set-point gap engages, a sweep that reaches the point contact first (no current: insulator, oxide, 0 V) runs
        the tip into the surface and the motor then pushes the bent wire until it stalls.  Kept internal: callers see the cycles done only."""
        step = self._step_nominal(x['move_speed_percent']) * self._motor_factor()
        d = self._dset(t_plan)
        g0 = self._gap_mech(t_plan)
        ZR = self._zr()
        outcome, k, stop = 'max_steps', n_max, None

        def cycles_to(gap_needed):
            need = g0 - ZR - gap_needed
            if need <= 0:
                return 0
            return math.ceil(need / step - 1e-12) if step > 0 else None
        k_found = cycles_to(d[0]) if d is not None else None
        if k_found is not None and k_found <= n_max:
            g = g0 - k_found * step
            k = k_found
            outcome = 'found' if g >= d[0] else ('overshoot' if g > D_CONTACT_M else 'crash_step')
            if outcome == 'crash_step':                                          # the step itself drove the sample into the retracted tip
                qc = q0 - (g0 - D_CONTACT_M)
                stop = dict(q_crash=qc, q_bend=qc, q_stall=min(q0, qc - BEND_M), recess0=self.tip['recess'], kind='approach_step')
        else:
            k_touch = cycles_to(D_CONTACT_M)
            if k_touch is not None and k_touch <= n_max:                         # no set-point current: the Z extension runs into the surface
                outcome = 'run_in'
                qc = q0 - (g0 - D_CONTACT_M)
                stop = dict(q_crash=q0 - k_touch * step, q_bend=qc, q_stall=min(q0, qc - BEND_M), recess0=self.tip['recess'], kind='approach_run_in')
        settle = APPROACH_SETTLE_S if outcome in ('found', 'overshoot', 'crash_step') else 0.0
        q_end = q0 - k * step
        if stop is not None:
            q_end = max(q_end, stop['q_stall'])                                  # the stick-slip motor stalls against the bent tip wire
        q_end = max(q_end, HOLDER_TRAVEL_M[0])                                   # the holder cannot pass the end of its travel
        drive_end = t_plan + (k + 1) * CYCLE_S
        x.update(outcome=outcome, steps=k, step_m=step, g0=g0, q_start=q0, q_end=q_end, t_plan=t_plan, n_plan=n_max, d_found=d[0] if d else None,
                 drive_end=drive_end, end=drive_end + settle)
        self.holder['stop'], self.holder['drive'] = stop, None

    @staticmethod
    def _approach_q(x, t):
        """Holder position during the approach: one step at the start of every cycle after the first, down to the planned end."""
        j = max(0, math.floor((t - x['t_plan']) / CYCLE_S + 1e-9))
        return max(x['q_start'] - min(j, x['steps']) * x['step_m'], x['q_end'])

    def _approach_extension(self, o, t=None):
        """Real Z extension during the approach: each cycle sweeps 0 -> full range, stopped at the point contact; the engaging cycle stops
        at the set-point gap and holds it during the settling."""
        t = self.t if t is None else t
        x = o['extra']
        G = self._gap_mech(t)
        if G is None:
            return 0.0
        ZR = self._zr()
        rel = (t - x['t_plan']) / CYCLE_S
        j = math.floor(rel + 1e-9)
        ph = min(1.0, max(0.0, rel - j))
        if x['outcome'] in ('found', 'overshoot', 'crash_step') and j >= x['steps']:
            hold = max(0.0, min(ZR, G - x['d_found']))
            return min(ZR * ph, hold) if j == x['steps'] else hold
        return min(ZR * ph, max(0.0, G - D_CONTACT_M))

    def _approach_command(self, o, t=None):
        """Z the controller commands (and reports) during the approach: the full ramp in every cycle whether or not the apex meets the surface
        (the wire takes it); the engaging cycle stops at the detected set point and holds it (real units)."""
        t = self.t if t is None else t
        x = o['extra']
        ZR = self._zr()
        rel = (t - x['t_plan']) / CYCLE_S
        j = math.floor(rel + 1e-9)
        ph = min(1.0, max(0.0, rel - j))
        if x['outcome'] in ('found', 'overshoot', 'crash_step') and j >= x['steps']:
            G = self._gap_mech(t)
            hold = 0.0 if G is None else max(0.0, min(ZR, G - x['d_found']))
            return min(ZR * ph, hold) if j == x['steps'] else hold
        return ZR * ph

    def _approach_steps_done(self, o, t=None):
        """Motor steps executed so far (none below 10 % move speed)."""
        t = self.t if t is None else t
        x = o['extra']
        if x['step_m'] <= 0:
            return x['steps_before']
        last = x['steps'] if x['outcome'] in ('found', 'overshoot', 'crash_step') else x['n_plan']
        return x['steps_before'] + min(last, max(0, math.floor((t - x['t_plan']) / CYCLE_S + 1e-9)))

    def _replan_approach(self):
        """The motor factor changed during the approach: the remaining steps use the new step from the cycle in progress on."""
        o = self.op
        x = o['extra']
        j = max(0, math.floor((self.t - x['t_plan']) / CYCLE_S + 1e-9))
        last = x['steps'] if x['outcome'] in ('found', 'overshoot', 'crash_step') else x['n_plan']
        st = self.holder['stop']
        if j >= last:
            return                                                           # stepping over (engaging / settling)
        done = min(j, x['steps'])
        q_j = max(x['q_start'] - done * x['step_m'], x['q_end'])
        x['steps_before'] += done if x['step_m'] > 0 else 0
        x['j0'] += j
        if st is not None and st.get('crashed'):                             # already run into the surface: only the step length changes,
            step = self._step_nominal(x['move_speed_percent']) * self._motor_factor()     # the crash and the stall point stay
            n = x['n_plan'] - j
            x.update(t_plan=x['t_plan'] + j * CYCLE_S, q_start=q_j, step_m=step, steps=n, n_plan=n,
                     q_end=max(q_j - n * step, st['q_stall'], HOLDER_TRAVEL_M[0]))
            return
        self._plan_approach(x, x['t_plan'] + j * CYCLE_S, q_j, x['n_plan'] - j)
        o['duration_s'] = x['end'] - o['t0']

    def _approach_cycles(self, o, t=None):
        t = self.t if t is None else t
        x = o['extra']
        return int(min(x['max_steps'], x['j0'] + max(0, math.floor((t - x['t_plan']) / CYCLE_S + 1e-9))))

    def _need_engaged_op(self):
        self._need_ready()
        self._need_idle()
        self._need_head()
        self._need_tip()
        if self._holder_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        if self.engaged is None:
            raise ActionRejected('NOT_ENGAGED')

    def _cmd_scan_start(self, image_size_m, time_per_line_s, points, rotation_rad, offset_x_m, offset_y_m, slope_x_rad, slope_y_rad):
        self._need_engaged_op()
        reach = image_size_m / 2 * (abs(math.cos(rotation_rad)) + abs(math.sin(rotation_rad)))
        if abs(offset_x_m) + reach > XY_RANGE_M / 2 + 1e-15 or abs(offset_y_m) + reach > XY_RANGE_M / 2 + 1e-15:
            raise ActionRejected('SCAN_OUT_OF_RANGE')
        if points > SIM_MAX_POINTS:
            raise ActionRejected('SIMULATION_LIMIT')
        if self._record_bytes() + 4 * points * points * 4 > RECORD_MEMORY_BYTES:
            raise ActionRejected('RECORDS_FULL')
        data = {c: {d: np.full((points, points), np.nan, np.float32) for d in DIRECTIONS} for c in CHANNELS}
        dur = points * 2.0 * time_per_line_s
        meta = dict(image_size_m=image_size_m, time_per_line_s=time_per_line_s, points=points, rotation_rad=rotation_rad, offset_m=[offset_x_m, offset_y_m],
                    slope_rad=[slope_x_rad, slope_y_rad], lines_completed=0, channels=list(CHANNELS), directions=list(DIRECTIONS), channel_units=dict(CHANNEL_UNITS),
                    pixel_size_m=[image_size_m / points, image_size_m / points], line_rate_hz=1.0 / (2.0 * time_per_line_s), duration_s=dur,
                    z_controller=dict(self.zc), sample_id=self.holder['sample'], tip_number=self.tip_count, holder_angle_rad=self.holder['angle'])
        rid = self._new_record('IMG', 'img', 'image', meta, data)
        if image_size_m / points < XY_DAC_M:
            self._warn(self.records[rid], 'pixel_below_xy_dac_step')                # the 16-bit XY output repeats positions: not a finer spatial sampling
        if (offset_x_m, offset_y_m) != tuple(self.scan_centre):
            self.creep = dict(t=self.t, dx=offset_x_m - self.scan_centre[0], dy=offset_y_m - self.scan_centre[1])
            self.scan_centre = (offset_x_m, offset_y_m)
        job = dict(size=image_size_m, n=points, tpl=time_per_line_s, rot=rotation_rad, offset=(offset_x_m, offset_y_m), slope=(slope_x_rad, slope_y_rad), t0=self.t)
        oid = self._new_op('scan', rid, dur, dict(job=job, blocks_done=0, zc_log=[(self.t, dict(self.zc))], tip_log=[(self.t, copy.deepcopy(self.tip))]))
        self._event('scan', 'SCAN_STARTED', f'成像{rid}开始（{points}×{points}，{image_size_m * 1e9:.4g} nm，{time_per_line_s:g} s/行）', rid=rid)
        return dict(operation_id=oid)

    def _spec_start(self, kind, params, n_curves, data_points):
        self._need_engaged_op()
        if n_curves > MAX_CURVES:
            raise ActionRejected('TOO_MANY_CURVES')
        if data_points > SIM_MAX_SPEC_POINTS:
            raise ActionRejected('SIMULATION_LIMIT')
        per = 2.0 * params['mod_time_s'] + SPEC_MOVE_S
        meta = dict(kind=kind, **copy.deepcopy(params), curves_completed=0, curves_total=n_curves, per_curve_s=per, z_controller=dict(self.zc),
                    sample_id=self.holder['sample'], tip_number=self.tip_count)
        rid = self._new_record('SPC', 'spc', 'spectroscopy', meta, dict(curves=[]))
        oid = self._new_op('spectroscopy', rid, n_curves * per, dict(per=per, n=n_curves, done=0, tip_log=[(self.t, copy.deepcopy(self.tip))]))
        self._event('spectroscopy', 'SPECTROSCOPY_STARTED', f'谱测量{rid}开始（{n_curves}条）', rid=rid)
        return dict(operation_id=oid)

    def _cmd_spectroscopy_iv(self, start_v, range_v, mod_time_s, data_points, repetitions, positions_m, keep_z_controller):
        if not TIP_VOLTAGE_RANGE_V[0] <= start_v + range_v <= TIP_VOLTAGE_RANGE_V[1] or range_v == 0.0:
            raise ActionRejected('VOLTAGE_OUT_OF_RANGE')
        self._check_positions(positions_m)
        return self._spec_start('current_voltage', dict(start_v=start_v, range_v=range_v, mod_time_s=mod_time_s, data_points=data_points, repetitions=repetitions,
                                                        positions_m=positions_m, keep_z_controller=keep_z_controller), repetitions * len(positions_m), data_points)

    def _cmd_spectroscopy_iz(self, start_m, range_m, mod_time_s, data_points, repetitions, positions_m):
        if range_m == 0.0:
            raise ActionRejected('INVALID_ARGUMENT')
        self._check_positions(positions_m)
        return self._spec_start('current_distance', dict(start_m=start_m, range_m=range_m, mod_time_s=mod_time_s, data_points=data_points, repetitions=repetitions,
                                                         positions_m=positions_m), repetitions * len(positions_m), data_points)

    @staticmethod
    def _check_positions(positions):
        for p in positions:
            if abs(p[0]) > XY_RANGE_M / 2 or abs(p[1]) > XY_RANGE_M / 2:
                raise ActionRejected('SCAN_OUT_OF_RANGE')

    def _cmd_tip_cleaning_pulse(self):
        self._need_ready()
        self._need_head()
        self._need_tip()
        if self.engaged is None:
            raise ActionRejected('NOT_ENGAGED')
        if self.op is not None and self.op['kind'] != 'scan':
            raise ActionRejected('MEASUREMENT_RUNNING')
        self.seq['pulse'] += 1
        r = self._rng(601, self.seq['pulse'])
        tp = self.tip
        nxt = PULSE_TRANSITIONS[tp['kind']]
        kind = str(r.choice(list(nxt), p=np.array(list(nxt.values()))))
        if kind != tp['kind']:
            new = draw_tip(r, kind)
            new['offset'], new['recess'] = tp['offset'], tp['recess']
            self.tip = new
        self.tip['pickup'] = False
        self._log_tip()
        self.led['cleaning_pulses'].add(1.0)
        self._event('tip', 'CLEANING_PULSE', '针尖清洁脉冲（约5 V、100 ms）')
        return dict(voltage_v=CLEANING_PULSE_V, duration_s=CLEANING_PULSE_S)

    def _log_tip(self):
        if self.op is not None and 'tip_log' in self.op['extra']:
            self.op['extra']['tip_log'].append((self.t, copy.deepcopy(self.tip)))

    def _cmd_magnifier_view(self):
        self._need_plant()
        mag = 1.0 if self.cover_lifted else MAGNIFICATION
        out = dict(holder_in_head=self.holder['location'] == 'head', magnification=mag, gap_estimate_m=None, appears_touching=False, reflection_visible=False, tip_over=None)
        g = self._gap_mech()
        if g is None:
            return out
        quant, res = (MAGNIFIER_QUANT_M, MAGNIFIER_TOUCH_M) if not self.cover_lifted else (EYE_QUANT_M, EYE_TOUCH_M)
        p = self._tip_point()
        surf, mid, _top = self._site(p)
        s = self.samples.get(self.holder['sample'])
        out['tip_over'] = 'sample' if s is not None and surf is s['surface'] else ('support' if s is not None and surf is s['support_surface'] else 'holder_face')
        out['reflection_visible'] = (surf.t.get('rq_m') or 0.0) <= 5e-9
        seen = self._seen_gap(g, not self.cover_lifted)                  # view bias and the jitter of this look, also for 'touching'
        if seen < res:
            out.update(appears_touching=True, gap_estimate_m=0.0)
        elif g <= 5e-3:
            out['gap_estimate_m'] = round(seen / quant) * quant
        return out

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
        elif o['kind'] == 'spectroscopy':
            self._finish(self.records[o['record_id']], 'failed')
        else:
            self.led['approach_steps'].add(float(self._approach_steps_done(o)))
            self.holder['drive'], self.holder['stop'] = None, None           # the approach stops where the holder is
        self._end_op('failed', code)
        self._event('error' if o['kind'] != 'approach' else 'approach', code, ERROR_ZH.get(code, code), rid=o['record_id'] or o['op'])

    def _cancel_op(self, entry):
        o = self.op
        if o is None or self.operations[o['op']] is not entry:
            raise ActionRejected('NOT_RUNNING')
        if o['kind'] == 'scan':
            self._scan_partial(o)
            self._finish(self.records[o['record_id']], 'cancelled')
        elif o['kind'] == 'spectroscopy':
            self._finish(self.records[o['record_id']], 'cancelled')
        else:
            self.led['approach_steps'].add(float(self._approach_steps_done(o)))
            self.holder['drive'], self.holder['stop'] = None, None           # the approach stops where the holder is (Stop button)
        self._end_op('cancelled', 'CANCELLED')
        self._event(o['kind'], 'CANCELLED', '操作被取消', rid=o['record_id'] or o['op'])

    def _complete_approach(self, o):
        x = o['extra']
        self.led['approach_steps'].add(float(self._approach_steps_done(o, x['end'])))
        self.holder['q'] = x['q_end']
        self.holder['stop'], self.holder['drive'] = None, None
        if x['outcome'] in ('found', 'overshoot', 'crash_step'):
            self.engaged = dict(t0=self.t, contact=x['outcome'] == 'crash_step')
            self._last_check_t = self.t
            self._end_op('completed')
            self._event('approach', 'APPROACH_DONE', '进针完成（Approach done），Z控制器保持设定电流', rid=o['op'])
            return
        self._end_op('failed', 'APPROACH_FAILED')
        self._event('approach', 'APPROACH_FAILED', ERROR_ZH['APPROACH_FAILED'], rid=o['op'])

    def _crash(self, where, t=None, key=None):
        tp = self.tip
        if tp is None:
            return
        if key is not None:
            if key in self._crash_keys:
                return                                                  # one crash incident per record / approach / contact episode
            self._crash_keys.add(key)
        first = tp['kind'] != 'crashed'
        if first:
            new = draw_tip(self._rng(37, self.tip_count, int(round((self.t if t is None else t) * 1e3))), 'crashed')
            new['offset'], new['recess'] = tp['offset'], tp['recess']
            self.tip = new
            self._log_tip()
        self.led['tip_crashes'].add(1.0)
        self._event('fault', 'TIP_CRASH', f'针尖撞到样品（{where}；评测者可见）', t=t)

    def _zc_at(self, log, t):
        cur = log[0][1]
        for tt, zc in log:
            if tt <= t + 1e-12:
                cur = zc
        return cur

    def _block_rng(self, seq, j, direction):
        return _rng(self._seed(), 503, seq, j, 1 if direction == 'backward' else 0)

    def _burst(self, t):
        on = False
        for tt, f, a in self.fault_log:
            if f == 'vibration_burst' and tt <= t:
                on = a
        return on

    def _run_block(self, o, j0, j1):
        """Lines j0..j1-1 of an image, forward and backward, with the Z-controller settings and the tip state in effect at the block start: the
        surface on a grid with margins, dilated by the paraboloid tip end, plus the atomic corrugation at the pixel centres, then the feedback."""
        job, x = o['extra']['job'], o['extra']
        rec = self.records[o['record_id']]
        n, S, tpl = job['n'], job['size'], job['tpl']
        px = S / n
        nl = j1 - j0
        t_blk = job['t0'] + 2.0 * j0 * tpl
        zc = self._zc_at(x['zc_log'], t_blk)
        tip = self._zc_at(x['tip_log'], t_blk)
        seq = int(o['record_id'][3:])
        th = self.holder['angle']
        p_axis = _rot(tip['offset'], -th)
        off_s = _rot(job['offset'], -th)
        surf, mid, top = self._site((p_axis[0] + off_s[0], p_axis[1] + off_s[1]))
        mat = material(mid)
        d = self._dset(zc=zc, mid=mid)
        two_k = d[1] if d else 2 * KAPPA_1EV * math.sqrt(barrier_ev(mat, self.plant['humidity']))
        d_op = d[0] if d else D_CONTACT_M
        d_rf = reference_gap(mat, self.plant['humidity'])
        att_l = corrugation_attenuation(mat, tip['apex_r'], d_op, d_rf, mat['a_s'] or 1e-9)
        att_c = corrugation_attenuation(mat, tip['apex_r'], d_op, d_rf, mat['cdw']['period']) if mat['cdw'] else 0.0
        if tip['pickup']:
            att_l, att_c = 0.3 * att_l, 0.5 * att_c
        s_rec = self.samples.get(self.holder['sample'])
        contam = self._contamination(s_rec, t_blk) if s_rec is not None and surf is s_rec['surface'] else 0.0
        Rg = tip['geo_r']
        m = min(DILATION_MAX_PX, int(math.ceil(2.0 * math.sqrt(2 * Rg * surf.characteristic_height()) / px)))     # margin with a factor 2 of safety
        u = ((np.arange(-m, n + m) + 0.5) / n - 0.5) * S
        v = ((np.arange(j0 - m, j1 + m) + 0.5) / n - 0.5) * S
        U, V = np.meshgrid(u, v)
        c, sn = math.cos(job['rot']), math.sin(job['rot'])
        xc, yc = job['offset'][0] + c * U - sn * V, job['offset'][1] + sn * U + c * V
        xc, yc = np.round(xc / XY_DAC_M) * XY_DAC_M, np.round(yc / XY_DAC_M) * XY_DAC_M       # commanded scanner positions on the 16-bit grid
        fast_s = _rot((c, sn), -th)
        ca, sa = math.cos(-th), math.sin(-th)
        omega = 2 * math.pi * FB_HZ_PER_GAIN * (zc['i_gain'] + P_WEIGHT * zc['p_gain'])
        dt = tpl / n
        i_set = zc['setpoint_a']
        e_lo, e_hi = -math.log(I_SAT_A / i_set) / two_k, math.log(i_set / I_FLOOR_A) / two_k
        osc = max(0.0, (zc['i_gain'] + P_WEIGHT * zc['p_gain']) / CRITICAL_GAIN - 1.0)
        vib0 = VIBRATION_M[self.plant['floor_vibration']] * (COVER_OFF_VIBRATION if self.cover_lifted else 1.0)
        rho = math.exp(-dt / VIB_TAU_S)
        ZR = self._zr()
        stats = dict(n=0, zsat=0, lost=0, isat=0, crash_gap=None, truncated=False)
        rows = np.arange(j0 - m, j1 + m)

        def surface_grid(PX, PY, shift):
            """Dilated apparent height at the interior pixels for the apex displaced by shift (sample frame): the tip end (paraboloid of radius
            Rg) is applied where the block is not planar (height range beyond its fitted plane above 0.05 nm; a plane is not changed by it)."""
            PXi, PYi = PX[m:m + nl, m:m + n] + shift[0], PY[m:m + nl, m:m + n] + shift[1]
            h_in, ang_in = surf.evaluate(PXi, PYi)
            h_in = h_in + surf.contamination(PXi, PYi, contam)
            res = h_in - _plane_fit(h_in, np.ones(h_in.shape, bool)) if min(h_in.shape) >= 2 else h_in - h_in.mean()
            ptp = float(np.max(res) - np.min(res))
            m_need = int(math.ceil(math.sqrt(2 * Rg * ptp) / px)) if ptp > 0.05e-9 else 0
            if m_need > m:
                stats['truncated'] = True
            ml = min(m, m_need)
            if ml <= 0:
                hd = h_in
            else:
                h_ext, _ = surf.evaluate(PX[m - ml: m + nl + ml, m - ml: m + n + ml] + shift[0], PY[m - ml: m + nl + ml, m - ml: m + n + ml] + shift[1])
                h_ext = h_ext + surf.contamination(PX[m - ml: m + nl + ml, m - ml: m + n + ml] + shift[0], PY[m - ml: m + nl + ml, m - ml: m + n + ml] + shift[1], contam)
                hd = dilate_paraboloid(h_ext, px, px, Rg, ml, ml)
            return hd + surf.pattern(PXi, PYi, ang_in, fast_s, px, (att_l, att_c))

        for direction in DIRECTIONS:
            rev = direction == 'backward'
            tl = job['t0'] + (2.0 * rows + (1.0 if rev else 0.0) + 0.5) * tpl
            dr = [self._drift(t) for t in tl]
            cr = np.zeros((len(rows), 2))
            if self.creep is not None:
                f = CREEP_FRACTION * np.exp(-np.maximum(tl - self.creep['t'], 0.0) / CREEP_TAU_S)
                cr = np.stack([-f * self.creep['dx'], -f * self.creep['dy']], axis=1)
            hy = (1.0 if rev else -1.0) * 0.5 * HYSTERESIS_FRACTION * S
            dx = (1 + self.cal['x']) * xc + hy * c + cr[:, :1]
            dy = (1 + self.cal['y']) * yc + hy * sn + cr[:, 1:]
            PX = p_axis[0] + ca * dx - sa * dy + np.array([q[0] for q in dr])[:, None]
            PY = p_axis[1] + sa * dx + ca * dy + np.array([q[1] for q in dr])[:, None]
            slope = job['slope'][0] * xc + job['slope'][1] * yc
            if rev:
                PX, PY, slope = PX[:, ::-1], PY[:, ::-1], slope[:, ::-1]
            h = surface_grid(PX, PY, (0.0, 0.0))
            if tip['second'] is not None:
                sd = _rot((tip['second']['dx'], tip['second']['dy']), -th)
                h2 = surface_grid(PX, PY, sd) - tip['second']['recess']
                h = h + np.log1p(np.exp(np.clip(two_k * (h2 - h), -50, 50))) / two_k
            rngs = [self._block_rng(seq, j, direction) for j in range(j0, j1)]
            if tip['telegraph'] is not None or tip['pickup']:
                amp = tip['telegraph']['amp'] if tip['telegraph'] else 0.08e-9
                h = h + np.stack([telegraph(r, n, dt, amp, TELEGRAPH_RATE_HZ) for r in rngs])
            tli = tl[m:m + nl]
            base = np.array([self.holder['q'] - top - q[2] + tip['recess'] for q in dr[m:m + nl]])[:, None]
            sig = [vib0 * (VIBRATION_BURST_FACTOR if self._burst(t) else 1.0) for t in tli]
            geo = base - h - slope[m:m + nl, m:m + n] + _ar1_lines(rngs, n, rho, sig)       # gap at zero Z extension (with the scan-plane slope)
            y = geo - d[0] if d else np.full(geo.shape, ZR + 1e-6)
            z, e = servo(y, dt, omega, e_lo, e_hi, ZR)
            if osc > 0:
                ph = np.array([r.random() * 2 * math.pi for r in rngs])
                wob = min(1e-9, OSC_M * osc) * np.sin(2 * math.pi * FB_HZ_PER_GAIN * CRITICAL_GAIN * 1.5 * np.arange(n) * dt + ph[:, None])
                z = np.clip(z + wob, 0.0, ZR)
            gap = geo - z
            gmin = float(np.min(gap))
            if gmin < D_CONTACT_M:
                stats['crash_gap'] = gmin if stats['crash_gap'] is None else min(stats['crash_gap'], gmin)
            if d:
                cur = i_set * np.exp(-two_k * (np.maximum(gap, D_CONTACT_M) - d[0]))
            else:
                cur = np.abs(tunnel_current(zc['tip_voltage_v'], np.maximum(gap, D_CONTACT_M), mat, barrier_ev(mat, self.plant['humidity']), KB * self.plant['room_k'] / E_CH))
            cur = np.minimum(cur, I_SAT_A)
            stats['n'] += cur.size
            stats['zsat'] += int(np.sum((z <= 1e-15) | (z >= ZR - 1e-15)))
            stats['lost'] += int(np.sum(cur < I_FLOOR_A))
            stats['isat'] += int(np.sum(cur >= I_SAT_A * (1 - 1e-9)))
            n1, n2 = np.stack([r.standard_normal(n) for r in rngs]), np.stack([r.standard_normal(n) for r in rngs])
            sign = 1.0 if zc['tip_voltage_v'] >= 0 else -1.0
            meas = np.clip(sign * (cur * (1 + I_REL_NOISE * n1) + I_NOISE_A * n2), -I_SAT_A, I_SAT_A)
            meas = np.round(meas / I_ADC_A) * I_ADC_A
            topo = np.round((Z_RANGE_M / 2 - z / (1 + self.cal['z'])) / Z_DAC_M) * Z_DAC_M          # nominal Z output (real / (1 + cal_z)), 16-bit
            for name, arr in (('topography', topo), ('current', meas)):
                rec['data'][name][direction][j0:j1] = (arr[:, ::-1] if rev else arr).astype(np.float32)
        rec['meta']['lines_completed'] = j1
        self.led['scan_lines'].add(2.0 * nl)
        n_tot = max(1, stats['n'])
        if stats['zsat'] / n_tot > 0.01:
            self._warn(rec, 'z_range_exceeded')
        if stats['lost'] / n_tot > 0.01:
            self._warn(rec, 'lost_contact')
        if stats['isat'] / n_tot > 0.001:
            self._warn(rec, 'current_saturated')
        if osc > 0:
            self._warn(rec, 'feedback_oscillation')
        if tip['second'] is not None:
            self._warn(rec, 'double_tip')
        if stats['truncated']:
            self._warn(rec, 'tip_footprint_truncated')
        t_end = job['t0'] + 2.0 * j1 * tpl
        if stats['crash_gap'] is not None and self.tip is not None:
            self.tip['recess'] += D_CONTACT_M - stats['crash_gap']
            self._crash('成像中针尖压进表面', t=t_end, key=o['record_id'])
            self._warn(rec, 'tip_crash')
        r = self._rng(701, seq, j0)
        c_now = self._contamination(s_rec, t_end) if s_rec is not None else 0.0
        if self.tip is not None:
            if not self.tip['pickup'] and r.random() < 1.0 - math.exp(-PICKUP_PER_LINE * 2 * nl * (0.1 + c_now)):
                self.tip['pickup'] = True
                self._event('fault', 'TIP_PICKUP', '针尖拾取了表面材料（评测者可见）', t=t_end)
                self._warn(rec, 'tip_pickup')
            elif self.tip['pickup'] and r.random() < 1.0 - math.exp(-DROP_PER_LINE * 2 * nl):
                self.tip['pickup'] = False
                self._event('fault', 'TIP_DROP', '针尖又丢掉了拾取的材料（评测者可见）', t=t_end)
            x['tip_log'].append((t_end, copy.deepcopy(self.tip)))

    @staticmethod
    def _warn(rec, w):
        if w not in rec['warnings']:
            rec['warnings'].append(w)

    def _scan_partial(self, o):
        job = o['extra']['job']
        done = min(job['n'], int(math.floor((self.t - job['t0']) / (2.0 * job['tpl']) + 1e-9)))
        j0 = o['extra']['blocks_done'] * BLOCK_LINES
        if done > j0:
            self._run_block(o, j0, done)

    def _spec_curve(self, o, idx):
        """One spectroscopy curve (forward and backward) at its position with the settings and tip at its start."""
        rec = self.records[o['record_id']]
        meta = rec['meta']
        kind = meta['kind']
        t_start = o['t0'] + idx * o['extra']['per']
        tip = self._zc_at(o['extra']['tip_log'], t_start)
        zc = meta['z_controller']
        pos = meta['positions_m'][idx // meta['repetitions']]
        th = self.holder['angle']
        pq = [round(p / XY_DAC_M) * XY_DAC_M for p in pos]                                  # commanded position on the 16-bit XY grid
        pa = _rot((tip['offset'][0] + (1 + self.cal['x']) * pq[0], tip['offset'][1] + (1 + self.cal['y']) * pq[1]), -th)
        dr0 = self._drift(t_start)
        surf, mid, top = self._site((pa[0] + dr0[0], pa[1] + dr0[1]))
        mat = material(mid)
        phi = barrier_ev(mat, self.plant['humidity'])
        kT = KB * self.plant['room_k'] / E_CH
        d = self._dset(zc=zc, mid=mid)
        npts = meta['data_points']
        r = _rng(self._seed(), 801, int(o['record_id'][3:]), idx)
        t_mod0 = t_start + SPEC_MOVE_S
        ts = t_mod0 + np.concatenate([np.linspace(0, meta['mod_time_s'], npts), meta['mod_time_s'] + np.linspace(0, meta['mod_time_s'], npts)])
        dz = np.array([self._drift(t)[2] for t in ts]) - self._drift(t_mod0)[2]
        vib = VIBRATION_M[self.plant['floor_vibration']] * (COVER_OFF_VIBRATION if self.cover_lifted else 1.0) * (VIBRATION_BURST_FACTOR if self._burst(t_mod0) else 1.0)
        noise_gap = _ar1_lines([r], 2 * npts, math.exp(-(meta['mod_time_s'] / npts) / VIB_TAU_S), [vib])[0]      # correlated gap vibration
        tele = telegraph(r, 2 * npts, meta['mod_time_s'] / npts, tip['telegraph']['amp'], TELEGRAPH_RATE_HZ) if tip['telegraph'] else np.zeros(2 * npts)
        crash_gap = None
        z_rec = None
        if kind == 'current_voltage':
            v_f = meta['start_v'] + meta['range_v'] * np.linspace(0, 1, npts)
            xs = np.concatenate([v_f, v_f[::-1]])
            gap0 = d[0] if d else D_CONTACT_M
            if meta['keep_z_controller']:
                gaps = []
                for vv in xs:
                    dv = self._dset(zc=dict(zc, tip_voltage_v=float(vv)), mid=mid)
                    gaps.append(dv[0] if dv else D_CONTACT_M - 1e-12)          # no current at this voltage: the feedback drives the tip into the surface
                gaps = np.array(gaps)
                cross = np.nonzero(np.sign(xs[:-1]) * np.sign(xs[1:]) <= 0)[0]
                if len(cross):                                                 # the sweep passes 0 V: no current there, the feedback drives the tip in
                    gaps[cross[0] + 1:] = np.minimum(gaps[cross[0] + 1:], D_CONTACT_M - 1e-12)
                z_rec = gap0 - gaps
            else:
                gaps = np.full(2 * npts, gap0)
            gaps = gaps - dz - tele + noise_gap
            cur = np.array([float(tunnel_current(float(vv), np.array([max(g, D_CONTACT_M)]), mat, phi, kT)[0]) for vv, g in zip(xs, gaps)])
            if tip['second'] is not None:
                cur = cur + np.array([float(tunnel_current(float(vv), np.array([max(g, D_CONTACT_M) + tip['second']['recess']]), mat, phi, kT)[0])
                                      for vv, g in zip(xs, gaps)])
        else:
            z_f = meta['start_m'] + meta['range_m'] * np.linspace(0, 1, npts)
            xs = np.concatenate([z_f, z_f[::-1]])
            gaps = (d[0] if d else D_CONTACT_M) - xs * (1 + self.cal['z']) - dz - tele + noise_gap        # the commanded (nominal) Z offsets move the tip (1 + cal_z) x
            cur = tunnel_current(zc['tip_voltage_v'], np.maximum(gaps, D_CONTACT_M), mat, phi, kT)
            if tip['second'] is not None:
                cur = cur + tunnel_current(zc['tip_voltage_v'], np.maximum(gaps, D_CONTACT_M) + tip['second']['recess'], mat, phi, kT)
        if float(np.min(gaps)) < D_CONTACT_M:
            crash_gap = float(np.min(gaps))
        meas = np.clip(np.clip(cur, -I_SAT_A, I_SAT_A) * (1 + I_REL_NOISE * r.standard_normal(2 * npts)) + I_NOISE_A * r.standard_normal(2 * npts), -I_SAT_A, I_SAT_A)
        meas = np.round(meas / I_ADC_A) * I_ADC_A
        curve = dict(position_m=list(pos), repetition=idx % meta['repetitions'], x_forward=xs[:npts].tolist(), x_backward=xs[npts:].tolist(),
                     current_forward_a=meas[:npts].tolist(), current_backward_a=meas[npts:].tolist(), x_unit='V' if kind == 'current_voltage' else 'm')
        if z_rec is not None:
            curve.update(z_forward_m=(z_rec[:npts] / (1 + self.cal['z'])).tolist(), z_backward_m=(z_rec[npts:] / (1 + self.cal['z'])).tolist())   # nominal Z output
        rec['data']['curves'].append(curve)
        meta['curves_completed'] = len(rec['data']['curves'])
        self.led['spectroscopy_curves'].add(1.0)
        if np.max(np.abs(meas)) >= I_SAT_A * (1 - 1e-9):
            self._warn(rec, 'current_saturated')
        if crash_gap is not None and self.tip is not None:
            self.tip['recess'] += D_CONTACT_M - crash_gap
            self._crash('谱测量中针尖压进表面', t=t_start + o['extra']['per'], key=o['record_id'])
            self._warn(rec, 'tip_crash')
            o['extra']['tip_log'].append((t_start + o['extra']['per'], copy.deepcopy(self.tip)))

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
        n = rec['meta']['lines_completed']
        if n < 2:
            raise ActionRejected('RECORD_NOT_SUITABLE')
        return rec, np.asarray(rec['data'][channel][direction], float)[:n]

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
            raise ActionRejected('RECORDS_FULL')
        st = roughness_stats(z) if channel == 'topography' else None
        meta = dict(source_record_id=record_id, channel=channel, direction=direction, method=method, unit=CHANNEL_UNITS[channel],
                    pixel_size_m=rec['meta'].get('pixel_size_m'), points=[z.shape[1], z.shape[0]], stats=st)
        rid = self._new_record('PR', 'pr', 'processed', meta, dict(map=z.astype(np.float32)))
        pr = self.records[rid]
        pr['template_source'] = copy.deepcopy(rec['template_source'])
        self._finish(pr, 'completed')
        return dict(record_id=rid, source_record_id=record_id, stats=st)

    def _cmd_analysis_roughness(self, record_id, channel, direction, flatten, region_px):
        _, img = self._image(record_id, channel, direction)
        st = roughness_stats(self._region(flatten_image(img, flatten), region_px))
        if st is None:
            raise ActionRejected('REGION_OUT_OF_RANGE')
        return st

    def _cmd_analysis_line_profile(self, record_id, channel, direction, flatten, start_px, end_px, points):
        rec, img = self._image(record_id, channel, direction)
        for p in (start_px, end_px):
            if not (0 <= p[0] <= img.shape[1] - 1 and 0 <= p[1] <= img.shape[0] - 1):
                raise ActionRejected('REGION_OUT_OF_RANGE')
        vals = line_profile(flatten_image(img, flatten), start_px, end_px, points)
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

    def _cmd_analysis_lattice(self, record_id, channel, direction, region_px):
        rec, img = self._image(record_id, channel, direction)
        z = self._region(img, region_px)
        if min(z.shape) < 16:
            raise ActionRejected('REGION_OUT_OF_RANGE')
        px, py = rec['meta']['pixel_size_m']
        peaks, hexa = lattice_fft(z, px, py)
        if not peaks:
            raise ActionRejected('NO_LATTICE_FOUND')
        return dict(peaks=peaks, hexagonal_lattice_m=hexa)

    def _curve(self, record_id, curve_index, kind):
        rec = self._record(record_id)
        if rec['kind'] != 'spectroscopy' or rec['status'] == 'running' or rec['meta']['kind'] != kind:
            raise ActionRejected('RECORD_NOT_SUITABLE')
        if curve_index >= len(rec['data']['curves']):
            raise ActionRejected('UNKNOWN_CURVE')
        return rec, rec['data']['curves'][curve_index]

    def _cmd_analysis_iv(self, record_id, curve_index, branch, threshold_a):
        _, c = self._curve(record_id, curve_index, 'current_voltage')
        return iv_analysis(c['x_' + branch], c['current_' + branch + '_a'], threshold_a)

    def _cmd_analysis_iz(self, record_id, curve_index, branch, i_min_a, i_max_a):
        _, c = self._curve(record_id, curve_index, 'current_distance')
        r = iz_analysis(c['x_' + branch], c['current_' + branch + '_a'], i_min_a, i_max_a)
        if r is None:
            raise ActionRejected('FIT_FAILED')
        return r

    def _out(self, rec):
        return {k: copy.deepcopy(v) for k, v in rec.items() if k in ('record_id', 'kind', 'status', 'started_s', 'finished_s', 'meta', 'warnings', 'template_source')}

    def _cmd_measurement_result(self, record_id, channel, direction, region_px, max_values):
        rec = self._record(record_id)
        head = self._out(rec)
        head['meta'] = json.loads(json.dumps(head['meta'], default=float))
        if rec['kind'] in ('image', 'processed'):
            ch = channel or ('topography' if rec['kind'] == 'image' else rec['meta']['channel'])
            d = direction or ('forward' if rec['kind'] == 'image' else rec['meta']['direction'])
            if rec['kind'] == 'image':
                if ch not in rec['data'] or d not in rec['data'][ch]:
                    raise ActionRejected('CHANNEL_NOT_RECORDED')
                img = np.asarray(rec['data'][ch][d], float)[:rec['meta']['lines_completed']]
            else:
                if ch != rec['meta']['channel'] or d != rec['meta']['direction']:
                    raise ActionRejected('CHANNEL_NOT_RECORDED')
                img = np.asarray(rec['data']['map'], float)
            if img.shape[0] == 0:
                return dict(record=head, data=dict(channel=ch, direction=d, unit=CHANNEL_UNITS[ch], shape=[0, img.shape[1]], values=[], factor=1, pixel_size_m=None, stats=None))
            img = self._region(img, region_px)
            small, f = downsample(img, max_values)
            ps = rec['meta'].get('pixel_size_m') or [None, None]
            fin = small[np.isfinite(small)]
            stats = dict(min=float(fin.min()), max=float(fin.max()), mean=float(fin.mean()), rms=float(fin.std())) if fin.size else None
            return dict(record=head, data=dict(channel=ch, direction=d, unit=CHANNEL_UNITS[ch], shape=list(small.shape), factor=f,
                                               values=[[None if not math.isfinite(v) else float(v) for v in row] for row in small],
                                               pixel_size_m=[ps[0] * f, ps[1] * f] if ps[0] else None, stats=stats))
        curves = copy.deepcopy(rec['data']['curves'])
        total = sum(sum(len(v) for k, v in c.items() if isinstance(v, list) and k != 'position_m') for c in curves) or 1
        f = max(1, math.ceil(total / max_values))
        if f > 1:
            for c in curves:
                for k in list(c):
                    if isinstance(c[k], list) and k != 'position_m':
                        c[k] = c[k][::f]
        return dict(record=head, data=dict(curves=curves, factor=f, units=dict(x='V' if rec['meta']['kind'] == 'current_voltage' else 'm', current='A', z='m')))

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
        return dict(libraries=library_sha256(), substances=len(tunnel_lib()['subs']), sample_templates=len(sample_templates()['templates']))

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        if fault in INSTANT_FAULTS:
            if active:
                if fault == 'tip_pickup' and self.tip is not None and self.tip_installed:
                    self.tip['pickup'] = True
                    self._log_tip()
                elif fault == 'thermal_disturbance':
                    self._kick('thermal_disturbance')
                self._event('fault', 'FAULT_' + fault.upper(), f'{fault} 注入')
            return dict(faults=sorted(self.faults))
        before = fault in self.faults
        if active:
            self.faults.add(fault)
        else:
            self.faults.discard(fault)
        if before != active:
            self.fault_log.append((self.t, fault, active))
            if fault == 'approach_motor_dirty' and self.op is not None and self.op['kind'] == 'approach':
                self._replan_approach()                                       # a running move takes the new speed at once (_advance)
            self._event('fault', 'FAULT_' + ('ON' if active else 'OFF'), f'{fault} {"注入" if active else "清除"}')
        return dict(faults=sorted(self.faults))

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
            if guard > 5_000_000:
                raise RuntimeError('step did not converge')
        self.t = t_end if abs(self.t - t_end) < 1e-9 else self.t
        self._handle_events()
        self.revision += 1

    def _next_event_dt(self):
        c = [math.inf]
        t = self.t
        if self.power and self.state == 'booting':
            c.append(self.state_t0 + BOOT_S - t)
        if self.tip_work:
            c.append(self.tip_work['until'] - t)
        h = self.holder
        st = h['stop']
        dv = h['drive']
        if dv is not None and h['location'] == 'head':
            if dv['until'] is not None:
                c.append(dv['until'] - t)
            v = self._motor_speed(dv)
            if v > 0:
                lo, hi = self._drive_limits(dv)
                dist = (hi - h['q']) if dv['dir'] > 0 else (h['q'] - lo)
                if dist > 1e-15:
                    c.append(dist / v)                                        # travel end / stall reached
                if st is not None and not st.get('crashed') and dv['dir'] < 0 and h['q'] > st['q_crash']:
                    c.append((h['q'] - st['q_crash']) / v)
        o = self.op
        if o is not None:
            c.append(o['t0'] + o['duration_s'] - t)
            if o['kind'] == 'approach':
                x = o['extra']
                if x['drive_end'] > t + 1e-12:
                    c.append(x['drive_end'] - t)
                if st is not None and not st.get('crashed') and x['step_m'] > 0:
                    tc = x['t_plan'] + math.ceil((x['q_start'] - st['q_crash']) / x['step_m'] - 1e-9) * CYCLE_S     # the cycle whose step reaches it
                    if tc > t + 1e-12:
                        c.append(tc - t)
            if o['kind'] == 'scan':
                job = o['extra']['job']
                c.append(job['t0'] + 2.0 * job['tpl'] * min(job['n'], (o['extra']['blocks_done'] + 1) * BLOCK_LINES) - t)
            elif o['kind'] == 'spectroscopy':
                c.append(o['t0'] + (o['extra']['done'] + 1) * o['extra']['per'] - t)
        if self.engaged is not None and (o is None or o['kind'] not in ('scan', 'spectroscopy')):
            c.append(IDLE_CHECK_S)
        return max(0.0, min(c))

    def _advance(self, h):
        if h <= 0:
            return
        if self.power:
            self.led['electrical_energy_j'].add(POWER_W * h)
        hd = self.holder
        o = self.op
        moved = False
        if hd['location'] == 'head' and o is not None and o['kind'] == 'approach':
            q_new = self._approach_q(o['extra'], self.t + h)
            self.led['motor_travel_m'].add(abs(q_new - hd['q']))
            hd['q'], moved = q_new, True
        elif hd['location'] == 'head' and hd['drive'] is not None:
            dv = hd['drive']
            h_eff = h if dv['until'] is None else max(0.0, min(h, dv['until'] - self.t))
            lo, hi = self._drive_limits(dv)
            q_new = min(hi, max(lo, hd['q'] + dv['dir'] * self._motor_speed(dv) * h_eff))
            self.led['motor_travel_m'].add(abs(q_new - hd['q']))
            hd['q'], moved = q_new, True
        if moved:
            st = hd['stop']
            if st is not None and hd['q'] < st['q_bend'] and self.tip is not None:
                self.tip['recess'] = max(self.tip['recess'], st['recess0'] + st['q_bend'] - hd['q'])      # the wire bends back with the holder
        self.t += h

    def _handle_events(self):
        t = self.t
        eps = 1e-9
        if self.power and self.state == 'booting' and t >= self.state_t0 + BOOT_S - eps:
            self.state = 'ready'
            self._event('power', 'READY', 'Naio软件与控制器就绪')
        if self.tip_work and t >= self.tip_work['until'] - eps:
            w = self.tip_work
            self.tip_work = None
            if w['action'] == 'prepare':
                self.tip, self.tip_installed = w['tip'], True
                self.tip_count = self.seq['tip']
                self.led['tips_prepared'].add(1.0)
                self._kick('tip')
                self._event('tip', 'TIP_INSTALLED', f'新针尖{self.tip_count}已装在针尖压片下')
            else:
                self.tip = None
                self._event('tip', 'TIP_REMOVED', '旧针尖已取出')
        hd = self.holder
        st = hd['stop']
        if st is not None and not st.get('crashed') and hd['location'] == 'head' and hd['q'] <= st['q_crash'] + 1e-15:
            st['crashed'] = True
            dv = hd['drive']
            v = self._motor_speed(dv) if dv is not None else 0.0
            t_c = t - (st['q_crash'] - hd['q']) / v if v > 0 else t
            self._crash('进针电机把样品推到针尖上' if st['kind'] == 'motor' else '自动进针撞针', t=t_c)
            if st['kind'] == 'motor':
                self._event('fault', 'MOTOR_STALLED', '样品顶住针尖，进针电机打滑停住（评测者可见）', t=t_c)
        dv = hd['drive']
        if dv is not None:
            lo, hi = self._drive_limits(dv)
            if (dv['until'] is not None and t >= dv['until'] - eps) or (dv['until'] is None and (hd['q'] >= hi - 1e-15 if dv['dir'] > 0 else hd['q'] <= lo + 1e-15)) \
                    or hd['location'] != 'head':
                hd['drive'] = None                                            # button released / end of travel reached
        if st is not None and not self._holder_moving() and (self.op is None or self.op['kind'] != 'approach'):
            hd['stop'] = None
        o = self.op
        if o is not None:
            if o['kind'] == 'scan':
                job = o['extra']['job']
                while True:
                    b = o['extra']['blocks_done']
                    j0 = b * BLOCK_LINES
                    if j0 >= job['n']:
                        break
                    j1 = min(job['n'], j0 + BLOCK_LINES)
                    if t < job['t0'] + 2.0 * job['tpl'] * j1 - eps:
                        break
                    self._run_block(o, j0, j1)
                    o['extra']['blocks_done'] = b + 1
                if o['extra']['blocks_done'] * BLOCK_LINES >= job['n']:
                    self._finish(self.records[o['record_id']], 'completed')
                    self._end_op('completed')
                    self._event('scan', 'SCAN_DONE', f'成像{o["record_id"]}完成', rid=o['record_id'])
            elif o['kind'] == 'spectroscopy':
                while o['extra']['done'] < o['extra']['n'] and t >= o['t0'] + (o['extra']['done'] + 1) * o['extra']['per'] - eps:
                    self._spec_curve(o, o['extra']['done'])
                    o['extra']['done'] += 1
                if o['extra']['done'] >= o['extra']['n']:
                    self._finish(self.records[o['record_id']], 'completed')
                    self._end_op('completed')
                    self._event('spectroscopy', 'SPECTROSCOPY_DONE', f'谱测量{o["record_id"]}完成', rid=o['record_id'])
            elif t >= o['t0'] + o['duration_s'] - eps:
                self._complete_approach(o)
        if self.engaged is not None and (self.op is None or self.op['kind'] not in ('scan', 'spectroscopy')):
            self._idle_contact_check()
        self._last_check_t = self.t

    def _idle_contact_check(self):
        """Engaged idle feedback: the tip meets the surface when the surface drifts onto the retracted tip or when the feedback, finding no
        set-point current (e.g. 0 V tip voltage), extends the Z scanner into the surface; the crossing is bisected in time, the tip is crashed
        and pressed back by the overlap; a receding surface or a reachable set point lets the contact open again."""
        if self._gap_mech() is None:
            return

        def raw(t):
            return self._z_state(t)['raw']
        r = raw(self.t)
        if r < D_CONTACT_M:
            if not self.engaged['contact']:
                lo, hi = self._last_check_t, self.t
                if lo < hi and raw(lo) >= D_CONTACT_M:
                    for _ in range(50):
                        mid = 0.5 * (lo + hi)
                        if raw(mid) < D_CONTACT_M:
                            hi = mid
                        else:
                            lo = mid
                else:
                    hi = lo                                                   # a setting changed at the last check (e.g. 0 V)
                self.engaged['contact'] = True
                self._crash('针尖压到样品上（热漂移或无设定电流时Z扫描器全伸出）', t=hi)
            if self.tip is not None:
                self.tip['recess'] += D_CONTACT_M - r                         # the tip is pressed back by the overlap
        elif self.engaged['contact'] and r > D_CONTACT_M + 0.1e-9:
            self.engaged['contact'] = False


# ---------------------------------------------------------------- sessions (identity, visibility filter, idempotent requests)
_RECORD_ARG = {'measurement.result': 'record_id', 'records.delete': 'record_id', 'analysis.flatten': 'record_id', 'analysis.roughness': 'record_id',
               'analysis.line_profile': 'record_id', 'analysis.step_height': 'record_id', 'analysis.lattice': 'record_id', 'analysis.iv': 'record_id',
               'analysis.iz': 'record_id'}
_DATA_GUARDED = set(_RECORD_ARG)
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED
                    and k not in DECLARED}
_REPLAYABLE_QUERIES = {'measurement.result', 'records.list', 'events.read', 'magnifier.view', 'provenance.read', 'analysis.roughness', 'analysis.line_profile',
                       'analysis.step_height', 'analysis.lattice', 'analysis.iv', 'analysis.iz'}
_TRUTH_WARNINGS = {'tip_crash', 'tip_pickup', 'double_tip', 'tip_footprint_truncated'}      # evaluator knowledge: the instrument cannot tell


def _response_bytes(result):
    try:
        return len(json.dumps(result, default=float))
    except (TypeError, ValueError):
        return 0


class NanosurfNaiostmSession:
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
        meta = copy.deepcopy(export_nanosurf_naiostm_contract_metadata())
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
                rec['template_source'] = dict(library=rec['template_source']['library'], sample_library=rec['template_source']['sample_library'], template_id=None,
                                              substance=None)
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
    'NOT_IMPLEMENTED': '仪器有该功能，本资产未实现', 'ACCESS_DENIED': '无权访问（评测槽、另一个agent的记录或操作）', 'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作不在进行中',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_ON': '通电或有操作时不能重新声明实验室条件', 'POWER_OFF': '控制器未通电（或操作中断电）', 'NOT_READY': 'Naio软件初始化中',
    'MEASUREMENT_RUNNING': '有自动进针、成像或谱测量在进行', 'ENGAGED': '已进针（Z控制器保持隧穿；先Withdraw或Retract）', 'NOT_ENGAGED': '未进针（先自动进针）',
    'MOTION_IN_PROGRESS': '进针电机在移动样品座或正在装/取针尖', 'HOLDER_NOT_IN_HEAD': '样品座不在扫描头U形槽中', 'HOLDER_IN_HEAD': '样品座在扫描头中（先取出）',
    'COVER_ON': '放大罩罩着（先提起放大罩）', 'NO_TIP': '针尖座上没有针尖（或正在装/取针尖）', 'TIP_INSTALLED': '针尖座上已有针尖（先取出旧针尖）',
    'WIRE_EXHAUSTED': 'Pt/Ir丝已用完（不足4 mm）', 'UNKNOWN_SAMPLE': '样品不存在', 'SAMPLE_NOT_ON_BENCH': '样品不在实验台上', 'HOLDER_OCCUPIED': '样品座磁性端已有样品',
    'HOLDER_EMPTY': '样品座上没有样品', 'NOT_CLEAVABLE': '该样品不能用胶带解理（手册：金样品不能清洁）', 'SAMPLE_TOO_THIN': '样品再解理就太薄（<0.05 mm）',
    'DUPLICATE_SAMPLE': '样品ID已存在', 'TOO_MANY_SAMPLES': '样品数量超过上限（12）', 'OVERRIDE_NOT_APPLICABLE': '覆盖的形貌参数不属于该模板', 'UNKNOWN_MATERIAL': '隧穿库中没有该材料',
'COLLISION_RISK': '按肉眼估计样品离针尖太近（手推、放入或取出样品座须估计≥0.2 mm，转动须≥0.3 mm；估计含本次放入的观察偏差与每次观察的抖动）',
    'SCAN_OUT_OF_RANGE': '扫描区或谱测量位置（含偏移与旋转）超出扫描器500×500 nm', 'VOLTAGE_OUT_OF_RANGE': '调制终点超出针尖电压±10 V（或范围为0）',
    'SIMULATION_LIMIT': '超出仿真上限（仿真边界，不是仪器故障；具体原因见各能力的错误说明）', 'TOO_MANY_CURVES': '一次谱测量超过128条曲线',
    'RECORDS_FULL': '记录已满：删除记录后再试', 'UNKNOWN_RECORD': '记录不存在', 'RECORD_IN_USE': '记录属于进行中的操作', 'RECORD_NOT_SUITABLE': '记录种类或状态不适用',
    'CHANNEL_NOT_RECORDED': '该记录没有这个通道或方向', 'REGION_OUT_OF_RANGE': '区域或端点超出图像', 'REGION_REQUIRED': 'regions方法需要region_a_px与region_b_px',
    'NO_STEP_FOUND': '高度直方图没有两个峰（不是两级表面）', 'NO_LATTICE_FOUND': '频谱中没有周期峰', 'UNKNOWN_CURVE': '曲线序号不存在', 'FIT_FAILED': '拟合点不足或电流不随靠近增大',
    'APPROACH_FAILED': '自动进针失败：达到最大步数仍未在Z量程内检测到设定电流（电机步长为0、样品太远、绝缘或氧化表面、针尖电压为0时发生；后三种情况针尖会撞到表面）',
    'CANCELLED': '操作被取消',
}
_S = ['operation.id', 'operation.kind', 'operation.progress', 'operation.remaining_s']
_Z = ['zscanner.extension_m', 'current.reading_a', 'probe_status.light']
_HOLDER = ['holder.location', 'holder.position_m', 'holder.target_m', 'holder.angle_rad', 'truth.gap_m', 'truth.setpoint_gap_m']
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.calibration', 'truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'],
    'sample.provide': ['sample.bench_ids', 'truth.samples'],
    'cover.lift': ['cover.lifted', 'cover.lift_m', 'zscanner.extension_m'], 'cover.place': ['cover.lifted', 'cover.lift_m', 'zscanner.extension_m'],   # vibration seen by the feedback
    'tip.remove': ['tip.installed', 'tip.handling', 'truth.tip'],
    'tip.prepare': ['tip.handling', 'tip.wire_remaining_m'],
    'sample.mount': ['holder.sample_id', 'sample.bench_ids', 'truth.samples'],
    'sample.unmount': ['holder.sample_id', 'sample.bench_ids', 'truth.samples'],
    'sample.cleave': ['truth.samples'],
    'holder.insert': list(_HOLDER),
    'holder.push': ['holder.position_m', 'holder.target_m', 'truth.gap_m', 'truth.setpoint_gap_m'],
    'holder.rotate': ['holder.angle_rad', 'truth.gap_m', 'truth.setpoint_gap_m'],
    'holder.remove': list(_HOLDER),
    'maintenance.clean_guides': ['truth.faults'],
    'power.set': ['power.enabled', 'power.led_on', 'system.state', 'approach.state', 'holder.moving', 'holder.target_m', 'scan.lines_completed', 'truth.gap_m',
                  'truth.tip'] + _Z + _S,
    'power.press_button': ['power.enabled', 'power.led_on', 'system.state', 'approach.state', 'holder.moving', 'holder.target_m', 'scan.lines_completed', 'truth.gap_m',
                           'truth.tip'] + _Z + _S,
    'z_controller.set': ['z_controller.settings', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z,
    'approach.advance': ['holder.moving', 'holder.target_m'],
    'approach.retract': ['approach.state', 'holder.moving', 'holder.target_m', 'truth.gap_m'] + _Z,
    'approach.home': ['approach.state', 'holder.moving', 'holder.target_m', 'truth.gap_m'] + _Z,
    'approach.stop': ['holder.moving', 'holder.target_m'],
    'approach.withdraw': ['approach.state', 'holder.moving', 'holder.target_m', 'truth.gap_m'] + _Z,
    'approach.start': ['approach.state', 'holder.moving', 'holder.target_m'] + _Z + _S,
    'scan.start': ['scan.lines_completed'] + _S,
    'spectroscopy.iv': list(_S), 'spectroscopy.iz': list(_S),
    'tip.cleaning_pulse': ['truth.tip'],
    'analysis.flatten': ['result.last_record_id'],
    'analysis.roughness': [], 'analysis.line_profile': [], 'analysis.step_height': [], 'analysis.lattice': [], 'analysis.iv': [], 'analysis.iz': [],
    'records.delete': ['result.last_record_id'],
    'fault.inject': ['truth.faults', 'truth.tip', 'holder.target_m', 'zscanner.extension_m'],
    'sim.step': ['clock.time_s'],
}
_OPS = _S + ['result.last_record_id', 'scan.lines_completed']
_ENG = ['approach.state', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z
_MOT = ['holder.position_m', 'holder.moving', 'holder.target_m', 'motor']
ON_STEP = {
    'plant.configure': ['truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m'] + _Z,
    'power.set': ['system.state', 'truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip', 'holder.position_m', 'holder.moving', 'holder.target_m',
                  'approach.state'] + _Z + _OPS,
    'power.press_button': ['system.state', 'truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip', 'holder.position_m', 'holder.moving', 'holder.target_m',
                           'approach.state'] + _Z + _OPS,
    'cover.lift': ['truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z + _OPS,
    'cover.place': ['truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z + _OPS,
    'tip.remove': ['tip.handling', 'truth.drift_m'],
    'tip.prepare': ['tip.handling', 'tip.installed', 'truth.tip', 'truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m'],
    'holder.insert': ['truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m'] + _Z,
    'holder.push': ['truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m'] + _Z,
    'holder.rotate': ['truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m'] + _Z,
    'holder.remove': ['truth.drift_m', 'truth.gap_m'],
    'sample.cleave': [],
    'z_controller.set': ['truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z + _OPS,
    'approach.advance': ['holder.position_m', 'holder.moving', 'holder.target_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z,
    'approach.retract': ['holder.position_m', 'holder.moving', 'holder.target_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z,
    'approach.home': ['holder.position_m', 'holder.moving', 'holder.target_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z,
    'approach.stop': ['holder.position_m', 'holder.moving', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z,
    'approach.withdraw': ['holder.position_m', 'holder.moving', 'holder.target_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip'] + _Z,
    'approach.start': ['holder.position_m', 'holder.moving', 'holder.target_m'] + _ENG + _OPS,
    'scan.start': ['truth.tip'] + _Z + _OPS,
    'spectroscopy.iv': ['truth.tip'] + _Z + _OPS, 'spectroscopy.iz': ['truth.tip'] + _Z + _OPS,
    'tip.cleaning_pulse': ['truth.tip'] + _Z + _OPS,
    'analysis.flatten': ['result.last_record_id'], 'records.delete': ['result.last_record_id'],
    'fault.inject': ['truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip', 'holder.position_m', 'holder.moving', 'holder.target_m',
                     'approach.state'] + _Z + _OPS,
    'sim.step': ['approach.state', 'current.reading_a', 'holder.moving', 'holder.position_m', 'holder.target_m', 'probe_status.light', 'result.last_record_id',
                 'scan.lines_completed', 'system.state', 'tip.handling', 'tip.installed', 'truth.drift_m', 'truth.gap_m', 'truth.setpoint_gap_m', 'truth.tip',
                 'zscanner.extension_m'] + _S,
}
for _k in list(ON_STEP):
    ON_STEP[_k] = [x for x in dict.fromkeys(ON_STEP[_k]) if x != 'motor']
ON_STEP_ZH = {
    'plant.configure': '室温波动决定之后的热漂移', 'power.set': '上电8 s后Naio软件就绪；热漂移自上电起重新积累', 'power.press_button': '同power.set',
    'cover.lift': '提起/罩上放大罩是一次热扰动（漂移随时间积累）', 'cover.place': '同cover.lift', 'tip.remove': '约60 s后旧针尖取出',
    'tip.prepare': '约60 s后新针尖装上（针尖状态为隐藏真值；装针是一次热扰动）', 'holder.insert': '手的温度带来热漂移（随时间积累）', 'holder.push': '同holder.insert',
    'holder.rotate': '同holder.insert', 'holder.remove': '同holder.insert', 'z_controller.set': '进针后反馈按新设定保持间隙；扫描中从下一块生效',
    'approach.advance': '电机以最大速度移动样品座，推到接触时撞针（样品推弯针尖）', 'approach.retract': '电机后退', 'approach.home': '电机退到最远', 'approach.stop': '样品座停止',
    'approach.withdraw': '电机后退给定步数', 'approach.start': '每20 ms一个周期推进；完成后进针或失败（可能撞针）',
    'scan.start': '按行推进（每16行一块写入正反向形貌与电流、更新针尖拾取/撞针与警告），完成后写入记录', 'spectroscopy.iv': '每条曲线结束时写入（可能撞针）',
    'spectroscopy.iz': '同spectroscopy.iv', 'tip.cleaning_pulse': '新的针尖状态影响之后的成像与读数', 'analysis.flatten': '之后完成的记录写入新的最新记录号',
    'records.delete': '同analysis.flatten', 'fault.inject': '电机变慢、振动、针尖拾取、热扰动随时间的影响',
    'sim.step': '推进初始化、装/取针尖、电机运动与撞针、自动进针周期、成像块与谱曲线、热漂移与进针状态的间隙、电能账',
}
_R = ['power.enabled', 'system.state']
_IDLE = ['operation.id', 'approach.state']
PRECONDITIONS = {
    'plant.configure': ('断电且没有操作时才可（重新）声明', ['power.enabled', 'operation.id']),
    'sample.provide': ('已声明实验室条件；样品ID未用过、样品数≤12；覆盖参数属于模板且在物理范围内', ['plant.configured', 'sample.bench_ids']),
    'cover.lift': ('已声明实验室条件', ['plant.configured']), 'cover.place': ('已声明实验室条件', ['plant.configured']),
    'tip.remove': ('放大罩已提起、样品座不在扫描头中、不在装取针尖、针尖座上有针尖', ['cover.lifted', 'holder.location', 'tip.handling', 'tip.installed']),
    'tip.prepare': ('放大罩已提起、样品座不在扫描头中、不在装取针尖、针尖座上没有针尖、丝≥4 mm', ['cover.lifted', 'holder.location', 'tip.handling', 'tip.installed',
                                                                              'tip.wire_remaining_m']),
    'sample.mount': ('样品在实验台上；样品座不在扫描头中且磁性端空着', ['sample.bench_ids', 'holder.location', 'holder.sample_id']),
    'sample.unmount': ('样品座不在扫描头中且有样品', ['holder.location', 'holder.sample_id']),
    'sample.cleave': ('样品在实验台上、可解理、解理后厚度≥0.05 mm', ['sample.bench_ids']),
    'holder.insert': ('放大罩已提起；样品座不在扫描头中；不在装取针尖；放入位置处样品表面离针尖按肉眼估计（0.1 mm分辨、本次放入的观察偏差与观察抖动）≥0.2 mm',
                      ['cover.lifted', 'holder.location', 'tip.handling']),
    'holder.push': ('放大罩已提起；样品座在扫描头中；无操作、未进针、电机不动；新位置处样品表面离针尖按肉眼估计（0.1 mm分辨、本次放入的观察偏差与观察抖动）≥0.2 mm',
                    ['cover.lifted', 'holder.location', 'holder.moving'] + _IDLE),
    'holder.rotate': ('同holder.push；转动后按肉眼估计的间隙≥0.3 mm', ['cover.lifted', 'holder.location', 'holder.moving'] + _IDLE),
    'holder.remove': ('同holder.push；按肉眼估计的间隙≥0.2 mm', ['cover.lifted', 'holder.location', 'holder.moving'] + _IDLE),
    'maintenance.clean_guides': ('放大罩已提起；样品座不在扫描头中', ['cover.lifted', 'holder.location']),
    'power.set': ('已声明实验室条件', ['plant.configured', 'power.enabled']), 'power.press_button': ('同power.set（按一次切换通断）', ['plant.configured', 'power.enabled']),
    'z_controller.set': ('已就绪；没有自动进针或谱测量在进行（成像中可改，从下一块生效）', _R + ['operation.kind']),
    'approach.advance': ('已就绪；样品座在扫描头中；无操作、未进针；电机不在运动（先Stop）；不在装取针尖', _R + ['holder.location', 'holder.moving'] + _IDLE + ['tip.handling']),
    'approach.retract': ('已就绪；样品座在扫描头中；无操作；电机不在运动；不在装取针尖（进针状态先缩回Z）', _R + ['holder.location', 'holder.moving', 'operation.id', 'tip.handling']),
    'approach.home': ('同approach.retract', _R + ['holder.location', 'holder.moving', 'operation.id', 'tip.handling']),
    'approach.stop': ('已就绪；没有自动进针等操作在进行（进行中的自动进针用cancel停止）', _R + ['operation.id']),
    'approach.withdraw': ('同approach.retract', _R + ['holder.location', 'holder.moving', 'operation.id', 'tip.handling']),
    'approach.start': ('已就绪；样品座在扫描头中且静止；无操作、未进针；有针尖', _R + ['holder.location', 'holder.moving'] + _IDLE + ['tip.installed', 'tip.handling']),
    'scan.start': ('已进针；无操作；样品座静止；扫描区在扫描器内', _R + _IDLE + ['holder.moving', 'tip.installed']),
    'spectroscopy.iv': ('已进针；无操作；位置在扫描器内；终点电压在±10 V内；曲线≤128条', _R + _IDLE + ['holder.moving', 'tip.installed']),
    'spectroscopy.iz': ('已进针；无操作；位置在扫描器内；曲线≤128条', _R + _IDLE + ['holder.moving', 'tip.installed']),
    'tip.cleaning_pulse': ('已进针（可在成像中）；没有自动进针或谱测量', _R + ['approach.state', 'operation.kind', 'tip.installed']),
    'magnifier.view': ('已声明实验室条件', ['plant.configured']),
    'analysis.flatten': ('记录为已结束的图像（≥2行）', ['result.last_record_id']), 'analysis.roughness': ('同analysis.flatten；区域在图像内', ['result.last_record_id']),
    'analysis.line_profile': ('同analysis.flatten；端点在图像内', ['result.last_record_id']),
    'analysis.step_height': ('同analysis.flatten；histogram需两级表面，regions需两个区域', ['result.last_record_id']),
    'analysis.lattice': ('同analysis.flatten；区域至少16×16像素', ['result.last_record_id']),
    'analysis.iv': ('记录为已结束的I-V谱，曲线序号存在', ['result.last_record_id']), 'analysis.iz': ('记录为已结束的I-z谱，曲线序号存在，所选电流区间内≥5点', ['result.last_record_id']),
    'measurement.result': ('记录存在；通道与方向存在；区域在图像内', ['result.last_record_id']),
    'records.delete': ('记录存在且不属于进行中的操作；agent只能删除自己或宿主的记录', ['operation.id', 'result.last_record_id']),
    'fault.inject': ('已声明实验室条件', ['plant.configured']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE', 'DEVICE_ERROR']
_RE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY']
_ENGAGED_START = _RE + ['MEASUREMENT_RUNNING', 'HOLDER_NOT_IN_HEAD', 'NO_TIP', 'MOTION_IN_PROGRESS', 'NOT_ENGAGED']
_REC = ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'CHANNEL_NOT_RECORDED']
CAP_ERRORS = {
    'plant.configure': ['POWER_ON'],
    'sample.provide': ['PLANT_REQUIRED', 'DUPLICATE_SAMPLE', 'TOO_MANY_SAMPLES', 'OVERRIDE_NOT_APPLICABLE', 'SIMULATION_LIMIT'],
    'cover.lift': ['PLANT_REQUIRED'], 'cover.place': ['PLANT_REQUIRED'],
    'tip.remove': ['PLANT_REQUIRED', 'COVER_ON', 'HOLDER_IN_HEAD', 'MOTION_IN_PROGRESS', 'NO_TIP'],
    'tip.prepare': ['PLANT_REQUIRED', 'COVER_ON', 'HOLDER_IN_HEAD', 'MOTION_IN_PROGRESS', 'TIP_INSTALLED', 'WIRE_EXHAUSTED'],
    'sample.mount': ['PLANT_REQUIRED', 'UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'HOLDER_IN_HEAD', 'HOLDER_OCCUPIED'],
    'sample.unmount': ['PLANT_REQUIRED', 'HOLDER_IN_HEAD', 'HOLDER_EMPTY'],
    'sample.cleave': ['PLANT_REQUIRED', 'UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'NOT_CLEAVABLE', 'SAMPLE_TOO_THIN'],
    'holder.insert': ['PLANT_REQUIRED', 'COVER_ON', 'HOLDER_IN_HEAD', 'MOTION_IN_PROGRESS', 'COLLISION_RISK'],
    'holder.push': ['PLANT_REQUIRED', 'COVER_ON', 'HOLDER_NOT_IN_HEAD', 'MEASUREMENT_RUNNING', 'ENGAGED', 'MOTION_IN_PROGRESS', 'COLLISION_RISK'],
    'holder.rotate': ['PLANT_REQUIRED', 'COVER_ON', 'HOLDER_NOT_IN_HEAD', 'MEASUREMENT_RUNNING', 'ENGAGED', 'MOTION_IN_PROGRESS', 'COLLISION_RISK'],
    'holder.remove': ['PLANT_REQUIRED', 'COVER_ON', 'HOLDER_NOT_IN_HEAD', 'MEASUREMENT_RUNNING', 'ENGAGED', 'MOTION_IN_PROGRESS', 'COLLISION_RISK'],
    'maintenance.clean_guides': ['PLANT_REQUIRED', 'COVER_ON', 'HOLDER_IN_HEAD'],
    'power.set': ['PLANT_REQUIRED'], 'power.press_button': ['PLANT_REQUIRED'],
    'z_controller.set': _RE + ['MEASUREMENT_RUNNING'],
    'approach.advance': _RE + ['HOLDER_NOT_IN_HEAD', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'ENGAGED'],
    'approach.retract': _RE + ['HOLDER_NOT_IN_HEAD', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS'],
    'approach.home': _RE + ['HOLDER_NOT_IN_HEAD', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS'],
    'approach.stop': _RE + ['MEASUREMENT_RUNNING'],
    'approach.withdraw': _RE + ['HOLDER_NOT_IN_HEAD', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS'],
    'approach.start': _RE + ['HOLDER_NOT_IN_HEAD', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'ENGAGED', 'NO_TIP'] + ['APPROACH_FAILED', 'POWER_OFF', 'CANCELLED'],
    'scan.start': _ENGAGED_START + ['SCAN_OUT_OF_RANGE', 'SIMULATION_LIMIT', 'RECORDS_FULL'] + ['POWER_OFF', 'CANCELLED'],
    'spectroscopy.iv': _ENGAGED_START + ['VOLTAGE_OUT_OF_RANGE', 'SCAN_OUT_OF_RANGE', 'TOO_MANY_CURVES', 'SIMULATION_LIMIT', 'RECORDS_FULL'] + ['POWER_OFF', 'CANCELLED'],
    'spectroscopy.iz': _ENGAGED_START + ['SCAN_OUT_OF_RANGE', 'TOO_MANY_CURVES', 'SIMULATION_LIMIT', 'RECORDS_FULL'] + ['POWER_OFF', 'CANCELLED'],
    'tip.cleaning_pulse': _RE + ['HOLDER_NOT_IN_HEAD', 'NO_TIP', 'NOT_ENGAGED', 'MEASUREMENT_RUNNING'],
    'magnifier.view': ['PLANT_REQUIRED'],
    'analysis.flatten': _REC + ['RECORDS_FULL'], 'analysis.roughness': _REC + ['REGION_OUT_OF_RANGE'], 'analysis.line_profile': _REC + ['REGION_OUT_OF_RANGE'],
    'analysis.step_height': _REC + ['REGION_OUT_OF_RANGE', 'REGION_REQUIRED', 'NO_STEP_FOUND'], 'analysis.lattice': _REC + ['REGION_OUT_OF_RANGE', 'NO_LATTICE_FOUND'],
    'analysis.iv': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'UNKNOWN_CURVE'], 'analysis.iz': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'UNKNOWN_CURVE', 'FIT_FAILED'],
    'measurement.result': ['UNKNOWN_RECORD', 'CHANNEL_NOT_RECORDED', 'REGION_OUT_OF_RANGE'],
    'records.delete': ['UNKNOWN_RECORD', 'RECORD_IN_USE'], 'fault.inject': ['PLANT_REQUIRED'],
}
_PARTIAL = {'POWER_OFF', 'APPROACH_FAILED', 'CANCELLED'}
CAP_ERROR_ZH = {
    ('scan.start', 'SIMULATION_LIMIT'): '点数超过仿真上限2048（与仪器最大值相同）', ('spectroscopy.iv', 'SIMULATION_LIMIT'): '每条曲线点数超过仿真上限4096（仪器可存65535点）',
    ('spectroscopy.iz', 'SIMULATION_LIMIT'): '每条曲线点数超过仿真上限4096（仪器可存65535点）',
    ('sample.provide', 'SIMULATION_LIMIT'): '晶粒过多：每1 µm图块超过20000个晶粒（增大晶粒尺寸；仿真边界，不是仪器故障）',
    ('sample.provide', 'INVALID_ARGUMENT'): '参数不符合输入模式，或形貌参数超出物理范围（台阶平均坡度超过0.05）',
    ('scan.start', 'RECORDS_FULL'): '记录已满：记录数达到200，或新图像会使全部记录的像素内存超过2 GiB（仿真上限）；删除记录后再试',
    ('analysis.flatten', 'RECORDS_FULL'): '记录已满：记录数达到200，或处理结果会使像素内存超过2 GiB（仿真上限）',
    ('approach.start', 'POWER_OFF'): '未通电；或进针中断电（样品座停在当时位置）', ('approach.start', 'CANCELLED'): '取消：样品座停在当时位置，不进针',
    ('scan.start', 'POWER_OFF'): '未通电；或成像中断电：记录以failed结束，保留已完成的行', ('scan.start', 'CANCELLED'): '成像被取消：记录以cancelled结束，保留已完成的行，仍在进针状态',
    ('spectroscopy.iv', 'CANCELLED'): '取消：已完成的曲线保留', ('spectroscopy.iz', 'CANCELLED'): '取消：已完成的曲线保留',
    ('spectroscopy.iv', 'POWER_OFF'): '未通电；或测量中断电：已完成的曲线保留', ('spectroscopy.iz', 'POWER_OFF'): '未通电；或测量中断电：已完成的曲线保留',
}
MODEL_NONE_ZH = {
    'plant.configure': '实验室条件没有可见模型', 'sample.provide': '样品在实验台上，场景中没有样品几何', 'sample.mount': '样品钢片为小构件（磁性端为接口点sample_face）',
    'sample.unmount': '同sample.mount', 'sample.cleave': '胶带解理不改变几何', 'tip.remove': '针尖丝为小构件，装取没有关节', 'tip.prepare': '同tip.remove',
    'holder.rotate': '样品座为旋转对称圆柱，转角不镜像到场景', 'maintenance.clean_guides': '清洁没有可见变化', 'z_controller.set': '软件设置没有可见变化',
    'scan.start': '扫描器位移（nm）在场景中不可见', 'spectroscopy.iv': '电压扫描没有可见变化', 'spectroscopy.iz': 'Z调制（nm）不可见', 'tip.cleaning_pulse': '电压脉冲不可见',
    'analysis.flatten': '软件计算', 'records.delete': '只删除软件中的记录', 'fault.inject': '注入故障本身没有可见变化',
}
SCENE_MIRRORED = {'holder.position_m', 'holder.location', 'cover.lift_m', 'cover.lifted', 'power.enabled', 'power.led_on'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持或未实现：没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节（样品座沿隧穿轴的位置、放大罩抬起）、背面电源键与蓝色电源LED（' + '、'.join(seen)
                                   + '；绑定见合同model_bindings），运行层本身不驱动模型' + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {
    'approach.start': ('每20 ms一个周期（Z全缩回、电机一步、Z伸出200 nm）；在Z量程内检测到设定电流后稳定0.5 s，approach.state=engaged；达到最大步数则APPROACH_FAILED',
                       '取消或断电时样品座停在当时位置、不进针；步长超过Z量程时可能冲过设定点（红灯）或把样品推到针尖（撞针，评测者可见）；无电流（绝缘、厚氧化层、0 V）时Z伸出撞到表面'
                       '、针尖弯曲、电机顶住后打滑，直到最大步数'),
    'scan.start': ('按线周期（2×time/line）推进，每16行一块写入正反向形貌与电流，全部行完成后记录completed、写入最新记录号',
                   '取消时保留已完成的行（按取消时刻计算到当前行），仍在进针状态；断电时保留已完成的行、记录failed、Z回零；成像中针尖可能拾取材料、双针尖或撞针（评测者可见）'),
    'spectroscopy.iv': ('每条曲线（移到位置0.1 s＋正反向各mod_time）结束时写入，全部完成后记录completed', '取消或断电时已完成的曲线保留；Z控制器开着扫过0 V时针尖被驱向样品而撞针'),
    'spectroscopy.iz': ('每条曲线结束时写入，全部完成后记录completed', '取消或断电时已完成的曲线保留；Z压过点接触时撞针（评测者可见），电流饱和在100 nA'),
}
RESOURCES = {'approach.start': ['approach_motor', 'z_scanner', 'controller', 'host_clock'], 'scan.start': ['xy_scanner', 'z_scanner', 'current_amplifier', 'controller', 'host_clock'],
             'spectroscopy.iv': ['z_scanner', 'current_amplifier', 'controller', 'host_clock'], 'spectroscopy.iz': ['z_scanner', 'current_amplifier', 'controller', 'host_clock'],
             'approach.advance': ['approach_motor'], 'approach.retract': ['approach_motor'], 'approach.home': ['approach_motor'], 'approach.stop': ['approach_motor'],
             'approach.withdraw': ['approach_motor', 'z_scanner'], 'power.set': ['mains', 'controller'], 'power.press_button': ['mains', 'controller'],
             'z_controller.set': ['controller'], 'tip.cleaning_pulse': ['controller', 'tip'], 'cover.lift': ['cover'], 'cover.place': ['cover'], 'tip.remove': ['tip', 'tool_set'],
             'tip.prepare': ['tip', 'tool_set', 'pt_ir_wire'], 'sample.mount': ['bench', 'sample_holder'], 'sample.unmount': ['bench', 'sample_holder'], 'sample.cleave': ['bench'],
             'holder.insert': ['sample_holder', 'scan_head'], 'holder.push': ['sample_holder'], 'holder.rotate': ['sample_holder'], 'holder.remove': ['sample_holder'],
             'maintenance.clean_guides': ['scan_head'], 'magnifier.view': ['magnifier'], 'plant.configure': ['room'], 'sample.provide': ['bench'],
             'analysis.flatten': ['computer'], 'analysis.roughness': ['computer'], 'analysis.line_profile': ['computer'], 'analysis.step_height': ['computer'],
             'analysis.lattice': ['computer'], 'analysis.iv': ['computer'], 'analysis.iz': ['computer'], 'records.delete': ['computer'], 'fault.inject': ['evaluator'],
             'sim.step': ['host_clock']}
OBSERVES = {
    'power.set': ['power.enabled', 'power.led_on', 'system.state'], 'power.press_button': ['power.enabled', 'power.led_on', 'system.state'],
    'cover.lift': ['cover.lifted'], 'cover.place': ['cover.lifted'], 'tip.remove': ['tip.installed', 'tip.handling'], 'tip.prepare': ['tip.installed', 'tip.handling'],
    'sample.mount': ['holder.sample_id'], 'sample.unmount': ['sample.bench_ids'], 'holder.insert': ['holder.location', 'holder.angle_rad'],
    'holder.push': ['holder.location', 'holder.moving'], 'holder.rotate': ['holder.angle_rad'], 'holder.remove': ['holder.location'],
    'z_controller.set': ['z_controller.settings', 'zscanner.extension_m'], 'approach.advance': ['holder.moving'],
    'approach.retract': ['holder.moving', 'approach.state'], 'approach.home': ['holder.moving', 'approach.state'], 'approach.stop': ['holder.moving'],
    'approach.withdraw': ['holder.moving', 'approach.state'], 'approach.start': ['approach.state', 'probe_status.light', 'operation.id', 'zscanner.extension_m'],
    'scan.start': ['operation.id', 'operation.progress', 'scan.lines_completed', 'result.last_record_id'],
    'spectroscopy.iv': ['operation.id', 'operation.progress', 'result.last_record_id'], 'spectroscopy.iz': ['operation.id', 'operation.progress', 'result.last_record_id'],
    'tip.cleaning_pulse': ['current.reading_a'], 'analysis.flatten': ['result.last_record_id'], 'records.delete': ['result.last_record_id'],
}
_WB = 'user_workbook'
_QD = 'manufacturer_primary'
_MAN = 'manufacturer_manual'
_NS = 'manufacturer_web'
_AZ = 'catalogue_azonano'
_PHYS = 'physics_literature'
_LIB = 'template_libraries'
_SIM = 'simulation_assumption'
CAP_SOURCES = {
    'plant.configure': [_MAN, _SIM], 'sample.provide': [_LIB, _MAN, _SIM], 'cover.lift': [_MAN, _SIM], 'cover.place': [_MAN, _SIM], 'tip.remove': [_MAN, _SIM],
    'tip.prepare': [_MAN, _QD, _SIM], 'sample.mount': [_MAN, _QD, _SIM], 'sample.unmount': [_MAN, _SIM], 'sample.cleave': [_MAN, _SIM],
    'holder.insert': [_MAN, _SIM], 'holder.push': [_MAN, _SIM], 'holder.rotate': [_MAN, _SIM], 'holder.remove': [_MAN, _SIM], 'maintenance.clean_guides': [_MAN],
    'power.set': [_MAN, _QD, _SIM], 'power.press_button': [_MAN, _SIM], 'z_controller.set': [_MAN, _QD, _WB], 'approach.advance': [_MAN, _SIM],
    'approach.retract': [_MAN, _SIM], 'approach.home': [_MAN, _SIM], 'approach.stop': [_MAN], 'approach.withdraw': [_MAN, _SIM], 'approach.start': [_MAN, _QD, _SIM],
    'scan.start': [_QD, _MAN, _WB, _NS, _AZ, _PHYS, _LIB, _SIM], 'spectroscopy.iv': [_QD, _MAN, _WB, _PHYS, _LIB, _SIM],
    'spectroscopy.iz': [_QD, _MAN, _WB, _PHYS, _LIB, _SIM], 'tip.cleaning_pulse': [_MAN, _SIM], 'magnifier.view': [_MAN, _SIM],
    'analysis.flatten': [_MAN], 'analysis.roughness': [_PHYS], 'analysis.line_profile': [_MAN], 'analysis.step_height': [_MAN, _PHYS], 'analysis.lattice': [_MAN, _PHYS],
    'analysis.iv': [_MAN, _PHYS], 'analysis.iz': [_MAN, _PHYS], 'measurement.result': [_WB, _MAN], 'records.list': [_SIM], 'records.delete': [_SIM], 'events.read': [_SIM],
    'provenance.read': [_LIB], 'fault.inject': [_SIM], 'ledger.read': [_SIM], 'sim.step': [_SIM],
    **{k: [_QD, _MAN] for k in list(DECLARED) + list(UNSUPPORTED)}}
PARAM_ZH = {'room_k': '室温（Fermi分布与记录）', 'humidity': '相对湿度（大气中的表观势垒＝(0.45−0.35 RH)×平均功函数，工程值）',
            'temperature_stability_k': '扫描头处室温波动均方根（机械回路30 nm/K，由手册“0.1 °C引起几nm”推得）', 'floor_vibration': '地面振动等级（花岗岩台与橡胶弹簧脚之后的间隙振动）',
            'noise_seed': '噪声种子（决定出厂针尖、扫描器标定误差、漂移方向与测量噪声）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.naiostm_stm.limitations。'
CAP_LIMITS = {
    'sample.provide': ['样品为样品库19种模板的程序化表面（种子确定，同一位置每次扫描相同）；材料常数取隧穿库（57种：功函数、晶格、台阶、电阻率、带隙与态密度模型、大气表面状态逐项标证据）；'
                       '样品贴在镀镍钢片上（钢片尺寸为工程值），手册限制直径≤10 mm、厚≤3 mm。'],
    'tip.prepare': ['剪断后针尖状态按工程概率（锋利45 %、双针尖20 %、钝20 %、不稳定15 %），尖端半径与第二尖端为隐藏真值；出厂针尖为锋利（手册：出厂标定用的针尖可达原子分辨）。'],
    'holder.insert': ['手推距离下限0.2 mm、转动0.3 mm为工程值（手册：推到1 mm以内、不要碰针尖）：由提罩时的肉眼估计判断（0.1 mm分辨，每次放入的观察偏差±15 %，'
                      '每次观察另有约20 µm的抖动，与magnifier.view提罩时的估计相同），不是精确间隙；针尖偏离样品座轴线0.5–2.5 mm（工程），转动样品座换位置。'],
    'approach.advance': ['电机最大速度0.25 mm/s为工程值；返回的是按住时间与名义行程（Naio软件不知道样品座位置）；Advance不因电流停下（手册：看着放大镜推到“几分之一毫米”）；'
                         '推过接触时针尖弯曲20 µm后电机打滑，样品座停住而电机仍被驱动到松开（撞针与打滑只对评测者可见）。'],
    'approach.start': ['自动进针：步长＝3 nm×Move speed（工程；手册：步长太大会撞针、太小电机不动），周期20 ms（工程；手册：10–20 s未完成应先用电机再靠近）；'
                       '检测条件为Z伸出200 nm内达到设定电流（手册）；导向球脏时电机运动×0.2（工程，注入后立即生效）；进行中只公布已走周期数/最大步数与按最大步数计的剩余时间上限，'
                       '结果（何时找到、是否撞针）只在发生时可见。'],
    'scan.start': ['成像：表观高度＝程序化形貌＋表面原子晶格（与1T-TaS2电荷密度波）的表观起伏（随尖端半径与间隙衰减、沿快轴按像素平均），双针尖按两尖端电流相加（log-sum-exp），'
                   '不稳定针尖按电报噪声；Z反馈在对数电流误差上（带宽2π·10 Hz·(I+0.5P)、临界增益60，工程），误差在5 pA与100 nA处饱和、Z量程200 nm；间隙振动、热漂移（扰动后'
                   '衰减，900 s，工程）、室温波动（30 nm/K）、压电蠕变2 %、扫描器标定误差（隐藏，σ 3 %）与正反向迟滞0.4 %（工程）；形貌为16位Z输出（3.1 pm步长），电流为放大器'
                   '（2 pA＋1 %噪声、16位ADC）；XY位置按16位7.6 pm步长量化（像素间距小于该步长时重复同一DAC值并警告pixel_below_xy_dac_step）；针尖几何：尖端按隐藏半径的抛物面对'
                   '形貌做灰度膨胀（块内平整时跳过，窗口最多±64点，不足时警告tip_footprint_truncated），原子起伏来自最突出的尖端原子；电流像用设定点附近的指数关系换算'
                   '（无电流时用完整隧穿积分）；每16行一块计算。'],
    'spectroscopy.iv': ['I-V：一维WKB隧穿积分（Simmons梯形平均势垒随能量、室温Fermi分布、样品态密度模型、针尖态密度平坦、表面氧化层透射），大气中表观势垒降低（工程）；'
                        '调制中Z保持，热漂移改变间隙（正反向不重合）；高电压下的场发射区只按截断处理。'],
    'spectroscopy.iz': ['I-z：同一隧穿积分随间隙；越过点接触（0.25 nm，工程）即撞针，电流在100 nA饱和。'],
    'magnifier.view': ['间隙估计的分辨率（放大镜10 µm、肉眼0.1 mm）、最小可分辨间隙（30 µm/0.2 mm）、观察角偏差（每次放入±15 %）与每次观察的抖动（放大镜2 µm、肉眼20 µm，'
                       '随时间变化）为工程值；“看起来已接触”按同一带偏差与抖动的估计判断；样品粗糙时看不到针尖镜像。'],
    'tip.cleaning_pulse': ['脉冲后针尖状态的转移概率为工程值（手册只说明用来去掉针尖拾取的材料）。'],
    'z_controller.set': ['设定点按25 pA步长取整（规格）；增益的数值尺度（带宽每单位增益10 Hz）为工程值；D增益未建模（为0）。'],
    'measurement.result': ['记录的template_source.template_id与substance只对评测者可见（agent为null）；图像按块平均降采样。'],
    'plant.configure': ['湿度决定大气中的表观势垒；室温波动与振动等级为宿主输入；重新声明需断电。'],
    'power.set': ['Naio软件初始化8 s为工程值；功率30 W（规格）计入电能账。'],
}
LIMITATIONS = [
    '选定配置：Nanosurf NaioSTM（扫描范围500×500×200 nm、电流0.1–100 nA 25 pA步长、针尖电压±10 V、2048×2048点、行频10 Hz、恒流/恒高成像、I-V与I-z谱、stick-slip自动进针、'
    '带10×放大镜的放大罩）；刻蚀模式为已声明未实现；按电流停止的谱、侧视相机、主动隔振台为不支持的选件。',
    '厂家公布：扫描范围与16位数字分辨率、电流范围与步长、针尖电压、图像与谱点数、行频、样品尺寸、功率、外形与质量、尺寸图、针尖丝规格、清洁脉冲、放大倍数、进针流程与状态灯、'
    '恒高模式的增益、热漂移的量级与稳定时间；进针步长与速度、反馈带宽尺度、针尖状态概率、振动、漂移、蠕变、标定误差、大气势垒因子为工程假设。',
    '隧穿：一维WKB、能量相关梯形平均势垒、平坦针尖态密度、样品态密度为分类模型（金属、半金属V形、半导体带隙、CDW赝能隙、拓扑表面锥、绝缘体）；像电势、针尖电场引起的能带弯曲、'
    '场发射区、非弹性隧穿、力与表面变形（HOPG的巨大起伏只按表观起伏）未建模。',
    '样品形貌为程序化模板（台面与台阶、多晶晶粒、粗糙面、原子晶格与CDW超晶格的表观起伏），不是真实STM数据；物质常数来自公开表（隧穿库逐项标证据）。',
    '没有真实NaioSTM硬件或Naio软件接口；装样、装针、放大罩与样品座的手工操作为宿主/机器人抽象，没有接触验证。',
]


def _sampling_text(k):
    if k in ('clock.time_s', 'holder.position_m', 'operation.progress', 'operation.remaining_s', 'truth.drift_m', 'truth.gap_m', 'zscanner.extension_m'):
        return 'Instantaneous value at the read time: motor motion, operation progress and the thermal drift are integrated exactly and cut at every event.'
    if k == 'current.reading_a':
        return 'Instantaneous current at the read time with the amplifier noise of that millisecond (deterministic in the noise seed and the time).'
    if k == 'scan.lines_completed':
        return 'Updated when a block of 16 lines is completed (or at the end, cancellation or failure of the scan).'
    return 'Changes when a command is accepted or at an event of the operation; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    if k in DECLARED:
        return ['NOT_IMPLEMENTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    return '；另一个agent的操作进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）' if k in _CONTROL_GUARDED else ''


def _documented_parameters():
    return {
        'scan_range_m': ([XY_RANGE_M, XY_RANGE_M, Z_RANGE_M], 'm', '扫描范围500×500×200 nm（代表值；QD Japan页面、手册、调研表）', [_QD, _MAN, _WB]),
        'digital_resolution_m': ([XY_DAC_M, XY_DAC_M, Z_DAC_M], 'm', '数字分辨率7.6×7.6×3.1 pm＝最大范围/16位（不是空间准确度）', [_QD, _MAN]),
        'current_range_a': (list(CURRENT_RANGE_A), 'A', '电流放大器0.1–100 nA', [_QD, _MAN, _WB]), 'setpoint_step_a': (SETPOINT_STEP_A, 'A', '电流设定25 pA步长', [_QD, _MAN, _WB]),
        'tip_voltage_range_v': (list(TIP_VOLTAGE_RANGE_V), 'V', '针尖电压−10 V到+10 V，样品接仪器地（手册）', [_MAN]),
        'max_points': (MAX_POINTS, '1', '成像最多2048×2048点', [_QD, _MAN, _WB]), 'spectroscopy_points_max': (SPEC_POINTS_MAX, '1', '谱最多65535点（QD页面写65335，冲突已记录）', [_MAN, _QD]),
        'line_rate_max_hz': (LINE_RATE_MAX_HZ, 'Hz', '行频最高10 Hz', [_QD, _MAN, _WB]), 'sample_max_m': ([SAMPLE_MAX_DIAMETER_M, SAMPLE_MAX_THICKNESS_M], 'm', '样品直径≤10 mm、厚≤3 mm', [_QD, _MAN, _WB]),
        'power_w': (POWER_W, 'W', '90–240 V AC、50/60 Hz、30 W', [_QD, _MAN]), 'body_dimensions_m': (list(BODY_DIMENSIONS_M), 'm', '204×204×104 mm、3.45 kg', [_QD, _MAN, _WB]),
        'cleaning_pulse_v': (CLEANING_PULSE_V, 'V', '清洁脉冲电压约5 V（手册）', [_MAN]), 'cleaning_pulse_s': (CLEANING_PULSE_S, 's', '清洁脉冲时长约100 ms（手册）', [_MAN]),
        'tip_wire': ([WIRE_LENGTH_M, TIP_CUT_LENGTH_M], 'm', 'Pt/Ir 90/10丝Ø0.25 mm、30 cm；在约4 mm处剪拉断（手册）', [_MAN]),
        'magnification': (MAGNIFICATION, '1', '放大罩10×放大镜（手册）', [_MAN]), 'manual_push_m': (MANUAL_PUSH_M, 'm', '手推样品座到针尖1 mm以内（手册）', [_MAN]),
        'constant_height_gains': (dict(CONSTANT_HEIGHT_GAINS), '1', '恒高模式I增益4、P增益0（手册；两个增益均无量纲）', [_MAN]),
    }


def _derived_parameters():
    """Values taken from qualitative manufacturer statements: evidence assumption, with the rule and the range the statement allows."""
    return {
        'loop_m_per_k': (LOOP_M_PER_K, 'm/K', '机械回路的热膨胀系数：手册5.2“0.1 °C的变化已使（例如）钢制样品座长度变化几纳米”，取3 nm/0.1 K＝30 nm/K（推导的工程值）',
                         [_MAN, _SIM], '“几纳米”按2–9 nm计：20–90 nm/K'),
    }


def _engineering_parameters():
    return {
        'boot_s': (BOOT_S, 's', '控制器与Naio软件初始化'), 'motor_speed_m_s': (MOTOR_SPEED_M_S, 'm/s', 'Advance/Retract最大速度'),
        'step_m_per_percent': (STEP_M_PER_PERCENT, 'm', '自动进针步长/Move speed百分数'), 'motor_min_percent': (MOTOR_MIN_PERCENT, '1', '电机能走的最小Move speed'),
        'cycle_s': (CYCLE_S, 's', '自动进针周期'), 'bend_m': (BEND_M, 'm', '撞针后针尖丝弯曲到电机打滑的距离'), 'd_contact_m': (D_CONTACT_M, 'm', '点接触间隙（透射为1）'),
        'air_barrier': ([AIR_BARRIER_A, AIR_BARRIER_B], '1', '大气表观势垒因子A−B·RH'), 'i_floor_a': (I_FLOOR_A, 'A', '电流噪声底（误差饱和）'),
        'i_noise_a': (I_NOISE_A, 'A', '放大器噪声RMS'), 'fb_hz_per_gain': (FB_HZ_PER_GAIN, 'Hz', '每单位增益反馈带宽'), 'critical_gain': (CRITICAL_GAIN, '1', '反馈振荡临界增益'),
        'vibration_m': (dict(VIBRATION_M), 'm', '间隙振动RMS（按地面振动等级）'), 'drift_tau_s': (DRIFT_TAU_S, 's', '扰动后热稳定时间常数'),
        'drift_kick_m': (dict(DRIFT_KICK_M), 'm', '各类扰动的漂移幅度'), 'creep_fraction': (CREEP_FRACTION, '1', '压电蠕变比例'), 'creep_tau_s': (CREEP_TAU_S, 's', '蠕变时间常数'),
        'cal_sigma': (CAL_SIGMA, '1', '扫描器标定误差1σ'), 'hysteresis_fraction': (HYSTERESIS_FRACTION, '1', '正反向迟滞位移/图像尺寸'),
        'corr_beta_per_m': (CORR_BETA_M, '1/m', '原子起伏随尖端半径与间隙的衰减常数（0.25 nm周期）'), 'tip_probs': (dict(TIP_PROBS), '1', '剪断后针尖状态概率'),
        'pickup_per_line': (PICKUP_PER_LINE, '1', '每行拾取材料的概率×(0.1+污染)'), 'drop_per_line': (DROP_PER_LINE, '1', '每行丢掉拾取材料的概率'),
        'push_min_gap_m': (PUSH_MIN_GAP_M, 'm', '手工操作的最小间隙'), 'tip_offset_m': (list(TIP_OFFSET_M), 'm', '针尖偏离样品座轴线的范围'), 'tip_work_s': (TIP_WORK_S, 's', '装/取针尖时间'),
        'default_setpoint_a': (DEFAULT_ZC['setpoint_a'], 'A', '软件启动时的电流设定点'), 'default_tip_voltage_v': (DEFAULT_ZC['tip_voltage_v'], 'V', '软件启动时的针尖电压'),
        'default_gains': (dict(p_gain=DEFAULT_ZC['p_gain'], i_gain=DEFAULT_ZC['i_gain']), '1', '软件启动时的P与I增益（无量纲）'), 'block_lines': (BLOCK_LINES, '1', '图像按块计算的行数'),
        'eye_quant_m': (EYE_QUANT_M, 'm', '提罩时肉眼判断间隙的分辨（手工操作的0.2/0.3 mm限度按此估计判断）'),
        'look_sigma_m': (dict(eye=EYE_SIGMA_M, magnifier=MAGNIFIER_SIGMA_M), 'm', '每次观察间隙的判断抖动（肉眼/放大镜，独立、3σ截断、随时间变化）'),
    }


def engineering_parameters():
    return {k: dict(value=v, unit=u, meaning_zh=m, evidence='assumption') for k, (v, u, m) in _engineering_parameters().items()}


def export_nanosurf_naiostm_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'NanosurfNaiostmSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=[_SIM])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED or k in DECLARED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if k in UNSUPPORTED else 'declared' if k in DECLARED else 'implemented',
                 implementation={layer: ('declared' if k in DECLARED and layer == 'process' else 'absent') if un or layer in ('scene', 'robot', 'hardware') else 'implemented'
                                 for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.nanosurf_naiostm.NanosurfNaiostmSession.invoke',
                                              invocation=f'session.invoke(instance_id="{INSTANCE_ID}",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='NanosurfNaiostmRuntime.invoke / NanosurfNaiostmSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate',
                                     description_zh='设定、目标、状态标志或立即改变的派生量；运动与操作进度只随step推进') for s in EFFECTS.get(k, [])],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置、分析或查询；运动由step推进',
                                completion_state_ids=(['operation.id', 'approach.state'] if k == 'approach.start' else ['operation.id', 'result.last_record_id'] if async_ else []),
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=(EXEC_TEXT[k][1] if k in EXEC_TEXT else
                                                    ('不支持或未实现：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果（运动目标），被拒时状态不变'))),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH.get(e, e)),
                              side_effects='unknown' if e == 'DEVICE_ERROR' else 'partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED.get(k) or DECLARED.get(k)] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k) or '随step演化') for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in _documented_parameters().items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    for name, (val, unit, meaning, src, unc) in _derived_parameters().items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    for name, (val, unit, meaning) in _engineering_parameters().items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['granite_table', 'isolation_feet', 'isolation_springs', 'controller_housing', 'button_power', 'led_power', 'rear_connectors', 'head_ring',
                       'scan_head_body', 'scan_head_cap', 'slot_floor', 'guide_spheres', 'approach_motor', 'holder_magnet', 'tip_holder', 'tip_clamp', 'stm_tip',
                       'sample_holder', 'holder_magnet_face', 'holder_handle', 'magnifying_cover', 'cover_window', 'magnifier', 'magnifier_lens'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='NaioSTM扫描隧道显微镜：样品座与stick-slip进针（手推、Advance/Retract/Home/Withdraw、自动进针）、剪切Pt/Ir针尖的隐藏状态、隧穿电流（WKB、功函数、态密度、Fermi分布、'
                   '氧化层）、对数电流Z反馈、恒流形貌与恒高电流像、I-V与I-z谱、热漂移与压电误差、图像与谱分析',
        equations=['I = G0 T_ox int rho_s(e) [f(e + eV_tip) - f(e)] exp(-2 (d - d_c) kappa(e)) de, kappa(e) = sqrt(2 m (phi - e V_s / 2 ... )) / hbar with phi = '
                   '(0.45 - 0.35 RH) (phi_tip + phi_sample) / 2, V_s = -V_tip, T_ox = exp(-2 kappa_ox t_ox)',
                   'feedback: dz/dt = omega clamp(y - z, e_lo, e_hi), omega = 2 pi 10 Hz (I + 0.5 P), e_lo = -ln(100 nA / I_set) / (2 kappa), e_hi = ln(I_set / 5 pA) / (2 kappa)',
                   'approach: cycle k finds the set point when g0 - k s - 200 nm <= d_set (s = 3 nm x move speed %); touch when g0 - k s - 200 nm <= d_c',
                   'apparent height = h_meso + (c A / span) sum_i sinc(G_i.f px / 2) cos(G_i.(r - t0)), A = min(2, exp(-3e9 (0.25 nm / a)(R + d - R_ref - d_ref)))',
                   'double tip: h = h1 + ln(1 + exp(2 kappa (h2 - h1))) / (2 kappa)',
                   'drift: sum_k A_k (1 - exp(-(t - t_k) / 900 s)) + 30 nm/K sigma_T sqrt(2/3) sum_j sin(2 pi t / P_j + phi_j)',
                   'I-z: phi_app = (d ln I / dz / 2 / 5.123e9)^2 eV'],
        parameters=params, source_ids=[_SIM, _QD, _MAN, _NS, _AZ, _PHYS, _LIB, _WB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_nanosurf_naiostm_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'naiostm_stm': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'naiostm_stm': mechanism})
