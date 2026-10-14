"""CHEM-194 INFICON UL1000 Fab mobile helium leak detector (dry scroll pump, counter-flow turbo, 180 deg magnetic sector mass spectrometer): process runtime.

Device (brochure; operating manual iina70en1-14): minimum detectable helium leak rate <5e-12 mbar l/s (vacuum, ULTRA) and <5e-8 mbar l/s
(sniffer); maximum inlet pressure 15 mbar; evacuation 25 m3/h; helium pumping speed at the inlet GROSS 8 / FINE 7 / ULTRA 2.5 l/s; autoranging GROSS
(15-1 mbar, 1e-6), FINE (2-0.4 mbar, 1e-10), ULTRA (<0.4 mbar, 5e-12); maximum display 0.1 mbar l/s; signal time constant <1 s; run-up 3 min; masses
2 / 3 / 4; START / STOP (long press vents after the vent delay) / ZERO keys, rotatable display; auto zero (internal background after >= 20 s in
standby), ZERO and I-ZERO (only on a stable signal; the zero level follows a falling signal down and then holds), triggers 1 / 2 (defaults 1e-9 / 1e-8 mbar l/s), alarm delay 30 s,
maximum evacuation time 30 min (W76), internal calibration with the built-in test leak (about 25 s; factor 0.1-10 else W81 / W82), external
calibration, sniffer mode (inlet 0.1-1 mbar else W62 / W63), machine factor; 1068 x 525 x 850 mm, 110 kg.

Process model (fixed 0.5 s grid anchored at t = 0, gas dynamics on 0.05 s sub-steps; SI units: Pa, m3, Pa m3/s for gas flows / leak rates):
  * Part under test: volume V, surface A of a library material (outgassing q(t) with t the pumping time since the last vent: metals ~1/t, polymers
    ~1/sqrt(t)), real leaks with a helium standard leak rate L (He at 1 bar into vacuum) at a position: each leak has a path (length, diameter) whose
    free-molecular conductance (Santeler transmission probability, CAS Eqs. 20-21) and volume delay the helium (response time); air enters by the
    same leaks (molecular flow: x sqrt(M_He/M_air); viscous above an engineering threshold); O-ring permeation leaks give a slow helium signal only.
  * Pumping: evacuation and GROSS / FINE by the scroll pump (25 m3/h), ULTRA by the turbo upper stage; helium is pumped at the documented inlet
    helium speeds; autoranging on the inlet pressure p1; venting brings p1 back to the atmosphere.
  * Detector: measured helium flow S_He p_He plus the internal background (rises with the helium throughput, relaxes to its floor) times the true
    sensitivity (temperature drift) over the calibrated sensitivity; first-order signal filter (I-CAL, < 1 s); ZERO / I-ZERO / auto-zero offsets;
    machine factor; range display limits and an absolute noise floor per range (the detection limit is not the reading error of every reading);
    triggers -> pass / reject; sniffer mode: helium from a pressurised part captured by the sniffer tip with an exponential distance law, the air
    helium (5.24 ppm) as background.
  * Ledgers: helium and total gas (Pa m3) close exactly per species."""
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.inficon_ul1000fab"
CATALOG_ID = "CHEM-194"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t2_vacuum/inficon_ul1000fab'
LIBS = {'gases': 'gas_conversion_library.json', 'outgassing': 'outgassing_library.json', 'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def gases():
    return _lib('gases')['entries']


def materials():
    return _lib('outgassing')['entries']


def instrument():
    return _lib('instrument')


DOC = {k: v['value'] for k, v in instrument()['documented'].items()}
ENG = {k: v['value'] for k, v in instrument()['engineering'].items()}
ENG_NOTE = {k: v['engineering_note_zh'] for k, v in instrument()['engineering'].items()}
ENG_UNIT = {k: v['unit'] for k, v in instrument()['engineering'].items()}
DT = 0.5
N_SUB = 10
R_GAS = 8.314462618
MBAR_L = 0.1                                  # 1 mbar l/s = 0.1 Pa m3/s
P_REF = 1e5                                   # standard leak rate: helium at 1 bar into vacuum
M_HE = gases()['helium']['molar_mass_kg_mol']
M_AIR = gases()['air']['molar_mass_kg_mol']
RANGES = {'GROSS': DOC['range_gross'], 'FINE': DOC['range_fine'], 'ULTRA': DOC['range_ultra']}
S_HE = DOC['helium_speed_m3_s']
S_ROUGH = DOC['roughing_speed_m3_s']
P_MAX_IN = DOC['max_inlet_pressure_pa']
DISPLAY_LIMIT = {'GROSS': 1e-6 * MBAR_L, 'FINE': 1e-10 * MBAR_L, 'ULTRA': 1e-12 * MBAR_L}   # displayed after ZERO (manual 4.2.2.4)
MAX_DISPLAY = DOC['max_display_ultra_pa_m3_s']
INTERNAL_V = 2e-4                             # inlet / valve block volume of the detector itself (m3; engineering)
STATES = ['off', 'run_up', 'standby', 'evacuation', 'measure', 'vent', 'calibration']
FAULTS = ['filament_1_failure', 'both_filaments_failure', 'turbo_failure', 'scroll_pump_failure', 'sniffer_capillary_blocked', 'sniffer_capillary_broken',
          'internal_test_leak_empty', 'helium_contamination']
ZERO_MODES = ['manual', 'i_zero', 'zero_at_ultra', 'disabled']
MAX_LEAKS = 12
MAX_EVENTS_LOG = 2000
MAX_LOG = 2000
C_PRIME_HE = 311.0                            # m3 s-1 m-2, unit-area conductance of helium at 293 K (CAS Table 8)
_M64 = (1 << 64) - 1


def _mix64(x):
    x = (x + 0x9E3779B97F4A7C15) & _M64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _M64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _M64
    return x ^ (x >> 31)


def _uni(seed, k, ch):
    return ((_mix64(_mix64(_mix64(seed & _M64) ^ (k & _M64)) ^ ch) >> 11) + 0.5) / 9007199254740992.0


def _gauss(seed, k, ch):
    u1, u2 = _uni(seed, k, 2 * ch), _uni(seed, k, 2 * ch + 1)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)


def santeler(L, R):
    """Transmission probability of a circular tube (CAS Eq. 21, Santeler)."""
    return 1.0 / (1.0 + 3.0 * L / (8.0 * R) * (1.0 + 1.0 / (3.0 * (1.0 + L / (7.0 * R)))))


def tube_conductance_he(L, d):
    """Free-molecular helium conductance of a tube (m3/s): C = C'_He x pi R^2 x tau (CAS Eq. 20)."""
    R = d / 2.0
    return C_PRIME_HE * math.pi * R * R * santeler(L, R)


def convert_leak(q_he, gas):
    """Equivalent leak rate of another gas in the free-molecular regime: Q_x = Q_He sqrt(M_He / M_x) (CAS Eq. 14)."""
    return q_he * gases()[gas]['molecular_flow_factor']


def outgassing_q(mat, t_s):
    """Specific outgassing rate (Pa m3 s-1 m-2) after t_s of pumping."""
    m = materials()[mat]
    t_h = max(t_s, 60.0) / 3600.0
    if m['time_law'] == 'inverse_time':
        return m['q_pa_m3_s_m2'] * (m['t_ref_h'] / t_h)
    if m['time_law'] == 'inverse_sqrt_time':
        return m['q_pa_m3_s_m2'] * math.sqrt(m['t_ref_h'] / t_h)
    return m['q_pa_m3_s_m2']


def mantissa_exponent(q_pa_m3_s):
    """Display form in mbar l/s: mantissa 1.0-9.9 and the decimal exponent."""
    if q_pa_m3_s is None or q_pa_m3_s <= 0:
        return None, None
    v = q_pa_m3_s / MBAR_L
    e = math.floor(math.log10(v))
    m = round(v / 10 ** e, 1)
    if m >= 10.0:
        m, e = 1.0, e + 1
    return m, e


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


def integer(lo, hi):
    return {'type': 'integer', 'x-unit': '1', 'minimum': lo, 'maximum': hi}


BOOL = {'type': 'boolean'}
TEXT = {'type': 'string'}
FREE_OBJ = dict(type='object')


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
        if 'pattern' in s and not re.fullmatch(s['pattern'], v):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'enum' in s and v not in s['enum']:
            raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.:-]+$')
PLANT_EXAMPLE = dict(ambient_k=295.15, pressure_pa=101325.0, temperature_at_calibration_k=295.15, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 283.15, 313.15), pressure_pa=number('Pa', 80000.0, 106000.0), temperature_at_calibration_k=number('K', 283.15, 313.15),
                 noise_seed=integer(0, 2 ** 31 - 1)))
LEAK = obj(dict(leak_id=SID, helium_leak_rate_pa_m3_s=number('Pa m3/s', 0.0, 100.0), path_length_m=number('m', 0.0, 20.0), path_diameter_m=number('m', 1e-4, 0.2),
                kind=enum(['channel', 'seal_permeation'])), optional=('path_length_m', 'path_diameter_m', 'kind'))
PART = obj(dict(part_id=SID, volume_m3=number('m3', 0.0, 10.0), surface_area_m2=number('m2', 0.0, 200.0), material=enum(sorted(materials())),
                leaks=array(LEAK, 0, MAX_LEAKS), helium_fill_pa=number('Pa', 0.0, 2e6)), optional=('leaks', 'helium_fill_pa'))
SETTINGS = obj(dict(trigger_1_pa_m3_s=number('Pa m3/s', 1e-14, 1.0), trigger_2_pa_m3_s=number('Pa m3/s', 1e-14, 1.0), mode=enum(['vacuum', 'sniff']),
                    zero_mode=enum(ZERO_MODES), background_suppression=BOOL, vent_delay_s=number('s', 0.0, 10.0), max_evacuation_s=number('s', 5.0, 1800.0),
                    alarm_delay_s=number('s', 0.0, 600.0), machine_factor=number('1', 1e-3, 1e4), data_log_period_s=number('s', 0.5, 3600.0),
                    max_evacuation_100mbar_s=number('s', 1.0, 540.0), calibration_request=BOOL),
               optional=('trigger_1_pa_m3_s', 'trigger_2_pa_m3_s', 'mode', 'zero_mode', 'background_suppression', 'vent_delay_s', 'max_evacuation_s', 'alarm_delay_s',
                         'machine_factor', 'data_log_period_s', 'max_evacuation_100mbar_s', 'calibration_request'))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'operation', 'part', 'alarm', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
_BODY = ['detector_body']
_PANEL = ['display_panel']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明环境温度与大气压、上次校准时的温度（灵敏度漂移基准）与噪声种子', 'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['mains_switch'], '主电源通/断（通电后约3 min启动到待机）', 'agent')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['mains_switch'], '主电源开关拨一下（等价于power.set取反）', 'agent')
cap('part.connect', PART, obj(dict(part_id=TEXT, notes=array(TEXT, 0, 8))),
    dict(part_id='chamber', volume_m3=0.01, surface_area_m2=0.3, material='stainless_unbaked', leaks=[dict(leak_id='weld', helium_leak_rate_pa_m3_s=1e-9, path_length_m=0.3, path_diameter_m=0.004)]),
    ['inlet_flange'], '把被测件接到DN25 KF入口（真空模式：关机、启动或待机且入口已放气；吸枪模式下只声明为吸枪检查对象，不接入口）：容积、内表面积与材料（出气）、真实漏孔（氦标准漏率：氦在1 bar、约20 °C下漏向真空的流量；到入口的通道长度与直径；通道或O圈渗透）；helium_fill_pa为被测件内的氦分压（绝压，吸枪检查时用）', 'agent', 'robot_skill')
cap('part.disconnect', obj({}), obj(dict(part_id=nullable(TEXT))), {}, ['inlet_flange'], '取下被测件（入口须已放气），入口恢复为堵头', 'agent', 'robot_skill')
cap('sniffer.connect', obj(dict(connected=BOOL)), obj(dict(connected=BOOL)), dict(connected=True), ['inlet_flange'], '在入口接上/取下SL200吸枪管（入口须已放气）', 'agent', 'robot_skill')
cap('measure.start', obj({}), obj(dict(state=TEXT)), {}, ['start_key'], 'START：开始抽空并按入口压力自动换档测量（真空模式）或开始吸枪测量；测量中再按为最大值保持', 'agent')
cap('measure.stop', obj(dict(hold_s=number('s', 0.0, 30.0)), optional=('hold_s',)), obj(dict(state=TEXT, vented=BOOL)), {}, ['stop_key'],
    'STOP：中断测量回到待机；按住超过放气延时（默认2 s）则入口放气', 'agent')
cap('zero.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL, offset_pa_m3_s=number('Pa m3/s'))), dict(enabled=True), ['zero_key'],
    'ZERO：把当前读数作为背景从后续读数中扣除（I·ZERO模式只在信号稳定时允许，并跟随下降的背景）；enabled=false相当于按住3 s撤销', 'agent')
cap('zero.toggle', obj({}), obj(dict(enabled=BOOL)), {}, ['zero_key'], '面板ZERO键：未启用时启用，已启用时保持（撤销需zero.set enabled=false）', 'agent')
cap('calibration.start', obj(dict(kind=enum(['internal', 'external']), test_leak_pa_m3_s=number('Pa m3/s', 1e-11, 1e-3)), optional=('test_leak_pa_m3_s',)),
    obj(dict(kind=TEXT, duration_s=number('s'))), dict(kind='internal'), ['cal_key', 'internal_test_leak'],
    '校准：内部（内置校准漏孔，入口堵住时约25 s，接着被测件时按容积延长）或外部（外部校准漏孔接在入口/被测件上）；因子超出0.1–10报警并保留旧因子', 'agent')
cap('spray.set', obj(dict(target=SID, on=BOOL)), obj(dict(target=TEXT, on=BOOL)), dict(target='weld', on=True), ['inlet_flange', 'mass_spectrometer'],
    '用氦喷枪对准被测件上的位置（漏孔标识或任意位置名）喷/停（SMART-Spray喷枪抽象）', 'agent', 'robot_skill')
cap('sniffer.position', obj(dict(target=SID, distance_m=number('m', 0.0, 1.0))), obj(dict(target=TEXT, distance_m=number('m'))), dict(target='weld', distance_m=0.002),
    ['inlet_flange'], '吸枪模式：把吸枪口移到被测件某位置附近（距离）', 'agent', 'robot_skill')
cap('settings.configure', SETTINGS, obj(dict(settings=FREE_OBJ)), dict(trigger_1_pa_m3_s=1e-10), ['soft_pads', 'display_panel'],
    '菜单设置：触发阈值1/2、真空/吸枪模式、零点模式（手动/I·ZERO/到ULTRA自动/禁用）、背景抑制、放气延时、最长抽空时间、报警延时、机器因子、数据记录周期', 'agent', 'configuration')
cap('display.rotate', obj(dict(angle_rad=number('rad', -1.5708, 1.5708))), obj(dict(angle_rad=number('rad'))), dict(angle_rad=0.5), ['display_panel'],
    '转动可旋转的控制面板（显示屏朝向）', 'agent', 'robot_skill')
cap('leakrate.convert', obj(dict(gas=enum(sorted(gases())), helium_leak_rate_pa_m3_s=number('Pa m3/s', 0.0, 1.0)), optional=('helium_leak_rate_pa_m3_s',)),
    obj(dict(gas=TEXT, helium_leak_rate_pa_m3_s=nullable(number('Pa m3/s')), equivalent_leak_rate_pa_m3_s=nullable(number('Pa m3/s')), factor=number('1'), regime=TEXT)),
    dict(gas='nitrogen'), _PANEL, '把（当前显示或给定的）氦漏率换算为其他气体/蒸气在分子流下的漏率（Q_x = Q_He·√(M_He/M_x)，模板库102种）', 'agent', 'analysis')
cap('datalog.read', obj(dict(last=integer(1, MAX_LOG)), optional=('last',)), obj(dict(records=array(FREE_OBJ, 0, MAX_LOG), period_s=number('s'))), dict(last=20), _PANEL,
    '读取RS232漏率时间序列（时间、状态、量程、入口压力、显示漏率、合格判定、报警）', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _BODY,
    '读取事件记录（agent看不到故障与真值事件）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(gas=FREE_OBJ, detector=FREE_OBJ, leaks=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, _BODY,
    '评测者读取过程真值：被测件总压、氦分压、出气负荷、各漏孔真实氦/空气流量与通道延迟、内部背景、真实灵敏度与校准误差', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '氦与总气体（Pa·m³）的守恒账', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='filament_1_failure', active=True), _BODY,
    '故障注入：灯丝1断（切到灯丝2，W45）、两灯丝断（E47）、涡轮泵故障（E52）、涡旋泵故障、吸枪毛细管堵（W62）/断（W63）、内置校准漏孔耗尽（W81）、质谱室氦污染（背景升高）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'auto_leak_test.run': 'Auto Leak Test模式需要选配的TC1000测试室（未选）。', 'remote.rc1000': 'RC1000遥控器为选件（未选）。',
    'mass.select_h2_3he': '质量数2（H₂）与3（³He）示踪：本资产只建氦（质量数4）。', 'external_pump.machine_factor_measurement': '外接辅助泵组与机器因子的实测（需外部泵）不建模；机器因子只作设置。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'detector_body', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'detector_body', '环境条件是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'mains_switch', '主电源是否接通')
slot('state.current', enum(STATES), '1', 'derived', 'display_panel', '工作状态：off、run_up（启动）、standby、evacuation（抽空）、measure、vent（放气）、calibration')
slot('state.remaining_s', nullable(number('s')), 's', 'derived', 'display_panel', '启动或校准剩余时间（其他状态为空）')
slot('range.current', nullable(enum(list(RANGES))), '1', 'derived', 'display_panel', '当前测量量程（GROSS/FINE/ULTRA；未测量为空）')
slot('pressure.inlet_pa', nullable(number('Pa')), 'Pa', 'sensor', 'inlet_flange', '入口压力p1（被测件侧；断电或启动中为空）')
slot('pressure.foreline_pa', nullable(number('Pa')), 'Pa', 'sensor', 'scroll_pump', '前级压力p2（涡轮泵与涡旋泵之间）')
slot('leak.display_pa_m3_s', nullable(number('Pa m3/s')), 'Pa m3/s', 'sensor', 'display_panel', '显示的氦漏率（已扣零点、乘机器因子，量程显示下限截断；未测量为空）')
slot('display.leak_mantissa', nullable(number('1')), '1', 'derived', 'lcd_digits', '显示屏漏率尾数（mbar·L/s表示，1.0–9.9；显示用，不是SI量）')
slot('display.leak_exponent', nullable(number('1')), '1', 'derived', 'lcd_digits', '显示屏漏率十的指数（mbar·L/s表示；显示用）')
slot('leak.status', enum(['none', 'pass', 'reject']), '1', 'derived', 'display_panel', '合格判定：测量中显示漏率高于触发阈值1为reject，否则pass；未测量为none')
slot('leak.trigger2_exceeded', BOOL, '1', 'derived', 'display_panel', '是否超过触发阈值2')
slot('alarm.audible', BOOL, '1', 'derived', 'display_panel', '声光报警（超过阈值1，且START后读数曾低于阈值1或已过报警延时；手册6.4.5）')
slot('zero.enabled', BOOL, '1', 'setpoint', 'zero_key', 'ZERO是否启用')
slot('zero.offset_pa_m3_s', number('Pa m3/s'), 'Pa m3/s', 'derived', 'zero_key', '当前扣除的背景（ZERO/自动零点/I·ZERO跟随）')
slot('signal.stable', BOOL, '1', 'derived', 'display_panel', '信号稳定标志（STABLE，I·ZERO可用）')
slot('calibration.factor', number('1'), '1', 'derived', 'internal_test_leak', '校准因子（相对出厂灵敏度）')
slot('display.angle_rad', number('rad'), 'rad', 'setpoint', 'display_panel', '可旋转控制面板的转角')
slot('settings.current', FREE_OBJ, '1', 'setpoint', 'display_panel', '当前菜单设置')
slot('part.connected', nullable(TEXT), '1', 'derived', 'inlet_flange', '入口上的被测件标识（堵头为空；吸枪为sniffer）')
slot('alarms.active', array(TEXT, 0, 16), '1', 'derived', 'display_panel', '当前报警与警告（W45、E47、E52、W62、W63、W76、W81、W82、校准请求等）')
slot('indicator.reject', BOOL, '1', 'derived', 'reject_led', '面板红色漏孔指示（判定reject）')
slot('indicator.ready', BOOL, '1', 'derived', 'ready_led', '面板绿色就绪/测量指示')
slot('truth.gas', FREE_OBJ, '1', 'process_truth', 'inlet_flange', '气体真值：被测件总压、氦分压、出气负荷、抽速、抽气时间', 'evaluator')
slot('truth.detector', FREE_OBJ, '1', 'process_truth', 'mass_spectrometer', '检测器真值：进入质谱的氦流量、内部背景、真实/校准灵敏度、未经扣除的信号', 'evaluator')
slot('truth.leaks', FREE_OBJ, '1', 'process_truth', 'inlet_flange', '各漏孔真值：氦与空气流量、通道导率与延迟、喷氦状态', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'detector_body', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'display.angle_rad', 'display.leak_mantissa', 'display.leak_exponent', 'indicator.reject', 'indicator.ready'}


# ---------------------------------------------------------------- runtime
class InficonUl1000fabRuntime:
    def __init__(self, instance_id='inficon_ul1000fab_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.powered = False
        self.state = 'off'
        self.timer = 0.0
        self.standby_s = 0.0
        self.measure_s = 0.0
        self.evac_s = 0.0
        self.range = None
        self.part = None
        self.sniffer = False
        self.p1 = None                                     # inlet / part total pressure (Pa)
        self.p_he = None                                   # helium partial pressure in the part (Pa)
        self.paths = {}                                    # leak id -> helium partial in the leak path (Pa)
        self.pump_s = 0.0                                  # pumping time since the last vent (outgassing clock)
        self.vent_on = False
        self.spray = {}
        self.sniff_target, self.sniff_d = None, 1.0
        self.sniff_part = None                             # sniffer mode: the pressurised object being sniffed (not on the inlet)
        self.bg = ENG['internal_background_floor_pa_m3_s']
        self.sig = 0.0                                     # filtered detector signal (Pa m3/s, before zero / machine factor)
        self.sig_hist = []
        self.zero_on, self.zero0, self.zero_t0, self.zero_tau = False, 0.0, 0.0, None
        self.auto_zero = 0.0
        self.s_cal = 1.0                                   # calibrated sensitivity (relative)
        self.cal_factor = 1.0
        self.cal = None
        self.angle = 0.0
        self.alarm_since = None
        self.settings = dict(trigger_1_pa_m3_s=DOC['trigger_1_pa_m3_s'], trigger_2_pa_m3_s=DOC['trigger_2_pa_m3_s'], mode='vacuum', zero_mode='manual',
                             background_suppression=True, vent_delay_s=DOC['vent_delay_s'], max_evacuation_s=DOC['max_evacuation_s'], alarm_delay_s=DOC['alarm_delay_s'],
                             machine_factor=1.0, data_log_period_s=1.0, max_evacuation_100mbar_s=30.0, calibration_request=False)
        self.filament = 1
        self.faults = set()
        self.warnings = {}
        self.events, self.events_dropped = [], 0
        self.log, self.next_log = [], 0.0
        self.revision = 0
        self.led = dict(he_initial=0.0, he_leak_in=0.0, he_permeation_in=0.0, he_vent_in=0.0, he_pumped=0.0, he_vent_out=0.0, gas_initial=0.0, gas_leak_in=0.0,
                        gas_outgassing=0.0, gas_vent_in=0.0, gas_pumped=0.0, gas_vent_out=0.0, he_removed_with_part=0.0, gas_removed_with_part=0.0, he_added_with_part=0.0,
                        gas_added_with_part=0.0)
        self._principals, self._requests, self._caller = {}, {}, None

    # ---- sessions
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
        return InficonUl1000fabSession(self, name)

    def _event(self, kind, code, message):
        if len(self.events) >= MAX_EVENTS_LOG:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message))

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self.powered:
            raise ActionRejected('POWER_OFF')

    def _need_ready(self):
        self._need_power()
        if self.state == 'run_up':
            raise ActionRejected('RUNNING_UP')
        if 'both_filaments_failure' in self.faults or 'turbo_failure' in self.faults:
            raise ActionRejected('DETECTOR_FAULT')

    # ---- gas system
    def _V(self):
        return INTERNAL_V + (self.part['volume_m3'] if self.part else 0.0)

    def _he_out(self, leak_id):
        """Helium partial pressure outside a leak: spray, else the air helium."""
        pa = self.plant['pressure_pa']
        frac = ENG['spray_he_fraction'] if self.spray.get(leak_id) else ENG['helium_in_air_mol_fraction']
        return frac * pa

    def _leaks(self):
        return self.part['leaks'] if (self.part and not self.sniffer) else []

    def _leak_cond(self, lk):
        """(helium conductance of the leak itself at the reference pressure, helium conductance of its path, path volume)."""
        c_leak = lk['helium_leak_rate_pa_m3_s'] / P_REF
        L, d = lk.get('path_length_m') or 0.0, lk.get('path_diameter_m') or 0.01
        if L <= 0.0:
            return c_leak, None, 0.0
        return c_leak, tube_conductance_he(L, d), math.pi * d * d / 4.0 * L

    @staticmethod
    def _air_cond(lk, c_leak, c_path):
        """Air conductance of a leak channel: viscous or molecular scaling of the helium conductance, limited by the path in series."""
        viscous = lk['helium_leak_rate_pa_m3_s'] > ENG['leak_viscous_threshold_pa_m3_s']
        c_air = c_leak * (ENG['air_viscosity_ratio'] if viscous else math.sqrt(M_HE / M_AIR))
        if c_path:                                                           # air is limited by the same path as the helium (series conductance)
            c_air = 1.0 / (1.0 / c_air + 1.0 / (c_path * math.sqrt(M_HE / M_AIR)))
        return c_air

    def _pump_speeds(self):
        """(total-gas pumping speed, helium pumping speed) at the inlet in the current state."""
        st = self.state
        if not self.powered or st in ('off', 'run_up', 'standby', 'vent'):
            return 0.0, 0.0
        if self.sniffer:
            return S_ROUGH, S_HE['FINE']
        if 'scroll_pump_failure' in self.faults:
            return 0.0, 0.0
        if st in ('evacuation',) or (st == 'calibration' and self.range is None):
            return S_ROUGH, S_ROUGH
        rng = self.range or 'GROSS'
        if rng == 'ULTRA':
            return ENG['air_speed_ultra_m3_s'], S_HE['ULTRA']
        return S_ROUGH, S_HE[rng]

    def _outgas(self):
        if not self.part or self.sniffer or self.part['surface_area_m2'] <= 0:
            return 0.0
        return outgassing_q(self.part['material'], self.pump_s) * self.part['surface_area_m2'] if self.p1 < 0.5 * self.plant['pressure_pa'] else 0.0

    # ---- physics
    def step(self, dt_s):
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = t_end
            self.t_grid = math.floor(t_end / DT + 1e-9) * DT
            return self.t
        while self.t_grid + DT <= t_end + 1e-9:
            self.t = self.t_grid
            self._tick(DT)
            self.t_grid += DT
            self.tick += 1
        self.t = t_end
        self._update_warnings()
        return self.t

    def _tick(self, h):
        pa = self.plant['pressure_pa']
        # ---- state timers
        if self.state == 'run_up':
            self.timer -= h
            if self.timer <= 1e-9:
                self.state, self.timer, self.standby_s = 'standby', 0.0, 0.0
                self._event('operation', 'READY', '启动完成，待机')
        elif self.state == 'standby':
            self.standby_s += h
        if self.state in ('evacuation', 'measure'):
            self.evac_s += h
        if self.state == 'measure':
            self.measure_s += h
        # ---- gas dynamics on sub-steps
        S_tot, S_he = self._pump_speeds()
        V = self._V()
        dt = h / N_SUB
        L = self.led
        for _ in range(N_SUB):
            self._outgas_now = self._outgas() if self.state in ('evacuation', 'measure', 'calibration') else 0.0
            # dp/dt = (B - (S + A) p) / V per species: conductances to fixed outside pressures (direct leaks, air leaks, vent) are taken implicitly (exact
            # exponential, stable for any conductance / volume); constant sources (outgassing, permeation, sniffer line, path outflow) go into B
            Bg, Ag, Bh, Ah = self._outgas_now, 0.0, 0.0, 0.0
            direct, air, path_out = [], [], 0.0
            for lk in self._leaks():
                c_leak, c_path, v_path = self._leak_cond(lk)
                if lk.get('kind') == 'seal_permeation':
                    target = lk['helium_leak_rate_pa_m3_s'] if self.spray.get(lk['leak_id']) else 0.0
                    lk['_perm'] = lk.get('_perm', 0.0) + (target - lk.get('_perm', 0.0)) * (1 - math.exp(-dt / ENG['elastomer_he_permeation_tau_s']))
                    Bh += lk['_perm']
                    L['he_permeation_in'] += lk['_perm'] * dt
                    continue
                c_air = self._air_cond(lk, c_leak, c_path)
                if self.p1 < pa:
                    air.append(c_air)
                    Ag += c_air
                    Bg += c_air * pa
                p_out = self._he_out(lk['leak_id'])
                if c_path:                                                   # helium crosses the leak into the path volume, then the path into the part (exact over the sub-step)
                    pp = self.paths.get(lk['leak_id'], self.p_he)
                    ctot = c_leak + c_path
                    p_eq = (c_leak * p_out + c_path * self.p_he) / ctot
                    k = ctot * dt / v_path
                    integ = p_eq * dt + (pp - p_eq) * v_path / ctot * (1.0 - math.exp(-k))     # integral of the path pressure over the sub-step
                    he_in = c_leak * (p_out * dt - integ)
                    out = c_path * (integ - self.p_he * dt)
                    self.paths[lk['leak_id']] = pp + (he_in - out) / v_path
                    L['he_leak_in'] += he_in
                    path_out += out / dt
                else:
                    direct.append((c_leak, p_out))
                    Ah += c_leak
                    Bh += c_leak * p_out
            Bh += path_out
            sniff_f = 0.0
            if self.sniffer and self.state == 'measure':                     # sniffer line: constant air flow through the capillary
                sniff_f = ENG['sniff_flow_pa_m3_s'] * self._capillary_factor()
                Bg += sniff_f
                Bh += sniff_f * ENG['helium_in_air_mol_fraction']
            cv = V / ENG['vent_tau_s'] if self.vent_on else 0.0               # vent valve: the inlet refills with room air (conductance V / tau)
            he_frac = ENG['helium_in_air_mol_fraction']
            Ag += cv
            Bg += cv * pa
            Ah += cv
            Bh += cv * pa * he_frac
            for key, B, A_, S in (('gas', Bg, Ag, S_tot), ('he', Bh, Ah, S_he)):
                p0 = self.p1 if key == 'gas' else self.p_he
                k_ = S + A_
                if k_ > 0:
                    p_eq = B / k_
                    e_ = math.exp(-k_ * dt / V)
                    p_new = p_eq + (p0 - p_eq) * e_
                    integ = p_eq * dt + (p0 - p_eq) * V / k_ * (1.0 - e_)             # integral of p over the sub-step
                else:
                    p_new = p0 + B * dt / V
                    integ = 0.5 * (p0 + p_new) * dt
                L[key + '_pumped'] += S * integ
                if key == 'gas':
                    for c in air:
                        L['gas_leak_in'] += c * (pa * dt - integ)
                    L['gas_leak_in'] += sniff_f * dt
                    L['gas_vent_in'] += cv * (pa * dt - integ)
                    self.p1 = p_new
                else:
                    for c, po in direct:
                        L['he_leak_in'] += c * (po * dt - integ)
                    L['he_leak_in'] += sniff_f * he_frac * dt
                    L['he_vent_in'] += cv * (pa * he_frac * dt - integ)
                    self.p_he = p_new
            L['gas_outgassing'] += self._outgas_now * dt
        if self.state in ('evacuation', 'measure', 'calibration') and self.p1 < 0.5 * pa:
            self.pump_s += h
        if self.vent_on and abs(self.p1 - pa) < 1.0:
            self.vent_on = False
            self.state = 'standby'
            self.standby_s = 0.0
            self.pump_s = 0.0
            self._event('operation', 'VENTED', '入口已放气')
        # ---- autoranging
        if self.state == 'calibration' and self.range is None:               # calibration from standby: pump the inlet down first (as START), then autorange
            self.evac_s += h
            if self.p1 < P_MAX_IN:
                self.range, self.range_s = 'GROSS', 0.0
            elif (self.p1 >= 1e4 and self.evac_s > self.settings['max_evacuation_100mbar_s']) or self.evac_s > self.settings['max_evacuation_s']:
                self.cal = None
                self.state, self.range = 'standby', None
                self.warnings_latch = 'W76'
                self._event('alarm', 'W76', '校准抽空超时（大漏或设置过短），校准中止')
        if self.state == 'evacuation' and not self.sniffer:
            if self.p1 < P_MAX_IN:
                self.state, self.range, self.measure_s, self.range_s = 'measure', 'GROSS', 0.0, 0.0
                self._event('operation', 'MEASURE_GROSS', '入口压力低于15 mbar：GROSS测量')
            elif (self.p1 >= 1e4 and self.evac_s > self.settings['max_evacuation_100mbar_s']) or self.evac_s > self.settings['max_evacuation_s']:
                self.state, self.range = 'standby', None                    # two-stage gross-leak check (manual: p1 < 100 mbar within 30 s, < 15 mbar within the maximum time)
                self.warnings_latch = 'W76'
                self._event('alarm', 'W76', '超过最长抽空时间（大漏或设置过短）')
        self.range_s = getattr(self, 'range_s', 0.0) + h
        if self.state in ('measure', 'calibration') and self.range is not None and not self.sniffer and (self.range_s >= ENG['range_dwell_s'] or self.p1 >= P_MAX_IN * 1.02):
            for rng, (lo, hi) in (('ULTRA', (None, RANGES['ULTRA']['p_max_pa'])), ('FINE', (RANGES['ULTRA']['p_max_pa'], RANGES['FINE']['p_max_pa'])),
                                  ('GROSS', (RANGES['FINE']['p_max_pa'], P_MAX_IN))):
                if self.p1 < hi * (0.98 if rng != self.range else 1.10) and (lo is None or self.p1 >= lo * (0.90 if rng == self.range else 1.0)):
                    if rng != self.range:
                        order = ['GROSS', 'FINE', 'ULTRA']
                        up = self.range in order and order.index(rng) > order.index(self.range)
                        if up and self.t - getattr(self, 'downgrade_t', -1e9) < ENG['range_hold_after_downgrade_s']:
                            break                                            # anti-hunting: no upgrade shortly after a downgrade
                        if not up:
                            self.downgrade_t = self.t
                        self.range, self.range_s = rng, 0.0
                        self._event('operation', 'RANGE_' + rng, f'换档到{rng}')
                        if rng == 'ULTRA' and self.state == 'measure' and self.settings['zero_mode'] == 'zero_at_ultra' and not self.zero_on:
                            self._do_zero()
                    break
            else:
                if self.p1 >= P_MAX_IN * 1.02:
                    self.range = None
                    if self.state == 'measure':
                        self.state = 'evacuation'
                    elif self.state == 'calibration' and self.cal and self.cal['stage'] != 'baseline':
                        prev = self.cal['prev_state']                          # test leak already open: the measurement is lost, the old factor stays
                        self.cal = None
                        self.state = 'evacuation' if prev == 'measure' else 'standby'
                        self._event('alarm', 'CAL_ABORTED', '校准中入口压力回升到15 mbar以上：校准中止，保留原校准因子')
                    elif self.state == 'calibration' and self.cal:
                        self.cal['t'] = 0.0                                    # baseline restarts after the pump-down
        # ---- detector signal (helium flow into the mass spectrometer)
        q_det = S_he * self.p_he if (self.state == 'measure' or (self.state == 'calibration' and self.range is not None)) else 0.0
        if self.sniffer and self.state == 'measure':
            q_det = self._sniff_signal()
        if self.cal and self.cal['kind'] == 'internal':
            q_det += self._test_leak_now() if self.cal['stage'] == 'open' else 0.0
        if 'helium_contamination' in self.faults:
            self.bg = max(self.bg, 1e-8 * MBAR_L)
        self.bg += (ENG['internal_background_floor_pa_m3_s'] - self.bg) * (1 - math.exp(-h / ENG['internal_background_tau_s'])) + ENG['internal_background_gain'] * q_det * h * 1e-3
        raw = (q_det + self.bg) * self._s_true() / self.s_cal if self.powered and (self.state == 'measure' or (self.state == 'calibration' and self.range is not None)) else 0.0
        if 'both_filaments_failure' in self.faults:
            raw = 0.0
        self.sig += (raw - self.sig) * (1 - math.exp(-h / (0.8 * DOC['signal_time_constant_s'])))
        if self.zero_on and self.sig - self.auto_zero < self.zero0:          # manual 4.2.2.4: the zero level follows a falling signal and then holds
            self.zero0 = max(self.sig - self.auto_zero, 0.0)
        if self.auto_zero > self.sig and self.state == 'measure':
            self.auto_zero = max(self.sig, 0.0)
        self.sig_hist.append((self.t + h, self.sig))
        del self.sig_hist[:-40]
        # ---- calibration routine
        if self.cal:
            self._cal_tick(h)
        if self.state == 'measure' and not getattr(self, 'below_t1', False) and self._status(self._display_value_at(self.tick + 1)) == 'pass':
            self.below_t1 = True                                             # manual 6.4.5: reading dropped below trigger 1 since START (checked every tick)
        # ---- data log
        if self.powered and self.t + h >= self.next_log - 1e-9:
            disp = self._display_value()
            self.log.append(dict(time_s=round(self.t + h, 3), state=self.state, range=self.range, inlet_pa=self._p1_reading(), leak_pa_m3_s=disp,
                                 status=self._status(disp), alarms=sorted(self.warnings)))
            del self.log[:-MAX_LOG]
            self.next_log = self.t + h + self.settings['data_log_period_s']

    def _capillary_factor(self):
        return 0.05 if 'sniffer_capillary_blocked' in self.faults else 5.0 if 'sniffer_capillary_broken' in self.faults else 1.0

    def _sniff_signal(self):
        """Sniffer mode: helium flow drawn through the line = air background + captured helium escaping from the pressurised part."""
        pa = self.plant['pressure_pa']
        flow = ENG['sniff_flow_pa_m3_s'] * self._capillary_factor()
        bgr = ENG['helium_in_air_mol_fraction'] * flow * (1.0 if pa > 0 else 0.0)
        cap_ = 0.0
        if self.sniff_part and self.sniff_target:
            fill = self.sniff_part.get('helium_fill_pa') or 0.0
            for lk in self.sniff_part['leaks']:
                if lk['leak_id'] == self.sniff_target and lk.get('kind', 'channel') == 'channel':
                    q_out = lk['helium_leak_rate_pa_m3_s'] * fill / P_REF
                    cap_ += q_out * math.exp(-self.sniff_d / ENG['sniff_capture_length_m'])
        return bgr + min(cap_, flow)

    def _s_true(self):
        drift = ENG['sensitivity_drift_per_k'] * (self.plant['ambient_k'] - self.plant['temperature_at_calibration_k'])
        f = 1.0 + drift
        if 'filament_1_failure' in self.faults:
            f *= 0.85                                                       # filament 2 has a different emission until recalibrated (engineering)
        return f

    def _test_leak_now(self):
        if 'internal_test_leak_empty' in self.faults:
            return 0.0
        return ENG['internal_test_leak_pa_m3_s'] * (1 + ENG['test_leak_tempco_per_k'] * (self.plant['ambient_k'] - 293.15))

    def _cal_tick(self, h):
        C = self.cal
        if self.range is None:                                               # still pumping the inlet down: the calibration clock has not started
            return
        C['t'] += h
        tau = self._V() / max(self._pump_speeds()[1], 1e-6)
        if C['kind'] == 'internal':
            if C['stage'] == 'recover':                                      # test leak closed: its helium tail decays, then a second baseline is taken
                if C['t'] >= C['t_recover']:
                    base1, t1 = self.sig, self.t + h                         # the baseline is interpolated between before and after (drifting background)
                    w = (C['t_open_end'] - C['t_base']) / max(t1 - C['t_base'], DT)
                    base_at_open = C['base'] + w * (base1 - C['base'])
                    nominal = ENG['internal_test_leak_pa_m3_s'] * (1 + ENG['test_leak_tempco_per_k'] * (self.plant['ambient_k'] - 293.15))   # temperature-compensated
                    self._finish_cal((C['sig_open'] - base_at_open) / nominal if nominal > 0 else 0.0)
            elif C['stage'] == 'baseline' and C['t'] >= max(10.0, 5 * tau) and (self._stable_cal() or C['t'] >= 120.0):
                C['base'] = self.sig                                         # baseline after the part's air helium has been pumped out
                C['t_base'] = self.t + h
                C['stage'], C['t'] = 'open', 0.0
                C['t_open'] = self.t + h
            elif C['stage'] == 'open' and C['t'] >= max(10.0, 5 * tau):
                C['sig_open'], C['t_open_end'] = self.sig, self.t + h
                C['t_recover'] = self._recover_time(max(self.sig - C['base'], 0.0), tau)
                C['stage'], C['t'] = 'recover', 0.0
        else:
            if C['t'] >= max(15.0, 5 * tau):
                measured = self.sig - C.get('base', 0.0)
                self._finish_cal(measured / C['nominal'] if C['nominal'] > 0 else 0.0)

    def _recover_time(self, amplitude, tau_gas):
        """Time for the test-leak tail (signal time constant and gas response) to fall below 0.1 x trigger 1, on the 0.5 s grid."""
        tau = max(0.8 * DOC['signal_time_constant_s'], tau_gas)
        t = tau * math.log(max(amplitude / (0.1 * self.settings['trigger_1_pa_m3_s']), 1.0))
        return min(max(math.ceil(t / DT - 1e-9) * DT, 2.0), ENG['cal_recover_max_s'])

    def _finish_cal(self, ratio):
        """ratio = displayed / true for the reference leak; the new calibration factor scales the sensitivity accordingly."""
        lo, hi = DOC['cal_factor_limits']
        new_factor = self.cal_factor * ratio if ratio > 0 else 0.0
        if not (lo <= new_factor <= hi):
            code = 'W81' if new_factor < lo else 'W82'
            self.warnings_latch = code
            self._event('alarm', code, '校准因子超出范围，保留旧因子：' + ('过低' if code == 'W81' else '过高'))
        else:
            self.s_cal *= ratio
            self.cal_factor = new_factor
            self.t_cal_k = self.plant['ambient_k']
            self._event('operation', 'CALIBRATED', f'校准完成，因子{new_factor:.3f}')
        prev = self.cal['prev_state']
        self.cal = None
        self.state = prev if prev in ('measure', 'standby') else 'standby'
        if self.state == 'standby':
            self.range = None

    def _do_zero(self):
        self.zero_on = True
        self.zero0 = max(self.sig - self.auto_zero, 0.0)
        self.zero_t0 = self.t

    def _zero_offset(self):
        return self.auto_zero + (self.zero0 if self.zero_on else 0.0)

    def _stable_cal(self):
        hist = [v for _, v in self.sig_hist[-10:]]
        return len(hist) >= 10 and abs(hist[-1] - hist[0]) <= 0.02 * ENG['internal_test_leak_pa_m3_s']

    def _cal_duration(self):
        """Estimated internal calibration time from now: pump-down (from standby), baseline, test leak open, recovery after closing."""
        return self._cal_remaining(fresh=True)

    def _cal_remaining(self, fresh=False):
        C = self.cal
        tau = self._V() / max(S_HE['ULTRA'] if self.part is not None else S_HE['FINE'], 1e-6)
        base_d, open_d = max(10.0, 5 * tau), max(10.0, 5 * tau)
        rec_d = self._recover_time(ENG['internal_test_leak_pa_m3_s'], tau if self.part is not None else 0.0)   # tail of the test leak (blanked inlet: total about 25 s, manual)
        if C['kind'] != 'internal':
            return max(max(15.0, 5 * self._V() / S_HE['ULTRA']) - (0.0 if fresh else C['t']), 0.0)
        evac = 0.0
        if self.range is None and self.p1 is not None and self.p1 > P_MAX_IN:   # pump-down estimate to 15 mbar (roughing speed), on the 0.5 s grid
            evac = math.ceil(self._V() / S_ROUGH * math.log(self.p1 / P_MAX_IN) / DT - 1e-9) * DT
        t = 0.0 if fresh or self.range is None else C['t']
        order = [None, 'GROSS', 'FINE', 'ULTRA']                              # remaining autoranging steps (one dwell each) before the signal is stable
        dwell = max(order.index('ULTRA') - max(order.index(self.range), 1), 0) * ENG['range_dwell_s'] if self.part is not None else 0.0
        if C['stage'] == 'baseline':
            return evac + dwell + max(base_d - t, 0.0) + open_d + rec_d
        if C['stage'] == 'open':
            return max(open_d - t, 0.0) + rec_d
        return max(C.get('t_recover', rec_d) - t, 0.5)

    def _stable(self):
        hist = [v for _, v in self.sig_hist[-10:]]
        if len(hist) < 10:
            return False
        drift = abs(hist[-1] - hist[0]) / ((len(hist) - 1) * DT)               # signal change per second
        return drift * 10.0 < 0.5 * self.settings['trigger_1_pa_m3_s']

    def _display_value(self):
        return self._display_value_at(self.tick)

    def _display_value_at(self, tick):
        if not self.powered or self.state != 'measure' or 'both_filaments_failure' in self.faults:
            return None
        rng = 'FINE' if self.sniffer else self.range
        mf = 1.0 if self.sniffer else self.settings['machine_factor']
        v = (self.sig - self._zero_offset()) * mf
        floor_abs = ENG['detection_noise_floor_fraction'] * (DOC['min_leak_sniffer_pa_m3_s'] if self.sniffer else RANGES[rng]['limit_pa_m3_s'])
        v += ENG['signal_noise_rel'] * abs(v) * _gauss(self.seed, tick, 1) + floor_abs * _gauss(self.seed, tick, 2)
        lim = DISPLAY_LIMIT[rng] if not self.sniffer else DOC['min_leak_sniffer_pa_m3_s']
        return float(min(max(v, lim), MAX_DISPLAY))

    def _status(self, disp):
        if disp is None:
            return 'none'
        return 'reject' if disp > self.settings['trigger_1_pa_m3_s'] else 'pass'

    def _p1_reading(self):
        if not self.powered or self.state == 'run_up' or self.p1 is None:
            return None
        return float(self.p1)

    def _update_warnings(self):
        W = {}
        if self.plant is None or not self.powered:
            self.warnings = W
            return
        if 'filament_1_failure' in self.faults:
            W['W45'] = '灯丝1无法发射，已切换到灯丝2'
        if 'both_filaments_failure' in self.faults:
            W['E47'] = '两个灯丝都无法发射'
        if 'turbo_failure' in self.faults:
            W['E52'] = '涡轮分子泵转速过低'
        if self.sniffer and self.state == 'measure' and self.measure_s >= 5.0:
            lo, hi = DOC['sniff_inlet_window_pa']
            if self.p1 < lo:
                W['W62'] = '吸枪毛细管流量过低（入口压力低于0.1 mbar）'
            elif self.p1 > hi:
                W['W63'] = '吸枪毛细管断（入口压力高于1 mbar）'
        latch = getattr(self, 'warnings_latch', None)
        if latch:
            W[latch] = {'W76': '超过最长抽空时间', 'W81': '校准因子过低', 'W82': '校准因子过高'}[latch]
        if self.settings['calibration_request'] and abs(self.plant['ambient_k'] - self.t_cal_k) > 5.0:
            W['W80'] = '请校准（环境温度与上次校准相差超过5 K）'
        disp = self._display_value()
        if self._status(disp) == 'reject':
            if self.alarm_since is None:
                self.alarm_since = self.t
        else:
            self.alarm_since = None
            if self._status(disp) == 'pass':
                self.below_t1 = True
        self.warnings = W

    def _audible(self):
        """Manual 6.4.5: after START the alarm sounds once the leak rate has dropped below trigger 1 or the alarm delay has elapsed."""
        return bool(getattr(self, 'below_t1', False)) or self.t - getattr(self, 'start_t', self.t) >= self.settings['alarm_delay_s']

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'datalog.read', 'leakrate.convert'):
            self.revision += 1
        if capability_id != 'sim.step':
            self._update_warnings()
        return out

    def _cmd_plant_configure(self, ambient_k, pressure_pa, temperature_at_calibration_k, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(ambient_k=ambient_k, pressure_pa=pressure_pa, temperature_at_calibration_k=temperature_at_calibration_k)
        if first:
            self.t_cal_k = temperature_at_calibration_k
        self.seed = noise_seed
        if first:
            self.p1 = pressure_pa
            self.p_he = pressure_pa * ENG['helium_in_air_mol_fraction']
            self.led['gas_initial'] = self.p1 * self._V()
            self.led['he_initial'] = self.p_he * self._V()
        self._event('system', 'PLANT_CONFIGURED', '环境条件已声明')
        return dict(configured=True)

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not self.powered:
            self.powered = True
            self.state, self.timer = 'run_up', DOC['run_up_s']
            self.next_log = self.t
            self._event('system', 'POWER_ON', '通电，启动（约3 min）')
        elif not on and self.powered:
            self.powered = False
            self.state, self.range, self.cal, self.vent_on = 'off', None, None, False
            self.zero_on, self.auto_zero = False, 0.0
            self._event('system', 'POWER_OFF', '断电')
        return dict(on=self.powered)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(on=not self.powered)

    def _vented_inlet(self):
        return self.p1 is not None and self.p1 > 0.95 * self.plant['pressure_pa'] and self.state in ('off', 'run_up', 'standby')

    def _cmd_part_connect(self, part_id, volume_m3, surface_area_m2, material, leaks=None, helium_fill_pa=None):
        self._need_plant()
        if self.part is not None or (self.sniffer and self.sniff_part is not None):
            raise ActionRejected('INLET_OCCUPIED')
        if not self.sniffer and not self._vented_inlet():
            raise ActionRejected('INLET_NOT_VENTED')
        ids = [lk['leak_id'] for lk in (leaks or [])]
        if len(set(ids)) != len(ids):
            raise ActionRejected('DUPLICATE_LEAK')
        if self.sniffer:                                                     # sniffer mode: the part stays off the inlet; it is the object being sniffed
            self.sniff_part = dict(part_id=part_id, volume_m3=float(volume_m3), surface_area_m2=float(surface_area_m2), material=material,
                                   leaks=[dict(lk, kind=lk.get('kind', 'channel')) for lk in (leaks or [])], helium_fill_pa=float(helium_fill_pa or 0.0))
            notes = ['吸枪模式：被测件不接入口，作为吸枪检查对象（漏孔按充氦压力helium_fill_pa向外漏氦；用sniffer.position把吸枪口移到漏孔附近）']
            if not helium_fill_pa:
                notes.append('未充氦（helium_fill_pa=0）：吸枪只看到空气中的氦背景')
            self._event('part', 'SNIFF_OBJECT', f'吸枪检查对象{part_id}')
            return dict(part_id=part_id, notes=notes)
        pa = self.plant['pressure_pa']
        V0 = self._V()
        self.part = dict(part_id=part_id, volume_m3=float(volume_m3), surface_area_m2=float(surface_area_m2), material=material,
                         leaks=[dict(lk, kind=lk.get('kind', 'channel')) for lk in (leaks or [])], helium_fill_pa=float(helium_fill_pa or 0.0))
        dV = self._V() - V0
        self.led['gas_added_with_part'] += pa * dV
        self.led['he_added_with_part'] += pa * ENG['helium_in_air_mol_fraction'] * dV
        self.p1 = (self.p1 * V0 + pa * dV) / self._V()
        self.p_he = (self.p_he * V0 + pa * ENG['helium_in_air_mol_fraction'] * dV) / self._V()
        self.paths = {}
        for lk in self._leaks():
            c_leak, c_path, v_path = self._leak_cond(lk)
            if c_path:
                self.paths[lk['leak_id']] = pa * ENG['helium_in_air_mol_fraction']
                self.led['he_added_with_part'] += self.paths[lk['leak_id']] * v_path
        self.pump_s = 0.0
        notes = []
        if any(lk['helium_leak_rate_pa_m3_s'] > ENG['leak_viscous_threshold_pa_m3_s'] for lk in self.part['leaks']):
            notes.append('含大漏（>1e-4 mbar·L/s）：可能抽不到15 mbar（W76）')
        if volume_m3 > 0.05:
            notes.append('大容积：抽空与氦响应时间长（响应时间约V/S_He）')
        if materials()[material]['time_law'] == 'inverse_sqrt_time':
            notes.append('聚合物材料：出气按1/√t下降，到达ULTRA可能需要较长抽气时间；聚合物对氦渗透大')
        self._event('part', 'PART_CONNECTED', f'接上被测件{part_id}')
        return dict(part_id=part_id, notes=notes)

    def _remove_inlet_contents(self):
        V0 = self._V()
        Vp = self.part['volume_m3'] if self.part else 0.0
        self.led['gas_removed_with_part'] += self.p1 * Vp
        self.led['he_removed_with_part'] += self.p_he * Vp
        for lk in self._leaks():
            c_leak, c_path, v_path = self._leak_cond(lk)
            if c_path:
                self.led['he_removed_with_part'] += self.paths.get(lk['leak_id'], 0.0) * v_path
        self.part = None
        self.paths = {}
        del V0

    def _cmd_part_disconnect(self):
        self._need_plant()
        if self.sniffer and self.sniff_part is not None:                     # sniffer mode: release the sniffed object (nothing on the inlet changes)
            pid, self.sniff_part, self.sniff_target = self.sniff_part['part_id'], None, None
            self._event('part', 'SNIFF_OBJECT_REMOVED', f'移开吸枪检查对象{pid}')
            return dict(part_id=pid)
        if self.part is None:
            raise ActionRejected('NO_PART')
        if not self._vented_inlet():
            raise ActionRejected('INLET_NOT_VENTED')
        pid = self.part['part_id']
        self._remove_inlet_contents()
        self.spray = {}
        self._event('part', 'PART_DISCONNECTED', f'取下被测件{pid}')
        return dict(part_id=pid)

    def _cmd_sniffer_connect(self, connected):
        self._need_plant()
        if connected and (self.part is not None and not self.sniffer):
            raise ActionRejected('INLET_OCCUPIED')
        if not self._vented_inlet():
            raise ActionRejected('INLET_NOT_VENTED')
        self.sniffer = bool(connected)
        if not self.sniffer:
            self.sniff_part, self.sniff_target = None, None                  # the sniffed object belongs to the sniffer set-up
        return dict(connected=self.sniffer)

    def _cmd_measure_start(self):
        self._need_ready()
        if self.state in ('evacuation', 'measure'):
            return dict(state=self.state)                                    # START again: maximum hold (display only; not modelled separately)
        if self.state == 'calibration':
            raise ActionRejected('CALIBRATING')
        if self.settings['mode'] == 'sniff' and not self.sniffer:
            raise ActionRejected('NO_SNIFFER')
        if self.settings['mode'] == 'vacuum' and self.sniffer:
            raise ActionRejected('SNIFFER_IN_VACUUM_MODE')
        if self.settings['background_suppression'] and self.standby_s >= DOC['autozero_standby_s']:
            self.auto_zero = self.bg * self._s_true() / self.s_cal          # internal background measured in standby, subtracted from now on
        else:
            self.auto_zero = 0.0
        if self.vent_on:                                                     # START while venting: the vent valve closes and the part is evacuated
            self.vent_on = False
            self._event('operation', 'VENT_CLOSED', 'START：放气阀关闭，开始抽空')
        self.evac_s, self.measure_s = 0.0, 0.0
        self.start_t, self.below_t1 = self.t, False
        self.downgrade_t = -1e9                                              # the anti-hunting hold belongs to one measurement
        self.warnings_latch = None
        if self.sniffer:
            self.state, self.range = 'measure', 'FINE'
        else:
            self.state, self.range = 'evacuation', None
        self._event('operation', 'START', '开始测量')
        return dict(state=self.state)

    def _cmd_measure_stop(self, hold_s=None):
        self._need_power()
        if self.state == 'run_up':
            raise ActionRejected('RUNNING_UP')
        vent = (hold_s or 0.0) > self.settings['vent_delay_s']
        if self.cal:
            self.cal = None
        self.state, self.range = 'standby', None
        if self.sniffer:                                                     # the sniffer line refills the inlet to the atmosphere through the capillary (lumped)
            pa = self.plant['pressure_pa']
            V = self._V()
            self.led['gas_vent_in'] += (pa - self.p1) * V
            self.led['he_vent_in'] += (pa - self.p1) * V * ENG['helium_in_air_mol_fraction']
            self.p1, self.p_he = pa, self.p_he + (pa - self.p1) * ENG['helium_in_air_mol_fraction']
        self.standby_s = 0.0
        self.zero_on = False
        self.alarm_since = None
        if vent:
            self.vent_on = True
            self.state = 'vent'
            self._event('operation', 'VENT', '入口放气')
        return dict(state=self.state, vented=vent)

    def _cmd_zero_set(self, enabled):
        self._need_ready()
        mode = self.settings['zero_mode']
        if enabled:
            if mode == 'disabled':
                raise ActionRejected('ZERO_DISABLED')
            if self.state != 'measure':
                raise ActionRejected('NOT_MEASURING')
            if mode == 'i_zero' and not self._stable():
                raise ActionRejected('SIGNAL_NOT_STABLE')
            self._do_zero()
        else:
            self.zero_on = False
        return dict(enabled=self.zero_on, offset_pa_m3_s=self._zero_offset())

    def _cmd_zero_toggle(self):
        if self.zero_on:
            return dict(enabled=True)
        return dict(enabled=self._cmd_zero_set(enabled=True)['enabled'])

    def _cmd_calibration_start(self, kind, test_leak_pa_m3_s=None):
        self._need_ready()
        if self.state not in ('standby', 'measure'):
            raise ActionRejected('NOT_READY_FOR_CALIBRATION')
        if self.sniffer:
            raise ActionRejected('SNIFFER_IN_VACUUM_MODE')
        if kind == 'external':
            if test_leak_pa_m3_s is None:
                raise ActionRejected('MISSING_SETTING')
            if self.state != 'measure':
                raise ActionRejected('NOT_MEASURING')
        prev = self.state
        self.evac_s = 0.0
        if getattr(self, 'warnings_latch', None) in ('W76', 'W81', 'W82'):
            self.warnings_latch = None                                       # a new calibration attempt replaces the previous evacuation / calibration warning
        self.cal = dict(kind=kind, t=0.0, t0=self.t, stage='baseline' if kind == 'internal' else 'open', prev_state=prev, nominal=test_leak_pa_m3_s or 0.0, base=0.0)
        if kind == 'external':
            self.cal['base'] = 0.0
        if prev == 'standby':
            self.state, self.range = 'calibration', None
        else:
            self.state = 'calibration'
        dur = self._cal_duration() if kind == 'internal' else max(15.0, 5 * self._V() / S_HE['ULTRA'])
        self.cal['duration'] = dur
        self._event('operation', 'CAL_START', f'开始{kind}校准')
        return dict(kind=kind, duration_s=dur)

    def _cmd_spray_set(self, target, on):
        self._need_plant()
        if self.part is None or self.sniffer:
            raise ActionRejected('NO_PART')
        self.spray[target] = bool(on)
        return dict(target=target, on=bool(on))

    def _cmd_sniffer_position(self, target, distance_m):
        self._need_plant()
        if not self.sniffer:
            raise ActionRejected('NO_SNIFFER')
        self.sniff_target, self.sniff_d = target, float(distance_m)
        return dict(target=target, distance_m=float(distance_m))

    def _cmd_settings_configure(self, **kw):
        self._need_power()
        if self.state in ('evacuation', 'measure', 'calibration') and 'mode' in kw and kw['mode'] != self.settings['mode']:
            raise ActionRejected('MEASURING')
        new = dict(self.settings, **{k: v for k, v in kw.items() if v is not None})
        if new['trigger_2_pa_m3_s'] < new['trigger_1_pa_m3_s']:
            raise ActionRejected('TRIGGER_ORDER')
        self.settings = new
        return dict(settings=dict(self.settings))

    def _cmd_display_rotate(self, angle_rad):
        self._need_plant()
        self.angle = float(angle_rad)
        return dict(angle_rad=self.angle)

    def _cmd_leakrate_convert(self, gas, helium_leak_rate_pa_m3_s=None):
        q = helium_leak_rate_pa_m3_s
        if q is None:
            q = self._display_value() if self.plant else None
        f = gases()[gas]['molecular_flow_factor']
        return dict(gas=gas, helium_leak_rate_pa_m3_s=q, equivalent_leak_rate_pa_m3_s=None if q is None else q * f, factor=f, regime='free_molecular')

    def _cmd_datalog_read(self, last=None):
        self._need_power()
        return dict(records=copy.deepcopy(self.log[-(last or MAX_LOG):]), period_s=self.settings['data_log_period_s'])

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s - 1e-9], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        v = self.read('evaluator')
        return dict(gas=v['truth.gas'], detector=v['truth.detector'], leaks=v['truth.leaks'], faults=v['truth.faults'])

    def _ledger(self):
        L = dict(self.led)
        V = self._V()
        path_he = 0.0
        for lk in self._leaks():
            c_leak, c_path, v_path = self._leak_cond(lk)
            if c_path:
                path_he += self.paths.get(lk['leak_id'], 0.0) * v_path
        L['he_in_volume'] = (self.p_he or 0.0) * V + path_he
        L['gas_in_volume'] = (self.p1 or 0.0) * V
        return L

    def _cmd_ledger_read(self):
        return dict(ledger=self._ledger())

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}：{"注入" if active else "清除"}')
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- read
    def read(self, visibility='agent'):
        pw = self.powered and self.plant is not None
        disp = self._display_value() if self.plant else None
        m, e = mantissa_exponent(disp)
        out = {'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.on': self.powered, 'state.current': self.state,
               'state.remaining_s': self.timer if self.state == 'run_up' else (self._cal_remaining() if self.cal else None)}
        out['range.current'] = self.range if (self.state == 'measure') else None
        p1 = self._p1_reading() if self.plant else None
        out['pressure.inlet_pa'] = None if p1 is None else round(p1 * (1 + 0.02 * _gauss(self.seed, self.tick, 3)), 6)
        out['pressure.foreline_pa'] = (ENG['foreline_pressure_measure_pa'] if self.state in ('measure', 'evacuation', 'calibration') else 5.0) if pw and self.state != 'run_up' else None
        out['leak.display_pa_m3_s'] = disp
        out['display.leak_mantissa'] = m
        out['display.leak_exponent'] = e
        out['leak.status'] = self._status(disp)
        out['leak.trigger2_exceeded'] = disp is not None and disp > self.settings['trigger_2_pa_m3_s']
        out['alarm.audible'] = bool(pw and out['leak.status'] == 'reject' and self._audible())
        out['zero.enabled'] = self.zero_on
        out['zero.offset_pa_m3_s'] = self._zero_offset()
        out['signal.stable'] = self._stable() if self.state == 'measure' else False
        out['calibration.factor'] = self.cal_factor
        out['display.angle_rad'] = self.angle
        out['settings.current'] = dict(self.settings)
        out['part.connected'] = 'sniffer' if self.sniffer else (self.part['part_id'] if self.part else None)
        out['alarms.active'] = sorted(self.warnings)
        out['indicator.reject'] = bool(pw and out['leak.status'] == 'reject')
        out['indicator.ready'] = bool(pw and self.state in ('standby', 'measure'))
        if visibility != 'agent':
            S_tot, S_he = self._pump_speeds() if self.plant else (0.0, 0.0)
            out['truth.gas'] = dict(total_pressure_pa=self.p1, helium_partial_pa=self.p_he, outgassing_pa_m3_s=self._outgas() if self.plant and self.p1 is not None else 0.0,
                                    pumping_speed_total_m3_s=S_tot, pumping_speed_helium_m3_s=S_he, pumping_time_s=self.pump_s, volume_m3=self._V(),
                                    response_time_s=(self._V() / S_he) if S_he > 0 else None, vent_open=self.vent_on)
            out['truth.detector'] = dict(helium_flow_to_ms_pa_m3_s=S_he * (self.p_he or 0.0), internal_background_pa_m3_s=self.bg, sensitivity_true=self._s_true() if self.plant else None,
                                         sensitivity_calibrated=self.s_cal, raw_signal_pa_m3_s=self.sig, filament=2 if 'filament_1_failure' in self.faults else 1,
                                         calibration=None if not self.cal else dict(kind=self.cal['kind'], stage=self.cal['stage'], t_s=self.cal['t']))
            lks = {}
            if self.part and self.plant:
                pa = self.plant['pressure_pa']
                for lk in self.part['leaks']:
                    c_leak, c_path, v_path = self._leak_cond(lk)
                    lks[lk['leak_id']] = dict(kind=lk.get('kind', 'channel'), helium_leak_rate_pa_m3_s=lk['helium_leak_rate_pa_m3_s'], sprayed=bool(self.spray.get(lk['leak_id'])),
                                              path_conductance_he_m3_s=c_path, path_delay_s=(v_path / c_path) if c_path else 0.0,
                                              helium_in_path_pa=self.paths.get(lk['leak_id']), helium_out_pa=self._he_out(lk['leak_id']),
                                              air_inflow_pa_m3_s=0.0 if lk.get('kind') == 'seal_permeation' else
                                              self._air_cond(lk, c_leak, c_path) * max(pa - (self.p1 if self.p1 is not None else pa), 0.0))
            out['truth.leaks'] = lks
            out['truth.faults'] = sorted(self.faults)
        return out


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
class InficonUl1000fabSession:
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
        meta = copy.deepcopy(export_inficon_ul1000fab_contract_metadata())
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

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = _jsonable(h.read(self._role))
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None)
                                  for k in ids})

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
            h._caller = self._principal
            try:
                result = _jsonable(h.invoke(capability_id, **arguments))
            finally:
                h._caller = None
            if self._role == 'agent' and capability_id == 'events.read':
                result = dict(events=[e for e in result['events'] if e['kind'] not in ('fault', 'truth')], dropped=result['dropped'])
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None, status='succeeded',
                            state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=copy.deepcopy(result), observations={}, error=None)
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
        raise ActionRejected('ACCESS_DENIED')

    def poll(self, operation_id):
        raise ActionRejected('UNKNOWN_OPERATION')

    def cancel(self, operation_id, request_id):
        raise ActionRejected('UNKNOWN_OPERATION')


# ---------------------------------------------------------------- contract metadata
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值或评测者能力）',
    'PLANT_REQUIRED': '尚未声明环境条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '检漏仪未通电', 'RUNNING_UP': '启动中（约3 min）',
    'DETECTOR_FAULT': '质谱或涡轮泵故障（E47/E52），不能测量', 'INLET_OCCUPIED': '入口上已有被测件或吸枪（吸枪模式下已声明检查对象）', 'INLET_NOT_VENTED': '入口未放气（先STOP并按住放气）',
    'DUPLICATE_LEAK': '漏孔标识重复', 'NO_PART': '入口上没有被测件', 'CALIBRATING': '校准进行中', 'NO_SNIFFER': '吸枪模式但没有接吸枪管',
    'SNIFFER_IN_VACUUM_MODE': '真空模式下接的是吸枪管（或吸枪模式下不能做真空校准）', 'ZERO_DISABLED': 'ZERO功能被禁用', 'NOT_MEASURING': '不在测量状态',
    'SIGNAL_NOT_STABLE': 'I·ZERO：信号不稳定（漂移大于0.5×触发阈值1），不能置零', 'NOT_READY_FOR_CALIBRATION': '只能在待机或测量状态校准',
    'MISSING_SETTING': '缺少必需参数（外部校准须给校准漏孔漏率）', 'MEASURING': '测量中不能切换真空/吸枪模式', 'TRIGGER_ORDER': '触发阈值2不能低于阈值1',
}
_P = ['PLANT_REQUIRED']
_PW = _P + ['POWER_OFF']
_RD = _PW + ['RUNNING_UP', 'DETECTOR_FAULT']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': _P, 'power.toggle': _P,
    'part.connect': _P + ['INLET_OCCUPIED', 'INLET_NOT_VENTED', 'DUPLICATE_LEAK'], 'part.disconnect': _P + ['NO_PART', 'INLET_NOT_VENTED'],
    'sniffer.connect': _P + ['INLET_OCCUPIED', 'INLET_NOT_VENTED'],
    'measure.start': _RD + ['CALIBRATING', 'NO_SNIFFER', 'SNIFFER_IN_VACUUM_MODE'], 'measure.stop': _PW + ['RUNNING_UP'],
    'zero.set': _RD + ['ZERO_DISABLED', 'NOT_MEASURING', 'SIGNAL_NOT_STABLE'], 'zero.toggle': _RD + ['ZERO_DISABLED', 'NOT_MEASURING', 'SIGNAL_NOT_STABLE'],
    'calibration.start': _RD + ['NOT_READY_FOR_CALIBRATION', 'SNIFFER_IN_VACUUM_MODE', 'MISSING_SETTING', 'NOT_MEASURING'],
    'spray.set': _P + ['NO_PART'], 'sniffer.position': _P + ['NO_SNIFFER'], 'settings.configure': _PW + ['MEASURING', 'TRIGGER_ORDER'],
    'display.rotate': _P, 'leakrate.convert': [], 'datalog.read': _PW, 'events.read': [], 'truth.inspect': [], 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子；之后再声明须给同一种子', ['plant.configured']),
    'power.set': ('断电时测量、校准与放气中断', ['power.on']), 'power.toggle': ('同power.set', ['power.on']),
    'part.connect': ('真空模式：入口为空且已放气（关机、启动、待机且入口接近大气压），被测件接到入口；吸枪模式（已接吸枪）：被测件不接入口而声明为吸枪检查对象（不需放气，只能声明一个）；漏孔标识唯一', ['part.connected', 'state.current', 'pressure.inlet_pa']),
    'part.disconnect': ('真空模式：入口有被测件且已放气；吸枪模式：移开已声明的检查对象', ['part.connected', 'state.current', 'pressure.inlet_pa']),
    'sniffer.connect': ('入口为空（或已是吸枪）且已放气；取下吸枪时一并清除吸枪检查对象', ['part.connected', 'state.current']),
    'measure.start': ('已通电、启动完成、质谱与涡轮泵正常；不在校准中；模式与入口一致（真空模式不接吸枪，吸枪模式须接吸枪）', ['power.on', 'state.current', 'part.connected', 'settings.current']),
    'measure.stop': ('已通电且不在启动中；按住超过放气延时则放气', ['power.on', 'state.current']),
    'zero.set': ('测量中；ZERO未被禁用；I·ZERO模式要求信号稳定', ['state.current', 'signal.stable', 'settings.current']),
    'zero.toggle': ('同zero.set', ['state.current', 'signal.stable']),
    'calibration.start': ('待机或测量状态；真空模式；外部校准须在测量中并给出校准漏孔漏率', ['state.current', 'part.connected']),
    'spray.set': ('入口上有被测件（真空模式）', ['part.connected']), 'sniffer.position': ('接了吸枪管', ['part.connected']),
    'settings.configure': ('已通电；测量中不能改模式；阈值2≥阈值1', ['power.on', 'state.current', 'settings.current']),
    'display.rotate': ('已声明环境条件', ['plant.configured']), 'datalog.read': ('已通电', ['power.on']), 'fault.inject': ('无', ['plant.configured']),
}
_EVOLVE = ['state.current', 'state.remaining_s', 'range.current', 'zero.enabled', 'pressure.inlet_pa', 'pressure.foreline_pa', 'leak.display_pa_m3_s', 'display.leak_mantissa',
           'display.leak_exponent', 'leak.status', 'leak.trigger2_exceeded', 'alarm.audible', 'zero.offset_pa_m3_s', 'signal.stable', 'calibration.factor', 'alarms.active',
           'indicator.reject', 'indicator.ready', 'truth.gas', 'truth.detector', 'truth.leaks']
_DISP = ['leak.display_pa_m3_s', 'display.leak_mantissa', 'display.leak_exponent', 'leak.status', 'leak.trigger2_exceeded', 'alarm.audible', 'indicator.reject', 'indicator.ready',
         'alarms.active']
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.gas', 'truth.detector', 'truth.leaks', 'alarms.active'],
    'power.set': ['power.on', 'state.current', 'state.remaining_s', 'range.current', 'pressure.inlet_pa', 'pressure.foreline_pa', 'zero.enabled', 'zero.offset_pa_m3_s',
                  'truth.detector', 'truth.gas', 'signal.stable'] + _DISP,
    'part.connect': ['part.connected', 'truth.gas', 'truth.leaks', 'pressure.inlet_pa'], 'part.disconnect': ['part.connected', 'truth.gas', 'truth.leaks', 'pressure.inlet_pa'],
    'sniffer.connect': ['part.connected', 'truth.gas', 'truth.leaks'],
    'measure.start': ['state.current', 'range.current', 'zero.offset_pa_m3_s', 'pressure.inlet_pa', 'pressure.foreline_pa', 'truth.gas', 'truth.detector', 'signal.stable'] + _DISP,
    'measure.stop': ['state.current', 'range.current', 'zero.enabled', 'zero.offset_pa_m3_s', 'pressure.inlet_pa', 'pressure.foreline_pa', 'truth.gas', 'truth.detector',
                     'signal.stable', 'state.remaining_s'] + _DISP,
    'zero.set': ['zero.enabled', 'zero.offset_pa_m3_s'] + _DISP, 'zero.toggle': ['zero.enabled', 'zero.offset_pa_m3_s'] + _DISP,
    'calibration.start': ['state.current', 'state.remaining_s', 'range.current', 'pressure.foreline_pa', 'truth.detector', 'truth.gas', 'signal.stable'] + _DISP,
    'spray.set': ['truth.leaks'], 'sniffer.position': [], 'settings.configure': ['settings.current', 'signal.stable'] + _DISP, 'display.rotate': ['display.angle_rad'],
    'fault.inject': ['truth.faults', 'truth.detector', 'truth.gas', 'signal.stable'] + _DISP, 'sim.step': ['clock.time_s'],
}
EFFECTS['power.toggle'] = list(EFFECTS['power.set'])
ON_STEP = {
    'sim.step': _EVOLVE + ['clock.time_s'], 'power.set': _EVOLVE, 'power.toggle': _EVOLVE, 'measure.start': _EVOLVE, 'measure.stop': _EVOLVE, 'calibration.start': _EVOLVE,
    'zero.set': ['zero.offset_pa_m3_s'] + _DISP, 'zero.toggle': ['zero.offset_pa_m3_s'] + _DISP, 'spray.set': _EVOLVE, 'sniffer.position': _EVOLVE, 'part.connect': _EVOLVE,
    'fault.inject': _EVOLVE, 'plant.configure': ['truth.gas', 'truth.detector'], 'settings.configure': _DISP,
}
ON_STEP_ZH = {
    'sim.step': '按0.5 s网格（气体0.05 s子步）积分：启动计时、被测件总压与氦分压（漏孔、通道延迟、出气、抽速）、自动换档、检测器信号与内部背景、零点、校准、判定与报警、数据记录',
    'measure.start': '抽空到15 mbar后按入口压力换档（GROSS/FINE/ULTRA）测量', 'measure.stop': '放气时入口按时间常数回到大气压', 'calibration.start': '校准约25 s（接着被测件时按容积延长）后更新因子',
    'zero.set': 'I·ZERO跟随下降的背景', 'spray.set': '氦经漏孔与通道到达质谱（响应时间）', 'sniffer.position': '吸枪读数随距离变化', 'part.connect': '被测件参与抽空与出气',
}
EXEC_TEXT = {
    'measure.start': ('state.current由evacuation变为measure，range.current按入口压力换档', '抽空超过最长抽空时间报W76并回到待机'),
    'calibration.start': ('state.current回到standby或measure，calibration.factor更新（或W81/W82保留旧因子）', 'STOP或断电中止校准，保留旧因子'),
}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('part.connect', 'part.disconnect', 'sniffer.connect', 'spray.set', 'sniffer.position'):
    RESOURCES[_k] = ['host_clock', 'inlet_flange']
OBSERVES = {'measure.start': ['state.current', 'range.current', 'pressure.inlet_pa'], 'calibration.start': ['state.current', 'calibration.factor'],
            'spray.set': ['leak.display_pa_m3_s'], 'zero.set': ['zero.offset_pa_m3_s']}
_WB, _BR, _MN, _C14, _C26, _NI, _PC, _SIM = ('user_workbook', 'manufacturer_brochure', 'manufacturer_operating_manual', 'cas_vacuum_ion_sources_2014',
                                             'cas_vacuum_accelerators_2026', 'nist_webbook', 'pubchem', 'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({'power.set': [_MN], 'power.toggle': [_MN], 'part.connect': [_MN, _C14, _C26], 'measure.start': [_BR, _MN], 'measure.stop': [_MN], 'zero.set': [_BR, _MN],
                    'zero.toggle': [_MN], 'calibration.start': [_BR, _MN], 'spray.set': [_BR], 'sniffer.connect': [_MN], 'sniffer.position': [_BR, _MN],
                    'settings.configure': [_MN], 'display.rotate': [_BR], 'leakrate.convert': [_C14, _NI, _PC], 'datalog.read': [_MN], 'fault.inject': [_MN, _SIM]})
CAP_SOURCES.update({k: [_BR, _MN] for k in UNSUPPORTED})
PARAM_ZH = {'ambient_k': '环境温度', 'pressure_pa': '大气压', 'temperature_at_calibration_k': '上次校准时的环境温度（灵敏度漂移基准）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.helium_leak_detection.limitations。'
CAP_LIMITS = {
    'part.connect': ['被测件为单一容积节点（不解析内部流场）；漏孔按氦标准漏率与分子流（大于1e-4 mbar·L/s按粘滞流近似）；通道延迟按Santeler管道导率与通道容积；出气按库中材料与时间规律。'],
    'spray.set': ['喷氦抽象为漏孔外侧的氦摩尔分数（工程值0.5）；喷枪覆盖范围与扩散到相邻漏孔不建模。'],
    'sniffer.position': ['吸枪捕集按距离指数衰减（工程长度5 mm）；空气中氦背景5.24 ppm决定吸枪检出限量级。'],
    'calibration.start': ['内置校准漏孔漏率为工程值（手册未给数值）；外部校准漏孔按给定的标称值，真实值假定等于标称。',
                          '从待机开始的内部校准先把入口（及所接被测件）抽到15 mbar以下并自动换档，再测基线、打开校准漏孔，关闭后等其氦从信号中衰减才结束；返回的duration_s与state.remaining_s是按容积与抽速的估计。'],
    'zero.set': ['ZERO/I·ZERO/自动零点：扣除按下时的信号，信号继续下降时零点随之下降后保持（手册4.2.2.4）；I·ZERO只在信号稳定时允许；厂家具体算法未公开。'],
}
LIMITATIONS = [
    '所选配置：UL1000 Fab（230 V 50 Hz，550-100A），真空模式与吸枪模式（SL200吸枪管）；TC1000测试室、遥控器、SMART-Spray喷枪的实际控制与H₂/³He示踪为不支持或抽象。',
    '气体：被测件单节点，总压与氦分压分别积分；抽速按状态与量程（抽空/GROSS/FINE用涡旋泵25 m³/h，ULTRA用涡轮上级）；氦抽速用手册GROSS 8/FINE 7/ULTRA 2.5 L/s；换档阈值15/2/0.4 mbar。',
    '检测器：信号=进入质谱的氦流量＋内部背景，乘真实/校准灵敏度之比；内部背景、灵敏度温漂、噪声、喷氦分数、吸枪流量等为工程值（instrument_model.json逐项说明）；检出限只是各量程的下限，不是每次读数误差。',
    '漏率换算：分子流按√(M_He/M_x)（CAS Eq. 14；102种气体/蒸气的摩尔质量来自NIST与PubChem）；粘滞流换算需要粘度，不提供。',
    '被测件、漏孔、喷枪、吸枪管与氦在场景中没有几何；场景中控制面板转角、显示屏漏率数字（尾数与指数）与红/绿指示按运行层状态变化。',
]


def _sampling_text(k):
    if k == 'clock.time_s':
        return 'Host clock.'
    if SLOTS[k]['origin'] == 'sensor':
        return 'Integrated on the fixed 0.5 s grid (anchored at t = 0); between grid points the last value is held; sensor noise is drawn per grid tick.'
    if SLOTS[k]['origin'] in ('derived', 'process_truth'):
        return 'Integrated on the fixed 0.5 s grid (anchored at t = 0); between grid points the last value is held.'
    return 'Changes when a command is accepted.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if CAPS[k]['visibility'] != 'agent' else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _pre_text(k):
    errs, text = CAP_ERRORS.get(k, []), PRECONDITIONS[k][0]
    if 'PLANT_REQUIRED' in errs and '环境条件' not in text:
        text = '已声明环境条件（否则PLANT_REQUIRED）；' + text
    return text


def _pre_slots(k):
    errs = CAP_ERRORS.get(k, [])
    ids = (['plant.configured'] if 'PLANT_REQUIRED' in errs else []) + list(PRECONDITIONS[k][1])
    return list(dict.fromkeys(ids))


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：无模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景关节、读数或指示（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节或读数', binding_ids=[])


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_brochure': _BR, 'manufacturer_operating_manual': _MN}
    WB_KEYS = ('min_leak_vacuum_pa_m3_s', 'min_leak_sniffer_pa_m3_s', 'max_inlet_pressure_pa', 'roughing_speed_m3_s', 'helium_speed_ultra_m3_s', 'dimensions_m', 'mass_kg')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明），不是实测')
    asm('helium_unit_area_conductance_m3_s_m2', C_PRIME_HE, 'm3/(s m2)', '293 K氦的单位面积孔导率（CAS表8）', '文献值（293 K）', (_C14,))
    asm('detector_internal_volume_m3', INTERNAL_V, 'm3', '检漏仪入口与阀块的内部容积', '工程值')
    return P


SLOT_REFERENCE = {'pressure.inlet_pa': '绝压（入口/被测件侧）', 'pressure.foreline_pa': '绝压（前级）', 'leak.display_pa_m3_s': '氦漏率（Pa·m³/s；1 mbar·L/s = 0.1 Pa·m³/s）',
                  'display.leak_mantissa': '显示单位mbar·L/s的尾数（显示用）', 'display.leak_exponent': '显示单位mbar·L/s的十的指数（显示用）',
                  'display.angle_rad': '控制面板绕竖直轴（+Z）自正前方的转角，自上向下看逆时针为正，0时面板朝−Y（操作者）'}


def export_inficon_ul1000fab_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'InficonUl1000fabSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.inficon_ul1000fab.InficonUl1000fabSession.invoke',
                                              invocation=f'session.invoke(instance_id="inficon_ul1000fab_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='InficonUl1000fabRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（压力、信号与判定在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['inlet_flange', 'scroll_pump', 'turbo_pump', 'mass_spectrometer', 'internal_test_leak', 'display_panel', 'start_key', 'stop_key', 'zero_key'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='氦质谱检漏（逆流式）：涡旋泵抽空被测件，入口压力低于15/2/0.4 mbar时依次进入GROSS/FINE/ULTRA，经漏孔进入的氦逆流（或经涡轮中级/上级）到达180°磁偏转质谱，'
                   '信号经校准换算为氦漏率，扣除背景后与触发阈值比较；吸枪模式从吸枪管连续吸入空气测氦浓度',
        equations=['V dp/dt = sum_i C_air,i (p_atm - p) + q(t) A - S_tot p;  V dp_He/dt = sum_i C_path,i (p_He,path,i - p_He) - S_He p_He',
                   'v_path,i dp_He,path,i/dt = C_leak,i (p_He,out,i - p_He,path,i) - C_path,i (p_He,path,i - p_He);  C_leak = L / 1 bar,  C_air = C_leak sqrt(M_He / M_air)',
                   'C_path = C\'_He pi R^2 tau(L, R),  tau = 1 / (1 + 3L/(8R) (1 + 1/(3 (1 + L/(7R)))))  (Santeler)',
                   'q(t) = q_ref (t_ref / t) (metals) or q_ref sqrt(t_ref / t) (polymers)',
                   'signal = (S_He p_He + B) s_true / s_cal;  display = machine_factor (filtered signal - zero offset), clipped to the range display limit',
                   'Q_x = Q_He sqrt(M_He / M_x) (free molecular)'],
        parameters=_params(), source_ids=[_SIM, _WB, _BR, _MN, _C14, _C26, _NI, _PC], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ul_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('inficon_ul1000fab', caps, slots, {'helium_leak_detection': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'helium_leak_detection': mechanism})
