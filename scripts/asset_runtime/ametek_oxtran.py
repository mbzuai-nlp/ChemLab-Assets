"""CHEM-147 AMETEK MOCON OX-TRAN 2/22 L oxygen permeation analyzer runtime (ASTM D3985 coulometric method, SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * test-cell block: lumped heat node with a PI-controlled thermoelectric heater/cooler (10-40 degC documented, +/-0.2 K);
  * RH generator: first-order approach of both gas streams to the RH setpoint (0 = dry, 5-90 % controlled range from the
    task table) with a hidden static bias inside the documented +/-4 % RH accuracy band;
  * each of the two cells: removable film cartridge in a drawer, pneumatic clamp, a 1-D Fickian film (implicit finite volume,
    exactly conservative) between a test-gas chamber (O2 partial pressure y_O2 (P_baro - p_w)) and a swept carrier-gas
    chamber (well-mixed, turnover F/V), temperature (Arrhenius) and water-activity (host coefficient) dependence of P and D,
    test-gas edge leak and TruSeal-flushed ambient ingress into the carrier side, air-equilibrated film when mounted;
  * one shared Coulox sensor (Faraday: I = 4 F eta n_O2, 1 cc/day = 0.000199 A, first-order response, 10 pA counts, noise)
    multiplexed by the sequencer through cell exams and ReZero exams (carrier only, system baseline);
  * reported OTR = (I_exam - I_rezero) / (4 F) / area x 760 mmHg / P_baro x 100 / permeant % (no user gain: the 2/22 has no documented
    sensor adjustment; out-of-spec certified films mean service),
    quantised to the documented resolution; conditioning, individual zero, standard / convergence / continuous test modes.
Permeation templates (P, D at 298 K) come from the asset's oxygen_permeation_library.json (polyVERSE experimental rows);
activation energies, humidity coefficients, chamber volumes, leaks, sensor lag/noise and the thermal plant are explicit host
priors (evidence = assumption).  Observed results are computed only from the quantised sensor observations.
"""
from __future__ import annotations

import copy
import json
import math
import random
import re
from pathlib import Path

PROTOCOL_VERSION = '1.0.0'
SLUG = 'ametek_oxtran'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-147'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t1_thermal' / SLUG
LIBRARY_PATH = ASSET / 'oxygen_permeation_library.json'

# ---------------------------------------------------------------- documented / physical constants
FARADAY = 96485.33212
GAS_CONSTANT = 8.314462618
V_STP_M3_MOL = 0.022414                          # m3(STP)/mol, 0 degC 101.325 kPa (cc(STP) convention of OTR and Coulox)
STD_PRESSURE_PA = 101325.0                       # 760 mmHg, barometric normalisation target (manual appendix D)
CC_DAY_PER_MOL_S = V_STP_M3_MOL * 1e6 * 86400.0  # 1 mol/s = 1.9366e9 cm3(STP)/day
AMP_PER_CC_DAY = 4 * FARADAY / CC_DAY_PER_MOL_S  # 1.993e-4 A per cc/day (manual: 0.000199 A)
COUNT_A = 1e-11                                  # 1 raw count ~ 10 pA (related 2/28 manual)
AIR_O2_FRACTION = 0.2095
TEMPERATURE_RANGE_K = (283.15, 313.15)           # documented 10 .. 40 degC
TEMPERATURE_ACCURACY_K = 0.2                     # documented +/-0.2 degC
RH_RANGE = (0.05, 0.90)                          # task table: controlled RH 5-90 % (+/-4 %); 0 = dry gas (certified-film method)
RH_ACCURACY = 0.04
TEST_AREA_M2 = 50e-4                             # documented standard film test area
MASK_AREA_RANGE_M2 = (5e-4, 50e-4)               # foil masks down to 5 cm2 (product page "5.0 cm2 masked" range)
OTR_RANGE_CC_M2_DAY = (0.005, 200.0)             # documented L model, 50 cm2
OTR_RESOLUTION_CC_M2_DAY = 0.002                 # task table (2020 brochure); 2025 brochure/product page list 0.0001
REPEATABILITY_CC_M2_DAY = 0.002                  # "0.002 or 1 %, whichever is greater"
REPEATABILITY_REL = 0.01
PACKAGE_RANGE_CC_DAY = (0.000025, 1.0)           # documented cc/(pkg day) range (L model)
SENSOR_RANGE_MOL_S = PACKAGE_RANGE_CC_DAY[1] / CC_DAY_PER_MOL_S   # upper detection limit = 200 cc/(m2 day) x 50 cm2 = 1 cc/day
OVER_RANGE_FACTOR = 1.25                         # sensor protection threshold relative to the upper range (engineering)
OVER_RANGE_CURRENT_A = OVER_RANGE_FACTOR * 4 * FARADAY * SENSOR_RANGE_MOL_S   # acted on the measured sensor current
MAX_PROVENANCE = 1000                            # provenance.read lists the most recent test operations
MAX_FILM_THICKNESS_M = 120 * 25.4e-6             # documented "up to 120 mil"
CARRIER_FLOW_SCCM = 10.0                         # per cell, certified-film instruction "carrier gas flow is set to 10 sccm"
SUPPLY_GAUGE_PA = dict(carrier=(137.9e3, 193.1e3), test=(137.9e3, 220.6e3), nominal=172.4e3)   # 20-28 / 20-32 psi, 25 psi nominal
TEMPERATURE_DISPLAY_RESOLUTION_K = 0.01
RH_DISPLAY_RESOLUTION = 0.001
BAROMETER_RESOLUTION_PA = 10.0
DRAWER_TRAVEL_M = 0.190
CLAMP_TRAVEL_M = 0.003
POWER_SWITCH_ON_RAD = 0.30
N_FILM_NODES = 32
MAX_SUBSTEP_S = 10.0
OPEN_WINDOW_MOL_S_PA = 1e-9                      # empty cartridge: conductance of the open 50 cm2 window between the chambers (engineering)
MAX_READINGS = 5000                              # readings per test record (schema maxItems)
CELLS = ('A', 'B')


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


def array(s, minimum=0, maximum=MAX_READINGS):
    return dict(type='array', items=s, minItems=minimum, maxItems=maximum)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


BOOL = {'type': 'boolean'}
TEXT = {'type': 'string'}
INT = {'type': 'integer', 'x-unit': '1'}
CELL = enum(CELLS)
PHASE = enum(['idle', 'running', 'succeeded', 'cancelled', 'failed'])
CELL_PHASE = enum(['idle', 'zero_start', 'conditioning', 'testing', 'zero_end', 'succeeded', 'cancelled', 'failed'])


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


def saturation_pressure_pa(T):
    """Buck (1996) saturation vapour pressure of water over liquid water."""
    c = T - 273.15
    return 611.21 * math.exp((18.678 - c / 234.5) * (c / (257.14 + c)))


def molar_flow(sccm):
    return sccm * 1e-6 / 60.0 / V_STP_M3_MOL


def cc_m2_day(mol_m2_s):
    return None if mol_m2_s is None else mol_m2_s * CC_DAY_PER_MOL_S


# ---------------------------------------------------------------- inputs
PLANT_EXAMPLE = dict(ambient_k=296.15, ambient_rh=0.45, barometric_pa=101325.0, block_heat_capacity_j_k=4000.0, block_loss_w_k=1.5, tec_max_w=40.0,
                     controller_gain_w_k=25.0, controller_integral_s=600.0, temperature_sensor_bias_k=0.05, rh_time_constant_s=600.0, rh_bias=0.012,
                     test_chamber_volume_m3=4e-6, carrier_chamber_volume_m3=4e-6, test_gas_flow_sccm_per_cell=10.0, catalyst_removal_efficiency=0.999,
                     edge_leak_mol_s_pa=2.5e-20, ambient_ingress_mol_s_pa=1e-18, truseal_flush_efficiency=0.95, rezero_path_leak_mol_s=5e-16,
                     sensor_time_constant_s=45.0, sensor_efficiency=0.998, sensor_background_a=2e-11, sensor_noise_a=4e-10, sensor_relative_noise=0.003,
                     noise_seed=147, clamp_actuation_s=2.0, drawer_speed_m_s=0.10, carrier_total_flow_sccm=60.0, test_total_flow_sccm=20.0, accept_engineering=True)
PLANT = obj(dict(ambient_k=number('K', 283.15, 308.15), ambient_rh=number('1', 0.0, 0.95), barometric_pa=number('Pa', 53300.0, 113300.0),
                 block_heat_capacity_j_k=number('J/K', 200.0, 50000.0), block_loss_w_k=number('W/K', 0.05, 50.0), tec_max_w=number('W', 2.0, 400.0),
                 controller_gain_w_k=number('W/K', 0.1, 1000.0), controller_integral_s=number('s', 5.0, 20000.0),
                 temperature_sensor_bias_k=number('K', -TEMPERATURE_ACCURACY_K, TEMPERATURE_ACCURACY_K),
                 rh_time_constant_s=number('s', 5.0, 20000.0), rh_bias=number('1', -RH_ACCURACY, RH_ACCURACY),
                 test_chamber_volume_m3=number('m^3', 1e-7, 1e-4), carrier_chamber_volume_m3=number('m^3', 1e-7, 1e-4),
                 test_gas_flow_sccm_per_cell=number('cm^3/min', 1.0, 100.0), catalyst_removal_efficiency=number('1', 0.0, 1.0),
                 edge_leak_mol_s_pa=number('mol/(s*Pa)', 0.0, 1e-14), ambient_ingress_mol_s_pa=number('mol/(s*Pa)', 0.0, 1e-13),
                 truseal_flush_efficiency=number('1', 0.0, 1.0), rezero_path_leak_mol_s=number('mol/s', 0.0, 1e-10),
                 sensor_time_constant_s=number('s', 0.5, 1800.0), sensor_efficiency=number('1', 0.8, 1.05), sensor_background_a=number('A', 0.0, 1e-7),
                 sensor_noise_a=number('A', 0.0, 1e-7), sensor_relative_noise=number('1', 0.0, 0.1), noise_seed=INT,
                 clamp_actuation_s=number('s', 0.1, 60.0), drawer_speed_m_s=number('m/s', 0.005, 2.0),
                 carrier_total_flow_sccm=number('cm^3/min', 1.0, 500.0), test_total_flow_sccm=number('cm^3/min', 1.0, 500.0), accept_engineering=BOOL))
GAS_EXAMPLE = dict(carrier_h2_fraction=0.02, carrier_o2_impurity_fraction=2e-6, carrier_supply_gauge_pa=172.4e3, test_o2_fraction=0.999, test_supply_gauge_pa=172.4e3,
                   accept_gas_supply=True)
GAS = obj(dict(carrier_h2_fraction=number('1', 0.0, 0.05), carrier_o2_impurity_fraction=number('1', 0.0, 1e-3), carrier_supply_gauge_pa=number('Pa', 0.0, 400e3),
               test_o2_fraction=number('1', 0.01, 1.0),
               test_supply_gauge_pa=number('Pa', 0.0, 400e3), accept_gas_supply=BOOL))
MOUNT_EXAMPLE = dict(cell='A', template_id='poly_ethylene_terephthalate', thickness_m=100e-6, exposed_area_m2=TEST_AREA_M2, permeability_override_mol_m_per_m2_s_pa=None,
                     diffusivity_override_m2_s=None, permeability_activation_energy_j_mol=45000.0, diffusivity_activation_energy_j_mol=45000.0,
                     humidity_coefficient=None, accept_transport_assumptions=True)
MOUNT = obj(dict(cell=CELL, template_id=TEXT, thickness_m=number('m', 1e-6, MAX_FILM_THICKNESS_M), exposed_area_m2=number('m^2', *MASK_AREA_RANGE_M2),
                 permeability_override_mol_m_per_m2_s_pa=nullable(number('mol*m/(m^2*s*Pa)', 1e-22, 1e-10)),
                 diffusivity_override_m2_s=nullable(number('m^2/s', 1e-17, 1e-6)),
                 permeability_activation_energy_j_mol=number('J/mol', 0.0, 150000.0), diffusivity_activation_energy_j_mol=number('J/mol', 0.0, 150000.0),
                 humidity_coefficient=nullable(number('1', 0.0, 20.0)), accept_transport_assumptions=BOOL))
TEST_CFG_EXAMPLE = dict(cell='A', test_mode='convergence', cycles=10, exam_minutes=30.0, conditioning_hours=0.5, individual_zero='end', convergence_percent=1.0,
                        convergence_count=3, max_cycles=60, report_basis='area', declared_area_m2=TEST_AREA_M2, declared_thickness_m=100e-6, temperature_tolerance_k=0.2)
TEST_CFG = obj(dict(cell=CELL, test_mode=enum(['continuous', 'standard', 'convergence']), cycles=number('1', 1, 1000), exam_minutes=number('min', 1.0, 600.0),
                    conditioning_hours=number('h', 0.0, 200.0), individual_zero=enum(['none', 'start', 'end', 'both']), convergence_percent=number('%', 0.1, 20.0),
                    convergence_count=number('1', 2, 20), max_cycles=number('1', 1, 2000), report_basis=enum(['area', 'package']),
                    declared_area_m2=number('m^2', *MASK_AREA_RANGE_M2), declared_thickness_m=nullable(number('m', 1e-6, MAX_FILM_THICKNESS_M)),
                    temperature_tolerance_k=number('K', 0.05, 5.0)))
TEST_CFG['properties']['cycles'] = INT | dict(minimum=1, maximum=1000)
TEST_CFG['properties']['convergence_count'] = INT | dict(minimum=2, maximum=20)
TEST_CFG['properties']['max_cycles'] = INT | dict(minimum=1, maximum=2000)
INSTRUMENT_EXAMPLE = dict(permeant_concentration_percent=100.0, rezero_enabled=True, rezero_frequency=2, rezero_exam_minutes=30.0, barometer_mode='auto', manual_barometer_pa=None)
INSTRUMENT = obj(dict(permeant_concentration_percent=number('%', 1.0, 100.0), rezero_enabled=BOOL, rezero_frequency=INT | dict(minimum=1, maximum=10),
                      rezero_exam_minutes=number('min', 1.0, 600.0), barometer_mode=enum(['auto', 'manual']), manual_barometer_pa=nullable(number('Pa', 53300.0, 113300.0))))
READING = obj(dict(index=INT, kind=enum(['exam', 'individual_zero']), time_s=number('s'), sensor_counts=INT, otr_mol_m2_s=nullable(number('mol/(m^2*s)')),
                   otr_cc_m2_day=nullable(number('cm^3/(m^2*d)')), otr_mol_pkg_s=number('mol/s'), otr_cc_pkg_day=number('cm^3/d'),
                   temperature_k=nullable(number('K')), rh_fraction=number('1'), barometric_pa=number('Pa')))
ZERO = obj(dict(time_s=number('s'), sensor_counts=INT, current_a=number('A')))
RESULT = obj(dict(operation_id=nullable(TEXT), cell=CELL, phase=PHASE, cell_phase=CELL_PHASE, elapsed_s=number('s'), exam_count=INT,
                  readings=array(READING), rezero_readings=array(ZERO), individual_zero_mol_m2_s=nullable(number('mol/(m^2*s)')),
                  final_otr_mol_m2_s=nullable(number('mol/(m^2*s)')), final_otr_cc_m2_day=nullable(number('cm^3/(m^2*d)')),
                  final_otr_cc_pkg_day=nullable(number('cm^3/d')), converged=BOOL, repeatability_relative=nullable(number('1')),
                  within_repeatability_spec=nullable(BOOL), permeance_mol_m2_s_pa=nullable(number('mol/(m^2*s*Pa)')),
                  permeability_mol_m_per_m2_s_pa=nullable(number('mol*m/(m^2*s*Pa)')), permeability_barrer=nullable(number('Barrer')),
                  conditions=obj(dict(target_k=nullable(number('K')), target_rh=number('1'), permeant_concentration_percent=nullable(number('%')),
                                      declared_area_m2=nullable(number('m^2')), declared_thickness_m=nullable(number('m')), report_basis=nullable(TEXT),
                                      carrier_flow_sccm=number('cm^3/min'), exam_minutes=nullable(number('min')))),
                  validity=enum(['not_completed', 'valid', 'below_range', 'over_range', 'sensor_over_range', 'not_converged', 'temperature_out_of_tolerance', 'humidity_out_of_tolerance', 'cancelled']),
                  terminal_reason=nullable(TEXT), template_source=nullable(TEXT), evidence=TEXT))
ANALYSIS = obj(dict(readings_mol_m2_s=array(number('mol/(m^2*s)', -1e-3, 1e-3), 1, MAX_READINGS), individual_zero_mol_m2_s=nullable(number('mol/(m^2*s)', -1e-3, 1e-3)),
                    window=INT | dict(minimum=1, maximum=100), driving_partial_pressure_pa=number('Pa', 1.0, 2e5), thickness_m=nullable(number('m', 1e-7, 0.01)),
                    convergence_percent=number('%', 0.01, 50.0)))
ANALYSIS_OUTPUT = obj(dict(steady_state_otr_mol_m2_s=number('mol/(m^2*s)'), steady_state_otr_cc_m2_day=number('cm^3/(m^2*d)'), repeatability_relative=nullable(number('1')),
                           converged=BOOL, permeance_mol_m2_s_pa=number('mol/(m^2*s*Pa)'), permeability_mol_m_per_m2_s_pa=nullable(number('mol*m/(m^2*s*Pa)')),
                           permeability_barrer=nullable(number('Barrer')), evidence=TEXT))
LEDGER_KEYS = ['film_mounted_mol', 'film_unmounted_mol', 'film_air_exchange_mol', 'film_test_side_in_mol', 'film_to_carrier_mol', 'film_content_mol', 'film_residual_mol',
               'carrier_in_mol', 'edge_leak_mol', 'ambient_ingress_mol', 'carrier_o2_impurity_mol', 'open_cell_mol', 'to_sensor_mol', 'to_vent_mol', 'released_to_air_mol',
               'carrier_chamber_mol', 'carrier_residual_balance_mol', 'rezero_stream_mol', 'sensor_consumed_mol', 'sensor_passthrough_mol', 'sensor_input_mol',
               'sensor_in_transit_mol', 'sensor_discarded_mol', 'sensor_balance_residual_mol', 'carrier_gas_used_mol', 'test_gas_used_mol', 'tec_heat_j', 'ambient_exchange_j', 'block_thermal_j', 'energy_residual_j',
               'over_range_events']
LEDGER = obj({k: number('1' if k == 'over_range_events' else 'J' if k.endswith('_j') else 'mol') for k in LEDGER_KEYS})

# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c) if isinstance(c, (list, tuple)) else [c], function_zh=meaning,
                   visibility=visibility, kind=kind, effects=[])


CONFIGURED = obj(dict(configured=BOOL))
CELL_ARG = obj(dict(cell=CELL))
cap('plant.configure', PLANT, CONFIGURED, PLANT_EXAMPLE, ['thermal_module', 'rh_generator', 'coulox_sensor'],
    '宿主声明实验室环境、测试池热容/散热/热电上限与PI参数、湿度发生器时间常数与偏差、腔体体积、流量、泄漏、传感器响应/噪声等工程参数（须显式接受）', 'evaluator', 'configuration')
cap('gas.configure', GAS, CONFIGURED, GAS_EXAMPLE, ['carrier_gas_inlet', 'test_gas_inlet'],
    '宿主声明外部气源：载气H₂份额（厂家98/2 N₂/H₂）、测试气氧份额（厂家≥99.9% O₂，也可接空气）与两路供气表压', 'evaluator', 'configuration')
cap('instrument.configure', INSTRUMENT, CONFIGURED, INSTRUMENT_EXAMPLE, 'controller_board',
    '仪器级测试参数：渗透物浓度（%，用于归一到100% O₂）、ReZero开关/频率/检测时长、气压计自动或手动', 'agent', 'configuration')
cap('test.configure', TEST_CFG, CONFIGURED, TEST_CFG_EXAMPLE, 'controller_board',
    '单池测试方法：模式（连续/标准N周期/收敛）、检测分钟、调理小时、单池零点（无/开始/结束/两者）、收敛判据、报告基准（面积/整包）、声明面积与厚度、温度容差', 'agent', 'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), 'power_switch',
    '开关整机电源；断电停止气流、温控与传感器、取消进行中的测试（已记录的读数保留），夹紧状态保持（工程假设）')
cap('thermal.set_temperature', obj(dict(target_k=number('K', *TEMPERATURE_RANGE_K))), obj(dict(target_k=number('K'))), dict(target_k=296.15), 'thermal_module',
    '只写入测试池目标温度（10–40°C）；实际温度由热电PI控温随宿主step演化')
cap('humidity.set', obj(dict(rh_fraction=number('1', 0.0, RH_RANGE[1]))), obj(dict(rh_fraction=number('1'))), dict(rh_fraction=0.0), 'rh_generator',
    '只写入载气与测试气的相对湿度目标（0=干气，或5–90%受控范围）；实际湿度按一阶响应趋近，含±4%内的静态偏差')
cap('drawer.move', obj(dict(cell=CELL, open=BOOL)), obj(dict(cell=CELL, target_m=number('m'))), dict(cell='A', open=True), ['drawer_a', 'drawer_b'],
    '宿主（操作者/机器人代理）以有限速度拉开或推回测试池抽屉；须先松开夹紧', 'evaluator', 'robot_skill')
cap('sample.mount', MOUNT, obj(dict(cell=CELL, mounted=BOOL, film_o2_mol=number('mol'))), MOUNT_EXAMPLE, ['cartridge_a', 'cartridge_b', 'film_a', 'film_b'],
    '宿主在台面把膜样（涂Apiezon脂）装入拉出的测试盒；绑定模板库记录（P、D）与显式活化能/湿度系数/覆盖面积（掩膜）先验；膜内初始为与空气平衡的溶解氧', 'evaluator', 'robot_skill')
cap('sample.unmount', CELL_ARG, obj(dict(cell=CELL, removed_film_o2_mol=number('mol'))), dict(cell='A'), ['cartridge_a', 'cartridge_b'],
    '宿主从拉出的测试盒取下膜样（膜内溶解氧随膜离开）', 'evaluator', 'robot_skill')
cap('cell.load_unload', CELL_ARG, obj(dict(cell=CELL, clamping=BOOL)), dict(cell='A'), ['button_load_a', 'button_load_b', 'clamp_a', 'clamp_b'],
    '面板装卸键：未夹紧时（抽屉须已推到底）气动夹紧测试盒，已夹紧时松开（该池测试须已结束）；动作需要有限时间')
cap('test.start', CELL_ARG, obj(dict(operation_id=TEXT)), dict(cell='A'), ['controller_board', 'coulox_sensor'],
    '开始单池测试：核对通电、配置、气源压力、夹紧；按方法依次单池零点→调理→检测周期→结束零点，结果由传感器量化观测计算')
cap('test.stop', CELL_ARG, obj(dict(cell=CELL, phase=PHASE, exam_count=INT)), dict(cell='A'), 'controller_board',
    '停止该池检测序列：连续模式（已有检测）按操作者停止正常结束——设了结束零点则先测零点，再按已测读数给出结果；其他情况为终止（已记录读数保留）。膜仍在池内、测试气切回载气冲洗')
cap('test.result', CELL_ARG, RESULT, dict(cell='A'), 'controller_board',
    '返回该池最近一次测试的读数序列、ReZero基线、最终OTR、重复性、名义渗透率与有效性；所有角色都隐去所选模板身份', 'agent', 'query')
cap('analysis.otr', ANALYSIS, ANALYSIS_OUTPUT, dict(readings_mol_m2_s=[3.02e-8, 3.03e-8, 3.03e-8], individual_zero_mol_m2_s=None, window=3, driving_partial_pressure_pa=101325.0,
                                                    thickness_m=25e-6, convergence_percent=1.0), 'controller_board',
    '纯函数：由读数序列求稳态OTR（末窗口均值减单池零点）、相对重复性、渗透量与渗透率（名义驱动压差、厚度先验）', 'agent', 'analysis')
cap('ledger.read', obj({}), LEDGER, {}, 'controller_board', '受限O₂物质账（膜、载气腔、泄漏、传感器、排空）与测试池热量账', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(cell_a=nullable(TEXT), cell_b=nullable(TEXT), operations=array(obj(dict(operation_id=TEXT, source=nullable(TEXT))), 0, MAX_PROVENANCE))), {}, 'controller_board',
    '受限：两池当前膜样与最近1000次测试所用模板记录的来源', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), 'controller_board', '唯一宿主时间推进（纳秒整数时钟，余数累计）', 'evaluator', 'simulation')
UNSUPPORTED = {
    'hardware.connect': '无真实Ethernet/USB通信、WinPerm 2/PermWare或PERM-NET Lite连接',
    'robot.mount_film': '无机器人切膜、涂脂、装夹与抽屉操作验证（由宿主sample.mount/drawer.move代理）',
    'package.test': '未选整包/容器测试适配器（Package testing adapter）',
    'cartridge.reduced_area': '未选5.64cm²缩小面积测试盒（量程上限×9）；仅支持箔掩膜覆盖面积',
    'cartridge.edge_effect': '未选边缘效应测试盒',
    'cartridge.blister': '未选泡罩测试盒',
    'test_mode.convergence_by_hours': '“按小时收敛”模式的判据未公开（帮助系统不可得），未实现',
    'test.qc_mode': 'QC通过/不通过模式未实现',
    'test.auto_method': '自动测试方法开发（Auto-Test）未实现',
    'test.sequencing': '多条件顺序测试（Sequential testing）未实现；可由宿主按条件重复test.configure/test.start',
    'report.export': '报告打印/USB导出未实现（结果经test.result返回）',
    'accuracy.certification': '无认证膜实测、NIST可溯源验证或厂家准确度实验',
    'sensor.adjust': '2/22认证膜说明：认证膜结果超出允差时“分析仪可能需要维修”（联系厂家服务），只有2/12R要求校准；产品页与规格书称Coulox为绝对传感器、无需校准。'
                     '“Permeant Sensor Adjustment”只见于同机身2/28手册附录F，2/22未公开该功能，故不提供用户增益调整',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, 'controller_board', v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


OTR_REF = '按760mmHg与100%渗透物归一的单位面积氧透过率（cc指0°C、101.325kPa标准体积；×1.9366e9=cc/(m²·day)）。'
slot('clock.time_s', number('s'), 's', 'derived', 'controller_board', '宿主时钟；read不推进')
for k, c, m in [('plant.configured', 'thermal_module', '工程过程参数已声明'), ('gas.configured', 'carrier_gas_inlet', '外部气源已声明'),
                ('instrument.configured', 'controller_board', '仪器级测试参数已设定'), ('power.enabled', 'power_switch', '整机通电'),
                ('gas.supply_ok', 'carrier_gas_inlet', '两路供气表压在厂家范围内（载气20–28psi、测试气20–32psi）'),
                ('sensor.over_range', 'coulox_sensor', '传感器处于超量程保护（旁路）')]:
    slot(k, BOOL, '1', 'derived', c, m)
slot('thermal.target_k', number('K'), 'K', 'setpoint', 'thermal_module', '测试池目标温度，只是目标')
slot('thermal.indicated_k', nullable(number('K')), 'K', 'sensor', 'thermal_module', '测试池温度显示（0.01K量化，含±0.2K内的静态偏差）；断电为null')
slot('humidity.target_fraction', number('1'), '1', 'setpoint', 'rh_generator', '载气与测试气相对湿度目标，只是目标（0=干气）')
slot('humidity.indicated_fraction', nullable(number('1')), '1', 'sensor', 'rh_generator', '仪器显示的相对湿度（按其控制模型；不含静态偏差，0.001量化）；断电为null')
slot('barometer.indicated_pa', nullable(number('Pa')), 'Pa', 'sensor', 'barometer', '内置气压计读数（10Pa量化）；断电为null', reference='绝对大气压。')
slot('sensor.raw_counts', nullable(INT), '1', 'sensor', 'coulox_sensor', '传感器当前电流原始计数（1计数≈10pA，含与检测读数同源的相关测量噪声）；断电为null')
slot('sensor.route', enum(['bypass', 'A', 'B', 'Z']), '1', 'derived', 'flow_controllers', '当前送往传感器的气流：旁路/A池/B池/ReZero')
slot('sequence.slot_remaining_s', nullable(number('s')), 's', 'derived', 'controller_board', '当前检测时段剩余时间；无序列为null')
for c in 'ab':
    C_ = c.upper()
    slot(f'drawer_{c}.position_m', number('m', 0.0, DRAWER_TRAVEL_M), 'm', 'scene', f'drawer_{c}', f'{C_}池抽屉拉出距离（0=推到底，0.190m=全开）', 'evaluator',
         '资产坐标−Y方向（朝操作者）平移。')
    slot(f'clamp_{c}.position_m', number('m', 0.0, CLAMP_TRAVEL_M), 'm', 'scene', f'clamp_{c}', f'{C_}池气动压板下压行程（0=松开，0.003m=夹紧）', 'evaluator', '资产坐标−Z方向平移。')
    slot(f'cell_{c}.drawer_closed', BOOL, '1', 'derived', f'drawer_{c}', f'{C_}池抽屉已推到底且未在移动')
    slot(f'cell_{c}.clamped', BOOL, '1', 'derived', f'clamp_{c}', f'{C_}池测试盒已气动夹紧（动作完成）')
    slot(f'cell_{c}.clamp_moving', BOOL, '1', 'derived', f'clamp_{c}', f'{C_}池气动压板正在夹紧或松开行程中（此时装卸键被拒绝）')
    slot(f'cell_{c}.method_configured', BOOL, '1', 'derived', 'controller_board', f'{C_}池已用test.configure设定测试方法')
    slot(f'cell_{c}.indicator_on', BOOL, '1', 'derived', f'lamp_cell_{c}', f'{C_}池状态灯（本资产映射：已夹紧且通电=亮）')
    slot(f'cell_{c}.film_mounted', BOOL, '1', 'derived', f'cartridge_{c}', f'{C_}池测试盒内装有膜样')
    slot(f'cell_{c}.phase', CELL_PHASE, '1', 'derived', 'controller_board', f'{C_}池测试阶段（单池零点/调理/检测/结束零点/完成；前面板公开显示）')
    slot(f'cell_{c}.exam_count', INT, '1', 'derived', 'controller_board', f'{C_}池本次测试已完成的检测次数（前面板公开显示）')
    slot(f'cell_{c}.last_otr_mol_m2_s', nullable(number('mol/(m^2*s)')), 'mol/(m^2*s)', 'sensor', 'controller_board',
         f'{C_}池最近一次检测报告的OTR（量化后；未检测为null；前面板公开显示）', reference=OTR_REF)
    slot(f'cell_{c}.final_otr_mol_m2_s', nullable(number('mol/(m^2*s)')), 'mol/(m^2*s)', 'sensor', 'controller_board', f'{C_}池最近一次完成测试的最终OTR（扣除单池零点；前面板公开显示）',
         reference=OTR_REF)
    for k, u, m in [('flux_mol_s', 'mol/s', '膜向载气腔的真实O₂通量（膜出口面）'), ('film_o2_mol', 'mol', '膜内溶解氧总量'),
                    ('test_side_o2_pa', 'Pa', '测试气腔O₂分压'), ('carrier_side_o2_mol', 'mol', '载气腔内O₂量'), ('leak_mol_s', 'mol/s', '边缘泄漏与环境渗入之和')]:
        slot(f'truth.cell_{c}.{k}', number(u), u, 'process_truth', f'cartridge_{c}' if k != 'test_side_o2_pa' else f'cell_head_{c}', f'{C_}池' + m, 'evaluator',
             '测试气腔内O₂的绝对分压（全混体积）。' if k == 'test_side_o2_pa' else None)
for k, u, c, m in [('truth.block_temperature_k', 'K', 'thermal_module', '测试池块真实温度'), ('truth.rh_fraction', '1', 'rh_generator', '气流真实相对湿度（含静态偏差）'),
                   ('truth.sensor_current_a', 'A', 'coulox_sensor', '传感器真实电流（无量化与噪声）'), ('truth.sensor_input_mol_s', 'mol/s', 'coulox_sensor', '进入传感器的O₂摩尔流'),
                   ('truth.tec_power_w', 'W', 'thermal_module', '热电模块瞬时热功率（正=加热）')]:
    slot(k, number(u), u, 'process_truth', c, m, 'evaluator')
slot('power.angle_rad', number('rad'), 'rad', 'scene', 'power_switch', '后置电源摇臂开关姿态：0关、0.30rad开', 'evaluator')


def analyze_otr(readings_mol_m2_s, individual_zero_mol_m2_s, window, driving_partial_pressure_pa, thickness_m, convergence_percent):
    """Pure steady-state arithmetic on supplied observations (no process truth)."""
    k = min(window, len(readings_mol_m2_s))
    last = readings_mol_m2_s[-k:]
    mean = sum(last) / k
    zero = individual_zero_mol_m2_s or 0.0
    ss = mean - zero
    rep = None
    if k >= 2 and abs(mean) > 0:
        sd = math.sqrt(sum((x - mean) ** 2 for x in last) / (k - 1))
        rep = sd / abs(mean)
    converged = k >= 2 and all(abs(last[i] - last[i - 1]) <= convergence_percent / 100.0 * max(abs(last[i]), 1e-30) for i in range(1, k))
    permeance = ss / driving_partial_pressure_pa
    perm = None if thickness_m is None else permeance * thickness_m
    return dict(steady_state_otr_mol_m2_s=ss, steady_state_otr_cc_m2_day=ss * CC_DAY_PER_MOL_S, repeatability_relative=rep, converged=converged,
                permeance_mol_m2_s_pa=permeance, permeability_mol_m_per_m2_s_pa=perm, permeability_barrer=None if perm is None else perm / BARRER_SI,
                evidence='mean of the last window readings minus the supplied individual zero; permeance = OTR / supplied driving partial pressure; '
                         'permeability = permeance x supplied thickness (Barrer = 3.3464e-16 mol m/(m2 s Pa))')


BARRER_SI = 1e-10 * (1e-6 / V_STP_M3_MOL) * 1e-2 / (1e-4 * 101325.0 / 76.0)


def is_stable(values, k, percent, floor):
    """Convergence by cycles: over the last k intervals every step and the cumulative drift stay within max(percent of the value, floor)."""
    if len(values) < k + 1:
        return False
    last = values[-(k + 1):]
    allow = max(percent / 100.0 * abs(last[-1]), floor)
    return all(abs(last[i] - last[i - 1]) <= allow for i in range(1, len(last))) and abs(last[-1] - last[0]) <= allow


# ---------------------------------------------------------------- film numerics
def film_chamber_step(c, dx, D, c_top, n_h, sigma, area, q_in, kh, dt):
    """Backward-Euler film and well-mixed carrier chamber solved together (one tridiagonal system of n+1 unknowns).

    Unknowns: film cells x_i (mol/m3) and y = sigma n_h, the film-equivalent concentration of the chamber O2 (sigma = S R T / V_h).
    Chamber: n_h' - n_h = dt (A J_bot' + q_in - kh n_h').  The matrix is a strictly diagonally dominant M-matrix, so the step is
    unconditionally stable and non-negative for any film permeance and chamber volume, and exactly conservative:
    sum((x' - x) dx) A + (n_h' - n_h) = dt (A J_top + q_in - kh n_h').  Returns (x', n_h', J_top_in, J_bot_out)."""
    n = len(c)
    r = D * dt / (dx * dx)
    beta = 2.0 * dt * area * D * sigma / dx
    N = n + 1
    a = [-r] * N
    b = [1.0 + 2.0 * r] * N
    e = [-r] * N
    d = list(c) + [sigma * n_h + dt * sigma * q_in]
    a[0] = 0.0
    b[0] += r
    d[0] += 2.0 * r * c_top
    b[n - 1] += r
    e[n - 1] = -2.0 * r
    a[n] = -beta
    b[n] = 1.0 + beta + dt * kh
    e[n] = 0.0
    cp = [0.0] * N
    dp = [0.0] * N
    cp[0] = e[0] / b[0]
    dp[0] = d[0] / b[0]
    for i in range(1, N):
        m = b[i] - a[i] * cp[i - 1]
        cp[i] = e[i] / m
        dp[i] = (d[i] - a[i] * dp[i - 1]) / m
    x = [0.0] * N
    x[-1] = dp[-1]
    for i in range(N - 2, -1, -1):
        x[i] = dp[i] - cp[i] * x[i + 1]
    y = x.pop()
    return x, y / sigma, 2.0 * D * (c_top - x[0]) / dx, 2.0 * D * (x[-1] - y) / dx


def film_step(c, dx, D, c_top, c_bot, dt):
    """Backward-Euler finite-volume diffusion step (Dirichlet faces at +/- dx/2); returns (new c, J_top_in, J_bot_out) in mol/(m2 s).

    Exactly conservative: sum((c_new - c_old) dx) = (J_top_in - J_bot_out) dt."""
    n = len(c)
    r = D * dt / (dx * dx)
    a = [-r] * n
    b = [1.0 + 2.0 * r] * n
    e = [-r] * n
    d = list(c)
    b[0] = 1.0 + 3.0 * r; d[0] += 2.0 * r * c_top; a[0] = 0.0
    b[-1] = 1.0 + 3.0 * r; d[-1] += 2.0 * r * c_bot; e[-1] = 0.0
    if n == 1:
        b[0] = 1.0 + 4.0 * r
        d[0] = c[0] + 2.0 * r * (c_top + c_bot)
    cp = [0.0] * n
    dp = [0.0] * n
    cp[0] = e[0] / b[0]
    dp[0] = d[0] / b[0]
    for i in range(1, n):
        m = b[i] - a[i] * cp[i - 1]
        cp[i] = e[i] / m
        dp[i] = (d[i] - a[i] * dp[i - 1]) / m
    x = [0.0] * n
    x[-1] = dp[-1]
    for i in range(n - 2, -1, -1):
        x[i] = dp[i] - cp[i] * x[i + 1]
    j_top = 2.0 * D * (c_top - x[0]) / dx
    j_bot = 2.0 * D * (x[-1] - c_bot) / dx
    return x, j_top, j_bot


class AmetekOxtranRuntime:
    """One OX-TRAN 2/22 L instance; the host clock is the only time source (``step``)."""

    def __init__(self, instance_id='oxtran_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.gas = None
        self.instrument = None
        self.power = False
        self.target_k = 296.15
        self.T = 296.15
        self.integral_w = 0.0
        self.tec_w = 0.0
        self.rh_target = 0.0
        self.rh_model = 0.0
        self.rng = random.Random(0)
        self.cells = {c: dict(cfg=None, drawer=0.0, drawer_move=None, clamp=0.0, clamp_move=None, clamped=False, film=None, conc=None,
                              p_up=0.0, n_h=0.0, test=None, last_otr=None, final_otr=None, test_counter=0, exam_count=0, phase='idle', source=None,
                              flux=0.0, leak=0.0) for c in CELLS}
        self.sensor_I = 0.0        # total sensor current = O2 part + background part (noise-free, evaluator truth)
        self.sensor_I_o2 = 0.0
        self.sensor_I_bg = 0.0
        self.noise_abs = 0.0       # correlated measurement noise states (A, relative)
        self.noise_rel = 0.0
        self.sensor_in = 0.0
        self.route = 'bypass'
        self.over_range = False
        self.seq = None            # dict(kind, start_ns, end_ns) of the running slot
        self.exams_since_zero = 0
        self.zero_current = None   # measured baseline (A), None before the first ReZero
        self.zero_list = []
        self.records = {}
        self.operations = {}
        self._principals = {}
        self._requests = {}
        self._owners = {}
        self._cancel_operations = {}
        self.book = {k: 0.0 for k in LEDGER_KEYS if k not in ('film_content_mol', 'film_residual_mol', 'carrier_chamber_mol', 'carrier_residual_balance_mol',
                                                               'sensor_in_transit_mol', 'sensor_balance_residual_mol', 'block_thermal_j', 'energy_residual_j')}
        self._comp = {k: 0.0 for k in self.book}     # Neumaier compensation of every ledger sum

    # ------------------------------------------------------------ helpers
    def _add(self, key, value):
        """Compensated (Neumaier) accumulation of a ledger term."""
        s_ = self.book[key]
        t_ = s_ + value
        self._comp[key] += (s_ - t_) + value if abs(s_) >= abs(value) else (value - t_) + s_
        self.book[key] = t_

    def _measured_current(self):
        """Sensor current as the instrument measures it: truth plus correlated additive and proportional noise."""
        return self.sensor_I + self.noise_abs + self.noise_rel * max(self.sensor_I_o2, 0.0)

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
        return AmetekOxtranSession(self, name)

    def _ambient(self):
        return self.plant['ambient_k'] if self.plant else 296.15

    def _baro(self):
        return self.plant['barometric_pa'] if self.plant else STD_PRESSURE_PA

    def _air_o2_pa(self):
        Ta = self._ambient()
        pw = (self.plant['ambient_rh'] if self.plant else 0.0) * saturation_pressure_pa(Ta)
        return AIR_O2_FRACTION * (self._baro() - pw)

    def _rh_true(self):
        if self.rh_model <= 0.0:
            return 0.0
        return min(1.0, max(0.0, self.rh_model + (self.plant['rh_bias'] if self.plant else 0.0)))

    def _carrier_residual_fraction(self):
        """O2 left in the carrier after the H2/catalyst stage (manual: 1-3 % H2 needed for catalytic removal)."""
        g = self.gas
        if g is None:
            return 0.0
        removal = self.plant['catalyst_removal_efficiency'] if g['carrier_h2_fraction'] >= 0.01 else 0.0
        return g['carrier_o2_impurity_fraction'] * (1.0 - removal)

    def _carrier_ok(self):
        g = self.gas
        return g is not None and SUPPLY_GAUGE_PA['carrier'][0] <= g['carrier_supply_gauge_pa'] <= SUPPLY_GAUGE_PA['carrier'][1]

    def _test_gas_ok(self):
        g = self.gas
        return g is not None and SUPPLY_GAUGE_PA['test'][0] <= g['test_supply_gauge_pa'] <= SUPPLY_GAUGE_PA['test'][1]

    def _supply_ok(self):
        return self._carrier_ok() and self._test_gas_ok()

    def _transport(self, film, T, aw):
        """Effective P, D (and S = P/D) of a mounted film at temperature T and water activity aw."""
        inv = 1.0 / T - 1.0 / film['T_ref']
        beta = film['beta'] * aw
        P = film['P_ref'] * math.exp(-film['E_P'] / GAS_CONSTANT * inv + beta)
        D = film['D_ref'] * math.exp(-film['E_D'] / GAS_CONSTANT * inv + beta)
        return P, D, P / D

    def _cell_open(self, cell):
        c = self.cells[cell]
        return not c['clamped'] or c['clamp_move'] is not None

    def _test_active(self, cell):
        t = self.cells[cell]['test']
        return t is not None and t['phase'] == 'running'

    def _indicated_T(self):
        return quantize(self.T + self.plant['temperature_sensor_bias_k'], TEMPERATURE_DISPLAY_RESOLUTION_K) if (self.power and self.plant) else None

    def _film_content(self, cell):
        c = self.cells[cell]
        if c['film'] is None:
            return 0.0
        return sum(c['conc']) * c['film']['dx'] * c['film']['area']

    def read(self, visibility='agent'):
        d = {'clock.time_s': self.time_s, 'plant.configured': self.plant is not None, 'gas.configured': self.gas is not None,
             'instrument.configured': self.instrument is not None, 'power.enabled': self.power, 'gas.supply_ok': self._supply_ok(), 'sensor.over_range': self.over_range,
             'thermal.target_k': self.target_k, 'thermal.indicated_k': self._indicated_T(), 'humidity.target_fraction': self.rh_target,
             'humidity.indicated_fraction': quantize(self.rh_model, RH_DISPLAY_RESOLUTION) if self.power else None,
             'barometer.indicated_pa': quantize(self._baro(), BAROMETER_RESOLUTION_PA) if self.power else None,
             'sensor.raw_counts': self._panel_counts(), 'sensor.route': self.route,
             'sequence.slot_remaining_s': None if self.seq is None else (self.seq['end_ns'] - self.time_ns) / 1e9,
             'truth.block_temperature_k': self.T, 'truth.rh_fraction': self._rh_true(), 'truth.sensor_current_a': self.sensor_I,
             'truth.sensor_input_mol_s': self.sensor_in, 'truth.tec_power_w': self.tec_w, 'power.angle_rad': POWER_SWITCH_ON_RAD if self.power else 0.0}
        for cell in CELLS:
            c = self.cells[cell]
            k = cell.lower()
            p = self.plant
            d.update({f'drawer_{k}.position_m': c['drawer'], f'clamp_{k}.position_m': c['clamp'],
                      f'cell_{k}.drawer_closed': c['drawer'] == 0.0 and c['drawer_move'] is None, f'cell_{k}.clamped': c['clamped'] and c['clamp_move'] is None, f'cell_{k}.clamp_moving': c['clamp_move'] is not None,
                      f'cell_{k}.method_configured': c['cfg'] is not None,
                      f'cell_{k}.indicator_on': bool(self.power and c['clamped'] and c['clamp_move'] is None), f'cell_{k}.film_mounted': c['film'] is not None,
                      f'cell_{k}.phase': c['phase'], f'cell_{k}.exam_count': c['exam_count'], f'cell_{k}.last_otr_mol_m2_s': c['last_otr'],
                      f'cell_{k}.final_otr_mol_m2_s': c['final_otr'],
                      f'truth.cell_{k}.flux_mol_s': c['flux'], f'truth.cell_{k}.film_o2_mol': self._film_content(cell), f'truth.cell_{k}.test_side_o2_pa': c['p_up'],
                      f'truth.cell_{k}.carrier_side_o2_mol': c['n_h'], f'truth.cell_{k}.leak_mol_s': c['leak']})
        return {k: copy.deepcopy(v) for k, v in d.items() if visibility != 'agent' or SLOTS[k]['visibility'] == 'agent'}

    def _panel_counts(self):
        if not self.power:
            return None
        I = self._measured_current()
        return int(round(I / COUNT_A)) if math.isfinite(I) else None

    def ledger(self):
        b = {k: v + self._comp[k] for k, v in self.book.items()}
        content = sum(self._film_content(c) for c in CELLS)
        chamber = sum(self.cells[c]['n_h'] for c in CELLS)
        b['film_content_mol'] = content
        b['carrier_chamber_mol'] = chamber
        b['film_residual_mol'] = b['film_mounted_mol'] - b['film_unmounted_mol'] + b['film_air_exchange_mol'] + b['film_test_side_in_mol'] - b['film_to_carrier_mol'] - content
        b['carrier_residual_balance_mol'] = b['carrier_in_mol'] - b['to_sensor_mol'] - b['to_vent_mol'] - b['released_to_air_mol'] - chamber
        b['sensor_in_transit_mol'] = (self.plant['sensor_time_constant_s'] * self.sensor_I_o2 / (4.0 * FARADAY)) if self.plant else 0.0
        b['sensor_balance_residual_mol'] = (b['sensor_input_mol'] - b['sensor_consumed_mol'] - b['sensor_passthrough_mol'] - b['sensor_in_transit_mol']
                                            - b['sensor_discarded_mol'])
        b['block_thermal_j'] = (self.plant['block_heat_capacity_j_k'] * (self.T - self._ambient())) if self.plant else 0.0
        b['energy_residual_j'] = b['tec_heat_j'] - b['ambient_exchange_j'] - b['block_thermal_j']
        return b

    # ------------------------------------------------------------ test bookkeeping
    def _new_test(self, cell):
        c = self.cells[cell]
        c['test_counter'] += 1
        op = f'{self.instance_id}:test:{cell}:{c["test_counter"]}'
        cfg = copy.deepcopy(c['cfg'])
        inst = copy.deepcopy(self.instrument)
        first = 'zero_start' if cfg['individual_zero'] in ('start', 'both') else ('conditioning' if cfg['conditioning_hours'] > 0 else 'testing')
        t = dict(op=op, cell=cell, cfg=cfg, inst=inst, phase='running', cell_phase=first, start_ns=self.time_ns, end_ns=None, readings=[], zeros=[],
                 individual_zero=None, zero_run=[], zero_stable=None, conditioning_end_ns=None, converged=False, validity='not_completed', terminal_reason=None, final=None,
                 temperature_excursion=False, humidity_excursion=False, target_k=self.target_k, target_rh=self.rh_target, source=c['source'])
        if first == 'conditioning':
            t['conditioning_end_ns'] = self.time_ns + round(cfg['conditioning_hours'] * 3600e9)
        c['test'] = t
        c['phase'] = first
        c['exam_count'] = 0
        c['last_otr'] = None
        self.operations[op] = t
        self.records[op] = self._result_of(t)
        return op

    def _result_of(self, t):
        cfg = t['cfg']
        fin = t['final']
        return dict(operation_id=t['op'], cell=t['cell'], phase=t['phase'], cell_phase=t['cell_phase'],
                    elapsed_s=((t['end_ns'] if t['end_ns'] is not None else self.time_ns) - t['start_ns']) / 1e9, exam_count=len([r for r in t['readings'] if r['kind'] == 'exam']),
                    readings=copy.deepcopy(t['readings']), rezero_readings=copy.deepcopy(t['zeros']), individual_zero_mol_m2_s=t['individual_zero'],
                    final_otr_mol_m2_s=None if fin is None else fin['otr'], final_otr_cc_m2_day=None if fin is None else fin['otr_cc'],
                    final_otr_cc_pkg_day=None if fin is None else fin['otr_pkg_cc'], converged=t['converged'],
                    repeatability_relative=None if fin is None else fin['rep'], within_repeatability_spec=None if fin is None else fin['rep_ok'],
                    permeance_mol_m2_s_pa=None if fin is None else fin['permeance'], permeability_mol_m_per_m2_s_pa=None if fin is None else fin['permeability'],
                    permeability_barrer=None if (fin is None or fin['permeability'] is None) else fin['permeability'] / BARRER_SI,
                    conditions=dict(target_k=t['target_k'], target_rh=t['target_rh'], permeant_concentration_percent=t['inst']['permeant_concentration_percent'],
                                    declared_area_m2=cfg['declared_area_m2'], declared_thickness_m=cfg['declared_thickness_m'], report_basis=cfg['report_basis'],
                                    carrier_flow_sccm=CARRIER_FLOW_SCCM, exam_minutes=cfg['exam_minutes']),
                    validity=t['validity'], terminal_reason=t['terminal_reason'], template_source=t['source'],
                    evidence='synthetic_forward_model; quantised Coulox sensor counts with ReZero baseline, barometric (760 mmHg) and permeant normalisation; '
                             'film transport from the selected experimental P/D template with explicit temperature/humidity/leak/sensor priors; not native OX-TRAN data')

    def _finish(self, cell, phase, reason):
        c = self.cells[cell]
        t = c['test']
        cfg = t['cfg']
        exams = [r for r in t['readings'] if r['kind'] == 'exam']
        fin = None
        if exams:
            k = cfg['convergence_count'] if cfg['test_mode'] == 'convergence' else 1
            window = [r['otr_mol_m2_s'] for r in exams[-k:]]
            zero = t['individual_zero'] or 0.0
            otr = sum(window) / len(window) - zero
            last3 = [r['otr_mol_m2_s'] for r in exams[-3:]]
            rep = None
            rep_ok = None
            if len(last3) >= 2:
                m = sum(last3) / len(last3)
                sd = math.sqrt(sum((x - m) ** 2 for x in last3) / (len(last3) - 1))
                rep = sd / abs(m) if abs(m) > 0 else None
                allow = max(REPEATABILITY_CC_M2_DAY / CC_DAY_PER_MOL_S * TEST_AREA_M2 / cfg['declared_area_m2'], REPEATABILITY_REL * abs(m))
                rep_ok = sd <= allow
            permeance = otr / STD_PRESSURE_PA
            fin = dict(otr=otr, otr_cc=otr * CC_DAY_PER_MOL_S, otr_pkg_cc=otr * cfg['declared_area_m2'] * CC_DAY_PER_MOL_S, rep=rep, rep_ok=rep_ok,
                       permeance=permeance, permeability=None if cfg['declared_thickness_m'] is None else permeance * cfg['declared_thickness_m'])
        if phase == 'cancelled':
            validity = 'cancelled'
        elif reason == 'SENSOR_OVER_RANGE':
            validity = 'sensor_over_range'
        elif fin is None:
            validity = 'not_completed'
        elif t['temperature_excursion']:
            validity = 'temperature_out_of_tolerance'
        elif t['humidity_excursion']:
            validity = 'humidity_out_of_tolerance'
        elif (cfg['test_mode'] == 'convergence' and not t['converged']) or t['zero_stable'] is False:
            validity = 'not_converged'
        else:
            lo, hi = OTR_RANGE_CC_M2_DAY
            scale = TEST_AREA_M2 / cfg['declared_area_m2']
            validity = 'below_range' if fin['otr_cc'] < lo * scale else 'over_range' if fin['otr_cc'] > hi * scale else 'valid'
        t.update(phase=phase, terminal_reason=reason, validity=validity, end_ns=self.time_ns, final=fin, cell_phase={'succeeded': 'succeeded', 'failed': 'failed'}.get(phase, 'cancelled'))
        c['phase'] = t['cell_phase']
        c['final_otr'] = None if fin is None or validity != 'valid' else fin['otr']
        self.records[t['op']] = self._result_of(t)

    def _result(self, cell):
        t = self.cells[cell]['test']
        if t is None:
            return dict(operation_id=None, cell=cell, phase='idle', cell_phase='idle', elapsed_s=0.0, exam_count=0, readings=[], rezero_readings=[],
                        individual_zero_mol_m2_s=None, final_otr_mol_m2_s=None, final_otr_cc_m2_day=None, final_otr_cc_pkg_day=None, converged=False,
                        repeatability_relative=None, within_repeatability_spec=None, permeance_mol_m2_s_pa=None, permeability_mol_m_per_m2_s_pa=None,
                        permeability_barrer=None, conditions=dict(target_k=None, target_rh=self.rh_target, permeant_concentration_percent=None, declared_area_m2=None,
                                                                  declared_thickness_m=None, report_basis=None, carrier_flow_sccm=CARRIER_FLOW_SCCM, exam_minutes=None),
                        validity='not_completed', terminal_reason=None, template_source=None, evidence='no_measurement')
        if t['phase'] == 'running':
            return self._result_of(t)
        return copy.deepcopy(self.records[t['op']])

    # ------------------------------------------------------------ sequencer
    def _active_cells(self):
        return [c for c in CELLS if self._test_active(c)]

    def _slot_minutes(self, kind):
        if kind == 'Z':
            return self.instrument['rezero_exam_minutes']
        cfg = self.cells[kind]['cfg']
        if self._test_active(kind):
            return self.cells[kind]['test']['cfg']['exam_minutes']
        other = [self.cells[c]['test']['cfg']['exam_minutes'] for c in self._active_cells()]
        return other[0] if other else (cfg['exam_minutes'] if cfg else 30.0)

    def _next_slot(self, previous):
        inst = self.instrument
        if inst['rezero_enabled'] and (self.zero_current is None or self.exams_since_zero >= inst['rezero_frequency']):   # never an exam without a baseline
            kind = 'Z'
        elif previous in (None, 'Z'):
            kind = self._last_cell_slot_next()
        else:
            kind = 'B' if previous == 'A' else 'A'
        if kind == 'Z':
            self.exams_since_zero = 0
        self.seq = dict(kind=kind, start_ns=self.time_ns, end_ns=self.time_ns + max(1, round(self._slot_minutes(kind) * 60e9)))
        self._route()

    def _last_cell_slot_next(self):
        last = getattr(self, '_last_cell_slot', None)
        return 'A' if last in (None, 'B') else 'B'

    def _route(self):
        """Valve routing for the current slot (sensor bypassed during conditioning, idle slots and over-range protection).

        ``routed_since_ns`` remembers when the sensor was switched onto the slot's stream: a slot whose stream was switched in
        part-way (conditioning ended, a test started, over-range recovery) is not a complete exam and records nothing."""
        s = self.seq
        route = 'bypass'
        if s is not None and self.power and not self.over_range:
            if s['kind'] == 'Z':
                route = 'Z'
            elif self._test_active(s['kind']) and self.cells[s['kind']]['phase'] in ('zero_start', 'testing', 'zero_end'):
                route = s['kind']
        if s is not None:
            if route == 'bypass':
                s['routed_since_ns'] = None
            elif route != self.route or s.get('routed_since_ns') is None:
                s['routed_since_ns'] = self.time_ns
        self.route = route

    def _measure_current(self):
        return quantize(self._measured_current(), COUNT_A)

    def _complete_slot(self):
        s = self.seq
        kind = s['kind']
        now = self.time_s
        full = self.route == kind and s.get('routed_since_ns') == s['start_ns']
        if kind == 'Z':
            if full:
                I = self._measure_current()
                self.zero_current = I
                z = dict(time_s=quantize(now, 1.0), sensor_counts=int(round(I / COUNT_A)), current_a=I)
                self.zero_list.append(z)
                for cell in self._active_cells():
                    t = self.cells[cell]['test']
                    if len(t['zeros']) < MAX_READINGS:
                        t['zeros'].append(dict(z, time_s=quantize(now - t['start_ns'] / 1e9, 1.0)))   # same time base as the exams
        else:
            self._last_cell_slot = kind
            self.exams_since_zero += 1
            c = self.cells[kind]
            t = c['test']
            if t is not None and t['phase'] == 'running' and full:
                self._record_exam(kind)
            elif t is not None and t['phase'] == 'running' and c['phase'] == 'conditioning':
                pass
        self.seq = None
        self.route = 'bypass'
        if self._active_cells() and self.power:
            self._next_slot(kind)

    def _record_exam(self, cell):
        c = self.cells[cell]
        t = c['test']
        cfg, inst = t['cfg'], t['inst']
        p = self.plant
        I = self._measure_current()
        base = self.zero_current if self.zero_current is not None else 0.0
        n_meas = (I - base) / (4.0 * FARADAY)
        P_used = self._baro() if inst['barometer_mode'] == 'auto' else inst['manual_barometer_pa']
        pkg = n_meas * (STD_PRESSURE_PA / P_used) * (100.0 / inst['permeant_concentration_percent'])
        pkg_cc = quantize(pkg * CC_DAY_PER_MOL_S, OTR_RESOLUTION_CC_M2_DAY * TEST_AREA_M2)
        pkg = pkg_cc / CC_DAY_PER_MOL_S
        otr = pkg / cfg['declared_area_m2']
        kind = 'individual_zero' if c['phase'] in ('zero_start', 'zero_end') else 'exam'
        Ti = self._indicated_T()
        row = dict(index=len(t['readings']), kind=kind, time_s=quantize(self.time_s - t['start_ns'] / 1e9, 1.0), sensor_counts=int(round(I / COUNT_A)),
                   otr_mol_m2_s=otr, otr_cc_m2_day=otr * CC_DAY_PER_MOL_S, otr_mol_pkg_s=pkg, otr_cc_pkg_day=pkg_cc, temperature_k=Ti,
                   rh_fraction=quantize(self.rh_model, RH_DISPLAY_RESOLUTION), barometric_pa=quantize(self._baro(), BAROMETER_RESOLUTION_PA))
        if len(t['readings']) >= MAX_READINGS:
            self._finish(cell, 'failed', 'TOO_MANY_READINGS')
            return
        t['readings'].append(row)
        floor = 2.0 * OTR_RESOLUTION_CC_M2_DAY / CC_DAY_PER_MOL_S * TEST_AREA_M2 / cfg['declared_area_m2']
        if kind == 'individual_zero':
            # the zero is repeated until it is stable: after the test gas is removed the film keeps desorbing dissolved O2
            t['zero_run'].append(otr)
            k = cfg['convergence_count']
            run = t['zero_run']
            stable = is_stable(run, k, cfg['convergence_percent'], floor)
            if stable or len(run) >= cfg['max_cycles']:
                t['individual_zero'] = otr
                t['zero_stable'] = t['zero_stable'] and stable if t['zero_stable'] is not None else stable
                t['zero_run'] = []
                if c['phase'] == 'zero_start':
                    nxt = 'conditioning' if cfg['conditioning_hours'] > 0 else 'testing'
                    c['phase'] = t['cell_phase'] = nxt
                    if nxt == 'conditioning':
                        t['conditioning_end_ns'] = self.time_ns + round(cfg['conditioning_hours'] * 3600e9)
                else:
                    self._finish(cell, 'succeeded', 'TEST_COMPLETE')
            return
        c['exam_count'] += 1
        c['last_otr'] = otr
        if Ti is not None and abs(Ti - t['target_k']) > cfg['temperature_tolerance_k'] + 1e-9:
            t['temperature_excursion'] = True
        if abs(self.rh_target - t['target_rh']) > 1e-12 or abs(self.rh_model - t['target_rh']) > RH_ACCURACY + 1e-9:
            t['humidity_excursion'] = True
        exams = [r['otr_mol_m2_s'] for r in t['readings'] if r['kind'] == 'exam']
        done = False
        if cfg['test_mode'] == 'standard' and len(exams) >= cfg['cycles']:
            done = True
        elif cfg['test_mode'] == 'convergence':
            t['converged'] = is_stable(exams, cfg['convergence_count'], cfg['convergence_percent'], floor)
            done = t['converged'] or len(exams) >= cfg['max_cycles']
        if done:
            if cfg['individual_zero'] in ('end', 'both'):
                c['phase'] = t['cell_phase'] = 'zero_end'
            else:
                self._finish(cell, 'succeeded', 'TEST_COMPLETE')

    def _abort_test(self, cell):
        """Cancellation of the asynchronous test operation: always an abort (readings kept), unlike the graceful continuous stop."""
        self._finish(cell, 'cancelled', 'USER_CANCEL')
        self._route()
        self.revision += 1

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
            return self._result(a['cell'])
        if k == 'ledger.read':
            return self.ledger()
        if k == 'provenance.read':
            return dict(cell_a=self.cells['A']['source'], cell_b=self.cells['B']['source'],
                        operations=[dict(operation_id=op, source=t['source']) for op, t in list(self.operations.items())[-MAX_PROVENANCE:]])
        if k == 'analysis.otr':
            return analyze_otr(**a)
        if k == 'sim.step':
            self.step(a['dt_s'])
            return dict(time_s=self.time_s)
        if k == 'plant.configure':
            if self.plant is not None:
                raise ActionRejected('PLANT_ALREADY_CONFIGURED')
            if not a['accept_engineering']:
                raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            if a['block_heat_capacity_j_k'] / (a['controller_gain_w_k'] + a['block_loss_w_k']) < 5 * MAX_SUBSTEP_S:
                raise ActionRejected('THERMAL_TIME_CONSTANT_OUTSIDE_NUMERICAL_DOMAIN')
            self.plant = a
            self.rng = random.Random(a['noise_seed'])
            self.T = a['ambient_k']
            self.target_k = min(max(TEMPERATURE_RANGE_K[0], a['ambient_k']), TEMPERATURE_RANGE_K[1])
            out = dict(configured=True)
        elif k == 'gas.configure':
            if not a['accept_gas_supply']:
                raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            if any(self._test_active(c) for c in CELLS):
                raise ActionRejected('BUSY')
            self.gas = a
            self.zero_current = None                          # a new carrier/test supply invalidates the ReZero baseline
            out = dict(configured=True)
        elif k == 'instrument.configure':
            if a['barometer_mode'] == 'manual' and a['manual_barometer_pa'] is None:
                raise ActionRejected('MANUAL_BAROMETER_REQUIRED')
            if any(self._test_active(c) for c in CELLS):
                raise ActionRejected('BUSY')
            self.instrument = a
            out = dict(configured=True)
        elif k == 'test.configure':
            if self._test_active(a['cell']):
                raise ActionRejected('BUSY')
            if a['test_mode'] == 'convergence' and a['max_cycles'] < a['convergence_count'] + 1:
                raise ActionRejected('INVALID_ARGUMENT')
            cell = a.pop('cell')
            self.cells[cell]['cfg'] = a
            out = dict(configured=True)
        elif k == 'power.set':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if not a['enabled'] and self.power:
                for c in CELLS:
                    if self._test_active(c):
                        self._finish(c, 'cancelled', 'POWER_OFF')
                self.seq = None
                self.route = 'bypass'
                self.tec_w = 0.0
                self.integral_w = 0.0
                self.zero_current = None                      # the sensor baseline must be re-measured after a restart
                self._add('sensor_discarded_mol', self.plant['sensor_time_constant_s'] * self.sensor_I_o2 / (4.0 * FARADAY))
                self.sensor_I = self.sensor_I_o2 = self.sensor_I_bg = 0.0
                self.noise_abs = self.noise_rel = 0.0
                for c in CELLS:
                    cc = self.cells[c]
                    if cc['clamp_move'] is not None:          # a clamp stroke in progress stops where it is and the cell is not sealed
                        cc['clamp_move'] = None
                        if cc['clamped']:
                            self._open_cell(c)                # release stroke interrupted: the seal is already broken
                        cc['clamped'] = False
            if a['enabled'] and not self.power:
                self.noise_abs = self.plant['sensor_noise_a'] * self.rng.gauss(0.0, 1.0)          # stationary start of the noise process
                self.noise_rel = self.plant['sensor_relative_noise'] * self.rng.gauss(0.0, 1.0)
            self.power = a['enabled']
            out = dict(enabled=self.power)
        elif k == 'thermal.set_temperature':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            self.target_k = a['target_k']
            out = dict(target_k=self.target_k)
        elif k == 'humidity.set':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if 0.0 < a['rh_fraction'] < RH_RANGE[0]:
                raise ActionRejected('RH_OUTSIDE_CONTROLLED_RANGE')
            self.rh_target = a['rh_fraction']
            out = dict(rh_fraction=self.rh_target)
        elif k == 'drawer.move':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            c = self.cells[a['cell']]
            if c['clamped'] or c['clamp_move'] is not None or c['clamp'] > 0.0:
                raise ActionRejected('CARTRIDGE_CLAMPED')          # the platen must be fully raised before the drawer slides
            target = DRAWER_TRAVEL_M if a['open'] else 0.0
            c['drawer_move'] = None if c['drawer'] == target else dict(start_ns=self.time_ns, x0=c['drawer'], target=target)
            out = dict(cell=a['cell'], target_m=target)
        elif k == 'sample.mount':
            out = self._mount(a)
        elif k == 'sample.unmount':
            c = self.cells[a['cell']]
            if c['film'] is None:
                raise ActionRejected('NO_FILM')
            if not (c['drawer'] == DRAWER_TRAVEL_M and c['drawer_move'] is None) or c['clamped'] or c['clamp_move'] is not None:
                raise ActionRejected('DRAWER_NOT_OPEN')
            removed = self._film_content(a['cell'])
            self._add('film_unmounted_mol', removed)
            c['film'] = None
            c['conc'] = None
            c['source'] = None
            out = dict(cell=a['cell'], removed_film_o2_mol=removed)
        elif k == 'cell.load_unload':
            if self.plant is None:
                raise ActionRejected('PLANT_REQUIRED')
            if not self.power:
                raise ActionRejected('POWER_OFF')
            cell = a['cell']
            c = self.cells[cell]
            if c['clamp_move'] is not None:
                raise ActionRejected('CLAMP_BUSY')
            if c['clamped']:
                if self._test_active(cell):
                    raise ActionRejected('TEST_ACTIVE')
                c['clamp_move'] = dict(start_ns=self.time_ns, q0=c['clamp'], target=0.0)
                out = dict(cell=cell, clamping=False)
            else:
                if not (c['drawer'] == 0.0 and c['drawer_move'] is None):
                    raise ActionRejected('DRAWER_NOT_CLOSED')
                c['clamp_move'] = dict(start_ns=self.time_ns, q0=c['clamp'], target=CLAMP_TRAVEL_M)
                out = dict(cell=cell, clamping=True)
        elif k == 'test.start':
            cell = a['cell']
            c = self.cells[cell]
            if self.plant is None or self.gas is None:
                raise ActionRejected('PLANT_REQUIRED')
            if not self.power:
                raise ActionRejected('POWER_OFF')
            if self.instrument is None or c['cfg'] is None:
                raise ActionRejected('METHOD_REQUIRED')
            if not self._supply_ok():
                raise ActionRejected('GAS_SUPPLY_PRESSURE_OUT_OF_RANGE')
            if not c['clamped'] or c['clamp_move'] is not None:
                raise ActionRejected('CELL_NOT_CLAMPED')
            if self._test_active(cell):
                raise ActionRejected('BUSY')
            op = self._new_test(cell)
            if self.seq is None:
                self._next_slot(None)
            else:
                self._route()
            out = dict(operation_id=op)
        elif k == 'test.stop':
            cell = a['cell']
            if not self._test_active(cell):
                raise ActionRejected('NOT_RUNNING')
            c = self.cells[cell]
            t = c['test']
            n_exam = len([r for r in t['readings'] if r['kind'] == 'exam'])
            if t['cfg']['test_mode'] == 'continuous' and n_exam > 0 and c['phase'] == 'testing':
                # a continuous test is ended by the operator: optional end zero, then an evaluated result (2/28 manual)
                if t['cfg']['individual_zero'] in ('end', 'both'):
                    c['phase'] = t['cell_phase'] = 'zero_end'
                    if self.seq is not None and self.route == cell:
                        self.seq['routed_since_ns'] = self.time_ns       # the running exam slot is not a complete zero
                else:
                    self._finish(cell, 'succeeded', 'OPERATOR_STOP')
            else:
                self._finish(cell, 'cancelled', 'USER_CANCEL')
            self._route()
            out = dict(cell=cell, phase=t['phase'], exam_count=n_exam)
        else:  # pragma: no cover
            raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision += 1
        return copy.deepcopy(out)

    def _mount(self, a):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')
        c = self.cells[a['cell']]
        if c['film'] is not None:
            raise ActionRejected('FILM_ALREADY_MOUNTED')
        if not (c['drawer'] == DRAWER_TRAVEL_M and c['drawer_move'] is None) or c['clamped'] or c['clamp_move'] is not None:
            raise ActionRejected('DRAWER_NOT_OPEN')
        if not a['accept_transport_assumptions']:
            raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
        rec = library().get(a['template_id'])
        if rec is None:
            raise ActionRejected('UNKNOWN_TEMPLATE')
        P = a['permeability_override_mol_m_per_m2_s_pa'] or rec['oxygen_permeability_mol_m_per_m2_s_Pa']
        D = a['diffusivity_override_m2_s'] or rec['oxygen_diffusivity_m2_s']
        if D is None:
            raise ActionRejected('DIFFUSIVITY_REQUIRED')
        water = rec['humidity_class'] != 'negligible_assumed'
        if not water and a['humidity_coefficient'] not in (None, 0.0):
            raise ActionRejected('HUMIDITY_COEFFICIENT_NOT_APPLICABLE')
        if water and a['humidity_coefficient'] is None:
            raise ActionRejected('HUMIDITY_COEFFICIENT_REQUIRED')   # host prior needed at mount time for every water-sensitive template
        L = a['thickness_m']
        film = dict(template=rec['id'], T_ref=rec['reference_temperature_K'], P_ref=P, D_ref=D, E_P=a['permeability_activation_energy_j_mol'],
                    E_D=a['diffusivity_activation_energy_j_mol'], beta=a['humidity_coefficient'] or 0.0, beta_given=a['humidity_coefficient'] is not None,
                    water_sensitive=water, L=L, dx=L / N_FILM_NODES, area=a['exposed_area_m2'],
                    source=f"{LIBRARY_PATH.relative_to(ROOT)}#{rec['id']}", P_evidence='host_override' if a['permeability_override_mol_m_per_m2_s_pa'] else rec['evidence'],
                    D_evidence='host_override' if a['diffusivity_override_m2_s'] else rec['diffusivity_evidence'])
        _, _, S = self._transport(film, self._ambient(), self.plant['ambient_rh'])
        c0 = S * self._air_o2_pa()
        c['film'] = film
        c['conc'] = [c0] * N_FILM_NODES
        c['source'] = film['source']
        content = self._film_content(a['cell'])
        self._add('film_mounted_mol', content)
        return dict(cell=a['cell'], mounted=True, film_o2_mol=content)

    # ------------------------------------------------------------ time integration
    def _next_events(self, end):
        ev = [end, self.time_ns + round(MAX_SUBSTEP_S * 1e9)]
        p = self.plant
        for c in CELLS:
            cc = self.cells[c]
            if cc['drawer_move'] is not None:
                m = cc['drawer_move']
                ev.append(m['start_ns'] + max(1, math.ceil(abs(m['target'] - m['x0']) / p['drawer_speed_m_s'] * 1e9)))
            if cc['clamp_move'] is not None:
                m = cc['clamp_move']
                ev.append(m['start_ns'] + max(1, math.ceil(abs(m['target'] - m['q0']) / CLAMP_TRAVEL_M * p['clamp_actuation_s'] * 1e9)))
            t = cc['test']
            if t is not None and t['phase'] == 'running' and cc['phase'] == 'conditioning' and t['conditioning_end_ns'] is not None:
                ev.append(t['conditioning_end_ns'])
        if self.seq is not None:
            ev.append(self.seq['end_ns'])
        return min(t for t in ev if t > self.time_ns)

    def _advance_mechanics(self, nxt):
        p = self.plant
        for cell in CELLS:
            c = self.cells[cell]
            if c['drawer_move'] is not None:
                m = c['drawer_move']
                dist = abs(m['target'] - m['x0'])
                s = (nxt - m['start_ns']) / 1e9 * p['drawer_speed_m_s']
                if s >= dist - 1e-15:
                    c['drawer'] = m['target']
                    c['drawer_move'] = None
                else:
                    c['drawer'] = m['x0'] + math.copysign(s, m['target'] - m['x0'])
            if c['clamp_move'] is not None:
                m = c['clamp_move']
                dist = abs(m['target'] - m['q0'])
                s = (nxt - m['start_ns']) / 1e9 / p['clamp_actuation_s'] * CLAMP_TRAVEL_M
                if s >= dist - 1e-15:
                    c['clamp'] = m['target']
                    c['clamp_move'] = None
                    if m['target'] > 0:
                        self._seal_cell(cell)
                    else:
                        self._open_cell(cell)
                else:
                    c['clamp'] = m['q0'] + math.copysign(s, m['target'] - m['q0'])

    def _seal_cell(self, cell):
        """Clamp completed: both chambers start with trapped laboratory air."""
        c = self.cells[cell]
        p = self.plant
        c['clamped'] = True
        self._add('released_to_air_mol', c['n_h'])   # (always 0 for an open cell; keeps the chamber balance closed regardless)
        c['p_up'] = self._air_o2_pa()
        n_air = self._air_o2_pa() * p['carrier_chamber_volume_m3'] / (GAS_CONSTANT * self._ambient())
        c['n_h'] = n_air
        self._add('carrier_in_mol', n_air)   # trapped air counted as carrier-side input so the chamber balance stays closed

    def _open_cell(self, cell):
        c = self.cells[cell]
        c['clamped'] = False
        self._add('released_to_air_mol', c['n_h'])
        c['n_h'] = 0.0
        c['p_up'] = self._air_o2_pa()

    def _integrate(self, dt):
        p = self.plant
        Ta = p['ambient_k']
        # ---- test-cell block temperature: PI thermoelectric (heats and cools), conditional integration
        if self.power:
            e = self.target_k - (self.T + p['temperature_sensor_bias_k'])     # the controller acts on its own (biased) sensor
            u = p['controller_gain_w_k'] * e + self.integral_w
            Q = min(max(u, -p['tec_max_w']), p['tec_max_w'])
            if -p['tec_max_w'] < u < p['tec_max_w'] or (u >= p['tec_max_w'] and e < 0) or (u <= -p['tec_max_w'] and e > 0):
                self.integral_w = min(max(self.integral_w + p['controller_gain_w_k'] / p['controller_integral_s'] * e * dt, -p['tec_max_w']), p['tec_max_w'])
        else:
            Q = 0.0
        self.tec_w = Q
        # exact exponential update of the block for constant Q over the sub-step (unconditionally stable)
        G, Cb = p['block_loss_w_k'], p['block_heat_capacity_j_k']
        T_inf = Ta + Q / G
        tau_b = Cb / G
        e_b = math.exp(-dt / tau_b)
        T_new = T_inf + (self.T - T_inf) * e_b
        self._add('tec_heat_j', Q * dt)
        self._add('ambient_exchange_j', G * ((T_inf - Ta) * dt + (self.T - T_inf) * tau_b * (1.0 - e_b)))   # exact integral of G (T - T_amb) dt
        self.T = T_new
        # ---- RH generator model (indicated) and true humidity
        if self.power:
            self.rh_model = self.rh_target + (self.rh_model - self.rh_target) * math.exp(-dt / p['rh_time_constant_s'])
            if self.rh_target == 0.0 and self.rh_model < 1e-6:
                self.rh_model = 0.0
        aw = self._rh_true()
        pw = aw * saturation_pressure_pa(self.T)
        g = self.gas
        n_c = molar_flow(CARRIER_FLOW_SCCM)
        # ---- per cell: chambers, film, leaks
        outflows = {}
        for cell in CELLS:
            c = self.cells[cell]
            film = c['film']
            open_ = self._cell_open(cell)
            flow_on = self.power and not open_
            c['flux'] = 0.0
            c['leak'] = 0.0
            if open_:
                outflows[cell] = 0.0
                if film is not None:
                    Tf = Ta
                    _, D, S = self._transport(film, Tf, p['ambient_rh'])
                    cb = S * self._air_o2_pa()
                    jt_, jb_ = self._film_advance(c, D, cb, cb, dt)
                    self._add('film_air_exchange_mol', (jt_ - jb_) * film['area'] * dt)
                continue
            V_h = p['carrier_chamber_volume_m3']
            V_t = p['test_chamber_volume_m3']
            carrier_on = flow_on and self._carrier_ok()
            # test-gas chamber: test gas during conditioning/testing, carrier flush otherwise; no supply = no flow (stagnant)
            t = c['test']
            testing_gas = (flow_on and t is not None and t['phase'] == 'running' and c['phase'] in ('conditioning', 'testing') and g is not None)
            if (testing_gas and self._test_gas_ok()) or (flow_on and not testing_gas and carrier_on):
                target = g['test_o2_fraction'] * max(self._baro() - pw, 0.0) if testing_gas else 0.0
                F_t = molar_flow(p['test_gas_flow_sccm_per_cell']) * GAS_CONSTANT * self.T / self._baro()
                c['p_up'] = target + (c['p_up'] - target) * math.exp(-F_t / V_t * dt)
            p_up = c['p_up']
            # carrier-side inputs other than the film (constant over the sub-step); the sweep (kh) needs carrier flow
            edge = p['edge_leak_mol_s_pa'] * p_up
            ingress = p['ambient_ingress_mol_s_pa'] * (1.0 - p['truseal_flush_efficiency']) * self._air_o2_pa()
            residual = self._carrier_residual_fraction() * n_c if carrier_on else 0.0
            q_in = edge + ingress + residual
            kh = n_c * GAS_CONSTANT * self.T / self._baro() / V_h if carrier_on else 0.0
            n_old = c['n_h']
            film_out = 0.0
            open_flow = 0.0
            if film is not None:
                _, D, S = self._transport(film, self.T, aw)
                j_top, j_bot, n_new, out = self._film_chamber_advance(c, D, S * p_up, S * GAS_CONSTANT * self.T / V_h, q_in, kh, dt)
                film_out = j_bot * film['area']           # mol/s averaged over the sub-step
                self._add('film_test_side_in_mol', j_top * film['area'] * dt)
                self._add('film_to_carrier_mol', film_out * dt)
            else:
                # empty cartridge: the chambers communicate through the open window (two-way, solved implicitly)
                a_h = GAS_CONSTANT * self.T / V_h
                n_new = (n_old + dt * (q_in + OPEN_WINDOW_MOL_S_PA * p_up)) / (1.0 + dt * kh + dt * OPEN_WINDOW_MOL_S_PA * a_h)
                open_flow = OPEN_WINDOW_MOL_S_PA * (p_up - n_new * a_h)
                out = kh * n_new
                self._add('open_cell_mol', open_flow * dt)
            inputs = film_out + q_in + open_flow
            self._add('edge_leak_mol', edge * dt)
            self._add('ambient_ingress_mol', ingress * dt)
            self._add('carrier_o2_impurity_mol', residual * dt)
            self._add('carrier_in_mol', inputs * dt)
            c['flux'] = film_out
            c['leak'] = edge + ingress
            c['n_h'] = n_new                                # sweep outflow 'out' = backward-Euler kh n_h' of every solver sub-step
            outflows[cell] = out
        # ---- rezero stream and sensor
        zero_stream = (self._carrier_residual_fraction() * n_c + p['rezero_path_leak_mol_s']) if (self.power and self._carrier_ok()) else 0.0
        self._add('rezero_stream_mol', zero_stream * dt)
        route = self.route
        if route in CELLS:
            s_in = outflows.get(route, 0.0)
        elif route == 'Z':
            s_in = zero_stream
        else:
            s_in = 0.0
        for cell, out in outflows.items():
            if cell == route:
                self._add('to_sensor_mol', out * dt)
            else:
                self._add('to_vent_mol', out * dt)
        self.sensor_in = s_in
        if self.power:
            eta = p['sensor_efficiency']
            tau_s = p['sensor_time_constant_s']
            e_s = math.exp(-dt / tau_s)
            I_ss = 4.0 * FARADAY * eta * s_in
            I0 = self.sensor_I_o2
            self.sensor_I_o2 = I_ss + (I0 - I_ss) * e_s
            self.sensor_I_bg = p['sensor_background_a'] + (self.sensor_I_bg - p['sensor_background_a']) * e_s
            self.sensor_I = self.sensor_I_o2 + self.sensor_I_bg
            self._add('sensor_input_mol', s_in * dt)
            self._add('sensor_consumed_mol', (I_ss * dt + (I0 - I_ss) * tau_s * (1.0 - e_s)) / (4.0 * FARADAY))   # exact charge of the O2 current
            self._add('sensor_passthrough_mol', (1.0 - eta) * s_in * dt)
            # measurement noise: first-order correlated with the sensor time constant, additive and proportional (one stream for
            # panel counts, exam and ReZero readings, so every agent-visible channel carries the same noise)
            k_s = math.sqrt(max(0.0, 1.0 - e_s * e_s))
            self.noise_abs = self.noise_abs * e_s + p['sensor_noise_a'] * k_s * self.rng.gauss(0.0, 1.0)
            self.noise_rel = self.noise_rel * e_s + p['sensor_relative_noise'] * k_s * self.rng.gauss(0.0, 1.0)
            if self._carrier_ok():
                self._add('carrier_gas_used_mol', molar_flow(p['carrier_total_flow_sccm']) * dt)
            if self._active_cells() and self._test_gas_ok():
                self._add('test_gas_used_mol', molar_flow(p['test_total_flow_sccm']) * dt)
        return s_in

    def _film_chamber_advance(self, c, D, c_top, sigma, q_in, kh, dt):
        """Film and carrier chamber advanced together; returns sub-step averaged (J_top_in, J_bot_out), the new chamber O2 and the
        averaged sweep outflow (mol/s)."""
        film = c['film']
        tau = film['L'] ** 2 / D
        h = min(dt, max(0.02 * tau, 1.0))
        n = max(1, math.ceil(dt / h - 1e-12))
        h = dt / n
        jt = jb = ot = 0.0
        conc, n_h = c['conc'], c['n_h']
        for _ in range(n):
            conc, n_h, a_, b_ = film_chamber_step(conc, film['dx'], D, c_top, n_h, sigma, film['area'], q_in, kh, h)
            jt += a_ * h
            jb += b_ * h
            ot += kh * n_h * h
        c['conc'] = conc
        return jt / dt, jb / dt, n_h, ot / dt

    def _film_advance(self, c, D, c_top, c_bot, dt):
        film = c['film']
        tau = film['L'] ** 2 / D
        h = min(dt, max(0.02 * tau, 1.0))
        n = max(1, math.ceil(dt / h - 1e-12))
        h = dt / n
        jt = jb = 0.0
        conc = c['conc']
        for _ in range(n):
            conc, a_, b_ = film_step(conc, film['dx'], D, c_top, c_bot, h)
            jt += a_ * h
            jb += b_ * h
        c['conc'] = conc
        return jt / dt, jb / dt

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
            s_in = self._integrate(dt)
            self._advance_mechanics(nxt)
            self.time_ns = nxt
            # sensor protection: an excessive measured current switches the sensor to bypass; an over-ranged cell fails its own test,
            # an over-ranged ReZero stream fails every active test (2/28 manual: "all active tests will be failed")
            I_meas = self._measured_current()
            if self.power and self.route in CELLS + ('Z',) and not (math.isfinite(I_meas) and I_meas <= OVER_RANGE_CURRENT_A):
                stream = self.route
                self._add('over_range_events', 1)
                self.over_range = True
                self._route()                                   # -> bypass for the rest of the slot
                for cell in ([stream] if stream in CELLS else list(self._active_cells())):
                    if self._test_active(cell):
                        self._finish(cell, 'failed', 'SENSOR_OVER_RANGE')
            for cell in CELLS:
                cc = self.cells[cell]
                t = cc['test']
                if t is not None and t['phase'] == 'running' and cc['phase'] == 'conditioning' and t['conditioning_end_ns'] is not None and self.time_ns >= t['conditioning_end_ns']:
                    cc['phase'] = t['cell_phase'] = 'testing'
                    t['conditioning_end_ns'] = None
                    self._route()
            if self.seq is not None and self.time_ns >= self.seq['end_ns']:
                self._complete_slot()
            if self.seq is not None and not self._active_cells():
                self.seq = None
                self.route = 'bypass'
            if self.over_range and self.route == 'bypass' and self._measured_current() < 0.5 * OVER_RANGE_CURRENT_A:
                self.over_range = False                         # routing resumes with the next slot; the tripped stream is not re-entered
        self.revision += 1


class AmetekOxtranSession:
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
        meta = copy.deepcopy(export_ametek_oxtran_contract_metadata())
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

    @staticmethod
    def _filter_result(result):
        result = copy.deepcopy(result)
        # Public test payloads use the same redaction for every role; provenance.read is the evaluator channel.
        if 'readings' in result and 'template_source' in result:
            result['template_source'] = (str(LIBRARY_PATH.relative_to(ROOT)) + '; selected record available only through evaluator provenance.read') if result['template_source'] is not None else None
        return result

    def _foreign(self, cell):
        h = self._host
        t = h.cells[cell]['test']
        owner = h._owners.get(t['op']) if t is not None else None
        return owner is not None and owner != self._principal and h._principals.get(owner) == 'agent'

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
            # Tests started by another *agent* are private to it; panel/host-started tests are shared by every operator.
            if self._role == 'agent':
                cell = arguments.get('cell') if isinstance(arguments, dict) else None
                if capability_id in ('test.stop', 'test.result', 'test.configure', 'cell.load_unload') and cell in CELLS and self._foreign(cell):
                    raise ActionRejected('ACCESS_DENIED')
                if capability_id in ('thermal.set_temperature', 'humidity.set', 'instrument.configure') and any(
                        self._foreign(c) and h._test_active(c) for c in CELLS):
                    raise ActionRejected('ACCESS_DENIED')
                if capability_id == 'power.set' and not arguments['enabled'] and any(self._foreign(c) and h._test_active(c) for c in CELLS):
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
        t = h.operations.get(operation_id)
        if t is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        result = h._result_of(t) if t['phase'] == 'running' else h.records[operation_id]
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
        t = h.operations[operation_id]
        if t['phase'] != 'running':
            raise ActionRejected('NOT_RUNNING')
        h._abort_test(t['cell'])
        result = dict(cell=t['cell'], phase=t['phase'], exam_count=len([r for r in t['readings'] if r['kind'] == 'exam']))
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='succeeded',
                        state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=result, observations={}, error=None)
        h._requests[key] = (json.dumps(['cancel', operation_id]), copy.deepcopy(response))
        h._cancel_operations[key] = operation_id
        return response


WRITE_VIA = {'thermal.target_k': ('thermal.set_temperature', 'target_k'), 'humidity.target_fraction': ('humidity.set', 'rh_fraction')}
_PHASES = ['cell_a.phase', 'cell_b.phase']
PRECONDITIONS = {
    'plant.configure': ('只能声明一次（plant.configured为假），须显式接受工程参数；热时间常数须不小于5个数值子步', ['plant.configured']),
    'gas.configure': ('须显式接受气源声明；两池都没有进行中的测试', _PHASES),
    'instrument.configure': ('两池都没有进行中的测试；手动气压计模式须给出气压值', _PHASES),
    'test.configure': ('该池没有进行中的测试；收敛模式的最大周期数须不小于收敛次数+1', _PHASES),
    'power.set': ('须已声明工程参数；断电会取消所有进行中的测试', ['plant.configured', 'power.enabled']),
    'thermal.set_temperature': ('须已声明工程参数；目标在10–40°C', ['plant.configured']),
    'humidity.set': ('须已声明工程参数；目标为0（干气）或5–90%', ['plant.configured']),
    'drawer.move': ('须已声明工程参数；该池未夹紧、压板完全抬起且不在行程中', ['plant.configured', 'cell_a.clamped', 'cell_b.clamped', 'clamp_a.position_m', 'clamp_b.position_m']),
    'sample.mount': ('抽屉完全拉出且静止、测试盒未夹紧、盒内无膜；须接受传输先验；亲水模板须给出湿度系数，疏水模板不接受非零系数',
                     ['drawer_a.position_m', 'drawer_b.position_m', 'cell_a.clamped', 'cell_b.clamped', 'cell_a.film_mounted', 'cell_b.film_mounted']),
    'sample.unmount': ('抽屉完全拉出且静止、测试盒未夹紧、盒内有膜', ['drawer_a.position_m', 'drawer_b.position_m', 'cell_a.clamped', 'cell_b.clamped', 'cell_a.film_mounted', 'cell_b.film_mounted']),
    'cell.load_unload': ('须通电且压板不在行程中；夹紧前抽屉须推到底；松开前该池测试须已结束',
                         ['power.enabled', 'cell_a.clamp_moving', 'cell_b.clamp_moving', 'cell_a.drawer_closed', 'cell_b.drawer_closed', 'cell_a.clamped', 'cell_b.clamped']
                         + _PHASES),
    'test.start': ('须已声明工程参数、气源、仪器参数与该池方法；通电；两路供气表压在厂家范围；该池已夹紧且没有进行中的测试',
                   ['plant.configured', 'gas.configured', 'instrument.configured', 'cell_a.method_configured', 'cell_b.method_configured', 'power.enabled',
                    'gas.supply_ok', 'cell_a.clamped', 'cell_b.clamped'] + _PHASES),
    'test.stop': ('该池有进行中的测试', _PHASES),
}
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入结构、类型或范围（或收敛判据与最大周期数矛盾）', 'ACCESS_DENIED': '该角色无权调用，或该池测试属于另一个agent',
    'REQUEST_CONFLICT': '同一request_id已用于不同的请求', 'UNKNOWN_INSTANCE': '实例ID不存在', 'UNSUPPORTED': '本配置不提供该功能（理由见function_zh）',
    'PLANT_ALREADY_CONFIGURED': '工程参数只能声明一次', 'ENGINEERING_ACCEPTANCE_REQUIRED': '未显式接受工程假设、气源声明或传输先验',
    'THERMAL_TIME_CONSTANT_OUTSIDE_NUMERICAL_DOMAIN': '测试池热时间常数小于5个数值子步，超出数值适用域', 'BUSY': '有进行中的测试，不能改这项配置',
    'MANUAL_BAROMETER_REQUIRED': '手动气压计模式缺少气压值', 'PLANT_REQUIRED': '尚未声明工程参数（test.start还需要气源声明）',
    'RH_OUTSIDE_CONTROLLED_RANGE': '湿度目标既不是0也不在5–90%受控范围', 'CARTRIDGE_CLAMPED': '测试盒已夹紧或压板未完全抬起，抽屉不能移动',
    'FILM_ALREADY_MOUNTED': '测试盒内已有膜样', 'DRAWER_NOT_OPEN': '抽屉未完全拉出、仍在移动或测试盒被夹紧', 'UNKNOWN_TEMPLATE': '模板库中没有该模板',
    'DIFFUSIVITY_REQUIRED': '模板没有实验扩散系数，且未给出替代值', 'HUMIDITY_COEFFICIENT_NOT_APPLICABLE': '疏水模板不接受非零湿度系数',
    'HUMIDITY_COEFFICIENT_REQUIRED': '亲水模板装膜时必须给出湿度系数', 'NO_FILM': '测试盒内没有膜样',
    'POWER_OFF': '未通电时拒绝（无副作用）；运行中断电使测试以POWER_OFF取消（已记录读数保留）', 'CLAMP_BUSY': '压板正在动作',
    'TEST_ACTIVE': '该池测试进行中，不能松开', 'DRAWER_NOT_CLOSED': '抽屉未推到底，不能夹紧', 'METHOD_REQUIRED': '未设定仪器参数或该池测试方法',
    'GAS_SUPPLY_PRESSURE_OUT_OF_RANGE': '载气或测试气供气表压不在厂家范围', 'CELL_NOT_CLAMPED': '该池未夹紧', 'NOT_RUNNING': '该池没有进行中的测试',
    'USER_CANCEL': '测试被停止或取消（已记录读数保留）', 'SENSOR_OVER_RANGE': '传感器超量程保护使测试失败（池通道：该池；ReZero通道：全部进行中的测试）',
    'TOO_MANY_READINGS': '读数达到5000条记录上限，测试失败',
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_GUARDED = {'test.stop', 'test.result', 'test.configure', 'cell.load_unload', 'thermal.set_temperature', 'humidity.set', 'instrument.configure', 'power.set'}


def _errors_of(k):
    """Codes the Session can return for capability k: schema/idempotency/instance errors, ACCESS_DENIED only for evaluator
    capabilities or the foreign-test guard, then the runtime's own rejections (and asynchronous terminal reasons)."""
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    access = ['ACCESS_DENIED'] if (CAPS[k]['visibility'] != 'agent' or k in _GUARDED) else []
    return _COMMON_ERRORS + access + CAP_ERRORS.get(k, [])
CAP_ERRORS = {
    'plant.configure': ['PLANT_ALREADY_CONFIGURED', 'ENGINEERING_ACCEPTANCE_REQUIRED', 'THERMAL_TIME_CONSTANT_OUTSIDE_NUMERICAL_DOMAIN'],
    'gas.configure': ['ENGINEERING_ACCEPTANCE_REQUIRED', 'BUSY'], 'instrument.configure': ['MANUAL_BAROMETER_REQUIRED', 'BUSY'], 'test.configure': ['BUSY'],
    'power.set': ['PLANT_REQUIRED'], 'thermal.set_temperature': ['PLANT_REQUIRED'], 'humidity.set': ['PLANT_REQUIRED', 'RH_OUTSIDE_CONTROLLED_RANGE'],
    'drawer.move': ['PLANT_REQUIRED', 'CARTRIDGE_CLAMPED'],
    'sample.mount': ['PLANT_REQUIRED', 'FILM_ALREADY_MOUNTED', 'DRAWER_NOT_OPEN', 'ENGINEERING_ACCEPTANCE_REQUIRED', 'UNKNOWN_TEMPLATE', 'DIFFUSIVITY_REQUIRED',
                     'HUMIDITY_COEFFICIENT_NOT_APPLICABLE', 'HUMIDITY_COEFFICIENT_REQUIRED'],
    'sample.unmount': ['NO_FILM', 'DRAWER_NOT_OPEN'], 'cell.load_unload': ['PLANT_REQUIRED', 'POWER_OFF', 'CLAMP_BUSY', 'TEST_ACTIVE', 'DRAWER_NOT_CLOSED'],
    'test.start': ['PLANT_REQUIRED', 'POWER_OFF', 'METHOD_REQUIRED', 'GAS_SUPPLY_PRESSURE_OUT_OF_RANGE', 'CELL_NOT_CLAMPED', 'BUSY', 'USER_CANCEL', 'SENSOR_OVER_RANGE',
                   'TOO_MANY_READINGS'],
    'test.stop': ['NOT_RUNNING'],
}
_PARTIAL = {'USER_CANCEL', 'POWER_OFF', 'SENSOR_OVER_RANGE', 'TOO_MANY_READINGS'}
_MAN, _BRO, _PAGE, _CERT, _M228, _ASTM = ('manufacturer_primary', 'manufacturer_brochure_2025', 'manufacturer_product_page', 'manufacturer_certified_film_instructions',
                                          'related_model_manual_228', 'astm_d3985')
CAP_SOURCES = {       # documents each capability's behaviour (or its unsupported status) rests on; ids are registered in the contract sources
    'plant.configure': ['simulation_assumption', _M228], 'gas.configure': [_PAGE, _M228, 'simulation_assumption'], 'instrument.configure': [_M228, _CERT],
    'test.configure': [_CERT, _M228, _MAN], 'power.set': [_PAGE, 'simulation_assumption'], 'thermal.set_temperature': [_MAN, _BRO, _M228, 'simulation_assumption'],
    'humidity.set': ['user_workbook', _MAN, _BRO, 'simulation_assumption'], 'drawer.move': [_PAGE, _M228, 'geometry', 'simulation_assumption'],
    'sample.mount': ['template_library', 'template_source_polyverse', _PAGE, 'simulation_assumption'], 'sample.unmount': ['template_library', 'simulation_assumption'],
    'cell.load_unload': [_PAGE, _M228], 'test.start': [_MAN, _CERT, _M228, _ASTM, 'template_library', 'simulation_assumption'], 'test.stop': [_M228],
    'test.result': [_MAN, _M228, _ASTM], 'analysis.otr': [_ASTM, _M228], 'ledger.read': ['simulation_assumption', 'runtime_code'],
    'provenance.read': ['template_library', 'template_source_polyverse'], 'sim.step': ['runtime_code', 'simulation_assumption'],
    'hardware.connect': [_PAGE], 'robot.mount_film': ['simulation_assumption'], 'package.test': [_PAGE, _MAN], 'cartridge.reduced_area': [_PAGE, _MAN],
    'cartridge.edge_effect': [_PAGE, _MAN], 'cartridge.blister': [_PAGE, _MAN], 'test_mode.convergence_by_hours': [_PAGE, _BRO], 'test.qc_mode': [_PAGE, _BRO],
    'test.auto_method': [_PAGE, _BRO], 'test.sequencing': [_PAGE, _BRO], 'report.export': [_PAGE, _BRO], 'accuracy.certification': [_CERT, _ASTM],
    'sensor.adjust': [_CERT, _PAGE, _M228]}
_RESULT_SLOTS = ['cell_a.phase', 'cell_b.phase', 'cell_a.exam_count', 'cell_b.exam_count', 'cell_a.last_otr_mol_m2_s', 'cell_b.last_otr_mol_m2_s',
                 'cell_a.final_otr_mol_m2_s', 'cell_b.final_otr_mol_m2_s']
OBSERVES = {
    'plant.configure': ['plant.configured'], 'gas.configure': ['gas.configured', 'gas.supply_ok'], 'instrument.configure': ['instrument.configured'],
    'power.set': ['power.enabled'], 'thermal.set_temperature': ['thermal.target_k', 'thermal.indicated_k'],
    'humidity.set': ['humidity.target_fraction', 'humidity.indicated_fraction'],
    'drawer.move': ['drawer_a.position_m', 'drawer_b.position_m', 'cell_a.drawer_closed', 'cell_b.drawer_closed'],
    'sample.mount': ['cell_a.film_mounted', 'cell_b.film_mounted'], 'sample.unmount': ['cell_a.film_mounted', 'cell_b.film_mounted'],
    'cell.load_unload': ['cell_a.clamped', 'cell_b.clamped', 'cell_a.clamp_moving', 'cell_b.clamp_moving', 'cell_a.indicator_on', 'cell_b.indicator_on'],
    'test.configure': ['cell_a.method_configured', 'cell_b.method_configured'],
    'test.start': _RESULT_SLOTS + ['sensor.route', 'sequence.slot_remaining_s'], 'test.stop': _RESULT_SLOTS, 'test.result': _RESULT_SLOTS,
    'sim.step': ['clock.time_s'],
}
PARAM_ZH = {
    'ambient_k': '实验室环境温度', 'ambient_rh': '实验室环境相对湿度（决定装膜时膜内溶解氧与环境渗入的O₂分压）', 'barometric_pa': '实验室大气压（绝对）',
    'block_heat_capacity_j_k': '测试池块集中热容', 'block_loss_w_k': '测试池块对环境的散热系数', 'tec_max_w': '热电模块最大加热/制冷功率',
    'controller_gain_w_k': '温控PI比例增益', 'controller_integral_s': '温控PI积分时间', 'temperature_sensor_bias_k': '池温传感器静态偏差（在±0.2K内）',
    'rh_time_constant_s': '湿度发生器一阶时间常数', 'rh_bias': '湿度静态偏差（在±4%RH内）', 'test_chamber_volume_m3': '每池测试气腔全混体积',
    'carrier_chamber_volume_m3': '每池载气腔全混体积', 'test_gas_flow_sccm_per_cell': '每池测试气流量（标准状态）', 'catalyst_removal_efficiency': '载气催化除氧效率（H₂≥1%时有效）',
    'edge_leak_mol_s_pa': '测试盒边缘泄漏导通（按测试侧O₂分压）', 'ambient_ingress_mol_s_pa': '环境向载气侧的渗入导通（TruSeal冲洗前）',
    'truseal_flush_efficiency': 'TruSeal双密封冲洗对环境渗入的去除比例', 'rezero_path_leak_mol_s': 'ReZero路径的O₂漏入速率', 'sensor_time_constant_s': 'Coulox传感器一阶响应时间常数',
    'sensor_efficiency': '传感器O₂还原效率', 'sensor_background_a': '传感器背景电流', 'sensor_noise_a': '传感器加性噪声标准差', 'sensor_relative_noise': '传感器比例噪声标准差',
    'noise_seed': '噪声随机数种子', 'clamp_actuation_s': '气动压板全行程时间', 'drawer_speed_m_s': '宿主拉/推抽屉的速度', 'carrier_total_flow_sccm': '整机载气总消耗流量（标准状态）',
    'test_total_flow_sccm': '整机测试气总消耗流量（标准状态）', 'accept_engineering': '宿主显式接受以上工程参数', 'carrier_h2_fraction': '载气中H₂份额（厂家98/2 N₂/H₂）',
    'carrier_o2_impurity_fraction': '载气钢瓶O₂杂质份额', 'carrier_supply_gauge_pa': '载气供气表压', 'test_o2_fraction': '测试气O₂份额（≥99.9%或空气）',
    'test_supply_gauge_pa': '测试气供气表压', 'accept_gas_supply': '宿主显式接受气源声明', 'thickness_m': '膜样实际厚度', 'exposed_area_m2': '膜样暴露面积（箔掩膜可缩小）',
    'permeability_override_mol_m_per_m2_s_pa': '宿主替代的298K渗透系数', 'diffusivity_override_m2_s': '宿主替代的298K扩散系数',
    'permeability_activation_energy_j_mol': '渗透系数的Arrhenius活化能', 'diffusivity_activation_energy_j_mol': '扩散系数的Arrhenius活化能',
    'humidity_coefficient': '亲水聚合物的湿度塑化系数β（P、D乘exp(β·a_w)）', 'accept_transport_assumptions': '宿主显式接受膜传输先验',
    'cycles': '标准模式检测周期数', 'exam_minutes': '每个检测时段长度', 'conditioning_hours': '调理时间（传感器旁路、测试气已通）',
    'convergence_percent': '收敛判据：相邻检测相对变化上限', 'convergence_count': '收敛判据：连续满足的检测次数', 'max_cycles': '收敛模式最大检测周期数',
    'declared_area_m2': '报告用的声明面积', 'declared_thickness_m': '报告渗透率用的声明厚度', 'temperature_tolerance_k': '测试期间允许的池温偏差',
    'permeant_concentration_percent': '测试气渗透物浓度（归一到100% O₂）', 'rezero_enabled': '是否启用ReZero基线', 'rezero_frequency': '每几个池时段做一次ReZero',
    'rezero_exam_minutes': 'ReZero时段长度', 'manual_barometer_pa': '手动输入的气压',
}
NULLABLE_ZH = {'permeability_override_mol_m_per_m2_s_pa': 'null=用模板298K渗透系数', 'diffusivity_override_m2_s': 'null=用模板扩散系数，模板无D时必须给出',
               'humidity_coefficient': '疏水模板必须为null或0，亲水模板装膜时必须给出', 'declared_thickness_m': 'null=不报告渗透率',
               'manual_barometer_pa': 'null=用内置气压计（自动模式）'}
_CELL_SLOTS = lambda k: [f'cell_{c}.{k}' for c in 'ab']   # noqa: E731
EFFECTS = {
    'plant.configure': ['plant.configured', 'thermal.target_k', 'truth.block_temperature_k'],
    'gas.configure': ['gas.configured', 'gas.supply_ok'],
    'instrument.configure': ['instrument.configured'],
    'test.configure': _CELL_SLOTS('method_configured'),
    'power.set': ['power.enabled', 'power.angle_rad', 'thermal.indicated_k', 'humidity.indicated_fraction', 'barometer.indicated_pa', 'sensor.raw_counts', 'sensor.route',
                  'sequence.slot_remaining_s', 'truth.tec_power_w'] + _CELL_SLOTS('phase') + _CELL_SLOTS('indicator_on') + _CELL_SLOTS('final_otr_mol_m2_s') + _CELL_SLOTS('clamped')
                 + _CELL_SLOTS('clamp_moving') + [f'truth.cell_{c}.{k}' for c in 'ab' for k in ('carrier_side_o2_mol', 'test_side_o2_pa')] + ['truth.sensor_current_a'],
    'thermal.set_temperature': ['thermal.target_k'],
    'humidity.set': ['humidity.target_fraction'],
    'drawer.move': _CELL_SLOTS('drawer_closed'),
    'sample.mount': _CELL_SLOTS('film_mounted') + [f'truth.cell_{c}.film_o2_mol' for c in 'ab'],
    'sample.unmount': _CELL_SLOTS('film_mounted') + [f'truth.cell_{c}.film_o2_mol' for c in 'ab'],
    'cell.load_unload': _CELL_SLOTS('clamped') + _CELL_SLOTS('indicator_on') + _CELL_SLOTS('clamp_moving'),
    'test.start': _CELL_SLOTS('phase') + _CELL_SLOTS('exam_count') + _CELL_SLOTS('last_otr_mol_m2_s') + ['sensor.route', 'sequence.slot_remaining_s'],
    'test.stop': _CELL_SLOTS('phase') + _CELL_SLOTS('final_otr_mol_m2_s') + ['sensor.route'],
    'sim.step': list(SLOTS),
}
for _k, _v in EFFECTS.items():
    CAPS[_k]['effects'] = _v
ERRORS = sorted(set(re.findall(r"ActionRejected\('([A-Z_0-9]+)'", Path(__file__).read_text())) |
                {'USER_CANCEL', 'OPERATOR_STOP', 'POWER_OFF', 'SENSOR_OVER_RANGE', 'TEST_COMPLETE', 'TOO_MANY_READINGS'})
ASYNC = {'test.start'}
LIMITATIONS = [
    '选定OX-TRAN 2/22 L两池型、标准50cm²可拆卸水平测试盒；未选缩小面积(5.64cm²)/边缘效应/泡罩测试盒、整包测试适配器、PermWare与PERM-NET Lite；不建模Auto-Test、QC模式、顺序测试与报告导出。',
    '厂家规格（L型50cm²）：量程0.005–200cc/(m²·day)、整包0.000025–1.0cc/(pkg·day)、温度10–40°C±0.2°C、膜厚≤120mil；分辨率按调研表取0.002cc/(m²·day)（2025规格书/产品页为0.0001），RH按调研表取5–90%±4%（2025规格书为0%/30–90%±3%，产品页为0–90%±3%），冲突记录在source_evidence.json；0%RH作为干气测试另行允许（认证膜说明）。',
    '测试池块为集中热节点，热电模块可加热/制冷；热容、散热、功率上限与PI参数为宿主工程参数，缺省值按同机身2/28手册“约60分钟到达40°C设定的99%”调校；控温以自身（带±0.2K内静态偏差的）传感器为准，因此真实池温=设定值−偏差、显示值=设定值。',
    '湿度发生器为一阶响应加静态偏差（±4%内），加湿原理与水源未公开；湿测试气按道尔顿分压稀释O₂驱动压（p_O₂=y·(P−p_w)），仪器报告不对此稀释作修正（工程判断）。',
    '膜为一维Fick扩散：P、D取所选模板在298K的实验值（polyVERSE汇编），S=P/D；温度按宿主给定的活化能Arrhenius外推（缺省45kJ/mol，约6%/K，处于厂家“6–15%/°C”下限）；模板库的D·S与P不一致时以P与D为准。亲水聚合物的湿度塑化系数必须由宿主显式给出，疏水聚合物湿度影响按零处理（假设）；未建模结晶度、取向、涂层分层、非Fick溶胀或浓度依赖扩散。',
    '载气腔、测试气腔为全混体积（宿主给定体积与流量）；边缘泄漏∝测试侧O₂分压，环境渗入按TruSeal冲洗效率折减；单池零点在测试侧改通载气后测量，可扣除环境渗入但不能扣除与测试气分压成正比的边缘泄漏；空测试盒按开放窗口大导通处理（通常触发传感器超量程）。',
    'Coulox传感器按法拉第定律（1cc/day≈0.000199A）、一阶响应、背景电流、效率、10pA计数量化与宿主噪声建模；噪声为一阶相关（相关时间=传感器时间常数）的加性与比例噪声，面板原始计数、检测与ReZero读数取自同一测量信号；测得电流超过量程上限1.25倍即旁路保护（检测滞后不超过一个≤10s子步）：池通道超量程使该池测试失败，ReZero通道超量程使所有进行中的测试失败（2/28手册），该时段不再接回；电流回落到阈值一半后解除，下一时段恢复轮换；传感器寿命/电解液消耗未建模。',
    '调研表把“载气和测试气流”列为操作设置：本资产载气固定为每池10sccm（认证膜说明规定的设定值），测试气每池流量与两路总流量是宿主（evaluator）的工程参数，agent不能改流量；流量计、针阀和流量报警未建模。',
    '检测序列按固定时段A、B、Z轮换（ReZero频率=每N个池时段一次ReZero），空闲池的时段以旁路计时；该排法由认证膜说明中“周期数/测试小时”的比例推断，厂家帮助系统不可得。报告OTR=(I检测−I_ReZero)/(4F)/面积×760mmHg/P大气×100/渗透物浓度，并按调研表分辨率量化；2/22没有公开的用户增益调整（认证膜超差需送修），sensor.adjust为unsupported。',
    '连续模式由操作者test.stop结束（有结束零点则先测零点）；标准/收敛模式中途test.stop为终止；异步操作的cancel总是中止。测试中温度或湿度设定改变或显示值偏离容差时结果分别标为temperature/humidity_out_of_tolerance。收敛模式按最近N次相对变化≤阈值判定（阈值与N由方法给出，厂家精确算法未公开）；重复性按“0.002cc/(m²·day)或1%取大”判定（掩膜时绝对项与读数量化一样按50cm²/覆盖面积放大）；名义渗透量/渗透率=OTR/101325Pa（及×声明厚度），未扣除湿气稀释。',
    '抽屉拉出/推回、装膜/取膜为宿主代理（不是机器人接触验证）；夹紧为气动一键动作；断电时压板停在当前位置，未完成的行程视为未密封（载气腔与空气相通），压板未完全抬起时抽屉不能拉出（工程假设）；盖板只是被动场景关节。',
    'agent只能看到带噪声的量化计数、显示温湿度、气压、检测读数与结果；真实通量、膜内含氧、腔体分压、真实湿度与温度、所选模板身份只对evaluator可见；test.result对所有角色隐去模板身份；亲水模板的湿度系数在装膜时由宿主给出，agent的湿度设定不会因模板类别被拒。某个agent启动的测试，其他agent不能停止、读取完整记录（test.result）、改配置、改该池夹紧，也不能在其运行时改温度、湿度、仪器参数或断电；各池阶段、检测次数、最近与最终OTR属于前面板公开显示，所有操作者可见。',
    '时间推进为事件对齐的子步（≤10s；膜内再按扩散时间细分）：膜与载气腔在同一隐式方程组中求解（对任意渗透率与腔体体积无条件稳定、非负）；O₂物质账按求解器的真实界面通量、吹扫出流与传感器电荷（含在途量）记账，热量账按指数解析积分记账，均用补偿求和，因此残差是对积分器的真实守恒检查而不是恒等式；不同step切分只造成传感器噪声抽样时刻外的微小数值差。',
    '只有传感器整段接在该气流上的检测时段才记录读数或ReZero：调理在时段中途结束、测试在时段中途开始或超量程恢复后的剩余时段不计。无载气压力时载气腔不吹扫、传感器无进气；无测试气压力时测试腔不换气。更换气源或断电后先重新测ReZero；启用ReZero时没有基线就先排ReZero时段，不会做无基线的检测。',
    '没有真实硬件、WinPerm 2界面、认证膜实测、NIST溯源或ASTM D3985精密度验证；模板为文献汇编的干气P、D，不是OX-TRAN原生测试文件。',
]


def export_ametek_oxtran_contract_metadata():
    slots = {}
    caps = {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'],
                        access='read_write' if k in WRITE_VIA else 'read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'AmetekOxtranSession.read(state_ids=[{k!r}])'),
                        write_via=None if k not in WRITE_VIA else dict(capability_id=WRITE_VIA[k][0], argument=WRITE_VIA[k][1], fixed_arguments={}),
                        sampling=dict(mode='instant', time_domain='simulation',
                                      window_description='Pure read; sensor counts 10 pA, display 0.01 K / 0.001 RH / 10 Pa quantisation; exam readings at the end of each exam slot.'),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.ametek_oxtran.AmetekOxtranSession.invoke',
                                              invocation=f'session.invoke(instance_id="oxtran_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(
                     description_zh=PRECONDITIONS[k][0] + '；被拒时状态不变。', state_ids=list(PRECONDITIONS[k][1]), enforced_by='runtime', check='AmetekOxtranRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或操作标志；温湿度、扩散、传感器与检测序列只随step推进')
                                for s in v['effects']],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=dict(mode='none', description_zh='运行层只输出状态，由场景层绑定关节与读数', binding_ids=[]),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion='按方法达到N周期/收敛/单池零点结束（或停止/超量程/断电）' if async_ else '原子配置或外部动作请求，实际状态由step推进',
                                completion_state_ids=['cell_a.phase', 'cell_b.phase'] if async_ else [], timeout_s=None, cancellation='supported' if async_ else 'unsupported',
                                retry='same_request_id', resources=['host_clock', 'test_cell', 'coulox_sensor', 'flow_controllers'],
                                partial_failure_zh='停止/断电/超量程只终止该池检测序列；已记录读数、ReZero记录与膜内状态保留，测试气切回载气冲洗。'),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH[e], side_effects='partial' if async_ and e in _PARTIAL else 'none')
                         for e in _errors_of(k)],
                 verification=[], limitations=list(LIMITATIONS), source_ids=list(CAP_SOURCES[k]))
        if k in ('test.start', 'cell.load_unload', 'drawer.move', 'thermal.set_temperature', 'humidity.set', 'power.set', 'sample.mount'):
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh='由统一宿主时钟推进温湿度、机械动作、扩散、腔体吹扫、传感器与检测序列')
                                   for s in SLOTS if s not in v['effects'] and SLOTS[s]['origin'] in ('sensor', 'derived', 'scene', 'process_truth')]
        caps[k] = c
    mechanism = dict(
        component_ids=['thermal_module', 'rh_generator', 'cartridge_a', 'cartridge_b', 'film_a', 'film_b', 'cell_head_a', 'cell_head_b', 'coulox_sensor',
                       'rezero_cell', 'flow_controllers', 'catalyst_converter', 'barometer', 'controller_board'],
        state_ids=list(slots), meaning_zh='测试池热电控温与湿度发生、膜内一维Fick扩散（Arrhenius与水活度修正）、载气/测试气腔吹扫、边缘泄漏与环境渗入、库仑传感器与ReZero基线、检测序列与OTR归一',
        implementation='lumped',
        equations=['C_b dT/dt = Q_tec - G (T - T_amb) ; Q_tec = clamp(Kp e + I, -Q_max, Q_max) (conditional integration), exact exponential update per sub-step',
                   'dc/dt = D d2c/dx2 in the film ; c(0) = S p_up, c(L) = S p_dn ; P(T,aw) = P_ref exp(-E_P/R (1/T - 1/T_ref) + beta aw), D likewise, S = P/D ; backward-Euler finite volume (exactly conservative)',
                   'p_up -> y_O2 (P_baro - RH p_sat(T)) during conditioning/testing (0 when flushed with carrier), first order with tau = V_t/F_t',
                   'dn_h/dt = J_film A + k_edge p_up + k_amb (1 - eta_flush) p_O2,air + x_res n_c - (F_c/V_h) n_h (film and chamber solved in one backward-Euler tridiagonal system; F_c = 0 without carrier supply)',
                   'I_sensor -> 4 F eta n_in + I_bg, first order tau_s ; counts = I / 10 pA ; 1 cc/day = 0.000199 A',
                   'OTR = (I_exam - I_rezero)/(4F) / A x 760 mmHg / P_baro x 100 / permeant%, quantised to 0.002 cc/(m2 day) at 50 cm2',
                   'final OTR = mean(last N exams) - individual zero ; repeatability allowance = max(0.002 cc/(m2 day), 1 %)'],
        parameters={}, source_ids=['simulation_assumption', 'template_library'], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_oxtran_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'oxygen_permeation': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'gas.configure': (GAS, GAS_EXAMPLE), 'sample.mount': (MOUNT, MOUNT_EXAMPLE),
                                'test.configure': (TEST_CFG, TEST_CFG_EXAMPLE), 'instrument.configure': (INSTRUMENT, INSTRUMENT_EXAMPLE)})
    params = mechanism['parameters']
    for cap_name, (schema, example) in (('sample.mount', (MOUNT, MOUNT_EXAMPLE)), ('test.configure', (TEST_CFG, TEST_CFG_EXAMPLE)),
                                        ('instrument.configure', (INSTRUMENT, INSTRUMENT_EXAMPLE))):
        for name, prop in schema['properties'].items():
            if name in params or 'anyOf' not in prop:
                continue
            base = next(x for x in prop['anyOf'] if x.get('type') != 'null')
            params[name] = dict(value=example.get(name), unit=base.get('x-unit', '1'),
                                meaning_zh=f'{PARAM_ZH.get(name, name)}；宿主在 {cap_name} 显式输入，可为null（{NULLABLE_ZH[name]}）；此处登记示例值，非厂家标定或实测。',
                                evidence='assumption', source_ids=['simulation_assumption'],
                                uncertainty=f"schema 允许 {base.get('minimum')}–{base.get('maximum')} {base.get('x-unit', '1')} 或 null")
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'oxygen_permeation': mechanism})
