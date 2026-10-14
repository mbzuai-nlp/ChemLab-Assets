"""CHEM-090 Elementar isoprime precisION stable isotope ratio mass spectrometer with the centrION continuous-flow interface: process runtime.

Device (product page key features; brochure 05/2020: ion optics, centrION, critical specifications): electron-impact source over a single Edwards
nEXT240D turbomolecular pump, 0-5 kV acceleration, 90 deg stigmatic magnet with 108 mm radius (resolution > 110 m/dm at 10 % valley, 1-76 amu at
3 kV, max single mass 96 amu), Universal Collector Array (four fixed Faraday cups and an electrostatic filter for HD / 3He; up to 10 beams over
+-25 % mass), 0-100 V amplifiers with automatic resistor switching, 800 (dual inlet) / 1100 (continuous flow) CO2 molecules per ion, H3+ factor
<= 8.0 ppm/nA, automatic vacuum isolation valve on power failure, pump-down within 12 h without bakeout, colour status LED; centrION with up to five
continuous-flow inlets, six monitoring (reference) gases, digital MFCs, dilution and sleep / wake-up; standard configurations for CO2, N2, CO, SO2,
H2, O2 and N2O.

Process model (fixed 0.1 s grid anchored at t = 0 while beams are measured; otherwise intervals are skipped exactly because the vacuum, turbo,
filament hours and helium use are closed-form functions of time; SI units: Pa absolute, A, V, ohm, T, mol, isotope deltas as fractions):
  * Vacuum: analyser pressure p = (Q_water(t) + Q_floor + Q_leak + Q_he) / (S x turbo speed fraction), Q_water = q1 (3600 s / t_pumped) after an
    air exposure (calibrated to the 12 h pump-down); the turbo runs up linearly; a power failure closes the isolation valve (sealed pressure rise),
    the turbo runs down; the source may only be switched on below the ready pressure.
  * Gas into the source: centrION carrier helium with the open split - the selected inlet's sample peaks (Gaussian in time, moles of converted
    gas) and the monitoring-gas pulses (rectangular with a rise time) reach the source with the split fraction x (1 - dilution); dual inlet:
    viscous flow from a bellows (pressure falls as it is consumed).
  * Ions: ions/s = molecules/s x (emission / nominal) / (molecules per ion of the mode x relative ionisation of the gas); isotopologue shares from
    the isotope ratios (17O mass-dependent, lambda 0.528); magnet field B for the centre mass m from B r = sqrt(2 m U / e); detuning moves the beams
    off the flat-topped cups; abundance-sensitivity tail; H3+ = K I2^2 on m/z 3; air / water background currents; ratio linearity with the beam.
  * Measurement: Faraday currents with shot noise and resistor Johnson noise, V = I R + offset, 100 V range, automatic resistor switching; every
    0.1 s sample of an acquisition is recorded; peaks are detected on the major beam, integrated against the preceding baseline; reference peaks
    are the scheduled monitoring-gas pulses; sample deltas are calculated against the last reference peak (assigned reference value), CO2 with the
    17O correction (Santrock-type, the anchors of delta_templates.json), H2 with the H3+ factor determined by the operator; normalisation to the
    international scales with reference materials (least squares, at least two materials).
  * Ledgers: injected sample / reference moles = open-split vent + source (neutral pumped + ionised); collected charge = ionised charge reaching the
    cups; helium used = carrier flow x time."""
import bisect
import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.elementar_isoprimeprecision"
CATALOG_ID = "CHEM-090"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t3_analytical/elementar_isoprimeprecision'
LIBS = {'instrument': 'instrument_model.json', 'deltas': 'delta_templates.json'}


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
DERIVED = {k: v['value'] for k, v in instrument()['derived'].items()}
RM = _lib('deltas')['substances']
ANCHOR = {k: v['value'] for k, v in _lib('deltas')['scale_anchors'].items()}
RM_IDS = sorted(RM)
DT = 0.1
E_CH = 1.602176634e-19
U_KG = 1.66053906660e-27
K_B = 1.380649e-23
N_A = 6.02214076e23
R_GAS = 8.314462618
LAMBDA_17 = ANCHOR['17O_correction']['lambda_']
N_INLETS = 5
DI_MIN_MAJOR_A = 1e-11                                                   # dual inlet: mean major beam a block needs to be evaluated (10 pA; engineering)
N_REF = 6
INLET_TYPES = ['ea', 'gc', 'liquiface', 'trace_gas', 'headspace', 'dual_inlet']
SPECIES = ['CO2', 'N2', 'CO', 'SO2', 'H2', 'O2', 'N2O']
# standard detector configuration (brochure p.26): masses on the cups, the major mass first
MASSES = {'CO2': [44, 45, 46], 'N2': [28, 29], 'CO': [28, 29, 30], 'SO2': [64, 66], 'H2': [2, 3], 'O2': [32, 33, 34], 'N2O': [44, 45, 46]}
ISOTOPES = {'CO2': ['13C', '18O'], 'N2': ['15N'], 'CO': ['18O'], 'SO2': ['34S'], 'H2': ['2H'], 'O2': ['18O'], 'N2O': ['15N', '18O']}
STATUS = ['off', 'red', 'amber', 'green']
FAULTS = ['mains_outage', 'filament_burnout', 'vacuum_leak', 'he_supply_empty', 'magnet_drift', 'reference_gas_empty']
MAX_EVENTS_LOG = 3000
MAX_RESULTS = 2000
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


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ---------------------------------------------------------------- isotopes
def ratios_from_deltas(species, d):
    """Absolute isotope ratios of a gas from its deltas (fractions on VPDB, Air-N2, VSMOW, VCDT; 17O mass dependent with lambda 0.528)."""
    d13, d15, d18, d2, d34 = (d.get(k, 0.0) for k in ('13C', '15N', '18O', '2H', '34S'))
    r18 = ANCHOR['18O_VSMOW'] * (1.0 + d18)
    r17 = ANCHOR['17O_VSMOW'] * (1.0 + d18) ** LAMBDA_17
    r13 = ANCHOR['13C_VPDB'] * (1.0 + d13)
    r15 = ANCHOR['15N_air'] * (1.0 + d15)
    r2 = ANCHOR['2H_VSMOW'] * (1.0 + d2)
    r34 = ANCHOR['34S_VCDT'] * (1.0 + d34)
    r33 = 0.0078 * (1.0 + d34) ** 0.515                                 # 33S/32S of normal sulfur (mass dependent; engineering, only on m/z 65)
    return dict(r13=r13, r17=r17, r18=r18, r15=r15, r2=r2, r34=r34, r33=r33)


def isotopologues(species, r):
    """Relative abundances of the measured masses (major isotopologue = 1)."""
    if species == 'CO2':
        return {44: 1.0, 45: r['r13'] + 2 * r['r17'], 46: 2 * r['r18'] + 2 * r['r13'] * r['r17'] + r['r17'] ** 2}
    if species == 'N2':
        return {28: 1.0, 29: 2 * r['r15']}
    if species == 'CO':
        return {28: 1.0, 29: r['r13'] + r['r17'], 30: r['r18'] + r['r13'] * r['r17']}
    if species == 'SO2':
        return {64: 1.0, 66: r['r34'] + 2 * r['r18'] + 2 * r['r33'] * r['r17'] + r['r17'] ** 2}
    if species == 'H2':
        return {2: 1.0, 3: 2 * r['r2']}
    if species == 'O2':
        return {32: 1.0, 33: 2 * r['r17'], 34: 2 * r['r18'] + r['r17'] ** 2}
    if species == 'N2O':
        return {44: 1.0, 45: 2 * r['r15'] + r['r17'], 46: r['r18'] + 2 * r['r15'] * r['r17'] + r['r15'] ** 2}
    raise ValueError(species)


def co2_17o_correction(r45, r46):
    """R13 and R18 from the CO2 ratios 45R and 46R with R17 = K R18^lambda (Santrock-type solution).  K is taken from the VSMOW anchors
    (R17_VSMOW / R18_VSMOW^lambda) - the same relation the forward model (ratios_from_deltas) uses to make R17, so the round trip is exact; the
    IUPAC 2010 VPDB-CO2 constant in delta_templates.json ('17O_correction', K = 0.01022461) is kept for reference and only its lambda is used.
    Reported deltas are relative to the reference gas and normalised with standards, so the choice of K cancels to first order."""
    K = ANCHOR['17O_VSMOW'] / ANCHOR['18O_VSMOW'] ** LAMBDA_17
    if not (r45 > 0.0 and r46 > 0.0):
        return None
    x = r46 / 2.0                                                        # Newton on R18 (kept positive)
    converged = False
    for _ in range(60):
        r17 = K * x ** LAMBDA_17
        f = -3.0 * r17 ** 2 + 2.0 * r45 * r17 + 2.0 * x - r46
        df = -6.0 * r17 * LAMBDA_17 * r17 / x + 2.0 * r45 * LAMBDA_17 * r17 / x + 2.0
        step = f / df if df != 0.0 else float('inf')
        x_new = x - step
        if not math.isfinite(x_new) or x_new <= 0.0:
            x_new = 0.5 * x                                               # damped: stay in R18 > 0
        step, x = x - x_new, x_new
        if abs(step) < 1e-18:
            converged = True
            break
    r13 = r45 - 2.0 * K * x ** LAMBDA_17
    if not converged or r13 <= 0.0:
        return None                                                      # unphysical ratio pair (no beam / noise): no delta
    return r13, x


def deltas_from_ratios(species, rr):
    """Deltas from the absolute ratios of the measured masses (inverse of isotopologues for the reported isotopes)."""
    if species == 'CO2':
        sol = co2_17o_correction(rr[45], rr[46])
        if sol is None:
            return None
        r13, r18 = sol
        return {'13C': r13 / ANCHOR['13C_VPDB'] - 1.0, '18O': r18 / ANCHOR['18O_VSMOW'] - 1.0}
    if species == 'N2':
        return {'15N': rr[29] / 2.0 / ANCHOR['15N_air'] - 1.0}
    if species == 'CO':
        return {'18O': rr[30] / ANCHOR['18O_VSMOW'] - 1.0}                # 13C-17O term neglected (documented limitation)
    if species == 'SO2':
        return {'34S': (rr[66] - 2.0 * ANCHOR['18O_VSMOW']) / ANCHOR['34S_VCDT'] - 1.0}   # oxygen of SO2 assumed at VSMOW (documented limitation)
    if species == 'H2':
        return {'2H': rr[3] / 2.0 / ANCHOR['2H_VSMOW'] - 1.0}
    if species == 'O2':
        return {'18O': (rr[34] - (rr[33] / 2.0) ** 2) / 2.0 / ANCHOR['18O_VSMOW'] - 1.0}
    if species == 'N2O':
        r15 = (rr[45] - ANCHOR['17O_VSMOW']) / 2.0
        return {'15N': r15 / ANCHOR['15N_air'] - 1.0, '18O': (rr[46] - 2 * r15 * ANCHOR['17O_VSMOW'] - r15 ** 2) / ANCHOR['18O_VSMOW'] - 1.0}
    raise ValueError(species)


def b_for(mass, volts):
    """Magnet field (T) that puts mass (u) on the axis at an acceleration voltage (V): B r = sqrt(2 m U / e)."""
    return math.sqrt(2.0 * mass * U_KG * volts / E_CH) / DOC['radius_m']


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
ITEM_ID = {'type': 'string', 'pattern': r'[a-z][a-z0-9_]{0,47}'}
DELTAS = obj({k: number('1', -0.999, 10.0) for k in ('13C', '15N', '18O', '2H', '34S')}, optional=('13C', '15N', '18O', '2H', '34S'))


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


REFGAS = obj(dict(species=enum(SPECIES), deltas=DELTAS, fill_mol=number('mol', 0.0, 10.0)), optional=('fill_mol',))
PLANT_EXAMPLE = dict(ambient_k=295.15, pressure_pa=101325.0, he_supply_gauge_pa=2.0e7, inlets=['ea', 'gc', None, None, 'dual_inlet'],
                     reference_gases=[dict(species='CO2', deltas={'13C': -0.0367, '18O': 0.0251}), dict(species='N2', deltas={'15N': -0.0012}),
                                      None, None, None, None],
                     combustion_o2_delta18o=0.0235, vented=False, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 288.15, 303.15), pressure_pa=number('Pa', 80000.0, 110000.0), he_supply_gauge_pa=number('Pa', 0.0, 2.5e7),
                 inlets=array(nullable(enum(INLET_TYPES)), N_INLETS, N_INLETS), reference_gases=array(nullable(REFGAS), N_REF, N_REF),
                 combustion_o2_delta18o=number('1', -0.1, 0.1), vented=BOOL, noise_seed=integer(0, 2 ** 31 - 1)),
            optional=('combustion_o2_delta18o', 'vented'))
DECLARE = obj(dict(sample_id=ITEM_ID, deltas=DELTAS, note=TEXT), optional=('note',))
INJECT = obj(dict(inlet=integer(1, N_INLETS), species=enum(SPECIES), amount_mol=number('mol', 1e-12, 1e-3), sample_id=ITEM_ID, substance_id=enum(RM_IDS),
                  delay_s=number('s', 0.0, 3600.0), width_s=number('s', 0.5, 120.0)),
             optional=('sample_id', 'substance_id', 'delay_s', 'width_s'))
PULSE = obj(dict(gas=integer(1, N_REF), start_s=number('s', 0.0, 7200.0), duration_s=number('s', 2.0, 120.0), intensity_a=number('A', 1e-11, 5e-8)),
            optional=('intensity_a',))
RUN = obj(dict(duration_s=number('s', 5.0, 7200.0), reference_pulses=array(PULSE, 0, 40)), optional=('reference_pulses',))
DI = obj(dict(species=enum(SPECIES), sample_id=ITEM_ID, substance_id=enum(RM_IDS), sample_bar_ul=number('bar uL', 1.0, 2000.0), reference_gas=integer(1, N_REF),
              cycles=integer(1, 30), integration_s=number('s', 4.0, 60.0)),
         optional=('sample_id', 'substance_id', 'cycles', 'integration_s'))
NORM = obj(dict(isotope=enum(['13C', '15N', '18O', '2H', '34S']), standards=array(obj(dict(peak=integer(1, 10 ** 6), substance_id=enum(RM_IDS))), 2, 40)))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'operation', 'warning', 'maintenance', 'fault', 'truth', 'alarm']), code=TEXT, message_zh=TEXT))
_BODY = ['chassis']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明室温、大气压、氦气瓶表压、centrION上连接的5个进样系统（元素分析仪、GC、LiquiFace、痕量气、顶空、双路进样或空）、6种监测（参比）气的气种与真值δ、'
    '燃烧用氧的δ18O、开机前分析器是否暴露大气、噪声种子', 'evaluator', 'configuration')
cap('sample.declare', DECLARE, obj(dict(sample_id=TEXT)), dict(sample_id='leaf_a', deltas={'13C': -0.0281, '15N': 0.0034}), _BODY,
    '宿主声明一个未知样品（编号与真值δ；操作者只见编号）', 'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['power_switch'], '主电源开/关（涡轮泵、电子学、centrION）')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['power_switch'], '主电源开关按一下')
cap('vacuum.vent', obj({}), obj(dict(vented=BOOL)), {}, ['turbo_pump'], '分析器放气到大气（维护；须关灯丝并关泵）', 'agent', 'device_command')
cap('vacuum.pump_down', obj({}), obj(dict(pumping=BOOL)), {}, ['turbo_pump'], '启动涡轮泵抽真空（不烘烤12 h内抽到工作真空）', 'agent', 'device_command')
cap('source.set', obj(dict(filament=BOOL, emission_a=number('A', 50e-6, 800e-6), acceleration_v=number('V', 500.0, 5000.0)),
                      optional=('filament', 'emission_a', 'acceleration_v')),
    obj(dict(source=FREE_OBJ)), dict(filament=True, emission_a=400e-6, acceleration_v=3000.0), ['ion_source'],
    '离子源：灯丝开/关、发射（捕集）电流、离子加速电压（0–5 kV）')
cap('source.replace_filament', obj({}), obj(dict(source=FREE_OBJ)), {}, ['ion_source'], '更换灯丝（维护：须先放气；自对中灯丝，累计小时清零）', 'agent', 'robot_skill')
cap('magnet.set_species', obj(dict(species=enum(SPECIES))), obj(dict(field_t=number('T'), masses=array(integer(1, 96), 1, 10))), dict(species='CO2'),
    ['magnet'], '磁场跳到某气种的标准检测器配置（主质量在中心杯）')
cap('magnet.peak_center', obj({}), obj(dict(field_t=number('T'), offset_t=number('T'))), {}, ['magnet'], '峰中心：扫描磁场使主束落在杯平顶中心')
cap('amplifier.configure', obj(dict(mode=enum(['auto', 'fixed']), resistors_ohm=array(number('ohm', 1e8, 1e12), 1, 10)), optional=('resistors_ohm',)),
    obj(dict(amplifier=FREE_OBJ)), dict(mode='auto'), ['collector_array'], '放大器：自动切换电阻或固定电阻（按质量从主到次）')
cap('centrion.set', obj(dict(inlet=integer(1, N_INLETS), carrier_on=BOOL, carrier_m3_s=number('m3/s', 1.0e-7, 3.0e-6), dilution=number('1', 0.0, 0.95), sleep=BOOL),
                        optional=('inlet', 'carrier_on', 'carrier_m3_s', 'dilution', 'sleep')),
    obj(dict(centrion=FREE_OBJ)), dict(inlet=1, carrier_on=True, carrier_m3_s=1.67e-6, dilution=0.0), ['centrion'],
    'centrION：选择进样口、氦载气开关与流量（数字MFC）、稀释份额、休眠/唤醒')
cap('reference.assign', obj(dict(gas=integer(1, N_REF), deltas=DELTAS)), obj(dict(gas=integer(1, N_REF), deltas=FREE_OBJ)), dict(gas=1, deltas={'13C': -0.0367, '18O': 0.0251}),
    ['centrion'], '给第k种监测（参比）气赋值（ionOS中的参比气δ；本机用它把样品换算到参比标尺）')
cap('sample.inject', INJECT, obj(dict(injection=integer(1, 10 ** 6), arrival_s=number('s'))), dict(inlet=1, species='CO2', amount_mol=4.0e-6, substance_id='usgs40', delay_s=30.0),
    ['centrion'], '进样系统把转化好的样品气（编号或标准物质）经第k个进样口送入centrION（元素分析仪/GC的燃烧与色谱不在本机范围）', 'agent', 'robot_skill')
cap('acquisition.start', RUN, obj(dict(run=integer(1, 10 ** 6), ends_s=number('s'))), dict(duration_s=300.0, reference_pulses=[dict(gas=1, start_s=20.0, duration_s=20.0)]),
    ['collector_array'], '开始一次连续流采集（按方法在指定时间注入参比气脉冲；结束时自动识别峰、积分并计算δ）')
cap('acquisition.stop', obj({}), obj(dict(run=nullable(integer(1, 10 ** 6)))), {}, ['collector_array'], '提前结束当前采集（已采到的数据照常处理）')
cap('dual_inlet.start', DI, obj(dict(run=integer(1, 10 ** 6), ends_s=number('s'))), dict(species='CO2', substance_id='nbs19', sample_bar_ul=100.0, reference_gas=1, cycles=10),
    ['centrion'], '双路进样：样品与参比气交替进源，按周期积分，结束时给出δ与内精度')
cap('h3.determine', obj(dict(run=integer(1, 10 ** 6))), obj(dict(factor_ppm_na=number('ppm/nA'), points=integer(0, 100))), dict(run=1), ['collector_array'],
    '用一次含不同强度H₂参比脉冲的采集确定H₃⁺因子（m/z 3对m/z 2电流平方的斜率）')
cap('calibration.normalize', NORM, obj(dict(isotope=TEXT, slope=number('1'), intercept=number('1'), residuals=array(number('1'), 2, 40))),
    dict(isotope='13C', standards=[dict(peak=2, substance_id='usgs40'), dict(peak=3, substance_id='usgs41')]), ['collector_array'],
    '用标准物质把相对参比气的δ归一到国际标尺（最小二乘直线，至少两种标准物质）')
cap('hood.set', obj(dict(angle_rad=number('rad', 0.0, 1.9))), obj(dict(angle_rad=number('rad'))), dict(angle_rad=1.9), ['hood'], '透明护罩开合（维护时打开；上翻）', 'agent', 'robot_skill')
cap('results.read', obj(dict(since_peak=integer(0, 10 ** 6)), optional=('since_peak',)), obj(dict(peaks=array(FREE_OBJ, 0, MAX_RESULTS))), dict(since_peak=0),
    ['collector_array'], '读取峰结果（面积、比值、相对参比气的δ、归一后的δ）', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _BODY,
    '事件记录（操作、报警、维护）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(process=FREE_OBJ, samples=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, _BODY,
    '评测者读取过程真值：真空、离子流、注入样品的真值δ、参比气真值', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '样品与参比气物质的量、收集电荷、氦气用量的守恒账', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL, gas=integer(1, N_REF)), optional=('gas',)), obj(dict(faults=array(TEXT, 0, 16))),
    dict(fault='vacuum_leak', active=True), _BODY, '故障注入：停电、灯丝烧断、真空漏气、氦气耗尽、磁场漂移、某参比气瓶空', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 30 * DAY))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'bakeout.start': '烘烤为选件（产品页：optional bakeout），本配置未选。',
    'clumped.measure': 'CO₂团簇同位素（47–49）需选配检测器（产品册Optional detector configurations），标准通用收集器阵列不支持。',
    'sample.combust': '元素分析仪/GC的燃烧、还原与色谱属于外接进样系统（另一台仪器），本机只接收已转化的气体。',
    'halogen.measure': 'ClCH₃、BrCH₃同位素需选配检测器，本配置不支持。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'chassis', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'chassis', '环境与进样配置是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'power_switch', '主电源是否接通')
slot('status.led', enum(STATUS), '1', 'derived', 'status_led', '状态LED颜色：off无电、amber抽真空/待机、green就绪（真空好且灯丝开）、red报警')
for _c, _zh in (('green', '绿'), ('amber', '琥珀'), ('red', '红')):
    slot(f'indicator.led_{_c}', BOOL, '1', 'derived', 'status_led', f'前面状态灯的{_zh}色是否点亮（status.led为{_c}时为真；场景灯按此着色）')
slot('vacuum.pressure_pa', nullable(number('Pa')), 'Pa', 'sensor', 'turbo_pump', '分析器真空读数（两位有效数字；无电或分析器在大气时为空）')
slot('vacuum.state', enum(['vented', 'pumping', 'ready', 'isolated', 'off']), '1', 'derived', 'turbo_pump', '真空状态：大气、抽气中、就绪、隔离阀关闭、泵停')
slot('source.state', FREE_OBJ, '1', 'setpoint', 'ion_source', '离子源：灯丝、发射电流、加速电压、灯丝累计小时、灯丝故障（filament_failed：烧断或开路；fault_active：故障仍在）')
slot('magnet.state', FREE_OBJ, '1', 'setpoint', 'magnet', '磁场：设定、气种配置与各杯质量')
slot('amplifier.state', FREE_OBJ, '1', 'setpoint', 'collector_array', '放大器：模式与各杯电阻')
slot('beams.v', FREE_OBJ, 'V', 'sensor', 'collector_array', '各杯放大器电压（ionOS束流显示，0.1 s；0–100 V，饱和读100）')
slot('centrion.state', FREE_OBJ, '1', 'setpoint', 'centrion', 'centrION：进样口、氦载气、稀释、休眠、参比气赋值')
slot('acquisition.state', FREE_OBJ, '1', 'derived', 'collector_array', '采集：运行号、开始与结束时间、已识别峰数')
slot('results.count', integer(0, 10 ** 6), '1', 'derived', 'collector_array', '已处理的峰结果数')
slot('calibration.state', FREE_OBJ, '1', 'derived', 'collector_array', '各同位素的归一直线与H₃⁺因子')
slot('hood.angle_rad', number('rad'), 'rad', 'setpoint', 'hood', '透明护罩开启角（0关闭）')
slot('truth.process', FREE_OBJ, '1', 'process_truth', 'chassis', '真空、各质量真实离子流、灯丝、磁场偏差、在途样品', 'evaluator')
slot('truth.samples', FREE_OBJ, '1', 'process_truth', 'centrion', '注入的样品与参比气真值（物质的量与δ）', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'chassis', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'hood.angle_rad'}


# ---------------------------------------------------------------- runtime
class ElementarIsoprimeprecisionRuntime:
    def __init__(self, instance_id='elementar_isoprimeprecision_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.power = False
        self.hood = 0.0
        self.vac = dict(exposed_at=None, pumping_since=None, turbo_on_at=None, turbo_off_at=None, speed_at_off=0.0, sealed_at=None, sealed_p0=None,
                        isolation_open=False)
        self.source = dict(filament=False, emission_a=ENG['emission_nominal_a'], acceleration_v=3000.0, hours=0.0, burnt=False)
        self.magnet = dict(species=None, field_t=0.0, set_at=0.0)
        self.amp = dict(mode='auto', fixed=None, current={})
        self.cen = dict(inlet=1, carrier_on=False, carrier_m3_s=ENG['carrier_he_m3_s'], dilution=0.0, sleep=False, assigned={})
        self.declared = {}
        self.in_flight = []                                                  # gas pulses on their way to the source
        self.run = None
        self.di = None
        self.results = []
        self.norm = {}
        self.h3_k = 0.0                                                      # operator's H3+ factor (A^-1), 0 until determined
        self.run_h3 = {}                                                     # run id -> H3+ factor its ratios were processed with
        self.n_inj = self.n_run = self.n_peak = 0
        self.beams = {}
        self.delivered = []                                                  # every injected sample (truth for the peak results)
        self.ref_left = []
        self.faults = set()
        self.fault_gas = set()
        self.events, self.events_dropped = [], 0
        self.revision = 0
        self.led = None
        self.he_seg = None
        self.t_polled = 0.0                                                  # end of the host's last step (events are not dated before it)
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
        return ElementarIsoprimeprecisionSession(self, name)

    def _event(self, kind, code, message):
        self.events.append(dict(time_s=round(self.t, 6), kind=kind, code=code, message_zh=message))
        if len(self.events) > MAX_EVENTS_LOG:
            self.events_dropped += len(self.events) - MAX_EVENTS_LOG
            del self.events[:-MAX_EVENTS_LOG]

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self._mains():
            raise ActionRejected('POWER_OFF')

    # ---- closed-form plant state
    def _mains(self):
        return self.power and 'mains_outage' not in self.faults

    def _turbo_speed(self, t=None):
        """Turbo speed fraction: linear run-up from turbo_on_at, exponential run-down after turbo_off_at."""
        t = self.t if t is None else t
        v = self.vac
        if v['turbo_on_at'] is not None:
            return min(max(t - v['turbo_on_at'], 0.0) / ENG['turbo_spinup_s'], 1.0)
        if v['turbo_off_at'] is not None:
            return v['speed_at_off'] * math.exp(-max(t - v['turbo_off_at'], 0.0) / ENG['turbo_spindown_s'])
        return 0.0

    def _gas_load(self, t=None):
        """Gas load on the analyser (Pa m3/s): water from the last air exposure (1/t), floor, leak, helium from the open split."""
        t = self.t if t is None else t
        v = self.vac
        q = ENG['outgassing_floor_pa_m3_s'] + ENG['air_leak_pa_m3_s'] + (ENG['fault_leak_pa_m3_s'] if 'vacuum_leak' in self.faults else 0.0)
        if v['pumping_since'] is not None:
            q += ENG['outgassing_q1_pa_m3_s'] * 3600.0 / max(t - v['pumping_since'], 60.0)
        if self._mains() and self.cen['carrier_on'] and not self.cen['sleep'] and self._he_ok():
            q += ENG['source_gas_load_pa_m3_s']
        return q

    def _pressure(self, t=None):
        """Analyser pressure (Pa absolute)."""
        t = self.t if t is None else t
        v = self.vac
        pa = self.plant['pressure_pa'] if self.plant else 101325.0
        if v['pumping_since'] is None and v['sealed_at'] is None:
            return pa                                                        # vented
        if v['sealed_at'] is not None:                                        # isolation valve closed: sealed volume, pressure rises
            return min(v['sealed_p0'] + ENG['sealed_rise_pa_s'] * max(t - v['sealed_at'], 0.0) * (50.0 if 'vacuum_leak' in self.faults else 1.0), pa)
        s = ENG['turbo_speed_m3_s'] * self._turbo_speed(t)
        if s <= 1e-6:
            return pa
        return min(self._gas_load(t) / s, pa)

    def _vacuum_state(self):
        v = self.vac
        if v['sealed_at'] is not None:
            return 'isolated'
        if v['pumping_since'] is None:
            return 'vented'
        if not self._mains():
            return 'off'
        p_dry = self._pressure() - (ENG['source_gas_load_pa_m3_s'] / (ENG['turbo_speed_m3_s'] * max(self._turbo_speed(), 1e-6))
                                   if self._mains() and self.cen['carrier_on'] and not self.cen['sleep'] and self._he_ok() else 0.0)
        return 'ready' if p_dry <= ENG['vacuum_ready_pa'] and self._turbo_speed() >= 0.999 else 'pumping'      # judged without the helium load

    def _he_ok(self):
        return self.plant is not None and self.plant['he_supply_gauge_pa'] > 0.0 and 'he_supply_empty' not in self.faults and self.led is not None \
            and self.led['he_used_m3'] < self._he_available()

    def _he_available(self):
        return ENG['he_supply_m3'] * self.plant['he_supply_gauge_pa'] / self.plant['pressure_pa']

    def _source_on(self):
        return self.source['filament'] and self._mains() and not self.source['burnt'] and 'filament_burnout' not in self.faults

    # ---- ion optics
    def _field_true(self):
        drift = ENG['magnet_drift_t_per_h'] * (self.t - self.magnet['set_at']) / 3600.0 * (40.0 if 'magnet_drift' in self.faults else 1.0)
        return self.magnet['field_t'] + drift

    def _transmission(self, mass, centre):
        """Fraction of the beam of `mass` on its cup (flat-topped peaks; detuning moves the beam off the flat top)."""
        if self.magnet['species'] is None or self.magnet['field_t'] <= 0.0:
            return 0.0
        b0 = b_for(centre, self.source['acceleration_v'])
        dm = 2.0 * (self._field_true() - b0) / b0 * mass                    # mass offset (u) on the collector plane
        half_flat = 0.5 * ENG['peak_flat_fraction']
        edge = mass / DOC['resolution'] / 2.355
        x = abs(dm) - half_flat
        return 1.0 if x <= 0.0 else math.exp(-0.5 * (x / edge) ** 2)

    def _rel_ionisation(self, species):
        return ENG['relative_ionisation'][species]

    def _ions_per_molecule(self, species, mode):
        eff = DOC['ionisation_cf'] if mode == 'cf' else DOC['ionisation_di']
        return self.source['emission_a'] / ENG['emission_nominal_a'] * self._rel_ionisation(species) / eff * ENG['ion_transmission']

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'results.read'):
            self.revision += 1
        return out

    def _cmd_plant_configure(self, ambient_k, pressure_pa, he_supply_gauge_pa, inlets, reference_gases, noise_seed, combustion_o2_delta18o=0.0235, vented=False):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        if self.plant is not None and (list(inlets) != self.plant['inlets'] or [r and r['species'] for r in reference_gases] != [r and r['species'] for r in self.plant['reference_gases']]):
            raise ActionRejected('CONFIG_FIXED')
        for r in reference_gases:
            if r is not None and any(k not in ISOTOPES[r['species']] for k in r['deltas']):
                raise ActionRejected('INVALID_ARGUMENT')
        first = self.plant is None
        self.plant = dict(ambient_k=ambient_k, pressure_pa=pressure_pa, he_supply_gauge_pa=he_supply_gauge_pa, inlets=list(inlets),
                          reference_gases=copy.deepcopy(reference_gases), combustion_o2_delta18o=combustion_o2_delta18o)
        self.seed = noise_seed
        if first:
            self.he_seg = None
            self.led = dict(injected_mol={}, vented_mol={}, source_mol={}, ionised_mol={}, ref_injected_mol={}, ref_vented_mol={}, ref_source_mol={},
                            ref_ionised_mol={}, charge_c=0.0, charge_ionised_c=0.0, he_used_m3=0.0, di_consumed_mol=0.0, di_source_mol=0.0, di_ionised_mol=0.0)
            self.ref_left = [None if r is None else float(r.get('fill_mol', 2.0)) for r in reference_gases]
            if vented:
                self.vac.update(pumping_since=None, sealed_at=None)
            else:                                                             # a system standing pumped down for a long time
                self.vac.update(pumping_since=-30 * DAY, exposed_at=-30 * DAY)
        self._event('system', 'PLANT_CONFIGURED', '环境与进样配置已声明')
        return dict(configured=True)

    def _cmd_sample_declare(self, sample_id, deltas, note=None):
        self._need_plant()
        if sample_id in RM:
            raise ActionRejected('NAME_TAKEN')
        self.declared[sample_id] = dict(deltas=dict(deltas), note=note)
        return dict(sample_id=sample_id)

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not self.power:
            self.power = True
            self._event('system', 'POWER_ON', '主电源接通')
            self._mains_on()
        elif not on and self.power:
            self._mains_off()
            self.power = False
            self._event('system', 'POWER_OFF', '主电源断开：灯丝关闭、隔离阀关闭、涡轮泵停转')
        return dict(on=self.power)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(on=not self.power)

    def _mains_on(self):
        v = self.vac
        if v['pumping_since'] is not None or v['sealed_at'] is not None:     # the turbo restarts; the isolation valve reopens once at speed
            if v['sealed_at'] is not None:
                v['reopen_pending'] = True
            v['turbo_on_at'] = self.t - ENG['turbo_spinup_s'] * self._turbo_speed()
            v['turbo_off_at'] = None
            if not v.get('reopen_pending'):
                v['isolation_open'] = True

    def _mains_off(self):
        v = self.vac
        if self.source['filament']:
            self._account_filament()
            self.source['filament'] = False
        if v['pumping_since'] is not None and v['sealed_at'] is None:          # automatic isolation valve closes: the analyser is sealed
            v['sealed_p0'] = self._pressure()
            v['sealed_at'] = self.t
            v['isolation_open'] = False
        v['speed_at_off'] = self._turbo_speed()
        v['turbo_off_at'] = self.t
        v['turbo_on_at'] = None

    def _settle_vacuum(self):
        """Reopen the isolation valve once the restarted turbo is at speed (the sealed analyser rejoins the pumped system)."""
        v = self.vac
        if v.get('reopen_pending') and self._mains() and v['turbo_on_at'] is not None and self.t - v['turbo_on_at'] >= ENG['turbo_spinup_s']:
            p_sealed = self._pressure()
            v['reopen_pending'] = False
            v['sealed_at'] = None
            v['isolation_open'] = True
            if p_sealed >= 0.5 * self.plant['pressure_pa']:
                v['pumping_since'] = self.t                                   # it had risen to atmosphere: restart the water outgassing
            self._event('system', 'ISOLATION_OPEN', f'涡轮泵到速，隔离阀重新打开（封闭期间压力升到{p_sealed:.2g} Pa）')

    def _cmd_vacuum_vent(self):
        self._need_power()
        if self._source_on():
            raise ActionRejected('FILAMENT_ON')
        v = self.vac
        v.update(pumping_since=None, sealed_at=None, turbo_on_at=None, turbo_off_at=None, speed_at_off=0.0, isolation_open=False, exposed_at=self.t)
        self._event('maintenance', 'VENTED', '分析器放气到大气')
        return dict(vented=True)

    def _cmd_vacuum_pump_down(self):
        self._need_power()
        v = self.vac
        if v['pumping_since'] is None:
            v.update(pumping_since=self.t, turbo_on_at=self.t, turbo_off_at=None, isolation_open=True, sealed_at=None)
            self._event('maintenance', 'PUMP_DOWN', '开始抽真空（不烘烤约12 h到工作真空）')
        return dict(pumping=True)

    def _account_filament(self):
        if self.source['filament'] and self.source.get('on_since') is not None:
            self.source['hours'] += (self.t - self.source['on_since']) / 3600.0
            self.source['on_since'] = self.t if self.source['filament'] else None

    def _cmd_source_replace_filament(self):
        self._need_plant()
        if self._vacuum_state() != 'vented':
            raise ActionRejected('NOT_VENTED')
        if self.hood < 1.0:
            raise ActionRejected('HOOD_CLOSED')
        if 'filament_burnout' in self.faults:
            raise ActionRejected('FAULT_ACTIVE')
        self.source.update(filament=False, burnt=False, hours=0.0, on_since=None)
        self._event('maintenance', 'FILAMENT_REPLACED', '灯丝已更换（累计小时清零；抽真空后可开灯丝）')
        return dict(source=self._source_view())

    def _cmd_source_set(self, filament=None, emission_a=None, acceleration_v=None):
        self._need_power()
        if filament and not self.source['filament']:
            if self._vacuum_state() != 'ready':
                raise ActionRejected('VACUUM_NOT_READY')
            if self.source['burnt'] or 'filament_burnout' in self.faults:
                raise ActionRejected('FILAMENT_FAILED')
        if acceleration_v is not None and self.magnet['species'] is not None and max(MASSES[self.magnet['species']]) > self._max_mass(acceleration_v):
            raise ActionRejected('OUT_OF_RANGE')
        self._account_filament()
        if filament is not None and filament != self.source['filament']:
            self.source['filament'] = filament
            self.source['on_since'] = self.t if filament else None
            self._event('operation', 'FILAMENT_ON' if filament else 'FILAMENT_OFF', '灯丝' + ('开' if filament else '关'))
        if emission_a is not None:
            self.source['emission_a'] = float(emission_a)
        if acceleration_v is not None:
            self.source['acceleration_v'] = float(acceleration_v)
            if self.magnet['species'] is not None:                            # field follows the voltage (same masses on the cups)
                self.magnet['field_t'] = b_for(self._centre(self.magnet['species']), acceleration_v)
                self.magnet['set_at'] = self.t
        return dict(source=self._source_view())

    def _source_view(self):
        hours = self.source['hours'] + ((self.t - self.source['on_since']) / 3600.0 if self.source['filament'] and self.source.get('on_since') is not None else 0.0)
        return dict(filament=self.source['filament'], emission_a=self.source['emission_a'], acceleration_v=self.source['acceleration_v'], filament_hours=hours,
                    filament_failed=bool(self.source['burnt'] or 'filament_burnout' in self.faults),        # open filament reported by the source supply
                    fault_active='filament_burnout' in self.faults)

    def _max_mass(self, volts):
        return (DERIVED['magnet_field_max_t'] * DOC['radius_m']) ** 2 * E_CH / (2.0 * U_KG * volts)

    def _centre(self, species):
        m = MASSES[species]
        return 0.5 * (m[0] + m[-1])

    def _cmd_magnet_set_species(self, species):
        self._need_power()
        if max(MASSES[species]) > self._max_mass(self.source['acceleration_v']) or max(MASSES[species]) > DOC['max_single_mass_u']:
            raise ActionRejected('OUT_OF_RANGE')
        if self.run is not None or self.di is not None:
            raise ActionRejected('RUN_ACTIVE')
        self.magnet.update(species=species, field_t=b_for(self._centre(species), self.source['acceleration_v']) * (1.0 + 4e-4), set_at=self.t)
        self.amp['current'] = {}
        self._event('operation', 'MAGNET_JUMP', f'磁场跳到{species}配置')
        return dict(field_t=self.magnet['field_t'], masses=list(MASSES[species]))

    def _cmd_magnet_peak_center(self):
        self._need_power()
        if self.magnet['species'] is None:
            raise ActionRejected('NO_SPECIES')
        if not self._source_on():
            raise ActionRejected('SOURCE_OFF')
        if self.run is not None or self.di is not None:
            raise ActionRejected('RUN_ACTIVE')
        b0 = b_for(self._centre(self.magnet['species']), self.source['acceleration_v'])
        offset = self._field_true() - b0
        self.magnet['field_t'] = b0
        self.magnet['set_at'] = self.t
        self._event('operation', 'PEAK_CENTER', f'峰中心完成（偏差{offset:.2e} T）')
        return dict(field_t=b0, offset_t=offset)

    def _cmd_amplifier_configure(self, mode, resistors_ohm=None):
        self._need_power()
        if mode == 'fixed' and (resistors_ohm is None or (self.magnet['species'] and len(resistors_ohm) != len(MASSES[self.magnet['species']]))):
            raise ActionRejected('INVALID_ARGUMENT')
        if self.run is not None or self.di is not None:
            raise ActionRejected('RUN_ACTIVE')
        self.amp.update(mode=mode, fixed=list(resistors_ohm) if resistors_ohm else None, current={})
        return dict(amplifier=dict(mode=mode, resistors_ohm=self.amp['fixed']))

    def _cmd_centrion_set(self, inlet=None, carrier_on=None, carrier_m3_s=None, dilution=None, sleep=None):
        self._need_power()
        if inlet is not None and self.plant['inlets'][inlet - 1] is None:
            raise ActionRejected('INLET_EMPTY')
        if carrier_on and not self._he_ok():
            raise ActionRejected('NO_HELIUM')
        if self.run is not None and inlet is not None and inlet != self.cen['inlet']:
            pass                                                              # allowed: ionOS switches inlets inside a sequence
        for k, v in (('inlet', inlet), ('carrier_on', carrier_on), ('carrier_m3_s', carrier_m3_s), ('dilution', dilution), ('sleep', sleep)):
            if v is not None:
                self.cen[k] = v
        return dict(centrion=self._cen_view())

    def _cen_view(self):
        return dict(inlet=self.cen['inlet'], inlet_type=self.plant['inlets'][self.cen['inlet'] - 1] if self.plant else None, carrier_on=self.cen['carrier_on'],
                    carrier_m3_s=self.cen['carrier_m3_s'], dilution=self.cen['dilution'], sleep=self.cen['sleep'],
                    assigned={str(k): v for k, v in sorted(self.cen['assigned'].items())})

    def _cmd_reference_assign(self, gas, deltas):
        self._need_plant()
        r = self.plant['reference_gases'][gas - 1]
        if r is None:
            raise ActionRejected('NO_REFERENCE_GAS')
        if any(k not in ISOTOPES[r['species']] for k in deltas):
            raise ActionRejected('INVALID_ARGUMENT')
        self.cen['assigned'][gas] = dict(deltas)
        return dict(gas=gas, deltas=dict(deltas))

    def _sample_deltas(self, species, sample_id=None, substance_id=None):
        if (sample_id is None) == (substance_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if sample_id is not None:
            if sample_id not in self.declared:
                raise ActionRejected('UNKNOWN_SAMPLE')
            d = dict(self.declared[sample_id]['deltas'])
        else:
            d = {k: v['delta'] for k, v in RM[substance_id]['values'].items()}
        if species in ('CO2',) and '18O' not in d:
            d['18O'] = self.plant['combustion_o2_delta18o']                   # combustion CO2 takes its oxygen from the reagent O2
        need = [k for k in ISOTOPES[species] if k not in d and not (species == 'CO2' and k == '18O')]
        if need:
            raise ActionRejected('NO_DELTA_FOR_SPECIES')
        return d

    def _cmd_sample_inject(self, inlet, species, amount_mol, sample_id=None, substance_id=None, delay_s=0.0, width_s=None):
        self._need_power()
        kind = self.plant['inlets'][inlet - 1]
        if kind is None:
            raise ActionRejected('INLET_EMPTY')
        if kind == 'dual_inlet':
            raise ActionRejected('WRONG_INLET')
        if not self.cen['carrier_on'] or self.cen['sleep']:
            raise ActionRejected('NO_CARRIER')
        d = self._sample_deltas(species, sample_id, substance_id)
        width = width_s if width_s is not None else ({'ea': 20.0, 'gc': 3.0, 'liquiface': 30.0, 'trace_gas': 8.0, 'headspace': 10.0}[kind])
        self.n_inj += 1
        arrival = self.t + delay_s
        g = dict(id=self.n_inj, kind='sample', inlet=inlet, species=species, amount=amount_mol, deltas=d, centre=arrival + 4.0 * width,
                 sigma=width / 2.355, label=sample_id or substance_id, sample_id=sample_id, substance_id=substance_id, done=0.0)
        self.in_flight.append(g)
        self.delivered.append(dict(id=g['id'], inlet=inlet, species=species, amount=amount_mol, deltas=dict(d), centre=g['centre'], sigma=g['sigma'],
                                   label=g['label'], sample_id=sample_id, substance_id=substance_id, selected=inlet == self.cen['inlet']))
        L = self.led
        L['injected_mol'][species] = L['injected_mol'].get(species, 0.0) + amount_mol
        self._event('operation', 'SAMPLE_INJECTED', f'第{inlet}进样口送入{species}样品（{(sample_id or substance_id)}，{amount_mol:.3g} mol）')
        return dict(injection=self.n_inj, arrival_s=arrival + 4.0 * width)

    def _cmd_acquisition_start(self, duration_s, reference_pulses=()):
        self._need_power()
        if self.run is not None or self.di is not None:
            raise ActionRejected('RUN_ACTIVE')
        if self.magnet['species'] is None:
            raise ActionRejected('NO_SPECIES')
        if not self._source_on():
            raise ActionRejected('SOURCE_OFF')
        if not self.cen['carrier_on'] or self.cen['sleep']:
            raise ActionRejected('NO_CARRIER')
        for p in reference_pulses:
            r = self.plant['reference_gases'][p['gas'] - 1]
            if r is None:
                raise ActionRejected('NO_REFERENCE_GAS')
            if r['species'] != self.magnet['species']:
                raise ActionRejected('WRONG_SPECIES')
            if p['start_s'] + p['duration_s'] > duration_s:
                raise ActionRejected('INVALID_ARGUMENT')
        self.n_run += 1
        if self.amp['mode'] == 'auto':
            self.amp['current'] = {}                                          # auto ranging restarts from the configuration's default resistors
        t0 = self.t_grid if abs(self.t - self.t_grid) < 1e-9 else round(self.t_grid + DT, 9)   # on the 0.1 s tick grid (as dual_inlet.start)
        t_end = round(t0 + math.ceil(duration_s / DT - 1e-9) * DT, 9)        # the run ends on the 0.1 s grid (results independent of the host's step split)
        self.run = dict(id=self.n_run, t0=t0, t_end=t_end, species=self.magnet['species'], masses=list(MASSES[self.magnet['species']]),
                        pulses=[dict(p, t_on=t0 + p['start_s'], t_off=t0 + p['start_s'] + p['duration_s'], intensity_a=p.get('intensity_a', ENG['reference_pulse_a']))
                                for p in reference_pulses],
                        times=[], v=[], r=[], major_i_true=[], i2sq=[])
        for p in self.run['pulses']:                                          # the pulse gas is drawn when it is injected
            self.in_flight.append(dict(id=None, kind='reference', gas=p['gas'], species=self.run['species'], t_on=p['t_on'], t_off=p['t_off'],
                                       intensity=p['intensity_a'], deltas=dict(self.plant['reference_gases'][p['gas'] - 1]['deltas']), done=0.0))
        self._event('operation', 'RUN_START', f'采集{self.n_run}开始（{duration_s:.0f} s，{len(reference_pulses)}个参比脉冲）')
        return dict(run=self.n_run, ends_s=t_end)

    def _cmd_acquisition_stop(self):
        self._need_plant()
        if self.di is not None:                                               # dual inlet: the completed blocks are evaluated, the bellows pumped out
            rid = self.di['id']
            self._finish_di()
            return dict(run=rid)
        if self.run is None:
            return dict(run=None)
        rid = self.run['id']
        self._finish_run()
        return dict(run=rid)

    def _cmd_dual_inlet_start(self, species, sample_bar_ul, reference_gas, sample_id=None, substance_id=None, cycles=8, integration_s=20.0):
        self._need_power()
        if 'dual_inlet' not in self.plant['inlets']:
            raise ActionRejected('NO_DUAL_INLET')
        if self.run is not None or self.di is not None:
            raise ActionRejected('RUN_ACTIVE')
        if self.magnet['species'] != species:
            raise ActionRejected('WRONG_SPECIES')
        if not self._source_on():
            raise ActionRejected('SOURCE_OFF')
        r = self.plant['reference_gases'][reference_gas - 1]
        if r is None or r['species'] != species:
            raise ActionRejected('NO_REFERENCE_GAS')
        if reference_gas not in self.cen['assigned']:
            raise ActionRejected('REFERENCE_UNASSIGNED')
        d = self._sample_deltas(species, sample_id, substance_id)
        n = sample_bar_ul * 1e5 * 1e-9 / (R_GAS * self.plant['ambient_k'])
        rt = R_GAS * self.plant['ambient_k']
        # bellows compressed until the major beam reaches the target intensity (viscous capillary: current ~ p^2), both sides balanced
        tot = sum(isotopologues(species, ratios_from_deltas(species, d)).values())
        k_cur = ENG['di_capillary_m3_s'] / 1e5 / rt * N_A * self._ions_per_molecule(species, 'di') * E_CH / tot   # major current = k_cur p^2
        p_target = math.sqrt(ENG['di_target_major_a'] / k_cur) if k_cur > 0 else 0.0
        v_s = max(n * rt / max(p_target, 1.0), ENG['di_bellows_min_m3'])
        n_r = (n * rt / v_s) * v_s / rt
        self.n_run += 1
        if self.amp['mode'] == 'auto':
            self.amp['current'] = {}
        # the blocks run on the 0.1 s tick grid: a start between grid points begins at the next grid point (a tick that began before the start
        # would otherwise be counted as an extra block)
        t0 = self.t_grid if abs(self.t - self.t_grid) < 1e-9 else round(self.t_grid + DT, 9)
        nper = 2 * cycles + 1                                                 # ref, sample, ref, ... , ref
        self.di = dict(id=self.n_run, t0=t0, t_end=round(t0 + math.ceil(nper * (integration_s + ENG['di_switch_s']) / DT - 1e-9) * DT, 9), species=species, cycles=cycles, integration_s=integration_s,
                       sample=dict(n=n, deltas=d, label=sample_id or substance_id, sample_id=sample_id, substance_id=substance_id), ref_gas=reference_gas,
                       ref=dict(n=n_r, deltas=dict(r['deltas'])), sums=[], cur=None, v=dict(sample=v_s, ref=v_s))
        self.led['di_loaded_mol'] = self.led.get('di_loaded_mol', 0.0) + n + n_r
        self._event('operation', 'DI_START', f'双路进样{self.n_run}开始（{cycles}周期）')
        return dict(run=self.n_run, ends_s=self.di['t_end'])

    def _cmd_h3_determine(self, run):
        self._need_plant()
        pts = [(p['i2'], p['ratios']['3/2']) for p in self.results if p['run'] == run and p['type'] == 'reference' and p['species'] == 'H2'
               and p.get('i2') and p['ratios'].get('3/2') is not None]
        if len(pts) < 3 or max(x for x, _ in pts) < 1.5 * min(x for x, _ in pts):
            raise ActionRejected('NEED_H2_PULSES')
        # m/z 3 / m/z 2 = 2 R(D/H) + K I2: the slope over the mean m/z 2 current of each pulse (A^-1); the processing then removes K * integral(I2^2)
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        k = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        # the run's ratios were corrected with the factor in force when it was processed: the residual slope adds to that factor (a repeat on the same
        # run gives the same factor; a later run processed with the new factor shows no residual slope)
        self.h3_k = max(self.run_h3.get(run, 0.0) + k, 0.0)
        self._event('operation', 'H3_FACTOR', f'H₃⁺因子{self.h3_k * 1e-3:.2f} ppm/nA')
        return dict(factor_ppm_na=self.h3_k * 1e-3, points=len(pts))

    def _cmd_calibration_normalize(self, isotope, standards):
        self._need_plant()
        xs, ys = [], []
        for s in standards:
            p = next((q for q in self.results if q['peak'] == s['peak']), None)
            if p is None or p['type'] == 'reference' or isotope not in (p.get('delta_ref') or {}):
                raise ActionRejected('UNKNOWN_PEAK')
            v = RM[s['substance_id']]['values'].get(isotope)
            if v is None or v['kind'] in ('approximate', 'interval_midpoint'):
                raise ActionRejected('NOT_A_STANDARD')
            xs.append(p['delta_ref'][isotope])
            ys.append(v['delta'])
        if max(ys) - min(ys) < 0.005:
            raise ActionRejected('STANDARDS_TOO_CLOSE')
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0.0:
            raise ActionRejected('STANDARDS_TOO_CLOSE')
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        icpt = my - slope * mx
        res = [y - (slope * x + icpt) for x, y in zip(xs, ys)]
        self.norm[isotope] = dict(slope=slope, intercept=icpt, standards=[dict(s) for s in standards], at_s=self.t)
        for p in self.results:
            self._apply_norm(p)
        return dict(isotope=isotope, slope=slope, intercept=icpt, residuals=res)

    def _apply_norm(self, p):
        if p.get('delta_ref'):
            p['delta_normalized'] = {k: (self.norm[k]['slope'] * v + self.norm[k]['intercept']) for k, v in p['delta_ref'].items() if k in self.norm}

    def _cmd_hood_set(self, angle_rad):
        self._need_plant()
        self.hood = float(angle_rad)
        return dict(angle_rad=self.hood)

    def _cmd_results_read(self, since_peak=0):
        return dict(peaks=[self._public_peak(p) for p in self.results if p['peak'] > since_peak][-MAX_RESULTS:])

    def _public_peak(self, p):
        keys = ('peak', 'run', 'type', 'species', 'start_s', 'end_s', 'apex_s', 'amplitude_v', 'area_vs', 'ratios', 'delta_ref', 'delta_normalized',
                'reference_peak', 'saturated', 'internal_se', 'cycle_sd', 'cycles', 'major_current_a', 'label')
        return {k: copy.deepcopy(p[k]) for k in keys if k in p and not (k == 'label' and p['type'] == 'sample' and p.get('sample_id') is not None)}

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        self._need_plant()
        r = self.read('evaluator')
        return dict(process=r['truth.process'], samples=r['truth.samples'], faults=r['truth.faults'])

    def _cmd_ledger_read(self):
        return dict(ledger=self._ledger())

    def _cmd_fault_inject(self, fault, active, gas=None):
        self._need_plant()
        if fault == 'reference_gas_empty':
            if gas is None:
                raise ActionRejected('GAS_REQUIRED')
            (self.fault_gas.add if active else self.fault_gas.discard)(gas)
        was = self._mains()
        (self.faults.add if active else self.faults.discard)(fault)
        if was and not self._mains():
            self._mains_off()
            self._event('alarm', 'POWER_FAILURE', '停电：隔离阀关闭保护离子光学')
        elif not was and self._mains():
            self._mains_on()
        self._he_check()
        if fault == 'filament_burnout' and active and self.source['filament']:
            self._account_filament()
            self.source.update(filament=False, burnt=True, on_since=None)
            self._event('alarm', 'FILAMENT_BURNT', '灯丝烧断')
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}' + ('：注入' if active else '：清除'))
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- physics
    def _busy(self):
        return self.run is not None or self.di is not None or bool(self.in_flight)

    def step(self, dt_s):
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = round(self.t + float(dt_s), 9)
        if self.plant is None:
            self.t = t_end
            self.t_polled = t_end
            self.t_grid = math.floor(t_end / DT + 1e-6) * DT
            self.tick = int(round(self.t_grid / DT))
            return self.t
        while self.t_grid + DT <= t_end + 1e-9:
            if not self._busy():                                              # nothing is measured: closed-form states, jump the grid exactly
                n = int(math.floor((t_end - self.t_grid) / DT + 1e-6))                 # tolerance: (t_end - t_grid) / DT carries float error at long horizons
                t_ev = self._next_quiet_event(self.t_grid, self.t_grid + n * DT)
                m = n if t_ev is None else min(n, max(1, int(math.ceil((t_ev - self.t_grid) / DT - 1e-9))))
                self._jump(m * DT)                                            # a trip, burnout, empty helium or valve reopening lands on its grid point
                self.tick += m
                self.t_grid = round(self.tick * DT, 9)
                if m == n:
                    break
                continue
            self._tick(DT)
            self.tick += 1
            self.t_grid = round(self.tick * DT, 9)
            self._settle_vacuum()
            self._checks()
        self.t = t_end
        self.t_polled = t_end
        self._checks(grid=False)                                              # off-grid end: trip, burnout and run ends wait for the next grid point
        return self.t

    def _next_quiet_event(self, ta, tb):
        """Earliest time in (ta, tb] at which a closed-form quiet interval changes discretely: vacuum trip of the lit source, filament end of
        life, helium running out, isolation valve reopening.  None when nothing happens."""
        ev = []
        if self._source_on():
            thr = 4.0 * ENG['vacuum_ready_pa']
            if self._pressure(ta + 1e-9) > thr:                                 # already above (a leak just appeared): trip at the next grid point
                ev.append(ta + 1e-9)
            elif self._pressure(tb) > thr or any(self._pressure(ta + (tb - ta) * f) > thr for f in (1e-6, 1e-4, 1e-2, 0.1, 0.25, 0.5, 0.75)):
                pts = sorted({ta + (tb - ta) * f for f in [1e-9, 1e-6, 1e-4, 1e-3, 1e-2] + [k / 64.0 for k in range(1, 65)]})
                lo, hi = ta, next((t for t in pts if self._pressure(t) > thr), tb)
                for _ in range(60):
                    mid = 0.5 * (lo + hi)
                    lo, hi = (lo, mid) if self._pressure(mid) > thr else (mid, hi)
                ev.append(hi)
        if self.source['filament'] and self.source.get('on_since') is not None and not self.source['burnt']:
            ev.append(self.source['on_since'] + (ENG['filament_life_h'] - self.source['hours']) * 3600.0)
        if self._mains() and self.cen['carrier_on'] and self._he_ok():
            flow = self.cen['carrier_m3_s'] * (0.1 if self.cen['sleep'] else 1.0)
            ev.append(ta + (self._he_available() - self.led['he_used_m3']) / flow)
        v = self.vac
        if v.get('reopen_pending') and self._mains() and v['turbo_on_at'] is not None:
            ev.append(v['turbo_on_at'] + ENG['turbo_spinup_s'])
        ev = [t for t in ev if ta < t <= tb + 1e-9]
        return min(ev) if ev else None

    def _jump(self, dt):
        """Quiet interval: helium use and the vacuum are closed-form; the pressure check runs at the end."""
        t1 = self.t_grid + dt
        self._he_use(self.t_grid, t1)
        self.t = t1
        self._settle_vacuum()
        self._checks()

    def _he_use(self, ta, tb):
        """Helium used, in closed form over segments of constant flow (used = used at the segment start + flow x elapsed), so the sum does not
        depend on how the time is partitioned."""
        flow = (self.cen['carrier_m3_s'] * (0.1 if self.cen['sleep'] else 1.0)) if (self._mains() and self.cen['carrier_on'] and self._he_ok()) else 0.0
        seg = self.he_seg
        if seg is None or seg[2] != flow or abs(seg[0] + seg[3] - ta) > 1e-6:
            seg = self.he_seg = [ta, self.led['he_used_m3'], flow, 0.0]       # start, used at start, flow, elapsed
        seg[3] = round(tb - seg[0], 9)
        t_empty = None
        if flow > 0.0:
            avail = self._he_available()
            if seg[1] + flow * seg[3] >= avail:
                t_empty = seg[0] + (avail - seg[1]) / flow
            self.led['he_used_m3'] = min(seg[1] + flow * seg[3], avail)
        self._he_check(t_empty)

    def _he_check(self, t_event=None):
        if self.cen['carrier_on'] and not self._he_ok():
            self.cen['carrier_on'] = False
            t_now, self.t = self.t, (self.t if t_event is None else max(t_event, self.t_polled))   # the time the cylinder ran empty (not before the last poll)
            self._event('alarm', 'HELIUM_EMPTY', '氦气耗尽：载气停止')
            self.t = t_now

    def _checks(self, grid=True):
        if grid and self._source_on() and self._pressure() > 4.0 * ENG['vacuum_ready_pa']:
            self._account_filament()
            self.source.update(filament=False, on_since=None)
            self._event('alarm', 'VACUUM_TRIP', '分析器压力过高：灯丝自动关闭')
        if grid and self.source['filament'] and self._source_view()['filament_hours'] >= ENG['filament_life_h'] - 1e-9 and not self.source['burnt']:
            self._account_filament()
            self.source.update(filament=False, burnt=True, on_since=None)
            self._event('alarm', 'FILAMENT_BURNT', '灯丝到寿命烧断')
        if grid and self.run is not None and self.t >= self.run['t_end'] - 1e-9:
            self._finish_run()
        if grid and self.di is not None and self.t >= self.di['t_end'] - 1e-9:
            self._finish_di()

    def _pulse_flow(self, g, ta, tb):
        """Molecules of a gas pulse reaching the source between ta and tb, and the moles injected / vented over the interval."""
        if g['kind'] == 'sample':
            n = g['amount'] * (_phi((tb - g['centre']) / g['sigma']) - _phi((ta - g['centre']) / g['sigma']))
            return n
        # reference pulse: a rectangular flow (rise time) whose source current equals the requested major intensity
        rise = ENG['pulse_rise_s']

        def area(t):                                                       # integral of the rise-limited rectangular profile from t_on (unit height)
            def ramp(x):
                return 0.0 if x <= 0 else (x * x / (2 * rise) if x < rise else x - rise / 2)
            return ramp(t - g['t_on']) - ramp(t - g['t_off'])
        return area(tb) - area(ta)

    def _tick(self, h):
        L = self.led
        ta, tb = self.t_grid, self.t_grid + h
        self.t = ta
        self._he_use(ta, tb)
        src_on = self._source_on()
        split = ENG['open_split_source_fraction'] * (1.0 - self.cen['dilution'])
        carrier = self._mains() and self.cen['carrier_on'] and not self.cen['sleep'] and self._he_ok()
        currents = {}                                                         # mass -> true current (A)
        species_run = self.magnet['species']
        i_major_by_species = {}
        for g in list(self.in_flight):
            sp = g['species']
            if g['kind'] == 'sample':
                n = self._pulse_flow(g, ta, tb)
                g['done'] += n
                if not carrier or self.plant['inlets'][g['inlet'] - 1] is None or g['inlet'] != self.cen['inlet']:
                    n_src = 0.0                                               # a non-selected inlet vents through centrION
                else:
                    n_src = n * split
                ion_mol = n_src * (self._ions_per_molecule(sp, 'cf') if src_on else 0.0)
                L['vented_mol'][sp] = L['vented_mol'].get(sp, 0.0) + n - n_src
                L['source_mol'][sp] = L['source_mol'].get(sp, 0.0) + n_src - ion_mol
                L['ionised_mol'][sp] = L['ionised_mol'].get(sp, 0.0) + ion_mol
                if g['done'] >= g['amount'] * (1.0 - 1e-12) or tb > g['centre'] + 8.0 * g['sigma']:
                    rest = g['amount'] - g['done']
                    if rest > 0.0:
                        L['vented_mol'][sp] = L['vented_mol'].get(sp, 0.0) + rest
                    g['done'] = g['amount']
                    self.in_flight.remove(g)
                ion_rate = ion_mol * N_A / h
            else:
                frac = self._pulse_flow(g, ta, tb) / h                         # 0..1 of the pulse plateau
                gas_ok = g['gas'] not in self.fault_gas and self.ref_left[g['gas'] - 1] is not None and self.ref_left[g['gas'] - 1] > 0.0
                eff = self._ions_per_molecule(sp, 'cf')
                tot_ref = sum(isotopologues(sp, ratios_from_deltas(sp, g['deltas'])).values())
                ion_rate = (frac * g['intensity'] / E_CH * tot_ref) if (gas_ok and src_on and carrier) else 0.0
                mol_src = ion_rate / eff / N_A * h if eff > 0 else 0.0
                mol_total = mol_src / ENG['open_split_source_fraction'] if mol_src > 0.0 else 0.0
                if gas_ok and frac > 0.0:
                    self.ref_left[g['gas'] - 1] = max(self.ref_left[g['gas'] - 1] - mol_total, 0.0)
                L['ref_injected_mol'][sp] = L['ref_injected_mol'].get(sp, 0.0) + mol_total
                L['ref_vented_mol'][sp] = L['ref_vented_mol'].get(sp, 0.0) + mol_total - mol_src
                L['ref_source_mol'][sp] = L['ref_source_mol'].get(sp, 0.0) + mol_src - ion_rate * h / N_A
                L['ref_ionised_mol'][sp] = L['ref_ionised_mol'].get(sp, 0.0) + ion_rate * h / N_A
                if tb >= g['t_off'] + 2 * ENG['pulse_rise_s']:
                    self.in_flight.remove(g)
            if ion_rate > 0.0:
                iso = isotopologues(sp, ratios_from_deltas(sp, g['deltas']))
                tot = sum(iso.values())
                i_major = ion_rate * E_CH / tot
                for m, a in iso.items():
                    lin = ENG['linearity_permil_na'].get(f'{sp.lower()}_{m}', 0.0)          # ratio linearity with the major beam (permil per nA)
                    currents[m] = currents.get(m, 0.0) + i_major * a * (1.0 + lin * 1e-3 * i_major / 1e-9)
                i_major_by_species[sp] = i_major_by_species.get(sp, 0.0) + i_major
        if self.di is not None and self._mains():
            self._di_tick(ta, tb, h, currents, src_on)
        L['charge_ionised_c'] += sum(currents.values()) * h
        # air / water background (present whenever the source runs)
        if src_on:
            for m, i in self._background().items():
                currents[m] = currents.get(m, 0.0) + i
            if 2 in currents:
                currents[3] = currents.get(3, 0.0) + ENG['h3_factor_ppm_na'] * 1e3 * currents[2] ** 2      # H3+ = K I2^2 (K in A^-1)
        self.beams = dict(currents)
        if self.run is not None and species_run is not None:
            self._record(currents, h, i_major_by_species.get(species_run, 0.0))
        self.t = tb

    def _di_tick(self, ta, tb, h, currents, src_on):
        """Dual inlet: change-over between the reference and sample bellows; viscous flow from the bellows (pressure falls with use)."""
        d = self.di
        if ta < d['t0'] - 1e-9:                                              # before the first block (start between grid points): bellows still closed
            return
        per = d['integration_s'] + ENG['di_switch_s']
        k = int((ta - d['t0']) // per)
        side = 'ref' if k % 2 == 0 else 'sample'
        within = (ta - d['t0']) - k * per
        gas = d[side]
        p = gas['n'] * R_GAS * self.plant['ambient_k'] / d['v'][side]
        flow_mol = ENG['di_capillary_m3_s'] * (p / 1e5) * p / (R_GAS * self.plant['ambient_k']) * h
        flow_mol = min(flow_mol, gas['n'])
        gas['n'] -= flow_mol
        self.led['di_consumed_mol'] += flow_mol
        if not src_on:
            self.led['di_source_mol'] += flow_mol
            return
        ion_mol = flow_mol * self._ions_per_molecule(d['species'], 'di')
        self.led['di_source_mol'] += flow_mol - ion_mol
        self.led['di_ionised_mol'] += ion_mol
        iso = isotopologues(d['species'], ratios_from_deltas(d['species'], gas['deltas']))
        tot = sum(iso.values())
        rate = ion_mol * N_A / h * E_CH
        for m, a in iso.items():
            currents[m] = currents.get(m, 0.0) + rate * a / tot
        if within >= ENG['di_switch_s']:                                     # integrate after the change-over settling time
            if d['cur'] is None or d['cur']['k'] != k:
                if d['cur'] is not None:
                    d['sums'].append(d['cur'])
                d['cur'] = dict(k=k, side=side, s={})
            meas = self._measure(currents, h)
            for m, v in meas['i'].items():
                d['cur']['s'][m] = d['cur']['s'].get(m, 0.0) + v * h

    def _auto_resistor(self, idx, i_true, cur):
        """Automatic resistor switching: start from the configuration's default resistor of the cup and step down only when the amplifier would
        saturate (95 V; brochure: auto-resistor switching for enriched samples); never step up inside a configuration."""
        opts = sorted(ENG['resistors_ohm'])
        if cur is None:
            cur = ENG['default_resistors_ohm'][self.magnet['species']][idx]
        while abs(i_true) * cur > 95.0 and cur > opts[0]:
            cur = max(r for r in opts if r < cur)
        return cur

    def _resistor(self, idx, i_true, commit=True):
        if self.amp['mode'] == 'fixed' and self.amp['fixed']:
            return self.amp['fixed'][min(idx, len(self.amp['fixed']) - 1)]
        cur = self._auto_resistor(idx, i_true, self.amp['current'].get(idx))
        if commit:
            self.amp['current'][idx] = cur
        return cur

    def _cup_currents(self, currents):
        """True currents arriving on the configured cups (transmission on the flat-topped cups and the abundance-sensitivity tails)."""
        sp = self.magnet['species']
        centre = self._centre(sp)
        out = {}
        for m in MASSES[sp]:
            i = currents.get(m, 0.0) * self._transmission(m, centre)
            for mm, ii in currents.items():
                if abs(mm - m) == 1:
                    i += ii * ENG['abundance_sensitivity'] * self._transmission(mm, centre)
            out[m] = i
        return out

    def _measure(self, currents, h):
        """Measured cups: shot and Johnson noise, V = I R + offset, 100 V range; returns currents, volts and the resistors used."""
        masses = MASSES[self.magnet['species']]
        cup = self._cup_currents(currents)
        out_i, out_v, out_r, sat = {}, {}, {}, False
        for idx, m in enumerate(masses):
            i = cup[m]
            r = self._resistor(idx, i)
            n_ions = max(i, 0.0) * h / E_CH
            shot = math.sqrt(n_ions) * E_CH / h * _gauss(self.seed, self.tick, 10 + idx)
            john = math.sqrt(4.0 * K_B * ENG['amplifier_temperature_k'] / r / (2.0 * h)) * _gauss(self.seed, self.tick, 30 + idx)
            v = (i + shot + john) * r + ENG['baseline_offset_v']
            if v > DOC['amplifier_range_v'][1]:
                v = DOC['amplifier_range_v'][1]
                sat = True
            out_v[m], out_r[m] = v, r
            out_i[m] = (v - ENG['baseline_offset_v']) / r
        return dict(i=out_i, v=out_v, r=out_r, sat=sat)

    def _background(self):
        cur = {}
        if self._source_on():
            leak = 1.0 + (ENG['fault_leak_pa_m3_s'] / ENG['air_leak_pa_m3_s'] if 'vacuum_leak' in self.faults else 0.0)
            for key, i in ENG['air_background_a'].items():
                cur[int(key[1:])] = i * leak
        return cur

    def _display(self):
        """Beam display (V) without noise and without changing the amplifier state (pure)."""
        if self.magnet['species'] is None or not self._source_on():
            return {}
        cur = self.beams if self.beams else self._background()
        cup = self._cup_currents(cur)
        out = {}
        for idx, m in enumerate(MASSES[self.magnet['species']]):
            r = self._resistor(idx, cup[m], commit=False)
            out[str(m)] = round(min(cup[m] * r + ENG['baseline_offset_v'], DOC['amplifier_range_v'][1]), 4)
        return out

    def _record(self, currents, h, i_major_true):
        run = self.run
        meas = self._measure(currents, h)
        run['times'].append(round(self.t_grid + h, 9))
        run['v'].append(meas['v'])
        run['r'].append(meas['r'])
        run['major_i_true'].append(i_major_true)
        run.setdefault('sat', []).append(meas['sat'])
        self.led['charge_c'] += sum(meas['i'].values()) * h

    # ---- processing
    def _finish_run(self):
        run = self.run
        self.run = None
        self.run_h3[run['id']] = self.h3_k
        for g in list(self.in_flight):                                        # pulses not yet delivered are cancelled with the run
            if g['kind'] == 'reference' and g['t_on'] >= self.t:
                self.in_flight.remove(g)
        sp = run['species']
        masses = run['masses']
        major = masses[0]
        ts, vs, rs = run['times'], run['v'], run['r']
        if len(ts) < 20:
            self._event('operation', 'RUN_END', f'采集{run["id"]}结束（数据不足）')
            return
        cur = [{m: (vs[k][m] - ENG['baseline_offset_v']) / rs[k][m] for m in masses} for k in range(len(ts))]
        ymaj = [c[major] for c in cur]
        srt = sorted(ymaj)
        low = srt[:max(10, len(srt) * 3 // 10)]                              # baseline from the lowest 30 % of the trace (peaks take less)
        base = low[len(low) // 2]
        noise = max(1e-15, (sum((y - base) ** 2 for y in low) / len(low)) ** 0.5)
        thr = base + max(20.0 * noise, 2e-12)
        segs, start = [], None
        for k, y in enumerate(ymaj):
            if y > thr and start is None:
                start = k
            elif y <= thr and start is not None:
                if k - start >= 10:
                    segs.append((start, k))
                start = None
        if start is not None and len(ymaj) - start >= 10:
            segs.append((start, len(ymaj)))
        last_ref = None
        for a, b in segs:
            a0, b0 = max(0, a - 20), min(len(ymaj), b + 30)                   # widen to the tails
            pre = list(range(max(0, a0 - 50), a0)) or list(range(b0, min(len(ymaj), b0 + 50))) or [a0]
            bl = {m: sum(cur[k][m] for k in pre) / len(pre) for m in masses}
            area = {m: sum(cur[k][m] - bl[m] for k in range(a0, b0)) * DT for m in masses}
            amp_k = max(range(a0, b0), key=lambda k: ymaj[k])
            t_mid = ts[amp_k]
            pulse = next((p for p in run['pulses'] if p['t_on'] - 1.0 <= t_mid <= p['t_off'] + 3.0), None)
            self.n_peak += 1
            jitter = ENG['ratio_flicker'] * _gauss(self.seed, 1_000_000 + self.n_peak, 7)          # source ratio instability between peaks
            ratios = {}
            for m in masses[1:]:
                num = area[m]
                if sp == 'H2' and m == 3:
                    num -= self.h3_k * sum((cur[k][2] - bl[2]) ** 2 for k in range(a0, b0)) * DT        # operator's H3+ correction
                ratios[f'{m}/{major}'] = num / area[major] * (1.0 + jitter) if area[major] > 0 else None
            pk = dict(peak=self.n_peak, run=run['id'], type='reference' if pulse else 'sample', species=sp, start_s=ts[a0], end_s=ts[b0 - 1], apex_s=t_mid,
                      amplitude_v=vs[amp_k][major], area_vs=sum((cur[k][major] - bl[major]) * rs[k][major] for k in range(a0, b0)) * DT, ratios=ratios,
                      saturated=any(run['sat'][k] for k in range(a0, b0)),
                      i2=(sum((cur[k][2] - bl[2]) ** 2 for k in range(a0, b0)) * DT / area[major]) if sp == 'H2' and area[major] > 0 else None)   # intensity-weighted m/z 2 current
            if pulse:
                pk['gas'] = pulse['gas']
                last_ref = pk
            else:
                truth = self._truth_of_peak(run, t_mid)
                pk['truth'] = truth
                if truth:
                    pk['sample_id'] = truth.get('sample_id')
                    pk['label'] = truth.get('label')
                self._delta_vs_ref(pk, last_ref)
            self.results.append(pk)
        if len(self.results) > 10 * MAX_RESULTS:
            del self.results[:len(self.results) - 10 * MAX_RESULTS]
        self._event('operation', 'RUN_END', f'采集{run["id"]}结束：识别{len(segs)}个峰')

    def _truth_of_peak(self, run, t_mid):
        best = None
        for g in self.delivered:
            if g['species'] == run['species'] and abs(g['centre'] - t_mid) < 4.0 * g['sigma'] + 5.0:
                if best is None or abs(g['centre'] - t_mid) < abs(best['centre'] - t_mid):
                    best = g
        return None if best is None else dict(label=best['label'], sample_id=best['sample_id'], deltas=dict(best['deltas']), amount_mol=best['amount'])

    def _delta_vs_ref(self, pk, ref):
        sp = pk['species']
        if ref is None or ref.get('gas') not in self.cen['assigned']:
            pk['delta_ref'] = None
            pk['reference_peak'] = None if ref is None else ref['peak']
            return
        assigned = self.cen['assigned'][ref['gas']]
        rr_ref = isotopologues(sp, ratios_from_deltas(sp, assigned))          # absolute ratios the operator assigns to the reference gas
        rr = {}
        masses = MASSES[sp]
        major = masses[0]
        for m in masses[1:]:
            key = f'{m}/{major}'
            if pk['ratios'].get(key) is None or not ref['ratios'].get(key) or pk['ratios'][key] <= 0 or ref['ratios'][key] <= 0:
                pk['delta_ref'] = None                                        # no beam on a cup (e.g. drifted off): no delta
                return
            rr[m] = pk['ratios'][key] / ref['ratios'][key] * rr_ref[m]
        rr[major] = 1.0
        dl = deltas_from_ratios(sp, rr)
        pk['delta_ref'] = None if dl is None else dict(dl)
        if dl is None:
            return
        pk['reference_peak'] = ref['peak']
        self._apply_norm(pk)

    def _finish_di(self):
        d = self.di
        self.di = None
        self.led['di_evacuated_mol'] = self.led.get('di_evacuated_mol', 0.0) + d['sample']['n'] + d['ref']['n']   # bellows pumped out after the measurement
        if d['cur'] is not None:
            d['sums'].append(d['cur'])
        sp = d['species']
        masses = MASSES[sp]
        major = masses[0]
        refs = [c for c in d['sums'] if c['side'] == 'ref']
        smps = [c for c in d['sums'] if c['side'] == 'sample']
        cyc = []
        for s in smps:                                                      # each sample block against the mean of its neighbouring reference blocks
            nb = [r for r in refs if abs(r['k'] - s['k']) == 1]
            i_min = DI_MIN_MAJOR_A * d['integration_s']                       # integrated major beam (A s) of a usable block
            if not nb or s['s'].get(major, 0.0) < i_min or any(r['s'].get(major, 0.0) < i_min for r in nb):
                continue
            rr = {}
            ok = True
            for m in masses[1:]:
                rs = s['s'][m] / s['s'][major]
                rref = sum(r['s'][m] / r['s'][major] for r in nb) / len(nb)
                if rref <= 0 or rs <= 0 or not all(r['s'].get(major, 0.0) > 0 for r in nb):   # beams off the cups: block not usable
                    ok = False
                    break
                rr[m] = rs / rref
            if ok:
                cyc.append(rr)
        self.n_peak += 1
        pk = dict(peak=self.n_peak, run=d['id'], type='dual_inlet', species=sp, start_s=d['t0'], end_s=self.t, apex_s=None, amplitude_v=None, area_vs=None,
                  ratios={}, saturated=False, cycles=len(cyc), label=d['sample']['label'],
                  major_current_a=(sum(c['s'].get(major, 0.0) for c in smps) / (len(smps) * d['integration_s']) if smps else 0.0), sample_id=d['sample']['sample_id'],
                  truth=dict(label=d['sample']['label'], sample_id=d['sample']['sample_id'], deltas=dict(d['sample']['deltas'])))
        assigned = self.cen['assigned'][d['ref_gas']]
        rr_ref = isotopologues(sp, ratios_from_deltas(sp, assigned))
        per_cycle = []
        for c in cyc:
            rr = {m: c[m] * rr_ref[m] for m in masses[1:]}
            rr[major] = 1.0
            dl = deltas_from_ratios(sp, rr)
            if dl is not None:
                per_cycle.append(dl)
        pk['cycles'] = len(per_cycle)
        if per_cycle:
            keys = per_cycle[0].keys()
            mean = {k: sum(p[k] for p in per_cycle) / len(per_cycle) for k in keys}
            csd = {k: (math.sqrt(sum((p[k] - mean[k]) ** 2 for p in per_cycle) / (len(per_cycle) - 1)) if len(per_cycle) > 1 else None) for k in keys}
            pk['delta_ref'] = mean
            pk['cycle_sd'] = csd                                             # standard deviation of the cycle deltas (1 sigma)
            pk['internal_se'] = {k: (v / math.sqrt(len(per_cycle)) if v is not None else None) for k, v in csd.items()}   # standard error of the mean
            # (the brochure's dual-inlet internal precision is read as 2 x this standard error)
            pk['ratios'] = {f'{m}/{major}': sum(c[m] for c in cyc) / len(cyc) for m in masses[1:]}
            self._apply_norm(pk)
        else:
            pk['delta_ref'] = None
        self.results.append(pk)
        self._event('operation', 'DI_END', f'双路进样{d["id"]}结束：{pk["cycles"]}个有效周期')

    # ---- ledger / read
    def _ledger(self):
        if self.led is None:
            return {}
        L = copy.deepcopy(self.led)
        L['in_flight_mol'] = {}
        for g in self.in_flight:
            if g['kind'] == 'sample':
                L['in_flight_mol'][g['species']] = L['in_flight_mol'].get(g['species'], 0.0) + g['amount'] - g['done']
        L['di_left_mol'] = (self.di['sample']['n'] + self.di['ref']['n']) if self.di else 0.0
        L.setdefault('di_evacuated_mol', 0.0)
        L.setdefault('di_loaded_mol', 0.0)
        L['sign_convention_zh'] = ('样品：injected = vented（开口分流排空与未选进样口）+ source（进源未电离，被泵抽走）+ ionised + in_flight；参比气同理；'
                                   '电荷：charge_c为各杯测到的电荷（含噪声与本底），charge_ionised_c为样品与参比电离产生的电荷；氦：he_used_m3为载气用量（标准状态体积）')
        return L

    def _status(self):
        """Colour LED: off without mains; red on a failed filament, no helium (empty or exhausted), a vacuum that is not ready long after the
        pump-down began, or a power failure the analyser has not recovered from; green when ready with the source on; amber otherwise."""
        if not self._mains():
            return 'off'
        v = self.vac
        slow = self._vacuum_state() == 'pumping' and v['pumping_since'] is not None and self.t - v['pumping_since'] > ENG['pump_ready_alarm_s']
        if self.source['burnt'] or 'filament_burnout' in self.faults or not self._he_ok() or slow:
            return 'red'
        if self._vacuum_state() == 'ready' and self._source_on():
            return 'green'
        return 'amber'

    def read(self, visibility='agent'):
        pc = self.plant is not None
        p = self._pressure() if pc else None
        vstate = self._vacuum_state() if pc else 'off'
        beams = self._display() if pc else {}
        out = {'clock.time_s': self.t, 'plant.configured': pc, 'power.on': self.power, 'status.led': self._status() if pc else 'off',
               'vacuum.pressure_pa': (float(f'{p:.2g}') if (pc and self._mains() and vstate in ('ready', 'pumping') and p < 0.5 * self.plant['pressure_pa']) else None),
               'vacuum.state': vstate, 'source.state': self._source_view() if pc else {},
               'magnet.state': dict(species=self.magnet['species'], field_t=self.magnet['field_t'], masses=MASSES.get(self.magnet['species'], [])),
               'amplifier.state': dict(mode=self.amp['mode'], resistors_ohm={str(MASSES[self.magnet['species']][k]): v for k, v in self.amp['current'].items()}
                                       if self.magnet['species'] else {}),
               'beams.v': beams, 'centrion.state': self._cen_view() if pc else {},
               'acquisition.state': (dict(run=self.run['id'], mode='continuous_flow', started_s=self.run['t0'], ends_s=self.run['t_end'], samples=len(self.run['times']))
                                     if self.run else dict(run=self.di['id'], mode='dual_inlet', started_s=self.di['t0'], ends_s=self.di['t_end']) if self.di else {}),
               'results.count': len(self.results),
               'calibration.state': dict(normalization={k: dict(slope=v['slope'], intercept=v['intercept']) for k, v in self.norm.items()},
                                         h3_factor_ppm_na=self.h3_k * 1e-3),
               'hood.angle_rad': self.hood}
        for c in ('green', 'amber', 'red'):
            out[f'indicator.led_{c}'] = out['status.led'] == c
        if visibility != 'agent':
            out['truth.process'] = (dict(pressure_pa=p, turbo_speed=self._turbo_speed(), field_offset_t=(self._field_true() - b_for(self._centre(self.magnet['species']),
                                                                                                                                  self.source['acceleration_v']))
                                         if self.magnet['species'] else None, beams_a={str(k): v for k, v in sorted(self.beams.items())},
                                         isolation_open=self.vac['isolation_open'], reference_left_mol=list(self.ref_left), h3_factor_true_ppm_na=ENG['h3_factor_ppm_na'],
                                         in_flight=len(self.in_flight)) if pc else {})
            out['truth.samples'] = dict(declared={k: dict(v) for k, v in sorted(self.declared.items())},
                                        reference_gases=copy.deepcopy(self.plant['reference_gases']) if pc else [],
                                        peaks={str(q['peak']): copy.deepcopy(q.get('truth')) for q in self.results if q.get('truth')})
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
class ElementarIsoprimeprecisionSession:
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
        meta = copy.deepcopy(export_elementar_isoprimeprecision_contract_metadata())
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
    'PLANT_REQUIRED': '尚未声明环境与进样配置', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'CONFIG_FIXED': '进样口与参比气种只能在首次声明时设定',
    'NAME_TAKEN': '样品编号与标准物质编号重名', 'POWER_OFF': '主电源未接通或停电', 'FILAMENT_ON': '灯丝开着（先关灯丝再放气）',
    'VACUUM_NOT_READY': '分析器真空未就绪（不能开灯丝）', 'FILAMENT_FAILED': '灯丝已烧断（须更换）', 'OUT_OF_RANGE': '该加速电压下磁场达不到这些质量（3 kV时1–76 amu）',
    'RUN_ACTIVE': '采集或双路进样正在进行', 'NO_SPECIES': '尚未把磁场跳到某气种配置', 'SOURCE_OFF': '离子源灯丝未开', 'INLET_EMPTY': '该进样口没有连接进样系统',
    'NO_HELIUM': '没有氦气（瓶空或供气故障）', 'NO_REFERENCE_GAS': '该位置没有参比气或气种不符', 'UNKNOWN_SAMPLE': '样品编号未声明',
    'NO_DELTA_FOR_SPECIES': '该样品/标准物质没有这种气体所需同位素的δ值', 'WRONG_INLET': '双路进样口不能用于连续流进样（用dual_inlet.start）',
    'NO_CARRIER': '氦载气未开或处于休眠', 'WRONG_SPECIES': '参比气或双路进样的气种与当前磁场配置不符', 'NO_DUAL_INLET': '本配置没有连接双路进样系统',
    'REFERENCE_UNASSIGNED': '参比气尚未赋值（reference.assign）', 'NEED_H2_PULSES': '该次采集需要至少3个强度不同（相差≥1.5倍）的H₂参比脉冲',
    'UNKNOWN_PEAK': '峰编号不存在、是参比峰或没有该同位素的δ', 'NOT_A_STANDARD': '该物质的该同位素没有可用于归一的汇编或目录值（近似值或区间不行）',
    'STANDARDS_TOO_CLOSE': '标准物质的δ相差太小（<5 ‰），不能确定直线', 'GAS_REQUIRED': '该故障须指定参比气位置',
    'NOT_VENTED': '分析器未放气（须先vacuum.vent）', 'HOOD_CLOSED': '护罩未打开（≥1.0 rad；离子源在护罩下）', 'FAULT_ACTIVE': '灯丝故障仍在注入中（先清除故障）',
}
_P = ['PLANT_REQUIRED']
_PP = _P + ['POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED', 'CONFIG_FIXED'], 'sample.declare': _P + ['NAME_TAKEN'], 'power.set': _P, 'power.toggle': _P,
    'vacuum.vent': _PP + ['FILAMENT_ON'], 'vacuum.pump_down': _PP,
    'source.set': _PP + ['VACUUM_NOT_READY', 'FILAMENT_FAILED', 'OUT_OF_RANGE'], 'source.replace_filament': _P + ['NOT_VENTED', 'HOOD_CLOSED', 'FAULT_ACTIVE'], 'magnet.set_species': _PP + ['OUT_OF_RANGE', 'RUN_ACTIVE'],
    'magnet.peak_center': _PP + ['NO_SPECIES', 'SOURCE_OFF', 'RUN_ACTIVE'], 'amplifier.configure': _PP + ['RUN_ACTIVE'],
    'centrion.set': _PP + ['INLET_EMPTY', 'NO_HELIUM'], 'reference.assign': _P + ['NO_REFERENCE_GAS'],
    'sample.inject': _PP + ['INLET_EMPTY', 'WRONG_INLET', 'NO_CARRIER', 'UNKNOWN_SAMPLE', 'NO_DELTA_FOR_SPECIES'],
    'acquisition.start': _PP + ['RUN_ACTIVE', 'NO_SPECIES', 'SOURCE_OFF', 'NO_CARRIER', 'NO_REFERENCE_GAS', 'WRONG_SPECIES'], 'acquisition.stop': _P,
    'dual_inlet.start': _PP + ['NO_DUAL_INLET', 'RUN_ACTIVE', 'WRONG_SPECIES', 'SOURCE_OFF', 'NO_REFERENCE_GAS', 'REFERENCE_UNASSIGNED', 'UNKNOWN_SAMPLE',
                               'NO_DELTA_FOR_SPECIES'],
    'h3.determine': _P + ['NEED_H2_PULSES'], 'calibration.normalize': _P + ['UNKNOWN_PEAK', 'NOT_A_STANDARD', 'STANDARDS_TOO_CLOSE'],
    'hood.set': _P, 'results.read': [], 'events.read': [], 'truth.inspect': _P, 'ledger.read': [], 'fault.inject': _P + ['GAS_REQUIRED'], 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子、进样口与参比气种（之后不可改）；首次声明时分析器默认已长期抽真空（vented=true时在大气）', ['plant.configured']),
    'sample.declare': ('编号不与标准物质重名', ['plant.configured']), 'power.set': ('无', ['power.on']), 'power.toggle': ('无', ['power.on']),
    'vacuum.vent': ('有电；灯丝关', ['power.on', 'source.state']), 'vacuum.pump_down': ('有电', ['power.on', 'vacuum.state']),
    'source.set': ('有电；开灯丝时真空就绪且灯丝未烧断；加速电压须能让磁场覆盖当前配置的质量', ['power.on', 'vacuum.state', 'source.state', 'magnet.state']),
    'source.replace_filament': ('分析器已放气；护罩打开（≥1.0 rad）；灯丝故障已清除', ['vacuum.state', 'hood.angle_rad', 'source.state']),
    'magnet.set_species': ('有电；该气种的质量在当前加速电压的范围内；没有采集在进行', ['power.on', 'source.state', 'acquisition.state']),
    'magnet.peak_center': ('有电；已选气种；灯丝开；没有采集在进行', ['power.on', 'magnet.state', 'source.state', 'acquisition.state']),
    'amplifier.configure': ('有电；没有采集在进行；固定模式须给出与当前配置杯数相同的电阻', ['power.on', 'acquisition.state', 'magnet.state']),
    'centrion.set': ('有电；选择的进样口已连接；开载气须有氦气', ['power.on', 'centrion.state']),
    'reference.assign': ('该位置有参比气；只给该气种的同位素', ['centrion.state']),
    'sample.inject': ('有电；进样口已连接且不是双路进样；载气开且未休眠；样品已声明或为标准物质且有所需同位素', ['power.on', 'centrion.state']),
    'acquisition.start': ('有电；已选气种；灯丝开；载气开；参比脉冲的气种与配置相同；没有采集在进行', ['power.on', 'magnet.state', 'source.state', 'centrion.state', 'acquisition.state']),
    'acquisition.stop': ('无', ['acquisition.state']),
    'dual_inlet.start': ('有电；连接了双路进样；当前配置为该气种；灯丝开；参比气已赋值；没有采集在进行', ['power.on', 'magnet.state', 'source.state', 'centrion.state', 'acquisition.state']),
    'h3.determine': ('该次采集含至少3个强度不同的H₂参比脉冲', ['results.count']), 'calibration.normalize': ('峰为样品峰且有该同位素的δ；至少两种标准物质，δ相差≥5 ‰', ['results.count']),
    'hood.set': ('无', ['hood.angle_rad']), 'truth.inspect': ('无', ['plant.configured']), 'fault.inject': ('参比气瓶空须给gas', ['plant.configured']),
}
_EVOLVE = ['vacuum.pressure_pa', 'vacuum.state', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'beams.v', 'source.state', 'acquisition.state', 'results.count', 'amplifier.state', 'centrion.state',
           'calibration.state', 'truth.process', 'truth.samples']
EFFECTS = {
    'plant.configure': ['plant.configured', 'vacuum.state', 'vacuum.pressure_pa', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'centrion.state', 'source.state', 'truth.process', 'truth.samples'],
    'sample.declare': ['truth.samples'],
    'power.set': ['power.on', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'vacuum.state', 'vacuum.pressure_pa', 'source.state', 'beams.v', 'truth.process'],
    'power.toggle': ['power.on', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'vacuum.state', 'vacuum.pressure_pa', 'source.state', 'beams.v', 'truth.process'],
    'vacuum.vent': ['vacuum.state', 'vacuum.pressure_pa', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'truth.process'],
    'vacuum.pump_down': ['vacuum.state', 'vacuum.pressure_pa', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'truth.process'],
    'source.replace_filament': ['source.state', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'truth.process'],
    'source.set': ['source.state', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'beams.v', 'magnet.state', 'truth.process'],
    'magnet.set_species': ['magnet.state', 'beams.v', 'amplifier.state', 'truth.process'],
    'magnet.peak_center': ['magnet.state', 'beams.v', 'truth.process'],
    'amplifier.configure': ['amplifier.state', 'beams.v'],
    'centrion.set': ['centrion.state', 'vacuum.pressure_pa', 'vacuum.state', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'truth.process'],
    'reference.assign': ['centrion.state'],
    'sample.inject': ['truth.samples', 'truth.process'],
    'acquisition.start': ['acquisition.state', 'amplifier.state', 'truth.process'],
    'acquisition.stop': ['acquisition.state', 'results.count', 'truth.samples', 'truth.process'],
    'dual_inlet.start': ['amplifier.state', 'acquisition.state'],
    'h3.determine': ['calibration.state'], 'calibration.normalize': ['calibration.state'], 'hood.set': ['hood.angle_rad'],
    'fault.inject': ['truth.faults', 'power.on', 'status.led', 'indicator.led_green', 'indicator.led_amber', 'indicator.led_red', 'vacuum.state', 'vacuum.pressure_pa', 'source.state', 'beams.v', 'centrion.state', 'truth.process'],
    'sim.step': ['clock.time_s'],
}
_STEPPED = ('plant.configure', 'power.set', 'power.toggle', 'vacuum.vent', 'vacuum.pump_down', 'source.set', 'magnet.set_species', 'magnet.peak_center',
            'centrion.set', 'sample.inject', 'acquisition.start', 'dual_inlet.start', 'fault.inject')
ON_STEP = {k: list(_EVOLVE) for k in _STEPPED}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {
    'sim.step': '有离子流要测时按0.1 s网格积分（样品峰与参比脉冲在途、采集或双路进样进行中），否则真空、涡轮泵、灯丝小时与氦气用量按时间解析推进：'
                '分析器压力、离子流、放大器电压、峰识别与δ计算、状态LED',
    'vacuum.pump_down': '分析器压力按水汽出气（1/t）下降，约12 h就绪', 'vacuum.vent': '分析器回到大气', 'power.set': '涡轮泵升速或停转；停电时隔离阀封闭分析器',
    'sample.inject': '样品峰按设定延时与峰宽到达离子源（开口分流）', 'acquisition.start': '记录各杯电压，结束时识别峰并计算δ', 'dual_inlet.start': '样品与参比交替进源，结束时给出δ与内精度',
    'fault.inject': '故障作用于电源、灯丝、真空、氦气、磁场或参比气',
}
EXEC_TEXT = {'acquisition.start': ('acquisition.state有运行号与结束时间；到结束时间（或acquisition.stop）后峰结果写入results', '前置条件不满足时拒绝'),
             'dual_inlet.start': ('acquisition.state为dual_inlet；到结束时间后结果写入results（δ与周期内精度）', '前置条件不满足时拒绝')}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('sample.inject', 'hood.set'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'magnet.peak_center': ['beams.v'], 'acquisition.start': ['beams.v'], 'dual_inlet.start': ['beams.v'], 'vacuum.pump_down': ['vacuum.pressure_pa']}
_WB, _PG, _BR, _CI, _CR, _US, _VS, _SIM = ('user_workbook', 'manufacturer_product_page', 'manufacturer_brochure_2020', 'ciaaw_element_pages',
                                          'ciaaw_reference_materials', 'usgs_reference', 'literature_vsmow_ratios', 'simulation_assumption')
CAP_SOURCES = {k: [_BR] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_BR, _WB, _SIM], 'sample.declare': [_SIM], 'centrion.set': [_BR, _PG], 'reference.assign': [_BR, _CR],
                    'sample.inject': [_BR, _CR, _US, _SIM], 'acquisition.start': [_BR, _PG, _SIM], 'dual_inlet.start': [_BR, _SIM], 'h3.determine': [_BR],
                    'calibration.normalize': [_BR, _CR, _US], 'hood.set': [_BR, _SIM], 'results.read': [_BR, _SIM], 'events.read': [_SIM],
                    'truth.inspect': [_SIM], 'ledger.read': [_SIM], 'fault.inject': [_BR, _SIM], 'sim.step': [_SIM], 'power.set': [_BR, _SIM], 'power.toggle': [_BR, _SIM],
                    'amplifier.configure': [_PG, _BR], 'magnet.set_species': [_BR], 'magnet.peak_center': [_BR], 'source.set': [_BR], 'vacuum.vent': [_BR, _SIM],
                    'vacuum.pump_down': [_BR]})
CAP_SOURCES.update({k: [_PG, _BR] for k in UNSUPPORTED})
PARAM_ZH = {'ambient_k': '室温', 'pressure_pa': '大气压', 'he_supply_gauge_pa': '氦气瓶表压', 'inlets': '5个进样口连接的进样系统（或空）',
            'reference_gases': '6个参比（监测）气位置：气种、真值δ与可用量', 'combustion_o2_delta18o': '燃烧用氧的δ18O（VSMOW；元素分析仪CO₂的氧来自它）',
            'vented': '开机前分析器是否在大气', 'noise_seed': '噪声种子', 'sample_id': '样品编号', 'deltas': 'δ值（小数，‰÷1000；13C VPDB、15N Air-N2、18O VSMOW、2H VSMOW、34S VCDT）',
            'note': '备注', 'on': '开或关', 'filament': '灯丝开关', 'emission_a': '发射（捕集）电流', 'acceleration_v': '离子加速电压', 'species': '气种',
            'mode': '放大器模式', 'resistors_ohm': '固定模式下各杯电阻（主到次）', 'inlet': '进样口（1–5）', 'carrier_on': '氦载气开关', 'carrier_m3_s': '氦载气流量',
            'dilution': '稀释份额（0–0.95）', 'sleep': '休眠', 'gas': '参比气位置（1–6）', 'amount_mol': '样品气体的物质的量', 'substance_id': '标准物质编号（模板库）',
            'delay_s': '进样到开始出峰的延时', 'width_s': '峰的半高宽', 'duration_s': '时长', 'reference_pulses': '参比脉冲（气、开始时间、时长、主束强度）',
            'start_s': '相对采集开始的时间', 'intensity_a': '主束电流', 'sample_bar_ul': '样品量（bar·µL）', 'reference_gas': '参比气位置', 'cycles': '周期数',
            'integration_s': '每次积分时间', 'run': '运行号', 'isotope': '同位素', 'standards': '标准物质峰（峰号与标准物质编号）', 'peak': '峰号',
            'angle_rad': '护罩开启角', 'since_peak': '从该峰号之后', 'since_s': '起始时间', 'fault': '故障名', 'active': '注入或清除', 'dt_s': '推进时间'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.irms_process.limitations。'
CAP_LIMITS = {
    'sample.inject': ['样品以已转化的气体峰（高斯形状）进入centrION：元素分析仪/GC/LiquiFace的燃烧、还原、色谱与分馏不在本机范围（完全转化、不分馏）；未选中的进样口的样品经centrION排空。'],
    'acquisition.start': ['峰识别按主束超过基线的阈值；参比峰按方法中的参比脉冲时间认定；δ相对最近的前一个参比峰；CO₂做¹⁷O校正（λ=0.528），CO、SO₂、O₂、N₂O的计算有简化（见机理局限）。'],
    'dual_inlet.start': ['双路进样按样品/参比交替的块积分（每块切换后8 s不积分），每个样品块对相邻参比块求比值；气囊按目标压力压缩，样品量少时压力与束流较低。'],
    'calibration.normalize': ['两点或多点最小二乘直线；只接受CIAAW汇编值或USGS目录值（近似值与区间中点不行）。'],
    'h3.determine': ['按各H₂参比峰的3/2比对强度加权m/z 2电流作直线，斜率即H₃⁺因子（累加到已用的因子上）。'],
}
LIMITATIONS = [
    '时间网格：真空跳闸、灯丝到寿命、氦气用尽与隔离阀重新打开在0.1 s网格上判定（静止区间按闭式计算的时刻拆分），落在两网格点之间的步进终点只结束到期的采集；双路进样与采集从下一个网格点开始。',
    '¹⁷O校正：K取VSMOW锚点（R17/R18^λ），与正向模型一致；模板库中IUPAC 2010的VPDB-CO₂常数K=0.01022461只作参考（只用其λ）；报告δ相对参比气并经标准归一，K的选择一阶抵消。',
    '双路进样精度：结果给出各周期δ的标准差（cycle_sd）与平均值的标准误差（internal_se）；产品册的双路内部精度按2×internal_se解读。灯丝烧断须放气后source.replace_filament更换。',
    '所选配置：isoprime precisION标准配置（通用收集器阵列：CO₂、N₂、CO、SO₂、H₂、O₂、N₂O），集成centrION（5个进样口、6种参比气），由宿主声明连接的进样系统（元素分析仪、GC、LiquiFace、痕量气、顶空、双路进样）；不含烘烤、团簇同位素与卤素检测器选件。',
    '离子源：电离效率按厂家“每离子的CO₂分子数”（连续流1100、双路进样800）并与发射电流成正比，其他气种用相对电离截面（工程值）；开口分流进源份额与离子传输效率为工程值。',
    '离子光学：磁场按B r = sqrt(2mU/e)（r=108 mm）；各气种配置的质量落在固定杯上，偏离时按平顶峰与分辨率110的边缘衰减；磁场漂移为工程值。',
    '检测：法拉第杯散粒噪声与反馈电阻热噪声、100 V量程、默认电阻与饱和时自动降档；线性度（‰/nA）、峰间比值不稳定、丰度灵敏度与空气/水汽本底为工程值（均在厂家指标之内）。',
    '同位素：标尺比值（VPDB的¹³C/¹²C与¹⁷O校正常数为IUPAC推荐值的工程假设，其余由CIAAW数据推得）只用于把δ换算成离子流；报告的δ相对参比气，并用标准物质归一。CO的³⁰/²⁸忽略¹³C¹⁷O项，SO₂的⁶⁶/⁶⁴假设氧为VSMOW，N₂O与O₂只给简化换算。',
    '真空：分析器压力按水汽出气（1/t，按12 h标定）、残余出气、漏气与氦负载除以涡轮泵抽速；停电时隔离阀封闭分析器，压力按工程速率上升，来电后泵到速再开阀。',
    '场景：机箱、护罩、分析器部件与接口有几何；离子、气体与样品没有几何；护罩开启角与状态LED按运行层状态变化，电源开关为弹簧键。',
]


def _sampling_text(k):
    if k == 'clock.time_s':
        return 'Host clock.'
    if SLOTS[k]['origin'] == 'sensor':
        return 'Read from the 0.1 s acquisition grid while beams are measured (otherwise from the closed-form vacuum and background); rounded for display.'
    if SLOTS[k]['origin'] in ('derived', 'process_truth'):
        return 'Updated on the 0.1 s grid while beams are measured; between measurements closed-form functions of the host clock.'
    return 'Changes when a command is accepted.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if CAPS[k]['visibility'] != 'agent' else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _pre_text(k):
    errs, text = CAP_ERRORS.get(k, []), PRECONDITIONS[k][0]
    if 'PLANT_REQUIRED' in errs and '声明' not in text:
        text = '已声明环境与进样配置（否则PLANT_REQUIRED）；' + text
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
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节或指示灯', binding_ids=[])


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_product_page': _PG, 'manufacturer_brochure_2020': _BR}
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in ('ionisation_cf', 'resolution', 'simultaneous_beams',
                                                                                                                                 'amplifier_range_v', 'centrion_inlets', 'centrion_monitoring_gases') else ()))
    cal = {k for k, v in instrument()['engineering'].items() if v.get('calibrated_by')}
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明），不是实测' + ('；由calibrate_irms.py标定' if k in cal else ''))
    for k, v in instrument()['derived'].items():
        asm(k, v['value'], v['unit'], '由厂家数据推导：' + v['derivation'], '推导值', (_BR,))
    for k, v in _lib('deltas')['scale_anchors'].items():
        src = {'ciaaw_element_pages': _CI, 'literature_vsmow_ratios': _VS, 'simulation_assumption': _SIM}[v['source']]
        if v['evidence'] == 'documented':
            doc('anchor_' + k, v['value'], v['unit'], '标尺绝对比值：' + v['derivation'], (src,))
        else:
            asm('anchor_' + k, v['value'], v['unit'], '标尺绝对比值：' + v['derivation'], '推导值' if v['evidence'] == 'derived' else 'IUPAC推荐值，原文未能缓存', (src,))
    st = _lib('deltas')
    asm('delta_templates', dict(file='assets/expansion_batch04/t3_analytical/elementar_isoprimeprecision/delta_templates.json', counts=st['counts']), '1',
        '标准物质δ模板（CIAAW汇编带k=2不确定度、USGS目录值；每条带来源）', '实测文献数据；适用规则见模板库rules_zh', (_CR, _US))
    for name, val, unit, meaning in (('elementary_charge_c', E_CH, 'C', '元电荷'), ('atomic_mass_unit_kg', U_KG, 'kg', '原子质量单位'), ('boltzmann_j_k', K_B, 'J/K', '玻尔兹曼常数'),
                                     ('avogadro_mol', N_A, '1/mol', '阿伏伽德罗常数'), ('gas_constant_j_mol_k', R_GAS, 'J/(mol K)', '气体常数'), ('lambda_17o', LAMBDA_17, '1', '¹⁷O质量相关分馏指数')):
        asm(name, val, unit, meaning, '物理常数或推荐常数', (_SIM,))
    return P


SLOT_REFERENCE = {'vacuum.pressure_pa': '分析器绝对压力', 'beams.v': '各杯放大器电压（含基线偏置）'}


def export_elementar_isoprimeprecision_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'ElementarIsoprimeprecisionSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.elementar_isoprimeprecision.ElementarIsoprimeprecisionSession.invoke',
                                              invocation=f'session.invoke(instance_id="elementar_isoprimeprecision_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='ElementarIsoprimeprecisionRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（压力、离子流与结果在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['chassis', 'ion_source', 'magnet', 'flight_tube', 'collector_array', 'turbo_pump', 'centrion', 'hood', 'status_led'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='稳定同位素比质谱：centrION把进样系统送来的已转化气体（或参比气脉冲）经开口分流送入电子轰击离子源，离子经加速与磁场按质量分开，'
                   '法拉第杯接收各同位素体的离子流，放大器给出电压；ionOS识别峰、积分并相对参比气与标准物质计算δ；涡轮泵维持分析器真空，停电时隔离阀保护离子光学',
        equations=['p = (q1 3600/t + Q_floor + Q_leak + Q_He) / (S n_turbo / n_rated)  (sealed after a power failure: dp/dt = const)',
                   'ions/s = molecules/s x f_split (1 - dilution) x (I_e / I_e,nom) x sigma_rel / N_eff x T_ion;  N_eff = 1100 (CF) or 800 (DI) CO2 molecules per ion',
                   'I_m = e x ions/s x a_m / sum(a);  a_44 = 1, a_45 = R13 + 2 R17, a_46 = 2 R18 + 2 R13 R17 + R17^2 (other gases analogous);  R17 = R17_VSMOW (1 + d18)^0.528',
                   'B r = sqrt(2 m U / e);  cup fraction = 1 within the flat top, Gaussian edge with sigma = m / (R 2.355)',
                   'V = (I + n_shot + n_Johnson) R + V_0 <= 100 V;  n_shot ~ sqrt(I e / dt);  n_Johnson ~ sqrt(4 k T / (R 2 dt));  minor beams x (1 + L I_major)',
                   'H3+: I_3 += K I_2^2;  ratio 3/2 = 2 R_D + K I_2 (K from the slope)',
                   'delta_ref: R_sample,abs = (r_sample / r_ref) R_ref,assigned;  CO2: solve 45R = R13 + 2K R18^l, 46R = 2R18 + 2 R13 K R18^l + K^2 R18^2l',
                   'normalised delta = slope x delta_ref + intercept (least squares over reference materials)'],
        parameters=_params(), source_ids=[_SIM, _WB, _PG, _BR, _CI, _CR, _US, _VS], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_irms_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('elementar_isoprimeprecision', caps, slots, {'irms_process': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'sample.inject': (INJECT, CAPS['sample.inject']['example_arguments']),
                                'acquisition.start': (RUN, CAPS['acquisition.start']['example_arguments'])})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'irms_process': mechanism})
