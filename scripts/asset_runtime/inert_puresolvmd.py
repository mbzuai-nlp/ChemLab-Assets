"""CHEM-200 Inert PureSolv MD 7 solvent purification system (7 solvents, detailed Specifications configuration): process runtime.

Device (product page, detailed Specifications; 2024 data sheet; user procedures of installed PureSolv / Grubbs-type systems: UCD SOP for a PureSolv
400-3-MD, Texas A&M SDS manual): 7 solvents, 17 L 304 stainless reservoirs pushed by inert gas through individual regulators, dual columns
(101 mm x 635 mm, ~4 L each; 800 L drying capacity on HPLC-grade solvent), 7 micron particle filter, 5-way evacuate / refill / dispense valve
("eliminates possibility of dispensing solvent into the vacuum pump"), stainless needle valve, collection-flask inert-gas refill through an individual
low-pressure regulator with a 7 psi relief and a check valve, 24/40 flask joints, vacuum manifold with a 20 L/min / 6 Torr pump, 0 to -30 inHg vacuum
gauge, 0-100 psi pressure gauge, 2-way isolation ball valve, 70 psi main relief, grounding reel, flammable cabinet.

Process model (fixed 0.1 s grid anchored at t = 0; quiescent intervals are skipped exactly; SI units: Pa absolute inside, gauge readings in the
gauge slots, m3, kg, mol, K):
  * Gas: building supply (plant) -> isolation valve -> inert-gas manifold (pressure gauge, 70 psi relief) -> per channel a keg push regulator
    (keg head pressure, regulator creep fault, 60 psi keg relief from the Texas A&M manual) and a flask refill regulator (7 psi relief, check valve).
  * Vacuum: pump (20 L/min, 6 Torr ultimate) on a manifold volume; flasks connected through the 5-way valve conductance; liquid left in an evacuated
    flask evaporates at its vapour pressure (solvent template) and is pumped away (solvent load on the pump).
  * Liquid path per channel: keg -> outlet valve -> column inlet valve -> column 1 -> column 2 -> column outlet valve -> filter -> 5-way valve
    (dispense) -> needle valve -> line hold-up -> flask.  Laminar flow Q = dP / (mu (R_col1 + R_col2 + R_filter + R_needle / opening)), dP = keg head -
    flask pressure - rho g h (viscosity and density from the solvent templates).
  * Purification: each column holds water (alumina / molecular sieves) up to a capacity; the outlet water follows a breakthrough curve of the column
    loading; the Q5 column of the hydrocarbon channels removes dissolved oxygen the same way; alumina-only channels do not degas (sparging the keg
    does: exponential, UCD 45 min for 15 L).  Stabilisers in the feed use up column capacity.
  * Flask: liquid volume, water, oxygen; gas moles (inert / air fractions, vapour); purging by evacuate / refill cycles; residual air and undried
    glass walls contaminate the dispensed solvent (water and oxygen); 2/3 fill rule; refill pushes the line hold-up into the flask; turning the
    5-way valve through "evacuate" with solvent in the line sends it into the vacuum line; leaving "refill" with solvent in the flask floods the gas
    manifold (UCD warnings).
  * Ledgers: solvent mass and water mass of every channel and the flask / pump / vent gas moles close exactly."""
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.inert_puresolvmd"
CATALOG_ID = "CHEM-200"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t2_vacuum/inert_puresolvmd'
LIBS = {'instrument': 'instrument_model.json', 'solvents': 'solvent_templates.json'}


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
SOLV = _lib('solvents')['substances']
SOLVENT_IDS = sorted(SOLV)
DT = 0.1
T0 = 273.15
G_ACC = 9.80665
R_GAS = 8.314462618
PSI = 6894.757293168
INHG = 3386.389
N_CH = 7
M_AIR = 0.0289647
M_W = 0.01801528
POSITIONS = ['closed', 'evacuate', 'dispense', 'refill']               # clockwise order seen from the front: top, left, bottom, right
ANGLE = {'closed': 0.0, 'evacuate': math.pi / 2, 'dispense': math.pi, 'refill': -math.pi / 2}
FAULTS = ['pump_failure', 'gas_supply_empty', 'filter_clogged', 'flask_joint_leak', 'check_valve_leak', 'regulator_creep']
GAUGE_SWEEP = 1.5 * math.pi
NEEDLE_TURNS = 3.0
MAX_EVENTS_LOG = 3000
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


def p_vap(sid, T):
    """Vapour pressure (Pa) of a solvent template: NIST Antoine fit (shared library) or a measured point with a Trouton slope."""
    v = SOLV[sid]['vapour_pressure']
    if v['kind'] == 'antoine':
        return 10 ** (v['A'] - v['B_k'] / (T + v['C_k'])) * 1e5
    return v['p_ref_pa'] * math.exp(-v['dh_vap_j_mol'] / R_GAS * (1.0 / T - 1.0 / v['t_ref_k']))


def p_sat_water(T):
    t = T - T0
    return 610.94 * math.exp(17.625 * t / (t + 243.04))


def breakthrough(loading_fraction):
    x = (loading_fraction - ENG['breakthrough_center']) / ENG['breakthrough_width']
    return 1.0 / (1.0 + math.exp(-max(min(x, 60.0), -60.0)))


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
CHANNEL = integer(1, N_CH)


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


SOLVENT_SLOT = nullable(enum(SOLVENT_IDS))
PLANT_EXAMPLE = dict(ambient_k=295.15, relative_humidity=0.45, pressure_pa=101325.0, gas='nitrogen', supply_gauge_pa=50.0 * PSI,
                     solvents=['tetrahydrofuran', 'dichloromethane', 'toluene', 'hexane', 'diethyl_ether', 'acetonitrile', 'dmf'], noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 288.15, 308.15), relative_humidity=number('1', 0.0, 0.9), pressure_pa=number('Pa', 80000.0, 110000.0),
                 gas=enum(['nitrogen', 'argon']), supply_gauge_pa=number('Pa', 0.0, 120.0 * PSI), solvents=array(SOLVENT_SLOT, N_CH, N_CH),
                 initial_fill_m3=number('m3', 0.0, 0.017), feed_water_ppm=number('ppm', 0.0, 2000.0), noise_seed=integer(0, 2 ** 31 - 1)),
            optional=('initial_fill_m3', 'feed_water_ppm'))
REG = obj(dict(channel=CHANNEL, keg_gauge_pa=number('Pa', 0.0, 30.0 * PSI), flask_gauge_pa=number('Pa', 0.0, 10.0 * PSI)), optional=('keg_gauge_pa', 'flask_gauge_pa'))
LINE = obj(dict(channel=CHANNEL, keg_gas=BOOL, keg_outlet=BOOL, column_inlet=BOOL, column_outlet=BOOL), optional=('keg_gas', 'keg_outlet', 'column_inlet', 'column_outlet'))
FLASK = obj(dict(channel=CHANNEL, volume_m3=number('m3', 5e-5, 0.005), oven_dried=BOOL), optional=('oven_dried',))
REFILL = obj(dict(channel=CHANNEL, volume_m3=number('m3', 1e-4, 0.017), water_ppm=number('ppm', 0.0, 5000.0), stabiliser_ppm=number('ppm', 0.0, 1000.0), grounded=BOOL),
             optional=('water_ppm', 'stabiliser_ppm', 'grounded'))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'operation', 'warning', 'maintenance', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
_BODY = ['frame']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明室温湿度、大气压、惰性气种类与楼宇供气表压、7路溶剂（模板库编号或空）、初始储罐液量与进料含水、噪声种子', 'evaluator', 'configuration')
cap('gas.isolation', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['isolation_lever'], '主隔离球阀开/关（惰性气主路）')
cap('regulator.set', REG, obj(dict(channel=CHANNEL, keg_gauge_pa=number('Pa'), flask_gauge_pa=number('Pa'))), dict(channel=1, keg_gauge_pa=15.0 * PSI, flask_gauge_pa=3.0 * PSI),
    ['gas_manifold'], '第k路储罐推送调压阀与接收瓶回填调压阀设定（表压）')
cap('pump.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['pump_switch'], '真空泵开/关')
cap('pump.toggle', obj({}), obj(dict(on=BOOL)), {}, ['pump_switch'], '真空泵开关按一下')
cap('valve.turn', obj(dict(channel=CHANNEL, position=enum(POSITIONS), direction=enum(['shortest', 'clockwise', 'counterclockwise'])), optional=('direction',)),
    obj(dict(channel=CHANNEL, position=TEXT, passed=array(TEXT, 0, 4))), dict(channel=1, position='evacuate'), [f'valve_lever_{k}' for k in range(1, N_CH + 1)],
    '把第k路五通阀手柄转到关闭/抽空/分配/回填（转动经过的中间位置也会接通）')
cap('needle.set', obj(dict(channel=CHANNEL, opening=number('1', 0.0, 1.0))), obj(dict(channel=CHANNEL, opening=number('1'))), dict(channel=1, opening=0.5),
    [f'needle_valve_{k}' for k in range(1, N_CH + 1)], '第k路针阀开度（0关、1全开：3圈）')
cap('line.set', LINE, obj(dict(channel=CHANNEL, valves=FREE_OBJ)), dict(channel=1, keg_gas=True, keg_outlet=True, column_inlet=True, column_outlet=True), ['plumbing'],
    '第k路储罐进气/出液快接阀与柱进出口阀开/关', 'agent', 'robot_skill')
cap('flask.attach', FLASK, obj(dict(channel=CHANNEL, attached=BOOL)), dict(channel=1, volume_m3=0.0005, oven_dried=True), [f'flask_joint_{k}' for k in range(1, N_CH + 1)],
    '把接收瓶（Straus瓶或滴液漏斗）装到第k路24/40接口（瓶内为室内空气；可声明是否烘干）', 'agent', 'robot_skill')
cap('flask.detach', obj(dict(channel=CHANNEL)), obj(dict(flask=FREE_OBJ)), dict(channel=1), [f'flask_joint_{k}' for k in range(1, N_CH + 1)],
    '取下第k路接收瓶（须五通阀关闭）：可称量与可读的溶剂体积与质量、瓶内表压；含水与溶解氧本机不测（评测者真值）', 'agent', 'robot_skill')
cap('keg.refill', REFILL, obj(dict(channel=CHANNEL, volume_m3=number('m3'), keg_m3=number('m3'))), dict(channel=1, volume_m3=0.010, water_ppm=100.0, grounded=True),
    ['reservoir_1', 'reservoir_2', 'reservoir_3', 'reservoir_4', 'reservoir_5', 'reservoir_6', 'reservoir_7'],
    '给第k个储罐加注原溶剂（柜门开、储罐出液与进气阀关；去压后加注，接地线夹好；溶剂不得含稳定剂）', 'agent', 'robot_skill')
cap('keg.sparge', obj(dict(channel=CHANNEL, duration_s=number('s', 60.0, 4 * 3600.0))), obj(dict(channel=CHANNEL, until_s=number('s'))), dict(channel=1, duration_s=2700.0),
    ['reservoir_1', 'reservoir_2', 'reservoir_3', 'reservoir_4', 'reservoir_5', 'reservoir_6', 'reservoir_7'],
    '用惰性气从出液口鼓泡除氧（UCD：15 L储罐45 min；排气到通风橱，带走溶剂蒸气）', 'agent', 'robot_skill')
cap('column.replace', obj(dict(channel=CHANNEL)), obj(dict(channel=CHANNEL, replaced=BOOL)), dict(channel=1), ['columns'],
    '更换第k路两根纯化柱（新柱；须柱进出口阀关）', 'agent', 'robot_skill')
cap('cabinet.door', obj(dict(side=enum(['left', 'right']), angle_rad=number('rad', 0.0, 1.7))), obj(dict(side=TEXT, angle_rad=number('rad'))), dict(side='left', angle_rad=1.5),
    ['cabinet_door_left', 'cabinet_door_right'], '易燃品柜左/右门转到给定角度', 'agent', 'robot_skill')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _BODY,
    '事件记录（操作、警告、维护）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(channels=array(FREE_OBJ, N_CH, N_CH), manifold=FREE_OBJ, faults=array(TEXT, 0, 16), removed_flasks=array(FREE_OBJ, 0, 100000))), {}, _BODY,
    '评测者读取过程真值：储罐液量、含水、溶解氧与顶压，柱负荷，接收瓶内容与气体，歧管压力；已取下接收瓶的含水、溶解氧与是否在惰性气下', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '每路溶剂质量、水量与接收瓶气体摩尔数的守恒账', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL, channel=CHANNEL), optional=('channel',)), obj(dict(faults=array(TEXT, 0, 32))),
    dict(fault='filter_clogged', active=True, channel=2), _BODY, '故障注入：真空泵故障、供气耗尽、第k路过滤器堵塞、接口漏气、单向阀漏、调压阀漂移', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * DAY))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'column.regenerate': '柱再生套件为选件（数据表OPTIONS；未选）：用column.replace换新柱。',
    'glovebox.dispense': '手套箱集成为选件（未选）。',
    'keg.level_read': '储罐为不透明不锈钢罐，本配置没有液位计（同类系统的数字液位计不在PureSolv规格中）。',
    'water.measure': '本机不测含水；溶剂含水须另用卡尔费休滴定（真值见truth.inspect）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'frame', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'frame', '环境与溶剂配置是否已声明')
slot('channels.solvents', array(SOLVENT_SLOT, N_CH, N_CH), '1', 'setpoint', 'frame', '7路溶剂（宿主声明；标签上的溶剂名）')
slot('pump.on', BOOL, '1', 'setpoint', 'vacuum_pump', '真空泵是否开')
slot('gas.isolation_open', BOOL, '1', 'setpoint', 'isolation_valve', '主隔离阀是否开')
slot('gas.isolation_angle_rad', number('rad'), 'rad', 'setpoint', 'isolation_lever', '主隔离阀手柄角（0关、π/2开）')
slot('gauge.vacuum_pa', number('Pa'), 'Pa', 'sensor', 'vacuum_gauge', '真空表读数（表压，0至−30 inHg≈−101.6 kPa；指针读数）')
slot('gauge.pressure_pa', number('Pa'), 'Pa', 'sensor', 'pressure_gauge', '压力表读数（惰性气歧管表压，0–100 psi）')
slot('gauge.vacuum_angle_rad', number('rad'), 'rad', 'derived', 'vacuum_gauge_needle', '真空表指针转角（0为零位，270°满量程）')
slot('gauge.pressure_angle_rad', number('rad'), 'rad', 'derived', 'pressure_gauge_needle', '压力表指针转角')
slot('cabinet.left_angle_rad', number('rad'), 'rad', 'setpoint', 'cabinet_door_left', '易燃品柜左门开启角')
slot('cabinet.right_angle_rad', number('rad'), 'rad', 'setpoint', 'cabinet_door_right', '易燃品柜右门开启角')
for _k in range(1, N_CH + 1):
    slot(f'valve.ch{_k}_position', enum(POSITIONS), '1', 'setpoint', f'valve_lever_{_k}', f'第{_k}路五通阀位置')
    slot(f'valve.ch{_k}_angle_rad', number('rad'), 'rad', 'setpoint', f'valve_lever_{_k}', f'第{_k}路五通阀手柄角（关0、抽空π/2、分配π、回填−π/2）')
    slot(f'needle.ch{_k}_opening', number('1'), '1', 'setpoint', f'needle_valve_{_k}', f'第{_k}路针阀开度（0–1）')
    slot(f'needle.ch{_k}_angle_rad', number('rad'), 'rad', 'setpoint', f'needle_valve_{_k}', f'第{_k}路针阀手轮转角（3圈全开）')
    slot(f'gauge.station{_k}_pa', number('Pa'), 'Pa', 'sensor', f'station_gauge_{_k}', f'第{_k}路工位压力表读数（接收瓶气路表压，0–15 psi；负压读0）')
    slot(f'gauge.station{_k}_angle_rad', number('rad'), 'rad', 'derived', f'station_gauge_needle_{_k}', f'第{_k}路工位压力表指针转角')
    slot(f'flask.ch{_k}', FREE_OBJ, '1', 'derived', f'flask_joint_{_k}', f'第{_k}路接收瓶：是否装上、容积、可见液位（液体体积）')
slot('regulators.settings', FREE_OBJ, '1', 'setpoint', 'gas_manifold', '各路储罐推送与接收瓶回填调压阀设定（表压）')
slot('lines.valves', FREE_OBJ, '1', 'setpoint', 'plumbing', '各路储罐进气/出液与柱进出口阀状态')
slot('reservoirs.recorded', FREE_OBJ, '1', 'derived', 'cabinet', '各储罐操作记录：累计加注体积与已取下接收瓶中的溶剂体积（UCD：须记录加注量以估计柱再生时间）')
slot('truth.channels', FREE_OBJ, '1', 'process_truth', 'columns', '各路储罐液量/含水/溶解氧/顶压、柱吸水与除氧负荷、出口含水、接收瓶内容与气体', 'evaluator')
slot('truth.manifold', FREE_OBJ, '1', 'process_truth', 'plumbing', '惰性气歧管与真空歧管的绝对压力、泵抽走的溶剂', 'evaluator')
slot('truth.faults', array(TEXT, 0, 32), '1', 'process_truth', 'frame', '已注入的故障', 'evaluator')
_NEEDLES = ['gauge.vacuum_angle_rad', 'gauge.pressure_angle_rad'] + [f'gauge.station{k}_angle_rad' for k in range(1, N_CH + 1)]
_GAUGES = ['gauge.vacuum_pa', 'gauge.pressure_pa'] + [f'gauge.station{k}_pa' for k in range(1, N_CH + 1)]
_JOINT_SLOTS = ([f'valve.ch{k}_angle_rad' for k in range(1, N_CH + 1)] + [f'needle.ch{k}_angle_rad' for k in range(1, N_CH + 1)] + _NEEDLES
                + ['gas.isolation_angle_rad', 'cabinet.left_angle_rad', 'cabinet.right_angle_rad'])
SCENE_MIRRORED = set(_JOINT_SLOTS)


# ---------------------------------------------------------------- runtime
def _new_channel(sid):
    pk = SOLV[sid]['column_packing'] if sid else 'alumina'
    cap = ENG['column_water_capacity_kg']
    caps = [cap * (ENG['sieve_capacity_factor'] if pk == 'sieves' else 1.0), cap * (ENG['sieve_capacity_factor'] if pk == 'sieves' else 1.0)]
    if pk == 'alumina_q5':
        caps[1] = 0.0                                                       # the second column is Q5 (oxygen scavenger), no drying
    return dict(solvent=sid, packing=pk,
                keg=dict(v=0.0, water=0.0, o2=0.0, stab=0.0, gas_open=True, outlet_open=True, p_head=None, reg_keg=15.0 * PSI, reg_flask=3.0 * PSI, creep=0.0, creep_ticks=0,
                         sparge_until=-1.0, added=0.0, out=0.0, low_warned=False, n_gas=0.0),
                col=dict(inlet=True, outlet=True, load=[0.0, 0.0], cap=caps, stab_load=0.0, o2_load=0.0,
                         o2_cap=ENG['q5_o2_capacity_kg'] if pk == 'alumina_q5' else 0.0, warned=False),
                valve='closed', angle=0.0, needle=0.0, line=dict(v=0.0, water=0.0, o2=0.0),
                flask=None, refill_since=None, flood_warned=False)


class InertPuresolvmdRuntime:
    def __init__(self, instance_id='inert_puresolvmd_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.isolation = False
        self.pump = False
        self.doors = dict(left=0.0, right=0.0)
        self.ch = [_new_channel(None) for _ in range(N_CH)]
        self.p_vm = None                                                    # vacuum manifold (Pa abs)
        self.removed = []                                                   # full records of flasks taken off (evaluator truth)
        self.vm_air = 1.0                                                   # fraction of room-air gas (air, O2, water vapour) in the vacuum manifold
        self.p_im = None                                                    # inert-gas manifold (Pa abs)
        self.faults = set()
        self.fault_ch = {}
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
        return InertPuresolvmdSession(self, name)

    def _event(self, kind, code, message):
        self.events.append(dict(time_s=round(self.t, 6), kind=kind, code=code, message_zh=message))
        if len(self.events) > MAX_EVENTS_LOG:
            self.events_dropped += len(self.events) - MAX_EVENTS_LOG
            del self.events[:-MAX_EVENTS_LOG]

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _ch(self, channel):
        c = self.ch[channel - 1]
        if c['solvent'] is None:
            raise ActionRejected('EMPTY_CHANNEL')
        return c

    # ---- derived quantities
    def _T(self):
        return self.plant['ambient_k']

    def _pa(self):
        return self.plant['pressure_pa']

    def _rho(self, sid):
        return SOLV[sid]['density']['value']

    def _mu(self, sid):
        return SOLV[sid]['viscosity']['value']

    def _supply_ok(self):
        return self.plant is not None and 'gas_supply_empty' not in self.faults and self.plant['supply_gauge_pa'] > 0.0

    def _flask_gas_volume(self, f):
        return max(f['vol'] - f['liq'], 1e-7)

    def _flask_p(self, c):
        f = c['flask']
        if f is None:
            return self._pa()
        p = (f['n_inert'] + f['n_air'] + f['n_o2'] + f['n_h2o']) * R_GAS * self._T() / self._flask_gas_volume(f)
        if f['liq'] > 1e-12:
            p += p_vap(c['solvent'], self._T())
        return p

    def _keg_gas_volume(self, c):
        return max(DOC['reservoir_volume_m3'] + ENG['keg_gas_headspace_min_m3'] - c['keg']['v'], ENG['keg_gas_headspace_min_m3'])

    def _quiet(self):
        """True when nothing evolves: no open valve path, no pump pumping a non-equilibrated manifold, no sparging, no creep, no leaks."""
        if 'flask_joint_leak' in self.faults:
            return False
        if self._feed() and abs(self.p_im - self._p_supply()) > 1e-9:          # the manifold refills from the building supply
            return False
        if self.pump and 'pump_failure' not in self.faults and self.p_vm > DOC['pump_ultimate_pa'] * (1.0 + 1e-9):
            return False
        for c in self.ch:
            if c['solvent'] is None:
                continue
            if c['valve'] != 'closed' or self.t < c['keg']['sparge_until']:
                return False
            draw, n_new = self._keg_plan(c)
            if abs(n_new - c['keg']['n_gas']) > 1e-12 * max(c['keg']['n_gas'], 1e-9):
                return False
            f = c['flask']
            if f is not None and f['liq'] > 0.0 and f['n_h2o'] > 0.0:
                return False
        return True

    def _keg_head(self, k, c, n_ticks):
        """Keg head pressure after n grid ticks: the push regulator (a creeping regulator drifts linearly, counted in whole ticks so that any
        step partition gives the same value) and the 60 psi keg relief."""
        kg, pa = c['keg'], self._pa()
        if 'regulator_creep' in self.faults and self.fault_ch.get('regulator_creep') == k + 1:
            kg['creep_ticks'] += n_ticks
            kg['creep'] = ENG['regulator_creep_pa_s'] * DT * kg['creep_ticks']
        RT = R_GAS * self._T()
        Vg = self._keg_gas_volume(c)
        draw, n_new = self._keg_plan(c)                                      # gas moles: the headspace keeps its gas unless the regulator adds or vents
        if draw > 0.0 and not self._feed():
            self._draw_manifold(draw)                                        # an isolated manifold supplies the gas: its pressure falls
        kg['n_gas'] = n_new
        kg['p_head'] = kg['n_gas'] * RT / Vg
        wants = kg['gas_open'] and kg['reg_keg'] + kg['creep'] > DOC['tamu_keg_relief_pa'] and self.p_im - pa > DOC['tamu_keg_relief_pa']
        if kg['p_head'] - pa > DOC['tamu_keg_relief_pa'] or wants:
            kg['p_head'] = min(kg['p_head'], pa + DOC['tamu_keg_relief_pa'])
            if not kg.get('relief_warned'):
                kg['relief_warned'] = True
                self._event('warning', 'KEG_RELIEF', f'第{k + 1}储罐超过60 psi：储罐泄压阀放气')
        kg['n_gas'] = kg['p_head'] * Vg / RT

    def _keg_plan(self, c):
        """(mol drawn from the manifold, new headspace moles) for the push regulator: below its set point it admits gas - from the building
        supply without limit, from an isolated manifold at most until the two pressures are equal; above its set point (setting lowered,
        creep removed) it relieves to the set point; the regulator does not let keg gas flow back into the manifold."""
        kg = c['keg']
        if not kg['gas_open']:
            return 0.0, kg['n_gas']
        RT = R_GAS * self._T()
        Vg = self._keg_gas_volume(c)
        p_set = self._pa() + min(kg['reg_keg'] + kg['creep'], DOC['tamu_keg_relief_pa'])   # beyond 60 psi the keg relief valve holds the head
        n_set = p_set * Vg / RT
        if kg['n_gas'] > n_set * (1.0 + 1e-12):                              # relieving regulator vents to its set point
            return 0.0, n_set
        if self._feed():
            n_want = min(p_set, self.p_im) * Vg / RT
            return max(n_want - kg['n_gas'], 0.0), max(n_want, kg['n_gas'])
        V_im = ENG['gas_manifold_volume_m3']
        n_im = self.p_im * V_im / RT
        d_eq = (n_im * Vg - kg['n_gas'] * V_im) / (V_im + Vg)              # equal pressures in manifold and keg
        d = min(max(n_set - kg['n_gas'], 0.0), max(d_eq, 0.0))
        return d, kg['n_gas'] + d

    def _feed(self):
        """Building gas flows into the manifold (isolation valve open and supply available)."""
        return self.isolation and self._supply_ok()

    def _ticks_to_keg_relief(self):
        """Whole ticks until a creeping push regulator first exceeds the 60 psi keg relief (None when it cannot), so that the relief event
        is stamped at the same tick for any step partition."""
        ch = self.fault_ch.get('regulator_creep')
        if 'regulator_creep' not in self.faults or ch is None or self.ch[ch - 1]['solvent'] is None:
            return None
        kg = self.ch[ch - 1]['keg']
        if kg.get('relief_warned') or not kg['gas_open'] or self.p_im - self._pa() <= DOC['tamu_keg_relief_pa']:
            return None
        rest = DOC['tamu_keg_relief_pa'] - kg['reg_keg'] - kg['creep']
        if rest < 0.0:
            return None
        return int(math.floor(rest / (ENG['regulator_creep_pa_s'] * DT)))

    def _p_supply(self):
        return self._pa() + min(self.plant['supply_gauge_pa'], DOC['main_relief_pa'])

    def _draw_manifold(self, n):
        """Gas (mol) taken from the isolated inert-gas manifold (isolation valve closed or supply exhausted): its pressure falls."""
        self.p_im = max(self.p_im - n * R_GAS * self._T() / ENG['gas_manifold_volume_m3'], 0.0)

    # ---- physics
    def step(self, dt_s):
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = round(self.t + float(dt_s), 9)
        if self.plant is None:
            self.t = t_end
            self.t_grid = math.floor(t_end / DT + 1e-9) * DT
            self.tick = int(round(self.t_grid / DT))
            return self.t
        while self.t_grid + DT <= t_end + 1e-9:
            if self._quiet():                                               # nothing changes but a creeping regulator: jump the grid exactly
                n = int(math.floor((t_end - self.t_grid) / DT + 1e-9))
                n_relief = self._ticks_to_keg_relief()
                limited = n_relief is not None and n_relief < n               # stop the jump just before the creeping regulator passes the relief
                if limited:
                    n = n_relief
                if n > 0 and 'regulator_creep' in self.faults:
                    self.t = round((self.tick + n) * DT, 9)
                    for k, c in enumerate(self.ch):
                        if c['solvent'] is not None:
                            self._keg_head(k, c, n)
                self.tick += n
                self.t_grid = round(self.tick * DT, 9)
                if not limited:
                    break
                if self.t_grid + DT > t_end + 1e-9:
                    break
            self.t = self.t_grid
            self._tick(DT)
            self.tick += 1
            self.t_grid = round(self.tick * DT, 9)
        self.t = t_end
        return self.t

    def _tick(self, h):
        L = self.led
        T, pa = self._T(), self._pa()
        RT = R_GAS * T
        # ---- inert-gas manifold: supply through the isolation valve, 70 psi relief
        if self._feed():
            if self.plant['supply_gauge_pa'] > DOC['main_relief_pa'] and not self._relief_warned:
                self._event('warning', 'MAIN_RELIEF', '供气超过70 psi：主泄压阀放气')
                self._relief_warned = True
            self.p_im = self._p_supply()
        # ---- vacuum manifold: pump and leak
        V_vm = ENG['manifold_volume_m3']
        n_vm = self.p_vm * V_vm / RT
        a_vm = self.vm_air * n_vm                                           # air-derived moles in the vacuum manifold (pumping keeps the fraction)
        if self.pump and 'pump_failure' not in self.faults:
            n_vm -= DOC['pump_speed_m3_s'] * max(self.p_vm - DOC['pump_ultimate_pa'], 0.0) / RT * h
            a_vm = self.vm_air * n_vm
        for k, c in enumerate(self.ch):
            sid = c['solvent']
            if sid is None:
                continue
            kg, col, f = c['keg'], c['col'], c['flask']
            lk = L['ch'][k]
            rho, mu = self._rho(sid), self._mu(sid)
            M = SOLV[sid]['molar_mass_kg_mol'] or 0.1
            # ---- keg head pressure: push regulator (with creep fault) or expansion of the headspace
            self._keg_head(k, c, 1)
            # ---- sparging (keg disconnected from the column, gas through the dip tube, vented): oxygen out, vapour carried away
            if self.t < kg['sparge_until'] and self.isolation and self._supply_ok():
                f_o2 = 1.0 - math.exp(-h / ENG['sparge_tau_s'])
                lk['sparge_o2_kg'] += kg['o2'] * f_o2
                kg['o2'] -= kg['o2'] * f_o2
                n_gas = ENG['sparge_flow_m3_s'] * pa / RT * h
                pv = p_vap(sid, T)
                y = min(pv / pa, ENG['sparge_vapour_fraction_max'])           # vented gas leaves saturated; near the boiling point the bubbles are capped
                if pv > ENG['sparge_vapour_fraction_max'] * pa and not kg.get('boil_warned'):
                    kg['boil_warned'] = True
                    self._event('warning', 'SPARGE_NEAR_BOILING', f'第{k + 1}储罐鼓泡时溶剂接近沸点（蒸气压超过环境压力的一半）：大量溶剂随气带走')
                m_vap = min(n_gas * y / (1.0 - y) * M, rho * kg['v'])
                kg['p_head'] = pa
                kg['n_gas'] = pa * self._keg_gas_volume(c) / RT
                if m_vap > 0.0 and kg['v'] > 0.0:
                    fw = kg['water'] / (rho * kg['v'])
                    kg['v'] -= m_vap / rho
                    lk['sparge_solvent_kg'] += m_vap
                    w_out = m_vap * fw * 0.0                                    # dry gas strips little water (not modelled)
                    kg['water'] -= w_out
            # ---- dispensing: keg -> columns -> filter -> 5-way (dispense) -> needle -> line -> flask
            if c['valve'] == 'dispense' and kg['outlet_open'] and col['inlet'] and col['outlet'] and c['needle'] > 0.0:
                if kg['v'] <= ENG['dip_tube_residual_m3'] + 1e-12:
                    if not kg.get('empty_warned'):
                        kg['empty_warned'] = True
                        self._event('warning', 'KEG_EMPTY', f'第{k + 1}储罐已空：惰性气进入柱（不要把柱抽干）')
                else:
                    p_f = self._flask_p(c)                                       # no flask: the open joint is at room pressure
                    dP = kg['p_head'] - p_f - rho * G_ACC * ENG['head_height_m']
                    R_f = ENG['filter_resistance_m3'] * (ENG['filter_clog_factor'] if ('filter_clogged' in self.faults and self.fault_ch.get('filter_clogged') == k + 1) else 1.0)
                    Rt = 2.0 * ENG['column_resistance_m3'] + R_f + ENG['needle_resistance_m3'] / c['needle']
                    Q = max(dP, 0.0) / (mu * Rt)
                    room = 0.5 * max(f['vol'] - f['liq'], 0.0) if f is not None else math.inf
                    dV = min(Q * h, kg['v'] - ENG['dip_tube_residual_m3'], room + max(ENG['line_holdup_m3'] - c['line']['v'], 0.0))   # a closed flask fills at most to its brim
                    if dV > 0.0:
                        m = rho * dV
                        w_in = kg['water'] / (rho * kg['v'])
                        o_in = kg['o2'] / (rho * kg['v'])
                        s_in = kg['stab'] / (rho * kg['v'])
                        kg['water'] -= m * w_in
                        kg['o2'] -= m * o_in
                        kg['stab'] -= m * s_in
                        kg['v'] -= dV
                        col['stab_load'] += m * s_in                                  # stabiliser adsorbs on the first column
                        fl = ENG['water_floor_ppm'] * 1e-6
                        cap1 = max(col['cap'][0] - ENG['stabiliser_capacity_kg_per_kg'] * col['stab_load'], 1e-9)
                        w1 = fl + max(w_in - fl, 0.0) * breakthrough(col['load'][0] / cap1) if w_in > fl else w_in
                        col['load'][0] += m * (w_in - w1)
                        if col['cap'][1] > 0.0:
                            w2 = fl + max(w1 - fl, 0.0) * breakthrough(col['load'][1] / col['cap'][1]) if w1 > fl else w1
                            col['load'][1] += m * (w1 - w2)
                        else:
                            w2 = w1
                        if col['o2_cap'] > 0.0:
                            ofl = ENG['o2_floor_ppm'] * 1e-6
                            o2o = ofl + max(o_in - ofl, 0.0) * breakthrough(col['o2_load'] / col['o2_cap']) if o_in > ofl else o_in
                            col['o2_load'] += m * (o_in - o2o)
                        else:
                            o2o = o_in
                        if w2 > 2.0 * fl + 1e-6 and not col['warned']:
                            col['warned'] = True
                            self._event('truth', 'COLUMN_BREAKTHROUGH', f'第{k + 1}路柱出口含水开始上升（突破）')
                        ln = c['line']
                        to_line = min(dV, ENG['line_holdup_m3'] - ln['v'])
                        fr = to_line / dV
                        ln['v'] += to_line
                        ln['water'] += m * w2 * fr
                        ln['o2'] += m * o2o * fr
                        lk['dispensed_kg'] += m
                        if f is None:                                         # nothing on the joint: dry solvent runs onto the drip tray
                            lk['drip_solvent_kg'] += rho * (dV - to_line)
                            lk['drip_water_kg'] += m * w2 * (1.0 - fr)
                            lk['drip_o2_kg'] += m * o2o * (1.0 - fr)
                            if dV > to_line and not c.get('spill_warned'):
                                c['spill_warned'] = True
                                self._event('warning', 'SOLVENT_SPILL', f'第{k + 1}路没有接收瓶时转到分配：溶剂从接口流到接液盘')
                        else:
                            f['liq'] += dV - to_line
                            f['water'] += m * w2 * (1.0 - fr)
                            f['o2'] += m * o2o * (1.0 - fr)
                        if f is not None and f['liq'] > DOC['ucd_flask_fill_max'] * f['vol'] and not f['over_warned']:
                            f['over_warned'] = True
                            self._event('warning', 'FLASK_OVER_TWO_THIRDS', f'第{k + 1}路接收瓶超过2/3满（UCD：不要超过2/3）')
                    if f is not None and self._flask_p(c) - pa > DOC['flask_refill']['relief_pa'] and not f['press_warned']:
                        f['press_warned'] = True
                        self._event('warning', 'FLASK_PRESSURISED', f'第{k + 1}路分配时接收瓶表压超过7 psi（瓶已满或未先抽空：密闭瓶内气体被压缩）')
            if f is None:                                                    # open joint: room air into the vacuum, inert gas out of the refill line
                if c['valve'] == 'evacuate':
                    n_in = min(ENG['valve_conductance_m3_s'] * max(pa - self.p_vm, 0.0) / RT * h, 0.5 * max(pa - self.p_vm, 0.0) * V_vm / RT)
                    n_vm += n_in
                    a_vm += n_in
                elif c['valve'] == 'refill' and not self._feed():
                    self._draw_manifold(ENG['refill_conductance_m3_s'] * max(min(pa + kg['reg_flask'], self.p_im) - pa, 0.0) / RT * h)
                continue
            # ---- flask gas: evacuate / refill / leaks / relief
            Vg = self._flask_gas_volume(f)
            n_g = f['n_inert'] + f['n_air'] + f['n_o2'] + f['n_h2o']
            p_gas = n_g * RT / Vg
            pv = p_vap(sid, T) if f['liq'] > 1e-12 else 0.0
            if c['valve'] == 'evacuate':
                if 'check_valve_leak' in self.faults and self.fault_ch.get('check_valve_leak') == k + 1 and self.p_im > p_gas + pv:
                    p_t = min(pa + kg['reg_flask'], self.p_im)              # leaking check valve: refill gas bleeds into the evacuated flask
                    dn = ENG['check_valve_leak_fraction'] * ENG['refill_conductance_m3_s'] * max(p_t - p_gas - pv, 0.0) / RT * h
                    f['n_inert'] += dn
                    lk['flask_refill_mol'] += dn
                    if not self._feed():
                        self._draw_manifold(dn)
                    n_g += dn
                    p_gas = n_g * RT / Vg
                C = ENG['valve_conductance_m3_s']
                p_tot = p_gas + pv
                if self.p_vm > p_tot:                                        # the manifold is at the higher pressure: its gas flows back into the flask
                    back = min(C * (self.p_vm - p_tot) / RT * h, 0.5 * (self.p_vm - p_tot) * min(Vg, V_vm) / RT)
                    air = back * self.vm_air
                    f['n_inert'] += back - air
                    f['n_air'] += air * 0.7905
                    f['n_o2'] += air * 0.2095
                    lk['flask_evac_mol'] -= back
                    n_vm -= back
                    a_vm -= air
                    if air > 0.0 and not f.get('back_warned'):
                        f['back_warned'] = True
                        self._event('warning', 'VACUUM_BACKFLOW', f'第{k + 1}路抽空时真空歧管压力高于瓶内：歧管中的气体（含空气）倒灌进接收瓶')
                    n_g = f['n_inert'] + f['n_air'] + f['n_o2'] + f['n_h2o']
                    p_gas = n_g * RT / Vg
                    p_tot = p_gas + pv
                dn = max(C * (p_tot - self.p_vm) / RT * h, 0.0)
                dn = min(dn, 0.5 * (n_g + pv * Vg / RT))
                share_g = p_gas / max(p_tot, 1e-12)
                out_g = dn * share_g
                if n_g > 0.0:
                    for key in ('n_inert', 'n_air', 'n_o2', 'n_h2o'):
                        frac = f[key] / n_g
                        f[key] -= out_g * frac
                        lk['flask_evac_mol'] += out_g * frac
                        if key == 'n_h2o':
                            lk['water_to_pump_kg'] += out_g * frac * M_W
                        if key != 'n_inert':
                            a_vm += out_g * frac
                n_vm += out_g
                if pv > 0.0:                                                # vapour: liquid evaporates into the pump
                    n_v = dn * (1.0 - share_g)
                    area = ENG['flask_area_m2_per_l'] * f['vol'] * 1000.0
                    n_v = min(n_v, ENG['evaporation_hm_m_s'] * area * pv / RT * h)
                    m_v = min(n_v * M, rho * f['liq'])
                    fw = f['water'] / max(rho * f['liq'], 1e-12)
                    fo = f['o2'] / max(rho * f['liq'], 1e-12)
                    f['liq'] -= m_v / rho
                    f['water'] -= m_v * fw
                    f['o2'] -= m_v * fo
                    lk['solvent_to_pump_kg'] += m_v
                    lk['water_to_pump_kg'] += m_v * fw
                    lk['o2_to_pump_kg'] += m_v * fo
                    n_vm += m_v / M
                    if not f['evac_liq_warned']:
                        f['evac_liq_warned'] = True
                        self._event('warning', 'EVACUATE_WITH_SOLVENT', f'第{k + 1}路接收瓶有溶剂时抽空：溶剂蒸发进入真空泵')
            elif c['valve'] == 'refill':
                p_t = min(pa + kg['reg_flask'], self.p_im)
                if p_t > p_gas + pv:
                    dn = ENG['refill_conductance_m3_s'] * (p_t - p_gas - pv) / RT * h
                    dn = min(dn, (p_t - p_gas - pv) * Vg / RT)
                    f['n_inert'] += dn
                    lk['flask_refill_mol'] += dn
                    if not self._feed():
                        self._draw_manifold(dn)
                if f['liq'] > 1e-9:
                    if c['refill_since'] is None:
                        c['refill_since'] = self.t
                    elif self.t - c['refill_since'] > 60.0 and not c['flood_warned']:
                        c['flood_warned'] = True
                        self._event('warning', 'SOLVENT_IN_GAS_MANIFOLD', f'第{k + 1}路回填位停留时瓶中有溶剂：溶剂可能进入气体歧管损坏表（UCD）')
            if c['valve'] != 'refill':
                c['refill_since'] = None
            if 'flask_joint_leak' in self.faults and self.fault_ch.get('flask_joint_leak') == k + 1:
                dn = ENG['flask_leak_m3_s_pa'] * max(pa - p_gas - pv, 0.0) * pa / RT * h
                self._add_air(f, dn, lk)
            p_gas = (f['n_inert'] + f['n_air'] + f['n_o2'] + f['n_h2o']) * RT / self._flask_gas_volume(f)
            if c['valve'] == 'refill' and p_gas + pv - pa > DOC['flask_refill']['relief_pa']:   # the relief valve sits on the refill line
                n_g = f['n_inert'] + f['n_air'] + f['n_o2'] + f['n_h2o']
                excess = (p_gas + pv - pa - DOC['flask_refill']['relief_pa']) * self._flask_gas_volume(f) / RT
                excess = min(excess, n_g)
                for key in ('n_inert', 'n_air', 'n_o2', 'n_h2o'):
                    frac = f[key] / max(n_g, 1e-30)
                    f[key] -= excess * frac
                    lk['flask_relief_mol'] += excess * frac
                    if key == 'n_h2o':
                        lk['water_vented_kg'] += excess * frac * M_W
            # ---- contact of the dry solvent with the flask gas and walls: water dissolves, oxygen equilibrates
            if f['liq'] > 1e-12:
                if f['n_h2o'] > 0.0:
                    f['water'] += f['n_h2o'] * M_W
                    lk['gas_dissolved_mol'] += f['n_h2o']
                    f['n_h2o'] = 0.0
                if f['wall_water'] > 0.0:
                    f['water'] += f['wall_water']
                    f['wall_water'] = 0.0
                m_l = rho * f['liq']
                Hk = ENG['o2_air_saturated_ppm'] * 1e-6 / (0.2095 * 101325.0)
                Vg = self._flask_gas_volume(f)
                tot = f['o2'] + f['n_o2'] * 0.0319988
                p_o2 = tot / (Hk * m_l + Vg * 0.0319988 / RT)
                f['o2'] = Hk * p_o2 * m_l
                lk['gas_dissolved_mol'] += f['n_o2'] - p_o2 * Vg / RT
                f['n_o2'] = p_o2 * Vg / RT
        self.p_vm = max(n_vm * RT / V_vm, 1e-3)
        self.vm_air = min(max(a_vm / n_vm, 0.0), 1.0) if n_vm > 0.0 else self.vm_air
        if self.pump and 'pump_failure' not in self.faults and self.p_vm - DOC['pump_ultimate_pa'] < 1e-6 * DOC['pump_ultimate_pa']:
            self.p_vm = max(self.p_vm, DOC['pump_ultimate_pa'])                # at the ultimate pressure

    def _add_air(self, f, n, lk):
        """Room air entering a flask (n mol): water vapour, oxygen and the rest."""
        x_w = self.plant['relative_humidity'] * p_sat_water(self._T()) / self._pa()
        f['n_h2o'] += n * x_w
        f['n_o2'] += n * (1.0 - x_w) * 0.2095
        f['n_air'] += n * (1.0 - x_w) * 0.7905
        lk['air_in_mol'] += n
        lk['water_from_air_kg'] += n * x_w * M_W

    _relief_warned = False

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read'):
            self.revision += 1
        return out

    def _chan_ledger(self):
        return dict(initial_solvent_kg=0.0, added_solvent_kg=0.0, initial_water_kg=0.0, added_water_kg=0.0, dispensed_kg=0.0, removed_solvent_kg=0.0,
                    removed_water_kg=0.0, solvent_to_pump_kg=0.0, water_to_pump_kg=0.0, o2_to_pump_kg=0.0, sparge_solvent_kg=0.0, sparge_o2_kg=0.0,
                    drip_solvent_kg=0.0, drip_water_kg=0.0, drip_o2_kg=0.0, column_water_removed_kg=0.0, water_vented_kg=0.0, water_from_air_kg=0.0, wall_water_kg=0.0, air_in_mol=0.0, flask_refill_mol=0.0,
                    flask_evac_mol=0.0, flask_relief_mol=0.0, flask_removed_mol=0.0, gas_dissolved_mol=0.0)

    def _cmd_plant_configure(self, ambient_k, relative_humidity, pressure_pa, gas, supply_gauge_pa, solvents, noise_seed, initial_fill_m3=0.012, feed_water_ppm=None):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        if self.plant is not None and list(solvents) != [c['solvent'] for c in self.ch]:
            raise ActionRejected('SOLVENTS_FIXED')
        first = self.plant is None
        w_ppm = ENG['hplc_inlet_water_ppm'] if feed_water_ppm is None else feed_water_ppm
        self.plant = dict(ambient_k=ambient_k, relative_humidity=relative_humidity, pressure_pa=pressure_pa, gas=gas, supply_gauge_pa=supply_gauge_pa)
        self.seed = noise_seed
        if first:
            self.led = dict(ch=[self._chan_ledger() for _ in range(N_CH)])
            self.p_vm = self.p_im = pressure_pa
            self.vm_air = 1.0
            self.ch = [_new_channel(sid) for sid in solvents]
            for k, c in enumerate(self.ch):
                if c['solvent'] is None:
                    continue
                rho = self._rho(c['solvent'])
                kg = c['keg']
                kg['v'] = initial_fill_m3
                kg['water'] = rho * initial_fill_m3 * w_ppm * 1e-6
                kg['o2'] = rho * initial_fill_m3 * ENG['o2_air_saturated_ppm'] * 1e-6 * 0.05      # filled and sparged before (5 % of air saturation)
                kg['p_head'] = pressure_pa
                kg['n_gas'] = pressure_pa * self._keg_gas_volume(c) / (R_GAS * ambient_k)
                lk = self.led['ch'][k]
                lk['initial_solvent_kg'] = rho * initial_fill_m3
                lk['initial_water_kg'] = kg['water']
        self._event('system', 'PLANT_CONFIGURED', '环境与溶剂配置已声明')
        return dict(configured=True)

    def _cmd_gas_isolation(self, open):
        self._need_plant()
        if open != self.isolation:
            self.isolation = open
            self._event('operation', 'GAS_OPEN' if open else 'GAS_CLOSED', '主隔离阀' + ('开' if open else '关'))
            if open and self._supply_ok():
                self.p_im = min(self._pa() + self.plant['supply_gauge_pa'], self._pa() + DOC['main_relief_pa'])
        return dict(open=self.isolation)

    def _cmd_regulator_set(self, channel, keg_gauge_pa=None, flask_gauge_pa=None):
        self._need_plant()
        c = self._ch(channel)
        if keg_gauge_pa is not None:
            c['keg']['reg_keg'] = float(keg_gauge_pa)
            c['keg']['creep'], c['keg']['creep_ticks'] = 0.0, 0
        if flask_gauge_pa is not None:
            c['keg']['reg_flask'] = float(flask_gauge_pa)
        return dict(channel=channel, keg_gauge_pa=c['keg']['reg_keg'], flask_gauge_pa=c['keg']['reg_flask'])

    def _cmd_pump_set(self, on):
        self._need_plant()
        if on != self.pump:
            self.pump = on
            self._event('operation', 'PUMP_ON' if on else 'PUMP_OFF', '真空泵' + ('开' if on else '关'))
        return dict(on=self.pump)

    def _cmd_pump_toggle(self):
        return self._cmd_pump_set(on=not self.pump)

    def _path(self, start, target, direction):
        cw = ['closed', 'refill', 'dispense', 'evacuate']                    # clockwise seen from the front: top, right, bottom, left
        i, j = cw.index(start), cw.index(target)
        d_cw, d_ccw = (j - i) % 4, (i - j) % 4
        if direction == 'shortest':
            direction = 'clockwise' if d_cw <= d_ccw else 'counterclockwise'
        n, step = (d_cw, 1) if direction == 'clockwise' else (d_ccw, -1)
        return [cw[(i + step * m) % 4] for m in range(1, n)]

    def _drain_line(self, c, k, into):
        ln, lk = c['line'], self.led['ch'][k]
        if ln['v'] <= 0.0:
            return
        rho = self._rho(c['solvent'])
        if into == 'flask' and c['flask'] is not None:
            f = c['flask']
            fr = min(ln['v'], max(0.99 * f['vol'] - f['liq'], 0.0)) / ln['v']   # a full flask takes no more: the rest stays in the line
            f['liq'] += fr * ln['v']
            f['water'] += fr * ln['water']
            f['o2'] += fr * ln['o2']
            if fr < 1.0:
                ln.update(v=ln['v'] * (1.0 - fr), water=ln['water'] * (1.0 - fr), o2=ln['o2'] * (1.0 - fr))
                self._event('warning', 'FLASK_FULL', f'第{k + 1}路接收瓶已满：分配管存液只吹入一部分')
                return
        elif into == 'pump':
            lk['solvent_to_pump_kg'] += rho * ln['v']
            lk['water_to_pump_kg'] += ln['water']
            lk['o2_to_pump_kg'] += ln['o2']
            self._event('warning', 'SOLVENT_TO_VACUUM', f'第{k + 1}路分配管中的溶剂经抽空位吸进真空系统（UCD：不要经“Evacuate”转回关闭；开泵约1 h清除）')
        else:
            lk['drip_solvent_kg'] += rho * ln['v']
            lk['drip_water_kg'] += ln['water']
            lk['drip_o2_kg'] += ln['o2']
            self._event('warning', 'SOLVENT_DRIP', f'第{k + 1}路分配管中的溶剂滴到接液盘')
        ln.update(v=0.0, water=0.0, o2=0.0)

    def _cmd_valve_turn(self, channel, position, direction='shortest'):
        self._need_plant()
        c = self._ch(channel)
        k = channel - 1
        passed = self._path(c['valve'], position, direction) if position != c['valve'] else []
        for pos in passed + [position]:
            if pos == 'evacuate' and c['line']['v'] > 0.0 and self.pump:
                self._drain_line(c, k, 'pump')
            if pos == 'refill' and c['line']['v'] > 0.0 and c['flask'] is not None and self.p_im > self._pa():
                self._drain_line(c, k, 'flask')                             # back fill clears the dispensing line into the flask (UCD)
        c['valve'] = position
        c['angle'] = ANGLE[position]
        return dict(channel=channel, position=position, passed=passed)

    def _cmd_needle_set(self, channel, opening):
        self._need_plant()
        c = self._ch(channel)
        c['needle'] = float(opening)
        return dict(channel=channel, opening=c['needle'])

    def _cmd_line_set(self, channel, keg_gas=None, keg_outlet=None, column_inlet=None, column_outlet=None):
        self._need_plant()
        c = self._ch(channel)
        for key, v in (('gas_open', keg_gas), ('outlet_open', keg_outlet)):
            if v is not None:
                c['keg'][key] = v
        for key, v in (('inlet', column_inlet), ('outlet', column_outlet)):
            if v is not None:
                c['col'][key] = v
        return dict(channel=channel, valves=self._valves(c))

    def _valves(self, c):
        return dict(keg_gas=c['keg']['gas_open'], keg_outlet=c['keg']['outlet_open'], column_inlet=c['col']['inlet'], column_outlet=c['col']['outlet'])

    def _cmd_flask_attach(self, channel, volume_m3, oven_dried=True):
        self._need_plant()
        c = self._ch(channel)
        if c['flask'] is not None:
            raise ActionRejected('FLASK_ATTACHED')
        k = channel - 1
        f = dict(vol=float(volume_m3), liq=0.0, water=0.0, o2=0.0, n_inert=0.0, n_air=0.0, n_o2=0.0, n_h2o=0.0,
                 wall_water=0.0 if oven_dried else ENG['wall_water_kg'] * volume_m3 / 5e-4, over_warned=False, press_warned=False, evac_liq_warned=False, attached_at=self.t)
        self._add_air(f, self._pa() * f['vol'] / (R_GAS * self._T()), self.led['ch'][k])
        self.led['ch'][k]['wall_water_kg'] += f['wall_water']
        c['flask'] = f
        self._event('operation', 'FLASK_ATTACHED', f'第{channel}路装上接收瓶（{volume_m3 * 1e3:.2f} L，{"已烘干" if oven_dried else "未烘干"}）')
        return dict(channel=channel, attached=True)

    def _cmd_flask_detach(self, channel):
        self._need_plant()
        c = self._ch(channel)
        f = c['flask']
        if f is None:
            raise ActionRejected('NO_FLASK')
        if c['valve'] != 'closed':
            raise ActionRejected('VALVE_NOT_CLOSED')
        k = channel - 1
        lk = self.led['ch'][k]
        rho = self._rho(c['solvent'])
        m = rho * f['liq']
        p = self._flask_p(c)
        full = dict(channel=channel, solvent=c['solvent'], volume_m3=f['liq'], mass_kg=m, water_ppm=(f['water'] / m * 1e6) if m > 0 else None,
                    oxygen_ppm=(f['o2'] / m * 1e6) if m > 0 else None, gas_gauge_pa=p - self._pa(), under_inert=(f['n_air'] + f['n_o2']) / max(
                        f['n_inert'] + f['n_air'] + f['n_o2'] + f['n_h2o'], 1e-30) < 0.01, flask_volume_m3=f['vol'], removed_at_s=self.t)
        self.removed.append(full)                                            # water, oxygen and gas composition: evaluator truth only (not measured here)
        out = {key: full[key] for key in ('channel', 'solvent', 'volume_m3', 'mass_kg', 'gas_gauge_pa', 'flask_volume_m3')}
        lk['removed_solvent_kg'] += m
        lk['removed_water_kg'] += f['water'] + f['n_h2o'] * M_W + f['wall_water']
        lk['flask_removed_mol'] += f['n_inert'] + f['n_air'] + f['n_o2'] + f['n_h2o']
        c['keg']['out'] += f['liq']
        c['flask'] = None
        if c['line']['v'] > 0.0:
            self._drain_line(c, k, 'drip')
        self._event('operation', 'FLASK_DETACHED', f'第{channel}路取下接收瓶：{f["liq"] * 1e3:.1f} mL')
        return dict(flask=out)

    def _doors_open(self):
        return max(self.doors.values()) >= 1.0

    def _cmd_keg_refill(self, channel, volume_m3, water_ppm=None, stabiliser_ppm=0.0, grounded=True):
        self._need_plant()
        c = self._ch(channel)
        if not self._doors_open():
            raise ActionRejected('CABINET_CLOSED')
        if c['keg']['outlet_open'] or c['keg']['gas_open']:
            raise ActionRejected('KEG_CONNECTED')
        kg, lk = c['keg'], self.led['ch'][channel - 1]
        if kg['v'] + volume_m3 > DOC['reservoir_volume_m3'] + 1e-12:
            raise ActionRejected('KEG_OVERFILL')
        rho = self._rho(c['solvent'])
        w = (ENG['hplc_inlet_water_ppm'] if water_ppm is None else water_ppm) * 1e-6
        m = rho * volume_m3
        kg['v'] += volume_m3
        kg['water'] += m * w
        kg['stab'] += m * stabiliser_ppm * 1e-6
        kg['o2'] = rho * kg['v'] * ENG['o2_air_saturated_ppm'] * 1e-6           # opened to air: the whole keg is air saturated again
        kg['p_head'] = self._pa()
        kg['n_gas'] = kg['p_head'] * self._keg_gas_volume(c) / (R_GAS * self._T())
        kg['added'] += volume_m3
        kg['low_warned'] = kg['empty_warned'] = False
        lk['added_solvent_kg'] += m
        lk['added_water_kg'] += m * w
        if not grounded:
            self._event('warning', 'NOT_GROUNDED', f'第{channel}储罐加注时未夹接地线（静电风险；厂家：伸缩接地线用于加注）')
        if stabiliser_ppm > 0.0:
            self._event('warning', 'STABILISER_IN_FEED', f'第{channel}储罐加注的溶剂含稳定剂（UCD：不得含阻聚剂/稳定剂；占用柱容量）')
        self._event('maintenance', 'KEG_REFILLED', f'第{channel}储罐加注{volume_m3 * 1e3:.2f} L（须鼓泡除氧后再接回）')
        return dict(channel=channel, volume_m3=volume_m3, keg_m3=kg['v'])

    def _cmd_keg_sparge(self, channel, duration_s):
        self._need_plant()
        c = self._ch(channel)
        if c['keg']['outlet_open']:
            raise ActionRejected('KEG_CONNECTED')
        if not (self.isolation and self._supply_ok()):
            raise ActionRejected('NO_GAS')
        c['keg']['sparge_until'] = self.t + duration_s
        self._event('maintenance', 'SPARGE', f'第{channel}储罐鼓泡除氧{duration_s / 60:.0f} min')
        return dict(channel=channel, until_s=c['keg']['sparge_until'])

    def _cmd_column_replace(self, channel):
        self._need_plant()
        c = self._ch(channel)
        if c['col']['inlet'] or c['col']['outlet']:
            raise ActionRejected('COLUMN_OPEN')
        fresh = _new_channel(c['solvent'])['col']
        fresh.update(inlet=False, outlet=False)
        k = channel - 1
        self.led['ch'][k]['column_water_removed_kg'] += sum(c['col']['load'])
        c['col'] = fresh
        self._event('maintenance', 'COLUMN_REPLACED', f'第{channel}路更换新柱')
        return dict(channel=channel, replaced=True)

    def _cmd_cabinet_door(self, side, angle_rad):
        self._need_plant()
        self.doors[side] = float(angle_rad)
        return dict(side=side, angle_rad=self.doors[side])

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        self._need_plant()
        r = self.read('evaluator')
        return dict(channels=r['truth.channels']['channels'], manifold=r['truth.manifold'], faults=r['truth.faults'], removed_flasks=copy.deepcopy(self.removed))

    def _ledger(self):
        if self.led is None:
            return {}
        out = dict(channels=[])
        for k, c in enumerate(self.ch):
            lk = dict(self.led['ch'][k])
            if c['solvent'] is not None:
                rho = self._rho(c['solvent'])
                f = c['flask']
                lk['solvent_in_system_kg'] = rho * (c['keg']['v'] + c['line']['v'] + (f['liq'] if f else 0.0))
                lk['water_in_system_kg'] = (c['keg']['water'] + sum(c['col']['load']) + c['line']['water'] +
                                            ((f['water'] + f['n_h2o'] * M_W + f['wall_water']) if f else 0.0))
                lk['flask_gas_mol'] = (f['n_inert'] + f['n_air'] + f['n_o2'] + f['n_h2o']) if f else 0.0
            out['channels'].append(lk)
        out['sign_convention_zh'] = ('每路：溶剂质量 solvent_in_system = initial + added − removed − to_pump − sparge − drip；水 water_in_system = initial + added + from_air + wall '
                                     '− removed − to_pump − vented − drip − column_water_removed；接收瓶气体（不含溶剂蒸气）flask_gas = air_in + refill − evac − relief − removed − dissolved（水汽与氧溶入溶剂）')
        return out

    def _cmd_ledger_read(self):
        return dict(ledger=self._ledger())

    def _cmd_fault_inject(self, fault, active, channel=None):
        self._need_plant()
        if fault in ('filter_clogged', 'flask_joint_leak', 'regulator_creep', 'check_valve_leak'):
            if channel is None:
                raise ActionRejected('CHANNEL_REQUIRED')
            (self.fault_ch.__setitem__(fault, channel) if active else self.fault_ch.pop(fault, None))
        (self.faults.add if active else self.faults.discard)(fault)
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}' + (f'（第{channel}路）' if channel else '') + ('：注入' if active else '：清除'))
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- read
    def _gauges(self):
        q = ENG['valve_gauge_quantum']
        pa = self._pa() if self.plant else 101325.0
        vac = 0.0 if self.plant is None else max(min(self.p_vm - pa, 0.0), -30.0 * INHG)
        prs = 0.0 if self.plant is None else max(min(self.p_im - pa, 100.0 * PSI), 0.0)
        vac = round(vac / q['vacuum_pa']) * q['vacuum_pa']
        prs = round(prs / q['pressure_pa']) * q['pressure_pa']
        st = []
        for c in self.ch:
            v = 0.0
            if self.plant is not None and c['solvent'] is not None and c['flask'] is not None:
                v = max(min(self._flask_p(c) - pa, 15.0 * PSI), 0.0)
            st.append(round(v / q['station_pa']) * q['station_pa'])
        return vac, prs, st

    def read(self, visibility='agent'):
        pc = self.plant is not None
        vac, prs, st = self._gauges()
        out = {'clock.time_s': self.t, 'plant.configured': pc, 'channels.solvents': [c['solvent'] for c in self.ch], 'pump.on': self.pump,
               'gas.isolation_open': self.isolation, 'gas.isolation_angle_rad': math.pi / 2 if self.isolation else 0.0,
               'gauge.vacuum_pa': vac, 'gauge.pressure_pa': prs, 'gauge.vacuum_angle_rad': -GAUGE_SWEEP * (-vac) / (30.0 * INHG),
               'gauge.pressure_angle_rad': -GAUGE_SWEEP * prs / (100.0 * PSI), 'cabinet.left_angle_rad': self.doors['left'], 'cabinet.right_angle_rad': self.doors['right'],
               'regulators.settings': {str(k + 1): dict(keg_gauge_pa=c['keg']['reg_keg'], flask_gauge_pa=c['keg']['reg_flask']) for k, c in enumerate(self.ch) if c['solvent']},
               'lines.valves': {str(k + 1): self._valves(c) for k, c in enumerate(self.ch) if c['solvent']},
               'reservoirs.recorded': {str(k + 1): dict(added_m3=c['keg']['added'], dispensed_into_removed_flasks_m3=c['keg']['out']) for k, c in enumerate(self.ch) if c['solvent']}}
        for k, c in enumerate(self.ch):
            n = k + 1
            out[f'valve.ch{n}_position'] = c['valve']
            out[f'valve.ch{n}_angle_rad'] = c['angle']
            out[f'needle.ch{n}_opening'] = c['needle']
            out[f'needle.ch{n}_angle_rad'] = c['needle'] * NEEDLE_TURNS * 2 * math.pi
            out[f'gauge.station{n}_pa'] = st[k]
            out[f'gauge.station{n}_angle_rad'] = -GAUGE_SWEEP * st[k] / (15.0 * PSI)
            f = c['flask']
            out[f'flask.ch{n}'] = dict(attached=f is not None, volume_m3=f['vol'] if f else None, liquid_m3=f['liq'] if f else None)
        if visibility != 'agent':
            chans = []
            for k, c in enumerate(self.ch):
                if c['solvent'] is None or not pc:
                    chans.append(dict(solvent=None))
                    continue
                rho = self._rho(c['solvent'])
                kg, col, f = c['keg'], c['col'], c['flask']
                m_k = rho * kg['v']
                chans.append(dict(solvent=c['solvent'], packing=c['packing'], keg_m3=kg['v'], keg_water_ppm=kg['water'] / m_k * 1e6 if m_k > 0 else None,
                                  keg_o2_ppm=kg['o2'] / m_k * 1e6 if m_k > 0 else None, keg_head_pa=kg['p_head'], sparging=self.t < kg['sparge_until'],
                                  column_water_load_kg=list(col['load']), column_water_capacity_kg=list(col['cap']), column_load_fraction=[
                                      (col['load'][i] / col['cap'][i]) if col['cap'][i] > 0 else None for i in range(2)], q5_o2_load_kg=col['o2_load'],
                                  stabiliser_on_column_kg=col['stab_load'], line_m3=c['line']['v'],
                                  flask=None if f is None else dict(volume_m3=f['vol'], liquid_m3=f['liq'], water_ppm=(f['water'] / (rho * f['liq']) * 1e6) if f['liq'] > 0 else None,
                                                                    o2_ppm=(f['o2'] / (rho * f['liq']) * 1e6) if f['liq'] > 0 else None, pressure_pa=self._flask_p(c),
                                                                    gas_mol=dict(inert=f['n_inert'], air=f['n_air'], o2=f['n_o2'], h2o=f['n_h2o']), wall_water_kg=f['wall_water'])))
            out['truth.channels'] = dict(channels=chans)
            out['truth.manifold'] = dict(vacuum_pa=self.p_vm, inert_pa=self.p_im, supply_ok=self._supply_ok()) if pc else {}
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
class InertPuresolvmdSession:
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
        meta = copy.deepcopy(export_inert_puresolvmd_contract_metadata())
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
    'UNKNOWN_OPERATION': '会话不支持该操作（本运行层的能力都是同步的，没有可查询或取消的异步操作）',
    'PLANT_REQUIRED': '尚未声明环境与溶剂配置', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'SOLVENTS_FIXED': '各路溶剂只能在首次声明时设定（换溶剂须换柱与储罐）',
    'EMPTY_CHANNEL': '该路没有配置溶剂', 'FLASK_ATTACHED': '该路已装接收瓶', 'NO_FLASK': '该路没有接收瓶', 'VALVE_NOT_CLOSED': '五通阀不在关闭位：先转到关闭再取瓶',
    'CABINET_CLOSED': '柜门未打开（须开柜门取放储罐）', 'KEG_CONNECTED': '储罐出液或进气阀未关（先断开再加注或鼓泡）', 'KEG_OVERFILL': '超过17 L储罐容积',
    'NO_GAS': '没有惰性气（主隔离阀关或供气耗尽）', 'COLUMN_OPEN': '柱进出口阀未关（先关再换柱）', 'CHANNEL_REQUIRED': '该故障须指定通道',
}
_P = ['PLANT_REQUIRED']
_PC = _P + ['EMPTY_CHANNEL']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED', 'SOLVENTS_FIXED'], 'gas.isolation': _P, 'regulator.set': _PC, 'pump.set': _P, 'pump.toggle': _P, 'valve.turn': _PC,
    'needle.set': _PC, 'line.set': _PC, 'flask.attach': _PC + ['FLASK_ATTACHED'], 'flask.detach': _PC + ['NO_FLASK', 'VALVE_NOT_CLOSED'],
    'keg.refill': _PC + ['CABINET_CLOSED', 'KEG_CONNECTED', 'KEG_OVERFILL'], 'keg.sparge': _PC + ['KEG_CONNECTED', 'NO_GAS'], 'column.replace': _PC + ['COLUMN_OPEN'],
    'cabinet.door': _P, 'events.read': [], 'truth.inspect': _P, 'ledger.read': [], 'fault.inject': _P + ['CHANNEL_REQUIRED'], 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子与7路溶剂（之后不可改）；首次声明时储罐按initial_fill装好并已鼓泡、主隔离阀关、泵关', ['plant.configured']),
    'gas.isolation': ('无', ['gas.isolation_open']), 'regulator.set': ('该路配置了溶剂', ['regulators.settings']), 'pump.set': ('无', ['pump.on']),
    'pump.toggle': ('无', ['pump.on']), 'valve.turn': ('该路配置了溶剂；转动经过的位置也会短暂接通', ['channels.solvents']),
    'needle.set': ('该路配置了溶剂', ['channels.solvents']), 'line.set': ('该路配置了溶剂', ['lines.valves']),
    'flask.attach': ('该路没有接收瓶', ['channels.solvents']), 'flask.detach': ('该路有接收瓶且五通阀在关闭位', ['channels.solvents']),
    'keg.refill': ('柜门打开、储罐出液与进气阀关、不超过17 L', ['cabinet.left_angle_rad', 'cabinet.right_angle_rad', 'lines.valves']),
    'keg.sparge': ('储罐出液阀关、有惰性气', ['lines.valves', 'gas.isolation_open']), 'column.replace': ('柱进出口阀关', ['lines.valves']),
    'cabinet.door': ('无', ['plant.configured']), 'truth.inspect': ('无', ['plant.configured']), 'fault.inject': ('通道类故障须给channel', ['plant.configured']),
}
_GAUGE_EFFECTS = _GAUGES + _NEEDLES
_EVOLVE = _GAUGE_EFFECTS + [f'flask.ch{k}' for k in range(1, N_CH + 1)] + ['truth.channels', 'truth.manifold']
EFFECTS = {
    'plant.configure': ['plant.configured', 'channels.solvents', 'regulators.settings', 'lines.valves', 'reservoirs.recorded', 'truth.channels', 'truth.manifold']
                       + _GAUGE_EFFECTS,
    'gas.isolation': ['gas.isolation_open', 'gas.isolation_angle_rad', 'truth.manifold', 'truth.channels'] + _GAUGE_EFFECTS,
    'regulator.set': ['regulators.settings', 'truth.channels'], 'pump.set': ['pump.on'], 'pump.toggle': ['pump.on'],
    'valve.turn': [f'valve.ch{k}_position' for k in range(1, N_CH + 1)] + [f'valve.ch{k}_angle_rad' for k in range(1, N_CH + 1)] + [f'flask.ch{k}' for k in range(1, N_CH + 1)]
                  + ['truth.channels'] + _GAUGE_EFFECTS,
    'needle.set': [f'needle.ch{k}_opening' for k in range(1, N_CH + 1)] + [f'needle.ch{k}_angle_rad' for k in range(1, N_CH + 1)],
    'line.set': ['lines.valves'],
    'flask.attach': [f'flask.ch{k}' for k in range(1, N_CH + 1)] + ['truth.channels'] + _GAUGE_EFFECTS,
    'flask.detach': [f'flask.ch{k}' for k in range(1, N_CH + 1)] + ['truth.channels', 'reservoirs.recorded'] + _GAUGE_EFFECTS,
    'keg.refill': ['truth.channels', 'reservoirs.recorded'], 'keg.sparge': ['truth.channels'], 'column.replace': ['truth.channels', 'lines.valves'],
    'cabinet.door': ['cabinet.left_angle_rad', 'cabinet.right_angle_rad'],
    'fault.inject': ['truth.faults', 'truth.manifold', 'truth.channels'], 'sim.step': ['clock.time_s'],
}
_STEPPED = ('plant.configure', 'gas.isolation', 'regulator.set', 'pump.set', 'pump.toggle', 'valve.turn', 'needle.set', 'line.set', 'flask.attach', 'flask.detach',
            'keg.refill', 'keg.sparge', 'column.replace', 'fault.inject')
ON_STEP = {k: list(_EVOLVE) for k in _STEPPED}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {
    'sim.step': '按0.1 s网格积分（静止区间整段跳过）：惰性气歧管与储罐顶压、真空歧管与泵、接收瓶抽空/回填/泄压、分配流量（层流）、柱的吸水与除氧及突破、鼓泡除氧、'
                '残余空气与瓶壁水对溶剂的污染、表针',
    'valve.turn': '抽空：瓶内气体与溶剂蒸气被抽走；回填：惰性气充到回填压力；分配：溶剂经两根柱与针阀流入瓶中', 'needle.set': '分配流量随开度变化',
    'pump.set': '真空歧管抽到极限压力', 'gas.isolation': '惰性气歧管与储罐顶压建立', 'keg.sparge': '储罐溶解氧按指数下降并带走溶剂蒸气',
    'flask.attach': '瓶中室内空气（含水汽与氧）在分配时进入溶剂，除非先抽空/回填置换', 'keg.refill': '新料含水与溶解氧（空气饱和）', 'fault.inject': '故障作用于泵、供气、过滤器、接口或调压阀',
}
EXEC_TEXT = {'valve.turn': ('valve.k_position为目标位置；经过的位置有短暂作用（抽空位吸走分配管存液，回填位把存液吹入瓶中）', '未配置溶剂时拒绝'),
             'flask.detach': ('flask.chk.attached为false，返回瓶中溶剂体积、质量与瓶内表压（含水、溶解氧与是否在惰性气下记入评测者真值removed_flasks）', '五通阀不在关闭位时拒绝')}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('line.set', 'flask.attach', 'flask.detach', 'keg.refill', 'keg.sparge', 'column.replace', 'cabinet.door'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'valve.turn': ['gauge.vacuum_pa', 'gauge.pressure_pa'], 'gas.isolation': ['gauge.pressure_pa'], 'pump.set': ['gauge.vacuum_pa'],
            'flask.detach': [], 'needle.set': []}
_WB, _PG, _DS, _UCD, _TAMU, _PCH, _SIM = ('user_workbook', 'manufacturer_product_page', 'manufacturer_datasheet_2024', 'user_sop_ucd_puresolv', 'user_manual_tamu_sds',
                                          'pubchem_solvent_properties', 'simulation_assumption')
CAP_SOURCES = {k: [_PG] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_PG, _WB, _SIM], 'valve.turn': [_PG, _UCD], 'flask.attach': [_PG, _UCD, _SIM], 'flask.detach': [_PG, _UCD, _PCH, _WB],
                    'keg.refill': [_PG, _UCD, _TAMU], 'keg.sparge': [_UCD, _TAMU], 'column.replace': [_PG, _DS], 'regulator.set': [_PG, _UCD, _TAMU],
                    'events.read': [_SIM], 'truth.inspect': [_SIM], 'ledger.read': [_SIM], 'fault.inject': [_TAMU, _SIM], 'sim.step': [_SIM]})
CAP_SOURCES.update({k: [_DS, _PG] for k in UNSUPPORTED})
PARAM_ZH = {'ambient_k': '室温', 'relative_humidity': '室内相对湿度（0–1）', 'pressure_pa': '大气压', 'gas': '惰性气（氮气或氩气）', 'supply_gauge_pa': '楼宇供气表压',
            'solvents': '7路溶剂（模板库编号或空）', 'initial_fill_m3': '初始储罐液量', 'feed_water_ppm': '初始进料含水（质量ppm）', 'noise_seed': '噪声种子（本机为模拟表，读数按刻度量化，没有随机噪声；保留以统一接口）',
            'open': '开或关', 'channel': '通道（1–7）', 'keg_gauge_pa': '储罐推送调压阀表压', 'flask_gauge_pa': '接收瓶回填调压阀表压', 'on': '开或关',
            'position': '五通阀位置', 'direction': '转动方向（shortest、clockwise、counterclockwise）', 'opening': '针阀开度（0–1）', 'keg_gas': '储罐进气阀',
            'keg_outlet': '储罐出液阀', 'column_inlet': '柱进口阀', 'column_outlet': '柱出口阀', 'volume_m3': '体积', 'oven_dried': '接收瓶是否烘干',
            'water_ppm': '加注溶剂含水（质量ppm）', 'stabiliser_ppm': '加注溶剂稳定剂含量（质量ppm）', 'grounded': '加注时是否夹接地线', 'duration_s': '鼓泡时间',
            'side': '柜门（左/右）', 'angle_rad': '开启角', 'since_s': '起始时间', 'fault': '故障名', 'active': '注入或清除', 'dt_s': '推进时间'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.solvent_purification_process.limitations。'
CAP_LIMITS = {
    'valve.turn': ['五通阀按位置离散切换，经过的位置只计存液的去向（UCD警告），不计瞬间的气体交换；厂家称该阀杜绝把溶剂分配进真空泵，运行层也不允许分配流直接进泵。'],
    'flask.detach': ['只返回可称量与可读的量；含水与溶解氧本机不测（实际须卡尔费休滴定与氧测定），在评测者truth.inspect的removed_flasks中；柱出口含水的地板值与突破曲线为工程值，不针对具体溶剂。'],
    'keg.refill': ['加注视为离线操作的结果（取出储罐、去压、加注、放回）；稳定剂只计对柱容量的占用。'],
    'keg.sparge': ['鼓泡除氧按指数（时间常数按UCD的15 L/45 min）；带走的溶剂按饱和蒸气计。'],
}
LIMITATIONS = [
    '所选配置：Inert PureSolv MD 7，7路溶剂、每路两根柱（溶剂与柱填料由宿主配置：氧化铝/氧化铝、氧化铝/Q5、分子筛）、17 L储罐、易燃品柜、真空泵；无手套箱集成与柱再生套件。',
    '流动：层流Q=ΔP/(μ·ΣR)，黏度与密度来自溶剂模板（实测文献值），柱床、过滤器与针阀阻力为工程值；储罐顶压按调压阀瞬时建立，断开进气后按顶空气体随液位下降而膨胀；'
    '惰性气歧管在隔离阀关或供气耗尽时为有限体积（1 L，工程值），回填与储罐调压阀补气（最多到两边压力相等，调压阀不倒流）使其压力下降；倒灌气体中的溶剂蒸气按惰性气、水汽按空气计（近似）；几个储罐同时从隔离歧管补气时按通道顺序依次取到两边压力相等（近似，不解联立分配）；五通阀在没有接收瓶时打开：抽空位吸入室内空气，回填位放出惰性气，分配位溶剂流到接液盘。',
    '鼓泡：带走的溶剂蒸气按饱和计，蒸气摩尔分数上限0.5（接近沸点时给警告）；抽空位真空歧管压力高于瓶内时歧管气体（按其空气比例）倒灌进瓶。',
    '纯化：吸水与除氧按柱负荷的突破曲线（容量按800 L HPLC级溶剂标定，进料含水为工程假设）；出口含水地板值不针对具体溶剂；氧化铝柱不除氧（Texas A&M）。',
    '接收瓶：残余空气中的水汽与瓶壁水全部溶入干溶剂（不计水在溶剂中的溶解度上限：烃类与芳烃中实际可能以水滴析出），氧按亨利型瞬时分配；瓶壁水按是否烘干给出；溶剂蒸气只在抽空时计为损失。',
    '手册：UCD的SOP针对PureSolv 400-3-MD（同厂老型号），Texas A&M手册针对Grubbs型系统（厂家未注明）——操作压力、再生体积（400 L）与柱填料分配为同类系统证据，与厂家800 L冲突见source_evidence。',
    '场景：框架、柜、储罐、柱、面板与各阀有几何；接收瓶、溶剂、惰性气与真空没有几何；7个五通阀手柄、7个针阀、9个表针、隔离阀手柄与两扇柜门按运行层状态镜像，真空泵开关为弹簧键。',
]


def _sampling_text(k):
    if k == 'clock.time_s':
        return 'Host clock.'
    if SLOTS[k]['origin'] == 'sensor':
        return 'Analog gauge read on the fixed 0.1 s grid (quantised to the dial resolution); between grid points the last value is held.'
    if SLOTS[k]['origin'] in ('derived', 'process_truth'):
        return 'Integrated on the fixed 0.1 s grid (anchored at t = 0; quiescent intervals skipped exactly); between grid points the last value is held.'
    return 'Changes when a command is accepted.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if CAPS[k]['visibility'] != 'agent' else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _pre_text(k):
    errs, text = CAP_ERRORS.get(k, []), PRECONDITIONS[k][0]
    if 'PLANT_REQUIRED' in errs and '声明' not in text:
        text = '已声明环境与溶剂配置（否则PLANT_REQUIRED）；' + text
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
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景关节（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节', binding_ids=[])


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_product_page': _PG, 'manufacturer_datasheet_2024': _DS, 'user_sop_ucd_puresolv': _UCD, 'user_manual_tamu_sds': _TAMU}
    WB_KEYS = ('dimensions_m', 'reservoir_volume_m3', 'column_dimensions_m', 'column_volume_m3', 'drying_capacity_m3', 'pressure_gauge_pa', 'main_relief_pa',
               'pump_speed_m3_s', 'pump_ultimate_pa', 'particle_filter_m', 'solvents_max')
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in WB_KEYS else ()))
    cal = {k for k, v in instrument()['engineering'].items() if v.get('calibrated_by')}
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明），不是实测' + ('；由calibrate_ps.py标定' if k in cal else ''))
    st = _lib('solvents')
    asm('solvent_templates', dict(file='assets/expansion_batch04/t2_vacuum/inert_puresolvmd/solvent_templates.json', counts=st['counts']), '1',
        '分配溶剂模板：蒸气压（NIST Antoine或PubChem实测点）、密度与黏度（PubChem/HSDB）、柱填料与厂家清单标记（每条带evidence与source）', '实测文献数据；适用规则见模板库rules_zh',
        (_PCH,))
    for name, val, unit, meaning in (('gas_constant_j_mol_k', R_GAS, 'J/(mol K)', '气体常数'), ('psi_pa', PSI, 'Pa', '1 psi'), ('inhg_pa', INHG, 'Pa', '1 inHg')):
        asm(name, val, unit, meaning, '物理常数或单位换算', (_SIM,))
    return P


SLOT_REFERENCE = {'gauge.vacuum_pa': '表压（相对大气，负为真空）', 'gauge.pressure_pa': '表压（相对大气）', **{f'gauge.station{k}_pa': '表压（相对大气，负压读0）' for k in range(1, N_CH + 1)}}


def export_inert_puresolvmd_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'InertPuresolvmdSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.inert_puresolvmd.InertPuresolvmdSession.invoke',
                                              invocation=f'session.invoke(instance_id="inert_puresolvmd_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='InertPuresolvmdRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（压力、流量与组成在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['frame', 'cabinet', 'columns', 'plumbing', 'vacuum_pump', 'gas_manifold', 'panel'] + [f'reservoir_{k}' for k in range(1, N_CH + 1)],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='溶剂纯化与无水分配：惰性气把储罐中的原溶剂推过两根串联纯化柱（氧化铝吸水、Q5除氧、分子筛），经过滤器、五通阀与针阀流入事先抽空/回填置换过的接收瓶；'
                   '柱负荷累积直至突破；储罐鼓泡除氧；接收瓶残余空气与瓶壁水污染干溶剂；真空泵抽走瓶内气体与残留溶剂蒸气；各压力表指示歧管与瓶内压力',
        equations=['Q = max(p_head - p_flask - rho g h, 0) / (mu (2 R_col + R_filter + R_needle / a))',
                   'w_out = w_floor + (w_in - w_floor) / (1 + exp(-(L/C - x50)/dx));  dL/dt = rho Q (w_in - w_out)  (per column in series; Q5 the same for O2)',
                   'p_flask = n_gas R T / (V - V_liq) + p_vap(T) [liquid present];  evacuate: dn/dt = -C (p_flask - p_vac) / (R T);  refill: dn/dt = C_r (p_reg - p_flask) / (R T)',
                   'p_vac: dn/dt = -S (p_vac - p_ult) / (R T) + sum flask flows;  sparging: dO2/dt = -O2 / tau',
                   'flask contact: water vapour and wall water dissolve; O2 partitions with H = c_sat / (0.2095 p0)',
                   'keg headspace: p_head = n_keg R T / (V_keg + V_min - V_liq); push regulator adds dn = min(n_set - n_keg, (n_im V_keg - n_keg V_im)/(V_im + V_keg)) from an isolated manifold (unlimited from the building supply), relieves above its set point, no back-flow',
                   'isolated inert-gas manifold: dp_im = -R T (dn_kegs + dn_refill) / V_im (V_im = gas_manifold_volume_m3); refilled to the supply pressure while the isolation valve is open and the supply available',
                   'sparging: carried solvent n_v = n_gas y / (1 - y), y = min(p_vap / p_amb, sparge_vapour_fraction_max)',
                   'evacuate: back-flow dn = -C (p_vac - p_flask) / (R T) when p_vac > p_flask, carrying the manifold air fraction; open joint (no flask): room air into the vacuum, inert gas out of the refill line, solvent onto the drip tray'],
        parameters=_params(), source_ids=[_SIM, _WB, _PG, _DS, _UCD, _TAMU, _PCH], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ps_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('inert_puresolvmd', caps, slots, {'solvent_purification_process': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'flask.attach': (FLASK, CAPS['flask.attach']['example_arguments']),
                                'keg.refill': (REFILL, CAPS['keg.refill']['example_arguments'])})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'solvent_purification_process': mechanism})
