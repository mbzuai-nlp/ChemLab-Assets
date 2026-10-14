"""CHEM-184 New Era Pump Systems NE-1000 Multi-Phaser programmable single syringe pump: process runtime.

Device (user manual Publication #1200-01, firmware V3.923, 04/06/16; dimension drawing Rev A 5/8/08): one syringe up to 60 mL (140 mL partially
filled), step motor (400 steps/rev, 1/8-1/2 microstepping, 15/28 to a 20 rev/inch drive screw: 0.2126-0.8504 um per step), plunger speed
0.004205 cm/hr - 5.1005 cm/min (0.73 uL/hr with a B-D 1 mL to 1699 mL/hr with a B-D 60 mL), syringe inside diameter 0.100-50.00 mm, maximum
force 35 lbf at minimum and 18 lbf at maximum speed, stall detection (motor stopped, program paused, alarm), guide rod collar clamp, 41-phase
pumping program (RATE, FILL, INCR, DECR, STOP, JP, LP:ST / LP:nn / LP:EN, PS:nn, IF, Et / ES / Et:rS, CLR.D, BEEP, OUT), purge at top speed,
volumes infused and withdrawn accumulated separately (computed from the diameter setting), 4-digit LCD with 8 LEDs, 9 keys (4 arrows, Rate,
Volume, Diameter / Setup, direction, Start / Stop), RS-232 (Basic protocol command set), TTL I/O (operational trigger, direction, event, program
input, user output, motor-operating output), setup (power failure restart, low noise, alarm, keypad lockout, beep), 12 V DC 800 mA, 1.63 kg.

Process model (fixed 0.1 s grid; switched off or stopped with no pressure to relax is skipped exactly; SI units: m, m3, m3/s, Pa, N, s):
  * The motor runs the plunger at v = Q_set / A_set (A_set from the diameter setting); the true displaced flow is A_true v (the true inside
    diameter of the loaded syringe may differ - evaluator) and the computed 'volume dispensed' is A_set x travel.
  * Fluid path: syringe + liquid + tubing + needle; C dp/dt = A_true v - (p - p_back) / R with R = sum 8 mu L / (pi r^4) (Hagen-Poiseuille) and
    C = C_syringe + C_tubing (pi r^2 L 2r / (E t)) + V_liquid / K + V_air / p_abs; integrated exactly over each tick (linear ODE); withdrawal pulls
    liquid from the reservoir the outlet sits in, limited by cavitation.
  * Force F = p A_true + F_friction; the pump stalls when F exceeds the speed-dependent maximum (35 lbf at minimum, 18 lbf at maximum speed,
    interpolated in log speed) for the debounce time, at the collar clamp and at the ends of travel.
  * Ledgers: liquid volume - in the syringe + delivered to the outlet - drawn from the reservoir = placed; the computed volume dispensed against
    the true delivered volume (diameter and compliance error)."""
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.newera_ne1000"
CATALOG_ID = "CHEM-184"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch05/t5_automation/newera_ne1000'
LIBS = {'instrument': 'instrument_model.json', 'syringes': 'syringe_templates.json'}
DT = 0.1
MAX_EVENTS_LOG = 3000
IN = 0.0254


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
SYR = _lib('syringes')['syringes']
LIQ = _lib('syringes')['liquids']
LIQ_IDS = sorted(LIQ)
TUBING = ENG['tubing']
NEEDLES = ENG['needles']
V_MAX = DOC['max_speed_m_s']
V_MIN = DOC['min_speed_m_s']
TRAVEL = DOC['max_travel_m']['without_collar']
COLLAR_PARK = DOC['max_travel_m']['with_collar']                          # the collar stays on the rod (manual 5: loosen, do not remove); parked against the holder block
X_ZERO = COLLAR_PARK - 0.002                                              # the plunger bottoms 2 mm before the parked collar (engineering)
ZERO_GAP = TRAVEL - X_ZERO
D_RANGE = DOC['diameter_range_m']
FUNCS = ['RAT', 'FIL', 'INC', 'DEC', 'STP', 'JMP', 'LPS', 'LOP', 'LPE', 'PAS', 'IF', 'EVN', 'EVS', 'EVR', 'CLD', 'BEP', 'OUT']
RATE_FUNCS = ('RAT', 'FIL', 'INC', 'DEC')
UNIT_FACTOR = {'UM': 1e-9 / 60.0, 'MM': 1e-6 / 60.0, 'UH': 1e-9 / 3600.0, 'MH': 1e-6 / 3600.0}
KEYS = ['up1', 'up2', 'up3', 'up4', 'rate', 'volume', 'diameter', 'direction', 'start']
_M64 = (1 << 64) - 1


def _mix64(x):
    x = (x + 0x9E3779B97F4A7C15) & _M64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _M64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _M64
    return x ^ (x >> 31)


def _uni(seed, k, ch):
    return ((_mix64(_mix64(_mix64(seed & _M64) ^ (k & _M64)) ^ ch) >> 11) + 0.5) / 9007199254740992.0


def p_vap(sid, t_k):
    v = LIQ[sid]['vapour_pressure']
    v = v.get('runtime_model', v)
    if v['kind'] == 'antoine':
        return 1e5 * 10.0 ** (v['A'] - v['B_k'] / (t_k + v['C_k']))
    return v['p_ref_pa'] * math.exp(-v['dh_vap_j_mol'] / 8.314462618 * (1.0 / t_k - 1.0 / v['t_ref_k']))


def max_force(v):
    """Maximum drive force (N) at plunger speed v: 35 lbf at the minimum, 18 lbf at the maximum speed, linear in log10(speed) (engineering)."""
    f = DOC['max_force_n']
    v = min(max(abs(v), V_MIN), V_MAX)
    s = (math.log10(v) - math.log10(V_MIN)) / (math.log10(V_MAX) - math.log10(V_MIN))
    return f['at_min_speed'] + (f['at_max_speed'] - f['at_min_speed']) * s


def fmt4(x):
    """Four LCD digits with the decimal point placed by magnitude (as the pump shows rates, volumes and diameters)."""
    x = abs(x)
    for dec in (3, 2, 1, 0):
        s = f'{x:.{dec}f}'
        if len(s.replace('.', '')) <= 4:
            return s
    return 'EEEE'


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
        if 'enum' in s and v not in s['enum']:
            raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


RATE = number('m3/s', 0.0, 1.0e-3)
PHASE = obj(dict(function=enum(FUNCS), rate_m3_s=RATE, rate_units=enum(sorted(UNIT_FACTOR)), volume_m3=number('m3', 0.0, 1.0e-3), direction=enum(['INF', 'WDR']),
                 parameter=integer(0, 99), tenths=BOOL), optional=('rate_m3_s', 'rate_units', 'volume_m3', 'direction', 'parameter', 'tenths'))
PLANT_EXAMPLE = dict(ambient_k=295.15, pressure_pa=101325.0, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 278.15, 313.15), pressure_pa=number('Pa', 70000.0, 110000.0), noise_seed=integer(0, 2 ** 31 - 1)))
LOAD = obj(dict(syringe=enum(sorted(SYR)), liquid_id=enum(LIQ_IDS), fill_volume_m3=number('m3', 0.0, 2.0e-4), air_volume_m3=number('m3', 0.0, 5.0e-5)),
           optional=('air_volume_m3',))
OUTLET = obj(dict(tubing=nullable(enum(sorted(TUBING))), tubing_length_m=number('m', 0.0, 20.0), needle=enum(sorted(NEEDLES)), backpressure_pa=number('Pa', -5.0e4, 2.0e7),
                  reservoir_liquid_m3=number('m3', 0.0, 1.0)), optional=('tubing', 'tubing_length_m', 'needle', 'backpressure_pa', 'reservoir_liquid_m3'))
FAULTS = ['mains_outage', 'occlusion', 'motor_failure']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['chassis'], '声明环境：室温、大气压与噪声种子（之后可改环境，种子不可改）', 'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['power_switch'], '后部电源开关（按“1”开）；开机清零已排体积（手册7.6 F）')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['power_switch'], '后部电源开关拨一下（切换开/关）')
cap('keypad.press', obj(dict(key=enum(KEYS))), obj(dict(display=TEXT, mode=TEXT)), dict(key='rate'), ['keypad_membrane'],
    '按面板键：4个方向键（各位数字加1）、Rate、Volume、Diameter、方向键、Start/Stop（与手册第6章一致的键盘逻辑）')
cap('syringe.set_diameter', obj(dict(diameter_m=number('m', D_RANGE[0], D_RANGE[1]))), obj(dict(diameter_m=number('m'))), dict(diameter_m=0.02659), ['keypad_membrane'],
    '设定注射器内径（0.100–50.00 mm；只能在程序停止时；改内径清零已排体积并按内径设体积单位）', 'agent', 'configuration')
cap('rate.set', obj(dict(rate_m3_s=RATE, units=enum(sorted(UNIT_FACTOR)), continue_paused=BOOL), optional=('units', 'continue_paused')), obj(dict(rate_m3_s=RATE, units=TEXT)),
    dict(rate_m3_s=100e-6 / 3600.0, units='MH'), ['keypad_membrane'], '设定当前阶段的流量（RAT；推进速度须在最低与最高速度之间；运行中可改，单位不能改）', 'agent', 'configuration')
cap('volume.set', obj(dict(volume_m3=number('m3', 0.0, 1.0e-3))), obj(dict(volume_m3=number('m3'))), dict(volume_m3=5e-6), ['keypad_membrane'],
    '设定当前阶段的“待排体积”（0为连续；程序停止时）', 'agent', 'configuration')
cap('direction.set', obj(dict(direction=enum(['INF', 'WDR', 'REV']))), obj(dict(direction=TEXT)), dict(direction='INF'), ['key_direction'],
    '设定方向（注入/抽取/反向；运行中只有连续泵送时可改）')
cap('phase.select', obj(dict(phase=integer(1, 41))), obj(dict(phase=integer(1, 41))), dict(phase=1), ['keypad_membrane'], '选择当前程序阶段（PHN；程序停止时）', 'agent', 'configuration')
cap('phase.set_function', PHASE, obj(dict(phase=integer(1, 41), definition=FREE_OBJ)), dict(function='RAT', rate_m3_s=50e-6 / 3600.0, rate_units='MH', volume_m3=1e-6, direction='INF'),
    ['keypad_membrane'], '设定当前阶段的功能与数据（FUN/RAT/VOL/DIR；程序停止时）', 'agent', 'configuration')
cap('program.clear', obj({}), obj(dict(phases=integer(0, 41))), {}, ['keypad_membrane'], '清除程序（开机按最右方向键的复位；程序停止时）', 'agent', 'configuration')
cap('pump.run', obj(dict(phase=integer(1, 41)), optional=('phase',)), obj(dict(state=TEXT)), {}, ['key_start'], '启动程序（RUN；暂停时从暂停处继续；可指定阶段）')
cap('pump.stop', obj({}), obj(dict(state=TEXT)), {}, ['key_start'], '停止（STP：运行中→暂停；暂停中→复位到第1阶段）')
cap('pump.purge', obj(dict(on=BOOL)), obj(dict(state=TEXT)), dict(on=True), ['key_start'], '清洗：按住Start/Stop 1 s后最高速泵送，松开停止（PUR/STP）')
cap('volume.clear', obj(dict(direction=enum(['INF', 'WDR', 'BOTH']))), obj(dict(dispensed=FREE_OBJ)), dict(direction='BOTH'), ['keypad_membrane'], '清零已排体积（CLD；程序停止时）')
cap('setup.configure', obj(dict(power_failure=BOOL, low_noise=BOOL, alarm=BOOL, beep=BOOL, keypad_lockout=BOOL, trigger=enum(['FT', 'FH', 'F2', 'LE', 'ST', 'T2', 'SP', 'P2', 'RL', 'RH', 'SL', 'SH', 'OF']),
                                direction_input=integer(0, 1), motor_output=integer(0, 1)),
                           optional=('power_failure', 'low_noise', 'alarm', 'beep', 'keypad_lockout', 'trigger', 'direction_input', 'motor_output')),
    obj(dict(setup=FREE_OBJ)), dict(power_failure=True, alarm=True), ['keypad_membrane'], '设置：断电重启、低噪声、报警蜂鸣、键盘锁、提示音、TTL触发方式、方向输入、电机TTL输出', 'agent', 'configuration')
cap('serial.send', obj(dict(command=TEXT)), obj(dict(response=TEXT)), dict(command='DIS'), ['rs232_to_computer'], '经RS-232发送命令（Basic协议；回应为地址+状态+数据）')
cap('ttl.set', obj(dict(pin=integer(2, 6), level=integer(0, 1))), obj(dict(inputs=FREE_OBJ)), dict(pin=2, level=0), ['ttl_io'],
    'TTL I/O输入电平（2运行触发、3方向、4事件、6程序输入；外部设备驱动；须保持≥100 ms）', 'agent', 'robot_skill')
cap('syringe.load', LOAD, obj(dict(syringe=FREE_OBJ)), dict(syringe='bd_60ml', liquid_id='water', fill_volume_m3=50e-6), ['holder_block', 'pusher_block', 'syringe_barrel', 'plunger'],
    '装注射器（目录）：按液量把推块滑到活塞法兰（按下驱动螺母键）、夹紧筒身（程序停止时；超过行程的注射器只能部分装液）', 'agent', 'robot_skill')
cap('syringe.unload', obj({}), obj(dict(syringe=FREE_OBJ)), {}, ['holder_block', 'syringe_barrel'], '取下注射器：返回剩余液量与气泡', 'agent', 'robot_skill')
cap('outlet.connect', OUTLET, obj(dict(outlet=FREE_OBJ)), dict(tubing='ptfe_0p8mm', tubing_length_m=0.5, needle='none', backpressure_pa=0.0, reservoir_liquid_m3=0.0), ['holder_block', 'syringe_barrel'],
    '连接出口：管路（目录、长度）、针头、下游背压（表压）、出口所在储液（抽取用）', 'agent', 'robot_skill')
cap('collar.set', obj(dict(position_m=nullable(number('m', 0.0, COLLAR_PARK)))), obj(dict(position_m=nullable(number('m')))), dict(position_m=0.10), ['guide_collar'],
    '导杆限位夹：限制注入时推块行程（推块碰到即失速报警；None为移开）', 'agent', 'robot_skill')
cap('events.read', obj(dict(since_s=number('s', 0.0)), optional=('since_s',)), obj(dict(events=array(FREE_OBJ), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), ['chassis'],
    '事件与报警记录', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(process=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, ['chassis'], '评测者：压力、真实流量、推力、注射器内液量、已送出液量', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, ['chassis'], '评测者：液量守恒账', 'evaluator', 'query')
cap('medium.configure', obj(dict(syringe_id_error=number('1', -0.05, 0.05), backpressure_pa=number('Pa', -5.0e4, 2.0e7), clear_backpressure=BOOL),
                           optional=('syringe_id_error', 'backpressure_pa', 'clear_backpressure')),
    obj(dict(medium=FREE_OBJ)), dict(syringe_id_error=0.01), ['holder_block', 'syringe_barrel'], '评测者：注射器真实内径相对偏差、下游背压变化', 'evaluator', 'simulation')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='occlusion', active=True), ['chassis'],
    '评测者：注入或清除故障（停电、出口堵塞、电机失效）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), ['chassis'], '推进宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'program.subprogram': '子程序选择Pr:In / Pr:nn：未建模（用RUN <phase>指定起始阶段代替）。',
    'serial.safe_mode': 'RS-232 Safe协议（CRC、超时报警）：未建模，只实现Basic协议命令集。',
    'network.address': 'RS-232泵网络地址与多泵往复/双泵模式（*ADR、DUAL、RECP）：单台泵，未建模。',
    'pressure.measure': '压力测量：本型号没有压力传感器（失速只按推力判断）。',
    'ttl.override_trigger': 'TTL运行触发的程序内覆盖（FUN TRG / tr:aa，含Et/Bt事件陷阱重定向，手册9.3.18与例9）：未建模，只用setup.configure的默认触发方式。',
    'direction.sticky': '“粘滞方向”（DIR STK，Withdraw LED闪烁）：未建模。',
    'program.entry_lockout': '程序录入模式锁定（LOC P / P-L:n，手册6.13.4）：键盘的程序录入模式本身被抽象为phase.*能力，未建模此锁定。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['chassis'], _v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'chassis', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'chassis', '环境是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'power_switch', '电源开关是否在“1”')
slot('syringe.diameter_m', number('m'), 'm', 'setpoint', 'keypad_membrane', '设定的注射器内径')
slot('program.state', FREE_OBJ, '1', 'setpoint', 'keypad_membrane', '程序：各阶段定义（功能、流量、单位、体积、方向、参数）与当前选中阶段')
slot('run.state', enum(['stopped', 'running', 'paused', 'purging', 'pause_timer', 'user_wait']), '1', 'derived', 'key_start', '运行状态：停止、运行、暂停、清洗、定时暂停、等待触发')
slot('run.phase', integer(1, 41), '1', 'derived', 'keypad_membrane', '正在执行的阶段号')
slot('pump.rate_m3_s', number('m3/s'), 'm3/s', 'derived', 'keypad_membrane', '当前泵送流量（按设定内径；不泵送时为0）')
slot('pump.direction', enum(['INF', 'WDR']), '1', 'derived', 'key_direction', '当前方向')
slot('volume.dispensed', FREE_OBJ, '1', 'derived', 'keypad_membrane', '已排体积（按设定内径计算）：注入与抽取分别累计，m³')
slot('alarm.state', nullable(enum(['stall', 'program_error', 'out_of_range'])), '1', 'derived', 'lcd', '报警：失速、程序错误、超出范围（开机复位只在RS-232的第一个回应A?R中报告）')
slot('pusher.position_m', number('m', 0.0, TRAVEL), 'm', 'derived', 'pusher_block', '推块位置（离末端板的行程，0–4.74 in；推块关节=该值）')
slot('collar.position_m', nullable(number('m')), 'm', 'setpoint', 'guide_collar', '限位夹位置（推块右端面碰到它的行程值；None为推到注射器夹块旁停放，行程4.46 in）')
slot('syringe.state', nullable(FREE_OBJ), '1', 'setpoint', 'holder_block', '已装注射器：型号、液体（看得到的刻度液量见truth）')
slot('outlet.state', FREE_OBJ, '1', 'setpoint', 'holder_block', '出口：管路、长度、针头、背压')
slot('setup.state', FREE_OBJ, '1', 'setpoint', 'keypad_membrane', '设置：断电重启、低噪声、报警、键盘锁、提示音、TTL触发方式等')
slot('ttl.state', FREE_OBJ, '1', 'derived', 'ttl_io', 'TTL输入电平与输出（5用户输出、7电机运行）')
slot('display.text', TEXT, '1', 'derived', 'lcd', 'LCD四位文字（含小数点；手册6.14/6.15字形：StAL失速、oor超出范围、or:nn/Er:nn阶段越界/程序错误（冒号不计）、----不适用、LoC-键盘锁、PAUS等待触发、 oFF连续、PurG清洗、PS:nn定时暂停）')
slot('display.lcd_cells', TEXT, '1', 'derived', 'lcd', 'LCD各位七段字形（display.text中七段码与“6”相同的G写作6；场景读数用）')
for _k, _zh in (('mm', 'mm单位LED'), ('ml', 'mL单位LED'), ('ul', 'µL单位LED'), ('min', 'min单位LED'), ('hr', 'hr单位LED'), ('dispensed', 'Dispensed LED'),
                ('withdraw', 'Withdraw LED（抽取方向）'), ('pumping', 'Pumping LED（电机运转）'), ('rs232', 'RS-232接收三角')):
    slot(f'indicator.{_k}', BOOL, '1', 'derived', 'rs232_icon' if _k == 'rs232' else f'led_{_k}', _zh + '是否点亮')
slot('truth.process', FREE_OBJ, '1', 'process_truth', 'syringe_barrel', '压力、真实流量、推力与最大推力、注射器内液量与气泡、送出与抽入液量、析气', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'chassis', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'pusher.position_m', 'collar.position_m', 'display.lcd_cells'} | {f'indicator.{k}' for k in ('mm', 'ml', 'ul', 'min', 'hr', 'dispensed', 'withdraw', 'pumping', 'rs232')}
slot('collar.joint_m', number('m', 0.0, COLLAR_PARK), 'm', 'derived', 'guide_collar', '限位夹关节位置（停放时在4.46 in，紧靠注射器夹块）')
SCENE_MIRRORED = (SCENE_MIRRORED - {'collar.position_m'}) | {'collar.joint_m'}


def _cells(text):
    """Four LCD digit cells and the position of the decimal point (after cell k) of a displayed number."""
    cells, dp = [], None
    for ch in text:
        if ch == '.':
            dp = len(cells) - 1
        else:
            cells.append(ch)
    pad = 4 - len(cells)
    cells = [' '] * pad + cells[-4:]
    return [c if c.isdigit() else '0' for c in cells], (dp + pad if dp is not None else None)


def _text(cells, dp):
    s = ''.join(cells)
    return s if dp is None or dp >= 3 else s[:dp + 1] + '.' + s[dp + 1:]


def _empty_phase():
    return None


def _default_phase():
    return dict(function='RAT', rate_m3_s=0.0, rate_units='MH', volume_m3=0.0, direction='INF', parameter=0, tenths=False)


# ---------------------------------------------------------------- runtime
class NeweraNe1000Runtime:
    def __init__(self, instance_id='newera_ne1000_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.power = False
        self.diameter = None                                                 # must be set before the program can operate (manual 3)
        self.vol_units_override = None
        self.phases = [_default_phase()] + [_empty_phase() for _ in range(40)]
        self.sel = 1
        self.run = dict(state='stopped', idx=1, phase_start=0.0, timer=0.0, loops=[], trap=None, rate=None, direction='INF', purge=False, was_running=False)
        self.dispensed = {'INF': 0.0, 'WDR': 0.0}
        self.alarm = None
        self.alarm_phase = None
        self.setup = dict(power_failure=False, low_noise=False, alarm=True, beep=True, keypad_lockout=False, trigger='FT', direction_input=0, motor_output=0)
        self.ttl_in = {2: 1, 3: 1, 4: 1, 6: 1}                                # inputs pulled up (logic high) when unconnected
        self.ttl_pending = {}
        self.ttl_out = {5: 0, 7: 0}
        self.ui = dict(mode='rate', edit=None, edit_t=None, message=None, units_edit=None, units_t=None)
        self.rs232_seen = False
        self.buzzer = False
        self.x = 0.0                                                         # pusher block travel from the end plate (m)
        self.collar = None
        self.syringe = None
        self.outlet = dict(tubing=None, tubing_length_m=0.0, needle='none', backpressure_pa=0.0, reservoir_liquid_m3=0.0)
        self.id_error = 0.0
        self.bp_hidden = None                                                 # evaluator-set back pressure (not visible in outlet.state)
        self.p = 0.0                                                          # gauge pressure in the syringe (Pa)
        self.vs = 0.0                                                         # volume stored by compliance, C p (m3)
        self.force = 0.0
        self.over_force_s = 0.0
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
        return NeweraNe1000Session(self, name)

    def _event(self, kind, code, message):
        self.events.append(dict(time_s=round(self.t, 6), kind=kind, code=code, message_zh=message))
        if len(self.events) > MAX_EVENTS_LOG:
            self.events_dropped += len(self.events) - MAX_EVENTS_LOG
            del self.events[:-MAX_EVENTS_LOG]

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _on(self):
        return self.plant is not None and self.power and 'mains_outage' not in self.faults

    def _need_on(self):
        self._need_plant()
        if not self._on():
            raise ActionRejected('POWER_OFF')

    def _operating(self):
        return self.run['state'] in ('running', 'purging', 'pause_timer', 'user_wait')

    def _need_stopped(self):
        if self._operating():
            raise ActionRejected('PROGRAM_OPERATING')

    def _need_no_alarm(self):
        if self.alarm is not None:
            raise ActionRejected('ALARM_ACTIVE')

    # ---- geometry and fluidics
    def _area_set(self):
        return math.pi * self.diameter ** 2 / 4.0 if self.diameter else None

    def _area_true(self):
        s = SYR[self.syringe['type']]
        return math.pi * (s['inside_diameter_m'] * (1.0 + self.id_error)) ** 2 / 4.0

    def _vol_units(self):
        if self.vol_units_override:
            return self.vol_units_override
        return 'UL' if (self.diameter or 0.0) <= 14.0e-3 + 1e-12 else 'ML'

    def _resistance(self):
        if 'occlusion' in self.faults:
            return 1e22
        o = self.outlet
        mu = LIQ[self.syringe['liquid_id']]['viscosity']['value'] if self.syringe else 1e-3
        r_total = 8.0 * mu * 0.010 / (math.pi * (1.0e-3) ** 4)               # luer tip: 2 mm bore, 10 mm long (engineering)
        if o['tubing'] and o['tubing_length_m'] > 0.0:
            r = TUBING[o['tubing']]['id_m'] / 2.0
            r_total += 8.0 * mu * o['tubing_length_m'] / (math.pi * r ** 4)
        n = NEEDLES[o['needle']]
        if n['id_m']:
            r_total += 8.0 * mu * n['length_m'] / (math.pi * (n['id_m'] / 2.0) ** 4)
        return r_total

    def _compliance_lin(self):
        """Linear part of the syringe-line compliance (barrel / plunger seal, tubing wall, liquid compressibility); air is handled by the gas law."""
        s = SYR[self.syringe['type']]
        c = ENG['syringe_compliance_m3_pa'][s['material']] * s['nominal_m3']
        o = self.outlet
        if o['tubing'] and o['tubing_length_m'] > 0.0:
            tb = TUBING[o['tubing']]
            r, t = tb['id_m'] / 2.0, (tb['od_m'] - tb['id_m']) / 2.0
            c += math.pi * r * r * o['tubing_length_m'] * 2.0 * r / (tb['modulus_pa'] * t)
            c += math.pi * r * r * o['tubing_length_m'] / ENG['liquid_bulk_modulus_pa']
        c += max(self.syringe['liquid_m3'], 0.0) / ENG['liquid_bulk_modulus_pa']
        return max(c, 1e-18)

    def _compliance(self):
        """Total compliance at the current pressure: linear part + isothermal air dV/dp = V_air / p_abs."""
        return self._compliance_lin() + self._air_volume() / max(self.plant['pressure_pa'] + self.p, 1.0)

    def _air_volume(self, p=None):
        if not self.syringe or self.syringe['air_m3_atm'] <= 0.0:
            return 0.0
        p = self.p if p is None else p
        return self.syringe['air_m3_atm'] * self.plant['pressure_pa'] / max(self.plant['pressure_pa'] + p, 1.0)

    # ---- capabilities
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read'):
            self.revision += 1
            self._outputs()
        return out

    def _outputs(self):
        """Outputs that follow the motor state at once (not only on the next tick): TTL pin 7 (motor operating; ROM 1 also during a pause timer,
        manual 8.6) and the plunger force (none while the motor stands)."""
        moving = self._on() and self._motor_speed() != 0.0
        self.ttl_out[7] = 1 if (moving or (self._on() and self.setup['motor_output'] == 1 and self.run['state'] == 'pause_timer')) else 0
        if not moving:                                                        # a standing drive carries the pressure load only
            self.force = self.p * self._area_true() if self.syringe is not None else 0.0
            self.over_force_s = 0.0

    def _cmd_plant_configure(self, ambient_k, pressure_pa, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(ambient_k=ambient_k, pressure_pa=pressure_pa)
        self.seed = noise_seed
        if first:
            self.led = dict(placed_m3=0.0, delivered_m3=0.0, drawn_m3=0.0, removed_m3=0.0, air_in_m3=0.0)
        self._event('system', 'PLANT_CONFIGURED', '环境已声明（接上12 V电源适配器）')
        return dict(configured=True)

    def _cmd_power_set(self, on):
        self._need_plant()
        if on != self.power:
            self.power = on
            if self._on():
                self._power_up()
            else:
                self._power_down()
        return dict(on=self.power)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(not self.power)

    def _power_up(self):
        self.dispensed = {'INF': 0.0, 'WDR': 0.0}                            # manual 7.6 F: cleared when the pump is powered on
        self.rs232_seen = False
        self.ui.update(mode='rate', edit=None, edit_t=None, message=None, units_edit=None, units_t=None)
        self.alarm = None                                                     # alarms do not survive a power cycle (the power-up reset is reported instead)
        self.over_force_s = 0.0
        restart = self.run.get('was_running') and self.setup['power_failure']
        self.run.update(state='stopped', idx=1, loops=[], trap=None, rate=None, purge=False, timer=0.0)
        self._power_reset_alarm = True                                       # RS-232 status 'A?R' until read
        self._event('system', 'POWER_ON', '开机' + ('（断电模式：重新启动程序）' if restart else ''))
        if restart and self.diameter:
            self._start_program(1)

    def _power_down(self):
        self.run['was_running'] = self._operating()
        self.run.update(state='stopped', purge=False)
        self.ttl_out[7] = 0
        self.over_force_s = 0.0
        self._event('system', 'POWER_OFF', '断电/关机：电机停止')

    # ---- settings
    def _cmd_syringe_set_diameter(self, diameter_m):
        self._need_on()
        self._need_stopped()
        self._need_no_alarm()
        new = round(diameter_m, 6)
        self._cancel_pause()
        if new != self.diameter:
            self.dispensed = {'INF': 0.0, 'WDR': 0.0}                        # manual 7.6 C: cleared when the diameter is changed
        self.diameter = new
        return dict(diameter_m=self.diameter)

    def _cur_phase(self):
        ph = self.phases[self.sel - 1]
        if ph is None:
            ph = self.phases[self.sel - 1] = _default_phase()
        return ph

    def _check_rate(self, rate):
        a = self._area_set()
        if a is None:
            raise ActionRejected('DIAMETER_NOT_SET')
        if rate != 0.0 and not (V_MIN * (1 - 1e-9) <= rate / a <= V_MAX * (1 + 1e-9)):
            raise ActionRejected('OUT_OF_RANGE')

    def _peek_phase(self):
        """The selected phase without creating it (reads must not change the program): an empty phase reads as a default RATE phase."""
        return self.phases[self.sel - 1] or _default_phase()

    def _shown_phase(self, create=False):
        """The phase whose rate the pump shows and changes: the executing (or paused) phase while the program is under way, else the selected phase."""
        if self.run['state'] in ('running', 'pause_timer', 'user_wait', 'paused') and 1 <= self.run['idx'] <= 41 and self.phases[self.run['idx'] - 1] is not None:
            return self.phases[self.run['idx'] - 1]
        return self._cur_phase() if create else self._peek_phase()

    def _cmd_rate_set(self, rate_m3_s, units=None, continue_paused=False, store=True):
        self._need_on()
        self._need_no_alarm()
        paused = self.run['state'] == 'paused'
        ph = self._peek_phase() if paused and not continue_paused else self._shown_phase()
        if ph['function'] not in (('RAT',) if paused and continue_paused else RATE_FUNCS):
            raise ActionRejected('NOT_APPLICABLE')                            # RAT C changes only a paused RATE phase
        self._check_rate(rate_m3_s)
        ph = self._shown_phase(create=True)
        if self.run['state'] == 'purging':                                    # pumping: no units change (6.6.1); a rate of 0.0 stops the pump (6.6)
            if units is not None and units != ph['rate_units']:
                raise ActionRejected('UNITS_WHILE_PUMPING')
            if rate_m3_s == 0.0:
                self._trigger_stop()
                self._event('operation', 'STOPPED_ZERO_RATE', '流量设为0：清洗停止')
        if self._operating() and self.run['state'] != 'purging':              # a purge is not a program: a rate is stored in the selected phase
            if units is not None and units != ph['rate_units']:
                raise ActionRejected('UNITS_WHILE_PUMPING')
            nxt = self.phases[self.run['idx']] if self.run['idx'] < 41 else None
            if ph['function'] != 'RAT' or (nxt and nxt['function'] in ('INC', 'DEC')):
                raise ActionRejected('NOT_APPLICABLE')
            if rate_m3_s == 0.0:                                              # manual 6.6: a pumping rate of 0.0 stops the pump
                self._reset_program()
                self._event('operation', 'STOPPED_ZERO_RATE', '流量设为0：泵停止（程序回到第1阶段）')
            else:
                self.run['rate'] = rate_m3_s
            if not store:                                                     # manual 6.1: a rate changed over RS-232 while operating is not stored
                return dict(rate_m3_s=float(rate_m3_s), units=ph['rate_units'])
        elif paused and (not continue_paused or rate_m3_s == 0.0):
            self._cancel_pause()                                              # manual 7.7: a change while paused restarts from Phase 1 (a rate of 0 stops)
            ph = self._cur_phase()                                            # ... and is stored in the selected phase
        elif paused:                                                          # RAT C: the pause is kept and the paused RATE phase resumes at the new rate
            self.run['rate'] = float(rate_m3_s)
        ph['rate_m3_s'] = float(rate_m3_s)
        if units is not None:
            ph['rate_units'] = units
        return dict(rate_m3_s=ph['rate_m3_s'], units=ph['rate_units'])

    def _cancel_pause(self):
        """Manual 7.7: a setting changed while the program is paused cancels the pause (the program restarts from Phase 1)."""
        if self.run['state'] == 'paused':
            self._reset_program()
            self._event('operation', 'RESET', '暂停中修改设定：取消暂停，程序回到第1阶段')

    def _cmd_volume_set(self, volume_m3):
        self._need_on()
        self._need_stopped()
        self._need_no_alarm()
        if (self._peek_phase()['function']) not in RATE_FUNCS:
            raise ActionRejected('NOT_APPLICABLE')
        self._cancel_pause()
        ph = self._cur_phase()
        if ph['function'] not in RATE_FUNCS:
            raise ActionRejected('NOT_APPLICABLE')
        ph['volume_m3'] = float(volume_m3)
        return dict(volume_m3=ph['volume_m3'])

    def _cmd_direction_set(self, direction):
        self._need_on()
        self._need_no_alarm()
        if self.run['state'] == 'purging':                                    # the purge runs in the currently set direction: it follows the change
            ph = self._peek_phase()
            if ph['function'] not in RATE_FUNCS:
                raise ActionRejected('NOT_APPLICABLE')
            new = {'INF': 'INF', 'WDR': 'WDR', 'REV': 'WDR' if self.run['direction'] == 'INF' else 'INF'}[direction]
            self._cur_phase()['direction'] = new
            self.run['direction'] = new
            return dict(direction=new)
        if self._operating():
            cur = self.phases[self.run['idx'] - 1] if 1 <= self.run['idx'] <= 41 else None
            if not cur or cur['function'] not in RATE_FUNCS or (cur.get('volume_m3') or 0.0) > 0.0:
                raise ActionRejected('NOT_APPLICABLE')                          # only while pumping continuously (manual 6.8)
            new = {'INF': 'INF', 'WDR': 'WDR', 'REV': 'WDR' if self.run['direction'] == 'INF' else 'INF'}[direction]
            cur['direction'] = new
            self.run['direction'] = new
            return dict(direction=new)
        self._cancel_pause()
        ph = self._peek_phase()
        new = {'INF': 'INF', 'WDR': 'WDR', 'REV': 'WDR' if ph['direction'] == 'INF' else 'INF'}[direction]
        self._cur_phase()['direction'] = new                                  # stored: only now is an empty selected phase created
        return dict(direction=new)

    def _cmd_phase_select(self, phase):
        self._need_on()
        self._need_stopped()
        self._cancel_pause()
        self.sel = int(phase)
        return dict(phase=self.sel)

    def _cmd_phase_set_function(self, function, rate_m3_s=None, rate_units=None, volume_m3=None, direction=None, parameter=None, tenths=False):
        self._need_on()
        self._need_stopped()
        self._need_no_alarm()
        d = _default_phase()
        d['function'] = function
        if function in RATE_FUNCS:
            if rate_m3_s is not None and not (function == 'FIL' and rate_m3_s == 0.0) and function not in ('INC', 'DEC'):
                self._check_rate(rate_m3_s)
            d.update(rate_m3_s=float(rate_m3_s or 0.0), rate_units=rate_units or 'MH', volume_m3=float(volume_m3 or 0.0), direction=direction or 'INF')
        else:
            p = int(parameter or 0)
            if function in ('JMP', 'IF', 'EVN', 'EVS') and not 1 <= p <= 41:
                raise ActionRejected('OUT_OF_RANGE')
            if function == 'LOP' and not 1 <= p <= 99:
                raise ActionRejected('OUT_OF_RANGE')
            if function == 'OUT' and p not in (0, 1):
                raise ActionRejected('OUT_OF_RANGE')
            if function == 'PAS' and tenths and not 1 <= p <= 99:
                raise ActionRejected('OUT_OF_RANGE')
            d.update(parameter=p, tenths=bool(tenths) if function == 'PAS' else False, rate_m3_s=0.0, volume_m3=0.0, direction=direction or 'INF')
        self.phases[self.sel - 1] = d
        self.run.update(state='stopped', idx=1, loops=[], trap=None)
        return dict(phase=self.sel, definition=dict(d))

    def _cmd_program_clear(self):
        self._need_on()
        self._need_stopped()
        self.phases = [_default_phase()] + [_empty_phase() for _ in range(40)]
        self.sel = 1
        self._reset_program()
        return dict(phases=1)

    def _cmd_volume_clear(self, direction):
        self._need_on()
        self._need_stopped()
        self._cancel_pause()
        for k in (('INF', 'WDR') if direction == 'BOTH' else (direction,)):
            self.dispensed[k] = 0.0
        return dict(dispensed=self._dispensed_view())

    def _dispensed_view(self):
        return dict(infused_m3=self.dispensed['INF'], withdrawn_m3=self.dispensed['WDR'], units=self._vol_units())

    def _cmd_setup_configure(self, **kw):
        self._need_on()
        self._need_stopped()
        self.setup.update(kw)
        return dict(setup=dict(self.setup))

    # ---- program control
    def _reset_program(self):
        self.run.update(state='stopped', idx=1, loops=[], trap=None, rate=None, purge=False, timer=0.0, resume='running')

    def _start_program(self, phase=1):
        if self.diameter is None:
            raise ActionRejected('DIAMETER_NOT_SET')
        first = self.phases[int(phase) - 1]
        self.run.update(state='running', idx=int(phase), loops=[], trap=None, rate=None, purge=False, resume='running',
                        direction=first['direction'] if first and first['function'] in RATE_FUNCS else 'INF')   # no stale direction from an earlier run
        self._event('operation', 'RUN', f'程序从第{phase}阶段开始')
        self._enter_phase()

    def _trigger_start(self, phase=None):
        st = self.run['state']
        self.ui.update(units_edit=None, units_t=None)                         # a start drops an uncommitted units change (the keypad commits first)
        if phase is not None:
            self._start_program(phase)                                        # RUN <n>: start at phase n whatever the state
        elif st == 'paused':
            self.run['state'] = self.run.get('resume', 'running')            # back into what the stop interrupted (pumping, PS:nn with its time left, PS:00)
            self._event('operation', 'RESUME', '程序继续')
        elif st == 'user_wait':
            self.run['idx'] += 1
            self.run['state'] = 'running'
            self._enter_phase()
        elif st == 'stopped':
            self._start_program(1)

    def _trigger_stop(self):
        st = self.run['state']
        if st in ('running', 'pause_timer', 'user_wait', 'purging'):
            if self.run['purge']:
                self.run.update(purge=False, state='stopped')
                self._event('operation', 'PURGE_END', '清洗结束')
            else:
                self.run['resume'] = st
                self.run['state'] = 'paused'
                self._event('operation', 'PAUSE', '程序暂停（Pumping LED闪烁）')
        elif st == 'paused':
            self._reset_program()
            self._event('operation', 'RESET', '取消暂停，程序回到第1阶段')

    def _cmd_pump_run(self, phase=None):
        self._need_on()
        self._need_no_alarm()
        if self.run['state'] in ('running', 'pause_timer', 'purging'):
            raise ActionRejected('ALREADY_RUNNING')
        self._trigger_start(phase)
        return dict(state=self.run['state'])

    def _cmd_pump_stop(self):
        self._need_on()
        self._trigger_stop()
        return dict(state=self.run['state'])

    def _cmd_pump_purge(self, on):
        self._need_on()
        if on:
            self._need_no_alarm()
            if self._operating() or self.run['state'] == 'paused':
                raise ActionRejected('PROGRAM_OPERATING')                    # manual 7.4: purge only with the Pumping Program stopped (not paused)
            if self.diameter is None:
                raise ActionRejected('DIAMETER_NOT_SET')
            self.run.update(state='purging', purge=True, direction=self._shown_direction(), rate=None, trap=None)   # manual 7.4: in the currently set direction
            self._event('operation', 'PURGE', '清洗：最高速泵送')
        elif self.run['purge']:
            self._trigger_stop()
        return dict(state=self.run['state'])

    def _program_error(self, kind, why):
        self.alarm = kind
        self.alarm_phase = self.run['idx']
        self._reset_program()
        self._event('alarm', 'PROGRAM_ERROR' if kind == 'program_error' else 'OUT_OF_RANGE', why)

    def _enter_phase(self):
        """Execute phases until a pumping, pause or wait phase (non-pumping functions are instantaneous)."""
        r = self.run
        for _ in range(200):
            if r['idx'] > 41:
                self._finish('程序超过第41阶段：隐含停止')
                return
            ph = self.phases[r['idx'] - 1]
            if ph is None or ph['function'] == 'STP':
                self._finish('程序停止（STOP）')
                return
            f = ph['function']
            if f in RATE_FUNCS:
                if f == 'RAT':
                    rate, direction, vol = ph['rate_m3_s'], ph['direction'], ph['volume_m3']
                elif f in ('INC', 'DEC'):
                    if r['rate'] is None:
                        self._program_error('program_error', f'第{r["idx"]}阶段INCR/DECR没有基准流量')
                        return
                    rate = r['rate'] + (ph['rate_m3_s'] if f == 'INC' else -ph['rate_m3_s'])
                    direction, vol = ph['direction'], ph['volume_m3']
                else:                                                        # FIL: reverse and refill the volume dispensed
                    prev = r['direction']
                    direction = 'WDR' if prev == 'INF' else 'INF'
                    vol = self.dispensed[prev]
                    rate = ph['rate_m3_s'] if ph['rate_m3_s'] > 0.0 else (r['rate'] or 0.0)
                    self.dispensed = {'INF': 0.0, 'WDR': 0.0}
                    if vol <= 0.0:
                        r['idx'] += 1
                        continue
                a = self._area_set()
                if rate <= 0.0 or not (V_MIN * (1 - 1e-9) <= rate / a <= V_MAX * (1 + 1e-9)):
                    self._program_error('out_of_range', f'第{r["idx"]}阶段流量超出范围')
                    return
                r.update(rate=rate, direction=direction, target=vol, pumped=0.0, state='running')
                return
            if f == 'PAS':
                n = ph['parameter'] / 10.0 if ph.get('tenths') else float(ph['parameter'])
                r['rate_hold'] = r['rate']
                if n > 0.0:
                    r.update(state='pause_timer', timer=n)
                else:
                    r['state'] = 'user_wait'
                    if self.setup['alarm']:
                        self._event('operation', 'WAIT_TRIGGER', '等待启动触发（PS:00）')
                return
            if f == 'JMP':
                r['idx'] = ph['parameter']
                continue
            if f == 'LPS':
                if len(r['loops']) >= 3:
                    self._program_error('program_error', '循环嵌套超过3层')
                    return
                r['loops'].append(dict(start=r['idx'] + 1, remaining=None))
                r['idx'] += 1
                continue
            if f in ('LOP', 'LPE'):
                if not r['loops']:
                    r['loops'].append(dict(start=1, remaining=None))           # implied unpaired loop start at Phase 1
                top = r['loops'][-1]
                if f == 'LPE':
                    r['idx'] = top['start']
                    continue
                if top['remaining'] is None:
                    top['remaining'] = ph['parameter'] - 1
                if top['remaining'] > 0:
                    top['remaining'] -= 1
                    r['idx'] = top['start']
                else:
                    r['loops'].pop()
                    r['idx'] += 1
                continue
            if f == 'IF':
                r['idx'] = ph['parameter'] if self.ttl_in[6] == 0 else r['idx'] + 1
                continue
            if f in ('EVN', 'EVS'):
                r['trap'] = dict(target=ph['parameter'], kind='falling' if f == 'EVN' else 'edge')
                if f == 'EVN' and self.ttl_in[4] == 0:                       # a low level at execution triggers at once
                    r['trap'] = None
                    r['idx'] = ph['parameter']
                    continue
                r['idx'] += 1
                continue
            if f == 'EVR':
                r['trap'] = None
                r['idx'] += 1
                continue
            if f == 'CLD':
                self.dispensed = {'INF': 0.0, 'WDR': 0.0}
                r['idx'] += 1
                continue
            if f == 'BEP':
                self._event('operation', 'BEEP', '程序蜂鸣')
                r['idx'] += 1
                continue
            if f == 'OUT':
                self.ttl_out[5] = ph['parameter']
                r['idx'] += 1
                continue
        self._program_error('program_error', '程序在一次推进中执行了200个非泵送阶段（无限跳转）')

    def _finish(self, why):
        self._reset_program()
        self._event('operation', 'PROGRAM_END', why + ('（蜂鸣）' if self.setup['alarm'] else ''))

    # ---- keypad (manual chapter 6)
    def _cmd_keypad_press(self, key):
        self._need_on()
        ui = self.ui
        if self.alarm is not None:                                            # any key clears an alarm (and does nothing else)
            self._event('operation', 'ALARM_CLEARED', f'按键清除报警{self.alarm}')
            self.alarm = None
            ui['message'] = None
            return dict(display=self._display_text(), mode=ui['mode'])
        if ui['message']:
            ui['message'] = None
            ui['edit'] = None
            return dict(display=self._display_text(), mode=ui['mode'])
        locked = self.setup['keypad_lockout'] and self.ttl_in[6] != 0        # manual 8.7: the Lockout Disable Key grounds TTL pin 6
        if locked and key not in ('start', 'rate', 'volume', 'diameter'):
            ui['message'] = 'LOC'                                             # locked: only start / stop and reviewing the settings
            return dict(display=self._display_text(), mode=ui['mode'])
        if ui.get('units_edit') is not None:                                  # manual 6.6.1: Rate Units Change mode
            if key.startswith('up'):
                order = sorted(UNIT_FACTOR, key=lambda u: ('UM', 'MM', 'UH', 'MH').index(u))
                ui['units_edit'] = order[(order.index(ui['units_edit']) + 1) % 4]
                ui['units_t'] = self.t
                return dict(display=self._display_text(), mode=ui['mode'])
            self._commit_units()
            return dict(display=self._display_text(), mode=ui['mode'])
        if key != 'start' and self.run['state'] == 'user_wait' and not locked:
            self._reset_program()                                             # manual 9.3.12: at PS:00 any key but Start resets the program
            self._event('operation', 'RESET', '等待触发时按其他键：程序回到第1阶段')
        if key.startswith('up'):
            if ui['mode'] == 'dispensed':
                if not self._operating():                                     # manual 6.7.2 / 7.6 A: an up arrow on the Volume Dispensed display clears it
                    if self.run['state'] == 'paused':
                        self._reset_program()
                    self.dispensed = {'INF': 0.0, 'WDR': 0.0}
                    self._event('operation', 'DISPENSED_CLEARED', '已排体积清零（按住方向键）')
                return dict(display=self._display_text(), mode=ui['mode'])
            if ui['mode'] != 'rate' and self._operating():
                ui['message'] = 'NA'
                return dict(display=self._display_text(), mode=ui['mode'])
            if self.run['state'] == 'paused':
                self._reset_program()                                         # manual 7.7: any key other than Start cancels the pause
            if ui['edit'] is None:
                ui['edit'] = _cells(self._value_text())
            cells, dp = ui['edit']
            i = int(key[2]) - 1
            cells[i] = str((int(cells[i]) + 1) % 10) if cells[i].isdigit() else '1'
            ui['edit_t'] = self.t
            return dict(display=_text(cells, dp), mode=ui['mode'])
        self._commit_edit()
        if ui['message']:
            return dict(display=self._display_text(), mode=ui['mode'])
        if key != 'start' and self.run['state'] == 'paused' and not locked:
            self._reset_program()                                             # manual 7.7: any other key cancels the pause
        if key == 'rate':
            ph = self._peek_phase()
            if ui['mode'] == 'rate' and not locked and not self._operating() and ph['function'] == 'RAT':
                ui['units_edit'], ui['units_t'] = ph.get('rate_units', 'MH'), self.t   # a press while the rate is shown: units change mode
            ui['mode'] = 'rate'
        elif key == 'volume':
            ui['mode'] = 'dispensed' if ui['mode'] == 'volume' else 'volume'
        elif key == 'diameter':
            ui['mode'] = 'diameter'
        elif key == 'direction':
            try:
                self._cmd_direction_set('REV')
            except ActionRejected:
                ui['message'] = 'NA'
        elif key == 'start':
            if self.run['state'] in ('running', 'pause_timer', 'user_wait', 'purging') and self.run['state'] != 'user_wait':
                self._trigger_stop()
            else:
                try:
                    self._trigger_start()
                except ActionRejected:
                    ui['message'] = 'NA'
        return dict(display=self._display_text(), mode=ui['mode'])

    def _commit_units(self):
        ui = self.ui
        u, ui['units_edit'], ui['units_t'] = ui.get('units_edit'), None, None
        if u is None:
            return
        ph = self._peek_phase()
        old = ph.get('rate_units', 'MH')
        if u == old:
            return                                                            # nothing stored: an empty selected phase stays empty
        rate = (ph.get('rate_m3_s') or 0.0) / UNIT_FACTOR[old] * UNIT_FACTOR[u]   # the displayed number stays, the units change
        try:
            self._check_rate(rate)
        except ActionRejected:
            ui['message'] = 'oor'
            return
        ph = self._cur_phase()
        ph['rate_units'], ph['rate_m3_s'] = u, rate
        self._event('operation', 'UNITS_STORED', f'流量单位改为{u}（显示闪烁一次）')

    def _commit_edit(self):
        ui = self.ui
        if ui['edit'] is None:
            return
        txt = _text(*ui['edit']).replace(' ', '')
        ui['edit'], ui['edit_t'] = None, None
        try:
            v = float(txt)
        except ValueError:
            ui['message'] = 'oor'
            return
        try:
            if ui['mode'] == 'rate':
                ph = self._shown_phase()
                self._cmd_rate_set(v * UNIT_FACTOR[ph['rate_units']])
            elif ui['mode'] == 'volume':
                self._cmd_volume_set(v * (1e-9 if self._vol_units() == 'UL' else 1e-6))
            elif ui['mode'] == 'diameter':
                if not D_RANGE[0] - 1e-12 <= v * 1e-3 <= D_RANGE[1] + 1e-12:
                    raise ActionRejected('OUT_OF_RANGE')
                self._cmd_syringe_set_diameter(v * 1e-3)
            self._event('operation', 'VALUE_STORED', f'{ui["mode"]} = {txt}（显示闪烁一次）')
        except ActionRejected:
            ui['message'] = 'oor'

    # ---- RS-232 (Basic protocol)
    def _status_prompt(self):
        st = self.run['state']
        if st == 'purging':
            return 'X'
        if st == 'running':
            return 'I' if self.run['direction'] == 'INF' else 'W'
        return {'stopped': 'S', 'paused': 'P', 'pause_timer': 'T', 'user_wait': 'U'}[st]

    def _cmd_serial_send(self, command):
        self._need_on()
        self.rs232_seen = True
        cmd = re.sub(r'\s+', '', command.upper())
        if cmd.startswith('*'):
            cmd = cmd[1:]
        m = re.match(r'^(\d{1,2})(?=[A-Z]|$)', cmd)
        if m:
            if int(m.group(1)) != 0:
                return dict(response='')                                     # another pump's address: ignored
            cmd = cmd[m.end():]
        alarm_txt = ''
        if getattr(self, '_power_reset_alarm', False):
            alarm_txt, self._power_reset_alarm = 'A?R', False
        elif self.alarm is not None:
            alarm_txt = {'stall': 'A?S', 'program_error': 'A?E', 'out_of_range': 'A?O'}[self.alarm]
            self.alarm = None                                                 # the alarm status sent in a response acknowledges it (manual 10.3)
            self.ui['message'] = None
        if alarm_txt:
            return dict(response='00' + alarm_txt)
        keep = {k: v for k, v in self.__dict__.items() if k not in ('_principals', '_requests', '_caller')}
        snapshot = copy.deepcopy(keep)
        try:
            data = self._serial(cmd)
        except ActionRejected as exc:
            data = {'OUT_OF_RANGE': '?OOR', 'UNKNOWN_COMMAND': '?'}.get(str(exc), '?NA')
            self.__dict__.update(snapshot)                                    # a refused command changes nothing (the RS-232 triangle stays lit)
            self.rs232_seen = True
        except (ValueError, IndexError, KeyError, TypeError):                # a malformed number the parser did not anticipate: out of range
            data = '?OOR'
            self.__dict__.update(snapshot)
            self.rs232_seen = True
        return dict(response='00' + self._status_prompt() + data)

    def _serial(self, cmd):
        def num(s):
            if not re.fullmatch(r'\d{0,4}\.?\d{0,3}', s) or not re.search(r'\d', s) or len(s.replace('.', '')) > 4:
                raise ActionRejected('OUT_OF_RANGE')
            return float(s)
        if cmd == '':
            return ''
        if cmd.startswith('DIA'):
            a = cmd[3:]
            if not a:
                return fmt4((self.diameter or 0.0) * 1e3)
            v = num(a) * 1e-3
            if not D_RANGE[0] - 1e-12 <= v <= D_RANGE[1] + 1e-12:
                raise ActionRejected('OUT_OF_RANGE')
            self._cmd_syringe_set_diameter(v)
            return ''
        if cmd.startswith('PHN'):
            a = cmd[3:]
            if not a:
                return f'{self.sel:02d}'
            n = int(a) if a.isdigit() else 0
            if not 1 <= n <= 41:
                raise ActionRejected('OUT_OF_RANGE')
            self._cmd_phase_select(n)
            return ''
        if cmd.startswith('FUN'):
            a = cmd[3:]
            if not a:
                ph = self._peek_phase()
                return ph['function'] + (f'{ph["parameter"]:02d}' if ph['function'] in ('JMP', 'IF', 'EVN', 'EVS', 'LOP', 'PAS', 'OUT') else '')
            m = re.fullmatch(r'(RAT|FIL|INC|DEC|STP|JMP|LPS|LOP|LPE|PAS|IF|EVN|EVS|EVR|CLD|BEP|OUT)([\d.]*)', a)
            if not m:
                raise ActionRejected('UNKNOWN_COMMAND' if not a.startswith(('PRI', 'PRL', 'TRG')) else 'NOT_APPLICABLE')
            f, par = m.group(1), m.group(2)
            if par and (not re.fullmatch(r'\d{0,2}(\.\d)?', par) or f not in ('JMP', 'LOP', 'PAS', 'IF', 'EVN', 'EVS', 'OUT') or par == '.'):
                raise ActionRejected('OUT_OF_RANGE')                          # 1-2 digits (PAS: n.n) and only for the functions with a parameter
            if '.' in par and f != 'PAS':
                raise ActionRejected('OUT_OF_RANGE')                          # only PAS takes tenths of a second
            if f == 'PAS' and '.' in par:
                self._cmd_phase_set_function(f, parameter=int(round(float(par) * 10)), tenths=True)
            else:
                cur = self._peek_phase()
                kw = dict(rate_m3_s=cur.get('rate_m3_s', 0.0), rate_units=cur.get('rate_units', 'MH'), volume_m3=cur.get('volume_m3', 0.0),
                          direction=cur.get('direction', 'INF')) if f in RATE_FUNCS and cur['function'] in RATE_FUNCS else {}
                self._cmd_phase_set_function(f, parameter=int(par) if par else 0, **kw)
            return ''
        if cmd.startswith('RAT'):
            a = cmd[3:]
            ph = self._shown_phase()
            if not a:
                under_way = self.run['state'] in ('running', 'paused') and ph['function'] in RATE_FUNCS
                if ph['function'] not in RATE_FUNCS:
                    ph = self._peek_phase()
                rate = self.run['rate'] if under_way and self.run['rate'] is not None else ph.get('rate_m3_s', 0.0)
                return fmt4(rate / UNIT_FACTOR[ph.get('rate_units', 'MH')]) + ph.get('rate_units', 'MH')
            mode = a[0] if a[0] in 'CI' else None
            a = a[1:] if mode else a
            m = re.fullmatch(r'([\d.]+)(UM|MM|UH|MH)?', a)
            if not m:
                raise ActionRejected('OUT_OF_RANGE')
            units = m.group(2) or ph['rate_units']
            if mode == 'I' and self.run['state'] == 'running' and self.run['direction'] != 'INF':
                return ''
            self._cmd_rate_set(num(m.group(1)) * UNIT_FACTOR[units], units=m.group(2), continue_paused=(mode == 'C'), store=not self._operating())
            return ''
        if cmd.startswith('VOL'):
            a = cmd[3:]
            if not a:
                ph = self._peek_phase()
                return fmt4((ph.get('volume_m3') or 0.0) / (1e-9 if self._vol_units() == 'UL' else 1e-6)) + self._vol_units()
            if a in ('UL', 'ML'):
                self._need_stopped()
                self._cancel_pause()
                self.vol_units_override = a
                return ''
            self._cmd_volume_set(num(a) * (1e-9 if self._vol_units() == 'UL' else 1e-6))
            return ''
        if cmd.startswith('DIR'):
            a = cmd[3:]
            if not a:
                return self._shown_direction()
            if a not in ('INF', 'WDR', 'REV'):
                raise ActionRejected('NOT_APPLICABLE')
            self._cmd_direction_set(a)
            return ''
        if cmd.startswith('RUN'):
            a = cmd[3:]
            if a.startswith('E'):
                b = a[1:]
                if b and not (b.isdigit() and 1 <= int(b) <= 41):
                    raise ActionRejected('OUT_OF_RANGE')
                if self._operating() and not self.run['purge']:               # a purge is not a program: no event jump
                    target = int(b) if b else (self.run['trap'] or {}).get('target')
                    if target:
                        self._jump_event(target)
                return ''
            if a and not (a.isdigit() and 1 <= int(a) <= 41):
                raise ActionRejected('OUT_OF_RANGE')
            self._cmd_pump_run(int(a) if a else None)
            return ''
        if cmd == 'PUR':
            self._cmd_pump_purge(True)
            return ''
        if cmd == 'STP':
            self._cmd_pump_stop()
            return ''
        if cmd == 'DIS':
            f = 1e-9 if self._vol_units() == 'UL' else 1e-6
            return f'I{fmt4(self.dispensed["INF"] / f)}W{fmt4(self.dispensed["WDR"] / f)}{self._vol_units()}'
        if cmd.startswith('CLD'):
            a = cmd[3:]
            if a not in ('INF', 'WDR'):
                raise ActionRejected('NOT_APPLICABLE')
            self._cmd_volume_clear(a)
            return ''
        for key, name in (('LN', 'low_noise'), ('AL', 'alarm'), ('PF', 'power_failure'), ('BP', 'beep')):
            if cmd.startswith(key) and re.fullmatch(key + r'[01]?', cmd):
                a = cmd[len(key):]
                if not a:
                    return '1' if self.setup[name] else '0'
                self._cmd_setup_configure(**{name: a == '1'})
                return ''
        if cmd.startswith('TRG'):
            a = cmd[3:]
            if not a:
                return self.setup['trigger']
            if a not in ('FT', 'FH', 'F2', 'LE', 'ST', 'T2', 'SP', 'P2', 'RL', 'RH', 'SL', 'SH', 'OF'):
                raise ActionRejected('OUT_OF_RANGE')
            self._cmd_setup_configure(trigger=a)
            return ''
        for key, name in (('DIN', 'direction_input'), ('ROM', 'motor_output')):
            if cmd.startswith(key):
                a = cmd[3:]
                if not a:
                    return str(self.setup[name])
                if a not in ('0', '1'):
                    raise ActionRejected('OUT_OF_RANGE')
                self._cmd_setup_configure(**{name: int(a)})
                return ''
        if cmd.startswith('BUZ'):
            a = cmd[3:]
            if not a:
                return '1' if self.buzzer else '0'
            if not re.fullmatch(r'0|1\d{0,2}', a):
                raise ActionRejected('OUT_OF_RANGE')
            self.buzzer = a.startswith('1') and (len(a) == 1 or int(a[1:]) == 0)   # continuous; a counted beep ends by itself
            self._event('operation', 'BUZZER', '蜂鸣器' + ('开' if a.startswith('1') else '关') + (f'（{int(a[1:])}声）' if len(a) > 1 and int(a[1:]) else ''))
            return ''
        if cmd.startswith('LOC'):
            a = cmd[3:]
            if not a:
                return '1' if self.setup['keypad_lockout'] else '0'
            if a in ('0', '1'):
                self._cmd_setup_configure(keypad_lockout=a == '1')
                return ''
            raise ActionRejected('NOT_APPLICABLE')
        if cmd.startswith('OUT5'):
            a = cmd[4:]
            if a not in ('0', '1'):
                raise ActionRejected('OUT_OF_RANGE')
            self.ttl_out[5] = int(a)
            return ''
        if cmd.startswith('IN'):
            a = cmd[2:]
            if a not in ('2', '3', '4', '6'):
                raise ActionRejected('OUT_OF_RANGE')
            return str(self.ttl_in[int(a)])
        if cmd == 'VER':
            return 'NE1000V3.923'
        if cmd in ('RESET',):
            self._need_stopped()
            self.phases = [_default_phase()] + [_empty_phase() for _ in range(40)]
            self.setup = dict(power_failure=False, low_noise=False, alarm=True, beep=True, keypad_lockout=False, trigger='FT', direction_input=0, motor_output=0)
            self.vol_units_override = None
            self._reset_program()
            return ''
        raise ActionRejected('UNKNOWN_COMMAND')

    def _jump_event(self, target):
        r = self.run
        r['trap'] = None
        r['idx'] = target
        r['state'] = 'running'
        self._event('operation', 'EVENT', f'事件触发：跳到第{target}阶段')
        self._enter_phase()

    # ---- TTL I/O
    def _cmd_ttl_set(self, pin, level):
        self._need_plant()
        if pin == 5:
            raise ActionRejected('NOT_AN_INPUT')
        if level != self.ttl_in[pin]:
            self.ttl_pending[pin] = (level, self.t)                           # recognised after the level is held >= 100 ms (firmware filter)
        else:
            self.ttl_pending.pop(pin, None)
        return dict(inputs={str(k): v for k, v in self.ttl_in.items()})

    def _ttl_edge(self, pin, level):
        old = self.ttl_in[pin]
        self.ttl_in[pin] = level
        if not self._on():
            return
        falling, rising = old == 1 and level == 0, old == 0 and level == 1
        if pin == 2:
            mode = self.setup['trigger']
            start = (mode in ('FT', 'FH', 'ST') and falling) or (mode in ('F2', 'LE', 'T2') and rising) or (mode == 'RL' and falling) or (mode == 'RH' and rising)
            stop = (mode in ('SP',) and falling) or (mode in ('P2', 'FH') and rising) or (mode == 'LE' and falling) or (mode == 'SL' and falling) or (mode == 'SH' and rising)
            toggle = mode in ('FT', 'F2')
            if self.alarm is not None:
                return
            try:
                if toggle and start:
                    if self.run['state'] in ('running', 'pause_timer', 'purging'):
                        self._trigger_stop()
                    else:
                        self._trigger_start()
                elif start and not self.run['state'] in ('running', 'pause_timer', 'purging'):
                    self._trigger_start()
                elif stop and self.run['state'] in ('running', 'pause_timer', 'user_wait'):
                    self._trigger_stop()
            except ActionRejected as exc:                                     # a trigger the pump cannot act on (no diameter yet): ignored, like the keypad's '----'
                self._event('operation', 'TRIGGER_IGNORED', f'TTL运行触发被忽略（{ERROR_ZH.get(str(exc), str(exc))}）')
                return
        elif pin == 3:
            if self.run['state'] == 'running':
                new = ('INF' if falling else 'WDR') if self.setup['direction_input'] == 0 else ('WDR' if falling else 'INF')
                try:
                    self._cmd_direction_set(new)
                except ActionRejected:
                    pass
        elif pin == 4:
            trap = self.run['trap']
            if trap and self._operating() and not self.run['purge'] and (falling or (trap['kind'] == 'edge' and rising)):
                self._jump_event(trap['target'])

    # ---- syringe, outlet, collar
    def _cmd_syringe_load(self, syringe, liquid_id, fill_volume_m3, air_volume_m3=0.0):
        self._need_plant()
        if self.syringe is not None:
            raise ActionRejected('SYRINGE_LOADED')
        if self._operating():
            raise ActionRejected('PROGRAM_OPERATING')
        s = SYR[syringe]
        a = math.pi * (s['inside_diameter_m'] * (1.0 + self.id_error)) ** 2 / 4.0
        if fill_volume_m3 + air_volume_m3 > s['nominal_m3'] * 1.0 + 1e-15:
            raise ActionRejected('OVERFILL')
        travel = (fill_volume_m3 + air_volume_m3) / a
        if self.collar is not None and X_ZERO - travel > self.collar + 1e-12:
            raise ActionRejected('COLLAR_IN_THE_WAY')                        # the pusher cannot reach the plunger flange past the collar
        if travel > X_ZERO + 1e-12:
            raise ActionRejected('SYRINGE_TOO_LONG')                          # manual: 140 mL syringes only partially filled
        if s['inside_diameter_m'] > 0.05:
            raise ActionRejected('SYRINGE_TOO_LARGE')
        self.syringe = dict(type=syringe, liquid_id=liquid_id, liquid_m3=float(fill_volume_m3), air_m3_atm=float(air_volume_m3), leaked_m3=0.0, cavitated=False)
        self.x = X_ZERO - travel                                              # the pusher block is slid onto the plunger flange (drive-nut button)
        self.p, self.vs = 0.0, 0.0
        self.led['placed_m3'] += fill_volume_m3
        self._event('operation', 'SYRINGE_LOADED', f'装上{syringe}（{liquid_id} {fill_volume_m3 * 1e6:.3g} mL）')
        return dict(syringe=self._syringe_view())

    def _syringe_view(self):
        if self.syringe is None:
            return None
        return dict(type=self.syringe['type'], liquid_id=self.syringe['liquid_id'])

    def _cmd_syringe_unload(self):
        self._need_plant()
        if self.syringe is None:
            raise ActionRejected('NO_SYRINGE')
        if self._operating():
            raise ActionRejected('PROGRAM_OPERATING')
        s = self.syringe
        out = dict(type=s['type'], liquid_id=s['liquid_id'], liquid_m3=s['liquid_m3'], air_m3=s['air_m3_atm'])
        self.led['removed_m3'] += s['liquid_m3']
        self.syringe = None
        self.p, self.vs = 0.0, 0.0
        self._event('operation', 'SYRINGE_UNLOADED', '取下注射器')
        return dict(syringe=out)

    def _cmd_outlet_connect(self, tubing=None, tubing_length_m=0.0, needle='none', backpressure_pa=0.0, reservoir_liquid_m3=0.0):
        self._need_plant()
        if self._operating():
            raise ActionRejected('PROGRAM_OPERATING')
        self.outlet = dict(tubing=tubing, tubing_length_m=float(tubing_length_m if tubing else 0.0), needle=needle, backpressure_pa=float(backpressure_pa),
                           reservoir_liquid_m3=float(reservoir_liquid_m3))
        return dict(outlet=dict(self.outlet))                                # an evaluator back-pressure disturbance (medium.configure) stays in force

    def _pb(self):
        return self.outlet['backpressure_pa'] if self.bp_hidden is None else self.bp_hidden

    def _cmd_collar_set(self, position_m):
        self._need_plant()
        if position_m is not None and position_m < self.x - 1e-9:
            raise ActionRejected('BEHIND_PUSHER')
        self.collar = None if position_m is None else float(position_m)
        return dict(position_m=self.collar)

    def _cmd_medium_configure(self, syringe_id_error=None, backpressure_pa=None, clear_backpressure=False):
        self._need_plant()
        if syringe_id_error is not None:
            if self.syringe is not None:
                raise ActionRejected('SYRINGE_LOADED')
            self.id_error = float(syringe_id_error)
        if clear_backpressure:
            self.bp_hidden = None                                             # back to the back pressure the agent declared
        if backpressure_pa is not None:
            self.bp_hidden = float(backpressure_pa)                           # the real downstream pressure changes; the agent's declared outlet does not
        return dict(medium=dict(syringe_id_error=self.id_error, backpressure_pa=self._pb()))

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
            self._power_down()
        elif not was and self._on():
            self._power_up()
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}' + ('：注入' if active else '：清除'))
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- physics
    def _value_text(self):
        ui = self.ui
        if ui['mode'] == 'rate':
            ph = self._shown_phase()
            under_way = (self._operating() or self.run['state'] == 'paused') and ph['function'] in RATE_FUNCS
            if ph['function'] not in RATE_FUNCS:                              # inside a pause / control phase: the selected phase's rate
                ph = self._peek_phase()
            units = ph.get('rate_units', 'MH') if ph['function'] in RATE_FUNCS else 'MH'
            rate = self.run['rate'] if under_way and self.run['rate'] is not None else ph.get('rate_m3_s', 0.0)   # INC / DEC / FIL: the actual rate
            return fmt4(rate / UNIT_FACTOR[units])
        f = 1e-9 if self._vol_units() == 'UL' else 1e-6
        if ui['mode'] == 'volume':
            return fmt4((self._peek_phase().get('volume_m3') or 0.0) / f)
        if ui['mode'] == 'dispensed':
            return fmt4(self.dispensed[self._shown_direction()] / f)
        return fmt4((self.diameter or 0.0) * 1e3)

    def _shown_direction(self):
        if self.run['state'] in ('running', 'purging', 'paused', 'pause_timer', 'user_wait'):
            return self.run['direction']
        ph = self.phases[self.sel - 1]
        return ph['direction'] if ph and ph['function'] in RATE_FUNCS else 'INF'

    def _display_text(self):
        if not self._on():
            return ''
        ui, r = self.ui, self.run
        if self.alarm == 'stall':
            return 'StAL'
        if self.alarm == 'program_error':                                      # manual 6.14 glyphs (the colon between digits 2 and 3 is not a cell)
            return f'Er{(self.alarm_phase or 0) % 100:02d}'
        if self.alarm == 'out_of_range':
            return f'or{(self.alarm_phase or 0) % 100:02d}'
        if ui['message']:
            return {'oor': 'oor', 'NA': '----', 'LOC': 'LoC-'}[ui['message']]
        if r['state'] == 'purging':
            return 'PurG'
        if r['state'] == 'pause_timer':
            return f'PS{int(math.ceil(r["timer"] - 1e-9)) % 100:02d}'
        if r['state'] == 'user_wait':
            return 'PAUS'
        if ui.get('units_edit') is not None:
            return 'UntS'
        if ui['edit'] is not None:
            return _text(*ui['edit'])
        if ui['mode'] == 'volume' and not (self._peek_phase().get('volume_m3') or 0.0):
            return ' oFF'
        return self._value_text()

    def _leds(self):
        on = self._on()
        d = dict(mm=False, ml=False, ul=False, min=False, hr=False, dispensed=False, withdraw=False, pumping=False, rs232=False)
        if not on:
            return d
        ui = self.ui
        if ui['mode'] == 'rate':
            ph = self._shown_phase()
            u = ui.get('units_edit') or (ph.get('rate_units', 'MH') if ph['function'] in RATE_FUNCS else 'MH')
            d.update(ul=u[0] == 'U', ml=u[0] == 'M', min=u[1] == 'M', hr=u[1] == 'H')
        elif ui['mode'] in ('volume', 'dispensed'):
            d.update(ul=self._vol_units() == 'UL', ml=self._vol_units() == 'ML', dispensed=ui['mode'] == 'dispensed')
        else:
            d['mm'] = True
        d['withdraw'] = self._shown_direction() == 'WDR'
        d['pumping'] = self._motor_speed() != 0.0
        d['rs232'] = self.rs232_seen
        return d

    def _motor_speed(self):
        if not self._on() or self.alarm is not None or 'motor_failure' in self.faults or self.diameter is None:
            return 0.0
        st = self.run['state']
        sign = 1.0 if self.run['direction'] == 'INF' else -1.0
        if st == 'purging':
            return V_MAX * sign
        if st == 'running' and self.run.get('rate'):
            return self.run['rate'] / self._area_set() * sign
        return 0.0

    def _quiet(self):
        if self.plant is None:
            return True
        if self.ui['edit'] is not None or self.ui.get('units_edit') is not None or self.ttl_pending:
            return False
        if self._on() and self.run['state'] in ('running', 'purging', 'pause_timer'):
            return False
        if self.syringe is not None and abs(self.p - self._pb()) > 1e-9 and 'occlusion' not in self.faults:
            return False
        return True

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
                n = int(math.floor((t_end - self.t_grid) / DT + 1e-6))
                if n > 0:
                    self.tick += n
                    self.t_grid = round(self.tick * DT, 9)
                    self.t = self.t_grid
                    continue
            self.t = round(self.t_grid + DT, 9)                              # tick events carry the end of their grid interval (never before a command)
            self._tick(DT)
            self.tick += 1
            self.t_grid = round(self.tick * DT, 9)
            self.t = self.t_grid
        self.t = t_end
        self._outputs()
        return self.t

    def _stall(self, why):
        self.alarm = 'stall'
        self.alarm_phase = self.run['idx']
        self.run['resume'] = 'running'                                        # a stall interrupts pumping: Start resumes the phase where it stopped
        self.run['state'] = 'paused' if not self.run['purge'] else 'stopped'
        self.run['purge'] = False
        self.over_force_s = 0.0
        self._event('alarm', 'STALL', '失速报警：' + why + '（电机停止、程序暂停' + ('、蜂鸣' if self.setup['alarm'] else '') + '）')

    def _tick(self, h):
        ui = self.ui
        if ui['edit'] is not None and self.t - ui['edit_t'] >= ENG['display_commit_s'] - 1e-9:
            self._commit_edit()                                               # stored 2 s after the last arrow key (manual 6.1)
        if ui.get('units_edit') is not None and self.t - ui['units_t'] >= ENG['display_commit_s'] - 1e-9:
            self._commit_units()                                              # manual 6.6.1: or wait 2 seconds
        for pin, (lvl, t0) in list(self.ttl_pending.items()):
            if self.t - t0 >= DOC['ttl_min_level_s'] - 1e-9:
                del self.ttl_pending[pin]
                self._ttl_edge(pin, lvl)
        rem = h
        for _ in range(100):                                                  # a phase that ends inside the tick hands the rest of the tick on
            r = self.run
            if self._on() and r['state'] == 'pause_timer':
                if r['timer'] > rem + 1e-9:
                    r['timer'] = round(r['timer'] - rem, 9)
                    self._fluid(rem, 0.0)
                    rem = 0.0
                    break
                used = max(r['timer'], 0.0)
                self._fluid(used, 0.0)
                rem = round(rem - used, 12)
                r['timer'] = 0.0
                r['idx'] += 1
                r['state'] = 'running'
                self._enter_phase()
                continue
            sub, stop = self._move(rem)
            rem = round(rem - sub, 12)
            if stop == 'target' and r['state'] == 'running':
                r['idx'] += 1
                self._enter_phase()
                if rem > 1e-12:
                    continue
            elif stop and stop != 'target' and self.alarm is None:
                self._stall(stop)
            if rem > 1e-12:
                self._fluid(rem, 0.0)                                         # stopped for the rest of the tick: the line relaxes
                rem = 0.0
            break
        self._outputs()

    def _move(self, hh):
        """Advance the pusher for up to hh seconds; stop early at the phase volume or a mechanical limit.  Returns (time used, stop reason)."""
        r = self.run
        v = self._motor_speed()
        if self._on() and r['state'] in ('running', 'purging') and 'motor_failure' in self.faults and self.alarm is None:
            self._stall('电机不转（电机失效）')
            v = 0.0
        if v == 0.0 or hh <= 0.0:
            self._fluid(hh, 0.0)
            return hh, None
        a_set = self._area_set()
        sub, stop = hh, None
        if r['state'] == 'running' and (r.get('target') or 0.0) > 0.0:
            left = max(r['target'] - r.get('pumped', 0.0), 0.0)
            if a_set * abs(v) * hh >= left - 1e-18:
                sub, stop = left / (a_set * abs(v)), 'target'
        if v > 0.0:
            lim, why = TRAVEL, '推块到行程末端'
            c = COLLAR_PARK if self.collar is None else self.collar
            if c < lim:
                lim, why = c, '推块碰到导杆限位夹'
            if self.syringe is not None and X_ZERO < lim:
                lim, why = X_ZERO, '活塞推到底'
        else:
            lim, why = 0.0, '推块退到末端板'
            if self.syringe is not None:
                lim2 = X_ZERO - 1.1 * SYR[self.syringe['type']]['nominal_m3'] / self._area_true()
                if lim2 > lim:
                    lim, why = lim2, '活塞拉到注射器刻度末端'
        t_lim = max((lim - self.x) / v, 0.0)
        if t_lim < sub - 1e-15:                                               # a mechanical limit before the phase volume
            sub, stop = t_lim, why
        dx = (max(lim - self.x, 0.0) if v > 0.0 else min(lim - self.x, 0.0)) if stop == why else v * sub   # never backwards through a limit
        self.x = min(max(self.x + dx, 0.0), TRAVEL)
        if dx != 0.0:
            k = r['direction']
            self.dispensed[k] += a_set * abs(dx)
            r['pumped'] = r.get('pumped', 0.0) + a_set * abs(dx)
            units = 1e-9 if self._vol_units() == 'UL' else 1e-6
            if self.dispensed[k] / units >= 9999.5:
                self.dispensed[k] = 0.0                                       # manual 7.6 E: rolls over from 9999 to 0
        self._fluid(sub, dx)
        return sub, stop

    def _fluid(self, h, dx):
        """Syringe - line pressure over h seconds with the pusher moving dx: the pressure follows the exact exponential of the linearised
        circuit (constant C and R over the tick), the volumes are then booked exactly - the linear stored volume C_lin p is tracked as C_lin
        changes and the air follows the ideal-gas law at the new pressure - so the geometric syringe volume always equals its contents."""
        self.force = 0.0
        if self.syringe is None or h <= 0.0 or (dx == 0.0 and (self.p == self._pb() or 'occlusion' in self.faults)):
            return                                                            # standing and relaxed, or standing against a blocked outlet: nothing flows
        s = self.syringe
        a = self._area_true()
        q_in = a * dx / h
        R, C = self._resistance(), self._compliance()
        pb = self._pb()
        p_ss = pb + R * q_in
        tau = R * C
        p_new = p_ss + (self.p - p_ss) * math.exp(-h / tau) if tau > 0 else p_ss
        p_min = ENG['cavitation_abs_pa'] - self.plant['pressure_pa']
        if p_new < p_min:                                                     # withdrawal faster than the line can supply: outgassing / cavitation
            inflow = (p_min - pb) / R * h if math.isfinite(R) else 0.0
            self._book(q_in * h, p_min, out_fixed=inflow)
            if not s['cavitated']:
                s['cavitated'] = True
                self._event('truth', 'CAVITATION', '抽取时液体压力过低：析气/空化（气泡进入注射器）')
        else:
            self._book(q_in * h, p_new)
        if dx != 0.0:
            fric = ENG['friction_n'][SYR[s['type']]['material']]
            v = abs(dx) / h
            self.force = self.p * a + math.copysign(fric, dx)
            if abs(self.force) > max_force(v):
                self.over_force_s += h
                if self.over_force_s >= ENG['stall_debounce_s'] - 1e-9 and self.alarm is None:
                    self._event('truth', 'STALL_FORCE', f'推力{abs(self.force):.0f} N超过该速度的最大推力{max_force(v):.0f} N')
                    self._stall('推力超过电机能力（管路堵塞或背压过高）')
            else:
                self.over_force_s = 0.0
        elif self.p != pb and abs(self.p - pb) < 1e-6 * max(1.0, abs(pb)):  # relaxed: snap to the back pressure (the remaining stored volume moves)
            self._book(0.0, pb)

    def _book(self, swept, p_new, out_fixed=None):
        """Volume bookkeeping for a pressure change self.p -> p_new while the plunger sweeps `swept` m3 (positive = infusing).
        Contents = liquid - C_lin p + V_air(p); out = swept - d(C_lin p) + dV_air (+ gas formed when the outflow is fixed by cavitation)."""
        s, L = self.syringe, self.led
        c_lin = self._compliance_lin()
        p_atm = self.plant['pressure_pa']
        dv_air = self._air_volume(p_new) - self._air_volume(self.p)
        out = swept - (c_lin * p_new - self.vs) + dv_air
        gas = 0.0
        if out_fixed is not None:
            gas = max(out_fixed - out, 0.0)                                   # geometric growth not filled by inflow or decompression
            out = out_fixed if gas > 0.0 else out
        self.vs = c_lin * p_new
        p_abs = max(p_atm + p_new, 1.0)
        if gas > 0.0:
            s['air_m3_atm'] += gas * p_abs / p_atm
            L['gas_formed_m3'] = L.get('gas_formed_m3', 0.0) + gas
        if out > 0.0:
            liq_out = min(out, s['liquid_m3'])
            s['liquid_m3'] -= liq_out
            L['delivered_m3'] += liq_out
            air_out = out - liq_out
            if air_out > 0.0:
                s['air_m3_atm'] = max(s['air_m3_atm'] - air_out * p_abs / p_atm, 0.0)
                L['air_out_m3'] = L.get('air_out_m3', 0.0) + air_out
        elif out < 0.0:
            line = p_new > 0.0                                                # a pressurised outlet line pushes its own liquid back
            draw = -out if line else min(-out, self.outlet['reservoir_liquid_m3'])
            if not line:
                self.outlet['reservoir_liquid_m3'] -= draw
            s['liquid_m3'] += draw
            L['drawn_m3'] += draw
            if -out - draw > 0.0:
                if L['air_in_m3'] == 0.0:
                    self._event('truth', 'AIR_DRAWN', '出口没有液体：抽取吸入空气')
                s['air_m3_atm'] += (-out - draw) * p_abs / p_atm
                L['air_in_m3'] += -out - draw
        self.p = p_new

    # ---- read
    def read(self, visibility='agent'):
        pc = self.plant is not None
        leds = self._leds() if pc else dict.fromkeys(('mm', 'ml', 'ul', 'min', 'hr', 'dispensed', 'withdraw', 'pumping', 'rs232'), False)
        out = {'clock.time_s': self.t, 'plant.configured': pc, 'power.on': self.power, 'syringe.diameter_m': self.diameter or 0.0,
               'program.state': dict(selected=self.sel, phases=[dict(p) if p else None for p in self.phases]),
               'run.state': self.run['state'], 'run.phase': self.run['idx'] if 1 <= self.run['idx'] <= 41 else 41,
               'pump.rate_m3_s': abs(self._motor_speed()) * (self._area_set() or 0.0) if pc else 0.0,
               'pump.direction': self._shown_direction(), 'volume.dispensed': self._dispensed_view(), 'alarm.state': self.alarm if pc else None,
               'pusher.position_m': self.x, 'collar.position_m': self.collar, 'collar.joint_m': self.collar if self.collar is not None else COLLAR_PARK,
               'syringe.state': self._syringe_view(), 'outlet.state': dict(self.outlet), 'setup.state': dict(self.setup),
               'ttl.state': dict(inputs={str(k): v for k, v in self.ttl_in.items()}, outputs={str(k): v for k, v in self.ttl_out.items()}),
               'display.text': self._display_text() if pc else ''}
        out['display.lcd_cells'] = out['display.text'].replace('G', '6')
        for k, v in leds.items():
            out[f'indicator.{k}'] = bool(v)
        if visibility != 'agent':
            s = self.syringe
            out['truth.process'] = dict(pressure_pa=self.p, backpressure_pa=self._pb() if pc else 0.0, force_n=self.force, max_force_n=max_force(abs(self._motor_speed()) or V_MIN),
                                        true_flow_m3_s=(self._area_true() * self._motor_speed()) if s else 0.0,
                                        syringe=None if s is None else dict(type=s['type'], liquid_m3=s['liquid_m3'], air_m3_atm=s['air_m3_atm'], cavitated=s['cavitated'],
                                                                            true_inside_diameter_m=SYR[s['type']]['inside_diameter_m'] * (1.0 + self.id_error)),
                                        delivered_m3=self.led['delivered_m3'] if self.led else 0.0, drawn_m3=self.led['drawn_m3'] if self.led else 0.0,
                                        resistance_pa_s_m3=self._resistance() if s else None, compliance_m3_pa=self._compliance() if s else None) if pc else {}
            out['truth.faults'] = sorted(self.faults)
        return out

    def _ledger(self):
        if self.led is None:
            return {}
        L = dict(self.led)
        L['stored_m3'] = self.vs
        if self.syringe is not None:                                          # independent closure: the barrel volume behind the plunger = its contents
            geo = self._area_true() * (X_ZERO - self.x)
            L['geometric_balance_m3'] = geo - (self.syringe['liquid_m3'] - self.vs + self._air_volume())
        else:
            L['geometric_balance_m3'] = 0.0
        L.setdefault('gas_formed_m3', 0.0)
        L['in_syringe_m3'] = self.syringe['liquid_m3'] if self.syringe else 0.0
        L['liquid_balance_m3'] = L['placed_m3'] + L['drawn_m3'] - L['delivered_m3'] - L['removed_m3'] - L['in_syringe_m3']
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
class NeweraNe1000Session:
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
        meta = copy.deepcopy(export_newera_ne1000_contract_metadata())
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
    'POWER_OFF': '电源关（或停电）', 'PROGRAM_OPERATING': '程序在运行（先停止；暂停状态不算运行）', 'ALARM_ACTIVE': '报警未清除（按任意键或经RS-232读取状态清除）',
    'DIAMETER_NOT_SET': '尚未设定注射器内径', 'OUT_OF_RANGE': '超出范围：流量对应的推进速度不在最低与最高速度之间，或阶段参数越界（泵显示oor）',
    'NOT_APPLICABLE': '当前阶段功能或状态下不适用（泵显示----）', 'UNITS_WHILE_PUMPING': '泵送中不能改流量单位', 'ALREADY_RUNNING': '程序已在运行',
    'SYRINGE_LOADED': '已装注射器（先取下；内径偏差只能在装之前设定）', 'OVERFILL': '装液量加气泡超过注射器标称容量', 'SYRINGE_TOO_LONG': '液柱超出推块行程4.74 in（只能部分装液）',
    'COLLAR_IN_THE_WAY': '限位夹挡在推块与活塞法兰之间（先移开限位夹）',
    'SYRINGE_TOO_LARGE': '注射器内径超过50 mm', 'NO_SYRINGE': '没有装注射器', 'BEHIND_PUSHER': '限位夹不能放在推块已经越过的位置', 'NOT_AN_INPUT': '针脚5是输出，不能作为输入',
}
_P = ['PLANT_REQUIRED']
_A = _P + ['POWER_OFF']
_OP = ['PROGRAM_OPERATING']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': _P, 'power.toggle': _P, 'keypad.press': _A,
    'syringe.set_diameter': _A + _OP + ['ALARM_ACTIVE'], 'rate.set': _A + ['ALARM_ACTIVE', 'NOT_APPLICABLE', 'DIAMETER_NOT_SET', 'OUT_OF_RANGE', 'UNITS_WHILE_PUMPING'],
    'volume.set': _A + _OP + ['ALARM_ACTIVE', 'NOT_APPLICABLE'], 'direction.set': _A + ['ALARM_ACTIVE', 'NOT_APPLICABLE'], 'phase.select': _A + _OP,
    'phase.set_function': _A + _OP + ['ALARM_ACTIVE', 'DIAMETER_NOT_SET', 'OUT_OF_RANGE'], 'program.clear': _A + _OP,
    'pump.run': _A + ['ALARM_ACTIVE', 'ALREADY_RUNNING', 'DIAMETER_NOT_SET'], 'pump.stop': _A, 'pump.purge': _A + _OP + ['ALARM_ACTIVE', 'DIAMETER_NOT_SET'],
    'volume.clear': _A + _OP, 'setup.configure': _A + _OP, 'serial.send': _A, 'ttl.set': _P + ['NOT_AN_INPUT'],
    'syringe.load': _P + _OP + ['SYRINGE_LOADED', 'OVERFILL', 'SYRINGE_TOO_LONG', 'SYRINGE_TOO_LARGE', 'COLLAR_IN_THE_WAY'], 'syringe.unload': _P + _OP + ['NO_SYRINGE'],
    'outlet.connect': _P + _OP, 'collar.set': _P + ['BEHIND_PUSHER'], 'events.read': [], 'truth.inspect': _P, 'ledger.read': [], 'medium.configure': _P + ['SYRINGE_LOADED'],
    'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_ON = ('电源开', ['power.on'])
_STOPPED = ('电源开；程序不在运行（停止或暂停；暂停时修改设定会取消暂停，程序回到第1阶段，手册7.7）', ['power.on', 'run.state'])
_STOPPED_NA = ('电源开；程序不在运行；无报警', ['power.on', 'run.state', 'alarm.state'])
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子（之后不可改）', ['plant.configured']),
    'power.set': ('无', ['power.on']), 'power.toggle': ('无', ['power.on']),
    'keypad.press': ('电源开（键盘锁定时除Start/Stop外的键只显示LoC-；有报警时任意键只清除报警）', ['power.on', 'setup.state', 'alarm.state']),
    'syringe.set_diameter': _STOPPED_NA,
    'rate.set': ('电源开；无报警；已设内径；当前阶段为流量功能；推进速度在范围内（运行中只能改RAT阶段的流量、不能改单位）', ['power.on', 'alarm.state', 'syringe.diameter_m', 'program.state', 'run.state']),
    'volume.set': ('电源开；程序不在运行；无报警；当前阶段为流量功能', ['power.on', 'run.state', 'alarm.state', 'program.state']),
    'direction.set': ('电源开；无报警；运行中只有连续泵送（体积为0）时可改', ['power.on', 'alarm.state', 'run.state', 'program.state']),
    'phase.select': _STOPPED, 'phase.set_function': ('电源开；程序不在运行；无报警；流量在范围内（需已设内径）', ['power.on', 'run.state', 'alarm.state', 'syringe.diameter_m']),
    'program.clear': _STOPPED, 'pump.run': ('电源开；无报警；程序不在运行；已设内径（阶段流量越界时报警or:nn而不是拒绝）', ['power.on', 'alarm.state', 'run.state', 'syringe.diameter_m']),
    'pump.stop': _ON, 'pump.purge': ('电源开；无报警；程序不在运行；已设内径', ['power.on', 'alarm.state', 'run.state', 'syringe.diameter_m']),
    'volume.clear': _STOPPED, 'setup.configure': _STOPPED, 'serial.send': _ON,
    'ttl.set': ('针脚2、3、4、6（泵关电时电平记录但不响应）', ['ttl.state']),
    'syringe.load': ('程序不在运行；未装注射器；液量加气泡不超过标称容量且液柱装得下（活塞到底位置为4.38 in）；限位夹不挡在推块要到的位置前', ['run.state', 'syringe.state', 'collar.position_m']),
    'syringe.unload': ('程序不在运行；已装注射器', ['run.state', 'syringe.state']), 'outlet.connect': ('程序不在运行', ['run.state', 'outlet.state']),
    'collar.set': ('限位夹位置不小于推块当前位置', ['pusher.position_m', 'collar.position_m']), 'truth.inspect': ('无', ['plant.configured']),
    'medium.configure': ('内径偏差只能在装注射器之前设定', ['plant.configured', 'syringe.state']), 'fault.inject': ('无', ['plant.configured']),
}
_DISPLAY = ['display.text', 'display.lcd_cells'] + [f'indicator.{k}' for k in ('mm', 'ml', 'ul', 'min', 'hr', 'dispensed', 'withdraw', 'pumping', 'rs232')]
_RUN = ['run.state', 'run.phase', 'pump.rate_m3_s', 'pump.direction', 'alarm.state', 'truth.process']
_EVOLVE = _DISPLAY + _RUN + ['pusher.position_m', 'volume.dispensed', 'ttl.state', 'outlet.state']
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.process'] + _DISPLAY,
    'power.set': ['power.on', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY, 'power.toggle': ['power.on', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY,
    'keypad.press': ['program.state', 'syringe.diameter_m', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY,
    'syringe.set_diameter': ['syringe.diameter_m', 'volume.dispensed'] + _RUN + _DISPLAY, 'rate.set': ['program.state', 'pump.rate_m3_s', 'pump.direction', 'truth.process', 'run.state', 'run.phase', 'ttl.state'] + _DISPLAY,
    'volume.set': ['program.state'] + _RUN + _DISPLAY, 'direction.set': ['program.state', 'pump.direction', 'truth.process'] + _RUN + _DISPLAY,
    'phase.select': ['program.state', 'pump.direction'] + _RUN + _DISPLAY,
    'phase.set_function': ['program.state', 'pump.direction', 'run.state', 'run.phase'] + _DISPLAY,
    'program.clear': ['program.state', 'pump.direction', 'run.state', 'run.phase'] + _DISPLAY, 'pump.run': _RUN + _DISPLAY + ['ttl.state', 'program.state', 'volume.dispensed'],
    'pump.stop': _RUN + _DISPLAY + ['ttl.state'], 'pump.purge': _RUN + _DISPLAY + ['ttl.state'], 'volume.clear': ['volume.dispensed'] + _RUN + _DISPLAY,
    'setup.configure': ['setup.state', 'ttl.state'] + _DISPLAY, 'serial.send': ['program.state', 'syringe.diameter_m', 'volume.dispensed', 'setup.state'] + _RUN + _DISPLAY + ['ttl.state'],
    'ttl.set': ['ttl.state'], 'syringe.load': ['syringe.state', 'pusher.position_m', 'truth.process'], 'syringe.unload': ['syringe.state', 'truth.process'],
    'outlet.connect': ['outlet.state', 'truth.process'], 'collar.set': ['collar.position_m', 'collar.joint_m'],
    'medium.configure': ['truth.process'], 'fault.inject': ['truth.faults', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY,
    'sim.step': ['clock.time_s'],
}
_STEPPED = ('power.set', 'power.toggle', 'keypad.press', 'rate.set', 'direction.set', 'pump.run', 'pump.purge', 'serial.send', 'ttl.set', 'syringe.load',
            'outlet.connect', 'medium.configure', 'fault.inject', 'plant.configure')
ON_STEP = {k: list(_EVOLVE) + ['program.state', 'syringe.diameter_m'] for k in _STEPPED}   # keypad edits are stored 2 s after the last arrow
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s', 'program.state', 'syringe.diameter_m']
ON_STEP_ZH = {'sim.step': '按0.1 s网格积分：推块按设定流量/内径推进（行程、限位夹、活塞到底），注射器—管路压力（顺应性与流阻的指数解），送出/抽入液量、空化，推力与失速，'
                          '程序阶段推进（体积到达、暂停定时、TTL触发≥100 ms），按键输入2 s后存入；不泵送且压力已松弛（或出口堵塞、没有流动）时整段跳过'}
EXEC_TEXT = {}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('syringe.load', 'syringe.unload', 'outlet.connect', 'collar.set', 'power.set', 'power.toggle', 'keypad.press'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'pump.run': ['indicator.pumping', 'pusher.position_m'], 'pump.stop': ['indicator.pumping'], 'power.set': ['display.text'], 'rate.set': ['display.text'],
            'keypad.press': ['display.text']}
_WB, _MAN, _DRW, _PH, _NIST, _PC, _LIB, _SIM = ('user_workbook', 'manufacturer_manual', 'manufacturer_drawing', 'manufacturer_product_page', 'nist_webbook', 'pubchem',
                                                'liquid_library', 'simulation_assumption')
CAP_SOURCES = {k: [_MAN] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_WB, _SIM], 'syringe.load': [_MAN, _DRW, _LIB, _NIST, _PC], 'syringe.unload': [_MAN, _SIM], 'outlet.connect': [_SIM],
                    'collar.set': [_MAN, _DRW], 'events.read': [_MAN, _SIM], 'truth.inspect': [_SIM], 'ledger.read': [_SIM], 'medium.configure': [_SIM],
                    'fault.inject': [_MAN, _SIM], 'sim.step': [_SIM]})
PARAM_ZH = {'ambient_k': '室温', 'pressure_pa': '大气压', 'noise_seed': '噪声种子', 'on': '开或关', 'key': '面板键', 'diameter_m': '注射器内径', 'rate_m3_s': '流量',
            'units': '流量单位', 'continue_paused': '暂停后继续', 'volume_m3': '体积', 'direction': '方向', 'phase': '阶段号', 'function': '阶段功能', 'rate_units': '流量单位',
            'parameter': '功能参数', 'tenths': '暂停按0.1 s', 'command': 'RS-232命令', 'pin': 'TTL针脚', 'level': '电平', 'syringe': '注射器（目录）', 'liquid_id': '液体（模板库）',
            'fill_volume_m3': '装液量', 'air_volume_m3': '气泡体积', 'tubing': '管路（目录）', 'tubing_length_m': '管长', 'needle': '针头（目录）', 'backpressure_pa': '下游背压（表压）',
            'reservoir_liquid_m3': '出口储液量', 'position_m': '限位夹位置', 'since_s': '起始时间', 'syringe_id_error': '注射器内径相对偏差', 'clear_backpressure': '取消背压扰动', 'fault': '故障名',
            'active': '注入或清除', 'dt_s': '推进时间', 'power_failure': '断电重启', 'low_noise': '低噪声', 'alarm': '报警蜂鸣', 'beep': '提示音',
            'keypad_lockout': '键盘锁', 'trigger': 'TTL触发方式', 'direction_input': '方向输入方式', 'motor_output': '电机TTL输出方式'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.syringe_pump_process.limitations。'
CAP_LIMITS = {
    'medium.configure': ['评测者扰动：真实内径偏差只能在装注射器前设定；下游背压扰动一直有效（代理重新连接出口也不取消，outlet.state仍显示代理声明的背压），直到评测者再次设定或以clear_backpressure=true取消。'],
    'keypad.press': ['手册第6章键盘逻辑：方向键使对应数位加1（9后回0）、2 s无操作或按非方向键后存入；显示流量时再按Rate进入单位修改（UntS，方向键轮换单位）；暂停时除Start外任意键取消暂停，PS:00时除Start外任意键复位程序；'
                     '键盘锁定（未插锁定解除键=TTL针脚6未接地）时只能启停与查看设定；按住类操作（Rate按住进入程序录入、同按两键设小数点）与Setup菜单由phase.*、volume.clear与setup.configure能力代替。'],
    'serial.send': ['Basic协议命令集（DIA、RAT（含C/I前缀）、VOL（含UL/ML）、DIR、RUN（含RUN E）、STP、PUR、DIS、CLD、FUN、PHN、LOC、BUZ、TRG、DIN、ROM、AL、PF、LN、BP、OUT5、IN、VER、RESET）；运行中经RS-232改的流量不存入阶段（手册6.1）；Safe模式、地址网络、LOC P与DIR STK为unsupported或?NA；回应不含帧字节（STX/ETX）。'],
    'syringe.load': ['注射器按目录（手册12.7表）给内径与容量，筒长按行程推算；装液量超出4.74 in行程时只能部分装（手册：140 mL注射器部分装液）；气泡按大气压体积给出。'],
    'outlet.connect': ['管路与针头按Hagen–Poiseuille层流流阻（目录工程值）；下游为恒定背压；不建模多相流与管路惯性。'],
    'ttl.set': ['TTL输入按手册7.5：电平须保持≥100 ms才被识别；触发方式FT/FH/F2/LE/ST/T2/SP/P2/RL/RH/SL/SH/OF。'],
}
LIMITATIONS = [
    '所选配置：New Era NE-1000单通道可编程注射泵（X系列之前的标准NE-1000，12 V DC外部电源），装一支注射器；手册（NE-1000 Syringe Pump User Manual）为主来源，厂家尺寸图Rev A给外形与行程。',
    '驱动：步进电机加丝杠，推进速度=流量/设定截面积；最高5.1005 cm/min、最低0.004205 cm/hr；最大推力35 lbf（最低速）到18 lbf（最高速），中间按速度对数插值（工程）；微步与步进脉动未建模（连续推进）。',
    '流体：注射器+管路+针头为一个集总顺应性C（塑料/玻璃/钢注射器形变、管壁形变、液体压缩、气泡按理想气体）与一个层流流阻R；压力按p_ss=p_back+R·Q的指数解逐0.1 s积分，'
    '因此塑料注射器、气泡与高流阻管路会出现启动滞后与停止后的拖尾流；顺应性与摩擦为工程值。',
    '失速：推力（压力×真实截面积+摩擦）超过速度对应的最大推力持续0.2 s（工程去抖）、推块碰到限位夹、活塞推到底或拉到注射器刻度末端时报警StAL并暂停程序；本型号没有压力传感器。',
    '抽取时管路内压力低于空化绝对压力（20 kPa，工程）时析出气泡，气泡留在注射器中；出口储液不足时吸入空气。',
    '已排体积按设定内径计算（泵自己的计算值），真实送出液量按真实内径（评测者可设内径偏差）、顺应性与气泡计算，两者可以不同。',
    '程序：41阶段，功能RAT/FIL/INC/DEC/STP/JMP/LPS/LOP/LPE/PAS/IF/EVN/EVS/EVR/CLD/BEP/OUT按手册第8章；子程序选择与Safe协议unsupported。',
    '显示：4位七段LCD（含小数点）与8个LED（mm、mL、µL、min、hr、Dispensed、Withdraw、Pumping）加RS-232接收指示；文字StAL、oor、or:nn、Er:nn、----、LoC-、UntS、PAUS、 oFF、PurG、PS:nn按手册6.14/6.15字形（冒号不计位）；'
    'LED闪烁（暂停时Pumping闪烁、单位修改时单位LED闪烁）表示为点亮或熄灭，不建模闪烁。',
    '时间：运行层按0.1 s网格积分；两个格点之间发出的命令从该格的起点起作用（最多提前0.1 s），该格内产生的事件记在格的终点。',
    '场景：红色楔形机身、斜面键盘（9个弹簧键）、LCD、导杆与丝杠、推块（棱柱关节，随pusher.position_m）、注射器夹、限位夹（棱柱关节）、后部电源开关有几何；注射器内液体与管路没有几何（运行层状态）。'
    'Matterix的BridgedJointSync默认“变化”阈值为2 mm（=推块约1.1 mL），场景验证把本资产的阈值设为0.02 mm（matterix_scene.py的cfg_asset）；用默认阈值时推块按2 mm台阶跟随。',
]


def _sampling_text(k):
    if k == 'clock.time_s':
        return 'Host clock.'
    if SLOTS[k]['origin'] in ('sensor', 'derived', 'process_truth'):
        return 'Updated on the 0.1 s grid of the host steps and at once when a command changes it (display values as the pump shows them).'
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
        """Contract parameters in SI: in -> m (the source value is noted in the meaning)."""
        if unit != 'in':
            return name, val, unit, ''
        f = lambda x: x * IN
        val2 = {kk: f(x) for kk, x in val.items()} if isinstance(val, dict) else f(val)
        return (name[:-5] + '_m' if name.endswith('_inch') else name + '_m'), val2, 'm', f'（换算为SI；资料原值{val} in）'

    def doc(name, val, unit, meaning, src):
        name, val, unit, extra = si(name, val, unit)
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning + extra, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_manual': _MAN, 'manufacturer_drawing': _DRW, 'manufacturer_product_page': _PH}
    WB_KEYS = ('dimensions_inch', 'mass_kg', 'features_rate_range', 'syringe_sizes', 'program_phases')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
    for k in sorted(ENG):
        ev = instrument()['engineering'][k]['evidence']
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '已标定，模型自洽，不是实测' if ev == 'calibrated' else
            '由厂家手册推得' if ev in ('drawing', 'documented_derived') else '工程值（见instrument_model.json的engineering说明），不是实测')
    for k, v in instrument()['derived'].items():
        asm(k, v['value'], v['unit'], '由厂家数据推导：' + v['note_zh'], '推导值', (_MAN,))
    st = _lib('syringes')
    asm('syringe_templates', dict(file='assets/expansion_batch05/t5_automation/newera_ne1000/syringe_templates.json', counts=st['counts']), '1',
        '注射器目录（手册12.7表：厂家、容量、内径、最大/最小流量）与液体模板（CHEM-008液体模板库，NIST/PubChem）；每条带来源', '文献与厂家数据；适用规则见模板库rules_zh',
        (_MAN, _NIST, _PC, _LIB))
    return P


SLOT_REFERENCE = {'pump.rate_m3_s': '泵按设定内径计算的流量，不是真实流量（见truth.process.true_flow_m3_s）',
                  'volume.dispensed': '泵按设定内径计算的已排体积'}


def export_newera_ne1000_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'NeweraNe1000Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.newera_ne1000.NeweraNe1000Session.invoke',
                                              invocation=f'session.invoke(instance_id="newera_ne1000_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='NeweraNe1000Runtime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（推块、压力与程序在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['chassis', 'keypad_membrane', 'lcd', 'drive_screw', 'guide_rod_rear', 'guide_rod_front', 'pusher_block', 'holder_block', 'guide_collar', 'power_switch',
                       'syringe_barrel', 'plunger'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='步进电机经齿轮与丝杠带动推块，按设定流量/设定内径的速度推或拉注射器活塞；液体经注射器嘴、管路与针头流出或吸入；'
                   '注射器、管路、液体与气泡的顺应性和流道流阻决定压力与真实流量；推力超过电机能力或碰到机械限位时失速报警；41阶段程序控制流量、体积、方向与暂停',
        equations=['v = Q_set / A_set (purge: v = v_max);  dx/dt = v  (limited by travel, collar, plunger bottom-out)',
                   'Q_in = A_true v;  C dp/dt = Q_in - (p - p_back) / R  =>  p(t+h) = p_ss + (p - p_ss) exp(-h / RC),  p_ss = p_back + R Q_in',
                   'C = c_syr V_nom + C_tube + V_liq / K + V_air / p_abs;  R = sum 8 mu L / (pi r^4)',
                   'F = p A_true + F_fric sign(v);  stall if |F| > F_max(v) for 0.2 s;  F_max = interp(log v; 35 lbf at v_min, 18 lbf at v_max)',
                   'dispensed += A_set |dx|  (pump display);  delivered = integral of (p - p_back) / R dt  (truth)'],
        parameters=_params(), source_ids=[_SIM, _WB, _MAN, _DRW, _NIST, _PC, _LIB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_newera_ne1000_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('newera_ne1000', caps, slots, {'syringe_pump_process': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'syringe.load': (LOAD, CAPS['syringe.load']['example_arguments'])})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'syringe_pump_process': mechanism})
