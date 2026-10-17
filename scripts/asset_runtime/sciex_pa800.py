"""CHEM-093 SCIEX PA 800 Plus capillary electrophoresis system: runtime (process model, session, contract metadata).

Documented (PA 800 Plus brochure p.11; PA 800 Plus Maintenance Guide RUO-IDV-05-5519-D): separation voltage 1-30 kV in 0.1 kV steps with
normal / reverse polarity (HV+ / HV- indicators), current 3-300 uA in 0.1 uA steps, pressure -5 to +100 psi, sample storage 4-60 C,
capillary (coolant) 15-60 C, UV filters 200/214/254/280 nm, PDA 190-600 nm at 0.5-32 Hz, buffer trays 2 x 36 (6 x 6) and sample trays
2 x 48 (6 x 8) universal vials (max 1.5 mL, waste 1.0 mL), capillary length to detector + 10.2 cm = total length, sample cover (outer door)
and cartridge cover (inner door), insertion bar clamping the cartridge on the interface block, PWR / UV / HV+ / HV- LEDs.

Process model (SI): background electrolyte (BGE) speciation from charge balance with the real limiting mobilities, pKa and ionisation
enthalpies of assets/reactions/sciex_pa800_mobility_library (PHREEQC / Hirokawa-STEEP data), water-viscosity temperature factor, empirical
ionic-strength factor, EOF of bare fused silica (engineering sigmoid), Joule heating through the capillary wall, current limit, Hagen-Poiseuille
pressure injection / rinse, electrokinetic injection, Gaussian zones migrating with (mu_eff + mu_eof) E + v_hydrodynamic and dispersing by
diffusion (Nernst-Einstein D) + Taylor-Aris, UV detection with NIST log-epsilon spectra (direct) or Kohlrausch displacement (indirect).
Every mole of every species is booked in vials, capillary fill, zones or removed vials (conservation ledger).

Host contract: step(dt_s) is the only call that advances physics; read()/describe()/electropherogram.read are pure; setters write targets
only; rejected calls change nothing.  Matterix RECIPES: module 'asset_runtime.sciex_pa800', cls 'SciexPa800Runtime', instance_in_ctor.
"""
from __future__ import annotations

import copy
import json
import math
import random
import re
import zlib
from pathlib import Path

from asset_runtime import _annotations as _ann

PROTOCOL_VERSION = '1.0.0'
ASSET_TYPE_ID = 'instrument.sciex_pa800'
ASSET_VERSION = '1.0.0'
CATALOG_ID = 'CHEM-093'
ROOT = Path(__file__).resolve().parents[2]
LIBRARY_REL = 'assets/reactions/sciex_pa800_mobility_library/templates.json'
UV_REL = 'assets/reactions/spectral_library/uv_vis.json'

F, R, KB, QE, T25 = 96485.33212, 8.314462618, 1.380649e-23, 1.602176634e-19, 298.15
PSI = 6894.757293168
V_MIN, V_MAX, V_STEP = 1000.0, 30000.0, 100.0                     # brochure: 1-30 kV programmable in 0.1 kV increments
I_MIN, I_MAX, I_STEP = 3e-6, 300e-6, 1e-7                          # brochure: 3-300 uA in 0.1 uA increments
P_MIN, P_MAX = -5 * PSI, 100 * PSI                                 # brochure: -5 to +100 psi
CAP_T = (288.15, 333.15)                                           # capillary temperature control 15-60 C
SAMPLE_T = (277.15, 333.15)                                        # sample temperature control 4-60 C
UV_FILTERS_M = (200e-9, 214e-9, 254e-9, 280e-9)                    # standard UV filters
PDA_RANGE_M = (190e-9, 600e-9)
RATE_RANGE_HZ = (0.5, 32.0)                                        # PDA scan collection frequency
VIAL_MAX_M3 = 1.5e-6                                               # maintenance guide: never more than 1.5 mL
L_OFFSET_M = 0.102                                                 # maintenance guide table 1-3: total = length to detector + 10.2 cm
ID_CHOICES_M = (2.0e-5, 5.0e-5, 7.5e-5, 1.0e-4)
COATINGS = ('bare_fused_silica', 'neutral', 'n_cho')
SAMPLE_COVER_OPEN, CARTRIDGE_COVER_OPEN, INSERTION_BAR_UP = math.pi / 2, 1.4, 1.0
TRAYS = {'BI': ('buffer_tray_inlet', 6, 6, 'inlet'), 'BO': ('buffer_tray_outlet', 6, 6, 'outlet'),
         'SI': ('sample_tray_inlet', 6, 8, 'inlet'), 'SO': ('sample_tray_outlet', 6, 8, 'outlet')}
NEUTRAL_MARKERS = ('dmso', 'benzyl_alcohol', 'acetophenone', 'nitrobenzene', 'caffeine', 'methanol', 'ethanol', 'urea')

# engineering assumptions (mechanism parameters, evidence=assumption)
PLANT = dict(eof_reference_m2_v_s=7.0e-8, eof_silanol_pk=5.0, eof_low_ph_fraction=0.05, eof_reference_ionic_strength_mol_m3=20.0, eof_ionic_exponent=0.25,
             ionic_strength_coefficient=0.77, coolant_h_w_m2_k=1000.0, silica_k_w_m_k=1.4, polyimide_k_w_m_k=0.155,
             silica_outer_diameter_m=3.40e-4, capillary_outer_diameter_m=3.65e-4, detector_window_m=2.0e-4, baseline_noise_au=2.0e-5,
             neutral_diffusion_m2_s=1.0e-9, capillary_tau_s=60.0, sample_tau_s=600.0, lamp_warmup_s=300.0,
             buffer_min_volume_m3=0.5e-6, sample_min_volume_m3=5.0e-9, ambient_k=295.15)

# ------------------------------------------------------------------------------------------------------------------------ schemas
def number(unit, lo=None, hi=None):
    d = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        d['minimum'] = lo
    if hi is not None:
        d['maximum'] = hi
    return d


def integer(lo=None, hi=None):
    d = {'type': 'integer', 'x-unit': '1'}
    if lo is not None:
        d['minimum'] = lo
    if hi is not None:
        d['maximum'] = hi
    return d


def obj(props, required=None):
    return dict(type='object', properties=props, required=list(props) if required is None else list(required), additionalProperties=False)


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


BOOL, TEXT = {'type': 'boolean'}, {'type': 'string', 'maxLength': 200}
POS_INLET = {'type': 'string', 'pattern': '^(BI|SI):[A-F][1-8]$'}
POS_OUTLET = {'type': 'string', 'pattern': '^(BO|SO):[A-F][1-8]$'}
POS_ANY = {'type': 'string', 'pattern': '^(BI|BO|SI|SO):[A-F][1-8]$'}
PHASE = {'type': 'string', 'enum': ['idle', 'running', 'succeeded', 'cancelled', 'failed']}
STAGE = {'type': 'string', 'enum': ['idle', 'rinse', 'inject', 'separate', 'wait', 'done']}
STEP = {'type': 'object', 'additionalProperties': False, 'required': ['type', 'duration_s'], 'properties': {
    'type': {'type': 'string', 'enum': ['rinse', 'inject', 'separate', 'wait']},
    'duration_s': number('s', 0.1, 36000.0),
    'inlet': POS_INLET, 'outlet': POS_OUTLET,
    'injection_mode': {'type': 'string', 'enum': ['pressure', 'voltage']},
    'pressure_pa': number('Pa', P_MIN, P_MAX),
    'voltage_v': number('V', -V_MAX, V_MAX),
    'control': {'type': 'string', 'enum': ['voltage', 'current']},
    'current_a': number('A', I_MIN, I_MAX),
    'current_limit_a': number('A', I_MIN, I_MAX),
    'ramp_s': number('s', 0.0, 120.0)}}
PEAK = obj({'migration_time_s': number('s', 0), 'height_au': number('1'), 'area_au_s': number('1*s'), 'corrected_area_au': number('1'),
            'area_percent': number('1', 0, 100), 'width_half_s': number('s', 0)})
RESULT = obj({'phase': PHASE, 'available': BOOL, 'detection': {'type': 'string', 'enum': ['direct_uv', 'indirect_uv', 'none']},
              'wavelength_m': nullable(number('m', 0)), 'data_rate_hz': nullable(number('Hz', 0)),
              'time_s': {'type': 'array', 'items': number('s', 0), 'maxItems': 2000000},
              'absorbance_au': {'type': 'array', 'items': number('1'), 'maxItems': 2000000},
              'current_trace_a': {'type': 'array', 'items': number('A'), 'maxItems': 200000},
              'peaks': {'type': 'array', 'items': PEAK, 'maxItems': 500},
              'separation_voltage_v': nullable(number('V')), 'mean_current_a': nullable(number('A', 0)),
              'template_source': TEXT, 'evidence': obj({'mobility': TEXT, 'uv_absorptivity': TEXT, 'eof': TEXT, 'noise': TEXT})})
VIAL_INFO = {'type': 'object', 'additionalProperties': obj({'label': TEXT, 'volume_m3': number('m^3', 0)})}

# capability: (input, output, components, meaning_zh, kind, visibility, example, effects)
CAPS = {
    'power.set': (obj({'enabled': BOOL}), obj({'enabled': BOOL}), ['power_switch', 'led_power'],
                  '主电源设定；断电时高压与运行立即停止、氘灯熄灭并保留已采集数据', 'device_command', 'agent', {'enabled': True},
                  ['power.enabled', 'cycle.phase', 'cycle.fault', 'lamp.on', 'led.power', 'led.uv', 'hv.voltage_v', 'hv.current_a']),
    'power.toggle': (obj({}), obj({'enabled': BOOL}), ['power_switch', 'led_power'], '电源翘板开关按一下（切换通断；断电效果同power.set）', 'device_command', 'agent', {},
                     ['power.enabled', 'cycle.phase', 'cycle.fault', 'lamp.on', 'led.power', 'led.uv', 'hv.voltage_v', 'hv.current_a']),
    'sample_cover.set_position': (obj({'angle_rad': number('rad', 0.0, SAMPLE_COVER_OPEN)}), obj({'angle_rad': number('rad', 0.0, SAMPLE_COVER_OPEN)}),
                                  ['sample_cover', 'door_window'], '样品盖（外门）开合；高压或运行中打开触发联锁停机', 'device_command', 'agent',
                                  {'angle_rad': SAMPLE_COVER_OPEN}, ['sample_cover.angle_rad', 'cycle.phase', 'cycle.fault']),
    'cartridge_cover.set_position': (obj({'angle_rad': number('rad', 0.0, CARTRIDGE_COVER_OPEN)}), obj({'angle_rad': number('rad', 0.0, CARTRIDGE_COVER_OPEN)}),
                                     ['cartridge_cover'], '卡盒盖（内门）开合；须先打开样品盖', 'device_command', 'agent', {'angle_rad': CARTRIDGE_COVER_OPEN},
                                     ['cartridge_cover.angle_rad', 'cycle.phase', 'cycle.fault']),
    'insertion_bar.set_position': (obj({'angle_rad': number('rad', 0.0, INSERTION_BAR_UP)}), obj({'angle_rad': number('rad', 0.0, INSERTION_BAR_UP)}),
                                   ['insertion_bar', 'interface_block'], '抬起/压下插入杆（卡盒夹紧）；两道门都打开时才可操作', 'device_command', 'agent',
                                   {'angle_rad': INSERTION_BAR_UP}, ['insertion_bar.angle_rad']),
    'cartridge.install': (obj({'inner_diameter_m': {'type': 'number', 'x-unit': 'm', 'enum': list(ID_CHOICES_M)},
                               'length_to_detector_m': number('m', 0.2, 1.0), 'coating': {'type': 'string', 'enum': list(COATINGS)}}),
                          obj({'installed': BOOL, 'total_length_m': number('m', 0)}), ['capillary_cartridge', 'interface_block'],
                          '在接口块上装入毛细管卡盒（总长=到检测器长度+10.2 cm）；新毛细管为干管，需先冲洗', 'configuration', 'agent',
                          {'inner_diameter_m': 5.0e-5, 'length_to_detector_m': 0.5, 'coating': 'bare_fused_silica'},
                          ['cartridge.installed', 'cartridge.inner_diameter_m', 'cartridge.length_to_detector_m', 'cartridge.total_length_m', 'cartridge.coating']),
    'cartridge.remove': (obj({}), obj({'installed': BOOL}), ['capillary_cartridge', 'interface_block'], '取下卡盒；管内液体与未出峰区带随卡盒移出并记账',
                         'configuration', 'agent', {}, ['cartridge.installed']),
    'capillary.set_temperature': (obj({'temperature_k': number('K', *CAP_T)}), obj({'temperature_k': number('K', *CAP_T)}), ['capillary_cartridge'],
                                  '毛细管冷却液温度目标（15–60 °C）；实际温度随step一阶趋近', 'device_command', 'agent', {'temperature_k': 298.15},
                                  ['capillary.target_k']),
    'sample_storage.set_temperature': (obj({'temperature_k': number('K', *SAMPLE_T)}), obj({'temperature_k': number('K', *SAMPLE_T)}),
                                       ['sample_tray_inlet', 'sample_tray_outlet'], '样品存储温度目标（4–60 °C）', 'device_command', 'agent',
                                       {'temperature_k': 283.15}, ['sample_storage.target_k']),
    'lamp.set': (obj({'on': BOOL}), obj({'on': BOOL}), ['uv_detector', 'led_uv'], '紫外/PDA检测器氘灯开关；点亮后需暖机；运行中尚有分离步时关灯则中止（LAMP_OFF）',
                 'device_command', 'agent', {'on': True}, ['lamp.on', 'lamp.ready', 'led.uv', 'cycle.phase', 'cycle.fault']),
    'detector.configure': (obj({'module': {'type': 'string', 'enum': ['uv_filter', 'pda']}, 'mode': {'type': 'string', 'enum': ['direct_uv', 'indirect_uv']},
                                'wavelength_m': number('m', *PDA_RANGE_M), 'data_rate_hz': number('Hz', *RATE_RANGE_HZ)}),
                           obj({'module': TEXT, 'mode': TEXT, 'wavelength_m': number('m', 0), 'data_rate_hz': number('Hz', 0)}),
                           ['uv_detector', 'fiber_optic_cable'], '检测模式（直接/间接紫外）、波长（滤光片200/214/254/280 nm或PDA 190–600 nm）与采集频率',
                           'configuration', 'agent', {'module': 'pda', 'mode': 'direct_uv', 'wavelength_m': 214e-9, 'data_rate_hz': 4.0},
                           ['detector.module', 'detector.mode', 'detector.wavelength_m', 'detector.data_rate_hz']),
    'method.configure': (obj({'steps': {'type': 'array', 'items': STEP, 'minItems': 1, 'maxItems': 20}}), obj({'configured': BOOL, 'step_count': integer(0)}),
                         ['interface_block', 'electrode_inlet', 'electrode_outlet'], '方法程序：冲洗（压力）、进样（压力/电动）、分离（恒压/恒流、斜坡、电流上限）、等待',
                         'configuration', 'agent',
                         {'steps': [{'type': 'rinse', 'inlet': 'BI:A1', 'outlet': 'BO:A1', 'pressure_pa': 20 * PSI, 'duration_s': 120.0},
                                    {'type': 'inject', 'injection_mode': 'pressure', 'inlet': 'SI:A1', 'outlet': 'BO:B1', 'pressure_pa': 0.5 * PSI, 'duration_s': 5.0},
                                    {'type': 'separate', 'inlet': 'BI:B1', 'outlet': 'BO:B1', 'control': 'voltage', 'voltage_v': 25000.0,
                                     'current_limit_a': 3e-4, 'ramp_s': 10.0, 'duration_s': 600.0}]},
                         ['method.configured', 'method.step_count']),
    'run.start': (obj({}), obj({'phase': PHASE, 'operation_id': TEXT}), ['interface_block', 'capillary_cartridge', 'uv_detector', 'electrode_inlet',
                                                                          'electrode_outlet', 'led_hv_positive', 'led_hv_negative'],
                  '启动方法；由宿主step推进各步并采集电泳图', 'device_command', 'agent', {}, ['cycle.phase', 'cycle.stage', 'cycle.step_index']),
    'run.stop': (obj({}), obj({'phase': PHASE}), ['interface_block', 'led_hv_positive', 'led_hv_negative'], '中止运行，高压立即关断，保留已采集数据与已迁移物质',
                 'device_command', 'agent', {}, ['cycle.phase', 'cycle.stage', 'cycle.fault', 'hv.voltage_v', 'hv.current_a', 'pressure.applied_pa',
                                                 'led.hv_positive', 'led.hv_negative']),
    'electropherogram.read': (obj({}), RESULT, ['uv_detector'], '读取电泳图（时间-吸光度）、电流曲线与自动积分峰表（迁移时间、峰面积、校正面积、面积百分比）；纯读取',
                              'query', 'agent', {}, []),
    'vial.place': (obj({'position': POS_ANY, 'volume_m3': number('m^3', 1e-9, VIAL_MAX_M3),
                        'composition_mol_m3': {'type': 'object', 'additionalProperties': number('mol/m^3', 0.0, 2000.0), 'maxProperties': 40},
                        'label': TEXT}),
                   obj({'position': POS_ANY, 'placed': BOOL}), ['buffer_tray_inlet', 'buffer_tray_outlet', 'sample_tray_inlet', 'sample_tray_outlet'],
                   '受信任宿主在托盘位放入样品瓶并登记组成（非机器人配液）；需样品盖打开', 'configuration', 'evaluator',
                   {'position': 'SI:A1', 'volume_m3': 1.0e-6, 'composition_mol_m3': {'benzoic_acid': 0.5}, 'label': 'sample 1'},
                   ['trays.vials', 'truth.vial_contents_mol']),
    'vial.remove': (obj({'position': POS_ANY}), obj({'position': POS_ANY, 'volume_m3': number('m^3', 0),
                                                     'contents_mol': {'type': 'object', 'additionalProperties': number('mol', 0)}}),
                    ['buffer_tray_inlet', 'buffer_tray_outlet', 'sample_tray_inlet', 'sample_tray_outlet'], '受信任宿主取走样品瓶，返回瓶内物质（记入移出账）',
                    'configuration', 'evaluator', {'position': 'SI:A1'}, ['trays.vials', 'truth.vial_contents_mol']),
    'lif.configure': (obj({'excitation_m': number('m', 3.0e-7, 7.0e-7), 'emission_m': number('m', 3.5e-7, 7.5e-7)}), obj({'configured': BOOL}),
                      ['uv_detector'], '激光诱导荧光检测（488 nm）：无荧光模板，未实现', 'configuration', 'agent',
                      {'excitation_m': 4.88e-7, 'emission_m': 5.2e-7}, []),
}
UNSUPPORTED = {'lif.configure'}

# slot: (schema, unit, origin, visibility, components, write (capability, argument) or None, meaning_zh)
SLOTS = {
    'power.enabled': (BOOL, '1', 'setpoint', 'agent', ['power_switch'], ('power.set', 'enabled'), None),
    'sample_cover.angle_rad': (number('rad', 0.0, SAMPLE_COVER_OPEN), 'rad', 'scene', 'agent', ['sample_cover'], ('sample_cover.set_position', 'angle_rad'),
                               '样品盖（外门）开启角，0=关闭，π/2=全开（开门总高98.6 cm）'),
    'cartridge_cover.angle_rad': (number('rad', 0.0, CARTRIDGE_COVER_OPEN), 'rad', 'scene', 'agent', ['cartridge_cover'],
                                  ('cartridge_cover.set_position', 'angle_rad'), '卡盒盖（内门）开启角，0=关闭'),
    'insertion_bar.angle_rad': (number('rad', 0.0, INSERTION_BAR_UP), 'rad', 'scene', 'agent', ['insertion_bar'], ('insertion_bar.set_position', 'angle_rad'),
                                '插入杆抬起角，0=压下夹紧卡盒'),
    'cartridge.installed': (BOOL, '1', 'derived', 'agent', ['capillary_cartridge'], None, '接口块上是否装有毛细管卡盒'),
    'cartridge.inner_diameter_m': (nullable(number('m', 0)), 'm', 'derived', 'agent', ['capillary_cartridge'], None, '毛细管内径（未装卡盒为null）'),
    'cartridge.length_to_detector_m': (nullable(number('m', 0)), 'm', 'derived', 'agent', ['capillary_cartridge'], None, '进样端到检测窗的有效长度'),
    'cartridge.total_length_m': (nullable(number('m', 0)), 'm', 'derived', 'agent', ['capillary_cartridge'], None, '毛细管总长=有效长度+10.2 cm（维护手册表1-3）'),
    'cartridge.coating': (nullable({'type': 'string', 'enum': list(COATINGS)}), '1', 'derived', 'agent', ['capillary_cartridge'], None,
                          '内壁：裸熔融石英（有电渗流）或中性/N-CHO涂层（电渗流被抑制）'),
    'truth.capillary_liquid_fraction': (number('1', 0, 1), '1', 'process_truth', 'evaluator', ['capillary_cartridge'], None,
                                        '毛细管被液体充满的比例（新管为0，冲洗满一个管体积后为1；真机无此读数）'),
    'capillary.target_k': (number('K', *CAP_T), 'K', 'setpoint', 'agent', ['capillary_cartridge'], ('capillary.set_temperature', 'temperature_k'),
                           '毛细管冷却液温度目标'),
    'capillary.temperature_k': (number('K', 0), 'K', 'sensor', 'agent', ['capillary_cartridge'], None, '冷却液温度读数（0.1 K量化）'),
    'sample_storage.target_k': (number('K', *SAMPLE_T), 'K', 'setpoint', 'agent', ['sample_tray_inlet', 'sample_tray_outlet'],
                                ('sample_storage.set_temperature', 'temperature_k'), '样品托盘温控目标'),
    'sample_storage.temperature_k': (number('K', 0), 'K', 'sensor', 'agent', ['sample_tray_inlet', 'sample_tray_outlet'], None, '样品托盘温度读数（0.1 K量化）'),
    'lamp.on': (BOOL, '1', 'setpoint', 'agent', ['uv_detector'], ('lamp.set', 'on'), '氘灯开关设定'),
    'lamp.ready': (BOOL, '1', 'derived', 'agent', ['uv_detector', 'led_uv'], None, '氘灯已点亮且暖机时间达到（工程阈值300 s）'),
    'detector.module': ({'type': 'string', 'enum': ['uv_filter', 'pda']}, '1', 'setpoint', 'agent', ['uv_detector'], None, '检测模块：滤光片紫外或二极管阵列'),
    'detector.mode': ({'type': 'string', 'enum': ['direct_uv', 'indirect_uv']}, '1', 'setpoint', 'agent', ['uv_detector'], None, '直接紫外或间接紫外检测'),
    'detector.wavelength_m': (number('m', *PDA_RANGE_M), 'm', 'setpoint', 'agent', ['uv_detector'], None, '检测波长'),
    'detector.data_rate_hz': (number('Hz', *RATE_RANGE_HZ), 'Hz', 'setpoint', 'agent', ['uv_detector'], None, '数据采集频率'),
    'method.configured': (BOOL, '1', 'derived', 'agent', ['interface_block'], None, None),
    'method.step_count': (integer(0, 20), '1', 'derived', 'agent', ['interface_block'], None, '方法程序步数'),
    'cycle.phase': (PHASE, '1', 'derived', 'agent', ['interface_block'], None, None),
    'cycle.stage': (STAGE, '1', 'derived', 'agent', ['interface_block'], None, '当前方法步类型'),
    'cycle.step_index': (integer(-1, 20), '1', 'derived', 'agent', ['interface_block'], None, '当前方法步序号（未运行为-1）'),
    'cycle.elapsed_s': (number('s', 0), 's', 'derived', 'agent', ['interface_block'], None, None),
    'cycle.fault': (TEXT, '1', 'derived', 'agent', ['interface_block'], None, '最近一次中止/故障原因（DOOR_INTERLOCK、CURRENT_FAULT、CAPILLARY_DRY、VIAL_OVERFLOW、VIAL_EMPTY、VIAL_MISSING、POWER_LOSS、LAMP_OFF、USER_STOP），空串为无'),
    'hv.voltage_v': (number('V', -V_MAX, V_MAX), 'V', 'sensor', 'agent', ['interface_block', 'electrode_inlet'], None, '实际施加的分离电压读数（带极性，正=进样端为正极）'),
    'hv.current_a': (number('A', 0), 'A', 'sensor', 'agent', ['interface_block', 'electrode_inlet'], None, '分离电流读数（0.1 µA量化）'),
    'pressure.applied_pa': (number('Pa', P_MIN, P_MAX), 'Pa', 'sensor', 'agent', ['interface_block'], None, '当前施加的压力（表压，正=由进样端推向出口端）'),
    'led.power': (BOOL, '1', 'derived', 'agent', ['led_power'], None, '面板PWR指示灯'),
    'led.uv': (BOOL, '1', 'derived', 'agent', ['led_uv'], None, '面板UV指示灯（氘灯点亮）'),
    'led.hv_positive': (BOOL, '1', 'derived', 'agent', ['led_hv_positive'], None, '面板HV+指示灯（正极性高压施加中）'),
    'led.hv_negative': (BOOL, '1', 'derived', 'agent', ['led_hv_negative'], None, '面板HV−指示灯（反极性高压施加中）'),
    'trays.vials': (VIAL_INFO, '1', 'derived', 'agent', ['buffer_tray_inlet', 'buffer_tray_outlet', 'sample_tray_inlet', 'sample_tray_outlet'], None,
                    '各托盘位上的样品瓶标签与液体体积（肉眼可见量，不含组成）'),
    'result.available': (BOOL, '1', 'derived', 'agent', ['uv_detector'], None, '是否已有可读取的电泳图'),
    'truth.bge_ph': (nullable(number('1', 0, 14)), '1', 'process_truth', 'evaluator', ['capillary_cartridge'], None, '管内背景电解质由电荷平衡求得的pH'),
    'truth.bge_conductivity_s_m': (nullable(number('S/m', 0)), 'S/m', 'process_truth', 'evaluator', ['capillary_cartridge'], None, '管内背景电解质电导率（当前缓冲温度）'),
    'truth.bge_ionic_strength_mol_m3': (nullable(number('mol/m^3', 0)), 'mol/m^3', 'process_truth', 'evaluator', ['capillary_cartridge'], None, '背景电解质离子强度'),
    'truth.eof_mobility_m2_v_s': (nullable(number('m^2/(V*s)')), 'm^2/(V*s)', 'process_truth', 'evaluator', ['capillary_cartridge'], None, '电渗淌度（工程模型）'),
    'truth.buffer_temperature_k': (number('K', 0), 'K', 'process_truth', 'evaluator', ['capillary_cartridge'], None, '管内缓冲液平均温度（冷却液温度+焦耳热温升）'),
    'truth.capillary_fill_mol_m3': ({'type': 'object', 'additionalProperties': number('mol/m^3', 0)}, 'mol/m^3', 'process_truth', 'evaluator',
                                    ['capillary_cartridge'], None, '管内液体（背景电解质）组成'),
    'truth.zones': ({'type': 'array', 'items': {'type': 'object'}}, '1', 'process_truth', 'evaluator', ['capillary_cartridge'], None,
                    '管内样品区带：物质、摩尔数、中心位置与方差'),
    'truth.vial_contents_mol': ({'type': 'object', 'additionalProperties': {'type': 'object'}}, 'mol', 'process_truth', 'evaluator',
                                ['buffer_tray_inlet', 'buffer_tray_outlet', 'sample_tray_inlet', 'sample_tray_outlet'], None, '各瓶内各物质摩尔数'),
    'truth.species_ledger_mol': ({'type': 'object', 'additionalProperties': {'type': 'object'}}, 'mol', 'process_truth', 'evaluator', ['capillary_cartridge'],
                                 None, '逐物质守恒账：放入、瓶内、管内液、区带、已移出'),
    'truth.peak_identities': ({'type': 'array', 'items': {'type': 'object'}}, '1', 'process_truth', 'evaluator', ['uv_detector'], None,
                              '本次运行各物质的理论迁移时间、有效淌度与检测响应（评测用）'),
    'truth.charge_passed_c': (number('C', 0), 'C', 'process_truth', 'evaluator', ['electrode_inlet', 'electrode_outlet'], None, '本次分离累计通过电量'),
    'truth.joule_energy_j': (number('J', 0), 'J', 'process_truth', 'evaluator', ['capillary_cartridge'], None, '本次分离累计焦耳热'),
}


# ------------------------------------------------------------------------------------------------------------------------ validation
class ActionRejected(ValueError):
    """Stable error codes; never disclose sample composition."""


def _type_ok(t, v):
    return {'number': isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v),
            'integer': isinstance(v, int) and not isinstance(v, bool), 'boolean': type(v) is bool, 'string': isinstance(v, str),
            'object': isinstance(v, dict), 'array': isinstance(v, list), 'null': v is None}[t]


def _check(schema, value):
    if 'anyOf' in schema:
        return any(_check(s, value) for s in schema['anyOf'])
    t = schema.get('type')
    if t is not None and not any(_type_ok(x, value) for x in ([t] if isinstance(t, str) else t)):
        return False
    if 'enum' in schema and not any(value == e and type(value) is type(e) or (isinstance(e, float) and isinstance(value, (int, float))
                                                                              and not isinstance(value, bool) and value == e) for e in schema['enum']):
        return False
    if 'const' in schema and value != schema['const']:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            return False
    if isinstance(value, str):
        if len(value) > schema.get('maxLength', 10 ** 9) or ('pattern' in schema and not re.search(schema['pattern'], value)):
            return False
    if isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10 ** 9):
            return False
        if 'items' in schema and not all(_check(schema['items'], x) for x in value):
            return False
    if isinstance(value, dict):
        if len(value) > schema.get('maxProperties', 10 ** 9) or not set(schema.get('required', [])) <= set(value):
            return False
        props = schema.get('properties', {})
        for k, v in value.items():
            if not isinstance(k, str):
                return False
            sub = props.get(k, schema.get('additionalProperties', True))
            if sub is False or (isinstance(sub, dict) and not _check(sub, v)):
                return False
    return True


def validate(schema, value):
    if not _check(schema, value):
        raise ActionRejected('INVALID_ARGUMENT')


# ------------------------------------------------------------------------------------------------------------------------ chemistry
_LIB_CACHE = {}


def library():
    if 'lib' not in _LIB_CACHE:
        lib = json.loads((ROOT / LIBRARY_REL).read_text(encoding='utf-8'))
        uv = json.loads((ROOT / UV_REL).read_text(encoding='utf-8'))['compounds']
        _LIB_CACHE['lib'] = lib
        _LIB_CACHE['uv'] = uv
    return _LIB_CACHE['lib'], _LIB_CACHE['uv']


REFERENCE_ONLY = {'hydrogen_ion', 'hydroxide', 'bicarbonate', 'carbonate', 'hydrogen_sulfate', 'phosphate', 'hydrogen_phosphate', 'dihydrogen_phosphate',
                  'hydrosulfide', 'sulfide', 'ammonium'}   # protonatable single ions: their acid / base constituents carry the equilibria


def known_species(sid):
    lib, _ = library()
    return (sid in lib['records'] and sid not in REFERENCE_ONLY) or sid in NEUTRAL_MARKERS


def water_viscosity(T):
    """Water viscosity (Pa s), Vogel-type correlation A 10^(B/(T-C)), A=2.414e-5 Pa s, B=247.8 K, C=140 K."""
    return 2.414e-5 * 10 ** (247.8 / (T - 140.0))


def pkw(T):
    """Water ion product, Harned-Owen correlation pKw = 4470.99/T - 6.0875 + 0.01706 T."""
    return 4470.99 / T - 6.0875 + 0.01706 * T


def forms(sid):
    """[(charge, mobility25 signed, pKa25 or None, enthalpy or None)] sorted by charge; [] for a neutral marker."""
    if sid in NEUTRAL_MARKERS:
        return []
    rec = library()[0]['records'][sid]
    enth = rec.get('enthalpy_J_mol') or [None] * len(rec['ionic_forms'])
    return [(f['charge'], f['mobility_m2_V_s'], f['pKa'], h) for f, h in zip(rec['ionic_forms'], enth)]


def fractions(sid, pH, T):
    """Distribution over ionic states (charge -> fraction, neutral state included as 0) at pH, temperature T (van 't Hoff pKa)."""
    fs = forms(sid)
    if not fs:
        return {0: 1.0}
    if all(f[2] is None for f in fs):                       # strong ion: one state
        return {fs[0][0]: 1.0}

    def pka(f):
        p = f[2]
        return p + (f[3] / (R * math.log(10)) * (1.0 / T - 1.0 / T25) if f[3] else 0.0)
    logs = {0: 0.0}
    pos = sorted([f for f in fs if f[0] > 0], key=lambda f: f[0])
    neg = sorted([f for f in fs if f[0] < 0], key=lambda f: -f[0])
    acc = 0.0
    for f in pos:                                           # state z <-> z-1 + H+ : log([z]/[z-1]) = pKa_z - pH
        acc += pka(f) - pH
        logs[f[0]] = acc
    acc = 0.0
    for f in neg:                                           # state z+1 <-> z + H+ : log([z]/[z+1]) = pH - pKa_z
        acc += pH - pka(f)
        logs[f[0]] = acc
    m = max(logs.values())
    w = {z: 10 ** (v - m) for z, v in logs.items()}
    s = sum(w.values())
    return {z: v / s for z, v in w.items()}


def mobility_factor(z, ionic_mol_l, T, coeff):
    """Empirical ionic-strength factor exp(-coeff sqrt(|z| I)) (I in mol/L) times the water-viscosity temperature factor."""
    return math.exp(-coeff * math.sqrt(abs(z) * max(ionic_mol_l, 0.0))) * water_viscosity(T25) / water_viscosity(T)


_BGE_CACHE = {}


def solve_bge(comp, T, coeff=PLANT['ionic_strength_coefficient']):
    """Charge balance -> pH, ionic strength (mol/m^3), conductivity (S/m) of a solution {species: mol/m^3} (memoised, pure)."""
    key = (tuple(sorted((k, round(v, 12)) for k, v in comp.items())), round(T, 4), coeff)
    hit = _BGE_CACHE.get(key)
    if hit is not None:
        return copy.deepcopy(hit)
    if len(_BGE_CACHE) > 4096:
        _BGE_CACHE.clear()
    out = _solve_bge(comp, round(T, 4), coeff)
    _BGE_CACHE[key] = out
    return copy.deepcopy(out)


def _solve_bge(comp, T, coeff):
    lib = library()[0]['records']
    mu_h, mu_oh = lib['hydrogen_ion']['ionic_forms'][0]['mobility_m2_V_s'], lib['hydroxide']['ionic_forms'][0]['mobility_m2_V_s']
    kw = 10 ** (-pkw(T))
    sp = {k: c for k, c in comp.items() if c > 0 and k not in NEUTRAL_MARKERS}

    def balance(pH):
        h = 10 ** (-pH) * 1000.0                             # mol/m^3
        q = h - kw / 10 ** (-pH) * 1000.0
        for k, c in sp.items():
            q += c * sum(z * a for z, a in fractions(k, pH, T).items())
        return q
    lo, hi = -1.0, 15.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if balance(mid) > 0:
            lo = mid
        else:
            hi = mid
    pH = 0.5 * (lo + hi)
    h, oh = 10 ** (-pH) * 1000.0, kw / 10 ** (-pH) * 1000.0
    ionic = 0.5 * (h + oh)
    fr = {k: fractions(k, pH, T) for k in sp}
    for k, c in sp.items():
        ionic += 0.5 * c * sum(z * z * a for z, a in fr[k].items())
    I_l = ionic / 1000.0
    kappa = F * (h * abs(mu_h) * mobility_factor(1, I_l, T, coeff) + oh * abs(mu_oh) * mobility_factor(1, I_l, T, coeff))
    for k, c in sp.items():
        mus = {f[0]: f[1] for f in forms(k)}
        kappa += F * c * sum(a * abs(mus[z]) * mobility_factor(z, I_l, T, coeff) for z, a in fr[k].items() if z != 0)
    return dict(pH=pH, ionic_mol_m3=ionic, conductivity_s_m=kappa, fractions=fr)


def effective_mobility(sid, pH, ionic_mol_m3, T, coeff=PLANT['ionic_strength_coefficient']):
    """(mu_eff signed, mean charge, D) of a species in a BGE of given pH / ionic strength at T."""
    fs = forms(sid)
    fr = fractions(sid, pH, T)
    mus = {f[0]: f[1] for f in fs}
    I_l = ionic_mol_m3 / 1000.0
    mu = sum(a * mus[z] * mobility_factor(z, I_l, T, coeff) for z, a in fr.items() if z != 0)
    zbar = sum(a * z for z, a in fr.items())
    d_forms = [abs(mus[z]) * mobility_factor(z, I_l, T, coeff) * KB * T / (abs(z) * QE) for z in mus]
    d_ion = sum(d_forms) / len(d_forms) if d_forms else PLANT['neutral_diffusion_m2_s'] * (T / T25) * water_viscosity(T25) / water_viscosity(T)
    return mu, zbar, d_ion


def log_epsilon(sid, wavelength_m):
    """NIST UV log10(epsilon / (L mol^-1 cm^-1)) at the wavelength, from the shared spectral library (linked by CAS); None if unknown."""
    lib, uv = library()
    sl = sid if sid in NEUTRAL_MARKERS else ((lib['records'].get(sid) or {}).get('shared_ids') or {}).get('spectral_library')
    spectra = (uv.get(sl) or {}).get('spectra') or []
    nm = wavelength_m * 1e9
    for s in spectra:
        if 'epsilon' not in str(s.get('y_units', '')).lower():
            continue
        x, y = s['wavelength_nm'], s['y']
        pts = sorted((a, b) for a, b in zip(x, y) if a is not None and b is not None)
        if not pts or not pts[0][0] <= nm <= pts[-1][0]:
            continue
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            if x0 <= nm <= x1:
                return y0 + (y1 - y0) * (nm - x0) / (x1 - x0) if x1 > x0 else y0
        return pts[-1][1]
    return None


# ------------------------------------------------------------------------------------------------------------------------ runtime
class SciexPa800Runtime:
    def __init__(self, instance_id='pa800_01'):
        self.instance_id = instance_id
        self.time_s = 0.0
        self.revision = 0
        self.powered = False
        self.sample_cover = 0.0
        self.cartridge_cover = 0.0
        self.insertion_bar = 0.0
        self.cartridge = None                               # dict(id_m, ld_m, lt_m, coating)
        self.liquid_fraction = 0.0
        self.cap_fill = {}                                  # capillary liquid composition mol/m^3
        self.zones = []                                     # dicts: sid, n_mol, x_m, var_m2
        self.cap_target, self.cap_temp = 298.15, PLANT['ambient_k']
        self.sample_target, self.sample_temp = 293.15, PLANT['ambient_k']
        self.lamp_on, self.lamp_elapsed = False, 0.0
        self.det = dict(module='pda', mode='direct_uv', wavelength_m=214e-9, data_rate_hz=4.0)
        self.method = []
        self.phase, self.stage, self.step_index, self.elapsed, self.step_elapsed, self.fault = 'idle', 'idle', -1, 0.0, 0.0, ''
        self.voltage, self.current, self.pressure, self.buffer_temp = 0.0, 0.0, 0.0, PLANT['ambient_k']
        self.vials = {}                                     # pos -> dict(label, volume_m3, mol{sid: mol})
        self.placed, self.removed = {}, {}                  # ledger: mol placed into / removed from the instrument per species
        self.trace_t, self.trace_a, self.trace_i = [], [], []
        self.next_sample_t = 0.0
        self.sep_t = 0.0
        self.charge, self.joule = 0.0, 0.0
        self.truth_peaks = []
        self.run_count = 0
        self.operation_id = None
        self._rng = random.Random(0)
        self._inj_tag = None
        self._principals = {'__public_agent__': 'agent'}
        self._requests, self._owners = {}, {}
        self._bge_cache = None
        library()

    # ---- principals / sessions
    def register_principal(self, principal_id, visibility='agent'):
        if visibility not in ('agent', 'evaluator', 'debug'):
            raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:
            raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id] = visibility

    def session(self, principal_id='__public_agent__'):
        if principal_id not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return SciexPa800Session(self, principal_id)

    def describe(self):
        return self.session().describe(instance_id=self.instance_id)

    def debug_snapshot(self):
        """Trusted host inspection (pure): evaluator state plus the zone list and vial contents."""
        return dict(state=self.read('evaluator'), zones=copy.deepcopy(self.zones), vials=copy.deepcopy(self.vials))

    # ---- derived quantities (pure)
    def _hv_active(self):
        return self.phase == 'running' and abs(self.voltage) > 0.0

    def _bge(self, T=None):
        if not self.cap_fill or self.liquid_fraction < 1.0 - 1e-9:
            return None
        T = self.buffer_temp if T is None else T
        key = (tuple(sorted(self.cap_fill.items())), round(T, 6))
        if self._bge_cache and self._bge_cache[0] == key:
            return self._bge_cache[1]
        b = solve_bge(self.cap_fill, T)
        self._bge_cache = (key, b)
        return b

    def _eof(self, bge, T):
        if not self.cartridge or bge is None or self.cartridge['coating'] != 'bare_fused_silica':
            return 0.0 if self.cartridge else None
        P = PLANT
        f0 = P['eof_low_ph_fraction']
        s = f0 + (1.0 - f0) / (1.0 + 10 ** (P['eof_silanol_pk'] - bge['pH']))
        g = (P['eof_reference_ionic_strength_mol_m3'] / max(bge['ionic_mol_m3'], 1e-3)) ** P['eof_ionic_exponent']
        g = min(max(g, 0.5), 1.6)
        return P['eof_reference_m2_v_s'] * s * g * water_viscosity(T25) / water_viscosity(T)

    def _ledger(self):
        out = {}
        sids = set(self.placed) | set(self.removed)
        for sid in sids:
            in_vials = sum(v['mol'].get(sid, 0.0) for v in self.vials.values())
            in_fill = self.cap_fill.get(sid, 0.0) * self._cap_volume() * self.liquid_fraction if self.cartridge else 0.0
            in_zones = sum(z['n_mol'] for z in self.zones if z['sid'] == sid)
            placed, removed = self.placed.get(sid, 0.0), self.removed.get(sid, 0.0)
            out[sid] = dict(placed=placed, in_vials=in_vials, in_capillary_liquid=in_fill, in_zones=in_zones, removed=removed,
                            residual=placed - removed - in_vials - in_fill - in_zones)
        return out

    def _cap_volume(self):
        c = self.cartridge
        return math.pi * (c['id_m'] / 2) ** 2 * c['lt_m'] if c else 0.0

    def _values(self):
        c = self.cartridge or {}
        bge = self._bge()
        q = lambda x, s: round(x / s) * s   # noqa: E731
        return {
            'power.enabled': self.powered, 'sample_cover.angle_rad': self.sample_cover, 'cartridge_cover.angle_rad': self.cartridge_cover,
            'insertion_bar.angle_rad': self.insertion_bar, 'cartridge.installed': self.cartridge is not None,
            'cartridge.inner_diameter_m': c.get('id_m'), 'cartridge.length_to_detector_m': c.get('ld_m'), 'cartridge.total_length_m': c.get('lt_m'),
            'cartridge.coating': c.get('coating'), 'truth.capillary_liquid_fraction': self.liquid_fraction,
            'capillary.target_k': self.cap_target, 'capillary.temperature_k': q(self.cap_temp, 0.1),
            'sample_storage.target_k': self.sample_target, 'sample_storage.temperature_k': q(self.sample_temp, 0.1),
            'lamp.on': self.lamp_on, 'lamp.ready': self._lamp_ready(),
            'detector.module': self.det['module'], 'detector.mode': self.det['mode'], 'detector.wavelength_m': self.det['wavelength_m'],
            'detector.data_rate_hz': self.det['data_rate_hz'], 'method.configured': bool(self.method), 'method.step_count': len(self.method),
            'cycle.phase': self.phase, 'cycle.stage': self.stage, 'cycle.step_index': self.step_index, 'cycle.elapsed_s': self.elapsed,
            'cycle.fault': self.fault, 'hv.voltage_v': q(self.voltage, 10.0), 'hv.current_a': q(self.current, I_STEP),
            'pressure.applied_pa': q(self.pressure, 1.0),
            'led.power': self.powered, 'led.uv': self.powered and self.lamp_on,
            'led.hv_positive': self._hv_active() and self.voltage > 0, 'led.hv_negative': self._hv_active() and self.voltage < 0,
            'trays.vials': {p: dict(label=v['label'], volume_m3=q(v['volume_m3'], 1e-9)) for p, v in sorted(self.vials.items())},
            'result.available': bool(self.trace_t),
            'truth.bge_ph': bge['pH'] if bge else None, 'truth.bge_conductivity_s_m': bge['conductivity_s_m'] if bge else None,
            'truth.bge_ionic_strength_mol_m3': bge['ionic_mol_m3'] if bge else None,
            'truth.eof_mobility_m2_v_s': self._eof(bge, self.buffer_temp) if bge else None, 'truth.buffer_temperature_k': self.buffer_temp,
            'truth.capillary_fill_mol_m3': dict(self.cap_fill), 'truth.zones': copy.deepcopy(self.zones),
            'truth.vial_contents_mol': {p: dict(v['mol']) for p, v in sorted(self.vials.items())},
            'truth.species_ledger_mol': self._ledger(), 'truth.peak_identities': copy.deepcopy(self.truth_peaks),
            'truth.charge_passed_c': self.charge, 'truth.joule_energy_j': self.joule}

    def read(self, visibility='agent'):
        vals = self._values()
        return copy.deepcopy({k: v for k, v in vals.items() if SLOTS[k][3] == 'agent' or visibility in ('evaluator', 'debug')})

    def _lamp_ready(self):
        return self.powered and self.lamp_on and self.lamp_elapsed >= PLANT['lamp_warmup_s'] - 1e-9

    # ---- results (pure)
    def _peaks(self):
        t, a = self.trace_t, self.trace_a
        if len(t) < 5:
            return []
        sign = -1.0 if self.det['mode'] == 'indirect_uv' else 1.0
        y = [sign * v for v in a]
        n = len(y)
        base = sorted(y)[n // 10] if n >= 10 else min(y)
        noise = PLANT['baseline_noise_au']
        thr, edge = base + 10 * noise, base + 3 * noise
        peaks, k = [], 0
        while k < n:
            if y[k] > thr:
                i0 = k
                while i0 > 0 and y[i0 - 1] > edge:
                    i0 -= 1
                i1 = k
                while i1 < n - 1 and y[i1 + 1] > edge:
                    i1 += 1
                seg = range(i0, i1 + 1)
                apex = max(seg, key=lambda j: y[j])
                area = sum(0.5 * (y[j] + y[j + 1] - 2 * base) * (t[j + 1] - t[j]) for j in range(i0, i1))
                h = y[apex] - base
                above = [t[j] for j in seg if y[j] - base >= h / 2]
                tm = t[apex]
                if area > 0 and tm > 0:
                    peaks.append(dict(migration_time_s=tm, height_au=h, area_au_s=area, corrected_area_au=area / tm,
                                      area_percent=0.0, width_half_s=(max(above) - min(above)) if above else 0.0))
                k = i1 + 1
            else:
                k += 1
        tot = sum(p['area_au_s'] for p in peaks)
        for p in peaks:
            p['area_percent'] = 100.0 * p['area_au_s'] / tot if tot > 0 else 0.0
        return peaks

    def _result(self):
        sep = next((s for s in self.method if s['type'] == 'separate'), None)
        ev = dict(mobility='measured_literature / derived (sciex_pa800_mobility_library: PHREEQC Nernst-Einstein, Hirokawa-STEEP via ionize)',
                  uv_absorptivity='measured (NIST UV log-epsilon via spectral_library); indirect mode: derived Kohlrausch transfer ratio',
                  eof='assumption (bare fused silica sigmoid in pH, ionic-strength power law)', noise='synthetic (Gaussian baseline noise, seeded)')
        return dict(phase=self.phase, available=bool(self.trace_t), detection=self.det['mode'] if self.trace_t else 'none',
                    wavelength_m=self.det['wavelength_m'] if self.trace_t else None, data_rate_hz=self.det['data_rate_hz'] if self.trace_t else None,
                    time_s=list(self.trace_t), absorbance_au=list(self.trace_a), current_trace_a=list(self.trace_i), peaks=self._peaks(),
                    separation_voltage_v=(sep.get('voltage_v') if sep and sep.get('control', 'voltage') == 'voltage' else None) if self.trace_t else None,
                    mean_current_a=(sum(self.trace_i) / len(self.trace_i)) if self.trace_i else None, template_source=LIBRARY_REL, evidence=ev)

    # ---- capability implementation (state changes only after all checks)
    def invoke(self, capability, **a):
        if capability not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0], a)
        if capability in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        running = self.phase == 'running'
        if capability in ('cartridge.install', 'cartridge.remove', 'method.configure', 'detector.configure', 'vial.place', 'vial.remove',
                          'insertion_bar.set_position', 'capillary.set_temperature') and running:
            raise ActionRejected('BUSY')
        result = None
        if capability == 'power.set' or capability == 'power.toggle':
            en = (not self.powered) if capability == 'power.toggle' else a['enabled']
            self.powered = en
            if not en:
                self.lamp_elapsed = 0.0
                self.lamp_on = False
                if running:
                    self._abort('cancelled', 'POWER_LOSS')
            result = {'enabled': en}
        elif capability == 'sample_cover.set_position':
            ang = a['angle_rad']
            if ang < SAMPLE_COVER_OPEN - 1e-9 and self.cartridge_cover > 1e-6:
                raise ActionRejected('PRECONDITION_FAILED')          # the outer door cannot close over an open cartridge cover
            self.sample_cover = ang
            if ang > 1e-3 and running:
                self._abort('cancelled', 'DOOR_INTERLOCK')
            result = {'angle_rad': ang}
        elif capability == 'cartridge_cover.set_position':
            ang = a['angle_rad']
            if ang > 1e-6 and self.sample_cover < SAMPLE_COVER_OPEN * 0.9:
                raise ActionRejected('PRECONDITION_FAILED')
            if ang < CARTRIDGE_COVER_OPEN * 0.9 and self.insertion_bar > 1e-6:
                raise ActionRejected('PRECONDITION_FAILED')          # lower the insertion bar before closing the cartridge cover
            self.cartridge_cover = ang
            if ang > 1e-3 and running:
                self._abort('cancelled', 'DOOR_INTERLOCK')
            result = {'angle_rad': ang}
        elif capability == 'insertion_bar.set_position':
            if self.sample_cover < SAMPLE_COVER_OPEN * 0.9 or self.cartridge_cover < CARTRIDGE_COVER_OPEN * 0.9:
                raise ActionRejected('PRECONDITION_FAILED')
            self.insertion_bar = a['angle_rad']
            result = {'angle_rad': self.insertion_bar}
        elif capability == 'cartridge.install':
            if self.cartridge is not None:
                raise ActionRejected('PRECONDITION_FAILED')
            if self.insertion_bar < INSERTION_BAR_UP * 0.9 or self.cartridge_cover < CARTRIDGE_COVER_OPEN * 0.9:
                raise ActionRejected('PRECONDITION_FAILED')
            ld = a['length_to_detector_m']
            self.cartridge = dict(id_m=float(a['inner_diameter_m']), ld_m=ld, lt_m=ld + L_OFFSET_M, coating=a['coating'])
            self.liquid_fraction, self.cap_fill, self.zones = 0.0, {}, []
            self._bge_cache = None
            result = {'installed': True, 'total_length_m': self.cartridge['lt_m']}
        elif capability == 'cartridge.remove':
            if self.cartridge is None or self.insertion_bar < INSERTION_BAR_UP * 0.9 or self.cartridge_cover < CARTRIDGE_COVER_OPEN * 0.9:
                raise ActionRejected('PRECONDITION_FAILED')
            vol = self._cap_volume() * self.liquid_fraction
            for sid, c in self.cap_fill.items():
                self.removed[sid] = self.removed.get(sid, 0.0) + c * vol
            for z in self.zones:
                self.removed[z['sid']] = self.removed.get(z['sid'], 0.0) + z['n_mol']
            self.cartridge, self.liquid_fraction, self.cap_fill, self.zones = None, 0.0, {}, []
            self._bge_cache = None
            result = {'installed': False}
        elif capability == 'capillary.set_temperature':
            self.cap_target = a['temperature_k']
            result = dict(a)
        elif capability == 'sample_storage.set_temperature':
            self.sample_target = a['temperature_k']
            result = dict(a)
        elif capability == 'lamp.set':
            if a['on'] and not self.powered:
                raise ActionRejected('PRECONDITION_FAILED')
            if a['on'] and not self.lamp_on:
                self.lamp_elapsed = 0.0
            self.lamp_on = a['on']
            if running and not a['on'] and any(st['type'] == 'separate' for st in self.method[max(self.step_index, 0):]):
                self._abort('cancelled', 'LAMP_OFF')        # a separation (detection) is still to come
            result = dict(a)
        elif capability == 'detector.configure':
            wl = a['wavelength_m']
            if a['module'] == 'uv_filter' and not any(abs(wl - f) < 0.5e-9 for f in UV_FILTERS_M):
                raise ActionRejected('INVALID_ARGUMENT')
            self.det = dict(module=a['module'], mode=a['mode'], wavelength_m=wl, data_rate_hz=a['data_rate_hz'])
            result = dict(self.det)
        elif capability == 'method.configure':
            steps = [self._check_step(s) for s in a['steps']]
            if sum(1 for s in steps if s['type'] == 'separate') > 1:
                raise ActionRejected('INVALID_ARGUMENT')
            self.method = steps
            if self.phase in ('succeeded', 'cancelled', 'failed'):
                self.phase, self.stage, self.step_index = 'idle', 'idle', -1
            result = {'configured': True, 'step_count': len(steps)}
        elif capability == 'run.start':
            self._check_start()
            self.run_count += 1
            self.operation_id = f'{self.instance_id}:run:{self.run_count}'
            self._rng = random.Random(zlib.crc32(f'{self.instance_id}:{self.run_count}'.encode()))
            self.phase, self.fault, self.elapsed = 'running', '', 0.0
            self.trace_t, self.trace_a, self.trace_i, self.truth_peaks = [], [], [], []
            self.charge = self.joule = 0.0
            self._enter_step(0)
            result = {'phase': self.phase, 'operation_id': self.operation_id}
        elif capability == 'run.stop':
            if running:
                self._abort('cancelled', 'USER_STOP')
            result = {'phase': self.phase}
        elif capability == 'electropherogram.read':
            return copy.deepcopy(self._result())
        elif capability == 'vial.place':
            pos = a['position']
            self._check_pos(pos)
            if self.sample_cover < SAMPLE_COVER_OPEN * 0.9 or pos in self.vials:
                raise ActionRejected('PRECONDITION_FAILED')
            comp = a['composition_mol_m3']
            if not all(known_species(s) for s in comp):
                raise ActionRejected('UNKNOWN_SPECIES')
            vol = a['volume_m3']
            mol = {s: c * vol for s, c in comp.items() if c > 0}
            self.vials[pos] = dict(label=a['label'], volume_m3=vol, mol=mol)
            for s, n in mol.items():
                self.placed[s] = self.placed.get(s, 0.0) + n
            result = {'position': pos, 'placed': True}
        elif capability == 'vial.remove':
            pos = a['position']
            if self.sample_cover < SAMPLE_COVER_OPEN * 0.9 or pos not in self.vials:
                raise ActionRejected('PRECONDITION_FAILED')
            v = self.vials.pop(pos)
            for s, n in v['mol'].items():
                self.removed[s] = self.removed.get(s, 0.0) + n
            result = {'position': pos, 'volume_m3': v['volume_m3'], 'contents_mol': dict(v['mol'])}
        self.revision += 1
        return copy.deepcopy(result)

    def _check_pos(self, pos):
        tray, cell = pos.split(':')
        rows, cols = TRAYS[tray][1], TRAYS[tray][2]
        if not ('A' <= cell[0] <= chr(ord('A') + rows - 1) and 1 <= int(cell[1:]) <= cols):
            raise ActionRejected('INVALID_ARGUMENT')

    def _check_step(self, s):
        s = dict(s)
        t = s['type']
        need = {'rinse': ('inlet', 'outlet', 'pressure_pa'), 'inject': ('inlet', 'outlet', 'injection_mode'),
                'separate': ('inlet', 'outlet', 'control'), 'wait': ()}[t]
        allowed = set(need) | {'type', 'duration_s'} | {'rinse': set(), 'inject': {'pressure_pa', 'voltage_v'},
                                                       'separate': {'voltage_v', 'current_a', 'current_limit_a', 'ramp_s', 'pressure_pa'},
                                                       'wait': {'inlet', 'outlet'}}[t]
        if not set(need) <= set(s) or not set(s) <= allowed:
            raise ActionRejected('INVALID_ARGUMENT')
        for k in ('inlet', 'outlet'):
            if k in s:
                self._check_pos(s[k])
        if t == 'inject':
            if s['injection_mode'] == 'pressure' and ('pressure_pa' not in s or 'voltage_v' in s or s['pressure_pa'] <= 0):
                raise ActionRejected('INVALID_ARGUMENT')
            if s['injection_mode'] == 'voltage' and ('voltage_v' not in s or 'pressure_pa' in s or not V_MIN <= abs(s['voltage_v']) <= V_MAX):
                raise ActionRejected('INVALID_ARGUMENT')
            if s['duration_s'] > 300.0:
                raise ActionRejected('INVALID_ARGUMENT')
        if t == 'separate':
            if s['control'] == 'voltage':
                if 'voltage_v' not in s or 'current_a' in s or not V_MIN <= abs(s['voltage_v']) <= V_MAX:
                    raise ActionRejected('INVALID_ARGUMENT')
                if abs(round(s['voltage_v'] / V_STEP) * V_STEP - s['voltage_v']) > 1e-6:
                    raise ActionRejected('INVALID_ARGUMENT')
            else:
                if 'current_a' not in s or 'voltage_v' not in s or not V_MIN <= abs(s['voltage_v']) <= V_MAX:
                    raise ActionRejected('INVALID_ARGUMENT')            # constant current: voltage_v = polarity and compliance limit
            for k in ('current_a', 'current_limit_a'):
                if k in s and abs(round(s[k] / I_STEP) * I_STEP - s[k]) > 1e-12:
                    raise ActionRejected('INVALID_ARGUMENT')
            s.setdefault('ramp_s', 10.0)
            s.setdefault('current_limit_a', I_MAX)
            s.setdefault('pressure_pa', 0.0)
        if t == 'rinse' and s['pressure_pa'] == 0:
            raise ActionRejected('INVALID_ARGUMENT')
        return s

    def _check_start(self):
        if self.phase == 'running':
            raise ActionRejected('BUSY')
        if not self.powered or not self.method or self.cartridge is None:
            raise ActionRejected('PRECONDITION_FAILED')
        if self.sample_cover > 1e-6 or self.cartridge_cover > 1e-6 or self.insertion_bar > 1e-6:
            raise ActionRejected('PRECONDITION_FAILED')
        if any(s['type'] == 'separate' for s in self.method) and not self._lamp_ready():
            raise ActionRejected('PRECONDITION_FAILED')
        for s in self.method:
            for k in ('inlet', 'outlet'):
                if k in s and s[k] not in self.vials:
                    raise ActionRejected('PRECONDITION_FAILED')

    # ---- run state machine
    def _enter_step(self, i):
        self.step_index, self.step_elapsed = i, 0.0
        self._inj_tag = f'{self.run_count}:{i}'
        if i >= len(self.method):
            self.phase, self.stage = 'succeeded', 'done'
            self.voltage = self.current = self.pressure = 0.0
            self._finish_truth()
            return
        s = self.method[i]
        self.stage = s['type']
        self.voltage = self.current = self.pressure = 0.0
        if s['type'] == 'separate':
            self.sep_t, self.next_sample_t = 0.0, 0.0
            self.trace_t, self.trace_a, self.trace_i = [], [], []
            self._record_truth(s)

    def _abort(self, phase, fault):
        self.phase, self.fault = phase, fault
        self.voltage = self.current = self.pressure = 0.0
        self.stage = 'done'
        self._finish_truth()

    def _finish_truth(self):
        self.buffer_temp = self.cap_temp

    def _record_truth(self, s):
        """Theoretical migration of each zone species in this separation (evaluator truth; computed at step start)."""
        bge = self._bge(self.cap_temp)
        self.truth_peaks = []
        if bge is None or not self.cartridge:
            return
        V, I, Tb = self._electrical(s, bge, ramp=1.0)              # steady state after the ramp (current limit / constant current / Joule)
        bge_t = solve_bge(self.cap_fill, Tb)
        E = V / self.cartridge['lt_m']
        eof = self._eof(bge_t, Tb) or 0.0
        area = math.pi * (self.cartridge['id_m'] / 2) ** 2
        v_hd = self._poiseuille(s['pressure_pa'], Tb) / area if s.get('pressure_pa') else 0.0
        for z in self.zones:
            if z['sid'] in NEUTRAL_MARKERS:
                mu, zbar = 0.0, 0.0
            else:
                mu, zbar, _ = effective_mobility(z['sid'], bge_t['pH'], bge_t['ionic_mol_m3'], Tb)
            v = (mu + eof) * E + v_hd
            le = log_epsilon(z['sid'], self.det['wavelength_m'])
            dist = self.cartridge['ld_m'] - z['x_m']
            t_exp = None
            if v > 0 and dist > 0:                                 # linear ramp: half speed on average while ramping
                t_exp = dist / v + s['ramp_s'] / 2 if dist >= v * s['ramp_s'] / 2 else math.sqrt(2 * dist * s['ramp_s'] / v)
            self.truth_peaks.append(dict(substance_id=z['sid'], n_mol=z['n_mol'], mu_eff_m2_v_s=mu, mean_charge=zbar, eof_m2_v_s=eof,
                                         steady_voltage_v=V, steady_current_a=I, hydrodynamic_m_s=v_hd, expected_migration_time_s=t_exp,
                                         log_epsilon=le, detectable=self._response(z['sid'], bge) != 0.0))

    def _response(self, sid, bge):
        """Absorbance per mol/m^3 of analyte at the detector (AU m^3/mol); 0 when no template allows detection."""
        b_cm = self.cartridge['id_m'] * 100.0
        if self.det['mode'] == 'direct_uv':
            le = log_epsilon(sid, self.det['wavelength_m'])
            return 0.0 if le is None else 10 ** le * b_cm / 1000.0
        # indirect UV: probe = the BGE co-ion with the highest absorbance; counter-ion = dominant opposite-charge constituent
        mu_a, z_a, _ = effective_mobility(sid, bge['pH'], bge['ionic_mol_m3'], self.buffer_temp)
        if abs(z_a) < 1e-6:
            return 0.0
        best = None
        for k, c in self.cap_fill.items():
            le = log_epsilon(k, self.det['wavelength_m'])
            if le is None or k in NEUTRAL_MARKERS:
                continue
            mu_p, z_p, _ = effective_mobility(k, bge['pH'], bge['ionic_mol_m3'], self.buffer_temp)
            if z_p * z_a > 0 and (best is None or 10 ** le * c > best[0]):
                best = (10 ** le * c, k, mu_p, z_p, le)
        if best is None:
            return 0.0
        _, k, mu_p, z_p, le = best
        counter = [(c * abs(effective_mobility(j, bge['pH'], bge['ionic_mol_m3'], self.buffer_temp)[1]), j) for j, c in self.cap_fill.items()
                   if j not in NEUTRAL_MARKERS and effective_mobility(j, bge['pH'], bge['ionic_mol_m3'], self.buffer_temp)[1] * z_a < 0]
        if not counter:
            return 0.0
        j = max(counter)[1]
        mu_c = effective_mobility(j, bge['pH'], bge['ionic_mol_m3'], self.buffer_temp)[0]
        if abs(mu_a) < 1e-15 or abs(mu_p - mu_c) < 1e-15:
            return 0.0
        tr = (z_a / z_p) * (mu_p / mu_a) * ((mu_a - mu_c) / (mu_p - mu_c))  # Kohlrausch transfer ratio (mol probe displaced per mol analyte)
        return -tr * 10 ** le * b_cm / 1000.0

    # ---- fluid handling (uniform-mixing capillary; every mole booked)
    def _vial_take(self, pos, vol):
        v = self.vials[pos]
        frac = min(vol / v['volume_m3'], 1.0) if v['volume_m3'] > 0 else 0.0
        out = {s: n * frac for s, n in v['mol'].items()}
        for s in out:
            v['mol'][s] -= out[s]
            if v['mol'][s] < 1e-30:
                v['mol'][s] = 0.0
        v['volume_m3'] = max(v['volume_m3'] - vol, 0.0)
        return out

    def _vial_add(self, pos, vol, mol):
        v = self.vials[pos]
        v['volume_m3'] += vol
        for s, n in mol.items():
            v['mol'][s] = v['mol'].get(s, 0.0) + n

    def _flow(self, vol, src, dst, move_zones=True):
        """Hydrodynamic flow of `vol` m^3 from vial src through the capillary into vial dst (zones carried along unless their velocity
        already contains the bulk flow, as in a separation)."""
        cv = self._cap_volume()
        area = math.pi * (self.cartridge['id_m'] / 2) ** 2
        vin = self._vial_take(src, vol)
        # liquid already in the capillary is pushed out at the far end; a dry capillary first fills
        fill_vol = cv * self.liquid_fraction
        room = cv - fill_vol
        to_fill = min(vol, room)
        excess = vol - to_fill
        conc_in = {s: n / vol for s, n in vin.items()} if vol > 0 else {}
        new_fill_vol = fill_vol + to_fill
        mol_fill = {s: self.cap_fill.get(s, 0.0) * fill_vol for s in set(self.cap_fill) | set(conc_in)}
        for s, c in conc_in.items():
            mol_fill[s] = mol_fill.get(s, 0.0) + c * to_fill
        out = {}
        if excess > 0 and new_fill_vol > 0:
            # mix the excess through: out = excess * (mixed concentration); uniform-mixing approximation of plug flow
            total_vol = new_fill_vol + excess
            for s in set(mol_fill) | set(conc_in):
                tot = mol_fill.get(s, 0.0) + conc_in.get(s, 0.0) * excess
                out[s] = tot * excess / total_vol
                mol_fill[s] = tot - out[s]
        self.liquid_fraction = min(new_fill_vol / cv, 1.0) if cv > 0 else 0.0
        self.cap_fill = {s: n / (cv * self.liquid_fraction) for s, n in mol_fill.items() if n > 0} if self.liquid_fraction > 0 else {}
        self._bge_cache = None
        if move_zones:
            dx = vol / area
            for z in self.zones:
                z['x_m'] += dx if src.startswith(('BI', 'SI')) else -dx
        self._vial_add(dst, excess, out)
        if move_zones:
            inlet, outlet = (src, dst) if src.startswith(('BI', 'SI')) else (dst, src)
            self._exit_zones(inlet, outlet)

    def _displace(self, vol, outlet):
        """A sample plug of `vol` m^3 enters at the inlet: the same volume of capillary liquid (BGE at its concentration) leaves into the
        outlet vial and the BGE left inside is diluted accordingly (the plug's solutes are booked as zones)."""
        cv = self._cap_volume()
        frac = min(vol / cv, 1.0) if cv > 0 else 0.0
        out = {k: c * cv * frac for k, c in self.cap_fill.items()}
        self.cap_fill = {k: c * (1.0 - frac) for k, c in self.cap_fill.items()}
        self._bge_cache = None
        self._vial_add(outlet, vol, out)

    def _exit_zones(self, inlet, outlet):
        lt = self.cartridge['lt_m']
        for z in list(self.zones):
            if z['x_m'] > lt:
                self._vial_add(outlet, 0.0, {z['sid']: z['n_mol']})
                self.zones.remove(z)
            elif z['x_m'] < 0:
                self._vial_add(inlet, 0.0, {z['sid']: z['n_mol']})
                self.zones.remove(z)

    def _grow_plug(self, take, lengths):
        """Add injected moles to this injection step's plug zones (one zone per species and step); species i's plug of length L_i sits at
        [0, L_i] (pressure injection: the same L for every species; electrokinetic: L_i = v_i t)."""
        for sid, n in take.items():
            if n <= 0:
                continue
            zone = next((z for z in self.zones if z['sid'] == sid and z.get('inj') == self._inj_tag), None)
            if zone is None:
                zone = dict(sid=sid, n_mol=0.0, x_m=0.0, var_m2=0.0, inj=self._inj_tag, plug_m=0.0)
                self.zones.append(zone)
            zone['n_mol'] += n
            zone['plug_m'] += lengths[sid] if isinstance(lengths, dict) else lengths
            L = zone['plug_m']
            zone.update(x_m=L / 2, var_m2=L * L / 12.0)

    def _poiseuille(self, dp, T):
        r = self.cartridge['id_m'] / 2
        return math.pi * r ** 4 * dp / (8 * water_viscosity(T) * self.cartridge['lt_m'])

    # ---- physics
    def step(self, dt_s):
        if isinstance(dt_s, bool) or not isinstance(dt_s, (int, float)) or not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError('dt_s must be finite and positive')
        left = float(dt_s)
        while left > 1e-12:
            h = min(left, 0.5)
            if self.phase == 'running':
                s = self.method[self.step_index]
                h = min(h, max(s['duration_s'] - self.step_elapsed, 0.0))
                if h <= 1e-12:
                    self._enter_step(self.step_index + 1)
                    continue
                self._advance_step(s, h)
                if self.phase != 'running':                  # a fault inside the step ends the run
                    self._thermal(h)
                    self.time_s += h
                    left -= h
                    continue
                self.step_elapsed += h
                self.elapsed += h
                if self.step_elapsed >= s['duration_s'] - 1e-9:
                    self._enter_step(self.step_index + 1)
            else:
                self.buffer_temp = self.cap_temp
            self._thermal(h)
            self.time_s += h
            left -= h
        self.revision += 1
        return self.read('evaluator')

    def _thermal(self, h):
        P = PLANT
        amb = P['ambient_k']
        cap_goal = self.cap_target if self.powered else amb
        smp_goal = self.sample_target if self.powered else amb
        self.cap_temp = cap_goal + (self.cap_temp - cap_goal) * math.exp(-h / P['capillary_tau_s'])
        self.sample_temp = smp_goal + (self.sample_temp - smp_goal) * math.exp(-h / P['sample_tau_s'])
        if self.powered and self.lamp_on:
            self.lamp_elapsed += h

    def _vial_guard(self, *positions):
        for p in positions:
            v = self.vials.get(p)
            if v is None:
                self._abort('failed', 'VIAL_MISSING')
                return False
            if v['volume_m3'] > VIAL_MAX_M3 + 1e-12:
                self._abort('failed', 'VIAL_OVERFLOW')
                return False
            vmin = PLANT['buffer_min_volume_m3'] if p.startswith('B') else PLANT['sample_min_volume_m3']
            if v['volume_m3'] < vmin - 1e-15:
                self._abort('failed', 'VIAL_EMPTY')
                return False
        return True

    def _advance_step(self, s, h):
        t = s['type']
        if t == 'wait':
            return
        inlet, outlet = s['inlet'], s['outlet']
        if not self._vial_guard(inlet, outlet):
            return
        T = self.cap_temp
        if t == 'rinse':
            self.pressure = s['pressure_pa']
            q = self._poiseuille(abs(self.pressure), T)
            src, dst = (inlet, outlet) if self.pressure > 0 else (outlet, inlet)
            self._flow(q * h, src, dst)
            self._vial_guard(inlet, outlet)
            return
        if t == 'inject':
            if self.liquid_fraction < 1.0 - 1e-9:
                self._abort('failed', 'CURRENT_FAULT' if s['injection_mode'] == 'voltage' else 'CAPILLARY_DRY')
                return
            if s['injection_mode'] == 'pressure':
                self.pressure = s['pressure_pa']
                vol = self._poiseuille(self.pressure, T) * h
                area = math.pi * (self.cartridge['id_m'] / 2) ** 2
                l = vol / area
                take = self._vial_take(inlet, vol)
                for z in self.zones:                        # zones of earlier steps are pushed downstream by the plug
                    if z.get('inj') != self._inj_tag:
                        z['x_m'] += l
                self._displace(vol, outlet)                  # displaced capillary liquid leaves at the outlet end
                self._grow_plug(take, l)
                self._exit_zones(inlet, outlet)
            else:
                bge = self._bge(T)
                V, I, Tb = self._electrical(dict(control='voltage', voltage_v=s['voltage_v'], current_limit_a=I_MAX, ramp_s=0.0), bge, ramp=1.0)
                self.voltage, self.current, self.buffer_temp = V, I, Tb
                self.charge += I * h
                self.joule += abs(V) * I * h
                E = self.voltage / self.cartridge['lt_m']
                area = math.pi * (self.cartridge['id_m'] / 2) ** 2
                eof = self._eof(bge, T) or 0.0
                vial = self.vials[inlet]
                conc = {k: n / vial['volume_m3'] for k, n in vial['mol'].items() if n > 0} if vial['volume_m3'] > 0 else {}
                for z in self.zones:                        # zones of earlier steps move electrophoretically during the injection
                    if z.get('inj') != self._inj_tag:
                        mu_z = effective_mobility(z['sid'], bge['pH'], bge['ionic_mol_m3'], T)[0] if z['sid'] not in NEUTRAL_MARKERS else 0.0
                        z['x_m'] += (mu_z + eof) * E * h
                for sid, c in conc.items():
                    mu, _, _ = effective_mobility(sid, bge['pH'], bge['ionic_mol_m3'], T) if sid not in NEUTRAL_MARKERS else (0.0, 0, 0)
                    v = (mu + eof) * E
                    if v <= 0:
                        continue
                    n = min(c * area * v * h, vial['mol'][sid])
                    vial['mol'][sid] -= n
                    self._grow_plug({sid: n}, {sid: v * h})
                self._exit_zones(inlet, outlet)
                if eof * E > 0:                             # EOF bulk flow: sample solvent enters, capillary liquid leaves at the outlet
                    vol = area * eof * E * h
                    vial['volume_m3'] = max(vial['volume_m3'] - vol, 0.0)
                    self._displace(vol, outlet)
            return
        self._separate(s, h)

    def _electrical(self, s, bge, ramp=None):
        """Voltage, current, buffer temperature (fixed point with Joule heating) for the present separation sub-step (ramp=1: steady state)."""
        c = self.cartridge
        area = math.pi * (c['id_m'] / 2) ** 2
        P = PLANT
        r_i, r_o, r_p = c['id_m'] / 2, P['silica_outer_diameter_m'] / 2, P['capillary_outer_diameter_m'] / 2
        rth = (math.log(r_o / r_i) / (2 * math.pi * P['silica_k_w_m_k']) + math.log(r_p / r_o) / (2 * math.pi * P['polyimide_k_w_m_k'])
               + 1.0 / (2 * math.pi * r_p * P['coolant_h_w_m2_k']))
        if ramp is None:
            ramp = min(self.sep_t / s['ramp_s'], 1.0) if s['ramp_s'] > 0 else 1.0
        sign = 1.0 if s['voltage_v'] > 0 else -1.0
        T = self.cap_temp
        for _ in range(8):
            kappa = solve_bge(self.cap_fill, T)['conductivity_s_m'] if abs(T - self.cap_temp) > 1e-9 else bge['conductivity_s_m']
            g = kappa * area / c['lt_m']                         # conductance (S)
            if s['control'] == 'voltage':
                V = s['voltage_v'] * ramp
                I = g * abs(V)
                if I > s['current_limit_a']:                     # current-limited: the supply lowers the voltage
                    I = s['current_limit_a']
                    V = sign * I / g
            else:
                I = s['current_a'] * ramp
                V = sign * min(I / g, abs(s['voltage_v']))
                I = g * abs(V)
            T_new = self.cap_temp + abs(V) * I / c['lt_m'] * rth
            if abs(T_new - T) < 1e-4:
                T = T_new
                break
            T = T_new
        return V, I, T

    def _separate(self, s, h):
        c = self.cartridge
        if self.liquid_fraction < 1.0 - 1e-9 or not self.cap_fill:
            self._abort('failed', 'CURRENT_FAULT')
            return
        bge0 = self._bge(self.cap_temp)
        V, I, Tb = self._electrical(s, bge0)
        self.voltage, self.current, self.buffer_temp = V, I, Tb
        bge = solve_bge(self.cap_fill, Tb)
        eof = self._eof(bge, Tb) or 0.0
        E = V / c['lt_m']
        area = math.pi * (c['id_m'] / 2) ** 2
        v_hd = self._poiseuille(s['pressure_pa'], Tb) / area if s['pressure_pa'] else 0.0
        self.pressure = s['pressure_pa']
        # zone velocities and dispersion in this sub-step
        kin = []
        for z in self.zones:
            if z['sid'] in NEUTRAL_MARKERS:
                mu, d = 0.0, PLANT['neutral_diffusion_m2_s'] * (Tb / T25) * water_viscosity(T25) / water_viscosity(Tb)
            else:
                mu, _, d = effective_mobility(z['sid'], bge['pH'], bge['ionic_mol_m3'], Tb)
            d_eff = d + (c['id_m'] / 2) ** 2 * v_hd ** 2 / (48 * d) if v_hd else d
            kin.append(((mu + eof) * E + v_hd, d_eff, self._response(z['sid'], bge)))
        # detector samples inside (sep_t, sep_t + h]
        rate = self.det['data_rate_hz']
        noise = PLANT['baseline_noise_au']
        win = PLANT['detector_window_m']
        while self.next_sample_t <= self.sep_t + h + 1e-12:
            tau = self.next_sample_t - self.sep_t
            if tau >= -1e-12:
                a = 0.0
                for z, (v, d, resp) in zip(self.zones, kin):
                    if resp == 0.0:
                        continue
                    x = z['x_m'] + v * max(tau, 0.0)
                    var = z['var_m2'] + 2 * d * max(tau, 0.0) + win * win / 12.0
                    cdet = z['n_mol'] / (area * math.sqrt(2 * math.pi * var)) * math.exp(-(c['ld_m'] - x) ** 2 / (2 * var))
                    a += resp * cdet
                self.trace_t.append(round(self.next_sample_t, 9))
                self.trace_a.append(a + self._rng.gauss(0.0, noise))
                self.trace_i.append(I)
            self.next_sample_t = round(self.next_sample_t + 1.0 / rate, 9)
        for z, (v, d, _) in zip(self.zones, kin):
            z['x_m'] += v * h
            z['var_m2'] += 2 * d * h
        self._exit_zones(s['inlet'], s['outlet'])
        # EOF / pressure bulk flow moves solvent volume from inlet to outlet vial (solutes of the BGE fill carried at its concentration)
        bulk = area * (eof * E + v_hd) * h
        if abs(bulk) > 0:
            self._flow(abs(bulk), s['inlet'] if bulk > 0 else s['outlet'], s['outlet'] if bulk > 0 else s['inlet'], move_zones=False)
        self.charge += I * h
        self.joule += abs(V) * I * h
        self.sep_t += h
        self._vial_guard(s['inlet'], s['outlet'])


# ------------------------------------------------------------------------------------------------------------------------ session
class SciexPa800Session:
    """Principal-bound protocol session (describe / read / invoke / write / poll / cancel)."""

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
        meta = export_sciex_pa800_contract_metadata()
        agent = self._role == 'agent'
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version=ASSET_VERSION,
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
                    observations={k: dict(value=copy.deepcopy(vals[k]), unit=SLOTS[k][1],
                                          quality='estimated' if SLOTS[k][2] in ('derived', 'sensor') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        try:
            return self._invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None, status='rejected',
                        state_revision=self._host.revision, clock={'domain': 'simulation', 'time_s': self._host.time_s}, result={},
                        observations={}, error=dict(code=str(exc), message=str(exc), retryable=str(exc) == 'BUSY', side_effects='none'))

    def _invoke(self, *, instance_id, capability_id, arguments, request_id):
        self._target(instance_id)
        h = self._host
        if not isinstance(capability_id, str) or capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role == 'agent' and CAPS[capability_id][5] != 'agent':
            raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id, str) or not request_id or not isinstance(arguments, dict):
            raise ActionRejected('INVALID_ARGUMENT')
        validate(CAPS[capability_id][0], arguments)
        key = (self._principal, instance_id, request_id)
        try:
            digest = json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            raise ActionRejected('INVALID_ARGUMENT') from None
        if key in h._requests:
            old, result = h._requests[key]
            if old != digest:
                raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        result = h.invoke(capability_id, **copy.deepcopy(arguments))
        op = result.get('operation_id') if capability_id == 'run.start' else None
        if op:
            h._owners[op] = self._principal
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=op,
                        status='accepted' if op else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                        result=result, observations={}, error=None)
        if capability_id != 'electropherogram.read':           # queries are not cached (their value follows the state)
            h._requests[key] = (digest, copy.deepcopy(response))
        return response

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5] or (self._role == 'agent' and SLOTS[state_id][3] != 'agent'):
            raise ActionRejected('ACCESS_DENIED')
        cap, arg = SLOTS[state_id][5]
        return self.invoke(instance_id=instance_id, capability_id=cap, arguments={arg: value}, request_id=request_id)

    def poll(self, operation_id):
        h = self._host
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        if operation_id != h.operation_id:
            raise ActionRejected('UNKNOWN_OPERATION')
        return dict(operation_id=operation_id, status=h.phase, result=h._result())

    def cancel(self, operation_id, request_id):
        self.poll(operation_id)
        response = self.invoke(instance_id=self._host.instance_id, capability_id='run.stop', arguments={}, request_id=request_id)
        return dict(operation_id=operation_id, status=self._host.phase, result=response['result'])


# ------------------------------------------------------------------------------------------------------------------------ contract metadata
PRECONDITIONS = {
    'sample_cover.set_position': ('关闭样品盖前卡盒盖必须已关闭；运行中打开触发联锁（HV关断、运行取消）', ['cartridge_cover.angle_rad', 'cycle.phase'],
                                  'angle < open or cartridge_cover == 0; opening while running -> cancelled/DOOR_INTERLOCK'),
    'cartridge_cover.set_position': ('打开卡盒盖要求样品盖全开；关闭前插入杆须压下', ['sample_cover.angle_rad', 'insertion_bar.angle_rad'],
                                     'opening: sample_cover >= 0.9 open; closing: insertion_bar == 0'),
    'insertion_bar.set_position': ('样品盖与卡盒盖都已全开，且未在运行', ['sample_cover.angle_rad', 'cartridge_cover.angle_rad', 'cycle.phase'],
                                   'both covers >= 0.9 open and phase != running'),
    'cartridge.install': ('未装卡盒、卡盒盖全开、插入杆抬起、未在运行', ['cartridge.installed', 'cartridge_cover.angle_rad', 'insertion_bar.angle_rad', 'cycle.phase'],
                          'not installed and covers open and bar raised and phase != running'),
    'cartridge.remove': ('已装卡盒、卡盒盖全开、插入杆抬起、未在运行', ['cartridge.installed', 'cartridge_cover.angle_rad', 'insertion_bar.angle_rad', 'cycle.phase'],
                         'installed and covers open and bar raised and phase != running'),
    'lamp.set': ('点灯需要通电', ['power.enabled'], 'on -> powered'),
    'detector.configure': ('滤光片模块只接受200/214/254/280 nm；运行中不可改', ['cycle.phase'], 'uv_filter -> wavelength in filters; phase != running'),
    'method.configure': ('每步字段与类型匹配、位置在托盘范围内、最多一个分离步；运行中不可改', ['cycle.phase'], 'per-type required fields; <=1 separate; phase != running'),
    'run.start': ('通电、已装卡盒、两门关闭且插入杆压下、方法已配置、引用的瓶位有瓶；含分离步时氘灯已暖机',
                  ['power.enabled', 'cartridge.installed', 'sample_cover.angle_rad', 'cartridge_cover.angle_rad', 'insertion_bar.angle_rad',
                   'method.configured', 'lamp.ready', 'trays.vials', 'cycle.phase'],
                  'powered and installed and covers closed and bar down and method and vials present and (no separate or lamp ready)'),
    'vial.place': ('样品盖打开、该位空、物质在迁移率库或中性标记表内、体积≤1.5 mL', ['sample_cover.angle_rad', 'trays.vials', 'cycle.phase'],
                   'cover open and position empty and species known and phase != running'),
    'vial.remove': ('样品盖打开且该位有瓶、未在运行', ['sample_cover.angle_rad', 'trays.vials', 'cycle.phase'], 'cover open and vial present and phase != running'),
    'capillary.set_temperature': ('运行中不可改', ['cycle.phase'], 'phase != running'),
}
ERRORS = ['UNKNOWN_INSTANCE', 'UNKNOWN_CAPABILITY', 'ACCESS_DENIED', 'INVALID_ARGUMENT', 'PRECONDITION_FAILED', 'BUSY', 'REQUEST_CONFLICT',
          'UNKNOWN_SPECIES', 'UNSUPPORTED']


def export_sciex_pa800_contract_metadata():
    caps, slots = {}, {}
    for key, (schema, unit, origin, visibility, components, write, meaning) in SLOTS.items():
        slots[key] = dict(component_ids=list(components), value_schema=copy.deepcopy(schema), unit=unit, reference_conditions=None,
                          meaning_zh=meaning, origin=origin, visibility=visibility, access='read_write' if write else 'read_only',
                          read_binding=dict(backend='host', artifact_id='runtime', accessor=f'SciexPa800Session.read(state_ids=[{key!r}])'),
                          write_via=dict(capability_id=write[0], argument=write[1], fixed_arguments={}) if write else None,
                          sampling=dict(mode='instant', time_domain='simulation', window_description=None),
                          source_ids=['manufacturer_primary', 'simulation_assumption'] if origin in ('setpoint', 'scene') else ['simulation_assumption'])
    for key, (inp, out, components, meaning, kind, vis, example, effects) in CAPS.items():
        unsupported = key in UNSUPPORTED
        async_ = key == 'run.start'
        pre = []
        if key in PRECONDITIONS:
            d, st, chk = PRECONDITIONS[key]
            pre = [dict(description_zh=d, state_ids=st, enforced_by='runtime', check=chk)]
        caps[key] = dict(
            function_zh=meaning, kind=kind, visibility=vis, component_ids=list(components), availability='unsupported' if unsupported else 'implemented',
            input_schema=copy.deepcopy(inp), output_schema=copy.deepcopy(out),
            implementation=dict(process='absent' if unsupported else 'implemented', scene='absent', robot='absent', hardware='absent',
                                host='absent' if unsupported else 'implemented'),
            binding=None if unsupported else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.sciex_pa800.SciexPa800Session.invoke',
                                                  invocation=f'session.invoke(instance_id="pa800_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
            preconditions=pre, state_effects=[dict(state_id=k, timing='immediate' if not async_ else 'on_step', description_zh=meaning) for k in effects],
            observes=[], model_effects=dict(mode='none', description_zh='运行层过程；几何联动见 model_bindings 与 Matterix 场景验证。', binding_ids=[]),
            execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                           completion_criterion='cycle.phase=succeeded（方法各步按时长完成）；取消/联锁/故障保留已采集数据' if async_ else '目标写入或观测返回；不推进物理时钟',
                           completion_state_ids=['cycle.phase'] if async_ else list(effects), timeout_s=None,
                           cancellation='supported' if async_ else 'unsupported', retry='same_request_id',
                           resources=['target_instance', 'principal_request_cache'],
                           partial_failure_zh='拒绝不改状态；取消与故障不回滚已迁移、已进样或已冲洗的物质（全部记账）。请求去重只在当前进程内存有效。'),
            errors=[dict(code=e, meaning_zh=e, side_effects='none') for e in ERRORS],
            example_arguments=copy.deepcopy(example), verification=[],
            limitations=(['无488 nm荧光模板与LIF光学模型，调用返回UNSUPPORTED。'] if unsupported else
                         ['工程模型，无真实硬件连接；进程内过滤不能替代文件与进程部署隔离。']),
            source_ids=['manufacturer_primary', 'manufacturer_manual', 'simulation_assumption'])
    P = PLANT
    mech = dict(
        component_ids=['capillary_cartridge', 'interface_block', 'electrode_inlet', 'electrode_outlet', 'uv_detector'],
        state_ids=['truth.bge_ph', 'truth.bge_conductivity_s_m', 'truth.eof_mobility_m2_v_s', 'truth.buffer_temperature_k', 'truth.zones',
                   'hv.current_a', 'truth.species_ledger_mol'],
        meaning_zh='毛细管区带电泳：背景电解质电荷平衡求pH与电导，真实极限淌度/pKa给出有效淌度，电渗流、焦耳热、压力进样与扩散/泰勒色散决定迁移时间与峰形，紫外按NIST摩尔吸光系数检测',
        implementation='numerical',
        equations=['charge balance: sum_j c_j sum_z z alpha_jz(pH) + [H+] - Kw/[H+] = 0 (bisection); pKa(T) = pKa25 + dH/(R ln10)(1/T - 1/298.15)',
                   'mu_z(I,T) = mu_z,25 exp(-a sqrt(|z| I[mol/L])) eta(298.15)/eta(T); eta = 2.414e-5 10^(247.8/(T-140)) Pa s',
                   'kappa = F (sum_j c_j sum_z alpha_jz |z| |mu_z| + [H+]|mu_H| + [OH-]|mu_OH|)',
                   'mu_eof = mu_ref / (1 + 10^(pK_s - pH)) clip((I_ref/I)^n, 0.3, 2) eta(298.15)/eta(T) (bare fused silica; 0 for coated)',
                   'I = kappa A V / L_t; T_buffer = T_coolant + (V I / L_t) R_th, R_th = ln(r_o/r_i)/(2 pi k_s) + ln(r_p/r_o)/(2 pi k_p) + 1/(2 pi r_p h)',
                   'Q = pi r^4 dP / (8 eta L_t); v = (mu_eff + mu_eof) V/L_t + Q/(pi r^2)',
                   'sigma_x^2 = l_inj^2/12 + 2 D t + w_det^2/12; D = |mu_z| k T/(|z| e); D_eff = D + r^2 v_hd^2/(48 D)',
                   'A = sum_i 10^logeps_i(lambda) (b[cm]) c_i[mol/L]; indirect: A = -TR eps_p b c, TR = (z_A/z_P)(mu_P/mu_A)(mu_A-mu_C)/(mu_P-mu_C)'],
        parameters={
            'eof_reference_m2_v_s': dict(value=P['eof_reference_m2_v_s'], unit='m^2/(V*s)', meaning_zh='裸熔融石英高pH、10 mM时的电渗淌度（工程假设，±50%）',
                                         evidence='assumption', source_ids=['simulation_assumption'], uncertainty='±50%'),
            'eof_silanol_pk': dict(value=P['eof_silanol_pk'], unit='1', meaning_zh='电渗流随pH的S形中点（硅醇解离的工程代理）', evidence='assumption',
                                   source_ids=['simulation_assumption'], uncertainty='±1'),
            'ionic_strength_coefficient': dict(value=P['ionic_strength_coefficient'], unit='1',
                                               meaning_zh='淌度离子强度经验修正 exp(−a√(|z|I)) 的系数a（文献经验式形式，未逐项核对）', evidence='assumption',
                                               source_ids=['simulation_assumption'], uncertainty=None),
            'coolant_h_w_m2_k': dict(value=P['coolant_h_w_m2_k'], unit='W/(m^2*K)', meaning_zh='卡盒冷却液对毛细管外壁的对流换热系数（工程假设）',
                                     evidence='assumption', source_ids=['simulation_assumption'], uncertainty='±50%'),
            'baseline_noise_au': dict(value=P['baseline_noise_au'], unit='1', meaning_zh='检测器基线噪声标准差（合成，未按厂家指标标定）', evidence='assumption',
                                      source_ids=['simulation_assumption'], uncertainty=None),
            'lamp_warmup_s': dict(value=P['lamp_warmup_s'], unit='s', meaning_zh='氘灯暖机阈值（工程假设）', evidence='assumption',
                                  source_ids=['simulation_assumption'], uncertainty=None),
            'length_offset_m': dict(value=L_OFFSET_M, unit='m', meaning_zh='总长与到检测器长度之差10.2 cm', evidence='documented',
                                    source_ids=['manufacturer_manual'], uncertainty=None),
            'vial_max_volume_m3': dict(value=VIAL_MAX_M3, unit='m^3', meaning_zh='通用样品瓶最大装液1.5 mL（超量损坏压力系统）', evidence='documented',
                                       source_ids=['manufacturer_manual'], uncertainty=None)},
        source_ids=['mobility_library', 'manufacturer_primary', 'manufacturer_manual', 'simulation_assumption'],
        limitations=['极限淌度为25 °C、零离子强度值；离子强度、温度与pKa修正为工程近似；不含电迁移色散、样品堆积、吸附与毛细管壁相互作用。',
                     '电渗流为工程S形模型；涂层毛细管电渗按0处理。焦耳热只算平均温升，未算径向温度梯度。',
                     'BGE离子在电极处的电解与缓冲液耗竭只记累计电量，不改变瓶内组成。'])
    thermal = dict(component_ids=['capillary_cartridge', 'sample_tray_inlet', 'sample_tray_outlet'],
                   state_ids=['capillary.temperature_k', 'sample_storage.temperature_k'],
                   meaning_zh='冷却液与样品托盘温控按一阶响应趋近目标（断电回环境）', implementation='lumped',
                   equations=['T_next = T_goal + (T - T_goal) exp(-dt/tau)'],
                   parameters={'capillary_tau_s': dict(value=P['capillary_tau_s'], unit='s', meaning_zh='冷却液温控时间常数（假设）', evidence='assumption',
                                                       source_ids=['simulation_assumption'], uncertainty=None),
                               'sample_tau_s': dict(value=P['sample_tau_s'], unit='s', meaning_zh='样品托盘温控时间常数（假设）', evidence='assumption',
                                                    source_ids=['simulation_assumption'], uncertainty=None)},
                   source_ids=['manufacturer_primary', 'simulation_assumption'], limitations=['温控范围按厂家；动态响应为假设。'])
    mechs = {'electrophoresis': mech, 'thermostat': thermal}
    _ann.annotate_export('sciex_pa800', caps, slots, mechs, inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms=mechs)
