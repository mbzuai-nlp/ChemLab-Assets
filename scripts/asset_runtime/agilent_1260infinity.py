"""CHEM-094 Agilent 1260 Infinity III SFC system (G4301A SFC Control Module, G4782A SFC Binary Pump, G4767A SFC Multisampler,
G7116A Multicolumn Thermostat, G7115A DAD WR with SFC flow cell, solvent cabinet) - process runtime.

Physics (SI; the host's step(dt_s) is the only clock):
* CO2 path: host-connected cylinder (40-70 bar documented supply) -> control-module chiller (first order to a 278.15 K target inside the
  documented -20...9 degC range) -> booster -> pump channel A; modifier from bottle B1/B2 on channel B (high-pressure binary mixing).
* Hydraulics: pump pressure = BPR set pressure + Kozeny-Carman column drop (log-blended CO2/modifier viscosity) + extra-column drop;
  600 bar pump limit trips the pump (documented 600 bar); BPR 100-400 bar, 40-70 degC (documented G4301A ranges).
* Column oven: first-order heating / cooling with time constants derived from the documented MCT rates (ambient->40 degC ~5 min,
  40->20 degC ~10 min).
* Retention: per analyte, column and modifier the intercept a of log10 k = a - m log10(phi) comes from the measured CC BY 4.0
  retention times of the template library (back-solved, evidence 'derived'); the run integrates band migration
  dz/dt = 1 / (t0 (1 + k(phi(t - t_D - z t0)))) for the configured program, with t0 = eps_T * column volume / flow, the documented
  600-900 uL pump delay volume, and a density correction from a Peng-Robinson CO2 equation of state (NIST critical constants).
* Detection: DAD absorbance A = eps(lambda) * 10 mm * c(t) (documented SFC flow cell 10 mm / 13 uL); eps from NIST UV curves where the
  library holds one, else an assumed 1e3 m^2/mol (flagged); documented +-0.7e-5 AU noise and 0.9e-3 AU/h drift.
* Ledgers: vial volume and species moles (vial -> column -> waste/exhaust), modifier volume (bottle -> waste), CO2 mass (cylinder -> vent).

Sessions filter visibility: agents never see the vial composition, process truths or ledgers; template.read only returns public reference
data.  Matterix bridge: RECIPES module="asset_runtime.agilent_1260infinity", cls="Agilent1260infinityRuntime", instance_in_ctor=True, advance=STEP_DT.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

try:
    from asset_runtime import _annotations as _ann
except ImportError:  # pragma: no cover - imported as a plain module
    _ann = None

PROTOCOL_VERSION = '1.0.0'
ASSET_TYPE_ID = 'instrument.agilent_1260infinity'
CATALOG_ID = 'CHEM-094'
ROOT = Path(__file__).resolve().parents[2]
LIBRARY_REL = 'assets/reactions/agilent_1260infinity_sfc_retention_library/sfc_retention_library.json'

# ------------------------------------------------------------------------------------------------ documented specifications
FLOW_MIN, FLOW_MAX = 0.001e-6 / 60, 5.0e-6 / 60            # m^3/s, G4782A settable 0.001-5 mL/min
PUMP_MAX_PA = 600e5                                          # G4782A / system: 600 bar up to 5 mL/min
BPR_MIN_PA, BPR_MAX_PA = 100e5, 400e5                        # G4301A BPR settable 100-400 bar
BPR_T_MIN, BPR_T_MAX = 313.15, 343.15                        # G4301A BPR thermal range 40-70 degC
SUPPLY_MIN_PA, SUPPLY_MAX_PA = 40e5, 70e5                    # G4301A CO2 supply pressure 40-70 bar
CHILLER_TARGET_K = 278.15                                    # inside the documented -20...9 degC chiller range (assumption: 5 degC)
INJ_MIN, INJ_MAX, INJ_STEP = 0.1e-9, 90e-9, 0.1e-9           # G4767A SFC mode 0.1-90 uL in 0.1 uL steps
INJECT_CYCLE_S = 13.0                                        # G4767A SFC injection cycle < 13 s (1 uL)
COL_T_MAX = 358.15                                           # G7116A up to 85 degC; lower limit ambient - 10 K, >= 4 degC
COL_T_ABS_MIN = 277.15
N_COLUMN_POSITIONS = 4                                       # G7116A four columns up to 300 mm, 4-column selection valve
N_DRAWERS, N_VIAL_POSITIONS = 4, 108                         # 4 x 2H drawers, 432 x 2 mL vials in total (G4767A)
VIAL_CAPACITY_M3 = 2e-6
LAMBDA_MIN, LAMBDA_MAX = 190e-9, 950e-9                      # G7115A 190-950 nm
FLOW_CELL_PATH_M, FLOW_CELL_VOLUME_M3, FLOW_CELL_MAX_PA = 0.010, 13e-9, 400e5   # SFC flow cell 10 mm, 13 uL, 400 bar
DAD_NOISE_AU, DAD_DRIFT_AU_S = 0.7e-5, 0.9e-3 / 3600.0       # G7115A noise < +-0.7e-5 AU, drift < 0.9e-3 AU/h
DELAY_MIN_M3, DELAY_MAX_M3 = 600e-9, 900e-9                  # G4782A delay volume 600-900 uL incl. 400 uL mixer, depending on back pressure
MAX_PROGRAM_ROWS = 20                                        # assumption: table length (G4301A documents 20 BPR timetable entries)
# derived from documented MCT rates: ambient(25)->40 degC in ~5 min, 40->20 degC in ~10 min (to within 0.5 K)
COL_TAU_HEAT_S = 300.0 / math.log(15.0 / 0.5)
COL_TAU_COOL_S = 600.0 / math.log(20.0 / 0.5)

# ------------------------------------------------------------------------------------------------ assumptions (flagged)
TOTAL_POROSITY = 0.65            # eps_T of fully porous packed columns (t0 = eps_T * V_column / F)
BED_POROSITY = 0.40              # interparticle porosity (Kozeny-Carman)
LOGLOG_SLOPE_M = 1.5             # log10 k vs log10(phi) slope (Snyder-Soczewinski type), same for all analytes
PHI_MIN = 0.01                   # lower bound of the modifier fraction in the retention law
DENSITY_COEFF = 2.0              # d ln k / d(rho/rho_ref)
REF_FLOW_M3_S = 1.5e-6 / 60      # 1.5 mL/min of the source runs (documented in the articles)
REF_DWELL_S = 18.0               # UPC2 gradient delay of the source runs (0.45 mL at 1.5 mL/min) - not documented, assumption
REF_COLUMN_K, REF_BPR_PA = 313.15, 13.0e6                    # source runs: 40 degC, BPR 13 MPa (documented)
PLATE_HEIGHT_DP = 3.0            # reduced plate height h = H / dp
ETA_CO2_PA_S = 6.5e-5            # CO2 viscosity near 40 degC / 150 bar (engineering value)
ETA_MEOH_PA_S = 5.44e-4          # methanol, 25 degC (CRC Handbook)
EXTRA_COLUMN_R = 4.0e13          # Pa s/m^3 per 1e-4 Pa s: tubing + flow cell drop (~10 bar at 1.5 mL/min)
PRESSURE_TAU_S, FLOW_TAU_S, BPR_HEAT_TAU_S, CHILLER_TAU_S = 3.0, 1.0, 60.0, 300.0
ASSUMED_EPSILON_M2_MOL = 1.0e3   # 1e4 L/(mol cm) when no NIST UV curve is available
VIAL_DEAD_VOLUME_M3 = 10e-9      # needle cannot draw the last 10 uL
READY_TOL_PA, READY_TOL_K, READY_FLOW_FRACTION = 5e5, 1.0, 0.98
CO2_PUMP_DENSITY_KG_M3 = 900.0   # chilled liquid CO2 at the pump head (approx., used for CO2 mass consumption)

# CO2 critical constants (NIST WebBook / Span & Wagner 1996) for the Peng-Robinson density
CO2_TC, CO2_PC, CO2_OMEGA, CO2_M = 304.1282, 7.3773e6, 0.22394, 0.0440095
R_GAS = 8.314462618

MODULES = ('control', 'pump', 'sampler', 'mct', 'dad')
MODIFIERS = ('methanol', 'methanol_ammonia_10mM', 'methanol_water_2pct')
PHASES = ('idle', 'injecting', 'running', 'succeeded', 'cancelled', 'failed')
MECH = {  # mechanism -> (state id, unit, upper limit, component)
    'sampler_door': ('sampler_door.angle_rad', 'rad', 1.75, 'sampler_door'),
    'drawer_1': ('drawer_1.position_m', 'm', 0.30, 'drawer_1'),
    'drawer_2': ('drawer_2.position_m', 'm', 0.30, 'drawer_2'),
    'drawer_3': ('drawer_3.position_m', 'm', 0.30, 'drawer_3'),
    'drawer_4': ('drawer_4.position_m', 'm', 0.30, 'drawer_4'),
    'mct_door': ('mct_door.angle_rad', 'rad', 1.50, 'mct_door'),
}


class ActionRejected(ValueError):
    """Precondition / argument failure; the runtime state is unchanged.  Messages never disclose sample identity."""


# --------------------------------------------------------------------------------------------------------------- schemas
def number(unit, lo=None, hi=None):
    d = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        d['minimum'] = lo
    if hi is not None:
        d['maximum'] = hi
    return d


def integer(lo, hi):
    return {'type': 'integer', 'x-unit': '1', 'minimum': lo, 'maximum': hi}


def obj(props, required=None):
    return {'type': 'object', 'properties': props, 'required': list(props) if required is None else required, 'additionalProperties': False}


def arr(items, lo=None, hi=None):
    d = {'type': 'array', 'items': items}
    if lo is not None:
        d['minItems'] = lo
    if hi is not None:
        d['maxItems'] = hi
    return d


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


def enum(values):
    return {'type': 'string', 'enum': list(values)}


BOOL, TEXT = {'type': 'boolean'}, {'type': 'string'}
COLUMN_IDS = ('silica', 'bridged_ethylene_hybrid', 'diol', 'c18_hss_sb', 'pfp_csh', 'pgc')
PROGRAM_ROW = obj({'time_s': number('s', 0, 3600), 'modifier_fraction': number('1', 0.0, 1.0)})
METHOD = obj({
    'flow_m3_s': number('m^3/s', FLOW_MIN, FLOW_MAX), 'modifier_channel': enum(['B1', 'B2']),
    'program': arr(PROGRAM_ROW, 1, MAX_PROGRAM_ROWS), 'bpr_pa': number('Pa', BPR_MIN_PA, BPR_MAX_PA),
    'bpr_temperature_k': number('K', BPR_T_MIN, BPR_T_MAX), 'column_temperature_k': number('K', COL_T_ABS_MIN, COL_T_MAX),
    'column_position': integer(1, N_COLUMN_POSITIONS), 'injection_volume_m3': number('m^3', INJ_MIN, INJ_MAX),
    'wavelength_m': number('m', LAMBDA_MIN, LAMBDA_MAX), 'run_time_s': number('s', 10, 3600), 'data_rate_hz': number('Hz', 0.5, 10)})
METHOD_EXAMPLE = dict(flow_m3_s=REF_FLOW_M3_S, modifier_channel='B1',
                      program=[dict(time_s=0.0, modifier_fraction=0.02), dict(time_s=60.0, modifier_fraction=0.02),
                               dict(time_s=300.0, modifier_fraction=0.45), dict(time_s=360.0, modifier_fraction=0.45)],
                      bpr_pa=REF_BPR_PA, bpr_temperature_k=333.15, column_temperature_k=REF_COLUMN_K, column_position=1,
                      injection_volume_m3=2e-9, wavelength_m=254e-9, run_time_s=420.0, data_rate_hz=5.0)
SUPPLY = obj({'connected': BOOL, 'supply_pa': number('Pa', 0, 80e5), 'cylinder_co2_kg': number('kg', 0, 60), 'ambient_k': number('K', 288.15, 303.15)})
SUPPLY_EXAMPLE = dict(connected=True, supply_pa=57e5, cylinder_co2_kg=10.0, ambient_k=298.15)
BOTTLE = obj({'channel': enum(['B1', 'B2']), 'modifier': enum(MODIFIERS), 'volume_m3': number('m^3', 0, 2e-3)})
BOTTLE_EXAMPLE = dict(channel='B1', modifier='methanol', volume_m3=1e-3)
COLUMN_INSTALL = obj({'position': integer(1, N_COLUMN_POSITIONS), 'column': enum(list(COLUMN_IDS) + ['none'])})
VIAL = obj({'drawer': integer(1, N_DRAWERS), 'position': integer(1, N_VIAL_POSITIONS), 'volume_m3': number('m^3', 1e-8, VIAL_CAPACITY_M3),
            'composition_mol_m3': {'type': 'object', 'additionalProperties': number('mol/m^3', 0, 1000), 'maxProperties': 16}})
VIAL_EXAMPLE = dict(drawer=1, position=1, volume_m3=1e-6, composition_mol_m3={'caffeine': 0.257, 'vanillin': 0.329})
VIAL_REF = obj({'drawer': integer(1, N_DRAWERS), 'position': integer(1, N_VIAL_POSITIONS)})
PEAK = obj({'retention_time_s': number('s', 0), 'area_au_s': number('AU*s'), 'height_au': number('AU'), 'width_half_height_s': number('s', 0)})
CHROM = obj({'run_id': TEXT, 'phase': enum(PHASES), 'elapsed_s': number('s', 0), 'time_s': arr(number('s', 0)),
             'absorbance_au': arr(number('AU')), 'peaks': arr(PEAK), 'complete': BOOL, 'wavelength_m': nullable(number('m')),
             'template_source': TEXT, 'evidence': TEXT, 'warnings': arr(TEXT)})
QUANT_IN = obj({'calibration': arr(obj({'concentration_mol_m3': number('mol/m^3', 0, 1000), 'area_au_s': number('AU*s')}), 2, 20),
                'retention_window_s': arr(number('s', 0, 3600), 2, 2)})
QUANT_OUT = obj({'slope_au_s_m3_mol': number('AU*s*m^3/mol'), 'intercept_au_s': number('AU*s'), 'r_squared': number('1', 0, 1),
                 'peak_area_au_s': nullable(number('AU*s')), 'concentration_mol_m3': nullable(number('mol/m^3')), 'quality': TEXT})
TEMPLATE_OUT = obj({'substance_id': TEXT, 'name': TEXT, 'cas': nullable(TEXT), 'molecular_weight_g_mol': nullable(number('g/mol')),
                    'reference_retention_s': {'type': 'object', 'additionalProperties': {'type': 'object', 'additionalProperties': nullable(number('s', 0))}},
                    'uv_curve': BOOL, 'template_source': TEXT, 'evidence': TEXT, 'licence': TEXT})
LEDGER = obj({'vial_volume_m3': number('m^3', 0), 'injected_volume_m3': number('m^3', 0), 'volume_residual_m3': number('m^3'),
              'species_residual_mol': number('mol'), 'injected_mol': number('mol', 0), 'column_mol': number('mol', 0), 'eluted_mol': number('mol', 0),
              'modifier_drawn_m3': number('m^3', 0), 'modifier_residual_m3': number('m^3'), 'co2_used_kg': number('kg', 0), 'co2_residual_kg': number('kg'),
              'waste_m3': number('m^3', 0)})
EMPTY = obj({})

# capability -> (input, output, components, meaning, kind, visibility, example, effects)
CAPS = {}


def _cap(key, inp, out, comps, meaning, kind='device_command', vis='agent', example=None, effects=()):
    CAPS[key] = (inp, out, list(comps), meaning, kind, vis, example if example is not None else {}, list(effects))


_cap('power.set', obj({'enabled': BOOL}), obj({'enabled': BOOL}), ['pump_power_switch', 'sampler_power_switch', 'mct_power_switch', 'dad_power_switch', 'control_power_switch'],
     '整套五个模块统一通断电（软件/机器人整体操作）；断电停泵、取消运行，保留已注入物质', example={'enabled': True},
     effects=['power.enabled', 'pump.enabled', 'run.phase'] + [f'power.{m}' for m in MODULES])
_cap('power.toggle', obj({'module': enum(MODULES)}), obj({'module': enum(MODULES), 'enabled': BOOL}),
     ['pump_power_switch', 'sampler_power_switch', 'mct_power_switch', 'dad_power_switch', 'control_power_switch'],
     '单个模块前面板（控制模块为背面）电源开关翻转；任一模块断电取消进行中的运行，仅泵或控制模块断电清除泵运行目标', example={'module': 'pump'},
     effects=['power.enabled', 'pump.enabled', 'run.phase'] + [f'power.{m}' for m in MODULES])
_cap('supply.configure', SUPPLY, obj({'configured': BOOL}), ['co2_inlet', 'control_housing'],
     '宿主连接CO2气瓶：供气表压、可用CO2质量与环境温度（实验室管路边界，非机器人动作）', 'configuration', 'evaluator', SUPPLY_EXAMPLE,
     ['supply.connected', 'supply.pressure_pa', 'co2.ready'])
_cap('solvent.configure', BOTTLE, obj({'configured': BOOL}), ['bottle_b1', 'bottle_b2', 'solvent_cabinet'],
     '宿主在溶剂柜放置改性剂瓶B1/B2（甲醇、甲醇+10 mmol/L氨、甲醇+2%水）及体积', 'configuration', 'evaluator', BOTTLE_EXAMPLE, ['solvent.b1', 'solvent.b2'])
_cap('column.install', COLUMN_INSTALL, obj({'position': integer(1, N_COLUMN_POSITIONS), 'column': enum(list(COLUMN_IDS) + ['none'])}),
     ['column_1', 'column_2', 'column_3', 'column_4', 'mct_door'],
     '在柱温箱四个柱位安装/拆下模板库六种固定相之一（100×3.0 mm）；要求柱温箱门打开且泵停', 'configuration', 'evaluator',
     {'position': 1, 'column': 'silica'}, ['column.installed'])
_cap('sample.register_vial', VIAL, obj({'registered': BOOL}), ['drawer_1', 'drawer_2', 'drawer_3', 'drawer_4', 'vial_tray_1'],
     '宿主登记已放入抽屉托盘的样品瓶：体积与真值组成（物质为模板库substance_id）；要求该抽屉拉出', 'configuration', 'evaluator', VIAL_EXAMPLE,
     ['sample.vial_count'])
_cap('sample.remove_vial', VIAL_REF, obj({'returned_m3': number('m^3', 0)}), ['drawer_1', 'drawer_2', 'drawer_3', 'drawer_4'],
     '取走样品瓶（返还剩余液体，已注入部分留在账本中）；要求抽屉拉出', 'configuration', 'evaluator', {'drawer': 1, 'position': 1}, ['sample.vial_count'])
_cap('method.configure', METHOD, obj({'configured': BOOL, 'delay_volume_m3': number('m^3', DELAY_MIN_M3, DELAY_MAX_M3)}),
     ['pump_head_a', 'pump_head_b', 'column_valve', 'bpr_unit', 'flow_cell', 'sampler_housing'],
     '写入方法目标：流量、改性剂通道与梯度表、背压与背压阀温度、柱温、柱位、进样体积、检测波长、运行时间与采样率；只写目标，实际值随step演化',
     'configuration', 'agent', METHOD_EXAMPLE,
     ['method.configured', 'pump.flow_target_m3_s', 'bpr.target_pa', 'bpr.temperature_target_k', 'column.target_k', 'column.selected', 'detector.wavelength_m'])
_cap('pump.set', obj({'enabled': BOOL}), obj({'enabled': BOOL}), ['pump_head_a', 'pump_head_b', 'booster_unit'],
     '泵运行目标（平衡/冲洗按方法初始组成）；需全部模块通电、CO2就绪、已配置方法且所选柱位有柱', example={'enabled': True}, effects=['pump.enabled'])
for _m, (_sid, _unit, _hi, _comp) in MECH.items():
    _arg = 'angle_rad' if _unit == 'rad' else 'position_m'
    _cap(f'{_m}.set', obj({_arg: number(_unit, 0, _hi)}), obj({_arg: number(_unit, 0, _hi)}), [_comp],
         '宿主/场景权威机构位置（0=关闭）；运行或进样中拒绝打开进样器门、抽屉与柱温箱门', example={_arg: 0.0}, effects=[_sid])
_cap('run.start', VIAL_REF, obj({'phase': enum(PHASES), 'operation_id': TEXT}), ['needle_assembly', 'sampler_housing', 'column_valve', 'flow_cell'],
     '从指定抽屉/瓶位进样并开始采集：系统就绪（流量、背压、柱温在容差内）、门和该抽屉关闭、瓶内余量足够；异步，按step推进到运行时间结束',
     example={'drawer': 1, 'position': 1}, effects=['run.phase', 'run.elapsed_s', 'sample.vial_count'])
_cap('run.abort', EMPTY, obj({'phase': enum(PHASES)}), ['needle_assembly', 'flow_cell'], '取消当前进样/采集，保留已采集数据与物料账本',
     effects=['run.phase'])
_cap('chromatogram.read', obj({'max_points': integer(10, 40000)}, required=[]), CHROM, ['flow_cell', 'dad_housing'],
     '读取最近一次运行已采集的色谱图与峰表（无物质身份）；纯读取，不推进时钟', 'query', example={'max_points': 2000})
_cap('peaks.quantify', QUANT_IN, QUANT_OUT, ['flow_cell'],
     '用调用者提供的外标点（浓度-峰面积）线性拟合，并对最近完成运行中保留时间窗内最大峰定量；不访问样品真值', 'analysis', example=dict(
         calibration=[dict(concentration_mol_m3=0.1, area_au_s=0.01), dict(concentration_mol_m3=0.5, area_au_s=0.05)], retention_window_s=[100.0, 200.0]))
_cap('template.read', obj({'substance_id': TEXT}), TEMPLATE_OUT, ['flow_cell'],
     '查询公开的SFC保留时间模板（参考方法下六种柱×三种改性剂的实测首次进样保留时间）；与样品身份无关', 'query', example={'substance_id': 'caffeine'})
_cap('ledger.read', EMPTY, LEDGER, ['vial_tray_1', 'column_1', 'waste_port', 'co2_inlet'],
     '私有物料账本：样品瓶体积与物质（瓶→柱→废液/排气）、改性剂、CO2与废液守恒残差', 'query', 'evaluator')
UNSUPPORTED = ('chiral.separate', 'ms.detect', 'hardware.connect', 'sample_thermostat.set')
for _k, _z in (('chiral.separate', '手性柱与对映体分离：模板库无手性固定相实测数据，未实现'),
               ('ms.detect', '质谱联用：本配置只有DAD，MS未实现'),
               ('hardware.connect', '真实仪器（OpenLab CDS/RC.NET）连接未实现'),
               ('sample_thermostat.set', '进样器样品恒温器为选配件，本配置未建模')):
    _cap(_k, EMPTY, EMPTY, ['sampler_housing' if 'sample' in _k else 'control_housing'], _z)

# state slots: key -> (schema, unit, origin, visibility, components, write (cap, arg) or None, meaning_zh)
SLOTS = {}


def _slot(key, schema, unit, origin, vis, comps, write=None, meaning=''):
    SLOTS[key] = (schema, unit, origin, vis, list(comps), write, meaning)


_slot('power.enabled', BOOL, '1', 'derived', 'agent', ['pump_power_switch'], None, '五个模块全部通电时为true')
for _m in MODULES:
    _slot(f'power.{_m}', BOOL, '1', 'setpoint', 'agent', [f'{_m}_power_switch'], None, f'{_m}模块电源开关状态')
    _slot(f'status.{_m}', enum(['off', 'not_ready', 'ready', 'run', 'error']), '1', 'derived', 'agent', [f'{_m}_status_led'], None,
          f'{_m}模块状态指示灯代理（off/not_ready黄/ready绿/run/error红）')
_slot('supply.connected', BOOL, '1', 'scene', 'agent', ['co2_inlet'], None, 'CO2气瓶是否接到控制模块进口')
_slot('supply.pressure_pa', number('Pa', 0), 'Pa', 'derived', 'agent', ['co2_inlet'], None, 'CO2供气表压（气瓶空时为0）')
_slot('co2.ready', BOOL, '1', 'derived', 'agent', ['chiller_unit'], None, '供气压力在40–70 bar且冷却器≤9 °C时CO2调理就绪')
_slot('chiller.temperature_k', number('K', 0), 'K', 'process_truth', 'evaluator', ['chiller_unit'], None, 'CO2再液化冷却器温度真值')
_slot('booster.pressure_pa', number('Pa', 0), 'Pa', 'process_truth', 'evaluator', ['booster_unit'], None, '增压泵出口表压真值（略低于柱头压力）')
_slot('method.configured', BOOL, '1', 'setpoint', 'agent', ['sampler_housing'])
_slot('pump.enabled', BOOL, '1', 'setpoint', 'agent', ['pump_head_a'], ('pump.set', 'enabled'))
_slot('pump.flow_target_m3_s', nullable(number('m^3/s', FLOW_MIN, FLOW_MAX)), 'm^3/s', 'setpoint', 'agent', ['pump_head_a'])
_slot('pump.flow_m3_s', number('m^3/s', 0), 'm^3/s', 'derived', 'agent', ['pump_head_a'], None, '泵实际总体积流量（泵头条件，未作压缩性换算）')
_slot('pump.modifier_fraction', number('1', 0, 1), '1', 'derived', 'agent', ['pump_head_b'], None, '泵出口改性剂体积分数（梯度表当前值）')
_slot('pump.pressure_pa', number('Pa', 0), 'Pa', 'process_truth', 'evaluator', ['pump_head_a'], None, '泵出口表压真值')
_slot('pump.indicated_pressure_pa', number('Pa', 0), 'Pa', 'derived', 'agent', ['pump_head_a'], None, '泵压力显示（0.1 bar量化）')
_slot('bpr.target_pa', nullable(number('Pa', BPR_MIN_PA, BPR_MAX_PA)), 'Pa', 'setpoint', 'agent', ['bpr_unit'])
_slot('bpr.pressure_pa', number('Pa', 0), 'Pa', 'process_truth', 'evaluator', ['bpr_unit'], None, '背压阀入口表压真值（系统出口压力）')
_slot('bpr.indicated_pressure_pa', number('Pa', 0), 'Pa', 'derived', 'agent', ['bpr_unit'], None, '背压显示（±0.25 bar精度代理，0.01 bar量化）')
_slot('bpr.temperature_target_k', nullable(number('K', BPR_T_MIN, BPR_T_MAX)), 'K', 'setpoint', 'agent', ['bpr_unit'])
_slot('bpr.temperature_k', number('K', 0), 'K', 'process_truth', 'evaluator', ['bpr_unit'], None, '背压阀加热头温度真值')
_slot('column.target_k', nullable(number('K', COL_T_ABS_MIN, COL_T_MAX)), 'K', 'setpoint', 'agent', ['mct_housing'])
_slot('column.temperature_k', number('K', 0), 'K', 'process_truth', 'evaluator', ['mct_housing'], None, '柱温箱温区温度真值（单节点）')
_slot('column.indicated_k', number('K', 0), 'K', 'derived', 'agent', ['mct_housing'], None, '柱温显示（0.1 K量化）')
_slot('column.selected', nullable(integer(1, N_COLUMN_POSITIONS)), '1', 'setpoint', 'agent', ['column_valve'])
_slot('column.installed', {'type': 'array', 'items': enum(list(COLUMN_IDS) + ['none']), 'minItems': 4, 'maxItems': 4}, '1', 'scene', 'agent',
      ['column_1', 'column_2', 'column_3', 'column_4'], None, '四个柱位安装的固定相类型（实验室配置，agent可见）')
_slot('mobile_phase.co2_density_kg_m3', nullable(number('kg/m^3', 0)), 'kg/m^3', 'process_truth', 'evaluator', ['column_1'], None,
      '柱温与背压下纯CO2密度（Peng-Robinson）')
_slot('detector.wavelength_m', nullable(number('m', LAMBDA_MIN, LAMBDA_MAX)), 'm', 'setpoint', 'agent', ['flow_cell'])
_slot('detector.absorbance_au', number('AU'), 'AU', 'derived', 'agent', ['flow_cell'], None, '流通池当前吸光度（含噪声与漂移）')
_slot('system.ready', BOOL, '1', 'derived', 'agent', ['control_status_led'], None, '流量、背压、柱温均到达方法目标容差内')
_slot('system.fault', TEXT, '1', 'derived', 'agent', ['control_status_led'], None, '最近故障代码（空串=无）')
_slot('run.phase', enum(PHASES), '1', 'derived', 'agent', ['dad_status_led'])
_slot('run.elapsed_s', number('s', 0), 's', 'derived', 'agent', ['dad_status_led'], None, '自进样阀切换（采集零点）起的运行时间')
_slot('run.count', integer(0, 10 ** 9), '1', 'derived', 'agent', ['dad_status_led'], None, '已启动的运行次数')
_slot('sample.vial_count', integer(0, N_DRAWERS * N_VIAL_POSITIONS), '1', 'scene', 'agent', ['vial_tray_1'], None, '已登记样品瓶数')
for _m, (_sid, _unit, _hi, _comp) in MECH.items():
    _slot(_sid, number(_unit, 0, _hi), _unit, 'scene', 'agent', [_comp], (f'{_m}.set', 'angle_rad' if _unit == 'rad' else 'position_m'))
_slot('solvent.b1', nullable(enum(MODIFIERS)), '1', 'scene', 'agent', ['bottle_b1'], None, 'B1瓶改性剂类型')
_slot('solvent.b2', nullable(enum(MODIFIERS)), '1', 'scene', 'agent', ['bottle_b2'], None, 'B2瓶改性剂类型')
_slot('truth.bottle_b1_m3', number('m^3', 0), 'm^3', 'process_truth', 'evaluator', ['bottle_b1'], None, 'B1瓶剩余改性剂体积')
_slot('truth.bottle_b2_m3', number('m^3', 0), 'm^3', 'process_truth', 'evaluator', ['bottle_b2'], None, 'B2瓶剩余改性剂体积')
_slot('truth.cylinder_co2_kg', number('kg', 0), 'kg', 'process_truth', 'evaluator', ['co2_inlet'], None, '气瓶剩余CO2质量')
_slot('truth.vented_co2_kg', number('kg', 0), 'kg', 'process_truth', 'evaluator', ['exhaust_port'], None, '经背压阀排出的CO2累计质量')
_slot('truth.waste_m3', number('m^3', 0), 'm^3', 'process_truth', 'evaluator', ['waste_port'], None, '废液（改性剂+样品溶剂）累计体积')
_slot('truth.column_mol', number('mol', 0), 'mol', 'process_truth', 'evaluator', ['column_1'], None, '已注入但尚未流出色谱柱的物质总量')
_slot('truth.eluted_mol', number('mol', 0), 'mol', 'process_truth', 'evaluator', ['waste_port'], None, '已流出到废液/排气的物质总量')

REFERENCE_ZH = {
    'supply.pressure_pa': '表压；CO2供气相对环境大气压。厂家40–70 bar（580–1000 psi）按表压解释，这是本资产的假设。',
    'booster.pressure_pa': '表压；相对环境大气压。', 'pump.pressure_pa': '表压；相对环境大气压，=背压+柱压降+柱外压降。',
    'pump.indicated_pressure_pa': '表压；相对环境大气压。厂家600 bar上限按表压解释。',
    'bpr.target_pa': '表压；背压阀设定相对环境大气压（厂家100–400 bar）。', 'bpr.pressure_pa': '表压；相对环境大气压。',
    'bpr.indicated_pressure_pa': '表压；相对环境大气压。',
    'pump.flow_target_m3_s': '泵头条件下的总体积流量（CO2+改性剂），与厂家mL/min设定一致；未作可压缩性换算。',
    'pump.flow_m3_s': '泵头条件下的总体积流量（CO2+改性剂）；色谱模型按柱内体积流量近似使用。',
    'mobile_phase.co2_density_kg_m3': '纯CO2在柱温与背压（绝压=表压+101325 Pa）下的Peng-Robinson密度；忽略改性剂对密度的影响。',
}


# ------------------------------------------------------------------------------------------------------ physical models
def co2_density(temperature_k, pressure_abs_pa):
    """Peng-Robinson density of pure CO2 (dense root).  NIST critical constants; ~5-10 % below reference data in the dense region."""
    a = 0.45724 * R_GAS ** 2 * CO2_TC ** 2 / CO2_PC
    b = 0.07780 * R_GAS * CO2_TC / CO2_PC
    kappa = 0.37464 + 1.54226 * CO2_OMEGA - 0.26992 * CO2_OMEGA ** 2
    alpha = (1 + kappa * (1 - math.sqrt(temperature_k / CO2_TC))) ** 2
    A = a * alpha * pressure_abs_pa / (R_GAS * temperature_k) ** 2
    B = b * pressure_abs_pa / (R_GAS * temperature_k)
    roots = np.roots([1.0, -(1 - B), A - 3 * B ** 2 - 2 * B, -(A * B - B ** 2 - B ** 3)])
    real = sorted(r.real for r in roots if abs(r.imag) < 1e-9 and r.real > B)
    z = real[0]
    return float(pressure_abs_pa * CO2_M / (z * R_GAS * temperature_k))


@lru_cache(maxsize=1)
def _rho_ref():
    return co2_density(REF_COLUMN_K, REF_BPR_PA + 101325.0)


def void_time_s(length_m, inner_diameter_m, flow_m3_s):
    return TOTAL_POROSITY * math.pi * (inner_diameter_m / 2) ** 2 * length_m / flow_m3_s


def delay_volume_m3(bpr_pa):
    """Documented 600-900 uL 'depending on back pressure'; linear in the 100-400 bar BPR range (mapping is an assumption)."""
    f = min(1.0, max(0.0, (bpr_pa - BPR_MIN_PA) / (BPR_MAX_PA - BPR_MIN_PA)))
    return DELAY_MIN_M3 + f * (DELAY_MAX_M3 - DELAY_MIN_M3)


def mixture_viscosity(phi):
    phi = min(1.0, max(0.0, phi))
    return math.exp(phi * math.log(ETA_MEOH_PA_S) + (1 - phi) * math.log(ETA_CO2_PA_S))


def column_pressure_drop(flow_m3_s, phi, column):
    """Kozeny-Carman drop over the packed bed (Pa)."""
    area = math.pi * (column['inner_diameter_m'] / 2) ** 2
    u_s = flow_m3_s / area
    e = BED_POROSITY
    return 180.0 * mixture_viscosity(phi) * u_s * column['length_m'] * (1 - e) ** 2 / (e ** 3 * column['particle_m'] ** 2)


def program_value(program, t):
    """Piecewise-linear modifier fraction; holds the first value before t=0 and the last after the table."""
    times = np.array([p[0] for p in program], float)
    vals = np.array([p[1] for p in program], float)
    return np.interp(t, times, vals)


def migrate(a, m, program, t0_s, delay_s, t_end_s, dt_s=0.05, extra_ln_k=0.0):
    """Vectorised band migration under a gradient.  a: array of log10 k at phi = 1.  Returns (t_R, k at elution, phi at elution);
    NaN where the band has not left the column by t_end_s."""
    a = np.atleast_1d(np.asarray(a, float))
    z = np.zeros_like(a)
    tr = np.full_like(a, np.nan)
    ke = np.full_like(a, np.nan)
    pe = np.full_like(a, np.nan)
    times = np.array([p[0] for p in program], float)
    vals = np.array([p[1] for p in program], float)
    t = 0.0
    while t < t_end_s and np.isnan(tr).any():
        phi = np.interp(t - delay_s - z * t0_s, times, vals)
        k = 10.0 ** (a - m * np.log10(np.maximum(phi, PHI_MIN))) * math.exp(extra_ln_k)
        v = 1.0 / (t0_s * (1.0 + k))
        z_new = z + v * dt_s
        done = (z_new >= 1.0) & np.isnan(tr)
        if done.any():
            frac = (1.0 - z[done]) / (z_new[done] - z[done])
            tr[done] = t + frac * dt_s
            ke[done] = k[done]
            pe[done] = phi[done]
        z = np.where(np.isnan(tr), z_new, z)
        t += dt_s
    return tr, ke, pe


@lru_cache(maxsize=1)
def library():
    data = json.loads((ROOT / LIBRARY_REL).read_text(encoding='utf-8'))
    return data


@lru_cache(maxsize=1)
def library_sha256():
    return hashlib.sha256((ROOT / LIBRARY_REL).read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def columns():
    """Stationary phases of the source data by id (stored as a list in the library so that column ids are never mistaken for compound ids)."""
    return {c['column_id']: c for c in library()['columns']}


@lru_cache(maxsize=1)
def templates():
    return {r['substance_id']: r for r in library()['templates']}


def epsilon(sid, wavelength_m):
    """(epsilon m^2/mol, evidence) at the wavelength: NIST curve when the library has one covering it, else the flagged assumption."""
    uv = templates()[sid].get('uv_vis')
    if uv and uv['wavelength_m'][0] <= wavelength_m <= uv['wavelength_m'][-1]:
        log_eps = float(np.interp(wavelength_m, uv['wavelength_m'], uv['log10_epsilon_l_mol_cm']))
        return 10.0 ** log_eps * 0.1, 'measured_nist_uv'          # L/(mol cm) -> m^2/mol
    return ASSUMED_EPSILON_M2_MOL, 'assumption'


def baseline(time_s, signal, segment_s=30.0):
    """Slowly varying baseline: 20th percentile of each segment, linearly interpolated (removes detector drift before peak picking)."""
    t = np.asarray(time_s, float)
    y = np.asarray(signal, float)
    if len(t) < 3:
        return np.zeros_like(y)
    edges = np.arange(t[0], t[-1] + segment_s, segment_s)
    cx, cy = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (t >= lo) & (t < hi)
        if sel.sum() >= 3:
            cx.append(float(t[sel].mean()))
            cy.append(float(np.percentile(y[sel], 20)))
    if not cx:
        return np.full_like(y, float(np.percentile(y, 20)))
    return np.interp(t, cx, cy)


def detect_peaks(time_s, signal, threshold):
    """Baseline-corrected local maxima above threshold; area by trapezoid between the neighbouring minima (or the trace ends)."""
    t = np.asarray(time_s, float)
    y = np.asarray(signal, float) - baseline(time_s, signal)
    peaks = []
    if len(y) < 5:
        return peaks
    i = 1
    while i < len(y) - 1:
        if y[i] >= threshold and y[i] >= y[i - 1] and y[i] > y[i + 1]:
            lo = i
            while lo > 0 and y[lo - 1] <= y[lo] and y[lo - 1] > threshold * 0.2:
                lo -= 1
            hi = i
            while hi < len(y) - 1 and y[hi + 1] <= y[hi] and y[hi + 1] > threshold * 0.2:
                hi += 1
            seg_t, seg_y = t[max(0, lo - 1):hi + 2], y[max(0, lo - 1):hi + 2]
            area = float(np.trapezoid(seg_y, seg_t)) if len(seg_t) > 1 else 0.0
            half = y[i] / 2
            above = np.where(seg_y >= half)[0]
            width = float(seg_t[above[-1]] - seg_t[above[0]]) if len(above) > 1 else 0.0
            peaks.append(dict(retention_time_s=float(t[i]), area_au_s=area, height_au=float(y[i]), width_half_height_s=width))
            i = hi + 1
        else:
            i += 1
    return peaks


def validate(schema, value):
    if 'anyOf' in schema:
        for s in schema['anyOf']:
            try:
                validate(s, value)
                return
            except ActionRejected:
                pass
        raise ActionRejected('INVALID_ARGUMENT')
    t = schema.get('type')
    if t in ('number', 'integer'):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ActionRejected('INVALID_ARGUMENT')
        if t == 'integer' and (not isinstance(value, int)):
            raise ActionRejected('INVALID_ARGUMENT')
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
    elif t == 'boolean':
        if type(value) is not bool:
            raise ActionRejected('INVALID_ARGUMENT')
    elif t == 'string':
        if not isinstance(value, str):
            raise ActionRejected('INVALID_ARGUMENT')
    elif t == 'null':
        if value is not None:
            raise ActionRejected('INVALID_ARGUMENT')
    elif t == 'array':
        if not isinstance(value, list) or not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
        for v in value:
            validate(schema['items'], v)
    elif t == 'object':
        if not isinstance(value, dict) or not set(schema.get('required', [])) <= set(value):
            raise ActionRejected('INVALID_ARGUMENT')
        if len(value) > schema.get('maxProperties', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
        props = schema.get('properties', {})
        for k, v in value.items():
            if not isinstance(k, str):
                raise ActionRejected('INVALID_ARGUMENT')
            sub = props.get(k, schema.get('additionalProperties', True))
            if sub is False:
                raise ActionRejected('INVALID_ARGUMENT')
            if isinstance(sub, dict):
                validate(sub, v)
    if 'enum' in schema and value not in schema['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# --------------------------------------------------------------------------------------------------------------- runtime
class Agilent1260infinityRuntime:
    """Process model of one SFC system instance (host-owned clock)."""

    def __init__(self, instance_id='sfc_01'):
        self.instance_id = instance_id
        self.time_s = 0.0
        self.revision = 0
        self.ambient_k = 298.15
        self.power = {m: False for m in MODULES}
        self.supply = dict(connected=False, supply_pa=0.0, cylinder_co2_kg=0.0)
        self.co2_initial_kg = 0.0
        self.vented_co2_kg = 0.0
        self.bottles = {'B1': None, 'B2': None}
        self.modifier_loaded_m3 = 0.0
        self.modifier_drawn_m3 = 0.0
        self.waste_m3 = 0.0
        self.columns = {p: 'none' for p in range(1, N_COLUMN_POSITIONS + 1)}
        self.mech = {m: 0.0 for m in MECH}
        self.vials = {}
        self.vial_loaded_m3 = 0.0
        self.vial_returned_m3 = 0.0
        self.injected_m3 = 0.0
        self.method = None
        self.pump_enabled = False
        self.chiller_k = 298.15
        self.booster_pa = 0.0
        self.flow = 0.0
        self.pump_pa = 0.0
        self.bpr_pa = 0.0
        self.bpr_k = 298.15
        self.column_k = 298.15
        self.fault = ''
        self.phase = 'idle'
        self.run = None
        self.run_count = 0
        self.operation_id = None
        self.bands = []           # {'sid','mol','t_r','sigma','eluted'}
        self.injected_mol = 0.0
        self.eluted_mol = 0.0
        self.absorbance = 0.0
        self._principals = {'__public_agent__': 'agent'}
        self._requests = {}
        self._owners = {}

    # ---- principals / sessions -------------------------------------------------------------------------
    def register_principal(self, principal_id, visibility='agent'):
        if visibility not in ('agent', 'evaluator', 'debug'):
            raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:
            raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id] = visibility

    def session(self, principal_id='__public_agent__'):
        if principal_id not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return Agilent1260infinitySession(self, principal_id)

    def describe(self):
        return self.session().describe(instance_id=self.instance_id)

    # ---- derived quantities ---------------------------------------------------------------------------------
    def _column(self):
        if not self.method:
            return None
        cid = self.columns[self.method['column_position']]
        return None if cid == 'none' else dict(columns()[cid], id=cid)

    def _program(self):
        return [(r['time_s'], r['modifier_fraction']) for r in self.method['program']]

    def _phi_now(self):
        if not self.method:
            return 0.0
        prog = self._program()
        if self.phase == 'running' and self.run:
            return float(program_value(prog, self.run['elapsed']))
        return float(prog[0][1])

    def _all_powered(self):
        return all(self.power.values())

    def _co2_ready(self):
        return (self.supply['connected'] and SUPPLY_MIN_PA <= self.supply['supply_pa'] <= SUPPLY_MAX_PA + 1e5
                and self.supply['cylinder_co2_kg'] > 0 and self.chiller_k <= 282.15 and self.power['control'])

    def _ready(self):
        if not (self.method and self.pump_enabled and self._all_powered() and self._co2_ready() and not self.fault):
            return False
        m = self.method
        return (self.flow >= READY_FLOW_FRACTION * m['flow_m3_s'] and abs(self.bpr_pa - m['bpr_pa']) <= READY_TOL_PA
                and abs(self.column_k - m['column_temperature_k']) <= READY_TOL_K)

    def _status(self, module):
        if not self.power[module]:
            return 'off'
        if self.fault:
            return 'error'
        if self.phase in ('injecting', 'running'):
            return 'run'
        return 'ready' if self._ready() else 'not_ready'

    def _density(self):
        if not self.method:
            return None
        return co2_density(self.column_k, max(self.bpr_pa, 1e5) + 101325.0)

    def _values(self):
        m = self.method or {}
        v = {'power.enabled': self._all_powered()}
        for mod in MODULES:
            v[f'power.{mod}'] = self.power[mod]
            v[f'status.{mod}'] = self._status(mod)
        v.update({
            'supply.connected': self.supply['connected'],
            'supply.pressure_pa': self.supply['supply_pa'] if self.supply['connected'] and self.supply['cylinder_co2_kg'] > 0 else 0.0,
            'co2.ready': bool(self._co2_ready()), 'chiller.temperature_k': self.chiller_k, 'booster.pressure_pa': self.booster_pa,
            'method.configured': self.method is not None, 'pump.enabled': self.pump_enabled,
            'pump.flow_target_m3_s': m.get('flow_m3_s'), 'pump.flow_m3_s': self.flow,
            'pump.modifier_fraction': self._phi_now() if self.pump_enabled else 0.0,
            'pump.pressure_pa': self.pump_pa, 'pump.indicated_pressure_pa': round(self.pump_pa / 1e4) * 1e4,
            'bpr.target_pa': m.get('bpr_pa'), 'bpr.pressure_pa': self.bpr_pa, 'bpr.indicated_pressure_pa': round(self.bpr_pa / 1e3) * 1e3,
            'bpr.temperature_target_k': m.get('bpr_temperature_k'), 'bpr.temperature_k': self.bpr_k,
            'column.target_k': m.get('column_temperature_k'), 'column.temperature_k': self.column_k,
            'column.indicated_k': round(self.column_k * 10) / 10, 'column.selected': m.get('column_position'),
            'column.installed': [self.columns[p] for p in range(1, N_COLUMN_POSITIONS + 1)],
            'mobile_phase.co2_density_kg_m3': self._density(), 'detector.wavelength_m': m.get('wavelength_m'),
            'detector.absorbance_au': self.absorbance, 'system.ready': self._ready(), 'system.fault': self.fault,
            'run.phase': self.phase, 'run.elapsed_s': self.run['elapsed'] if self.run else 0.0, 'run.count': self.run_count,
            'sample.vial_count': len(self.vials),
            'solvent.b1': (self.bottles['B1'] or {}).get('modifier'), 'solvent.b2': (self.bottles['B2'] or {}).get('modifier'),
            'truth.bottle_b1_m3': (self.bottles['B1'] or {}).get('volume_m3', 0.0), 'truth.bottle_b2_m3': (self.bottles['B2'] or {}).get('volume_m3', 0.0),
            'truth.cylinder_co2_kg': self.supply['cylinder_co2_kg'], 'truth.vented_co2_kg': self.vented_co2_kg, 'truth.waste_m3': self.waste_m3,
            'truth.column_mol': sum(b['mol'] for b in self.bands if not b['eluted']), 'truth.eluted_mol': self.eluted_mol})
        for name, (sid, *_r) in MECH.items():
            v[sid] = self.mech[name]
        return v

    def read(self, visibility='agent'):
        return copy.deepcopy({k: val for k, val in self._values().items() if SLOTS[k][3] == 'agent' or visibility in ('evaluator', 'debug')})

    def _ledger(self):
        vial = sum(x['volume_m3'] for x in self.vials.values())
        species_in_vials = sum(x['volume_m3'] * sum(x['composition'].values()) for x in self.vials.values())
        column = sum(b['mol'] for b in self.bands if not b['eluted'])
        bottles = sum((b or {}).get('volume_m3', 0.0) for b in self.bottles.values())
        return dict(vial_volume_m3=vial, injected_volume_m3=self.injected_m3,
                    volume_residual_m3=self.vial_loaded_m3 - vial - self.injected_m3 - self.vial_returned_m3,
                    species_residual_mol=self._species_loaded - species_in_vials - self.injected_mol - self._species_returned,
                    injected_mol=self.injected_mol, column_mol=column, eluted_mol=self.eluted_mol,
                    modifier_drawn_m3=self.modifier_drawn_m3, modifier_residual_m3=self.modifier_loaded_m3 - bottles - self.modifier_drawn_m3,
                    co2_used_kg=self.vented_co2_kg, co2_residual_kg=self.co2_initial_kg - self.supply['cylinder_co2_kg'] - self.vented_co2_kg,
                    waste_m3=self.waste_m3)

    _species_loaded = 0.0
    _species_returned = 0.0

    # ---- capabilities -----------------------------------------------------------------------------------------
    def invoke(self, capability, **a):
        if capability not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability][0], a)
        if CAPS[capability][4] in ('query', 'analysis'):       # pure reads / caller-data analysis: no state snapshot needed
            result = self._invoke(capability, a)
            validate(CAPS[capability][1], result)
            return copy.deepcopy(result)
        before = copy.deepcopy({k: v for k, v in self.__dict__.items() if k not in ('_principals', '_requests', '_owners')})
        try:
            result = self._invoke(capability, a)
            validate(CAPS[capability][1], result)
        except Exception:
            self.__dict__.update(before)
            raise
        self.revision += 1
        return copy.deepcopy(result)

    def _busy(self):
        return self.phase in ('injecting', 'running')

    def _invoke(self, k, a):
        if k == 'chromatogram.read':
            return self._chromatogram(a.get('max_points', 2000))
        if k == 'ledger.read':
            return self._ledger()
        if k == 'template.read':
            return self._template(a['substance_id'])
        if k == 'peaks.quantify':
            return self._quantify(a)
        if k == 'power.set':
            for m in MODULES:
                self.power[m] = a['enabled']
            if not a['enabled']:
                self._power_lost('control')
            return {'enabled': a['enabled']}
        if k == 'power.toggle':
            self.power[a['module']] = not self.power[a['module']]
            if not self.power[a['module']]:
                self._power_lost(a['module'])
            return {'module': a['module'], 'enabled': self.power[a['module']]}
        if k == 'supply.configure':
            if self.pump_enabled:
                raise ActionRejected('PRECONDITION_FAILED')
            if a['connected'] and not (SUPPLY_MIN_PA <= a['supply_pa'] <= SUPPLY_MAX_PA):
                raise ActionRejected('INVALID_ARGUMENT')
            self.supply = dict(connected=a['connected'], supply_pa=a['supply_pa'] if a['connected'] else 0.0, cylinder_co2_kg=a['cylinder_co2_kg'])
            self.co2_initial_kg = a['cylinder_co2_kg'] + self.vented_co2_kg
            if self.ambient_k != a['ambient_k'] and not any(self.power.values()):
                self.chiller_k = self.bpr_k = self.column_k = a['ambient_k']
            self.ambient_k = a['ambient_k']
            return {'configured': True}
        if k == 'solvent.configure':
            if self.pump_enabled:
                raise ActionRejected('PRECONDITION_FAILED')
            old = (self.bottles[a['channel']] or {}).get('volume_m3', 0.0)
            self.modifier_loaded_m3 += a['volume_m3'] - old
            self.bottles[a['channel']] = dict(modifier=a['modifier'], volume_m3=a['volume_m3'])
            return {'configured': True}
        if k == 'column.install':
            if self.pump_enabled or self._busy() or self.mech['mct_door'] < 0.5:
                raise ActionRejected('PRECONDITION_FAILED')
            self.columns[a['position']] = a['column']
            return dict(a)
        if k == 'sample.register_vial':
            if self.mech[f"drawer_{a['drawer']}"] < 0.15 or (a['drawer'], a['position']) in self.vials:
                raise ActionRejected('PRECONDITION_FAILED')
            comp = a['composition_mol_m3']
            if not comp or any(s not in templates() for s in comp):
                raise ActionRejected('INVALID_ARGUMENT')
            self.vials[(a['drawer'], a['position'])] = dict(volume_m3=a['volume_m3'], composition=dict(comp))
            self.vial_loaded_m3 += a['volume_m3']
            self._species_loaded += a['volume_m3'] * sum(comp.values())
            return {'registered': True}
        if k == 'sample.remove_vial':
            key = (a['drawer'], a['position'])
            if key not in self.vials or self.mech[f"drawer_{a['drawer']}"] < 0.15:
                raise ActionRejected('PRECONDITION_FAILED')
            v = self.vials.pop(key)
            self.vial_returned_m3 += v['volume_m3']
            self._species_returned += v['volume_m3'] * sum(v['composition'].values())
            return {'returned_m3': v['volume_m3']}
        if k == 'method.configure':
            if self._busy():
                raise ActionRejected('BUSY')
            prog = a['program']
            if prog[0]['time_s'] != 0.0 or any(prog[i + 1]['time_s'] <= prog[i]['time_s'] for i in range(len(prog) - 1)):
                raise ActionRejected('INVALID_ARGUMENT')
            if a['column_temperature_k'] < max(COL_T_ABS_MIN, self.ambient_k - 10.0):
                raise ActionRejected('INVALID_ARGUMENT')
            n = a['injection_volume_m3'] / INJ_STEP
            if abs(n - round(n)) > 1e-6:
                raise ActionRejected('INVALID_ARGUMENT')
            if self.pump_enabled:                       # the running pump cannot be switched to an empty bottle or an empty column position
                bottle = self.bottles[a['modifier_channel']]
                if not bottle or bottle['volume_m3'] <= 0 or self.columns[a['column_position']] == 'none':
                    raise ActionRejected('PRECONDITION_FAILED')
            self.method = copy.deepcopy(a)
            return {'configured': True, 'delay_volume_m3': delay_volume_m3(a['bpr_pa'])}
        if k == 'pump.set':
            if a['enabled']:
                if not (self._all_powered() and self._co2_ready() and self.method and self._column()):
                    raise ActionRejected('PRECONDITION_FAILED')
                bottle = self.bottles[self.method['modifier_channel']]
                if not bottle or bottle['volume_m3'] <= 0:
                    raise ActionRejected('PRECONDITION_FAILED')
                self.fault = ''
            elif self._busy():
                self._finish('cancelled')
            self.pump_enabled = a['enabled']
            return {'enabled': a['enabled']}
        if k.endswith('.set') and k[:-4] in MECH:
            name = k[:-4]
            arg = 'angle_rad' if MECH[name][1] == 'rad' else 'position_m'
            opening = a[arg] > 1e-6
            if opening and self._busy():                 # doors / drawers / oven door stay closed while injecting or acquiring
                raise ActionRejected('BUSY')
            self.mech[name] = a[arg]
            return dict(a)
        if k == 'run.start':
            return self._start(a)
        if k == 'run.abort':
            if self._busy():
                self._finish('cancelled')
            return {'phase': self.phase}
        raise ActionRejected('UNKNOWN_CAPABILITY')

    def _power_lost(self, module):
        """A module lost power: any run in progress is cancelled (every module takes part in a run); the pump target is cleared only when
        the pump itself or the control module (CO2 conditioning, BPR) is off - the other modules are independent of the pump."""
        if self._busy():
            self._finish('cancelled')
        if module in ('pump', 'control'):
            self.pump_enabled = False

    def _start(self, a):
        if self._busy():
            raise ActionRejected('BUSY')
        key = (a['drawer'], a['position'])
        if not self._ready() or self.mech['sampler_door'] > 1e-6 or self.mech[f"drawer_{a['drawer']}"] > 1e-6:
            raise ActionRejected('PRECONDITION_FAILED')
        vial = self.vials.get(key)
        m = self.method
        if vial is None or vial['volume_m3'] < m['injection_volume_m3'] + VIAL_DEAD_VOLUME_M3:
            raise ActionRejected('PRECONDITION_FAILED')
        bottle = self.bottles[m['modifier_channel']]
        if not bottle or self._column() is None:
            raise ActionRejected('PRECONDITION_FAILED')
        need = sum(m['flow_m3_s'] * float(program_value(self._program(), t)) * 1.0 for t in np.arange(0, m['run_time_s'] + INJECT_CYCLE_S, 1.0))
        if bottle['volume_m3'] < need:
            raise ActionRejected('PRECONDITION_FAILED')
        self.run_count += 1
        self.operation_id = f'{self.instance_id}:run:{self.run_count}'
        self.run = dict(run_id=self.operation_id, vial=key, elapsed=0.0, inject_left=INJECT_CYCLE_S, t=[], y=[], peaks=[], warnings=[],
                        wavelength=m['wavelength_m'], next_sample=0.0, rng=np.random.default_rng(
                            int(hashlib.sha256(self.operation_id.encode()).hexdigest()[:12], 16)), drift0=0.0)
        self.phase = 'injecting'
        return {'phase': self.phase, 'operation_id': self.operation_id}

    def _inject(self):
        """End of the injection cycle: draw the volume, create the bands from the measured templates and the current conditions."""
        m, run = self.method, self.run
        vial = self.vials[run['vial']]
        vol = m['injection_volume_m3']
        vial['volume_m3'] -= vol
        self.injected_m3 += vol
        self.waste_m3 += vol                                   # the sample solvent leaves with the mobile phase
        col = self._column()
        modifier = self.bottles[m['modifier_channel']]['modifier']
        t0 = void_time_s(col['length_m'], col['inner_diameter_m'], m['flow_m3_s'])
        delay = delay_volume_m3(m['bpr_pa']) / m['flow_m3_s']
        rho = co2_density(self.column_k, m['bpr_pa'] + 101325.0)
        extra = -DENSITY_COEFF * (rho / _rho_ref() - 1.0)
        n_plates = col['length_m'] / (PLATE_HEIGHT_DP * col['particle_m'])
        extra_var = ((FLOW_CELL_VOLUME_M3 / m['flow_m3_s']) ** 2 + (vol / m['flow_m3_s']) ** 2) / 12.0   # flow cell + injection plug (s^2)
        sids, a_vals, mols = [], [], []
        for sid, c in vial['composition'].items():
            moles = c * vol
            self.injected_mol += moles
            entry = templates()[sid]['retention'][col['id']][modifier]
            if entry['status'] != 'eluted' or 'model_fit' not in entry:
                self.bands.append(dict(sid=sid, mol=moles, t_r=None, sigma=None, eps=0.0, eluted=False, done=True, run=run['run_id'],
                                       evidence='not_eluted_in_source_data' if entry['status'] != 'eluted' else 'outside_reference_program'))
                continue
            sids.append(sid)
            a_vals.append(entry['model_fit']['log10_k_at_phi_1'])
            mols.append(moles)
        if sids:
            tr, ke, _ = migrate(np.array(a_vals), LOGLOG_SLOPE_M, self._program(), t0, delay, m['run_time_s'] + 600.0, dt_s=0.05, extra_ln_k=extra)
            for sid, t_r, k_e, moles in zip(sids, tr, ke, mols):
                eps, ev = epsilon(sid, m['wavelength_m'])
                if np.isnan(t_r):
                    self.bands.append(dict(sid=sid, mol=moles, t_r=None, sigma=None, eps=eps, eluted=False, done=True, run=run['run_id'],
                                           evidence='retained_beyond_run'))
                    continue
                sigma = math.sqrt((t0 * (1.0 + float(k_e))) ** 2 / n_plates + extra_var)
                # flow_s: flow-weighted time this band has travelled since injection (it keeps migrating whenever the pump delivers, also after
                # its run ended -> late eluters reach the waste and appear as carry-over peaks in a later run)
                self.bands.append(dict(sid=sid, mol=moles, t_r=float(t_r), sigma=sigma, eps=eps, wl=m['wavelength_m'], eluted=False, done=False,
                                       flow_s=0.0, flow_ref=m['flow_m3_s'], evidence=ev, run=run['run_id']))
        run['t0'] = t0
        run['delay_s'] = delay
        run['flow'] = m['flow_m3_s']

    def _signal(self, t):
        """Absorbance at run time t (inside the current step): every migrating band, including carry-over from earlier runs."""
        run = self.run
        total = 0.0
        lag = run['elapsed'] - t                                    # sample time lies `lag` seconds before the end of this step
        for b in self.bands:
            if b['t_r'] is None or b['done']:
                continue
            fs = b['flow_s'] - lag * (self.flow / b['flow_ref'])
            dz = (fs - b['t_r']) / b['sigma']
            if abs(dz) < 8:
                eps = b['eps'] if b['wl'] == run['wavelength'] else epsilon(b['sid'], run['wavelength'])[0]
                c = b['mol'] / (run['flow'] * b['sigma'] * math.sqrt(2 * math.pi)) * math.exp(-0.5 * dz * dz)
                total += eps * FLOW_CELL_PATH_M * c
        noise = float(run['rng'].normal(0.0, DAD_NOISE_AU / 2.5))
        return total + noise + DAD_DRIFT_AU_S * t

    def _finish(self, phase):
        run = self.run
        self.phase = phase
        if run is not None:
            thr = 10 * DAD_NOISE_AU
            run['peaks'] = detect_peaks(run['t'], run['y'], thr) if phase == 'succeeded' else []
        self.absorbance = 0.0

    def _chromatogram(self, max_points):
        run = self.run
        if run is None:
            return dict(run_id='', phase=self.phase, elapsed_s=0.0, time_s=[], absorbance_au=[], peaks=[], complete=False, wavelength_m=None,
                        template_source=LIBRARY_REL, evidence='no run', warnings=[])
        n = len(run['t'])
        step = max(1, math.ceil(n / max_points))
        return dict(run_id=run['run_id'], phase=self.phase, elapsed_s=run['elapsed'], time_s=list(run['t'][::step]), absorbance_au=list(run['y'][::step]),
                    peaks=copy.deepcopy(run['peaks']), complete=self.phase == 'succeeded', wavelength_m=run['wavelength'],
                    template_source=f'{LIBRARY_REL}#sha256={library_sha256()[:16]}',
                    evidence='retention: measured CC BY 4.0 SFC data (Waters UPC2) transferred with a derived migration model; '
                             'absorbance: NIST UV curves where available, else assumed; peak shape: assumed plate height',
                    warnings=list(run['warnings']) + ['retention transferred from another SFC system; dwell volume and column lot effects are modelled, not measured',
                                                      'peak responses use NIST UV curves where the template library holds one, otherwise an assumed molar '
                                                      'absorptivity; per-peak response evidence is evaluator-only',
                                                      'quantitation requires the caller\'s own external calibration'])

    def _template(self, sid):
        rec = templates().get(sid)
        if rec is None:
            raise ActionRejected('UNKNOWN_REFERENCE')
        ref = {c: {m: v['t_r_first_s'] for m, v in mods.items()} for c, mods in rec['retention'].items()}
        return dict(substance_id=sid, name=rec['name'], cas=rec['cas'], molecular_weight_g_mol=rec['molecular_weight_g_mol'], reference_retention_s=ref,
                    uv_curve=rec['uv_vis'] is not None, template_source=f'{LIBRARY_REL}#{sid}', evidence=rec['evidence'],
                    licence=rec['source']['licence'])

    def _quantify(self, a):
        x = np.array([p['concentration_mol_m3'] for p in a['calibration']], float)
        y = np.array([p['area_au_s'] for p in a['calibration']], float)
        if np.ptp(x) <= 0:
            raise ActionRejected('INVALID_CALIBRATION')
        slope, intercept = np.polyfit(x, y, 1)
        ss = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - float(np.sum((y - (slope * x + intercept)) ** 2)) / ss if ss > 0 else 0.0
        if slope <= 0:
            raise ActionRejected('INVALID_CALIBRATION')
        lo, hi = sorted(a['retention_window_s'])
        peaks = [p for p in (self.run['peaks'] if self.run and self.phase == 'succeeded' else []) if lo <= p['retention_time_s'] <= hi]
        if not peaks:
            return dict(slope_au_s_m3_mol=float(slope), intercept_au_s=float(intercept), r_squared=max(0.0, min(1.0, r2)), peak_area_au_s=None,
                        concentration_mol_m3=None, quality='no_peak_in_window_or_no_completed_run')
        p = max(peaks, key=lambda q: q['area_au_s'])
        conc = (p['area_au_s'] - intercept) / slope
        quality = 'ok' if x.min() <= conc <= x.max() and r2 >= 0.99 else 'extrapolated_or_poor_fit'
        return dict(slope_au_s_m3_mol=float(slope), intercept_au_s=float(intercept), r_squared=max(0.0, min(1.0, r2)), peak_area_au_s=p['area_au_s'],
                    concentration_mol_m3=float(conc), quality=quality)

    # ---- clock ------------------------------------------------------------------------------------------------
    def step(self, dt_s):
        if isinstance(dt_s, bool) or not isinstance(dt_s, (int, float)) or not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError('dt_s must be finite and positive')
        left = float(dt_s)
        while left > 1e-12:
            h = min(left, 0.1 if self._busy() else 1.0)
            self._advance(h)
            left -= h
        self.revision += 1
        return self.read('evaluator')

    def _advance(self, h):
        amb = self.ambient_k
        m = self.method
        # control module: chiller + booster
        target = CHILLER_TARGET_K if (self.power['control'] and self.supply['connected']) else amb
        self.chiller_k = target + (self.chiller_k - target) * math.exp(-h / CHILLER_TAU_S)
        # BPR heater and column oven
        bt = m['bpr_temperature_k'] if (m and self.power['control']) else amb
        self.bpr_k = bt + (self.bpr_k - bt) * math.exp(-h / BPR_HEAT_TAU_S)
        ct = m['column_temperature_k'] if (m and self.power['mct']) else amb
        if self.mech['mct_door'] > 0.05 and self.power['mct']:
            ct = amb + 0.5 * (ct - amb)                          # door open: oven cannot hold its set point (assumption)
        tau = COL_TAU_HEAT_S if ct > self.column_k else COL_TAU_COOL_S
        self.column_k = ct + (self.column_k - ct) * math.exp(-h / tau)
        # pump, pressures
        running = self.pump_enabled and m is not None and self._all_powered()
        if running and not self._co2_ready():
            self.fault = 'CO2_SUPPLY'
            self.pump_enabled = running = False
            if self._busy():
                self._finish('failed')
        col = self._column()
        f_target = m['flow_m3_s'] if running and col else 0.0
        self.flow = f_target + (self.flow - f_target) * math.exp(-h / FLOW_TAU_S)
        phi = self._phi_now()
        if running and col:
            bpr_target = m['bpr_pa']
            drop = column_pressure_drop(self.flow, phi, col) + EXTRA_COLUMN_R * (mixture_viscosity(phi) / 1e-4) * self.flow
            self.bpr_pa = bpr_target + (self.bpr_pa - bpr_target) * math.exp(-h / PRESSURE_TAU_S)
            p_target = self.bpr_pa + drop
            self.pump_pa = p_target + (self.pump_pa - p_target) * math.exp(-h / PRESSURE_TAU_S)
            self.booster_pa = max(0.0, 0.95 * self.pump_pa)
            if self.pump_pa > PUMP_MAX_PA:
                self.fault = 'OVERPRESSURE'
                self.pump_enabled = False
                if self._busy():
                    self._finish('failed')
            # consumption ledgers
            vol = self.flow * h
            bottle = self.bottles[m['modifier_channel']] or {'volume_m3': 0.0}
            mod = min(bottle['volume_m3'], vol * phi)
            bottle['volume_m3'] -= mod
            self.modifier_drawn_m3 += mod
            self.waste_m3 += mod
            co2 = min(self.supply['cylinder_co2_kg'], vol * (1 - phi) * CO2_PUMP_DENSITY_KG_M3)
            self.supply['cylinder_co2_kg'] -= co2
            self.vented_co2_kg += co2
            for b in self.bands:                                    # bands migrate whenever the pump delivers
                if b['t_r'] is None or b['done']:
                    continue
                b['flow_s'] += h * self.flow / b['flow_ref']
                if not b['eluted'] and b['flow_s'] >= b['t_r'] + 4 * b['sigma']:
                    b['eluted'] = True
                    self.eluted_mol += b['mol']
                if b['flow_s'] >= b['t_r'] + 8 * b['sigma']:
                    b['done'] = True
            if bottle['volume_m3'] <= 0.0 and (phi > 0 or self.bottles[m['modifier_channel']] is None):
                self.fault = 'MODIFIER_EMPTY'
                self.pump_enabled = False
                if self._busy():
                    self._finish('failed')
        else:
            for attr in ('bpr_pa', 'pump_pa', 'booster_pa'):
                setattr(self, attr, getattr(self, attr) * math.exp(-h / 30.0))
        # run state machine
        if self.phase == 'injecting':
            self.run['inject_left'] -= h
            if self.run['inject_left'] <= 1e-9:
                self._inject()
                self.phase = 'running'
        elif self.phase == 'running':
            run = self.run
            run['elapsed'] += h
            period = 1.0 / m['data_rate_hz']
            while run['next_sample'] <= run['elapsed'] + 1e-9 and run['next_sample'] <= m['run_time_s'] + 1e-9:
                y = self._signal(run['next_sample'])
                run['t'].append(round(run['next_sample'], 6))
                run['y'].append(y)
                self.absorbance = y
                run['next_sample'] += period
            if run['elapsed'] >= m['run_time_s'] - 1e-9:
                self._finish('succeeded')
        self.time_s += h


class Agilent1260infinitySession:
    """Principal-bound protocol session: describe / read / invoke / write / poll / cancel with visibility filtering."""

    def __init__(self, host, principal_id):
        self._host = host
        self._principal = principal_id

    @property
    def _role(self):
        return self._host._principals[self._principal]

    def _target(self, instance_id):
        if instance_id != self._host.instance_id:
            raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:
            self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID:
            raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_agilent_1260infinity_contract_metadata()
        agent = self._role == 'agent'
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0',
                    capabilities={k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or not agent},
                    state_slots={k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or not agent})

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        vals = h.read(self._role)
        ids = list(vals) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded', instance_id=instance_id, state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(vals[k]), unit=SLOTS[k][1], quality='estimated' if SLOTS[k][2] == 'derived' else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        try:
            return self._invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None, status='rejected',
                        state_revision=self._host.revision, clock={'domain': 'simulation', 'time_s': self._host.time_s}, result={}, observations={},
                        error=dict(code=str(exc), message=str(exc), retryable=False, side_effects='none'))

    def _invoke(self, *, instance_id, capability_id, arguments, request_id):
        self._target(instance_id)
        h = self._host
        if not isinstance(capability_id, str) or capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role == 'agent' and CAPS[capability_id][5] != 'agent':
            raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id, str) or not request_id or not isinstance(arguments, dict):
            raise ActionRejected('INVALID_ARGUMENT')
        key = (self._principal, instance_id, request_id)
        try:
            digest = json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            raise ActionRejected('INVALID_ARGUMENT')
        if key in h._requests:
            old, result = h._requests[key]
            if old != digest:
                raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        result = h.invoke(capability_id, **arguments)
        op = result.get('operation_id') if capability_id == 'run.start' else None
        if op:
            h._owners[op] = self._principal
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=op,
                        status='accepted' if op else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                        result=result, observations={}, error=None)
        if CAPS[capability_id][4] not in ('query', 'analysis'):   # pure queries are recomputed on retry (no unbounded cache of large arrays)
            h._requests[key] = (digest, copy.deepcopy(response))
        return response

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:
            raise ActionRejected('ACCESS_DENIED')
        cap, arg = SLOTS[state_id][5]
        return self.invoke(instance_id=instance_id, capability_id=cap, arguments={arg: value}, request_id=request_id)

    def poll(self, operation_id):
        h = self._host
        if operation_id != h.operation_id or h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id, status=h.phase, result=h._chromatogram(2000))

    def cancel(self, operation_id, request_id):
        self.poll(operation_id)
        response = self.invoke(instance_id=self._host.instance_id, capability_id='run.abort', arguments={}, request_id=request_id)
        return dict(operation_id=operation_id, status=self._host.phase, result=response['result'])


# ------------------------------------------------------------------------------------------------------ contract metadata
PRECONDITIONS = {
    'pump.set': ('开泵：五模块通电、CO2就绪（供气40–70 bar且冷却器≤9 °C）、已配置方法、所选柱位已装柱、所用改性剂瓶有液',
                 ['power.enabled', 'co2.ready', 'method.configured', 'column.installed', 'solvent.b1'], 'all_powered and co2_ready and method and column and bottle>0'),
    'run.start': ('系统就绪（流量≥98%目标、背压±5 bar、柱温±1 K）、进样器门与该抽屉关闭、瓶内余量≥进样体积+10 µL、改性剂足够本次运行',
                  ['system.ready', 'sampler_door.angle_rad', 'drawer_1.position_m', 'run.phase'], 'ready and door==0 and drawer==0 and vial>=inj+dead and phase not busy'),
    'method.configure': ('运行中锁定方法；梯度表时间从0递增；柱温≥环境−10 K且≥4 °C；进样体积为0.1 µL整数倍；泵运行时不得切换到无液改性剂瓶或空柱位',
                         ['run.phase', 'pump.enabled', 'solvent.b1', 'column.installed'], 'phase not busy and (not pump or (bottle>0 and column installed))'),
    'column.install': ('柱温箱门打开（≥0.5 rad）、泵停且不在运行', ['mct_door.angle_rad', 'pump.enabled', 'run.phase'], 'mct_door>=0.5 and not pump and not busy'),
    'sample.register_vial': ('目标抽屉拉出≥0.15 m、瓶位空、物质在模板库', ['drawer_1.position_m', 'sample.vial_count'], 'drawer>=0.15 and free and sid in library'),
    'sample.remove_vial': ('目标抽屉拉出≥0.15 m且瓶位有瓶', ['drawer_1.position_m'], 'drawer>=0.15 and occupied'),
    'supply.configure': ('泵停时才能更换气瓶；连接时供气40–70 bar', ['pump.enabled'], 'not pump and 40e5<=supply<=70e5'),
    'solvent.configure': ('泵停时才能换瓶', ['pump.enabled'], 'not pump'),
    'sampler_door.set': ('进样/采集中不得打开进样器门', ['run.phase'], 'opening -> phase not busy'),
    'mct_door.set': ('进样/采集中不得打开柱温箱门（保留在进样时按当时条件计算）', ['run.phase'], 'opening -> phase not busy'),
}
for _i in range(1, 5):
    PRECONDITIONS[f'drawer_{_i}.set'] = ('进样/采集中不得拉出抽屉', ['run.phase'], 'opening -> phase not busy')


def export_agilent_1260infinity_contract_metadata():
    caps, slots = {}, {}
    for key, (schema, unit, origin, vis, comps, write, meaning) in SLOTS.items():
        slots[key] = dict(component_ids=comps, value_schema=copy.deepcopy(schema), unit=unit, reference_conditions=REFERENCE_ZH.get(key),
                          meaning_zh=meaning or None, origin=origin, visibility=vis, access='read_write' if write else 'read_only',
                          read_binding=dict(backend='host', artifact_id='runtime', accessor=f'Agilent1260infinitySession.read(state_ids=[{key!r}])'),
                          write_via=dict(capability_id=write[0], argument=write[1], fixed_arguments={}) if write else None,
                          sampling=dict(mode='instant', time_domain='simulation', window_description=None),
                          source_ids=['manufacturer_specs', 'simulation_assumption'])
    for key, (inp, out, comps, meaning, kind, vis, example, effects) in CAPS.items():
        unsupported = key in UNSUPPORTED
        is_async = key == 'run.start'
        pre = []
        if key in PRECONDITIONS:
            d, states, check = PRECONDITIONS[key]
            pre = [dict(description_zh=d, state_ids=states, enforced_by='runtime', check=check)]
        caps[key] = dict(
            function_zh=meaning, kind=kind, visibility=vis, component_ids=comps, availability='unsupported' if unsupported else 'implemented',
            input_schema=copy.deepcopy(inp), output_schema=copy.deepcopy(out),
            implementation=dict(process='absent' if unsupported else 'implemented', scene='absent', robot='absent', hardware='absent',
                                host='absent' if unsupported else 'implemented'),
            binding=None if unsupported else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.agilent_1260infinity.Agilent1260infinitySession.invoke',
                                                  invocation=f'session.invoke(instance_id="sfc_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
            preconditions=pre, state_effects=[dict(state_id=s, timing='on_completion' if is_async else 'immediate', description_zh=meaning) for s in effects],
            observes=[], model_effects=dict(mode='none', description_zh='过程层能力；几何联动由宿主另行绑定并在Matterix场景中验证。', binding_ids=[]),
            execution=dict(mode='asynchronous' if is_async else 'synchronous', time_domain='simulation',
                           completion_criterion='run.phase=succeeded（运行时间结束）；取消/失败保留已采集数据与物料账本' if is_async else
                           ('返回只读结果，不推进时钟' if kind in ('query', 'analysis') else '设定写入；物理量只随step演化'),
                           completion_state_ids=['run.phase'] if is_async else list(effects), timeout_s=None,
                           cancellation='supported' if is_async else 'unsupported', retry='same_request_id',
                           resources=['target_instance', 'principal_request_cache'],
                           partial_failure_zh='拒绝时状态不变；取消/断电/故障不回滚已注入样品、已消耗改性剂与CO2。请求去重只在当前进程内存有效。'),
            errors=[dict(code=e, meaning_zh=e, side_effects='none') for e in
                    ['UNKNOWN_INSTANCE', 'UNKNOWN_CAPABILITY', 'ACCESS_DENIED', 'INVALID_ARGUMENT', 'PRECONDITION_FAILED', 'BUSY', 'REQUEST_CONFLICT',
                     'UNKNOWN_REFERENCE', 'INVALID_CALIBRATION', 'UNSUPPORTED']],
            example_arguments=copy.deepcopy(example), verification=[],
            limitations=['仿真过程模型，无真实硬件连接；进程内会话过滤不能替代文件与进程级部署隔离。'] if not unsupported else ['未实现；调用返回UNSUPPORTED。'],
            source_ids=['manufacturer_specs', 'sfc_retention_data', 'simulation_assumption'])
    p = lambda v, u, z, ev='assumption', src=('simulation_assumption',): dict(value=v, unit=u, meaning_zh=z, evidence=ev, source_ids=list(src), uncertainty=None)  # noqa: E731
    mechanisms = {
        'retention': dict(
            component_ids=['column_1', 'column_2', 'column_3', 'column_4', 'column_valve', 'mct_housing'],
            state_ids=['column.temperature_k', 'mobile_phase.co2_density_kg_m3', 'run.phase', 'truth.column_mol', 'truth.eluted_mol'],
            meaning_zh='梯度SFC谱带迁移：模板库实测保留时间反解的log-log保留截距，按配置梯度、流量、延迟体积、柱温与背压（CO2密度）积分出峰时间',
            implementation='lumped',
            equations=['log10 k = a - m*log10(max(phi, phi_min)) - c_rho*(rho/rho_ref - 1)/ln(10)',
                       'dz/dt = 1/(t0*(1+k(phi(t - t_D - z*t0))))，z=1时出峰',
                       't0 = eps_T*pi*r^2*L/F；t_D = V_delay(BPR)/F，V_delay 600–900 µL',
                       'sigma_t^2 = (t0*(1+k_e))^2/N + ((V_cell/F)^2 + (V_inj/F)^2)/12，N = L/(h*dp)'],
            parameters={'loglog_slope_m': p(LOGLOG_SLOPE_M, '1', 'log k–log φ斜率，所有物质同一值'),
                        'total_porosity': p(TOTAL_POROSITY, '1', '柱总孔隙率（死时间）'),
                        'density_coefficient': p(DENSITY_COEFF, '1', 'ln k对CO2相对密度的系数'),
                        'reference_dwell_s': p(REF_DWELL_S, 's', '源数据UPC2系统梯度延迟（未公开，假设）'),
                        'reduced_plate_height': p(PLATE_HEIGHT_DP, '1', '折合塔板高度h'),
                        'phi_min': p(PHI_MIN, '1', '保留律中改性剂分数下限'),
                        'delay_volume_range': p([DELAY_MIN_M3, DELAY_MAX_M3], 'm^3', 'G4782A延迟体积600–900 µL（随背压，线性映射为假设）', 'documented', ['manufacturer_specs']),
                        'reference_method_flow': p(REF_FLOW_M3_S, 'm^3/s', '源数据方法流量1.5 mL/min', 'documented', ['sfc_retention_data'])},
            source_ids=['sfc_retention_data', 'manufacturer_specs', 'simulation_assumption'],
            limitations=['m、ε_T、密度系数与UPC2延迟为假设；参考方法下保留时间复现实测（残差<0.5 s），其它方法为外推。',
                         '保留时间与峰宽在进样时按当时流量/梯度/柱温/背压一次算定；运行中条件变化不改变已进样谱带（运行中门与方法均锁定）。'
                         '运行结束后泵继续输液时谱带继续迁移（按流量折算），晚出峰进入废液，或在下一次运行中作为残留峰出现。',
                         '源数据中未出峰/未检出、或晚于文献运行终点（2条，未拟合）的组合，按“留在柱上”处理，不代表真实色谱行为已知。',
                         '未建模柱老化（库中有12个月漂移数据但未驱动运行层）、对映体选择性、过载与峰拖尾。']),
        'hydraulics': dict(
            component_ids=['pump_head_a', 'pump_head_b', 'booster_unit', 'chiller_unit', 'bpr_unit', 'column_1'],
            state_ids=['pump.pressure_pa', 'bpr.pressure_pa', 'pump.flow_m3_s', 'chiller.temperature_k', 'booster.pressure_pa'],
            meaning_zh='泵压=背压+Kozeny-Carman柱压降+柱外压降；600 bar跳泵；CO2经冷却再液化与增压后供泵A，改性剂由泵B高压混合',
            implementation='lumped',
            equations=['dP_col = 180*eta_mix*u_s*L*(1-e)^2/(e^3*dp^2)', 'eta_mix = exp(phi*ln eta_MeOH + (1-phi)*ln eta_CO2)',
                       'P_pump -> P_BPR + dP_col + R_extra*(eta/1e-4)*F（一阶，tau=3 s）', 'P_pump > 600 bar -> OVERPRESSURE 停泵'],
            parameters={'pump_max_pa': p(PUMP_MAX_PA, 'Pa', '系统/泵最高压力600 bar', 'documented', ['manufacturer_specs']),
                        'bpr_range_pa': p([BPR_MIN_PA, BPR_MAX_PA], 'Pa', 'BPR可设100–400 bar', 'documented', ['manufacturer_specs']),
                        'supply_range_pa': p([SUPPLY_MIN_PA, SUPPLY_MAX_PA], 'Pa', 'CO2供气40–70 bar', 'documented', ['manufacturer_specs']),
                        'eta_co2_pa_s': p(ETA_CO2_PA_S, 'Pa*s', 'CO2黏度（工程值）'), 'eta_methanol_pa_s': p(ETA_MEOH_PA_S, 'Pa*s', '甲醇25 °C黏度（常用手册值，本资产未列来源，按假设登记）'),
                        'bed_porosity': p(BED_POROSITY, '1', '床层孔隙率'), 'extra_column_resistance': p(EXTRA_COLUMN_R, 'Pa*s/m^3', '柱外阻力（每1e-4 Pa s）'),
                        'pressure_tau_s': p(PRESSURE_TAU_S, 's', '压力一阶时间常数'), 'chiller_target_k': p(CHILLER_TARGET_K, 'K', '冷却器目标（厂家范围−20…9 °C内取5 °C）')},
            source_ids=['manufacturer_specs', 'simulation_assumption'],
            limitations=['流量按泵头条件体积计，未做压缩性补偿；改性剂对CO2密度的影响忽略。']),
        'thermal': dict(
            component_ids=['mct_housing', 'mct_door', 'bpr_unit', 'chiller_unit'], state_ids=['column.temperature_k', 'bpr.temperature_k', 'chiller.temperature_k'],
            meaning_zh='柱温箱一阶升降温，时间常数由厂家速率换算；门打开时只能维持目标与环境差的一半（假设）', implementation='lumped',
            equations=['T -> T_target，升温tau=300 s/ln(30)，降温tau=600 s/ln(40)'],
            parameters={'tau_heat_s': p(COL_TAU_HEAT_S, 's', '由厂家“环境→40 °C约5 min”换算（一阶到0.5 K以内）', 'documented', ['manufacturer_specs']),
                        'tau_cool_s': p(COL_TAU_COOL_S, 's', '由厂家“40→20 °C约10 min”换算（一阶到0.5 K以内）', 'documented', ['manufacturer_specs'])},
            source_ids=['manufacturer_specs'], limitations=['单节点温区；两个独立温区按同一温度处理。']),
        'detection': dict(
            component_ids=['flow_cell', 'dad_housing'], state_ids=['detector.absorbance_au', 'detector.wavelength_m'],
            meaning_zh='DAD吸光度=ε(λ)·10 mm·c(t)，高斯谱带；噪声与漂移取厂家规格', implementation='lumped',
            equations=['c(t) = n/(F*sigma*sqrt(2pi))*exp(-(t-tR)^2/(2 sigma^2))', 'A = eps*l*c + N(0, 0.7e-5/2.5) + drift*t'],
            parameters={'flow_cell_path_m': p(FLOW_CELL_PATH_M, 'm', 'SFC流通池光程10 mm', 'documented', ['manufacturer_specs']),
                        'noise_au': p(DAD_NOISE_AU, 'AU', '噪声<±0.7e-5 AU', 'documented', ['manufacturer_specs']),
                        'assumed_epsilon_m2_mol': p(ASSUMED_EPSILON_M2_MOL, 'm^2/mol', '无NIST紫外曲线时的假设摩尔吸光系数')},
            source_ids=['manufacturer_specs', 'nist_uv', 'simulation_assumption'],
            limitations=['多数物质无NIST紫外曲线，响应为假设（结果warnings中标出）；无检测器饱和与带宽效应。']),
    }
    if _ann is not None:
        _ann.annotate_export('agilent_1260infinity', caps, slots, mechanisms, inputs={'method.configure': (METHOD, METHOD_EXAMPLE), 'supply.configure': (SUPPLY, SUPPLY_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms=mechanisms)
