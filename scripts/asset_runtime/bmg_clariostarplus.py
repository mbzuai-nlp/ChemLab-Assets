"""CHEM-173 BMG LABTECH CLARIOstar Plus multi-mode microplate reader: process runtime.

Device (BMG product page, technical specifications): 6- to 1536-well plates; xenon flash lamp; low-noise and red-shifted PMTs; CCD UV/vis
spectrometer; dual LVF Monochromators and a linear variable dichroic mirror; filter slides with 4 filters each; top and bottom free-air optics;
automatic focal height (0.1 mm); Enhanced Dynamic Range (8 decades); absorbance 220-1000 nm, 0-4 OD, 0.001 OD, accuracy < 1 % at 2 OD,
precision < 0.5 % at 1 OD / < 0.8 % at 2 OD, linearity <= 0.8 % at 2 OD; read times 8 s (96) / 15 s (384) / 28 s (1536) in flying mode and
19 / 57 / 184 s with 10 flashes; FI LOD (FITC) 0.15 pM filters top, 0.35 pM monochromator top, 3 pM monochromator bottom; FP < 0.5 mP SD at
1 nM FITC; LUM < 0.4 pM ATP; incubation +3 C above ambient up to 45 C; linear / orbital / double-orbital shaking; up to two reagent injectors,
3-500 uL per well, up to 420 uL/s, back-flushing; 45 W x 51 D x 40 H cm, 32 kg.  Selected configuration: CLARIOstar Plus with spectrometer,
LVF monochromators, FP filter set, two injectors, incubation to 45 C; the PC with SMART Control / MARS is the host (functional equivalents here).

Process model (fixed 0.1 s grid anchored at t = 0, state held between grid points): plates are SBS footprints with the ANSI/SLAS 4-2004 well
positions; each well holds a volume and a composition of library species (PhotochemCAD / OMLC molar extinction and emission spectra, quantum
yields) plus evaluator-defined reactions (first-order conversions with Arrhenius temperature dependence, optionally triggered by an injected
species) and binding pairs (tracer / receptor, Kd) for polarisation.  Chamber air relaxes to the incubator set point (or ambient), the plate
follows with a time constant; evaporation depends on temperature, sealing and edge position.  Injectors deliver reagent from primed lines at the
programmed speed; mixing to homogeneity follows a time constant shortened by shaking.  Readings: absorbance from Beer-Lambert with the well path
length (volume / bottom area + meniscus), plate background, stray light (0-4 OD) and photometric noise; fluorescence intensity from the
excitation absorbance, quantum yield, emission overlap with the emission band, inner-filter correction, focal-height factor, plate background and
shot / dark noise with gain or EDR; fluorescence polarisation from the bound fraction (Perrin-type mixing of free / bound anisotropy, G factor);
luminescence from ATP with glow or flash luciferase reagent.  Plate read timing follows the documented read times; kinetic cycles, shaking,
injections and settling are scheduled per well.  MARS-like analysis: blank correction, linear / 4-parameter standard curves, Z' factor."""
import copy
import json
import math
import re
import zlib
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.bmg_clariostarplus"
CATALOG_ID = "CHEM-173"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t4_measure/bmg_clariostarplus'
LIBS = {'spectra': 'spectral_library.json', 'plates': 'plate_library.json', 'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def spectra():
    return _lib('spectra')['entries']


def plate_formats():
    return _lib('plates')['formats']


def instrument():
    return _lib('instrument')


DOC = {k: v['value'] for k, v in instrument()['documented'].items()}
ENG = instrument()['engineering']
ENG_NOTE = instrument()['engineering_note_zh']
DT = 0.1                                     # process grid (s)
R_GAS = 8.314462618
LN10 = math.log(10.0)
FORMATS = ['6', '12', '24', '48', '96', '384', '1536']
PLATE_TYPES = ['clear', 'uv_transparent', 'black', 'white', 'black_clear_bottom']
MODES = ['abs_endpoint', 'abs_spectrum', 'fi_endpoint', 'fi_em_spectrum', 'fp_endpoint', 'lum_endpoint']
SHAKE_MODES = ['none', 'linear', 'orbital', 'double_orbital']
PUMPS = ['A', 'B']
MAX_PLATES = 20
MAX_ITEMS = 200
MAX_EVENTS_LOG = 2000
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


# ---------------------------------------------------------------- plate geometry (ANSI/SLAS 4-2004 for 96 / 384 / 1536)
def plate_shape(fmt):
    f = plate_formats()[fmt]
    return f['rows'], f['cols']


def well_names(fmt):
    rows, cols = plate_shape(fmt)
    out = []
    for r in range(rows):
        rn = chr(65 + r) if r < 26 else 'A' + chr(65 + r - 26)
        for c in range(cols):
            out.append(f'{rn}{c + 1}')
    return out


def well_index(fmt, name):
    m = re.fullmatch(r'([A-Z]{1,2})(\d{1,2})', name)
    if not m:
        return None
    rows, cols = plate_shape(fmt)
    rs = m.group(1)
    r = ord(rs[0]) - 65 if len(rs) == 1 else 26 + ord(rs[1]) - 65
    c = int(m.group(2)) - 1
    if not (0 <= r < rows and 0 <= c < cols):
        return None
    return r, c


def well_xy(fmt, name):
    """Well centre in the plate frame (m from the A1 corner of the footprint: +x along the columns, +y along the rows)."""
    f = plate_formats()[fmt]
    r, c = well_index(fmt, name)
    return f['a1_from_left_m'] + c * f['pitch_m'], f['a1_from_top_m'] + r * f['pitch_m']


def expand_wells(fmt, spec):
    """'A1', 'A1:B12' (rectangle), 'all' or a list of these -> ordered well names (row-wise)."""
    names = well_names(fmt)
    items = spec if isinstance(spec, list) else [spec]
    out = []
    for it in items:
        if it == 'all':
            out += names
            continue
        if ':' in it:
            a, b = it.split(':', 1)
            ia, ib = well_index(fmt, a), well_index(fmt, b)
            if ia is None or ib is None:
                raise ActionRejected('INVALID_WELL')
            for r in range(min(ia[0], ib[0]), max(ia[0], ib[0]) + 1):
                for c in range(min(ia[1], ib[1]), max(ia[1], ib[1]) + 1):
                    out.append(names[r * plate_shape(fmt)[1] + c])
            continue
        if well_index(fmt, it) is None:
            raise ActionRejected('INVALID_WELL')
        out.append(it)
    seen = set()
    return [w for w in out if not (w in seen or seen.add(w))]


def edge_well(fmt, name):
    rows, cols = plate_shape(fmt)
    r, c = well_index(fmt, name)
    return r in (0, rows - 1) or c in (0, cols - 1)


# ---------------------------------------------------------------- spectra helpers
_SPEC_CACHE = {}


def eps_at(species, wl_m):
    """Molar extinction (m2/mol) of a library species at wl_m (linear interpolation, 0 outside the measured range)."""
    e = spectra()[species]['absorption']
    key = (species, 'abs')
    if key not in _SPEC_CACHE:
        _SPEC_CACHE[key] = (np.asarray(e['wavelength_m']), np.asarray(e['epsilon_m2_mol']))
    x, y = _SPEC_CACHE[key]
    return float(np.interp(wl_m, x, y, left=0.0, right=0.0))


def emission_fraction(species, lo_m, hi_m):
    """Fraction of the (normalised) emission spectrum inside [lo, hi]; library spectra without emission -> 0."""
    e = spectra()[species].get('emission')
    if not e:
        return 0.0
    key = (species, 'ems')
    if key not in _SPEC_CACHE:
        x, y = np.asarray(e['wavelength_m']), np.asarray(e['relative'])
        _SPEC_CACHE[key] = (x, y, float(np.trapezoid(y, x)) if hasattr(np, 'trapezoid') else float(np.trapz(y, x)))
    x, y, tot = _SPEC_CACHE[key]
    if tot <= 0 or hi_m <= x[0] or lo_m >= x[-1]:
        return 0.0
    g = np.linspace(max(lo_m, x[0]), min(hi_m, x[-1]), 64)
    v = np.interp(g, x, y)
    part = float(np.trapezoid(v, g)) if hasattr(np, 'trapezoid') else float(np.trapz(v, g))
    return part / tot


def quantum_yield(species):
    q = spectra()[species].get('quantum_yield')
    return float(q['value']) if q else 0.0


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
            for key, value in v.items():
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


SID = dict(type='string', minLength=1, maxLength=60, pattern='^[A-Za-z0-9_.:+-]+$')
WELLS = array(dict(type='string', minLength=2, maxLength=9, pattern='^(all|[A-Z]{1,2}[0-9]{1,2}(:[A-Z]{1,2}[0-9]{1,2})?)$'), 1, 64)
WL = lambda lo=220e-9, hi=1000e-9: number('m', lo, hi)                          # noqa: E731
KITS = ['water', 'pbs_buffer', 'fluorescein_standard', 'quinine_standard', 'atp_standard', 'glow_luciferase_reagent', 'flash_luciferase_reagent',
        'od_check_dye']
SPECIAL_SPECIES = ['atp', 'glow_luciferase', 'flash_luciferase', 'scatterer']
FAULTS = ['lamp_aging', 'injector_clog', 'bubble_in_line', 'plate_warped', 'chamber_heater_failure', 'detector_noise']
REACTION = obj(dict(source=SID, product=SID, k_per_s=number('1/s', 0.0, 10.0), ea_j_mol=number('J/mol', 0.0, 2.0e5), trigger=SID), optional=('ea_j_mol', 'trigger'))
BINDING = obj(dict(tracer=SID, receptor_mol_m3=number('mol/m3', 0.0, 1.0), kd_mol_m3=number('mol/m3', 1e-9, 1.0), anisotropy_free=number('1', 0.0, 0.4),
                   anisotropy_bound=number('1', 0.0, 0.4)), optional=('anisotropy_free', 'anisotropy_bound'))
COMPOSITION = dict(type='object', additionalProperties=number('mol/m3', 0.0, 1000.0))
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 288.15, 303.15), mains_voltage_v=number('V', 0.0, 264.0), noise_seed=integer(0, 2 ** 31 - 1)))
SHAKE = obj(dict(mode=enum(SHAKE_MODES), frequency_hz=number('1/s', 1.0, 12.0), duration_s=number('s', 0.0, 600.0), before_each_cycle=BOOL),
            optional=('frequency_hz', 'before_each_cycle'))
INJECTION = obj(dict(pump=enum(PUMPS), volume_m3=number('m3', 3e-9, 5e-7), speed_m3_s=number('m3/s', 1e-7, 4.2e-7), cycle=integer(1, 1000)), optional=('speed_m3_s', 'cycle'))
RES_SCHEMA = {'type': 'number', 'x-unit': 'm', 'enum': [1e-9, 2e-9, 5e-9, 10e-9]}
PROTOCOL = obj(dict(mode=enum(MODES), format=enum(FORMATS), wells=WELLS, wavelengths_m=array(WL(), 1, 8), spectrum_range_m=array(WL(), 2, 2),
                    resolution_m=RES_SCHEMA,
                    excitation_m=WL(240e-9, 900e-9), emission_m=WL(240e-9, 900e-9), excitation_bandwidth_m=number('m', 8e-9, 100e-9),
                    emission_bandwidth_m=number('m', 8e-9, 100e-9), emission_range_m=array(WL(240e-9, 900e-9), 2, 2), optic=enum(['top', 'bottom']),
                    flashes=integer(1, 200), gain=nullable(integer(0, 4095)), focal_height_m=nullable(number('m', 0.0, 0.025)), settling_s=number('s', 0.0, 10.0),
                    cycles=integer(1, 1000), cycle_time_s=number('s', 0.0, 86400.0), shake=SHAKE, injections=array(INJECTION, 0, 4),
                    integration_s=number('s', 0.01, 60.0), pathlength_correction=BOOL, read_order=enum(['row', 'serpentine', 'column']), emission2_m=WL(240e-9, 900e-9)),
               optional=('wavelengths_m', 'spectrum_range_m', 'resolution_m', 'excitation_m', 'emission_m', 'excitation_bandwidth_m', 'emission_bandwidth_m',
                         'emission_range_m', 'optic', 'flashes', 'gain', 'focal_height_m', 'settling_s', 'cycles', 'cycle_time_s', 'shake', 'injections',
                         'integration_s', 'pathlength_correction', 'read_order', 'emission2_m'))
PROTOCOL_EXAMPLE = dict(mode='abs_endpoint', format='96', wells=['A1:H12'], wavelengths_m=[4.5e-7], flashes=22)
RESULT_ROW = obj(dict(well=TEXT, cycle=INT, time_s=number('s'), value=nullable(number('1')), unit=TEXT, flags=array(TEXT, 0, 8), wavelength_m=nullable(number('m'))))
EVENT = obj(dict(time_s=number('s'), kind=enum(['system', 'plate', 'injector', 'run', 'warning', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
_RDR = ['reader_housing']

cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, _RDR, '宿主声明实验室条件：室温、电源电压（100/115/230 V自动量程）与噪声种子（只在首次声明时生效）',
    'evaluator', 'simulation')
cap('sample.provide', obj(dict(sample_id=SID, volume_m3=number('m3', 1e-7, 1e-3), composition=COMPOSITION, reactions=array(REACTION, 0, 8), binding=BINDING,
                               turbidity=number('1', 0.0, 5.0)), optional=('reactions', 'binding', 'turbidity')),
    obj(dict(sample_id=TEXT, volume_m3=number('m3'))), dict(sample_id='dye_mix', volume_m3=1e-5, composition={'fluorescein_in_basic_ethanol': 1e-4}),
    ['plate_carrier'], '评测者提供样品/储备液（组成对agent隐藏）：谱库物种浓度（mol/m³）、ATP、浊度，可选一级反应（源→产物、速率、活化能、触发物）与示踪剂–受体结合（Kd、自由/结合各向异性）',
    'evaluator', 'simulation')
cap('kit.provide', obj(dict(kit=enum(KITS), volume_m3=number('m3', 1e-6, 1e-3)), optional=('volume_m3',)), obj(dict(item_id=TEXT, volume_m3=number('m3'))),
    dict(kit='fluorescein_standard'), ['plate_carrier'], '提供缓冲液、荧光素/奎宁/ATP标准、萤光素酶试剂（辉光/闪光）与OD检查染料', 'evaluator', 'simulation')
cap('plate.prepare', obj(dict(plate_id=SID, format=enum(FORMATS), type=enum(PLATE_TYPES))), obj(dict(plate_id=TEXT, wells=INT)),
    dict(plate_id='p1', format='96', type='clear'), ['microplate'], '取一块空微孔板（6–1536孔；透明、紫外透明、黑、白、黑底透明）放在台面上', 'agent', 'robot_skill')
cap('well.dispense', obj(dict(plate_id=SID, wells=WELLS, source_id=SID, volume_m3=number('m3', 1e-10, 2e-5))), obj(dict(plate_id=TEXT, wells=INT, volume_m3=number('m3'))),
    dict(plate_id='p1', wells=['A1:A12'], source_id='fluorescein_standard', volume_m3=1e-7), ['microplate'],
    '向孔中加样（每孔同体积；多次加样按体积混合；板须在台面上）', 'agent', 'robot_skill')
cap('plate.seal', obj(dict(plate_id=SID, sealed=BOOL)), obj(dict(plate_id=TEXT, sealed=BOOL)), dict(plate_id='p1', sealed=True), ['microplate'],
    '贴上/揭下光学透明封板膜（减少蒸发）', 'agent', 'robot_skill')
cap('drawer.set', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['plate_carrier'], '板托架送出/收回（约2 s）', 'agent', 'device_command')
cap('plate.load', obj(dict(plate_id=SID)), obj(dict(plate_id=TEXT)), dict(plate_id='p1'), ['plate_carrier', 'microplate'], '把板放到送出的板托架上', 'agent', 'robot_skill')
cap('plate.unload', obj({}), obj(dict(plate_id=TEXT)), {}, ['plate_carrier', 'microplate'], '从送出的板托架上取下板放回台面', 'agent', 'robot_skill')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['reader_housing'], '读板仪通/断电（等价于电源键）', 'agent', 'device_command')
cap('power.press_switch', obj({}), obj(dict(on=BOOL)), {}, ['power_button'], '按一下前面板电源键（翻转通断）', 'agent', 'device_command')
cap('lid.set', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['top_lid'], '打开/关上顶盖（注射器、试剂瓶与滤光片滑片维护）', 'agent', 'robot_skill')
cap('injector.load_reagent', obj(dict(pump=enum(PUMPS), source_id=SID, volume_m3=number('m3', 1e-7, 5e-5))), obj(dict(pump=TEXT, volume_m3=number('m3'))),
    dict(pump='A', source_id='flash_luciferase_reagent', volume_m3=1e-5), ['injector_a', 'injector_b', 'injector_a_plunger', 'injector_b_plunger'], '把试剂倒入注射泵A/B的试剂瓶（须开顶盖）', 'agent', 'robot_skill')
cap('injector.prime', obj(dict(pump=enum(PUMPS), volume_m3=number('m3', 1e-7, 5e-6))), obj(dict(pump=TEXT, primed=BOOL, line_m3=number('m3'))),
    dict(pump='A', volume_m3=1e-6), ['injector_a', 'injector_b', 'injector_a_plunger', 'injector_b_plunger'], '注射泵灌注：试剂充满管路（排入废液槽）', 'agent', 'device_command')
cap('injector.backflush', obj(dict(pump=enum(PUMPS))), obj(dict(pump=TEXT, returned_m3=number('m3'))), dict(pump='A'), ['injector_a', 'injector_b', 'injector_a_plunger', 'injector_b_plunger'],
    '注射泵回抽：管路中的试剂退回试剂瓶', 'agent', 'device_command')
cap('incubator.set', obj(dict(temperature_k=nullable(number('K', 291.15, 318.15)))), obj(dict(temperature_k=nullable(number('K')))), dict(temperature_k=310.15),
    ['incubation_chamber'], '设定测量室温育温度（环境+3 °C至45 °C；null关闭）', 'agent', 'device_command')
cap('protocol.configure', PROTOCOL, obj(dict(mode=TEXT, wells=INT, plate_read_time_s=number('s'), cycles=INT)), PROTOCOL_EXAMPLE, ['reader_housing'],
    '设置测量程序（SMART Control等价）：模式（吸收终点/光谱、荧光终点/发射光谱、偏振、发光）、板型与孔、波长与带宽、光路顶/底、闪光数、增益或EDR、焦高、沉降、动力学循环、振荡与注射',
    'agent', 'configuration')
cap('focus.adjust', obj(dict(well=SID)), obj(dict(focal_height_m=number('m'))), dict(well='A1'), ['optical_head'], '在指定孔上自动调焦（0.1 mm分辨率）', 'agent', 'device_command')
cap('gain.adjust', obj(dict(well=SID, target_fraction=number('1', 0.05, 0.95)), optional=('target_fraction',)), obj(dict(gain=INT)), dict(well='A1'), ['optical_head'],
    '在指定孔上自动增益（荧光/偏振/发光程序）', 'agent', 'device_command')
cap('run.start', obj({}), obj(dict(run_id=TEXT, plate_read_time_s=number('s'), estimated_s=number('s'))), {}, ['reader_housing', 'optical_head'],
    '开始测量：按程序逐孔读数（动力学按循环；振荡、注射、沉降）', 'agent', 'device_command')
cap('run.stop', obj({}), obj(dict(run_id=nullable(TEXT), state=TEXT)), {}, ['reader_housing'], '停止当前测量（已读的数据保留）', 'agent', 'device_command')
cap('data.read', obj(dict(cycle=integer(0, 1000), max_rows=integer(1, 200000)), optional=('cycle', 'max_rows')),
    obj(dict(run_id=nullable(TEXT), mode=nullable(TEXT), state=TEXT, rows=array(RESULT_ROW, 0, 200000))), dict(cycle=0), ['optical_head'],
    '读取最近一次测量的逐孔数据（OD、RFU、mP或RLU；动力学为逐孔时间曲线；cycle=0为全部）。每行flags：EMPTY孔内无液体；OUT_OF_RANGE吸光超过4 OD；'
    'PATHLENGTH_CORRECTED_1CM已按孔内光程换算到1 cm；PATHLENGTH_NOT_APPLICABLE要求了光程校正但不适用（超量程、不透明板或空孔，value为原始OD）；'
    'SPECTRUM_MAX光谱模式中该行为光谱最大值（wavelength_m为其波长，全谱用data.spectrum）；EMISSION_2第二发射波长的读数；'
    'OVERFLOW固定增益下计数超过满量程（value为null）；LOW_SIGNAL偏振通道总强度低于暗噪声的10倍（空白或空孔，value为null）', 'agent', 'query')
cap('data.spectrum', obj(dict(well=SID)), obj(dict(well=TEXT, wavelength_m=array(number('m'), 0, 2000), value=array(nullable(number('1')), 0, 2000), unit=TEXT)),
    dict(well='A1'), ['optical_head'], '读取光谱模式下某孔的吸收或发射光谱', 'agent', 'query')
cap('data.analyze', obj(dict(cycle=integer(1, 1000), blank_wells=WELLS, standards=dict(type='object', additionalProperties=number('mol/m3', 0.0, 1000.0)),
                             fit=enum(['none', 'linear', '4pl']), positive_wells=WELLS, negative_wells=WELLS),
                        optional=('cycle', 'blank_wells', 'standards', 'fit', 'positive_wells', 'negative_wells')),
    obj(dict(blank=nullable(number('1')), fit=FREE_OBJ, concentrations=dict(type='object'), z_prime=nullable(number('1')))),
    dict(blank_wells=['H1:H3'], standards={'A1': 0.0, 'A2': 1e-4}, fit='linear'), ['reader_housing'], 'MARS等价分析：空白扣除、标准曲线（线性/四参数）与浓度、Z′因子', 'agent', 'analysis')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=INT)), dict(since_s=0.0), _RDR, '读取事件记录', 'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(name=TEXT, file=TEXT, entries=INT)), 0, 8), species=array(TEXT, 0, 400))), {}, _RDR,
    '模板库来源与谱库物种目录', 'agent', 'query')
cap('truth.inspect', obj(dict(plate_id=SID), optional=('plate_id',)), obj(dict(state=FREE_OBJ)), {}, ['microplate'],
    '评测者读取真值：孔体积、组成、温度、反应进度、结合分数，光学状态', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, _RDR, '账目：分配、注射、灌注、回抽、蒸发与废液体积', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL, pump=enum(PUMPS)), optional=('pump',)), obj(dict(faults=array(TEXT, 0, 32))),
    dict(fault='lamp_aging', active=True), _RDR, '故障注入：闪光灯老化、注射器堵塞、管路气泡、板翘曲（焦高）、温育加热失控、检测器噪声', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), _RDR, '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'alphascreen.read': 'AlphaScreen/AlphaLISA需要680 nm激光选件，不在所选配置中。', 'trf.read': '时间分辨荧光（TRF/TR-FRET）的滤光片组与门控读数不在所选配置中。',
    'acu.set': '大气控制单元（O2/CO2）为选件，不在所选配置中。', 'stacker.load': '微孔板堆叠器为选件，不在所选配置中。',
    'nephelometry.read': '散射比浊模式不在所选配置中。', 'barcode.read': '条码读取器为选件，不在所选配置中。',
    'data.export': '数据导出与LIMS接口由宿主电脑上的MARS软件完成（本资产只提供数据读取与分析）。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, _RDR, _v, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'reader_housing', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'reader_housing', '实验室条件是否已声明')
slot('power.on', BOOL, '1', 'setpoint', 'reader_housing', '读板仪是否通电')
slot('lamp.power', BOOL, '1', 'derived', 'power_button', '电源键指示灯（通电时亮）')
slot('lamp.busy', BOOL, '1', 'derived', 'status_led', '状态指示灯（测量、注射、调焦或灌注进行中时亮）')
slot('drawer.position_m', number('m'), 'm', 'derived', 'plate_carrier', '板托架送出距离（0收回，0.185 m送出；电动约2 s）')
slot('lid.angle_rad', number('rad'), 'rad', 'setpoint', 'top_lid', '顶盖开角（0关，1.25 rad开）')
slot('injector.a_plunger_m', number('m'), 'm', 'derived', 'injector_a_plunger', '注射泵A柱塞行程（0为排空位，0.04 m为满筒；柱塞从下方驱动，吸液时下行）')
slot('injector.b_plunger_m', number('m'), 'm', 'derived', 'injector_b_plunger', '注射泵B柱塞行程（0为排空位，0.04 m为满筒；柱塞从下方驱动，吸液时下行）')
slot('chamber.temperature_k', nullable(number('K')), 'K', 'sensor', 'incubation_chamber', '测量室温度读数（0.1 K；断电为空）')
slot('incubator.setpoint_k', nullable(number('K')), 'K', 'setpoint', 'incubation_chamber', '温育设定温度（null为关闭）')
slot('carrier.plate', nullable(FREE_OBJ), '1', 'derived', 'plate_carrier', '板托架上的板（标识、板型、类型、是否封膜）')
slot('plates.available', array(TEXT, 0, MAX_PLATES), '1', 'derived', 'microplate', '台面上可用的板')
slot('items.available', array(TEXT, 0, MAX_ITEMS), '1', 'derived', 'microplate', '可用的样品、标准与试剂')
slot('injectors.status', FREE_OBJ, '1', 'sensor', 'injector_a', '注射泵A/B：试剂瓶液量、管路是否灌注、试剂标识')
slot('protocol.current', nullable(FREE_OBJ), '1', 'setpoint', 'reader_housing', '当前测量程序摘要（模式、板型、孔数、循环、单板读数时间）')
slot('run.status', FREE_OBJ, '1', 'derived', 'reader_housing', '测量状态：运行号、状态、当前循环、进度')
slot('focus.height_m', nullable(number('m')), 'm', 'setpoint', 'optical_head', '当前焦高（自动调焦结果或程序设定；自孔板底面起）')
slot('warnings.active', array(TEXT, 0, 32), '1', 'derived', 'reader_housing', '当前提示（顶盖开、试剂不足、温度未到、溢出、OD超量程等）')
slot('truth.plates', FREE_OBJ, '1', 'process_truth', 'microplate', '各板各孔真值：体积、组成、温度、反应与结合', 'evaluator')
slot('truth.optics', FREE_OBJ, '1', 'process_truth', 'optical_head', '光学真值：灯输出、最佳焦高、噪声与背景', 'evaluator')
slot('truth.faults', array(TEXT, 0, 32), '1', 'process_truth', 'reader_housing', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'drawer.position_m', 'lid.angle_rad', 'injector.a_plunger_m', 'injector.b_plunger_m', 'lamp.power', 'lamp.busy'}


# ---------------------------------------------------------------- runtime
def _psat(T):
    """Water saturation pressure (Pa), Tetens / Magnus form (engineering)."""
    t = T - 273.15
    return 610.94 * math.exp(17.625 * t / (t + 243.04))


def _well(fmt):
    return plate_formats()[fmt]['well']


def _bottom_area(fmt):
    return _well(fmt)['bottom_area_m2']


class BmgClariostarplusRuntime:
    CARRIER_TRAVEL = 0.185
    LID_OPEN = 1.25

    def __init__(self, instance_id='bmg_clariostarplus_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.powered = False
        self.drawer = dict(open=False, pos=0.0)
        self.lid = 0.0
        self.chamber_T = None
        self.setpoint = None
        self.carrier = None                         # plate id on the carrier
        self.plates = {}
        self.items = {}
        self.inj = {p: dict(reagent=None, bottle=0.0, line=0.0, primed=False, syringe=0.0, comp={}, reactions=[]) for p in PUMPS}
        self.protocol = None
        self.focus = None
        self.gain_set = None
        self.run = None
        self.last_run = None
        self.busy_until = 0.0
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.warnings = {}
        self.revision = 0
        self.led = dict(dispensed_m3=0.0, injected_m3=0.0, primed_to_waste_m3=0.0, backflushed_m3=0.0, evaporated_m3=0.0, reagent_loaded_m3=0.0, plate_waste_m3=0.0)
        self._principals, self._requests, self._owners, self._caller = {}, {}, {}, None

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
        return BmgClariostarplusSession(self, name)

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

    def _fault(self, f, p=''):
        return (f, p) in self.faults

    def _busy(self):
        return self.run is not None or self.t < self.busy_until - 1e-9

    # ---- plate / well state
    def _new_plate(self, fmt, ptype):
        n = len(well_names(fmt))
        return dict(format=fmt, type=ptype, sealed=False, where='bench', V=np.zeros(n), amt={}, unmixed={}, mix_tau=np.full(n, ENG['mixing_tau_still_s']),
                    rxn={}, binding={}, turbidity=np.zeros(n), T=self.plant['room_k'] if self.plant else 295.15, edge=np.array([edge_well(fmt, w) for w in well_names(fmt)]),
                    names=well_names(fmt), flash_t0=np.full(n, np.nan), glow_t0=np.full(n, np.nan))

    def _species_add(self, pl, idx, comp, vol, reactions=None, binding=None, turbidity=0.0, unmixed=False):
        """Add vol (m3) of a solution with composition comp (mol/m3) to the wells idx (array of indices)."""
        target = pl['unmixed'] if unmixed else pl['amt']
        for s, c in comp.items():
            for d in (pl['amt'], pl['unmixed']):
                d.setdefault(s, np.zeros(len(pl['V'])))
            target[s][idx] += c * vol
        pl['turbidity'][idx] = (pl['turbidity'][idx] * pl['V'][idx] + turbidity * vol) / np.maximum(pl['V'][idx] + vol, 1e-15)
        pl['V'][idx] += vol
        for r in reactions or []:
            key = (r['source'], r['product'], r.get('trigger'))
            pl['rxn'].setdefault(key, dict(k=r['k_per_s'], ea=r.get('ea_j_mol', 0.0), wells=np.zeros(len(pl['V']), bool)))['wells'][idx] = True
            for s in (r['source'], r['product']) + ((r['trigger'],) if r.get('trigger') else ()):
                for d in (pl['amt'], pl['unmixed']):
                    d.setdefault(s, np.zeros(len(pl['V'])))
        if binding:
            b = pl['binding'].setdefault(binding['tracer'], dict(kd=binding['kd_mol_m3'], r_free=binding.get('anisotropy_free', ENG['anisotropy_free_default']),
                                                                 r_bound=binding.get('anisotropy_bound', ENG['anisotropy_bound_default']), receptor=np.zeros(len(pl['V']))))
            b['receptor'][idx] += binding['receptor_mol_m3'] * vol

    def _conc(self, pl, s, i, include_unmixed=True):
        V = pl['V'][i]
        if V <= 0:
            return 0.0
        a = pl['amt'].get(s)
        u = pl['unmixed'].get(s)
        return ((a[i] if a is not None else 0.0) + ((u[i] if u is not None else 0.0) if include_unmixed else 0.0)) / V

    def _pathlength(self, pl, i):
        fmt = pl['format']
        V = pl['V'][i]
        if V <= 0:
            return 0.0
        return max(V / _bottom_area(fmt) - ENG['meniscus_m'] * (_bottom_area(fmt) / 3.2e-5) ** 0.5, 0.0)

    # ---- physics
    def step(self, dt_s):
        """Advance the clock; the state is integrated on the fixed DT grid anchored at t = 0 (partition independent); between grid points the
        last state is held (a command acts from the grid point before it, at most DT earlier)."""
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = t_end
            self.t_grid = math.floor(t_end / DT + 1e-9) * DT                 # stay on the k*DT grid anchored at t = 0
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
        room = self.plant['room_k']
        # carrier and lid motion
        tgt = self.CARRIER_TRAVEL if self.drawer['open'] else 0.0
        v = self.CARRIER_TRAVEL / ENG['carrier_motion_s']
        if self.powered:
            self.drawer['pos'] += max(-v * h, min(v * h, tgt - self.drawer['pos']))
        # chamber temperature: incubator set point (heater) or ambient + electronics
        if self.chamber_T is None:
            self.chamber_T = room
        heat = self.powered and self.setpoint is not None and not self._fault('chamber_heater_failure')
        T_target = (self.setpoint if heat else room + (ENG['electronics_rise_k'] if self.powered else 0.0))
        if self.powered and self._fault('chamber_heater_failure') and self.setpoint is not None:
            T_target = self.setpoint + 4.0
        if self.drawer['pos'] > 0.01:
            T_target = T_target - ENG['open_drawer_loss'] * (T_target - room)
        dT = (T_target - self.chamber_T) * (1.0 - math.exp(-h / ENG['chamber_tau_s']))
        self.chamber_T += max(-ENG['heater_rate_k_s'] * h, min(ENG['heater_rate_k_s'] * h, dT))
        # plates: temperature, evaporation, mixing, reactions
        for pid, pl in self.plates.items():
            env = self.chamber_T if (pid == self.carrier and self.drawer['pos'] < 0.01) else room
            pl['T'] += (env - pl['T']) * (1.0 - math.exp(-h / ENG['plate_tau_s']))
            self._evaporate(pl, h)
            self._mix(pl, h)
            self._react(pl, h)
        self._run_machine(h)

    def _evaporate(self, pl, h):
        filled = pl['V'] > 0
        if not filled.any():
            return
        rate = ENG['evaporation_m3_s_96'] * (_bottom_area(pl['format']) / 3.2e-5) * _psat(pl['T']) / _psat(298.15)
        rate *= ENG['sealed_evaporation_factor'] if pl['sealed'] else 1.0
        dv = np.where(filled, rate * h * np.where(pl['edge'], ENG['edge_evaporation_factor'], 1.0), 0.0)
        dv = np.minimum(dv, pl['V'])
        pl['V'] -= dv
        self.led['evaporated_m3'] += float(dv.sum())

    def _mix(self, pl, h):
        if not pl['unmixed']:
            return
        f = 1.0 - np.exp(-h / np.maximum(pl['mix_tau'], 1e-3))
        for s, u in pl['unmixed'].items():
            if not u.any():
                continue
            d = u * f
            pl['amt'][s] += d
            u -= d
        shaking = self.run is not None and self.run.get('shaking_until', 0.0) > self.t and pl is self.plates.get(self.carrier)
        pl['mix_tau'] = np.where(pl['mix_tau'] < ENG['mixing_tau_still_s'], pl['mix_tau'] * math.exp(h / ENG['mixing_relax_s']), ENG['mixing_tau_still_s'])
        if shaking:
            pl['mix_tau'][:] = np.minimum(pl['mix_tau'], ENG['mixing_tau_shaking_s'])

    def _react(self, pl, h):
        for (src, prod, trig), r in pl['rxn'].items():
            k = r['k'] * math.exp(-r['ea'] / R_GAS * (1.0 / pl['T'] - 1.0 / 298.15))
            a = pl['amt'][src]
            if trig:
                V = np.maximum(pl['V'], 1e-15)
                ct = pl['amt'][trig] / V
                act = ct / (ct + ENG['trigger_half_mol_m3'])
            else:
                act = 1.0
            d = a * (1.0 - np.exp(-k * act * h)) * r['wells']
            a -= d
            pl['amt'][prod] += d

    # ---- optics
    def _lamp(self):
        return ENG['lamp_aged_output'] if self._fault('lamp_aging') else 1.0

    def _focus_factor(self, pl, i, z):
        if z is None:
            z = ENG['default_focal_height_m']
        z_opt = self._z_opt(pl, i)
        return math.exp(-((z - z_opt) / ENG['focus_width_m']) ** 2)

    def _z_opt(self, pl, i):
        h = pl['V'][i] / _bottom_area(pl['format'])
        z = ENG['focus_depth_fraction'] * h + ENG['plate_bottom_m']
        return z + (ENG['warp_offset_m'] if self._fault('plate_warped') else 0.0)

    def _species_list(self, pl):
        return [s for s in pl['amt'] if s in spectra()]

    def _absorbance_true(self, pl, i, wl):
        """Decadic absorbance of well i at wl: sum of epsilon (decadic, m2/mol) x c x path length + turbidity + plate background."""
        l = self._pathlength(pl, i)
        a = sum(eps_at(s, wl) * self._conc(pl, s, i) * l for s in self._species_list(pl))
        a += pl['turbidity'][i] * l / 0.01 * (wl / 600e-9) ** -2
        return a + self._plate_od(pl, wl)

    def _plate_od(self, pl, wl):
        t = pl['type']
        if t in ('black', 'white'):
            return 6.0
        nm = wl * 1e9
        if t == 'uv_transparent':
            return 0.045 + 0.4 * math.exp(-(nm - 220.0) / 10.0)
        return 0.045 + 3.0 * math.exp(-(nm - 280.0) / 14.0) if nm < 400 else 0.045        # polystyrene cut-off below about 320 nm (engineering)

    def _abs_read(self, pl, i, wl, flashes, k_noise):
        A = self._absorbance_true(pl, i, wl)
        s = ENG['stray_light_fraction']
        T = 10.0 ** (-A)
        A_obs = -math.log10((T + s) / (1.0 + s)) * (1.0 + ENG['photometric_scale_error'])
        sig = math.sqrt((ENG['abs_relative_noise'] * A_obs) ** 2 + (ENG['abs_transmittance_noise'] * 10 ** A_obs / LN10) ** 2 / max(flashes, 1) * 22.0)
        if self._fault('detector_noise'):
            sig *= 5.0
        return float(A_obs + sig * _gauss(self.seed ^ _h(pl['names'][i]), k_noise, 3) / self._lamp() ** 0.5)

    def _fi_signal(self, pl, i, ex, em, bw_ex, bw_em, optic, z):
        """Mean fluorescence counts (before gain normalisation) of well i: absorbed excitation x quantum yield x emission fraction in the band x
        collection (focus, optic) x inner-filter correction; plus plate / buffer background."""
        l = self._pathlength(pl, i)
        if pl['V'][i] <= 0:
            return self._fi_background(pl, i, optic)
        A_ex = sum(eps_at(s, ex) * self._conc(pl, s, i) * l for s in self._species_list(pl))
        A_em = sum(eps_at(s, em) * self._conc(pl, s, i) * l for s in self._species_list(pl))
        ife = (1.0 - 10 ** (-A_ex)) / max(A_ex * LN10, 1e-12) if A_ex > 1e-6 else 1.0
        ife *= 10 ** (-0.5 * A_em)
        sig = 0.0
        for s in self._species_list(pl):
            c = self._conc(pl, s, i)
            q = quantum_yield(s)
            if c <= 0 or q <= 0:
                continue
            sig += c * eps_at(s, ex) * LN10 * q * emission_fraction(s, em - bw_em / 2, em + bw_em / 2)
        coll = self._focus_factor(pl, i, z) if optic == 'top' else ENG['bottom_collection'] * (1.0 if pl['type'] in ('black_clear_bottom', 'clear', 'uv_transparent') else 0.02)
        V_det = min(pl['V'][i], ENG['detection_volume_m3'] * (_bottom_area(pl['format']) / 1.0e-5))
        S = ENG['fi_counts_per_unit'] * sig * ife * coll * (bw_ex / 10e-9) * V_det / 2.0e-8 * self._lamp()
        return S + self._fi_background(pl, i, optic)

    def _fi_background(self, pl, i, optic):
        bg = dict(black=1.0, black_clear_bottom=1.5, white=12.0, clear=6.0, uv_transparent=5.0)[pl['type']] * ENG['fi_plate_background_counts']
        return bg * (ENG['bottom_background_factor'] if optic == 'bottom' else 1.0)

    def _gain_factor(self, gain):
        """Counts scale of a fixed PMT gain relative to the EDR reference (engineering power law)."""
        if gain is None:
            return 1.0
        return (max(gain, 1) / ENG['gain_reference']) ** ENG['gain_exponent']

    def _noisy_counts(self, mean, flashes, k_noise, ch, dark_scale=1.0):
        """Mean counts are normalised to 20 flashes; shot and dark noise average down with the square root of the flash number.  dark_scale multiplies
        the dark variance (luminescence: proportional to the integration time, so the detection limit improves with its square root)."""
        sd = math.sqrt((max(mean, 0.0) * ENG['counts_per_photon'] + 0.5 * ENG['dark_counts_sd'] ** 2 * dark_scale) * 20.0 / max(flashes, 1))
        if self._fault('detector_noise'):
            sd *= 5.0
        return float(mean + sd * _gauss(self.seed, k_noise, ch))

    @staticmethod
    def _reported(v):
        """Reported PMT counts: the electronic dark offset is added and the ADC cannot go below zero (the blank wells keep a positive reading)."""
        return max(v + ENG['dark_offset_counts'], 0.0)

    def _fp_read(self, pl, i, P, k_noise):
        """Parallel / perpendicular intensities from the intensity-weighted anisotropy of free and bound tracer (G factor); returns mP."""
        S = self._fi_signal(pl, i, P['excitation_m'], P['emission_m'], P['excitation_bandwidth_m'], P['emission_bandwidth_m'], P['optic'], P.get('focal_height_m') or self.focus)
        bg = self._fi_background(pl, i, P['optic'])
        r_num, w = 0.0, 0.0
        for tr, b in pl['binding'].items():
            Lt = self._conc(pl, tr, i)
            Rt = b['receptor'][i] / max(pl['V'][i], 1e-15)
            if Lt <= 0:
                continue
            s_ = Lt + Rt + b['kd']
            frac = ((s_ - math.sqrt(max(s_ * s_ - 4 * Lt * Rt, 0.0))) / 2.0) / Lt
            r_num += Lt * (frac * b['r_bound'] + (1 - frac) * b['r_free'])
            w += Lt
        r = r_num / w if w > 0 else ENG['anisotropy_free_default']
        F = max(S - bg, 0.0) * ENG['filter_signal_factor']               # polarisation reads through the FP filter set
        I_par = F * (1 + 2 * r) / 3.0 + bg / 2
        I_perp = F * (1 - r) / 3.0 + bg / 2
        G = ENG['g_factor_true']
        flashes = P.get('flashes', 100)
        ip = max(self._noisy_counts(I_par * self._gain_factor(P.get('gain')), flashes, k_noise, 21), 0.0)     # PMT counts cannot be negative
        iq = max(self._noisy_counts(I_perp * G * self._gain_factor(P.get('gain')), flashes, k_noise, 23), 0.0)
        Gs = ENG['g_factor_set']                                      # the perpendicular channel reads G x; the instrument divides it out
        dark_sd = math.sqrt(0.5 * ENG['dark_counts_sd'] ** 2 * 20.0 / max(flashes, 1))
        if ip + iq / Gs < 10.0 * dark_sd:                             # no measurable emission (blank / empty well): polarisation undefined
            return None, r
        pol = (ip - iq / Gs) / (ip + iq / Gs)
        return float(1000.0 * pol), r

    def _lum_signal(self, pl, i, integ):
        if pl['V'][i] <= 0:
            return ENG['lum_dark_counts']
        atp = self._conc(pl, 'atp', i)
        glow = self._conc(pl, 'glow_luciferase', i, include_unmixed=False)
        flash = self._conc(pl, 'flash_luciferase', i, include_unmixed=False)
        L = 0.0
        if glow > 0:
            L += ENG['lum_glow_counts_per_mol'] * atp * pl['V'][i] * min(glow / ENG['luciferase_saturation_mol_m3'], 1.0)
        if flash > 0:
            t0 = pl['flash_t0'][i]
            decay = math.exp(-(self.t - t0) / ENG['flash_decay_s']) if np.isfinite(t0) else 1.0
            L += ENG['lum_flash_counts_per_mol'] * atp * pl['V'][i] * min(flash / ENG['luciferase_saturation_mol_m3'], 1.0) * decay
        if glow > 0:
            L *= math.exp(-max(self.t - pl['glow_t0'][i], 0.0) / ENG['glow_decay_s']) if np.isfinite(pl['glow_t0'][i]) else 1.0
        return (L + ENG['lum_dark_counts']) * integ

    # ---- protocol timing (documented read times; other formats and modes engineering)
    def _plate_read_time(self, P, n_sel):
        fmt, mode = P['format'], P['mode']
        n_fmt = len(well_names(fmt))
        if mode in ('abs_endpoint', 'fi_endpoint', 'fp_endpoint'):
            n = P.get('flashes', 22 if mode == 'abs_endpoint' else 20)
            key = fmt if fmt in ('96', '384', '1536') else '96'
            t1, t10 = DOC['read_time_flying_s'][key], DOC['read_time_10_flashes_s'][key]
            full = t1 + (t10 - t1) * (n - 1) / 9.0
            if key != fmt:
                full *= max(n_fmt / 96.0, 0.25)
            return full * n_sel / n_fmt + ENG['read_overhead_s'] * (2.0 if mode == 'fp_endpoint' else 1.0)
        if mode == 'abs_spectrum':
            return n_sel * ENG['abs_spectrum_s_per_well'] + ENG['read_overhead_s']
        if mode == 'fi_em_spectrum':
            lo, hi = P['emission_range_m']
            pts = int(round((hi - lo) / 5e-9)) + 1
            return n_sel * (0.2 + pts * ENG['em_scan_s_per_point'] * max(1.0, P.get('flashes', 10) / 10.0)) + ENG['read_overhead_s']
        return n_sel * (P.get('integration_s', 1.0) + ENG['lum_move_s']) + ENG['read_overhead_s']

    def _ordered(self, P, wells):
        """Well reading order: row by row (default), serpentine (alternate rows reversed) or column by column."""
        fmt, order = P['format'], P.get('read_order', 'row')
        idx = {w: well_index(fmt, w) for w in wells}
        if order == 'column':
            return sorted(wells, key=lambda w: (idx[w][1], idx[w][0]))
        if order == 'serpentine':
            return sorted(wells, key=lambda w: (idx[w][0], idx[w][1] if idx[w][0] % 2 == 0 else -idx[w][1]))
        return sorted(wells, key=lambda w: idx[w])

    def _schedule(self, P, wells, t0):
        """Event list (time, order, kind, data) of one run: per cycle injections (plate mode), shaking, settling, then the wells in order."""
        wells = self._ordered(P, wells)
        n = len(wells)
        t_read = self._plate_read_time(P, n)
        ev, k = [], 0
        cyc_t = P.get('cycle_time_s', 0.0)
        t_prev_end, t_prev_start = t0, None
        for c in range(1, P.get('cycles', 1) + 1):
            # a cycle starts one cycle time after the previous start, but never before the previous cycle has ended (an overrun delays the later cycles)
            t = t0 if t_prev_start is None else max(t_prev_start + cyc_t, t_prev_end)
            t_prev_start = t
            for inj in P.get('injections', []):
                if inj.get('cycle', 1) != c:
                    continue
                per = inj['volume_m3'] / inj.get('speed_m3_s', 3.0e-7) + ENG['injection_move_s']
                for w in wells:
                    ev.append((t, k, 'inject', (w, inj['pump'], inj['volume_m3'], inj.get('speed_m3_s', 3.0e-7))))
                    k += 1
                    t += per
            sh = P.get('shake')
            if sh and sh['mode'] != 'none' and (c == 1 or sh.get('before_each_cycle', False)):
                ev.append((t, k, 'shake', sh['duration_s']))
                k += 1
                t += sh['duration_s']
            t += P.get('settling_s', 0.0)
            dt = (t_read - ENG['read_overhead_s']) / n
            t += ENG['read_overhead_s'] / 2
            for j, w in enumerate(wells):
                ev.append((t + j * dt, k, 'read', (w, c)))
                k += 1
            t_prev_end = t + n * dt + ENG['read_overhead_s'] / 2
            ev.append((t_prev_end, k, 'cycle_end', c))
            k += 1
        ev.sort()
        return ev, t_read

    def _cycle_budget(self, P, n):
        inj = sum((i['volume_m3'] / i.get('speed_m3_s', 3.0e-7) + ENG['injection_move_s']) * n for i in P.get('injections', []))
        sh = P.get('shake')
        shake = sh['duration_s'] if sh and sh['mode'] != 'none' and sh.get('before_each_cycle', False) else 0.0
        return self._plate_read_time(P, n) + shake + P.get('settling_s', 0.0) + inj

    # ---- run state machine
    def _run_machine(self, h):
        R = self.run
        if R is None:
            return
        pl = self.plates[R['plate']]
        while R['next'] < len(R['events']) and R['events'][R['next']][0] <= self.t + h + 1e-9:
            t_ev, _, kind, data = R['events'][R['next']]
            R['next'] += 1
            if kind == 'inject':
                w, pump, vol, speed = data
                self._inject(pl, w, pump, vol, speed)
            elif kind == 'shake':
                R['shaking_until'] = self.t + h + data
            elif kind == 'read':
                self._read_well(R, pl, *data)
            elif kind == 'cycle_end':
                R['cycle'] = data
        if R['next'] >= len(R['events']):
            R['state'] = 'completed'
            R['t_done'] = self.t + h
            self._event('run', 'RUN_COMPLETED', f'测量{R["run_id"]}完成：{len(R["wells"])}孔×{R["P"].get("cycles", 1)}循环')
            self.last_run, self.run = R, None

    def _inject(self, pl, w, pump, vol, speed):
        J = self.inj[pump]
        i = pl['names'].index(w)
        clog = 0.6 if self._fault('injector_clog', pump) else 1.0
        bub = 0.85 if self._fault('bubble_in_line', pump) else 1.0
        d = min(vol * clog * bub, J['bottle'] + J['line'])                   # volume leaving the nozzle
        if d <= 0:
            self.warnings['INJECTOR_EMPTY_' + pump] = f'注射泵{pump}没有试剂'
            return
        refill = min(d, J['bottle'])                                          # the syringe refills the line from the bottle by the same volume
        J['bottle'] -= refill
        J['line'] -= d - refill
        v = d
        if pl['V'][i] + v > _well(pl['format'])['max_volume_m3'] * 1.0001:
            v = max(_well(pl['format'])['max_volume_m3'] - pl['V'][i], 0.0)
            self.warnings['WELL_OVERFLOW'] = f'{w}注射后溢出（多余试剂进入废液）'
            self.led['plate_waste_m3'] += d - v
        self.led['injected_m3'] += v
        idx = np.array([i])
        self._species_add(pl, idx, J['comp'], v, reactions=J['reactions'], unmixed=True)
        pl['mix_tau'][i] = ENG['mixing_tau_injection_s'] * (3.0e-7 / speed)
        if J['comp'].get('flash_luciferase'):
            pl['flash_t0'][i] = self.t
        J['syringe'] = (J['syringe'] + 0.37 * v / ENG['syringe_volume_m3']) % 1.0

    def _read_well(self, R, pl, w, cycle):
        P = R['P']
        i = pl['names'].index(w)
        k = self.tick * 4096 + R['k_read']
        R['k_read'] += 1
        mode = P['mode']
        flags = []
        if pl['V'][i] <= 0:
            flags.append('EMPTY')
        t = self.t
        if mode == 'abs_endpoint':
            for wl in P['wavelengths_m']:
                v = self._abs_read(pl, i, wl, P.get('flashes', 22), k)
                fl = list(flags)
                if v > DOC['od_range'][1]:
                    fl.append('OUT_OF_RANGE')
                if P.get('pathlength_correction'):
                    l = self._pathlength(pl, i)
                    if 'OUT_OF_RANGE' in fl or self._plate_od(pl, wl) >= DOC['od_range'][1]:
                        fl.append('PATHLENGTH_NOT_APPLICABLE')                # saturated well or opaque plate: no 1 cm correction of a clipped value
                    elif l <= 0:
                        fl.append('PATHLENGTH_NOT_APPLICABLE')                # empty well: no liquid path to correct
                    else:
                        v = (v - self._plate_od(pl, wl)) * 0.01 / (l * (1.0 + ENG['pathlength_estimate_sd'] * _gauss(self.seed, k, 41)))
                        fl.append('PATHLENGTH_CORRECTED_1CM')
                R['rows'].append(dict(well=w, cycle=cycle, time_s=t, value=round(v, 4), unit='OD', flags=fl, wavelength_m=wl))
                k += 1
            return
        if mode == 'abs_spectrum':
            lo, hi = P['spectrum_range_m']
            res = P.get('resolution_m', 2e-9)
            grid = np.arange(lo, hi + res / 2, res)
            vals = [round(self._abs_read(pl, i, float(x), P.get('flashes', 10), k + j), 4) for j, x in enumerate(grid)]
            R['spectra'][w] = dict(wavelength_m=[float(x) for x in grid], value=vals, unit='OD')
            pk = int(np.argmax(vals))
            R['rows'].append(dict(well=w, cycle=cycle, time_s=t, value=vals[pk], unit='OD', flags=flags + ['SPECTRUM_MAX'], wavelength_m=float(grid[pk])))
            return
        if mode == 'fi_em_spectrum':
            lo, hi = P['emission_range_m']
            grid = np.arange(lo, hi + 2.5e-9, 5e-9)
            vals = []
            for j, em in enumerate(grid):
                m = self._fi_signal(pl, i, P['excitation_m'], float(em), P['excitation_bandwidth_m'], P['emission_bandwidth_m'], P.get('optic', 'top'),
                                    P.get('focal_height_m') or self.focus)
                vals.append(round(self._reported(self._noisy_counts(m * self._gain_factor(P.get('gain')), P.get('flashes', 10), k + j, 5)), 1))
            R['spectra'][w] = dict(wavelength_m=[float(x) for x in grid], value=vals, unit='RFU')
            pk = int(np.argmax(vals))
            R['rows'].append(dict(well=w, cycle=cycle, time_s=t, value=vals[pk], unit='RFU', flags=flags + ['SPECTRUM_MAX'], wavelength_m=float(grid[pk])))
            return
        if mode == 'fi_endpoint':
            m = self._fi_signal(pl, i, P['excitation_m'], P['emission_m'], P['excitation_bandwidth_m'], P['emission_bandwidth_m'], P.get('optic', 'top'),
                                P.get('focal_height_m') or self.focus) * self._gain_factor(P.get('gain'))
            v = self._reported(self._noisy_counts(m, P.get('flashes', 20), k, 7))
            if P.get('gain') is not None and v > ENG['counts_full_scale']:
                R['rows'].append(dict(well=w, cycle=cycle, time_s=t, value=None, unit='RFU', flags=flags + ['OVERFLOW'], wavelength_m=P['emission_m']))
                return
            R['rows'].append(dict(well=w, cycle=cycle, time_s=t, value=round(v, 1), unit='RFU', flags=flags, wavelength_m=P['emission_m']))
            if P.get('emission2_m'):                                        # second emission (ratiometric / FRET-style read, simultaneous detection)
                m2 = self._fi_signal(pl, i, P['excitation_m'], P['emission2_m'], P['excitation_bandwidth_m'], P['emission_bandwidth_m'], P.get('optic', 'top'),
                                     P.get('focal_height_m') or self.focus) * self._gain_factor(P.get('gain'))
                v2 = self._reported(self._noisy_counts(m2, P.get('flashes', 20), k + 1, 8))
                R['rows'].append(dict(well=w, cycle=cycle, time_s=t, value=round(v2, 1), unit='RFU', flags=list(flags) + ['EMISSION_2'], wavelength_m=P['emission2_m']))
            return
        if mode == 'fp_endpoint':
            mp, r = self._fp_read(pl, i, P, k)
            R['rows'].append(dict(well=w, cycle=cycle, time_s=t, value=None if mp is None else round(mp, 2), unit='mP',
                                  flags=flags + ([] if mp is not None else ['LOW_SIGNAL']), wavelength_m=P['emission_m']))
            return
        integ = P.get('integration_s', 1.0)
        m = self._lum_signal(pl, i, integ) * self._gain_factor(P.get('gain'))
        v = self._reported(self._noisy_counts(m, 20, k, 9, dark_scale=integ))  # 1 s integration ~ the 20-flash reference; dark variance grows with integration
        R['rows'].append(dict(well=w, cycle=cycle, time_s=t, value=round(v, 1), unit='RLU', flags=flags, wavelength_m=None))

    def _update_warnings(self):
        if self.lid > 1e-6 and self.powered:
            self.warnings['LID_OPEN'] = '顶盖打开：不能测量'
        else:
            self.warnings.pop('LID_OPEN', None)
        for p, J in self.inj.items():
            key = 'REAGENT_LOW_' + p
            if J['reagent'] and J['bottle'] < ENG['reagent_low_m3']:
                self.warnings[key] = f'注射泵{p}试剂瓶液量低'
            else:
                self.warnings.pop(key, None)
        if self.setpoint is not None and self.powered and self.chamber_T is not None and abs(self.chamber_T - self.setpoint) > 0.5:
            self.warnings['TEMPERATURE_NOT_REACHED'] = '温育温度未到设定值'
        else:
            self.warnings.pop('TEMPERATURE_NOT_REACHED', None)

    # ---- read
    def read(self, visibility='agent'):
        out = {'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.on': self.powered, 'lamp.power': self.powered,
               'lamp.busy': bool(self.powered and self._busy()), 'drawer.position_m': self.drawer['pos'], 'lid.angle_rad': self.lid}
        for p in PUMPS:
            out[f'injector.{p.lower()}_plunger_m'] = ENG['plunger_travel_m'] * self.inj[p]['syringe']
        out['chamber.temperature_k'] = None if (not self.powered or self.chamber_T is None) else round(self.chamber_T, 1)
        out['incubator.setpoint_k'] = self.setpoint
        pl = self.plates.get(self.carrier)
        out['carrier.plate'] = None if pl is None else dict(plate_id=self.carrier, format=pl['format'], type=pl['type'], sealed=pl['sealed'])
        out['plates.available'] = sorted(k for k, v in self.plates.items() if v['where'] == 'bench')
        out['items.available'] = sorted(self.items)
        out['injectors.status'] = {p: dict(reagent=J['reagent'], bottle_m3=J['bottle'], line_m3=J['line'], primed=J['primed']) for p, J in self.inj.items()}
        P = self.protocol
        out['protocol.current'] = None if P is None else dict(mode=P['mode'], format=P['format'], wells=len(P['_wells']), cycles=P.get('cycles', 1),
                                                              plate_read_time_s=P['_t_read'], gain=P.get('gain'), focal_height_m=P.get('focal_height_m'))
        R = self.run or self.last_run
        out['run.status'] = dict(run_id=R['run_id'] if R else None, state=R['state'] if R else 'none', cycle=R.get('cycle', 0) if R else 0,
                                 cycles=R['P'].get('cycles', 1) if R else 0, progress=(R['next'] / max(len(R['events']), 1)) if R else 0.0)
        out['focus.height_m'] = self.focus
        out['warnings.active'] = sorted(self.warnings)
        if visibility != 'agent':
            tp = {}
            for pid, p_ in self.plates.items():
                names = p_['names']
                filled = [j for j in range(len(names)) if p_['V'][j] > 0]
                tp[pid] = dict(format=p_['format'], type=p_['type'], where=p_['where'], sealed=p_['sealed'], temperature_k=p_['T'],
                               wells={names[j]: dict(volume_m3=float(p_['V'][j]), conc_mol_m3={s: float(self._conc(p_, s, j)) for s in p_['amt'] if self._conc(p_, s, j) > 0},
                                                     unmixed_mol={s: float(u[j]) for s, u in p_['unmixed'].items() if u[j] > 0}) for j in filled[:400]})
            out['truth.plates'] = tp
            cp = self.plates.get(self.carrier)
            out['truth.optics'] = dict(lamp_output=self._lamp(), g_factor=ENG['g_factor_true'], stray_light=ENG['stray_light_fraction'],
                                       carrier_focus_optimum_m=(self._z_opt(cp, int(np.argmax(cp['V']))) if (cp is not None and cp['V'].any()) else None))
            out['truth.faults'] = sorted(f'{f}:{c}' if c else f for f, c in self.faults)
        return out

    # ---------------------------------------------------------------- commands
    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(room_k=float(room_k), mains_voltage_v=float(mains_voltage_v))
        self.seed = int(noise_seed)
        if self.powered and not self._mains_ok():
            self._power_off('电源电压超出允许范围：断电')
        if first:
            self.chamber_T = float(room_k)
            self._event('system', 'PLANT', f'实验室：室温{room_k - 273.15:.1f} °C、电源{mains_voltage_v:.0f} V')
        return dict(configured=True)

    def _mains_ok(self):
        v = self.plant['mains_voltage_v']
        return any(abs(v - nom) <= 0.1 * nom for nom in DOC['mains_v'])

    def _cmd_sample_provide(self, sample_id, volume_m3, composition, reactions=(), binding=None, turbidity=0.0):
        self._need_plant()
        if sample_id in self.items:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.items) >= MAX_ITEMS:
            raise ActionRejected('TOO_MANY_ITEMS')
        for s in list(composition) + [x for r in reactions for x in (r['source'], r['product'], r.get('trigger')) if x] + ([binding['tracer']] if binding else []):
            if s not in spectra() and s not in SPECIAL_SPECIES:
                raise ActionRejected('UNKNOWN_SPECIES')
        self.items[sample_id] = dict(kind='sample', V=float(volume_m3), comp={k: float(v) for k, v in composition.items()}, reactions=[dict(r) for r in reactions],
                                     binding=dict(binding) if binding else None, turbidity=float(turbidity))
        self._own('item:' + sample_id)
        self._event('system', 'SAMPLE', f'样品{sample_id}（{volume_m3 * 1e6:.1f} ml）')
        return dict(sample_id=sample_id, volume_m3=float(volume_m3))

    def _cmd_kit_provide(self, kit, volume_m3=1e-4):
        self._need_plant()
        comp = dict(KIT_COMPOSITION[kit])
        if kit in self.items:
            self.items[kit]['V'] += volume_m3
        else:
            if len(self.items) >= MAX_ITEMS:
                raise ActionRejected('TOO_MANY_ITEMS')
            self.items[kit] = dict(kind='kit', V=float(volume_m3), comp=comp, reactions=[], binding=None, turbidity=0.0)
        return dict(item_id=kit, volume_m3=self.items[kit]['V'])

    def _cmd_plate_prepare(self, plate_id, format, type):
        self._need_plant()
        if plate_id in self.plates:
            raise ActionRejected('PLATE_EXISTS')
        if len(self.plates) >= MAX_PLATES:
            raise ActionRejected('TOO_MANY_PLATES')
        self.plates[plate_id] = self._new_plate(format, type)
        self._own('plate:' + plate_id)
        return dict(plate_id=plate_id, wells=len(self.plates[plate_id]['names']))

    def _plate(self, plate_id):
        pl = self.plates.get(plate_id)
        if pl is None:
            raise ActionRejected('UNKNOWN_PLATE')
        return pl

    def _cmd_well_dispense(self, plate_id, wells, source_id, volume_m3):
        self._need_plant()
        pl = self._plate(plate_id)
        if pl['where'] != 'bench':
            raise ActionRejected('PLATE_IN_READER')
        names = expand_wells(pl['format'], wells)
        it = self.items.get(source_id)
        if it is None:
            raise ActionRejected('UNKNOWN_ITEM')
        need = volume_m3 * len(names)
        if it['V'] < need - 1e-15:
            raise ActionRejected('INSUFFICIENT_VOLUME')
        idx = np.array([pl['names'].index(w) for w in names])
        if (pl['V'][idx] + volume_m3 > _well(pl['format'])['max_volume_m3'] * 1.0001).any():
            raise ActionRejected('WELL_OVERFLOW')
        it['V'] -= need
        self._species_add(pl, idx, it['comp'], volume_m3, reactions=it['reactions'], binding=it['binding'], turbidity=it['turbidity'])
        pl['mix_tau'][idx] = ENG['mixing_tau_pipette_s']
        if it['comp'].get('glow_luciferase'):
            pl['glow_t0'][idx] = np.where(np.isfinite(pl['glow_t0'][idx]), pl['glow_t0'][idx], self.t)
        self.led['dispensed_m3'] += need
        return dict(plate_id=plate_id, wells=len(names), volume_m3=float(volume_m3))

    def _cmd_plate_seal(self, plate_id, sealed):
        self._need_plant()
        pl = self._plate(plate_id)
        if pl['where'] != 'bench':
            raise ActionRejected('PLATE_IN_READER')
        pl['sealed'] = bool(sealed)
        return dict(plate_id=plate_id, sealed=pl['sealed'])

    def _cmd_drawer_set(self, open):
        self._need_power()
        if self._busy():
            raise ActionRejected('BUSY')
        self.drawer['open'] = bool(open)
        return dict(open=self.drawer['open'])

    def _carrier_out(self):
        if not self.drawer['open'] or self.drawer['pos'] < self.CARRIER_TRAVEL - 1e-9:
            raise ActionRejected('DRAWER_CLOSED')

    def _cmd_plate_load(self, plate_id):
        self._need_plant()
        pl = self._plate(plate_id)
        self._carrier_out()
        if self.carrier is not None:
            raise ActionRejected('CARRIER_OCCUPIED')
        if pl['where'] != 'bench':
            raise ActionRejected('PLATE_IN_READER')
        pl['where'] = 'carrier'
        self.carrier = plate_id
        return dict(plate_id=plate_id)

    def _cmd_plate_unload(self):
        self._need_plant()
        self._carrier_out()
        if self.carrier is None:
            raise ActionRejected('CARRIER_EMPTY')
        if self._busy():
            raise ActionRejected('BUSY')
        pid = self.carrier
        self.plates[pid]['where'] = 'bench'
        self.carrier = None
        return dict(plate_id=pid)

    def _power_off(self, why):
        self.powered = False
        if self.run is not None:
            R = self.run
            R['state'] = 'stopped'
            self.last_run, self.run = R, None
        self.busy_until = 0.0
        self._event('system', 'POWER_OFF', why)

    def _cmd_power_set(self, on):
        self._need_plant()
        if on and not self._mains_ok():
            raise ActionRejected('MAINS_OUT_OF_RANGE')
        if on and not self.powered:
            self.powered = True
            self.busy_until = self.t + ENG['initialisation_s']
            self._event('system', 'POWER_ON', '读板仪通电（初始化、灯与光路自检）')
        elif not on and self.powered:
            self._power_off('读板仪断电')
        return dict(on=self.powered)

    def _cmd_power_press_switch(self):
        return self._cmd_power_set(not self.powered)

    def _cmd_lid_set(self, open):
        self._need_plant()
        if open and self.run is not None:
            raise ActionRejected('RUN_ACTIVE')
        self.lid = self.LID_OPEN if open else 0.0
        return dict(open=bool(open))

    def _cmd_injector_load_reagent(self, pump, source_id, volume_m3):
        self._need_plant()
        if self.lid <= 1e-6:
            raise ActionRejected('LID_CLOSED')
        it = self.items.get(source_id)
        if it is None:
            raise ActionRejected('UNKNOWN_ITEM')
        if it['V'] < volume_m3 - 1e-15:
            raise ActionRejected('INSUFFICIENT_VOLUME')
        J = self.inj[pump]
        if J['reagent'] not in (None, source_id) and (J['bottle'] > 0 or J['line'] > 0):
            raise ActionRejected('REAGENT_MISMATCH')
        if J['bottle'] + volume_m3 > ENG['reagent_bottle_m3'] * (1 + 1e-9):
            raise ActionRejected('BOTTLE_FULL')
        it['V'] -= volume_m3
        J['bottle'] += volume_m3
        J['reagent'] = source_id
        J['comp'] = dict(it['comp'])
        J['reactions'] = [dict(r) for r in it['reactions']]
        self.led['reagent_loaded_m3'] += volume_m3
        return dict(pump=pump, volume_m3=J['bottle'])

    def _cmd_injector_prime(self, pump, volume_m3):
        self._need_power()
        if self._busy():
            raise ActionRejected('BUSY')
        J = self.inj[pump]
        if J['reagent'] is None or J['bottle'] < volume_m3 - 1e-15:
            raise ActionRejected('NO_REAGENT')
        if self._fault('injector_clog', pump):
            raise ActionRejected('INJECTOR_BLOCKED')
        J['bottle'] -= volume_m3
        fill = min(volume_m3, ENG['line_volume_m3'] - J['line'])
        J['line'] += fill
        self.led['primed_to_waste_m3'] += volume_m3 - fill
        J['primed'] = J['line'] >= ENG['line_volume_m3'] * 0.999
        J['syringe'] = min(1.0, volume_m3 / ENG['syringe_volume_m3'])
        self.busy_until = self.t + volume_m3 / ENG['prime_speed_m3_s']
        return dict(pump=pump, primed=J['primed'], line_m3=J['line'])

    def _cmd_injector_backflush(self, pump):
        self._need_power()
        if self._busy():
            raise ActionRejected('BUSY')
        J = self.inj[pump]
        back = J['line']
        J['bottle'] += back
        J['line'] = 0.0
        J['primed'] = False
        J['syringe'] = 0.0
        self.led['backflushed_m3'] += back
        self.busy_until = self.t + back / ENG['prime_speed_m3_s'] + 1.0
        return dict(pump=pump, returned_m3=back)

    def _cmd_incubator_set(self, temperature_k):
        self._need_power()
        if temperature_k is not None and temperature_k < self.plant['room_k'] + DOC['incubation_k'][0] - 1e-9:
            raise ActionRejected('TEMPERATURE_TOO_LOW')
        if temperature_k is not None and temperature_k > DOC['incubation_k'][1] + 1e-9:
            raise ActionRejected('INVALID_ARGUMENT')
        self.setpoint = None if temperature_k is None else float(temperature_k)
        return dict(temperature_k=self.setpoint)

    def _cmd_protocol_configure(self, mode, format, wells, **kw):
        self._need_plant()
        if self.run is not None:
            raise ActionRejected('RUN_ACTIVE')
        P = dict(mode=mode, format=format, wells=list(wells), **kw)
        names = expand_wells(format, wells)
        if mode == 'abs_endpoint' and not P.get('wavelengths_m'):
            raise ActionRejected('MISSING_SETTING')
        if mode == 'abs_spectrum':
            r = P.get('spectrum_range_m')
            if not r or r[1] <= r[0]:
                raise ActionRejected('MISSING_SETTING')
        if mode in ('fi_endpoint', 'fi_em_spectrum', 'fp_endpoint'):
            for k_, d in (('excitation_bandwidth_m', 10e-9), ('emission_bandwidth_m', 20e-9), ('optic', 'top')):
                P.setdefault(k_, d)
            if 'excitation_m' not in P or (mode != 'fi_em_spectrum' and 'emission_m' not in P) or (mode == 'fi_em_spectrum' and 'emission_range_m' not in P):
                raise ActionRejected('MISSING_SETTING')
            lo, hi = (DOC['fi_mono_range_m'] if mode != 'fp_endpoint' else DOC['fi_filter_range_m'])
            em_lo = P['emission_m'] if mode != 'fi_em_spectrum' else P['emission_range_m'][0]
            em_hi = P['emission_m'] if mode != 'fi_em_spectrum' else P['emission_range_m'][1]
            if not (lo <= P['excitation_m'] <= hi and lo <= em_lo and em_hi <= ENG['red_pmt_limit_m']):
                raise ActionRejected('WAVELENGTH_OUT_OF_RANGE')
            if em_lo - P['excitation_m'] < (P['excitation_bandwidth_m'] + P['emission_bandwidth_m']) / 2 + ENG['dichroic_gap_m']:
                raise ActionRejected('BANDWIDTH_OVERLAP')
            if P.get('emission2_m') is not None and (mode != 'fi_endpoint' or P['emission2_m'] - P['excitation_m'] < (P['excitation_bandwidth_m'] + P['emission_bandwidth_m']) / 2
                                                     + ENG['dichroic_gap_m'] or P['emission2_m'] > ENG['red_pmt_limit_m']):
                raise ActionRejected('BANDWIDTH_OVERLAP')
        if mode == 'fp_endpoint':
            P.setdefault('flashes', 100)                                    # FP default: 100 flashes per well (engineering)
        if P.get('cycles', 1) > 1:
            if 'cycle_time_s' not in P:
                raise ActionRejected('MISSING_SETTING')
            if P['cycle_time_s'] < self._cycle_budget(P, len(names)) - 1e-9:
                raise ActionRejected('CYCLE_TOO_SHORT')
        for inj in P.get('injections', []):
            if inj.get('cycle', 1) > P.get('cycles', 1):
                raise ActionRejected('INVALID_ARGUMENT')
        if P.get('optic') == 'bottom' and mode.startswith('abs'):
            raise ActionRejected('INVALID_ARGUMENT')
        P['_wells'] = names
        P['_t_read'] = self._plate_read_time(P, len(names))
        self.protocol = P
        return dict(mode=mode, wells=len(names), plate_read_time_s=P['_t_read'], cycles=P.get('cycles', 1))

    def _ready_for_reading(self):
        self._need_power()
        if self._busy():
            raise ActionRejected('BUSY')
        if self.carrier is None:
            raise ActionRejected('NO_PLATE')
        if self.drawer['open'] or self.drawer['pos'] > 1e-9:
            raise ActionRejected('DRAWER_OPEN')
        if self.lid > 1e-6:
            raise ActionRejected('LID_OPEN')

    def _cmd_focus_adjust(self, well):
        self._ready_for_reading()
        pl = self.plates[self.carrier]
        if well_index(pl['format'], well) is None:
            raise ActionRejected('INVALID_WELL')
        i = pl['names'].index(well)
        if pl['V'][i] <= 0:
            raise ActionRejected('EMPTY_WELL')
        z = round(self._z_opt(pl, i) / DOC['focal_resolution_m']) * DOC['focal_resolution_m']
        self.focus = z
        self.busy_until = self.t + ENG['focus_scan_s']
        return dict(focal_height_m=z)

    def _cmd_gain_adjust(self, well, target_fraction=0.9):
        self._ready_for_reading()
        P = self.protocol
        if P is None or P['mode'] not in ('fi_endpoint', 'fp_endpoint', 'lum_endpoint', 'fi_em_spectrum'):
            raise ActionRejected('NO_PROTOCOL')
        pl = self.plates[self.carrier]
        if pl['format'] != P['format']:
            raise ActionRejected('FORMAT_MISMATCH')
        if well_index(pl['format'], well) is None:
            raise ActionRejected('INVALID_WELL')
        i = pl['names'].index(well)
        if P['mode'] == 'lum_endpoint':
            m = self._lum_signal(pl, i, P.get('integration_s', 1.0))
        else:
            em = P['emission_m'] if 'emission_m' in P else sum(P['emission_range_m']) / 2
            m = self._fi_signal(pl, i, P['excitation_m'], em, P['excitation_bandwidth_m'], P['emission_bandwidth_m'], P.get('optic', 'top'), P.get('focal_height_m') or self.focus)
        g = ENG['gain_reference'] * (target_fraction * ENG['counts_full_scale'] / max(m, 1e-9)) ** (1.0 / ENG['gain_exponent'])
        g = int(max(0, min(4095, round(g))))
        P['gain'] = g
        self.busy_until = self.t + 2.0
        return dict(gain=g)

    def _cmd_run_start(self):
        self._ready_for_reading()
        P = self.protocol
        if P is None:
            raise ActionRejected('NO_PROTOCOL')
        pl = self.plates[self.carrier]
        if pl['format'] != P['format']:
            raise ActionRejected('FORMAT_MISMATCH')
        need = {}
        for inj in P.get('injections', []):
            need[inj['pump']] = need.get(inj['pump'], 0.0) + inj['volume_m3'] * len(P['_wells'])
        for p, v in need.items():
            if not self.inj[p]['primed']:
                raise ActionRejected('INJECTOR_NOT_PRIMED')
            if self.inj[p]['bottle'] + self.inj[p]['line'] < v - 1e-15:
                raise ActionRejected('INSUFFICIENT_REAGENT')
        for key in [k_ for k_ in self.warnings if k_ in ('WELL_OVERFLOW', 'OPAQUE_PLATE') or k_.startswith('INJECTOR_EMPTY_')]:
            self.warnings.pop(key)                                            # one-shot warnings of the previous run end with it
        if P['mode'].startswith('abs') and pl['type'] in ('black', 'white'):
            self.warnings['OPAQUE_PLATE'] = '不透明板做吸光测量：读数超量程'
        rid = f'run{sum(1 for e in self.events if e["code"] == "RUN_START") + 1:03d}'
        ev, t_read = self._schedule(P, P['_wells'], self.t + DT)
        self.run = dict(run_id=rid, P=copy.deepcopy(P), plate=self.carrier, wells=list(P['_wells']), events=ev, next=0, state='running', cycle=0, rows=[], spectra={},
                        t0=self.t, k_read=0, shaking_until=0.0)
        self._event('run', 'RUN_START', f'测量{rid}开始：{P["mode"]}，{len(P["_wells"])}孔，{P.get("cycles", 1)}循环')
        est = (ev[-1][0] - self.t) if ev else 0.0
        return dict(run_id=rid, plate_read_time_s=t_read, estimated_s=est)

    def _cmd_run_stop(self):
        self._need_plant()
        if self.run is None:
            raise ActionRejected('NO_RUN')
        R = self.run
        R['state'] = 'stopped'
        self.last_run, self.run = R, None
        self._event('run', 'RUN_STOPPED', f'测量{R["run_id"]}停止')
        return dict(run_id=R['run_id'], state='stopped')

    def _cmd_data_read(self, cycle=0, max_rows=200000):
        R = self.run or self.last_run
        if R is None:
            return dict(run_id=None, mode=None, state='none', rows=[])
        rows = [dict(r) for r in R['rows'] if cycle == 0 or r['cycle'] == cycle][:max_rows]
        return dict(run_id=R['run_id'], mode=R['P']['mode'], state=R['state'], rows=rows)

    def _cmd_data_spectrum(self, well):
        R = self.run or self.last_run
        if R is None or well not in R['spectra']:
            raise ActionRejected('NO_DATA')
        s = R['spectra'][well]
        return dict(well=well, wavelength_m=s['wavelength_m'], value=s['value'], unit=s['unit'])

    def _cmd_data_analyze(self, cycle=None, blank_wells=None, standards=None, fit='none', positive_wells=None, negative_wells=None):
        R = self.run or self.last_run
        if R is None:
            raise ActionRejected('NO_DATA')
        fmt = R['P']['format']
        c = cycle or (max(r['cycle'] for r in R['rows']) if R['rows'] else 1)
        vals = {}
        for r in R['rows']:
            if r['cycle'] == c and r['value'] is not None and r['well'] not in vals:
                vals[r['well']] = r['value']

        def mean(ws):
            v = [vals[w] for w in ws if w in vals]
            return (sum(v) / len(v), v) if v else (None, [])
        blank = None
        if blank_wells:
            blank, _ = mean(expand_wells(fmt, blank_wells))
        corr = {w: v - (blank or 0.0) for w, v in vals.items()}
        out_fit, conc = {}, {}
        if standards and fit != 'none':
            pts = [(float(cv), corr[w]) for w, cv in standards.items() if w in corr]
            if len(pts) < (4 if fit == '4pl' else 2) or len({p[0] for p in pts}) < (4 if fit == '4pl' else 2):
                raise ActionRejected('INSUFFICIENT_STANDARDS')                 # needs distinct concentrations (a fit through one x is undefined)
            xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
            if fit == 'linear':
                b, a = np.polyfit(xs, ys, 1)
                yhat = a + b * xs
                out_fit = dict(model='linear', intercept=float(a), slope=float(b), r2=float(1 - ((ys - yhat) ** 2).sum() / max(((ys - ys.mean()) ** 2).sum(), 1e-30)))
                conc = {w: (v - a) / b if b != 0 else None for w, v in corr.items()}
            else:
                p4 = _fit4pl(xs, ys)
                out_fit = dict(model='4pl', bottom=p4[0], top=p4[1], ec50=p4[2], hill=p4[3])
                conc = {w: _inv4pl(p4, v) for w, v in corr.items()}
        zp = None
        if positive_wells and negative_wells:
            mp, vp = mean(expand_wells(fmt, positive_wells))
            mn, vn = mean(expand_wells(fmt, negative_wells))
            if len(vp) >= 2 and len(vn) >= 2 and mp != mn:
                zp = 1.0 - 3.0 * (float(np.std(vp, ddof=1)) + float(np.std(vn, ddof=1))) / abs(mp - mn)
        return dict(blank=None if blank is None else float(blank), fit=_jsonable(out_fit), concentrations={w: (None if v is None else float(v)) for w, v in conc.items()},
                    z_prime=None if zp is None else float(zp))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_provenance_read(self):
        libs = [dict(name=k, file=str((ASSET_DIR / v).relative_to(ROOT)), entries=len(_lib(k).get('entries', _lib(k).get('formats', _lib(k).get('documented', {})))))
                for k, v in LIBS.items()]
        return dict(libraries=libs, species=sorted(spectra())[:400])

    def _cmd_truth_inspect(self, plate_id=None):
        r = self.read('evaluator')
        plates = r['truth.plates'] if plate_id is None else {plate_id: r['truth.plates'].get(plate_id)}
        return dict(state=_jsonable(dict(plates=plates, optics=r['truth.optics'], faults=r['truth.faults'])))

    def _cmd_ledger_read(self):
        return dict(ledger={k: float(v) for k, v in self.led.items()})

    def _cmd_fault_inject(self, fault, active, pump=None):
        self._need_plant()
        per = fault in ('injector_clog', 'bubble_in_line')
        if per and pump is None:
            raise ActionRejected('INVALID_ARGUMENT')
        key = (fault, pump if per else '')
        (self.faults.add if active else self.faults.discard)(key)
        self._event('fault', 'FAULT', f'{fault} {"on" if active else "off"} {pump or ""}')
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
        if mutating:
            self.revision += 1
        return out

    _NOCOPY = ('_principals', '_requests')

    def _snapshot(self):
        return {k: copy.deepcopy(v) for k, v in self.__dict__.items() if k not in self._NOCOPY}

    def _restore(self, snap):
        for k, v in snap.items():
            self.__dict__[k] = v


KIT_COMPOSITION = dict(water={}, pbs_buffer={}, fluorescein_standard={'fluorescein_in_basic_ethanol': 1.0e-3}, quinine_standard={'quinine_sulfate_in_0_5_m_h2so4': 1.0e-2},
                       atp_standard={'atp': 1.0e-3}, glow_luciferase_reagent={'glow_luciferase': 1.0e-3}, flash_luciferase_reagent={'flash_luciferase': 1.0e-3},
                       od_check_dye={'rhodamine_b_in_ethanol': 1.0e-2})


def _fit4pl(x, y):
    """Four-parameter logistic y = d + (a - d) / (1 + (x / c)^b) by a coarse grid on (c, b) and linear least squares for (a, d)."""
    best = None
    xp = np.maximum(x, 1e-30)
    pos = xp[xp > 1e-29]
    if len(pos) == 0:
        raise ActionRejected('INSUFFICIENT_STANDARDS')
    for c in np.geomspace(pos.min() / 10, pos.max() * 10, 60):
        for b in np.linspace(0.3, 4.0, 38):
            g = 1.0 / (1.0 + (xp / c) ** b)
            M = np.vstack([g, 1 - g]).T
            coef, *_ = np.linalg.lstsq(M, y, rcond=None)
            r = float(((M @ coef - y) ** 2).sum())
            if best is None or r < best[0]:
                best = (r, float(coef[0]), float(coef[1]), float(c), float(b))
    _, a, d, c, b = best
    return [a, d, c, b]


def _inv4pl(p, y):
    a, d, c, b = p
    try:
        q = (a - d) / (y - d) - 1.0
        return float(c * q ** (1.0 / b)) if q > 0 else None
    except ZeroDivisionError:
        return None


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
_CONTROL_GUARDED = {'plate.prepare', 'well.dispense', 'plate.seal', 'plate.load', 'plate.unload', 'injector.load_reagent', 'run.start', 'run.stop', 'focus.adjust',
                    'gain.adjust'}


class BmgClariostarplusSession:
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
        meta = copy.deepcopy(export_bmg_clariostarplus_contract_metadata())
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
            values['items.available'] = [x for x in values['items.available'] if not self._foreign('item:' + x)]
            values['plates.available'] = [x for x in values['plates.available'] if not self._foreign('plate:' + x)]
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
        for key in ('source_id',):
            iid = arguments.get(key)
            if isinstance(iid, str) and self._foreign('item:' + iid):
                raise ActionRejected('ACCESS_DENIED')
        pid = arguments.get('plate_id')
        if isinstance(pid, str) and self._foreign('plate:' + pid):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in ('plate.unload', 'run.start', 'run.stop', 'focus.adjust', 'gain.adjust') and self._host.carrier is not None \
                and self._foreign('plate:' + self._host.carrier):
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
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值、评测者能力或另一个agent的板/样品）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '读板仪未通电', 'MAINS_OUT_OF_RANGE': '电源电压不在100/115/230 V（±10 %）内',
    'ITEM_EXISTS': 'ID已存在', 'TOO_MANY_ITEMS': '样品与试剂数量超过上限', 'UNKNOWN_SPECIES': '物种不在谱库或特殊物种表中', 'UNKNOWN_ITEM': '样品或试剂不存在',
    'PLATE_EXISTS': '板ID已存在', 'TOO_MANY_PLATES': '板数量超过上限', 'UNKNOWN_PLATE': '板不存在', 'PLATE_IN_READER': '板在读板仪的板托架上（先取下）',
    'INVALID_WELL': '孔号不属于该板型', 'INSUFFICIENT_VOLUME': '样品或试剂体积不够', 'WELL_OVERFLOW': '超过孔的最大体积', 'BUSY': '初始化、测量、灌注、回抽或调焦进行中',
    'DRAWER_CLOSED': '板托架未送出', 'DRAWER_OPEN': '板托架送出（先收回）', 'CARRIER_OCCUPIED': '板托架上已有板', 'CARRIER_EMPTY': '板托架上没有板',
    'RUN_ACTIVE': '测量进行中', 'LID_CLOSED': '顶盖关着（先打开）', 'LID_OPEN': '顶盖打开（先关上）', 'REAGENT_MISMATCH': '注射泵中还有另一种试剂（先回抽或用完）',
    'BOTTLE_FULL': '试剂瓶容量不够', 'NO_REAGENT': '注射泵没有足够试剂', 'INJECTOR_BLOCKED': '注射器堵塞', 'TEMPERATURE_TOO_LOW': '温育温度须至少比室温高3 K',
    'MISSING_SETTING': '该模式必需的设置缺失（波长、光谱范围、激发/发射、循环时间）', 'WAVELENGTH_OUT_OF_RANGE': '波长超出该检测方式的范围',
    'BANDWIDTH_OVERLAP': '激发与发射带宽重叠（二向色镜需要间隔）', 'CYCLE_TOO_SHORT': '循环时间短于单板读数、振荡、沉降与注射所需时间', 'NO_PLATE': '板托架上没有板',
    'EMPTY_WELL': '空孔', 'NO_PROTOCOL': '没有合适的测量程序', 'FORMAT_MISMATCH': '程序板型与板托架上的板不符', 'INJECTOR_NOT_PRIMED': '注射泵未灌注',
    'INSUFFICIENT_REAGENT': '注射试剂不够本次测量', 'NO_RUN': '没有测量', 'NO_DATA': '没有数据（或该孔没有光谱）', 'INSUFFICIENT_STANDARDS': '标准点不够拟合',
}
_P = ['PLANT_REQUIRED']
_PW = _P + ['POWER_OFF']
_READY = _PW + ['BUSY', 'NO_PLATE', 'DRAWER_OPEN', 'LID_OPEN']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'sample.provide': _P + ['ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_SPECIES'], 'kit.provide': _P + ['TOO_MANY_ITEMS'],
    'plate.prepare': _P + ['PLATE_EXISTS', 'TOO_MANY_PLATES'],
    'well.dispense': _P + ['UNKNOWN_PLATE', 'PLATE_IN_READER', 'INVALID_WELL', 'UNKNOWN_ITEM', 'INSUFFICIENT_VOLUME', 'WELL_OVERFLOW'],
    'plate.seal': _P + ['UNKNOWN_PLATE', 'PLATE_IN_READER'], 'drawer.set': _PW + ['BUSY'],
    'plate.load': _P + ['UNKNOWN_PLATE', 'DRAWER_CLOSED', 'CARRIER_OCCUPIED', 'PLATE_IN_READER'], 'plate.unload': _P + ['DRAWER_CLOSED', 'CARRIER_EMPTY', 'BUSY'],
    'power.set': _P + ['MAINS_OUT_OF_RANGE'], 'power.press_switch': _P + ['MAINS_OUT_OF_RANGE'], 'lid.set': _P + ['RUN_ACTIVE'],
    'injector.load_reagent': _P + ['LID_CLOSED', 'UNKNOWN_ITEM', 'INSUFFICIENT_VOLUME', 'REAGENT_MISMATCH', 'BOTTLE_FULL'],
    'injector.prime': _PW + ['BUSY', 'NO_REAGENT', 'INJECTOR_BLOCKED'], 'injector.backflush': _PW + ['BUSY'], 'incubator.set': _PW + ['TEMPERATURE_TOO_LOW'],
    'protocol.configure': _P + ['RUN_ACTIVE', 'INVALID_WELL', 'MISSING_SETTING', 'WAVELENGTH_OUT_OF_RANGE', 'BANDWIDTH_OVERLAP', 'CYCLE_TOO_SHORT'],
    'focus.adjust': _READY + ['INVALID_WELL', 'EMPTY_WELL'], 'gain.adjust': _READY + ['NO_PROTOCOL', 'FORMAT_MISMATCH', 'INVALID_WELL'],
    'run.start': _READY + ['NO_PROTOCOL', 'FORMAT_MISMATCH', 'INJECTOR_NOT_PRIMED', 'INSUFFICIENT_REAGENT'], 'run.stop': _P + ['NO_RUN'],
    'data.read': [], 'data.spectrum': ['NO_DATA'], 'data.analyze': ['NO_DATA', 'INVALID_WELL', 'INSUFFICIENT_STANDARDS'], 'events.read': [], 'provenance.read': [],
    'truth.inspect': [], 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子；之后再声明须给同一种子', ['plant.configured']),
    'sample.provide': ('ID唯一；物种在谱库或特殊物种表中', ['items.available']), 'kit.provide': ('同一试剂再次提供时增加体积', ['items.available']),
    'plate.prepare': ('板ID唯一', ['plates.available']), 'well.dispense': ('板在台面上；孔属于板型；来源体积足够；不超过孔的最大体积', ['plates.available', 'items.available']),
    'plate.seal': ('板在台面上', ['plates.available']), 'drawer.set': ('已通电；没有初始化、测量、灌注或调焦', ['power.on', 'lamp.busy']),
    'plate.load': ('板托架已送出且空；板在台面上', ['drawer.position_m', 'carrier.plate']), 'plate.unload': ('板托架已送出且有板；不在测量中', ['drawer.position_m', 'carrier.plate']),
    'power.set': ('电源电压在100/115/230 V（±10 %）内才能通电', ['power.on']), 'power.press_switch': ('同power.set', ['power.on']),
    'lid.set': ('测量中不能打开顶盖', ['run.status']), 'injector.load_reagent': ('顶盖打开；试剂已提供；泵中没有另一种试剂；瓶容量足够', ['lid.angle_rad', 'injectors.status']),
    'injector.prime': ('已通电、不忙；泵中试剂足够；注射器未堵塞', ['power.on', 'injectors.status']), 'injector.backflush': ('已通电、不忙', ['power.on', 'lamp.busy']),
    'incubator.set': ('已通电；设定至少比室温高3 K、不超过45 °C', ['power.on', 'chamber.temperature_k']),
    'protocol.configure': ('不在测量中；该模式的必需设置齐全；波长在范围内、激发与发射不重叠；动力学循环时间足够', ['run.status']),
    'focus.adjust': ('已通电、不忙；板在板托架上且已收回；顶盖关；孔不空', ['power.on', 'carrier.plate', 'drawer.position_m', 'lid.angle_rad']),
    'gain.adjust': ('同focus.adjust；有荧光/偏振/发光程序且板型相符', ['power.on', 'protocol.current', 'carrier.plate', 'drawer.position_m', 'lid.angle_rad']),
    'run.start': ('已通电、不忙；板在板托架上且已收回；顶盖关；有程序且板型相符；注射泵已灌注且试剂足够',
                  ['power.on', 'carrier.plate', 'drawer.position_m', 'lid.angle_rad', 'protocol.current', 'injectors.status']),
    'run.stop': ('有测量；托架上的板属于本agent', ['run.status']), 'data.spectrum': ('最近测量为光谱模式且有该孔', ['run.status']), 'data.analyze': ('有测量数据', ['run.status']),
    'fault.inject': ('注射器故障须给泵', ['plant.configured']),
}
_PL = ['injector.a_plunger_m', 'injector.b_plunger_m']
EFFECTS = {
    'plant.configure': ['plant.configured', 'power.on', 'lamp.power', 'lamp.busy', 'run.status', 'chamber.temperature_k'],
    'sample.provide': ['items.available'], 'kit.provide': ['items.available'], 'plate.prepare': ['plates.available', 'truth.plates'], 'well.dispense': ['truth.plates'],
    'plate.seal': ['truth.plates'], 'drawer.set': [], 'plate.load': ['carrier.plate', 'plates.available', 'truth.plates', 'truth.optics'],
    'plate.unload': ['carrier.plate', 'plates.available', 'truth.plates', 'truth.optics'],
    'power.set': ['power.on', 'lamp.power', 'lamp.busy', 'chamber.temperature_k', 'run.status'], 'power.press_switch': ['power.on', 'lamp.power', 'lamp.busy', 'chamber.temperature_k', 'run.status'],
    'lid.set': ['lid.angle_rad'], 'injector.load_reagent': ['injectors.status'], 'injector.prime': ['injectors.status', 'lamp.busy'] + _PL,
    'injector.backflush': ['injectors.status', 'lamp.busy'] + _PL, 'incubator.set': ['incubator.setpoint_k'], 'protocol.configure': ['protocol.current'],
    'focus.adjust': ['focus.height_m', 'lamp.busy'], 'gain.adjust': ['protocol.current', 'lamp.busy'], 'run.start': ['run.status', 'lamp.busy', 'warnings.active'], 'run.stop': ['run.status', 'lamp.busy'],
    'fault.inject': ['truth.faults', 'truth.optics'], 'sim.step': ['clock.time_s'],
}
_EVOLVE = ['chamber.temperature_k', 'truth.plates', 'truth.optics', 'warnings.active', 'lamp.busy']
ON_STEP = {
    'sim.step': _EVOLVE + ['drawer.position_m', 'run.status', 'injectors.status', 'clock.time_s'] + _PL,
    'drawer.set': ['drawer.position_m', 'truth.plates'], 'incubator.set': ['chamber.temperature_k', 'truth.plates', 'warnings.active'],
    'run.start': ['run.status', 'injectors.status', 'truth.plates', 'lamp.busy', 'warnings.active'] + _PL, 'power.set': ['drawer.position_m', 'lamp.busy', 'chamber.temperature_k'],
    'power.press_switch': ['drawer.position_m', 'lamp.busy', 'chamber.temperature_k'], 'well.dispense': ['truth.plates', 'truth.optics'],
    'injector.prime': ['lamp.busy'], 'injector.backflush': ['lamp.busy'], 'focus.adjust': ['lamp.busy'], 'gain.adjust': ['lamp.busy'], 'lid.set': ['warnings.active'],
    'fault.inject': ['truth.plates', 'chamber.temperature_k'], 'plate.seal': ['truth.plates'], 'sample.provide': [], 'plant.configure': ['chamber.temperature_k', 'truth.plates'],
}
ON_STEP_ZH = {
    'sim.step': '按0.1 s网格积分：板托架运动、测量室与板温度、蒸发、混合、反应、注射与逐孔读数', 'drawer.set': '板托架约2 s送出/收回；送出时板回到室温环境',
    'incubator.set': '测量室按时间常数升降温，板随之变化', 'run.start': '按程序注射、振荡、沉降并逐孔读数；动力学按循环', 'power.set': '通电后初始化约20 s；断电后托架停在原位',
    'power.press_switch': '同power.set', 'well.dispense': '加样后孔内混合、反应与蒸发随时间进行', 'injector.prime': '灌注期间状态灯亮', 'injector.backflush': '回抽期间状态灯亮',
    'focus.adjust': '调焦扫描期间状态灯亮', 'gain.adjust': '增益调整期间状态灯亮', 'lid.set': '顶盖开着时提示不能测量', 'fault.inject': '故障作用于随后的光学、注射与温度',
    'plate.seal': '封膜后蒸发减少', 'plant.configure': '室温影响测量室与台面上的板',
}
EXEC_TEXT = {
    'run.start': ('run.status.state从running到completed（单板读数时间按厂家读板时间；动力学按循环时间；注射、振荡与沉降在读数前）',
                  '测量中断（run.stop、断电）时已读的孔保留数据；未读的孔没有数据'),
}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('run.start', 'focus.adjust', 'gain.adjust', 'injector.prime', 'injector.backflush', 'drawer.set'):
    RESOURCES[_k] = ['host_clock', 'optical_head', 'plate_carrier']
OBSERVES = {'run.start': ['run.status'], 'drawer.set': ['drawer.position_m'], 'incubator.set': ['chamber.temperature_k'], 'lid.set': ['lid.angle_rad'],
            'injector.prime': _PL, 'injector.backflush': _PL, 'focus.adjust': ['focus.height_m']}
_WB, _PR, _OM, _SL, _SIM = 'user_workbook', 'manufacturer_primary', 'omlc_photochemcad', 'ansi_slas_standards', 'simulation_assumption'
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({'sample.provide': [_OM, _SIM], 'kit.provide': [_OM, _SIM], 'plate.prepare': [_SL, _PR], 'well.dispense': [_SL, _SIM], 'drawer.set': [_PR, _SIM],
                    'power.set': [_PR], 'power.press_switch': [_PR], 'injector.load_reagent': [_PR, _SIM], 'injector.prime': [_PR, _SIM], 'injector.backflush': [_PR],
                    'incubator.set': [_PR], 'protocol.configure': [_PR, _WB], 'focus.adjust': [_PR], 'gain.adjust': [_PR, _SIM], 'run.start': [_PR, _OM, _SIM],
                    'data.read': [_PR, _OM], 'data.spectrum': [_PR, _OM], 'data.analyze': [_PR, _SIM], 'provenance.read': [_OM, _SL], 'fault.inject': [_SIM]})
CAP_SOURCES.update({k: [_PR] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温', 'mains_voltage_v': '电源电压（100/115/230 V自动量程）', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.microplate_reader_process.limitations。'
CAP_LIMITS = {
    'run.start': ['读板时间按厂家单板读数时间（飞行模式1次/10次闪光）按孔数与闪光数插值；6–48孔板与光谱、发光模式的读数时间为工程值；逐孔读数时刻按顺序均分。'],
    'data.analyze': ['MARS功能等价：空白扣除、线性或四参数标准曲线、Z′因子；不复制MARS的全部分析模板。'],
    'protocol.configure': ['荧光单色器带宽8–100 nm与二向色间隔为工程值；吸光度8个波长同时读取（光谱仪）。'],
}
LIMITATIONS = [
    '选定配置：CLARIOstar Plus（UV/vis光谱仪、LVF单色器、FP滤光片组、两个注射泵、温育至45 °C）；电脑上的SMART Control/MARS为宿主（功能等价实现）；AlphaScreen激光、TRF、大气控制、堆叠器与条码为不支持。',
    '吸光：比尔–朗伯定律（PhotochemCAD/OMLC摩尔消光谱，十进制）、孔的光程=体积/底面积−弯月面修正、板本底（聚苯乙烯紫外截止为工程值）、杂散光使读数在约4 OD以上饱和；噪声按厂家精密度（1 OD 0.5 %、2 OD 0.8 %）的70 %设定。',
    '荧光：吸收的激发光×量子产率×发射带内分数×内滤校正×焦高因子×收集效率；仪器常数与暗噪声按厂家FITC检测限（顶读单色器0.35 pM）标定；偏振按结合分数加权各向异性；发光按ATP与萤光素酶试剂（辉光/闪光衰减）；常数为工程值。',
    '孔内：混合按时间常数（静置/移液/注射/振荡）、一级反应（Arrhenius）、蒸发（饱和蒸汽压、边缘孔、封膜）；溶液中物种的谱取自有机溶剂或缓冲液中的数据库测量，水相差异未修正。',
    '样品、板与试剂为过程数据；场景中只有板托架、顶盖、注射泵柱塞与两个指示灯随运行层状态变化；板放在板托架上的几何为参考板（不随运行层板型变化）。',
]


def _sampling_text(k):
    if k in ('chamber.temperature_k', 'truth.plates', 'truth.optics'):
        return 'Integrated on the fixed 0.1 s grid (anchored at t = 0); between grid points the last value is held.'
    if k in SCENE_MIRRORED:
        return 'Position / state at the host time (the carrier moves with its speed; lamps follow the power and busy state).'
    return 'Changes when a command is accepted or a scheduled run event (injection, read, cycle end) is executed.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or CAPS[k]['visibility'] != 'agent') else []
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
    if k in _CONTROL_GUARDED:
        return '；agent只能用自己准备的板与样品（ACCESS_DENIED）'
    return ''


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：无模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景关节或指示灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节或读数', binding_ids=[])


def _params():
    P = {}

    def doc(name, val, unit, meaning, src=(_PR,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    for k, v in instrument()['documented'].items():
        doc(k, v['value'], v['unit'], v['note_zh'] + '（原文：' + v['quote'] + '）', (_PR, _WB) if k in ('abs_range_m', 'od_range', 'od_resolution', 'injection_volume_m3', 'dimensions_m', 'mass_kg',
                                                                                            'read_time_flying_s', 'plate_formats') else (_PR,))
    doc('slas_footprint_m', [0.12776, 0.08548], 'm', 'SBS微孔板外形127.76×85.48 mm（ANSI/SLAS 1-2004）', (_SL,))
    doc('slas_pitch_m', {'96': 0.009, '384': 0.0045, '1536': 0.00225}, 'm', '96/384/1536孔孔距9/4.5/2.25 mm（ANSI/SLAS 4-2004）', (_SL,))
    for k in sorted(ENG):
        asm(k, ENG[k], ENG_UNIT.get(k, '1'), ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明）' + ('；按厂家规格标定' if k in CALIBRATED else ''))
    return P


ENG_UNIT = instrument().get('engineering_unit', {})
CALIBRATED = {'fi_counts_per_unit', 'lum_glow_counts_per_mol'}
SLOT_REFERENCE = {'chamber.temperature_k': '热力学温度（ITS-90）；测量室空气温度读数（0.1 K分辨率）', 'drawer.position_m': '板托架自收回位置沿资产−Y的送出距离',
                  'lid.angle_rad': '顶盖绕后缘铰链（资产−X轴）自关闭位置的转角', 'injector.a_plunger_m': '注射泵A柱塞自排空位置向下（资产−Z）的行程',
                  'injector.b_plunger_m': '注射泵B柱塞自排空位置向下（资产−Z）的行程', 'focus.height_m': '焦点高于孔底内表面的距离'}


def export_bmg_clariostarplus_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'BmgClariostarplusSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.bmg_clariostarplus.BmgClariostarplusSession.invoke',
                                              invocation=f'session.invoke(instance_id="bmg_clariostarplus_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + _foreign_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='BmgClariostarplusRuntime.invoke / BmgClariostarplusSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（运动、温度、孔内过程与读数在之后的step中演化）',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['reader_housing', 'optical_head', 'plate_carrier', 'microplate', 'incubation_chamber', 'injector_a', 'injector_b', 'top_lid', 'power_button', 'status_led'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='多模式微孔板读板：板托架把SBS板送入测量室；闪光灯经LVF单色器或光谱仪照射各孔，PMT或CCD按吸光、荧光、偏振或发光读数；测量室温育、振荡与注射在读数之间调度；'
                   'MARS等价分析给出空白扣除、标准曲线、浓度与Z′',
        equations=['A(lambda) = sum_i eps_i(lambda) c_i l + A_plate(lambda) + turbidity; l = V / A_bottom - meniscus; A_obs = -log10((10^-A + s)/(1 + s)) + noise',
                   'F = K G sum_i c_i eps_i(ex) ln10 Phi_i f_em,i * IFE * focus * collection + background; IFE = (1 - 10^-A_ex)/(A_ex ln10) 10^(-A_em/2)',
                   'FP: r = sum_i L_i (f_b r_b + (1 - f_b) r_f) / sum_i L_i, f_b from the quadratic binding equation (Kd); mP = 1000 (I_par - G I_perp) / (I_par + G I_perp)',
                   'LUM = K_L [ATP] V (glow: exp(-t/tau_glow); flash: exp(-(t - t_inj)/tau_flash)) + dark',
                   'dT_chamber/dt = (T_set - T)/tau_c (rate-limited); dT_plate/dt = (T_env - T_plate)/tau_p; dV/dt = -e (Psat(T)/Psat(298 K)) (edge, seal)',
                   'mixing: unmixed -> mixed with tau (still / pipette / injection / shaking); reactions dA/dt = -k(T) a(trigger) A',
                   'plate read time t(n flashes) = t1 + (t10 - t1)(n - 1)/9 scaled by the selected wells (documented 96 / 384 / 1536 read times)'],
        parameters=_params(), source_ids=[_SIM, _WB, _PR, _OM, _SL], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_bmg_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('bmg_clariostarplus', caps, slots, {'microplate_reader_process': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'microplate_reader_process': mechanism})
