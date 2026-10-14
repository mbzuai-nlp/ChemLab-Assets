"""CHEM-199 Esco Frontier Acela EFA-5UDUVW-9 ducted fume hood (1.5 m, US version) with the Sentinel XL airflow monitor: process runtime.

Device (US brochure / catalogue 9010376 vI; Sentinel XL sell sheet 9010040 vC): bench-top ducted fume hood 1525 W x 900 D x 1406 H mm (1521 mm
with the exhaust collar, 1603 mm with the sash fully open; base cabinet not included), interior 1301 x 675 x 1230 mm, vertical 5 deg sloped sash
(6 mm laminated tempered glass, chain and sprocket), sash stop at 457 mm with Creep Down above it (unless held), key lock (limit to 457 mm or
locked closed), one 305 mm fiberglass exhaust collar; required exhaust 777 / 942 / 1177 m3/h at 14.6 / 23.7 / 34.7 Pa for 0.3 / 0.4 / 0.5 m/s at
457 mm and 1175 / 1556 / 1958 m3/h at 28.7 / 49.3 / 76.6 Pa with the sash fully open (670 mm); LED lighting 915 lux; rocker switches for the light
and the fan (exhaust blower circuit); cold water and gas service fixtures; duplex outlets.  Selected option: Sentinel XL monitor SXL-EFA (face
velocity in real time, low / high alarm 0.3 / 0.7 m/s by default, 85 dBA mutable alarm, SAFE / UNSAFE / MUTE LEDs, metric or imperial units,
PIN, self test, night setback relay input that mutes the alarm, sensor range 0.2 - 0.9 m/s, temperature compensated 18 - 30 C, needs field
calibration).

Process model (fixed 0.1 s grid; quiet periods - settled flow and sensor, no timer running - are skipped exactly with every linear quantity
written as base + rate x ticks; SI units: m, m3/s, Pa, kg, s):
  * Exhaust: the external system (building suction with duct and manual damper, a dedicated blower on the fan switch with a variable-speed
    curve, or a constant-volume supply) against the hood loss k(h) Q^2; k(h) and the effective opening A_eff(h) are least-squares fits of
    the six documented operating points (piecewise linear in the sash height, closed-sash bypass engineering); Q follows its target with a
    first-order lag.  Face velocity v = Q / A_eff(h).
  * Sash: set by hand (two-way joint in the scene); above the 457 mm stop it creeps down unless held; the key lock limits it to 457 mm or locks
    it closed.
  * Sentinel XL: sensor = gain x v (uncalibrated gain error, temperature error outside 18 - 30 C, 1 s lag), clamped to 0.2 - 0.9 m/s; alarm
    after a delay below the low or above the high set point; mute, night setback, self test, menu (low / high set points, units), PIN,
    field calibration.
  * Vapour sources (open liquid surfaces on the worktop, the gas fixture's methane, an SF6 tracer): evaporation k_m(u) A p_vap M / (R T);
    the escaped fraction e(v_eff) (engineering curve calibrated to the ASHRAE 110 certificate) goes to the operator's breathing zone, the
    rest to the exhaust; breathing-zone and exhaust concentrations against the NIOSH REL and the lower flammable limit (truth).
  * Ledgers: liquid placed = remaining + evaporated + removed; evaporated = exhausted + escaped (per substance); water and gas delivered."""
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.esco_efa5uduvw"
CATALOG_ID = "CHEM-199"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch05/t2_vacuum/esco_efa5uduvw'
LIBS = {'instrument': 'instrument_model.json', 'vapours': 'vapour_templates.json'}
DT = 0.1
MAX_EVENTS_LOG = 3000
R_GAS = 8.314462618


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
DER = {k: v['value'] for k, v in instrument()['derived'].items()}
ENG_NOTE = {k: v['engineering_note_zh'] for k, v in instrument()['engineering'].items()}
ENG_UNIT = {k: v['unit'] for k, v in instrument()['engineering'].items()}
LIQ = _lib('vapours')['liquids']
GAS = _lib('vapours')['gases']
LIQ_IDS = sorted(LIQ)
H_MAX = DOC['max_sash_opening_m']
H_STOP = DOC['sash_stop_m']
FPM = 0.00508                                                            # m/s per ft/min
UNITS = ('metric', 'imperial')
STATUS_CELLS = {'SAFE': 'SAFE', 'LOW AIRFLOW': 'Lo FLo', 'HIGH AIRFLOW': 'HI FLo', 'SELF TEST': 'tESt', 'LOW ALARM SET': 'Lo SEt', 'HIGH ALARM SET': 'HI SEt',
                'UNITS': 'UnItS', 'PIN LOCKED': 'PIn', 'NIGHT SETBACK': 'nIGHt', 'INVALID': 'Err', '': ''}


def _mix64(x):
    x = (x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return x ^ (x >> 31)


def _uni(seed, k, ch):
    """Deterministic uniform in [-1, 1) keyed by seed, index and channel (identical whether ticks are stepped or skipped)."""
    return (_mix64((seed * 1000003 + k) * 131 + ch) >> 11) / float(1 << 52) - 1.0


def p_vap(sid, t_k):
    v = LIQ[sid]['vapour_pressure']
    v = v.get('runtime_model', v)
    if v['kind'] == 'antoine':
        return 1e5 * 10.0 ** (v['A'] - v['B_k'] / (t_k + v['C_k']))
    return v['p_ref_pa'] * math.exp(-v['dh_vap_j_mol'] / R_GAS * (1.0 / t_k - 1.0 / v['t_ref_k']))


def _interp(h, pts):
    if h <= pts[0][0]:
        return pts[0][1]
    for (h0, y0), (h1, y1) in zip(pts, pts[1:]):
        if h <= h1:
            return y0 + (y1 - y0) * (h - h0) / (h1 - h0)
    return pts[-1][1]


A_PTS = [(0.0, ENG['a_eff_closed_m2']), (H_STOP, DER['a_eff_457']), (H_MAX, DER['a_eff_670'])]
K_PTS = [(0.0, ENG['k_hood_closed']), (H_STOP, DER['k_hood_457']), (H_MAX, DER['k_hood_670'])]


def a_eff(h):
    """Effective opening (flow per face velocity) at sash height h: fit of the documented points, closed-sash bypass engineering."""
    return _interp(h, A_PTS)


def k_hood(h):
    """Hood static pressure loss coefficient (dp = k Q^2) at sash height h."""
    return _interp(h, K_PTS)


def escape_fraction(v_eff, q):
    """Fraction of a source released in the work zone that escapes to the operator's breathing zone (engineering containment curve)."""
    if q <= 0.0:
        return 1.0
    c = ENG['containment']
    x = (v_eff - c['v50_m_s']) / c['scale_m_s']
    low = 1.0 / (1.0 + math.exp(min(x, 700.0)))
    turb = c['turbulence_amplitude'] / (1.0 + math.exp(-(v_eff - c['turbulence_onset_m_s']) / c['turbulence_scale_m_s']))
    return min(1.0, low + turb)


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
    elif k == 'null' and v is not None:
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


PLANT_EXAMPLE = dict(ambient_k=295.15, pressure_pa=101325.0, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 283.15, 313.15), pressure_pa=number('Pa', 80000.0, 110000.0), noise_seed=integer(0, 2 ** 31 - 1)))
EXHAUST = obj(dict(mode=enum(['building', 'blower', 'constant_volume', 'none']), suction_pa=number('Pa', 0.0, 1500.0), duct_k=number('Pa s2/m6', 0.0, 1.0e6),
                   damper_open=nullable(number('1', 0.05, 1.0)), blower_speed=number('1', 0.0, 1.0), flow_m3_s=number('m3/s', 0.0, 1.5)),
              optional=('suction_pa', 'duct_k', 'damper_open', 'blower_speed', 'flow_m3_s'))
EXHAUST_EXAMPLE = dict(mode='building', suction_pa=250.0, duct_k=3000.0, damper_open=0.5)
MON_KEYS = ['menu', 'up', 'down', 'test', 'set', 'mute']
FAULTS = ['mains_outage', 'blower_failure', 'duct_blockage', 'lamp_failure', 'sensor_drift']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['body'], '声明环境：室温、大气压与噪声种子（之后可改环境，种子不可改）', 'evaluator', 'configuration')
cap('facility.configure', obj(dict(water_supply_pa=number('Pa', 0.0, 8.0e5), gas_supply_pa=number('Pa', 0.0, 5000.0)), optional=('water_supply_pa', 'gas_supply_pa')),
    obj(dict(facility=FREE_OBJ)), dict(water_supply_pa=3.0e5, gas_supply_pa=1743.0), ['side_access'], '实验室公用：冷水与燃气的供给表压（0为未接）', 'evaluator', 'configuration')
cap('exhaust.configure', EXHAUST, obj(dict(exhaust=FREE_OBJ)), EXHAUST_EXAMPLE, ['exhaust_collar'],
    '宿主声明外部排风系统：建筑排风（总管负压、风管阻力、手动风阀开度）、专用风机（风机开关联动、变频转速）、定风量或未接；本柜不含风机（智能体只能调风阀与风机转速）', 'evaluator', 'configuration')
cap('damper.set', obj(dict(open_fraction=number('1', 0.05, 1.0))), obj(dict(open_fraction=number('1'))), dict(open_fraction=0.6), ['exhaust_collar'],
    '调节手动风阀开度（12 in风阀，平衡排风量）', 'agent', 'robot_skill')
cap('blower.set_speed', obj(dict(speed=number('1', 0.0, 1.0))), obj(dict(speed=number('1'))), dict(speed=0.8), ['exhaust_collar'], '专用风机的变频转速比', 'agent', 'configuration')
cap('light.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['light_switch'], '照明翘板开关')
cap('light.toggle', obj({}), obj(dict(on=BOOL)), {}, ['light_switch'], '照明翘板开关拨一下')
cap('fan.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['fan_switch'], '风机翘板开关（接通外部专用风机的回路）')
cap('fan.toggle', obj({}), obj(dict(on=BOOL)), {}, ['fan_switch'], '风机翘板开关拨一下')
cap('sash.set', obj(dict(height_m=number('m', 0.0, H_MAX), hold=BOOL), optional=('hold',)), obj(dict(height_m=number('m'), held=BOOL)), dict(height_m=H_STOP),
    ['sash'], '拉动移门到某开度（0–670 mm；越过457 mm限位后不扶住会自降；钥匙锁限制）', 'agent', 'robot_skill')
cap('sash.release', obj({}), obj(dict(held=BOOL)), {}, ['sash'], '松开扶住的移门（高于457 mm时开始自降）', 'agent', 'robot_skill')
cap('keylock.set', obj(dict(mode=enum(['off', 'limit', 'closed']))), obj(dict(mode=TEXT)), dict(mode='limit'), ['keylock'],
    '用钥匙设定移门锁：off、limit（不能高于457 mm）、closed（锁在全关；移门须已关闭）', 'agent', 'robot_skill')
cap('monitor.press', obj(dict(key=enum(MON_KEYS))), obj(dict(display=TEXT)), dict(key='mute'), ['monitor_panel'],
    '按Sentinel XL面板键：MENU（设置项）、上/下（调整）、SET（存入）、TEST（自检）、MUTE（静音）')
cap('monitor.configure', obj(dict(low_alarm_m_s=number('m/s', 0.2, 0.9), high_alarm_m_s=number('m/s', 0.2, 0.9), units=enum(UNITS), pin=nullable(TEXT),
                                  new_pin=nullable(TEXT)), optional=('low_alarm_m_s', 'high_alarm_m_s', 'units', 'pin', 'new_pin')),
    obj(dict(settings=FREE_OBJ)), dict(low_alarm_m_s=0.3, high_alarm_m_s=0.7, units='metric'), ['monitor_panel'],
    '设置报警低/高设定、显示单位与PIN（设了PIN时须给出PIN）', 'agent', 'configuration')
cap('monitor.calibrate', obj(dict(reference_m_s=number('m/s', 0.2, 0.9), pin=nullable(TEXT)), optional=('pin',)), obj(dict(calibrated=BOOL, reading_m_s=number('m/s'))),
    dict(reference_m_s=0.3), ['monitor_panel', 'airflow_sensor'], '现场校准：以参考风速计（anemometer.measure）在开口测得的面风速校正传感器，返回校准后的读数（不返回增益）', 'agent', 'configuration')
cap('anemometer.measure', obj({}), obj(dict(face_velocity_m_s=number('m/s'), points=integer(1, 100))), {}, ['sash'],
    '用参考热式风速计在移门开口按ASHRAE 110网格（3×6点）测面风速，返回平均值（含风速计系统误差与读数噪声）', 'agent', 'robot_skill')
cap('monitor.night_setback', obj(dict(closed=BOOL)), obj(dict(closed=BOOL)), dict(closed=True), ['monitor_panel'], '夜间节能继电器输入（闭合即静音报警；楼宇控制）', 'agent', 'robot_skill')
cap('source.place', obj(dict(source_id=TEXT, liquid_id=enum(LIQ_IDS), mass_kg=number('kg', 0.0, 5.0), area_m2=number('m2', 1e-5, 0.1)), optional=()),
    obj(dict(source=FREE_OBJ)), dict(source_id='beaker1', liquid_id='ethanol', mass_kg=0.05, area_m2=0.0028), ['worktop'],
    '在台面放一个敞口液体（模板库液体、质量、液面面积）', 'agent', 'robot_skill')
cap('source.remove', obj(dict(source_id=TEXT)), obj(dict(source=FREE_OBJ)), dict(source_id='beaker1'), ['worktop'], '取走一个敞口液体，返回剩余质量', 'agent', 'robot_skill')
cap('fixture.set', obj(dict(fixture=enum(['cold_water', 'gas']), open_fraction=number('1', 0.0, 1.0))), obj(dict(fixtures=FREE_OBJ)), dict(fixture='cold_water', open_fraction=0.5),
    ['knob_water', 'knob_gas'], '冷水或燃气龙头开度（0–1；燃气未点燃时进入柜内气流）', 'agent', 'robot_skill')
cap('disturbance.configure', obj(dict(cross_draft_m_s=number('m/s', 0.0, 1.0), walk_by_s=number('s', 0.0, 600.0)), optional=('cross_draft_m_s', 'walk_by_s')),
    obj(dict(disturbance=FREE_OBJ)), dict(cross_draft_m_s=0.05), ['body'], '评测者：室内横向气流、有人从柜前走过的时长', 'evaluator', 'simulation')
cap('tracer.release', obj(dict(rate_l_min=number('L/min', 0.0, 10.0))), obj(dict(rate_l_min=number('L/min'))), dict(rate_l_min=4.0), ['worktop'],
    '评测者：ASHRAE 110示踪气体SF6释放速率（0停止）', 'evaluator', 'simulation')
cap('events.read', obj(dict(since_s=number('s', 0.0)), optional=('since_s',)), obj(dict(events=array(FREE_OBJ), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), ['body'],
    '事件与报警记录', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(process=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, ['body'], '评测者：排风量、真实面风速、静压、泄漏分数、各物质浓度与暴露限值', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, ['body'], '评测者：液体、蒸气、水与燃气守恒账', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='blower_failure', active=True), ['body'],
    '评测者：注入或清除故障（停电、风机失效、风管堵塞、灯坏、传感器漂移）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), ['body'], '推进宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'vav.control': 'VAV控制器与移门传感器（VAV-TSI-FHC50选件）：未选配。',
    'sash.auto_position': '自动移门定位系统（VAV-SASH-AUTO选件）：未选配。',
    'exhaust.filter': '排风活性炭/HEPA/BIBO过滤模块（选件）：未选配。',
    'distillation_grid.mount': '蒸馏架（DG-5US选件）：未选配（台面上只有放置的敞口液体）。',
    'sash.horizontal_panels': '组合式移门的水平推拉玻璃（EFA-5UDUCW-9）：本型号为垂直移门（UDUVW）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['body'], _v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'body', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'body', '环境是否已声明')
slot('mains.on', BOOL, '1', 'derived', 'body', '柜体电源（照明、插座、风机回路与Sentinel）是否有电')
slot('light.on', BOOL, '1', 'setpoint', 'light_switch', '照明开关是否在开')
slot('fan.on', BOOL, '1', 'setpoint', 'fan_switch', '风机开关是否在开')
slot('lamp.lit', BOOL, '1', 'derived', 'led_lamp', 'LED灯是否点亮')
slot('lighting.illuminance_lux', number('lux'), 'lux', 'derived', 'led_lamp', '台面照度（灯亮时915 lux，厂家值）')
slot('sash.height_m', number('m', 0.0, H_MAX), 'm', 'derived', 'sash', '移门开度（台面到移门下沿；移门关节=该值）')
slot('sash.held', BOOL, '1', 'setpoint', 'sash', '移门是否被扶住（高于457 mm不扶住会自降）')
slot('keylock.mode', enum(['off', 'limit', 'closed']), '1', 'setpoint', 'keylock', '钥匙锁状态')
slot('exhaust.controls', FREE_OBJ, '1', 'setpoint', 'exhaust_collar', '操作者能调的排风控制：手动风阀开度（None为这条支管没有风阀，damper.set被拒）与专用风机转速比')
slot('exhaust.state', FREE_OBJ, '1', 'setpoint', 'exhaust_collar', '外部排风系统的宿主声明（方式、总管负压、风管阻力、风阀开度、风机转速、定风量；评测者）', 'evaluator')
slot('monitor.reading_m_s', nullable(number('m/s')), 'm/s', 'sensor', 'monitor_panel', 'Sentinel显示的面风速（m/s；断电为None）')
slot('monitor.alarm', nullable(enum(['low', 'high'])), '1', 'derived', 'monitor_panel', 'Sentinel报警：低风速或高风速')
slot('monitor.muted', BOOL, '1', 'derived', 'monitor_panel', '报警是否被静音（按键或夜间节能输入）')
slot('monitor.settings', FREE_OBJ, '1', 'setpoint', 'monitor_panel', 'Sentinel设定：低/高报警、单位、是否设了PIN、是否已现场校准')
slot('monitor.menu', nullable(TEXT), '1', 'derived', 'monitor_panel', '正在调整的菜单项（None为运行显示）')
slot('night_setback.closed', BOOL, '1', 'setpoint', 'monitor_panel', '夜间节能继电器输入是否闭合')
slot('display.velocity', nullable(number('1')), '1', 'derived', 'monitor_lcd', 'LCD第1行面风速数值（m/s或fpm，单位见monitor.settings）')
slot('display.text', TEXT, '1', 'derived', 'monitor_lcd', 'LCD第2行文字（SAFE、LOW AIRFLOW、HIGH AIRFLOW、SELF TEST、设置项等）')
slot('display.lcd_cells', TEXT, '1', 'derived', 'monitor_lcd', 'LCD第2行在场景七段读数上的字形（如SAFE、Lo FLo、HI FLo、tESt）')
for _k, _zh, _c in (('safe', 'SAFE绿灯', 'led_safe'), ('unsafe', 'UNSAFE红灯', 'led_unsafe'), ('mute', 'MUTE黄灯', 'led_mute')):
    slot(f'indicator.{_k}', BOOL, '1', 'derived', _c, _zh + '是否点亮')
slot('buzzer.on', BOOL, '1', 'derived', 'monitor_panel', '报警蜂鸣器是否在响（85 dBA）')
slot('outlets.powered', BOOL, '1', 'derived', 'outlet_right', '插座是否有电')
slot('fixtures.state', FREE_OBJ, '1', 'setpoint', ['knob_water', 'knob_gas'], '冷水与燃气龙头开度')
slot('sources.placed', FREE_OBJ, '1', 'setpoint', 'worktop', '台面上放置的敞口液体（编号、液体、放入质量、液面面积；剩余量见truth）')
slot('truth.process', FREE_OBJ, '1', 'process_truth', 'body',
     '排风量与目标、真实面风速、柜体静压、传感器增益、泄漏分数、各来源蒸发速率与剩余量、排风与呼吸带浓度及NIOSH限值、燃气爆炸下限比例', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'body', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'sash.height_m', 'display.velocity', 'display.lcd_cells', 'lamp.lit', 'indicator.safe', 'indicator.unsafe', 'indicator.mute', 'fixtures.state'}
slot('fixture.water_open', number('1', 0.0, 1.0), '1', 'setpoint', 'knob_water', '冷水龙头开度（旋钮关节=开度×90°）')
slot('fixture.gas_open', number('1', 0.0, 1.0), '1', 'setpoint', 'knob_gas', '燃气龙头开度（旋钮关节=开度×90°）')
SCENE_MIRRORED = (SCENE_MIRRORED - {'fixtures.state'}) | {'fixture.water_open', 'fixture.gas_open'}


def _empty_monitor():
    return dict(low=DOC['sxl_low_alarm_m_s'], high=DOC['sxl_high_alarm_m_s'], units='metric', pin=None, calibrated=False, muted=False, menu=None, edit=None,
                menu_t=None, test_until=None, pending=None, pending_t=0.0, alarm=None, message=None, message_until=None)


# ---------------------------------------------------------------- runtime
class EscoEfa5uduvwRuntime:
    def __init__(self, instance_id='esco_efa5uduvw_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.faults = set()
        self.light = False
        self.fan = False
        self.exhaust = dict(mode='none', suction_pa=ENG['building_suction_pa'], duct_k=ENG['duct_k'], damper_open=None, blower_speed=1.0, flow_m3_s=0.0)
        self.facility = dict(water_supply_pa=ENG['water_supply']['pressure_pa'], gas_supply_pa=ENG['gas_supply']['pressure_pa'])
        self.q = 0.0                                                          # exhaust flow (m3/s)
        self.h = 0.0                                                          # sash opening (m)
        self.held = False
        self.keylock = 'off'
        self.mon = _empty_monitor()
        self.v_s = 0.0                                                        # sensor signal (m/s, incl. gain and temperature error; before clamping)
        self.gain0 = 1.0
        self.night = False
        self.sources = {}
        self.fixtures = {'cold_water': 0.0, 'gas': 0.0}
        self.cross = ENG['room_cross_draft_m_s']
        self.walk_until = None
        self.tracer = 0.0                                                     # SF6 release (m3/s)
        self.events, self.events_dropped = [], 0
        self.revision = 0
        self.base, self.rate, self.t0 = {}, {}, 0                             # linear accumulators: value = base + rate x (tick - t0) x DT
        self.placed, self.removed = {}, {}
        self.flags = set()
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
        return EscoEfa5uduvwSession(self, name)

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

    # ---- linear accumulators
    def val(self, key):
        return self.base.get(key, 0.0) + self.rate.get(key, 0.0) * (self.tick - self.t0) * DT

    def _rebase(self):
        for k in set(self.base) | set(self.rate):
            self.base[k] = self.val(k)
        self.t0 = self.tick

    def _set_rates(self):
        self.rate = self._rates()

    # ---- airflow
    def _k_total(self):
        e = self.exhaust
        k = k_hood(self.h) + e['duct_k'] * (100.0 if 'duct_blockage' in self.faults else 1.0)
        if e.get('damper_open') is not None:
            k += ENG['damper_k1'] * (1.0 / e['damper_open'] - 1.0) ** 2
        return k

    def _q_target(self):
        if self.plant is None:
            return 0.0
        e = self.exhaust
        if e['mode'] == 'building':
            return math.sqrt(max(e['suction_pa'], 0.0) / self._k_total())
        if e['mode'] == 'blower':
            if not (self.fan and self._mains()) or 'blower_failure' in self.faults:
                return 0.0
            b = ENG['blower_curve']
            return e['blower_speed'] * math.sqrt(b['p0_pa'] / (b['c_pa_s2_m6'] + self._k_total()))
        if e['mode'] == 'constant_volume':
            return e['flow_m3_s'] * (0.3 if 'duct_blockage' in self.faults else 1.0)
        return 0.0

    def _face_velocity(self, q=None):
        return (self.q if q is None else q) / a_eff(self.h)

    def _cross_total(self):
        return self.cross + (ENG['walk_by_cross_draft_m_s'] if self.walk_until is not None and self.walk_until > self.t + 1e-9 else 0.0)

    def _escape(self):
        v = self._face_velocity()
        return escape_fraction(v - ENG['containment']['cross_factor'] * self._cross_total(), self.q)

    # ---- monitor
    def _mon_on(self):
        return self._mains()

    def _gain(self):
        g = self.gain0 * (1.2 if 'sensor_drift' in self.faults else 1.0)
        tc = self.plant['ambient_k'] - 273.15 if self.plant else 22.0
        lo, hi = DOC['sxl_temp_comp_c']
        dist = max(lo - tc, 0.0, tc - hi)
        return g * (1.0 + ENG['sensor']['temp_coeff_per_k'] * dist)

    def _sensor_target(self):
        return self._gain() * self._face_velocity() if self._mon_on() else 0.0

    def _reading(self):
        lo, hi = DOC['sxl_velocity_range_m_s']
        return min(max(self.v_s, lo), hi)

    def _cond(self):
        r = self.v_s                                                          # the signal, not the clamped display: below 0.2 / above 0.9 m/s still alarms
        m = self.mon
        return 'low' if r < m['low'] - 1e-12 else 'high' if r > m['high'] + 1e-12 else None

    # ---- sources and rates
    def _evap_rate(self, s):
        if self.val('m:' + s['source_id']) <= 0.0:
            return 0.0
        L = LIQ[s['liquid_id']]
        T = self.plant['ambient_k']
        a, b = ENG['inner_air_speed_factor']['a'], ENG['inner_air_speed_factor']['b']
        u = a * self._face_velocity() + b
        km = ENG['evaporation_k_m']['k0'] + ENG['evaporation_k_m']['k1'] * u
        return km * s['area_m2'] * p_vap(s['liquid_id'], T) * L['molar_mass_kg_mol'] / (R_GAS * T)

    def _gas_flows(self):
        """(water m3/s, methane m3/s) through the service fixtures."""
        if self.plant is None:
            return 0.0, 0.0
        w, g = ENG['water_supply'], ENG['gas_supply']
        fw = self.fixtures['cold_water'] * w['max_flow_m3_s'] * math.sqrt(max(self.facility['water_supply_pa'], 0.0) / w['pressure_pa'])
        fg = self.fixtures['gas'] * g['max_flow_m3_s'] * math.sqrt(max(self.facility['gas_supply_pa'], 0.0) / g['pressure_pa'])
        return fw, fg

    def _generation(self):
        """Mass generation rate (kg/s) per substance in the work zone."""
        G = {}
        if self.plant is None:
            return G
        T, p = self.plant['ambient_k'], self.plant['pressure_pa']
        for s in self.sources.values():
            r = self._evap_rate(s)
            if r > 0.0:
                G[s['liquid_id']] = G.get(s['liquid_id'], 0.0) + r
        _, fg = self._gas_flows()
        if fg > 0.0:
            G['methane'] = fg * p * GAS['methane']['molar_mass_kg_mol'] / (R_GAS * T)
        if self.tracer > 0.0:
            G['sulfur_hexafluoride'] = self.tracer * p * GAS['sulfur_hexafluoride']['molar_mass_kg_mol'] / (R_GAS * T)
        return G

    def _rates(self):
        r = {}
        if self.plant is None:
            return r
        e = self._escape()
        for s in self.sources.values():
            ev = self._evap_rate(s)
            if ev > 0.0:
                r['m:' + s['source_id']] = -ev
        for sub, g in self._generation().items():
            r['evap:' + sub] = g
            r['esc:' + sub] = e * g
            r['exh:' + sub] = (1.0 - e) * g
        fw, fg = self._gas_flows()
        if fw > 0.0:
            r['water_m3'] = fw
        if fg > 0.0:
            r['gas_m3'] = fg
        if self.tracer > 0.0:
            r['tracer_m3'] = self.tracer
        return r

    # ---- capabilities
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        query = capability_id in ('events.read', 'truth.inspect', 'ledger.read')
        if not query and capability_id != 'sim.step':
            self._rebase()
        out = fn(**args)
        if not query:
            self.revision += 1
            if capability_id != 'sim.step':
                self._set_rates()
                if self.plant is not None:
                    self._truth_events()
        return out

    def _cmd_plant_configure(self, ambient_k, pressure_pa, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        for s in self.sources.values():
            L = LIQ[s['liquid_id']]
            if not L['melting_point_k'] < ambient_k < L['boiling_point']['value']:
                raise ActionRejected('NOT_LIQUID')
        first = self.plant is None
        self.plant = dict(ambient_k=ambient_k, pressure_pa=pressure_pa)
        self.seed = noise_seed
        if first:
            self.gain0 = 1.0 + ENG['sensor']['gain_error_max'] * _uni(noise_seed, 0, 7)    # uncalibrated sensor (field calibration needed)
            # the building branch the hood is connected to differs from the published defaults (seeded): an omitted suction / duct
            # resistance is this branch's, so agent-visible data and published constants do not give the true face velocity
            self.exhaust['suction_pa'] = ENG['building_suction_pa'] * (1.0 + 0.2 * _uni(noise_seed, 0, 31))
            self.exhaust['duct_k'] = ENG['duct_k'] * (1.0 + 0.3 * _uni(noise_seed, 0, 32))
        self._event('system', 'PLANT_CONFIGURED', '环境已声明（柜体接电）')
        return dict(configured=True)

    def _cmd_facility_configure(self, water_supply_pa=None, gas_supply_pa=None):
        if water_supply_pa is not None:
            self.facility['water_supply_pa'] = float(water_supply_pa)
        if gas_supply_pa is not None:
            self.facility['gas_supply_pa'] = float(gas_supply_pa)
        return dict(facility=dict(self.facility))

    def _cmd_exhaust_configure(self, mode, suction_pa=None, duct_k=None, damper_open='keep', blower_speed=None, flow_m3_s=None):
        self._need_plant()
        e = dict(self.exhaust, mode=mode)
        for k, v in (('suction_pa', suction_pa), ('duct_k', duct_k), ('blower_speed', blower_speed), ('flow_m3_s', flow_m3_s)):
            if v is not None:
                e[k] = float(v)
        if damper_open != 'keep':                                             # omitted keeps the damper; null removes it (fully open)
            e['damper_open'] = None if damper_open is None else float(damper_open)
        self.exhaust = e
        self._event('facility', 'EXHAUST_CONFIGURED', f'排风系统：{mode}')            # facility declaration: not shown to the agent
        return dict(exhaust=dict(e))

    def _cmd_damper_set(self, open_fraction):
        self._need_plant()
        if self.exhaust['damper_open'] is None:
            raise ActionRejected('NO_DAMPER')                                 # the facility declared no manual damper on this branch
        self.exhaust['damper_open'] = float(open_fraction)
        return dict(open_fraction=self.exhaust['damper_open'])

    def _cmd_blower_set_speed(self, speed):
        self._need_plant()
        self.exhaust['blower_speed'] = float(speed)
        return dict(speed=self.exhaust['blower_speed'])

    def _cmd_light_set(self, on):
        self._need_plant()
        self.light = bool(on)
        return dict(on=self.light)

    def _cmd_light_toggle(self):
        return self._cmd_light_set(not self.light)

    def _cmd_fan_set(self, on):
        self._need_plant()
        self.fan = bool(on)
        return dict(on=self.fan)

    def _cmd_fan_toggle(self):
        return self._cmd_fan_set(not self.fan)

    def _cmd_sash_set(self, height_m, hold=False):
        self._need_plant()
        h = float(height_m)
        if self.keylock == 'closed' and h > 0.0:
            raise ActionRejected('SASH_LOCKED')
        if self.keylock == 'limit' and h > H_STOP + 1e-9:
            raise ActionRejected('ABOVE_STOP_LOCKED')
        if h > H_STOP + 1e-9 and self.h <= H_STOP + 1e-9:
            self._event('operation', 'SASH_OVER_STOP', '移门越过457 mm限位（不扶住会自降）')
        self.h = h
        self.held = bool(hold) and h > H_STOP + 1e-9
        return dict(height_m=self.h, held=self.held)

    def _cmd_sash_release(self):
        self._need_plant()
        self.held = False
        return dict(held=False)

    def _cmd_keylock_set(self, mode):
        self._need_plant()
        if mode == 'closed' and self.h > 1e-9:
            raise ActionRejected('SASH_NOT_CLOSED')
        if mode == 'limit' and self.h > H_STOP + 1e-9:
            raise ActionRejected('SASH_ABOVE_STOP')
        self.keylock = mode
        self._event('operation', 'KEYLOCK', f'钥匙锁：{mode}')
        return dict(mode=mode)

    # ---- Sentinel XL
    def _need_mon(self):
        self._need_plant()
        if not self._mon_on():
            raise ActionRejected('MONITOR_OFF')

    def _check_pin(self, pin):
        if self.mon['pin'] is not None and pin != self.mon['pin']:
            raise ActionRejected('PIN_INVALID')

    def _cmd_monitor_press(self, key):
        self._need_mon()
        m = self.mon
        if key == 'mute':
            if m['alarm'] is not None and not m['muted']:
                m['muted'] = True
                self._event('operation', 'ALARM_MUTED', '报警静音（MUTE黄灯）')
        elif key == 'test':
            if m['menu'] is None:
                m['test_until'] = self.t + ENG['self_test_s']
                self._event('operation', 'SELF_TEST', '自检：指示灯全亮、蜂鸣器响')
        elif key == 'menu':
            if m['pin'] is not None:
                m['message'], m['message_until'] = 'PIN LOCKED', self.t + 2.0     # the keypad PIN entry is not modelled (monitor.configure takes the PIN)
            else:
                items = ['low', 'high', 'units']
                nxt = 0 if m['menu'] is None else items.index(m['menu']) + 1
                if nxt >= len(items):
                    m['menu'], m['edit'] = None, None
                else:
                    m['menu'] = items[nxt]
                    m['edit'] = m[items[nxt]]
                m['menu_t'] = self.t
        elif key in ('up', 'down') and m['menu'] is not None:
            if m['menu'] == 'units':
                m['edit'] = 'imperial' if m['edit'] == 'metric' else 'metric'
            else:
                lo, hi = DOC['sxl_velocity_range_m_s']
                m['edit'] = round(min(max(m['edit'] + (0.01 if key == 'up' else -0.01), lo), hi), 2)
            m['menu_t'] = self.t
        elif key == 'set' and m['menu'] is not None:
            new = dict(low=m['low'], high=m['high'])
            if m['menu'] in new:
                new[m['menu']] = m['edit']
            if new['low'] >= new['high']:
                m['message'], m['message_until'] = 'INVALID', self.t + 2.0
            else:
                m[m['menu']] = m['edit']
                self._event('operation', 'MONITOR_SET', f'Sentinel设定{m["menu"]} = {m["edit"]}')
            m['menu_t'] = self.t
        return dict(display=self._display_text())

    def _cmd_monitor_configure(self, low_alarm_m_s=None, high_alarm_m_s=None, units=None, pin=None, new_pin=None):
        self._need_mon()
        self._check_pin(pin)
        m = self.mon
        low = m['low'] if low_alarm_m_s is None else round(float(low_alarm_m_s), 3)
        high = m['high'] if high_alarm_m_s is None else round(float(high_alarm_m_s), 3)
        if low >= high:
            raise ActionRejected('OUT_OF_RANGE')
        m.update(low=low, high=high)
        if units is not None:
            m['units'] = units
        if new_pin is not None:
            m['pin'] = new_pin or None
        return dict(settings=self._settings())

    def _cmd_monitor_calibrate(self, reference_m_s, pin=None):
        self._need_mon()
        self._check_pin(pin)
        lo, hi = DOC['sxl_velocity_range_m_s']
        if not lo <= self.v_s <= hi:
            raise ActionRejected('NOT_CALIBRATABLE')                         # the sensor must read inside its range
        f = float(reference_m_s) / self.v_s
        self.gain0 *= f
        self.v_s *= f                                                         # the signal is rescaled at once (the reading shows the reference)
        self.mon['calibrated'] = True
        self._event('operation', 'CALIBRATED', f'现场校准：参考面风速{reference_m_s:.2f} m/s')
        return dict(calibrated=True, reading_m_s=self._reading())

    def _cmd_anemometer_measure(self):
        self._need_plant()
        if self.h < 0.10 - 1e-12:
            raise ActionRejected('SASH_TOO_LOW')
        a = ENG['reference_anemometer']
        v = self._face_velocity() * (1.0 + a['bias_max'] * _uni(self.seed, 0, 21)) + a['noise_m_s'] * _uni(self.seed, self.tick, 22)
        return dict(face_velocity_m_s=max(v, 0.0), points=18)

    def _cmd_monitor_night_setback(self, closed):
        self._need_plant()
        self.night = bool(closed)
        return dict(closed=self.night)

    def _settings(self):
        m = self.mon
        return dict(low_alarm_m_s=m['low'], high_alarm_m_s=m['high'], units=m['units'], pin_set=m['pin'] is not None, calibrated=m['calibrated'])

    # ---- sources, fixtures, disturbances
    def _cmd_source_place(self, source_id, liquid_id, mass_kg, area_m2):
        self._need_plant()
        if source_id in self.sources:
            raise ActionRejected('DUPLICATE_ID')
        L = LIQ[liquid_id]
        if not L['melting_point_k'] < self.plant['ambient_k'] < L['boiling_point']['value']:
            raise ActionRejected('NOT_LIQUID')
        self.sources[source_id] = dict(source_id=source_id, liquid_id=liquid_id, mass_kg=float(mass_kg), area_m2=float(area_m2))
        self.base['m:' + source_id] = float(mass_kg)
        self.placed[liquid_id] = self.placed.get(liquid_id, 0.0) + float(mass_kg)
        self._event('operation', 'SOURCE_PLACED', f'放入{source_id}（{liquid_id} {mass_kg * 1000:.3g} g，液面{area_m2 * 1e4:.3g} cm²）')
        return dict(source=dict(self.sources[source_id]))

    def _cmd_source_remove(self, source_id):
        self._need_plant()
        if source_id not in self.sources:
            raise ActionRejected('NO_SOURCE')
        s = self.sources.pop(source_id)
        left = max(self.val('m:' + source_id), 0.0)
        self.base.pop('m:' + source_id, None)
        self.rate.pop('m:' + source_id, None)
        self.removed[s['liquid_id']] = self.removed.get(s['liquid_id'], 0.0) + left
        self._event('operation', 'SOURCE_REMOVED', f'取走{source_id}（剩{left * 1000:.4g} g）')
        return dict(source=dict(s, remaining_kg=left))

    def _cmd_fixture_set(self, fixture, open_fraction):
        self._need_plant()
        self.fixtures[fixture] = float(open_fraction)
        return dict(fixtures=dict(self.fixtures))

    def _cmd_disturbance_configure(self, cross_draft_m_s=None, walk_by_s=None):
        self._need_plant()
        if cross_draft_m_s is not None:
            self.cross = float(cross_draft_m_s)
        if walk_by_s is not None:
            self.walk_until = self.t + float(walk_by_s) if walk_by_s > 0 else None
        return dict(disturbance=dict(cross_draft_m_s=self.cross, walk_by_until_s=self.walk_until))

    def _cmd_tracer_release(self, rate_l_min):
        self._need_plant()
        self.tracer = float(rate_l_min) * 1e-3 / 60.0
        return dict(rate_l_min=float(rate_l_min))

    def _cmd_events_read(self, since_s=0.0):
        return dict(events=[e for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        self._need_plant()
        r = self.read('evaluator')
        return dict(process=r['truth.process'], faults=r['truth.faults'])

    def _cmd_ledger_read(self):
        return dict(ledger=self._ledger())

    def _mon_power_off(self):
        self.mon.update(alarm=None, muted=False, pending=None, pending_t=0.0, menu=None, edit=None, test_until=None, message=None, message_until=None)
        self.v_s = 0.0

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        if not self._mon_on():
            self._mon_power_off()                                             # the monitor goes dark with the mains, not on the next tick
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}' + ('：注入' if active else '：清除'))
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- time
    def _creeping(self):
        return self.h > H_STOP + 1e-12 and not self.held

    def _timers(self):
        m = self.mon
        return (m['test_until'] is not None or m['menu'] is not None or m['message'] is not None or self.walk_until is not None)

    def _quiet(self):
        if self.plant is None:
            return True
        if self.q != self._q_target() or self.v_s != self._sensor_target() or self._creeping() or self._timers():
            return False
        if self._mon_on() and (self._cond() != self.mon['alarm'] or self.mon['pending'] is not None):
            return False
        return True

    def _quiet_ticks(self):
        """How many ticks may be skipped without a source running dry inside them (the drying tick is stepped)."""
        n = 10 ** 12
        for s in self.sources.values():
            r = -self.rate.get('m:' + s['source_id'], 0.0)
            if r > 0.0:
                n = min(n, int(math.floor(self.val('m:' + s['source_id']) / (r * DT))) - 1)
        return n

    def step(self, dt_s):
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = round(self.t + float(dt_s), 9)
        if self.plant is None:
            self.t = t_end
            self.tick = int(math.floor(t_end / DT + 1e-6))
            self.t0 = self.tick
            self.t_grid = round(self.tick * DT, 9)
            return self.t
        while self.t_grid + DT <= t_end + 1e-9:
            if self._quiet():
                n = min(int(math.floor((t_end - self.t_grid) / DT + 1e-6)), self._quiet_ticks())
                if n > 0:
                    self.tick += n                                            # every linear quantity is base + rate x ticks: exact
                    self.t_grid = round(self.tick * DT, 9)
                    self.t = self.t_grid
                    continue
            self.t = round(self.t_grid + DT, 9)                               # events of the tick carry the end of its interval
            self._tick(DT)
            self.tick += 1
            self.t_grid = round(self.tick * DT, 9)
            self.t = self.t_grid
        self.t = t_end
        return self.t

    def _tick(self, h):
        m = self.mon
        if self.walk_until is not None and self.t >= self.walk_until - 1e-9:
            self.walk_until = None
        if m['test_until'] is not None and self.t >= m['test_until'] - 1e-9:
            m['test_until'] = None
        if m['message'] is not None and self.t >= m['message_until'] - 1e-9:
            m['message'], m['message_until'] = None, None
        if m['menu'] is not None and self.t - m['menu_t'] >= ENG['menu_timeout_s'] - 1e-9:
            m['menu'], m['edit'] = None, None                                  # no key for 30 s: back to the run display without storing
        if self._creeping():                                                  # Creep Down: the sash returns to the 457 mm stop
            self.h = max(self.h - ENG['creep_speed_m_s'] * h, H_STOP)
            if self.h <= H_STOP + 1e-12:
                self.h = H_STOP
                self._event('operation', 'SASH_CREPT_DOWN', '移门自降回到457 mm')
        qt = self._q_target()
        self.q = qt + (self.q - qt) * math.exp(-h / ENG['flow_time_constant_s'])
        if abs(self.q - qt) <= 1e-9 * max(qt, 1e-3):
            self.q = qt
        if not self._mon_on():
            self._mon_power_off()
        else:
            st = self._sensor_target()
            self.v_s = st + (self.v_s - st) * math.exp(-h / ENG['sensor']['time_constant_s'])
            if abs(self.v_s - st) <= 1e-9 * max(st, 1e-3):
                self.v_s = st
            c = self._cond()
            if c is None:
                if m['alarm'] is not None:
                    self._event('alarm', 'ALARM_CLEARED', '面风速回到设定范围：报警解除')
                m.update(alarm=None, muted=False, pending=None, pending_t=0.0)
            elif c != m['alarm']:
                if m['pending'] != c:
                    m['pending'], m['pending_t'] = c, 0.0
                m['pending_t'] += h
                if m['pending_t'] >= ENG['alarm_delay_s'] - 1e-9:
                    m.update(alarm=c, muted=False, pending=None, pending_t=0.0)
                    self._event('alarm', 'ALARM_' + c.upper(), ('面风速低于' if c == 'low' else '面风速高于') + '报警设定：UNSAFE红灯、蜂鸣器' +
                                ('（夜间节能输入：静音）' if self.night else ''))
            else:
                m['pending'], m['pending_t'] = None, 0.0
        # linear quantities over this tick (a source running dry gives its remaining mass)
        self._rebase()
        rates = self._rates()
        e = self._escape()
        for s in list(self.sources.values()):
            key = 'm:' + s['source_id']
            r = -rates.get(key, 0.0)
            if r <= 0.0:
                continue
            left = self.base[key]
            dm = min(r * h, left)
            self.base[key] = left - dm
            sub = s['liquid_id']
            for k, f in (('evap:', 1.0), ('esc:', e), ('exh:', 1.0 - e)):
                self.base[k + sub] = self.base.get(k + sub, 0.0) + f * dm
            if dm < r * h:
                self.base[key] = 0.0
                self._event('operation', 'SOURCE_DRY', f'{s["source_id"]}已蒸干')
        for k, r in rates.items():
            if k.startswith('m:') or (k.split(':', 1)[0] in ('evap', 'esc', 'exh') and k.split(':', 1)[1] in LIQ):
                continue
            self.base[k] = self.base.get(k, 0.0) + r * h
        self.t0 = self.tick + 1                                               # the step loop advances the tick after this
        self.rate = {}                                                        # values are the bases now (no stale rate while the new rates are computed)
        self.rate = self._rates()
        self._truth_events()

    def _truth_events(self):
        G = self._generation()
        if not G:
            return
        T, p = self.plant['ambient_k'], self.plant['pressure_pa']
        e = self._escape()
        for sub, g in G.items():
            rec = LIQ.get(sub) or GAS[sub]
            vol = g / rec['molar_mass_kg_mol'] * R_GAS * T / p
            bz = e * vol / ENG['breathing_zone_dilution_m3_s'] * 1e6
            rel = rec['niosh_rel'].get('value_ppm')
            if rel and bz > rel and ('rel', sub) not in self.flags:
                self.flags.add(('rel', sub))
                self._event('truth', 'BZ_OVER_REL', f'{sub}呼吸带浓度{bz:.3g} ppm超过NIOSH限值{rel:g} ppm')
            if sub == 'methane' and self.q > 0.0 and (1.0 - e) * vol / self.q > 0.25 * GAS['methane']['lower_flammable_limit'] and ('lfl', sub) not in self.flags:
                self.flags.add(('lfl', sub))
                self._event('truth', 'EXHAUST_FLAMMABLE', '排风中甲烷超过爆炸下限的25 %')

    # ---- display and read
    def _display_text(self):
        if self.plant is None or not self._mon_on():
            return ''
        m = self.mon
        if m['test_until'] is not None:
            return 'SELF TEST'
        if m['message'] is not None:
            return m['message']
        if m['menu'] is not None:
            return {'low': 'LOW ALARM SET', 'high': 'HIGH ALARM SET', 'units': 'UNITS'}[m['menu']]
        if m['alarm'] is not None:
            return 'LOW AIRFLOW' if m['alarm'] == 'low' else 'HIGH AIRFLOW'
        return 'SAFE'

    def _shown_reading(self):
        """The reading the LCD shows (m/s): sensor signal clamped to the range plus display noise keyed by the tick (identical when skipped)."""
        lo, hi = DOC['sxl_velocity_range_m_s']
        return min(max(self._reading() + ENG['sensor']['noise_m_s'] * _uni(self.seed, self.tick, 3), lo), hi)

    def _display_velocity(self):
        if self.plant is None or not self._mon_on():
            return None
        m = self.mon
        units = m['edit'] if m['menu'] == 'units' else m['units']
        v = m['edit'] if m['menu'] in ('low', 'high') else self._shown_reading()
        return round(v, 2) if units == 'metric' else float(round(v / FPM))

    def _lights(self):
        on = self.plant is not None and self._mon_on()
        m = self.mon
        if not on:
            return dict(safe=False, unsafe=False, mute=False, buzzer=False)
        if m['test_until'] is not None:
            return dict(safe=True, unsafe=True, mute=True, buzzer=True)
        alarm = m['alarm'] is not None
        silenced = m['muted'] or self.night
        return dict(safe=not alarm, unsafe=alarm, mute=alarm and silenced, buzzer=alarm and not silenced)

    def _process(self):
        T, p = self.plant['ambient_k'], self.plant['pressure_pa']
        e = self._escape()
        conc = {}
        for sub, g in self._generation().items():
            rec = LIQ.get(sub) or GAS[sub]
            vol = g / rec['molar_mass_kg_mol'] * R_GAS * T / p
            rel = rec['niosh_rel']
            conc[sub] = dict(generation_kg_s=g, exhaust_ppm=((1.0 - e) * vol / self.q * 1e6) if self.q > 0.0 else None,
                             breathing_zone_ppm=e * vol / ENG['breathing_zone_dilution_m3_s'] * 1e6, niosh_rel_ppm=rel.get('value_ppm'), niosh_rel_kind=rel.get('kind'))
        fw, fg = self._gas_flows()
        lfl = None
        if 'methane' in conc and self.q > 0.0:
            lfl = conc['methane']['exhaust_ppm'] * 1e-6 / GAS['methane']['lower_flammable_limit']
        return dict(exhaust_flow_m3_s=self.q, exhaust_flow_target_m3_s=self._q_target(), face_velocity_m_s=self._face_velocity(), effective_opening_m2=a_eff(self.h),
                    hood_static_pa=k_hood(self.h) * self.q ** 2, sensor_gain=self._gain(), sensor_signal_m_s=self.v_s, escape_fraction=e, cross_draft_m_s=self._cross_total(),
                    sources={k: dict(liquid_id=s['liquid_id'], remaining_kg=max(self.val('m:' + k), 0.0), evaporation_kg_s=self._evap_rate(s)) for k, s in self.sources.items()},
                    concentrations=conc, methane_exhaust_lfl_fraction=lfl, water_flow_m3_s=fw, gas_flow_m3_s=fg, tracer_m3_s=self.tracer)

    def read(self, visibility='agent'):
        pc = self.plant is not None
        lights = self._lights()
        text = self._display_text() if pc else ''
        out = {'clock.time_s': self.t, 'plant.configured': pc, 'mains.on': self._mains(), 'light.on': self.light, 'fan.on': self.fan,
               'lamp.lit': pc and self.light and self._mains() and 'lamp_failure' not in self.faults,
               'sash.height_m': self.h, 'sash.held': self.held, 'keylock.mode': self.keylock,
               'exhaust.controls': dict(damper_open=self.exhaust['damper_open'], blower_speed=self.exhaust['blower_speed']),
               'monitor.reading_m_s': round(self._shown_reading(), 2) if pc and self._mon_on() else None,
               'monitor.alarm': self.mon['alarm'] if pc else None, 'monitor.muted': bool(pc and self.mon['alarm'] is not None and (self.mon['muted'] or self.night)),
               'monitor.settings': self._settings(), 'monitor.menu': self.mon['menu'], 'night_setback.closed': self.night,
               'display.velocity': self._display_velocity() if pc else None, 'display.text': text, 'display.lcd_cells': STATUS_CELLS.get(text, text[:8]),
               'indicator.safe': lights['safe'], 'indicator.unsafe': lights['unsafe'], 'indicator.mute': lights['mute'], 'buzzer.on': lights['buzzer'],
               'outlets.powered': self._mains(), 'fixtures.state': dict(self.fixtures), 'fixture.water_open': self.fixtures['cold_water'],
               'fixture.gas_open': self.fixtures['gas'],
               'sources.placed': {k: dict(liquid_id=s['liquid_id'], placed_kg=s['mass_kg'], area_m2=s['area_m2']) for k, s in self.sources.items()}}
        out['lighting.illuminance_lux'] = DOC['lamp_intensity_lux'] if out['lamp.lit'] else 0.0
        if visibility != 'agent':
            out['exhaust.state'] = dict(self.exhaust)
            out['truth.process'] = self._process() if pc else {}
            out['truth.faults'] = sorted(self.faults)
        return out

    def _ledger(self):
        if self.plant is None:
            return {}
        subs = sorted({k.split(':', 1)[1] for k in set(self.base) | set(self.rate) if ':' in k and not k.startswith('m:')} | set(self.placed))
        L = dict(substances={}, water_m3=self.val('water_m3'), gas_m3=self.val('gas_m3'), tracer_m3=self.val('tracer_m3'))
        worst_l, worst_v = 0.0, 0.0
        for sub in subs:
            rem = sum(max(self.val('m:' + k), 0.0) for k, s in self.sources.items() if s['liquid_id'] == sub)
            ev, es, ex = self.val('evap:' + sub), self.val('esc:' + sub), self.val('exh:' + sub)
            rec = dict(evaporated_kg=ev, escaped_kg=es, exhausted_kg=ex, vapour_balance_kg=ev - es - ex)
            if sub in LIQ:
                rec.update(placed_kg=self.placed.get(sub, 0.0), removed_kg=self.removed.get(sub, 0.0), remaining_kg=rem,
                           liquid_balance_kg=self.placed.get(sub, 0.0) - self.removed.get(sub, 0.0) - rem - ev)
                worst_l = max(worst_l, abs(rec['liquid_balance_kg']))
            worst_v = max(worst_v, abs(rec['vapour_balance_kg']))
            L['substances'][sub] = rec
        L.update(worst_liquid_balance_kg=worst_l, worst_vapour_balance_kg=worst_v)
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
class EscoEfa5uduvwSession:
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
        meta = copy.deepcopy(export_esco_efa5uduvw_contract_metadata())
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
            except Exception:                                                 # defensive: no exception escapes the session, the state is restored
                h.__dict__.update(snapshot)
                raise ActionRejected('INVALID_ARGUMENT')
            finally:
                h._caller = None
            if self._role == 'agent' and capability_id == 'events.read':
                result = dict(events=[e for e in result['events'] if e['kind'] not in ('fault', 'truth', 'facility')], dropped=result['dropped'])
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
    'NOT_LIQUID': '该液体在室温下不是液体（低于熔点或高于沸点）', 'SASH_LOCKED': '移门被钥匙锁在全关', 'ABOVE_STOP_LOCKED': '钥匙锁限制移门不能高于457 mm',
    'SASH_NOT_CLOSED': '要锁在全关，移门须先关闭', 'SASH_ABOVE_STOP': '要限制在457 mm，移门须先降到457 mm以下', 'MONITOR_OFF': 'Sentinel没有电（停电）',
    'PIN_INVALID': 'PIN不对', 'NO_DAMPER': '这条排风支管没有装手动风阀（宿主声明）', 'SASH_TOO_LOW': '移门开度低于100 mm，风速计网格放不下', 'OUT_OF_RANGE': '低报警设定须低于高报警设定', 'NOT_CALIBRATABLE': '传感器读数不在0.2–0.9 m/s量程内，不能校准',
    'DUPLICATE_ID': '来源编号已存在', 'NO_SOURCE': '没有该来源',
}
_P = ['PLANT_REQUIRED']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED', 'NOT_LIQUID'], 'facility.configure': [], 'exhaust.configure': _P, 'damper.set': _P + ['NO_DAMPER'], 'blower.set_speed': _P,
    'light.set': _P, 'light.toggle': _P, 'fan.set': _P, 'fan.toggle': _P, 'sash.set': _P + ['SASH_LOCKED', 'ABOVE_STOP_LOCKED'], 'sash.release': _P,
    'keylock.set': _P + ['SASH_NOT_CLOSED', 'SASH_ABOVE_STOP'], 'monitor.press': _P + ['MONITOR_OFF'], 'monitor.configure': _P + ['MONITOR_OFF', 'PIN_INVALID', 'OUT_OF_RANGE'],
    'monitor.calibrate': _P + ['MONITOR_OFF', 'PIN_INVALID', 'NOT_CALIBRATABLE'], 'anemometer.measure': _P + ['SASH_TOO_LOW'], 'monitor.night_setback': _P, 'source.place': _P + ['DUPLICATE_ID', 'NOT_LIQUID'],
    'source.remove': _P + ['NO_SOURCE'], 'fixture.set': _P, 'disturbance.configure': _P, 'tracer.release': _P, 'events.read': [], 'truth.inspect': _P, 'ledger.read': [],
    'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_MONP = ('柜体有电（Sentinel由柜体电源供电）', ['mains.on'])
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子（之后不可改）；台面上的液体在新室温下仍是液体', ['plant.configured', 'sources.placed']),
    'facility.configure': ('无', ['fixtures.state']), 'exhaust.configure': ('无（外部系统由设施或宿主声明）', ['exhaust.state']),
    'damper.set': ('排风支管装有手动风阀（exhaust.controls的damper_open不为None）', ['exhaust.controls']), 'blower.set_speed': ('无（只在专用风机方式下起作用）', ['exhaust.controls']),
    'light.set': ('无', ['light.on']), 'light.toggle': ('无', ['light.on']), 'fan.set': ('无（只在专用风机方式下控制风机）', ['fan.on']), 'fan.toggle': ('无', ['fan.on']),
    'sash.set': ('钥匙锁不是closed（开门时）；钥匙锁为limit时不能高于457 mm', ['keylock.mode', 'sash.height_m']), 'sash.release': ('无', ['sash.held']),
    'keylock.set': ('锁在全关时移门须已关闭；限制在457 mm时移门须不高于457 mm', ['sash.height_m', 'keylock.mode']),
    'monitor.press': _MONP, 'monitor.configure': ('柜体有电；设了PIN时须给出PIN；低报警<高报警', ['mains.on', 'monitor.settings']),
    'monitor.calibrate': ('柜体有电；设了PIN时须给出PIN；传感器读数在量程内', ['mains.on', 'monitor.settings', 'monitor.reading_m_s']),
    'anemometer.measure': ('移门开度不低于100 mm（风速计网格）', ['sash.height_m']), 'monitor.night_setback': ('无', ['night_setback.closed']), 'source.place': ('编号未用；液体在室温下是液体', ['sources.placed']),
    'source.remove': ('来源存在', ['sources.placed']), 'fixture.set': ('无（供给压力为0时没有流量）', ['fixtures.state']),
    'disturbance.configure': ('无', ['plant.configured']), 'tracer.release': ('无', ['plant.configured']), 'truth.inspect': ('无', ['plant.configured']),
    'fault.inject': ('无', ['plant.configured']),
}
_MON = ['monitor.reading_m_s', 'monitor.alarm', 'monitor.muted', 'monitor.menu', 'display.velocity', 'display.text', 'display.lcd_cells', 'indicator.safe',
        'indicator.unsafe', 'indicator.mute', 'buzzer.on']
_LIGHT = ['lamp.lit', 'lighting.illuminance_lux']
_EVOLVE = _MON + ['sash.height_m', 'truth.process']
EFFECTS = {
    'plant.configure': ['plant.configured', 'mains.on', 'outlets.powered', 'exhaust.state', 'truth.process'] + _LIGHT + _MON, 'facility.configure': ['truth.process'],
    'exhaust.configure': ['exhaust.state', 'exhaust.controls', 'truth.process'], 'damper.set': ['exhaust.state', 'exhaust.controls', 'truth.process'],
    'blower.set_speed': ['exhaust.state', 'exhaust.controls', 'truth.process'],
    'light.set': ['light.on'] + _LIGHT, 'light.toggle': ['light.on'] + _LIGHT, 'fan.set': ['fan.on', 'truth.process'], 'fan.toggle': ['fan.on', 'truth.process'],
    'sash.set': ['sash.height_m', 'sash.held', 'truth.process'], 'sash.release': ['sash.held'], 'keylock.set': ['keylock.mode'],
    'monitor.press': ['monitor.settings'] + _MON, 'monitor.configure': ['monitor.settings'] + _MON, 'monitor.calibrate': ['monitor.settings', 'truth.process'] + _MON,
    'anemometer.measure': [], 'monitor.night_setback': ['night_setback.closed', 'monitor.muted', 'indicator.mute', 'buzzer.on'], 'source.place': ['sources.placed', 'truth.process'],
    'source.remove': ['sources.placed', 'truth.process'], 'fixture.set': ['fixtures.state', 'fixture.water_open', 'fixture.gas_open', 'truth.process'],
    'disturbance.configure': ['truth.process'], 'tracer.release': ['truth.process'],
    'fault.inject': ['truth.faults', 'mains.on', 'outlets.powered', 'truth.process'] + _LIGHT + _MON, 'sim.step': ['clock.time_s'],
}
_STEPPED = ('plant.configure', 'facility.configure', 'exhaust.configure', 'damper.set', 'blower.set_speed', 'fan.set', 'fan.toggle', 'sash.set', 'sash.release',
            'monitor.press', 'monitor.configure', 'monitor.calibrate', 'monitor.night_setback', 'source.place', 'source.remove', 'fixture.set', 'disturbance.configure',
            'tracer.release', 'fault.inject')
ON_STEP = {k: list(_EVOLVE) for k in _STEPPED}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {'sim.step': '按0.1 s网格积分：移门自降、排风量一阶响应、传感器滞后、Sentinel报警延时、自检与菜单计时、走动扰动；蒸发、泄漏、排风与公用量按速率累计；'
                          '排风与传感器已稳定且没有计时在走时整段跳过（线性量写成基值+速率×格数，跳过与逐格完全一致）'}
EXEC_TEXT = {}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('sash.set', 'sash.release', 'keylock.set', 'anemometer.measure', 'damper.set', 'light.set', 'light.toggle', 'fan.set', 'fan.toggle', 'monitor.press', 'source.place', 'source.remove',
           'fixture.set'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'sash.set': ['sash.height_m'], 'monitor.press': ['display.text'], 'light.set': ['lamp.lit'], 'exhaust.configure': ['monitor.reading_m_s']}
_WB, _BRO, _SXL, _PH, _PC, _LIB, _SIM = ('user_workbook', 'manufacturer_brochure', 'sentinel_xl_sheet', 'manufacturer_photo', 'pubchem', 'liquid_library', 'simulation_assumption')
CAP_SOURCES = {k: [_BRO] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_WB, _SIM], 'facility.configure': [_BRO, _SIM], 'exhaust.configure': [_BRO, _SIM], 'damper.set': [_BRO, _SIM], 'blower.set_speed': [_BRO, _SIM],
                    'monitor.press': [_SXL, _SIM], 'monitor.configure': [_SXL], 'monitor.calibrate': [_SXL, _BRO], 'anemometer.measure': [_SIM], 'monitor.night_setback': [_SXL],
                    'source.place': [_LIB, _PC, _SIM], 'source.remove': [_SIM], 'disturbance.configure': [_SIM], 'tracer.release': [_BRO, _PC, _SIM],
                    'events.read': [_SIM], 'truth.inspect': [_SIM], 'ledger.read': [_SIM], 'fault.inject': [_SIM], 'sim.step': [_SIM]})
PARAM_ZH = {'ambient_k': '室温', 'pressure_pa': '大气压', 'noise_seed': '噪声种子', 'water_supply_pa': '供水表压', 'gas_supply_pa': '供气表压', 'mode': '方式',
            'suction_pa': '建筑排风负压', 'duct_k': '风管阻力系数', 'damper_open': '风阀开度', 'blower_speed': '风机转速比', 'flow_m3_s': '定风量', 'open_fraction': '开度',
            'speed': '转速比', 'on': '开或关', 'height_m': '移门开度', 'hold': '扶住', 'key': '面板键', 'low_alarm_m_s': '低报警设定', 'high_alarm_m_s': '高报警设定',
            'units': '显示单位', 'pin': '当前PIN', 'new_pin': '新PIN', 'reference_m_s': '参考面风速', 'closed': '继电器闭合', 'source_id': '来源编号', 'liquid_id': '液体（模板库）',
            'mass_kg': '液体质量', 'area_m2': '液面面积', 'fixture': '龙头', 'cross_draft_m_s': '横向气流', 'walk_by_s': '走动时长', 'rate_l_min': 'SF6释放速率',
            'since_s': '起始时间', 'fault': '故障名', 'active': '注入或清除', 'dt_s': '推进时间'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.fume_hood_process.limitations。'
CAP_LIMITS = {
    'monitor.press': ['Sentinel XL的菜单文字与键序厂家资料未给出：MENU依次进入低报警、高报警、单位设置，上/下调整（0.01 m/s），SET存入，30 s无操作退出（工程表示）；'
                      '设了PIN时键盘显示PIN LOCKED，PIN只经monitor.configure给出。'],
    'exhaust.configure': ['本柜不含风机：排风量由外部系统决定（建筑排风负压+风管+风阀、专用风机曲线或定风量；均为工程模型，非厂家曲线）；风机开关只在专用风机方式下接通风机回路；'
                          '未给出的总管负压与风管阻力取本支管的值（按噪声种子在默认250 Pa±20 %、3000±30 %内），智能体不能由公开常数算出真实面风速。'],
    'source.place': ['敞口液面蒸发按传质系数（随柜内风速）×饱和蒸气压；不建模蒸发冷却、液体升温与混合物。'],
    'anemometer.measure': ['参考风速计的系统误差（±2 %内，按噪声种子）与每次读数噪声（±0.005 m/s）为工程值；给出网格平均，不模拟逐点风速分布（没有CFD）。'],
    'monitor.calibrate': ['校准把传感器读数对到给定参考值（Sentinel XL资料：出厂后须现场校准）；校准精度取决于参考值（应来自anemometer.measure）；增益本身不对智能体公开。'],
    'tracer.release': ['ASHRAE 110示踪试验的简化：SF6以给定速率在工作区释放，呼吸带浓度=泄漏分数×释放量/呼吸带稀释风量（工程模型）。'],
}
LIMITATIONS = [
    '所选配置：Esco Frontier Acela EFA-5UDUVW-9（物料号2090256，1.5 m、900 mm深、Resinate Plus内衬、垂直移门、Esco白、110–120 VAC），柜体不含底柜；按调研表“风速监视”选配Sentinel XL监视器（SXL-EFA，出厂安装，需现场校准）；'
    '冷水与燃气龙头按工程图第18、19号零件建入。',
    '排风：厂家只给6个工作点（457 mm与全开670 mm，0.3/0.4/0.5 m/s的排风量与静压）；有效开口A_eff与柜体阻力k按最小二乘拟合，窗位之间线性插值，移门全关的旁通面积与阻力为工程值；外部风机、风管与风阀为工程模型。',
    '面风速是开口平均值v=Q/A_eff；没有CFD，柜内不同位置的风速差别不建模。',
    '容纳：泄漏分数随有效面风速（减去横向气流）的工程曲线，按ASHRAE 110证书（EFA-4，0.31 m/s，SF6 4 L/min时呼吸带<0.010 ppm）标定到设计点约0.005 ppm；标称面风速不保证所有姿态与放置工况的隔离（调研表缺口），'
    '人员走动只以附加横向气流表示，操作者手臂、柜内大件设备与热源的影响未建模。',
    'Sentinel XL：传感器增益（未校准误差、温度补偿范围外误差、漂移）、1 s滞后与显示噪声为工程值；报警延时、菜单文字与键序厂家资料未给出（工程表示）；LCD在场景中用七段读数近似（第1行数值，第2行状态字形）。',
    '移门：457 mm限位、自降（不扶住时）与钥匙锁为厂家描述；自降速度为工程值；链条链轮、配重与移门玻璃的力学不建模（开度为状态）。',
    '照明：灯亮时台面照度915 lux（厂家值），不随位置变化。',
    '浓度：排风与呼吸带浓度为准稳态（柜内空气不积累）；NIOSH推荐限值只作参照，不做合规判定；燃气龙头的天然气按甲烷、未点燃时进入柜内气流，排风中超过爆炸下限25 %记为真值事件。',
    '时间：运行层按0.1 s网格积分；命令在下一格起作用，格内事件记在格的终点。',
]


def _sampling_text(k):
    if k == 'clock.time_s':
        return 'Host clock.'
    if SLOTS[k]['origin'] in ('sensor', 'derived', 'process_truth'):
        return 'Updated on the 0.1 s grid of the host steps and at once when a command changes it.'
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
        conv = {'deg': ('rad', math.radians, '_deg', '_rad'), 'degC': ('K', lambda x: x + 273.15, '_c', '_k')}
        if unit not in conv:
            return name, val, unit, ''
        u, f, a, b = conv[unit]
        val2 = [f(x) for x in val] if isinstance(val, list) else f(val)
        return (name[:-len(a)] + b if name.endswith(a) else name + b), val2, u, f'（换算为SI；资料原值{val} {unit}）'

    def doc(name, val, unit, meaning, src):
        name, val, unit, extra = si(name, val, unit)
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning + extra, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_brochure': _BRO, 'sentinel_xl_sheet': _SXL}
    WB_KEYS = ('external_dimensions_m', 'height_with_collar_m', 'height_full_sash_m', 'exhaust_design_30', 'exhaust_design_40', 'exhaust_design_50', 'exhaust_outlet',
               'lamp_intensity_lux', 'working_height_m')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
    for k in sorted(ENG):
        ev = instrument()['engineering'][k]['evidence']
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '由厂家数据推得' if ev == 'documented_derived' else '工程值（见instrument_model.json的engineering说明），不是实测')
    for k, v in instrument()['derived'].items():
        asm(k, v['value'], v['unit'], '由厂家数据推导：' + v['note_zh'], '最小二乘拟合值', (_BRO,))
    vt = _lib('vapours')
    asm('vapour_templates', dict(file='assets/expansion_batch05/t2_vacuum/esco_efa5uduvw/vapour_templates.json', counts=vt['counts']), '1',
        '蒸气来源模板：51种液体（CHEM-008液体库：蒸气压、摩尔质量等）及NIOSH推荐暴露限值（PubChem），SF6示踪气体与甲烷（燃气）；每条带来源', '文献数据；适用规则见模板库rules_zh',
        (_LIB, _PC))
    asm('gas_constant_j_mol_k', R_GAS, 'J/(mol K)', '气体常数', '物理常数', (_SIM,))
    return P


SLOT_REFERENCE = {'monitor.reading_m_s': '开口平均面风速的传感器读数（未现场校准时有增益误差），不是真实面风速（见truth.process.face_velocity_m_s）',
                  'lighting.illuminance_lux': '厂家照度值（台面），不是逐点测量'}


def export_esco_efa5uduvw_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=list(v['component']) if isinstance(v['component'], (list, tuple)) else [v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'EscoEfa5uduvwSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.esco_efa5uduvw.EscoEfa5uduvwSession.invoke',
                                              invocation=f'session.invoke(instance_id="esco_efa5uduvw_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='EscoEfa5uduvwRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（排风、传感器与蒸发在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['body', 'sash', 'exhaust_collar', 'baffle', 'worktop', 'monitor_panel', 'airflow_sensor', 'led_lamp', 'light_switch', 'fan_switch', 'knob_water', 'knob_gas', 'gooseneck', 'gas_turret', 'keylock'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='外部排风经排风口从柜内抽气，空气从移门开口（及旁通）进入，形成面风速把工作区产生的蒸气带入排风；移门开度改变有效开口与阻力；Sentinel XL按传感器读数显示面风速并在越过设定时报警；'
                   '敞口液体按传质蒸发，一部分随湍流与横向气流逃逸到操作者呼吸带',
        equations=['k_total = k_hood(h) + K_duct + K1 (1/theta - 1)^2;  building: Q* = sqrt(p_s / k_total);  blower: Q* = s sqrt(p0 / (c + k_total));  constant: Q* = Q_set',
                   'dQ/dt = (Q* - Q) / tau_Q;  v = Q / A_eff(h);  dp_hood = k_hood(h) Q^2  (A_eff, k_hood: least squares of the six documented points, linear in h)',
                   'sensor: dv_s/dt = (g v - v_s) / tau_s;  reading = clamp(v_s, 0.2, 0.9);  alarm if reading < low or > high for t_delay',
                   'evaporation: m_dot = (k0 + k1 (a v + b)) A p_vap(T) M / (R T);  escape: e = 1 / (1 + exp((v - 0.5 u_cross - v50) / s)) + turbulence term',
                   'C_bz = e G_vol / Q_bz;  C_exhaust = (1 - e) G_vol / Q'],
        parameters=_params(), source_ids=[_SIM, _WB, _BRO, _SXL, _PC, _LIB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_esco_efa5uduvw_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('esco_efa5uduvw', caps, slots, {'fume_hood_process': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'exhaust.configure': (EXHAUST, EXHAUST_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'fume_hood_process': mechanism})
