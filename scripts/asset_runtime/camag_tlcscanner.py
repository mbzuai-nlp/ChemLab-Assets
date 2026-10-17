"""CHEM-092 CAMAG TLC Scanner 4 (scanning densitometer) runtime: measured Rf templates + absorption templates -> densitograms, Rf, in-situ
spectra, spectral matching and calibration.

Process model (SI; the host clock `step(dt_s)` is the only thing that advances physics):

* Mechanics: sliding door (operator motion at an assumed 0.25 m/s), X/Y stage driven by step motors (positioning 0.15 m/s, scanning <= 0.1 m/s,
  documented), load position 185 mm in front of home (geometry), interlocks: the stage leaves the compartment only with the door fully open, the
  door closes only with the stage inside, scans run only with the door closed.
* Optics: deuterium (190-450 nm), halogen-tungsten (350-900 nm) and 366 nm LED sources (documented ranges); relative throughput curves, warm-up
  time and noise are host-given assumptions (plant.configure); monochromator bandwidth 5 / 20 nm (documented) as a Gaussian slit function;
  below 200 nm without nitrogen flushing the throughput is cut (assumption).
* Plate: the trusted host registers a developed plate (mobile phase volume fractions, tracks, substances and amounts).  Spot centres come from
  the MEASURED Rf of each compound in that mobile phase (assets/reactions/camag_tlcscanner_tlc_library, Xu et al. 2022, Apache-2.0); between
  two measured ratios of the same binary family the Rf is interpolated linearly (evidence interpolated_measured); outside the measured family /
  range the plate is rejected.  Spot shape: application band (length L, Gaussian edges) x Gaussian along Y whose variance grows with migration
  distance (assumption).
* Signal (absorbance mode): local decadic absorbance a = k_plate * sum_s eps_s(lambda) * Gamma_s (Gamma = surface amount averaged over the slit,
  eps from the template, convolved with the bandwidth), reflectance signal AU = A_sat * (1 - exp(-a / A_sat)) (Kubelka-Munk-like saturation),
  plus Gaussian noise; k_plate, A_sat and the noise level are uncalibrated host assumptions.  Fluorescence mode models ONLY the quenching of the
  F254 indicator background (no native fluorescence templates are available); analyte emission is reported as not modelled.
* Evaluation: baseline, peak finding, Rf = (y_peak - y_application) / (y_front - y_application), heights and areas; in-situ spectra; spectral
  matching against the public library curves; calibration (linear / quadratic / Michaelis-Menten) from caller-given standard amounts only.

Matterix bridge: RECIPES['instrument.camag_tlcscanner'] = dict(module='asset_runtime.camag_tlcscanner', cls='CamagTlcscannerRuntime',
instance_in_ctor=True, advance=STEP_DT)."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SLUG = 'camag_tlcscanner'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-092'
PROTOCOL_VERSION = '1.0.0'
ASSET_VERSION = '1.0.0'
TEMPLATE_REL = 'assets/reactions/camag_tlcscanner_tlc_library/templates.json'

# ---- geometry-derived constants (assets/.../geometry_manifest.json stage_geometry; engineering)
DOOR_OPEN_M = 0.248
STAGE_X_RANGE = (0.0, 0.200)
STAGE_Y_RANGE = (-0.185, 0.210)
LOAD_Y = -0.185
Y_INSIDE = -0.030               # stage fully inside the compartment (door may close) for stage.y_m >= this
BEAM_FROM_STAGE_CENTRE = 0.100  # beam position relative to the stage centre = 0.100 - q (both axes)
POSITIONING_SPEED = 0.150       # m/s (documented)
MAX_SCAN_SPEED = 0.100          # m/s (documented)
MONO_SPEED = 200e-9             # m/s monochromator positioning (2012 brochure)
SPECTRUM_SPEED = 100e-9         # m/s spectrum recording (documented)
LAMP_RANGES = {'deuterium': (190e-9, 450e-9), 'halogen_tungsten': (350e-9, 900e-9), 'led_366': (366e-9, 366e-9)}
SOLVENTS = ['hexane', 'ethyl_acetate', 'dichloromethane', 'methanol', 'diethyl_ether']
FAMILIES = {'hexane/ethyl_acetate': ('hexane', 'ethyl_acetate'), 'dichloromethane/methanol': ('dichloromethane', 'methanol'),
            'hexane/diethyl_ether': ('hexane', 'diethyl_ether')}
PHASES = ['idle', 'running', 'succeeded', 'cancelled', 'failed']
SQ2 = math.sqrt(2.0)


class ActionRejected(ValueError):
    """A precondition or argument check failed; the state is unchanged."""


# ---------------------------------------------------------------------------------------------------------------------------- schemas
BOOL, TEXT = {'type': 'boolean'}, {'type': 'string'}


def number(unit, low=None, high=None):
    s = {'type': 'number', 'x-unit': unit}
    if low is not None:
        s['minimum'] = low
    if high is not None:
        s['maximum'] = high
    return s


def integer(low, high):
    return {'type': 'integer', 'x-unit': '1', 'minimum': low, 'maximum': high}


def obj(props, optional=()):
    return dict(type='object', properties=props, required=[k for k in props if k not in optional], additionalProperties=False)


def arr(item, lo=None, hi=None):
    s = dict(type='array', items=item)
    if lo is not None:
        s['minItems'] = lo
    if hi is not None:
        s['maxItems'] = hi
    return s


def enum(*values):
    return {'type': 'string', 'enum': list(values)}


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


def validate(s, v):
    if 'anyOf' in s:
        if v is None and any(q.get('type') == 'null' for q in s['anyOf']):
            return
        return validate(next(q for q in s['anyOf'] if q.get('type') != 'null'), v)
    t = s.get('type')
    if t in ('number', 'integer'):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ActionRejected('INVALID_ARGUMENT')
        if t == 'integer' and type(v) is not int:
            raise ActionRejected('INVALID_ARGUMENT')
        if not s.get('minimum', -math.inf) <= v <= s.get('maximum', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'enum' in s and not any(abs(v - e) <= 1e-12 * max(1.0, abs(e)) for e in s['enum']):
            raise ActionRejected('INVALID_ARGUMENT')
    elif t == 'boolean':
        if type(v) is not bool:
            raise ActionRejected('INVALID_ARGUMENT')
    elif t == 'string':
        if not isinstance(v, str) or ('enum' in s and v not in s['enum']):
            raise ActionRejected('INVALID_ARGUMENT')
    elif t == 'object':
        if not isinstance(v, dict) or not set(s.get('required', [])) <= v.keys():
            raise ActionRejected('INVALID_ARGUMENT')
        for k, x in v.items():
            if k not in s['properties']:
                raise ActionRejected('INVALID_ARGUMENT')
            validate(s['properties'][k], x)
    elif t == 'array':
        if not isinstance(v, list) or not s.get('minItems', 0) <= len(v) <= s.get('maxItems', 10 ** 9):
            raise ActionRejected('INVALID_ARGUMENT')
        for x in v:
            validate(s['items'], x)
    elif t == 'null':
        if v is not None:
            raise ActionRejected('INVALID_ARGUMENT')


PLANT = obj({'lamp_warmup_s': number('s', 0.0, 7200.0), 'noise_au': number('1', 0.0, 0.05), 'plate_factor': number('1', 0.1, 50.0),
             'saturation_au': number('1', 0.05, 5.0), 'spot_sigma0_m': number('m', 2e-4, 5e-3), 'spot_broadening_m': number('m', 0.0, 1e-3),
             'band_edge_sigma_m': number('m', 5e-5, 3e-3), 'f254_background': number('1', 0.0, 10.0), 'door_speed_m_s': number('m/s', 0.01, 1.0),
             'seed': integer(0, 2147483647), 'accept_synthetic_response': BOOL, 'accept_uncalibrated_model': BOOL})
PLANT_EXAMPLE = dict(lamp_warmup_s=300.0, noise_au=2e-4, plate_factor=4.0, saturation_au=0.8, spot_sigma0_m=1.0e-3, spot_broadening_m=2.0e-5,
                     band_edge_sigma_m=5e-4, f254_background=1.0, door_speed_m_s=0.25, seed=92, accept_synthetic_response=False,
                     accept_uncalibrated_model=True)
FAST_PLANT = dict(PLANT_EXAMPLE, lamp_warmup_s=1.0, noise_au=0.0)
PHASE_FRACTIONS = obj({k: number('1', 0.0, 1.0) for k in SOLVENTS})
SPOT = obj({'substance_id': TEXT, 'amount_kg': number('kg', 1e-12, 1e-6)})
TRACK = obj({'track_x_m': number('m', 0.0, 0.2), 'band_length_m': number('m', 1e-3, 0.03), 'spots': arr(SPOT, 0, 12)})
PLATE = obj({'plate_id': TEXT, 'width_m': number('m', 0.02, 0.2), 'height_m': number('m', 0.02, 0.2), 'layer': enum('silica_gel_60', 'silica_gel_60_f254'),
             'mobile_phase': PHASE_FRACTIONS, 'application_y_m': number('m', 0.002, 0.05), 'front_y_m': number('m', 0.02, 0.2),
             'tracks': arr(TRACK, 1, 36), 'derivatized': BOOL})
PLATE_EXAMPLE = dict(plate_id='hptlc_01', width_m=0.10, height_m=0.10, layer='silica_gel_60_f254',
                     mobile_phase=dict(hexane=0.75, ethyl_acetate=0.25, dichloromethane=0.0, methanol=0.0, diethyl_ether=0.0),
                     application_y_m=0.008, front_y_m=0.070,
                     tracks=[dict(track_x_m=0.015, band_length_m=0.008, spots=[dict(substance_id='xu_tlc_038', amount_kg=2e-10),
                                                                              dict(substance_id='xu_tlc_048', amount_kg=2e-10)]),
                             dict(track_x_m=0.030, band_length_m=0.008, spots=[dict(substance_id='xu_tlc_038', amount_kg=4e-10),
                                                                              dict(substance_id='xu_tlc_048', amount_kg=4e-10)])],
                     derivatized=False)
BANDWIDTHS = [5e-9, 20e-9]
METHOD = obj({'mode': enum('absorbance', 'fluorescence'), 'wavelengths_m': arr(number('m', 190e-9, 900e-9), 1, 31),
              'bandwidth_m': dict(number('m', 5e-9, 20e-9), enum=BANDWIDTHS), 'slit_length_m': number('m', 0.2e-3, 12e-3),
              'slit_width_m': number('m', 0.1e-3, 1.2e-3), 'scan_speed_m_s': number('m/s', 1e-3, MAX_SCAN_SPEED),
              'data_step_m': number('m', 25e-6, 200e-6), 'tracks_x_m': arr(number('m', 0.0, 0.2), 1, 36), 'scan_start_y_m': number('m', 0.0, 0.2),
              'scan_end_y_m': number('m', 0.0, 0.2), 'application_y_m': number('m', 0.0, 0.2), 'front_y_m': number('m', 0.0, 0.2)})
METHOD_EXAMPLE = dict(mode='absorbance', wavelengths_m=[254e-9], bandwidth_m=20e-9, slit_length_m=6e-3, slit_width_m=0.3e-3, scan_speed_m_s=0.02,
                      data_step_m=100e-6, tracks_x_m=[0.015, 0.030], scan_start_y_m=0.004, scan_end_y_m=0.074, application_y_m=0.008, front_y_m=0.070)
PEAK = obj({'rf': number('1'), 'y_m': number('m'), 'height_au': number('1'), 'area_au_m': number('m'), 'start_y_m': number('m'), 'end_y_m': number('m')})
TRACE = obj({'track_index': integer(0, 35), 'track_x_m': number('m'), 'wavelength_m': number('m'), 'mode': TEXT,
             'signal_unit': enum('AU', 'relative_fluorescence'), 'complete': BOOL,
             'y_m': arr(number('m')), 'signal': arr(number('1')), 'peaks': arr(PEAK)})
RESULT = obj({'phase': enum(*PHASES), 'complete': BOOL, 'traces': arr(TRACE), 'warning': TEXT, 'template_source': TEXT, 'library_sha256': TEXT,
              'evidence': TEXT, 'template_coverage': enum('complete', 'partial', 'none'), 'limitations': arr(TEXT)})
MEASURE = obj({'phase': enum(*PHASES), 'operation': enum('none', 'scan', 'spectrum'), 'track_index': nullable(integer(0, 35)),
               'wavelength_m': nullable(number('m')), 'plate_x_m': number('m'), 'plate_y_m': number('m'), 'signal': nullable(number('1')),
               'completed_points': integer(0, 10 ** 7), 'elapsed_s': number('s', 0.0), 'warning': TEXT})
SPECTRUM = obj({'phase': enum(*PHASES), 'complete': BOOL, 'plate_x_m': nullable(number('m')), 'plate_y_m': nullable(number('m')),
                'wavelength_m': arr(number('m')), 'signal': arr(number('1')), 'warning': TEXT, 'template_source': TEXT, 'evidence': TEXT,
                'limitations': arr(TEXT)})
MATCH = obj({'candidates': arr(obj({'substance_id': TEXT, 'name_en': nullable(TEXT), 'correlation': number('1', -1.0, 1.0), 'overlap_m': number('m', 0.0),
                                    'uv_basis': TEXT})), 'compared': integer(0, 10000), 'evidence': TEXT})
CAL = obj({'model': enum('linear', 'quadratic', 'michaelis_menten'), 'coefficients': arr(number('1')), 'r_squared': nullable(number('1')),
           'standards': arr(obj({'track_index': integer(0, 35), 'amount_kg': number('kg'), 'area_au_m': nullable(number('m'))})),
           'unknowns': arr(obj({'track_index': integer(0, 35), 'area_au_m': nullable(number('m')), 'estimated_amount_kg': nullable(number('kg')),
                                'within_calibration_range': BOOL})), 'evidence': TEXT})
TEMPLATE_OUT = obj({'substance_id': TEXT, 'name_en': nullable(TEXT), 'cas': nullable(TEXT), 'molar_mass_kg_mol': number('kg/mol'),
                    'rf_points': arr(obj({'mobile_phase': PHASE_FRACTIONS, 'rf': number('1', 0.0, 1.0)})),
                    'uv_basis': nullable(TEXT), 'lambda_max_m': nullable(number('m')), 'epsilon_max_m2_mol': nullable(number('m^2/mol')),
                    'source': TEXT, 'evidence': TEXT, 'licence': TEXT})
LEDGER = obj({'plate_id': nullable(TEXT), 'attached': BOOL, 'location': enum('none', 'external', 'stage', 'removed'), 'mobile_phase_family': nullable(TEXT),
              'registered_kg': number('kg', 0.0), 'external_kg': number('kg', 0.0), 'on_stage_kg': number('kg', 0.0), 'removed_kg': number('kg', 0.0),
              'consumed_kg': number('kg', 0.0), 'residual_kg': number('kg'), 'plates_registered': integer(0, 10 ** 6),
              'spots': arr(obj({'track_index': integer(0, 35), 'substance_id': TEXT, 'amount_kg': number('kg'), 'amount_mol': number('mol'),
                                'rf': number('1'), 'rf_evidence': TEXT, 'center_y_m': number('m'), 'sigma_y_m': number('m'), 'uv_basis': TEXT})),
              'photon_exposure_s': number('s', 0.0)})

# ------------------------------------------------------------------------------------------------------------------------- capabilities
CAPS = {}
UNSUPPORTED = {'derivatization.apply', 'lamp.select_mercury'}


def cap(k, i, o, comps, zh, kind='device_command', vis='agent', ex=None):
    CAPS[k] = (i, o, comps, zh, kind, vis, ex or {})


ST = obj({'target_x_m': number('m'), 'target_y_m': number('m')})
cap('plant.configure', PLANT, obj({'configured': BOOL}), ['housing', 'optical_head'], '一次性宿主指定暖机、噪声、板因子、饱和、斑点展宽、F254背景、门速与随机种子（全部为未标定假设，须显式确认）',
    'configuration', 'evaluator', PLANT_EXAMPLE)
cap('plate.register', PLATE, obj({'registered': BOOL, 'mobile_phase_family': TEXT, 'rf_evidence': TEXT}), ['stage'],
    '宿主登记一块已展开的TLC/HPTLC板（流动相体积分数、样道、物质与上样量）；斑点位置取模板库同一流动相的实测Rf', 'configuration', 'evaluator', PLATE_EXAMPLE)
cap('plate.place', obj({}), obj({'loaded': BOOL, 'attachment_event': TEXT}), ['stage', 'stage_stopper', 'door'],
    '宿主在场景把板放到放板位载台后挂接（门全开、载台在load位）', 'device_command', 'evaluator')
cap('plate.remove', obj({'recipient_id': TEXT}), obj({'loaded': BOOL, 'attachment_event': TEXT}), ['stage', 'door'], '把板从载台取下（门全开、载台在load位），板与斑点物料完整移出',
    'device_command', 'evaluator', {'recipient_id': 'external_rack'})
cap('power.set', obj({'enabled': BOOL}), obj({'enabled': BOOL}), ['mains_switch', 'power_inlet', 'led_power'], '主电源；关机取消运行、熄灯，载台停在原处', ex={'enabled': True})
cap('power.toggle', obj({}), obj({'enabled': BOOL}), ['mains_switch', 'led_power'], '后面板主电源翘板开关按一下：切换通电/断电（断电同power.set false）')
cap('door.set', obj({'opened': BOOL}), obj({'target_open': BOOL}), ['door'], '滑门目标（手动滑动，按假设门速推进）；载台在舱外时拒绝关门', ex={'opened': True})
cap('stage.load', obj({}), ST, ['stage', 'stage_carriage', 'key_load'], 'load：载台X回0、Y伸出到放板位（需通电、门全开、无运行）')
cap('stage.home', obj({}), ST, ['stage', 'stage_carriage', 'key_reset'], '载台回原位（舱内X=Y=0）')
cap('stage.jog', obj({'axis': enum('x', 'y'), 'distance_m': number('m', -0.2, 0.2)}), ST, ['stage', 'stage_carriage', 'key_up', 'key_down', 'key_left', 'key_right'],
    '方向键：载台沿X或Y移动给定距离（限位内；越出舱外需门全开）', ex={'axis': 'y', 'distance_m': 0.001})
cap('illumination.set', obj({'enabled': BOOL}), obj({'enabled': BOOL}), ['illumination_tube', 'key_illum'], '扫描舱照明（UV 254 nm荧光管）开关', ex={'enabled': True})
cap('illumination.toggle', obj({}), obj({'enabled': BOOL}), ['illumination_tube', 'key_illum'], 'illum键：切换扫描舱照明')
cap('lamp.select', obj({'source': enum('deuterium', 'halogen_tungsten', 'led_366', 'auto')}), obj({'source': TEXT}), ['optical_head', 'housing'],
    '选择光源（auto=400 nm以下氘灯、以上卤钨灯，两灯同时点亮）；点亮后按宿主暖机时长就绪', ex={'source': 'auto'})
cap('nitrogen.set', obj({'enabled': BOOL}), obj({'enabled': BOOL}), ['nitrogen_inlet'], '单色器氮气吹扫（宿主声明气源）；200 nm以下不吹扫时通量按假设削减', ex={'enabled': False})
cap('method.configure', METHOD, obj({'configured': BOOL}), ['optical_head', 'display_digits'], '扫描方法：吸收/荧光、1–31个波长、带宽5/20 nm、狭缝长宽、扫描速度、数据步长、样道与扫描范围、点样线与前沿',
    'configuration', 'agent', METHOD_EXAMPLE)
cap('scan.start', obj({}), obj({'phase': enum(*PHASES), 'operation_id': TEXT}), ['stage', 'stage_carriage', 'optical_head', 'display_digits'],
    '异步扫描全部样道（每个波长一遍）：载台定位后沿Y匀速扫描并逐点记录信号（需门关、板在台、方法与光源就绪）')
cap('scan.stop', obj({}), obj({'phase': enum(*PHASES)}), ['stage', 'optical_head'], '取消扫描/光谱，保留已记录的点')
cap('spectrum.start', obj({'plate_x_m': number('m', 0.0, 0.2), 'plate_y_m': number('m', 0.0, 0.2), 'start_m': number('m', 190e-9, 900e-9),
                           'end_m': number('m', 190e-9, 900e-9), 'interval_m': number('m', 0.5e-9, 10e-9)}),
    obj({'phase': enum(*PHASES), 'operation_id': TEXT}), ['stage', 'optical_head'], '异步原位光谱：载台把斑点移到光束下，单色器按100 nm/s扫描波长',
    ex={'plate_x_m': 0.015, 'plate_y_m': 0.03, 'start_m': 200e-9, 'end_m': 400e-9, 'interval_m': 2e-9})
cap('process.measure', obj({}), MEASURE, ['display_digits', 'optical_head'], '纯读当前运行阶段、板坐标与最近一点信号', 'query')
cap('results.read', obj({}), RESULT, ['display_digits'], '纯读密度峰图与积分峰（Rf/峰高/峰面积）', 'query')
cap('spectrum.read', obj({}), SPECTRUM, ['display_digits'], '纯读最近一次原位光谱', 'query')
cap('spectrum.match', obj({'min_overlap_m': number('m', 5e-9, 500e-9)}), MATCH, ['housing'], '把最近一次原位光谱与公开库吸收曲线做相关比较，返回前5名（只用公开模板，不读样品真值）',
    'analysis', 'agent', {'min_overlap_m': 40e-9})
cap('quantify.fit', obj({'standard_tracks': arr(integer(0, 35), 2, 36), 'standard_amounts_kg': arr(number('kg', 1e-13, 1e-5), 2, 36),
                         'unknown_tracks': arr(integer(0, 35), 0, 36), 'target_rf': number('1', 0.0, 1.0), 'rf_window': number('1', 0.005, 0.2),
                         'wavelength_m': number('m', 190e-9, 900e-9), 'model': enum('linear', 'quadratic', 'michaelis_menten')}),
    CAL, ['housing'], '用调用方给出的标准量与本次实测峰面积拟合标线并反算未知样道（不读隐藏上样量）', 'analysis', 'agent',
    {'standard_tracks': [0, 1], 'standard_amounts_kg': [2e-10, 4e-10], 'unknown_tracks': [], 'target_rf': 0.5, 'rf_window': 0.05, 'wavelength_m': 254e-9,
     'model': 'linear'})
cap('template.read', obj({'substance_id': TEXT}), TEMPLATE_OUT, ['housing'], '公开参考模板（实测Rf与吸收曲线摘要），不透露当前板身份', 'query', 'agent', {'substance_id': 'xu_tlc_038'})
cap('product.read', obj({}), LEDGER, ['stage'], '私有板账本：板身份、物质、上样量、所用Rf与斑点中心、物料守恒', 'query', 'evaluator')
cap('reset', obj({}), obj({'phase': enum(*PHASES)}), ['key_reset', 'stage', 'stage_carriage'], 'reset键：取消运行、清告警、载台回原位（舱外且门未全开时只取消）')
cap('derivatization.apply', obj({}), obj({}), ['stage'], '衍生化不在本仪器内进行，显色后响应无公开模板，明确UNSUPPORTED', 'configuration')
cap('lamp.select_mercury', obj({}), obj({}), ['optical_head'], '旧版高压汞灯不属于现行LED版产品，明确UNSUPPORTED', 'configuration')

SLOTS, SLOT_ZH = {}, {}


def slot(k, s, unit, zh, origin='derived', vis='agent', comps=('housing',), write=None):
    SLOTS[k] = (s, unit, origin, vis, list(comps), write)
    SLOT_ZH[k] = zh


slot('plant.configured', BOOL, '1', '宿主一次性装置假设已配置', 'setpoint', comps=['housing'])
slot('power.enabled', BOOL, '1', '主电源（后面板开关）', 'setpoint', comps=['mains_switch', 'led_power'], write=('power.set', 'enabled'))
slot('host.online', BOOL, '1', '与宿主（visionCATS代理）联机，通电即联机', 'derived', comps=['led_online'])
slot('door.target_open', BOOL, '1', '滑门目标（true=全开）', 'setpoint', comps=['door'], write=('door.set', 'opened'))
slot('door.position_m', number('m', 0.0, DOOR_OPEN_M), 'm', '滑门向右滑开距离（0=关闭）', 'scene', comps=['door'])
slot('door.closed', BOOL, '1', '滑门完全关闭', 'derived', comps=['door'])
slot('stage.x_m', number('m', *STAGE_X_RANGE), 'm', '载台X位置（关节坐标，0=原位）', 'scene', comps=['stage_carriage'])
slot('stage.y_m', number('m', *STAGE_Y_RANGE), 'm', '载台Y位置（关节坐标，0=原位，−0.185 m=放板位）', 'scene', comps=['stage'])
slot('stage.target_x_m', number('m', *STAGE_X_RANGE), 'm', '载台X目标', 'setpoint', comps=['stage_carriage'])
slot('stage.target_y_m', number('m', *STAGE_Y_RANGE), 'm', '载台Y目标', 'setpoint', comps=['stage'])
slot('stage.moving', BOOL, '1', '载台正在移动', 'derived', comps=['stage'])
slot('stage.at_load', BOOL, '1', '载台在放板位', 'derived', comps=['stage'])
slot('stage.inside', BOOL, '1', '载台完全在扫描舱内（可关门）', 'derived', comps=['stage'])
slot('plate.loaded', BOOL, '1', '载台上有已挂接的板', 'scene', comps=['stage'])
slot('plate.x_m', number('m'), 'm', '光束在板坐标系中的X（板左下角为原点；无板时按20×20 cm名义板）', 'derived', comps=['stage', 'display_digits'])
slot('plate.y_m', number('m'), 'm', '光束在板坐标系中的Y（展开方向）', 'derived', comps=['stage', 'display_digits'])
slot('illumination.enabled', BOOL, '1', '扫描舱照明', 'setpoint', comps=['illumination_tube'], write=('illumination.set', 'enabled'))
slot('lamp.source', enum('none', 'deuterium', 'halogen_tungsten', 'led_366', 'auto'), '1', '选定光源', 'setpoint', comps=['optical_head'])
slot('lamp.on_time_s', number('s', 0.0), 's', '当前光源点亮后的时间', 'derived', comps=['optical_head'])
slot('lamp.ready', BOOL, '1', '光源已达宿主假设暖机时长', 'derived', comps=['optical_head'])
slot('nitrogen.enabled', BOOL, '1', '单色器氮气吹扫', 'setpoint', comps=['nitrogen_inlet'], write=('nitrogen.set', 'enabled'))
slot('method.configured', BOOL, '1', '扫描方法已配置', 'setpoint', comps=['optical_head'])
slot('method.mode', enum('none', 'absorbance', 'fluorescence'), '1', '检测模式', 'setpoint', comps=['optical_head'])
slot('method.bandwidth_m', nullable(number('m')), 'm', '单色器带宽设置', 'setpoint', comps=['optical_head'])
slot('monochromator.wavelength_m', number('m', 190e-9, 900e-9), 'm', '单色器当前波长（LCD的W）', 'derived', comps=['optical_head', 'display_digits'])
slot('scan.phase', enum(*PHASES), '1', '扫描/光谱异步操作生命周期', 'derived', comps=['optical_head'])
slot('scan.operation', enum('none', 'scan', 'spectrum'), '1', '当前或最近一次操作类型', 'derived', comps=['optical_head'])
slot('scan.track_index', nullable(integer(0, 35)), '1', '正在扫描的样道序号', 'derived', comps=['stage'])
slot('scan.completed_points', integer(0, 10 ** 7), '1', '已记录的数据点数', 'derived', comps=['optical_head'])
slot('scan.elapsed_s', number('s', 0.0), 's', '本次操作已推进时间', 'derived', comps=['optical_head'])
slot('scan.signal', nullable(number('1')), '1', '最近一点反射吸收信号（AU）或相对荧光，非实机标定', 'sensor', comps=['optical_head'])
slot('safety.warning', TEXT, '1', '不泄露板身份的告警', 'derived', comps=['display_digits'])
slot('truth.plate', LEDGER, '1', '板身份、物质、上样量、Rf与斑点中心、物料守恒的私有账本', 'process_truth', 'evaluator', comps=['stage'])


# --------------------------------------------------------------------------------------------------------------------------- helpers
def _phi(z):
    return 0.5 * (1.0 + np.vectorize(math.erf)(np.asarray(z, float) / SQ2))


def _gauss_conv(lam_grid, eps, lam, bw):
    """Template epsilon convolved with a Gaussian slit function of FWHM bw, evaluated at lam (arrays in m)."""
    if lam_grid is None or len(lam_grid) == 0:
        return 0.0, False
    sig = bw / 2.354820045
    lo, hi = lam_grid[0], lam_grid[-1]
    if not lo - 1e-15 <= lam <= hi + 1e-15:
        return 0.0, False                                   # outside the template: unknown (callers refuse to measure there)
    nodes = lam + sig * np.linspace(-3.0, 3.0, 25)
    inside = (nodes >= lo) & (nodes <= hi)
    w = np.exp(-0.5 * ((nodes[inside] - lam) / sig) ** 2)    # slit function truncated to the template range and renormalised
    e = np.interp(nodes[inside], lam_grid, eps)
    return float(np.sum(w * e) / np.sum(w)), True


def lamp_throughput(source, lam, nitrogen):
    """Relative source x optics throughput (assumption: smooth shapes inside the documented continuum ranges)."""
    if source in ('deuterium', 'auto') and (source == 'deuterium' or lam <= 400e-9):
        lo, hi = LAMP_RANGES['deuterium']
        if not lo - 1e-12 <= lam <= hi + 1e-12:
            return 0.0
        t = 1.0 if lam <= 300e-9 else 1.0 - 0.7 * (lam - 300e-9) / 150e-9
    elif source in ('halogen_tungsten', 'auto'):
        lo, hi = LAMP_RANGES['halogen_tungsten']
        if not lo - 1e-12 <= lam <= hi + 1e-12:
            return 0.0
        t = 0.2 + 0.8 * min(1.0, (lam - 350e-9) / 100e-9) if lam < 450e-9 else (1.0 if lam <= 800e-9 else 1.0 - 0.4 * (lam - 800e-9) / 100e-9)
    elif source == 'led_366':
        return 1.0 if abs(lam - 366e-9) <= 1e-9 else 0.0
    else:
        return 0.0
    if lam < 200e-9 and not nitrogen:
        t *= 0.2
    return max(t, 0.0)


# ------------------------------------------------------------------------------------------------------------------------------ runtime
class CamagTlcscannerRuntime:
    def __init__(self, instance_id='tlcscanner_01'):
        self.instance_id = instance_id
        doc = json.loads((ROOT / TEMPLATE_REL).read_text(encoding='utf-8'))
        self.templates = {r['substance_id']: r for r in doc['templates']}
        self.library_sha256 = hashlib.sha256((ROOT / TEMPLATE_REL).read_bytes()).hexdigest()
        self._uv = {}
        for k, r in self.templates.items():
            u = r.get('uv_absorption')
            if u:
                self._uv[k] = (np.asarray(u['wavelength_m'], float), np.asarray(u['epsilon_m2_mol'], float))
        self.time_s, self.revision = 0.0, 0
        self.plant = None
        self.powered = False
        self.door_target, self.door_pos = False, 0.0
        self.stage = [0.0, 0.0]
        self.stage_target = [0.0, 0.0]
        self.stage_speed = POSITIONING_SPEED
        self.illumination = False
        self.lamp_source, self.lamp_on_s = 'none', 0.0
        self.nitrogen = False
        self.method = None
        self.mono = 254e-9
        self.plate = None              # registered (truth); dict
        self.plate_location = 'none'   # none | external (registered, in the host's hands) | stage | removed (left the instrument)
        self._registered_kg, self._removed_kg, self._plates_registered = 0.0, 0.0, 0
        self.phase, self.operation, self.operation_id, self._op_count = 'idle', 'none', '', 0
        self.elapsed_s, self.warning = 0.0, ''
        self.traces, self.spectrum = [], None
        self._schedule, self._seg, self._seg_t = [], 0, 0.0
        self.exposure_s = 0.0
        self._rng = np.random.default_rng(0)
        self._principals = {'__public_agent__': 'agent'}
        self._requests, self._owners, self._operation_records = {}, {}, {}

    # -- principals / sessions ---------------------------------------------------------------------------
    def register_principal(self, principal_id, visibility='agent'):
        if visibility not in ('agent', 'debug', 'evaluator') or not isinstance(principal_id, str) or not principal_id:
            raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:
            raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id] = visibility

    def session(self, principal_id='__public_agent__'):
        if principal_id not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return CamagTlcscannerSession(self, principal_id)

    def describe(self):
        return self.session().describe(instance_id=self.instance_id)

    def debug_snapshot(self):
        v = copy.deepcopy({k: x for k, x in self.__dict__.items() if k not in ('templates', '_uv', '_rng')})
        v['_rng_state'] = copy.deepcopy(self._rng.bit_generator.state)
        return v

    # -- derived quantities ---------------------------------------------------------------------------------
    def _plate_dims(self):
        if self.plate and self.plate_location == 'stage':
            return self.plate['width_m'], self.plate['height_m']
        return 0.2, 0.2

    def plate_coords(self, q=None):
        qx, qy = self.stage if q is None else q
        w, h = self._plate_dims()
        return BEAM_FROM_STAGE_CENTRE - qx + w / 2, BEAM_FROM_STAGE_CENTRE - qy + h / 2

    def stage_for(self, x_p, y_p):
        w, h = self._plate_dims()
        return BEAM_FROM_STAGE_CENTRE + w / 2 - x_p, BEAM_FROM_STAGE_CENTRE + h / 2 - y_p

    def _lamp_ready(self):
        return bool(self.powered and self.lamp_source != 'none' and self.plant and self.lamp_on_s >= self.plant['lamp_warmup_s'])

    def _moving(self):
        return abs(self.stage[0] - self.stage_target[0]) > 1e-12 or abs(self.stage[1] - self.stage_target[1]) > 1e-12

    def _plate_mass(self):
        return sum(sp['amount_kg'] for t in self.plate['tracks'] for sp in t['spots']) if self.plate else 0.0

    def _ledger(self):
        m = self._plate_mass()
        ext = m if self.plate_location == 'external' else 0.0
        on = m if self.plate_location == 'stage' else 0.0
        spots = []
        if self.plate:
            for ti, t in enumerate(self.plate['tracks']):
                for sp in t['spots']:
                    spots.append(dict(track_index=ti, substance_id=sp['substance_id'], amount_kg=sp['amount_kg'], amount_mol=sp['amount_mol'], rf=sp['rf'],
                                      rf_evidence=sp['rf_evidence'], center_y_m=sp['center_y_m'], sigma_y_m=sp['sigma_y_m'], uv_basis=sp['uv_basis']))
        reg, rem = self._registered_kg, self._removed_kg
        return dict(plate_id=self.plate['plate_id'] if self.plate else None, attached=self.plate_location == 'stage', location=self.plate_location,
                    mobile_phase_family=self.plate['family'] if self.plate else None, registered_kg=reg, external_kg=ext, on_stage_kg=on, removed_kg=rem,
                    consumed_kg=0.0, residual_kg=reg - ext - on - rem - 0.0, plates_registered=self._plates_registered, spots=spots,
                    photon_exposure_s=self.exposure_s)

    def read(self, visibility='agent'):
        if visibility not in ('agent', 'debug', 'evaluator'):
            raise ActionRejected('ACCESS_DENIED')
        xp, yp = self.plate_coords()
        last = None
        if self.operation == 'scan' and self.traces:
            t = self.traces[-1]
            last = t['signal'][-1] if t['signal'] else None
        elif self.operation == 'spectrum' and self.spectrum and self.spectrum['signal']:
            last = self.spectrum['signal'][-1]
        seg = self._schedule[self._seg] if self.phase == 'running' and self._seg < len(self._schedule) else None
        v = {'plant.configured': bool(self.plant), 'power.enabled': self.powered, 'host.online': self.powered, 'door.target_open': self.door_target,
             'door.position_m': self.door_pos, 'door.closed': self.door_pos <= 1e-9, 'stage.x_m': self.stage[0], 'stage.y_m': self.stage[1],
             'stage.target_x_m': self.stage_target[0], 'stage.target_y_m': self.stage_target[1], 'stage.moving': self._moving(),
             'stage.at_load': abs(self.stage[1] - LOAD_Y) < 1e-6 and abs(self.stage[0]) < 1e-6, 'stage.inside': self.stage[1] >= Y_INSIDE - 1e-12,
             'plate.loaded': self.plate_location == 'stage', 'plate.x_m': xp, 'plate.y_m': yp, 'illumination.enabled': self.illumination,
             'lamp.source': self.lamp_source, 'lamp.on_time_s': self.lamp_on_s, 'lamp.ready': self._lamp_ready(), 'nitrogen.enabled': self.nitrogen,
             'method.configured': bool(self.method), 'method.mode': self.method['mode'] if self.method else 'none',
             'method.bandwidth_m': self.method['bandwidth_m'] if self.method else None, 'monochromator.wavelength_m': self.mono,
             'scan.phase': self.phase, 'scan.operation': self.operation, 'scan.track_index': seg['track'] if seg and seg.get('track') is not None else None,
             'scan.completed_points': self._points(), 'scan.elapsed_s': self.elapsed_s, 'scan.signal': last, 'safety.warning': self.warning,
             'truth.plate': self._ledger()}
        return copy.deepcopy({k: x for k, x in v.items() if visibility != 'agent' or SLOTS[k][3] == 'agent'})

    def _points(self):
        if self.operation == 'spectrum' and self.spectrum:
            return len(self.spectrum['signal'])
        return sum(len(t['signal']) for t in self.traces)

    # -- plate physics ------------------------------------------------------------------------------------------
    def _family(self, phase):
        tot = sum(phase.values())
        if abs(tot - 1.0) > 1e-3:
            raise ActionRejected('MOBILE_PHASE_FRACTIONS')
        nz = {k for k, v in phase.items() if v > 1e-9}
        for fam, (a, b) in FAMILIES.items():
            if nz <= {a, b}:
                return fam, phase[b]
        raise ActionRejected('MOBILE_PHASE_OUTSIDE_TEMPLATE')

    def rf_for(self, substance_id, phase):
        rec = self.templates.get(substance_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_REFERENCE')
        fam, frac = self._family(phase)
        a, b = FAMILIES[fam]
        groups = {}                                         # polar fraction -> replicate Rf values (merged duplicate dataset ids)
        for p in rec['values']['rf_points']:
            mp = p['mobile_phase_volume_fraction']
            nz = {k for k, v in mp.items() if v > 1e-9}
            if nz <= {a, b}:
                groups.setdefault(round(mp[b], 6), []).append((p['rf'], p.get('conflict', False)))
        if not groups:
            raise ActionRejected('MOBILE_PHASE_OUTSIDE_TEMPLATE')

        def value(f_):
            vals = groups[f_]
            if any(c for _, c in vals):
                raise ActionRejected('RF_TEMPLATE_CONFLICT')
            return sum(v for v, _ in vals) / len(vals), len(vals)
        fr = sorted(groups)
        for f_ in fr:
            if abs(f_ - frac) <= 1e-4:
                rf, n = value(f_)
                return rf, 'measured' if n == 1 else 'measured_mean', fam
        lo = [f_ for f_ in fr if f_ < frac]
        hi = [f_ for f_ in fr if f_ > frac]
        if not lo or not hi:
            raise ActionRejected('MOBILE_PHASE_OUTSIDE_TEMPLATE')
        (r0, _), (r1, _) = value(lo[-1]), value(hi[0])
        f0, f1 = lo[-1], hi[0]
        return r0 + (r1 - r0) * (frac - f0) / (f1 - f0), 'interpolated_measured', fam

    def _eps(self, substance_id, lam, bw):
        """(epsilon at lam [m2/mol], covered by template, basis)."""
        if substance_id in self._uv:
            g, e = self._uv[substance_id]
            val, ok = _gauss_conv(g, e, lam, bw)
            return val, ok, self.templates[substance_id]['uv_absorption']['basis']
        if self.plant and self.plant['accept_synthetic_response']:
            ok = 200e-9 - 1e-15 <= lam <= 400e-9 + 1e-15
            return (500.0 if ok else 0.0), ok, 'synthetic_flat_200_400nm'
        return 0.0, False, 'none'

    def _uncovered(self, wavelengths, near=None):
        """True if some analyte on the plate (optionally: whose spot reaches the plate point `near`) has no absorption data at one of the
        wavelengths - the simulator then cannot predict the signal and refuses the measurement (it never silently returns zero)."""
        for t in self.plate['tracks']:
            for sp in t['spots']:
                if near is not None:
                    x_, y_ = near
                    if abs(x_ - t['track_x_m']) > t['band_length_m'] / 2 + 4 * self.plant['band_edge_sigma_m'] + self.method['slit_length_m'] / 2 or \
                            abs(y_ - sp['center_y_m']) > 4 * sp['sigma_y_m'] + self.method['slit_width_m'] / 2:
                        continue
                for lam in wavelengths:
                    if not self._eps(sp['substance_id'], lam, self.method['bandwidth_m'])[1]:
                        return True
        return False

    def _surface(self, xp, yp, lam, bw):
        """Slit-averaged decadic absorbance contribution sum k*eps*Gamma at plate point (xp, yp); also template coverage flag."""
        m, p = self.method, self.plant
        Ls, Ws = m['slit_length_m'], m['slit_width_m']
        xs = xp + Ls * np.linspace(-0.5, 0.5, 15)
        total, covered = 0.0, True
        for t in self.plate['tracks']:
            x0, Lb, sx = t['track_x_m'], t['band_length_m'], p['band_edge_sigma_m']
            px = np.mean((_phi((xs - x0 + Lb / 2) / sx) - _phi((xs - x0 - Lb / 2) / sx)) / Lb)
            if px <= 1e-12:
                continue
            for sp in t['spots']:
                sy = sp['sigma_y_m']
                py = float((_phi((yp + Ws / 2 - sp['center_y_m']) / sy) - _phi((yp - Ws / 2 - sp['center_y_m']) / sy)) / Ws)
                if py <= 1e-15:
                    continue
                eps, ok, _ = self._eps(sp['substance_id'], lam, bw)
                covered = covered and ok
                total += p['plate_factor'] * eps * sp['amount_mol'] * px * py
        return total, covered

    def _signal(self, xp, yp, lam):
        m, p = self.method, self.plant
        a, covered = self._surface(xp, yp, lam, m['bandwidth_m'])
        thr = lamp_throughput(self.lamp_source, lam, self.nitrogen)
        sigma = p['noise_au'] / math.sqrt(max(thr, 1e-6))
        if m['mode'] == 'absorbance':
            clean = p['saturation_au'] * (1.0 - math.exp(-a / p['saturation_au']))
        else:   # fluorescence quenching of the F254 indicator (only below 300 nm excitation); native analyte emission not modelled
            bg = p['f254_background'] if (self.plate['layer'] == 'silica_gel_60_f254' and lam < 300e-9) else 0.0
            clean = bg * 10.0 ** (-a)
        return clean + (float(self._rng.normal(0.0, sigma)) if sigma > 0 else 0.0), covered

    # -- operations -----------------------------------------------------------------------------------------------
    def _new_op(self, kind):
        self._op_count += 1
        self.operation, self.phase, self.elapsed_s, self.warning = kind, 'running', 0.0, ''
        self.operation_id = f'{self.instance_id}:{kind}:{self._op_count}'
        return self.operation_id

    def _archive(self):
        if self.operation_id:
            self._operation_records[self.operation_id] = dict(operation_id=self.operation_id, status=self.phase, result=self._measure())

    def _stop(self, phase, warning=''):
        if self.phase == 'running':
            self.phase = phase
            self.warning = warning
            self.stage_target = list(self.stage)
            self._archive()

    def _ready_to_measure(self):
        if not self.powered:
            raise ActionRejected('POWER_OFF')
        if not self.plant:
            raise ActionRejected('PLANT_NOT_CONFIGURED')
        if self.door_pos > 1e-9 or self.door_target:
            raise ActionRejected('DOOR_OPEN')
        if self.plate_location != 'stage':
            raise ActionRejected('NO_PLATE')
        if self.phase == 'running':
            raise ActionRejected('BUSY')
        if not self._lamp_ready():
            raise ActionRejected('LAMP_NOT_READY')

    def _build_scan(self):
        m = self.method
        y0, y1 = m['scan_start_y_m'], m['scan_end_y_m']
        n = int(math.floor(abs(y1 - y0) / m['data_step_m'] + 1e-9)) + 1
        if n < 5 or n > 8001:
            raise ActionRejected('SCAN_RANGE')
        sched, pos, lam = [], list(self.stage), self.mono
        w, h = self.plate['width_m'], self.plate['height_m']
        if m['mode'] == 'absorbance' and self.lamp_source == 'led_366':
            raise ActionRejected('LED_FLUORESCENCE_ONLY')
        if self._uncovered(m['wavelengths_m']):
            raise ActionRejected('TEMPLATE_RANGE')
        for li, wl in enumerate(m['wavelengths_m']):
            if m['mode'] == 'fluorescence' and self.lamp_source == 'led_366' and abs(wl - 366e-9) > 1e-12:
                raise ActionRejected('WAVELENGTH_OUTSIDE_LAMP_RANGE')
            if lamp_throughput(self.lamp_source, wl, self.nitrogen) <= 0.0:
                raise ActionRejected('WAVELENGTH_OUTSIDE_LAMP_RANGE')
            sched.append(dict(kind='mono', duration=float(abs(wl - lam) / MONO_SPEED), lam0=float(lam), lam1=float(wl)))
            lam = wl
            for ti, x in enumerate(m['tracks_x_m']):
                if not (0.0 <= x <= w and 0.0 <= min(y0, y1) and max(y0, y1) <= h):
                    raise ActionRejected('OUTSIDE_PLATE')
                q0 = self.stage_for(x, y0)
                q1 = self.stage_for(x, y1)
                for q in (q0, q1):
                    if not (STAGE_X_RANGE[0] - 1e-9 <= q[0] <= STAGE_X_RANGE[1] + 1e-9 and Y_INSIDE - 1e-9 <= q[1] <= STAGE_Y_RANGE[1] + 1e-9):
                        raise ActionRejected('OUTSIDE_STAGE_TRAVEL')
                d = max(abs(q0[0] - pos[0]), abs(q0[1] - pos[1]))
                sched.append(dict(kind='move', duration=float(d / POSITIONING_SPEED), p0=[float(v) for v in pos], p1=[float(v) for v in q0], track=ti))
                ys = [float(v) for v in np.linspace(y0, y1, n)]
                sched.append(dict(kind='scan', duration=float(abs(y1 - y0) / m['scan_speed_m_s']), p0=[float(v) for v in q0], p1=[float(v) for v in q1], track=ti, x=x, wavelength=wl,
                                  ys=ys, done=0, li=li))
                pos = list(q1)
        return sched

    def _measure(self):
        xp, yp = self.plate_coords()
        seg = self._schedule[self._seg] if self.phase == 'running' and self._seg < len(self._schedule) else None
        last = None
        if self.operation == 'scan' and self.traces and self.traces[-1]['signal']:
            last = self.traces[-1]['signal'][-1]
        if self.operation == 'spectrum' and self.spectrum and self.spectrum['signal']:
            last = self.spectrum['signal'][-1]
        return dict(phase=self.phase, operation=self.operation, track_index=seg['track'] if seg and seg.get('track') is not None else None,
                    wavelength_m=self.mono if self.lamp_source != 'none' else None, plate_x_m=xp, plate_y_m=yp, signal=last,
                    completed_points=self._points(), elapsed_s=self.elapsed_s, warning=self.warning)

    # -- evaluation ---------------------------------------------------------------------------------------------------
    def _peaks(self, trace):
        """Baseline (iterative linear fit through the points that are not peaks), 5-point smoothing, regions above max(5 sigma, 1 mAU), each region
        split at the valleys between maxima whose prominence exceeds max(3 sigma, 5 % of the smaller maximum) (drop-line integration)."""
        y, s = np.asarray(trace['y_m'], float), np.asarray(trace['signal'], float)
        if len(s) < 9:
            return []
        if self.method['mode'] == 'fluorescence':
            s = -s                                                    # quenching gives dips in the indicator background
        noise = float(np.median(np.abs(np.diff(s))) / 0.6745 / SQ2)
        keep = np.ones(len(s), bool)
        base = np.full(len(s), float(np.min(s)))
        for _ in range(6):                                            # robust baseline: refit without the points standing above it
            if keep.sum() < 4:
                break
            c1, c0 = np.polyfit(y[keep], s[keep], 1)
            base = c0 + c1 * y
            new_keep = (s - base) <= max(3.0 * noise, 5e-4)
            if (new_keep == keep).all():
                break
            keep = new_keep
        r = s - base
        sm = np.convolve(r, np.ones(5) / 5.0, mode='same')
        thr = max(5.0 * noise, 1e-3)
        app, front = self.method['application_y_m'], self.method['front_y_m']
        above = sm > thr
        peaks, i = [], 0
        while i < len(above):
            if not above[i]:
                i += 1
                continue
            j = i
            while j < len(above) and above[j]:
                j += 1
            a0, a1 = max(0, i - 1), min(len(r) - 1, j)
            # maxima and valleys inside the region
            idx = list(range(a0, a1 + 1))
            maxima = [k for k in idx[1:-1] if sm[k] >= sm[k - 1] and sm[k] > sm[k + 1] and sm[k] > thr]
            if not maxima:
                maxima = [a0 + int(np.argmax(sm[a0:a1 + 1]))]
            merged = [maxima[0]]
            for k in maxima[1:]:
                prev = merged[-1]
                v = prev + int(np.argmin(sm[prev:k + 1]))
                prom = min(sm[prev], sm[k]) - sm[v]
                if prom >= max(3.0 * noise, 0.05 * min(sm[prev], sm[k])):
                    merged.append(k)
                elif sm[k] > sm[prev]:
                    merged[-1] = k
            bounds = [a0] + [merged[n] + int(np.argmin(sm[merged[n]:merged[n + 1] + 1])) for n in range(len(merged) - 1)] + [a1]
            for n, kmax in enumerate(merged):
                b0, b1 = bounds[n], bounds[n + 1]
                seg = slice(b0, b1 + 1)
                area = float(np.sum(0.5 * (r[seg][1:] + r[seg][:-1]) * np.abs(np.diff(y[seg])))) if b1 > b0 else 0.0
                peaks.append(dict(rf=float((y[kmax] - app) / (front - app)), y_m=float(y[kmax]), height_au=float(sm[kmax]), area_au_m=area,
                                  start_y_m=float(y[b0]), end_y_m=float(y[b1])))
            i = j
        return peaks

    def _result(self):
        cov = [t.get('_covered', True) for t in self.traces]
        return dict(phase=self.phase, complete=self.phase == 'succeeded' and self.operation == 'scan',
                    traces=[{k: v for k, v in t.items() if not k.startswith('_')} for t in self.traces], warning=self.warning,
                    template_source=TEMPLATE_REL, library_sha256=self.library_sha256,
                    evidence='measured Rf (Xu et al. 2022) + measured/derived solution absorption -> simulated reflectance densitogram (uncalibrated)',
                    template_coverage='none' if not self.traces else ('complete' if all(cov) else 'partial'),
                    limitations=['k_plate、饱和常数、斑点展宽与噪声为宿主假设，未用本机实测标定', '溶液吸收谱转用于板上反射为derived近似',
                                 '荧光模式只模拟F254指示剂背景被吸收物淬灭，未模拟分析物自身荧光'])

    # -- protocol entry --------------------------------------------------------------------------------------------------
    def invoke(self, capability_id, **arguments):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id][0], arguments)
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        out = self._invoke(capability_id, arguments)
        if CAPS[capability_id][4] not in ('query', 'analysis'):
            self.revision += 1
        return out

    def _invoke(self, k, a):
        if k == 'plant.configure':
            if self.plant:
                raise ActionRejected('INITIALIZATION_ONLY')
            if not a['accept_uncalibrated_model']:
                raise ActionRejected('EVIDENCE_ACK_REQUIRED')
            self.plant = dict(a)
            self._rng = np.random.default_rng(a['seed'])
            return dict(configured=True)
        if k == 'plate.register':
            if self.plate_location in ('external', 'stage'):
                raise ActionRejected('PLATE_EXISTS')
            if not self.plant:
                raise ActionRejected('PLANT_NOT_CONFIGURED')
            if a['derivatized']:
                raise ActionRejected('UNSUPPORTED')
            if not a['application_y_m'] < a['front_y_m'] <= a['height_m']:
                raise ActionRejected('PLATE_GEOMETRY')
            fam, _ = self._family(a['mobile_phase'])
            plate = copy.deepcopy(a)
            plate['family'] = fam
            evs = set()
            for t in plate['tracks']:
                if not t['track_x_m'] <= a['width_m']:
                    raise ActionRejected('PLATE_GEOMETRY')
                for sp in t['spots']:
                    rf, ev, _ = self.rf_for(sp['substance_id'], a['mobile_phase'])
                    rec = self.templates[sp['substance_id']]
                    if sp['substance_id'] not in self._uv and not self.plant['accept_synthetic_response']:
                        raise ActionRejected('NO_ABSORPTION_TEMPLATE')
                    d = rf * (a['front_y_m'] - a['application_y_m'])
                    sp.update(rf=rf, rf_evidence=ev, amount_mol=sp['amount_kg'] / rec['molar_mass_kg_mol'],
                              center_y_m=a['application_y_m'] + d,
                              sigma_y_m=math.sqrt(self.plant['spot_sigma0_m'] ** 2 + self.plant['spot_broadening_m'] * d),
                              uv_basis=(rec['uv_absorption'] or {}).get('basis', 'synthetic_flat_200_400nm'))
                    evs.add(ev)
            self.plate, self.plate_location = plate, 'external'
            self._registered_kg += self._plate_mass()
            self._plates_registered += 1
            return dict(registered=True, mobile_phase_family=fam, rf_evidence='interpolated_measured' if 'interpolated_measured' in evs else 'measured')
        if k in ('plate.place', 'plate.remove'):
            if self.door_pos < DOOR_OPEN_M - 1e-9 or not self.door_target:
                raise ActionRejected('DOOR_CLOSED')
            if not (abs(self.stage[1] - LOAD_Y) < 1e-6 and abs(self.stage[0]) < 1e-6) or self._moving():
                raise ActionRejected('STAGE_NOT_AT_LOAD')
            if self.phase == 'running':
                raise ActionRejected('BUSY')
            if k == 'plate.place':
                if self.plate is None or self.plate_location != 'external':
                    raise ActionRejected('NO_PLATE')
                self.plate_location = 'stage'
                return dict(loaded=True, attachment_event='plate_attached_to_stage')
            if self.plate_location != 'stage':
                raise ActionRejected('NO_PLATE')
            self.plate_location = 'removed'
            self._removed_kg += self._plate_mass()
            return dict(loaded=False, attachment_event='plate_detached_to_' + a['recipient_id'])
        if k == 'power.toggle':
            a = {'enabled': not self.powered}
            k = 'power.set'
        if k == 'power.set':
            if a['enabled'] and not self.powered:
                self.powered = True
            elif not a['enabled'] and self.powered:
                self._stop('cancelled', 'POWER_OFF')
                self.powered, self.lamp_source, self.lamp_on_s = False, 'none', 0.0
                self.stage_target = list(self.stage)
                self.illumination = False
            return dict(enabled=self.powered)
        if k == 'door.set':
            if not self.plant:
                raise ActionRejected('PLANT_NOT_CONFIGURED')
            if not a['opened'] and (self.stage[1] < Y_INSIDE - 1e-12 or self.stage_target[1] < Y_INSIDE - 1e-12):
                raise ActionRejected('STAGE_OBSTRUCTS_DOOR')
            if a['opened'] and self.phase == 'running':
                raise ActionRejected('BUSY')
            self.door_target = a['opened']
            return dict(target_open=self.door_target)
        if k in ('stage.load', 'stage.home', 'stage.jog'):
            if not self.powered:
                raise ActionRejected('POWER_OFF')
            if self.phase == 'running':
                raise ActionRejected('BUSY')
            if k == 'stage.load':
                tgt = [0.0, LOAD_Y]
            elif k == 'stage.home':
                tgt = [0.0, 0.0]
            else:
                tgt = list(self.stage_target)
                i = 0 if a['axis'] == 'x' else 1
                tgt[i] = tgt[i] + a['distance_m']
                rng = STAGE_X_RANGE if i == 0 else STAGE_Y_RANGE
                if not rng[0] - 1e-12 <= tgt[i] <= rng[1] + 1e-12:
                    raise ActionRejected('OUTSIDE_STAGE_TRAVEL')
            if (tgt[1] < Y_INSIDE - 1e-12 or self.stage[1] < Y_INSIDE - 1e-12) and (self.door_pos < DOOR_OPEN_M - 1e-9 or not self.door_target):
                raise ActionRejected('DOOR_CLOSED')
            self.stage_target, self.stage_speed = tgt, POSITIONING_SPEED
            return dict(target_x_m=tgt[0], target_y_m=tgt[1])
        if k in ('illumination.set', 'illumination.toggle'):
            if not self.powered:
                raise ActionRejected('POWER_OFF')
            self.illumination = (not self.illumination) if k == 'illumination.toggle' else a['enabled']
            return dict(enabled=self.illumination)
        if k == 'lamp.select':
            if not self.powered:
                raise ActionRejected('POWER_OFF')
            if self.phase == 'running':
                raise ActionRejected('BUSY')
            if a['source'] != self.lamp_source:
                self.lamp_source, self.lamp_on_s = a['source'], 0.0
            return dict(source=self.lamp_source)
        if k == 'nitrogen.set':
            if self.phase == 'running':
                raise ActionRejected('BUSY')
            self.nitrogen = a['enabled']
            return dict(enabled=self.nitrogen)
        if k == 'method.configure':
            if self.phase == 'running':
                raise ActionRejected('BUSY')
            if not a['application_y_m'] < a['front_y_m'] or abs(a['scan_end_y_m'] - a['scan_start_y_m']) < 5 * a['data_step_m']:
                raise ActionRejected('INVALID_ARGUMENT')
            if a['mode'] == 'fluorescence' and any(wl > 400e-9 for wl in a['wavelengths_m']):
                raise ActionRejected('EXCITATION_ABOVE_CUTOFF')
            self.method = copy.deepcopy(a)
            return dict(configured=True)
        if k == 'scan.start':
            self._ready_to_measure()
            if not self.method:
                raise ActionRejected('METHOD_NOT_CONFIGURED')
            sched = self._build_scan()
            self._schedule, self._seg, self._seg_t = sched, 0, 0.0
            self.traces, self.spectrum = [], None
            self.illumination = False                              # the compartment lamp would add stray light: off while measuring
            op = self._new_op('scan')
            return dict(phase=self.phase, operation_id=op)
        if k == 'scan.stop':
            self._stop('cancelled', 'CANCELLED')
            return dict(phase=self.phase)
        if k == 'spectrum.start':
            self._ready_to_measure()
            if not self.method:
                raise ActionRejected('METHOD_NOT_CONFIGURED')
            if self.method['mode'] != 'absorbance':
                raise ActionRejected('SPECTRUM_ABSORBANCE_ONLY')
            if not a['start_m'] < a['end_m']:
                raise ActionRejected('INVALID_ARGUMENT')
            lams = [float(v) for v in np.arange(a['start_m'], a['end_m'] + 0.5 * a['interval_m'], a['interval_m'])]
            if self.lamp_source == 'led_366':
                raise ActionRejected('LED_FLUORESCENCE_ONLY')
            if len(lams) < 5 or any(lamp_throughput(self.lamp_source, l_, self.nitrogen) <= 0.0 for l_ in lams):
                raise ActionRejected('WAVELENGTH_OUTSIDE_LAMP_RANGE')
            if self._uncovered(lams, near=(a['plate_x_m'], a['plate_y_m'])):
                raise ActionRejected('TEMPLATE_RANGE')
            w, h = self.plate['width_m'], self.plate['height_m']
            if not (a['plate_x_m'] <= w and a['plate_y_m'] <= h):
                raise ActionRejected('OUTSIDE_PLATE')
            q = self.stage_for(a['plate_x_m'], a['plate_y_m'])
            if not (STAGE_X_RANGE[0] - 1e-9 <= q[0] <= STAGE_X_RANGE[1] + 1e-9 and Y_INSIDE - 1e-9 <= q[1] <= STAGE_Y_RANGE[1] + 1e-9):
                raise ActionRejected('OUTSIDE_STAGE_TRAVEL')
            d = max(abs(q[0] - self.stage[0]), abs(q[1] - self.stage[1]))
            self._schedule = [dict(kind='move', duration=float(d / POSITIONING_SPEED), p0=[float(v) for v in self.stage], p1=[float(v) for v in q], track=None),
                              dict(kind='mono', duration=float(abs(lams[0] - self.mono) / MONO_SPEED), lam0=float(self.mono), lam1=lams[0]),
                              dict(kind='spectrum', duration=float((lams[-1] - lams[0]) / SPECTRUM_SPEED), lams=lams, done=0, x=a['plate_x_m'],
                                   y=a['plate_y_m'], track=None)]
            self._seg, self._seg_t = 0, 0.0
            self.spectrum = dict(plate_x_m=a['plate_x_m'], plate_y_m=a['plate_y_m'], wavelength_m=[], signal=[], _covered=True)
            self.illumination = False
            op = self._new_op('spectrum')
            return dict(phase=self.phase, operation_id=op)
        if k == 'process.measure':
            return self._measure()
        if k == 'results.read':
            return self._result()
        if k == 'spectrum.read':
            s = self.spectrum or dict(plate_x_m=None, plate_y_m=None, wavelength_m=[], signal=[])
            return dict(phase=self.phase if self.operation == 'spectrum' else 'idle', complete=self.operation == 'spectrum' and self.phase == 'succeeded',
                        plate_x_m=s['plate_x_m'], plate_y_m=s['plate_y_m'], wavelength_m=list(s['wavelength_m']), signal=list(s['signal']),
                        warning=self.warning if self.operation == 'spectrum' else '', template_source=TEMPLATE_REL,
                        evidence='solution absorption template (measured NIST / derived HSDB band model) transferred to the plate (derived)',
                        limitations=['原位光谱=溶液ε(λ)经板因子与饱和变换，非板上实测反射谱'])
        if k == 'spectrum.match':
            s = self.spectrum
            if not s or len(s['signal']) < 5:
                raise ActionRejected('NO_SPECTRUM')
            lam = np.asarray(s['wavelength_m'])
            sig = np.asarray(s['signal'])
            cands = []
            for sid, (g, e) in self._uv.items():
                sel = (lam >= g[0]) & (lam <= g[-1])
                if sel.sum() < 5 or lam[sel][-1] - lam[sel][0] < a['min_overlap_m'] - 1e-15:
                    continue
                ref = np.interp(lam[sel], g, e)
                x_, y_ = sig[sel] - sig[sel].mean(), ref - ref.mean()
                den = math.sqrt(float(np.sum(x_ ** 2) * np.sum(y_ ** 2)))
                if den <= 0:
                    continue
                cands.append(dict(substance_id=sid, name_en=self.templates[sid]['name_en'], correlation=float(np.sum(x_ * y_) / den),
                                  overlap_m=float(lam[sel][-1] - lam[sel][0]), uv_basis=self.templates[sid]['uv_absorption']['basis']))
            cands.sort(key=lambda c: -c['correlation'])
            return dict(candidates=cands[:5], compared=len(cands), evidence='Pearson correlation against public library curves (shape only)')
        if k == 'quantify.fit':
            return self._fit(a)
        if k == 'template.read':
            rec = self.templates.get(a['substance_id'])
            if rec is None:
                raise ActionRejected('UNKNOWN_REFERENCE')
            u = rec.get('uv_absorption') or {}
            return dict(substance_id=rec['substance_id'], name_en=rec['name_en'], cas=rec['cas'], molar_mass_kg_mol=rec['molar_mass_kg_mol'],
                        rf_points=[dict(mobile_phase=p['mobile_phase_volume_fraction'], rf=p['rf']) for p in rec['values']['rf_points']],
                        uv_basis=u.get('basis'), lambda_max_m=u.get('lambda_max_m'), epsilon_max_m2_mol=u.get('epsilon_max_m2_mol'),
                        source=rec['source']['dataset'], evidence=rec['evidence'], licence=rec['licence'])
        if k == 'product.read':
            return self._ledger()
        if k == 'reset':
            self._stop('cancelled', 'RESET')
            self.warning = ''
            if self.powered and (self.stage[1] >= Y_INSIDE - 1e-12 or (self.door_pos >= DOOR_OPEN_M - 1e-9 and self.door_target)):
                self.stage_target, self.stage_speed = [0.0, 0.0], POSITIONING_SPEED
            return dict(phase=self.phase)
        raise ActionRejected('UNSUPPORTED')

    def _fit(self, a):
        if len(a['standard_tracks']) != len(a['standard_amounts_kg']):
            raise ActionRejected('INVALID_ARGUMENT')
        traces = [t for t in self.traces if t['complete'] and abs(t['wavelength_m'] - a['wavelength_m']) <= 1e-12]
        by_track = {t['track_index']: t for t in traces}

        def area(ti):
            t = by_track.get(ti)
            if t is None:
                raise ActionRejected('NO_COMPLETE_TRACE')
            near = [p for p in t['peaks'] if abs(p['rf'] - a['target_rf']) <= a['rf_window']]
            return max(near, key=lambda p: p['area_au_m'])['area_au_m'] if near else None
        std = [dict(track_index=ti, amount_kg=m_, area_au_m=area(ti)) for ti, m_ in zip(a['standard_tracks'], a['standard_amounts_kg'])]
        pts = [(s['amount_kg'], s['area_au_m']) for s in std if s['area_au_m'] is not None]
        need = {'linear': 2, 'quadratic': 3, 'michaelis_menten': 3}[a['model']]
        if len({p[0] for p in pts}) < need:
            raise ActionRejected('CALIBRATION_INPUT')
        x, y = np.array([p[0] for p in pts]) * 1e9, np.array([p[1] for p in pts])      # fit in ng for conditioning
        if a['model'] == 'linear':
            c = np.polyfit(x, y, 1)[::-1]

            def f(v):
                return c[0] + c[1] * v
        elif a['model'] == 'quadratic':
            c = np.polyfit(x, y, 2)[::-1]

            def f(v):
                return c[0] + c[1] * v + c[2] * v * v
        else:
            vm, km = float(y.max() * 1.5), float(np.median(x))
            for _ in range(200):                                                   # Gauss-Newton on y = vm x / (km + x)
                pred = vm * x / (km + x)
                J = np.column_stack([x / (km + x), -vm * x / (km + x) ** 2])
                step, *_ = np.linalg.lstsq(J, y - pred, rcond=None)
                vm, km = vm + step[0], max(km + step[1], 1e-9)
                if np.max(np.abs(step)) < 1e-12:
                    break
            c = np.array([vm, km])

            def f(v):
                return c[0] * v / (c[1] + v)
        pred = np.array([f(v) for v in x])
        ss = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - float(np.sum((y - pred) ** 2)) / ss if ss > 0 else None
        lo, hi = float(x.min()), float(x.max())
        grid = np.linspace(lo, hi, 2001)
        fg = np.array([f(v) for v in grid])
        unknown = []
        for ti in a['unknown_tracks']:
            ar = area(ti)
            est, inside = None, False
            tol = 1e-9 * max(abs(fg[0]), abs(fg[-1]))
            if ar is not None and np.all(np.diff(fg) > 0) and fg[0] - tol <= ar <= fg[-1] + tol:
                est, inside = float(np.interp(min(max(ar, fg[0]), fg[-1]), fg, grid)) * 1e-9, True
            unknown.append(dict(track_index=ti, area_au_m=ar, estimated_amount_kg=est, within_calibration_range=inside))
        scale = {'linear': [1.0, 1e9], 'quadratic': [1.0, 1e9, 1e18], 'michaelis_menten': [1.0, 1e-9]}[a['model']]
        return dict(model=a['model'], coefficients=[float(ci * s_) for ci, s_ in zip(c, scale)], r_squared=r2, standards=std, unknowns=unknown,
                    evidence='fit of caller-given standard amounts to measured (simulated) peak areas; amounts in kg, areas in AU*m; extrapolation returns null')

    # -- clock --------------------------------------------------------------------------------------------------------------
    def step(self, dt_s):
        validate(number('s', 0.0, 86400.0), dt_s)
        if dt_s == 0:
            return
        if self.powered and self.lamp_source != 'none':
            self.lamp_on_s += dt_s
        if self.plant:                                             # door: operator motion toward the target
            tgt = DOOR_OPEN_M if self.door_target else 0.0
            dv = self.plant['door_speed_m_s'] * dt_s
            self.door_pos = min(tgt, self.door_pos + dv) if tgt > self.door_pos else max(tgt, self.door_pos - dv)
        if self.phase == 'running':
            self._advance_op(dt_s)
        elif self.powered:                                         # free stage motion (load / home / jog)
            for i in (0, 1):
                d = self.stage_target[i] - self.stage[i]
                mv = self.stage_speed * dt_s
                self.stage[i] = self.stage_target[i] if abs(d) <= mv else self.stage[i] + math.copysign(mv, d)
        self.time_s += dt_s
        self.revision += 1

    def _advance_op(self, dt_s):
        remaining = dt_s
        while remaining > 1e-15 and self.phase == 'running':
            if self._seg >= len(self._schedule):
                self.phase = 'succeeded'
                self.stage_target = list(self.stage)
                self._archive()
                break
            seg = self._schedule[self._seg]
            dur = seg['duration']
            use = min(remaining, max(0.0, dur - self._seg_t))
            self._seg_t += use
            remaining -= use
            self.elapsed_s += use
            frac = 1.0 if dur <= 0 else min(1.0, self._seg_t / dur)
            if seg['kind'] in ('move', 'scan'):
                self.stage = [seg['p0'][i] + (seg['p1'][i] - seg['p0'][i]) * frac for i in (0, 1)]
                self.stage_target = list(self.stage)
            if seg['kind'] == 'mono':
                self.mono = seg['lam0'] + (seg['lam1'] - seg['lam0']) * frac
            if seg['kind'] in ('scan', 'spectrum'):
                self.exposure_s += use
            if seg['kind'] == 'scan':
                self.mono = seg['wavelength']
                if seg['done'] == 0 and (not self.traces or self.traces[-1]['_seg'] != self._seg):
                    self.traces.append(dict(track_index=seg['track'], track_x_m=seg['x'], wavelength_m=seg['wavelength'], mode=self.method['mode'],
                                            signal_unit='AU' if self.method['mode'] == 'absorbance' else 'relative_fluorescence',
                                            complete=False, y_m=[], signal=[], peaks=[], _seg=self._seg, _covered=True))
                tr = self.traces[-1]
                n = len(seg['ys'])
                upto = n if frac >= 1.0 else int(math.floor(frac * (n - 1) + 1e-9)) + 1
                while seg['done'] < upto:
                    yv = seg['ys'][seg['done']]
                    sval, cov = self._signal(seg['x'], yv, seg['wavelength'])
                    tr['y_m'].append(yv)
                    tr['signal'].append(sval)
                    tr['_covered'] = tr['_covered'] and cov
                    seg['done'] += 1
                if frac >= 1.0:
                    tr['complete'] = True
                    tr['peaks'] = self._peaks(tr)
            if seg['kind'] == 'spectrum':
                lams = seg['lams']
                n = len(lams)
                upto = n if frac >= 1.0 else int(math.floor(frac * (n - 1) + 1e-9)) + 1
                while seg['done'] < upto:
                    lv = lams[seg['done']]
                    self.mono = lv
                    m = self.method
                    a_, cov = self._surface(seg['x'], seg['y'], lv, m['bandwidth_m'])
                    thr = lamp_throughput(self.lamp_source, lv, self.nitrogen)
                    sigma = self.plant['noise_au'] / math.sqrt(max(thr, 1e-6))
                    val = self.plant['saturation_au'] * (1.0 - math.exp(-a_ / self.plant['saturation_au']))
                    val += float(self._rng.normal(0.0, sigma)) if sigma > 0 else 0.0
                    self.spectrum['wavelength_m'].append(lv)
                    self.spectrum['signal'].append(val)
                    self.spectrum['_covered'] = self.spectrum['_covered'] and cov
                    seg['done'] += 1
            if self._seg_t >= dur - 1e-12:
                self._seg += 1
                self._seg_t = 0.0
            if self._seg >= len(self._schedule):                   # the last segment finished inside this step
                self.phase = 'succeeded'
                self.stage_target = list(self.stage)
                self._archive()
                break
            if use <= 0.0 and dur > 0:
                break


# ------------------------------------------------------------------------------------------------------------------------------ session
class CamagTlcscannerSession:
    """Only the trusted host assigns roles; returned data obey the same boundary."""

    def __init__(self, host, principal_id):
        self._host, self._principal = host, principal_id

    @property
    def _role(self):
        return self._host._principals[self._principal]

    def _target(self, i):
        if i != self._host.instance_id:
            raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:
            self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID:
            raise ActionRejected('UNKNOWN_INSTANCE')
        m = export_camag_tlcscanner_contract_metadata()
        agent = self._role == 'agent'
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version=ASSET_VERSION,
                    capabilities={k: v for k, v in m['capabilities'].items() if v['visibility'] == 'agent' or not agent},
                    state_slots={k: v for k, v in m['state_slots'].items() if v['visibility'] == 'agent' or not agent})

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        v = h.read(self._role)
        ids = list(v) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        obs = {}
        for k in ids:
            origin = SLOTS[k][2]
            obs[k] = dict(value=v[k], unit=SLOTS[k][1], quality='unavailable' if v[k] is None else ('estimated' if origin in ('derived', 'sensor') else 'valid'),
                          sample_time_s=h.time_s)
        return dict(status='succeeded', instance_id=instance_id, state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations=obs)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        h = self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id, str) or capability_id not in CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and CAPS[capability_id][5] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id, str) or not request_id or not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            key = (self._principal, instance_id, request_id)
            digest = json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            if key in h._requests:
                old, result = h._requests[key]
                if old != digest:
                    raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(result)
            result = h.invoke(capability_id, **arguments)
            op = result.get('operation_id') if isinstance(result, dict) else None
            if op:
                h._owners[op] = self._principal
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=op,
                            status='accepted' if op else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
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
        if state_id not in SLOTS or not SLOTS[state_id][5]:
            raise ActionRejected('ACCESS_DENIED')
        cap_id, arg = SLOTS[state_id][5]
        return self.invoke(instance_id=instance_id, capability_id=cap_id, arguments={arg: value}, request_id=request_id)

    def poll(self, operation_id):
        h = self._host
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        if operation_id in h._operation_records:
            return copy.deepcopy(h._operation_records[operation_id])
        if operation_id != h.operation_id:
            raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id, status=h.phase, result=h._measure())

    def cancel(self, operation_id, request_id):
        snapshot = self.poll(operation_id)
        h = self._host
        if not isinstance(request_id, str) or not request_id:
            raise ActionRejected('INVALID_ARGUMENT')
        key = (self._principal, h.instance_id, request_id)
        digest = json.dumps(['scan.cancel', operation_id])
        if key in h._requests:
            old, response = h._requests[key]
            if old != digest:
                raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(response)
        result = h.invoke('scan.stop') if snapshot['status'] == 'running' else {'phase': snapshot['status']}
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='succeeded',
                        state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=result, observations={}, error=None)
        h._requests[key] = (digest, copy.deepcopy(response))
        return response


# --------------------------------------------------------------------------------------------------------------- contract metadata
ERRORS = ['INVALID_ARGUMENT', 'UNKNOWN_INSTANCE', 'UNKNOWN_CAPABILITY', 'UNSUPPORTED', 'ACCESS_DENIED', 'REQUEST_CONFLICT', 'UNKNOWN_REFERENCE',
          'INITIALIZATION_ONLY', 'EVIDENCE_ACK_REQUIRED', 'PLANT_NOT_CONFIGURED', 'POWER_OFF', 'BUSY', 'DOOR_OPEN', 'DOOR_CLOSED', 'STAGE_OBSTRUCTS_DOOR',
          'STAGE_NOT_AT_LOAD', 'OUTSIDE_STAGE_TRAVEL', 'NO_PLATE', 'PLATE_EXISTS', 'PLATE_GEOMETRY', 'RF_TEMPLATE_CONFLICT', 'TEMPLATE_RANGE', 'LED_FLUORESCENCE_ONLY', 'MOBILE_PHASE_FRACTIONS',
          'MOBILE_PHASE_OUTSIDE_TEMPLATE', 'NO_ABSORPTION_TEMPLATE', 'LAMP_NOT_READY', 'WAVELENGTH_OUTSIDE_LAMP_RANGE', 'EXCITATION_ABOVE_CUTOFF',
          'METHOD_NOT_CONFIGURED', 'SCAN_RANGE', 'OUTSIDE_PLATE', 'SPECTRUM_ABSORBANCE_ONLY', 'NO_SPECTRUM', 'NO_COMPLETE_TRACE', 'CALIBRATION_INPUT']
ERROR_ZH = {'DOOR_OPEN': '滑门未完全关闭（扫描需避光）', 'DOOR_CLOSED': '滑门未全开（载台不能越出舱外/不能放取板）', 'STAGE_OBSTRUCTS_DOOR': '载台在舱外，不能关门',
            'MOBILE_PHASE_OUTSIDE_TEMPLATE': '流动相不在该化合物实测的二元族及比例范围内', 'NO_ABSORPTION_TEMPLATE': '化合物无公开吸收模板且宿主未确认合成响应',
            'LAMP_NOT_READY': '光源未点亮或未达暖机时长', 'WAVELENGTH_OUTSIDE_LAMP_RANGE': '波长超出所选光源连续谱范围',
            'EXCITATION_ABOVE_CUTOFF': '荧光激发波长高于400 nm截止滤光片', 'CALIBRATION_INPUT': '有效标准点不足以拟合所选模型',
            'PLATE_EXISTS': '已有登记的板（台上或在外待放），先取下/放置', 'RF_TEMPLATE_CONFLICT': '该化合物在此流动相的重复实测Rf相差>0.2，模板不可信',
            'TEMPLATE_RANGE': '至少一种板上物质在所请求波长没有吸收数据，模拟器不能预测信号（不返回零）', 'LED_FLUORESCENCE_ONLY': '366 nm LED只用于荧光激发'}


PARAM_ZH = {'lamp_warmup_s': '光源暖机时长', 'noise_au': '基线噪声标准差（AU）', 'plate_factor': '板因子k_plate（溶液ε到板上局部吸光度的换算）',
            'saturation_au': 'Kubelka–Munk型饱和常数A_sat', 'spot_sigma0_m': '点样带初始高斯宽度σ0', 'spot_broadening_m': '斑点方差随迁移距离增长系数β',
            'band_edge_sigma_m': '点样带两端的高斯边缘宽度', 'f254_background': 'F254指示剂荧光背景（相对）', 'door_speed_m_s': '操作者滑门速度',
            'seed': '噪声随机种子', 'accept_synthetic_response': '是否允许无吸收模板物质使用合成平坦响应', 'accept_uncalibrated_model': '是否确认使用未标定仪器模型',
            'width_m': '板宽', 'height_m': '板高', 'application_y_m': '点样线位置（距板下缘）', 'front_y_m': '溶剂前沿位置（距板下缘）',
            'bandwidth_m': '单色器带宽', 'slit_length_m': '狭缝像长度', 'slit_width_m': '狭缝像宽度', 'scan_speed_m_s': '扫描速度', 'data_step_m': '数据步长',
            'scan_start_y_m': '扫描起点（板坐标Y）', 'scan_end_y_m': '扫描终点（板坐标Y）', 'wavelengths_m': '扫描波长列表', 'tracks_x_m': '样道X位置列表'}


@__import__('functools').lru_cache(maxsize=1)
def _library_counts():
    doc = json.loads((ROOT / TEMPLATE_REL).read_text(encoding='utf-8'))
    return doc['n_substances'], doc['n_with_uv'], doc['n_rf_points']


def export_camag_tlcscanner_contract_metadata():
    caps, slots = {}, {}
    n_sub, n_uv, n_pts = _library_counts()
    immediate = {
        'plant.configure': ['plant.configured'], 'plate.register': ['truth.plate'], 'plate.place': ['plate.loaded', 'plate.x_m', 'plate.y_m', 'truth.plate'],
        'plate.remove': ['plate.loaded', 'plate.x_m', 'plate.y_m', 'truth.plate'],
        'power.set': ['power.enabled', 'host.online', 'lamp.source', 'lamp.on_time_s', 'lamp.ready', 'illumination.enabled', 'stage.target_x_m',
                      'stage.target_y_m', 'scan.phase', 'safety.warning'],
        'power.toggle': ['power.enabled', 'host.online', 'lamp.source', 'lamp.on_time_s', 'lamp.ready', 'illumination.enabled', 'stage.target_x_m',
                         'stage.target_y_m', 'scan.phase', 'safety.warning'],
        'door.set': ['door.target_open'], 'stage.load': ['stage.target_x_m', 'stage.target_y_m'], 'stage.home': ['stage.target_x_m', 'stage.target_y_m'],
        'stage.jog': ['stage.target_x_m', 'stage.target_y_m'], 'illumination.set': ['illumination.enabled'], 'illumination.toggle': ['illumination.enabled'],
        'lamp.select': ['lamp.source', 'lamp.on_time_s', 'lamp.ready'], 'nitrogen.set': ['nitrogen.enabled'],
        'method.configure': ['method.configured', 'method.mode', 'method.bandwidth_m'],
        'scan.start': ['scan.phase', 'scan.operation', 'scan.elapsed_s', 'scan.completed_points', 'scan.signal', 'safety.warning', 'illumination.enabled'],
        'scan.stop': ['scan.phase', 'safety.warning', 'stage.target_x_m', 'stage.target_y_m'],
        'spectrum.start': ['scan.phase', 'scan.operation', 'scan.elapsed_s', 'scan.completed_points', 'scan.signal', 'safety.warning', 'illumination.enabled'],
        'reset': ['scan.phase', 'safety.warning', 'stage.target_x_m', 'stage.target_y_m']}
    on_step = {
        'power.set': ['lamp.on_time_s', 'lamp.ready'], 'power.toggle': ['lamp.on_time_s', 'lamp.ready'], 'door.set': ['door.position_m', 'door.closed'],
        'stage.load': ['stage.x_m', 'stage.y_m', 'stage.moving', 'stage.at_load', 'stage.inside', 'plate.x_m', 'plate.y_m'],
        'stage.home': ['stage.x_m', 'stage.y_m', 'stage.moving', 'stage.at_load', 'stage.inside', 'plate.x_m', 'plate.y_m'],
        'stage.jog': ['stage.x_m', 'stage.y_m', 'stage.moving', 'stage.at_load', 'stage.inside', 'plate.x_m', 'plate.y_m'],
        'reset': ['stage.x_m', 'stage.y_m', 'stage.moving', 'stage.at_load', 'stage.inside', 'plate.x_m', 'plate.y_m'],
        'lamp.select': ['lamp.on_time_s', 'lamp.ready'],
        'scan.start': ['stage.x_m', 'stage.y_m', 'stage.target_x_m', 'stage.target_y_m', 'plate.x_m', 'plate.y_m', 'monochromator.wavelength_m',
                       'scan.track_index', 'scan.completed_points', 'scan.elapsed_s', 'scan.signal', 'truth.plate'],
        'spectrum.start': ['stage.x_m', 'stage.y_m', 'stage.target_x_m', 'stage.target_y_m', 'plate.x_m', 'plate.y_m', 'monochromator.wavelength_m',
                           'scan.completed_points', 'scan.elapsed_s', 'scan.signal', 'truth.plate']}
    on_completion = {'scan.start': ['scan.phase'], 'spectrum.start': ['scan.phase']}
    observed = {
        'plant.configure': ['plant.configured'], 'plate.place': ['plate.loaded'], 'plate.remove': ['plate.loaded'], 'power.set': ['power.enabled'], 'power.toggle': ['power.enabled'],
        'door.set': ['door.target_open', 'door.position_m'], 'stage.load': ['stage.x_m', 'stage.y_m', 'stage.at_load'], 'stage.home': ['stage.x_m', 'stage.y_m'],
        'stage.jog': ['stage.x_m', 'stage.y_m'], 'illumination.set': ['illumination.enabled'], 'illumination.toggle': ['illumination.enabled'],
        'lamp.select': ['lamp.source', 'lamp.ready'], 'nitrogen.set': ['nitrogen.enabled'], 'method.configure': ['method.configured'],
        'scan.start': ['scan.phase'], 'scan.stop': ['scan.phase'], 'spectrum.start': ['scan.phase'], 'reset': ['scan.phase'],
        'process.measure': ['scan.phase', 'scan.operation', 'scan.track_index', 'plate.x_m', 'plate.y_m', 'scan.signal', 'scan.completed_points',
                            'scan.elapsed_s', 'monochromator.wavelength_m', 'safety.warning'],
        'results.read': ['scan.phase', 'scan.completed_points'], 'spectrum.read': ['scan.phase', 'scan.completed_points'], 'product.read': ['truth.plate']}
    desc = {'immediate': '本次通过校验的控制/配置立即改变或清除该状态；不推进仿真时间', 'on_step': '由后续宿主step推进（载台/门运动、暖机、逐点记录）',
            'on_completion': '全部样道/波长或光谱点完成后提交终态'}

    def effects_for(key):
        return [dict(state_id=s, timing=t, description_zh=desc[t]) for t, mp in (('immediate', immediate), ('on_step', on_step), ('on_completion', on_completion))
                for s in mp.get(key, [])]
    for k, (s, u, o, v, c, w) in SLOTS.items():
        slots[k] = dict(meaning_zh=SLOT_ZH[k], component_ids=c, value_schema=copy.deepcopy(s), unit=u, reference_conditions=None, origin=o, visibility=v,
                        access='read_write' if w else 'read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'CamagTlcscannerSession.read(state_ids=[{k!r}])'),
                        write_via=dict(capability_id=w[0], argument=w[1], fixed_arguments={}) if w else None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=None),
                        source_ids=['manufacturer_primary', 'simulation_assumption'] if o != 'process_truth' else ['xu2022_tlc_dataset', 'simulation_assumption'])
    pre = {
        'plate.place': '门全开、载台在放板位且静止、板已登记在外部、无运行', 'plate.remove': '门全开、载台在放板位且静止、板在台、无运行',
        'door.set': '关门时载台必须在舱内（stage.y_m≥−0.030 m）；运行中不能开门', 'stage.load': '通电、门全开、无运行', 'stage.home': '通电、无运行；越出舱外时门全开',
        'stage.jog': '通电、无运行、目标在行程内；越出舱外时门全开', 'scan.start': '通电、门关、板在台、方法已配置、光源就绪、无运行、扫描路径在舱内行程内、板上物质的吸收模板覆盖全部扫描波长、吸收模式不用LED',
        'spectrum.start': '同scan.start且为吸收模式（LED只用于荧光）、波长在光源范围内且光束处斑点物质的吸收模板覆盖所请求波长', 'quantify.fit': '最近一次扫描在该波长有完整样道；有效标准点数≥模型参数数',
        'plate.register': '装置已配置、无已登记的板（台上或待放）、流动相属于实测二元族且无冲突重复、物质有吸收模板（或宿主确认合成响应）'}
    for k, (inp, out, comps, zh, kind, vis, ex) in CAPS.items():
        unsupported = k in UNSUPPORTED
        query = kind in ('query', 'analysis')
        asynchronous = k in ('scan.start', 'spectrum.start')
        caps[k] = dict(
            function_zh=zh, kind=kind, visibility=vis, component_ids=comps, availability='unsupported' if unsupported else 'implemented',
            input_schema=copy.deepcopy(inp), output_schema=copy.deepcopy(out),
            implementation=dict(process='absent' if unsupported else 'implemented', scene='absent', robot='absent', hardware='absent',
                                host='absent' if unsupported else 'implemented'),
            binding=None if unsupported else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.camag_tlcscanner.CamagTlcscannerSession.invoke',
                                                  invocation=f'session.invoke(instance_id=..., capability_id={k!r}, arguments=..., request_id=...)'),
            preconditions=[dict(description_zh=pre[k], state_ids=[], enforced_by='runtime', check='ActionRejected before any state change')] if k in pre else [],
            state_effects=[] if (query or unsupported) else effects_for(k), observes=observed.get(k, []),
            model_effects=dict(mode='none', description_zh='不支持的选项无变化' if unsupported else '待场景求值验证后由build_contract.py更新', binding_ids=[]),
            execution=dict(mode='asynchronous' if asynchronous else 'synchronous', time_domain='simulation',
                           completion_criterion='全部样道×波长扫描完成或全部光谱点记录完成' if asynchronous else '事务返回；纯读/分析不推进时间',
                           completion_state_ids=['scan.phase'] if asynchronous else [], timeout_s=None,
                           cancellation='supported' if asynchronous else 'unsupported', retry='same_request_id',
                           resources=['host_clock', 'stage', 'optical_head', 'request_cache'],
                           partial_failure_zh='取消/断电保留已记录的点与已完成样道的峰；载台停在当前位置' if asynchronous else '拒绝时状态不变'),
            errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in ERRORS],
            example_arguments=copy.deepcopy(ex), verification=[],
            limitations=['密度峰图是实测Rf模板与溶液吸收模板经未标定仪器模型的组合，不是TLC Scanner 4实机复现；荧光只模拟F254淬灭；衍生化与汞灯不支持'],
            source_ids=['manufacturer_primary', 'xu2022_tlc_dataset', 'simulation_assumption'])
    mech = dict(
        component_ids=['housing', 'optical_head', 'stage', 'stage_carriage', 'door', 'illumination_tube', 'display_digits'], state_ids=list(slots),
        meaning_zh='实测Rf斑点定位、溶液吸收模板的带宽卷积、狭缝积分、Kubelka–Munk型饱和、噪声、峰积分、原位光谱、谱库相关与标线拟合；门/载台运动与联锁',
        implementation='lumped',
        equations=[
            'Rf: exact measured point of the compound in the same binary mobile-phase family; linear interpolation in the polar volume fraction between the two '
            'bracketing measured ratios (interpolated_measured); outside -> MOBILE_PHASE_OUTSIDE_TEMPLATE',
            'spot centre y_c = y_app + Rf (y_front - y_app); sigma_y^2 = sigma0^2 + beta (y_c - y_app); band profile along X = box(L) * Gaussian(sigma_edge)',
            'Gamma_slit = n [mol] * <p_x>_slit length * <p_y>_slit width; eps_bw(lambda) = Gaussian(FWHM = bandwidth) weighted mean of the template eps',
            'a = k_plate * sum_s eps_bw,s * Gamma_slit,s;  AU = A_sat (1 - exp(-a / A_sat)) + N(0, noise_au / sqrt(throughput(lamp, lambda)))',
            'fluorescence: S = B_F254 * 10^(-a) (only F254 layer and excitation < 300 nm); analyte emission not modelled',
            'throughput: D2 1.0 (190-300 nm) -> 0.3 (450 nm); W 0.2 (350 nm) -> 1.0 (450-800 nm) -> 0.6 (900 nm); LED 366 nm only; x0.2 below 200 nm without N2',
            'stage: positioning 0.15 m/s per axis, scan at the method speed (<= 0.1 m/s), samples every data step; monochromator 200 nm/s; spectra 100 nm/s',
            'beam on the plate (plate centred on the stage): x_p = 0.100 - q_x + W/2, y_p = 0.100 - q_y + H/2',
            'peaks: linear baseline from both scan ends, 5-point smoothing, threshold max(5 sigma_noise, 0.001 AU), trapezoid area [AU m]',
            'calibration: least squares on caller standards (linear / quadratic / Michaelis-Menten Gauss-Newton); inverse only inside the standard range',
            'scanning is non-destructive: registered mass = mass on the plate; photon exposure time is booked'],
        parameters={}, source_ids=['manufacturer_primary', 'manufacturer_brochure_2012', 'xu2022_tlc_dataset', 'nist_webbook_uv', 'pubchem_uv', 'simulation_assumption'],
        limitations=['Xu et al. Rf: silica plates of an automated platform, 300 s development; humidity, saturation, temperature and plate brand not reported.',
                     f'Solution UV spectra (NIST / HSDB) are used for reflectance on silica (derived); {n_sub - n_uv} of {n_sub} compounds have no '
                     f'absorption template; measurements at wavelengths outside a template range are refused (TEMPLATE_RANGE).',
                     'The documented A/D figure (100 ms per double conversion) is not used as a sampling limit: it contradicts 100 mm/s with 25-200 um data '
                     'steps; the runtime samples at the data step.',
                     'Scanning is modelled as non-destructive (no photodegradation or evaporation of the spots).',
                     'k_plate, A_sat, noise, spot broadening, lamp throughput and warm-up are uncalibrated assumptions; the 42 slit combinations are not public.',
                     'Native fluorescence, derivatization, legacy mercury lamp, RS-232 protocol and visionCATS reports are not implemented.'])
    spec = importlib.util.spec_from_file_location('_tlc_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH)
    ann.TRUTH_ZH.update(SLOT_ZH)
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'densitometry': mech},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'plate.register': (PLATE, PLATE_EXAMPLE), 'method.configure': (METHOD, METHOD_EXAMPLE)})
    for k, v in slots.items():
        v['meaning_zh'] = SLOT_ZH[k] + '；来源=' + v['origin'] + '，不代表实机标定。'
    mech['parameters'] = {k.lower(): v for k, v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'densitometry': mech})
