"""CHEM-150 IKA C 6000 isoperibol Package 2/12 (oxygen-bomb calorimeter with one C 6012 halogen-resistant decomposition vessel):
runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time; instrument cycle 1 s):
  * four lumped heat nodes -- decomposition-vessel shell and head (fast and slow part of the vessel), inner-vessel water with the
    inner-vessel metal, stirrer and probe, and the outer water vessel (jacket) -- coupled by the vessel-to-water conductances (only
    while the closed cover immerses the vessel in a filled inner vessel), the defined isoperibol exchange water <-> jacket, small
    leaks to the room and the cooling-water heat exchanger of the jacket; the jacket is regulated to the working temperature
    (22 / 25 / 30 C) by a PI-controlled heater against the cooling water; the inner water is filled from the cooling-water supply
    and brought to the start temperature by the inner-circuit heater; integrated by RK4 sub-steps with every energy flow booked
    from the same stages (the ledger closes to rounding);
  * the measurement sequence of the manual: close cover (lift), fill inner vessel, post-fill temperature check (45 s + 180 s, E07),
    O2 filling (optional flush), equalisation, pre-trial with drift criteria (E03), ignition (electrical energy measured, cotton
    thread), decomposition detection (E04), main trial with a stability criterion (E29), after-trial, evaluation, degassing,
    draining and opening; cancel before ignition keeps the sample (restartable), cancel after ignition does not;
  * combustion: the sample releases m q_V,gr from the CHEM-150 combustion template library (NIST-derived pure compounds, oxygen-bomb
    polymer values, benzoic acid with the NIST SRM 39j certified value and its bomb-condition factor) times the burned fraction
    (oxygen supply, pressed benzoic acid, volatile liquids without capsule lose vapour in the O2 flush), plus the cotton thread, the
    electrical ignition energy, combustion aids and the heat of HNO3 (from the residual air nitrogen and part of the sample N) and
    H2SO4 (all sample S) formation (Parr constants 14.1 cal/meq and 72.2 kcal/mol); the heat enters the vessel nodes instantly;
  * instrument: T1 inner-vessel temperature with a first-order probe lag, noise and 0.0001 K resolution, 12-s protocol averages
    (manual 11.5.5), Regnault-Pfaundler corrected rise (isoperibol) or a shortened main trial with exponential extrapolation
    (dynamic, IKA fast mode), Ho = (C dT - Qext) / m and C = (Ho,ref m + Qext) / dT, per-vessel and per-mode C-values from selected
    calibrations with the manual's statistics, evaluation with acid corrections and the net value Hu;
  * the adiabatic mode does not exist on the isoperibol model (workbook gap, manual 5.3).
"""
from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path

PROTOCOL_VERSION = '1.0.0'
ASSET_VERSION = '1.2.0'
SLUG = 'ika_c6000'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-150'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch03/t1_thermal' / SLUG
LIBRARY_PATH = ASSET / 'combustion_library.json'
MANIFEST_PATH = ASSET / 'geometry_manifest.json'

GAS_CONSTANT = 8.314462618
T_REF = 298.15
P_ATM = 101325.0
CP_WATER = 4181.0                               # J/(kg K) liquid water near 25 C (engineering constant)
CYCLE_S = 1.0                                   # instrument cycle (engineering)
CYCLE_NS = 1_000_000_000
PROTOCOL_S = 12.0                               # manual 11.5.5: protocol view shows averaged temperatures every 12 s
RESOLUTION_K = 1e-4                             # data sheet: temperature measurement resolution 0.0001 K
MAX_ENERGY_J = 40000.0                          # data sheet: measuring range max. 40000 J
SAMPLE_MASS_RANGE_KG = (1e-6, 5e-3)             # manual 11.5: 0.001 g to 5 g
WEIGH_RESOLUTION_KG = 1e-7                      # manual 11.5: weigh the substance to 0.1 mg
O2_NOMINAL_PA = 3.0e6                           # manual 6.1: 30 bar
O2_MAX_PA = 4.0e6                               # data sheet: oxygen operating pressure max. 40 bar
O2_PURITY_MIN = 0.995                           # manual 6.1: quality 3.5 (99.95 %) required; lower purities accepted by the model down to 99.5 %
WORK_TEMPS_K = {22: 295.15, 25: 298.15, 30: 303.15}      # data sheet: working temperatures 22 / 25 / 30 C
MODES = [f'{kind}_{t}' for kind in ('isoperibol', 'dynamic') for t in (22, 25, 30)]
COOLING_BANDS_K = {22: {'tap_water': (285.15, 293.15), 'chiller': (290.15, 293.15)},   # manual 5.3 table: cooling temperature per working
                   25: {'tap_water': (293.15, 296.15), 'chiller': (293.15, 296.15)},   # temperature (22 C: 12-20 C tap / 17-20 C condenser,
                   30: {'tap_water': (296.15, 300.15), 'chiller': (296.15, 300.15)}}   # 25 C: 20-23 C, 30 C: 23-27 C)
COOLING_LIMITS_K = (285.15, 300.15)             # data sheet: cooling medium 12-27 C (system test "red" outside)
COOLING_PRESSURE_MAX_PA = 1.5e5                 # data sheet: cooling medium permissible operating pressure 1.5 bar
COOLING_FLOW_NOMINAL_M3_S = 60e-3 / 3600.0      # data sheet: 60-70 l/h, recommended 60 l/h at 18 C
COOLING_FLOW_MIN_M3_S = 30e-3 / 3600.0          # E31/E32 flow too low below half the documented minimum (engineering threshold)
AMBIENT_LIMITS_K = (293.15, 303.15)             # data sheet: permissible ambient temperature 20-30 C
ROOM_BAND_K = 2.0                               # manual 5.3: working temperature within room temperature +-2 C for precise measurements
WARM_UP_S = 3600.0                              # manual 5.3: run hot for approx. 1 hour before measurements
SOFTWARE_LOAD_S = 30.0                          # manual 10.1: information screen about 30 s, cover opens automatically
SYSTEM_TEST_S = 300.0                           # manual 10.2: initial system start ca. 5 minutes (used for every power-on)
MODE_TEST_S = 60.0                              # manual 11.4: a working-mode change runs a system test (cooling water range); duration engineering
FACTORY_REFERENCE_J_KG = 26461.0e3              # manual 10.3 factory settings: reference combustion value 26461 (J/g)
O2_FLUSH_CYCLES = 2                             # manual 11.4.2: O2 flush = briefly filled with oxygen then emptied twice before the actual filling
Q_PER_MOL_O2 = 13.1e6 * 0.032                   # J per mol O2 consumed by organic combustion (~13.1 kJ per g O2, Huggett/Thornton; engineering)
LIMIT_TOL_K = 1e-9
MAX_VESSELS = 4                                 # brochure 2018: up to 4 decomposition vessels per device
PRESSURE_TEST_IGNITIONS = 1000                  # brochure 2018 FAQ: check the vessel after 1000 experiments or 1 year
COTTON_THREAD_J = 50.0                          # manual 11.4.3: "IKA cotton wool thread" preset with 50 J
HNO3_J_MOL = 14.1 * 4.184 * 1000.0              # Parr 435M appendix B: heat of formation of nitric acid 14.1 cal/meq
H2SO4_J_MOL = 72.2 * 4.184 * 1000.0             # Parr 435M appendix B: sulfuric acid from SO2 under bomb conditions 72.2 kcal/mol
M_S_KG_MOL = 0.03206
E04_RISE_K = 0.05                               # manual E04: required rise within the first minute after ignition (value engineering)
E04_WINDOW_S = 60.0
# ---- engineering constants of the lumped model (C 6000 internals are not published; see outputs/.../derive_plant_parameters.py)
PLANT_FIT = dict(C_METAL=350.0, WATER_KG=1.700, G_SHELL=40.0, G_HEAD=2.4, G_WJ=0.55, G_WA=0.08, G_BA=0.60,
                 C_JACKET=10000.0, G_JA=1.5, HX_EFF=0.30, P_JACKET_MAX=400.0, P_INNER_MAX=600.0, P_STIRRER=0.25)
VESSEL_VOLUME_M3 = {'C 6012': 0.22e-3, 'C 6010': 0.22e-3}   # free gas volume (not published; engineering)
VESSEL_HEAD_SHARE = 0.10                        # share of the vessel heat capacity in the slow head node (engineering; tau ~ 50 s)
WATER_FILL_SD_KG = 3e-4                         # repeatability of the automatic water fill (engineering)
COUPLING_SD = 0.06                              # run-to-run spread of the vessel-to-water heat transfer (vessel seat, stirring; engineering)
FILL_RATE_KG_S = 0.060                          # inner-vessel filling / draining (engineering)
LIFT_SPEED_M_S = 0.016                          # cover lift (about 12 s over 0.195 m, engineering)
LIFT_TRAVEL_M = 0.195                           # brochure 2018: height 425 mm closed, 620 mm open
STIRRER_RPM = 500.0                             # engineering
STIRRER_JOINT_MAX_RAD = 2 * math.pi
SENSOR_TAU_S = 2.0
SENSOR_NOISE_K = 1.0e-4
KP_JACKET_W_K, TI_JACKET_S = 400.0, 120.0
KP_INNER_W_K = 2000.0
IGNITION_MEAN_J = 30.0                          # electrical ignition energy (engineering; Parr 6300 fixed 10 cal for its heating wire)
IGNITION_SPREAD = 0.10
IGNITION_MEASURE_SD_J = 0.5
THREAD_SPREAD = 0.04
XI_AIR_HNO3 = 0.035                             # fraction of the residual N2 oxidised to HNO3 at 26.4 kJ (engineering; Parr fixed 8-10 cal)
XI_SAMPLE_N = 0.10                              # fraction of the sample nitrogen oxidised to HNO3 (engineering)
TITRATION_SD_MOL = 2e-5
O2_FILL_S, O2_FLUSH_S, DEGAS_S = 20.0, 25.0, 20.0
POST_FILL_WAIT_S, FILL_TEMP_WAIT_S = 45.0, 180.0   # manual E07: 45 s after filling, then another 180 s
FILL_TEMP_MARGIN_K = 0.3                        # fill water must be this much below the working temperature (engineering)
EQUALISE_TOL_K, EQUALISE_HOLD_S, EQUALISE_TIMEOUT_S = 0.010, 30.0, 600.0
PRE_MIN_S = {'isoperibol': 150.0, 'dynamic': 120.0}
PRE_TIMEOUT_S = 900.0
DRIFT_MAX_K_S, DRIFT_STEP_K_S = 3.0e-4, 4.0e-5  # pre-trial drift criteria on 60-s slopes (engineering)
MAIN_MIN_S = {'isoperibol': 210.0, 'dynamic': 150.0}
MAIN_MAX_S = 600.0
MAIN_LINEAR_K_S = 4.0e-5                        # main trial ends when two consecutive 60-s slopes differ by less (engineering)
AFTER_S = {'isoperibol': 150.0, 'dynamic': 0.0}
DYN_FIT_START_S = 36.0                          # dynamic extrapolation uses the main trial from t0 + 36 s
MAX_RECORDS = 400
MAX_EVENTS = 1000
MAX_POINTS = 400
AIDS = {'gelatine_capsule': 19500.0e3, 'paraffin_oil': 46000.0e3}   # J/kg label values of the combustion aids (engineering assumptions)
FORMS = ['pellet', 'powder', 'liquid', 'capsule']


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


# ---------------------------------------------------------------- template library
_LIBRARY = None


def _lib():
    global _LIBRARY
    if _LIBRARY is None:
        raw = json.loads(LIBRARY_PATH.read_text())
        _LIBRARY = dict(meta={k: v for k, v in raw.items() if k != 'substances'}, by_id={s['substance_id']: s for s in raw['substances']})
    return _LIBRARY


def library():
    """Deep copy of the combustion template library (substances by id + key values)."""
    return copy.deepcopy(_lib())


def h_vap_water_j_kg():
    return _lib()['meta']['key_values']['h_vap_water_J_g'] * 1000.0


def water_per_hydrogen():
    return _lib()['meta']['key_values']['water_per_hydrogen']


def benzoic_condition_factor(p_pa, m_sample_kg, m_water_kg, volume_m3, t_k):
    """NIST SRM 39j certificate: 1 + 1e-6 [200 (P - 3.0) + 42 (m_s/V - 3) + 30 (m_w/V - 3) - 45 (t - 25)], P in MPa, m in g, V in L."""
    v_l = volume_m3 * 1000.0
    return 1.0 + 1e-6 * (200.0 * (p_pa / 1e6 - 3.0) + 42.0 * (m_sample_kg * 1000.0 / v_l - 3.0) + 30.0 * (m_water_kg * 1000.0 / v_l - 3.0)
                         - 45.0 * (t_k - 273.15 - 25.0))


def vapour_pressure_pa(tb_k, t_k):
    """Clausius-Clapeyron with Trouton's rule (88 J/(mol K)) from the normal boiling point (engineering estimate)."""
    return P_ATM * math.exp(-88.0 * tb_k / GAS_CONSTANT * (1.0 / t_k - 1.0 / tb_k))


def quantize(value, step):
    """Value on the instrument grid, rounded to 10 decimals so that a documented limit such as 300.15 K compares exactly."""
    return round(round(value / step) * step, 10)


def linfit(ts, ys):
    n = len(ts)
    tm, ym = sum(ts) / n, sum(ys) / n
    sxx = sum((t - tm) ** 2 for t in ts)
    b = sum((t - tm) * (y - ym) for t, y in zip(ts, ys)) / sxx if sxx > 0 else 0.0
    return ym - b * tm, b


def mode_parts(mode):
    kind, t = mode.split('_')
    return kind, int(t)


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c) if isinstance(c, (list, tuple)) else [c], function_zh=meaning,
                   visibility=visibility, kind=kind, effects=[])


PLANT_EXAMPLE = dict(ambient_k=298.15, cooling_source='tap_water', cooling_water_k=294.65, cooling_flow_m3_s=COOLING_FLOW_NOMINAL_M3_S, cooling_pressure_pa=1.2e5,
                     oxygen_supply_pa=3.0e6, oxygen_purity=0.9995, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', AMBIENT_LIMITS_K[0], AMBIENT_LIMITS_K[1]), cooling_source=enum(['tap_water', 'chiller']), cooling_water_k=number('K', 278.15, 308.15),
                 cooling_flow_m3_s=number('m^3/s', 0.0, 1e-4), cooling_pressure_pa=number('Pa', 0.0, 6e5), oxygen_supply_pa=number('Pa', 0.0, 5e6),
                 oxygen_purity=number('1', O2_PURITY_MIN, 1.0), noise_seed=INT))
CONFIGURED = obj(dict(configured=BOOL))
OPERATION = obj(dict(operation_id=TEXT))
VESSEL_OUT = obj(dict(rfid=TEXT, model=enum(['C 6012', 'C 6010'])))
SAMPLE = obj(dict(substance_id=TEXT, mass_kg=number('kg', SAMPLE_MASS_RANGE_KG[0], SAMPLE_MASS_RANGE_KG[1]), form=enum(FORMS)))
AID_ITEM = obj(dict(kind=enum(list(AIDS)), mass_kg=number('kg', 1e-6, 2e-3)))
PREPARE = obj(dict(rfid=TEXT, sample=nullable(SAMPLE), aids=array(AID_ITEM, 0, 2), absorption_water_kg=number('kg', 0.0, 1e-2)))
PREPARE_EXAMPLE = dict(rfid='C6012-0001', sample=dict(substance_id='benzoic_acid', mass_kg=1.0e-3, form='pellet'), aids=[], absorption_water_kg=0.0)
PREPARE_OUT = obj(dict(rfid=TEXT, balance_sample_kg=nullable(number('kg')), balance_aids_kg=array(number('kg'), 0, 2)))
MEAS_AID = obj(dict(aid_name=TEXT, mass_kg=nullable(number('kg', 0.0, 5e-3))))
CREATE = obj(dict(name=dict(type='string', minLength=1, maxLength=30), sample_mass_kg=number('kg', SAMPLE_MASS_RANGE_KG[0], SAMPLE_MASS_RANGE_KG[1]),
                  kind=enum(['measurement', 'calibration']), vessel_name=nullable(TEXT), aids=array(MEAS_AID, 0, 6)))
CREATE_EXAMPLE = dict(name='BA_cal_1', sample_mass_kg=1.0e-3, kind='calibration', vessel_name=None, aids=[dict(aid_name='IKA cotton wool thread', mass_kg=None)])
PHASES = ['created', 'awaiting_vessel', 'closing', 'filling', 'fill_check', 'o2_filling', 'equalising', 'pre_trial', 'main_trial', 'after_trial', 'degassing',
          'draining', 'opening', 'completed', 'failed', 'cancelled_before_ignition', 'cancelled_after_ignition']
POINT = obj(dict(time_s=number('s'), t1_k=number('K')))
RESULT = obj(dict(measurement_id=TEXT, name=TEXT, kind=enum(['measurement', 'calibration']), status=enum(PHASES), mode=nullable(enum(MODES)),
                  vessel_name=nullable(TEXT), sample_mass_kg=number('kg'), error_code=nullable(TEXT),
                  delta_t_k=nullable(number('K')), ignition_energy_j=nullable(number('J')), q_ext_j=nullable(number('J')), acid_correction_j=nullable(number('J')),
                  total_heat_j=nullable(number('J')), c_value_j_k=nullable(number('J/K')),
                  gross_calorific_value_j_kg=nullable(number('J/kg')), reference_value_j_kg=nullable(number('J/kg')),
                  fore_drift_k_s=nullable(number('K/s')), after_drift_k_s=nullable(number('K/s')), ignition_time_s=nullable(number('s')),
                  main_end_time_s=nullable(number('s')), started_s=nullable(number('s')), finished_s=nullable(number('s')),
                  quality_flags=array(TEXT, 0, 12), points=array(POINT, 0, MAX_POINTS)))
EVAL = obj(dict(measurement_id=TEXT, sulfur_mass_fraction=number('1', 0.0, 0.5), total_acid_mol=nullable(number('mol', 0.0, 0.1)),
                hydrogen_mass_fraction=nullable(number('1', 0.0, 0.25)), moisture_mass_fraction=number('1', 0.0, 0.9)))
EVAL_EXAMPLE = dict(measurement_id='m-2', sulfur_mass_fraction=0.0, total_acid_mol=None, hydrogen_mass_fraction=0.0495, moisture_mass_fraction=0.0)
EVAL_OUT = obj(dict(measurement_id=TEXT, kind=enum(['measurement', 'calibration']), standard=TEXT, gross_uncorrected_j_kg=nullable(number('J/kg')),
                    nitric_acid_j=number('J'), sulfuric_acid_j=number('J'), gross_calorific_value_j_kg=nullable(number('J/kg')),
                    net_calorific_value_j_kg=nullable(number('J/kg')), c_value_uncorrected_j_k=nullable(number('J/K')), c_value_j_k=nullable(number('J/K'))))
SIM = obj(dict(measurement_id=TEXT, delta_t_k=number('K', 1e-4, 10.0), ignition_energy_j=number('J', 0.0, 500.0), c_value_j_k=nullable(number('J/K', 1000.0, 50000.0)),
               reference_value_j_kg=nullable(number('J/kg', 1e6, 6e7))))
SIM_OUT = obj(dict(measurement_id=TEXT, kind=enum(['measurement', 'calibration']), gross_calorific_value_j_kg=nullable(number('J/kg')), c_value_j_k=nullable(number('J/K'))))
STATS = obj(dict(vessel_name=TEXT, mode=enum(MODES), c_value_j_k=nullable(number('J/K')), n=INT, mean_j_k=nullable(number('J/K')), max_j_k=nullable(number('J/K')),
                 min_j_k=nullable(number('J/K')), range_j_k=nullable(number('J/K')), sigma_j_k=nullable(number('J/K')), rsd=nullable(number('1')),
                 uwl_j_k=nullable(number('J/K')), lwl_j_k=nullable(number('J/K')), ucl_j_k=nullable(number('J/K')), lcl_j_k=nullable(number('J/K'))))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'system', 'mode', 'vessel', 'measurement', 'warning', 'error', 'maintenance', 'data']), code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['jacket_heater_j', 'inner_heater_j', 'stirrer_j', 'ignition_j', 'thread_j', 'aid_j', 'sample_j', 'acid_j', 'cooling_removed_j', 'room_loss_j',
               'fill_sensible_j', 'drain_sensible_j', 'stored_change_j', 'energy_residual_j', 'water_filled_kg', 'water_drained_kg', 'water_in_vessel_kg',
               'water_residual_kg', 'o2_filled_mol', 'o2_vented_mol', 'o2_consumed_mol', 'o2_in_vessel_mol', 'o2_residual_mol']
LEDGER = obj({k: number('kg' if k.endswith('_kg') else 'mol' if k.endswith('_mol') else 'J') for k in LEDGER_KEYS})

cap('plant.configure', PLANT, CONFIGURED, PLANT_EXAMPLE, ['housing', 'condenser_in_fitting', 'oxygen_inlet', 'mains_socket'],
    '宿主声明实验室条件（可随时重新声明以模拟供水/供氧变化；随机数种子只在首次声明时生效）：室温（20–30°C）、冷却水来源（自来水经C 25减压阀或冷却器）、冷却水温度、流量（额定60–70L/h）与压力（≤1.5bar）、'
    '氧气供给绝对压力（经C 29减压阀，额定30bar、≤40bar）与纯度、噪声随机数种子', 'evaluator', 'configuration')
cap('vessel.provide', VESSEL_OUT, obj(dict(provided=BOOL)), dict(rfid='C6010-0002', model='C 6010'), 'decomposition_vessel',
    '宿主在实验台上放入另一个实体氧弹（C 6012或C 6010，带RFID编号）；Package 2/12自带一个C 6012（RFID C6012-0001）', 'evaluator', 'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), 'mains_switch',
    '右侧主电源开关：通电后软件加载约30s并自动开盖，随后自检约5min（冷却水温度、流量与温度检查）后进入等待；断电时若未先在菜单“关机”，回到上次关机保存的状态：'
    '其后新建的记录丢失、改动或删除的已保存记录恢复、标定选择与C值回到当时（手册10.3“按程序关机以免数据丢失”的工程解释）')
cap('power.toggle', obj({}), obj(dict(enabled=BOOL)), {}, 'mains_switch', '主电源翘板开关按压（Matterix弹簧键）：切换通电状态，语义同power.set')
cap('system.shutdown', obj({}), obj(dict(shutdown=BOOL)), {}, 'touch_display', '菜单“关机”（仅在等待状态）：保存测量、关盖、关闭软件；之后才应断开主电源（手册10.3）')
cap('error.acknowledge', obj({}), obj(dict(acknowledged=TEXT)), {}, 'touch_display', '确认当前错误信息，仪器回到“等待”状态（手册第13章各错误的处理）')
cap('mode.set', obj(dict(mode=enum(MODES))), obj(dict(mode=enum(MODES), jacket_setpoint_k=number('K'))), dict(mode='isoperibol_25'), ['touch_display', 'outer_vessel'],
    '工作模式：等温外套（isoperibol）或动态（dynamic），各有22/25/30°C起始温度；只显示与冷却水温度相符的模式（手册5.3表：22°C需冷却水12–20°C（冷却器17–20°C），25°C需20–23°C，30°C需23–27°C）；'
    '换模式后仪器运行一次自检（手册11.4，本资产约60s，工程值），外筒调节到新工作温度；本型号无绝热模式')
cap('settings.configure', obj(dict(o2_flush=BOOL, auto_vessel_detection=BOOL, reference_value_j_kg=number('J/kg', 2.0e7, 3.0e7))), CONFIGURED,
    dict(o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=26434.0e3), 'touch_display',
    '测量设置：充氧前冲洗（O2 flush：先充氧再排空两次以除去空气氮，出厂关闭）、自动氧弹识别（出厂打开）与标定用参考燃烧值（印在苯甲酸片包装上；出厂值26461J/g，使用NIST SRM 39j时应设为证书值26434J/g）')
cap('aid.define', obj(dict(list_no=integer(1, 3), name=dict(type='string', minLength=1, maxLength=30), specific_energy_j_kg=nullable(number('J/kg', 0.0, 1e8)),
                           absolute_energy_j=nullable(number('J', 0.0, 5000.0)))),
    CONFIGURED, dict(list_no=2, name='Gelatine capsule C 9', specific_energy_j_kg=19500.0e3, absolute_energy_j=None), 'touch_display',
    '点火/助燃辅料清单（外加能量1–3）：按单位质量热值（specific_energy_j_kg，J/kg）或绝对能量（absolute_energy_j，J）二选一登记；出厂“IKA cotton wool thread”50J不可修改删除')
cap('vessel.scan', obj(dict(rfid=TEXT)), obj(dict(rfid=TEXT, registered_name=nullable(TEXT))), dict(rfid='C6012-0001'), ['rfid_sensor_field', 'decomposition_vessel'],
    '把实体氧弹移过前沿RFID感应区：仪器读出其RFID编号（成功时蜂鸣）；自动识别打开时用于确定本次测量的氧弹；读卡失败（E30）未建模', 'agent', 'robot_skill')
cap('vessel.register', obj(dict(name=dict(type='string', minLength=1, maxLength=30), serial_number=dict(type='string', minLength=10, maxLength=10),
                                previous_ignitions=integer(0, 100000), platinum_wire=BOOL)), obj(dict(vessel_name=TEXT, rfid=TEXT)),
    dict(name='C6012 no. 1', serial_number='0012345678', previous_ignitions=0, platinum_wire=False), ['touch_display', 'rfid_sensor_field'],
    '登记氧弹（手册11.2.1）：名称、10位序列号、已点火次数、是否铂点火丝，绑定最近一次扫描的RFID；每台最多4个')
cap('vessel.prepare', PREPARE, PREPARE_OUT, PREPARE_EXAMPLE, ['decomposition_vessel', 'crucible_holder_cage'],
    '宿主/机器人在台面上准备氧弹：坩埚中称入库中样品（压片/粉末/液体/胶囊）与助燃物、接好棉线、按需加入吸收水并拧紧；返回0.1mg天平示值；氧弹须清洁且不在盖上', 'evaluator', 'robot_skill')
cap('vessel.hang', obj(dict(rfid=TEXT)), obj(dict(hung=BOOL)), dict(rfid='C6012-0001'), ['filling_head', 'decomposition_vessel'],
    '把已拧紧的氧弹挂入盖下充氧头（咔嗒到位）；须开盖、无其他氧弹挂着', 'agent', 'robot_skill')
cap('vessel.remove', obj({}), obj(dict(rfid=TEXT)), {}, ['filling_head', 'decomposition_vessel'], '从充氧头取下氧弹放到台面；须开盖且无测量进行（已排气）', 'agent', 'robot_skill')
cap('vessel.inspect', obj(dict(rfid=TEXT)), obj(dict(rfid=TEXT, state=TEXT, residue=enum(['none', 'clean', 'soot', 'unburnt_sample']), note_zh=TEXT)), dict(rfid='C6012-0001'),
    'decomposition_vessel', '打开已排气的氧弹检查坩埚与残渣（手册6.4：有未燃残渣须重做）', 'agent', 'robot_skill')
cap('washings.titrate', obj(dict(rfid=TEXT)), obj(dict(rfid=TEXT, total_acid_mol=number('mol'))), dict(rfid='C6012-0001'), 'decomposition_vessel',
    '用蒸馏水冲洗已燃烧并排气的氧弹内壁，洗液以NaOH滴定得到总酸（HNO3+2×H2SO4，mol当量，含滴定误差）；实验室分析步骤，不是C 6000功能', 'agent', 'analysis')
cap('vessel.clean', obj(dict(rfid=TEXT)), obj(dict(rfid=TEXT, state=TEXT)), dict(rfid='C6012-0001'), 'decomposition_vessel',
    '清洁并擦干已燃烧（或未用）的氧弹与坩埚（手册12.1.1），使其可再次准备', 'agent', 'robot_skill')
cap('vessel.set_c_value', obj(dict(vessel_name=TEXT, mode=enum(MODES), c_value_j_k=number('J/K', 1000.0, 50000.0))), CONFIGURED,
    dict(vessel_name='C6012 no. 1', mode='isoperibol_25', c_value_j_k=8700.0), 'touch_display',
    '手动输入某氧弹在某工作模式下的C值（手册11.2.3“Manual C-value”）：取代该模式基于标定的C值，标定选择清空（之后可重新选择标定）')
cap('vessel.select_calibrations', obj(dict(vessel_name=TEXT, mode=enum(MODES), measurement_ids=array(TEXT, 1, 50))), STATS,
    dict(vessel_name='C6012 no. 1', mode='isoperibol_25', measurement_ids=['m-1', 'm-2']), 'touch_display',
    '选择已完成的标定计入C值（手册11.2.3）：C值=所选标定平均值；统计给出平均、最大、最小、极差、σ、RSD与警告/控制限（±2σ/√N、±3σ/√N）')
cap('vessel.statistics', obj(dict(vessel_name=TEXT, mode=enum(MODES))), STATS, dict(vessel_name='C6012 no. 1', mode='isoperibol_25'), 'touch_display',
    '读取某氧弹某模式的C值与标定统计', 'agent', 'query')
cap('measurement.create', CREATE, obj(dict(measurement_id=TEXT)), CREATE_EXAMPLE, 'touch_display',
    '新建测量（手册11.5.1）：名称、称样量（手动或天平）、测量/标定、氧弹（null=自动识别）与所用点火/助燃辅料（比能量辅料须给质量）；标定需参考燃烧值，测量需该氧弹在当前模式下已有C值')
cap('measurement.start', obj(dict(measurement_id=TEXT)), OPERATION, dict(measurement_id='m-1'), ['touch_display', 'cover_shell', 'inner_vessel', 'outer_vessel', 'filling_head'],
    '启动测量（异步，手册11.5.4）：自动识别时先等待扫描并挂入氧弹（measurement.confirm确认），随后关盖、注水、温度检查、充氧（可冲洗）、平衡、前期、点火、主期、'
    '后期、计算、排气、排水、开盖；等温外套约15min（每小时约4次），动态约10min（约6次）')
cap('measurement.confirm', obj({}), obj(dict(measurement_id=TEXT)), {}, ['touch_display', 'filling_head'],
    '确认氧弹已拧紧并挂好（手册11.5.4第3–4步），仪器开始关盖与测量')
cap('measurement.result', obj(dict(measurement_id=TEXT)), RESULT, dict(measurement_id='m-1'), 'touch_display',
    '读取测量/标定结果：校正温升ΔT、测得点火能、外加能量合计、C值（标定评价后含酸校正）、热值Ho（J/kg）、测得总热量C·ΔT（total_heat_j）、前/后期漂移与12s平均温度曲线、质量标记', 'agent', 'query')
cap('measurement.evaluate', EVAL, EVAL_OUT, EVAL_EXAMPLE, 'touch_display',
    '按DIN 51900做酸校正（手册6.1：Qext含酸生成热；总酸滴定mol与硫含量→HNO3 14.1cal/meq、H2SO4 72.2kcal/mol）：测量给出校正后高位热值与低位热值Hu=Ho−h_vap,w(8.936w_H+w_水分)（不改记录）；'
    '标定则保存酸校正并得到C=(H_ref·m+Qext+Q_酸)/ΔT，正在使用该标定的C值随之重算', 'agent', 'analysis')
cap('measurement.simulate', SIM, SIM_OUT, dict(measurement_id='m-1', delta_t_k=3.05, ignition_energy_j=30.0, c_value_j_k=None, reference_value_j_kg=None), 'touch_display',
    '模拟计算（手册11.5.3）：用给定ΔT与点火能重算已有测量；C值（测量）或参考值（标定）默认取该记录自己的值，可改写；不改变记录', 'agent', 'analysis')
cap('measurement.delete', obj(dict(measurement_ids=array(TEXT, 1, 50))), obj(dict(deleted=array(TEXT, 0, 50))), dict(measurement_ids=['m-1']), 'touch_display',
    '删除测量或标定记录（“测量”菜单Delete）：进行中的记录与某氧弹C值正在使用的标定不能删除；agent只能删除自己的记录')
cap('archive.list', obj({}), obj(dict(measurements=array(obj(dict(measurement_id=TEXT, name=TEXT, kind=TEXT, status=enum(PHASES), saved=BOOL)), 0, MAX_RECORDS))), {},
    'touch_display', '归档列表（本调用者可见的测量与标定）', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), 'touch_display',
    '读取事件日志（电源、自检、模式、氧弹、测量、警告、错误）', 'agent', 'query')
cap('cover.open', obj({}), obj(dict(target_m=number('m'))), {}, ['cover_shell', 'lift_column', 'lift_drive'], '维护程序“Open Device”：升起盖子（约12s）；须通电、无测量进行')
cap('cover.close', obj({}), obj(dict(target_m=number('m'))), {}, ['cover_shell', 'lift_column', 'lift_drive'], '维护程序“Close Device”：降下盖子；须通电、无测量进行')
cap('maintenance.run', obj(dict(program=enum(['stirrer', 'flow_inner', 'flow_outer', 'rfid', 'o2_degas', 'fill_water', 'empty_water']))),
    obj(dict(program=TEXT, running=BOOL, stirrer_rpm=nullable(number('rpm')), flow_m3_s=nullable(number('m^3/s')))), dict(program='stirrer'),
    ['stirrer_shaft', 'circulation_pump', 'o2_valve_group', 'water_valve_group'],
    '维护程序（手册11.7.1）：搅拌开关（stirrer_rpm显示转速）、内/外水路流量检查（flow_m3_s）、RFID读取、给挂着的氧弹排气、手动注水/排水；须通电、自检完成、无测量进行')
cap('ledger.read', obj({}), LEDGER, {}, 'housing', '受限：能量账（外筒/内筒加热、搅拌、点火、棉线、助燃物、样品、酸、冷却水带走、室温散失、注水与排水显热、储热）与水、氧气物料账', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, substances=array(TEXT, 0, 200))), {}, 'decomposition_vessel', '受限：本实例使用过的模板库记录', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), 'housing',
    '唯一宿主时间推进（纳秒整数时钟，余数累计；1s仪器周期在全局时间网格上执行）', 'evaluator', 'simulation')
# error codes of the operating instructions (chapter 13, p.36-37) whose causes the simulation does not have: the evaluator raises them (fault.inject);
# the device shows the documented message, a running measurement ends with it (end sequence: degas, drain, open), and the documented remedy clears it:
# 'ack' = confirm the message (back to Wait), 'test' = confirm, then the system test runs again (Restart), 'power' = switch the device off and on.
# A cause that is still present shows the code again where the device would find it: at the next measurement start, system test or power-up.
MANUAL_FAULTS = {   # code: (displayed message, remedy, where a persisting cause shows again)
    'E01': ('Wrong stirrer rotation speed!', 'ack', 'start'), 'E02': ('Internal communication error', 'power', 'boot'),
    'E09': ('No ignition wire detected!', 'ack', 'start'), 'E10': ('Upper water sensor reacts too soon!', 'ack', 'start'),
    'E14': ('Temperature sensor at upper limit!', 'power', 'boot'), 'E15': ('Temperature sensor at lower limit!', 'power', 'boot'),
    'E20': ('No heat pulse in inner water cycle!', 'test', 'test'), 'E21': ('No heat pulse in outer water cycle!', 'test', 'test'),
    'E22': ('Error converter!', 'test', 'test'), 'E23': ('Error regulator!', 'test', 'test'),
    'E30': ('Error while reading the vessel ID!', 'ack', None)}          # E30: one failed RFID read (scan again); not kept as a cause
SWEEP_CAPS = ('fault.inject', 'fault.clear')
cap('fault.inject', obj(dict(code=enum(list(MANUAL_FAULTS)))), obj(dict(faults=array(TEXT, 0, 16))), dict(code='E22'), 'housing',
    '评测者：注入手册第13章错误码（E01、E02、E09、E10、E14、E15、E20–E23、E30）：屏上显示该错误，进行中的测量以该码结束（照常排气、排水、开盖）；按手册排除：E01/E09/E10/E30'
    '确认后回到等待，E20–E23确认后重新自检，E02/E14/E15须关机再开机；原因未撤销时在下次测量开始、自检或开机时再出现（E30只是一次读卡失败，须重新扫描）',
    'evaluator', 'simulation')
cap('fault.clear', obj({}), obj(dict(faults=array(TEXT, 0, 16))), {}, 'housing', '评测者：撤销全部注入的错误原因（代表维修）；屏上的错误仍按手册排除', 'evaluator', 'simulation')
# ---------------------------------------------------------------- touch-screen functions of the operating instructions (front panel, panel_ui.json)
_NAME30 = dict(type='string', minLength=1, maxLength=30)
LANGUAGES = ['English', 'Deutsch']                                    # assumption: the languages of the menu are not listed
UNITS = ['J/g', 'kJ/kg', 'cal/g', 'BTU/lb']                            # factory J/g (manual 10.4); the other units are an assumption
REPORTS = ['DIN 51900']               # the Report the manual names and shows (11.6.4 example); the input forms and formulas of other standards are
                                       # not in our manuals (they 'can be found in the respective standard'), so they are not offered (sync audit 2026-10-19)
PREFERENCES = obj(dict(language=enum(LANGUAGES), unit=enum(UNITS), decomposition=BOOL, input_takeover=BOOL, sample_name_from_date=BOOL, report=enum(REPORTS),
                       key_tones=BOOL, volume=integer(0, 10)))
FACTORY_PREFERENCES = dict(language='English', unit='J/g', decomposition=False, input_takeover=True, sample_name_from_date=True, report='DIN 51900', key_tones=True,
                           volume=5)                                    # manual 10.4 factory settings (volume: assumption)
MODULES = obj(dict(balance=BOOL, sample_rack=BOOL, printer=BOOL, tap_water=BOOL))
EPOCH0 = 1767225600.0                                                  # device clock 2026-01-01 00:00 at t = 0 (assumption)
MAX_USERS = 30
cap('measurement.cancel', obj({}), obj(dict(measurement_id=TEXT, cancelled_after_ignition=BOOL)), {}, ['touch_display', 'cover_shell'],
    '测量画面的取消键（手册11.5.5项D）：取消进行中的测量；点火前取消可再次启动，点火后不可；仪器照常排气、排水、开盖（进入这一结束程序后不能再取消）')
cap('measurement.modify', obj(dict(measurement_id=TEXT, name=_NAME30, sample_mass_kg=number('kg', SAMPLE_MASS_RANGE_KG[0], SAMPLE_MASS_RANGE_KG[1]),
                                   vessel_name=nullable(TEXT), aids=array(MEAS_AID, 0, 6), user=nullable(TEXT))), obj(dict(measurement_id=TEXT)),
    dict(measurement_id='m-1', name='BA_cal_1', sample_mass_kg=1.0e-3, vessel_name=None, aids=[dict(aid_name='IKA cotton wool thread', mass_kg=None)], user=None),
    'touch_display', '测量菜单Edit > Modify（手册11.5.2）：修改尚未点火的记录的名称、称样量、氧弹、点火/助燃辅料与用户（规则同新建测量）')
cap('measurement.convert', obj(dict(measurement_id=TEXT)), obj(dict(measurement_id=TEXT, kind=enum(['measurement', 'calibration']))), dict(measurement_id='m-1'),
    'touch_display', '测量菜单Edit > Meas. <-> calib.（手册11.5.2）：测量与标定互换；已完成的记录按当前C值（测量）或参考燃烧值（标定）重算；C值正在使用的标定不能转换')
cap('vessel.modify', obj(dict(vessel_name=TEXT, name=_NAME30, serial_number=dict(type='string', minLength=10, maxLength=10), previous_ignitions=integer(0, 100000),
                              platinum_wire=BOOL)), obj(dict(vessel_name=TEXT)),
    dict(vessel_name='C6012 no. 1', name='C6012 no. 1', serial_number='0012345678', previous_ignitions=0, platinum_wire=False), 'touch_display',
    '氧弹菜单Edit > Modify（手册11.2.2）：修改名称、序列号、已点火次数与铂丝；记录中的氧弹名随之改名')
cap('vessel.delete', obj(dict(vessel_name=TEXT)), obj(dict(deleted=BOOL)), dict(vessel_name='C6012 no. 1'), 'touch_display',
    '氧弹菜单Edit > Delete（手册11.2.2）：删除登记（测量进行中不能删除；已有记录保留氧弹名）')
cap('aid.delete', obj(dict(name=_NAME30)), CONFIGURED, dict(name='Gelatine capsule C 9'), 'touch_display',
    '点火/助燃辅料清单Edit > Delete（手册11.4.3）；出厂“IKA cotton wool thread”不可删除')
cap('user.save', obj(dict(name=_NAME30, abbreviation=dict(type='string', minLength=1, maxLength=5), replace=nullable(TEXT))), obj(dict(name=TEXT)),
    dict(name='Operator A', abbreviation='OA', replace=None), 'touch_display', '设置 > User：新建或修改用户（名称≤30字符且唯一、缩写，手册11.4.6；最多30个为假设）')
cap('user.delete', obj(dict(name=TEXT)), obj(dict(deleted=BOOL)), dict(name='Operator A'), 'touch_display', '设置 > User > Edit > Delete（手册11.4.6）')
cap('user.select', obj(dict(name=nullable(TEXT))), obj(dict(name=nullable(TEXT))), dict(name='Operator A'), 'touch_display',
    '新测量表单的User下拉（手册11.5.1）：当前用户，新建的测量记入该用户')
cap('preferences.configure', PREFERENCES, CONFIGURED, dict(FACTORY_PREFERENCES), 'touch_display',
    '设置：语言、单位（显示值随之换算）、测量设置中的Decomposition（试验后不自动排气，留作残余分析）、Input takeover（新测量沿用上次的氧弹、辅料与用户）、'
    'Sample name from date and time（按YYMMDDHHmmss自动命名）、Report（热值计算标准）、Audio（按键音、音量）（手册11.4）')
cap('clock.set', obj(dict(date=TEXT, time=TEXT)), obj(dict(datetime=TEXT)), dict(date='2026-10-19', time='08:30'), 'touch_display',
    '设置 > Date/time（手册11.4.8）：设备日期与时间（YYYY-MM-DD、HH:MM）')
cap('modules.configure', MODULES, CONFIGURED, dict(balance=False, sample_rack=False, printer=False, tap_water=True), ['touch_display', 'interface_panel'],
    '模块（手册11.3）：天平、样品架、打印机、Tap water（直接接自来水时须开，自检据此判断冷却方式）的启用；本配置没有连接天平、样品架与打印机')
cap('system.test_restart', obj({}), obj(dict(state=TEXT)), {}, 'touch_display', '功能栏“System test restart”：重新运行自检（等待状态，无测量）')
cap('system.test_skip', obj({}), obj(dict(test_result=nullable(enum(['green', 'yellow', 'red'])))), {}, 'touch_display',
    '功能栏“Skip Initial System Test”：不等完自检时长，按当前冷却水读数立即给出自检结果')
cap('system.factory_reset', obj({}), CONFIGURED, {}, 'touch_display',
    '维护 > Factory Reset：设置回到出厂状态（工作模式、测量设置、参考燃烧值、辅料、用户、单位、语言、报告、声音、模块）；登记的氧弹与测量记录保留（假设）')
cap('maintenance.o2_fill', obj({}), obj(dict(rfid=TEXT)), {}, ['filling_head', 'o2_valve_group', 'decomposition_vessel'],
    '维护程序“O2 Fill”（手册11.7.1）：给挂着的氧弹手动充氧（之后用O2 Degas排气）；须开盖、氧弹已准备或清洁且未带压')
cap('maintenance.change_seal', obj(dict(active=BOOL)), obj(dict(active=BOOL)), dict(active=True), ['filling_head'],
    '维护程序“Change Seal”（手册11.7.1）：充氧活塞降到最低位以便更换密封圈（须接氧气）；再次选择结束；进行中不能启动测量或充氧')
cap('device.empty_for_transport', obj({}), obj(dict(draining=BOOL)), {}, ['water_valve_group', 'discharge_fitting'],
    '维护 > Empty Device（手册11.7、12.1.3）：程序控制排空水路（内筒排水；外筒水路排空未建模）')
TOUCH_CAPS = ('measurement.cancel', 'measurement.modify', 'measurement.convert', 'vessel.modify', 'vessel.delete', 'aid.delete', 'user.save', 'user.delete',
              'user.select', 'preferences.configure', 'clock.set', 'modules.configure', 'system.test_restart', 'system.test_skip', 'system.factory_reset',
              'maintenance.o2_fill', 'maintenance.change_seal', 'device.empty_for_transport')   # touch-screen functions added in 1.1.0 (verified by the panel suite)
UNSUPPORTED = {
    'mode.adiabatic': '本型号（C 6000 isoperibol）没有绝热模式与绝热调整（只有C 6000 global standards有）',
    'sample_rack.use': 'C 5020样品架不在Package 2/12中',
    'data.export': '打印、U盘导出、以太网/FTP与CalWin C 6040通信未建模',
    'sample.halogen': '含卤素样品的HCl/Cl2生成与卤素酸校正未建模（C 6012虽耐卤素，模板库只收C/H/N/O/S物质）',
    'hardware.connect': '无真实串口/USB/以太网与仪器固件通信',
    'robot.handle_vessel': '无机器人拧紧氧弹、挂接与RFID移动的接触验证',
    'accuracy.certification': '无厂家标定证书、压力检验或不确定度实验',
}
UNSUPPORTED_COMPONENTS = {'mode.adiabatic': ['outer_vessel', 'touch_display'], 'sample_rack.use': ['interface_panel'], 'data.export': ['usb_front_port', 'interface_panel'],
                          'maintenance.change_seal': ['filling_head'], 'device.empty_for_transport': ['water_valve_group', 'discharge_fitting'],
                          'sample.halogen': ['decomposition_vessel'], 'hardware.connect': ['interface_panel'], 'robot.handle_vessel': ['decomposition_vessel', 'filling_head'],
                          'accuracy.certification': ['housing', 'decomposition_vessel']}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, UNSUPPORTED_COMPONENTS[k], v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


STATES = ['off', 'booting', 'system_test', 'wait', 'measuring', 'error', 'shutdown']
VESSEL_STATES = ['clean', 'prepared', 'pressurized', 'burned', 'burned_degassed', 'opened']
slot('clock.time_s', number('s'), 's', 'derived', 'housing', '宿主时钟；read不推进')
slot('plant.configured', BOOL, '1', 'derived', 'housing', '实验室条件已声明')
slot('power.enabled', BOOL, '1', 'derived', 'mains_switch', '整机通电')
slot('system.state', enum(STATES), '1', 'derived', 'touch_display', '仪器状态：off/booting/system_test/wait/measuring/error/shutdown')
slot('system.test_result', nullable(enum(['green', 'yellow', 'red'])), '1', 'derived', 'touch_display',
     '最近自检结果（绿=通过，黄=所选模式与冷却水不符须更改，红=冷却水温度超出12–27°C或水路流量过低E31/E32）；自检进行中或未自检为null')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'touch_display', '待确认的错误代码（E03、E04、E05、E07、E11、E24、E29、E31、E32、SYSTEM_TEST_RED、EVALUATION_FAILED）；无为null')
slot('system.warm_up_s', number('s'), 's', 'derived', 'electronics', '本次通电以来的时间（手册建议约1h预热）')
slot('mode.working', nullable(enum(MODES)), '1', 'setpoint', 'touch_display', '当前工作模式；未设置为null')
slot('mode.available', array(enum(MODES), 0, 6), '1', 'derived', 'touch_display', '按冷却水温度可选的工作模式（手册5.3表）')
slot('settings.o2_flush', BOOL, '1', 'setpoint', 'o2_valve_group', 'O2冲洗：正式充氧前先充氧再排空两次（手册11.4.2，降低残余空气氮）')
slot('settings.auto_vessel_detection', BOOL, '1', 'setpoint', 'rfid_sensor_field', '自动氧弹识别（测量开始前须扫描）')
slot('settings.reference_value_j_kg', number('J/kg'), 'J/kg', 'setpoint', 'touch_display', '标定用参考燃烧值（苯甲酸，J/kg）')
slot('cover.state', enum(['open', 'closed', 'moving']), '1', 'derived', 'cover_shell', '盖的状态（升降位置在两端时为open/closed）')
slot('stirrer.running', BOOL, '1', 'derived', 'stirrer_shaft', '搅拌运转中')
slot('stirrer.indicated_rpm', nullable(number('rpm')), 'rpm', 'sensor', 'stirrer_shaft', '搅拌转速示值（维护程序显示；断电为null）')
slot('inner.indicated_k', nullable(number('K')), 'K', 'sensor', 'temperature_probe', 'T1内筒温度示值（0.0001K分辨率、探头滞后与噪声，每1s）；断电为null')
slot('outer.indicated_k', nullable(number('K')), 'K', 'sensor', 'outer_vessel', 'T2外筒温度示值；断电为null')
slot('cooling_water.indicated_k', nullable(number('K')), 'K', 'sensor', 'condenser_in_fitting', 'T5冷却水温度示值；断电为null')
slot('ambient.indicated_k', nullable(number('K')), 'K', 'sensor', 'electronics', 'T6环境温度示值；断电为null')
slot('measurement.id', nullable(TEXT), '1', 'derived', 'touch_display', '进行中的测量ID；无为null')
slot('measurement.phase', nullable(enum(PHASES)), '1', 'derived', 'touch_display', '进行中测量的阶段；无为null')
slot('measurement.started_by_other_agent', BOOL, '1', 'derived', 'touch_display', '进行中的测量属于另一个agent（按调用者计算）')
slot('vessel.last_scanned_rfid', nullable(TEXT), '1', 'derived', 'rfid_sensor_field', '最近一次RFID读到的氧弹编号')
slot('vessel.hung_rfid', nullable(TEXT), '1', 'derived', 'filling_head', '挂在充氧头上的氧弹RFID（过程状态；模型中C 6012网格始终挂在盖下，不随此状态移动）；无为null')
slot('vessels.registered', {'type': 'object', 'additionalProperties': obj(dict(rfid=TEXT, serial_number=TEXT, ignitions=INT,
                                                                                c_values_j_k={'type': 'object', 'additionalProperties': nullable(number('J/K'))}))},
     '1', 'derived', 'touch_display', '已登记氧弹：RFID、序列号、点火次数与各模式C值')
slot('result.delta_t_k', nullable(number('K')), 'K', 'derived', 'touch_display', '最近完成测量的校正温升ΔT（结果画面）；无或断电后为null（记录仍在归档）')
slot('result.gross_calorific_value_j_kg', nullable(number('J/kg')), 'J/kg', 'derived', 'touch_display', '最近完成的一次运行为测量时的高位热值Ho（J/kg；标定后、无或断电后为null）')
slot('result.c_value_j_k', nullable(number('J/K')), 'J/K', 'derived', 'touch_display', '最近完成的一次运行为标定时得到的C值（测量后、无或断电后为null）')
slot('lift.position_m', number('m', 0.0, LIFT_TRAVEL_M), 'm', 'process_truth', 'cover_shell', '升降位置（0关盖，0.195m开盖；电机驱动；过程权威，镜像到盖子关节）', 'evaluator',
     '沿+Z，关盖为0。')
slot('stirrer.angle_rad', number('rad', 0.0, STIRRER_JOINT_MAX_RAD), 'rad', 'process_truth', 'stirrer_shaft', '搅拌轴转角（按转速积分，模一周；过程权威，镜像到搅拌轴关节）', 'evaluator',
     '绕+Z，俯视逆时针为正。')
for k, u, c, m in [('truth.shell_k', 'K', 'decomposition_vessel', '氧弹壳体节点真实温度'), ('truth.head_k', 'K', 'decomposition_vessel', '氧弹头部（慢节点）真实温度'),
                   ('truth.water_k', 'K', 'inner_vessel', '内筒水与内筒金属真实温度'), ('truth.jacket_k', 'K', 'outer_vessel', '外筒水真实温度'),
                   ('truth.water_kg', 'kg', 'inner_vessel', '内筒水质量'), ('truth.c_total_j_k', 'J/K', 'inner_vessel', '氧弹浸没时量热系统真实总热容（氧弹+水+内筒金属）'),
                   ('truth.jacket_heater_w', 'W', 'outer_vessel', '外筒加热功率'), ('truth.inner_heater_w', 'W', 'inner_vessel', '内水路加热功率'),
                   ('truth.cooling_w', 'W', 'outer_vessel', '冷却水带走功率'), ('truth.stirrer_w', 'W', 'stirrer_propeller', '搅拌输入功率'),
                   ('truth.last_heat_released_j', 'J', 'decomposition_vessel', '最近一次点火在氧弹内释放的总热（样品+棉线+点火+助燃物+酸）'),
                   ('truth.vessel_pressure_pa', 'Pa', 'decomposition_vessel', '挂着的氧弹内气体绝对压力（25°C等效）')]:
    slot(k, number(u), u, 'process_truth', c, m, 'evaluator',
         '绝对压力，按氧弹内全部气体物质的量在25°C（298.15K）与氧弹自由容积下的理想气体压力；未挂氧弹时为1atm' if k == 'truth.vessel_pressure_pa' else None)
slot('truth.vessels', {'type': 'object', 'additionalProperties': {'type': 'object'}}, '1', 'process_truth', 'decomposition_vessel',
     '各实体氧弹的真实状态（位置、内容、气体与酸摩尔数、燃烧分数）', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'housing', '评测者注入、尚未撤销的手册错误原因（第13章）', 'evaluator')


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


def _new_vessel(rfid, model, rng):
    c = (1200.0 if model == 'C 6012' else 1150.0) * (1.0 + rng.gauss(0.0, 0.005))      # individual vessel heat capacity (engineering)
    return dict(rfid=rfid, model=model, location='bench', state='clean', c_j_k=c, T_shell=None, T_head=None, ignitions=0, sample=None, aids=[],
                absorption_water_kg=0.0, gas=None, acids=dict(HNO3=0.0, H2SO4=0.0), burned_fraction=None, residue='none', vapour_lost_kg=0.0,
                inspection_required=False, last_heat_j=0.0)


PLANT_KEYS = ['ambient_k', 'cooling_source', 'cooling_water_k', 'cooling_flow_m3_s', 'cooling_pressure_pa', 'oxygen_supply_pa', 'oxygen_purity', 'noise_seed']
LEDGER_FLOWS = ['jacket_heater_j', 'inner_heater_j', 'stirrer_j', 'ignition_j', 'thread_j', 'aid_j', 'sample_j', 'acid_j', 'cooling_removed_j', 'room_loss_j',
                'fill_sensible_j', 'drain_sensible_j', 'water_filled_kg', 'water_drained_kg', 'o2_filled_mol', 'o2_vented_mol', 'o2_consumed_mol']


slot('measurements.list', array({'type': 'object'}, 0, MAX_RECORDS), '1', 'derived', 'touch_display',
     '测量与标定列表（名称、类型、状态、模式、氧弹、称样量、用户、温升、C值、热值；会话对agent只列本人记录）')
slot('aids.lists', {'type': 'object'}, '1', 'setpoint', 'touch_display', '外加能量清单1–3（名称、比能量或绝对能量、出厂条目标记）')
slot('users.list', array({'type': 'object'}, 0, MAX_USERS), '1', 'setpoint', 'touch_display', '用户（名称、缩写）')
slot('users.current', nullable(TEXT), '1', 'setpoint', 'touch_display', '新测量表单当前选中的用户')
slot('preferences', PREFERENCES, '1', 'setpoint', 'touch_display', '设置：语言、单位、Decomposition、Input takeover、按日期命名、Report、按键音、音量')
slot('modules.state', MODULES, '1', 'setpoint', 'interface_panel', '模块启用：天平、样品架、打印机、Tap water')
slot('device.datetime', TEXT, '1', 'derived', 'touch_display', '设备日期时间（ISO，随仿真时钟走）')
slot('maintenance.seal_change', BOOL, '1', 'derived', 'filling_head', '维护程序Change Seal进行中（充氧活塞在最低位）')
slot('simulation.last', nullable({'type': 'object'}), '1', 'derived', 'touch_display', '最近一次模拟计算的结果（显示在测量列表上）')
slot('evaluation.last', nullable({'type': 'object'}), '1', 'derived', 'touch_display', '最近一次标准评价的结果页（按Report）')


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.ika_c6000 (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import importlib.util
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


_panel_ui = _panel_engine()
PANEL_SPEC = _panel_ui.load_spec(ASSET / 'panel_ui.json', capabilities=set(CAPS))
PANEL_CAPS = _panel_ui.capability_definitions(PANEL_SPEC)                    # panel.press / touch / capture / frame (CAPS stays the instrument's command set)
PANEL_GESTURES = [k for k in PANEL_CAPS if k not in ('panel.capture', 'panel.frame')]
_PANELS = __import__('weakref').WeakKeyDictionary()                           # runtime -> its panel (kept out of the runtime's own data)


class IkaC6000Runtime:
    """C 6000 isoperibol with its C 6012 vessel: process model, instrument logic and evaluation (SI units)."""

    def __init__(self, instance_id='c6000_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.fit = dict(PLANT_FIT)
        self.rng = random.Random(0)
        self.Tw, self.Tj = T_REF, T_REF
        self.water_kg = 0.0
        self.probe_k, self.t2_lag = T_REF, T_REF
        self.readings = None
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.power_on_s = None
        self.shutdown_done = True
        self.test_result = None
        self.error = None
        self.faults = set()                              # causes of manual error codes injected by the evaluator (MANUAL_FAULTS)
        self.mode = 'isoperibol_22'                      # manual 10.2: an isoperibol device is preset to Isoperibol (22 C)
        self.settings = dict(o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=FACTORY_REFERENCE_J_KG)   # manual factory settings
        self.aid_lists = {1: {'IKA cotton wool thread': dict(energy_kind='absolute', value=COTTON_THREAD_J, fixed=True)}, 2: {}, 3: {}}
        self.lift, self.lift_target = 0.0, 0.0
        self.maint_stirrer = False
        self.stir_angle = 0.0
        self.p_jacket = self.p_inner = self.p_stir = 0.0
        self.jacket_int = 0.0
        self.water_target = 0.0
        self.g_factor = 1.0
        self.vessels = {}
        self.hung = None
        self.last_scanned = None
        self.registry = {}
        self.records = {}
        self.meas_seq = 0
        self.run = None
        self.operations, self.op_seq = {}, 0
        self.events, self.events_dropped = [], 0
        self.last_result = dict(delta_t=None, ho=None, c=None, mid=None)
        self.saved_copies = {}                           # records as last saved by a menu shutdown (restored if later changes are lost)
        self.saved_c_data = {}                           # per vessel: calibration selections, C-values and manual flags at the last shutdown
        self.immersed = False                            # vessel immersed in the filled inner vessel, latched on the 1 s instrument cycle
        self.test_len_s = SYSTEM_TEST_S
        self.used_substances = set()
        self.led = {k: _Sum() for k in LEDGER_FLOWS}
        self._E0 = None
        self._principals, self._requests, self._owners = {}, {}, {}
        self._caller = None                                                  # session principal while a session call runs (panel keys act as that caller)
        # touch-screen functions (operating instructions chapter 10-11): users, settings, modules, clock, maintenance states, last results
        self.users, self.current_user = [], None
        self.prefs = dict(FACTORY_PREFERENCES)
        self.modules = dict(balance=False, sample_rack=False, printer=False, tap_water=False)
        self.clock_offset = EPOCH0
        self.seal_change = False
        self.last_input = None
        self.last_simulation = self.last_evaluation = None
        self.power_epoch = 0
        self.panel_capture_dir = None

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
        return IkaC6000Session(self, name)

    def _event(self, kind, code, message, mid=None):
        """Event log entry; ``mid`` ties it to a measurement so that sessions can hide another agent's measurement events."""
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.time_s, kind=kind, code=code, message_zh=message, _mid=mid))

    def _work_k(self, mode=None):
        return WORK_TEMPS_K[mode_parts(mode or self.mode)[1]]

    def _c_water(self):
        return self.fit['C_METAL'] + self.water_kg * CP_WATER

    def _immersed(self):
        return self.hung is not None and self.lift <= 1e-6 and self.water_kg >= 0.9 * self.fit['WATER_KG']

    @staticmethod
    def _vessel_c(v):
        """Vessel heat capacity including the absorption water inside it (manual 11.1: use the same amount in calibration and measurement)."""
        return v['c_j_k'] + v['absorption_water_kg'] * CP_WATER

    def _vessel_nodes(self, v):
        c = self._vessel_c(v)
        return c * (1.0 - VESSEL_HEAD_SHARE), c * VESSEL_HEAD_SHARE

    @staticmethod
    def _pressurized(v):
        """Oxygen-filled or burned but not yet degassed: the vessel is under pressure (about 30 bar before, ~29 bar after ignition)."""
        return v['state'] in ('pressurized', 'burned')

    def _stored_energy(self):
        e = self._c_water() * (self.Tw - T_REF) + self.fit['C_JACKET'] * (self.Tj - T_REF)
        for v in self.vessels.values():
            cs, ch = self._vessel_nodes(v)
            e += cs * (v['T_shell'] - T_REF) + ch * (v['T_head'] - T_REF)
        return e

    def _available_modes(self, t5=None):
        if self.plant is None:
            return []
        t5 = self.plant['cooling_water_k'] if t5 is None else t5
        out = []
        for m in MODES:
            lo, hi = COOLING_BANDS_K[mode_parts(m)[1]][self.plant['cooling_source']]
            if lo - LIMIT_TOL_K <= t5 <= hi + LIMIT_TOL_K:
                out.append(m)
        return out

    def _cover_state(self):
        if self.lift <= 1e-9:
            return 'closed'
        if self.lift >= LIFT_TRAVEL_M - 1e-9:
            return 'open'
        return 'moving'

    def _stirring(self):
        return self.power and self.water_kg > 0.05 and (self.maint_stirrer or (self.run is not None and self.run['phase'] in (
            'filling', 'fill_check', 'o2_filling', 'equalising', 'pre_trial', 'main_trial', 'after_trial', 'degassing')))

    def _new_op(self, kind, **data):
        self.op_seq += 1
        op = dict(operation_id=f'{kind}-{self.op_seq}', kind=kind, phase='running', t0=self.time_s, reason=None, **data)
        self.operations[op['operation_id']] = op
        return op

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        m = self.readings if self.power else None
        hv = self.vessels.get(self.hung) if self.hung else None
        values = {
            'clock.time_s': self.time_s, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.test_result': self.test_result, 'system.error_code': self.error, 'system.warm_up_s': (self.time_s - self.power_on_s) if self.power_on_s is not None else 0.0,
            'mode.working': self.mode, 'mode.available': self._available_modes(self.readings['t5'] if self.readings else None) if self.power else [],
            'settings.o2_flush': self.settings['o2_flush'], 'settings.auto_vessel_detection': self.settings['auto_vessel_detection'],
            'settings.reference_value_j_kg': self.settings['reference_value_j_kg'], 'cover.state': self._cover_state(), 'stirrer.running': self._stirring(),
            'stirrer.indicated_rpm': (m['rpm'] if m else None), 'inner.indicated_k': m['t1'] if m else None, 'outer.indicated_k': m['t2'] if m else None,
            'cooling_water.indicated_k': m['t5'] if m else None, 'ambient.indicated_k': m['t6'] if m else None,
            'measurement.id': self.run['mid'] if self.run else None, 'measurement.phase': self.run['phase'] if self.run else None,
            'measurement.started_by_other_agent': False, 'vessel.last_scanned_rfid': self.last_scanned, 'vessel.hung_rfid': self.hung,
            'vessels.registered': {n: dict(rfid=r['rfid'], serial_number=r['serial_number'], ignitions=r['previous_ignitions'] + r['ignitions'],
                                           c_values_j_k={md: r['c'].get(md) for md in MODES}) for n, r in self.registry.items()},
            'result.delta_t_k': self.last_result['delta_t'], 'result.gross_calorific_value_j_kg': self.last_result['ho'], 'result.c_value_j_k': self.last_result['c'],
            'lift.position_m': self.lift, 'stirrer.angle_rad': self.stir_angle,
            'measurements.list': [self._list_entry(r) for r in self.records.values()],
            'aids.lists': {str(n): {k: dict(v) for k, v in lst.items()} for n, lst in self.aid_lists.items()},
            'users.list': copy.deepcopy(self.users), 'users.current': self.current_user, 'preferences': dict(self.prefs), 'modules.state': dict(self.modules),
            'device.datetime': self._iso(), 'maintenance.seal_change': self.seal_change, 'simulation.last': copy.deepcopy(self.last_simulation),
            'evaluation.last': copy.deepcopy(self.last_evaluation),
            'truth.shell_k': hv['T_shell'] if hv else self.Tw, 'truth.head_k': hv['T_head'] if hv else self.Tw, 'truth.water_k': self.Tw, 'truth.jacket_k': self.Tj,
            'truth.water_kg': self.water_kg, 'truth.c_total_j_k': self._c_water() + (self._vessel_c(hv) if hv else 0.0),
            'truth.jacket_heater_w': self.p_jacket, 'truth.inner_heater_w': self.p_inner, 'truth.cooling_w': self._cooling_w(), 'truth.stirrer_w': self.p_stir,
            'truth.last_heat_released_j': hv['last_heat_j'] if hv else 0.0, 'truth.vessel_pressure_pa': self._vessel_pressure(hv) if hv else P_ATM,
            'truth.faults': sorted(self.faults),
            'truth.vessels': {r: dict(model=v['model'], location=v['location'], state=v['state'], ignitions=v['ignitions'], sample=copy.deepcopy(v['sample']),
                                      gas_mol=copy.deepcopy(v['gas']), acids_mol=dict(v['acids']), burned_fraction=v['burned_fraction'], residue=v['residue'],
                                      vapour_lost_kg=v['vapour_lost_kg'], inspection_required=v['inspection_required'], heat_capacity_j_k=self._vessel_c(v),
                                      absorption_water_kg=v['absorption_water_kg'])
                              for r, v in self.vessels.items()},
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _cooling_w(self):
        if not (self.plant and self.power):
            return 0.0
        return self._g_cool() * (self.Tj - self.plant['cooling_water_k'])

    def _g_cool(self):
        return self.fit['HX_EFF'] * self.plant['cooling_flow_m3_s'] * 1000.0 * CP_WATER if (self.plant and self.power) else 0.0

    def _vessel_pressure(self, v):
        if not v or not v['gas']:
            return P_ATM
        n = sum(v['gas'].values())
        return n * GAS_CONSTANT * T_REF / VESSEL_VOLUME_M3[v['model']]

    def ledger(self):
        led = {k: v.value for k, v in self.led.items()}
        stored = (self._stored_energy() - self._E0) if self._E0 is not None else 0.0
        inputs = (led['jacket_heater_j'] + led['inner_heater_j'] + led['stirrer_j'] + led['ignition_j'] + led['thread_j'] + led['aid_j'] + led['sample_j']
                  + led['acid_j'] - led['cooling_removed_j'] - led['room_loss_j'] + led['fill_sensible_j'] - led['drain_sensible_j'])
        o2_in = sum(v['gas']['O2'] for v in self.vessels.values() if v['gas'])
        led.update(stored_change_j=stored, energy_residual_j=stored - inputs, water_in_vessel_kg=self.water_kg,
                   water_residual_kg=self.water_kg - (led['water_filled_kg'] - led['water_drained_kg']), o2_in_vessel_mol=o2_in,
                   o2_residual_mol=o2_in - (led['o2_filled_mol'] - led['o2_vented_mol'] - led['o2_consumed_mol']))
        return led

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id in PANEL_CAPS:
            out = self._panel_command(capability_id, a)
            if capability_id in PANEL_GESTURES:
                self.revision += 1
            return out
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

    def _need_ready(self):
        """Powered, software up, system test done, no measurement running, no pending error."""
        self._need_power()
        if self.state in ('booting', 'system_test', 'shutdown'):
            raise ActionRejected('NOT_READY')
        if self.run is not None:
            raise ActionRejected('MEASUREMENT_RUNNING')
        if self.error is not None:
            raise ActionRejected('ERROR_PENDING')

    def _cmd_plant_configure(self, **a):
        if a['cooling_pressure_pa'] > COOLING_PRESSURE_MAX_PA:
            raise ActionRejected('COOLING_PRESSURE_TOO_HIGH')
        first = self.plant is None
        if not first:
            a = dict(a, noise_seed=self.plant['noise_seed'])                   # the seed is fixed at the first declaration
        self.plant = dict(a)
        if first:
            self.rng = random.Random(a['noise_seed'])
            amb = a['ambient_k']
            self.Tw = self.Tj = self.probe_k = self.t2_lag = amb
            self.modules['tap_water'] = a['cooling_source'] == 'tap_water'       # the device is installed for the declared cooling water
            self.vessels = {'C6012-0001': _new_vessel('C6012-0001', 'C 6012', random.Random(a['noise_seed'] * 7919 + 1))}
            for v in self.vessels.values():
                v['T_shell'] = v['T_head'] = amb
            self._E0 = self._stored_energy()
        return dict(configured=True)

    def _cmd_vessel_provide(self, rfid, model):
        self._need_plant()
        if not rfid or rfid in self.vessels or len(self.vessels) >= 8:
            raise ActionRejected('INVALID_VESSEL')
        v = _new_vessel(rfid, model, random.Random(sum(map(ord, rfid)) * 131 + self.plant['noise_seed']))
        v['T_shell'] = v['T_head'] = self.plant['ambient_k']
        e0 = self._stored_energy()
        self.vessels[rfid] = v
        self._E0 += self._stored_energy() - e0                   # a vessel brought in at room temperature is not a heat input
        return dict(provided=True)

    def _fault_found(self, where):
        """An injected cause that is still present shows its code where the device finds it (power-up, system test, measurement start)."""
        code = next((c for c in sorted(self.faults) if MANUAL_FAULTS[c][2] == where), None)
        if code is not None:
            self._show_fault(code)
        return code is not None

    def _show_fault(self, code):
        """The device shows a manual error: a running measurement ends with it (its end sequence still runs), otherwise the error waits."""
        if self.run is not None and not self.run.get('final'):
            self._fail_run(code, f'{code}：{MANUAL_FAULTS[code][0]}（评测者注入）')
        elif self.run is not None:
            self.run['final'] = ('failed', code)
        else:
            self.error, self.state, self.state_t0 = code, 'error', self.time_s
            self._event('error', code, f'{code}：{MANUAL_FAULTS[code][0]}（评测者注入）')

    def _cmd_fault_inject(self, code):
        self._need_plant()
        if code == 'E30':
            self.last_scanned = None                                          # the read just failed: scan again
        else:
            if len(self.faults) >= 16 and code not in self.faults:
                raise ActionRejected('INVALID_ARGUMENT')
            self.faults.add(code)
        if self.power and self.state not in ('off', 'booting', 'shutdown'):
            self._show_fault(code)
        return dict(faults=sorted(self.faults))

    def _cmd_fault_clear(self):
        self._need_plant()
        self.faults.clear()
        self._event('fault', 'FAULT_CLEAR', '评测者撤销注入的错误原因（维修）')
        return dict(faults=[])

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            self.power = True
            self.power_epoch += 1
            self.state, self.state_t0 = 'booting', self.time_s
            self.test_len_s = SYSTEM_TEST_S
            self.power_on_s = self.time_s
            self.test_result = None
            self.error = None
            self.jacket_int = 0.0
            self.shutdown_done = False
            self._event('power', 'POWER_ON', '通电：软件加载约30s，盖子自动打开，随后自检')
        elif not enabled and self.power:
            self._power_off()
        return dict(enabled=self.power)

    def _power_off(self):
        if self.run is not None:
            self._abort_run('POWER_OFF', '断电中止测量：盖子停在当前位置，内筒水未排出，已充氧的氧弹未排气')
        if not self.shutdown_done:
            lost, reverted = [], []
            for mid, r in list(self.records.items()):
                if not r['saved'] and mid not in self.saved_copies:          # never saved: gone
                    del self.records[mid]
                    lost.append(mid)
            for mid, saved in self.saved_copies.items():                   # changed or deleted since the last shutdown: the saved version
                if mid not in self.records or not self.records[mid]['saved']:
                    self.records[mid] = copy.deepcopy(saved)
                    reverted.append(mid)
            changed = False
            for name, r in self.registry.items():                          # C-value data as of the last shutdown (registrations persist)
                old = self.saved_c_data.get(name) or dict(calibrations={m: [] for m in MODES}, c={m: None for m in MODES}, manual={m: False for m in MODES})
                if any(r[k] != old[k] for k in ('calibrations', 'c', 'manual')):
                    r.update(copy.deepcopy(old))
                    changed = True
            if lost or reverted or changed:
                self._rebuild_c_values()
                self._event('data', 'DATA_LOST', f'未经菜单关机直接断电：{len(lost)}条未保存的记录丢失，{len(reverted)}条恢复到上次关机保存的内容，'
                                                 f'C值与标定选择回到上次关机时（手册8.2.7）')
        self.power = False
        self.state = 'off'
        self.readings = None
        self.last_result = dict(delta_t=None, ho=None, c=None, mid=None)          # the result screen goes dark; the records stay in the archive
        self.maint_stirrer = False
        self.p_jacket = self.p_inner = self.p_stir = 0.0
        self.lift_target = self.lift
        self.water_target = self.water_kg
        self.power_on_s = None
        self._event('power', 'POWER_OFF', '断电')

    def _cmd_power_toggle(self):
        return self._cmd_power_set(not self.power)

    def _rebuild_c_values(self):
        """Selections may only reference existing completed calibrations; calibration-based C-values are recomputed (manual ones kept)."""
        for r in self.registry.values():
            for md in MODES:
                keep = [m for m in r['calibrations'][md] if m in self.records and self.records[m]['kind'] == 'calibration'
                        and self.records[m]['status'] == 'completed' and self.records[m]['c_value_j_k'] is not None]
                r['calibrations'][md] = keep
                if not r['manual'][md]:
                    r['c'][md] = sum(self.records[m]['c_value_j_k'] for m in keep) / len(keep) if keep else None

    def _cmd_system_shutdown(self):
        self._need_power()
        if self.run is not None:
            raise ActionRejected('MEASUREMENT_RUNNING')
        if self.error is not None:
            raise ActionRejected('ERROR_PENDING')
        if self.state != 'wait':                                          # manual 10.3: the device must be in Wait mode
            raise ActionRejected('NOT_READY')
        for r in self.records.values():
            r['saved'] = True
        self.saved_copies = {mid: copy.deepcopy(r) for mid, r in self.records.items()}
        self.saved_c_data = {n: copy.deepcopy(dict(calibrations=r['calibrations'], c=r['c'], manual=r['manual'])) for n, r in self.registry.items()}
        self.shutdown_done = True
        self.lift_target = 0.0
        self.maint_stirrer = False
        self.state, self.state_t0 = 'shutdown', self.time_s
        self._event('system', 'SHUTDOWN', '菜单关机：测量已保存，盖子关闭，软件关闭；现在可以断开主电源')
        return dict(shutdown=True)

    def _cmd_error_acknowledge(self):
        self._need_power()
        if self.error is None:
            raise ActionRejected('NO_ERROR')
        code = self.error
        if code in MANUAL_FAULTS and MANUAL_FAULTS[code][1] == 'power':
            raise ActionRejected('SWITCH_OFF_REQUIRED')                       # manual p.36-37: switch the device off and on
        self.error = None
        if code in ('SYSTEM_TEST_RED', 'E31', 'E32') or (code in MANUAL_FAULTS and MANUAL_FAULTS[code][1] == 'test'):
            self.state, self.state_t0 = 'system_test', self.time_s             # "Restart": the system test runs again
            self.test_len_s = SYSTEM_TEST_S
            self._event('system', 'SYSTEM_TEST_RESTART', '确认后重新自检')
        else:
            self.state, self.state_t0 = 'wait', self.time_s
        return dict(acknowledged=code)

    def _cmd_mode_set(self, mode):
        self._need_ready()
        if mode not in self._available_modes(self.readings['t5'] if self.readings else None):
            raise ActionRejected('RUN_MODE_NOT_POSSIBLE')
        self.mode = mode
        self.state, self.state_t0, self.test_len_s = 'system_test', self.time_s, MODE_TEST_S   # manual 11.4: a mode change runs a system test
        self.test_result = None
        self._event('mode', 'MODE', f'工作模式{mode}，外筒调节到{self._work_k() - 273.15:.0f}°C；自检运行')
        return dict(mode=mode, jacket_setpoint_k=self._work_k())

    def _cmd_settings_configure(self, o2_flush, auto_vessel_detection, reference_value_j_kg):
        self._need_power()
        if self.run is not None:
            raise ActionRejected('MEASUREMENT_RUNNING')
        self.settings.update(o2_flush=o2_flush, auto_vessel_detection=auto_vessel_detection, reference_value_j_kg=reference_value_j_kg)
        return dict(configured=True)

    def _cmd_aid_define(self, list_no, name, specific_energy_j_kg, absolute_energy_j):
        self._need_power()
        if (specific_energy_j_kg is None) == (absolute_energy_j is None):          # exactly one of J/kg or J
            raise ActionRejected('INVALID_ARGUMENT')
        for lst in self.aid_lists.values():
            if name in lst and lst[name].get('fixed'):
                raise ActionRejected('AID_FIXED')
        for lst in self.aid_lists.values():
            lst.pop(name, None)
        if specific_energy_j_kg is not None:
            self.aid_lists[list_no][name] = dict(energy_kind='specific', value=specific_energy_j_kg, fixed=False)
        else:
            self.aid_lists[list_no][name] = dict(energy_kind='absolute', value=absolute_energy_j, fixed=False)
        return dict(configured=True)

    # ---------------------------------------------------------------- vessel handling (robot / host skills)
    def _vessel(self, rfid):
        if rfid not in self.vessels:
            raise ActionRejected('UNKNOWN_VESSEL')
        return self.vessels[rfid]

    def _cmd_vessel_scan(self, rfid):
        self._need_power()
        if self.state in ('booting', 'shutdown'):
            raise ActionRejected('NOT_READY')
        v = self._vessel(rfid)
        if v['location'] != 'bench':
            raise ActionRejected('VESSEL_NOT_ON_BENCH')
        self.last_scanned = rfid
        if self.run is not None and self.run['phase'] == 'awaiting_vessel':
            self.run['scanned_after_start'] = rfid
        name = next((n for n, r in self.registry.items() if r['rfid'] == rfid), None)
        self._event('vessel', 'RFID', f'RFID读取{rfid}' + (f'（已登记为{name}）' if name else '（未登记）'))
        return dict(rfid=rfid, registered_name=name)

    def _cmd_vessel_register(self, name, serial_number, previous_ignitions, platinum_wire):
        self._need_ready()
        if self.last_scanned is None:
            raise ActionRejected('VESSEL_NOT_SCANNED')
        if name in self.registry or any(r['rfid'] == self.last_scanned for r in self.registry.values()):
            raise ActionRejected('VESSEL_ALREADY_REGISTERED')
        if len(self.registry) >= MAX_VESSELS:
            raise ActionRejected('TOO_MANY_VESSELS')
        if not serial_number.isalnum():
            raise ActionRejected('INVALID_ARGUMENT')
        self.registry[name] = dict(rfid=self.last_scanned, serial_number=serial_number, previous_ignitions=int(previous_ignitions), ignitions=0,
                                   platinum_wire=platinum_wire, calibrations={m: [] for m in MODES}, c={m: None for m in MODES}, manual={m: False for m in MODES})
        if previous_ignitions >= PRESSURE_TEST_IGNITIONS:
            self._event('warning', 'PRESSURE_TEST_DUE', f'{name}已达到建议点火次数{PRESSURE_TEST_IGNITIONS}：应做压力检验（可确认后继续使用）')
        return dict(vessel_name=name, rfid=self.last_scanned)

    def _cmd_vessel_prepare(self, rfid, sample, aids, absorption_water_kg):
        self._need_plant()
        v = self._vessel(rfid)
        if v['location'] != 'bench':
            raise ActionRejected('VESSEL_NOT_ON_BENCH')
        if v['state'] != 'clean':
            raise ActionRejected('VESSEL_NOT_CLEAN')
        if sample is not None:
            lib = _lib()['by_id']
            if sample['substance_id'] not in lib:
                raise ActionRejected('UNKNOWN_SUBSTANCE')
            rec = lib[sample['substance_id']]
            if rec['phase_298K'] == 'solid' and sample['form'] == 'liquid' or rec['phase_298K'] == 'liquid' and sample['form'] in ('pellet', 'powder'):
                raise ActionRejected('FORM_NOT_POSSIBLE')
            if sample['form'] == 'capsule' and not any(a['kind'] == 'gelatine_capsule' for a in aids):
                raise ActionRejected('CAPSULE_REQUIRED')
            self.used_substances.add(sample['substance_id'])
        e0 = self._stored_energy()
        v.update(state='prepared', sample=copy.deepcopy(sample), aids=copy.deepcopy(aids), absorption_water_kg=absorption_water_kg, gas=None,
                 acids=dict(HNO3=0.0, H2SO4=0.0), burned_fraction=None, residue='none', vapour_lost_kg=0.0, last_heat_j=0.0)
        self._E0 += self._stored_energy() - e0                   # absorption water poured in at the vessel temperature is not a heat input
        return dict(rfid=rfid, balance_sample_kg=quantize(sample['mass_kg'], WEIGH_RESOLUTION_KG) if sample else None,
                    balance_aids_kg=[quantize(a['mass_kg'], WEIGH_RESOLUTION_KG) for a in aids])

    def _cmd_vessel_hang(self, rfid):
        self._need_plant()
        v = self._vessel(rfid)
        if self._cover_state() != 'open':
            raise ActionRejected('COVER_NOT_OPEN')
        if self.hung is not None:
            raise ActionRejected('VESSEL_ALREADY_HUNG')
        if v['location'] != 'bench':
            raise ActionRejected('VESSEL_NOT_ON_BENCH')
        if v['state'] not in ('clean', 'prepared'):
            raise ActionRejected('VESSEL_NOT_READY')
        v['location'] = 'hung'
        self.hung = rfid
        return dict(hung=True)

    def _cmd_vessel_remove(self):
        self._need_plant()
        if self.hung is None:
            raise ActionRejected('NO_VESSEL_HUNG')
        if self.run is not None and self.run['phase'] != 'awaiting_vessel':
            raise ActionRejected('MEASUREMENT_RUNNING')
        if self._cover_state() != 'open':
            raise ActionRejected('COVER_NOT_OPEN')
        v = self.vessels[self.hung]
        if self._pressurized(v):                                    # filled, or burned and not degassed (e.g. after a power loss)
            raise ActionRejected('VESSEL_PRESSURIZED')
        v['location'] = 'bench'
        rfid, self.hung = self.hung, None
        return dict(rfid=rfid)

    def _cmd_vessel_inspect(self, rfid):
        self._need_plant()
        v = self._vessel(rfid)
        if v['location'] != 'bench':
            raise ActionRejected('VESSEL_NOT_ON_BENCH')
        if self._pressurized(v):
            raise ActionRejected('VESSEL_PRESSURIZED')
        if v['state'] == 'burned_degassed':
            v['state'] = 'opened'
        note = {'none': '坩埚未燃烧（无样品或未点火）', 'clean': '坩埚与内壁无可见未燃残渣',
                'soot': '坩埚与内壁有炭黑：燃烧不完全，结果不可用须重做（手册6.4）',
                'unburnt_sample': '坩埚中残留大量未燃样品：燃烧失败，须重做'}[v['residue']]
        return dict(rfid=rfid, state=v['state'], residue=v['residue'], note_zh=note)

    def _cmd_washings_titrate(self, rfid):
        self._need_plant()
        v = self._vessel(rfid)
        if v['location'] != 'bench':
            raise ActionRejected('VESSEL_NOT_ON_BENCH')
        if self._pressurized(v):
            raise ActionRejected('VESSEL_PRESSURIZED')
        if v['state'] not in ('burned_degassed', 'opened'):
            raise ActionRejected('VESSEL_NOT_BURNED')
        if v.get('titrated'):
            raise ActionRejected('ALREADY_TITRATED')
        v['titrated'] = True
        v['state'] = 'opened'
        total = v['acids']['HNO3'] + 2.0 * v['acids']['H2SO4']
        return dict(rfid=rfid, total_acid_mol=max(0.0, total + self.rng.gauss(0.0, TITRATION_SD_MOL)))

    def _cmd_vessel_clean(self, rfid):
        self._need_plant()
        v = self._vessel(rfid)
        if v['location'] != 'bench':
            raise ActionRejected('VESSEL_NOT_ON_BENCH')
        if self._pressurized(v):
            raise ActionRejected('VESSEL_PRESSURIZED')
        if v['gas']:                                                # residual gas at 1 atm leaves when the vessel is opened
            self.led['o2_vented_mol'].add(v['gas']['O2'])
        e0 = self._stored_energy()
        v.update(state='clean', sample=None, aids=[], absorption_water_kg=0.0, gas=None, acids=dict(HNO3=0.0, H2SO4=0.0), burned_fraction=None,
                 residue='none', vapour_lost_kg=0.0, titrated=False)
        self._E0 += self._stored_energy() - e0                   # washings and absorption water leave at the vessel temperature
        return dict(rfid=rfid, state='clean')

    # ---------------------------------------------------------------- C-values
    def _registered(self, name):
        if name not in self.registry:
            raise ActionRejected('VESSEL_NOT_REGISTERED')
        return self.registry[name]

    def _cmd_vessel_set_c_value(self, vessel_name, mode, c_value_j_k):
        self._need_power()
        r = self._registered(vessel_name)
        r['c'][mode] = c_value_j_k
        r['manual'][mode] = True
        r['calibrations'][mode] = []                                  # the C-value is no longer based on selected calibrations
        return dict(configured=True)

    def _stats(self, vessel_name, mode):
        r = self.registry[vessel_name]
        cs = [self.records[m]['c_value_j_k'] for m in r['calibrations'][mode] if m in self.records]
        out = dict(vessel_name=vessel_name, mode=mode, c_value_j_k=r['c'][mode], n=len(cs), mean_j_k=None, max_j_k=None, min_j_k=None, range_j_k=None,
                   sigma_j_k=None, rsd=None, uwl_j_k=None, lwl_j_k=None, ucl_j_k=None, lcl_j_k=None)
        if cs:
            mean = sum(cs) / len(cs)
            out.update(mean_j_k=mean, max_j_k=max(cs), min_j_k=min(cs), range_j_k=max(cs) - min(cs))
            if len(cs) > 1:
                sd = math.sqrt(sum((c - mean) ** 2 for c in cs) / (len(cs) - 1))
                k = sd / math.sqrt(len(cs))
                out.update(sigma_j_k=sd, rsd=sd / mean, uwl_j_k=mean + 2 * k, lwl_j_k=mean - 2 * k, ucl_j_k=mean + 3 * k, lcl_j_k=mean - 3 * k)
        return out

    def _cmd_vessel_select_calibrations(self, vessel_name, mode, measurement_ids):
        self._need_power()
        r = self._registered(vessel_name)
        if len(set(measurement_ids)) != len(measurement_ids):
            raise ActionRejected('INVALID_ARGUMENT')
        for mid in measurement_ids:
            rec = self.records.get(mid)
            if rec is None:
                raise ActionRejected('UNKNOWN_MEASUREMENT')
            if rec['kind'] != 'calibration' or rec['status'] != 'completed' or rec['c_value_j_k'] is None:
                raise ActionRejected('NOT_A_CALIBRATION')
            if rec['vessel_name'] != vessel_name or rec['mode'] != mode:
                raise ActionRejected('CALIBRATION_MISMATCH')
        r['calibrations'][mode] = list(measurement_ids)
        r['c'][mode] = sum(self.records[m]['c_value_j_k'] for m in measurement_ids) / len(measurement_ids)
        r['manual'][mode] = False
        return self._stats(vessel_name, mode)

    def _cmd_vessel_statistics(self, vessel_name, mode):
        self._registered(vessel_name)
        return self._stats(vessel_name, mode)

    # ---------------------------------------------------------------- measurements
    def _aid_def(self, name):
        for lst in self.aid_lists.values():
            if name in lst:
                return lst[name]
        raise ActionRejected('UNKNOWN_AID')

    def _cmd_measurement_create(self, name, sample_mass_kg, kind, vessel_name, aids):
        self._need_power()
        if self.state in ('booting', 'shutdown'):
            raise ActionRejected('NOT_READY')
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('ARCHIVE_FULL')
        if vessel_name is None and not self.settings['auto_vessel_detection']:
            raise ActionRejected('VESSEL_REQUIRED')
        if vessel_name is not None:
            self._registered(vessel_name)
        entered = []
        for a in aids:
            d = self._aid_def(a['aid_name'])
            if (d['energy_kind'] == 'specific') != (a['mass_kg'] is not None):
                raise ActionRejected('AID_MASS_MISMATCH')
            entered.append(dict(aid_name=a['aid_name'], energy_kind=d['energy_kind'], value=d['value'], mass_kg=a['mass_kg'],
                                energy_j=d['value'] if d['energy_kind'] == 'absolute' else d['value'] * a['mass_kg']))
        if sum(e['energy_j'] for e in entered) > MAX_ENERGY_J:
            raise ActionRejected('INVALID_ARGUMENT')
        self.meas_seq += 1
        mid = f'm-{self.meas_seq}'
        self.records[mid] = dict(measurement_id=mid, name=name, kind=kind, sample_mass_kg=sample_mass_kg, vessel_name=vessel_name, vessel_requested=vessel_name,
                                 aids=entered, status='created', c_value_raw_j_k=None, acid_correction_j=None,
                                 mode=None, error_code=None, created_s=self.time_s, started_s=None, finished_s=None, delta_t_k=None, ignition_energy_j=None,
                                 q_ext_j=None, c_value_j_k=None, gross_calorific_value_j_kg=None, reference_value_j_kg=None, fore_drift_k_s=None,
                                 after_drift_k_s=None, ignition_time_s=None, main_end_time_s=None, quality_flags=[], points=[], saved=False, ignited=False,
                                 vessel_rfid_truth=None, delta_t_cal=None, user=self.current_user)
        self.last_input = dict(vessel_name=vessel_name, aids=copy.deepcopy(aids), user=self.current_user)
        return dict(measurement_id=mid)

    def _record(self, mid):
        if mid not in self.records:
            raise ActionRejected('UNKNOWN_MEASUREMENT')
        return self.records[mid]

    def _cmd_measurement_start(self, measurement_id):
        rec = self._record(measurement_id)
        self._need_ready()
        if rec['status'] not in ('created', 'cancelled_before_ignition', 'failed') or rec['ignited']:
            raise ActionRejected('NOT_STARTABLE')
        if self.test_result != 'green' or self.mode not in self._available_modes(self.readings['t5'] if self.readings else None):
            raise ActionRejected('RUN_MODE_NOT_POSSIBLE')
        if self._cover_state() != 'open':
            raise ActionRejected('COVER_NOT_OPEN')
        if self.plant['oxygen_supply_pa'] > O2_MAX_PA:
            raise ActionRejected('O2_PRESSURE_TOO_HIGH')
        requested = rec['vessel_requested']
        if requested is not None and requested not in self.registry:
            raise ActionRejected('VESSEL_NOT_REGISTERED')
        if rec['kind'] == 'measurement' and requested is not None and self.registry[requested]['c'][self.mode] is None:
            raise ActionRejected('C_VALUE_REQUIRED')
        op = self._new_op('measurement', measurement_id=measurement_id)
        self.water_target = 0.0
        self.maint_stirrer = False
        rec.update(status='awaiting_vessel', mode=self.mode, error_code=None, started_s=self.time_s, finished_s=None, quality_flags=[], points=[],
                   vessel_name=requested, saved=False)
        self.run = dict(mid=measurement_id, op=op, phase='awaiting_vessel', t_phase=self.time_s, kind=rec['kind'], mode=self.mode,
                        vessel_name=requested, scanned_after_start=None, raw=[], t_ign=None, o2_filled=False, ignited=False, fill_target=None,
                        stable_since=None, t_main_end=None, fill_check_t=None, e_ign=None)
        self.state = 'measuring'
        self._event('measurement', 'START', f'测量{measurement_id}已启动：请扫描并挂入氧弹后确认', mid=measurement_id)
        self._fault_found('start')
        return dict(operation_id=op['operation_id'])

    def _cmd_measurement_confirm(self):
        self._need_power()
        run = self.run
        if run is None or run['phase'] != 'awaiting_vessel':
            raise ActionRejected('NOT_AWAITING_VESSEL')
        rec = self.records[run['mid']]
        name = run['vessel_name']
        if name is None:
            rfid = run['scanned_after_start']
            if rfid is None:
                raise ActionRejected('VESSEL_NOT_SCANNED')
            name = next((n for n, r in self.registry.items() if r['rfid'] == rfid), None)
            if name is None:
                raise ActionRejected('VESSEL_NOT_REGISTERED')
        if rec['kind'] == 'measurement' and self.registry[name]['c'][run['mode']] is None:
            raise ActionRejected('C_VALUE_REQUIRED')
        run['vessel_name'] = name
        reg = self.registry[name]
        run['c_value'] = reg['c'][run['mode']] if rec['kind'] == 'measurement' else None   # fixed for this run, with its basis:
        run['c_manual'] = rec['kind'] == 'measurement' and reg['manual'][run['mode']]
        run['cal_delta_t'] = [self.records[x]['delta_t_k'] for x in reg['calibrations'][run['mode']] if x in self.records]
        rec['vessel_name'] = name
        self._set_phase('closing')
        self.lift_target = 0.0
        return dict(measurement_id=run['mid'])

    def _set_phase(self, phase):
        self.run['phase'] = phase
        self.run['t_phase'] = self.time_s
        self.records[self.run['mid']]['status'] = phase if phase in PHASES else self.records[self.run['mid']]['status']

    def _abort_run(self, code, message):
        """Immediate abort (power loss): nothing is degassed, drained or opened."""
        run, rec = self.run, self.records[self.run['mid']]
        rec.update(status='failed', error_code=code, finished_s=self.time_s)
        rec['ignited'] = rec['ignited'] or run['ignited']
        self._finish_op(run['op'], 'failed', code)
        self.run = None
        self._event('error', code, message, mid=rec['measurement_id'])

    def _finish_op(self, op, phase, reason=None):
        op['phase'], op['reason'], op['t1'] = phase, reason, self.time_s

    def _cancel_run(self, op):
        run = self.run
        if run is None or run['op'] is not op:
            raise ActionRejected('NOT_RUNNING')
        if run['phase'] in ('degassing', 'draining', 'opening'):
            raise ActionRejected('NOT_CANCELLABLE')
        after = run['ignited']
        run['final'] = ('cancelled_after_ignition' if after else 'cancelled_before_ignition', 'USER_CANCEL')
        self._event('measurement', 'CANCEL', '测量被取消' + ('（点火后：不能再次启动）' if after else '（点火前：可再次启动）'), mid=run['mid'])
        if run['phase'] == 'awaiting_vessel':
            self._end_run()
        else:
            self._set_phase('degassing')
            self.p_inner = 0.0

    def _fail_run(self, code, message):
        """Device error during a run: the end sequence (degas, drain, open) still runs, then the error waits for acknowledgement."""
        run = self.run
        run['final'] = ('failed', code)
        self.p_inner = 0.0
        self._set_phase('degassing')
        self._event('error', code, message, mid=run['mid'])

    def _end_run(self):
        run = self.run
        rec = self.records[run['mid']]
        status, code = run.get('final', ('completed', None))
        rec.update(status=status, error_code=code, finished_s=self.time_s, ignited=rec['ignited'] or run['ignited'])
        if status == 'cancelled_before_ignition':
            rec['vessel_name'] = rec['vessel_requested']                  # a restart identifies its vessel again
        self._finish_op(run['op'], {'completed': 'succeeded', 'failed': 'failed'}.get(status, 'cancelled'), code)
        self.run = None
        if status == 'failed' and code:
            self.error = code
            self.state = 'error'
        else:
            self.state = 'wait'
        self._event('measurement', status.upper(), f'测量{rec["measurement_id"]}结束：{status}', mid=rec['measurement_id'])

    def _result(self, rec):
        """Record view; total_heat_j = C dT is the total heat the calorimeter measured (sample + external energies + acids)."""
        out = {k: copy.deepcopy(rec[k]) for k in ('measurement_id', 'name', 'kind', 'status', 'mode', 'vessel_name', 'sample_mass_kg', 'error_code', 'delta_t_k',
                                                  'ignition_energy_j', 'q_ext_j', 'acid_correction_j', 'c_value_j_k', 'gross_calorific_value_j_kg', 'reference_value_j_kg',
                                                  'fore_drift_k_s', 'after_drift_k_s', 'ignition_time_s', 'main_end_time_s', 'started_s', 'finished_s',
                                                  'quality_flags', 'points')}
        out['total_heat_j'] = rec['c_value_j_k'] * rec['delta_t_k'] if rec['c_value_j_k'] is not None and rec['delta_t_k'] is not None else None
        return out

    def _cmd_measurement_result(self, measurement_id):
        return self._result(self._record(measurement_id))

    def _cmd_measurement_evaluate(self, measurement_id, sulfur_mass_fraction, total_acid_mol, hydrogen_mass_fraction, moisture_mass_fraction):
        """DIN 51900 acid corrections (manual 6.1: Q_ext includes the heat of acid formation).  A measurement is evaluated without changing
        its record (Ho corrected, Hu); a calibration stores its acid correction, so its C-value = (H_ref m + Q_ext + Q_acid) / dT, and a
        C-value that currently uses this calibration is recomputed."""
        rec = self._record(measurement_id)
        if rec['status'] != 'completed' or rec['delta_t_k'] is None or \
                (rec['kind'] == 'measurement' and rec['gross_calorific_value_j_kg'] is None) or (rec['kind'] == 'calibration' and rec['c_value_raw_j_k'] is None):
            raise ActionRejected('NOT_EVALUABLE')
        m = rec['sample_mass_kg']
        n_s = sulfur_mass_fraction * m / M_S_KG_MOL
        q_s = n_s * H2SO4_J_MOL
        n_hno3 = max(0.0, (total_acid_mol if total_acid_mol is not None else 0.0) - 2.0 * n_s)
        q_n = n_hno3 * HNO3_J_MOL
        if rec['kind'] == 'calibration':
            c = rec['c_value_raw_j_k'] + (q_n + q_s) / rec['delta_t_k']
            rec.update(c_value_j_k=c, acid_correction_j=q_n + q_s, saved=False)
            for name, r in self.registry.items():
                for md in MODES:
                    if measurement_id in r['calibrations'][md] and not r['manual'][md]:
                        r['c'][md] = sum(self.records[x]['c_value_j_k'] for x in r['calibrations'][md]) / len(r['calibrations'][md])
                        self._event('data', 'C_VALUE_UPDATED', f'{name}（{md}）的C值按酸校正后的标定更新为{r["c"][md]:.1f}J/K', mid=measurement_id)
            out = dict(measurement_id=measurement_id, kind='calibration', standard=self.prefs['report'], gross_uncorrected_j_kg=None, nitric_acid_j=q_n,
                       sulfuric_acid_j=q_s, gross_calorific_value_j_kg=None, net_calorific_value_j_kg=None, c_value_uncorrected_j_k=rec['c_value_raw_j_k'], c_value_j_k=c)
            self.last_evaluation = dict(out)
            return out
        ho = rec['gross_calorific_value_j_kg'] - (q_n + q_s) / m
        hu = None
        if hydrogen_mass_fraction is not None:
            hu = ho - h_vap_water_j_kg() * (water_per_hydrogen() * hydrogen_mass_fraction + moisture_mass_fraction)
        out = dict(measurement_id=measurement_id, kind='measurement', standard=self.prefs['report'], gross_uncorrected_j_kg=rec['gross_calorific_value_j_kg'],
                   nitric_acid_j=q_n, sulfuric_acid_j=q_s, gross_calorific_value_j_kg=ho, net_calorific_value_j_kg=hu, c_value_uncorrected_j_k=None,
                   c_value_j_k=rec['c_value_j_k'])
        self.last_evaluation = dict(out)
        return out

    def _cmd_measurement_simulate(self, measurement_id, delta_t_k, ignition_energy_j, c_value_j_k, reference_value_j_kg):
        """Manual 11.5.3: the record's own values are the defaults; a calibration keeps its stored acid correction."""
        rec = self._record(measurement_id)
        q_ext = ignition_energy_j + sum(a['energy_j'] for a in rec['aids'])
        m = rec['sample_mass_kg']
        if rec['kind'] == 'calibration':
            ref = reference_value_j_kg if reference_value_j_kg is not None else (rec['reference_value_j_kg'] if rec['reference_value_j_kg'] is not None
                                                                                  else self.settings['reference_value_j_kg'])
            out = dict(measurement_id=measurement_id, kind='calibration', gross_calorific_value_j_kg=None,
                       c_value_j_k=(ref * m + q_ext + (rec['acid_correction_j'] or 0.0)) / delta_t_k)
            self.last_simulation = dict(out, delta_t_k=delta_t_k, ignition_energy_j=ignition_energy_j)
            return out
        c = c_value_j_k if c_value_j_k is not None else rec['c_value_j_k']
        if c is None:
            vn, md = rec['vessel_name'], rec['mode'] or self.mode
            c = self.registry[vn]['c'][md] if vn in self.registry else None
        if c is None:
            raise ActionRejected('C_VALUE_REQUIRED')
        out = dict(measurement_id=measurement_id, kind='measurement', gross_calorific_value_j_kg=(c * delta_t_k - q_ext) / m, c_value_j_k=c)
        self.last_simulation = dict(out, delta_t_k=delta_t_k, ignition_energy_j=ignition_energy_j)
        return out

    def _cmd_measurement_delete(self, measurement_ids):
        """Measurement menu "Delete": not the running record and not a calibration a C-value is based on (manual 11.5.2 / menu tree)."""
        self._need_power()
        if self.state in ('booting', 'shutdown'):
            raise ActionRejected('NOT_READY')
        if len(set(measurement_ids)) != len(measurement_ids):
            raise ActionRejected('INVALID_ARGUMENT')
        for mid in measurement_ids:
            self._record(mid)
            if self.run is not None and self.run['mid'] == mid:
                raise ActionRejected('MEASUREMENT_RUNNING')
            if any(mid in r['calibrations'][md] and not r['manual'][md] for r in self.registry.values() for md in MODES):
                raise ActionRejected('RECORD_IN_USE')
        for mid in measurement_ids:
            del self.records[mid]                                          # a saved copy stays until the next menu shutdown
            self._event('data', 'DELETED', f'记录{mid}已删除', mid=mid)
        return dict(deleted=list(measurement_ids))

    def _cmd_archive_list(self):
        return dict(measurements=[dict(measurement_id=r['measurement_id'], name=r['name'], kind=r['kind'], status=r['status'], saved=r['saved'])
                                  for r in self.records.values()])

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_mid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    # ---------------------------------------------------------------- cover and maintenance
    def _cmd_cover_open(self):
        self._need_ready()
        self.lift_target = LIFT_TRAVEL_M
        return dict(target_m=self.lift_target)

    def _cmd_cover_close(self):
        self._need_ready()
        self.lift_target = 0.0
        return dict(target_m=self.lift_target)

    def _cmd_maintenance_run(self, program):
        self._need_ready()
        rpm = flow = None
        running = False
        if program == 'stirrer':
            self.maint_stirrer = not self.maint_stirrer
            running = self.maint_stirrer
            rpm = STIRRER_RPM if (running and self.water_kg > 0.05) else 0.0
        elif program == 'flow_outer':
            flow = self.plant['cooling_flow_m3_s']
        elif program == 'flow_inner':
            flow = self.plant['cooling_flow_m3_s'] if self.plant['cooling_pressure_pa'] >= 0.3e5 else 0.0
        elif program == 'rfid':
            self._event('maintenance', 'RFID_READ', f'RFID维护读取：{self.last_scanned or "无"}')
        elif program == 'o2_degas':
            if self.hung is None:
                raise ActionRejected('NO_VESSEL_HUNG')
            self._degas(self.vessels[self.hung])
        elif program == 'fill_water':
            self.water_target = self.fit['WATER_KG']
            running = True
        elif program == 'empty_water':
            self.water_target = 0.0
            running = True
        return dict(program=program, running=running, stirrer_rpm=rpm, flow_m3_s=flow)

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        return dict(library=str(LIBRARY_PATH.relative_to(ROOT)), substances=sorted(self.used_substances))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.time_s)

    # ---------------------------------------------------------------- touch-screen functions (operating instructions chapter 10-11)
    def _iso(self):
        return self._iso_at(self.time_s)

    def _iso_at(self, t_s):
        import datetime
        return datetime.datetime.fromtimestamp(self.clock_offset + t_s, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')

    def _list_entry(self, r):
        return {k: r.get(k) for k in ('measurement_id', 'name', 'kind', 'status', 'mode', 'vessel_name', 'sample_mass_kg', 'user', 'created_s', 'finished_s',
                                      'delta_t_k', 'c_value_j_k', 'gross_calorific_value_j_kg', 'error_code', 'saved', 'ignited')}

    def _entered_aids(self, aids):
        out = []
        for a in aids:
            d = self._aid_def(a['aid_name'])
            if (d['energy_kind'] == 'specific') != (a['mass_kg'] is not None):
                raise ActionRejected('AID_MASS_MISMATCH')
            out.append(dict(aid_name=a['aid_name'], energy_kind=d['energy_kind'], value=d['value'], mass_kg=a['mass_kg'],
                            energy_j=d['value'] if d['energy_kind'] == 'absolute' else d['value'] * a['mass_kg']))
        if sum(e['energy_j'] for e in out) > MAX_ENERGY_J:
            raise ActionRejected('INVALID_ARGUMENT')
        return out

    def _need_user(self, name):
        if name is not None and not any(u['name'] == name for u in self.users):
            raise ActionRejected('UNKNOWN_USER')

    def _cmd_measurement_cancel(self):
        self._need_power()
        if self.run is None:
            raise ActionRejected('NOT_RUNNING')
        mid, after = self.run['mid'], self.run['ignited']
        self._cancel_run(self.run['op'])
        return dict(measurement_id=mid, cancelled_after_ignition=after)

    def _cmd_measurement_modify(self, measurement_id, name, sample_mass_kg, vessel_name, aids, user):
        self._need_power()
        rec = self._record(measurement_id)
        if self.run is not None and self.run['mid'] == measurement_id:
            raise ActionRejected('MEASUREMENT_RUNNING')
        if rec['ignited'] or rec['status'] not in ('created', 'cancelled_before_ignition', 'failed'):
            raise ActionRejected('NOT_MODIFIABLE')
        if vessel_name is None and not self.settings['auto_vessel_detection']:
            raise ActionRejected('VESSEL_REQUIRED')
        if vessel_name is not None:
            self._registered(vessel_name)
        self._need_user(user)
        entered = self._entered_aids(aids)
        rec.update(name=name, sample_mass_kg=sample_mass_kg, vessel_name=vessel_name, vessel_requested=vessel_name, aids=entered, user=user, saved=False)
        return dict(measurement_id=measurement_id)

    def _cmd_measurement_convert(self, measurement_id):
        self._need_power()
        rec = self._record(measurement_id)
        if self.run is not None and self.run['mid'] == measurement_id:
            raise ActionRejected('MEASUREMENT_RUNNING')
        if any(measurement_id in r['calibrations'][md] for r in self.registry.values() for md in MODES):
            raise ActionRejected('RECORD_IN_USE')                           # manual 11.5.2: a calibration used for a C-value cannot be converted
        new = 'calibration' if rec['kind'] == 'measurement' else 'measurement'
        if rec['status'] == 'completed' and rec['delta_t_k']:
            dt, m, q = rec['delta_t_k'], rec['sample_mass_kg'], rec['q_ext_j'] or 0.0
            if new == 'calibration':
                ref = self.settings['reference_value_j_kg']
                c = (ref * m + q) / dt
                rec.update(kind=new, reference_value_j_kg=ref, c_value_raw_j_k=c, c_value_j_k=c, acid_correction_j=None, gross_calorific_value_j_kg=None)
            else:
                c = self.registry[rec['vessel_name']]['c'].get(rec['mode']) if rec['vessel_name'] in self.registry else None
                if c is None:
                    raise ActionRejected('C_VALUE_REQUIRED')
                rec.update(kind=new, reference_value_j_kg=None, c_value_raw_j_k=None, c_value_j_k=c, acid_correction_j=None, gross_calorific_value_j_kg=(c * dt - q) / m)
        else:
            rec['kind'] = new
        rec['saved'] = False
        return dict(measurement_id=measurement_id, kind=new)

    def _cmd_vessel_modify(self, vessel_name, name, serial_number, previous_ignitions, platinum_wire):
        self._need_ready()
        r = self._registered(vessel_name)
        if name != vessel_name and name in self.registry:
            raise ActionRejected('VESSEL_ALREADY_REGISTERED')
        if not serial_number.isalnum():
            raise ActionRejected('INVALID_ARGUMENT')
        r.update(serial_number=serial_number, previous_ignitions=int(previous_ignitions), platinum_wire=platinum_wire)
        if name != vessel_name:
            self.registry = {(name if k == vessel_name else k): v for k, v in self.registry.items()}
            for rec in self.records.values():
                for key in ('vessel_name', 'vessel_requested'):
                    if rec[key] == vessel_name:
                        rec[key] = name
            if self.last_input and self.last_input['vessel_name'] == vessel_name:
                self.last_input['vessel_name'] = name
        return dict(vessel_name=name)

    def _cmd_vessel_delete(self, vessel_name):
        self._need_ready()
        self._registered(vessel_name)
        del self.registry[vessel_name]
        for rec in self.records.values():                                  # created measurements of this vessel fall back to automatic detection
            if rec['status'] == 'created' and rec['vessel_requested'] == vessel_name:
                rec['vessel_requested'] = rec['vessel_name'] = None
        if self.last_input and self.last_input['vessel_name'] == vessel_name:
            self.last_input['vessel_name'] = None
        self._event('vessel', 'DELETED', f'氧弹登记{vessel_name}已删除')
        return dict(deleted=True)

    def _cmd_aid_delete(self, name):
        self._need_power()
        d = self._aid_def(name)
        if d.get('fixed'):
            raise ActionRejected('AID_FIXED')
        for lst in self.aid_lists.values():
            lst.pop(name, None)
        return dict(configured=True)

    def _cmd_user_save(self, name, abbreviation, replace):
        self._need_power()
        if replace is not None and not any(u['name'] == replace for u in self.users):
            raise ActionRejected('UNKNOWN_USER')
        if name != replace and any(u['name'] == name for u in self.users):
            raise ActionRejected('USER_EXISTS')
        if replace is None and len(self.users) >= MAX_USERS:
            raise ActionRejected('TOO_MANY_USERS')
        rec = dict(name=name, abbreviation=abbreviation)
        if replace is None:
            self.users.append(rec)
        else:
            self.users = [rec if u['name'] == replace else u for u in self.users]
            if self.current_user == replace:
                self.current_user = name
        return dict(name=name)

    def _cmd_user_delete(self, name):
        self._need_power()
        self._need_user(name)
        self.users = [u for u in self.users if u['name'] != name]
        if self.current_user == name:
            self.current_user = None
        return dict(deleted=True)

    def _cmd_user_select(self, name):
        self._need_power()
        self._need_user(name)
        self.current_user = name
        return dict(name=name)

    def _cmd_preferences_configure(self, **a):
        self._need_power()
        if self.run is not None and a['decomposition'] != self.prefs['decomposition']:
            raise ActionRejected('MEASUREMENT_RUNNING')
        self.prefs = dict(a)
        return dict(configured=True)

    def _cmd_clock_set(self, date, time):
        import datetime
        self._need_power()
        try:
            t = datetime.datetime.strptime(date + 'T' + time, '%Y-%m-%dT%H:%M').replace(tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        self.clock_offset = t - self.time_s
        return dict(datetime=self._iso())

    def _cmd_modules_configure(self, **a):
        self._need_power()
        if self.run is not None:
            raise ActionRejected('MEASUREMENT_RUNNING')
        self.modules = dict(a)
        return dict(configured=True)

    def _cmd_system_test_restart(self):
        self._need_ready()
        self.state, self.state_t0, self.test_len_s = 'system_test', self.time_s, SYSTEM_TEST_S
        self.test_result = None
        self._event('system', 'SYSTEM_TEST_RESTART', '功能栏：重新自检')
        return dict(state=self.state)

    def _cmd_system_test_skip(self):
        self._need_power()
        if self.state != 'system_test' or self.readings is None:
            raise ActionRejected('NOT_IN_SYSTEM_TEST')
        self._system_test_result()
        self._event('system', 'SYSTEM_TEST_SKIPPED', '功能栏：跳过自检等待，按当前读数给出结果')
        return dict(test_result=self.test_result)

    def _cmd_system_factory_reset(self):
        self._need_ready()
        self.mode = 'isoperibol_22'
        self.settings = dict(o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=FACTORY_REFERENCE_J_KG)
        self.aid_lists = {1: {'IKA cotton wool thread': dict(energy_kind='absolute', value=COTTON_THREAD_J, fixed=True)}, 2: {}, 3: {}}
        self.users, self.current_user, self.last_input = [], None, None
        self.prefs = dict(FACTORY_PREFERENCES)
        self.modules = dict(balance=False, sample_rack=False, printer=False, tap_water=self.plant['cooling_source'] == 'tap_water')
        self.state, self.state_t0, self.test_len_s, self.test_result = 'system_test', self.time_s, MODE_TEST_S, None   # the factory mode is tested
        self._event('system', 'FACTORY_RESET', '恢复出厂设置（氧弹登记与测量记录保留）')
        return dict(configured=True)

    def _cmd_maintenance_o2_fill(self):
        self._need_ready()
        if self.seal_change:
            raise ActionRejected('SEAL_CHANGE_ACTIVE')
        if self.hung is None:
            raise ActionRejected('NO_VESSEL_HUNG')
        v = self.vessels[self.hung]
        if v['state'] not in ('clean', 'prepared') or self._pressurized(v):
            raise ActionRejected('VESSEL_NOT_READY')
        self._fill_oxygen(v)
        self._event('maintenance', 'O2_FILL', f'维护：氧弹{self.hung}手动充氧')
        return dict(rfid=self.hung)

    def _cmd_maintenance_change_seal(self, active):
        self._need_ready()
        if active and self.plant['oxygen_supply_pa'] <= P_ATM:
            raise ActionRejected('NO_OXYGEN')                              # manual 11.7.1: only possible when oxygen is connected
        self.seal_change = active
        self._event('maintenance', 'CHANGE_SEAL', '维护：充氧活塞降到最低位（换密封圈）' if active else '维护：换密封圈结束')
        return dict(active=active)

    def _cmd_device_empty_for_transport(self):
        self._need_ready()
        self.water_target = 0.0
        self.maint_stirrer = False
        self._event('maintenance', 'EMPTY_DEVICE', '维护：排空水路（内筒排水）')
        return dict(draining=True)

    # ---------------------------------------------------------------- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    @property
    def panel(self):
        p = _PANELS.get(self)
        if p is None:
            p = _PANELS[self] = _panel_ui.PanelUI(PANEL_SPEC, self)
        return p

    def panel_read(self):
        """What the touch screen shows: the agent slots plus power, power-up count, names, the run progress and curve, and display-ready rows of the
        measurement, archive, vessel, calibration, aid, information and log lists (read-only views of the runtime state)."""
        out = self.read('agent')
        run = self.run
        rec = self.records.get(run['mid']) if run else None
        seq = ['awaiting_vessel', 'closing', 'filling', 'fill_check', 'o2_filling', 'equalising', 'pre_trial', 'main_trial', 'after_trial', 'degassing', 'draining',
               'opening']
        t0 = rec['started_s'] if rec and rec['started_s'] is not None else self.time_s
        names = [r['name'] for r in self.records.values()]
        last = next((n for n in reversed(names) if '_' in n and n.rsplit('_', 1)[-1].isdigit()), None)
        stem, num = (last.rsplit('_', 1)[0], int(last.rsplit('_', 1)[1])) if last else ('Measurement', 0)
        unit = self.prefs['unit']
        fac = {'J/g': 1e-3, 'kJ/kg': 1e-3, 'cal/g': 1 / 4186.8, 'BTU/lb': 1 / 2326.0}[unit]
        list_of = {}
        for n, lst in self.aid_lists.items():
            for name in lst:
                list_of.setdefault(name, n)

        def slots(aids):
            out_ = {}
            for a in aids:
                n = list_of.get(a['aid_name'])
                if n is not None and str(n) not in out_:
                    out_[str(n)] = dict(name=a['aid_name'], mass_g=round(a['mass_kg'] * 1e3, 7) if a.get('mass_kg') is not None else None)
            return out_

        def value_text(r):
            if r['kind'] == 'calibration' and r.get('c_value_j_k') is not None:
                return f"{r['c_value_j_k']:.1f} J/K"
            if r.get('gross_calorific_value_j_kg') is not None:
                return f"{r['gross_calorific_value_j_kg'] * fac:.1f} {unit}"
            return ''

        def when(t_s):
            return None if t_s is None else self._iso_at(t_s).replace('T', ' ')[:16]

        def fmt(v, f, u=''):
            return '' if v is None else (f.format(v) + u)

        views, details, curves = {}, {}, {}
        for m, r in self.records.items():
            v = self._result(r)
            v.update(user=r['user'], aid_slots=slots(r['aids']), aids=copy.deepcopy(r['aids']))
            views[m] = v
            details[m] = [dict(label=k, value=x) for k, x in (
                ('Name', r['name']), ('Type', r['kind'].capitalize()), ('Status', r['status']), ('Working mode', r['mode'] or ''),
                ('Vessel', r['vessel_name'] or 'automatic'), ('Sample weight', fmt(r['sample_mass_kg'] * 1e3, '{:.4f}', ' g')), ('User', r['user'] or ''),
                ('External energy', fmt(sum(a['energy_j'] for a in r['aids']), '{:.1f}', ' J')), ('Delta T', fmt(r['delta_t_k'], '{:.4f}', ' K')),
                ('Ignition energy', fmt(r['ignition_energy_j'], '{:.1f}', ' J')), ('C-value', fmt(r['c_value_j_k'], '{:.1f}', ' J/K')),
                ('Ho', fmt(r['gross_calorific_value_j_kg'] * fac if r['gross_calorific_value_j_kg'] is not None else None, '{:.1f}', ' ' + unit)),
                ('Acid correction', fmt(r['acid_correction_j'], '{:.1f}', ' J')), ('Fore drift', fmt(r['fore_drift_k_s'], '{:.2e}', ' K/s')),
                ('After drift', fmt(r['after_drift_k_s'], '{:.2e}', ' K/s')), ('Started', when(r['started_s']) or ''), ('Finished', when(r['finished_s']) or ''),
                ('Error', r['error_code'] or ''), ('Flags', ', '.join(r['quality_flags'])))]
            pts = [[p['time_s'] - (r['started_s'] or 0.0), p['t1_k'] - 273.15] for p in r['points']]
            curves[m] = dict(points=pts, span=max(600.0, pts[-1][0] - pts[0][0]) if pts else None,
                             y_min=round(min(p[1] for p in pts) - 0.2, 1) if pts else None)
        done = [r for r in self.records.values() if r['status'] in ('completed', 'failed', 'cancelled_after_ignition')]
        row = lambda r: dict(measurement_id=r['measurement_id'], name=r['name'], kind=r['kind'], status=r['status'], value_text=value_text(r),   # noqa: E731
                             finished_s=r['finished_s'] if r['finished_s'] is not None else r['created_s'], date=when(r['finished_s']) or '')
        archive = sorted(done, key=lambda r: -(r['finished_s'] or 0.0))
        vessel_rows, cal_rows, cal_sel, stat_rows = [], {}, {}, {}
        for n, r in self.registry.items():
            c = r['c'].get(self.mode)
            vessel_rows.append(dict(name=n, serial_number=r['serial_number'], ignitions=r['previous_ignitions'] + r['ignitions'],
                                    previous_ignitions=r['previous_ignitions'], platinum_wire=r['platinum_wire'], rfid=r['rfid'],
                                    c_text=f'{c:.1f} J/K' if c is not None else '-'))
            cal_rows[n] = [dict(id=m, text=f"{x['name']}  {x['c_value_j_k']:.1f} J/K") for m, x in self.records.items()
                           if x['kind'] == 'calibration' and x['status'] == 'completed' and x['c_value_j_k'] is not None and x['vessel_name'] == n
                           and x['mode'] == self.mode]
            cal_sel[n] = list(r['calibrations'][self.mode])
            sts = self._stats(n, self.mode)
            stat_rows[n] = [dict(label=k, value=x) for k, x in (
                ('C-value', fmt(sts['c_value_j_k'], '{:.1f}', ' J/K') + (' (manual)' if r['manual'][self.mode] else '')), ('Calibrations', str(sts['n'])),
                ('Average', fmt(sts['mean_j_k'], '{:.1f}', ' J/K')), ('Maximum', fmt(sts['max_j_k'], '{:.1f}', ' J/K')),
                ('Minimum', fmt(sts['min_j_k'], '{:.1f}', ' J/K')), ('Range', fmt(sts['range_j_k'], '{:.1f}', ' J/K')),
                ('RSD', fmt(sts['rsd'] * 100 if sts['rsd'] is not None else None, '{:.3f}', ' %')), ('Sigma', fmt(sts['sigma_j_k'], '{:.2f}', ' J/K')),
                ('UWL / LWL', '' if sts['uwl_j_k'] is None else f"{sts['uwl_j_k']:.1f} / {sts['lwl_j_k']:.1f}"),
                ('UCL / LCL', '' if sts['ucl_j_k'] is None else f"{sts['ucl_j_k']:.1f} / {sts['lcl_j_k']:.1f}"))]
        aid_rows = {str(n): [dict(name=k, energy_kind=d['energy_kind'], value=d['value'],
                                  entry_value=d['value'] * 1e-3 if d['energy_kind'] == 'specific' else d['value'],
                                  energy_text=f"{d['value'] * 1e-3:.1f} J/g" if d['energy_kind'] == 'specific' else f"{d['value']:.1f} J") for k, d in lst.items()]
                    for n, lst in self.aid_lists.items()}
        aid_rows['0'] = [x for n in sorted(self.aid_lists) for x in aid_rows[str(n)]]
        m_ = self.readings if self.power else None
        info = [('Device', 'C 6000 isoperibol'), ('Software version', 'simulated 1.0'), ('Working mode', self.mode),
                ('Available modes', ', '.join(out['mode.available']) or '-'), ('System test', self.test_result or '-'),
                ('T1 inner vessel', fmt(m_['t1'] - 273.15 if m_ else None, '{:.4f}', ' C')), ('T2 outer vessel', fmt(m_['t2'] - 273.15 if m_ else None, '{:.4f}', ' C')),
                ('T5 cooling water', fmt(m_['t5'] - 273.15 if m_ else None, '{:.2f}', ' C')), ('T6 ambient', fmt(m_['t6'] - 273.15 if m_ else None, '{:.2f}', ' C')),
                ('Since power-on', fmt((self.time_s - self.power_on_s) / 60.0 if self.power_on_s is not None else None, '{:.0f}', ' min')),
                ('Registered vessels', str(len(self.registry))), ('Stored measurements', f'{len(self.records)} / {MAX_RECORDS}')]
        last_aids = {}
        if self.last_input:
            last_aids = slots(self.last_input['aids'])
        out.update({
            'host.powered': self.power, 'host.power_epoch': self.power_epoch,
            'host.date_name': self._iso().replace('-', '').replace(':', '').replace('T', '')[2:], 'host.next_auto_name': f'{stem}_{num + 1}',
            'host.last_input': dict(vessel_name=self.last_input['vessel_name'], user=self.last_input['user'], aids=last_aids) if self.last_input else None,
            'host.progress': (seq.index(run['phase']) + 1) / len(seq) if run and run['phase'] in seq else None,
            'host.run_points': [[t - t0, T - 273.15] for t, T in (run['raw'] if run else [])][-MAX_POINTS:],
            'host.run_span': (run['raw'][-1][0] - t0) if run and run['raw'] else 0.0,
            'host.run_y_min': round(min(T for _, T in run['raw']) - 273.15 - 0.2, 1) if run and run['raw'] else None,
            'host.meas_rows': [row(r) for r in self.records.values()], 'host.archive_rows': [row(r) for r in archive],
            'host.archive_by_name': [row(r) for r in sorted(done, key=lambda r: r['name'].lower())],
            'host.records': views, 'host.detail_rows': details, 'host.record_curves': curves, 'host.vessel_rows': vessel_rows, 'host.cal_rows': cal_rows,
            'host.cal_selected': cal_sel, 'host.stat_rows': stat_rows, 'host.aid_rows': aid_rows, 'host.info_rows': [dict(label=k, value=v) for k, v in info],
            'host.log_rows': [dict(n=i, time=when(e['time_s']), code=f"{e['kind']} {e['code']}") for i, e in enumerate(self.events)][::-1][:100],
            'host.last_id': next(reversed(self.records), None)})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel action calls the same command a direct call would (each command checks before it changes anything).  A key pressed through a
        session acts as that session's caller: the session's ownership guards apply and the records and runs it creates belong to the caller."""
        try:
            if capability_id in UNSUPPORTED:
                raise ActionRejected('UNSUPPORTED')
            caller = self._caller
            if caller is not None:
                IkaC6000Session(self, caller)._guard(capability_id, arguments)
            out = self.invoke(capability_id, **arguments)
            if caller is not None:
                if capability_id in ASYNC and out.get('operation_id'):
                    self._owners[out['operation_id']] = caller
                if capability_id == 'measurement.create':
                    self._owners[out['measurement_id']] = caller
            return out
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.time_s

    def _panel_command(self, capability_id, a):
        if _panel_ui.check_arguments(PANEL_CAPS[capability_id]['input_schema'], a):      # optional keys (duration_s, to_x / to_y)
            raise ActionRejected('INVALID_ARGUMENT')
        try:
            if capability_id == 'panel.press':
                return dict(accepted=bool(self.panel.press(a['input_id'], float(a.get('duration_s', 0.1)))))
            if capability_id == 'panel.touch':
                to = (a['to_x'], a['to_y']) if 'to_x' in a and 'to_y' in a else None
                return dict(accepted=bool(self.panel.touch(a['display_id'], a['x'], a['y'], float(a.get('duration_s', 0.1)), to)))
            if capability_id == 'panel.capture':
                return self._panel_capture(a['display_id'])
            return dict(frame=self.panel.frame())
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _panel_capture(self, display_id):
        """Close-up camera of a display: a PNG file named by its content (large data is returned as a file reference)."""
        import hashlib
        import tempfile
        data, w, h = self.panel.png_bytes(display_id)
        sha = hashlib.sha256(data).hexdigest()
        d = Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f'{display_id}_{sha[:16]}.png'
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.time_s, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))

    # ---------------------------------------------------------------- combustion chemistry
    def _fill_oxygen(self, v):
        """Fill the vessel to the supply pressure; optional O2 flush (manual 11.4.2: filled and emptied twice before the actual filling)."""
        V = VESSEL_VOLUME_M3[v['model']] - (v['absorption_water_kg'] / 1000.0)
        p = self.plant['oxygen_supply_pa']
        purity = self.plant['oxygen_purity']
        rt = GAS_CONSTANT * T_REF
        n_air = P_ATM * V / rt
        gas = dict(O2=0.2095 * n_air, N2=0.7808 * n_air, Ar=0.0097 * n_air, CO2=0.0)
        self.led['o2_filled_mol'].add(gas['O2'])                             # O2 of the air enclosed when the vessel was closed
        added = max(0.0, (p - P_ATM) * V / rt)
        filled = added * purity
        gas['O2'] += filled
        gas['N2'] += added * (1.0 - purity)
        self.led['o2_filled_mol'].add(filled)
        if self.settings['o2_flush'] and p > P_ATM:
            for _ in range(O2_FLUSH_CYCLES):                             # manual 11.4.2: filled, then emptied twice before the actual filling
                keep = P_ATM / p
                vent = gas['O2'] * (1.0 - keep)
                for k in gas:
                    gas[k] *= keep
                self.led['o2_vented_mol'].add(vent)
                gas['O2'] += filled
                gas['N2'] += added * (1.0 - purity)
                self.led['o2_filled_mol'].add(filled)
                self._flush_losses(v, V)
        v['gas'] = gas
        v['fill_pressure_pa'] = p
        v['state'] = 'pressurized'

    def _flush_losses(self, v, V):
        """Vapour of an uncapsuled volatile liquid sample leaves with the vented flush gas (vapour in equilibrium with the liquid)."""
        s = v['sample']
        if not s or s['form'] != 'liquid':
            return
        rec = _lib()['by_id'][s['substance_id']]
        tb = rec.get('boiling_point_K')
        if not tb:
            return
        m_vap = vapour_pressure_pa(tb, T_REF) * V * rec['molar_mass_g_mol'] / 1000.0 / (GAS_CONSTANT * T_REF)
        lost = min(s['mass_kg'] - v['vapour_lost_kg'], m_vap)
        v['vapour_lost_kg'] += max(0.0, lost)

    def _degas(self, v):
        if not v['gas']:
            return
        self.led['o2_vented_mol'].add(v['gas']['O2'])
        v['gas'] = None
        v['state'] = {'pressurized': 'prepared', 'burned': 'burned_degassed'}.get(v['state'], v['state'])

    def _burn(self, v):
        """Ignition: energies released in the vessel (process truth) and the electrical energy the device measures.  The cotton thread, the
        combustion aids and the sample share the oxygen in the vessel: with too little oxygen all of them burn only partly."""
        rng = self.rng
        e_ign = IGNITION_MEAN_J * max(0.5, 1.0 + rng.gauss(0.0, IGNITION_SPREAD))
        e_thread = COTTON_THREAD_J * max(0.5, 1.0 + rng.gauss(0.0, THREAD_SPREAD))
        e_aids = sum(AIDS[a['kind']] * a['mass_kg'] for a in v['aids'])
        gas = v['gas'] or dict(O2=0.0, N2=0.0, Ar=0.0, CO2=0.0)
        p_fill = v.get('fill_pressure_pa', P_ATM)
        s = v['sample']
        e_sample, n_s, n_n, n_c, o2_need, frac, residue = 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 'clean'
        rec = q = m = n = None
        if s:
            rec = _lib()['by_id'][s['substance_id']]
            m = max(0.0, s['mass_kg'] - v['vapour_lost_kg'])
            M = rec['molar_mass_g_mol'] / 1000.0
            n = m / M                                                           # mol (repeat units for polymers)
            o2_need = n * rec['o2_mol_per_mol']
            if s['substance_id'] == 'benzoic_acid':
                q = rec['certified_calorific_value']['value_J_g'] * 1000.0 * benzoic_condition_factor(p_fill, m, v['absorption_water_kg'],
                                                                                                      VESSEL_VOLUME_M3[v['model']], self._work_k(self.run['mode']))
            else:
                q = rec['gross_calorific_value_J_g'] * 1000.0
            frac = min(1.0, max(0.0, (p_fill - 0.5e6) / 1.5e6))                  # low filling pressure: incomplete combustion
            if s['substance_id'] == 'benzoic_acid' and s['form'] == 'powder':
                frac *= 0.97 + 0.025 * rng.random()                             # loose benzoic acid sprays (manual 6.3: burn only pressed)
        demand = (e_thread + e_aids) / Q_PER_MOL_O2 + o2_need * frac
        f_ox = 1.0
        if demand > 0.0 and gas['O2'] < 1.02 * demand:
            f_ox = max(0.0, 0.9 * gas['O2'] / demand)                           # oxygen-starved: every combustible burns only partly
        e_thread *= f_ox
        e_aids *= f_ox
        if s:
            frac *= f_ox
            e_sample = q * m * frac
            n_s = n * rec['element_counts'].get('S', 0) * frac
            n_n = n * rec['element_counts'].get('N', 0) * frac
            n_c = n * rec['element_counts'].get('C', 0) * frac
            residue = 'clean' if frac >= 0.9995 else 'soot' if frac >= 0.95 else 'unburnt_sample'
            self.used_substances.add(s['substance_id'])
        q_base = e_sample + e_thread + e_aids + e_ign
        n_hno3_air = min(XI_AIR_HNO3 * math.sqrt(max(q_base, 0.0) / 26434.0), 0.2) * 2.0 * gas['N2']
        n_hno3_sample = XI_SAMPLE_N * n_n
        o2_aux = (e_thread + e_aids) / Q_PER_MOL_O2                              # thread and aids: ~13.1 kJ per g O2 (Huggett's rule)
        o2_acid = 1.25 * (n_hno3_air + n_hno3_sample) + 0.5 * n_s
        n_s_acid = n_s
        if o2_need * frac + o2_aux + o2_acid > gas['O2'] and o2_acid > 0.0:     # acids form only from the oxygen left over
            scale = max(0.0, gas['O2'] - (o2_need * frac + o2_aux)) / o2_acid
            n_hno3_air, n_hno3_sample, n_s_acid = n_hno3_air * scale, n_hno3_sample * scale, n_s * scale
        n_hno3 = n_hno3_air + n_hno3_sample
        e_acid = n_hno3 * HNO3_J_MOL + n_s_acid * H2SO4_J_MOL
        o2_used = min(o2_need * frac + o2_aux + 1.25 * n_hno3 + 0.5 * n_s_acid, gas['O2'])
        gas['O2'] -= o2_used
        gas['N2'] = max(0.0, gas['N2'] - 0.5 * n_hno3_air + 0.5 * (n_n - n_hno3_sample))
        gas['CO2'] += n_c + 0.75 * o2_aux
        self.led['o2_consumed_mol'].add(o2_used)
        v['gas'] = gas
        v['acids'] = dict(HNO3=n_hno3, H2SO4=n_s_acid)
        v['burned_fraction'] = frac if s else None
        v['residue'] = residue if s else 'none'
        v['state'] = 'burned'
        v['ignitions'] += 1
        total = e_sample + e_thread + e_aids + e_ign + e_acid
        v['last_heat_j'] = total
        cs, ch = self._vessel_nodes(v)
        v['T_shell'] += total * (1.0 - VESSEL_HEAD_SHARE) / cs
        v['T_head'] += total * VESSEL_HEAD_SHARE / ch
        for k, val in (('ignition_j', e_ign), ('thread_j', e_thread), ('aid_j', e_aids), ('sample_j', e_sample), ('acid_j', e_acid)):
            self.led[k].add(val)
        if total > MAX_ENERGY_J:
            v['inspection_required'] = True                                     # truth only; the instrument warns after it has measured the rise
        return e_ign + rng.gauss(0.0, IGNITION_MEASURE_SD_J)

    # ---------------------------------------------------------------- physics: RK4 sub-steps with the ledger booked from the same stages
    def _derivs(self, Tw, Tj, vT, book=None):
        """Temperature derivatives of the water node, the jacket and every vessel (shell, head); book=(weight, dt) adds flows to the ledger."""
        f = self.fit
        pl = self.plant
        Ta, T5 = pl['ambient_k'], pl['cooling_water_k']
        immersed = self.immersed                                              # latched on the 1 s cycle: host-step independent
        q_w = f['G_WJ'] * (Tj - Tw) + f['G_WA'] * (Ta - Tw) + self.p_stir + self.p_inner
        q_j = self.p_jacket - self._g_cool() * (Tj - T5) - f['G_JA'] * (Tj - Ta) + f['G_WJ'] * (Tw - Tj)
        room = f['G_WA'] * (Tw - Ta) + f['G_JA'] * (Tj - Ta)
        dv = {}
        for r, (Ts, Th) in vT.items():
            v = self.vessels[r]
            cs, ch = self._vessel_nodes(v)
            if immersed and r == self.hung:
                qs, qh = self.g_factor * f['G_SHELL'] * (Tw - Ts), self.g_factor * f['G_HEAD'] * (Tw - Th)
                q_w -= qs + qh
            else:
                qs, qh = f['G_BA'] * (1 - VESSEL_HEAD_SHARE) * (Ta - Ts), f['G_BA'] * VESSEL_HEAD_SHARE * (Ta - Th)
                room -= qs + qh
            dv[r] = (qs / cs, qh / ch)
        if book is not None:
            w, dt = book
            L = self.led
            L['jacket_heater_j'].add(w * dt * self.p_jacket)
            L['inner_heater_j'].add(w * dt * self.p_inner)
            L['stirrer_j'].add(w * dt * self.p_stir)
            L['cooling_removed_j'].add(w * dt * self._g_cool() * (Tj - T5))
            L['room_loss_j'].add(w * dt * room)
        return q_w / self._c_water(), q_j / f['C_JACKET'], dv

    def _integrate(self, dt):
        if dt <= 0 or self.plant is None:
            return
        n = max(1, int(math.ceil(dt / 0.5 - 1e-9)))
        h = dt / n
        for _ in range(n):
            y0 = (self.Tw, self.Tj, {r: (v['T_shell'], v['T_head']) for r, v in self.vessels.items()})
            k = []
            stage = y0
            for i, (a, w) in enumerate(((0.0, 1 / 6), (0.5, 1 / 3), (0.5, 1 / 3), (1.0, 1 / 6))):
                if i:
                    dw, dj, dv = k[-1]
                    stage = (y0[0] + a * h * dw, y0[1] + a * h * dj, {r: (y0[2][r][0] + a * h * dv[r][0], y0[2][r][1] + a * h * dv[r][1]) for r in y0[2]})
                k.append(self._derivs(stage[0], stage[1], stage[2], book=(w, h)))
            self.Tw = y0[0] + h * sum(w * kk[0] for w, kk in zip((1 / 6, 1 / 3, 1 / 3, 1 / 6), k))
            self.Tj = y0[1] + h * sum(w * kk[1] for w, kk in zip((1 / 6, 1 / 3, 1 / 3, 1 / 6), k))
            for r, v in self.vessels.items():
                v['T_shell'] = y0[2][r][0] + h * sum(w * kk[2][r][0] for w, kk in zip((1 / 6, 1 / 3, 1 / 3, 1 / 6), k))
                v['T_head'] = y0[2][r][1] + h * sum(w * kk[2][r][1] for w, kk in zip((1 / 6, 1 / 3, 1 / 3, 1 / 6), k))

    def _change_water(self, target, dt):
        """Fill from the cooling-water supply (at its temperature) or drain at the node temperature; energy booked as sensible heat."""
        if abs(target - self.water_kg) < 1e-12:
            return
        dm = max(-FILL_RATE_KG_S * dt, min(FILL_RATE_KG_S * dt, target - self.water_kg))
        if dm > 0:
            t_in = self.plant['cooling_water_k']
            C0 = self._c_water()
            self.Tw = (C0 * self.Tw + dm * CP_WATER * t_in) / (C0 + dm * CP_WATER)
            self.led['fill_sensible_j'].add(dm * CP_WATER * (t_in - T_REF))
            self.led['water_filled_kg'].add(dm)
        else:
            self.led['drain_sensible_j'].add(-dm * CP_WATER * (self.Tw - T_REF))
            self.led['water_drained_kg'].add(-dm)
        self.water_kg = max(0.0, self.water_kg + dm)

    # ---------------------------------------------------------------- instrument cycle
    def _measure(self):
        f = 1.0 - math.exp(-CYCLE_S / (SENSOR_TAU_S if (self.water_kg > 0.05 or self.lift < 0.05) else 60.0))
        env = self.Tw if (self.water_kg > 0.05 or self.lift < 0.05) else self.plant['ambient_k']
        self.probe_k += (env - self.probe_k) * f
        g = self.rng.gauss
        if self.state in ('booting', 'shutdown'):
            self.readings = None
            return
        self.readings = dict(t1=quantize(self.probe_k + g(0.0, SENSOR_NOISE_K), RESOLUTION_K), t2=quantize(self.Tj + g(0.0, SENSOR_NOISE_K), RESOLUTION_K),
                             t5=quantize(self.plant['cooling_water_k'] + g(0.0, 0.002), 0.01), t6=quantize(self.plant['ambient_k'] + g(0.0, 0.01), 0.01),
                             rpm=(STIRRER_RPM + round(g(0.0, 2.0))) if self._stirring() else 0.0)

    def _control(self):
        """Jacket PI (heater against the cooling water) and the inner-circuit heater during equalisation; held for the next cycle."""
        if self.state in ('off', 'booting', 'shutdown') or self.readings is None:
            self.p_jacket = self.p_inner = 0.0
        else:
            e = self._work_k() - self.readings['t2']
            p = KP_JACKET_W_K * e + self.jacket_int
            if 0.0 < p < self.fit['P_JACKET_MAX'] or (p <= 0.0 and e > 0) or (p >= self.fit['P_JACKET_MAX'] and e < 0):
                self.jacket_int += KP_JACKET_W_K * e * CYCLE_S / TI_JACKET_S
            self.p_jacket = min(self.fit['P_JACKET_MAX'], max(0.0, KP_JACKET_W_K * e + self.jacket_int))
            if self.run is not None and self.run['phase'] == 'equalising':
                self.p_inner = min(self.fit['P_INNER_MAX'], max(0.0, KP_INNER_W_K * (self._work_k(self.run['mode']) - self.readings['t1'] - 0.004)))
            else:
                self.p_inner = 0.0
        self.p_stir = self.fit['P_STIRRER'] if self._stirring() else 0.0

    def _tick(self):
        t = self.time_s
        # maintenance water change (per cycle); the cover lift and the stirrer shaft move continuously in _move
        if self.power and self.run is None:
            self._change_water(self.water_target, CYCLE_S)
        self._measure()
        if self.state == 'booting' and t - self.state_t0 >= SOFTWARE_LOAD_S - 1e-9:
            self.state, self.state_t0 = 'system_test', t
            self.lift_target = LIFT_TRAVEL_M                                     # manual 10.1: the cover opens automatically
            self._event('system', 'SYSTEM_TEST', '自检开始（冷却水温度、流量、温度）')
            self._fault_found('boot')
        elif self.state == 'system_test' and t - self.state_t0 >= self.test_len_s - 1e-9:
            self._system_test_result()
        if self.run is not None:
            self._advance_run()
        self._control()
        self.immersed = self._immersed()

    def _system_test_result(self):
        if self._fault_found('test'):
            self.test_result = 'red'
            self.state_t0 = self.time_s
            return
        r = self.readings
        if self.plant['cooling_flow_m3_s'] < COOLING_FLOW_MIN_M3_S or self.plant['cooling_pressure_pa'] < 0.3e5:
            self.error, self.state = ('E32' if self.plant['cooling_flow_m3_s'] < COOLING_FLOW_MIN_M3_S else 'E31'), 'error'
            self.test_result = 'red'
            self._event('error', self.error, '自检：水路流量过低（请检查冷却器与滤水器）')
        elif not COOLING_LIMITS_K[0] - LIMIT_TOL_K <= r['t5'] <= COOLING_LIMITS_K[1] + LIMIT_TOL_K:
            self.test_result, self.error, self.state = 'red', 'SYSTEM_TEST_RED', 'error'
            self._event('error', 'SYSTEM_TEST_RED', '自检红色：冷却水温度超出12–27°C')
        elif self.mode not in self._available_modes(r['t5']):
            self.test_result, self.state = 'yellow', 'wait'
            self._event('system', 'SYSTEM_TEST_YELLOW', '自检黄色：当前工作模式与冷却水温度不符，须更改模式')
        elif self.modules['tap_water'] != (self.plant['cooling_source'] == 'tap_water'):
            self.test_result, self.state = 'yellow', 'wait'
            self._event('system', 'SYSTEM_TEST_YELLOW', '自检黄色：模块Tap water设置与冷却水供给不符（手册11.3），须更改')
        else:
            self.test_result, self.state = 'green', 'wait'
            self._event('system', 'SYSTEM_TEST_GREEN', '自检通过，等待')
        self.state_t0 = self.time_s

    # ---------------------------------------------------------------- measurement sequence
    def _slope(self, t0, t1):
        pts = [(t, T) for t, T in self.run['raw'] if t0 < t <= t1 + 1e-9]
        if len(pts) < 10:
            return None
        return linfit([p[0] for p in pts], [p[1] for p in pts])[1]

    def _advance_run(self):
        run = self.run
        t = self.time_s
        el = t - run['t_phase']
        rec = self.records[run['mid']]
        kind_mode = mode_parts(run['mode'])[0]
        tw = self._work_k(run['mode'])
        r = self.readings
        if run['phase'] in ('pre_trial', 'main_trial', 'after_trial'):
            run['raw'].append((t, r['t1']))
        ph = run['phase']
        if ph == 'closing':
            if self.lift <= 1e-9:
                if self.hung is None:
                    self.lift_target = LIFT_TRAVEL_M
                    self._fail_run('E11', 'E11：关盖错误——盖未到达下限触点或点火触点未闭合（未挂氧弹）')
                    return
                v = self.vessels[self.hung]
                if v['state'] != 'prepared':
                    self.lift_target = LIFT_TRAVEL_M
                    self._fail_run('E11', 'E11：点火回路不通（挂的是未准备的氧弹）')
                    return
                run['fill_target'] = self.fit['WATER_KG'] + self.rng.gauss(0.0, WATER_FILL_SD_KG)
                self.g_factor = max(0.5, 1.0 + self.rng.gauss(0.0, COUPLING_SD))
                run['vessel_rfid'] = self.hung
                rec['vessel_rfid_truth'] = self.hung
                self._set_phase('filling')
        elif ph == 'filling':
            if self.plant['cooling_flow_m3_s'] < COOLING_FLOW_MIN_M3_S or self.plant['cooling_pressure_pa'] < 0.3e5:
                if el >= 120.0:
                    self._fail_run('E05', 'E05：规定时间内内筒未注满（检查滤水器与冷却水流量）')
                return
            self._change_water(run['fill_target'], CYCLE_S)
            if abs(self.water_kg - run['fill_target']) < 1e-12:
                self._set_phase('fill_check')
        elif ph == 'fill_check':
            if el >= POST_FILL_WAIT_S - 1e-9:
                if r['t1'] <= tw - FILL_TEMP_MARGIN_K:
                    self._set_phase('o2_filling')
                elif el >= POST_FILL_WAIT_S + FILL_TEMP_WAIT_S - 1e-9:
                    self._fail_run('E07', 'E07：注水温度未低于要求值（冷却水/冷凝器温度设得过高）')
        elif ph == 'o2_filling':
            if el >= O2_FILL_S + (O2_FLUSH_S if self.settings['o2_flush'] else 0.0) - 1e-9:
                self._fill_oxygen(self.vessels[run['vessel_rfid']])
                run['o2_filled'] = True
                self._set_phase('equalising')
        elif ph == 'equalising':
            ok = abs(r['t1'] - tw) <= EQUALISE_TOL_K and abs(r['t2'] - tw) <= 0.02
            run['stable_since'] = (run['stable_since'] or t) if ok else None
            if ok and t - run['stable_since'] >= EQUALISE_HOLD_S - 1e-9:
                self._set_phase('pre_trial')
            elif el >= EQUALISE_TIMEOUT_S:
                self._fail_run('E24', 'E24：内水路未在规定时间内达到工作温度稳定判据')
        elif ph == 'pre_trial':
            if el >= PRE_MIN_S[kind_mode] - 1e-9:
                g2, g1 = self._slope(t - 60.0, t), self._slope(t - 120.0, t - 60.0)
                if g1 is not None and g2 is not None and abs(g2) < DRIFT_MAX_K_S and abs(g2 - g1) < DRIFT_STEP_K_S:
                    self._ignite(run, rec)
                    return
            if el >= PRE_TIMEOUT_S:
                self._fail_run('E03', 'E03：前期未达到漂移判据')
        elif ph == 'main_trial':
            since = t - run['t_ign']
            if not run.get('e04_checked') and since >= E04_WINDOW_S - 1e-9:
                run['e04_checked'] = True
                if r['t1'] - run['theta_ign'] < E04_RISE_K:
                    self._fail_run('E04', 'E04：点火后1min内未检测到温升（样品未燃：称样太少、氧压太低或点火线接触不良）')
                    return
            if kind_mode == 'dynamic':
                if since >= MAIN_MIN_S['dynamic'] - 1e-9:
                    run['t_main_end'] = t
                    try:
                        self._evaluate(run, rec)
                        self._set_phase('degassing')
                    except ActionRejected:
                        self._fail_run('EVALUATION_FAILED', '温升曲线外推失败，无法计算结果')
                return
            if since >= MAIN_MIN_S['isoperibol'] - 1e-9:
                g2, g1 = self._slope(t - 60.0, t), self._slope(t - 120.0, t - 60.0)
                if g1 is not None and g2 is not None and abs(g2 - g1) < MAIN_LINEAR_K_S:
                    run['t_main_end'] = run['t_ign'] + PROTOCOL_S * math.ceil((t - run['t_ign']) / PROTOCOL_S - 1e-9)
                    self._set_phase('after_trial')
                    return
            if since >= MAIN_MAX_S:
                self._fail_run('E29', 'E29：主期稳定判据超时')
        elif ph == 'after_trial':
            if t >= run['t_main_end'] + AFTER_S['isoperibol'] - 1e-9:
                try:
                    self._evaluate(run, rec)
                    self._set_phase('degassing')
                except (ActionRejected, ZeroDivisionError):
                    self._fail_run('EVALUATION_FAILED', '前后期数据不足，无法计算结果')
        elif ph == 'degassing':
            if el >= (DEGAS_S if run['o2_filled'] else 0.0) - 1e-9:
                if run['o2_filled'] and run.get('vessel_rfid') and not self.prefs['decomposition']:   # Decomposition on: vented by hand
                    self._degas(self.vessels[run['vessel_rfid']])
                self._set_phase('draining')
        elif ph == 'draining':
            self._change_water(0.0, CYCLE_S)
            if self.water_kg <= 1e-12:
                self.lift_target = LIFT_TRAVEL_M
                self._set_phase('opening')
        elif ph == 'opening':
            self.lift_target = LIFT_TRAVEL_M
            if self.lift >= LIFT_TRAVEL_M - 1e-9:
                self._end_run()

    def _ignite(self, run, rec):
        v = self.vessels[run['vessel_rfid']]
        pts = self._blocks(run, run['t_phase'], self.time_s, backwards=True)
        a, b = linfit([p[0] for p in pts], [p[1] for p in pts])
        run['t_ign'] = self.time_s
        run['theta_ign'] = a + b * self.time_s
        run['e_ign'] = self._burn(v)
        run['ignited'] = True
        rec['ignited'] = True
        reg = self.registry.get(run['vessel_name'])
        if reg is not None:
            reg['ignitions'] += 1
            if reg['previous_ignitions'] + reg['ignitions'] == PRESSURE_TEST_IGNITIONS:
                self._event('warning', 'PRESSURE_TEST_DUE', f'{run["vessel_name"]}达到建议点火次数{PRESSURE_TEST_IGNITIONS}：应做压力检验')
        self._set_phase('main_trial')
        self._event('measurement', 'IGNITION', f'点火（测得点火能{run["e_ign"]:.1f}J）', mid=run['mid'])

    def _blocks(self, run, t_from, t_to, backwards=False):
        """12-s averages of the 1-s readings; aligned on t_to (backwards) or on t_from."""
        raw = run['raw']
        out = []
        n = int(math.floor((t_to - t_from) / PROTOCOL_S + 1e-9))
        for k in range(n):
            a = (t_to - (k + 1) * PROTOCOL_S) if backwards else (t_from + k * PROTOCOL_S)
            vals = [T for tt, T in raw if a < tt <= a + PROTOCOL_S + 1e-9]
            if vals:
                out.append((a + PROTOCOL_S / 2, sum(vals) / len(vals)))
        return sorted(out)

    def _evaluate(self, run, rec):
        kind_mode = mode_parts(run['mode'])[0]
        t0 = run['t_ign']
        fore = [p for p in self._blocks(run, t0 - PRE_MIN_S[kind_mode], t0, backwards=True) if p[0] < t0]
        af, gf = linfit([p[0] for p in fore], [p[1] for p in fore])
        th0 = af + gf * t0
        thf = sum(p[1] for p in fore) / len(fore)
        flags = []
        if kind_mode == 'isoperibol':
            tn = run['t_main_end']
            after = self._blocks(run, tn, tn + AFTER_S['isoperibol'])
            aa, ga = linfit([p[0] for p in after], [p[1] for p in after])
            thn = aa + ga * tn
            tha = sum(p[1] for p in after) / len(after)
            pts = [(t0, th0)] + [(t, T) for t, T in run['raw'] if t0 < t < tn] + [(tn, thn)]
            den = (tha - thf) if abs(tha - thf) > 1e-9 else 1e-9

            def g(th):
                return gf + (ga - gf) * (th - thf) / den
            corr = sum((g(p0[1]) + g(p1[1])) / 2.0 * (p1[0] - p0[0]) for p0, p1 in zip(pts[:-1], pts[1:]))
            dT = thn - th0 - corr
            rec.update(after_drift_k_s=ga, main_end_time_s=tn)
        else:
            data = [(t - t0, T) for t, T in run['raw'] if t0 + DYN_FIT_START_S <= t <= run['t_main_end'] + 1e-9]
            a, b, tau = _fit_exponential_tail(data)
            dT = (a + b * tau) - (th0 + gf * tau)
            rec.update(after_drift_k_s=b, main_end_time_s=run['t_main_end'])
        e_ign = run['e_ign']
        q_ext = e_ign + sum(x['energy_j'] for x in rec['aids'])
        m = rec['sample_mass_kg']
        rec.update(delta_t_k=dT, ignition_energy_j=e_ign, q_ext_j=q_ext, fore_drift_k_s=gf, ignition_time_s=t0)
        reg = self.registry[run['vessel_name']]
        if rec['kind'] == 'calibration':
            ref = self.settings['reference_value_j_kg']
            c = (ref * m + q_ext) / dT if dT > 0 else None
            rec.update(c_value_j_k=c, c_value_raw_j_k=c, acid_correction_j=None, reference_value_j_kg=ref, gross_calorific_value_j_kg=None)
            self.last_result = dict(delta_t=dT, ho=None, c=c, mid=rec['measurement_id'])
        else:
            c = run['c_value']                                             # the C-value valid when the run was confirmed
            ho = (c * dT - q_ext) / m
            rec.update(c_value_j_k=c, gross_calorific_value_j_kg=ho, reference_value_j_kg=None)
            self.last_result = dict(delta_t=dT, ho=ho, c=None, mid=rec['measurement_id'])
            cal = run['cal_delta_t']                                       # the calibrations behind the C-value fixed at confirmation
            if cal and not 0.5 <= dT / (sum(cal) / len(cal)) <= 1.5:
                flags.append('rise_far_from_calibration')
        if c is not None and c * dT > MAX_ENERGY_J:
            flags.append('energy_above_40kJ')
            self._event('warning', 'ENERGY_ABOVE_40KJ', f'测得能量约{c * dT:.0f}J超过40000J量程上限：氧弹与量热仪须送检（手册11.5）', mid=rec['measurement_id'])
        if dT > 5.0:
            flags.append('rise_above_5K')
        if abs(self.plant['ambient_k'] - self._work_k(run['mode'])) > ROOM_BAND_K:
            flags.append('room_outside_working_band')
        if self.power_on_s is not None and self.time_s - self.power_on_s < WARM_UP_S:
            flags.append('warm_up_under_1h')
        if reg['previous_ignitions'] + reg['ignitions'] >= PRESSURE_TEST_IGNITIONS:
            flags.append('pressure_test_due')
        if run.get('c_manual'):
            flags.append('manual_c_value')
        rec['quality_flags'] = flags
        pts = self._blocks(run, run['raw'][0][0] - 1.0, run['raw'][-1][0]) if run['raw'] else []
        rec['points'] = [dict(time_s=p[0], t1_k=p[1]) for p in pts][-MAX_POINTS:]

    # ---------------------------------------------------------------- clock
    def _next_event(self, end):
        return min(end, (self.time_ns // CYCLE_NS + 1) * CYCLE_NS)

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
            self._move((nxt - self.time_ns) / 1e9)
            self.time_ns = nxt
            if nxt % CYCLE_NS == 0:
                if self.power:
                    self._tick()
                else:
                    self._measure_off()
        self.revision += 1

    def _move(self, dt):
        """Cover lift (motor, about 16 mm/s) and stirrer shaft turn continuously between the 1 s instrument cycles, so the mirrored
        joints move smoothly at any host step; the logic that reads them (cover closed / open, immersion) still runs on the cycle."""
        if self.power:
            d = LIFT_SPEED_M_S * dt
            self.lift = min(self.lift_target, self.lift + d) if self.lift < self.lift_target else max(self.lift_target, self.lift - d)
        if self._stirring():
            self.stir_angle = (self.stir_angle + 2 * math.pi * STIRRER_RPM / 60.0 * dt) % STIRRER_JOINT_MAX_RAD

    def _measure_off(self):
        self.readings = None


def _fit_exponential_tail(data):
    """Least squares T(x) = a + b x - c exp(-x / tau) over tau (log grid + golden refinement); returns (a, b, tau)."""
    if len(data) < 8:
        raise ActionRejected('EVALUATION_FAILED')
    xs = [d[0] for d in data]
    ys = [d[1] for d in data]

    def sse(tau):
        # linear least squares for a, b, c with basis (1, x, -exp(-x/tau))
        cols = [[1.0] * len(xs), xs, [-math.exp(-x / tau) for x in xs]]
        A = [[sum(ci * cj for ci, cj in zip(cols[i], cols[j])) for j in range(3)] for i in range(3)]
        rhs = [sum(ci * y for ci, y in zip(cols[i], ys)) for i in range(3)]
        sol = _solve3(A, rhs)
        res = sum((y - (sol[0] + sol[1] * x - sol[2] * math.exp(-x / tau))) ** 2 for x, y in zip(xs, ys))
        return res, sol
    grid = [10.0 * (1.08 ** k) for k in range(40)]
    best = min(grid, key=lambda tau: sse(tau)[0])
    lo, hi = best / 1.08, best * 1.08
    gr = (math.sqrt(5) - 1) / 2
    x1, x2 = hi - gr * (hi - lo), lo + gr * (hi - lo)
    f1, f2 = sse(x1)[0], sse(x2)[0]
    for _ in range(40):
        if f1 < f2:
            hi, x2, f2 = x2, x1, f1
            x1 = hi - gr * (hi - lo)
            f1 = sse(x1)[0]
        else:
            lo, x1, f1 = x1, x2, f2
            x2 = lo + gr * (hi - lo)
            f2 = sse(x2)[0]
    tau = (lo + hi) / 2
    sol = sse(tau)[1]
    return sol[0], sol[1], tau


def _solve3(A, b):
    M = [row[:] + [bb] for row, bb in zip(A, b)]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(M[r][i]))
        M[i], M[p] = M[p], M[i]
        if abs(M[i][i]) < 1e-300:
            raise ActionRejected('EVALUATION_FAILED')
        for r in range(3):
            if r != i:
                f = M[r][i] / M[i][i]
                M[r] = [x - f * y for x, y in zip(M[r], M[i])]
    return [M[i][3] / M[i][i] for i in range(3)]


# ---------------------------------------------------------------- host session: identity filter, request binding, ownership
# hanging / removing / scanning the vessel (robot skills) and the mains rocker switch (power.set / power.toggle) are physical: never refused (the device
# reacts by its own rules; switching off is a power loss); the menu Shutdown (soft) stays guarded
# during another agent's measurement only commands that stop or change that measurement (cancel, confirm, cover, mode, measurement settings, external
# energies, the vessel's C-value and record, maintenance programs on the same circuits, system test, reset) or the soft Shutdown are refused
# (ACCESS_DENIED); the others (start another measurement, register a vessel, acknowledge, preferences) follow the device's own rules
_CONTROL_GUARDED = {'measurement.confirm', 'cover.open', 'cover.close', 'maintenance.run', 'mode.set', 'settings.configure', 'system.shutdown',
                    'aid.define',
                    'vessel.set_c_value', 'vessel.select_calibrations', 'measurement.cancel', 'vessel.modify', 'vessel.delete', 'system.test_restart',
                    'system.factory_reset', 'maintenance.o2_fill', 'maintenance.change_seal', 'device.empty_for_transport',
                    'modules.configure'}
_DATA_GUARDED = {'measurement.result', 'measurement.evaluate', 'measurement.simulate', 'measurement.start', 'measurement.delete', 'measurement.modify',
                 'measurement.convert'}


class IkaC6000Session:
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
        meta = copy.deepcopy(export_ika_c6000_contract_metadata())
        caps = {k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or self._role != 'agent'}
        slots = {k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or self._role != 'agent'}
        if self._role == 'agent':                                    # no references to slots the agent cannot read
            for c in caps.values():
                c['state_effects'] = [e for e in c['state_effects'] if e['state_id'] in slots]
                c['observes'] = [x for x in c['observes'] if x in slots]
                for pre in c['preconditions']:
                    pre['state_ids'] = [x for x in pre['state_ids'] if x in slots]
                c['execution']['completion_state_ids'] = [x for x in c['execution']['completion_state_ids'] if x in slots]
            for m in meta['mechanisms'].values():
                m['state_ids'] = [x for x in m['state_ids'] if x in slots]
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version=ASSET_VERSION, capabilities=caps, state_slots=slots)

    def _foreign(self, object_id):
        owner = self._host._owners.get(object_id)
        return owner is not None and owner != self._principal and self._host._principals.get(owner) == 'agent'

    def _foreign_run(self):
        run = self._host.run
        return run is not None and self._foreign(run['mid'])

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        values['measurement.started_by_other_agent'] = self._role == 'agent' and self._foreign_run()
        if self._role == 'agent' and self._foreign_run():
            values['measurement.id'] = None
        if self._role == 'agent' and self._foreign(h.last_result.get('mid')):     # another agent's result stays with that agent
            for k in ('result.delta_t_k', 'result.gross_calorific_value_j_kg', 'result.c_value_j_k'):
                values[k] = None
        if self._role == 'agent':                                              # records of another agent stay with that agent
            values['measurements.list'] = [r for r in values['measurements.list'] if not self._foreign(r['measurement_id'])]
            for k in ('simulation.last', 'evaluation.last'):
                if values[k] and self._foreign(values[k]['measurement_id']):
                    values[k] = None
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
        if capability_id in _CONTROL_GUARDED and self._foreign_run():
            if capability_id in ('power.set', 'power.toggle') and not h.power:
                return                                                   # switching an unpowered device on is harmless
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED and self._foreign(arguments.get('measurement_id')):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in ('vessel.select_calibrations', 'measurement.delete') and any(self._foreign(m) for m in arguments['measurement_ids']):
            raise ActionRejected('ACCESS_DENIED')
        # changing a record (deleting it, storing a calibration's acid correction) needs the caller's own record, and a C-value is not
        # changed while another agent's measurement runs (like vessel.set_c_value / vessel.select_calibrations)
        if capability_id == 'measurement.delete' and any(m in h.records and h._owners.get(m) != self._principal for m in arguments['measurement_ids']):
            raise ActionRejected('ACCESS_DENIED')                          # unknown ids are reported by the runtime (UNKNOWN_MEASUREMENT)
        if capability_id == 'measurement.evaluate':
            rec = h.records.get(arguments.get('measurement_id'))
            if rec is not None and rec['kind'] == 'calibration' and (h._owners.get(arguments['measurement_id']) != self._principal or self._foreign_run()):
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
            if not isinstance(capability_id, str) or (capability_id not in CAPS and capability_id not in PANEL_CAPS):
                raise ActionRejected('UNKNOWN_CAPABILITY')
            table = PANEL_CAPS if capability_id in PANEL_CAPS else CAPS
            if self._role == 'agent' and table[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            if table is CAPS:
                validate(table[capability_id]['input_schema'], arguments)
            elif _panel_ui.check_arguments(table[capability_id]['input_schema'], arguments):
                raise ActionRejected('INVALID_ARGUMENT')
            self._guard(capability_id, arguments)
            h._caller = self._principal
            try:
                result = h.invoke(capability_id, **arguments)
            finally:
                h._caller = None
            if capability_id == 'archive.list' and self._role == 'agent':
                result = dict(measurements=[m for m in result['measurements'] if not self._foreign(m['measurement_id'])])
            if capability_id == 'events.read' and self._role == 'agent':   # another agent's measurement events stay with that agent
                result = dict(events=[{k: v for k, v in e.items() if k != '_mid'} for e in h.events
                                      if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_mid'))], dropped=h.events_dropped)
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
            if capability_id == 'measurement.create':
                h._owners[result['measurement_id']] = self._principal
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
        rec = h.records.get(op['measurement_id'])
        return dict(operation_id=operation_id, status=op['phase'], result=h._result(rec) if rec else None)

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
            h._cancel_run(op)
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
ASYNC = {'measurement.start'}
_POWER_EFFECTS = ['power.enabled', 'system.state', 'system.test_result', 'system.error_code', 'system.warm_up_s', 'mode.available', 'inner.indicated_k',
                  'outer.indicated_k', 'cooling_water.indicated_k', 'ambient.indicated_k', 'stirrer.indicated_rpm', 'stirrer.running', 'measurement.id',
                  'measurement.phase', 'truth.jacket_heater_w', 'truth.inner_heater_w', 'truth.stirrer_w', 'truth.cooling_w', 'result.delta_t_k',
                  'result.gross_calorific_value_j_kg', 'result.c_value_j_k', 'vessels.registered']
_HUNG_EFFECTS = ['vessel.hung_rfid', 'truth.vessels', 'truth.shell_k', 'truth.head_k', 'truth.c_total_j_k', 'truth.last_heat_released_j', 'truth.vessel_pressure_pa']
EFFECTS = {
    'plant.configure': ['plant.configured', 'mode.available', 'truth.water_k', 'truth.jacket_k', 'truth.shell_k', 'truth.head_k', 'truth.cooling_w', 'truth.vessels'],
    'vessel.provide': ['truth.vessels'],
    'power.set': _POWER_EFFECTS, 'power.toggle': _POWER_EFFECTS, 'system.shutdown': ['system.state', 'stirrer.running'],
    'error.acknowledge': ['system.error_code', 'system.state'],
    'fault.inject': ['truth.faults', 'system.error_code', 'system.state', 'measurement.phase'], 'fault.clear': ['truth.faults'],
    'mode.set': ['mode.working', 'system.state', 'system.test_result'], 'settings.configure': ['settings.o2_flush', 'settings.auto_vessel_detection', 'settings.reference_value_j_kg'],
    'aid.define': [], 'vessel.scan': ['vessel.last_scanned_rfid'], 'vessel.register': ['vessels.registered'], 'vessel.prepare': ['truth.vessels'],
    'vessel.hang': _HUNG_EFFECTS, 'vessel.remove': _HUNG_EFFECTS, 'vessel.inspect': ['truth.vessels'],
    'washings.titrate': ['truth.vessels'], 'vessel.clean': ['truth.vessels'], 'vessel.set_c_value': ['vessels.registered'], 'vessel.select_calibrations': ['vessels.registered'],
    'vessel.statistics': [], 'measurement.create': [], 'measurement.start': ['measurement.id', 'measurement.phase', 'system.state', 'stirrer.running'],
    'measurement.confirm': ['measurement.phase'], 'measurement.result': [], 'measurement.evaluate': ['vessels.registered'], 'measurement.simulate': [],
    'measurement.delete': [], 'archive.list': [], 'events.read': [], 'cover.open': [], 'cover.close': [],
    'maintenance.run': ['stirrer.running', 'truth.vessel_pressure_pa', 'truth.vessels'], 'ledger.read': [], 'provenance.read': [], 'sim.step': ['clock.time_s'],
    'measurement.cancel': ['measurement.phase', 'measurements.list'], 'measurement.modify': ['measurements.list'], 'measurement.convert': ['measurements.list'],
    'vessel.modify': ['vessels.registered', 'measurements.list'], 'vessel.delete': ['vessels.registered', 'measurements.list'], 'aid.delete': ['aids.lists'],
    'user.save': ['users.list', 'users.current'], 'user.delete': ['users.list', 'users.current'], 'user.select': ['users.current'], 'preferences.configure': ['preferences'],
    'clock.set': ['device.datetime'], 'modules.configure': ['modules.state'], 'system.test_restart': ['system.state', 'system.test_result'],
    'system.test_skip': ['system.state', 'system.test_result', 'system.error_code'],
    'system.factory_reset': ['mode.working', 'settings.o2_flush', 'settings.auto_vessel_detection', 'settings.reference_value_j_kg', 'aids.lists', 'users.list',
                             'users.current', 'preferences', 'modules.state', 'system.state', 'system.test_result'],
    'maintenance.o2_fill': ['truth.vessel_pressure_pa', 'truth.vessels'], 'maintenance.change_seal': ['maintenance.seal_change'],
    'device.empty_for_transport': ['stirrer.running', 'truth.water_kg'],
}
EFFECTS['aid.define'] = ['aids.lists']
EFFECTS['measurement.create'] = ['measurements.list']
EFFECTS['measurement.delete'] = ['measurements.list']
EFFECTS['measurement.simulate'] = ['simulation.last']
EFFECTS['measurement.evaluate'] = ['vessels.registered', 'evaluation.last', 'measurements.list']
EFFECTS['plant.configure'] = EFFECTS['plant.configure'] + ['modules.state']                 # the Tap water module follows the declared cooling source
for _k in ('measurement.start', 'measurement.confirm', 'system.shutdown', 'power.set', 'power.toggle'):
    EFFECTS[_k] = EFFECTS[_k] + ['measurements.list']                                     # the record's status / vessel / saved flag in the list
for _k, _v in EFFECTS.items():
    CAPS[_k]['effects'] = list(_v)
_TIMED = {'power.set', 'power.toggle', 'system.shutdown', 'error.acknowledge', 'mode.set', 'measurement.start', 'measurement.confirm', 'cover.open', 'cover.close',
          'maintenance.run', 'sim.step', 'measurement.cancel', 'system.test_restart', 'system.factory_reset', 'device.empty_for_transport'}
PRECONDITIONS = {
    'plant.configure': ('冷却水压力≤1.5bar（数据表）；重新声明时随机数种子保持首次值', []),
    'vessel.provide': ('已声明实验室条件；RFID不重复（实验台最多8个实体氧弹）', ['plant.configured']),
    'power.set': ('已声明实验室条件', ['plant.configured']), 'power.toggle': ('已声明实验室条件', ['plant.configured']),
    'system.shutdown': ('通电、处于等待状态（手册10.3）、无测量与待确认错误', ['power.enabled', 'system.state', 'measurement.id', 'system.error_code']),
    'error.acknowledge': ('通电且有待确认的错误；E02/E14/E15须关机再开机（SWITCH_OFF_REQUIRED）', ['power.enabled', 'system.error_code']),
    'fault.inject': ('评测者；已声明实验室条件；同时最多16个原因', ['plant.configured', 'truth.faults']), 'fault.clear': ('评测者；已声明实验室条件', ['plant.configured']),
    'mode.set': ('通电、自检完成、无测量与待确认错误；模式须与冷却水温度相符（手册5.3表）；换模式后自检运行（期间NOT_READY）', ['power.enabled', 'system.state', 'system.error_code',
                                                                                                  'mode.available', 'cooling_water.indicated_k']),
    'settings.configure': ('通电、无测量进行', ['power.enabled', 'measurement.id']),
    'aid.define': ('通电；出厂棉线条目不可覆盖；单位质量热值与绝对能量（≤5000J）二选一', ['power.enabled']),
    'vessel.scan': ('通电且软件已启动；该实体氧弹在实验台上（挂在盖上的不能扫描）', ['power.enabled', 'system.state', 'vessel.hung_rfid']),
    'vessel.register': ('通电、自检完成、无测量；已扫描RFID；名称与RFID未登记；最多4个；序列号10位字母数字', ['power.enabled', 'system.state', 'vessel.last_scanned_rfid',
                                                                                                'vessels.registered']),
    'vessel.prepare': ('氧弹在实验台上且清洁；物质在模板库中且形态可行（液体不能压片、固体不能按液体）；胶囊形态须带明胶胶囊助燃物', ['truth.vessels']),
    'vessel.hang': ('盖完全打开、充氧头空着；氧弹在台面上且清洁或已准备', ['cover.state', 'vessel.hung_rfid', 'truth.vessels']),
    'vessel.remove': ('有挂着的氧弹、盖完全打开、无测量进行（任何测量的等待挂弹阶段除外：此时可取下重挂）、氧弹未带压（已充氧或已燃烧而未排气都算带压，例如断电中止后）',
                      ['vessel.hung_rfid', 'cover.state', 'measurement.id', 'truth.vessel_pressure_pa']),
    'vessel.inspect': ('氧弹在实验台上且未带压', ['truth.vessels']),
    'washings.titrate': ('氧弹在实验台上、未带压、已燃烧并排气、尚未滴定', ['truth.vessels']),
    'vessel.clean': ('氧弹在实验台上且未带压；残余气体随开弹放出（计入排放）', ['truth.vessels']),
    'vessel.set_c_value': ('通电；氧弹已登记；手动C值取代该模式的标定选择（选择清空）', ['power.enabled', 'vessels.registered']),
    'vessel.select_calibrations': ('通电；氧弹已登记；所选记录是本氧弹本模式已完成的标定且不属于另一个agent', ['power.enabled', 'vessels.registered']),
    'vessel.statistics': ('氧弹已登记', ['vessels.registered']),
    'measurement.create': ('通电且软件已启动；关闭自动识别时须指定已登记氧弹；辅料已登记且比能量辅料给出质量；归档未满（400条）', ['power.enabled', 'system.state',
                                                                                                    'settings.auto_vessel_detection', 'vessels.registered']),
    'measurement.start': ('通电、自检绿色、无测量与待确认错误；记录未点火过；当前模式与冷却水相符；盖完全打开；氧气供给≤40bar；指定氧弹的测量须已有该模式C值',
                          ['power.enabled', 'system.state', 'system.test_result', 'system.error_code', 'mode.working', 'mode.available', 'cover.state', 'vessels.registered']),
    'measurement.confirm': ('有处于等待挂弹阶段的测量；自动识别时启动后须扫描过已登记氧弹；测量须已有该氧弹该模式的C值', ['measurement.phase', 'vessel.last_scanned_rfid',
                                                                                                     'vessels.registered']),
    'measurement.result': ('记录存在且不属于另一个agent', []),
    'measurement.evaluate': ('已完成的测量或标定；测量不属于另一个agent；标定须是调用者自己的记录且没有另一个agent的测量在进行（会改C值）', ['measurement.started_by_other_agent']),
    'measurement.simulate': ('记录存在且不属于另一个agent；测量须有C值（给定、记录自身或已登记）', ['vessels.registered']),
    'measurement.delete': ('通电且软件已启动；记录存在且是调用者（agent）自己创建的、不在进行中、未被基于标定的C值使用（手动C值会清空该模式的标定选择）',
                           ['power.enabled', 'system.state', 'measurement.id', 'vessels.registered']),
    'archive.list': ('无', []), 'events.read': ('无', []),
    'cover.open': ('通电、自检完成、无测量与待确认错误', ['power.enabled', 'system.state', 'measurement.id', 'system.error_code']),
    'cover.close': ('通电、自检完成、无测量与待确认错误', ['power.enabled', 'system.state', 'measurement.id', 'system.error_code']),
    'maintenance.run': ('通电、自检完成、无测量与待确认错误；排气须有挂着的氧弹', ['power.enabled', 'system.state', 'measurement.id', 'vessel.hung_rfid']),
    'ledger.read': ('无', []), 'provenance.read': ('无', []), 'sim.step': ('无', []),
    'measurement.cancel': ('通电；有测量进行且未进入排气、排水、开盖的结束程序', ['power.enabled', 'measurement.id', 'measurement.phase']),
    'measurement.modify': ('通电；记录存在、未点火、未在进行；氧弹与辅料规则同新建测量；用户已登记', ['power.enabled', 'measurements.list', 'vessels.registered', 'users.list']),
    'measurement.convert': ('通电；记录不在进行且未被C值使用；已完成的测量转标定按当前参考燃烧值重算，标定转测量须有该氧弹该模式C值', ['power.enabled', 'measurements.list', 'vessels.registered']),
    'vessel.modify': ('通电、自检完成、无测量与待确认错误；氧弹已登记；新名称未被占用；序列号10位字母数字', ['power.enabled', 'system.state', 'vessels.registered']),
    'vessel.delete': ('通电、自检完成、无测量与待确认错误；氧弹已登记', ['power.enabled', 'system.state', 'vessels.registered']),
    'aid.delete': ('通电；辅料已登记且不是出厂棉线', ['power.enabled', 'aids.lists']),
    'user.save': ('通电；名称唯一（改名时原名存在）；最多30个', ['power.enabled', 'users.list']), 'user.delete': ('通电；用户存在', ['power.enabled', 'users.list']),
    'user.select': ('通电；用户存在或null', ['power.enabled', 'users.list']),
    'preferences.configure': ('通电；测量进行中不能改Decomposition', ['power.enabled', 'measurement.id']), 'clock.set': ('通电；日期与时间有效', ['power.enabled']),
    'modules.configure': ('通电、无测量进行', ['power.enabled', 'measurement.id']),
    'system.test_restart': ('通电、自检完成、无测量与待确认错误', ['power.enabled', 'system.state', 'measurement.id', 'system.error_code']),
    'system.test_skip': ('通电且自检进行中', ['power.enabled', 'system.state']),
    'system.factory_reset': ('通电、自检完成、无测量与待确认错误', ['power.enabled', 'system.state', 'measurement.id', 'system.error_code']),
    'maintenance.o2_fill': ('通电、自检完成、无测量与待确认错误；未在换密封圈；有挂着的清洁或已准备且未带压的氧弹', ['power.enabled', 'system.state', 'vessel.hung_rfid',
                                                                                                  'maintenance.seal_change']),
    'maintenance.change_seal': ('通电、自检完成、无测量与待确认错误；开始时须接氧气', ['power.enabled', 'system.state', 'measurement.id']),
    'device.empty_for_transport': ('通电、自检完成、无测量与待确认错误', ['power.enabled', 'system.state', 'measurement.id']),
}
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入结构、类型或范围', 'ACCESS_DENIED': '该角色无权调用，或当前测量/记录属于另一个agent', 'REQUEST_CONFLICT': '同一request_id已用于不同的请求',
    'UNKNOWN_INSTANCE': '实例ID不存在', 'UNSUPPORTED': '本配置不提供该功能（理由见function_zh）', 'UNKNOWN_CAPABILITY': '能力ID不存在', 'UNKNOWN_OPERATION': '操作ID不存在',
    'NOT_RUNNING': '操作不在进行中', 'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_OFF': '未通电（或断电中止了测量）', 'COOLING_PRESSURE_TOO_HIGH': '冷却水压力超过1.5bar',
    'MEASUREMENT_RUNNING': '有测量正在进行', 'INVALID_VESSEL': 'RFID为空、重复或实验台已有8个氧弹', 'NOT_READY': '软件加载、自检或关机中（关机只能在等待状态）',
    'NOT_CANCELLABLE': '测量已进入自动结束程序（排气、排水、开盖），结果或错误已确定，不能再取消', 'RECORD_IN_USE': '该标定正被某个氧弹的C值使用，不能删除',
    'NO_ERROR': '没有待确认的错误', 'ERROR_PENDING': '有待确认的错误（先确认）', 'SWITCH_OFF_REQUIRED': 'E02/E14/E15：确认无效，须关机再开机（手册第13章）', 'RUN_MODE_NOT_POSSIBLE': 'E26：所选工作模式与冷却水温度不符（手册5.3表）',
    'AID_FIXED': '出厂“IKA cotton wool thread”条目不可修改或删除', 'UNKNOWN_VESSEL': '实体氧弹不存在', 'VESSEL_NOT_ON_BENCH': '氧弹不在实验台上（挂在盖上）',
    'VESSEL_NOT_SCANNED': '尚未用RFID扫描氧弹（自动识别时须在测量启动后扫描）', 'VESSEL_ALREADY_REGISTERED': '名称或RFID已登记', 'TOO_MANY_VESSELS': '每台最多登记4个氧弹',
    'VESSEL_NOT_CLEAN': '氧弹未清洁（先检查/滴定后清洁）', 'UNKNOWN_SUBSTANCE': '物质不在燃烧模板库中（含卤素物质未收录）', 'FORM_NOT_POSSIBLE': '样品形态与25°C相态不符',
    'CAPSULE_REQUIRED': '胶囊形态须同时加入明胶胶囊助燃物', 'COVER_NOT_OPEN': '盖未完全打开', 'VESSEL_ALREADY_HUNG': '充氧头上已有氧弹', 'VESSEL_NOT_READY': '氧弹已燃烧或带压，不能挂入',
    'NO_VESSEL_HUNG': '充氧头上没有氧弹', 'VESSEL_PRESSURIZED': '氧弹仍带压（先排气：维护程序o2_degas）', 'VESSEL_NOT_BURNED': '氧弹未燃烧或未排气',
    'ALREADY_TITRATED': '洗液已滴定过', 'VESSEL_NOT_REGISTERED': '氧弹未登记', 'UNKNOWN_MEASUREMENT': '测量ID不存在', 'NOT_A_CALIBRATION': '所选记录不是已完成的标定',
    'CALIBRATION_MISMATCH': '所选标定不属于该氧弹或该工作模式', 'ARCHIVE_FULL': '归档已满（400条）', 'VESSEL_REQUIRED': '关闭自动识别时须指定氧弹',
    'UNKNOWN_AID': '辅料未登记', 'AID_MASS_MISMATCH': '比能量辅料须给质量，绝对能量辅料不给质量', 'NOT_STARTABLE': '该记录已点火或正在进行，不能再次启动',
    'O2_PRESSURE_TOO_HIGH': '氧气供给超过40bar（数据表上限）', 'C_VALUE_REQUIRED': '该氧弹在当前模式下没有C值（先标定或手动输入）', 'NOT_AWAITING_VESSEL': '没有等待挂弹确认的测量',
    'NOT_EVALUABLE': '只有已完成且有结果的测量或标定能做标准评价', 'EVALUATION_FAILED': '温升曲线数据不足，无法计算',
    'E03': 'E03：前期未达到漂移判据', 'E04': 'E04：点火后1min内无温升（样品未燃）', 'E05': 'E05：规定时间内内筒未注满（水流量不足）',
    'E07': 'E07：注水温度未低于要求值（冷却水过热）', 'E11': 'E11：关盖错误/点火回路不通（未挂或挂了未准备的氧弹）', 'E24': 'E24：内水路未达到工作温度稳定判据',
    'E29': 'E29：主期稳定判据超时', 'E31': 'E31：内水路流量过低', 'E32': 'E32：外水路流量过低', 'SYSTEM_TEST_RED': '自检红色：冷却水温度超出12–27°C',
    'USER_CANCEL': '测量被取消（点火前取消可重新启动，点火后不可）',
    'NOT_MODIFIABLE': '记录已点火或已完成，不能修改', 'UNKNOWN_USER': '用户未登记', 'USER_EXISTS': '用户名已存在', 'TOO_MANY_USERS': '最多30个用户（假设）',
    'NOT_IN_SYSTEM_TEST': '自检未在进行', 'SEAL_CHANGE_ACTIVE': '换密封圈程序进行中', 'NO_OXYGEN': '未接氧气（换密封圈须接氧气）',
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_READY = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'MEASUREMENT_RUNNING', 'ERROR_PENDING']
CAP_ERRORS = {
    'plant.configure': ['COOLING_PRESSURE_TOO_HIGH'], 'vessel.provide': ['PLANT_REQUIRED', 'INVALID_VESSEL'],
    'power.set': ['PLANT_REQUIRED'], 'power.toggle': ['PLANT_REQUIRED'], 'system.shutdown': ['PLANT_REQUIRED', 'POWER_OFF', 'MEASUREMENT_RUNNING', 'ERROR_PENDING', 'NOT_READY'],
    'error.acknowledge': ['PLANT_REQUIRED', 'POWER_OFF', 'NO_ERROR', 'SWITCH_OFF_REQUIRED'], 'mode.set': _READY + ['RUN_MODE_NOT_POSSIBLE'],
    'fault.inject': ['PLANT_REQUIRED'], 'fault.clear': ['PLANT_REQUIRED'],
    'settings.configure': ['PLANT_REQUIRED', 'POWER_OFF', 'MEASUREMENT_RUNNING'], 'aid.define': ['PLANT_REQUIRED', 'POWER_OFF', 'AID_FIXED'],
    'vessel.scan': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'UNKNOWN_VESSEL', 'VESSEL_NOT_ON_BENCH'],
    'vessel.register': _READY + ['VESSEL_NOT_SCANNED', 'VESSEL_ALREADY_REGISTERED', 'TOO_MANY_VESSELS'],
    'vessel.prepare': ['PLANT_REQUIRED', 'UNKNOWN_VESSEL', 'VESSEL_NOT_ON_BENCH', 'VESSEL_NOT_CLEAN', 'UNKNOWN_SUBSTANCE', 'FORM_NOT_POSSIBLE', 'CAPSULE_REQUIRED'],
    'vessel.hang': ['PLANT_REQUIRED', 'UNKNOWN_VESSEL', 'COVER_NOT_OPEN', 'VESSEL_ALREADY_HUNG', 'VESSEL_NOT_ON_BENCH', 'VESSEL_NOT_READY'],
    'vessel.remove': ['PLANT_REQUIRED', 'NO_VESSEL_HUNG', 'MEASUREMENT_RUNNING', 'COVER_NOT_OPEN', 'VESSEL_PRESSURIZED'],
    'vessel.inspect': ['PLANT_REQUIRED', 'UNKNOWN_VESSEL', 'VESSEL_NOT_ON_BENCH', 'VESSEL_PRESSURIZED'],
    'washings.titrate': ['PLANT_REQUIRED', 'UNKNOWN_VESSEL', 'VESSEL_NOT_ON_BENCH', 'VESSEL_PRESSURIZED', 'VESSEL_NOT_BURNED', 'ALREADY_TITRATED'],
    'vessel.clean': ['PLANT_REQUIRED', 'UNKNOWN_VESSEL', 'VESSEL_NOT_ON_BENCH', 'VESSEL_PRESSURIZED'],
    'vessel.set_c_value': ['PLANT_REQUIRED', 'POWER_OFF', 'VESSEL_NOT_REGISTERED'],
    'vessel.select_calibrations': ['PLANT_REQUIRED', 'POWER_OFF', 'VESSEL_NOT_REGISTERED', 'UNKNOWN_MEASUREMENT', 'NOT_A_CALIBRATION', 'CALIBRATION_MISMATCH'],
    'vessel.statistics': ['VESSEL_NOT_REGISTERED'],
    'measurement.create': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ARCHIVE_FULL', 'VESSEL_REQUIRED', 'VESSEL_NOT_REGISTERED', 'UNKNOWN_AID', 'AID_MASS_MISMATCH'],
    'measurement.start': _READY + ['UNKNOWN_MEASUREMENT', 'NOT_STARTABLE', 'RUN_MODE_NOT_POSSIBLE', 'COVER_NOT_OPEN', 'O2_PRESSURE_TOO_HIGH', 'VESSEL_NOT_REGISTERED',
                                   'C_VALUE_REQUIRED', 'E03', 'E04', 'E05', 'E07', 'E11', 'E24', 'E29', 'EVALUATION_FAILED', 'USER_CANCEL', 'NOT_CANCELLABLE'],
    'measurement.confirm': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_AWAITING_VESSEL', 'VESSEL_NOT_SCANNED', 'VESSEL_NOT_REGISTERED', 'C_VALUE_REQUIRED'],
    'measurement.result': ['UNKNOWN_MEASUREMENT'], 'measurement.evaluate': ['UNKNOWN_MEASUREMENT', 'NOT_EVALUABLE'],
    'measurement.simulate': ['UNKNOWN_MEASUREMENT', 'C_VALUE_REQUIRED'], 'cover.open': _READY, 'cover.close': _READY,
    'measurement.delete': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'UNKNOWN_MEASUREMENT', 'MEASUREMENT_RUNNING', 'RECORD_IN_USE'],
    'maintenance.run': _READY + ['NO_VESSEL_HUNG'],
    'measurement.cancel': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_RUNNING', 'NOT_CANCELLABLE'],
    'measurement.modify': ['PLANT_REQUIRED', 'POWER_OFF', 'UNKNOWN_MEASUREMENT', 'MEASUREMENT_RUNNING', 'NOT_MODIFIABLE', 'VESSEL_REQUIRED', 'VESSEL_NOT_REGISTERED',
                           'UNKNOWN_USER', 'UNKNOWN_AID', 'AID_MASS_MISMATCH'],
    'measurement.convert': ['PLANT_REQUIRED', 'POWER_OFF', 'UNKNOWN_MEASUREMENT', 'MEASUREMENT_RUNNING', 'RECORD_IN_USE', 'C_VALUE_REQUIRED'],
    'vessel.modify': _READY + ['VESSEL_NOT_REGISTERED', 'VESSEL_ALREADY_REGISTERED'], 'vessel.delete': _READY + ['VESSEL_NOT_REGISTERED'],
    'aid.delete': ['PLANT_REQUIRED', 'POWER_OFF', 'UNKNOWN_AID', 'AID_FIXED'],
    'user.save': ['PLANT_REQUIRED', 'POWER_OFF', 'UNKNOWN_USER', 'USER_EXISTS', 'TOO_MANY_USERS'], 'user.delete': ['PLANT_REQUIRED', 'POWER_OFF', 'UNKNOWN_USER'],
    'user.select': ['PLANT_REQUIRED', 'POWER_OFF', 'UNKNOWN_USER'], 'preferences.configure': ['PLANT_REQUIRED', 'POWER_OFF', 'MEASUREMENT_RUNNING'],
    'clock.set': ['PLANT_REQUIRED', 'POWER_OFF'], 'modules.configure': ['PLANT_REQUIRED', 'POWER_OFF', 'MEASUREMENT_RUNNING'],
    'system.test_restart': _READY, 'system.test_skip': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_IN_SYSTEM_TEST'], 'system.factory_reset': _READY,
    'maintenance.o2_fill': _READY + ['SEAL_CHANGE_ACTIVE', 'NO_VESSEL_HUNG', 'VESSEL_NOT_READY'], 'maintenance.change_seal': _READY + ['NO_OXYGEN'],
    'device.empty_for_transport': _READY,
}
_PARTIAL = {'USER_CANCEL', 'POWER_OFF', 'E03', 'E04', 'E05', 'E07', 'E11', 'E24', 'E29', 'EVALUATION_FAILED'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件（室温、冷却水、氧气供给、噪声种子），没有可见模型变化',
    'vessel.provide': '只在过程层增加一个实体氧弹；场景只显示随包的一个C 6012（挂在盖下）',
    'system.shutdown': '保存记录并把盖的目标位置设为关闭；升降随sim.step推进（盖的升降绑定已在场景流程中验证）',
    'error.acknowledge': '只清除错误状态，没有可见模型变化', 'fault.inject': '评测者故障注入：只改错误状态与测量结局，没有可见模型变化',
    'fault.clear': '评测者撤销故障原因，没有可见模型变化', 'mode.set': '只改工作模式与外筒目标温度；温度读数随sim.step演化',
    'settings.configure': '只写入测量设置，没有可见模型变化', 'aid.define': '只写入辅料清单，没有可见模型变化',
    'vessel.scan': '只记录RFID读取；场景不移动氧弹（氧弹几何固定挂在盖下）', 'vessel.register': '只写入氧弹登记，没有可见模型变化',
    'vessel.prepare': '样品、助燃物与吸收水只进入过程账；场景没有坩埚内容的可见表示',
    'vessel.hang': '过程层把氧弹挂入充氧头；场景中氧弹始终挂在盖下（没有实体装卸）', 'vessel.remove': '过程层把氧弹放回台面；场景中氧弹仍挂在盖下（没有实体装卸）',
    'vessel.inspect': '只读残渣观察，没有可见模型变化', 'washings.titrate': '实验室滴定分析，没有可见模型变化', 'vessel.clean': '只改变过程层氧弹状态，没有可见模型变化',
    'vessel.set_c_value': '只写入C值', 'vessel.select_calibrations': '只写入C值与统计', 'vessel.statistics': '只读统计', 'measurement.create': '只新建记录',
    'measurement.confirm': '确认后盖的目标位置设为关闭；升降、搅拌与读数随sim.step推进（绑定在场景流程中验证）',
    'measurement.result': '只读结果', 'measurement.evaluate': '分析：测量只返回校正值；标定保存酸校正并可能更新C值（登记数据，无可见模型变化）',
    'measurement.simulate': '只读计算', 'measurement.delete': '只删除归档记录，没有可见模型变化', 'archive.list': '只读列表', 'events.read': '只读事件',
    'ledger.read': '只读账目（evaluator）', 'provenance.read': '只读来源记录（evaluator）',
    'sim.step': '推进统一宿主时钟；升降、搅拌与读数随绑定状态变化，由各绑定在场景中验证',
    **{k: '只写入设备设置或记录，没有可见模型变化' for k in ('measurement.modify', 'measurement.convert', 'vessel.modify', 'vessel.delete', 'aid.delete', 'user.save',
                                                     'user.delete', 'user.select', 'preferences.configure', 'clock.set', 'modules.configure', 'system.factory_reset')},
    'measurement.cancel': '取消后照常排气、排水、开盖；升降随sim.step推进', 'system.test_restart': '只改仪器状态（自检），读数随sim.step推进',
    'system.test_skip': '只改仪器状态', 'maintenance.o2_fill': '过程层给挂着的氧弹充氧，没有可见模型变化', 'maintenance.change_seal': '充氧活塞位置未建模为几何',
    'device.empty_for_transport': '内筒排水随sim.step推进，没有可见几何变化',
}
_GUARDED = _CONTROL_GUARDED | _DATA_GUARDED
_MEAS_RES = ['host_clock', 'lift', 'inner_vessel', 'outer_vessel', 'decomposition_vessel', 'oxygen_supply', 'cooling_water']
RESOURCES = {'measurement.start': _MEAS_RES, 'measurement.confirm': ['lift', 'filling_head', 'decomposition_vessel'], 'cover.open': ['lift'], 'cover.close': ['lift'],
             'maintenance.run': ['inner_vessel', 'stirrer', 'oxygen_valves', 'water_valves', 'decomposition_vessel'], 'power.set': ['mains', 'lift'],
             'power.toggle': ['mains', 'lift'], 'system.shutdown': ['mains', 'lift'], 'error.acknowledge': ['touch_display'], 'mode.set': ['outer_vessel', 'cooling_water'],
             'settings.configure': ['touch_display'], 'aid.define': ['touch_display'], 'plant.configure': ['cooling_water', 'oxygen_supply', 'room'],
             'vessel.provide': ['decomposition_vessel'], 'vessel.scan': ['rfid_sensor', 'decomposition_vessel'], 'vessel.register': ['touch_display'],
             'vessel.prepare': ['decomposition_vessel'], 'vessel.hang': ['filling_head', 'decomposition_vessel'], 'vessel.remove': ['filling_head', 'decomposition_vessel'],
             'vessel.inspect': ['decomposition_vessel'], 'washings.titrate': ['decomposition_vessel'], 'vessel.clean': ['decomposition_vessel'],
             'vessel.set_c_value': ['vessel_registry'], 'vessel.select_calibrations': ['vessel_registry'], 'measurement.evaluate': ['archive', 'vessel_registry'],
             'measurement.create': ['archive'], 'measurement.delete': ['archive'], 'sim.step': ['host_clock'],
             'measurement.cancel': ['lift', 'inner_vessel', 'decomposition_vessel'], 'measurement.modify': ['archive'], 'measurement.convert': ['archive', 'vessel_registry'],
             'vessel.modify': ['vessel_registry', 'archive'], 'vessel.delete': ['vessel_registry'], 'aid.delete': ['touch_display'], 'user.save': ['touch_display'],
             'user.delete': ['touch_display'], 'user.select': ['touch_display'], 'preferences.configure': ['touch_display'], 'clock.set': ['touch_display'],
             'modules.configure': ['touch_display'], 'system.test_restart': ['outer_vessel', 'cooling_water'], 'system.test_skip': ['touch_display'],
             'system.factory_reset': ['touch_display', 'outer_vessel'], 'maintenance.o2_fill': ['oxygen_valves', 'decomposition_vessel'],
             'maintenance.change_seal': ['oxygen_valves'], 'device.empty_for_transport': ['inner_vessel', 'water_valves']}
_ROBOT = '实体操作为宿主/机器人技能的过程层抽象，没有接触或机器人验证；模型中C 6012网格始终挂在盖下，不随操作移动。'
_ROLLBACK = ('断电未经菜单关机时回到上次关机保存的状态：其后新建的记录丢失，改动或删除的已保存记录恢复，标定选择与C值回到当时（手册10.3的工程解释）；'
             '断电中止测量时不排气、不排水，氧弹在排气前一直算带压。')
CAP_LIMITS = {
    'plant.configure': ['宿主声明的实验室条件；冷却水与氧气供给的压力波动、滤水器堵塞等动态不建模（流量过低只按声明值判E31/E32）。'],
    'vessel.provide': ['只在过程层增加实体氧弹；C 6010与C 6012在本模型中只有热容工程值不同（约1150/1200J/K），自由容积同为0.22L（工程值）。'],
    'power.set': [_ROLLBACK], 'power.toggle': [_ROLLBACK, '主电源翘板开关按Matterix面板键约定建为一次按压切换的弹簧键，不是保持式翘板。'],
    'system.shutdown': ['只在等待状态可用（手册10.3）；保存全部记录与C值数据。'],
    'mode.set': ['换模式后的自检时长60s为工程值（手册11.4只写运行自检）；本型号没有绝热模式。'],
    'settings.configure': ['出厂参考燃烧值26461J/g；使用NIST SRM 39j时须设为证书值26434J/g。'],
    'vessel.scan': [_ROBOT, 'RFID读卡失败（E30）未建模。'], 'vessel.prepare': [_ROBOT, '称样误差按0.1mg天平示值量化；助燃物热值为标签值假设。'],
    'vessel.hang': [_ROBOT], 'vessel.remove': [_ROBOT], 'vessel.inspect': [_ROBOT], 'vessel.clean': [_ROBOT],
    'washings.titrate': ['洗液滴定是实验室分析步骤的抽象（总酸带2e-5mol误差），不是C 6000的功能。'],
    'measurement.start': ['测量顺序时长与稳定判据、Regnault–Pfaundler与动态外推评价为本资产的工程实现，不是IKA固件；E03/E24/E29超时在正常条件下不触发。',
                          '结果按确认时锁定的C值计算；燃烧、酸生成与挥发损失为模型值（见mechanisms限制）。'],
    'measurement.confirm': ['自动识别时只核对启动后扫描的RFID；挂上的实体氧弹与扫描不符无法被仪器发现（与真实仪器相同）。'],
    'measurement.evaluate': ['DIN 51900酸校正常数取Parr 6300手册（HNO3 14.1cal/meq、H2SO4 72.2kcal/mol）；卤素酸校正不支持。'],
    'measurement.delete': ['每个agent只能删除自己的记录；归档400条上限没有按agent的配额。'],
    'maintenance.run': ['只建模搅拌、内/外水路流量检查、RFID读取、氧弹排气与注/排水；手动充氧、换密封圈与排空见各自能力。'],
    'maintenance.change_seal': ['充氧活塞位置只作为状态（maintenance.seal_change），没有几何与机械模型。'],
    'device.empty_for_transport': ['只排空内筒水路；外筒水路排空与运输准备未建模（外筒热节点保留）。'],
    'preferences.configure': ['语言（英/德）、单位（J/g、kJ/kg、cal/g、BTU/lb）与报告标准（DIN 51900、ISO 1928、ASTM D5865、ASTM D240）的选项清单为假设；'
                              '所有报告标准都按DIN 51900的酸校正与Hu公式计算，只改结果页标注。'],
    'modules.configure': ['本配置没有连接天平、样品架与打印机：启用后请求天平值、样品架与打印在面板上显示未连接；Tap water与声明的冷却水来源不符时自检为黄色（假设）。'],
    'system.factory_reset': ['恢复出厂只重置设置、辅料、用户、模式与模块；登记的氧弹与测量记录保留（假设）。'],
    'sim.step': ['唯一的宿主时间推进；逻辑与读数在1s仪器周期上执行，升降与搅拌连续推进。'],
}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.c6000_isoperibol_bomb_calorimetry.limitations。'


def _foreign_run_text(k):
    """Session guard shared by the control capabilities (IkaC6000Session._guard)."""
    if k not in _CONTROL_GUARDED:
        return ''
    return '；另一个agent的测量进行中时，agent调用被拒（ACCESS_DENIED' + ('；未通电时开机除外' if k in ('power.set', 'power.toggle') else '') + '）'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    access = ['ACCESS_DENIED'] if (CAPS[k]['visibility'] != 'agent' or k in _GUARDED) else []
    return list(dict.fromkeys(_COMMON_ERRORS + access + CAP_ERRORS.get(k, [])))


OBSERVES = {
    'plant.configure': ['plant.configured'], 'vessel.provide': [], 'power.set': ['power.enabled', 'system.state'], 'power.toggle': ['power.enabled', 'system.state'],
    'system.shutdown': ['system.state', 'cover.state'], 'error.acknowledge': ['system.error_code', 'system.state'], 'mode.set': ['mode.working', 'outer.indicated_k'],
    'settings.configure': ['settings.o2_flush', 'settings.auto_vessel_detection', 'settings.reference_value_j_kg'], 'aid.define': [],
    'vessel.scan': ['vessel.last_scanned_rfid'], 'vessel.register': ['vessels.registered'], 'vessel.prepare': [], 'vessel.hang': ['vessel.hung_rfid'],
    'vessel.remove': ['vessel.hung_rfid'], 'vessel.inspect': [], 'washings.titrate': [], 'vessel.clean': [], 'vessel.set_c_value': ['vessels.registered'],
    'vessel.select_calibrations': ['vessels.registered'], 'vessel.statistics': ['vessels.registered'], 'measurement.create': [],
    'measurement.start': ['measurement.id', 'measurement.phase', 'inner.indicated_k', 'cover.state'], 'measurement.confirm': ['measurement.phase', 'cover.state'],
    'measurement.result': ['result.delta_t_k', 'result.gross_calorific_value_j_kg', 'result.c_value_j_k'], 'measurement.evaluate': ['vessels.registered'],
    'measurement.simulate': [], 'measurement.delete': [],
    'archive.list': [], 'events.read': ['system.error_code'], 'cover.open': ['cover.state'], 'cover.close': ['cover.state'],
    'maintenance.run': ['stirrer.running', 'stirrer.indicated_rpm'], 'ledger.read': [], 'provenance.read': [], 'sim.step': ['clock.time_s'],
    'measurement.cancel': ['measurement.phase'], 'system.test_restart': ['system.state', 'system.test_result'], 'system.test_skip': ['system.test_result'],
    'maintenance.change_seal': ['maintenance.seal_change'],
}
_MAN, _DS, _WB, _BR = 'manufacturer_manual', 'manufacturer_primary', 'user_workbook', 'manufacturer_brochure_2018'
CAP_SOURCES = {
    'plant.configure': [_DS, _MAN, 'simulation_assumption'], 'vessel.provide': [_DS, _BR, 'simulation_assumption'], 'power.set': [_MAN], 'power.toggle': [_MAN],
    'system.shutdown': [_MAN], 'error.acknowledge': [_MAN], 'fault.inject': [_MAN, 'simulation_assumption'], 'fault.clear': ['simulation_assumption'], 'mode.set': [_DS, _MAN, _WB], 'settings.configure': [_MAN, 'nist_srm39j'], 'aid.define': [_MAN],
    'vessel.scan': [_MAN, _DS], 'vessel.register': [_MAN, _BR], 'vessel.prepare': [_MAN, 'combustion_library', 'simulation_assumption'], 'vessel.hang': [_MAN],
    'vessel.remove': [_MAN], 'vessel.inspect': [_MAN], 'washings.titrate': [_MAN, 'parr_6300_manual', _BR, 'simulation_assumption'], 'vessel.clean': [_MAN],
    'vessel.set_c_value': [_MAN], 'vessel.select_calibrations': [_MAN], 'vessel.statistics': [_MAN], 'measurement.create': [_MAN, _WB],
    'measurement.start': [_MAN, _DS, _WB, 'plant_derivation', 'combustion_library', 'parr_6300_manual', 'nist_srm39j'], 'measurement.confirm': [_MAN],
    'measurement.result': [_MAN, _DS], 'measurement.evaluate': [_MAN, 'parr_6300_manual', 'nist_webbook', 'polymer_bomb_heats'], 'measurement.simulate': [_MAN],
    'measurement.delete': [_MAN],
    'archive.list': [_MAN], 'events.read': [_MAN], 'cover.open': [_MAN, _BR], 'cover.close': [_MAN, _BR], 'maintenance.run': [_MAN],
    'ledger.read': ['runtime_code'], 'provenance.read': ['combustion_library'], 'sim.step': ['runtime_code', 'plant_derivation'],
    'mode.adiabatic': [_MAN, _WB, _BR], 'sample_rack.use': [_DS, _MAN], 'data.export': [_MAN, _DS], 'maintenance.change_seal': [_MAN],
    'device.empty_for_transport': [_MAN], 'sample.halogen': [_DS, _MAN, 'combustion_library'], 'hardware.connect': [_DS], 'robot.handle_vessel': ['simulation_assumption'],
    'accuracy.certification': [_DS, _BR],
    **{k: [_MAN] for k in ('measurement.cancel', 'measurement.modify', 'measurement.convert', 'vessel.modify', 'vessel.delete', 'aid.delete', 'user.save', 'user.delete',
                           'user.select', 'clock.set', 'system.test_restart', 'system.test_skip', 'maintenance.o2_fill')},
    'preferences.configure': [_MAN, 'simulation_assumption'], 'modules.configure': [_MAN, 'simulation_assumption'], 'system.factory_reset': [_MAN, 'simulation_assumption']}
PARAM_ZH = {
    'ambient_k': '实验室环境温度（数据表允许20–30°C）', 'cooling_source': '冷却水来源（自来水经C 25减压阀，或冷却器/冷凝器）', 'cooling_water_k': '冷却水温度（数据表12–27°C）',
    'cooling_flow_m3_s': '冷却水流量（数据表60–70L/h）', 'cooling_pressure_pa': '冷却水表压（≤1.5bar）', 'oxygen_supply_pa': '氧气供给绝对压力（30bar，≤40bar）',
    'oxygen_purity': '氧气纯度（3.5级=99.95%）', 'noise_seed': '随机数种子（称量噪声、棉线与点火能、充水量、酸生成）', 'rfid': '实体氧弹RFID编号', 'model': '氧弹型号',
    'sample': '样品（库中物质、质量、形态）', 'aids': '助燃物（明胶胶囊或石蜡油）与质量', 'absorption_water_kg': '预先加入氧弹的吸收水质量',
}
LIMITATIONS = [
    '选定C 6000 isoperibol + 一个C 6012耐卤素氧弹（Package 2/12，不含RC 2冷却器与样品架）；只有等温外套与动态模式（22/25/30°C），没有绝热模式；打印、U盘/以太网导出与CalWin通信未建模。',
    '量热系统为四节点集总模型（氧弹快节点与慢节点、内筒水+内筒金属+搅拌+探头、外筒水）；热容、传热系数、外筒换热器效率、加热功率、搅拌热与氧弹内体积均为工程值，由厂家“40000J约5K”“1g苯甲酸约3K”与每小时4/6次测量反推并在derive_plant_parameters.json中核对，不是厂家数据。',
    '测量顺序按手册：关盖、注水（冷却水温度）、45s后温度检查（E07再等180s）、充氧（可冲洗）、平衡、前期漂移判据、点火、1min温升检查（E04）、主期稳定判据、后期、计算、排气、排水、开盖；各阶段时长与判据阈值为工程值。',
    '等温外套结果用Regnault–Pfaundler校正（前后期12s平均直线与主期1s读数积分）；动态模式截短主期并用指数外推（Dickinson型），其方法偏差由同模式标定吸收——与厂家“每个工作模式分别标定”一致；两种方法都是本资产的实现，不是IKA固件算法。',
    '燃烧放热来自CHEM-150模板库（NIST生成焓推导的标准态恒容高位热值，未做Washburn换算；聚合物为FAA氧弹实测值；苯甲酸用NIST SRM 39j证书值与条件修正式）；氧压<2MPa、苯甲酸未压片、氧量不足时燃烧不完全；未装胶囊的挥发性液体在充氧冲洗中按饱和蒸气损失。',
    '酸生成：残余空气与氧气杂质中的N2按与放热相关的工程分数生成HNO3、样品N的10%生成HNO3、样品S全部生成H2SO4；生成热用Parr 6300手册常数（14.1cal/meq、72.2kcal/mol）；卤素样品不支持。',
    '仪器读数：T1带2s探头滞后、0.1mK噪声与0.0001K分辨率，T2、T5、T6示值；点火电能约30J（工程值，仪器测得值带0.5J误差）、棉线50J（出厂值，实际±4%）；充水量重复性0.3g；这些随机项使重复标定的RSD与厂家0.05%/0.15%同量级（见验证记录）。',
    '断电未经菜单关机时回到上次菜单关机保存的状态：其后新建的记录丢失，改动或删除的已保存记录恢复，各氧弹的标定选择、C值与手动标记回到当时（登记与点火计数保留）——手册10.3的工程解释；'
    '断电中止测量时盖停在原位、水不排、氧弹不排气，氧弹在排气前一直算带压（取弹、检查、滴定、清洁被拒，需维护程序o2_degas）。',
    '换工作模式后仪器自检（手册11.4）按60s工程值；出厂参考燃烧值26461J/g（手册10.3），使用NIST SRM 39j苯甲酸时须设为证书值26434J/g；O2冲洗为充氧后排空两次（手册11.4.2）。',
    'DIN 51900酸校正：标定评价后C值含标定自身的酸生成热（手册6.1 Qext含酸），测量评价给出校正后Ho与Hu；RFID读卡失败（E30）未建模，冷却水与模式不符（E26）以拒绝码RUN_MODE_NOT_POSSIBLE表示。',
    '实体操作（称样、装弹、RFID移动、挂弹、取弹、检查、洗液滴定、清洁）是宿主/机器人技能的过程层抽象，没有接触或机器人验证；场景中C 6012氧弹固定挂在盖下。',
    'agent只看到示值、设定、结果与事件；真实温度、热容、放热、气体与酸摩尔数、燃烧分数只对evaluator可见；滴定与残渣检查返回的是带误差的观察值。',
    '没有真实硬件、IKA固件或厂家准确度验证。',
]


PANEL_PRECONDITION_ZH = ('面板按当前屏幕与手册规则处理手势：断电时屏幕黑；软件加载约30s、自检约5min（安全须知须确认）后进入主菜单；错误信息须确认；被调用能力的前提同样适用'
                         '（被拒时显示仪器的信息，手势不被接受）；经会话的手势以该会话调用者身份执行：取消、删除、修改、评价另一agent的测量或记录与直接调用一样被拒（ACCESS_DENIED），'
                         '新建的测量与启动的测量归调用者；屏幕本身显示全部记录')
PANEL_LIMITATION_ZH = '面板层按panel_ui.json（手册PDF页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟；手册未写之处（页面布局、语言与单位清单、提示文字）见panel_ui.json的assumptions。'


def export_ika_c6000_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'IkaC6000Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation',
                                      window_description='Pure read of the latest 1 s instrument cycle; T1/T2 at 0.0001 K resolution with probe lag and noise.'),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.ika_c6000.IkaC6000Session.invoke',
                                              invocation=f'session.invoke(instance_id="c6000_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_run_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['measurement.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='IkaC6000Runtime.invoke / IkaC6000Session._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定、操作或状态标志；温度、升降、搅拌与测量进度只随step推进')
                                for s in v['effects']],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=dict(mode='none', description_zh='不支持：不实现，没有模型变化' if un else MODEL_NONE_ZH.get(k, '状态绑定到场景显示/关节（见合同model_bindings）'), binding_ids=[]),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion='测量结束（完成、失败或取消）：排气、排水并开盖后记录状态确定' if async_ else '原子设定、配置或查询，实际物理状态由step推进',
                                completion_state_ids=['measurement.id', 'measurement.phase', 'result.delta_t_k'] if async_ else [],
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id',
                                resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('点火前取消或出错：样品保留（挥发性样品可能已损失），记录可再次启动；点火后取消或出错：样品已燃烧、氧弹点火次数已计，记录不能再次启动；'
                                                    '两种情况都照常排气、排水、开盖（进入这一结束程序后不能再取消）；断电中止时不排气不排水。') if async_ else
                                                   ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH[e], side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in _TIMED:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh='由统一宿主时钟推进升降、注排水、温度、测量阶段与结果')
                                   for s in SLOTS if s not in v['effects'] and SLOTS[s]['origin'] in ('sensor', 'derived', 'scene', 'process_truth', 'setpoint')]
        caps[k] = c
    fx = list(dict.fromkeys(x for c in _panel_ui.spec_capabilities(PANEL_SPEC) for x in CAPS[c]['effects']))
    for k, d in PANEL_CAPS.items():
        g = k in PANEL_GESTURES
        caps[k] = dict(function_zh=d['function_zh'], kind=d['kind'], visibility=d['visibility'], component_ids=sorted(set(d['component_ids']) | ({'touch_bezel'} if k != 'panel.press' else set())),
                       input_schema=copy.deepcopy(d['input_schema']), output_schema=copy.deepcopy(d['output_schema']), example_arguments=copy.deepcopy(d['example_arguments']),
                       availability='implemented', implementation={layer: 'absent' if layer in ('scene', 'robot', 'hardware') else 'implemented'
                                                                  for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                       binding=dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.ika_c6000.IkaC6000Session.invoke',
                                    invocation=f'session.invoke(instance_id="c6000_01",capability_id={k!r},arguments={d["example_arguments"]!r},request_id="unique")'),
                       preconditions=[dict(description_zh=PANEL_PRECONDITION_ZH, state_ids=['power.enabled', 'system.state', 'system.error_code', 'measurement.id'],
                                           enforced_by='runtime', check='panel_ui.json screens, gates and bindings')] if g else [],
                       state_effects=[dict(state_id=x, timing='immediate', description_zh='面板手势调用的能力的效果（与直接调用同一能力相同）') for x in fx] if g else [],
                       observes=[], model_effects=dict(mode='none', description_zh='屏幕纹理与触摸联动属于后续Matterix步骤，本版本只在过程层。', binding_ids=[]),
                       execution=dict(mode='synchronous', time_domain='simulation',
                                      completion_criterion='手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕须用panel.capture查看' if g else '不推进物理时钟',
                                      completion_state_ids=fx if g else [], timeout_s=None, cancellation='unsupported', retry='same_request_id',
                                      resources=['touch_display'], partial_failure_zh='手势调用的每个能力各自先检查再修改；被拒绝时显示仪器自己的信息，手势返回accepted=false'),
                       errors=[dict(code=e, meaning_zh=ERROR_ZH[e], side_effects='none') for e in _COMMON_ERRORS],
                       verification=[], limitations=[PANEL_LIMITATION_ZH], source_ids=[_MAN, 'simulation_assumption'])
    params = {}
    units = dict(C_METAL='J/K', WATER_KG='kg', G_SHELL='W/K', G_HEAD='W/K', G_WJ='W/K', G_WA='W/K', G_BA='W/K', C_JACKET='J/K', G_JA='W/K', HX_EFF='1',
                 P_JACKET_MAX='W', P_INNER_MAX='W', P_STIRRER='W')
    mean = dict(C_METAL='内筒金属+搅拌+探头热容', WATER_KG='内筒注水质量', G_SHELL='氧弹快节点与内筒水的传热系数', G_HEAD='氧弹慢节点（头部与配件）与内筒水的传热系数',
                G_WJ='内筒水与外筒之间确定的热交换（等温外套）', G_WA='内筒经盖对室温的散热', G_BA='氧弹在空气中的散热', C_JACKET='外筒水与绝热层热容',
                G_JA='外筒对室温散热', HX_EFF='外筒冷却换热器效率（乘冷却水热容流量）', P_JACKET_MAX='外筒加热器最大功率', P_INNER_MAX='内水路加热器最大功率',
                P_STIRRER='搅拌输入功率')
    for name, val in PLANT_FIT.items():
        params['fit_' + name.lower()] = dict(value=val, unit=units[name], meaning_zh=mean[name], evidence='assumption', source_ids=['plant_derivation', 'simulation_assumption'],
                                             uncertainty='工程值：由厂家40000J约5K、1g苯甲酸约3K、每小时4/6次与0.05%/0.15%重复性在derive_plant_parameters.json中反推与核对，不是厂家数据')
    for name, (val, unit, meaning, src) in {
            'max_energy_j': (MAX_ENERGY_J, 'J', '量程最大能量', _DS), 'resolution_k': (RESOLUTION_K, 'K', '温度测量分辨率', _DS), 'o2_nominal_pa': (O2_NOMINAL_PA, 'Pa', '氧气压力30bar', _MAN),
            'o2_max_pa': (O2_MAX_PA, 'Pa', '氧气最大工作压力40bar', _DS), 'protocol_s': (PROTOCOL_S, 's', '协议温度平均间隔12s', _MAN), 'cotton_thread_j': (COTTON_THREAD_J, 'J', '出厂棉线外加能量', _MAN),
            'system_test_s': (SYSTEM_TEST_S, 's', '初次自检约5min', _MAN), 'warm_up_s': (WARM_UP_S, 's', '建议预热约1h', _MAN),
            'hno3_j_mol': (HNO3_J_MOL, 'J/mol', 'HNO3生成热（14.1cal/meq）', 'parr_6300_manual'), 'h2so4_j_mol': (H2SO4_J_MOL, 'J/mol', 'H2SO4自SO2生成热（72.2kcal/mol）', 'parr_6300_manual'),
            'max_vessels': (float(MAX_VESSELS), '1', '每台最多氧弹数', _BR), 'pressure_test_ignitions': (float(PRESSURE_TEST_IGNITIONS), '1', '建议压力检验的点火次数', _BR),
            'lift_travel_m': (LIFT_TRAVEL_M, 'm', '开盖升降行程（620−425mm）', _BR)}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=[src], uncertainty=None)
    for name, (val, unit, meaning) in {
            'vessel_volume_m3': (VESSEL_VOLUME_M3['C 6012'], 'm^3', 'C 6012氧弹内气体体积（工程值）'), 'vessel_head_share': (VESSEL_HEAD_SHARE, '1', '氧弹慢节点热容份额'),
            'sensor_tau_s': (SENSOR_TAU_S, 's', 'T1探头时间常数'), 'sensor_noise_k': (SENSOR_NOISE_K, 'K', 'T1读数噪声'), 'ignition_mean_j': (IGNITION_MEAN_J, 'J', '点火电能均值'),
            'xi_air_hno3': (XI_AIR_HNO3, '1', '残余N2生成HNO3分数（26.4kJ时）'), 'xi_sample_n': (XI_SAMPLE_N, '1', '样品N生成HNO3分数'), 'fill_rate_kg_s': (FILL_RATE_KG_S, 'kg/s', '注/排水速率'),
            'lift_speed_m_s': (LIFT_SPEED_M_S, 'm/s', '升降速度'), 'stirrer_rpm': (STIRRER_RPM, 'rpm', '搅拌转速'),
            'kp_jacket_w_k': (KP_JACKET_W_K, 'W/K', '外筒PI比例增益'), 'ti_jacket_s': (TI_JACKET_S, 's', '外筒PI积分时间'), 'kp_inner_w_k': (KP_INNER_W_K, 'W/K', '内水路加热比例增益')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=['simulation_assumption'], uncertainty='工程值')
    mechanism = dict(
        component_ids=['decomposition_vessel', 'inner_vessel', 'outer_vessel', 'temperature_probe', 'stirrer_shaft', 'filling_head', 'o2_valve_group', 'water_valve_group',
                       'circulation_pump', 'electronics', 'lift_drive', 'cover_shell'],
        state_ids=list(slots), meaning_zh='C 6000等温外套氧弹量热：四节点热模型与外筒调节、测量顺序（注水、充氧、平衡、前期、点火、主期、后期、排气排水）、燃烧与酸生成、Regnault–Pfaundler/动态外推评价、标定C值统计',
        implementation='lumped',
        equations=['C_s dT_s/dt = G_s (T_w - T_s) ; C_h dT_h/dt = G_h (T_w - T_h)   (vessel immersed; in air: G_BA (T_a - T))',
                   'C_w dT_w/dt = G_s (T_s - T_w) + G_h (T_h - T_w) + G_WJ (T_j - T_w) + G_WA (T_a - T_w) + P_stir + P_inner ; C_w = C_metal + m_w c_w',
                   'C_j dT_j/dt = P_jacket - eps m_dot c_w (T_j - T_cool) - G_JA (T_j - T_a) + G_WJ (T_w - T_j) ; P_jacket = PI(T_work - T2)',
                   'Q = m q_V,gr f_burn + E_thread + E_ign + sum m_aid q_aid + n_HNO3 dH_HNO3 + n_H2SO4 dH_H2SO4 ; dT_s = Q (1-s)/C_s , dT_h = Q s/C_h',
                   'n_HNO3 = xi_air sqrt(Q/26.4 kJ) 2 n_N2 + xi_N n_N,sample ; n_H2SO4 = n_S,sample',
                   'isoperibol: dT = theta_n - theta_0 - int_{t0}^{tn} [g_f + (g_a - g_f)(theta - theta_f)/(theta_a - theta_f)] dt',
                   'dynamic: theta(t) = a + b x - c exp(-x/tau) fitted on the shortened main trial ; dT = (a + b tau) - (theta_0 + g_f tau)',
                   'Ho = (C dT - Q_ext)/m ; C = (H_ref m + Q_ext + Q_acid)/dT (Q_acid after measurement.evaluate of the calibration) ; Ho,DIN = Ho - Q_acid/m ;'
                   ' Hu = Ho,DIN - h_vap,w (8.936 w_H + w_H2O)'],
        parameters=params, source_ids=['simulation_assumption', 'plant_derivation', 'combustion_library', _DS, _MAN, 'parr_6300_manual', 'nist_srm39j'], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_c6000_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'c6000_isoperibol_bomb_calorimetry': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'vessel.prepare': (PREPARE, PREPARE_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'c6000_isoperibol_bomb_calorimetry': mechanism})
