"""CHEM-183 Bronkhorst CEM W-202A-NN0-K controlled evaporator and mixer: process runtime.

Device (brochure 9.60.038D, manual 9.17.126E): 3-way control ('mixing') valve, mixing chamber and heat exchanger with a 100 W heater, PT100 and
an internal safety switch at 200 C; liquid from a liquid flow meter (the meter drives the CEM mixing valve) is atomised into the carrier gas from
a gas mass flow controller and evaporated in the heat exchanger; capacity about 120 g/h water with 10 ln/min gas at 1 atm (W-202A capacity
diagram: water 0.2-10 ln/min gas, 10-120 g/h, 100 g/h at 0.2 ln/min rising to 120 g/h at 0.6 ln/min; IPA up to about 320 g/h); max. pressure
100 bar (rating), heater max. 200 C; 1/8" compression connections; mounted vertically (valve on top, vapour outlet down); 1.7 kg.  The E-8000
readout / control unit (power, temperature controller, setpoints) and the upstream liquid flow meter and gas MFC are dependency modules: the
runtime contains functional stand-ins (first-order flow control to the set point within their full scale), not models of those instruments.

Process model (fixed 0.1 s grid anchored at t = 0, state held between grid points):
  * Flows: gas n_g = Q_n P_n / (R T_n) (Bronkhorst normal conditions 0 C, 1 atm) and liquid mass flow follow their set points with first-order
    lags when the supplies are connected and pressurised above the process pressure; gas bubbles in an un-bled liquid line make the liquid flow
    fluctuate; liquid pressure above the gas pressure without gas flow pushes liquid back into the gas line.
  * Heat exchanger block: C dT/dt = P_heater - Q_stream - UA_loss (T - T_amb); P_heater from a PI controller on the PT100 reading (0..100 W,
    no cooling), interrupted by the safety switch above 200 C.
  * Evaporation: the stream leaves at T_out solving UA_hx (T_hx - T_out) = sum_i s n_i (cp_l,i (T_out - T_in) + dHvap_i(T_out)) +
    (1 - s) sum_i n_i cp_l,i (T_out - T_in) + n_g cp_g (T_out - T_in), where the evaporated fraction s is limited by saturation (dew point of
    the outlet mixture <= T_out, Raoult's law with vapour pressures from one continuous Antoine fit of the NIST parameter sets per liquid) and by
    the heat available; dHvap from Clausius-Clapeyron on the
    Antoine equation.  Unevaporated liquid pools in the exchanger, evaporates later or is carried out as droplets when the pool exceeds its limit.
  * Outlet: composition lags with the exchanger volume / volumetric flow; in the downstream line the vapour condenses down to saturation at the
    line temperature (plant setting, e.g. heat tracing).
  * Ledger: every species mass is booked (fed, bled, evaporated, delivered, condensed in the line, carried over, pooled, pushed into the gas line)."""
import copy
import json
import math
import re
import zlib
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.bronkhorst_cemw"
CATALOG_ID = "CHEM-183"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch03/t5_automation/bronkhorst_cemw'
LIBS = {'vapour': 'vapour_library.json', 'gas': 'gas_library.json', 'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def liquids():
    return _lib('vapour')['entries']


def gases():
    return _lib('gas')['entries']


def instrument():
    return _lib('instrument')


DOC = {k: v['value'] for k, v in instrument()['documented'].items()}
ENG = {k: v['value'] for k, v in instrument()['engineering'].items()}
ENG_NOTE = {k: v['engineering_note_zh'] for k, v in instrument()['engineering'].items()}
ENG_UNIT = {k: v['unit'] for k, v in instrument()['engineering'].items()}
DT = 0.1                                       # process grid (s)
R_GAS = 8.314462618
LN10 = math.log(10.0)
T_N, P_N = DOC['normal_conditions']['T_K'], DOC['normal_conditions']['P_pa']      # Bronkhorst normal conditions (0 C, 1 atm)
LN_MIN = 1e-3 / 60.0                           # 1 ln/min in m3/s (normal)
GAS_FS_MAX = DOC['max_gas_m3_s']               # 10 ln/min
LIQ_FS_MAX = 1.0e-4                            # 360 g/h: liquid flow meter full scale limit of the stand-in (IPA capacity 320 g/h)
P_MAX = DOC['max_pressure_pa']
T_HEATER_MAX = DOC['max_heater_temperature_k']
SAFETY_RESET_K = T_HEATER_MAX - 5.0
DP_MIN = 0.2e5                                 # minimum supply over-pressure for full flow (engineering)
PURGE_CLEAR_S = 300.0                          # gas purge time that clears liquid pushed into the gas line (engineering, manual purge 30 min)
BLEED_TURNS_RAD = 3.0 * math.pi                # air relief fitting opened 1.5 turns (engineering)
BLEED_SCREW_RATE = BLEED_TURNS_RAD / 2.0       # screw turned in about 2 s
MAX_EVENTS_LOG = 2000
FAULTS = ['heater_failure', 'pt100_open', 'clogging', 'gas_inclusion', 'pressure_fluctuation']
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


def _h(text):
    return zlib.crc32(str(text).encode()) & 0x7FFFFFFF


# ---------------------------------------------------------------- thermophysical properties (library)
_ANT = {}


def _antoine(sp, T):
    """Continuous Antoine fit of all NIST parameter sets of the liquid (vapour_library antoine_fit) -> (A, B, C, extrapolated)."""
    f = _ANT.get(sp)
    if f is None:
        a = liquids()[sp]['antoine_fit']
        f = _ANT[sp] = (a['A'], a['B'], a['C'], a['t_range_K'][0], a['t_range_K'][1])
    A, B, C, lo, hi = f
    return A, B, C, not (lo - 1e-9 <= T <= hi + 1e-9)


def _narrow(sp):
    """Degenerate fits over a narrow NIST range (< 25 K): the Antoine curve is used only inside its range."""
    a = liquids()[sp]['antoine_fit']
    return a['t_range_K'][1] - a['t_range_K'][0] < 25.0


def psat(sp, T):
    """Vapour pressure (Pa) from the continuous Antoine fit of the NIST parameter sets: log10(P/bar) = A - B/(T + C).  Below the region where
    T + C stays positive (fits with a large negative C) the curve is continued with the Clausius-Clapeyron slope from T = 30 - C.  For a degenerate
    fit over a narrow range (< 25 K) the Antoine curve is used only inside its range; outside, Clausius-Clapeyron from the nearer range edge with the
    Watson enthalpy of vaporization integrated from that edge (the slope of a narrow degenerate set is meaningless)."""
    A, B, C, _ = _antoine(sp, T)
    if _narrow(sp):
        lo, hi = liquids()[sp]['antoine_fit']['t_range_K']
        if lo <= T <= hi:
            return max(1e5 * 10.0 ** (A - B / (T + C)), 1e-12)
        te = lo if T < lo else hi
        pe = 1e5 * 10.0 ** (A - B / (te + C))
        tc = liquids()[sp].get('critical_temperature_K') or 1e9
        Tx = min(T, tc)                                                  # no vapour pressure above Tc: the curve is continued as p(Tc) T / Tc
        n = 24                                                           # ln p(T) = ln p(te) + int_te^T dH(t) / (R t^2) dt  (Simpson; monotonic since dH > 0)
        hh = (Tx - te) / n
        acc = sum((1 if i in (0, n) else 4 if i % 2 else 2) * dhvap(sp, te + i * hh) / (te + i * hh) ** 2 for i in range(n + 1)) * hh / 3.0
        return max(pe * math.exp(acc / R_GAS) * (T / Tx), 1e-12)
    t0 = 30.0 - C
    if T < t0:
        p0 = 1e5 * 10.0 ** (A - B / 30.0)
        return max(p0 * math.exp(-LN10 * B * t0 * t0 / 900.0 * (1.0 / T - 1.0 / t0)), 1e-12)
    return max(1e5 * 10.0 ** (A - B / (T + C)), 1e-12)


def _dh_cc(sp, T):
    A, B, C, _ = _antoine(sp, T)
    return R_GAS * LN10 * B * T * T / max((T + C) ** 2, 900.0)


def dhvap(sp, T):
    """Enthalpy of vaporization (J/mol): Watson correlation dH(T) = dH_ref ((Tc - T)/(Tc - T_ref))^0.38 when the NIST critical temperature is known
    (dH_ref = library dhvap_reference: the median Watson anchor at the boiling point over the NIST rows, method-A rows excluded when others exist;
    without rows Clausius-Clapeyron at the boiling point); otherwise Clausius-Clapeyron on the Antoine fit (ideal vapour, liquid volume neglected -
    too high near the critical point).  Watson with the fixed exponent 0.38 is about +10...18 % high for some alcohols near Tc and for carboxylic
    acids (association), see the library note."""
    e = liquids()[sp]
    tc = e.get('critical_temperature_K')
    if tc:
        ref = e.get('dhvap_reference')
        if ref and ref['T_K'] < tc - 5.0:
            h0, t0 = ref['value_J_mol'], ref['T_K']
        else:
            t0 = e.get('boiling_point_K') or 0.7 * tc
            h0 = _dh_cc(sp, t0)
        return h0 * (max(tc - T, 0.5) / (tc - t0)) ** 0.38
    return _dh_cc(sp, T)


def cp_liq(sp):
    return liquids()[sp]['cp_liquid']['value_J_mol_K']


def molar_mass(sp):
    return liquids()[sp]['molar_mass_kg_mol']


def cp_gas_pure(g, T):
    e = gases()[g]
    if 'composition_mole_fraction' in e:
        tot = sum(e['composition_mole_fraction'].values())
        return sum(x * cp_gas_pure(k, T) for k, x in e['composition_mole_fraction'].items()) / tot
    sets = e['shomate']
    s = next((s for s in sets if s['t_range_K'][0] <= T <= s['t_range_K'][1]), min(sets, key=lambda s: abs(T - s['t_range_K'][0])))
    t = max(T, s['t_range_K'][0] if T < s['t_range_K'][0] else T) / 1000.0
    return s['A'] + s['B'] * t + s['C'] * t * t + s['D'] * t ** 3 + s['E'] / (t * t)


def gas_molar_mass(g):
    return gases()[g]['molar_mass_kg_mol']


def dew_point(y, P, lo=200.0, hi=650.0):
    """Dew point (K) of a vapour with mole fractions y over the condensable species (Raoult): sum_i y_i P / psat_i(T) = 1."""
    if not y or sum(y.values()) <= 0:
        return None

    def g(T):
        return sum(v * P / psat(s, T) for s, v in y.items()) - 1.0
    if g(hi) > 0:
        return hi
    if g(lo) < 0:
        return lo
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if g(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def flash_inert(n_vap, n_gas, P, T):
    """Isothermal Raoult flash of condensable vapours n_vap (mol/s) with an inert gas n_gas at T, P -> vapour moles left per species (the rest
    condenses).  sum_i z_i / (1 + V (K_i - 1)) = 1 over the condensables, K_i = p_sat,i(T) / P, V = vapour fraction of the total."""
    F = n_gas + sum(n_vap.values())
    if F <= 0 or not n_vap:
        return dict(n_vap)
    K = {s: psat(s, T) / P for s in n_vap}
    if sum(n / F / K[s] for s, n in n_vap.items()) <= 1.0:            # below the dew point: nothing condenses
        return dict(n_vap)

    def f(V):
        return sum((n / F) / (1.0 + V * (K[s] - 1.0)) for s, n in n_vap.items()) - 1.0
    lo, hi = n_gas / F, 1.0                       # f(lo) < 0 (all condensables liquid), f(hi) > 0 (below the dew point): f increases with V
    if f(lo) >= 0:
        return {s: 0.0 for s in n_vap}
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    V = 0.5 * (lo + hi)
    out = {}
    for s, n in n_vap.items():
        x = (n / F) / (1.0 + V * (K[s] - 1.0))
        out[s] = max(0.0, min(n, K[s] * x * V * F))
    return out


def sat_fraction(n_liq, n_gas_total, P, T):
    """Largest fraction s of the liquid molar flows n_liq that can be vapour at T and P (outlet dew point <= T), composition kept."""
    N = sum(n_liq.values())
    if N <= 0:
        return 1.0
    a = sum(n * P / psat(s, T) for s, n in n_liq.items())
    if a - N <= 0:
        return 1.0
    return max(0.0, min(1.0, n_gas_total / (a - N)))


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
        elif 'additionalProperties' in s and isinstance(s['additionalProperties'], dict):
            if len(v) < s.get('minProperties', 0) or len(v) > s.get('maxProperties', 10 ** 6):
                raise ActionRejected('INVALID_ARGUMENT')
            for key, value in v.items():
                if 'propertyNames' in s:
                    validate(s['propertyNames'], key)
                validate(s['additionalProperties'], value)
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
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=60, pattern='^[a-z0-9_]+$')
COMPOSITION = dict(type='object', propertyNames=SID, additionalProperties=number('1', 0.0, 1.0), minProperties=1, maxProperties=8)
PLANT_EXAMPLE = dict(ambient_k=295.15, process_pressure_pa=101325.0, line_temperature_k=295.15, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 278.15, 313.15), process_pressure_pa=number('Pa', 1.0e3, 1.0e7), line_temperature_k=number('K', 273.15, 523.15),
                 noise_seed=integer(0, 2 ** 31 - 1)))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'supply', 'flow', 'heater', 'warning', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
CALC_OUT = obj(dict(vapour_mole_fraction=FREE_OBJ, vapour_flow_m3_s=number('m3/s'), total_flow_m3_s=number('m3/s'), dew_point_k=nullable(number('K')),
                    recommended_setpoint_k=nullable(number('K')), heat_load_w=number('W'), heat_available_w=number('W'), within_documented_window=BOOL,
                    notes=array(TEXT, 0, 16)))
_BODY = ['heat_exchanger']
_VALVE = ['mixing_valve', 'valve_coil']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明环境温度、过程（出口）绝压、下游管路温度（伴热）与噪声种子', 'evaluator', 'configuration')
cap('supply.connect_liquid', obj(dict(composition=COMPOSITION, pressure_pa=number('Pa', 0.0, 2.0e7))), obj(dict(connected=BOOL, liquids=array(TEXT, 1, 8))),
    dict(composition={'water': 1.0}, pressure_pa=3.0e5), ['liquid_inlet', 'mixing_block'],
    '接上游液体：液体组成（质量分数，蒸气库中的液体）与供液绝压；新接的液体管路含气泡，需排气', 'agent', 'robot_skill')
cap('supply.connect_gas', obj(dict(gas=enum(sorted(gases())), pressure_pa=number('Pa', 0.0, 2.0e7))), obj(dict(connected=BOOL, gas=TEXT)),
    dict(gas='nitrogen', pressure_pa=4.0e5), ['gas_inlet', 'mixing_block'], '接上游载气：气体种类（载气库）与供气绝压', 'agent', 'robot_skill')
cap('controllers.configure', obj(dict(gas_full_scale_m3_s=number('m3/s', 1.0e-6, GAS_FS_MAX), liquid_full_scale_kg_s=number('kg/s', 1.0e-7, LIQ_FS_MAX))),
    obj(dict(gas_full_scale_m3_s=number('m3/s'), liquid_full_scale_kg_s=number('kg/s'))), dict(gas_full_scale_m3_s=GAS_FS_MAX, liquid_full_scale_kg_s=120e-3 / 3600),
    ['mixing_valve'], '设定上游流量计/控制器量程（依赖模块的替身）：载气MFC满量程（标准状态体积流量，≤10 ln/min）与液体流量计满量程', 'agent', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['power_connector', 'heat_exchanger'],
    'E-8000读出/控制单元供电（CEM加热与上游流量计）：断电时阀门关闭、加热停止', 'agent', 'device_command')
cap('gas.set_flow', obj(dict(setpoint_m3_s=number('m3/s', 0.0, GAS_FS_MAX))), obj(dict(setpoint_m3_s=number('m3/s'))), dict(setpoint_m3_s=10 * LN_MIN),
    ['gas_inlet', 'mixing_block'], '载气流量设定（标准状态0 °C、1 atm体积流量，m3/s；1 ln/min = 1.667e-5 m3/s）', 'agent', 'device_command')
cap('liquid.set_flow', obj(dict(setpoint_kg_s=number('kg/s', 0.0, LIQ_FS_MAX))), obj(dict(setpoint_kg_s=number('kg/s'))), dict(setpoint_kg_s=100e-3 / 3600),
    _VALVE + ['liquid_inlet'], '液体质量流量设定（液体流量计通过CEM混合阀控制）', 'agent', 'device_command')
cap('heater.set_temperature', obj(dict(setpoint_k=number('K', 273.15, T_HEATER_MAX))), obj(dict(setpoint_k=number('K'))), dict(setpoint_k=413.15),
    ['heat_exchanger', 'power_connector'], '换热器温度设定（0…200 °C；加热器不能制冷，设定低于实际温度等于关断加热；0 °C即停止加热）', 'agent', 'device_command')
cap('bleed.set', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['bleed_fitting', 'mixing_valve'],
    '混合阀排气口（空气释放接头）打开/关闭：排出液体管路中的气泡（须先把液体设定置0）', 'agent', 'robot_skill')
cap('mixture.calculate', obj(dict(composition=COMPOSITION, gas=enum(sorted(gases())), liquid_kg_s=number('kg/s', 0.0, LIQ_FS_MAX), gas_m3_s=number('m3/s', 0.0, GAS_FS_MAX),
                                  pressure_pa=number('Pa', 1.0e3, 1.0e7)), optional=('composition', 'gas', 'liquid_kg_s', 'gas_m3_s', 'pressure_pa')),
    CALC_OUT, dict(liquid_kg_s=100e-3 / 3600, gas_m3_s=10 * LN_MIN), ['heat_exchanger'],
    'FLUIDAT等价计算：按液体组成、载气、流量与过程压力计算蒸气组成、露点、推荐温度设定与热负荷（缺省用当前接入与设定）', 'agent', 'analysis')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _BODY,
    '读取事件记录（agent看不到故障与真值事件）', 'agent', 'query')
cap('provenance.read', obj({}), obj(dict(liquids=FREE_OBJ, gas=FREE_OBJ, sources=FREE_OBJ)), {}, _BODY,
    '当前液体与载气的数据来源（NIST WebBook条目、Antoine参数与引用）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(stream=FREE_OBJ, thermal=FREE_OBJ, lines=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, _BODY,
    '评测者读取过程真值：出口组成、露点、冷凝、换热器温度与热流、积液与管路状态', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '物料账：各物种进料、排气、蒸发、输出、管路冷凝、带出、积液与倒灌（kg）', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='heater_failure', active=True), _BODY,
    '故障注入：加热器损坏、PT100断路、堵塞（溶解物残留或烧结）、液体管路含气、供压波动', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'fieldbus.connect': 'RS232/现场总线通讯属于E-8000读出/控制单元的选件（宿主侧），CEM本体不提供。',
    'dilution_gas.set': '稀释气路属于VDM模块的选项，W-202A单体CEM没有。',
    'trace_heating.set': '出口伴热控制属于VDM模块选项或外部伴热带；本资产只把下游管路温度作为宿主声明的环境条件。',
    'dual_carrier.set': '双载气供应属于VDM模块选项，W-202A单体CEM没有。',
    'solids.vaporise': '溶解在溶剂中的固体前驱体蒸发（厂家称可行）没有建模：没有固体蒸气压与残留数据。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'heat_exchanger', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'heat_exchanger', '环境条件是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'power_connector', 'E-8000供电是否接通')
slot('setpoint.gas_m3_s', number('m3/s'), 'm3/s', 'setpoint', 'gas_inlet', '载气流量设定（标准状态体积流量）')
slot('setpoint.liquid_kg_s', number('kg/s'), 'kg/s', 'setpoint', 'mixing_valve', '液体质量流量设定')
slot('setpoint.temperature_k', number('K'), 'K', 'setpoint', 'heat_exchanger', '换热器温度设定（273.15 K即0 °C，等于停止加热）')
slot('measured.gas_m3_s', nullable(number('m3/s')), 'm3/s', 'sensor', 'gas_inlet', '载气流量读数（MFC，标准状态体积流量；断电为空）')
slot('measured.liquid_kg_s', nullable(number('kg/s')), 'kg/s', 'sensor', 'mixing_valve', '液体流量读数（液体流量计；断电为空）')
slot('measured.temperature_k', nullable(number('K')), 'K', 'sensor', 'heat_exchanger', '换热器PT100温度读数（经E-8000，含电缆电阻偏差；断电或传感器断路为空）')
slot('heater.output_fraction', number('1', 0.0, 1.0), '1', 'derived', 'heat_exchanger', '加热器输出占100 W的比例（温控器输出）')
slot('bleed.open', BOOL, '1', 'setpoint', 'bleed_fitting', '排气口是否打开')
slot('bleed.screw_angle_rad', number('rad'), 'rad', 'derived', 'bleed_fitting', '排气口（空气释放接头）旋开角度（关0，开约3π；约2 s转完）')
slot('supply.liquid', nullable(FREE_OBJ), '1', 'setpoint', 'liquid_inlet', '接入的液体：组成（质量分数）与供液绝压')
slot('supply.gas', nullable(FREE_OBJ), '1', 'setpoint', 'gas_inlet', '接入的载气：种类与供气绝压')
slot('controllers.full_scale', FREE_OBJ, '1', 'setpoint', 'mixing_valve', '上游载气MFC与液体流量计的满量程（m3/s标准状态、kg/s）')
slot('alarms.active', array(TEXT, 0, 32), '1', 'derived', 'heat_exchanger', '当前报警与提示（只含E-8000与流量计能测到的或由操作者自己的设定与接法推出的：传感器断路、超温、温度未到、流量偏低、无载气加液、液压高于气压、建议排气、能力窗口外、设定接近安全开关或远高于沸点）')
slot('truth.stream', FREE_OBJ, '1', 'process_truth', 'vapour_outlet', '出口真值：组成、温度、露点、蒸发分数、下游冷凝与输出流量', 'evaluator')
slot('truth.thermal', FREE_OBJ, '1', 'process_truth', 'heat_exchanger', '换热器真值：块温、加热功率、对流体热流、散热、安全开关', 'evaluator')
slot('truth.lines', FREE_OBJ, '1', 'process_truth', 'mixing_block', '管路真值：实际流量、积液、液体管路含气、气路进液、堵塞', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'heat_exchanger', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'bleed.screw_angle_rad'}


# ---------------------------------------------------------------- runtime
class BronkhorstCemwRuntime:
    def __init__(self, instance_id='bronkhorst_cemw_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.powered = False
        self.liq_supply = None                       # dict(w={sp: mass fraction}, p=Pa)
        self.gas_supply = None                       # dict(gas=id, p=Pa)
        self.fs = dict(gas=GAS_FS_MAX, liquid=120e-3 / 3600)
        self.sp = dict(gas=0.0, liquid=0.0, T=273.15)
        self.sp_t = {}
        self.q_gas = 0.0                             # actual gas flow (m3/s normal)
        self.m_liq = 0.0                             # actual liquid flow through the mixing valve (kg/s)
        self.T_hx = None                             # heat exchanger block temperature (K)
        self.T_meas = None                           # PT100 first-order reading (K)
        self.integ = 0.0                             # PI integrator (W)
        self.P_heat = 0.0
        self.safety_tripped = False
        self.pool = {}                               # unevaporated liquid in the exchanger (kg per species)
        self.out = dict(n={}, n_gas=0.0, T=None, s=1.0, Q=0.0)    # lagged outlet stream (mol/s per species)
        self.calc = {}                               # last instantaneous solution (truth)
        self.bubbles = False
        self.bleed_open = False
        self.bleed_angle = 0.0
        self.bleed_flow_s = 0.0
        self.gas_line_liquid = 0.0                   # liquid pushed into the gas line (kg)
        self.gas_line_kg = {}                        # per-species inventory of that liquid (kg)
        self.bled_since_connect = True
        self.outlet = {}                             # outlet / exchanger vapour inventory per species (mol)
        self.purge_s = 0.0
        self.clog = 1.0
        self.overheat_s = 0.0
        self.unstable = False
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.warnings = {}
        self.revision = 0
        self.led = {}
        self._principals, self._requests, self._caller = {}, {}, None

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
        return BronkhorstCemwSession(self, name)

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

    def _book(self, sp, key, kg):
        if kg:
            self.led.setdefault(sp, {}).setdefault(key, 0.0)
            self.led[sp][key] += kg

    # ---- derived quantities
    def _P(self):
        return self.plant['process_pressure_pa']

    def _n_gas(self, q=None):
        return (self.q_gas if q is None else q) * P_N / (R_GAS * T_N)

    def _feed_moles(self, m, w):
        return {s: m * x / molar_mass(s) for s, x in w.items() if x > 0}

    def _gas_cp(self, T):
        return cp_gas_pure(self.gas_supply['gas'], T) if self.gas_supply else 29.1

    def _window(self, w, m, q, P=None):
        """Documented W-202A capacity diagram (1 atm; figure readings): gas 0.2...10 ln/min (both curves); water 10...120 g/h with the knee
        100 g/h at 0.2 -> 120 g/h at 0.6 ln/min; IPA up to 320 g/h; other liquids: only the 0.2 ln/min minimum gas flow; the diagram is for
        1 atm (other process pressures are flagged).  -> list of violated limits."""
        out = []
        W = DOC['capacity_water_window']
        if m <= 0:
            return out
        if q < W['gas_m3_s'][0] - 1e-12:
            out.append('GAS_BELOW_WINDOW')
        dom = max(w, key=w.get)
        if dom == 'water':
            lo, hi = W['liquid_kg_s']
            (q0, m0), (q1, m1) = W['knee']
            cap_ = m1 if q >= q1 else m0 + (m1 - m0) * max(q - q0, 0.0) / (q1 - q0)
            if m < lo - 1e-15:
                out.append('LIQUID_BELOW_WINDOW')
            if m > cap_ + 1e-15:
                out.append('LIQUID_ABOVE_WINDOW')
        elif dom == 'propan_2_ol':
            if m > DOC['capacity_ipa_kg_s'] + 1e-15:
                out.append('LIQUID_ABOVE_WINDOW')
        P = P if P is not None else (self._P() if self.plant else P_N)
        if abs(P - P_N) > 0.2e5:
            out.append('PRESSURE_NOT_CHARACTERISED')
        return out

    def _solve_outlet(self, n_liq, n_g, T_hx, T_in, P, n_pool=None, T_pool=None):
        """Steady exchange over one step: -> (T_out, s evaporated fraction of the offered liquid, Q to the stream W).  n_liq is fresh liquid at
        T_in; n_pool (included in n_liq) is liquid pooled in the exchanger, already at T_pool."""
        N = sum(n_liq.values())
        if N <= 0 and n_g <= 0:
            return T_hx, 1.0, 0.0
        flow_ref = ENG['ua_hx_gas_ref_m3_s'] * P_N / (R_GAS * T_N)
        ua = ENG['ua_hx_W_K'] * max(((n_g + N) / flow_ref) ** ENG['ua_hx_flow_exponent'], 0.3) * self.clog
        cpl = {s: cp_liq(s) for s in n_liq}

        pool = n_pool or {}
        Tp = T_in if T_pool is None else T_pool

        def demand(T, s):
            q = 0.0
            for sp, n in n_liq.items():
                n_p = min(pool.get(sp, 0.0), n)
                q += (n - n_p) * cpl[sp] * (T - T_in) + n_p * cpl[sp] * (T - Tp) + s * n * dhvap(sp, T)
            return q + n_g * self._gas_cp(0.5 * (T + T_in)) * (T - T_in)

        def s_at(T):
            return sat_fraction(n_liq, n_g, P, T) if N > 0 else 1.0
        lo, hi = max(150.0, min(T_in, T_hx) - 80.0), max(T_hx, T_in)     # evaporative cooling may take the stream below the inlet temperature
        if ua * (T_hx - lo) - demand(lo, s_at(lo)) <= 0:                # heat limited even at the lowest temperature
            need = sum(n * dhvap(sp, lo) for sp, n in n_liq.items())
            s = min(s_at(lo), max(0.0, (ua * (T_hx - lo) - n_g * self._gas_cp(lo) * (lo - T_in)) / need)) if need > 0 else 1.0
            return lo, s, ua * (T_hx - lo)
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if ua * (T_hx - mid) - demand(mid, s_at(mid)) > 0:
                lo = mid
            else:
                hi = mid
        T = 0.5 * (lo + hi)
        return T, s_at(T), ua * (T_hx - T)

    # ---- physics
    def step(self, dt_s):
        """Advance the clock; the state is integrated on the fixed DT grid anchored at t = 0 (partition independent); between grid points the
        last state is held (a command acts from the grid point before it, at most DT earlier)."""
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
        T_amb = pl['ambient_k']
        P = self._P()
        k = self.tick
        fluct = 1.0
        if 'pressure_fluctuation' in self.faults:
            fluct += ENG['unstable_rel_amplitude'] * _gauss(self.seed, k, 3)
        # -- gas flow (MFC stand-in)
        q_target = 0.0
        if self.powered and self.gas_supply and self.sp['gas'] > 0:
            dp = self.gas_supply['p'] - P
            q_target = self.sp['gas'] * max(0.0, min(1.0, dp / DP_MIN)) * fluct
        self.q_gas += (q_target - self.q_gas) * (1 - math.exp(-h / ENG['gas_mfc_tau_s']))
        self.q_gas = 0.0 if q_target == 0.0 and self.q_gas < 1e-12 else max(self.q_gas, 0.0)
        # -- liquid flow through the mixing valve (liquid flow meter stand-in)
        m_target = 0.0
        if self.powered and self.liq_supply and self.sp['liquid'] > 0 and not self.bleed_open:
            dp = self.liq_supply['p'] - P
            m_target = self.sp['liquid'] * max(0.0, min(1.0, dp / DP_MIN)) * fluct * self.clog
            if self.bubbles or 'gas_inclusion' in self.faults:
                m_target *= 1.0 + ENG['bubble_rel_amplitude'] * _gauss(self.seed, k, 5)
        w_now = self.liq_supply['w'] if self.liq_supply else {}
        self.unstable = bool(w_now) and self.m_liq > 0 and any(x in ('GAS_BELOW_WINDOW', 'LIQUID_ABOVE_WINDOW') for x in self._window(w_now, self.m_liq, self.q_gas))
        if self.unstable:                                        # outside the documented atomisation window: pulsating aerosol (engineering amplitude)
            m_target *= 1.0 + ENG['unstable_rel_amplitude'] * _gauss(self.seed, k, 9)
        self.m_liq += (m_target - self.m_liq) * (1 - math.exp(-h / ENG['valve_tau_s']))
        self.m_liq = 0.0 if m_target == 0.0 and self.m_liq < 1e-12 else max(self.m_liq, 0.0)
        w = self.liq_supply['w'] if self.liq_supply else {}
        # -- bleed outlet
        tgt = BLEED_TURNS_RAD if self.bleed_open else 0.0
        self.bleed_angle += max(-BLEED_SCREW_RATE * h, min(BLEED_SCREW_RATE * h, tgt - self.bleed_angle))
        if self.bleed_open and self.liq_supply and self.liq_supply['p'] > pl['ambient_pressure_pa'] + 1e3 and self.bleed_angle > 0.5 * BLEED_TURNS_RAD:
            m_b = ENG['bleed_rate_kg_s'] * max(0.0, min(1.0, (self.liq_supply['p'] - pl['ambient_pressure_pa']) / DP_MIN)) * h   # the bleed vents to the room
            for s, x in w.items():
                self._book(s, 'fed_kg', m_b * x)
                self._book(s, 'bled_kg', m_b * x)
            self.bleed_flow_s += h
            if self.bleed_flow_s >= ENG['bleed_clear_s'] and self.bubbles:
                self.bubbles = False
                self._event('truth', 'LIQUID_LINE_BLED', '液体管路气泡已排净')
        # -- liquid into the gas line (liquid pressure above gas pressure while no gas flows)
        n_g = self._n_gas()
        m_in = self.m_liq
        if m_in > 0 and self.gas_supply is not None and self.liq_supply['p'] > self.gas_supply['p'] and self.q_gas < 0.01 * LN_MIN:
            back = 0.5 * m_in * h
            self.gas_line_liquid += back
            for s, x in w.items():
                self.gas_line_kg[s] = self.gas_line_kg.get(s, 0.0) + back * x
                self._book(s, 'fed_kg', back * x)
                self._book(s, 'into_gas_line_kg', back * x)
            m_in *= 0.5
        if self.gas_line_liquid > 0 and self.q_gas >= 0.5 * self.fs['gas'] and m_in < 1e-9:
            self.purge_s += h
            if self.purge_s >= PURGE_CLEAR_S:
                for s_, m_ in self.gas_line_kg.items():
                    self._book(s_, 'purged_from_gas_line_kg', m_)
                    self._book(s_, 'into_gas_line_kg', -m_)
                self.gas_line_kg = {}
                self.gas_line_liquid = 0.0
                self.purge_s = 0.0
                self._event('truth', 'GAS_LINE_PURGED', '气路中的液体已被载气吹出（经出口排出）')
        # -- evaporation in the heat exchanger
        for s, x in w.items():
            self._book(s, 'fed_kg', m_in * x * h)
        pool_out = {s: m / ENG['pool_tau_s'] for s, m in self.pool.items() if m > 0}    # pooled liquid offered to the stream (kg/s)
        n_liq = self._feed_moles(m_in, w)
        n_pool = {s: ms / molar_mass(s) for s, ms in pool_out.items()}
        for s, n in n_pool.items():
            n_liq[s] = n_liq.get(s, 0.0) + n
        T_in = T_amb + ENG['liquid_inlet_temperature_offset_k']
        T_out, s_ev, Q = self._solve_outlet(n_liq, n_g, self.T_hx, T_in, P, n_pool, self.calc.get('T_out') if self.calc else None)
        for s, n in n_liq.items():
            kg = n * molar_mass(s) * h
            from_pool = min(pool_out.get(s, 0.0) * h, self.pool.get(s, 0.0))
            self.pool[s] = self.pool.get(s, 0.0) - from_pool + kg * (1 - s_ev)
            self._book(s, 'evaporated_kg', kg * s_ev)
        pool_tot = sum(self.pool.values())
        if pool_tot > ENG['liquid_pool_max_kg']:                 # the exchanger is flooded: droplets leave with the stream
            excess = pool_tot - ENG['liquid_pool_max_kg']
            for s in list(self.pool):
                c = excess * self.pool[s] / pool_tot
                self.pool[s] -= c
                self._book(s, 'carried_over_kg', c)
        # -- heat exchanger block and the E-8000 controller
        self.T_meas += (self.T_hx - self.T_meas) * (1 - math.exp(-h / ENG['pt100_tau_s']))
        reading = self._pt100()
        P_cmd = 0.0
        if self.powered and reading is not None and self.sp['T'] > 273.15 + 1e-9:
            e = self.sp['T'] - reading
            u = ENG['controller_kp_W_K'] * e + self.integ
            if 0.0 < u < DOC['heater_power_w'] or (u >= DOC['heater_power_w'] and e < 0) or (u <= 0 and e > 0):
                self.integ += ENG['controller_kp_W_K'] * e * h / ENG['controller_ti_s']
            P_cmd = max(0.0, min(DOC['heater_power_w'], u))
        else:
            self.integ = 0.0
        if self.T_hx > T_HEATER_MAX:
            if not self.safety_tripped:
                self._event('heater', 'SAFETY_SWITCH', '换热器超过200 °C：内部安全开关切断加热')
            self.safety_tripped = True
        elif self.safety_tripped and self.T_hx < SAFETY_RESET_K:
            self.safety_tripped = False
        if self.safety_tripped or 'heater_failure' in self.faults:
            P_cmd = 0.0
        self.P_heat = P_cmd
        loss = ENG['ua_loss_W_K'] * (self.T_hx - T_amb)
        self.T_hx += (P_cmd - Q - loss) * h / ENG['block_heat_capacity_J_K']
        # -- clogging by burn-in (overheating above the liquid's boiling point while liquid is present)
        tb = max((liquids()[s]['boiling_point_K'] or 373.15) for s in w) if w else None
        if tb and self.m_liq > 0 and self.T_hx > tb + ENG['burn_in_margin_k']:
            self.overheat_s += h
        if 'clogging' in self.faults:
            self.clog = max(0.2, self.clog - 0.002 * h)
        # -- outlet: explicit vapour inventory of the mixing chamber / exchanger (V_hx) that empties with tau = V_hx / Q_actual; downstream the
        #    leaving vapour is flashed (Raoult, inert carrier gas) at the line temperature - the condensate is booked, the rest is delivered
        n_vap = {s: n * s_ev for s, n in n_liq.items() if n * s_ev > 0}
        q_vol = (n_g + sum(n_vap.values())) * R_GAS * max(T_out, 200.0) / P
        tau = min(60.0, max(0.05, ENG['hx_internal_volume_m3'] / q_vol)) if q_vol > 0 else 60.0
        a = 1 - math.exp(-h / tau)
        leaving = {}
        for s in set(self.outlet) | set(n_vap):
            inv = self.outlet.get(s, 0.0) + n_vap.get(s, 0.0) * h
            out_ = inv * a
            self.outlet[s] = inv - out_
            if out_ > 0:
                leaving[s] = out_ / h
        self.outlet = {s: v for s, v in self.outlet.items() if v > 1e-18}
        self.out['n'] = leaving
        self.out['n_gas'] = n_g
        T_line = pl['line_temperature_k']
        kept = flash_inert(leaving, n_g, P, T_line) if leaving else {}
        for s, n in leaving.items():
            kg = n * molar_mass(s) * h
            frac = kept.get(s, n) / n if n > 0 else 1.0
            self._book(s, 'delivered_kg', kg * frac)
            self._book(s, 'condensed_in_line_kg', kg * (1 - frac))
        tot_cond = sum(leaving.values())
        s_line = (sum(kept.values()) / tot_cond) if tot_cond > 0 else 1.0
        tot_out = n_g + sum(kept.values())
        y = {s: n / tot_out for s, n in kept.items() if n > 0} if tot_out > 0 else {}
        y_hx = {s: n / (n_g + sum(n_vap.values())) for s, n in n_vap.items()} if n_vap else {}
        ev_kg_s = sum(n * molar_mass(sp) * s_ev for sp, n in n_liq.items())
        self.calc = dict(T_out=T_out, s_ev=s_ev, Q=Q, s_line=s_line, y=y, y_hx=y_hx, n_g=n_g, n_liq=n_liq, loss=loss, feed_kg_s=m_in, evaporation_kg_s=ev_kg_s,
                         y_line_in={s: n / (n_g + tot_cond) for s, n in leaving.items()} if leaving else {}, conditions=self._conditions(s_ev, s_line))

    def _conditions(self, s_ev, s_line):
        """Process conditions that no CEM / E-8000 sensor detects (evaluator truth only)."""
        c = []
        if self.m_liq > 0 and s_ev < 0.999:
            c.append('INCOMPLETE_EVAPORATION')
        if sum(self.pool.values()) > 0.5 * ENG['liquid_pool_max_kg']:
            c.append('LIQUID_ACCUMULATING')
        if s_line < 0.999:
            c.append('CONDENSATION_DOWNSTREAM')
        if self.bubbles and self.liq_supply:
            c.append('GAS_IN_LIQUID_LINE')
        if self.gas_line_liquid > 0:
            c.append('LIQUID_IN_GAS_LINE')
        if self.overheat_s > 60.0:
            c.append('OVERHEATING_LIQUID')
        if self.clog < 0.95:
            c.append('FLOW_RESTRICTED')
        if self.unstable:
            c.append('VAPOUR_UNSTABLE')
        return c

    def _pt100(self):
        if not self.powered or 'pt100_open' in self.faults or self.T_meas is None:
            return None
        return self.T_meas + 0.1 * ENG['sensor_cable_m'] + ENG['pt100_noise_k'] * _gauss(self.seed, self.tick, 7)

    def _update_warnings(self):
        """Agent-visible alarms: only what the E-8000 / flow meters measure (PT100, flow readings) or what follows from the operator's own set points
        and connections; conditions only the process knows (incomplete evaporation, pooling, line condensation, gas in the liquid line ...) are
        evaluator truth (truth.stream / truth.lines conditions)."""
        W = {}
        if self.plant is None:
            self.warnings = W
            return
        reading = self._pt100()
        pw = self.powered
        if pw and 'pt100_open' in self.faults:
            W['SENSOR_FAILURE'] = 'PT100读数断路：温控器停止加热'
        if pw and reading is not None and reading > T_HEATER_MAX:
            W['OVER_TEMPERATURE'] = '换热器读数超过200 °C（内部安全开关切断加热）'
        if pw and reading is not None and self.sp['T'] > 273.15 and abs(reading - self.sp['T']) > 2.0:
            W['TEMPERATURE_NOT_REACHED'] = '换热器温度未到设定值'
        if pw and self.sp['T'] >= T_HEATER_MAX - 2.0:
            W['SETPOINT_AT_SAFETY_LIMIT'] = '设定接近200 °C安全开关：过冲会使加热被切断'
        if pw and self.sp['gas'] > 0 and self.q_gas < 0.9 * self.sp['gas'] and self.t - self.sp_t.get('gas', 0.0) > 5 * ENG['gas_mfc_tau_s']:
            W['GAS_FLOW_DEVIATION'] = '载气读数低于设定（供气压力不足或管路问题）'
        if pw and self.sp['liquid'] > 0 and self.m_liq < 0.9 * self.sp['liquid'] and self.t - self.sp_t.get('liquid', 0.0) > 5 * ENG['valve_tau_s']:
            W['LIQUID_FLOW_DEVIATION'] = '液体读数低于设定（供液压力不足、堵塞或混合阀问题）'
        if self.sp['liquid'] > 0 and pw and self.sp['gas'] <= 0:
            W['LIQUID_WITHOUT_GAS'] = '没有载气设定就加液体（厂家要求先有载气）'
        if self.liq_supply and self.gas_supply and self.liq_supply['p'] > self.gas_supply['p'] and self.sp['liquid'] > 0:
            W['LIQUID_PRESSURE_ABOVE_GAS'] = '供液压力高于供气压力（液体可能进入气路）'
        if self.liq_supply and not self.bled_since_connect:
            W['BLEED_RECOMMENDED'] = '换液后尚未排气（厂家建议首次使用与换液后排气）'
        if pw and self.sp['liquid'] > 0 and self.liq_supply:
            for x in self._window(self.liq_supply['w'], self.sp['liquid'], self.sp['gas']):
                W['WINDOW_' + x] = '设定在厂家能力图（1 atm）范围之外：' + x
        if self.liq_supply and pw and self.sp['T'] > 273.15:
            tb = max((liquids()[s]['boiling_point_K'] or 373.15) for s in self.liq_supply['w'])
            if self.sp['T'] > tb + ENG['burn_in_margin_k']:
                W['SETPOINT_FAR_ABOVE_BOILING'] = '设定温度远高于液体沸点：有过热烧结风险（厂家故障表）'
        self.warnings = W

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'provenance.read', 'truth.inspect', 'ledger.read', 'mixture.calculate'):
            self.revision += 1
        if capability_id != 'sim.step':
            self._update_warnings()
        return out

    def _cmd_plant_configure(self, ambient_k, process_pressure_pa, line_temperature_k, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(ambient_k=ambient_k, process_pressure_pa=process_pressure_pa, line_temperature_k=line_temperature_k, ambient_pressure_pa=P_N)
        self.seed = noise_seed
        if first:
            self.T_hx = self.T_meas = ambient_k
            self._event('system', 'PLANT_CONFIGURED', '环境条件已声明')
        return dict(configured=True)

    def _cmd_supply_connect_liquid(self, composition, pressure_pa):
        self._need_plant()
        if any(s not in liquids() for s in composition):
            raise ActionRejected('UNKNOWN_LIQUID')
        tot = sum(composition.values())
        if abs(tot - 1.0) > 1e-6:
            raise ActionRejected('INVALID_COMPOSITION')
        if pressure_pa > P_MAX:
            raise ActionRejected('PRESSURE_ABOVE_RATING')
        if self.sp['liquid'] > 0:
            raise ActionRejected('LIQUID_FLOWING')
        w = {s: v / tot for s, v in composition.items() if v > 0}
        changed = self.liq_supply is None or self.liq_supply['w'] != w
        self.liq_supply = dict(w=w, p=float(pressure_pa))
        if changed:
            self.bubbles = True
            self.bleed_flow_s = 0.0
            self.bled_since_connect = False
        self._event('supply', 'LIQUID_CONNECTED', '接入液体：' + '、'.join(f'{s} {v:.3g}' for s, v in w.items()))
        return dict(connected=True, liquids=sorted(w))

    def _cmd_supply_connect_gas(self, gas, pressure_pa):
        self._need_plant()
        if pressure_pa > P_MAX:
            raise ActionRejected('PRESSURE_ABOVE_RATING')
        if self.sp['gas'] > 0:
            raise ActionRejected('GAS_FLOWING')
        self.gas_supply = dict(gas=gas, p=float(pressure_pa))
        self._event('supply', 'GAS_CONNECTED', f'接入载气：{gas}')
        return dict(connected=True, gas=gas)

    def _cmd_controllers_configure(self, gas_full_scale_m3_s, liquid_full_scale_kg_s):
        self._need_plant()
        if self.sp['gas'] > 0 or self.sp['liquid'] > 0:
            raise ActionRejected('FLOW_ACTIVE')
        self.fs = dict(gas=float(gas_full_scale_m3_s), liquid=float(liquid_full_scale_kg_s))
        return dict(gas_full_scale_m3_s=self.fs['gas'], liquid_full_scale_kg_s=self.fs['liquid'])

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not self.powered:
            self._event('system', 'POWER_ON', '通电：阀门按上次设定动作，温控器启动')
        if not on and self.powered:
            self.integ = 0.0
            self.P_heat = 0.0
            self._event('system', 'POWER_OFF', '断电：阀门关闭、停止加热')
        self.powered = bool(on)
        return dict(on=self.powered)

    def _cmd_gas_set_flow(self, setpoint_m3_s):
        self._need_power()
        if setpoint_m3_s > self.fs['gas'] * 1.0000001:
            raise ActionRejected('SETPOINT_ABOVE_FULL_SCALE')
        if setpoint_m3_s > 0 and self.gas_supply is None:
            raise ActionRejected('NO_GAS_SUPPLY')
        self.sp['gas'] = float(setpoint_m3_s)
        self.sp_t['gas'] = self.t
        return dict(setpoint_m3_s=self.sp['gas'])

    def _cmd_liquid_set_flow(self, setpoint_kg_s):
        self._need_power()
        if setpoint_kg_s > self.fs['liquid'] * 1.0000001:
            raise ActionRejected('SETPOINT_ABOVE_FULL_SCALE')
        if setpoint_kg_s > 0 and self.liq_supply is None:
            raise ActionRejected('NO_LIQUID_SUPPLY')
        if setpoint_kg_s > 0 and self.bleed_open:
            raise ActionRejected('BLEED_OPEN')
        self.sp['liquid'] = float(setpoint_kg_s)
        self.sp_t['liquid'] = self.t
        return dict(setpoint_kg_s=self.sp['liquid'])

    def _cmd_heater_set_temperature(self, setpoint_k):
        self._need_power()
        self.sp['T'] = float(setpoint_k)
        return dict(setpoint_k=self.sp['T'])

    def _cmd_bleed_set(self, open):
        self._need_plant()
        if open and self.sp['liquid'] > 0:
            raise ActionRejected('LIQUID_SETPOINT_NOT_ZERO')
        if open and not self.bleed_open:
            self.bleed_flow_s = 0.0
        if open:
            self.bled_since_connect = True
        self.bleed_open = bool(open)
        self._event('flow', 'BLEED_OPEN' if open else 'BLEED_CLOSED', '排气口打开' if open else '排气口关闭')
        return dict(open=self.bleed_open)

    def _cmd_mixture_calculate(self, composition=None, gas=None, liquid_kg_s=None, gas_m3_s=None, pressure_pa=None):
        """FLUIDAT-equivalent design calculation (no state change): vapour composition, dew point, recommended set point and heat load."""
        if composition is None:
            if self.liq_supply is None:
                raise ActionRejected('NO_LIQUID_SUPPLY')
            composition = self.liq_supply['w']
        if any(s not in liquids() for s in composition):
            raise ActionRejected('UNKNOWN_LIQUID')
        tot = sum(composition.values())
        if abs(tot - 1.0) > 1e-6:
            raise ActionRejected('INVALID_COMPOSITION')
        gas = gas or (self.gas_supply['gas'] if self.gas_supply else None)
        if gas is None:
            raise ActionRejected('NO_GAS_SUPPLY')
        m = self.sp['liquid'] if liquid_kg_s is None else liquid_kg_s
        q = self.sp['gas'] if gas_m3_s is None else gas_m3_s
        P = pressure_pa if pressure_pa is not None else (self._P() if self.plant else P_N)
        T_in = self.plant['ambient_k'] if self.plant else 293.15
        w = {s: v / tot for s, v in composition.items() if v > 0}
        n_liq = self._feed_moles(m, w)
        n_g = q * P_N / (R_GAS * T_N)
        N = sum(n_liq.values())
        y = {s: n / (n_g + N) for s, n in n_liq.items()} if N + n_g > 0 else {}
        dp = dew_point(y, P) if N > 0 else None
        notes = []
        rec = None
        heat = 0.0
        if dp is not None:
            T_out = max(dp + 10.0, T_in)
            heat = sum(n * (cp_liq(s) * (T_out - T_in) + dhvap(s, T_out)) for s, n in n_liq.items()) + n_g * cp_gas_pure(gas, T_out) * (T_out - T_in)
            flow_ref = ENG['ua_hx_gas_ref_m3_s'] * P_N / (R_GAS * T_N)
            ua = ENG['ua_hx_W_K'] * max(((n_g + N) / flow_ref) ** ENG['ua_hx_flow_exponent'], 0.3)
            rec = T_out + heat / ua
            if rec > T_HEATER_MAX:
                notes.append('推荐温度超过200 °C：降低液体流量或提高载气')
                rec = T_HEATER_MAX
            if any(_antoine(s, dp)[3] for s in n_liq):
                notes.append('露点处使用了Antoine参数范围外的外推')
        avail = DOC['heater_power_w'] - ENG['ua_loss_W_K'] * ((rec or T_in) - T_in)
        if heat > avail:
            notes.append('热负荷超过加热器可用功率（约100 W减散热）：不能完全蒸发')
        win = self._window(w, m, q, P)
        notes += ['厂家能力图之外：' + x for x in win]
        if N > 0 and q <= 0:
            notes.append('没有载气')
        return dict(vapour_mole_fraction={s: round(v, 9) for s, v in y.items()}, vapour_flow_m3_s=N * R_GAS * T_N / P_N, total_flow_m3_s=(N + n_g) * R_GAS * T_N / P_N,
                    dew_point_k=dp, recommended_setpoint_k=rec, heat_load_w=heat, heat_available_w=avail, within_documented_window=not win, notes=notes)

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_provenance_read(self):
        sp = sorted(self.liq_supply['w']) if self.liq_supply else []
        L = {s: dict(name_en=liquids()[s]['name_en'], cas=liquids()[s]['cas'], pages=liquids()[s]['pages'], antoine=liquids()[s]['antoine'],
                     vapour_pressure_quality=liquids()[s]['antoine_fit']['quality'], vapour_pressure_note_zh=liquids()[s]['antoine_fit'].get('quality_note_zh'),
                     cp_liquid=dict(value_J_mol_K=liquids()[s]['cp_liquid']['value_J_mol_K'], reference=liquids()[s]['cp_liquid']['reference'])) for s in sp}
        g = self.gas_supply['gas'] if self.gas_supply else None
        G = {} if g is None else dict(id=g, evidence=gases()[g]['evidence'], source=gases()[g]['source'], note_zh=gases()[g].get('note_zh'))
        return dict(liquids=L, gas=G, sources=dict(liquids='NIST Chemistry WebBook (SRD 69) via vapour_library.json', instrument='Bronkhorst brochure 9.60.038D, manual 9.17.126E'))

    def _cmd_truth_inspect(self):
        v = self.read('evaluator')
        return dict(stream=v['truth.stream'], thermal=v['truth.thermal'], lines=v['truth.lines'], faults=v['truth.faults'])

    def _cmd_ledger_read(self):
        led = {s: {k: float(x) for k, x in d.items()} for s, d in self.led.items()}
        for s in set(led) | set(self.pool) | set(self.outlet):
            led.setdefault(s, {})['pooled_kg'] = float(self.pool.get(s, 0.0))
            ev = led[s].get('evaporated_kg', 0.0)
            led[s]['outlet_holdup_kg'] = float(self.outlet.get(s, 0.0) * molar_mass(s))
        return dict(ledger=led)

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        if fault == 'clogging' and not active:
            self.clog = 1.0
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}：{"注入" if active else "清除"}')
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- read
    def read(self, visibility='agent'):
        pw = self.powered
        out = {'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.on': pw,
               'setpoint.gas_m3_s': self.sp['gas'], 'setpoint.liquid_kg_s': self.sp['liquid'], 'setpoint.temperature_k': self.sp['T'],
               'measured.gas_m3_s': (max(0.0, self.q_gas * (1 + ENG['flow_noise_rel'] * _gauss(self.seed, self.tick, 11))) if pw else None),
               'measured.liquid_kg_s': (max(0.0, self.m_liq * (1 + ENG['flow_noise_rel'] * _gauss(self.seed, self.tick, 13))) if pw else None)}
        r = self._pt100() if self.plant else None
        out['measured.temperature_k'] = None if r is None else round(r, 2)
        out['heater.output_fraction'] = self.P_heat / DOC['heater_power_w']
        out['bleed.open'] = self.bleed_open
        out['bleed.screw_angle_rad'] = self.bleed_angle
        out['supply.liquid'] = None if self.liq_supply is None else dict(composition=dict(self.liq_supply['w']), pressure_pa=self.liq_supply['p'])
        out['supply.gas'] = None if self.gas_supply is None else dict(gas=self.gas_supply['gas'], pressure_pa=self.gas_supply['p'])
        out['controllers.full_scale'] = dict(gas_m3_s=self.fs['gas'], liquid_kg_s=self.fs['liquid'])
        out['alarms.active'] = sorted(self.warnings)
        if visibility != 'agent':
            c = self.calc or {}
            P = self._P() if self.plant else P_N
            tot = self.out['n_gas'] + sum(self.out['n'].values())
            out['truth.stream'] = dict(
                outlet_temperature_k=c.get('T_out'), evaporated_fraction_of_offered=c.get('s_ev', 1.0), liquid_feed_kg_s=c.get('feed_kg_s', 0.0),
                evaporation_kg_s=c.get('evaporation_kg_s', 0.0),
                evaporated_fraction=min(1.0, c['evaporation_kg_s'] / c['feed_kg_s']) if c.get('feed_kg_s', 0.0) > 0 else 1.0,
                conditions=list(c.get('conditions', [])), mole_fraction_at_exchanger=c.get('y_hx', {}),
                mole_fraction_delivered=c.get('y', {}), dew_point_k=dew_point(c['y_hx'], P) if c.get('y_hx') else None,
                dew_point_line_k=dew_point(c['y_line_in'], P) if c.get('y_line_in') else None, line_saturation_fraction=c.get('s_line', 1.0),
                vapour_mol_s=dict(self.out['n']), gas_mol_s=self.out['n_gas'], total_flow_actual_m3_s=tot * R_GAS * (c.get('T_out') or 293.15) / P if tot else 0.0,
                process_pressure_pa=P)
            out['truth.thermal'] = dict(block_temperature_k=self.T_hx, heater_power_w=self.P_heat, heat_to_stream_w=c.get('Q', 0.0), heat_loss_w=c.get('loss', 0.0),
                                        safety_switch_tripped=self.safety_tripped, controller_integral_w=self.integ)
            out['truth.lines'] = dict(gas_flow_m3_s=self.q_gas, liquid_flow_kg_s=self.m_liq, pool_kg=dict(self.pool), gas_in_liquid_line=self.bubbles,
                                      liquid_in_gas_line_kg=self.gas_line_liquid, clog_factor=self.clog, overheating_s=self.overheat_s)
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
class BronkhorstCemwSession:
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
        meta = copy.deepcopy(export_bronkhorst_cemw_contract_metadata())
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
    'PLANT_REQUIRED': '尚未声明环境条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': 'E-8000未通电',
    'UNKNOWN_LIQUID': '液体不在蒸气库中', 'INVALID_COMPOSITION': '质量分数之和不为1', 'PRESSURE_ABOVE_RATING': '供压超过100 bar额定压力',
    'LIQUID_FLOWING': '液体设定不为0（先把液体设定置0再换液体）', 'GAS_FLOWING': '载气设定不为0（先把载气设定置0再换气体）', 'FLOW_ACTIVE': '有流量设定（先全部置0）',
    'SETPOINT_ABOVE_FULL_SCALE': '设定超过流量计/控制器满量程', 'NO_GAS_SUPPLY': '没有接入载气', 'NO_LIQUID_SUPPLY': '没有接入液体',
    'BLEED_OPEN': '排气口打开时不能加液体（先关闭排气口）', 'LIQUID_SETPOINT_NOT_ZERO': '排气前须把液体设定置0（关闭混合阀）',
}
_P = ['PLANT_REQUIRED']
_PW = _P + ['POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'supply.connect_liquid': _P + ['UNKNOWN_LIQUID', 'INVALID_COMPOSITION', 'PRESSURE_ABOVE_RATING', 'LIQUID_FLOWING'],
    'supply.connect_gas': _P + ['PRESSURE_ABOVE_RATING', 'GAS_FLOWING'], 'controllers.configure': _P + ['FLOW_ACTIVE'], 'power.set': _P,
    'gas.set_flow': _PW + ['SETPOINT_ABOVE_FULL_SCALE', 'NO_GAS_SUPPLY'], 'liquid.set_flow': _PW + ['SETPOINT_ABOVE_FULL_SCALE', 'NO_LIQUID_SUPPLY', 'BLEED_OPEN'],
    'heater.set_temperature': _PW, 'bleed.set': _P + ['LIQUID_SETPOINT_NOT_ZERO'],
    'mixture.calculate': ['NO_LIQUID_SUPPLY', 'NO_GAS_SUPPLY', 'UNKNOWN_LIQUID', 'INVALID_COMPOSITION'], 'events.read': [], 'provenance.read': [],
    'truth.inspect': [], 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子；之后再声明须给同一种子', ['plant.configured']),
    'supply.connect_liquid': ('液体在蒸气库中、质量分数之和为1、供液压力不超过100 bar；液体设定为0', ['setpoint.liquid_kg_s', 'supply.liquid']),
    'supply.connect_gas': ('供气压力不超过100 bar；载气设定为0', ['setpoint.gas_m3_s', 'supply.gas']),
    'controllers.configure': ('载气与液体设定都为0', ['setpoint.gas_m3_s', 'setpoint.liquid_kg_s']),
    'power.set': ('无', ['power.on']),
    'gas.set_flow': ('已通电；设定不超过MFC满量程；设定大于0时已接入载气', ['power.on', 'controllers.full_scale', 'supply.gas']),
    'liquid.set_flow': ('已通电；设定不超过液体流量计满量程；设定大于0时已接入液体且排气口关闭', ['power.on', 'controllers.full_scale', 'supply.liquid', 'bleed.open']),
    'heater.set_temperature': ('已通电；0…200 °C', ['power.on']),
    'bleed.set': ('打开前液体设定为0（混合阀关闭）', ['setpoint.liquid_kg_s', 'bleed.open']),
    'mixture.calculate': ('缺省参数时须已接入液体与载气', ['supply.liquid', 'supply.gas']),
    'fault.inject': ('无', ['plant.configured']),
}
_EVOLVE = ['measured.gas_m3_s', 'measured.liquid_kg_s', 'measured.temperature_k', 'heater.output_fraction', 'alarms.active', 'truth.stream', 'truth.thermal', 'truth.lines']
EFFECTS = {
    'plant.configure': ['plant.configured', 'measured.temperature_k', 'truth.thermal', 'truth.stream', 'alarms.active'],
    'supply.connect_liquid': ['supply.liquid', 'alarms.active', 'truth.lines'], 'supply.connect_gas': ['supply.gas', 'alarms.active'],
    'controllers.configure': ['controllers.full_scale'],
    'power.set': ['power.on', 'measured.gas_m3_s', 'measured.liquid_kg_s', 'measured.temperature_k', 'heater.output_fraction', 'alarms.active', 'truth.thermal'],
    'gas.set_flow': ['setpoint.gas_m3_s', 'alarms.active'], 'liquid.set_flow': ['setpoint.liquid_kg_s', 'alarms.active'],
    'heater.set_temperature': ['setpoint.temperature_k', 'alarms.active'], 'bleed.set': ['bleed.open', 'alarms.active'],
    'fault.inject': ['truth.faults', 'truth.lines', 'measured.temperature_k', 'alarms.active'], 'sim.step': ['clock.time_s'],
}
ON_STEP = {
    'sim.step': _EVOLVE + ['bleed.screw_angle_rad', 'clock.time_s'],
    'gas.set_flow': ['measured.gas_m3_s', 'truth.stream', 'truth.thermal', 'truth.lines', 'alarms.active'],
    'liquid.set_flow': ['measured.liquid_kg_s', 'truth.stream', 'truth.thermal', 'truth.lines', 'alarms.active'],
    'heater.set_temperature': ['measured.temperature_k', 'heater.output_fraction', 'truth.thermal', 'truth.stream', 'alarms.active'],
    'power.set': ['measured.temperature_k', 'heater.output_fraction', 'truth.thermal', 'truth.lines', 'truth.stream'],
    'bleed.set': ['bleed.screw_angle_rad', 'truth.lines', 'alarms.active'], 'supply.connect_liquid': ['truth.lines'],
    'supply.connect_gas': ['truth.lines', 'truth.stream'], 'fault.inject': ['truth.thermal', 'truth.lines', 'truth.stream', 'alarms.active'],
    'plant.configure': ['truth.thermal', 'truth.stream', 'alarms.active'],
}
ON_STEP_ZH = {
    'sim.step': '按0.1 s网格积分：流量响应、换热器温度与温控器、蒸发与积液、出口组成滞后、下游冷凝与排气口旋转',
    'gas.set_flow': '载气按MFC时间常数趋近设定（供压不足时达不到）；出口组成与换热随之变化',
    'liquid.set_flow': '液体按流量计＋混合阀闭环时间常数趋近设定；蒸发、积液与热负荷随之变化',
    'heater.set_temperature': '温控器按PI调节加热功率（0–100 W，不能制冷），块温与PT100读数随之变化',
    'power.set': '断电后阀门关闭、加热停止，块温自然冷却', 'bleed.set': '排气口约2 s旋开/旋紧；打开且供液加压时液体排出，约20 s排净气泡',
    'supply.connect_liquid': '新液体的管路含气泡，直到排气', 'supply.connect_gas': '载气物性改变换热与组成', 'fault.inject': '故障作用于随后的加热、测温、流量与传热',
    'plant.configure': '环境温度、过程压力与管路温度影响散热、露点与冷凝',
}
EXEC_TEXT = {}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('gas.set_flow', 'liquid.set_flow', 'heater.set_temperature', 'bleed.set', 'power.set'):
    RESOURCES[_k] = ['host_clock', 'mixing_valve', 'heat_exchanger']
OBSERVES = {'gas.set_flow': ['measured.gas_m3_s'], 'liquid.set_flow': ['measured.liquid_kg_s'], 'heater.set_temperature': ['measured.temperature_k', 'heater.output_fraction'],
            'bleed.set': ['bleed.screw_angle_rad'], 'power.set': ['measured.temperature_k']}
_WB, _BR, _MN, _FQ, _NI, _SIM = 'user_workbook', 'manufacturer_brochure', 'manufacturer_manual', 'manufacturer_faq', 'nist_webbook', 'simulation_assumption'
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({'supply.connect_liquid': [_NI, _MN, _SIM], 'supply.connect_gas': [_NI, _SIM], 'controllers.configure': [_BR, _SIM], 'power.set': [_MN],
                    'gas.set_flow': [_BR, _FQ, _SIM], 'liquid.set_flow': [_BR, _MN, _SIM], 'heater.set_temperature': [_BR, _MN], 'bleed.set': [_MN, _SIM],
                    'mixture.calculate': [_NI, _BR, _MN, _SIM], 'provenance.read': [_NI, _BR], 'fault.inject': [_MN, _SIM], 'plant.configure': [_BR, _SIM]})
CAP_SOURCES.update({k: [_BR, _MN] for k in UNSUPPORTED})
PARAM_ZH = {'ambient_k': '环境温度（液体与载气进口温度、外壳散热基准）', 'process_pressure_pa': '过程（出口）绝压', 'line_temperature_k': '下游管路温度（伴热或环境）',
            'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.controlled_evaporation_mixing.limitations。'
CAP_LIMITS = {
    'mixture.calculate': ['FLUIDAT等价：理想气体与拉乌尔定律、NIST Antoine蒸气压（范围外外推并提示）；推荐温度=露点+10 K再加按传热系数估计的温差，不复制FLUIDAT的物性库与算法。'],
    'gas.set_flow': ['载气MFC为依赖模块（如EL-FLOW Select）：这里是一阶响应的替身，不建模MFC本身的传感器与校准。'],
    'liquid.set_flow': ['液体流量计为依赖模块（如LIQUI-FLOW/mini CORI-FLOW），通过CEM混合阀控制：这里是一阶闭环替身。'],
    'supply.connect_liquid': ['混合液按理想溶液（拉乌尔定律）；换热器内按进料组成整体蒸发（不分馏），下游管路冷凝按拉乌尔闪蒸（分馏）；未建模溶解固体。'],
    'heater.set_temperature': ['E-8000温控器为PI替身（工程参数）；设定低于实际温度只是停止加热（不能制冷）；超过200 °C内部安全开关切断加热、降到195 °C恢复；设定接近200 °C时过冲可能触发安全开关（提示SETPOINT_AT_SAFETY_LIMIT）。'],
    'bleed.set': ['排气口通向室内：供液压力高于大气时液体以工程速率排出；约20 s排净气泡（工程值）；排气口转动只镜像到场景，不是机器人旋拧验证。'],
    'power.set': ['E-8000供电：断电时上游流量计/控制器的阀门关闭、加热输出归零；不建模E-8000本身的显示与通讯。'],
}
LIMITATIONS = [
    '所选配置：CEM W-202A-NN0-K（100 W、1/8英寸卡套接口、Kalrez密封）；E-8000读出/控制单元、液体流量计与载气MFC为依赖模块，运行层中只有一阶响应替身；VDM选项（稀释气、双载气、伴热控制）与现场总线为不支持。',
    '蒸发：拉乌尔定律＋NIST Antoine连续拟合的蒸气压决定出口露点不得高于出口温度；出口温度由换热器块与流体之间的能量平衡（逆流近似、传热系数为工程值）求得；蒸发焓用Watson关系（NIST临界温度与沸点附近的NIST蒸发焓），没有临界温度时用克劳修斯–克拉佩龙关系。',
    '能力：能量上限由100 W加热器与传热/散热工程参数给出，在calibration_report.json中与厂家水120 g/h（10与0.6 ln/min）对照；厂家能力图在低载气处的拐点（0.2 ln/min时100 g/h）不是能量限制，按雾化窗口处理：窗口外（水的拐点线、IPA 320 g/h、所有液体0.2 ln/min最小载气）蒸气流量波动并报警；其他液体只用最小载气。',
    '下游：只有一段管路温度（宿主声明）决定冷凝（拉乌尔闪蒸，惰性载气）；管路长度、压降与停留时间未建模；冷凝液计入账目但不再回流。',
    'agent可见报警只含E-8000与流量计能测到的量或操作者自己的设定与接法推出的提示；未完全蒸发、积液、下游冷凝、液体管路含气、气路进液与堵塞只在评测者真值（truth.stream/truth.lines的conditions）中。',
    '液体与载气在场景中没有几何；场景中只有排气口旋钮随运行层状态转动；支架为工程假设（厂家要求竖直安装在刚性结构上）。',
]


def _sampling_text(k):
    if k.startswith(('measured.', 'truth.', 'heater.')) or k == 'alarms.active':
        return 'Integrated on the fixed 0.1 s grid (anchored at t = 0); between grid points the last value is held; sensor noise is drawn per grid tick.'
    if k in SCENE_MIRRORED:
        return 'Screw angle at the host time (turns towards open / closed at a fixed rate).'
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
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景关节（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节或读数', binding_ids=[])


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_brochure': _BR, 'manufacturer_manual': _MN, 'manufacturer_faq': _FQ}
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (src_of[v['source_id']],) + ((_WB,) if k in ('heater_power_w', 'max_liquid_water_kg_s', 'max_gas_m3_s',
                                                                                                                                   'max_pressure_pa', 'max_heater_temperature_k',
                                                                                                                                   'connection_1_8_inch', 'dimensions_m', 'mass_kg') else ()))
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明）' + ('；与厂家能力图对照标定' if k in CALIBRATED else ''))
    asm('dp_min_pa', DP_MIN, 'Pa', '供压高出过程压力此值以上才能达到全流量（低于时按比例减小）', '工程值')
    asm('purge_clear_s', PURGE_CLEAR_S, 's', '载气≥50 %满量程吹扫此时间后气路中的液体清除（厂家吹扫步骤约30 min，取5 min）', '工程值')
    asm('bleed_turns_rad', BLEED_TURNS_RAD, 'rad', '排气接头打开的旋转角（1.5圈）', '工程值')
    return P


CALIBRATED = {'ua_hx_W_K', 'ua_loss_W_K'}
SLOT_REFERENCE = {'setpoint.gas_m3_s': '标准状态（Bronkhorst“n”：0 °C、1 atm）体积流量', 'measured.gas_m3_s': '标准状态（0 °C、1 atm）体积流量',
                  'measured.temperature_k': '热力学温度（ITS-90）；PT100读数含约0.1 K/m的电缆偏差', 'setpoint.temperature_k': '热力学温度（ITS-90）',
                  'bleed.screw_angle_rad': '排气接头绕自身竖直轴（资产+Z）自关闭位置的旋开角'}


def export_bronkhorst_cemw_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'BronkhorstCemwSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.bronkhorst_cemw.BronkhorstCemwSession.invoke',
                                              invocation=f'session.invoke(instance_id="bronkhorst_cemw_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='BronkhorstCemwRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（流量、温度、蒸发与排气口在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['heat_exchanger', 'mixing_valve', 'mixing_block', 'valve_coil', 'liquid_inlet', 'gas_inlet', 'vapour_outlet', 'bleed_fitting'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='受控蒸发混合：液体流量计经CEM混合阀把液体雾化进载气，换热器（100 W、PT100、200 °C安全开关）把气溶胶蒸发成蒸气混合物；出口露点、蒸发分数与下游冷凝由能量平衡与饱和蒸气压决定',
        equations=['n_g = Q_n P_n / (R T_n), (T_n, P_n) = (273.15 K, 101325 Pa)',
                   'log10(p_sat / bar) = A - B / (T + C)  (one continuous fit of the NIST Antoine sets + normal boiling point);  dHvap = R ln10 B T^2 / (T + C)^2',
                   'dew point: sum_i y_i P / p_sat,i(T_dp) = 1;  s = min(1, n_g / (sum_i n_i P / p_sat,i(T_out) - sum_i n_i))',
                   'UA_hx (T_hx - T_out) = sum_i n_i [s (cp_l,i (T_out - T_in) + dHvap_i(T_out)) + (1 - s) cp_l,i (T_out - T_in)] + n_g cp_g (T_out - T_in)',
                   'C dT_hx/dt = P_heater - Q_stream - UA_loss (T_hx - T_amb);  P_heater = clip(Kp e + I, 0, 100 W), 0 above 200 C (safety switch)',
                   'outlet lag tau = V_hx / Q_actual;  downstream: s_line = min(1, n_g / (sum_i n_i P / p_sat,i(T_line) - sum_i n_i))'],
        parameters=_params(), source_ids=[_SIM, _WB, _BR, _MN, _FQ, _NI], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_cem_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('bronkhorst_cemw', caps, slots, {'controlled_evaporation_mixing': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'controlled_evaporation_mixing': mechanism})
