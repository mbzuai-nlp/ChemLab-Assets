"""CHEM-008 IKA Plate (RCT digital) 25004601 magnetic hotplate stirrer: process runtime.

Device (IKA datasheet, Technische Daten; operating instructions 20014388a, 11/2018): one stirring position for up to 20 l water, 9 W motor,
50-1500 rpm in 10 rpm steps, right / left rotation, automatic reverse and intermittent mode, viscosity trend; Ø 135 mm aluminium-alloy plate,
600 W heater, plate set point 0-310 C (1 K), adjustable safety circuit 50-370 C (factory: max set + 60 C; Err. 25 when it cuts the heater), PT1000 /
ETS-D5 / ETS-D6 medium control (PID or 2P), Error 5 'sensor not in medium' detection (1-30 min, only below 50 C and > 5 K from the set point), timer
(1 s - 143940 min) / counter, lock, operating modes A / B / D, HOT! display above 50 C when switched off, SmartTemp hot-surface warning, NAMUR
commands over USB / RS 232 with watchdog modes 1 and 2, 160 x 85 x 270 mm, 2.4 kg.

Process model (fixed 0.1 s grid; quiet intervals - everything at ambient, heater and motor off, no open liquid on the plate - are skipped exactly,
a running timer / counter is advanced analytically over them; SI units: K, W, J, kg, m, rad/s):
  * Plate node C_p dT_p/dt = P_heater + P_magnet - G_p (T_p - T_a) - eps sigma A_exposed (T_p^4 - T_a^4) - Q_pl.  P_magnet = P_max (n/1500)^2
    (eddy currents of the drive magnets, calibrated to the datasheet's +13 K after 1 h at 1500 rpm).
  * Vessel + liquid node (C_l = m cp + m_v cp_v): Q_pl = U A_bottom f_stir f_mat (T_p - T_l) with U calibrated to 6.5 K/min (1 l water in an
    H1500 beaker); losses h_side A (T_l - T_a); open-surface evaporation k_m A (p_vap(T) - p_background) M / (R T) with its latent heat (water
    vapour in the room air: relative humidity x p_vap(T_a)); boiling at the normal
    boiling point corrected to the ambient pressure (Clausius-Clapeyron with the template's enthalpy of vaporisation): the excess heat boils
    liquid off.  Properties from liquid_templates.json (51 liquids, NIST / PubChem).
  * Control: plate PI on the plate sensor (no external sensor); with a PT1000 in the medium the medium is controlled (PID: the plate target
    follows a PID of the medium error, limited to the plate maximum; 2P: full plate maximum below the set point, heater off above it); the
    safety sensor (plate centre) trips the heater at the safety limit (Err. 25).  Sensor first-order lags (liquid / air).
  * Stirring: motor ramps to the set speed; the magnetic coupling transmits at most tau_c(L); the bar's drag tau = a mu w L^3 + b rho w^2 L^5
    decides whether the bar follows or decouples (the coupling torque limits the load, so viscosity alone never overloads the 9 W motor); a
    blocked motor gives Err. 4; the viscosity trend is the load torque relative to the torque when the speed had settled (100 %).
  * Ledgers: electric heater + magnet energy = change of the plate and charge enthalpy + losses + latent heat; liquid mass = remaining +
    evaporated + boiled off + removed with the vessel."""
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.ika_plate_rctdigital"
CATALOG_ID = "CHEM-008"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch05/t4_measure/ika_plate_rctdigital'
LIBS = {'instrument': 'instrument_model.json', 'liquids': 'liquid_templates.json'}
C0 = 273.15
DT = 0.1
SIGMA = 5.670374419e-8
R_GAS = 8.314462618
RPM = 2.0 * math.pi / 60.0
MAX_EVENTS_LOG = 3000
TEMP_PER_RAD = 100.0 / (2.0 * math.pi)                                  # temperature knob: 100 K per turn (encoder; engineering)
SPEED_PER_RAD = 500.0 / (2.0 * math.pi)                                 # speed knob: 500 rpm per turn (engineering)


def _step_round(x, step):
    """Round half up to a multiple of step (a knob in steps never rounds half to even)."""
    return math.floor(x / step + 0.5 + 1e-12) * step


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def instrument():
    return _lib('instrument')


DOC = {k: v['value'] for k, v in instrument()['documented'].items()}
ENG = {k: v['value'] for k, v in instrument()['engineering'].items()}
ENG_NOTE = {k: v['engineering_note_zh'] for k, v in instrument()['engineering'].items()}
ENG_UNIT = {k: v['unit'] for k, v in instrument()['engineering'].items()}
LIQ = _lib('liquids')['substances']
LIQ_IDS = sorted(LIQ)
VESSELS = ENG['vessels']
PLATE_MAX_K = C0 + DOC['set_temperature_range_c'][1]
SAFE_RANGE_K = (C0 + DOC['safety_circuit_c'][0], C0 + DOC['safety_circuit_c'][1])
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


# ---------------------------------------------------------------- liquid properties
def p_vap(sid, t_k):
    """Vapour pressure (Pa): the template's Antoine fit (log10 p/bar = A - B/(T + C)), or its two-point Clausius-Clapeyron line (a measured point
    and the normal boiling point; liquids without a NIST Antoine fit in the CHEM-200 library, or whose fit misses 1 atm at the NIST boiling point
    by more than 15 % - then through (T_b, 1 atm) with the enthalpy of vaporisation)."""
    v = LIQ[sid]['vapour_pressure']
    v = v.get('runtime_model', v)                                         # Antoine fits inconsistent with the NIST boiling point are replaced
    if v['kind'] == 'antoine':
        return 1e5 * 10.0 ** (v['A'] - v['B_k'] / (t_k + v['C_k']))
    return v['p_ref_pa'] * math.exp(-v['dh_vap_j_mol'] / R_GAS * (1.0 / t_k - 1.0 / v['t_ref_k']))


def boiling_k(sid, pressure_pa):
    """Boiling point at the ambient pressure: NIST normal boiling point shifted by Clausius-Clapeyron with the enthalpy of vaporisation."""
    s = LIQ[sid]
    tb = s['boiling_point']['value']
    hv = s['enthalpy_vaporization']['molar_j_mol']
    return 1.0 / (1.0 / tb - R_GAS / hv * math.log(pressure_pa / 101325.0))


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
TEXT = {'type': 'string', 'maxLength': 80}
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


TEMP = number('K', C0, PLATE_MAX_K)
SPEED_RAD = number('rad/s', 0.0, DOC['speed_range_rpm'][1] * RPM)
PLANT_EXAMPLE = dict(ambient_k=295.15, pressure_pa=101325.0, mains_v=230.0, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', C0 + DOC['ambient_range_c'][0], C0 + DOC['ambient_range_c'][1]), pressure_pa=number('Pa', 70000.0, 110000.0),
                 mains_v=number('V', 180.0, 260.0), noise_seed=integer(0, 2 ** 31 - 1), relative_humidity=number('1', 0.0, 1.0)), optional=('relative_humidity',))
VESSEL = obj(dict(vessel=enum(sorted(VESSELS)), substance_id=enum(LIQ_IDS), volume_m3=number('m3', 1e-6, 5e-3), initial_k=number('K', 250.0, 400.0),
                  stir_bar_length_m=number('m', 0.0, 0.08)), optional=('initial_k', 'stir_bar_length_m'))
MENU = obj(dict(operating_mode=enum(['A', 'B', 'D']), beep=BOOL, unit=enum(['C', 'F']), err5_timeout_s=number('s', 0.0, 1800.0), expiry_stop_heating=BOOL,
                expiry_stop_stirring=BOOL, display=enum(['safe', 'visc'])),
           optional=('operating_mode', 'beep', 'unit', 'err5_timeout_s', 'expiry_stop_heating', 'expiry_stop_stirring', 'display'))
FAULTS = ['mains_outage', 'motor_blocked', 'sensor_short', 'triac_short', 'heater_switch_fault', 'safety_test_fault']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['housing'], '声明环境：室温、大气压、电源电压、相对湿度（默认0.5）与噪声种子（之后可改环境，种子不可改）',
    'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['power_switch'], '侧面电源开关（左开右关）')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['power_switch'], '电源开关拨一下（切换开/关）')
cap('heating.set_temperature', obj(dict(temperature_k=TEMP)), obj(dict(temperature_k=TEMP)), dict(temperature_k=C0 + 100.0), ['knob_temperature'],
    '温度旋钮：设定温度（无外接传感器时为加热盘温度，接PT1000时为介质温度；0–310 °C，1 K）')
cap('heating.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['temp_push_key'], '开/关加热功能')
cap('heating.toggle', obj({}), obj(dict(on=BOOL)), {}, ['temp_push_key'], '按温度旋钮：切换加热功能')
cap('heating.set_mode', obj(dict(mode=enum(['pid', '2p']))), obj(dict(mode=enum(['pid', '2p']))), dict(mode='pid'), ['housing'], '菜单：外接传感器控温方式（PID或两点）', 'agent', 'configuration')
cap('safety.set_limit', obj(dict(limit_k=number('K', *SAFE_RANGE_K))), obj(dict(limit_k=number('K'))), dict(limit_k=C0 + 200.0), ['safety_screw'],
    '用螺丝刀调安全温度螺钉（50–370 °C，任意位置；加热中调到加热盘温度以下即Err. 25切断加热，不加热时Err. 24）', 'agent', 'robot_skill')
cap('stirring.set_speed', obj(dict(speed_rad_s=SPEED_RAD)), obj(dict(speed_rad_s=SPEED_RAD)), dict(speed_rad_s=600 * RPM), ['knob_speed'],
    '转速旋钮：设定转速（0或50–1500 rpm，10 rpm步进）')
cap('stirring.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['speed_push_key'], '开/关搅拌功能')
cap('stirring.toggle', obj({}), obj(dict(on=BOOL)), {}, ['speed_push_key'], '按转速旋钮：切换搅拌功能')
cap('stirring.configure', obj(dict(direction=enum(['cw', 'ccw']), auto_reverse=BOOL, run_s=number('s', 0.0, 600.0), pause_s=number('s', 3.0, 300.0)),
                              optional=('direction', 'auto_reverse', 'run_s', 'pause_s')),
    obj(dict(pattern=FREE_OBJ)), dict(direction='cw', auto_reverse=False, run_s=0.0, pause_s=3.0), ['housing'], '菜单：转向、自动换向与间歇运行（运行0为连续）', 'agent', 'configuration')
cap('timer.set', obj(dict(duration_s=number('s', 0.0, DOC['timer_range_s'][1]))), obj(dict(timer=FREE_OBJ)), dict(duration_s=600.0), ['timer_key'],
    '定时器：设定倒计时（0为正计时计数器）')
cap('timer.toggle', obj({}), obj(dict(timer=FREE_OBJ)), {}, ['timer_key'], 'Timer键：启动/暂停定时或计数')
cap('timer.clear', obj({}), obj(dict(timer=FREE_OBJ)), {}, ['timer_key'], '按住Timer键2 s：退出定时/计数')
cap('menu.configure', MENU, obj(dict(menu=FREE_OBJ)), dict(operating_mode='A', err5_timeout_s=600.0, display='visc'), ['menu_key'],
    '菜单：工作模式A/B/D、提示音、温度单位、Error 5时限、定时结束动作、SAFE/VISC显示（须停加热与搅拌）', 'agent', 'configuration')
cap('lock.toggle', obj({}), obj(dict(locked=BOOL)), {}, ['lock_key'], '按住Lock键2 s：锁定/解锁旋钮与按键')
cap('viscosity.reset', obj({}), obj(dict(visc_trend=nullable(number('%')))), {}, ['menu_key'], '按住Menu键2 s：黏度趋势复位为100 %')
cap('sensor.set', obj(dict(connected=BOOL, immersed=BOOL)), obj(dict(sensor=FREE_OBJ)), dict(connected=True, immersed=True), ['sensor_jack'],
    '插拔PT1000温度传感器并放入或取出介质（插入深度≥20 mm）', 'agent', 'robot_skill')
cap('vessel.place', VESSEL, obj(dict(vessel=FREE_OBJ)), dict(vessel='h1500', substance_id='water', volume_m3=1e-3, stir_bar_length_m=0.04), ['heating_plate'],
    '把装有液体（模板库）与搅拌子的容器放到加热盘中央', 'agent', 'robot_skill')
cap('vessel.remove', obj({}), obj(dict(vessel=FREE_OBJ)), {}, ['heating_plate'], '取下容器：返回液体温度、剩余质量与蒸发量', 'agent', 'robot_skill')
cap('interface.set', obj(dict(connected=BOOL)), obj(dict(connected=BOOL)), dict(connected=True), ['usb_port'], '插拔USB/RS 232数据线（labworldsoft或NAMUR命令）', 'agent', 'robot_skill')
cap('namur.send', obj(dict(command=TEXT)), obj(dict(response=TEXT)), dict(command='IN_PV_2'), ['usb_port'], '发送NAMUR命令（IN_PV/IN_SP/OUT_SP/START/STOP/RESET/SET_MODE/看门狗）')
cap('medium.modify', obj(dict(viscosity_factor=number('1', 0.01, 1e5))), obj(dict(viscosity_pa_s=number('Pa s'))), dict(viscosity_factor=5.0), ['heating_plate'],
    '评测者：改变容器内介质的黏度（反应增稠或稀释的结果）', 'evaluator', 'simulation')
cap('events.read', obj(dict(since_s=number('s', 0.0)), optional=('since_s',)), obj(dict(events=array(FREE_OBJ), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), ['housing'],
    '事件与错误记录', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(process=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, ['housing'], '评测者：加热盘、介质、传感器、搅拌子的真实状态', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, ['housing'], '评测者：能量与液体质量守恒账', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='motor_blocked', active=True), ['housing'],
    '评测者：注入或清除故障（停电、电机卡死、传感器短路、加热TRIAC短路、加热开关元件故障、安全自检失败）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), ['housing'], '推进宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'stirbar.detect_break': '搅拌子脱落检测：本型号没有（技术数据Break detection stirring bar: no）。',
    'ph.measure': 'pH测量：本型号没有（技术数据pH measurement: no）。',
    'weighing.tare': '称量：本型号没有（技术数据Weighing function: no）。',
    'program.run': '温度/转速程序：本型号没有（技术数据Programs: no；时间斜坡须经labworldsoft在电脑上实现）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['housing'], _v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'housing', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'housing', '环境是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'power_switch', '电源开关是否在开位')
slot('heating.set_k', TEMP, 'K', 'setpoint', 'knob_temperature', '设定温度（加热盘或介质；温度旋钮转角=(设定−273.15 K)×2π/100 K）')
slot('heating.on', BOOL, '1', 'setpoint', 'temp_push_key', '加热功能是否打开（错误或安全回路切断时自动关闭）')
slot('heating.control', enum(['plate', 'pid', '2p']), '1', 'derived', 'knob_temperature', '当前控温对象：加热盘（无外接传感器）、介质PID或两点控制')
slot('safety.limit_k', number('K'), 'K', 'setpoint', 'safety_screw', '安全温度（加热盘中心）')
slot('stirring.set_rad_s', SPEED_RAD, 'rad/s', 'setpoint', 'knob_speed', '设定转速（转速旋钮转角=设定转速(rpm)×2π/500）')
slot('stirring.on', BOOL, '1', 'setpoint', 'speed_push_key', '搅拌功能是否打开')
slot('stirring.pattern', FREE_OBJ, '1', 'setpoint', 'knob_speed', '转向、自动换向、间歇运行时间')
slot('timer.state', FREE_OBJ, '1', 'derived', 'timer_key', '定时/计数：模式、运行、剩余或已过时间')
slot('menu.state', FREE_OBJ, '1', 'setpoint', 'menu_key', '菜单：工作模式、提示音、单位、Error 5时限、定时结束动作、SAFE/VISC')
slot('lock.on', BOOL, '1', 'setpoint', 'lock_key', '是否锁定')
slot('sensor.state', FREE_OBJ, '1', 'setpoint', 'sensor_jack', 'PT1000：是否插入接口、是否在介质中')
slot('vessel.state', nullable(FREE_OBJ), '1', 'setpoint', 'heating_plate', '加热盘上的容器：型号、液体、搅拌子（无则为空）')
slot('interface.state', FREE_OBJ, '1', 'setpoint', 'usb_port', '数据线与看门狗：是否连接、看门狗模式与时间、WD安全值')
slot('error.code', nullable(integer(1, 99)), '1', 'derived', 'lcd', '当前错误码（Err. n；关机后排除原因再开机清除）')
slot('display.temperature', nullable(number('degC')), 'degC', 'sensor', 'lcd', '实际温度显示（07：接PT1000时为介质温度，否则为加热盘温度；0.1 °C；关机时空，HOT!时显示加热盘温度）')
slot('display.set_temperature', nullable(number('degC')), 'degC', 'derived', 'lcd', '设定温度显示（02；1 °C）')
slot('display.speed', nullable(number('rpm')), 'rpm', 'sensor', 'lcd', '实际转速显示（08；rpm）')
slot('display.set_speed', nullable(number('rpm')), 'rpm', 'derived', 'lcd', '设定转速显示（01；rpm）')
slot('display.message', TEXT, '1', 'derived', 'lcd', '显示文字（七段字形）：HOt（HOT!）、Enn（Err. n）、SAFE（看门狗2的WD：设定改为WD安全值）或空')
slot('display.visc_trend', nullable(number('%')), '%', 'sensor', 'lcd', '黏度趋势（12；VISC显示时，相对稳定时的扭矩，%；三位显示，最大999）')
slot('display.safe_temperature', nullable(number('degC')), 'degC', 'derived', 'lcd', '安全回路温度显示（11；菜单选SAFE时显示，°C；VISC时为空）')
slot('display.timer', TEXT, '1', 'derived', 'lcd', '定时/计数显示（04）：100 h以内为hhmmss六位（冒号印在LCD上），以上为“天天时时”加两空格（按印刷冒号读作dd:hh，手册day:hour）；未设定为空')
for _k, _zh in (('heating', '加热符号（06）'), ('motor', '电机转动符号（03）'), ('standby', '待机LED（I）'), ('hot', 'SmartTemp热表面警示（>50 °C）'),
                ('lock', '锁定符号（15）'), ('sensor', 'PT1000已连接符号（13）')):
    slot(f'indicator.{_k}', BOOL, '1', 'derived', {'heating': 'heating_lamp', 'motor': 'motor_lamp', 'standby': 'standby_led', 'hot': 'hot_lamp',
                                                   'lock': 'lock_lamp', 'sensor': 'sensor_lamp'}[_k], _zh + '是否点亮')
slot('truth.process', FREE_OBJ, '1', 'process_truth', 'heating_plate', '加热盘、介质、传感器真实温度，搅拌子转速与耦合，扭矩，功率', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'housing', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'heating.set_k', 'stirring.set_rad_s', 'indicator.heating', 'indicator.motor', 'indicator.standby', 'indicator.hot', 'indicator.lock',
                  'indicator.sensor', 'display.temperature', 'display.set_temperature', 'display.speed', 'display.set_speed', 'display.message', 'display.visc_trend',
                  'display.safe_temperature', 'display.timer'}


# ---------------------------------------------------------------- runtime
class IkaPlateRctdigitalRuntime:
    def __init__(self, instance_id='ika_plate_rctdigital_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.power = False
        self.menu = dict(operating_mode='A', beep=True, unit='C', err5_timeout_s=0.0, expiry_stop_heating=False, expiry_stop_stirring=False, display='safe')
        self.heat = dict(set_k=C0 + 25.0, on=False, mode='pid', safe_k=C0 + DOC['safety_circuit_c'][1])
        self.stir = dict(set_rpm=0.0, on=False, direction='cw', auto_reverse=False, run_s=0.0, pause_s=3.0)
        self.sensor = dict(connected=False, immersed=False)
        self.vessel = None
        self.iface = dict(connected=False, wd_mode=0, wd_time_s=None, wd_last=None, wd_temp_k=None, wd_speed_rpm=None, wd2_active=False)
        self.timer = dict(mode=None, duration_s=0.0, value_s=0.0, running=False, expired=False)
        self.locked = False
        self.error = None
        self.Tp = self.Tl = self.Ts = None                                  # plate, charge (vessel + liquid), sensor-tip temperatures
        self.omega = 0.0                                                     # motor speed (signed, rad/s)
        self.bar = dict(omega=0.0, coupled=True, torque=0.0)
        self.ctrl = dict(i_plate=0.0, i_med=0.0, e_prev=None, on2p=True, target_p=None, duty=0.0)
        self.err5 = None                                                     # (start time, sensor reading) of the Error 5 window
        self.visc = dict(ref=None, stable_since=None, value=None)
        self.cycle = dict(phase='run', since=0.0, sign=1.0)
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.revision = 0
        self.led = None
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
        return IkaPlateRctdigitalSession(self, name)

    def _event(self, kind, code, message):
        self.events.append(dict(time_s=round(self.t, 6), kind=kind, code=code, message_zh=message))
        if len(self.events) > MAX_EVENTS_LOG:
            self.events_dropped += len(self.events) - MAX_EVENTS_LOG
            del self.events[:-MAX_EVENTS_LOG]

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _mains(self):
        return self.plant is not None and 'mains_outage' not in self.faults

    def _on(self):
        return self.power and self._mains()

    def _need_on(self):
        self._need_plant()
        if not self._on():
            raise ActionRejected('POWER_OFF')

    def _need_unlocked(self):
        if self.locked and not getattr(self, '_remote', False):
            raise ActionRejected('LOCKED')

    def _need_no_error(self):
        if self.error is not None:
            raise ActionRejected('ERROR_ACTIVE')

    # ---- geometry of the charge
    def _bottom_area(self):
        v = VESSELS[self.vessel['type']]
        r = min(v['od_m'], DOC['plate_diameter_m']) / 2.0
        return 0.8 * math.pi * r * r                                        # flat contact part of the bottom (rounded rim excluded)

    def _liquid_height(self):
        v = VESSELS[self.vessel['type']]
        rin = v['od_m'] / 2.0 - 0.002
        return self.vessel['volume_m3'] / (math.pi * rin * rin)

    def _surface_area(self):
        v = VESSELS[self.vessel['type']]
        return math.pi * (v['od_m'] / 2.0 - 0.002) ** 2

    def _charge_cap(self):
        v = VESSELS[self.vessel['type']]
        cp_v = ENG['glass_cp_j_kgk'] if v['material'] == 'glass' else ENG['steel_cp_j_kgk']
        return self.vessel['mass_kg'] * LIQ[self.vessel['sid']]['heat_capacity']['value'] + v['mass_kg'] * cp_v

    def _density(self):
        return LIQ[self.vessel['sid']]['density']['value']

    def _viscosity(self):
        return LIQ[self.vessel['sid']]['viscosity']['value'] * self.vessel['visc_factor']

    # ---- capabilities
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read'):
            self.revision += 1
        return out

    def _cmd_plant_configure(self, ambient_k, pressure_pa, mains_v, noise_seed, relative_humidity=0.5):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        if self.vessel is not None and self.vessel['mass_kg'] > 0.0 and (LIQ[self.vessel['sid']].get('melting_point_k') or 0.0) >= ambient_k:
            raise ActionRejected('NOT_LIQUID')                                # freezing is not modelled: the liquid on the plate would solidify
        first = self.plant is None
        if not first and ambient_k != self.plant['ambient_k']:              # enthalpies are counted relative to ambient: book the change of reference
            d = ambient_k - self.plant['ambient_k']
            self.led['e_reconfigure_j'] += -(ENG['plate_heat_capacity_j_k'] + (self._charge_cap() if self.vessel is not None else 0.0)) * d
        self.plant = dict(ambient_k=ambient_k, pressure_pa=pressure_pa, mains_v=mains_v, relative_humidity=float(relative_humidity))
        self.seed = noise_seed
        if first:
            self.Tp = ambient_k
            self.led = dict(e_heater_j=0.0, e_magnet_j=0.0, e_loss_plate_j=0.0, e_loss_side_j=0.0, e_latent_j=0.0, e_sensible_evap_j=0.0,
                            e_vessel_in_j=0.0, e_vessel_out_j=0.0, e_ambient_reset_j=0.0, h_plate_j=0.0, h_charge_j=0.0,
                            m_placed_kg=0.0, m_evaporated_kg=0.0, m_boiled_kg=0.0, m_removed_kg=0.0, m_liquid_kg=0.0, e_reconfigure_j=0.0)
        self._event('system', 'PLANT_CONFIGURED', '环境已声明')
        return dict(configured=True)

    def _mains_restored(self):
        """Power back (switch on or mains restored): mode A / D start with heating and stirring off, mode B continues (manual)."""
        if self.menu['operating_mode'] != 'B':
            self.heat['on'] = False
            self.stir['on'] = False
        if 'safety_test_fault' in self.faults:
            self._set_error(21, '加热盘安全自检失败（安全继电器不断开）')
        if self.sensor['connected'] and 'sensor_short' in self.faults:
            self._set_error(14, '外接温度传感器短路')
        self._event('system', 'POWER_ON', f'开机，工作模式{self.menu["operating_mode"]}')

    def _power_off(self):
        if self.menu['operating_mode'] != 'B':                                # modes A / D restart with heating and stirring off
            self.heat['on'] = False
            self.stir['on'] = False
        self.omega = 0.0
        self.bar.update(omega=0.0, torque=0.0)
        self.err5 = None
        self.ctrl.update(i_plate=0.0, i_med=0.0, e_prev=None, duty=0.0)
        if self.error is not None and self._error_cleared():
            self._event('system', 'ERROR_CLEARED', f'关机清除Err. {self.error}')
            self.error = None

    def _error_cleared(self):
        e = self.error
        if e == 3:
            return self._internal_k() <= C0 + 70.0
        if e == 4:
            return 'motor_blocked' not in self.faults
        if e == 14:
            return 'sensor_short' not in self.faults
        if e in (21,):
            return 'safety_test_fault' not in self.faults
        if e in (24, 25):
            return self.Tp + ENG['safety_sensor_offset_k'] < self.heat['safe_k'] and 'triac_short' not in self.faults
        if e == 31:
            return 'heater_switch_fault' not in self.faults
        return True

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not self.power:
            self.power = True
            if self._mains():
                self._mains_restored()
        elif not on and self.power:
            self.power = False
            self._power_off()
            self._event('system', 'POWER_OFF', '关机' + ('（加热盘>50 °C显示HOT!）' if self.Tp > C0 + DOC['hot_display_c'] else ''))
        return dict(on=self.power)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(not self.power)

    def _cmd_heating_set_temperature(self, temperature_k):
        self._need_on()
        self._need_unlocked()
        t = C0 + _step_round(temperature_k - C0, 1.0)                        # 1 K resolution
        if t > self.heat['safe_k'] - DOC['safe_margin_k'] + 1e-9:
            raise ActionRejected('ABOVE_SAFETY_LIMIT')
        self.heat['set_k'] = t
        self.err5 = None
        return dict(temperature_k=t)

    def _cmd_heating_set(self, on):
        self._need_on()
        self._need_unlocked()
        if on:
            self._need_no_error()
            if self.sensor['connected'] and 'sensor_short' in self.faults:
                raise ActionRejected('SENSOR_FAULT')
        if on != self.heat['on']:
            self.heat['on'] = on
            self.err5 = None
            self.ctrl.update(i_plate=0.0, i_med=0.0, e_prev=None, on2p=True)
            self._event('operation', 'HEATING_ON' if on else 'HEATING_OFF', '加热' + ('开' if on else '关'))
        return dict(on=self.heat['on'])

    def _cmd_heating_toggle(self):
        return self._cmd_heating_set(not self.heat['on'])

    def _cmd_heating_set_mode(self, mode):
        self._need_on()
        self._need_unlocked()
        self._need_menu_idle()
        self.heat['mode'] = mode
        return dict(mode=mode)

    def _need_menu_idle(self):
        if self.heat['on'] or self.stir['on']:
            raise ActionRejected('FUNCTIONS_RUNNING')                         # manual: the menu can only be entered with stirring and heating off

    def _cmd_safety_set_limit(self, limit_k):
        """The screw B (manual p.27): any position in 50-370 C; set-up procedure and functional test turn it below the hot plate -> Err. 25."""
        self._need_plant()
        self.heat['safe_k'] = float(limit_k)
        self.heat['set_k'] = min(self.heat['set_k'], max(C0, limit_k - DOC['safe_margin_k']))   # the set point follows the knob maximum (limit - 15 K)
        if self._on() and self.Tp + ENG['safety_sensor_offset_k'] >= limit_k:
            if self.heat['on'] or 'triac_short' in self.faults:
                self._set_error(25, '安全回路切断加热（安全温度调到加热盘温度以下）')
            else:
                self._set_error(24, '加热盘温度高于新设的安全温度')
        self._event('operation', 'SAFE_TEMP', f'安全温度{limit_k - C0:.0f} °C')
        return dict(limit_k=self.heat['safe_k'])

    def _cmd_stirring_set_speed(self, speed_rad_s):
        self._need_on()
        self._need_unlocked()
        rpm = speed_rad_s / RPM
        rpm = 0.0 if rpm < 25.0 else min(max(_step_round(rpm, DOC['speed_step_rpm']), DOC['speed_range_rpm'][0]), DOC['speed_range_rpm'][1])
        self.stir['set_rpm'] = rpm
        self.visc.update(ref=None, stable_since=None)
        return dict(speed_rad_s=rpm * RPM)

    def _cmd_stirring_set(self, on):
        self._need_on()
        self._need_unlocked()
        if on:
            self._need_no_error()
        if on != self.stir['on']:
            self.stir['on'] = on
            self.cycle.update(phase='run', since=self.t)
            self.visc.update(ref=None, stable_since=None)
            self._event('operation', 'STIRRING_ON' if on else 'STIRRING_OFF', '搅拌' + ('开' if on else '关'))
        return dict(on=self.stir['on'])

    def _cmd_stirring_toggle(self):
        return self._cmd_stirring_set(not self.stir['on'])

    def _cmd_stirring_configure(self, direction=None, auto_reverse=None, run_s=None, pause_s=None):
        self._need_on()
        self._need_unlocked()
        self._need_menu_idle()
        if run_s is not None and run_s != 0.0 and not (10.0 <= run_s <= 600.0):
            raise ActionRejected('INVALID_ARGUMENT')
        new = dict(self.stir)
        if direction is not None:
            new['direction'] = direction
        if auto_reverse is not None:
            new['auto_reverse'] = auto_reverse
        if run_s is not None:
            new['run_s'] = _step_round(run_s, 10.0)
        if pause_s is not None:
            new['pause_s'] = _step_round(pause_s, 1.0)
        if new['auto_reverse'] and new['run_s'] == 0.0:
            raise ActionRejected('NEEDS_INTERVAL')                            # the reversal happens after each pause
        self.stir = new
        return dict(pattern=self._pattern())

    def _pattern(self):
        return dict(direction=self.stir['direction'], auto_reverse=self.stir['auto_reverse'], run_s=self.stir['run_s'], pause_s=self.stir['pause_s'],
                    intermittent=self.stir['run_s'] > 0.0)

    def _cmd_timer_set(self, duration_s):
        self._need_on()
        self._need_unlocked()
        d = _step_round(duration_s, 1.0)
        self.timer = dict(mode='timer' if d > 0 else 'counter', duration_s=d, value_s=d, running=False, expired=False)
        return dict(timer=self._timer_view())

    def _cmd_timer_toggle(self):
        self._need_on()
        self._need_unlocked()
        if self.timer['mode'] is None:
            raise ActionRejected('TIMER_NOT_SET')
        if self.timer['expired']:
            raise ActionRejected('TIMER_EXPIRED')
        self.timer['running'] = not self.timer['running']
        return dict(timer=self._timer_view())

    def _cmd_timer_clear(self):
        self._need_on()
        self._need_unlocked()
        self.timer = dict(mode=None, duration_s=0.0, value_s=0.0, running=False, expired=False)
        return dict(timer=self._timer_view())

    def _timer_view(self):
        return dict(mode=self.timer['mode'], running=self.timer['running'], expired=self.timer['expired'], duration_s=self.timer['duration_s'],
                    remaining_s=self.timer['value_s'] if self.timer['mode'] == 'timer' else None,
                    elapsed_s=self.timer['value_s'] if self.timer['mode'] == 'counter' else None)

    def _cmd_menu_configure(self, **kw):
        self._need_on()
        self._need_unlocked()
        if any(k != 'display' for k in kw):
            self._need_menu_idle()
        if 'err5_timeout_s' in kw and kw['err5_timeout_s'] != 0.0 and not (60.0 <= kw['err5_timeout_s'] <= 1800.0):
            raise ActionRejected('INVALID_ARGUMENT')
        m = dict(self.menu)
        m.update(kw)
        if 'err5_timeout_s' in kw:
            m['err5_timeout_s'] = _step_round(kw['err5_timeout_s'], 60.0)
        if m['display'] != self.menu['display']:
            self.visc.update(ref=None, stable_since=None)
        self.menu = m
        return dict(menu=dict(self.menu))

    def _cmd_lock_toggle(self):
        self._need_on()
        self.locked = not self.locked
        self._event('operation', 'LOCK' if self.locked else 'UNLOCK', '锁定' if self.locked else '解锁')
        return dict(locked=self.locked)

    def _cmd_viscosity_reset(self):
        self._need_on()
        self._need_unlocked()
        if self.menu['display'] != 'visc':
            raise ActionRejected('NOT_VISC_MODE')
        self.visc.update(ref=self.bar['torque'] if self.bar['torque'] > 0 else None)
        return dict(visc_trend=100.0 if self.visc['ref'] else None)

    def _cmd_sensor_set(self, connected, immersed):
        self._need_plant()
        if immersed and not connected:
            raise ActionRejected('INVALID_ARGUMENT')
        if immersed and (self.vessel is None or self.vessel['mass_kg'] <= 0.0 or self._liquid_height() < DOC['sensor_depth_m']):
            raise ActionRejected('NOT_IMMERSIBLE')                            # manual: at least 20 mm into the medium
        was = self.sensor['connected']
        self.sensor = dict(connected=connected, immersed=immersed)
        if was and not connected and self._on() and self.heat['on']:
            self._set_error(6, '加热中拔出传感器插头')
        if connected and not was and 'sensor_short' in self.faults and self._on():
            self._set_error(14, '插入的外接温度传感器短路')
        if connected and not was:
            self.Ts = self.Tl if immersed and self.Tl is not None else self.plant['ambient_k']
            self.ctrl.update(i_med=0.0, e_prev=None, on2p=True, d_filt=0.0)
            self.err5 = None
        return dict(sensor=dict(self.sensor))

    def _cmd_vessel_place(self, vessel, substance_id, volume_m3, initial_k=None, stir_bar_length_m=0.04):
        self._need_plant()
        if self.vessel is not None:
            raise ActionRejected('OCCUPIED')
        v = VESSELS[vessel]
        if volume_m3 > v['capacity_m3'] + 1e-12:
            raise ActionRejected('OVERFILL')
        t0 = self.plant['ambient_k'] if initial_k is None else initial_k
        s = LIQ[substance_id]
        if (s.get('melting_point_k') or 0.0) >= t0 or t0 >= boiling_k(substance_id, self.plant['pressure_pa']):
            raise ActionRejected('NOT_LIQUID')
        if stir_bar_length_m and not (DOC['stir_bar_length_m'][0] - 1e-9 <= stir_bar_length_m <= min(DOC['stir_bar_length_m'][1], v['od_m'] - 0.008) + 1e-9):
            raise ActionRejected('BAR_LENGTH')
        m = volume_m3 * s['density']['value']
        self.vessel = dict(type=vessel, sid=substance_id, volume_m3=volume_m3, mass_kg=m, bar_m=float(stir_bar_length_m or 0.0), visc_factor=1.0, placed_at=self.t)
        self.Tl = t0
        cap_ = self._charge_cap()
        self.led['m_placed_kg'] += m
        self.led['m_liquid_kg'] = m
        self.led['e_vessel_in_j'] += cap_ * (t0 - self.plant['ambient_k'])
        self.led['h_charge_j'] = cap_ * (t0 - self.plant['ambient_k'])
        self.bar.update(omega=0.0, coupled=True, torque=0.0)
        self.visc.update(ref=None, stable_since=None)
        self._event('operation', 'VESSEL_PLACED', f'放上{vessel}（{substance_id} {volume_m3 * 1e6:.0f} mL）')
        return dict(vessel=self._vessel_view())

    def _vessel_view(self):
        if self.vessel is None:
            return None
        return dict(vessel=self.vessel['type'], substance_id=self.vessel['sid'], stir_bar_length_m=self.vessel['bar_m'])

    def _cmd_vessel_remove(self):
        self._need_plant()
        if self.vessel is None:
            raise ActionRejected('NO_VESSEL')
        if self.sensor['immersed']:
            raise ActionRejected('SENSOR_IMMERSED')
        out = dict(vessel=self.vessel['type'], substance_id=self.vessel['sid'], temperature_k=self.Tl, liquid_mass_kg=self.vessel['mass_kg'],
                   evaporated_kg=self.vessel.get('evap_kg', 0.0), boiled_kg=self.vessel.get('boil_kg', 0.0))
        cap_ = self._charge_cap()
        self.led['e_vessel_out_j'] += cap_ * (self.Tl - self.plant['ambient_k'])
        self.led['h_charge_j'] = 0.0
        self.led['m_removed_kg'] += self.vessel['mass_kg']
        self.led['m_liquid_kg'] = 0.0
        self.vessel = None
        self.Tl = None
        self.bar.update(omega=0.0, coupled=True, torque=0.0)
        self.visc.update(ref=None, stable_since=None)
        self._event('operation', 'VESSEL_REMOVED', '取下容器')
        return dict(vessel=out)

    def _cmd_interface_set(self, connected):
        self._need_plant()
        self.iface['connected'] = connected                                 # a pulled cable stops the commands: the watchdog keeps running
        return dict(connected=connected)

    def _cmd_namur_send(self, command):
        """NAMUR commands of the manual (only on request; every command restarts the watchdog)."""
        self._need_on()
        if not self.iface['connected']:
            raise ActionRejected('NO_CONNECTION')
        cmd = command.strip().upper()
        self.iface['wd_last'] = self.t
        self._remote = True
        try:
            return self._namur(cmd)
        finally:
            self._remote = False

    def _namur(self, cmd):
        parts = cmd.split()
        head = parts[0] if parts else ''
        arg = float(parts[1]) if len(parts) > 1 and re.fullmatch(r'-?\d+(\.\d+)?', parts[1]) else None
        disp = self._display_values()
        if head == 'IN_NAME':
            return dict(response='RCT digital')
        if head == 'IN_PV_1':
            if not self.sensor['connected']:
                return dict(response='-- 1')
            return dict(response=f'{self._sensor_reading() - C0:.1f} 1')
        if head == 'IN_PV_2':
            return dict(response=f'{self._plate_reading() - C0:.1f} 2')
        if head == 'IN_PV_4':
            return dict(response=f'{disp["speed"] or 0:.0f} 4')
        if head == 'IN_PV_5':
            return dict(response=f'{disp["visc"] if disp["visc"] is not None else 0:.0f} 5')
        if head == 'IN_SP_1':
            return dict(response=f'{self.heat["set_k"] - C0:.0f} 1')
        if head == 'IN_SP_3':
            return dict(response=f'{self.heat["safe_k"] - C0:.0f} 3')
        if head == 'IN_SP_4':
            return dict(response=f'{self.stir["set_rpm"]:.0f} 4')
        if head == 'OUT_SP_1' and arg is not None:
            self._cmd_heating_set_temperature(arg + C0)
            return dict(response=f'{self.heat["set_k"] - C0:.0f}')
        if head == 'OUT_SP_4' and arg is not None:
            self._cmd_stirring_set_speed(arg * RPM)
            return dict(response=f'{self.stir["set_rpm"]:.0f}')
        if head in ('START_1', 'STOP_1'):
            self._cmd_heating_set(head == 'START_1')
            return dict(response='')
        if head in ('START_4', 'STOP_4'):
            self._cmd_stirring_set(head == 'START_4')
            return dict(response='')
        if head == 'RESET':
            self.heat['on'] = self.stir['on'] = False
            self.iface.update(wd_mode=0, wd2_active=False)
            return dict(response='')
        m = re.fullmatch(r'SET_MODE_([ABD])', head)
        if m:
            if self.heat['on'] or self.stir['on']:
                raise ActionRejected('FUNCTIONS_RUNNING')
            self.menu['operating_mode'] = m.group(1)
            return dict(response='')
        m = re.fullmatch(r'OUT_SP_(12|42)@(\d+(\.\d+)?)', head)
        if m:
            val = float(m.group(2))
            if m.group(1) == '12':
                self.iface['wd_temp_k'] = C0 + val
            else:
                self.iface['wd_speed_rpm'] = val
            return dict(response=f'{val:g}')
        m = re.fullmatch(r'OUT_WD([12])@(\d+)', head)
        if m:
            mode, secs = int(m.group(1)), float(m.group(2))
            if mode == 2 and secs == 0:
                self.iface.update(wd_mode=0, wd2_active=False)
                return dict(response='0')
            if not DOC['watchdog_s'][0] <= secs <= DOC['watchdog_s'][1]:
                raise ActionRejected('INVALID_ARGUMENT')
            self.iface.update(wd_mode=mode, wd_time_s=secs, wd_last=self.t)
            return dict(response=f'{secs:g}')
        raise ActionRejected('UNKNOWN_COMMAND')

    def _cmd_medium_modify(self, viscosity_factor):
        self._need_plant()
        if self.vessel is None:
            raise ActionRejected('NO_VESSEL')
        self.vessel['visc_factor'] = float(viscosity_factor)
        return dict(viscosity_pa_s=self._viscosity())

    def _cmd_events_read(self, since_s=0.0):
        return dict(events=[e for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        self._need_plant()
        r = self.read('evaluator')
        return dict(process=r['truth.process'], faults=r['truth.faults'])

    def _cmd_ledger_read(self):
        return dict(ledger=self._ledger())

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        was = self._on()
        (self.faults.add if active else self.faults.discard)(fault)
        if was and not self._on():
            self._power_off()
            self._event('alarm', 'POWER_FAILURE', '停电')
        elif not was and self._on():
            self._mains_restored()
        if active and fault == 'sensor_short' and self.sensor['connected'] and self._on():
            self._set_error(14, '外接温度传感器短路')
        if active and fault == 'heater_switch_fault' and self._on():
            self._set_error(31, '加热开关元件故障')
        if active and fault == 'motor_blocked' and self._on() and self.stir['on']:
            self._set_error(4, '电机卡住')
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}' + ('：注入' if active else '：清除'))
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- errors
    def _set_error(self, code, why):
        if self.error is None:
            self.error = code
            self._event('alarm', f'ERR_{code}', f'Err. {code}：{why}')
        self.heat['on'] = False
        if code in (1, 4):
            self.stir['on'] = False
            self.omega = 0.0
        self.err5 = None

    # ---- physics
    def _quiet(self):
        if self.plant is None:
            return True
        ta = self.plant['ambient_k']
        st = self.stir
        if self._on() and (self.heat['on'] or self.iface['wd_mode'] or 'triac_short' in self.faults
                           or (st['on'] and (self._motor_target() != 0.0 or st['run_s'] > 0.0 or 'motor_blocked' in self.faults))):
            return False                                                      # (a blocked motor with stirring on must tick to raise Err. 4)
        if self.vessel is not None and self.vessel['mass_kg'] > 0.0:
            return False                                                      # an open liquid surface evaporates (and cools) even on an idle plate
        return abs(self.omega) < 1e-12 and abs(self.Tp - ta) < 1e-9 and (self.Tl is None or abs(self.Tl - ta) < 1e-9) and (self.Ts is None or abs(self.Ts - ta) < 1e-9)

    def _quiet_jump(self, t_end):
        """Grid ticks that can be skipped exactly (0: tick normally); a running timer / counter is advanced analytically, its expiry is ticked."""
        n = int(math.floor((t_end - self.t_grid) / DT + 1e-6))                 # tolerance: (t_end - t_grid) / DT carries float error
        tm = self.timer
        if n > 0 and tm['running'] and self._on():
            if tm['mode'] == 'timer':
                n = min(n, int(math.ceil(tm['value_s'] / DT - 1e-6)) - 1)
                if n > 0:
                    tm['value_s'] = round(tm['value_s'] - n * DT, 9)
            else:
                tm['value_s'] = round(math.fmod(tm['value_s'] + n * DT, 100 * 86400.0), 9)
        return max(n, 0)

    def step(self, dt_s):
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = round(self.t + float(dt_s), 9)
        while self.t_grid + DT <= t_end + 1e-9:
            if self._quiet():                                                 # everything at ambient and idle: jump the grid exactly
                n = self._quiet_jump(t_end)
                if n > 0:
                    self.tick += n
                    self.t_grid = round(self.tick * DT, 9)
                    self.t = self.t_grid
                    continue
            self.t = self.t_grid
            self._tick(DT)
            self.tick += 1
            self.t_grid = round(self.tick * DT, 9)
            self.t = self.t_grid
        self.t = t_end
        return self.t

    def _internal_k(self):
        ta = self.plant['ambient_k']
        k = ENG['internal_coupling']
        if self.vessel is not None and VESSELS[self.vessel['type']]['od_m'] > DOC['plate_diameter_m'] + 0.02:
            k *= ENG['overhang_internal_factor']                              # a vessel overhanging the plate traps heat over the housing
        return ta + k * (self.Tp - ta)

    def _plate_reading(self):
        return self.Tp + ENG['plate_sensor_noise_k'] * _gauss(self.seed, self.tick, 1)

    def _sensor_reading(self):
        return self.Ts + ENG['sensor_noise_k'] * _gauss(self.seed, self.tick, 2)

    def _plate_max(self):
        return min(PLATE_MAX_K, self.heat['safe_k'] - DOC['safe_margin_k'])

    def _controlled(self):
        if self.sensor['connected']:
            return self.heat['mode']
        return 'plate'

    def _heater_power(self, h):
        """Controller: the heater duty (0..1) from the plate PI on a plate target from the plate set point, the medium PID or the 2P rule."""
        c = self.ctrl
        if 'triac_short' in self.faults and self._on() and self.error not in (25, 31):
            c.update(duty=1.0, target_p=None)                                 # short-circuited switch: full power until the safety relay opens
            return DOC['heater_power_w'] * (self.plant['mains_v'] / 230.0) ** 2
        if not (self._on() and self.heat['on'] and self.error is None) or 'heater_switch_fault' in self.faults:
            c.update(duty=0.0, target_p=None)
            return 0.0
        tset = self.heat['set_k']
        mode = self._controlled()
        pmax = self._plate_max()
        if mode == 'plate':
            target = min(tset, pmax)
        else:
            ts = self._sensor_reading()
            e = tset - ts
            if mode == '2p':
                if ts < tset - ENG['two_point_hysteresis_k']:
                    c['on2p'] = True
                elif ts >= tset:
                    c['on2p'] = False
                target = pmax if c['on2p'] else None
            else:
                raw = 0.0 if c['e_prev'] is None else (e - c['e_prev']) / h
                c['e_prev'] = e
                c['d_filt'] = c.get('d_filt', 0.0) + (raw - c.get('d_filt', 0.0)) * h / ENG['pid_d_filter_s']   # derivative on a filtered signal
                if abs(e) < ENG['pid_i_band_k']:                              # conditional integration (no wind-up during the rise)
                    c['i_med'] = min(max(c['i_med'] + ENG['pid_ki_s'] * e * h, -50.0), ENG['max_plate_over_medium_k'])
                target = tset + ENG['pid_kp'] * e + c['i_med'] + ENG['pid_kd_s'] * c['d_filt']
                target = min(max(target, self.plant['ambient_k']), min(pmax, tset + ENG['max_plate_over_medium_k']))
        c['target_p'] = target
        if target is None:
            c['i_plate'] = 0.0
            c['duty'] = 0.0
            return 0.0
        ep = target - self._plate_reading()
        raw = ENG['plate_kp_w_k'] * ep + c['i_plate']
        if 0.0 < raw < DOC['heater_power_w'] or (raw >= DOC['heater_power_w'] and ep < 0) or (raw <= 0 and ep > 0):
            c['i_plate'] = min(max(c['i_plate'] + ENG['plate_ki_w_ks'] * ep * h, 0.0), DOC['heater_power_w'])
        p = min(max(raw, 0.0), DOC['heater_power_w']) * (self.plant['mains_v'] / 230.0) ** 2
        p = min(p, DOC['heater_power_w'] * 1.2)
        c['duty'] = p / DOC['heater_power_w']
        return p

    def _motor_target(self):
        if not (self._on() and self.stir['on'] and self.error is None) or 'motor_blocked' in self.faults:
            return 0.0
        rpm = self.stir['set_rpm']
        if self.stir['run_s'] > 0.0 and self.cycle['phase'] == 'pause':
            return 0.0
        return rpm * RPM * self.cycle['sign']

    def _advance_cycle(self, h):
        s, c = self.stir, self.cycle
        if not (self._on() and s['on']) or s['run_s'] <= 0.0:
            c.update(phase='run', since=self.t + h, sign=1.0 if s['direction'] == 'cw' else -1.0)   # (re)start in the chosen direction
            return
        if not s['auto_reverse']:
            c['sign'] = 1.0 if s['direction'] == 'cw' else -1.0               # intermittent without reversal keeps the chosen direction
        length = s['run_s'] if c['phase'] == 'run' else s['pause_s']
        if self.t + h - c['since'] >= length - 1e-9:
            if c['phase'] == 'run':
                c.update(phase='pause', since=self.t + h)
            else:
                c.update(phase='run', since=self.t + h)
                if s['auto_reverse']:
                    c['sign'] = -c['sign']

    def _drag(self, w):
        """Drag torque (N m) of the bar turning at w in the liquid."""
        if self.vessel is None or self.vessel['bar_m'] <= 0.0 or self.vessel['mass_kg'] <= 0.0:
            return 0.0
        L = self.vessel['bar_m']
        w = abs(w)
        return ENG['drag_viscous'] * self._viscosity() * w * L ** 3 + ENG['drag_inertial'] * self._density() * w * w * L ** 5

    def _tick(self, h):
        L = self.led
        ta = self.plant['ambient_k']
        k = self.tick
        # ---- watchdog
        f = self.iface
        if self._on() and f['wd_mode'] and f['wd_last'] is not None and self.t + h - f['wd_last'] > f['wd_time_s'] + 1e-9:
            if f['wd_mode'] == 1:
                self._set_error(1, '看门狗1：电脑在设定时间内未发送数据，加热与搅拌关闭')
                f['wd_mode'] = 0
            elif not f['wd2_active']:
                f['wd2_active'] = True                                        # manual: the target values are changed to the WD safety values
                if f['wd_temp_k'] is not None:
                    self.heat['set_k'] = min(C0 + _step_round(f['wd_temp_k'] - C0, 1.0), self.heat['safe_k'] - DOC['safe_margin_k'], PLATE_MAX_K)
                if f['wd_speed_rpm'] is not None:
                    r = f['wd_speed_rpm']
                    self.stir['set_rpm'] = 0.0 if r < 25.0 else min(max(_step_round(r, DOC['speed_step_rpm']), DOC['speed_range_rpm'][0]), DOC['speed_range_rpm'][1])
                self._event('warning', 'WD', '看门狗2：设定值改为WD安全值')
        # ---- stirring
        self._advance_cycle(h)
        target = self._motor_target()
        ramp = DOC['speed_range_rpm'][1] * RPM / ENG['motor_ramp_s'] * h
        self.omega = target if abs(target - self.omega) <= ramp else self.omega + math.copysign(ramp, target - self.omega)
        w = abs(self.omega)
        tau_c = ENG['coupling_torque_nm_per_m'] * (self.vessel['bar_m'] if self.vessel else 0.0)
        if self.vessel is None or self.vessel['bar_m'] <= 0.0 or self.vessel['mass_kg'] <= 0.0:
            self.bar.update(omega=0.0, coupled=True, torque=0.0)
        else:
            drag = self._drag(w)
            if drag <= tau_c:
                self.bar.update(omega=self.omega, coupled=True, torque=drag)
            else:                                                             # the bar slips out of the magnetic field and only wobbles
                wb = 0.15 * w
                self.bar.update(omega=math.copysign(wb, self.omega), coupled=False, torque=min(tau_c, self._drag(wb)) * 0.5)
                if self.bar.get('was_coupled', True):
                    self._event('truth', 'BAR_DECOUPLED', '搅拌子脱耦（转速过高或介质过黏；本机无检测，只记入真值）')
            self.bar['was_coupled'] = self.bar['coupled']
        if 'motor_blocked' in self.faults and self._on() and self.stir['on']:
            self._set_error(4, '电机卡住')
        # ---- thermal
        p_heat = self._heater_power(h)
        p_mag = ENG['magnet_heating_w_at_max'] * (w / (DOC['speed_range_rpm'][1] * RPM)) ** 2
        area = ENG['plate_area_m2']
        if self.vessel is not None:
            ab = self._bottom_area()
            exposed = max(area - math.pi * (min(VESSELS[self.vessel['type']]['od_m'], DOC['plate_diameter_m']) / 2.0) ** 2, 0.0)
            f_stir = ENG['unstirred_factor'] + (1.0 - ENG['unstirred_factor']) * min(abs(self.bar['omega']) / (ENG['stir_full_rpm'] * RPM), 1.0)
            if self.vessel['volume_m3'] > ENG['volume_mixing_limit_m3']:
                f_stir = ENG['unstirred_factor']
            f_mat = ENG['steel_bottom_factor'] if VESSELS[self.vessel['type']]['material'] == 'steel' else 1.0
            g_pl = ENG['contact_conductance_w_m2k'] * ab * f_stir * f_mat
        else:
            exposed, g_pl = area, 0.0
        q_loss_p = ENG['plate_loss_w_k'] * (self.Tp - ta) + ENG['plate_emissivity'] * SIGMA * exposed * (self.Tp ** 4 - ta ** 4)
        q_pl = g_pl * (self.Tp - self.Tl) if self.vessel is not None else 0.0
        dTp = h * (p_heat + p_mag - q_loss_p - q_pl) / ENG['plate_heat_capacity_j_k']
        L['e_heater_j'] += p_heat * h
        L['e_magnet_j'] += p_mag * h
        L['e_loss_plate_j'] += q_loss_p * h
        if self.vessel is not None:
            vv = VESSELS[self.vessel['type']]
            sid = self.vessel['sid']
            hgt = self._liquid_height() if self.vessel['mass_kg'] > 0 else 0.0
            a_side = math.pi * vv['od_m'] * min(hgt + 0.01, vv['height_m']) + self._surface_area()
            q_side = ENG['side_loss_w_m2k'] * a_side * (self.Tl - ta)
            cp_l = LIQ[sid]['heat_capacity']['value']
            tb = boiling_k(sid, self.plant['pressure_pa'])
            hv = LIQ[sid]['enthalpy_vaporization']['value']
            cap_old = self._charge_cap()
            H = cap_old * (self.Tl - ta)                                     # charge enthalpy relative to ambient
            m_evap = m_boil = 0.0
            if self.vessel['mass_kg'] > 0.0:
                pv = min(p_vap(sid, min(self.Tl, tb)), self.plant['pressure_pa'])
                if sid == 'water':                                            # the room air already holds water vapour
                    pv = max(pv - self.plant.get('relative_humidity', 0.5) * p_vap('water', ta), 0.0)
                m_evap = min(ENG['evap_mass_transfer_m_s'] * self._surface_area() * pv * LIQ[sid]['molar_mass_kg_mol'] / (R_GAS * self.Tl) * h, self.vessel['mass_kg'])
            H += (q_pl - q_side) * h - m_evap * hv - m_evap * cp_l * (self.Tl - ta)   # the vapour leaves with its latent and sensible heat
            sens = m_evap * cp_l * (self.Tl - ta)
            self.vessel['mass_kg'] -= m_evap
            cap_ = self._charge_cap()
            t_new = ta + H / cap_
            if self.vessel['mass_kg'] > 0.0 and t_new > tb:                  # boiling: the excess over the boiling point boils liquid off
                excess = (t_new - tb) * cap_
                m_boil = min(excess / hv, self.vessel['mass_kg'])
                H -= m_boil * hv + m_boil * cp_l * (tb - ta)
                sens += m_boil * cp_l * (tb - ta)
                self.vessel['mass_kg'] -= m_boil
                cap_ = self._charge_cap()
                t_new = ta + H / cap_
            for m_out, key in ((m_evap, 'evap_kg'), (m_boil, 'boil_kg')):
                if m_out > 0:
                    self.vessel[key] = self.vessel.get(key, 0.0) + m_out
                    L['m_evaporated_kg' if key == 'evap_kg' else 'm_boiled_kg'] += m_out
            if 0.0 < self.vessel['mass_kg'] < 1e-9:                          # last traces evaporate
                L['m_evaporated_kg'] += self.vessel['mass_kg']
                self.vessel['evap_kg'] = self.vessel.get('evap_kg', 0.0) + self.vessel['mass_kg']
                self.vessel['mass_kg'] = 0.0
            self.vessel['volume_m3'] = self.vessel['mass_kg'] / self._density()
            L['e_sensible_evap_j'] += sens
            L['e_latent_j'] += (m_evap + m_boil) * hv
            L['e_loss_side_j'] += q_side * h
            if self.vessel['mass_kg'] <= 0.0:
                if self.sensor['immersed']:
                    self.sensor['immersed'] = False
                    self._event('truth', 'DRY', '液体已蒸干，传感器露出（本机不能检测，只记入真值）')
            self.Tl = t_new
            L['m_liquid_kg'] = self.vessel['mass_kg']
        self.Tp += dTp
        # ---- sensor (first-order lag to the medium or the air above the plate)
        if self.sensor['connected']:
            target = self.Tl if (self.sensor['immersed'] and self.vessel is not None) else ta + ENG['sensor_air_coupling'] * (self.Tp - ta)
            tau = ENG['sensor_tau_liquid_s'] if self.sensor['immersed'] else ENG['sensor_tau_air_s']
            self.Ts = target + (self.Ts - target) * math.exp(-h / tau)
        elif self.Ts is not None:
            self.Ts = ta + (self.Ts - ta) * math.exp(-h / ENG['sensor_tau_air_s'])
            if abs(self.Ts - ta) < ENG['ambient_snap_k']:
                self.Ts = None
        # ---- safety and errors
        if self._on():
            if (self.heat['on'] or 'triac_short' in self.faults) and self.error != 25 and self.Tp + ENG['safety_sensor_offset_k'] >= self.heat['safe_k']:
                self._set_error(25, '安全回路切断加热（加热盘达到安全温度）')
            if self._internal_k() > C0 + 80.0:
                self._set_error(3, '机内温度高于80 °C')
            if self.heat['on'] and self.sensor['connected'] and self.menu['err5_timeout_s'] > 0 and self._controlled() != 'plate':
                ts = self._sensor_reading()
                if ts < C0 + DOC['error5_conditions']['sensor_below_c'] and self.heat['set_k'] - ts > DOC['error5_conditions']['difference_k']:
                    if self.err5 is None:
                        self.err5 = (self.t + h, self.Ts)
                    elif self.t + h - self.err5[0] >= self.menu['err5_timeout_s'] - 1e-9:
                        if self.Ts - self.err5[1] < ENG['err5_rise_k']:
                            self._set_error(5, '时限内传感器读数没有上升（传感器不在介质中）')
                        else:
                            self.err5 = (self.t + h, self.Ts)
                else:
                    self.err5 = None
            # ---- timer
            tm = self.timer
            if tm['running']:
                if tm['mode'] == 'timer':
                    tm['value_s'] = max(round(tm['value_s'] - h, 9), 0.0)
                    if tm['value_s'] <= 0.0:
                        tm.update(running=False, expired=True)
                        self._event('operation', 'TIMER_EXPIRED', '定时结束' + ('（蜂鸣）' if self.menu['beep'] else ''))
                        if self.menu['expiry_stop_heating']:
                            self.heat['on'] = False
                        if self.menu['expiry_stop_stirring']:
                            self.stir['on'] = False
                else:
                    tm['value_s'] = round(tm['value_s'] + h, 9)
                    if tm['value_s'] >= 100 * 86400.0:
                        tm['value_s'] = 0.0
            # ---- viscosity trend
            vs = self.visc
            if self.menu['display'] == 'visc' and self.stir['on'] and self.bar['torque'] > 0 and abs(abs(self.omega) - self.stir['set_rpm'] * RPM) < 1e-6 \
                    and self.stir['run_s'] == 0.0:
                if vs['stable_since'] is None:
                    vs['stable_since'] = self.t + h
                elif vs['ref'] is None and self.t + h - vs['stable_since'] >= ENG['visc_settle_s'] - 1e-9:
                    vs['ref'] = self.bar['torque']
            else:
                vs.update(stable_since=None)
                if not self.stir['on']:
                    vs['ref'] = None
        cap_ = self._charge_cap() if self.vessel is not None else 0.0
        L['h_plate_j'] = ENG['plate_heat_capacity_j_k'] * (self.Tp - ta)
        L['h_charge_j'] = cap_ * (self.Tl - ta) if self.vessel is not None else 0.0
        idle = not (self._on() and (self.heat['on'] or 'triac_short' in self.faults)) and abs(self.omega) < 1e-12 and self._motor_target() == 0.0 \
            and (self.vessel is None or self.vessel['mass_kg'] <= 0.0)           # an evaporating liquid is never snapped to ambient
        if abs(self.Tp - ta) < ENG['ambient_snap_k'] and idle:
            L['e_ambient_reset_j'] += ENG['plate_heat_capacity_j_k'] * (self.Tp - ta)
            self.Tp = ta
            L['h_plate_j'] = 0.0
        if self.vessel is not None and abs(self.Tl - ta) < ENG['ambient_snap_k'] and self.Tp == ta and idle:
            L['e_ambient_reset_j'] += cap_ * (self.Tl - ta)
            self.Tl = ta
            L['h_charge_j'] = 0.0

    # ---- read
    def _display_values(self):
        on = self._on()
        hot = self.plant is not None and self.Tp > C0 + DOC['hot_display_c']
        if not on:
            return dict(temp=round(self.Tp - C0, 1) if hot and self._mains() else None, set_temp=None, speed=None, set_speed=None,
                        message='HOt' if hot and self._mains() else '', visc=None, safe=None, timer='')
        t_act = self._sensor_reading() if self.sensor['connected'] and self.Ts is not None else self._plate_reading()
        msg = f'E{self.error:02d}' if self.error is not None else ('SAFE' if self.iface['wd2_active'] else '')
        vs = self.visc
        visc = None
        if self.menu['display'] == 'visc' and vs['ref']:
            visc = min(round(100.0 * self.bar['torque'] / vs['ref'] * (1.0 + ENG['torque_noise'] * _gauss(self.seed, self.tick, 3))), 999)   # 3-digit field (12)
        speed = abs(self.omega) / RPM * (1.0 + ENG['speed_noise'] * _gauss(self.seed, self.tick, 4)) if abs(self.omega) > 0 else 0.0
        return dict(temp=round(t_act - C0, 1), set_temp=round(self.heat['set_k'] - C0), speed=round(speed / 10.0) * 10.0 if speed >= 25.0 else round(speed),
                    set_speed=self.stir['set_rpm'], message=msg, visc=visc, safe=round(self.heat['safe_k'] - C0) if self.menu['display'] == 'safe' else None,
                    timer=self._timer_text())

    def _timer_text(self):
        """Timer / counter field 04: hhmmss below 100 h (the colons are printed on the LCD), 'ddhh' + two blanks above (manual: day:hour, read
        dd:hh on the printed colons); '' when not set."""
        tm = self.timer
        if tm['mode'] is None:
            return ''
        v = int(math.ceil(tm['value_s'] - 1e-9)) if tm['mode'] == 'timer' else int(math.floor(tm['value_s'] + 1e-9))
        if v < 100 * 3600:
            return f'{v // 3600:02d}{v % 3600 // 60:02d}{v % 60:02d}'
        return f'{v // 86400:02d}{v % 86400 // 3600:02d}  '

    def read(self, visibility='agent'):
        pc = self.plant is not None
        d = self._display_values() if pc else dict(temp=None, set_temp=None, speed=None, set_speed=None, message='', visc=None, safe=None, timer='')
        on = pc and self._on()
        out = {'clock.time_s': self.t, 'plant.configured': pc, 'power.on': self.power, 'heating.set_k': self.heat['set_k'], 'heating.on': self.heat['on'],
               'heating.control': self._controlled(), 'safety.limit_k': self.heat['safe_k'], 'stirring.set_rad_s': self.stir['set_rpm'] * RPM,
               'stirring.on': self.stir['on'], 'stirring.pattern': self._pattern(), 'timer.state': self._timer_view(), 'menu.state': dict(self.menu),
               'lock.on': self.locked, 'sensor.state': dict(self.sensor), 'vessel.state': self._vessel_view(),
               'interface.state': dict(connected=self.iface['connected'], watchdog_mode=self.iface['wd_mode'], watchdog_s=self.iface['wd_time_s'],
                                       wd_temperature_k=self.iface['wd_temp_k'], wd_speed_rpm=self.iface['wd_speed_rpm'], wd2_active=self.iface['wd2_active']),
               'error.code': self.error, 'display.temperature': d['temp'], 'display.set_temperature': d['set_temp'], 'display.speed': d['speed'],
               'display.set_speed': d['set_speed'], 'display.message': d['message'], 'display.visc_trend': d['visc'], 'display.safe_temperature': d['safe'],
               'display.timer': d['timer'],
               'indicator.heating': bool(on and self.heat['on']), 'indicator.motor': bool(on and abs(self.omega) > 0),
               'indicator.standby': bool(pc and self._mains() and not self.power), 'indicator.hot': bool(pc and self._mains() and self.Tp > C0 + ENG['hot_surface_warn_c']),
               'indicator.lock': bool(on and self.locked), 'indicator.sensor': bool(on and self.sensor['connected'])}
        if visibility != 'agent':
            out['truth.process'] = (dict(plate_k=self.Tp, charge_k=self.Tl, sensor_k=self.Ts, internal_k=self._internal_k(), motor_rad_s=self.omega,
                                         bar_rad_s=self.bar['omega'], bar_coupled=self.bar['coupled'], torque_nm=self.bar['torque'], heater_duty=self.ctrl['duty'],
                                         plate_target_k=self.ctrl['target_p'],
                                         liquid=None if self.vessel is None else dict(substance_id=self.vessel['sid'], mass_kg=self.vessel['mass_kg'],
                                                                                      volume_m3=self.vessel['volume_m3'], viscosity_pa_s=self._viscosity(),
                                                                                      evaporated_kg=self.vessel.get('evap_kg', 0.0), boiled_kg=self.vessel.get('boil_kg', 0.0),
                                                                                      boiling_k=boiling_k(self.vessel['sid'], self.plant['pressure_pa'])))
                                    if pc else {})
            out['truth.faults'] = sorted(self.faults)
        return out

    def _ledger(self):
        if self.led is None:
            return {}
        L = dict(self.led)
        ta = self.plant['ambient_k']
        L['h_plate_j'] = ENG['plate_heat_capacity_j_k'] * (self.Tp - ta)
        L['h_charge_j'] = self._charge_cap() * (self.Tl - ta) if self.vessel is not None else 0.0
        L['energy_balance_j'] = (L['e_heater_j'] + L['e_magnet_j'] + L['e_vessel_in_j'] + L['e_reconfigure_j'] - L['e_vessel_out_j'] - L['e_loss_plate_j'] - L['e_loss_side_j']
                                 - L['e_latent_j'] - L['e_sensible_evap_j'] - L['e_ambient_reset_j'] - L['h_plate_j'] - L['h_charge_j'])
        L['mass_balance_kg'] = L['m_placed_kg'] - L['m_evaporated_kg'] - L['m_boiled_kg'] - L['m_removed_kg'] - L['m_liquid_kg']
        return L


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
class IkaPlateRctdigitalSession:
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
        meta = copy.deepcopy(export_ika_plate_rctdigital_contract_metadata())
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
            snapshot = copy.deepcopy({k: v for k, v in h.__dict__.items() if k not in ('_principals', '_requests', '_caller')})
            try:
                result = _jsonable(h.invoke(capability_id, **arguments))
            except ActionRejected:
                h.__dict__.update(snapshot)                                   # rejected: nothing changed (partially applied NAMUR commands included)
                raise
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
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本型号没有该功能', 'ACCESS_DENIED': '无权访问（真值或评测者能力）',
    'UNKNOWN_OPERATION': '会话不支持该操作（本运行层的能力都是同步的）', 'PLANT_REQUIRED': '尚未声明环境', 'SEED_FIXED': '噪声种子只能在首次声明时设定',
    'POWER_OFF': '电源开关关或停电', 'LOCKED': '已锁定（先按住Lock键2 s解锁）', 'ERROR_ACTIVE': '有错误码（关机排除原因后再开机）',
    'ABOVE_SAFETY_LIMIT': '设定温度须比安全温度至少低15 K', 'FUNCTIONS_RUNNING': '须先停加热与搅拌才能进菜单',
    'NEEDS_INTERVAL': '自动换向须设间歇运行时间（每次暂停后换向）', 'TIMER_NOT_SET': '尚未设定定时/计数', 'TIMER_EXPIRED': '定时已结束（重新设定）',
    'NOT_VISC_MODE': '显示不在VISC（菜单Safe/Visc设为VISC）', 'NOT_IMMERSIBLE': '没有容器或液深不足20 mm，传感器不能插入介质', 'SENSOR_FAULT': '外接传感器故障（短路）',
    'OCCUPIED': '加热盘上已有容器', 'OVERFILL': '液体体积超过容器容量', 'NOT_LIQUID': '该物质在此温度下不是液体（低于熔点或高于沸点；改环境时：盘上液体会凝固，凝固未建模）',
    'BAR_LENGTH': '搅拌子长度须在30–80 mm且小于容器内径', 'NO_VESSEL': '加热盘上没有容器', 'SENSOR_IMMERSED': '先把传感器从介质中取出',
    'NO_CONNECTION': '未连接USB/RS 232数据线', 'UNKNOWN_COMMAND': '不是手册列出的NAMUR命令',
}
_P = ['PLANT_REQUIRED']
_PO = _P + ['POWER_OFF']
_PL = _PO + ['LOCKED']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED', 'NOT_LIQUID'], 'power.set': _P, 'power.toggle': _P,
    'heating.set_temperature': _PL + ['ABOVE_SAFETY_LIMIT'], 'heating.set': _PL + ['ERROR_ACTIVE', 'SENSOR_FAULT'], 'heating.toggle': _PL + ['ERROR_ACTIVE', 'SENSOR_FAULT'],
    'heating.set_mode': _PL + ['FUNCTIONS_RUNNING'], 'safety.set_limit': _P,
    'stirring.set_speed': _PL, 'stirring.set': _PL + ['ERROR_ACTIVE'], 'stirring.toggle': _PL + ['ERROR_ACTIVE'],
    'stirring.configure': _PL + ['FUNCTIONS_RUNNING', 'NEEDS_INTERVAL'], 'timer.set': _PL, 'timer.toggle': _PL + ['TIMER_NOT_SET', 'TIMER_EXPIRED'], 'timer.clear': _PL,
    'menu.configure': _PL + ['FUNCTIONS_RUNNING'], 'lock.toggle': _PO, 'viscosity.reset': _PL + ['NOT_VISC_MODE'],
    'sensor.set': _P + ['NOT_IMMERSIBLE'], 'vessel.place': _P + ['OCCUPIED', 'OVERFILL', 'NOT_LIQUID', 'BAR_LENGTH'], 'vessel.remove': _P + ['NO_VESSEL', 'SENSOR_IMMERSED'],
    'interface.set': _P,
    'namur.send': _PO + ['NO_CONNECTION', 'UNKNOWN_COMMAND', 'LOCKED', 'ABOVE_SAFETY_LIMIT', 'ERROR_ACTIVE', 'SENSOR_FAULT', 'FUNCTIONS_RUNNING'],
    'medium.modify': _P + ['NO_VESSEL'], 'events.read': [], 'truth.inspect': _P, 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子（之后不可改）；盘上有液体时室温须高于其熔点', ['plant.configured', 'vessel.state']), 'power.set': ('无', ['power.on']), 'power.toggle': ('无', ['power.on']),
    'heating.set_temperature': ('开机；未锁定；设定比安全温度至少低15 K', ['power.on', 'lock.on', 'safety.limit_k']),
    'heating.set': ('开机；未锁定；开加热时无错误码且传感器无故障', ['power.on', 'lock.on', 'error.code']),
    'heating.toggle': ('开机；未锁定；开加热时无错误码且传感器无故障', ['power.on', 'lock.on', 'error.code']),
    'heating.set_mode': ('开机；未锁定；加热与搅拌关闭（菜单）', ['power.on', 'lock.on', 'heating.on', 'stirring.on']),
    'safety.set_limit': ('已声明环境；螺钉任意位置50–370 °C；设定温度高于新安全温度−15 K时降到该值；调到加热盘温度以下：加热中Err. 25、不加热时Err. 24',
                         ['heating.on', 'heating.set_k', 'safety.limit_k']),
    'stirring.set_speed': ('开机；未锁定', ['power.on', 'lock.on']), 'stirring.set': ('开机；未锁定；开搅拌时无错误码', ['power.on', 'lock.on', 'error.code']),
    'stirring.toggle': ('开机；未锁定；开搅拌时无错误码', ['power.on', 'lock.on', 'error.code']),
    'stirring.configure': ('开机；未锁定；加热与搅拌关闭（菜单）；自动换向须设间歇', ['power.on', 'lock.on', 'heating.on', 'stirring.on']),
    'timer.set': ('开机；未锁定', ['power.on', 'lock.on']), 'timer.toggle': ('开机；未锁定；已设定且未结束', ['power.on', 'lock.on', 'timer.state']),
    'timer.clear': ('开机；未锁定', ['power.on', 'lock.on']),
    'menu.configure': ('开机；未锁定；除SAFE/VISC显示外须加热与搅拌关闭', ['power.on', 'lock.on', 'heating.on', 'stirring.on']),
    'lock.toggle': ('开机', ['power.on']), 'viscosity.reset': ('开机；未锁定；显示在VISC', ['power.on', 'lock.on', 'menu.state']),
    'sensor.set': ('放入介质须有容器且液深≥20 mm', ['vessel.state', 'sensor.state']),
    'vessel.place': ('加热盘空；体积不超过容量；物质在初温为液体；搅拌子30–80 mm且小于内径', ['vessel.state']),
    'vessel.remove': ('有容器；传感器不在介质中', ['vessel.state', 'sensor.state']), 'interface.set': ('无', ['interface.state']),
    'namur.send': ('开机；已连接数据线；设定类命令同对应能力的前提', ['power.on', 'interface.state']),
    'medium.modify': ('有容器', ['vessel.state']), 'truth.inspect': ('无', ['plant.configured']), 'fault.inject': ('无', ['plant.configured']),
}
_DISPLAY = ['display.temperature', 'display.set_temperature', 'display.speed', 'display.set_speed', 'display.message', 'display.visc_trend', 'display.safe_temperature',
            'display.timer']
_IND = ['indicator.heating', 'indicator.motor', 'indicator.standby', 'indicator.hot', 'indicator.lock', 'indicator.sensor']
_EVOLVE = _DISPLAY + _IND + ['heating.on', 'stirring.on', 'error.code', 'timer.state', 'sensor.state', 'interface.state', 'truth.process',
                              'heating.set_k', 'stirring.set_rad_s']   # the set points change on a step only through watchdog 2 (WD safety values)
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.process'] + _DISPLAY + _IND,
    'power.set': ['power.on', 'heating.on', 'stirring.on', 'error.code', 'truth.process'] + _DISPLAY + _IND,
    'power.toggle': ['power.on', 'heating.on', 'stirring.on', 'error.code', 'truth.process'] + _DISPLAY + _IND,
    'heating.set_temperature': ['heating.set_k', 'display.set_temperature'],
    'heating.set': ['heating.on', 'indicator.heating', 'truth.process'], 'heating.toggle': ['heating.on', 'indicator.heating', 'truth.process'],
    'heating.set_mode': ['heating.control'],
    'safety.set_limit': ['safety.limit_k', 'heating.set_k', 'display.set_temperature', 'heating.on', 'error.code', 'indicator.heating', 'display.message', 'display.safe_temperature', 'truth.process'],
    'stirring.set_speed': ['stirring.set_rad_s', 'display.set_speed', 'display.visc_trend'],
    'stirring.set': ['stirring.on', 'display.visc_trend', 'truth.process'], 'stirring.toggle': ['stirring.on', 'display.visc_trend', 'truth.process'],
    'stirring.configure': ['stirring.pattern', 'truth.process'], 'timer.set': ['timer.state', 'display.timer'], 'timer.toggle': ['timer.state', 'display.timer'], 'timer.clear': ['timer.state', 'display.timer'],
    'menu.configure': ['menu.state', 'display.visc_trend', 'display.safe_temperature'], 'lock.toggle': ['lock.on', 'indicator.lock'], 'viscosity.reset': ['display.visc_trend'],
    'sensor.set': ['sensor.state', 'heating.control', 'heating.on', 'error.code', 'indicator.sensor', 'indicator.heating', 'display.temperature', 'display.message',
                   'truth.process'],
    'vessel.place': ['vessel.state', 'truth.process', 'display.visc_trend'],
    'vessel.remove': ['vessel.state', 'truth.process', 'display.visc_trend'],
    'interface.set': ['interface.state'],
    'namur.send': ['heating.set_k', 'heating.on', 'stirring.set_rad_s', 'stirring.on', 'menu.state', 'interface.state',
                   'display.set_temperature', 'display.set_speed', 'display.message', 'display.visc_trend', 'indicator.heating', 'truth.process'],
    'medium.modify': ['truth.process'],
    'fault.inject': ['truth.faults', 'heating.on', 'stirring.on', 'error.code', 'truth.process'] + _DISPLAY + _IND,
    'sim.step': ['clock.time_s'],
}
_STEPPED = ('plant.configure', 'power.set', 'power.toggle', 'heating.set_temperature', 'heating.set', 'heating.toggle', 'heating.set_mode', 'safety.set_limit',
            'stirring.set_speed', 'stirring.set', 'stirring.toggle', 'stirring.configure', 'timer.set', 'timer.toggle', 'menu.configure', 'sensor.set', 'vessel.place',
            'vessel.remove', 'namur.send', 'medium.modify', 'fault.inject')
ON_STEP = {k: list(_EVOLVE) for k in _STEPPED}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {'sim.step': '按0.1 s网格积分：加热盘与介质温度、蒸发与沸腾、传感器滞后、控温、电机转速与搅拌子耦合、安全回路与错误、定时器、黏度趋势、看门狗；'
                          '全部在室温、停止且盘上没有敞口液体时整段跳过（运行中的定时/计数解析推进，到时那一步照常积分）'}
EXEC_TEXT = {}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('sensor.set', 'vessel.place', 'vessel.remove', 'interface.set', 'safety.set_limit'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'heating.set': ['display.temperature'], 'stirring.set': ['display.speed'], 'namur.send': ['display.temperature', 'display.speed']}
_WB, _DS, _MAN, _NIST, _PC, _LIB, _SIM = ('user_workbook', 'manufacturer_datasheet', 'manufacturer_manual_2018', 'nist_webbook', 'pubchem', 'liquid_libraries',
                                          'simulation_assumption')
CAP_SOURCES = {k: [_DS, _MAN] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_DS, _WB, _SIM], 'vessel.place': [_MAN, _NIST, _PC, _LIB, _SIM], 'vessel.remove': [_SIM], 'medium.modify': [_SIM],
                    'events.read': [_MAN, _SIM], 'truth.inspect': [_SIM], 'ledger.read': [_SIM], 'fault.inject': [_MAN, _SIM], 'sim.step': [_SIM],
                    'namur.send': [_MAN], 'interface.set': [_DS, _MAN]})
PARAM_ZH = {'ambient_k': '室温', 'pressure_pa': '大气压', 'mains_v': '电源电压', 'noise_seed': '噪声种子', 'on': '开或关', 'temperature_k': '设定温度',
            'mode': '控温方式', 'limit_k': '安全温度', 'speed_rad_s': '设定转速', 'direction': '转向（cw顺时针、ccw逆时针）', 'auto_reverse': '自动换向',
            'run_s': '间歇运行时间（0为连续）', 'pause_s': '间歇暂停时间', 'duration_s': '定时（0为计数器）', 'operating_mode': '工作模式A/B/D', 'beep': '提示音',
            'unit': '温度单位', 'err5_timeout_s': 'Error 5时限（0关闭）', 'expiry_stop_heating': '定时结束停加热', 'expiry_stop_stirring': '定时结束停搅拌',
            'display': 'SAFE或VISC显示', 'connected': '已插入', 'immersed': '传感器在介质中', 'vessel': '容器型号', 'substance_id': '液体（模板库）',
            'volume_m3': '液体体积', 'initial_k': '液体初温', 'stir_bar_length_m': '搅拌子长度（0为无）', 'command': 'NAMUR命令', 'viscosity_factor': '黏度倍数',
            'since_s': '起始时间', 'fault': '故障名', 'active': '注入或清除', 'dt_s': '推进时间'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.hotplate_process.limitations。'
CAP_LIMITS = {
    'vessel.place': ['液体为模板库中的单一纯物质；物性除蒸气压外不随温度变化；容器尺寸与质量为工程目录值；没有溶质、反应或混合物。'],
    'namur.send': ['实现手册列出的NAMUR命令；命令回应格式按手册示例简化（值加通道号）；labworldsoft的图形与斜坡不在本机内。'],
    'menu.configure': ['菜单的两点探头校准与固件版本未建模；单位只影响显示说明（接口一律SI）。'],
}
LIMITATIONS = [
    '所选配置：IKA Plate (RCT digital) 25004601（220–230 V），附PT1000.60传感器；ETS-D5/D6接触温度计未单独建模（按PT1000处理，精度见数据表）。',
    '热模型：加热盘与“容器+液体”两个集总节点；盘—液传热系数按1 L水在H1500烧杯中6.5 K/min标定（手册写7 K/min，见冲突），搅拌与不锈钢锅底按工程倍数修正；侧壁与液面损失线性化；敞口蒸发按传质系数；沸点按Clausius–Clapeyron随大气压修正。',
    '自升温：磁体涡流发热与转速平方成正比，按数据表“最高转速1 h自升温+13 K”标定（手册写+8 °C，见冲突）。',
    '控温：加热盘PI、介质PID或两点控制为工程实现，复核数据表精度（PT1000±0.5 K、加热盘±5 K）；IKA固件的实际控制算法未公开。',
    '搅拌：磁力耦合扭矩、搅拌子阻力系数为工程值；脱耦时搅拌子只摆动（约电机转速的15 %），传热与混合按此降低；黏度趋势为相对扭矩。',
    '错误码：实现Err. 1、3、4、5、6、14、21、24、25、31（手册列表）；Err. 2按NAMUR看门狗2的“改为安全值并显示WD”实现；看门狗1手册正文写“显示ER 2”、错误表写Err. 1，按错误表显示Err. 1（E01）；'
    'Err. 13、22、26、44、46需要内部传感器失效细节，未实现。',
    'Err. 4：磁耦合能传的扭矩小于电机扭矩，介质过黏时搅拌子先脱耦，电机不会因黏度过载；Err. 4只在评测者注入motor_blocked且搅拌开启时出现。',
    '工作模式D：存储并显示D，开机与A相同（加热、搅拌关）；手册中“开机须按旋钮确认安全温度”和“新设定值闪5 s、按旋钮确认否则恢复”未建模，设定立即生效。',
    '安全温度：螺钉可调到50–370 °C任意位置（手册设定步骤与功能检查）；最高设定温度仍比安全温度低15 K；调到加热盘温度以下时加热中为Err. 25、不加热时为Err. 24（手册设定步骤写Err. 25，错误表Err. 24为“加热盘温度高于安全温度”）。',
    '蒸发：敞口液面按传质系数与蒸气压差计算；水的背景蒸气压按相对湿度（plant.configure，默认0.5）；其他溶剂背景为0。液体不会凝固（凝固未建模）：放上与改环境时温度须高于熔点。',
    '显示文字按七段字形给出：HOT!写作HOt、Err. n写作Enn、看门狗2的WD写作SAFE（七段字形画不出T与W）；含义与手册一致。',
    '场景：本体、加热盘、旋钮、按键、显示与接口有几何；容器、液体与搅拌子没有几何（运行层状态）；LCD的实际/设定温度、实际/设定转速、安全温度或黏度趋势、定时/计数与文字按运行层状态显示，'
    '指示符号按状态点亮，旋钮为双向关节。',
]


def _sampling_text(k):
    if k == 'clock.time_s':
        return 'Host clock.'
    if SLOTS[k]['origin'] in ('sensor', 'derived', 'process_truth'):
        return 'Updated on the 0.1 s grid of the host steps (display values rounded as the LCD shows them).'
    return 'Changes when a command is accepted.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if CAPS[k]['visibility'] != 'agent' else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _pre_text(k):
    errs, text = CAP_ERRORS.get(k, []), PRECONDITIONS[k][0]
    if 'PLANT_REQUIRED' in errs and '声明' not in text:
        text = '已声明环境（否则PLANT_REQUIRED）；' + text
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
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节、读数或指示灯', binding_ids=[])


def _params():
    P = {}

    def si(name, val, unit):
        """Contract parameters in SI: degC -> K, rpm -> rad/s, min -> s (the datasheet / runtime value is kept beside it)."""
        conv = {'degC': ('K', lambda x: x + C0, '_c', '_k'), 'rpm': ('rad/s', lambda x: x * RPM, '_rpm', '_rad_s'), 'min': ('s', lambda x: x * 60.0, '_min', '_s')}
        if unit not in conv:
            return name, val, unit, ''
        u, f, a, b = conv[unit]
        val2 = [f(x) if isinstance(x, (int, float)) else x for x in val] if isinstance(val, list) else f(val)
        return (name[:-len(a)] + b if name.endswith(a) else name + b), val2, u, f'（换算为SI；资料原值{val} {unit}）'

    def doc(name, val, unit, meaning, src):
        name, val, unit, extra = si(name, val, unit)
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning + extra, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        name, val, unit, extra = si(name, val, unit)
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning + extra, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_datasheet': _DS, 'manufacturer_manual_2018': _MAN}
    WB_KEYS = ('stir_volume_max_m3', 'speed_range_rpm', 'speed_step_rpm', 'heater_power_w', 'set_temperature_range_c', 'plate_diameter_m', 'dimensions_m', 'mass_kg')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
    for k in sorted(ENG):
        ev = instrument()['engineering'][k]['evidence']
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '已标定（calibrate_hotplate.py），模型自洽，不是实测' if ev == 'calibrated' else '工程值（见instrument_model.json的engineering说明），不是实测')
    for k, v in instrument()['derived'].items():
        asm(k, v['value'], v['unit'], '由厂家数据推导：' + v['note_zh'], '推导值', (_DS, _MAN))
    lt = _lib('liquids')
    asm('liquid_templates', dict(file='assets/expansion_batch05/t4_measure/ika_plate_rctdigital/liquid_templates.json', counts=lt['counts']), '1',
        '液体模板（比热、沸点、汽化热来自NIST，密度、黏度、蒸气压来自PubChem与共享库；每条带来源）', '实测文献数据；适用规则见模板库rules_zh', (_NIST, _PC, _LIB))
    for name, val, unit, meaning in (('stefan_boltzmann', SIGMA, 'W/(m2 K4)', '斯特藩—玻尔兹曼常数'), ('gas_constant_j_mol_k', R_GAS, 'J/(mol K)', '气体常数')):
        asm(name, val, unit, meaning, '物理常数', (_SIM,))
    return P


SLOT_REFERENCE = {'display.temperature': '显示的实际温度（°C，接PT1000时为介质）', 'display.speed': '显示的实际电机转速（rpm）'}


def export_ika_plate_rctdigital_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'IkaPlateRctdigitalSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.ika_plate_rctdigital.IkaPlateRctdigitalSession.invoke',
                                              invocation=f'session.invoke(instance_id="ika_plate_rctdigital_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='IkaPlateRctdigitalRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（温度与转速在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['housing', 'heating_plate', 'knob_temperature', 'knob_speed', 'lcd', 'safety_screw', 'sensor_jack'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='磁力加热搅拌：600 W加热元件加热铝合金盘，盘经容器底把热量传给液体；驱动磁体带动容器内的搅拌子；无外接传感器时控制加热盘温度，'
                   '接PT1000时控制介质温度（PID或两点）；安全回路在安全温度切断加热；定时、锁定、工作模式、黏度趋势、NAMUR远程与看门狗按手册',
        equations=['C_p dT_p/dt = P_heat + P_mag - G_p (T_p - T_a) - eps sigma A_exp (T_p^4 - T_a^4) - U A_b f_stir f_mat (T_p - T_l)',
                   '(m c_p + m_v c_v) dT_l/dt = U A_b f_stir f_mat (T_p - T_l) - h A_side (T_l - T_a) - dm_evap/dt h_vap;  T_l <= T_b(p)',
                   'dm_evap/dt = k_m A_s p_vap(T_l) M / (R T_l);  T_b(p) = 1 / (1/T_b0 - R/h_vap,mol ln(p/p0))',
                   'P_mag = P_max (n/1500)^2;  f_stir = f_0 + (1 - f_0) min(n_bar/600, 1)',
                   'tau_drag = a mu w L^3 + b rho w^2 L^5;  decoupled if tau_drag > tau_c = c L;  visc trend = tau / tau_ref x 100 %',
                   'plate PI: P = Kp (T_target - T_p) + Ki int(.);  medium PID: T_target = T_set + Kp e + Ki int e + Kd de/dt (<= min(310 C, T_safe - 15 K))'],
        parameters=_params(), source_ids=[_SIM, _WB, _DS, _MAN, _NIST, _PC, _LIB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ika_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('ika_plate_rctdigital', caps, slots, {'hotplate_process': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'vessel.place': (VESSEL, CAPS['vessel.place']['example_arguments']),
                                'menu.configure': (MENU, CAPS['menu.configure']['example_arguments'])})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'hotplate_process': mechanism})
