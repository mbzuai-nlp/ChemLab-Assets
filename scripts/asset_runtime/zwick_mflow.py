"""CHEM-146 ZwickRoell Mflow 1128610 extrusion plastometer runtime (ISO 1133 methods A and B, SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * barrel/furnace lumped heat node with a PI controller (heater limited by an explicit engineering power), ambient loss;
  * polymer charge: enthalpy with sensible + latent (melting range) terms, pellet compaction (bulk -> melt density),
    heat exchange with the barrel, thermal-history clock above the melting onset;
  * die flow: reference MVR of the selected manufacturer record transferred to the actual condition with explicit
    host priors (power-law index n, flow activation energy Ea, thermal-history rate) and the piston friction:
        Q = MVR_ref * ((m g - F_f) / (m_ref g))^(1/n) * exp(-(Ea / (n R)) (1/T - 1/T_ref)) * exp(k_h t_hist) * phi^3 ;
  * piston rides on the charge column (compaction + extrusion), weight potential energy -> friction heat (barrel) and
    pressure work (charge); manual host withdraw / insert at finite speed;
  * automatic cutter: alternate blade half turns, the strand is severed when the blade crosses pi/2;
  * external RS-232 analytical balance proxy with settling time, resolution, gain/offset (no built-in balance);
  * displacement encoder (0.003 mm), time base (0.002 s), temperature display (0.01 K) quantisation.
A single MVR/MFR scalar does not identify the rheology: n, Ea, melt density, cp, latent heat, compaction and history are
explicit host priors (evidence = assumption).  The observed results are computed only from quantised observations.
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = '1.0.0'
SLUG = 'zwick_mflow'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-146'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t1_thermal' / SLUG
LIBRARY_PATH = ASSET / 'melt_flow_library.json'

# ---------------------------------------------------------------- documented / standard constants
GRAVITY = 9.80665
GAS_CONSTANT = 8.314462618
BARREL_DIAMETER_M = 0.00955                      # 1128612 (documented)
PISTON_HEAD_DIAMETER_M = 0.009474                # ISO 1133-1 piston head
STANDARD_AREA_M2 = math.pi / 4 * ((BARREL_DIAMETER_M + PISTON_HEAD_DIAMETER_M) / 2) ** 2   # ISO mean area, 0.711 cm^2
DIE_DIAMETER_M = 0.002095                        # 312342 (documented)
DIE_LENGTH_M = 0.008
DIE_AREA_M2 = math.pi * DIE_DIAMETER_M ** 2 / 4
BARREL_LENGTH_M = 0.180
PISTON_TRAVEL_M = BARREL_LENGTH_M
PISTON_LOAD_KG = 0.325                           # the piston alone is the 0.325 kg load stage (documented)
LOAD_RANGE_KG = (0.325, 21.6)                    # documented total test load range
TEMPERATURE_RANGE_K = (323.15, 723.15)           # documented 50 .. 450 degC
ENCODER_RESOLUTION_M = 3e-6                      # documented 0.003 mm
TIME_RESOLUTION_S = 0.002                        # documented 0.002 s
TEMPERATURE_DISPLAY_RESOLUTION_K = 0.01          # documented "< 0.1 K"; this model shows 0.01 K steps
CONNECTED_APPARENT_POWER_VA = 700.0              # documented 0.7 kVA full load (not the heater power)
MFR_RANGE_KG_S = (0.1e-3 / 600, 2000e-3 / 600)   # product page approx. 0.1 .. 2000 g/10 min
# transducer arm (same geometry as outputs/.../interface_definition.py; checked by the tests)
SENSOR_ZERO_POSITION_M = 0.050
SENSOR_ARM_LENGTH_M = 0.140
SENSOR_ELEVATION_MAX_RAD = 0.42
SENSOR_REFERENCE_ELEVATION_RAD = math.asin(-SENSOR_ZERO_POSITION_M / SENSOR_ARM_LENGTH_M)
SENSOR_RANGE_M = (0.0, SENSOR_ZERO_POSITION_M + SENSOR_ARM_LENGTH_M * math.sin(SENSOR_ELEVATION_MAX_RAD))
CUTTER_TRAVEL_RAD = math.pi
POWER_SWITCH_ON_RAD = 0.28
MAX_SUBSTEP_S = 0.05
MIN_PISTON_M = 0.005                             # the piston must stay above the die (end of usable charge)
LIVE_MVR_WINDOW_S = 4.0


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


def array(s, minimum=0, maximum=200001):
    return dict(type='array', items=s, minItems=minimum, maxItems=maximum)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


BOOL = {'type': 'boolean'}
TEXT = {'type': 'string'}
INT = {'type': 'integer', 'x-unit': '1'}
PHASE = enum(['idle', 'running', 'succeeded', 'cancelled', 'failed'])


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
        _LIBRARY = {r['id']: r for r in json.loads(LIBRARY_PATH.read_text())['templates']}
    return _LIBRARY


def quantize(value, step):
    return round(value / step) * step


def sensor_arm_angle(position_m):
    """Transducer-arm joint angle (0 = reference pose, piston on the die); upper stop beyond ~107 mm."""
    s = (position_m - SENSOR_ZERO_POSITION_M) / SENSOR_ARM_LENGTH_M
    if s >= math.sin(SENSOR_ELEVATION_MAX_RAD):
        elevation = SENSOR_ELEVATION_MAX_RAD
    else:
        elevation = math.asin(max(s, math.sin(SENSOR_REFERENCE_ELEVATION_RAD)))
    return elevation - SENSOR_REFERENCE_ELEVATION_RAD


# ---------------------------------------------------------------- inputs
PLANT_EXAMPLE = dict(ambient_k=296.15, barrel_heat_capacity_j_k=900.0, barrel_loss_w_k=0.55, heater_max_w=450.0, controller_gain_w_k=80.0,
                     controller_integral_s=120.0, sample_conductance_w_k=0.25, piston_friction_n=0.30, manual_piston_speed_m_s=0.02,
                     cutter_half_turn_s=0.25, accept_engineering=True)
PLANT = obj(dict(ambient_k=number('K', 273.15, 323.15), barrel_heat_capacity_j_k=number('J/K', 100.0, 5000.0), barrel_loss_w_k=number('W/K', 0.01, 10.0),
                 heater_max_w=number('W', 10.0, CONNECTED_APPARENT_POWER_VA), controller_gain_w_k=number('W/K', 1.0, 2000.0),
                 controller_integral_s=number('s', 5.0, 10000.0), sample_conductance_w_k=number('W/K', 0.001, 20.0),
                 piston_friction_n=number('N', 0.0, 3.0), manual_piston_speed_m_s=number('m/s', 0.001, 0.1), cutter_half_turn_s=number('s', 0.05, 5.0),
                 accept_engineering=BOOL))
SAMPLE_EXAMPLE = dict(reference_id='basf_ultradur_b4520', reference_temperature_k=523.15, reference_load_kg=2.16, mass_kg=0.0060,
                      bulk_density_kg_m3=800.0, melt_density_kg_m3=1110.0, specific_heat_j_kg_k=1900.0, initial_temperature_k=296.15,
                      melting_low_k=488.15, melting_high_k=498.15, latent_heat_j_kg=48000.0, power_law_index=0.75,
                      activation_energy_j_mol=50000.0, history_rate_per_s=5e-5, accept_rheology_thermal_density_and_packing_assumptions=True)
SAMPLE = obj(dict(reference_id=TEXT, reference_temperature_k=number('K', 323.15, 723.15), reference_load_kg=number('kg', 0.325, 21.6),
                  mass_kg=number('kg', 1e-4, 0.02), bulk_density_kg_m3=number('kg/m^3', 100.0, 3000.0), melt_density_kg_m3=number('kg/m^3', 300.0, 3000.0),
                  specific_heat_j_kg_k=number('J/(kg*K)', 300.0, 5000.0), initial_temperature_k=number('K', 273.15, 373.15),
                  melting_low_k=number('K', 273.15, 723.15), melting_high_k=number('K', 273.15, 723.15), latent_heat_j_kg=number('J/kg', 0.0, 3e5),
                  power_law_index=number('1', 0.2, 1.0), activation_energy_j_mol=number('J/mol', 0.0, 2e5), history_rate_per_s=number('1/s', -0.005, 0.005),
                  accept_rheology_thermal_density_and_packing_assumptions=BOOL))
METHOD_EXAMPLE = dict(test_load_kg=2.16, preheat_time_s=300.0, duration_s=240.0, sample_interval_s=0.2, cut_interval_s=10.0,
                      window_upper_m=0.050, window_lower_m=0.020, temperature_tolerance_k=0.5, automatic_weighing=True)
METHOD = obj(dict(test_load_kg=number('kg', 0.325, 21.6), preheat_time_s=number('s', 60.0, 3600.0), duration_s=number('s', 1.0, 3600.0),
                  sample_interval_s=number('s', TIME_RESOLUTION_S, 30.0), cut_interval_s=nullable(number('s', 0.5, 600.0)),
                  window_upper_m=number('m', 0.005, 0.075), window_lower_m=number('m', 0.005, 0.075), temperature_tolerance_k=number('K', 0.01, 5.0),
                  automatic_weighing=BOOL))
BALANCE_EXAMPLE = dict(resolution_kg=1e-7, gain=1.0, offset_kg=0.0, settling_s=4.0, accept_external_balance_proxy=True)
BALANCE = obj(dict(resolution_kg=number('kg', 1e-9, 1e-3), gain=number('1', 0.5, 1.5), offset_kg=number('kg', -1e-3, 1e-3), settling_s=number('s', 0.01, 120.0),
                   accept_external_balance_proxy=BOOL))
CAL_ENCODER = obj(dict(observed_low_m=number('m', -0.01, 0.2), observed_high_m=number('m', -0.01, 0.2), reference_low_m=number('m', 0.0, 0.2), reference_high_m=number('m', 0.0, 0.2)))
CAL_BALANCE = obj(dict(observed_low_kg=number('kg', -1e-3, 0.2), observed_high_kg=number('kg', -1e-3, 0.2), reference_low_kg=number('kg', 0.0, 0.2), reference_high_kg=number('kg', 0.0, 0.2)))
ROW = obj(dict(point_index=INT, time_s=number('s'), encoder_position_m=nullable(number('m')), temperature_k=nullable(number('K')),
               quality=enum(['ok', 'encoder_out_of_range', 'power_off'])))
SEGMENT = obj(dict(segment_id=TEXT, start_time_s=nullable(number('s')), end_time_s=number('s'), duration_s=nullable(number('s')),
                   observed_mass_kg=nullable(number('kg')), status=enum(['unweighed', 'weighing', 'weighed']), measurement_segment=BOOL))
FLOW = obj(dict(mvr_m3_s=nullable(number('m^3/s')), mvr_cm3_10min=nullable(number('cm^3/(10min)')), mfr_kg_s=nullable(number('kg/s')),
                mfr_g_10min=nullable(number('g/(10min)')), density_at_test_temperature_kg_m3=nullable(number('kg/m^3')),
                window_positions_m=nullable(array(number('m'), 2, 2)), window_times_s=nullable(array(number('s'), 2, 2)), weighed_segments=INT, evidence=TEXT))
VALIDITY = enum(['not_completed', 'valid', 'window_not_traversed', 'temperature_out_of_tolerance', 'preheat_below_iso_minimum', 'sample_exhausted', 'cancelled'])
RESULT = obj(dict(operation_id=nullable(TEXT), phase=PHASE, elapsed_s=number('s'), test_temperature_k=nullable(number('K')), test_load_kg=nullable(number('kg')),
                  rows=array(ROW), segments=array(SEGMENT, 0, 10000), flow=nullable(FLOW), validity=VALIDITY, terminal_reason=nullable(TEXT),
                  template_source=nullable(TEXT), evidence=TEXT))
ANALYSIS = obj(dict(start_position_m=number('m', 0.0, 0.2), end_position_m=number('m', 0.0, 0.2), start_time_s=number('s', 0.0, 1e9), end_time_s=number('s', 0.0, 1e9),
                    area_prior_m2=number('m^2', 1e-6, 1e-3), weighed_segments=array(obj(dict(mass_kg=number('kg', 0.0, 1.0), duration_s=number('s', TIME_RESOLUTION_S, 1e6))), 0, 10000),
                    melt_density_prior_kg_m3=nullable(number('kg/m^3', 100.0, 5000.0))))
ANALYSIS_OUTPUT = obj(dict(mvr_m3_s=number('m^3/s'), mvr_cm3_10min=number('cm^3/(10min)'), mfr_kg_s=nullable(number('kg/s')), mfr_g_10min=nullable(number('g/(10min)')),
                           density_derived_mfr_kg_s=nullable(number('kg/s')), density_at_test_temperature_kg_m3=nullable(number('kg/m^3')), evidence=TEXT))
LEDGER_KEYS = ['loaded_mass_kg', 'sample_mass_kg', 'strand_mass_kg', 'segments_mass_kg', 'collected_mass_kg', 'removed_mass_kg', 'extruded_mass_kg', 'mass_residual_kg',
               'heater_input_j', 'host_work_j', 'loaded_enthalpy_j', 'barrel_thermal_j', 'sample_enthalpy_j', 'load_potential_j', 'ambient_export_j',
               'extruded_enthalpy_j', 'removed_enthalpy_j', 'friction_heat_j', 'pressure_work_j', 'energy_residual_j']
LEDGER = obj({k: number('kg' if k.endswith('_kg') else 'J') for k in LEDGER_KEYS})

# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=[c], function_zh=meaning, visibility=visibility, kind=kind, effects=[])


CONFIGURED = obj(dict(configured=BOOL))
cap('plant.configure', PLANT, CONFIGURED, PLANT_EXAMPLE, 'heater', '宿主声明炉体热容/散热、加热器上限与PI控温参数、活塞摩擦、手动速度和切刀半圈时间（工程参数，须显式接受）', 'evaluator', 'configuration')
cap('balance.configure', BALANCE, CONFIGURED, BALANCE_EXAMPLE, 'balance_rs232', '配置外接分析天平代理（分辨率、增益/偏置、稳定时间）；Mflow本身没有内置天平', 'evaluator', 'configuration')
cap('method.configure', METHOD, CONFIGURED, METHOD_EXAMPLE, 'controller', '设定方法：试验载荷、预热时间、最长采集时间、采样与切断间隔、活塞位移窗口、温度容差、自动称量', 'agent', 'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), 'power_switch', '开关整机电源；断电停止加热与切刀、取消进行中的采集，已发生的挤出/热量/切段保留')
cap('thermal.set_temperature', obj(dict(target_k=number('K', *TEMPERATURE_RANGE_K))), obj(dict(target_k=number('K'))), dict(target_k=523.15), 'heater',
    '只写入料筒目标温度（50–450°C）；实际温度由PI控温随宿主step演化')
cap('weight.configure', obj(dict(total_load_kg=number('kg', *LOAD_RANGE_KG))), obj(dict(total_load_kg=number('kg'))), dict(total_load_kg=2.16), 'test_weight',
    '宿主手动在活塞上放/换砝码，设定总试验载荷（含0.325kg活塞，不重复加算）；砝码势能变化计入宿主功', 'evaluator')
cap('sample.load', SAMPLE, obj(dict(present=BOOL, column_height_m=number('m'))), SAMPLE_EXAMPLE, 'barrel',
    '宿主经漏斗把颗粒/粉末装入料筒（活塞须已抽出）；绑定厂家MVR参考记录与显式熔体密度/比热/潜热/幂律/活化能/热历史先验', 'evaluator')
cap('sample.remove', obj({}), obj(dict(removed_mass_kg=number('kg'))), {}, 'barrel', '宿主清筒带走剩余料及其焓；已挤出的挤出丝/切段不受影响', 'evaluator')
cap('piston.withdraw', obj({}), obj(dict(target_m=number('m'))), {}, 'piston', '宿主以有限速度把活塞（连同砝码）提到料筒口上方；不是气动提升选件', 'evaluator')
cap('piston.insert', obj({}), obj(dict(target_m=number('m'))), {}, 'piston', '宿主以有限速度放下活塞，直到压在料柱上（或落到口模上）；之后砝码载荷作用于料柱', 'evaluator')
cap('cutter.cut', obj({}), obj(dict(requested=BOOL)), {}, 'cutter', '切刀转半圈；刀片经过口模下方(π/2)时切断当前挤出丝，形成一个切段（面板“循环”键同此能力）')
CAPS['cutter.cut']['component_ids'] = ['cutter', 'button_cycle']
cap('collection.clear', obj({}), obj(dict(collected_mass_kg=number('kg'))), {}, 'collection_tray', '宿主清走收集区全部切段与挂在口模下的挤出丝（称量记录保留）', 'evaluator')
cap('balance.weigh', obj(dict(segment_id=TEXT)), obj(dict(requested=BOOL)), dict(segment_id='mflow_01:acq:1:segment:2'), 'balance_rs232',
    '请求外接分析天平称量一个切段；经过稳定时间后按分辨率/标定给出观测质量')
cap('calibration.encoder', CAL_ENCODER, obj(dict(scale=number('1'), offset_m=number('m'))), dict(observed_low_m=0.010, observed_high_m=0.060, reference_low_m=0.010, reference_high_m=0.060),
    'displacement_sensor', '用独立长度标准的两点观测修正位移读数（只用宿主给的观测/参考值，不读过程真值）', 'evaluator')
cap('calibration.balance', CAL_BALANCE, obj(dict(scale=number('1'), offset_kg=number('kg'))), dict(observed_low_kg=0.0, observed_high_kg=0.001, reference_low_kg=0.0, reference_high_kg=0.001),
    'balance_rs232', '用标准砝码两点观测修正外接天平读数', 'evaluator')
cap('acquisition.start', obj({}), obj(dict(operation_id=TEXT)), {}, 'button_start',
    '开始采集：核对通电、方法、试样已装且活塞压在料柱上、载荷与方法一致、预热已满、炉温在容差内；先切一次清除预热期挤出物，再按间隔切段并记录位移/时间/温度')
cap('acquisition.stop', obj({}), obj(dict(phase=PHASE, elapsed_s=number('s'))), {}, 'button_stop', '只终止记录与后续自动切断；已挤出物、切段和热状态保留，活塞继续在载荷下下降')
cap('acquisition.result', obj({}), RESULT, {}, 'controller', '返回按观测（量化位移/时间、外部称量）计算的MVR/MFR、切段表与有效性；所有角色都隐去所选材料身份', 'agent', 'query')
cap('analysis.flow', ANALYSIS, ANALYSIS_OUTPUT, dict(start_position_m=0.050, end_position_m=0.020, start_time_s=0.0, end_time_s=60.0, area_prior_m2=STANDARD_AREA_M2,
                                                    weighed_segments=[dict(mass_kg=0.0002, duration_s=10.0)], melt_density_prior_kg_m3=None),
    'controller', 'ISO 1133纯函数分析：MVR=A·Δx/Δt（A为独立面积先验），MFR=Σm/Σt，按需给出密度换算；不读取过程真值', 'agent', 'analysis')
cap('ledger.read', obj({}), LEDGER, {}, 'controller', '受限质量/能量账本（装料、挤出、清筒、热输入、散热、摩擦、压力功、势能）', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(current_sample=nullable(TEXT), operation_source=nullable(TEXT))), {}, 'controller', '受限：当前试样与最近采集所用参考记录的来源', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=0.2), 'controller', '唯一宿主时间推进（纳秒整数时钟，余数累计）', 'evaluator', 'simulation')
UNSUPPORTED = {
    'hardware.connect': '无真实Ethernet/RS-232/USB通信或testXpert连接',
    'robot.load': '无机器人装料、压实与清筒验证',
    'pneumatic.lift': '未选气动砝码提升单元1128621',
    'pneumatic.load_selector': '未选带选重器的气动提升单元1128624',
    'die.plug': '未选口模塞1128618（高MFR防预流）',
    'die.change': '未建模更换口模；固定标准口模312342（手腕一转解锁口模的动作不在本资产能力内）',
    'method.astm_c_half_die': '未选半口模325554（ASTM D1238方法C）',
    'material.fluorinated_or_pvc': '所选料筒1128612与活塞1128614为非含氟材料用；含氟材料需耐酸料筒1128613/活塞1128615，PVC需口模092326（ASTM D3364），均未选。口模312342本身为烧结耐磨耐酸件、厂家列为含氟与非含氟材料通用',
    'cleaning.pneumatic_function': '未选清洗功能1128622',
    'preload.function': '未选预热期附加预载1128623',
    'piston.preheat_hold': '未选预热位活塞保持装置1128627（可在预热期完全卸载熔体、避免提前流出）；本资产预热期由宿主只放活塞0.325kg',
    'collection.separating_pane': '未选收集单个挤出物的分隔板1128619',
    'thermal.recool': '未选压缩空气回冷单元090173（厂家：平均缩短约50%冷却时间）；本资产只有自然冷却',
    'accuracy.certification': '没有ISO 1133-2温度准确度认证、天平计量证书或厂家准确度实验',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, 'controller', v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


slot('clock.time_s', number('s'), 's', 'derived', 'controller', '宿主时钟；read不推进')
for k, c, m in [('plant.configured', 'heater', '工程过程参数已声明'), ('method.configured', 'controller', '方法已设定'), ('balance.configured', 'balance_rs232', '外接天平代理已配置'),
                ('sample.present', 'barrel', '料筒内有试样'), ('power.enabled', 'power_switch', '整机通电'), ('piston.contact', 'piston', '活塞压在料柱上（载荷作用于试样）'),
                ('piston.moving', 'piston', '宿主手动升降进行中'), ('cutter.running', 'cutter', '切刀正在转半圈'), ('balance.busy', 'balance_rs232', '外接天平正在稳定/称量'),
                ('encoder.in_range', 'displacement_sensor', '活塞在位移传感器量程内（约0–107mm）且通电')]:
    slot(k, BOOL, '1', 'derived', c, m)
slot('thermal.target_k', number('K'), 'K', 'setpoint', 'heater', '料筒目标温度，只是目标')
slot('thermal.indicated_k', nullable(number('K')), 'K', 'sensor', 'heater', '料筒温度显示（0.01K量化）；断电为null；未按ISO 1133-2认证')
slot('heater.output_fraction', number('1', 0.0, 1.0), '1', 'derived', 'heater', 'PI控温输出占加热器上限的比例', 'evaluator')
slot('weight.total_load_kg', nullable(number('kg')), 'kg', 'setpoint', 'test_weight', '当前放在活塞上的总载荷（含0.325kg活塞）；未配置为null')
slot('method.test_load_kg', nullable(number('kg')), 'kg', 'setpoint', 'controller', '方法声明的试验载荷，采集开始时须与实际载荷一致')
slot('preheat.elapsed_s', nullable(number('s')), 's', 'derived', 'barrel', '装料后经过的时间（预热计时）；无试样为null')
slot('piston.position_m', number('m', 0.0, PISTON_TRAVEL_M), 'm', 'scene', 'piston', '活塞底面到口模上表面的精确距离（场景关节）；agent须通过位移传感器读数观测', 'evaluator',
     '资产坐标+Z；0=活塞底落在口模上表面，0.180m=抽出到料筒口。')
slot('piston.target_m', number('m', 0.0, PISTON_TRAVEL_M), 'm', 'setpoint', 'piston', '宿主最近一次手动升降命令的目标位置（抽出=0.180m，插入=当时料柱高度）；不随挤出更新', 'evaluator')
slot('sensor_arm.angle_rad', number('rad'), 'rad', 'scene', 'sensor_arm', '位移传感器转臂关节角（0=活塞落在口模上时；滚轮顶住活塞凸环下表面）', 'evaluator',
     '关节绕水平轴(-√½,-√½,0)；仰角=asin((位置-0.050)/0.140)，超过约0.107m停在上限位。')
slot('cutter.angle_rad', number('rad', 0.0, CUTTER_TRAVEL_RAD), 'rad', 'scene', 'cutter', '切刀刀片转角；0与π为两侧停位，经过π/2时切断挤出丝', 'evaluator',
     '绕竖直+Z轴；交替半圈0→π→0。')
slot('power.angle_rad', number('rad'), 'rad', 'scene', 'power_switch', '后置电源摇臂开关姿态：0关、0.28rad开', 'evaluator')
slot('extrudate.length_m', number('m'), 'm', 'scene', 'extrudate_strand', '挂在口模下尚未切断的挤出丝长度（体积/口模截面，忽略离模膨胀，仅供可视）', 'evaluator',
     '沿−Z自口模出口（z=0.228m）向下；可视上限0.150m。')
slot('extrudate.last_cut_length_m', number('m'), 'm', 'scene', 'extrudate_segment', '最近一个切段的长度（仅供可视，清走收集区后为0）', 'evaluator')
slot('encoder.position_m', nullable(number('m')), 'm', 'sensor', 'displacement_sensor', '位移传感器读数：活塞位置按0.003mm量化并经两点标定；超量程或断电为null')
slot('measurement.live_mvr_m3_s', nullable(number('m^3/s')), 'm^3/s', 'sensor', 'controller', '采集中按最近约4s的量化位移×ISO平均截面0.711cm²估算的实时MVR（屏幕Live MVR）；非采集为null',
     reference='熔体在料筒测试温度下的体积流率（不是23°C固体体积）。')
slot('measurement.mvr_m3_s', nullable(number('m^3/s')), 'm^3/s', 'sensor', 'controller', '最近一次完成采集的方法B MVR（位移窗口内的量化观测）',
     reference='熔体在料筒测试温度下的体积流率；×6e8=cm³/10min。')
slot('measurement.mfr_kg_s', nullable(number('kg/s')), 'kg/s', 'sensor', 'controller', '最近一次采集的方法A MFR（已称量测量切段Σm/Σt）；未称量为null',
     reference='质量流率；×6e5=g/10min。')
slot('measurement.validity', VALIDITY, '1', 'derived', 'controller', '最近一次采集的有效性')
slot('acquisition.phase', PHASE, '1', 'derived', 'controller', '采集状态；停止记录不等于停止挤出')
slot('acquisition.elapsed_s', number('s'), 's', 'derived', 'controller', '当前/最近一次采集的经过时间')
slot('acquisition.points', INT, '1', 'derived', 'controller', '当前/最近一次采集的记录点数')
slot('segments.count', INT, '1', 'derived', 'collection_tray', '收集区内切段数（含清除预热挤出物的首段）')
slot('segments.unweighed', INT, '1', 'derived', 'collection_tray', '尚未称量的测量切段数')
for k, u, c, m, ref in [('truth.barrel_temperature_k', 'K', 'heater', '料筒/炉体集中热节点真温', None),
                        ('truth.sample_temperature_k', 'K', 'barrel', '由焓反演的试样平均温度', None),
                        ('truth.melt_fraction', '1', 'barrel', '熔融分数（熔程内线性）', None),
                        ('truth.pellet_fraction', '1', 'barrel', '尚未熔实的颗粒质量份额（只减不增；按堆积密度占体积）', None),
                        ('truth.sample_mass_kg', 'kg', 'barrel', '料筒内剩余试样质量', None),
                        ('truth.flow_m3_s', 'm^3/s', 'die', '口模真实体积流率（显式流动代理，不作独立测量）', '熔体在试样温度下的体积流率。'),
                        ('truth.thermal_history_s', 's', 'barrel', '试样高于熔融起点的累计时间', None),
                        ('truth.strand_mass_kg', 'kg', 'extrudate_strand', '挂在口模下的挤出丝质量', None),
                        ('truth.heater_power_w', 'W', 'heater', '加热器瞬时功率', None)]:
    slot(k, number(u), u, 'process_truth', c, m, 'evaluator', ref)


def analyze_flow(start_position_m, end_position_m, start_time_s, end_time_s, area_prior_m2, weighed_segments, melt_density_prior_kg_m3):
    """ISO 1133 arithmetic on external observations only (pure function)."""
    if end_time_s <= start_time_s or start_position_m <= end_position_m:
        raise ActionRejected('OBSERVATION_DOMAIN')
    q = (start_position_m - end_position_m) * area_prior_m2 / (end_time_s - start_time_s)
    total_t = sum(r['duration_s'] for r in weighed_segments)
    mf = sum(r['mass_kg'] for r in weighed_segments) / total_t if weighed_segments else None
    return dict(mvr_m3_s=q, mvr_cm3_10min=q * 6e8, mfr_kg_s=mf, mfr_g_10min=None if mf is None else mf * 6e5,
                density_derived_mfr_kg_s=None if melt_density_prior_kg_m3 is None else q * melt_density_prior_kg_m3,
                density_at_test_temperature_kg_m3=None if mf is None else mf / q,
                evidence='ISO1133 method B: area_prior*dx/dt from supplied quantised positions/times; method A: sum(m)/sum(t) of supplied weighings; '
                         'density conversion only with a supplied melt density at test temperature')


HISTORY_LN_LIMIT = math.log(1e3)                 # thermal-history multiplier bounded to 1e-3 .. 1e3 (numerical domain)
MAX_ROWS = 200000                                # rows per acquisition (duration / sample interval + 1 must stay below)
MAX_SEGMENTS = 10000                             # segments per acquisition (RESULT schema maxItems); manual cuts share the budget


class ZwickMflowRuntime:
    """One Mflow instance; the host clock is the only time source (``step``)."""

    def __init__(self, instance_id='mflow_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.method = None
        self.balance = None
        self.power = False
        self.target_k = 296.15
        self.T_b = 296.15
        self.integral_w = 0.0
        self.heater_w = 0.0
        self.load_kg = None
        # piston: position = height of the piston bottom above the die top
        self.x = PISTON_TRAVEL_M
        self.x_target = PISTON_TRAVEL_M
        self.contact = False
        self._move = None          # dict(start_ns, x0, direction, speed, insert)
        # charge
        self.sample = None
        self.reference = None
        self.reference_cell = None
        self.source = None
        self.m_s = 0.0
        self.H_s = 0.0
        self.m_loaded = 0.0
        self.pellet = 0.0
        self.t_hist = 0.0
        self.load_ns = None
        self.q_true = 0.0
        # extrudate / cutter / balance
        self.strand_kg = 0.0
        self.strand_m3 = 0.0
        self.segments = []         # segments currently lying in the collection area
        self._segment_counter = 0
        self.last_cut_length = 0.0
        self.cutter_angle = 0.0
        self._cut = None           # dict(start_ns, mid_ns, end_ns, a0, a1, severed, auto)
        self._weigh_queue = []
        self._weighing = None      # dict(segment_id, end_ns)
        self.encoder_cal = (1.0, 0.0)
        self.balance_cal = (1.0, 0.0)
        # acquisitions: each keeps its own method snapshot, rows and segments, so later clears / method changes /
        # weighings of other segments never rewrite a finished record
        self.phase = 'idle'
        self.operation_id = None
        self.elapsed = 0.0
        self.rows = []
        self._counter = 0
        self.terminal_reason = None
        self._operation_source = None
        self._acq = None
        self._acqs = {}
        self.validity = 'not_completed'
        self.flow_result = None
        self.last_mvr = None
        self.last_mfr = None
        self.records = {}
        self._principals = {}
        self._requests = {}
        self._owners = {}
        self._cancel_operations = {}
        self.book = {k: 0.0 for k in ['loaded_mass_kg', 'collected_mass_kg', 'removed_mass_kg', 'heater_input_j', 'host_work_j', 'loaded_enthalpy_j',
                                      'ambient_export_j', 'extruded_enthalpy_j', 'removed_enthalpy_j', 'friction_heat_j', 'pressure_work_j']}

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
        return ZwickMflowSession(self, name)

    def _ambient(self):
        return self.plant['ambient_k'] if self.plant else 296.15

    def _phi_of_T(self, T):
        """Latent fraction used by the equilibrium enthalpy h(T) (a zero-width range melts completely at T >= T_lo)."""
        s = self.sample
        lo, hi = s['melting_low_k'], s['melting_high_k']
        if hi <= lo:
            return 1.0 if T >= lo else 0.0
        return min(1.0, max(0.0, (T - lo) / (hi - lo)))

    def _h_of_T(self, T):
        s = self.sample
        return s['specific_heat_j_kg_k'] * (T - self._ambient()) + s['latent_heat_j_kg'] * self._phi_of_T(T)

    def _phase_of_h(self, h):
        """Exact inverse of the piecewise-linear specific enthalpy -> (temperature, melt fraction).

        The melt fraction comes from the enthalpy, so a zero-width melting range still needs the whole latent heat."""
        s = self.sample
        cp, L, lo, hi, Ta = s['specific_heat_j_kg_k'], s['latent_heat_j_kg'], s['melting_low_k'], s['melting_high_k'], self._ambient()
        h_lo = cp * (lo - Ta)
        h_hi = cp * (hi - Ta) + L
        if h <= h_lo:
            return Ta + h / cp, 0.0
        if h >= h_hi:
            return Ta + (h - L) / cp, 1.0
        frac = (h - h_lo) / (h_hi - h_lo)
        return lo + frac * (hi - lo), frac

    def _sample_state(self):
        if not self.sample or self.m_s <= 0:
            return self._ambient(), 0.0
        return self._phase_of_h(self.H_s / self.m_s)

    def _sample_T(self):
        return self._sample_state()[0]

    def _phi(self):
        return self._sample_state()[1]

    def _column_height(self):
        if not self.sample or self.m_s <= 0:
            return 0.0
        s = self.sample
        return self.m_s * (self.pellet / s['bulk_density_kg_m3'] + (1.0 - self.pellet) / s['melt_density_kg_m3']) / STANDARD_AREA_M2

    def _load_mass(self):
        return PISTON_LOAD_KG if self.load_kg is None else self.load_kg

    def _flow(self, T, phi):
        """True melt volume flow through the die (m^3/s) for the loaded, contacting piston."""
        if not (self.sample and self.contact and self.m_s > 0):
            return 0.0
        s, ref = self.sample, self.reference_cell
        force = self._load_mass() * GRAVITY - self.plant['piston_friction_n']
        if force <= 0:
            return 0.0
        n = s['power_law_index']
        history = max(-HISTORY_LN_LIMIT, min(HISTORY_LN_LIMIT, s['history_rate_per_s'] * self.t_hist))
        return (ref['mvr_m3_s'] * (force / (s['reference_load_kg'] * GRAVITY)) ** (1.0 / n)
                * math.exp(-(s['activation_energy_j_mol'] / (n * GAS_CONSTANT)) * (1.0 / T - 1.0 / s['reference_temperature_k']))
                * math.exp(history) * phi ** 3)

    def _encoder(self):
        if not self.power or not (SENSOR_RANGE_M[0] <= self.x <= SENSOR_RANGE_M[1]):
            return None
        scale, offset = self.encoder_cal
        return quantize(scale * self.x + offset, ENCODER_RESOLUTION_M)

    def _indicated_T(self):
        return quantize(self.T_b, TEMPERATURE_DISPLAY_RESOLUTION_K) if self.power else None

    def _live_mvr(self):
        if self.phase != 'running' or not self.power:
            return None
        pts = [r for r in self.rows if r['encoder_position_m'] is not None]
        if len(pts) < 2:
            return None
        last = pts[-1]
        first = next((r for r in pts if last['time_s'] - r['time_s'] <= LIVE_MVR_WINDOW_S + 1e-9), None)
        if first is None or last['time_s'] <= first['time_s']:
            return None
        return max(0.0, (first['encoder_position_m'] - last['encoder_position_m']) * STANDARD_AREA_M2 / (last['time_s'] - first['time_s']))

    def _obs_time(self, t_ns, acq=None):
        return quantize((t_ns - acq['start_ns']) / 1e9, TIME_RESOLUTION_S) if acq else quantize(t_ns / 1e9, TIME_RESOLUTION_S)

    def read(self, visibility='agent'):
        T_s, phi = self._sample_state()
        d = {'clock.time_s': self.time_s, 'plant.configured': self.plant is not None, 'method.configured': self.method is not None,
             'balance.configured': self.balance is not None, 'sample.present': self.sample is not None, 'power.enabled': self.power,
             'piston.contact': self.contact, 'piston.moving': self._move is not None, 'cutter.running': self._cut is not None,
             'balance.busy': self._weighing is not None, 'encoder.in_range': self._encoder() is not None,
             'thermal.target_k': self.target_k, 'thermal.indicated_k': self._indicated_T(),
             'heater.output_fraction': self.heater_w / self.plant['heater_max_w'] if self.plant else 0.0,
             'weight.total_load_kg': self.load_kg, 'method.test_load_kg': self.method['test_load_kg'] if self.method else None,
             'preheat.elapsed_s': None if self.load_ns is None else (self.time_ns - self.load_ns) / 1e9,
             'piston.position_m': self.x, 'piston.target_m': self.x_target, 'sensor_arm.angle_rad': sensor_arm_angle(self.x),
             'cutter.angle_rad': self.cutter_angle, 'power.angle_rad': POWER_SWITCH_ON_RAD if self.power else 0.0,
             'extrudate.length_m': self.strand_m3 / DIE_AREA_M2, 'extrudate.last_cut_length_m': self.last_cut_length,
             'encoder.position_m': self._encoder(), 'measurement.live_mvr_m3_s': self._live_mvr(),
             'measurement.mvr_m3_s': self.last_mvr, 'measurement.mfr_kg_s': self.last_mfr, 'measurement.validity': self.validity,
             'acquisition.phase': self.phase, 'acquisition.elapsed_s': self.elapsed, 'acquisition.points': len(self.rows),
             'segments.count': len(self.segments), 'segments.unweighed': sum(1 for g in self.segments if g['measurement_segment'] and g['status'] != 'weighed'),
             'truth.barrel_temperature_k': self.T_b, 'truth.sample_temperature_k': T_s, 'truth.melt_fraction': phi,
             'truth.pellet_fraction': self.pellet, 'truth.sample_mass_kg': self.m_s, 'truth.flow_m3_s': self.q_true,
             'truth.thermal_history_s': self.t_hist, 'truth.strand_mass_kg': self.strand_kg, 'truth.heater_power_w': self.heater_w}
        return {k: copy.deepcopy(v) for k, v in d.items() if visibility != 'agent' or SLOTS[k]['visibility'] == 'agent'}

    def ledger(self):
        b = copy.deepcopy(self.book)
        seg_mass = sum(g['true_mass_kg'] for g in self.segments)
        b.update(sample_mass_kg=self.m_s, strand_mass_kg=self.strand_kg, segments_mass_kg=seg_mass,
                 extruded_mass_kg=self.strand_kg + seg_mass + self.book['collected_mass_kg'],
                 barrel_thermal_j=(self.plant['barrel_heat_capacity_j_k'] * (self.T_b - self._ambient())) if self.plant else 0.0,
                 sample_enthalpy_j=self.H_s, load_potential_j=self._load_mass() * GRAVITY * self.x)
        b['mass_residual_kg'] = b['loaded_mass_kg'] - self.m_s - self.strand_kg - seg_mass - b['collected_mass_kg'] - b['removed_mass_kg']
        b['energy_residual_j'] = (b['heater_input_j'] + b['host_work_j'] + b['loaded_enthalpy_j'] - b['barrel_thermal_j'] - b['sample_enthalpy_j']
                                  - (b['load_potential_j'] - self._potential0) - b['ambient_export_j'] - b['extruded_enthalpy_j'] - b['removed_enthalpy_j'])
        return b

    _potential0 = PISTON_LOAD_KG * GRAVITY * PISTON_TRAVEL_M

    # ------------------------------------------------------------ acquisition bookkeeping
    def _record(self, t_ns):
        enc = self._encoder()
        quality = 'power_off' if not self.power else 'ok' if enc is not None else 'encoder_out_of_range'
        self.rows.append(dict(point_index=len(self.rows), time_s=self._obs_time(t_ns, self._acq), encoder_position_m=enc, temperature_k=self._indicated_T(), quality=quality))

    @staticmethod
    def _public_segments(segments):
        return [dict(segment_id=g['segment_id'], start_time_s=g['start_obs_s'], end_time_s=g['end_obs_s'],
                     duration_s=None if g['start_obs_s'] is None else quantize(g['end_obs_s'] - g['start_obs_s'], TIME_RESOLUTION_S),
                     observed_mass_kg=g['observed_mass_kg'], status=g['status'], measurement_segment=g['measurement_segment']) for g in segments]

    @staticmethod
    def _compute_flow(acq):
        """Method B from the acquisition's own rows and window; method A from its own weighed measurement segments."""
        m = acq['method']
        pts = [r for r in acq['rows'] if r['encoder_position_m'] is not None and r['quality'] == 'ok']
        inside = [r for r in pts if m['window_lower_m'] - 1e-12 <= r['encoder_position_m'] <= m['window_upper_m'] + 1e-12]
        mvr = None
        window = None
        if len(inside) >= 2 and inside[0]['encoder_position_m'] > inside[-1]['encoder_position_m'] and inside[-1]['time_s'] > inside[0]['time_s']:
            a, b = inside[0], inside[-1]
            mvr = (a['encoder_position_m'] - b['encoder_position_m']) * STANDARD_AREA_M2 / (b['time_s'] - a['time_s'])
            window = ([a['encoder_position_m'], b['encoder_position_m']], [a['time_s'], b['time_s']])
        weighed = [g for g in acq['segments'] if g['measurement_segment'] and g['status'] == 'weighed']
        mfr = None
        if weighed:
            mfr = sum(g['observed_mass_kg'] for g in weighed) / sum(quantize(g['end_obs_s'] - g['start_obs_s'], TIME_RESOLUTION_S) for g in weighed)
        if mvr is None and mfr is None:
            return None
        return dict(mvr_m3_s=mvr, mvr_cm3_10min=None if mvr is None else mvr * 6e8, mfr_kg_s=mfr, mfr_g_10min=None if mfr is None else mfr * 6e5,
                    density_at_test_temperature_kg_m3=None if (mvr is None or mfr is None or mvr <= 0) else mfr / mvr,
                    window_positions_m=None if window is None else window[0], window_times_s=None if window is None else window[1], weighed_segments=len(weighed),
                    evidence='method B: ISO mean area 0.711 cm^2 x quantised encoder displacement / quantised time inside the window of the method used for this run; '
                             'method A: externally weighed measurement segments of this run / their quantised cut interval')

    def _result_of(self, acq):
        return dict(operation_id=acq['op'], phase=acq['phase'], elapsed_s=acq['elapsed'], test_temperature_k=acq['target_k'], test_load_kg=acq['load_kg'],
                    rows=copy.deepcopy(acq['rows']), segments=self._public_segments(acq['segments']),
                    flow=copy.deepcopy(acq['flow']) if acq['phase'] != 'running' else None, validity=acq['validity'], terminal_reason=acq['terminal_reason'],
                    template_source=acq['source'],
                    evidence='synthetic_forward_model; quantised encoder/time/temperature observations and external weighings; manufacturer MVR scalar '
                             'transferred with explicit rheology/thermal/density priors; not native Mflow data')

    def _result(self):
        if self._acq is None:
            return dict(operation_id=None, phase='idle', elapsed_s=0.0, test_temperature_k=None, test_load_kg=None, rows=[], segments=[], flow=None,
                        validity='not_completed', terminal_reason=None, template_source=None, evidence='no_measurement')
        if self._acq['phase'] == 'running':
            return self._result_of(self._acq)
        return copy.deepcopy(self.records[self._acq['op']])

    def _finish(self, phase, reason):
        acq = self._acq
        method = acq['method']
        if phase == 'cancelled':
            validity = 'cancelled'
        elif reason == 'SAMPLE_EXHAUSTED':
            validity = 'sample_exhausted'
        elif not acq['window_traversed']:
            validity = 'window_not_traversed'
        elif acq['temperature_excursion']:
            validity = 'temperature_out_of_tolerance'
        elif method['preheat_time_s'] < 300.0:
            validity = 'preheat_below_iso_minimum'
        else:
            validity = 'valid'
        acq.update(phase=phase, terminal_reason=reason, validity=validity, end_ns=self.time_ns, elapsed=self.elapsed)
        acq['flow'] = self._compute_flow(acq)
        self.phase, self.terminal_reason, self.validity, self.flow_result = phase, reason, validity, acq['flow']
        ok = acq['flow'] is not None and validity == 'valid'
        self.last_mvr = acq['flow']['mvr_m3_s'] if ok else None
        self.last_mfr = acq['flow']['mfr_kg_s'] if ok else None
        self.records[acq['op']] = self._result_of(acq)

    def _refresh_record(self, op):
        """A weighing that finishes after its run updates only that run's record (masses only, same method snapshot)."""
        acq = self._acqs.get(op)
        if acq is None or acq['phase'] == 'running':
            return
        acq['flow'] = self._compute_flow(acq)
        self.records[op] = self._result_of(acq)
        if op == self.operation_id:
            self.flow_result = acq['flow']
            if acq['validity'] == 'valid' and acq['flow'] is not None:
                self.last_mfr = acq['flow']['mfr_kg_s']

    # ------------------------------------------------------------ cutter and balance
    def _start_cut(self, auto):
        """Blade moves from where it stands to the far rest (0 or pi); the strand is severed where the path crosses pi/2."""
        a0 = self.cutter_angle
        a1 = CUTTER_TRAVEL_RAD if a0 < CUTTER_TRAVEL_RAD / 2 else 0.0
        duration = max(1, round(self.plant['cutter_half_turn_s'] * 1e9 * abs(a1 - a0) / CUTTER_TRAVEL_RAD))
        frac = (CUTTER_TRAVEL_RAD / 2 - a0) / (a1 - a0)
        self._cut = dict(start_ns=self.time_ns, mid_ns=self.time_ns + round(frac * duration), end_ns=self.time_ns + duration, a0=a0, a1=a1, severed=False, auto=auto)

    def _sever(self, t_ns):
        """Blade crosses pi/2: the hanging strand becomes a segment."""
        acq = self._acq if self.phase == 'running' else None
        if acq is not None:
            acq['cuts'] += 1
        op = acq['op'] if acq is not None else 'manual'
        start_obs = acq['last_cut_obs'] if acq is not None else None
        end_obs = self._obs_time(t_ns, acq)
        measurement = acq is not None and acq['cuts'] >= 2
        if self.strand_kg > 0 or acq is not None:
            self._segment_counter += 1
            seg = dict(segment_id=f'{op}:segment:{self._segment_counter}', operation_id=op, true_mass_kg=self.strand_kg, true_volume_m3=self.strand_m3,
                       start_obs_s=start_obs if measurement else None, end_obs_s=end_obs, observed_mass_kg=None, status='unweighed',
                       measurement_segment=measurement)
            self.segments.append(seg)
            if acq is not None:
                acq['segments'].append(seg)
            self.last_cut_length = self.strand_m3 / DIE_AREA_M2
            if measurement and acq['method']['automatic_weighing'] and self.balance is not None:
                self._weigh_queue.append(seg['segment_id'])
                self._start_next_weighing()
        self.strand_kg = 0.0
        self.strand_m3 = 0.0
        if acq is not None:
            acq['last_cut_obs'] = end_obs

    def _start_next_weighing(self):
        if self._weighing is None and self._weigh_queue and self.balance is not None:
            sid = self._weigh_queue.pop(0)
            seg = next(g for g in self.segments if g['segment_id'] == sid)
            seg['status'] = 'weighing'
            self._weighing = dict(segment_id=sid, end_ns=self.time_ns + max(1, round(self.balance['settling_s'] * 1e9)))

    def _complete_weighing(self):
        w = self._weighing
        self._weighing = None
        seg = next((g for g in self.segments if g['segment_id'] == w['segment_id']), None)
        if seg is not None:
            b = self.balance
            scale, offset = self.balance_cal
            raw = quantize(b['gain'] * seg['true_mass_kg'] + b['offset_kg'], b['resolution_kg'])
            seg['observed_mass_kg'] = scale * raw + offset
            seg['status'] = 'weighed'
            self._refresh_record(seg['operation_id'])
        self._start_next_weighing()

    # ------------------------------------------------------------ commands
    def invoke(self, capability_id, **arguments):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id]['input_schema'], arguments)
        k = capability_id
        a = copy.deepcopy(arguments)
        if k in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        if k == 'acquisition.result':
            return self._result()
        if k == 'ledger.read':
            return self.ledger()
        if k == 'provenance.read':
            return dict(current_sample=self.source, operation_source=self._operation_source)
        if k == 'analysis.flow':
            return analyze_flow(**a)
        if k == 'sim.step':
            self.step(a['dt_s'])
            return dict(time_s=self.time_s)
        if self.phase == 'running' and k not in ('acquisition.stop', 'power.set', 'balance.weigh', 'thermal.set_temperature', 'cutter.cut'):
            raise ActionRejected('BUSY')
        if k == 'plant.configure':
            if self.plant is not None:
                raise ActionRejected('PLANT_ALREADY_CONFIGURED')
            if not a['accept_engineering']:
                raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            if a['barrel_heat_capacity_j_k'] / (a['controller_gain_w_k'] + a['barrel_loss_w_k']) < 5 * MAX_SUBSTEP_S:
                raise ActionRejected('THERMAL_TIME_CONSTANT_OUTSIDE_NUMERICAL_DOMAIN')
            self.plant = a
            self.T_b = a['ambient_k']
            self.target_k = max(TEMPERATURE_RANGE_K[0], a['ambient_k'])
            out = dict(configured=True)
        elif k == 'balance.configure':
            if not a['accept_external_balance_proxy']:
                raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            if self._weighing is not None:
                raise ActionRejected('BALANCE_BUSY')
            self.balance = a
            out = dict(configured=True)
        elif k == 'method.configure':
            if a['window_upper_m'] <= a['window_lower_m']:
                raise ActionRejected('INVALID_WINDOW')
            nanos = a['sample_interval_s'] / TIME_RESOLUTION_S
            if abs(nanos - round(nanos)) > 1e-6:
                raise ActionRejected('SAMPLING_NOT_ON_TIME_BASE')
            if a['duration_s'] / a['sample_interval_s'] + 2 > MAX_ROWS:
                raise ActionRejected('TOO_MANY_SAMPLES')
            self.method = a
            out = dict(configured=True)
        elif k == 'power.set':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if not a['enabled']:
                if self.phase == 'running':
                    self._finish('cancelled', 'POWER_OFF')
                if self._cut is not None:
                    self._cut = None       # blade stops where it is; an uncrossed turn does not cut
                self.heater_w = 0.0
                self.integral_w = 0.0
            self.power = a['enabled']
            out = dict(enabled=self.power)
        elif k == 'thermal.set_temperature':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            self.target_k = a['target_k']
            out = dict(target_k=self.target_k)
        elif k == 'weight.configure':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if self._move is not None:
                raise ActionRejected('PISTON_MOVING')
            old = self._load_mass()
            self.load_kg = a['total_load_kg']
            self.book['host_work_j'] += (self._load_mass() - old) * GRAVITY * self.x
            out = dict(total_load_kg=self.load_kg)
        elif k == 'sample.load':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if self.sample is not None:
                raise ActionRejected('SAMPLE_PRESENT')
            if self._move is not None or self.x < PISTON_TRAVEL_M - 1e-12:
                raise ActionRejected('PISTON_NOT_WITHDRAWN')
            if not a['accept_rheology_thermal_density_and_packing_assumptions']:
                raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            if a['melting_high_k'] < a['melting_low_k']:
                raise ActionRejected('INVALID_MELTING_RANGE')
            if a['bulk_density_kg_m3'] > a['melt_density_kg_m3']:
                raise ActionRejected('BULK_DENSITY_EXCEEDS_MELT_DENSITY')
            ref = library().get(a['reference_id'])
            if ref is None:
                raise ActionRejected('UNKNOWN_REFERENCE')
            cell = next((m for m in ref['measurements'] if m.get('mvr_m3_s') is not None
                         and abs(m['reference_temperature_K'] - a['reference_temperature_k']) < 1e-6 and abs(m['reference_load_kg'] - a['reference_load_kg']) < 1e-9), None)
            if cell is None:
                raise ActionRejected('NO_NUMERIC_REFERENCE_AT_CONDITION')
            height = a['mass_kg'] / (a['bulk_density_kg_m3'] * STANDARD_AREA_M2)
            if height > BARREL_LENGTH_M:
                raise ActionRejected('CHARGE_EXCEEDS_BARREL')
            self.sample = a
            self.reference = ref
            self.reference_cell = cell
            self.source = json.dumps(dict(reference_id=a['reference_id'], grade=ref.get('grade'), condition=[cell['reference_temperature_K'], cell['reference_load_kg']],
                                          mvr_m3_s=cell['mvr_m3_s'], source=cell.get('source', {}).get('publication')), sort_keys=True, ensure_ascii=False)
            self.m_s = a['mass_kg']
            self.m_loaded = a['mass_kg']
            self.pellet = 1.0
            self.t_hist = 0.0
            self.H_s = a['mass_kg'] * self._h_of_T(a['initial_temperature_k'])
            self.book['loaded_mass_kg'] += a['mass_kg']
            self.book['loaded_enthalpy_j'] += self.H_s
            self.load_ns = self.time_ns
            out = dict(present=True, column_height_m=height)
        elif k == 'sample.remove':
            if self.sample is None:
                raise ActionRejected('NO_SAMPLE')
            if self._move is not None or self.x < PISTON_TRAVEL_M - 1e-12:
                raise ActionRejected('PISTON_NOT_WITHDRAWN')
            removed = self.m_s
            self.book['removed_mass_kg'] += removed
            self.book['removed_enthalpy_j'] += self.H_s
            self.m_s = 0.0
            self.H_s = 0.0
            self.sample = self.reference = self.reference_cell = self.source = None
            self.pellet = 0.0
            self.t_hist = 0.0
            self.load_ns = None
            out = dict(removed_mass_kg=removed)
        elif k in ('piston.withdraw', 'piston.insert'):
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if self._move is not None:
                raise ActionRejected('PISTON_MOVING')
            if k == 'piston.withdraw':
                target = PISTON_TRAVEL_M
                self.contact = False
            else:
                if self.contact:
                    raise ActionRejected('ALREADY_IN_CONTACT')
                target = max(self._column_height(), 0.0)
            self.x_target = target
            if abs(target - self.x) > 1e-12:
                self._move = dict(start_ns=self.time_ns, x0=self.x, direction=1.0 if target > self.x else -1.0, speed=self.plant['manual_piston_speed_m_s'], insert=k == 'piston.insert')
            elif k == 'piston.insert':
                self.contact = True
            out = dict(target_m=target)
        elif k == 'cutter.cut':
            if not self.power:
                raise ActionRejected('POWER_OFF')
            if self._cut is not None:
                raise ActionRejected('CUTTER_BUSY')
            if self.phase == 'running':
                if self._acq['manual_cuts'] >= self._acq['manual_cut_budget']:
                    raise ActionRejected('TOO_MANY_SEGMENTS')
                self._acq['manual_cuts'] += 1
            self._start_cut(auto=False)
            out = dict(requested=True)
        elif k == 'collection.clear':
            if self._weighing is not None:
                raise ActionRejected('BALANCE_BUSY')
            collected = self.strand_kg + sum(g['true_mass_kg'] for g in self.segments)
            self.book['collected_mass_kg'] += collected
            self.strand_kg = self.strand_m3 = 0.0
            self.segments = []
            self._weigh_queue = []
            self.last_cut_length = 0.0
            out = dict(collected_mass_kg=collected)
        elif k == 'balance.weigh':
            if self.balance is None:
                raise ActionRejected('BALANCE_REQUIRED')
            seg = next((g for g in self.segments if g['segment_id'] == a['segment_id']), None)
            if seg is None:
                raise ActionRejected('UNKNOWN_SEGMENT')
            if seg['status'] != 'unweighed' or a['segment_id'] in self._weigh_queue:
                raise ActionRejected('SEGMENT_ALREADY_WEIGHED_OR_QUEUED')
            self._weigh_queue.append(a['segment_id'])
            self._start_next_weighing()
            out = dict(requested=True)
        elif k == 'calibration.encoder':
            if a['observed_high_m'] - a['observed_low_m'] <= 1e-6 or a['reference_high_m'] - a['reference_low_m'] <= 1e-6:
                raise ActionRejected('DEGENERATE_CALIBRATION')
            # observed readings were taken with the current correction: compose the new correction on top
            s_old, o_old = self.encoder_cal
            gain = (a['reference_high_m'] - a['reference_low_m']) / (a['observed_high_m'] - a['observed_low_m'])
            off = a['reference_low_m'] - gain * a['observed_low_m']
            self.encoder_cal = (gain * s_old, gain * o_old + off)
            out = dict(scale=self.encoder_cal[0], offset_m=self.encoder_cal[1])
        elif k == 'calibration.balance':
            if a['observed_high_kg'] - a['observed_low_kg'] <= 1e-9 or a['reference_high_kg'] - a['reference_low_kg'] <= 1e-9:
                raise ActionRejected('DEGENERATE_CALIBRATION')
            s_old, o_old = self.balance_cal
            gain = (a['reference_high_kg'] - a['reference_low_kg']) / (a['observed_high_kg'] - a['observed_low_kg'])
            off = a['reference_low_kg'] - gain * a['observed_low_kg']
            self.balance_cal = (gain * s_old, gain * o_old + off)
            out = dict(scale=self.balance_cal[0], offset_kg=self.balance_cal[1])
        elif k == 'acquisition.start':
            if not self.power:
                raise ActionRejected('POWER_OFF')
            if self.plant is None or self.method is None:
                raise ActionRejected('CONFIGURATION_REQUIRED')
            if self.sample is None:
                raise ActionRejected('NO_SAMPLE')
            if not self.contact or self._move is not None:
                raise ActionRejected('PISTON_NOT_ON_CHARGE')
            if self.load_kg is None or abs(self.load_kg - self.method['test_load_kg']) > 1e-9:
                raise ActionRejected('LOAD_MISMATCH')
            if (self.time_ns - self.load_ns) / 1e9 < self.method['preheat_time_s'] - 1e-9:
                raise ActionRejected('PREHEAT_NOT_COMPLETE')
            if abs(self._indicated_T() - self.target_k) > self.method['temperature_tolerance_k'] + 1e-9:
                raise ActionRejected('TEMPERATURE_NOT_STABLE')
            if self._cut is not None:
                raise ActionRejected('CUTTER_BUSY')
            ci = self.method['cut_interval_s']
            if ci is not None and ci < 1.5 * self.plant['cutter_half_turn_s']:
                raise ActionRejected('CUT_INTERVAL_TOO_SHORT')
            self._counter += 1
            self.operation_id = f'{self.instance_id}:acq:{self._counter}'
            self.phase = 'running'
            self.elapsed = 0.0
            self.terminal_reason = None
            self.validity = 'not_completed'
            self.flow_result = None
            self._operation_source = self.source
            period = round(self.method['sample_interval_s'] * 1e9)
            self._acq = dict(op=self.operation_id, method=copy.deepcopy(self.method), rows=[], segments=[], phase='running', validity='not_completed',
                             terminal_reason=None, flow=None, elapsed=0.0, source=self.source, start_ns=self.time_ns,
                             end_limit_ns=self.time_ns + round(self.method['duration_s'] * 1e9), period_ns=period,
                             next_sample_ns=self.time_ns + period, next_cut_ns=None if ci is None else self.time_ns + round(ci * 1e9),
                             cuts=0, last_cut_obs=None, window_traversed=False, temperature_excursion=False, target_k=self.target_k, load_kg=self.load_kg, end_ns=None)
            auto_cuts = 0 if ci is None else int(self.method['duration_s'] // ci) + 2
            self._acq.update(manual_cut_budget=MAX_SEGMENTS - auto_cuts, manual_cuts=0)
            self._acqs[self.operation_id] = self._acq
            self.rows = self._acq['rows']
            self._record(self.time_ns)
            if ci is not None:
                self._start_cut(auto=True)     # trim cut: removes what flowed during the preheat
            out = dict(operation_id=self.operation_id)
        elif k == 'acquisition.stop':
            if self.phase != 'running':
                raise ActionRejected('NOT_RUNNING')
            self._finish('cancelled', 'USER_CANCEL')
            out = dict(phase=self.phase, elapsed_s=self.elapsed)
        else:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision += 1
        return copy.deepcopy(out)

    # ------------------------------------------------------------ time integration
    def _next_events(self, end):
        ev = [end, self.time_ns + round(MAX_SUBSTEP_S * 1e9)]
        if self._move is not None:
            m = self._move
            dist = abs(self.x_target - m['x0'])
            ev.append(m['start_ns'] + max(1, math.ceil(dist / m['speed'] * 1e9)))
        if self._cut is not None:
            ev += [self._cut['mid_ns'], self._cut['end_ns']] if not self._cut['severed'] else [self._cut['end_ns']]
        if self._weighing is not None:
            ev.append(self._weighing['end_ns'])
        if self.phase == 'running':
            ev += [self._acq['next_sample_ns'], self._acq['end_limit_ns']]
            if self._acq['next_cut_ns'] is not None:
                ev.append(self._acq['next_cut_ns'])
        return min(t for t in ev if t > self.time_ns)

    def _integrate(self, dt):
        """Explicit conservative update over one sub-step (all energy terms booked with the same values)."""
        p = self.plant
        Ta = p['ambient_k']
        # heater: PI with conditional integration (anti-windup)
        if self.power:
            e = self.target_k - self.T_b
            u = p['controller_gain_w_k'] * e + self.integral_w
            P = min(max(u, 0.0), p['heater_max_w'])
            if 0.0 < u < p['heater_max_w'] or (u >= p['heater_max_w'] and e < 0) or (u <= 0 and e > 0):
                self.integral_w = min(max(self.integral_w + p['controller_gain_w_k'] / p['controller_integral_s'] * e * dt, 0.0), p['heater_max_w'])
        else:
            P = 0.0
        self.heater_w = P
        loss = p['barrel_loss_w_k'] * (self.T_b - Ta)
        friction = 0.0
        pressure_work = 0.0
        self.q_true = 0.0
        exchange = 0.0
        if self.sample is not None and self.m_s > 0:
            T_s, phi = self._sample_state()
            # barrel -> charge heat over dt: exponential relaxation towards the barrel temperature with the secant heat
            # capacity (sensible + latent) of the charge; unconditionally stable for any charge size / conductance
            G = p['sample_conductance_w_k'] * (self.m_s / self.m_loaded)
            dT = self.T_b - T_s
            if abs(dT) > 1e-12 and G > 0:
                dH_eq = self.m_s * self._h_of_T(self.T_b) - self.H_s
                exchange = dH_eq * -math.expm1(-G * dt * dT / dH_eq)
            if T_s > self.sample['melting_low_k']:
                self.t_hist += dt
            # die flow for the contacting piston (melt fraction from the enthalpy)
            extruded_volume = 0.0
            if self.contact:
                q = self._flow(T_s, phi)
                dm = min(self.sample['melt_density_kg_m3'] * q * dt, phi * self.m_s)
                if dm > 0:
                    h = self.H_s / self.m_s
                    self.m_s -= dm
                    self.H_s -= dm * h
                    self.book['extruded_enthalpy_j'] += dm * h
                    self.strand_kg += dm
                    self.strand_m3 += dm / self.sample['melt_density_kg_m3']
                    extruded_volume = dm / self.sample['melt_density_kg_m3']
                    self.q_true = extruded_volume / dt
            self.H_s += exchange
            # compaction bookkeeping: unmelted pellets only turn into melt (never back to bulk pellets)
            self.pellet = min(self.pellet, 1.0 - self._phi())
            if self.contact:
                x_new = min(self._column_height(), self.x)
                dx = self.x - x_new
                if dx > 0:
                    friction = p['piston_friction_n'] * dx
                    force = self._load_mass() * GRAVITY - p['piston_friction_n']
                    pressure_work = force * dx
                    # the share that pushed melt through the die is dissipated there and leaves with the extrudate
                    # (delta_p * V_extruded); only the compaction share heats the charge left in the barrel
                    die_work = min(pressure_work, force / STANDARD_AREA_M2 * extruded_volume)
                    self.book['extruded_enthalpy_j'] += die_work
                    self.H_s += pressure_work - die_work
                    self.x = x_new
        self.book['heater_input_j'] += P * dt
        self.book['ambient_export_j'] += loss * dt
        self.book['friction_heat_j'] += friction
        self.book['pressure_work_j'] += pressure_work
        self.T_b += (P * dt - loss * dt - exchange + friction) / p['barrel_heat_capacity_j_k']

    def _advance_move(self, nxt):
        m = self._move
        dist = abs(self.x_target - m['x0'])
        t = (nxt - m['start_ns']) / 1e9
        done = t * m['speed'] >= dist - 1e-15
        new_x = self.x_target if done else m['x0'] + m['direction'] * m['speed'] * t
        dx = new_x - self.x
        heat = self.plant['piston_friction_n'] * abs(dx)
        self.book['host_work_j'] += self._load_mass() * GRAVITY * dx + heat
        self.book['friction_heat_j'] += heat
        self.T_b += heat / self.plant['barrel_heat_capacity_j_k']
        self.x = new_x
        if done:
            self._move = None
            if m['insert']:
                self.contact = True

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
            dt = (nxt - self.time_ns) / 1e9
            if self._move is not None:
                self._advance_move(nxt)
            self._integrate(dt)
            # a descending insert can meet the (shrinking) charge column
            if self._move is not None and self._move['insert'] and self.x <= self._column_height() + 1e-12:
                self.x = max(self._column_height(), 0.0)
                self.x_target = self.x
                self._move = None
                self.contact = True
            if self._cut is not None:
                c = self._cut
                frac = min(1.0, (nxt - c['start_ns']) / max(1, c['end_ns'] - c['start_ns']))
                self.cutter_angle = c['a0'] + (c['a1'] - c['a0']) * frac
            self.time_ns = nxt
            if self._cut is not None:
                c = self._cut
                if not c['severed'] and self.time_ns >= c['mid_ns']:
                    c['severed'] = True
                    self._sever(self.time_ns)
                if self.time_ns >= c['end_ns']:
                    self.cutter_angle = c['a1']
                    self._cut = None
            if self._weighing is not None and self.time_ns >= self._weighing['end_ns']:
                self._complete_weighing()
            if self.phase == 'running':
                acq = self._acq
                self.elapsed = (self.time_ns - acq['start_ns']) / 1e9
                acq['elapsed'] = self.elapsed
                am = acq['method']
                if acq['next_cut_ns'] is not None and self.time_ns >= acq['next_cut_ns']:
                    acq['next_cut_ns'] += round(am['cut_interval_s'] * 1e9)
                    if self._cut is None and self.power:
                        self._start_cut(auto=True)
                if self.time_ns >= acq['next_sample_ns']:
                    acq['next_sample_ns'] += acq['period_ns']
                    self._record(self.time_ns)
                    row = self.rows[-1]
                    if row['temperature_k'] is not None and abs(row['temperature_k'] - acq['target_k']) > am['temperature_tolerance_k'] + 1e-9:
                        enc = row['encoder_position_m']
                        if enc is not None and am['window_lower_m'] <= enc <= am['window_upper_m']:
                            acq['temperature_excursion'] = True
                    enc = row['encoder_position_m']
                    if enc is not None and enc <= am['window_lower_m']:
                        acq['window_traversed'] = any(r['encoder_position_m'] is not None and r['encoder_position_m'] >= am['window_upper_m'] for r in self.rows)
                        self._finish('succeeded' if acq['window_traversed'] else 'failed', 'WINDOW_TRAVERSED' if acq['window_traversed'] else 'STARTED_BELOW_WINDOW')
                if self.phase == 'running' and (self.m_s <= 1e-12 or self.x <= MIN_PISTON_M):
                    self._finish('failed', 'SAMPLE_EXHAUSTED')
                if self.phase == 'running' and self.time_ns >= acq['end_limit_ns']:
                    self._finish('failed', 'DURATION_ELAPSED')
        self.revision += 1


class ZwickMflowSession:
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
        meta = export_zwick_mflow_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0',
                    capabilities={k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or self._role != 'agent'},
                    state_slots={k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or self._role != 'agent'})

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

    def _filter_result(self, result):
        result = copy.deepcopy(result)
        # Public acquisition payloads use the same redaction for every role; provenance.read is the evaluator channel.
        if 'rows' in result and 'template_source' in result:
            result['template_source'] = (str(LIBRARY_PATH.relative_to(ROOT)) + '; selected record available only through evaluator provenance.read') if result['template_source'] is not None else None
            result['evidence'] = ('synthetic_forward_model; quantised encoder/time/temperature observations and external weighings; manufacturer MVR scalar with explicit priors; selected record restricted'
                                  if result['operation_id'] is not None else 'no_measurement')
        return result

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        h = self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id, str) or capability_id not in CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id, str) or not request_id:
                raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id]['input_schema'], arguments)
            key = (self._principal, request_id)
            digest = json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            if key in h._requests:
                old, response = h._requests[key]
                if old != digest:
                    raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(response)
            # Operations started by another *agent* are private to it; ones started from the physical panel or by the host
            # (evaluator/debug principals, e.g. a Matterix key press) are visible to every operator at the instrument.
            owner = h._owners.get(h.operation_id) if h.operation_id else None
            foreign = owner is not None and owner != self._principal and h._principals.get(owner) == 'agent'
            if capability_id in ('acquisition.stop', 'acquisition.result') and self._role == 'agent' and foreign:
                raise ActionRejected('ACCESS_DENIED')
            if capability_id in ('cutter.cut', 'thermal.set_temperature') and h.phase == 'running' and self._role == 'agent' and foreign:
                raise ActionRejected('ACCESS_DENIED')
            if capability_id == 'power.set' and not arguments['enabled'] and h.phase == 'running' and self._role == 'agent' and foreign:
                raise ActionRejected('ACCESS_DENIED')
            result = self._filter_result(h.invoke(capability_id, **arguments))
            operation = result.get('operation_id') if capability_id == 'acquisition.start' else None
            if operation:
                h._owners[operation] = self._principal
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=operation,
                            status='accepted' if operation else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                            result=result, observations={}, error=None)
            h._requests[key] = (digest, copy.deepcopy(response))
            return response
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None,
                        status='unsupported' if str(exc) == 'UNSUPPORTED' else 'rejected', state_revision=h.revision,
                        clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                        error=dict(code=str(exc), message=str(exc), retryable=False, side_effects='none'))

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        if not isinstance(state_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        entry = SLOTS.get(state_id)
        if not entry or state_id not in WRITE_VIA:
            raise ActionRejected('ACCESS_DENIED')
        capability, argument = WRITE_VIA[state_id]
        return self.invoke(instance_id=instance_id, capability_id=capability, arguments={argument: value}, request_id=request_id)

    def poll(self, operation_id):
        h = self._host
        if not isinstance(operation_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        result = h._result() if operation_id == h.operation_id and h.phase == 'running' else h.records.get(operation_id)
        if result is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        return dict(operation_id=operation_id, status=result['phase'], result=self._filter_result(result))

    def cancel(self, operation_id, request_id):
        self.poll(operation_id)
        h = self._host
        if not isinstance(request_id, str) or not request_id:
            raise ActionRejected('INVALID_ARGUMENT')
        key = (self._principal, request_id)
        if key in h._cancel_operations:
            if h._cancel_operations[key] != operation_id:
                raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(h._requests[key][1])
        if key in h._requests:
            raise ActionRejected('REQUEST_CONFLICT')
        if operation_id != h.operation_id or h.phase != 'running':
            raise ActionRejected('NOT_RUNNING')
        result = self.invoke(instance_id=h.instance_id, capability_id='acquisition.stop', arguments={}, request_id=request_id)
        if result['status'] == 'succeeded':
            h._cancel_operations[key] = operation_id
        return result


WRITE_VIA = {'thermal.target_k': ('thermal.set_temperature', 'target_k')}   # the load is placed by the host (evaluator weight.configure)
EFFECTS = {
    'plant.configure': ['plant.configured', 'thermal.target_k', 'truth.barrel_temperature_k', 'truth.sample_temperature_k', 'heater.output_fraction'],
    'balance.configure': ['balance.configured'],
    'method.configure': ['method.configured', 'method.test_load_kg'],
    'power.set': ['power.enabled', 'power.angle_rad', 'thermal.indicated_k', 'encoder.position_m', 'encoder.in_range', 'acquisition.phase', 'measurement.validity',
                  'measurement.live_mvr_m3_s', 'truth.heater_power_w', 'heater.output_fraction', 'cutter.running', 'measurement.mvr_m3_s', 'measurement.mfr_kg_s'],
    'thermal.set_temperature': ['thermal.target_k'],
    'weight.configure': ['weight.total_load_kg'],
    'sample.load': ['sample.present', 'preheat.elapsed_s', 'truth.sample_mass_kg', 'truth.sample_temperature_k', 'truth.melt_fraction', 'truth.pellet_fraction', 'truth.thermal_history_s'],
    'sample.remove': ['sample.present', 'preheat.elapsed_s', 'truth.sample_mass_kg', 'truth.sample_temperature_k', 'truth.melt_fraction', 'truth.pellet_fraction', 'truth.thermal_history_s'],
    'piston.withdraw': ['piston.target_m', 'piston.moving', 'piston.contact'],
    'piston.insert': ['piston.target_m', 'piston.moving', 'piston.contact'],
    'cutter.cut': ['cutter.running'],
    'collection.clear': ['segments.count', 'segments.unweighed', 'extrudate.length_m', 'extrudate.last_cut_length_m', 'truth.strand_mass_kg'],
    'balance.weigh': ['balance.busy', 'segments.unweighed'],
    'calibration.encoder': ['encoder.position_m'],
    'calibration.balance': [],
    'acquisition.start': ['acquisition.phase', 'acquisition.elapsed_s', 'acquisition.points', 'measurement.validity', 'cutter.running', 'measurement.live_mvr_m3_s'],
    'acquisition.stop': ['acquisition.phase', 'measurement.validity', 'measurement.live_mvr_m3_s', 'measurement.mvr_m3_s', 'measurement.mfr_kg_s'],
    'sim.step': list(SLOTS),
}
for _k, _v in EFFECTS.items():
    CAPS[_k]['effects'] = _v
ERRORS = sorted(set(re.findall(r"ActionRejected\('([A-Z_0-9]+)'", Path(__file__).read_text())) |
                {'USER_CANCEL', 'POWER_OFF', 'WINDOW_TRAVERSED', 'STARTED_BELOW_WINDOW', 'SAMPLE_EXHAUSTED', 'DURATION_ELAPSED'})
ASYNC = {'acquisition.start'}
LIMITATIONS = [
    '选定Mflow 1128610（230V）基础版：料筒1128612内径9.55mm、活塞1128614（即0.325kg载荷档）、口模312342 2.095mm×8mm（烧结耐磨耐酸）；未选气动提升/选重器1128621/1128624、清洗1128622、预载1128623、预热位活塞保持1128627、口模塞1128618、分隔板1128619、回冷090173、半口模325554、耐酸料筒/活塞1128613/1128615与PVC口模092326；不建模换口模。',
    '厂家规格：50–450°C、总载荷0.325–21.6kg、位移分辨率0.003mm（方法B误差限±0.02mm）、时间分辨率0.002s（误差限±0.01s）、温度显示分辨率<0.1K、口模以上0–75mm温度准确度<0.3K（ISO 1133-2）；这些是分辨率/误差限，不是本模型的噪声模型。',
    '炉体为集中热节点，PI控温；热容、散热、加热器上限（≤0.7kVA连接视在功率）、控温参数与试样换热导率都是宿主显式输入的工程参数，不是厂家数据；温度准确度未经ISO 1133-2认证。',
    '试样为装料焓节点：比热、潜热（熔程线性）、颗粒堆积密度、熔体密度均为宿主先验；厂家库只给23°C固体密度与DSC熔点，固体密度不得当作测试温度下熔体密度。',
    '口模流动由所选厂家MVR标量在其温度/载荷下锚定，经宿主给定的幂律指数n、流动活化能Ea和热历史速率外推到实际条件，并扣除活塞摩擦；单个MVR/MFR不能唯一决定完整流变曲线、入口压力损失、壁滑或温敏性。',
    '未熔颗粒按φ³抑制流动（显式假设）；颗粒压实只随熔化单向发生，重新凝固不会恢复颗粒堆积。',
    '活塞位置=料柱高度（ISO平均截面0.711cm²）；方法B的MVR由窗口内首末量化位移与量化时间计算；方法A的MFR只用外接天平称得的测量切段质量与其切断间隔；密度只在两者都有时给出。',
    '砝码下落势能分为摩擦功（进入炉体）与压力功：推动熔体通过口模的部分Δp·V在口模内耗散并随挤出物离开，压实部分进入留在料筒内的试样；手动升降活塞的宿主功含势能变化与摩擦热；挤出物带走其焓（在空气中冷却，视为离开系统边界），质量仍在库存中直到收集或清走。',
    '切刀为交替半圈，经过π/2瞬间切断；首次切断清除预热期挤出物，之后相邻两次自动切断之间为测量切段；切刀电机功率/速度未公开，半圈时间为工程参数。',
    '外接分析天平（厂家可选043851/014720，0.1mg读数）只作RS-232代理：稳定时间、增益/偏置和分辨率由宿主给定；称量不在Mflow主机内完成。',
    '位移传感器按照片所见转臂结构建模，量程约0–107mm（工程假设）；超量程或断电时读数为null；两点标定只使用宿主提供的独立观测值。',
    'agent只能看到量化位移、显示温度、切段观测和结果；精确活塞位置、熔融分数、真实流率和所选材料身份只对evaluator可见；acquisition.result对所有角色隐去材料身份，完整来源只能经provenance.read获得。',
    '时间推进为事件对齐的守恒子步（≤50ms；炉体PI为显式、料筒—试样换热为指数松弛）：所有能量/质量项用同一数值记账因而账本精确闭合，但不同step切分会造成约1e-8–1e-7m、1e-4K量级的真值差，恰在量化边界附近的个别记录行（约1%）会相差一个量化步（0.003mm），MVR不受影响。',
    '正向模型的位移—体积换算与方法B分析使用同一ISO平均截面0.711cm²，因此方法B的回收只检验量化与算术，不独立检验截面假设（环隙回流未建模）。',
    '默认n/Ea先验跨条件外推有明显误差：例如B4330G6 HR从250°C/2.16kg外推到260°C/5kg得约16.5cm³/10min，厂家表为19（−13%）；换条件时宿主应改用该条件的厂家数值格或给出更合适的先验。',
    '热历史倍率exp(k·t)限制在1e-3–1e3的数值域；堆积密度不得大于熔体密度（被拒）；每次采集的记录行不超过200000行（方法设定时检查）。',
    '每次采集保存自己的方法快照、记录行与切段：清走收集区、改方法或称量其它切段都不会改写已完成的结果；晚完成的称量只回填其所属采集。每次采集的切段不超过10000个（手动切刀与自动切段共用预算）；由某个agent启动的采集中，其他agent不能改温度设定、切刀、停止或读取其结果；面板/宿主启动的采集对所有操作者共享。',
    '没有真实硬件、testXpert连接、机器人装料/压实/清筒、挤出丝接触或卷曲、离模膨胀实测与ISO认证。',
]


def export_zwick_mflow_contract_metadata():
    slots = {}
    caps = {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'],
                        access='read_write' if k in WRITE_VIA else 'read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'ZwickMflowSession.read(state_ids=[{k!r}])'),
                        write_via=None if k not in WRITE_VIA else dict(capability_id=WRITE_VIA[k][0], argument=WRITE_VIA[k][1], fixed_arguments={}),
                        sampling=dict(mode='instant', time_domain='simulation', window_description='Pure read; encoder 0.003 mm, time 0.002 s, display 0.01 K quantisation; rows on the method time grid.'),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.zwick_mflow.ZwickMflowSession.invoke',
                                              invocation=f'session.invoke(instance_id="mflow_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or v['kind'] in ('query', 'analysis', 'simulation') else [dict(
                     description_zh='入口原子核对通电、配置、试样/活塞/载荷/预热/温度、切刀与天平忙闲等前提；被拒时状态不变。',
                     state_ids=['power.enabled', 'plant.configured', 'sample.present', 'piston.contact', 'acquisition.phase'], enforced_by='runtime', check='ZwickMflowRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或操作标志；温度、位移、挤出与切段只随step推进') for s in v['effects']],
                 observes=[], model_effects=dict(mode='none', description_zh='运行层只输出状态，由场景层绑定关节与读数', binding_ids=[]),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion='活塞读数越过窗口下限（或超时/料尽/取消）' if async_ else '原子配置或外部动作请求，实际状态由step推进',
                                completion_state_ids=['acquisition.phase'] if async_ else [], timeout_s=None, cancellation='supported' if async_ else 'unsupported',
                                retry='same_request_id', resources=['host_clock', 'barrel', 'piston', 'cutter', 'encoder'],
                                partial_failure_zh='停止/断电/超时只终止记录与自动切断；已挤出物、切段、称量记录与热状态全部保留。'),
                 errors=[dict(code=e, meaning_zh=e, side_effects='partial' if async_ and e in ('USER_CANCEL', 'POWER_OFF', 'SAMPLE_EXHAUSTED', 'DURATION_ELAPSED', 'STARTED_BELOW_WINDOW') else 'none') for e in ERRORS],
                 verification=[], limitations=LIMITATIONS, source_ids=['manufacturer_primary', 'simulation_assumption', 'template_library'])
        if k in ('acquisition.start', 'piston.insert', 'piston.withdraw', 'cutter.cut', 'thermal.set_temperature', 'power.set', 'balance.weigh', 'weight.configure', 'sample.load'):
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh='由统一宿主时钟推进热、熔化、挤出、切刀、称量与量化观测')
                                   for s in SLOTS if s not in v['effects'] and SLOTS[s]['origin'] in ('sensor', 'derived', 'scene', 'process_truth')]
        caps[k] = c
    mechanism = dict(
        component_ids=['heater', 'barrel', 'piston', 'test_weight', 'die', 'cutter', 'displacement_sensor', 'sensor_arm', 'controller', 'balance_rs232'],
        state_ids=list(slots), meaning_zh='料筒PI控温、装料熔化与压实、幂律口模流动（载荷/摩擦/温度/热历史）、活塞位移观测、切刀切段与外部称量',
        implementation='lumped',
        equations=['C_b dT_b/dt = P_heater - G_b (T_b - T_amb) - Q_bs + F_f |dx|/dt ; P_heater = clamp(Kp e + I, 0, P_max), dI/dt = (Kp/Ti) e (conditional integration)',
                   'h(T) = cp (T - T_amb) + L phi(T), phi = clamp((T - T_lo)/(T_hi - T_lo), 0, 1) ; dH_s/dt = G_s (m_s/m_0)(T_b - T_s) + (m g - F_f) dx/dt - h_s dm_ext/dt',
                   'Q = MVR_ref ((m g - F_f)/(m_ref g))^(1/n) exp(-(Ea/(n R))(1/T_s - 1/T_ref)) exp(k_h t_hist) phi^3 ; dm_ext = rho_melt Q dt',
                   'x_piston = m_s (p/rho_bulk + (1-p)/rho_melt) / A_ISO, A_ISO = pi/4 ((D_barrel + D_piston)/2)^2 = 0.711 cm^2 ; p = min(p, 1 - phi)',
                   'W_host = m g dx + F_f |dx| (manual moves) ; potential released under load -> F_f dx (barrel) + (m g - F_f) dx (charge)',
                   'MVR_B = A_ISO (x_first - x_last)/(t_last - t_first) inside [window_lower, window_upper] from quantised encoder (3 um) and time (2 ms)',
                   'MFR_A = sum(m_obs)/sum(dt_cut) over externally weighed measurement segments; density_T = MFR/MVR',
                   'arm elevation = asin((x - 0.050)/0.140), upper stop 0.42 rad; encoder range ~0 .. 0.107 m'],
        parameters={}, source_ids=['simulation_assumption', 'template_library'], limitations=LIMITATIONS)
    import importlib.util
    spec = importlib.util.spec_from_file_location('_mflow_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.annotate_export(SLUG, caps, slots, {'melt_flow_rate': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'sample.load': (SAMPLE, SAMPLE_EXAMPLE), 'method.configure': (METHOD, METHOD_EXAMPLE),
                                'balance.configure': (BALANCE, BALANCE_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'melt_flow_rate': mechanism})
