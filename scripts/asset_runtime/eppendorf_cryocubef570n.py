"""CHEM-197 Eppendorf CryoCube F570n ULT freezer (230 V, F571300031; 2026 brochure configuration): process runtime.

Device (2026 brochure AA01016520/EN3/ESE; operating manual 004550117901-00/102020; German product page): set range -50 ... -86 C (factory -80 C),
570 L in 5 compartments with 5 insulated inner doors, 4 shelves (65 kg each), up to 25 racks; 8.3 kWh/day (230 V, -80 C, 20 C ambient, empty with
shelves), pull-down 20 C -> -80 C in 3 h 53 min, recovery to -80 C 29 min after a 60 s opening, heat output 347 W; 130 mm polyurethane walls (no
vacuum insulation panels on the n model); two-stage cascade R-290 / R-170, air cooled; 7.5 A at 230 V; ambient 15-32 C, <= 80 %RH; probe in the
middle compartment; alarms: interior temperature (high / low limits, on-site and remote delays 0-40 min, 0 -> 15 s), mains outage (battery back-up
circuit, 72 h panel and alarm), battery low, system errors E-01 ... E-04, filter clean; four-digit lock code; automatic pressure compensation after
closing, auto vent valve 1-2 min (1-2 h when iced); air filter every 3 months, seals monthly; 110.3 x 89.8 x 195 cm, 281 kg.

Process model (fixed 1 s grid anchored at t = 0; SI units, temperatures in K, the panel shows integer C):
  * Cabinet: liner node (stainless liner, evaporator loops, shelves, inner doors) - foam node - room (two equal conductances 2 UA); five
    compartment air nodes (implicit) exchange heat with the liner, with the stored items and with room air entering through open doors
    (buoyant doorway flow Cd W H/3 sqrt(g H d_rho / rho), scaled by the outer and inner door openings; inner-door gaps when only the outer door is open)
    and through the gasket; the probe sits in compartment 3 behind a first-order lag.
  * Refrigeration: two-stage cascade abstracted as one heat pump on the liner: capacity limit Q_max(T_liner) (linear to the cascade floor), supply
    limit 7.5 A x 230 V, COP = eta T_e / (T_c - T_e) with the condenser temperature from the room, the rejected heat and the filter dust; a PI
    controller on the probe sets the capacity (real units switch / modulate the compressors - not published); E-04 when the condenser is too hot.
  * Moisture: water in the entering air above saturation at the compartment temperature deposits as frost (latent heat to the liner); gasket
    infiltration ices the door gasket and the auto vent valve; frost lowers the liner-air heat transfer; frost melts when the liner is above 0 C
    (defrosting with the power off), melt water stays until wiped (interior.dry).
  * Pressure: after the outer door closes the cooling air leaves a vacuum relieved through the vent valve (automatic / auto vent pressed / iced);
    the outer door cannot be pulled open above a pressure difference.
  * Items: racks / boxes / samples with water (ice / liquid with latent heat), polymer, cardboard, steel and aluminium masses; enthalpy state;
    convective and shelf contact heat transfer; degradation index = equivalent storage time at -80 C (Arrhenius, engineering), thaw events.
  * Panel: alarms with delays, signal tone and mute / test, BMS remote relay, battery back-up (charging, 72 h), lock code and programming mode,
    optional monitoring log (VisioNize abstraction).
  * Ledgers: cabinet energy (conduction, air exchange, refrigeration, items in / out, melt water wiped), water (frost, melt water) and
    interior air moles close exactly; electrical energy and room heat are accumulated."""
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.eppendorf_cryocubef570n"
CATALOG_ID = "CHEM-197"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t2_vacuum/eppendorf_cryocubef570n'
LIBS = {'instrument': 'instrument_model.json'}


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
DT = 1.0
T0 = 273.15
G_ACC = 9.80665
R_GAS = 8.314462618
R_AIR = 287.05                                  # J/(kg K), dry air
CP_AIR = 1006.0                                 # J/(kg K)
M_AIR = 0.0289647                               # kg/mol
M_W = 0.01801528                                # kg/mol
L_FUS = 333.55e3                                # J/kg, ice melting at 0 C
L_SUB = 2.834e6                                 # J/kg, sublimation (deposition) of water vapour on ice
L_VAP = L_SUB - L_FUS
CP_WATER = 4184.0                               # J/(kg K), liquid water
CP_ICE_A, CP_ICE_B = 185.0, 6.89                # cp_ice(T) = A + B T (J/(kg K)); 2067 at 0 C, 1515 at -80 C (Giauque-Stout fit)
CP_MAT = {'polymer_kg': 1920.0, 'cardboard_kg': 1340.0, 'steel_kg': 500.0, 'aluminium_kg': 900.0}   # J/(kg K), room-temperature literature values
N_COMP = 5
PROBE_COMP = 2                                  # middle compartment (0-based)
DOOR_MAX = 1.75                                 # rad
INNER_MAX = 1.75
OUTER_FOR_INNER = 1.40                          # outer door opening needed to swing an inner door (rad)
INNER_FOR_LOADING = 1.20
HANDLE_DOWN = 1.0                               # handle angle when pulled down (unlocked), rad
W_INT, D_INT, H_INT = DOC['internal_dimensions_m']['w'], DOC['internal_dimensions_m']['d'], DOC['internal_dimensions_m']['h']
H_COMP = H_INT / N_COMP
V_COMP = W_INT * D_INT * H_COMP
P_MAX_EL = DOC['current_a'] * 230.0             # W, supply limit (7.5 A at 230 V)
T_SET_DEFAULT = DOC['factory_set_temperature_k']
STATES = ['off', 'outage', 'pull_down', 'running']
FAULTS = ['interior_sensor_failure', 'condenser_sensor_failure', 'heat_exchanger_sensor_failure', 'compressor_failure', 'condenser_fan_failure',
          'mains_outage', 'battery_aged', 'door_gasket_damage']
ITEM_KINDS = ['rack', 'box', 'sample', 'other']
LOG_PERIOD = 60.0
MAX_EVENTS_LOG = 3000
MAX_LOG = 20000
DAY = 86400.0
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


def p_sat(T):
    """Saturation vapour pressure (Pa): over ice below 0 C (Murphy and Koop 2005, eq. 7), over water above (Magnus, Alduchov-Eskridge)."""
    if T < T0:
        return math.exp(9.550426 - 5723.265 / T + 3.53068 * math.log(T) - 0.00728332 * T)
    t = T - T0
    return 610.94 * math.exp(17.625 * t / (t + 243.04))


def rho_air(T, p):
    return p / (R_AIR * T)


def w_vap(pv, p):
    """kg water vapour per kg dry air."""
    return 0.62198 * pv / max(p - pv, 1.0)


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
CODE = {'type': 'string', 'pattern': r'[0-9]{4}'}
ITEM_ID = {'type': 'string', 'pattern': r'[A-Za-z0-9_.:/-]{1,64}'}


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


T_SET_LO, T_SET_HI = (round(x, 6) for x in DOC['set_temperature_range_k'])
PLANT_EXAMPLE = dict(ambient_k=293.15, relative_humidity=0.5, pressure_pa=101325.0, mains_v=230.0, monitoring=True, remote_connected=False, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 273.15, 318.15), relative_humidity=number('1', 0.0, 1.0), pressure_pa=number('Pa', 70000.0, 110000.0),
                 mains_v=number('V', 180.0, 260.0), monitoring=BOOL, remote_connected=BOOL, noise_seed=integer(0, 2 ** 31 - 1)),
            optional=('mains_v', 'monitoring', 'remote_connected'))
SETTINGS = obj(dict(set_point_k=number('K', T_SET_LO, T_SET_HI), high_alarm_k=number('K', 150.0, 300.0), low_alarm_k=number('K', 150.0, 300.0),
                    onsite_delay_s=number('s', 0.0, 2400.0), remote_delay_s=number('s', 0.0, 2400.0)),
               optional=('set_point_k', 'high_alarm_k', 'low_alarm_k', 'onsite_delay_s', 'remote_delay_s'))
SETTINGS_EXAMPLE = dict(set_point_k=193.15, high_alarm_k=203.15, low_alarm_k=183.15)
ITEM = obj(dict(item_id=ITEM_ID, compartment=integer(1, N_COMP), kind=enum(ITEM_KINDS), label=TEXT, water_kg=number('kg', 0.0, 65.0),
                polymer_kg=number('kg', 0.0, 65.0), cardboard_kg=number('kg', 0.0, 65.0), steel_kg=number('kg', 0.0, 65.0), aluminium_kg=number('kg', 0.0, 65.0),
                initial_k=number('K', 150.0, 330.0)),
           optional=('label', 'water_kg', 'polymer_kg', 'cardboard_kg', 'steel_kg', 'aluminium_kg'))
ITEM_EXAMPLE = dict(item_id='rack_A1', compartment=3, kind='rack', label='Freezer8/C3/R1', water_kg=1.2, polymer_kg=0.6, cardboard_kg=0.8, steel_kg=2.5, initial_k=193.15)
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'operation', 'door', 'alarm', 'maintenance', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
LOGREC = obj(dict(time_s=number('s'), temperature_c=nullable(number('1')), outer_door_open=BOOL, alarms=array(TEXT, 0, 16)))
_BODY = ['cabinet']
_PANEL = ['control_panel']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明室温、相对湿度、大气压、电源电压、是否接监控（VisioNize，开门与温度记录）、是否由计算机远程控制与噪声种子', 'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['mains_switch'], '开关盖板后的主电源开关通/断（压缩机延时启动）')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['mains_switch'], '主电源开关拨一下')
cap('battery.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['battery_switch'], '电池开关通/断（备用电路：停电时为面板与报警供电72 h）')
cap('battery.toggle', obj({}), obj(dict(on=BOOL)), {}, ['battery_switch'], '电池开关拨一下')
cap('panel.unlock', obj(dict(code=CODE)), obj(dict(programming=BOOL)), dict(code='1234'), _PANEL, 'lock软键＋四位锁定码：进入编程模式（锁定码启用时）')
cap('panel.lock', obj({}), obj(dict(programming=BOOL)), {}, ['lock_key'], 'lock软键：退出编程模式并保存参数')
cap('code.change', obj(dict(code=CODE)), obj(dict(code_active=BOOL)), dict(code='1234'), _PANEL, 'code change软键：设置四位锁定码（0000为停用）')
cap('settings.configure', SETTINGS, obj(dict(settings=FREE_OBJ)), SETTINGS_EXAMPLE, _PANEL,
    'set temp / high alarm / low alarm软键与8/9键：设定腔温（−50…−86 °C）、高/低温报警限与现场/远程报警延时')
cap('alarm.mute', obj({}), obj(dict(muted=BOOL, test=BOOL)), {}, ['mute_key'], 'alarm test mute软键：静音当前报警音（无报警时测试：报警音响3 s、7个指示灯全亮）')
cap('handle.set', obj(dict(locked=BOOL)), obj(dict(locked=BOOL)), dict(locked=False), ['door_handle'], '外门把手：向前向下拉到底（解锁）或向上压到锁定位置')
cap('lock.set', obj(dict(locked=BOOL)), obj(dict(locked=BOOL)), dict(locked=True), ['door_handle'], '把手上的钥匙锁芯锁/开（只在把手锁定时）', 'agent', 'robot_skill')
cap('door.move', obj(dict(angle_rad=number('rad', 0.0, DOOR_MAX))), obj(dict(angle_rad=number('rad'))), dict(angle_rad=1.5), ['outer_door'],
    '外门转到给定角度（0为关闭；拉开须把手已解锁且内外压差已平衡；关门须内门全关、把手在下方）', 'agent', 'robot_skill')
cap('inner_door.move', obj(dict(compartment=integer(1, N_COMP), angle_rad=number('rad', 0.0, INNER_MAX))), obj(dict(compartment=integer(1, N_COMP), angle_rad=number('rad'))),
    dict(compartment=3, angle_rad=1.5), ['inner_door_1', 'inner_door_2', 'inner_door_3', 'inner_door_4', 'inner_door_5'], '某个内室的保温内门转到给定角度（须外门大开）', 'agent', 'robot_skill')
cap('vent.press', obj({}), obj(dict(pressed=BOOL)), {}, ['auto_vent_button'], 'auto vent泄压阀按键：加速压力平衡（1–2 min）')
cap('vent.deice', obj({}), obj(dict(removed_kg=number('kg'))), {}, ['auto_vent_button'], '开门后除去auto vent泄压阀上的冰（故障排除）', 'agent', 'robot_skill')
cap('seals.clean', obj({}), obj(dict(removed_kg=number('kg'))), {}, ['outer_door'], '清洁外门门封上的冰霜（每月）', 'agent', 'robot_skill')
cap('filter.clean', obj({}), obj(dict(cleaned=BOOL)), {}, ['air_filter'], '清洁外门下方的空气滤网与进风格栅（每3个月）', 'agent', 'robot_skill')
cap('load.place', ITEM, obj(dict(item_id=TEXT, compartment=integer(1, N_COMP), compartment_load_kg=number('kg'))), ITEM_EXAMPLE, ['shelves'],
    '把冻存架/盒/样品放进某个内室（外门与该内门打开；每室≤65 kg、≤5个冻存架；位置标签由外部样品管理系统给出）', 'agent', 'robot_skill')
cap('load.remove', obj(dict(item_id=ITEM_ID)), obj(dict(item_id=TEXT, removed=BOOL)), dict(item_id='rack_A1'), ['shelves'], '从内室取出物品（外门与该内门打开）', 'agent', 'robot_skill')
cap('inventory.read', obj({}), obj(dict(items=array(FREE_OBJ, 0, 500))), {}, ['shelves'],
    '库存位置（宿主/外部样品管理系统声明的物品、内室与标签；本机不识别样品）', 'agent', 'query')
cap('interior.dry', obj({}), obj(dict(wiped_kg=number('kg'))), {}, ['cabinet'], '除霜后擦干融水并擦干门封（须断电、外门开、霜已化完）', 'agent', 'robot_skill')
cap('log.read', obj(dict(last=integer(1, MAX_LOG))), obj(dict(records=array(LOGREC, 0, MAX_LOG))), dict(last=60), _PANEL,
    '监控记录（接VisioNize时每60 s记录显示温度、外门开关与报警；未接监控则无记录）', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _BODY,
    '事件记录（电源、门、报警、维护、操作）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(thermal=FREE_OBJ, items=FREE_OBJ, power=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, _BODY,
    '评测者读取过程真值：各内室空气、内胆、泡沫、探头温度，制冷量、电功率、COP与冷凝温度，霜与融水，内外压差，物品温度与降解，电池与滤网', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '箱体能量（J）、水（kg）与内腔空气（mol）的守恒账，电能与向室内散热', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='mains_outage', active=True), _BODY,
    '故障注入：腔温/冷凝器/换热器传感器失效（E-01/E-02/E-03）、压缩机故障、冷凝风机故障、市电中断、电池老化、门封损坏', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * DAY))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'backup.co2': 'CO₂备份系统为选件（−50…−70 °C；未选，经穿线孔接入）。', 'backup.ln2': 'LN₂备份系统为选件（至−85 °C；未选）。',
    'chart_recorder.read': '圆图记录仪为选件（7天记录；未选）。', 'cooling_water.connect': '水冷只在F570hw；F570n为风冷。',
    'rs485.command': '手册：RS-485接口仅供内部使用。', 'sample.identify': '本机不识别样品（库存位置由外部样品管理系统维护；条码扫描不属于本机）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'cabinet', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'cabinet', '环境条件是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'mains_switch', '主电源开关是否接通')
slot('battery.on', BOOL, '1', 'setpoint', 'battery_switch', '电池开关（备用电路）是否接通')
slot('state.current', enum(STATES), '1', 'derived', 'control_panel', '状态：off（主开关断）、outage（市电中断）、pull_down（降温中，尚未到设定）、running（控温）')
slot('temperature.interior_k', nullable(number('K')), 'K', 'sensor', 'control_panel', '中间内室探头测得的腔温（面板无电或E-01时为空）')
slot('display.temperature', nullable(number('1')), '1', 'derived', 'display', '显示屏腔温数字（°C取整；显示用）')
slot('display.code', nullable(enum(['E-01', 'E-02', 'E-03', 'E-04'])), '1', 'derived', 'display', '显示屏系统错误代码')
slot('display.flashing', BOOL, '1', 'derived', 'display', '停电时显示每10 s闪烁')
slot('settings.current', FREE_OBJ, '1', 'setpoint', 'control_panel', '设定：腔温、高/低温报警限、现场/远程报警延时')
slot('panel.programming', BOOL, '1', 'setpoint', 'lock_key', '编程模式（锁定码启用时须先解锁）')
slot('panel.code_active', BOOL, '1', 'setpoint', 'lock_key', '锁定码是否启用')
slot('alarms.active', array(TEXT, 0, 16), '1', 'derived', 'control_panel', '当前报警：TEMP_HIGH、TEMP_LOW、POWER_FAIL、BATTERY_LOW、E-01…E-04、FILTER_CLEAN')
slot('alarm.audible', BOOL, '1', 'derived', 'control_panel', '报警音（可静音；新报警重新鸣响）')
slot('alarm.remote_relay', BOOL, '1', 'derived', 'control_panel', 'BMS远程报警继电器（停电、腔温超限经远程延时；失电安全：停电且电池耗尽或电池开关断开时也报警，主开关断开时不报警）')
slot('indicator.temp_alarm', BOOL, '1', 'derived', 'led_temp_alarm', 'temp alarm指示灯')
slot('indicator.power_fail', BOOL, '1', 'derived', 'led_power_fail', 'power fail指示灯（停电时闪烁）')
slot('indicator.battery_low', BOOL, '1', 'derived', 'led_battery_low', 'battery low指示灯（电池电压低或无市电）')
slot('indicator.fault', BOOL, '1', 'derived', 'led_fault', 'fault指示灯（系统错误）')
slot('indicator.filter_clean', BOOL, '1', 'derived', 'led_filter_clean', 'filter clean指示灯（滤网须清洁）')
slot('indicator.remote', BOOL, '1', 'derived', 'led_remote', 'remote control指示灯（由计算机控制）')
slot('indicator.lock', BOOL, '1', 'derived', 'led_lock', 'lock指示灯（编程模式时亮）')
slot('door.outer_angle_rad', number('rad'), 'rad', 'setpoint', 'outer_door', '外门开启角（0关闭）')
slot('door.handle_angle_rad', number('rad'), 'rad', 'setpoint', 'door_handle', '把手转角（0向上锁定，1.0向下解锁）')
slot('door.cylinder_locked', BOOL, '1', 'setpoint', 'door_handle', '把手锁芯是否锁上')
for _i in range(1, N_COMP + 1):
    slot(f'door.inner{_i}_angle_rad', number('rad'), 'rad', 'setpoint', f'inner_door_{_i}', f'第{_i}内室保温内门开启角（0关闭；1为最上层）')
slot('inventory.count', integer(0, 500), '1', 'derived', 'shelves', '库存物品数（宿主声明）')
slot('truth.thermal', FREE_OBJ, '1', 'process_truth', 'cabinet', '各内室空气、内胆、泡沫、探头温度，制冷量、电功率、COP、冷凝温度，霜与融水，内外压差', 'evaluator')
slot('truth.items', FREE_OBJ, '1', 'process_truth', 'shelves', '物品温度、液态分数、最高温度、−80 °C等效储存时间与解冻次数', 'evaluator')
slot('truth.power', FREE_OBJ, '1', 'process_truth', 'cabinet', '市电、电池电量、滤网积尘、压缩机状态', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'cabinet', '已注入的故障', 'evaluator')
_INDS = ['indicator.temp_alarm', 'indicator.power_fail', 'indicator.battery_low', 'indicator.fault', 'indicator.filter_clean', 'indicator.remote', 'indicator.lock']
_DOORS = ['door.outer_angle_rad', 'door.handle_angle_rad'] + [f'door.inner{i}_angle_rad' for i in range(1, N_COMP + 1)]
SCENE_MIRRORED = set(['display.temperature'] + _INDS + _DOORS)


# ---------------------------------------------------------------- items
def item_capacity_sensible(it):
    return sum(CP_MAT[k] * it[k] for k in CP_MAT)


def h_ice(T):
    return CP_ICE_A * (T - T0) + 0.5 * CP_ICE_B * (T * T - T0 * T0)


def item_H(it, T):
    """Enthalpy (J) relative to liquid water and solids at 0 C (fully frozen below 0 C, fully liquid above)."""
    Cs, mw = item_capacity_sensible(it), it['water_kg']
    if T >= T0:
        return (Cs + mw * CP_WATER) * (T - T0)
    return Cs * (T - T0) + mw * (h_ice(T) - L_FUS)


def item_T(it, H):
    """(temperature, liquid fraction) from the enthalpy."""
    Cs, mw = item_capacity_sensible(it), it['water_kg']
    if H >= 0.0:
        C = Cs + mw * CP_WATER
        return (T0 + H / C if C > 0 else T0), (1.0 if mw > 0 else 0.0)
    if mw > 0 and H >= -mw * L_FUS:
        return T0, 1.0 + H / (mw * L_FUS)
    if mw <= 0:
        return T0 + H / Cs, 0.0
    a = 0.5 * mw * CP_ICE_B                                                 # a T^2 + b T + c = 0 below 0 C
    b = Cs + mw * CP_ICE_A
    c = -(b * T0 + a * T0 * T0) - H - mw * L_FUS
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a), 0.0


def item_dHdT(it, T, phi):
    Cs, mw = item_capacity_sensible(it), it['water_kg']
    if mw > 0 and 0.0 < phi < 1.0:
        return 1e12                                                         # melting: latent plateau
    if T >= T0 and mw > 0 and phi >= 1.0:
        return Cs + mw * CP_WATER
    return Cs + mw * (CP_ICE_A + CP_ICE_B * T)


# ---------------------------------------------------------------- runtime
class EppendorfCryocubef570nRuntime:
    def __init__(self, instance_id='eppendorf_cryocubef570n_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.mains_switch = False
        self.battery_switch = False
        self.battery = 1.0                                # charge fraction
        self.dust = 0.0                                   # filter dust (1 = needs cleaning)
        self.settings = dict(set_point_k=T_SET_DEFAULT, high_alarm_k=T_SET_DEFAULT + 10.0, low_alarm_k=T_SET_DEFAULT - 10.0,
                             onsite_delay_s=DOC['alarm_delay_onsite_s']['factory'], remote_delay_s=DOC['alarm_delay_remote_s']['factory'])
        self.code = '0000'
        self.programming = False
        self.door, self.handle, self.cyl = 0.0, 0.0, False
        self.inner = [0.0] * N_COMP
        self.vent_until = -1.0
        self.items = {}
        self.Tl = self.Tf = self.Tp = None
        self.Ta = None
        self.n_gas = None
        self.integ = 0.0
        self.Q = self.P = 0.0
        self.T_cond = None
        self.e04, self.e04_since = False, -1.0
        self.reached = False
        self.frost = dict(liner=0.0, gasket=0.0, vent=0.0)
        self.melt = 0.0
        self.faults = set()
        self.alarm_since = {}
        self.alarms, self.relay, self.relay_codes = [], False, set()
        self.muted_set, self.test_until = set(), -1.0
        self.events, self.events_dropped = [], 0
        self.log, self.next_log = [], 0.0
        self.revision = 0
        self.led = dict(e_initial_j=0.0, e_conduction_in_j=0.0, e_exchange_in_j=0.0, e_refrigeration_out_j=0.0, e_items_added_j=0.0, e_items_removed_j=0.0,
                        e_meltwater_wiped_j=0.0, w_deposited_kg=0.0, w_wiped_kg=0.0, w_deiced_kg=0.0, n_initial_mol=0.0, n_door_mol=0.0, n_vent_mol=0.0,
                        electric_energy_j=0.0, room_heat_j=0.0)
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
        return EppendorfCryocubef570nSession(self, name)

    def _event(self, kind, code, message):
        if len(self.events) >= MAX_EVENTS_LOG:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message))

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    # ---- derived quantities
    def _mains_ok(self):
        return self.plant is not None and self.mains_switch and 'mains_outage' not in self.faults

    def _on_battery(self):
        return self.plant is not None and self.battery_switch and self.battery > 0.0 and not self._mains_ok()

    def _panel_on(self):
        return self._mains_ok() or self._on_battery()

    def _p_amb(self):
        return self.plant['pressure_pa']

    def _T_mean(self):
        return sum(self.Ta) / N_COMP

    def _p_int(self):
        return self.n_gas * R_GAS * self._T_mean() / ENG['interior_gas_volume_m3']

    def _dp(self):
        return self._p_amb() - self._p_int()

    def _cabinet_U(self):
        U = ENG['liner_heat_capacity_j_k'] * (self.Tl - T0) + ENG['foam_heat_capacity_j_k'] * (self.Tf - T0)
        U += sum(ENG['air_heat_capacity_j_k'] * (T - T0) for T in self.Ta)
        U += sum(it['H'] for it in self.items.values())
        U -= L_SUB * (self.frost['liner'] + self.frost['gasket'] + self.frost['vent']) + L_VAP * self.melt
        return U

    def _comp_load(self, c):
        return sum(it['mass_kg'] for it in self.items.values() if it['compartment'] == c)

    def _sensor_k(self):
        if not self._panel_on() or 'interior_sensor_failure' in self.faults:
            return None
        return self.Tp + ENG['display_noise_k'] * _gauss(self.seed, self.tick, 1)

    def _codes(self):
        C = []
        if not self._panel_on():
            return C
        for f, c in (('interior_sensor_failure', 'E-01'), ('condenser_sensor_failure', 'E-02'), ('heat_exchanger_sensor_failure', 'E-03')):
            if f in self.faults:
                C.append(c)
        if self.e04:
            C.append('E-04')
        return C

    def _state(self):
        if self.plant is None or not self.mains_switch:
            return 'off'
        if not self._mains_ok():
            return 'outage'
        return 'running' if self.reached else 'pull_down'

    # ---- physics
    def step(self, dt_s):
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = t_end
            self.t_grid = math.floor(t_end / DT + 1e-9) * DT
            self.tick = int(round(self.t_grid / DT))
            return self.t
        while self.t_grid + DT <= t_end + 1e-9:
            self.t = self.t_grid
            self._tick(DT)
            self.t_grid += DT
            self.tick += 1
        self.t = round(t_end, 9)                                              # no float drift of the clock from split steps
        return self.t

    def _exchange(self, Tc):
        """Room-air volume flow into each compartment (m3/s; the same volume leaves): doorway buoyancy flow through the opened doors, gaps of closed
        inner doors behind an open outer door, gasket infiltration with the outer door closed."""
        Ta_amb, pa = self.plant['ambient_k'], self._p_amb()
        ro = rho_air(Ta_amb, pa)
        f_out = min(self.door / 1.2, 1.0)
        out = []
        for i in range(N_COMP):
            ri = rho_air(Tc[i], pa)
            if f_out > 0.0:
                f_in = max(min(self.inner[i] / 1.0, 1.0), ENG['inner_door_leak_fraction'])
                v = ENG['door_discharge_coefficient'] * W_INT * H_COMP / 3.0 * math.sqrt(G_ACC * H_COMP * abs(ri - ro) / (0.5 * (ri + ro)))
                out.append(f_out * f_in * v)
            else:
                out.append(0.0)
        inf = ENG['gasket_infiltration_m3_s'] * (1.0 + ENG['gasket_frost_infiltration_gain'] * self.frost['gasket']) * (5.0 if 'door_gasket_damage' in self.faults else 1.0)
        return out, inf / N_COMP

    def _tick(self, h):
        L = self.led
        Tamb, pa = self.plant['ambient_k'], self._p_amb()
        Ca, Cl, Cf = ENG['air_heat_capacity_j_k'], ENG['liner_heat_capacity_j_k'], ENG['foam_heat_capacity_j_k']
        ua2 = 2.0 * ENG['ua_wall_w_k']
        ro = rho_air(Tamb, pa)
        w_amb = w_vap(self.plant['relative_humidity'] * p_sat(Tamb), pa)
        g_la = ENG['liner_air_ua_w_k'] / (1.0 + self.frost['liner'] / ENG['frost_ua_ref_kg'])
        vex, vinf = self._exchange(self.Ta)
        dQ_liner = 0.0
        # ---- items: state, link conductances
        by_comp = [[] for _ in range(N_COMP)]
        for it in self.items.values():
            T, phi = item_T(it, it['H'])
            it['T'], it['phi'] = T, phi
            C_eff = item_dHdT(it, T, phi)
            it['G_air'] = min(it['G_air0'], 0.5 * C_eff / h)
            it['G_c'] = min(it['G_c0'], 0.5 * C_eff / h)
            by_comp[it['compartment'] - 1].append(it)
        # ---- compartment air (implicit), item / liner flows, moisture deposition
        dep_liner = dep_gasket = dep_vent = 0.0
        for i in range(N_COMP):
            gx = ro * (vex[i] + vinf) * CP_AIR                                 # W/K to the room
            num = Ca * self.Ta[i] + h * (g_la * self.Tl + gx * Tamb + sum(it['G_air'] * it['T'] for it in by_comp[i]))
            den = Ca + h * (g_la + gx + sum(it['G_air'] for it in by_comp[i]))
            Tn = num / den
            dQ_liner -= h * g_la * (self.Tl - Tn)
            L['e_exchange_in_j'] += h * gx * (Tamb - Tn)
            for it in by_comp[i]:
                it['H'] += h * it['G_air'] * (Tn - it['T'])
            self.Ta[i] = Tn
            w_sat = w_vap(p_sat(Tn), pa)                                     # vapour above saturation deposits as frost
            m_ex = ro * vex[i] * max(w_amb - w_sat, 0.0) * h
            m_in = ro * vinf * max(w_amb - w_sat, 0.0) * h
            dep_liner += m_ex + m_in * (1.0 - ENG['gasket_frost_fraction'] - ENG['vent_frost_fraction'])
            dep_gasket += m_in * ENG['gasket_frost_fraction']
            dep_vent += m_in * ENG['vent_frost_fraction']
        for it in self.items.values():                                       # shelf contact with the liner
            q = h * it['G_c'] * (self.Tl - it['T'])
            it['H'] += q
            dQ_liner -= q
        dep = dep_liner + dep_gasket + dep_vent
        self.frost['liner'] += dep_liner
        self.frost['gasket'] += dep_gasket
        self.frost['vent'] += dep_vent
        L['w_deposited_kg'] += dep
        dQ_liner += L_SUB * dep                                               # deposition releases the sublimation heat into the liner
        # ---- refrigeration (cascade abstracted as one heat pump on the liner)
        mains = self._mains_ok()
        cooling = mains and 'compressor_failure' not in self.faults
        uac = ENG['condenser_ua_w_k'] * max(1.0 - ENG['condenser_dust_ua_loss'] * min(self.dust, 1.5), 0.05)   # dust on the filter starves the condenser
        if 'condenser_fan_failure' in self.faults:
            uac *= ENG['condenser_fan_failure_ua_fraction']
        Q = 0.0
        P = ENG['base_power_w'] if mains else 0.0
        Tc = self.T_cond                                                     # condenser / compressor-shell node (thermal mass)
        if cooling and not self.e04:
            Te = self.Tl - ENG['evaporator_approach_k']
            qmax = min(max(ENG['capacity_m80_w'] * (self.Tl - ENG['capacity_floor_k']) / (193.15 - ENG['capacity_floor_k']), 0.0), ENG['capacity_max_w'])
            if 'interior_sensor_failure' in self.faults:
                u = qmax                                                     # no probe: run flat out (safe side)
            else:
                e = self.Tp - self.settings['set_point_k']
                kp = ENG['controller_gain_w_k']
                self.integ = min(max(self.integ + kp / ENG['controller_integral_s'] * e * h, 0.0), qmax)
                u = kp * e + self.integ
            Q = min(max(u, 0.0), qmax)
            cop = ENG['carnot_fraction'] * Te / max(Tc - Te, 5.0)            # COP at the present condenser temperature; supply limit
            Q = min(Q, max(cop * (P_MAX_EL - ENG['base_power_w']), 0.0))
            P = ENG['base_power_w'] + Q / cop
        else:
            self.integ = 0.0
        Cc = ENG['condenser_heat_capacity_j_k']
        Tc_new = (Cc * Tc + h * (Q + P + uac * Tamb)) / (Cc + h * uac)      # implicit condenser node: heat in (Q + P), air-cooled to the room
        q_room = uac * (Tc_new - Tamb)
        L['e_condenser_stored_j'] = L.get('e_condenser_stored_j', 0.0) + Cc * (Tc_new - Tc)
        self.T_cond, self.Q, self.P = Tc_new, Q, P
        if Tc_new > ENG['condenser_e04_k'] and not self.e04 and mains:
            self.e04, self.e04_since = True, self.t + h
            self._event('alarm', 'E-04', '冷凝器温度过高（清洁滤网、检查环境温度）：压缩机保护停机')
        elif self.e04 and (not mains or (Tc_new < ENG['condenser_e04_k'] - 10.0 and self.t + h - self.e04_since >= ENG['e04_min_off_s'] - 1e-9)):
            self.e04 = False
        dQ_liner -= Q * h
        L['e_refrigeration_out_j'] += Q * h
        L['electric_energy_j'] += P * h
        L['room_heat_j'] += q_room * h
        # ---- walls
        q_lf = ua2 * (self.Tf - self.Tl)
        q_fa = ua2 * (Tamb - self.Tf)
        dQ_liner += h * q_lf
        self.Tf += h * (q_fa - q_lf) / Cf
        L['e_conduction_in_j'] += h * q_fa
        self.Tl += dQ_liner / Cl
        # ---- melting (liner above 0 C: frost on the liner, gasket and vent melts, heat from the liner)
        if self.Tl > T0:
            tot = self.frost['liner'] + self.frost['gasket'] + self.frost['vent']
            if tot > 0.0:
                m = min(tot, Cl * (self.Tl - T0) / L_FUS)
                f = m / tot
                for k in self.frost:
                    self.frost[k] -= self.frost[k] * f
                self.melt += m
                self.Tl -= m * L_FUS / Cl
        elif self.Tl < T0 and self.melt > 0.0:                             # melt water left in the cabinet refreezes on the liner
            m = min(self.melt, Cl * (T0 - self.Tl) / L_FUS)
            self.melt -= m
            self.frost['liner'] += m
            self.Tl += m * L_FUS / Cl
        # ---- probe
        self.Tp += (self.Ta[PROBE_COMP] - self.Tp) * (1.0 - math.exp(-h / ENG['probe_tau_s']))
        if mains and not self.reached and self.Tp <= self.settings['set_point_k'] + 1.0:
            self.reached = True
            self._event('operation', 'SET_POINT_REACHED', '腔温达到设定温度')
        # ---- interior pressure (vent valve; equalised while the outer door is open)
        V = ENG['interior_gas_volume_m3']
        n_eq = pa * V / (R_GAS * self._T_mean())
        if self.door > 0.02:
            L['n_door_mol'] += n_eq - self.n_gas
            self.n_gas = n_eq
        else:
            iced = self.frost['vent'] >= ENG['vent_iced_kg']
            tau = ENG['vent_tau_iced_s'] if iced else (ENG['vent_tau_pressed_s'] if self.t < self.vent_until else ENG['vent_tau_auto_s'])
            g_leak = (5.0 if 'door_gasket_damage' in self.faults else 1.0) / ENG['gasket_leak_tau_s']   # gasket / access-port leak path in parallel
            dn = (n_eq - self.n_gas) * (1.0 - math.exp(-h * (1.0 / tau + g_leak)))
            self.n_gas += dn
            L['n_vent_mol'] += dn
        # ---- items: degradation index and thaw events
        Ea = ENG['degradation_activation_j_mol']
        for it in self.items.values():
            T, phi = item_T(it, it['H'])
            it['eq_s'] += h * math.exp(-Ea / R_GAS * (1.0 / T - 1.0 / 193.15))
            it['T_max'] = max(it['T_max'], T)
            if phi > 0.0 and not it['thawed_now'] and it['water_kg'] > 0:
                it['thaws'] += 1
                self._event('truth', 'THAW', f'物品{it["item_id"]}开始解冻')
            it['thawed_now'] = phi > 0.0
        # ---- filter dust, battery
        if mains:
            self.dust += h / (ENG['dust_days_to_clean'] * DAY)
        if self.battery_switch:
            if mains:
                self.battery = min(self.battery + h / DOC['battery_charge_s'], 1.0)
            elif self.battery > 0.0:
                life = DOC['battery_backup_s'] * (0.1 if 'battery_aged' in self.faults else 1.0)
                self.battery = max(self.battery - h / life, 0.0)
        self._update_alarms(h)
        # ---- monitoring log
        if self.plant.get('monitoring') and self.t + h >= self.next_log - 1e-9:
            s = self._sensor_k()
            self.log.append(dict(time_s=round(self.t + h, 3), temperature_c=None if s is None else round(s - T0), outer_door_open=self.door > 0.02, alarms=list(self.alarms)))
            del self.log[:-MAX_LOG]
            self.next_log = self.t + h + LOG_PERIOD

    def _update_alarms(self, h=0.0, timed=True):
        """Grid ticks (timed=True) start and count the alarm delays at grid times; accepted commands (timed=False) only re-evaluate the untimed
        alarms and drop a timed alarm whose condition is gone, without starting or advancing any delay."""
        A = []
        relay = False
        if self.plant is not None and self._panel_on():
            s = self._sensor_k()
            for code, cond in (('TEMP_HIGH', s is not None and self.Tp > self.settings['high_alarm_k']),
                               ('TEMP_LOW', s is not None and self.Tp < self.settings['low_alarm_k'])):
                if not timed:
                    if cond and code in self.alarms:
                        A.append(code)
                    if cond and code in self.relay_codes:                      # remote delay already elapsed at a grid tick
                        relay = True
                    if not cond:
                        self.alarm_since.pop(code, None)
                        self.relay_codes.discard(code)
                    continue
                if cond:
                    since = self.alarm_since.setdefault(code, self.t + h)
                    d_on = self.settings['onsite_delay_s'] or DOC['alarm_delay_zero_s']
                    d_re = self.settings['remote_delay_s'] or DOC['alarm_delay_zero_s']
                    if self.t + h - since >= d_on - 1e-9:
                        A.append(code)
                    if self.t + h - since >= d_re - 1e-9:
                        relay = True
                        self.relay_codes.add(code)
                else:
                    self.alarm_since.pop(code, None)
                    self.relay_codes.discard(code)
            if self._on_battery():
                A.append('POWER_FAIL')
                relay = True
            if self.battery_switch and (self.battery < ENG['battery_low_fraction'] or not self._mains_ok()):
                A.append('BATTERY_LOW')
            A += self._codes()
            if self.dust >= 1.0:
                A.append('FILTER_CLEAN')
        else:
            self.alarm_since = {}
            self.relay_codes = set()
            relay = self.plant is not None and self.mains_switch                # fail-safe contact: an outage with an exhausted battery reports alarm
        new = set(A) - set(self.alarms)
        for c in sorted(new):
            self._event('alarm', c, '报警：' + c)
        self.muted_set &= set(A)                                              # a muted alarm that goes away and comes back sounds again
        self.alarms, self.relay = A, relay

    def _audible(self):
        sounding = {a for a in self.alarms if a in ('TEMP_HIGH', 'TEMP_LOW', 'POWER_FAIL') or a.startswith('E-')}
        return bool(self._panel_on() and ((sounding - self.muted_set) or self.t < self.test_until))

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'inventory.read', 'log.read'):
            self.revision += 1
        if self.plant is not None and 'alarms.active' in EFFECTS.get(capability_id, ()):
            self._update_alarms(timed=False)
        return out

    def _cmd_plant_configure(self, ambient_k, relative_humidity, pressure_pa, noise_seed, mains_v=None, monitoring=None, remote_connected=None):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        old = self.plant or {}
        self.plant = dict(ambient_k=ambient_k, relative_humidity=relative_humidity, pressure_pa=pressure_pa, mains_v=mains_v if mains_v is not None else old.get('mains_v', 230.0),
                          monitoring=monitoring if monitoring is not None else old.get('monitoring', False),
                          remote_connected=remote_connected if remote_connected is not None else old.get('remote_connected', False))
        self.seed = noise_seed
        if first:                                                            # a freshly installed freezer at room temperature, dry, switched off
            self.Tl = self.Tf = self.Tp = self.T_cond = ambient_k
            self.Ta = [ambient_k] * N_COMP
            self.n_gas = pressure_pa * ENG['interior_gas_volume_m3'] / (R_GAS * ambient_k)
            self.led['n_initial_mol'] = self.n_gas
            self.led['e_initial_j'] = self._cabinet_U()
        self._event('system', 'PLANT_CONFIGURED', '环境条件已声明')
        return dict(configured=True)

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not self.mains_switch:
            self.mains_switch = True
            self.reached = False
            self._event('system', 'POWER_ON', '主电源开关接通：显示软件版本，压缩机延时启动')
        elif not on and self.mains_switch:
            self.mains_switch = False
            self.integ = 0.0
            self._event('system', 'POWER_OFF', '主电源开关断开')
        return dict(on=self.mains_switch)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(on=not self.mains_switch)

    def _cmd_battery_set(self, on):
        self._need_plant()
        if on != self.battery_switch:
            self.battery_switch = on
            self._event('system', 'BATTERY_ON' if on else 'BATTERY_OFF', '电池开关' + ('接通（停电报警启用）' if on else '断开（停电不报警）'))
        return dict(on=self.battery_switch)

    def _cmd_battery_toggle(self):
        return self._cmd_battery_set(on=not self.battery_switch)

    def _need_panel(self):
        self._need_plant()
        if not self._panel_on():
            raise ActionRejected('POWER_OFF')

    def _need_programming(self):
        self._need_panel()
        if self.code != '0000' and not self.programming:
            raise ActionRejected('PANEL_LOCKED')

    def _cmd_panel_unlock(self, code):
        self._need_panel()
        if self.code != '0000' and code != self.code:
            raise ActionRejected('WRONG_CODE')
        self.programming = True
        return dict(programming=True)

    def _cmd_panel_lock(self):
        self._need_panel()
        if self.programming:
            self.programming = False
            self._event('operation', 'PARAMETERS_SAVED', '退出编程模式，参数已保存')
        return dict(programming=False)

    def _cmd_code_change(self, code):
        self._need_programming()
        self.code = code
        self._event('operation', 'CODE_CHANGED', '锁定码已' + ('停用' if code == '0000' else '设置'))
        return dict(code_active=code != '0000')

    def _cmd_settings_configure(self, set_point_k=None, high_alarm_k=None, low_alarm_k=None, onsite_delay_s=None, remote_delay_s=None):
        self._need_programming()
        S = dict(self.settings)
        for k, v in dict(set_point_k=set_point_k, high_alarm_k=high_alarm_k, low_alarm_k=low_alarm_k, onsite_delay_s=onsite_delay_s, remote_delay_s=remote_delay_s).items():
            if v is not None:
                S[k] = float(v)
        hi_lo = S['set_point_k'] + DOC['high_alarm_limits']['min_above_set_k']
        lo_hi = S['set_point_k'] - DOC['low_alarm_limits']['max_below_set_k']
        if not hi_lo - 1e-9 <= S['high_alarm_k'] <= DOC['high_alarm_limits']['max_k'] + 1e-9 or not DOC['low_alarm_limits']['min_k'] - 1e-9 <= S['low_alarm_k'] <= lo_hi + 1e-9:
            raise ActionRejected('OUT_OF_LIMITS' if (high_alarm_k is not None or low_alarm_k is not None) else 'LIMITS_CONFLICT')
        self.settings = S
        if set_point_k is not None:
            self.reached = self.reached and self.Tp <= S['set_point_k'] + 1.0
        return dict(settings=dict(S))

    def _cmd_alarm_mute(self):
        self._need_panel()
        sounding = {a for a in self.alarms if a in ('TEMP_HIGH', 'TEMP_LOW', 'POWER_FAIL') or a.startswith('E-')}
        if sounding - self.muted_set:
            self.muted_set |= sounding
            return dict(muted=True, test=False)
        self.test_until = self.t + ENG['mute_test_s']                         # no alarm: test the signal tone
        return dict(muted=False, test=True)

    def _cmd_handle_set(self, locked):
        self._need_plant()
        if locked and self.door > 1e-9:
            raise ActionRejected('DOOR_OPEN')
        if not locked and self.cyl:
            raise ActionRejected('CYLINDER_LOCKED')
        self.handle = 0.0 if locked else HANDLE_DOWN
        return dict(locked=locked)

    def _cmd_lock_set(self, locked):
        self._need_plant()
        if locked and self.handle > 1e-9:
            raise ActionRejected('HANDLE_NOT_LOCKED')
        self.cyl = locked
        return dict(locked=locked)

    def _cmd_door_move(self, angle_rad):
        self._need_plant()
        if angle_rad > 1e-9 and self.door <= 1e-9:
            if self.handle < HANDLE_DOWN - 1e-9:
                raise ActionRejected('HANDLE_LOCKED')
            if self._dp() > ENG['door_open_dp_pa']:
                raise ActionRejected('NEGATIVE_PRESSURE')                     # vacuum after the last closing: wait / auto vent
        if angle_rad <= 1e-9 and self.door > 1e-9:
            if any(a > 1e-9 for a in self.inner):
                raise ActionRejected('INNER_DOOR_OPEN')
            if self.handle < HANDLE_DOWN - 1e-9:
                raise ActionRejected('HANDLE_LOCKED')                         # closing with the handle up damages it (manual)
        if angle_rad < OUTER_FOR_INNER - 1e-9 and any(a > 1e-9 for a in self.inner):
            raise ActionRejected('INNER_DOOR_OPEN')
        was_open = self.door > 1e-9
        self.door = float(angle_rad)
        if was_open != (self.door > 1e-9):
            self._event('door', 'OUTER_OPEN' if self.door > 1e-9 else 'OUTER_CLOSED', '外门' + ('打开' if self.door > 1e-9 else '关闭（开始压力平衡）'))
        return dict(angle_rad=self.door)

    def _cmd_inner_door_move(self, compartment, angle_rad):
        self._need_plant()
        if self.door < OUTER_FOR_INNER - 1e-9:
            raise ActionRejected('OUTER_DOOR_CLOSED')
        i = compartment - 1
        was_open = self.inner[i] > 1e-9
        self.inner[i] = float(angle_rad)
        if was_open != (self.inner[i] > 1e-9):
            self._event('door', f'INNER{compartment}_' + ('OPEN' if self.inner[i] > 1e-9 else 'CLOSED'), f'第{compartment}内门' + ('打开' if self.inner[i] > 1e-9 else '关闭'))
        return dict(compartment=compartment, angle_rad=self.inner[i])

    def _cmd_vent_press(self):
        self._need_plant()
        self.vent_until = self.t + ENG['vent_press_window_s']
        return dict(pressed=True)

    def _cmd_vent_deice(self):
        self._need_plant()
        if self.door <= 1e-9:
            raise ActionRejected('OUTER_DOOR_CLOSED')
        m = self.frost['vent']
        self.frost['vent'] = 0.0
        self.led['w_deiced_kg'] += m
        self.led['e_meltwater_wiped_j'] -= L_SUB * m                          # ice taken out: its (negative) internal energy leaves the cabinet
        self._event('maintenance', 'VENT_DEICED', f'除去泄压阀上的冰{m * 1e3:.1f} g')
        return dict(removed_kg=m)

    def _cmd_seals_clean(self):
        self._need_plant()
        if self.door <= 1e-9:
            raise ActionRejected('OUTER_DOOR_CLOSED')
        m = self.frost['gasket']
        self.frost['gasket'] = 0.0
        self.led['w_deiced_kg'] += m
        self.led['e_meltwater_wiped_j'] -= L_SUB * m
        self._event('maintenance', 'SEALS_CLEANED', f'清洁门封，除霜{m * 1e3:.1f} g')
        return dict(removed_kg=m)

    def _cmd_filter_clean(self):
        self._need_plant()
        self.dust = 0.0
        self._event('maintenance', 'FILTER_CLEANED', '清洁空气滤网与进风格栅')
        return dict(cleaned=True)

    def _cmd_load_place(self, item_id, compartment, kind, initial_k, label='', water_kg=0.0, polymer_kg=0.0, cardboard_kg=0.0, steel_kg=0.0, aluminium_kg=0.0):
        self._need_plant()
        if item_id in self.items:
            raise ActionRejected('DUPLICATE_ITEM')
        self._need_access(compartment)
        mass = water_kg + polymer_kg + cardboard_kg + steel_kg + aluminium_kg
        if mass <= 0.0:
            raise ActionRejected('INVALID_ARGUMENT')
        if self._comp_load(compartment) + mass > DOC['shelf_load_kg'] + 1e-9:
            raise ActionRejected('SHELF_OVERLOAD')
        if kind == 'rack' and sum(1 for it in self.items.values() if it['compartment'] == compartment and it['kind'] == 'rack') >= DOC['max_racks'] // N_COMP:
            raise ActionRejected('COMPARTMENT_FULL')
        it = dict(item_id=item_id, compartment=compartment, kind=kind, label=label, water_kg=water_kg, polymer_kg=polymer_kg, cardboard_kg=cardboard_kg,
                  steel_kg=steel_kg, aluminium_kg=aluminium_kg, mass_kg=mass, placed_s=self.t, eq_s=0.0, thaws=0, thawed_now=initial_k >= T0 and water_kg > 0)
        area = 6.0 * (mass / ENG['item_bulk_density_kg_m3']) ** (2.0 / 3.0)
        it['G_air0'] = ENG['item_h_w_m2_k'] * area
        it['G_c0'] = ENG['item_contact_w_k_per_kg'] * mass
        it['H'] = item_H(it, initial_k)
        it['T'], it['phi'] = item_T(it, it['H'])
        it['T_max'] = initial_k
        self.items[item_id] = it
        self.led['e_items_added_j'] += it['H']
        self._event('operation', 'LOAD', f'放入{kind}「{label or item_id}」到第{compartment}室（{mass:.2f} kg）')
        return dict(item_id=item_id, compartment=compartment, compartment_load_kg=self._comp_load(compartment))

    def _need_access(self, compartment):
        if self.door < OUTER_FOR_INNER - 1e-9:
            raise ActionRejected('OUTER_DOOR_CLOSED')
        if self.inner[compartment - 1] < INNER_FOR_LOADING - 1e-9:
            raise ActionRejected('INNER_DOOR_CLOSED')

    def _cmd_load_remove(self, item_id):
        self._need_plant()
        if item_id not in self.items:
            raise ActionRejected('UNKNOWN_ITEM')
        it = self.items[item_id]
        self._need_access(it['compartment'])
        self.led['e_items_removed_j'] += it['H']
        del self.items[item_id]
        self._event('operation', 'UNLOAD', f'取出「{it["label"] or item_id}」（第{it["compartment"]}室）')
        return dict(item_id=item_id, removed=True)

    def _cmd_inventory_read(self):
        return dict(items=[dict(item_id=it['item_id'], compartment=it['compartment'], kind=it['kind'], label=it['label'], mass_kg=it['mass_kg'], placed_s=it['placed_s'])
                           for it in sorted(self.items.values(), key=lambda x: (x['compartment'], x['item_id']))])

    def _cmd_interior_dry(self):
        self._need_plant()
        if self.mains_switch:
            raise ActionRejected('POWER_ON')
        if self.door <= 1e-9:
            raise ActionRejected('OUTER_DOOR_CLOSED')
        if self.frost['liner'] + self.frost['gasket'] + self.frost['vent'] > 1e-9:
            raise ActionRejected('FROST_REMAINING')
        m = self.melt
        self.melt = 0.0
        self.led['w_wiped_kg'] += m
        self.led['e_meltwater_wiped_j'] -= L_VAP * m
        self._event('maintenance', 'INTERIOR_DRIED', f'擦干融水{m * 1e3:.0f} g并擦干门封')
        return dict(wiped_kg=m)

    def _cmd_log_read(self, last):
        self._need_plant()
        if not self.plant.get('monitoring'):
            raise ActionRejected('NO_MONITORING')
        return dict(records=[dict(r) for r in self.log[-last:]])

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s - 1e-9], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        v = self.read('evaluator')
        return dict(thermal=v['truth.thermal'], items=v['truth.items'], power=v['truth.power'], faults=v['truth.faults'])

    def _ledger(self):
        L = copy.deepcopy(self.led)
        if self.plant is not None:
            L['u_cabinet_j'] = self._cabinet_U()
            L['frost_kg'] = self.frost['liner'] + self.frost['gasket'] + self.frost['vent']
            L['melt_water_kg'] = self.melt
            L['n_interior_mol'] = self.n_gas
        L['sign_convention_zh'] = ('e_*_in_j为进入箱体的能量（正=流入），e_*_out_j与e_items_removed_j、e_meltwater_wiped_j为离开箱体的能量（按离开的物质在箱体参考态下的焓记，'
                                   '融水以液态水焓计，通常为负）；u_cabinet_j以0 °C干燥箱体与水蒸气为参考；e_condenser_stored_j为冷凝器节点相对室温的储热（不属于箱体）')
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
        pc = self.plant is not None
        pon = pc and self._panel_on()
        s = self._sensor_k() if pc else None
        flashing = bool(pon and self._on_battery())
        disp = None
        if s is not None and not (flashing and int(self.t // DOC['outage_display_flash_s']) % 2 == 1):
            disp = float(round(s - T0))
        codes = self._codes() if pc else []
        al = list(self.alarms) if pon else []
        test = bool(pon and self.t < self.test_until)                         # alarm test: all indicator lights are lit
        out = {'clock.time_s': self.t, 'plant.configured': pc, 'power.on': self.mains_switch, 'battery.on': self.battery_switch, 'state.current': self._state(),
               'temperature.interior_k': s, 'display.temperature': disp, 'display.code': codes[0] if codes else None, 'display.flashing': flashing,
               'settings.current': dict(self.settings), 'panel.programming': self.programming, 'panel.code_active': self.code != '0000',
               'alarms.active': al, 'alarm.audible': self._audible() if pc else False, 'alarm.remote_relay': bool(pc and self.relay),
               'indicator.temp_alarm': test or bool(pon and ({'TEMP_HIGH', 'TEMP_LOW'} & set(al))), 'indicator.power_fail': test or bool(pon and 'POWER_FAIL' in al),
               'indicator.battery_low': test or bool(pon and 'BATTERY_LOW' in al), 'indicator.fault': test or bool(pon and codes), 'indicator.filter_clean': test or bool(pon and 'FILTER_CLEAN' in al),
               'indicator.remote': test or bool(pon and self.plant.get('remote_connected')), 'indicator.lock': test or bool(pon and self.programming and self.code != '0000'),
               'door.outer_angle_rad': self.door, 'door.handle_angle_rad': self.handle, 'door.cylinder_locked': self.cyl, 'inventory.count': len(self.items)}
        for i in range(N_COMP):
            out[f'door.inner{i + 1}_angle_rad'] = self.inner[i]
        if visibility != 'agent':
            if not pc:
                out['truth.thermal'], out['truth.items'], out['truth.power'] = {}, {}, {}
            else:
                out['truth.thermal'] = dict(air_k=list(self.Ta), liner_k=self.Tl, foam_k=self.Tf, probe_k=self.Tp, refrigeration_w=self.Q, electric_power_w=self.P,
                                            cop=(self.Q / (self.P - ENG['base_power_w'])) if self.P > ENG['base_power_w'] + 1e-9 else None, condenser_k=self.T_cond,
                                            frost_kg=dict(self.frost), melt_water_kg=self.melt, pressure_deficit_pa=self._dp(), interior_pressure_pa=self._p_int(),
                                            controller_integral_w=self.integ, e04_protection=self.e04, set_point_reached=self.reached)
                out['truth.items'] = {k: dict(compartment=it['compartment'], kind=it['kind'], temperature_k=item_T(it, it['H'])[0], liquid_fraction=item_T(it, it['H'])[1],
                                              max_temperature_k=it['T_max'], equivalent_storage_s_at_m80=it['eq_s'], thaw_events=it['thaws'], mass_kg=it['mass_kg'])
                                      for k, it in self.items.items()}
                out['truth.power'] = dict(mains_ok=self._mains_ok(), on_battery=self._on_battery(), battery_charge=self.battery, filter_dust=self.dust,
                                          compressor_running=self.Q > 0.0, mains_current_a=self.P / self.plant['mains_v'])
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
class EppendorfCryocubef570nSession:
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
        meta = copy.deepcopy(export_eppendorf_cryocubef570n_contract_metadata())
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
    'PLANT_REQUIRED': '尚未声明环境条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '控制面板无电（主开关断且无电池供电）',
    'POWER_ON': '须先断开主电源开关（除霜与擦干）', 'PANEL_LOCKED': '面板被锁定码保护：先用lock软键输入锁定码进入编程模式', 'WRONG_CODE': '锁定码错误',
    'OUT_OF_LIMITS': '报警限超出允许范围（低温限−91 °C至设定−5 °C；高温限设定+5 °C至−10 °C）', 'LIMITS_CONFLICT': '新的设定温度使现有报警限越界：同时给出新的报警限',
    'DOOR_OPEN': '外门开着：先关门再把把手压到锁定位置', 'CYLINDER_LOCKED': '把手锁芯锁着：先用钥匙开锁', 'HANDLE_NOT_LOCKED': '把手不在锁定位置：锁芯只能在锁定位置上锁',
    'HANDLE_LOCKED': '把手在锁定（向上）位置：先向前向下拉到底（开门与关门都须如此，否则损坏把手）', 'NEGATIVE_PRESSURE': '内腔负压使外门拉不开：等待压力平衡或按auto vent键',
    'INNER_DOOR_OPEN': '内门开着：先关内门再关外门（或外门开度不足以容纳打开的内门）', 'OUTER_DOOR_CLOSED': '外门未充分打开（须约80°以上）',
    'INNER_DOOR_CLOSED': '该内室的内门未打开', 'DUPLICATE_ITEM': '物品编号已存在', 'UNKNOWN_ITEM': '没有该物品', 'SHELF_OVERLOAD': '超过隔板承重65 kg',
    'COMPARTMENT_FULL': '该内室已放满5个冻存架', 'FROST_REMAINING': '霜还没化完（断电开门等待融化）', 'NO_MONITORING': '未接监控（VisioNize）：没有记录',
}
_P = ['PLANT_REQUIRED']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': _P, 'power.toggle': _P, 'battery.set': _P, 'battery.toggle': _P,
    'panel.unlock': _P + ['POWER_OFF', 'WRONG_CODE'], 'panel.lock': _P + ['POWER_OFF'], 'code.change': _P + ['POWER_OFF', 'PANEL_LOCKED'],
    'settings.configure': _P + ['POWER_OFF', 'PANEL_LOCKED', 'OUT_OF_LIMITS', 'LIMITS_CONFLICT'], 'alarm.mute': _P + ['POWER_OFF'],
    'handle.set': _P + ['DOOR_OPEN', 'CYLINDER_LOCKED'], 'lock.set': _P + ['HANDLE_NOT_LOCKED'],
    'door.move': _P + ['HANDLE_LOCKED', 'NEGATIVE_PRESSURE', 'INNER_DOOR_OPEN'], 'inner_door.move': _P + ['OUTER_DOOR_CLOSED'],
    'vent.press': _P, 'vent.deice': _P + ['OUTER_DOOR_CLOSED'], 'seals.clean': _P + ['OUTER_DOOR_CLOSED'], 'filter.clean': _P,
    'load.place': _P + ['DUPLICATE_ITEM', 'OUTER_DOOR_CLOSED', 'INNER_DOOR_CLOSED', 'SHELF_OVERLOAD', 'COMPARTMENT_FULL'],
    'load.remove': _P + ['UNKNOWN_ITEM', 'OUTER_DOOR_CLOSED', 'INNER_DOOR_CLOSED'], 'inventory.read': [],
    'interior.dry': _P + ['POWER_ON', 'OUTER_DOOR_CLOSED', 'FROST_REMAINING'], 'log.read': _P + ['NO_MONITORING'],
    'events.read': [], 'truth.inspect': [], 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子（之后须给同一种子）；首次声明时冷柜在室温、干燥、断电', ['plant.configured']),
    'power.set': ('主开关接通后压缩机延时启动降温', ['power.on']), 'power.toggle': ('同power.set', ['power.on']),
    'battery.set': ('电池开关接通后停电时面板与报警由电池供电', ['battery.on']), 'battery.toggle': ('同battery.set', ['battery.on']),
    'panel.unlock': ('面板有电；锁定码启用时须输入正确的四位码', ['panel.code_active']), 'panel.lock': ('面板有电', ['panel.programming']),
    'code.change': ('面板有电；锁定码启用时须在编程模式', ['panel.programming', 'panel.code_active']),
    'settings.configure': ('面板有电；锁定码启用时须在编程模式；设定−50…−86 °C，低温限−91 °C至设定−5 °C，高温限设定+5 °C至−10 °C，延时0–40 min（0为15 s）',
                           ['panel.programming', 'settings.current']),
    'alarm.mute': ('面板有电；有鸣响的报警时静音，否则测试报警音', ['alarms.active']),
    'handle.set': ('锁定（向上压）须外门已关；解锁须锁芯未锁', ['door.outer_angle_rad', 'door.cylinder_locked']),
    'lock.set': ('上锁须把手在锁定位置', ['door.handle_angle_rad']),
    'door.move': ('开门须把手已解锁且内腔负压已平衡；关门须内门全关、把手在下方；外门开度须容纳打开的内门', ['door.handle_angle_rad', 'door.outer_angle_rad']),
    'inner_door.move': ('外门须开到约80°以上', ['door.outer_angle_rad']), 'vent.press': ('无', ['plant.configured']),
    'vent.deice': ('外门打开', ['door.outer_angle_rad']), 'seals.clean': ('外门打开', ['door.outer_angle_rad']), 'filter.clean': ('无', ['plant.configured']),
    'load.place': ('外门与该内室内门打开；物品编号唯一；每室≤65 kg、≤5个冻存架', ['door.outer_angle_rad', 'inventory.count']),
    'load.remove': ('物品存在；外门与其内门打开', ['door.outer_angle_rad', 'inventory.count']),
    'interior.dry': ('主开关已断、外门打开、霜已化完', ['power.on', 'door.outer_angle_rad']), 'log.read': ('接了监控（plant.configure monitoring）', ['plant.configured']),
    'fault.inject': ('无', ['plant.configured']),
}
_LAMPS_ALARMS = ['alarms.active', 'alarm.audible', 'alarm.remote_relay'] + _INDS
_EVOLVE = (['state.current', 'temperature.interior_k', 'display.temperature', 'display.code', 'display.flashing', 'truth.thermal', 'truth.items', 'truth.power']
           + _LAMPS_ALARMS)
EFFECTS = {
    'plant.configure': ['plant.configured', 'state.current', 'truth.thermal', 'truth.power', 'indicator.remote', 'temperature.interior_k', 'display.temperature',
                        'display.code', 'display.flashing'] + _LAMPS_ALARMS,
    'power.set': ['power.on', 'state.current', 'truth.thermal', 'truth.power', 'temperature.interior_k', 'display.temperature', 'display.code', 'display.flashing'] + _LAMPS_ALARMS,
    'battery.set': ['battery.on', 'truth.power', 'state.current', 'temperature.interior_k', 'display.temperature', 'display.code', 'display.flashing'] + _LAMPS_ALARMS,
    'panel.unlock': ['panel.programming', 'indicator.lock'], 'panel.lock': ['panel.programming', 'indicator.lock'],
    'code.change': ['panel.code_active', 'indicator.lock'],
    'settings.configure': ['settings.current', 'state.current', 'truth.thermal'] + _LAMPS_ALARMS,
    'alarm.mute': ['alarm.audible'] + _INDS, 'handle.set': ['door.handle_angle_rad'], 'lock.set': ['door.cylinder_locked'],
    'door.move': ['door.outer_angle_rad'], 'inner_door.move': [f'door.inner{i}_angle_rad' for i in range(1, N_COMP + 1)],
    'vent.press': [], 'vent.deice': ['truth.thermal'], 'seals.clean': ['truth.thermal'], 'filter.clean': ['truth.power', 'indicator.filter_clean', 'alarms.active'],
    'load.place': ['inventory.count', 'truth.items'], 'load.remove': ['inventory.count', 'truth.items'], 'interior.dry': ['truth.thermal'],
    'fault.inject': ['truth.faults', 'truth.power', 'state.current', 'temperature.interior_k', 'display.temperature', 'display.code', 'display.flashing', 'truth.thermal']
                    + _LAMPS_ALARMS,
    'sim.step': ['clock.time_s'],
}
EFFECTS['power.toggle'] = list(EFFECTS['power.set'])
EFFECTS['battery.toggle'] = list(EFFECTS['battery.set'])
_STEPPED = ('plant.configure', 'power.set', 'power.toggle', 'battery.set', 'battery.toggle', 'settings.configure', 'alarm.mute', 'door.move', 'inner_door.move', 'vent.press',
            'vent.deice', 'seals.clean', 'filter.clean', 'load.place', 'load.remove', 'interior.dry', 'fault.inject')
ON_STEP = {k: list(_EVOLVE) for k in _STEPPED}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {
    'sim.step': '按1 s网格积分：内室空气、内胆、泡沫与物品传热，开门空气交换与结霜，制冷量（比例积分控制、冷凝温度、供电上限）与电功率，压力平衡，融霜，电池与滤网，报警与灯光',
    'power.set': '压缩机降温或断电后腔温回升', 'door.move': '开门空气交换使腔温升高、结霜；关门后负压与压力平衡', 'inner_door.move': '打开的内室与室内空气交换',
    'vent.press': '压力平衡加快（1–2 min）', 'settings.configure': '控制按新设定调节、报警按新限值判定', 'load.place': '物品与内室空气和隔板换热（热物品使腔温升高）',
    'load.remove': '内室热负荷变化', 'filter.clean': '冷凝器散热恢复', 'fault.inject': '故障作用于传感器、压缩机、冷凝风机、市电、电池或门封',
    'battery.set': '停电时由电池供电报警', 'alarm.mute': '新的报警出现时重新鸣响', 'vent.deice': '泄压阀恢复正常平衡速度', 'seals.clean': '门封渗入减少',
    'interior.dry': '可重新开机', 'plant.configure': '室温与湿度影响漏热、结霜与冷凝温度',
}
EXEC_TEXT = {'power.set': ('state.current经pull_down（约3 h 53 min，空柜从20 °C）变为running', '断电后腔温逐步回升'),
             'door.move': ('door.outer_angle_rad为给定角度', '负压未平衡时拒绝开门')}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('load.place', 'load.remove', 'door.move', 'inner_door.move', 'vent.deice', 'seals.clean', 'filter.clean', 'interior.dry', 'lock.set'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'power.set': ['state.current', 'temperature.interior_k'], 'door.move': ['door.outer_angle_rad', 'temperature.interior_k'],
            'load.place': ['temperature.interior_k'], 'settings.configure': ['temperature.interior_k']}
_WB, _BR, _MN, _PG, _SIM = 'user_workbook', 'manufacturer_brochure_2026', 'manufacturer_operating_manual', 'manufacturer_product_page_de', 'simulation_assumption'
CAP_SOURCES = {k: [_MN] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_MN, _SIM], 'load.place': [_BR, _MN, _WB], 'load.remove': [_BR, _MN], 'inventory.read': [_WB, _SIM],
                    'log.read': [_BR, _WB, _SIM], 'events.read': [_SIM], 'truth.inspect': [_SIM], 'ledger.read': [_SIM], 'fault.inject': [_MN, _SIM], 'sim.step': [_SIM],
                    'settings.configure': [_MN, _BR, _WB], 'door.move': [_MN, _BR], 'vent.press': [_MN, _BR, _PG]})
CAP_SOURCES.update({k: [_BR, _MN] for k in UNSUPPORTED})
PARAM_ZH = {'ambient_k': '室温', 'relative_humidity': '室内相对湿度（0–1）', 'pressure_pa': '大气压', 'mains_v': '电源电压', 'monitoring': '是否接监控（VisioNize）',
            'remote_connected': '是否由计算机远程控制（remote control灯）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）', 'on': '是否接通',
            'code': '四位锁定码（0000为停用）', 'set_point_k': '设定腔温', 'high_alarm_k': '高温报警限', 'low_alarm_k': '低温报警限', 'onsite_delay_s': '现场报警延时',
            'remote_delay_s': '远程报警延时', 'locked': '锁定或解锁', 'angle_rad': '开启角', 'compartment': '内室（1为最上层）', 'item_id': '物品编号',
            'kind': '物品类型（冻存架、冻存盒、样品、其他）', 'label': '外部样品管理系统中的位置/标签', 'water_kg': '含水（水溶液样品）质量', 'polymer_kg': '塑料（管、盒）质量',
            'cardboard_kg': '纸盒质量', 'steel_kg': '不锈钢（冻存架）质量', 'aluminium_kg': '铝（冻存架）质量', 'initial_k': '放入时的温度', 'last': '最近记录条数',
            'fault': '故障名', 'active': '注入或清除', 'since_s': '起始时间', 'dt_s': '推进时间'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.ult_freezer_thermal.limitations。'
CAP_LIMITS = {
    'door.move': ['开门空气交换按门洞浮力流（流量系数按开门60 s后29 min恢复标定，标定假设外门与5扇内门全开60 s；只开外门约6 min、开一扇内门约13 min恢复）；'
                  '每个内室独立与室内交换，内室之间的上下烟囱效应与冷空气从上层经下层溢出不建模；外门开度按线性比例；内门缝隙按比例；'
                  '负压按泄压阀（自动约20 s、按键约10 s、冰堵约2600 s时间常数）与门封漏气（1800 s）并联平衡，负压门力阈值为工程值。'],
    'load.place': ['物品为集总热节点（水/冰相变按纯水，冻存保护剂与过冷不建模；内部导热阻力不计，大块物品冻结快于实际，例如20 kg温水约4.7 h冻透）；对流与隔板接触换热为工程值；库存位置由宿主声明，本机不识别样品。'],
    'settings.configure': ['设定与报警限按手册范围；软键操作序列（PP、---、EE显示）抽象为一次设定调用。'],
    'power.set': ['复叠压缩机抽象为一台比例积分调节制冷量的热泵（真实机组的启停/调速策略未公开）；压缩机延时启动不单独建模。'],
    'log.read': ['监控记录是VisioNize等外部监控的抽象（每60 s一条）；本机自身的温度记录与圆图记录仪（选件）不建模。'],
}
LIMITATIONS = [
    '所选配置：CryoCube F570n，230 V 50 Hz，左侧把手，5个内室（订货号F571300031，2026 brochure版本）；风冷，聚氨酯130 mm墙，无真空隔热板；CO₂/LN₂备份、圆图记录仪与VisioNize硬件未选。',
    '热模型：五个内室空气节点、一个内胆节点（含蒸发盘管、隔板与内门）与一个泡沫节点；墙体传热系数、热容、换热系数为工程值，内胆热容、−80 °C制冷量、卡诺比例与开门流量系数按样本降温3 h 53 min、8.3 kWh/天与开门60 s后29 min恢复标定（calibrate_ult.py）。',
    '制冷：两级复叠抽象为一台热泵，COP=η·T_e/(T_c−T_e)，制冷量上限随内胆温度线性降到复叠极限温度，电功率限于7.5 A×230 V；压缩机实际启停与回差、除霜加热不建模。',
    '湿气：进入空气中超过内室饱和量的水汽全部结霜；霜的分布（内胆、门封、泄压阀）与霜层热阻为工程值；融霜只在内胆高于0 °C时发生。',
    '样品：降解指数为−80 °C等效储存时间（阿伦尼乌斯，表观活化能80 kJ/mol，工程值，不代表具体样品的稳定性）；解冻按纯水0 °C；库存位置由外部样品管理系统维护。',
    '箱体、门与面板有几何；空气、霜、样品管与冻存盒内容在场景中没有几何（冻存架与物品为过程数据）；场景中外门、把手、5扇内门、显示数字与7个指示灯按运行层状态变化。',
]


def _sampling_text(k):
    if k == 'clock.time_s':
        return 'Host clock.'
    if SLOTS[k]['origin'] == 'sensor':
        return 'Integrated on the fixed 1 s grid (anchored at t = 0); between grid points the last value is held; sensor noise is drawn per grid tick.'
    if SLOTS[k]['origin'] in ('derived', 'process_truth'):
        return 'Integrated on the fixed 1 s grid (anchored at t = 0); between grid points the last value is held.'
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
    src_of = {'manufacturer_brochure_2026': _BR, 'manufacturer_operating_manual': _MN, 'manufacturer_product_page_de': _PG}
    WB_KEYS = ('set_temperature_range_k', 'capacity_m3', 'compartments', 'inner_doors', 'max_racks', 'energy_kwh_day', 'energy_conditions', 'wall_thickness_m',
               'external_dimensions_m', 'internal_dimensions_m', 'mass_kg', 'handle_extra_width_m', 'door_open_extra_width_m', 'page_height_m')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
    cal = {k for k, v in instrument()['engineering'].items() if v.get('calibrated_by')}
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明），不是实测' +
            ('；由calibrate_ult.py按样本降温、耗电或恢复时间标定' if k in cal else ''))
    for name, val, unit, meaning in (('latent_heat_fusion_j_kg', L_FUS, 'J/kg', '冰的熔化热'), ('latent_heat_sublimation_j_kg', L_SUB, 'J/kg', '水汽凝华（结霜）热'),
                                     ('cp_water_j_kg_k', CP_WATER, 'J/(kg K)', '液态水比热'), ('cp_ice_fit', [CP_ICE_A, CP_ICE_B], 'J/(kg K)', '冰比热线性拟合cp=A+B·T'),
                                     ('cp_materials_j_kg_k', dict(CP_MAT), 'J/(kg K)', '聚丙烯、纸板、不锈钢、铝的比热（室温文献值）'),
                                     ('air_gas_constant_j_kg_k', R_AIR, 'J/(kg K)', '干空气气体常数')):
        asm(name, val, unit, meaning, '物理常数或文献值', (_SIM,))
    return P


SLOT_REFERENCE = {'temperature.interior_k': '中间（第3）内室的探头温度', 'display.temperature': '°C取整的腔温数字',
                  'door.handle_angle_rad': '0向上锁定，1.0向下解锁', 'door.outer_angle_rad': '0关闭，正值为开启角'}


def export_eppendorf_cryocubef570n_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'EppendorfCryocubef570nSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.eppendorf_cryocubef570n.EppendorfCryocubef570nSession.invoke',
                                              invocation=f'session.invoke(instance_id="eppendorf_cryocubef570n_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='EppendorfCryocubef570nRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（温度、霜与报警在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['cabinet', 'outer_door', 'shelves', 'control_panel', 'compressors', 'air_filter', 'interior_liner'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='超低温保存：两级复叠制冷把热量从内胆（蒸发盘管围绕内腔）搬到风冷冷凝器；130 mm聚氨酯墙漏热；开门时冷空气外溢、室内湿空气进入并在内胆结霜；'
                   '关门后空气冷却形成负压，经泄压阀平衡；探头在中间内室，控制器按设定调节；超限、停电、电池、系统错误与滤网报警',
        equations=['C_a dT_a,i/dt = G_la (T_l - T_a,i) + sum_j G_j (T_j - T_a,i) + rho cp V_dot_i (T_room - T_a,i)  (implicit)',
                   'V_dot_i = Cd W H_i / 3 sqrt(g H_i |rho_i - rho_room| / rho_mean) x f_outer x max(f_inner, eps) + V_dot_gasket / 5',
                   'C_l dT_l/dt = sum G (T - T_l) + 2UA (T_f - T_l) + L_sub m_dot_frost - Q_ref;  C_f dT_f/dt = 2UA (T_room - T_f) - 2UA (T_f - T_l)',
                   'Q_ref = clip(Kp e + I, 0, min(Q_max(T_l), COP (P_max - P_base)));  COP = eta T_e / (T_c - T_e) at the present condenser temperature',
                   'C_c dT_c/dt = Q + P - UA_c (T_c - T_room)  (implicit condenser / compressor-shell node);  E-04 when T_c > T_E04: compressor off for at least t_off, '
                   'released below T_E04 - 10 K',
                   'm_dot_frost = rho V_dot (w_room - w_sat(T_a));  dn/dt = (n_eq - n) (1/tau_vent + 1/tau_gasket)  (vent valve: auto / pressed / iced, gasket leak in parallel);  '
                   'door locked while p_room - p_int > dp_max',
                   'melt water: melts when T_l > 0 C, refreezes on the liner when T_l < 0 C (latent heat L_fus to / from the liner)',
                   'item: H(T) with ice / water latent heat;  eq_storage += exp(-Ea/R (1/T - 1/193.15)) dt'],
        parameters=_params(), source_ids=[_SIM, _WB, _BR, _MN, _PG], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ult_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('eppendorf_cryocubef570n', caps, slots, {'ult_freezer_thermal': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'settings.configure': (SETTINGS, SETTINGS_EXAMPLE), 'load.place': (ITEM, ITEM_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'ult_freezer_thermal': mechanism})
