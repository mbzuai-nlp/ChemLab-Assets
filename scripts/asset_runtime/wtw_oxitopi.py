"""CHEM-171 WTW OxiTop-i respirometric BOD measuring system (OxiTop-i IS 6 set): process runtime.

Device (OxiTop-i / IDS operating manual ba77059e02, WTW BOD primer, WTW product catalog 2019, distributor pages): OxiTop-i IS 6 = six OxiTop-i
measuring heads (3 blue, 3 grey; H 69 x 70 mm, 85 g, CR 2450 battery, keys <M>, <ENTER> and navigation key, graphic display, three-colour status LED;
pressure 500-1250 hPa, BOD 0-4000 mg/l, accuracy +-0.5 % of the measured value +-1 hPa, 20 +- 1 C measuring temperature, 15-21 C filling
temperature; duration 1-7 days with one stored value per day; units mg/l, dhPa, hPa, digit; sample volumes 22.7-432 ml), six PF 600 amber bottles
(510 ml), rubber sleeves GK 600 L with NaOH pellets (CO2 absorber), RST 600 stirring bars and the inductive stirring platform IS 6 (265 x 181 x
67 mm, 1.6 kg, 180-450 rpm).  The thermostat cabinet (20 C) is host environment.

Process model (fixed 60 s grid): per bottle a closed gas phase (O2, inert gas, CO2, saturated water vapour; ideal gas) over the liquid sample;
oxygen exchange gas <-> liquid with kLa = kL A / V (inductive stirring or diffusion only) towards the Benson-Krause solubility; biological oxygen
uptake of every substance (pool = concentration x ThOD x f_ult from the template library, first-order rate k20 with theta 1.047^(T-20), dissolved
oxygen limitation, seed / adaptation lag, pH, toxicity, free chlorine), endogenous respiration of the seed, nitrification of ammonium (4.57 mg O2 /
mg N, nitrifier growth, allylthiourea inhibition), sulfite oxidation (OxiTop PM tablet); CO2 production (respiratory quotient) into the dissolved
inorganic carbon, CO2(aq) exchange with the gas and absorption by the NaOH pellets (capacity from the pellet mass); liquid temperature relaxing to
the cabinet air (door opening disturbs the cabinet); leaks.  Measuring head: piezo-resistive sensor (gain / offset / noise within the documented
accuracy, 1 hPa resolution), AutoTemp (60 min adaption, test interval, start within 3 h), zero, BOD by the WTW equation with the selected sample
volume and 510 ml / 20 C, daily stored values, overflow at 50 digits, status LED (blue / red / green, flashing every 5 s), display and key menu,
battery drain; OxiTop PT pneumatic test.  Evaluation: seeded-dilution BOD correction."""
import copy
import json
import math
import re
import zlib
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.wtw_oxitopi"
CATALOG_ID = "CHEM-171"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t4_measure/wtw_oxitopi'
LIBS = {'substances': 'substance_library.json', 'samples': 'sample_library.json', 'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- libraries
_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def substances():
    return _lib('substances')['entries']


def sample_lib():
    return _lib('samples')


def instrument():
    return _lib('instrument')


DOC = instrument()['documented']
ENG = instrument()['engineering']
ENG_NOTE = instrument()['engineering_note_zh']
DT = 60.0                                    # physics grid (s)
R_GAS = 8.314462618
M_NAOH = 0.039997                            # kg/mol
M_O2 = 31.998                                # g/mol
P_STD = 101325.0
AIR_O2 = 0.20946                             # dry-air mole fraction
AIR_CO2 = 0.0004
HEADS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']
POSITIONS = ['P1', 'P2', 'P3', 'P4', 'P5', 'P6']
HEAD_COLOR = dict(h1='blue', h2='blue', h3='blue', h4='grey', h5='grey', h6='grey')
VOLUMES_ML = [float(v) for v in DOC['sample_volumes_ml']]
UNITS = ['mg/l', 'dhPa', 'hPa', 'digit']
EQ = DOC['bod_equation']
V_TOT_ML = EQ['V_tot_ml']
BOTTLE_M3 = DOC['bottle_volume_m3']
HEAD_LIFT_M = 0.09                           # scene travel of a head lifted off its bottle (engineering)
SEED_TYPES = ['none', 'sewage', 'activated_sludge', 'adapted']
FAULTS = ['head_untight', 'pellets_in_sample', 'incubator_failure', 'battery_drain']
KITS = ['gga_standard', 'gga_stock_r10', 'distilled_water', 'nth_600', 'nhp_600', 'oxitop_pm', 'dilution_water', 'settled_sewage_seed', 'sodium_sulfite_r11', 'domestic_influent', 'domestic_effluent',
        'river_water', 'food_industry']
MAX_SAMPLES = 80
ALK_DEFAULT = 2.0                            # mol/m3 carbonate alkalinity of a provided water sample with pH >= 6 when not declared (engineering)
STIRRED_RATE_FACTOR = ENG.get('stirred_rate_factor', 1.0)
BATTERY_FAULT_FACTOR = 400.0                 # fault 'battery_drain': discharge 400 x faster (low-battery state within about one day of measuring)
LEAK_NO_SLEEVE_PER_S = 1e-4                  # head screwed on without the rubber sleeve: the neck does not seal (engineering)
N_PER_NH4CL = 14.007 / 53.49
MAX_EVENTS_LOG = 1500
MAX_TRACE = 20000
PSEUDO = {   # wastewater pseudo-components (engineering): mg O2 per mg COD 1, first-order rates at 20 C
    'ww_readily': dict(name='readily biodegradable wastewater COD', thod_nh4_mg_mg=1.0, kinetics=dict(f_ult=1.0, k20_per_day=0.6, lag_days=0.0), evidence='assumption'),
    'ww_slowly': dict(name='slowly biodegradable wastewater COD', thod_nh4_mg_mg=1.0, kinetics=dict(f_ult=1.0, k20_per_day=0.12, lag_days=0.0), evidence='assumption'),
    'ww_inert': dict(name='inert wastewater COD', thod_nh4_mg_mg=1.0, kinetics=dict(f_ult=0.0, k20_per_day=0.0, lag_days=0.0), evidence='assumption'),
}
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


class _Sum:
    def __init__(self):
        self.s = 0.0
        self.c = 0.0

    def add(self, x):
        t = self.s + x
        if abs(self.s) >= abs(x):
            self.c += (self.s - t) + x
        else:
            self.c += (x - t) + self.s
        self.s = t

    def value(self):
        return self.s + self.c


# ---------------------------------------------------------------- gas / water properties (Benson and Krause, USGS OWQ TM 2011.03)
_BK = DOC['oxygen_solubility']


def p_vapour(T):
    """Saturation vapour pressure of water (Pa), Benson-Krause eq. 11."""
    a, b, c = _BK['vapour_pressure_atm']
    return P_STD * math.exp(a + b / T + c / (T * T))


def do_saturation_1atm(T):
    """Air-saturated dissolved oxygen at 1 atm moist air (mg/l), Benson-Krause eq. 7."""
    c0, c1, c2, c3, c4 = _BK['DO0_coefficients']
    return math.exp(c0 + c1 / T + c2 / T ** 2 + c3 / T ** 3 + c4 / T ** 4)


def henry_o2(T):
    """Dissolved oxygen per unit O2 partial pressure (mg/l per Pa) at temperature T."""
    return do_saturation_1atm(T) / (AIR_O2 * (P_STD - p_vapour(T)))


def bod_factor(volume_ml):
    """WTW BOD equation: mg/l per hPa of oxygen partial-pressure difference for the selected sample volume (510 ml bottle, 20 C)."""
    M, R, T0, Tm, a = EQ['M_O2_mg_mol'], EQ['R_l_hpa_mol_k'], EQ['T0_k'], EQ['Tm_k'], EQ['alpha_bunsen']
    return M / (R * Tm) * ((V_TOT_ML - volume_ml) / volume_ml + a * Tm / T0)


DIGIT_HPA = 1.0 / bod_factor(432.0)          # 1 digit = mg/l at the 432 ml setting (manual table factor 1): about 3.56 hPa


PKA_C = ENG['co2_pka1']                      # CO2(aq) / HCO3-
PKA_P = 7.20                                 # H2PO4- / HPO4(2-) (engineering constant at about 20 C)
LN10 = math.log(10.0)
CO2_AQ_MAX = 30.0                            # mol/m3: cap of the CO2(aq) implied by a declared pH and alkalinity (about 1 atm CO2)


def alk_of(ph, dic, pt):
    """Proton-acceptor balance (mol/m3) relative to CO2(aq), H2PO4- and H2O: [HCO3-] + [HPO4(2-)] + [OH-] - [H+] (carbonate below pH 8.3)."""
    r1, r2 = 10.0 ** (ph - PKA_C), 10.0 ** (ph - PKA_P)
    return dic * r1 / (1.0 + r1) + pt * r2 / (1.0 + r2) + 10.0 ** (ph - 11.0) - 10.0 ** (3.0 - ph)


def ph_of(alk, dic, pt, guess=7.0):
    """pH from the alkalinity, DIC and total phosphate (safeguarded Newton iteration on the monotone balance; 2 <= pH <= 12.5)."""
    lo, hi = 2.0, 12.5
    if alk <= alk_of(lo, dic, pt):
        return lo
    if alk >= alk_of(hi, dic, pt):
        return hi
    x = min(max(guess, lo + 1e-6), hi - 1e-6)
    for _ in range(60):
        f = alk_of(x, dic, pt) - alk
        if abs(f) <= 1e-11 * (1.0 + abs(alk)) or hi - lo < 1e-9:
            break
        if f < 0.0:
            lo = x
        else:
            hi = x
        r1, r2 = 10.0 ** (x - PKA_C), 10.0 ** (x - PKA_P)
        d = LN10 * (dic * r1 / (1.0 + r1) ** 2 + pt * r2 / (1.0 + r2) ** 2 + 10.0 ** (x - 11.0) + 10.0 ** (3.0 - x))
        nx = x - f / d if d > 0 else 0.5 * (lo + hi)
        x = nx if lo < nx < hi else 0.5 * (lo + hi)
    return x


def dic_of(ph, alk_c):
    """DIC (mol/m3) of a declared carbonate alkalinity at a declared pH ([HCO3-] = alkalinity, [CO2(aq)] = alkalinity 10^(pKa1 - pH))."""
    return alk_c + min(CO2_AQ_MAX, alk_c * 10.0 ** (PKA_C - ph)) if alk_c > 0.0 else 0.0


def co2_aq(L):
    """CO2(aq) (mol/m3) of a liquid."""
    return L['dic'] / (1.0 + 10.0 ** (L['ph'] - PKA_C))


def _ph_factor(ph):
    lo, hi = ENG['ph_tolerance']
    d = (lo - ph) if ph < lo else (ph - hi) if ph > hi else 0.0
    return math.exp(-(d / ENG['ph_width']) ** 2)


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
INT = {'type': 'integer', 'x-unit': '1'}


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


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.:+-]+$')
HEAD = enum(HEADS)
POS = enum(POSITIONS)
VOL = {'type': 'number', 'x-unit': 'm3', 'enum': [v * 1e-6 for v in VOLUMES_ML]}
SUBST = dict(type='string', minLength=1, maxLength=40, pattern='^[a-z0-9_]+$')
SUB_C = obj(dict(substance_id=SUBST, concentration_kg_m3=number('kg/m3', 0.0, 50.0)))
INHIB = obj(dict(name=SID, concentration_kg_m3=number('kg/m3', 0.0, 10.0), ic50_kg_m3=number('kg/m3', 1e-6, 10.0), hill=number('1', 0.5, 4.0)), optional=('hill',))
SEED = obj(dict(type=enum(SEED_TYPES), volume_fraction=number('1', 0.0, 1.0)))       # 1 = the water carries its own biomass (raw wastewater)
COMPOSITION = obj(dict(substances=array(SUB_C, 0, 20), nh4_n_kg_m3=number('kg/m3', 0.0, 2.0), ph=number('1', 2.0, 12.0), seed=SEED, nitrifier_activity=number('1', 0.0, 1.0),
                       inhibitors=array(INHIB, 0, 6), free_chlorine_kg_m3=number('kg/m3', 0.0, 0.05), temperature_k=number('K', 273.15, 318.15),
                       dissolved_oxygen_kg_m3=number('kg/m3', 0.0, 0.02), alkalinity_mol_m3=number('mol/m3', 0.0, 20.0), phosphate_mol_m3=number('mol/m3', 0.0, 50.0)),
                  optional=('nh4_n_kg_m3', 'seed', 'nitrifier_activity', 'inhibitors', 'free_chlorine_kg_m3', 'dissolved_oxygen_kg_m3', 'temperature_k',
                            'alkalinity_mol_m3', 'phosphate_mol_m3'))
PLANT_EXAMPLE = dict(incubator_k=293.15, room_k=295.15, ambient_pressure_pa=101325.0, noise_seed=1)
PLANT = obj(dict(incubator_k=number('K', 278.15, 313.15), room_k=number('K', 283.15, 313.15), ambient_pressure_pa=number('Pa', 70000.0, 108000.0),
                 noise_seed=integer(0, 2 ** 31 - 1)))
EVENT = obj(dict(time_s=number('s'), kind=enum(['head', 'bottle', 'sample', 'platform', 'incubator', 'warning', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
STORED = obj(dict(day=INT, value=nullable(number('1')), unit=TEXT, over_range=BOOL, delta_p_pa=nullable(number('Pa'))))
HEAD_READ = obj(dict(head=TEXT, display_on=BOOL, screen=TEXT, lines=array(TEXT, 0, 6), value=nullable(number('1')), unit=TEXT, label=TEXT, state=TEXT,
                     stored=array(STORED, 0, 7), led=enum(['off', 'blue', 'red', 'green']), battery_low=BOOL))

cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['inductive_platform'],
    '宿主声明实验室条件：恒温箱设定温度（手册要求20±1 °C）、室温、环境气压（海拔）与噪声种子（只在首次声明时生效）', 'evaluator', 'simulation')
cap('stock.provide', obj(dict(stock_id=SID, volume_m3=number('m3', 1e-6, 0.01), composition=COMPOSITION)), obj(dict(stock_id=TEXT, volume_m3=number('m3'))),
    dict(stock_id='ww_sample', volume_m3=0.001, composition=dict(substances=[dict(substance_id='glucose', concentration_kg_m3=0.15)], ph=7.2,
                                                                 seed=dict(type='sewage', volume_fraction=0.01))), ['inductive_platform'],
    '评测者提供真实水样（组成对agent隐藏：可降解物质、氨氮、pH、接种、硝化活性、抑制物、游离氯、温度、溶解氧）', 'evaluator', 'simulation')
cap('kit.provide', obj(dict(kit=enum(KITS), volume_m3=number('m3', 1e-6, 0.01), lot_test_value_kg_m3=number('kg/m3', 0.05, 1.0)), optional=('volume_m3', 'lot_test_value_kg_m3')),
    obj(dict(items=array(TEXT, 0, 8))), dict(kit='nth_600'), ['inductive_platform'],
    '提供试剂与样品模板：葡萄糖–谷氨酸标准溶液（V5 150+150 mg/l、R10储备液）、蒸馏水、NTH 600、NHP 600 NaOH颗粒、OxiTop PM片剂（批次检验值）、稀释水（R14营养液）、'
    '沉淀生活污水接种液、亚硫酸钠溶液、污水模板（再次提供同一溶液时增加体积）', 'evaluator', 'simulation')
cap('substance.define', obj(dict(substance_id=SUBST, name=TEXT, thod_kg_kg=number('1', 0.0, 4.0), f_ult=number('1', 0.0, 1.0), k20_per_s=number('1/s', 0.0, 1e-3),
                                 lag_s=number('s', 0.0, 2e6), ic50_kg_m3=number('kg/m3', 1e-6, 10.0)), optional=('lag_s', 'ic50_kg_m3')),
    obj(dict(substance_id=TEXT)), dict(substance_id='cmpd_x', name='compound X', thod_kg_kg=1.5, f_ult=0.8, k20_per_s=3e-6), ['inductive_platform'],
    '评测者定义物质（理论需氧量、可降解比例、20 °C一级速率、滞后期、抑制浓度）', 'evaluator', 'simulation')
cap('sample.prepare', obj(dict(sample_id=SID, volume_m3=number('m3', 1e-6, 0.005), sources=array(obj(dict(source_id=SID, volume_m3=number('m3', 1e-8, 0.005))), 0, 4),
                               diluent=enum(['none', 'dilution_water', 'distilled_water']), seed_volume_fraction=number('1', 0.0, 0.1), atu_kg_m3=number('kg/m3', 0.0, 0.02),
                               ph_adjust_to=number('1', 6.0, 8.0), aerate=BOOL, dechlorinate=BOOL, temperature_k=number('K', 278.15, 313.15)),
                          optional=('diluent', 'seed_volume_fraction', 'atu_kg_m3', 'ph_adjust_to', 'aerate', 'dechlorinate', 'temperature_k')),
    obj(dict(sample_id=TEXT, volume_m3=number('m3'), dilution_factor=number('1'))),
    dict(sample_id='s1', volume_m3=5e-4, sources=[dict(source_id='ww_sample', volume_m3=5e-4)], aerate=True, temperature_k=293.15), ['inductive_platform'],
    '配制测量溶液：取样（可稀释到稀释水或蒸馏水）、接种（沉淀生活污水）、加ATU、调pH 6.6–7.2、充氧（摇匀约15 min到饱和）、去氯、预调温（装瓶时须15–21 °C）', 'agent', 'robot_skill')
cap('bottle.fill', obj(dict(position=POS, sample_id=SID, volume_m3=VOL)), obj(dict(position=TEXT, volume_m3=number('m3'))),
    dict(position='P1', sample_id='s1', volume_m3=4.32e-4), ['bottle_1', 'bottle_2', 'bottle_3', 'bottle_4', 'bottle_5', 'bottle_6'],
    '用溢流量瓶取样（22.7、43.5、97、164、250、365、432 ml之一）并经漏斗倒入PF 600样品瓶', 'agent', 'robot_skill')
cap('bottle.add_stir_bar', obj(dict(position=POS)), obj(dict(position=TEXT)), dict(position='P1'), ['magnet_rotor_1', 'magnet_rotor_2', 'magnet_rotor_3', 'magnet_rotor_4', 'magnet_rotor_5', 'magnet_rotor_6'],
    '放入RST 600磁力搅拌子', 'agent', 'robot_skill')
cap('bottle.add_reagent', obj(dict(position=POS, reagent=enum(['nth_600', 'oxitop_pm', 'sodium_sulfite_r11']), drops=integer(1, 30), volume_m3=number('m3', 1e-7, 5e-5)),
                          optional=('drops', 'volume_m3')),
    obj(dict(position=TEXT, reagent=TEXT)), dict(position='P1', reagent='nth_600', drops=9), ['bottle_1', 'bottle_2', 'bottle_3', 'bottle_4', 'bottle_5', 'bottle_6'],
    '向样品瓶加试剂：NTH 600硝化抑制剂（按滴：432 ml加9滴…22.7 ml加1滴）、OxiTop PM校准片（164 ml、无NaOH）、亚硫酸钠溶液', 'agent', 'robot_skill')
cap('bottle.insert_sleeve', obj(dict(position=POS, naoh_pellets=integer(0, 3))), obj(dict(position=TEXT, naoh_pellets=INT)), dict(position='P1', naoh_pellets=2),
    ['sleeve_1', 'sleeve_2', 'sleeve_3', 'sleeve_4', 'sleeve_5', 'sleeve_6'], '把GK 600橡胶套插入瓶颈并放入NaOH颗粒（通常2粒；PM检查不放）', 'agent', 'robot_skill')
cap('bottle.empty', obj(dict(position=POS)), obj(dict(position=TEXT, removed_m3=number('m3'))), dict(position='P1'),
    ['bottle_1', 'bottle_2', 'bottle_3', 'bottle_4', 'bottle_5', 'bottle_6'], '倒空并清洗样品瓶（取出搅拌子与橡胶套，不用洗涤剂；须先取下测头）', 'agent', 'robot_skill')
cap('head.screw_on', obj(dict(head=HEAD, position=POS, tight=BOOL), optional=('tight',)), obj(dict(head=TEXT, position=TEXT, sealed=BOOL)),
    dict(head='h1', position='P1'), ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'], '把OxiTop-i测头拧到样品瓶上（橡胶套密封；不涂润滑脂）', 'agent', 'robot_skill')
cap('head.unscrew', obj(dict(head=HEAD)), obj(dict(head=TEXT)), dict(head='h1'), ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'], '从样品瓶上拧下测头（测量中拧下会使压力回到环境）',
    'agent', 'robot_skill')
cap('head.configure', obj(dict(head=HEAD, duration_days=integer(1, 7), unit=enum(UNITS), sample_volume_m3=VOL), optional=('duration_days', 'unit', 'sample_volume_m3')),
    obj(dict(head=TEXT, duration_days=INT, unit=TEXT, sample_volume_m3=number('m3'))), dict(head='h1', duration_days=5, unit='mg/l', sample_volume_m3=4.32e-4),
    ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'], '测量设置（<ENTER>菜单）：测量天数1–7、单位（mg/l、ΔhPa、hPa、digit）、样品体积（7档）', 'agent', 'device_command')
cap('head.set_system', obj(dict(head=HEAD, head_id=integer(0, 999), illumination=BOOL, signal_led=BOOL, switch_off_s=number('s', 0.0, 540.0)),
                           optional=('head_id', 'illumination', 'signal_led', 'switch_off_s')),
    obj(dict(head=TEXT, head_id=INT)), dict(head='h1', head_id=11), ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'],
    '系统设置（<ENTER_>菜单）：测头ID 0–999、显示照明、状态灯、自动关闭时间0–9 min', 'agent', 'device_command')
cap('head.start', obj(dict(head=HEAD)), obj(dict(head=TEXT, state=TEXT)), dict(head='h1'), ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'],
    '在BOD参数信息页按<M>开始测量（AutoTemp：60 min适应、测试间隔、最迟3 h开始；之前存储的值被删除）', 'agent', 'device_command')
cap('head.stop', obj(dict(head=HEAD)), obj(dict(head=TEXT, state=TEXT)), dict(head='h1'), ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'],
    '<M_>关闭测头（测量中有确认提示；确认后测量停止，已存储的值保留）', 'agent', 'device_command')
cap('head.press_key', obj(dict(head=HEAD, key=enum(['M', 'ENTER', 'NAV']), long=BOOL), optional=('long',)), obj(dict(head=TEXT, screen=TEXT, lines=array(TEXT, 0, 6))),
    dict(head='h1', key='M'), [f'head_{i}' for i in range(1, 7)] + [f'head_{i}_key_{k}' for i in range(1, 7) for k in ('m', 'enter', 'nav')],
    '按测头按键（<M>、<ENTER>、导航键；长按约2 s）：开机、信息页、开始测量、菜单与设置、查看存储值', 'agent', 'device_command')
cap('head.read', obj(dict(head=HEAD)), HEAD_READ, dict(head='h1'), ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'],
    '按任意键唤醒显示并读取：当前值（BODx.y）、单位、状态、存储值与状态灯', 'agent', 'device_command')
cap('head.replace_battery', obj(dict(head=HEAD)), obj(dict(head=TEXT)), dict(head='h1'), ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'],
    '更换CR 2450电池（测量中也可更换，数据不丢失）', 'agent', 'robot_skill')
cap('head.pt_test', obj(dict(head=HEAD)), obj(dict(head=TEXT, measured_digits=number('1'), control_digits=INT, deviation_digits=number('1'), passed=BOOL, delta_p_pa=number('Pa'))),
    dict(head='h1'), ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'],
    'OxiTop PT气动检查（Service/OxiTop PT：注射器0.5→2.0 ml产生负压；与海拔对应的控制值比较，允许±3 digit）', 'agent', 'robot_skill')
cap('platform.set_power', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['inductive_platform'], '接通/断开IS 6感应搅拌台电源（电源由恒温箱插座供给）', 'agent', 'device_command')
cap('incubator.set_door', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['inductive_platform'],
    '开关恒温箱门（宿主环境；开门时箱内温度波动，影响瓶内压力）', 'agent', 'robot_skill')
cap('eval.bod', obj(dict(sample_value_kg_m3=number('kg/m3', -1.0, 10.0), blank_value_kg_m3=number('kg/m3', -1.0, 1.0), sample_volume_m3=number('m3', 1e-7, 0.01),
                         total_volume_m3=number('m3', 1e-7, 0.01))),
    obj(dict(bod_kg_m3=number('kg/m3'), dilution_factor=number('1'), formula=TEXT)),
    dict(sample_value_kg_m3=0.03, blank_value_kg_m3=0.0015, sample_volume_m3=2e-5, total_volume_m3=1e-3), ['inductive_platform'],
    '评估：接种稀释样的BOD校正 BOD = (A − B (Vt − Ve)/Vt) × Vt/Ve（BOD入门手册）', 'agent', 'analysis')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=INT)), dict(since_s=0.0), ['inductive_platform'], '读取事件记录', 'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(name=TEXT, file=TEXT, entries=INT)), 0, 8), substances=array(TEXT, 0, 200))), {}, ['inductive_platform'],
    '模板库来源与可用物质', 'agent', 'query')
cap('bottle.inspect', obj(dict(position=POS)), obj(dict(position=TEXT, state=dict(type='object'))), dict(position='P1'), ['bottle_1', 'bottle_2', 'bottle_3', 'bottle_4', 'bottle_5', 'bottle_6'],
    '评测者读取瓶内真值（溶解氧、气相分压、消耗氧、剩余可降解量、硝化、CO2吸收、温度）', 'evaluator', 'query')
cap('data.trace', obj(dict(head=HEAD, max_points=integer(10, MAX_TRACE)), optional=('max_points',)),
    obj(dict(head=TEXT, time_s=array(number('s'), 0, MAX_TRACE), pressure_pa=array(number('Pa'), 0, MAX_TRACE), delta_p_pa=array(nullable(number('Pa')), 0, MAX_TRACE),
             bod_true_kg_m3=array(nullable(number('kg/m3')), 0, MAX_TRACE))),
    dict(head='h1'), ['head_1', 'head_2', 'head_3', 'head_4', 'head_5', 'head_6'], '评测者读取测头的逐分钟压力–时间曲线与真实BOD（测头本身只存每日值）', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=dict(type='object'))), {}, ['inductive_platform'], '账目：氧消耗、CO2生成与吸收、样品用量与废液', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL, target=enum(POSITIONS + HEADS)), optional=('target',)), obj(dict(faults=array(TEXT, 0, 32))),
    dict(fault='head_untight', active=True, target='P2'), ['inductive_platform'], '故障注入：测头未拧紧（漏气）、NaOH颗粒掉入样品、恒温箱失控、电池快速耗尽', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 864000.0))), obj(dict(time_s=number('s'))), dict(dt_s=3600.0), ['inductive_platform'], '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'wireless.connect': 'OxiTop-IDS的无线连接（WLM、Multi 3630/3620 IDS测量仪）只用于IDS测头；本配置为OxiTop-i（只用测头按键）。',
    'data.export': 'OxiTop-i没有数据接口；导出到U盘/PC要用IDS测头加测量仪或OxiTop Control系统。',
    'measurement.long_term': '超过7天的测量（最长180天）只用于IDS测头加测量仪；OxiTop-i最长7天。',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, ['head_1'], v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


_PN = lambda u: nullable(number(u))                                             # noqa: E731
slot('clock.time_s', number('s'), 's', 'derived', 'inductive_platform', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'inductive_platform', '实验室条件是否已声明')
slot('incubator.temperature_k', _PN('K'), 'K', 'sensor', 'inductive_platform', '恒温箱显示温度（宿主环境；设定20 °C）')
slot('incubator.door_open', BOOL, '1', 'setpoint', 'inductive_platform', '恒温箱门是否开着')
slot('platform.powered', BOOL, '1', 'setpoint', 'inductive_platform', 'IS 6感应搅拌台是否通电')
slot('platform.stir_velocity_rad_s', number('rad/s'), 'rad/s', 'derived', 'inductive_platform', '磁力搅拌子的角速度（IS 6软件控制转速，通电时按300 rpm ≈ 31.4 rad/s，断电为0）')
slot('setup.positions', dict(type='object'), '1', 'derived', 'inductive_platform', '六个工位的装样记录（溢流瓶体积、样品、搅拌子、橡胶套与NaOH颗粒、试剂、测头）')
slot('samples.available', array(TEXT, 0, MAX_SAMPLES), '1', 'derived', 'inductive_platform', '可用的样品与试剂')
slot('heads.status', dict(type='object'), '1', 'derived', 'head_1', '六个测头的状态（关/空闲/AutoTemp适应/测试/测量/结束）、设置、ID、所在工位、电池')
slot('heads.stored_values', dict(type='object'), '1', 'sensor', 'head_1', '各测头已存储的每日值（单位按当前设置；超过50 digit标over_range仍给数值，压力超出量程时为空）')
slot('warnings.active', array(TEXT, 0, 16), '1', 'derived', 'inductive_platform', '当前提示（电池电量低、压力过高、超量程、装样温度不合适等）')
for _i, _hd in enumerate(HEADS, start=1):
    slot(f'display.{_hd}_value', _PN('1'), '1', 'sensor', f'head_{_i}_display', f'测头{_i}显示屏上的数值（按所选单位；超过50 digit仍显示数值，显示关闭、AutoTemp期间或压力超出500–1250 hPa（OFL）时为空）')
    slot(f'head.{_hd}_lift_m', number('m'), 'm', 'derived', f'head_{_i}', f'测头{_i}相对样品瓶的抬起距离（0拧在瓶上，0.09 m取下放在瓶上方）')
    for _c in ('blue', 'red', 'green'):
        slot(f'led.{_hd}_{_c}', BOOL, '1', 'derived', f'head_{_i}_led_{_c}', f'测头{_i}状态灯的{dict(blue="蓝", red="红", green="绿")[_c]}色发光（约每5 s闪一次）')
slot('truth.incubator_air_k', number('K'), 'K', 'process_truth', 'inductive_platform', '恒温箱空气温度（真值）', 'evaluator')
slot('truth.bottles', dict(type='object'), '1', 'process_truth', 'bottle_1', '各瓶真值：液体体积与温度、溶解氧、气相分压与总压、累计耗氧、真实BOD、剩余可降解量、氨氮与硝化、CO2与NaOH', 'evaluator')
slot('truth.heads', dict(type='object'), '1', 'process_truth', 'head_1', '各测头真值：真实压力、传感器增益与零点误差、零点压力、电池剩余', 'evaluator')
slot('truth.faults', array(TEXT, 0, 32), '1', 'process_truth', 'inductive_platform', '已注入的故障', 'evaluator')
SCENE_MIRRORED = ({f'head.{h}_lift_m' for h in HEADS} | {f'led.{h}_{c}' for h in HEADS for c in ('blue', 'red', 'green')} | {f'display.{h}_value' for h in HEADS}
                  | {'platform.stir_velocity_rad_s'})


# ---------------------------------------------------------------- runtime
class WtwOxitopiRuntime:
    def __init__(self, instance_id='oxitop_i_is6_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.T_air = 293.15
        self.door_open = False
        self.platform_on = False
        self.faults = set()                                  # (fault, target) tuples; target '' for incubator_failure
        self.samples = {}
        self.subs = {k: copy.deepcopy(v) for k, v in substances().items()}
        self.subs.update({k: copy.deepcopy(v) for k, v in PSEUDO.items()})
        self.bottles = {p: self._empty_bottle(p) for p in POSITIONS}
        self.heads = {h: self._new_head(h, i) for i, h in enumerate(HEADS, start=1)}
        for h, p in zip(HEADS, POSITIONS):                  # as stored: each head screwed onto an empty, dry bottle (scene reference pose, lift 0)
            self.heads[h].update(position=p, lift=0.0)
            self.bottles[p].update(head=h, sealed=True)
        self.led = dict(o2_consumed_mg=_Sum(), co2_produced_mol=_Sum(), co2_absorbed_mol=_Sum(), co2_initial_mol=_Sum(), sample_used_m3=_Sum(), waste_m3=_Sum(),
                        leak_mol=_Sum())
        self.events, self.events_dropped = [], 0
        self._traces = {h: [] for h in HEADS}               # per-minute pressure traces (append-only; outside the command snapshot)
        self.warnings = {}
        self.revision = 0
        self._principals = {}
        self._requests = {}
        self._owners = {}
        self._caller = None
        self.pm_lot = None

    # ---- construction helpers
    def _empty_bottle(self, pos):
        return dict(pos=pos, V_b=BOTTLE_M3, liquid=None, stir_bar=False, sleeve=False, pellets=0, naoh_mol=0.0, head=None, sealed=False, tight=True, reagents={},
                    T=None, gas=None, nominal_ml=None, sample_id=None)

    def _new_head(self, h, i):
        return dict(id=10 + i, color=HEAD_COLOR[h], duration=5, unit='mg/l', volume_ml=432.0, illumination=True, signal_led=True, switch_off_s=120.0,
                    battery=1.0, state='off', display_on=False, display_off_at=None, screen='off', menu_item=0, stored=[], position=None, lift=HEAD_LIFT_M,
                    t_start=None, t0=None, t_end=None, p_zero=None, next_test=None, gain=0.0, offset=0.0, stored_idx=None, led_done=False)

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
        return WtwOxitopiSession(self, name)

    def _event(self, kind, code, message, ref=None):
        if len(self.events) >= MAX_EVENTS_LOG:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message, _ref=ref))

    def _own(self, object_id):
        if self._caller is not None:
            self._owners[object_id] = self._caller

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    # ---- environment
    def _fault(self, name, target=''):
        return (name, target) in self.faults

    def _incubator_target(self):
        if self._fault('incubator_failure'):
            return self.plant['room_k']
        return self.plant['incubator_k'] + 0.3 * math.sin(2 * math.pi * self.t / 1800.0)   # thermostat cycling within +-0.5 K (BOD primer: 20.0 +- 0.5 C)

    # ---- physics
    def step(self, dt_s):
        """Advance the host clock; physics on the fixed 60 s grid (state between grid points is held, any split of an interval gives the same result)."""
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = t_end
            return self.t
        while (self.tick + 1) * DT <= t_end + 1e-9:
            self.tick += 1
            self.t = self.tick * DT
            self._tick(DT)
        self.t = t_end
        self._heads_ui()
        return self.t

    def _tick(self, dt):
        # cabinet air: closed door -> thermostat target (600 s), open door -> room (120 s)
        target = self.plant['room_k'] if self.door_open else self._incubator_target()
        tau = 120.0 if self.door_open else 600.0
        self.T_air = target + (self.T_air - target) * math.exp(-dt / tau)
        for b in self.bottles.values():
            if b['liquid'] is not None or b['gas'] is not None:
                self._bottle_tick(b, dt)
        for h, hd in self.heads.items():
            self._head_tick(h, hd, dt)

    def _kla(self, b):
        L = b['liquid']
        if L is None or L['V'] <= 0:
            return 0.0
        stirred = self.platform_on and b['stir_bar']
        kl = ENG['kl_stirred_m_s'] if stirred else ENG['kl_unstirred_m_s']
        return kl * ENG['liquid_surface_m2'] / L['V']

    def _gas_volume(self, b):
        return b['V_b'] - (b['liquid']['V'] if b['liquid'] else 0.0) - (ENG['stir_bar_volume_m3'] if b['stir_bar'] else 0.0) + (ENG['head_internal_volume_m3'] if b['head'] else 0.0)

    def _bottle_tick(self, b, dt):
        L, G = b['liquid'], b['gas']
        # temperature of liquid and gas (one node): natural convection to the cabinet air
        m_w = (L['V'] * 998.0) if L else 0.0
        C = m_w * 4182.0 + ENG['glass_heat_capacity_j_k']
        tau_th = C / (ENG['heat_transfer_w_m2_k'] * ENG['bottle_outer_area_m2'])
        b['T'] = self.T_air + (b['T'] - self.T_air) * math.exp(-dt / tau_th)
        T = b['T']
        if G is not None and not b['sealed']:            # open bottle: the head space follows the room air
            self._air_fill(b)
        if L is not None:
            # ---- biology and chemistry (mg/l of O2), dissolved-oxygen limitation; substeps when the demand is large against the dissolved oxygen
            n_sub = 1
            demand = self._bio_rates(b, T)['total'] * dt
            if demand > 0.4 * max(L['DO'], 0.05):
                n_sub = min(40, int(math.ceil(demand / (0.4 * max(L['DO'], 0.05)))))
            h = dt / n_sub
            for _ in range(n_sub):
                self._bio_apply(b, self._bio_rates(b, T), h)
                self._exchange(b, h, T)
        # ---- leak: untight head (fault or not screwed tight) or missing rubber sleeve -> the head space relaxes to the room air
        rate = self._leak_rate(b)
        if b['sealed'] and rate > 0.0:
            Vg = self._gas_volume(b)
            dry_amb = (self.plant['ambient_pressure_pa'] - p_vapour(T)) * Vg / (R_GAS * T)
            f = 1.0 - math.exp(-rate * dt)
            for key, frac in (('o2', AIR_O2), ('inert', 1 - AIR_O2 - AIR_CO2), ('co2', AIR_CO2)):
                d = (dry_amb * frac - G[key]) * f
                G[key] += d
                self.led['leak_mol'].add(d)

    def _leak_rate(self, b):
        if not b['sealed']:
            return 0.0
        if not b['sleeve']:
            return LEAK_NO_SLEEVE_PER_S
        if not b['tight'] or self._fault('head_untight', b['pos']):
            return ENG['leak_rate_per_s']
        return 0.0

    def _air_fill(self, b):
        T = b['T']
        Vg = self._gas_volume(b)
        dry = (self.plant['ambient_pressure_pa'] - p_vapour(T)) * Vg / (R_GAS * T)
        b['gas'] = dict(o2=dry * AIR_O2, inert=dry * (1 - AIR_O2 - AIR_CO2), co2=dry * AIR_CO2)

    def _bio_rates(self, b, T):
        """Oxygen uptake rates (mg/l/s) of the liquid in bottle b at temperature T."""
        L = b['liquid']
        DO = max(L['DO'], 0.0)
        f_do = DO / (ENG['do_half_saturation_mg_l'] + DO)
        tc = ENG['theta_carbon'] ** (T - 293.15)
        tn = ENG['theta_nitrification'] ** (T - 293.15)
        f_ph = _ph_factor(L['ph'])
        f_tox = math.exp(-L['chlorine'] / ENG['chlorine_kill_mg_l'])
        for inh in L['inhibitors']:
            f_tox *= 1.0 / (1.0 + (inh['c'] / inh['ic50']) ** inh['hill'])
        live = L['seed_type'] != 'none' and L['active']
        out = dict(pools={}, endo=0.0, nit=0.0, sulfite=0.0, f_do=f_do)
        stir = STIRRED_RATE_FACTOR if (self.platform_on and b['stir_bar']) else 1.0     # suspended biomass under inductive stirring (calibrated)
        if live:
            for sid, p in L['pools'].items():
                if p['L'] <= 0.0:
                    continue
                k = p['k20'] * tc * p['a'] * stir
                out['pools'][sid] = k * p['L'] * f_do * f_ph * f_tox
            out['endo'] = ENG['seed_endogenous_mg_l_day_per_pct'] / 86400.0 * (100.0 * L['seed_frac']) * tc * f_do * f_ph * f_tox if L['seed_frac'] > 0 else 0.0
            if L['nh4'] > 0.0 and L['xn'] > 0.0:
                f_atu = 1.0 / (1.0 + (L['atu'] / ENG['atu_ki_mg_l']) ** 2)
                qn = 10.0 / 86400.0                        # mg N/l/s nitrification capacity at full nitrifier activity (assumption)
                out['nit'] = 4.57 * qn * L['xn'] * tn * L['nh4'] / (ENG['nitrifier_ks_mg_l'] + L['nh4']) * f_atu * f_do * f_ph * f_tox
        if L['sulfite'] > 0.0:                             # 2 SO3(2-) + O2 -> 2 SO4(2-); sulfite in mg O2-equivalent per litre
            out['sulfite'] = L['sulfite'] / L['sulfite_tau'] * DO / (0.05 + DO)
        out['total'] = sum(out['pools'].values()) + out['endo'] + out['nit'] + out['sulfite']
        return out

    def _bio_apply(self, b, r, h):
        L = b['liquid']
        want = dict(pools={sid: min(L['pools'][sid]['L'], rate * h) for sid, rate in r['pools'].items()}, endo=r['endo'] * h,
                    nit=min(L['nh4'] * 4.57, r['nit'] * h), sulfite=min(L['sulfite'], r['sulfite'] * h))
        total = sum(want['pools'].values()) + want['endo'] + want['nit'] + want['sulfite']
        f = 1.0 if total <= max(L['DO'], 0.0) else max(L['DO'], 0.0) / total     # never consume more oxygen than is dissolved
        cons = co2 = 0.0
        for sid, d in want['pools'].items():
            p = L['pools'][sid]
            d *= f
            p['L'] -= d
            dn = d * p['n_per_o2']                          # organic nitrogen released as ammonium (ThOD with NH3 as the nitrogen product)
            L['nh4'] += dn
            L['alk'] += dn / 14.007                         # NH3 + H+ -> NH4+ (mol/m3)
            cons += d
            co2 += d * ENG['respiratory_quotient']
        d = want['endo'] * f
        cons += d
        co2 += d * ENG['respiratory_quotient']
        d = want['nit'] * f
        L['nh4'] -= d / 4.57
        L['no3'] += d / 4.57
        L['alk'] -= 2.0 * d / 4.57 / 14.007                 # nitrification releases 2 H+ per N (7.14 mg CaCO3 per mg N)
        cons += d
        d = want['sulfite'] * f
        L['sulfite'] -= d
        cons += d
        L['DO'] -= cons
        L['o2_used'] += cons                               # mg/l
        self.led['o2_consumed_mg'].add(cons * L['V'] * 1000.0)
        dic = co2 / M_O2                                    # mol/m3 of CO2 (mg/l O2 = g/m3, respiratory quotient applied above)
        L['dic'] += dic
        L['ph'] = ph_of(L['alk'], L['dic'], L['pt'], L['ph'])
        self.led['co2_produced_mol'].add(dic * L['V'])
        # adaptation (lag) and nitrifier growth
        for p in L['pools'].values():
            if p['a'] < 1.0:
                tau = p['lag']
                p['a'] = 1.0 if tau <= 0 else 1.0 - (1.0 - p['a']) * math.exp(-h / tau)
        if L['xn'] > 0.0 and L['nh4'] > 0.0:
            f_atu = 1.0 / (1.0 + (L['atu'] / ENG['atu_ki_mg_l']) ** 2)
            mu = ENG['nitrifier_mu_per_day'] / 86400.0 * ENG['theta_nitrification'] ** (b['T'] - 293.15) * f_atu * r['f_do']
            L['xn'] = min(1.0, L['xn'] * math.exp(mu * h))

    def _exchange(self, b, h, T):
        """Gas <-> liquid O2 (exact two-compartment relaxation), CO2(aq) <-> gas and NaOH absorption of gas CO2."""
        L, G = b['liquid'], b['gas']
        if G is None:
            return
        Vg = self._gas_volume(b)
        Vl_l = L['V'] * 1000.0
        kla = self._kla(b)
        if b['sealed']:
            # O2: gas amount n (mol) with p = n R T / Vg; equilibrium DO = KH p; total oxygen M_tot conserved
            KH = henry_o2(T)                                # mg/l per Pa
            beta = KH * R_GAS * T / Vg                      # mg/l per mol of gas O2
            n0, c0 = G['o2'], L['DO']
            tot = n0 * M_O2 * 1000.0 + c0 * Vl_l            # mg
            n_eq = tot / (M_O2 * 1000.0 + beta * Vl_l)
            c_eq = beta * n_eq
            lam = kla * (1.0 + beta * Vl_l / (M_O2 * 1000.0))
            e = math.exp(-lam * h)
            L['DO'] = c_eq + (c0 - c_eq) * e
            G['o2'] = max(0.0, (tot - L['DO'] * Vl_l) / (M_O2 * 1000.0))
            # CO2: aqueous CO2 fraction of DIC by pKa1; Henry exchange with the gas
            kh_c = ENG['co2_henry_mol_m3_pa']               # mol/m3 per Pa
            k_c = kla * ENG['co2_kla_ratio']
            p_co2 = G['co2'] * R_GAS * T / Vg
            flux = k_c * (co2_aq(L) - kh_c * p_co2) * L['V'] * h      # mol from liquid to gas (explicit; k_c h < 0.3 on the grid)
            flux = max(-G['co2'], min(flux, L['dic'] * L['V']))
            L['dic'] -= flux / L['V']
            G['co2'] += flux
            L['ph'] = ph_of(L['alk'], L['dic'], L['pt'], L['ph'])
            if b['sleeve'] and b['naoh_mol'] > 0.0:
                take = min(G['co2'] * (1.0 - math.exp(-h / ENG['naoh_absorption_s'])), b['naoh_mol'] / 2.0)
                G['co2'] -= take
                b['naoh_mol'] -= 2.0 * take
                self.led['co2_absorbed_mol'].add(take)
        else:
            L['DO'] = min(L['DO'] + kla * (henry_o2(T) * AIR_O2 * (self.plant['ambient_pressure_pa'] - p_vapour(T)) - L['DO']) * h,
                          henry_o2(T) * AIR_O2 * self.plant['ambient_pressure_pa'])

    def pressure(self, b):
        """Total gas pressure in the bottle (Pa)."""
        G = b['gas']
        if G is None:
            return self.plant['ambient_pressure_pa'] if self.plant else P_STD
        Vg = self._gas_volume(b)
        T = b['T']
        return (G['o2'] + G['inert'] + G['co2']) * R_GAS * T / Vg + p_vapour(T)

    # ---- measuring head
    def _head_pressure(self, h, hd, noise_k=0):
        """Sensor reading (Pa): true pressure of the bottle the head sits on (room air when not on a bottle) with gain, offset and noise; 1 hPa resolution."""
        if hd['position'] is not None:
            p = self.pressure(self.bottles[hd['position']])
        else:
            p = self.plant['ambient_pressure_pa']
        meas = p * (1.0 + hd['gain']) + hd['offset'] + ENG['sensor_noise_pa'] * _gauss(self.seed ^ _h(h), self.tick, 7 + noise_k)
        return meas

    def _autotemp_interval(self, hd):
        return DOC['autotemp']['test_interval_bod5_s'] * hd['duration'] / 5.0

    def _head_tick(self, h, hd, dt):
        # battery
        if hd['battery'] > 0.0:
            B = DOC['battery']
            life = B['life_off_days'] if hd['state'] in ('off', 'idle', 'finished') else (B['life_measuring_led_on_days'] if hd['signal_led'] else B['life_measuring_led_off_days'])
            drain = dt / 86400.0 / life * (BATTERY_FAULT_FACTOR if self._fault('battery_drain', h) else 1.0)
            hd['battery'] = max(0.0, hd['battery'] - drain)
            if hd['battery'] <= 0.0 and hd['state'] != 'dead':  # an empty battery stops the head in every state until head.replace_battery
                running = hd['state'] in ('adaption', 'test', 'measuring')
                hd['state'] = 'dead'
                hd['display_on'] = False
                hd['screen'] = 'off'
                self._event('head', 'BATTERY_EMPTY', f'测头{h}电池耗尽' + ('，测量停止（已存值保留）' if running else '，不能开机'), ref=h)
        st = hd['state']
        if st in ('adaption', 'test', 'measuring') and hd['t_start'] is not None:
            pm = self._head_pressure(h, hd)
            if st == 'adaption' and self.t >= hd['t_start'] + DOC['autotemp']['adaption_s'] - 1e-9:
                hd['p_zero'] = pm                       # zero at the end of the adaption phase (in any case)
                hd['state'] = 'test'
                hd['next_test'] = self.t + self._autotemp_interval(hd)
                self._event('head', 'AUTOTEMP_ZERO', f'测头{h}适应期结束，零点{pm / 100:.0f} hPa', ref=h)
            elif st == 'test' and self.t >= hd['next_test'] - 1e-9:
                latest = hd['t_start'] + min(DOC['autotemp']['start_latest_s'], max(DOC['autotemp']['adaption_s'] + self._autotemp_interval(hd),
                                                                                   DOC['autotemp']['max_initial_fraction'] * hd['duration'] * 86400.0))
                if pm > hd['p_zero'] + 50.0 and self.t < latest - 1e-9:      # pressure rose (> 0.5 hPa): sample still warming -> zero again
                    hd['p_zero'] = pm
                    hd['next_test'] = self.t + self._autotemp_interval(hd)
                else:
                    hd['state'] = 'measuring'
                    hd['t0'] = self.t
                    self._event('head', 'MEASUREMENT_START', f'测头{h}开始测量（AutoTemp用时{(self.t - hd["t_start"]) / 60:.0f} min）', ref=h)
            elif st == 'measuring':
                el = self.t - hd['t0']
                n = len(hd['stored'])
                if n < hd['duration'] and el >= (n + 1) * DOC['store_interval_s'] - 1e-9:
                    dp = hd['p_zero'] - pm
                    hd['stored'].append(dict(day=n + 1, dp=dp, ofl=self._ofl(pm), over=self._over_range(dp)))
                    if hd['stored'][-1]['over'] and 'RANGE_EXCEEDED_' + h not in self.warnings:
                        self.warnings['RANGE_EXCEEDED_' + h] = f'{h}超过所选样品体积的测量范围（>50 digit）：稀释样品或换小体积重新测量'
                    if n + 1 >= hd['duration']:
                        hd['state'] = 'finished'
                        hd['t_end'] = self.t
                        self._event('head', 'MEASUREMENT_END', f'测头{h}测量结束（{hd["duration"]}天）', ref=h)
            tr = self._traces[h]
            if len(tr) < MAX_TRACE:
                L = self.bottles[hd['position']]['liquid'] if hd['position'] is not None else None
                tr.append((self.t, pm, (hd['p_zero'] - pm) if hd['p_zero'] is not None else None, (L['o2_used'] / 1000.0) if L else None))
        if hd['state'] == 'finished' and hd['t_end'] is not None and self.t - hd['t_end'] > DOC['led_green_after_end_s']:
            hd['led_done'] = True

    def _ofl(self, pm):
        lo, hi = DOC['pressure_range_pa']
        return pm > hi or pm < lo

    def _over_range(self, dp):
        return dp / 100.0 / DIGIT_HPA > DOC['digit_range'][1] + 0.5

    def _heads_ui(self):
        for hd in self.heads.values():
            if hd['display_on'] and hd['display_off_at'] is not None and self.t >= hd['display_off_at'] - 1e-9:
                hd['display_on'] = False
                hd['screen'] = 'off'
                if hd['state'] == 'idle':
                    hd['state'] = 'off'

    def _value_in_unit(self, hd, dp_pa, unit=None):
        unit = unit or hd['unit']
        if dp_pa is None:
            return None
        dp_hpa = dp_pa / 100.0
        if unit == 'mg/l':
            return round(bod_factor(hd['volume_ml']) * dp_hpa, 1)
        if unit == 'dhPa':
            return float(round(dp_hpa))
        if unit == 'hPa':
            return float(round((hd['p_zero'] - dp_pa) / 100.0)) if hd['p_zero'] is not None else None
        return float(round(dp_hpa / DIGIT_HPA))

    def _stored_value(self, hd, s):
        return None if s['ofl'] else self._value_in_unit(hd, s['dp'])

    def _stored_list(self, hd):
        return [dict(day=s['day'], value=self._stored_value(hd, s), unit=hd['unit'], over_range=bool(s['over']), delta_p_pa=round(s['dp'], 1)) for s in hd['stored']]

    def _display(self, h, hd):
        """What the display shows (lines, numeric value, label)."""
        if not hd['display_on']:
            return dict(lines=[], value=None, label='', screen='off')
        sc = hd['screen']
        unit_txt = dict(zip(UNITS, ['mg/l', 'ΔhPa', 'hPa', 'digit']))[hd['unit']]
        if sc == 'id':
            return dict(lines=[f'ID: {hd["id"]}'], value=None, label='ID', screen=sc)
        if sc == 'info':
            return dict(lines=[f'ID: {hd["id"]}', f'{hd["duration"]} d', f'{hd["volume_ml"]:g} ml', unit_txt], value=None, label='BOD parameters', screen=sc)
        if sc == 'menu_meas':
            items = [f'Duration... {hd["duration"]}', f'Unit... {unit_txt}', f'Sample volume... {hd["volume_ml"]:g}']
            return dict(lines=['> ' + x if i == hd['menu_item'] else '  ' + x for i, x in enumerate(items)], value=None, label='Measurement settings', screen=sc)
        if sc == 'menu_sys':
            items = [f'Illumination {"on" if hd["illumination"] else "off"}', f'Signal LED {"on" if hd["signal_led"] else "off"}',
                     f'Switch-off time... {hd["switch_off_s"] / 60:.0f} min', f'ID... {hd["id"]}']
            return dict(lines=['> ' + x if i == hd['menu_item'] else '  ' + x for i, x in enumerate(items)], value=None, label='System settings', screen=sc)
        if sc == 'confirm_off':
            return dict(lines=['Stop measurement?', '<M> yes  <ENTER> no'], value=None, label='confirm', screen=sc)
        if sc == 'stored' and hd['stored_idx'] is not None and hd['stored']:
            s = hd['stored'][hd['stored_idx']]
            v = self._stored_value(hd, s)
            return dict(lines=[f'ID: {hd["id"]}', f'BOD{s["day"]} [{unit_txt}]:', 'OFL' if v is None else f'{v:g}'], value=v, label=f'BOD{s["day"]}', screen=sc)
        if hd['state'] in ('adaption', 'test'):
            return dict(lines=[f'ID: {hd["id"]}', 'AutoTemp', ''], value=None, label='AutoTemp', screen='value')
        if hd['state'] == 'finished' and hd['stored']:     # measurement over: the last stored value
            s = hd['stored'][-1]
            v = self._stored_value(hd, s)
            return dict(lines=[f'ID: {hd["id"]}', f'BOD{s["day"]} [{unit_txt}]:', 'OFL' if v is None else f'{v:g}'], value=v, label=f'BOD{s["day"]}', screen='value')
        if hd['state'] != 'measuring' or hd['p_zero'] is None:
            return dict(lines=[f'ID: {hd["id"]}'], value=None, label='ID', screen='id')
        pm = self._head_pressure(h, hd, noise_k=3)
        dp = hd['p_zero'] - pm
        days = (self.t - hd['t0']) / 86400.0
        v = None if self._ofl(pm) else self._value_in_unit(hd, dp)
        label = f'BOD{min(days, hd["duration"]):.1f}'
        return dict(lines=[f'ID: {hd["id"]}', f'{label} [{unit_txt}]:', 'OFL' if v is None else f'{v:g}'], value=v, label=label, screen='value')

    def _led(self, h, hd):
        if not hd['signal_led'] or hd['state'] in ('off', 'dead', 'idle'):
            return 'off', False
        low = hd['battery'] < self._low_battery_fraction(hd)
        color = None
        if hd['state'] in ('adaption', 'test', 'measuring'):
            color = 'red' if (low or self._ofl(self._head_pressure(h, hd))) else 'blue'        # critical: battery nearly empty or pressure too high
        elif hd['state'] == 'finished' and not hd['led_done']:
            color = 'green'
        if color is None:
            return 'off', False
        phase0 = hd['t_start'] or 0.0
        lit = ((self.t - phase0) % DOC['led_flash_period_s']) < 0.5     # flash about every 5 s (0.5 s on; assumption)
        return color, lit

    def _stir_velocity(self):
        return ENG['nominal_rpm'] * 2.0 * math.pi / 60.0 if self.platform_on else 0.0

    def _fault_list(self):
        return sorted(f'{f}:{t}' if t else f for f, t in self.faults)

    def _low_battery_fraction(self, hd):
        B = DOC['battery']
        return B['continue_after_low_battery_days'] / B['life_measuring_led_on_days']

    # ---- read
    def read(self, visibility='agent'):
        heads_status, stored = {}, {}
        out = {'clock.time_s': self.t, 'plant.configured': self.plant is not None,
               'incubator.temperature_k': round(self.T_air, 1) if self.plant else None, 'incubator.door_open': self.door_open,
               'platform.powered': self.platform_on, 'platform.stir_velocity_rad_s': self._stir_velocity()}
        setup = {}
        for p, b in self.bottles.items():
            setup[p] = dict(filled_ml=b['nominal_ml'], sample_id=b['sample_id'], stir_bar=b['stir_bar'], sleeve=b['sleeve'], naoh_pellets=b['pellets'],
                            reagents=dict(b['reagents']), head=b['head'])
        out['setup.positions'] = setup
        out['samples.available'] = sorted(self.samples)
        for i, (h, hd) in enumerate(self.heads.items(), start=1):
            d = self._display(h, hd)
            color, lit = self._led(h, hd)
            heads_status[h] = dict(id=hd['id'], color=hd['color'], state=hd['state'], screen=d['screen'], position=hd['position'], duration_days=hd['duration'],
                                   unit=hd['unit'], sample_volume_m3=hd['volume_ml'] * 1e-6, battery_low=hd['battery'] < self._low_battery_fraction(hd),
                                   elapsed_s=(self.t - hd['t0']) if hd['t0'] is not None and hd['state'] in ('measuring', 'finished') else None,
                                   stored_count=len(hd['stored']), led=color)
            stored[h] = self._stored_list(hd)
            out[f'display.{h}_value'] = d['value']
            out[f'head.{h}_lift_m'] = hd['lift']
            for c in ('blue', 'red', 'green'):
                out[f'led.{h}_{c}'] = bool(lit and color == c)
        out['heads.status'] = heads_status
        out['heads.stored_values'] = stored
        out['warnings.active'] = sorted(self.warnings)
        if visibility != 'agent':
            out['truth.incubator_air_k'] = self.T_air
            tb = {}
            for p, b in self.bottles.items():
                L, G = b['liquid'], b['gas']
                if L is None and G is None:
                    tb[p] = None
                    continue
                Vg = self._gas_volume(b)
                T = b['T']
                tb[p] = dict(liquid_m3=L['V'] if L else 0.0, temperature_k=T, do_kg_m3=(L['DO'] / 1000.0) if L else None, pressure_pa=self.pressure(b),
                             p_o2_pa=G['o2'] * R_GAS * T / Vg if G else None, p_co2_pa=G['co2'] * R_GAS * T / Vg if G else None,
                             o2_consumed_kg_m3=(L['o2_used'] / 1000.0) if L else None, remaining_bod_kg_m3=(sum(q['L'] for q in L['pools'].values()) / 1000.0) if L else None,
                             nh4_n_kg_m3=(L['nh4'] / 1000.0) if L else None, nitrified_n_kg_m3=(L['no3'] / 1000.0) if L else None, dic_mol_m3=L['dic'] if L else None,
                             naoh_mol=b['naoh_mol'], sealed=b['sealed'], gas_volume_m3=Vg, bottle_volume_m3=b['V_b'])
            out['truth.bottles'] = tb
            out['truth.heads'] = {h: dict(p_true_pa=(self.pressure(self.bottles[hd['position']]) if hd['position'] else (self.plant['ambient_pressure_pa'] if self.plant else None)),
                                          gain=hd['gain'], offset_pa=hd['offset'], p_zero_pa=hd['p_zero'], battery=hd['battery']) for h, hd in self.heads.items()}
            out['truth.faults'] = self._fault_list()
        return out

    # ---------------------------------------------------------------- commands
    def _cmd_plant_configure(self, incubator_k, room_k, ambient_pressure_pa, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(incubator_k=float(incubator_k), room_k=float(room_k), ambient_pressure_pa=float(ambient_pressure_pa))
        self.seed = int(noise_seed)
        if first:
            self.T_air = float(incubator_k)
            for i, (h, hd) in enumerate(self.heads.items()):
                s = self.seed ^ _h(h)
                hd['gain'] = ENG['sensor_gain_sigma'] * max(-2.0, min(2.0, _gauss(s, 1, 1)))
                hd['offset'] = ENG['sensor_offset_sigma_pa'] * max(-2.0, min(2.0, _gauss(s, 1, 2)))
            for p, b in self.bottles.items():
                b['V_b'] = BOTTLE_M3 + ENG['bottle_volume_sigma_m3'] * max(-2.5, min(2.5, _gauss(self.seed ^ _h(p), 2, 1)))
            self._event('incubator', 'PLANT', f'实验室：恒温箱{incubator_k - 273.15:.1f} °C、室温{room_k - 273.15:.1f} °C、气压{ambient_pressure_pa / 100:.0f} hPa')
        return dict(configured=True)

    def _composition(self, comp, volume):
        subs, nh4, atu, sulfite = {}, float(comp.get('nh4_n_kg_m3', 0.0)) * 1000.0, 0.0, 0.0
        for s in comp.get('substances', []):
            sid, c = s['substance_id'], float(s['concentration_kg_m3']) * 1000.0         # mg/l
            if sid not in self.subs:
                raise ActionRejected('UNKNOWN_SUBSTANCE')
            if sid == 'sodium_sulfite':                     # chemical oxygen demand: 2 SO3(2-) + O2 -> 2 SO4(2-)
                sulfite += c * self.subs[sid]['thod_nh4_mg_mg']
            elif sid == 'ammonium_chloride':                # nitrogen source for nitrification
                nh4 += c * N_PER_NH4CL
            elif sid == 'allylthiourea':                    # nitrification inhibitor (ATU)
                atu += c
            else:
                subs[sid] = subs.get(sid, 0.0) + c
        seed = comp.get('seed', dict(type='none', volume_fraction=0.0))
        ph = float(comp['ph'])
        alk_c = float(comp.get('alkalinity_mol_m3', ALK_DEFAULT if ph >= 6.0 else 0.0))
        pt = float(comp.get('phosphate_mol_m3', 0.0))
        dic = dic_of(ph, alk_c)
        return dict(V=float(volume), subs=subs, nh4=nh4, ph=ph, dic=dic, pt=pt, alk=alk_of(ph, dic, pt), seed_type=seed['type'],
                    seed_frac=float(seed['volume_fraction']), xn=float(comp.get('nitrifier_activity', 0.0)), atu=atu,
                    inhibitors=[dict(name=x['name'], c=x['concentration_kg_m3'] * 1000.0, ic50=x['ic50_kg_m3'] * 1000.0, hill=x.get('hill', 1.0))
                                for x in comp.get('inhibitors', [])],
                    chlorine=float(comp.get('free_chlorine_kg_m3', 0.0)) * 1000.0, T=float(comp.get('temperature_k', 293.15)),
                    DO=None if comp.get('dissolved_oxygen_kg_m3') is None else float(comp['dissolved_oxygen_kg_m3']) * 1000.0, sulfite=sulfite, naoh=False)

    def _water(self, volume):
        """Distilled water: no buffer, no nutrients, no carbonate (air-saturated oxygen)."""
        return dict(V=float(volume), subs={}, nh4=0.0, ph=7.0, dic=0.0, pt=0.0, alk=alk_of(7.0, 0.0, 0.0), seed_type='none', seed_frac=0.0, xn=0.0, atu=0.0,
                    inhibitors=[], chlorine=0.0, T=293.15, DO=None, sulfite=0.0, naoh=False)

    def _add_sample(self, sid, comp, owner_ref):
        if sid in self.samples:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_ITEMS')
        self.samples[sid] = comp
        self._own('sample:' + sid)

    def _cmd_stock_provide(self, stock_id, volume_m3, composition):
        self._need_plant()
        self._add_sample(stock_id, self._composition(composition, volume_m3), stock_id)
        self._event('sample', 'STOCK', f'水样{stock_id}（{volume_m3 * 1e6:.0f} ml）')
        return dict(stock_id=stock_id, volume_m3=float(volume_m3))

    def _cmd_kit_provide(self, kit, volume_m3=1e-3, lot_test_value_kg_m3=None):
        self._need_plant()
        S = sample_lib()
        items = []
        if kit == 'oxitop_pm' and lot_test_value_kg_m3 is None:
            raise ActionRejected('INVALID_ARGUMENT')

        def add(sid, comp):
            if sid not in self.samples:
                self.samples[sid] = comp
                items.append(sid)
            elif not comp.get('reagent'):
                self.samples[sid]['V'] += comp['V']             # more of the same solution
                items.append(sid)
        if kit in ('gga_standard', 'gga_stock_r10'):
            g = S['samples'][kit]
            add(kit, self._composition(dict(substances=[dict(substance_id=k, concentration_kg_m3=v / 1000.0) for k, v in g['solutes'].items()], ph=g['ph'],
                                            alkalinity_mol_m3=g['alkalinity_mol_m3'], phosphate_mol_m3=g['phosphate_mol_m3']), volume_m3))
        elif kit == 'dilution_water':                       # R14 nutrient solution in aerated water (air-saturated, about 9 mg/l at 20 C)
            g = S['samples'][kit]
            add(kit, self._composition(dict(substances=[], ph=g['ph'], nh4_n_kg_m3=g['nh4_n_mg_l'] / 1000.0, alkalinity_mol_m3=g['alkalinity_mol_m3'],
                                            phosphate_mol_m3=g['phosphate_mol_m3']), volume_m3))
        elif kit == 'distilled_water':                      # no buffer, no nutrients, no carbonate (air-saturated)
            add(kit, self._water(volume_m3))
        elif kit == 'sodium_sulfite_r11':
            g = S['reagents'][kit]
            add(kit, self._composition(dict(substances=[dict(substance_id='sodium_sulfite', concentration_kg_m3=g['solutes']['sodium_sulfite'] / 1000.0)], ph=8.0,
                                            alkalinity_mol_m3=0.0), volume_m3))
        elif kit in ('settled_sewage_seed', 'domestic_influent', 'domestic_effluent', 'river_water', 'food_industry'):
            g = S['samples'][kit]
            t = g['components']
            add(kit, self._composition(dict(substances=[dict(substance_id='ww_readily', concentration_kg_m3=t['readily_mg_l'] / 1000.0),
                                                        dict(substance_id='ww_slowly', concentration_kg_m3=t['slowly_mg_l'] / 1000.0),
                                                        dict(substance_id='ww_inert', concentration_kg_m3=t['inert_cod_mg_l'] / 1000.0)],
                                            nh4_n_kg_m3=t['nh4_n_mg_l'] / 1000.0, ph=g['ph'], alkalinity_mol_m3=g['alkalinity_mol_m3'],
                                            seed=dict(type='sewage', volume_fraction=1.0), nitrifier_activity=g['nitrifier_activity']), volume_m3))
        elif kit in ('nth_600', 'nhp_600'):
            add(kit, dict(reagent=True, V=25e-6 if kit == 'nth_600' else 0.0))
        else:                                               # oxitop_pm: lot test value printed on the package (mg/l in 164 ml)
            self.pm_lot = float(lot_test_value_kg_m3) * 1000.0
            add(kit, dict(reagent=True, V=0.0))
        self._event('sample', 'KIT', f'试剂/样品：{kit}')
        return dict(items=items)

    def _cmd_substance_define(self, substance_id, name, thod_kg_kg, f_ult, k20_per_s, lag_s=0.0, ic50_kg_m3=None):
        self._need_plant()
        if substance_id in self.subs:
            raise ActionRejected('ITEM_EXISTS')
        self.subs[substance_id] = dict(name=name, thod_nh4_mg_mg=float(thod_kg_kg), kinetics=dict(f_ult=float(f_ult), k20_per_day=float(k20_per_s) * 86400.0,
                                                                                                 lag_days=float(lag_s) / 86400.0),
                                       ic50_mg_l=None if ic50_kg_m3 is None else float(ic50_kg_m3) * 1000.0, evidence='evaluator')
        return dict(substance_id=substance_id)

    def _source(self, sid):
        s = self.samples.get(sid)
        if s is None or s.get('reagent'):
            raise ActionRejected('UNKNOWN_SAMPLE')
        return s

    def _cmd_sample_prepare(self, sample_id, volume_m3, sources, diluent='none', seed_volume_fraction=0.0, atu_kg_m3=0.0, ph_adjust_to=None, aerate=False,
                            dechlorinate=False, temperature_k=None):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_ITEMS')
        tot = sum(s['volume_m3'] for s in sources)
        srcs = [(self._source(s['source_id']), s['volume_m3']) for s in sources]
        need = {}
        for src, v in srcs:
            need[id(src)] = need.get(id(src), 0.0) + v
        seed_v = seed_volume_fraction * volume_m3
        if seed_v > 0 and 'settled_sewage_seed' not in self.samples:
            raise ActionRejected('NO_SEED')
        if tot + seed_v > volume_m3 * (1 + 1e-9):
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        dil_v = max(0.0, volume_m3 - tot - seed_v)
        if diluent == 'none' and dil_v > 1e-6 * volume_m3:      # nothing to make up the volume
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        dil = None
        if dil_v > 0.0 and diluent == 'dilution_water':
            if 'dilution_water' not in self.samples:
                raise ActionRejected('UNKNOWN_SAMPLE')
            dil = self.samples['dilution_water']
            need[id(dil)] = need.get(id(dil), 0.0) + dil_v
        parts = list(srcs)
        if seed_v > 0:
            seed = self.samples['settled_sewage_seed']
            need[id(seed)] = need.get(id(seed), 0.0) + seed_v
            parts.append((seed, seed_v))
        if dil is not None:
            parts.append((dil, dil_v))
        elif dil_v > 0.0:                                   # distilled water: no buffer, no nutrients, no carbonate
            parts.append((self._water(dil_v), dil_v))
        pool = {id(x): x for x, _ in parts}
        if any(pool[k]['V'] < v - 1e-12 for k, v in need.items()):
            raise ActionRejected('INSUFFICIENT_SAMPLE')
        V = volume_m3
        out = dict(V=V, subs={}, nh4=0.0, ph=7.0, dic=0.0, alk=0.0, pt=0.0, seed_type='none', seed_frac=0.0, xn=0.0, atu=0.0, inhibitors=[], chlorine=0.0,
                   T=0.0, DO=None, sulfite=0.0, naoh=False)
        do_sum = 0.0
        seed_rank = {t: i for i, t in enumerate(SEED_TYPES)}
        for src, v in parts:
            f = v / V
            for k, c in src['subs'].items():
                out['subs'][k] = out['subs'].get(k, 0.0) + c * f
            for k in ('nh4', 'xn', 'atu', 'chlorine', 'sulfite', 'T', 'dic', 'alk', 'pt', 'seed_frac'):
                out[k] += src[k] * f
            do_sum += (self._sat(src['T']) if src['DO'] is None else src['DO']) * f
            for inh in src['inhibitors']:
                out['inhibitors'].append(dict(inh, c=inh['c'] * f))
            if seed_rank[src['seed_type']] > seed_rank[out['seed_type']]:
                out['seed_type'] = src['seed_type']
        if diluent == 'distilled_water' and dil_v > 0.0:   # pure distilled water damages the cells (BOD primer p.20): osmotic stress by its volume fraction
            out['inhibitors'].append(dict(name='osmotic_stress_distilled_water', c=dil_v / V, ic50=1.5, hill=1.0))
        out['DO'] = do_sum
        out['ph'] = ph_of(out['alk'], out['dic'], out['pt'])
        if ph_adjust_to is not None:                       # acid / base addition: DIC and phosphate unchanged, alkalinity moved to the target pH
            out['ph'] = float(ph_adjust_to)
            out['alk'] = alk_of(out['ph'], out['dic'], out['pt'])
        out['atu'] += float(atu_kg_m3) * 1000.0
        if dechlorinate:
            out['chlorine'] = 0.0
        if temperature_k is not None:
            out['T'] = float(temperature_k)
        if aerate or temperature_k is not None:            # about 15 min shaking in air: O2 saturated, CO2 equilibrated with the room air
            out['DO'] = None
            self._equilibrate_co2(out)
        out['aerated'] = bool(aerate)
        for src, v in parts:
            if id(src) in pool and src in self.samples.values():
                src['V'] -= v
        self.led['sample_used_m3'].add(tot + seed_v)
        self._add_sample(sample_id, out, sample_id)
        return dict(sample_id=sample_id, volume_m3=float(V), dilution_factor=float(V / tot) if tot > 0 else 0.0)

    def _equilibrate_co2(self, L):
        """CO2(aq) to equilibrium with the room air at constant alkalinity and phosphate (DIC and pH follow)."""
        T = L['T']
        c = ENG['co2_henry_mol_m3_pa'] * AIR_CO2 * (self.plant['ambient_pressure_pa'] - p_vapour(T))
        lo, hi = 2.0, 12.5
        for _ in range(60):                                 # alk_of with DIC = c (1 + r1) is monotone in pH
            mid = 0.5 * (lo + hi)
            if alk_of(mid, c * (1.0 + 10.0 ** (mid - PKA_C)), L['pt']) < L['alk']:
                lo = mid
            else:
                hi = mid
        L['ph'] = 0.5 * (lo + hi)
        L['dic'] = c * (1.0 + 10.0 ** (L['ph'] - PKA_C))

    def _bottle(self, position):
        return self.bottles[position]

    def _sat(self, T):
        """Air-saturated dissolved oxygen (mg/l) at temperature T and the declared ambient pressure."""
        return henry_o2(T) * AIR_O2 * (self.plant['ambient_pressure_pa'] - p_vapour(T))

    def _cmd_bottle_fill(self, position, sample_id, volume_m3):
        self._need_plant()
        b = self._bottle(position)
        if b['head'] is not None:
            raise ActionRejected('HEAD_ON_BOTTLE')
        if b['sleeve']:
            raise ActionRejected('SLEEVE_IN_NECK')
        if b['liquid'] is not None:
            raise ActionRejected('BOTTLE_NOT_EMPTY')
        s = self._source(sample_id)
        if s['V'] < volume_m3 - 1e-12:
            raise ActionRejected('INSUFFICIENT_SAMPLE')
        s['V'] -= volume_m3
        T = s['T']
        sat = self._sat(T)                                  # air saturation at the sample temperature (mg/l)
        do = sat if (s.get('aerated') or s['DO'] is None) else min(s['DO'], 2.0 * sat)
        pools = {}
        seed_lag = (ENG['seed_lag_days'].get(s['seed_type']) or 0.0) if s['seed_type'] != 'none' else 0.0
        for sid, c in s['subs'].items():
            e = self.subs[sid]
            kin = e['kinetics']
            lag = ((kin.get('lag_days') or 0.0) + seed_lag) * 86400.0
            thod = e['thod_nh4_mg_mg']
            pools[sid] = dict(L=c * thod * kin['f_ult'], k20=kin['k20_per_day'] / 86400.0, a=0.0 if lag > 0 else 1.0, lag=lag,
                              n_per_o2=(e.get('n_mass_fraction', 0.0) / thod) if thod > 0 else 0.0)
        inhibitors = list(copy.deepcopy(s['inhibitors']))
        for sid, c in s['subs'].items():
            ic = self.subs[sid].get('ic50_mg_l')
            if ic:
                inhibitors.append(dict(name=sid, c=c, ic50=ic, hill=1.5))
        b['liquid'] = dict(V=float(volume_m3), pools=pools, DO=do, nh4=s['nh4'], no3=0.0, ph=s['ph'], dic=s['dic'], alk=s['alk'], pt=s['pt'],
                           seed_type=s['seed_type'], seed_frac=s['seed_frac'],
                           xn=s['xn'], atu=s['atu'], inhibitors=inhibitors, chlorine=s['chlorine'], sulfite=s['sulfite'], sulfite_tau=ENG['sulfite_reaction_s'],
                           naoh=False, active=True, o2_used=0.0, T_fill=T)
        b['T'] = T
        Vg = self._gas_volume(b)
        dry = (self.plant['ambient_pressure_pa'] - p_vapour(T)) * Vg / (R_GAS * T)
        b['gas'] = dict(o2=dry * AIR_O2, inert=dry * (1 - AIR_O2 - AIR_CO2), co2=dry * AIR_CO2)
        b['nominal_ml'] = round(volume_m3 * 1e6, 1)
        b['sample_id'] = sample_id
        self.led['co2_initial_mol'].add(s['dic'] * volume_m3)          # dissolved inorganic carbon brought in with the sample
        lo, hi = DOC['filling_temperature_k']
        if not lo - 1e-9 <= T <= hi + 1e-9:
            self.warnings['FILLING_TEMPERATURE_' + position] = f'{position}装样温度不在15–21 °C（AutoTemp可能来不及补偿）'
            self._event('warning', 'FILLING_TEMPERATURE', self.warnings['FILLING_TEMPERATURE_' + position], ref=position)
            self._event('truth', 'FILLING_TEMPERATURE_VALUE', f'{position}装样温度{T - 273.15:.1f} °C', ref=position)    # evaluator only
        self._own('bottle:' + position)
        return dict(position=position, volume_m3=float(volume_m3))

    def _cmd_bottle_add_stir_bar(self, position):
        self._need_plant()
        b = self._bottle(position)
        if b['head'] is not None:
            raise ActionRejected('HEAD_ON_BOTTLE')
        if b['sleeve']:
            raise ActionRejected('SLEEVE_IN_NECK')
        if b['stir_bar']:
            raise ActionRejected('ALREADY_PRESENT')
        b['stir_bar'] = True
        return dict(position=position)

    def _cmd_bottle_add_reagent(self, position, reagent, drops=None, volume_m3=None):
        self._need_plant()
        b = self._bottle(position)
        if b['head'] is not None:
            raise ActionRejected('HEAD_ON_BOTTLE')
        if b['sleeve']:
            raise ActionRejected('SLEEVE_IN_NECK')
        L = b['liquid']
        if L is None:
            raise ActionRejected('BOTTLE_EMPTY')
        if reagent not in self.samples:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if reagent == 'nth_600':
            if drops is None:
                raise ActionRejected('INVALID_ARGUMENT')
            v = drops * sample_lib()['reagents']['nth_600']['drop_volume_m3']
            L['atu'] += 5000.0 * v / L['V']                 # 5 g/l ATU -> mg/l
            b['reagents']['nth_600_drops'] = b['reagents'].get('nth_600_drops', 0) + drops
        elif reagent == 'oxitop_pm':
            if self.pm_lot is None:
                raise ActionRejected('UNKNOWN_SAMPLE')
            nominal = 164.0e-6                              # lot test value refers to 164 ml (manual 4.5.1)
            L['sulfite'] += self.pm_lot * nominal / L['V']  # mg O2-equivalent per litre
            L['sulfite_tau'] = ENG['pm_tablet_reaction_s']
            b['reagents']['oxitop_pm'] = b['reagents'].get('oxitop_pm', 0) + 1
        else:
            if volume_m3 is None:
                raise ActionRejected('INVALID_ARGUMENT')
            src = self._source('sodium_sulfite_r11')
            if src['V'] < volume_m3:
                raise ActionRejected('INSUFFICIENT_SAMPLE')
            src['V'] -= volume_m3
            L['sulfite'] = (L['sulfite'] * L['V'] + src['sulfite'] * volume_m3) / (L['V'] + volume_m3)
            L['V'] += volume_m3
            b['reagents']['sodium_sulfite_r11_m3'] = b['reagents'].get('sodium_sulfite_r11_m3', 0.0) + volume_m3
        return dict(position=position, reagent=reagent)

    def _cmd_bottle_insert_sleeve(self, position, naoh_pellets):
        self._need_plant()
        b = self._bottle(position)
        if b['head'] is not None:
            raise ActionRejected('HEAD_ON_BOTTLE')
        if b['sleeve']:
            raise ActionRejected('ALREADY_PRESENT')
        if naoh_pellets > 0 and 'nhp_600' not in self.samples:
            raise ActionRejected('UNKNOWN_SAMPLE')
        b['sleeve'] = True
        b['pellets'] = int(naoh_pellets)
        b['naoh_mol'] = naoh_pellets * sample_lib()['reagents']['nhp_600']['pellet_mass_kg'] / M_NAOH
        if self._fault('pellets_in_sample', position):
            self._pellets_fall(b)
        return dict(position=position, naoh_pellets=int(naoh_pellets))

    def _cmd_bottle_empty(self, position):
        self._need_plant()
        b = self._bottle(position)
        if b['head'] is not None:
            raise ActionRejected('HEAD_ON_BOTTLE')
        v = b['liquid']['V'] if b['liquid'] else 0.0
        if b['liquid'] is None and not b['stir_bar'] and not b['sleeve']:
            raise ActionRejected('BOTTLE_EMPTY')
        self.led['waste_m3'].add(v)
        V_b = b['V_b']
        self.bottles[position] = self._empty_bottle(position)
        self.bottles[position]['V_b'] = V_b
        self.warnings.pop('FILLING_TEMPERATURE_' + position, None)
        self._owners.pop('bottle:' + position, None)
        return dict(position=position, removed_m3=float(v))

    def _head(self, head):
        return self.heads[head]

    def _cmd_head_screw_on(self, head, position, tight=True):
        self._need_plant()
        hd = self._head(head)
        if hd['position'] is not None:
            raise ActionRejected('HEAD_ON_BOTTLE')
        b = self._bottle(position)
        if b['head'] is not None:
            raise ActionRejected('POSITION_OCCUPIED')
        hd['position'] = position
        hd['lift'] = 0.0
        b['head'] = head
        b['tight'] = bool(tight)
        if b['T'] is None:                                  # empty bottle: closed air volume at the cabinet temperature
            b['T'] = self.T_air
        self._air_fill(b)                                   # the head space holds room air at the moment of closing
        self.led['co2_initial_mol'].add(b['gas']['co2'])    # air CO2 enclosed with the head space
        b['sealed'] = True
        if not b['sleeve']:
            self.warnings['NO_SLEEVE_' + position] = f'{position}没有橡胶套：瓶口不密封'
        self._own('head:' + head)
        return dict(head=head, position=position, sealed=self._leak_rate(b) == 0.0)

    def _cmd_head_unscrew(self, head):
        self._need_plant()
        hd = self._head(head)
        if hd['position'] is None:
            raise ActionRejected('HEAD_NOT_ON_BOTTLE')
        b = self._bottle(hd['position'])
        b['head'] = None
        b['sealed'] = False
        b['tight'] = True
        if b['gas'] is not None:
            self._air_fill(b)
        self.warnings.pop('NO_SLEEVE_' + hd['position'], None)
        hd['position'] = None
        hd['lift'] = HEAD_LIFT_M
        self._release_head(head)
        return dict(head=head)

    def _release_head(self, head):
        hd = self.heads[head]
        if hd['position'] is None and hd['state'] not in ('adaption', 'test', 'measuring'):
            self._owners.pop('head:' + head, None)

    def _wake(self, hd, screen=None):
        if hd['state'] == 'dead':
            raise ActionRejected('BATTERY_EMPTY')
        self._display_on(hd, screen)

    def _display_on(self, hd, screen=None):
        hd['display_on'] = True
        if hd['state'] == 'off':
            hd['state'] = 'idle'
        hd['display_off_at'] = self.t + (60.0 if hd['battery'] < self._low_battery_fraction(hd) else max(10.0, hd['switch_off_s']))
        if screen:
            hd['screen'] = screen
        elif hd['screen'] in ('off', None):
            hd['screen'] = 'value' if hd['state'] in ('adaption', 'test', 'measuring', 'finished') else 'id'

    def _cmd_head_configure(self, head, duration_days=None, unit=None, sample_volume_m3=None):
        self._need_plant()
        hd = self._head(head)
        if hd['state'] in ('adaption', 'test', 'measuring'):
            raise ActionRejected('MEASUREMENT_RUNNING')
        self._wake(hd, 'info')
        if duration_days is not None:
            hd['duration'] = int(duration_days)
        if unit is not None:
            hd['unit'] = unit
        if sample_volume_m3 is not None:
            hd['volume_ml'] = round(sample_volume_m3 * 1e6, 1)
        return dict(head=head, duration_days=hd['duration'], unit=hd['unit'], sample_volume_m3=hd['volume_ml'] * 1e-6)

    def _cmd_head_set_system(self, head, head_id=None, illumination=None, signal_led=None, switch_off_s=None):
        self._need_plant()
        hd = self._head(head)
        if head_id is not None:
            hd['id'] = int(head_id)
        if illumination is not None:
            hd['illumination'] = bool(illumination)
        if signal_led is not None:
            hd['signal_led'] = bool(signal_led)
        if switch_off_s is not None:
            hd['switch_off_s'] = float(round(switch_off_s / 60.0) * 60.0)
        self._wake(hd, 'id')                                # the new switch-off time applies to this wake-up
        return dict(head=head, head_id=hd['id'])

    def _start(self, head, hd):
        if hd['state'] in ('adaption', 'test', 'measuring'):
            raise ActionRejected('MEASUREMENT_RUNNING')
        hd.update(state='adaption', t_start=self.t, t0=None, t_end=None, p_zero=None, next_test=None, stored=[], led_done=False, stored_idx=None)
        self._traces[head] = []
        for k in [k for k in self.warnings if k.endswith('_' + head)]:
            self.warnings.pop(k)
        self._own('head:' + head)
        hd['display_on'] = True
        hd['screen'] = 'value'
        hd['display_off_at'] = self.t + max(10.0, hd['switch_off_s'])
        self._event('head', 'START', f'测头{head}开始（AutoTemp）', ref=head)
        if hd['position'] is not None:
            b = self.bottles[hd['position']]
            if b['liquid'] is not None and abs(hd['volume_ml'] - b['liquid']['V'] * 1e6) > 0.6:
                self.warnings['VOLUME_SETTING_' + head] = f'{head}样品体积设置{hd["volume_ml"]:g} ml与瓶中实际{b["liquid"]["V"] * 1e6:.1f} ml不一致'

    def _cmd_head_start(self, head):
        self._need_plant()
        hd = self._head(head)
        if hd['state'] == 'dead':
            raise ActionRejected('BATTERY_EMPTY')
        self._start(head, hd)
        return dict(head=head, state=hd['state'])

    def _stop(self, head, hd):
        if hd['state'] in ('adaption', 'test', 'measuring'):
            self._event('head', 'STOPPED', f'测头{head}测量停止（已存值保留）', ref=head)
        hd['state'] = 'off'
        hd['display_on'] = False
        hd['screen'] = 'off'
        self._release_head(head)

    def _cmd_head_stop(self, head):
        self._need_plant()
        hd = self._head(head)
        if hd['state'] in ('off', 'dead'):
            raise ActionRejected('HEAD_OFF')
        self._stop(head, hd)
        return dict(head=head, state=hd['state'])

    def _cmd_head_press_key(self, head, key, long=False):
        self._need_plant()
        hd = self._head(head)
        if hd['state'] == 'dead':
            raise ActionRejected('BATTERY_EMPTY')
        was_on = hd['display_on']
        sc = hd['screen'] if was_on else 'off'
        measuring = hd['state'] in ('adaption', 'test', 'measuring')
        if not was_on and not long:                         # any key switches the display / head on
            self._wake(hd)
        elif key == 'M' and long:
            if measuring:
                self._wake(hd, 'confirm_off')
            else:
                self._stop(head, hd)
        elif sc == 'confirm_off':
            if key == 'M':
                self._stop(head, hd)
            else:
                self._wake(hd, 'value')
        elif key == 'M':
            if sc == 'id':
                self._wake(hd, 'info')
            elif sc == 'info':
                if measuring:
                    self._wake(hd, 'value')
                else:
                    self._start(head, hd)
            elif sc in ('menu_meas', 'menu_sys'):
                self._wake(hd, 'info' if sc == 'menu_meas' else 'id')
            else:
                self._wake(hd, 'id')
        elif key == 'ENTER':
            if long:
                hd['menu_item'] = 0
                self._wake(hd, 'menu_sys')
            elif sc in ('id', 'info') and not measuring:
                hd['menu_item'] = 0
                self._wake(hd, 'menu_meas')
            elif sc == 'menu_meas':
                hd['menu_item'] = (hd['menu_item'] + 1) % 3
                self._wake(hd)
            elif sc == 'menu_sys':
                if hd['menu_item'] == 0:
                    hd['illumination'] = not hd['illumination']
                elif hd['menu_item'] == 1:
                    hd['signal_led'] = not hd['signal_led']
                hd['menu_item'] = (hd['menu_item'] + 1) % 4
                self._wake(hd)
            else:
                self._wake(hd)
        else:                                               # navigation key
            if sc == 'menu_meas':
                if hd['menu_item'] == 0:
                    hd['duration'] = hd['duration'] % 7 + 1
                elif hd['menu_item'] == 1:
                    hd['unit'] = UNITS[(UNITS.index(hd['unit']) + 1) % len(UNITS)]
                else:
                    hd['volume_ml'] = VOLUMES_ML[(VOLUMES_ML.index(hd['volume_ml']) + 1) % len(VOLUMES_ML)] if hd['volume_ml'] in VOLUMES_ML else VOLUMES_ML[0]
            elif sc == 'menu_sys':
                if hd['menu_item'] == 2:
                    hd['switch_off_s'] = (hd['switch_off_s'] + 60.0) % 600.0
                elif hd['menu_item'] == 3:
                    hd['id'] = (hd['id'] + 1) % 1000
            elif hd['stored'] and sc in ('value', 'stored'):
                hd['stored_idx'] = 0 if hd['stored_idx'] is None else (hd['stored_idx'] + 1) % len(hd['stored'])
                hd['screen'] = 'stored'
            self._wake(hd)
        d = self._display(head, hd)
        return dict(head=head, screen=d['screen'], lines=d['lines'])

    def _cmd_head_read(self, head):
        self._need_plant()
        hd = self._head(head)
        if hd['state'] != 'dead':                           # an empty battery shows nothing
            self._display_on(hd)
        d = self._display(head, hd)
        color, _ = self._led(head, hd)
        stored = self._stored_list(hd)                      # same rule as heads.stored_values: value kept over 50 digits (flagged), None only for OFL
        return dict(head=head, display_on=hd['display_on'], screen=d['screen'], lines=d['lines'], value=d['value'], unit=hd['unit'], label=d['label'],
                    state=hd['state'], stored=stored, led=color, battery_low=hd['battery'] < self._low_battery_fraction(hd))

    def _cmd_head_replace_battery(self, head):
        self._need_plant()
        hd = self._head(head)
        hd['battery'] = 1.0
        if hd['state'] == 'dead':
            hd['state'] = 'off'
        self.faults.discard(('battery_drain', head))
        return dict(head=head)

    def _cmd_head_pt_test(self, head):
        self._need_plant()
        hd = self._head(head)
        if hd['position'] is not None:
            raise ActionRejected('HEAD_ON_BOTTLE')
        if hd['state'] in ('adaption', 'test', 'measuring'):
            raise ActionRejected('MEASUREMENT_RUNNING')
        if hd['state'] == 'dead':
            raise ActionRejected('BATTERY_EMPTY')
        PT = DOC['pt_test']
        p0 = self.plant['ambient_pressure_pa']
        V0 = ENG['pt_device_volume_m3']
        dV = (PT['syringe_end_ml'] - PT['syringe_start_ml']) * 1e-6
        dp_true = p0 * dV / (V0 + dV)
        meas0 = p0 * (1 + hd['gain']) + hd['offset'] + ENG['sensor_noise_pa'] * _gauss(self.seed ^ _h(head), self.tick, 31)
        meas1 = (p0 - dp_true) * (1 + hd['gain']) + hd['offset'] + ENG['sensor_noise_pa'] * _gauss(self.seed ^ _h(head), self.tick, 32)
        dp = round((meas0 - meas1) / 100.0) * 100.0
        digits = round(dp / 100.0 / DIGIT_HPA, 1)
        table = sorted((abs(v * 100.0 - p0), PT['control_digits'][k]) for k, v in PT['mean_pressure_hpa'].items())
        ctrl = table[0][1]
        self._wake(hd, 'id')
        self._event('head', 'PT_TEST', f'测头{head} PT检查：{digits:.0f} digit（控制值{ctrl}）', ref=head)
        return dict(head=head, measured_digits=float(digits), control_digits=int(ctrl), deviation_digits=float(round(digits - ctrl, 1)),
                    passed=abs(round(digits) - ctrl) <= PT['tolerance_digits'], delta_p_pa=float(dp))

    def _cmd_platform_set_power(self, on):
        self._need_plant()
        self.platform_on = bool(on)
        self._event('platform', 'POWER', 'IS 6搅拌台' + ('通电' if on else '断电'))
        return dict(on=self.platform_on)

    def _cmd_incubator_set_door(self, open):
        self._need_plant()
        self.door_open = bool(open)
        self._event('incubator', 'DOOR', '恒温箱门' + ('打开' if open else '关上'))
        return dict(open=self.door_open)

    def _cmd_eval_bod(self, sample_value_kg_m3, blank_value_kg_m3, sample_volume_m3, total_volume_m3):
        if sample_volume_m3 > total_volume_m3 * (1 + 1e-12):
            raise ActionRejected('INVALID_ARGUMENT')
        A, B, Ve, Vt = sample_value_kg_m3, blank_value_kg_m3, sample_volume_m3, total_volume_m3
        bod = (A - B * (Vt - Ve) / Vt) * Vt / Ve
        return dict(bod_kg_m3=float(bod), dilution_factor=float(Vt / Ve), formula='BOD = (A - B (Vt - Ve) / Vt) Vt / Ve (BOD primer, seeded dilution)')

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_provenance_read(self):
        libs = [dict(name=k, file=str((ASSET_DIR / v).relative_to(ROOT)), entries=len(_lib(k).get('entries', _lib(k).get('samples', _lib(k).get('documented', {})))))
                for k, v in LIBS.items()]
        return dict(libraries=libs, substances=sorted(k for k in self.subs if k not in PSEUDO)[:200])

    def _cmd_bottle_inspect(self, position):
        tb = self.read('evaluator')['truth.bottles'][position]
        L = self.bottles[position]['liquid']
        st = dict(tb or {}, pools={k: dict(remaining_kg_m3=p['L'] / 1000.0, adaptation=p['a']) for k, p in L['pools'].items()} if L else {},
                  ph=L['ph'] if L else None, alkalinity_mol_m3=L['alk'] if L else None, atu_kg_m3=(L['atu'] / 1000.0) if L else None, nitrifier_activity=L['xn'] if L else None,
                  sulfite_o2_kg_m3=(L['sulfite'] / 1000.0) if L else None, naoh_in_sample=bool(L and L['naoh']))
        return dict(position=position, state=json.loads(json.dumps(st, default=float)))

    def _cmd_data_trace(self, head, max_points=MAX_TRACE):
        tr = self._traces[head]
        n = len(tr)
        idx = list(range(n)) if n <= max_points else [round(i * (n - 1) / (max_points - 1)) for i in range(max_points)]
        return dict(head=head, time_s=[float(tr[i][0]) for i in idx], pressure_pa=[float(tr[i][1]) for i in idx],
                    delta_p_pa=[None if tr[i][2] is None else float(tr[i][2]) for i in idx], bod_true_kg_m3=[None if tr[i][3] is None else float(tr[i][3]) for i in idx])

    def _cmd_ledger_read(self):
        return dict(ledger={k: float(v.value()) for k, v in self.led.items()})

    def _cmd_fault_inject(self, fault, active, target=None):
        self._need_plant()
        if fault in ('head_untight', 'pellets_in_sample') and target not in POSITIONS:
            raise ActionRejected('INVALID_ARGUMENT')
        if fault == 'battery_drain' and target not in HEADS:
            raise ActionRejected('INVALID_ARGUMENT')
        key = (fault, '' if fault == 'incubator_failure' else target)
        if active:
            self.faults.add(key)
            if fault == 'pellets_in_sample' and self.bottles[target]['sleeve']:
                self._pellets_fall(self.bottles[target])
        else:
            self.faults.discard(key)
        self._event('fault', 'FAULT', f'{fault} {"on" if active else "off"} {target or ""}')
        return dict(faults=self._fault_list())

    def _pellets_fall(self, b):
        """NaOH pellets drop from the rubber sleeve into the sample: strongly alkaline sample (no biology, CO2 bound in the liquid), no CO2 absorber left in the sleeve."""
        L = b['liquid']
        if L is None or b['naoh_mol'] <= 0.0:
            return
        L['naoh'] = True
        L['alk'] += b['naoh_mol'] / L['V']                  # dissolved NaOH (mol/m3)
        L['ph'] = ph_of(L['alk'], L['dic'], L['pt'], 12.0)
        b['naoh_mol'] = 0.0

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    # ---------------------------------------------------------------- invoke
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        snap = None
        if CAPS[capability_id]['kind'] not in ('query', 'analysis') and capability_id != 'sim.step':
            snap = self._snapshot()
        try:
            out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        except ActionRejected:
            if snap is not None:
                self._restore(snap)
            raise
        if CAPS[capability_id]['kind'] not in ('query', 'analysis') and capability_id != 'sim.step':
            self.revision += 1
        return out

    _NOCOPY = ('_principals', '_requests')

    def _snapshot(self):
        return {k: copy.deepcopy(v) for k, v in self.__dict__.items() if k not in self._NOCOPY}

    def _restore(self, snap):
        for k, v in snap.items():
            self.__dict__[k] = v


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED
                    and k != 'sample.prepare'}


class WtwOxitopiSession:
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
        meta = copy.deepcopy(export_wtw_oxitopi_contract_metadata())
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

    def _foreign(self, object_id):
        owner = self._host._owners.get(object_id)
        return owner is not None and owner != self._principal and self._host._principals.get(owner) == 'agent'

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        if self._role == 'agent':
            values['samples.available'] = [x for x in values['samples.available'] if not self._foreign('sample:' + x)]
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

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        for key in ('sample_id',):
            sid = arguments.get(key)
            if isinstance(sid, str) and self._foreign('sample:' + sid):
                raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'sample.prepare' and any(self._foreign('sample:' + s['source_id']) for s in arguments.get('sources', [])):
            raise ActionRejected('ACCESS_DENIED')
        pos, head = arguments.get('position'), arguments.get('head')
        if capability_id in _CONTROL_GUARDED and isinstance(pos, str) and self._foreign('bottle:' + pos):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _CONTROL_GUARDED and isinstance(head, str) and self._foreign('head:' + head):
            raise ActionRejected('ACCESS_DENIED')

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
            self._guard(capability_id, arguments)
            h._caller = self._principal
            try:
                result = h.invoke(capability_id, **arguments)
            finally:
                h._caller = None
            if self._role == 'agent' and capability_id == 'events.read':
                result = dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in h.events
                                      if e['time_s'] >= arguments['since_s'] and e['kind'] not in ('fault', 'truth')], dropped=h.events_dropped)
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
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围（或组合不成立）', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值、另一个agent的样品或瓶）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'UNKNOWN_SUBSTANCE': '物质不在模板库中', 'ITEM_EXISTS': 'ID已存在',
    'TOO_MANY_ITEMS': '样品数量超过上限', 'UNKNOWN_SAMPLE': '样品或试剂不存在', 'INSUFFICIENT_SAMPLE': '样品体积不够', 'VOLUME_OUT_OF_RANGE': '体积不合适（来源之和超过终体积或没有稀释液补足）',
    'NO_SEED': '没有接种液（先kit.provide settled_sewage_seed）', 'HEAD_ON_BOTTLE': '测头还拧在瓶上', 'SLEEVE_IN_NECK': '橡胶套已插入瓶颈', 'BOTTLE_NOT_EMPTY': '瓶中已有样品',
    'BOTTLE_EMPTY': '瓶中没有样品', 'ALREADY_PRESENT': '已经放入', 'POSITION_OCCUPIED': '该瓶已拧上另一个测头', 'HEAD_NOT_ON_BOTTLE': '测头不在瓶上',
    'MEASUREMENT_RUNNING': '测量进行中（先停止）', 'BATTERY_EMPTY': '电池耗尽（先更换电池）', 'HEAD_OFF': '测头已关闭',
}
_P = ['PLANT_REQUIRED']
_HD = ['BATTERY_EMPTY']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'stock.provide': _P + ['ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_SUBSTANCE'], 'kit.provide': _P + ['UNKNOWN_SUBSTANCE'], 'substance.define': _P + ['ITEM_EXISTS'],
    'sample.prepare': _P + ['ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_SAMPLE', 'INSUFFICIENT_SAMPLE', 'VOLUME_OUT_OF_RANGE', 'NO_SEED'],
    'bottle.fill': _P + ['HEAD_ON_BOTTLE', 'SLEEVE_IN_NECK', 'BOTTLE_NOT_EMPTY', 'UNKNOWN_SAMPLE', 'INSUFFICIENT_SAMPLE'],
    'bottle.add_stir_bar': _P + ['HEAD_ON_BOTTLE', 'SLEEVE_IN_NECK', 'ALREADY_PRESENT'],
    'bottle.add_reagent': _P + ['HEAD_ON_BOTTLE', 'SLEEVE_IN_NECK', 'BOTTLE_EMPTY', 'UNKNOWN_SAMPLE', 'INSUFFICIENT_SAMPLE'],
    'bottle.insert_sleeve': _P + ['HEAD_ON_BOTTLE', 'ALREADY_PRESENT', 'UNKNOWN_SAMPLE'], 'bottle.empty': _P + ['HEAD_ON_BOTTLE', 'BOTTLE_EMPTY'],
    'head.screw_on': _P + ['HEAD_ON_BOTTLE', 'POSITION_OCCUPIED'], 'head.unscrew': _P + ['HEAD_NOT_ON_BOTTLE'], 'head.configure': _P + ['MEASUREMENT_RUNNING'] + _HD,
    'head.set_system': _P + _HD, 'head.start': _P + ['MEASUREMENT_RUNNING'] + _HD, 'head.stop': _P + ['HEAD_OFF'], 'head.press_key': _P + _HD + ['MEASUREMENT_RUNNING'], 'head.read': _P,
    'head.replace_battery': _P, 'head.pt_test': _P + ['HEAD_ON_BOTTLE', 'MEASUREMENT_RUNNING'] + _HD, 'platform.set_power': _P, 'incubator.set_door': _P,
    'eval.bod': [], 'events.read': [], 'provenance.read': [], 'bottle.inspect': [], 'data.trace': [], 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子；之后再声明须给同一种子', ['plant.configured']),
    'stock.provide': ('已声明实验室条件；ID唯一；物质在模板库或已由substance.define定义', ['plant.configured', 'samples.available']),
    'kit.provide': ('已声明实验室条件；OxiTop PM须给批次检验值', ['plant.configured']), 'substance.define': ('已声明实验室条件；ID唯一', ['plant.configured']),
    'sample.prepare': ('已声明实验室条件；ID唯一；来源存在且体积足够；来源与接种体积之和不超过终体积，不足部分须有稀释液；接种须先有接种液', ['plant.configured', 'samples.available']),
    'bottle.fill': ('瓶空、没有测头与橡胶套；样品体积足够；体积为溢流量瓶的7档之一', ['setup.positions', 'samples.available']),
    'bottle.add_stir_bar': ('没有测头与橡胶套；瓶中还没有搅拌子', ['setup.positions']), 'bottle.add_reagent': ('瓶中有样品、没有测头与橡胶套；试剂已提供', ['setup.positions']),
    'bottle.insert_sleeve': ('没有测头、还没有橡胶套；放NaOH颗粒时须已提供NHP 600', ['setup.positions']), 'bottle.empty': ('没有测头；瓶中有东西', ['setup.positions']),
    'head.screw_on': ('测头不在别的瓶上；该瓶没有测头（没有橡胶套时不密封）', ['setup.positions', 'heads.status']), 'head.unscrew': ('测头在瓶上', ['heads.status']),
    'head.configure': ('不在测量中；电池未耗尽', ['heads.status']), 'head.set_system': ('电池未耗尽', ['heads.status']),
    'head.start': ('不在测量中；电池未耗尽（重新开始会删除之前存储的值）', ['heads.status']), 'head.stop': ('测头开着', ['heads.status']),
    'head.press_key': ('电池未耗尽', ['heads.status']), 'head.read': ('已声明实验室条件', ['plant.configured']), 'head.replace_battery': ('已声明实验室条件', ['plant.configured']),
    'head.pt_test': ('测头不在瓶上、不在测量中；电池未耗尽', ['heads.status']), 'platform.set_power': ('已声明实验室条件', ['plant.configured']),
    'incubator.set_door': ('已声明实验室条件', ['plant.configured']), 'eval.bod': ('样品体积不大于总体积', []), 'fault.inject': ('已声明实验室条件；瓶类故障须给工位、电池故障须给测头', ['plant.configured']),
}
_LEDS = [f'led.{h}_{c}' for h in HEADS for c in ('blue', 'red', 'green')]
_DISP = [f'display.{h}_value' for h in HEADS]
_LIFT = [f'head.{h}_lift_m' for h in HEADS]
EFFECTS = {
    'plant.configure': ['plant.configured', 'incubator.temperature_k', 'truth.incubator_air_k', 'truth.heads', 'truth.bottles'] + _DISP + _LEDS,
    'stock.provide': ['samples.available'], 'kit.provide': ['samples.available'], 'substance.define': [],
    'sample.prepare': ['samples.available'], 'bottle.fill': ['setup.positions', 'truth.bottles', 'warnings.active'], 'bottle.add_stir_bar': ['setup.positions', 'truth.bottles'],
    'bottle.add_reagent': ['setup.positions', 'truth.bottles'], 'bottle.insert_sleeve': ['setup.positions', 'truth.bottles'],
    'bottle.empty': ['setup.positions', 'truth.bottles', 'warnings.active'],
    'head.screw_on': ['setup.positions', 'heads.status', 'truth.bottles', 'truth.heads', 'warnings.active'] + _LIFT + _DISP + _LEDS,
    'head.unscrew': ['setup.positions', 'heads.status', 'truth.bottles', 'truth.heads', 'warnings.active'] + _LIFT + _DISP + _LEDS,
    'head.configure': ['heads.status', 'heads.stored_values'] + _DISP, 'head.set_system': ['heads.status'] + _DISP + _LEDS,
    'head.start': ['heads.status', 'heads.stored_values', 'truth.heads', 'warnings.active'] + _DISP + _LEDS,
    'head.stop': ['heads.status'] + _DISP + _LEDS, 'head.press_key': ['heads.status', 'heads.stored_values', 'truth.heads', 'warnings.active'] + _DISP + _LEDS,
    'head.read': ['heads.status'] + _DISP, 'head.replace_battery': ['heads.status', 'truth.heads', 'truth.faults'] + _LEDS + _DISP, 'head.pt_test': ['heads.status'] + _DISP,
    'platform.set_power': ['platform.powered', 'platform.stir_velocity_rad_s'], 'incubator.set_door': ['incubator.door_open'],
    'fault.inject': ['truth.faults', 'truth.bottles'], 'sim.step': ['clock.time_s'],
}
_PHYS = ['incubator.temperature_k', 'truth.incubator_air_k', 'truth.bottles', 'truth.heads', 'heads.status', 'heads.stored_values', 'warnings.active'] + _DISP + _LEDS
ON_STEP = {
    'plant.configure': _PHYS, 'sim.step': _PHYS + ['clock.time_s'], 'fault.inject': _PHYS, 'head.start': _PHYS, 'head.stop': _PHYS,
    'head.screw_on': _PHYS, 'head.unscrew': _PHYS, 'bottle.fill': _PHYS, 'bottle.insert_sleeve': _PHYS, 'bottle.add_reagent': _PHYS, 'bottle.add_stir_bar': _PHYS,
    'platform.set_power': ['truth.bottles', 'truth.heads', 'heads.stored_values'] + _DISP, 'incubator.set_door': _PHYS,
    'head.configure': _PHYS, 'head.press_key': _PHYS, 'head.read': ['heads.status'] + _DISP, 'head.set_system': _PHYS, 'head.replace_battery': _PHYS,
}
ON_STEP_ZH = {
    'plant.configure': '声明后恒温箱温度、瓶内温度与压力按宿主网格演化', 'sim.step': '按60 s网格积分：温度、气液交换、生物耗氧、CO2吸收、测头AutoTemp与存值',
    'fault.inject': '故障作用于随后的过程（漏气、NaOH进入样品、恒温箱失控、电池耗尽）', 'head.start': 'AutoTemp 60 min适应→测试间隔→开始测量；每24 h存一个值；到期结束（绿灯约24 h）',
    'head.stop': '测量停止后不再存值', 'head.screw_on': '密封后瓶内气体与样品之间交换氧，生物耗氧使压力下降', 'head.unscrew': '打开后瓶内气相回到室内空气',
    'bottle.fill': '样品温度向恒温箱温度松弛；生物降解开始', 'bottle.insert_sleeve': 'NaOH颗粒吸收CO2（容量有限）', 'bottle.add_reagent': 'ATU抑制硝化；PM片剂/亚硫酸钠按约1 h消耗氧',
    'bottle.add_stir_bar': '感应搅拌时气液传质加快', 'platform.set_power': '通电后搅拌子转动，气液传质加快；断电只靠扩散', 'incubator.set_door': '开门时箱内温度向室温变化，关门后恢复',
    'head.configure': '显示约2 min后自动关闭', 'head.press_key': '显示约2 min后自动关闭；开始测量后同head.start', 'head.read': '显示约2 min后自动关闭',
    'head.set_system': '设置在随后的显示与状态灯上生效', 'head.replace_battery': '电量恢复，状态灯恢复颜色',
}
EXEC_TEXT = {
    'head.start': ('测量状态adaption→test→measuring→finished（heads.status），每24 h一个存储值', '重新开始删除之前的存储值；拧下测头或漏气时压力回到环境，数值偏低；电池耗尽时测量停止（已存值保留）'),
    'head.press_key': ('按菜单逻辑立即切换显示页或设置（见lines）', '同步原子操作：成功时一次写入全部效果，被拒时状态不变'),
}
RESOURCES = {k: ['host_clock'] for k in CAPS}
OBSERVES = {'head.start': ['heads.status'] + _DISP + _LEDS, 'head.read': ['heads.status', 'heads.stored_values'] + _DISP, 'head.press_key': ['heads.status'] + _DISP,
            'bottle.fill': ['setup.positions'], 'head.screw_on': ['setup.positions'] + _LIFT, 'head.unscrew': ['setup.positions'] + _LIFT,
            'platform.set_power': ['platform.powered'], 'incubator.set_door': ['incubator.door_open', 'incubator.temperature_k']}
_WB, _PR, _M4, _BP, _CAT, _PRES, _AR, _IS6, _SET, _PF, _USGS, _PUB, _SIM = (
    'user_workbook', 'manufacturer_primary', 'manufacturer_manual_e04', 'manufacturer_bod_primer', 'manufacturer_catalog', 'manufacturer_presentation',
    'manufacturer_application_report', 'distributor_is6', 'distributor_is6_set', 'distributor_pf600', 'usgs_oxygen_solubility', 'pubchem_hsdb', 'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_PR, _BP, _SIM], 'stock.provide': [_PUB, _SIM], 'kit.provide': [_BP, _CAT, _PR, _SIM], 'substance.define': [_SIM],
                    'sample.prepare': [_BP, _PR, _SIM], 'bottle.fill': [_PR, _BP, _CAT], 'bottle.add_stir_bar': [_PR, _BP, _CAT], 'bottle.add_reagent': [_PR, _BP, _CAT],
                    'bottle.insert_sleeve': [_PR, _BP, _CAT], 'bottle.empty': [_BP], 'head.screw_on': [_PR, _BP], 'head.unscrew': [_PR], 'head.configure': [_PR, _M4],
                    'head.set_system': [_PR], 'head.start': [_PR, _BP], 'head.stop': [_PR], 'head.press_key': [_PR], 'head.read': [_PR, _BP], 'head.replace_battery': [_PR],
                    'head.pt_test': [_PR, _BP], 'platform.set_power': [_IS6, _CAT, _BP], 'incubator.set_door': [_BP, _SIM], 'eval.bod': [_BP],
                    'provenance.read': [_PUB, _USGS], 'fault.inject': [_PR, _SIM]})
CAP_SOURCES.update({k: [_PR, _CAT] for k in UNSUPPORTED})
PARAM_ZH = {'incubator_k': '恒温箱设定温度', 'room_k': '实验室室温（开门时箱内温度趋向它）', 'ambient_pressure_pa': '环境气压（海拔）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.respirometric_bod_process.limitations。'
CAP_LIMITS = {
    'head.start': ['AutoTemp：60 min适应期（不评估压力）后置零；测试间隔BOD5为30 min，其它时长按天数等比例（假设）；压力升高时重新置零，最迟3 h（或3 %的测量时长）开始；'
                   'BOD按WTW公式由压降换算（510 ml、20 °C、α=0.03103），与真实耗氧之差来自温度、瓶容积公差、CO2未完全吸收与传感器误差。'],
    'head.press_key': ['菜单为简化等价实现：信息页、测量设置（天数/单位/体积）、系统设置（照明/状态灯/自动关闭/ID）、存储值浏览、关机确认；日期时间与语言菜单未建模。'],
    'head.pt_test': ['PT检测器与测头的气体体积9.4 ml由入门手册的控制值反推（derived）；只检查传感器读数，不检查长期密封（厂家）。'],
    'sample.prepare': ['混合按体积分数线性相加；纯蒸馏水稀释作为渗透压抑制（工程模型）；充氧到饱和、预调温与去氯为理想操作；pH按设定值（不模拟缓冲容量）。'],
    'bottle.fill': ['溢流量瓶体积为7档之一（厂家）；装样温度不在15–21 °C时给提示（AutoTemp只能补偿有限温差）；瓶的实际容积按σ=2 ml的公差偏离510 ml（假设）。'],
    'eval.bod': ['接种稀释校正公式取自BOD入门手册；不做多稀释度的线性回归。'],
}
LIMITATIONS = [
    '选定配置：OxiTop-i IS 6套装（6个OxiTop-i测头：3蓝3灰、6个PF 600瓶、6个GK 600 L橡胶套、6个RST 600搅拌子、IS 6感应搅拌台）；恒温箱为宿主环境（plant.configure声明20 °C）；溢流量瓶与实验室玻璃器皿只作为体积档；IDS无线、长期测量与数据导出不支持。',
    '气相：理想气体（O2、惰性气体、CO2与饱和水蒸气）；氧溶解度按Benson–Krause；瓶内气体与液体同一温度；液面传质kLa = kL·A/V（kL为假设）；不建模气泡、液膜与瓶壁吸附。',
    '生物降解：每种物质的可降解氧当量按浓度×ThOD(NH4)×f_ult，一级速率k20（文献点推导）乘温度系数1.047^(T−20)、溶解氧Monod限制、pH、毒性与游离氯；接种类型决定滞后；不建模生物量增长、底物共代谢、挥发、吸附与混合物间的相互作用。',
    '硝化：氨氮按4.57 mg O2/mg N，硝化菌活性按0.45 1/d增长，ATU按Ki 0.05 mg/l抑制；亚硝酸积累与反硝化不建模。',
    'CO2：呼吸商1；液相溶解无机碳按碳酸第一解离分配（pH固定为样品设定值），与气相交换；NaOH颗粒按900 s时间常数吸收气相CO2（容量按0.12 g/粒）；颗粒受潮溶解不建模。',
    '温度：瓶内液体按自然对流向恒温箱空气松弛（换热系数8 W/(m²·K)）；恒温箱温度在设定值±0.3 K周期波动，开门趋向室温；不建模瓶间温差与搅拌发热。',
    '测头：传感器增益/零点/噪声在厂家精度内随机（按种子）；显示与存储按所选单位换算；电池按厂家寿命折算的线性放电；状态灯每5 s闪0.5 s（闪烁时长为假设）；菜单为简化等价实现。',
    '样品瓶、样品液体、试剂与颗粒在场景中只作为静态几何（瓶、橡胶套、颗粒与搅拌子）；装样、加试剂、拧测头为宿主/机器人抽象，没有液体与接触仿真；测头拧上/取下用测头抬起关节（0/0.09 m）表示。',
]


def _sampling_text(k):
    if k.startswith(('display.', 'led.')):
        return 'Value at the host time: display value from the last sensor reading (60 s grid); LED flash phase from the measurement start (5 s period).'
    if k.startswith(('truth.', 'heads.', 'incubator.temperature')) or k == 'clock.time_s':
        return 'Integrated on the 60 s host grid; between grid points the last grid value is held.'
    return 'Changes when a command is accepted; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k == 'sample.prepare' or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _pre_text(k):
    errs, text = CAP_ERRORS.get(k, []), PRECONDITIONS[k][0]
    if 'PLANT_REQUIRED' in errs and '实验室条件' not in text:
        text = '已声明实验室条件（否则PLANT_REQUIRED）；' + text
    return text


def _pre_slots(k):
    errs = CAP_ERRORS.get(k, [])
    ids = (['plant.configured'] if 'PLANT_REQUIRED' in errs else []) + list(PRECONDITIONS[k][1])
    return list(dict.fromkeys(ids))


def _foreign_text(k):
    if k in _CONTROL_GUARDED and k.startswith('bottle.'):
        return '；agent只能操作自己装样的瓶与自己的样品（ACCESS_DENIED）'
    if k in _CONTROL_GUARDED and k.startswith('head.'):
        return '；另一个agent正在使用的测头（拧在瓶上或测量中）与它装样的瓶不能操作（ACCESS_DENIED）'
    if k == 'sample.prepare':
        return '；agent只能用自己配的样品与评测者给的水样（ACCESS_DENIED）'
    return ''


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：无模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节、LCD读数或状态灯（' + '、'.join(seen[:6]) + ('…' if len(seen) > 6 else '')
                                   + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节或读数', binding_ids=[])


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    doc('pressure_range_pa', list(DOC['pressure_range_pa']), 'Pa', '测头压力量程500–1250 hPa', [_PR, _WB])
    doc('bod_range_kg_m3', [0.0, 4.0], 'kg/m3', 'BOD量程0–4000 mg/l', [_PR, _WB])
    doc('accuracy_relative', DOC['accuracy']['relative'], '1', '精度±0.5 %测量值', [_PR, _WB])
    doc('accuracy_absolute_pa', DOC['accuracy']['absolute_pa'], 'Pa', '精度±1 hPa', [_PR, _WB])
    doc('measuring_temperature_k', list(DOC['measuring_temperature_k']), 'K', '允许测量温度20±1 °C', [_PR, _WB])
    doc('filling_temperature_k', list(DOC['filling_temperature_k']), 'K', '装样时样品温度15–21 °C', [_PR, _BP])
    doc('head_dimensions_m', [0.069, 0.070], 'm', '测头高69 mm、直径70 mm', [_PR, _WB])
    doc('head_mass_kg', 0.085, 'kg', '测头约85 g', [_PR, _WB])
    doc('measuring_duration_s', [d * 86400.0 for d in DOC['duration_days']], 's', '测量时长1–7天（OxiTop-i；能力参数duration_days为整天数）', [_PR, _SET])
    doc('sample_volumes_m3', [v * 1e-6 for v in VOLUMES_ML], 'm3', '样品体积7档（22.7–432 ml）', [_PR, _BP])
    doc('bod_equation', dict(EQ), '1', 'BOD = M(O2)/(R·Tm)·((Vtot − Vl)/Vl + α·Tm/T0)·Δp(O2)（M 32000 mg/mol、R 83.144 l·hPa/(mol·K)、Tm 293.15 K、Vtot 510 ml、α 0.03103；复合记录，子字段名带单位）', [_BP])
    asm('digit_pa', DIGIT_HPA * 100.0, 'Pa', '1 digit = 432 ml设置下的1 mg/l（约3.56 hPa）：由入门手册BOD公式（Vtot 510 ml、20 °C、α 0.03103）在432 ml档（手册倍数1）计算；'
        '50 digit为显示上限（手册p.29）', '计算值（非厂家直接给出的数值）；公式与输入为厂家资料，结果与手册体积表倍数相差<1.2 %', (_PR, _BP))
    doc('autotemp', dict(DOC['autotemp']), 's', 'AutoTemp：适应60 min、BOD5测试间隔30 min、最迟3 h开始', [_BP])
    doc('nth_600', dict(atu_kg_m3=5.0, drops_per_m3=20000), '1', 'NTH 600：5 g/l烯丙基硫脲（atu_kg_m3），每升样品20滴（drops_per_m3）得5 mg/l ATU', [_BP, _CAT])
    asm('nth_600_drop_volume_m3', 5e-8, 'm3', 'NTH 600每滴体积0.05 ml：由5 mg/l目标、5 g/l浓度与每升20滴推算（资料未直接给出滴体积）',
        '计算值；实际滴体积随滴瓶与操作约±20 %', (_BP, _CAT))
    doc('gga_standard_kg_m3', dict(thod=0.307, bod5_dilution=[0.17, 0.25], respirometric=[0.23, 0.29]), 'kg/m3', '葡萄糖–谷氨酸标准溶液：理论307 mg/l；稀释法210±40；WTW呼吸法检查260±30', [_BP])
    doc('pt_test', dict(DOC['pt_test']), '1', 'OxiTop PT：注射器0.5→2.0 ml；入门手册p.36全表29个海拔（−300…2500 m）的平均气压与控制值（海平面39 digit），允许±3 digit（复合记录，子字段名带单位）', [_BP])
    doc('battery', dict(DOC['battery']), '1', 'CR 2450（600 mAh）；寿命关机约3.5年、测量（灯关）约3年、灯开约1.4年；电量低后还能测7天（复合记录，子字段名带单位）', [_PR])
    doc('is6_platform', dict(DOC['stirring_platform']), '1', 'IS 6：265×181×67 mm、1.6 kg、6位、180–450 rpm、IP 30、100–240 V（复合记录，子字段名带单位）', [_IS6, _CAT])
    doc('bottle_volume_m3', BOTTLE_M3, 'm3', 'PF 600样品瓶510 ml', [_CAT, _PF, _BP])
    doc('oxygen_solubility', dict(_BK), '1', 'Benson–Krause：DO0(T)、水蒸气压u(T)、θ0', [_USGS])
    for k, unit in (('bottle_volume_sigma_m3', 'm3'), ('stir_bar_volume_m3', 'm3'), ('pt_device_volume_m3', 'm3'), ('kl_stirred_m_s', 'm/s'), ('kl_unstirred_m_s', 'm/s'),
                    ('liquid_surface_m2', 'm2'), ('nominal_rpm', '1/min'), ('heat_transfer_w_m2_k', 'W/(m2*K)'), ('bottle_outer_area_m2', 'm2'), ('glass_heat_capacity_j_k', 'J/K'),
                    ('naoh_absorption_s', 's'), ('co2_henry_mol_m3_pa', 'mol/(m3*Pa)'), ('co2_pka1', '1'), ('co2_kla_ratio', '1'), ('respiratory_quotient', '1'),
                    ('theta_carbon', '1'), ('theta_nitrification', '1'), ('nitrifier_mu_per_day', '1/d'), ('nitrifier_ks_mg_l', 'mg/l'), ('atu_ki_mg_l', 'mg/l'),
                    ('do_half_saturation_mg_l', 'mg/l'), ('ph_recommended', '1'), ('ph_tolerance', '1'), ('ph_width', '1'), ('seed_endogenous_mg_l_day_per_pct', 'mg/(l*d)'), ('chlorine_kill_mg_l', 'mg/l'),
                    ('sensor_gain_sigma', '1'), ('sensor_offset_sigma_pa', 'Pa'), ('sensor_noise_pa', 'Pa'), ('leak_rate_per_s', '1/s'), ('pm_tablet_reaction_s', 's'),
                    ('sulfite_reaction_s', 's'), ('head_internal_volume_m3', 'm3'), ('stirred_rate_factor', '1')):
        v = ENG[k]
        if unit == '1/min':
            v, unit = v / 60.0, '1/s'
        elif unit == '1/d':
            v, unit = v / 86400.0, '1/s'
        elif unit == 'mg/l':
            v = [x / 1000.0 for x in v] if isinstance(v, list) else v / 1000.0
            unit = 'kg/m3'
        elif unit == 'mg/(l*d)':
            v, unit = v / 1000.0 / 86400.0, 'kg/(m3*s)'
        asm(PARAM_RENAME.get(k, k), v, unit, ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明）')
    asm('seed_lag_s', {k: (None if v is None else v * 86400.0) for k, v in ENG['seed_lag_days'].items()}, 's', ENG_NOTE['seed_lag_days'], '工程值')
    asm('pseudo_components', {k: dict(thod=v['thod_nh4_mg_mg'], f_ult=v['kinetics']['f_ult'], k20_per_s=v['kinetics']['k20_per_day'] / 86400.0) for k, v in PSEUDO.items()}, '1',
        '污水拟组分（易降解、慢降解、惰性COD）的速率', '工程值')
    asm('nitrification_capacity_kg_m3_s', 10.0 / 1000.0 / 86400.0, 'kg/(m3*s)', '硝化菌完全活化时的硝化能力（10 mg N/(l·d)）', '工程值')
    asm('led_on_time_s', 0.5, 's', '状态灯每次闪亮的时长（厂家只给约每5 s闪一次）', '工程值')
    asm('incubator_cycle', dict(amplitude_k=0.3, period_s=1800.0, door_open_tau_s=120.0, door_closed_tau_s=600.0), '1', '恒温箱温度周期波动与开门响应', '工程值（厂家：20.0±0.5 °C）', (_BP, _SIM))
    asm('head_lift_m', HEAD_LIFT_M, 'm', '场景中测头从瓶上取下时的抬起距离', '工程值')
    return P


PARAM_RENAME = {'nominal_rpm': 'nominal_stir_rate', 'nitrifier_mu_per_day': 'nitrifier_mu', 'nitrifier_ks_mg_l': 'nitrifier_ks', 'atu_ki_mg_l': 'atu_ki',
                'do_half_saturation_mg_l': 'do_half_saturation', 'seed_endogenous_mg_l_day_per_pct': 'seed_endogenous_rate_per_percent_seed',
                'chlorine_kill_mg_l': 'chlorine_kill_concentration'}   # contract names follow the SI unit of the exported value
SLOT_REFERENCE = {
    **{f'display.{h}_value': f'测头面板显示值，单位随测头设置（见heads.status.{h}.unit：mg/l、ΔhPa、hPa或digit），不是SI换算值' for h in HEADS},
    'heads.stored_values': '每个存储值带unit字段（测头当前单位：mg/l、ΔhPa、hPa或digit）与SI的delta_p_pa（Pa）；超过50 digit标over_range但仍给数值，压力超出500–1250 hPa时为空',
    'incubator.temperature_k': '热力学温度（ITS-90）；恒温箱显示读数（0.1 K分辨率）', 'truth.incubator_air_k': '热力学温度（ITS-90）',
    'platform.stir_velocity_rad_s': '搅拌子绕竖直轴的角速度（300 rpm ≈ 31.4 rad/s）',
}


def export_wtw_oxitopi_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'WtwOxitopiSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.wtw_oxitopi.WtwOxitopiSession.invoke',
                                              invocation=f'session.invoke(instance_id="oxitop_i_is6_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + _foreign_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='WtwOxitopiRuntime.invoke / WtwOxitopiSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（温度、压力与降解在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['inductive_platform'] + [f'bottle_{i}' for i in range(1, 7)] + [f'head_{i}' for i in range(1, 7)] + [f'sleeve_{i}' for i in range(1, 7)]
                      + [f'magnet_rotor_{i}' for i in range(1, 7)],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='呼吸测压BOD：密闭瓶内气相（O2、惰性气体、CO2、水蒸气）与液相（溶解氧、可降解物质、氨氮、溶解无机碳）的质量与能量平衡；生物耗氧使O2从气相溶入液相，'
                   'CO2被NaOH吸收，瓶内压力下降；测头按WTW公式把压降换算为BOD并每天存值',
        equations=['p = (n_O2 + n_inert + n_CO2) R T / V_g + p_w(T); V_g = V_bottle - V_l - V_bar + V_head',
                   'DO_eq = K_H(T) p_O2, K_H = DO0_BK(T) / (0.20946 (101325 - p_w)); exact two-compartment relaxation with kLa = kL A / V_l',
                   'dL_i/dt = -k20_i theta^(T-20) a_i L_i DO/(0.2 + DO) f_pH f_tox, L_i(0) = c_i ThOD_i f_ult,i; nitrification 4.57 mg O2/mg N, ATU 1/(1 + (ATU/0.05)^2)',
                   'CO2 = RQ x O2 (mol); dDIC/dt from respiration, CO2(aq) = DIC / (1 + 10^(pH - 6.38)), gas exchange 0.9 kLa, NaOH: dn/dt = -n_CO2 / 900 s while capacity lasts',
                   'dT/dt = (T_air - T) h A / C; T_air -> T_set + 0.3 sin(2 pi t / 1800) (door closed, 600 s) or T_room (door open, 120 s)',
                   'head: p_meas = p (1 + g) + o + noise; AutoTemp: zero after 60 min, test every 30 min x duration / 5, start when the pressure no longer rises (latest 3 h);',
                   'BOD = 32000 / (83.144 x 293.15) x ((510 - V_l) / V_l + 0.03103 x 293.15 / 273.15) x dp(hPa); digit = BOD at 432 ml (about 3.56 hPa); over range above 50 digits'],
        parameters=_params(), source_ids=[_SIM, _WB, _PR, _M4, _BP, _CAT, _IS6, _SET, _PF, _USGS, _PUB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_oxitop_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('wtw_oxitopi', caps, slots, {'respirometric_bod_process': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'respirometric_bod_process': mechanism})
