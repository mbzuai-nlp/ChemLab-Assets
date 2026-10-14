"""CHEM-198 BINDER KBF 115 constant climate chamber (KBF115-230V, art. no. 9020-0320; 2022 data sheet configuration): process runtime.

Device (data sheet 23.05.2022; KBF-S (E6) 7001-0357 manual of the same 115 cabinet with the RD4 controller; KBF (E7) 7001-0569 manual of the
touchscreen program controller): dry mode 0-70 C, climate mode 10-70 C and 10-80 %RH inside the climate chart (guaranteed range A, time-limited
range B max. 24 h); variation +-0.2 K, fluctuation +-0.1 K, humidity fluctuation <= +-2 %RH at 25 C / 60 %RH and 40 C / 75 %RH; recovery after
30 s door opening 6 min (25/60) and 7 min (40/75); max. heat compensation 200 W at 40 C; 470 Wh/h at 40/75; 102 L (600 x 483 x 351 mm), 2 / 5
shelves, 30 kg per rack, 100 kg load; 2 kW, 16 A; 880 x 650 x 1050 mm, 128 kg; APT.line preheating chamber, door heating, ESG glass inner door,
class 3.1 safety device, capacitive humidity sensor and steam humidifier (0.07 L near boiling), dehumidification by dew point undershoot of the
evaporator, 0.5 L internal wastewater can, touchscreen program controller (25 programs x 100 sections, ramp / step, tolerance band pause,
section repetitions), tolerance range alarms (2-10 K, 5-20 %RH, delay 15-120 min), door-open behaviour (fan to minimum, everything off after
60 s, door alarm after 5 min), internal data logger (60 s), power-failure resume.

Process model (fixed 1 s grid anchored at t = 0; SI units: K, Pa, kg, J, W; relative humidity as a fraction 0-1):
  * Air: one well-mixed node (interior + APT.line preheating chamber) with dry-air heat capacity and the water-vapour mass; implicit energy
    balance with the stainless interior (steel node), the ESG glass door node, the evaporator, the stored samples, the heater share, the fan heat,
    the steam and the room air entering through the gasket leak and the open doors (buoyant doorway flow on the virtual-temperature density
    difference plus a door-swing pumping volume).
  * Heating / refrigeration (DCT, abstracted): PI temperature controller on the Pt100 reading: heater (half into the preheating-chamber walls) or
    extra cooling; while the compressor runs (set point <= 60 C in control) a bypassed base cooling acts and the heater compensates it; cooling
    capacity Q_max(T_air); evaporator temperature from the cooling load, or the dew point minus the undershoot while dehumidifying.
  * Humidity: PI controller on the capacitive sensor: steam (limited by the module and its warm-up after re-activation) or dehumidification
    (evaporator below the dew point: condensate to the wastewater can, ice below 0 C that lowers the capacity and melts with warm air);
    condensation / evaporation on the steel walls (drained) and the glass door (fog when the door heating fails or the glass is cold).
  * Water: freshwater pipe (tap) or the optional 20 L can feeds the 0.07 L steam container; wastewater can 0.5 L pumped at 0.4 L.
  * Samples: deliquescent salts (Greenspan 1977 deliquescence humidities), dissolved / deliquesced solutes (measured aw(molality) branches of the
    shared NIST/TRC library), adsorbents (measured isotherms) and inert items; heat and vapour exchange with the air; cold samples collect a
    condensate film; exposure statistics (mean kinetic temperature, mean RH) and an optional host-declared degradation law.
  * Controller: fixed value / program / idle modes, functions (humidity control, idle), safety controller class 3.1 (limit / offset, active after
    the set point was reached once), class 1 cut-off, tolerance range alarms, door alarm, water / wastewater / humidity system alarms, buzzer and
    reset, power failure resume, data logger.
  * Ledgers: chamber energy (air, vapour, steel, glass, films, ice, samples) and the water of the whole humidity system close exactly; electrical
    energy is accumulated."""
import bisect
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.binder_kbf115"
CATALOG_ID = "CHEM-198"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t2_vacuum/binder_kbf115'
LIBS = {'instrument': 'instrument_model.json', 'sorption': 'sorption_templates.json'}


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
SORPTION = _lib('sorption')['substances']
DT = 1.0
T0 = 273.15
G_ACC = 9.80665
R_GAS = 8.314462618
R_AIR = 287.05                                  # J/(kg K), dry air
R_VAP = 461.52                                  # J/(kg K), water vapour
CP_AIR = 1006.0                                 # J/(kg K), dry air
CP_VAP = 1860.0                                 # J/(kg K), water vapour
CP_WATER = 4184.0                               # J/(kg K), liquid water
L0 = 2.501e6                                    # J/kg, vaporisation enthalpy at 0 C (reference: liquid water at 0 C)
L_FUS = 333.55e3                                # J/kg, ice melting at 0 C
M_W = 0.01801528                                # kg/mol
T_STEAM = 373.15
MKT_DH = 83.144e3                               # J/mol, mean kinetic temperature activation energy (ICH Q1A convention)
CHART_A = [tuple(p) for p in DOC['chart_a']]
CHART_B = [tuple(p) for p in DOC['chart_b']]
T_SET_LO, T_SET_HI = DOC['setting_range_temperature_k']
RH_SET_HI = DOC['setting_range_humidity'][1]
FAN_LO, FAN_HI = DOC['fan_range']
W_INT, H_INT, D_INT = DOC['internal_dimensions_m']['w'], DOC['internal_dimensions_m']['h'], DOC['internal_dimensions_m']['d']
DOOR_MAX = 1.75                                 # rad, outer door
GLASS_MAX = 1.65                                # rad, glass inner door
OUTER_FOR_GLASS = 1.30                          # outer door opening needed to swing the glass door
GLASS_FOR_LOADING = 1.20
A_WALL = 2.0 * (W_INT * H_INT + W_INT * D_INT + H_INT * D_INT)     # m2, interior surface (condensation)
A_GLASS = W_INT * H_INT
MODES = ['fixed', 'program', 'idle']
STATES = ['off', 'outage', 'idle', 'fixed', 'program', 'program_paused', 'door_off']
FAULTS = ['heater_failure', 'compressor_failure', 'humidity_module_failure', 'fan_failure', 'temperature_sensor_failure', 'humidity_sensor_failure',
          'safety_sensor_failure', 'door_sensor_failure', 'door_heating_failure', 'wastewater_blocked', 'mains_outage']
SUBSTANCE_IDS = sorted(SORPTION) + ['inert']
LOG_MIN, LOG_MAX = 60.0, 3600.0
MAX_EVENTS_LOG = 3000
MAX_LOG = 100000
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


def rho_sat(T):
    return p_sat(T) / (R_VAP * T)


def dew_point(pv):
    """Dew point (K) over liquid water from the vapour pressure (inverse Magnus)."""
    if pv <= 1e-6:
        return 180.0
    g = math.log(pv / 610.94)
    return T0 + 243.04 * g / (17.625 - g)


def rho_moist(T, p, pv):
    return (p - pv) / (R_AIR * T) + pv / (R_VAP * T)


def _interp(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x) - 1
    f = (x - xs[i]) / (xs[i + 1] - xs[i])
    return ys[i] + f * (ys[i + 1] - ys[i])


def _in_poly(pt, poly):
    x, y = pt
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
        on = min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9 and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9 and \
            abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) <= 1e-6 * (abs(x2 - x1) + abs(y2 - y1) + 1.0)
        if on:
            return True
    return inside


def chart_region(T, rh):
    """Climate-chart region of a set point: 'A' guaranteed, 'B' time-limited (24 h), 'outside', or 'dry' (humidity control off)."""
    pt = (T - T0, rh * 100.0)
    if _in_poly(pt, CHART_A):
        return 'A'
    if _in_poly(pt, CHART_B):
        return 'B'
    return 'outside'


# ---------------------------------------------------------------- sample templates
def drh(sub, T):
    """Deliquescence humidity of a salt at T (Greenspan Table 2, linear in T, clamped to the tabulated range); None when not tabulated."""
    d = SORPTION[sub].get('deliquescence') if sub in SORPTION else None
    if not d:
        return None
    return _interp(d['temperature_k'], d['relative_humidity'], T)


def branch_aw(sub, m):
    """Water activity of the solution at molality m (mol/kg) on the measured branch; below the most concentrated measured point a salt with a
    deliquescence humidity is extrapolated linearly from the last two points down to that humidity; returns (aw, quality)."""
    b = SORPTION[sub]['solution_branch']
    ms, aws = b['molality_mol_kg'], b['water_activity']
    if m <= ms[-1]:
        return (_interp(ms, aws, m) if m >= ms[0] else 1.0 - (1.0 - aws[0]) * m / ms[0]), 'measured'
    if SORPTION[sub].get('deliquescence') and len(ms) >= 2:
        slope = (aws[-1] - aws[-2]) / (ms[-1] - ms[-2])
        return aws[-1] + slope * (m - ms[-1]), 'extrapolated'
    return aws[-1], 'measured_limit'                                         # no solubility known: the most concentrated measured solution


def branch_molality(sub, aw):
    """Inverse of branch_aw: molality at which the solution water activity equals aw (None outside the usable range)."""
    b = SORPTION[sub]['solution_branch']
    ms, aws = b['molality_mol_kg'], b['water_activity']
    if aw >= aws[0]:
        return ms[0] * (1.0 - aw) / max(1.0 - aws[0], 1e-9)
    if aw >= aws[-1]:
        return _interp(aws[::-1], ms[::-1], aw)
    if SORPTION[sub].get('deliquescence') and len(ms) >= 2:
        slope = (aws[-1] - aws[-2]) / (ms[-1] - ms[-2])
        return ms[-1] + (aw - aws[-1]) / slope if slope < 0 else None
    return None


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


PLANT_EXAMPLE = dict(ambient_k=295.15, relative_humidity=0.5, pressure_pa=101325.0, water_supply='pipe', shelves=2, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 283.15, 313.15), relative_humidity=number('1', 0.0, 0.9), pressure_pa=number('Pa', 70000.0, 110000.0),
                 mains_v=number('V', 180.0, 253.0), water_supply=enum(['pipe', 'can']), shelves=integer(2, 5), noise_seed=integer(0, 2 ** 31 - 1)),
            optional=('mains_v', 'water_supply', 'shelves'))
SETPOINT = obj(dict(temperature_k=number('K', T_SET_LO, T_SET_HI), relative_humidity=number('1', 0.0, RH_SET_HI), fan_fraction=number('1', FAN_LO, FAN_HI)),
               optional=('temperature_k', 'relative_humidity', 'fan_fraction'))
SETPOINT_EXAMPLE = dict(temperature_k=313.15, relative_humidity=0.75, fan_fraction=1.0)
SECTION = obj(dict(duration_s=number('s', 1.0, DOC['section_duration_max_s']), temperature_k=number('K', T_SET_LO, T_SET_HI),
                   relative_humidity=number('1', 0.0, RH_SET_HI), fan_fraction=number('1', FAN_LO, FAN_HI), transition=enum(['ramp', 'step']),
                   humidity_control=BOOL, tolerance_k=nullable(number('K', 0.1, 50.0)), tolerance_rh=nullable(number('1', 0.01, 0.5))),
              optional=('fan_fraction', 'humidity_control', 'tolerance_k', 'tolerance_rh'))
PROGRAM = obj(dict(program=integer(2, DOC['programs']['memories']), sections=array(SECTION, 1, DOC['programs']['sections']),
                   repeat=nullable(obj(dict(from_section=integer(1, 99), to_section=integer(2, 100), count=integer(1, 99))))),
              optional=('repeat',))
PROGRAM_EXAMPLE = dict(program=2, sections=[dict(duration_s=3600.0, temperature_k=298.15, relative_humidity=0.60, transition='step'),
                                            dict(duration_s=7200.0, temperature_k=313.15, relative_humidity=0.75, transition='ramp'),
                                            dict(duration_s=1.0, temperature_k=313.15, relative_humidity=0.75, transition='step')])
SAMPLE = obj(dict(sample_id=ITEM_ID, substance_id=enum(SUBSTANCE_IDS), shelf=integer(1, 5), dry_mass_kg=number('kg', 1e-5, 30.0),
                  exposed_area_m2=number('m2', 1e-5, 0.5), initial_k=number('K', 250.0, 350.0), initial_water_kg=number('kg', 0.0, 30.0),
                  heat_capacity_j_kg_k=number('J/(kg K)', 100.0, 5000.0), container_mass_kg=number('kg', 0.0, 30.0), heat_output_w=number('W', 0.0, 500.0),
                  degradation=obj(dict(rate_ref_per_s=number('1/s', 0.0, 1.0), activation_j_mol=number('J/mol', 0.0, 300000.0),
                                       humidity_exponent=number('1', 0.0, 10.0)))),
             optional=('exposed_area_m2', 'initial_water_kg', 'heat_capacity_j_kg_k', 'container_mass_kg', 'heat_output_w', 'degradation'))
SAMPLE_EXAMPLE = dict(sample_id='nacl_dish_1', substance_id='sodium_chloride', shelf=1, dry_mass_kg=0.010, exposed_area_m2=0.005, initial_k=295.15,
                      container_mass_kg=0.05)
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'operation', 'door', 'alarm', 'program', 'maintenance', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
LOGREC = obj(dict(time_s=number('s'), temperature_c=nullable(number('1')), humidity_pct=nullable(number('1')), set_temperature_c=number('1'),
                  set_humidity_pct=nullable(number('1')), door_open=BOOL, mode=TEXT, alarms=array(TEXT, 0, 16)))
_BODY = ['cabinet']
_PANEL = ['touchscreen']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明室温、室内相对湿度、大气压、电源电压、供水方式（水管或选配20 L水桶）、搁架数（2–5）与噪声种子', 'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['power_switch'], '仪表面板上的主电源开关通/断（通电后按断电前的模式继续）')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['power_switch'], '主电源开关按一下')
cap('setpoint.set', SETPOINT, obj(dict(setpoints=FREE_OBJ, chart_region=TEXT)), SETPOINT_EXAMPLE, _PANEL,
    '定值模式设定：温度（−5…70 °C；控湿时控制范围10…70 °C）、相对湿度（0…80 %RH，受气候图限制）与风机转速（40–100 %）')
cap('function.set', obj(dict(humidity_control=BOOL, idle_mode=BOOL), optional=('humidity_control', 'idle_mode')),
    obj(dict(functions=FREE_OBJ)), dict(humidity_control=True), _PANEL, '控制器功能：湿度控制开/关（Humidity off）、Idle mode（停止控制）')
cap('safety.configure', obj(dict(mode=enum(['limit', 'offset']), value_k=number('K', 0.5, 373.15))), obj(dict(safety=FREE_OBJ)), dict(mode='offset', value_k=2.0),
    _PANEL, '3.1级安全控制器：Limit（绝对温度上限，273.15–373.15 K）或Offset（设定之上的偏移，0.5–30 K）')
cap('tolerance.configure', obj(dict(temperature_k=number('K', DOC['temperature_tolerance_k'][0], DOC['temperature_tolerance_k'][1]),
                                    relative_humidity=number('1', DOC['humidity_tolerance'][0], DOC['humidity_tolerance'][1]),
                                    delay_s=number('s', DOC['range_alarm_delay_s'][0], DOC['range_alarm_delay_s'][1])),
                               optional=('temperature_k', 'relative_humidity', 'delay_s')),
    obj(dict(tolerance=FREE_OBJ)), dict(temperature_k=2.0, relative_humidity=0.05, delay_s=900.0), _PANEL, '公差带报警：温度2–10 K、湿度5–20 %RH、延时15–120 min')
cap('buzzer.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), _PANEL, '报警蜂鸣器开/关')
cap('alarm.reset', obj({}), obj(dict(muted=array(TEXT, 0, 16), cleared=array(TEXT, 0, 16))), {}, _PANEL,
    'Reset alarm：静音全部报警；条件已消失的报警同时清除（“Humidity system”报警不得确认）')
cap('program.write', PROGRAM, obj(dict(program=integer(2, 25), sections=integer(1, 100), duration_s=number('s'))), PROGRAM_EXAMPLE, _PANEL,
    '写入时间程序（2…25号；每段时长、温度、湿度、风机、ramp/step、湿度控制、程序公差带；可重复若干连续段）')
cap('program.start', obj(dict(program=integer(2, 25))), obj(dict(program=integer(2, 25), started=BOOL)), dict(program=2), _PANEL, '启动时间程序（结束后转定值模式）')
cap('program.stop', obj({}), obj(dict(stopped=BOOL)), {}, _PANEL, '停止程序，转定值模式')
cap('logger.configure', obj(dict(interval_s=number('s', LOG_MIN, LOG_MAX))), obj(dict(interval_s=number('s'))), dict(interval_s=60.0), _PANEL,
    '数据记录器存储间隔（出厂60 s）')
cap('log.read', obj(dict(last=integer(1, MAX_LOG))), obj(dict(records=array(LOGREC, 0, MAX_LOG))), dict(last=60), ['usb_port'],
    '读出内置数据记录器（经USB以开放格式导出：时间、温度、湿度、设定、门、模式、报警）', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _PANEL,
    '事件列表（电源、门、报警、程序、维护）', 'agent', 'query')
cap('door.move', obj(dict(angle_rad=number('rad', 0.0, DOOR_MAX))), obj(dict(angle_rad=number('rad'))), dict(angle_rad=1.6), ['outer_door'],
    '外门（右铰链、左侧把手）转到给定角度；关外门须先关玻璃内门', 'agent', 'robot_skill')
cap('glass_door.move', obj(dict(angle_rad=number('rad', 0.0, GLASS_MAX))), obj(dict(angle_rad=number('rad'))), dict(angle_rad=1.5), ['glass_door'],
    'ESG玻璃内门转到给定角度（须外门大开）', 'agent', 'robot_skill')
cap('water.supply', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['freshwater_inlet'], '供水：打开/关闭进水龙头（水管供水）或接通/断开水桶', 'agent', 'robot_skill')
cap('can.refill', obj({}), obj(dict(can_kg=number('kg'))), {}, ['freshwater_inlet'], '把选配外置水桶加满去离子水（仅选桶供水时）', 'agent', 'robot_skill')
cap('sample.place', SAMPLE, obj(dict(sample_id=TEXT, shelf=integer(1, 5), shelf_load_kg=number('kg'))), SAMPLE_EXAMPLE, ['shelves'],
    '把样品（敞口皿中的盐、可溶固体、吸附剂或惰性物）放到某层搁架（外门与玻璃门打开；每层≤30 kg、总≤100 kg）', 'agent', 'robot_skill')
cap('sample.remove', obj(dict(sample_id=ITEM_ID)), obj(dict(sample=FREE_OBJ)), dict(sample_id='nacl_dish_1'), ['shelves'],
    '取出样品并称量、目视：总质量、质量变化与外观（干燥、潮解、表面凝水）；暴露统计与降解只在评测者真值中', 'agent', 'robot_skill')
cap('samples.read', obj({}), obj(dict(samples=array(FREE_OBJ, 0, 200))), {}, ['shelves'], '腔内样品清单（宿主声明的编号、物质与搁架；不含内部状态）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(climate=FREE_OBJ, samples=FREE_OBJ, power=FREE_OBJ, faults=array(TEXT, 0, 16), removed_samples=array(FREE_OBJ, 0, 10000))), {}, _BODY,
    '评测者读取过程真值：空气温湿度与露点、金属与玻璃温度、蒸发器与冰、凝水、加湿模块、废水罐、控制器积分、功率，样品温度、含水与状态', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '腔内能量（J）与湿度系统水量（kg）的守恒账、电能', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='door_heating_failure', active=True), _BODY,
    '故障注入：加热器、压缩机、加湿模块、风机、温度/湿度/安全传感器、门传感器、门加热失效，废水管堵塞，市电中断', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * DAY))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'week_program.run': '周程序（5个×100切换点）本运行层未实现；只实现定值、时间程序与Idle模式。',
    'alarm_relay.read': '无源报警继电器输出为选件（数据表OPTIONS；未选）。', 'analog_output.read': '4–20 mA模拟输出为选件（未选）。',
    'door_lock.set': '可锁门把手为选件（未选）。', 'light.set': 'LED灯条为附件（未选）。', 'rs485.command': 'RS 485接口为选件（未选；以太网APT-COM为标配但本运行层不建模通信）。',
    'pt100_object.read': '附加柔性Pt100为选件（未选）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'cabinet', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'cabinet', '环境条件是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'power_switch', '主电源开关是否接通')
slot('state.current', enum(STATES), '1', 'derived', 'touchscreen',
     '状态：off、outage（市电中断）、idle、fixed（定值）、program、program_paused（超出程序公差带暂停）、door_off（开门60 s后全部功能关闭）')
slot('temperature.chamber_k', nullable(number('K')), 'K', 'sensor', 'touchscreen', 'Pt100测得的腔温（无电或传感器故障时为空）')
slot('humidity.chamber', nullable(number('1')), '1', 'sensor', 'touchscreen', '电容传感器测得的腔内相对湿度（0–1；湿度控制关时仍显示）')
slot('display.temperature', nullable(number('1')), '1', 'derived', 'touchscreen', '触摸屏温度数字（°C，0.1位）')
slot('display.humidity', nullable(number('1')), '1', 'derived', 'touchscreen', '触摸屏湿度数字（%RH，0.1位）')
slot('setpoint.current', FREE_OBJ, '1', 'setpoint', 'touchscreen', '当前有效设定：温度、湿度（湿度控制关时为空）、风机、模式与来源（定值或程序段）')
slot('setpoint.fixed', FREE_OBJ, '1', 'setpoint', 'touchscreen', '定值模式的设定（程序结束后恢复）')
slot('functions.current', FREE_OBJ, '1', 'setpoint', 'touchscreen', '控制器功能：湿度控制、Idle mode')
slot('chart.region', enum(['A', 'B', 'outside', 'dry']), '1', 'derived', 'touchscreen', '当前设定在气候图中的区域：A保证区、B限时区（最长24 h）、outside、dry（不控湿）')
slot('program.status', FREE_OBJ, '1', 'derived', 'touchscreen', '程序运行状态：程序号、段号、段内时间、重复计数、是否暂停、已存程序号')
slot('safety.settings', FREE_OBJ, '1', 'setpoint', 'touchscreen', '安全控制器模式与值、生效的绝对上限')
slot('tolerance.settings', FREE_OBJ, '1', 'setpoint', 'touchscreen', '公差带报警设定（温度、湿度、延时）')
slot('alarms.active', array(TEXT, 0, 16), '1', 'derived', 'touchscreen',
     '报警：TEMPERATURE_RANGE、HUMIDITY_RANGE、DOOR_OPEN、SAFETY_CONTROLLER、SAFETY_SENSOR、TEMPERATURE_SENSOR、HUMIDITY_SENSOR、DOOR_SENSOR、FRESHWATER_SUPPLY、'
     'FRESHWATER_CAN_EMPTY、WASTEWATER、HUMIDITY_SYSTEM、OVERTEMPERATURE_CLASS1、POWER_FAILURE')
slot('notifications.active', array(TEXT, 0, 16), '1', 'derived', 'touchscreen',
     '信息图标：Idle mode、Temperature range、Humidity range、Humidity off、Door open、Humidity module service；以及气候图提示CLIMATE_CHART_B（限时区）、'
     'CLIMATE_CHART_B_24H（限时区已连续运行24 h）、CLIMATE_CHART_OUTSIDE（设定在气候图之外，精度不保证）——气候图提示为运行层对数据表气候图的表示，非控制器原有图标')
slot('alarm.audible', BOOL, '1', 'derived', 'touchscreen', '报警蜂鸣（蜂鸣器开且有未静音报警）')
slot('indicator.pilot', BOOL, '1', 'derived', 'pilot_lamp', '三角仪表面板上的电源指示灯（主开关接通且有市电）')
slot('indicator.alarm_header', BOOL, '1', 'derived', 'alarm_header', '触摸屏标题栏红色闪烁（报警状态）')
slot('indicator.door_open', BOOL, '1', 'derived', 'door_icon', '触摸屏“Door open”信息图标')
slot('indicator.water', BOOL, '1', 'derived', 'water_icon', '触摸屏供水/废水报警图标（Freshwater supply、Wastewater）')
slot('door.outer_angle_rad', number('rad'), 'rad', 'setpoint', 'outer_door', '外门开启角（0关闭）')
slot('door.glass_angle_rad', number('rad'), 'rad', 'setpoint', 'glass_door', '玻璃内门开启角（0关闭）')
slot('door.glass_fogged', BOOL, '1', 'derived', 'glass_door', '玻璃内门内侧可见起雾（凝水膜>2 g/m²）')
slot('water.supply_open', BOOL, '1', 'setpoint', 'freshwater_inlet', '进水龙头/水桶是否接通')
slot('water.can_kg', nullable(number('kg')), 'kg', 'derived', 'freshwater_inlet', '选配水桶剩余水量（水管供水时为空；桶上可见液位）')
slot('samples.count', integer(0, 200), '1', 'derived', 'shelves', '腔内样品数（宿主声明）')
slot('logger.settings', FREE_OBJ, '1', 'setpoint', 'touchscreen', '数据记录器存储间隔与记录条数')
slot('truth.climate', FREE_OBJ, '1', 'process_truth', 'cabinet', '空气温湿度与露点、金属、玻璃、蒸发器、冰、凝水、加湿与废水、控制器与功率', 'evaluator')
slot('truth.samples', FREE_OBJ, '1', 'process_truth', 'shelves', '样品温度、含水、状态与暴露统计', 'evaluator')
slot('truth.power', FREE_OBJ, '1', 'process_truth', 'cabinet', '电功率分项与电流', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'cabinet', '已注入的故障', 'evaluator')
_INDS = ['indicator.pilot', 'indicator.alarm_header', 'indicator.door_open', 'indicator.water']
_DOORS = ['door.outer_angle_rad', 'door.glass_angle_rad']
SCENE_MIRRORED = set(['display.temperature', 'display.humidity'] + _INDS + _DOORS)
ALARM_TEXT = {'TEMPERATURE_RANGE': '温度超出公差带', 'HUMIDITY_RANGE': '湿度超出公差带', 'DOOR_OPEN': '门已开5 min', 'SAFETY_CONTROLLER': '超过安全控制器上限（加热关闭）',
              'SAFETY_SENSOR': '安全控制器传感器故障', 'TEMPERATURE_SENSOR': '温度传感器故障', 'HUMIDITY_SENSOR': '湿度传感器故障', 'DOOR_SENSOR': '门传感器故障',
              'FRESHWATER_SUPPLY': '加湿模块无法注水（龙头关或进水阀故障）', 'FRESHWATER_CAN_EMPTY': '水桶已空', 'WASTEWATER': '废水罐无法排空（废水管堵塞）',
              'HUMIDITY_SYSTEM': '加湿模块故障（不得确认，须停机联系服务）', 'OVERTEMPERATURE_CLASS1': '1级过温保护断开加热', 'POWER_FAILURE': '停电'}
NO_ACK = {'HUMIDITY_SYSTEM'}


# ---------------------------------------------------------------- runtime
class BinderKbf115Runtime:
    def __init__(self, instance_id='binder_kbf115_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.main_switch = False
        self.mode = 'fixed'
        self.fixed = dict(temperature_k=T0 + 25.0, relative_humidity=0.60, fan_fraction=1.0)
        self.functions = dict(humidity_control=True, idle_mode=False)
        self.safety = dict(mode='offset', value_k=2.0)
        self.tol = dict(temperature_k=3.0, relative_humidity=0.10, delay_s=900.0)
        self.buzzer = True
        self.programs = {}
        self.prog = None                                   # running program state
        self.log_interval = DOC['storage_interval_s']
        self.door = self.glass = 0.0
        self.door_since = None
        self.supply_open = True
        self.can_kg = None
        self.samples = {}
        self.faults = set()
        self.alarms, self.alarm_since, self.muted, self.ended = [], {}, set(), set()
        self.events, self.events_dropped = [], 0
        self.log, self.next_log = [], 0.0
        self.revision = 0
        self.Ta = self.Ts = self.Tg = self.Tp = None
        self.mv = None
        self.RHp = None
        self.T_plate = self.T_dehum = None
        self.ice_p = self.ice_d = 0.0                       # ice on the cooling plate / on the dehumidification evaporator
        self.fog = 0.0                                     # kg water film on the glass door
        self.hum_kg = 0.0                                  # water in the steam container
        self.hum_ready = 0.0                               # 0..1 warm-up of the steam module
        self.hum_wait = 0.0                                # remaining start-up delay after power-on (E7: about 20 min)
        self.mains_prev = False
        self.rh_out_s = 0.0                                # time the humidity error has stayed outside the integral band
        self.t_out_s = 0.0                                 # time the temperature error has stayed outside the integral band
        self.d_filt = 0.0                                  # dehumidification demand after the evaporator response (kg/s)
        self.comp_prev = None                              # compressor state of the last controlling tick (bumpless transfer of the base cooling)
        self.cold = 0.0                                    # 0..1 cold state of the cooling-plate evaporator (lags the compressor)
        self.removed = []                                  # full reports of removed samples (evaluator truth)
        self.waste = 0.0
        self.I_T = self.I_RH = 0.0
        self.heat_out = self.heat_store = 0.0
        self.reached_at = [0.0, 0.0]
        self.reached_T = self.reached_RH = False
        self.safety_trip = False
        self.class1_trip = False
        self.service_s = 0.0
        self.P = dict(heater=0.0, fan=0.0, compressor=0.0, humidifier=0.0, control=0.0, door_heating=0.0, pump=0.0)
        self.Q_cool = 0.0
        self.steam = 0.0
        self.dehum = False
        self.led = dict(e_initial_j=0.0, e_heater_in_j=0.0, e_fan_in_j=0.0, e_door_heating_in_j=0.0, e_wall_in_j=0.0, e_glass_in_j=0.0, e_exchange_in_j=0.0,
                        e_steam_in_j=0.0, e_sample_heat_in_j=0.0, e_refrigeration_out_j=0.0, e_drain_out_j=0.0, e_samples_added_j=0.0, e_samples_removed_j=0.0,
                        w_supply_in_kg=0.0, w_exchange_in_kg=0.0, w_pumped_out_kg=0.0, w_overflow_kg=0.0, w_samples_added_kg=0.0, w_samples_removed_kg=0.0,
                        w_initial_kg=0.0, w_can_refill_kg=0.0, electric_energy_j=0.0, e_reconfigure_in_j=0.0)
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
        return BinderKbf115Session(self, name)

    def _event(self, kind, code, message):
        self.events.append(dict(time_s=round(self.t, 6), kind=kind, code=code, message_zh=message))
        if len(self.events) > MAX_EVENTS_LOG:
            self.events_dropped += len(self.events) - MAX_EVENTS_LOG
            del self.events[:-MAX_EVENTS_LOG]

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    # ---- derived quantities
    def _mains_ok(self):
        return self.plant is not None and self.main_switch and 'mains_outage' not in self.faults

    def _V(self):
        return ENG['air_volume_m3']

    def _C_dry(self):
        return rho_moist(self.plant['ambient_k'], self.plant['pressure_pa'], 0.0) * self._V() * CP_AIR

    def _pv(self):
        return self.mv / self._V() * R_VAP * self.Ta

    def _rh(self):
        return self._pv() / p_sat(self.Ta)

    def _door_open(self):
        return self.door > 0.02

    def _door_seen(self):
        return self._door_open() and 'door_sensor_failure' not in self.faults

    def _door_off(self):
        return self._door_seen() and self.door_since is not None and self.t - self.door_since >= DOC['door_open_behaviour']['all_off_after_s'] - 1e-9

    def _setpoint(self):
        """Active set point: fixed value or the running program section (ramp / step)."""
        if self.mode == 'program' and self.prog is not None:
            P = self.programs[self.prog['program']]
            secs = P['sections']
            i = self.prog['section']
            s = secs[i]
            T, rh = s['temperature_k'], s['relative_humidity']
            if s['transition'] == 'ramp' and i + 1 < len(secs):
                f = min(self.prog['elapsed'] / s['duration_s'], 1.0)
                T += f * (secs[i + 1]['temperature_k'] - T)
                rh += f * (secs[i + 1]['relative_humidity'] - rh)
            return dict(temperature_k=T, relative_humidity=rh, fan_fraction=s.get('fan_fraction', 1.0), humidity_control=s.get('humidity_control', True) and self.functions['humidity_control'],
                        source=f'program {self.prog["program"]} section {i + 1}', tolerance_k=s.get('tolerance_k'), tolerance_rh=s.get('tolerance_rh'))
        return dict(self.fixed, humidity_control=self.functions['humidity_control'], source='fixed', tolerance_k=None, tolerance_rh=None)

    def _controlling(self):
        return self._mains_ok() and not self.functions['idle_mode'] and self.mode != 'idle'

    def _humidity_active(self, sp):
        T = sp['temperature_k']
        return sp['humidity_control'] and DOC['humidity_off_below_k'] <= T <= T0 + 95.0

    def _sensor_T(self):
        if not self._mains_ok() or 'temperature_sensor_failure' in self.faults:
            return None
        return self.Tp + ENG['display_noise_k'] * _gauss(self.seed, self.tick, 1)

    def _sensor_RH(self):
        if not self._mains_ok() or 'humidity_sensor_failure' in self.faults:
            return None
        return min(max(self.RHp + ENG['display_noise_rh'] * _gauss(self.seed, self.tick, 2), 0.0), 1.0)

    def _state(self):
        if self.plant is None or not self.main_switch:
            return 'off'
        if not self._mains_ok():
            return 'outage'
        if self.functions['idle_mode'] or self.mode == 'idle':
            return 'idle'
        if self._door_off():
            return 'door_off'
        if self.mode == 'program' and self.prog is not None:
            return 'program_paused' if self.prog['paused'] else 'program'
        return 'fixed'

    def _q_max(self, Ta):
        return max(ENG['cooling_capacity_40c_w'] * (1.0 + ENG['cooling_capacity_slope_per_k'] * (Ta - (T0 + 40.0))), 0.0)

    def _sample_cap(self, s):
        return s['dry_mass_kg'] * s['cp'] + s['container_mass_kg'] * 840.0 + (s['water_kg'] + s['film_kg']) * CP_WATER

    def _U(self):
        """Chamber energy (J; reference: dry air, liquid water and steel at 0 C)."""
        U = (self._C_dry() + self.mv * CP_VAP) * (self.Ta - T0) + self.mv * L0
        U += ENG['steel_heat_capacity_j_k'] * (self.Ts - T0) + ENG['door_glass_heat_capacity_j_k'] * (self.Tg - T0) + self.fog * CP_WATER * (self.Tg - T0)
        U -= self.ice * L_FUS
        U += self.heat_store                                                 # heat held in the heater element (above its idle state)
        U += sum(self._sample_cap(s) * (s['T'] - T0) for s in self.samples.values())
        return U

    def _W(self):
        """Water held in the humidity system (kg): vapour, glass film, evaporator ice, steam container, wastewater can, the can, samples."""
        return self.mv + self.fog + self.ice + self.hum_kg + self.waste + (self.can_kg or 0.0) + sum(s['water_kg'] + s['film_kg'] for s in self.samples.values())

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
        self.t = t_end
        return self.t

    def _sample_surface(self, s, T, rh_air):
        """Surface water activity of a sample at temperature T (None: no exchange from the bulk, only the condensate film)."""
        sub = s['substance_id']
        if sub == 'inert':
            return None
        S = SORPTION[sub]
        if S['kind'] == 'adsorbent':
            return None                                                      # adsorbents are handled as first-order uptake below
        d = drh(sub, T)
        b = S['solution_branch']
        if s['water_kg'] <= 1e-12:
            if d is not None and rh_air >= d:
                s['state'] = 'deliquescing'
                return d
            if d is None and b and rh_air >= b['water_activity'][-1]:
                s['state'] = 'deliquescing'
                return b['water_activity'][-1]
            s['state'] = 'dry' if d is not None else 'unknown'
            return None
        if not b:                                                            # salt without a measured solution branch: amount not quantified
            s['state'] = 'deliquesced_unquantified'
            return None
        n = s['dry_mass_kg'] / b['solute_molar_mass_kg_mol']
        m = n / s['water_kg']
        aw, q = branch_aw(sub, m)
        if q == 'measured_limit':                                            # beyond the most concentrated measured point: unknown, no further drying
            s['state'] = 'solution_at_measured_limit'
            return aw if rh_air >= aw else None
        if d is not None and aw < d:                                         # saturated: solid and saturated solution coexist
            s['state'] = 'saturated_solution'
            return d
        s['state'] = 'solution' if q == 'measured' else 'solution_extrapolated'
        return aw

    def _tick(self, h):
        L = self.led
        Tamb, pa, rh_amb = self.plant['ambient_k'], self.plant['pressure_pa'], self.plant['relative_humidity']
        V = self._V()
        Cd = self._C_dry()
        mains = self._mains_ok()
        if mains and not self.mains_prev:
            self.hum_wait, self.hum_ready = DOC['humidity_startup_delay_s'], 0.0
        self.mains_prev = mains
        if mains and self.hum_wait > 0.0:                                     # the start-up delay runs whether or not humidity control is on
            self.hum_wait = max(self.hum_wait - h, 0.0)
        ctrl = self._controlling()
        sp = self._setpoint()
        door_open = self._door_open()
        door_seen = self._door_seen()
        door_off = self._door_off()
        run = ctrl and not door_off
        # ---- fan
        fan = 0.0
        if mains and not door_off and 'fan_failure' not in self.faults and not (self.functions['idle_mode'] or self.mode == 'idle'):
            fan = FAN_LO if door_seen else sp['fan_fraction']
        fh = max(fan, 0.15) ** 0.8                                         # forced convection factor (natural convection floor)
        # ---- sensors (first order)
        pv = self._pv()
        rh_air = pv / p_sat(self.Ta)
        self.Tp += (self.Ta - self.Tp) * (1.0 - math.exp(-h / ENG['t_sensor_tau_s']))
        self.RHp += (min(rh_air, 1.0) - self.RHp) * (1.0 - math.exp(-h / ENG['rh_sensor_tau_s']))
        sT, sRH = self._sensor_T(), self._sensor_RH()
        hum_on = run and self._humidity_active(sp) and 'humidity_module_failure' not in self.faults
        # ---- temperature controller
        T_set = sp['temperature_k']
        heater = 0.0
        q_extra = 0.0
        comp = run and T_set <= ENG['compressor_on_below_k'] and 'compressor_failure' not in self.faults
        cold0 = self.cold                                                    # evaporator cold mass: the base cooling follows the compressor with a lag
        self.cold += ((1.0 if comp else 0.0) - self.cold) * (1.0 - math.exp(-h / ENG['evaporator_tau_s']))
        if run and self.comp_prev is not None:                               # bumpless transfer: the base cooling the integral offsets follows the evaporator
            self.I_T = min(max(self.I_T + (self.cold - cold0) * ENG['base_cooling_w'] * fh, -self._q_max(self.Ta) if comp else 0.0), ENG['heater_max_w'])
        self.comp_prev = comp if run else None
        if run and sT is not None:
            e = T_set - sT
            kp = ENG['t_controller_gain_w_k'] * fh                            # gain follows the air-side heat transfer (fan speed): no overshoot at low fan
            lo = -self._q_max(self.Ta) if comp else 0.0
            u0 = kp * e + self.I_T
            self.t_out_s = 0.0 if abs(e) <= ENG['t_integral_band_k'] else self.t_out_s + h
            if (abs(e) <= ENG['t_integral_band_k'] or self.t_out_s >= ENG['t_integral_hold_s']) \
                    and not ((u0 >= ENG['heater_max_w'] and e > 0.0) or (u0 <= lo and e < 0.0)):   # integral separation (released after a hold), anti-windup
                self.I_T = min(max(self.I_T + kp / ENG['t_controller_integral_s'] * e * h, lo), ENG['heater_max_w'])
            u = kp * e + self.I_T
            if u >= 0.0:
                heater = min(u, ENG['heater_max_w'])
            elif comp:
                q_extra = -u
        else:
            self.I_T, self.t_out_s = 0.0, 0.0
        if self.safety_trip or self.class1_trip or 'heater_failure' in self.faults:
            heater = 0.0
        heater_el = heater                                                   # electrical power drawn by the element
        heater = self.heat_out                                               # heat released by the element (first-order thermal lag)
        self.heat_out += (heater_el - self.heat_out) * (1.0 - math.exp(-h / ENG['heater_tau_s']))
        self.heat_store += h * (heater_el - heater)
        # ---- humidity controller: one continuous output, steam (> 0) or a dehumidification demand on the evaporator (< 0), in kg/s
        self.steam, self.dehum = 0.0, False
        d_dem = 0.0
        if hum_on and sRH is not None and sT is not None:
            e = sp['relative_humidity'] - sRH * p_sat(sT) / p_sat(T_set)          # humidity referred to the set temperature: controls the vapour content
            kp = V * rho_sat(self.Ta) / ENG['rh_control_time_s']                 # kg/s per unit RH: closes the loop in about rh_control_time_s
            d_max = ENG['dehum_evaporator_ua_w_k'] / (rho_moist(self.Ta, pa, pv) * CP_AIR) * self.mv / V
            v0 = kp * e + self.I_RH
            self.rh_out_s = 0.0 if abs(e) <= ENG['rh_integral_band'] else self.rh_out_s + h
            s_lim = min(ENG['steam_max_kg_s'] * self.hum_ready, self.hum_kg / h)
            if not ((v0 >= s_lim and e > 0.0) or (v0 <= -d_max and e < 0.0) or (v0 < 0.0 and not comp)):   # integral separation (slow outside the band), anti-windup
                f_i = 1.0 if abs(e) <= ENG['rh_integral_band'] or self.rh_out_s >= ENG['rh_integral_hold_s'] else 0.0
                self.I_RH = min(max(self.I_RH + f_i * kp / ENG['rh_controller_integral_s'] * e * h, -d_max), ENG['steam_max_kg_s'])
            v = kp * e + self.I_RH
            if v > 0.0:
                self.steam = min(v, ENG['steam_max_kg_s'] * self.hum_ready, self.hum_kg / h)
            elif v < 0.0 and comp:
                d_dem = -v
            if self.hum_wait <= 0.0:                                         # first switch-on / power interruption: about 20 min before humidification
                self.hum_ready = min(self.hum_ready + h / DOC['humidifier_restart_s'], 1.0)
        else:
            self.I_RH, self.rh_out_s = 0.0, 0.0
            self.hum_ready = max(self.hum_ready - h / DOC['humidifier_restart_s'], 0.0) if not hum_on else self.hum_ready
        # ---- evaporator
        # two evaporators: the cooling plate (DCT base cooling plus extra cooling) and the dehumidification evaporator behind the rear wall
        # (E7); vapour transfer to both follows the Lewis relation h_m = UA / (rho c_p); ice on each lowers its own conductance
        lew = 1.0 / (rho_moist(self.Ta, pa, pv) * CP_AIR)
        ua_c = ENG['evaporator_ua_w_k'] * fh / (1.0 + self.ice_p / ENG['evaporator_ice_limit_kg'])
        ua_d = ENG['dehum_evaporator_ua_w_k'] * fh / (1.0 + self.ice_d / ENG['evaporator_ice_limit_kg'])
        qmax = self._q_max(self.Ta)
        Tc = Td = self.Ta
        m_c = m_d = 0.0
        self.d_filt += (d_dem - self.d_filt) * (1.0 - math.exp(-h / ENG['dehum_response_s']))   # evaporator / expansion-valve response: no step-to-step switching
        cool_on = self.cold > 1e-9
        if cool_on:
            q_base = ENG['base_cooling_w'] * fh * self.cold                 # DCT hot-gas bypass holds the plate-to-air difference at any fan speed
            Tc = max(self.Ta - min(q_base + q_extra, qmax * self.cold) / max(ua_c, 1e-9), ENG['evaporator_min_k'])   # a warm evaporator cools only as it gets cold
            if comp and self.d_filt > 1e-9:                                           # dehumidification evaporator temperature that condenses the demanded flow
                rho_t = self.mv / V - self.d_filt / max(ua_d * lew, 1e-12)
                T_d = ENG['evaporator_min_k']
                if rho_t > rho_sat(ENG['evaporator_min_k']):
                    T_d = dew_point(rho_t * R_VAP * self.Ta)
                    for _ in range(3):
                        T_d = dew_point(rho_t * R_VAP * T_d)
                Td = max(min(T_d, self.Ta), ENG['evaporator_min_k'])
                self.dehum = Td < self.Ta
            def latent(m, T):
                return m / h * (L0 + CP_VAP * (self.Ta - T0) + (L_FUS if T < T0 else -CP_WATER * (T - T0)))
            # dehumidification first: its load takes capacity, the plate (bypassed base cooling + extra cooling) gets what is left
            m_d = max(ua_d * lew * (self.mv / V - rho_sat(Td)), 0.0) * h if self.dehum else 0.0
            load_d = (ua_d * (self.Ta - Td) + latent(m_d, Td)) if self.dehum else 0.0
            if load_d > qmax:
                f = qmax / load_d
                Td = self.Ta - f * (self.Ta - Td)
                m_d = max(ua_d * lew * (self.mv / V - rho_sat(Td)), 0.0) * h
                load_d = ua_d * (self.Ta - Td) + latent(m_d, Td)
            room = max(qmax * self.cold - load_d, 0.0)
            sens_d = ua_d * (self.Ta - Td) if self.dehum else 0.0              # the dehumidification evaporator's sensible cooling counts toward the cooling demand
            Tc = max(self.Ta - min(max(q_base + q_extra - sens_d, 0.0), room) / max(ua_c, 1e-9), ENG['evaporator_min_k'])
            for _ in range(3):
                m_c = max(ua_c * lew * (self.mv / V - rho_sat(Tc)), 0.0) * h
                load_c = ua_c * (self.Ta - Tc) + latent(m_c, Tc)
                if load_c <= room * (1.0 + 1e-9) + 1e-9:
                    break
                Tc = self.Ta - (self.Ta - Tc) * room / load_c                  # condensate on the plate counts against the capacity too
        self.T_plate = Tc if cool_on else None
        self.T_dehum = Td if (comp and self.dehum) else None
        G_c = ua_c if cool_on else 0.0
        G_d = ua_d if (comp and self.dehum) else 0.0
        m_c, m_d = min(m_c, 0.3 * self.mv), min(m_d, 0.3 * self.mv)
        melt_p = melt_d = 0.0                                                 # ice melts with heat drawn from the air where its evaporator is warm
        if self.Ta > T0:
            cap = ENG['ice_melt_ua_w_k'] * fh * (self.Ta - T0) * h / L_FUS
            if self.ice_p > 0.0 and (not comp or Tc >= T0):
                melt_p = min(cap, self.ice_p)
            if self.ice_d > 0.0 and (not (comp and self.dehum) or Td >= T0):
                melt_d = min(cap, self.ice_d)
        melt = melt_p + melt_d
        # ---- air exchange (gasket leak always; open doors: buoyant doorway flow and door-swing pumping)
        pv_amb = rh_amb * p_sat(Tamb)
        rho_v_amb = pv_amb / (R_VAP * Tamb)
        vdot = ENG['leak_flow_m3_s']
        if door_open:
            fo = min(self.door / ENG['door_full_rad'], 1.0)
            fg = max(min(self.glass / ENG['door_full_rad'], 1.0), 0.05)
            r_in, r_out = rho_moist(self.Ta, pa, pv), rho_moist(Tamb, pa, pv_amb)
            vdot += ENG['door_discharge_coefficient'] * W_INT * H_INT / 3.0 * math.sqrt(G_ACC * H_INT * abs(r_in - r_out) / (0.5 * (r_in + r_out))) * fo * fg
        vex = vdot * h + self._pump_due
        self._pump_due = 0.0
        vex = min(vex, V)
        rho_ex = rho_moist(Tamb, pa, pv_amb)
        m_in = rho_v_amb * vex                                              # vapour entering
        m_out = self.mv / V * vex                                           # vapour leaving
        # ---- condensation / evaporation on the steel walls (drained) and the glass door film
        hm = 0.010 * fh
        m_wall = max(hm * A_WALL * (self.mv / V - rho_sat(self.Ts)), 0.0) * h
        m_glass = hm * A_GLASS * (self.mv / V - rho_sat(self.Tg)) * h
        if m_glass < 0.0:
            m_glass = max(m_glass, -self.fog)
        # ---- samples: heat and vapour exchange
        rows = []
        for s in self.samples.values():
            G = ENG['sample_h_w_m2_k'] * fh * s['area']
            hm_s = ENG['sample_hm_m_s'] * fh * s['area']
            Tloc = self.Ta + ENG['spatial_offset_k'][s['shelf'] - 1] / max(fan, FAN_LO)
            rh_loc = min(self.mv / V / rho_sat(Tloc), 1.0)
            # condensate film: grows below the dew point, evaporates otherwise
            film = hm_s * (self.mv / V - rho_sat(s['T'])) * h
            if film < 0.0:
                film = max(film, -s['film_kg'])
            bulk = 0.0
            sub = s['substance_id']
            if sub != 'inert' and SORPTION[sub]['kind'] == 'adsorbent':
                ad = SORPTION[sub]['adsorption']
                q_eq = _interp(ad['relative_humidity'], ad['uptake_kg_kg'], min(rh_loc, 1.0))
                bulk = (q_eq * s['dry_mass_kg'] - s['water_kg']) * (1.0 - math.exp(-h / ENG['adsorbent_tau_s']))
                s['state'] = 'adsorbing' if bulk > 0 else 'desorbing' if bulk < 0 else 'equilibrium'
            else:
                aw = self._sample_surface(s, s['T'], rh_loc)
                if aw is not None:
                    bulk = hm_s * (self.mv / V - aw * rho_sat(s['T'])) * h
                    if bulk < 0.0:
                        bulk = max(bulk, -s['water_kg'])
                    if s['water_kg'] <= 1e-12 and bulk < 0.0:
                        bulk = 0.0
            rows.append((s, G, Tloc, film, bulk))
        # ---- vapour mass balance of the air
        sinks = m_out + m_c + m_d + m_wall + max(m_glass, 0.0) + sum(max(f, 0.0) + max(b, 0.0) for _, _, _, f, b in rows)
        scale = 1.0
        avail = self.mv + m_in + self.steam * h + max(-m_glass, 0.0) + sum(max(-f, 0.0) + max(-b, 0.0) for _, _, _, f, b in rows)
        if sinks > 0.9 * avail:
            scale = 0.9 * avail / sinks
            m_out, m_c, m_d, m_wall = m_out * scale, m_c * scale, m_d * scale, m_wall * scale
            m_glass = m_glass * scale if m_glass > 0 else m_glass
            rows = [(s, G, Tl, f * scale if f > 0 else f, b * scale if b > 0 else b) for s, G, Tl, f, b in rows]
        m_steam = self.steam * h
        mv_new = self.mv + m_in - m_out + m_steam - m_c - m_d - m_wall - m_glass - sum(f + b for _, _, _, f, b in rows)
        # ---- heat inputs
        P_fan = ENG['fan_power_w'] * fan ** 3
        hw = 1.0 - (1.0 - ENG['heater_wall_fraction']) * min(fan / FAN_LO, 1.0)    # without the fan the element heats the preheating-chamber walls
        G_as = ENG['air_steel_ua_w_k'] * fh
        G_ag = ENG['door_glass_air_ua_w_k'] * fh
        gx = rho_ex * vex / h * CP_AIR                                      # W/K, dry-air exchange
        # incoming vapour sensible enthalpy (relative to 0 C) at its source temperature; outgoing vapour leaves at the new air temperature
        h_in = m_in * CP_VAP * (Tamb - T0) + m_steam * CP_VAP * (T_STEAM - T0) + max(-m_glass, 0.0) * CP_VAP * (self.Tg - T0) + \
            sum(max(-f, 0.0) * CP_VAP * (s['T'] - T0) + max(-b, 0.0) * CP_VAP * (s['T'] - T0) for s, _, _, f, b in rows)
        m_left = m_out + m_c + m_d + m_wall + max(m_glass, 0.0) + sum(max(f, 0.0) + max(b, 0.0) for _, _, _, f, b in rows)
        Gs_sum = sum(G for _, G, _, _, _ in rows)
        GsT = sum(G * s['T'] for s, G, _, _, _ in rows)
        # implicit energy balance of dry air + vapour: (Cd + (mv + inflow) cp_v) x' = (Cd + mv cp_v) x + inflow sensible + h sum G (T_k - T0) ... (x = Ta - T0)
        num = (Cd + self.mv * CP_VAP) * (self.Ta - T0) + h_in - melt * L_FUS + \
            h * ((1.0 - hw) * heater + P_fan + G_as * (self.Ts - T0) + G_ag * (self.Tg - T0) + gx * (Tamb - T0) + G_c * (Tc - T0) + G_d * (Td - T0)
                 + (GsT - Gs_sum * T0))
        den = Cd + (mv_new + m_left) * CP_VAP + h * (G_as + G_ag + gx + G_c + G_d + Gs_sum)
        Ta_new = T0 + num / den
        # ---- ledgers of the air step
        L['e_heater_in_j'] += h * heater_el
        L['e_fan_in_j'] += h * P_fan
        L['e_exchange_in_j'] += h * gx * (Tamb - Ta_new) + m_in * (L0 + CP_VAP * (Tamb - T0)) - m_out * (L0 + CP_VAP * (Ta_new - T0))
        L['e_steam_in_j'] += m_steam * (L0 + CP_VAP * (T_STEAM - T0))
        h_v = L0 + CP_VAP * (Ta_new - T0)                                   # enthalpy of vapour leaving the air
        # evaporator: sensible + latent removed by the refrigerant; condensate drains (liquid) or stays as ice
        q_ev = h * (G_c * (Ta_new - Tc) + G_d * (Ta_new - Td))
        for m, T, store in ((m_c, Tc, 'ice_p'), (m_d, Td, 'ice_d')):
            if m <= 0.0:
                continue
            if T < T0:
                q_ev += m * (h_v + L_FUS)
                setattr(self, store, getattr(self, store) + m)
            else:
                q_ev += m * (h_v - CP_WATER * (T - T0))
                self.waste += m
                L['e_drain_out_j'] += m * CP_WATER * (T - T0)
        L['e_refrigeration_out_j'] += q_ev
        self.Q_cool = q_ev / h
        # ice melting with heat from the air (melt water drains at 0 C, i.e. with zero enthalpy)
        self.ice_p -= melt_p
        self.ice_d -= melt_d
        self.waste += melt
        # steel: heater share, air, room, door heating -> glass, wall condensate (drained)
        Cs = ENG['steel_heat_capacity_j_k']
        Ts_old = self.Ts
        Gc = ENG['sample_contact_w_k']
        q_s = h * (hw * heater + G_as * (Ta_new - self.Ts) + ENG['cabinet_ua_w_k'] * (Tamb - self.Ts) + sum(Gc * (s['T'] - self.Ts) for s, _, _, _, _ in rows))
        L['e_wall_in_j'] += h * ENG['cabinet_ua_w_k'] * (Tamb - self.Ts)
        if m_wall > 0.0:
            q_s += m_wall * (h_v - CP_WATER * (self.Ts - T0))
            self.waste += m_wall
            L['e_drain_out_j'] += m_wall * CP_WATER * (self.Ts - T0)
        self.Ts += q_s / Cs
        # glass door: air, room, door heating, fog film
        Cg = ENG['door_glass_heat_capacity_j_k'] + self.fog * CP_WATER
        P_dh = ENG['door_heating_w'] if (mains and 'door_heating_failure' not in self.faults) else 0.0
        g_amb = ENG['door_glass_ua_w_k'] * (6.0 if door_open else 1.0)
        q_g = h * (G_ag * (Ta_new - self.Tg) + g_amb * (Tamb - self.Tg) + P_dh)
        L['e_glass_in_j'] += h * g_amb * (Tamb - self.Tg)
        L['e_door_heating_in_j'] += h * P_dh
        if m_glass > 0.0:
            q_g += m_glass * h_v                                            # vapour enthalpy into the glass (film stored at the glass temperature)
        elif m_glass < 0.0:
            q_g += m_glass * (L0 + CP_VAP * (self.Tg - T0))                 # evaporation takes the vapour enthalpy at the glass temperature
        U_g = Cg * (self.Tg - T0) + q_g
        self.fog = max(self.fog + m_glass, 0.0)
        self.Tg = T0 + U_g / (ENG['door_glass_heat_capacity_j_k'] + self.fog * CP_WATER)
        # samples
        for s, G, Tloc, f, b in rows:
            C0s = self._sample_cap(s)
            q = h * (G * (Ta_new - s['T']) + s['heat_w'] + Gc * (Ts_old - s['T']))     # contact with the wire rack (steel node)
            L['e_sample_heat_in_j'] += h * s['heat_w']
            for m in (f, b):
                q += m * h_v if m > 0.0 else m * (L0 + CP_VAP * (s['T'] - T0))
            s['film_kg'] = max(s['film_kg'] + f, 0.0)
            s['water_kg'] = max(s['water_kg'] + b, 0.0)
            s['T'] = T0 + (C0s * (s['T'] - T0) + q) / self._sample_cap(s)
            if f > 0.0 and s['film_kg'] > 1e-6 and not s['condensing']:
                s['condensing'] = True
                s['condensation_events'] += 1
                self._event('truth', 'SAMPLE_CONDENSATION', f'样品{s["sample_id"]}表面凝水')
            elif s['film_kg'] <= 1e-9:
                s['condensing'] = False
        self.Ta = Ta_new
        self.mv = mv_new
        # ---- water side: steam container refill, wastewater pump
        self.hum_kg -= m_steam
        cap_kg = ENG['freshwater_reserve_kg']
        need = cap_kg - self.hum_kg
        if need > 0.0 and hum_on:
            src = self.supply_open and (self.plant['water_supply'] == 'pipe' or (self.can_kg or 0.0) > 0.0)
            if src:
                take = need if self.plant['water_supply'] == 'pipe' else min(need, self.can_kg)
                if self.plant['water_supply'] == 'can':
                    self.can_kg -= take
                else:
                    L['w_supply_in_kg'] += take
                self.hum_kg += take
        P_pump = 0.0
        if self.waste >= ENG['wastewater_pump_kg'] and mains and 'wastewater_blocked' not in self.faults:
            out = min(ENG['wastewater_pump_kg_s'] * h, self.waste)
            self.waste -= out
            L['w_pumped_out_kg'] += out
            P_pump = 30.0
        if self.waste > DOC['wastewater_tank_kg']:
            L['w_overflow_kg'] += self.waste - DOC['wastewater_tank_kg']
            self.waste = DOC['wastewater_tank_kg']
        L['w_exchange_in_kg'] += m_in - m_out
        # ---- electrical power
        P_hum = 0.0
        if hum_on or self.hum_ready > 0.0:
            P_hum = (ENG['humidifier_standby_w'] * self.hum_ready if mains else 0.0) + self.steam * (CP_WATER * (T_STEAM - ENG['humidifier_feed_k']) + (L0 + CP_VAP * 100.0 - CP_WATER * 100.0))
        self.P = dict(heater=heater_el, fan=P_fan, compressor=ENG['compressor_power_w'] if comp else 0.0, humidifier=P_hum,
                      control=ENG['control_power_w'] if mains else 0.0, door_heating=P_dh, pump=P_pump)
        L['electric_energy_j'] += h * sum(self.P.values())
        # ---- set point reached flags follow the active set point (program step, ramp or end): the safety controller and the tolerance alarms wait
        # until the new set point has been reached once (E6 10.2, E7 12.4)
        if self.reached_T and abs(T_set - self.reached_at[0]) > 0.5:
            self.reached_T = False
        if self.reached_RH and abs(sp['relative_humidity'] - self.reached_at[1]) > 0.02:
            self.reached_RH = False
        # ---- safety controller (class 3.1) and class 1 cut-off
        lim = self._safety_limit(sp)
        if mains and 'safety_sensor_failure' not in self.faults and self.reached_T and self.Ta > lim and not self.safety_trip:
            self.safety_trip = True
            self._event('alarm', 'SAFETY_CONTROLLER', '超过安全控制器上限：加热关闭')
        elif self.safety_trip and self.Ta < lim:
            self.safety_trip = False
            self._event('operation', 'SAFETY_RELEASED', '腔温降到安全值以下：加热恢复')
        if self.Ta > ENG['class1_cutoff_k'] and not self.class1_trip and mains:
            self.class1_trip = True
            self._event('alarm', 'OVERTEMPERATURE_CLASS1', '1级过温保护断开加热')
        # ---- set point reached flags, program clock
        if run and sT is not None and not self.reached_T and abs(sT - T_set) <= 0.5:
            self.reached_T = True
            self.reached_at[0] = T_set
            self._event('operation', 'SET_POINT_REACHED', '温度达到设定')
        if run and hum_on and sRH is not None and not self.reached_RH and self.hum_ready >= 1.0 and abs(sRH - sp['relative_humidity']) <= 0.02:   # humidifier fully available
            self.reached_RH = True
            self.reached_at[1] = sp['relative_humidity']
        if self.mode == 'program' and self.prog is not None and mains:
            self._advance_program(h, sp, sT, sRH)
        if hum_on:
            self.service_s += h
        reg = chart_region(sp['temperature_k'], sp['relative_humidity']) if (run and self._humidity_active(sp)) else 'dry'
        self.b_time = self.b_time + h if reg == 'B' else 0.0
        self.region = reg
        # ---- samples: exposure statistics and degradation
        for s, _, Tloc, _, _ in rows:
            T = s['T']
            rh_s = min(self.mv / V / rho_sat(T), 1.0)
            s['t_exposed_s'] += h
            s['arr_sum'] += h * math.exp(-MKT_DH / (R_GAS * T))
            s['rh_sum'] += h * rh_s
            s['T_max'], s['T_min'] = max(s['T_max'], T), min(s['T_min'], T)
            d = drh(s['substance_id'], T) if s['substance_id'] != 'inert' else None
            if d is not None and rh_s >= d:
                s['t_above_drh_s'] += h
            if s['degradation']:
                g = s['degradation']
                k = g['rate_ref_per_s'] * math.exp(-g['activation_j_mol'] / R_GAS * (1.0 / T - 1.0 / 298.15)) * (max(rh_s, 1e-6) / 0.60) ** g['humidity_exponent']
                s['degraded'] += (1.0 - s['degraded']) * (1.0 - math.exp(-k * h))
        self._update_alarms(h)
        # ---- data logger
        if mains and self.t + h >= self.next_log - 1e-9:
            sp2 = self._setpoint()
            T_r, RH_r = self._sensor_T(), self._sensor_RH()
            self.log.append(dict(time_s=round(self.t + h, 3), temperature_c=None if T_r is None else round(T_r - T0, 2),
                                 humidity_pct=None if RH_r is None else round(RH_r * 100.0, 2), set_temperature_c=round(sp2['temperature_k'] - T0, 2),
                                 set_humidity_pct=round(sp2['relative_humidity'] * 100.0, 2) if self._humidity_active(sp2) else None,
                                 door_open=self._door_open(), mode=self._state(), alarms=list(self.alarms)))
            del self.log[:-MAX_LOG]
            self.next_log = self.t + h + self.log_interval

    _pump_due = 0.0

    @property
    def ice(self):
        return self.ice_p + self.ice_d

    def _safety_limit(self, sp=None):
        sp = sp or self._setpoint()
        return self.safety['value_k'] if self.safety['mode'] == 'limit' else sp['temperature_k'] + self.safety['value_k']

    def _advance_program(self, h, sp, sT, sRH):
        p = self.prog
        P = self.programs[p['program']]
        s = P['sections'][p['section']]
        tk, tr = s.get('tolerance_k'), s.get('tolerance_rh')
        out = (tk is not None and sT is not None and abs(sT - sp['temperature_k']) > tk) or \
              (tr is not None and sRH is not None and sp['humidity_control'] and abs(sRH - sp['relative_humidity']) > tr)
        if out != p['paused']:
            p['paused'] = out
            self._event('program', 'PROGRAM_PAUSE' if out else 'PROGRAM_CONTINUE', '程序暂停（超出公差带）' if out else '回到公差带内：程序继续')
        if out:
            return
        p['elapsed'] += h
        while p['elapsed'] >= s['duration_s'] - 1e-9:
            p['elapsed'] -= s['duration_s']
            rep = P.get('repeat')
            if rep and p['section'] + 1 == rep['to_section'] and p['reps_done'] < rep['count']:
                p['reps_done'] += 1
                p['section'] = rep['from_section'] - 1
            else:
                p['section'] += 1
            if p['section'] >= len(P['sections']):
                self._event('program', 'PROGRAM_END', f'程序{p["program"]}结束：转定值模式')
                self.mode, self.prog = 'fixed', None
                return
            s = P['sections'][p['section']]
            self._event('program', 'SECTION', f'程序段{p["section"] + 1}')

    def _update_alarms(self, h=0.0, timed=True):
        """Alarm list of the touchscreen.  Grid ticks (timed=True) start and count the delayed alarms (tolerance range, door) at grid times; accepted
        commands (timed=False) re-evaluate the immediate alarms only and keep a delayed alarm while its condition holds.  An alarm whose condition
        ended stays displayed (and sounding) until it is reset, unless it was already acknowledged (then it clears by itself); the panel is dead
        during a power failure, and a POWER_FAILURE alarm is shown after the power returns."""
        if self.plant is None:
            return
        if not self._mains_ok():
            if self.main_switch:
                self._outage_seen = True
            self.notes = []
            return
        C = set()
        notes = []
        sp = self._setpoint()
        sT, sRH = self._sensor_T(), self._sensor_RH()
        ctrl = self._controlling()
        hum = self._humidity_active(sp)
        out_T = ctrl and self.reached_T and sT is not None and abs(sT - sp['temperature_k']) > self.tol['temperature_k']
        out_RH = ctrl and hum and self.reached_RH and sRH is not None and abs(sRH - sp['relative_humidity']) > self.tol['relative_humidity']
        door = self._door_seen()
        for code, cond in (('IDLE_MODE', self.functions['idle_mode'] or self.mode == 'idle'), ('TEMPERATURE_RANGE', out_T), ('HUMIDITY_RANGE', out_RH),
                           ('HUMIDITY_OFF', not hum), ('DOOR_OPEN', door), ('HUMIDITY_MODULE_SERVICE', self.service_s >= DOC['humidity_service_s']),
                           ('CLIMATE_CHART_B', hum and ctrl and chart_region(sp['temperature_k'], sp['relative_humidity']) == 'B'),
                           ('CLIMATE_CHART_B_24H', hum and ctrl and self.b_time >= 86400.0),
                           ('CLIMATE_CHART_OUTSIDE', hum and ctrl and chart_region(sp['temperature_k'], sp['relative_humidity']) == 'outside')):
            if cond:
                notes.append(code)
        for code, cond, delay in (('TEMPERATURE_RANGE', out_T, self.tol['delay_s']), ('HUMIDITY_RANGE', out_RH, self.tol['delay_s']),
                                  ('DOOR_OPEN', door, DOC['door_open_alarm_s'])):
            if not timed:
                if cond and code in self.alarms and code not in self.ended:
                    C.add(code)
                if not cond:
                    self.alarm_since.pop(code, None)
                continue
            if cond:
                since = self.alarm_since.setdefault(code, self.t + h)
                if self.t + h - since >= delay - 1e-9:
                    C.add(code)
            else:
                self.alarm_since.pop(code, None)
        for code, cond in (('SAFETY_CONTROLLER', self.safety_trip), ('SAFETY_SENSOR', 'safety_sensor_failure' in self.faults),
                           ('TEMPERATURE_SENSOR', 'temperature_sensor_failure' in self.faults), ('HUMIDITY_SENSOR', 'humidity_sensor_failure' in self.faults),
                           ('DOOR_SENSOR', 'door_sensor_failure' in self.faults), ('HUMIDITY_SYSTEM', 'humidity_module_failure' in self.faults and hum and ctrl),
                           ('OVERTEMPERATURE_CLASS1', self.class1_trip), ('WASTEWATER', self.waste >= DOC['wastewater_tank_kg'] - 1e-9),
                           ('FRESHWATER_SUPPLY', hum and ctrl and not self.supply_open and self.hum_kg < 0.9 * ENG['freshwater_reserve_kg']),   # cannot fill up (E7)
                           ('FRESHWATER_CAN_EMPTY', hum and ctrl and self.plant['water_supply'] == 'can' and (self.can_kg or 0.0) <= 0.0)):
            if cond:
                C.add(code)
        if self._outage_seen:
            self._outage_seen = False
            self._event('alarm', 'POWER_FAILURE', '报警：' + ALARM_TEXT['POWER_FAILURE'] + '（电源恢复，按断电前的模式继续）')
            if 'POWER_FAILURE' not in self.alarms:
                self.alarms.append('POWER_FAILURE')
            self.ended.add('POWER_FAILURE')
        kept = []
        for c in self.alarms:
            if c in C:
                self.ended.discard(c)
                kept.append(c)
            elif c in self.muted and c not in self.ended:
                continue                                                     # acknowledged and over: clears by itself
            else:
                self.ended.add(c)
                kept.append(c)
        new = [c for c in sorted(C) if c not in kept]
        for c in new:
            self._event('alarm', c, '报警：' + ALARM_TEXT.get(c, c))
            self.muted.discard(c)
        self.alarms = kept + new
        self.muted &= set(self.alarms)
        self.notes = notes

    notes = []
    ended = set()
    b_time = 0.0
    region = 'dry'
    _outage_seen = False

    def _audible(self):
        if not self._mains_ok() or not self.buzzer:
            return False
        return any(a not in self.muted for a in self.alarms)

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'samples.read', 'log.read'):
            self.revision += 1
        if self.plant is not None and 'alarms.active' in EFFECTS.get(capability_id, ()):
            self._update_alarms(timed=False)
        return out

    def _cmd_plant_configure(self, ambient_k, relative_humidity, pressure_pa, noise_seed, mains_v=None, water_supply=None, shelves=None):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        old = self.plant or {}
        ws = water_supply if water_supply is not None else old.get('water_supply', 'pipe')
        if not first and ws != old['water_supply']:
            raise ActionRejected('SUPPLY_FIXED')
        n_sh = shelves if shelves is not None else old.get('shelves', DOC['shelves']['std'])
        if any(s['shelf'] > n_sh for s in self.samples.values()):
            raise ActionRejected('SHELF_OCCUPIED')
        u_old = None if first else self._U()
        self.plant = dict(ambient_k=ambient_k, relative_humidity=relative_humidity, pressure_pa=pressure_pa, mains_v=mains_v if mains_v is not None else old.get('mains_v', 230.0),
                          water_supply=ws, shelves=n_sh)
        self.seed = noise_seed
        if first:                                                            # a chamber standing in the room, at room conditions, switched off
            self.Ta = self.Ts = self.Tg = self.Tp = ambient_k
            self.mv = relative_humidity * p_sat(ambient_k) / (R_VAP * ambient_k) * self._V()
            self.RHp = relative_humidity
            self.hum_kg = ENG['freshwater_reserve_kg']
            self.can_kg = ENG['can_capacity_kg'] if ws == 'can' else None
            self.led['e_initial_j'] = self._U()
            self.led['w_initial_kg'] = self._W()
        else:                                                                # new ambient pressure / temperature: the stored energy is re-referenced
            self.led['e_reconfigure_in_j'] += self._U() - u_old
        self._event('system', 'PLANT_CONFIGURED', '环境条件已声明')
        return dict(configured=True)

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not self.main_switch:
            self.main_switch = True
            self._event('system', 'POWER_ON', '主电源开关接通：按断电前的模式继续')
        elif not on and self.main_switch:
            self.main_switch = False
            self.I_T = self.I_RH = 0.0
            self.class1_trip = self.safety_trip = False
            self.alarms, self.ended, self.muted, self.alarm_since, self._outage_seen = [], set(), set(), {}, False
            self._event('system', 'POWER_OFF', '主电源开关断开')
        return dict(on=self.main_switch)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(on=not self.main_switch)

    def _need_panel(self):
        self._need_plant()
        if not self._mains_ok():
            raise ActionRejected('POWER_OFF')

    def _cmd_setpoint_set(self, temperature_k=None, relative_humidity=None, fan_fraction=None):
        self._need_panel()
        new = dict(self.fixed)
        for k, v in (('temperature_k', temperature_k), ('relative_humidity', relative_humidity), ('fan_fraction', fan_fraction)):
            if v is not None:
                new[k] = float(v)
        if new['temperature_k'] != self.fixed['temperature_k']:
            self.reached_T = False
        if new['relative_humidity'] != self.fixed['relative_humidity']:
            self.reached_RH = False
        self.fixed = new
        self._event('operation', 'SETPOINT', f'定值设定：{new["temperature_k"] - T0:.1f} °C、{new["relative_humidity"] * 100:.1f} %RH、风机{new["fan_fraction"] * 100:.0f} %')
        active = self._humidity_active(dict(new, humidity_control=self.functions['humidity_control']))   # same rule as chart.region (humidity off below 5 C)
        return dict(setpoints=dict(new), chart_region=chart_region(new['temperature_k'], new['relative_humidity']) if active else 'dry')

    def _cmd_function_set(self, humidity_control=None, idle_mode=None):
        self._need_panel()
        if humidity_control is not None and humidity_control != self.functions['humidity_control']:
            self.functions['humidity_control'] = humidity_control
            self.reached_RH = False
            self._event('operation', 'HUMIDITY_ON' if humidity_control else 'HUMIDITY_OFF', '湿度控制开' if humidity_control else '湿度控制关（加湿模块冷却）')
        if idle_mode is not None and idle_mode != self.functions['idle_mode']:
            self.functions['idle_mode'] = idle_mode
            self.reached_T = self.reached_RH = False
            self._event('operation', 'IDLE' if idle_mode else 'IDLE_OFF', 'Idle mode：停止控制' if idle_mode else '退出Idle mode')
        return dict(functions=dict(self.functions))

    def _cmd_safety_configure(self, mode, value_k):
        self._need_panel()
        if mode == 'limit' and not T0 <= value_k <= T0 + 100.0:
            raise ActionRejected('OUT_OF_LIMITS')
        if mode == 'offset' and value_k > 30.0:
            raise ActionRejected('OUT_OF_LIMITS')
        self.safety = dict(mode=mode, value_k=float(value_k))
        return dict(safety=dict(self.safety, limit_k=self._safety_limit()))

    def _cmd_tolerance_configure(self, temperature_k=None, relative_humidity=None, delay_s=None):
        self._need_panel()
        for k, v in (('temperature_k', temperature_k), ('relative_humidity', relative_humidity), ('delay_s', delay_s)):
            if v is not None:
                self.tol[k] = float(v)
        return dict(tolerance=dict(self.tol))

    def _cmd_buzzer_set(self, on):
        self._need_panel()
        self.buzzer = on
        return dict(on=on)

    def _cmd_alarm_reset(self):
        self._need_panel()
        muted = sorted(a for a in self.alarms if a not in NO_ACK and a not in self.ended)
        cleared = sorted(a for a in self.alarms if a in self.ended and a not in NO_ACK)
        self.muted |= set(muted)
        self.alarms = [a for a in self.alarms if a not in cleared]
        self.ended -= set(cleared)
        self.muted &= set(self.alarms)
        self._event('operation', 'ALARM_RESET', 'Reset alarm：静音' + '、'.join(muted) + ('；清除' + '、'.join(cleared) if cleared else ''))
        return dict(muted=muted, cleared=cleared)

    def _cmd_program_write(self, program, sections, repeat=None):
        self._need_panel()
        if self.prog is not None and self.prog['program'] == program:
            raise ActionRejected('PROGRAM_RUNNING')
        if repeat is not None and not (1 <= repeat['from_section'] < repeat['to_section'] <= len(sections)):
            raise ActionRejected('INVALID_REPEAT')
        self.programs[program] = dict(sections=[dict(s, transition=s['transition']) for s in copy.deepcopy(sections)], repeat=copy.deepcopy(repeat))
        dur = sum(s['duration_s'] for s in sections)
        if repeat:
            dur += repeat['count'] * sum(s['duration_s'] for s in sections[repeat['from_section'] - 1:repeat['to_section']])
        self._event('program', 'PROGRAM_WRITTEN', f'程序{program}写入：{len(sections)}段')
        return dict(program=program, sections=len(sections), duration_s=dur)

    def _cmd_program_start(self, program):
        self._need_panel()
        if program not in self.programs:
            raise ActionRejected('UNKNOWN_PROGRAM')
        self.mode = 'program'
        self.prog = dict(program=program, section=0, elapsed=0.0, reps_done=0, paused=False)
        self.reached_T = self.reached_RH = False
        self._event('program', 'PROGRAM_START', f'程序{program}启动')
        return dict(program=program, started=True)

    def _cmd_program_stop(self):
        self._need_panel()
        was = self.prog is not None
        self.mode, self.prog = 'fixed', None
        self.reached_T = self.reached_RH = False
        if was:
            self._event('program', 'PROGRAM_STOP', '程序停止：转定值模式')
        return dict(stopped=was)

    def _cmd_logger_configure(self, interval_s):
        self._need_panel()
        self.log_interval = float(interval_s)
        self.next_log = self.t + self.log_interval
        return dict(interval_s=self.log_interval)

    def _cmd_log_read(self, last):
        self._need_plant()
        return dict(records=copy.deepcopy(self.log[-last:]))

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_door_move(self, angle_rad):
        self._need_plant()
        if angle_rad <= 0.02 and self.glass > 0.02:
            raise ActionRejected('GLASS_DOOR_OPEN')
        if angle_rad < OUTER_FOR_GLASS and self.glass > 0.02:
            raise ActionRejected('GLASS_DOOR_OPEN')
        was = self._door_open()
        self.door = float(angle_rad)
        now = self._door_open()
        if now and not was:
            self.door_since = self.t
            self._event('door', 'DOOR_OPEN', '外门打开：风机降到最低转速')
        elif was and not now:
            self.door_since = None
            self._event('door', 'DOOR_CLOSED', '外门关闭：控制恢复')
        return dict(angle_rad=self.door)

    def _cmd_glass_door_move(self, angle_rad):
        self._need_plant()
        if angle_rad > 0.02 and self.door < OUTER_FOR_GLASS:
            raise ActionRejected('OUTER_DOOR_CLOSED')
        was = self.glass > 0.02
        self.glass = float(angle_rad)
        if self.glass > 0.02 and not was:
            self._pump_due += ENG['door_pumping_fraction'] * self._V()        # door-swing pumping, applied on the next grid tick
        return dict(angle_rad=self.glass)

    def _cmd_water_supply(self, open):
        self._need_plant()
        self.supply_open = open
        self._event('maintenance', 'WATER_OPEN' if open else 'WATER_CLOSED', '供水接通' if open else '供水断开')
        return dict(open=open)

    def _cmd_can_refill(self):
        self._need_plant()
        if self.plant['water_supply'] != 'can':
            raise ActionRejected('NO_CAN')
        add = ENG['can_capacity_kg'] - self.can_kg
        self.can_kg = ENG['can_capacity_kg']
        self.led['w_can_refill_kg'] += add
        self._event('maintenance', 'CAN_REFILLED', f'水桶加满（{add:.2f} kg）')
        return dict(can_kg=self.can_kg)

    def _need_access(self):
        if self.door < OUTER_FOR_GLASS:
            raise ActionRejected('OUTER_DOOR_CLOSED')
        if self.glass < GLASS_FOR_LOADING:
            raise ActionRejected('GLASS_DOOR_CLOSED')

    def _cmd_sample_place(self, sample_id, substance_id, shelf, dry_mass_kg, initial_k, exposed_area_m2=None, initial_water_kg=0.0,
                          heat_capacity_j_kg_k=None, container_mass_kg=0.0, heat_output_w=0.0, degradation=None):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('DUPLICATE_SAMPLE')
        if shelf > self.plant['shelves']:
            raise ActionRejected('NO_SHELF')
        self._need_access()
        m_tot = dry_mass_kg + initial_water_kg + container_mass_kg
        load = sum(s['dry_mass_kg'] + s['water_kg'] + s['film_kg'] + s['container_mass_kg'] for s in self.samples.values() if s['shelf'] == shelf)
        if load + m_tot > DOC['load_per_rack_kg'] + 1e-9:
            raise ActionRejected('SHELF_OVERLOAD')
        if sum(s['dry_mass_kg'] + s['water_kg'] + s['film_kg'] + s['container_mass_kg'] for s in self.samples.values()) + m_tot > DOC['permitted_load_kg'] + 1e-9:
            raise ActionRejected('LOAD_LIMIT')
        if sum(s['heat_w'] for s in self.samples.values()) + heat_output_w > ENG['sample_heat_total_limit_w'] + 1e-9:
            raise ActionRejected('HEAT_LOAD_LIMIT')
        sub = SORPTION.get(substance_id)
        if initial_water_kg > 0.0 and sub is not None and sub['kind'] != 'adsorbent' and not sub.get('solution_branch'):
            raise ActionRejected('UNQUANTIFIED_SOLUTION')
        cp = heat_capacity_j_kg_k if heat_capacity_j_kg_k is not None else 840.0
        film0 = initial_water_kg if substance_id == 'inert' else 0.0
        initial_water_kg = 0.0 if substance_id == 'inert' else initial_water_kg
        s = dict(sample_id=sample_id, substance_id=substance_id, shelf=shelf, dry_mass_kg=dry_mass_kg, cp=cp, container_mass_kg=container_mass_kg,
                 area=exposed_area_m2 if exposed_area_m2 is not None else max(ENG['default_area_m2_per_kg'] * dry_mass_kg, 1e-4),
                 water_kg=initial_water_kg, film_kg=film0, T=initial_k, heat_w=float(heat_output_w), state='placed', degradation=copy.deepcopy(degradation), degraded=0.0,
                 t_exposed_s=0.0, arr_sum=0.0, rh_sum=0.0, T_max=initial_k, T_min=initial_k, t_above_drh_s=0.0, condensing=False, condensation_events=0,
                 placed_at_s=self.t, initial_water_kg=initial_water_kg + film0)
        self.samples[sample_id] = s
        self.led['e_samples_added_j'] += self._sample_cap(s) * (initial_k - T0)
        self.led['w_samples_added_kg'] += initial_water_kg + film0
        load += m_tot
        self._event('operation', 'SAMPLE_PLACED', f'样品{sample_id}放到第{shelf}层')
        return dict(sample_id=sample_id, shelf=shelf, shelf_load_kg=load)

    def _sample_report(self, s):
        n = s['t_exposed_s']
        mkt = (MKT_DH / R_GAS) / (-math.log(s['arr_sum'] / n)) if n > 0 and s['arr_sum'] > 0 else None
        appearance = 'wet_film' if s['film_kg'] > 1e-6 else ('liquid' if s['water_kg'] > 0.5 * s['dry_mass_kg'] and s['substance_id'] != 'inert'
                                                             and SORPTION.get(s['substance_id'], {}).get('kind') != 'adsorbent' else
                                                             'damp' if s['water_kg'] > 1e-6 and SORPTION.get(s['substance_id'], {}).get('kind') != 'adsorbent' else 'dry_powder')
        if s['state'] == 'deliquesced_unquantified':
            appearance = 'deliquescing'
        return dict(sample_id=s['sample_id'], substance_id=s['substance_id'], shelf=s['shelf'],
                    total_mass_kg=s['dry_mass_kg'] + s['water_kg'] + s['film_kg'] + s['container_mass_kg'],
                    mass_change_kg=s['water_kg'] + s['film_kg'] - s['initial_water_kg'], appearance=appearance, state=s['state'],
                    exposure=dict(duration_s=n, mean_kinetic_temperature_k=mkt, mean_relative_humidity=s['rh_sum'] / n if n > 0 else None,
                                  max_temperature_k=s['T_max'], min_temperature_k=s['T_min'], time_above_deliquescence_s=s['t_above_drh_s'],
                                  condensation_events=s['condensation_events']),
                    degraded_fraction=s['degraded'] if s['degradation'] else None)

    def _cmd_sample_remove(self, sample_id):
        self._need_plant()
        if sample_id not in self.samples:
            raise ActionRejected('UNKNOWN_SAMPLE')
        self._need_access()
        s = self.samples.pop(sample_id)
        self.led['e_samples_removed_j'] += self._sample_cap(s) * (s['T'] - T0)
        self.led['w_samples_removed_kg'] += s['water_kg'] + s['film_kg']
        self._event('operation', 'SAMPLE_REMOVED', f'样品{sample_id}取出')
        rep = self._sample_report(s)
        self.removed.append(dict(rep, removed_at_s=self.t, temperature_k=s['T'], water_kg=s['water_kg'], film_kg=s['film_kg']))
        return dict(sample={k: rep[k] for k in ('sample_id', 'substance_id', 'shelf', 'total_mass_kg', 'mass_change_kg', 'appearance')})

    def _cmd_samples_read(self):
        return dict(samples=[dict(sample_id=s['sample_id'], substance_id=s['substance_id'], shelf=s['shelf'], placed_at_s=s['placed_at_s']) for s in self.samples.values()])

    def _cmd_truth_inspect(self):
        self._need_plant()
        r = self.read('evaluator')
        return dict(climate=r['truth.climate'], samples=r['truth.samples'], power=r['truth.power'], faults=r['truth.faults'], removed_samples=copy.deepcopy(self.removed))

    def _ledger(self):
        L = copy.deepcopy(self.led)
        if self.plant is not None:
            L['u_chamber_j'] = self._U()
            L['w_system_kg'] = self._W()
            L['sign_convention_zh'] = ('e_*_in_j为进入腔内（空气、金属、玻璃、冰与样品）的能量（正=流入）；e_refrigeration_out_j、e_drain_out_j与e_samples_removed_j为离开的能量；'
                                       '参考态为0 °C的干空气、液态水与金属，水蒸气含0 °C汽化焓；w_*为湿度系统（空气中的水汽、玻璃凝水、蒸发器冰、加湿容器、废水罐、水桶、样品水）的水量账')
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
        mains = pc and self._mains_ok()
        sT = self._sensor_T() if pc else None
        sRH = self._sensor_RH() if pc else None
        sp = self._setpoint()
        al = list(self.alarms) if mains else []
        prog = None
        if self.prog is not None:
            prog = dict(self.prog, section=self.prog['section'] + 1)
        out = {'clock.time_s': self.t, 'plant.configured': pc, 'power.on': self.main_switch, 'state.current': self._state(),
               'temperature.chamber_k': sT, 'humidity.chamber': sRH,
               'display.temperature': None if sT is None else round(sT - T0, 1), 'display.humidity': None if sRH is None else round(sRH * 100.0, 1),
               'setpoint.current': dict(temperature_k=sp['temperature_k'], relative_humidity=sp['relative_humidity'] if self._humidity_active(sp) else None,
                                        fan_fraction=sp['fan_fraction'], mode=self.mode, source=sp['source']),
               'setpoint.fixed': dict(self.fixed), 'functions.current': dict(self.functions),
               'chart.region': chart_region(sp['temperature_k'], sp['relative_humidity']) if self._humidity_active(sp) else 'dry',
               'program.status': dict(running=prog, stored=sorted(self.programs)),
               'safety.settings': dict(self.safety, limit_k=self._safety_limit(sp)), 'tolerance.settings': dict(self.tol),
               'alarms.active': al, 'notifications.active': list(self.notes) if mains else [], 'alarm.audible': self._audible() if pc else False,
               'indicator.pilot': bool(mains), 'indicator.alarm_header': bool(mains and al), 'indicator.door_open': bool(mains and 'DOOR_OPEN' in self.notes),
               'indicator.water': bool(mains and ({'FRESHWATER_SUPPLY', 'FRESHWATER_CAN_EMPTY', 'WASTEWATER'} & set(al))),
               'door.outer_angle_rad': self.door, 'door.glass_angle_rad': self.glass, 'door.glass_fogged': bool(pc and self.fog / A_GLASS > 0.002),
               'water.supply_open': self.supply_open, 'water.can_kg': self.can_kg, 'samples.count': len(self.samples),
               'logger.settings': dict(interval_s=self.log_interval, records=len(self.log))}
        if visibility != 'agent':
            if not pc:
                out['truth.climate'], out['truth.samples'], out['truth.power'] = {}, {}, {}
            else:
                pv = self._pv()
                out['truth.climate'] = dict(air_k=self.Ta, relative_humidity=pv / p_sat(self.Ta), dew_point_k=dew_point(pv), vapour_kg=self.mv, steel_k=self.Ts,
                                            glass_k=self.Tg, glass_film_kg=self.fog, cooling_plate_k=self.T_plate, dehumidification_evaporator_k=self.T_dehum, evaporator_ice_kg=self.ice, plate_ice_kg=self.ice_p, dehumidifier_ice_kg=self.ice_d, dehumidifying=self.dehum,
                                            steam_kg_s=self.steam, steam_container_kg=self.hum_kg, humidifier_ready=self.hum_ready, wastewater_kg=self.waste,
                                            cooling_w=self.Q_cool, controller_integral_w=self.I_T, humidity_integral_kg_s=self.I_RH, safety_tripped=self.safety_trip,
                                            class1_tripped=self.class1_trip, set_point_reached=self.reached_T, humidity_reached=self.reached_RH,
                                            sensor_k=self.Tp, sensor_rh=self.RHp)
                out['truth.samples'] = {k: dict(self._sample_report(s), temperature_k=s['T'], water_kg=s['water_kg'], film_kg=s['film_kg']) for k, s in self.samples.items()}
                out['truth.power'] = dict(self.P, total_w=sum(self.P.values()), mains_current_a=sum(self.P.values()) / self.plant['mains_v'])
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
class BinderKbf115Session:
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
        meta = copy.deepcopy(export_binder_kbf115_contract_metadata())
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
    'PLANT_REQUIRED': '尚未声明环境条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'SUPPLY_FIXED': '供水方式只能在首次声明时设定',
    'SHELF_OCCUPIED': '减少搁架数时该层上还有样品', 'POWER_OFF': '控制器无电（主开关断或停电）', 'OUT_OF_LIMITS': '安全控制器值超出范围（Limit 0…100 °C；Offset ≤30 K）',
    'PROGRAM_RUNNING': '该程序正在运行，不能覆盖', 'INVALID_REPEAT': '重复设置无效（起始段须小于目标段且不超过段数）', 'UNKNOWN_PROGRAM': '该程序号没有写入程序',
    'GLASS_DOOR_OPEN': '玻璃内门开着：先关玻璃门（或外门开度不足以容纳打开的玻璃门）', 'OUTER_DOOR_CLOSED': '外门未充分打开（须约75°以上）',
    'GLASS_DOOR_CLOSED': '玻璃内门未打开', 'NO_CAN': '本机为水管供水，没有水桶', 'DUPLICATE_SAMPLE': '样品编号已存在', 'UNKNOWN_SAMPLE': '没有该样品',
    'NO_SHELF': '该层没有搁架（搁架数见plant.configure）', 'SHELF_OVERLOAD': '超过每层搁架30 kg', 'LOAD_LIMIT': '超过总允许载荷100 kg',
    'UNQUANTIFIED_SOLUTION': '该物质没有实测溶液支（水活度–浓度），不能以溶液状态放入',
    'HEAT_LOAD_LIMIT': '腔内样品总发热超过500 W（工程上限；数据表40 °C最大热补偿200 W）',
}
_P = ['PLANT_REQUIRED']
_PP = _P + ['POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED', 'SUPPLY_FIXED', 'SHELF_OCCUPIED'], 'power.set': _P, 'power.toggle': _P, 'setpoint.set': _PP, 'function.set': _PP,
    'safety.configure': _PP + ['OUT_OF_LIMITS'], 'tolerance.configure': _PP, 'buzzer.set': _PP, 'alarm.reset': _PP,
    'program.write': _PP + ['PROGRAM_RUNNING', 'INVALID_REPEAT'], 'program.start': _PP + ['UNKNOWN_PROGRAM'], 'program.stop': _PP, 'logger.configure': _PP,
    'log.read': _P, 'events.read': [], 'door.move': _P + ['GLASS_DOOR_OPEN'], 'glass_door.move': _P + ['OUTER_DOOR_CLOSED'], 'water.supply': _P,
    'can.refill': _P + ['NO_CAN'], 'sample.place': _P + ['DUPLICATE_SAMPLE', 'NO_SHELF', 'OUTER_DOOR_CLOSED', 'GLASS_DOOR_CLOSED', 'SHELF_OVERLOAD', 'LOAD_LIMIT',
                                                         'UNQUANTIFIED_SOLUTION', 'HEAT_LOAD_LIMIT'],
    'sample.remove': _P + ['UNKNOWN_SAMPLE', 'OUTER_DOOR_CLOSED', 'GLASS_DOOR_CLOSED'], 'samples.read': [], 'truth.inspect': _P, 'ledger.read': [],
    'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子与供水方式（之后不可改）；首次声明时箱体在室温室湿、断电', ['plant.configured']),
    'power.set': ('无', ['power.on']), 'power.toggle': ('无', ['power.on']),
    'setpoint.set': ('控制器有电；温度−5…70 °C、湿度0…80 %RH、风机40–100 %（控湿时温度控制范围10…70 °C，湿度受气候图限制）', ['power.on', 'setpoint.fixed']),
    'function.set': ('控制器有电', ['power.on', 'functions.current']), 'safety.configure': ('控制器有电；Limit 0…100 °C，Offset ≤30 K', ['power.on', 'safety.settings']),
    'tolerance.configure': ('控制器有电；温度2–10 K、湿度5–20 %RH、延时15–120 min', ['power.on', 'tolerance.settings']), 'buzzer.set': ('控制器有电', ['power.on']),
    'alarm.reset': ('控制器有电', ['power.on', 'alarms.active']), 'program.write': ('控制器有电；不能覆盖正在运行的程序', ['power.on', 'program.status']),
    'program.start': ('控制器有电；该程序已写入', ['power.on', 'program.status']), 'program.stop': ('控制器有电', ['power.on', 'program.status']),
    'logger.configure': ('控制器有电', ['power.on', 'logger.settings']), 'log.read': ('无', ['logger.settings']),
    'door.move': ('关外门（或开度小于约75°）须先关玻璃内门', ['door.glass_angle_rad']), 'glass_door.move': ('开玻璃门须外门开到约75°以上', ['door.outer_angle_rad']),
    'water.supply': ('无', ['water.supply_open']), 'can.refill': ('选桶供水', ['water.can_kg']),
    'sample.place': ('外门与玻璃门打开；样品编号唯一；该层有搁架；每层≤30 kg、总≤100 kg；样品总发热≤500 W', ['door.outer_angle_rad', 'door.glass_angle_rad', 'samples.count']),
    'sample.remove': ('样品存在；外门与玻璃门打开', ['door.outer_angle_rad', 'door.glass_angle_rad', 'samples.count']),
    'truth.inspect': ('无', ['plant.configured']), 'fault.inject': ('无', ['plant.configured']),
}
_DISPLAY = ['temperature.chamber_k', 'humidity.chamber', 'display.temperature', 'display.humidity']
_ALARMS = ['alarms.active', 'notifications.active', 'alarm.audible'] + _INDS
_EVOLVE = ['state.current', 'program.status', 'setpoint.current', 'chart.region', 'safety.settings', 'door.glass_fogged', 'water.can_kg', 'logger.settings', 'truth.climate',
           'truth.samples', 'truth.power'] + _DISPLAY + _ALARMS
EFFECTS = {
    'plant.configure': ['plant.configured', 'water.can_kg', 'truth.climate', 'truth.power', 'state.current'] + _DISPLAY + _ALARMS,
    # indicator.pilot follows the mains: power.set and fault.inject (mains_outage) carry it through _ALARMS
    'power.set': ['power.on', 'state.current', 'truth.power', 'truth.climate'] + _DISPLAY + _ALARMS,
    'setpoint.set': ['setpoint.fixed', 'setpoint.current', 'chart.region', 'safety.settings', 'truth.climate'] + _ALARMS,
    'function.set': ['functions.current', 'setpoint.current', 'chart.region', 'state.current', 'truth.climate'] + _ALARMS,
    'safety.configure': ['safety.settings'], 'tolerance.configure': ['tolerance.settings'] + _ALARMS, 'buzzer.set': ['alarm.audible'],
    'alarm.reset': ['alarms.active', 'alarm.audible', 'indicator.alarm_header', 'indicator.water'],
    'program.write': ['program.status'], 'program.start': ['program.status', 'setpoint.current', 'chart.region', 'state.current', 'safety.settings', 'truth.climate'] + _ALARMS,
    'program.stop': ['program.status', 'setpoint.current', 'chart.region', 'state.current', 'safety.settings', 'truth.climate'] + _ALARMS, 'logger.configure': ['logger.settings'],
    'door.move': ['door.outer_angle_rad', 'state.current'] + _ALARMS, 'glass_door.move': ['door.glass_angle_rad'],
    'water.supply': ['water.supply_open'] + _ALARMS, 'can.refill': ['water.can_kg'] + _ALARMS,
    'sample.place': ['samples.count', 'truth.samples'], 'sample.remove': ['samples.count', 'truth.samples'],
    'fault.inject': ['truth.faults', 'truth.power', 'truth.climate', 'state.current'] + _DISPLAY + _ALARMS,
    'sim.step': ['clock.time_s'],
}
EFFECTS['power.toggle'] = list(EFFECTS['power.set'])
_STEPPED = ('plant.configure', 'power.set', 'power.toggle', 'setpoint.set', 'function.set', 'safety.configure', 'tolerance.configure', 'alarm.reset', 'program.start',
            'program.stop', 'door.move', 'glass_door.move', 'water.supply', 'can.refill', 'sample.place', 'sample.remove', 'fault.inject')
ON_STEP = {k: list(_EVOLVE) for k in _STEPPED}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {
    'sim.step': '按1 s网格积分：空气（干空气与水汽）、内腔金属、玻璃门与样品的传热传质，温度与湿度比例积分控制（加热、制冷、蒸汽加湿、蒸发器除湿与结冰），'
                '门封与开门空气交换，壁面与玻璃凝水，加湿容器补水、废水泵，安全控制器、公差带与门报警，程序推进，数据记录',
    'setpoint.set': '温度与湿度按新设定调节（恢复时间与气候图限制）', 'function.set': '湿度控制关：加湿模块冷却（重新开启后最长10 min可用）；Idle：停止控制',
    'door.move': '开门：风机降到最低、空气交换、湿度与温度下降；60 s后全部功能关闭；5 min后门报警', 'glass_door.move': '打开玻璃门时门扇带出腔内空气',
    'program.start': '设定按程序段（ramp/step）变化', 'program.stop': '回到定值设定', 'sample.place': '样品与空气换热、吸湿/潮解/放湿，冷样品表面凝水',
    'sample.remove': '腔内湿负荷变化', 'water.supply': '断水后加湿容器耗尽并报警', 'can.refill': '加湿恢复', 'power.set': '通电后按断电前的模式继续',
    'fault.inject': '故障作用于加热、制冷、加湿、风机、传感器、门加热、废水或市电', 'alarm.reset': '静音后条件消失的报警自动清除',
    'tolerance.configure': '公差带报警按新带宽与延时判定', 'safety.configure': '安全控制器按新上限动作', 'plant.configure': '室温室湿影响漏热、空气交换与开门恢复',
}
EXEC_TEXT = {'setpoint.set': ('setpoint.current为新值；腔温与湿度在之后的step中趋近', '超出设定范围时拒绝'),
             'door.move': ('door.outer_angle_rad为给定角度', '玻璃门开着时拒绝关外门'),
             'program.start': ('program.status.running为该程序；设定按段推进', '程序不存在时拒绝')}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('door.move', 'glass_door.move', 'water.supply', 'can.refill', 'sample.place', 'sample.remove'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'setpoint.set': ['temperature.chamber_k', 'humidity.chamber'], 'door.move': ['door.outer_angle_rad', 'temperature.chamber_k', 'humidity.chamber'],
            'program.start': ['program.status', 'temperature.chamber_k', 'humidity.chamber'], 'sample.place': ['humidity.chamber']}
_WB, _DS, _E6, _E7, _GS, _AQ, _DV, _SIM = ('user_workbook', 'manufacturer_datasheet_2022', 'manufacturer_manual_kbfs_e6_sibling', 'manufacturer_manual_kbf_e7_controller',
                                           'greenspan_1977', 'nist_trc_thermoml_via_chem007', 'nist_isodb_water_via_chem123', 'simulation_assumption')
CAP_SOURCES = {k: [_E7] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_DS, _SIM], 'power.set': [_E7, _E6], 'power.toggle': [_E7, _E6], 'setpoint.set': [_DS, _E7, _WB],
                    'safety.configure': [_DS, _E6], 'sample.place': [_DS, _GS, _AQ, _DV, _WB, _SIM], 'sample.remove': [_GS, _AQ, _DV, _WB, _SIM],
                    'samples.read': [_WB, _SIM], 'log.read': [_DS, _E7, _WB], 'water.supply': [_DS, _E7, _E6], 'can.refill': [_DS, _E6],
                    'events.read': [_E7, _SIM], 'truth.inspect': [_SIM], 'ledger.read': [_SIM], 'fault.inject': [_E7, _E6, _SIM], 'sim.step': [_SIM],
                    'door.move': [_DS, _E7], 'glass_door.move': [_DS, _SIM]})
CAP_SOURCES.update({k: [_DS] for k in UNSUPPORTED})
CAP_SOURCES['week_program.run'] = [_E7]
PARAM_ZH = {'ambient_k': '室温', 'relative_humidity': '相对湿度（0–1）', 'pressure_pa': '大气压', 'mains_v': '电源电压', 'water_supply': '供水方式（pipe水管或can选配水桶）',
            'shelves': '搁架数（标配2，最多5）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）', 'on': '是否接通', 'temperature_k': '温度设定或公差带宽',
            'fan_fraction': '风机转速（0.4–1）', 'humidity_control': '湿度控制开/关', 'idle_mode': 'Idle mode', 'mode': '安全控制器模式', 'value_k': '安全控制器值',
            'delay_s': '公差带报警延时', 'program': '程序号（2–25）', 'sections': '程序段列表', 'repeat': '重复设置', 'interval_s': '数据记录间隔', 'last': '最近记录条数',
            'since_s': '起始时间', 'angle_rad': '开启角', 'open': '接通或断开', 'sample_id': '样品编号', 'substance_id': '物质（模板库编号或inert）', 'shelf': '搁架层（1为最上层）',
            'dry_mass_kg': '干样品质量', 'heat_output_w': '样品或被测件的发热功率（如通电器件；数据表“最大热补偿”即此负荷）', 'exposed_area_m2': '暴露面积', 'initial_k': '放入时温度', 'initial_water_kg': '放入时所含水（溶液或吸附水；惰性物为表面水）',
            'heat_capacity_j_kg_k': '干样品比热（默认840）', 'container_mass_kg': '容器（培养皿）质量', 'degradation': '宿主声明的降解规律：k = rate_ref_per_s·exp(−Ea/R·(1/T − 1/298.15 K))·(RH/0.60)^n，参考条件25 °C/60 %RH（ICH长期条件）',
            'fault': '故障名', 'active': '注入或清除', 'dt_s': '推进时间'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.climate_chamber_process.limitations。'
CAP_LIMITS = {
    'setpoint.set': ['控制为比例积分抽象（真实MB2控制器参数未公开）；气候图之外的设定可以输入，能否达到由蒸发器最低温度、制冷量与湿负荷决定（区域B按蒸发器结冰限时）。'],
    'door.move': ['开门空气交换按门洞浮力流（虚温密度差）加门扇扫掠体积（工程值）；开门恢复时间由湿度控制时间（rh_control_time_s，一个参数兼顾两个目标）标定到数据表6/7 min（±25 %）（空载、22 °C室温、50 %RH假设）。'],
    'sample.place': ['盐的潮解湿度来自Greenspan 1977表2（5 K网格线性插值）；溶液含水来自CHEM-007库的实测水活度–质量摩尔浓度点（取最接近25 °C的数据组，温度依赖不建模）；'
                     '无溶液支的盐潮解后水量不量化；无潮解湿度的溶质以实测最浓点代替饱和点（近似）；吸附剂等温线只在测得温度；表面传质系数为工程值；'
                     '降解只有宿主声明降解规律时才计算，本机不提供任何材料的老化数据。'],
    'sample.remove': ['只返回称量质量与目视外观；暴露记录（平均动力学温度按ICH约定ΔH=83.144 kJ/mol）、内部状态与降解分数在truth.inspect的removed_samples中（评测者）。',
                      '降解规律的参考条件为298.15 K与60 %RH（k = rate_ref·exp(−Ea/R·(1/T−1/298.15))·(RH/0.60)^n）。'],
    'program.write': ['实现时间程序（ramp/step、段公差带暂停、段重复）；周程序与程序内特殊功能（灯光、CO₂等）未实现。'],
    'log.read': ['数据记录器内容为运行层记录；USB导出格式（文件）不建模，返回记录列表。'],
    'water.supply': ['水管供水视为压力无限；水质（去离子水）与钙化不建模。'],
}
LIMITATIONS = [
    '所选配置：BINDER KBF 115，KBF115-230V（订货号9020-0320，2022数据表），标配2个搁架、水管供水（可选20 L水桶）、左侧30 mm穿线孔、以太网与USB；无报警继电器、模拟输出、门锁、灯条与RS 485等选件。',
    '热湿模型：一个充分混合的空气节点（含预热室）、内腔金属节点、玻璃门节点与样品节点；传热系数、热容、门封漏气、制冷量曲线与控制参数为工程值，'
    '其中门封漏气流量、40 °C最大制冷量、压缩机功率与湿度控制时间由calibrate_kbf.py按数据表或同平台手册标定。',
    '制冷：DCT抽象为设定≤60 °C时压缩机持续运行、热气旁通调节制冷量与加热反控（基础制冷量随风机换热系数缩放，冷板与空气温差不随风速变化）；真实控制策略未公开。除湿按除湿蒸发器把需求的水汽量冷凝所需的蒸发器温度抽象（需求经约2 min响应滤波）；蒸发器低于0 °C时结冰。',
    '湿度系统：设定低于5 °C时湿度控制自动关闭（E7；同段“回到0…95 °C后重开”的下限取5 °C）；首次开机或断电恢复后约20 min开始加湿（E7）；'
    'B区角点10 °C/40 %RH（风机100 %）因除湿与制冷交替温度波动约±0.15 K（其余点约±0.02 K）；温湿度偏差连续超出积分分离带（温度15 min、湿度30 min）后积分恢复，压缩机启停时制冷板冷量按约4 min时间常数变化（蒸发器冷质量）且基础制冷量随之无扰转入/转出积分（设定向上跨越60 °C时过冲约0.4–0.6 K，向下时冷板逐渐变冷、不过冷），温度比例增益随风机换热系数缩放，除湿蒸发器按约2 min响应时间跟随需求、其显热计入制冷需求（均为工程值）；样品总发热上限500 W为工程值。',
    '空间不均匀：按搁架位置给固定温度偏差（数据表±0.2 K以内，风机转速降低时放大）；腔内湿度视为均匀。',
    '手册：KBF-S（E6）为同一115箱体但RD4控制器的另一型号（空重116 kg、湿度20–80 %），KBF（E7）为后一代触摸屏控制器（KBF 130起）；2022版KBF 115（E6.1触摸屏）手册未能获取，'
    '控制器行为按E7、湿度系统硬件按E6，均为同系列证据。',
    '样品：只有模板库中的物质（28种潮解盐、75种溶质溶液支、3种吸附剂）与惰性物；材料老化（聚合物、药物固体）没有实测数据，只在宿主声明降解规律时计算。',
    '箱体、门、面板与搁架有几何；空气、水汽、凝水与样品在场景中没有几何；场景中外门、玻璃内门、触摸屏温湿度数字与3个图标按运行层状态变化。',
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
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景关节、读数或图标（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节或读数', binding_ids=[])


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_datasheet_2022': _DS, 'manufacturer_manual_kbfs_e6_sibling': _E6, 'manufacturer_manual_kbf_e7_controller': _E7,
              'greenspan_1977': _GS, 'manufacturer_product_page': _DS}
    WB_KEYS = ('temperature_range_dry_k', 'temperature_range_climate_k', 'humidity_range', 'temperature_variation_40c_k', 'temperature_fluctuation_k',
               'humidity_fluctuation', 'recovery_25_60_s', 'recovery_40_75_s', 'interior_volume_m3', 'nominal_power_w', 'housing_net_m', 'net_weight_kg', 'chart_a', 'chart_b')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
    cal = {k for k, v in instrument()['engineering'].items() if v.get('calibrated_by')}
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明），不是实测' +
            ('；由calibrate_kbf.py按数据表或同平台手册标定' if k in cal else ''))
    for name, val, unit, meaning in (('cp_dry_air_j_kg_k', CP_AIR, 'J/(kg K)', '干空气比热'), ('cp_vapour_j_kg_k', CP_VAP, 'J/(kg K)', '水蒸气比热'),
                                     ('cp_water_j_kg_k', CP_WATER, 'J/(kg K)', '液态水比热'), ('latent_heat_0c_j_kg', L0, 'J/kg', '0 °C汽化焓'),
                                     ('latent_heat_fusion_j_kg', L_FUS, 'J/kg', '冰的熔化热'), ('mkt_activation_j_mol', MKT_DH, 'J/mol', '平均动力学温度的活化能（ICH约定）'),
                                     ('gas_constant_vapour_j_kg_k', R_VAP, 'J/(kg K)', '水蒸气气体常数')):
        asm(name, val, unit, meaning, '物理常数或约定值', (_SIM,))
    st = _lib('sorption')
    asm('sorption_templates', dict(file='assets/expansion_batch04/t2_vacuum/binder_kbf115/sorption_templates.json', counts=st['counts']), '1',
        '样品吸湿模板库：Greenspan 1977表2潮解湿度（28种盐）、CHEM-007库溶液支（水活度–质量摩尔浓度）与CHEM-123库吸附等温线（每条带evidence与source）',
        '实测文献数据；适用规则见模板库rules_zh', (_GS, _AQ, _DV))
    return P


SLOT_REFERENCE = {'temperature.chamber_k': '内腔Pt100（腔中心附近）测得的空气温度', 'humidity.chamber': '电容传感器测得的相对湿度（对液态水饱和压，0–1）',
                  'display.temperature': '°C，0.1位', 'display.humidity': '%RH，0.1位', 'door.outer_angle_rad': '0关闭，正值为开启角'}


def export_binder_kbf115_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'BinderKbf115Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.binder_kbf115.BinderKbf115Session.invoke',
                                              invocation=f'session.invoke(instance_id="binder_kbf115_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='BinderKbf115Runtime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（温度、湿度与报警在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['cabinet', 'outer_door', 'glass_door', 'shelves', 'touchscreen', 'interior_chamber', 'refrigeration_unit', 'freshwater_inlet'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='恒温恒湿：APT.line预热室加热与DCT制冷（加热反控）保持腔温；蒸汽加湿与蒸发器露点下冲除湿保持相对湿度；门封漏气与开门时室内空气进入，'
                   '控制器按设定（定值或程序段）调节；样品与腔内空气换热并按物质吸湿、潮解或放湿；壁面与玻璃门在露点以下凝水；安全控制器、公差带、门与供排水报警',
        equations=['(C_d + m_v c_pv) dT_a/dt = sum G_k (T_k - T_a) + (1-f_w) P_heat + P_fan + rho c_p V_dot (T_room - T_a) + sum m_in c_pv (T_in - T_a)  (implicit)',
                   'dm_v/dt = m_steam + V_dot (rho_v,room - rho_v) - h_m,ev (rho_v - rho_sat(T_ev))+ - sum h_m A (rho_v - a_s rho_sat(T_s))',
                   'P_heat - Q_cool,extra = Kp f_h e_T + I_T (I_T integrates inside |e_T| <= 1 K or after 15 min outside; +-Q_base f_h moved into / out of I_T when the compressor switches);  Q_cool = Q_base f_h + Q_extra <= Q_max(T_a);  T_plate = T_a - (Q_base f_h + Q_extra - Q_sens,dehum)/UA_plate;  dehumidification demand d_f: dd_f/dt = (d - d_f)/tau_dehum, T_dehum condenses d_f',
                   'm_steam = clip(Kp_RH e_RH + I_RH, 0, m_max * ready)  (e_RH > 0);  dehumidify when e_RH < 0',
                   'V_dot_door = Cd W H/3 sqrt(g H |rho_in - rho_room| / rho_mean) f_outer max(f_glass, 0.05) + pumping on opening the glass door',
                   'salt: dry while RH < DRH(T) (Greenspan Table 2); solution: a_w(m) from measured branches; adsorbent: q -> q_eq(RH) with tau',
                   'MKT = (dH/R) / -ln(mean(exp(-dH / R T)))'],
        parameters=_params(), source_ids=[_SIM, _WB, _DS, _E6, _E7, _GS, _AQ, _DV], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_kbf_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('binder_kbf115', caps, slots, {'climate_chamber_process': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'setpoint.set': (SETPOINT, SETPOINT_EXAMPLE), 'sample.place': (SAMPLE, SAMPLE_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'climate_chamber_process': mechanism})
