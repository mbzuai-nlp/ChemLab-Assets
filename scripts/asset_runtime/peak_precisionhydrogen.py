"""CHEM-196 PEAK Scientific Precision Hydrogen Trace 500 (archived model; PEM hydrogen generator with regenerative PSA dryers): process runtime.

Device (2023 archived datasheet; 2019 Trace data sheet; installation guide; same-series SL user manual): 500 cc/min H2 (sccm at 273.15 K and 1.01
bar), up to 100 psi / 6.9 bar, 99.99999 % (O2 basis), dew point -75 C, 1/8" Swagelok outlet, ASTM Type II feed water (< 1 uS/cm), 0.4-1.2 L/day,
787 W, 35 C maximum ambient, 406 x 380 x 540 mm, 31.5 kg; PEM electrolysis on demand at the pre-set pressure, regenerative PSA dryers, automatic
loading pump from an external DI water bottle, de-ionizer cartridge (12 months), power-up diagnostics with a leak test (about 6 min), internal leak
detection with automatic shutdown, overpressure switch, critical water level detection, touch HMI (START / STOP, MENU, set pressure, units, shut
down = depressurise), 24 h run-in before trace use.

Process model (fixed 0.5 s grid anchored at t = 0; SI units, amounts in mol, pressures absolute in Pa, the HMI shows gauge pressure):
  * Cell: H2O -> H2 + 1/2 O2; hydrogen n_dot = I / 2F (Faraday); production follows the pressure error (PI with anti-windup, limited to 500 sccm); the
    regenerative PSA dryer vents a purge fraction of the product, so the cell makes (1 + f) n_dot; water used = (1 + f) n_dot M_w plus a constant
    loss (vapour / droplets with the O2 vent) - f and the constant are calibrated to 0.4 (standby) ... 1.2 (500 sccm) L/day.
  * Gas: one internal volume (cell cathode, dryers, buffer) at the ambient temperature; the delivery valve opens it to the application line (host
    consumer: demand flow with a minimum pressure, external line leak); stand-by keeps the internal pressure with the valve closed; depressurise
    vents the stored hydrogen through the exhaust; overpressure switch.
  * Water: internal tank with low / high level sensors and a critical level; the loading pump draws from the bottle and the feed water mixes into
    the tank; the tank water circulates through the de-ionizer cartridge (0.3 l/min), which takes up the ions until its capacity is used; the cell
    is fed from the tank, so its conductivity rises only after the cartridge breaks through (and falls again after a replacement).
  * Diagnostics: power-up water check, pressurise and hold (leak test about 6 min); standby leak check; low pressure at full production
    (external leak / demand above 500 sccm) -> automatic shutdown.
  * Quality: output dew point dries from the initial value towards -75 C with the dryer time constant (24 h run-in); O2 at the nominal level,
    higher with a membrane crossover fault.
  * Power: idle + cell power per mol/s (calibrated to 787 W at 500 sccm); heat = power - HHV x hydrogen made.
  * Ledgers: hydrogen (made, purged, stored, delivered, leaked, vented) and water (bottle, tank, consumed) close exactly."""
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.peak_precisionhydrogen"
CATALOG_ID = "CHEM-196"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t2_vacuum/peak_precisionhydrogen'
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
DT = 0.5
R_GAS = 8.314462618
F_CONST = 96485.33212                           # C/mol (CODATA)
M_W = 0.01801528                                # kg/mol water
RHO_W = 997.0                                   # kg/m3 water at 25 C
HHV_H2 = 285.83e3                               # J/mol, H2 higher heating value (standard enthalpy of formation of liquid water)
SCCM = 101000.0 / (R_GAS * 273.15) * 1e-6 / 60.0  # mol/s per sccm (datasheet reference 273.15 K, 1.01 bar)
N_MAX = DOC['max_flow_mol_s']
P_MAX_G = DOC['max_pressure_pa']
LINE_V = 2.0e-5                                 # application line volume (2 m of 1/8" tubing ~ 10 ml + fittings; engineering)
STATES = ['off', 'diagnostics', 'standby', 'running', 'depressurising', 'fault']
FAULTS = ['internal_leak', 'membrane_crossover', 'loading_pump_failure', 'cell_failure', 'pressure_sensor_drift']
MAX_EVENTS_LOG = 2000
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


PLANT_EXAMPLE = dict(ambient_k=295.15, pressure_pa=101325.0, mains_v=230.0, bottle_volume_m3=0.004, bottle_water_m3=0.003, feed_conductivity_s_m=5e-5, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 273.15, 318.15), pressure_pa=number('Pa', 80000.0, 106000.0), mains_v=number('V', 90.0, 260.0),
                 bottle_volume_m3=number('m3', 0.0005, 0.02), bottle_water_m3=number('m3', 0.0, 0.02), feed_conductivity_s_m=number('S/m', 0.0, 0.1),
                 noise_seed=integer(0, 2 ** 31 - 1)))
CONSUMER_EXAMPLE = dict(demand_sccm=150.0, min_pressure_pa=2.5e5, external_leak_sccm_at_5bar=0.0)
CONSUMER = obj(dict(demand_sccm=number('sccm', 0.0, 3000.0), min_pressure_pa=number('Pa', 0.0, 7.0e5), external_leak_sccm_at_5bar=number('sccm', 0.0, 3000.0)),
               optional=('external_leak_sccm_at_5bar',))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'operation', 'water', 'alarm', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
_BODY = ['generator_body']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明环境温度与大气压、电源电压、外接去离子水瓶（容积、存水量、进水电导率）与噪声种子', 'evaluator', 'configuration')
cap('consumer.set', CONSUMER, obj(dict(demand_sccm=number('sccm'), min_pressure_pa=number('Pa'))), CONSUMER_EXAMPLE, ['rear_fittings'],
    '宿主声明下游应用：需求流量（GC载气/燃气、ICP-MS碰撞气，sccm）、应用所需的最低表压、输出管路外漏（5 bar表压下的sccm）', 'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['mains_switch'], '背面电源开关通/断（通电后自检、加水与泄漏测试约6 min，然后待机）', 'agent')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['mains_switch'], '电源开关拨一下（等价于power.set取反）', 'agent')
cap('cycle.start', obj({}), obj(dict(state=TEXT)), {}, ['touch_screen'], '触摸屏START：打开输出阀向应用供氢（按设定压力保持）', 'agent')
cap('cycle.stop', obj({}), obj(dict(state=TEXT)), {}, ['touch_screen'], '触摸屏STOP：关闭输出阀回到待机（机内保压）', 'agent')
cap('cycle.toggle', obj({}), obj(dict(state=TEXT)), {}, ['start_stop_key'], '触摸屏START/STOP键（按一下切换供氢与待机）', 'agent')
cap('settings.configure', obj(dict(pressure_set_pa=number('Pa', 0.0, P_MAX_G), units=enum(['bar', 'psi'])), optional=('pressure_set_pa', 'units')),
    obj(dict(settings=FREE_OBJ)), dict(pressure_set_pa=5.17e5), ['touch_screen'], 'MENU：设定输出压力（表压，最高6.9 bar）与显示单位（bar/psi）', 'agent')
cap('output.depressurise', obj({}), obj(dict(state=TEXT)), {}, ['touch_screen', 'rear_fittings'], 'MENU→SHUT DOWN：停止产氢并把机内储氢经排气口排空（去压）', 'agent')
cap('alarm.mute', obj({}), obj(dict(muted=BOOL)), {}, ['touch_screen'], '报警画面的静音键', 'agent')
cap('water.refill', obj(dict(volume_m3=number('m3', 0.0, 0.02), conductivity_s_m=number('S/m', 0.0, 0.1)), optional=('conductivity_s_m',)),
    obj(dict(bottle_water_m3=number('m3'))), dict(volume_m3=0.003), ['rear_fittings'], '给外接水瓶加去离子水（体积、电导率；不超过瓶容积）', 'agent', 'robot_skill')
cap('deioniser.replace', obj({}), obj(dict(replaced=BOOL)), {}, ['deioniser_column'], '更换去离子柱（维护；须断电）', 'agent', 'robot_skill')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _BODY,
    '事件记录（自检、泄漏测试、加水、报警、操作）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(gas=FREE_OBJ, water=FREE_OBJ, cell=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, _BODY,
    '评测者读取过程真值：机内氢量与压力、产氢与吹扫、输出露点与氧含量、水箱与水瓶存水、去离子柱消耗、电解池水电导率、电功率与散热', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '氢（mol）与水（kg）的守恒账', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='internal_leak', active=True), _BODY,
    '故障注入：机内泄漏、膜氧透过、加水泵故障、电解池故障、压力传感器漂移', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'compressor.boost': '样本：无集成压缩机（Integrated compressor: No）。', 'gas.select': '本机只产氢（Gas types: Hydrogen）。',
    'leak_detector.gc_oven': 'GC炉内氢泄漏检测器为选件（未选）。', 'stack.multi_unit_control': '多台并联叠放的联控不建模（单台）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'generator_body', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'generator_body', '环境条件是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'mains_switch', '电源是否接通')
slot('state.current', enum(STATES), '1', 'derived', 'touch_screen', '工作状态：off、diagnostics（自检与泄漏测试）、standby、running（供氢）、depressurising（去压）、fault')
slot('state.remaining_s', nullable(number('s')), 's', 'derived', 'touch_screen', '自检/泄漏测试剩余时间（其他状态为空）')
slot('pressure.delivery_pa', nullable(number('Pa')), 'Pa', 'sensor', 'touch_screen', '输出压力（表压；屏幕DELIVERY PRESSURE；断电为空）')
slot('pressure.set_pa', number('Pa'), 'Pa', 'setpoint', 'touch_screen', '设定输出压力（表压）')
slot('flow.percent', nullable(number('1')), '1', 'derived', 'touch_screen', '屏幕% FLOW：当前产氢占最大产氢的百分比')
slot('display.pressure', nullable(number('1')), '1', 'derived', 'display_digits', '屏幕压力数字（按设定单位bar或psi；显示用）')
slot('display.flow_percent', nullable(number('1')), '1', 'derived', 'display_digits', '屏幕% FLOW数字（显示用）')
slot('water.level_ok', BOOL, '1', 'derived', 'water_tank', '水箱水位是否高于低水位传感器')
slot('alarms.active', array(TEXT, 0, 16), '1', 'derived', 'touch_screen', '当前报警（LEAK_TEST_FAILED、INTERNAL_LEAK、LOW_PRESSURE、OVERPRESSURE、WATER_LOW、WATER_QUALITY、CELL_FAULT、AMBIENT_TEMPERATURE、DEIONISER_DUE）')
slot('alarm.audible', BOOL, '1', 'derived', 'touch_screen', '报警声（可静音）')
slot('indicator.ok', BOOL, '1', 'derived', 'hex_ring', '六边形灯带与实心六边形绿色（正常：待机或供氢；对产品照片的解读）')
slot('indicator.water', BOOL, '1', 'derived', 'water_icon', '触摸屏缺水图标琥珀色（缺水）')
slot('indicator.fault', BOOL, '1', 'derived', 'fault_frame', '触摸屏红色故障边框（故障/泄漏，对应红色故障画面）')
slot('indicator.depressurised', BOOL, '1', 'derived', 'vent_icon', '触摸屏去压图标蓝色（已去压）')
slot('settings.current', FREE_OBJ, '1', 'setpoint', 'touch_screen', '当前设定（输出压力、显示单位）')
slot('deioniser.age_s', number('s'), 's', 'derived', 'deioniser_column', '去离子柱已使用时间（建议12个月更换）')
slot('truth.gas', FREE_OBJ, '1', 'process_truth', 'generator_body', '机内氢量与绝压、产氢与吹扫速率、输出露点与氧含量、向应用供氢与外漏', 'evaluator')
slot('truth.water', FREE_OBJ, '1', 'process_truth', 'water_tank', '水箱与水瓶存水、加水泵、去离子柱消耗、电解池进水电导率', 'evaluator')
slot('truth.cell', FREE_OBJ, '1', 'process_truth', 'pem_cell', '电解池电流、电功率、散热、膜状态', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'generator_body', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'display.pressure', 'display.flow_percent', 'indicator.ok', 'indicator.water', 'indicator.fault', 'indicator.depressurised', 'power.on'}   # power.on: tank window lit


LATCHED = ('LEAK_TEST_FAILED', 'INTERNAL_LEAK', 'LOW_PRESSURE', 'OVERPRESSURE', 'CELL_FAULT')


# ---------------------------------------------------------------- runtime
class PeakPrecisionhydrogenRuntime:
    def __init__(self, instance_id='peak_precisionhydrogen_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.consumer = dict(demand_sccm=0.0, min_pressure_pa=0.0, external_leak_sccm_at_5bar=0.0)
        self.powered = False
        self.state = 'off'
        self.diag = None                                  # dict(phase, t, p_hold0)
        self.valve = False                                # delivery valve
        self.depressurised = False
        self.settings = dict(pressure_set_pa=5.17e5, units='psi')
        self.n_int = None
        self.n_line = None
        self.n_dot = 0.0                                  # product hydrogen made (mol/s, last tick)
        self.p_meas = None
        self.tank = 0.0                                   # internal tank water (m3)
        self.bottle = 0.0
        self.bottle_kappa = 0.0
        self.pump_on = False
        self.di_used = 0.0                                # de-ionizer ion load taken up (eq)
        self.di_age = 0.0
        self.membrane_dose = 0.0                          # exposure to conductive water (S/m x s)
        self.dew_k = ENG['dew_point_initial_k']
        self.maxflow_s = 0.0
        self.integ = 0.0                                  # integral part of the pressure controller (mol/s)
        self.p_rate = 0.0                                 # filtered rise rate of the measured pressure (Pa/s)
        self.tank_kappa = 5e-6                            # conductivity of the water in the tank / cell loop (S/m)
        self.standby_hist = []
        self.latched = set()
        self.muted = False
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.revision = 0
        self.P_el = 0.0
        self.alarms = []
        self.led = dict(h2_initial_mol=0.0, h2_made_mol=0.0, h2_purged_mol=0.0, h2_delivered_mol=0.0, h2_external_leak_mol=0.0, h2_internal_leak_mol=0.0,
                        h2_vented_mol=0.0, water_initial_kg=0.0, water_added_kg=0.0, water_electrolysed_kg=0.0, water_lost_kg=0.0, energy_j=0.0, heat_j=0.0)
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
        return PeakPrecisionhydrogenSession(self, name)

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
    def _T(self):
        return self.plant['ambient_k']

    def _pa(self):
        return self.plant['pressure_pa']

    def _V(self):
        return ENG['internal_gas_volume_m3']

    def _p_int(self):
        V = self._V() + (LINE_V if self.valve else 0.0)
        n = self.n_int + (self.n_line if self.valve else 0.0)
        return n * R_GAS * self._T() / V

    def _p_line(self):
        return self._p_int() if self.valve else self.n_line * R_GAS * self._T() / LINE_V

    def _tank_frac(self):
        return self.tank / ENG['tank_volume_m3']

    def _kappa_cell(self):
        return self.tank_kappa                                                     # the cell is fed from the tank / de-ioniser loop

    def _producing_allowed(self):
        return (self.powered and self.state in ('diagnostics', 'standby', 'running') and not self.latched and 'cell_failure' not in self.faults
                and self._tank_frac() > ENG['tank_critical_fraction'] and not self.depressurised)

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
        self._update_alarms()
        return self.t

    def _tick(self, h):
        pa, T = self._pa(), self._T()
        L = self.led
        RT = R_GAS * T
        # ---- control: pressure set point (gauge) -> production
        p_g = self._p_int() - pa
        drift = -1.0e5 if 'pressure_sensor_drift' in self.faults else 0.0          # sensor reads 1 bar low: the controller over-pressurises
        if self.p_meas is not None:
            self.p_rate += ((p_g + drift - self.p_meas) / h - self.p_rate) * (1 - math.exp(-h / 30.0))
        self.p_meas = p_g + drift
        target = self.settings['pressure_set_pa']
        if self.state == 'diagnostics' and self.diag['phase'] == 'pressurise':
            target = ENG['leak_test_pressure_pa']
        n_dot = 0.0
        if self._producing_allowed() and not (self.state == 'diagnostics' and self.diag['phase'] != 'pressurise'):
            err = target - self.p_meas                                        # proportional-integral control with conditional integration (anti-windup)
            kp = ENG['pressure_controller_gain_mol_s_per_pa']
            u = kp * err + self.integ
            if not ((u >= N_MAX and err > 0.0) or (u <= 0.0 and err < 0.0)):   # no integration while saturated in the direction of the error
                self.integ = min(max(self.integ + kp / ENG['pressure_controller_integral_s'] * err * h, 0.0), N_MAX)
            n_dot = min(max(kp * err + self.integ, 0.0), N_MAX)
        else:
            self.integ = 0.0
        self.n_dot = n_dot
        purge = ENG['psa_purge_fraction'] * n_dot
        # ---- gas balance (explicit on the 0.5 s grid; the controller time constant is several seconds)
        n_int_old, n_line_old = self.n_int, self.n_line
        d_int = n_dot * h
        L['h2_made_mol'] += n_dot * h
        L['h2_purged_mol'] += purge * h                                        # vented by the PSA regeneration (made on top of the product)
        if 'internal_leak' in self.faults:
            q = 20.0 * SCCM / 5e5 * max(self._p_int() - pa, 0.0) * h
            q = min(q, self.n_int * 0.5)
            d_int -= q
            L['h2_internal_leak_mol'] += q
        if self.state == 'depressurising':
            q = (self.n_int - pa * self._V() / RT) * (1 - math.exp(-h / 5.0))      # exhaust valve to the atmosphere (tau 5 s)
            d_int -= max(q, 0.0)
            L['h2_vented_mol'] += max(q, 0.0)
        self.n_int += d_int
        # application line: consumer draw and external leak
        p_line_g = self._p_line() - pa
        dem = self.consumer['demand_sccm'] * SCCM
        mp = self.consumer['min_pressure_pa']
        draw = dem if p_line_g >= mp else dem * max(p_line_g, 0.0) / max(mp, 1.0)
        leak = self.consumer['external_leak_sccm_at_5bar'] * SCCM / 5e5 * max(p_line_g, 0.0)
        avail = (self.n_int + self.n_line - pa * (self._V() + LINE_V) / RT) if self.valve else (self.n_line - pa * LINE_V / RT)
        out = min((draw + leak) * h, max(avail, 0.0))
        f_d = draw / (draw + leak) if draw + leak > 0 else 0.0
        L['h2_delivered_mol'] += out * f_d
        L['h2_external_leak_mol'] += out * (1 - f_d)
        if self.valve:                                                           # one node: split the withdrawal by volume
            tot = self.n_int + self.n_line - out
            self.n_int = tot * self._V() / (self._V() + LINE_V)
            self.n_line = tot - self.n_int
        else:
            self.n_line -= out
        self.delivered_ok = p_line_g >= mp
        self.last_draw = draw
        if 'cell_failure' in self.faults and self.powered and self.state in ('standby', 'running', 'diagnostics'):   # recognised as a cell fault, also during the power-up test
            self._latch('CELL_FAULT', '电解池故障：停止产氢')
        # ---- overpressure switch
        if self._p_int() - pa > ENG['overpressure_switch_pa'] and 'OVERPRESSURE' not in self.latched:     # switch on the gauge pressure
            self._latch('OVERPRESSURE', '超压开关动作：电解池停机')
        # ---- water: consumption, loading pump, de-ionizer
        if self.powered:
            w = (n_dot + purge) * M_W * h                                       # kg electrolysed
            loss = ENG['water_loss_base_l_day'] * 1e-3 * RHO_W / DAY * h
            take = min(self.tank * RHO_W, w + loss)
            frac_w = w / (w + loss) if w + loss > 0 else 0.0
            self.tank -= take / RHO_W
            L['water_electrolysed_kg'] += take * frac_w
            L['water_lost_kg'] += take * (1 - frac_w)
            fr = self._tank_frac()
            if fr < ENG['tank_low_fraction']:
                self.pump_on = True
            if fr >= ENG['tank_high_fraction']:
                self.pump_on = False
            if self.pump_on and 'loading_pump_failure' not in self.faults and self.bottle > 0:
                dv = min(ENG['loading_pump_m3_s'] * h, self.bottle, ENG['tank_volume_m3'] - self.tank)
                self.bottle -= dv
                self.tank_kappa = (self.tank * self.tank_kappa + dv * self.bottle_kappa) / (self.tank + dv)   # feed water mixes into the tank
                self.tank += dv
            if self.tank > 0:                                                  # the tank water circulates through the de-ioniser cartridge
                cap_ = ENG['deioniser_capacity_eq']
                brk = min(max((self.di_used - cap_) / (0.2 * cap_), 0.0), 1.0)
                k_out = 5e-6 + brk * max(self.tank_kappa - 5e-6, 0.0)
                f = 1 - math.exp(-ENG['deioniser_recirculation_m3_s'] * h / self.tank)
                dk = (self.tank_kappa - k_out) * f
                self.di_used += max(dk, 0.0) * self.tank / ENG['equivalent_conductance_s_m2_eq']
                self.tank_kappa -= dk
            self.di_age += h
            kc = self._kappa_cell()
            if kc > ENG['cell_water_conductivity_alarm_s_m']:
                self.membrane_dose += kc * h
        # ---- diagnostics sequence (power-up water check, pressurise, hold = leak test)
        if self.state == 'diagnostics':
            D = self.diag
            D['t'] += h
            if D['phase'] == 'water_check':
                if self._tank_frac() >= ENG['tank_low_fraction']:
                    D['phase'], D['t_test'] = 'pressurise', 0.0                    # the leak-test timing starts once the water check has passed
                    self._event('operation', 'WATER_OK', '水位正常，开始加压')
                elif D['t'] >= 180.0 and not D.get('water_low_logged'):
                    D['water_low_logged'] = True
                    self._event('alarm', 'WATER_LOW', '自检：无法加水到低水位（检查外接水瓶）')
            else:
                D['t_test'] += h
            if D['phase'] == 'pressurise':
                if self.p_meas >= ENG['leak_test_pressure_pa'] - 2000.0 or D['t_test'] >= DOC['leak_test_s'] - 60.0:
                    D['phase'], D['p0'], D['t_hold'] = 'hold', self.p_meas, D['t_test']
            elif D['phase'] == 'hold' and D['t_test'] >= DOC['leak_test_s']:
                decay = D['p0'] - self.p_meas
                if decay > ENG['leak_test_decay_limit_pa'] or D['p0'] < ENG['leak_test_pressure_pa'] * 0.5:
                    self._latch('LEAK_TEST_FAILED', f'泄漏测试未通过（保压下降{decay / 1e5:.2f} bar）')
                else:
                    self.state, self.diag = 'standby', None
                    self._event('operation', 'DIAGNOSTICS_OK', '自检与泄漏测试通过：待机')
        # ---- standby leak check (valve closed: production needed to hold the pressure means a leak)
        if self.state == 'standby' and not self.depressurised and abs(self.p_meas - self.settings['pressure_set_pa']) < 5e3:
            self.standby_hist.append(n_dot)                                   # only while holding at the set point (not while pressurising)
            del self.standby_hist[:-240]
            if len(self.standby_hist) >= 240:
                rate = min(self.standby_hist) * RT / self._V()                  # sustained make-up production (a tight system drops to zero)
                if rate > ENG['standby_leak_check_rate_pa_s']:
                    self._latch('INTERNAL_LEAK', '待机保压检测到机内泄漏：自动停机')
        else:
            self.standby_hist = []
        # ---- low pressure at full production (external leak / demand above capacity)
        gap = self.settings['pressure_set_pa'] - 3e4 - self.p_meas
        if (self.state == 'running' and n_dot >= N_MAX * 0.999 and gap > 0.0
                and (self.p_rate <= 0.0 or gap / self.p_rate > ENG['low_pressure_recovery_s'])):   # counts only if the pressure cannot recover in time
            self.maxflow_s += h
            if self.maxflow_s >= ENG['max_flow_duration_s']:
                self._latch('LOW_PRESSURE', '满负荷仍达不到设定压力（应用需求加外部泄漏超过最大产氢）：停止产氢')
        else:
            self.maxflow_s = 0.0
        if self.state == 'depressurising' and self._p_int() - pa < 5e3:
            self.state = 'standby'
            self._event('operation', 'DEPRESSURISED', '机内已去压（停止产氢）')
        # ---- dryer and purity
        if self.powered and self.state != 'off':
            self.dew_k += (DOC['dew_point_k'] - self.dew_k) * (1 - math.exp(-h / ENG['dryer_tau_s']))
        else:
            self.dew_k += (ENG['dew_point_initial_k'] - self.dew_k) * (1 - math.exp(-h / (30 * DAY)))
        # ---- power and heat
        P = (ENG['idle_power_w'] + ENG['cell_power_per_mol_s'] * (n_dot + purge)) if self.powered else 0.0
        self.P_el = P
        L['energy_j'] += P * h
        L['heat_j'] += (P - HHV_H2 * (n_dot + purge)) * h
        self._update_alarms()                                                     # a new alarm sounds again within the tick it appears

    def _latch(self, code, message):
        if code not in self.latched:
            self.latched.add(code)
            self.state, self.valve = 'fault', False
            if self.diag is not None:
                self.diag = None
            self._event('alarm', code, message)

    def _update_alarms(self):
        A = set(self.latched)
        if self.plant is not None and self.powered:
            if self._tank_frac() <= ENG['tank_critical_fraction'] or (self.state == 'diagnostics' and self.diag and self.diag['phase'] == 'water_check' and self.diag['t'] >= 180.0):
                A.add('WATER_LOW')
            if self._kappa_cell() > ENG['cell_water_conductivity_alarm_s_m']:
                A.add('WATER_QUALITY')
            if 'cell_failure' in self.faults:
                A.add('CELL_FAULT')
            if self._T() > DOC['max_ambient_k'] or self._T() < DOC['operating_ambient_k'][0]:
                A.add('AMBIENT_TEMPERATURE')
            if self.di_age > DOC['deioniser_interval_s']:
                A.add('DEIONISER_DUE')
        new = sorted(A)
        if (set(new) - set(self.alarms)) & set(LATCHED + ('CELL_FAULT',)):
            self.muted = False                                                    # a new sounding alarm sounds again (silent notices do not cancel the mute)
        self.alarms = new if self.powered else []

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read'):
            self.revision += 1
        if capability_id != 'sim.step':
            self._update_alarms()
        return out

    def _cmd_plant_configure(self, ambient_k, pressure_pa, mains_v, bottle_volume_m3, bottle_water_m3, feed_conductivity_s_m, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        if bottle_water_m3 > bottle_volume_m3 or (self.plant is not None and self.bottle > bottle_volume_m3 + 1e-12):
            raise ActionRejected('BOTTLE_OVERFLOW')                                # re-declaration keeps the bottle content: the new bottle must hold it
        first = self.plant is None
        self.plant = dict(ambient_k=ambient_k, pressure_pa=pressure_pa, mains_v=mains_v, bottle_volume_m3=bottle_volume_m3)
        self.seed = noise_seed
        if first:
            RT = R_GAS * ambient_k
            self.n_int = pressure_pa * self._V() / RT                             # depressurised generator (hydrogen at atmospheric pressure)
            self.n_line = pressure_pa * LINE_V / RT
            self.led['h2_initial_mol'] = self.n_int + self.n_line
            self.tank = 0.6 * ENG['tank_volume_m3']
            self.bottle, self.bottle_kappa = bottle_water_m3, feed_conductivity_s_m
            self.led['water_initial_kg'] = (self.tank + self.bottle) * RHO_W
        self._event('system', 'PLANT_CONFIGURED', '环境条件已声明')
        return dict(configured=True)

    def _cmd_consumer_set(self, demand_sccm, min_pressure_pa, external_leak_sccm_at_5bar=None):
        self._need_plant()
        self.consumer = dict(demand_sccm=demand_sccm, min_pressure_pa=min_pressure_pa,
                             external_leak_sccm_at_5bar=self.consumer['external_leak_sccm_at_5bar'] if external_leak_sccm_at_5bar is None else external_leak_sccm_at_5bar)
        return dict(demand_sccm=demand_sccm, min_pressure_pa=min_pressure_pa)

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not self.powered:
            self.powered = True
            self.state, self.diag = 'diagnostics', dict(phase='water_check', t=0.0)
            self.latched, self.muted, self.depressurised = set(), False, False
            self._event('system', 'POWER_ON', '通电：自检、加水与泄漏测试（约6 min）')
        elif not on and self.powered:
            if self.valve:
                tot = self.n_int + self.n_line
                self.n_int = tot * self._V() / (self._V() + LINE_V)
                self.n_line = tot - self.n_int
            self.powered, self.state, self.diag, self.valve, self.pump_on = False, 'off', None, False, False
            self.n_dot, self.P_el = 0.0, 0.0
            self._event('system', 'POWER_OFF', '断电：停止产氢（机内保留储氢）')
        return dict(on=self.powered)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(on=not self.powered)

    def _ready(self):
        self._need_power()
        if self.state == 'diagnostics':
            raise ActionRejected('DIAGNOSTICS_RUNNING')
        if self.state == 'fault':
            raise ActionRejected('FAULT_ACTIVE')

    def _cmd_cycle_start(self):
        self._ready()
        if self.state == 'depressurising':
            raise ActionRejected('DEPRESSURISING')
        if self.state != 'running':
            self.depressurised = False
            self.valve = True
            self.state = 'running'
            self.maxflow_s = 0.0
            self._event('operation', 'START', '开始供氢（输出阀打开）')
        return dict(state=self.state)

    def _cmd_cycle_stop(self):
        self._ready()
        if self.state == 'running':
            tot = self.n_int + self.n_line
            self.n_int = tot * self._V() / (self._V() + LINE_V)
            self.n_line = tot - self.n_int
            self.valve, self.state = False, 'standby'
            self.integ = 0.0                                                      # the load is gone: the integral would over-pressurise the stand-by
            self._event('operation', 'STOP', '停止供氢：待机（机内保压）')
        return dict(state=self.state)

    def _cmd_cycle_toggle(self):
        if self.powered and self.state == 'running':
            return self._cmd_cycle_stop()
        return self._cmd_cycle_start()

    def _cmd_settings_configure(self, pressure_set_pa=None, units=None):
        self._need_power()
        if pressure_set_pa is not None:
            self.settings['pressure_set_pa'] = float(pressure_set_pa)
        if units is not None:
            self.settings['units'] = units
        return dict(settings=dict(self.settings))

    def _cmd_output_depressurise(self):
        self._need_power()
        if self.state == 'diagnostics':
            raise ActionRejected('DIAGNOSTICS_RUNNING')
        if self.valve:
            tot = self.n_int + self.n_line
            self.n_int = tot * self._V() / (self._V() + LINE_V)
            self.n_line = tot - self.n_int
        self.valve, self.depressurised = False, True
        self.state = 'depressurising'
        self.integ = 0.0
        self.latched = set()                                                      # SHUT DOWN acknowledges a latched alarm (restart through START)
        self._event('operation', 'SHUT_DOWN', '停止产氢并去压（储氢经排气口排出）')
        return dict(state=self.state)

    def _cmd_alarm_mute(self):
        self._need_power()
        if not set(self.alarms) & set(LATCHED + ('CELL_FAULT',)):                   # only a sounding (fault) alarm can be muted
            raise ActionRejected('NO_ALARM')
        self.muted = True
        return dict(muted=True)

    def _cmd_water_refill(self, volume_m3, conductivity_s_m=None):
        self._need_plant()
        if self.bottle + volume_m3 > self.plant['bottle_volume_m3'] + 1e-12:
            raise ActionRejected('BOTTLE_OVERFLOW')
        k = self.bottle_kappa if conductivity_s_m is None else conductivity_s_m
        tot = self.bottle + volume_m3
        self.bottle_kappa = (self.bottle * self.bottle_kappa + volume_m3 * k) / tot if tot > 0 else k
        self.bottle = tot
        self.led['water_added_kg'] += volume_m3 * RHO_W
        self._event('water', 'REFILL', f'水瓶加水{volume_m3 * 1e3:.2f} L')
        return dict(bottle_water_m3=self.bottle)

    def _cmd_deioniser_replace(self):
        self._need_plant()
        if self.powered:
            raise ActionRejected('POWER_ON')
        self.di_used, self.di_age = 0.0, 0.0
        self._event('operation', 'DEIONISER_REPLACED', '更换去离子柱')
        return dict(replaced=True)

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s - 1e-9], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        v = self.read('evaluator')
        return dict(gas=v['truth.gas'], water=v['truth.water'], cell=v['truth.cell'], faults=v['truth.faults'])

    def _ledger(self):
        L = copy.deepcopy(self.led)
        L['h2_stored_mol'] = (self.n_int + self.n_line) if self.plant else 0.0
        L['water_in_tank_kg'] = self.tank * RHO_W
        L['water_in_bottle_kg'] = self.bottle * RHO_W
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
    def _display_pressure(self):
        if not self.powered or self.p_meas is None:
            return None
        v = max(self.p_meas + ENG['pressure_noise_pa'] * _gauss(self.seed, self.tick, 1), 0.0)
        return round(v / 1e5, 1) if self.settings['units'] == 'bar' else round(v / 6894.757, 0)

    def read(self, visibility='agent'):
        pw = self.powered and self.plant is not None
        p_del = (max(self.p_meas + ENG['pressure_noise_pa'] * _gauss(self.seed, self.tick, 1), 0.0) if (pw and self.p_meas is not None) else None)
        fault = bool(set(self.alarms) & set(LATCHED + ('CELL_FAULT',)))
        out = {'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.on': self.powered, 'state.current': self.state,
               'state.remaining_s': max(DOC['leak_test_s'] - self.diag.get('t_test', 0.0), 0.0) if (self.state == 'diagnostics' and self.diag) else None,
               'pressure.delivery_pa': p_del, 'pressure.set_pa': self.settings['pressure_set_pa'],
               'flow.percent': round(100.0 * self.n_dot / N_MAX, 1) if pw else None, 'display.pressure': self._display_pressure() if pw else None,
               'display.flow_percent': round(100.0 * self.n_dot / N_MAX) if pw else None,
               'water.level_ok': bool(self.plant is not None and self._tank_frac() >= ENG['tank_low_fraction']),
               'alarms.active': list(self.alarms) if pw else [], 'alarm.audible': bool(pw and self.alarms and not self.muted and fault),
               'indicator.ok': bool(pw and self.state in ('standby', 'running') and not self.depressurised and not fault and 'WATER_LOW' not in self.alarms),
               'indicator.water': bool(pw and 'WATER_LOW' in self.alarms), 'indicator.fault': bool(pw and fault),
               'indicator.depressurised': bool(pw and self.depressurised and self.state != 'fault'), 'settings.current': dict(self.settings),
               'deioniser.age_s': self.di_age}
        if visibility != 'agent':
            if self.plant is None:
                out['truth.gas'], out['truth.water'], out['truth.cell'] = {}, {}, {}
            else:
                pa = self._pa()
                o2 = ENG['o2_crossover_fault_mol_frac'] if 'membrane_crossover' in self.faults else ENG['o2_nominal_mol_frac'] * (1.0 + self.membrane_dose / 1.0)
                out['truth.gas'] = dict(internal_pressure_abs_pa=self._p_int(), line_pressure_abs_pa=self._p_line(), stored_h2_mol=self.n_int + self.n_line,
                                        product_mol_s=self.n_dot, purge_mol_s=ENG['psa_purge_fraction'] * self.n_dot, product_sccm=self.n_dot / SCCM,
                                        delivered_sccm=getattr(self, 'last_draw', 0.0) / SCCM, demand_met=bool(getattr(self, 'delivered_ok', False)),
                                        dew_point_k=self.dew_k, water_ppmv=e_ice_simple(self.dew_k) / max(self._p_line(), 1.0) * 1e6, o2_mol_frac=o2, purity=1.0 - o2,
                                        valve_open=self.valve, depressurised=self.depressurised)
                out['truth.water'] = dict(tank_m3=self.tank, tank_fraction=self._tank_frac(), bottle_m3=self.bottle, bottle_conductivity_s_m=self.bottle_kappa,
                                          pump_on=self.pump_on, deioniser_used_eq=self.di_used, deioniser_capacity_eq=ENG['deioniser_capacity_eq'],
                                          cell_water_conductivity_s_m=self._kappa_cell())
                out['truth.cell'] = dict(current_a=2 * F_CONST * (1 + ENG['psa_purge_fraction']) * self.n_dot, electrical_power_w=self.P_el,
                                         heat_w=self.P_el - HHV_H2 * (1 + ENG['psa_purge_fraction']) * self.n_dot, mains_current_a=self.P_el / self.plant['mains_v'],
                                         membrane_conductive_water_dose=self.membrane_dose, latched=sorted(self.latched))
            out['truth.faults'] = sorted(self.faults)
        return out


def e_ice_simple(T):
    """Saturation pressure over ice / water (Magnus-type, Pa; only for the truth ppmv of the output dew point)."""
    t = T - 273.15
    return 611.2 * math.exp(22.46 * t / (272.62 + t)) if t < 0 else 611.2 * math.exp(17.62 * t / (243.12 + t))


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
class PeakPrecisionhydrogenSession:
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
        meta = copy.deepcopy(export_peak_precisionhydrogen_contract_metadata())
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
    'PLANT_REQUIRED': '尚未声明环境条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '发生器未通电', 'POWER_ON': '须先断电（维护）',
    'DIAGNOSTICS_RUNNING': '自检与泄漏测试进行中（约6 min）', 'FAULT_ACTIVE': '有锁定报警（泄漏、超压、低压、电解池故障）：须SHUT DOWN去压或重新上电',
    'DEPRESSURISING': '去压进行中', 'NO_ALARM': '没有可静音的报警', 'BOTTLE_OVERFLOW': '水瓶装不下（超过瓶容积）',
}
_P = ['PLANT_REQUIRED']
_PW = _P + ['POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED', 'BOTTLE_OVERFLOW'], 'consumer.set': _P, 'power.set': _P, 'power.toggle': _P,
    'cycle.start': _PW + ['DIAGNOSTICS_RUNNING', 'FAULT_ACTIVE', 'DEPRESSURISING'], 'cycle.stop': _PW + ['DIAGNOSTICS_RUNNING', 'FAULT_ACTIVE'],
    'cycle.toggle': _PW + ['DIAGNOSTICS_RUNNING', 'FAULT_ACTIVE', 'DEPRESSURISING'], 'settings.configure': _PW, 'output.depressurise': _PW + ['DIAGNOSTICS_RUNNING'],
    'alarm.mute': _PW + ['NO_ALARM'], 'water.refill': _P + ['BOTTLE_OVERFLOW'], 'deioniser.replace': _P + ['POWER_ON'],
    'events.read': [], 'truth.inspect': [], 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子、水瓶存水与进水电导率；之后再声明须给同一种子，且只更新环境、电源与瓶容积（瓶中存水与电导率保持，新瓶须装得下）；存水不超过瓶容积', ['plant.configured']),
    'consumer.set': ('已声明环境条件', ['plant.configured']),
    'power.set': ('通电后先自检、加水与泄漏测试（约6 min）；断电停止产氢、保留机内储氢', ['power.on']), 'power.toggle': ('同power.set', ['power.on']),
    'cycle.start': ('已通电、自检完成、无锁定报警、不在去压中', ['power.on', 'state.current', 'alarms.active']),
    'cycle.stop': ('已通电、自检完成、无锁定报警', ['power.on', 'state.current']), 'cycle.toggle': ('同cycle.start / cycle.stop', ['power.on', 'state.current']),
    'settings.configure': ('已通电；设定压力≤6.9 bar表压', ['power.on']), 'output.depressurise': ('已通电且不在自检中；同时确认锁定报警', ['power.on', 'state.current']),
    'alarm.mute': ('已通电且有鸣响的报警（泄漏、超压、低压、电解池故障；缺水与水质提示不鸣响）', ['power.on', 'alarms.active']), 'water.refill': ('加水后不超过瓶容积', ['plant.configured']),
    'deioniser.replace': ('已断电', ['power.on']), 'fault.inject': ('无', ['plant.configured']),
}
_DISP = ['display.pressure', 'display.flow_percent', 'pressure.delivery_pa', 'flow.percent']
_IND = ['indicator.ok', 'indicator.water', 'indicator.fault', 'indicator.depressurised', 'alarms.active', 'alarm.audible']
_EVOLVE = ['state.current', 'state.remaining_s', 'water.level_ok', 'deioniser.age_s', 'truth.gas', 'truth.water', 'truth.cell'] + _DISP + _IND
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.gas', 'truth.water', 'truth.cell', 'water.level_ok'] + _IND,
    'consumer.set': ['truth.gas'],
    'power.set': ['power.on', 'state.current', 'state.remaining_s', 'truth.gas', 'truth.water', 'truth.cell'] + _DISP + _IND,
    'cycle.start': ['state.current', 'truth.gas'] + _IND, 'cycle.stop': ['state.current', 'truth.gas'] + _IND, 'cycle.toggle': ['state.current', 'truth.gas'] + _IND,
    'settings.configure': ['settings.current', 'pressure.set_pa', 'display.pressure'], 'output.depressurise': ['state.current', 'truth.gas', 'truth.cell'] + _IND,
    'alarm.mute': ['alarm.audible'], 'water.refill': ['truth.water'], 'deioniser.replace': ['deioniser.age_s', 'truth.water'] + _IND,
    'fault.inject': ['truth.faults', 'truth.gas', 'truth.water'] + _IND, 'sim.step': ['clock.time_s'],
}
EFFECTS['power.toggle'] = list(EFFECTS['power.set'])
ON_STEP = {k: list(_EVOLVE) for k in ('power.set', 'power.toggle', 'cycle.start', 'cycle.stop', 'cycle.toggle', 'settings.configure', 'output.depressurise', 'consumer.set',
                                      'water.refill', 'deioniser.replace', 'fault.inject', 'plant.configure', 'alarm.mute')}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {
    'sim.step': '按0.5 s网格积分：自检/泄漏测试、按设定压力产氢（法拉第、PSA吹扫）、机内与输出管路储氢、应用取用与外漏、超压/低压/机内泄漏、水箱与加水泵、去离子柱消耗、干燥露点、电功率与散热、报警与灯光',
    'power.set': '自检、加水与泄漏测试约6 min后待机', 'cycle.start': '输出阀打开后按设定压力向应用供氢', 'settings.configure': '产氢按新的设定压力调节',
    'output.depressurise': '储氢经排气口排出后停止产氢', 'consumer.set': '应用取用与外漏改变产氢与压力', 'water.refill': '加水泵从水瓶补水', 'deioniser.replace': '新柱重新开始吸附离子',
    'fault.inject': '故障作用于泄漏、膜、加水泵、电解池或压力传感器', 'plant.configure': '环境温度影响储氢压力与报警', 'alarm.mute': '新的报警出现时重新鸣响',
}
EXEC_TEXT = {'power.set': ('state.current经diagnostics（约6 min）变为standby，或泄漏测试失败为fault', '断电中止自检'),
             'cycle.start': ('state.current为running，pressure.delivery_pa趋近设定压力', '满负荷仍达不到设定压力超过10 min报LOW_PRESSURE并停止产氢')}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('water.refill', 'deioniser.replace'):
    RESOURCES[_k] = ['host_clock', 'water_supply']
OBSERVES = {'power.set': ['state.current'], 'cycle.start': ['state.current', 'pressure.delivery_pa'], 'output.depressurise': ['state.current', 'pressure.delivery_pa']}
_WB, _D23, _D19, _IG, _SL, _SIM = ('user_workbook', 'manufacturer_datasheet_2023', 'manufacturer_datasheet_2019', 'manufacturer_installation_guide', 'same_series_sl_user_manual',
                                   'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({'power.set': [_IG, _SL], 'power.toggle': [_IG], 'cycle.start': [_IG, _D23], 'cycle.stop': [_IG], 'cycle.toggle': [_IG], 'settings.configure': [_IG, _D23],
                    'output.depressurise': [_IG, _SL], 'alarm.mute': [_IG], 'water.refill': [_IG, _D23], 'deioniser.replace': [_D23, _SL], 'consumer.set': [_D23, _SIM],
                    'fault.inject': [_SL, _SIM]})
CAP_SOURCES.update({k: [_D23] for k in UNSUPPORTED})
PARAM_ZH = {'ambient_k': '环境温度', 'pressure_pa': '大气压', 'mains_v': '电源电压', 'bottle_volume_m3': '外接水瓶容积', 'bottle_water_m3': '水瓶初始存水',
            'feed_conductivity_s_m': '进水电导率（ASTM II要求<1 µS/cm=1e-4 S/m）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）',
            'demand_sccm': '应用需求流量（sccm，273.15 K、1.01 bar）', 'min_pressure_pa': '应用所需最低表压', 'external_leak_sccm_at_5bar': '输出管路外漏（5 bar表压下的sccm）',
            'on': '是否通电', 'pressure_set_pa': '设定输出表压', 'units': '显示单位', 'volume_m3': '加水体积', 'conductivity_s_m': '所加水的电导率',
            'fault': '故障名', 'active': '注入或清除', 'since_s': '起始时间', 'dt_s': '推进时间'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.pem_hydrogen_generation.limitations。'
CAP_LIMITS = {
    'consumer.set': ['应用按“需求流量＋最低压力”的流量控制器抽象：压力不足时取用按比例减少；外漏按与表压成正比的导率。'],
    'power.set': ['自检顺序（查水位→加压到4 bar→保压至约6 min）与泄漏判据为工程值；同系列SL手册写泄漏测试约6 min。'],
    'water.refill': ['水瓶与水箱按单节点体积；去离子柱按离子当量容量吸附，穿透后电解池进水电导率逐步升到进水值（工程值）。'],
}
LIMITATIONS = [
    '所选配置：Precision Hydrogen Trace 500（64-0500，存档型号，产品页标停产）：PEM电解＋再生式PSA干燥器，自动加水泵与外接4 L水瓶，触摸屏；无集成压缩机。',
    '产氢与压力：机内储气为单一容积（0.4 L，同系列SL为175 mL；工程值）在环境温度下按理想气体；产氢按压力误差比例积分控制（防积分饱和）并限于500 sccm（273.15 K、1.01 bar基准）；没有电解池与PSA塔的详细动力学。',
    '耗水与功率：PSA吹扫比例与固定耗水按样本0.4–1.2 L/天标定，电解池单位产氢功率按787 W（最大产氢）标定；样本散热1000 BTU/h（约293 W）与787 W功耗不能同时由能量平衡满足（见冲突），运行层散热=电功率−产氢高热值。',
    '纯度：99.99999 %按氧含量（NPL验证）——运行层只给氧与水（露点）两项杂质；露点由干燥器时间常数从初值降到−75 °C（2019版样本），24 h运行后接近规格。',
    '诊断与报警：泄漏测试、待机保压检漏、满负荷低压停机、超压开关、临界水位与去离子柱判据为按样本与同系列手册描述的工程实现；灯光：六边形灯带绿色表示正常为产品照片的解读，水箱视窗通电即亮（同系列手册：上电时水箱会亮），红色故障边框对应安装指南的红色故障画面，缺水与去压图标的形状颜色为工程表示。',
    '应用、管路、水瓶与氢气在场景中没有几何；场景中触摸屏数字、六边形灯与按键按运行层状态变化。',
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
    src_of = {'manufacturer_datasheet_2023': _D23, 'manufacturer_datasheet_2019': _D19, 'manufacturer_installation_guide': _IG, 'same_series_sl_user_manual': _SL}
    WB_KEYS = ('max_flow_mol_s', 'flow_reference', 'max_pressure_pa', 'purity', 'purity_basis', 'water_consumption_l_day', 'power_w', 'water_conductivity_max_s_m',
               'dimensions_m', 'mass_kg')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
        if v['source_id'] == 'same_series_sl_user_manual':
            P[k.lower()]['uncertainty'] = '取自同系列Precision Hydrogen SL用户手册（较小型号）：Trace 500可能不同'
    cal = {k for k, v in instrument()['engineering'].items() if v.get('calibrated_by')}
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明），不是实测' +
            ('；由calibrate_h2.py按样本耗水与功耗标定' if k in cal else ''))
    asm('application_line_volume_m3', LINE_V, 'm3', '输出到应用的管路容积（2 m 1/8"管＋接头）', '工程值')
    asm('faraday_constant_c_mol', F_CONST, 'C/mol', '法拉第常数（CODATA）', '物理常数', ('simulation_assumption',))
    asm('h2_higher_heating_value_j_mol', HHV_H2, 'J/mol', '氢的高热值（生成液态水的标准焓）', '物理常数（NIST）', ('simulation_assumption',))
    return P


SLOT_REFERENCE = {'pressure.delivery_pa': '表压（相对环境大气压）', 'pressure.set_pa': '表压', 'flow.percent': '产氢量占500 sccm的百分比',
                  'display.pressure': '按设定单位（bar或psi）显示的表压数字', 'display.flow_percent': '屏幕% FLOW数字'}


def export_peak_precisionhydrogen_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'PeakPrecisionhydrogenSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.peak_precisionhydrogen.PeakPrecisionhydrogenSession.invoke',
                                              invocation=f'session.invoke(instance_id="peak_precisionhydrogen_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='PeakPrecisionhydrogenRuntime.invoke')],
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
        component_ids=['pem_cell', 'psa_dryers', 'water_tank', 'loading_pump', 'deioniser_column', 'touch_screen', 'rear_fittings'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='PEM电解制氢：去离子水在阳极分解为氧、质子与电子，质子穿过膜在阴极生成氢；氢经再生式PSA干燥后按设定压力按需供给应用；'
                   '水箱由加水泵从外接水瓶补水并经去离子柱；自检泄漏测试、待机检漏、超压与低压停机、临界水位保护',
        equations=['n_dot_H2 = I / (2 F);  n_dot = clip(K e + I_int, 0, 500 sccm), dI_int/dt = K e / T_i (not while saturated);  cell makes (1 + f_PSA) n_dot',
                   'V_int dn/dt = n_dot - (leaks) - (vent);  p = n R T / V;  valve open: one node V_int + V_line',
                   'application: draw = demand if p_line >= p_min else demand p_line / p_min;  leak = c (p_line - p_atm)',
                   'water: dm/dt = (1 + f_PSA) n_dot M_w + m_dot_loss;  tank refilled from the bottle between the low / high level sensors',
                   'tank: kappa_tank mixes with the feed; de-ionizer loop: d(kappa_tank)/dt = -(kappa_tank - kappa_out) q_rec / V_tank, d(used)/dt = (kappa_tank - kappa_out) q_rec / Lambda_eq;  kappa_cell = kappa_tank',
                   'dryer: dT_dew/dt = (T_dew,spec - T_dew) / tau_dryer;  P = P_idle + k_cell (1 + f_PSA) n_dot;  heat = P - HHV (1 + f_PSA) n_dot'],
        parameters=_params(), source_ids=[_SIM, _WB, _D23, _D19, _IG, _SL], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_h2_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('peak_precisionhydrogen', caps, slots, {'pem_hydrogen_generation': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'consumer.set': (CONSUMER, CONSUMER_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'pem_hydrogen_generation': mechanism})
