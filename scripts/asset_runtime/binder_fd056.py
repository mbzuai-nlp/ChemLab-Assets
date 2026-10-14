"""CHEM-011 BINDER FD 56 (FD056-230V, 9010-0303) drying and heating chamber with forced convection: process runtime.

Device (BINDER Data Sheet Model FD 56, 27.04.2022; operating manual BD/BF/ED/FD/FED E3.1 12/2017): 60 l interior (400 x 440 x 345 mm, steam space
67 l), ambient + 10 C to 300 C, fluctuation +-0.3 K and variation +-1.7 K at 150 C, 15 min to 150 C, 4 min recovery after the door was open 30 s,
80 air changes per hour at 100 C, 1.1 kW, 290 Wh/h at 150 C; R4 controller in the triangular panel on the door (up, down, OK, back, standby keys;
set-point in 1 C steps; fixed fan speed), electromechanical exhaust air flap (CLOS, 1, 2, 3, OPEn in 15 deg steps), class 2 safety controller
(temperature limiter 10-300 C: heating off until manual reset, '!TLim'), class 1 overtemperature protective device (330 C, reset below 230 C after
disconnecting the mains >= 10 s), timer 'Delayed Off' (0-9 d, 0-23 h, 0-59 min; fan on or off afterwards), ramp function, USB data recording;
heating and fan stop while the door is open; 2 (max. 4) chrome racks of 30 kg, 70 kg in total; 560 x 625 x 565 mm.

Process model (fixed 0.1 s grid; standby or no mains with everything at ambient and no wet sample is skipped exactly; SI units):
  * Four nodes: heater element, chamber air (+ fan duct), inner liner with racks, insulation and outer shell:
      C_h dT_h/dt = P_heat - G_ha (T_h - T_a) - G_hl (T_h - T_l);  C_a dT_a/dt = G_ha (T_h - T_a) + P_fan - G_la (T_a - T_l) - m_dot c_p (T_a - T_amb) - sum Q_sample
      C_l dT_l/dt = G_la (T_a - T_l) + G_hl (T_h - T_l) - G_ls (T_l - T_s) - G_door (T_l - T_amb);  C_s dT_s/dt = G_ls (T_l - T_s) - G_sa (T_s - T_amb)
    G_ha, G_la with / without fan; m_dot from the air change of the flap position (80 /h fully open, datasheet) or the door-open exchange.
    G_ls, C_l and the door-open exchange are calibrated to the datasheet (290 W at 150 C, 15 min to 150 C, 4 min recovery).
  * Controller: Pt100 (first-order lag + noise) -> PID on the (ramp-limited) set-point -> heater duty through a solid-state relay period; the
    safety controller (class 2) latches the heating off above its limit; the class 1 device cuts above 330 C.
  * Samples on the racks: labware (load_templates.json materials) + dry solid + a liquid (CHEM-008 liquid library): C dT/dt = h A (T_local - T)
    - m_evap h_vap; m_evap = k_m A_open (p_vap(T) - p_partial) M / (R T) x min(1, X / X_crit) (constant, then falling rate); boiling above T_b(p);
    the vapour is held in the chamber air and vented with the air change (its total partial pressure never exceeds the ambient pressure - the
    excess is displaced through the exhaust; vapour above saturation at the air temperature condenses on the liner, its latent heat back to the
    air, the condensate drained and not re-evaporated); the local air temperature carries the documented +-1.7 K spatial field.
  * Ledgers: electric heater + fan energy + condensation heat + sample enthalpy in = node and sample enthalpy + shell and door losses + vented air
    + latent heat + sample enthalpy out; every liquid's mass: placed = on samples + evaporated + boiled + removed; vapour: evaporated + boiled =
    in the chamber + vented + condensed."""
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.binder_fd056"
CATALOG_ID = "CHEM-011"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch05/t4_measure/binder_fd056'
LIBS = {'instrument': 'instrument_model.json', 'loads': 'load_templates.json'}
C0 = 273.15
DT = 0.1
R_GAS = 8.314462618
M_AIR = 0.028965
CP_AIR = 1006.0
MAX_EVENTS_LOG = 3000
MAX_USB_ROWS = 200000


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


def _step_round(x, step):
    """Round half up to a multiple of step (the arrow keys never round half to even)."""
    return math.floor(x / step + 0.5 + 1e-12) * step


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
MAT = _lib('loads')['materials']
LIQ = _lib('loads')['liquids']
LIQ_IDS = sorted(LIQ)
WARES = ENG['ware_catalogue']
V_AIR = DOC['steam_space_m3']
T_MAX_SET = C0 + DOC['temperature_max_c']
SAFE_RANGE_K = (C0 + DOC['safety_range_c'][0], C0 + DOC['safety_range_c'][1])
CLASS1 = (C0 + DOC['overtemp_class1_text']['cut_off_c'], C0 + DOC['overtemp_class1_text']['reset_c'])
LEVELS = (1, 2, 3, 4)
POSITIONS = ('front_left', 'front_right', 'rear_left', 'rear_right')
FLAP_TEXT = {0: 'CLOS', 1: '1', 2: '2', 3: '3', 4: 'OPEn'}
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


# ---------------------------------------------------------------- liquid properties (CHEM-008 liquid template library)
def p_vap(sid, t_k):
    """Vapour pressure (Pa): the template's Antoine fit or its Clausius-Clapeyron runtime model (see the CHEM-008 library)."""
    v = LIQ[sid]['vapour_pressure']
    v = v.get('runtime_model', v)
    if v['kind'] == 'antoine':
        return 1e5 * 10.0 ** (v['A'] - v['B_k'] / (t_k + v['C_k']))
    return v['p_ref_pa'] * math.exp(-v['dh_vap_j_mol'] / R_GAS * (1.0 / t_k - 1.0 / v['t_ref_k']))


def boiling_k(sid, pressure_pa):
    s = LIQ[sid]
    return 1.0 / (1.0 / s['boiling_point']['value'] - R_GAS / s['enthalpy_vaporization']['molar_j_mol'] * math.log(pressure_pa / 101325.0))


def air_density(t_k, p_pa):
    return p_pa * M_AIR / (R_GAS * t_k)


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
SAMPLE_ID = {'type': 'string', 'minLength': 1, 'maxLength': 32, 'pattern': r'[A-Za-z0-9_\-]+'}
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


TEMP = number('K', C0, T_MAX_SET)
PLANT_EXAMPLE = dict(ambient_k=295.15, pressure_pa=101325.0, mains_v=230.0, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', C0 + 18.0, C0 + 40.0), pressure_pa=number('Pa', 70000.0, 110000.0), mains_v=number('V', 200.0, 253.0),
                 noise_seed=integer(0, 2 ** 31 - 1), relative_humidity=number('1', 0.0, 1.0)), optional=('relative_humidity',))
SAMPLE = obj(dict(sample_id=SAMPLE_ID, level=integer(1, 4), position=enum(POSITIONS), ware=enum(sorted(WARES)), dry_mass_kg=number('kg', 0.0, 30.0),
                  dry_cp_j_kgk=number('J/(kg K)', 100.0, 5000.0), dry_density_kg_m3=number('kg/m3', 200.0, 22000.0), liquid_id=nullable(enum(LIQ_IDS)),
                  liquid_mass_kg=number('kg', 0.0, 5.0), initial_k=number('K', 250.0, 600.0)),
             optional=('dry_cp_j_kgk', 'dry_density_kg_m3', 'liquid_id', 'liquid_mass_kg', 'initial_k'))
SAMPLE_EXAMPLE = dict(sample_id='s1', level=1, position='front_left', ware='weighing_bottle_50x30', dry_mass_kg=0.005, liquid_id='water', liquid_mass_kg=0.002)
FAULTS = ['mains_outage', 'heater_open', 'ssr_stuck_on', 'sensor_rupture', 'fan_failure', 'safety_controller_failure']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['housing'], '声明环境：室温（18–40 °C）、大气压、电源电压、相对湿度（默认0.5）与噪声种子（之后可改环境，种子不可改）',
    'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['btn_standby'], '待机键（按住约3 s）：开（正常显示、控温与风机）或待机')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['btn_standby'], '按住待机键约3 s：切换开/待机')
cap('door.set', obj(dict(angle_rad=number('rad', 0.0, ENG['door_max_rad']))), obj(dict(angle_rad=number('rad'), open=BOOL)), dict(angle_rad=1.57), ['door'],
    '开关门（门把手；开角0–103.5°；开门时加热与风机停止）', 'agent', 'robot_skill')
cap('temperature.set', obj(dict(temperature_k=TEMP)), obj(dict(temperature_k=TEMP)), dict(temperature_k=C0 + 105.0), ['panel'],
    '设定温度（设定菜单：方向键+OK；1 °C步进，0–300 °C；低于室温+10 °C的设定达不到——不能制冷）', 'agent', 'configuration')
cap('temperature.step', obj(dict(delta_k=number('K', -10.0, 10.0))), obj(dict(temperature_k=TEMP)), dict(delta_k=1.0), ['btn_up', 'btn_down'],
    '方向键：设定温度加/减（整数K；场景中上键+1 K、下键−1 K）')
cap('safety.set_limit', obj(dict(limit_k=number('K', *SAFE_RANGE_K))), obj(dict(limit_k=number('K'))), dict(limit_k=C0 + 110.0), ['panel'],
    '2级安全控制器设定（10–300 °C，1 °C；宜比温度设定高2–5 °C；超过时完全关闭加热直到手动复位）', 'agent', 'configuration')
cap('alarm.acknowledge', obj({}), obj(dict(alarm=nullable(TEXT), timer=FREE_OBJ)), {}, ['btn_ok'],
    'OK键：复位已冷却到安全设定以下的2级报警并恢复控温；定时结束后关闭定时功能（恢复加热）')
cap('flap.set', obj(dict(position=integer(0, 4))), obj(dict(position=integer(0, 4), text=TEXT)), dict(position=2), ['flap_actuator'],
    '排气风门开度（0=CLOS、1、2、3、4=OPEn，15°步进；开度越大换气越多，全开时均匀性变差）', 'agent', 'configuration')
cap('timer.set', obj(dict(days=integer(0, 9), hours=integer(0, 23), minutes=integer(0, 59), fan_after=enum(['on', 'off'])), optional=('fan_after',)),
    obj(dict(timer=FREE_OBJ)), dict(days=0, hours=2, minutes=0, fan_after='on'), ['panel'],
    '定时“延时关闭”：立即开始倒计时，结束时关加热，风机按选择开（100 %）或关；全部为0时结束定时', 'agent', 'configuration')
cap('ramp.set', obj(dict(gradient_k_min=integer(0, 10), target_k=TEMP)), obj(dict(ramp=FREE_OBJ)), dict(gradient_k_min=2, target_k=C0 + 150.0), ['panel'],
    '斜坡：梯度（°C/min，0关闭）与目标设定；目标高于实际温度时有效设定按梯度上升', 'agent', 'configuration')
cap('unit.set', obj(dict(unit=enum(['C', 'F']))), obj(dict(unit=enum(['C', 'F']))), dict(unit='C'), ['panel'], '温度单位°C/°F（只影响显示；接口一律SI）', 'agent',
    'configuration')
cap('display.cycle', obj({}), obj(dict(page=TEXT)), {}, ['btn_back'], '返回键：文字行在设定、风门、安全控制器、定时、斜坡、USB之间切换（菜单导航的抽象）')
cap('usb.set_stick', obj(dict(inserted=BOOL)), obj(dict(inserted=BOOL)), dict(inserted=True), ['usb_port'], '在三角面板USB口插拔U盘', 'agent', 'robot_skill')
cap('usb.record', obj(dict(on=BOOL, interval_min=integer(1, 60)), optional=('interval_min',)), obj(dict(usb=FREE_OBJ)), dict(on=True, interval_min=1), ['usb_port'],
    'USB数据记录开始/停止（记录间隔1–60 min）：定时、实际温度、设定、风机、风门、安全控制器、加热比例', 'agent', 'configuration')
cap('usb.read', obj(dict(since_s=number('s', 0.0)), optional=('since_s',)), obj(dict(rows=array(FREE_OBJ, 0, MAX_USB_ROWS), header=array(TEXT, 0, 16))), dict(since_s=0.0),
    ['usb_port'], '读出U盘上的CSV记录（运行记录）', 'agent', 'query')
cap('rack.set', obj(dict(level=integer(1, 4), present=BOOL)), obj(dict(racks=array(integer(1, 4), 0, 4))), dict(level=2, present=True), ['liner'],
    '放入或取出搁架（层1–4；标准2层在1、3层；须开门；取出须先拿走其上的样品）', 'agent', 'robot_skill')
cap('sample.place', SAMPLE, obj(dict(sample=FREE_OBJ)), SAMPLE_EXAMPLE, ['liner'],
    '把器皿（材料目录）连同干固体与湿液体（液体模板库）放到某层搁架的某个位置（须开门；搁架30 kg、总负载70 kg）', 'agent', 'robot_skill')
cap('sample.remove', obj(dict(sample_id=SAMPLE_ID)), obj(dict(sample=FREE_OBJ)), dict(sample_id='s1'), ['liner'],
    '取出样品（须开门）：返回器皿与样品的总质量、干固体与剩余液体质量和温度（“干燥样品”输出，称量在天平上）', 'agent', 'robot_skill')
cap('events.read', obj(dict(since_s=number('s', 0.0)), optional=('since_s',)), obj(dict(events=array(FREE_OBJ), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), ['housing'],
    '事件与报警记录', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(process=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, ['housing'], '评测者：各节点真实温度、换气、样品温度与含湿量、腔内蒸气', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, ['housing'], '评测者：能量与液体质量守恒账', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='ssr_stuck_on', active=True), ['housing'],
    '评测者：注入或清除故障（停电、加热器断路、固态继电器常通、Pt100断线、风机故障、安全控制器失效）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), ['housing'], '推进宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'fan.set_speed': '风速设定：FD为固定风速（手册：BF、FED才可设风速）。',
    'timer.delayed_on': '定时“延时开启”：只有BF、FED（手册8.1）。',
    'timer.temperature_dependent_off': '定时“温度相关延时关闭”：只有BF、FED（手册8.1）。',
    'object_temperature.read': '物体温度显示（附加Pt100）：选件，本配置没有。',
    'analog_output.read': '4–20 mA模拟输出：选件，本配置没有。',
    'interior_light.set': '内部照明：选件，本配置没有。',
    'inert_gas.connect': '惰性气体接口：选件（气密型），本配置没有。',
    'network.connect': '以太网接口（APT-COM）：选件，本配置没有。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['housing'], _v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'housing', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'housing', '环境是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'btn_standby', '控制器开（非待机）')
slot('door.angle_rad', number('rad', 0.0, ENG['door_max_rad']), 'rad', 'setpoint', 'door', '门开角（0关；门关节=该角）')
slot('door.open', BOOL, '1', 'derived', 'door', '门开关：开角大于门开关阈值即为开门（加热与风机停）')
slot('temperature.set_k', TEMP, 'K', 'setpoint', 'panel', '温度设定（斜坡运行时为目标；有效设定见ramp.state）')
slot('safety.limit_k', number('K'), 'K', 'setpoint', 'panel', '2级安全控制器设定')
slot('flap.position', integer(0, 4), '1', 'setpoint', 'flap_actuator', '排气风门开度（0=CLOS…4=OPEn）')
slot('flap.angle_rad', number('rad', 0.0, math.radians(60.0)), 'rad', 'derived', 'flap_actuator', '风门执行器转角=开度×15°（后部风门执行器关节）')
slot('timer.state', FREE_OBJ, '1', 'derived', 'panel', '定时“延时关闭”：是否设定、运行、结束、剩余时间、结束后风机')
slot('ramp.state', FREE_OBJ, '1', 'derived', 'panel', '斜坡：梯度、目标、有效设定、是否运行')
slot('unit', enum(['C', 'F']), '1', 'setpoint', 'panel', '显示温度单位')
slot('usb.state', FREE_OBJ, '1', 'setpoint', 'usb_port', 'U盘是否插入、是否记录、间隔、已记行数')
slot('racks.levels', array(integer(1, 4), 0, 4), '1', 'setpoint', 'liner', '搁架所在层')
slot('samples.placed', array(FREE_OBJ, 0, 16), '1', 'setpoint', 'liner', '腔内样品：编号、层、位置、器皿（运行层不给出温度与质量——须取出称量）')
slot('fan.running', BOOL, '1', 'derived', 'fan_cover', '风机是否运转（能听到）')
slot('alarm.state', nullable(enum(['limit_active', 'limit_latched', 'overtemperature'])), '1', 'derived', 'panel',
     '报警：2级限温报警（仍高于安全设定/已降下待复位）或1级过温保护')
slot('display.temperature', nullable(number('1')), '1', 'sensor', 'display', '大数字：实际温度（°C或°F，1°；Pt100断线显示1999；待机熄灭）')
slot('display.text', TEXT, '1', 'derived', 'display', '文字行（七段字形）：设定“SP 150”、风门“FL CLOS”、安全“SAFE 160”、定时“t dhhmm”、斜坡、报警“ALAr tLI”等')
for _k, _zh in (('heating', '加热图标'), ('timer', '定时图标'), ('alarm', '安全控制器报警图标'), ('standby', '待机图标')):
    slot(f'indicator.{_k}', BOOL, '1', 'derived', {'heating': 'icon_heating', 'timer': 'icon_timer', 'alarm': 'icon_alarm', 'standby': 'icon_standby'}[_k], _zh + '是否点亮')
slot('truth.process', FREE_OBJ, '1', 'process_truth', 'liner', '加热器、空气、内胆、外壳真实温度，换气，加热功率，样品温度与含湿量，腔内蒸气', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'housing', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'door.angle_rad', 'flap.angle_rad', 'display.temperature', 'display.text', 'indicator.heating', 'indicator.timer', 'indicator.alarm', 'indicator.standby'}


# ---------------------------------------------------------------- runtime
class BinderFd056Runtime:
    def __init__(self, instance_id='binder_fd056_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.on = False                                                       # starts in standby
        self.door = 0.0
        self.set_k = C0 + 25.0
        self.safe_k = SAFE_RANGE_K[1]                                         # factory setting unknown: the maximum (assumption)
        self.flap = 0
        self.unit = 'C'
        self.page = 'setp'
        self.timer = dict(set_s=0.0, remaining_s=0.0, running=False, expired=False, fan_after='on')
        self.ramp = dict(gradient=0, target_k=None, effective_k=None, active=False)
        self.usb = dict(inserted=False, recording=False, interval_s=60.0, next_s=None, rows=[])
        self.racks = {1, 3}
        self.samples = {}
        self.alarm2 = None                                                    # None / 'active' / 'latched'
        self.class1 = False
        self.class1_reset_armed = False
        self.outage_since = None
        self.Th = self.Ta = self.Tl = self.Ts = self.Tsens = None
        self.vapour = {}                                                      # liquid id -> vapour mass in the chamber air (kg)
        self.ctrl = dict(i=0.0, e_prev=None, d=0.0, duty=0.0, heater_w=0.0)
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
        return BinderFd056Session(self, name)

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

    def _active(self):
        return self.on and self._mains()

    def _need_active(self):
        self._need_plant()
        if not self._mains():
            raise ActionRejected('NO_MAINS')
        if not self.on:
            raise ActionRejected('STANDBY')

    def _door_open(self):
        return self.door > ENG['door_switch_rad']

    def _need_door_open(self):
        if not self._door_open():
            raise ActionRejected('DOOR_CLOSED')

    def _fan_on(self):
        if not self._active() or self._door_open() or 'fan_failure' in self.faults:
            return False
        return not (self.timer['expired'] and self.timer['fan_after'] == 'off')

    def _effective_set(self):
        return self.ramp['effective_k'] if self.ramp['active'] else self.set_k

    # ---- samples
    def _sample_cap(self, s):
        w = WARES[s['ware']]
        cp_l = LIQ[s['liquid_id']]['heat_capacity']['value'] if s['liquid_id'] else 0.0
        return w['mass_kg'] * MAT[w['material']]['heat_capacity']['value'] + s['dry_mass_kg'] * s['dry_cp'] + s['liquid_kg'] * cp_l

    def _sample_mass(self, s):
        return WARES[s['ware']]['mass_kg'] + s['dry_mass_kg'] + s['liquid_kg']

    def _rack_load(self, level):
        return sum(self._sample_mass(s) for s in self.samples.values() if s['level'] == level)

    def _offset(self, level, position):
        """Local air temperature offset (K) at a rack position: the documented +-1.7 K at 150 C, scaled with the over-temperature."""
        ta = self.plant['ambient_k']
        f = ENG['uniformity_shape'][level - 1][POSITIONS.index(position)]
        g = DOC['temperature_variation_150_k'] * (self.Ta - ta) / (C0 + 150.0 - (C0 + 22.0))
        if self.flap == 4:
            g *= ENG['flap_open_uniformity_factor']
        if not self._fan_on():
            g *= ENG['nofan_uniformity_factor']
        return f * g

    # ---- capabilities
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'usb.read'):
            self.revision += 1
        return out

    def _cmd_plant_configure(self, ambient_k, pressure_pa, mains_v, noise_seed, relative_humidity=None):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        rh = ENG['ambient_rh_default'] if relative_humidity is None else float(relative_humidity)
        first = self.plant is None
        if not first:
            for s in self.samples.values():
                if s['liquid_id'] and s['liquid_kg'] > 0.0 and (LIQ[s['liquid_id']].get('melting_point_k') or 0.0) >= ambient_k:
                    raise ActionRejected('NOT_LIQUID')
        if not first and ambient_k != self.plant['ambient_k']:                # enthalpies are counted relative to ambient: book the change of reference
            d = ambient_k - self.plant['ambient_k']
            self.led['e_reconfigure_j'] += -(self._node_cap() + sum(self._sample_cap(s) for s in self.samples.values())) * d
        self.plant = dict(ambient_k=ambient_k, pressure_pa=pressure_pa, mains_v=mains_v, relative_humidity=rh)
        self.seed = noise_seed
        if first:
            self.Th = self.Ta = self.Tl = self.Ts = self.Tsens = ambient_k
            self.led = dict(e_heater_j=0.0, e_fan_j=0.0, e_shell_loss_j=0.0, e_door_loss_j=0.0, e_vent_j=0.0, e_latent_j=0.0, e_samples_in_j=0.0, e_samples_out_j=0.0,
                            e_ambient_reset_j=0.0, e_reconfigure_j=0.0, m_placed_kg=0.0, m_evaporated_kg=0.0, m_boiled_kg=0.0, m_removed_kg=0.0,
                            m_vented_kg=0.0, m_condensed_kg=0.0, e_condensation_j=0.0, e_electric_total_j=0.0)
        self._event('system', 'PLANT_CONFIGURED', '环境已声明（插上电源）')
        return dict(configured=True)

    def _node_cap(self):
        return ENG['heater_heat_capacity_j_k'] + ENG['air_heat_capacity_j_k'] + ENG['liner_heat_capacity_j_k'] + ENG['shell_heat_capacity_j_k']

    def _cmd_power_set(self, on):
        self._need_plant()
        if not self._mains():
            raise ActionRejected('NO_MAINS')
        if on != self.on:
            self.on = on
            self.ctrl.update(i=0.0, e_prev=None, d=0.0)
            self._event('operation', 'STANDBY_OFF' if on else 'STANDBY_ON', '退出待机（正常显示）' if on else '进入待机')
        return dict(on=self.on)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(not self.on)

    def _cmd_door_set(self, angle_rad):
        self._need_plant()
        was = self._door_open()
        self.door = float(angle_rad)
        if was != self._door_open():
            self._event('operation', 'DOOR_OPEN' if self._door_open() else 'DOOR_CLOSED', '开门（加热与风机停）' if self._door_open() else '关门')
            if self._door_open():
                self.ctrl.update(duty=0.0, heater_w=0.0, i=0.0, e_prev=None, d=0.0)   # the door switch cuts heater and fan immediately (manual 5.1)
        return dict(angle_rad=self.door, open=self._door_open())

    def _cmd_temperature_set(self, temperature_k):
        self._need_active()
        t = C0 + _step_round(temperature_k - C0, 1.0)
        self.set_k = min(max(t, C0), T_MAX_SET)
        self.ramp.update(active=False, effective_k=None, gradient=0, target_k=None)
        self.ctrl.update(i=0.0)
        return dict(temperature_k=self.set_k)

    def _cmd_temperature_step(self, delta_k):
        self._need_active()
        return self._cmd_temperature_set(min(max(self.set_k + _step_round(delta_k, 1.0), C0), T_MAX_SET))

    def _cmd_safety_set_limit(self, limit_k):
        self._need_active()
        self.safe_k = C0 + _step_round(limit_k - C0, 1.0)
        self._event('operation', 'SAFETY_SET', f'安全控制器设定{self.safe_k - C0:.0f} °C')
        return dict(limit_k=self.safe_k)

    def _cmd_alarm_acknowledge(self):
        self._need_active()
        if self.alarm2 == 'latched':
            self.alarm2 = None
            self.ctrl.update(i=0.0, e_prev=None, d=0.0)
            self._event('operation', 'ALARM_RESET', '复位2级安全控制器报警，恢复控温')
        if self.timer['expired']:
            self.timer.update(set_s=0.0, remaining_s=0.0, running=False, expired=False)
            self._event('operation', 'TIMER_OFF', '关闭已结束的定时功能，恢复加热')
        return dict(alarm=self._alarm_state(), timer=self._timer_view())

    def _cmd_flap_set(self, position):
        self._need_active()
        self.flap = int(position)
        return dict(position=self.flap, text=FLAP_TEXT[self.flap])

    def _cmd_timer_set(self, days, hours, minutes, fan_after=None):
        self._need_active()
        total = days * 86400.0 + hours * 3600.0 + minutes * 60.0
        if total <= 0.0:
            self.timer.update(set_s=0.0, remaining_s=0.0, running=False, expired=False)
            self._event('operation', 'TIMER_OFF', '定时结束（全部设为0）')
        else:
            self.timer = dict(set_s=total, remaining_s=total, running=True, expired=False, fan_after=fan_after or self.timer['fan_after'])
            self._event('operation', 'TIMER_SET', f'延时关闭定时{days} d {hours} h {minutes} min')
        return dict(timer=self._timer_view())

    def _timer_view(self):
        tm = self.timer
        return dict(set=tm['set_s'] > 0.0, running=tm['running'], expired=tm['expired'], remaining_s=tm['remaining_s'], set_s=tm['set_s'], fan_after=tm['fan_after'])

    def _cmd_ramp_set(self, gradient_k_min, target_k):
        self._need_active()
        target = C0 + _step_round(target_k - C0, 1.0)
        if gradient_k_min == 0 or target <= self.Tsens:                       # off, or the target is not above the actual value: set directly
            self.set_k = target
            self.ramp = dict(gradient=int(gradient_k_min), target_k=target if gradient_k_min else None, effective_k=None, active=False)
        else:
            self.ramp = dict(gradient=int(gradient_k_min), target_k=target, effective_k=self.set_k, active=True)
            self.set_k = target
        return dict(ramp=self._ramp_view())

    def _ramp_view(self):
        r = self.ramp
        return dict(gradient_k_min=r['gradient'], target_k=r['target_k'], effective_k=r['effective_k'] if r['active'] else self.set_k, active=r['active'])

    def _cmd_unit_set(self, unit):
        self._need_active()
        self.unit = unit
        return dict(unit=unit)

    def _cmd_display_cycle(self):
        self._need_active()
        pages = ['setp', 'flap', 'safety', 'timer', 'ramp', 'usb']
        self.page = pages[(pages.index(self.page) + 1) % len(pages)]
        return dict(page=self.page)

    def _cmd_usb_set_stick(self, inserted):
        self._need_plant()
        if not inserted and self.usb['recording']:
            self.usb.update(recording=False, next_s=None)
            self._event('warning', 'USB_REMOVED_WHILE_RECORDING', '记录中拔出U盘：记录停止')
        if not inserted:
            self.usb['rows'] = []                                             # the data leave with the stick
        self.usb['inserted'] = inserted
        return dict(inserted=inserted)

    def _cmd_usb_record(self, on, interval_min=None):
        self._need_active()
        if not self.usb['inserted']:
            raise ActionRejected('NO_STICK')
        if interval_min is not None:
            self.usb['interval_s'] = 60.0 * interval_min
        if on and not self.usb['recording']:
            self.usb.update(recording=True, next_s=round(self.t_grid + DT, 9))   # first row at the next grid point, then every interval
            self._event('operation', 'USB_START', f'开始USB记录（间隔{self.usb["interval_s"] / 60.0:.0f} min）')
        elif not on and self.usb['recording']:
            self.usb.update(recording=False, next_s=None)
            self._event('operation', 'USB_STOP', '停止USB记录')
        return dict(usb=self._usb_view())

    def _usb_view(self):
        return dict(inserted=self.usb['inserted'], recording=self.usb['recording'], interval_s=self.usb['interval_s'], rows=len(self.usb['rows']))

    def _cmd_usb_read(self, since_s=0.0):
        self._need_plant()
        if not self.usb['inserted']:
            raise ActionRejected('NO_STICK')
        return dict(header=['time_s', 'timer_s', 'actual_c', 'setpoint_c', 'fan', 'flap', 'safety_c', 'heating_percent'],
                    rows=[r for r in self.usb['rows'] if r['time_s'] >= since_s])

    def _cmd_rack_set(self, level, present):
        self._need_plant()
        self._need_door_open()
        if present:
            self.racks.add(level)
        else:
            if any(s['level'] == level for s in self.samples.values()):
                raise ActionRejected('RACK_LOADED')
            self.racks.discard(level)
        return dict(racks=sorted(self.racks))

    def _cmd_sample_place(self, sample_id, level, position, ware, dry_mass_kg, dry_cp_j_kgk=1000.0, dry_density_kg_m3=1500.0, liquid_id=None, liquid_mass_kg=0.0,
                          initial_k=None):
        self._need_plant()
        self._need_door_open()
        if sample_id in self.samples:
            raise ActionRejected('DUPLICATE_ID')
        if level not in self.racks:
            raise ActionRejected('NO_RACK')
        if any(s['level'] == level and s['position'] == position for s in self.samples.values()):
            raise ActionRejected('POSITION_OCCUPIED')
        if liquid_mass_kg > 0.0 and not liquid_id:
            raise ActionRejected('INVALID_ARGUMENT')
        w = WARES[ware]
        t0 = self.plant['ambient_k'] if initial_k is None else float(initial_k)
        lid = liquid_id if liquid_mass_kg > 0.0 else None
        if (liquid_mass_kg / LIQ[lid]['density']['value'] if lid else 0.0) + dry_mass_kg / dry_density_kg_m3 > w['capacity_m3'] + 1e-12:
            raise ActionRejected('OVERFILL')                                  # solid (bulk density, default 1500 kg/m3) and liquid must fit the labware
        if lid:
            if (LIQ[lid].get('melting_point_k') or 0.0) >= t0 or t0 >= boiling_k(lid, self.plant['pressure_pa']):
                raise ActionRejected('NOT_LIQUID')
        s = dict(level=level, position=position, ware=ware, dry_mass_kg=float(dry_mass_kg), dry_cp=float(dry_cp_j_kgk), liquid_id=lid, liquid_kg=float(liquid_mass_kg),
                 T=t0, evap_kg=0.0, boil_kg=0.0, placed_at=self.t, overheated=False)
        if self._rack_load(level) + self._sample_mass(s) > DOC['rack_load_kg'] + 1e-9 or \
                sum(self._sample_mass(x) for x in self.samples.values()) + self._sample_mass(s) > DOC['total_load_kg'] + 1e-9:
            raise ActionRejected('OVERLOAD')
        self.samples[sample_id] = s
        ta = self.plant['ambient_k']
        self.led['e_samples_in_j'] += self._sample_cap(s) * (t0 - ta)
        self.led['m_placed_kg'] += s['liquid_kg']
        self._event('operation', 'SAMPLE_PLACED', f'放入样品{sample_id}（{ware}，第{level}层{position}）')
        return dict(sample=self._sample_public(sample_id))

    def _sample_public(self, sid):
        s = self.samples[sid]
        return dict(sample_id=sid, level=s['level'], position=s['position'], ware=s['ware'])

    def _cmd_sample_remove(self, sample_id):
        self._need_plant()
        self._need_door_open()
        if sample_id not in self.samples:
            raise ActionRejected('NO_SAMPLE')
        s = self.samples.pop(sample_id)
        ta = self.plant['ambient_k']
        self.led['e_samples_out_j'] += self._sample_cap(s) * (s['T'] - ta)
        self.led['m_removed_kg'] += s['liquid_kg']
        self._event('operation', 'SAMPLE_REMOVED', f'取出样品{sample_id}')
        return dict(sample=dict(sample_id=sample_id, ware=s['ware'], ware_mass_kg=WARES[s['ware']]['mass_kg'], total_mass_kg=self._sample_mass(s),
                                dry_mass_kg=s['dry_mass_kg'], liquid_id=s['liquid_id'], liquid_mass_kg=s['liquid_kg'], temperature_k=s['T'],
                                liquid_lost_kg=s['evap_kg'] + s['boil_kg']))

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
        was = self._mains()
        (self.faults.add if active else self.faults.discard)(fault)
        if was and not self._mains():
            self.outage_since = self.t
            self._event('alarm', 'POWER_FAILURE', '停电（USB记录中断）')
        elif not was and self._mains():
            if self.class1 and self.t - (self.outage_since or self.t) >= 10.0 - 1e-9:
                self.class1_reset_armed = True                                # manual 7.1: after >= 10 s off the alarm clears once below 230 C
                if self.Ta < CLASS1[1]:
                    self.class1 = False
                    self.class1_reset_armed = False
                    self._event('system', 'OVERTEMP_RESET', '断电≥10 s且已冷却到230 °C以下：1级过温保护复位')
            self.outage_since = None
            u = self.usb
            if u['recording'] and u['next_s'] is not None:                    # recording was interrupted: the rows of the outage are not written
                while u['next_s'] <= self.t_grid + 1e-9:
                    u['next_s'] = round(u['next_s'] + u['interval_s'], 9)
            self._event('system', 'POWER_RESTORED', '来电（控制器恢复原状态；USB记录继续，停电期间无记录）')
        if active and fault == 'sensor_rupture':
            self.ctrl.update(i=0.0, d=0.0, e_prev=None)
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}' + ('：注入' if active else '：清除'))
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- alarms
    def _alarm_state(self):
        if self.class1:
            return 'overtemperature'
        return {'active': 'limit_active', 'latched': 'limit_latched'}.get(self.alarm2)

    # ---- physics
    def _quiet(self):
        if self.plant is None:
            return True
        if self._active():
            return False                                                      # the controller runs the fan whenever it is on (door closed)
        if 'ssr_stuck_on' in self.faults and self._mains() and not self.class1 and (self.alarm2 is None or 'safety_controller_failure' in self.faults):
            return False
        if any(s['liquid_kg'] > 0.0 for s in self.samples.values()) or any(m > 0.0 for m in self.vapour.values()):
            return False
        ta = self.plant['ambient_k']
        return all(abs(x - ta) < 1e-9 for x in (self.Th, self.Ta, self.Tl, self.Ts, self.Tsens)) and all(abs(s['T'] - ta) < 1e-9 for s in self.samples.values())

    def _quiet_jump(self, t_end):
        n = int(math.floor((t_end - self.t_grid) / DT + 1e-6))
        tm = self.timer
        if n > 0 and tm['running'] and self._mains():
            n = min(n, int(math.ceil(tm['remaining_s'] / DT - 1e-6)) - 1)      # the expiry itself is ticked
        u = self.usb
        if n > 0 and u['recording'] and self._mains() and u['next_s'] is not None:
            k = int(math.floor((self.t_grid + n * DT - u['next_s']) / u['interval_s'] + 1e-9)) + 1 if u['next_s'] <= self.t_grid + n * DT + 1e-9 else 0
            for i in range(max(k, 0)):                                        # rows written analytically: each with its own timer value and noise sample
                r_t = u['next_s']
                tm_s = max(round(tm['remaining_s'] - (r_t - self.t_grid), 9), 0.0) if tm['running'] else None
                self._usb_row(r_t, tick=int(round(r_t / DT)) - 1, timer_s=tm_s)
                u['next_s'] = round(u['next_s'] + u['interval_s'], 9)
        if n > 0 and tm['running'] and self._mains():
            tm['remaining_s'] = round(tm['remaining_s'] - n * DT, 9)
        return max(n, 0)

    def step(self, dt_s):
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = round(self.t + float(dt_s), 9)
        if self.plant is None:
            self.t = t_end
            self.tick = int(math.floor(t_end / DT + 1e-6))
            self.t_grid = round(self.tick * DT, 9)
            return self.t
        while self.t_grid + DT <= t_end + 1e-9:
            if self._quiet():
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

    def _air_change_kg_s(self):
        """Air exchanged with the room (kg/s): the door-open exchange, or the flap air change (80 /h fully open at 100 C with the fan, datasheet)."""
        rho = air_density(self.Ta, self.plant['pressure_pa'])
        if self._door_open():
            return ENG['door_open_air_change_per_s'] * V_AIR * rho
        n = ENG['flap_air_change_per_h'][self.flap] / 3600.0
        if not self._fan_on():
            n *= ENG['flap_nofan_factor']
        return n * V_AIR * rho

    def _controller(self, h):
        """PID on the Pt100 reading -> heater duty (0..1); the solid-state relay switches the full heater power for duty x its period."""
        c = self.ctrl
        allowed = self._active() and not self._door_open() and self.alarm2 is None and not self.class1 and not self.timer['expired'] \
            and 'sensor_rupture' not in self.faults
        if not allowed:
            c.update(duty=0.0, i=0.0, e_prev=None, d=0.0)
            return 0.0
        if self.ramp['active']:
            r = self.ramp
            r['effective_k'] = min(r['effective_k'] + r['gradient'] / 60.0 * h, r['target_k'])
            if r['effective_k'] >= r['target_k'] - 1e-12:
                r.update(active=False, effective_k=None)
                self._event('operation', 'RAMP_DONE', '斜坡到达目标设定')
        e = self._effective_set() - self.Tsens
        raw = 0.0 if c['e_prev'] is None else (e - c['e_prev']) / h
        c['e_prev'] = e
        c['d'] += (raw - c['d']) * h / ENG['pid_d_filter_s']
        if abs(e) < ENG['pid_i_band_k']:
            c['i'] = min(max(c['i'] + ENG['pid_ki_w_ks'] * e * h, 0.0), ENG['heater_power_w'])
        p = ENG['pid_kp_w_k'] * e + c['i'] + ENG['pid_kd_ws_k'] * c['d']
        c['duty'] = min(max(p / ENG['heater_power_w'], 0.0), 1.0)
        return c['duty']

    def _tick(self, h):
        L = self.led
        ta = self.plant['ambient_k']
        P = self.plant['pressure_pa']
        mains = self._mains()
        # ---- controller and heater
        duty = self._controller(h) if mains else 0.0
        per = ENG['ssr_period_s']
        phase = (self.t % per) / per
        stuck = 'ssr_stuck_on' in self.faults and mains and not self.class1
        relay = stuck or (duty > 0.0 and phase < duty - 1e-12)
        safety_relay_closed = self.alarm2 is None or 'safety_controller_failure' in self.faults   # the class 2 limiter opens its own relay
        heater_ok = 'heater_open' not in self.faults and not self.class1 and mains and safety_relay_closed
        p_heat = ENG['heater_power_w'] * (self.plant['mains_v'] / 230.0) ** 2 if (relay and heater_ok) else 0.0
        self.ctrl['heater_w'] = p_heat
        fan = self._fan_on()
        p_fan = ENG['fan_power_w'] if fan else 0.0
        # ---- conductances
        g_ha = ENG['heater_air_w_k'] if fan else ENG['heater_air_nofan_w_k']
        g_la = ENG['liner_air_w_k'] if fan else ENG['liner_air_nofan_w_k']
        g_ls, g_sa, g_hl = ENG['liner_shell_w_k'], ENG['shell_ambient_w_k'], ENG['heater_liner_w_k']
        g_door = ENG['door_open_liner_loss_w_k'] if self._door_open() else 0.0
        m_dot = self._air_change_kg_s()
        q_ha = g_ha * (self.Th - self.Ta)
        q_hl = g_hl * (self.Th - self.Tl)                                     # the heater sits in the side pre-heating duct of the liner
        q_la = g_la * (self.Ta - self.Tl)
        q_ls = g_ls * (self.Tl - self.Ts)
        q_sa = g_sa * (self.Ts - ta)
        q_door = g_door * (self.Tl - ta)
        q_vent = m_dot * CP_AIR * (self.Ta - ta)
        # ---- samples
        hs = ENG['sample_h_fan_w_m2k'] if fan else ENG['sample_h_nofan_w_m2k']
        km = ENG['evap_mass_transfer_fan_m_s'] if fan else ENG['evap_mass_transfer_nofan_m_s']
        q_samples = 0.0
        evap_total = {}
        for sid_, s in self.samples.items():
            w = WARES[s['ware']]
            t_loc = self.Ta + self._offset(s['level'], s['position'])
            q = hs * w['area_m2'] * (t_loc - s['T'])
            q_samples += q
            cap_ = self._sample_cap(s)
            H = cap_ * (s['T'] - ta) + q * h
            m_e = m_b = 0.0
            lid = s['liquid_id']
            if lid and s['liquid_kg'] > 0.0:
                liq = LIQ[lid]
                hv, cp_l, mm = liq['enthalpy_vaporization']['value'], liq['heat_capacity']['value'], liq['molar_mass_kg_mol']
                tb = boiling_k(lid, P)
                pv = min(p_vap(lid, min(s['T'], tb)), P)
                pp = self.vapour.get(lid, 0.0) * R_GAS * self.Ta / (mm * V_AIR)
                if lid == 'water':
                    pp += self._room_water_pa()
                X = s['liquid_kg'] / s['dry_mass_kg'] if s['dry_mass_kg'] > 0.0 else math.inf
                f = min(1.0, X / ENG['critical_moisture_fraction'])
                m_e = min(max(km * w['open_area_m2'] * (pv - pp) * mm / (R_GAS * s['T']), 0.0) * f * h, s['liquid_kg'])
                H -= m_e * hv + m_e * cp_l * (s['T'] - ta)
                s['liquid_kg'] -= m_e
                cap_ = self._sample_cap(s)
                t_new = ta + H / cap_
                if s['liquid_kg'] > 0.0 and t_new > tb:                       # boiling: the excess over the boiling point boils liquid off
                    m_b = min((t_new - tb) * cap_ / hv, s['liquid_kg'])
                    H -= m_b * hv + m_b * cp_l * (tb - ta)
                    s['liquid_kg'] -= m_b
                    cap_ = self._sample_cap(s)
                    t_new = ta + H / cap_
                if 0.0 < s['liquid_kg'] < 1e-12:                              # last traces
                    m_e += s['liquid_kg']
                    s['liquid_kg'] = 0.0
                L['e_latent_j'] += (m_e + m_b) * hv
                L['e_vent_j'] += (m_e * (s['T'] - ta) + m_b * (tb - ta)) * cp_l          # the vapour leaves with its sensible heat
                s['evap_kg'] += m_e
                s['boil_kg'] += m_b
                L['m_evaporated_kg'] += m_e
                L['m_boiled_kg'] += m_b
                evap_total[lid] = evap_total.get(lid, 0.0) + m_e + m_b
                s['T'] = t_new
            else:
                s['T'] = ta + H / cap_
            if s['T'] > MAT[w['material']]['max_service_k']['value'] and not s['overheated']:
                s['overheated'] = True
                self._event('truth', 'WARE_OVERHEATED', f'样品{sid_}的器皿超过材料最高使用温度')
        # ---- node temperatures
        self.Th += h * (p_heat - q_ha - q_hl) / ENG['heater_heat_capacity_j_k']
        self.Ta += h * (q_ha + p_fan - q_la - q_vent - q_samples) / ENG['air_heat_capacity_j_k']
        self.Tl += h * (q_la + q_hl - q_ls - q_door) / ENG['liner_heat_capacity_j_k']
        self.Ts += h * (q_ls - q_sa) / ENG['shell_heat_capacity_j_k']
        # ---- chamber vapour: evaporated in, vented out with the air change
        m_air = air_density(self.Ta, P) * V_AIR
        for lid, m in evap_total.items():
            self.vapour[lid] = self.vapour.get(lid, 0.0) + m
        bg_w = self._room_water_pa()                                          # room humidity carried in with the fresh air (background, not tracked)
        q_cond = 0.0
        for lid in list(self.vapour):                                         # above saturation at the air temperature the excess condenses on the
            mm = LIQ[lid]['molar_mass_kg_mol']                                # liner: latent heat back to the air, condensate drained (not re-evaporated)
            bg = bg_w if lid == 'water' else 0.0
            p_i = self.vapour[lid] * R_GAS * self.Ta / (mm * V_AIR)
            p_sat = min(p_vap(lid, self.Ta), P)
            if p_i + bg > p_sat:
                m_c = min((p_i + bg - p_sat) * mm * V_AIR / (R_GAS * self.Ta), self.vapour[lid])
                self.vapour[lid] -= m_c
                L['m_condensed_kg'] += m_c
                q_cond += m_c * LIQ[lid]['enthalpy_vaporization']['value'] / h
        L['e_condensation_j'] += q_cond * h
        self.Ta += h * q_cond / ENG['air_heat_capacity_j_k']               # latent heat of the condensate back to the air
        p_v = sum(m * R_GAS * self.Ta / (LIQ[lid]['molar_mass_kg_mol'] * V_AIR) for lid, m in self.vapour.items()) + bg_w
        if p_v > P and p_v > bg_w:                                            # the chamber is open through the exhaust: vapour above the ambient
            f_keep = max(P - bg_w, 0.0) / (p_v - bg_w)                        # pressure displaces the gas out of the duct
            for lid in list(self.vapour):
                out = self.vapour[lid] * (1.0 - f_keep)
                self.vapour[lid] -= out
                L['m_vented_kg'] += out
        for lid in list(self.vapour):
            out = min(self.vapour[lid], self.vapour[lid] * m_dot * h / m_air)
            self.vapour[lid] -= out
            L['m_vented_kg'] += out
            if self.vapour[lid] < 1e-15 and not evap_total.get(lid):
                L['m_vented_kg'] += self.vapour.pop(lid)
        L['e_heater_j'] += p_heat * h
        L['e_fan_j'] += p_fan * h
        L['e_shell_loss_j'] += q_sa * h
        L['e_door_loss_j'] += q_door * h
        L['e_vent_j'] += q_vent * h
        L['e_electric_total_j'] += (p_heat + p_fan + (ENG['controller_power_w'] if mains else 0.0)) * h
        # ---- Pt100 (lag + noise)
        tau = ENG['sensor_tau_s']
        self.Tsens = self.Ta + (self.Tsens - self.Ta) * math.exp(-h / tau)
        # ---- safety devices
        if mains:
            safety_reading = self.Ta + ENG['safety_sensor_offset_k']
            if 'safety_controller_failure' not in self.faults:
                if safety_reading > self.safe_k and self.alarm2 != 'active':
                    if self.alarm2 is None:
                        self._event('alarm', 'TEMP_LIMIT', '2级安全控制器动作：超过安全设定，加热完全关闭（!TLim）')
                    self.alarm2 = 'active'
                elif self.alarm2 == 'active' and safety_reading < self.safe_k:
                    self.alarm2 = 'latched'
                    self._event('alarm', 'TEMP_LIMIT_COOLED', '已降到安全设定以下：报警常亮，按OK复位')
            if self.class1 and self.class1_reset_armed and self.Ta < CLASS1[1]:
                self.class1 = False
                self.class1_reset_armed = False
                self._event('system', 'OVERTEMP_RESET', '重启后腔温降到230 °C以下：1级过温保护报警自动清除')
            if not self.class1 and self.Ta >= CLASS1[0]:
                self.class1 = True
                self.class1_reset_armed = False
                self._event('alarm', 'OVERTEMPERATURE', '1级过温保护动作（330 °C）：加热切断，须断电≥10 s并冷却')
        # ---- timer (runs while mains are present)
        tm = self.timer
        if mains and tm['running']:
            tm['remaining_s'] = max(round(tm['remaining_s'] - h, 9), 0.0)
            if tm['remaining_s'] <= 0.0:
                tm.update(running=False, expired=True)
                self._event('operation', 'TIMER_EXPIRED', '延时关闭定时结束：加热关闭' + ('，风机继续' if tm['fan_after'] == 'on' else '，风机停止'))
        # ---- USB record
        u = self.usb
        if mains and u['recording'] and u['next_s'] is not None and self.t + h >= u['next_s'] - 1e-9:
            self._usb_row(u['next_s'], tick=int(round(u['next_s'] / DT)) - 1)
            u['next_s'] = round(u['next_s'] + u['interval_s'], 9)
        # ---- ambient snap (idle only)
        idle = not self._active() and p_heat == 0.0 and not fan and not self.samples and not self.vapour
        if idle and all(abs(x - ta) < ENG['ambient_snap_k'] for x in (self.Th, self.Ta, self.Tl, self.Ts)):
            L['e_ambient_reset_j'] += (ENG['heater_heat_capacity_j_k'] * (self.Th - ta) + ENG['air_heat_capacity_j_k'] * (self.Ta - ta)
                                       + ENG['liner_heat_capacity_j_k'] * (self.Tl - ta) + ENG['shell_heat_capacity_j_k'] * (self.Ts - ta))
            self.Th = self.Ta = self.Tl = self.Ts = ta
            if abs(self.Tsens - ta) < ENG['ambient_snap_k']:
                self.Tsens = ta

    def _room_water_pa(self):
        return self.plant['relative_humidity'] * p_vap('water', self.plant['ambient_k'])

    def _sensor_reading(self, tick=None):
        return self.Tsens + ENG['sensor_noise_k'] * _gauss(self.seed, self.tick if tick is None else tick, 1)

    def _usb_row(self, t, tick=None, timer_s=None):
        """One CSV row at time t; the noise sample is the one of the grid tick ending at t (identical whether the interval is ticked or skipped)."""
        u = self.usb
        if len(u['rows']) >= MAX_USB_ROWS:
            return
        u['rows'].append(dict(time_s=t, timer_s=(timer_s if timer_s is not None else self.timer['remaining_s']) if self.timer['set_s'] > 0 else None,
                              actual_c=None if 'sensor_rupture' in self.faults else round(self._sensor_reading(tick) - C0, 1),
                              setpoint_c=round(self._effective_set() - C0, 1), fan='on' if self._fan_on() else 'off', flap=FLAP_TEXT[self.flap],
                              safety_c=round(self.safe_k - C0), heating_percent=round(100.0 * self.ctrl['duty'])))

    # ---- read
    def _disp_temp(self, k):
        return k - C0 if self.unit == 'C' else (k - C0) * 9.0 / 5.0 + 32.0

    def _display_values(self):
        if not self._active():
            return dict(temp=None, text='')
        if 'sensor_rupture' in self.faults:
            temp = 1999.0
        else:
            temp = float(round(self._disp_temp(self._sensor_reading())))
        a = self._alarm_state()
        if a == 'overtemperature':
            text = 'ALAr OtP'
        elif a:
            text = 'ALAr tLI'
        elif self.timer['expired']:
            text = 't   End'
        elif self.page == 'setp':
            text = f'SP{self._disp_temp(self._effective_set()):6.0f}'
        elif self.page == 'flap':
            text = f'FL{FLAP_TEXT[self.flap]:>6}'
        elif self.page == 'safety':
            text = f'SAFE{self._disp_temp(self.safe_k):4.0f}'
        elif self.page == 'timer':
            r = int(math.ceil(self.timer['remaining_s'] - 1e-9))
            text = f't {r // 86400:1d}{r % 86400 // 3600:02d}{r % 3600 // 60:02d}' if self.timer['set_s'] > 0 else 't   OFF'
        elif self.page == 'ramp':
            text = f'r{self.ramp["gradient"]:2d}{self._disp_temp(self.ramp["target_k"]):5.0f}' if self.ramp['gradient'] else 'r   OFF'
        else:
            text = 'Usb   On' if self.usb['recording'] else ('Usb  OFF' if self.usb['inserted'] else 'Usb   --')
        return dict(temp=temp, text=text)

    def read(self, visibility='agent'):
        pc = self.plant is not None
        d = self._display_values() if pc else dict(temp=None, text='')
        act = pc and self._active()
        out = {'clock.time_s': self.t, 'plant.configured': pc, 'power.on': self.on, 'door.angle_rad': self.door, 'door.open': self._door_open(),
               'temperature.set_k': self.set_k, 'safety.limit_k': self.safe_k, 'flap.position': self.flap, 'flap.angle_rad': math.radians(15.0) * self.flap,
               'timer.state': self._timer_view(), 'ramp.state': self._ramp_view(), 'unit': self.unit, 'usb.state': self._usb_view(), 'racks.levels': sorted(self.racks),
               'samples.placed': [self._sample_public(k) for k in sorted(self.samples)], 'fan.running': bool(pc and self._fan_on()),
               'alarm.state': self._alarm_state() if pc else None, 'display.temperature': d['temp'], 'display.text': d['text'],
               'indicator.heating': bool(act and self.ctrl['heater_w'] > 0.0), 'indicator.timer': bool(act and (self.timer['running'] or self.timer['expired'])),
               'indicator.alarm': bool(act and self._alarm_state() is not None), 'indicator.standby': bool(pc and self._mains() and not self.on)}
        if visibility != 'agent':
            out['truth.process'] = (dict(heater_k=self.Th, air_k=self.Ta, liner_k=self.Tl, shell_k=self.Ts, sensor_k=self.Tsens, heater_w=self.ctrl['heater_w'],
                                         duty=self.ctrl['duty'], air_change_kg_s=self._air_change_kg_s(), effective_set_k=self._effective_set(),
                                         class1_tripped=self.class1, vapour_kg=dict(self.vapour),
                                         samples={k: dict(temperature_k=s['T'], local_air_k=self.Ta + self._offset(s['level'], s['position']), liquid_kg=s['liquid_kg'],
                                                          evaporated_kg=s['evap_kg'], boiled_kg=s['boil_kg'], total_mass_kg=self._sample_mass(s),
                                                          moisture_ratio=(s['liquid_kg'] / s['dry_mass_kg'] if s['dry_mass_kg'] > 0 else None))
                                                  for k, s in self.samples.items()})
                                    if pc else {})
            out['truth.faults'] = sorted(self.faults)
        return out

    def _ledger(self):
        if self.led is None:
            return {}
        L = dict(self.led)
        ta = self.plant['ambient_k']
        L['h_nodes_j'] = (ENG['heater_heat_capacity_j_k'] * (self.Th - ta) + ENG['air_heat_capacity_j_k'] * (self.Ta - ta) + ENG['liner_heat_capacity_j_k'] * (self.Tl - ta)
                          + ENG['shell_heat_capacity_j_k'] * (self.Ts - ta))
        L['h_samples_j'] = sum(self._sample_cap(s) * (s['T'] - ta) for s in self.samples.values())
        L['energy_balance_j'] = (L['e_heater_j'] + L['e_fan_j'] + L['e_condensation_j'] + L['e_samples_in_j'] + L['e_reconfigure_j'] - L['e_samples_out_j'] - L['e_shell_loss_j'] - L['e_door_loss_j']
                                 - L['e_vent_j'] - L['e_latent_j'] - L['e_ambient_reset_j'] - L['h_nodes_j'] - L['h_samples_j'])
        L['m_on_samples_kg'] = sum(s['liquid_kg'] for s in self.samples.values())
        L['m_vapour_in_chamber_kg'] = sum(self.vapour.values())
        L['mass_balance_kg'] = L['m_placed_kg'] - L['m_evaporated_kg'] - L['m_boiled_kg'] - L['m_removed_kg'] - L['m_on_samples_kg']
        L['vapour_balance_kg'] = L['m_evaporated_kg'] + L['m_boiled_kg'] - L['m_vented_kg'] - L['m_condensed_kg'] - L['m_vapour_in_chamber_kg']
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
class BinderFd056Session:
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
        meta = copy.deepcopy(export_binder_fd056_contract_metadata())
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
                h.__dict__.update(snapshot)                                   # rejected: nothing changed
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
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本型号或本配置没有该功能', 'ACCESS_DENIED': '无权访问（真值或评测者能力）',
    'UNKNOWN_OPERATION': '会话不支持该操作（本运行层的能力都是同步的）', 'PLANT_REQUIRED': '尚未声明环境', 'SEED_FIXED': '噪声种子只能在首次声明时设定',
    'NO_MAINS': '停电（控制器不工作）', 'STANDBY': '控制器在待机（先按住待机键约3 s）', 'DOOR_CLOSED': '门关着（先开门）', 'NO_STICK': '没有插U盘',
    'RACK_LOADED': '该层搁架上还有样品', 'DUPLICATE_ID': '样品编号已存在', 'NO_RACK': '该层没有搁架', 'POSITION_OCCUPIED': '该位置已有样品',
    'OVERFILL': '液体与固体超过器皿容量', 'NOT_LIQUID': '液体在此温度下不是液体（低于熔点或高于沸点；改环境时：样品上的液体会凝固，凝固未建模）',
    'OVERLOAD': '超过每层30 kg或总负载70 kg', 'NO_SAMPLE': '没有该样品',
}
_P = ['PLANT_REQUIRED']
_A = _P + ['NO_MAINS', 'STANDBY']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED', 'NOT_LIQUID'], 'power.set': _P + ['NO_MAINS'], 'power.toggle': _P + ['NO_MAINS'], 'door.set': _P,
    'temperature.set': _A, 'temperature.step': _A, 'safety.set_limit': _A, 'alarm.acknowledge': _A, 'flap.set': _A, 'timer.set': _A, 'ramp.set': _A, 'unit.set': _A,
    'display.cycle': _A, 'usb.set_stick': _P, 'usb.record': _A + ['NO_STICK'], 'usb.read': _P + ['NO_STICK'], 'rack.set': _P + ['DOOR_CLOSED', 'RACK_LOADED'],
    'sample.place': _P + ['DOOR_CLOSED', 'DUPLICATE_ID', 'NO_RACK', 'POSITION_OCCUPIED', 'OVERFILL', 'NOT_LIQUID', 'OVERLOAD', 'INVALID_ARGUMENT'],
    'sample.remove': _P + ['DOOR_CLOSED', 'NO_SAMPLE'], 'events.read': [], 'truth.inspect': _P, 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_CTRL = ('有电且不在待机', ['power.on'])
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子（之后不可改）；样品上有液体时室温须高于其熔点', ['plant.configured', 'samples.placed']),
    'power.set': ('有电', ['power.on']), 'power.toggle': ('有电', ['power.on']), 'door.set': ('无', ['door.angle_rad']),
    'temperature.set': _CTRL, 'temperature.step': _CTRL, 'safety.set_limit': _CTRL, 'alarm.acknowledge': ('有电且不在待机；报警须已降到安全设定以下才会复位', ['power.on', 'alarm.state']),
    'flap.set': _CTRL, 'timer.set': _CTRL, 'ramp.set': ('有电且不在待机；目标高于实际温度时才按梯度运行', ['power.on', 'ramp.state']), 'unit.set': _CTRL, 'display.cycle': _CTRL,
    'usb.set_stick': ('无', ['usb.state']), 'usb.record': ('有电且不在待机；已插U盘', ['power.on', 'usb.state']), 'usb.read': ('已插U盘', ['usb.state']),
    'rack.set': ('门开着；取出时该层无样品', ['door.open', 'racks.levels', 'samples.placed']),
    'sample.place': ('门开着；该层有搁架且位置空；液体在初温为液体；不超过器皿容量与搁架30 kg、总70 kg', ['door.open', 'racks.levels', 'samples.placed']),
    'sample.remove': ('门开着；样品存在', ['door.open', 'samples.placed']),
    'truth.inspect': ('无', ['plant.configured']), 'fault.inject': ('无', ['plant.configured']),
}
_DISPLAY = ['display.temperature', 'display.text']
_IND = ['indicator.heating', 'indicator.timer', 'indicator.alarm', 'indicator.standby']
_EVOLVE = _DISPLAY + _IND + ['fan.running', 'alarm.state', 'timer.state', 'ramp.state', 'usb.state', 'truth.process']
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.process'] + _DISPLAY + _IND + ['fan.running', 'alarm.state'],
    'power.set': ['power.on', 'fan.running', 'truth.process'] + _DISPLAY + _IND, 'power.toggle': ['power.on', 'fan.running', 'truth.process'] + _DISPLAY + _IND,
    'door.set': ['door.angle_rad', 'door.open', 'fan.running', 'truth.process', 'indicator.heating'],
    'temperature.set': ['temperature.set_k', 'ramp.state', 'display.text', 'truth.process'], 'temperature.step': ['temperature.set_k', 'ramp.state', 'display.text', 'truth.process'],
    'safety.set_limit': ['safety.limit_k', 'display.text'],
    'alarm.acknowledge': ['alarm.state', 'timer.state', 'fan.running', 'indicator.alarm', 'indicator.timer', 'display.text', 'truth.process'],
    'flap.set': ['flap.position', 'flap.angle_rad', 'display.text', 'truth.process'], 'timer.set': ['timer.state', 'indicator.timer', 'display.text'],
    'ramp.set': ['temperature.set_k', 'ramp.state', 'display.text', 'truth.process'], 'unit.set': ['unit', 'display.temperature', 'display.text'], 'display.cycle': ['display.text'],
    'usb.set_stick': ['usb.state', 'display.text'], 'usb.record': ['usb.state', 'display.text'], 'usb.read': [],
    'rack.set': ['racks.levels'], 'sample.place': ['samples.placed', 'truth.process'], 'sample.remove': ['samples.placed', 'truth.process'],
    'fault.inject': ['truth.faults', 'fan.running', 'alarm.state', 'truth.process'] + _DISPLAY + _IND,
    'sim.step': ['clock.time_s'],
}
_STEPPED = ('plant.configure', 'power.set', 'power.toggle', 'door.set', 'temperature.set', 'temperature.step', 'safety.set_limit', 'alarm.acknowledge', 'flap.set',
            'timer.set', 'ramp.set', 'sample.place', 'sample.remove', 'fault.inject', 'usb.record')
ON_STEP = {k: list(_EVOLVE) for k in _STEPPED}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {'sim.step': '按0.1 s网格积分：加热器、空气、内胆、外壳与样品温度，换气，样品干燥与腔内蒸气，Pt100，控温与斜坡，安全装置，定时，USB记录；'
                          '待机或停电、全部在室温且无湿样品时整段跳过（定时与USB记录解析推进）'}
EXEC_TEXT = {}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('door.set', 'usb.set_stick', 'rack.set', 'sample.place', 'sample.remove'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'temperature.set': ['display.temperature'], 'power.set': ['display.temperature', 'indicator.standby'], 'door.set': ['fan.running'],
            'usb.record': ['usb.state']}
_WB, _DS, _MAN, _MATS, _NIST, _PC, _LIB, _SIM = ('user_workbook', 'manufacturer_datasheet', 'manufacturer_manual_2017', 'material_datasheets', 'nist_webbook', 'pubchem',
                                                 'liquid_library', 'simulation_assumption')
CAP_SOURCES = {k: [_DS, _MAN] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_DS, _WB, _SIM], 'sample.place': [_MATS, _NIST, _PC, _LIB, _SIM], 'sample.remove': [_SIM], 'events.read': [_MAN, _SIM],
                    'truth.inspect': [_SIM], 'ledger.read': [_SIM], 'fault.inject': [_MAN, _SIM], 'sim.step': [_SIM], 'door.set': [_DS, _MAN], 'rack.set': [_DS, _MAN]})
PARAM_ZH = {'ambient_k': '室温', 'pressure_pa': '大气压', 'mains_v': '电源电压', 'noise_seed': '噪声种子', 'relative_humidity': '室内相对湿度', 'on': '开或待机',
            'angle_rad': '门开角', 'temperature_k': '温度设定', 'delta_k': '设定增减', 'limit_k': '安全控制器设定', 'position': '风门开度0–4或搁架位置',
            'days': '天', 'hours': '时', 'minutes': '分', 'fan_after': '定时结束后风机', 'gradient_k_min': '斜坡梯度（°C/min）', 'target_k': '斜坡目标',
            'unit': '温度单位', 'inserted': 'U盘插入', 'interval_min': '记录间隔', 'since_s': '起始时间', 'level': '搁架层', 'present': '放入或取出',
            'sample_id': '样品编号', 'ware': '器皿（目录）', 'dry_mass_kg': '干固体质量', 'dry_cp_j_kgk': '干固体比热', 'liquid_id': '湿液体（模板库）',
            'liquid_mass_kg': '液体质量', 'initial_k': '样品初温', 'fault': '故障名', 'active': '注入或清除', 'dt_s': '推进时间'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.drying_oven_process.limitations。'
CAP_LIMITS = {
    'sample.place': ['器皿材料与尺寸来自目录（工程尺寸，材料物性有来源）；干固体只有质量与比热（不吸附、不分解）；湿液体为模板库中的单一纯液体；不建模溶剂可燃性与爆炸下限（手册：本机不用于可燃物）。'],
    'display.cycle': ['R4菜单的方向键/OK/返回导航被抽象为直接能力（设定、风门、安全、定时、斜坡、USB）；返回键在场景中切换文字行页面。'],
    'temperature.step': ['场景中上、下键各按一次=±1 K（菜单中按住连续变化未建模）。'],
    'usb.read': ['CSV内容按手册列出的量；日期时间以仿真时钟秒表示；文件名与语言格式未建模。'],
}
LIMITATIONS = [
    '所选配置：BINDER FD 56（FD056-230V，9010-0303），标准配置（2层镀铬搁架，无选件）；数据表2022版为主来源，手册E3.1（12/2017）补充控制器与安全装置细节。',
    '热模型：加热器、腔内空气、内胆（含搁架与风扇）、隔热层与外壳四个集总节点；内胆热容、内胆—外壳导热与开门换气各1项标定（15 min到150 °C、150 °C能耗290 W、开门30 s后4 min恢复）；'
    '其余传热系数为工程值；空间温差按数据表±1.7 K（150 °C）的形状场给出，不是CFD。',
    '控温：Pt100一阶滞后加噪声，PID加固态继电器周期为工程实现，复核数据表±0.3 K波动；BINDER R4的实际控制算法未公开。',
    '换气：风门5档按15°步进，全开为数据表80次/h（100 °C），中间档与关闭漏风为工程值；风机停止时只靠热浮力（工程倍数）；开门时加热与风机停止（手册）。',
    '1级过温保护的复位需要拔掉电源≥10 s：本资产中只有评测者的mains_outage故障模拟断电，代理的power.set只是待机，不能复位1级过温保护（拔插电源的机器人动作未建模）。',
    '安全：安全控制器读数取腔内空气温度（无偏差假设）；300 °C设定配300 °C安全设定（均为上限）时，控温波动可触发限温报警（手册建议安全设定比温度设定高2–5 °C，最高温度下无法满足）。'
    '2级安全控制器（限温器）超过设定后完全关闭加热并锁定，降到设定以下后按OK复位；1级过温保护330 °C切断，断电≥10 s且冷却到230 °C以下才复位；出厂安全设定未知，取最大300 °C（假设）。',
    '样品：器皿+干固体（堆积密度默认1500 kg/m³，可声明；固体与液体须装得下）+一种液体；恒速干燥段按湿表面传质（风机开/关两档系数），含湿量低于临界值（工程值0.15）后线性降速；沸点以上过剩热量使液体沸腾；腔内蒸气随换气排出（各蒸气不超过腔温下的饱和蒸气压，超出部分在内胆上冷凝、放出汽化热、冷凝液排走不再蒸发；总蒸气分压不超过大气压，超出部分经排气管挤出），'
    '水的背景按室内相对湿度；不建模冷凝液的再蒸发、吸湿、化学变化与可燃气氛。',
    'FD为固定风速；延时开启、温度相关延时关闭、风速设定与各选件为unsupported；R4菜单导航（方向键/OK/返回的逐级操作）被抽象为直接能力，场景中五个按键为弹簧键。',
    '显示文字按七段字形：设定“SP”、风门“FL CLOS/1/2/3/OPEn”、安全“SAFE”、定时“t dhhmm”、斜坡“r”、USB“Usb”、2级报警“ALAr tLI”（手册!TLim）、1级“ALAr OtP”、定时结束“t End”；'
    '大数字为实际温度（Pt100断线显示1999）。',
    '场景：箱体、门（双向关节）、三角控制面板（显示、5键）、内腔、2层搁架、风扇罩、排气管与后部风门执行器（随风门开度转动）有几何；样品没有几何（运行层状态）。',
]


def _sampling_text(k):
    if k == 'clock.time_s':
        return 'Host clock.'
    if SLOTS[k]['origin'] in ('sensor', 'derived', 'process_truth'):
        return 'Updated on the 0.1 s grid of the host steps (display values as the controller shows them).'
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
        """Contract parameters in SI: degC -> K, min -> s, deg -> rad, in -> m (the source value is noted in the meaning)."""
        conv = {'degC': ('K', lambda x: x + C0, '_c', '_k'), 'min': ('s', lambda x: x * 60.0, '_min', '_s'), 'deg': ('rad', math.radians, '_deg', '_rad'),
                'K/min': ('K/s', lambda x: x / 60.0, '', '_k_s'), '1/h': ('1/s', lambda x: x / 3600.0, '_per_h', '_per_s')}
        if unit not in conv:
            return name, val, unit, ''
        u, f, a, b = conv[unit]
        val2 = ({kk: (f(x) if isinstance(x, (int, float)) else x) for kk, x in val.items()} if isinstance(val, dict) else
                [f(x) if isinstance(x, (int, float)) else x for x in val] if isinstance(val, list) else f(val))
        return (name[:-len(a)] + b if a and name.endswith(a) else name + b), val2, u, f'（换算为SI；资料原值{val} {unit}）'

    def doc(name, val, unit, meaning, src):
        name, val, unit, extra = si(name, val, unit)
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning + extra, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        name, val, unit, extra = si(name, val, unit)
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning + extra, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_datasheet': _DS, 'manufacturer_manual_2017': _MAN}
    WB_KEYS = ('temperature_range_above_ambient_k', 'temperature_max_c', 'temperature_variation_150_k', 'temperature_fluctuation_150_k', 'heating_up_150_s',
               'rated_voltage_v', 'nominal_power_w', 'interior_volume_m3', 'housing_dimensions_m', 'net_weight_kg')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
    for k in sorted(ENG):
        ev = instrument()['engineering'][k]['evidence']
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '已标定（calibrate_oven.py），模型自洽，不是实测' if ev == 'calibrated' else
            '由厂家图纸或数据推得' if ev in ('drawing', 'documented_derived') else '工程值（见instrument_model.json的engineering说明），不是实测')
    for k, v in instrument()['derived'].items():
        asm(k, v['value'], v['unit'], '由厂家数据推导：' + v['note_zh'], '推导值', (_DS, _MAN))
    lt = _lib('loads')
    asm('load_templates', dict(file='assets/expansion_batch05/t4_measure/binder_fd056/load_templates.json', counts=lt['counts']), '1',
        '装载模板：器皿材料（厂家数据表、NIST、PubChem）与湿液体（CHEM-008液体模板库，NIST/PubChem）；每条带来源', '实测文献数据；适用规则见模板库rules_zh',
        (_MATS, _NIST, _PC, _LIB))
    asm('gas_constant_j_mol_k', R_GAS, 'J/(mol K)', '气体常数', '物理常数', (_SIM,))
    asm('air_cp_j_kgk', CP_AIR, 'J/(kg K)', '空气定压比热（25–150 °C平均）', '物理常数（工程取值）', (_SIM,))
    return P


SLOT_REFERENCE = {'display.temperature': '显示的实际腔温（控制传感器位置，°C或°F整数）'}


def export_binder_fd056_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'BinderFd056Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.binder_fd056.BinderFd056Session.invoke',
                                              invocation=f'session.invoke(instance_id="binder_fd056_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='BinderFd056Runtime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（温度与样品在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['housing', 'door', 'panel', 'liner', 'fan_cover', 'exhaust_duct', 'flap_actuator', 'display'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='强制对流干燥：加热器加热循环空气，风机把热空气吹过搁架上的器皿与样品；排气风门调节新风换气；R4控制器用Pt100按设定（可带斜坡）PID控温；'
                   '2级安全控制器与1级过温保护切断加热；开门时加热与风机停止；样品中的液体按湿表面传质蒸发，蒸气随换气排出',
        equations=['C_h dT_h/dt = P_heat - G_ha (T_h - T_a) - G_hl (T_h - T_l);  C_a dT_a/dt = G_ha (T_h - T_a) + P_fan - G_la (T_a - T_l) - m_dot c_p (T_a - T_amb) - sum h A_i (T_a + dT_i - T_i)',
                   'C_l dT_l/dt = G_la (T_a - T_l) + G_hl (T_h - T_l) - G_ls (T_l - T_s) - G_door (T_l - T_amb);  C_s dT_s/dt = G_ls (T_l - T_s) - G_sa (T_s - T_amb)',
                   'm_dot = n(flap) / 3600 V_steam rho_air(T_a) (x f_nofan without fan);  door open: m_dot = k_door V rho',
                   'C_i dT_i/dt = h A_i (T_a + dT_i - T_i) - m_evap h_vap;  m_evap = k_m A_open (p_vap(T_i) - p_partial) M / (R T_i) min(1, X / X_crit)',
                   'dT_i = 1.7 K x shape(level, position) x (T_a - T_amb) / 128 K (x 1.6 flap open, x 4 fan off)',
                   'duty = clamp((Kp e + Ki int e + Kd de/dt) / P_heater, 0, 1);  heater on for duty x SSR period;  heating off when T_safety > limit (latched)'],
        parameters=_params(), source_ids=[_SIM, _WB, _DS, _MAN, _MATS, _NIST, _PC, _LIB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_binder_fd056_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('binder_fd056', caps, slots, {'drying_oven_process': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'sample.place': (SAMPLE, SAMPLE_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'drying_oven_process': mechanism})
