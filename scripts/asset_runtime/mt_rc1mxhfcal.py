"""CHEM-149 METTLER TOLEDO RC1mx + HFCal (standard torque stirrer, MidTemp oil, AP01-2 glass reactor): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * three lumped heat nodes -- reaction mass + inserts (C_r = sum m_i cp_i + C_ins), hot oil loop + jacket + reactor glass
    (C_j) and cold oil container (C_c) -- coupled by the jacket heat-transfer coefficient UA, the control valve (cold oil mixed
    into the hot loop, G_v u), the emergency safety-valve bypass, the coolant heat exchanger (effectiveness with the declared
    coolant flow), losses to the room, the electrical heater (2000 W, RC1e MidTemp) and the pump heat of the cold loop; advanced
    by Crank-Nicolson sub-steps with the actuators held over each 2 s instrument cycle; every energy flow is integrated with the
    same mid-point temperatures as the state update, so the ledger closes to rounding;
  * the thermostat constants are fitted to the manufacturer's heating/cooling curves of the AP01-2 reactor on the RC1e
    (digitised curves 1-4: empty reactor in the Tj mode, 1 L and 2 L water at 200 rpm in the Tr mode, 1 L glycerol with
    emergency program A; outputs/.../thermal_calibration.json);
  * UA = U A_wet(V) with the jacketed wetted area from the reactor fill table of the geometry, 1/U = 1/phi + 1/h_r and
    h_r = h_ref (n/200 rpm)^(2/3) (rho/rho_w)^(2/3) (cp/cp_w)^(1/3) (k/k_w)^(2/3) (mu_w/mu)^(1/3) (Nusselt ~ Re^(2/3) Pr^(1/3);
    water reference fitted), natural convection without stirring;
  * stirrer: speed ramp, anchor power number Np = 330/Re + 0.35, torque limited by the glass stirrer (0.3 N m, RC1e) and the
    motor (1 N m, RC1mx), stirrer power into the mass; the shaft angle is mirrored modulo one turn;
  * chemistry: the reaction mass is a mole inventory of library substances (NIST liquid heat capacities and enthalpies of
    formation, PubChem densities); library reactions run with host-supplied Arrhenius kinetics (backward-Euler extents on
    adaptive sub-steps); the temperature update conserves sum n_i (dfH_i + cp_i (T - 298.15 K)) + C_ins (T - 298.15 K), so the
    reaction heat and its Kirchhoff correction come from the enthalpies of formation; dosing mixes the fed mass at its feed
    temperature; the bottom valve drains the mass;
  * instrument (2 s cycle, RC1e): Pt100 readings with static offsets, correlated noise and 0.01 K resolution; Tj PI (heater /
    control valve with the ~100 W stationary cooling); Tr cascade PI (RC1e AP01 defaults P 9 / I 180 s aqueous, P 4.5 organic)
    limited to |Tj - Tr| <= T diff max (50 K glass reactor; released while Tr rises during cooling, RC1e 9.6), Tj min/max and the
    oil limits; linear ramps; power-up to Tsafe with the last stirrer set value (RC1e 5.9); safety limits and emergency programs A,
    B1 and C with the RC1e actions (B1/C ramp from the current temperature), emergency button, Tsafe and Rsafe;
  * HFCal: calibration heater (host prior power), UA / baseline calibration in the Tr mode, heat-capacity ramp, online heat flow
    UA_cal (Tr - Tj), experiment record and event log; calorimetry.evaluate computes q_r = q_flow + q_accu + q_dos - q_cal -
    q_baseline, its integral, the reaction enthalpy, the adiabatic temperature rise, the thermal conversion, the accumulation and
    the MTSR from indicated values only, with quality flags (calibrations, steady state, incomplete reaction, boiling);
  * the model has no boiling: Tr/Tj/end/cp targets and Tsafe must stay 2 K below the bubble point, and so must the state and the
    active targets after material is charged or dosed; a mass that still reaches its bubble point (e.g. a runaway or an unpowered
    reactor) raises BOILING_NOT_MODELLED and flags evaluations of that window.
"""
from __future__ import annotations

import bisect
import copy
import json
import math
import random
import re
from pathlib import Path

PROTOCOL_VERSION = '1.0.0'
SLUG = 'mt_rc1mxhfcal'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-149'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t1_thermal' / SLUG
LIBRARY_PATH = ASSET / 'reaction_mass_library.json'
MANIFEST_PATH = ASSET / 'geometry_manifest.json'

GAS_CONSTANT = 8.314462618
T_REF = 298.15
# ---------------------------------------------------------------- documented instrument data
CYCLE_S = 2.0                                   # RC1e 9.2: measured value acquisition every 2 s
CYCLE_NS = 2_000_000_000
RESOLUTION_K = 0.01                             # RC1e 9.2: Tj, Tr resolution 10 mK
TJ_DEVICE_MIN_K = 223.15                        # RC1mx 7.2 MidTemp: Tj -50 C device limit (with cryostat)
TJ_VISCOSITY_MIN_K = 228.15                     # RC1mx 7.2 MidTemp: -45 C oil viscosity limit
TJ_MAX_K = 503.15                               # RC1mx 7.2 MidTemp: +230 C
TJOIL_LIMITS_K = (220.15, 506.15)               # RC1e 9.3 MidTemp oil: Tjoil min -53 C, max +233 C (safety monitoring)
TR_RANGE_K = (253.15, 473.15)                   # RC1e 9.2 MidTemp: Tr -20 C (with cryostat) to +200 C
HEATER_POWER_W = 2000.0                         # RC1e 9.2/9.3 MidTemp heater (predecessor; RC1mx 7.1: max. 4800 W consumption)
BASE_COOLING_W = 100.0                          # RC1e 3.2: heated circuit cooled constantly with approx. 100 W in stationary operation
TDIFF_MAX_K = 50.0                              # RC1e 4 / 5.2: |Tj - Tr| limited to 50 K in the Tr mode (glass reactor)
COOLANT_MARGIN_K = 10.0                         # RC1e 9.4: lowest Tj used >= coolant temperature + 10 K
COOLANT_MIN_FLOW_M3_S = 10e-3 / 60.0            # RC1mx 7.3: flow rate >= 10 L/min
WATER_COOLANT_MAX_K = 298.15                    # RC1e 9.4: water ~15 C, 25 C maximum
TR_CONTROLLER = {'aqueous': (9.0, None), 'organic': (4.5, None), 'aqueous_pi': (9.0, 180.0), 'organic_pi': (4.5, 180.0)}
#   RC1e 4.2.1 AP01-1/-2: the software presets the reset time to 999999 s (P-only, default); the table gives the PI values P 9 / I 180 s
#   (aqueous) and P 4.5 / I 180 s (organic) for users who enable the integral action
EMERGENCY_GRADIENT_K_S = 0.1                    # RC1e 5.5: Tj (or Tr) to Tsafe with gradient 0.1 K/s
MOTOR_SPEED_MAX_RPM = 2500.0                    # RC1mx 7.5 standard torque stirrer: 1...2500 rpm
MOTOR_TORQUE_MAX_NM = 1.0                       # RC1mx 7.5 standard torque stirrer: max. 1 N m
AP01_SPEED_RANGE_RPM = (30.0, 850.0)            # RC1e 9.5 AP01-2: 30 to 850 rpm (stirrer type and viscosity)
GLASS_STIRRER_TORQUE_MAX_NM = 0.3               # RC1e 9.5: glass stirrers max. 0.3 N m
STIRRER_MIN_RPM_B1 = 25.0                       # RC1e 5.6: stirrer speed less than 25 rpm -> B1 (with Rmin > 0)
STIRRER_LOW_DELAY_S = 60.0                      # RC1e 5.6 ("for 1" [min], truncated in the source)
STIRRER_OFF_DELAY_S = 180.0                     # RC1e 5.6: stirrer switched off manually for 3 min (Rmin > 0) -> B1
TC_MAX_DELAY_S = 180.0                          # RC1e 5.4 example: Tc > Tc max for 3 min -> A (table 5.6 prints "3 s": conflict recorded)
TC_LIMITS_K = (220.15, 313.15)                  # RC1e 5.6: Tc outside limits (Tjoil min ... +40 C) -> C
TIMED_CAUSES = ('TC_ABOVE_MAX_3MIN', 'STIRRER_OFF_3MIN', 'STIRRER_BELOW_25RPM')   # delayed causes: their delay restarts after a reset
NOMINAL_VOLUME_M3 = 2.5e-3                      # RC1e 9.5 AP01-2
OPERATING_VOLUME_M3 = (0.5e-3, 2.0e-3)          # RC1e 9.5 AP01-2
PT100_MAX_ERROR_K = 0.5                         # RC1mx 7.2 class A: max. permissible error 0.5 C (-20...100 C)
# ---------------------------------------------------------------- thermostat constants fitted to the RC1e curves (outputs/.../thermal_calibration.json;
#                                                                  C_INS is a fixed engineering value, the curves do not resolve it)
PLANT_FIT = dict(C_J=9041.9, L_J=6.71, G_VALVE=57.102, G_SAFETY=55.898, C_C=31967.0, UA_COIL=289.94, P_COLD=494.45, C_INS=150.0, L_R=0.0051656, K_EV=6.7878e-06, U_W200=181.31)
FIT_CONDITIONS = dict(ambient_k=295.15, coolant_k=289.15, coolant_flow_m3_s=10e-3 / 60.0, stirrer_rpm=200.0)
# ---------------------------------------------------------------- engineering values
PHI_WALL_W_M2K = 1.0 / (0.004 / 1.14 + 1.0 / 1500.0)   # 4 mm Duran wall (k 1.14 W/m K) + MidTemp oil film ~1500 W/m2 K (engineering)
H_NATURAL_W_M2K = 100.0                         # inner film without stirring (engineering)
UA_EMPTY_W_K = 1.0                              # empty reactor: inserts/air to the glass (engineering)
L_COLD_W_K = 3.0                                # cold oil container gains from the room (engineering)
KP_TJ_W_K, TI_TJ_S = 1000.0, 60.0               # Tj PI (engineering; Kp T / C_j = 0.22 <= 0.5)
STIRRER_ACCEL_RPM_S = 100.0                     # engineering
IMPELLER_D_M = 0.100                            # anchor diameter (geometry, engineering)
NOISE_TAU_S = 20.0                              # correlation time of the Pt100 noise (engineering)
DOSING_RESOLUTION_KG = 1e-4                     # dosing balance 0.1 g (engineering)
DRAIN_HANDLE_RAD_S = math.pi / 2                # bottom-valve handle turns in 1 s (host proxy, engineering)
STIRRER_JOINT_MAX_RAD = 2 * math.pi
DRAIN_OPEN_RAD = math.pi / 2
MAX_SUBSTEP_S = CYCLE_S
MIN_SUBSTEP_S = 1e-3
REACTION_DT_K = 0.1                             # adaptive sub-step: adiabatic reaction heating <= 0.1 K per sub-step
MAX_POINTS = 20000
MAX_EVENTS = 1000
MAX_RECORDS = 200
WATER_REF = dict(rho=997.0, cp=4181.0, k=0.607, mu=0.00089)      # film-coefficient reference (water 25 C, engineering constants)
PURGE_MIN_FLOW_M3_S = 80e-6 / 60.0              # RC1mx 7.4 (not in the process model)
PURGE_MAX_INLET_PA = 0.5e5                      # RC1mx 7.4 (not in the process model)
TROUTON_J_MOL_K = 88.0                          # Trouton's rule for the vapour-pressure estimate (engineering)
TDIFF_RELEASE_K_S = 1e-3                        # RC1e 9.6: T diff max not effective while dTr/dt > 0 during cooling: slope threshold 0.06 K/min, raised
TDIFF_RELEASE_PER_NOISE = 0.24                  #   to 0.24/s x the declared Pt100 noise (~6 x the 30 s slope scatter of the correlated noise) (engineering)
TR_SLOPE_CYCLES = 15                            # least-squares slope of the indicated Tr over the last 15 cycles (30 s, engineering)
TJ_CASCADE_MARGIN_K = 1.0                       # cascade output kept 1 K inside Tj min / Tj max so reading noise does not trip C / A (engineering)
CAL_DRIFT_MAX_REL = 0.05                        # HFCal: drift of (Tr - Tj) in the averaged halves > 5 % of the signal -> not steady (engineering)
CP_MIN_SLOPE_FRACTION = 0.2                     # heat-capacity ramp: measured dTr/dt below 20 % of the programmed rate -> no response
EVAL_EDGE_FRACTION = 0.05                       # evaluation: |q_r| at a window edge above 5 % of the peak -> reaction not complete / already running
EVAL_END_SIGMA, EVAL_START_SIGMA = 1.0, 2.0     # edge levels in units of the q_r scatter: the mean over the last third of the window (<= 10 min)
#   and over 150...30 s before it never exceeded 0.83 / 1.63 scatter units in 32 040 baseline windows (0.003-0.02 K noise, 0.8-1.9 kg water,
#   aqueous/organic Tr control, 900/1800 s); slow jacket swings fed by the P cascade dominate these means (engineering calibration recorded in
#   outputs/.../mt_rc1mxhfcal/evaluation_statistics_calibration.json by calibrate_evaluation_statistics.py)


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


def array(s, minimum=0, maximum=MAX_POINTS):
    return dict(type='array', items=s, minItems=minimum, maxItems=maximum)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


BOOL = {'type': 'boolean'}
TEXT = {'type': 'string'}
INT = {'type': 'integer', 'x-unit': '1'}


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
        if not isinstance(v, dict) or set(v) != set(s['properties']):
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
    elif k == 'string' and not isinstance(v, str):
        raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- library and reactor geometry
_LIBRARY = None
_FILL = None


def _lib():
    """Eligible substances and reactions of reaction_mass_library.json (loaded once; shared read-only inside the runtime)."""
    global _LIBRARY
    if _LIBRARY is None:
        raw = json.loads(LIBRARY_PATH.read_text())
        subs = {}
        for s in raw['substances']:
            if not s['runtime_eligible']:
                continue
            subs[s['id']] = dict(M=s['molar_mass_kg_mol'], cp_mol=s['cp_liquid']['value_J_mol_K'], cp_kg=s['cp_liquid']['value_J_kg_K'],
                                 rho=s['density']['value_kg_m3'], dfh=(s['delta_f_h_liquid'] or {}).get('value_J_mol'), name_zh=s['name_zh'],
                                 tb=s.get('boiling_point_K'), tm=s.get('melting_point_K'))
        reactions = {r['id']: dict(reactants=dict(r['reactants']), products=dict(r['products']), dh=r['delta_r_h_J_mol'], name_zh=r['name_zh'])
                     for r in raw['reactions'] if r['runtime_eligible']}
        _LIBRARY = dict(substances=subs, reactions=reactions, sha=raw.get('built'), source=str(LIBRARY_PATH.relative_to(ROOT)))
    return _LIBRARY


def library():
    """Deep copy of the eligible library records (callers may modify it without touching the runtime cache)."""
    return copy.deepcopy(_lib())


def fill_table():
    """(volume m3, liquid height m, jacketed wetted area m2) of the AP01-2 inner glass profile (geometry_manifest.json)."""
    global _FILL
    if _FILL is None:
        rows = json.loads(MANIFEST_PATH.read_text())['reactor']['fill_table']
        _FILL = ([r['volume_m3'] for r in rows], [r['height_m'] for r in rows], [r['wetted_jacketed_area_m2'] for r in rows])
    return _FILL


def _interp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1] + (ys[-1] - ys[-2]) / (xs[-1] - xs[-2]) * (x - xs[-1])
    i = bisect.bisect_right(xs, x) - 1
    return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i])


def wetted_area(volume_m3):
    v, h, a = fill_table()
    return max(_interp(volume_m3, v, a), 0.0)


def liquid_height(volume_m3):
    v, h, a = fill_table()
    return max(_interp(volume_m3, v, h), 0.0)


def quantize(value, step):
    return None if value is None else round(round(value / step) * step, 10)


# ---------------------------------------------------------------- thermal plant (shared with the curve fit)
def coolant_conductance(flow_m3_s, heat_capacity_j_m3k, ua_coil):
    """Effectiveness of the cooling coil: H = C_w (1 - exp(-UA_coil / C_w)) with C_w = flow rho cp of the coolant."""
    cw = flow_m3_s * heat_capacity_j_m3k
    return cw * (1.0 - math.exp(-ua_coil / cw)) if cw > 0 else 0.0


def solve3(A, b):
    """Gaussian elimination with partial pivoting (3x3)."""
    M = [list(A[i]) + [b[i]] for i in range(3)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        for r in range(c + 1, 3):
            f = M[r][c] / M[c][c]
            for k in range(c, 4):
                M[r][k] -= f * M[c][k]
    x = [0.0, 0.0, 0.0]
    for r in (2, 1, 0):
        x[r] = (M[r][3] - sum(M[r][k] * x[k] for k in range(r + 1, 3))) / M[r][r]
    return x


def vapour_pressure_pa(tb_k, T):
    """Clausius-Clapeyron anchored at the NIST normal boiling point with Trouton's rule (dS_vap = 88 J/(mol K), engineering)."""
    return 101325.0 * math.exp(-TROUTON_J_MOL_K * tb_k / GAS_CONSTANT * (1.0 / T - 1.0 / tb_k))


def top_loss_coefficient(T_r, p_vap_pa, fit):
    """Reactor top losses: conduction/convection through cover, flange and probes (L_R) plus vapour evaporating and condensing
    under the cooler cover, proportional to the Raoult vapour pressure of the mass (K_EV p_vap); both fitted to the RC1e curves
    (the ~10 K Tj - Tr offset of water at 95 C, the ~2 K offset of glycerol at 200 C).  Coefficient in W/K, held over a sub-step."""
    return fit['L_R'] + fit['K_EV'] * p_vap_pa


def plant_step(T, h, C_r, UA, Lr, G_v, heater_w, source_w, H, coolant_k, ambient_k, p_cold_w, fit):
    """One Crank-Nicolson step of the three nodes (reaction mass, hot loop, cold loop) with the top-loss coefficient Lr held.
    Returns the new temperatures and the energy flows (J) evaluated at the mid-point temperatures (consistent with the
    update, so the ledger closes to rounding)."""
    Cj, Cc, Lj = fit['C_J'], fit['C_C'], fit['L_J']
    M = [[-(UA + Lr) / C_r, UA / C_r, 0.0],
         [UA / Cj, -(UA + Lj + G_v) / Cj, G_v / Cj],
         [0.0, G_v / Cc, -(G_v + H + L_COLD_W_K) / Cc]]
    bvec = [(Lr * ambient_k + source_w) / C_r, (Lj * ambient_k + heater_w) / Cj, (H * coolant_k + L_COLD_W_K * ambient_k + p_cold_w) / Cc]
    lhs = [[(1.0 if i == j else 0.0) - 0.5 * h * M[i][j] for j in range(3)] for i in range(3)]
    rhs = [T[i] + 0.5 * h * sum(M[i][j] * T[j] for j in range(3)) + h * bvec[i] for i in range(3)]
    T1 = solve3(lhs, rhs)
    Tm = [(T[i] + T1[i]) / 2 for i in range(3)]
    flows = dict(jacket_to_mass_j=h * UA * (Tm[1] - Tm[0]), mass_loss_j=h * Lr * (Tm[0] - ambient_k), jacket_loss_j=h * Lj * (Tm[1] - ambient_k),
                 valve_exchange_j=h * G_v * (Tm[1] - Tm[2]), coolant_removed_j=h * H * (Tm[2] - coolant_k), cold_room_gain_j=h * L_COLD_W_K * (ambient_k - Tm[2]),
                 heater_j=h * heater_w, mass_source_j=h * source_w, cold_pump_j=h * p_cold_w)
    return T1, flows


class TjController:
    """Tj PI of the RC1 thermostat: net power demand -> heater (0..2000 W) or control-valve opening; the stationary ~100 W
    cooling of the heated circuit is kept while heating (RC1e 3.2).  Conditional integration (anti-windup)."""

    def __init__(self, fit, kp=KP_TJ_W_K, ti=TI_TJ_S):
        self.fit, self.kp, self.ti, self.i = fit, kp, ti, 0.0

    def reset(self, integral=0.0):
        self.i = integral

    def output(self, tj_set, tj_m, tc_m):
        e = tj_set - tj_m
        req = self.kp * e + self.i
        dT = tj_m - tc_m
        g = self.fit['G_VALVE']
        base_u = min(1.0, BASE_COOLING_W / (g * max(dT, 10.0))) if dT > 1.0 else 0.0
        base_w = base_u * g * max(dT, 0.0)
        if req + base_w >= 0.0:
            heater = min(req + base_w, HEATER_POWER_W)
            u = base_u
            sat_hi = heater >= HEATER_POWER_W
            sat_lo = False
        else:
            heater = 0.0
            u = min(1.0, max(0.0, -req / (g * dT))) if dT > 1.0 else 0.0
            sat_hi = False
            sat_lo = u >= 1.0 or dT <= 1.0
        if not ((sat_hi and e > 0) or (sat_lo and e < 0)):
            self.i += self.kp * CYCLE_S / self.ti * e
            self.i = max(-3.0 * HEATER_POWER_W, min(HEATER_POWER_W, self.i))
        return heater, u


class TrController:
    """Tr cascade controller of the RC1 (RC1e 4.2): Tj set = Tr set + P (Tr set - Tr) [+ (P / I) integral when an I time is
    set; the RC1e software default is P-only], limited to Tr +- T diff max and to the admissible Tj window; the lower limit
    Tr - T diff max is not effective while cooling with dTr/dt > 0 (RC1e 9.6, e.g. a runaway); conditional integration."""

    def __init__(self, p=9.0, i_s=None):
        self.p, self.i_s, self.i = p, i_s, 0.0

    def reset(self):
        self.i = 0.0

    def output(self, tr_set, tr_m, tdiff, tj_lo, tj_hi, release_cooling=False):
        e = tr_set - tr_m
        raw = tr_set + self.p * e + self.i
        lo = tj_lo if (release_cooling and raw < tr_m) else max(tj_lo, tr_m - tdiff)
        hi = min(tj_hi, tr_m + tdiff)
        if lo > hi:
            lo = hi = min(max(tr_m, tj_lo), tj_hi)
        out = min(max(raw, lo), hi)
        if self.i_s is not None and not ((raw >= hi and e > 0) or (raw <= lo and e < 0)):
            self.i += self.p * CYCLE_S / self.i_s * e
        return out


def tr_rising(hist, threshold_k_s=TDIFF_RELEASE_K_S):
    """dTr/dt > 0 for the RC1e 9.6 release of T diff max: least-squares slope of the indicated Tr over the last TR_SLOPE_CYCLES
    cycles (hist = [(t, Tr)]) above threshold_k_s and above 4 standard errors of the slope (residual scatter inflated by its
    lag-1 autocorrelation)."""
    n = len(hist)
    if n < TR_SLOPE_CYCLES:
        return False
    tm = sum(x[0] for x in hist) / n
    ym = sum(x[1] for x in hist) / n
    sxx = sum((x[0] - tm) ** 2 for x in hist)
    if sxx <= 0.0:
        return False
    b = sum((x[0] - tm) * (x[1] - ym) for x in hist) / sxx
    if b <= threshold_k_s:
        return False
    res = [x[1] - ym - b * (x[0] - tm) for x in hist]
    ss = sum(r * r for r in res)
    rho = min(0.95, max(0.0, sum(res[i] * res[i + 1] for i in range(n - 1)) / ss)) if ss > 0.0 else 0.0
    se = math.sqrt(ss / (n - 2) / sxx) * math.sqrt((1.0 + rho) / (1.0 - rho))
    return b > 4.0 * se


def stirrer_torque(rpm, rho, mu):
    """Anchor stirrer in a smooth vessel: Np = 330/Re + 0.35 (laminar/turbulent blend, engineering); returns (torque N m, power W)."""
    n = rpm / 60.0
    if n <= 0.0:
        return 0.0, 0.0
    re_ = rho * n * IMPELLER_D_M ** 2 / mu
    npow = 330.0 / re_ + 0.35
    power = npow * rho * n ** 3 * IMPELLER_D_M ** 5
    return power / (2.0 * math.pi * n), power


def film_coefficient(rpm, props):
    """Inner film coefficient of the anchor (Nusselt ~ Re^(2/3) Pr^(1/3)); water at 200 rpm = fitted reference."""
    if rpm <= 0.0:
        return H_NATURAL_W_M2K
    u = PLANT_FIT['U_W200']
    h_ref = 1.0 / (1.0 / u - 1.0 / PHI_WALL_W_M2K)
    w = WATER_REF
    factor = (props['rho'] / w['rho']) ** (2 / 3) * (props['cp'] / w['cp']) ** (1 / 3) * (props['k'] / w['k']) ** (2 / 3) * (w['mu'] / props['mu']) ** (1 / 3)
    return max(H_NATURAL_W_M2K, h_ref * (rpm / 200.0) ** (2 / 3) * factor)


def overall_ua(volume_m3, rpm, props, u_override=None):
    if volume_m3 <= 1e-7:
        return UA_EMPTY_W_K
    if u_override is not None:
        return u_override * wetted_area(volume_m3)
    h = film_coefficient(rpm, props)
    return wetted_area(volume_m3) / (1.0 / PHI_WALL_W_M2K + 1.0 / h)


# ---------------------------------------------------------------- reaction mass (mole inventory of library substances)
def mixture_properties(n, priors):
    """Mass, liquid volume (ideal mixing of the pure-liquid densities at 25 C), heat capacity sum n cp (without inserts) and the
    properties of the film coefficient (mass-weighted density and specific heat; viscosity and conductivity are host priors)."""
    subs = _lib()['substances']
    m = sum(v * subs[k]['M'] for k, v in n.items())
    V = sum(v * subs[k]['M'] / subs[k]['rho'] for k, v in n.items())
    C = sum(v * subs[k]['cp_mol'] for k, v in n.items())
    return dict(mass=m, volume=V, heat_capacity=C, rho=m / V if V > 0 else WATER_REF['rho'], cp=C / m if m > 0 else WATER_REF['cp'],
                mu=priors['mu'], k=priors['k'])


def mixture_vapour_pressure(n, T):
    """Raoult: sum x_i p_i*(T) with p_i* from the NIST normal boiling point (Clausius-Clapeyron/Trouton)."""
    subs = _lib()['substances']
    tot = sum(n.values())
    if tot <= 0.0:
        return 0.0
    return sum(v / tot * vapour_pressure_pa(subs[k]['tb'], T) for k, v in n.items() if subs[k]['tb'] and v > 0.0)


def bubble_point_k(n):
    """Temperature at which the Raoult vapour pressure reaches 101325 Pa (bisection 150-800 K); None without volatile component."""
    if not n or mixture_vapour_pressure(n, 800.0) < 101325.0:
        return None
    lo, hi = 150.0, 800.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        (lo, hi) = (mid, hi) if mixture_vapour_pressure(n, mid) < 101325.0 else (lo, mid)
    return 0.5 * (lo + hi)


def formation_enthalpy(n):
    subs = _lib()['substances']
    return sum(v * (subs[k]['dfh'] or 0.0) for k, v in n.items())


def reaction_rate(rx, cfg, n, V, T):
    """Rate of reaction (mol/s): 10^log10_a exp(-Ea/(R T)) prod c_i^order_i V, c in mol/m3 (host-supplied kinetics)."""
    if V <= 0.0:
        return 0.0
    r = 10.0 ** cfg['log10_a'] * math.exp(-cfg['ea'] / (GAS_CONSTANT * T)) * V
    for s, o in cfg['orders'].items():
        if o > 0.0:
            c = n.get(s, 0.0) / V
            if c <= 0.0:
                return 0.0
            r *= c ** o
    return r


def extent_backward_euler(rx, cfg, n, V, T, h):
    """Extent over h solving dxi = h r(n - nu dxi) (implicit, monotone, bounded by the limiting reactant)."""
    reac = rx['reactants']
    xi_max = min(n.get(s, 0.0) / c for s, c in reac.items())
    r0 = reaction_rate(rx, cfg, n, V, T)
    if xi_max <= 0.0 or r0 <= 0.0:
        return 0.0

    def f(x):
        m = dict(n)
        for s, c in reac.items():
            m[s] = max(n.get(s, 0.0) - c * x, 0.0)
        return x - h * reaction_rate(rx, cfg, m, V, T)
    hi = min(xi_max, h * r0)
    if f(hi) <= 0.0:
        return hi
    lo = 0.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        (lo, hi) = (mid, hi) if f(mid) < 0.0 else (lo, mid)
        if hi - lo <= 1e-14 * max(hi, 1e-30):
            break
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------- schemas
MODE = enum(['off', 'tj', 'tr'])
PROGRAM = enum(['A', 'B1', 'C'])
PHASE = enum(['idle', 'running', 'succeeded', 'cancelled', 'failed'])
COOLANTS = {'water': 4.18e6, 'ethanol': 1.92e6, 'silicone_oil': 1.44e6}     # volumetric heat capacity J/(m3 K) at ~20 C (engineering constants)
PLANT_EXAMPLE = dict(ambient_k=295.15, coolant_kind='water', coolant_inlet_k=289.15, coolant_flow_m3_s=10e-3 / 60.0, cal_heater_power_w=10.0,
                     tr_offset_k=0.05, tj_offset_k=-0.04, tc_offset_k=0.1, sensor_noise_k=0.003, noise_seed=7, drain_flow_m3_s=3e-5,
                     dosing_max_rate_kg_s=5e-3, accept_engineering=True)
PLANT = obj(dict(ambient_k=number('K', 283.15, 308.15), coolant_kind=enum(list(COOLANTS)), coolant_inlet_k=number('K', 220.15, WATER_COOLANT_MAX_K),
                 coolant_flow_m3_s=number('m^3/s', COOLANT_MIN_FLOW_M3_S, 40e-3 / 60.0), cal_heater_power_w=number('W', 1.0, 50.0),
                 tr_offset_k=number('K', -PT100_MAX_ERROR_K, PT100_MAX_ERROR_K), tj_offset_k=number('K', -PT100_MAX_ERROR_K, PT100_MAX_ERROR_K),
                 tc_offset_k=number('K', -PT100_MAX_ERROR_K, PT100_MAX_ERROR_K), sensor_noise_k=number('K', 0.0, 0.02), noise_seed=INT,
                 drain_flow_m3_s=number('m^3/s', 1e-6, 1e-4), dosing_max_rate_kg_s=number('kg/s', 1e-5, 0.02), accept_engineering=BOOL))
SAFETY_EXAMPLE = dict(tsafe_k=298.15, tr_min_k=268.15, tr_max_k=393.15, tj_min_k=263.15, tj_max_k=423.15, tdiff_max_k=50.0, rsafe_rpm=None,
                      rmin_rpm=0.0, rmax_rpm=850.0, tc_max_k=313.15)
SAFETY = obj(dict(tsafe_k=number('K', TR_RANGE_K[0], TR_RANGE_K[1]), tr_min_k=number('K', TR_RANGE_K[0], TR_RANGE_K[1]), tr_max_k=number('K', TR_RANGE_K[0], TR_RANGE_K[1]),
                  tj_min_k=number('K', TJ_DEVICE_MIN_K, TJ_MAX_K), tj_max_k=number('K', TJ_DEVICE_MIN_K, TJ_MAX_K), tdiff_max_k=number('K', 1.0, TDIFF_MAX_K),
                  rsafe_rpm=nullable(number('rpm', 0.0, AP01_SPEED_RANGE_RPM[1])), rmin_rpm=number('rpm', 0.0, AP01_SPEED_RANGE_RPM[1]),
                  rmax_rpm=number('rpm', AP01_SPEED_RANGE_RPM[0], AP01_SPEED_RANGE_RPM[1]), tc_max_k=number('K', 253.15, 323.15)))
CHARGE_ITEM = obj(dict(substance_id=TEXT, mass_kg=number('kg', 1e-6, 3.0)))
CHARGE_EXAMPLE = dict(substances=[dict(substance_id='water', mass_kg=0.80), dict(substance_id='acetic_acid', mass_kg=0.10)], temperature_k=298.15,
                      viscosity_pa_s=0.0011, thermal_conductivity_w_m_k=0.55, accept_priors=True)
CHARGE = obj(dict(substances=array(CHARGE_ITEM, 1, 20), temperature_k=number('K', 253.15, 373.15), viscosity_pa_s=number('Pa s', 2e-4, 2.0),
                  thermal_conductivity_w_m_k=number('W/(m K)', 0.05, 1.0), accept_priors=BOOL))
ORDER_ITEM = obj(dict(substance_id=TEXT, order=number('1', 0.0, 2.0)))
REACTION_ITEM = obj(dict(reaction_id=TEXT, log10_a=number('1', -10.0, 30.0), activation_energy_j_mol=number('J/mol', 0.0, 300000.0), orders=array(ORDER_ITEM, 1, 4)))
REACTION_EXAMPLE = dict(reactions=[dict(reaction_id='acetic_anhydride_hydrolysis', log10_a=5.2, activation_energy_j_mol=46000.0,
                                        orders=[dict(substance_id='acetic_anhydride', order=1.0), dict(substance_id='water', order=0.0)])], accept_priors=True)
REACTIONS = obj(dict(reactions=array(REACTION_ITEM, 0, 6), accept_priors=BOOL))
TASK = obj(dict(end_k=number('K', TJ_DEVICE_MIN_K, TJ_MAX_K), ramp_s=number('s', 0.0, 86400.0)))
TR_TASK = obj(dict(end_k=number('K', TR_RANGE_K[0], TR_RANGE_K[1]), ramp_s=number('s', 0.0, 86400.0), controller=enum(list(TR_CONTROLLER))))
DOSING = obj(dict(substance_id=TEXT, rate_kg_s=number('kg/s', 1e-6, 0.02), total_mass_kg=number('kg', 1e-5, 2.0), feed_temperature_k=nullable(number('K', 253.15, 373.15))))
DOSING_EXAMPLE = dict(substance_id='acetic_anhydride', rate_kg_s=1.5e-4, total_mass_kg=0.102, feed_temperature_k=None)
CAL = obj(dict(pre_s=number('s', 300.0, 3600.0), heat_s=number('s', 300.0, 3600.0), post_s=number('s', 300.0, 3600.0)))
CP = obj(dict(delta_k=number('K', 2.0, 20.0), rate_k_min=number('K/min', 0.1, 2.0), hold_s=number('s', 300.0, 3600.0)))
EVAL = obj(dict(experiment_id=TEXT, start_s=number('s', 0.0), end_s=number('s', 0.0), limiting_reagent_mol=nullable(number('mol', 1e-6, 100.0)),
                heat_capacity_j_k=nullable(number('J/K', 100.0, 50000.0))))
QUALITY_FLAGS = ['ok', 'calibration_not_after_window', 'calibration_not_before_window', 'window_partly_unrecorded', 'emergency_in_window',
                 'thermal_conversion_not_monotonic', 'reaction_incomplete_at_window_end', 'reaction_running_at_window_start', 'boiling_not_modelled']
EVAL_POINT = obj(dict(time_s=number('s'), tr_k=number('K'), q_flow_w=number('W'), q_accu_w=number('W'), q_dos_w=number('W'), q_cal_w=number('W'),
                      q_baseline_w=number('W'), q_r_w=number('W'), thermal_conversion=number('1'), accumulation=number('1'), mtsr_k=number('K')))
EVAL_OUT = obj(dict(experiment_id=TEXT, start_s=number('s'), end_s=number('s'), points=array(EVAL_POINT, 0, 2000), heat_released_j=number('J'),
                    reaction_enthalpy_j_mol=nullable(number('J/mol')), adiabatic_temperature_rise_k=number('K'), max_heat_flow_w=number('W'),
                    time_of_max_heat_flow_s=number('s'), final_thermal_conversion=number('1'), max_accumulation=number('1'), max_mtsr_k=number('K'),
                    ua_basis=enum(['bracketing_calibrations', 'single_calibration']), interpolation=enum(['dosed_mass', 'time', 'none']), heat_capacity_basis=enum(['declared', 'measured_bracketing', 'measured_single']),
                    dosed_mass_kg=number('kg'), quality=array(enum(QUALITY_FLAGS), 1, len(QUALITY_FLAGS))))
POINT = obj(dict(time_s=number('s'), tr_k=nullable(number('K')), tj_k=nullable(number('K')), tc_k=nullable(number('K')), rpm=nullable(number('rpm')),
                 mode=MODE, tr_setpoint_k=nullable(number('K')), tj_setpoint_k=nullable(number('K')), dosed_kg=nullable(number('kg')), dosing_rate_kg_s=number('kg/s'),
                 dosing_feed_k=nullable(number('K')), dosing_substance=nullable(TEXT), cal_on=BOOL, emergency=nullable(PROGRAM)))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'task', 'warning', 'emergency', 'calibration', 'dosing', 'experiment', 'charge', 'discharge', 'safety']),
                 code=TEXT, message_zh=TEXT))
CAL_RESULT = obj(dict(operation_id=TEXT, phase=PHASE, time_s=number('s'), ua_w_k=nullable(number('W/K')), baseline_w=nullable(number('W')),
                      delta_t_base_k=nullable(number('K')), delta_t_heat_k=nullable(number('K')), tr_k=nullable(number('K')), rpm=nullable(number('rpm')),
                      uncertainty_w_k=nullable(number('W/K')), reason=nullable(TEXT)))
CP_RESULT = obj(dict(operation_id=TEXT, phase=PHASE, time_s=number('s'), heat_capacity_j_k=nullable(number('J/K')), ua_w_k=nullable(number('W/K')),
                     tr_start_k=nullable(number('K')), tr_end_k=nullable(number('K')), measured_rate_k_s=nullable(number('K/s')), reason=nullable(TEXT)))
DOSE_RESULT = obj(dict(operation_id=TEXT, phase=PHASE, substance_id=TEXT, dosed_kg=number('kg'), total_mass_kg=number('kg'), reason=nullable(TEXT)))
LEDGER_KEYS = ['heater_j', 'cold_pump_j', 'stirrer_j', 'cal_heater_j', 'reaction_heat_j', 'dosing_sensible_j', 'discharge_sensible_j', 'jacket_loss_j', 'mass_loss_j',
               'coolant_removed_j', 'cold_room_gain_j', 'jacket_to_mass_j', 'valve_exchange_j', 'stored_change_j', 'energy_residual_j',
               'charged_kg', 'dosed_kg', 'discharged_kg', 'reaction_mass_defect_kg', 'mass_kg', 'mass_residual_kg']
LEDGER = obj({k: number('kg' if k.endswith('_kg') else 'J') for k in LEDGER_KEYS})

# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c) if isinstance(c, (list, tuple)) else [c], function_zh=meaning,
                   visibility=visibility, kind=kind, effects=[])


CONFIGURED = obj(dict(configured=BOOL))
OPERATION = obj(dict(operation_id=TEXT))
cap('plant.configure', PLANT, CONFIGURED, PLANT_EXAMPLE, ['column_body', 'coolant_plate', 'cal_heater', 'tr_sensor', 'dip_tube', 'drain_valve_body'],
    '宿主一次声明实验室环境与工程参数：室温、冷却介质（水/乙醇/硅油）进口温度与流量（≥10L/min）、HFCal校准加热器功率先验、Tr/Tj/Tc铂电阻静态偏差（≤A级0.5K）与相关噪声、'
    '底阀放料流量、外部加料单元最大速率（须显式接受）', 'evaluator', 'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), 'button_power',
    '电控柜底部电源开关：通电后泵运行，恒温器以Tj模式把夹套调到Tsafe、搅拌回到断电前的设定（RC1e 5.9：与正常开机相同）；断电时加热、泵与搅拌停止（反应在无冷却下继续，与厂家“断电时反应绝热进行”一致）')
cap('power.toggle', obj({}), obj(dict(enabled=BOOL)), {}, 'button_power', '电源按钮（面板键）：切换通电状态，语义同power.set')
cap('safety.configure', SAFETY, CONFIGURED, SAFETY_EXAMPLE, ['electronics_cabinet', 'touch_display'],
    '反应器安全设置（手册5.2）：Tsafe、Tr min/max、Tj min/max、T diff max（AP01-2玻璃反应器≤50K）、Rsafe（null=保持转速）、Rmin/Rmax（AP01-2 30–850rpm）、Tc max；Tsafe须低于料液泡点−2K（模型不含沸腾）', 'agent', 'configuration')
cap('reactor.charge', CHARGE, obj(dict(charged_kg=number('kg'), volume_m3=number('m^3'))), CHARGE_EXAMPLE, ['reactor_vessel', 'port_stopper', 'reactor_cover'],
    '宿主经ST 45/40大口向反应器加入库中液体（按质量），给出加入温度与料液黏度、导热系数先验（须显式接受）；总体积不得超过AP01-2操作上限2L；混合后温度与当前任务终值须低于新料液泡点−2K', 'evaluator', 'robot_skill')
cap('reaction.configure', REACTIONS, CONFIGURED, REACTION_EXAMPLE, ['reactor_vessel'],
    '宿主为库中反应（反应焓由NIST生成焓导出）给出Arrhenius动力学先验（lg A、Ea、各反应物级数，须显式接受）；库中反应不含动力学', 'evaluator', 'configuration')
cap('experiment.start', obj(dict(name=TEXT)), obj(dict(experiment_id=TEXT)), dict(name='hydrolysis_run_1'), ['touch_display', 'electronics_cabinet'],
    '开始实验（手册5.3）：此后每2s记录Tr/Tj/Tc/转速/设定值/加料/校准加热器与事件，可导出与评价')
cap('experiment.end', obj(dict(end_mode=enum(['keep', 'cool_to']), end_temperature_k=nullable(number('K', TJ_DEVICE_MIN_K, TJ_MAX_K)))), obj(dict(experiment_id=TEXT, points=INT)),
    dict(end_mode='keep', end_temperature_k=None), 'touch_display', '结束实验（手册5.7）：停止记录并保存；end_mode=cool_to时Tj模式跳到给定温度，keep保持当前设定')
cap('thermostat.set_tj', TASK, obj(dict(tj_setpoint_k=number('K'))), dict(end_k=323.15, ramp_s=600.0), ['oil_heater', 'control_valve', 'reactor_jacket'],
    'Tj任务（手册5.4）：Tj模式，夹套温度从当前设定按给定时间线性变化到终值（0=跳变），受Tj min/max限制；终值须低于料液泡点−2K（模型不含沸腾）；低于冷却介质+10K时记录控制质量警告')
cap('thermostat.set_tr', TR_TASK, obj(dict(tr_setpoint_k=number('K'))), dict(end_k=313.15, ramp_s=0.0, controller='aqueous'), ['tr_sensor', 'oil_heater', 'control_valve'],
    'Tr任务（手册5.5）：Tr模式串级控制（RC1e软件默认只用P：AP01水溶液P 9、有机溶液P 4.5；*_pi选项按手册表加I 180s），|Tj−Tr|≤T diff max（冷却中Tr仍上升时不限制，RC1e 9.6）；须反应器内≥0.5L（Tr传感器浸没）且终值低于料液泡点−2K')
cap('stirrer.set', obj(dict(rpm=number('rpm', 0.0, MOTOR_SPEED_MAX_RPM))), obj(dict(setpoint_rpm=number('rpm'))), dict(rpm=200.0), ['stirrer_motor', 'stirrer_shaft', 'stirrer_impeller'],
    '搅拌转速（手册5.6）：0或AP01-2允许的30…Rmax rpm；电机加速到设定值，转矩受玻璃搅拌器0.3N·m与电机1N·m限制')
cap('stirrer.toggle', obj({}), obj(dict(setpoint_rpm=number('rpm'))), {}, 'button_stirrer', '搅拌开关按钮（面板键）：开→上次非零设定值，关→0')
cap('dosing.start', DOSING, OPERATION, DOSING_EXAMPLE, ['dip_tube'],
    '外部加料单元经浸管按恒定质量流率加入库中液体（进料温度null=室温）；完成、停止或急停时结束；总体积不得超过2L，加完后料液泡点−2K须高于当前Tr与任务终值；结果按加料天平0.1g示值', 'agent', 'device_command')
cap('dosing.stop', obj({}), DOSE_RESULT, {}, 'dip_tube', '停止加料：已加入的量保留（结果为0.1g天平示值）；另一个agent启动的加料不能停止')
cap('calibration.run', CAL, OPERATION, dict(pre_s=600.0, heat_s=900.0, post_s=600.0), ['cal_heater', 'tr_sensor', 'reactor_jacket'],
    'HFCal标定：Tr模式稳态下前基线→校准加热器（已声明功率）→后基线，各段后半取平均的(Tr−Tj)，UA=P_cal/(ΔT_heat−ΔT_base)，基线热流=UA·ΔT_base；各段后半(Tr−Tj)的漂移计入不确定度，超过信号5%判为未达稳态而失败；条件改变则失败')
cap('calibration.cp', CP, OPERATION, dict(delta_k=5.0, rate_k_min=0.5, hold_s=600.0), ['tr_sensor', 'reactor_jacket'],
    '比热测定：保持→Tr按给定速率升温ΔT→保持；C_total=UA·[(Tj−Tr)_ramp−平均(Tj−Tr)_hold]/(dTr/dt)_实测（斜坡后半示值Tr的最小二乘斜率；需先有UA标定）；取消或失败时Tr设定停在当前值')
cap('calibration.result', obj(dict(operation_id=TEXT)), {'anyOf': [CAL_RESULT, CP_RESULT]}, dict(operation_id='cal-1'), 'cal_heater', '读取标定操作结果（UA/基线或比热）', 'agent', 'query')
cap('calorimetry.evaluate', EVAL, EVAL_OUT, dict(experiment_id='exp-1', start_s=0.0, end_s=7200.0, limiting_reagent_mol=1.0, heat_capacity_j_k=None),
    ['tr_sensor', 'reactor_jacket', 'cal_heater'],
    '热流量热评价（只用示值）：q_r=UA(Tr−Tj)+C·dTr/dt+ṁcp(Tr−T_feed)−q_cal−q_base（UA、基线与热容在前后标定之间按加料量或时间内插），积分放热、反应焓、绝热温升、热转化率、累积度与MTSR；窗口末端热流未回到基线、窗口开始时已在放热、窗口内达到泡点等给出质量标记', 'agent', 'analysis')
cap('experiment.data', obj(dict(experiment_id=TEXT)), obj(dict(experiment_id=TEXT, name=TEXT, running=BOOL, interval_s=number('s'), points=array(POINT))), dict(experiment_id='exp-1'),
    'touch_display', '导出实验记录（示值，每2s；超过20000点时抽稀并加倍间隔）', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), 'touch_display', '读取事件日志（任务、警告、急停、标定、加料）', 'agent', 'query')
cap('emergency.trigger', obj({}), obj(dict(program=PROGRAM)), {}, 'button_emergency', '急停按钮：触发急停程序A（加热关、控制阀全开、安全阀开、安全继电器断开、切到Tj模式、校准加热器关、加料停止）')
cap('emergency.reset', obj({}), obj(dict(reset=BOOL)), {}, 'touch_display', '复位急停程序：须立即触发的条件（Tr/Tj/Tc限值）已消除；带延时的原因（Tc>Tc max、搅拌停转/低速）复位后重新计时；复位后A或Tj模式保持当前夹套温度，安全继电器闭合')
cap('reactor.discharge', obj(dict(open=BOOL)), obj(dict(target_rad=number('rad'))), dict(open=True), ['drain_valve_body', 'drain_valve_handle'],
    '宿主开/关反应器底阀（手柄有限速度）：打开时料液按已声明的放料流量流出，直到排空', 'evaluator', 'robot_skill')
cap('ledger.read', obj({}), LEDGER, {}, 'column_body', '受限：能量账（加热、泵热、搅拌、校准、反应、加料与放料显热、散热、冷却水带走、储热与残差）与质量账', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, substances=array(TEXT, 0, 80), reactions=array(TEXT, 0, 6))), {}, 'reactor_vessel',
    '受限：反应器中物质与已配置反应所用的库记录', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), 'column_body',
    '唯一宿主时间推进（纳秒整数时钟，余数累计；仪器2s周期在全局时间网格上执行）', 'evaluator', 'simulation')
UNSUPPORTED = {
    'rtcal.enable': '未选RTCal选件（实时量热、RTCal插座不接线）',
    'mode.adiabatic': '本资产不实现绝热模式（RC1e文档提到）',
    'mode.distillation': '未实现蒸馏/回流模式（RC1e 4.3）：无冷凝器与蒸发模型',
    'mode.crystallization': '未实现结晶模式（RC1e 4.4）：无相变模型',
    'stirrer.high_torque': '未选高转矩搅拌电机（1–625rpm、4N·m）',
    'reactor.select_type': '只建模AP01-2玻璃反应器（手册5.1的其他反应器类型未建模）',
    'purge.set': '惰性吹扫（≥80mL/min、≤0.5bar）未进入过程模型',
    'oil.fill_or_change': '导热油加注/更换、油位浮子开关与急停程序B2未建模（油位恒定）',
    'hardware.connect': '无真实USB/以太网/CAN与iControl软件通信',
    'robot.assemble_reactor': '无机器人装反应器、对开法兰、盖与探头的接触验证',
    'accuracy.certification': '无厂家标定、Pt100季度校准或不确定度实验',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, 'electronics_cabinet', v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


slot('clock.time_s', number('s'), 's', 'derived', 'electronics_cabinet', '宿主时钟；read不推进')
for k, c, m in [('plant.configured', 'column_body', '工程参数已声明'), ('power.enabled', 'button_power', '整机通电'),
                ('status.ok', 'lamp_status_ok', '通电且无急停程序（电控柜状态灯绿段）'), ('emergency.active', 'lamp_status_alarm', '急停程序激活（状态灯红段，工程灯语）'),
                ('safety_relay.closed', 'electronics_cabinet', '安全继电器闭合（急停程序A或断电时断开）'), ('thermostat.ramp_active', 'oil_heater', 'Tj或Tr设定值仍在按任务线性变化'),
                ('cal_heater.on', 'cal_heater', '校准加热器通电'), ('calibration.running', 'cal_heater', '有进行中的UA或比热标定'),
                ('dosing.active', 'dip_tube', '加料进行中'), ('experiment.running', 'touch_display', '实验记录进行中'),
                ('experiment.started_by_other_agent', 'touch_display', '当前实验由另一个agent启动：本调用者不能改任务、停止或读取其数据（按调用者计算）'),
                ('drain.open', 'drain_valve_handle', '底阀已打开（手柄开度超过一半）')]:
    slot(k, BOOL, '1', 'derived', c, m)
slot('thermostat.mode', MODE, '1', 'derived', 'oil_heater', '恒温器控制方式：off/tj（夹套温度控制）/tr（反应温度串级控制）；急停程序改变方式时同步')
slot('thermostat.tj_setpoint_k', nullable(number('K')), 'K', 'setpoint', 'oil_heater', '当前夹套温度设定（含斜坡与急停程序的梯度目标；Tr模式下为串级控制器输出）；断电为null')
slot('thermostat.tr_setpoint_k', nullable(number('K')), 'K', 'setpoint', 'tr_sensor', '当前反应温度设定（含斜坡）；非Tr模式为null')
slot('reactor.indicated_k', nullable(number('K')), 'K', 'sensor', 'tr_sensor', 'Tr铂电阻示值（0.01K分辨率，含静态偏差与相关噪声，每2s采样）；断电为null')
slot('jacket.indicated_k', nullable(number('K')), 'K', 'sensor', 'reactor_jacket', 'Tj铂电阻示值（流入夹套的油温；0.01K、含偏差与噪声）；断电为null')
slot('cooling_oil.indicated_k', nullable(number('K')), 'K', 'sensor', 'cooling_coil', 'Tc铂电阻示值（冷油箱温度）；断电为null')
slot('stirrer.setpoint_rpm', number('rpm'), 'rpm', 'setpoint', 'stirrer_motor', '搅拌转速设定（急停时按Rsafe或保持）')
slot('stirrer.indicated_rpm', nullable(number('rpm')), 'rpm', 'sensor', 'stirrer_motor', '搅拌实际转速示值（1rpm）；断电为null')
slot('calorimetry.heat_flow_w', nullable(number('W')), 'W', 'derived', 'reactor_jacket', '在线热流UA_cal·(Tr−Tj)（最近一次UA标定×示值温差，未扣基线）；无标定或断电为null')
slot('calibration.ua_w_k', nullable(number('W/K')), 'W/K', 'derived', 'cal_heater', '最近一次HFCal标定的UA；无标定为null')
slot('calibration.baseline_w', nullable(number('W')), 'W', 'derived', 'cal_heater', '最近一次标定的基线热流UA·ΔT_base（无反应时的表观热流：散热、搅拌功率与传感器偏差之和）')
slot('calibration.heat_capacity_j_k', nullable(number('J/K')), 'J/K', 'derived', 'tr_sensor', '最近一次比热测定得到的反应物料+插件总热容；无测定为null')
slot('dosing.indicated_mass_kg', nullable(number('kg')), 'kg', 'sensor', 'dip_tube', '当前（或最近一次）加料已加入质量（加料天平0.1g示值）；无加料记录为null')
slot('dosing.rate_kg_s', number('kg/s'), 'kg/s', 'setpoint', 'dip_tube', '当前加料速率设定（无加料为0）')
slot('reactor.charged_mass_kg', number('kg'), 'kg', 'derived', 'reactor_vessel', '按操作记录累计的加入−放出质量（加料天平与装料记录，不是称量真值）')
slot('emergency.program', nullable(PROGRAM), '1', 'derived', 'electronics_cabinet', '当前急停程序A/B1/C；无为null')
slot('emergency.cause', nullable(TEXT), '1', 'derived', 'electronics_cabinet', '触发急停的原因代码；无为null')
slot('experiment.id', nullable(TEXT), '1', 'derived', 'touch_display', '当前实验ID；无为null')
for k, sch, u, c, m in [('safety.tsafe_k', number('K'), 'K', 'electronics_cabinet', '安全温度Tsafe（急停程序B1/C的目标）'),
                        ('safety.tr_min_k', number('K'), 'K', 'tr_sensor', 'Tr下限（低于→急停程序C）'), ('safety.tr_max_k', number('K'), 'K', 'tr_sensor', 'Tr上限（高于→急停程序A）'),
                        ('safety.tj_min_k', number('K'), 'K', 'reactor_jacket', 'Tj下限（低于→C）'), ('safety.tj_max_k', number('K'), 'K', 'reactor_jacket', 'Tj上限（高于→A）'),
                        ('safety.tdiff_max_k', number('K'), 'K', 'reactor_jacket', 'Tr模式允许的|Tj−Tr|（AP01-2≤50K）'),
                        ('safety.rsafe_rpm', nullable(number('rpm')), 'rpm', 'stirrer_motor', '急停时的搅拌转速（null=保持）'),
                        ('safety.rmin_rpm', number('rpm'), 'rpm', 'stirrer_motor', 'Rmin（>0时搅拌停转/低于25rpm会触发B1）'),
                        ('safety.rmax_rpm', number('rpm'), 'rpm', 'stirrer_motor', 'Rmax（转速设定上限）'), ('safety.tc_max_k', number('K'), 'K', 'cooling_coil', 'Tc上限（超过3min→A）')]:
    slot(k, sch, u, 'setpoint', c, m)
slot('stirrer.angle_rad', number('rad', 0.0, STIRRER_JOINT_MAX_RAD), 'rad', 'scene', 'stirrer_shaft', '搅拌轴转角（按实际转速积分，模一周）', 'evaluator', '绕+Z，俯视逆时针为正。')
slot('drain.angle_rad', number('rad', 0.0, DRAIN_OPEN_RAD), 'rad', 'scene', 'drain_valve_handle', '底阀手柄开角（0关、π/2开）', 'evaluator', '绕+Y，开启时手柄向下。')
for k, u, c, m in [('truth.reactor_k', 'K', 'reactor_vessel', '反应物料真实温度'), ('truth.jacket_k', 'K', 'reactor_jacket', '热油回路/夹套真实温度'),
                   ('truth.cooling_oil_k', 'K', 'cooling_coil', '冷油箱真实温度'), ('truth.ua_w_k', 'W/K', 'reactor_jacket', '当前真实UA'),
                   ('truth.volume_m3', 'm^3', 'reactor_vessel', '料液体积'), ('truth.mass_kg', 'kg', 'reactor_vessel', '料液质量'),
                   ('truth.heat_capacity_j_k', 'J/K', 'reactor_vessel', '料液+插件热容'), ('truth.reaction_power_w', 'W', 'reactor_vessel', '反应放热功率（按生成焓）'),
                   ('truth.heater_power_w', 'W', 'oil_heater', '油路加热功率'), ('truth.valve_opening', '1', 'control_valve', '控制阀开度'),
                   ('truth.stirrer_power_w', 'W', 'stirrer_impeller', '搅拌输入功率'), ('truth.stirrer_torque_nm', 'N m', 'stirrer_impeller', '搅拌转矩')]:
    slot(k, number(u), u, 'process_truth', c, m, 'evaluator')
slot('truth.bubble_point_k', nullable(number('K')), 'K', 'process_truth', 'reactor_vessel', '料液泡点（Raoult+Clausius–Clapeyron/Trouton估算，模型有效性上限）', 'evaluator')
slot('truth.composition_mol', {'type': 'object', 'additionalProperties': number('mol')}, 'mol', 'process_truth', 'reactor_vessel', '反应器内各物质摩尔数', 'evaluator')


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


def _ramp_value(task, t):
    if task is None:
        return None
    if task['duration'] <= 0.0 or t >= task['t0'] + task['duration']:
        return task['end']
    return task['start'] + (task['end'] - task['start']) * (t - task['t0']) / task['duration']


def _qr_noise(values, half=15):
    """Scatter of a heat-flow series: RMS of its deviations from the centred running mean over +-half points (60 s on the 2 s record;
    robust to the 0.01 K quantisation of the derivative term, which makes a median-based scale collapse at low noise)."""
    n = len(values)
    if n < 5:
        return 0.0
    pref = [0.0]
    for v in values:
        pref.append(pref[-1] + v)
    ss = 0.0
    for i, v in enumerate(values):
        a, b = max(0, i - half), min(n, i + half + 1)
        ss += (v - (pref[b] - pref[a]) / (b - a)) ** 2
    return math.sqrt(ss / n)


def _stage_statistics(xs):
    """Averaged half of a calibration stage, xs = [(Tr - Tj, Tr, t)]: standard error of the mean and the significant drift of (Tr - Tj).
    The Pt100 noise is correlated (and the P-only cascade feeds it back into the jacket), so both standard errors are inflated by the
    lag-1 autocorrelation of the residuals (effective sample size); a least-squares drift across the half counts only when it exceeds
    three of its standard errors (a transient still decaying), otherwise it is noise and 0."""
    n = len(xs)
    y = [x[0] for x in xs]
    t = [x[2] for x in xs]
    ym = sum(y) / n
    if n < 5:
        return dict(se_mean=0.0, drift=0.0)
    tm = sum(t) / n
    sxx = sum((ti - tm) ** 2 for ti in t)
    b = sum((ti - tm) * (yi - ym) for ti, yi in zip(t, y)) / sxx if sxx > 0.0 else 0.0
    res = [yi - ym - b * (ti - tm) for ti, yi in zip(t, y)]
    ss = sum(r * r for r in res)
    rho = min(0.95, max(0.0, sum(res[i] * res[i + 1] for i in range(n - 1)) / ss)) if ss > 0.0 else 0.0
    infl = math.sqrt((1.0 + rho) / (1.0 - rho))
    s2 = ss / (n - 2)
    span = t[-1] - t[0]
    drift, se_drift = b * span, (math.sqrt(s2 / sxx) * span * infl if sxx > 0.0 else 0.0)
    return dict(se_mean=math.sqrt(sum((yi - ym) ** 2 for yi in y) / (n - 1) / n) * infl, drift=abs(drift) if abs(drift) > 3.0 * se_drift else 0.0)


class MtRc1mxhfcalRuntime:
    """RC1mx + HFCal with the AP01-2 reactor: process model, instrument logic and HFCal evaluation (SI units)."""

    def __init__(self, instance_id='rc1mx_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.fit = dict(PLANT_FIT)
        self.T = [T_REF, T_REF, T_REF]
        self.n = {}
        self.priors = dict(mu=WATER_REF['mu'], k=WATER_REF['k'])
        self.reaction_cfg = {}
        self.power = False
        self.mode = 'off'
        self.tj_task = None
        self.tr_task = None
        self.tj_set = None
        self.tr_set = None
        self.emergency = None
        self.relay_closed = True
        self.heater_w, self.valve_u, self.safety_valve = 0.0, 0.0, False
        self.tj_ctrl = TjController(self.fit)
        self.tr_ctrl = TrController(*TR_CONTROLLER['aqueous'])
        self.stir_set, self.stir_last, self.stir_rpm, self.stir_angle = 0.0, 0.0, 0.0, 0.0
        self.stir_torque, self.stir_power = 0.0, 0.0
        self.cal_on = False
        self.dosing = None
        self.dose_log = []
        self.drain_target, self.drain_angle = 0.0, 0.0
        self.safety = dict(SAFETY_EXAMPLE)
        self.noise = [0.0, 0.0, 0.0]
        self.rng = random.Random(0)
        self.meas = None
        self.experiment = None
        self.records = {}
        self.events, self.events_dropped = [], 0
        self.calibrations, self.cp_results = [], []
        self.operations, self.op_seq = {}, 0
        self.cal_op, self.cp_op = None, None
        self.charged_kg = 0.0
        self.dosed_total = 0.0
        self.led = {k: _Sum() for k in LEDGER_KEYS if k not in ('stored_change_j', 'energy_residual_j', 'mass_kg', 'mass_residual_kg')}
        self._E0 = None
        self._m0 = 0.0
        self._tc_high_since = None
        self._stir_low_since = None
        self._stir_off_since = None
        self._stir_off_manual = True                       # stirrer at 0 because of an operator command (RC1e 5.6 rule), not an Rsafe stop
        self._stir_at_power_off = 0.0                      # last stirrer set value, restored at power-up (RC1e 5.9)
        self._tr_hist = []                                 # (time, indicated Tr) of the last cycles: dTr/dt for the T diff release
        self._boiling, self._boil_log = False, []          # bubble-point crossings [start, end] (the model has no boiling)
        self._reaction_power = 0.0
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
        return MtRc1mxhfcalSession(self, name)

    def _event(self, kind, code, message):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.time_s, kind=kind, code=code, message_zh=message))

    def _props(self):
        return mixture_properties(self.n, self.priors)

    def _C_r(self, props=None):
        return (props or self._props())['heat_capacity'] + self.fit['C_INS']

    def _ua(self, props=None):
        props = props or self._props()
        return overall_ua(props['volume'], self.stir_rpm, props)

    def _stored_energy(self):
        return self._C_r() * (self.T[0] - T_REF) + self.fit['C_J'] * (self.T[1] - T_REF) + self.fit['C_C'] * (self.T[2] - T_REF)

    def _coolant_H(self):
        p = self.plant
        return coolant_conductance(p['coolant_flow_m3_s'], COOLANTS[p['coolant_kind']], self.fit['UA_COIL'])

    def _new_op(self, kind, owner_hint=None, **data):
        self.op_seq += 1
        op = dict(operation_id=f'{kind}-{self.op_seq}', kind=kind, phase='running', t0=self.time_s, reason=None, **data)
        self.operations[op['operation_id']] = op
        return op

    def _cal_valid_for(self, op):
        """Conditions of a running calibration: Tr mode without ramp, stirring, no dosing, drain closed, no emergency."""
        return (self.power and self.emergency is None and self.mode == 'tr' and not self._ramping() and self.dosing is None and self.drain_angle == 0.0
                and self.stir_set >= AP01_SPEED_RANGE_RPM[0] and abs(self.stir_set - op['rpm']) < 1e-9)

    def _ramping(self):
        t = self.time_s
        task = self.tr_task if self.mode == 'tr' else self.tj_task
        return task is not None and task['duration'] > 0.0 and t < task['t0'] + task['duration']

    # ---------------------------------------------------------------- read
    def _latest_cal(self):
        return self.calibrations[-1] if self.calibrations else None

    def read(self, visibility='agent'):
        m = self.meas if self.power else None
        cal = self._latest_cal()
        props = self._props()
        values = {
            'clock.time_s': self.time_s, 'plant.configured': self.plant is not None, 'power.enabled': self.power,
            'status.ok': self.power and self.emergency is None, 'emergency.active': self.emergency is not None, 'safety_relay.closed': self.relay_closed,
            'thermostat.ramp_active': self.power and self._ramping(), 'cal_heater.on': self.cal_on, 'calibration.running': self.cal_op is not None or self.cp_op is not None,
            'dosing.active': self.dosing is not None, 'experiment.running': self.experiment is not None, 'experiment.started_by_other_agent': False,
            'drain.open': self.drain_angle >= DRAIN_OPEN_RAD / 2,
            'thermostat.mode': self.mode, 'thermostat.tj_setpoint_k': self.tj_set if self.power else None,
            'thermostat.tr_setpoint_k': self.tr_set if self.power and self.mode == 'tr' else None,
            'reactor.indicated_k': m['tr'] if m else None, 'jacket.indicated_k': m['tj'] if m else None, 'cooling_oil.indicated_k': m['tc'] if m else None,
            'stirrer.setpoint_rpm': self.stir_set, 'stirrer.indicated_rpm': m['rpm'] if m else None,
            'calorimetry.heat_flow_w': (cal['ua_w_k'] * (m['tr'] - m['tj'])) if (m and cal) else None,
            'calibration.ua_w_k': cal['ua_w_k'] if cal else None, 'calibration.baseline_w': cal['baseline_w'] if cal else None,
            'calibration.heat_capacity_j_k': self.cp_results[-1]['heat_capacity_j_k'] if self.cp_results else None,
            'dosing.indicated_mass_kg': quantize(self.dosing['dosed'], DOSING_RESOLUTION_KG) if self.dosing else (self.dose_log[-1]['indicated'] if self.dose_log else None),
            'dosing.rate_kg_s': self.dosing['rate'] if self.dosing else 0.0, 'reactor.charged_mass_kg': self.charged_kg,
            'emergency.program': self.emergency['program'] if self.emergency else None, 'emergency.cause': self.emergency['cause'] if self.emergency else None,
            'experiment.id': self.experiment['id'] if self.experiment else None,
            'safety.tsafe_k': self.safety['tsafe_k'], 'safety.tr_min_k': self.safety['tr_min_k'], 'safety.tr_max_k': self.safety['tr_max_k'],
            'safety.tj_min_k': self.safety['tj_min_k'], 'safety.tj_max_k': self.safety['tj_max_k'], 'safety.tdiff_max_k': self.safety['tdiff_max_k'],
            'safety.rsafe_rpm': self.safety['rsafe_rpm'], 'safety.rmin_rpm': self.safety['rmin_rpm'], 'safety.rmax_rpm': self.safety['rmax_rpm'],
            'safety.tc_max_k': self.safety['tc_max_k'],
            'stirrer.angle_rad': self.stir_angle, 'drain.angle_rad': self.drain_angle,
            'truth.reactor_k': self.T[0], 'truth.jacket_k': self.T[1], 'truth.cooling_oil_k': self.T[2], 'truth.ua_w_k': self._ua(props),
            'truth.volume_m3': props['volume'], 'truth.mass_kg': props['mass'], 'truth.heat_capacity_j_k': self._C_r(props),
            'truth.reaction_power_w': self._reaction_power, 'truth.heater_power_w': self.heater_w if self.power else 0.0,
            'truth.valve_opening': self.valve_u if self.power else 0.0, 'truth.stirrer_power_w': self.stir_power, 'truth.stirrer_torque_nm': self.stir_torque,
            'truth.bubble_point_k': bubble_point_k(self.n), 'truth.composition_mol': dict(self.n),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def ledger(self):
        led = {k: v.value for k, v in self.led.items()}
        stored = (self._stored_energy() - self._E0) if self._E0 is not None else 0.0
        inputs = (led['heater_j'] + led['cold_pump_j'] + led['stirrer_j'] + led['cal_heater_j'] + led['reaction_heat_j'] + led['dosing_sensible_j']
                  - led['discharge_sensible_j'] - led['jacket_loss_j'] - led['mass_loss_j'] - led['coolant_removed_j'] + led['cold_room_gain_j'])
        mass = self._props()['mass']
        led.update(stored_change_j=stored, energy_residual_j=stored - inputs, mass_kg=mass,
                   mass_residual_kg=mass - (led['charged_kg'] + led['dosed_kg'] - led['discharged_kg'] + led['reaction_mass_defect_kg']))
        return led

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**a)
        self.revision += 1
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _need_no_emergency(self):
        if self.emergency is not None:
            raise ActionRejected('EMERGENCY_ACTIVE')

    def _cmd_plant_configure(self, **a):
        if self.plant is not None:
            raise ActionRejected('PLANT_ALREADY_CONFIGURED')
        if not a['accept_engineering']:
            raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
        if a['coolant_kind'] == 'water' and a['coolant_inlet_k'] < 274.15:
            raise ActionRejected('COOLANT_OUT_OF_RANGE')
        self.plant = dict(a, noise_seed=int(a['noise_seed']))
        self.rng = random.Random(int(a['noise_seed']))
        self.T = [a['ambient_k'], a['ambient_k'], a['ambient_k']]
        self.safety = dict(SAFETY_EXAMPLE)
        self._E0 = self._stored_energy()
        self._event('power', 'PLANT_CONFIGURED', '工程参数已声明')
        return dict(configured=True)

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            self.power = True
            self.mode = 'tj'
            self.tr_task, self.tr_set = None, None
            self.tj_ctrl.reset()
            self.relay_closed, self.safety_valve = True, False
            self.noise = [0.0, 0.0, 0.0]
            self._measure()
            self._tr_hist = [(self.time_s, self.meas['tr'])]
            lo, hi = max(self.safety['tj_min_k'], TJ_DEVICE_MIN_K), min(self.safety['tj_max_k'], TJ_MAX_K)
            target = min(max(self.safety['tsafe_k'], lo), hi)             # RC1e 5.9: the jacket is cooled/heated to the last Tsafe
            self.tj_task = dict(start=target, end=target, t0=self.time_s, duration=0.0)
            self.tj_set = target
            self.stir_set = self._stir_at_power_off                       # RC1e 5.9: stirrer back to the last defined set value
            self._event('power', 'POWER_ON', f'通电：泵运行，Tj模式把夹套调到Tsafe {target - 273.15:.2f}°C，搅拌回到断电前设定{self.stir_set:.0f}rpm（RC1e 5.9）')
        elif not enabled and self.power:
            self._power_off('POWER_OFF')
        return dict(enabled=self.power)

    def _power_off(self, code):
        self._stir_at_power_off = self.stir_set
        self.power = False
        self.mode = 'off'
        self.tj_task = self.tr_task = None
        self.tj_set = self.tr_set = None
        self.heater_w, self.valve_u, self.safety_valve = 0.0, 0.0, False
        self.stir_set = 0.0
        self.cal_on = False
        self.relay_closed = False
        for op in (self.cal_op, self.cp_op):
            if op is not None:
                self._finish_op(op, 'failed', code)
        self.cal_op = self.cp_op = None
        if self.dosing is not None:
            self._stop_dosing('cancelled', code)
        self.meas = None
        self._tr_hist = []
        if self.emergency is not None:
            self._event('emergency', 'EMERGENCY_ENDED_BY_POWER_OFF', f'断电结束急停程序{self.emergency["program"]}')
            self.emergency = None
        self._tc_high_since = self._stir_low_since = self._stir_off_since = None
        self._event('power', code, '断电：加热、泵、搅拌与校准加热器停止；反应在无冷却下继续')

    def _cmd_power_toggle(self):
        return self._cmd_power_set(not self.power)

    def _cmd_safety_configure(self, **a):
        self._need_plant()
        self._need_no_emergency()
        if not (a['tr_min_k'] < a['tr_max_k'] and a['tj_max_k'] - a['tj_min_k'] >= 2.0 * TJ_CASCADE_MARGIN_K + 1.0 and a['tr_min_k'] <= a['tsafe_k'] <= a['tr_max_k']
                and a['rmin_rpm'] <= a['rmax_rpm']
                and (a['rsafe_rpm'] is None or a['rsafe_rpm'] == 0.0 or AP01_SPEED_RANGE_RPM[0] <= a['rsafe_rpm'] <= a['rmax_rpm'])):
            raise ActionRejected('INVALID_SAFETY_SETTINGS')
        if self.power and (self.meas['tr'] > a['tr_max_k'] or self.meas['tj'] > a['tj_max_k'] or self.meas['tr'] < a['tr_min_k'] or self.meas['tj'] < a['tj_min_k']):
            raise ActionRejected('LIMIT_VIOLATED_NOW')
        self._check_below_bubble_point(a['tsafe_k'])                         # power-up and programs B1/C drive the mass towards Tsafe
        if self.stir_set > a['rmax_rpm']:
            raise ActionRejected('STIRRER_ABOVE_RMAX')
        self.safety = dict(a)
        self._event('safety', 'SAFETY_CONFIGURED', '安全设置已更新')
        return dict(configured=True)

    def _cmd_reactor_charge(self, substances, temperature_k, viscosity_pa_s, thermal_conductivity_w_m_k, accept_priors):
        self._need_plant()
        if not accept_priors:
            raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
        subs = _lib()['substances']
        add = {}
        for it in substances:
            s = subs.get(it['substance_id'])
            if s is None:
                raise ActionRejected('UNKNOWN_SUBSTANCE')
            if (s['tm'] and temperature_k <= s['tm']) or (s['tb'] and temperature_k >= s['tb']):
                raise ActionRejected('NOT_LIQUID')
            add[it['substance_id']] = add.get(it['substance_id'], 0.0) + it['mass_kg'] / s['M']
        if self.drain_angle > 0.0 or self.drain_target > 0.0:
            raise ActionRejected('DRAIN_OPEN')
        if self.cal_op is not None or self.cp_op is not None:
            raise ActionRejected('CALIBRATION_RUNNING')
        new = dict(self.n)
        for k, v in add.items():
            new[k] = new.get(k, 0.0) + v
        if mixture_properties(new, self.priors)['volume'] > OPERATING_VOLUME_M3[1] * (1 + 1e-9):
            raise ActionRejected('VOLUME_ABOVE_OPERATING_RANGE')
        c_add = sum(v * subs[k]['cp_mol'] for k, v in add.items())
        C_new = mixture_properties(new, self.priors)['heat_capacity'] + self.fit['C_INS']
        t_mix = T_REF + (self._C_r() * (self.T[0] - T_REF) + c_add * (temperature_k - T_REF)) / C_new
        self._check_below_bubble_point(max(t_mix, self._task_target_k() or -math.inf, self.safety['tsafe_k']), new)
        mass = sum(it['mass_kg'] for it in substances)
        self._mix_in(add, temperature_k, 'charge')
        self.priors = dict(mu=viscosity_pa_s, k=thermal_conductivity_w_m_k)
        self.charged_kg += mass
        self.led['charged_kg'].add(mass)
        self._event('charge', 'CHARGED', f'装料{mass * 1e3:.1f}g（{", ".join(sorted(add))}），{temperature_k - 273.15:.1f}°C')
        return dict(charged_kg=mass, volume_m3=self._props()['volume'])

    def _mix_in(self, add_mol, T_feed, kind):
        """Adiabatic mixing of fed liquid (sensible enthalpy relative to 298.15 K) into the reaction mass."""
        subs = _lib()['substances']
        C_old = self._C_r()
        sens = C_old * (self.T[0] - T_REF) + sum(v * subs[k]['cp_mol'] for k, v in add_mol.items()) * (T_feed - T_REF)
        for k, v in add_mol.items():
            self.n[k] = self.n.get(k, 0.0) + v
        self.T[0] = T_REF + sens / self._C_r()
        booked = sum(v * subs[k]['cp_mol'] for k, v in add_mol.items()) * (T_feed - T_REF)
        self.led['dosing_sensible_j'].add(booked)

    def _cmd_reaction_configure(self, reactions, accept_priors):
        self._need_plant()
        if not accept_priors:
            raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
        lib = _lib()['reactions']
        cfg = {}
        for r in reactions:
            rx = lib.get(r['reaction_id'])
            if rx is None:
                raise ActionRejected('UNKNOWN_REACTION')
            orders = {o['substance_id']: o['order'] for o in r['orders']}
            if set(orders) != set(rx['reactants']) or len(orders) != len(r['orders']) or r['reaction_id'] in cfg:
                raise ActionRejected('INVALID_KINETICS')
            cfg[r['reaction_id']] = dict(log10_a=r['log10_a'], ea=r['activation_energy_j_mol'], orders=orders)
        self.reaction_cfg = cfg
        return dict(configured=True)

    def _cmd_experiment_start(self, name):
        self._need_power()
        if self.experiment is not None:
            raise ActionRejected('EXPERIMENT_RUNNING')
        if not name.strip():
            raise ActionRejected('INVALID_ARGUMENT')
        self.op_seq += 1
        eid = f'exp-{self.op_seq}'
        self.experiment = dict(id=eid, name=name, t0=self.time_s, interval=CYCLE_S, points=[], next_t=self.time_s)
        self._event('experiment', 'EXPERIMENT_STARTED', f'实验{name}开始')
        self._record_point(force=True)
        return dict(experiment_id=eid)

    def _cmd_experiment_end(self, end_mode, end_temperature_k):
        if self.experiment is None:
            raise ActionRejected('NO_EXPERIMENT')
        if end_mode == 'cool_to':
            if end_temperature_k is None:
                raise ActionRejected('INVALID_ARGUMENT')
            self._need_power()
            self._need_no_emergency()
            self._check_tj_limits(end_temperature_k)
            self._check_below_bubble_point(end_temperature_k)
        elif end_temperature_k is not None:
            raise ActionRejected('INVALID_ARGUMENT')
        e = self.experiment
        self._record_point(force=True)
        self._close_experiment()
        if end_mode == 'cool_to':
            self._start_tj_task(end_temperature_k, 0.0)
        return dict(experiment_id=e['id'], points=len(e['points']))

    def _close_experiment(self):
        e = self.experiment
        self.experiment = None
        e['running'] = False
        e['t1'] = self.time_s
        self.records[e['id']] = e
        if len(self.records) > MAX_RECORDS:
            self.records.pop(next(iter(self.records)))
        self._event('experiment', 'EXPERIMENT_ENDED', f'实验{e["name"]}结束')

    def _check_tj_limits(self, end_k):
        if not (max(self.safety['tj_min_k'], TJ_DEVICE_MIN_K) <= end_k <= min(self.safety['tj_max_k'], TJ_MAX_K)):
            raise ActionRejected('OUTSIDE_SAFETY_LIMITS')

    def _check_below_bubble_point(self, temperature_k, n=None):
        """Model validity: no boiling/reflux, so a target (or the state after adding material) must stay 2 K below the bubble point."""
        bp = bubble_point_k(self.n if n is None else n)
        if bp is not None and temperature_k > bp - 2.0:
            raise ActionRejected('ABOVE_BUBBLE_POINT')

    def _task_target_k(self):
        """Temperature the active thermostat task drives the mass to (task end value); None without a task."""
        if not self.power:
            return None
        task = self.tr_task if self.mode == 'tr' else self.tj_task
        return task['end'] if task is not None else None

    def _start_tj_task(self, end_k, ramp_s):
        start = self.tj_set if self.tj_set is not None else self.T[1]
        if self.mode != 'tj':
            start = self.meas['tj'] if self.meas else self.T[1]
            self.tj_ctrl.reset()
        self.mode = 'tj'
        self.tr_task, self.tr_set = None, None
        self.tj_task = dict(start=start, end=end_k, t0=self.time_s, duration=ramp_s)
        self.tj_set = _ramp_value(self.tj_task, self.time_s)
        if end_k < self.plant['coolant_inlet_k'] + COOLANT_MARGIN_K:
            self._event('warning', 'TJ_BELOW_COOLANT_MARGIN', 'Tj设定低于冷却介质温度+10K：控制质量下降（RC1e 9.4）')
        if end_k < TJ_VISCOSITY_MIN_K:
            self._event('warning', 'TJ_BELOW_OIL_VISCOSITY_LIMIT', 'Tj设定低于MidTemp油黏度限−45°C（设备限−50°C，需低温槽；RC1mx 7.2）')
        self._event('task', 'TJ_TASK', f'Tj任务：{end_k - 273.15:.2f}°C，斜坡{ramp_s:.0f}s')

    def _invalidate_cal_ops(self, reason):
        if self.cp_op is not None:
            self._hold_cp_ramp(self.cp_op)
        for op in (self.cal_op, self.cp_op):
            if op is not None:
                self._finish_op(op, 'failed', reason)
        self.cal_op = self.cp_op = None
        self.cal_on = False

    def _cmd_thermostat_set_tj(self, end_k, ramp_s):
        self._need_power()
        self._need_no_emergency()
        self._check_tj_limits(end_k)
        self._check_below_bubble_point(end_k)
        self._invalidate_cal_ops('CONDITIONS_CHANGED')
        self._start_tj_task(end_k, ramp_s)
        return dict(tj_setpoint_k=self.tj_set)

    def _cmd_thermostat_set_tr(self, end_k, ramp_s, controller):
        self._need_power()
        self._need_no_emergency()
        if not self.safety['tr_min_k'] <= end_k <= self.safety['tr_max_k']:
            raise ActionRejected('OUTSIDE_SAFETY_LIMITS')
        if self._props()['volume'] < OPERATING_VOLUME_M3[0] * (1 - 1e-9):
            raise ActionRejected('VOLUME_BELOW_OPERATING_RANGE')
        self._check_below_bubble_point(end_k)
        self._invalidate_cal_ops('CONDITIONS_CHANGED')
        self._set_tr_task(end_k, ramp_s, controller)
        return dict(tr_setpoint_k=self.tr_set)

    def _set_tr_task(self, end_k, ramp_s, controller, event=True):
        start = self.tr_set if (self.mode == 'tr' and self.tr_set is not None) else self.meas['tr']
        if self.mode != 'tr':
            self.tr_ctrl = TrController(*TR_CONTROLLER[controller])
        else:
            self.tr_ctrl.p, self.tr_ctrl.i_s = TR_CONTROLLER[controller]
        self.mode = 'tr'
        self.tj_task = None
        self.tr_task = dict(start=start, end=end_k, t0=self.time_s, duration=ramp_s, controller=controller)
        self.tr_set = _ramp_value(self.tr_task, self.time_s)
        if event:
            self._event('task', 'TR_TASK', f'Tr任务：{end_k - 273.15:.2f}°C，斜坡{ramp_s:.0f}s，{controller}参数')

    def _cmd_stirrer_set(self, rpm):
        self._need_power()
        self._need_no_emergency()
        if rpm != 0.0 and not (AP01_SPEED_RANGE_RPM[0] <= rpm <= self.safety['rmax_rpm']):
            raise ActionRejected('OUTSIDE_STIRRER_RANGE')
        return self._set_stirrer(rpm)

    def _set_stirrer(self, rpm):
        if rpm != self.stir_set:
            if self.cal_op is not None or self.cp_op is not None:
                self._invalidate_cal_ops('CONDITIONS_CHANGED')
            self._event('task', 'STIRRER', f'搅拌设定{rpm:.0f}rpm')
        if rpm > 0.0:
            self.stir_last = rpm
        else:
            self._stir_off_manual = True                                  # operator switched the stirrer off (RC1e 5.6 rule)
        self.stir_set = rpm
        return dict(setpoint_rpm=self.stir_set)

    def _cmd_stirrer_toggle(self):
        self._need_power()
        self._need_no_emergency()
        if self.stir_set > 0.0:
            return self._set_stirrer(0.0)
        if self.stir_last <= 0.0:
            raise ActionRejected('STIRRER_SETPOINT_REQUIRED')
        if self.stir_last > self.safety['rmax_rpm']:
            raise ActionRejected('OUTSIDE_STIRRER_RANGE')
        return self._set_stirrer(self.stir_last)

    def _cmd_dosing_start(self, substance_id, rate_kg_s, total_mass_kg, feed_temperature_k):
        self._need_power()
        self._need_no_emergency()
        if self.dosing is not None:
            raise ActionRejected('DOSING_ACTIVE')
        s = _lib()['substances'].get(substance_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SUBSTANCE')
        feed = self.plant['ambient_k'] if feed_temperature_k is None else feed_temperature_k
        if (s['tm'] and feed <= s['tm']) or (s['tb'] and feed >= s['tb']):
            raise ActionRejected('NOT_LIQUID')
        if rate_kg_s > self.plant['dosing_max_rate_kg_s']:
            raise ActionRejected('RATE_ABOVE_DOSING_UNIT')
        new = dict(self.n)
        new[substance_id] = new.get(substance_id, 0.0) + total_mass_kg / s['M']
        if mixture_properties(new, self.priors)['volume'] > OPERATING_VOLUME_M3[1] * (1 + 1e-9):
            raise ActionRejected('VOLUME_ABOVE_OPERATING_RANGE')
        self._check_below_bubble_point(max(self.meas['tr'], self._task_target_k() or -math.inf, self.safety['tsafe_k']), new)
        if self.drain_angle > 0.0 or self.drain_target > 0.0:
            raise ActionRejected('DRAIN_OPEN')
        self._invalidate_cal_ops('CONDITIONS_CHANGED')
        op = self._new_op('dose', substance_id=substance_id, total=total_mass_kg)
        self.dosing = dict(op=op, substance=substance_id, rate=rate_kg_s, total=total_mass_kg, dosed=0.0, feed=feed)
        self._event('dosing', 'DOSING_STARTED', f'加料开始：{substance_id} {rate_kg_s * 1e3:.3f}g/s，共{total_mass_kg * 1e3:.1f}g')
        return dict(operation_id=op['operation_id'])

    def _stop_dosing(self, phase, reason):
        d = self.dosing
        self.dosing = None
        d['op'].update(dosed=d['dosed'])
        self._finish_op(d['op'], phase, reason)
        self.dose_log.append(dict(substance=d['substance'], dosed=d['dosed'], indicated=quantize(d['dosed'], DOSING_RESOLUTION_KG), t1=self.time_s))
        self._event('dosing', 'DOSING_' + phase.upper(), f'加料结束（{phase}）：{quantize(d["dosed"], DOSING_RESOLUTION_KG) * 1e3:.1f}g')

    def _cmd_dosing_stop(self):
        if self.dosing is None:
            raise ActionRejected('NOT_DOSING')
        op = self.dosing['op']
        self._stop_dosing('cancelled', 'USER_CANCEL')
        return self._dose_result(op)

    def _dose_result(self, op):
        """Agent-visible dose record: the 0.1 g balance reading of the mass dosed so far (running) or in total (finished)."""
        dosed = self.dosing['dosed'] if (self.dosing is not None and self.dosing['op'] is op) else op.get('dosed', 0.0)
        return dict(operation_id=op['operation_id'], phase=op['phase'], substance_id=op['substance_id'], dosed_kg=quantize(dosed, DOSING_RESOLUTION_KG),
                    total_mass_kg=op['total'], reason=op['reason'])

    def _check_calibration_ready(self):
        self._need_power()
        self._need_no_emergency()
        if self.cal_op is not None or self.cp_op is not None:
            raise ActionRejected('BUSY')
        if self.mode != 'tr' or self._ramping():
            raise ActionRejected('TR_MODE_REQUIRED')
        if self._props()['volume'] < OPERATING_VOLUME_M3[0] * (1 - 1e-9):
            raise ActionRejected('VOLUME_BELOW_OPERATING_RANGE')
        if self.stir_set < AP01_SPEED_RANGE_RPM[0]:
            raise ActionRejected('STIRRER_REQUIRED')
        if self.dosing is not None:
            raise ActionRejected('DOSING_ACTIVE')
        if self.drain_angle > 0.0 or self.drain_target > 0.0:
            raise ActionRejected('DRAIN_OPEN')

    def _cmd_calibration_run(self, pre_s, heat_s, post_s):
        self._check_calibration_ready()
        op = self._new_op('cal', rpm=self.stir_set, stage='pre', stage_t0=self.time_s, durations=dict(pre=pre_s, heat=heat_s, post=post_s), sums={}, samples=[])
        self.cal_op = op
        self._event('calibration', 'CALIBRATION_STARTED', 'HFCal标定开始：前基线')
        return dict(operation_id=op['operation_id'])

    def _cmd_calibration_result(self, operation_id):
        op = self.operations.get(operation_id)
        if op is None or op['kind'] not in ('cal', 'cp'):
            raise ActionRejected('UNKNOWN_OPERATION')
        return self._cal_result(op)

    def _cal_result(self, op):
        if op['kind'] == 'cal':
            r = op.get('result') or {}
            return dict(operation_id=op['operation_id'], phase=op['phase'], time_s=op.get('t1', self.time_s), ua_w_k=r.get('ua_w_k'), baseline_w=r.get('baseline_w'),
                        delta_t_base_k=r.get('delta_t_base_k'), delta_t_heat_k=r.get('delta_t_heat_k'), tr_k=r.get('tr_k'), rpm=op['rpm'],
                        uncertainty_w_k=r.get('uncertainty_w_k'), reason=op['reason'])
        r = op.get('result') or {}
        return dict(operation_id=op['operation_id'], phase=op['phase'], time_s=op.get('t1', self.time_s), heat_capacity_j_k=r.get('heat_capacity_j_k'),
                    ua_w_k=r.get('ua_w_k'), tr_start_k=r.get('tr_start_k'), tr_end_k=r.get('tr_end_k'), measured_rate_k_s=r.get('measured_rate_k_s'), reason=op['reason'])

    def _hold_cp_ramp(self, op):
        """A heat-capacity ramp belongs to its operation: when the operation ends early the Tr set point stays where it is."""
        task = op.get('task')
        if task is not None and self.power and self.mode == 'tr' and self.tr_task is task and self._ramping():
            hold = self.tr_set
            self.tr_task = dict(start=hold, end=hold, t0=self.time_s, duration=0.0, controller=task['controller'])
            self._event('task', 'CP_RAMP_HELD', f'比热测定终止：Tr设定停在{hold - 273.15:.2f}°C')

    def _cancel_operation(self, op):
        """Session.cancel: dosing stops (dosed mass kept); a calibration ends without a result; a cp ramp stops where it is."""
        if op['kind'] == 'dose':
            self._stop_dosing('cancelled', 'USER_CANCEL')
            return
        if self.cp_op is op:
            self._hold_cp_ramp(op)
            self.cp_op = None
        self._finish_op(op, 'cancelled', 'USER_CANCEL')
        if self.cal_op is op:
            self.cal_op, self.cal_on = None, False

    def _finish_op(self, op, phase, reason=None):
        op['phase'] = phase
        op['reason'] = reason
        op['t1'] = self.time_s

    def _cmd_emergency_trigger(self):
        self._need_power()
        self._enter_emergency('A', 'EMERGENCY_BUTTON')
        return dict(program=self.emergency['program'])

    def _cmd_emergency_reset(self):
        self._need_power()
        if self.emergency is None:
            raise ActionRejected('NO_EMERGENCY')
        if [c for c in self._active_causes() if c[1] not in TIMED_CAUSES]:
            raise ActionRejected('CAUSE_PERSISTS')
        prog = self.emergency['program']
        self.emergency = None
        self.relay_closed = True
        self.safety_valve = False
        if prog == 'A' or self.mode != 'tr':
            self.mode = 'tj'
            self.tr_task, self.tr_set = None, None
            hold = self.meas['tj']
            self.tj_task = dict(start=hold, end=hold, t0=self.time_s, duration=0.0)
            self.tj_set = hold
            self.tj_ctrl.reset()
        self._restart_delay_timers()
        self._event('emergency', 'EMERGENCY_RESET', f'急停程序{prog}已复位')
        return dict(reset=True)

    def _restart_delay_timers(self):
        """After a reset the delayed causes start their delay again from the current state (RC1e 5.4: warnings precede programs)."""
        m, sf, t = self.meas, self.safety, self.time_s
        self._tc_high_since = t if m['tc'] > sf['tc_max_k'] else None
        watch = sf['rmin_rpm'] > 0.0
        self._stir_off_since = t if (watch and self.stir_set == 0.0 and self._stir_off_manual) else None
        self._stir_low_since = t if (watch and self.stir_set > 0.0 and m['rpm'] < STIRRER_MIN_RPM_B1) else None
        if self._tc_high_since is not None:
            self._event('warning', 'TC_ABOVE_MAX', 'Tc仍高于Tc max：3min内未消除将再次触发急停程序A')
        if self._stir_off_since is not None:
            self._event('warning', 'STIRRER_OFF', '搅拌仍关闭（Rmin>0）：3min内未恢复将再次触发B1')

    def _cmd_reactor_discharge(self, open):
        self._need_plant()
        if open and self.dosing is not None:
            raise ActionRejected('DOSING_ACTIVE')
        self.drain_target = DRAIN_OPEN_RAD if open else 0.0
        if open:
            self._invalidate_cal_ops('CONDITIONS_CHANGED')
        self._event('discharge', 'DRAIN_OPEN' if open else 'DRAIN_CLOSE', '底阀' + ('打开' if open else '关闭'))
        return dict(target_rad=self.drain_target)

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        return dict(library=_lib()['source'], substances=sorted(self.n), reactions=sorted(self.reaction_cfg))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.time_s)

    def _cmd_calibration_cp(self, delta_k, rate_k_min, hold_s):
        self._check_calibration_ready()
        if not self.calibrations:
            raise ActionRejected('UA_CALIBRATION_REQUIRED')
        end = self.tr_set + delta_k
        bp = bubble_point_k(self.n)
        if end > self.safety['tr_max_k'] or (bp is not None and end > bp - 2.0):
            raise ActionRejected('OUTSIDE_SAFETY_LIMITS' if end > self.safety['tr_max_k'] else 'ABOVE_BUBBLE_POINT')
        op = self._new_op('cp', rpm=self.stir_set, stage='hold1', stage_t0=self.time_s, hold=hold_s, delta=delta_k, rate=rate_k_min / 60.0,
                          controller=self.tr_task['controller'], tr_start=self.tr_set, samples=[])
        self.cp_op = op
        self._event('calibration', 'CP_STARTED', f'比热测定开始：保持{hold_s:.0f}s，随后以{rate_k_min:.2f}K/min升温{delta_k:.1f}K')
        return dict(operation_id=op['operation_id'])

    def _cmd_experiment_data(self, experiment_id):
        e = self._experiment(experiment_id)
        return dict(experiment_id=e['id'], name=e['name'], running=e is self.experiment, interval_s=e['interval'], points=[dict(p) for p in e['points']])

    def _experiment(self, experiment_id):
        if self.experiment is not None and self.experiment['id'] == experiment_id:
            return self.experiment
        if experiment_id in self.records:
            return self.records[experiment_id]
        raise ActionRejected('UNKNOWN_EXPERIMENT')

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    # ---------------------------------------------------------------- HFCal evaluation (indicated values only)
    def _cmd_calorimetry_evaluate(self, experiment_id, start_s, end_s, limiting_reagent_mol, heat_capacity_j_k):
        e = self._experiment(experiment_id)
        pts = [p for p in e['points'] if start_s <= p['time_s'] <= end_s and p['tr_k'] is not None and p['tj_k'] is not None]
        if end_s <= start_s or len(pts) < 10:
            raise ActionRejected('WINDOW_TOO_SHORT')
        cals = [c for c in self.calibrations]
        before = [c for c in cals if c['time_s'] <= start_s]
        after = [c for c in cals if c['time_s'] >= end_s]
        if not cals:
            raise ActionRejected('UA_CALIBRATION_REQUIRED')
        quality = []
        c0 = before[-1] if before else None
        c1 = after[0] if after else None
        if c0 is None:
            quality.append('calibration_not_before_window')
        if c1 is None:
            quality.append('calibration_not_after_window')
        if c0 is None and c1 is None:
            c0 = min(cals, key=lambda c: min(abs(c['time_s'] - start_s), abs(c['time_s'] - end_s)))

        def interp(a, b, key, t, dosed):
            """RC1 practice: between a calibration before and one after the window the value is interpolated over the dosed amount
            when material was dosed in between (the volume, and hence UA, follows the dosing), otherwise over time."""
            if a is None:
                return b[key]
            if b is None or b['time_s'] <= a['time_s']:
                return a[key]
            if b['dosed_kg'] - a['dosed_kg'] > 1e-6:
                f = (dosed - a['dosed_kg']) / (b['dosed_kg'] - a['dosed_kg'])
            else:
                f = (t - a['time_s']) / (b['time_s'] - a['time_s'])
            f = min(max(f, 0.0), 1.0)
            return a[key] + f * (b[key] - a[key])
        if heat_capacity_j_k is not None:
            cp_basis = 'declared'
            C_of = lambda t, dosed: heat_capacity_j_k   # noqa: E731
        else:
            cps = self.cp_results
            if not cps:
                raise ActionRejected('HEAT_CAPACITY_REQUIRED')
            p0 = [c for c in cps if c['time_s'] <= start_s]
            p1 = [c for c in cps if c['time_s'] >= end_s]
            a, b = (p0[-1] if p0 else None), (p1[0] if p1 else None)
            if a is None and b is None:
                a = cps[-1]
            cp_basis = 'measured_bracketing' if (a is not None and b is not None) else 'measured_single'
            C_of = lambda t, dosed: interp(a, b, 'heat_capacity_j_k', t, dosed)   # noqa: E731
        subs = _lib()['substances']
        iv = e['interval']                                                   # record spacing: 2 s, doubled by each decimation of long records
        half = max(1, int(round(30.0 / iv)))                                  # +-30 s central difference

        def heat_flows(seq):
            """(t, Tr, q_flow, q_accu, q_dos, q_cal, q_baseline, q_r) of each point of seq (+-30 s central difference of the indicated Tr)."""
            out, m = [], len(seq)
            for i, p in enumerate(seq):
                t = p['time_s']
                j0, j1 = max(0, i - half), min(m - 1, i + half)
                dTdt = (seq[j1]['tr_k'] - seq[j0]['tr_k']) / (seq[j1]['time_s'] - seq[j0]['time_s']) if seq[j1]['time_s'] > seq[j0]['time_s'] else 0.0
                dosed_t = p['dosed_kg'] or 0.0
                ua = interp(c0, c1, 'ua_w_k', t, dosed_t)
                base = interp(c0, c1, 'baseline_w', t, dosed_t)
                q_flow = ua * (p['tr_k'] - p['tj_k'])
                q_acc = C_of(t, dosed_t) * dTdt
                q_dos = (p['dosing_rate_kg_s'] * subs[p['dosing_substance']]['cp_kg'] * (p['tr_k'] - p['dosing_feed_k'])) if p['dosing_rate_kg_s'] > 0 and p['dosing_substance'] else 0.0
                q_cal = self.plant['cal_heater_power_w'] if p['cal_on'] else 0.0
                out.append((t, p['tr_k'], q_flow, q_acc, q_dos, q_cal, base, q_flow + q_acc + q_dos - q_cal - base))
            return out
        n = len(pts)
        ts = [p['time_s'] for p in pts]
        q = heat_flows(pts)
        before_window = [p for p in e['points'] if start_s - 180.0 - 1e-9 <= p['time_s'] < start_s and p['tr_k'] is not None and p['tj_k'] is not None]
        q_before = ([x for x in heat_flows(before_window) if start_s - 150.0 - 1e-9 <= x[0] <= start_s - 30.0 + 1e-9]
                    if len(before_window) >= 0.8 * 180.0 / iv else [])          # 150 ... 30 s before the window: full +-30 s derivatives, no window data
        q_before = q_before if len(q_before) >= 0.8 * 120.0 / iv else []           # >= 80 % of the span recorded (any record spacing)
        Q, cum = 0.0, [0.0]
        for i in range(1, n):
            Q += 0.5 * (q[i][7] + q[i - 1][7]) * (ts[i] - ts[i - 1])
            cum.append(Q)
        C_end = C_of(ts[-1], pts[-1]['dosed_kg'] or 0.0)
        dT_ad = Q / C_end
        dosed = [p['dosed_kg'] or 0.0 for p in pts]
        d0, d1 = dosed[0], dosed[-1]
        out_pts, X_prev, mono, acc_max, mtsr_max = [], -1.0, True, 0.0, -math.inf
        for i in range(n):
            X = cum[i] / Q if Q != 0.0 else 0.0
            f = (dosed[i] - d0) / (d1 - d0) if d1 - d0 > 1e-9 else 1.0
            acc = min(max(f - X, 0.0), 1.0)
            mtsr = q[i][1] + acc * dT_ad
            if X < X_prev - 0.02:
                mono = False
            X_prev = max(X_prev, X)
            acc_max, mtsr_max = max(acc_max, acc), max(mtsr_max, mtsr)
            out_pts.append(dict(time_s=ts[i], tr_k=q[i][1], q_flow_w=q[i][2], q_accu_w=q[i][3], q_dos_w=q[i][4], q_cal_w=q[i][5], q_baseline_w=q[i][6], q_r_w=q[i][7],
                                thermal_conversion=X, accumulation=acc, mtsr_k=mtsr))
        if not mono:
            quality.append('thermal_conversion_not_monotonic')
        expected = int((min(end_s, e.get('t1', self.time_s)) - max(start_s, e['t0'])) / e['interval']) + 1
        if n < 0.9 * expected:
            quality.append('window_partly_unrecorded')
        if any(p['emergency'] for p in pts):
            quality.append('emergency_in_window')
        peak = max(abs(x[7]) for x in q)
        sigma = _qr_noise([x[7] for x in q], half)                           # scatter of q_r around its 60 s running mean
        k = max(5, min(int(round(600.0 / iv)), n // 3))                     # last third of the window (<= 10 min) before the last 30 s,
        edge = q[-(k + half):-half] if n >= k + 2 * half else q[-k:]        # whose derivatives are one-sided
        if abs(sum(x[7] for x in edge) / len(edge)) > max(EVAL_EDGE_FRACTION * peak, EVAL_END_SIGMA * sigma):
            quality.append('reaction_incomplete_at_window_end')             # heat flow not back to baseline: Q, X, dT_ad, MTSR normalised to a partial heat
        if q_before and abs(sum(x[7] for x in q_before) / len(q_before)) > max(EVAL_EDGE_FRACTION * peak, EVAL_START_SIGMA * sigma):
            quality.append('reaction_running_at_window_start')              # heat was already being released 150-30 s before the window
        if any(b0 <= ts[-1] and (b1 is None or b1 >= ts[0]) for b0, b1 in self._boil_log):
            quality.append('boiling_not_modelled')
        imax = max(range(n), key=lambda i: q[i][7])
        step = max(1, math.ceil(n / 2000))
        return dict(experiment_id=e['id'], start_s=ts[0], end_s=ts[-1], points=out_pts[::step][:2000], heat_released_j=Q,
                    reaction_enthalpy_j_mol=(-Q / limiting_reagent_mol) if limiting_reagent_mol else None, adiabatic_temperature_rise_k=dT_ad,
                    max_heat_flow_w=q[imax][7], time_of_max_heat_flow_s=ts[imax], final_thermal_conversion=cum[-1] / Q if Q != 0.0 else 0.0,
                    max_accumulation=acc_max, max_mtsr_k=mtsr_max, ua_basis='bracketing_calibrations' if (c0 is not None and c1 is not None) else 'single_calibration',
                    interpolation=('dosed_mass' if c1['dosed_kg'] - c0['dosed_kg'] > 1e-6 else 'time') if (c0 is not None and c1 is not None) else 'none',
                    heat_capacity_basis=cp_basis, dosed_mass_kg=d1 - d0, quality=quality or ['ok'])

    # ---------------------------------------------------------------- instrument cycle
    def _measure(self):
        p = self.plant
        a = math.exp(-CYCLE_S / NOISE_TAU_S)
        s = p['sensor_noise_k'] * math.sqrt(1.0 - a * a)
        for i in range(3):
            self.noise[i] = a * self.noise[i] + s * self.rng.gauss(0.0, 1.0)
        self.meas = dict(tr=quantize(self.T[0] + p['tr_offset_k'] + self.noise[0], RESOLUTION_K), tj=quantize(self.T[1] + p['tj_offset_k'] + self.noise[1], RESOLUTION_K),
                         tc=quantize(self.T[2] + p['tc_offset_k'] + self.noise[2], RESOLUTION_K), rpm=float(round(self.stir_rpm)))

    def _record_point(self, force=False):
        e = self.experiment
        if e is None or (not force and self.time_s + 1e-9 < e['next_t']):
            return
        m = self.meas if self.power else None
        d = self.dosing
        e['points'].append(dict(time_s=self.time_s, tr_k=m['tr'] if m else None, tj_k=m['tj'] if m else None, tc_k=m['tc'] if m else None, rpm=m['rpm'] if m else None,
                                mode=self.mode, tr_setpoint_k=self.tr_set if self.mode == 'tr' else None, tj_setpoint_k=self.tj_set if self.power else None,
                                dosed_kg=quantize(self.dosed_total, DOSING_RESOLUTION_KG),
                                dosing_rate_kg_s=d['rate'] if d else 0.0, dosing_feed_k=d['feed'] if d else None, dosing_substance=d['substance'] if d else None,
                                cal_on=self.cal_on, emergency=self.emergency['program'] if self.emergency else None))
        e['next_t'] = self.time_s + e['interval']
        if len(e['points']) > MAX_POINTS:
            e['points'] = e['points'][::2]
            e['interval'] *= 2.0
            e['next_t'] = e['points'][-1]['time_s'] + e['interval']

    def _active_causes(self):
        """Limit violations of the current cycle (indicated values, RC1e 5.6 / RC1mx 5.2); timed causes use their delay timers."""
        if not self.power or self.meas is None:
            return []
        m, sf, t = self.meas, self.safety, self.time_s
        causes = []
        if m['tr'] > sf['tr_max_k']:
            causes.append(('A', 'TR_ABOVE_MAX'))
        if m['tj'] > sf['tj_max_k']:
            causes.append(('A', 'TJ_ABOVE_MAX'))
        if m['tr'] < sf['tr_min_k']:
            causes.append(('C', 'TR_BELOW_MIN'))
        if m['tj'] < sf['tj_min_k']:
            causes.append(('C', 'TJ_BELOW_MIN'))
        if not TJOIL_LIMITS_K[0] <= m['tj'] <= TJOIL_LIMITS_K[1]:
            causes.append(('C', 'TJ_OUTSIDE_OIL_LIMITS'))
        if m['tc'] > m['tj'] + 5.0 and self.mode != 'off':
            causes.append(('C', 'TC_ABOVE_TJ'))
        if not TC_LIMITS_K[0] <= m['tc'] <= TC_LIMITS_K[1]:
            causes.append(('C', 'TC_OUTSIDE_LIMITS'))
        if self._tc_high_since is not None and t - self._tc_high_since >= TC_MAX_DELAY_S - 1e-9:
            causes.append(('A', 'TC_ABOVE_MAX_3MIN'))
        if sf['rmin_rpm'] > 0.0:
            if self._stir_off_since is not None and t - self._stir_off_since >= STIRRER_OFF_DELAY_S - 1e-9:
                causes.append(('B1', 'STIRRER_OFF_3MIN'))
            if self._stir_low_since is not None and t - self._stir_low_since >= STIRRER_LOW_DELAY_S - 1e-9:
                causes.append(('B1', 'STIRRER_BELOW_25RPM'))
        return causes

    def _safety_cycle(self):
        m, sf, t = self.meas, self.safety, self.time_s
        if m['tc'] > sf['tc_max_k']:
            if self._tc_high_since is None:
                self._tc_high_since = t
                self._event('warning', 'TC_ABOVE_MAX', 'Tc高于Tc max：3min内未消除将触发急停程序A')
        else:
            self._tc_high_since = None
        if sf['rmin_rpm'] > 0.0:
            if self.stir_set == 0.0 and self._stir_off_manual:
                if self._stir_off_since is None:
                    self._stir_off_since = t
                    self._event('warning', 'STIRRER_OFF', '搅拌已手动关闭（Rmin>0）：3min内未恢复将触发B1')
            else:
                self._stir_off_since = None
            if self.stir_set > 0.0 and m['rpm'] < STIRRER_MIN_RPM_B1:
                self._stir_low_since = self._stir_low_since if self._stir_low_since is not None else t
            else:
                self._stir_low_since = None
        else:
            self._stir_off_since = self._stir_low_since = None
        causes = self._active_causes()
        if not causes:
            return
        progs = {c[0] for c in causes}
        if len(causes) >= 2 or 'A' in progs:
            prog = 'A'                                       # RC1e 5.6: more than one error -> A
        else:
            prog = causes[0][0]
        cause = '+'.join(c[1] for c in causes)
        cur = self.emergency['program'] if self.emergency else None
        if cur is None or (prog == 'A' and cur != 'A'):
            self._enter_emergency(prog, cause)

    def _enter_emergency(self, program, cause):
        prev = self.emergency
        self.emergency = dict(program=program, cause=cause, t0=self.time_s)
        self._invalidate_cal_ops('EMERGENCY')
        if self.dosing is not None:
            self._stop_dosing('cancelled', 'EMERGENCY')
        if self.safety['rsafe_rpm'] is not None:
            if self.safety['rsafe_rpm'] == 0.0 and self.stir_set > 0.0:
                self._stir_off_manual = False                                 # stopped by the program (Rsafe), not by the operator
            self.stir_set = self.safety['rsafe_rpm']
        m = self.meas
        if program == 'A':
            self.relay_closed = False
            self.mode = 'tj'
            self.tr_task, self.tr_set = None, None
            self.tj_task = None
            self.tj_set = m['tj']
            self.tj_ctrl.reset()
        elif program == 'B1':                                               # RC1e 5.5: Tj mode, from the current Tj to Tsafe at -0.1 K/s,
            if self.mode == 'tr':                                            # Tj maintained if already <= Tsafe (footnote 1)
                self.tr_task, self.tr_set = None, None
            self.mode = 'tj'
            cur = m['tj']
            end = min(cur, self.safety['tsafe_k'])
            self.tj_task = dict(start=cur, end=end, t0=self.time_s, duration=(cur - end) / EMERGENCY_GRADIENT_K_S)
            self.tj_set = cur
            self.tj_ctrl.reset(min(self.tj_ctrl.i, 0.0))                    # B1 cools: a stored heating demand is dropped
        else:                                                                # C: Tj or Tr from the current value to Tsafe at +-0.1 K/s
            ts = self.safety['tsafe_k']
            if self.mode == 'tr':
                cur = m['tr']
                self.tr_task = dict(start=cur, end=ts, t0=self.time_s, duration=abs(cur - ts) / EMERGENCY_GRADIENT_K_S,
                                    controller=(self.tr_task or {}).get('controller', 'aqueous'))
                self.tr_set = cur
            else:
                self.mode = 'tj'
                cur = m['tj']
                self.tj_task = dict(start=cur, end=ts, t0=self.time_s, duration=abs(cur - ts) / EMERGENCY_GRADIENT_K_S)
                self.tj_set = cur
                if ts < cur:
                    self.tj_ctrl.reset(min(self.tj_ctrl.i, 0.0))
        self._event('emergency', 'EMERGENCY_' + program, f'急停程序{program}' + ('（升级自' + prev['program'] + '）' if prev else '') + f'：{cause}')

    def _control(self):
        """Actuators for the next 2 s cycle from the indicated values (held until the next cycle)."""
        m = self.meas
        t = self.time_s
        if self.emergency is not None and self.emergency['program'] == 'A':
            self.heater_w, self.valve_u, self.safety_valve = 0.0, 1.0, True
            self.tj_set = m['tj']
            return
        self.safety_valve = False
        lo = max(self.safety['tj_min_k'], TJ_DEVICE_MIN_K)
        hi = min(self.safety['tj_max_k'], TJ_MAX_K)
        if self.mode == 'tr':
            self.tr_set = _ramp_value(self.tr_task, t)
            self.tj_set = self.tr_ctrl.output(self.tr_set, m['tr'], self.safety['tdiff_max_k'], lo + TJ_CASCADE_MARGIN_K, hi - TJ_CASCADE_MARGIN_K,
                                              release_cooling=self._tr_rising())
        else:
            self.tj_set = min(max(_ramp_value(self.tj_task, t), lo), hi) if self.emergency is None else _ramp_value(self.tj_task, t)
        self.heater_w, self.valve_u = self.tj_ctrl.output(self.tj_set, m['tj'], m['tc'])

    def _tr_rising(self):
        return tr_rising(self._tr_hist, max(TDIFF_RELEASE_K_S, TDIFF_RELEASE_PER_NOISE * self.plant['sensor_noise_k']))

    def _advance_ops(self):
        """HFCal operation state machines on the indicated values of this cycle."""
        m, t = self.meas, self.time_s
        op = self.cal_op
        if op is not None:
            if not self._cal_valid_for(op):
                self._finish_op(op, 'failed', 'CONDITIONS_CHANGED')
                self.cal_op, self.cal_on = None, False
            else:
                dur = op['durations'][op['stage']]
                el = t - op['stage_t0']
                if el >= dur / 2:
                    op['sums'].setdefault(op['stage'], []).append((m['tr'] - m['tj'], m['tr'], t))
                if el >= dur - 1e-9:
                    nxt = {'pre': 'heat', 'heat': 'post', 'post': None}[op['stage']]
                    if nxt is None:
                        self._complete_cal(op)
                    else:
                        op['stage'], op['stage_t0'] = nxt, t
                        self.cal_on = nxt == 'heat'
                        self._event('calibration', 'CALIBRATION_' + nxt.upper(), {'heat': '校准加热器通电', 'post': '校准加热器断电：后基线'}[nxt])
        op = self.cp_op
        if op is not None:
            if not (self.power and self.emergency is None and self.mode == 'tr' and self.dosing is None and self.drain_angle == 0.0 and abs(self.stir_set - op['rpm']) < 1e-9):
                self._hold_cp_ramp(op)
                self._finish_op(op, 'failed', 'CONDITIONS_CHANGED')
                self.cp_op = None
            else:
                el = t - op['stage_t0']
                st = op['stage']
                if st in ('hold1', 'hold2'):
                    if el >= op['hold'] / 2:
                        op.setdefault(st, []).append(m['tj'] - m['tr'])
                    if el >= op['hold'] - 1e-9:
                        if st == 'hold1':
                            op['stage'], op['stage_t0'] = 'ramp', t
                            self._set_tr_task(op['tr_start'] + op['delta'], op['delta'] / op['rate'], op['controller'], event=False)
                            op['task'] = self.tr_task
                        else:
                            self._complete_cp(op)
                else:
                    ramp_s = op['delta'] / op['rate']
                    if el >= ramp_s / 2:
                        op.setdefault('ramp', []).append((t, m['tr'], m['tj'] - m['tr']))
                    if el >= ramp_s - 1e-9:
                        op['stage'], op['stage_t0'] = 'hold2', t

    def _complete_cal(self, op):
        P = self.plant['cal_heater_power_w']
        mean = lambda xs: sum(x[0] for x in xs) / len(xs)   # noqa: E731
        base = 0.5 * (mean(op['sums']['pre']) + mean(op['sums']['post']))
        heat = mean(op['sums']['heat'])
        if heat - base <= 1e-6:
            self._finish_op(op, 'failed', 'NO_CALIBRATION_RESPONSE')
            self.cal_op, self.cal_on = None, False
            return
        ua = P / (heat - base)
        stats = {k: _stage_statistics(op['sums'][k]) for k in ('pre', 'heat', 'post')}
        se = math.sqrt(stats['heat']['se_mean'] ** 2 + 0.25 * (stats['pre']['se_mean'] ** 2 + stats['post']['se_mean'] ** 2))
        bias = stats['heat']['drift'] + 0.5 * (stats['pre']['drift'] + stats['post']['drift'])
        if bias > CAL_DRIFT_MAX_REL * (heat - base):
            self._finish_op(op, 'failed', 'CALIBRATION_NOT_STEADY')
            self.cal_op, self.cal_on = None, False
            self._event('calibration', 'CALIBRATION_NOT_STEADY', f'HFCal标定失败：各段后半(Tr−Tj)的显著漂移{bias:.3f}K超过信号{heat - base:.3f}K的5%，请加长各段')
            return
        tr = sum(x[1] for x in op['sums']['heat']) / len(op['sums']['heat'])
        res = dict(ua_w_k=ua, baseline_w=ua * base, delta_t_base_k=base, delta_t_heat_k=heat, tr_k=tr, uncertainty_w_k=ua * math.hypot(se, bias) / (heat - base),
                   time_s=self.time_s, dosed_kg=quantize(self.dosed_total, DOSING_RESOLUTION_KG))
        op['result'] = res
        self.calibrations.append(dict(res, rpm=op['rpm'], operation_id=op['operation_id']))
        self._finish_op(op, 'succeeded')
        self.cal_op, self.cal_on = None, False
        self._event('calibration', 'CALIBRATION_DONE', f'HFCal标定完成：UA={ua:.2f}W/K，基线{ua * base:.2f}W')

    def _complete_cp(self, op):
        ua = self.calibrations[-1]['ua_w_k']
        hold = 0.5 * (sum(op['hold1']) / len(op['hold1']) + sum(op['hold2']) / len(op['hold2']))
        rows = op.get('ramp', [])
        n = len(rows)
        slope = 0.0
        if n >= 3:                                                       # measured dTr/dt: least squares of the indicated Tr in the averaged half
            tm = sum(x[0] for x in rows) / n
            ym = sum(x[1] for x in rows) / n
            sxx = sum((x[0] - tm) ** 2 for x in rows)
            slope = sum((x[0] - tm) * (x[1] - ym) for x in rows) / sxx if sxx > 0.0 else 0.0
        ramp = sum(x[2] for x in rows) / n if n else hold
        C = ua * (ramp - hold) / slope if slope > CP_MIN_SLOPE_FRACTION * op['rate'] else -1.0
        res = dict(heat_capacity_j_k=C if C > 0.0 else None, ua_w_k=ua, tr_start_k=op['tr_start'], tr_end_k=op['tr_start'] + op['delta'],
                   measured_rate_k_s=slope if n >= 3 else None, time_s=self.time_s, dosed_kg=quantize(self.dosed_total, DOSING_RESOLUTION_KG))
        op['result'] = res
        if C <= 0.0:
            self._finish_op(op, 'failed', 'NO_CALIBRATION_RESPONSE')
        else:
            self.cp_results.append(dict(res, operation_id=op['operation_id']))
            self._finish_op(op, 'succeeded')
            self._event('calibration', 'CP_DONE', f'比热测定完成：C={C:.0f}J/K')
        self.cp_op = None

    def _tick(self):
        self._measure()
        self._tr_hist.append((self.time_s, self.meas['tr']))
        del self._tr_hist[:-TR_SLOPE_CYCLES]
        if self.mode == 'tr' and self._props()['volume'] < OPERATING_VOLUME_M3[0] * (1 - 1e-9):
            self.mode = 'tj'                                 # Tr sensor no longer immersed (discharge): hold the jacket
            self.tr_task, self.tr_set = None, None
            self.tj_task = dict(start=self.meas['tj'], end=self.meas['tj'], t0=self.time_s, duration=0.0)
            self.tj_ctrl.reset()
            self._invalidate_cal_ops('CONDITIONS_CHANGED')
            self._event('warning', 'TR_SENSOR_NOT_IMMERSED', '料液少于0.5L，Tr传感器未浸没：转为Tj模式保持当前夹套温度')
        if self.dosing is not None and self.dosing['dosed'] >= self.dosing['total'] - 1e-12:
            self._stop_dosing('succeeded', None)
        self._record_point()
        self._advance_ops()
        self._safety_cycle()
        self._control()
        self._stirrer_command()

    def _stirrer_command(self):
        rmax = self.safety['rmax_rpm']
        if self.stir_set > rmax and self.emergency is None:
            self.stir_set = rmax

    # ---------------------------------------------------------------- physics between cycles
    def _substep_limit(self, h):
        """Adiabatic reaction heating <= REACTION_DT_K and dosing <= 1 % of the mass per sub-step."""
        props = self._props()
        C = self._C_r(props)
        q = 0.0
        rx = _lib()['reactions']
        for rid, cfg in self.reaction_cfg.items():
            q += abs(reaction_rate(rx[rid], cfg, self.n, props['volume'], self.T[0]) * rx[rid]['dh'])
        if q > 0.0:
            h = min(h, REACTION_DT_K * C / q)
        if self.dosing is not None and props['mass'] > 0.0:
            h = min(h, 0.01 * props['mass'] / self.dosing['rate'])
        return max(h, MIN_SUBSTEP_S)

    def _react(self, h):
        rx = _lib()['reactions']
        if not self.reaction_cfg or not self.n:
            self._reaction_power = 0.0
            return
        V = self._props()['volume']
        m0 = self._props()['mass']
        hf0 = formation_enthalpy(self.n)
        C0 = self._C_r()
        sens0 = C0 * (self.T[0] - T_REF)
        changed = False
        for rid, cfg in self.reaction_cfg.items():
            r = rx[rid]
            dxi = extent_backward_euler(r, cfg, self.n, V, self.T[0], h)
            if dxi <= 0.0:
                continue
            changed = True
            for s_, c in r['reactants'].items():
                self.n[s_] = max(self.n.get(s_, 0.0) - c * dxi, 0.0)
            for s_, c in r['products'].items():
                self.n[s_] = self.n.get(s_, 0.0) + c * dxi
        if not changed:
            self._reaction_power = 0.0
            return
        q = hf0 - formation_enthalpy(self.n)                      # -dH_r dxi at 298.15 K
        self.T[0] = T_REF + (sens0 + q) / self._C_r()
        self.led['reaction_heat_j'].add(q)
        self.led['reaction_mass_defect_kg'].add(self._props()['mass'] - m0)   # rounded library molar masses (PubChem, 0.01 g/mol)
        self._reaction_power = q / h

    def _stirrer_physics(self, h, props):
        target = self.stir_set if self.power else 0.0
        if props['volume'] < 1e-7:
            rho, mu = 1.2, 1.8e-5                                  # air: empty reactor
        else:
            rho, mu = props['rho'], props['mu']
        step = STIRRER_ACCEL_RPM_S * h
        rpm0 = self.stir_rpm
        rpm = min(target, rpm0 + step) if target >= rpm0 else max(target, rpm0 - step)
        t_ramp = min(h, abs(rpm - rpm0) / STIRRER_ACCEL_RPM_S)             # time spent accelerating inside this sub-step
        limit = min(MOTOR_TORQUE_MAX_NM, GLASS_STIRRER_TORQUE_MAX_NM)
        tq, pw = stirrer_torque(rpm, rho, mu)
        if tq > limit:                                             # torque-limited speed (bisection)
            lo, hi = 0.0, rpm
            for _ in range(40):
                mid = 0.5 * (lo + hi)
                (lo, hi) = (mid, hi) if stirrer_torque(mid, rho, mu)[0] <= limit else (lo, mid)
            rpm = lo
            tq, pw = stirrer_torque(rpm, rho, mu)
        self.stir_rpm, self.stir_torque = rpm, tq
        self.stir_power = pw if props['volume'] >= 1e-7 else 0.0
        turns = (0.5 * (rpm0 + rpm) * t_ramp + rpm * (h - t_ramp)) / 60.0        # exact for constant acceleration, then constant speed
        self.stir_angle = (self.stir_angle + 2.0 * math.pi * turns) % (2.0 * math.pi)

    def _integrate(self, dt):
        p = self.plant
        H = self._coolant_H()
        t_left = dt
        while t_left > 1e-12:
            h = self._substep_limit(min(t_left, MAX_SUBSTEP_S))
            h = min(h, t_left)
            props = self._props()
            self._stirrer_physics(h, props)
            self._react(h)
            props = self._props()
            C_r = self._C_r(props)
            UA = overall_ua(props['volume'], self.stir_rpm, props)
            Lr = top_loss_coefficient(self.T[0], mixture_vapour_pressure(self.n, self.T[0]), self.fit)
            on = self.power
            G_v = (self.valve_u * self.fit['G_VALVE'] + (self.fit['G_SAFETY'] if self.safety_valve else 0.0)) if on else 0.0
            heater = self.heater_w if on else 0.0
            src = self.stir_power + (p['cal_heater_power_w'] if self.cal_on else 0.0)
            pump = self.fit['P_COLD'] if on else 0.0
            self.T, fl = plant_step(self.T, h, C_r, UA, Lr, G_v, heater, src, H, p['coolant_inlet_k'], p['ambient_k'], pump, self.fit)
            for k in ('heater_j', 'jacket_loss_j', 'mass_loss_j', 'coolant_removed_j', 'cold_room_gain_j', 'jacket_to_mass_j', 'valve_exchange_j'):
                self.led[k].add(fl[k])
            self.led['cold_pump_j'].add(fl['cold_pump_j'])
            self.led['stirrer_j'].add(self.stir_power * h)
            self.led['cal_heater_j'].add((p['cal_heater_power_w'] if self.cal_on else 0.0) * h)
            if self.dosing is not None:
                d = self.dosing
                dm = min(d['rate'] * h, d['total'] - d['dosed'])
                if dm > 0.0:
                    s = _lib()['substances'][d['substance']]
                    self._mix_in({d['substance']: dm / s['M']}, d['feed'], 'dose')
                    d['dosed'] += dm
                    self.dosed_total += dm
                    self.charged_kg += dm
                    self.led['dosed_kg'].add(dm)
            step_rad = DRAIN_HANDLE_RAD_S * h
            self.drain_angle = min(self.drain_target, self.drain_angle + step_rad) if self.drain_target >= self.drain_angle else max(self.drain_target, self.drain_angle - step_rad)
            if self.drain_angle > 0.0 and self.n:
                V = props['volume']
                dV = min(p['drain_flow_m3_s'] * self.drain_angle / DRAIN_OPEN_RAD * h, V)
                if V > 0.0 and dV > 0.0:
                    f = dV / V if V - dV > 1e-9 else 1.0
                    subs = _lib()['substances']
                    out_mass = f * props['mass']
                    self.led['discharge_sensible_j'].add(f * sum(v * subs[k]['cp_mol'] for k, v in self.n.items()) * (self.T[0] - T_REF))
                    self.n = {k: v * (1.0 - f) for k, v in self.n.items() if v * (1.0 - f) > 1e-15} if f < 1.0 else {}
                    self.charged_kg = max(self.charged_kg - out_mass, 0.0) if f < 1.0 else 0.0     # emptied: nothing left on record
                    self.led['discharged_kg'].add(out_mass)
            t_left -= h

    def _check_boiling(self):
        """The model has no boiling: flag when the Raoult vapour pressure of the mass reaches 1 atm and when it falls below again."""
        boiling = bool(self.n) and mixture_vapour_pressure(self.n, self.T[0]) >= 101325.0
        if boiling and not self._boiling:
            self._boil_log.append([self.time_s, None])
            del self._boil_log[:-MAX_RECORDS]
            self._event('warning', 'BOILING_NOT_MODELLED', '料液达到泡点：模型不含沸腾、回流与蒸发损失，此后的温度与热流超出模型适用范围')
        elif self._boiling and not boiling:
            self._boil_log[-1][1] = self.time_s
            self._event('warning', 'BELOW_BUBBLE_POINT', '料液回到泡点以下')
        self._boiling = boiling

    def _next_event(self, end):
        nxt = (self.time_ns // CYCLE_NS + 1) * CYCLE_NS
        return min(end, nxt)

    def step(self, dt_s):
        validate(number('s', 0.0, 86400.0), dt_s)
        raw = dt_s * 1e9 + self._clock_remainder_ns
        ns = round(raw)
        self._clock_remainder_ns = raw - ns
        if ns <= 0 or self.plant is None:
            self.time_ns += max(ns, 0)
            self.revision += 1
            return
        end = self.time_ns + ns
        while self.time_ns < end:
            nxt = self._next_event(end)
            self._integrate((nxt - self.time_ns) / 1e9)
            self.time_ns = nxt
            if nxt % CYCLE_NS == 0:
                self._check_boiling()
                if self.power:
                    self._tick()
        self.revision += 1


# ---------------------------------------------------------------- host session: identity filter, request binding, ownership
_CONTROL_GUARDED = {'experiment.end', 'thermostat.set_tj', 'thermostat.set_tr', 'stirrer.set', 'stirrer.toggle', 'dosing.start', 'dosing.stop', 'calibration.run',
                    'calibration.cp', 'safety.configure', 'emergency.reset', 'power.set', 'power.toggle'}
_DATA_GUARDED = {'experiment.data', 'calorimetry.evaluate', 'calibration.result'}


class MtRc1mxhfcalSession:
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
        meta = copy.deepcopy(export_mt_rc1mxhfcal_contract_metadata())
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0',
                    capabilities={k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or self._role != 'agent'},
                    state_slots={k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or self._role != 'agent'})

    def _foreign_experiment(self):
        h = self._host
        e = h.experiment
        owner = h._owners.get(e['id']) if e is not None else None
        return owner is not None and owner != self._principal and h._principals.get(owner) == 'agent'

    def _foreign_object(self, object_id):
        owner = self._host._owners.get(object_id)
        return owner is not None and owner != self._principal and self._host._principals.get(owner) == 'agent'

    def _foreign_operation(self):
        """A running dose / calibration / heat-capacity operation started by another agent."""
        h = self._host
        return any(op is not None and self._foreign_object(op['operation_id']) for op in (h.dosing['op'] if h.dosing else None, h.cal_op, h.cp_op))

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        values['experiment.started_by_other_agent'] = self._role == 'agent' and self._foreign_experiment()
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=CYCLE_S if SLOTS[k]['origin'] == 'sensor' else None) for k in ids})

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        h = self._host
        if capability_id in _CONTROL_GUARDED and ((h.experiment is not None and self._foreign_experiment()) or self._foreign_operation()):
            if capability_id in ('power.set', 'power.toggle') and not (h.power and (capability_id == 'power.toggle' or not arguments.get('enabled', True))):
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in ('experiment.data', 'calorimetry.evaluate') and self._foreign_object(arguments['experiment_id']):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'calibration.result' and self._foreign_object(arguments['operation_id']):
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
            if key in h._requests:                                 # a request_id is bound to its first request, accepted or rejected
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
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
            if capability_id == 'experiment.start':
                h._owners[result['experiment_id']] = self._principal
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
        raise ActionRejected('ACCESS_DENIED')                       # no writable slot: set points are written through capabilities

    def poll(self, operation_id):
        h = self._host
        if not isinstance(operation_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        op = h.operations.get(operation_id)
        if op is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        result = h._dose_result(op) if op['kind'] == 'dose' else h._cal_result(op)
        return dict(operation_id=operation_id, status=op['phase'], result=result)

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
        except ActionRejected as exc:
            if digest is not None:
                h._requests[key] = (digest, str(exc))
            raise
        h._cancel_operation(op)
        h.revision += 1
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='succeeded',
                        state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=dict(phase=op['phase']), observations={}, error=None)
        h._requests[key] = (digest, copy.deepcopy(response))
        return response


# ---------------------------------------------------------------- contract metadata
ASYNC = {'dosing.start', 'calibration.run', 'calibration.cp'}
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.reactor_k', 'truth.jacket_k', 'truth.cooling_oil_k'],
    'power.set': ['power.enabled', 'status.ok', 'thermostat.mode', 'thermostat.tj_setpoint_k', 'thermostat.tr_setpoint_k', 'thermostat.ramp_active', 'reactor.indicated_k',
                  'jacket.indicated_k', 'cooling_oil.indicated_k', 'stirrer.indicated_rpm', 'stirrer.setpoint_rpm', 'safety_relay.closed', 'cal_heater.on', 'calibration.running',
                  'dosing.active', 'dosing.rate_kg_s', 'dosing.indicated_mass_kg', 'calorimetry.heat_flow_w', 'emergency.active', 'emergency.program', 'emergency.cause',
                  'truth.heater_power_w', 'truth.valve_opening'],
    'safety.configure': ['safety.tsafe_k', 'safety.tr_min_k', 'safety.tr_max_k', 'safety.tj_min_k', 'safety.tj_max_k', 'safety.tdiff_max_k', 'safety.rsafe_rpm',
                         'safety.rmin_rpm', 'safety.rmax_rpm', 'safety.tc_max_k'],
    'reactor.charge': ['reactor.charged_mass_kg', 'truth.volume_m3', 'truth.mass_kg', 'truth.heat_capacity_j_k', 'truth.composition_mol', 'truth.reactor_k',
                       'truth.bubble_point_k', 'truth.ua_w_k'],
    'reaction.configure': [],
    'experiment.start': ['experiment.running', 'experiment.id', 'experiment.started_by_other_agent'],
    'experiment.end': ['experiment.running', 'experiment.id', 'experiment.started_by_other_agent', 'thermostat.mode', 'thermostat.tj_setpoint_k', 'thermostat.tr_setpoint_k',
                       'thermostat.ramp_active'],
    'thermostat.set_tj': ['thermostat.mode', 'thermostat.tj_setpoint_k', 'thermostat.tr_setpoint_k', 'thermostat.ramp_active', 'calibration.running', 'cal_heater.on'],
    'thermostat.set_tr': ['thermostat.mode', 'thermostat.tj_setpoint_k', 'thermostat.tr_setpoint_k', 'thermostat.ramp_active', 'calibration.running', 'cal_heater.on'],
    'stirrer.set': ['stirrer.setpoint_rpm', 'calibration.running', 'cal_heater.on', 'thermostat.ramp_active'],
    'dosing.start': ['dosing.active', 'dosing.rate_kg_s', 'dosing.indicated_mass_kg', 'calibration.running', 'cal_heater.on', 'thermostat.ramp_active'],
    'dosing.stop': ['dosing.active', 'dosing.rate_kg_s'],
    'calibration.run': ['calibration.running'],
    'calibration.cp': ['calibration.running'],
    'emergency.trigger': ['emergency.active', 'emergency.program', 'emergency.cause', 'status.ok', 'safety_relay.closed', 'thermostat.mode', 'thermostat.tj_setpoint_k',
                          'thermostat.tr_setpoint_k', 'thermostat.ramp_active', 'cal_heater.on', 'calibration.running', 'dosing.active', 'dosing.rate_kg_s', 'stirrer.setpoint_rpm'],
    'emergency.reset': ['emergency.active', 'emergency.program', 'emergency.cause', 'status.ok', 'safety_relay.closed', 'thermostat.mode', 'thermostat.tj_setpoint_k',
                        'thermostat.tr_setpoint_k', 'thermostat.ramp_active'],
    'reactor.discharge': ['calibration.running', 'cal_heater.on', 'thermostat.ramp_active'],
    'sim.step': list(SLOTS),
}
EFFECTS['power.toggle'] = EFFECTS['power.set']
EFFECTS['stirrer.toggle'] = EFFECTS['stirrer.set']
for _k, _v in EFFECTS.items():
    CAPS[_k]['effects'] = _v
ERRORS = sorted(set(re.findall(r"ActionRejected\('([A-Z_0-9]+)'", Path(__file__).read_text())))
PRECONDITIONS = {
    'plant.configure': ('只能声明一次并显式接受；水冷时进口温度1–25°C（厂家：约15°C、最高25°C），流量≥10L/min', ['plant.configured']),
    'power.set': ('须已声明工程参数；断电取消校准与加料、停止加热/泵/搅拌', ['plant.configured', 'power.enabled']),
    'power.toggle': ('须已声明工程参数', ['plant.configured', 'power.enabled']),
    'safety.configure': ('须已声明工程参数、无急停程序；Tr min<Tsafe≤Tr max、Tj max−Tj min≥3K（串级输出在两端各留1K）、Rmin≤Rmax、Rsafe为null/0或30…Rmax；通电时当前示值须在新限值内；当前搅拌设定不高于Rmax；'
                         'Tsafe低于当前料液泡点−2K（通电与B1/C把料液带向Tsafe）',
                         ['plant.configured', 'emergency.active', 'reactor.indicated_k', 'jacket.indicated_k', 'stirrer.setpoint_rpm', 'truth.bubble_point_k']),
    'reactor.charge': ('须已声明工程参数并接受先验；物质在库中且在加入温度下为液体；底阀关闭、无进行中的标定；加入后体积≤2L（AP01-2操作上限）；混合后温度、当前任务终值与Tsafe低于新料液泡点−2K',
                       ['plant.configured', 'drain.open', 'calibration.running', 'truth.bubble_point_k']),
    'reaction.configure': ('须已声明工程参数并接受动力学先验；反应在库中且可用；每个反应物都给出级数', ['plant.configured']),
    'experiment.start': ('须通电；无进行中的实验', ['power.enabled', 'experiment.running']),
    'experiment.end': ('有进行中的实验；cool_to须通电、无急停、温度在Tj限值内且低于料液泡点−2K', ['experiment.running', 'power.enabled', 'emergency.active', 'truth.bubble_point_k']),
    'thermostat.set_tj': ('须通电、无急停程序；终值在max(Tj min, −50°C)…min(Tj max, 230°C)内且低于料液泡点−2K（会中止进行中的标定）',
                          ['power.enabled', 'emergency.active', 'safety.tj_min_k', 'safety.tj_max_k', 'truth.bubble_point_k']),
    'thermostat.set_tr': ('须通电、无急停程序；终值在Tr min…Tr max内；反应器内≥0.5L；终值低于料液泡点−2K（模型有效性）', ['power.enabled', 'emergency.active', 'safety.tr_min_k',
                                                                                                         'safety.tr_max_k', 'truth.volume_m3', 'truth.bubble_point_k']),
    'stirrer.set': ('须通电、无急停程序；0或30…Rmax rpm', ['power.enabled', 'emergency.active', 'safety.rmax_rpm']),
    'stirrer.toggle': ('须通电、无急停程序；打开时须有上次非零设定且不高于Rmax', ['power.enabled', 'emergency.active', 'stirrer.setpoint_rpm', 'safety.rmax_rpm']),
    'dosing.start': ('须通电、无急停、无进行中的加料；物质在库中且在进料温度下为液体；速率不超过已声明的加料单元上限；加完后体积≤2L且泡点−2K高于当前Tr示值、任务终值与Tsafe；底阀关闭',
                     ['power.enabled', 'emergency.active', 'dosing.active', 'drain.open', 'truth.volume_m3', 'truth.bubble_point_k', 'reactor.indicated_k']),
    'dosing.stop': ('有进行中的加料', ['dosing.active']),
    'calibration.run': ('须通电、无急停；Tr模式且无斜坡；反应器内≥0.5L；搅拌≥30rpm；无加料、底阀关闭、无其他标定', ['power.enabled', 'emergency.active', 'thermostat.mode',
                                                                                                      'thermostat.ramp_active', 'stirrer.setpoint_rpm', 'dosing.active', 'drain.open',
                                                                                                      'calibration.running', 'truth.volume_m3']),
    'calibration.cp': ('同calibration.run，且已有UA标定；升温终点在Tr max与泡点−2K以下', ['power.enabled', 'emergency.active', 'thermostat.mode', 'thermostat.ramp_active',
                                                                              'calibration.ua_w_k', 'calibration.running', 'safety.tr_max_k']),
    'calibration.result': ('操作存在', []),
    'calorimetry.evaluate': ('实验存在；窗口内至少10个有效点；至少有一次UA标定；未给热容时须有比热测定', ['calibration.ua_w_k', 'calibration.heat_capacity_j_k']),
    'experiment.data': ('实验存在', []),
    'emergency.trigger': ('须通电', ['power.enabled']),
    'emergency.reset': ('须通电、有急停程序且立即触发的条件（Tr/Tj/Tc限值、Tc>Tj+5K）已消除；带延时的原因复位后重新计时', ['power.enabled', 'emergency.active',
                                                                                                      'reactor.indicated_k', 'jacket.indicated_k', 'cooling_oil.indicated_k']),
    'reactor.discharge': ('须已声明工程参数；打开时无进行中的加料', ['plant.configured', 'dosing.active']),
}
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入结构、类型或范围', 'ACCESS_DENIED': '该角色无权调用，或当前实验/操作属于另一个agent', 'REQUEST_CONFLICT': '同一request_id已用于不同的请求',
    'UNKNOWN_INSTANCE': '实例ID不存在', 'UNSUPPORTED': '本配置不提供该功能（理由见function_zh）', 'UNKNOWN_CAPABILITY': '能力ID不存在', 'UNKNOWN_OPERATION': '操作ID不存在',
    'PLANT_ALREADY_CONFIGURED': '工程参数只能声明一次', 'ENGINEERING_ACCEPTANCE_REQUIRED': '未显式接受工程参数或先验', 'COOLANT_OUT_OF_RANGE': '冷却水进口温度低于1°C（会结冰）',
    'PLANT_REQUIRED': '尚未声明工程参数', 'POWER_OFF': '未通电', 'EMERGENCY_ACTIVE': '急停程序激活期间不能改设定（手册：急停程序期间不能做任何更改）',
    'INVALID_SAFETY_SETTINGS': '安全设置自相矛盾（上下限、Tj窗口不足3K、Tsafe、Rmin/Rmax或Rsafe范围）', 'LIMIT_VIOLATED_NOW': '当前示值已超出新的安全限值', 'STIRRER_ABOVE_RMAX': '当前搅拌设定高于新Rmax',
    'UNKNOWN_SUBSTANCE': '物质不在反应物料库中（或不可用于运行层）', 'NOT_LIQUID': '该物质在给定温度下不是液体（熔点/沸点）', 'DRAIN_OPEN': '底阀未关闭',
    'CALIBRATION_RUNNING': '标定进行中，不能加料', 'VOLUME_ABOVE_OPERATING_RANGE': '体积将超过AP01-2操作上限2L', 'UNKNOWN_REACTION': '反应不在库中或不可用',
    'INVALID_KINETICS': '动力学参数不完整（每个反应物须给出级数，反应不得重复）', 'EXPERIMENT_RUNNING': '已有进行中的实验', 'NO_EXPERIMENT': '没有进行中的实验',
    'OUTSIDE_SAFETY_LIMITS': '设定超出安全限值（Tj min/max、Tr min/max或设备温区）', 'VOLUME_BELOW_OPERATING_RANGE': '反应器内少于0.5L（AP01-2操作下限，Tr传感器未浸没）',
    'ABOVE_BUBBLE_POINT': '设定高于料液泡点−2K：本模型不含沸腾/回流（AP01-2只允许常压以下）', 'OUTSIDE_STIRRER_RANGE': '转速不在0或30…Rmax rpm（AP01-2 30–850rpm）内',
    'STIRRER_SETPOINT_REQUIRED': '搅拌开关按钮需要上次非零转速设定', 'DOSING_ACTIVE': '加料进行中', 'RATE_ABOVE_DOSING_UNIT': '加料速率超过已声明的加料单元上限',
    'NOT_DOSING': '没有进行中的加料', 'BUSY': '已有进行中的标定', 'TR_MODE_REQUIRED': '标定需要Tr模式且设定不在斜坡中', 'STIRRER_REQUIRED': '标定需要搅拌（≥30rpm）',
    'UA_CALIBRATION_REQUIRED': '需要先完成UA标定', 'NO_EMERGENCY': '没有激活的急停程序', 'CAUSE_PERSISTS': '触发急停的条件仍然存在',
    'UNKNOWN_EXPERIMENT': '实验ID不存在', 'WINDOW_TOO_SHORT': '评价窗口无效或有效点少于10个', 'HEAT_CAPACITY_REQUIRED': '未给热容且没有比热测定',
    'CONDITIONS_CHANGED': '标定期间条件改变（任务、搅拌、加料、放料或急停）：标定失败', 'EMERGENCY': '急停程序中止了操作', 'USER_CANCEL': '操作被停止或取消（已完成部分保留）',
    'NO_CALIBRATION_RESPONSE': '标定信号无响应（温差变化非正，或比热斜坡的实测升温速率低于设定的20%）', 'NOT_RUNNING': '操作不在进行中',
    'CALIBRATION_NOT_STEADY': '标定各段后半(Tr−Tj)的显著漂移（超过其3倍标准误差）超过信号的5%：未达稳态，请加长各段时长',
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
CAP_ERRORS = {
    'plant.configure': ['PLANT_ALREADY_CONFIGURED', 'ENGINEERING_ACCEPTANCE_REQUIRED', 'COOLANT_OUT_OF_RANGE'],
    'power.set': ['PLANT_REQUIRED'], 'power.toggle': ['PLANT_REQUIRED'],
    'safety.configure': ['PLANT_REQUIRED', 'EMERGENCY_ACTIVE', 'INVALID_SAFETY_SETTINGS', 'LIMIT_VIOLATED_NOW', 'STIRRER_ABOVE_RMAX', 'ABOVE_BUBBLE_POINT'],
    'reactor.charge': ['PLANT_REQUIRED', 'ENGINEERING_ACCEPTANCE_REQUIRED', 'UNKNOWN_SUBSTANCE', 'NOT_LIQUID', 'DRAIN_OPEN', 'CALIBRATION_RUNNING', 'VOLUME_ABOVE_OPERATING_RANGE',
                       'ABOVE_BUBBLE_POINT'],
    'reaction.configure': ['PLANT_REQUIRED', 'ENGINEERING_ACCEPTANCE_REQUIRED', 'UNKNOWN_REACTION', 'INVALID_KINETICS'],
    'experiment.start': ['PLANT_REQUIRED', 'POWER_OFF', 'EXPERIMENT_RUNNING'],
    'experiment.end': ['NO_EXPERIMENT', 'PLANT_REQUIRED', 'POWER_OFF', 'EMERGENCY_ACTIVE', 'OUTSIDE_SAFETY_LIMITS', 'ABOVE_BUBBLE_POINT'],
    'thermostat.set_tj': ['PLANT_REQUIRED', 'POWER_OFF', 'EMERGENCY_ACTIVE', 'OUTSIDE_SAFETY_LIMITS', 'ABOVE_BUBBLE_POINT'],
    'thermostat.set_tr': ['PLANT_REQUIRED', 'POWER_OFF', 'EMERGENCY_ACTIVE', 'OUTSIDE_SAFETY_LIMITS', 'VOLUME_BELOW_OPERATING_RANGE', 'ABOVE_BUBBLE_POINT'],
    'stirrer.set': ['PLANT_REQUIRED', 'POWER_OFF', 'EMERGENCY_ACTIVE', 'OUTSIDE_STIRRER_RANGE'],
    'stirrer.toggle': ['PLANT_REQUIRED', 'POWER_OFF', 'EMERGENCY_ACTIVE', 'STIRRER_SETPOINT_REQUIRED', 'OUTSIDE_STIRRER_RANGE'],
    'dosing.start': ['PLANT_REQUIRED', 'POWER_OFF', 'EMERGENCY_ACTIVE', 'DOSING_ACTIVE', 'UNKNOWN_SUBSTANCE', 'NOT_LIQUID', 'RATE_ABOVE_DOSING_UNIT',
                     'VOLUME_ABOVE_OPERATING_RANGE', 'ABOVE_BUBBLE_POINT', 'DRAIN_OPEN', 'USER_CANCEL', 'EMERGENCY', 'POWER_OFF'],
    'dosing.stop': ['NOT_DOSING'],
    'calibration.run': ['PLANT_REQUIRED', 'POWER_OFF', 'EMERGENCY_ACTIVE', 'BUSY', 'TR_MODE_REQUIRED', 'VOLUME_BELOW_OPERATING_RANGE', 'STIRRER_REQUIRED', 'DOSING_ACTIVE',
                        'DRAIN_OPEN', 'CONDITIONS_CHANGED', 'EMERGENCY', 'USER_CANCEL', 'NO_CALIBRATION_RESPONSE', 'CALIBRATION_NOT_STEADY'],
    'calibration.cp': ['PLANT_REQUIRED', 'POWER_OFF', 'EMERGENCY_ACTIVE', 'BUSY', 'TR_MODE_REQUIRED', 'VOLUME_BELOW_OPERATING_RANGE', 'STIRRER_REQUIRED', 'DOSING_ACTIVE',
                       'DRAIN_OPEN', 'UA_CALIBRATION_REQUIRED', 'OUTSIDE_SAFETY_LIMITS', 'ABOVE_BUBBLE_POINT', 'CONDITIONS_CHANGED', 'EMERGENCY', 'USER_CANCEL',
                       'NO_CALIBRATION_RESPONSE'],
    'calibration.result': ['UNKNOWN_OPERATION'], 'experiment.data': ['UNKNOWN_EXPERIMENT'],
    'calorimetry.evaluate': ['UNKNOWN_EXPERIMENT', 'WINDOW_TOO_SHORT', 'UA_CALIBRATION_REQUIRED', 'HEAT_CAPACITY_REQUIRED'],
    'emergency.trigger': ['PLANT_REQUIRED', 'POWER_OFF'], 'emergency.reset': ['PLANT_REQUIRED', 'POWER_OFF', 'NO_EMERGENCY', 'CAUSE_PERSISTS'],
    'reactor.discharge': ['PLANT_REQUIRED', 'DOSING_ACTIVE'],
}
_PARTIAL = {'USER_CANCEL', 'EMERGENCY', 'POWER_OFF', 'CONDITIONS_CHANGED'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主工程参数（室温、冷却介质、校准加热器先验、传感器偏差），没有可见模型变化',
    'safety.configure': '只写入安全限值与急停参数；越限引起的急停在sim.step中由状态灯段表现',
    'reactor.charge': '装料只改变过程物料账；Matterix场景与Blender模型都没有液位或料液的可见表示（玻璃反应器按空釜外观建模）',
    'reaction.configure': '只写入宿主动力学先验，没有可见模型变化',
    'experiment.start': '只开始实验记录，没有可见模型变化',
    'experiment.end': '结束实验记录；cool_to写入Tj目标，读数随sim.step演化（Tr/Tj读数绑定已在set_tr流程中验证）',
    'thermostat.set_tj': '只写入Tj目标；Tr/Tj读数随sim.step演化（display_tr_sim/display_tj_sim绑定已在set_tr流程中验证），本能力未在场景流程中单独执行',
    'dosing.start': '加料只改变过程物料账与放热；场景没有加料泵或液位的可见绑定，Tr/Tj读数随sim.step演化',
    'dosing.stop': '停止加料，没有可见模型变化',
    'calibration.run': '校准加热器没有可见绑定；Tr/Tj读数随sim.step演化',
    'calibration.cp': 'Tr斜坡在Tr/Tj读数中随sim.step演化；没有立即可见的变化',
    'calibration.result': '只读结果', 'calorimetry.evaluate': '只读分析（示值）', 'experiment.data': '只读数据', 'events.read': '只读事件',
    'ledger.read': '只读账目（evaluator）', 'provenance.read': '只读来源记录（evaluator）',
    'sim.step': '推进统一宿主时钟；读数、状态灯与关节随绑定状态变化，由各绑定在场景中验证',
}
_GUARDED = _CONTROL_GUARDED | _DATA_GUARDED


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    access = ['ACCESS_DENIED'] if (CAPS[k]['visibility'] != 'agent' or k in _GUARDED) else []
    return list(dict.fromkeys(_COMMON_ERRORS + access + CAP_ERRORS.get(k, [])))


OBSERVES = {
    'plant.configure': ['plant.configured'], 'power.set': ['power.enabled', 'status.ok', 'thermostat.mode'], 'power.toggle': ['power.enabled', 'status.ok'],
    'safety.configure': ['safety.tsafe_k', 'safety.tr_max_k', 'safety.tj_max_k', 'safety.rmax_rpm'], 'reactor.charge': ['reactor.charged_mass_kg'],
    'reaction.configure': [], 'experiment.start': ['experiment.running', 'experiment.id'], 'experiment.end': ['experiment.running'],
    'thermostat.set_tj': ['thermostat.mode', 'thermostat.tj_setpoint_k', 'jacket.indicated_k', 'thermostat.ramp_active'],
    'thermostat.set_tr': ['thermostat.mode', 'thermostat.tr_setpoint_k', 'reactor.indicated_k', 'thermostat.ramp_active'],
    'stirrer.set': ['stirrer.setpoint_rpm', 'stirrer.indicated_rpm'], 'stirrer.toggle': ['stirrer.setpoint_rpm', 'stirrer.indicated_rpm'],
    'dosing.start': ['dosing.active', 'dosing.indicated_mass_kg', 'reactor.indicated_k', 'calorimetry.heat_flow_w'], 'dosing.stop': ['dosing.active', 'dosing.indicated_mass_kg'],
    'calibration.run': ['calibration.running', 'cal_heater.on', 'calibration.ua_w_k', 'calibration.baseline_w'],
    'calibration.cp': ['calibration.running', 'calibration.heat_capacity_j_k', 'thermostat.tr_setpoint_k'], 'calibration.result': ['calibration.running'],
    'calorimetry.evaluate': ['calibration.ua_w_k', 'calibration.heat_capacity_j_k'], 'experiment.data': ['experiment.running'], 'events.read': ['emergency.active'],
    'emergency.trigger': ['emergency.active', 'emergency.program', 'status.ok', 'safety_relay.closed'], 'emergency.reset': ['emergency.active', 'status.ok'],
    'reactor.discharge': ['drain.open', 'reactor.charged_mass_kg'], 'sim.step': ['clock.time_s'],
}
_MAN, _PRE, _WB = 'manufacturer_primary', 'manufacturer_predecessor_manual', 'user_workbook'
CAP_SOURCES = {
    'plant.configure': ['simulation_assumption', _MAN, _PRE], 'power.set': [_MAN, _PRE], 'power.toggle': [_MAN], 'safety.configure': [_MAN, _PRE],
    'reactor.charge': ['reaction_mass_library', _PRE, 'simulation_assumption'], 'reaction.configure': ['reaction_mass_library', 'simulation_assumption'],
    'experiment.start': [_MAN], 'experiment.end': [_MAN], 'thermostat.set_tj': [_MAN, _PRE, 'thermal_calibration'], 'thermostat.set_tr': [_MAN, _PRE, 'thermal_calibration'],
    'stirrer.set': [_MAN, _PRE, _WB], 'stirrer.toggle': [_MAN], 'dosing.start': [_WB, 'reaction_mass_library', 'simulation_assumption'], 'dosing.stop': [_WB],
    'calibration.run': [_WB, 'rtcal_brochure', 'hfcal_product_page', 'simulation_assumption'], 'calibration.cp': ['rtcal_brochure', 'simulation_assumption'],
    'calibration.result': ['rtcal_brochure'], 'calorimetry.evaluate': [_WB, 'rtcal_brochure', 'hfcal_product_page', 'runtime_code'], 'experiment.data': [_MAN, _PRE],
    'events.read': [_MAN, _PRE], 'emergency.trigger': [_MAN, _PRE], 'emergency.reset': [_PRE], 'reactor.discharge': [_PRE, 'simulation_assumption'],
    'ledger.read': ['runtime_code'], 'provenance.read': ['reaction_mass_library'], 'sim.step': ['runtime_code', 'thermal_calibration'],
    'rtcal.enable': ['rtcal_brochure'], 'mode.adiabatic': [_PRE], 'mode.distillation': [_PRE], 'mode.crystallization': [_PRE], 'stirrer.high_torque': [_MAN],
    'reactor.select_type': [_MAN], 'purge.set': [_MAN], 'oil.fill_or_change': [_MAN, _PRE], 'hardware.connect': [_MAN], 'robot.assemble_reactor': ['simulation_assumption'],
    'accuracy.certification': [_MAN]}
PARAM_ZH = {
    'ambient_k': '实验室环境温度', 'coolant_kind': '冷却介质（水或低温槽乙醇/硅油）', 'coolant_inlet_k': '冷却介质进口温度（水约15°C、最高25°C）',
    'coolant_flow_m3_s': '冷却介质流量（≥10L/min）', 'cal_heater_power_w': 'HFCal校准加热器功率（先验；厂家未公开）',
    'tr_offset_k': 'Tr铂电阻静态偏差（A级≤0.5K）', 'tj_offset_k': 'Tj铂电阻静态偏差', 'tc_offset_k': 'Tc铂电阻静态偏差', 'sensor_noise_k': '铂电阻相关噪声标准差（相关时间20s）',
    'noise_seed': '噪声随机数种子', 'drain_flow_m3_s': '底阀全开时的放料流量', 'dosing_max_rate_kg_s': '外部加料单元最大质量流率', 'accept_engineering': '宿主显式接受工程参数',
    'substances': '装入物质与质量', 'temperature_k': '装料温度', 'viscosity_pa_s': '料液黏度先验（搅拌功率与液膜系数）', 'thermal_conductivity_w_m_k': '料液导热系数先验（液膜系数）',
    'accept_priors': '宿主显式接受先验', 'reactions': '反应与动力学先验', 'end_k': '任务终值温度', 'ramp_s': '线性斜坡时间（0=跳变）', 'controller': 'Tr控制参数组',
    'rpm': '搅拌转速', 'substance_id': '加料物质', 'rate_kg_s': '加料质量流率', 'total_mass_kg': '加料总质量', 'feed_temperature_k': '进料温度（null=室温）',
    'pre_s': '标定前基线时长', 'heat_s': '校准加热时长', 'post_s': '标定后基线时长', 'delta_k': '比热测定升温幅度', 'rate_k_min': '比热测定升温速率', 'hold_s': '比热测定前后保持时长',
    'limiting_reagent_mol': '评价用限量试剂摩尔数（给出反应焓）', 'heat_capacity_j_k': '评价用热容（null=用比热测定）',
}
LIMITATIONS = [
    '选定RC1mx + HFCal、标准转矩搅拌电机、MidTemp油与AP01-2玻璃反应器（玻璃锚式搅拌桨）；未选RTCal、高转矩搅拌与其他反应器，不实现绝热/蒸馏/结晶模式、惰性吹扫、油位与B2程序、iControl通信与真实硬件。',
    '恒温器为三节点集总模型（反应物料+插件、热油回路+夹套+玻璃、冷油箱），加热器2000W与约100W稳态冷却取自前代RC1e MidTemp；热油回路热容、散热、控制阀与安全阀交换系数、冷油箱热容、冷却盘管、冷回路泵热、顶部散热与蒸发系数、水在200rpm的总传热系数由RC1e厂家AP01-2典型曲线（空釜、1L/2L水、1L甘油+急停A）数字化后拟合（thermal_calibration.json，模型升温时间为厂家曲线的0.97–1.08倍、降温时间为0.87–1.08倍）；插件热容150J/K为工程值；RC1mx厂家未给同类曲线，这些常数是前代型号的集总工程值。',
    'UA=U·A_wet(V)：浸润夹套面积来自反应器内轮廓（工程几何），液膜系数按Nu∝Re^(2/3)Pr^(1/3)随转速、密度、比热、导热与黏度变化（黏度与导热为宿主先验，温度无关），壁与油膜导热phi为工程值；甘油曲线的高温U只用于拟合验证。',
    '反应物料为库中液体的摩尔账（NIST液体比热与生成焓、PubChem密度，理想混合体积、无热膨胀）；反应焓由生成焓导出，能量按生成焓+显热守恒更新（含Kirchhoff修正）；动力学（lg A、Ea、级数）全是宿主先验，库中没有；不含可逆平衡、多相、溶解热/混合热、蒸发吸热与回流。',
    '顶部散热=(L_R+K_EV·p_vap)(Tr−Ta)，p_vap用Raoult定律与由NIST正常沸点按Clausius–Clapeyron/Trouton规则（88J/(mol·K)）估算的纯物质蒸气压（水在40°C高估约一倍）；模型不含沸腾：Tr/Tj任务、结束冷却、比热斜坡、Tsafe以及加料与装料后的状态和目标都必须低于料液泡点−2K（AP01-2只允许常压以下）；料液仍达到泡点时（例如失控）发出BOILING_NOT_MODELLED警告并在评价中标记，温度不受沸腾限制。',
    '仪器按RC1e文档2s周期采样（0.01K分辨率、A级偏差≤0.5K、相关噪声）并执行Tj PI（工程增益Kp 1000W/K、Ti 60s，Kp·2s/C_j=0.22）与Tr串级控制（RC1e软件默认只用P：水溶液9、有机4.5；*_pi选项加I 180s），|Tj−Tr|≤T diff max，冷却中Tr仍上升（示值Tr近30s最小二乘斜率大于0.06K/min与0.24/s×声明噪声中的较大者，且超过其4倍自相关修正标准误差，工程阈值）时下限不起作用（RC1e 9.6）；串级输出保持在Tj min+1K…Tj max−1K之内（工程裕量，避免读数噪声误触发C/A）；Tj设定低于冷却介质+10K只记警告（厂家：控制质量要求）；通电与正常开机相同：Tj模式到Tsafe、搅拌回到断电前设定（RC1e 5.9）。',
    '急停程序按RC1e表：A（加热关、控制阀全开、安全阀旁通、安全继电器断开、切Tj模式）、B1（Tj模式从当前Tj示值以0.1K/s降到Tsafe，已低于Tsafe则保持）、C（Tj或Tr从当前示值以±0.1K/s到Tsafe），均关校准加热器、按Rsafe或保持转速并停止加料（加料停止为“安全联动”的工程解释）；触发：Tr>Tr max、Tj>Tj max（A），Tr<Tr min、Tj<Tj min、Tj超出油温限、Tc>Tj+5K、Tc超出(Tjoil min…40°C)（C），Tc>Tc max持续3min（A；RC1e表5.6印为3s、示例文字与报错为3min，采用3min），Rmin>0时手动停转搅拌3min或低于25rpm持续1min（B1；Rsafe=0由程序停转不算手动停转），同一周期两个以上原因→A；复位须立即原因已消除，带延时的原因复位后重新计时；油位、传感器失配、Tc跳变与通信故障未建模；冷油箱Tc只随拟合曲线间接校核，Tc>40°C触发C的时机为近似。',
    'HFCal：校准加热器功率为宿主先验（厂家未公开HFCal灵敏度）；UA标定取前后基线与加热段后半的(Tr−Tj)平均，不确定度按残差自相关修正的有效样本数计算，后半的显著漂移（>3倍标准误差）计入不确定度，超过信号5%判为未达稳态而失败（阈值为工程值）；比热由Tr斜坡求得，除以斜坡后半示值Tr的实测斜率；评价q_r=UA(Tr−Tj)+C·dTr/dt+ṁcp(Tr−T_feed)−q_cal−q_base，UA、基线与热容在前后标定之间按加料量（有加料时）或时间内插，只用示值；放热积分、反应焓、绝热温升、热转化率、累积度与MTSR不读取过程真值，都按窗口内放热归一，窗口末端热流未回到基线（窗口最后三分之一（≤10min）的平均值大于峰值5%与1倍q_r散度中的较大者）、窗口开始前150–30s已在放热（大于峰值5%与2倍散度）或窗口内达到泡点时给出质量标记；散度阈值按基线仿真标定（32040个基线窗口的最大值为0.83与1.63倍，evaluation_statistics_calibration.json），高噪声下低于该水平的残余热流检测不到（0.8kg水、0.02K噪声时约1.3–1.5W）；不含Stoessel危险等级（需要MTT与TMRad）。',
    '搅拌：锚式桨功率数Np=330/Re+0.35（工程关联式），转矩受玻璃搅拌器0.3N·m限制（超过则降速），电机加速100rpm/s；搅拌功率计入基线；未含涡流、液面变形与固体悬浮。',
    '外部加料单元（泵/天平）由宿主声明最大速率，经浸管恒速加入；底阀放料按宿主声明的流量与手柄开度线性流出；加料天平0.1g示值（加料结果、轮询与事件都按此示值）。',
    'agent只看到示值（Tr/Tj/Tc、转速、在线热流、标定结果、加料量、设定、安全与急停状态）；真实温度、UA、组成、反应功率、泡点与能量账只对evaluator可见，但以泡点或体积为界的拒绝可被用来逐步试探这些界限；某个agent启动的实验或正在进行的加料/标定，其他agent不能改任务、停止、取消或读取其数据；同一request_id无论被接受或被拒都绑定到第一次请求；急停按钮任何人都可按。',
    '时间推进：2s仪器周期内按反应放热（每子步绝热升温≤0.1K）与加料（每子步≤1%质量）自适应子步；热方程用Crank–Nicolson（能量流与状态更新一致），反应用隐式欧拉，加料与放料为精确混合；能量账残差为舍入量级，检查的是各子步状态更新与记账的一致性（热流在同一中点温度下积分、混合与反应按焓账），不是与独立积分的比较；搅拌轴转角按匀加速精确积分。',
    '没有真实硬件、iControl导出文件或厂家HFCal准确度验证；反应动力学不是经验证的反应数据（调研表：HFCal灵敏度、具体玻璃反应器容积与传热系数需附件资料与实验校准）。',
]


def export_mt_rc1mxhfcal_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'MtRc1mxhfcalSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation',
                                      window_description='Pure read of the latest 2 s instrument cycle; Pt100 readings at 0.01 K resolution with static offsets and correlated noise.'),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.mt_rc1mxhfcal.MtRc1mxhfcalSession.invoke',
                                              invocation=f'session.invoke(instance_id="rc1mx_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + '；被拒时状态不变。', state_ids=list(PRECONDITIONS[k][1]),
                                                                              enforced_by='runtime', check='MtRc1mxhfcalRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定、操作或状态标志；温度、转速、反应、加料量与标定进度只随step推进')
                                for s in v['effects']],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=dict(mode='none', description_zh='不支持：不实现，没有模型变化' if un else MODEL_NONE_ZH.get(k, '状态绑定到场景显示/关节（见合同model_bindings）'), binding_ids=[]),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=('加料达到总量（或停止/急停/断电）' if k == 'dosing.start' else '三段完成并算出UA与基线（或条件改变/急停/断电而失败）'
                                                      if k == 'calibration.run' else '保持-斜坡-保持完成并算出热容') if async_ else '原子设定、配置或查询，实际物理状态由step推进',
                                completion_state_ids=(['dosing.active', 'dosing.indicated_mass_kg'] if k == 'dosing.start' else ['calibration.running', 'calibration.ua_w_k']
                                                      if k == 'calibration.run' else ['calibration.running', 'calibration.heat_capacity_j_k']) if async_ else [],
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id',
                                resources=['host_clock', 'thermostat', 'reactor', 'calibration_heater', 'dosing_unit'],
                                partial_failure_zh='停止、急停、断电或条件改变只终止本操作；已加入的物料与已记录数据保留，失败的标定不更新UA/热容，终止的比热斜坡让Tr设定停在当前值。'),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH[e], side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=list(LIMITATIONS), source_ids=list(CAP_SOURCES[k]))
        if k in ('power.set', 'power.toggle', 'thermostat.set_tj', 'thermostat.set_tr', 'stirrer.set', 'stirrer.toggle', 'dosing.start', 'calibration.run', 'calibration.cp',
                 'emergency.trigger', 'reactor.discharge', 'reactor.charge'):
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh='由统一宿主时钟推进温度、转速、反应、加料、标定与急停逻辑')
                                   for s in SLOTS if s not in v['effects'] and SLOTS[s]['origin'] in ('sensor', 'derived', 'scene', 'process_truth', 'setpoint')]
        caps[k] = c
    params = {}
    for name, val in PLANT_FIT.items():
        params['fit_' + name.lower()] = dict(value=val, unit={'C_J': 'J/K', 'L_J': 'W/K', 'G_VALVE': 'W/K', 'G_SAFETY': 'W/K', 'C_C': 'J/K', 'UA_COIL': 'W/K', 'P_COLD': 'W',
                                                              'C_INS': 'J/K', 'L_R': 'W/K', 'K_EV': 'W/(K Pa)', 'U_W200': 'W/(m^2 K)'}[name],
                                             meaning_zh={'C_J': '热油回路+夹套+玻璃集总热容', 'L_J': '热油回路对室温散热系数', 'G_VALVE': '控制阀全开时冷油交换系数', 'G_SAFETY': '急停安全阀旁通附加交换系数',
                                                         'C_C': '冷油箱集总热容', 'UA_COIL': '冷却盘管传热系数', 'P_COLD': '冷回路泵热', 'C_INS': '反应物料节点中的插件热容（工程值）',
                                                         'L_R': '反应器顶部干散热系数（拟合到下限）', 'K_EV': '顶部蒸发-冷凝散热系数', 'U_W200': '水、200rpm玻璃锚式桨的总传热系数'}[name],
                                             evidence='assumption', source_ids=['thermal_calibration', _PRE] if name != 'C_INS' else ['simulation_assumption'],
                                             uncertainty='工程值' if name == 'C_INS' else '拟合估计值（非厂家直接给出）：由前代RC1e AP01-2典型曲线（示意曲线）数字化拟合；各曲线RMSE 1.3–4.2K')
    for name, (val, unit, meaning, src) in {'heater_power_w': (HEATER_POWER_W, 'W', '油路加热器功率（RC1e MidTemp）', _PRE), 'base_cooling_w': (BASE_COOLING_W, 'W', '稳态冷却约100W（RC1e）', _PRE),
                                            'tdiff_max_k': (TDIFF_MAX_K, 'K', '|Tj−Tr|限值（玻璃反应器）', _PRE), 'cycle_s': (CYCLE_S, 's', '测量周期（RC1e）', _PRE),
                                            'resolution_k': (RESOLUTION_K, 'K', 'Tj/Tr分辨率（RC1e）', _PRE), 'motor_torque_max_nm': (MOTOR_TORQUE_MAX_NM, 'N m', '标准转矩电机最大转矩', _MAN),
                                            'glass_stirrer_torque_max_nm': (GLASS_STIRRER_TORQUE_MAX_NM, 'N m', '玻璃搅拌器最大转矩（AP01-2）', _PRE),
                                            'tj_max_k': (TJ_MAX_K, 'K', 'MidTemp Tj上限230°C', _MAN), 'tj_device_min_k': (TJ_DEVICE_MIN_K, 'K', 'Tj设备下限−50°C', _MAN)}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=[src], uncertainty=None if src == _MAN else '前代RC1e数据用于RC1mx（相关型号）')
    for name, (val, unit, meaning) in {'phi_wall_w_m2k': (PHI_WALL_W_M2K, 'W/(m^2 K)', '4mm Duran壁+油膜导热（工程值）'), 'kp_tj_w_k': (KP_TJ_W_K, 'W/K', 'Tj PI比例增益（工程值）'),
                                       'ti_tj_s': (TI_TJ_S, 's', 'Tj PI积分时间（工程值）'), 'trouton_j_mol_k': (TROUTON_J_MOL_K, 'J/(mol K)', 'Trouton规则蒸发熵（蒸气压估算）'),
                                       'h_natural_w_m2k': (H_NATURAL_W_M2K, 'W/(m^2 K)', '无搅拌液膜系数（工程值）'), 'stirrer_accel_rpm_s': (STIRRER_ACCEL_RPM_S, 'rpm/s', '搅拌加速度（工程值）')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=['simulation_assumption'], uncertainty='工程值')
    mechanism = dict(
        component_ids=['column_body', 'oil_heater', 'oil_pump', 'control_valve', 'cooling_coil', 'oil_expansion_tank', 'reactor_jacket', 'reactor_vessel', 'jacket_oil',
                       'stirrer_motor', 'stirrer_impeller', 'tr_sensor', 'cal_heater', 'dip_tube', 'drain_valve_body'],
        state_ids=list(slots), meaning_zh='RC1mx恒温器三节点热模型与Tj/Tr串级控制、急停程序、AP01-2反应物料的库物质摩尔账与宿主动力学放热、加料与放料、HFCal标定与热流量热评价',
        implementation='lumped',
        equations=['C_r dT_r/dt = UA (T_j - T_r) - (L_R + K_EV p_vap(T_r)) (T_r - T_a) + P_stir + P_cal ; C_r = sum n_i cp_i + C_ins',
                   'C_j dT_j/dt = P_heater - UA (T_j - T_r) - L_j (T_j - T_a) - (u G_valve + s G_safety) (T_j - T_c)',
                   'C_c dT_c/dt = (u G_valve + s G_safety) (T_j - T_c) - H (T_c - T_w) - L_c (T_c - T_a) + P_pump ; H = C_w (1 - exp(-UA_coil / C_w))',
                   'UA = A_wet(V) / (1/phi + 1/h_r) ; h_r = h_ref (n/200)^(2/3) (rho/rho_w)^(2/3) (cp/cp_w)^(1/3) (k/k_w)^(2/3) (mu_w/mu)^(1/3)',
                   'T_j,set = T_r,set + P (T_r,set - T_r,m) [+ (P/I) int e dt], |T_j,set - T_r,m| <= T diff max ; heater / valve from the Tj PI with the ~100 W stationary cooling',
                   'r_k = 10^lgA exp(-Ea/(R T_r)) prod c_i^o_i V ; sum n_i (dfH_i + cp_i (T - 298.15)) + C_ins (T - 298.15) conserved over each reaction update',
                   'p_vap = sum x_i 101325 exp(-88 T_b,i / R (1/T - 1/T_b,i)) (Raoult, Clausius-Clapeyron/Trouton)',
                   'UA_cal = P_cal / (dT_heat - dT_base), dT = mean(T_r,m - T_j,m) ; C = UA [(T_j - T_r)_ramp - (T_j - T_r)_hold] / rate',
                   'q_r = UA (T_r,m - T_j,m) + C dT_r,m/dt + m_dot cp (T_r,m - T_feed) - P_cal - q_base ; Q_r = int q_r dt ; dT_ad = Q_r / C ; X = int q_r / Q_r ; MTSR = T_r + X_acc dT_ad'],
        parameters=params, source_ids=['simulation_assumption', 'reaction_mass_library', 'thermal_calibration', _PRE, _MAN], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_rc1mx_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'rc1_heat_flow_calorimetry': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'reactor.charge': (CHARGE, CHARGE_EXAMPLE), 'dosing.start': (DOSING, DOSING_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'rc1_heat_flow_calorimetry': mechanism})
