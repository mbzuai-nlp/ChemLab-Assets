"""CHEM-172 SEAL Analytical AA500 segmented continuous flow analyzer (4 nutrient channels + AS2 XY sampler): process runtime.

Device (SEAL AA500 product page and brochure, OmniProcess specification list): up to six channels (this configuration: four on MT508 multi-test
manifolds - Ch1 nitrate + nitrite with copper-cadmium coil reduction, Ch2 ammonia (phenol / Berthelot), Ch3 orthophosphate (molybdenum blue),
Ch4 silicate (silicomolybdenum blue)); peristaltic pump with 34 tube positions, motorised platen (engage / release), multi-speed motor; reagent
valves; solid-state heating baths; dual-beam LED digital photometers (LED modules 250-880 nm, flow cells 10-50 mm, bubble-through cuvette, online
debubbling); up to 120 samples/h per channel; automatic start-up / shutdown, auto standards and dilution, reagent level sensing; AS2 XY sampler
(440 x 470 mm) with two sample racks, a standard rack, a wash station and a dilutor.  The PC with the AACE software is host; its sequence, peak
processing and calibration functions are implemented here as functional equivalents.

Process model (fixed 0.5 s grid anchored at t = 0, state held between grid points): each channel pumps a set of flow-rated pump tubes (sample, air, reagents, dilution
water; the silicate set follows the EPA 366.0 manifold) with tube wear; the sample fraction of the stream is q_sample / q_liquid.  The probe draws
sample (sample time) and wash water (wash time) from the sampler; the analyte-equivalent concentration entering the manifold (nitrate x cadmium
efficiency + nitrite for NOx; ammonium; orthophosphate + arsenate - iron precipitation loss; silicate) and the matrix load (colour, turbidity,
salinity refractive-index blank) travel to the detector with the transport delay (manifold volume / total flow) and are dispersed by three mixing
stages in series.  The colour forms only where the reagents are present (reagent valve and bottle level, same delay and dispersion) to the
fraction 1 - exp(-k(T) t_res) (Arrhenius in the heating-bath temperature).  Detector: Beer-Lambert A = eps L c_chromophore + reagent blank + matrix
+ LED warm-up drift (dual-beam compensated) + baseline drift + noise; air bubbles through the cuvette in the raw trace (removed by online
debubbling).  Cadmium efficiency falls with the nitrate reduced, with air and with residual chlorine; activation restores it.  AACE equivalent:
sequence of cups, peak height in the end window of the sample time at the transport delay, baseline correction, carry-over correction,
drift-standard correction, linear / quadratic calibration, QC, over-range auto-dilution, cadmium efficiency check."""
import collections
import copy
import json
import math
import re
import zlib
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.seal_aa500"
CATALOG_ID = "CHEM-172"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t4_measure/seal_aa500'
LIBS = {'methods': 'method_library.json', 'substances': 'substance_library.json', 'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- libraries
_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def methods():
    return _lib('methods')['methods']


def substances():
    return _lib('substances')['entries']


def instrument():
    return _lib('instrument')


DOC = instrument()['documented']
ENG = instrument()['engineering']
ENG_NOTE = instrument()['engineering_note_zh']
DT = 0.5                                     # process grid (s)
R_GAS = 8.314462618
MW = dict(N=14.007, P=30.974, Si=28.0855, As=74.922, Fe=55.845, Cl=35.45)
CHANNELS = ['ch1', 'ch2', 'ch3', 'ch4']
CHEMS = ['nitrate_nitrite', 'nitrite', 'ammonia', 'phosphate', 'silicate']
DEFAULT_METHOD = dict(ch1='MT508_A026_nitrate_nitrite', ch2='MT508_A025_ammonia', ch3='MT508_A029_phosphate', ch4='MT508_A030_silicate')
INSTALLED_MANIFOLD = dict(ch1='MT508', ch2='MT508', ch3='MT508', ch4='MT508')
BATHS = {'ch2', 'ch3'}                        # heating baths on the ammonia and phosphate channels
LED_MODULES_M = [540e-9, 660e-9, 820e-9, 880e-9]
DEFAULT_LED = dict(ch1=540e-9, ch2=660e-9, ch3=880e-9, ch4=820e-9)
CELLS_M = [0.010, 0.015, 0.020, 0.030, 0.050]
SPECIES = ['nitrate_n', 'nitrite_n', 'ammonium_n', 'phosphate_p', 'silicate_si', 'arsenate_as', 'iron', 'chlorine', 'colour', 'turbidity', 'salinity']
SPECIES_UNIT = dict(nitrate_n='kg/m3', nitrite_n='kg/m3', ammonium_n='kg/m3', phosphate_p='kg/m3', silicate_si='kg/m3', arsenate_as='kg/m3', iron='kg/m3', chlorine='kg/m3',
                    colour='kg/m3', turbidity='NTU', salinity='1e-3')
ANALYTE_OF = dict(nitrate_nitrite='N', nitrite='N', ammonia='N', phosphate='P', silicate='Si')
REAGENTS = dict(   # per chemistry: reagent -> pump tube; plus sample tube, air tube and optional dilution water (set by the range design)
    nitrate_nitrite=dict(imidazole_buffer='red_red', colour_reagent='black_black'),
    nitrite=dict(colour_reagent='black_black', buffer_water='red_red'),
    ammonia=dict(complexing_reagent='orange_orange', alkaline_phenol='black_black', hypochlorite_nitroprusside='orange_yellow'),
    phosphate=dict(molybdate_antimony='black_black', ascorbic_acid='orange_white'),
    silicate=dict(molybdate='epa_0.41', oxalic_acid='epa_0.32', ascorbic_acid='epa_0.41'))
AIR_TUBE = dict(nitrate_nitrite='black_black', nitrite='black_black', ammonia='black_black', phosphate='black_black', silicate='epa_0.25')
KITS = ['nitrate_stock', 'nitrite_stock', 'ammonium_stock', 'phosphate_stock', 'silicate_stock', 'reagents_nitrate_nitrite', 'reagents_nitrite', 'reagents_ammonia',
        'reagents_phosphate', 'reagents_silicate', 'wash_water', 'cadmium_activation']
STOCKS = dict(nitrate_stock=('potassium_nitrate', 'nitrate_n', 7.218), nitrite_stock=('potassium_nitrite', 'nitrite_n', 6.072),
              ammonium_stock=('ammonium_chloride', 'ammonium_n', 3.819), phosphate_stock=('potassium_dihydrogen_phosphate', 'phosphate_p', 0.4393),
              silicate_stock=('sodium_hexafluorosilicate', 'silicate_si', 0.6696))
STOCK_NOMINAL = dict(nitrate_stock=1.0, nitrite_stock=1.0, ammonium_stock=1.0, phosphate_stock=0.1, silicate_stock=0.1)   # kg/m3 of the element (EPA labels)
CUP_TYPES = ['baseline', 'calibrant', 'drift', 'sample', 'qc', 'carryover_high', 'carryover_low', 'blank', 'nitrite_check', 'nitrate_check']
FAULTS = ['reagent_empty', 'led_failure', 'bath_failure', 'pump_tube_worn', 'cadmium_air', 'leak', 'probe_blocked']
MAX_TRACE = 120000
MAX_EVENTS_LOG = 1500
MAX_ITEMS = 300
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


def tube_flow(name):
    """Flow-rated pump tube (m3/s): the colour series, or an EPA 366.0 manifold flow written 'epa_<ml/min>'."""
    if name.startswith('epa_'):
        return float(name[4:]) * 1e-6 / 60.0
    return ENG['pump_tube_series_m3_s'][name]


TUBE_SERIES = sorted(ENG['pump_tube_series_m3_s'].items(), key=lambda kv: kv[1])


def design_manifold(method_id, cell_m):
    """Tube set of a method: the reagent / air tubes of its chemistry and a sample tube (+ dilution water) chosen so that the top of the method's
    range gives about 0.8 AU in the installed flow cell (manifold design rule, engineering)."""
    m = methods()[method_id]
    chem = m['analyte_key']
    c = m['chemistry']
    eps = c['molar_absorptivity_m2_mol']
    top = m['range_highest']['si'] if 'range_highest' in m else None
    rng = method_range(method_id)
    top_mol = rng['high_mol']
    reag = {r: tube_flow(t) for r, t in REAGENTS[chem].items()}
    q_reag = sum(reag.values())
    if True:                                                    # silicate keeps the EPA 366.0 reagent / air flows; all channels follow the design rule
        f_target = 0.8 / max(eps * cell_m * top_mol, 1e-12)
        best = None
        for name, q in TUBE_SERIES:
            for dname, qd in [('none', 0.0)] + TUBE_SERIES:
                f = q / (q + q_reag + qd)
                err = abs(math.log(max(f, 1e-9) / min(f_target, 0.6)))
                if best is None or err < best[0] - 1e-12:
                    best = (err, name, q, dname, qd)
        _, sname, q_s, dname, q_dil = best
    q_liq = q_s + q_reag + q_dil
    del top
    return dict(chemistry=chem, sample_m3_s=q_s, reagents_m3_s=reag, dilution_m3_s=q_dil, air_m3_s=tube_flow(AIR_TUBE[chem]), liquid_m3_s=q_liq,
                sample_fraction=q_s / q_liq, cell_m=cell_m)


def method_range(method_id):
    """Range of a method in mol/m3 of the analyte element (lowest and highest documented range)."""
    m = methods()[method_id]
    el = ANALYTE_OF.get(m['analyte_key'])
    out = {}
    for k, r in (('low', m['range_lowest']['si']), ('high', m['range_highest']['si'])):
        if r['unit'] == 'mol/m3':
            out[k + '_mol'] = r['high']
        else:
            mw = {'SiO2': 60.083}.get(r.get('basis'), MW.get(el, 1.0))
            out[k + '_mol'] = r['high'] / (mw * 1e-3)
    return out


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


# ---------------------------------------------------------------- sampler layout (shared with the geometry)
RACK_COLS, RACK_ROWS = 5, 12                 # AS2 racks: 60 positions (5 x 12), 5 ml tubes
RACK_PITCH = 0.018
STD_COLS, STD_ROWS = 2, 11                   # 22-position standard rack
PROBE_TRAVEL_X, PROBE_TRAVEL_Y, PROBE_TRAVEL_Z = 0.36, 0.30, ENG['probe_travel_m']
RACKS = ['A', 'B', 'S']


def rack_position_xy(rack, pos):
    """Probe XY travel (m from the home / wash position) of a rack position (1-based, filled row by row from the front)."""
    if rack == 'S':
        col, row = (pos - 1) % STD_COLS, (pos - 1) // STD_COLS
        return 0.035 + col * RACK_PITCH, 0.035 + row * RACK_PITCH
    col, row = (pos - 1) % RACK_COLS, (pos - 1) // RACK_COLS
    x0 = 0.115 if rack == 'A' else 0.235
    return x0 + col * RACK_PITCH, 0.035 + row * RACK_PITCH * 1.15


def rack_size(rack):
    return STD_COLS * STD_ROWS if rack == 'S' else RACK_COLS * RACK_ROWS


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.:+-]+$')
CH = enum(CHANNELS)
COMPOSITION = obj({s: number(SPECIES_UNIT[s], 0.0, 1000.0 if s in ('turbidity', 'salinity') else 0.5) for s in SPECIES}, optional=tuple(SPECIES))
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 288.15, 308.15), mains_voltage_v=number('V', 0.0, 260.0), noise_seed=integer(0, 2 ** 31 - 1)))
CUP = obj(dict(rack=enum(RACKS), position=integer(1, 60), type=enum(CUP_TYPES), nominal_kg_m3=obj({c: number('kg/m3', 0.0, 1.0) for c in CHANNELS}, optional=tuple(CHANNELS))),
          optional=('nominal_kg_m3',))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'pump', 'channel', 'sampler', 'run', 'warning', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
RESULT_ROW = obj(dict(cup=INT, rack=TEXT, position=INT, type=TEXT, channel=TEXT, peak_au=nullable(number('1')), concentration_kg_m3=nullable(number('kg/m3')),
                      dilution=number('1'), flags=array(TEXT, 0, 8)))
_COMP = ['pump_module', 'chemistry_module', 'detector_module', 'sampler_base']

cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['pump_module'], '宿主声明实验室条件：室温、电源电压（220–240 V）与噪声种子（只在首次声明时生效）',
    'evaluator', 'simulation')
cap('sample.provide', obj(dict(sample_id=SID, volume_m3=number('m3', 1e-7, 0.01), composition=COMPOSITION)), obj(dict(sample_id=TEXT, volume_m3=number('m3'))),
    dict(sample_id='river_1', volume_m3=5e-5, composition=dict(nitrate_n=0.0018, ammonium_n=0.0002, phosphate_p=0.00012, silicate_si=0.0035)), ['sampler_base'],
    '评测者提供真实水样（组成对agent隐藏：硝酸盐、亚硝酸盐、氨、磷酸盐、硅酸盐、砷酸盐、铁、余氯、色度、浊度、盐度）', 'evaluator', 'simulation')
cap('kit.provide', obj(dict(kit=enum(KITS), volume_m3=number('m3', 1e-6, 0.005)), optional=('volume_m3',)), obj(dict(items=array(TEXT, 0, 8))), dict(kit='nitrate_stock'),
    ['chemistry_module'], '提供EPA配方储备液（KNO3、KNO2、NH4Cl、KH2PO4、Na2SiF6）、各化学试剂组、清洗水与镉活化液', 'evaluator', 'simulation')
cap('standard.prepare', obj(dict(standard_id=SID, volume_m3=number('m3', 1e-6, 0.002), components=array(obj(dict(source_id=SID, volume_m3=number('m3', 1e-9, 0.002))), 1, 6))),
    obj(dict(standard_id=TEXT, volume_m3=number('m3'), nominal_kg_m3=dict(type='object'))), dict(standard_id='mix_1', volume_m3=1e-4,
                                                                                                    components=[dict(source_id='nitrate_stock', volume_m3=1e-6)]),
    ['sampler_base'], '用储备液配制混合标准（容量瓶稀释到体积，余量为清洗水/去离子水）；返回按标签值计算的标称浓度', 'agent', 'robot_skill')
cap('sampler.load', obj(dict(rack=enum(RACKS), position=integer(1, 60), item_id=SID, volume_m3=number('m3', 1e-7, 1.5e-5))), obj(dict(rack=TEXT, position=INT)),
    dict(rack='A', position=1, item_id='river_1', volume_m3=5e-6), ['sample_rack_a', 'sample_rack_b', 'standard_rack'], '把样品或标准倒入进样管并放到进样器架位（A、B样品架各60位，S标准架22位；5 ml管）',
    'agent', 'robot_skill')
cap('sampler.unload', obj(dict(rack=enum(RACKS), position=integer(1, 60))), obj(dict(rack=TEXT, position=INT, removed_m3=number('m3'))), dict(rack='A', position=1),
    ['sample_rack_a', 'sample_rack_b', 'standard_rack'], '取下进样管（剩余液体倒入废液）', 'agent', 'robot_skill')
cap('sampler.move', obj(dict(target=enum(['home', 'wash', 'position']), rack=enum(RACKS), position=integer(1, 60), probe_down=BOOL), optional=('rack', 'position', 'probe_down')),
    obj(dict(x_m=number('m'), y_m=number('m'), z_m=number('m'))), dict(target='wash', probe_down=True), ['sampler_arm', 'sampler_probe'],
    '手动移动进样针（回原位、到清洗槽或到某架位，可放下进样针吸液）', 'agent', 'device_command')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['pump_module'], '主机与进样器通/断电（等价于后部电源开关）', 'agent', 'device_command')
cap('power.press_switch', obj({}), obj(dict(on=BOOL)), {}, ['power_switch'], '按一下电源开关（翻转通断）', 'agent', 'device_command')
cap('system.startup', obj({}), obj(dict(state=TEXT)), {}, ['pump_module'], 'AACE自动开机程序：合上压板、泵正常速、阀切清洗、开LED与加热浴，10 min后阀切试剂，基线稳定后就绪', 'agent', 'device_command')
cap('system.shutdown', obj({}), obj(dict(state=TEXT)), {}, ['pump_module'], 'AACE自动关机程序：阀切清洗、泵快速冲洗15 min、关LED与加热浴、停泵、抬起压板、升起进样针', 'agent', 'device_command')
cap('pump.set', obj(dict(running=BOOL, speed=enum(['normal', 'fast'])), optional=('speed',)), obj(dict(running=BOOL, speed=TEXT)), dict(running=True, speed='normal'),
    ['pump_module', 'pump_rotor'], '蠕动泵启停与速度（正常/快速冲洗约3倍）', 'agent', 'device_command')
cap('platen.set', obj(dict(engaged=BOOL)), obj(dict(engaged=BOOL)), dict(engaged=True), ['pump_platen'], '电动泵压板合上/抬起（抬起时泵管不受压、不流动）', 'agent', 'device_command')
cap('pump.lid_set', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['pump_lid'], '打开/关上泵盖（换泵管时）', 'agent', 'robot_skill')
cap('valve.set', obj(dict(channel=CH, position=enum(['reagent', 'wash']))), obj(dict(channel=TEXT, position=TEXT)), dict(channel='ch1', position='reagent'),
    ['chemistry_module'], '通道试剂阀：试剂或清洗水', 'agent', 'device_command')
cap('lamp.set', obj(dict(channel=CH, on=BOOL)), obj(dict(channel=TEXT, on=BOOL)), dict(channel='ch1', on=True), ['detector_ch1', 'detector_ch2', 'detector_ch3', 'detector_ch4'],
    '开/关通道检测器LED（开后约15 min预热）', 'agent', 'device_command')
cap('bath.set', obj(dict(channel=enum(sorted(BATHS)), temperature_k=nullable(number('K', 298.15, 333.15)))), obj(dict(channel=TEXT, temperature_k=nullable(number('K')))),
    dict(channel='ch3', temperature_k=310.15), ['heating_bath_ch2', 'heating_bath_ch3'], '设定加热浴温度（氨与磷酸盐通道，37 °C；null关闭）', 'agent', 'device_command')
cap('cover.set', obj(dict(channel=CH, open=BOOL)), obj(dict(channel=TEXT, open=BOOL)), dict(channel='ch1', open=True),
    ['detector_cover_ch1', 'detector_cover_ch2', 'detector_cover_ch3', 'detector_cover_ch4'], '打开/关上检测器盖（换LED模块或流通池）', 'agent', 'robot_skill')
LED_SCHEMA = {'type': 'number', 'x-unit': 'm', 'enum': LED_MODULES_M}
cap('led.install', obj(dict(channel=CH, wavelength_m=LED_SCHEMA)), obj(dict(channel=TEXT, wavelength_m=number('m'))),
    dict(channel='ch1', wavelength_m=540e-9), ['detector_ch1', 'detector_ch2', 'detector_ch3', 'detector_ch4'], '更换检测器LED波长模块（540、660、820、880 nm；须开盖、关灯）', 'agent', 'robot_skill')
cap('flowcell.install', obj(dict(channel=CH, path_m={'type': 'number', 'x-unit': 'm', 'enum': CELLS_M})), obj(dict(channel=TEXT, path_m=number('m'))),
    dict(channel='ch3', path_m=0.05), ['detector_ch1', 'detector_ch2', 'detector_ch3', 'detector_ch4'], '更换流通池（10–50 mm；须开盖、关灯、停泵）', 'agent', 'robot_skill')
cap('pumptube.install', obj(dict(channel=CH, method_id=SID)), obj(dict(channel=TEXT, sample_m3_s=number('m3/s'), sample_fraction=number('1'))),
    dict(channel='ch3', method_id='MT508_A029_phosphate'), ['pump_module'], '为所选方法装该通道的流量标定泵管组（样品、空气、试剂、稀释水；须抬起压板、开泵盖）', 'agent', 'robot_skill')
cap('channel.configure', obj(dict(channel=CH, method_id=SID)), obj(dict(channel=TEXT, method_id=TEXT, range_kg_m3=array(number('kg/m3'), 2, 2))),
    dict(channel='ch1', method_id='MT508_A026_nitrate_nitrite'), ['chemistry_module'], 'AACE中为通道选方法（须与通道的流路、化学、泵管与LED一致）', 'agent', 'device_command')
cap('cadmium.activate', obj(dict(channel=CH)), obj(dict(channel=TEXT, efficiency_note=TEXT)), dict(channel='ch1'), ['cadmium_coil'],
    '用硫酸铜/盐酸活化铜镉还原盘管（停泵或阀在清洗位；须有镉活化液）', 'agent', 'robot_skill')
cap('reagent.load', obj(dict(channel=CH, reagent=SID, volume_m3=number('m3', 1e-6, 1e-3))), obj(dict(channel=TEXT, reagent=TEXT, volume_m3=number('m3'))),
    dict(channel='ch1', reagent='colour_reagent', volume_m3=2.5e-4), ['chemistry_module'], '把试剂倒进通道试剂瓶（需先提供该化学的试剂组）', 'agent', 'robot_skill')
cap('run.start', obj(dict(cups=array(CUP, 1, 200), sample_s=number('s', 10.0, 300.0), wash_s=number('s', 5.0, 300.0), auto_dilute=BOOL, dilution_factor=number('1', 2.0, 100.0)),
                     optional=('auto_dilute', 'dilution_factor')), obj(dict(run_id=TEXT, cups=INT, throughput_per_s=number('1/s'), estimated_s=number('s'))),
    dict(cups=[dict(rack='S', position=1, type='baseline'), dict(rack='A', position=1, type='sample')], sample_s=40.0, wash_s=20.0), ['sampler_base', 'sampler_probe'],
    'AACE开始运行：按杯序列进样（进样时间/清洗时间决定通量，最高120样/h），处理峰高、校准、漂移、携带、QC与超量程自动稀释', 'agent', 'device_command')
cap('run.stop', obj({}), obj(dict(run_id=nullable(TEXT), state=TEXT)), {}, ['sampler_base'], '停止当前运行（已完成的杯保留结果）', 'agent', 'device_command')
cap('data.read_trace', obj(dict(channel=CH, kind=enum(['debubbled', 'raw']), max_points=integer(10, 20000)), optional=('kind', 'max_points')),
    obj(dict(channel=TEXT, time_s=array(number('s'), 0, 20000), absorbance=array(nullable(number('1')), 0, 20000))), dict(channel='ch1'),
    ['detector_ch1', 'detector_ch2', 'detector_ch3', 'detector_ch4'], '读取通道的连续响应峰曲线（在线去气泡或原始含气泡尖峰）', 'agent', 'query')
cap('data.results', obj({}), obj(dict(run_id=nullable(TEXT), state=TEXT, calibration=dict(type='object'), results=array(RESULT_ROW, 0, 4000), qc=array(dict(type='object'), 0, 400))),
    {}, ['pump_module'], '读取最近一次运行的结果：每杯每通道的峰高与浓度、校准曲线、QC、镉还原效率与标记', 'agent', 'query')
cap('data.recalculate', obj(dict(fit=enum(['linear', 'quadratic', 'auto']), carryover_correction=BOOL, drift_correction=BOOL)),
    obj(dict(run_id=nullable(TEXT), calibration=dict(type='object'))), dict(fit='auto', carryover_correction=True, drift_correction=True), ['pump_module'],
    'AACE重新计算：拟合阶次、携带校正、漂移校正', 'agent', 'analysis')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=INT)), dict(since_s=0.0), ['pump_module'], '读取事件记录', 'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(name=TEXT, file=TEXT, entries=INT)), 0, 8), methods=array(TEXT, 0, 200))), {}, ['pump_module'],
    '模板库来源与方法目录', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(state=dict(type='object'))), {}, ['chemistry_module'], '评测者读取真值：各通道流量、样品分数、到达检测器的分析物与显色、镉效率、加热浴、样品与标准的真实组成',
    'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=dict(type='object'))), {}, ['pump_module'], '账目：样品、试剂、清洗水与废液体积，镉还原的硝酸盐量', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL, channel=CH), optional=('channel',)), obj(dict(faults=array(TEXT, 0, 32))), dict(fault='reagent_empty', active=True, channel='ch2'),
    ['pump_module'], '故障注入：试剂用完、LED失效、加热浴失控、泵管老化、气泡进入镉盘管、泄漏（泵停）、进样针堵塞', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), ['pump_module'], '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'distillation.run': '在线蒸馏模块（氰化物、酚）不在所选配置中。', 'uv_digestion.run': '在线紫外消解（总氮、总磷，MT517/MT533）不在所选配置中。',
    'dialysis.run': '透析与气体扩散模块不在所选配置中。', 'fluorometer.read': '荧光、火焰光度与电化学检测器不在所选配置中。',
    'lims.export': 'LIMS导出由宿主电脑的AACE软件完成（本资产只提供结果读取）。',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, ['pump_module'], v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


_PN = lambda u: nullable(number(u))                                             # noqa: E731
slot('clock.time_s', number('s'), 's', 'derived', 'pump_module', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'pump_module', '实验室条件是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'pump_module', '主机与进样器是否通电')
slot('system.state', enum(['off', 'idle', 'starting', 'ready', 'running', 'shutting_down']), '1', 'derived', 'pump_module', 'AACE系统状态（关、空闲、开机程序中、就绪、运行、关机程序中）')
slot('pump.running', BOOL, '1', 'setpoint', 'pump_module', '泵是否运转')
slot('pump.speed', enum(['normal', 'fast']), '1', 'setpoint', 'pump_module', '泵速度档')
slot('pump.platen_engaged', BOOL, '1', 'setpoint', 'pump_platen', '压板是否合上')
slot('pump.platen_position_m', number('m'), 'm', 'derived', 'pump_platen', '压板压下行程（0抬起，0.004 m合上；电动约4 s）')
slot('pump.rotor_velocity_rad_s', number('rad/s'), 'rad/s', 'derived', 'pump_rotor', '泵滚轮转速（正常约1.05 rad/s，快速约3倍；停泵或压板抬起时为0）')
slot('pump.lid_angle_rad', number('rad'), 'rad', 'setpoint', 'pump_lid', '泵盖开角（0关，1.2 rad开）')
slot('channels.status', dict(type='object'), '1', 'derived', 'chemistry_module', '各通道：方法、化学、阀位、LED波长与开关、预热、流通池、泵管组与样品分数、就绪')
slot('baths.temperature_k', dict(type='object'), 'K', 'sensor', 'heating_bath_ch2', '加热浴温度读数（ch2、ch3）')
slot('detector.absorbance', dict(type='object'), '1', 'sensor', 'detector_ch1', '各通道检测器当前吸光度（去气泡后；灯关为空）')
slot('reagents.levels_m3', dict(type='object'), 'm3', 'sensor', 'chemistry_module', '各通道试剂瓶液位（液位检测）')
slot('sampler.contents', dict(type='object'), '1', 'derived', 'sampler_base', '进样器各架位的管（样品/标准标识与体积）')
slot('sampler.probe_x_m', number('m'), 'm', 'derived', 'sampler_arm', '进样臂X行程（自原位/清洗槽起）')
slot('sampler.probe_y_m', number('m'), 'm', 'derived', 'sampler_carriage', '进样臂Y行程（自原位起）')
slot('sampler.probe_z_m', number('m'), 'm', 'derived', 'sampler_probe', '进样针下降深度（0升起，0.10 m插到管底/清洗槽）')
slot('run.status', dict(type='object'), '1', 'derived', 'sampler_base', '运行状态：运行号、当前杯、已完成杯数、预计剩余时间')
slot('samples.available', array(TEXT, 0, MAX_ITEMS), '1', 'derived', 'sampler_base', '可用的样品、标准、储备液与试剂组')
slot('warnings.active', array(TEXT, 0, 32), '1', 'derived', 'pump_module', '当前提示（试剂液位低、基线未稳、超量程、镉效率低、泄漏等）')
slot('lamp.power', BOOL, '1', 'derived', 'power_lamp', '主机电源指示灯（通电时亮）')
for _i, _c in enumerate(CHANNELS, start=1):
    slot(f'cover.{_c}_angle_rad', number('rad'), 'rad', 'setpoint', f'detector_cover_{_c}', f'通道{_i}检测器盖开角（0关，1.3 rad开）')
slot('truth.streams', dict(type='object'), '1', 'process_truth', 'chemistry_module', '各通道真值：流量、样品分数、到达检测器的分析物当量、试剂到达比例、显色比例、镉效率、浴温、真实吸光度', 'evaluator')
slot('truth.items', dict(type='object'), '1', 'process_truth', 'sampler_base', '样品、标准、储备液的真实组成与体积', 'evaluator')
slot('truth.faults', array(TEXT, 0, 32), '1', 'process_truth', 'pump_module', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'pump.platen_position_m', 'pump.rotor_velocity_rad_s', 'pump.lid_angle_rad', 'sampler.probe_x_m', 'sampler.probe_y_m', 'sampler.probe_z_m', 'lamp.power'} | \
    {f'cover.{c}_angle_rad' for c in CHANNELS}


# ---------------------------------------------------------------- runtime
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


def _blank_comp():
    return {s: 0.0 for s in SPECIES}


class SealAa500Runtime:
    PLATEN_TRAVEL = 0.004
    LID_OPEN = 1.2
    COVER_OPEN = 1.3
    ROTOR_NORMAL = 2 * math.pi * 10.0 / 60.0          # 10 rpm roller head at normal speed (engineering)
    STARTUP_WASH_S = 600.0
    SHUTDOWN_RINSE_S = 900.0

    def __init__(self, instance_id='seal_aa500_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0                                  # time of the last integration grid point (state is held between grid points)
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.powered = False
        self.state = 'off'
        self.program = None                                # ('startup'|'shutdown', t_start)
        self.pump = dict(running=False, speed='normal', platen=False, platen_pos=0.0, lid=0.0, hours=0.0)
        self.ch = {c: self._new_channel(c) for c in CHANNELS}
        self.items = {}
        self.racks = {}
        self.probe = dict(x=0.0, y=0.0, z=0.0, tx=0.0, ty=0.0, tz=0.0, at='wash')
        self.run = None
        self.last_run = None
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.warnings = {}
        self.revision = 0
        self.led = dict(sample_used_m3=_Sum(), wash_used_m3=_Sum(), waste_m3=_Sum(), reagent_used_m3=_Sum(), nitrate_reduced_mol=_Sum())
        self._principals, self._requests, self._owners, self._caller = {}, {}, {}, None
        self._traces = {c: [] for c in CHANNELS}
        self._hist = {c: collections.deque() for c in CHANNELS}

    def _new_channel(self, c):
        m = DEFAULT_METHOD[c]
        chem = methods()[m]['analyte_key']
        return dict(method=m, chem=chem, led_m=DEFAULT_LED[c], led_on=False, led_since=None, cell_m=0.010, tubes=design_manifold(m, 0.010), tubes_for=m,
                    valve='wash', cover=0.0, bath_set=None, bath_T=None, cd_eff=ENG['cadmium_initial_efficiency'] if chem == 'nitrate_nitrite' else None,
                    cd_reduced=0.0, reagents={r: 0.0 for r in REAGENTS[chem]}, stages=[[0.0, 0.0, 0.0] for _ in range(ENG['dispersion_stages'])],
                    wear=1.0, drift=0.0, ou=0.0, A_true=None, A_meas=None)

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
        return SealAa500Session(self, name)

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

    def _need_power(self):
        self._need_plant()
        if not self.powered:
            raise ActionRejected('POWER_OFF')

    def _fault(self, f, c=''):
        return (f, c) in self.faults

    # ---- flows
    def _flowing(self):
        return self.powered and self.pump['running'] and self.pump['platen_pos'] >= self.PLATEN_TRAVEL - 1e-9 and not self._fault('leak')

    def _speed(self):
        return 3.0 if self.pump['speed'] == 'fast' else 1.0

    def _flows(self, c):
        """Actual flows (m3/s) of a channel: tube set x pump speed x wear (sample tube wears faster - engineering)."""
        ch = self.ch[c]
        tb = ch['tubes']
        if tb is None or not self._flowing():
            return None
        k = self._speed()
        worn = (0.85 if self._fault('pump_tube_worn', c) else 1.0)
        wear_s = max(0.5, 1.0 - 1.4 * ENG['pump_tube_wear_per_h'] * self.pump['hours']) * worn
        wear_r = max(0.5, 1.0 - ENG['pump_tube_wear_per_h'] * self.pump['hours'])
        q_s = tb['sample_m3_s'] * k * wear_s
        q_r = sum(tb['reagents_m3_s'].values()) * k * wear_r
        q_d = tb['dilution_m3_s'] * k * wear_r
        q_a = tb['air_m3_s'] * k * wear_r
        return dict(sample=q_s, reagents=q_r, dilution=q_d, air=q_a, liquid=q_s + q_r + q_d, fraction=q_s / (q_s + q_r + q_d))

    def _delay_s(self, fl):
        return (ENG['transport_volume_m3'] + ENG['manifold_volume_m3']) / max(fl['liquid'] + fl['air'], 1e-12)

    def _residence_s(self, fl):
        return ENG['manifold_volume_m3'] / max(fl['liquid'] + fl['air'], 1e-12)

    def _k_rate(self, c, T):
        chem = methods()[self.ch[c]['method']]['chemistry']
        T_ref = chem['bath_temperature_k'] or 295.15
        return chem['k_ref_per_s'] * math.exp(-ENG['activation_energy_j_mol'] / R_GAS * (1.0 / T - 1.0 / T_ref))

    # ---- probe input
    def _probe_input(self):
        """Composition entering the sample lines and whether it is air: the run state machine or the idle probe (wash / tube / air)."""
        if self.probe['z'] < PROBE_TRAVEL_Z * 0.8:
            return None
        at = self.probe['at']
        if at == 'wash':
            return _blank_comp()
        tube = self.racks.get(at)
        if tube is None or tube['V'] <= 0.0 or self._fault('probe_blocked'):
            return None
        comp = dict(tube['comp'])
        d = (self.run or {}).get('dilute_now')
        if d:
            comp = {k: v / d for k, v in comp.items()}
        return comp

    def _equivalent(self, c, comp):
        """Analyte equivalent (mol/m3 of element) that develops colour in channel c from a sample composition."""
        ch = self.ch[c]
        chem = ch['chem']
        if chem == 'nitrate_nitrite':
            return comp['nitrate_n'] / (MW['N'] * 1e-3) * (ch['cd_eff'] or 0.0) + comp['nitrite_n'] / (MW['N'] * 1e-3)
        if chem == 'nitrite':
            return comp['nitrite_n'] / (MW['N'] * 1e-3)
        if chem == 'ammonia':
            return comp['ammonium_n'] / (MW['N'] * 1e-3)
        if chem == 'phosphate':
            p = comp['phosphate_p'] / (MW['P'] * 1e-3) + comp['arsenate_as'] / (MW['As'] * 1e-3)
            thr = ENG['iron_phosphate_loss_threshold_kg_m3']
            if comp['iron'] > thr:
                p *= 1.0 - min(0.6, 0.25 * (comp['iron'] / thr - 1.0))
            return p
        return comp['silicate_si'] / (MW['Si'] * 1e-3)

    def _matrix(self, comp, cell):
        return cell * comp['colour'] * ENG['colour_absorbance_m2_kg'] * 100.0 + comp['turbidity'] * ENG['turbidity_au_per_ntu'] * cell / 0.010 \
            + comp['salinity'] * ENG['refractive_blank_au_per_salinity']

    # ---- physics
    def step(self, dt_s):
        """Advance the clock; the state is integrated on the fixed DT grid anchored at t = 0, so the result does not depend on how the host
        partitions the time; between grid points the last state is held (a command acts from the grid point before it, at most DT earlier)."""
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = self.t_grid = t_end
            return self.t
        while True:
            h = DT
            if self.t_grid + h > t_end + 1e-9:
                break
            self.t = self.t_grid
            self._tick(h)
            self.t_grid += h
            self.tick += 1
        self.t = t_end
        self._update_warnings()
        return self.t

    def _moving(self):
        p = self.probe
        return abs(p['x'] - p['tx']) > 1e-9 or abs(p['y'] - p['ty']) > 1e-9 or abs(p['z'] - p['tz']) > 1e-9 or \
            abs(self.pump['platen_pos'] - (self.PLATEN_TRAVEL if self.pump['platen'] else 0.0)) > 1e-12

    def _tick(self, h):
        room = self.plant['room_k']
        # platen (motorised) and sampler motion
        tgt = self.PLATEN_TRAVEL if (self.pump['platen'] and self.powered) else (0.0 if not self.pump['platen'] else self.pump['platen_pos'])
        rate = self.PLATEN_TRAVEL / ENG['platen_motion_s']
        self.pump['platen_pos'] += max(-rate * h, min(rate * h, tgt - self.pump['platen_pos']))
        if self.powered:
            self._run_machine(h)
            self._program_machine(h)
            p = self.probe
            v = 0.40 / ENG['sampler_move_s']
            for a in ('x', 'y'):
                p[a] += max(-v * h, min(v * h, p['t' + a] - p[a]))
            vz = PROBE_TRAVEL_Z / 0.5
            ready_xy = abs(p['x'] - p['tx']) < 1e-9 and abs(p['y'] - p['ty']) < 1e-9
            tz = p['tz'] if ready_xy else 0.0
            p['z'] += max(-vz * h, min(vz * h, tz - p['z']))
        flowing = self._flowing()
        if flowing:
            self.pump['hours'] += h / 3600.0
        comp = self._probe_input() if flowing else None
        for c in CHANNELS:
            self._channel_tick(c, h, comp, flowing, room)
        if flowing and comp is not None and self.probe['at'] != 'wash':
            tube = self.racks[self.probe['at']]
            q = sum((self._flows(c) or {}).get('sample', 0.0) for c in CHANNELS)
            take = min(tube['V'], q * h)
            tube['V'] -= take
            self.led['sample_used_m3'].add(take)
            R = self.run
            if tube['V'] <= 0.0 and R is not None and R.get('phase') == 'sample' and R['index'] < len(R['cups']):
                R['cups'][R['index']]['short'] = True                    # the tube ran dry during the sample time
        elif flowing and comp is not None:
            q = sum((self._flows(c) or {}).get('sample', 0.0) for c in CHANNELS)
            self.led['wash_used_m3'].add(q * h)

    def _channel_tick(self, c, h, comp, flowing, room):
        ch = self.ch[c]
        # heating bath
        if c in BATHS:
            if ch['bath_T'] is None:
                ch['bath_T'] = room
            tgt = ch['bath_set'] if (ch['bath_set'] is not None and self.powered and not self._fault('bath_failure', c)) else room
            if self._fault('bath_failure', c) and ch['bath_set'] is not None and self.powered:
                tgt = ch['bath_set'] + 6.0                       # runaway controller (heats beyond the set point)
            step = (tgt - ch['bath_T']) * (1.0 - math.exp(-h / ENG['bath_time_constant_s']))
            ch['bath_T'] += max(-ENG['bath_heat_rate_k_s'] * h, min(ENG['bath_heat_rate_k_s'] * h, step))
        T = ch['bath_T'] if c in BATHS else room
        fl = self._flows(c) if flowing else None
        st = ch['stages']
        if fl is not None:
            # reagents present?  (valve + bottle level); consumption
            present = ch['valve'] == 'reagent' and all(v > 0.0 for v in ch['reagents'].values()) and not self._fault('reagent_empty', c)
            liquid_in = fl['dilution'] * h + (fl['sample'] * h if comp is not None else 0.0)      # the sample line draws air when the probe is up / dry
            if ch['valve'] == 'reagent':
                per = {r: q * self._speed() for r, q in ch['tubes']['reagents_m3_s'].items()}
                for r, q in per.items():
                    take = min(ch['reagents'][r], q * h)
                    ch['reagents'][r] -= take
                    self.led['reagent_used_m3'].add(take)
                    liquid_in += take
            else:
                self.led['wash_used_m3'].add(fl['reagents'] * h)
                liquid_in += fl['reagents'] * h
            self.led['wash_used_m3'].add(fl['dilution'] * h)
            self.led['waste_m3'].add(liquid_in)
            # cadmium coil: reduction consumes capacity; air and chlorine damage
            if ch['chem'] == 'nitrate_nitrite' and ch['cd_eff'] is not None:
                if comp is not None:
                    n_no3 = comp['nitrate_n'] / (MW['N'] * 1e-3) * fl['sample'] * h * ch['cd_eff']
                    ch['cd_reduced'] += n_no3
                    self.led['nitrate_reduced_mol'].add(n_no3)
                    n_cl = comp['chlorine'] / (MW['Cl'] * 1e-3) * fl['sample'] * h
                    ch['cd_eff'] *= math.exp(-ENG['cadmium_chlorine_damage_per_mol'] * n_cl) * math.exp(-n_no3 / ENG['cadmium_capacity_mol'])
                if self._fault('cadmium_air', c) or (ch['valve'] == 'reagent' and ch['reagents'].get('imidazole_buffer', 1.0) <= 0.0):
                    ch['cd_eff'] *= (1.0 - ENG['cadmium_air_damage'] * h / 60.0)
            x_in = [0.0, 0.0, 1.0 if present else 0.0]
            tau_ou = ENG['flow_ratio_noise_time_s']                 # pump pulsation / segmentation irregularity of the sample : stream ratio
            ch['ou'] += -ch['ou'] * h / tau_ou + ENG['flow_ratio_noise'] * math.sqrt(2.0 * h / tau_ou) * _gauss(self.seed ^ _h(c), self.tick, 11)
            if comp is not None:
                x_in[0] = self._equivalent(c, comp) * fl['fraction'] * (1.0 + ch['ou'])
                x_in[1] = self._matrix(comp, ch['cell_m']) * fl['fraction'] * (1.0 + ch['ou'])
            hist = self._hist[c]
            hist.append(x_in)
            n_delay = max(1, int(round(self._delay_s(fl) / DT)))
            while len(hist) > n_delay:
                x_out = hist.popleft()
                tau = ENG['dispersion_time_constant_s'] / len(st) * (tubes_nominal(ch) / max(fl['liquid'] + fl['air'], 1e-12))
                a = 1.0 - math.exp(-h / max(tau, 1e-6))
                prev = x_out
                for s_ in st:
                    for j in range(3):
                        s_[j] += (prev[j] - s_[j]) * a
                    prev = s_
            phi = 1.0 - math.exp(-self._k_rate(c, T) * self._residence_s(fl))
        else:
            phi = 1.0 - math.exp(-self._k_rate(c, T) * 100.0)
        eq, matrix, r = st[-1]
        chem = methods()[ch['method']]['chemistry']
        eps = chem['molar_absorptivity_m2_mol'] or 0.0
        wl_ok = chem['wavelength_m'][0] is not None and abs(ch['led_m'] - chem['wavelength_m'][0]) < 30e-9
        A = (eps * ch['cell_m'] * phi * r * eq if wl_ok else 0.05 * eps * ch['cell_m'] * phi * r * eq) + ENG['reagent_blank_au'].get(ch['chem'], 0.01) * r + matrix
        ch['A_true'] = A
        ch['phi'] = phi
        # detector
        if self.powered and ch['led_on'] and not self._fault('led_failure', c):
            seed = self.seed ^ _h(c)
            ch['drift'] += ENG['baseline_drift_au_per_h'] / 3600.0 * h * (0.6 + 0.4 * _gauss(seed, self.tick, 3))
            age = self.t - ch['led_since']
            warm = ENG['led_warmup_drift_au'] * math.exp(-age / (ENG['led_warmup_s'] / 3.0)) * (1.0 - ENG['dual_beam_compensation'])
            noise = ENG['detector_noise_au'] * _gauss(seed, self.tick, 7)
            ch['A_meas'] = min(A + ch['drift'] + warm + noise, ENG['detector_full_scale_au'])      # photometer output clamps at full scale
            tr = self._traces[c]
            if (flowing or self.run) and len(tr) < MAX_TRACE:
                tr.append((self.t + h, ch['A_meas'], self.tick))
        else:
            ch['A_meas'] = None

    def _program_machine(self, h):
        if self.program is None:
            return
        kind, t0 = self.program
        el = self.t - t0
        if kind == 'startup':
            if el >= self.STARTUP_WASH_S and any(ch['valve'] == 'wash' for ch in self.ch.values()):
                for c, ch in self.ch.items():
                    ch['valve'] = 'reagent'
                self._event('system', 'REAGENTS_ON', '开机程序：阀切到试剂')
            if el >= self.STARTUP_WASH_S + 420.0 and self._baseline_ready():
                self.program = None
                self.state = 'ready'
                self._event('system', 'READY', '开机程序完成：基线稳定，系统就绪')
        else:
            if el >= self.SHUTDOWN_RINSE_S:
                for c, ch in self.ch.items():
                    ch['led_on'] = False
                    if c in BATHS:
                        ch['bath_set'] = None
                self.pump['running'] = False
                self.pump['speed'] = 'normal'
                self.pump['platen'] = False
                self.probe.update(tx=0.0, ty=0.0, tz=0.0, at='wash')
                self.program = None
                self.state = 'idle'
                self._event('system', 'SHUTDOWN_DONE', '关机程序完成：已冲洗，LED与加热浴关闭，泵停，压板抬起，进样针升起')

    def _baseline_ready(self):
        for c, ch in self.ch.items():
            if not ch['led_on'] or self.t - (self.t if ch['led_since'] is None else ch['led_since']) < ENG['led_warmup_s']:
                return False
            if c in BATHS and (ch['bath_set'] is None or abs(ch['bath_T'] - ch['bath_set']) > 0.5):
                return False
            if ch['valve'] != 'reagent' or ch['stages'][-1][2] < 0.98:
                return False
        return True

    # ---- run state machine (sampler sequence)
    def _run_machine(self, h):
        R = self.run
        if R is None:
            return
        if R['state'] == 'running':
            if R['phase'] is None:
                if R['index'] >= len(R['cups']):
                    R['state'] = 'draining'
                    self.probe.update(tx=0.0, ty=0.0, tz=PROBE_TRAVEL_Z, at='wash')
                else:
                    cup = R['cups'][R['index']]
                    x, y = rack_position_xy(cup['rack'], cup['position'])
                    self.probe.update(tx=x, ty=y, tz=0.0)
                    R['phase'], R['phase_t'] = 'move', 0.0
            else:
                R['phase_t'] += h
                cup = R['cups'][R['index']]
                if R['phase'] == 'move' and R['phase_t'] >= ENG['sampler_move_s'] - 1e-9:
                    self.probe.update(tz=PROBE_TRAVEL_Z, at=(cup['rack'], cup['position']))
                    if (cup['rack'], cup['position']) not in self.racks:
                        cup['no_tube'] = True                                 # the probe samples air: flagged in the results
                    R['dilute_now'] = cup.get('dilution') if cup.get('dilution', 1.0) > 1.0 else None
                    R['phase'], R['phase_t'] = 'sample', 0.0
                    cup['t_start'] = self.t + h
                elif R['phase'] == 'sample' and R['phase_t'] >= R['sample_s'] - 1e-9:
                    cup['t_end'] = self.t + h
                    cup['delay_s'] = {c: self._delay_s(self._flows(c)) for c in CHANNELS if self._flows(c)}
                    self.probe.update(tx=0.0, ty=0.0, tz=0.0)
                    R['dilute_now'] = None
                    R['phase'], R['phase_t'] = 'to_wash', 0.0
                elif R['phase'] == 'to_wash' and R['phase_t'] >= ENG['sampler_move_s'] - 1e-9:
                    self.probe.update(tz=PROBE_TRAVEL_Z, at='wash')
                    R['phase'], R['phase_t'] = 'wash', 0.0
                elif R['phase'] == 'wash' and R['phase_t'] >= R['wash_s'] - 2 * ENG['sampler_move_s'] - 1e-9:     # AACE cycle = sample + wash (moves inside the wash)
                    R['index'] += 1
                    R['phase'] = None
        self._evaluate_cups()
        if R['state'] == 'draining' and R['index'] < len(R['cups']):          # an auto-dilution re-run was queued while draining
            R['state'] = 'running'
            return
        if R['state'] == 'draining' and all(cup.get('peaks') is not None for cup in R['cups']):
            R['state'] = 'completed'
            R['t_done'] = self.t
            self.state = 'ready' if self._baseline_ready() else 'idle'
            self._compute_results()
            self._event('run', 'RUN_COMPLETED', f'运行{R["run_id"]}完成：{len(R["cups"])}杯')
            self.last_run, self.run = R, None

    def _window(self, cup, c):
        d = cup['delay_s'].get(c)
        if d is None:
            return None
        win = ENG['peak_window_fraction'] * (cup['t_end'] - cup['t_start'])
        end = cup['t_end'] + d + 0.5 * ENG['dispersion_time_constant_s']
        return end - win, end

    def _evaluate_cups(self):
        R = self.run
        for cup in R['cups']:
            if cup.get('peaks') is not None or cup.get('t_end') is None:
                continue
            wins = {c: self._window(cup, c) for c in CHANNELS}
            if any(w is not None and self.t < w[1] + 1e-9 for w in wins.values()):
                continue
            peaks, pmax = {}, {}
            for c, w in wins.items():
                if w is None:
                    peaks[c] = pmax[c] = None
                    continue
                vals = [a for (tt, a, _) in self._traces[c] if w[0] - 1e-9 <= tt <= w[1] + 1e-9]
                peaks[c] = (sum(vals) / len(vals)) if vals else None
                pmax[c] = max(vals) if vals else None                   # a plateau clipped anywhere in the window is saturated
            cup['peaks'] = peaks
            cup['peak_max'] = pmax
            if R.get('auto_dilute') and cup['type'] == 'sample' and cup.get('dilution', 1.0) == 1.0:
                cal = self._calibrate(R, partial=True)
                over = [c for c in CHANNELS if cal.get(c) and peaks.get(c) is not None and peaks[c] > cal[c]['max_peak'] * 1.02]
                if over:
                    new = dict(rack=cup['rack'], position=cup['position'], type='sample', dilution=R['dilution_factor'], rerun_of=cup['index'])
                    new['index'] = len(R['cups'])
                    R['cups'].append(new)
                    self._event('run', 'AUTO_DILUTION', f'第{cup["index"] + 1}杯超量程（{",".join(over)}），按{R["dilution_factor"]:g}倍自动稀释复测')

    # ---- AACE equivalent: baseline, carry-over, drift, calibration, results
    def _corrected(self, R, carry=True, drift=True):
        cups = [cu for cu in R['cups'] if cu.get('peaks') is not None]
        out = {}
        for c in CHANNELS:
            base = [((cu['t_start'] + cu['t_end']) / 2, cu['peaks'][c]) for cu in cups if cu['type'] == 'baseline' and cu['peaks'].get(c) is not None]

            def baseline(t):
                if not base:
                    return 0.0
                if t <= base[0][0]:
                    return base[0][1]
                if t >= base[-1][0]:
                    return base[-1][1]
                for (t0, b0), (t1, b1) in zip(base, base[1:]):
                    if t0 <= t <= t1:
                        return b0 + (b1 - b0) * (t - t0) / (t1 - t0)
                return base[-1][1]
            ps = {}
            for cu in cups:
                p = cu['peaks'].get(c)
                ps[cu['index']] = None if p is None else p - baseline((cu['t_start'] + cu['t_end']) / 2)
            k = 0.0
            hi = [cu for cu in cups if cu['type'] == 'carryover_high']
            if carry and hi:
                i = hi[0]['index']
                lows = [cu for cu in cups if cu['type'] == 'carryover_low' and cu['index'] in (i + 1, i + 2)]
                if len(lows) == 2 and ps[i] is not None and ps[lows[0]['index']] is not None and ps[lows[1]['index']] is not None:
                    k = max(0.0, (ps[lows[0]['index']] - ps[lows[1]['index']]) / ps[i]) if ps[i] > 1e-9 else 0.0      # EPA 366.0 10.5: k = (Pb1 - Pb2) / Phigh
            if carry and k > 0.0:
                order = sorted(cups, key=lambda cu: cu['t_start'])
                raw = dict(ps)
                for a, b in zip(order, order[1:]):
                    if raw.get(b['index']) is not None and raw.get(a['index']) is not None:
                        ps[b['index']] = raw[b['index']] - k * raw[a['index']]
            dr = [((cu['t_start'] + cu['t_end']) / 2, ps[cu['index']]) for cu in cups if cu['type'] == 'drift' and ps.get(cu['index'])]
            if drift and len(dr) >= 2 and dr[0][1] > 1e-6:
                ref = dr[0][1]
                for cu in cups:
                    t = (cu['t_start'] + cu['t_end']) / 2
                    if t <= dr[0][0] or ps.get(cu['index']) is None:
                        continue
                    f = dr[-1][1] / ref
                    for (t0, d0), (t1, d1) in zip(dr, dr[1:]):
                        if t0 <= t <= t1:
                            f = (d0 + (d1 - d0) * (t - t0) / (t1 - t0)) / ref
                            break
                    ps[cu['index']] = ps[cu['index']] / max(f, 1e-6)
            out[c] = dict(peaks=ps, carryover=k)
        return out

    def _calibrate(self, R, partial=False, fit='auto', carry=True, drift=True):
        corr = self._corrected(R, carry, drift)
        cal = {}
        for c in CHANNELS:
            pts = []
            for cu in R['cups']:
                if cu['type'] != 'calibrant' or corr[c]['peaks'].get(cu['index']) is None:
                    continue
                if ((cu.get('peak_max') or {}).get(c) or 0.0) >= 0.98 * ENG['detector_full_scale_au']:
                    continue                                                 # clipped calibrants are left out of the fit
                nom = (cu.get('nominal_kg_m3') or {}).get(c)
                if nom is None:
                    continue
                pts.append((nom, corr[c]['peaks'][cu['index']]))
            if len(pts) < 2:
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            order = 2 if (fit == 'quadratic' or (fit == 'auto' and max(ys) > ENG['quadratic_threshold_au'] and len(pts) >= 4)) else 1
            coef = _polyfit(xs, ys, order)
            yhat = [_poly(coef, x) for x in xs]
            ss_res = sum((y - yh) ** 2 for y, yh in zip(ys, yhat))
            ybar = sum(ys) / len(ys)
            ss_tot = sum((y - ybar) ** 2 for y in ys) or 1e-30
            cal[c] = dict(order=order, coefficients=coef, r2=1.0 - ss_res / ss_tot, points=len(pts), max_conc=max(xs), max_peak=max(ys), min_conc=min(x for x in xs if x > 0) if any(x > 0 for x in xs) else 0.0,
                          carryover=corr[c]['carryover'])
        return cal

    def _compute_results(self, fit=None, carry=None, drift=None, warn=True):
        R = self.run or self.last_run
        st = R.setdefault('settings', dict(fit='auto', carry=True, drift=True))      # AACE recalculation settings persist with the run
        fit, carry, drift = (st['fit'] if fit is None else fit), (st['carry'] if carry is None else carry), (st['drift'] if drift is None else drift)
        cal = self._calibrate(R, fit=fit, carry=carry, drift=drift)
        corr = self._corrected(R, carry, drift)
        rows, qc = [], []
        for cu in R['cups']:
            if cu.get('peaks') is None:
                continue
            for c in CHANNELS:
                p = corr[c]['peaks'].get(cu['index'])
                conc, flags = None, []
                raw = (cu.get('peak_max') or cu.get('peaks') or {}).get(c)
                saturated = raw is not None and raw >= 0.98 * ENG['detector_full_scale_au']
                if saturated:
                    flags.append('DETECTOR_SATURATED')                       # no concentration from a clipped peak
                if cu.get('no_tube'):
                    flags.append('NO_TUBE')
                if cu.get('short'):
                    flags.append('SAMPLE_SHORT')
                if p is not None and c in cal and not saturated and not cu.get('no_tube'):
                    conc = _invert(cal[c]['coefficients'], p)
                    d = cu.get('dilution', 1.0)
                    if conc is not None:
                        if conc > cal[c]['max_conc'] * 1.02:
                            flags.append('OVER_RANGE')
                        if 0.0 < conc < cal[c]['min_conc'] * 0.5:
                            flags.append('BELOW_LOWEST_STANDARD')
                        conc *= d
                if cu.get('rerun_of') is not None:
                    flags.append('AUTO_DILUTED')
                rows.append(dict(cup=cu['index'] + 1, rack=cu['rack'], position=cu['position'], type=cu['type'], channel=c, peak_au=p, concentration_kg_m3=conc,
                                 dilution=float(cu.get('dilution', 1.0)), flags=flags))
                nom = (cu.get('nominal_kg_m3') or {}).get(c)
                if cu['type'] == 'qc' and nom and conc is not None:
                    rec = conc / nom
                    qc.append(dict(cup=cu['index'] + 1, channel=c, nominal_kg_m3=nom, found_kg_m3=conc, recovery=rec, passed=abs(rec - 1.0) <= 0.10))
        eff = None
        n2 = [cu for cu in R['cups'] if cu['type'] == 'nitrite_check']
        n3 = [cu for cu in R['cups'] if cu['type'] == 'nitrate_check']
        if n2 and n3:
            p2, p3 = corr['ch1']['peaks'].get(n2[0]['index']), corr['ch1']['peaks'].get(n3[0]['index'])
            if p2 and p3 is not None:
                eff = p3 / p2
                if eff < 0.90 and warn:
                    self.warnings['CADMIUM_EFFICIENCY_LOW'] = f'镉还原效率{eff * 100:.0f} %（<90 %）：活化镉盘管'
        R['results'] = rows
        R['qc'] = qc
        R['calibration'] = {c: dict(v, cadmium_efficiency=eff if c == 'ch1' else None) for c, v in cal.items()}
        return R

    # ---- read
    def _update_warnings(self):
        for c, ch in self.ch.items():
            low = [r for r, v in ch['reagents'].items() if v < 0.05 * ENG['reagent_bottle_m3']]
            key = 'REAGENT_LOW_' + c
            if low and ch['valve'] == 'reagent':
                self.warnings[key] = f'{c}试剂液位低：{",".join(low)}'
            else:
                self.warnings.pop(key, None)
        if self._fault('leak') and self.powered:
            self.warnings['LEAK'] = '泄漏检测器报警：泵已停止'
        else:
            self.warnings.pop('LEAK', None)

    def read(self, visibility='agent'):
        out = {'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.on': self.powered, 'system.state': self.state,
               'pump.running': self.pump['running'], 'pump.speed': self.pump['speed'], 'pump.platen_engaged': self.pump['platen'],
               'pump.platen_position_m': self.pump['platen_pos'], 'pump.rotor_velocity_rad_s': self.ROTOR_NORMAL * self._speed() if self._flowing() else 0.0,
               'pump.lid_angle_rad': self.pump['lid'], 'lamp.power': self.powered}
        chs, baths, det, lev = {}, {}, {}, {}
        for c, ch in self.ch.items():
            m = methods()[ch['method']]
            rng = m['range_highest']['si']
            chs[c] = dict(method_id=ch['method'], chemistry=ch['chem'], seal_method=m['seal_method'], valve=ch['valve'], led_wavelength_m=ch['led_m'], led_on=ch['led_on'],
                          led_warm=bool(ch['led_on'] and self.t - (self.t if ch['led_since'] is None else ch['led_since']) >= ENG['led_warmup_s']), flowcell_path_m=ch['cell_m'],
                          tubes_for=ch['tubes_for'], sample_fraction=ch['tubes']['sample_fraction'] if ch['tubes'] else None,
                          range_kg_m3=[rng['low'], rng['high']], range_unit=rng['unit'], range_basis=rng.get('basis'),
                          result_basis=ANALYTE_OF[ch['chem']])
            if c in BATHS:
                baths[c] = None if ch['bath_T'] is None or not self.powered else round(ch['bath_T'], 1)
            det[c] = ch['A_meas']
            lev[c] = {r: v for r, v in ch['reagents'].items()}
            out[f'cover.{c}_angle_rad'] = ch['cover']
        out.update({'channels.status': chs, 'baths.temperature_k': baths, 'detector.absorbance': det, 'reagents.levels_m3': lev})
        out['sampler.contents'] = {f'{r}{p}': dict(item_id=t['item_id'], volume_m3=t['V']) for (r, p), t in sorted(self.racks.items())}
        out['sampler.probe_x_m'], out['sampler.probe_y_m'], out['sampler.probe_z_m'] = self.probe['x'], self.probe['y'], self.probe['z']
        R = self.run or self.last_run
        out['run.status'] = dict(run_id=R['run_id'] if R else None, state=R['state'] if R else 'none', cups=len(R['cups']) if R else 0,
                                 completed=sum(1 for cu in R['cups'] if cu.get('peaks') is not None) if R else 0,
                                 current_cup=(R['index'] + 1) if (R and R['state'] == 'running') else None)
        out['samples.available'] = sorted(self.items)
        out['warnings.active'] = sorted(self.warnings)
        if visibility != 'agent':
            ts = {}
            for c, ch in self.ch.items():
                fl = self._flows(c)
                eq, matrix, r = ch['stages'][-1]
                ts[c] = dict(flows_m3_s=fl, delay_s=self._delay_s(fl) if fl else None, equivalent_mol_m3=eq, matrix_au=matrix, reagent_fraction=r, colour_fraction=ch.get('phi'),
                             cadmium_efficiency=ch['cd_eff'], bath_k=ch['bath_T'], absorbance_true=ch['A_true'], drift_au=ch['drift'])
            out['truth.streams'] = ts
            out['truth.items'] = {k: dict(kind=v['kind'], volume_m3=v['V'], composition=dict(v['comp'])) for k, v in self.items.items()}
            out['truth.faults'] = sorted(f'{f}:{c}' if c else f for f, c in self.faults)
        return out

    # ---------------------------------------------------------------- commands
    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(room_k=float(room_k), mains_voltage_v=float(mains_voltage_v))
        self.seed = int(noise_seed)
        if self.powered and not 198.0 <= mains_voltage_v <= 264.0:
            self._power_off('电源电压超出220–240 V（±10 %）：断电')
        if first:
            self._event('system', 'PLANT', f'实验室：室温{room_k - 273.15:.1f} °C、电源{mains_voltage_v:.0f} V')
        return dict(configured=True)

    def _add_item(self, item_id, kind, volume, comp, nominal=None):
        if item_id in self.items:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.items) >= MAX_ITEMS:
            raise ActionRejected('TOO_MANY_ITEMS')
        self.items[item_id] = dict(kind=kind, V=float(volume), comp=comp, nominal=nominal)
        self._own('item:' + item_id)

    def _cmd_sample_provide(self, sample_id, volume_m3, composition):
        self._need_plant()
        comp = _blank_comp()
        comp.update({k: float(v) for k, v in composition.items()})
        self._add_item(sample_id, 'sample', volume_m3, comp)
        self._event('system', 'SAMPLE', f'样品{sample_id}（{volume_m3 * 1e6:.1f} ml）')
        return dict(sample_id=sample_id, volume_m3=float(volume_m3))

    def _cmd_kit_provide(self, kit, volume_m3=1e-3):
        self._need_plant()
        items = []
        if kit in STOCKS:
            sid, species, mass = STOCKS[kit]
            e = substances()[sid]
            comp = _blank_comp()
            comp[species] = mass * e['element_mass_fraction']                    # kg/m3 true (EPA recipe and PubChem formula)
            nominal = {species: STOCK_NOMINAL[kit]}
            if kit in self.items:
                self.items[kit]['V'] += volume_m3
            else:
                self._add_item(kit, 'stock', volume_m3, comp, nominal)
            items.append(kit)
        elif kit == 'wash_water':
            if kit in self.items:
                self.items[kit]['V'] += volume_m3
            else:
                self._add_item(kit, 'wash', volume_m3, _blank_comp(), {})
            items.append(kit)
        elif kit == 'cadmium_activation':
            self.items.setdefault(kit, dict(kind='reagent_kit', V=volume_m3, comp=_blank_comp(), nominal=None))
            items.append(kit)
        else:
            chem = kit[len('reagents_'):]
            for r in REAGENTS[chem]:
                key = f'{chem}:{r}'
                if key in self.items:
                    self.items[key]['V'] += volume_m3
                else:
                    self.items[key] = dict(kind='reagent', V=float(volume_m3), comp=_blank_comp(), nominal=None)
                items.append(key)
        self._event('system', 'KIT', f'试剂/储备液：{kit}')
        return dict(items=items)

    def _cmd_standard_prepare(self, standard_id, volume_m3, components):
        self._need_plant()
        if standard_id in self.items:
            raise ActionRejected('ITEM_EXISTS')
        tot = sum(x['volume_m3'] for x in components)
        if tot > volume_m3 * (1 + 1e-9):
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        need = {}
        for x in components:
            it = self.items.get(x['source_id'])
            if it is None or it['kind'] not in ('stock', 'standard', 'sample', 'wash'):
                raise ActionRejected('UNKNOWN_ITEM')
            need[x['source_id']] = need.get(x['source_id'], 0.0) + x['volume_m3']
        for k, v in need.items():
            if self.items[k]['V'] < v - 1e-12:
                raise ActionRejected('INSUFFICIENT_VOLUME')
        comp, nominal = _blank_comp(), {}
        for x in components:
            it = self.items[x['source_id']]
            f = x['volume_m3'] / volume_m3
            for s_ in SPECIES:
                comp[s_] += it['comp'][s_] * f
            for s_, v in (it.get('nominal') or {}).items():
                nominal[s_] = nominal.get(s_, 0.0) + v * f
            it['V'] -= x['volume_m3']
        self._add_item(standard_id, 'standard', volume_m3, comp, nominal)
        nom_ch = self._nominal_channels(nominal)
        return dict(standard_id=standard_id, volume_m3=float(volume_m3), nominal_kg_m3=nom_ch)

    def _nominal_channels(self, nominal):
        """Nominal concentration per channel (kg/m3 of the analyte element) of a labelled standard."""
        out = {}
        for c, ch in self.ch.items():
            chem = ch['chem']
            if chem == 'nitrate_nitrite':
                out[c] = nominal.get('nitrate_n', 0.0) + nominal.get('nitrite_n', 0.0)
            elif chem == 'nitrite':
                out[c] = nominal.get('nitrite_n', 0.0)
            elif chem == 'ammonia':
                out[c] = nominal.get('ammonium_n', 0.0)
            elif chem == 'phosphate':
                out[c] = nominal.get('phosphate_p', 0.0)
            else:
                out[c] = nominal.get('silicate_si', 0.0)
        return out

    def _cmd_sampler_load(self, rack, position, item_id, volume_m3):
        self._need_plant()
        if position > rack_size(rack):
            raise ActionRejected('INVALID_ARGUMENT')
        if (rack, position) in self.racks:
            raise ActionRejected('POSITION_OCCUPIED')
        it = self.items.get(item_id)
        if it is None or it['kind'] in ('reagent', 'reagent_kit'):
            raise ActionRejected('UNKNOWN_ITEM')
        if it['V'] < volume_m3 - 1e-12:
            raise ActionRejected('INSUFFICIENT_VOLUME')
        if self.run is not None and self.run['state'] == 'running' and self.probe['at'] == (rack, position):
            raise ActionRejected('PROBE_IN_TUBE')
        it['V'] -= volume_m3
        self.racks[(rack, position)] = dict(item_id=item_id, V=float(volume_m3), comp=dict(it['comp']), nominal=dict(it.get('nominal') or {}))
        self._own(f'tube:{rack}{position}')
        return dict(rack=rack, position=position)

    def _cmd_sampler_unload(self, rack, position):
        self._need_plant()
        t = self.racks.get((rack, position))
        if t is None:
            raise ActionRejected('POSITION_EMPTY')
        if self.probe['at'] == (rack, position) and self.probe['z'] > 1e-6:
            raise ActionRejected('PROBE_IN_TUBE')
        if self.run is not None and any((cu['rack'], cu['position']) == (rack, position) and cu.get('t_end') is None for cu in self.run['cups']):
            raise ActionRejected('RUN_ACTIVE')
        del self.racks[(rack, position)]
        self.led['waste_m3'].add(t['V'])
        self._owners.pop(f'tube:{rack}{position}', None)
        return dict(rack=rack, position=position, removed_m3=float(t['V']))

    def _cmd_sampler_move(self, target, rack=None, position=None, probe_down=False):
        self._need_power()
        if self.run is not None:
            raise ActionRejected('RUN_ACTIVE')
        if target == 'position':
            if rack is None or position is None or position > rack_size(rack):
                raise ActionRejected('INVALID_ARGUMENT')
            x, y = rack_position_xy(rack, position)
            at = (rack, position)
        else:
            x, y, at = 0.0, 0.0, 'wash'
        down = bool(probe_down) and target != 'home'
        self.probe.update(tx=x, ty=y, tz=PROBE_TRAVEL_Z if down else 0.0, at=at if down else at)
        if not down:
            self.probe['at'] = at if target != 'home' else 'wash'
        return dict(x_m=x, y_m=y, z_m=PROBE_TRAVEL_Z if down else 0.0)

    def _power_off(self, why):
        self.powered = False
        self.state = 'off'
        self.program = None
        self.pump['running'] = False                       # the pump motor stops (the platen keeps its position); restart is a new command
        for ch in self.ch.values():
            ch['led_on'] = False
        if self.run is not None:
            R = self.run
            R['state'] = 'stopped'
            R['cups'] = [cu for cu in R['cups'] if cu.get('peaks') is not None]     # completed cups keep their peaks and results
            self._compute_results()
            self.last_run, self.run = R, None
        self._event('system', 'POWER_OFF', why)

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not 198.0 <= self.plant['mains_voltage_v'] <= 264.0:
            raise ActionRejected('MAINS_OUT_OF_RANGE')
        if on and not self.powered:
            self.powered = True
            self.state = 'idle'
            self._event('system', 'POWER_ON', '主机与进样器通电（AACE连接）')
        elif not on and self.powered:
            self._power_off('主机与进样器断电')
        return dict(on=self.powered)

    def _cmd_power_press_switch(self):
        return self._cmd_power_set(not self.powered)

    def _cmd_system_startup(self):
        self._need_power()
        if self.program is not None or self.run is not None:
            raise ActionRejected('BUSY')
        if any(ch['tubes'] is None for ch in self.ch.values()):
            raise ActionRejected('TUBES_MISSING')
        if self.pump['lid'] > 1e-6:
            raise ActionRejected('PUMP_LID_OPEN')
        self.pump.update(platen=True, running=True, speed='normal')
        for c, ch in self.ch.items():
            ch['valve'] = 'wash'
            if not ch['led_on']:
                ch['led_on'], ch['led_since'] = True, self.t
            if c in BATHS:
                ch['bath_set'] = methods()[ch['method']]['chemistry']['bath_temperature_k'] or 310.15
        self.probe.update(tx=0.0, ty=0.0, tz=PROBE_TRAVEL_Z, at='wash')
        self.program = ('startup', self.t)
        self.state = 'starting'
        self._event('system', 'STARTUP', '自动开机程序开始')
        return dict(state=self.state)

    def _cmd_system_shutdown(self):
        self._need_power()
        if self.program is not None or self.run is not None:
            raise ActionRejected('BUSY')
        for ch in self.ch.values():
            ch['valve'] = 'wash'
        if self.pump['platen']:
            self.pump.update(running=True, speed='fast')
        self.probe.update(tx=0.0, ty=0.0, tz=PROBE_TRAVEL_Z, at='wash')
        self.program = ('shutdown', self.t)
        self.state = 'shutting_down'
        self._event('system', 'SHUTDOWN', '自动关机程序开始：阀切清洗、快速冲洗')
        return dict(state=self.state)

    def _cmd_pump_set(self, running, speed=None):
        self._need_power()
        if running and self.pump['lid'] > 1e-6:
            raise ActionRejected('PUMP_LID_OPEN')
        if running and self._fault('leak'):
            raise ActionRejected('LEAK_DETECTED')
        self.pump['running'] = bool(running)
        if speed is not None:
            self.pump['speed'] = speed
        return dict(running=self.pump['running'], speed=self.pump['speed'])

    def _cmd_platen_set(self, engaged):
        self._need_power()
        if engaged and self.pump['lid'] > 1e-6:
            raise ActionRejected('PUMP_LID_OPEN')
        self.pump['platen'] = bool(engaged)
        return dict(engaged=self.pump['platen'])

    def _cmd_pump_lid_set(self, open):
        self._need_plant()
        if open and self.pump['platen']:
            raise ActionRejected('PLATEN_ENGAGED')
        self.pump['lid'] = self.LID_OPEN if open else 0.0
        return dict(open=bool(open))

    def _cmd_valve_set(self, channel, position):
        self._need_power()
        self.ch[channel]['valve'] = position
        return dict(channel=channel, position=position)

    def _cmd_lamp_set(self, channel, on):
        self._need_power()
        ch = self.ch[channel]
        if on and ch['cover'] > 1e-6:
            raise ActionRejected('COVER_OPEN')
        if on and not ch['led_on']:
            ch['led_on'], ch['led_since'] = True, self.t
        elif not on:
            ch['led_on'] = False
        return dict(channel=channel, on=ch['led_on'])

    def _cmd_bath_set(self, channel, temperature_k):
        self._need_power()
        self.ch[channel]['bath_set'] = None if temperature_k is None else float(temperature_k)
        return dict(channel=channel, temperature_k=self.ch[channel]['bath_set'])

    def _cmd_cover_set(self, channel, open):
        self._need_plant()
        ch = self.ch[channel]
        if open and ch['led_on']:
            raise ActionRejected('LAMP_ON')
        ch['cover'] = self.COVER_OPEN if open else 0.0
        return dict(channel=channel, open=bool(open))

    def _cmd_led_install(self, channel, wavelength_m):
        self._need_plant()
        ch = self.ch[channel]
        if ch['cover'] <= 1e-6:
            raise ActionRejected('COVER_CLOSED')
        if ch['led_on']:
            raise ActionRejected('LAMP_ON')
        ch['led_m'] = float(wavelength_m)
        return dict(channel=channel, wavelength_m=ch['led_m'])

    def _cmd_flowcell_install(self, channel, path_m):
        self._need_plant()
        ch = self.ch[channel]
        if ch['cover'] <= 1e-6:
            raise ActionRejected('COVER_CLOSED')
        if ch['led_on']:
            raise ActionRejected('LAMP_ON')
        if self._flowing():
            raise ActionRejected('PUMP_RUNNING')
        ch['cell_m'] = float(path_m)
        return dict(channel=channel, path_m=ch['cell_m'])

    def _method_ok(self, channel, method_id):
        m = methods().get(method_id)
        if m is None:
            raise ActionRejected('UNKNOWN_METHOD')
        if m['analyte_key'] not in CHEMS or not m['supported_by_runtime']:
            raise ActionRejected('METHOD_NOT_SUPPORTED')
        if m['manifold'] != INSTALLED_MANIFOLD[channel]:
            raise ActionRejected('WRONG_MANIFOLD')
        return m

    def _cmd_pumptube_install(self, channel, method_id):
        self._need_plant()
        m = self._method_ok(channel, method_id)
        if self.pump['platen'] or self.pump['platen_pos'] > 1e-9:
            raise ActionRejected('PLATEN_ENGAGED')
        if self.pump['lid'] <= 1e-6:
            raise ActionRejected('PUMP_LID_CLOSED')
        ch = self.ch[channel]
        tb = design_manifold(method_id, ch['cell_m'])
        if m['analyte_key'] != ch['chem']:
            ch['chem'] = m['analyte_key']
            ch['reagents'] = {r: 0.0 for r in REAGENTS[ch['chem']]}
            ch['cd_eff'] = ENG['cadmium_initial_efficiency'] if ch['chem'] == 'nitrate_nitrite' else None
        ch['tubes'], ch['tubes_for'] = tb, method_id
        return dict(channel=channel, sample_m3_s=tb['sample_m3_s'], sample_fraction=tb['sample_fraction'])

    def _cmd_channel_configure(self, channel, method_id):
        self._need_power()
        m = self._method_ok(channel, method_id)
        ch = self.ch[channel]
        if self.run is not None:
            raise ActionRejected('RUN_ACTIVE')
        if ch['tubes_for'] != method_id or m['analyte_key'] != ch['chem']:
            raise ActionRejected('TUBES_MISMATCH')
        ch['method'] = method_id
        r = m['range_highest']['si']
        return dict(channel=channel, method_id=method_id, range_kg_m3=[r['low'], r['high']])

    def _cmd_cadmium_activate(self, channel):
        self._need_plant()
        ch = self.ch[channel]
        if ch['chem'] != 'nitrate_nitrite':
            raise ActionRejected('NO_CADMIUM')
        if 'cadmium_activation' not in self.items:
            raise ActionRejected('UNKNOWN_ITEM')
        if self._flowing() and ch['valve'] == 'reagent':
            raise ActionRejected('REAGENTS_FLOWING')
        ch['cd_eff'] = ENG['cadmium_initial_efficiency'] * (0.995 + 0.004 * _uni(self.seed, self.tick, 91))
        ch['cd_reduced'] = 0.0
        self.faults.discard(('cadmium_air', channel))
        self.warnings.pop('CADMIUM_EFFICIENCY_LOW', None)
        self._event('channel', 'CADMIUM_ACTIVATED', f'{channel}镉还原盘管已活化')
        return dict(channel=channel, efficiency_note='活化后用等浓度亚硝酸盐与硝酸盐标准核对还原效率（EPA 353.2）')

    def _cmd_reagent_load(self, channel, reagent, volume_m3):
        self._need_plant()
        ch = self.ch[channel]
        if reagent not in ch['reagents']:
            raise ActionRejected('WRONG_REAGENT')
        key = f'{ch["chem"]}:{reagent}'
        it = self.items.get(key)
        if it is None:
            raise ActionRejected('UNKNOWN_ITEM')
        if it['V'] < volume_m3 - 1e-12:
            raise ActionRejected('INSUFFICIENT_VOLUME')
        if ch['reagents'][reagent] + volume_m3 > ENG['reagent_bottle_m3'] * (1 + 1e-9):
            raise ActionRejected('BOTTLE_FULL')
        it['V'] -= volume_m3
        ch['reagents'][reagent] += volume_m3
        return dict(channel=channel, reagent=reagent, volume_m3=ch['reagents'][reagent])

    def _cmd_run_start(self, cups, sample_s, wash_s, auto_dilute=False, dilution_factor=10.0):
        self._need_power()
        if self.run is not None or self.program is not None:
            raise ActionRejected('BUSY')
        if not self._flowing():
            raise ActionRejected('PUMP_NOT_FLOWING')
        rate = 3600.0 / (sample_s + wash_s)                       # samples per hour; the two probe moves (2 x 1.5 s) take place inside the wash time
        if rate > DOC['throughput_per_channel_per_h_max'] * 1.0001:
            raise ActionRejected('RATE_TOO_HIGH')
        seq = []
        for i, cu in enumerate(cups):
            if cu['position'] > rack_size(cu['rack']):
                raise ActionRejected('INVALID_ARGUMENT')
            t = self.racks.get((cu['rack'], cu['position']))
            if t is None:
                raise ActionRejected('POSITION_EMPTY')
            nom = cu.get('nominal_kg_m3')
            if nom is None and t.get('nominal'):
                nom = self._nominal_channels(t['nominal'])
            seq.append(dict(rack=cu['rack'], position=cu['position'], type=cu['type'], nominal_kg_m3=nom, index=i, dilution=1.0))
        rid = f'run{sum(1 for e in self.events if e["code"] == "RUN_START") + 1:03d}'
        self.run = dict(run_id=rid, cups=seq, sample_s=float(sample_s), wash_s=float(wash_s), auto_dilute=bool(auto_dilute), dilution_factor=float(dilution_factor),
                        index=0, phase=None, phase_t=0.0, state='running', t0=self.t, dilute_now=None)
        self.state = 'running'
        self._event('run', 'RUN_START', f'运行{rid}开始：{len(seq)}杯，{rate:.0f}样/h')
        est = len(seq) * (sample_s + wash_s) + 240.0
        return dict(run_id=rid, cups=len(seq), throughput_per_s=rate / 3600.0, estimated_s=est)

    def _cmd_run_stop(self):
        self._need_plant()
        if self.run is None:
            raise ActionRejected('NO_RUN')
        R = self.run
        R['state'] = 'stopped'
        R['cups'] = [cu for cu in R['cups'] if cu.get('peaks') is not None]
        self._compute_results()
        self.probe.update(tx=0.0, ty=0.0, tz=PROBE_TRAVEL_Z, at='wash')
        self.last_run, self.run = R, None
        self.state = 'ready' if self._baseline_ready() else 'idle'
        self._event('run', 'RUN_STOPPED', f'运行{R["run_id"]}停止')
        return dict(run_id=R['run_id'], state='stopped')

    def _cmd_data_read_trace(self, channel, kind='debubbled', max_points=2000):
        tr = self._traces[channel]
        n = len(tr)
        idx = list(range(n)) if n <= max_points else [round(i * (n - 1) / (max_points - 1)) for i in range(max_points)]
        ts, vals = [], []
        fl = self._flows(channel)
        f_b = ((fl or {}).get('air', 0.0) / ENG['bubble_volume_m3']) if fl else 0.8
        p_bub = min(0.9, f_b * 0.12)
        for i in idx:
            t, a, k = tr[i]
            if kind == 'raw' and _uni(self.seed ^ _h(channel), k, 55) < p_bub:
                a = a + 0.6 + 0.4 * _uni(self.seed ^ _h(channel), k, 56)          # air bubble passing through the cuvette
            ts.append(float(t))
            vals.append(float(a))
        return dict(channel=channel, time_s=ts, absorbance=vals)

    def _cmd_data_results(self):
        R = self.run or self.last_run
        if R is None:
            return dict(run_id=None, state='none', calibration={}, results=[], qc=[])
        if R is self.run:
            self._compute_results(warn=False)
        return dict(run_id=R['run_id'], state=R['state'], calibration=_jsonable(R.get('calibration', {})), results=R.get('results', [])[:4000], qc=R.get('qc', []))

    def _cmd_data_recalculate(self, fit, carryover_correction, drift_correction):
        R = self.run or self.last_run
        if R is None:
            raise ActionRejected('NO_RUN')
        R['settings'] = dict(fit=fit, carry=bool(carryover_correction), drift=bool(drift_correction))
        self._compute_results(warn=False)
        return dict(run_id=R['run_id'], calibration=_jsonable(R['calibration']))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_provenance_read(self):
        libs = [dict(name=k, file=str((ASSET_DIR / v).relative_to(ROOT)), entries=len(_lib(k).get('methods', _lib(k).get('entries', _lib(k).get('documented', {})))))
                for k, v in LIBS.items()]
        return dict(libraries=libs, methods=sorted(methods())[:200])

    def _cmd_truth_inspect(self):
        r = self.read('evaluator')
        return dict(state=_jsonable(dict(streams=r['truth.streams'], items=r['truth.items'], faults=r['truth.faults'])))

    def _cmd_ledger_read(self):
        return dict(ledger={k: float(v.value()) for k, v in self.led.items()})

    def _cmd_fault_inject(self, fault, active, channel=None):
        self._need_plant()
        per_ch = fault in ('reagent_empty', 'led_failure', 'bath_failure', 'pump_tube_worn', 'cadmium_air')
        if per_ch and channel is None:
            raise ActionRejected('INVALID_ARGUMENT')
        if fault == 'bath_failure' and channel not in BATHS:
            raise ActionRejected('INVALID_ARGUMENT')
        if fault == 'cadmium_air' and self.ch[channel]['chem'] != 'nitrate_nitrite':
            raise ActionRejected('NO_CADMIUM')
        key = (fault, channel if per_ch else '')
        (self.faults.add if active else self.faults.discard)(key)
        if fault == 'leak' and active and self.powered:
            self.pump['running'] = False
        self._event('fault', 'FAULT', f'{fault} {"on" if active else "off"} {channel or ""}')
        return dict(faults=sorted(f'{f}:{c}' if c else f for f, c in self.faults))

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
        mutating = CAPS[capability_id]['kind'] not in ('query', 'analysis') and capability_id != 'sim.step'
        snap = self._snapshot() if mutating else None
        try:
            out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        except ActionRejected:
            if snap is not None:
                self._restore(snap)
            raise
        if mutating or capability_id == 'data.recalculate':
            self.revision += 1
        return out

    _NOCOPY = ('_principals', '_requests', '_traces')

    def _snapshot(self):
        return {k: copy.deepcopy(v) for k, v in self.__dict__.items() if k not in self._NOCOPY}

    def _restore(self, snap):
        for k, v in snap.items():
            self.__dict__[k] = v


def tubes_nominal(ch):
    tb = ch['tubes']
    return tb['liquid_m3_s'] + tb['air_m3_s']


def _polyfit(xs, ys, order):
    """Least squares polynomial (order 1 or 2) through the calibrants (normal equations)."""
    n = order + 1
    A = [[sum(x ** (i + j) for x in xs) for j in range(n)] for i in range(n)]
    b = [sum(y * x ** i for x, y in zip(xs, ys)) for i in range(n)]
    for i in range(n):                                   # Gaussian elimination with partial pivoting
        p = max(range(i, n), key=lambda r: abs(A[r][i]))
        A[i], A[p], b[i], b[p] = A[p], A[i], b[p], b[i]
        for r in range(i + 1, n):
            f = A[r][i] / A[i][i] if A[i][i] else 0.0
            for cc in range(i, n):
                A[r][cc] -= f * A[i][cc]
            b[r] -= f * b[i]
    coef = [0.0] * n
    for i in range(n - 1, -1, -1):
        coef[i] = (b[i] - sum(A[i][cc] * coef[cc] for cc in range(i + 1, n))) / A[i][i] if A[i][i] else 0.0
    return coef


def _poly(coef, x):
    return sum(c * x ** i for i, c in enumerate(coef))


def _invert(coef, y):
    if len(coef) == 2:
        return (y - coef[0]) / coef[1] if coef[1] else None
    c0, c1, c2 = coef
    if abs(c2) < 1e-15:
        return (y - c0) / c1 if c1 else None
    disc = c1 * c1 - 4 * c2 * (c0 - y)
    if disc < 0:
        return None
    r = [(-c1 + s * math.sqrt(disc)) / (2 * c2) for s in (1, -1)]
    pos = [x for x in r if x >= -1e-6]
    return min(pos) if pos else r[0]


def _jsonable(o):
    return json.loads(json.dumps(o, default=float))


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED
                    and k not in ('standard.prepare',)}


class SealAa500Session:
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
        meta = copy.deepcopy(export_seal_aa500_contract_metadata())
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
            values['samples.available'] = [x for x in values['samples.available'] if not self._foreign('item:' + x)]
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
        iid = arguments.get('item_id')
        if isinstance(iid, str) and self._foreign('item:' + iid):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'standard.prepare' and any(self._foreign('item:' + s['source_id']) for s in arguments.get('components', [])):
            raise ActionRejected('ACCESS_DENIED')
        rack, pos = arguments.get('rack'), arguments.get('position')
        if capability_id in ('sampler.unload',) and isinstance(rack, str) and self._foreign(f'tube:{rack}{pos}'):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'run.start' and any(self._foreign(f'tube:{c["rack"]}{c["position"]}') for c in arguments.get('cups', [])):
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
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值、评测者能力或另一个agent的样品/管）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '主机未通电', 'MAINS_OUT_OF_RANGE': '电源电压超出220–240 V（±10 %）',
    'ITEM_EXISTS': 'ID已存在', 'TOO_MANY_ITEMS': '数量超过上限', 'UNKNOWN_ITEM': '样品、标准、储备液或试剂不存在', 'INSUFFICIENT_VOLUME': '体积不够',
    'VOLUME_OUT_OF_RANGE': '组分体积之和超过终体积', 'POSITION_OCCUPIED': '架位已有管', 'POSITION_EMPTY': '架位没有管', 'PROBE_IN_TUBE': '进样针正在该管中',
    'RUN_ACTIVE': '运行进行中', 'BUSY': '开/关机程序或运行进行中', 'TUBES_MISSING': '有通道未装泵管', 'PUMP_LID_OPEN': '泵盖开着', 'LEAK_DETECTED': '泄漏检测器报警',
    'PLATEN_ENGAGED': '压板合着（先抬起）', 'COVER_OPEN': '检测器盖开着', 'LAMP_ON': 'LED开着（先关灯）', 'COVER_CLOSED': '检测器盖关着（先打开）',
    'PUMP_RUNNING': '泵在流动（先停泵）', 'UNKNOWN_METHOD': '方法不在方法库中', 'METHOD_NOT_SUPPORTED': '该方法的化学不在运行层支持范围（NOx、亚硝酸盐、氨、磷酸盐、硅酸盐）',
    'WRONG_MANIFOLD': '方法不属于该通道已装的多测试流路（MT508）', 'PUMP_LID_CLOSED': '泵盖关着（换泵管须开盖）', 'TUBES_MISMATCH': '已装泵管组不是为该方法设计的（先pumptube.install）',
    'NO_CADMIUM': '该通道没有镉还原盘管（不是NOx化学）', 'REAGENTS_FLOWING': '试剂正在流（阀切清洗或停泵）', 'WRONG_REAGENT': '试剂不属于该通道的化学',
    'BOTTLE_FULL': '试剂瓶容量不够', 'PUMP_NOT_FLOWING': '泵未流动（开泵、合压板）', 'RATE_TOO_HIGH': '进样+清洗时间对应的通量超过120样/h', 'NO_RUN': '没有运行',
}
_P = ['PLANT_REQUIRED']
_PW = _P + ['POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'sample.provide': _P + ['ITEM_EXISTS', 'TOO_MANY_ITEMS'], 'kit.provide': _P + ['ITEM_EXISTS', 'TOO_MANY_ITEMS'],
    'standard.prepare': _P + ['ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_ITEM', 'INSUFFICIENT_VOLUME', 'VOLUME_OUT_OF_RANGE'],
    'sampler.load': _P + ['POSITION_OCCUPIED', 'UNKNOWN_ITEM', 'INSUFFICIENT_VOLUME', 'PROBE_IN_TUBE'], 'sampler.unload': _P + ['POSITION_EMPTY', 'PROBE_IN_TUBE', 'RUN_ACTIVE'],
    'sampler.move': _PW + ['RUN_ACTIVE'], 'power.set': _P + ['MAINS_OUT_OF_RANGE'], 'power.press_switch': _P + ['MAINS_OUT_OF_RANGE'],
    'system.startup': _PW + ['BUSY', 'TUBES_MISSING', 'PUMP_LID_OPEN'], 'system.shutdown': _PW + ['BUSY'], 'pump.set': _PW + ['PUMP_LID_OPEN', 'LEAK_DETECTED'],
    'platen.set': _PW + ['PUMP_LID_OPEN'], 'pump.lid_set': _P + ['PLATEN_ENGAGED'], 'valve.set': _PW, 'lamp.set': _PW + ['COVER_OPEN'], 'bath.set': _PW,
    'cover.set': _P + ['LAMP_ON'], 'led.install': _P + ['COVER_CLOSED', 'LAMP_ON'], 'flowcell.install': _P + ['COVER_CLOSED', 'LAMP_ON', 'PUMP_RUNNING'],
    'pumptube.install': _P + ['UNKNOWN_METHOD', 'METHOD_NOT_SUPPORTED', 'WRONG_MANIFOLD', 'PLATEN_ENGAGED', 'PUMP_LID_CLOSED'],
    'channel.configure': _PW + ['UNKNOWN_METHOD', 'METHOD_NOT_SUPPORTED', 'WRONG_MANIFOLD', 'RUN_ACTIVE', 'TUBES_MISMATCH'],
    'cadmium.activate': _P + ['NO_CADMIUM', 'UNKNOWN_ITEM', 'REAGENTS_FLOWING'], 'reagent.load': _P + ['WRONG_REAGENT', 'UNKNOWN_ITEM', 'INSUFFICIENT_VOLUME', 'BOTTLE_FULL'],
    'run.start': _PW + ['BUSY', 'PUMP_NOT_FLOWING', 'RATE_TOO_HIGH', 'POSITION_EMPTY'], 'run.stop': _P + ['NO_RUN'],
    'data.read_trace': [], 'data.results': [], 'data.recalculate': ['NO_RUN'], 'events.read': [], 'provenance.read': [], 'truth.inspect': [], 'ledger.read': [],
    'fault.inject': _P + ['NO_CADMIUM'], 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子；之后再声明须给同一种子', ['plant.configured']),
    'sample.provide': ('ID唯一', ['samples.available']), 'kit.provide': ('同一储备液或试剂组再次提供时增加体积', ['samples.available']),
    'standard.prepare': ('ID唯一；组分存在、体积足够、组分体积之和不超过终体积', ['samples.available']),
    'sampler.load': ('架位为空、位号在架容量内；样品/标准体积足够；进样针不在该位', ['sampler.contents', 'samples.available']),
    'sampler.unload': ('架位有管；进样针不在该管；运行中未进样的杯不能取下', ['sampler.contents', 'run.status']),
    'sampler.move': ('已通电；不在运行中', ['power.on', 'run.status']), 'power.set': ('电源电压在220–240 V（±10 %）内才能通电', ['power.on']),
    'power.press_switch': ('同power.set', ['power.on']), 'system.startup': ('已通电；没有程序或运行；各通道已装泵管；泵盖关着', ['power.on', 'system.state', 'pump.lid_angle_rad']),
    'system.shutdown': ('已通电；没有程序或运行', ['power.on', 'system.state']), 'pump.set': ('已通电；泵盖关着；无泄漏报警', ['power.on', 'pump.lid_angle_rad']),
    'platen.set': ('已通电；合上压板时泵盖须关着', ['power.on', 'pump.lid_angle_rad']), 'pump.lid_set': ('开盖时压板须抬起', ['pump.platen_engaged']),
    'valve.set': ('已通电', ['power.on']), 'lamp.set': ('已通电；开灯时检测器盖须关着', ['power.on']), 'bath.set': ('已通电', ['power.on']),
    'cover.set': ('开盖时LED须关着', ['channels.status']), 'led.install': ('检测器盖开着、LED关着', ['channels.status']),
    'flowcell.install': ('检测器盖开着、LED关着、泵不流动', ['channels.status', 'pump.running']),
    'pumptube.install': ('方法在方法库且其化学被支持、属于该通道的MT508流路；压板抬起、泵盖开着', ['pump.platen_engaged', 'pump.lid_angle_rad']),
    'channel.configure': ('已通电；方法有效且已装该方法的泵管组；不在运行中', ['channels.status', 'run.status']),
    'cadmium.activate': ('NOx通道；已提供镉活化液；试剂不在流动', ['channels.status']), 'reagent.load': ('试剂属于该通道化学且已提供；瓶容量足够', ['reagents.levels_m3']),
    'run.start': ('已通电；无程序或运行；泵在流动；各杯架位有管；进样+清洗+移动时间对应通量不超过120样/h', ['power.on', 'pump.running', 'sampler.contents']),
    'run.stop': ('有运行', ['run.status']), 'data.recalculate': ('有运行结果', ['run.status']),
    'fault.inject': ('通道类故障须给通道；加热浴故障只对ch2、ch3；气泡入镉只对NOx通道', ['plant.configured']),
}
_COVERS = [f'cover.{c}_angle_rad' for c in CHANNELS]
_PROBE = ['sampler.probe_x_m', 'sampler.probe_y_m', 'sampler.probe_z_m']
_FLOW = ['detector.absorbance', 'truth.streams', 'reagents.levels_m3', 'sampler.contents', 'truth.items', 'pump.rotor_velocity_rad_s', 'pump.platen_position_m', 'baths.temperature_k',
         'warnings.active', 'channels.status', 'system.state', 'run.status']
EFFECTS = {
    'plant.configure': ['plant.configured', 'power.on', 'system.state', 'lamp.power', 'channels.status', 'detector.absorbance', 'truth.streams', 'run.status',
                        'pump.running', 'pump.rotor_velocity_rad_s', 'baths.temperature_k', 'warnings.active'],
    'sample.provide': ['samples.available', 'truth.items'], 'kit.provide': ['samples.available', 'truth.items'], 'standard.prepare': ['samples.available', 'truth.items'],
    'sampler.load': ['sampler.contents', 'truth.items'], 'sampler.unload': ['sampler.contents'], 'sampler.move': [],
    'power.set': ['truth.streams', 'power.on', 'system.state', 'lamp.power', 'channels.status', 'detector.absorbance', 'run.status', 'pump.rotor_velocity_rad_s', 'baths.temperature_k',
                  'pump.running', 'warnings.active'],
    'power.press_switch': ['truth.streams', 'power.on', 'system.state', 'lamp.power', 'channels.status', 'detector.absorbance', 'run.status', 'pump.rotor_velocity_rad_s',
                           'baths.temperature_k', 'pump.running', 'warnings.active'],
    'system.startup': ['truth.streams', 'system.state', 'pump.running', 'pump.speed', 'pump.platen_engaged', 'channels.status', 'pump.rotor_velocity_rad_s', 'detector.absorbance'],
    'system.shutdown': ['truth.streams', 'system.state', 'pump.running', 'pump.speed', 'channels.status', 'pump.rotor_velocity_rad_s'],
    'pump.set': ['truth.streams', 'pump.running', 'pump.speed', 'pump.rotor_velocity_rad_s'], 'platen.set': ['pump.platen_engaged', 'pump.rotor_velocity_rad_s'], 'pump.lid_set': ['pump.lid_angle_rad'],
    'valve.set': ['channels.status'], 'lamp.set': ['channels.status', 'detector.absorbance'], 'bath.set': [], 'cover.set': _COVERS,
    'led.install': ['channels.status'], 'flowcell.install': ['channels.status'], 'pumptube.install': ['truth.streams', 'channels.status', 'reagents.levels_m3'], 'channel.configure': ['channels.status'],
    'cadmium.activate': ['truth.streams', 'warnings.active', 'truth.faults'], 'reagent.load': ['reagents.levels_m3', 'truth.items'],
    'run.start': ['run.status', 'system.state'], 'run.stop': ['run.status', 'system.state', 'warnings.active'], 'data.recalculate': ['run.status'],
    'fault.inject': ['truth.streams', 'truth.faults', 'pump.running', 'pump.rotor_velocity_rad_s'], 'sim.step': ['clock.time_s'],
}
ON_STEP = {
    'sim.step': _FLOW + _PROBE + ['clock.time_s'], 'system.startup': _FLOW + _PROBE, 'system.shutdown': _FLOW + _PROBE + ['pump.running', 'pump.speed', 'pump.platen_engaged'],
    'pump.set': _FLOW, 'platen.set': _FLOW, 'valve.set': _FLOW, 'lamp.set': ['detector.absorbance', 'channels.status', 'system.state'], 'bath.set': ['baths.temperature_k', 'detector.absorbance', 'truth.streams'],
    'run.start': _FLOW + _PROBE, 'run.stop': _PROBE + ['detector.absorbance', 'truth.streams'], 'sampler.move': _PROBE + ['detector.absorbance', 'truth.streams', 'sampler.contents'], 'power.set': ['pump.platen_position_m'] + _FLOW,
    'power.press_switch': ['pump.platen_position_m'] + _FLOW, 'fault.inject': _FLOW, 'cadmium.activate': ['detector.absorbance'], 'reagent.load': ['detector.absorbance', 'truth.streams'],
    'pumptube.install': _FLOW, 'channel.configure': ['detector.absorbance', 'truth.streams'], 'flowcell.install': ['detector.absorbance', 'truth.streams'],
    'led.install': ['detector.absorbance', 'truth.streams'], 'sampler.load': ['detector.absorbance', 'truth.streams', 'sampler.contents'],
    'plant.configure': ['baths.temperature_k', 'detector.absorbance', 'truth.streams'],
}
ON_STEP_ZH = {
    'sim.step': '按0.5 s网格积分：泵管流量、进样序列、传输延迟、三级离散、显色、检测器、镉效率、加热浴、试剂与废液', 'system.startup': '压板约4 s合上；10 min后阀切试剂；基线稳定后状态为就绪',
    'system.shutdown': '快速冲洗15 min后关灯、关浴、停泵、抬压板', 'pump.set': '泵流动后样品与试剂按流量进入流路', 'platen.set': '压板约4 s到位，之后泵管才流动',
    'valve.set': '试剂前沿约2 min后到达检测器', 'lamp.set': 'LED约15 min预热漂移', 'bath.set': '加热浴按时间常数升降温，影响显色速率',
    'run.start': '按杯序列进样、清洗（进样针移动在清洗时间内）；每杯的峰在传输延迟后到达；超量程时自动稀释复测', 'run.stop': '进样针升起并回到清洗槽，流路中仍在路上的样品冲出', 'sampler.move': '进样针移动约1.5 s，放下后吸液', 'power.set': '断电后压板保持，泵停',
    'power.press_switch': '同power.set', 'fault.inject': '故障作用于随后的流路与检测', 'cadmium.activate': '活化后还原效率恢复', 'reagent.load': '试剂瓶有液后试剂前沿进入流路',
    'pumptube.install': '新泵管组改变样品分数与灵敏度', 'channel.configure': '方法决定化学参数与量程', 'flowcell.install': '光程改变灵敏度', 'led.install': '波长与化学不匹配时灵敏度大幅下降',
    'sampler.load': '运行中的进样从该管吸液', 'plant.configure': '室温影响未加热通道的显色速率',
}
EXEC_TEXT = {
    'run.start': ('run.status.state从running到completed（每杯进样+清洗+移动，峰在传输延迟后到达；结束后data.results给出结果）',
                  '运行中断（run.stop、断电）时已完成的杯保留峰高与结果，未进样的杯丢弃；进样针堵塞或管空时该杯峰低并可能标记'),
    'system.startup': ('system.state从starting到ready（约20–40 min：清洗10 min、试剂到达、LED预热15 min、加热浴升到37 °C（升温决定总时长）、基线稳定）', '程序中断电时停止；状态回到off'),
    'system.shutdown': ('system.state从shutting_down到idle（快速冲洗15 min后关灯、关浴、停泵、抬压板）', '程序中断电时停止'),
}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('run.start', 'system.startup', 'system.shutdown', 'sampler.move'):
    RESOURCES[_k] = ['host_clock', 'sampler', 'pump']
OBSERVES = {'run.start': ['run.status', 'detector.absorbance'], 'system.startup': ['system.state', 'detector.absorbance'], 'system.shutdown': ['system.state'],
            'pump.set': ['pump.running'], 'platen.set': ['pump.platen_engaged'], 'lamp.set': ['detector.absorbance'], 'bath.set': ['baths.temperature_k'],
            'sampler.move': _PROBE, 'cover.set': _COVERS, 'pump.lid_set': ['pump.lid_angle_rad']}
_WB, _PR, _BR, _OM, _NIT, _E353, _E350, _E365, _E366, _PUB, _SIM = ('user_workbook', 'manufacturer_primary', 'manufacturer_brochure', 'distributor_omniprocess',
                                                                   'manufacturer_method_nitrate', 'epa_353_2', 'epa_350_1', 'epa_365_1', 'epa_366_0', 'pubchem',
                                                                   'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_OM, _SIM], 'sample.provide': [_SIM], 'kit.provide': [_E353, _E350, _E365, _E366, _PUB], 'standard.prepare': [_E353, _E366, _SIM],
                    'sampler.load': [_OM, _SIM], 'sampler.unload': [_OM], 'sampler.move': [_OM, _SIM], 'power.set': [_OM], 'power.press_switch': [_OM],
                    'system.startup': [_PR, _BR, _OM], 'system.shutdown': [_PR, _BR, _OM], 'pump.set': [_OM], 'platen.set': [_PR, _OM], 'pump.lid_set': [_SIM],
                    'valve.set': [_OM, _PR], 'lamp.set': [_BR, _OM], 'bath.set': [_OM, _SIM], 'cover.set': [_SIM], 'led.install': [_BR, _OM], 'flowcell.install': [_BR, _OM],
                    'pumptube.install': [_OM, _E366, _SIM], 'channel.configure': [_PR], 'cadmium.activate': [_E353, _SIM], 'reagent.load': [_BR, _SIM],
                    'run.start': [_PR, _E366, _E353], 'run.stop': [_SIM], 'data.read_trace': [_BR, _PR], 'data.results': [_E353, _E366, _PR], 'data.recalculate': [_PR, _E353],
                    'provenance.read': [_PR, _PUB], 'fault.inject': [_OM, _E353, _SIM]})
CAP_SOURCES.update({k: [_PR, _OM] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温', 'mains_voltage_v': '电源电压（220–240 V）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.segmented_flow_process.limitations。'
CAP_LIMITS = {
    'run.start': ['AACE功能等价：峰高取进样窗口末25 %（按传输延迟与离散时间平移），基线杯线性插值校正，携带系数由高-低-低三杯求得，漂移标准线性校正，线性/二次校准，QC ±10 %，超量程按稀释倍数复测；'
                  '不复制AACE的界面与全部峰形算法。'],
    'pumptube.install': ['泵管组按“量程顶端约0.8 AU”的流路设计规则从流量标定泵管系列中选样品管与稀释水管（工程规则）；硅酸盐的试剂与空气流量取EPA 366.0图1。'],
    'data.read_trace': ['在线去气泡曲线为检测器读数；原始曲线叠加气泡通过冒泡式比色池时的尖峰（频率=空气流量/气泡体积）。'],
    'cadmium.activate': ['活化为理想操作（效率恢复到约0.99）；镉盘管的长期老化只按还原量、气泡与余氯建模。'],
}
LIMITATIONS = [
    '选定配置：AA500主机4通道（MT508多测试流路：NOx镉还原、氨、磷酸盐、硅酸盐）+ AS2 XY进样器；电脑与AACE软件为宿主（功能等价实现）；在线蒸馏、紫外消解、透析与荧光/火焰/电化学检测器不在配置中。',
    '氨通道A025按苯酚试剂（Berthelot）建模；同流路的水杨酸方法A041/A042换装后沿用同一组集总试剂。流路：分段流按传输延迟+三级串联混合近似（不模拟每个液段与气泡的流体力学）；显色按一级反应与Arrhenius温度依赖；各化学的摩尔吸光系数、速率常数与试剂空白为工程假设。',
    '检测：比尔–朗伯定律线性，光度计输出在满量程3 AU截止（超过时结果标DETECTOR_SATURATED、不给浓度）；高吸光度区的非线性由二次校准吸收；试剂过量消耗引起的显色非线性未建模；LED预热漂移按双光束补偿95 %；噪声与漂移为工程值（用EPA MDL/精密度做仿真复核）。',
    '干扰：砷酸盐按磷酸盐等摩尔响应、高铁使磷酸盐沉淀损失、余氯降低镉效率、样品色度/浊度与盐度折射率空白叠加；硫化物、表面活性剂等其它干扰未建模。',
    '样品、标准、试剂、清洗水与废液是过程数据，场景中没有液体几何；进样针、压板、泵滚轮、泵盖与检测器盖按运行层状态运动。',
]


def _sampling_text(k):
    if k in ('detector.absorbance', 'truth.streams') or k.startswith('baths'):
        return 'Integrated on the fixed 0.5 s grid (anchored at t = 0); between grid points the last value is held.'
    if k in SCENE_MIRRORED:
        return 'Position / speed at the host time (motorised platen and sampler arm move with their speeds).'
    return 'Changes when a command is accepted or a program / run step completes.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k == 'standard.prepare' or CAPS[k]['visibility'] != 'agent') else []
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
    if k in ('sampler.unload', 'run.start', 'sampler.load', 'standard.prepare'):
        return '；agent只能用自己的样品/标准与自己装的管（ACCESS_DENIED）'
    return ''


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：无模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景关节或指示灯（' + '、'.join(seen[:6]) + ('…' if len(seen) > 6 else '')
                    + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节或读数', binding_ids=[])


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    doc('channels_max', DOC['channels_max'], '1', '机内最多6通道（本配置4通道）', [_PR, _OM, _WB])
    doc('throughput_per_channel_max', DOC['throughput_per_channel_per_h_max'] / 3600.0, '1/s', '每通道最高120样/h（合计600测试/h）', [_PR, _WB])
    doc('led_wavelength_range_m', list(DOC['led_wavelength_m']), 'm', 'LED波长模块250–880 nm', [_BR, _WB])
    doc('flow_cell_path_range_m', list(DOC['flow_cell_path_m']), 'm', '流通池光程10–50 mm', [_BR, _OM, _WB])
    doc('main_unit_dimensions_m', list(DOC['main_unit_dimensions_m']), 'm', '主机955 W×350 D×300 H mm（随配置加深）', [_OM, _WB])
    doc('main_unit_mass_kg', list(DOC['main_unit_mass_kg']), 'kg', '主机23–45 kg（依配置）', [_OM, _WB])
    doc('mains_v', list(DOC['mains_v']), 'V', '电源220–240 V、50 Hz', [_OM])
    doc('pump_tube_positions', DOC['pump_tube_positions'], '1', '蠕动泵34个泵管位', [_OM])
    doc('sampler_as2_footprint_m', list(DOC['sampler_as2_footprint_m']), 'm', 'AS2进样器占地440×470 mm', [_OM])
    doc('sampler_mass_kg', list(DOC['sampler_mass_kg']), 'kg', '进样器15–31 kg', [_OM])
    doc('epa366_manifold_m3_s', {k: v * 1e-6 / 60.0 for k, v in DOC['epa_366_manifold_ml_min'].items()}, 'm3/s', 'EPA 366.0硅酸盐流路泵管流量（样品0.41、空气0.25、钼酸盐0.41、草酸0.32、抗坏血酸0.41、清洗1.57 ml/min）', [_E366])
    doc('epa366_timing_s', dict(sample=DOC['epa_366_sample_time_s'], wash=DOC['epa_366_wash_time_s']), 's', 'EPA 366.0：60样/h，进样40 s、清洗20 s', [_E366])
    doc('epa366_mdl_kg_m3', DOC['epa_366_mdl_kg_m3'], 'kg/m3', 'EPA 366.0：MDL 0.0012 mg Si/l', [_E366])
    doc('epa_ranges_kg_m3', dict(nitrate_nitrite=DOC['epa_353_2_range_kg_m3'], ammonia=DOC['epa_350_1_range_kg_m3'], phosphorus=DOC['epa_365_1_range_kg_m3']), 'kg/m3',
        'EPA量程：NOx 0.05–10、氨0.01–2.0 mg N/l、磷0.01–1.0 mg P/l', [_E353, _E350, _E365])
    doc('griess_band_m', [520e-9, 560e-9], 'm', '格里斯偶氮染料测量波段520–560 nm', [_NIT])
    doc('silicate_maxima_m', [660e-9, 820e-9], 'm', '硅钼蓝两个吸收峰660与820 nm', [_E366])
    for k, unit in (('manifold_volume_m3', 'm3'), ('transport_volume_m3', 'm3'), ('flowcell_volume_m3', 'm3'), ('dispersion_stages', '1'), ('dispersion_time_constant_s', 's'),
                    ('bubble_volume_m3', 'm3'), ('detector_noise_au', '1'), ('baseline_drift_au_per_h', '1/s'), ('led_warmup_s', 's'), ('led_warmup_drift_au', '1'),
                    ('dual_beam_compensation', '1'), ('cadmium_initial_efficiency', '1'), ('cadmium_capacity_mol', 'mol'), ('cadmium_air_damage', '1/s'),
                    ('cadmium_chlorine_damage_per_mol', '1/mol'), ('bath_heat_rate_k_s', 'K/s'), ('bath_time_constant_s', 's'), ('activation_energy_j_mol', 'J/mol'),
                    ('pump_tube_wear_per_h', '1/s'), ('platen_motion_s', 's'), ('sampler_move_s', 's'), ('probe_travel_m', 'm'), ('reagent_bottle_m3', 'm3'),
                    ('refractive_blank_au_per_salinity', '1'), ('iron_phosphate_loss_threshold_kg_m3', 'kg/m3'), ('colour_absorbance_m2_kg', 'm2/kg'),
                    ('turbidity_au_per_ntu', '1'), ('peak_window_fraction', '1'), ('quadratic_threshold_au', '1'),
                    ('flow_ratio_noise', '1'), ('flow_ratio_noise_time_s', 's'), ('detector_full_scale_au', '1')):
        v = ENG[k]
        if k == 'baseline_drift_au_per_h':
            v = v / 3600.0
        elif k == 'pump_tube_wear_per_h':
            v = v / 3600.0
        elif k == 'cadmium_air_damage':
            v = v / 60.0
        asm(PARAM_RENAME.get(k, k), v, unit, ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明）')
    asm('pump_tube_series_m3_s', dict(ENG['pump_tube_series_m3_s']), 'm3/s', ENG_NOTE['pump_tube_series_m3_s'], '工程值（颜色系列为通用约定，未在缓存资料中逐一给出）')
    asm('reagent_blank_au', dict(ENG['reagent_blank_au']), '1', ENG_NOTE['reagent_blank_au'], '工程值')
    asm('chemistry_constants', {k: dict(molar_absorptivity_m2_mol=m['chemistry']['molar_absorptivity_m2_mol'], k_ref_per_s=m['chemistry']['k_ref_per_s'],
                                        bath_temperature_k=m['chemistry']['bath_temperature_k']) for k, m in methods().items() if m['analyte_key'] in CHEMS and m['manifold'] == 'MT508'},
        '1', 'MT508方法的显色常数：摩尔吸光系数（m²/mol）、参考温度下一级速率（1/s）、加热浴温度（K）', '典型数量级假设（未在缓存资料中给出），按EPA精密度与MDL复核')
    asm('manifold_design_rule_au', 0.8, '1', '泵管组设计规则：方法量程顶端在所装流通池中约0.8 AU', '工程规则')
    asm('pump_rotor_speed_rad_s', SealAa500Runtime.ROTOR_NORMAL, 'rad/s', '泵滚轮正常转速（场景镜像用，约10 rpm；快速约3倍）', '工程值')
    asm('startup_wash_s', SealAa500Runtime.STARTUP_WASH_S, 's', '开机程序清洗时间（10 min）', '工程值（厂家只写“自动开机”）')
    asm('shutdown_rinse_s', SealAa500Runtime.SHUTDOWN_RINSE_S, 's', '关机程序快速冲洗时间（15 min）', '工程值')
    return P


PARAM_RENAME = {'baseline_drift_au_per_h': 'baseline_drift_rate', 'pump_tube_wear_per_h': 'pump_tube_wear_rate', 'cadmium_air_damage': 'cadmium_air_damage_rate'}
SLOT_REFERENCE = {'baths.temperature_k': '热力学温度（ITS-90）；加热浴读数（0.1 K分辨率）', 'detector.absorbance': '十进制吸光度（以该通道清洗水为参比的检测器读数，无量纲）',
                  'pump.rotor_velocity_rad_s': '泵滚轮绕其轴（资产+Y方向）的角速度',
                  'pump.platen_position_m': '压板自抬起位置向下（资产−Z）的行程；0为抬起，0.004 m为合上',
                  'pump.lid_angle_rad': '泵盖绕后缘铰链（资产−X轴）自关闭位置的转角',
                  'sampler.probe_x_m': '进样器X臂自清洗槽原位沿资产+X的行程', 'sampler.probe_y_m': '进样头自清洗槽原位沿资产+Y的行程',
                  'sampler.probe_z_m': '进样针自升起位置向下（资产−Z）的下降深度'}
SLOT_REFERENCE.update({f'cover.{c}_angle_rad': f'{c}检测器罩绕右端上缘铰链（资产+Y轴）自关闭位置的转角' for c in CHANNELS})


def export_seal_aa500_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'SealAa500Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.seal_aa500.SealAa500Session.invoke',
                                              invocation=f'session.invoke(instance_id="seal_aa500_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + _foreign_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='SealAa500Runtime.invoke / SealAa500Session._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（流路、显色与检测在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['pump_module', 'pump_rotor', 'pump_platen', 'chemistry_module', 'cadmium_coil', 'heating_bath_ch2', 'heating_bath_ch3', 'detector_module']
                      + [f'detector_{c}' for c in CHANNELS] + ['sampler_base', 'sampler_arm', 'sampler_probe'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='分段连续流动分析：进样针按进样/清洗时间交替吸取样品与清洗水，与试剂、空气在泵管比例下合流，经反应盘管显色，传输延迟后到达双光束LED检测器；'
                   '峰在每杯进样时间内接近稳态，AACE按峰高、基线、携带、漂移与校准曲线换算浓度',
        equations=['q_i = q_tube,i x speed x wear; sample fraction f = q_sample / (q_sample + q_reagents + q_dilution)',
                   't_delay = (V_transport + V_manifold) / (q_liquid + q_air); t_res = V_manifold / (q_liquid + q_air)',
                   'x_in = f c_eq(sample); three mixing stages dy_k/dt = (y_{k-1} - y_k) / (tau / 3), tau scaled by the nominal / actual flow',
                   'c_eq: NOx = eta_Cd c_NO3 + c_NO2; PO4 = c_P + c_As (- iron precipitation loss); NH3 = c_NH4; Si = c_Si (mol/m3 of element)',
                   'flow-ratio pulsation: x_in -> x_in (1 + u), du = -u dt / tau_u + sigma_u sqrt(2 dt / tau_u) N(0,1)',
                   'phi = 1 - exp(-k(T) t_res), k(T) = k_ref exp(-Ea/R (1/T - 1/T_ref)); A = eps L phi r y_3 + blank r + matrix; A_meas = A + drift + warm-up (1 - 0.95) + noise',
                   'eta_Cd -> eta_Cd exp(-n_NO3/n_cap) exp(-k_Cl n_Cl) (1 - air damage); activation restores 0.99',
                   'AACE: peak = mean(A_meas over the last 25 % of the sample time shifted by t_delay); baseline-corrected; carry-over k = (Pb1 - Pb2) / Phigh and P_i,c = P_i - k P_i-1 (EPA 366.0 10.5); drift ratio; '
                   'linear / quadratic least squares; QC +-10 %; auto-dilution when above the top calibrant'],
        parameters=_params(), source_ids=[_SIM, _WB, _PR, _BR, _OM, _NIT, _E353, _E350, _E365, _E366, _PUB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_aa500_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('seal_aa500', caps, slots, {'segmented_flow_process': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'segmented_flow_process': mechanism})
