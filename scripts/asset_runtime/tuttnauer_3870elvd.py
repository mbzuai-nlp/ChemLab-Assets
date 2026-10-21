"""CHEM-192 Tuttnauer 3870ELV-D vertical laboratory autoclave (85 l): process runtime.

Device (3870ELV-D product page): top-loading vertical autoclave, 316L chamber 380 x 690 mm, 85 l, sterilization 105-138 C, 8 factory programs
(solids 134 C or 121 C, liquids and waste liquids 121 C), optional cooling coil + compressed air (selected), extended sterilization time up to
999 min, 200 stored cycles (USB / Ethernet / optional printer), automatic pressure lock (the lid does not open while the chamber is pressurised),
730 x 1000 x 540 mm, 113 kg, 3-phase.  Same-platform data (3870 ELV operation / technician manuals, labelled as such): 6 kW heater in the water
at the chamber bottom, water filled to the level electrode, two temperature sensors, maximum pressure 225 kPa abs., 204 kPa at 121 C, safety
valve 40 psig, exhaust modes (fast / slow liquids / slow waste / cooling with compressed air), program end temperatures, heat time-out, alarms
(Low Heat, High Temperature +3 C, High / Low Pressure +-20 kPa, No Water), 300 W heat loss, average cycle times of the liquid programs.

Process model (fixed 0.5 s grid anchored at t = 0, state held between grid points):
  * Chamber: one thermal node (water charge + vessel) at T_w; while water is present the gas space holds saturated steam (IAPWS-IF97
    saturation pressure) plus residual air n_air (ideal gas), P = p_sat(T_w) + n_air R T_w / V_gas.  Heating: 6 kW into the water (off at the
    225 kPa limit, without water - cut-out - or with a heater fault); while the air valve is open the chamber boils at atmospheric pressure, the
    vented steam purges the air (first-order), the valve closes after the purge time.  Heat goes to the loads by condensing steam
    (h_condense / h_liquid / h_waste), losses UA (T_w - T_amb), latent heat of the vented / flashed steam (Clausius-Clapeyron on IAPWS).
  * Programs: water inlet -> heating -> sterilization (timer runs while the chamber sensor is at or above the set temperature, on/off control)
    -> exhaust (fast: tau; slow liquids / waste: rate-limited pressure decay; cooling: coil + compressed air keeps P = factor x p_sat while the
    chamber and the loads cool) -> drain -> end (lid unlocks below the door pressure and at or below the program end temperature).
  * Loads: liquid containers (any liquid of the library; mass, nominal volume, container material, loose / sealed closure), solids (material,
    mass) and waste bags, each one lumped temperature; F0 = int 10^((T - 121.1)/10) dt; survivors log10 N = log10 N0 - int dt / D(T),
    D(T) = D121 10^((121.1 - T)/z); boil-over of loose containers above the boiling point at the chamber pressure, burst of sealed containers when
    the internal vapour + air pressure exceeds the chamber pressure by the burst limit, softening of plastics above their maximum use temperature.
  * Records: the last 200 cycles (program, set points, phase log, minimum / maximum hold temperature, chamber F0, result)."""
import copy
import json
import math
import re
import zlib
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_VERSION = "1.2.0"
SLUG = 'tuttnauer_3870elvd'
ASSET_TYPE_ID = "instrument.tuttnauer_3870elvd"
CATALOG_ID = "CHEM-192"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch03/t2_vacuum/tuttnauer_3870elvd'
LIBS = {'liquids': 'liquid_library.json', 'materials': 'load_material_library.json', 'organisms': 'organism_library.json', 'steam': 'steam_tables.json',
        'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def liquids():
    return _lib('liquids')['entries']


def materials():
    return _lib('materials')['entries']


def organisms():
    return _lib('organisms')['entries']


def instrument():
    return _lib('instrument')


DOC = {k: v['value'] for k, v in instrument()['documented'].items()}
ENG = {k: v['value'] for k, v in instrument()['engineering'].items()}
ENG_NOTE = {k: v['engineering_note_zh'] for k, v in instrument()['engineering'].items()}
ENG_UNIT = {k: v['unit'] for k, v in instrument()['engineering'].items()}
DT = 0.5                                       # process grid (s)
R_GAS = 8.314462618
M_W = 0.01801528                               # water molar mass (kg/mol)
CP_W = 4184.0                                  # liquid water heat capacity (J/(kg K)); NIST 75.375 J/(mol K) at 298 K
RHO_W = 1000.0
V_CH = DOC['chamber_volume_m3']
T_SET_RANGE = DOC['sterilization_temperature_range_k']
P_MAX_121 = DOC['max_pressure_pa']               # 225 kPa abs. at 121 C (technician manual example) = p_sat(121 C) + about 20 kPa
P_MARGIN = P_MAX_121 - 204e3                     # applied to every set temperature: no heating above p_sat(T_set) + 21 kPa
P_SAFETY = 101325.0 + DOC['safety_valve_gauge_pa']
LID_OPEN = 1.75                                # lid open angle (rad), engineering
LID_RATE = LID_OPEN / 2.0                      # lid swings in about 2 s
GAUGE_FULL = 4.0e5                             # mechanical gauge 0...4 bar g over 270 degrees (engineering)
GAUGE_SWEEP = 1.5 * math.pi
MAX_LIQUID_DENSITY = 1600.0                     # kg/m3: densest library liquids (chlorinated solvents) - a liquid mass above container volume x this cannot fit
BURST_DP = 1.5e5                               # sealed glass / plastic container burst over-pressure (engineering)
MAX_LOADS = 40
MAX_RECORDS = int(DOC['stored_cycles'])
MAX_EVENTS_LOG = 2000
FAULTS = ['heater_failure', 'air_valve_stuck_closed', 'door_gasket_leak', 'water_supply_off', 'pressure_sensor_fault', 'temperature_sensor_drift', 'pressure_sensor_drift',
          'heater_relay_stuck']
# Displayed messages of the ELV-D manual (rev D, 'Dépannage' table p.81-84, cycle-error list p.54) that no physical fault above produces: evaluator-only
# fault.inject(fault=<code>, active=True); active=False is the remedy.  Kinds: 'cycle' fails the running cycle with the message (Cycle failed, exhaust;
# refused without a running cycle); 'service' is latched (call service): a running cycle fails and starts are refused until cleared; 'notice' is a latched
# message only (clock.set also clears the RTC message).  English texts: the manual's screen pictures are English, these messages are only in its French
# table, so the English wording is our own translation (assumption).  Not injectable (absent hardware): the vacuum time error (no vacuum pump), door
# opening / closing errors (no motorised door), accessory stop time, and the water / distilled-water / compressed-air supply errors of the digital-input
# options (not fitted).
DOC_MESSAGES = {
    'msg_high_temp_end': ('cycle', 'High Temp. (End)', 'Temp. Élevée (Fin)', 'p.81: the chamber does not reach the required temperature within 10 minutes; run a new cycle'),
    'msg_high_temp_cooler': ('cycle', 'High Temp. (Cooler)', 'Temp. Élevée (Refroidisseur)', 'p.81: the cooling phase does not reach the temperature in the preset time; check the tap water supply'),
    'msg_heating_time_keep': ('cycle', 'Heating time error (keep)', 'Erreur dans le temps de chauffe (conserver)', 'p.82: the optional Keep Heat phase does not reach the temperature in time; check for overload'),
    'msg_low_pressure_cooling': ('cycle', 'Low pressure (cooling)', 'Pression basse (Refroidissement)', 'p.82: the pressure does not reach the level needed before cooling; check the compressed air supply'),
    'msg_high_pressure_end': ('cycle', 'High pressure (end)', 'Pression élevée (fin)', 'p.82: atmospheric pressure +-10 kPa not reached in the final phase; run a new cycle'),
    'msg_high_pressure_exhaust': ('cycle', 'High pressure (exhaust)', 'Pression élevée (évacuation)', 'p.82: the preset pressure is not reached 10 min after the exhaust starts; run a new cycle'),
    'msg_pressure_time_error': ('cycle', 'Pressure time error', 'Erreur dans le temps de pression', 'p.82-83: pressure conditions not reached in time during air removal; check for overload'),
    'msg_door_open_cycle': ('cycle', 'Door is open', 'La porte est ouverte (durant le cycle)', 'p.83: the door is open during the cycle; close the door and run a new cycle'),
    'msg_air_error': ('cycle', 'Air error', "Erreur d'air", 'p.83: atmospheric pressure not reached 10 minutes after the end of the cycle; wait, then run a new cycle'),
    'msg_rtc_set': ('notice', 'RTC error - set date and time', "Erreur dans l'HTR - veuillez saisir la date et l'heure actuelles", 'p.83: set the date and time; if it persists call the technician'),
    'msg_time_error': ('service', 'Time error', 'Erreur dans le temps (réel)', 'p.83: real-time clock error; call the technician'),
    'msg_maintenance_due': ('notice', 'Maintenance date passed - call service', 'Date de contrôle périodique dépassée', 'p.83: the periodic maintenance date has passed; call service'),
    'msg_cycle_counter_due': ('notice', 'Cycle counter exceeded - call service', 'Le compteur de cycle est dépassé', 'p.83-84: more cycles since the last maintenance than the Cycle Counter parameter; call service'),
    'msg_analog_input_temp': ('service', 'Analog input error', "Erreur d'entrée analogique", 'p.83: a temperature sensor is disconnected or out of range; call service'),
    'msg_chamber_temp_range': ('service', 'Chamber temp. out of range', 'La température de la chambre est hors plage', 'p.84: the chamber temperature is too high or low for the normal range; call service'),
    'msg_chamber_pressure_range': ('service', 'Chamber pressure out of range', 'La pression de la chambre est hors plage', 'p.84: the chamber pressure is too high or low for the normal range; call service'),
    'msg_io_board_failure': ('service', 'I/O board failure', 'Échec de la carte E/S', 'p.84: I/O board failure (cycle active or not); call service'),
    'msg_io_board_disconnected': ('service', 'I/O board not connected', "La carte E/S n'est pas connectée", 'p.84: I/O board disconnected (cycle active or not); call service'),
}
FAULTS = FAULTS + list(DOC_MESSAGES)
STAGES = ['idle', 'water_inlet', 'pulse', 'heating', 'sterilization', 'exhaust', 'cooling', 'drain', 'dry', 'complete', 'failed']
EXHAUST = ['fast', 'slow_liquids', 'slow_waste', 'cooling']
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


# ---------------------------------------------------------------- water / steam (IAPWS-IF97) and library liquids
_N = _lib('steam')['saturation']['n']


def psat_water(T):
    """IAPWS-IF97 saturation pressure (Pa), Eq. (30); valid 273.15...647.096 K."""
    T = min(max(T, 273.15), 647.0)
    th = T + _N[8] / (T - _N[9])
    a = th * th + _N[0] * th + _N[1]
    b = _N[2] * th * th + _N[3] * th + _N[4]
    c = _N[5] * th * th + _N[6] * th + _N[7]
    return 1e6 * (2 * c / (-b + math.sqrt(b * b - 4 * a * c))) ** 4


def tsat_water(P):
    """Saturation temperature (K) at pressure P (Pa), by inverting Eq. (30)."""
    lo, hi = 273.15, 640.0
    P = max(P, 611.3)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if psat_water(mid) < P:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def hfg_water(T):
    """Latent heat (J/kg) from Clausius-Clapeyron on the IAPWS saturation line (ideal vapour; +1.6 / +2.6 / +3.4 % against the steam tables at 100 / 121 / 134 C)."""
    h = 1e-3
    return R_GAS * T * T * (math.log(psat_water(T + h)) - math.log(psat_water(T - h))) / (2 * h) / M_W


def _antoine(sp, T):
    f = liquids()[sp]['antoine_fit']
    return 1e5 * 10.0 ** (f['A'] - f['B'] / (T + f['C']))


def hvap_molar(sp, T):
    """Enthalpy of vaporization (J/mol): Watson relation anchored at the library's dhvap_reference (median of the NIST rows at the boiling point)
    when the critical temperature is known, else Clausius-Clapeyron on the continuous Antoine fit."""
    e = liquids()[sp]
    tc, ref = e.get('critical_temperature_K'), e.get('dhvap_reference')
    if tc and ref and ref['T_K'] < tc - 5.0:
        return ref['value_J_mol'] * (max(tc - T, 0.5) / (tc - ref['T_K'])) ** 0.38
    f = e['antoine_fit']
    return R_GAS * math.log(10.0) * f['B'] * T * T / max((T + f['C']) ** 2, 900.0)


def psat_liquid(sp, T):
    """Vapour pressure (Pa) of a library liquid: continuous NIST Antoine fit; below T = 30 - C (fits with a large negative C) Clausius-Clapeyron
    continuation with the slope at that point; a degenerate fit over a narrow range (< 25 K) only inside its range, outside Clausius-Clapeyron from
    the nearer edge with the Watson enthalpy."""
    if sp == 'water':
        return psat_water(T)
    f = liquids()[sp]['antoine_fit']
    lo, hi = f['t_range_K']
    if hi - lo < 25.0:
        if lo <= T <= hi:
            return _antoine(sp, T)
        te = lo if T < lo else hi
        return _antoine(sp, te) * math.exp(-hvap_molar(sp, 0.5 * (T + te)) / R_GAS * (1.0 / T - 1.0 / te))
    t0 = 30.0 - f['C']
    if T < t0:
        return 1e5 * 10.0 ** (f['A'] - f['B'] / 30.0) * math.exp(-math.log(10.0) * f['B'] * t0 * t0 / 900.0 * (1.0 / T - 1.0 / t0))
    return _antoine(sp, T)


def tsat_liquid(sp, P):
    lo, hi = 150.0, 700.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if psat_liquid(sp, mid) < P:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def cp_mass(sp):
    if sp == 'water':
        return CP_W
    e = liquids()[sp]
    return e['cp_liquid']['value_J_mol_K'] / e['molar_mass_kg_mol']


def hfg_liquid(sp, T):
    if sp == 'water':
        return hfg_water(T)
    return hvap_molar(sp, T) / liquids()[sp]['molar_mass_kg_mol']


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
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- programs (same-platform defaults; 3870ELV-D programs named on the product page)
PROGRAMS = {
    'solids_134': dict(name_zh='固体与玻璃器材134 °C', temperature_k=407.15, time_s=240.0, exhaust='fast', end_temperature_k=393.15, editable=False),
    'solids_121': dict(name_zh='不耐高温固体（塑料）121 °C', temperature_k=394.15, time_s=1200.0, exhaust='fast', end_temperature_k=393.15, editable=False),
    'liquids_121': dict(name_zh='液体121 °C（慢排）', temperature_k=394.15, time_s=1200.0, exhaust='slow_liquids', end_temperature_k=373.15, editable=False),
    'liquids_121_cooling': dict(name_zh='液体121 °C＋冷却（冷却盘管＋压缩空气）', temperature_k=394.15, time_s=1200.0, exhaust='cooling', end_temperature_k=363.15,
                                editable=False),
    'waste_121': dict(name_zh='废物（液体废物）121 °C（慢排）', temperature_k=394.15, time_s=1800.0, exhaust='slow_waste', end_temperature_k=358.15, editable=False),
    'extended_time': dict(name_zh='延长灭菌时间（最长999 min）', temperature_k=394.15, time_s=3600.0, exhaust='slow_liquids', end_temperature_k=373.15, editable=True),
    'custom_1': dict(name_zh='用户程序1', temperature_k=394.15, time_s=900.0, exhaust='fast', end_temperature_k=393.15, editable=True),
    'custom_2': dict(name_zh='用户程序2', temperature_k=394.15, time_s=900.0, exhaust='slow_liquids', end_temperature_k=373.15, editable=True),
}
HEAT_TIMEOUT = {'fast': 3600.0, 'slow_liquids': 3600.0, 'slow_waste': 5400.0, 'cooling': 5400.0}
LEGACY_PROGRAMS = list(PROGRAMS)                                           # program.next keeps stepping through this earlier list (API)
# the 3870ELV-D's own programme list and cycle-parameter table (French operation manual rev D: section 5, table 6.2): the main screen's
# UP / DOWN select among these ten; the earlier keys above stay selectable through program.select.  Pressures are absolute.
NEUTRAL = dict(pulse_a_count=0, pulse_a_hold_s=10.0, pulse_a_low_pa=130e3, pulse_a_high_pa=180e3, pulse_b_count=0, pulse_b_hold_s=2.0, pulse_b_low_pa=160e3,
               pulse_b_high_pa=180e3, f0_mode=False, cooling_end_k=None, cooling_exhaust_rate_pa_s=None, dry1_on_s=4.0, dry1_off_s=12.0, dry_stage1_s=0.0,
               dry2_on_s=2.0, dry2_off_s=20.0, repeat_cycles=1, repeat_pause_s=120.0)
CUSTOM_TABLE = dict(NEUTRAL, pulse_a_count=3, cooling_end_k=353.15, cooling_exhaust_rate_pa_s=5e3, dry_stage1_s=300.0)   # table 6.2 (Custom 1 example)
for _v in PROGRAMS.values():
    _v.update(NEUTRAL)
_INSTR = dict(exhaust='fast', end_temperature_k=393.15, editable=False, **dict(NEUTRAL, pulse_a_count=1))
ELVD_PROGRAMS = {
    'unwrapped_instruments': dict(name_zh='1 未包装器械134 °C', temperature_k=407.15, time_s=240.0, **_INSTR),
    'wrapped_instruments': dict(name_zh='2 包装器械134 °C', temperature_k=407.15, time_s=420.0, **_INSTR),
    'unwrapped_pouches': dict(name_zh='3 未包装灭菌袋134 °C', temperature_k=407.15, time_s=420.0, **_INSTR),
    'wrapped_pouches': dict(name_zh='4 包装灭菌袋134 °C', temperature_k=407.15, time_s=420.0, **_INSTR),
    'delicate_unwrapped': dict(name_zh='5 未包装精细器械121 °C', temperature_k=394.15, time_s=1200.0, **_INSTR),
    'delicate_wrapped': dict(name_zh='6 包装易碎器械121 °C', temperature_k=394.15, time_s=1200.0, **_INSTR),
    'custom_1': dict(name_zh='7 用户程序1', temperature_k=407.15, time_s=240.0, exhaust='fast', end_temperature_k=393.15, editable=True, **CUSTOM_TABLE),
    'custom_2': dict(name_zh='8 用户程序2', temperature_k=394.15, time_s=1200.0, exhaust='fast', end_temperature_k=393.15, editable=True, **CUSTOM_TABLE),
    'liquid_a': dict(name_zh='9 液体A 121 °C（强制冷却到95 °C）', temperature_k=394.15, time_s=1200.0, exhaust='cooling', end_temperature_k=368.15, editable=False,
                     **dict(NEUTRAL, pulse_a_count=1, cooling_end_k=368.15, cooling_exhaust_rate_pa_s=5e3)),
    'liquid_b': dict(name_zh='10 液体B 121 °C（强制冷却到85 °C）', temperature_k=394.15, time_s=1800.0, exhaust='cooling', end_temperature_k=358.15, editable=False,
                     **dict(NEUTRAL, pulse_a_count=1, cooling_end_k=358.15, cooling_exhaust_rate_pa_s=5e3)),
}
PROGRAMS.update(ELVD_PROGRAMS)
ELVD_ORDER = list(ELVD_PROGRAMS)                                           # programme numbers 1-10 of the main screen
PROGRAM_NAMES = {'unwrapped_instruments': 'Unwrapped instruments', 'wrapped_instruments': 'Wrapped instruments', 'unwrapped_pouches': 'Unwrapped pouches',
                 'wrapped_pouches': 'Wrapped pouches', 'delicate_unwrapped': 'Delicate unwrapped', 'delicate_wrapped': 'Delicate wrapped', 'custom_1': 'Custom 1',
                 'custom_2': 'Custom 2', 'liquid_a': 'Liquid A', 'liquid_b': 'Liquid B', 'solids_134': 'Solids 134 (ELV)', 'solids_121': 'Solids 121 (ELV)',
                 'liquids_121': 'Liquids 121 (ELV)', 'liquids_121_cooling': 'Liquids + cooling (ELV)', 'waste_121': 'Waste 121 (ELV)', 'extended_time': 'Extended time (ELV)'}
TABLE_KEYS = [k for k in NEUTRAL]
EPOCH = '2026-10-19T08:00:00'                                              # wall clock at t = 0 (set with clock.set)
GAINS_OFFSETS = {'drain_temperature': (0.04, -4.0), 'chamber_temperature': (0.04, -4.0), 'reference_temperature_1': (0.04, -4.0), 'chamber_pressure': (0.125, -100.0)}
MAX_USERS = 30
MAX_USB_FILES = 50


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.:-]+$')
LIQ = dict(type='string', minLength=1, maxLength=60, pattern='^[a-z0-9_]+$')
PLANT_EXAMPLE = dict(ambient_k=295.15, atmospheric_pressure_pa=101325.0, water_supply=True, compressed_air=True, cooling_water=True, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', 283.15, 313.15), atmospheric_pressure_pa=number('Pa', 80000.0, 106000.0), water_supply=BOOL, compressed_air=BOOL,
                 cooling_water=BOOL, noise_seed=integer(0, 2 ** 31 - 1)))
LOAD = obj(dict(load_id=SID, kind=enum(['liquid', 'solid', 'waste']), mass_kg=number('kg', 0.001, 25.0), liquid=LIQ, container_volume_m3=number('m3', 1e-6, 0.01),
                material=enum(sorted(materials())), closure=enum(['loose', 'sealed', 'none']), organism=enum(sorted(organisms())),
                log10_n0=number('1', 0.0, 12.0)), optional=('liquid', 'container_volume_m3', 'closure', 'organism', 'log10_n0'))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'cycle', 'load', 'alarm', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
RECORD = FREE_OBJ
_BODY = ['autoclave_cabinet']
_CH = ['pressure_chamber']
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _BODY,
    '宿主声明环境温度与大气压、纯水供应、压缩空气与冷却水（冷却选件）以及噪声种子', 'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['main_switch', 'control_console'], '主电源通/断（断电时进行中的周期失败，加热停止）', 'agent', 'device_command')
cap('power.press_switch', obj({}), obj(dict(on=BOOL)), {}, ['main_switch'], '按主电源翻转开关（等价于power.set取反）', 'agent', 'device_command')
cap('lid.set', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['chamber_lid', 'lid_handle'],
    '打开/关闭顶盖（腔内有压、周期进行中或腔温高于结束温度时自动压力锁不放开）', 'agent', 'robot_skill')
cap('load.add', LOAD, obj(dict(load_id=TEXT, notes=array(TEXT, 0, 8))), dict(load_id='media_1', kind='liquid', mass_kg=0.7, liquid='water', container_volume_m3=1e-3,
                                                                            material='borosilicate_glass', closure='loose'), ['load_basket'],
    '把负载放进灭菌腔：液体（液体库物种、质量、容器标称体积、材料与盖子松/紧）、固体（材料与质量）或废物袋；可附生物指示物（物种与初始对数）', 'agent', 'robot_skill')
cap('load.remove', obj(dict(load_id=SID)), obj(dict(load_id=TEXT, state=FREE_OBJ)), dict(load_id='media_1'), ['load_basket'],
    '取出负载（顶盖须打开）；返回取出时的可见状态（是否破裂、溢出、变形）', 'agent', 'robot_skill')
cap('program.select', obj(dict(program=enum(sorted(PROGRAMS)))), obj(dict(program=TEXT, parameters=FREE_OBJ)), dict(program='liquids_121'), ['control_console'],
    '选择灭菌程序：3870ELV-D的10个程序（未包装/包装器械与灭菌袋134 °C、精细器械121 °C、用户程序1/2、液体A/B）或早期ELV程序键（固体134/121 °C、液体121 °C慢排、液体＋冷却、废物、延长时间）', 'agent', 'configuration')
cap('program.next', obj({}), obj(dict(program=TEXT)), {}, ['button_select', 'control_console'], '面板“程序”键：切换到下一个程序', 'agent', 'device_command')
cap('program.configure', obj(dict(program=enum(sorted(k for k, v in PROGRAMS.items() if v['editable'])), temperature_k=number('K', T_SET_RANGE[0], T_SET_RANGE[1]),
                                  time_s=number('s', 60.0, DOC['extended_time_max_s']), exhaust=enum(EXHAUST), end_temperature_k=number('K', 313.15, 410.15)),
                             optional=('temperature_k', 'time_s', 'exhaust', 'end_temperature_k')),
    obj(dict(program=TEXT, parameters=FREE_OBJ)), dict(program='extended_time', time_s=7200.0), ['control_console'],
    '修改可编辑程序（延长时间、用户程序）的灭菌温度（105–138 °C）、时间（最长999 min）、排气方式与结束温度', 'agent', 'configuration')
cap('cycle.start', obj({}), obj(dict(cycle_number=integer(0, 10 ** 9), program=TEXT, operation_id=TEXT)), {}, ['button_start', 'control_console'],
    '开始所选程序的周期（顶盖须关闭并锁定；有纯水供应）', 'agent', 'device_command')
cap('cycle.stop', obj({}), obj(dict(cycle_number=integer(0, 10 ** 9), stage=TEXT)), {}, ['button_start', 'control_console'],
    '中止周期（Manual Stop：转入排气，记录为失败）', 'agent', 'device_command')
cap('cycle.toggle', obj({}), obj(dict(running=BOOL)), {}, ['button_start'], '面板“开始/停止”键：空闲时开始、运行时中止', 'agent', 'device_command')
cap('records.read', obj(dict(last=integer(1, MAX_RECORDS)), optional=('last',)), obj(dict(records=array(RECORD, 0, MAX_RECORDS), stored=integer(0, MAX_RECORDS))),
    dict(last=1), ['control_console'], '读取机内保存的周期记录（最多200个；程序、设定、分段温度/压力记录、保持段最高/最低温度、腔体F0、结果）', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=integer(0, 10 ** 9))), dict(since_s=0.0), _BODY,
    '读取事件记录（agent看不到故障与真值事件）', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(chamber=FREE_OBJ, loads=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, _BODY,
    '评测者读取过程真值：腔内水量、空气残留、蒸汽压、各负载中心温度、F0、存活对数与损坏', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _BODY, '水账与负载质量账：进水、排汽、闪蒸、排水、负载溢出与破裂（kg）', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 32))), dict(fault='air_valve_stuck_closed', active=True), _BODY,
    '故障注入：加热器损坏、排气阀卡死（空气排不净）、门封泄漏、纯水断供、压力传感器故障、温度传感器漂移（+1.5 K）、压力传感器漂移（−30 kPa）、加热接触器粘连；'
    '以及手册p.81-84列出的显示消息（msg_*）：周期类使运行中的周期失败并显示该消息（无周期时NO_CYCLE），需维修类锁存（运行中周期失败、不能开始，SERVICE_REQUIRED），'
    '提示类只显示；active=false为排除原因（RTC消息也由clock.set清除）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), _BODY, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'vacuum.prevacuum': '预真空/后真空周期需要真空泵选件（未选）。', 'bowie_dick.test': 'Bowie & Dick蒸汽穿透测试需要真空泵＋蒸汽发生器选件（未选）。',
    'media.isothermal': '培养基等温处理（60–95 °C）与保温程序为特殊程序选件（未选）。', 'biohazard_filter.enable': '生物危害排气0.2 µm过滤为选件（未选）。',
    'printer.print': '内置打印机为选件（未选）；记录经records.read读取。', 'rpcr.monitor': 'RPCR远程监控软件（以太网）为选件与宿主软件。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _BODY, _v, 'agent', 'device_command')
BASE_CAPS = list(CAPS)
# ---- the ELV-D controller's own menu functions (French operation manual rev D, sections 6 and 10); the front panel calls these
_ED = ['button_select', 'button_down', 'control_console']
_HHMM = dict(type='string', pattern='^([01][0-9]|2[0-3]):[0-5][0-9]$')
cap('program.step', obj(dict(direction=enum(['next', 'previous']))), obj(dict(program=TEXT, number=integer(1, 10))), dict(direction='next'), _ED,
    '主屏UP/DOWN：在3870ELV-D的10个程序中选下一个/上一个（门须打开：门关着时控制器不许换程序）', 'agent', 'device_command')
TABLE_SCHEMA = dict(pulse_a_count=integer(0, 10), pulse_a_hold_s=number('s', 1.0, 100.0), pulse_a_low_pa=number('Pa', 5e3, 200e3), pulse_a_high_pa=number('Pa', 5e3, 200e3),
                    pulse_b_count=integer(0, 10), pulse_b_hold_s=number('s', 1.0, 100.0), pulse_b_low_pa=number('Pa', 5e3, 200e3), pulse_b_high_pa=number('Pa', 5e3, 200e3),
                    f0_mode=BOOL, cooling_mode=integer(0, 3), cooling_end_k=number('K', 303.15, 393.15), cooling_exhaust_rate_pa_s=number('Pa/s', 1e3, 100e3),
                    exhaust_mode=integer(1, 2), dry1_on_s=number('s', 0.0, 120.0), dry1_off_s=number('s', 0.0, 120.0), dry_stage1_s=number('s', 0.0, 7200.0),
                    dry2_on_s=number('s', 0.0, 120.0), dry2_off_s=number('s', 0.0, 120.0), repeat_cycles=integer(1, 50), repeat_pause_s=number('s', 60.0, 3600.0))
cap('program.set_parameters', obj(dict(program=enum(['custom_1', 'custom_2']), **TABLE_SCHEMA), optional=tuple(TABLE_SCHEMA)), obj(dict(program=TEXT, parameters=FREE_OBJ)),
    dict(program='custom_1', pulse_a_count=2, f0_mode=True), ['control_console'],
    '用户程序1/2的周期参数（表6.2）：脉冲组A/B次数、保持时间与低/高压、F0模式、冷却方式（0无/1强制）、冷却结束温度、冷却后排气速率、排气方式（1快/2慢）、干燥两段加热通/断时间与第一段时长、多次循环次数与间隔',
    'agent', 'configuration')
cap('settings.configure', obj(dict(print_rate_total_s=number('s', 60.0, 1800.0), print_rate_sterilization_s=number('s', 60.0, 1800.0),
                                   screensaver_s=number('s', 0.0, 36000.0)), optional=('print_rate_total_s', 'print_rate_sterilization_s', 'screensaver_s')),
    obj(dict(settings=FREE_OBJ)), dict(screensaver_s=3600.0), ['control_console'], '系统参数：打印间隔（全程/灭菌段，1–30 min）与屏幕保护延时（0–600 min）', 'agent', 'configuration')
cap('clock.set', obj(dict(date=dict(type='string', pattern='^[0-9]{2}:[0-9]{2}:[0-9]{2}$'), time=dict(type='string', pattern='^[0-9]{2}:[0-9]{2}:[0-9]{2}$')),
                     optional=('date', 'time')), obj(dict(wall=TEXT)), dict(date='20:10:26', time='09:30:00'), ['control_console'],
    '维护菜单“设定日期与时间”：时间HH:MM:SS（24 h）与日期DD:MM:YY', 'agent', 'configuration')
cap('timer.configure', obj(dict(enabled=BOOL, time=_HHMM), optional=('time',)), obj(dict(timer=FREE_OBJ)), dict(enabled=True, time='07:30'), ['control_console'],
    '高级选项“定时启动周期”：设定启动时刻HH:MM并启用/停用（启用时到点自动开始所选程序，门须关闭；启动后自动停用）', 'agent', 'configuration')
cap('user.sign_in', obj(dict(user=dict(type='string', minLength=1, maxLength=20), code=dict(type='string', pattern='^[0-9]{4}$'))), obj(dict(user=TEXT)),
    dict(user='Admin', code='0001'), ['control_console'], '“选择用户”后输入4位代码进入主菜单（Admin出厂代码0001）', 'agent', 'query')
cap('user.create', obj(dict(code=dict(type='string', pattern='^[0-9]{4}$'))), obj(dict(user=TEXT)), dict(code='1234'), ['control_console'],
    '“选择用户 > Create new...”：以新的4位代码建立用户（最多30个ID代码）', 'agent', 'configuration')
cap('usb.export', obj(dict(content=enum(['history', 'gains_offsets', 'parameters']))), obj(dict(file=FREE_OBJ)), dict(content='history'), ['control_console'],
    '导出到USB存储：周期历史、增益与补偿、全部参数（USB存储已插在USB口）', 'agent', 'device_command')
APP_CAPS = [k for k in CAPS if k not in BASE_CAPS]


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'autoclave_cabinet', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'autoclave_cabinet', '环境条件是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'main_switch', '主电源是否接通')
slot('lid.angle_rad', number('rad'), 'rad', 'derived', 'chamber_lid', '顶盖开角（0关，1.75 rad开；约2 s转完）')
slot('lid.locked', BOOL, '1', 'derived', 'chamber_lid', '自动压力锁是否锁住顶盖')
slot('chamber.temperature_k', nullable(number('K')), 'K', 'sensor', 'pressure_chamber', '温度传感器1（腔体，控制用）读数；断电为空')
slot('chamber.temperature2_k', nullable(number('K')), 'K', 'sensor', 'pressure_chamber', '温度传感器2读数：有液体负载时为参考瓶探头，否则为腔体第二传感器；断电为空')
slot('chamber.pressure_pa', nullable(number('Pa')), 'Pa', 'sensor', 'pressure_chamber', '腔压读数（绝压）；断电为空')
slot('gauge.needle_rad', number('rad'), 'rad', 'derived', 'pressure_gauge', '机械压力表指针角（0–4 bar表压对应0–1.5π，断电也指示）')
slot('display.temperature_c', nullable(number('1')), '1', 'derived', 'control_display', '显示屏上的腔温数值（°C，一位小数；断电为空）')
slot('program.current', FREE_OBJ, '1', 'setpoint', 'control_console', '当前程序：名称、灭菌温度、时间、排气方式、结束温度')
slot('cycle.status', FREE_OBJ, '1', 'derived', 'control_console', '周期状态：周期号、阶段、阶段已用时间、剩余灭菌时间、结果')
slot('water.level_ok', BOOL, '1', 'sensor', 'water_electrode', '腔底水位电极是否浸在水中')
slot('loads.present', array(TEXT, 0, MAX_LOADS), '1', 'derived', 'load_basket', '腔内负载标识')
slot('records.stored', integer(0, MAX_RECORDS), '1', 'derived', 'control_console', '机内保存的周期记录数（最多200）')
slot('alarms.active', array(TEXT, 0, 32), '1', 'derived', 'control_console', '当前报警（Low Heat、High/Low Temperature、High/Low Pressure、No Water、No Res Src、门未锁、门开、手动停止等）')
slot('truth.chamber', FREE_OBJ, '1', 'process_truth', 'pressure_chamber', '腔体真值：水量、水/腔壁温度、残留空气、蒸汽分压、加热功率、排气阀与安全阀', 'evaluator')
slot('truth.loads', FREE_OBJ, '1', 'process_truth', 'load_basket', '各负载真值：中心温度、F0、存活对数、质量损失、破裂与变形', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'autoclave_cabinet', '已注入的故障', 'evaluator')
BASE_SLOTS = list(SLOTS)
slot('program.parameters', FREE_OBJ, '1', 'setpoint', 'control_console', '当前程序的完整参数（程序号、显示名、表6.2的周期参数：脉冲、F0模式、冷却、干燥、多次循环）')
slot('cycle.phase', nullable(TEXT), '1', 'derived', 'control_console', '显示屏上的阶段名（Insert water、Pulse H/L、Heat、Sterilization、Cooling、Exhaust、Drain、Dry、Cycle completed/failed）；无周期为空')
slot('cycle.repeats_left', integer(0, 50), '1', 'derived', 'control_console', '多次循环中当前周期之后还要运行的周期数')
slot('clock.wall', TEXT, '1', 'derived', 'control_console', '控制器时钟（日期与时间，YYYY-MM-DD HH:MM:SS）')
slot('settings.system', FREE_OBJ, '1', 'setpoint', 'control_console', '系统参数：打印间隔（全程/灭菌段）与屏幕保护延时')
slot('timer.setting', FREE_OBJ, '1', 'setpoint', 'control_console', '定时启动：是否启用、启动时刻、是否已到点等待关门')
slot('users.names', array(TEXT, 0, MAX_USERS), '1', 'setpoint', 'control_console', '控制器里的用户名（代码不公开）')
slot('usb.files', array(FREE_OBJ, 0, MAX_USB_FILES), '1', 'derived', 'control_console', '导出到USB存储的文件（名称、内容类型、导出时刻、条目数）')
SCENE_MIRRORED = {'lid.angle_rad', 'gauge.needle_rad', 'display.temperature_c'}


# ---------------------------------------------------------------- front panel (panel_ui.json beside the contract, run by the shared engine _panel_ui.py)
def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.tuttnauer_3870elvd (tests, bridges)
        return engine
    except ImportError:                                                     # loaded from its file path
        import importlib.util
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


import os  # noqa: E402
_panel_ui = _panel_engine()
PANEL_SPEC = None if os.environ.get('TUTTNAUER_PANEL_BUILD') else _panel_ui.load_spec(ASSET_DIR / 'panel_ui.json', capabilities=set(CAPS))
PANEL_CAPS = _panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}   # panel.press / switch / capture / frame (kept out of CAPS:
ALL_CAPS = dict(CAPS, **PANEL_CAPS)                                                # CAPS stays the instrument's own command set)


def _hook_code(panel, ctx):
    return ''.join(str(int(panel.ui['vars'][f'c{i}'])) for i in range(4))


def _hook_sign_in(panel, ctx, a):
    """ENTER CODE > Set: the four digits as the user's code (the device checks it)."""
    return panel._invoke('user.sign_in', dict(user=panel.ui['vars']['usr'], code=_hook_code(panel, ctx)), None, ctx)


def _hook_create_user(panel, ctx, a):
    return panel._invoke('user.create', dict(code=_hook_code(panel, ctx)), None, ctx)


def _hook_clock_text(panel, ctx, a):
    """The digit fields of the date / time and timed-cycle screens as the command's text (DD:MM:YY, HH:MM:SS, HH:MM)."""
    v = panel.ui['vars']
    parts = {'date': ('dt_d', 'dt_mo', 'dt_y'), 'time': ('dt_h', 'dt_m', 'dt_s'), 'timer': ('tm_h', 'tm_m')}[a['which']]
    return ':'.join(f'{int(v[k]):02d}' for k in parts)


PANEL_HOOKS = {'sign_in': _hook_sign_in, 'create_user': _hook_create_user, 'clock_text': _hook_clock_text}


class _Panel(_panel_ui.PanelUI):
    """The panel's UI state belongs to the instrument: two panels are equal when their UI states are (state snapshots compare runtimes)."""
    def __eq__(self, o):
        return isinstance(o, _panel_ui.PanelUI) and self.ui == o.ui
    __hash__ = None


# ---------------------------------------------------------------- runtime
class TuttnauerElvdRuntime:
    def __init__(self, instance_id='tuttnauer_3870elvd_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.powered = False
        self.lid_open = False
        self.lid_angle = 0.0
        self.m_w = 0.0                         # water in the chamber (kg)
        self.T_w = None                        # water + vessel temperature (K)
        self.n_air = 0.0                       # air in the gas space (mol)
        self.air_valve = False
        self.vent_s = 0.0
        self.P_heat = 0.0
        self.loads = {}
        self.program = 'liquids_121'
        self.params = {k: dict(v) for k, v in PROGRAMS.items()}
        self.cycle = None
        self.cycles_run = 0
        self.records = []
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.warnings = {}
        self.revision = 0
        self.led = dict(water_supplied_kg=0.0, steam_vented_kg=0.0, flash_vented_kg=0.0, water_drained_kg=0.0, leak_kg=0.0, load_boilover_kg=0.0, load_burst_kg=0.0,
                        load_added_kg=0.0, load_removed_kg=0.0)
        self._principals, self._requests, self._caller = {}, {}, None
        self._cycle_owner = self._timer_owner = None                        # caller rule: who started the cycle / programmed the timed start
        self._op_owners = {}                                                # operation id of each cycle -> its owner (poll / cancel)
        self._elvd_init()

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
        return TuttnauerElvdSession(self, name)

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
    def _P_atm(self):
        return self.plant['atmospheric_pressure_pa']

    def _v_gas(self):
        v_loads = sum(L['volume_m3'] for L in self.loads.values())
        return max(V_CH - self.m_w / RHO_W - v_loads, 0.01)

    def _pressure(self):
        if self.T_w is None:
            return self._P_atm() if self.plant else 101325.0
        p_steam = psat_water(self.T_w) if self.m_w > 1e-6 else 0.0
        return p_steam + self.n_air * R_GAS * self.T_w / self._v_gas()

    def _p_set(self):
        return psat_water(self.params[self.program]['temperature_k'])

    def _p_max(self):
        """Maximum heating pressure of the running program: the sterilization pressure plus the margin of the documented example (225 kPa at 121 C)."""
        return psat_water(self.params[self.program]['temperature_k']) + P_MARGIN

    def _locked(self):
        if self.plant is None:
            return False
        running = self.cycle is not None and self.cycle['stage'] not in ('complete', 'failed')
        end_t = self.params[self.program]['end_temperature_k']
        return bool(running or self._pressure() - self._P_atm() > ENG['door_open_pressure_pa'] or (self.T_w is not None and self.T_w > max(end_t, 373.15) + 0.5
                                                                                                 and self._pressure() - self._P_atm() > 0.0))

    def _area(self, L):
        if L['kind'] == 'liquid':
            v = L['container_volume_m3']
            return 6 * math.pi * (v / (2 * math.pi)) ** (2.0 / 3.0)        # cylinder H = 2 D
        return None

    def _h(self, L, steam):
        if L['kind'] == 'liquid':
            return ENG['h_liquid_bottle_W_m2K'] if steam else ENG['h_air_bottle_W_m2K']
        return None

    def _load_ua(self, L, steam):
        """W/K between the chamber and a load."""
        if L['kind'] == 'liquid':
            return self._h(L, steam) * self._area(L)
        m = L['mass_kg']
        if L['kind'] == 'waste':
            tau = 1500.0 * (m / 2.0) ** (1.0 / 3.0)                          # bagged waste: poor steam penetration; time constant ~ size (engineering, 2 kg ref.)
        else:
            tau = {'stainless_316': 40.0, 'borosilicate_glass': 90.0}.get(L['material'], 150.0) * (m / 1.0) ** (1.0 / 3.0)   # 1 kg reference
        tau = tau if steam else tau * 6.0
        return m * L['cp'] / tau

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
            self.t = self.t_grid
            self._schedule()
        self.t = t_end
        self._update_warnings()
        return self.t

    def _tick(self, h):
        pl = self.plant
        T_amb, P_atm = pl['ambient_k'], self._P_atm()
        # lid
        tgt = LID_OPEN if self.lid_open else 0.0
        self.lid_angle += max(-LID_RATE * h, min(LID_RATE * h, tgt - self.lid_angle))
        C = self.cycle
        stage = C['stage'] if C else 'idle'
        if self.lid_open or self.lid_angle > 1e-6 or stage in ('water_inlet', 'dry') or (stage == 'heating' and self.air_valve):
            self._equalize_with_atmosphere()
        P = self._pressure()
        steam = self.m_w > 1e-6 and psat_water(self.T_w) > 0.5 * P and P > 0.9 * P_atm and stage in ('pulse', 'heating', 'sterilization', 'exhaust', 'drain')
        # ---- water inlet
        if stage == 'water_inlet':
            if 'water_supply_off' in self.faults or not pl['water_supply']:
                self._fail('NO_RES_SRC', '没有纯水供应（No Res Src）')
            else:
                dm = min(ENG['water_fill_rate_m3_s'] * RHO_W * h, ENG['water_charge_m3'] * RHO_W - self.m_w)
                if dm > 0:
                    self._mix_water(dm, T_amb)
                    self.led['water_supplied_kg'] += dm
                if self.m_w >= ENG['water_charge_m3'] * RHO_W - 1e-9:
                    if self.params[self.program]['pulse_a_count'] + self.params[self.program]['pulse_b_count'] > 0:
                        self._stage('pulse')                                    # ELV-D air pulses: heat to the high pressure, vent to the low one
                        C['pulse'] = dict(group='a', n=0, phase='H', hold_s=0.0)
                        if not self.params[self.program]['pulse_a_count']:
                            C['pulse']['group'] = 'b'
                    else:
                        self._begin_heating()
        # ---- heater
        P_cmd = 0.0
        par = self.params[self.program]
        pulse_h = stage == 'pulse' and C['pulse']['phase'] == 'H'
        if self.powered and C and stage == 'dry':
            P_cmd = DOC['heater_power_w'] if 'heater_failure' not in self.faults and self._dry_heater_on(C, par) and self.T_w < ENG['cut_out_dry_k'] else 0.0
        elif self.powered and C and (stage in ('heating', 'sterilization') or pulse_h) and 'heater_relay_stuck' in self.faults and 'heater_failure' not in self.faults:
            P_cmd = DOC['heater_power_w'] if self.m_w > 0.25 * ENG['water_charge_m3'] * RHO_W else 0.0    # contactor welded: the controller cannot switch off
        elif self.powered and C and (stage in ('heating', 'sterilization') or pulse_h) and 'heater_failure' not in self.faults:
            if self.m_w < 0.25 * ENG['water_charge_m3'] * RHO_W or self.T_w > ENG['cut_out_dry_k']:
                P_cmd = 0.0                                                  # cut-out: no water above the heater
            elif stage in ('heating', 'pulse'):
                P_cmd = DOC['heater_power_w'] if P < self._p_max() else 0.0
            else:
                band = ENG['controller_band_k']
                T_ctl = self._t1_true_reading()                                 # the controller sees sensor 1 (a drifting sensor shifts the real hold)
                if T_ctl < par['temperature_k'] + 0.3 - band / 2:
                    C['heater_on'] = True
                elif T_ctl > par['temperature_k'] + 0.3 + band / 2:
                    C['heater_on'] = False
                P_cmd = DOC['heater_power_w'] if C.get('heater_on') and P < self._p_max() else 0.0
        self.P_heat = P_cmd
        # ---- loads exchange with the chamber
        q_loads = 0.0
        for L in self.loads.values():
            if L['mass_kg'] <= 0:
                continue
            ua = self._load_ua(L, steam)
            a = 1 - math.exp(-ua * h / (L['mass_kg'] * L['cp']))
            dT = (self.T_w - L['T']) * a
            L['T'] += dT
            q_loads += L['mass_kg'] * L['cp'] * dT / h
        q_loss = ENG['ua_loss_W_K'] * (self.T_w - T_amb)
        if 'door_gasket_leak' in self.faults and P > P_atm + 2e3:
            m_leak = 2.0e-4 * min(1.0, (P - P_atm) / 1e5) * h
            m_leak = min(m_leak, self.m_w)
            self.m_w -= m_leak
            self.led['leak_kg'] += m_leak
            q_loss += m_leak * hfg_water(self.T_w) / h
        q_cool = 0.0
        if stage == 'cooling' and pl['cooling_water']:
            q_cool = ENG['cooling_coil_ua_W_K'] * (self.T_w - ENG['cooling_water_k'])
        Cth = ENG['vessel_heat_capacity_J_K'] + self.m_w * CP_W
        net = P_cmd - q_loads - q_loss - q_cool
        T_b = tsat_water(P_atm)
        # ---- air valve open during heating: boiling at atmospheric pressure, the vented steam purges the air
        if self.air_valve and stage == 'heating':
            self.n_air = min(self.n_air, max(P_atm - psat_water(min(self.T_w, T_b)), 0.0) * self._v_gas() / (R_GAS * self.T_w)) if self.T_w < T_b else self.n_air
            if self.T_w >= T_b - 1e-6 and net > 0 and self.m_w > 0:
                m_v = min(net * h / hfg_water(T_b), self.m_w)
                self.m_w -= m_v
                self.led['steam_vented_kg'] += m_v
                self.T_w = T_b
                self.vent_s += h
                self.n_air *= math.exp(-h / ENG['air_purge_time_s'])
                if self.vent_s >= 3.0 * ENG['air_purge_time_s']:
                    self.air_valve = False
                    self._event('cycle', 'AIR_VALVE_CLOSED', '排气阀关闭（空气已被蒸汽置换）')
            else:
                self.T_w += net * h / Cth
        else:
            self.T_w += net * h / Cth
        # ---- exhaust / cooling: pressure release and flashing
        if stage == 'pulse' and C['pulse']['phase'] == 'L':                  # pulse low: the exhaust valve vents towards the low pressure
            P = self._pressure()
            self._release_to(max(P_atm + (P - P_atm) * math.exp(-h / ENG['fast_exhaust_tau_s']), min(self._pulse_par(par)[2], P)))
        elif stage == 'exhaust':
            mode = C['exhaust']
            P = self._pressure()
            if mode == 'fast':
                P_new = P_atm + (P - P_atm) * math.exp(-h / ENG['fast_exhaust_tau_s'])
            elif mode == 'rate':                                             # after forced cooling: the cooling exhaust rate (table 6.2)
                P_new = max(P_atm, P - C['exhaust_rate'] * h)
            else:
                rate = ENG['slow_exhaust_rate_pa_s'] if mode == 'slow_liquids' else ENG['slow_waste_rate_pa_s']
                P_new = max(P_atm, P - rate * h)
            self._release_to(P_new)
            if self._pressure() <= P_atm + 2e3:
                self._stage('drain')
        elif stage == 'cooling':
            if pl['compressed_air'] and self.m_w > 0:
                # compressed air keeps the chamber above the boiling pressure of the hottest liquid load (the liquid must not boil while cooling)
                t_hot = max([L['T'] for L in self.loads.values() if L['kind'] == 'liquid' and L['mass_kg'] > 0] + [self.T_w])
                p_target = ENG['air_overpressure_factor'] * psat_water(t_hot)
                p_now = self._pressure()
                if p_now < p_target:
                    self.n_air += (p_target - p_now) * self._v_gas() / (R_GAS * self.T_w)
            ref = self._reference_temperature()
            if ref <= (par['cooling_end_k'] or par['end_temperature_k']) + 1e-9:
                self._event('cycle', 'COOLING_DONE', '冷却到结束温度，转入快排')
                C['exhaust'] = 'rate' if par['cooling_exhaust_rate_pa_s'] else 'fast'
                C['exhaust_rate'] = par['cooling_exhaust_rate_pa_s']
                self._stage('exhaust')
        elif stage == 'drain':
            C['drain_s'] = C.get('drain_s', 0.0) + h
            m_d = min(self.m_w, ENG['water_charge_m3'] * RHO_W * h / DOC['ex_time_at_127kpa_s'])
            self.m_w -= m_d
            self.led['water_drained_kg'] += m_d
            self.n_air = max(P_atm - (psat_water(self.T_w) if self.m_w > 1e-6 else 0.0), 0.0) * self._v_gas() / (R_GAS * self.T_w)
            if C['drain_s'] >= DOC['ex_time_at_127kpa_s'] and self._reference_temperature() <= max(par['end_temperature_k'], T_b + 0.01):
                if par['dry_stage1_s'] > 0 and C.get('result') != 'CYCLE FAIL':
                    self._stage('dry')                                      # drying: two heater on/off periods (table 6.2)
                else:
                    self._complete()
        # ---- with the air valve closed (and outside exhaust) the steam space follows the water temperature; the safety valve limits P
        P = self._pressure()
        if P > P_SAFETY:
            self._release_to(P_SAFETY)
            self._event('alarm', 'SAFETY_VALVE', '安全阀动作（40 psig）')
        # ---- loads: boil-over, burst, softening, lethality
        P = self._pressure()
        for lid_, L in self.loads.items():
            if L['mass_kg'] <= 0:
                continue
            if L['kind'] == 'liquid':
                sp = L['liquid']
                if L['closure'] in ('loose', 'none'):
                    T_b_l = tsat_liquid(sp, P)
                    if L['T'] > T_b_l + ENG['superheat_boilover_k']:
                        dT = L['T'] - (T_b_l + ENG['superheat_boilover_k'])
                        m_lost = min(L['mass_kg'], L['mass_kg'] * cp_mass(sp) * dT / hfg_liquid(sp, L['T']) + ENG['boilover_fraction_per_k'] * dT * L['mass_kg'] * h / 10.0)
                        L['mass_kg'] -= m_lost
                        L['lost_kg'] += m_lost
                        self.led['load_boilover_kg'] += m_lost
                        L['T'] = T_b_l + ENG['superheat_boilover_k']
                        if not L['boilover']:
                            L['boilover'] = True
                            self._event('truth', 'BOILOVER_' + lid_, f'{lid_}液体暴沸溢出')
                else:
                    p_int = psat_liquid(sp, L['T']) + L['p_air0'] * L['T'] / L['T_fill']
                    if p_int - P > BURST_DP and not L['burst']:
                        L['burst'] = True
                        self.led['load_burst_kg'] += L['mass_kg']
                        L['lost_kg'] += L['mass_kg']
                        L['mass_kg'] = 0.0
                        self._event('truth', 'BURST_' + lid_, f'{lid_}密封容器破裂（内压比腔压高{(p_int - P) / 1e3:.0f} kPa）')
                        continue
            mx = materials()[L['material']]['max_use_temperature_k']
            if mx is not None and L['T'] > mx + 1.0 and not L['softened']:
                L['softened'] = True
                self._event('truth', 'SOFTENED_' + lid_, f'{lid_}容器材料超过最高使用温度（变形）')
            L['F0_s'] += 10.0 ** ((L['T'] - 394.25) / 10.0) * h
            if L.get('organism'):
                o = organisms()[L['organism']]
                if o['D121_s']:
                    L['log_kill'] += h / (o['D121_s'] * 10.0 ** ((394.25 - L['T']) / o['z_K']))
        # ---- cycle bookkeeping: timers, alarms, phase log
        if C and stage not in ('complete', 'failed'):
            C['stage_s'] += h
            T1 = self._t1_true_reading()                                    # the controller's (sensor-1) view; records hold sensor values, not the truth
            if stage == 'heating':
                if T1 >= par['temperature_k'] - 1e-6:
                    self._stage('sterilization')
                    C['hold_min_k'], C['hold_max_k'] = T1, T1
                    C['heater_on'] = True
                elif C['stage_s'] > HEAT_TIMEOUT[par['exhaust']]:
                    self._fail('LOW_HEAT', '加热段超时，达不到灭菌温度（Low Heat）')
            elif stage == 'pulse':
                self._pulse_step(h, par)
            elif stage == 'dry':
                if C['stage_s'] >= 2.0 * par['dry_stage1_s'] - 1e-9:
                    self._complete()
            elif stage == 'sterilization':
                C['hold_min_k'] = min(C['hold_min_k'], T1)
                C['hold_max_k'] = max(C['hold_max_k'], T1)
                C['F0_s'] += 10.0 ** ((T1 - 394.25) / 10.0) * h
                T_meas = self._t1_true_reading()
                liquid_prog = C['exhaust'] in ('slow_liquids', 'cooling') and any(L['kind'] == 'liquid' and L['mass_kg'] > 0 for L in self.loads.values())
                T_hold = min(T_meas, self._reference_temperature()) if liquid_prog else T_meas   # liquids: the time counts once the reference bottle is hot
                if T_meas >= par['temperature_k'] - 0.5:
                    C['below_s'] = 0.0
                    if T_hold >= par['temperature_k'] - 0.5:
                        C['hold_s'] += (10.0 ** ((T_hold - 394.25) / 10.0) if par['f0_mode'] else 1.0) * h   # F0 mode: the time counts F0 (min)
                else:
                    C['below_s'] = C.get('below_s', 0.0) + h
                    if C['below_s'] > 10.0:
                        self._fail('LOW_TEMPERATURE', '灭菌段温度低于设定（Low Temperature）')
                hot = T1 > par['temperature_k'] + DOC['high_temperature_k']
                C['hot_s'] = C.get('hot_s', 0.0) + h if hot else 0.0
                if C['hot_s'] >= 10.0:
                    self._fail('HIGH_TEMPERATURE', '灭菌段温度高于设定3 °C超过10 s（High Temperature）')
                p_meas = self._p_reading()
                p_dev = 0.0 if p_meas is None else p_meas - psat_water(par['temperature_k'])
                C['hp_s'] = C.get('hp_s', 0.0) + h if p_dev > DOC['high_pressure_pa'] else 0.0
                if C['hp_s'] >= 5.0:
                    self._fail('HIGH_PRESSURE', '灭菌段压力高于灭菌压力20 kPa超过5 s（High Pressure）')
                C['lp_s'] = C.get('lp_s', 0.0) + h if (C['stage'] == 'sterilization' and p_dev < -DOC['high_pressure_pa']) else 0.0
                if C['lp_s'] >= 5.0:
                    self._fail('LOW_PRESSURE', '灭菌段压力低于灭菌压力20 kPa超过5 s（Low Pressure）')
                if C and C['stage'] == 'sterilization' and C['hold_s'] >= par['time_s'] - 1e-9:
                    self._event('cycle', 'STERILIZATION_DONE', '灭菌段结束')
                    if C['exhaust'] == 'cooling':
                        self._stage('cooling' if (pl['cooling_water'] and pl['compressed_air']) else 'exhaust')
                        if C['stage'] == 'exhaust':
                            C['exhaust'] = 'slow_liquids'
                            self._event('alarm', 'NO_COOLING_UTILITIES', '没有冷却水或压缩空气：改为液体慢排')
                    else:
                        self._stage('exhaust')
            if C['stage'] in ('heating', 'sterilization') and 'pressure_sensor_fault' in self.faults:
                self._fail('PRESS_SENSOR', '压力传感器故障：控制器中止周期（Press Sensor）')
            if C and (C['stage_s'] % 60.0 < h or stage != C.get('logged_stage')):
                pm = self._p_reading()
                C['log'].append(dict(t_s=round(self.t - C['t0'], 1), stage=C['stage'], T1_k=round(self._t1_true_reading(), 2), P_pa=None if pm is None else round(pm, 0)))
                C['logged_stage'] = C['stage']
                del C['log'][:-400]

    def _mix_water(self, dm, T_in):
        cth_old = ENG['vessel_heat_capacity_J_K'] + self.m_w * CP_W
        self.T_w = (cth_old * self.T_w + dm * CP_W * T_in) / (cth_old + dm * CP_W)
        self.m_w += dm

    def _release_to(self, P_new):
        """Vent the gas space down to P_new: residual air leaves in proportion, the water flashes to the saturation temperature."""
        P = self._pressure()
        if P <= P_new:
            return
        p_air = P - (psat_water(self.T_w) if self.m_w > 1e-6 else 0.0)
        self.n_air *= max(0.0, min(1.0, P_new / P))
        p_air_new = self.n_air * R_GAS * self.T_w / self._v_gas()
        p_steam_target = max(P_new - p_air_new, 611.0)
        T_new = tsat_water(p_steam_target)
        if self.m_w > 1e-6 and T_new < self.T_w:
            Cth = ENG['vessel_heat_capacity_J_K'] + self.m_w * CP_W
            m_f = min(self.m_w, Cth * (self.T_w - T_new) / hfg_water(0.5 * (self.T_w + T_new)))
            self.m_w -= m_f
            self.led['flash_vented_kg'] += m_f
            self.T_w = T_new
        del p_air

    def _equalize_with_atmosphere(self):
        """Gas space open to the room (lid open, water inlet or air valve open): a hot pressurised chamber flashes down to the atmospheric boiling point
        (stored heat leaves as vented steam), then air flows in or out until P = P_atm."""
        P_atm = self._P_atm()
        if self.T_w is None:
            return
        p_steam = psat_water(self.T_w) if self.m_w > 1e-6 else 0.0
        if p_steam > P_atm:                                                  # superheated relative to the room: flash to the boiling point, air is gone
            T_new = tsat_water(P_atm)
            Cth = ENG['vessel_heat_capacity_J_K'] + self.m_w * CP_W
            m_f = min(self.m_w, Cth * (self.T_w - T_new) / hfg_water(0.5 * (self.T_w + T_new)))
            self.m_w -= m_f
            self.led['flash_vented_kg'] += m_f
            self.T_w = T_new
            self.n_air = 0.0
            return
        self.n_air = (P_atm - p_steam) * self._v_gas() / (R_GAS * self.T_w)

    def _t1_true_reading(self):
        """Noise-free value of temperature sensor 1 (the control sensor), including an injected drift."""
        return self.T_w + (1.5 if 'temperature_sensor_drift' in self.faults else 0.0)

    def _p_reading(self):
        """Noise-free value of the pressure sensor (the controller's view), including an injected drift; None with a sensor fault."""
        if 'pressure_sensor_fault' in self.faults:
            return None
        return self._pressure() - (30e3 if 'pressure_sensor_drift' in self.faults else 0.0)

    def _reference_temperature(self):
        """Temperature sensor 2: probe in the largest liquid load (reference bottle) when there is one, else the chamber."""
        liq = [L for L in self.loads.values() if L['kind'] == 'liquid' and L['mass_kg'] > 0]
        if liq:
            return max(liq, key=lambda L: L['mass_kg'])['T']
        return self.T_w

    def _stage(self, s):
        C = self.cycle
        C['stage'] = s
        C['stage_s'] = 0.0
        C['phases'].append(dict(stage=s, t_s=round(self.t - C['t0'], 1)))
        self._event('cycle', 'STAGE_' + s.upper(), '进入阶段' + s)

    def _fail(self, code, msg):
        C = self.cycle
        C['result'] = 'CYCLE FAIL'
        C['failure'] = code
        self._event('alarm', code, msg)
        if C['stage'] in ('water_inlet', 'pulse', 'heating', 'sterilization', 'cooling'):
            C['exhaust'] = 'slow_liquids' if any(L['kind'] == 'liquid' for L in self.loads.values()) else 'fast'
            self._stage('exhaust')
        elif C['stage'] in ('exhaust', 'drain'):
            pass                                                            # already venting / draining: the cycle ends through drain as CYCLE FAIL
        else:
            self._finish('failed')

    def _complete(self):
        C = self.cycle
        if C.get('result') != 'CYCLE FAIL':
            C['result'] = 'CYCLE ENDED'
        self._finish('complete' if C['result'] == 'CYCLE ENDED' else 'failed')

    def _finish(self, state):
        C = self.cycle
        C['stage'] = state
        rec = dict(cycle_number=C['number'], operation_id=C.get('operation_id'), program=C['program'], parameters=dict(C['parameters']), started_s=C['t0'], ended_s=self.t, result=C['result'],
                   failure=C.get('failure'), hold_s=C['hold_s'], hold_min_k=C.get('hold_min_k'), hold_max_k=C.get('hold_max_k'), chamber_F0_s=C['F0_s'],
                   phases=list(C['phases']), log=list(C['log']))
        rec.update(started_at=C.get('started_at'), ended_at=self._wall_text(), run=C.get('run', 1))
        self.records.append(rec)
        del self.records[:-MAX_RECORDS]
        if state == 'complete' and C.get('run', 1) < C['parameters'].get('repeat_cycles', 1):   # multiple cycles: the next one after the pause
            self.repeat = dict(run=C.get('run', 1) + 1, at=self.t + C['parameters']['repeat_pause_s'])
        self._event('cycle', 'CYCLE_ENDED' if state == 'complete' else 'CYCLE_FAIL', f'周期{C["number"]}{"结束" if state == "complete" else "失败"}')

    def _pt(self, T, ch):
        if not self.powered or T is None:
            return None
        drift = 1.5 if 'temperature_sensor_drift' in self.faults and ch == 1 else 0.0
        return round(T + drift + ENG['temperature_sensor_noise_k'] * _gauss(self.seed, self.tick, ch), 2)

    def _update_warnings(self):
        W = {}
        if self.plant is None:
            self.warnings = W
            return
        C = self.cycle
        if C and C.get('failure'):
            W[C['failure']] = '周期失败：' + C['failure']
        if self.powered and self.lid_open:
            W['LID_OPEN'] = '顶盖打开'
        if self.powered and not (self.m_w > 0.25 * ENG['water_charge_m3'] * RHO_W) and C and C['stage'] in ('heating', 'sterilization'):
            W['NO_WATER'] = '腔内没有水（No Water）'
        if self.powered and (not self.plant['water_supply'] or 'water_supply_off' in self.faults):
            W['NO_RES_SRC'] = '没有纯水供应（No Res Src）'
        if self.powered and 'pressure_sensor_fault' in self.faults:
            W['PRESS_SENSOR'] = '压力传感器故障（Press Sensor）'
        if self.powered:
            for f in self._doc_latched():
                W[f.upper()] = DOC_MESSAGES[f][1]
        if self.powered and self._locked() and not (C and C['stage'] not in ('complete', 'failed')):
            W['DOOR_LOCKED_HOT'] = '腔内有压或温度高于结束温度：门锁不放开'
        self.warnings = W

    # ---- commands
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'records.read', 'panel.capture', 'panel.frame', 'user.sign_in'):
            self.revision += 1
        if capability_id != 'sim.step':
            self._update_warnings()
        return out

    def _cmd_plant_configure(self, ambient_k, atmospheric_pressure_pa, water_supply, compressed_air, cooling_water, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(ambient_k=ambient_k, atmospheric_pressure_pa=atmospheric_pressure_pa, water_supply=water_supply, compressed_air=compressed_air,
                          cooling_water=cooling_water)
        self.seed = noise_seed
        if first:
            self.T_w = ambient_k
            self.n_air = atmospheric_pressure_pa * V_CH / (R_GAS * ambient_k)
            self._event('system', 'PLANT_CONFIGURED', '环境条件已声明')
        return dict(configured=True)

    def _foreign_cycle(self):
        """Caller rule: an agent may not end a cycle another caller started (direct call or panel gesture, which acts as its session's caller)."""
        running = self.cycle is not None and self.cycle['stage'] not in ('complete', 'failed')
        c, owner = self._caller, self._cycle_owner
        return running and c is not None and self._principals.get(c) == 'agent' and owner is not None and owner != c

    def _cmd_power_set(self, on):
        self._need_plant()
        # the main switch is a hard switch (final sync 2026-10-19): never refused, also during another caller's cycle (a power failure, below)
        if not on and self.powered and self.cycle and self.cycle['stage'] not in ('complete', 'failed'):
            self.cycle['result'] = 'CYCLE FAIL'
            self.cycle['failure'] = 'POWER_FAIL'
            self._event('alarm', 'POWER_FAIL', '断电：周期失败（加热停止、阀门保持；腔内压力随冷却下降）')
            self._finish('failed')
        self.powered = bool(on)
        if not on:
            self.repeat = None
            self.timer['due'] = False
        else:
            self.power_epoch += 1
        self._event('system', 'POWER_ON' if on else 'POWER_OFF', '通电' if on else '断电')
        return dict(on=self.powered)

    def _cmd_power_press_switch(self):
        return self._cmd_power_set(not self.powered)

    def _cmd_lid_set(self, open):
        self._need_plant()
        if open and self._locked():
            raise ActionRejected('LID_LOCKED')
        if open:
            self.repeat = None                                              # opening the door ends a series of multiple cycles
        self.lid_open = bool(open)
        return dict(open=self.lid_open)

    def _cmd_load_add(self, load_id, kind, mass_kg, material, liquid=None, container_volume_m3=None, closure=None, organism=None, log10_n0=None):
        self._need_plant()
        if not self.lid_open or self.lid_angle < 0.5 * LID_OPEN:
            raise ActionRejected('LID_CLOSED')
        if load_id in self.loads:
            raise ActionRejected('LOAD_EXISTS')
        if len(self.loads) >= MAX_LOADS:
            raise ActionRejected('TOO_MANY_LOADS')
        if kind == 'liquid':
            if liquid is None or container_volume_m3 is None:
                raise ActionRejected('MISSING_SETTING')
            if liquid not in liquids() and liquid != 'water':
                raise ActionRejected('UNKNOWN_LIQUID')
        if (organism is None) != (log10_n0 is None):
            raise ActionRejected('MISSING_SETTING')
        if kind == 'liquid' and mass_kg > container_volume_m3 * MAX_LIQUID_DENSITY:
            raise ActionRejected('CONTAINER_OVERFILLED')
        liq_vol = sum(L['container_volume_m3'] for L in self.loads.values() if L['kind'] == 'liquid') + (container_volume_m3 if kind == 'liquid' else 0.0)
        solid_mass = sum(L['mass_kg'] for L in self.loads.values() if L['kind'] != 'liquid') + (mass_kg if kind != 'liquid' else 0.0)
        if liq_vol > DOC['loading_liquid_m3'] + 1e-12 or solid_mass > DOC['loading_instrument_kg'] + 1e-9:
            raise ActionRejected('OVERLOAD')
        sp = liquid if kind == 'liquid' else None
        cp = cp_mass(sp) if kind == 'liquid' else {'stainless_316': 500.0, 'borosilicate_glass': 830.0}.get(material, 1500.0) if kind == 'solid' else 3000.0
        vol = container_volume_m3 if kind == 'liquid' else mass_kg / {'stainless_316': 8000.0, 'borosilicate_glass': 2230.0}.get(material, 1000.0)
        self.loads[load_id] = dict(kind=kind, mass_kg=float(mass_kg), mass0_kg=float(mass_kg), material=material, liquid=sp, container_volume_m3=container_volume_m3,
                                   closure=closure or ('loose' if kind == 'liquid' else 'none'), organism=organism, log10_n0=log10_n0, cp=cp, volume_m3=vol,
                                   T=self.plant['ambient_k'], T_fill=self.plant['ambient_k'], p_air0=self._P_atm(), F0_s=0.0, log_kill=0.0, lost_kg=0.0,
                                   boilover=False, burst=False, softened=False)
        self.led['load_added_kg'] += mass_kg
        if self.lid_open or self.lid_angle > 1e-6:
            self._equalize_with_atmosphere()
        notes = []
        m = materials()[material]
        if not m['autoclavable']:
            notes.append(f'{material}不可高压灭菌（{m["note_zh"]}）')
        if kind == 'liquid' and (closure or 'loose') == 'sealed':
            notes.append('密封容器：Nalgene/厂家要求灭菌前取下或拧松盖子（排气时可能破裂）')
        if kind == 'liquid' and sp != 'water' and liquids()[sp]['antoine_fit']['quality'] != 'consistent':
            notes.append(f'{sp}的蒸气压数据不一致或温区窄（库标记inconsistent）：沸腾与内压计算不确定度大')
        if kind == 'liquid' and sp != 'water' and liquids()[sp]['boiling_point_K'] and liquids()[sp]['boiling_point_K'] < 373.15:
            notes.append(f'{sp}沸点低于100 °C：在灭菌温度下剧烈沸腾/蒸汽压高（不适合高压灭菌）')
        self._event('load', 'LOAD_ADDED', f'放入负载{load_id}（{kind}）')
        return dict(load_id=load_id, notes=notes)

    def _cmd_load_remove(self, load_id):
        self._need_plant()
        if not self.lid_open or self.lid_angle < 0.5 * LID_OPEN:
            raise ActionRejected('LID_CLOSED')
        if load_id not in self.loads:
            raise ActionRejected('UNKNOWN_LOAD')
        L = self.loads.pop(load_id)
        self.led['load_removed_kg'] += L['mass_kg']
        if self.lid_open or self.lid_angle > 1e-6:
            self._equalize_with_atmosphere()
        self._event('load', 'LOAD_REMOVED', f'取出负载{load_id}')
        return dict(load_id=load_id, state=dict(mass_kg=L['mass_kg'], liquid_lost=L['lost_kg'] > 1e-6, broken=L['burst'], deformed=L['softened'], hot=L['T'] > 333.15))

    def _cmd_program_select(self, program):
        self._need_power()
        if self.cycle and self.cycle['stage'] not in ('complete', 'failed'):
            raise ActionRejected('CYCLE_RUNNING')
        self.program = program
        return dict(program=program, parameters=dict(self.params[program]))

    def _cmd_program_next(self):
        keys = LEGACY_PROGRAMS
        nxt = keys[(keys.index(self.program) + 1) % len(keys)] if self.program in keys else keys[0]
        return dict(program=self._cmd_program_select(nxt)['program'])

    def _cmd_program_configure(self, program, temperature_k=None, time_s=None, exhaust=None, end_temperature_k=None):
        self._need_power()
        if self.cycle and self.cycle['stage'] not in ('complete', 'failed') and self.cycle['program'] == program:
            raise ActionRejected('CYCLE_RUNNING')
        p = self.params[program]
        if exhaust == 'cooling' and not (self.plant['compressed_air'] and self.plant['cooling_water']):
            raise ActionRejected('NO_COOLING_UTILITIES')
        new = dict(p, **{k: v for k, v in dict(temperature_k=temperature_k, time_s=time_s, exhaust=exhaust, end_temperature_k=end_temperature_k).items() if v is not None})
        if program != 'extended_time' and new['time_s'] > 99 * 60.0:
            raise ActionRejected('TIME_OUT_OF_RANGE')
        self.params[program] = new
        return dict(program=program, parameters=dict(new))

    def _cmd_cycle_start(self):
        self._need_power()
        if self.cycle and self.cycle['stage'] not in ('complete', 'failed'):
            raise ActionRejected('CYCLE_RUNNING')
        if self.lid_open or self.lid_angle > 1e-6:
            raise ActionRejected('LID_OPEN')
        if not self.plant['water_supply'] and self.m_w < ENG['water_charge_m3'] * RHO_W * 0.99:
            raise ActionRejected('NO_WATER_SUPPLY')
        par = self.params[self.program]
        if par['exhaust'] == 'cooling' and not (self.plant['compressed_air'] and self.plant['cooling_water']):
            raise ActionRejected('NO_COOLING_UTILITIES')
        if self._doc_latched('service'):
            raise ActionRejected('SERVICE_REQUIRED')
        self.repeat = None
        self._cycle_owner = self._caller                                    # a series of multiple cycles keeps this owner
        return self._start_cycle(1)

    def _start_cycle(self, run):
        par = self.params[self.program]
        self.cycles_run += 1
        op = f'{self.instance_id}:cycle:{self.cycles_run}'                  # every cycle (also a timed start or a repeat) is an operation of its owner
        self._op_owners[op] = self._cycle_owner
        self.cycle = dict(number=self.cycles_run, operation_id=op, program=self.program, parameters=dict(par), t0=self.t, stage='water_inlet', stage_s=0.0, hold_s=0.0, F0_s=0.0,
                          exhaust=par['exhaust'], phases=[dict(stage='water_inlet', t_s=0.0)], log=[], result=None, run=run, started_at=self._wall_text())
        for L in self.loads.values():
            L['F0_s'] = 0.0
        self._event('cycle', 'CYCLE_START', f'周期{self.cycles_run}开始：{par["name_zh"]}' + (f'（多次循环第{run}个）' if run > 1 else ''))
        return dict(cycle_number=self.cycles_run, program=self.program, operation_id=op)

    def _cmd_cycle_stop(self):
        self._need_power()
        C = self.cycle
        if not C or C['stage'] in ('complete', 'failed'):
            raise ActionRejected('NO_CYCLE')
        if self._foreign_cycle():
            raise ActionRejected('ACCESS_DENIED')
        stage = C['stage']
        self.repeat = None
        self._fail('MANUAL_STOP', '手动停止（Manual Stop）')
        return dict(cycle_number=C['number'], stage=stage)

    def _cmd_cycle_toggle(self):
        if self.cycle and self.cycle['stage'] not in ('complete', 'failed'):
            self._cmd_cycle_stop()
            return dict(running=False)
        self._cmd_cycle_start()
        return dict(running=True)

    def _cmd_records_read(self, last=None):
        self._need_power()
        n = last or MAX_RECORDS
        return dict(records=[copy.deepcopy(r) for r in self.records[-n:]], stored=len(self.records))

    def _cmd_events_read(self, since_s):
        return dict(events=[dict(e) for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        v = self.read('evaluator')
        return dict(chamber=v['truth.chamber'], loads=v['truth.loads'], faults=v['truth.faults'])

    def _cmd_ledger_read(self):
        led = {k: float(v) for k, v in self.led.items()}
        led['water_in_chamber_kg'] = self.m_w
        led['load_mass_in_chamber_kg'] = sum(L['mass_kg'] for L in self.loads.values())
        return dict(ledger=led)

    def _running(self):
        return self.cycle is not None and self.cycle['stage'] not in ('complete', 'failed')

    def _doc_latched(self, kind=None):
        return [f for f in sorted(self.faults) if f in DOC_MESSAGES and (kind is None or DOC_MESSAGES[f][0] == kind)]

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        if fault in DOC_MESSAGES:                                           # a documented message (manual p.81-84)
            kind, text = DOC_MESSAGES[fault][:2]
            if kind == 'cycle' and active and not self._running():
                raise ActionRejected('NO_CYCLE')
            if kind != 'cycle':
                (self.faults.add if active else self.faults.discard)(fault)
            if active and kind in ('cycle', 'service') and self._running():
                self._fail(fault.upper(), text)
            self._event('fault', 'FAULT_' + fault.upper(), f'手册消息{text}：{"注入" if active else "清除"}')
            return dict(faults=sorted(self.faults))
        (self.faults.add if active else self.faults.discard)(fault)
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}：{"注入" if active else "清除"}')
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- ELV-D controller: programme list, cycle-parameter table, clock, timed start, multiple cycles, users, USB export (manual rev D)
    def _elvd_init(self):
        self.clock_offset_s = 0.0
        self.settings = dict(print_rate_total_s=180.0, print_rate_sterilization_s=60.0, screensaver_s=5400.0)
        self.timer = dict(enabled=False, time='08:00', due=False)
        self.users = [dict(name='Admin', code='0001')]
        self.usb_files = []
        self.repeat = None
        self.power_epoch = 0
        self.panel = _Panel(PANEL_SPEC, self, PANEL_HOOKS) if PANEL_SPEC else None

    def _wall_s(self, t):
        import datetime
        return datetime.datetime.fromisoformat(EPOCH + '+00:00').timestamp() + t + self.clock_offset_s

    def _wall_text(self, t=None):
        import datetime
        return datetime.datetime.fromtimestamp(self._wall_s(self.t if t is None else t), datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    def _pulse_par(self, par):
        g = self.cycle['pulse']['group']
        return par[f'pulse_{g}_count'], par[f'pulse_{g}_hold_s'], par[f'pulse_{g}_low_pa'], par[f'pulse_{g}_high_pa']

    def _pulse_step(self, h, par):
        """Pulse phases: H heats with the valves closed until the high pressure, L vents until the low pressure (not below the room: no vacuum pump)
        and keeps the valve open for the hold time; then the next pulse, group B, and finally Heat with the gravity air valve."""
        C = self.cycle
        p = C['pulse']
        n, hold, lo, hi = self._pulse_par(par)
        P = self._pressure()
        if p['phase'] == 'H' and P >= hi - 1e-6:
            p['phase'] = 'L'
            self._event('cycle', 'PULSE_L', f'脉冲{p["group"].upper()}{p["n"] + 1}：排气到低压')
        elif p['phase'] == 'L' and P <= max(lo, self._P_atm() + 2e3) + 1e-6:
            p['hold_s'] += h
            if p['hold_s'] >= hold - 1e-9:
                p.update(n=p['n'] + 1, phase='H', hold_s=0.0)
                if p['n'] >= n:
                    if p['group'] == 'a' and par['pulse_b_count'] > 0:
                        p.update(group='b', n=0)
                    else:
                        self._begin_heating()
                        return
        if C['stage'] == 'pulse' and C['stage_s'] > HEAT_TIMEOUT[par['exhaust']]:
            self._fail('LOW_HEAT', '脉冲段超时，达不到脉冲压力（Heating time error）')

    def _begin_heating(self):
        self._stage('heating')
        self.air_valve = 'air_valve_stuck_closed' not in self.faults
        self.vent_s = 0.0

    def _dry_heater_on(self, C, par):
        first = C['stage_s'] < par['dry_stage1_s']
        on, off = (par['dry1_on_s'], par['dry1_off_s']) if first else (par['dry2_on_s'], par['dry2_off_s'])
        t = C['stage_s'] - (0.0 if first else par['dry_stage1_s'])
        return on > 0 and (off <= 0 or t % (on + off) < on)

    def _schedule(self):
        """Multiple cycles and the timed start, evaluated on the process grid (partition independent)."""
        running = self.cycle is not None and self.cycle['stage'] not in ('complete', 'failed')
        closed = not self.lid_open and self.lid_angle <= 1e-6
        if self.repeat and not running and self.t >= self.repeat['at'] - 1e-9:
            run, self.repeat = self.repeat['run'], None
            if self.powered and closed and self._start_possible():
                self._start_cycle(run)
                running = True
        if self.timer['enabled'] and self.powered and not running:
            if not self.timer['due']:
                hh, mm = (int(x) for x in self.timer['time'].split(':'))
                target, a, b = hh * 3600 + mm * 60, self._wall_s(self.t - DT) % 86400, self._wall_s(self.t) % 86400
                self.timer['due'] = (a < target <= b) if a <= b else (target > a or target <= b)
                if self.timer['due'] and not closed:
                    self._event('cycle', 'TIMED_DOOR_OPEN', '定时启动到点：门开着（等待关门）')
            if self.timer['due'] and closed:
                self.timer.update(enabled=False, due=False)
                if self._start_possible():
                    self._cycle_owner = self._timer_owner
                    self._start_cycle(1)
                    self._event('cycle', 'TIMED_START', '定时启动周期')

    def _start_possible(self):
        par = self.params[self.program]
        water = self.plant['water_supply'] or self.m_w >= ENG['water_charge_m3'] * RHO_W * 0.99
        return water and (par['exhaust'] != 'cooling' or (self.plant['compressed_air'] and self.plant['cooling_water'])) and not self._doc_latched('service')

    def _cmd_program_step(self, direction):
        self._need_power()
        if self.cycle and self.cycle['stage'] not in ('complete', 'failed'):
            raise ActionRejected('CYCLE_RUNNING')
        if not self.lid_open:
            raise ActionRejected('DOOR_CLOSED')
        i = ELVD_ORDER.index(self.program) if self.program in ELVD_ORDER else (-1 if direction == 'next' else 0)
        key = ELVD_ORDER[(i + (1 if direction == 'next' else -1)) % len(ELVD_ORDER)]
        self.program = key
        return dict(program=key, number=ELVD_ORDER.index(key) + 1)

    def _cmd_program_set_parameters(self, program, **kw):
        self._need_power()
        if self.cycle and self.cycle['stage'] not in ('complete', 'failed') and self.cycle['program'] == program:
            raise ActionRejected('CYCLE_RUNNING')
        p = dict(self.params[program])
        cm, em = kw.pop('cooling_mode', None), kw.pop('exhaust_mode', None)
        if cm in (2, 3):
            raise ActionRejected('NOT_AVAILABLE')                           # 2 and 3: 'S.O. pour ce modèle'
        if cm is not None or em is not None:
            cm = (1 if p['exhaust'] == 'cooling' else 0) if cm is None else cm
            em = (2 if p['exhaust'] in ('slow_liquids', 'slow_waste') else 1) if em is None else em
            if cm == 1 and not (self.plant['compressed_air'] and self.plant['cooling_water']):
                raise ActionRejected('NO_COOLING_UTILITIES')
            p['exhaust'] = 'cooling' if cm == 1 else ('slow_liquids' if em == 2 else 'fast')
            if cm == 1 and p['cooling_end_k'] is None:
                p['cooling_end_k'] = CUSTOM_TABLE['cooling_end_k']
        p.update({k: (float(v) if isinstance(v, float) else v) for k, v in kw.items()})
        if p['pulse_a_low_pa'] >= p['pulse_a_high_pa'] or p['pulse_b_low_pa'] >= p['pulse_b_high_pa']:
            raise ActionRejected('INVALID_ARGUMENT')
        self.params[program] = p
        return dict(program=program, parameters=dict(p))

    def _cmd_settings_configure(self, **kw):
        self._need_power()
        self.settings.update({k: float(v) for k, v in kw.items()})
        return dict(settings=dict(self.settings))

    def _cmd_clock_set(self, date=None, time=None):
        import datetime
        self._need_power()
        now = datetime.datetime.fromtimestamp(self._wall_s(self.t), datetime.timezone.utc)
        try:
            d, mo, y = (int(x) for x in (date or now.strftime('%d:%m:%y')).split(':'))
            hh, mm, ss = (int(x) for x in (time or now.strftime('%H:%M:%S')).split(':'))
            new = datetime.datetime(2000 + y, mo, d, hh, mm, ss, tzinfo=datetime.timezone.utc)
        except ValueError:
            raise ActionRejected('BAD_CLOCK') from None
        self.clock_offset_s += new.timestamp() - self._wall_s(self.t)
        self.faults.discard('msg_rtc_set')                                  # manual p.83: set the date and time
        return dict(wall=self._wall_text())

    def _cmd_timer_configure(self, enabled, time=None):
        self._need_power()
        self.timer = dict(enabled=bool(enabled), time=time or self.timer['time'], due=False)
        self._timer_owner = self._caller
        return dict(timer=dict(self.timer))

    def _cmd_user_sign_in(self, user, code):
        self._need_power()
        u = next((x for x in self.users if x['name'] == user), None)
        if u is None:
            raise ActionRejected('UNKNOWN_USER')
        if u['code'] != code:
            raise ActionRejected('WRONG_CODE')
        return dict(user=user)

    def _cmd_user_create(self, code):
        self._need_power()
        if len(self.users) >= MAX_USERS:
            raise ActionRejected('TOO_MANY_USERS')
        if any(u['code'] == code for u in self.users):
            raise ActionRejected('CODE_IN_USE')
        name = f'User {len(self.users)}'
        self.users.append(dict(name=name, code=code))
        return dict(user=name)

    def _cmd_usb_export(self, content):
        self._need_power()
        if content == 'history':
            data = [dict(cycle_number=r['cycle_number'], program=r['program'], started_at=r.get('started_at'), result=r['result']) for r in self.records]
        elif content == 'gains_offsets':
            data = [dict(channel=k, gain=g, offset=o) for k, (g, o) in GAINS_OFFSETS.items()]
        else:
            data = [dict(program=k, **{x: v for x, v in p.items() if x != 'name_zh'}) for k, p in self.params.items()] + [dict(system=dict(self.settings))]
        f = dict(name=f'{content}_{self._wall_text().replace(" ", "_").replace(":", "")}.csv', content=content, exported_at=self._wall_text(), entries=len(data),
                 data=copy.deepcopy(data))
        self.usb_files = (self.usb_files + [f])[-MAX_USB_FILES:]
        return dict(file={k: v for k, v in f.items() if k != 'data'})

    def _phase(self):
        C = self.cycle
        if C is None:
            return None
        st = C['stage']
        if st == 'pulse':
            return 'Pulse ' + C['pulse']['phase']
        return {'water_inlet': 'Insert water', 'heating': 'Heat', 'sterilization': 'Sterilization', 'cooling': 'Cooling', 'exhaust': 'Exhaust', 'drain': 'Drain',
                'dry': 'Dry', 'complete': 'Cycle completed', 'failed': 'Cycle failed'}[st]

    def _repeats_left(self):
        C = self.cycle
        if self.repeat:
            return max(0, C['parameters']['repeat_cycles'] - self.repeat['run'] + 1) if C else 0
        if C and C['stage'] not in ('complete', 'failed'):
            return max(0, C['parameters'].get('repeat_cycles', 1) - C.get('run', 1))
        return 0

    def _elvd_read(self):
        p = self.params[self.program]
        return {'program.parameters': dict(program=self.program, number=ELVD_ORDER.index(self.program) + 1 if self.program in ELVD_ORDER else None,
                                           name=PROGRAM_NAMES[self.program], editable=p['editable'], temperature_k=p['temperature_k'], time_s=p['time_s'],
                                           exhaust=p['exhaust'], end_temperature_k=p['end_temperature_k'], **{k: p[k] for k in TABLE_KEYS}),
                'cycle.phase': self._phase(), 'cycle.repeats_left': self._repeats_left(), 'clock.wall': self._wall_text(), 'settings.system': dict(self.settings),
                'timer.setting': dict(self.timer), 'users.names': [u['name'] for u in self.users],
                'usb.files': [{k: v for k, v in f.items() if k != 'data'} for f in self.usb_files]}

    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What the controller shows: the agent slots plus display texts (programme name, phase and messages, temperatures in C, pressure in kPa)."""
        out = self.read('agent')
        C = self.cycle
        par = self.params[self.program]
        t1, t2, p = out['chamber.temperature_k'], out['chamber.temperature2_k'], out['chamber.pressure_pa']
        running = bool(C) and C['stage'] not in ('complete', 'failed')
        moving = 1e-6 < self.lid_angle < LID_OPEN - 1e-6
        fail = {'LOW_HEAT': 'Heating time error', 'LOW_TEMPERATURE': 'Low temperature', 'HIGH_TEMPERATURE': 'High temperature', 'HIGH_PRESSURE': 'High pressure',
                'LOW_PRESSURE': 'Low pressure', 'MANUAL_STOP': 'Cancelled by user', 'POWER_FAIL': 'Power failure', 'NO_RES_SRC': 'No water',
                'PRESS_SENSOR': 'Analog input error', **{k.upper(): v[1] for k, v in DOC_MESSAGES.items()}}
        out.update({'host.powered': self.powered, 'host.power_epoch': self.power_epoch, 'host.program_name': PROGRAM_NAMES[self.program],
                    'host.ster_temp_c': par['temperature_k'] - 273.15, 'host.ster_time_min': par['time_s'] / 60.0, 'host.running': running,
                    'host.cycle_number': C['number'] if C else 0, 'host.ended': bool(C) and not running, 'host.failed': bool(C) and C['stage'] == 'failed',
                    'host.fail_text': fail.get(C.get('failure'), C.get('failure') or '') if C else '', 'host.phase': self._phase() or '',
                    'host.notice_text': DOC_MESSAGES[self._doc_latched()[0]][1] if self.powered and self._doc_latched() else '',
                    'host.remaining_min': (max(C['parameters']['time_s'] - C['hold_s'], 0.0) / 60.0) if running and C['stage'] == 'sterilization' else None,
                    'host.temp_c': None if t1 is None else t1 - 273.15, 'host.temp2_c': None if t2 is None else t2 - 273.15, 'host.pressure_kpa': None if p is None else p / 1e3,
                    'host.door_open': self.lid_open or self.lid_angle > 1e-6, 'host.door_moving': moving, 'host.door_opening': moving and self.lid_open,
                    'host.locked': self._locked(), 'host.time': self._wall_text()[11:], 'host.date': self._wall_text()[8:10] + ':' + self._wall_text()[5:7] + ':' + self._wall_text()[2:4],
                    'host.records': [dict(pos=i, n=r['cycle_number'], at=(r.get('started_at') or '')[2:16], program=PROGRAM_NAMES.get(r['program'], r['program']),
                                          result='OK' if r['result'] == 'CYCLE ENDED' else 'FAIL') for i, r in enumerate(reversed(self.records))],
                    'host.user_rows': [dict(pos=i, name=u['name']) for i, u in enumerate(self.users)],
                    'host.clock': dict(zip(('y', 'mo', 'd', 'h', 'm', 's'), (int(x) % 100 if j == 0 else int(x) for j, x in enumerate(
                        self._wall_text().replace('-', ' ').replace(':', ' ').split())))),
                    'host.timer_hm': dict(h=int(self.timer['time'][:2]), m=int(self.timer['time'][3:])), 'host.custom': self.program in ('custom_1', 'custom_2'), 'host.timer_on': self.timer['enabled'],
                    'host.params': {k: (v if v is None or isinstance(v, (bool, str)) else v - 273.15 if k.endswith('_k') else
                                        v / 1e3 if k.endswith(('_pa', '_pa_s')) else v / 60.0 if k in ('time_s', 'dry_stage1_s', 'repeat_pause_s') else v)
                                    for k, v in par.items() if k not in ('name_zh', 'editable')}})
        hp = out['host.params']
        hp['cooling_mode'] = 1 if par['exhaust'] == 'cooling' else 0
        hp['exhaust_mode'] = 2 if par['exhaust'] in ('slow_liquids', 'slow_waste') else 1
        hp['f0_mode'] = 1 if par['f0_mode'] else 0
        if hp['cooling_end_k'] is None:
            hp['cooling_end_k'] = par['end_temperature_k'] - 273.15
        if hp['cooling_exhaust_rate_pa_s'] is None:
            hp['cooling_exhaust_rate_pa_s'] = 5.0
        out['host.params'] = {k[:-2] if k.endswith('_k') else k: v for k, v in hp.items()}
        out['host.settings'] = dict(print_total_min=self.settings['print_rate_total_s'] / 60.0, print_ster_min=self.settings['print_rate_sterilization_s'] / 60.0,
                                    screensaver_min=self.settings['screensaver_s'] / 60.0)
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel action calls the same command a direct call would (each command checks before it changes anything)."""
        try:
            return self.invoke(capability_id, **arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.t

    def _panel(self, gesture, *args):
        try:
            return dict(accepted=bool(gesture(*args)))
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _cmd_panel_press(self, input_id, duration_s=0.1, held=()):
        return self._panel(self.panel.press, input_id, float(duration_s), tuple(held))

    def _cmd_panel_capture(self, display_id):
        import hashlib
        import tempfile
        try:
            data, w, h = self.panel.png_bytes(display_id)
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        sha = hashlib.sha256(data).hexdigest()
        d = Path(getattr(self, 'panel_capture_dir', None) or Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f'{display_id}_{sha[:16]}.png'
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.t, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))

    def _cmd_panel_frame(self):
        return dict(frame=self.panel.frame())

    # ---- read
    def read(self, visibility='agent'):
        pw = self.powered
        P = self._pressure() if self.plant else None
        out = {'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.on': pw, 'lid.angle_rad': self.lid_angle, 'lid.locked': self._locked()}
        out['chamber.temperature_k'] = self._pt(self.T_w, 1) if self.plant else None
        out['chamber.temperature2_k'] = self._pt(self._reference_temperature(), 2) if self.plant else None
        pr = None
        if pw and P is not None and 'pressure_sensor_fault' not in self.faults:
            pr = round(self._p_reading() + ENG['pressure_sensor_noise_pa'] * _gauss(self.seed, self.tick, 3), 0)
        out['chamber.pressure_pa'] = pr
        gauge = 0.0 if P is None else max(0.0, min(GAUGE_FULL, P - self._P_atm())) / GAUGE_FULL * GAUGE_SWEEP
        out['gauge.needle_rad'] = gauge
        out['display.temperature_c'] = None if out['chamber.temperature_k'] is None else round(out['chamber.temperature_k'] - 273.15, 1)
        par = self.params[self.program]
        out['program.current'] = dict(program=self.program, name_zh=par['name_zh'], temperature_k=par['temperature_k'], time_s=par['time_s'], exhaust=par['exhaust'],
                                      end_temperature_k=par['end_temperature_k'])
        C = self.cycle
        out['cycle.status'] = dict(cycle_number=C['number'] if C else None, operation_id=C.get('operation_id') if C else None, stage=C['stage'] if C else 'idle', stage_elapsed_s=C['stage_s'] if C else 0.0,
                                   remaining_hold_s=max(C['parameters']['time_s'] - C['hold_s'], 0.0) if C else None, result=C.get('result') if C else None)
        out['water.level_ok'] = self.m_w > 0.25 * ENG['water_charge_m3'] * RHO_W
        out['loads.present'] = sorted(self.loads)
        out['records.stored'] = len(self.records)
        out['alarms.active'] = sorted(self.warnings)
        out.update(self._elvd_read())
        if visibility != 'agent':
            out['truth.chamber'] = dict(water_kg=self.m_w, water_temperature_k=self.T_w, air_mol=self.n_air, pressure_pa=P,
                                        steam_partial_pa=psat_water(self.T_w) if (self.T_w and self.m_w > 1e-6) else 0.0, heater_w=self.P_heat, air_valve_open=self.air_valve,
                                        gas_volume_m3=self._v_gas() if self.plant else None)
            out['truth.loads'] = {k: dict(kind=L['kind'], temperature_k=L['T'], mass_kg=L['mass_kg'], lost_kg=L['lost_kg'], F0_s=L['F0_s'],
                                          log10_survivors=None if L['organism'] is None else L['log10_n0'] - L['log_kill'], boilover=L['boilover'], burst=L['burst'],
                                          deformed=L['softened']) for k, L in self.loads.items()}
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
class TuttnauerElvdSession:
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
        meta = copy.deepcopy(export_tuttnauer_3870elvd_contract_metadata())
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
            op = result.get('operation_id') if isinstance(result, dict) else None
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=op, status='accepted' if op else 'succeeded',
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
        """A cycle belongs to the session that started it (cycle.start / cycle.toggle / START on the panel; a timed start to the session that set
        the timer; repeats keep it): running, succeeded (CYCLE ENDED), cancelled (Manual Stop) or failed."""
        h = self._host
        if operation_id not in h._op_owners:
            raise ActionRejected('UNKNOWN_OPERATION')
        if h._op_owners[operation_id] != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        C = h.cycle
        if C and C.get('operation_id') == operation_id and C['stage'] not in ('complete', 'failed'):   # still in the chamber (a stopped cycle vents first)
            status = 'running' if C.get('result') is None else 'cancelled' if C.get('failure') == 'MANUAL_STOP' else 'failed'
            return dict(operation_id=operation_id, status=status, result=_jsonable(h.read(self._role)['cycle.status']))
        rec = next((r for r in reversed(h.records) if r.get('operation_id') == operation_id), None)
        if rec is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        status = 'succeeded' if rec['result'] == 'CYCLE ENDED' else 'cancelled' if rec.get('failure') == 'MANUAL_STOP' else 'failed'
        return dict(operation_id=operation_id, status=status, result=_jsonable(copy.deepcopy(rec)))

    def cancel(self, operation_id, request_id):
        if self.poll(operation_id)['status'] != 'running':
            raise ActionRejected('NO_CYCLE')
        return self.invoke(instance_id=self._host.instance_id, capability_id='cycle.stop', arguments={}, request_id=request_id)


# ---------------------------------------------------------------- contract metadata
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值或评测者能力；另一代理启动的周期不能停止或断电）',
    'PLANT_REQUIRED': '尚未声明环境条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '灭菌器未通电',
    'LID_LOCKED': '自动压力锁未放开（腔内有压、周期进行中或温度高于结束温度）', 'LID_CLOSED': '顶盖关着（先打开）', 'LID_OPEN': '顶盖未关好',
    'LOAD_EXISTS': '负载标识已存在', 'TOO_MANY_LOADS': '负载数量超过上限', 'UNKNOWN_LOAD': '负载不存在', 'UNKNOWN_LIQUID': '液体不在液体库中',
    'MISSING_SETTING': '缺少必需参数（液体负载须给液体与容器体积；生物指示物须同时给物种与初始对数）',
    'OVERLOAD': '超过最大装载（液体容器合计20 L、器械与废物合计20 kg，同平台手册）', 'CONTAINER_OVERFILLED': '液体质量超过容器容积所能容纳的量', 'CYCLE_RUNNING': '周期进行中', 'NO_CYCLE': '没有进行中的周期',
    'NO_WATER_SUPPLY': '没有纯水供应', 'NO_COOLING_UTILITIES': '没有冷却水或压缩空气（冷却程序不可用）', 'TIME_OUT_OF_RANGE': '非延长时间程序的灭菌时间不超过99 min',
    'DOOR_CLOSED': '门关着时控制器不许换程序（先开门）', 'NOT_AVAILABLE': '本型号没有该冷却方式（2、3为S.O.）', 'BAD_CLOCK': '日期或时间无效',
    'UNKNOWN_USER': '用户不存在', 'WRONG_CODE': '代码错误', 'TOO_MANY_USERS': '用户已达30个', 'CODE_IN_USE': '该代码已被其他用户使用',
    'SERVICE_REQUIRED': '手册列为“请联系维修”的故障消息仍在（评测者注入，fault.inject active=false清除）：不能开始周期',
}
_P = ['PLANT_REQUIRED']
_PW = _P + ['POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': _P, 'power.press_switch': _P, 'lid.set': _P + ['LID_LOCKED'],
    'load.add': _P + ['LID_CLOSED', 'LOAD_EXISTS', 'TOO_MANY_LOADS', 'MISSING_SETTING', 'UNKNOWN_LIQUID', 'CONTAINER_OVERFILLED', 'OVERLOAD'], 'load.remove': _P + ['LID_CLOSED', 'UNKNOWN_LOAD'],
    'program.select': _PW + ['CYCLE_RUNNING'], 'program.next': _PW + ['CYCLE_RUNNING'], 'program.configure': _PW + ['CYCLE_RUNNING', 'NO_COOLING_UTILITIES', 'TIME_OUT_OF_RANGE'],
    'cycle.start': _PW + ['CYCLE_RUNNING', 'LID_OPEN', 'NO_WATER_SUPPLY', 'NO_COOLING_UTILITIES', 'SERVICE_REQUIRED'], 'cycle.stop': _PW + ['NO_CYCLE', 'ACCESS_DENIED'],
    'cycle.toggle': _PW + ['CYCLE_RUNNING', 'NO_CYCLE', 'LID_OPEN', 'NO_WATER_SUPPLY', 'NO_COOLING_UTILITIES', 'ACCESS_DENIED', 'SERVICE_REQUIRED'], 'records.read': _PW, 'events.read': [], 'truth.inspect': [], 'ledger.read': [],
    'fault.inject': _P + ['NO_CYCLE'], 'sim.step': [],
    'program.step': _PW + ['CYCLE_RUNNING', 'DOOR_CLOSED'], 'program.set_parameters': _PW + ['CYCLE_RUNNING', 'NOT_AVAILABLE', 'NO_COOLING_UTILITIES'],
    'settings.configure': _PW, 'clock.set': _PW + ['BAD_CLOCK'], 'timer.configure': _PW, 'user.sign_in': _PW + ['UNKNOWN_USER', 'WRONG_CODE'],
    'user.create': _PW + ['TOO_MANY_USERS', 'CODE_IN_USE'], 'usb.export': _PW,
    **{k: [] for k in PANEL_CAPS},
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子；之后再声明须给同一种子', ['plant.configured']),
    'power.set': ('断电会使进行中的周期失败', ['power.on']), 'power.press_switch': ('同power.set', ['power.on']),
    'lid.set': ('打开时压力锁须放开：没有进行中的周期、腔压接近大气、腔温不高于结束温度', ['lid.locked', 'cycle.status', 'chamber.pressure_pa']),
    'load.add': ('顶盖已打开且开到至少一半（≥0.875 rad，lid.set后约1 s）；负载标识唯一；液体负载给液体与容器体积且液体装得下（液体质量≤容器体积×1600 kg/m³，取液体库中最密的氯代溶剂，否则CONTAINER_OVERFILLED）；液体容器合计≤20 L、器械与废物合计≤20 kg', ['lid.angle_rad', 'loads.present']),
    'load.remove': ('顶盖已打开且开到至少一半（≥0.875 rad）；负载存在', ['lid.angle_rad', 'loads.present']),
    'program.select': ('已通电；没有进行中的周期', ['power.on', 'cycle.status']), 'program.next': ('同program.select', ['power.on', 'cycle.status']),
    'program.configure': ('已通电；只能改可编辑程序；不能改正在运行的程序（周期中可改其他程序）；冷却排气需冷却水与压缩空气；非延长时间程序≤99 min', ['power.on', 'program.current', 'cycle.status']),
    'cycle.start': ('已通电；没有进行中的周期；顶盖完全关闭（lid.angle_rad为0，lid.set关闭后约2 s）；有纯水供应或腔内已有足量的水；冷却程序需冷却水与压缩空气', ['power.on', 'cycle.status', 'lid.angle_rad', 'alarms.active']),
    'cycle.stop': ('已通电；有进行中的周期（排气或排水中按停止：继续排气/排水，以失败结束）', ['power.on', 'cycle.status']), 'cycle.toggle': ('同cycle.start / cycle.stop', ['power.on', 'cycle.status', 'lid.angle_rad']),
    'records.read': ('已通电', ['power.on', 'records.stored']), 'fault.inject': ('无', ['plant.configured']),
    'program.step': ('已通电；没有进行中的周期；门已打开（门关着时控制器不许换程序）', ['power.on', 'cycle.status', 'lid.angle_rad']),
    'program.set_parameters': ('已通电；只改用户程序1/2；不能改正在运行的程序；冷却方式只有0与1（1需冷却水与压缩空气）；各脉冲组低压须低于高压', ['power.on', 'cycle.status', 'program.parameters']),
    'settings.configure': ('已通电', ['power.on']), 'clock.set': ('已通电；日期与时间须有效', ['power.on', 'clock.wall']), 'timer.configure': ('已通电', ['power.on']),
    'user.sign_in': ('已通电；用户存在且代码正确', ['power.on', 'users.names']), 'user.create': ('已通电；最多30个用户，代码不重复', ['power.on', 'users.names']),
    'usb.export': ('已通电（USB存储插在USB口）', ['power.on', 'records.stored']),
}
_EVOLVE = ['chamber.temperature_k', 'chamber.temperature2_k', 'chamber.pressure_pa', 'gauge.needle_rad', 'display.temperature_c', 'cycle.status', 'water.level_ok',
           'lid.locked', 'records.stored', 'alarms.active', 'truth.chamber', 'truth.loads', 'cycle.phase', 'cycle.repeats_left', 'clock.wall', 'timer.setting']
EFFECTS = {
    'plant.configure': ['plant.configured', 'chamber.temperature_k', 'chamber.temperature2_k', 'gauge.needle_rad', 'truth.chamber', 'alarms.active', 'lid.locked'],
    'power.set': ['power.on', 'chamber.temperature_k', 'chamber.temperature2_k', 'chamber.pressure_pa', 'display.temperature_c', 'cycle.status', 'records.stored', 'alarms.active',
                  'lid.locked'],
    'lid.set': ['alarms.active'],
    'load.add': ['loads.present', 'chamber.temperature2_k', 'truth.loads', 'truth.chamber', 'chamber.pressure_pa', 'gauge.needle_rad', 'lid.locked', 'alarms.active', 'chamber.temperature_k', 'display.temperature_c'],
    'load.remove': ['loads.present', 'chamber.temperature2_k', 'truth.loads', 'truth.chamber', 'chamber.pressure_pa', 'gauge.needle_rad', 'lid.locked', 'alarms.active', 'chamber.temperature_k', 'display.temperature_c'],
    'program.select': ['program.current', 'lid.locked', 'alarms.active'], 'program.configure': ['program.current', 'lid.locked', 'alarms.active'],
    'cycle.start': ['cycle.status', 'lid.locked', 'truth.loads', 'alarms.active'], 'cycle.stop': ['cycle.status', 'alarms.active', 'records.stored'],
    'fault.inject': ['truth.faults', 'chamber.temperature_k', 'chamber.pressure_pa', 'display.temperature_c', 'alarms.active'], 'sim.step': ['clock.time_s'],
}
for _k, _add in (('power.set', ['cycle.phase', 'cycle.repeats_left', 'timer.setting']), ('lid.set', ['cycle.repeats_left']),
                 ('program.select', ['program.parameters']), ('program.configure', ['program.parameters']), ('cycle.start', ['cycle.phase', 'cycle.repeats_left']),
                 ('cycle.stop', ['cycle.phase', 'cycle.repeats_left'])):
    EFFECTS[_k] = EFFECTS[_k] + _add                                        # effects on the ELV-D controller slots
EFFECTS.update({'program.step': ['program.current', 'program.parameters', 'lid.locked', 'alarms.active'], 'program.set_parameters': ['program.current', 'program.parameters'],
                'settings.configure': ['settings.system'], 'clock.set': ['clock.wall'], 'timer.configure': ['timer.setting'], 'user.sign_in': [],
                'user.create': ['users.names'], 'usb.export': ['usb.files']})
EFFECTS['power.press_switch'] = list(EFFECTS['power.set'])
EFFECTS['program.next'] = list(EFFECTS['program.select'])
EFFECTS['cycle.toggle'] = sorted(set(EFFECTS['cycle.start']) | set(EFFECTS['cycle.stop']))
_PANEL_CALLS = (_panel_ui.spec_capabilities(PANEL_SPEC) + ['user.sign_in', 'user.create']) if PANEL_SPEC else []   # the hooks call the user commands
_PANEL_EFFECTS = sorted(set(s for c in _PANEL_CALLS for s in EFFECTS.get(c, [])))
for _k in PANEL_CAPS:
    EFFECTS[_k] = [] if _k in ('panel.capture', 'panel.frame') else list(_PANEL_EFFECTS)   # a gesture changes what the commands it calls change
ON_STEP = {
    'sim.step': _EVOLVE + ['lid.angle_rad', 'clock.time_s'],
    'cycle.start': _EVOLVE, 'cycle.toggle': _EVOLVE, 'cycle.stop': _EVOLVE, 'lid.set': ['lid.angle_rad'],
    'power.set': ['chamber.temperature_k', 'cycle.status', 'truth.chamber'], 'power.press_switch': ['chamber.temperature_k', 'cycle.status', 'truth.chamber'],
    'load.add': ['truth.loads', 'chamber.temperature2_k'], 'fault.inject': _EVOLVE, 'plant.configure': ['truth.chamber', 'truth.loads'],
    'timer.configure': _EVOLVE, 'program.set_parameters': ['cycle.status', 'cycle.phase', 'cycle.repeats_left'],
    **{k: _EVOLVE for k in PANEL_CAPS if k not in ('panel.capture', 'panel.frame')},
}
ON_STEP_ZH = {
    'sim.step': '按0.5 s网格积分：进水、加热与排气阀置换空气、饱和蒸汽压、灭菌段控温与计时、排气/冷却/排水、负载中心温度、F0、杀灭、暴沸与破裂、报警与记录、顶盖转动',
    'cycle.start': '周期按进水→加热→灭菌→排气（或冷却）→排水→结束推进', 'cycle.toggle': '同cycle.start / cycle.stop', 'cycle.stop': '中止后转入排气并以失败结束',
    'lid.set': '顶盖约2 s转到位', 'power.set': '断电后加热停止、自然冷却', 'power.press_switch': '同power.set', 'load.add': '负载随腔温变化',
    'fault.inject': '故障作用于随后的加热、排气、密封、进水与传感器', 'plant.configure': '环境温度与大气压影响散热与沸点',
    'timer.configure': '到设定时刻且门关着时自动开始所选程序的周期（随后停用）', 'program.set_parameters': '下一个周期按新的脉冲、F0、冷却、干燥与多次循环参数运行',
    **{k: '手势调用的周期命令随后按step演化' for k in PANEL_CAPS if k not in ('panel.capture', 'panel.frame')},
}
EXEC_TEXT = {
    'cycle.start': ('cycle.status.stage依次为water_inlet、heating、sterilization、exhaust（或cooling后exhaust）、drain，最后complete（或failed）；结果写入records',
                    '报警或手动停止时转入排气并以“CYCLE FAIL”结束；已累计的F0与杀灭保留在负载上'),
}
RESOURCES = {k: ['host_clock'] for k in ALL_CAPS}
for _k in ('cycle.start', 'cycle.stop', 'cycle.toggle', 'lid.set', 'load.add', 'load.remove'):
    RESOURCES[_k] = ['host_clock', 'pressure_chamber']
OBSERVES = {'cycle.start': ['cycle.status', 'chamber.temperature_k', 'chamber.pressure_pa'], 'cycle.toggle': ['cycle.status'], 'lid.set': ['lid.angle_rad'],
            'cycle.stop': ['cycle.status']}
_WB, _PR, _EM, _ET, _IA, _NG, _TD, _WB2, _NI, _SIM = ('user_workbook', 'manufacturer_product_page', 'same_platform_elv_manual', 'same_platform_elv_technician_manual',
                                                     'iapws_if97', 'thermo_nalgene', 'thermal_destruction_notes', 'wells_bennik_2019', 'nist_webbook', 'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in ALL_CAPS}
CAP_SOURCES.update({'power.set': [_PR], 'power.press_switch': [_EM], 'lid.set': [_PR], 'load.add': [_NG, _NI, _TD, _WB2, _EM], 'load.remove': [_NG],
                    'program.select': [_PR, _EM, _ET], 'program.next': [_EM], 'program.configure': [_PR, _ET], 'cycle.start': [_PR, _EM, _ET, _IA, _SIM],
                    'cycle.stop': [_EM], 'cycle.toggle': [_EM], 'records.read': [_PR, _EM], 'fault.inject': [_EM, _ET, _SIM]})
CAP_SOURCES.update({k: [_PR] for k in UNSUPPORTED})
_ELVD = 'elvd_operation_manual_fr'
CAP_SOURCES.update({k: [_ELVD, _SIM] for k in APP_CAPS + list(PANEL_CAPS)})
CAP_SOURCES['program.select'] = CAP_SOURCES['program.select'] + [_ELVD]
PARAM_ZH = {'ambient_k': '环境温度', 'atmospheric_pressure_pa': '大气压（海拔）', 'water_supply': '纯水供应是否接通', 'compressed_air': '压缩空气（冷却选件）是否接通',
            'cooling_water': '冷却自来水（冷却选件）是否接通', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.steam_sterilization_process.limitations。'
CAP_LIMITS = {
    'cycle.start': ['程序默认参数取自同平台3870 ELV技术手册与操作手册（ELV-D未公开同等细节）；加热、排气、冷却速率为工程值并与同平台的平均周期时间对照。',
                    '重力置换排气：排气阀打开时气相保持大气压，水接近沸点时空气被蒸汽逐出（单节点模型：沸腾时空气即已排尽；air_purge_time_s只决定排气阀何时关闭）；不含预真空（选件未选）。'],
    'load.add': ['负载为集总热容：液体按瓶内一个温度，固体与废物袋按材料时间常数；容器自身热容未计；生物指示物按一级失活（D、z）。'],
    'records.read': ['记录格式按同平台打印样例的含义（阶段、温度、压力）；不复制厂家文件格式与RPCR软件。'],
    'lid.set': ['压力锁：腔压高于大气3 kPa（工程值）、周期进行中或温度高于结束温度时不放开；顶盖手动开合为宿主/机器人代理。'],
    'program.step': ['3870ELV-D的10个程序（法文操作手册D版第5章）；固定程序的脉冲次数（1次）与无干燥为假设（手册只写“加热到预设的高压脉冲”）。'],
    'program.set_parameters': ['表6.2参数的最小模型：脉冲组按“加热到高压—排气到低压并保持”重复（无真空泵：低压不低于大气压，之后仍按重力排气阀置换空气）；F0模式下灭菌时间按F0累计；'
                               '冷却后按冷却排气速率降压；干燥为两段加热器通/断（第二段时长取与第一段相同，为假设）；多次循环在间隔后自动再启动（开门即停止）。'],
    'settings.configure': ['打印间隔只是保存的设定（打印机为未选选件，不影响过程）；屏幕保护延时由前面板层使用。'],
    'user.sign_in': ['用户与代码为控制器设定；非Admin用户的权限手册未写，进入同一主菜单（假设）。'],
    'usb.export': ['假设USB存储一直插在USB口；导出内容为运行层记录的结构化副本，不复制厂家文件格式。'],
    'timer.configure': ['定时启动为一次性：到点启动后自动停用（手册未写是否每日重复）；门开着时等待关门。'],
    **{k: ['面板层按panel_ui.json（3870ELV-D法文操作手册页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟；手册未写之处的选择见panel_ui.json的assumptions。']
       for k in PANEL_CAPS},
}
LIMITATIONS = [
    '所选配置：3870ELV-D标准机型＋冷却选件（冷却盘管＋压缩空气）；真空泵、蒸汽发生器、生物过滤、培养基等温程序、打印机与RPCR为不支持；ELV-D未公开的加热功率、报警阈值与程序参数取自同平台3870 ELV手册（已标注same_platform_elv）。',
    '腔体：水＋腔壁一个热节点；有水时气相为饱和水蒸气（IAPWS-IF97）加残留空气（理想气体）；汽化潜热用IAPWS饱和线上的克劳修斯–克拉佩龙关系（比蒸汽表高约1.6–3.4 %：100 °C 1.6 %、121 °C 2.6 %、134 °C 3.4 %）。',
    '负载：集总温度；液体暴沸按过热超过阈值时闪蒸与溢出、密封容器按内压（液体蒸气压＋顶空空气）超过腔压1.5 bar破裂（工程值）；塑料按Nalgene最高使用温度判断变形。',
    '灭菌效力：F0（z=10 K，参考121.1 °C）与按生物指示物D121、z计算的存活对数；腔温达标不等于所有负载灭菌完成（负载中心温度滞后）。',
    '负载、液体与微生物在场景中没有几何；场景中顶盖、压力表指针与显示屏数字按运行层状态变化。',
    '控制器（3870ELV-D法文操作手册D版）：10个程序、表6.2周期参数（脉冲、F0、冷却、干燥、多次循环）、时钟、定时启动、用户代码与USB导出在运行层实现，前面板层（panel_ui.json）调用它们；早期ELV程序键保留给API。',
]


def _sampling_text(k):
    if k in ('clock.time_s',):
        return 'Host clock.'
    if SLOTS[k]['origin'] == 'sensor' or k == 'display.temperature_c':
        return 'Integrated on the fixed 0.5 s grid (anchored at t = 0); between grid points the last value is held; sensor noise is drawn per grid tick.'
    if k.startswith(('chamber.', 'truth.', 'water.', 'gauge.', 'cycle.')) or k in ('alarms.active', 'lid.locked', 'records.stored'):
        return 'Integrated on the fixed 0.5 s grid (anchored at t = 0); between grid points the last value is held.'
    if k in SCENE_MIRRORED:
        return 'Lid angle at the host time (turns at a fixed rate).'
    return 'Changes when a command is accepted.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if ALL_CAPS[k]['visibility'] != 'agent' else []
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
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景关节或显示（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节或读数', binding_ids=[])


def _si(key, value, unit):
    """Mechanism parameters in SI: K for temperatures, s, Pa, m, m3, kg, W; °C / min / psig / l only appear in the meaning text."""
    return value, unit


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    src_of = {'manufacturer_product_page': _PR, 'same_platform_elv_manual': _EM, 'same_platform_elv_technician_manual': _ET}
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], ('[同平台3870 ELV] ' if v['platform'] != '3870ELV-D' else '') + v['note_zh'] + '（原文：' + v['quote'] + '）',
            (src_of[v['source_id']],) + ((_WB,) if k in ('overall_dimensions_m', 'chamber_dimensions_m', 'chamber_volume_m3', 'sterilization_temperature_range_k', 'mass_kg',
                                                          'stored_cycles', 'extended_time_max_s') else ()))
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明）' + ('；按同平台平均周期时间标定：周期时间对照偏差约−7…+1 %（冷却段−4 %），取值不确定度约±30 %（不是实测）' if k in CALIBRATED else ''))
    asm('burst_dp_pa', BURST_DP, 'Pa', '密封玻璃/塑料容器能承受的内外压差（超过即破裂）', '工程值（容器差异很大）')
    asm('lid_open_rad', LID_OPEN, 'rad', '顶盖开角', '工程值')
    asm('gauge_full_scale_pa', GAUGE_FULL, 'Pa', '机械压力表满量程（表压）', '工程值')
    return P


CALIBRATED = {'slow_exhaust_rate_pa_s', 'cooling_coil_ua_W_K', 'h_liquid_bottle_W_m2K', 'h_air_bottle_W_m2K'}
SLOT_REFERENCE = {'chamber.temperature_k': '热力学温度（ITS-90）', 'chamber.temperature2_k': '热力学温度（ITS-90）', 'chamber.pressure_pa': '绝压',
                  'lid.angle_rad': '顶盖绕左侧铰链（资产−Y轴）自关闭位置的转角', 'gauge.needle_rad': '压力表指针自零位顺时针的转角（满量程4 bar表压=1.5π rad）',
                  'display.temperature_c': '摄氏温度数值（显示用，不是SI量）'}


def export_tuttnauer_3870elvd_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'TuttnauerElvdSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in ALL_CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.tuttnauer_3870elvd.TuttnauerElvdSession.invoke',
                                              invocation=f'session.invoke(instance_id="tuttnauer_3870elvd_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='TuttnauerElvdRuntime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（周期阶段、温度与压力在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k == 'cycle.start':                                               # an operation: the starter polls / cancels it
            c['execution'].update(mode='asynchronous', cancellation='supported', completion_state_ids=['cycle.status'],
                                  completion_criterion='周期结束（CYCLE ENDED）、手动停止或失败；poll(operation_id)给出running/succeeded/cancelled/failed')
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['pressure_chamber', 'heater', 'water_electrode', 'chamber_lid', 'load_basket', 'control_console', 'pressure_gauge', 'cooling_coil'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='重力置换式立式高压蒸汽灭菌：腔底6 kW加热水产生饱和蒸汽，排气阀开启沸腾置换空气后关闭升压；灭菌段控温计时；按程序快排、慢排或冷却盘管＋压缩空气冷却后排气、排水；负载通过冷凝换热升温，累计F0与杀灭',
        equations=['p_sat(T) = IAPWS-IF97 Eq. (30);  P = p_sat(T_w) + n_air R T_w / V_gas',
                   '(C_vessel + m_w c_w) dT_w/dt = P_heater - sum_i UA_i (T_w - T_i) - UA_loss (T_w - T_amb) - Q_coil  (air valve open at the boiling point: excess heat vents steam, n_air decays with tau_purge)',
                   'm_i c_i dT_i/dt = UA_i (T_w - T_i);  UA_i = h A_i (liquid containers) or m_i c_i / tau (solids, waste)',
                   'F0 = int 10^((T - 121.1 C)/10 K) dt;  log10 N = log10 N0 - int dt / (D121 10^((121.1 C - T)/z))',
                   'exhaust: P -> P_atm with tau (fast) or at a fixed rate (slow); flashing m_f = C dT / h_fg, h_fg = R T^2 dln p_sat/dT / M',
                   'boil-over: T_i > T_sat,i(P) + dT_superheat;  burst: p_sat,i(T_i) + p_air,0 T_i/T_fill - P > dp_burst'],
        parameters=_params(), source_ids=[_SIM, _WB, _PR, _EM, _ET, _IA, _NG, _TD, _WB2, _NI], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_tut_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('tuttnauer_3870elvd', caps, slots, {'steam_sterilization_process': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'steam_sterilization_process': mechanism})
