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
import binascii
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
FUNCS = ['RAT', 'FIL', 'INC', 'DEC', 'STP', 'JMP', 'LPS', 'LOP', 'LPE', 'PAS', 'IF', 'EVN', 'EVS', 'EVR', 'CLD', 'BEP', 'OUT', 'PRI', 'PRL', 'TRG']
RATE_FUNCS = ('RAT', 'FIL', 'INC', 'DEC')
UNIT_FACTOR = {'UM': 1e-9 / 60.0, 'MM': 1e-6 / 60.0, 'UH': 1e-9 / 3600.0, 'MH': 1e-6 / 3600.0}
KEYS = ['up1', 'up2', 'up3', 'up4', 'rate', 'volume', 'diameter', 'direction', 'start']
ARROWS = KEYS[:4]
ASSET_VERSION = '1.2.1'
# front-panel firmware (1.1.0; manual 6.4-6.13, 8, 9.1-9.2): the program functions in keypad order with their LCD names, operational trigger
# settings (FUN TRG <n> numbering; Et / bt only inside a program), baud rates, communications modes and the documented default program
FUNC_KEYPAD = [('RAT', 'rAtE'), ('FIL', 'FILL'), ('INC', 'InCr'), ('DEC', 'dECr'), ('STP', 'StoP'), ('JMP', 'JP:'), ('PRI', 'Pr:In'), ('PRL', 'Pr:'),
               ('LPS', 'LP:St'), ('LPE', 'LP:En'), ('LOP', 'LP:'), ('PAS', 'PS:'), ('IF', 'IF:'), ('EVN', 'Et:'), ('EVS', 'ES:'), ('EVR', 'Et:rS'),
               ('CLD', 'CLr.d'), ('TRG', 'tr:'), ('OUT', 'OUt.'), ('BEP', 'bEEP')]
FUNC_PARAM = {'JMP': (1, 41, 1), 'IF': (1, 41, 1), 'EVN': (1, 41, 1), 'EVS': (1, 41, 1), 'LOP': (1, 99, 1), 'PAS': (0, 99, 0), 'PRL': (0, 99, 0),
              'OUT': (0, 1, 0), 'TRG': (0, 14, 0)}                                 # (min, max, default) of the parameter shown after the name
TRIGGERS = ['FT', 'FH', 'F2', 'LE', 'ST', 'T2', 'SP', 'P2', 'RL', 'RH', 'SL', 'SH', 'OF', 'ET', 'BT']
TRIGGER_LCD = {'FT': 'Ft', 'FH': 'FH', 'F2': 'F2', 'LE': 'LE', 'ST': 'St', 'T2': 't2', 'SP': 'SP', 'P2': 'P2', 'RL': 'rL', 'RH': 'rH', 'SL': 'SL', 'SH': 'SH',
               'OF': 'oF', 'ET': 'Et', 'BT': 'bt'}
BAUDS = [300, 1200, 2400, 9600, 19200]
COMM_MODES = [('ADR', 'Addr'), ('RECP', 'rECP'), ('DUAL', 'dUAL'), ('ALTR', 'ALtr')]
FIRMWARE_LCD = 'r3.92'                                                      # manual 6.13.1 'r n.nn' (firmware V3.923)
LN_STEP_M = 0.8504e-6                                                     # manual 12.5: advance per step 0.2126-0.8504 um (1/8-1/2 micro-stepping); 8.2 Low Noise = coarse half steps
STX, ETX = '\x02', '\x03'                                                  # RS-232 Safe protocol packet framing (manual 10.2.3-10.2.4)
HOLD_S = 1.0                                                                # 'press and hold' (manual 6.4.1, 6.7.2, 6.9: one second)
REPEAT_DELAY_S, REPEAT_PERIOD_S = 0.5, 0.25                                 # auto-repeat of a held arrow key (not given: assumption)
DEFAULT_PROGRAMS = {'RECP': [dict(function='RAT', rate_m3_s=500e-6 / 3600.0, rate_units='MH', volume_m3=10e-6, direction='WDR', parameter=0, tenths=False),
                             dict(function='FIL', rate_m3_s=0.0, rate_units='MH', volume_m3=0.0, direction='INF', parameter=0, tenths=False),
                             dict(function='JMP', rate_m3_s=0.0, rate_units='MH', volume_m3=0.0, direction='INF', parameter=1, tenths=False)]}
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
FAULTS = ['mains_outage', 'occlusion', 'motor_failure', 'nv_memory_invalid', 'nv_memory_failed']   # 1.2.0: power-up memory errors Err.1 / Err.2 (manual 6.14)
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
    '评测者：注入或清除故障（停电、出口堵塞、电机失效；1.2.0：存储器数据无效（下次开机Err.1并复位为出厂值，一次性）、存储器损坏（清除前每次开机Err.2并复位为出厂值），手册6.14）',
    'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), ['chassis'], '推进宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'pressure.measure': '压力测量：本型号没有压力传感器（失速只按推力判断）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['chassis'], _v)

# ---------------------------------------------------------------- RS-232 commands of 1.2.0 (sync audit): the Safe protocol (1.0.0 placeholder
# serial.safe_mode), the computer's serial-port baud rate and the packets the pump sends on its own (Safe-mode auto-alarm)
SERIAL_CAPS = {}


def scap(k, s, o, e, c, meaning, kind='device_command'):
    SERIAL_CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility='agent', kind=kind)


scap('serial.safe_mode', obj(dict(command=TEXT)), obj(dict(response=TEXT, data=nullable(TEXT), crc_ok=nullable(BOOL))), dict(command='SAF10'),
     ['rs232_to_computer'], '经RS-232按Safe协议发送一条命令（手册10.2.3：STX、长度、命令、CCITT CRC-16、ETX）：泵在Basic模式也接受Safe包；SAF n（n=1–255 s）开启Safe模式与通讯'
     '超时、SAF0回到Basic；回应原样返回（response），能解包时给出数据与CRC是否正确')
scap('serial.configure_port', obj(dict(baud_rate=integer(300, 19200))), obj(dict(baud_rate=integer(300, 19200))), dict(baud_rate=19200), ['rs232_to_computer'],
     '计算机（本会话）串口的波特率（默认19200）：与泵的波特率不同则泵收不到有效字符、没有回应（手册10.1：每台泵须与计算机同一波特率）', 'configuration')
scap('serial.receive', obj({}), obj(dict(packets=array(TEXT, 0, 100))), {}, ['rs232_to_computer'],
     '读取并清空计算机串口收到的、泵主动发出的包（Safe模式的自动报警：复位A?R、失速A?S、程序错误A?E/A?O、通讯超时A?T；波特率不同时收不到）')


def _safe_pack(data):
    """Safe-protocol packet of the response / command data: STX, length (bytes after STX, itself included), data, CCITT CRC-16 (high, low), ETX."""
    crc = binascii.crc_hqx(data.encode('latin-1'), 0)
    return STX + chr(len(data) + 4) + data + chr(crc >> 8) + chr(crc & 0xFF) + ETX


def _safe_unpack(packet):
    """The data of a Safe-protocol packet, or None when it is corrupted (length, CRC or framing)."""
    if len(packet) < 5 or packet[0] != STX or packet[-1] != ETX or any(ord(ch) > 255 for ch in packet) or ord(packet[1]) != len(packet) - 1:
        return None
    data = packet[2:-3]
    return data if (ord(packet[-3]) << 8 | ord(packet[-2])) == binascii.crc_hqx(data.encode('latin-1'), 0) else None


# ---------------------------------------------------------------- front-panel firmware commands (1.1.0: kept out of CAPS, which stays the 1.0.0 command
# set; sub-program selection, trigger override, sticky direction, program entry lockout and the pump network were unsupported in 1.0.0)
KEYPAD_CAPS = {}


def kcap(k, s, o, e, c, meaning, kind='device_command'):
    KEYPAD_CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility='agent', kind=kind)


KEY_OUT = obj(dict(display=TEXT, mode=TEXT))
kcap('keypad.hold', obj(dict(key=enum(KEYS), duration_s=number('s', HOLD_S, 60.0))), KEY_OUT, dict(key='diameter', duration_s=1.5), ['keypad_membrane'],
     '按住面板键≥1 s：Rate→程序录入（PH:nn）、Volume→程序录入（功能）、Diameter→Setup序列、方向键→粘滞方向、Start/Stop→按住期间清洗（1 s后最高速，松开停止）、'
     '方向键→连续加数（最左键越过9后移小数点）、显示已排体积时清零、程序录入中按住Rate回第1阶段（手册6.4–6.12）')
kcap('keypad.chord', obj(dict(keys=array(enum(ARROWS), 2, 2))), KEY_OUT, dict(keys=['up2', 'up3']), ['keypad_membrane'],
     '同时按两个相邻方向键：在它们之间设或清小数点（手册6.4.1；PS:n.n的0.1 s暂停同理）')
kcap('power.on_with_key', obj(dict(key=enum(KEYS))), obj(dict(on=BOOL, display=TEXT)), dict(key='up1'), ['power_switch', 'keypad_membrane'],
     '按住一个键接通电源（手册6.13）：最左方向键→固件版本r3.92，最右方向键→复位程序rESt（并解除键盘锁与体积单位覆盖），Volume→默认程序菜单rECP，'
     'Diameter→程序录入锁定P-L.n；任一键都阻止断电重启模式的自动运行（手册8.1）')
kcap('direction.sticky', obj({}), obj(dict(direction=TEXT)), {}, ['key_direction'],
     '粘滞方向（DIR STK，手册6.8.1）：当前阶段沿用上一泵送阶段的方向，第一阶段按TTL针脚3电平与方向输入方式定；Withdraw LED闪烁；程序停止时', 'configuration')
kcap('program.edit', obj(dict(operation=enum(['insert', 'delete']))), obj(dict(phase=integer(1, 41))), dict(operation='insert'), ['keypad_membrane'],
     '程序编辑In/dE（手册9.2）：在当前阶段插入（后面阶段后移，第41阶段丢弃；插入的为第1阶段RATE、否则STOP）或删除（后面阶段前移）；跳转类参数随之调整；程序停止时',
     'configuration')
kcap('program.entry_lockout', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['keypad_membrane'],
     '程序录入模式锁定P-L.n（手册6.13.4）：开启后键盘不能进入程序录入，只能改第1阶段的流量、体积与方向；已有多阶段程序时不能开启', 'configuration')
kcap('program.load_default', obj(dict(program=enum(sorted(DEFAULT_PROGRAMS)))), obj(dict(phases=integer(0, 41))), dict(program='RECP'), ['keypad_membrane'],
     '载入默认程序（手册6.13.3）：RECP往复程序（RATE 500 mL/hr 10 mL抽取、FILL、JP:01）并把RS-232通讯模式设为往复；覆盖原程序', 'configuration')
kcap('program.subprogram', obj(dict(label=integer(0, 99))), obj(dict(state=TEXT)), dict(label=1), ['key_start'],
     '子程序选择（Pr:In等待时，手册9.3.7）：按标签Pr:nn查找（从当前阶段到末尾再从第1阶段），找到后已排体积清零并从标签之后的阶段继续；找不到时拒绝（泵显示oor）')
kcap('network.address', obj(dict(address=integer(0, 99), baud_rate=integer(300, 19200), mode=enum([m for m, _ in COMM_MODES])), optional=('address', 'baud_rate', 'mode')),
     obj(dict(network=FREE_OBJ)), dict(address=0, baud_rate=19200), ['rs232_to_computer', 'rs232_to_network'],
     'RS-232泵网络（Setup的Ad:nn与波特率、*ADR，手册8.9）：地址0–99（泵只回应自己地址的命令）、波特率300/1200/2400/9600/19200、通讯模式Addr/rECP/dUAL/ALtr'
     '（后三种需要第二台泵：仿真中不存在，泵只显示寻找第二台泵的提示）', 'configuration')
kcap('ttl.override_trigger', obj(dict(trigger=enum(TRIGGERS))), obj(dict(trigger=TEXT)), dict(trigger='ET'), ['ttl_io'],
     '程序运行中覆盖运行触发方式（tr:aa，手册9.3.18）：FT…OF按setup含义直到程序停止；ET把一次停止触发、BT把一次Stop键重定向到事件陷阱（无陷阱时到下一阶段）')
kcap('volume.set_units', obj(dict(units=enum(['UL', 'ML']))), obj(dict(units=TEXT)), dict(units='UL'), ['keypad_membrane'],
     '体积单位覆盖（UntS，手册7.1.2）：已排体积与待排体积改用µL或mL，不再随内径改变，直到复位；程序停止时', 'configuration')



def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.newera_ne1000 (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import importlib.util
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


_panel_ui = _panel_engine()
PANEL_SPEC = _panel_ui.load_spec(ASSET_DIR / 'panel_ui.json', capabilities=set(CAPS) | set(KEYPAD_CAPS))
PANEL_CAPS = _panel_ui.capability_definitions(PANEL_SPEC)                    # panel.press / panel.switch / panel.capture / panel.frame
ALL_CAPS = {**CAPS, **KEYPAD_CAPS, **SERIAL_CAPS, **PANEL_CAPS}

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
slot('alarm.state', nullable(enum(['stall', 'program_error', 'out_of_range', 'comm_timeout'])), '1', 'derived', 'lcd', '报警：失速、程序错误、超出范围、Safe模式通讯超时r232（1.2.0）（开机复位只在RS-232的第一个回应A?R中报告）')
slot('pusher.position_m', number('m', 0.0, TRAVEL), 'm', 'derived', 'pusher_block', '推块位置（离末端板的行程，0–4.74 in；推块关节=该值）')
slot('collar.position_m', nullable(number('m')), 'm', 'setpoint', 'guide_collar', '限位夹位置（推块右端面碰到它的行程值；None为推到注射器夹块旁停放，行程4.46 in）')
slot('syringe.state', nullable(FREE_OBJ), '1', 'setpoint', 'holder_block', '已装注射器：型号、液体（看得到的刻度液量见truth）')
slot('outlet.state', FREE_OBJ, '1', 'setpoint', 'holder_block', '出口：管路、长度、针头、背压')
slot('setup.state', FREE_OBJ, '1', 'setpoint', 'keypad_membrane', '设置：断电重启、低噪声、报警、键盘锁、提示音、TTL触发方式等')
slot('firmware.state', FREE_OBJ, '1', 'setpoint', 'keypad_membrane', '面板固件设定（1.1.0）：程序录入锁定P-L、体积单位覆盖（None按内径）、RS-232网络（地址、波特率、通讯模式）；1.2.0：Safe通讯模式超时（0为Basic模式）')
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


def _func_lcd(code, param, tenths):
    """LCD text of a program function and its parameter (manual 9.3): 'rAtE', 'JP:05', 'PS:0.5', 'tr:Ft', 'OUt.1', 'Pr:In' ..."""
    name = dict(FUNC_KEYPAD)[code]
    if code == 'TRG':
        return name + TRIGGER_LCD[TRIGGERS[param % len(TRIGGERS)]]
    if code == 'OUT':
        return name + str(param % 10)
    if code == 'PAS' and tenths:
        return f'{name}{param // 10 % 10}.{param % 10}'
    if code in FUNC_PARAM:
        return f'{name}{param % 100:02d}'
    return name


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
        self.ui = dict(mode='rate', edit=None, edit_t=None, message=None, units_edit=None, units_t=None, prog=None, setup=None, special=None, vunits=None,
                       flash_t=None, purge=None, search_until=None)
        self.entry_lockout = False                                           # P-L.n (manual 6.13.4)
        self.network = dict(address=0, baud_rate=19200, mode='ADR')          # Ad:nn, baud rate, communications mode (manual 8.9)
        self.safe_timeout = 0                                                # 1.2.0: SAF time-out in s (0 = Basic protocol), non-volatile (manual 10.2.3)
        self.safe_armed, self.safe_last_t = False, 0.0                       # the time-out runs from the last valid packet once armed
        self.host_baud = 19200                                               # the computer's serial port (serial.configure_port)
        self.serial_rx = []                                                  # packets the pump sent on its own and the computer received (serial.receive)
        self.ln_lag = 0.0                                                    # Low Noise: commanded travel not yet made by a whole half step (m)
        self.run_owner = None                                                # 1.2.0: principal whose call started the Pumping Program or purge
        self._restart_blocked = False
        self.power_epoch = 0                                                 # power-ups seen by the panel
        self.panel_capture_dir = None                                        # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)
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
        self.panel = _panel_ui.PanelUI(PANEL_SPEC, self)

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

    def _guard_run(self):
        """Lead rule 10-19 (1.2.0): an agent may not stop, pause, resume, reset or change (rate, direction, trigger override, sub-program, settings
        that cancel a pause) another agent's Pumping Program while it operates or is paused - from the keypad, RS-232 or a direct call alike, because
        all of them run with the session caller in _caller.  TTL inputs (external equipment, acted on by the host clock), the evaluator and unowned
        runs are not restricted.  1.2.1: the rear power switch (and power.set / power.toggle, which model it) is a hard switch - a physical action
        that is never refused; switching off acts as a power loss."""
        c, o = self._caller, self.run_owner
        if (self._operating() or self.run['state'] == 'paused') and c is not None and self._principals.get(c) == 'agent' and o is not None and o != c:
            raise ActionRejected('ACCESS_DENIED')

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
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'panel.capture', 'panel.frame'):
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

    def _nv_reset(self):
        """Err.n at power-up (manual 6.14): the values stored in the non-volatile memory were invalid and were reset to the factory settings."""
        self.phases = [_default_phase()] + [_empty_phase() for _ in range(40)]
        self.sel = 1
        self.diameter = None
        self.setup = dict(power_failure=False, low_noise=False, alarm=True, beep=True, keypad_lockout=False, trigger='FT', direction_input=0, motor_output=0)
        self.network = dict(address=0, baud_rate=19200, mode='ADR')
        self.safe_timeout = 0
        self.entry_lockout = False
        self.vol_units_override = None
        self.run['was_running'] = False

    def _auto_alarm(self, code):
        """Safe mode: the pump transmits a status packet by itself when an alarm occurs (manual 10.2.3); it does not acknowledge the alarm."""
        if self.safe_timeout > 0 and self._on() and self.host_baud == self.network['baud_rate']:
            self.serial_rx = (self.serial_rx + [_safe_pack(f'{self.network["address"]:02d}A?{code}')])[-100:]

    def _comm_timeout(self):
        """Safe-mode communications time-out (manual 10.2.3 / 6.14 r232): alarm, the pump and the Pumping Program stop; the timer waits for the next valid packet."""
        self.safe_armed = False
        self.alarm = 'comm_timeout'
        self.alarm_phase = self.run['idx']
        self._reset_program()
        self.ttl_out[7] = 0
        self._event('alarm', 'COMM_TIMEOUT', f'Safe模式通讯超时（{self.safe_timeout} s内没有有效包）：泵与程序停止，显示r232' + ('、蜂鸣' if self.setup['alarm'] else ''))
        self._auto_alarm('T')

    def _power_up(self):
        nv = 2 if 'nv_memory_failed' in self.faults else 1 if 'nv_memory_invalid' in self.faults else None
        if nv:                                                                # manual 6.14: Err.1 values invalid and reset; Err.2 memory may need replacing
            self._nv_reset()
            self.faults.discard('nv_memory_invalid')
            self._event('alarm', f'ERR_{nv}', f'开机检测到存储器错误Err.{nv}：存储的设定无效，已复位为出厂值' + ('（存储器可能需要更换）' if nv == 2 else ''))
        self.safe_armed = False                                               # manual 10.2.3: the time-out starts with the first valid packet
        self.dispensed = {'INF': 0.0, 'WDR': 0.0}                            # manual 7.6 F: cleared when the pump is powered on
        self.rs232_seen = False
        self.ui.update(mode='rate', edit=None, edit_t=None, message=None, units_edit=None, units_t=None, prog=None, setup=None, special=None, vunits=None,
                       flash_t=None, purge=None, search_until=self.t + 2.0 if self.network['mode'] != 'ADR' else None)   # rECP / dUAL: searching (manual 6.15)
        self.power_epoch += 1
        self.alarm = None                                                     # alarms do not survive a power cycle (the power-up reset is reported instead)
        self.over_force_s = 0.0
        restart = self.run.get('was_running') and self.setup['power_failure'] and not self._restart_blocked
        self.run.update(state='stopped', idx=1, loops=[], trap=None, rate=None, purge=False, timer=0.0)
        self._power_reset_alarm = True                                       # RS-232 status 'A?R' until read
        if nv:
            self.ui['special'] = dict(kind='nv_error', val=nv, val_t=None)   # Err.n until any key
        self._auto_alarm('R')                                                 # Safe mode: the reset alarm is sent at once
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
        self._guard_run()
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
        self._guard_run()
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
        new = {'INF': 'INF', 'WDR': 'WDR', 'REV': 'WDR' if self._resolve_dir(ph['direction'], first=True) == 'INF' else 'INF'}[direction]
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
            if function == 'TRG' and not 0 <= p < len(TRIGGERS):
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
        self._guard_run()
        self.run.update(state='stopped', idx=1, loops=[], trap=None, rate=None, purge=False, timer=0.0, resume='running', sub=None, trg=None, redirect=None)

    def _start_program(self, phase=1):
        if self.diameter is None:
            raise ActionRejected('DIAMETER_NOT_SET')
        first = self.phases[int(phase) - 1]
        self.run_owner = self._caller
        self.run.update(state='running', idx=int(phase), loops=[], trap=None, rate=None, purge=False, resume='running', sub=None, trg=None, redirect=None,
                        direction=self._resolve_dir(first['direction'], first=True) if first and first['function'] in RATE_FUNCS else 'INF')   # no stale direction
        if self.network['mode'] != 'ADR':
            self.ui['search_until'] = self.t + 2.0                            # rECP / dUAL / ALtr: looking for the secondary pump (absent)
        self._event('operation', 'RUN', f'程序从第{phase}阶段开始')
        self._enter_phase()

    def _trigger_start(self, phase=None):
        st = self.run['state']
        if st != 'stopped':
            self._guard_run()                                                 # resuming / continuing / restarting a program belongs to its owner
        self.ui.update(units_edit=None, units_t=None)                         # a start drops an uncommitted units change (the keypad commits first)
        if phase is not None:
            self._start_program(phase)                                        # RUN <n>: start at phase n whatever the state
        elif st == 'paused':
            self.run['state'] = self.run.get('resume', 'running')            # back into what the stop interrupted (pumping, PS:nn with its time left, PS:00)
            self._event('operation', 'RESUME', '程序继续')
        elif st == 'user_wait' and self.run.get('sub') is not None:
            if not self._select_subprogram(self.run['sub']):
                self.ui['message'] = 'oor'                                    # manual 9.3.7: label not found
        elif st == 'user_wait':
            self.run['idx'] += 1
            self.run['state'] = 'running'
            self._enter_phase()
        elif st == 'stopped':
            self._start_program(1)

    def _trigger_stop(self):
        self._guard_run()
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
            self.run_owner = self._caller
            self._event('operation', 'PURGE', '清洗：最高速泵送')
        elif self.run['purge']:
            self._trigger_stop()
        return dict(state=self.run['state'])

    def _program_error(self, kind, why):
        self.alarm = kind
        self.alarm_phase = self.run['idx']
        self._reset_program()
        self._event('alarm', 'PROGRAM_ERROR' if kind == 'program_error' else 'OUT_OF_RANGE', why)
        self._auto_alarm('E' if kind == 'program_error' else 'O')

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
                    rate, direction, vol = ph['rate_m3_s'], self._resolve_dir(ph['direction']), ph['volume_m3']
                elif f in ('INC', 'DEC'):
                    if r['rate'] is None:
                        self._program_error('program_error', f'第{r["idx"]}阶段INCR/DECR没有基准流量')
                        return
                    rate = r['rate'] + (ph['rate_m3_s'] if f == 'INC' else -ph['rate_m3_s'])
                    direction, vol = self._resolve_dir(ph['direction']), ph['volume_m3']
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
            if f == 'PRI':                                                   # manual 9.3.7: wait for the user's sub-program selection
                r.update(state='user_wait', sub=ph['parameter'])
                self._event('operation', 'SUBPROGRAM_WAIT', '子程序选择：等待输入标签并按Start（Pr:nn）')
                return
            if f == 'PRL':                                                   # manual 9.3.8: a label met in normal execution is JP:01
                r['idx'] = 1
                continue
            if f == 'TRG':                                                   # manual 9.3.18: override the operational trigger / trap redirect
                n = TRIGGERS[ph['parameter'] % len(TRIGGERS)]
                r['redirect' if n in ('ET', 'BT') else 'trg'] = n
                r['idx'] += 1
                continue
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
        if ui.get('special'):                                                 # power-up displays (manual 6.13)
            return self._special_key(key)
        if ui.get('setup'):
            return self._setup_key(key)
        if ui.get('prog'):
            return self._prog_key(key)
        if self.run['state'] == 'user_wait' and self.run.get('sub') is not None:
            return self._sub_key(key)
        if ui.get('vunits'):                                                  # manual 7.1.2: Volume Units change (UntS)
            if key in ARROWS:
                ui['vunits'].update(units='ML' if ui['vunits']['units'] == 'UL' else 'UL', t=self.t)
            else:
                self._vunits_store()
            return self._out()
        locked = self._locked()
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
                if not self._operating() and self.dispensed['INF'] == 0.0 and self.dispensed['WDR'] == 0.0:
                    if self.run['state'] == 'paused':
                        self._reset_program()
                    ui['vunits'] = dict(units=self._vol_units(), t=self.t)   # manual 7.1.2: at 0.000 an arrow enters the Volume Units change
                    return self._out()
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
            if self.run['state'] in ('running', 'pause_timer') and self.run.get('redirect') == 'BT':
                self._redirect_event()                                        # manual 9.3.18.1: bt redirects the Stop key to the event trap
            elif self.run['state'] in ('running', 'pause_timer', 'user_wait', 'purging') and self.run['state'] != 'user_wait':
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
        self._flash()
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
            self._flash()
            self._event('operation', 'VALUE_STORED', f'{ui["mode"]} = {txt}（显示闪烁一次）')
        except ActionRejected:
            ui['message'] = 'oor'

    # ---- front-panel firmware 1.1.0 (manual 6.4-6.13, 7.1.2, 7.4, 8, 9.1-9.2): long presses, the two-key decimal point, power-up keys, the Setup
    #      sequence, Program Entry Mode, volume units, sticky direction and sub-programs.  One state machine with the keypad above (self.ui), stepped
    #      by _tick (2 s stores, Setup advance, the purge hold); the LCD text with the manual's colons is _lcd_text().
    def _locked(self):
        return self.setup['keypad_lockout'] and self.ttl_in[6] != 0              # manual 8.7: the Lockout Disable Key grounds TTL pin 6

    def _fw_screen(self):
        ui = self.ui
        return 'special' if ui.get('special') else 'setup' if ui.get('setup') else 'program' if ui.get('prog') else 'run'

    def _flash(self):
        self.ui['flash_t'] = self.t                                           # manual 6.1: the display blinks when a new value is stored

    def _out(self):
        return dict(display=self._display_text(), mode=self.ui['mode'])

    def _resolve_dir(self, d, first=False):
        """Sticky direction (manual 6.8.1): the direction of the previous pumping phase, or for the first one the level of TTL pin 3 (dr:rE low =
        infuse, high = withdraw; dr:dU the reverse)."""
        if d != 'STK':
            return d
        if not first and self.run.get('rate') is not None:
            return self.run['direction']
        low = self.ttl_in[3] == 0
        return ('INF' if low else 'WDR') if self.setup['direction_input'] == 0 else ('WDR' if low else 'INF')

    def _repeats(self, duration_s):
        return 1 + max(0, int(math.floor((duration_s - REPEAT_DELAY_S) / REPEAT_PERIOD_S + 1e-9)))

    def _cmd_keypad_hold(self, key, duration_s):
        self._need_on()
        ui = self.ui
        if self.alarm is not None or ui['message'] or ui.get('special') or ui.get('vunits'):
            return self._cmd_keypad_press(key)                                # clears the alarm / message / special display as a press does
        if ui.get('setup'):
            if key in ARROWS and self._setup_item() != 'TTL':
                for _ in range(self._repeats(duration_s)):
                    self._setup_key(key)
                return self._out()
            return self._setup_key(key)
        if ui.get('prog'):
            pr = ui['prog']
            if key == 'rate':                                                 # manual 6.10: hold Phase # to reset to Phase 1
                self._prog_commit()
                if self._locked():
                    ui['message'] = 'LOC'
                    return self._out()
                self._cmd_phase_select(1)
                pr.update(view='phase', edfn='PH')
                return self._out()
            if key == 'volume':
                self._prog_commit()
                pr['view'] = 'function'
                return self._out()
            if key == 'up4' and pr['view'] == 'function' and self._pas_tenths(pr):   # manual 9.3.12: hold the right-most arrow past 9
                return self._out()
            if key in ARROWS:
                for _ in range(self._repeats(duration_s)):
                    self._prog_key(key)
                return self._out()
            return self._prog_key(key)
        r = self.run
        if r['state'] == 'user_wait' and r.get('sub') is not None:
            for _ in range(self._repeats(duration_s) if key in ARROWS else 1):
                self._cmd_keypad_press(key)
            return self._out()
        locked = self._locked()
        if key in ('rate', 'volume'):                                         # manual 6.6.2 / 6.7.3: Program Entry Mode
            if self._operating() or self.entry_lockout:
                ui['message'] = 'NA'
                return self._out()
            self._commit_edit()
            self._commit_units()
            if ui['message']:
                return self._out()
            if r['state'] == 'paused':
                self._reset_program()
            ui['prog'] = dict(view='phase' if key == 'rate' else 'function', edit=None, edit_t=None, edfn='PH', edfn_t=None)
            return self._out()
        if key == 'diameter':                                                 # manual 6.12: Setup
            if self._operating():
                ui['message'] = 'NA'
                return self._out()
            self._commit_edit()
            self._commit_units()
            if ui['message']:
                return self._out()
            if r['state'] == 'paused':
                self._reset_program()
            ui['setup'] = dict(idx=0, t=self.t, val=None, val_t=None, ttl=False, comm=False)
            return self._out()
        if key == 'direction':                                                # manual 6.8.1: hold (pump stopped) = sticky direction
            if locked:
                ui['message'] = 'LOC'
                return self._out()
            self._commit_edit()
            try:
                self._cmd_direction_sticky()
            except ActionRejected:
                ui['message'] = 'NA'
            return self._out()
        if key == 'start':                                                    # manual 6.9 / 7.4: the program starts, purge after 1 s until release
            if r['state'] != 'stopped' or self.alarm is not None:
                return self._cmd_keypad_press(key)
            self._commit_edit()
            self._commit_units()
            try:
                self._trigger_start()
            except ActionRejected:
                ui['message'] = 'NA'
                return self._out()
            ui['purge'] = dict(at=self.t + HOLD_S, until=self.t + duration_s, on=False)
            return self._out()
        if locked:
            ui['message'] = 'LOC'
            return self._out()
        if ui['mode'] == 'dispensed':                                         # manual 6.7.2: hold an arrow 1 s on Volume Dispensed: cleared
            return self._cmd_keypad_press(key)
        if key == 'up1' and ui['mode'] in ('rate', 'volume', 'diameter') and not (ui['mode'] != 'rate' and self._operating()):
            if r['state'] == 'paused':
                self._reset_program()
            if ui['edit'] is None:                                            # manual 6.4.1: the left-most digit scrolls past 9 to 0, then the point
                ui['edit'] = _cells(self._value_text())                       # shifts one position to the right per second held (assumption)
            cells, dp = ui['edit']
            cells[0] = '0'
            for _ in range(1 + int(math.floor(duration_s - HOLD_S + 1e-9))):
                dp = 0 if dp is None else (dp + 1 if dp < 2 else None)
            ui['edit'], ui['edit_t'] = (cells, dp), self.t
            return dict(display=_text(cells, dp), mode=ui['mode'])
        for _ in range(self._repeats(duration_s)):                            # manual 6.4: arrows held down increment continuously
            out = self._cmd_keypad_press(key)
        return out

    def _cmd_keypad_chord(self, keys):
        self._need_on()
        ui = self.ui
        if self.alarm is not None or ui['message'] or ui.get('special') or ui.get('vunits') or ui.get('setup'):
            return self._cmd_keypad_press(keys[0])
        a, b = sorted(ARROWS.index(k) for k in keys)
        if self._locked():
            ui['message'] = 'LOC'
            return self._out()
        pr = ui.get('prog')
        if pr is not None:
            e = pr.get('edit') or {}
            if (a, b) == (2, 3) and pr['view'] == 'function' and self._pas_tenths(pr):
                return self._out()
            ui['message'] = 'NA'
            return self._out()
        if b != a + 1 or ui['mode'] not in ('rate', 'volume', 'diameter') or (ui['mode'] != 'rate' and self._operating()) or ui.get('units_edit'):
            ui['message'] = 'NA'                                               # only two adjacent arrows on a number display (manual 6.4.1)
            return self._out()
        if self.run['state'] == 'paused':
            self._reset_program()
        if ui['edit'] is None:
            ui['edit'] = _cells(self._value_text())
        cells, dp = ui['edit']
        ui['edit'], ui['edit_t'] = (cells, None if dp == a else a), self.t   # the same pair again clears the point (whole number)
        return dict(display=_text(*ui['edit']), mode=ui['mode'])

    def _cmd_power_on_with_key(self, key):
        self._need_plant()
        if self.power:
            raise ActionRejected('NOT_APPLICABLE')                            # a power-up function: the switch must be off
        self._restart_blocked = True                                          # manual 8.1: any key held at power-up stops the auto restart
        try:
            self._cmd_power_set(True)
        finally:
            self._restart_blocked = False
        if self._on():
            if key == 'up1':
                self.ui['special'] = dict(kind='version', val=None, val_t=None)
            elif key == 'up4':                                                # manual 6.13.2 (and 7.1.2 / 8.7: units override and lockout reset)
                self.phases = [_default_phase()] + [_empty_phase() for _ in range(40)]
                self.sel = 1
                self.setup['keypad_lockout'] = False
                self.vol_units_override = None
                self._reset_program()
                self._event('operation', 'PROGRAM_RESET', '开机按住最右方向键：复位程序（rESt），解除键盘锁与体积单位覆盖')
                self.ui['special'] = dict(kind='reset', val=None, val_t=None)
            elif key == 'volume':
                self.ui['special'] = dict(kind='defaults', val=sorted(DEFAULT_PROGRAMS)[0], val_t=None)
            elif key == 'diameter':
                self.ui['special'] = dict(kind='plock', val=int(self.entry_lockout), val_t=None)
        return dict(on=self.power, display=self._display_text())

    def _cmd_direction_sticky(self):
        self._need_on()
        self._need_stopped()
        self._need_no_alarm()
        if self._peek_phase()['function'] not in RATE_FUNCS:
            raise ActionRejected('NOT_APPLICABLE')
        self._cancel_pause()
        self._cur_phase()['direction'] = 'STK'
        return dict(direction='STK')

    def _cmd_program_edit(self, operation):
        self._need_on()
        self._need_stopped()
        self._cancel_pause()
        s, ph = self.sel, self.phases
        refs = ('JMP', 'IF', 'EVN', 'EVS')
        if operation == 'insert':
            new = _default_phase() if s == 1 else dict(_default_phase(), function='STP')
            self.phases = ph[:s - 1] + [new] + ph[s - 1:40]
            for p in self.phases:                                             # manual 9.2: phase references follow the shifted section
                if p and p is not new and p['function'] in refs and p['parameter'] >= s:
                    p['parameter'] = min(p['parameter'] + 1, 41)
        else:
            self.phases = ph[:s - 1] + ph[s:] + [None]
            for p in self.phases:
                if p and p['function'] in refs and p['parameter'] > s:
                    p['parameter'] -= 1
            if self.phases[0] is None:
                self.phases[0] = _default_phase()
        self._reset_program()
        self._event('operation', 'PROGRAM_EDITED', f'程序编辑：第{s}阶段' + ('插入' if operation == 'insert' else '删除'))
        return dict(phase=s)

    def _cmd_program_entry_lockout(self, enabled):
        self._need_on()
        self._need_stopped()
        if enabled and any(p is not None for p in self.phases[1:]):
            raise ActionRejected('NOT_APPLICABLE')                            # manual 6.13.4: not with a multiple-phase program
        self.entry_lockout = bool(enabled)
        if enabled:
            self.sel = 1
        return dict(enabled=self.entry_lockout)

    def _cmd_program_load_default(self, program):
        self._need_on()
        self._need_stopped()
        steps = [dict(p) for p in DEFAULT_PROGRAMS[program]]
        self.phases = steps + [_empty_phase() for _ in range(41 - len(steps))]
        self.sel = 1
        self._reset_program()
        self.network.update(mode='RECP', baud_rate=19200)                     # manual 6.13.3: plus Reciprocating communications mode
        self._event('operation', 'DEFAULT_PROGRAM', f'载入默认程序{program}（往复泵程序），RS-232设为往复模式')
        return dict(phases=len(steps))

    def _select_subprogram(self, label):
        """Pr:In (manual 9.3.7): continue after the Pr:nn label phase, searching from the current phase to the end and then from Phase 1; the
        dispensed volumes are cleared and the label becomes the default of this Pr:In.  False (nothing changed) when no phase has the label."""
        r = self.run
        for i in list(range(r['idx'], 42)) + list(range(1, r['idx'])):
            p = self.phases[i - 1]
            if p and p['function'] == 'PRL' and p['parameter'] == label:
                break
        else:
            return False
        self.phases[r['idx'] - 1]['parameter'] = label
        self.dispensed = {'INF': 0.0, 'WDR': 0.0}
        r.update(sub=None, idx=i + 1, state='running')
        self._event('operation', 'SUBPROGRAM', f'子程序{label:02d}：从第{i + 1}阶段继续')
        self._enter_phase()
        return True

    def _cmd_program_subprogram(self, label):
        self._need_on()
        self._need_no_alarm()
        self._guard_run()
        if self.run['state'] != 'user_wait' or self.run.get('sub') is None:
            raise ActionRejected('NOT_APPLICABLE')
        if not self._select_subprogram(label):
            raise ActionRejected('OUT_OF_RANGE')
        return dict(state=self.run['state'])

    def _cmd_network_address(self, address=None, baud_rate=None, mode=None):
        self._need_on()
        if baud_rate is not None and baud_rate not in BAUDS:
            raise ActionRejected('OUT_OF_RANGE')
        n = self.network
        if address is not None:
            n['address'] = int(address)
        if baud_rate is not None:
            if n['baud_rate'] != int(baud_rate):
                self.safe_armed = False                                       # manual 10.2.3: a baud change disables the time-out until the next valid packet
            n['baud_rate'] = int(baud_rate)
        if mode is not None:
            n['mode'] = mode
            if mode != 'ADR':
                n['baud_rate'] = 19200                                        # manual 10.4.5: special modes use 19,200 baud
        return dict(network=dict(n))

    def _cmd_ttl_override_trigger(self, trigger):
        self._need_on()
        if not self._operating():
            raise ActionRejected('NOT_APPLICABLE')
        self._guard_run()
        if trigger in ('ET', 'BT'):
            self.run['redirect'] = trigger                                    # one-time event-trap redirect, the trigger configuration stays
        else:
            self.run['trg'] = trigger
        return dict(trigger=trigger)

    def _redirect_event(self):
        """Et / bt trap (manual 9.3.18.1): a stop trigger or the Stop key jumps to the event trap's phase, or to the next phase without a trap."""
        self._guard_run()
        r = self.run
        r['redirect'] = None
        if r['trap']:
            self._jump_event(r['trap']['target'])
        else:
            r.update(idx=r['idx'] + 1, state='running')
            self._event('operation', 'EVENT', f'触发重定向：无事件陷阱，继续第{r["idx"]}阶段')
            self._enter_phase()

    def _cmd_volume_set_units(self, units):
        self._need_on()
        self._need_stopped()
        self._cancel_pause()
        self.vol_units_override = units
        return dict(units=units)

    # keypad screens
    def _pas_tenths(self, pr):
        """PS:nn <-> PS:n.n: the decimal point between the two parameter digits (manual 9.3.12); False when the function shown is not PS."""
        e, ph = pr.get('edit') or {}, self._peek_phase()
        if (e.get('code') or ph['function']) != 'PAS':
            return False
        if e.get('kind') != 'param':
            same = not e and ph['function'] == 'PAS'                          # the stored PS phase, or a PS just selected (parameter 00)
            e = dict(kind='param', code='PAS', param=ph['parameter'] if same else 0, tenths=ph.get('tenths', False) if same else False)
        e['tenths'] = not e['tenths']
        pr['edit'], pr['edit_t'] = e, self.t
        return True

    def _special_key(self, key):
        ui = self.ui
        sp = ui['special']
        if sp['kind'] in ('defaults', 'plock') and key in ARROWS:
            if sp['kind'] == 'plock':
                sp['val'] = 1 - sp['val']
            else:
                names = sorted(DEFAULT_PROGRAMS)
                sp['val'] = names[(names.index(sp['val']) + 1) % len(names)]
            sp['val_t'] = self.t
            return self._out()
        if sp['kind'] == 'defaults' and key not in ARROWS:
            ui['special'] = None
            try:
                self._cmd_program_load_default(sp['val'])
                self._flash()
            except ActionRejected:
                ui['message'] = 'NA'
            return self._out()
        if sp['kind'] == 'plock' and sp['val_t'] is not None:
            self._special_store()
            return self._out()
        ui['special'] = None                                                  # manual 6.13: pressing any key clears the display
        return self._out()

    def _special_store(self):
        ui = self.ui
        sp, ui['special'] = ui['special'], None
        try:
            if bool(sp['val']) != self.entry_lockout:
                self._cmd_program_entry_lockout(bool(sp['val']))
                self._flash()
        except ActionRejected:
            ui['message'] = 'oor'

    def _setup_items(self):
        s = self.ui['setup']
        items = ['PF', 'LN', 'AL', 'TTL'] + (['TR', 'DR', 'RUN'] if s['ttl'] else [])
        if self.ttl_in[6] == 0:
            items.append('LOC')                                               # manual 8.7: shown only with the Lockout Disable Key inserted
        return items + ['BP'] + (['AD', 'BAUD'] if self.network['mode'] == 'ADR' else ['COMM'])

    def _setup_item(self):
        items = self._setup_items()
        return items[min(self.ui['setup']['idx'], len(items) - 1)]

    _SETUP_FLAGS = {'PF': 'power_failure', 'LN': 'low_noise', 'AL': 'alarm', 'LOC': 'keypad_lockout', 'BP': 'beep'}

    def _setup_value(self, item):
        if item in self._SETUP_FLAGS:
            return int(self.setup[self._SETUP_FLAGS[item]])
        return {'TR': lambda: self.setup['trigger'], 'DR': lambda: self.setup['direction_input'], 'RUN': lambda: self.setup['motor_output'],
                'AD': lambda: self.network['address'], 'BAUD': lambda: self.network['baud_rate'], 'COMM': lambda: self.network['mode'],
                'TTL': lambda: None}[item]()

    def _setup_text(self):
        s = self.ui['setup']
        item = self._setup_item()
        v = s['val'] if s['val'] is not None else self._setup_value(item)
        if item == 'AD' and s['comm']:
            return dict(COMM_MODES)[v if isinstance(v, str) else self.network['mode']]
        return {'PF': lambda: f'PF: {v}', 'LN': lambda: f'Ln: {v}', 'AL': lambda: f'AL: {v}', 'TTL': lambda: ' ttL', 'TR': lambda: 'tr:' + TRIGGER_LCD[v],
                'DR': lambda: 'dr:' + ('rE' if v == 0 else 'dU'), 'RUN': lambda: f'rUn.{v}', 'LOC': lambda: f'LoC.{v}', 'BP': lambda: f'bP: {v}',
                'AD': lambda: f'Ad:{v:02d}', 'BAUD': lambda: '1920' if v == 19200 else f'{v:4d}', 'COMM': lambda: dict(COMM_MODES)[v]}[item]()

    def _setup_key(self, key):
        ui = self.ui
        s = ui['setup']
        item = self._setup_item()
        if key not in ARROWS:                                                 # manual 6.12: a non-arrow key stores and advances
            if s['val'] is not None:
                self._setup_commit()
            self._setup_next()
            return self._out()
        if item == 'TTL':                                                     # manual 8: press any arrow key to select the TTL settings
            s['ttl'] = True
            s.update(idx=self._setup_items().index('TR'), t=self.t)
            return self._out()
        if self._locked():
            ui['message'] = 'LOC'
            return self._out()
        v = s['val'] if s['val'] is not None else self._setup_value(item)
        if item == 'AD' and (s['comm'] or key == 'up1'):                     # manual 8.9: the left-most arrow opens the communications mode menu
            modes = [m for m, _ in COMM_MODES]
            if not s['comm']:
                s['comm'] = True
                v = self.network['mode']
            else:
                v = modes[(modes.index(v if isinstance(v, str) else self.network['mode']) + 1) % len(modes)]
        elif item == 'AD':
            if key not in ('up3', 'up4'):
                return self._out()
            v = (((v // 10 + 1) % 10) * 10 + v % 10) if key == 'up3' else (v // 10 * 10 + (v % 10 + 1) % 10)
        elif item == 'BAUD':
            v = BAUDS[(BAUDS.index(v) + 1) % len(BAUDS)]
        elif item == 'COMM':
            modes = [m for m, _ in COMM_MODES]
            v = modes[(modes.index(v) + 1) % len(modes)]
        elif item == 'TR':
            if key not in ('up3', 'up4'):
                return self._out()
            v = TRIGGERS[(TRIGGERS.index(v) + 1) % 13]                        # the setup default: Ft ... oF (Et / bt only inside a program)
        elif item == 'DR':
            if key not in ('up3', 'up4'):
                return self._out()
            v = 1 - v
        else:
            if key != 'up4':                                                  # the arrow under the value (0 / 1)
                return self._out()
            v = 1 - v
        s.update(val=v, val_t=self.t)
        return self._out()

    def _setup_commit(self):
        s = self.ui['setup']
        item, v = self._setup_item(), s['val']
        s.update(val=None, val_t=None)
        try:
            if item in self._SETUP_FLAGS:
                changed = int(self.setup[self._SETUP_FLAGS[item]]) != v
                self._cmd_setup_configure(**{self._SETUP_FLAGS[item]: bool(v)})
            elif item in ('TR', 'DR', 'RUN'):
                key = {'TR': 'trigger', 'DR': 'direction_input', 'RUN': 'motor_output'}[item]
                changed = self.setup[key] != v
                self._cmd_setup_configure(**{key: v})
            else:
                key = {'AD': 'address' if not s['comm'] else 'mode', 'BAUD': 'baud_rate', 'COMM': 'mode'}[item]
                changed = self.network[key] != v
                self._cmd_network_address(**{key: v})
                s['comm'] = False
        except ActionRejected:
            self.ui['message'] = 'oor'
            return
        if changed:
            self._flash()

    def _setup_next(self):
        s = self.ui['setup']
        item = self._setup_item()
        items = self._setup_items()
        s.update(idx=(items.index(item) + 1) if item in items else len(items), t=self.t, val=None, val_t=None, comm=False)
        if s['idx'] >= len(items):                                            # manual 6.12: after the last parameter: the syringe diameter
            self.ui['setup'] = None
            self.ui['mode'] = 'diameter'

    def _prog_key(self, key):
        ui = self.ui
        pr = ui['prog']
        if key in ARROWS:
            if self._locked():
                ui['message'] = 'LOC'
                return self._out()
            if pr['view'] == 'phase':
                if key in ('up1', 'up2'):                                     # manual 9.2: the arrows under 'PH' select PH / In / dE
                    pr['edfn'] = {'PH': 'In', 'In': 'dE', 'dE': 'PH'}[pr['edfn']]
                    pr['edfn_t'] = self.t
                    return self._out()
                e = pr['edit'] if (pr['edit'] or {}).get('kind') == 'phase' else dict(kind='phase', d=[self.sel // 10, self.sel % 10])
                i = 0 if key == 'up3' else 1
                e['d'][i] = (e['d'][i] + 1) % 10
                if e['d'][0] * 10 + e['d'][1] > 41:
                    e['d'] = [4, 1]                                           # manual 9.1.1: past 41 the maximum phase number is shown
                pr['edit'], pr['edit_t'] = e, self.t
                return self._out()
            if pr['view'] == 'dispensed':
                return self._out()
            ph = self._peek_phase()
            e = pr['edit'] or dict(kind='function', code=ph['function'], param=ph['parameter'], tenths=ph.get('tenths', False))
            code = e['code']
            param_keys = ('up4',) if code == 'OUT' else ('up3', 'up4') if code in FUNC_PARAM else ()
            if key in param_keys:                                             # manual 6.11.1: the arrows under the parameter's digits
                if code == 'TRG':
                    p = (e['param'] + 1) % len(TRIGGERS)
                elif code == 'OUT':
                    p = 1 - e['param']
                else:
                    p = e['param']
                    p = (((p // 10 + 1) % 10) * 10 + p % 10) if key == 'up3' else (p // 10 * 10 + (p % 10 + 1) % 10)
                e = dict(e, kind='param', param=p)
            else:                                                             # manual 6.11: any other arrow selects the next function
                codes = [c for c, _ in FUNC_KEYPAD]
                code = codes[(codes.index(code) + 1) % len(codes)]
                e = dict(kind='function', code=code, param=FUNC_PARAM.get(code, (0, 0, 0))[2], tenths=False)
            pr['edit'], pr['edit_t'] = e, self.t
            return self._out()
        had_edfn = pr['edfn']
        self._prog_commit()
        if ui['message'] or ui.get('prog') is None:
            return self._out()
        pr['edfn'] = 'PH'
        if key == 'rate':
            if pr['view'] == 'phase' and had_edfn in ('In', 'dE'):            # manual 9.2: Phase # within 2 s performs Insert / Delete
                try:
                    self._cmd_program_edit('insert' if had_edfn == 'In' else 'delete')
                    self._flash()
                except ActionRejected:
                    ui['message'] = 'NA'
            elif pr['view'] == 'phase':
                if self._peek_phase()['function'] in RATE_FUNCS:              # manual 6.6.2 / 6.10: back to the rate display
                    ui['prog'] = None
                    ui['mode'] = 'rate'
            else:
                pr['view'] = 'phase'
        elif key == 'volume':
            if pr['view'] == 'phase' or pr['view'] == 'dispensed':
                pr['view'] = 'function'
            elif self._peek_phase()['function'] in RATE_FUNCS:                # manual 6.7.3: exit to the Volume to be Dispensed
                ui['prog'] = None
                ui['mode'] = 'volume'
            else:
                pr['view'] = 'dispensed'
        elif key == 'diameter':
            ui['prog'] = None
            ui['mode'] = 'diameter'
        elif key == 'start':
            ui['prog'] = None
            ui['mode'] = 'rate'
            return self._cmd_keypad_press('start')
        else:
            ui['message'] = 'NA'
        return self._out()

    def _prog_commit(self):
        """Store the phase number / function / parameter being entered (a non-arrow key or 2 s after the last arrow, manual 6.10-6.11)."""
        ui = self.ui
        pr = ui.get('prog')
        if not pr or not pr.get('edit'):
            return
        e, pr['edit'], pr['edit_t'] = pr['edit'], None, None
        try:
            if e['kind'] == 'phase':
                n = max(1, e['d'][0] * 10 + e['d'][1])
                if n != self.sel:
                    self._cmd_phase_select(n)
                    self._flash()
                return
            cur = self._peek_phase()
            if e['kind'] == 'function' and e['code'] == cur['function']:
                return
            if e['kind'] == 'param' and e['code'] == cur['function'] and e['param'] == cur['parameter'] and e['tenths'] == cur.get('tenths', False):
                return
            kw = dict(rate_m3_s=cur.get('rate_m3_s', 0.0), rate_units=cur.get('rate_units', 'MH'), volume_m3=cur.get('volume_m3', 0.0),
                      direction=cur['direction'] if cur['direction'] != 'STK' else 'INF') if e['code'] in RATE_FUNCS and cur['function'] in RATE_FUNCS else {}
            if e['code'] not in RATE_FUNCS:
                kw = dict(parameter=e['param'], tenths=e['tenths'] and e['code'] == 'PAS')
            self._cmd_phase_set_function(e['code'], **kw)
            if cur['direction'] == 'STK' and e['code'] in RATE_FUNCS and self._peek_phase()['function'] in RATE_FUNCS:
                self._cur_phase()['direction'] = 'STK'
            self._flash()
        except ActionRejected:
            ui['message'] = 'oor'

    def _prog_text(self):
        pr = self.ui['prog']
        if pr['view'] == 'phase':
            e = pr['edit'] if (pr['edit'] or {}).get('kind') == 'phase' else None
            n = f'{e["d"][0]}{e["d"][1]}' if e else f'{self.sel:02d}'
            return {'PH': 'PH:', 'In': 'In:', 'dE': 'dE:'}[pr['edfn']] + n
        if pr['view'] == 'dispensed':
            return fmt4(self.dispensed[self._shown_direction()] / (1e-9 if self._vol_units() == 'UL' else 1e-6))
        e = pr['edit']
        if e is None:
            ph = self._peek_phase()
            e = dict(code=ph['function'], param=ph['parameter'], tenths=ph.get('tenths', False))
        return _func_lcd(e['code'], e['param'], e['tenths'])

    def _sub_key(self, key):
        """Pr:In waiting for a sub-program (manual 9.3.7): the two right arrows set the label, Start continues; other keys are not applicable
        (cancel only by switching the power off and on)."""
        r, ui = self.run, self.ui
        if key in ('up3', 'up4'):
            p = r['sub']
            r['sub'] = (((p // 10 + 1) % 10) * 10 + p % 10) if key == 'up3' else (p // 10 * 10 + (p % 10 + 1) % 10)
        elif key == 'start':
            if not self._select_subprogram(r['sub']):
                ui['message'] = 'oor'
        else:
            ui['message'] = 'NA'
        return self._out()

    def _vunits_store(self):
        ui = self.ui
        u, ui['vunits'] = ui['vunits']['units'], None
        if u != self._vol_units() or self.vol_units_override is None:
            try:
                self._cmd_volume_set_units(u)
                self._flash()
            except ActionRejected:
                ui['message'] = 'NA'

    def _fw_tick(self):
        ui, t, wait = self.ui, self.t, ENG['display_commit_s']
        p = ui.get('purge')
        if p:
            if not p['on'] and t >= p['at'] - 1e-9 and self._on() and self.alarm is None and self.run['state'] == 'running':
                self.run.update(state='purging', purge=True, direction=self._shown_direction(), rate=None, trap=None)
                p['on'] = True
                self._event('operation', 'PURGE', '按住Start/Stop 1 s：清洗（最高速泵送）')
            if t >= p['until'] - 1e-9:
                ui['purge'] = None
                if self.run['purge']:                                         # manual 7.4: released: the pump stops
                    self.run.update(purge=False, state='stopped')
                    self._event('operation', 'PURGE_END', '松开Start/Stop：清洗结束，泵停止')
        pr = ui.get('prog')
        if pr:
            if pr.get('edit') and t - pr['edit_t'] >= wait - 1e-9:
                self._prog_commit()
            if pr['edfn'] != 'PH' and t - pr['edfn_t'] >= wait - 1e-9:
                pr['edfn'] = 'PH'                                             # manual 9.2: In / dE not confirmed within 2 s: canceled
        st = ui.get('setup')
        if st:
            if st['val'] is not None:
                if t - st['val_t'] >= wait - 1e-9:
                    self._setup_commit()
                    if ui.get('setup'):
                        self._setup_next()
            elif t - st['t'] >= wait - 1e-9:
                self._setup_next()                                            # manual 6.12: each parameter is shown for about 2 seconds
        sp = ui.get('special')
        if sp and sp['kind'] == 'plock' and sp['val_t'] is not None and t - sp['val_t'] >= wait - 1e-9:
            self._special_store()
        vu = ui.get('vunits')
        if vu and t - vu['t'] >= wait - 1e-9:
            self._vunits_store()

    def _fw_busy(self):
        ui = self.ui
        pr, sp = ui.get('prog'), ui.get('special')
        return bool(ui.get('setup') or ui.get('vunits') or ui.get('purge') or (pr and (pr.get('edit') or pr['edfn'] != 'PH'))
                    or (sp and sp.get('val_t') is not None))

    def _blink(self):
        """What blinks on the panel now (the slots keep the lit state): the display after a store, the units LEDs while a value or the units
        are being changed, Withdraw for sticky direction, Pumping while paused (manual 6.1, 6.3, 6.6.1, 7.1.2)."""
        ui = self.ui
        on = self._on()
        units = on and (ui['edit'] is not None or ui.get('units_edit') is not None or ui.get('vunits') is not None)
        sticky = on and not self._operating() and (self.phases[self.sel - 1] or {}).get('direction') == 'STK'
        return dict(display=on and ui.get('flash_t') is not None and self.t - ui['flash_t'] < 0.5 - 1e-9, mm=units, ml=units, ul=units, min=units,
                    hr=units, dispensed=False, withdraw=sticky, pumping=on and self.run['state'] == 'paused')

    # ---- RS-232 (Basic protocol)
    def _status_prompt(self):
        st = self.run['state']
        if st == 'purging':
            return 'X'
        if st == 'running':
            return 'I' if self.run['direction'] == 'INF' else 'W'
        return {'stopped': 'S', 'paused': 'P', 'pause_timer': 'T', 'user_wait': 'U'}[st]

    def _cmd_serial_send(self, command):
        """One command from the computer.  1.2.0: nothing arrives when the computer's baud rate differs from the pump's (manual 10.1); a Safe-protocol
        packet (STX ... ETX) is unpacked and checked (corrupted: ?COM), resets the Safe-mode time-out, and is accepted in Basic mode too; in Safe mode a
        packet without framing is discarded; the reply is framed while Safe mode is on (after the command: SAF n answers in the new mode), and a reply
        sent at a new baud rate (*ADR n B baud) does not reach a computer still at the old one."""
        self._need_on()
        if self.host_baud != self.network['baud_rate']:
            return dict(response='')
        baud = self.network['baud_rate']
        if command.startswith(STX):
            data = _safe_unpack(command)
            if data is None:
                self.rs232_seen = True
                reply = f'{self.network["address"]:02d}' + self._status_prompt() + '?COM'   # manual 10.3: invalid communications packet received
                return dict(response=_safe_pack(reply) if self.safe_timeout > 0 else reply)
            if self.safe_timeout > 0:
                self.safe_armed, self.safe_last_t = True, self.t
            command = data
        elif self.safe_timeout > 0:
            return dict(response='')                                          # Safe mode: no STX, not a packet (assumption: discarded without a reply)
        reply = self._serial_reply(command)
        if reply and self.safe_timeout > 0:
            reply = _safe_pack(reply)
        if self.network['baud_rate'] != baud and self.network['baud_rate'] != self.host_baud:
            reply = ''                                                        # the answer comes at the new baud rate (manual 10.4.5 *ADR ... B)
        return dict(response=reply)

    def _serial_reply(self, command):
        self.rs232_seen = True
        cmd = re.sub(r'\s+', '', command.upper())
        star = cmd.startswith('*')                                            # '*' bypasses the network address (system commands)
        if star:
            cmd = cmd[1:]
        m = re.match(r'^(\d{1,2})(?=[A-Z]|$)', cmd)
        if m:
            if not star and int(m.group(1)) != self.network['address']:
                return ''                                                     # another pump's address: ignored
            cmd = cmd[m.end():]
        elif not star and self.network['address'] != 0:
            return ''                                                         # no address: for the pump at address 0
        if not star and self.network['mode'] != 'ADR':
            return ''                                                         # special communications modes answer only '*' commands (manual 10.4.5)
        adr = f'{self.network["address"]:02d}'
        alarm_txt = ''
        if getattr(self, '_power_reset_alarm', False):
            alarm_txt, self._power_reset_alarm = 'A?R', False
        elif self.alarm is not None:
            alarm_txt = {'stall': 'A?S', 'program_error': 'A?E', 'out_of_range': 'A?O', 'comm_timeout': 'A?T'}[self.alarm]
            self.alarm = None                                                 # the alarm status sent in a response acknowledges it (manual 10.3)
            self.ui['message'] = None
        if alarm_txt:
            return adr + alarm_txt
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
        return adr + self._status_prompt() + data

    def _cmd_serial_safe_mode(self, command):
        sent = self._cmd_serial_send(_safe_pack(re.sub(r'\s+', '', command.upper())))['response']
        if not sent.startswith(STX):
            return dict(response=sent, data=sent if sent else None, crc_ok=None)
        data = _safe_unpack(sent)
        return dict(response=sent, data=data, crc_ok=data is not None)

    def _cmd_serial_configure_port(self, baud_rate):
        if baud_rate not in BAUDS:
            raise ActionRejected('OUT_OF_RANGE')                              # the pump's rates 300, 1200, 2400, 9600, 19200 (manual 12.5)
        self.host_baud = int(baud_rate)
        return dict(baud_rate=self.host_baud)

    def _cmd_serial_receive(self):
        packets, self.serial_rx = list(self.serial_rx), []
        return dict(packets=packets)

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
                return ph['function'] + (f'{ph["parameter"]:02d}' if ph['function'] in ('JMP', 'IF', 'EVN', 'EVS', 'LOP', 'PAS', 'OUT', 'PRL', 'TRG') else '')
            m = re.fullmatch(r'(RAT|FIL|INC|DEC|STP|JMP|LPS|LOP|LPE|PAS|IF|EVN|EVS|EVR|CLD|BEP|OUT|PRI|PRL|TRG)([\d.]*)', a)
            if not m:
                raise ActionRejected('UNKNOWN_COMMAND' if not a.startswith(('PRI', 'PRL', 'TRG')) else 'NOT_APPLICABLE')
            f, par = m.group(1), m.group(2)
            if par and (not re.fullmatch(r'\d{0,2}(\.\d)?', par) or f not in ('JMP', 'LOP', 'PAS', 'IF', 'EVN', 'EVS', 'OUT', 'PRL', 'TRG') or par == '.'):
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
                sticky = not self._operating() and (self.phases[self.sel - 1] or {}).get('direction') == 'STK'
                return 'STK' if sticky else self._shown_direction()
            if a not in ('INF', 'WDR', 'REV', 'STK'):
                raise ActionRejected('NOT_APPLICABLE')
            if a == 'STK':
                self._cmd_direction_sticky()
            else:
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
        if cmd.startswith('ADR'):                                            # *ADR [n [B baud]] | DUAL | RECP | ALTR (manual 10.4.5)
            a = cmd[3:]
            if not a:
                return f'{self.network["address"]:02d}' if self.network['mode'] == 'ADR' else self.network['mode']
            m = re.fullmatch(r'(\d{1,2})(?:B(\d+))?|(DUAL|RECP|ALTR)', a)
            if not m:
                raise ActionRejected('OUT_OF_RANGE')
            if m.group(3):
                self._cmd_network_address(mode=m.group(3))
            else:
                self._cmd_network_address(address=int(m.group(1)), mode='ADR', **({'baud_rate': int(m.group(2))} if m.group(2) else {}))
            return ''
        if cmd.startswith('SAF'):                                            # SAF [n] Safe communications mode, n = time-out 0-255 s (manual 10.4.5)
            a = cmd[3:]
            if not a:
                return str(self.safe_timeout)
            if not re.fullmatch(r'\d{1,3}', a) or int(a) > 255:
                raise ActionRejected('OUT_OF_RANGE')
            self.safe_timeout = int(a)
            self.safe_armed, self.safe_last_t = self.safe_timeout > 0, self.t   # valid packets must now come every n s
            self._event('operation', 'SAFE_MODE', f'Safe通讯模式：超时{self.safe_timeout} s' if self.safe_timeout else '回到Basic通讯模式')
            return ''
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
            mode = self.run.get('trg') or self.setup['trigger']               # manual 9.3.18: a program override until the program stops
            start = (mode in ('FT', 'FH', 'ST') and falling) or (mode in ('F2', 'LE', 'T2') and rising) or (mode == 'RL' and falling) or (mode == 'RH' and rising)
            stop = (mode in ('SP',) and falling) or (mode in ('P2', 'FH') and rising) or (mode == 'LE' and falling) or (mode == 'SL' and falling) or (mode == 'SH' and rising)
            toggle = mode in ('FT', 'F2')
            if self.alarm is not None:
                return
            try:
                if toggle and start:
                    if self.run['state'] in ('running', 'pause_timer') and self.run.get('redirect') == 'ET':
                        self._redirect_event()
                    elif self.run['state'] in ('running', 'pause_timer', 'purging'):
                        self._trigger_stop()
                    else:
                        self._trigger_start()
                elif start and not self.run['state'] in ('running', 'pause_timer', 'purging'):
                    self._trigger_start()
                elif stop and self.run['state'] in ('running', 'pause_timer', 'user_wait') and self.run.get('redirect') == 'ET':
                    self._redirect_event()
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

    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What the panel firmware draws: the agent slots plus whether the pump is live, the power-up count, the LCD text with its colon, the firmware
        screen and what blinks now."""
        out = self.read('agent')
        out.update({'host.powered': self._on(), 'host.power_epoch': self.power_epoch, 'host.lcd': self._lcd_text() if self.plant else '',
                    'host.mode': self._fw_screen(), 'host.message': self.ui['message'], 'host.blink': self._blink()})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel gesture calls the same command a direct call would (these commands check before they change anything)."""
        try:
            validate(ALL_CAPS[capability_id]['input_schema'], arguments)
            return self.invoke(capability_id, **arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.t

    def _panel(self, gesture, *args):
        self._need_plant()
        try:
            return dict(accepted=bool(gesture(*args)))
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _cmd_panel_press(self, input_id, duration_s=0.1, held=()):
        return self._panel(self.panel.press, input_id, float(duration_s), tuple(held))

    def _cmd_panel_switch(self, input_id, position, held=()):
        return self._panel(self.panel.switch, input_id, position, tuple(held))

    def _cmd_panel_capture(self, display_id):
        """Close-up camera of a display: a PNG file named by its content (large data is returned as a file reference)."""
        import hashlib
        import tempfile
        self._need_plant()
        try:
            data, w, h = self.panel.png_bytes(display_id)
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        sha = hashlib.sha256(data).hexdigest()
        d = Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f'{display_id}_{sha[:16]}.png'
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.t, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))

    def _cmd_panel_frame(self):
        return dict(frame=self.panel.frame())

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
        return self._resolve_dir(ph['direction'], first=True) if ph and ph['function'] in RATE_FUNCS else 'INF'

    def _display_text(self):
        return self._lcd_text().replace(':', '')

    def _lcd_text(self):
        """The LCD: four cells with decimal points and the colon between cells 2 and 3 (manual 6.2); display.text is this without the colon."""
        if not self._on():
            return ''
        ui, r = self.ui, self.run
        if self.alarm == 'stall':
            return 'StAL'
        if self.alarm == 'program_error':                                      # manual 6.14 glyphs (the colon between digits 2 and 3 is not a cell)
            return f'Er:{(self.alarm_phase or 0) % 100:02d}'
        if self.alarm == 'out_of_range':
            return f'or:{(self.alarm_phase or 0) % 100:02d}'
        if self.alarm == 'comm_timeout':
            return 'r232'                                                     # manual 6.14
        if ui['message']:
            return {'oor': 'oor', 'NA': '----', 'LOC': 'LoC-'}[ui['message']]
        if ui.get('special'):
            sp = ui['special']
            return {'version': FIRMWARE_LCD, 'reset': 'rESt', 'defaults': dict(COMM_MODES).get(sp['val'], sp['val']),
                    'plock': f'P-L.{sp["val"]}', 'nv_error': f'Err.{sp["val"]}'}[sp['kind']]
        if r['state'] == 'purging':
            return 'PurG'
        if r['state'] == 'pause_timer':
            return f'PS:{int(math.ceil(r["timer"] - 1e-9)) % 100:02d}'
        if r['state'] == 'user_wait' and r.get('sub') is not None:
            return f'Pr:{r["sub"] % 100:02d}'
        if r['state'] == 'user_wait':
            return 'PAUS'
        if ui.get('search_until') is not None and self.t < ui['search_until'] - 1e-9 and self.network['mode'] != 'ADR':
            return dict(COMM_MODES)[self.network['mode']]
        if ui.get('setup'):
            return self._setup_text()
        if ui.get('prog'):
            return self._prog_text()
        if ui.get('vunits'):
            return 'UntS'
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
        if ui.get('special') or ui.get('setup') or (ui.get('prog') and ui['prog']['view'] != 'dispensed'):
            pass                                                              # function / setup / power-up displays: no units
        elif ui.get('vunits') or ui.get('prog'):
            u = ui['vunits']['units'] if ui.get('vunits') else self._vol_units()
            d.update(ul=u == 'UL', ml=u == 'ML', dispensed=True)
        elif ui['mode'] == 'rate':
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
        if self.ui['edit'] is not None or self.ui.get('units_edit') is not None or self.ttl_pending or self._fw_busy():
            return False
        if self._on() and self.run['state'] in ('running', 'purging', 'pause_timer'):
            return False
        if self.syringe is not None and abs(self.p - self._pb()) > 1e-9 and 'occlusion' not in self.faults:
            return False
        if self.safe_timeout > 0 and self.safe_armed and self._on():
            return False                                                      # the Safe-mode time-out runs
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
        self._auto_alarm('S')

    def _tick(self, h):
        ui = self.ui
        self._fw_tick()
        if ui['edit'] is not None and self.t - ui['edit_t'] >= ENG['display_commit_s'] - 1e-9:
            self._commit_edit()                                               # stored 2 s after the last arrow key (manual 6.1)
        if ui.get('units_edit') is not None and self.t - ui['units_t'] >= ENG['display_commit_s'] - 1e-9:
            self._commit_units()                                              # manual 6.6.1: or wait 2 seconds
        for pin, (lvl, t0) in list(self.ttl_pending.items()):
            if self.t - t0 >= DOC['ttl_min_level_s'] - 1e-9:
                del self.ttl_pending[pin]
                self._ttl_edge(pin, lvl)
        if self.safe_timeout > 0 and self.safe_armed and self._on() and self.t - self.safe_last_t >= self.safe_timeout - 1e-9:
            self._comm_timeout()
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
        x0 = self.x
        self.x = min(max(self.x + dx, 0.0), TRAVEL)
        dx_motor = self._motor_steps(self.x - x0)
        if dx != 0.0:
            k = r['direction']
            self.dispensed[k] += a_set * abs(dx)
            r['pumped'] = r.get('pumped', 0.0) + a_set * abs(dx)
            units = 1e-9 if self._vol_units() == 'UL' else 1e-6
            if self.dispensed[k] / units >= 9999.5:
                self.dispensed[k] = 0.0                                       # manual 7.6 E: rolls over from 9999 to 0
        self._fluid(sub, dx_motor, abs(v))
        return sub, stop

    def _motor_steps(self, dx):
        """Pusher travel the motor actually makes: smooth with the fine micro-stepping; with Low Noise on (manual 8.2: less micro-stepping, more pulsation)
        whole half steps of 0.8504 um only, the rest is carried to the next step (ln_lag).  Counters and limits follow the commanded travel (self.x)."""
        if not self.setup['low_noise']:
            dx, self.ln_lag = dx + self.ln_lag, 0.0
            return dx
        tot = self.ln_lag + dx
        n = math.trunc(tot / LN_STEP_M + (1e-9 if tot > 0 else -1e-9))
        self.ln_lag = tot - n * LN_STEP_M
        return n * LN_STEP_M

    def _fluid(self, h, dx, v_motor=None):
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
            v = abs(dx) / h if v_motor is None else v_motor                   # the drive's speed (Low Noise steps come in bursts)
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
               'pusher.position_m': self.x - self.ln_lag, 'collar.position_m': self.collar, 'collar.joint_m': self.collar if self.collar is not None else COLLAR_PARK,
               'syringe.state': self._syringe_view(), 'outlet.state': dict(self.outlet), 'setup.state': dict(self.setup),
               'ttl.state': dict(inputs={str(k): v for k, v in self.ttl_in.items()}, outputs={str(k): v for k, v in self.ttl_out.items()}),
               'display.text': self._display_text() if pc else '',
               'firmware.state': dict(program_entry_lockout=self.entry_lockout, volume_units_override=self.vol_units_override, network=dict(self.network),
                                      safe_mode_timeout_s=self.safe_timeout)}
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
            geo = self._area_true() * (X_ZERO - (self.x - self.ln_lag))
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
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version=ASSET_VERSION, capabilities=caps, state_slots=slots)

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
            if not isinstance(capability_id, str) or capability_id not in ALL_CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and ALL_CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            validate(ALL_CAPS[capability_id]['input_schema'], arguments)
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
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本型号或本配置没有该功能',
    'ACCESS_DENIED': '无权访问（真值或评测者能力；1.2.0：另一agent正在运行或暂停的程序不能被停止、恢复、复位或修改；电源开关是硬开关，不受此限）',
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
    'keypad.hold': _A, 'keypad.chord': _A, 'power.on_with_key': _P + ['NOT_APPLICABLE'], 'direction.sticky': _A + _OP + ['ALARM_ACTIVE', 'NOT_APPLICABLE'],
    'program.edit': _A + _OP, 'program.entry_lockout': _A + _OP + ['NOT_APPLICABLE'], 'program.load_default': _A + _OP,
    'program.subprogram': _A + ['ALARM_ACTIVE', 'NOT_APPLICABLE', 'OUT_OF_RANGE'], 'network.address': _A + ['OUT_OF_RANGE'],
    'ttl.override_trigger': _A + ['NOT_APPLICABLE'], 'volume.set_units': _A + _OP,
    'serial.safe_mode': _A, 'serial.configure_port': ['OUT_OF_RANGE'], 'serial.receive': [],
    'panel.press': _P, 'panel.switch': _P, 'panel.capture': _P, 'panel.frame': [],
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
    'keypad.hold': ('电源开；按住≥1 s；程序录入与Setup须程序不在运行（P-L锁定时不能进程序录入）；粘滞方向须停止；清洗须程序停止且无报警；键盘锁定时只能启停与查看',
                    ['power.on', 'run.state', 'alarm.state', 'setup.state', 'firmware.state']),
    'keypad.chord': ('电源开；两个相邻方向键；数值显示（运行中只有流量）', ['power.on', 'run.state', 'setup.state']),
    'power.on_with_key': ('电源开关关（按住键接通电源）', ['power.on']), 'direction.sticky': ('电源开；程序不在运行；无报警；当前阶段为流量功能', ['power.on', 'run.state', 'alarm.state', 'program.state']),
    'program.edit': _STOPPED, 'program.entry_lockout': ('电源开；程序不在运行；开启时程序只有第1阶段', ['power.on', 'run.state', 'program.state']),
    'program.load_default': _STOPPED, 'program.subprogram': ('电源开；无报警；程序在Pr:In等待子程序选择；有该标签的Pr:nn阶段', ['power.on', 'alarm.state', 'run.state', 'program.state']),
    'network.address': ('电源开；波特率为300/1200/2400/9600/19200之一', ['power.on']), 'ttl.override_trigger': ('电源开；程序在运行', ['power.on', 'run.state']),
    'volume.set_units': _STOPPED,
    'serial.safe_mode': _ON, 'serial.configure_port': ('无（计算机一侧的串口设定；泵的波特率见firmware.state）', ['firmware.state']),
    'serial.receive': ('无（Safe模式开启且波特率相同时才有泵主动发出的包）', ['firmware.state']),
    **{k: ('面板按手册规则处理手势：关机时只有电源开关起作用；键盘锁定时只能启停与查看；被调用能力的前提同样适用（被拒时显示泵自己的----、oor或LoC-，手势不被接受）',
           ['power.on', 'run.state', 'alarm.state', 'setup.state']) for k in ('panel.press', 'panel.switch')},
    'panel.capture': ('无（关机时LCD与LED全暗）', ['power.on']),
}
_DISPLAY = ['display.text', 'display.lcd_cells'] + [f'indicator.{k}' for k in ('mm', 'ml', 'ul', 'min', 'hr', 'dispensed', 'withdraw', 'pumping', 'rs232')]
_RUN = ['run.state', 'run.phase', 'pump.rate_m3_s', 'pump.direction', 'alarm.state', 'truth.process']
_EVOLVE = _DISPLAY + _RUN + ['pusher.position_m', 'volume.dispensed', 'ttl.state', 'outlet.state']
_FW = ['setup.state', 'firmware.state']
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.process'] + _DISPLAY,
    'power.set': ['power.on', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY, 'power.toggle': ['power.on', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY,
    'keypad.press': ['program.state', 'syringe.diameter_m', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY + _FW,
    'syringe.set_diameter': ['syringe.diameter_m', 'volume.dispensed'] + _RUN + _DISPLAY, 'rate.set': ['program.state', 'pump.rate_m3_s', 'pump.direction', 'truth.process', 'run.state', 'run.phase', 'ttl.state'] + _DISPLAY,
    'volume.set': ['program.state'] + _RUN + _DISPLAY, 'direction.set': ['program.state', 'pump.direction', 'truth.process'] + _RUN + _DISPLAY,
    'phase.select': ['program.state', 'pump.direction'] + _RUN + _DISPLAY,
    'phase.set_function': ['program.state', 'pump.direction', 'run.state', 'run.phase'] + _DISPLAY,
    'program.clear': ['program.state', 'pump.direction', 'run.state', 'run.phase'] + _DISPLAY, 'pump.run': _RUN + _DISPLAY + ['ttl.state', 'program.state', 'volume.dispensed'],
    'pump.stop': _RUN + _DISPLAY + ['ttl.state'], 'pump.purge': _RUN + _DISPLAY + ['ttl.state'], 'volume.clear': ['volume.dispensed'] + _RUN + _DISPLAY,
    'setup.configure': ['setup.state', 'ttl.state'] + _DISPLAY, 'serial.send': ['program.state', 'syringe.diameter_m', 'volume.dispensed', 'setup.state'] + _RUN + _DISPLAY + ['ttl.state', 'firmware.state'],
    'ttl.set': ['ttl.state'], 'syringe.load': ['syringe.state', 'pusher.position_m', 'truth.process'], 'syringe.unload': ['syringe.state', 'truth.process'],
    'outlet.connect': ['outlet.state', 'truth.process'], 'collar.set': ['collar.position_m', 'collar.joint_m'],
    'medium.configure': ['truth.process'], 'fault.inject': ['truth.faults', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY,
    'sim.step': ['clock.time_s'],
    'keypad.hold': ['program.state', 'syringe.diameter_m', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY + _FW,
    'keypad.chord': _RUN + _DISPLAY, 'power.on_with_key': ['power.on', 'volume.dispensed', 'ttl.state', 'program.state'] + _RUN + _DISPLAY + _FW,
    'direction.sticky': ['program.state', 'pump.direction'] + _RUN + _DISPLAY, 'program.edit': ['program.state', 'pump.direction'] + _RUN + _DISPLAY,
    'program.entry_lockout': ['firmware.state', 'program.state', 'pump.direction'] + _DISPLAY,
    'program.load_default': ['program.state', 'firmware.state', 'pump.direction'] + _RUN + _DISPLAY,
    'program.subprogram': ['program.state', 'volume.dispensed', 'ttl.state'] + _RUN + _DISPLAY, 'network.address': ['firmware.state'],
    'ttl.override_trigger': [], 'volume.set_units': ['firmware.state', 'volume.dispensed'] + _RUN + _DISPLAY,
    'serial.configure_port': [], 'serial.receive': [],
}
EFFECTS['serial.safe_mode'] = list(EFFECTS['serial.send'])
EFFECTS['fault.inject'] = EFFECTS['fault.inject'] + ['program.state', 'syringe.diameter_m', 'setup.state', 'firmware.state']   # Err.n resets the stored values
EFFECTS['panel.press'] = list(dict.fromkeys(EFFECTS['keypad.press'] + EFFECTS['keypad.hold'] + EFFECTS['keypad.chord']))
EFFECTS['panel.switch'] = list(dict.fromkeys(EFFECTS['power.set'] + EFFECTS['power.on_with_key']))
_STEPPED = ('power.set', 'power.toggle', 'keypad.press', 'rate.set', 'direction.set', 'pump.run', 'pump.purge', 'serial.send', 'ttl.set', 'syringe.load',
            'outlet.connect', 'medium.configure', 'fault.inject', 'plant.configure', 'serial.safe_mode')
ON_STEP = {k: list(_EVOLVE) + ['program.state', 'syringe.diameter_m'] for k in _STEPPED}   # keypad edits are stored 2 s after the last arrow
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s', 'program.state', 'syringe.diameter_m'] + _FW      # 1.1.0: Setup and firmware stores 2 s later
for _k in ('keypad.press', 'keypad.hold', 'keypad.chord', 'power.on_with_key', 'program.subprogram', 'panel.press', 'panel.switch'):
    ON_STEP[_k] = list(_EVOLVE) + ['program.state', 'syringe.diameter_m'] + _FW
ON_STEP_ZH = {'sim.step': '按0.1 s网格积分：推块按设定流量/内径推进（行程、限位夹、活塞到底），注射器—管路压力（顺应性与流阻的指数解），送出/抽入液量、空化，推力与失速，'
                          '程序阶段推进（体积到达、暂停定时、TTL触发≥100 ms），按键输入2 s后存入；不泵送且压力已松弛（或出口堵塞、没有流动）时整段跳过'}
EXEC_TEXT = {}
RESOURCES = {k: ['host_clock'] for k in ALL_CAPS}
for _k in ('syringe.load', 'syringe.unload', 'outlet.connect', 'collar.set', 'power.set', 'power.toggle', 'keypad.press', 'keypad.hold', 'keypad.chord',
           'power.on_with_key', 'panel.press', 'panel.switch'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'pump.run': ['indicator.pumping', 'pusher.position_m'], 'pump.stop': ['indicator.pumping'], 'power.set': ['display.text'], 'rate.set': ['display.text'],
            'keypad.press': ['display.text'], 'keypad.hold': ['display.text'], 'keypad.chord': ['display.text'], 'power.on_with_key': ['display.text'],
            'program.subprogram': ['run.state', 'run.phase'], 'panel.press': ['display.text'], 'panel.switch': ['power.on', 'display.text']}
_WB, _MAN, _DRW, _PH, _NIST, _PC, _LIB, _SIM = ('user_workbook', 'manufacturer_manual', 'manufacturer_drawing', 'manufacturer_product_page', 'nist_webbook', 'pubchem',
                                                'liquid_library', 'simulation_assumption')
CAP_SOURCES = {k: [_MAN] for k in ALL_CAPS}
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
                     '键盘锁定（未插锁定解除键=TTL针脚6未接地）时只能启停与查看设定；1.1.0起同一固件状态机还处理Setup序列、程序录入模式、开机特殊显示、体积单位修改与Pr:In子程序选择'
                     '（按住类操作见keypad.hold，同按两键见keypad.chord，开机按键见power.on_with_key）；显示已排体积时短按方向键即清零（手册为按住1 s，保留1.0.0行为），为0时进入体积单位修改。'],
    'serial.send': ['Basic协议命令集（DIA、RAT（含C/I前缀）、VOL（含UL/ML）、DIR（含STK）、RUN（含RUN E）、STP、PUR、DIS、CLD、FUN（含PRI/PRL/TRG）、PHN、LOC、BUZ、TRG、DIN、ROM、AL、PF、LN、BP、OUT5、IN、VER、RESET、*ADR）；'
                    '运行中经RS-232改的流量不存入阶段（手册6.1）；泵只回应自己网络地址（无地址=地址0）的命令，特殊通讯模式只回应带*的命令；LOC P回?NA；Basic回应不含帧字节（STX/ETX）。',
                    '1.2.0：计算机串口波特率（serial.configure_port，默认19200）与泵不同时泵收不到有效字符、没有回应也不执行；*ADR n B baud改波特率后回应按新波特率发出（计算机未改则收不到）；'
                    'Safe协议（手册10.2.3–10.2.4）：以STX开头的包按长度、CCITT CRC-16（XMODEM，初值0）与ETX检查，坏包回?COM，Basic模式也接受Safe包；SAF n开启Safe模式（n秒通讯超时，'
                    '0回Basic），Safe模式下回应带帧，不带STX的命令被丢弃（假设，手册未写）；有效包重置超时，超时报警r232（A?T）停泵与程序；Safe模式下报警自动发包（serial.receive）。'],
    'keypad.hold': ['按住时长决定结果：≥1 s进入程序录入/Setup/粘滞方向；Start/Stop按住时程序启动，1 s后清洗到松开（按住时长=松开时刻，随仿真时钟step执行）；方向键按住在0.5 s后每0.25 s加1（假设）；'
                    '最左方向键按住先把最左位滚到0，此后每秒小数点右移一位（假设）；程序录入中按住最右方向键切换PS的0.1 s小数点。'],
    'keypad.chord': ['两个相邻方向键同时按：数值显示（流量、待排体积、内径）中在两键之间设小数点，再按同一对清除；程序录入的PS参数中切换0.1 s小数点；其他情况显示----。'],
    'power.on_with_key': ['只在电源关时：接通电源并执行开机特殊功能；默认程序菜单只有手册给出的rECP往复程序（按非方向键载入，假设）；P-L.n按方向键切换、非方向键或2 s后存入。'],
    'network.address': ['单台泵：rECP/dUAL/ALtr需要第二台泵（CBL-DUAL-3），仿真中不存在：泵在开机和启动后2 s显示该模式（寻找第二台泵），程序照常单泵运行；'
                        '1.2.0：波特率须与计算机串口（serial.configure_port）相同，否则RS-232没有通讯；改波特率暂停Safe模式超时计时直到下一个有效包。'],
    'serial.safe_mode': ['Safe包由运行层按手册格式组帧（长度字节、CCITT CRC-16高低字节）；代理也可用serial.send直接发自己组的包（字符码0–255表示字节）；包间0.5 s字节超时不建模（一次调用即一整包）。'],
    'serial.configure_port': ['只有波特率；数据帧8N1固定（手册10.2）；不建模电缆噪声与误码（低波特率的抗噪优势只在手册说明中）。'],
    'serial.receive': ['只收Safe模式的自动报警包（复位、失速、程序错误/越界、通讯超时）；自动发出的报警不清除报警（手册10.3），须回应一条有效命令或按键清除；最多保留100个包。'],
    'setup.configure': ['低噪声LN（手册8.2）：开启时电机只走整的半步（0.8504 µm，手册12.5的最粗微步），未满半步的行程留到下一步，送出流量按步进脉动（低速时明显）；'
                        '关闭时按细微步视为连续推进（1/8微步的脉动不建模）；电机声音不建模。'],
    'ttl.override_trigger': ['覆盖只在程序运行期间有效，程序停止或复位后回到setup的默认触发方式；ET/BT为一次性重定向。'],
    'syringe.load': ['注射器按目录（手册12.7表）给内径与容量，筒长按行程推算；装液量超出4.74 in行程时只能部分装（手册：140 mL注射器部分装液）；气泡按大气压体积给出。'],
    'outlet.connect': ['管路与针头按Hagen–Poiseuille层流流阻（目录工程值）；下游为恒定背压；不建模多相流与管路惯性。'],
    'ttl.set': ['TTL输入按手册7.5：电平须保持≥100 ms才被识别；触发方式FT/FH/F2/LE/ST/T2/SP/P2/RL/RH/SL/SH/OF。'],
}
LIMITATIONS = [
    '所选配置：New Era NE-1000单通道可编程注射泵（X系列之前的标准NE-1000，12 V DC外部电源），装一支注射器；手册（NE-1000 Syringe Pump User Manual）为主来源，厂家尺寸图Rev A给外形与行程。',
    '驱动：步进电机加丝杠，推进速度=流量/设定截面积；最高5.1005 cm/min、最低0.004205 cm/hr；最大推力35 lbf（最低速）到18 lbf（最高速），中间按速度对数插值（工程）；'
    '细微步时按连续推进（1/8微步脉动不建模）；1.2.0：低噪声模式LN（手册8.2）开启时推块按整的半步0.8504 µm（手册12.5）前进，送出流量与压力出现步进脉动，计数与限位按指令行程。',
    '流体：注射器+管路+针头为一个集总顺应性C（塑料/玻璃/钢注射器形变、管壁形变、液体压缩、气泡按理想气体）与一个层流流阻R；压力按p_ss=p_back+R·Q的指数解逐0.1 s积分，'
    '因此塑料注射器、气泡与高流阻管路会出现启动滞后与停止后的拖尾流；顺应性与摩擦为工程值。',
    '失速：推力（压力×真实截面积+摩擦）超过速度对应的最大推力持续0.2 s（工程去抖）、推块碰到限位夹、活塞推到底或拉到注射器刻度末端时报警StAL并暂停程序；本型号没有压力传感器。',
    '抽取时管路内压力低于空化绝对压力（20 kPa，工程）时析出气泡，气泡留在注射器中；出口储液不足时吸入空气。',
    '已排体积按设定内径计算（泵自己的计算值），真实送出液量按真实内径（评测者可设内径偏差）、顺应性与气泡计算，两者可以不同。',
    '程序：41阶段，功能RAT/FIL/INC/DEC/STP/JMP/LPS/LOP/LPE/PAS/IF/EVN/EVS/EVR/CLD/BEP/OUT按手册第8章，1.1.0起加子程序选择Pr:In/Pr:nn与触发覆盖tr:aa（含Et/bt重定向）、粘滞方向；'
    '1.2.0：RS-232 Safe协议（SAF、CRC包、通讯超时报警r232、自动报警包）与计算机串口波特率（不同则无通讯）。',
    '开机存储器错误（1.2.0，手册6.14）：评测者注入nv_memory_invalid（下次开机显示Err.1，存储的程序、内径、设置、网络与Safe模式复位为出厂值，一次性）或nv_memory_failed'
    '（清除前每次开机Err.2并复位）；任意键清除显示；存储器本身的电子故障不建模。',
    '显示：4位七段LCD（含小数点）与8个LED（mm、mL、µL、min、hr、Dispensed、Withdraw、Pumping）加RS-232接收指示；文字StAL、oor、or:nn、Er:nn、----、LoC-、UntS、PAUS、 oFF、PurG、PS:nn按手册6.14/6.15字形（冒号不计位）；'
    'LED闪烁（暂停时Pumping闪烁、单位修改时单位LED闪烁、粘滞方向时Withdraw闪烁、存入时显示闪烁）在状态槽中表示为点亮或熄灭；面板层（panel.capture/panel.frame）按仿真时钟画出闪烁。',
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


OWNER_GUARDED = {'keypad.press', 'keypad.hold', 'keypad.chord', 'rate.set', 'volume.set', 'direction.set', 'phase.select',
                 'phase.set_function', 'program.clear', 'pump.run', 'pump.stop', 'pump.purge', 'volume.clear', 'setup.configure', 'serial.send', 'serial.safe_mode',
                 'syringe.set_diameter', 'program.edit', 'program.entry_lockout', 'program.load_default', 'program.subprogram', 'network.address',
                 'ttl.override_trigger', 'volume.set_units', 'direction.sticky', 'panel.press'}   # 1.2.0 run-owner rule (_guard_run); 1.2.1: not the power switch


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if ALL_CAPS[k]['visibility'] != 'agent' or k in OWNER_GUARDED else []   # 1.2.1: not power.set / power.toggle (hard switch)
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
    for k, v in ALL_CAPS.items():
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
                   'dispensed += A_set |dx|  (pump display);  delivered = integral of (p - p_back) / R dt  (truth)',
                   'Low Noise (1.2.0): the pusher moves in whole half steps dx_m = 0.8504 um x trunc((lag + dx) / 0.8504 um); Q_in = A_true dx_m / h (pulsating)'],
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
