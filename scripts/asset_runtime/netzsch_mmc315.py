"""CHEM-148 NETZSCH MMC 315 Nexus multi-module calorimeter with the ARC module: runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * two lumped heat nodes: calorimeter jacket (top/side/bottom heaters, heat capacity C_j, loss G_ja to the room, optional
    cooling fan) and the sample + container (C_s = m_s c_s + m_v c_v) coupled by G_sj; the linear 2x2 system is advanced with
    its exact solution for piecewise-constant heater power and reaction heat (energy integrals booked analytically);
  * instrument cycle of 1 s (engineering): thermocouple/pressure sampling, correlated noise, test logic and a digital
    feed-forward + PI jacket controller whose power is held until the next cycle (physics sub-steps never cross a cycle);
    heater power limited to P_max; adiabatic tracking follows the measured sample temperature with the documented
    50 K/min tracking-rate limit;
  * decomposition kinetics of the loaded template: n-th order Arrhenius (closed-form update stable for every order), adaptively
    sub-stepped, heat m_r dH dalpha and gas nu n_r dalpha released exactly per sub-step;
  * container pressure: pad air (sealed in room air saturated with solvent vapour) + generated gas (ideal gas in the headspace =
    container + pressure-line dead volume - liquid) + solvent vapour (NIST Antoine, continued smoothly above the critical
    temperature, bounded by complete evaporation); readings saturate at the documented 150 bar detection limit;
  * instrument logic: Heat-Wait-Search with an exothermal threshold (documented example 0.02 K/min) on the regression slope of
    the indicated sample temperature, adiabatic tracking until the self-heating stops, Iso-Fixed / Iso-Track ageing with the
    same search windows, constant jacket heating-rate segments (0-5 K/min documented) that change to adiabatic mode when the
    sample overtakes the jacket, end criteria (temperature <= 500 C, pressure <= 150 bar, self-heat rate), over-temperature
    cut-off, cooling; results: onset, dT, phi, dT_ad, TMR, maximum self-heat and pressure rates, Townsend-Tou kinetics;
  * measured values: thermocouples with static offsets and correlated noise (0.01 K readability), pressure transducer
    (0.01 bar readability), all agent-visible readings from the same noisy measurement.
Templates (first-order kinetics of 51 substances, DTBP with an ARC measurement) come from arc_decomposition_library.json;
heat capacities, heats of decomposition (except DTBP), gas yields and all thermal-plant numbers are explicit host priors.
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
SLUG = 'netzsch_mmc315'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-148'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t1_thermal' / SLUG
LIBRARY_PATH = ASSET / 'arc_decomposition_library.json'

# ---------------------------------------------------------------- documented / physical constants
GAS_CONSTANT = 8.314462618
STD_PRESSURE_PA = 101325.0
MAX_TEMPERATURE_K = 773.15                      # documented RT to 500 degC (ARC module)
MAX_PRESSURE_PA = 150e5                         # documented max. pressure 150 bar
TRACKING_RATE_K_S = 50.0 / 60.0                 # documented tracking rate 50 K/min (ARC and scanning module)
HEATING_RATE_MAX_K_MIN = 5.0                    # documented ARC module heating rate 0-5 K/min (MMC 274 brochure)
TEMPERATURE_READABILITY_K = 0.01                # documented (MMC 274 brochure)
PRESSURE_READABILITY_PA = 0.01e5                # documented 0.01 bar (MMC 274 brochure)
CONTAINER_VOLUME_RANGE_M3 = (0.5e-6, 8.5e-6)    # workbook / 2026 overview: ARC module container 0.5-8.5 ml
EXAMPLE_THRESHOLD_K_MIN = 0.02                  # documented exothermal threshold of the DTBP example
TRAY_TRAVEL_M = 0.250
LID_OPEN_RAD = 1.90
POWER_SWITCH_ON_RAD = 0.30
CONTROL_PERIOD_S = 1.0                          # instrument sampling / control cycle (engineering)
CONTROL_NS = 1_000_000_000
CONTROLLER_MAX_LOOP_GAIN = 0.5                  # Kp * cycle / C_j bound of the digital PI (engineering stability margin)
MAX_SUBSTEP_S = CONTROL_PERIOD_S
RATE_WINDOW_S = 120.0                           # regression window of the exotherm-tracking rate (engineering)
MIN_REGRESSION_S = 120.0                        # search windows: at least this settling time, then regression over the rest
PRESSURE_RANGE_PA = 150e5                       # documented: pressures of up to 150 bar can be detected (readings saturate)
TC_RANGE_MAX_K = 1573.15                        # thermocouple reading range (type not published; engineering)
TC_MISMATCH_MAX_K = 0.02                        # calibrated sample/jacket thermocouple pair: well inside the 0.05 K end / 0.10 K start excess levels
TC_NOISE_MAX_K = 0.01                           # ARC-grade thermocouple noise bound (0.01 K readability, 0.02 K/min detection; engineering)
TC_NOISE_TAU_S = 20.0                           # correlation time of the thermocouple noise (engineering)
PRESSURE_NOISE_TAU_S = 10.0
END_WINDOW_MIN_S = 600.0                        # an adiabatic event ends when the slope over max(search regression, 10 min) is below half the threshold
DETECTION_SIGMAS, END_SIGMAS = 6.0, 4.0        # detection-limit margins in standard errors
INJECTION_FACTOR = 1.5                          # tracking-injected noise: 1.5 x the analytic term (runtime data: measured/analytic 0.8-1.15)
BUF_KEEP_S = END_WINDOW_MIN_S + 60.0
OVERTEMPERATURE_K = 823.15                      # over-temperature cut-off 50 K above the documented 500 degC range (engineering)
MAX_ADIABATIC_RISE_K = 1000.0                   # model range at load: m_r dH / C_s above this is rejected
COOL_MIN_ABOVE_AMBIENT_K = 5.0                  # cooling target at least 5 K above the room (fan / natural cooling only)
COOL_TIMEOUT_S = 48 * 3600.0
EXCESS_DETECT_K = 0.10                          # ramp / Iso-Fixed: sample hotter than the programmed or held jacket by this margin = self-heating
MAX_POINTS = 20000                              # result curve points per test (schema maxItems)
MAX_EXOTHERMS = 20
MAX_PROVENANCE = 1000
UNLOAD_MAX_K = 333.15                           # container may be removed only below 60 degC (engineering safety rule)
CONTAINER_MATERIALS = ('titanium', 'hastelloy', 'stainless_steel', 'inconel', 'glass_inlet')   # documented materials + the titanium sphere of the DTBP example


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
PHASE = enum(['idle', 'running', 'succeeded', 'cancelled', 'failed'])
STAGE = enum(['idle', 'heat', 'wait', 'search', 'exotherm', 'iso', 'ramp', 'cool', 'done'])
JACKET_MODE = enum(['off', 'programme', 'isothermal_hold', 'adiabatic_tracking', 'cooling'])


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
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (k == 'integer' and type(v) is not int):
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


_LIBRARY = None


def library():
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = json.loads(LIBRARY_PATH.read_text())
    return _LIBRARY


def templates():
    return {t['id']: t for t in library()['templates']}


def quantize(value, step):
    return round(value / step) * step


def solvent_vapour_pressure_pa(name, T):
    """log10(P/bar) = A - B/(T + C) with the NIST WebBook parameter sets; log-linear bridge between sets.  Above the critical
    temperature the highest set is continued smoothly (engineering pseudo-saturation curve, no phase jump); the caller caps it
    by complete evaporation of the solvent charge."""
    if name == 'none':
        return 0.0
    s = library()['solvents'][name]
    sets = sorted(s['antoine_log10_bar'], key=lambda a: a['T_min_K'])

    def log10p(a, TT):
        return a['A'] - a['B'] / (TT + a['C'])
    inside = [a for a in sets if a['T_min_K'] <= T <= a['T_max_K']]
    if inside:
        a = max(inside, key=lambda a: a['T_max_K'] - a['T_min_K'])
        return 1e5 * 10 ** log10p(a, T)
    below = [a for a in sets if a['T_max_K'] < T]
    above = [a for a in sets if a['T_min_K'] > T]
    if below and above:
        lo = max(below, key=lambda a: a['T_max_K']); hi = min(above, key=lambda a: a['T_min_K'])
        w = (T - lo['T_max_K']) / (hi['T_min_K'] - lo['T_max_K'])
        return 1e5 * 10 ** ((1 - w) * log10p(lo, T) + w * log10p(hi, T))
    a = below[-1] if below else above[0]
    return 1e5 * 10 ** log10p(a, T)


# ---------------------------------------------------------------- inputs
PLANT_EXAMPLE = dict(ambient_k=296.15, ambient_pressure_pa=101325.0, jacket_heat_capacity_j_k=1200.0, jacket_heater_max_w=1200.0, jacket_loss_w_k=0.15,
                     cooling_fan_w_k=0.50, sample_jacket_conductance_w_k=0.100, controller_gain_w_k=150.0, controller_integral_s=120.0,
                     sample_tc_offset_k=0.004, jacket_tc_offset_k=-0.004, tc_noise_k=0.003, pressure_offset_pa=300.0, pressure_noise_pa=400.0,
                     line_dead_volume_m3=1.0e-6, tray_speed_m_s=0.10, lid_speed_rad_s=1.0, noise_seed=148, accept_engineering=True)
PLANT = obj(dict(ambient_k=number('K', 283.15, 308.15), ambient_pressure_pa=number('Pa', 80000.0, 110000.0),
                 jacket_heat_capacity_j_k=number('J/K', 100.0, 20000.0), jacket_heater_max_w=number('W', 50.0, 10000.0), jacket_loss_w_k=number('W/K', 0.01, 20.0),
                 cooling_fan_w_k=number('W/K', 0.0, 50.0), sample_jacket_conductance_w_k=number('W/K', 0.005, 2.0),
                 controller_gain_w_k=number('W/K', 1.0, 5000.0), controller_integral_s=number('s', 5.0, 5000.0),
                 sample_tc_offset_k=number('K', -0.5, 0.5), jacket_tc_offset_k=number('K', -0.5, 0.5), tc_noise_k=number('K', 0.0, TC_NOISE_MAX_K),
                 pressure_offset_pa=number('Pa', -1e5, 1e5), pressure_noise_pa=number('Pa', 0.0, 1e5), line_dead_volume_m3=number('m^3', 0.0, 2e-5),
                 tray_speed_m_s=number('m/s', 0.01, 1.0), lid_speed_rad_s=number('rad/s', 0.1, 5.0), noise_seed=INT, accept_engineering=BOOL))
METHOD_EXAMPLE = dict(mode='hws', start_temperature_k=323.15, end_temperature_k=493.15, step_k=10.0, heat_rate_k_min=2.0, wait_min=15.0, search_min=15.0,
                      threshold_k_min=0.02, max_pressure_pa=140e5, max_rate_k_min=50.0, iso_temperature_k=None, iso_hours=None, cool_to_k=323.15, data_interval_s=30.0)
METHOD = obj(dict(mode=enum(['hws', 'iso_fixed', 'iso_track', 'ramp']), start_temperature_k=number('K', 283.15, MAX_TEMPERATURE_K),
                  end_temperature_k=number('K', 293.15, MAX_TEMPERATURE_K), step_k=number('K', 1.0, 50.0), heat_rate_k_min=number('K/min', 0.1, HEATING_RATE_MAX_K_MIN),
                  wait_min=number('min', 5.0, 120.0), search_min=number('min', 5.0, 240.0), threshold_k_min=number('K/min', 0.005, 1.0),
                  max_pressure_pa=number('Pa', 2e5, MAX_PRESSURE_PA), max_rate_k_min=number('K/min', 1.0, TRACKING_RATE_K_S * 60),
                  iso_temperature_k=nullable(number('K', 293.15, MAX_TEMPERATURE_K)), iso_hours=nullable(number('h', 0.1, 500.0)),
                  cool_to_k=number('K', 298.15, 423.15), data_interval_s=number('s', 5.0, 600.0)))
LOAD_EXAMPLE = dict(template_id='di_tbutyl_peroxide', sample_mass_kg=5.74e-3, reactant_mass_fraction=0.20, solvent='toluene', container_material='titanium',
                    container_volume_m3=8.5e-6, container_mass_kg=10.028e-3, container_cp_j_kg_k=576.8, sample_cp_j_kg_k=2099.3, liquid_density_kg_m3=867.0,
                    heat_of_decomposition_j_kg=None, gas_yield_mol_per_mol=0.336, reaction_order=1.0, accept_priors=True)
LOAD = obj(dict(template_id=TEXT, sample_mass_kg=number('kg', 1e-5, 0.02), reactant_mass_fraction=number('1', 0.0, 1.0), solvent=enum(['toluene', 'none']),
                container_material=enum(CONTAINER_MATERIALS), container_volume_m3=number('m^3', *CONTAINER_VOLUME_RANGE_M3), container_mass_kg=number('kg', 1e-3, 0.2),
                container_cp_j_kg_k=number('J/(kg*K)', 100.0, 2000.0), sample_cp_j_kg_k=nullable(number('J/(kg*K)', 500.0, 5000.0)),
                liquid_density_kg_m3=number('kg/m^3', 300.0, 3000.0), heat_of_decomposition_j_kg=nullable(number('J/kg', 1e3, 1e7)),
                gas_yield_mol_per_mol=nullable(number('1', 0.0, 10.0)), reaction_order=number('1', 0.5, 3.0), accept_priors=BOOL))
POINT = obj(dict(time_s=number('s'), stage=STAGE, sample_k=nullable(number('K')), jacket_k=nullable(number('K')), pressure_pa=nullable(number('Pa')),
                 self_heat_rate_k_min=nullable(number('K/min'))))
FIT_QUALITY = enum(['ok', 'too_few_points', 'poor_fit', 'implausible_parameters', 'not_adiabatic', 'incomplete_exotherm'])
KINETICS = obj(dict(model=TEXT, n=number('1'), activation_energy_j_mol=nullable(number('J/mol')), log10_a_per_s=nullable(number('1')),
                    points_used=INT, r2=nullable(number('1')), quality=FIT_QUALITY))
EXOTHERM = obj(dict(index=INT, conditions=enum(['adiabatic', 'isothermal_jacket']), onset_time_s=number('s'), onset_temperature_k=number('K'),
                    end_temperature_k=number('K'), max_temperature_k=number('K'), delta_t_observed_k=number('K'), max_excess_over_jacket_k=number('K'), phi=number('1'),
                    delta_t_adiabatic_k=nullable(number('K')), final_temperature_adiabatic_k=nullable(number('K')),
                    max_self_heat_rate_k_min=number('K/min'), temperature_at_max_rate_k=number('K'), time_to_max_rate_s=number('s'),
                    pressure_before_pa=number('Pa'), pressure_after_pa=number('Pa'), max_pressure_rate_pa_min=number('Pa/min'),
                    heat_released_j=nullable(number('J')), specific_heat_of_reaction_j_kg_sample=nullable(number('J/kg')),
                    heat_of_reaction_j_kg_reactant=nullable(number('J/kg')), tracking_rate_exceeded=BOOL, complete=BOOL, kinetics=KINETICS))
RESULT = obj(dict(operation_id=nullable(TEXT), phase=PHASE, stage=STAGE, method=TEXT, elapsed_s=number('s'), points=array(POINT), exotherms=array(EXOTHERM, 0, MAX_EXOTHERMS),
                  exotherm_count=INT, exotherms_dropped=INT, data_interval_s=number('s'),
                  max_temperature_k=nullable(number('K')), max_pressure_pa=nullable(number('Pa')), phi=nullable(number('1')),
                  validity=enum(['not_completed', 'valid', 'no_exotherm_detected', 'end_pressure_reached', 'end_rate_reached', 'pressure_limit_exceeded',
                                 'temperature_limit_exceeded', 'cancelled']),
                  terminal_reason=nullable(TEXT), template_source=nullable(TEXT), evidence=TEXT))
ANALYSIS = obj(dict(temperatures_k=array(number('K', 250.0, 1000.0), 3, MAX_POINTS), self_heat_rates_k_min=array(number('K/min', 1e-6, 1e4), 3, MAX_POINTS),
                    onset_temperature_k=number('K', 250.0, 1000.0), final_temperature_k=number('K', 250.0, 1000.0), phi=number('1', 1.0, 20.0), n=number('1', 0.5, 3.0)))
ANALYSIS_OUTPUT = obj(dict(activation_energy_j_mol=nullable(number('J/mol')), log10_a_per_s=nullable(number('1')), points_used=INT, r2=nullable(number('1')),
                           quality=FIT_QUALITY, delta_t_adiabatic_k=number('K'), phi_corrected_final_temperature_k=number('K'), evidence=TEXT))
LEDGER_KEYS = ['heater_energy_j', 'jacket_loss_j', 'fan_loss_j', 'sample_jacket_exchange_j', 'reaction_heat_j', 'stored_heat_j', 'energy_residual_j',
               'reactant_loaded_mol', 'reactant_consumed_mol', 'reactant_remaining_mol', 'reactant_residual_mol', 'gas_generated_mol', 'gas_vented_mol',
               'gas_in_container_mol', 'gas_residual_mol', 'pad_air_mol']
LEDGER = obj({k: number('J' if k.endswith('_j') else 'mol') for k in LEDGER_KEYS})

# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c) if isinstance(c, (list, tuple)) else [c], function_zh=meaning,
                   visibility=visibility, kind=kind, effects=[])


CONFIGURED = obj(dict(configured=BOOL))
cap('plant.configure', PLANT, CONFIGURED, PLANT_EXAMPLE, ['module_body', 'side_heater', 'sample_thermocouple', 'pressure_transducer'],
    '宿主声明实验室环境与量热腔工程参数：夹套热容/加热功率/散热/风扇、样品–夹套导热、PI参数、热电偶与压力传感器偏差/噪声、取压管死体积（须显式接受）', 'evaluator', 'configuration')
cap('method.configure', METHOD, CONFIGURED, METHOD_EXAMPLE, 'controller_board',
    '测试方法：HWS（起始/终止温度、台阶、加热速率、等待、搜索、放热阈值）、Iso-Fixed/Iso-Track等温老化、恒速升温；终止压力与速率、冷却目标、数据间隔', 'agent', 'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), 'power_switch', '开关整机电源；断电取消进行中的测试（已记录数据保留）')
cap('tray.move', obj(dict(extend=BOOL)), obj(dict(target_m=number('m'))), dict(extend=True), 'tray',
    '宿主（操作者/机器人代理）以有限速度拉出或推回模块托盘；测试中不得拉出', 'evaluator', 'robot_skill')
cap('lid.set', obj(dict(open=BOOL)), obj(dict(target_rad=number('rad'))), dict(open=True), 'lid', '宿主开/合ARC模块顶盖（有限速度）；测试中不得打开', 'evaluator', 'robot_skill')
cap('sample.load', LOAD, obj(dict(loaded=BOOL, reactant_mol=number('mol'), phi=number('1'), headspace_m3=number('m^3'))), LOAD_EXAMPLE,
    ['sample_container', 'bomb_stem', 'pressure_line', 'sample_thermocouple'],
    '宿主把装好样品的容器放入打开的量热腔并接取压管与热电偶：绑定模板动力学与显式先验（比热、分解热、产气量、液体密度）', 'evaluator', 'robot_skill')
cap('sample.unload', obj({}), obj(dict(unloaded=BOOL, vented_gas_mol=number('mol'))), {}, ['sample_container', 'pressure_line'],
    '宿主在冷却后（≤60°C）泄压并取出样品容器（产气与残留反应物随容器离开）', 'evaluator', 'robot_skill')
cap('test.start', obj({}), obj(dict(operation_id=TEXT)), {}, ['controller_board', 'side_heater', 'bottom_heater', 'top_heater', 'sample_thermocouple', 'pressure_transducer'],
    '开始测试：核对通电、方法、样品、顶盖关闭与托盘推到位；按方法执行HWS/等温/升温与绝热跟踪，结束后冷却')
cap('test.stop', obj({}), obj(dict(phase=PHASE, points=INT)), {}, 'controller_board', '停止（中止）测试：已记录数据保留，测试立即结束、加热器关闭，样品自然冷却（不开风扇、不执行cool_to_k）')
cap('test.result', obj({}), RESULT, {}, 'controller_board', '返回测试曲线（温度/压力/自热速率）、放热事件（起始、ΔT、φ、ΔT_ad、TMR、最大速率与压力、动力学）与有效性；隐去模板身份', 'agent', 'query')
cap('analysis.kinetics', ANALYSIS, ANALYSIS_OUTPUT, dict(temperatures_k=[390.0, 400.0, 410.0], self_heat_rates_k_min=[0.05, 0.14, 0.38], onset_temperature_k=383.36,
                                                         final_temperature_k=473.66, phi=1.48, n=1.0), 'controller_board',
    '纯函数：Townsend–Tou法由自热速率–温度数据求表观活化能与指前因子，并做φ修正', 'agent', 'analysis')
cap('ledger.read', obj({}), LEDGER, {}, 'controller_board', '受限：热量账（加热、散热、风扇、反应放热、储热）与物质账（反应物、产气、排放）', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(loaded_template=nullable(TEXT), operations=array(obj(dict(operation_id=TEXT, source=nullable(TEXT))), 0, MAX_PROVENANCE))), {},
    'controller_board', '受限：当前样品与最近1000次测试所用模板记录的来源', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), 'controller_board', '唯一宿主时间推进（纳秒整数时钟，余数累计；仪器1s周期在全局时间网格上执行）', 'evaluator', 'simulation')
UNSUPPORTED = {
    'module.scanning': '未选扫描模块（2.6mL，外加热器扫描）；本实例只装ARC模块',
    'module.coin_cell_high_temperature': '未选高温纽扣电池模块（RT–300°C、0.1mW）',
    'module.coin_cell_high_sensitivity': '未选高灵敏纽扣电池模块（RT–200°C、0.05mW）',
    'variphi.set': '未选VariPhi内加热器选件（φ补偿、恒功率/恒速扫描0–2K/min、放热/吸热检测）',
    'mode.fire_exposure': '火灾暴露模式需要VariPhi，未实现',
    'battery.cycler_connect': '无电池充放电仪联用（纽扣电池模块未选）',
    'sample.stir': '搅拌/排放/注入为ARC 305选项，MMC 315 ARC模块资料未列',
    'hardware.connect': '无真实USB/网络连接与Proteus软件通信',
    'robot.handle_container': '无机器人装样、接管、拧紧与取样验证（由宿主sample.load/unload代理）',
    'accuracy.certification': '无厂家校准、ASTM E1981标定或不确定度实验',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, 'controller_board', v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


slot('clock.time_s', number('s'), 's', 'derived', 'controller_board', '宿主时钟；read不推进')
slot('lab.ambient_k', nullable(number('K')), 'K', 'derived', 'module_body', '已声明的实验室室温（冷却目标须至少高于它5K）；未声明工程参数时为null')
slot('test.detection_limit_k_min', nullable(number('K/min')), 'K/min', 'derived', 'controller_board',
     '当前工程参数、所装样品与搜索时间下可用的最小放热阈值（热电偶失配漂移与噪声底的保守估计）；阈值低于它时test.start被拒绝；缺少参数/样品/方法时为null')
for k, c, m in [('plant.configured', 'module_body', '工程过程参数已声明'), ('method.configured', 'controller_board', '测试方法已设定'),
                ('power.enabled', 'power_switch', '整机通电'), ('tray.docked', 'tray', '托盘已推到位且静止（模块与对接塔连接）'),
                ('lid.closed', 'lid', 'ARC模块顶盖已关闭且静止'), ('lid.open', 'lid', 'ARC模块顶盖已全开且静止（装样/取样条件）'),
                ('sample.loaded', 'sample_container', '量热腔内有已接管的样品容器'), ('test.running', 'controller_board', '测试进行中（含冷却阶段）'),
                ('test.started_by_other_agent', 'controller_board', '当前/最近一次测试由另一个agent启动：本调用者不能停止、读取完整结果、改方法或断电（按调用者计算）')]:
    slot(k, BOOL, '1', 'derived', c, m)
slot('test.stage', STAGE, '1', 'derived', 'controller_board', '测试阶段：加热/等待/搜索/放热跟踪/等温/升温/冷却/完成')
slot('test.exotherm_count', INT, '1', 'derived', 'controller_board', '本次测试已检出的放热事件总数（含超过20条记录上限而未记录的）')
slot('test.step_target_k', nullable(number('K')), 'K', 'setpoint', 'controller_board',
     '当前程序温度：HWS台阶（加热/等待/搜索）、等温温度（Iso阶段及Iso-Fixed放热）或升温终点；绝热放热跟踪、冷却或无测试时为null')
slot('jacket.mode', JACKET_MODE, '1', 'derived', 'side_heater',
     '夹套控制方式：off/programme（台阶或升温程序）/isothermal_hold（保持程序温度）/adiabatic_tracking（跟踪样品，限50K/min）/cooling')
slot('sample.indicated_k', nullable(number('K')), 'K', 'sensor', 'sample_thermocouple', '样品热电偶温度（0.01K量化，含静态偏差与相关噪声，每1s采样）；断电为null')
slot('jacket.indicated_k', nullable(number('K')), 'K', 'sensor', 'side_heater', '夹套（加热器）热电偶温度（0.01K量化，含偏差与噪声）；断电为null')
slot('pressure.indicated_pa', nullable(number('Pa')), 'Pa', 'sensor', 'pressure_transducer', '样品容器压力（0.01bar量化，含偏差与噪声；检测上限150bar，超出时读数饱和）；断电或未装样为null',
     reference='绝对压力。')
slot('self_heat_rate.indicated_k_min', nullable(number('K/min')), 'K/min', 'derived', 'controller_board',
     '仪器由测得样品温度回归得到的当前自热/升温速率（最近2分钟）；断电或数据不足为null')
slot('tray.position_m', number('m', 0.0, TRAY_TRAVEL_M), 'm', 'scene', 'tray', '托盘拉出距离（0=推到位对接，0.25m=全拉出）', 'evaluator', '资产坐标−Y方向（朝操作者）平移。')
slot('lid.angle_rad', number('rad', 0.0, LID_OPEN_RAD), 'rad', 'scene', 'lid', '模块顶盖开角（0=关，1.90rad=全开）', 'evaluator', '绕后铰链、前缘向上。')
slot('power.angle_rad', number('rad'), 'rad', 'scene', 'power_switch', '后置电源摇臂开关姿态：0关、0.30rad开', 'evaluator')
for k, u, c, m in [('truth.sample_k', 'K', 'sample_container', '样品与容器真实温度'), ('truth.jacket_k', 'K', 'side_heater', '夹套真实温度'),
                   ('truth.pressure_pa', 'Pa', 'sample_container', '容器真实压力（绝对）'), ('truth.conversion', '1', 'sample_container', '模板反应物转化率'),
                   ('truth.reaction_power_w', 'W', 'sample_container', '反应放热功率'), ('truth.heater_power_w', 'W', 'side_heater', '夹套加热功率'),
                   ('truth.gas_generated_mol', 'mol', 'sample_container', '累计产气量')]:
    slot(k, number(u), u, 'process_truth', c, m, 'evaluator', '绝对压力。' if k == 'truth.pressure_pa' else None)


def townsend_tou(temps_k, rates_k_min, t_onset, t_final, n):
    """Apparent n-th order kinetics from adiabatic self-heat data: k* = (dT/dt) / dT_obs * (dT_obs / (T_f - T))^n ; ln k* vs 1/T."""
    dT = t_final - t_onset
    xs, ys = [], []
    for T, r in zip(temps_k, rates_k_min):
        if not (t_onset + 0.05 * dT <= T <= t_onset + 0.80 * dT) or r <= 0 or T >= t_final:
            continue
        k = r / 60.0 / dT * (dT / (t_final - T)) ** n
        xs.append(1.0 / T); ys.append(math.log(k))
    if len(xs) < 3 or dT <= 0:
        return dict(activation_energy_j_mol=None, log10_a_per_s=None, points_used=len(xs), r2=None)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return dict(activation_energy_j_mol=None, log10_a_per_s=None, points_used=len(xs), r2=None)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    icpt = my - slope * mx
    ss = sum((y - my) ** 2 for y in ys)
    res = sum((y - (icpt + slope * x)) ** 2 for x, y in zip(xs, ys))
    return dict(activation_energy_j_mol=-slope * GAS_CONSTANT, log10_a_per_s=icpt / math.log(10), points_used=len(xs), r2=(1 - res / ss) if ss > 0 else None)


def fit_quality(fit, adiabatic=True, complete=True):
    """Quality flag of a Townsend-Tou fit (engineering screens: >= 5 points, r2 >= 0.95, 40-400 kJ/mol); an event cut short by an end
    criterion, a stop or the ageing time is fitted against a truncated temperature rise and is flagged as incomplete."""
    if not adiabatic:
        return 'not_adiabatic'
    if not complete:
        return 'incomplete_exotherm'
    E = fit.get('activation_energy_j_mol')
    if E is None or fit.get('points_used', 0) < 5:
        return 'too_few_points'
    if fit.get('r2') is None or fit['r2'] < 0.95:
        return 'poor_fit'
    if not 40e3 <= E <= 400e3:
        return 'implausible_parameters'
    return 'ok'


def analyze_kinetics(temperatures_k, self_heat_rates_k_min, onset_temperature_k, final_temperature_k, phi, n):
    if len(temperatures_k) != len(self_heat_rates_k_min) or final_temperature_k <= onset_temperature_k:
        raise ActionRejected('INVALID_ARGUMENT')
    fit = townsend_tou(temperatures_k, self_heat_rates_k_min, onset_temperature_k, final_temperature_k, n)
    dT_ad = phi * (final_temperature_k - onset_temperature_k)
    return dict(fit, quality=fit_quality(fit), delta_t_adiabatic_k=dT_ad, phi_corrected_final_temperature_k=onset_temperature_k + dT_ad,
                evidence='Townsend-Tou n-th order analysis of the supplied self-heat data (alpha 0.05-0.80); dT_ad = phi dT_obs; no process truth used')


def search_regression_s(search_min):
    """Length of the search-window regression: the window minus its settling part (max(2 min, 1/3), at most half)."""
    window = search_min * 60.0
    return window - min(max(MIN_REGRESSION_S, window / 3.0), window / 2.0)


def end_window_s(search_min):
    return max(search_regression_s(search_min), END_WINDOW_MIN_S)


def slope_standard_error(tc_noise_k, G_sj, C_s, T):
    """Standard error (K/s) of a least-squares slope of the indicated sample temperature over T seconds at 1 Hz: correlated
    thermocouple noise (24 sigma^2 tau / T^3), 0.01 K quantisation (step^2 / T^3) and the noise that adiabatic tracking pushes into
    the sample through G_sj (G_sj/C_s * 2 sigma sqrt(tau/T), times a safety factor).  Checked against this runtime's own stationary
    adiabatic-tracking data (sigma 0.003-0.01 K, G_sj 0.1-2 W/K, 180-600 s windows): measured standard deviations of the slope agree
    with the analytic expression to a factor 0.8-1.15."""
    se_tc2 = 24.0 * tc_noise_k ** 2 * TC_NOISE_TAU_S / T ** 3
    se_q2 = TEMPERATURE_READABILITY_K ** 2 / T ** 3
    se_inj = INJECTION_FACTOR * G_sj / C_s * 2.0 * tc_noise_k * math.sqrt(TC_NOISE_TAU_S / T)
    return math.sqrt(se_tc2 + se_q2 + se_inj ** 2)


def detection_limit_k_min(plant, C_s, search_min):
    """Smallest usable exothermal threshold (K/min): no false detection (drift + 6 SE of the search slope) and a reachable event
    end (half the threshold >= drift + 4 SE of the end-window slope); drift = G_sj * thermocouple offset difference / C_s."""
    G = plant['sample_jacket_conductance_w_k']
    drift = G * abs(plant['sample_tc_offset_k'] - plant['jacket_tc_offset_k']) / C_s
    se_s = slope_standard_error(plant['tc_noise_k'], G, C_s, search_regression_s(search_min))
    se_e = slope_standard_error(plant['tc_noise_k'], G, C_s, end_window_s(search_min))
    return max(drift + DETECTION_SIGMAS * se_s, 2.0 * (drift + END_SIGMAS * se_e)) * 60.0


def slope(points):
    """Least-squares slope of (t, value) pairs."""
    n = len(points)
    if n < 3:
        return None
    mt = sum(p[0] for p in points) / n
    mv = sum(p[1] for p in points) / n
    st = sum((p[0] - mt) ** 2 for p in points)
    if st <= 0:
        return None
    return sum((p[0] - mt) * (p[1] - mv) for p in points) / st


def _phi2(x):
    """(e^x - 1 - x) / x^2, accurate for small |x|."""
    if abs(x) > 1e-3:
        return (math.expm1(x) - x) / (x * x)
    return 0.5 + x / 6.0 + x * x / 24.0 + x ** 3 / 120.0


def thermal_step(Cj, Cs, Gloss, Gsj, P, Q, Ta, Tj, Ts, dt):
    """Exact solution of Cj dTj/dt = P - Gloss (Tj - Ta) - Gsj (Tj - Ts), Cs dTs/dt = Q + Gsj (Tj - Ts) over dt (constant P, Q).
    Returns (Tj', Ts', int (Tj - Ta) dt, int (Tj - Ts) dt); increments use expm1 and the integrals the (e^x - 1 - x)/x^2 kernel so that
    millisecond sub-steps keep full relative accuracy."""
    a11, a12, a21, a22 = -(Gloss + Gsj) / Cj, Gsj / Cj, Gsj / Cs, -Gsj / Cs
    b1, b2 = P / Cj, Q / Cs
    det = a11 * a22 - a12 * a21                                   # = Gloss Gsj / (Cj Cs) > 0
    us = (-b1 * a22 + a12 * b2) / det                             # steady state of u = Tj - Ta, v = Ts - Ta
    vs = (-a11 * b2 + a21 * b1) / det
    tr = a11 + a22
    disc = math.sqrt(max(tr * tr / 4 - det, 0.0))                 # > 0: distinct real eigenvalues
    l1, l2 = tr / 2 + disc, tr / 2 - disc
    u0, v0 = Tj - Ta, Ts - Ta
    du, dv = u0 - us, v0 - vs
    e1, e2 = (a12, l1 - a11), (a12, l2 - a11)                     # eigenvectors
    D = e1[0] * e2[1] - e2[0] * e1[1]
    c1 = (du * e2[1] - e2[0] * dv) / D
    c2 = (e1[0] * dv - du * e1[1]) / D
    g1, g2 = math.expm1(l1 * dt), math.expm1(l2 * dt)
    u = u0 + c1 * e1[0] * g1 + c2 * e2[0] * g2
    v = v0 + c1 * e1[1] * g1 + c2 * e2[1] * g2
    h1, h2 = l1 * dt * dt * _phi2(l1 * dt), l2 * dt * dt * _phi2(l2 * dt)
    iu = u0 * dt + c1 * e1[0] * h1 + c2 * e2[0] * h2
    iv = v0 * dt + c1 * e1[1] * h1 + c2 * e2[1] * h2
    return Ta + u, Ta + v, iu, iu - iv


def nth_order_remaining(y, k, dt, n):
    """Exact y(dt) of dy/dt = -k y^n (constant k) written with log1p so that orders near 1 do not cancel; n < 1 may end at 0."""
    if y <= 0.0:
        return 0.0
    x = k * dt
    if n == 1.0:
        return y * math.exp(-x)
    yn = y ** (n - 1.0)
    z = (n - 1.0) * x * yn
    if z <= -1.0:
        return 0.0
    L = math.log1p(z) / z if abs(z) > 1e-12 else 1.0 - z / 2.0
    return y * math.exp(-x * yn * L)


class NetzschMmc315Runtime:
    """One MMC 315 Nexus with its ARC module; the host clock is the only time source (``step``)."""

    def __init__(self, instance_id='mmc315_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.method = None
        self.power = False
        self.tray = 0.0
        self.tray_move = None
        self.lid = 0.0
        self.lid_move = None
        self.T_j = 296.15
        self.T_s = 296.15
        self.integral_w = 0.0
        self.heater_w = 0.0                # held between control ticks
        self.q_rxn_w = 0.0
        self.sample = None
        self.rng = random.Random(0)
        self.noise = dict(s=0.0, j=0.0, p=0.0)
        self.rate_buf = []                 # (t_s, indicated sample K, indicated jacket K) sampled every control cycle
        self.test = None
        self.records = {}
        self.operations = {}
        self._principals = {}
        self._requests = {}
        self._owners = {}
        self.book = {k: 0.0 for k in ('heater_energy_j', 'jacket_loss_j', 'fan_loss_j', 'sample_jacket_exchange_j', 'reaction_heat_j', 'load_unload_heat_j',
                                      'reactant_loaded_mol', 'reactant_consumed_mol', 'reactant_unloaded_mol', 'gas_generated_mol', 'gas_vented_mol',
                                      'pad_air_mol')}
        self._comp = {k: 0.0 for k in self.book}

    # ------------------------------------------------------------ helpers
    @property
    def time_s(self):
        return self.time_ns / 1e9

    def register_principal(self, name, visibility):
        if not isinstance(name, str) or not name or visibility not in ('agent', 'evaluator', 'debug') or name in self._principals:
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if not isinstance(name, str) or name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return NetzschMmc315Session(self, name)

    def _add(self, key, value):
        s_ = self.book[key]
        t_ = s_ + value
        self._comp[key] += (s_ - t_) + value if abs(s_) >= abs(value) else (value - t_) + s_
        self.book[key] = t_

    def _ambient(self):
        return self.plant['ambient_k'] if self.plant else 296.15

    def _Cs(self):
        return self.sample['C_s'] if self.sample else 5.0            # empty cavity: thermocouple fixture and cavity air (engineering)

    def _docked(self):
        return self.tray == 0.0 and self.tray_move is None

    def _lid_closed(self):
        return self.lid == 0.0 and self.lid_move is None

    def _lid_open(self):
        return self.lid == LID_OPEN_RAD and self.lid_move is None

    def _test_active(self):
        return self.test is not None and self.test['phase'] == 'running'

    def _pressure_true(self, T=None):
        s = self.sample
        if s is None:
            return None
        T = self.T_s if T is None else T
        p_vap = solvent_vapour_pressure_pa(s['solvent'], T)
        p_full = s['n_solv'] * GAS_CONSTANT * T / s['V_g']
        p_solv = p_full if p_vap is None else min(p_vap, p_full)
        return (s['n_pad'] + s['n_gen']) * GAS_CONSTANT * T / s['V_g'] + p_solv

    def _meas_Ts(self):
        return self.T_s + self.plant['sample_tc_offset_k'] + self.noise['s']

    def _meas_Tj(self):
        return self.T_j + self.plant['jacket_tc_offset_k'] + self.noise['j']

    def _meas_p(self):
        p = self._pressure_true()
        return None if p is None else p + self.plant['pressure_offset_pa'] + self.noise['p']

    @staticmethod
    def _ind_T(T):
        """Indicated thermocouple temperature: 0.01 K readability, saturates at the reading range."""
        return quantize(min(T, TC_RANGE_MAX_K), TEMPERATURE_READABILITY_K)

    @staticmethod
    def _ind_p(p):
        """Indicated pressure: 0.01 bar readability, detection range up to the documented 150 bar."""
        return None if p is None else quantize(min(max(p, 0.0), PRESSURE_RANGE_PA), PRESSURE_READABILITY_PA)

    def _window(self, t0, t1=None):
        i = bisect.bisect_left(self.rate_buf, (t0 - 1e-9,))
        j = len(self.rate_buf) if t1 is None else bisect.bisect_right(self.rate_buf, (t1 + 1e-9, math.inf))
        return self.rate_buf[i:j]

    def _rate_k_s(self, window=RATE_WINDOW_S):
        pts = self._window(self.time_s - window)
        if len(pts) < 5 or pts[-1][0] - pts[0][0] < 0.5 * window:
            return None
        return slope(pts)

    def _excess_mean(self, window):
        """Mean indicated sample-minus-jacket temperature over the last window (None without samples)."""
        pts = self._window(self.time_s - window)
        return sum(q[1] - q[2] for q in pts) / len(pts) if pts else None

    def _search_slope(self, start_s, end_s):
        """Regression slope of the indicated sample temperature over a search window after discarding its settling part."""
        window = end_s - start_s
        settle = min(max(MIN_REGRESSION_S, window / 3.0), window / 2.0)
        return slope(self._window(start_s + settle, end_s))

    def _detection_limit(self):
        if self.plant is None or self.sample is None or self.method is None:
            return None
        return detection_limit_k_min(self.plant, self.sample['C_s'], self.method['search_min'])

    def _jacket_mode(self):
        t = self.test
        if not (self.power and self._test_active()):
            return 'off'
        st = t['stage']
        if st == 'cool':
            return 'cooling'
        if st in ('heat', 'ramp'):
            return 'programme'
        if st == 'wait':
            return 'isothermal_hold'
        if st == 'iso' or (st == 'exotherm' and t['cur'] is not None and t['cur']['conditions'] == 'isothermal_jacket'):
            if st == 'iso' and t['iso_tracking']:
                return 'adiabatic_tracking'
            return 'programme' if t['ramp_k'] < t['target_k'] - 1e-9 else 'isothermal_hold'
        return 'adiabatic_tracking'                                     # search / adiabatic exotherm

    def _programme_k(self):
        t = self.test
        if not self._test_active():
            return None
        st = t['stage']
        if st in ('heat', 'wait', 'search'):
            return t['step_k']
        if st in ('iso', 'ramp') or (st == 'exotherm' and t['cur'] is not None and t['cur']['conditions'] == 'isothermal_jacket'):
            return t['target_k']
        return None

    def read(self, visibility='agent'):
        on = self.power and self.plant is not None
        rate = self._rate_k_s() if on else None
        p = self._meas_p() if (on and self.sample) else None
        t = self.test
        d = {'clock.time_s': self.time_s, 'lab.ambient_k': self.plant['ambient_k'] if self.plant else None, 'test.detection_limit_k_min': self._detection_limit(),
             'plant.configured': self.plant is not None, 'method.configured': self.method is not None, 'power.enabled': self.power,
             'tray.docked': self._docked(), 'lid.closed': self._lid_closed(), 'lid.open': self._lid_open(), 'sample.loaded': self.sample is not None,
             'test.running': self._test_active(), 'test.started_by_other_agent': False,
             'test.stage': t['stage'] if self._test_active() else ('done' if t is not None else 'idle'),
             'test.exotherm_count': t['detected'] if t is not None else 0,
             'test.step_target_k': self._programme_k(), 'jacket.mode': self._jacket_mode(),
             'sample.indicated_k': self._ind_T(self._meas_Ts()) if on else None,
             'jacket.indicated_k': self._ind_T(self._meas_Tj()) if on else None,
             'pressure.indicated_pa': self._ind_p(p),
             'self_heat_rate.indicated_k_min': None if rate is None else quantize(rate * 60.0, 0.001),
             'tray.position_m': self.tray, 'lid.angle_rad': self.lid, 'power.angle_rad': POWER_SWITCH_ON_RAD if self.power else 0.0,
             'truth.sample_k': self.T_s, 'truth.jacket_k': self.T_j, 'truth.pressure_pa': self._pressure_true() or 0.0,
             'truth.conversion': self.sample['alpha'] if self.sample else 0.0, 'truth.reaction_power_w': self.q_rxn_w, 'truth.heater_power_w': self.heater_w,
             'truth.gas_generated_mol': self.sample['n_gen'] if self.sample else 0.0}
        return {k: copy.deepcopy(v) for k, v in d.items() if visibility != 'agent' or SLOTS[k]['visibility'] == 'agent'}

    def _stored_heat(self):
        Ta = self._ambient()
        return self.plant['jacket_heat_capacity_j_k'] * (self.T_j - Ta) + self._Cs() * (self.T_s - Ta) if self.plant else 0.0

    def ledger(self):
        b = {k: v + self._comp[k] for k, v in self.book.items()}
        stored = self._stored_heat()
        s = self.sample
        remaining = s['n_r0'] * (1.0 - s['alpha']) if s else 0.0
        in_container = s['n_gen'] if s else 0.0
        out = dict(heater_energy_j=b['heater_energy_j'], jacket_loss_j=b['jacket_loss_j'], fan_loss_j=b['fan_loss_j'], sample_jacket_exchange_j=b['sample_jacket_exchange_j'],
                   reaction_heat_j=b['reaction_heat_j'], stored_heat_j=stored,
                   energy_residual_j=b['heater_energy_j'] + b['reaction_heat_j'] + b['load_unload_heat_j'] - b['jacket_loss_j'] - b['fan_loss_j'] - stored,
                   reactant_loaded_mol=b['reactant_loaded_mol'], reactant_consumed_mol=b['reactant_consumed_mol'], reactant_remaining_mol=remaining,
                   reactant_residual_mol=b['reactant_loaded_mol'] - b['reactant_consumed_mol'] - b['reactant_unloaded_mol'] - remaining,
                   gas_generated_mol=b['gas_generated_mol'], gas_vented_mol=b['gas_vented_mol'], gas_in_container_mol=in_container,
                   gas_residual_mol=b['gas_generated_mol'] - b['gas_vented_mol'] - in_container, pad_air_mol=b['pad_air_mol'])
        return out

    # ------------------------------------------------------------ tests and records
    def _new_test(self):
        op = f'{self.instance_id}:test:{len(self.operations) + 1}'
        m = copy.deepcopy(self.method)
        mode = m['mode']
        t = dict(op=op, method=m, phase='running', stage=None, start_ns=self.time_ns, end_ns=None, points=[], exotherms=[], cur=None, target_k=None,
                 stage_end_s=None, search_start_s=None, ramp_k=None, step_k=None, next_point_s=self.time_s, validity='not_completed', terminal_reason=None,
                 max_T=None, max_p=None, source=self.sample['source'], template_id=self.sample['template']['id'], iso_end_s=None, iso_reached_s=None,
                 iso_tracking=False, track_sp=None, cool_start_s=None, detected=0, dropped=0, interval_s=m['data_interval_s'], phi=self.sample['phi'])
        self.test = t
        self.operations[op] = t
        if mode == 'hws':
            self._begin_heat(max(m['start_temperature_k'], self._meas_Ts()))
        elif mode in ('iso_fixed', 'iso_track'):
            t['stage'] = 'iso'; t['target_k'] = m['iso_temperature_k']; t['iso_end_s'] = self.time_s + m['iso_hours'] * 3600.0
            t['ramp_k'] = self._meas_Tj()
        else:
            t['stage'] = 'ramp'; t['target_k'] = m['end_temperature_k']; t['ramp_k'] = self._meas_Tj()
        self.integral_w = 0.0
        self.records[op] = self._result_of(t)
        return op

    def _begin_heat(self, step_target):
        t = self.test
        t.update(stage='heat', step_k=min(step_target, t['method']['end_temperature_k']), ramp_k=self._meas_Tj(), stage_end_s=None, search_start_s=None, track_sp=None)
        t['target_k'] = t['step_k']

    def _result_of(self, t):
        return dict(operation_id=t['op'], phase=t['phase'], stage=t['stage'] if t['phase'] == 'running' else 'done', method=t['method']['mode'],
                    elapsed_s=((t['end_ns'] if t['end_ns'] is not None else self.time_ns) - t['start_ns']) / 1e9, points=copy.deepcopy(t['points']),
                    exotherms=[self._exotherm_public(e) for e in t['exotherms']], exotherm_count=t['detected'], exotherms_dropped=t['dropped'],
                    data_interval_s=t['interval_s'], max_temperature_k=t['max_T'], max_pressure_pa=t['max_p'], phi=t['phi'],
                    validity=t['validity'], terminal_reason=t['terminal_reason'], template_source=t['source'],
                    evidence='synthetic_forward_model; HWS/iso/ramp logic, adiabatic tracking and Townsend-Tou evaluation of quantised noisy thermocouple/pressure '
                             'observations sampled every 1 s; kinetics from the selected manufacturer half-life template with explicit host priors; not native Proteus data')

    @staticmethod
    def _exotherm_public(e):
        return {k: copy.deepcopy(v) for k, v in e.items() if not k.startswith('_')}

    def _finish(self, phase, reason, validity=None):
        t = self.test
        if t is None or t['phase'] != 'running':
            return
        if validity is None:
            validity = 'cancelled' if phase == 'cancelled' else ('valid' if t['detected'] else 'no_exotherm_detected')
        if t['cur'] is not None:
            self._close_exotherm(complete=False)
        t.update(phase=phase, terminal_reason=reason, validity=validity, end_ns=self.time_ns, stage='done', target_k=None)
        self.heater_w = 0.0
        self.integral_w = 0.0
        self.records[t['op']] = self._result_of(t)

    def _result(self):
        t = self.test
        if t is None:
            return dict(operation_id=None, phase='idle', stage='idle', method=self.method['mode'] if self.method else 'none', elapsed_s=0.0, points=[], exotherms=[],
                        exotherm_count=0, exotherms_dropped=0, data_interval_s=self.method['data_interval_s'] if self.method else 0.0,
                        max_temperature_k=None, max_pressure_pa=None, phi=self.sample['phi'] if self.sample else None, validity='not_completed', terminal_reason=None,
                        template_source=None, evidence='no_measurement')
        return self._result_of(t) if t['phase'] == 'running' else copy.deepcopy(self.records[t['op']])

    # ------------------------------------------------------------ exotherm bookkeeping
    def _open_exotherm(self, T_meas, p_meas, conditions):
        t = self.test
        t['stage'] = 'exotherm'
        t['stage_end_s'] = None
        t['detected'] += 1
        Tq, pq = self._ind_T(T_meas), self._ind_p(p_meas)
        t['cur'] = dict(index=t['detected'] - 1, conditions=conditions, onset_time_s=quantize(self.time_s - t['start_ns'] / 1e9, 0.001), onset_temperature_k=Tq,
                        max_temperature_k=Tq, max_excess_over_jacket_k=quantize(Tq - self._ind_T(self._meas_Tj()), TEMPERATURE_READABILITY_K), max_self_heat_rate_k_min=0.0, temperature_at_max_rate_k=Tq, _t_max_rate=self.time_s, _t_onset=self.time_s,
                        pressure_before_pa=pq if pq is not None else 0.0, max_pressure_rate_pa_min=0.0, _samples=[], _low_since=None, _p_prev=(self.time_s, pq),
                        tracking_rate_exceeded=False)

    def _close_exotherm(self, complete):
        t = self.test
        e = t['cur']
        t['cur'] = None
        s = self.sample
        adiabatic = e['conditions'] == 'adiabatic'
        T_end = self._ind_T(self._meas_Ts())
        dT = T_end - e['onset_temperature_k'] if (adiabatic and complete) else max(e['max_temperature_k'], T_end) - e['onset_temperature_k']
        dT = max(dT, 0.0)
        phi = s['phi']
        temps, rates = [], []
        smp = e['_samples']
        if len(smp) >= 9:                                             # local regression rates on ~300 points of the 1 s record
            stride = max(1, len(smp) // 300)
            half = max(15, stride)
            for i in range(half, len(smp) - half, stride):
                win = smp[i - half:i + half + 1]
                r = slope(win)
                if r is not None:
                    temps.append(sum(q[1] for q in win) / len(win)); rates.append(r * 60.0)
        if adiabatic and dT > 0:
            fit = townsend_tou(temps, rates, e['onset_temperature_k'], e['onset_temperature_k'] + dT, s['n'])
        else:
            fit = dict(activation_energy_j_mol=None, log10_a_per_s=None, points_used=0, r2=None)
        p_after = self._ind_p(self._meas_p())
        heat = s['C_s'] * dT if adiabatic else None
        e.update(end_temperature_k=T_end, delta_t_observed_k=dT, phi=phi, delta_t_adiabatic_k=phi * dT if adiabatic else None,
                 final_temperature_adiabatic_k=e['onset_temperature_k'] + phi * dT if adiabatic else None,
                 time_to_max_rate_s=max(0.0, e['_t_max_rate'] - e['_t_onset']), pressure_after_pa=p_after if p_after is not None else 0.0, heat_released_j=heat,
                 specific_heat_of_reaction_j_kg_sample=None if heat is None else heat / s['m_s'],
                 heat_of_reaction_j_kg_reactant=(heat / s['m_r']) if (heat is not None and s['m_r'] > 0) else None,
                 complete=complete, kinetics=dict(model='Townsend-Tou n-th order, alpha 0.05-0.80', n=s['n'], quality=fit_quality(fit, adiabatic, complete), **fit))
        if len(t['exotherms']) < MAX_EXOTHERMS:
            t['exotherms'].append(e)
        else:
            t['dropped'] += 1

    def _record_point(self, force=False):
        t = self.test
        if not force and self.time_s + 1e-9 < t['next_point_s']:
            return
        if len(t['points']) >= MAX_POINTS:                            # data reduction: keep every second point and double the interval
            t['points'] = t['points'][::2]
            t['interval_s'] *= 2.0
        rate = self._rate_k_s()
        t['points'].append(dict(time_s=quantize(self.time_s - t['start_ns'] / 1e9, 0.001), stage=t['stage'], sample_k=self._ind_T(self._meas_Ts()),
                                jacket_k=self._ind_T(self._meas_Tj()), pressure_pa=self._ind_p(self._meas_p()),
                                self_heat_rate_k_min=None if rate is None else quantize(rate * 60.0, 0.001)))
        t['next_point_s'] = self.time_s + t['interval_s']

    # ------------------------------------------------------------ commands
    def invoke(self, capability_id, **arguments):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id]['input_schema'], arguments)
        k = capability_id
        a = copy.deepcopy(arguments)
        if k in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        if k == 'test.result':
            return self._result()
        if k == 'ledger.read':
            return self.ledger()
        if k == 'provenance.read':
            return dict(loaded_template=self.sample['source'] if self.sample else None,
                        operations=[dict(operation_id=op, source=t['source']) for op, t in list(self.operations.items())[-MAX_PROVENANCE:]])
        if k == 'analysis.kinetics':
            return analyze_kinetics(**a)
        if k == 'sim.step':
            self.step(a['dt_s'])
            return dict(time_s=self.time_s)
        if k == 'plant.configure':
            if self.plant is not None:
                raise ActionRejected('PLANT_ALREADY_CONFIGURED')
            if not a['accept_engineering']:
                raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            net = (a['jacket_heater_max_w'] - (a['jacket_loss_w_k'] + a['sample_jacket_conductance_w_k']) * (MAX_TEMPERATURE_K - a['ambient_k'])) / a['jacket_heat_capacity_j_k']
            if net < TRACKING_RATE_K_S:
                raise ActionRejected('TRACKING_RATE_NOT_REACHABLE')      # the documented 50 K/min must be reachable up to 500 degC
            if a['controller_gain_w_k'] * CONTROL_PERIOD_S / a['jacket_heat_capacity_j_k'] > CONTROLLER_MAX_LOOP_GAIN:
                raise ActionRejected('CONTROLLER_UNSTABLE')              # the 1 s digital PI must stay well inside its stability region
            if abs(a['sample_tc_offset_k'] - a['jacket_tc_offset_k']) > TC_MISMATCH_MAX_K:
                raise ActionRejected('THERMOCOUPLE_MISMATCH')            # uncalibrated pair: static excess would reach half the 0.10 K criterion
            self.plant = a
            self.rng = random.Random(a['noise_seed'])
            self.T_j = self.T_s = a['ambient_k']
            out = dict(configured=True)
        elif k == 'method.configure':
            if self._test_active():
                raise ActionRejected('BUSY')
            if a['mode'] == 'hws' and a['end_temperature_k'] <= a['start_temperature_k']:
                raise ActionRejected('INVALID_METHOD')
            if a['mode'] in ('iso_fixed', 'iso_track') and (a['iso_temperature_k'] is None or a['iso_hours'] is None):
                raise ActionRejected('INVALID_METHOD')
            if a['mode'] in ('iso_fixed', 'iso_track') and a['iso_temperature_k'] >= a['end_temperature_k']:
                raise ActionRejected('INVALID_METHOD')
            if self.plant is not None and a['cool_to_k'] < self.plant['ambient_k'] + COOL_MIN_ABOVE_AMBIENT_K:
                raise ActionRejected('COOL_TARGET_BELOW_AMBIENT')
            self.method = a
            out = dict(configured=True)
        elif k == 'power.set':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if not a['enabled'] and self.power:
                if self._test_active():
                    self._finish('cancelled', 'POWER_OFF', 'cancelled')
                self.heater_w = 0.0
                self.integral_w = 0.0
                self.rate_buf = []
            if a['enabled'] and not self.power:
                p = self.plant
                self.noise = dict(s=p['tc_noise_k'] * self.rng.gauss(0, 1), j=p['tc_noise_k'] * self.rng.gauss(0, 1), p=p['pressure_noise_pa'] * self.rng.gauss(0, 1))
            self.power = a['enabled']
            out = dict(enabled=self.power)
        elif k == 'tray.move':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if self._test_active():
                raise ActionRejected('TEST_ACTIVE')
            target = TRAY_TRAVEL_M if a['extend'] else 0.0
            self.tray_move = None if self.tray == target else dict(start_ns=self.time_ns, x0=self.tray, target=target)
            out = dict(target_m=target)
        elif k == 'lid.set':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if self._test_active():
                raise ActionRejected('TEST_ACTIVE')
            target = LID_OPEN_RAD if a['open'] else 0.0
            self.lid_move = None if self.lid == target else dict(start_ns=self.time_ns, x0=self.lid, target=target)
            out = dict(target_rad=target)
        elif k == 'sample.load':
            out = self._load(a)
        elif k == 'sample.unload':
            out = self._unload()
        elif k == 'test.start':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if not self.power:
                raise ActionRejected('POWER_OFF')
            if self.method is None:
                raise ActionRejected('METHOD_REQUIRED')
            if self.method['cool_to_k'] < self.plant['ambient_k'] + COOL_MIN_ABOVE_AMBIENT_K:
                raise ActionRejected('COOL_TARGET_BELOW_AMBIENT')
            if self.sample is None:
                raise ActionRejected('NO_SAMPLE')
            if not self._lid_closed():
                raise ActionRejected('LID_OPEN')
            if not self._docked():
                raise ActionRejected('TRAY_NOT_DOCKED')
            if self._test_active():
                raise ActionRejected('BUSY')
            if self._ind_T(self._meas_Ts()) >= self.method['end_temperature_k']:
                raise ActionRejected('SAMPLE_ABOVE_END_TEMPERATURE')
            if self.method['threshold_k_min'] < self._detection_limit():
                raise ActionRejected('THRESHOLD_BELOW_DETECTION_LIMIT')  # drift and noise floor of this plant, sample and search time
            out = dict(operation_id=self._new_test())
        elif k == 'test.stop':
            if not self._test_active():
                raise ActionRejected('NOT_RUNNING')
            self._abort_test()
            t = self.test
            out = dict(phase=t['phase'], points=len(t['points']))
        else:  # pragma: no cover
            raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision += 1
        return copy.deepcopy(out)

    def _abort_test(self):
        """Stop / cancellation: always an abort (recorded data kept); heaters off, natural cooling."""
        self._finish('cancelled', 'USER_CANCEL', 'cancelled')

    def _load(self, a):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')
        if self.sample is not None:
            raise ActionRejected('ALREADY_LOADED')
        if not self._lid_open():
            raise ActionRejected('LID_NOT_OPEN')
        if not a['accept_priors']:
            raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
        tpl = templates().get(a['template_id'])
        if tpl is None:
            raise ActionRejected('UNKNOWN_TEMPLATE')
        dH = a['heat_of_decomposition_j_kg'] if a['heat_of_decomposition_j_kg'] is not None else (
            tpl['heat_of_decomposition_J_per_g'] * 1e3 if tpl['heat_of_decomposition_J_per_g'] is not None else None)
        if a['reactant_mass_fraction'] > 0 and dH is None:
            raise ActionRejected('HEAT_OF_DECOMPOSITION_REQUIRED')
        if a['sample_cp_j_kg_k'] is None:
            raise ActionRejected('SAMPLE_HEAT_CAPACITY_REQUIRED')
        if a['reactant_mass_fraction'] > 0 and a['gas_yield_mol_per_mol'] is None:
            raise ActionRejected('GAS_YIELD_REQUIRED')
        p = self.plant
        V_liq = a['sample_mass_kg'] / a['liquid_density_kg_m3']
        if V_liq > 0.9 * a['container_volume_m3']:
            raise ActionRejected('CONTAINER_OVERFILLED')
        m_s, w = a['sample_mass_kg'], a['reactant_mass_fraction']
        C_s = m_s * a['sample_cp_j_kg_k'] + a['container_mass_kg'] * a['container_cp_j_kg_k']
        if m_s * w * (dH or 0.0) / C_s > MAX_ADIABATIC_RISE_K:
            raise ActionRejected('ADIABATIC_RISE_OUT_OF_MODEL_RANGE')
        M_r = tpl['molecular_weight_g_mol'] / 1000.0
        V_g = a['container_volume_m3'] + p['line_dead_volume_m3'] - V_liq
        Ta = self._ambient()
        n_solv = m_s * (1 - w) / (library()['solvents']['toluene']['molar_mass_g_mol'] / 1000.0) if a['solvent'] == 'toluene' else 0.0
        p_vap0 = min(solvent_vapour_pressure_pa(a['solvent'], Ta), n_solv * GAS_CONSTANT * Ta / V_g)
        n_pad = max(p['ambient_pressure_pa'] - p_vap0, 0.0) * V_g / (GAS_CONSTANT * Ta)     # sealed in room air already saturated with solvent vapour
        self.sample = dict(template=dict(id=tpl['id'], A=tpl['kinetics']['A_per_s'], Ea=tpl['kinetics']['Ea_J_mol']), n=a['reaction_order'], m_s=m_s, w=w, m_r=m_s * w,
                           M_r=M_r, n_r0=m_s * w / M_r, dH=dH or 0.0, nu=a['gas_yield_mol_per_mol'] or 0.0, cp_s=a['sample_cp_j_kg_k'], m_v=a['container_mass_kg'],
                           cp_v=a['container_cp_j_kg_k'], C_s=C_s, phi=C_s / (m_s * a['sample_cp_j_kg_k']), V_b=a['container_volume_m3'], V_liq=V_liq, V_g=V_g,
                           solvent=a['solvent'], n_solv=n_solv, n_pad=n_pad, alpha=0.0, n_gen=0.0,
                           material=a['container_material'], source=f"{LIBRARY_PATH.relative_to(ROOT)}#{tpl['id']}",
                           dH_evidence='host_override' if a['heat_of_decomposition_j_kg'] is not None else 'template_derived')
        before = 5.0 * (self.T_s - Ta)
        self.T_s = Ta
        self._add('load_unload_heat_j', 0.0 - before)
        self._add('reactant_loaded_mol', self.sample['n_r0'])
        self._add('pad_air_mol', self.sample['n_pad'])
        self.rate_buf = []
        s = self.sample
        return dict(loaded=True, reactant_mol=s['n_r0'], phi=s['phi'], headspace_m3=s['V_g'])

    def _unload(self):
        s = self.sample
        if s is None:
            raise ActionRejected('NO_SAMPLE')
        if self._test_active():
            raise ActionRejected('TEST_ACTIVE')
        if not self._lid_open():
            raise ActionRejected('LID_NOT_OPEN')
        if self.T_s > UNLOAD_MAX_K:
            raise ActionRejected('SAMPLE_TOO_HOT')
        Ta = self._ambient()
        self._add('gas_vented_mol', s['n_gen'])
        self._add('reactant_unloaded_mol', s['n_r0'] * (1.0 - s['alpha']))
        self._add('load_unload_heat_j', (5.0 - s['C_s']) * (self.T_s - Ta))
        vented = s['n_gen']
        self.sample = None
        self.rate_buf = []
        return dict(unloaded=True, vented_gas_mol=vented)

    # ------------------------------------------------------------ time integration
    def _reaction_dt_limit(self):
        s = self.sample
        if s is None or s['w'] == 0 or s['alpha'] >= 1.0:
            return MAX_SUBSTEP_S
        k = s['template']['A'] * math.exp(-s['template']['Ea'] / (GAS_CONSTANT * self.T_s))
        ra = k * (1.0 - s['alpha']) ** s['n']
        if ra <= 0:
            return MAX_SUBSTEP_S
        dT_rate = s['m_r'] * s['dH'] * ra / s['C_s']
        return max(1e-3, min(MAX_SUBSTEP_S, 0.002 / ra, 0.05 / dT_rate if dT_rate > 0 else MAX_SUBSTEP_S))

    def _next_events(self, end):
        tick = (self.time_ns // CONTROL_NS + 1) * CONTROL_NS
        ev = [end, tick, self.time_ns + max(1, round(self._reaction_dt_limit() * 1e9))]
        p = self.plant
        for mv, speed in ((self.tray_move, p['tray_speed_m_s']), (self.lid_move, p['lid_speed_rad_s'])):
            if mv is not None:
                ev.append(mv['start_ns'] + max(1, math.ceil(abs(mv['target'] - mv['x0']) / speed * 1e9)))
        return min(x for x in ev if x > self.time_ns)

    def _advance_mechanics(self, nxt):
        p = self.plant
        for attr, speed in (('tray', p['tray_speed_m_s']), ('lid', p['lid_speed_rad_s'])):
            mv = getattr(self, attr + '_move')
            if mv is None:
                continue
            dist = abs(mv['target'] - mv['x0'])
            s = (nxt - mv['start_ns']) / 1e9 * speed
            if s >= dist - 1e-15:
                setattr(self, attr, mv['target']); setattr(self, attr + '_move', None)
            else:
                setattr(self, attr, mv['x0'] + math.copysign(s, mv['target'] - mv['x0']))

    def _controller(self, dt):
        """Heater power for the next control cycle from the measured temperatures (feed-forward + PI, 0..P_max); adiabatic tracking
        follows the measured sample temperature with the documented 50 K/min tracking-rate limit."""
        p = self.plant
        t = self.test
        mode = self._jacket_mode()
        if mode in ('off', 'cooling'):
            self.integral_w = 0.0
            return 0.0
        m = t['method']
        Tj_m, Ts_m = self._meas_Tj(), self._meas_Ts()
        rate = m['heat_rate_k_min'] / 60.0
        ff = 0.0
        if mode == 'programme':
            t['ramp_k'] = min(t['target_k'], t['ramp_k'] + rate * dt)
            target = t['ramp_k']
            ff = rate if t['ramp_k'] < t['target_k'] else 0.0
        elif mode == 'isothermal_hold':
            target = t['target_k']
        else:
            sp = t['track_sp'] if t['track_sp'] is not None else Tj_m
            sp = Ts_m if Ts_m <= sp else min(Ts_m, sp + TRACKING_RATE_K_S * dt)
            t['track_sp'] = sp
            target = sp
            r = self._rate_k_s(30.0)
            ff = min(max(r or 0.0, -TRACKING_RATE_K_S), TRACKING_RATE_K_S)      # symmetric: noise is not rectified into heating
        target = min(target, MAX_TEMPERATURE_K)
        e = target - Tj_m
        u = (p['jacket_heat_capacity_j_k'] * ff + p['jacket_loss_w_k'] * (Tj_m - self._ambient()) + p['sample_jacket_conductance_w_k'] * (Tj_m - Ts_m)
             + p['controller_gain_w_k'] * e + self.integral_w)
        P = min(max(u, 0.0), p['jacket_heater_max_w'])
        if 0.0 < u < p['jacket_heater_max_w'] or (u >= p['jacket_heater_max_w'] and e < 0) or (u <= 0.0 and e > 0):
            self.integral_w += p['controller_gain_w_k'] / p['controller_integral_s'] * e * dt
            self.integral_w = min(max(self.integral_w, -p['jacket_heater_max_w']), p['jacket_heater_max_w'])
        return P

    def _integrate(self, dt):
        p = self.plant
        P = self.heater_w if self.power else 0.0
        s = self.sample
        Q = 0.0
        if s is not None and s['w'] > 0 and s['alpha'] < 1.0:
            k = s['template']['A'] * math.exp(-s['template']['Ea'] / (GAS_CONSTANT * self.T_s))
            y = 1.0 - s['alpha']
            y1 = nth_order_remaining(y, k, dt, s['n'])
            da = y - y1
            s['alpha'] = 1.0 - y1
            heat = s['m_r'] * s['dH'] * da
            gas = s['nu'] * s['n_r0'] * da
            s['n_gen'] += gas
            Q = heat / dt
            self._add('reaction_heat_j', heat)
            self._add('reactant_consumed_mol', s['n_r0'] * da)
            self._add('gas_generated_mol', gas)
        self.q_rxn_w = Q
        cooling = self._test_active() and self.test['stage'] == 'cool'
        G_fan = p['cooling_fan_w_k'] if (cooling and self.power) else 0.0
        G = p['jacket_loss_w_k'] + G_fan
        Ta = self._ambient()
        Tj, Ts, iu, ix = thermal_step(p['jacket_heat_capacity_j_k'], self._Cs(), G, p['sample_jacket_conductance_w_k'], P, Q, Ta, self.T_j, self.T_s, dt)
        self.T_j, self.T_s = Tj, Ts
        self._add('heater_energy_j', P * dt)
        self._add('jacket_loss_j', p['jacket_loss_w_k'] * iu)
        self._add('fan_loss_j', G_fan * iu)
        self._add('sample_jacket_exchange_j', p['sample_jacket_conductance_w_k'] * ix)

    def _tick(self):
        """One 1 s instrument cycle: sensor noise, sampling, test logic, then the heater power for the next cycle."""
        p = self.plant
        for key, sigma, tau in (('s', p['tc_noise_k'], TC_NOISE_TAU_S), ('j', p['tc_noise_k'], TC_NOISE_TAU_S), ('p', p['pressure_noise_pa'], PRESSURE_NOISE_TAU_S)):
            e_ = math.exp(-CONTROL_PERIOD_S / tau)
            self.noise[key] = self.noise[key] * e_ + sigma * math.sqrt(1.0 - e_ * e_) * self.rng.gauss(0, 1)
        now = self.time_s
        self.rate_buf.append((now, self._ind_T(self._meas_Ts()), self._ind_T(self._meas_Tj())))
        keep = max(BUF_KEEP_S, (end_window_s(self.test['method']['search_min']) + 60.0) if self._test_active() else 0.0)
        t = self.test
        if self._test_active() and t['search_start_s'] is not None and t['stage'] in ('search', 'iso'):
            keep = max(keep, now - t['search_start_s'] + 60.0)
        cut = bisect.bisect_left(self.rate_buf, (now - keep,))
        if cut:
            del self.rate_buf[:cut]
        if self._test_active():
            self._stage_logic()
        self.heater_w = self._controller(CONTROL_PERIOD_S)

    def _to_cool(self, reason, validity):
        t = self.test
        if t['cur'] is not None:
            self._close_exotherm(complete=False)
        t.update(stage='cool', target_k=None, stage_end_s=None, track_sp=None, cool_start_s=self.time_s, end_reason=reason, end_validity=validity)
        self.integral_w = 0.0

    def _after_exotherm(self, T_m):
        t = self.test
        m = t['method']
        now = self.time_s
        if m['mode'] == 'hws':
            if T_m + m['step_k'] <= m['end_temperature_k']:
                self._begin_heat(T_m + m['step_k'])
            else:
                self._to_cool('HWS_END_TEMPERATURE_REACHED', None)
        elif m['mode'] == 'ramp':
            if T_m < m['end_temperature_k'] - 1.0:
                t.update(stage='ramp', target_k=m['end_temperature_k'], ramp_k=self._meas_Tj(), track_sp=None)
            else:
                self._to_cool('RAMP_COMPLETE', None)
        elif now < t['iso_end_s'] - 1e-9:                              # isothermal ageing continues for the programmed time
            t.update(stage='iso', search_start_s=None, stage_end_s=None)
            if m['mode'] == 'iso_track':                               # adiabatic storage from here, detection windows start at once
                t.update(iso_tracking=True, iso_reached_s=now - m['wait_min'] * 60.0)
            else:                                                      # held jacket: let the sample settle back before the next window
                t['iso_reached_s'] = None
        else:
            self._to_cool('ISO_TIME_ELAPSED', None)

    def _stage_logic(self):
        t = self.test
        m = t['method']
        now = self.time_s
        thr = m['threshold_k_min']
        T_m = self._meas_Ts()
        Tq = self._ind_T(T_m)
        excess = quantize(Tq - self._ind_T(self._meas_Tj()), TEMPERATURE_READABILITY_K)
        excess30 = self._excess_mean(30.0)                              # detection uses the 30 s mean, not a single reading
        p_m = self._meas_p()
        pq = self._ind_p(p_m)
        t['max_T'] = Tq if t['max_T'] is None else max(t['max_T'], Tq)
        if pq is not None:
            t['max_p'] = pq if t['max_p'] is None else max(t['max_p'], pq)
        p_true = self._pressure_true()
        if p_true is not None and p_true > MAX_PRESSURE_PA:
            self._to_cool('PRESSURE_LIMIT_EXCEEDED', 'pressure_limit_exceeded')
            self._finish('failed', 'PRESSURE_LIMIT_EXCEEDED', 'pressure_limit_exceeded')
            return
        if T_m > OVERTEMPERATURE_K:
            self._to_cool('TEMPERATURE_LIMIT_EXCEEDED', 'temperature_limit_exceeded')
            self._finish('failed', 'TEMPERATURE_LIMIT_EXCEEDED', 'temperature_limit_exceeded')
            return
        stage = t['stage']
        rate = self._rate_k_s()
        if stage != 'cool':
            iso_settled = t['iso_reached_s'] is not None and now - t['iso_reached_s'] >= m['wait_min'] * 60.0
            programmed = (stage in ('heat', 'wait', 'ramp') or (stage == 'iso' and not iso_settled and not t['iso_tracking'])
                          or (stage == 'exotherm' and self._jacket_mode() == 'programme'))
            self_rate = None if rate is None else rate - (m['heat_rate_k_min'] / 60.0 if programmed else 0.0)   # self-heating, not the programme
            if pq is not None and pq >= m['max_pressure_pa']:
                self._to_cool('END_PRESSURE_REACHED', 'end_pressure_reached')
            elif self_rate is not None and self_rate * 60.0 >= m['max_rate_k_min']:
                self._to_cool('END_RATE_REACHED', 'end_rate_reached')
            elif Tq >= m['end_temperature_k']:
                self._to_cool('END_TEMPERATURE_REACHED', None)
        stage = t['stage']
        # a sample that overtakes the programmed or held jacket while still heating self-heats faster than the programme
        overtake = excess30 is not None and excess30 > EXCESS_DETECT_K and rate is not None and rate * 60.0 > thr
        if stage == 'heat':
            if overtake:
                self._open_exotherm(T_m, p_m, 'adiabatic')
            elif t['ramp_k'] >= t['step_k'] - 1e-9 and self._meas_Tj() >= t['step_k'] - 0.5:
                t['stage'] = 'wait'; t['stage_end_s'] = now + m['wait_min'] * 60.0
        elif stage == 'wait':
            if overtake:
                self._open_exotherm(T_m, p_m, 'adiabatic')
            elif now >= t['stage_end_s'] - 1e-9:
                t.update(stage='search', search_start_s=now, stage_end_s=now + m['search_min'] * 60.0, track_sp=None)
        elif stage == 'search':
            if now >= t['stage_end_s'] - 1e-9:
                r = self._search_slope(t['search_start_s'], now)
                t['stage_end_s'] = None
                if r is not None and r * 60.0 > thr:
                    self._open_exotherm(T_m, p_m, 'adiabatic')
                elif t['step_k'] + m['step_k'] <= m['end_temperature_k'] + 1e-9:
                    self._begin_heat(t['step_k'] + m['step_k'])
                else:
                    self._to_cool('HWS_END_TEMPERATURE_REACHED', None)
        elif stage == 'exotherm':
            e = t['cur']
            e['_samples'].append((now, Tq))
            if len(e['_samples']) > 40000:
                e['_samples'] = e['_samples'][::2]
            e['max_temperature_k'] = max(e['max_temperature_k'], Tq)
            e['max_excess_over_jacket_k'] = max(e['max_excess_over_jacket_k'], excess)
            pts = self._window(now - 30.0)
            r = slope(pts) if len(pts) >= 5 and pts[-1][0] - pts[0][0] >= 15.0 else None
            if r is not None and r * 60.0 > e['max_self_heat_rate_k_min']:
                e['max_self_heat_rate_k_min'] = r * 60.0                  # attributed to the centre of its 30 s regression window
                e['temperature_at_max_rate_k'] = quantize(sum(q[1] for q in pts) / len(pts), TEMPERATURE_READABILITY_K)
                e['_t_max_rate'] = sum(q[0] for q in pts) / len(pts)
            if r is not None and r > 0.98 * TRACKING_RATE_K_S:
                e['tracking_rate_exceeded'] = True
            tp, pp = e['_p_prev']
            if pq is not None and pp is not None and now - tp >= 10.0:
                e['max_pressure_rate_pa_min'] = max(e['max_pressure_rate_pa_min'], (pq - pp) / (now - tp) * 60.0)
                e['_p_prev'] = (now, pq)
            elif pp is None and pq is not None:
                e['_p_prev'] = (now, pq)
            adiabatic = e['conditions'] == 'adiabatic'
            end_window = end_window_s(m['search_min'])
            if m['mode'] in ('iso_fixed', 'iso_track') and now >= t['iso_end_s'] - 1e-9 and not adiabatic:
                self._close_exotherm(complete=False)                     # ageing time over; adiabatic events always end by the normal rule
                self._to_cool('ISO_TIME_ELAPSED', None)
                stage = None
            if stage == 'exotherm' and adiabatic:
                if now - e['_t_onset'] >= RATE_WINDOW_S + end_window:
                    r_end = slope(self._window(now - end_window))       # finished: end-window slope below half the threshold (hysteresis)
                    if r_end is not None and r_end * 60.0 < 0.5 * thr:
                        self._close_exotherm(complete=True)
                        self._after_exotherm(Tq)
            elif stage == 'exotherm':
                excess60 = self._excess_mean(60.0)
                if excess60 is not None and excess60 < 0.5 * EXCESS_DETECT_K and now - e['_t_onset'] > RATE_WINDOW_S:   # hysteresis 0.10 on / 0.05 off
                    e['_low_since'] = now if e['_low_since'] is None else e['_low_since']
                    if now - e['_low_since'] >= end_window:
                        self._close_exotherm(complete=True)
                        self._after_exotherm(Tq)
                else:
                    e['_low_since'] = None
        elif stage == 'iso':
            if t['iso_reached_s'] is None and t['ramp_k'] >= t['target_k'] - 1e-9 and abs(self._meas_Tj() - t['target_k']) < 0.5:
                t['iso_reached_s'] = now
            settled = t['iso_reached_s'] is not None and now - t['iso_reached_s'] >= m['wait_min'] * 60.0
            if now >= t['iso_end_s'] - 1e-9:
                self._to_cool('ISO_TIME_ELAPSED', None)
            elif not settled and not t['iso_tracking']:                # approach and settling: same overtaking criterion as heat/ramp
                if overtake:
                    self._open_exotherm(T_m, p_m, 'adiabatic' if m['mode'] == 'iso_track' else 'isothermal_jacket')
            elif t['search_start_s'] is None:                          # first detection window; Iso-Track: jacket now follows the sample
                t.update(search_start_s=now, stage_end_s=now + m['search_min'] * 60.0)
                if m['mode'] == 'iso_track' and not t['iso_tracking']:
                    t.update(iso_tracking=True, track_sp=None)
            elif m['mode'] == 'iso_fixed' and excess30 is not None and excess30 > EXCESS_DETECT_K:
                self._open_exotherm(T_m, p_m, 'isothermal_jacket')      # the sample releases heat into the held jacket
            elif now >= t['stage_end_s'] - 1e-9:                       # consecutive search windows, same regression as HWS
                r = self._search_slope(t['search_start_s'], now)
                if r is not None and r * 60.0 > thr:
                    self._open_exotherm(T_m, p_m, 'adiabatic' if m['mode'] == 'iso_track' else 'isothermal_jacket')
                else:
                    t.update(search_start_s=now, stage_end_s=now + m['search_min'] * 60.0)
        elif stage == 'ramp':
            if overtake:
                self._open_exotherm(T_m, p_m, 'adiabatic')             # change to adiabatic mode
            elif t['ramp_k'] >= t['target_k'] - 1e-9 and Tq >= t['target_k'] - 1.0:
                self._to_cool('RAMP_COMPLETE', None)
        elif stage == 'cool':
            timed_out = now - t['cool_start_s'] >= COOL_TIMEOUT_S
            if Tq <= m['cool_to_k'] or timed_out:
                reason = t.get('end_reason') or 'TEST_COMPLETE'
                self._record_point(force=True)
                self._finish('succeeded', reason + ';COOL_TIMEOUT' if (timed_out and Tq > m['cool_to_k']) else reason, t.get('end_validity'))
                return
        if self._test_active():
            self._record_point()

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
            nxt = self._next_events(end)
            self._integrate((nxt - self.time_ns) / 1e9)
            self._advance_mechanics(nxt)
            self.time_ns = nxt
            if self.power and nxt % CONTROL_NS == 0:
                self._tick()
        self.revision += 1


class NetzschMmc315Session:
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
        meta = copy.deepcopy(export_netzsch_mmc315_contract_metadata())
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0',
                    capabilities={k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or self._role != 'agent'},
                    state_slots={k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or self._role != 'agent'})

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        if 'test.started_by_other_agent' in values:
            values['test.started_by_other_agent'] = self._role == 'agent' and self._foreign()   # the guard binds agents only
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=RATE_WINDOW_S if k == 'self_heat_rate.indicated_k_min' else None) for k in ids})

    @staticmethod
    def _filter_result(result):
        result = copy.deepcopy(result)
        if 'exotherms' in result and 'template_source' in result:
            result['template_source'] = (str(LIBRARY_PATH.relative_to(ROOT)) + '; selected record available only through evaluator provenance.read') if result['template_source'] is not None else None
        return result

    def _foreign(self):
        h = self._host
        t = h.test
        owner = h._owners.get(t['op']) if t is not None else None
        return owner is not None and owner != self._principal and h._principals.get(owner) == 'agent'

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
            validate(CAPS[capability_id]['input_schema'], arguments)
            # a test started by another *agent* is private to it; host/panel-started tests are shared
            if self._role == 'agent':
                if capability_id in ('test.stop', 'test.result') and self._foreign():
                    raise ActionRejected('ACCESS_DENIED')
                if capability_id == 'method.configure' and self._foreign() and h._test_active():
                    raise ActionRejected('ACCESS_DENIED')
                if capability_id == 'power.set' and not arguments['enabled'] and self._foreign() and h._test_active():
                    raise ActionRejected('ACCESS_DENIED')
            result = self._filter_result(h.invoke(capability_id, **arguments))
            operation = result.get('operation_id') if capability_id == 'test.start' else None
            if operation:
                h._owners[operation] = self._principal
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=operation,
                            status='accepted' if operation else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                            result=result, observations={}, error=None)
            h._requests[key] = (digest, copy.deepcopy(response))
            return response
        except ActionRejected as exc:
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None,
                            status='unsupported' if str(exc) == 'UNSUPPORTED' else 'rejected', state_revision=h.revision,
                            clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                            error=dict(code=str(exc), message=str(exc), retryable=False, side_effects='none'))
            if key is not None and digest is not None and key not in h._requests:
                h._requests[key] = (digest, copy.deepcopy(response))
            return response

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        raise ActionRejected('ACCESS_DENIED')                       # no writable slot: setpoints are written through method.configure

    def poll(self, operation_id):
        h = self._host
        if not isinstance(operation_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        t = h.operations.get(operation_id)
        if t is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        result = h._result_of(t) if t['phase'] == 'running' else h.records[operation_id]
        return dict(operation_id=operation_id, status=result['phase'], result=self._filter_result(result))

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
                raise ActionRejected(response)                     # replay of the recorded rejection
            return copy.deepcopy(response)
        try:
            self.poll(operation_id)
            t = h.operations[operation_id]
            if t['phase'] != 'running' or h.test is not t:
                raise ActionRejected('NOT_RUNNING')
        except ActionRejected as exc:
            if digest is not None:
                h._requests[key] = (digest, str(exc))
            raise
        h._abort_test()
        h.revision += 1
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='succeeded',
                        state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=dict(phase=t['phase'], points=len(t['points'])),
                        observations={}, error=None)
        h._requests[key] = (digest, copy.deepcopy(response))
        return response

WRITE_VIA = {}
EFFECTS = {
    'plant.configure': ['plant.configured', 'lab.ambient_k', 'test.detection_limit_k_min', 'truth.sample_k', 'truth.jacket_k'],
    'method.configure': ['method.configured', 'test.detection_limit_k_min'],
    'power.set': ['power.enabled', 'power.angle_rad', 'sample.indicated_k', 'jacket.indicated_k', 'pressure.indicated_pa', 'self_heat_rate.indicated_k_min',
                  'test.running', 'test.stage', 'test.step_target_k', 'test.exotherm_count', 'jacket.mode', 'truth.heater_power_w'],
    'tray.move': ['tray.docked'],
    'lid.set': ['lid.closed', 'lid.open'],
    'sample.load': ['sample.loaded', 'test.detection_limit_k_min', 'sample.indicated_k', 'pressure.indicated_pa', 'self_heat_rate.indicated_k_min', 'truth.sample_k',
                    'truth.pressure_pa', 'truth.conversion', 'truth.gas_generated_mol'],
    'sample.unload': ['sample.loaded', 'test.detection_limit_k_min', 'sample.indicated_k', 'pressure.indicated_pa', 'self_heat_rate.indicated_k_min', 'truth.sample_k',
                      'truth.pressure_pa', 'truth.conversion', 'truth.gas_generated_mol'],
    'test.start': ['test.running', 'test.stage', 'test.step_target_k', 'test.exotherm_count', 'jacket.mode', 'test.started_by_other_agent'],
    'test.stop': ['test.running', 'test.stage', 'test.step_target_k', 'test.exotherm_count', 'jacket.mode', 'truth.heater_power_w'],
    'sim.step': list(SLOTS),
}
for _k, _v in EFFECTS.items():
    CAPS[_k]['effects'] = _v
ERRORS = sorted(set(re.findall(r"ActionRejected\('([A-Z_0-9]+)'", Path(__file__).read_text())))
ASYNC = {'test.start'}
PRECONDITIONS = {
    'plant.configure': ('只能声明一次（plant.configured为假）并显式接受；夹套加热功率须能在500°C仍达到厂家50K/min跟踪速率；1s数字PI须满足Kp·1s/C_j≤0.5（稳定裕度）；'
                        '样品与夹套热电偶须已标定（偏差之差≤0.02K）；热电偶噪声≤0.01K', ['plant.configured']),
    'method.configure': ('无进行中的测试；HWS终止温度须高于起始温度；等温模式须给出低于终止温度的等温温度与时长；已声明工程参数时冷却目标须比室温至少高5K',
                         ['test.running', 'plant.configured', 'lab.ambient_k']),
    'power.set': ('须已声明工程参数；断电取消进行中的测试', ['plant.configured', 'power.enabled']),
    'tray.move': ('须已声明工程参数；测试进行中不得移动托盘', ['plant.configured', 'test.running']),
    'lid.set': ('须已声明工程参数；测试进行中不得开盖', ['plant.configured', 'test.running']),
    'sample.load': ('须已声明工程参数、顶盖全开且静止（lid.open）、腔内无样品；须接受先验；须给比热，模板无分解热时须给分解热，含反应物时须给产气量；液体体积不超过容器的90%；'
                    '反应物全部分解的温升m_r·ΔH/C_s不超过1000K（模型范围）', ['plant.configured', 'lid.open', 'sample.loaded']),
    'sample.unload': ('腔内有样品、无进行中的测试、顶盖全开且静止（lid.open）、样品温度不高于60°C', ['sample.loaded', 'test.running', 'lid.open', 'truth.sample_k']),
    'test.start': ('须已声明工程参数、通电、已设方法（冷却目标比室温至少高5K）、已装样、顶盖关闭、托盘推到位、无进行中的测试、样品读数低于终止温度；'
                   '放热阈值不低于本配置的检出限test.detection_limit_k_min',
                   ['plant.configured', 'lab.ambient_k', 'power.enabled', 'method.configured', 'sample.loaded', 'lid.closed', 'tray.docked', 'test.running', 'sample.indicated_k',
                    'test.detection_limit_k_min']),
    'test.stop': ('有进行中的测试', ['test.running']),
}
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入结构、类型或范围（或分析数组长度不一致、终止温度不高于起始温度）', 'ACCESS_DENIED': '该角色无权调用，或当前测试属于另一个agent',
    'REQUEST_CONFLICT': '同一request_id已用于不同的请求', 'UNKNOWN_INSTANCE': '实例ID不存在', 'UNSUPPORTED': '本配置不提供该功能（理由见function_zh）',
    'PLANT_ALREADY_CONFIGURED': '工程参数只能声明一次', 'ENGINEERING_ACCEPTANCE_REQUIRED': '未显式接受工程参数或样品先验',
    'TRACKING_RATE_NOT_REACHABLE': '所给夹套热容/功率/散热在500°C达不到厂家50K/min跟踪速率', 'BUSY': '有进行中的测试', 'INVALID_METHOD': '方法参数自相矛盾',
    'CONTROLLER_UNSTABLE': '1s数字PI的Kp·周期/C_j超过0.5，控制回路会振荡',
    'THERMOCOUPLE_MISMATCH': '热电偶对未标定：样品与夹套热电偶偏差之差超过0.02K（远低于0.05K结束/0.10K检出的超温判据）',
    'THRESHOLD_BELOW_DETECTION_LIMIT': '放热阈值低于本配置的检出限（失配漂移G_sj·Δoffset/C_s与热电偶噪声、跟踪注入噪声、量化决定的搜索/结束窗斜率标准误差；数值见test.detection_limit_k_min）', 'COOL_TARGET_BELOW_AMBIENT': '冷却目标低于室温+5K，只靠风扇/自然冷却达不到',
    'ADIABATIC_RISE_OUT_OF_MODEL_RANGE': '反应物全部分解的温升m_r·ΔH/C_s超过1000K，超出本模型范围',
    'TEMPERATURE_LIMIT_EXCEEDED': '样品读数超过550°C超温保护：测试立即以失败结束，加热器关闭、样品自然冷却',
    'PLANT_REQUIRED': '尚未声明工程参数', 'TEST_ACTIVE': '测试进行中，不能移动托盘、开盖或取样', 'ALREADY_LOADED': '量热腔内已有样品容器',
    'LID_NOT_OPEN': '顶盖未全开或仍在运动', 'UNKNOWN_TEMPLATE': '模板库中没有该模板', 'HEAT_OF_DECOMPOSITION_REQUIRED': '模板没有公开分解热，须由宿主给出',
    'SAMPLE_HEAT_CAPACITY_REQUIRED': '须给出样品比热', 'GAS_YIELD_REQUIRED': '含反应物的样品须给出产气量', 'CONTAINER_OVERFILLED': '液体体积超过容器的90%',
    'NO_SAMPLE': '量热腔内没有样品容器', 'SAMPLE_TOO_HOT': '样品温度高于60°C，不能取出', 'POWER_OFF': '未通电时拒绝；运行中断电使测试以POWER_OFF取消（数据保留）',
    'METHOD_REQUIRED': '未设定测试方法', 'LID_OPEN': '顶盖未关闭', 'TRAY_NOT_DOCKED': '托盘未推到位对接', 'SAMPLE_ABOVE_END_TEMPERATURE': '样品温度已高于终止温度',
    'NOT_RUNNING': '没有进行中的测试', 'USER_CANCEL': '测试被停止或取消（数据保留）', 'PRESSURE_LIMIT_EXCEEDED': '容器压力（绝压）超过150bar上限：测试立即以失败结束，加热器关闭、样品自然冷却',
    'UNKNOWN_CAPABILITY': '能力ID不存在', 'UNKNOWN_OPERATION': '操作ID不存在',
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_GUARDED = {'test.stop', 'test.result', 'method.configure', 'power.set'}
CAP_ERRORS = {
    'plant.configure': ['PLANT_ALREADY_CONFIGURED', 'ENGINEERING_ACCEPTANCE_REQUIRED', 'TRACKING_RATE_NOT_REACHABLE', 'CONTROLLER_UNSTABLE', 'THERMOCOUPLE_MISMATCH'],
    'method.configure': ['BUSY', 'INVALID_METHOD', 'COOL_TARGET_BELOW_AMBIENT'], 'power.set': ['PLANT_REQUIRED'], 'tray.move': ['PLANT_REQUIRED', 'TEST_ACTIVE'],
    'lid.set': ['PLANT_REQUIRED', 'TEST_ACTIVE'],
    'sample.load': ['PLANT_REQUIRED', 'ALREADY_LOADED', 'LID_NOT_OPEN', 'ENGINEERING_ACCEPTANCE_REQUIRED', 'UNKNOWN_TEMPLATE', 'HEAT_OF_DECOMPOSITION_REQUIRED',
                    'SAMPLE_HEAT_CAPACITY_REQUIRED', 'GAS_YIELD_REQUIRED', 'CONTAINER_OVERFILLED', 'ADIABATIC_RISE_OUT_OF_MODEL_RANGE'],
    'sample.unload': ['NO_SAMPLE', 'TEST_ACTIVE', 'LID_NOT_OPEN', 'SAMPLE_TOO_HOT'],
    'test.start': ['PLANT_REQUIRED', 'POWER_OFF', 'METHOD_REQUIRED', 'COOL_TARGET_BELOW_AMBIENT', 'NO_SAMPLE', 'LID_OPEN', 'TRAY_NOT_DOCKED', 'BUSY',
                   'SAMPLE_ABOVE_END_TEMPERATURE', 'THRESHOLD_BELOW_DETECTION_LIMIT', 'USER_CANCEL', 'PRESSURE_LIMIT_EXCEEDED', 'TEMPERATURE_LIMIT_EXCEEDED'],
    'test.stop': ['NOT_RUNNING'],
}
_PARTIAL = {'USER_CANCEL', 'POWER_OFF', 'PRESSURE_LIMIT_EXCEEDED', 'TEMPERATURE_LIMIT_EXCEEDED'}


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    access = ['ACCESS_DENIED'] if (CAPS[k]['visibility'] != 'agent' or k in _GUARDED) else []
    return _COMMON_ERRORS + access + CAP_ERRORS.get(k, [])


OBSERVES = {
    'plant.configure': ['plant.configured'], 'method.configure': ['method.configured'], 'power.set': ['power.enabled'], 'tray.move': ['tray.docked', 'tray.position_m'],
    'lid.set': ['lid.closed', 'lid.open', 'lid.angle_rad'], 'sample.load': ['sample.loaded'], 'sample.unload': ['sample.loaded'],
    'test.start': ['test.running', 'test.stage', 'test.exotherm_count', 'test.step_target_k', 'jacket.mode', 'test.started_by_other_agent', 'test.detection_limit_k_min', 'sample.indicated_k',
                   'pressure.indicated_pa', 'self_heat_rate.indicated_k_min'],
    'test.stop': ['test.running', 'test.stage', 'jacket.mode'], 'test.result': ['test.running', 'test.stage', 'test.exotherm_count', 'test.started_by_other_agent'],
    'sim.step': ['clock.time_s'],
}
_MAN, _PRE, _WB = 'manufacturer_primary', 'manufacturer_predecessor_brochure', 'user_workbook'
CAP_SOURCES = {
    'plant.configure': ['simulation_assumption', _MAN], 'method.configure': [_PRE, _MAN, _WB], 'power.set': ['simulation_assumption'],
    'tray.move': [_MAN, _PRE, 'geometry'], 'lid.set': [_PRE, 'geometry'], 'sample.load': ['template_library', _PRE, _MAN, 'nist_toluene', 'simulation_assumption'],
    'sample.unload': ['simulation_assumption'], 'test.start': [_PRE, _MAN, 'template_library', 'simulation_assumption'], 'test.stop': [_PRE],
    'test.result': [_PRE, 'template_library'], 'analysis.kinetics': [_PRE], 'ledger.read': ['simulation_assumption', 'runtime_code'],
    'provenance.read': ['template_library'], 'sim.step': ['runtime_code', 'simulation_assumption'],
    'module.scanning': [_MAN, _PRE], 'module.coin_cell_high_temperature': [_MAN], 'module.coin_cell_high_sensitivity': [_MAN], 'variphi.set': [_PRE, _MAN],
    'mode.fire_exposure': [_PRE], 'battery.cycler_connect': [_PRE], 'sample.stir': [_MAN], 'hardware.connect': [_PRE], 'robot.handle_container': ['simulation_assumption'],
    'accuracy.certification': [_PRE]}
PARAM_ZH = {
    'ambient_k': '实验室环境温度', 'ambient_pressure_pa': '实验室大气压（决定容器初始空气量）', 'jacket_heat_capacity_j_k': '量热腔夹套（顶/侧/底加热器）集总热容',
    'jacket_heater_max_w': '夹套加热器总功率上限', 'jacket_loss_w_k': '夹套对环境的散热系数', 'cooling_fan_w_k': '冷却阶段风扇附加散热系数',
    'sample_jacket_conductance_w_k': '样品容器与夹套之间的传热系数', 'controller_gain_w_k': '夹套PI比例增益', 'controller_integral_s': '夹套PI积分时间',
    'sample_tc_offset_k': '样品热电偶静态偏差（与夹套热电偶之差须≤0.02K；失配漂移计入检出限）', 'jacket_tc_offset_k': '夹套热电偶静态偏差（与样品热电偶之差须≤0.02K）',
    'tc_noise_k': '热电偶相关噪声标准差（ARC级≤0.01K）',
    'pressure_offset_pa': '压力传感器静态偏差', 'pressure_noise_pa': '压力传感器相关噪声标准差', 'line_dead_volume_m3': '取压管与传感器死体积',
    'tray_speed_m_s': '宿主拉/推托盘速度', 'lid_speed_rad_s': '宿主开/合顶盖角速度', 'noise_seed': '噪声随机数种子', 'accept_engineering': '宿主显式接受以上工程参数',
    'mode': '测试模式', 'start_temperature_k': 'HWS起始温度', 'end_temperature_k': '终止温度（≤500°C）', 'step_k': 'HWS台阶温差', 'heat_rate_k_min': '台阶/升温加热速率（0–5K/min）',
    'wait_min': 'HWS/等温等待时间（须远长于样品–夹套热时间常数）', 'search_min': 'HWS/等温搜索窗长度（舍弃前1/3且至少2min后回归）', 'threshold_k_min': '放热检出阈值（厂家示例0.02K/min；不得低于test.detection_limit_k_min）', 'max_pressure_pa': '终止压力（≤150bar）',
    'max_rate_k_min': '终止自热速率（≤50K/min）', 'iso_temperature_k': '等温老化温度', 'iso_hours': '等温老化时长', 'cool_to_k': '冷却到此温度后测试结束',
    'data_interval_s': '曲线记录间隔', 'template_id': '模板库记录', 'sample_mass_kg': '样品质量（含溶剂）', 'reactant_mass_fraction': '模板反应物质量分数',
    'solvent': '溶剂（决定蒸气压）', 'container_material': '样品容器材质', 'container_volume_m3': '样品容器内容积（0.5–8.5mL）', 'container_mass_kg': '样品容器质量',
    'container_cp_j_kg_k': '容器材料比热', 'sample_cp_j_kg_k': '样品比热', 'liquid_density_kg_m3': '样品液体密度（决定气相体积）',
    'heat_of_decomposition_j_kg': '反应物分解热（每kg反应物）', 'gas_yield_mol_per_mol': '每摩尔反应物生成的不凝气摩尔数', 'reaction_order': '反应级数（厂家：一级）',
    'accept_priors': '宿主显式接受样品先验',
}
BACK_CALCULATED = {'sample_cp_j_kg_k': '280.675J/g÷133.7K=2099.3J/(kg·K)', 'container_cp_j_kg_k': '由φ=1.48与样品热容反推576.8J/(kg·K)',
                   'gas_yield_mol_per_mol': '由42.7bar压力台阶与1mL死体积反推0.336mol/mol'}
NULLABLE_ZH = {'iso_temperature_k': 'null=非等温模式', 'iso_hours': 'null=非等温模式', 'sample_cp_j_kg_k': '必须给出（null会被拒绝）',
               'heat_of_decomposition_j_kg': 'null=用模板值（只有DTBP有），其余模板必须给出', 'gas_yield_mol_per_mol': 'null只允许不含反应物的样品'}
LIMITATIONS = [
    '选定MMC 315 Nexus与ARC模块；未选扫描模块、两种纽扣电池模块与VariPhi内加热器，不实现火灾暴露、恒功率扫描、搅拌/注入与电池联用；Proteus软件与硬件通信未实现。',
    '厂家规格：ARC模块跟踪速率50K/min、室温–500°C、最高150bar、容器0.5–8.5mL（调研表/2026总目录）；温度读数0.01K、压力读数0.01bar、加热速率0–5K/min、“跟踪速率最高可设50K/min”“最高可检测150bar”、容器材质与HWS/Iso-Fixed/Iso-Track只见于前代MMC 274手册，作为相关型号依据；整机尺寸、质量与功率厂家未公开。',
    '量热腔为两节点集总热模型（夹套、样品+容器），夹套热容/加热功率/散热/风扇、样品–夹套传热、PI参数、热电偶与压力传感器偏差/噪声都是宿主工程参数；默认值保证500°C时仍能达到50K/min跟踪速率，非厂家数据；两支热电偶须已标定（偏差之差≤0.02K，参照ARC漂移检查做法），热电偶噪声不超过0.01K（ARC级），否则拒绝；启动测试时计算本配置的检出限（失配漂移G_sj·Δoffset/C_s加6倍搜索窗斜率标准误差，且阈值的一半须高于漂移加4倍结束窗标准误差；标准误差含相关噪声、量化与跟踪注入噪声，后者取1.5倍安全系数；解析式经本运行时稳态绝热跟踪数据校核，σ 0.003–0.01K、G_sj 0.1–2W/K、180–600s窗的实测标准差与解析值之比为0.8–1.15（本资产与独立复核的实测范围）），阈值低于检出限拒绝启动，检出限可由test.detection_limit_k_min读取。',
    '仪器按1s周期（工程值）采样、更新噪声、执行测试逻辑并计算下一周期的加热功率（前馈+数字PI，Kp·1s/C_j≤0.5）；物理子步不跨周期，结果与宿主步长无关。夹套只加热不主动制冷；绝热跟踪以测得样品温度为目标，目标变化限于厂家50K/min，样品更快时夹套落后、结果标记tracking_rate_exceeded；热电偶偏差失配会造成可检出的漂移。',
    '反应为单一模板反应物的n级Arrhenius分解（模板：Nouryon稀溶液或聚合物基体中的一级分解动力学），未含自催化、多步、相变、蒸发吸热或溶剂/产物二次反应；DTBP以外的模板没有公开分解热，必须由宿主给出；所有模板的产气量与样品比热都是宿主先验；m_r·ΔH/C_s超过1000K的装样被拒绝（模型范围）。',
    'DTBP示例的样品比热2099.3J/(kg·K)（=280.675J/g÷133.7K）、钛容器比热576.8J/(kg·K)（由φ=1.48反推）与产气量0.336mol/mol（由42.7bar压力台阶与所设1mL死体积反推）都由厂家示例结果反推；因此与示例的φ、ΔT、ΔT_ad、放热量和压力台阶的一致性不是独立预测，只有起始温度、自热速率曲线与Townsend–Tou动力学是由模板动力学独立预测的。',
    '压力=密封时的空气（室温下已被溶剂蒸气饱和）+产气（理想气体）+溶剂蒸气（纯溶剂NIST甲苯Antoine，不按Raoult组成修正；临界温度以上按最高参数组平滑延拓，并以全部汽化为上限，没有相变跳变）；气相体积固定为容器+死体积−装样液体体积，未含液体热膨胀、气体溶解或非理想性；压力读数到厂家可检测的150bar饱和，真实压力超过150bar测试失败；厂家未说明150bar是表压还是绝压，本资产按绝压处理（读数与极限均为绝对压力）。注意：该延拓不是超临界状态方程——装液密度高于溶剂临界密度且温度超过临界温度时，真实压力（液体膨胀充满容器/致密超临界流体）远高于模型值（独立复核的Peng–Robinson估算：5.74g甲苯在9.5mL中620K约267bar，模型约57bar），模型可能漏报150bar超压失效，对这类高装量高温测试是非保守的。',
    'HWS在每个搜索段末用示值样品温度的最小二乘斜率（舍弃前1/3且至少2min平衡时间，搜索与等待时间下限5min）与阈值比较；等待时间须远长于样品–夹套热时间常数（默认参数约3min），过短时平衡升温会被当作自热检出（与真实仪器相同）。在加热、等待、升温段以及等温段的升温与平衡期，样品比夹套高出的30s平均温差超过0.1K且仍在升温即判为自热快于程序：HWS/升温/Iso-Track转入绝热跟踪，Iso-Fixed记为非绝热事件。Iso-Fixed平衡后以30s平均超温超过0.1K（向夹套放热）或搜索段斜率超过阈值检出、夹套不跟踪，事件持续到60s平均超温回落到0.05K以下并保持一个结束窗（回差）为止，记录最大超温；Iso-Track平衡后夹套跟踪样品（绝热储存）、按搜索段检出；等温事件结束后继续等温直到设定时长；时长到时非绝热（Iso-Fixed）事件结束，绝热事件一律继续跟踪并按正常判据完整结束；升温段事件结束后继续升温。厂家只给出模式名称，细节为工程解释。',
    '绝热放热在最近一个结束窗（搜索回归长度与10min取大）的回归斜率低于阈值的一半时结束（回差，防止把接近阈值的失控提前结束）；前馈用对称的30s速率（噪声不被整流成加热）；终止自热速率只比较扣除程序升温后的自热速率（加热、等待与等温平衡期都扣除程序升温速率，因为样品追赶夹套的速率不超过它）；结果中的起始/结束/最高温度、压力、ΔT、φ、ΔT_ad、TMR、最大速率（归于30s回归窗中心）与Townsend–Tou动力学（附质量标记；被终止条件、停止或等温时长截断的事件标为incomplete_exotherm）都由1s采样、量化带噪声的示值计算，不读取过程真值；Iso-Fixed放热为非绝热，不给ΔT_ad、放热量与动力学。',
    '样品读数超过550°C（文档500°C以上50K，工程超温保护）测试失败；热电偶读数范围上限1300°C（型号未公开，工程值）；冷却目标须比室温至少高5K，冷却48h未达到时以COOL_TIMEOUT结束；曲线点满20000点时隔点抽稀并加倍记录间隔；放热事件超过20条时只计数不记录。',
    '托盘拉出/推回、开/合顶盖、装样/取样为宿主代理（不是机器人接触验证）；测试中禁止移动托盘或开盖，测试结束后可开盖，但取出容器须样品冷却到60°C以下并泄压（工程安全规则）；容器材质（含文档列出的玻璃内衬）只作标签，热容与质量由宿主给出。',
    'agent只看到量化带噪声的样品/夹套温度、压力、自热速率、阶段、夹套控制方式与结果；真实温度、转化率、放热功率、产气量与所选模板身份只对evaluator可见；某个agent启动的测试，其他agent不能停止、读取完整结果或改方法、断电（可由test.started_by_other_agent看到）；同一request_id无论被接受或被拒都绑定到第一次请求。',
    '时间推进为1s周期内事件对齐的子步（反应快时自适应缩短到1ms）：两节点热方程用精确解推进（expm1形式，毫秒子步仍保持精度），热量账按解析积分、物质账按每步反应增量记账，均用补偿求和，残差为真实守恒检查。',
    '没有真实硬件、Proteus导出文件、ASTM E1981校准或厂家准确度验证；模板为厂家半衰期测量得到的一级动力学，不是ARC原生测试文件（DTBP的ARC示例除外）。',
]


def export_netzsch_mmc315_contract_metadata():
    slots = {}
    caps = {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'NetzschMmc315Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='interval_mean' if k == 'self_heat_rate.indicated_k_min' else 'instant', time_domain='simulation',
                                      window_description='Pure read of the latest 1 s instrument cycle; thermocouples 0.01 K and pressure 0.01 bar quantisation with correlated noise (pressure saturates at 150 bar); self-heat rate = regression slope of the indicated sample temperature over the last 120 s.'),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.netzsch_mmc315.NetzschMmc315Session.invoke',
                                              invocation=f'session.invoke(instance_id="mmc315_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + '；被拒时状态不变。', state_ids=list(PRECONDITIONS[k][1]),
                                                                              enforced_by='runtime', check='NetzschMmc315Runtime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或操作标志；温度、反应、压力与测试阶段只随step推进')
                                for s in v['effects']],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=dict(mode='none', description_zh='运行层只输出状态，由场景层绑定关节与读数', binding_ids=[]),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion='冷却到设定温度后结束（HWS走完/终止温度/终止压力/终止速率/等温时间到）；或停止、断电、超压' if async_ else '原子配置或外部动作请求，实际状态由step推进',
                                completion_state_ids=['test.running', 'test.stage'] if async_ else [], timeout_s=None, cancellation='supported' if async_ else 'unsupported',
                                retry='same_request_id', resources=['host_clock', 'calorimeter_module', 'sample_container', 'pressure_transducer'],
                                partial_failure_zh='停止/断电/超压只终止本次测试；已记录曲线与放热事件保留，加热器关闭。'),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH[e], side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=list(LIMITATIONS), source_ids=list(CAP_SOURCES[k]))
        if k in ('test.start', 'power.set', 'tray.move', 'lid.set', 'sample.load'):
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh='由统一宿主时钟推进温度、反应、压力、机械动作与测试阶段')
                                   for s in SLOTS if s not in v['effects'] and SLOTS[s]['origin'] in ('sensor', 'derived', 'scene', 'process_truth', 'setpoint')]
        caps[k] = c
    mechanism = dict(
        component_ids=['module_body', 'side_heater', 'bottom_heater', 'top_heater', 'sample_container', 'sample_thermocouple', 'pressure_line', 'pressure_transducer',
                       'controller_board', 'cooling_fan'],
        state_ids=list(slots), meaning_zh='ARC模块量热腔的两节点热模型与绝热跟踪、模板反应物的n级Arrhenius分解放热与产气、容器压力（空气+产气+溶剂蒸气）、HWS/等温/升温测试逻辑与结果评价',
        implementation='lumped',
        equations=['C_j dT_j/dt = P - G_ja (T_j - T_a) - G_f (T_j - T_a) - G_sj (T_j - T_s) ; C_s dT_s/dt = Q_rxn + G_sj (T_j - T_s) (exact 2x2 solution per sub-step)',
                   'C_s = m_s c_s + m_v c_v ; phi = C_s / (m_s c_s) ; dT_ad = phi dT_obs',
                   'dalpha/dt = A exp(-Ea/(R T_s)) (1 - alpha)^n ; Q_rxn = m_r dH dalpha/dt ; n_gas = nu n_r0 alpha',
                   'p = (n_air + n_gas) R T_s / V_g + min(p_sat,solvent(T_s), n_solv R T_s / V_g) ; V_g = V_container + V_line - m_s/rho ; n_air = (p_amb - p_sat(T_a)) V_g / (R T_a)',
                   'every 1 s: P = clamp(C_j dT*/dt + G_ja (T_j,m - T_a) + G_sj (T_j,m - T_s,m) + Kp e + I, 0, P_max) held for the cycle ; T* = programme, hold or tracking set point (d T*/dt <= 50 K/min toward T_s,m)',
                   'HWS: heat to step -> wait -> search: slope(T_s,m over the window after max(2 min, 1/3)) > threshold => adiabatic exotherm tracking until slope(T_s,m over the end window = max(search regression, 10 min)) < threshold/2 ; ends at T_end / p_end / self-heat rate_end, then cool',
                   'y(t+dt) = y exp(-k dt y^(n-1) log1p(z)/z), z = (n-1) k dt y^(n-1) (exact n-th order step)',
                   'Townsend-Tou: k* = (dT/dt)/dT_obs (dT_obs/(T_f - T))^n ; ln k* = ln A - Ea/(R T)'],
        parameters={}, source_ids=['simulation_assumption', 'template_library'], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_mmc315_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'arc_calorimetry': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'method.configure': (METHOD, METHOD_EXAMPLE), 'sample.load': (LOAD, LOAD_EXAMPLE)})
    params = mechanism['parameters']
    for cap_name, (schema, example) in (('method.configure', (METHOD, METHOD_EXAMPLE)), ('sample.load', (LOAD, LOAD_EXAMPLE))):
        for name, prop in schema['properties'].items():
            if name in params or 'anyOf' not in prop:
                continue
            base = next(x for x in prop['anyOf'] if x.get('type') != 'null')
            params[name] = dict(value=example.get(name), unit=base.get('x-unit', '1'),
                                meaning_zh=f'{PARAM_ZH.get(name, name)}；宿主在 {cap_name} 显式输入，可为null（{NULLABLE_ZH[name]}）；此处登记示例值，非厂家标定或实测。',
                                evidence='assumption', source_ids=['simulation_assumption'],
                                uncertainty=f"schema 允许 {base.get('minimum')}–{base.get('maximum')} {base.get('x-unit', '1')} 或 null")
    for name, how in BACK_CALCULATED.items():
        if name in params:
            params[name]['meaning_zh'] = f'{PARAM_ZH.get(name, name)}：DTBP示例值由厂家示例结果反推（{how}），不是独立测得的物性；与该示例相应结果的一致性因此不是独立预测。'
            params[name]['uncertainty'] = '反推值：继承厂家示例读数与所设死体积/容器数据的不确定度'
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'arc_calorimetry': mechanism})
