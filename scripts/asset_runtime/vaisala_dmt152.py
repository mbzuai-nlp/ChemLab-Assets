"""CHEM-195 Vaisala DRYCAP dew point transmitter DMT152 (ISO G1/2"): process runtime.

Device (datasheet B210750EN-N; user guide M210865EN-K): thin-film capacitive polymer sensor DRYCAP 180U; calibrated range -80 ... -20 C Td with
+-2 C (-80 ... -40) / +-3 C (-40 ... -20), output range -100 ... +20 C; frost point below 0 C; response 63 % [90 %] at +20 C and 1 bar: wet -> dry
0.5 min [7.5 min], dry -> wet 2 s [5 s]; long-term stability better than 2 C / year (calibration interval 2 years, workbook); ppm 0-500 typical
with +-(0.2 ppm + 20 % of reading) at +20 C, 1013 mbar; the sensor is not pressure dependent - ppm needs the pressure (PRES / XPRES >= 0.1 bara) and
ppmw the molar mass (MOL); gases air / N2 / Ar / He / O2 (not H2, not pure CO2); flow has no effect on accuracy (1-2 l/min improves the response);
operating -40 ... +70 C, 0-50 bar; auto-calibration every 45 min (< 1 min warming, output active; fails in a changing environment, several failures
-> Autocal error); sensor purge weekly and at power-up (several minutes, output frozen); two scalable analog outputs (4-20 / 0-20 mA, 0-5 / 0-10 V)
with limit, purge-status and malfunction alarm levels, RS-485; supply 11-28 V (RS-485), 15-28 V (voltage out), 21-28 V (current out).

Process model (fixed 0.5 s grid anchored at t = 0; the sample line on a 5 s sub-grid anchored at t = 0; SI units, water as mole fraction x, partial
pressure e = x P, dew / frost point in K):
  * Process gas (host): species, water mole fraction, line pressure and temperature.  Installation: direct (sensor at line pressure and gas
    temperature) or a sampling line (material, length, inner diameter, flow, sensor pressure) - the water mole fraction is kept through the pressure
    reduction, the dew point at the sensor follows the sensor pressure.
  * Sample line: N cells in series; gas phase quasi-steady (transit << sorption time); each cell exchanges water with its wall by laminar mass
    transfer (Sh = 3.66) towards the equilibrium of the wall loading (metals: multi-class Langmuir, monolayer x roughness, K(T) = K0 exp(E/RT);
    polymers: three wall layers (5 / 20 / 75 % of the 1 mm wall) with Henry solubility from the equilibrium moisture uptake at 50 %RH and
    diffusion D = permeability / solubility, implicit layer diffusion); backward-Euler wall update solved per cell (monotone), polymer walls also
    add ambient moisture by permeation (CMC WVTR scaled by wall thickness and the partial-pressure difference); a leak fault mixes ambient air in.
    New tubing starts equilibrated with the room air.
  * Dew / frost point: x P = f(T_d, P) e_s(T_d) with Hardy's ITS-90 e_w / e_i and Greenspan enhancement factor (air form for all carriers).
  * Sensor: equivalent dew point of the polymer relaxes to the target - wetting single exponential, drying two parallel exponentials (fast part a,
    tau_1, tau_2 fitted to T63 / T90) - slower at low gas temperature (Arrhenius); reading = state + calibration offset (per unit, within the spec)
    + drift (rate drawn per unit, dry part corrected by auto-calibration) + noise; ppmv = e / (P_set - e) 1e6 with e from the reading at P_set.
  * Ledger: water moles from the process, out to the vent, on the walls, by permeation and leak close exactly."""
import bisect
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.vaisala_dmt152"
CATALOG_ID = "CHEM-195"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t2_vacuum/vaisala_dmt152'
LIBS = {'humidity': 'humidity_formulation.json', 'gases': 'gas_library.json', 'tubing': 'tubing_library.json', 'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def gases():
    return _lib('gases')['entries']


def tubing():
    return _lib('tubing')['entries']


def instrument():
    return _lib('instrument')


HF = {k: v['value'] for k, v in _lib('humidity')['entries'].items()}
DOC = {k: v['value'] for k, v in instrument()['documented'].items()}
ENG = {k: v['value'] for k, v in instrument()['engineering'].items()}
ENG_NOTE = {k: v['engineering_note_zh'] for k, v in instrument()['engineering'].items()}
ENG_UNIT = {k: v['unit'] for k, v in instrument()['engineering'].items()}
DT = 0.5
LINE_DT = 5.0
R_GAS = 8.314462618
M_W = 0.01801528
T0 = 273.15
P_STD, T_STD = 101325.0, 293.15                 # standard conditions of the sample flow (l/min at 20 C, 1 atm)
YEAR_S = 365.25 * 86400.0
STATES = ['off', 'purge', 'measuring', 'error']
MODES = {'4-20mA': (0.004, 0.020, 'A'), '0-20mA': (0.0, 0.020, 'A'), '0-5V': (0.0, 5.0, 'V'), '0-10V': (0.0, 10.0, 'V'),
         'I-alarm': (0.0, 0.020, 'A'), 'U-alarm': (0.0, 10.0, 'V')}             # AMODE 7 / 8: ON/OFF outputs (limit and purge-status alarms only here)
ALARM_MODES = ('I-alarm', 'U-alarm')
FAULTS = ['sensor_oil_contamination', 'sensor_damage', 'filter_clogged', 'sample_line_leak', 'autocal_disturbed']
MAX_EVENTS_LOG = 2000
TUBE_WALL_M = 0.001                             # polymer tubing wall thickness (engineering, 1 mm)
POLY_LAYERS = (0.05, 0.20, 0.75)                 # polymer wall layers (fractions of the wall, inner surface first) for the bulk water uptake
CAP_TRAVEL = 0.050                              # scene offset of the removed transport cap along +X (abstraction of taking it off)
_M64 = (1 << 64) - 1
_ISO = {}                                       # cached isotherm tables (material, T) -> (log10 p, loading)


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


# ---------------------------------------------------------------- humidity formulation (Hardy 1998, ITS-90)
def e_water(T):
    g = HF['water_g']
    return math.exp(sum(g[i] * T ** (i - 2) for i in range(7)) + g[7] * math.log(T))


def e_ice(T):
    k = HF['ice_k']
    return math.exp(sum(k[i] * T ** (i - 1) for i in range(5)) + k[5] * math.log(T))


def e_sat(T):
    """Saturation over ice below 0 C (frost point), over water above."""
    return e_ice(T) if T < T0 else e_water(T)


def _inv(cd, e):
    L = math.log(e)
    return sum(c * L ** i for i, c in enumerate(cd['c'])) / sum(d * L ** i for i, d in enumerate(cd['d']))


def t_from_e(e):
    """Dew / frost point (K) of a saturation pressure e (Pa): frost point while it stays below 0 C."""
    tf = _inv(HF['frostpoint_cd'], e)
    return tf if tf < T0 else _inv(HF['dewpoint_cd'], e)


def enhancement(T, P):
    t = T - T0
    if T < T0:
        c = HF['enh_ice_m100_m50'] if t < -50.0 else HF['enh_ice_m50_0']
    else:
        c = HF['enh_water_0_100'] if t >= 0.0 else HF['enh_water_m50_0']
    es = e_sat(T)
    alpha = sum(a * t ** i for i, a in enumerate(c['A']))
    beta = math.exp(sum(b * t ** i for i, b in enumerate(c['B'])))
    return math.exp(alpha * (1.0 - es / P) + beta * (P / es - 1.0))


def dewpoint(x, P):
    """Dew / frost point (K) of water mole fraction x at total pressure P: x P = f(Td, P) e_s(Td)."""
    e = max(x * P, 1e-6)
    Td = t_from_e(e)
    for _ in range(3):
        Td = t_from_e(e / enhancement(Td, P))
    return Td


def mole_fraction(Td, P):
    return min(enhancement(Td, P) * e_sat(Td) / P, 1.0)


def ppmv_from(Td, P):
    """Vaisala convention: ppmv = e / (P - e) 1e6 (per mole of dry gas)."""
    e = enhancement(Td, P) * e_sat(Td)
    return e / max(P - e, 1e-9) * 1e6
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


GAS_IDS = sorted(gases())
TUBE_IDS = sorted(tubing())
PLANT_EXAMPLE = dict(ambient_k=295.15, ambient_rh=0.40, pressure_pa=101325.0, calibration_age_s=0.0, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 263.15, 323.15), ambient_rh=number('1', 0.0, 1.0), pressure_pa=number('Pa', 80000.0, 106000.0),
                 calibration_age_s=number('s', 0.0, 20 * YEAR_S), noise_seed=integer(0, 2 ** 31 - 1)))
PROCESS_EXAMPLE = dict(gas='nitrogen', water_umol_mol=10.0, pressure_pa=101325.0, temperature_k=293.15)
PROCESS = obj(dict(gas=enum(GAS_IDS), water_umol_mol=number('umol/mol', 0.0, 50000.0), pressure_pa=number('Pa', 1000.0, 6.0e6), temperature_k=number('K', 223.15, 373.15)))
INSTALL_EXAMPLE = dict(mode='sampling_line', tubing_material='ss316l_ep', tubing_length_m=2.0, tubing_inner_diameter_m=0.004, flow_m3_s=1.0 / 60000.0,
                       sensor_pressure_pa=101325.0, pre_dried=False)
INSTALL = obj(dict(mode=enum(['direct', 'sampling_line']), tubing_material=enum(TUBE_IDS), tubing_length_m=number('m', 0.0, 50.0),
                   tubing_inner_diameter_m=number('m', 0.001, 0.02), flow_m3_s=number('m3/s', 1e-6, 1e-3), sensor_pressure_pa=number('Pa', 1.0e4, 5.0e6),
                   pre_dried=BOOL), optional=('tubing_material', 'tubing_length_m', 'tubing_inner_diameter_m', 'flow_m3_s', 'sensor_pressure_pa', 'pre_dried'))
SETTINGS = obj(dict(pressure_pa=number('Pa', 1.0e4, 5.0e6), xpressure_pa=number('Pa', 0.0, 5.0e6), molar_mass_kg_mol=number('kg/mol', 0.002, 0.4), purge_enabled=BOOL),
               optional=('pressure_pa', 'xpressure_pa', 'molar_mass_kg_mol', 'purge_enabled'))
LIMIT = nullable(obj(dict(lo=number('1'), hi=number('1'))))
OUTPUT = obj(dict(channel=integer(1, 2), mode=enum(list(MODES)), quantity=enum(['tdf', 'ppm']), lo=number('1'), hi=number('1'), limit_alarm=LIMIT, purge_status=BOOL,
                  error_level=number('1')), optional=('mode', 'quantity', 'lo', 'hi', 'limit_alarm', 'purge_status', 'error_level'))
READING = obj(dict(tdf_k=nullable(number('K')), h2o_umol_mol=nullable(number('umol/mol')), h2o_mg_kg=nullable(number('mg/kg')), frozen=BOOL, errors=array(TEXT, 0, 16)))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'operation', 'process', 'alarm', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
_BODY = ['transmitter_body']
_SENS = ['drycap_sensor', 'sensor_filter']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明室内温度、相对湿度与大气压（环境与漏入的湿气）、距上次校准的时间与噪声种子', 'evaluator', 'configuration')
cap('process.set', PROCESS, obj(dict(gas=TEXT, dew_point_at_line_k=number('K'), notes=array(TEXT, 0, 8))), PROCESS_EXAMPLE, ['process_thread'],
    '宿主声明工艺气体：种类、水的摩尔分数（µmol/mol）、管道压力与温度（干燥后的载气、手套箱气氛或催化气路）', 'evaluator', 'configuration')
cap('installation.set', INSTALL, obj(dict(mode=TEXT, sensor_pressure_pa=number('Pa'), notes=array(TEXT, 0, 8))), INSTALL_EXAMPLE, ['process_thread', 'sensor_filter'],
    '安装方式：直接拧入工艺管道（传感器在管道压力与气温下），或经取样管与取样池（管材、长度、内径、流量、传感器处压力；新管默认与室内空气平衡，可声明已吹干）', 'agent', 'robot_skill')
cap('power.set', obj(dict(on=BOOL, voltage_v=number('V', 0.0, 40.0)), optional=('voltage_v',)), obj(dict(on=BOOL, voltage_v=number('V'))), dict(on=True, voltage_v=24.0),
    ['m8_connector'], '供电通断与电压（RS-485 11–28 V、电压输出15–28 V、电流输出21–28 V DC）；上电先做传感器吹扫（输出冻结）', 'agent')
cap('settings.configure', SETTINGS, obj(dict(settings=FREE_OBJ)), dict(pressure_pa=101325.0), ['electronics'],
    '串口设定：PRES（ppm换算用压力，≥0.1 bara）、XPRES（临时压力，0为取消）、MOL（ppmw换算用摩尔质量）、XPUR（启用/停用传感器吹扫）', 'agent')
cap('output.configure', OUTPUT, obj(dict(channel=integer(1, 2), config=FREE_OBJ)), dict(channel=1, mode='4-20mA', quantity='tdf', lo=193.15, hi=293.15),
    ['m8_connector', 'electronics'], '模拟输出通道（AMODE）：测量模式4–20/0–20 mA、0–5/0–10 V（量为露/霜点K或ppm、量程上下限、故障输出电平），或ON/OFF报警模式I-alarm/U-alarm（限值报警与吹扫状态报警只在报警模式下有效）', 'agent')
cap('cap.remove', obj({}), obj(dict(fitted=BOOL)), {}, ['transport_cap'], '取下黄色运输保护帽（用户手册：保护帽使传感器保持干燥，安装前取下）', 'agent', 'robot_skill')
cap('cap.fit', obj({}), obj(dict(fitted=BOOL)), {}, ['transport_cap'], '装回运输保护帽（储存时；须先从管道拆下）', 'agent', 'robot_skill')
cap('installation.remove', obj({}), obj(dict(installed=BOOL)), {}, ['process_thread'], '从工艺管道或取样池拆下变送器（传感器暴露在室内空气中）', 'agent', 'robot_skill')
cap('measurement.send', obj({}), READING, {}, ['electronics'], 'RS-485 SEND：读取一次露/霜点、ppmv、ppmw、冻结标志与错误', 'agent', 'query')
cap('errors.read', obj({}), obj(dict(errors=array(TEXT, 0, 16))), {}, ['electronics'], 'ERRS：读取当前错误', 'agent', 'query')
cap('calibration.service', obj({}), obj(dict(calibrated=BOOL, calibration_age_s=number('s'))), {}, _BODY,
    '送厂校准（抽象）：漂移清零、重新标定偏差（在准确度指标内），校准龄归零；须断电拆下（不在线）', 'agent', 'robot_skill')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _BODY,
    '事件记录（吹扫、自动校准、错误、安装与设定变化）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(gas=FREE_OBJ, sensor=FREE_OBJ, line=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, _BODY,
    '评测者读取过程真值：工艺与传感器处的水摩尔分数、分压与真实露/霜点，传感器内部状态、偏差与漂移，取样管各段管壁吸附量', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '取样系统中水（mol）的守恒账：工艺带入、排出、管壁吸附、渗透与漏入', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='sensor_oil_contamination', active=True), _BODY,
    '故障注入：传感器油污染（响应变慢）、传感器损坏（T/F meas error）、过滤器堵塞（取样流量下降、直接安装时响应变慢）、取样管泄漏（室内空气漏入）、自动校准受扰（一直失败）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'field_check.dm70': 'DM70手持表现场比对为另购附件（未选）。', 'pressure.measure': 'DMT152没有压力传感器；ppm换算用设定压力（PRES/XPRES）。',
    'temperature.output': 'DMT152不输出气体温度。', 'analog.extend_range': '模拟输出扩展量程与输出校准（ACAL）不建模（AMODE的测量与ON/OFF报警模式已实现于output.configure）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'transmitter_body', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'transmitter_body', '环境条件是否已声明')
slot('process.configured', BOOL, '1', 'setpoint', 'process_thread', '工艺气体是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'm8_connector', '是否供电')
slot('supply.voltage_v', number('V'), 'V', 'setpoint', 'm8_connector', '供电电压')
slot('state.current', enum(STATES), '1', 'derived', 'electronics', '工作状态：off、purge（传感器吹扫，输出冻结）、measuring、error')
slot('state.remaining_s', nullable(number('s')), 's', 'derived', 'electronics', '吹扫剩余时间（其他状态为空）')
slot('autocal.active', BOOL, '1', 'derived', 'drycap_sensor', '自动校准进行中（输出保持有效）')
slot('reading.tdf_k', nullable(number('K')), 'K', 'sensor', 'drycap_sensor', '露/霜点读数（0 °C以下为霜点；输出范围173.15–293.15 K；无读数为空）')
slot('reading.h2o_umol_mol', nullable(number('umol/mol')), 'umol/mol', 'derived', 'electronics', '按设定压力换算的水含量（体积ppm，每摩尔干气）')
slot('reading.h2o_mg_kg', nullable(number('mg/kg')), 'mg/kg', 'derived', 'electronics', '按设定摩尔质量换算的水含量（质量ppm）')
slot('reading.frozen', BOOL, '1', 'derived', 'electronics', '输出冻结（吹扫中保持最后测量值）')
slot('reading.in_calibrated_range', BOOL, '1', 'derived', 'electronics', '读数是否在标定范围−80…−20 °C Td内（范围外准确度未规定）')
slot('output.analog', FREE_OBJ, '1', 'derived', 'm8_connector', '两路模拟输出：通道模式、量、当前输出值（A或V）与报警状态')
slot('diagnostics.errors', array(TEXT, 0, 16), '1', 'derived', 'electronics', '当前错误（Ambient temperature error、Autocal error、Voltage error、Voltage too low error、Voltage too low for mA output error、T meas error、F meas error）')
slot('alarms.active', array(TEXT, 0, 16), '1', 'derived', 'electronics', '当前报警：各通道限值报警、吹扫状态报警、故障报警')
slot('settings.current', FREE_OBJ, '1', 'setpoint', 'electronics', '当前设定（PRES、XPRES、MOL、XPUR）')
slot('installation.current', FREE_OBJ, '1', 'setpoint', 'process_thread', '当前安装方式与取样管参数（installed=false时在台面上、传感器接触室内空气或被保护帽封住）')
slot('cap.fitted', BOOL, '1', 'setpoint', 'transport_cap', '黄色运输保护帽是否装在过滤器上（出厂时装着）')
slot('cap.offset_m', number('m'), 'm', 'setpoint', 'transport_cap', '保护帽在场景中的位移（装着为0，取下为0.05 m；镜像到场景关节）')
slot('calibration.age_s', number('s'), 's', 'derived', 'transmitter_body', '距上次校准的时间（建议间隔2年）')
slot('truth.gas', FREE_OBJ, '1', 'process_truth', 'process_thread', '工艺与传感器处水的摩尔分数、分压、真实露/霜点', 'evaluator')
slot('truth.sensor', FREE_OBJ, '1', 'process_truth', 'drycap_sensor', '传感器内部等效露点、标定偏差、漂移、自动校准与吹扫计时', 'evaluator')
slot('truth.line', FREE_OBJ, '1', 'process_truth', 'sensor_filter', '取样管各段管壁吸附量、出口水摩尔分数、渗透与漏入', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'transmitter_body', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'cap.offset_m'}


# ---------------------------------------------------------------- runtime
class VaisalaDmt152Runtime:
    def __init__(self, instance_id='vaisala_dmt152_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.proc = None
        self.seed = 0
        self.powered = False
        self.voltage = 24.0
        self.state = 'off'
        self.purge_left = 0.0
        self.next_purge = None
        self.next_autocal = None
        self.autocal_t0 = 0.0
        self.startup = False
        self.purge_total = 0.0
        self.autocal_left = 0.0
        self.autocal_fails = 0
        self.autocal_ok = 0
        self.cap_on = True                                # delivered with the yellow transport protection cap
        self.installed = False                            # lying on the bench until installation.set
        self.inst = dict(mode='direct', tubing_material='ss316l_ep', tubing_length_m=0.0, tubing_inner_diameter_m=0.004, flow_m3_s=1.0 / 60000.0,
                         sensor_pressure_pa=None, pre_dried=False)
        self.settings = dict(pressure_pa=DOC['pres_default_pa'], xpressure_pa=0.0, molar_mass_kg_mol=DOC['mol_default_kg_mol'], purge_enabled=True)
        self.channels = {1: dict(mode='4-20mA', quantity='tdf', lo=193.15, hi=293.15, limit_alarm=None, purge_status=False, error_level=0.020),
                         2: dict(mode='0-10V', quantity='ppm', lo=0.0, hi=1.0e6, limit_alarm=None, purge_status=False, error_level=0.0)}
        self.limit_on = {1: False, 2: False}
        self.y_fast = self.y_slow = None                  # equivalent dew point of the polymer (K): fast / slow parts
        self.x_sensor = 0.0                               # water mole fraction at the sensor (last line update)
        self.cells = []                                   # wall loading per cell (mol/m2)
        self.bulk = []                                    # deeper polymer wall layers per cell (mol/m2)
        self.last_reading = None
        self.reading = None
        self.cal_age0 = 0.0
        self.cal_t0 = 0.0                                 # time of the last calibration (relative to the clock)
        self.cal_epoch = 0
        self.nopurge_s = 0.0
        self.unsuitable_s = 0.0
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.revision = 0
        self.errors = []
        self.alarms = []
        self.analog = {}
        self.target_hist = []
        self._tgt_key, self._tgt = None, None
        self._off_key, self._off = None, None
        self.led = dict(water_in_mol=0.0, water_out_mol=0.0, water_permeated_mol=0.0, water_leaked_mol=0.0, water_installed_with_tubing_mol=0.0,
                        water_removed_with_tubing_mol=0.0, water_adsorbed_mol=0.0, water_desorbed_mol=0.0)
        self._principals, self._requests, self._caller = {}, {}, None

    # ---- sessions
    @property
    def time_s(self):
        return self.t

    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator', 'debug'):
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return VaisalaDmt152Session(self, name)

    def _event(self, kind, code, message):
        if len(self.events) >= MAX_EVENTS_LOG:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message))

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    # ---- derived quantities
    def _x_ambient(self):
        pl = self.plant
        return pl['ambient_rh'] * e_water(pl['ambient_k']) / pl['pressure_pa']

    def _sensor_pressure(self):
        if self.proc is None or not self.installed:
            return self.plant['pressure_pa']
        if self.inst['mode'] == 'direct':
            return self.proc['pressure_pa']
        return self.inst['sensor_pressure_pa'] or self.plant['pressure_pa']

    def _sensor_temperature(self):
        """Direct: the sensor sits in the process gas; sampling cell: at room temperature."""
        if self.proc is not None and self.installed and self.inst['mode'] == 'direct':
            return self.proc['temperature_k']
        return self.plant['ambient_k']

    def _x_process(self):
        if self.proc is None:
            return self._x_ambient()
        w = self.proc['water_umol_mol'] * 1e-6
        return w / (1.0 + w)                               # umol per mol of dry gas -> mole fraction

    def _flow_mol_s(self):
        q = self.inst['flow_m3_s'] * (0.1 if 'filter_clogged' in self.faults else 1.0)
        return q * P_STD / (R_GAS * T_STD)

    def _line_geometry(self):
        L, d = self.inst['tubing_length_m'], self.inst['tubing_inner_diameter_m']
        n = int(ENG['line_cells'])
        return n, math.pi * d * L / n, d

    def _classes(self, mat, T):
        """(capacity, K) per site class: metals - equal-capacity classes over the site-energy distribution (Temkin-like); polymers - one class."""
        m = tubing()[mat]
        cap_ = m['sorption_capacity_mol_m2']
        if m['kind'] == 'metal':
            Es = ENG['metal_site_energies_j_mol']
            return [(cap_ / len(Es), ENG['langmuir_k0_per_pa'] * math.exp(E / (R_GAS * T))) for E in Es]
        raise ValueError('polymer walls use the layered bulk model (_polymer)')

    @staticmethod
    def _polymer(mat):
        """Bulk water uptake of a polymer wall: volumetric solubility S (mol / (m3 Pa)) from the equilibrium moisture uptake at 50 %RH / 23 C, diffusion
        coefficient D = permeability / S with the permeability from the documented WVTR; layers (inner surface -> outer) of the 1 mm wall."""
        m = tubing()[mat]
        S = m['moisture_uptake_50rh'] * m['density_kg_m3'] / M_W / (0.5 * 2809.0)        # p_sat(23 C) = 2809 Pa
        perm = m['wvtr_g_m2_d'] * 1e-3 / 86400.0 / M_W * m['wvtr_film_thickness_m'] / ENG['wvtr_reference_vapour_pa']
        return S, perm / S, [f * TUBE_WALL_M for f in POLY_LAYERS]

    def _n_eq(self, mat, T, p):
        if tubing()[mat]['kind'] == 'polymer':                              # first wall layer, linear (Henry) sorption
            S, D, dl = self._polymer(mat)
            return S * dl[0] * p
        return sum(c * K * p / (1.0 + K * p) for c, K in self._classes(mat, T))

    def _p_eq(self, mat, T, n):
        """Inverse isotherm by a cached table in log p (monotone)."""
        if tubing()[mat]['kind'] == 'polymer':
            S, D, dl = self._polymer(mat)
            return max(n, 0.0) / (S * dl[0])
        key = (mat, round(T, 6))
        tab = _ISO.get(key)
        if tab is None:
            lp = [-10.0 + 15.0 * i / 1500 for i in range(1501)]
            tab = _ISO[key] = (lp, [self._n_eq(mat, T, 10.0 ** x) for x in lp])
        lp, nn = tab
        if n <= nn[0]:
            return 10.0 ** lp[0] * max(n, 0.0) / nn[0]
        if n >= nn[-1]:
            return 10.0 ** lp[-1]
        i = bisect.bisect_left(nn, n)
        f = (n - nn[i - 1]) / (nn[i] - nn[i - 1])
        return 10.0 ** (lp[i - 1] + f * (lp[i] - lp[i - 1]))

    def _wall_mol(self):
        """Water held by the sampling-line walls (surface loading of every cell plus the deeper layers of a polymer wall)."""
        if not self.cells:
            return 0.0
        A = self._line_geometry()[1]
        return (sum(self.cells) + sum(sum(b) for b in self.bulk)) * A

    def _diffuse_wall(self, i, mat, h):
        """Implicit (backward-Euler) diffusion through the polymer wall layers of cell i (closed outer face; permeation is the separate WVTR flux)."""
        S, D, dl = self._polymer(mat)
        n = [self.cells[i]] + self.bulk[i]
        C = [S * x for x in dl]                                            # mol / (m2 Pa) per layer
        G = [D * S / (0.5 * (dl[k] + dl[k + 1])) for k in range(len(dl) - 1)]
        p = [n[k] / C[k] for k in range(len(n))]
        a = [0.0] * len(n); b = [0.0] * len(n); c = [0.0] * len(n); r = [C[k] * p[k] for k in range(len(n))]
        for k in range(len(n)):
            b[k] = C[k]
            if k > 0:
                a[k] = -h * G[k - 1]; b[k] += h * G[k - 1]
            if k < len(n) - 1:
                c[k] = -h * G[k]; b[k] += h * G[k]
        for k in range(1, len(n)):                                         # Thomas algorithm (tridiagonal)
            w = a[k] / b[k - 1]
            b[k] -= w * c[k - 1]; r[k] -= w * r[k - 1]
        p[-1] = r[-1] / b[-1]
        for k in range(len(n) - 2, -1, -1):
            p[k] = (r[k] - c[k] * p[k + 1]) / b[k]
        tot = sum(n)
        new = [C[k] * p[k] for k in range(len(n))]
        new[-1] += tot - sum(new)                                          # exact conservation of the wall's water
        self.cells[i], self.bulk[i] = new[0], new[1:]

    def _init_line(self, wet):
        self.cells = []
        self.bulk = []
        if self.inst['mode'] != 'sampling_line' or self.inst['tubing_length_m'] <= 0.0:
            return
        n, A, d = self._line_geometry()
        T = self.plant['ambient_k']
        p = (self._x_ambient() * self.plant['pressure_pa']) if wet else (self._x_process() * self._sensor_pressure())
        mat = self.inst['tubing_material']
        n_eq = self._n_eq(mat, T, p)
        self.cells = [n_eq] * n
        if tubing()[mat]['kind'] == 'polymer':                             # deeper wall layers in equilibrium with the same vapour pressure
            S, D, dl = self._polymer(mat)
            self.bulk = [[S * x * p for x in dl[1:]] for _ in range(n)]
        self.led['water_installed_with_tubing_mol'] += self._wall_mol()   # tubing brought in carries its water (walls are part of the ledger)

    def _update_line(self, h):
        """Quasi-steady gas phase, backward-Euler wall loading per cell (monotone in the loading, solved by bisection)."""
        if not self.installed:                                               # on the bench: room air at the sensor (or sealed under the cap)
            if not self.cap_on:
                self.x_sensor = self._x_ambient()
            return
        x_in = self._x_process()
        P = self._sensor_pressure()
        ndot = self._flow_mol_s()
        L = self.led
        if 'sample_line_leak' in self.faults:
            f = 1e-3 * (P_STD / max(P, 1e3))                             # ambient air at the first fitting (back-diffusion / leak; engineering)
            dx = f * (self._x_ambient() - x_in)
            L['water_leaked_mol'] += ndot * dx * h
            x_in += dx
        L['water_in_mol'] += ndot * self._x_process() * h
        if not self.cells:
            L['water_out_mol'] += ndot * x_in * h
            self.x_sensor = x_in
            return
        n_c, A, d = self._line_geometry()
        mat = self.inst['tubing_material']
        m = tubing()[mat]
        T = self.plant['ambient_k']
        D = ENG['water_diffusivity_air_m2_s'] * (T / 298.15) ** 1.75 * (1e5 / P)
        a = ENG['tube_sherwood'] * D / d * A / (R_GAS * T)                # mol / (s Pa): wall exchange conductance of one cell
        perm = 0.0
        if m['kind'] == 'polymer':
            wvtr = m['wvtr_g_m2_d'] * 1e-3 / 86400.0 / M_W * (m['wvtr_film_thickness_m'] / TUBE_WALL_M)     # mol / (m2 s) at the reference difference
            A_out = A * (d + 2 * TUBE_WALL_M) / d
            p_amb = self._x_ambient() * self.plant['pressure_pa']
        for i in range(n_c):
            if m['kind'] == 'polymer':
                perm = max(wvtr * A_out * (p_amb - x_in * P) / ENG['wvtr_reference_vapour_pa'], -ndot * x_in)
                L['water_permeated_mol'] += perm * h
                x_in = x_in + perm / ndot
            n_old = self.cells[i]
            beta = math.exp(-a * P / ndot)

            def x_out_of(nw):
                xe = self._p_eq(mat, T, nw) / P
                return xe + (x_in - xe) * beta

            def resid(nw):
                return nw - n_old - h * ndot * (x_in - x_out_of(nw)) / A
            n_target = self._n_eq(mat, T, x_in * P)
            lo, hi = (n_old, n_target) if n_target >= n_old else (n_target, n_old)
            f_lo, f_hi = resid(lo), resid(hi)
            if f_lo >= 0.0:
                n_new = lo
            elif f_hi <= 0.0:
                n_new = hi
            else:                                                         # Illinois regula falsi on the monotone residual
                side = 0
                n_new = lo
                for _ in range(80):
                    n_new = (lo * f_hi - hi * f_lo) / (f_hi - f_lo)
                    f_new = resid(n_new)
                    if abs(f_new) <= 1e-14 * max(abs(n_old), abs(n_target), 1e-12) or hi - lo <= 1e-15 * max(hi, 1e-12):
                        break
                    if f_new > 0.0:
                        hi, f_hi = n_new, f_new
                        if side == 1:
                            f_lo *= 0.5
                        side = 1
                    else:
                        lo, f_lo = n_new, f_new
                        if side == -1:
                            f_hi *= 0.5
                        side = -1
            x_out = x_in - (n_new - n_old) * A / (h * ndot)                # exact cell balance
            if n_new >= n_old:
                L['water_adsorbed_mol'] += (n_new - n_old) * A
            else:
                L['water_desorbed_mol'] += (n_old - n_new) * A
            self.cells[i] = n_new
            if m['kind'] == 'polymer':
                self._diffuse_wall(i, mat, h)
            x_in = x_out
        L['water_out_mol'] += ndot * x_in * h
        self.x_sensor = x_in

    def _target_td(self):
        key = (self.x_sensor, self._sensor_pressure(), self._sensor_temperature())
        if key != self._tgt_key:
            Td = dewpoint(self.x_sensor, key[1])
            self._tgt_key, self._tgt = key, min(Td, key[2] + ENG['condensation_margin_k'])   # condensation: the polymer saturates at the gas temperature
        return self._tgt

    def _y(self):
        a = ENG['drying_fast_fraction']
        return a * self.y_fast + (1.0 - a) * self.y_slow

    def _offset(self, y):
        """Per-unit calibration offset (K): drawn at the dry and wet calibration points within the accuracy spec, linear in between."""
        if self._off_key != (self.seed, self.cal_epoch):
            s = ENG['offset_sigma_fraction']
            self._off_key = (self.seed, self.cal_epoch)
            self._off = (s * DOC['accuracy_dry_k'] * max(-2.0, min(2.0, _gauss(self.seed, self.cal_epoch, 901))),
                         s * DOC['accuracy_wet_k'] * max(-2.0, min(2.0, _gauss(self.seed, self.cal_epoch, 902))),
                         DOC['long_term_stability_k_per_year'] * (2.0 * _uni(self.seed, self.cal_epoch, 903) - 1.0))
        od, ow = self._off[0], self._off[1]
        u = min(max((y - 193.15) / 60.0, 0.0), 1.0)
        return od + (ow - od) * u

    def _drift(self, y):
        age_y = self._cal_age() / YEAR_S
        self._offset(y)
        rate = self._off[2]
        dry_w = 1.0 - min(max((y - 193.15) / 60.0, 0.0), 1.0)
        corr = ENG['drift_dry_fraction_after_autocal'] if self.autocal_ok > 0 else 1.0
        return rate * age_y * (dry_w * corr + (1.0 - dry_w)) + ENG['drift_extra_no_purge_k_per_s'] * self.nopurge_s

    def _cal_age(self):
        return self.cal_age0 + (self.t - self.cal_t0)

    def _gas_bias(self):
        g = self.proc['gas'] if self.proc is not None else None
        h2 = min(ENG['h2_bias_k_per_h'] * self.unsuitable_s / 3600.0, ENG['h2_bias_max_k'])   # the sensor stays affected after the exposure (until service)
        return h2 + (ENG['co2_bias_k'] if g == 'carbon_dioxide' else 0.0)

    def _p_set(self):
        return self.settings['xpressure_pa'] if self.settings['xpressure_pa'] > 0 else self.settings['pressure_pa']

    def _errors_now(self):
        E = []
        if not self.powered:
            return E
        Ts = self._sensor_temperature()
        lo, hi = DOC['operating_temperature_k']
        if not lo <= Ts <= hi:
            E.append('Ambient temperature error')
        if self.autocal_fails >= ENG['autocal_fail_count']:
            E.append('Autocal error')
        rs = DOC['supply_v']['rs485']
        if self.voltage < rs[0]:
            E.append('Voltage too low error')
        elif self.voltage > rs[1]:
            E.append('Voltage error')
        if any(c['mode'].endswith('mA') for c in self.channels.values()) and self.voltage < DOC['supply_v']['current_out'][0]:
            E.append('Voltage too low for mA output error')
        if 'sensor_damage' in self.faults:
            E += ['T meas error', 'F meas error']
        return E

    # ---- physics
    def step(self, dt_s):
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = t_end
            self.t_grid = math.floor(t_end / DT + 1e-9) * DT
            self.tick = int(round(self.t_grid / DT))                         # the tick index stays anchored to the grid at t = 0
            return self.t
        while self.t_grid + DT <= t_end + 1e-9:
            self.t = self.t_grid
            self._tick(DT)
            self.t_grid += DT
            self.tick += 1
        self.t = t_end
        self._outputs()
        return self.t

    def _tick(self, h):
        if self.tick % int(round(LINE_DT / DT)) == 0:
            self._update_line(LINE_DT)
        target = self._target_td()
        Tg = self._sensor_temperature()
        slow = math.exp(ENG['response_activation_k'] * (1.0 / Tg - 1.0 / 293.15))
        if 'sensor_oil_contamination' in self.faults:
            slow *= 5.0
        if 'filter_clogged' in self.faults and self.inst['mode'] == 'direct':
            slow *= 3.0
        if self.y_fast is None:
            self.y_fast = self.y_slow = target
        if self.cap_on:
            pass                                                             # sealed under the transport cap: the polymer keeps its dry state
        elif self.state == 'purge':
            pass                                                         # heated sensor: excess water driven out (handled at the end of the purge)
        elif target >= self._y():
            a = 1.0 - math.exp(-h / (ENG['wetting_tau_s'] * slow))
            self.y_fast += (target - self.y_fast) * a
            self.y_slow += (target - self.y_slow) * a
        else:
            self.y_fast += (target - self.y_fast) * (1.0 - math.exp(-h / (ENG['drying_fast_tau_s'] * slow)))
            self.y_slow += (target - self.y_slow) * (1.0 - math.exp(-h / (ENG['drying_slow_tau_s'] * slow)))
        self.target_hist.append((self.t + h, target))
        del self.target_hist[:-int(120 / DT)]
        if self.proc is not None and self.proc['gas'] == 'hydrogen' and self.installed and not self.cap_on:
            self.unsuitable_s += h                                           # only while the sensor is exposed to the hydrogen
        if not self.settings['purge_enabled'] and self.powered:
            self.nopurge_s += h
        if not self.powered:
            self.reading = None
            return
        # ---- purge (power-up and weekly; output frozen)
        if self.state == 'purge':
            self.purge_left -= h
            if self.purge_left <= 1e-9:
                self.purge_left = 0.0
                self.state = 'measuring'
                self.startup = False
                self.y_fast = min(self.y_fast, target)                    # purge evaporated the excess water out of the polymer
                self.y_slow = min(self.y_slow, target)
                self.next_purge = self.t + h + DOC['purge_interval_s']
                self._event('operation', 'PURGE_DONE', '传感器吹扫结束，恢复测量')
        elif self.state == 'measuring' and self.settings['purge_enabled'] and self.next_purge is not None and self.t + h >= self.next_purge - 1e-9:
            self._start_purge()
        # ---- auto-calibration (45 min; 20 min near the dry end; output stays active)
        if self.state == 'measuring':
            if self.autocal_left > 0.0:
                self.autocal_left -= h
                if self.autocal_left <= 1e-9:
                    self.autocal_left = 0.0
                    ok = 'autocal_disturbed' not in self.faults and self._stable()
                    if ok:
                        self.autocal_fails, self.autocal_ok = 0, self.autocal_ok + 1
                        self._event('operation', 'AUTOCAL_OK', '自动校准完成（干端漂移已修正）')
                    else:
                        self.autocal_fails += 1
                        self._event('operation', 'AUTOCAL_FAILED', f'自动校准未完成（测量环境变化太快；第{self.autocal_fails}次）')
                    near_dry = (self.reading or 300.0) < 203.15
                    self.next_autocal = self.autocal_t0 + (ENG['autocal_dry_interval_s'] if near_dry else DOC['autocal_interval_s'])   # period counted start to start
            elif self.next_autocal is not None and self.t + h >= self.next_autocal - 1e-9:
                self.autocal_left = DOC['autocal_duration_s']
                self.autocal_t0 = self.next_autocal
        # ---- reading
        errs = self._errors_now()
        self.errors = errs
        if self.state == 'measuring' and not ({'T meas error', 'F meas error', 'Ambient temperature error', 'Voltage too low error'} & set(errs)):
            y = self._y()
            sig = ENG['noise_dry_k'] + (ENG['noise_k'] - ENG['noise_dry_k']) * min(max((y - 193.15) / 60.0, 0.0), 1.0)
            r = y + self._offset(y) + self._drift(y) + self._gas_bias() + sig * _gauss(self.seed, self.tick, 1)
            lo, hi = DOC['output_range_k']
            self.reading = min(max(r, lo), hi)
            self.last_reading = self.reading
            self.state = 'measuring'
        elif self.state == 'purge':
            if self.startup and self.purge_total - self.purge_left >= DOC['startup_approx_reading_s'] and not ({'T meas error', 'F meas error', 'Ambient temperature error', 'Voltage too low error'} & set(errs)):
                y = self._y()                                                # user guide: an approximated reading after one minute of the start-up
                lo, hi = DOC['output_range_k']
                self.reading = min(max(y + self._offset(y) + self._drift(y) + self._gas_bias(), lo), hi)
            elif {'T meas error', 'F meas error', 'Ambient temperature error', 'Voltage too low error'} & set(errs):
                self.reading = None                                          # the same errors that block a measurement also block the frozen / approximate output
            else:
                self.reading = self.last_reading
        else:
            self.reading = None

    def _stable(self):
        if len(self.target_hist) < 4:
            return True
        (t0, a), (t1, b) = self.target_hist[0], self.target_hist[-1]
        return abs(b - a) / max(t1 - t0, 1e-9) <= ENG['autocal_max_rate_k_s']

    def _start_purge(self, duration=None):
        self.state = 'purge'
        self.purge_left = self.purge_total = ENG['purge_duration_s'] if duration is None else duration
        self.autocal_left = 0.0
        self._event('operation', 'PURGE_START', '传感器吹扫开始（加热数分钟，输出冻结）')

    def _quantities(self):
        if self.reading is None:
            return None, None, None
        P = self._p_set()
        ppm = ppmv_from(self.reading, P)
        return self.reading, ppm, ppm * M_W / self.settings['molar_mass_kg_mol'] if ppm is not None else None

    def _outputs(self):
        """Analog channels and alarms from the current reading (limit alarm with 1 % hysteresis -> full scale; purge status -> low level;
        malfunction -> error level)."""
        if self.plant is None:
            return
        td, ppm, _ = self._quantities()
        malf = bool(self.errors) and self.powered
        frozen = self.powered and self.state == 'purge'
        al = []
        out = {}
        for ch, c in self.channels.items():
            lo_o, hi_o, unit = MODES[c['mode']]
            if not self.powered:
                out[ch] = dict(mode=c['mode'], quantity=c['quantity'], value=0.0, unit=unit, state='unpowered')
                continue
            q = td if c['quantity'] == 'tdf' else ppm
            st = 'normal'
            if malf and (c['mode'].endswith('V') or self.voltage >= DOC['supply_v']['current_out'][0] or not c['mode'].endswith('mA')):
                v, st = c['error_level'], 'malfunction'
            elif malf:
                v, st = 0.0, 'malfunction'
            elif c['mode'] in ALARM_MODES:                                 # ON/OFF output: limit alarm and / or purge status (AMODE 7 / 8)
                on = False
                if frozen and c['purge_status']:
                    on, st = True, 'purge_status'
                    al.append(f'ch{ch}_purge_status')
                lim = c['limit_alarm']
                if lim is not None and q is not None and not frozen:
                    hy = 0.01 * abs(c['hi'] - c['lo'])
                    if self.limit_on[ch]:
                        self.limit_on[ch] = q < lim['lo'] + hy or q > lim['hi'] - hy
                    else:
                        self.limit_on[ch] = q < lim['lo'] or q > lim['hi']
                    if self.limit_on[ch]:
                        on, st = True, 'limit_alarm'
                        al.append(f'ch{ch}_limit_alarm')
                elif not frozen:
                    self.limit_on[ch] = False
                v = hi_o if on else lo_o
            elif q is None:
                v, st = lo_o, 'no_reading'
            else:
                span = c['hi'] - c['lo']
                frac = min(max((q - c['lo']) / span, 0.0), 1.0) if span else 0.0
                v = lo_o + (hi_o - lo_o) * frac                              # measurement modes (AMODE 1-5): limit / purge alarms disabled
                if frozen:
                    st = 'frozen'
            out[ch] = dict(mode=c['mode'], quantity=c['quantity'], value=v, unit=unit, state=st)
        if malf:
            al.append('malfunction')
        self.analog = out
        self.alarms = sorted(set(al))

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'measurement.send', 'errors.read'):
            self.revision += 1
        if capability_id != 'sim.step' and self.plant is not None:
            self.errors = self._errors_now()
            self._outputs()
        return out

    def _cmd_plant_configure(self, ambient_k, ambient_rh, pressure_pa, calibration_age_s, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(ambient_k=ambient_k, ambient_rh=ambient_rh, pressure_pa=pressure_pa)
        self.seed = noise_seed
        if first:
            self.cal_age0, self.cal_t0 = calibration_age_s, self.t
            self.x_sensor = self._x_ambient()
            self.y_fast = self.y_slow = ENG['cap_storage_dew_point_k']      # kept dry under the transport cap (user guide)
        self._event('system', 'PLANT_CONFIGURED', '环境条件已声明')
        return dict(configured=True)

    def _cmd_process_set(self, gas, water_umol_mol, pressure_pa, temperature_k):
        self._need_plant()
        if self.installed and self.inst['mode'] == 'direct' and pressure_pa > DOC['operating_pressure_pa'][1]:
            raise ActionRejected('PRESSURE_TOO_HIGH')
        if dewpoint(water_umol_mol * 1e-6 / (1 + water_umol_mol * 1e-6), pressure_pa) > temperature_k:
            raise ActionRejected('CONDENSING')                              # liquid water in the line: the declared gas cannot hold this much vapour
        if self.installed and self.inst['mode'] == 'sampling_line' and (self.inst.get('sensor_pressure_pa') or 0.0) > pressure_pa * 1.0001:
            raise ActionRejected('INVALID_SETUP')
        first = self.proc is None
        self.proc = dict(gas=gas, water_umol_mol=water_umol_mol, pressure_pa=pressure_pa, temperature_k=temperature_k)
        if first and not self.cells and self.inst['mode'] == 'direct':
            self.x_sensor = self._x_ambient()
        notes = []
        cls = gases()[gas]['compatibility']
        if cls == 'not_suitable':
            notes.append(f'{gas}：样本写明不适用（氢气或纯二氧化碳），读数会偏离')
        elif cls == 'consult_manufacturer':
            notes.append(f'{gas}：样本只列空气、氮、氩、氦、氧；其他化学品须咨询厂家')
        Td = dewpoint(water_umol_mol * 1e-6 / (1 + water_umol_mol * 1e-6), pressure_pa)
        if self.installed and self.inst['mode'] == 'sampling_line':
            Ps = self.inst.get('sensor_pressure_pa') or pressure_pa
            if dewpoint(water_umol_mol * 1e-6 / (1 + water_umol_mol * 1e-6), Ps) > self.plant['ambient_k']:
                notes.append('取样管在室温下低于工艺气露点：管内与传感器处会冷凝（读数饱和在传感器温度）')
        self._event('truth', 'PROCESS_SET', f'工艺气体{gas}：{water_umol_mol:g} µmol/mol，{pressure_pa / 1e5:.3g} bar')   # process truth: hidden from the agent
        return dict(gas=gas, dew_point_at_line_k=Td, notes=notes)

    def _cmd_installation_set(self, mode, tubing_material=None, tubing_length_m=None, tubing_inner_diameter_m=None, flow_m3_s=None, sensor_pressure_pa=None, pre_dried=None):
        self._need_plant()
        if self.cap_on:
            raise ActionRejected('CAP_ON')
        new = dict(self.inst, mode=mode, **{k: v for k, v in dict(tubing_material=tubing_material, tubing_length_m=tubing_length_m,
                                                                   tubing_inner_diameter_m=tubing_inner_diameter_m, flow_m3_s=flow_m3_s,
                                                                   sensor_pressure_pa=sensor_pressure_pa, pre_dried=pre_dried).items() if v is not None})
        if mode == 'direct':
            new['sensor_pressure_pa'] = None
            if self.proc and self.proc['pressure_pa'] > DOC['operating_pressure_pa'][1]:
                raise ActionRejected('PRESSURE_TOO_HIGH')
        elif new['sensor_pressure_pa'] is None:
            raise ActionRejected('MISSING_SETTING')
        elif self.proc and new['sensor_pressure_pa'] > self.proc['pressure_pa'] * 1.0001:
            raise ActionRejected('INVALID_SETUP')                            # a sampling line cannot raise the pressure above the process line
        new['pre_dried'] = bool(pre_dried)
        old_walls = self._wall_mol()
        self.led['water_removed_with_tubing_mol'] += old_walls              # the old tubing leaves with its water
        self.inst = new
        self.installed = True
        self._init_line(wet=not new.get('pre_dried'))
        notes = []
        m = tubing()[new['tubing_material']]
        if mode == 'sampling_line' and m['kind'] == 'polymer':
            notes.append('手册：塑料等吸湿材料不宜用作取样管（透湿与吸湿使干端读数偏湿、响应变慢）')
        if mode == 'sampling_line' and self.proc and new['sensor_pressure_pa'] < self.proc['pressure_pa'] * 0.95:
            notes.append('传感器处压力低于工艺管道压力：露点随压力降低（ppm换算应设PRES为传感器处压力）')
        if mode == 'sampling_line' and not new.get('pre_dried'):
            notes.append('新装取样管与室内空气平衡（湿）：干燥需要时间，测量前先用干气吹扫')
        self._event('operation', 'INSTALLATION', f'安装方式{mode}')
        return dict(mode=mode, sensor_pressure_pa=self._sensor_pressure(), notes=notes)

    def _cmd_power_set(self, on, voltage_v=None):
        self._need_plant()
        if voltage_v is not None:
            self.voltage = float(voltage_v)
        if on and not self.powered:
            self.powered = True
            self._start_purge(DOC['startup_normal_s'][0])                   # power-up: self-diagnostics and purge, normal operation after about 10 min
            self.startup = True
            self.next_autocal = self.t + DOC['startup_normal_s'][0] + 60.0
            self.last_reading = None
            self._event('system', 'POWER_ON', '上电：先做传感器吹扫与自动校准')
        elif not on and self.powered:
            self.powered = False
            self.state, self.purge_left, self.autocal_left = 'off', 0.0, 0.0
            self.reading = self.last_reading = None
            self._event('system', 'POWER_OFF', '断电')
        return dict(on=self.powered, voltage_v=self.voltage)

    def _cmd_settings_configure(self, pressure_pa=None, xpressure_pa=None, molar_mass_kg_mol=None, purge_enabled=None):
        self._need_plant()
        if not self.powered:
            raise ActionRejected('POWER_OFF')
        if xpressure_pa is not None and 0.0 < xpressure_pa < DOC['pres_min_pa']:
            raise ActionRejected('PRESSURE_TOO_LOW')
        self.settings.update({k: v for k, v in dict(pressure_pa=pressure_pa, xpressure_pa=xpressure_pa, molar_mass_kg_mol=molar_mass_kg_mol,
                                                     purge_enabled=purge_enabled).items() if v is not None})
        return dict(settings=dict(self.settings))

    def _cmd_output_configure(self, channel, mode=None, quantity=None, lo=None, hi=None, limit_alarm='__keep__', purge_status=None, error_level=None):
        self._need_plant()
        if not self.powered:
            raise ActionRejected('POWER_OFF')
        c = dict(self.channels[channel])
        c.update({k: v for k, v in dict(mode=mode, quantity=quantity, lo=lo, hi=hi, purge_status=purge_status, error_level=error_level).items() if v is not None})
        if limit_alarm != '__keep__':
            c['limit_alarm'] = limit_alarm                                 # null clears the limit alarm
        if c['hi'] <= c['lo'] or (c['limit_alarm'] is not None and c['limit_alarm']['hi'] <= c['limit_alarm']['lo']):
            raise ActionRejected('RANGE_ORDER')
        lo_o, hi_o, _ = MODES[c['mode']]
        if not min(lo_o, 0.0) <= c['error_level'] <= max(hi_o, 0.0) * 1.1:
            raise ActionRejected('ERROR_LEVEL_OUT_OF_RANGE')
        self.channels[channel] = c
        self.limit_on[channel] = False
        return dict(channel=channel, config=dict(c))

    def _cmd_cap_remove(self):
        self._need_plant()
        if not self.cap_on:
            raise ActionRejected('CAP_NOT_FITTED')
        self.cap_on = False
        self._event('operation', 'CAP_REMOVED', '取下运输保护帽')
        return dict(fitted=False)

    def _cmd_cap_fit(self):
        self._need_plant()
        if self.cap_on:
            raise ActionRejected('CAP_ALREADY_FITTED')
        if self.installed:
            raise ActionRejected('INSTALLED')
        self.cap_on = True
        self._event('operation', 'CAP_FITTED', '装回运输保护帽')
        return dict(fitted=True)

    def _cmd_installation_remove(self):
        self._need_plant()
        if not self.installed:
            raise ActionRejected('NOT_INSTALLED')
        if self.cells:
            self.led['water_removed_with_tubing_mol'] += self._wall_mol()
            self.cells, self.bulk = [], []
        self.installed = False
        self._event('operation', 'UNINSTALLED', '从管道拆下变送器')
        return dict(installed=False)

    def _cmd_measurement_send(self):
        self._need_plant()
        if not self.powered or self.voltage < DOC['supply_v']['rs485'][0]:
            raise ActionRejected('POWER_OFF')
        td, ppm, ppmw = self._quantities()
        return dict(tdf_k=td, h2o_umol_mol=ppm, h2o_mg_kg=ppmw, frozen=self.state == 'purge', errors=list(self.errors))

    def _cmd_errors_read(self):
        self._need_plant()
        if not self.powered or self.voltage < DOC['supply_v']['rs485'][0]:
            raise ActionRejected('POWER_OFF')
        return dict(errors=list(self.errors))

    def _cmd_calibration_service(self):
        self._need_plant()
        if self.powered:
            raise ActionRejected('POWER_ON')
        if self.installed:
            raise ActionRejected('INSTALLED')                                # sent to the service: removed from the process first
        self.cal_epoch += 1
        self.unsuitable_s = 0.0
        self.cal_age0, self.cal_t0 = 0.0, self.t
        self.nopurge_s = 0.0
        self.autocal_ok, self.autocal_fails = 0, 0
        self._event('operation', 'CALIBRATED', '送厂校准完成（新的标定偏差，漂移清零）')
        return dict(calibrated=True, calibration_age_s=0.0)

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s - 1e-9], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        v = self.read('evaluator')
        return dict(gas=v['truth.gas'], sensor=v['truth.sensor'], line=v['truth.line'], faults=v['truth.faults'])

    def _ledger(self):
        L = copy.deepcopy(self.led)
        L['water_on_walls_mol'] = self._wall_mol()
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
        td, ppm, ppmw = self._quantities() if pw else (None, None, None)
        out = {'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'process.configured': self.proc is not None, 'power.on': self.powered,
               'supply.voltage_v': self.voltage, 'state.current': self.state, 'state.remaining_s': self.purge_left if self.state == 'purge' else None,
               'autocal.active': pw and self.autocal_left > 0.0, 'reading.tdf_k': td, 'reading.h2o_umol_mol': ppm, 'reading.h2o_mg_kg': ppmw,
               'reading.frozen': pw and self.state == 'purge',
               'reading.in_calibrated_range': td is not None and DOC['calibrated_range_k'][0] <= td <= DOC['calibrated_range_k'][1],
               'output.analog': {f'ch{k}': dict(v) for k, v in self.analog.items()}, 'diagnostics.errors': list(self.errors) if pw else [],
               'alarms.active': list(self.alarms) if pw else [], 'settings.current': dict(self.settings),
               'installation.current': dict(self.inst, installed=self.installed, sensor_pressure_pa=self._sensor_pressure() if self.plant else None),
               'cap.fitted': self.cap_on, 'cap.offset_m': 0.0 if self.cap_on else CAP_TRAVEL,
               'calibration.age_s': self._cal_age() if self.plant else self.cal_age0}
        if visibility != 'agent':
            if self.plant is None:
                out['truth.gas'], out['truth.sensor'], out['truth.line'] = {}, {}, {}
            else:
                P = self._sensor_pressure()
                xp = self._x_process()
                out['truth.gas'] = dict(x_process=xp, x_sensor=self.x_sensor, sensor_pressure_pa=P, sensor_temperature_k=self._sensor_temperature(),
                                        e_sensor_pa=self.x_sensor * P, dew_point_sensor_k=dewpoint(self.x_sensor, P),
                                        dew_point_line_k=dewpoint(xp, self.proc['pressure_pa']) if self.proc else None,
                                        ppmv_true=self.x_sensor / (1.0 - self.x_sensor) * 1e6, gas=self.proc['gas'] if self.proc else 'room_air',
                                        gas_compatibility=gases()[self.proc['gas']]['compatibility'] if self.proc else 'suitable')
                y = self._y() if self.y_fast is not None else 0.0
                out['truth.sensor'] = dict(y_fast_k=self.y_fast, y_slow_k=self.y_slow, y_k=y if self.y_fast is not None else None, target_k=self._target_td(), offset_k=self._offset(y), drift_k=self._drift(y),
                                           gas_bias_k=self._gas_bias(), autocal_successes=self.autocal_ok, autocal_failures_in_row=self.autocal_fails,
                                           next_autocal_s=self.next_autocal, next_purge_s=self.next_purge, calibration_epoch=self.cal_epoch)
                n, A, d = self._line_geometry()
                out['truth.line'] = dict(cells=len(self.cells), wall_loading_mol_m2=list(self.cells), wall_water_mol=self._wall_mol(),
                                         flow_mol_s=self._flow_mol_s(), material=self.inst['tubing_material'] if self.cells else None)
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
class VaisalaDmt152Session:
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
        meta = copy.deepcopy(export_vaisala_dmt152_contract_metadata())
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
    'PLANT_REQUIRED': '尚未声明环境条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '变送器未供电（或电压低于RS-485所需11 V）',
    'POWER_ON': '须先断电拆下（送厂校准）', 'PRESSURE_TOO_HIGH': '直接安装时工艺压力超过50 bar（最大测量压力）', 'PRESSURE_TOO_LOW': 'XPRES低于0.1 bara（0为取消）',
    'MISSING_SETTING': '取样安装须给传感器处压力', 'CAP_ON': '运输保护帽还装着（安装前须取下）', 'CAP_NOT_FITTED': '保护帽已取下',
    'CONDENSING': '工艺气体的露点高于气体温度（会冷凝成液态水）', 'INVALID_SETUP': '传感器处压力不能高于工艺管道压力', 'CAP_ALREADY_FITTED': '保护帽已装着', 'INSTALLED': '变送器还装在管道上（先拆下）', 'NOT_INSTALLED': '变送器没有安装', 'RANGE_ORDER': '量程或报警上限须大于下限', 'ERROR_LEVEL_OUT_OF_RANGE': '故障输出电平超出该输出模式的范围',
}
_P = ['PLANT_REQUIRED']
_PW = _P + ['POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'process.set': _P + ['PRESSURE_TOO_HIGH', 'CONDENSING', 'INVALID_SETUP'], 'installation.set': _P + ['CAP_ON', 'PRESSURE_TOO_HIGH', 'MISSING_SETTING', 'INVALID_SETUP'],
    'cap.remove': _P + ['CAP_NOT_FITTED'], 'cap.fit': _P + ['CAP_ALREADY_FITTED', 'INSTALLED'], 'installation.remove': _P + ['NOT_INSTALLED'],
    'power.set': _P, 'settings.configure': _PW + ['PRESSURE_TOO_LOW'], 'output.configure': _PW + ['RANGE_ORDER', 'ERROR_LEVEL_OUT_OF_RANGE'],
    'measurement.send': _PW, 'errors.read': _PW, 'calibration.service': _P + ['POWER_ON', 'INSTALLED'], 'events.read': [], 'truth.inspect': [], 'ledger.read': [],
    'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子与校准龄；之后再声明须给同一种子（校准龄只在首次生效）', ['plant.configured']),
    'process.set': ('直接安装时工艺压力≤50 bar', ['installation.current']),
    'installation.set': ('运输保护帽已取下；直接安装时工艺压力≤50 bar；取样安装须给传感器处压力；更换取样管时旧管连同其吸附水一并移走', ['cap.fitted', 'installation.current', 'process.configured']),
    'cap.remove': ('保护帽装着', ['cap.fitted']), 'cap.fit': ('保护帽已取下且变送器未安装', ['cap.fitted', 'installation.current']),
    'installation.remove': ('变送器已安装', ['installation.current']),
    'power.set': ('上电先做传感器吹扫（数分钟，输出冻结），断电时读数与输出消失', ['power.on']),
    'settings.configure': ('已供电；XPRES为0（取消）或≥0.1 bara', ['power.on', 'settings.current']),
    'output.configure': ('已供电；量程与报警上限大于下限；故障电平在该模式范围内', ['power.on', 'output.analog']),
    'measurement.send': ('已供电且电压≥11 V（RS-485）', ['power.on', 'supply.voltage_v']), 'errors.read': ('同measurement.send', ['power.on', 'supply.voltage_v']),
    'calibration.service': ('已断电并从管道拆下（送厂校准）', ['power.on', 'installation.current']), 'fault.inject': ('无', ['plant.configured']),
}
_EVOLVE = ['state.current', 'state.remaining_s', 'autocal.active', 'reading.tdf_k', 'reading.h2o_umol_mol', 'reading.h2o_mg_kg', 'reading.frozen',
           'reading.in_calibrated_range', 'output.analog', 'diagnostics.errors', 'alarms.active', 'calibration.age_s', 'truth.gas', 'truth.sensor', 'truth.line']
_OUT = ['output.analog', 'alarms.active', 'diagnostics.errors']
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.gas', 'truth.sensor', 'truth.line', 'calibration.age_s', 'installation.current', 'reading.tdf_k', 'reading.h2o_umol_mol',
                        'reading.h2o_mg_kg', 'reading.in_calibrated_range'] + _OUT,
    'process.set': ['process.configured', 'truth.gas', 'truth.sensor', 'installation.current', 'reading.tdf_k', 'reading.h2o_umol_mol', 'reading.h2o_mg_kg',
                    'reading.in_calibrated_range'] + _OUT,
    'installation.set': ['installation.current', 'truth.gas', 'truth.sensor', 'truth.line', 'reading.tdf_k', 'reading.h2o_umol_mol', 'reading.h2o_mg_kg',
                         'reading.in_calibrated_range'] + _OUT,
    'installation.remove': ['installation.current', 'truth.gas', 'truth.sensor', 'truth.line', 'reading.tdf_k', 'reading.h2o_umol_mol', 'reading.h2o_mg_kg',
                            'reading.in_calibrated_range'] + _OUT,
    'cap.remove': ['cap.fitted', 'cap.offset_m'], 'cap.fit': ['cap.fitted', 'cap.offset_m'],
    'power.set': ['power.on', 'supply.voltage_v', 'state.current', 'state.remaining_s', 'autocal.active', 'reading.tdf_k', 'reading.h2o_umol_mol', 'reading.h2o_mg_kg',
                  'reading.frozen', 'reading.in_calibrated_range', 'truth.sensor'] + _OUT,
    'settings.configure': ['settings.current', 'reading.h2o_umol_mol', 'reading.h2o_mg_kg'] + _OUT, 'output.configure': _OUT,
    'calibration.service': ['calibration.age_s', 'truth.sensor'], 'fault.inject': ['truth.faults', 'truth.gas', 'truth.sensor', 'truth.line'] + _OUT,
    'sim.step': ['clock.time_s'],
}
ON_STEP = {k: list(_EVOLVE) for k in ('sim.step', 'process.set', 'installation.set', 'installation.remove', 'cap.remove', 'cap.fit', 'power.set', 'settings.configure', 'output.configure', 'calibration.service',
                                      'fault.inject', 'plant.configure')}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {
    'sim.step': '按0.5 s网格（取样管5 s子网格）积分：取样管吸附/渗透/漏入、传感器等效露点（吸湿快、干燥慢）、吹扫与自动校准、读数、偏差与漂移、模拟输出与报警',
    'process.set': '新的水含量经取样管（吸附缓冲）到达传感器，读数按不对称响应变化', 'installation.set': '新装取样管从室内湿度开始干燥（或已吹干）',
    'power.set': '上电吹扫数分钟后开始测量，此后每45 min自动校准、每周吹扫', 'settings.configure': 'ppm换算与吹扫设定立即生效，停用吹扫后湿端漂移增大',
    'output.configure': '输出随读数更新', 'calibration.service': '新的标定偏差与零漂移从下一步起作用', 'fault.inject': '故障改变响应、读数或取样流量',
    'plant.configure': '室内湿度影响取样管渗透与漏入', 'cap.remove': '传感器接触室内空气后很快吸湿', 'cap.fit': '保护帽下传感器保持当前状态',
    'installation.remove': '拆下后传感器接触室内空气',
}
EXEC_TEXT = {'power.set': ('state.current经purge（吹扫，约5 min）变为measuring', '断电中止吹扫与自动校准')}
RESOURCES = {k: ['host_clock'] for k in CAPS}
RESOURCES['installation.set'] = ['host_clock', 'process_thread']
OBSERVES = {'power.set': ['state.current', 'reading.tdf_k'], 'process.set': ['reading.tdf_k'], 'installation.set': ['reading.tdf_k']}
_WB, _DS, _UG, _HD, _CM, _NI, _PC, _SIM = ('user_workbook', 'manufacturer_datasheet', 'manufacturer_user_guide', 'hardy_its90_1998', 'cmc_film_wvtr', 'nist_webbook',
                                           'pubchem', 'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({'cap.remove': [_UG], 'cap.fit': [_UG], 'installation.remove': [_UG], 'process.set': [_DS, _HD, _NI, _PC], 'installation.set': [_UG, _CM, _SIM], 'power.set': [_DS, _UG], 'settings.configure': [_UG],
                    'output.configure': [_DS, _UG], 'measurement.send': [_DS, _UG, _HD], 'errors.read': [_UG], 'calibration.service': [_DS, _WB],
                    'fault.inject': [_UG, _SIM]})
CAP_SOURCES.update({k: [_DS, _UG] for k in UNSUPPORTED})
PARAM_ZH = {'ambient_k': '室内温度', 'ambient_rh': '室内相对湿度（0–1）', 'pressure_pa': '压力', 'calibration_age_s': '距上次校准的时间（只在首次声明时生效）',
            'noise_seed': '噪声随机数种子（只在首次声明时生效）', 'gas': '工艺气体种类（气体库）', 'water_umol_mol': '工艺气体中水含量（每摩尔干气的µmol）',
            'temperature_k': '气体温度', 'mode': '安装方式/输出模式', 'tubing_material': '取样管材料（取样管库）', 'tubing_length_m': '取样管长度',
            'tubing_inner_diameter_m': '取样管内径', 'flow_m3_s': '取样流量（20 °C、1 atm标准状态）', 'sensor_pressure_pa': '传感器（取样池）处绝对压力',
            'pre_dried': '取样管是否已预先吹干', 'xpressure_pa': '临时压力XPRES（0为取消）', 'molar_mass_kg_mol': 'ppmw换算用的气体摩尔质量（MOL）',
            'purge_enabled': '是否启用传感器吹扫（XPUR）', 'channel': '模拟输出通道', 'quantity': '输出量（tdf露/霜点K或ppm）', 'lo': '量程下限（K或µmol/mol）',
            'hi': '量程上限', 'limit_alarm': '限值报警上下限（null为关闭）', 'purge_status': '吹扫期间输出吹扫状态电平', 'error_level': '故障输出电平（A或V）',
            'voltage_v': '供电电压', 'on': '是否供电'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.trace_moisture_measurement.limitations。'
CAP_LIMITS = {
    'installation.set': ['取样管按N段准稳态气相与逐段管壁交换（传质限制）：金属为多类Langmuir吸附（单层×粗糙度、吸附能为工程值）；聚合物管壁按三层体相吸水（溶解度由50 %RH平衡吸湿量、扩散系数=渗透率/溶解度，均为工程值）与透湿（CMC汇编的薄膜WVTR，参考条件按38 °C/90 %RH假设，按壁厚换算）；接头、阀门与死体积不单独建模。',
                         'CMC表中PTFE（200 µm薄膜18 g/(m²·d)）按单位厚度的透湿率高于PA6（25 µm 60 g/(m²·d)），与常见“尼龙比PTFE更透湿”的排序相反：运行层照样使用文献表值（PTFE可能为多孔级），PTFE管在室内湿气下读数偏湿。'],
    'process.set': ['工艺气体用水的摩尔分数表示；增强因子按Greenspan的湿空气式用于所有载气（工程近似）；不适用气体（氢、纯CO₂）的读数偏差为工程值。'],
    'calibration.service': ['送厂校准抽象为瞬时完成；新的标定偏差在准确度指标内随机抽取（每台仪器、每次校准不同）。'],
    'power.set': ['吹扫持续时间按手册“several minutes”取5 min（工程值）；吹扫时传感器多余的水被赶出（等效露点不高于当前目标）。'],
}
LIMITATIONS = [
    '所选配置：DMT152，ISO G1/2"螺纹、不锈钢网过滤器，模拟输出2路＋RS-485；DM70手持表、取样池与显示器为附件（取样池按installation.set的抽象）。',
    '露/霜点：Hardy 1998 ITS-90的水/冰饱和蒸气压与反算式，增强因子Greenspan式（−100…+100 °C；湿空气）；0 °C以下输出霜点。',
    '传感器响应：等效露点一阶模型，吸湿单指数（T63 2 s），干燥两段并联指数（快段比例与时间常数由T63 0.5 min、T90 7.5 min标定）；低温按阿伦尼乌斯变慢（工程值）；油污染使响应变慢（工程值）。',
    '准确度：每台仪器的标定偏差在干端/湿端指标内抽取（σ=指标×0.4），长期漂移速率抽取在±2 °C/年内，自动校准修正大部分干端漂移；具体自动校准与吹扫算法为厂家专有，按手册描述抽象。',
    '取样系统：传感器本身与压力无关；ppm按设定压力换算——设定压力与传感器处压力不一致时ppm有误差（手册警告）；管壁吸附、渗透与漏入为工程模型（tubing_library.json逐项说明）。',
    'ppmv按Vaisala定义e/(P−e)·10⁶（每摩尔干气），ppmw=ppmv·M_水/M_气（MOL设定）。',
    '上电：自检与吹扫期间输出冻结，一分钟后输出近似读数，约10 min后正常测量（手册：通常10–15 min）；每周吹扫按5 min（手册“several minutes”，工程值）。',
    '聚合物取样管：三层体相吸水使尼龙等吸湿管干燥需数天到数周（厚度1 mm、吸湿量与扩散系数为工程值）；PTFE按CMC表值透湿较高（见installation.set）。',
    '工艺气体、取样管与电缆在场景中没有几何；变送器本体为被动资产（没有显示屏与按键，读数经运行层与RS-485/模拟输出读取）。',
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
    src_of = {'manufacturer_datasheet': _DS, 'manufacturer_user_guide': _UG, 'user_workbook': _WB}
    WB_KEYS = ('calibrated_range_k', 'accuracy_dry_k', 'accuracy_wet_k', 't63_drying_s', 't63_wetting_s', 'mass_kg', 'length_m', 'body_diameter_m', 'calibration_interval_s')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS and v['source_id'] != 'user_workbook' else ()))
    for k, v in _lib('humidity')['entries'].items():
        doc('hardy_' + k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (_HD,))
    cal = {k for k, v in instrument()['engineering'].items() if v.get('calibrated_by')}
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明），不是实测' +
            ('；由calibrate_dp.py按样本T63/T90标定' if k in cal else ''))
    asm('line_update_interval_s', LINE_DT, 's', '取样管更新的子网格间隔（锚定t=0）', '数值方法参数')
    asm('polymer_tube_wall_m', TUBE_WALL_M, 'm', '聚合物取样管壁厚（透湿换算）', '工程值')
    return P


SLOT_REFERENCE = {'reading.tdf_k': '传感器处露/霜点（0 °C以下为霜点），热力学温度', 'reading.h2o_umol_mol': '按设定压力（PRES/XPRES）换算，每摩尔干气',
                  'reading.h2o_mg_kg': '按设定摩尔质量（MOL）换算', 'supply.voltage_v': '直流供电电压'}


def export_vaisala_dmt152_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'VaisalaDmt152Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.vaisala_dmt152.VaisalaDmt152Session.invoke',
                                              invocation=f'session.invoke(instance_id="vaisala_dmt152_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='VaisalaDmt152Runtime.invoke')],
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
        component_ids=['drycap_sensor', 'sensor_filter', 'process_thread', 'electronics', 'm8_connector'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='痕量水露点测量：工艺气体中的水经（取样管管壁吸附缓冲后）到达薄膜电容聚合物传感器，传感器等效露点按吸湿快、干燥慢的不对称响应趋近真实露/霜点；'
                   '读数含标定偏差与漂移，由自动校准与周期吹扫抑制；ppm按设定压力与摩尔质量换算，经两路模拟输出与RS-485输出',
        equations=['x P = f(T_d, P) e_s(T_d);  e_s = e_ice(T) below 0 C (frost point) else e_water(T)  (Hardy 1998, ITS-90)',
                   'ln e_w = sum g_i T^(i-2) + g7 ln T;  ln e_i = sum k_i T^(i-1) + k5 ln T;  f = exp(alpha (1 - e_s/P) + beta (P/e_s - 1))',
                   'line cell: n_dot (x_in - x_out) = A dn/dt;  x_out = x_eq + (x_in - x_eq) exp(-a P / n_dot),  a = Sh D / d A / (R T);  n_eq = N K p / (1 + K p)',
                   'polymer permeation: J = WVTR (d_ref / d_wall) (p_amb - p_in) / dp_ref',
                   'sensor: wetting dy/dt = (T_target - y) / tau_w;  drying y = a y_1 + (1 - a) y_2,  dy_k/dt = (T_target - y_k) / tau_k  (x exp(E/R (1/T - 1/293.15)))',
                   'reading = y + offset(y) + drift(age, y) + noise;  ppmv = e / (P_set - e) 1e6;  ppmw = ppmv M_w / M_gas',
                   'analog = lo_out + (hi_out - lo_out) clip((q - lo) / (hi - lo), 0, 1); limit alarm -> full scale; purge status -> low level; malfunction -> error level'],
        parameters=_params(), source_ids=[_SIM, _WB, _DS, _UG, _HD, _CM, _NI, _PC], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_dmt_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('vaisala_dmt152', caps, slots, {'trace_moisture_measurement': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'process.set': (PROCESS, PROCESS_EXAMPLE), 'installation.set': (INSTALL, INSTALL_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'trace_moisture_measurement': mechanism})
