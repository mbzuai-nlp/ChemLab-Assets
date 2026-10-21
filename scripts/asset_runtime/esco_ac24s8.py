"""CHEM-193 Esco Airstream Plus AC2-4S8-TU Class II (type A2) biological safety cabinet, 4 ft, stainless side walls: process runtime.

Device (AC2-TUV brochure, 4S8-TU column; S-series product page): work zone 1220 x 580 x 654 mm (0.56 m2 usable), motorized laminated-glass sash
(tested / safe opening 175 mm, maximum 470 mm), inflow 0.48 m/s and downflow 0.35 m/s (369 / 876 / 369 m3/h inflow / downflow / exhaust), about
1/3 of the plenum air exhausted to the room through an H14 filter and 2/3 recirculated through the downflow H14 filter (>99.999 % at MPPS), dual
DC-ECM blowers with self-compensation for filter loading, LED lamps 1193 lx, 200 W nominal, standby -60 % power, 236 kg, UV-30A lamp (accessory,
selected).  Same-platform data (AC2 Gen 3 user manual, Sentinel Gold controller): warm-up 3-15 min, post-purge 0-15 min, UV timer (60 min,
up to 18 h, sash fully closed only), alarm mute 30 s (0-299 s), filter life 10 000 h, UV lamp 2000 h, airflow monitor off outside 18-30 C,
sash states (closed: no blower, no light; safe height: work; fully open: alarm, light on for cleaning), push-and-hold motorized sash stopping
at the safe height, light switched on when the safe height is reached, standby (reduced speed, monitor off, only the FAN key), quickstart
mode, socket 5 A with fuse, sash-motor cycle warnings.

Process model (fixed 0.5 s grid anchored at t = 0, air transport on 0.1 s sub-steps; state held between grid points):
  * Blowers: each branch (downflow, exhaust) has a fan curve dp = u^2 P0 - P0 Q^2/Qmax^2 (duty u, fan laws) against the branch resistance
    dp = R_f (1 + m_dust/M2) Q + K Q^2 (+ the front-opening loss for the exhaust branch); the ECM controller drives u towards the duty that
    holds the target flow (nominal, standby fraction, 0) with a first-order time constant and saturates at 1 (filter loading beyond
    compensation -> flow drops -> AIRFLOW alarm).  Dust captured from the room inflow splits between the filters in proportion to the flows.
  * Air barrier: true inflow velocity = Q_exhaust / (W x opening); operator protection log10 APF = Lmax / (1 + exp(-(v_eff - v50)/w)) with
    v_eff reduced by cross draughts, arm movement, grille blockage and excessive downflow; product protection from the downflow and inflow
    window (performance envelope, engineering shape).  The displayed inflow comes from the exhaust sensor calibrated at the safe height.
  * Air: well-mixed work zone and plenum; room particles (>= 0.5 um count), process aerosol (count, viable fraction) and solvent vapours (kg per
    species) are transported by the flows; filters capture particles (penetration P) but not vapours; escape to the room = generation / APF;
    deposition on the work tray; UV inactivation of viable deposits (dose / D90).  Ledgers close exactly.
  * Chemical scenario: open vessels evaporate (Mackay-Matsugu mass transfer at the downflow velocity, Raoult not needed - single liquids,
    NIST Antoine vapour pressure at the cabinet temperature); the H14 filters do not retain vapours, about 2/3 of the air is recirculated and
    the unducted exhaust returns the vapour to the room (the cabinet is not a fume hood).
  * Electrical: electronics + LED + UV + blower shaft power / efficiency + socket loads (5 A fuse).

Front panel (panel.press / panel.switch / panel.capture / panel.frame): the Sentinel Gold keys, 4-line LCD and LEDs and the key switch (panel_ui.json
beside the contract, from manual chapter 2) run by the generic engine _panel_ui.py on the capabilities below; the menu functions (PIN session,
modes, display settings, clock, field calibration, admin resets) are capabilities of their own (menu.enter ... maintenance.field_calibration)."""
import copy
import hashlib
import json
import math
import tempfile
import re
import zlib
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.esco_ac24s8"
CATALOG_ID = "CHEM-193"
ASSET_VERSION = '1.1.2'
EPOCH_S = 1767225600.0                  # device clock at host time 0: 2026-01-01 00:00:00 (assumption; SET CLOCK / SET DATE move it)
PIN_DEFAULT = dict(fan='0000', admin='0009')
MENU_BEEP_S = 2.0                       # the buzzer sounds when the menu is entered (monitoring suspended; length not stated)
RECERT_S = 365 * 86400.0                # certification expires after a year (manual 5.2: at least once a year)
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t2_vacuum/esco_ac24s8'
LIBS = {'liquids': 'liquid_library.json', 'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def liquids():
    return _lib('liquids')['entries']


def instrument():
    return _lib('instrument')


DOC = {k: v['value'] for k, v in instrument()['documented'].items()}
ENG = {k: v['value'] for k, v in instrument()['engineering'].items()}
ENG_NOTE = {k: v['engineering_note_zh'] for k, v in instrument()['engineering'].items()}
ENG_UNIT = {k: v['unit'] for k, v in instrument()['engineering'].items()}
DT = 0.5                                       # process grid (s)
N_SUB = 5                                      # air-transport sub-steps per grid step
R_GAS = 8.314462618
RHO_AIR = 1.2
W_INT, D_INT, H_INT = DOC['internal_dimensions_m']
V_WZ = W_INT * D_INT * H_INT                   # work-zone air volume (m3)
A_TRAY = DOC['usable_work_area_m2']
Q_IN_NOM = DOC['inflow_volume_m3_s']
Q_DOWN_NOM = DOC['downflow_volume_m3_s']
Q_EXH_NOM = DOC['exhaust_volume_m3_s']
A_DOWN = Q_DOWN_NOM / DOC['downflow_velocity_m_s']   # downflow filter face area (m2)
H_SAFE = DOC['tested_opening_m']
H_MAX = DOC['max_opening_m']
V_IN_NOM = DOC['inflow_velocity_m_s']
V_FAIL = ENG['fail_inflow_fraction'] * V_IN_NOM
PENETRATION = 1.0 - DOC['filter_efficiency']
MAINS_V = 230.0
SOCKET_A = DOC['outlet_rating_a']
MAX_ITEMS = 24
MAX_EVENTS_LOG = 2000
MAX_LOG = 1000
FAULTS = ['exhaust_blower_failure', 'downflow_blower_failure', 'downflow_filter_leak', 'exhaust_obstructed', 'sash_sensor_error', 'airflow_sensor_uncalibrated',
          'filter_clogged']
FAN_STATES = ['off', 'warm_up', 'on', 'standby', 'post_purge']
INTERLOCK = {'fan': ['POST_PURGE'], 'lamp': ['STANDBY', 'POST_PURGE'], 'socket': ['STANDBY', 'WARMING_UP', 'POST_PURGE'],   # panel keys refused per fan state
             'uv': ['STANDBY', 'WARMING_UP', 'POST_PURGE'], 'sash': ['STANDBY', 'WARMING_UP'], 'mute': ['STANDBY'], 'menu': ['STANDBY', 'POST_PURGE']}                              # (manual 2.6 standby, 3.2 warm-up / post-purge)
ITEM_KINDS = ['open_vessel', 'closed_container', 'culture_plate', 'aerosol_source', 'equipment']
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


# ---------------------------------------------------------------- liquids (NIST library)
def _antoine(sp, T):
    f = liquids()[sp]['antoine_fit']
    return 1e5 * 10.0 ** (f['A'] - f['B'] / (T + f['C']))


def hvap_molar(sp, T):
    e = liquids()[sp]
    tc, ref = e.get('critical_temperature_K'), e.get('dhvap_reference')
    if tc and ref and ref['T_K'] < tc - 5.0:
        return ref['value_J_mol'] * (max(tc - T, 0.5) / (tc - ref['T_K'])) ** 0.38
    f = e['antoine_fit']
    return R_GAS * math.log(10.0) * f['B'] * T * T / max((T + f['C']) ** 2, 900.0)


def psat_liquid(sp, T):
    """Vapour pressure (Pa): continuous NIST Antoine fit; Clausius-Clapeyron continuation below T = 30 - C; degenerate narrow fits (< 25 K) only
    inside their range, outside integrated with the Watson enthalpy from the nearer edge."""
    f = liquids()[sp]['antoine_fit']
    lo, hi = f['t_range_K']
    if hi - lo < 25.0:
        if lo <= T <= hi:
            return _antoine(sp, T)
        te = lo if T < lo else hi
        n, hh = 16, (T - te) / 16
        acc = sum((1 if i in (0, n) else 4 if i % 2 else 2) * hvap_molar(sp, te + i * hh) / (te + i * hh) ** 2 for i in range(n + 1)) * hh / 3.0
        return _antoine(sp, te) * math.exp(acc / R_GAS)
    t0 = 30.0 - f['C']
    if T < t0:
        return 1e5 * 10.0 ** (f['A'] - f['B'] / 30.0) * math.exp(-math.log(10.0) * f['B'] * t0 * t0 / 900.0 * (1.0 / T - 1.0 / t0))
    return _antoine(sp, T)


def molar_mass(sp):
    return liquids()[sp]['molar_mass_kg_mol']


def iso_class(c_per_m3):
    """ISO 14644-1 class from the >= 0.5 um particle concentration: C_n = 10^N (0.1/0.5)^2.08, limited to the defined classes 1-9."""
    return round(min(9.0, max(1.0, math.log10(max(c_per_m3, 1e-9) / (0.1 / 0.5) ** 2.08))), 2)


def protection_log(v_eff, lmax, v50, width):
    return lmax / (1.0 + math.exp(-(v_eff - v50) / width))


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
LIQ = dict(type='string', minLength=1, maxLength=60, pattern='^[a-z0-9_]+$')
PLANT_EXAMPLE = dict(ambient_k=295.15, pressure_pa=101325.0, room_particles_m3=3.5e6, room_dust_kg_m3=3e-8, cross_draft_m_s=0.0, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 278.15, 318.15), pressure_pa=number('Pa', 80000.0, 106000.0), room_particles_m3=number('1/m3', 0.0, 1e10),
                 room_dust_kg_m3=number('kg/m3', 0.0, 1e-5), cross_draft_m_s=number('m/s', 0.0, 2.0), noise_seed=integer(0, 2 ** 31 - 1),
                 downflow_filter_dust_kg=number('kg', 0.0, 1.0), exhaust_filter_dust_kg=number('kg', 0.0, 1.0), filter_hours_h=number('h', 0.0, 1e5),
                 uv_hours_h=number('h', 0.0, 1e5), sash_cycles=integer(0, 10 ** 6)),
            optional=('downflow_filter_dust_kg', 'exhaust_filter_dust_kg', 'filter_hours_h', 'uv_hours_h', 'sash_cycles'))
ITEM = obj(dict(item_id=SID, kind=enum(ITEM_KINDS), x_m=number('m', 0.0, D_INT), y_m=number('m', -W_INT / 2, W_INT / 2), footprint_m2=number('m2', 1e-4, 0.3),
                liquid=LIQ, liquid_mass_kg=number('kg', 0.0, 5.0), surface_area_m2=number('m2', 1e-5, 0.2), aerosol_rate_per_s=number('1/s', 0.0, 1e9),
                viable_fraction=number('1', 0.0, 1.0), power_w=number('W', 0.0, 3000.0)),
           optional=('liquid', 'liquid_mass_kg', 'surface_area_m2', 'aerosol_rate_per_s', 'viable_fraction', 'power_w'))
SETTINGS = obj(dict(warm_up_s=number('s', 180.0, 900.0), post_purge_s=number('s', 0.0, 900.0), uv_timer_s=number('s', 0.0, 64800.0), mute_s=number('s', 0.0, 299.0),
                    mode=enum(['normal', 'quickstart']), airflow_monitor=BOOL, data_log_period_s=number('s', 10.0, 3600.0)),
               optional=('warm_up_s', 'post_purge_s', 'uv_timer_s', 'mute_s', 'mode', 'airflow_monitor', 'data_log_period_s'))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'operation', 'item', 'alarm', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
_BODY = ['cabinet_body']
_PANEL = ['control_panel']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明房间条件：环境温度与气压、室内≥0.5 µm粒子与粉尘浓度、横向气流，以及过滤器已积尘量与计时器初值（维护史）', 'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['key_switch', 'control_panel'], '钥匙开关通/断电（断电时风机、照明、UV与插座全部停止）', 'agent')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['key_switch'], '钥匙开关拨一下（等价于power.set取反）', 'agent')
cap('fan.set', obj(dict(state=enum(['on', 'off', 'standby']))), obj(dict(state=TEXT)), dict(state='on'), ['fan_button', 'downflow_blower', 'exhaust_blower'],
    '风机开（预热后进入工作状态）、关（进入后吹扫）或待机（降速、风量监测停用）', 'agent')
cap('fan.toggle', obj({}), obj(dict(state=TEXT)), {}, ['fan_button'], '面板风机键：关→开（预热），开/待机→关（后吹扫）', 'agent')
cap('lamp.toggle', obj({}), obj(dict(on=BOOL)), {}, ['lamp_button', 'led_light_bar'], '面板照明键：工作区LED灯开/关（窗全关时不能开）', 'agent')
cap('socket.toggle', obj({}), obj(dict(on=BOOL)), {}, ['socket_button', 'outlet_provision'], '面板插座键：柜内插座通/断（合计5 A，过载熔断）', 'agent')
cap('uv.toggle', obj({}), obj(dict(on=BOOL)), {}, ['uv_button', 'uv_lamp'], '面板UV键：UV灯开/关（只在窗全关、风机停且后吹扫结束时可开；按UV定时自动关）', 'agent')
cap('sash.move', obj(dict(direction=enum(['up', 'down']), hold_s=number('s', 0.1, 60.0)), optional=('hold_s',)), obj(dict(target_m=number('m'), moving=BOOL)),
    dict(direction='up'), ['up_button', 'down_button', 'sash_window'],
    '面板上/下键（按住移动电动窗）：不给hold_s时按住直到下一个停位（全关→安全高度→全开，或反向）；给hold_s时只移动这么久（中途松手停住，照明自动关）', 'agent')
cap('alarm.mute', obj({}), obj(dict(muted_until_s=nullable(number('s')))), {}, ['set_button'], '面板静音键：静音窗位全开报警与风量报警（静音时间到后再响）', 'agent')
cap('settings.configure', SETTINGS, obj(dict(settings=FREE_OBJ)), dict(warm_up_s=300.0, post_purge_s=180.0), ['control_panel', 'menu_pad'],
    '菜单设置：预热时间（3–15 min）、后吹扫（0–15 min）、UV定时（0–18 h）、静音时间（0–299 s）、正常/快速启动模式、风量监测开关、数据记录周期', 'agent', 'configuration')
cap('item.place', ITEM, obj(dict(item_id=TEXT, notes=array(TEXT, 0, 8))),
    dict(item_id='plate_1', kind='open_vessel', x_m=0.30, y_m=0.0, footprint_m2=0.0064, liquid='ethanol', liquid_mass_kg=0.02, surface_area_m2=0.0064), ['work_tray'],
    '把物品放进工作区（窗至少在安全高度）：开口容器（液体库物种、质量、液面面积）、密闭容器、培养皿、气溶胶源（粒子/秒与活菌比例）或用电设备（功率，接柜内插座）；位置x为距前开口内沿的深度', 'agent', 'robot_skill')
cap('item.remove', obj(dict(item_id=SID)), obj(dict(item_id=TEXT, state=FREE_OBJ)), dict(item_id='plate_1'), ['work_tray'], '取出物品（窗至少在安全高度）', 'agent', 'robot_skill')
cap('operator.set', obj(dict(arms_in=BOOL, arm_speed_m_s=number('m/s', 0.0, 2.0)), optional=('arm_speed_m_s',)), obj(dict(arms_in=BOOL, arm_speed_m_s=number('m/s'))),
    dict(arms_in=True, arm_speed_m_s=0.1), ['sash_window', 'front_grille'], '操作者手臂伸入/抽出工作区与手臂移动速度（影响前开口气幕）', 'agent', 'robot_skill')
cap('datalog.read', obj(dict(last=integer(1, MAX_LOG)), optional=('last',)), obj(dict(records=array(FREE_OBJ, 0, MAX_LOG), period_s=number('s'))), dict(last=10), _PANEL,
    '读取RS232数据记录（按记录周期采样的风机状态、窗位、显示风速、报警、计时器）', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _BODY,
    '读取事件记录（agent看不到故障与真值事件）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(airflow=FREE_OBJ, containment=FREE_OBJ, workzone=FREE_OBJ, items=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, _BODY,
    '评测者读取过程真值：两支路风量与占空比、过滤器阻力与积尘、真实入流/下行流、操作者与产品保护因子、工作区与静压箱粒子/气溶胶/溶剂蒸气浓度、ISO级别、台面活菌、各物品状态', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '粒子数、气溶胶、活菌、溶剂质量与积尘的守恒账、电能', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='exhaust_blower_failure', active=True), _BODY,
    '故障注入：排气/下行流风机失效、下行流过滤器泄漏、排气口被遮挡、窗位传感器故障、风速传感器未标定、过滤器堵塞（积尘到无法补偿）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
PIN = {'type': 'string', 'pattern': '^[0-9]{4}$'}
DATE = {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'}
_MENU = ['menu_pad', 'control_panel']
cap('menu.enter', obj(dict(pin=PIN), optional=('pin',)), obj(dict(level=TEXT)), dict(pin='0000'), _MENU,
    'MENU键＋四位PIN进入菜单（FAN PIN：设置与模式；ADMIN PIN：另有维护模式、现场标定与管理员设置；维护模式中不要PIN）；进入时蜂鸣器响、监测暂停，预热中只认ADMIN PIN且预热暂停', 'agent', 'configuration')
cap('menu.exit', obj({}), obj(dict(open=BOOL)), {}, _MENU, '退出菜单（恢复监测与预热计时）', 'agent', 'configuration')
cap('pin.check', obj(dict(pin=PIN)), obj(dict(level=TEXT)), dict(pin='0000'), _MENU, '输入FAN PIN（风机键与从全关抬窗前的PIN）：返回权限级别', 'agent', 'configuration')
cap('pin.set', obj(dict(which=enum(['fan', 'admin']), pin=PIN)), obj(dict(which=TEXT, pin_set=BOOL)), dict(which='fan', pin='1234'), _MENU,
    '管理员设置：新ADMIN PIN或新FAN PIN（FAN PIN 0000为停用）', 'agent', 'configuration')
cap('mode.set', obj(dict(mode=enum(['normal', 'quickstart', 'maintenance']))), obj(dict(mode=TEXT, maintenance=BOOL)), dict(mode='normal'), _MENU,
    'SET MODE：正常、快速启动或维护模式（维护模式需ADMIN PIN：报警停用、联锁解除，LCD闪烁MAINTENANCE）', 'agent', 'configuration')
cap('menu.configure', obj(dict(unit=enum(['metric', 'imperial']), filter_life_display=BOOL, language=enum(['english', 'german']), airflow_out_temp=BOOL,
                               date_certified=DATE), optional=('unit', 'filter_life_display', 'language', 'airflow_out_temp', 'date_certified')),
    obj(dict(settings=FREE_OBJ)), dict(unit='imperial'), _MENU,
    '显示设置：风速单位（m/s或fpm）、过滤器寿命显示、LCD语言、环境温度超出18–30 °C时是否显示风速、认证日期（满一年提示重新认证；后四项需ADMIN PIN）', 'agent', 'configuration')
cap('clock.set', obj(dict(date=DATE, time=dict(type='string', pattern='^[0-9]{2}:[0-9]{2}$')), optional=('date', 'time')), obj(dict(clock=TEXT)),
    dict(time='08:30'), _MENU, 'SET CLOCK / SET DATE：设定LCD时钟（断电后保持）', 'agent', 'configuration')
cap('meters.reset', obj(dict(meter=enum(['blower', 'uv', 'filter']))), obj(dict(meters=FREE_OBJ)), dict(meter='filter'), _MENU,
    '管理员设置：风机、UV灯或过滤器小时计清零（更换后）', 'agent', 'configuration')
cap('sash.set_cycles', obj(dict(count=integer(0, 16000))), obj(dict(sash_cycles=integer(0, 10 ** 6))), dict(count=0), _MENU, '管理员设置：窗电机循环次数清零或输入（0–16000）', 'agent',
    'configuration')
cap('settings.reset_default', obj({}), obj(dict(settings=FREE_OBJ)), {}, _MENU,
    '管理员设置：恢复出厂设置（预热3 min、后吹扫0、UV定时60 min、公制、风量监测开、ADMIN PIN 0009、过滤器寿命不显示、FAN PIN 0000；标定与小时计不变）', 'agent', 'configuration')
cap('maintenance.field_calibration', obj(dict(action=enum(['calibrate', 'reset']), measured_inflow_m_s=number('m/s', 0.1, 1.5)), optional=('measured_inflow_m_s',)),
    obj(dict(calibrated=BOOL, display_inflow_m_s=nullable(number('m/s')))), dict(action='calibrate', measured_inflow_m_s=0.45), _MENU,
    'FIELD CALIB（需ADMIN PIN）：AIRFLOW CALIBRATION按参考风速计测得的入流把显示对到参考值（风机工作、窗在安全高度）；RESET CALIBRATION恢复工厂标定', 'agent', 'configuration')
UNSUPPORTED = {
    'decontamination.vhp': '过氧化氢（VHP/HPV）气体去污染需要选配的注入口与外部发生器（未选）；福尔马林/二氧化氯熏蒸由维护人员按手册另行进行。',
    'exhaust.thimble_collar': '套管排气罩（接建筑排风，需554 m³/h、38 Pa）为选件（未选）：本柜排气回到室内。',
    'service_fixture.open': '气体/真空/水服务旋塞为改装预留（未安装）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.esco_ac24s8 (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import importlib.util
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


def _hook_text(panel, ctx, s):
    """Menu fields keyed in as numbers, written as text (time HH:MM, dates YYYY-MM-DD): s['pattern'].format(*fields)."""
    return s['pattern'].format(*[int(round(panel._src({'field': f}, ctx) or 0)) for f in s['fields']])


_panel_ui = _panel_engine()
PANEL_SPEC = _panel_ui.load_spec(ASSET_DIR / 'panel_ui.json', capabilities=set(CAPS))
PANEL_CAPS = _panel_ui.capability_definitions(PANEL_SPEC)                    # panel.press / switch / capture / frame (kept out of CAPS:
ALL_CAPS = dict(CAPS, **PANEL_CAPS)                                          # CAPS stays the instrument's own command set)
PANEL_HOOKS = {'ac2_text': _hook_text}


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'cabinet_body', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'cabinet_body', '房间条件是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'key_switch', '钥匙开关是否接通')
slot('fan.state', enum(FAN_STATES), '1', 'derived', 'fan_button', '风机状态：off、warm_up（预热）、on、standby（待机降速）、post_purge（后吹扫）')
slot('fan.remaining_s', nullable(number('s')), 's', 'derived', 'lcd_display', '预热或后吹扫剩余时间（其他状态为空）')
slot('sash.opening_m', number('m', 0.0, H_MAX), 'm', 'derived', 'sash_window', '电动窗开口高度（0全关、0.175 m安全工作高度、0.470 m全开）')
slot('sash.status', enum(['closed', 'safe', 'full_open', 'intermediate', 'moving', 'error']), '1', 'sensor', 'sash_window', '窗位磁开关判定的窗状态（closed=UV模式）')
slot('lamp.on', BOOL, '1', 'derived', 'led_light_bar', '工作区LED照明是否亮')
slot('socket.on', BOOL, '1', 'derived', 'outlet_provision', '柜内插座是否通电（熔断后为false）')
slot('uv.on', BOOL, '1', 'derived', 'uv_lamp', 'UV灯是否亮')
slot('uv.remaining_s', nullable(number('s')), 's', 'derived', 'uv_lamp', 'UV定时剩余时间（UV关或定时为无限时为空）')
slot('display.inflow_m_s', nullable(number('m/s')), 'm/s', 'sensor', 'lcd_display', '屏幕显示的入流风速（由排气风速传感器在安全窗高下标定换算，0.01 m/s分辨率；预热、待机、监测停用或断电时为空）')
slot('display.downflow_m_s', nullable(number('m/s')), 'm/s', 'sensor', 'lcd_display', '屏幕显示的下行流风速（下行流传感器，0.01 m/s分辨率；同上为空）')
slot('airflow.status', enum(['off', 'warm_up', 'ok', 'fail', 'standby', 'disabled', 'sash_closed']), '1', 'derived', 'lcd_display', '屏幕风量状态（Airflow: OK / NO!；预热、待机、监测停用；sash_closed＝风机工作或后吹扫时窗全关，风速传感器按安全高度标定，不判风量故障）')
slot('cabinet.temperature_k', nullable(number('K')), 'K', 'sensor', 'airflow_sensor', '诊断模式显示的柜内温度（断电为空）')
slot('alarms.active', array(TEXT, 0, 16), '1', 'derived', 'control_panel', '当前报警与警告（窗位、风量、窗位传感器、传感器未标定、过滤器寿命、UV灯寿命、窗电机循环；插座熔断只记事件）')
slot('alarm.audible', BOOL, '1', 'derived', 'alarm_led', '蜂鸣器是否在响（有声报警且未静音）')
slot('indicator.fan', BOOL, '1', 'derived', 'fan_led', '风机键绿色指示灯')
slot('indicator.light', BOOL, '1', 'derived', 'light_led', '照明/插座键蓝色指示灯（照明或插座任一开）')
slot('indicator.uv', BOOL, '1', 'derived', 'uv_led', 'UV键橙色指示灯')
slot('indicator.alarm', BOOL, '1', 'derived', 'alarm_led', '面板红色报警灯（有报警时亮）')
slot('meters', FREE_OBJ, '1', 'derived', 'lcd_display', '诊断计时器：风机小时、过滤器小时与寿命百分比（10000 h）、UV灯小时与寿命百分比（2000 h）、窗电机循环次数')
slot('settings.current', FREE_OBJ, '1', 'setpoint', 'control_panel', '当前菜单设置（预热、后吹扫、UV定时、静音时间、模式、风量监测、记录周期）')
slot('menu.state', FREE_OBJ, '1', 'derived', 'menu_pad', '菜单与面板状态：菜单是否打开及权限级别、维护模式、单位、语言、过滤器寿命显示、超温显示风速、认证日期、FAN PIN是否启用、现场标定增益')
slot('device.clock', TEXT, '1', 'derived', 'lcd_display', 'LCD时钟（YYYY-MM-DDTHH:MM:SS）')
slot('operator.arms_in', BOOL, '1', 'setpoint', 'front_grille', '操作者手臂是否伸入工作区')
slot('items.present', array(TEXT, 0, MAX_ITEMS), '1', 'derived', 'work_tray', '工作区内物品标识')
slot('truth.airflow', FREE_OBJ, '1', 'process_truth', 'downflow_blower', '风量真值：两支路风量、占空比、过滤器阻力与积尘、真实入流/下行流速度、功耗', 'evaluator')
slot('truth.containment', FREE_OBJ, '1', 'process_truth', 'front_grille', '保护真值：有效入流、气幕的操作者保护因子（APF）与产品保护因子（PPF）的对数（没有入流时为0）、实际逸出速率（经气幕直接逸出＋前开口交换带出，与账目一致；评价逸出请用aerosol_escape_rate_per_s）', 'evaluator')
slot('truth.workzone', FREE_OBJ, '1', 'process_truth', 'work_tray', '工作区与静压箱真值：室内粒子、过程气溶胶、活菌、溶剂蒸气浓度（kg/m³与ppm）、ISO 14644-1级别、台面活菌', 'evaluator')
slot('truth.items', FREE_OBJ, '1', 'process_truth', 'work_tray', '各物品真值：剩余液体、蒸发速率、累计逸出', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'cabinet_body', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'sash.opening_m', 'display.inflow_m_s', 'display.downflow_m_s', 'lamp.on', 'indicator.fan', 'indicator.light', 'indicator.uv', 'indicator.alarm'}


# ---------------------------------------------------------------- runtime
class EscoAc24s8Runtime:
    def __init__(self, instance_id='esco_ac24s8_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.powered = False
        self.fan = 'off'
        self.fan_timer = 0.0
        self.u = dict(down=0.0, exh=0.0)                  # ECM duties
        self.Q = dict(down=0.0, exh=0.0)                  # branch flows (m3/s)
        self.dust = dict(down=0.0, exh=0.0)               # dust on the filters (kg)
        self.h = 0.0                                      # sash opening (m)
        self.sash_target = 0.0
        self.sash_stop_at = None                          # host time at which a held key is released (hold_s)
        self.sash_moving_from = 0.0
        self.sash_start_at = None                         # host time of the key press (motion starts there, not at the previous grid point)
        self._dust_before_clog = None
        self.escape_rate = 0.0                            # process aerosol leaving the front in the last step (per s)
        self.lamp = False
        self.socket = False
        self.fuse_blown = False
        self.uv = False
        self.uv_left = None
        self.muted_until = None
        self.settings = dict(warm_up_s=DOC['warm_up_s']['default'], post_purge_s=DOC['post_purge_s']['default'], uv_timer_s=DOC['uv_timer_s']['default'],
                             mute_s=DOC['mute_s']['default'], mode='normal', airflow_monitor=True, data_log_period_s=60.0)
        self.meters = dict(blower_h=0.0, filter_h=0.0, uv_h=0.0, sash_cycles=0)
        self.arms_in = False
        self.arm_speed = 0.0
        self.items = {}
        self.c = dict(wz_room=0.0, pl_room=0.0, wz_proc=0.0, pl_proc=0.0, wz_via=0.0, pl_via=0.0)   # particle counts per m3
        self.vap = {}                                     # species -> dict(wz=kg/m3, pl=kg/m3)
        self.surface_viable = 0.0                         # viable particles deposited on the tray (count)
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.log, self.next_log = [], 0.0
        self.warnings = {}
        self.revision = 0
        self.energy_j = 0.0
        self.P_el = 0.0
        self.led = dict(room_in=0.0, room_captured_down=0.0, room_captured_exh=0.0, room_emitted=0.0, room_deposited=0.0, room_returned=0.0,
                        proc_generated=0.0, proc_escaped=0.0, proc_captured_down=0.0, proc_captured_exh=0.0, proc_emitted=0.0, proc_deposited=0.0,
                        viable_generated=0.0, viable_escaped=0.0, viable_captured=0.0, viable_emitted=0.0, viable_deposited=0.0, viable_uv_inactivated=0.0,
                        dust_in_kg=0.0, vapour={})
        self.T_cab = None
        self._principals, self._requests, self._caller = {}, {}, None
        self.menu = dict(open=False, level=None, beep_until=None)              # menu session (MENU + PIN)
        self.pins = dict(PIN_DEFAULT)
        self.maintenance = False
        self.disp = dict(unit='metric', filter_life_display=False, language='english', airflow_out_temp=False, date_certified=None)
        self.clock_offset = 0.0
        self.cal_gain = 1.0                                                    # field calibration of the inflow display
        self.power_epoch = 0
        self.panel_capture_dir = None                                          # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)
        self.panel = _panel_ui.PanelUI(PANEL_SPEC, self, hooks=PANEL_HOOKS)

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
        return EscoAc24s8Session(self, name)

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

    # ---- derived quantities
    def _sash_state(self):
        tol = ENG['sash_switch_tolerance_m']
        if 'sash_sensor_error' in self.faults:
            return 'error'
        if self.h != self.sash_target:
            return 'moving'
        if self.h <= tol:
            return 'closed'
        if abs(self.h - H_SAFE) <= tol:
            return 'safe'
        if self.h >= H_MAX - tol:
            return 'full_open'
        return 'intermediate'

    def _target_flows(self):
        if not self.powered or self.fan == 'off':
            return 0.0, 0.0
        if self.fan == 'standby':
            f = ENG['standby_flow_fraction']
            return f * Q_DOWN_NOM, f * Q_EXH_NOM
        return Q_DOWN_NOM, Q_EXH_NOM

    def _branch(self, b, h=None):
        """(R_lin, K_quad) of a branch: filter (linear, loaded) + system (quadratic) + front opening (exhaust branch only; opening h, default the sash)."""
        if b == 'down':
            Qn, dpf, dps, m2 = Q_DOWN_NOM, ENG['filter_dp_down_clean_pa'], ENG['system_dp_down_pa'], ENG['dust_doubling_down_kg']
        else:
            Qn, dpf, dps, m2 = Q_EXH_NOM, ENG['filter_dp_exh_clean_pa'], ENG['system_dp_exh_pa'], ENG['dust_doubling_exh_kg']
        R = dpf / Qn * (1.0 + self.dust[b] / m2)
        K = dps / Qn ** 2
        if b == 'exh':
            area = max(W_INT * (self.h if h is None else h), ENG['sash_leak_area_m2'])
            K += RHO_AIR / (2.0 * (ENG['opening_discharge_coeff'] * area) ** 2)
            if 'exhaust_obstructed' in self.faults:
                K += 6.0 * dps / Qn ** 2 + 400.0 / Qn ** 2
        return R, K

    def _fan(self, b):
        Qn = Q_DOWN_NOM if b == 'down' else Q_EXH_NOM
        return ENG['blower_shutoff_pa'], ENG['blower_free_flow_ratio'] * Qn

    def _flow_at(self, b, u):
        if u <= 0.0:
            return 0.0
        R, K = self._branch(b)
        P0, Qm = self._fan(b)
        a = K + P0 / Qm ** 2
        return (-R + math.sqrt(R * R + 4.0 * a * u * u * P0)) / (2.0 * a)

    def _duty_for(self, b, Q, h=None):
        if Q <= 0.0:
            return 0.0
        R, K = self._branch(b, h)
        P0, Qm = self._fan(b)
        return math.sqrt((R * Q + (K + P0 / Qm ** 2) * Q * Q) / P0)

    def _dp(self, b):
        R, K = self._branch(b)
        return R * self.Q[b] + K * self.Q[b] ** 2

    def _velocities(self):
        v_in = self.Q['exh'] / max(W_INT * self.h, ENG['sash_leak_area_m2'])
        return v_in, self.Q['down'] / A_DOWN

    def _block_fraction(self):
        zone = ENG['grille_zone_depth_m']
        blocked = sum(min(math.sqrt(it['footprint_m2']), W_INT) for it in self.items.values() if it['x_m'] < zone)
        if self.arms_in:
            blocked += 0.2                                   # two forearms resting on the grille (engineering)
        return min(blocked / W_INT, 1.0)

    def _protection(self):
        v_in, v_down = self._velocities()
        if self.Q['exh'] <= 1e-9:
            return dict(v_in=v_in, v_down=v_down, v_eff=0.0, apf_log10=0.0, ppf_log10=0.0)
        v_eff = (v_in * (1.0 - ENG['grille_block_weight'] * self._block_fraction()) - ENG['cross_draft_weight'] * self.plant['cross_draft_m_s']
                 - ENG['arm_motion_weight'] * (self.arm_speed if self.arms_in else 0.0) - max(0.0, v_down - ENG['downflow_excess_m_s']))
        apf = protection_log(v_eff, ENG['apf_log_max'], ENG['apf_v50_m_s'], ENG['apf_width_m_s'])
        v_p = min(v_down - ENG['ppf_down_min_m_s'], ENG['ppf_inflow_max_m_s'] - v_in, v_eff - ENG['apf_v50_m_s'] + 0.10)
        ppf = protection_log(v_p, ENG['ppf_log_max'], 0.0, ENG['apf_width_m_s'])
        return dict(v_in=v_in, v_down=v_down, v_eff=v_eff, apf_log10=apf, ppf_log10=ppf)

    def _p_atm(self):
        return self.plant['pressure_pa']

    def _evap_rate(self, it):
        """kg/s from an open liquid surface (Mackay-Matsugu, U = local air speed); background = work-zone concentration."""
        if it['kind'] != 'open_vessel' or it['liquid_mass_kg'] <= 0.0:
            return 0.0
        sp, T = it['liquid'], self.T_cab or self.plant['ambient_k']
        M = molar_mass(sp)
        D = ENG['water_diffusivity_air_m2_s'] * math.sqrt(0.01801528 / M)
        Sc = ENG['air_kinematic_viscosity_m2_s'] / D
        U = max(self.Q['down'] / A_DOWN, 0.05) * 3600.0              # m/h; 0.05 m/s natural convection without downflow
        X = math.sqrt(it['surface_area_m2'])
        K = ENG['evap_mackay_coeff'] * U ** 0.78 * X ** -0.11 * Sc ** -0.67 / 3600.0
        c_sat = min(psat_liquid(sp, T), self._p_atm()) * M / (R_GAS * T)
        c_bg = self.vap.get(sp, {}).get('wz', 0.0)
        return max(K * it['surface_area_m2'] * (c_sat - c_bg), 0.0)

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
        pl = self.plant
        # ---- sash motor (push and hold)
        if self.h != self.sash_target:
            if 'sash_sensor_error' in self.faults or self.meters['sash_cycles'] >= DOC['sash_cycle_warnings'][2]:
                self.sash_target = self.h
            else:
                t0 = max(self.t, self.sash_start_at if self.sash_start_at is not None else self.t)       # motion starts at the key press
                t1 = self.t + h if self.sash_stop_at is None else min(self.t + h, self.sash_stop_at)
                step_ = ENG['sash_speed_m_s'] * max(0.0, t1 - t0)
                self.sash_start_at = None
                d = self.sash_target - self.h
                self.h = self.sash_target if abs(d) <= step_ + 1e-12 else self.h + math.copysign(step_, d)
                if self.sash_stop_at is not None and self.t + h >= self.sash_stop_at - 1e-9:
                    self.sash_target = self.h                       # key released
                if self.h == self.sash_target:
                    self.sash_stop_at = None
                    self._sash_arrived()
        # ---- fan state timers
        if self.fan in ('warm_up', 'post_purge') and not (self.fan == 'warm_up' and self.menu['open']):   # the menu pauses the warm-up
            self.fan_timer -= h
            if self.fan_timer <= 1e-9:
                self.fan_timer = 0.0
                if self.fan == 'warm_up':
                    self.fan = 'on'
                    self._event('operation', 'WARM_UP_DONE', '预热结束，可以工作')
                else:
                    self.fan = 'off'
                    self._event('operation', 'POST_PURGE_DONE', '后吹扫结束，风机停止')
        # ---- ECM controllers and flows
        q_target = dict(zip(('down', 'exh'), self._target_flows()))
        a = 1.0 - math.exp(-h / ENG['ecm_control_tau_s'])
        for b in ('down', 'exh'):
            failed = (b == 'down' and 'downflow_blower_failure' in self.faults) or (b == 'exh' and 'exhaust_blower_failure' in self.faults)
            u_req = 0.0 if failed else min(1.0, self._duty_for(b, q_target[b]))
            if b == 'exh' and self._sash_state() == 'closed':
                u_req = min(u_req, self._duty_for(b, q_target[b], H_SAFE))       # closed sash: the controller holds the safe-height duty, no run-away
            self.u[b] += (u_req - self.u[b]) * a
            if self.u[b] < 1e-6 and u_req == 0.0:
                self.u[b] = 0.0
            self.Q[b] = self._flow_at(b, self.u[b])
        # ---- dust loading (all room dust drawn in is captured on the first pass, split by the flows)
        q_in = self.Q['exh']
        tot = self.Q['down'] + self.Q['exh']
        if q_in > 0 and tot > 0:
            dm = pl['room_dust_kg_m3'] * q_in * h
            self.dust['down'] += dm * self.Q['down'] / tot
            self.dust['exh'] += dm * self.Q['exh'] / tot
            self.led['dust_in_kg'] += dm
        # ---- air transport (particles, aerosol, vapours) on sub-steps
        prot = self._protection()
        self._transport(h, prot)
        # ---- UV
        if self.uv:
            self.meters['uv_h'] += h / 3600.0
            dose = ENG['uv_irradiance_w_m2'] * h
            f = 1.0 - 10.0 ** (-dose / ENG['uv_d90_j_m2'])
            kill = self.surface_viable * f + self.c['wz_via'] * V_WZ * f            # tray deposits and airborne viable aerosol in the irradiated work zone
            self.surface_viable -= self.surface_viable * f
            self.c['wz_via'] -= self.c['wz_via'] * f
            self.led['viable_uv_inactivated'] += kill
            if self.uv_left is not None:
                self.uv_left -= h
                if self.uv_left <= 1e-9:
                    self.uv, self.uv_left = False, None
                    self._event('operation', 'UV_TIMER_DONE', 'UV定时结束，UV灯关闭')
        # ---- hour meters
        if self.Q['down'] + self.Q['exh'] > 1e-6:
            self.meters['blower_h'] += h / 3600.0
            self.meters['filter_h'] += h / 3600.0
        # ---- electrical power and cabinet temperature
        shaft = sum(self.Q[b] * self._dp(b) for b in ('down', 'exh')) / ENG['blower_efficiency'] if self.powered else 0.0   # unpowered blowers coast down
        sock = sum(it.get('power_w') or 0.0 for it in self.items.values() if it['kind'] == 'equipment') if (self.socket and self.powered) else 0.0
        if sock > SOCKET_A * MAINS_V + 1e-9:
            self.socket, self.fuse_blown, sock = False, True, 0.0
            self._event('alarm', 'SOCKET_FUSE_BLOWN', '柜内插座过载（>5 A），熔断器熔断')
        P = (ENG['electronics_w'] if self.powered else 0.0) + (ENG['led_lamp_w'] if self.lamp else 0.0) + (ENG['uv_lamp_w'] if self.uv else 0.0) + shaft + sock
        self.P_el = P
        self.energy_j += P * h
        self.T_cab = pl['ambient_k'] + ENG['cabinet_temp_rise_k_per_w'] * (P - 0.6 * shaft)
        # ---- data log
        if self.powered and self.t + h >= self.next_log - 1e-9:
            self.log.append(dict(time_s=round(self.t + h, 3), fan=self.fan, sash=self._sash_state(), inflow_m_s=self._display('in'), downflow_m_s=self._display('down'),
                                 alarms=sorted(self.warnings)))
            del self.log[:-MAX_LOG]
            self.next_log = self.t + h + self.settings['data_log_period_s']

    def _transport(self, h, prot):
        pl = self.plant
        dt = h / N_SUB
        Qd, Qe = self.Q['down'], self.Q['exh']
        Qin = Qe
        open_frac = 1.0 if self.h > ENG['sash_switch_tolerance_m'] else ENG['sash_leak_area_m2'] / (W_INT * H_SAFE)   # closed sash: exchange only through the sash leak
        w = math.exp(-Qin / ENG['thermal_exchange_m3_s'])                # weight of the front exchange: 1 without inflow, -> 0 once the air barrier stands
        Qth = open_frac * (ENG['thermal_exchange_m3_s'] + ENG['downflow_spill_fraction'] * Qd) * w   # thermal exchange + downflow spilling out of the front
        P_down = 1e-3 if 'downflow_filter_leak' in self.faults else PENETRATION
        P_exh = PENETRATION
        esc = 10.0 ** (-prot['apf_log10']) * (1.0 - w) if Qin > 1e-9 else 0.0   # direct escape through the barrier; without inflow releases leave with Qth
        ingress = 10.0 ** (-prot['ppf_log10']) if Qin > 1e-9 else 0.0
        Qi = Qin * ingress                                              # room air entering the work zone leaves it again through the grilles to the plenum
        c_room = pl['room_particles_m3']
        k_dep = ENG['deposition_velocity_m_s'] * A_TRAY
        S_proc = sum(it.get('aerosol_rate_per_s') or 0.0 for it in self.items.values() if it['kind'] == 'aerosol_source')
        S_via = sum((it.get('aerosol_rate_per_s') or 0.0) * (it.get('viable_fraction') or 0.0) for it in self.items.values() if it['kind'] == 'aerosol_source')
        evap = {}
        for it in self.items.values():
            r = self._evap_rate(it)
            if r > 0:
                r = min(r, it['liquid_mass_kg'] / h)
                it['liquid_mass_kg'] -= r * h
                it['evaporated_kg'] += r * h
                it['last_rate_kg_s'] = r
                evap[it['liquid']] = evap.get(it['liquid'], 0.0) + r
            elif it['kind'] == 'open_vessel':
                it['last_rate_kg_s'] = 0.0
        L = self.led
        esc0 = L['proc_escaped']
        for _ in range(N_SUB):
            c = self.c
            # room particles: inflow carried into the plenum, a fraction ingresses the work zone; with the fan off thermal exchange
            room_in_pl = Qin * c_room * (1.0 - ingress) * dt
            room_in_wz = (Qin * c_room * ingress + Qth * c_room) * dt
            L['room_in'] += room_in_pl + room_in_wz
            d_wz = room_in_wz + P_down * c['pl_room'] * Qd * dt - (Qd + Qi + Qth) * c['wz_room'] * dt - k_dep * c['wz_room'] * dt
            d_pl = room_in_pl + (Qd + Qi) * c['wz_room'] * dt - Qd * c['pl_room'] * dt - Qe * c['pl_room'] * dt
            L['room_captured_down'] += (1 - P_down) * c['pl_room'] * Qd * dt
            L['room_captured_exh'] += (1 - P_exh) * c['pl_room'] * Qe * dt
            L['room_emitted'] += P_exh * c['pl_room'] * Qe * dt
            L['room_deposited'] += k_dep * c['wz_room'] * dt
            L['room_returned'] += Qth * c['wz_room'] * dt
            c['wz_room'] += d_wz / V_WZ
            c['pl_room'] += d_pl / ENG['plenum_volume_m3']
            # process aerosol and its viable part
            for key, S, gen, escd, capd, emit, dep in (('proc', S_proc, 'proc_generated', 'proc_escaped', None, 'proc_emitted', 'proc_deposited'),
                                                       ('via', S_via, 'viable_generated', 'viable_escaped', 'viable_captured', 'viable_emitted', 'viable_deposited')):
                wz, pk = 'wz_' + key, 'pl_' + key
                gen_n = S * dt
                L[gen] += gen_n
                out_room = gen_n * esc + Qth * c[wz] * dt
                L[escd] += out_room
                d_wz = gen_n * (1 - esc) + P_down * c[pk] * Qd * dt - (Qd + Qi + Qth) * c[wz] * dt - k_dep * c[wz] * dt
                d_pl = (Qd + Qi) * c[wz] * dt - Qd * c[pk] * dt - Qe * c[pk] * dt
                cap_n = (1 - P_down) * c[pk] * Qd * dt + (1 - P_exh) * c[pk] * Qe * dt
                if key == 'proc':
                    L['proc_captured_down'] += (1 - P_down) * c[pk] * Qd * dt
                    L['proc_captured_exh'] += (1 - P_exh) * c[pk] * Qe * dt
                else:
                    L[capd] += cap_n
                    self.surface_viable += k_dep * c[wz] * dt
                L[emit] += P_exh * c[pk] * Qe * dt
                L[dep] += k_dep * c[wz] * dt
                c[wz] += d_wz / V_WZ
                c[pk] += d_pl / ENG['plenum_volume_m3']
            # vapours: not retained by the filters
            for sp in set(evap) | set(self.vap):
                v = self.vap.setdefault(sp, dict(wz=0.0, pl=0.0))
                lv = L['vapour'].setdefault(sp, dict(evaporated_kg=0.0, escaped_front_kg=0.0, exhausted_to_room_kg=0.0))
                g = evap.get(sp, 0.0) * dt
                lv['evaporated_kg'] += g
                lv['escaped_front_kg'] += g * esc + Qth * v['wz'] * dt
                lv['exhausted_to_room_kg'] += Qe * v['pl'] * dt
                d_wz = g * (1 - esc) + Qd * v['pl'] * dt - (Qd + Qi + Qth) * v['wz'] * dt
                d_pl = (Qd + Qi) * v['wz'] * dt - Qd * v['pl'] * dt - Qe * v['pl'] * dt
                v['wz'] += d_wz / V_WZ
                v['pl'] += d_pl / ENG['plenum_volume_m3']
        self.escape_rate = (L['proc_escaped'] - esc0) / h if h > 0 else 0.0     # process aerosol leaving the front (direct escape + exchange), per s

    def _sash_arrived(self):
        st = self._sash_state()
        self.meters['sash_cycles'] += 1
        if st == 'safe':
            if not self.lamp and self.powered and self.fan != 'standby':
                self.lamp = True                                     # light switched on as a signal at the working height (not in standby: lights off)
            if self.settings['mode'] == 'quickstart' and self.powered and self.fan == 'off':
                self._start_fan()
        elif st == 'closed':
            self.lamp = False
            if self.settings['mode'] == 'quickstart' and self.fan in ('on', 'warm_up', 'standby'):
                self._stop_fan()
        elif st == 'full_open':
            if self.powered and self.fan != 'standby':
                self.lamp = True                                     # light on to facilitate cleaning
        elif not self.maintenance:                                   # maintenance: the window moves without the light switching off (5.3.1)
            self.lamp = False                                        # stopped mid-way: lights off
        self._event('operation', 'SASH_' + st.upper(), f'窗停在{self.h * 1000:.0f} mm（{st}）')

    def _start_fan(self):
        if self.maintenance:                                             # maintenance mode: fan without warm-up (manual 7 table)
            self.fan, self.fan_timer = 'on', 0.0
            self._event('operation', 'FAN_ON', '风机开启（维护模式，无预热）')
            return
        self.fan = 'warm_up'
        self.fan_timer = self.settings['warm_up_s']
        self._event('operation', 'FAN_ON', '风机开启，开始预热')

    def _stop_fan(self):
        if self.settings['post_purge_s'] > 0 and not self.maintenance:
            self.fan, self.fan_timer = 'post_purge', self.settings['post_purge_s']
            self._event('operation', 'POST_PURGE', '风机关闭，后吹扫')
        else:
            self.fan, self.fan_timer = 'off', 0.0
            self._event('operation', 'FAN_OFF', '风机关闭')

    def _monitor_enabled(self):
        a = self.plant['ambient_k']
        lo, hi = DOC['airflow_monitor_ambient_k']
        return self.settings['airflow_monitor'] and lo <= a <= hi

    def _shows_airflow(self):
        """Velocities on the LCD: with the monitor on; outside 18-30 C only when AIRFLOW OUT TEMP is set (the alarm stays off)."""
        a = self.plant['ambient_k']
        lo, hi = DOC['airflow_monitor_ambient_k']
        return self.settings['airflow_monitor'] and (lo <= a <= hi or self.disp['airflow_out_temp'])

    def _display(self, which):
        if not self.powered or self.fan in ('off', 'warm_up', 'standby') or not self._shows_airflow():
            return None
        if which == 'in':
            v = self.Q['exh'] / (W_INT * H_SAFE) * self.cal_gain      # exhaust sensor calibrated at the safe height (field calibration gain)
            if 'airflow_sensor_uncalibrated' in self.faults:
                v *= 1.25
            ch = 1
        else:
            v = self.Q['down'] / A_DOWN
            ch = 2
        v += ENG['velocity_display_noise_m_s'] * _gauss(self.seed, self.tick, ch)
        return round(max(v, 0.0), 2)

    def _airflow_status(self):
        if not self.powered or self.fan in ('off',):
            return 'off'
        if self.fan == 'warm_up':
            return 'warm_up'
        if self.fan == 'standby':
            return 'standby'
        if self.fan in ('on', 'post_purge') and self._sash_state() == 'closed':
            return 'sash_closed'                                     # monitor calibrated at the safe height; closed sash is not an airflow failure
        if not self._monitor_enabled():
            return 'disabled'
        if 'airflow_sensor_uncalibrated' in self.faults:
            return 'fail'
        v = self._display('in')
        return 'fail' if v is not None and v < V_FAIL else 'ok'

    def _update_warnings(self):
        W = {}
        if self.plant is None or not self.powered or self.maintenance or self.menu['open']:   # maintenance: alarms off; menu: not monitoring (2.1)
            self.warnings = W
            return
        st = self._sash_state()
        working = self.fan in ('on', 'warm_up')
        if st == 'error':
            W['SASH_ERROR'] = '窗位检测系统故障（SASH: ERROR POSITION）'
        elif working and st in ('full_open', 'intermediate'):
            W['SASH_ALARM'] = '窗不在正常工作高度'
        if self.fan == 'on' and self._airflow_status() == 'fail':
            W['AIRFLOW_FAIL'] = 'AIRFLOW: NO! 入流低于失效点'
        if 'airflow_sensor_uncalibrated' in self.faults:
            W['SENSOR_UNCALIBRATED'] = '风速传感器未标定'
        if self.meters['filter_h'] >= DOC['filter_life_h']:
            W['FILTER_LIFE'] = '过滤器寿命计时到（10000 h）'
        if self.meters['uv_h'] >= DOC['uv_lamp_life_h']:
            W['UV_LAMP_LIFE'] = 'UV灯寿命计时到（2000 h）'
        sc = self.meters['sash_cycles']
        for n, code in zip(DOC['sash_cycle_warnings'], ('REPLACE_SASH_MOTOR', 'STOP_USING_SASH', 'SASH_MOTOR_LOCKED')):
            if sc >= n:
                W.pop('REPLACE_SASH_MOTOR', None)
                W.pop('STOP_USING_SASH', None)
                W[code] = f'窗电机循环{sc}次'
        if self.disp['date_certified'] is not None and self._clock_text()[:10] >= self._expiry():
            W['RECERTIFICATION'] = '认证已过期：请联系服务重新认证（Call Service for re-certification）'
        self.warnings = W                                            # a blown socket fuse is not a panel alarm (manual alarm list): socket.on false + event

    def _audible(self):
        if self.menu['beep_until'] is not None and self.t < self.menu['beep_until']:
            return True                                              # menu entry beep
        loud = {'SASH_ALARM', 'AIRFLOW_FAIL', 'SASH_ERROR'} & set(self.warnings)
        return bool(loud) and not (self.muted_until is not None and self.t < self.muted_until)

    # ---- the Sentinel Gold menu (manual 2.2): PIN session, clock, display settings
    def _clock_s(self):
        return EPOCH_S + self.t + self.clock_offset

    def _clock_text(self):
        import datetime
        return datetime.datetime.fromtimestamp(math.floor(self._clock_s()), tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')

    def _expiry(self):
        import datetime
        d = datetime.date.fromisoformat(self.disp['date_certified'])
        return (d + datetime.timedelta(days=365)).isoformat()

    def _menu_state(self):
        return dict(open=self.menu['open'], level=self.menu['level'], maintenance=self.maintenance, fan_pin_set=self.pins['fan'] != '0000',
                    calibration_gain=self.cal_gain, **self.disp)

    def _need_admin(self):
        self._need_power()
        if not (self.menu['open'] and self.menu['level'] == 'admin'):
            raise ActionRejected('ADMIN_PIN_REQUIRED')

    def _level(self, pin):
        if pin is None:
            raise ActionRejected('WRONG_PIN')
        if pin == self.pins['admin']:
            return 'admin'
        if pin == self.pins['fan']:
            return 'fan'
        raise ActionRejected('WRONG_PIN')

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'datalog.read', 'panel.capture', 'panel.frame'):
            self.revision += 1
        if capability_id != 'sim.step':
            self._update_warnings()
        return out

    def _cmd_plant_configure(self, ambient_k, pressure_pa, room_particles_m3, room_dust_kg_m3, cross_draft_m_s, noise_seed, downflow_filter_dust_kg=None,
                             exhaust_filter_dust_kg=None, filter_hours_h=None, uv_hours_h=None, sash_cycles=None):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(ambient_k=ambient_k, pressure_pa=pressure_pa, room_particles_m3=room_particles_m3, room_dust_kg_m3=room_dust_kg_m3, cross_draft_m_s=cross_draft_m_s)
        self.seed = noise_seed
        if first:
            self.c['wz_room'] = self.c['pl_room'] = room_particles_m3            # cabinet idle in the room air
            self.T_cab = ambient_k
            for b, v in (('down', downflow_filter_dust_kg), ('exh', exhaust_filter_dust_kg)):
                if v is not None:
                    self.dust[b] = v
            if filter_hours_h is not None:
                self.meters['filter_h'] = self.meters['blower_h'] = filter_hours_h
            if uv_hours_h is not None:
                self.meters['uv_h'] = uv_hours_h
            if sash_cycles is not None:
                self.meters['sash_cycles'] = sash_cycles
            self.led['initial_room_particles'] = room_particles_m3 * (V_WZ + ENG['plenum_volume_m3'])
        self._event('system', 'PLANT_CONFIGURED', '房间条件已声明')
        return dict(configured=True)

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not self.powered:
            self.powered = True
            self.power_epoch += 1
            self.next_log = self.t
            self._event('system', 'POWER_ON', '钥匙开关接通')
        elif not on and self.powered:
            self.powered = False
            self.menu = dict(open=False, level=None, beep_until=None)
            self.fan, self.fan_timer = 'off', 0.0
            self.lamp = self.socket = self.uv = False
            self.uv_left = None
            self.sash_target, self.sash_stop_at, self.sash_start_at = self.h, None, None
            self._event('system', 'POWER_OFF', '钥匙开关断开：风机、照明、UV与插座停止')
            if self.fuse_blown:
                self.fuse_blown = False                              # the cartridge fuse is replaced while the cabinet is off (maintenance abstraction)
                self._event('system', 'FUSE_REPLACED', '断电期间更换插座熔断器')
        return dict(on=self.powered)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(on=not self.powered)

    def _interlock(self, what):
        """Buttons disabled during warm-up / post-purge / standby (manual 3.2, 2.6); INTERLOCK lists the codes per key; defeated in maintenance."""
        if self.maintenance:
            return
        code = {'standby': 'STANDBY', 'warm_up': 'WARMING_UP', 'post_purge': 'POST_PURGE'}.get(self.fan)
        if code in INTERLOCK[what]:
            raise ActionRejected(code)

    def _cmd_fan_set(self, state):
        self._need_power()
        self._interlock('fan')
        if state in ('on', 'standby') and self._sash_state() == 'closed' and not self.maintenance:
            raise ActionRejected('SASH_CLOSED')
        if state == 'on':
            if self.fan == 'off':
                self._start_fan()
            elif self.fan == 'standby':
                self.fan = 'on'
                if self._sash_state() in ('safe', 'full_open'):
                    self.lamp = True                                 # lights back on at the working height
                self._event('operation', 'STANDBY_OFF', '退出待机')
        elif state == 'standby':
            if self.fan != 'on':
                raise ActionRejected('FAN_NOT_RUNNING')
            self.fan = 'standby'
            self.lamp = False                                        # standby: reduced blower speed, lights off (60 % power saving)
            self._event('operation', 'STANDBY_ON', '待机（降速、照明关闭）')
        else:
            if self.fan in ('on', 'warm_up', 'standby'):
                self._stop_fan()
        return dict(state=self.fan)

    def _cmd_fan_toggle(self):
        """FAN key: off -> on (warm-up), warm-up / on / standby -> off (post-purge); never enters standby."""
        self._need_power()
        self._interlock('fan')
        if self.fan == 'off':
            if self._sash_state() == 'closed' and not self.maintenance:     # maintenance: fan and light also with the sash closed
                raise ActionRejected('SASH_CLOSED')
            self._start_fan()
        else:
            self._stop_fan()
        return dict(state=self.fan)

    def _cmd_lamp_toggle(self):
        self._need_power()
        self._interlock('lamp')
        if not self.lamp and self._sash_state() == 'closed' and not self.maintenance:
            raise ActionRejected('SASH_CLOSED')
        self.lamp = not self.lamp
        return dict(on=self.lamp)

    def _cmd_socket_toggle(self):
        self._need_power()
        self._interlock('socket')
        if self.fuse_blown:
            raise ActionRejected('FUSE_BLOWN')
        self.socket = not self.socket
        return dict(on=self.socket)

    def _cmd_uv_toggle(self):
        self._need_power()
        self._interlock('uv')
        if not self.uv:
            if self.maintenance:
                raise ActionRejected('MAINTENANCE_MODE')                   # UV interlocked in maintenance mode (manual 7 table)
            if self._sash_state() != 'closed':
                raise ActionRejected('SASH_NOT_CLOSED')
            if self.fan != 'off':
                raise ActionRejected('FAN_RUNNING')
            self.uv = True
            self.uv_left = self.settings['uv_timer_s'] if self.settings['uv_timer_s'] > 0 else None
            self._event('operation', 'UV_ON', 'UV灯开启')
        else:
            self.uv, self.uv_left = False, None
            self._event('operation', 'UV_OFF', 'UV灯关闭')
        return dict(on=self.uv)

    def _cmd_sash_move(self, direction, hold_s=None):
        self._need_power()
        self._interlock('sash')
        if 'sash_sensor_error' in self.faults:
            raise ActionRejected('SASH_SENSOR_ERROR')
        if self.meters['sash_cycles'] >= DOC['sash_cycle_warnings'][2]:
            raise ActionRejected('SASH_MOTOR_LOCKED')
        tol = ENG['sash_switch_tolerance_m']
        stops = [0.0, H_SAFE, H_MAX]
        if direction == 'up':
            nxt = [s for s in stops if s > self.h + tol]
            if not nxt:
                raise ActionRejected('SASH_AT_LIMIT')
            target = nxt[0]
            if self.uv:
                self.uv, self.uv_left = False, None                       # interlock: lifting the sash switches the UV lamp off
                self._event('operation', 'UV_OFF', '抬窗：UV灯联锁关闭')
        else:
            nxt = [s for s in stops if s < self.h - tol]
            if not nxt:
                raise ActionRejected('SASH_AT_LIMIT')
            target = nxt[-1]
        self.sash_target = target
        self.sash_stop_at = self.t + hold_s if hold_s is not None else None
        self.sash_start_at = self.t
        self.sash_moving_from = self.h
        return dict(target_m=target, moving=True)

    def _cmd_alarm_mute(self):
        self._need_power()
        self._interlock('mute')
        if not ({'SASH_ALARM', 'AIRFLOW_FAIL', 'SASH_ERROR'} & set(self.warnings)):
            raise ActionRejected('NO_ALARM')
        self.muted_until = self.t + self.settings['mute_s']
        return dict(muted_until_s=self.muted_until)

    def _cmd_menu_enter(self, pin=None):
        self._need_power()
        self._interlock('menu')                                                  # standby: only the FAN key; post-purge: keys disabled
        level = 'admin' if self.maintenance and pin is None else self._level(pin)     # maintenance mode: menu without PIN (manual 7, table)
        if self.fan == 'warm_up' and level != 'admin':
            raise ActionRejected('ADMIN_PIN_REQUIRED')                     # during warm-up only the ADMIN PIN opens the menu (2.2.1.5)
        self.menu = dict(open=True, level=level, beep_until=self.t + MENU_BEEP_S)
        self._event('operation', 'MENU_ENTER', f'进入菜单（{level}）：监测暂停' + ('，预热暂停' if self.fan == 'warm_up' else ''))
        return dict(level=level)

    def _cmd_menu_exit(self):
        self._need_power()
        if self.menu['open']:
            self._event('operation', 'MENU_EXIT', '退出菜单')
        self.menu = dict(open=False, level=None, beep_until=None)
        return dict(open=False)

    def _cmd_pin_check(self, pin):
        self._need_power()
        return dict(level=self._level(pin))

    def _cmd_pin_set(self, which, pin):
        self._need_admin()
        self.pins[which] = pin
        self._event('operation', 'PIN_SET', f'新的{which.upper()} PIN已设定')
        return dict(which=which, pin_set=pin != '0000')

    def _cmd_mode_set(self, mode):
        self._need_power()
        if not self.menu['open']:
            raise ActionRejected('MENU_CLOSED')
        if mode == 'maintenance':
            if self.menu['level'] != 'admin':
                raise ActionRejected('ADMIN_PIN_REQUIRED')
            self.maintenance = True
            self._event('operation', 'MAINTENANCE_MODE', '维护模式：报警停用、联锁解除')
        else:
            self.maintenance = False
            self.settings['mode'] = mode
            self._event('operation', 'MODE_' + mode.upper(), f'{mode}模式')
        return dict(mode=mode, maintenance=self.maintenance)

    def _cmd_menu_configure(self, unit=None, filter_life_display=None, language=None, airflow_out_temp=None, date_certified=None):
        self._need_power()
        if any(v is not None for v in (filter_life_display, language, airflow_out_temp, date_certified)):
            self._need_admin()                                            # admin settings (2.2.4); the unit is under SETTINGS
        if date_certified is not None:
            import datetime
            try:
                datetime.date.fromisoformat(date_certified)
            except ValueError:
                raise ActionRejected('INVALID_DATE') from None
            if not '2000-01-01' <= date_certified <= '2099-12-31':
                raise ActionRejected('INVALID_DATE')
        for k, v in dict(unit=unit, filter_life_display=filter_life_display, language=language, airflow_out_temp=airflow_out_temp, date_certified=date_certified).items():
            if v is not None:
                self.disp[k] = v
        return dict(settings=self._menu_state())

    def _cmd_clock_set(self, date=None, time=None):
        self._need_power()
        import datetime
        cur = self._clock_text()
        try:
            d = datetime.datetime.strptime(f'{date or cur[:10]} {time or cur[11:16]}', '%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            raise ActionRejected('INVALID_DATE') from None
        self.clock_offset = d.timestamp() - (EPOCH_S + self.t) + (0 if time else float(cur[17:19]))
        self._event('operation', 'CLOCK_SET', f'时钟设为{self._clock_text()}')
        return dict(clock=self._clock_text())

    def _cmd_meters_reset(self, meter):
        self._need_admin()
        self.meters[dict(blower='blower_h', uv='uv_h', filter='filter_h')[meter]] = 0.0
        self._event('maintenance', 'METER_RESET', f'{meter}小时计清零')
        return dict(meters=dict(self.meters))

    def _cmd_sash_set_cycles(self, count):
        self._need_admin()
        self.meters['sash_cycles'] = int(count)
        return dict(sash_cycles=self.meters['sash_cycles'])

    def _cmd_settings_reset_default(self):
        self._need_admin()
        self.settings.update(warm_up_s=DOC['warm_up_s']['default'], post_purge_s=DOC['post_purge_s']['default'], uv_timer_s=DOC['uv_timer_s']['default'],
                             airflow_monitor=True)
        self.disp.update(unit='metric', filter_life_display=False)
        self.pins = dict(PIN_DEFAULT)
        self._event('operation', 'RESET_DEFAULT', '恢复出厂设置（标定与小时计不变）')
        return dict(settings=dict(self.settings, **self._menu_state()))

    def _cmd_maintenance_field_calibration(self, action, measured_inflow_m_s=None):
        self._need_admin()
        if action == 'reset':
            self.cal_gain = 1.0
            self._event('maintenance', 'CALIBRATION_RESET', '恢复工厂标定')
            return dict(calibrated=False, display_inflow_m_s=self._display('in'))
        if self.fan != 'on' or self._sash_state() != 'safe' or measured_inflow_m_s is None:
            raise ActionRejected('NOT_CALIBRATABLE')                      # fan running, sash at the working height, a reference reading
        raw = self.Q['exh'] / (W_INT * H_SAFE)
        gain = measured_inflow_m_s / raw if raw > 1e-6 else 0.0
        if not 0.7 <= gain <= 1.3:
            raise ActionRejected('CALIBRATION_ERROR')                     # the sensor cannot be brought to the reference (manual: CALIBRATION ERROR)
        self.cal_gain = gain
        self.faults.discard('airflow_sensor_uncalibrated')                # the field calibration removes SENSOR UNCALIBRATED
        self._event('maintenance', 'FIELD_CALIBRATION', f'现场标定：参考入流{measured_inflow_m_s:.2f} m/s')
        return dict(calibrated=True, display_inflow_m_s=self._display('in'))

    def _cmd_settings_configure(self, **kw):
        self._need_power()
        admin_menu = self.menu['open'] and self.menu['level'] == 'admin'
        if self.fan == 'warm_up' and admin_menu and kw.get('warm_up_s') is None:
            pass                                                         # the ADMIN PIN menu works during warm-up, except WARM UP (2.2.1.5)
        elif self.fan in ('warm_up', 'post_purge', 'standby'):
            raise ActionRejected({'warm_up': 'WARMING_UP', 'post_purge': 'POST_PURGE', 'standby': 'STANDBY'}[self.fan])
        self.settings.update({k: v for k, v in kw.items() if v is not None})
        return dict(settings=dict(self.settings))

    def _cmd_item_place(self, item_id, kind, x_m, y_m, footprint_m2, liquid=None, liquid_mass_kg=None, surface_area_m2=None, aerosol_rate_per_s=None,
                        viable_fraction=None, power_w=None):
        self._need_plant()
        if self.h < H_SAFE - ENG['sash_switch_tolerance_m']:
            raise ActionRejected('SASH_CLOSED')
        if item_id in self.items:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.items) >= MAX_ITEMS:
            raise ActionRejected('TOO_MANY_ITEMS')
        if kind == 'open_vessel' and (liquid is None or liquid_mass_kg is None or surface_area_m2 is None):
            raise ActionRejected('MISSING_SETTING')
        if kind == 'aerosol_source' and aerosol_rate_per_s is None:
            raise ActionRejected('MISSING_SETTING')
        if kind == 'equipment' and power_w is None:
            raise ActionRejected('MISSING_SETTING')
        if liquid is not None and liquid not in liquids():
            raise ActionRejected('UNKNOWN_LIQUID')
        if sum(it['footprint_m2'] for it in self.items.values()) + footprint_m2 > A_TRAY:
            raise ActionRejected('NO_SPACE')
        self.items[item_id] = dict(kind=kind, x_m=x_m, y_m=y_m, footprint_m2=footprint_m2, liquid=liquid, liquid_mass_kg=float(liquid_mass_kg or 0.0),
                                   liquid_mass0_kg=float(liquid_mass_kg or 0.0), surface_area_m2=surface_area_m2, aerosol_rate_per_s=aerosol_rate_per_s,
                                   viable_fraction=viable_fraction if viable_fraction is not None else (0.0 if kind != 'aerosol_source' else 0.1), power_w=power_w,
                                   evaporated_kg=0.0, last_rate_kg_s=0.0, placed_s=self.t)
        if kind == 'open_vessel':
            self._placed[liquid] = self._placed.get(liquid, 0.0) + float(liquid_mass_kg)
        notes = []
        if x_m < ENG['grille_zone_depth_m']:
            notes.append('物品距前格栅不足150 mm：挡住入流格栅、削弱气幕（手册要求至少在前格栅后150 mm操作）')
        if kind == 'open_vessel':
            bp = liquids()[liquid].get('boiling_point_K')
            if bp and bp < 373.15:
                notes.append(f'{liquid}为挥发性溶剂：生物安全柜过滤器不截留蒸气，约2/3空气在柜内循环，排气回到室内（不是化学通风柜）')
            if liquids()[liquid]['antoine_fit']['quality'] != 'consistent':
                notes.append(f'{liquid}的蒸气压数据不一致或温区窄（库标记inconsistent）：蒸发速率不确定度大')
        if kind == 'equipment' and (power_w or 0.0) > SOCKET_A * MAINS_V:
            notes.append('设备功率超过柜内插座5 A额定值：接通时熔断')
        self._event('item', 'ITEM_PLACED', f'放入{item_id}（{kind}）')
        return dict(item_id=item_id, notes=notes)

    def _cmd_item_remove(self, item_id):
        self._need_plant()
        if self.h < H_SAFE - ENG['sash_switch_tolerance_m']:
            raise ActionRejected('SASH_CLOSED')
        if item_id not in self.items:
            raise ActionRejected('UNKNOWN_ITEM')
        it = self.items.pop(item_id)
        if it['kind'] == 'open_vessel':
            self.led['vapour'].setdefault(it['liquid'], dict(evaporated_kg=0.0, escaped_front_kg=0.0, exhausted_to_room_kg=0.0))
            self.led.setdefault('liquid_removed_kg', {})
            self.led['liquid_removed_kg'][it['liquid']] = self.led['liquid_removed_kg'].get(it['liquid'], 0.0) + it['liquid_mass_kg']
        self._event('item', 'ITEM_REMOVED', f'取出{item_id}')
        return dict(item_id=item_id, state=dict(kind=it['kind'], liquid_mass_kg=it['liquid_mass_kg'] if it['kind'] == 'open_vessel' else None))

    def _cmd_operator_set(self, arms_in, arm_speed_m_s=None):
        self._need_plant()
        if arms_in and self.h < H_SAFE - ENG['sash_switch_tolerance_m']:
            raise ActionRejected('SASH_CLOSED')
        self.arms_in = bool(arms_in)
        self.arm_speed = float(arm_speed_m_s or 0.0) if arms_in else 0.0
        return dict(arms_in=self.arms_in, arm_speed_m_s=self.arm_speed)

    def _cmd_datalog_read(self, last=None):
        self._need_power()
        recs = self.log[-(last or MAX_LOG):]
        return dict(records=copy.deepcopy(recs), period_s=self.settings['data_log_period_s'])

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s - 1e-9], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        v = self.read('evaluator')
        return dict(airflow=v['truth.airflow'], containment=v['truth.containment'], workzone=v['truth.workzone'], items=v['truth.items'], faults=v['truth.faults'])

    def _ledger(self):
        L = copy.deepcopy(self.led)
        Vp = ENG['plenum_volume_m3']
        c = self.c
        L['room_in_air'] = c['wz_room'] * V_WZ + c['pl_room'] * Vp
        L['proc_in_air'] = c['wz_proc'] * V_WZ + c['pl_proc'] * Vp
        L['viable_in_air'] = c['wz_via'] * V_WZ + c['pl_via'] * Vp
        L['viable_on_tray'] = self.surface_viable
        for sp, v in self.vap.items():
            L['vapour'].setdefault(sp, dict(evaporated_kg=0.0, escaped_front_kg=0.0, exhausted_to_room_kg=0.0))['in_air_kg'] = v['wz'] * V_WZ + v['pl'] * Vp
        L['liquid_in_vessels_kg'] = {}
        for it in self.items.values():
            if it['kind'] == 'open_vessel':
                L['liquid_in_vessels_kg'][it['liquid']] = L['liquid_in_vessels_kg'].get(it['liquid'], 0.0) + it['liquid_mass_kg']
        L['liquid_placed_kg'] = self._placed
        L['dust_on_filters_kg'] = dict(self.dust)
        L['electrical_energy_j'] = self.energy_j
        return L

    @property
    def _placed(self):
        return self.__dict__.setdefault('_placed_kg', {})

    def _cmd_ledger_read(self):
        return dict(ledger=self._ledger())

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        if fault == 'filter_clogged' and not active and fault in self.faults and self._dust_before_clog is not None:
            for b in ('down', 'exh'):                                # clearing removes only the injected dust (loading accumulated since stays)
                self.dust[b] = max(self._dust_before_clog[b] + (self.dust[b] - self._dust_at_clog[b]), 0.0)
            self._dust_before_clog = None
        if fault == 'filter_clogged' and active and fault not in self.faults:
            self._dust_before_clog = dict(self.dust)
        (self.faults.add if active else self.faults.discard)(fault)
        if fault == 'filter_clogged' and active:
            self.dust['down'] = max(self.dust['down'], 4.0 * ENG['dust_doubling_down_kg'])     # well beyond the 10 000 h loading (about 1.5 x M2)
            self.dust['exh'] = max(self.dust['exh'], 4.0 * ENG['dust_doubling_exh_kg'])
            self._dust_at_clog = dict(self.dust)
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}：{"注入" if active else "清除"}')
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What the panel sees: the agent slots plus the key switch, the power-up count and the LCD clock as numbers."""
        out = self.read('agent')
        c = self._clock_text()
        out.update({'host.powered': self.powered and self.plant is not None, 'host.power_epoch': self.power_epoch,
                    'host.clock': dict(year=int(c[0:4]), month=int(c[5:7]), day=int(c[8:10]), hour=int(c[11:13]), minute=int(c[14:16]))})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel action calls the same command a direct call would (these commands check before they change anything)."""
        try:
            validate(CAPS[capability_id]['input_schema'], arguments)
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

    def _cmd_panel_press(self, input_id, duration_s=0.1):
        return self._panel(self.panel.press, input_id, float(duration_s))

    def _cmd_panel_switch(self, input_id, position):
        return self._panel(self.panel.switch, input_id, position)

    def _cmd_panel_capture(self, display_id):
        """Close-up camera of a display: a PNG file named by its content (large data is returned as a file reference)."""
        self._need_plant()
        data, w, h = self.panel.png_bytes(display_id)
        sha = hashlib.sha256(data).hexdigest()
        d = Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f'{display_id}_{sha[:16]}.png'
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.t, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))

    def _cmd_panel_frame(self):
        return dict(frame=self.panel.frame())

    # ---- read
    def read(self, visibility='agent'):
        pw = self.powered and self.plant is not None
        out = {'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.on': self.powered, 'fan.state': self.fan,
               'fan.remaining_s': self.fan_timer if self.fan in ('warm_up', 'post_purge') else None, 'sash.opening_m': self.h}
        out['sash.status'] = self._sash_state()
        out['lamp.on'] = self.lamp
        out['socket.on'] = self.socket
        out['uv.on'] = self.uv
        out['uv.remaining_s'] = self.uv_left if self.uv else None
        out['display.inflow_m_s'] = self._display('in') if pw else None
        out['display.downflow_m_s'] = self._display('down') if pw else None
        out['airflow.status'] = self._airflow_status() if pw else 'off'
        out['cabinet.temperature_k'] = round(self.T_cab, 2) if (pw and self.T_cab) else None
        out['alarms.active'] = sorted(self.warnings)
        out['alarm.audible'] = self._audible() if pw else False
        out['indicator.fan'] = pw and self.fan != 'off'
        out['indicator.light'] = pw and (self.lamp or self.socket)
        out['indicator.uv'] = pw and self.uv
        out['indicator.alarm'] = pw and bool(self.warnings)
        out['meters'] = dict(blower_h=self.meters['blower_h'], filter_h=self.meters['filter_h'],
                             filter_life_fraction=max(0.0, 1.0 - self.meters['filter_h'] / DOC['filter_life_h']), uv_h=self.meters['uv_h'],
                             uv_life_fraction=max(0.0, 1.0 - self.meters['uv_h'] / DOC['uv_lamp_life_h']), sash_cycles=self.meters['sash_cycles'])
        out['settings.current'] = dict(self.settings)
        out['menu.state'] = self._menu_state()
        out['device.clock'] = self._clock_text()
        out['operator.arms_in'] = self.arms_in
        out['items.present'] = sorted(self.items)
        if visibility != 'agent':
            prot = self._protection() if self.plant else dict(v_in=0.0, v_down=0.0, v_eff=0.0, apf_log10=0.0, ppf_log10=0.0)
            out['truth.airflow'] = dict(downflow_m3_s=self.Q['down'], exhaust_m3_s=self.Q['exh'], inflow_m3_s=self.Q['exh'], duty_down=self.u['down'], duty_exh=self.u['exh'],
                                        filter_dp_down_pa=(self._branch('down')[0] * self.Q['down']), filter_dp_exh_pa=(self._branch('exh')[0] * self.Q['exh']),
                                        dust_down_kg=self.dust['down'], dust_exh_kg=self.dust['exh'], inflow_velocity_m_s=prot['v_in'], downflow_velocity_m_s=prot['v_down'],
                                        electrical_power_w=self.P_el, compensating=max(self.u.values()) < 0.999)
            S_proc = sum(it.get('aerosol_rate_per_s') or 0.0 for it in self.items.values() if it['kind'] == 'aerosol_source')
            out['truth.containment'] = dict(effective_inflow_m_s=prot['v_eff'], apf_log10=prot['apf_log10'], ppf_log10=prot['ppf_log10'],
                                            operator_protected=prot['apf_log10'] >= 5.0, product_protected=prot['ppf_log10'] >= 4.0,
                                            aerosol_escape_rate_per_s=self.escape_rate, grille_blocked_fraction=self._block_fraction())
            T = self.T_cab or (self.plant['ambient_k'] if self.plant else 295.15)
            Pp = self.plant['pressure_pa'] if self.plant else 101325.0
            vap = {sp: dict(workzone_kg_m3=v['wz'], plenum_kg_m3=v['pl'], workzone_ppm=v['wz'] * R_GAS * T / (molar_mass(sp) * Pp) * 1e6) for sp, v in self.vap.items()}
            conds = []
            if any(x['workzone_kg_m3'] > 0 for x in vap.values()):
                conds.append('VOLATILE_CHEMICAL_RECIRCULATED')
            out['truth.workzone'] = dict(room_particles_workzone_m3=self.c['wz_room'], room_particles_plenum_m3=self.c['pl_room'],
                                         iso_class_workzone=iso_class(self.c['wz_room'] + self.c['wz_proc']), process_aerosol_workzone_m3=self.c['wz_proc'],
                                         viable_workzone_m3=self.c['wz_via'], viable_on_tray_per_m2=self.surface_viable / A_TRAY, vapours=vap, conditions=conds)
            out['truth.items'] = {k: dict(kind=it['kind'], liquid=it['liquid'], liquid_mass_kg=it['liquid_mass_kg'], evaporated_kg=it['evaporated_kg'],
                                          evaporation_rate_kg_s=it['last_rate_kg_s']) for k, it in self.items.items()}
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
class EscoAc24s8Session:
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
        meta = copy.deepcopy(export_esco_ac24s8_contract_metadata())
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
    'PLANT_REQUIRED': '尚未声明房间条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '钥匙开关未接通',
    'STANDBY': '待机模式下只有风机键可用', 'WARMING_UP': '预热中：只有风机键与照明键可用', 'POST_PURGE': '后吹扫中：按键停用（只能移动窗）',
    'SASH_CLOSED': '窗全关（或低于安全高度）：风机与照明不能开、不能放取物品或伸手', 'SASH_NOT_CLOSED': 'UV灯只在窗完全关闭时可开', 'FAN_RUNNING': '风机或后吹扫进行中：UV灯不能开',
    'FAN_NOT_RUNNING': '风机未在工作状态', 'FUSE_BLOWN': '插座熔断器已熔断', 'SASH_AT_LIMIT': '窗已在该方向的终点', 'SASH_SENSOR_ERROR': '窗位检测故障：电动窗停用',
    'SASH_MOTOR_LOCKED': '窗电机循环达到16000次：锁定', 'NO_ALARM': '没有可静音的报警', 'ITEM_EXISTS': '物品标识已存在', 'TOO_MANY_ITEMS': '物品数量超过上限',
    'MISSING_SETTING': '缺少必需参数（开口容器须给液体、质量与液面面积；气溶胶源须给发生速率；设备须给功率）', 'UNKNOWN_LIQUID': '液体不在液体库中',
    'NO_SPACE': '台面放不下（物品占地合计超过0.56 m²）', 'UNKNOWN_ITEM': '物品不存在',
    'WRONG_PIN': 'PIN不对', 'ADMIN_PIN_REQUIRED': '需要用ADMIN PIN进入菜单（预热中、维护模式、现场标定与管理员设置）', 'MENU_CLOSED': '菜单未打开（先menu.enter）',
    'INVALID_DATE': '日期或时间无效（认证年份2000–2099）', 'NOT_CALIBRATABLE': '标定需风机工作、窗在安全工作高度并给出参考入流',
    'CALIBRATION_ERROR': '标定失败：参考值与传感器偏差过大（CALIBRATION ERROR）', 'MAINTENANCE_MODE': '维护模式中UV灯联锁',
}
_P = ['PLANT_REQUIRED']
_PW = _P + ['POWER_OFF']
_IL = ['STANDBY', 'WARMING_UP', 'POST_PURGE']                  # panel interlocks; each key lists only the ones that can refuse it (_interlock)
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': _P, 'power.toggle': _P,
    'fan.set': _PW + INTERLOCK['fan'] + ['SASH_CLOSED', 'FAN_NOT_RUNNING'], 'fan.toggle': _PW + INTERLOCK['fan'] + ['SASH_CLOSED'],
    'lamp.toggle': _PW + INTERLOCK['lamp'] + ['SASH_CLOSED'], 'socket.toggle': _PW + INTERLOCK['socket'] + ['FUSE_BLOWN'],
    'uv.toggle': _PW + INTERLOCK['uv'] + ['SASH_NOT_CLOSED', 'FAN_RUNNING', 'MAINTENANCE_MODE'],
    'sash.move': _PW + INTERLOCK['sash'] + ['SASH_SENSOR_ERROR', 'SASH_MOTOR_LOCKED', 'SASH_AT_LIMIT'], 'alarm.mute': _PW + INTERLOCK['mute'] + ['NO_ALARM'],
    'settings.configure': _PW + _IL,
    'item.place': _P + ['SASH_CLOSED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'MISSING_SETTING', 'UNKNOWN_LIQUID', 'NO_SPACE'], 'item.remove': _P + ['SASH_CLOSED', 'UNKNOWN_ITEM'],
    'operator.set': _P + ['SASH_CLOSED'], 'datalog.read': _PW, 'events.read': [], 'truth.inspect': [], 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
    'menu.enter': _PW + INTERLOCK['menu'] + ['WRONG_PIN', 'ADMIN_PIN_REQUIRED'], 'menu.exit': _PW, 'pin.check': _PW + ['WRONG_PIN'], 'pin.set': _PW + ['ADMIN_PIN_REQUIRED'],
    'mode.set': _PW + ['MENU_CLOSED', 'ADMIN_PIN_REQUIRED'], 'menu.configure': _PW + ['ADMIN_PIN_REQUIRED', 'INVALID_DATE'], 'clock.set': _PW + ['INVALID_DATE'],
    'meters.reset': _PW + ['ADMIN_PIN_REQUIRED'], 'sash.set_cycles': _PW + ['ADMIN_PIN_REQUIRED'], 'settings.reset_default': _PW + ['ADMIN_PIN_REQUIRED'],
    'maintenance.field_calibration': _PW + ['ADMIN_PIN_REQUIRED', 'NOT_CALIBRATABLE', 'CALIBRATION_ERROR'],
    **{k: ([] if k == 'panel.frame' else _P) for k in PANEL_CAPS},
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子与过滤器/计时器初值；之后再声明须给同一种子（初值只在首次生效）', ['plant.configured']),
    'power.set': ('断电时风机、照明、UV、插座全部停止，窗停在原位', ['power.on']), 'power.toggle': ('同power.set', ['power.on']),
    'fan.set': ('已通电；待机时只能用风机键，预热中只能关风机，后吹扫中不可；开风机或待机时窗不能全关；待机只能从工作状态进入；维护模式中无预热与后吹扫、窗全关时也可开风机', ['power.on', 'fan.state', 'sash.status']),
    'fan.toggle': ('同fan.set（关→开、开/待机→关）', ['power.on', 'fan.state', 'sash.status']),
    'lamp.toggle': ('已通电；不在待机或后吹扫；窗全关时不能开灯（维护模式中可以）', ['power.on', 'fan.state', 'sash.status']),
    'socket.toggle': ('已通电；不在待机、预热或后吹扫；熔断器未熔断（熔断后断电一次即视为更换熔断器）', ['power.on', 'fan.state', 'socket.on']),
    'uv.toggle': ('已通电；不在待机、预热或后吹扫；开UV需窗全关且风机停（后吹扫结束）；维护模式中UV联锁', ['power.on', 'fan.state', 'sash.status']),
    'sash.move': ('已通电；不在待机或预热；窗位传感器正常；窗电机未锁定；该方向还有停位', ['power.on', 'fan.state', 'sash.status', 'meters']),
    'alarm.mute': ('已通电；不在待机（待机时只有风机键可用）；有窗位、风量或窗位传感器报警', ['power.on', 'fan.state', 'alarms.active']),
    'settings.configure': ('已通电；不在预热、待机或后吹扫中（待机时只有风机键可用）；预热中用ADMIN PIN打开菜单时可设置（预热时间除外）', ['power.on', 'fan.state', 'menu.state']),
    'item.place': ('窗至少在安全工作高度；标识唯一；开口容器给液体、质量与液面面积，气溶胶源给速率，设备给功率；台面放得下', ['sash.opening_m', 'items.present']),
    'item.remove': ('窗至少在安全工作高度；物品存在', ['sash.opening_m', 'items.present']),
    'operator.set': ('伸手时窗至少在安全工作高度', ['sash.opening_m']), 'datalog.read': ('已通电', ['power.on']), 'fault.inject': ('无', ['plant.configured']),
    'menu.enter': ('已通电；不在待机或后吹扫（维护模式除外）；PIN为FAN或ADMIN PIN（维护模式中可不给）；预热中只认ADMIN PIN', ['power.on', 'fan.state', 'menu.state']), 'menu.exit': ('已通电', ['menu.state']),
    'pin.check': ('已通电', ['power.on']), 'pin.set': ('已用ADMIN PIN进入菜单', ['menu.state']),
    'mode.set': ('菜单已打开；维护模式需ADMIN PIN', ['menu.state']), 'menu.configure': ('已通电；单位以外的项需ADMIN PIN进入菜单；认证年份2000–2099', ['menu.state']),
    'clock.set': ('已通电；日期时间有效', ['device.clock']), 'meters.reset': ('已用ADMIN PIN进入菜单', ['menu.state', 'meters']),
    'sash.set_cycles': ('已用ADMIN PIN进入菜单', ['menu.state', 'meters']), 'settings.reset_default': ('已用ADMIN PIN进入菜单', ['menu.state']),
    'maintenance.field_calibration': ('已用ADMIN PIN进入菜单；标定时风机工作、窗在安全工作高度并给出参考入流；增益0.7–1.3', ['menu.state', 'fan.state', 'sash.status']),
    **{k: ('面板按当前画面处理手势（手册第2章Sentinel Gold）：钥匙开关断开时只有钥匙开关起作用；MENU与四位PIN进入菜单，上/下移动或改值，SET确认，MENU返回；'
           '主画面上/下按住移动电动窗，SET静音或进入诊断；被调用能力的前提同样适用（被拒时显示仪器提示，手势不被接受）',
           ['power.on', 'fan.state', 'sash.status', 'menu.state', 'alarms.active'])
       for k in PANEL_CAPS if k not in ('panel.capture', 'panel.frame')},
    'panel.capture': ('无（断电时为暗屏）', ['power.on']), 'panel.frame': ('无', ['plant.configured']),
}
_EVOLVE = ['device.clock', 'menu.state', 'fan.state', 'fan.remaining_s', 'sash.opening_m', 'sash.status', 'lamp.on', 'uv.on', 'uv.remaining_s', 'display.inflow_m_s', 'display.downflow_m_s',
           'airflow.status', 'cabinet.temperature_k', 'alarms.active', 'alarm.audible', 'indicator.fan', 'indicator.light', 'indicator.uv', 'indicator.alarm', 'meters',
           'socket.on', 'truth.airflow', 'truth.containment', 'truth.workzone', 'truth.items']
_IND = ['indicator.fan', 'indicator.light', 'indicator.uv', 'indicator.alarm', 'alarm.audible', 'alarms.active']
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.workzone', 'truth.airflow', 'truth.containment', 'cabinet.temperature_k', 'meters', 'airflow.status',
                        'display.inflow_m_s', 'display.downflow_m_s'] + _IND,
    'power.set': ['power.on', 'fan.state', 'fan.remaining_s', 'lamp.on', 'socket.on', 'uv.on', 'uv.remaining_s', 'display.inflow_m_s', 'display.downflow_m_s', 'airflow.status',
                  'cabinet.temperature_k', 'sash.status'] + _IND,
    'fan.set': ['fan.state', 'fan.remaining_s', 'lamp.on', 'display.inflow_m_s', 'display.downflow_m_s', 'airflow.status'] + _IND,
    'lamp.toggle': ['lamp.on', 'indicator.light'], 'socket.toggle': ['socket.on', 'indicator.light'], 'uv.toggle': ['uv.on', 'uv.remaining_s', 'indicator.uv'],
    'sash.move': ['sash.status', 'uv.on', 'uv.remaining_s', 'indicator.uv'] + _IND, 'alarm.mute': ['alarm.audible'], 'settings.configure': ['settings.current', 'airflow.status', 'display.inflow_m_s', 'display.downflow_m_s'] + _IND,
    'item.place': ['items.present', 'truth.items', 'truth.containment'], 'item.remove': ['items.present', 'truth.items', 'truth.containment'],
    'operator.set': ['operator.arms_in', 'truth.containment'], 'fault.inject': ['truth.faults', 'sash.status', 'display.inflow_m_s', 'airflow.status', 'truth.airflow'] + _IND,
    'sim.step': ['clock.time_s'],
}
EFFECTS['power.set'] = EFFECTS['power.set'] + ['menu.state']
EFFECTS['power.toggle'] = list(EFFECTS['power.set'])
EFFECTS['fan.toggle'] = list(EFFECTS['fan.set'])
EFFECTS.update({'menu.enter': ['menu.state'] + _IND, 'menu.exit': ['menu.state'] + _IND, 'pin.check': [], 'pin.set': ['menu.state'],
                'mode.set': ['menu.state', 'settings.current'] + _IND, 'menu.configure': ['menu.state', 'display.inflow_m_s', 'display.downflow_m_s'] + _IND,
                'clock.set': ['device.clock'] + _IND, 'meters.reset': ['meters'] + _IND, 'sash.set_cycles': ['meters'] + _IND,
                'settings.reset_default': ['settings.current', 'menu.state', 'airflow.status', 'display.inflow_m_s', 'display.downflow_m_s'] + _IND,
                'maintenance.field_calibration': ['menu.state', 'display.inflow_m_s', 'airflow.status', 'truth.faults'] + _IND})
ON_STEP = {
    'sim.step': _EVOLVE + ['clock.time_s'], 'fan.set': _EVOLVE, 'fan.toggle': _EVOLVE, 'sash.move': _EVOLVE, 'power.set': _EVOLVE, 'power.toggle': _EVOLVE,
    'uv.toggle': ['uv.on', 'uv.remaining_s', 'indicator.uv', 'meters', 'truth.workzone'], 'item.place': ['truth.items', 'truth.workzone'], 'operator.set': ['truth.workzone'],
    'fault.inject': _EVOLVE, 'plant.configure': _EVOLVE, 'socket.toggle': _EVOLVE,
}
_PANEL_FX = list(dict.fromkeys(x for c in _panel_ui.spec_capabilities(PANEL_SPEC) for x in EFFECTS.get(c, [])))
_GESTURES = [k for k in PANEL_CAPS if k not in ('panel.capture', 'panel.frame')]
for _k in _GESTURES:
    EFFECTS[_k] = list(_PANEL_FX)                                           # a gesture changes what the commands it calls change
for _k in ('lamp.toggle', 'uv.toggle', 'item.place', 'item.remove', 'operator.set', 'settings.configure', 'alarm.mute', 'menu.enter', 'menu.exit', 'mode.set',
           'menu.configure', 'clock.set', 'meters.reset', 'sash.set_cycles', 'settings.reset_default', 'maintenance.field_calibration', *_GESTURES):
    ON_STEP[_k] = _EVOLVE                                      # load, temperature, evaporation, protection and alarm re-sound follow at the next step
ON_STEP_ZH = {
    'sim.step': '按0.5 s网格（空气输运0.1 s子步）积分：电动窗、预热/后吹扫计时、ECM风机自补偿与风量、过滤器积尘、气幕保护、粒子/气溶胶/蒸气输运与沉积、UV、电功率与柜温、计时器与报警、数据记录',
    'fan.set': '风机按时间常数升降速；预热/后吹扫计时', 'fan.toggle': '同fan.set', 'sash.move': '电动窗以0.04 m/s移动到停位（或按住时间到停住），到位后照明与快速启动联动',
    'power.set': '风机停止后风量衰减、空气交换转为热对流', 'power.toggle': '同power.set', 'uv.toggle': 'UV定时倒计时与台面活菌失活', 'item.place': '开口容器蒸发、气溶胶源发生',
    'operator.set': '手臂移动削弱气幕', 'fault.inject': '故障作用于风机、过滤器、窗位与风速传感器', 'plant.configure': '室内粒子与粉尘随入流进入', 'socket.toggle': '插座负载计入电功率与柜温，超过5 A时熔断',
    'lamp.toggle': '照明功率计入电功率、柜温与蒸发', 'item.remove': '物品移出后蒸发、气溶胶与挡格栅效应消失', 'settings.configure': '计时、模式与风量监测设定在之后的step中生效',
    'alarm.mute': '静音时间到后报警重新鸣响',
}
EXEC_TEXT = {
    'sash.move': ('sash.opening_m到达目标停位（或按住时间结束）后sash.status为closed/safe/full_open/intermediate', '途中断电或窗位故障时停在原位'),
    'fan.set': ('fan.state经warm_up（预热时间）后为on；关风机时经post_purge后为off', '预热中关风机直接进入后吹扫/停止'),
    **{k: ('手势瞬时完成（不推进仿真时钟）：界面状态与被调用能力的效果立即生效；结果只说明是否被仪器接受，屏幕须用panel.capture查看',
           '一个手势可依次调用多个能力，前面已提交的不回滚；被拒的能力不改变状态') for k in _GESTURES},
}
RESOURCES = {k: ['host_clock'] for k in ALL_CAPS}
for _k in ('sash.move', 'item.place', 'item.remove', 'operator.set'):
    RESOURCES[_k] = ['host_clock', 'sash_window']
OBSERVES = {'sash.move': ['sash.opening_m', 'sash.status'], 'fan.set': ['fan.state', 'airflow.status'], 'fan.toggle': ['fan.state'], 'uv.toggle': ['uv.remaining_s']}
_WB, _BR, _PR, _MN, _NI, _SIM = ('user_workbook', 'manufacturer_brochure', 'manufacturer_product_page', 'same_platform_ac2_g3_manual', 'nist_webbook', 'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({'power.set': [_BR], 'power.toggle': [_BR], 'fan.set': [_MN, _BR], 'fan.toggle': [_MN], 'lamp.toggle': [_MN, _BR], 'socket.toggle': [_MN],
                    'uv.toggle': [_MN, _PR], 'sash.move': [_MN, _BR, _PR], 'alarm.mute': [_MN], 'settings.configure': [_MN], 'item.place': [_MN, _NI, _SIM],
                    'item.remove': [_MN], 'operator.set': [_MN, _SIM], 'datalog.read': [_BR, _MN], 'fault.inject': [_MN, _SIM]})
CAP_SOURCES.update({k: [_BR, _MN] for k in UNSUPPORTED})
_MENU_FUNCTIONS = ('menu.enter', 'menu.exit', 'pin.check', 'pin.set', 'mode.set', 'menu.configure', 'clock.set', 'meters.reset', 'sash.set_cycles',
                   'settings.reset_default', 'maintenance.field_calibration')
CAP_SOURCES.update({k: [_MN, _SIM] for k in _MENU_FUNCTIONS})
CAP_SOURCES.update({k: [_MN, _BR, _SIM] for k in PANEL_CAPS})
PARAM_ZH = {'ambient_k': '环境温度', 'pressure_pa': '大气压', 'room_particles_m3': '室内≥0.5 µm粒子数浓度', 'room_dust_kg_m3': '室内粉尘质量浓度',
            'cross_draft_m_s': '前开口处的室内横向气流速度', 'noise_seed': '噪声随机数种子（只在首次声明时生效）', 'downflow_filter_dust_kg': '下行流过滤器已积尘量（维护史）',
            'exhaust_filter_dust_kg': '排气过滤器已积尘量', 'filter_hours_h': '过滤器/风机小时计初值', 'uv_hours_h': 'UV灯小时计初值', 'sash_cycles': '窗电机循环次数初值',
            'pin': '四位PIN', 'which': 'FAN或ADMIN PIN', 'mode': '工作模式', 'unit': '风速单位', 'filter_life_display': '显示过滤器寿命', 'language': 'LCD语言',
            'airflow_out_temp': '超温时显示风速', 'date_certified': '认证日期', 'date': '日期YYYY-MM-DD', 'time': '时间HH:MM', 'meter': '小时计', 'count': '循环次数',
            'action': '标定或恢复工厂标定', 'measured_inflow_m_s': '参考风速计测得的入流', 'input_id': '面板输入件（按键、钥匙开关）', 'duration_s': '按住时间',
            'position': '开关位置', 'display_id': '显示件（LCD或指示灯）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.class_ii_bsc_airflow.limitations。'
CAP_LIMITS = {
    **{k: ['面板层按panel_ui.json解释手势并调用本运行层的同名能力；菜单树由手册2.2各节与p.31-32的MENU OPTIONS画面重建（p.13菜单图无文字）；LCD为4行24字符（假设）；'
           '手势不推进仿真时钟（按住上/下键的时长作为窗的移动时间交给sash.move）。'] for k in PANEL_CAPS},
    'sash.move': ['按住式电动窗按“按住直到停位或按住hold_s秒”抽象；窗速为工程值；窗位磁开关容差±5 mm（工程值）。'],
    'item.place': ['物品不改变工作区气流分布（只按是否在前格栅区计挡格栅比例）；开口容器为单一液体、等温（不计蒸发冷却）；气溶胶源按恒定发生速率。'],
    'operator.set': ['手臂对气幕的影响按有效入流速度的工程折减；没有手臂几何与机器人接触。'],
    'settings.configure': ['直接调用不要求PIN（菜单的PIN会话见menu.enter）；预热或后吹扫中拒绝设置。'],
    'maintenance.field_calibration': ['现场标定按一点增益把显示入流对到参考风速计读数（手册两点标定的失效点由标称点偏移推得，ADC细节不建模）；增益超出0.7–1.3视为CALIBRATION ERROR；'
                                      '标定清除“传感器未标定”状态。'],
    'mode.set': ['维护模式：报警停用、按键联锁（预热/后吹扫/待机）解除、窗中途停住时照明不关；UV只在窗全关时可开的联锁保留（假设）。'],
    'menu.enter': ['进入菜单时蜂鸣器响2 s（时长为假设），菜单打开期间不监测（报警清空），预热计时暂停。'],
    'clock.set': ['LCD时钟=宿主时钟（2026-01-01 00:00起，假设）+偏移，不含时区。'],
}
LIMITATIONS = [
    '所选配置：AC2-4S8-TU（4 ft、不锈钢侧壁S系列、220–240 V）＋UV-30A灯；排气直接回室内（套管排气罩未选）；VHP注入口、服务旋塞、IV杆与电源插座为预留未装；操作细节（预热、后吹扫、UV定时、静音、窗状态与报警、待机、快速启动）取自同平台AC2 Gen 3手册（已标注same_platform_ac2_g3）。',
    '气流：两条支路各一台ECM风机，风机曲线、过滤器与系统阻力为工程值（额定点按厂家风量与风速）；ECM自补偿以一阶时间常数逼近目标风量，占空比饱和后风量下降；过滤器积尘按室内粉尘随入流进入、首过全捕集、按风量分配；10000 h寿命用于标定积尘阻力增长。',
    '气幕与保护：操作者保护（KI-Discus意义的保护因子）与产品保护按有效入流/下行流的逻辑斯蒂曲线（工程形状，参照厂家性能包络图定性）；不解算前开口流场与手臂尾流。',
    '空气：工作区与静压箱两个全混合节点（0.1 s子步显式积分，账目逐步闭合）；粒子按≥0.5 µm计数，过滤器穿透率取厂家H14典型效率；ISO级别按ISO 14644-1公式由工作区浓度换算。',
    '化学风险：开口容器单一液体等温蒸发（Mackay–Matsugu关联式），H14过滤器不截留蒸气；约2/3空气循环，排气回到室内；没有可燃下限、毒性限值或活性炭过滤（不是化学通风柜）。',
    '物品、气溶胶、蒸气与手臂在场景中没有几何；场景中电动窗、风速显示数字、照明灯带与面板指示灯按运行层状态变化。',
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
    extra = ['ACCESS_DENIED'] if ALL_CAPS[k]['visibility'] != 'agent' else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _pre_text(k):
    errs, text = CAP_ERRORS.get(k, []), PRECONDITIONS[k][0]
    if 'PLANT_REQUIRED' in errs and '房间条件' not in text:
        text = '已声明房间条件（否则PLANT_REQUIRED）；' + text
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
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景关节、读数或指示灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节或读数', binding_ids=[])


CALIBRATED = {k for k, v in instrument()['engineering'].items() if v.get('calibrated_by')}


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_brochure': _BR, 'manufacturer_product_page': _PR, 'same_platform_ac2_g3_manual': _MN}
    WB_KEYS = ('external_width_m', 'external_depth_with_arm_rest_m', 'external_height_m', 'tested_opening_m', 'inflow_velocity_m_s', 'downflow_velocity_m_s',
               'exhaust_volume_m3_s', 'usable_work_area_m2', 'mass_kg')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], ('[同平台AC2 Gen 3] ' if v['platform'] != 'AC2-4S8-TU' else '') + v['note_zh'] + '（原文：' + v['quote'] + '）',
            (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
        if v['platform'] != 'AC2-4S8-TU':
            P[k.lower()]['uncertainty'] = ('取自上一代AC2 Gen 3手册（同平台Sentinel控制器）：TU型（Gen 4）的固件默认值与功能可能不同，未经TU型资料确认' +
                                           ('；运行层不用“半速”，待机风量由calibrate_bsc.py按样本“待机节能60 %”标定（standby_flow_fraction）' if k == 'standby_half_speed' else ''))
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明）' +
            ('；由calibrate_bsc.py按厂家额定功耗、待机节能60 %（待机照明关闭）与过滤器寿命10000 h标定，取值不确定度约±30 %（不是实测）' if k in CALIBRATED else ''))
    asm('workzone_volume_m3', V_WZ, 'm3', '工作区空气体积（厂家内尺寸1220×580×654 mm之积）', '由厂家内尺寸计算', (_BR,))
    asm('downflow_face_area_m2', A_DOWN, 'm2', '下行流过滤器面积（厂家下行流风量/下行流风速）', '由厂家数据计算', (_BR,))
    return P


SLOT_REFERENCE = {'sash.opening_m': '窗下沿到前格栅（工作台前沿）的竖直开口高度', 'display.inflow_m_s': '前开口平均入流速度（安全窗高下由排气风量换算）',
                  'display.downflow_m_s': '下行流过滤器出风面平均速度', 'cabinet.temperature_k': '热力学温度（ITS-90）'}


def export_esco_ac24s8_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'EscoAc24s8Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in ALL_CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.esco_ac24s8.EscoAc24s8Session.invoke',
                                              invocation=f'session.invoke(instance_id="esco_ac24s8_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='EscoAc24s8Runtime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（风量、窗位与浓度在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['downflow_blower', 'exhaust_blower', 'downflow_filter', 'exhaust_filter', 'sash_window', 'front_grille', 'work_tray', 'led_light_bar', 'uv_lamp',
                       'control_panel', 'lcd_display'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='II级A2型生物安全柜：两台直流ECM风机分别维持下行流与排气风量（对过滤器积尘自补偿），前开口入流形成气幕保护操作者，下行流H14过滤空气保护样品；'
                   '约1/3空气经排气过滤器回到室内、2/3循环；粒子与气溶胶被过滤器截留，溶剂蒸气不被截留',
        equations=['fan: dp = u^2 P0 - P0 Q^2 / Qmax^2;  branch: dp = R_f (1 + m_dust / M2) Q + K Q^2 (+ rho Q^2 / (2 (Cd W h)^2) for the front opening)',
                   'ECM: du/dt = (min(1, u_req(Q_target)) - u) / tau;  dust: dm_b/dt = c_dust Q_in Q_b / (Q_down + Q_exh)',
                   'v_in = Q_exh / (W h);  v_eff = v_in (1 - k_b f_block) - k_x v_draft - k_a v_arm - max(0, v_down - v_max)',
                   'log10 APF = L / (1 + exp(-(v_eff - v50) / w))  (air-barrier protection; the total escape is given by the front-exchange equation below)',
                   'front exchange: w = exp(-Q_in / Q_th);  Q_x = f_open (Q_th + s Q_d) w (f_open = 1, or A_leak / (W h_safe) with the sash closed);  escape = S 10^(-log APF) (1 - w) + Q_x c_wz',
                   'V_wz dc_wz/dt = S (1 - esc) + P Q_d c_pl + Q_in c_room / PPF + Q_x c_room - (Q_d + Q_in / PPF + Q_x) c_wz - v_dep A c_wz;  V_pl dc_pl/dt = Q_in (1 - 1/PPF) c_room + (Q_d + Q_in / PPF) c_wz - (Q_d + Q_e) c_pl',
                   'closed sash with the fan on: exhaust duty <= duty for the target flow at the safe height',
                   'evaporation: G = K A (p_sat(T) M / (R T) - c_wz),  K = 0.0292 U^0.78 X^-0.11 Sc^-0.67 (m/h)',
                   'ISO class N = log10(C_0.5um / (0.1/0.5)^2.08)'],
        parameters=_params(), source_ids=[_SIM, _WB, _BR, _PR, _MN, _NI], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_esco_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('esco_ac24s8', caps, slots, {'class_ii_bsc_airflow': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'class_ii_bsc_airflow': mechanism})
