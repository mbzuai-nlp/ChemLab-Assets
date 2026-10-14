"""CHEM-167 Bio-Rad CFX Opus 96 Real-Time PCR System (catalog 12011319): process runtime.

Device (Bulletin 7299, Bulletin 7279, Instrument Guide 10000119983): rear power switch; motorised retractable heated lid (30-110 C, default 105 C,
opened / closed only from the touch screen or software, obstruction stop); fixed 96-well Peltier block 4-100 C, max ramp 5 C/s, average 3.3 C/s,
accuracy +-0.2 C at 90 C, uniformity +-0.3 C, gradient across the rows (30-100 C, span 1-24 C); sample volume 1-50 ul with the calculated
(volume > 0) or block (volume 0) temperature-control mode; protocol steps: temperature (4.0-100.0 C, hold 1 s-17:59:59 or infinite, ramp
0.1-5 C/s, increment -10..10 C/cycle, extend -60..60 s/cycle, plate read, beep), gradient, GOTO (default 39 repeats) and melt curve; optics
shuttle with 6 filtered LEDs / 6 filtered photodiodes: channels 1-5 (FAM, HEX, Texas Red, Cy5, Quasar 705; documented bands) + FRET, scan
12 s all channels or 3 s single channel; LED status bar (green run, green flashing pause, blue flashing complete until the lid opens, red error).

Process model (fixed 0.1 s grid, numpy over the 96 wells): block Peltier servo (slew-limited proportional approach calibrated to the documented
average ramp), row gradient, a deterministic +-0.2 / +-0.3 C accuracy / uniformity pattern, per-well sample temperature lag depending on the volume,
heated lid; PCR chemistry per well and assay: a synthesis event closes every time the sample is re-heated after a cool phase - new copies =
copies x E_max x denaturation completeness (amplicon Tm, two-state) x primer annealing (primer Tm, with the mild low-temperature penalty of the
brochure GAPDH gradient) x extension completeness (polymerase speed vs. amplicon length) x hot-start activation x inhibitor x primer reserve, capped
by the polymerase capacity (linear phase); primer dimers in SYBR wells without template; TaqMan probes cleaved by synthesis; fluorescence =
dye amount x brightness x channel response (FPbase spectra x documented bands) x plate factor, plus background and deterministic noise; the
factory dye calibration unmixes the channels; analysis as CFX Maestro: baseline-subtracted curves, auto threshold (10 x baseline SD), fractional
Cq, standard curve (slope, efficiency, R2, unknown quantities), melt peaks (-dRFU/dT) and DeltaCq / DeltaDeltaCq."""
import copy
import csv
import io
import json
import math
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.biorad_cfxopus"
CATALOG_ID = "CHEM-167"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t4_measure/biorad_cfxopus'
LIBS = {'fluorophores': 'fluorophore_library.json', 'assays': 'assay_library.json', 'optics': 'optics_model.json'}

# ---------------------------------------------------------------- constants (SI)
R_GAS = 8.314462618
AVOGADRO = 6.02214076e23
DT = 0.1                                  # physics grid (s)
ROWS, COLS = 'ABCDEFGH', 12
WELLS = [f'{r}{c}' for r in ROWS for c in range(1, COLS + 1)]
WIDX = {w: i for i, w in enumerate(WELLS)}
NW = len(WELLS)
MAX_ASSAYS = 5                            # up to 5 targets per well (documented multiplex)
T_BLOCK_RANGE = (277.15, 373.15)          # 4-100 C
T_LID_RANGE = (303.15, 383.15)            # 30-110 C
LID_DEFAULT_K = 378.15                    # 105 C (documented default for the 96-well block)
LID_IDLE_K = 304.15                       # lid keeps 31 C during an infinite hold below 30 C (documented)
RAMP_MAX = 5.0                            # K/s documented maximum
RAMP_GAIN = 0.9                           # 1/s proportional approach: 60 -> 95 C arrives (0.1 K) in ~10.4 s = 3.4 K/s average (documented average 3.3)
ARRIVE_K = 0.1
VOLUME_RANGE = (0.0, 50e-9)               # protocol sample volume setting (m3); 0 = block mode
GRADIENT_RANGE = ((303.15, 372.15), 373.15, (1.0, 24.0))
SCAN_S = {'all_channels': 12.0, 'sybr_fam': 3.0, 'fret': 3.0}
CHANNELS = ['1', '2', '3', '4', '5', '6']
CHANNEL_NAME = {'1': 'FAM', '2': 'HEX', '3': 'Texas Red', '4': 'Cy5', '5': 'Quasar 705', '6': 'FRET'}
LID_TRAVEL = 0.232                        # m, retracted (interface definition)
LID_TIME_S = 6.0                          # motorised lid travel time (engineering)
SCREEN_RANGE = (0.0, (55.0 - 12.0) * math.pi / 180)
MAX_STEPS = 40                            # protocol length limit of this model (assumption; the guide gives none)
MAX_GOTO_CYCLES = 99                      # GOTO repeat limit of this model (assumption; the guide gives the default 39 only)
GOTO_DEFAULT_CYCLES = 39                  # documented default number of GOTO repeats
MAX_RUNS = 50
MAX_EVENTS = 1000
PLATE_TYPES = {'white_lowprofile_96': dict(factor_key='white_wells', max_ul=200.0), 'clear_lowprofile_96': dict(factor_key='clear_wells', max_ul=200.0),
               'white_8tube_strips': dict(factor_key='white_wells', max_ul=200.0)}      # well capacity 200 ul: typical low-profile plate (assumption)
SEALS = ['optical_film', 'heat_seal', 'none']
CHEMISTRIES = ['sybr', 'evagreen', 'probe']
SAMPLE_TYPES = ['unknown', 'standard', 'ntc', 'nrt', 'positive_control', 'negative_control']
# engineering chemistry constants (typical real-time PCR supermix; Bio-Rad does not publish the compositions)
CHEM = dict(primer_mol_m3=4.0e-4, probe_mol_m3=2.5e-4, dntp_mol_m3=0.8, sybr_bp_sat_per_m3=1.5e22, sybr_free_fraction=0.002, sybr_temp_coeff=0.012,
            probe_residual=0.03, probe_bind=0.9, pol_capacity_per_m3=2.0e19, act_tau_s=30.0, act_t_k=367.15, anneal_width_k=1.0, anneal_offset_k=2.0,
            primer_mg_shift_k=4.0, low_penalty_per_k=0.0095, low_ref_offset_k=1.0, pol_speed_nt_s=70.0, pol_opt_k=345.15, pol_width_k=14.0, melt_width_k=1.6,
            melt_tm_offset_k=-3.0, pd_seed_base=0.3, pd_efficiency=0.95, pd_tm_k=348.15, pd_length_bp=45, tau_sample_base_s=1.0,
            tau_sample_per_volume_s_m3=0.15e9, evaporation_m3_per_s_k=5.5e-14)
# primers 400 nM, probe 250 nM, dNTP 0.8 mM (stored in mol/m3); melt_tm_offset_k: the effective amplicon melting temperature in a SYBR supermix is
# taken 3 K below the two-state nearest-neighbour value of the library (domain melting of long amplicons, supermix additives; assumption +-2 K)
# primer annealing: the PrimerBank primer Tm (Primer3-type, monovalent salt only) is raised by ~4 K by the 3 mM Mg2+ of a PCR supermix (engineering);
# annealing is complete up to ~(Tm + 4 K + 2 K); below the optimum (PrimerBank Tm + 1 K) mispriming / dimer competition costs 0.95 %/K (fitted to the
# brochure GAPDH gradient in calibrate_cfx.py)
FAULTS = ['power_loss', 'lid_obstructed', 'led_failure_channel_1', 'led_failure_channel_2', 'led_failure_channel_3', 'led_failure_channel_4',
          'led_failure_channel_5', 'fan_failure', 'block_sensor_drift']


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- libraries
_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def fluorophores():
    return _lib('fluorophores')['substances']


def assays():
    return _lib('assays')['substances']


def optics():
    return _lib('optics')


CALIBRATED_DYES = ['fam', 'hex', 'rox_carboxy_x_rhodamine', 'texas_red', 'cy5', 'cy5_5', 'quasar_670', 'quasar_705', 'cal_fluor_orange_560',
                   'cal_fluor_red_610', 'sybr_green', 'evagreen']        # factory-calibrated dyes (assumption: the common CFX dyes; list in CFX Maestro guide)


def dye_brightness(d):
    """Relative molar brightness ext. coeff. x quantum yield; FPbase gaps filled with 5e4 M-1 cm-1 and QY 0.5 (assumption)."""
    eps = d.get('ext_coeff_m1_cm1') or 5.0e4
    qy = d.get('quantum_yield') or 0.5
    return eps * qy / (7.13e4 * 0.5)                                         # relative to FAM with the same fallback QY


def resp_fam_ch1():
    """FAM response in channel 1 x brightness: the detector scale rfu_per_nm_fam_ch1_20ul refers to it."""
    f = fluorophores()['fam']
    return f['channel_response']['1'] * dye_brightness(f)


def response_matrix(dyes):
    """Channel response of each dye (rows: dyes, columns: channels 1-6) x brightness."""
    fl = fluorophores()
    return np.array([[fl[d]['channel_response'].get(c, 0.0) * dye_brightness(fl[d]) for c in CHANNELS] for d in dyes])


# ---------------------------------------------------------------- deterministic noise
_MASK = (1 << 64) - 1


def _mix(z):
    z = (z + 0x9E3779B97F4A7C15) & _MASK
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
    return z ^ (z >> 31)


def _gauss(seed, a, b):
    x = _mix((seed * 1000003 + a * 4099 + b) & _MASK)
    y = _mix(x)
    u1 = ((x >> 11) + 0.5) / 9007199254740992.0
    u2 = ((y >> 11) + 0.5) / 9007199254740992.0
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _uniform(seed, a, b):
    return ((_mix((seed * 7919 + a * 131 + b) & _MASK) >> 11) + 0.5) / 9007199254740992.0


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


# ---------------------------------------------------------------- schema helpers (as CHEM-165 / CHEM-166)
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
            if len(v) > s.get('maxProperties', math.inf):
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
        if 'pattern' in s:
            import re
            if not re.fullmatch(s['pattern'], v):
                raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.-]+$')
LIBID = dict(type='string', minLength=1, maxLength=80)
WELL = enum(WELLS)
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 288.15, 308.15), mains_voltage_v=number('V', 0.0, 264.0), noise_seed=integer(0, 2 ** 31 - 1)))
STEP = obj(dict(kind=enum(['temperature', 'gradient', 'goto', 'melt']), temperature_k=nullable(number('K', 277.15, 373.15)), hold_s=nullable(number('s', 1.0, 64799.0)),
                plate_read=BOOL, ramp_rate_k_s=nullable(number('K/s', 0.1, 5.0)), increment_k=number('K', -10.0, 10.0), extend_s=number('s', -60.0, 60.0), beep=BOOL,
                lower_k=nullable(number('K', 303.15, 372.15)), upper_k=nullable(number('K', 304.15, 373.15)), goto_step=nullable(integer(1, MAX_STEPS)),
                cycles=nullable(integer(1, MAX_GOTO_CYCLES)), start_k=nullable(number('K', 277.15, 373.15)), end_k=nullable(number('K', 277.15, 373.15)),
                melt_increment_k=nullable(number('K', 0.1, 5.0))),
           optional=('temperature_k', 'hold_s', 'plate_read', 'ramp_rate_k_s', 'increment_k', 'extend_s', 'beep', 'lower_k', 'upper_k', 'goto_step', 'cycles', 'start_k',
                     'end_k', 'melt_increment_k'))
PROTOCOL_EXAMPLE = dict(name='SYBR_2step_melt', sample_volume_m3=2.0e-8, lid_temperature_k=378.15, steps=[
    dict(kind='temperature', temperature_k=368.15, hold_s=30.0), dict(kind='temperature', temperature_k=368.15, hold_s=10.0),
    dict(kind='temperature', temperature_k=333.15, hold_s=30.0, plate_read=True), dict(kind='goto', goto_step=2, cycles=39),
    dict(kind='melt', start_k=338.15, end_k=368.15, melt_increment_k=0.5, hold_s=5.0)])
WELL_SPEC = obj(dict(well=WELL, sample_type=enum(SAMPLE_TYPES), sample_id=nullable(SID), dilution=number('1', 1e-12, 1.0), template_volume_m3=number('m3', 0.0, 5e-8),
                     reaction_volume_m3=number('m3', 1e-9, 5e-8), assays=array(LIBID, 0, MAX_ASSAYS), chemistry=enum(CHEMISTRIES),
                     reporters=array(LIBID, 0, MAX_ASSAYS), quantity_copies=nullable(number('1', 0.0, 1e13)), biological_group=nullable(SID)),
                optional=('sample_id', 'dilution', 'template_volume_m3', 'reporters', 'quantity_copies', 'biological_group'))
PLATE_EXAMPLE = dict(plate_id='plate_1', plate_type='white_lowprofile_96', seal='optical_film', wells=[
    dict(well='A1', sample_type='standard', sample_id='gapdh_std', dilution=1.0, template_volume_m3=2e-9, reaction_volume_m3=2e-8, assays=['hs_gapdh'], chemistry='sybr',
         quantity_copies=1.0e6)])
OPERATION = obj(dict(operation_id=TEXT, warnings=array(enum(['no_plate_on_the_block']), 0, 4)), optional=('warnings',))
RUN_WARNINGS = ['POWER_FAILURE']
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'lid', 'plate', 'protocol', 'run', 'read', 'error', 'fault', 'data']), code=TEXT, message_zh=TEXT))
CURVE = obj(dict(well=TEXT, fluorophore=TEXT, channel=TEXT, cycles=array(INT, 0, 200), rfu=array(number('1'), 0, 200), baseline_subtracted=array(number('1'), 0, 200)))
MELT = obj(dict(well=TEXT, fluorophore=TEXT, temperature_k=array(number('K'), 0, 2000), rfu=array(number('1'), 0, 2000), neg_derivative=array(number('1/K'), 0, 2000)))
CQ_ROW = obj(dict(well=TEXT, fluorophore=TEXT, target=nullable(TEXT), sample_type=TEXT, cq=nullable(number('1')), end_rfu=number('1')))
PEAK = obj(dict(well=TEXT, fluorophore=TEXT, peaks_k=array(number('K'), 0, 8), heights=array(number('1/K'), 0, 8)))
STD = obj(dict(target=TEXT, fluorophore=TEXT, slope=nullable(number('1')), intercept=nullable(number('1')), r2=nullable(number('1')), efficiency=nullable(number('1')),
               points=INT, unknowns=array(obj(dict(well=TEXT, cq=nullable(number('1')), quantity_copies=nullable(number('1')))), 0, NW)))
GEX = obj(dict(sample=TEXT, target=TEXT, delta_cq=nullable(number('1')), delta_delta_cq=nullable(number('1')), relative_quantity=nullable(number('1'))))
RUN_REC = obj(dict(run_id=TEXT, protocol=TEXT, plate_id=nullable(TEXT), status=TEXT, started_s=number('s'), finished_s=nullable(number('s')), reads=INT, cycles=INT,
                   scan_mode=TEXT, warnings=array(enum(RUN_WARNINGS), 0, 50)))
LEDGER_KEYS = ['electrical_energy_j', 'peltier_heat_pumped_j', 'lid_heater_energy_j', 'amplicon_copies_made', 'primer_consumed_mol', 'dntp_consumed_mol',
               'probe_cleaved_mol', 'primer_dimer_copies_made', 'water_evaporated_m3', 'plate_reads', 'well_reads']
LEDGER = obj({k: number('J' if k.endswith('_j') else 'mol' if k.endswith('_mol') else 'm3' if k.endswith('_m3') else '1') for k in LEDGER_KEYS})
TRUTH_WELL = obj(dict(well=TEXT, occupied=BOOL, sample_temperature_k=number('K'), volume_m3=number('m3'), polymerase_active=number('1'), cycles_completed=INT,
                      assays=array(obj(dict(assay=TEXT, copies=number('1'), primer_remaining=number('1'), probe_cleaved=number('1'), last_efficiency=nullable(number('1')),
                                            melt_tm_k=number('K'))), 0, MAX_ASSAYS), primer_dimer_copies=number('1')))

cap('plant.configure', PLANT, obj(dict(plant=obj(dict(room_k=number('K'), mains_voltage_v=number('V'), noise_seed=INT)))), PLANT_EXAMPLE, ['chassis'],
    '宿主声明实验室条件：室温（288–308 K）、供电电压与噪声种子（只在首次声明时生效）', 'evaluator', 'simulation')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['switch_power'], '开机/关机（等同按后面板电源开关到指定位置）')
cap('power.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['switch_power'], '按一下后面板电源开关（切换开/关）')
cap('lid.open', obj({}), obj(dict(target_m=number('m'))), {}, ['lid', 'touch_screen'], '在触摸屏点Open Lid：电动盖退回后罩（约6 s）；运行中不能开盖')
cap('lid.close', obj({}), obj(dict(target_m=number('m'))), {}, ['lid', 'touch_screen'], '在触摸屏点Close Lid：电动盖关上并压住板（约6 s）；遇阻时停止')
cap('screen.tilt', obj(dict(angle_rad=number('rad', 12.0 * math.pi / 180, 55.0 * math.pi / 180))), obj(dict(angle_rad=number('rad'), tilt_from_reference_rad=number('rad'))),
    dict(angle_rad=30.0 * math.pi / 180), ['touch_screen'], '用手把触摸屏倾角调到12–55°（相对竖直）', 'agent', 'robot_skill')
COPIES_MAP = dict(type='object', propertyNames=LIBID, additionalProperties=number('1/m3', 0.0, 1e21), maxProperties=MAX_ASSAYS)
cap('sample.provide', obj(dict(sample_id=SID, kind=enum(['template', 'standard']), copies_per_m3=COPIES_MAP, inhibitor=number('1', 0.0, 1.0),
                               declared_copies_per_m3=nullable(COPIES_MAP)), optional=('inhibitor', 'declared_copies_per_m3')),
    obj(dict(sample_id=TEXT)), dict(sample_id='gapdh_std', kind='standard', copies_per_m3={'hs_gapdh': 5.0e14}, declared_copies_per_m3={'hs_gapdh': 5.0e14}),
    ['sample_block'], '评测者准备一个核酸样品：每个检测的真实拷贝浓度（copies/m3，键为assay库ID）、抑制物强度；标准品可声明标称浓度', 'evaluator', 'simulation')
cap('plate.prepare', obj(dict(plate_id=SID, plate_type=enum(list(PLATE_TYPES)), seal=enum(SEALS), wells=array(WELL_SPEC, 1, NW))),
    obj(dict(plate_id=TEXT, wells=INT, warnings=array(enum(['unsealed_plate_evaporation']), 0, 1)), optional=('warnings',)),
    PLATE_EXAMPLE, ['sample_block'], '在实验台上配一块96孔板：每孔的样品类型、样品与稀释倍数、模板体积、反应体积、检测（引物对，≤5个）、化学（SYBR/EvaGreen/探针）、'
    '探针报告染料、标准品拷贝数与生物学分组；封膜方式', 'agent', 'robot_skill')
cap('plate.load', obj(dict(plate_id=SID)), obj(dict(plate_id=TEXT)), dict(plate_id='plate_1'), ['sample_block'], '把配好的板压入样品块（盖子须已退回）', 'agent', 'robot_skill')
cap('plate.remove', obj({}), obj(dict(plate_id=TEXT)), {}, ['sample_block'], '从样品块取出板（盖子须已退回、不在运行中）', 'agent', 'robot_skill')
cap('protocol.set', obj(dict(name=dict(type='string', minLength=1, maxLength=32), sample_volume_m3=number('m3', 0.0, 5e-8), lid_temperature_k=number('K', 303.15, 383.15),
                             steps=array(STEP, 1, MAX_STEPS)), optional=('lid_temperature_k',)),
    obj(dict(name=TEXT, steps=INT, estimated_duration_s=number('s'), control_mode=enum(['calculated', 'block']))), PROTOCOL_EXAMPLE, ['touch_screen'],
    '新建/编辑协议：样品体积（0为块模式）、热盖温度（默认105 °C）、温度/梯度/GOTO（默认39次）/熔解步骤（保持时间、读板、升降温速率、每循环增量与延时、蜂鸣）；返回的预计时长按全通道扫描计算')
cap('run.start', obj(dict(scan_mode=enum(list(SCAN_S)))), OPERATION, dict(scan_mode='all_channels'), ['touch_screen', 'sample_block', 'lid'],
    '开始运行（盖子须关闭、已载入协议）：热盖先升温，然后按协议控温、读板；异步操作')
cap('run.pause', obj({}), obj(dict(state=TEXT)), {}, ['touch_screen'], '暂停运行（保持当前温度，计时暂停）')
cap('run.resume', obj({}), obj(dict(state=TEXT)), {}, ['touch_screen'], '继续已暂停的运行')
cap('run.skip_step', obj({}), obj(dict(step=INT)), {}, ['touch_screen'], '跳过当前步骤（GOTO循环内跳到下一步）')
cap('data.amplification', obj(dict(run_id=nullable(TEXT), wells=nullable(array(WELL, 1, NW)), fluorophore=nullable(LIBID)), optional=('run_id', 'wells', 'fluorophore')),
    obj(dict(run_id=TEXT, curves=array(CURVE, 0, NW * MAX_ASSAYS), baseline_cycles=array(INT, 2, 2))), dict(wells=['A1']), ['touch_screen'],
    '读扩增曲线：每孔每染料每循环的RFU与基线扣除后的RFU（出厂染料校准做颜色补偿）', 'agent', 'query')
cap('data.melt', obj(dict(run_id=nullable(TEXT), wells=nullable(array(WELL, 1, NW))), optional=('run_id', 'wells')), obj(dict(run_id=TEXT, curves=array(MELT, 0, NW * MAX_ASSAYS))),
    dict(wells=['A1']), ['touch_screen'], '读熔解曲线：温度、RFU与−dRFU/dT', 'agent', 'query')
cap('analysis.cq', obj(dict(run_id=nullable(TEXT), threshold_rfu=nullable(number('1', 0.0, 1e6)), baseline=nullable(array(integer(1, 199), 2, 2))),
                       optional=('run_id', 'threshold_rfu', 'baseline')),
    obj(dict(run_id=TEXT, thresholds=array(obj(dict(fluorophore=TEXT, threshold_rfu=number('1'), auto=BOOL)), 0, 128), results=array(CQ_ROW, 0, NW * MAX_ASSAYS))),
    {}, ['touch_screen'], '计算Cq：基线扣除（自动或给定基线循环）、阈值（自动=基线标准差的10倍，或给定）、分数Cq', 'agent', 'query')
cap('analysis.standard_curve', obj(dict(run_id=nullable(TEXT), threshold_rfu=nullable(number('1', 0.0, 1e6))), optional=('run_id', 'threshold_rfu')),
    obj(dict(run_id=TEXT, curves=array(STD, 0, NW * MAX_ASSAYS))), {}, ['touch_screen'], '标准曲线：Cq对log10拷贝数回归（斜率、截距、R2、效率），给出未知样品拷贝数', 'agent', 'query')
cap('analysis.melt_peaks', obj(dict(run_id=nullable(TEXT), min_height_fraction=nullable(number('1', 0.0, 1.0))), optional=('run_id', 'min_height_fraction')),
    obj(dict(run_id=TEXT, peaks=array(PEAK, 0, NW * MAX_ASSAYS))), {}, ['touch_screen'], '熔解峰：−dRFU/dT峰的温度与高度（峰高下限为最高峰的比例）', 'agent', 'query')
cap('analysis.gene_expression', obj(dict(run_id=nullable(TEXT), reference_targets=array(LIBID, 1, 4), control_group=SID, use_efficiency=BOOL), optional=('run_id', 'use_efficiency')),
    obj(dict(run_id=TEXT, results=array(GEX, 0, 400))), dict(reference_targets=['hs_gapdh'], control_group='ctrl'), ['touch_screen'],
    '基因表达：按生物学分组平均Cq，ΔCq（对参照基因）与ΔΔCq（对对照组），相对表达量（效率校正可选）', 'agent', 'query')
cap('data.export', obj(dict(run_id=nullable(TEXT), kind=enum(['amplification_csv', 'cq_csv', 'melt_csv', 'rdml'])), optional=('run_id',)),
    obj(dict(file=TEXT, bytes=INT, rows=INT)), dict(kind='cq_csv'), ['usb_front_port', 'touch_screen'], '把扩增、Cq、熔解数据或RDML导出到U盘（写文件并记事件，不改状态槽）', 'agent',
    'device_command')
cap('runs.list', obj({}), obj(dict(runs=array(RUN_REC, 0, MAX_RUNS))), {}, ['touch_screen'], '列出运行记录', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), ['touch_screen'], '读事件日志',
    'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(name=TEXT, file=TEXT, entries=INT)), 0, 8), channels=array(obj(dict(channel=TEXT, excitation_m=array(number('m'), 2, 2),
                                                                                                                                  detection_m=array(number('m'), 2, 2), dye=TEXT, evidence=TEXT)), 0, 8))),
    {}, ['touch_screen'], '读模板库与通道波段来源', 'agent', 'query')
cap('well.inspect', obj(dict(well=WELL)), obj(dict(truth=TRUTH_WELL)), dict(well='A1'), ['sample_block'], '评测者读某孔真值：样品温度、体积、酶活化、每个检测的拷贝数、'
    '剩余引物、切割探针、上一循环效率、引物二聚体', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=LEDGER)), {}, ['chassis'], '读守恒账目（电能、Peltier搬运热、热盖能量、扩增子、引物/dNTP/探针消耗、蒸发、读板次数）', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='lid_obstructed', active=True), ['chassis', 'lid'],
    '评测者故障注入：断电、盖子遇阻、某通道LED失效、风扇故障（降温变慢）、块温传感器漂移', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 7 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), ['chassis'], '推进宿主时钟（0.1 s物理网格）', 'evaluator',
    'simulation')
UNSUPPORTED = {
    'user.login': '用户账户与权限（Admin/Guest、密码）未建模',
    'brio.connect': 'BR.io云平台、邮件与网络文件夹未建模',
    'precision_melt.analyze': 'Precision Melt Analysis（另售软件的高分辨熔解分型）未建模',
    'allelic_discrimination.analyze': '等位基因分型（终点荧光聚类）未建模',
    'dye.calibrate': '新染料校准（用户校准板）未建模，只用出厂校准的染料',
    'firmware.update': '固件/软件升级未建模',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, ['touch_screen'], v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


_PN = lambda u: nullable(number(u))                                             # noqa: E731
slot('clock.time_s', number('s'), 's', 'derived', 'chassis', '仿真时钟（0.1 s物理网格）')
slot('plant.configured', BOOL, '1', 'setpoint', 'chassis', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'derived', 'switch_power', '整机通电（开关打开且市电正常）')
slot('lid.position_m', number('m'), 'm', 'derived', 'lid', '盖子退回距离（0关闭，0.232 m全开；场景盖子关节镜像）', reference='关闭位置')
slot('lid.state', enum(['closed', 'open', 'opening', 'closing', 'stopped']), '1', 'derived', 'lid', '盖子状态（stopped：遇阻停止）')
slot('screen.tilt_rad', number('rad'), 'rad', 'setpoint', 'touch_screen', '触摸屏倾角相对12°参考位置的增量（0–0.750 rad，即12–55°；场景屏幕关节镜像；screen.tilt输入的是绝对倾角）',
     reference='12°倾角（厂家可调范围下限；相对竖直的参考方向为假设）')
slot('plate.loaded', BOOL, '1', 'derived', 'sample_block', '样品块上有板')
slot('plate.id', nullable(TEXT), '1', 'derived', 'sample_block', '样品块上的板ID')
slot('plates.on_bench', array(TEXT, 0, 20), '1', 'derived', 'sample_block', '实验台上配好的板')
slot('protocol.name', nullable(TEXT), '1', 'setpoint', 'touch_screen', '当前载入的协议名')
slot('protocol.steps', INT, '1', 'setpoint', 'touch_screen', '协议步骤数')
slot('setpoint.block_k', _PN('K'), 'K', 'setpoint', 'sample_block', '当前步骤的样品块目标温度（梯度步骤为中间行）')
slot('setpoint.lid_k', _PN('K'), 'K', 'setpoint', 'lid', '热盖目标温度')
slot('sensor.block_temperature_k', _PN('K'), 'K', 'sensor', 'sample_block', '样品块温度传感器读数（断电为空）')
slot('sensor.sample_temperature_k', _PN('K'), 'K', 'derived', 'sample_block', '按样品体积计算的样品温度（计算模式；块模式等于块温）')
slot('sensor.lid_temperature_k', _PN('K'), 'K', 'sensor', 'lid', '热盖温度读数（断电为空）')
slot('display.block_temperature_c', _PN('°C'), '°C', 'derived', 'display_block_digits',
     '屏上温度（面板显示值，°C，一位小数）：计算模式运行中为按协议样品体积计算的样品温度，块模式或空闲时为块温；断电为空')
slot('display.cycle', nullable(INT), '1', 'derived', 'display_cycle_digits', '屏上当前循环数（运行中显示，否则为空）')
slot('run.state', enum(['idle', 'lid_heating', 'running', 'paused', 'power_failed', 'completed', 'stopped', 'error']), '1', 'derived', 'touch_screen',
     '运行状态（power_failed：断电中，来电后自动继续）')
slot('run.id', nullable(TEXT), '1', 'derived', 'touch_screen', '当前/最近一次运行ID')
slot('run.step', nullable(INT), '1', 'derived', 'touch_screen', '当前步骤（1起）')
slot('run.cycle', nullable(INT), '1', 'derived', 'touch_screen', '当前循环数（读板步骤执行次数+1）')
slot('run.reads', INT, '1', 'derived', 'touch_screen', '本次运行的读板次数')
slot('run.remaining_s', _PN('s'), 's', 'derived', 'touch_screen', '估计剩余时间')
slot('run.scan_mode', nullable(enum(list(SCAN_S))), '1', 'setpoint', 'touch_screen', '扫描模式（全通道12 s / SYBR-FAM 3 s / FRET 3 s，FRET时间为假设）')
slot('run.warnings', array(enum(RUN_WARNINGS), 0, 50), '1', 'derived', 'touch_screen', '本次运行的警告（POWER_FAILURE：运行中断电，来电后自动继续）')
slot('operation.kind', nullable(enum(['run'])), '1', 'derived', 'touch_screen', '进行中的异步操作')
slot('operation.progress', number('1'), '1', 'derived', 'touch_screen', '运行进度（0–1）')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'touch_screen', '进行中的运行是否由另一个agent启动')
slot('lamp.status_green', BOOL, '1', 'derived', 'status_bar_green', '状态灯条绿（运行中常亮/暂停闪；有错误时让位给红色）')
slot('lamp.status_blue', BOOL, '1', 'derived', 'status_bar_blue', '状态灯条蓝（运行完成，开盖前闪；有错误或运行中时不亮）')
slot('lamp.status_red', BOOL, '1', 'derived', 'status_bar_red', '状态灯条红（系统错误，闪；优先于绿与蓝）')
slot('status_bar.flashing', BOOL, '1', 'derived', 'status_bar_green', '当前亮着的颜色是否闪烁（暂停的绿、完成的蓝、错误的红）')
slot('errors.active', array(TEXT, 0, 16), '1', 'derived', 'touch_screen', '当前错误')
slot('runs.count', INT, '1', 'derived', 'touch_screen', '运行记录数')
slot('truth.block_temperature_k', number('K'), 'K', 'process_truth', 'sample_block', '样品块平均温度真值', 'evaluator')
slot('truth.lid_temperature_k', number('K'), 'K', 'process_truth', 'lid', '热盖温度真值', 'evaluator')
slot('truth.sample_temperature_mean_k', _PN('K'), 'K', 'process_truth', 'sample_block', '有样品孔的平均样品温度真值', 'evaluator')
slot('truth.peltier_power_w', number('W'), 'W', 'process_truth', 'sample_block', 'Peltier电功率（工程估计）', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'chassis', '注入的故障', 'evaluator')


# ---------------------------------------------------------------- physics helpers
def melt_fraction(T, tm, width_k=None):
    """Fraction of a duplex melted at T (two-state logistic; FWHM of -dF/dT = width_k)."""
    s = (CHEM['melt_width_k'] if width_k is None else width_k) / 3.525
    return 1.0 / (1.0 + np.exp(-(T - tm) / s))


def pol_speed(T):
    """Polymerase extension speed (nt/s): Taq-like optimum at 72 C (engineering)."""
    return CHEM['pol_speed_nt_s'] * np.exp(-((T - CHEM['pol_opt_k']) / CHEM['pol_width_k']) ** 2)


def anneal_factor(t_anneal, tm_primer):
    """Primer annealing completeness vs. the annealing temperature: logistic above (primer Tm + 3 K); below the optimum (primer Tm + 1 K) a mild
    competition penalty of 0.95 %/K (fitted to the brochure GAPDH gradient: E 98.7 % at 62 C, 93.6 % at 56 C)."""
    hi = 1.0 / (1.0 + np.exp((t_anneal - (tm_primer + CHEM['primer_mg_shift_k'] + CHEM['anneal_offset_k'])) / CHEM['anneal_width_k']))
    lo = 1.0 - CHEM['low_penalty_per_k'] * np.maximum(0.0, (tm_primer + CHEM['low_ref_offset_k']) - t_anneal)
    return hi * np.clip(lo, 0.0, 1.0)


def _poisson(lam, seed, a, b):
    """Deterministic Poisson draw (inverse transform for small means, normal approximation with continuity above 1000)."""
    if lam <= 0:
        return 0.0
    u = _uniform(seed, a, b)
    if lam > 50:
        return max(0.0, round(lam + math.sqrt(lam) * _gauss(seed, a, b + 7)))
    k, p = 0, math.exp(-lam)
    c = p
    while u > c and k < 10000:
        k += 1
        p *= lam / k
        c += p
    return float(k)


class BioradCfxOpusRuntime:
    def __init__(self, instance_id='cfx_opus_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.tick = 0
        self.plant = None
        self.switch_on = False
        self.power = False
        self.lid = dict(a0=0.0, t0=0.0, target=0.0, stopped=False)
        self.screen = 0.0
        self.T_row = None
        self.lid_T = None
        self.lid_set = None
        self.block_set = None                       # per-row targets (np.array) or None (Peltier idle)
        self.ramp_cap = RAMP_MAX
        self.Ts = np.zeros(NW)
        self.samples = {}
        self.plates = {}
        self.loaded = None
        self.protocol = None
        self.run = None
        self.runs = {}
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.faults = set()
        self.errors = {}
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.revision = 0
        self.seq = dict(op=0, run=0)
        self._principals = {}
        self._requests = {}
        self._owners = {}
        self._caller = None
        self._peltier_w = 0.0
        self._reset_wells()

    # ---------------------------------------------------------------- sessions
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
        return BioradCfxOpusSession(self, name)

    def _event(self, kind, code, message, ref=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message, _ref=ref))

    def _error(self, code, message):
        if code not in self.errors:
            self.errors[code] = message
            self._event('error', code, message)

    # ---------------------------------------------------------------- wells (numpy state of the loaded plate)
    def _reset_wells(self):
        A = MAX_ASSAYS
        self.occ = np.zeros(NW, bool)
        self.vol = np.zeros(NW)
        self.chem_kind = np.full(NW, -1)                 # index in CHEMISTRIES
        self.act = np.zeros(NW)
        self.inh = np.ones(NW)
        self.pd = np.zeros(NW)
        self.pd_seed = np.zeros(NW)
        self.p0 = np.zeros((NW, A))
        self.P = np.zeros((NW, A))
        self.N = np.zeros((NW, A))
        self.N_start = np.zeros((NW, A))
        self.probe0 = np.zeros((NW, A))
        self.cleaved = np.zeros((NW, A))
        self.active = np.zeros((NW, A), bool)
        self.tm_amp = np.full((NW, A), 360.0)
        self.L = np.full((NW, A), 100.0)
        self.e_max = np.zeros((NW, A))
        self.tm_p = np.full((NW, A), 333.0)
        self.dye = np.full((NW, A), -1)
        self.theta = np.zeros((NW, A))
        self.ext = np.zeros(NW)
        self.tmin = np.full(NW, np.inf)
        self.phase = np.zeros(NW, int)                   # 0 idle, 1 hot (denaturing), 2 cool (priming / extension)
        self.cycles_done = np.zeros(NW, int)
        self.last_e = np.full((NW, A), np.nan)
        self.dye_ids = []                                # fluorophore ids used on the loaded plate (column index of self.dye)
        self.well_dye = np.full(NW, -1)                  # intercalating dye of a SYBR / EvaGreen well (index in dye_ids), also without assays
        self._P_start = np.zeros((NW, A))

    # ---------------------------------------------------------------- plant, power
    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _room(self):
        return self.plant['room_k']

    def _mains_ok(self):
        v = self.plant['mains_voltage_v']
        return 'power_loss' not in self.faults and 90.0 <= v <= 264.0          # 100-240 VAC +-10 %

    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed):
        if self.plant is not None and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, noise_seed=noise_seed)
        if first:
            self.T_row = np.full(len(ROWS), room_k)
            self.lid_T = room_k
            self.Ts[:] = room_k
            s = noise_seed
            self._acc = 0.15 * (2 * _uniform(s, 1, 1) - 1)                                   # block accuracy (K at 90 C), within +-0.2
            edge = np.array([1.0 if (w[0] in 'AH' or w[1:] in ('1', '12')) else 0.4 for w in WELLS])
            self._unif = np.array([0.28 * edge[i] * (2 * _uniform(s, 2, i) - 1) for i in range(NW)])   # well-to-well (K at 90 C), within +-0.3
        if self.power and not self._mains_ok():
            self._power_off('mains')
        elif self.switch_on and not self.power and self._mains_ok():
            self._power_up()
        return dict(plant=dict(self.plant))

    def _power_up(self):
        self.power = True
        self.lid_set = None
        self._event('power', 'POWER_ON', 'CFX Opus通电：触摸屏启动，样品块与热盖待机')
        r = self.run
        if r is not None and r['state'] == 'power_failed':                 # the run in progress resumes automatically (guide p.121)
            prev = r.pop('before_failure')
            r['state'], r['lid_wait_s'] = 'lid_heating', 0.0
            r['calc_T'] = np.array(self._sensor_rows(), float)                   # the restarted firmware only knows the block sensor
            r['resume_state'] = None if prev == 'lid_heating' else prev
            s = r['protocol']['steps'][r['k']] if (prev != 'lid_heating' and r['k'] < len(r['protocol']['steps'])) else None
            self.lid_set = self._lid_target(s, r['target']) if s is not None else r['protocol']['lid_temperature_k']
            self._event('run', 'RUN_RESUMED_AFTER_POWER_FAILURE', '来电：显示断电警告，热盖重新升温后自动继续中断的运行', ref=r['run_id'])

    def _power_off(self, cause):
        r = self.run
        if r is not None and r['state'] in ('lid_heating', 'running', 'paused'):
            if cause == 'switch':
                self._finish_run('stopped', None)                              # switched off by the operator: the run ends (assumption)
            else:
                # an outage while the lid re-heats after an earlier outage keeps the step interrupted first
                r['before_failure'] = r['resume_state'] if (r['state'] == 'lid_heating' and r.get('resume_state') is not None) else r['state']
                if r['phase'] == 'read':
                    r['hold_left'] = 0.0                                         # the interrupted plate read is repeated after the restart
                r['state'] = 'power_failed'
                r['warnings'].append('POWER_FAILURE')
                self._event('run', 'RUN_POWER_FAILURE', '运行中断电：来电后自动继续（厂家）', ref=r['run_id'])
        self.power = False
        self.block_set = None
        self.lid_set = None
        if self.lid_state() in ('opening', 'closing'):
            self._set_lid(self._lid_pos())                                   # the motor stops where it is
        self._event('power', 'POWER_OFF', '断电（开关关闭）' if cause == 'switch' else '断电（市电中断或电压超范围）')

    def _set_power(self, enabled):
        self._need_plant()
        if enabled:
            if not self.power:
                if not self._mains_ok():
                    raise ActionRejected('MAINS_OUT_OF_RANGE')
                self.switch_on = True
                self._power_up()
        else:
            self.switch_on = False
            if self.power:
                self._power_off('switch')
            elif self.run is not None and self.run['state'] == 'power_failed':
                self._finish_run('stopped', None)                              # switched off during an outage: the run ends (same as while powered)
        return dict(enabled=self.power)

    def _cmd_power_set(self, enabled):
        return self._set_power(enabled)

    def _cmd_power_press_switch(self):
        return self._set_power(not self.switch_on)

    # ---------------------------------------------------------------- lid (analytic ramp) and screen
    def _lid_pos(self, t=None):
        d = self.lid
        t = self.t if t is None else t
        run_ = LID_TRAVEL / LID_TIME_S * max(0.0, t - d['t0'])
        return min(d['target'], d['a0'] + run_) if d['target'] >= d['a0'] else max(d['target'], d['a0'] - run_)

    def _set_lid(self, target):
        self.lid = dict(a0=self._lid_pos(), t0=self.t, target=target, stopped=self.lid.get('stopped', False) and target == self._lid_pos())

    def lid_state(self):
        a, tgt = self._lid_pos(), self.lid['target']
        if self.lid.get('stopped'):
            return 'stopped'
        if abs(a - tgt) > 1e-12:
            return 'opening' if tgt > a else 'closing'
        return 'open' if a >= LID_TRAVEL - 1e-9 else 'closed' if a <= 1e-9 else 'stopped'

    def _need_lid_open(self):
        self._need_plant()
        if self.lid_state() != 'open':
            raise ActionRejected('LID_NOT_OPEN')

    def _running(self):
        return self.run is not None and self.run['state'] in ('lid_heating', 'running', 'paused', 'power_failed')

    def _cmd_lid_open(self):
        self._need_power()
        if self._running():
            raise ActionRejected('RUN_IN_PROGRESS')
        self.lid['stopped'] = False
        self.errors.pop('LID_OBSTRUCTED', None)                                 # the operator retries; a persisting obstruction stops the lid again
        self._set_lid(LID_TRAVEL)
        if self.run is not None and self.run['state'] == 'completed':
            self.run['acknowledged'] = True                                   # the blue flashing ends when the lid is opened
        self._event('lid', 'LID_OPEN', '盖子退回（Open Lid）')
        return dict(target_m=LID_TRAVEL)

    def _cmd_lid_close(self):
        self._need_power()
        if self._running():
            raise ActionRejected('RUN_IN_PROGRESS')
        self.lid['stopped'] = False
        self.errors.pop('LID_OBSTRUCTED', None)
        self._set_lid(0.0)
        self._event('lid', 'LID_CLOSE', '盖子关闭（Close Lid）')
        return dict(target_m=0.0)

    def _cmd_screen_tilt(self, angle_rad):
        self._need_plant()
        self.screen = float(angle_rad) - 12.0 * math.pi / 180
        return dict(angle_rad=float(angle_rad), tilt_from_reference_rad=self.screen)

    # ---------------------------------------------------------------- samples and plates
    def _cmd_sample_provide(self, sample_id, kind, copies_per_m3, inhibitor=0.0, declared_copies_per_m3=None):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('ITEM_EXISTS')
        lib = assays()
        if any(k not in lib for k in copies_per_m3) or (declared_copies_per_m3 and any(k not in lib for k in declared_copies_per_m3)):
            raise ActionRejected('UNKNOWN_ASSAY')
        self.samples[sample_id] = dict(kind=kind, copies_per_m3=dict(copies_per_m3), inhibitor=float(inhibitor), declared=dict(declared_copies_per_m3 or {}))
        return dict(sample_id=sample_id)

    def _cmd_plate_prepare(self, plate_id, plate_type, seal, wells):
        self._need_plant()
        if plate_id in self.plates:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.plates) >= 20:
            raise ActionRejected('TOO_MANY_ITEMS')
        lib, fl = assays(), fluorophores()
        seen, spec = set(), {}
        seed = self.plant['noise_seed']
        pidx = len(self.seq) + sum(ord(ch) for ch in plate_id)
        for wsp in wells:
            w = wsp['well']
            if w in seen:
                raise ActionRejected('DUPLICATE_WELL')
            seen.add(w)
            asy = list(wsp['assays'])
            if any(a not in lib for a in asy):
                raise ActionRejected('UNKNOWN_ASSAY')
            if len(set(asy)) != len(asy):
                raise ActionRejected('DUPLICATE_WELL')
            rep = list(wsp.get('reporters') or [])
            if wsp['chemistry'] == 'probe':
                if len(rep) != len(asy) or any(d not in fl or fl[d]['role'] != 'reporter' or not fl[d].get('best_channel') for d in rep) or len(set(rep)) != len(rep):
                    raise ActionRejected('INVALID_REPORTERS')                   # a reporter needs measured spectra (channel response); VIC has none in FPbase
            elif rep or len(asy) > 1:
                raise ActionRejected('INVALID_REPORTERS')                       # intercalating dyes cannot separate several targets in one well
            vol = wsp['reaction_volume_m3']
            tv = wsp.get('template_volume_m3', 0.0)
            sid = wsp.get('sample_id')
            if tv > vol + 1e-15:
                raise ActionRejected('VOLUME_OUT_OF_RANGE')
            if vol > PLATE_TYPES[plate_type]['max_ul'] * 1e-9:
                raise ActionRejected('VOLUME_OUT_OF_RANGE')
            if sid is not None and sid not in self.samples:
                raise ActionRejected('UNKNOWN_SAMPLE')
            if wsp['sample_type'] in ('ntc',) and sid is not None:
                raise ActionRejected('NTC_WITH_TEMPLATE')
            if wsp['sample_type'] == 'standard' and wsp.get('quantity_copies') is None:
                raise ActionRejected('STANDARD_QUANTITY_REQUIRED')
            copies = {}
            for j, a in enumerate(asy):
                lam = 0.0
                if sid is not None:
                    lam = self.samples[sid]['copies_per_m3'].get(a, 0.0) * wsp.get('dilution', 1.0) * tv
                    lam *= 1.0 + 0.01 * _gauss(seed, pidx * 131 + WIDX[w], 11 + j)       # pipetting CV 1 % (engineering)
                copies[a] = _poisson(max(0.0, lam), seed, pidx * 977 + WIDX[w], 31 + j)
            spec[w] = dict(well=w, sample_type=wsp['sample_type'], sample_id=sid, dilution=wsp.get('dilution', 1.0), template_volume_m3=tv, volume_m3=vol,
                           assays=asy, chemistry=wsp['chemistry'], reporters=rep, quantity_copies=wsp.get('quantity_copies'),
                           biological_group=wsp.get('biological_group'), copies=copies,
                           inhibitor=self.samples[sid]['inhibitor'] if sid is not None else 0.0)
        self.plates[plate_id] = dict(plate_id=plate_id, plate_type=plate_type, seal=seal, wells=spec, location='bench')
        if self._caller is not None:
            self._owners[plate_id] = self._caller
        out = dict(plate_id=plate_id, wells=len(spec))
        if seal == 'none':
            out['warnings'] = ['unsealed_plate_evaporation']
        return out

    def _cmd_plate_load(self, plate_id):
        self._need_lid_open()
        p = self.plates.get(plate_id)
        if p is None:
            raise ActionRejected('UNKNOWN_ITEM')
        if self.loaded is not None:
            raise ActionRejected('BLOCK_OCCUPIED')
        if p['location'] != 'bench':
            raise ActionRejected('PLATE_NOT_ON_BENCH')
        p['location'] = 'block'
        self.loaded = plate_id
        self._build_wells(p)
        self._event('plate', 'PLATE_LOADED', f'板{plate_id}放入样品块', ref=plate_id)
        return dict(plate_id=plate_id)

    def _cmd_plate_remove(self):
        self._need_lid_open()
        if self._running():
            raise ActionRejected('RUN_IN_PROGRESS')
        if self.loaded is None:
            raise ActionRejected('NO_PLATE')
        pid = self.loaded
        self.plates[pid]['location'] = 'bench'
        self._store_wells(self.plates[pid])
        self.loaded = None
        self._reset_wells()
        self._event('plate', 'PLATE_REMOVED', f'板{pid}取出', ref=pid)
        return dict(plate_id=pid)

    def _build_wells(self, p):
        self._reset_wells()
        lib, room = assays(), self._room()
        dyes = []
        for w, s in p['wells'].items():
            i = WIDX[w]
            self.occ[i] = True
            self.vol[i] = s.get('state', {}).get('volume_m3', s['volume_m3'])
            self.chem_kind[i] = CHEMISTRIES.index(s['chemistry'])
            self.inh[i] = 1.0 - s['inhibitor']
            self.Ts[i] = s.get('state', {}).get('Ts', room)
            st = s.get('state')
            for j, a in enumerate(s['assays']):
                e = lib[a]
                self.active[i, j] = True
                self.tm_amp[i, j] = e['melt_tm_k'] + CHEM['melt_tm_offset_k']
                self.L[i, j] = e['amplicon_length_bp']
                self.e_max[i, j] = e['efficiency_max']
                if j == 0:
                    self.pd_seed[i] = CHEM['pd_seed_base'] * 2.0 ** (e['primer_dimer_score'] - 2)
                self.tm_p[i, j] = e['primer_tm_k']
                p0 = CHEM['primer_mol_m3'] * s['volume_m3'] * AVOGADRO                 # copies of each primer in the well
                self.p0[i, j] = p0
                self.P[i, j] = p0 if st is None else st['P'][j]
                self.N[i, j] = s['copies'][a] if st is None else st['N'][j]
                if s['chemistry'] == 'probe':
                    self.probe0[i, j] = CHEM['probe_mol_m3'] * s['volume_m3'] * AVOGADRO
                    self.cleaved[i, j] = 0.0 if st is None else st['cleaved'][j]
                    d = s['reporters'][j]
                else:
                    d = 'sybr_green' if s['chemistry'] == 'sybr' else 'evagreen'
                if d not in dyes:
                    dyes.append(d)
                self.dye[i, j] = dyes.index(d)
            if s['chemistry'] != 'probe':                                     # the supermix dye is present even without primers
                d = 'sybr_green' if s['chemistry'] == 'sybr' else 'evagreen'
                if d not in dyes:
                    dyes.append(d)
                self.well_dye[i] = dyes.index(d)
            if st is not None:
                self.act[i], self.pd[i], self.cycles_done[i] = st['act'], st['pd'], st['cycles']
        self.dye_ids = dyes

    def _store_wells(self, p):
        for w, s in p['wells'].items():
            i = WIDX[w]
            n = len(s['assays'])
            s['state'] = dict(volume_m3=float(self.vol[i]), Ts=float(self.Ts[i]), P=self.P[i, :n].tolist(), N=self.N[i, :n].tolist(),
                              cleaved=self.cleaved[i, :n].tolist(), act=float(self.act[i]), pd=float(self.pd[i]), cycles=int(self.cycles_done[i]))

    # ---------------------------------------------------------------- protocol
    def _cmd_protocol_set(self, name, sample_volume_m3, steps, lid_temperature_k=LID_DEFAULT_K):
        self._need_power()
        if self._running():
            raise ActionRejected('RUN_IN_PROGRESS')
        if 0.0 < sample_volume_m3 < 1e-9 - 1e-18:
            raise ActionRejected('INVALID_SAMPLE_VOLUME')                       # 0 (block mode) or 1-50 ul
        c01 = lambda T: round((float(T) - 273.15) * 10) / 10 + 273.15           # noqa: E731  0.1 C display resolution
        r01 = lambda x: round(float(x) * 10) / 10                                # noqa: E731
        norm, last_goto = [], -1
        for k, s in enumerate(steps):
            kind = s['kind']
            if kind != 'temperature' and s.get('beep'):
                raise ActionRejected('INVALID_STEP')                            # Table 10: the alert beep belongs to temperature steps
            if kind == 'temperature':
                if s.get('temperature_k') is None:
                    raise ActionRejected('INVALID_STEP')
                if s.get('lower_k') is not None or s.get('upper_k') is not None or s.get('goto_step') is not None or s.get('start_k') is not None:
                    raise ActionRejected('INVALID_STEP')
                hold = s.get('hold_s')
                norm.append(dict(kind=kind, temperature_k=c01(s['temperature_k']), hold_s=None if hold is None else float(round(hold)), plate_read=bool(s.get('plate_read', False)),
                                 ramp_rate_k_s=None if s.get('ramp_rate_k_s') is None else r01(s['ramp_rate_k_s']), increment_k=r01(s.get('increment_k', 0.0)),
                                 extend_s=float(round(s.get('extend_s', 0.0))), beep=bool(s.get('beep', False))))
            elif kind == 'gradient':
                lo, hi = s.get('lower_k'), s.get('upper_k')
                if lo is None or hi is None or s.get('hold_s') is None:
                    raise ActionRejected('INVALID_STEP')
                lo, hi = c01(lo), c01(hi)
                if not (GRADIENT_RANGE[0][0] - 1e-9 <= lo <= GRADIENT_RANGE[0][1] + 1e-9 and hi <= GRADIENT_RANGE[1] + 1e-9
                        and GRADIENT_RANGE[2][0] - 1e-9 <= hi - lo <= GRADIENT_RANGE[2][1] + 1e-9):
                    raise ActionRejected('INVALID_GRADIENT')
                if s.get('increment_k', 0.0) or s.get('ramp_rate_k_s') is not None or s.get('beep'):
                    raise ActionRejected('INVALID_STEP')                        # increment, ramp rate and beep are for temperature steps only (Table 10)
                norm.append(dict(kind=kind, lower_k=float(lo), upper_k=float(hi), hold_s=float(round(s['hold_s'])), plate_read=bool(s.get('plate_read', False)),
                                 extend_s=float(round(s.get('extend_s', 0.0)))))
            elif kind == 'goto':
                g, n = s.get('goto_step'), s.get('cycles')
                n = GOTO_DEFAULT_CYCLES if n is None else n                       # documented default: 39 repeats
                if g is None or not (last_goto + 1 < g <= k) or norm[g - 1]['kind'] not in ('temperature', 'gradient'):
                    raise ActionRejected('INVALID_GOTO')
                if any(x['hold_s'] is None for x in norm[g - 1:k] if x['kind'] == 'temperature'):
                    raise ActionRejected('INVALID_GOTO')                        # an infinite hold inside a loop never ends
                norm.append(dict(kind=kind, goto_step=int(g), cycles=int(n)))
                last_goto = k
            else:
                a, b, inc = s.get('start_k'), s.get('end_k'), s.get('melt_increment_k', 0.5)
                if a is None or b is None or inc is None:
                    raise ActionRejected('INVALID_MELT')
                a, b, inc = c01(a), c01(b), r01(inc)
                if b - a < 0.5 or inc < 0.1 or inc > b - a:
                    raise ActionRejected('INVALID_MELT')
                norm.append(dict(kind='melt', start_k=float(a), end_k=float(b), melt_increment_k=float(inc), hold_s=float(round(s.get('hold_s') or 5.0)), plate_read=True))
        if any(x['kind'] == 'temperature' and x['hold_s'] is None for x in norm[:-1]):
            raise ActionRejected('INVALID_STEP')                                # an infinite hold is only allowed as the last step
        self.protocol = dict(name=name, sample_volume_m3=float(sample_volume_m3), lid_temperature_k=float(lid_temperature_k), steps=norm)
        est = self._estimate(self.protocol, 'all_channels')
        self._event('protocol', 'PROTOCOL_LOADED', f'协议{name}：{len(norm)}步，预计{est / 60:.1f} min')
        return dict(name=name, steps=len(norm), estimated_duration_s=est, control_mode='calculated' if sample_volume_m3 > 0 else 'block')

    @staticmethod
    def _ramp_time(T_rows, Ts_rows, target, cap, tau_c):
        """Seconds until the engine sees arrival: the slowest row of the block servo (gain, slew cap) and, in calculated mode (tau_c > 0), its modelled
        sample within 0.3 K (block mode: block within 0.1 K)."""
        T_rows, Ts_rows, target = np.asarray(T_rows, float), np.asarray(Ts_rows, float), np.asarray(target, float)
        i = int(np.argmax(np.abs(target - T_rows) + (np.abs(T_rows - Ts_rows) if tau_c > 0 else 0.0)))
        tb, ts, tg = float(T_rows[i]), float(Ts_rows[i]), float(target[i])
        k = math.exp(-DT / tau_c) if tau_c > 0 else 0.0
        n = 0
        while n < 100000 and (abs(ts - tg) >= 0.3 if tau_c > 0 else abs(tb - tg) >= ARRIVE_K):
            tb += min(cap, max(-cap, RAMP_GAIN * (tg - tb))) * DT
            ts = tb + (ts - tb) * k
            n += 1
        return n * DT

    @staticmethod
    def _lid_time(T0, setpoint):
        """Seconds for the heated lid to reach setpoint - 1 K (rate clip((set - T)/20 s, -1, 1.5) K/s)."""
        e = setpoint - T0
        if e <= 1.0:
            return 0.0
        t = max(0.0, (e - 30.0) / 1.5)
        return min(600.0, t + 20.0 * math.log(min(e, 30.0)))

    @staticmethod
    def _cycle_of(steps, loops, k):
        """Cycle index of step k for its per-cycle increment / extension: the pass number of the enclosing GOTO loop (0 outside loops), so skipped
        steps of a loop do not shift later cycles."""
        g = next((i for i in range(k + 1, len(steps)) if steps[i]['kind'] == 'goto'), None)
        return loops.get(g, 0) if (g is not None and steps[g]['goto_step'] - 1 <= k) else 0

    @staticmethod
    def _segment_target(s, e, melt_i):
        if s['kind'] == 'gradient':
            return np.linspace(s['upper_k'], s['lower_k'], len(ROWS))
        if s['kind'] == 'melt':
            return np.full(len(ROWS), s['start_k'] + melt_i * s['melt_increment_k'])
        return np.full(len(ROWS), min(T_BLOCK_RANGE[1], max(T_BLOCK_RANGE[0], s['temperature_k'] + s['increment_k'] * e)))

    @staticmethod
    def _segment_cap(s, melt_i):
        if s['kind'] == 'temperature':
            return s.get('ramp_rate_k_s') or RAMP_MAX
        return 0.5 if (s['kind'] == 'melt' and melt_i > 0) else RAMP_MAX

    def _timeline(self, protocol, scan_mode):
        """The step executions the engine will run (GOTO loops unrolled, melt increments), each with its duration when it starts from the previous
        target: (index, ((k, exec, melt_i) -> position), [(k, e, melt_i, target, cap, hold_s or None, read_s, duration_s)]).  Memoised per protocol."""
        key = json.dumps([protocol['steps'], protocol['sample_volume_m3'], scan_mode], sort_keys=True)
        memo = self.__dict__.setdefault('_tl_memo', {})
        if key in memo:
            return memo[key]
        steps, loops, k, seg = protocol['steps'], {}, 0, []
        scan = SCAN_S[scan_mode]
        while k < len(steps) and len(seg) < 20000:
            s = steps[k]
            if s['kind'] == 'goto':
                n = loops.get(k, 0)
                if n < s['cycles']:
                    loops[k] = n + 1
                    k = s['goto_step'] - 1
                else:
                    k += 1
                continue
            e = self._cycle_of(steps, loops, k)
            if s['kind'] == 'melt':
                n = int(math.floor((s['end_k'] - s['start_k']) / s['melt_increment_k'] + 1e-9)) + 1
                seg += [(k, e, mi, self._segment_target(s, e, mi), self._segment_cap(s, mi), s['hold_s'], scan) for mi in range(n)]
            else:
                hold = None if s['hold_s'] is None else max(1.0, s['hold_s'] + s.get('extend_s', 0.0) * e)
                seg.append((k, e, 0, self._segment_target(s, e, 0), self._segment_cap(s, 0), hold, scan if s.get('plate_read') else 0.0))
                if hold is None:
                    break                                                       # infinite hold: the run waits there until skipped
            k += 1
        tau_c = CHEM['tau_sample_base_s'] + CHEM['tau_sample_per_volume_s_m3'] * protocol['sample_volume_m3'] if protocol['sample_volume_m3'] > 0 else 0.0
        out, prev = [], None
        for (k_, e, mi, tgt, cap, hold, read) in seg:
            ramp = 0.0 if prev is None else self._ramp_time(prev, prev, tgt, cap, tau_c)
            out.append((k_, e, mi, tgt, cap, hold, read, ramp + (hold or 0.0) + read))
            prev = tgt
        memo[key] = ({(x[0], x[1], x[2]): i for i, x in enumerate(out)}, out, tau_c)
        return memo[key]

    def _estimate(self, protocol, scan_mode):
        """Whole-run duration from the present state: lid heating, the first ramp from the present block temperature, then every step execution."""
        pos, seg, tau_c = self._timeline(protocol, scan_mode)
        if not seg:
            return 0.0
        rows = self.T_row if self.T_row is not None else np.full(len(ROWS), 298.15)
        lid = self._lid_time(self.lid_T if self.lid_T is not None else 298.15, protocol['lid_temperature_k'])
        first = self._ramp_time(rows, rows, seg[0][3], seg[0][4], tau_c) + (seg[0][5] or 0.0) + seg[0][6]
        return float(lid + first + sum(x[7] for x in seg[1:]))

    def _mean_block(self):
        return float(np.mean(self.T_row))

    # ---------------------------------------------------------------- run control
    def _clear_resolved_errors(self):
        active = {f'LED_FAILURE_CHANNEL_{c}' for c in range(1, 6) if f'led_failure_channel_{c}' in self.faults}
        if 'fan_failure' in self.faults:
            active.add('BLOCK_TEMPERATURE_TIMEOUT')
        for code in list(self.errors):
            if code not in active:
                del self.errors[code]

    def _cmd_run_start(self, scan_mode):
        self._need_power()
        if self._running():
            raise ActionRejected('RUN_IN_PROGRESS')
        if self.protocol is None:
            raise ActionRejected('NO_PROTOCOL')
        if self.lid_state() != 'closed':
            raise ActionRejected('LID_NOT_CLOSED')
        if len(self.runs) >= MAX_RUNS:
            raise ActionRejected('RUNS_FULL')
        self._clear_resolved_errors()                                          # acknowledged errors whose cause is gone are cleared by a new run
        self.seq['run'] += 1
        self.seq['op'] += 1
        rid, oid = f'R{self.seq["run"]:03d}', f'op{self.seq["op"]:04d}'
        steps = self.protocol['steps']
        amp_step = next((i for i, s in enumerate(steps) if s['kind'] in ('temperature', 'gradient') and s.get('plate_read')), None)
        amp_total = 0
        if amp_step is not None:
            amp_total = 1
            for i, s in enumerate(steps):
                if s['kind'] == 'goto' and s['goto_step'] - 1 <= amp_step < i:
                    amp_total = s['cycles'] + 1
        plate = self.plates.get(self.loaded) if self.loaded else None
        setup = {} if plate is None else {w: {k: v for k, v in s.items() if k not in ('copies', 'inhibitor', 'state')} for w, s in plate['wells'].items()}
        self.run = dict(run_id=rid, op_id=oid, state='lid_heating', scan_mode=scan_mode, protocol=copy.deepcopy(self.protocol), k=0, phase='ramp', hold_left=None,
                        read_left=0.0, loops={}, melt_i=0, amp_step=amp_step, cycle=0, reads=[], started_s=self.t, finished_s=None,
                        acknowledged=False, plate_id=self.loaded, setup=setup, dyes=list(self.dye_ids), target=None, calc_T=np.array(self._sensor_rows(), float), amp_total=amp_total,
                        error=None, ramp_started_s=self.t, lid_wait_s=0.0, warnings=[], resume_state=None)
        self.runs[rid] = self.run
        self.operations[oid] = dict(op=oid, kind='run', phase='running', start=self.t, record_id=rid)
        if self._caller is not None:
            self._owners[oid] = self._caller
            self._owners[rid] = self._caller
        self.lid_set = self.protocol['lid_temperature_k']
        self._timeline(self.run['protocol'], scan_mode)                         # memoise the duration estimate of this protocol
        warn = [] if plate is not None else ['no_plate_on_the_block']
        self._event('run', 'RUN_START', f'运行{rid}开始：热盖升温到{self.lid_set - 273.15:.0f} °C，然后执行协议{self.protocol["name"]}', ref=rid)
        return dict(operation_id=oid, warnings=warn) if warn else dict(operation_id=oid)

    def _cmd_run_pause(self):
        self._need_power()
        if self.run is None or self.run['state'] != 'running':
            raise ActionRejected('NOT_RUNNING')
        self.run['state'] = 'paused'
        self._event('run', 'RUN_PAUSED', '运行暂停（块温保持，计时暂停）', ref=self.run['run_id'])
        return dict(state='paused')

    def _cmd_run_resume(self):
        self._need_power()
        if self.run is None or self.run['state'] != 'paused':
            raise ActionRejected('NOT_PAUSED')
        self.run['state'] = 'running'
        self._event('run', 'RUN_RESUMED', '运行继续', ref=self.run['run_id'])
        return dict(state='running')

    def _cmd_run_skip_step(self):
        """Skip Step (guide p.122): leaves an infinite hold or the current step; inside a GOTO loop the rest of the cycle is skipped (next cycle, or
        out of the loop after its last cycle); a melt curve is skipped as a whole."""
        self._need_power()
        r = self.run
        if r is None or r['state'] not in ('running', 'paused'):
            raise ActionRejected('NOT_RUNNING')
        k = r['k']
        steps = r['protocol']['steps']
        g = next((i for i in range(k + 1, len(steps)) if steps[i]['kind'] == 'goto'), None)
        loop = g if (g is not None and steps[g]['goto_step'] - 1 <= k) else None
        self._event('run', 'STEP_SKIPPED', f'跳过第{k + 1}步' + ('（GOTO循环的本循环余下步骤）' if loop is not None else ''), ref=r['run_id'])
        r['melt_i'] = 0
        self._enter_step(loop if loop is not None else k + 1)
        return dict(step=(self.run['k'] + 1) if self.run is r and r['state'] in ('running', 'paused') else k + 1)

    def _cancel_op(self, op):
        if self.run is None or self.run['op_id'] != op['op'] or not self._running():
            raise ActionRejected('NOT_RUNNING')
        self._finish_run('stopped', None)

    def _finish_run(self, status, code):
        r = self.run
        r['state'] = status
        r['finished_s'] = self.t
        self.block_set = None
        self.lid_set = None
        op = self.operations.get(r['op_id'])
        if op is not None:
            op['phase'] = {'completed': 'succeeded', 'stopped': 'cancelled', 'error': 'failed'}[status]   # protocol.md operation statuses
        if code:
            r['error'] = code
            self._error(code, ERROR_ZH.get(code, code))
        self._event('run', 'RUN_' + status.upper(), f'运行{r["run_id"]}结束：{status}（{len(r["reads"])}次读板）', ref=r['run_id'])

    # ---------------------------------------------------------------- protocol engine
    def _row_targets(self, s, e):
        if s['kind'] == 'gradient':
            return np.linspace(s['upper_k'], s['lower_k'], len(ROWS))          # row A hottest, row H coolest (assumption)
        if s['kind'] == 'melt':
            return np.full(len(ROWS), s['start_k'] + self.run['melt_i'] * s['melt_increment_k'])
        return np.full(len(ROWS), min(T_BLOCK_RANGE[1], max(T_BLOCK_RANGE[0], s['temperature_k'] + s['increment_k'] * e)))

    def _enter_step(self, k):
        r = self.run
        steps = r['protocol']['steps']
        while k < len(steps) and steps[k]['kind'] == 'goto':
            s = steps[k]
            n = r['loops'].get(k, 0)
            if n < s['cycles']:
                r['loops'][k] = n + 1
                k = s['goto_step'] - 1
            else:
                k += 1
        if k >= len(steps):
            r['k'] = len(steps)
            self._finish_run('completed', None)
            return
        r['k'] = k
        s = steps[k]
        e = self._cycle_of(steps, r['loops'], k)
        r['phase'] = 'ramp'
        r['ramp_started_s'] = self.t
        r['target'] = self._row_targets(s, e)
        self.block_set = r['target']
        self.lid_set = self._lid_target(s, r['target'])
        self.ramp_cap = self._segment_cap(s, r['melt_i'])
        hold = s.get('hold_s')
        r['hold_left'] = None if hold is None else max(1.0, hold + s.get('extend_s', 0.0) * e)

    def _lid_target(self, s, target):
        """Guide p.102: during an infinite hold below 30.0 C the lid heater maintains 31.0 C; otherwise the protocol lid temperature."""
        if s is not None and s['kind'] == 'temperature' and s.get('hold_s') is None and float(np.max(target)) < 303.15:
            return LID_IDLE_K
        return self.run['protocol']['lid_temperature_k']

    def _reenter_step(self):
        """After a power failure: ramp again to the interrupted step's target; the remaining hold time and the melt position are kept."""
        r = self.run
        s = r['protocol']['steps'][r['k']]
        r['target'] = self._row_targets(s, self._cycle_of(r['protocol']['steps'], r['loops'], r['k']))
        self.block_set = r['target']
        self.lid_set = self._lid_target(s, r['target'])
        self.ramp_cap = self._segment_cap(s, r['melt_i'])
        r['phase'], r['ramp_started_s'] = 'ramp', self.t

    def _next_step(self):
        r = self.run
        s = r['protocol']['steps'][r['k']]
        if s['kind'] == 'melt':
            if s['start_k'] + (r['melt_i'] + 1) * s['melt_increment_k'] <= s['end_k'] + 1e-9:
                r['melt_i'] += 1
                self._enter_step(r['k'])
                return
            r['melt_i'] = 0
        self._enter_step(r['k'] + 1)

    def _arrived(self):
        r = self.run
        tgt = r['target']
        if self.run['protocol']['sample_volume_m3'] > 0:                       # calculated mode: modelled sample of the programmed volume
            return bool(np.all(np.abs(r['calc_T'] - tgt) < 0.3))
        return bool(np.all(np.abs(self._sensor_rows() - tgt) < ARRIVE_K))       # block mode

    def _sensor_rows(self):
        return self.T_row + (1.5 if 'block_sensor_drift' in self.faults else 0.0)

    def _engine(self):
        r = self.run
        if r is None or r['state'] not in ('lid_heating', 'running'):
            return None
        if r['state'] == 'lid_heating':
            r['lid_wait_s'] += DT
            if self.lid_T >= self.lid_set - 1.0 or r['lid_wait_s'] > 600.0:
                resume = r.get('resume_state')
                r['resume_state'] = None
                if resume is None:
                    r['state'] = 'running'
                    self._event('run', 'LID_READY', f'热盖到达{self.lid_T - 273.15:.1f} °C，开始第1步', ref=r['run_id'])
                    self._enter_step(0)
                else:
                    r['state'] = resume
                    self._event('run', 'LID_READY', f'热盖到达{self.lid_T - 273.15:.1f} °C，继续第{r["k"] + 1}步', ref=r['run_id'])
                    self._reenter_step()
            return None
        s = r['protocol']['steps'][r['k']]
        if r['phase'] == 'ramp':
            if self._arrived():
                r['phase'] = 'hold'
                if s.get('beep'):
                    self._event('run', 'BEEP', f'第{r["k"] + 1}步到达目标温度', ref=r['run_id'])
            elif self.t - r['ramp_started_s'] > 300.0 and 'BLOCK_TEMPERATURE_TIMEOUT' not in self.errors:
                self._error('BLOCK_TEMPERATURE_TIMEOUT', '样品块5 min内未到达目标温度（风扇或Peltier故障？）')
            return None
        if r['phase'] == 'hold':
            if r['hold_left'] is None:
                return None                                                   # infinite hold: until skip or stop
            r['hold_left'] -= DT
            if r['hold_left'] <= 1e-9:
                if s.get('plate_read'):
                    r['phase'] = 'read'
                    r['read_left'] = SCAN_S[r['scan_mode']]
                else:
                    self._next_step()
            return None
        r['read_left'] -= DT
        if r['read_left'] <= 1e-9:
            return 'read'
        return None

    # ---------------------------------------------------------------- physics tick
    def _physics_tick(self):
        h = DT
        room = self._room()
        if self.power and not self._mains_ok():
            self._power_off('mains')
        if self.lid_state() == 'closing' and 'lid_obstructed' in self.faults and self._lid_pos() < 0.5 * LID_TRAVEL:
            self._set_lid(self._lid_pos())
            self.lid['stopped'] = True
            self._error('LID_OBSTRUCTED', '盖子关闭时遇阻：已停止（清除障碍后重新开/关盖）')
        do_read = self._engine() if self.power else None
        # ---- block rows (Peltier servo, slew limited) or passive relaxation
        T0 = self.T_row.copy()
        if self.power and self.block_set is not None:
            cool_max = 1.2 if 'fan_failure' in self.faults else RAMP_MAX
            sensed_err = self.block_set - self._sensor_rows()
            rate = np.clip(RAMP_GAIN * sensed_err, -min(cool_max, self.ramp_cap), min(RAMP_MAX, self.ramp_cap))
            self.T_row = self.T_row + rate * h
        else:
            self.T_row = room + (self.T_row - room) * math.exp(-h / 600.0)
        dT = self.T_row - T0
        self._peltier_w = float(np.mean(np.abs(dT)) / h * 80.0 + 1.5 * np.mean(np.abs(self.T_row - room))) if self.power and self.block_set is not None else 0.0
        if self.power and self.run is not None and self.run['state'] in ('lid_heating', 'running', 'paused') and self.run.get('calc_T') is not None:
            tau_c = CHEM['tau_sample_base_s'] + CHEM['tau_sample_per_volume_s_m3'] * self.run['protocol']['sample_volume_m3']
            sens = self._sensor_rows()                                          # the firmware model only knows the block sensor
            self.run['calc_T'] = sens + (self.run['calc_T'] - sens) * math.exp(-h / tau_c)
        # ---- heated lid
        if self.power and self.lid_set is not None:
            self.lid_T += float(np.clip((self.lid_set - self.lid_T) / 20.0, -1.0, 1.5)) * h
            lid_w = max(0.0, 40.0 + 0.6 * (self.lid_T - room)) if self.lid_set > self.lid_T - 2 else 0.0
        else:
            self.lid_T = room + (self.lid_T - room) * math.exp(-h / 300.0)
            lid_w = 0.0
        # ---- wells: block offsets (accuracy, uniformity scale with the distance from the room), sample lag, chemistry
        rows = np.repeat(self.T_row, COLS)
        scale = (rows - room) / (363.15 - room)
        wb = rows + (self._acc + self._unif) * scale
        tau = np.where(self.occ, CHEM['tau_sample_base_s'] + CHEM['tau_sample_per_volume_s_m3'] * self.vol, 0.5)
        self.Ts = wb + (self.Ts - wb) * np.exp(-h / tau)
        if self.loaded is not None and self.occ.any():
            self._chemistry(h)
        # ---- ledger
        L = self.led
        L['peltier_heat_pumped_j'].add(self._peltier_w * h)
        L['lid_heater_energy_j'].add(lid_w * h)
        L['electrical_energy_j'].add(((60.0 + self._peltier_w * 1.6 + lid_w) if self.power else 0.0) * h)
        if do_read == 'read':
            self._do_read()
            self._next_step()

    def _chemistry(self, h):
        occ = self.occ
        Ts = self.Ts
        act_tau = CHEM['act_tau_s'] * np.exp(0.25 * np.clip(CHEM['act_t_k'] - Ts, 0.0, 60.0))
        self.act = np.where(occ & (Ts > 353.15), self.act + (1.0 - self.act) * (1.0 - np.exp(-h / act_tau)), self.act)
        act2 = self.active & occ[:, None]
        tm_hot = np.where(act2, self.tm_amp, np.inf).min(axis=1)
        hot_thr = np.maximum(348.15, np.where(np.isfinite(tm_hot), tm_hot - 2.0, 368.15))
        cool_thr = np.where(act2, self.tm_p, -np.inf).max(axis=1) + 10.0
        cool_thr = np.where(np.isfinite(cool_thr), cool_thr, 343.15)
        melt = melt_fraction(Ts[:, None], self.tm_amp)
        going_hot = occ & (self.phase != 1) & (Ts >= hot_thr)
        closing = going_hot & (self.phase == 2)
        if closing.any():
            n0 = np.where(self.N_start > 0, self.N_start, 1.0)
            self.last_e = np.where(closing[:, None] & act2, (self.N - self.N_start) / n0, self.last_e)
            self.cycles_done = self.cycles_done + closing
            sybr = closing & (self.chem_kind != CHEMISTRIES.index('probe')) & act2[:, 0]
            if sybr.any():
                pr = np.clip(self.P[:, 0] / np.maximum(self.p0[:, 0], 1.0), 0.0, 1.0)
                pd_new = np.where(sybr, (self.pd_seed * pr ** 2 + self.pd * CHEM['pd_efficiency'] * self.act * pr * self.inh)
                                  * (self.tmin < self.tm_p[:, 0] + 5.0), 0.0)
                pd_new = np.minimum(pd_new, np.maximum(self.P[:, 0], 0.0))
                self.pd = self.pd + pd_new
                self.P[:, 0] = self.P[:, 0] - pd_new
                self.led['primer_dimer_copies_made'].add(float(pd_new.sum()))
                self.led['primer_consumed_mol'].add(float(2 * pd_new.sum() / AVOGADRO))
        if going_hot.any():
            self.phase = np.where(going_hot, 1, self.phase)
            self.theta = np.where(going_hot[:, None], melt, self.theta)
            self.ext = np.where(going_hot, 0.0, self.ext)
            self.tmin = np.where(going_hot, np.inf, self.tmin)
        hot = occ & (self.phase == 1)
        self.theta = np.where(hot[:, None], np.maximum(self.theta, melt), self.theta)
        going_cool = hot & (Ts < cool_thr)
        if going_cool.any():
            self.phase = np.where(going_cool, 2, self.phase)
            self.N_start = np.where(going_cool[:, None], self.N, self.N_start)
            self._P_start = np.where(going_cool[:, None], self.P, getattr(self, '_P_start', self.P))
            self.ext = np.where(going_cool, 0.0, self.ext)
            self.tmin = np.where(going_cool, Ts, self.tmin)
        cool = occ & (self.phase == 2)
        if not cool.any():
            return
        self.ext = np.where(cool, self.ext + pol_speed(Ts) * h, self.ext)
        self.tmin = np.where(cool, np.minimum(self.tmin, Ts), self.tmin)
        f_a = anneal_factor(self.tmin[:, None], self.tm_p)
        f_e = 1.0 - np.exp(-self.ext[:, None] / self.L)
        reserve = np.clip(self._P_start / np.maximum(self.p0, 1.0), 0.0, 1.0)
        e_cyc = self.e_max * self.theta * f_a * self.act[:, None] * self.inh[:, None] * reserve
        want = self.N_start * e_cyc * f_e
        cap_w = CHEM['pol_capacity_per_m3'] * self.vol * self.act
        tot = np.where(act2, want, 0.0).sum(axis=1)
        k = np.minimum(1.0, cap_w * f_e.max(axis=1) / np.maximum(tot, 1.0))
        want = want * k[:, None]
        new = np.where(cool[:, None] & act2, np.maximum(0.0, want - (self.N - self.N_start)), 0.0)
        new = np.minimum(new, np.maximum(self.P, 0.0))
        if new.any():
            self.N = self.N + new
            self.P = self.P - new
            probe = (self.chem_kind == CHEMISTRIES.index('probe'))[:, None] & (self.probe0 > 0)
            cut = np.where(probe, new * CHEM['probe_bind'] * np.clip(1.0 - self.cleaved / np.maximum(self.probe0, 1.0), 0.0, 1.0), 0.0)
            self.cleaved = self.cleaved + cut
            L = self.led
            s_new = float(new.sum())
            L['amplicon_copies_made'].add(s_new)
            L['primer_consumed_mol'].add(2.0 * s_new / AVOGADRO)
            L['dntp_consumed_mol'].add(float((2.0 * new * self.L).sum()) / AVOGADRO)
            L['probe_cleaved_mol'].add(float(cut.sum()) / AVOGADRO)
        seal = self.plates[self.loaded]['seal']
        if seal == 'none':                                                      # a film / heat seal holds the water also with the lid open
            loss = CHEM['evaporation_m3_per_s_k'] * np.maximum(0.0, Ts - 313.15) * h * occ
            loss = np.minimum(loss, 0.5 * self.vol)
            self.vol = self.vol - loss
            self.led['water_evaporated_m3'].add(float(loss.sum()))

    # ---------------------------------------------------------------- optics
    def _fluorescence(self):
        """Raw RFU of the 96 wells in the six channels at this instant (before noise)."""
        opt = optics()
        fl = fluorophores()
        out = np.zeros((NW, len(CHANNELS)))
        if self.loaded is None:
            return out
        plate = self.plates[self.loaded]
        pf = opt['plate_factor'][PLATE_TYPES[plate['plate_type']]['factor_key']]
        k_det = opt['rfu_per_nm_fam_ch1_20ul']
        resp = {d: np.array([fl[d]['channel_response'].get(c, 0.0) for c in CHANNELS]) * dye_brightness(fl[d]) for d in self.dye_ids}
        geo = np.sqrt(np.maximum(self.vol, 1e-12) / 20e-9)                     # more liquid in the light path (engineering)
        Ts = self.Ts
        for i in np.nonzero(self.occ)[0]:
            kind = CHEMISTRIES[self.chem_kind[i]]
            V = self.vol[i]
            if kind == 'probe':
                for j in np.nonzero(self.active[i])[0]:
                    d = self.dye_ids[self.dye[i, j]]
                    copies = self.cleaved[i, j] + CHEM['probe_residual'] * (self.probe0[i, j] - self.cleaved[i, j])
                    nm = copies / AVOGADRO / (V * 1e3) * 1e9
                    out[i] += k_det * nm * resp[d] / resp_fam_ch1() * pf * geo[i]
            else:
                if self.well_dye[i] < 0:
                    continue
                d = self.dye_ids[self.well_dye[i]]
                bp = 0.0
                for j in np.nonzero(self.active[i])[0]:
                    bp += self.N[i, j] * self.L[i, j] * (1.0 - float(melt_fraction(Ts[i], self.tm_amp[i, j])))
                bp += self.pd[i] * CHEM['pd_length_bp'] * (1.0 - float(melt_fraction(Ts[i], CHEM['pd_tm_k'], 2.5)))
                conc = bp / V                                                  # bp per m3
                bsat = CHEM['sybr_bp_sat_per_m3']
                bound = conc / (1.0 + conc / bsat) + CHEM['sybr_free_fraction'] * bsat
                tq = max(0.2, 1.0 - CHEM['sybr_temp_coeff'] * (Ts[i] - 333.15))
                nm_equiv = bound / bsat * opt['sybr_dye_nm_equivalent'] * tq
                out[i] += k_det * nm_equiv * resp[d] / resp_fam_ch1() * pf * geo[i]
        bg = np.array(opt['background_rfu']) * pf
        out[self.occ] += bg
        out[~self.occ] += 0.3 * bg
        return out

    def _do_read(self):
        r = self.run
        raw = self._fluorescence()
        seed = self.plant['noise_seed']
        opt = optics()
        idx = len(r['reads'])
        chans = dict(all_channels=range(6), sybr_fam=[0], fret=[5])[r['scan_mode']]
        noisy = np.full((NW, 6), np.nan)
        for c in chans:
            failed = f'led_failure_channel_{c + 1}' in self.faults
            if failed:
                self._error(f'LED_FAILURE_CHANNEL_{c + 1}', f'通道{c + 1}的LED失效：该通道只读到噪声')
            for i in range(NW):
                sig = 0.0 if failed else raw[i, c]
                sd = math.sqrt(opt['read_noise_rfu'] ** 2 + (opt['relative_noise'] * sig) ** 2)
                noisy[i, c] = sig + sd * _gauss(seed, (self.seq['run'] * 4099 + idx) * 97 + c, i)
        s = r['protocol']['steps'][r['k']]
        kind = 'melt' if s['kind'] == 'melt' else 'amp' if r['k'] == r['amp_step'] else 'read'
        if kind == 'amp':
            r['cycle'] += 1
        r['reads'].append(dict(index=idx, step=r['k'] + 1, kind=kind, cycle=r['cycle'] if kind == 'amp' else None, time_s=self.t,
                               target_k=float(np.mean(r['target'])), rows_k=[float(x) for x in self.T_row],
                               raw=[[None if np.isnan(v) else round(float(v), 3) for v in row] for row in noisy]))
        self.led['plate_reads'].add(1.0)
        self.led['well_reads'].add(float(self.occ.sum()))

    # ---------------------------------------------------------------- clock
    def step(self, dt_s):
        """Advance the host clock: physics on the fixed 0.1 s grid (k * 0.1 s); state between grid points is held, so any split of the same interval
        gives identical results."""
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = t_end
            return self.t
        while (self.tick + 1) * DT <= t_end + 1e-9:
            self.tick += 1
            self.t = self.tick * DT
            self._physics_tick()
        self.t = t_end
        return self.t

    # ---------------------------------------------------------------- state read
    def read(self, visibility='agent'):
        on = self.power
        r = self.run
        running = self._running()
        sens_b = float(np.mean(self._sensor_rows())) if (on and self.T_row is not None) else None
        calc = None
        if on and self.T_row is not None:
            calc = float(np.mean(r['calc_T'])) if (r is not None and running and r['protocol']['sample_volume_m3'] > 0) else sens_b
        done_unack = r is not None and r['state'] == 'completed' and not r['acknowledged']
        err = bool(on and self.errors)
        v = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': on,
            'lid.position_m': self._lid_pos(), 'lid.state': self.lid_state(), 'screen.tilt_rad': self.screen,
            'plate.loaded': self.loaded is not None, 'plate.id': self.loaded,
            'plates.on_bench': sorted(k for k, p in self.plates.items() if p['location'] == 'bench'),
            'protocol.name': self.protocol['name'] if self.protocol else None, 'protocol.steps': len(self.protocol['steps']) if self.protocol else 0,
            'setpoint.block_k': float(np.mean(self.block_set)) if (on and self.block_set is not None) else None,
            'setpoint.lid_k': self.lid_set if on else None,
            'sensor.block_temperature_k': sens_b, 'sensor.sample_temperature_k': calc,
            'sensor.lid_temperature_k': float(self.lid_T) if (on and self.lid_T is not None) else None,
            'display.block_temperature_c': (round((calc - 273.15) * 10) / 10) if calc is not None else None,
            'display.cycle': min(r['cycle'] + 1, r['amp_total']) if (on and running and r['amp_total'] > 0 and r['state'] != 'lid_heating') else None,
            'run.state': r['state'] if r is not None else 'idle', 'run.id': r['run_id'] if r is not None else None,
            'run.step': (r['k'] + 1) if (running and r['state'] != 'lid_heating') else None,
            'run.cycle': min(r['cycle'] + 1, r['amp_total']) if (running and r['amp_total'] > 0 and r['state'] != 'lid_heating') else None,
            'run.reads': len(r['reads']) if r is not None else 0, 'run.remaining_s': self._remaining() if running else None,
            'run.scan_mode': r['scan_mode'] if r is not None else None, 'run.warnings': list(r['warnings']) if r is not None else [],
            'operation.kind': 'run' if running else None, 'operation.progress': self._progress(),
            'operation.started_by_other_agent': False,
            # one colour at a time (guide p.28): red error > green run (flashing when paused) > blue completed until the lid opens
            'lamp.status_green': bool(on and running and not err), 'lamp.status_blue': bool(on and done_unack and not err), 'lamp.status_red': err,
            'status_bar.flashing': bool(on and (err or (r is not None and r['state'] == 'paused') or done_unack)),
            'errors.active': sorted(self.errors), 'runs.count': len(self.runs),
            'truth.block_temperature_k': float(np.mean(self.T_row)) if self.T_row is not None else 298.15,
            'truth.lid_temperature_k': float(self.lid_T) if self.lid_T is not None else 298.15,
            'truth.sample_temperature_mean_k': float(np.mean(self.Ts[self.occ])) if self.occ.any() else None,
            'truth.peltier_power_w': self._peltier_w, 'truth.faults': sorted(self.faults),
        }
        if visibility == 'agent':
            v = {k: x for k, x in v.items() if SLOTS[k]['visibility'] == 'agent'}
        return v

    def _remaining(self):
        """Estimated time to the end of the run (None during the final infinite hold): the current step from the present block / sample
        temperatures, then the memoised step executions; lid re-heating while heating the lid or after a power failure."""
        r = self.run
        if r is None or not self._running():
            return None
        pos, seg, tau_c = self._timeline(r['protocol'], r['scan_mode'])
        if not seg:
            return 0.0
        lid = 0.0
        if r['state'] in ('lid_heating', 'power_failed'):
            lid = self._lid_time(self.lid_T, self.lid_set if (self.lid_set is not None and r['state'] == 'lid_heating') else r['protocol']['lid_temperature_k'])
        if r['state'] == 'lid_heating' and r.get('resume_state') is None and r['phase'] == 'ramp' and r['target'] is None:
            first = self._ramp_time(self.T_row, self.T_row, seg[0][3], seg[0][4], tau_c) + (seg[0][5] or 0.0) + seg[0][6]
            return float(lid + first + sum(x[7] for x in seg[1:]))
        i = pos.get((r['k'], self._cycle_of(r['protocol']['steps'], r['loops'], r['k']), r['melt_i'])) if r['k'] < len(r['protocol']['steps']) else None
        if i is None:
            return 0.0
        k_, e, mi, tgt, cap, hold, read, _ = seg[i]
        if hold is None and r['phase'] == 'hold':
            return None
        calc = r['calc_T'] if tau_c > 0 else self.T_row
        if r['phase'] == 'ramp' or r['state'] in ('power_failed', 'lid_heating'):
            cur = self._ramp_time(self._sensor_rows(), calc, tgt, cap, tau_c) + (r['hold_left'] if r['hold_left'] is not None else 0.0) + read
        elif r['phase'] == 'hold':
            cur = r['hold_left'] + read
        else:
            cur = r['read_left']
        return float(lid + cur + sum(x[7] for x in seg[i + 1:]))

    def _progress(self):
        r = self.run
        if r is None:
            return 0.0
        if not self._running():
            return 1.0 if r['state'] == 'completed' else 0.0
        rem = self._remaining()
        if rem is None:
            return 1.0                                                          # all programmed time done: waiting in the final infinite hold
        el = self.t - r['started_s']
        return float(min(1.0, max(0.0, el / max(el + rem, 1e-9))))

    # ---------------------------------------------------------------- analysis
    def _run_of(self, run_id):
        if run_id is None:
            if self.run is None:
                raise ActionRejected('NO_RUN_DATA')
            return self.run
        if run_id not in self.runs:
            raise ActionRejected('UNKNOWN_RUN')
        return self.runs[run_id]

    def _well_dyes(self, r, w):
        s = r['setup'].get(w)
        if s is None:
            return []
        if s['chemistry'] == 'probe':
            return list(zip(s['reporters'], s['assays']))
        return [('sybr_green' if s['chemistry'] == 'sybr' else 'evagreen', s['assays'][0] if s['assays'] else None)]

    def _unmixed(self, r, reads):
        """Per-well per-dye RFU series (factory dye calibration: least-squares unmixing over the dyes in the well; uncalibrated dyes and single-channel
        scans use the dye's best channel)."""
        fl = fluorophores()
        opt = optics()
        bg = np.array(opt['background_rfu'])
        out = {}
        for w in r['setup']:
            dyes = [(d, a) for d, a in self._well_dyes(r, w) if fl.get(d, {}).get('best_channel')]
            if not dyes:
                continue
            if not reads:                                                       # e.g. an end-point protocol or a run stopped before its first read
                for d, _ in dyes:
                    out[(w, d)] = np.zeros(0)
                continue
            i = WIDX[w]
            raw = np.array([[np.nan if x is None else x for x in rd['raw'][i]] for rd in reads])
            names = [d for d, _ in dyes]
            best = [int(fl[d]['best_channel']) - 1 for d in names]
            M = np.array([[fl[d]['channel_response'].get(c, 0.0) * dye_brightness(fl[d]) for c in CHANNELS] for d in names])
            full = raw.shape[0] > 0 and not np.isnan(raw[:, [0, 1, 2, 3, 4]]).any()
            if full and all(d in CALIBRATED_DYES for d in names) and len(names) > 1:
                cols = sorted(set(best))
                A = M[:, cols].T
                sol = np.linalg.lstsq(A, (raw[:, cols] - bg[cols]).T, rcond=None)[0].T
                for k, d in enumerate(names):
                    out[(w, d)] = sol[:, k] * M[k, best[k]] + bg[best[k]]
            else:
                for k, d in enumerate(names):
                    out[(w, d)] = raw[:, best[k]]
        return out

    @staticmethod
    def _baseline(y, cycles, fixed=None):
        n = len(y)
        if n < 4:
            return np.zeros(n), (1, max(1, n))
        if fixed is not None:
            b0, b1 = max(1, fixed[0]), min(n, fixed[1])
        else:
            head = y[:min(n, 8)]
            noise = max(1.0, float(np.std(np.diff(head[1:]))) if len(head) > 3 else 1.0)
            base0 = float(np.mean(y[1:4]))
            rise = next((k for k in range(3, n) if y[k] - base0 > 12.0 * noise), n)
            b0, b1 = 2, max(4, min(15, rise - 3))
            b1 = min(b1, n)
        idx = np.arange(b0 - 1, b1)
        if len(idx) < 2:
            idx = np.arange(0, min(n, 3))
        x = np.array(cycles, float)[idx]
        c = np.polyfit(x, y[idx], 1)
        if len(idx) >= 4:                                                     # keep a drift only when it is significant (2 standard errors)
            res = y[idx] - np.polyval(c, x)
            se = math.sqrt(float(np.sum(res ** 2)) / (len(idx) - 2) / max(float(np.sum((x - x.mean()) ** 2)), 1e-12))
            if abs(c[0]) < 2.0 * se:
                c = np.array([0.0, float(np.mean(y[idx]))])
        return np.polyval(c, np.array(cycles, float)), (int(b0), int(b1))

    def _amp_table(self, r, baseline=None):
        reads = [rd for rd in r['reads'] if rd['kind'] == 'amp']
        cyc = [rd['cycle'] for rd in reads]
        series = self._unmixed(r, reads)
        rows = {}
        for (w, d), y in series.items():
            y = np.asarray(y, float)
            if np.isnan(y).all():
                continue
            bl, rng = self._baseline(y, cyc, baseline)
            rows[(w, d)] = dict(rfu=y, bs=y - bl, range=rng)
        return cyc, rows

    def _thresholds(self, rows, manual=None):
        th = {}
        dyes = sorted({d for (_, d) in rows})
        for d in dyes:
            if manual is not None:
                th[d] = (float(manual), False)
                continue
            sds = [float(np.std(v['bs'][v['range'][0] - 1:v['range'][1]])) for (w, dd), v in rows.items() if dd == d]
            th[d] = (max(10.0, 10.0 * float(np.mean(sds)) if sds else 10.0), True)
        return th

    @staticmethod
    def _cq(cyc, bs, thr):
        """Fractional cycle where the baseline-subtracted curve, smoothed with a 3-point moving average (digital filter), first crosses the threshold
        and stays above it; log-linear interpolation between the bracketing cycles (exponential growth between reads), linear when a value is <= 0."""
        y = np.asarray(bs, float)
        if len(y) < 3:
            return None
        s = np.convolve(np.pad(y, 1, mode='edge'), np.ones(3) / 3.0, mode='valid')
        for k in range(1, len(s)):
            if s[k] >= thr and s[k - 1] < thr and (k + 1 >= len(s) or s[k + 1] >= thr):
                y0, y1 = s[k - 1], s[k]
                if y0 > 0:
                    f = (math.log(thr) - math.log(y0)) / (math.log(y1) - math.log(y0))
                else:
                    f = (thr - y0) / (y1 - y0)
                return float(cyc[k - 1] + f * (cyc[k] - cyc[k - 1]))
        return None

    def _cq_results(self, r, threshold=None, baseline=None):
        cyc, rows = self._amp_table(r, baseline)
        th = self._thresholds(rows, threshold)
        res = []
        for (w, d), v in sorted(rows.items(), key=lambda kv: (WIDX[kv[0][0]], kv[0][1])):
            tgt = dict(self._well_dyes(r, w)).get(d)
            res.append(dict(well=w, fluorophore=d, target=tgt, sample_type=r['setup'][w]['sample_type'], cq=self._cq(cyc, v['bs'], th[d][0]),
                            end_rfu=float(v['rfu'][-1]) if len(v['rfu']) else 0.0))
        return cyc, rows, th, res

    def _cmd_data_amplification(self, run_id=None, wells=None, fluorophore=None):
        r = self._run_of(run_id)
        cyc, rows = self._amp_table(r)
        curves = []
        for (w, d), v in sorted(rows.items(), key=lambda kv: (WIDX[kv[0][0]], kv[0][1])):
            if (wells and w not in wells) or (fluorophore and d != fluorophore):
                continue
            curves.append(dict(well=w, fluorophore=d, channel=fluorophores()[d]['best_channel'], cycles=list(cyc), rfu=[round(float(x), 3) for x in v['rfu']],
                               baseline_subtracted=[round(float(x), 3) for x in v['bs']]))
        rng = next(iter(rows.values()))['range'] if rows else (1, 1)
        return dict(run_id=r['run_id'], curves=curves, baseline_cycles=list(rng))

    def _cmd_data_melt(self, run_id=None, wells=None):
        r = self._run_of(run_id)
        reads = [rd for rd in r['reads'] if rd['kind'] == 'melt']
        out = []
        if len(reads) >= 3:
            series = self._unmixed(r, reads)
            T = np.array([rd['target_k'] for rd in reads])
            for (w, d), y in sorted(series.items(), key=lambda kv: (WIDX[kv[0][0]], kv[0][1])):
                if wells and w not in wells:
                    continue
                ys = np.convolve(np.pad(y, 1, mode='edge'), np.ones(3) / 3, mode='valid')
                der = -np.gradient(ys, T)
                out.append(dict(well=w, fluorophore=d, temperature_k=[round(float(x), 3) for x in T], rfu=[round(float(x), 3) for x in y],
                                neg_derivative=[round(float(x), 4) for x in der]))
        return dict(run_id=r['run_id'], curves=out)

    def _cmd_analysis_cq(self, run_id=None, threshold_rfu=None, baseline=None):
        r = self._run_of(run_id)
        if baseline is not None and baseline[0] >= baseline[1]:
            raise ActionRejected('INVALID_BASELINE')
        cyc, rows, th, res = self._cq_results(r, threshold_rfu, baseline)
        return dict(run_id=r['run_id'], thresholds=[dict(fluorophore=d, threshold_rfu=round(v[0], 3), auto=v[1]) for d, v in sorted(th.items())], results=res)

    def _cmd_analysis_standard_curve(self, run_id=None, threshold_rfu=None):
        r = self._run_of(run_id)
        cyc, rows, th, res = self._cq_results(r, threshold_rfu)
        curves = []
        keys = sorted({(x['target'], x['fluorophore']) for x in res if x['target']})
        for tgt, d in keys:
            pts = [(r['setup'][x['well']]['quantity_copies'], x['cq']) for x in res if x['target'] == tgt and x['fluorophore'] == d and x['sample_type'] == 'standard'
                   and x['cq'] is not None and (r['setup'][x['well']].get('quantity_copies') or 0) > 0]
            slope = intercept = r2 = eff = None
            if len(pts) >= 3 and len({p[0] for p in pts}) >= 2:
                xs = np.log10([p[0] for p in pts])
                ys = np.array([p[1] for p in pts])
                slope, intercept = (float(v) for v in np.polyfit(xs, ys, 1))
                pred = slope * xs + intercept
                ss = float(np.sum((ys - ys.mean()) ** 2))
                r2 = 1.0 - float(np.sum((ys - pred) ** 2)) / ss if ss > 0 else None
                eff = 10 ** (-1.0 / slope) - 1.0 if slope < 0 else None
            unk = [dict(well=x['well'], cq=x['cq'], quantity_copies=(10 ** ((x['cq'] - intercept) / slope) if (slope and x['cq'] is not None) else None))
                   for x in res if x['target'] == tgt and x['fluorophore'] == d and x['sample_type'] == 'unknown']
            curves.append(dict(target=tgt, fluorophore=d, slope=slope, intercept=intercept, r2=r2, efficiency=eff, points=len(pts), unknowns=unk))
        return dict(run_id=r['run_id'], curves=curves)

    def _cmd_analysis_melt_peaks(self, run_id=None, min_height_fraction=None):
        r = self._run_of(run_id)
        frac = 0.1 if min_height_fraction is None else min_height_fraction
        m = self._cmd_data_melt(r['run_id'])
        out = []
        plate_top = max([max(c['neg_derivative']) for c in m['curves'] if c['neg_derivative']] or [0.0])
        for c in m['curves']:
            T = np.array(c['temperature_k'])
            der = np.array(c['neg_derivative'])
            top = float(der.max()) if len(der) else 0.0
            noise = 1.4826 * float(np.median(np.abs(np.diff(der) - np.median(np.diff(der))))) / math.sqrt(2) if len(der) > 3 else 0.0
            floor = max(frac * top, frac * plate_top, 6.0 * noise)
            pk, hs = [], []
            for k in range(1, len(der) - 1):
                if der[k] >= der[k - 1] and der[k] > der[k + 1] and der[k] >= floor:
                    a, b, cc = der[k - 1], der[k], der[k + 1]
                    den = a - 2 * b + cc
                    off = 0.5 * (a - cc) / den if den != 0 else 0.0
                    pk.append(round(float(T[k] + off * (T[k + 1] - T[k])), 3))
                    hs.append(round(float(b), 4))
            out.append(dict(well=c['well'], fluorophore=c['fluorophore'], peaks_k=pk[:8], heights=hs[:8]))
        return dict(run_id=r['run_id'], peaks=out)

    def _cmd_analysis_gene_expression(self, run_id=None, reference_targets=(), control_group='', use_efficiency=False):
        r = self._run_of(run_id)
        cyc, rows, th, res = self._cq_results(r)
        eff = {}
        if use_efficiency:
            for c in self._cmd_analysis_standard_curve(r['run_id'])['curves']:
                if c['efficiency']:
                    eff[c['target']] = c['efficiency']
        groups = {}
        for x in res:
            g = r['setup'][x['well']].get('biological_group')
            if g is None or x['target'] is None or x['sample_type'] != 'unknown' or x['cq'] is None:
                continue
            groups.setdefault(g, {}).setdefault(x['target'], []).append(x['cq'])
        if control_group not in groups:
            raise ActionRejected('UNKNOWN_GROUP')
        mean = {g: {t: float(np.mean(v)) for t, v in d.items()} for g, d in groups.items()}
        out = []
        for g, d in sorted(mean.items()):
            refs = [d.get(t) for t in reference_targets]
            if any(x is None for x in refs):
                continue
            ref = float(np.mean(refs))
            cref = [mean[control_group].get(t) for t in reference_targets]
            for t, cq in sorted(d.items()):
                if t in reference_targets:
                    continue
                dcq = cq - ref
                ctrl = mean[control_group].get(t)
                ddcq = None if (ctrl is None or any(x is None for x in cref)) else dcq - (ctrl - float(np.mean(cref)))
                base = 1.0 + eff.get(t, 1.0)
                out.append(dict(sample=g, target=t, delta_cq=dcq, delta_delta_cq=ddcq, relative_quantity=(base ** (-ddcq)) if ddcq is not None else None))
        return dict(run_id=r['run_id'], results=out)

    def _cmd_data_export(self, kind, run_id=None):
        r = self._run_of(run_id)
        buf = io.StringIO()
        wr = csv.writer(buf)
        if kind == 'cq_csv':
            res = self._cq_results(r)[3]
            wr.writerow(['Well', 'Fluor', 'Target', 'Content', 'Cq', 'End RFU'])
            for x in res:
                wr.writerow([x['well'], x['fluorophore'], x['target'], x['sample_type'], '' if x['cq'] is None else f'{x["cq"]:.2f}', f'{x["end_rfu"]:.1f}'])
            rows = len(res)
        elif kind == 'amplification_csv':
            cur = self._cmd_data_amplification(r['run_id'])['curves']
            wr.writerow(['Cycle'] + [f'{c["well"]}_{c["fluorophore"]}' for c in cur])
            n = len(cur[0]['cycles']) if cur else 0
            for k in range(n):
                wr.writerow([cur[0]['cycles'][k]] + [c['rfu'][k] for c in cur])
            rows = n
        elif kind == 'melt_csv':
            cur = self._cmd_data_melt(r['run_id'])['curves']
            wr.writerow(['Temperature_K'] + [f'{c["well"]}_{c["fluorophore"]}' for c in cur])
            n = len(cur[0]['temperature_k']) if cur else 0
            for k in range(n):
                wr.writerow([cur[0]['temperature_k'][k]] + [c['rfu'][k] for c in cur])
            rows = n
        else:
            res = self._cq_results(r)[3]
            buf.write('<rdml version="1.2"><experiment id="%s"><run id="%s">' % (r['protocol']['name'], r['run_id']))
            for x in res:
                buf.write('<react id="%s"><data><tar id="%s"/><cq>%s</cq></data></react>' % (x['well'], x['target'], '' if x['cq'] is None else f'{x["cq"]:.3f}'))
            buf.write('</run></experiment></rdml>')
            rows = len(res)
        data = buf.getvalue().encode()
        name = f'{r["run_id"]}_{kind}.' + ('rdml' if kind == 'rdml' else 'csv')
        self._event('data', 'EXPORT', f'导出{name}（{len(data)} B）', ref=r['run_id'])
        return dict(file=name, bytes=len(data), rows=rows)

    def _cmd_runs_list(self):
        return dict(runs=[dict(run_id=r['run_id'], protocol=r['protocol']['name'], plate_id=r['plate_id'], status=r['state'], started_s=r['started_s'],
                               finished_s=r['finished_s'], reads=len(r['reads']), cycles=r['cycle'], scan_mode=r['scan_mode'], warnings=list(r['warnings']))
                          for r in self.runs.values()])

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_provenance_read(self):
        return dict(libraries=[dict(name=k, file=v, entries=len(_lib(k).get('substances', _lib(k).get('channels', {})))) for k, v in LIBS.items()],
                    channels=[dict(channel=c, excitation_m=[v['ex'][0] * 1e-9, v['ex'][1] * 1e-9], detection_m=[v['em'][0] * 1e-9, v['em'][1] * 1e-9], dye=v['dye'],
                                   evidence=v['evidence']) for c, v in optics()['channels'].items()])

    def _cmd_well_inspect(self, well):
        i = WIDX[well]
        out = []
        if self.loaded is not None and self.occ[i]:
            s = self.plates[self.loaded]['wells'][well]
            for j, a in enumerate(s['assays']):
                le = self.last_e[i, j]
                out.append(dict(assay=a, copies=float(self.N[i, j]), primer_remaining=float(self.P[i, j]), probe_cleaved=float(self.cleaved[i, j]),
                                last_efficiency=None if np.isnan(le) else float(le), melt_tm_k=float(self.tm_amp[i, j])))
        return dict(truth=dict(well=well, occupied=bool(self.occ[i]), sample_temperature_k=float(self.Ts[i]), volume_m3=float(self.vol[i]), polymerase_active=float(self.act[i]),
                               cycles_completed=int(self.cycles_done[i]), assays=out, primer_dimer_copies=float(self.pd[i])))

    def ledger(self):
        return {k: v.value() for k, v in self.led.items()}

    def _cmd_ledger_read(self):
        return dict(ledger=self.ledger())

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        if fault == 'power_loss':
            if active and self.power:
                self._power_off('mains')
            elif not active and self.switch_on and not self.power and self._mains_ok():
                self._power_up()
        if not active:
            code = {'lid_obstructed': 'LID_OBSTRUCTED', 'fan_failure': 'BLOCK_TEMPERATURE_TIMEOUT'}.get(fault, fault.upper() if fault.startswith('led') else None)
            if code in self.errors:
                del self.errors[code]                                           # the cause is repaired: the error and the red strip clear
        self._event('fault', 'FAULT', f'{fault} {"on" if active else "off"}')
        return dict(faults=sorted(self.faults))

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
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        if CAPS[capability_id]['kind'] not in ('query',) and capability_id != 'sim.step':
            self.revision += 1
        return out


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
ASYNC = {'run.start'}
_OWNED_PLATE = {'plate.load'}
_RUN_QUERIES = {'data.amplification', 'data.melt', 'analysis.cq', 'analysis.standard_curve', 'analysis.melt_peaks', 'analysis.gene_expression', 'data.export'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] == 'device_command' and k not in UNSUPPORTED and k != 'data.export'}


class BioradCfxOpusSession:
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
        meta = copy.deepcopy(export_biorad_cfxopus_contract_metadata())
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

    def _foreign_run(self):
        h = self._host
        return h.run is not None and h._running() and self._foreign(h.run['op_id'])

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        values['operation.started_by_other_agent'] = self._role == 'agent' and self._foreign_run()
        if self._role == 'agent':
            values['plates.on_bench'] = [x for x in values['plates.on_bench'] if not self._foreign(x)]
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        h = self._host
        if capability_id in _CONTROL_GUARDED and self._foreign_run():
            if not h.power and ((capability_id == 'power.set' and arguments.get('enabled') is True)
                                or (capability_id == 'power.press_switch' and not h.switch_on)):
                return                                                          # switching ON an unpowered instrument (not OFF during an outage)
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _OWNED_PLATE and self._foreign(arguments.get('plate_id')):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'plate.remove' and h.loaded is not None and self._foreign(h.loaded):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _RUN_QUERIES:
            rid = arguments.get('run_id') or (h.run['run_id'] if h.run is not None else None)
            if rid is not None and self._foreign(rid):
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
            if self._role == 'agent':
                if capability_id == 'runs.list':
                    result = dict(runs=[r for r in result['runs'] if not self._foreign(r['run_id'])])
                elif capability_id == 'events.read':
                    result = dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in h.events
                                          if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_ref')) and e['kind'] != 'fault'], dropped=h.events_dropped)
            operation = result.get('operation_id') if capability_id in ASYNC else None
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=operation,
                            status='accepted' if operation else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                            result=copy.deepcopy(result), observations={}, error=None)
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
        h = self._host
        if not isinstance(operation_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        op = h.operations.get(operation_id)
        if op is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        r = h.runs[op['record_id']]
        return dict(operation_id=operation_id, status=op['phase'],
                    result=dict(run_id=r['run_id'], state=r['state'], reads=len(r['reads']), cycles=r['cycle'], error=r['error']))

    def cancel(self, operation_id, request_id):
        h = self._host
        if not isinstance(request_id, str) or not request_id:
            raise ActionRejected('INVALID_ARGUMENT')
        key = (self._principal, request_id)
        digest = json.dumps(['cancel', operation_id]) if isinstance(operation_id, str) else None
        if key in h._requests:
            old, response = h._requests[key]
            if digest is None or old != digest:
                raise ActionRejected('REQUEST_CONFLICT')
            if isinstance(response, str):
                raise ActionRejected(response)
            return copy.deepcopy(response)
        try:
            self.poll(operation_id)
            op = h.operations[operation_id]
            if op['phase'] != 'running':
                raise ActionRejected('NOT_RUNNING')
            h._cancel_op(op)
        except ActionRejected as exc:
            if digest is not None:
                h._requests[key] = (digest, str(exc))
            raise
        h.revision += 1
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='succeeded',
                        state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=dict(phase=op['phase']), observations={}, error=None)
        h._requests[key] = (digest, copy.deepcopy(response))
        return response


# ---------------------------------------------------------------- contract metadata
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值、另一个agent的板、运行或数据）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'MAINS_OUT_OF_RANGE': '市电不在100–240 VAC ±10 %（或断电）',
    'POWER_OFF': '仪器未通电', 'RUN_IN_PROGRESS': '运行进行中（含热盖升温与暂停）', 'LID_NOT_OPEN': '盖子没有完全退回', 'LID_NOT_CLOSED': '盖子没有关好',
    'ITEM_EXISTS': 'ID已存在', 'TOO_MANY_ITEMS': '实验台上的板太多（≤20）', 'UNKNOWN_ASSAY': '检测（引物对）不在assay库中', 'UNKNOWN_SAMPLE': '样品不存在',
    'DUPLICATE_WELL': '孔位或检测重复', 'INVALID_REPORTERS': '探针法每个检测要一个不同的报告染料；SYBR/EvaGreen每孔只能一个检测且不写报告染料',
    'VOLUME_OUT_OF_RANGE': '模板体积大于反应体积或反应体积超过孔容量', 'NTC_WITH_TEMPLATE': '无模板对照不能加样品', 'STANDARD_QUANTITY_REQUIRED': '标准品孔要给拷贝数',
    'UNKNOWN_ITEM': '板不存在', 'BLOCK_OCCUPIED': '样品块上已有板', 'PLATE_NOT_ON_BENCH': '板不在实验台上', 'NO_PLATE': '样品块上没有板',
    'INVALID_STEP': '步骤字段不完整、不适用于该步骤类型，或无限保持不在最后一步', 'INVALID_GRADIENT': '梯度超范围（下限30–99 °C、上限≤100 °C、跨度1–24 °C）',
    'INVALID_GOTO': 'GOTO目标须为前面的温度/梯度步骤、不能嵌套，循环内不能有无限保持', 'INVALID_MELT': '熔解步骤的起止温度或增量无效',
    'NO_PROTOCOL': '没有载入协议', 'RUNS_FULL': '运行记录已满（50）', 'NOT_RUNNING': '没有进行中的运行', 'NOT_PAUSED': '运行没有暂停',
    'NO_RUN_DATA': '没有运行数据', 'UNKNOWN_RUN': '运行ID不存在', 'INVALID_BASELINE': '基线起止循环无效', 'UNKNOWN_GROUP': '对照组没有有效Cq',
    'UNKNOWN_OPERATION': '操作不存在',
    'LID_OBSTRUCTED': '盖子关闭时遇阻已停止', 'BLOCK_TEMPERATURE_TIMEOUT': '样品块5 min内未到达目标温度',
    'INVALID_SAMPLE_VOLUME': '样品体积须为0（块模式）或1–50 µl',
    **{f'LED_FAILURE_CHANNEL_{c}': f'通道{c}的LED失效' for c in range(1, 6)},
}
_OPS = ['operation.kind', 'operation.progress']
_RUN = ['run.state', 'run.id', 'run.step', 'run.cycle', 'run.reads', 'run.remaining_s', 'run.scan_mode', 'run.warnings', 'display.cycle', 'runs.count'] + _OPS
_LAMPS = ['lamp.status_green', 'lamp.status_blue', 'lamp.status_red', 'status_bar.flashing']
_THERMAL = ['sensor.block_temperature_k', 'sensor.sample_temperature_k', 'sensor.lid_temperature_k', 'display.block_temperature_c', 'truth.block_temperature_k',
            'truth.lid_temperature_k', 'truth.sample_temperature_mean_k', 'truth.peltier_power_w', 'setpoint.block_k', 'setpoint.lid_k']
_LID = ['lid.position_m', 'lid.state']
_PWR = ['power.enabled', 'sensor.block_temperature_k', 'sensor.sample_temperature_k', 'sensor.lid_temperature_k', 'display.block_temperature_c', 'display.cycle',
        'setpoint.block_k', 'setpoint.lid_k', 'lid.state'] + _LAMPS + _RUN + ['errors.active']
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.block_temperature_k', 'truth.lid_temperature_k', 'truth.sample_temperature_mean_k'] + _PWR,
    'power.set': _PWR, 'power.press_switch': _PWR,
    'lid.open': ['lid.state', 'errors.active'] + _LAMPS, 'lid.close': ['lid.state', 'errors.active'] + _LAMPS, 'screen.tilt': ['screen.tilt_rad'], 'sample.provide': [],
    'plate.prepare': ['plates.on_bench'], 'plate.load': ['plate.loaded', 'plate.id', 'plates.on_bench', 'truth.sample_temperature_mean_k'],
    'plate.remove': ['plate.loaded', 'plate.id', 'plates.on_bench', 'truth.sample_temperature_mean_k'], 'protocol.set': ['protocol.name', 'protocol.steps'],
    'run.start': _RUN + _LAMPS + ['setpoint.lid_k', 'sensor.sample_temperature_k', 'display.block_temperature_c', 'errors.active'], 'run.pause': ['run.state', 'status_bar.flashing'],
    'run.resume': ['run.state', 'status_bar.flashing'],
    'run.skip_step': _RUN + _LAMPS + ['setpoint.block_k', 'setpoint.lid_k', 'sensor.sample_temperature_k', 'display.block_temperature_c'],
    'fault.inject': ['truth.faults', 'errors.active'] + _PWR, 'sim.step': ['clock.time_s'],
}
_PHYS = _THERMAL + _LID + _RUN + _LAMPS + ['errors.active']
_PHYS_RUN = _THERMAL + _RUN + _LAMPS + ['errors.active']                       # a run never moves the lid (lid commands are refused while it runs)
ON_STEP = {'plant.configure': _PHYS, 'power.set': _PHYS, 'power.press_switch': _PHYS, 'lid.open': _LID + _LAMPS, 'lid.close': _LID + _LAMPS + ['errors.active'],
           'plate.load': ['truth.sample_temperature_mean_k'], 'run.start': _PHYS_RUN, 'run.pause': _PHYS_RUN, 'run.resume': _PHYS_RUN, 'run.skip_step': _PHYS_RUN,
           'fault.inject': _PHYS, 'sim.step': _PHYS}
ON_STEP_ZH = {'plant.configure': '室温决定样品块与热盖的被动温度', 'power.set': '通电后待机；断电时块与热盖回到室温、运行中止',
              'power.press_switch': '同power.set', 'lid.open': '电动盖约6 s退回', 'lid.close': '电动盖约6 s关闭（遇阻停止）', 'plate.load': '板内样品温度随样品块变化',
              'run.start': '热盖升温，然后按协议控温、读板，循环、熔解（4 °C等低于30 °C的无限保持时热盖保持31 °C），结束后样品块回到室温；运行中断电则暂停，来电后自动继续',
              'run.pause': '保持当前温度，计时停止',
              'run.resume': '继续升降温与计时', 'run.skip_step': '执行下一步', 'fault.inject': '故障改变控温、读板或盖子运动', 'sim.step': '0.1 s网格积分'}
PRECONDITIONS = {
    'power.set': ('已声明实验室条件；开机时市电在100–240 VAC ±10 %', ['plant.configured']), 'power.press_switch': ('同power.set', ['plant.configured']),
    'lid.open': ('通电；不在运行中（热盖升温、运行、暂停）', ['power.enabled', 'run.state']), 'lid.close': ('通电；不在运行中', ['power.enabled', 'run.state']),
    'screen.tilt': ('已声明实验室条件；角度12–55°', ['plant.configured']),
    'sample.provide': ('已声明实验室条件；ID唯一；检测在assay库中', ['plant.configured']),
    'plate.prepare': ('已声明实验室条件；板ID唯一、台上≤20块；孔位与检测不重复；探针法每检测一个不同的报告染料，SYBR/EvaGreen每孔一个检测；模板体积≤反应体积≤孔容量；'
                      '无模板对照不加样品；标准品给拷贝数', ['plates.on_bench']),
    'plate.load': ('盖子完全退回；板在实验台上；样品块空', ['lid.state', 'plate.loaded', 'plates.on_bench']),
    'plate.remove': ('盖子完全退回；不在运行中；样品块上有板', ['lid.state', 'run.state', 'plate.loaded']),
    'protocol.set': ('通电；不在运行中；步骤合法（温度4–100 °C、保持1 s–17:59:59或最后一步无限、升降温0.1–5 °C/s、增量±10 °C、延时±60 s、梯度30–100 °C跨度1–24 °C、'
                     'GOTO指向前面的温度/梯度步骤、熔解起止有效）', ['power.enabled', 'run.state']),
    'run.start': ('通电；不在运行中；已载入协议；盖子关好；运行记录未满', ['power.enabled', 'run.state', 'protocol.name', 'lid.state', 'runs.count']),
    'run.pause': ('通电；运行中（不含热盖升温）', ['power.enabled', 'run.state']), 'run.resume': ('通电；已暂停', ['power.enabled', 'run.state']),
    'run.skip_step': ('通电；运行中或暂停', ['power.enabled', 'run.state']),
    'data.amplification': ('有运行数据（给run_id或当前/最近一次运行）', ['run.id']), 'data.melt': ('同上', ['run.id']), 'analysis.cq': ('同上；基线起<止', ['run.id']),
    'analysis.standard_curve': ('同上', ['run.id']), 'analysis.melt_peaks': ('同上', ['run.id']), 'analysis.gene_expression': ('同上；对照组有Cq', ['run.id']),
    'data.export': ('同上', ['run.id']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_PW = ['PLANT_REQUIRED', 'POWER_OFF']
_DATA = ['NO_RUN_DATA', 'UNKNOWN_RUN']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'lid.open': _PW + ['RUN_IN_PROGRESS'], 'lid.close': _PW + ['RUN_IN_PROGRESS'], 'screen.tilt': ['PLANT_REQUIRED'],
    'sample.provide': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'UNKNOWN_ASSAY'],
    'plate.prepare': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'DUPLICATE_WELL', 'UNKNOWN_ASSAY', 'INVALID_REPORTERS', 'VOLUME_OUT_OF_RANGE', 'UNKNOWN_SAMPLE',
                      'NTC_WITH_TEMPLATE', 'STANDARD_QUANTITY_REQUIRED'],
    'plate.load': ['PLANT_REQUIRED', 'LID_NOT_OPEN', 'UNKNOWN_ITEM', 'BLOCK_OCCUPIED', 'PLATE_NOT_ON_BENCH'],
    'plate.remove': ['PLANT_REQUIRED', 'LID_NOT_OPEN', 'RUN_IN_PROGRESS', 'NO_PLATE'],
    'protocol.set': _PW + ['RUN_IN_PROGRESS', 'INVALID_SAMPLE_VOLUME', 'INVALID_STEP', 'INVALID_GRADIENT', 'INVALID_GOTO', 'INVALID_MELT'],
    'run.start': _PW + ['RUN_IN_PROGRESS', 'NO_PROTOCOL', 'LID_NOT_CLOSED', 'RUNS_FULL'], 'run.pause': _PW + ['NOT_RUNNING'], 'run.resume': _PW + ['NOT_PAUSED'],
    'run.skip_step': _PW + ['NOT_RUNNING'], 'data.amplification': _DATA, 'data.melt': _DATA, 'analysis.cq': _DATA + ['INVALID_BASELINE'],
    'analysis.standard_curve': _DATA, 'analysis.melt_peaks': _DATA, 'analysis.gene_expression': _DATA + ['UNKNOWN_GROUP'], 'data.export': _DATA,
    'runs.list': [], 'events.read': [], 'provenance.read': [], 'well.inspect': [], 'ledger.read': [], 'fault.inject': ['PLANT_REQUIRED'], 'sim.step': [],
}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件', 'screen.tilt': '屏幕倾角经宿主镜像到屏幕关节', 'sample.provide': '样品没有场景几何', 'plate.prepare': '板没有场景几何（样品块上表面为接口面）',
    'plate.load': '同左', 'plate.remove': '同左', 'protocol.set': '协议只在过程层', 'run.pause': '只在过程层', 'run.resume': '只在过程层',
    'data.amplification': '只读', 'data.melt': '只读', 'analysis.cq': '只读', 'analysis.standard_curve': '只读', 'analysis.melt_peaks': '只读', 'analysis.gene_expression': '只读',
    'data.export': '数据操作', 'runs.list': '只读', 'events.read': '只读', 'provenance.read': '只读', 'well.inspect': '只读真值（evaluator）', 'ledger.read': '只读账目（evaluator）',
    'fault.inject': '评估者故障注入', 'sim.step': '推进时钟'}
SCENE_MIRRORED = {'lid.position_m', 'screen.tilt_rad', 'display.block_temperature_c', 'display.cycle', 'lamp.status_green', 'lamp.status_blue', 'lamp.status_red',
                  'power.enabled'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节、屏上仿真数字或状态灯带（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {'run.start': ('协议最后一步结束（或无限保持被跳过）后run.state为completed（操作succeeded）；运行中市电中断时run.state为power_failed（操作仍running），'
                           '来电后热盖重新升温并自动继续（run.warnings记POWER_FAILURE，厂家）；用电源开关关机时运行停止（stopped，操作cancelled，假设）',
                           '取消（cancel）：立即停止，run.state为stopped（操作cancelled），已采集的读板数据保留')}
RESOURCES = {'power.set': ['mains'], 'power.press_switch': ['mains'], 'lid.open': ['lid_motor', 'host_clock'], 'lid.close': ['lid_motor', 'host_clock'], 'data.export': ['usb'],
             'screen.tilt': ['touch_screen'], 'plate.prepare': ['bench'], 'plate.load': ['sample_block'], 'plate.remove': ['sample_block'],
             'run.start': ['sample_block', 'heated_lid', 'optics_shuttle', 'host_clock'], 'plant.configure': ['room', 'mains'],
             'fault.inject': ['evaluator'], 'sim.step': ['host_clock']}
OBSERVES = {'power.set': ['power.enabled', 'display.block_temperature_c'], 'power.press_switch': ['power.enabled'], 'lid.open': ['lid.state', 'lid.position_m'],
            'lid.close': ['lid.state', 'lid.position_m'], 'screen.tilt': ['screen.tilt_rad'], 'plate.prepare': ['plates.on_bench'], 'plate.load': ['plate.loaded', 'plate.id'],
            'plate.remove': ['plate.loaded'], 'protocol.set': ['protocol.name', 'protocol.steps'], 'run.start': ['run.state', 'run.step', 'run.cycle', 'run.remaining_s', 'display.cycle'],
            'run.pause': ['run.state'], 'run.resume': ['run.state'], 'run.skip_step': ['run.step']}
_WB, _BR, _B79, _IG, _FP, _PB, _NN, _SIM = ('user_workbook', 'manufacturer_primary', 'manufacturer_family_brochure', 'manufacturer_instrument_guide', 'fpbase', 'primerbank',
                                            'biopython_meltingtemp', 'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({k: [_IG] for k in ('power.set', 'power.press_switch', 'lid.open', 'lid.close', 'plate.load', 'plate.remove', 'run.pause', 'run.resume', 'run.skip_step',
                                        'runs.list', 'events.read', 'data.export')})
CAP_SOURCES.update({'screen.tilt': [_BR], 'protocol.set': [_IG, _BR, _WB], 'run.start': [_IG, _BR, _B79, _WB], 'plate.prepare': [_PB, _FP, _SIM],
                    'sample.provide': [_PB, _SIM], 'data.amplification': [_B79, _FP, _SIM], 'data.melt': [_NN, _SIM], 'analysis.cq': [_BR, _SIM],
                    'analysis.standard_curve': [_BR, _B79, _SIM], 'analysis.melt_peaks': [_BR, _NN, _SIM], 'analysis.gene_expression': [_BR, _SIM],
                    'provenance.read': [_FP, _PB, _NN, _B79], 'well.inspect': [_SIM, _PB, _NN]})
CAP_SOURCES.update({k: [_BR, _IG] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温', 'mains_voltage_v': '供电电压', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.qpcr_process.limitations。'
CAP_LIMITS = {
    'protocol.set': ['步骤范围按仪器指南表10，温度按0.1 °C、保持与延时按1 s取整；GOTO默认39次（厂家），上限99次与协议最多40步为本模型假设；嵌套GOTO不支持（多重非嵌套循环支持）；'
                     '梯度按A行最高、H行最低线性分配（行序为假设）；无限保持只允许在最后一步；热盖温度默认105 °C（厂家）。'],
    'run.start': ['样品块为一阶伺服（最高5 °C/s，比例逼近按平均3.3 °C/s标定）；准确度±0.2 °C与均匀性±0.3 °C用确定性的偏差图案（随与室温差缩放）；样品温度按体积一阶滞后'
                  '（20 µl约4 s，工程值）；热盖一阶升温，低于30 °C的无限保持时保持31 °C（厂家）；计算模式按协议体积判断到温，块模式按块温；运行中市电中断时运行挂起，'
                  '来电后热盖重新升温并从中断的步骤继续（剩余保持时间保留，中断的读板重做）；预计时长按控温路径在0.1 s网格上回放（不含化学）。',
                  '扩增是每孔的循环事件模型（不是连续分子动力学）：再次加热时结束一个合成循环；引物二聚体、酶活化、抑制物为工程模型。'],
    'plate.prepare': ['检测为PrimerBank的SYBR引物对（序列与扩增子为真实数据）；探针法时探针序列不建模，报告染料按FPbase光谱；每孔模板拷贝按泊松抽样并含1 %移液误差（工程值）。'],
    'data.amplification': ['通道信号=染料量×亮度×通道响应（FPbase光谱×厂家波段）×板型系数×检测器标度；检测器标度按产品册7279 p.6的探针例子（1e5拷贝Cq 19.81）标定，'
                           'SYBR饱和亮度与噪声为工程值；出厂染料校准做颜色补偿（假设常用染料都已校准；FPbase没有光谱的染料（如VIC）不能作报告染料）。'],
    'run.skip_step': ['跳步（指南p.122）：无限保持时退出保持；GOTO循环内跳到下一个循环（本循环余下步骤与读板跳过），最后一个循环时退出循环；熔解曲线整步跳过。'],
    'analysis.cq': ['基线（自动：上升前的循环，漂移不显著时取常数）、自动阈值（基线标准差的10倍，至少10 RFU）、3点平滑后对数线性插值的分数Cq，按CFX Maestro的公开描述'
                    '实现，算法细节为工程实现。'],
    'analysis.melt_peaks': ['反应中扩增子熔解温度=库中最近邻两态Tm（Owczarzy镁离子校正，缓冲为假设）−3 K（工程偏移，±2 K）；熔解峰宽为工程值；真实扩增子可能分段熔解。'],
}
LIMITATIONS = [
    '选定配置：CFX Opus 96（12011319），固定96孔块、5色+FRET；CFX Maestro分析功能以运行层分析能力表示；BR.io云、用户账户、Precision Melt与新染料校准不在配置内。',
    '样品块与样品温度为集总模型（每行一个Peltier温度、孔间偏差为确定性图案、样品一阶滞后）；没有样品块内的温度场。',
    'PCR是每孔每检测的循环事件模型：效率=最大效率×变性完全度×引物退火×延伸完成度×热启动酶活化×抑制物×引物余量，受聚合酶能力限制；最大效率与引物二聚体倾向为工程关系，'
    '没有用某个试剂盒的实测数据标定（检测器标度按产品册探针例子标定）。Cq取决于试剂、样品与分析方法，不能由仪器设置直接决定。',
    '荧光为通道响应模型：染料光谱来自FPbase，滤光片按厂家波段的矩形带通，LED与光电二极管响应平坦（假设）；探针序列与淬灭效率为工程值。',
    'PCR板、样品与反应液没有场景几何；放板、配板、开关盖与调屏为宿主/机器人抽象，没有接触验证；触摸屏为贴图，屏上温度与循环数为仿真UI提示。',
    '状态灯条一次只亮一种颜色（错误红>运行绿>完成蓝，厂家颜色含义）；盖子运动时灯条不变、FRET扫描3 s、用电源开关关机时运行停止（不同于市电中断后的自动继续）为本模型假设；'
    '错误在原因消除（故障解除、重新开关盖、开始新运行）后清除。',
    '没有真实CFX Opus的硬件接口。',
]


def _sampling_text(k):
    if k.startswith(('sensor.', 'truth.', 'display.', 'lid.', 'run.', 'lamp.', 'status_bar', 'setpoint.', 'operation.')) or k in ('clock.time_s', 'errors.active'):
        return 'Integrated on the fixed 0.1 s host grid; between grid points the last grid value is held.'
    return 'Changes when a command is accepted; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _OWNED_PLATE or k in _RUN_QUERIES or k == 'plate.remove' or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    if k in _CONTROL_GUARDED:
        return '；另一个agent的运行进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）'
    if k in _OWNED_PLATE or k == 'plate.remove':
        return '；agent只能操作自己配的板（ACCESS_DENIED）'
    if k in _RUN_QUERIES:
        return '；agent只能读自己启动的运行数据（ACCESS_DENIED）'
    return ''


CHEM_UNITS = dict(primer_mol_m3='mol/m3', probe_mol_m3='mol/m3', dntp_mol_m3='mol/m3', sybr_bp_sat_per_m3='1/m3', sybr_free_fraction='1', sybr_temp_coeff='1/K',
                  probe_residual='1', probe_bind='1', pol_capacity_per_m3='1/m3', act_tau_s='s', act_t_k='K', anneal_width_k='K', anneal_offset_k='K', primer_mg_shift_k='K',
                  low_penalty_per_k='1/K', low_ref_offset_k='K', pol_speed_nt_s='1/s', pol_opt_k='K', pol_width_k='K', melt_width_k='K', melt_tm_offset_k='K', pd_seed_base='1',
                  pd_efficiency='1', pd_tm_k='K', pd_length_bp='1', tau_sample_base_s='s', tau_sample_per_volume_s_m3='s/m3', evaporation_m3_per_s_k='m3/(s K)')
CHEM_ZH = dict(primer_mol_m3='每条引物的终浓度（400 nM）', probe_mol_m3='探针终浓度（250 nM）', dntp_mol_m3='dNTP总浓度（0.8 mM）', sybr_bp_sat_per_m3='SYBR/EvaGreen结合饱和的双链碱基对浓度',
               sybr_free_fraction='游离染料的相对荧光', sybr_temp_coeff='嵌入染料荧光的温度系数', probe_residual='完整探针被淬灭后的残余荧光比例',
               probe_bind='每条新链切割探针的比例', pol_capacity_per_m3='每循环聚合酶最多能合成的拷贝浓度（线性期）', act_tau_s='热启动酶在95 °C的活化时间常数',
               act_t_k='活化参考温度', anneal_width_k='退火S形宽度', anneal_offset_k='退火完全的上限高于（引物Tm+镁离子升高）的温差',
               primer_mg_shift_k='3 mM Mg2+使PrimerBank引物Tm升高的量', low_penalty_per_k='退火温度低于最适每K的效率损失（产品册GAPDH梯度）',
               low_ref_offset_k='最适退火温度高于PrimerBank引物Tm的温差', pol_speed_nt_s='聚合酶最适温度下的延伸速度', pol_opt_k='聚合酶最适温度',
               pol_width_k='聚合酶速度随温度下降的高斯宽度', melt_width_k='熔解峰（−dF/dT）的半高宽', pd_seed_base='引物二聚体每循环新生成的基础量（随引物3′互补性加倍）',
               pd_efficiency='引物二聚体的扩增效率', pd_tm_k='引物二聚体熔解温度', pd_length_bp='引物二聚体长度',
               melt_tm_offset_k='反应中扩增子实际熔解温度相对库中两态最近邻Tm的偏移（长扩增子分段熔解、预混液添加剂）',
               tau_sample_base_s='样品温度滞后时间常数的常数项', tau_sample_per_volume_s_m3='样品温度滞后时间常数随样品体积的增量（20 µl约+3 s）',
               evaporation_m3_per_s_k='无封膜时每K（高于40 °C）的蒸发速率')


CHEM_UNC = dict(low_penalty_per_k='拟合值：按产品册7279 p.6 GAPDH退火梯度中56 °C与62 °C效率之比（93.6/98.7=0.948）标定（calibrate_cfx.py）；绝对效率随库中E_max，不拟合',
                melt_tm_offset_k='假设：常见SYBR熔解峰比两态最近邻计算值低1–5 K；±2 K', pd_seed_base='工程值：按引物3′互补性，使倾向高的检测在40循环左右的NTC出现引物二聚体',
                evaporation_m3_per_s_k='工程值：无封膜的20 µl反应40循环约失水15 %（热盖直接压在开口孔上，真实值取决于板与热盖接触）')


def export_biorad_cfxopus_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'BioradCfxOpusSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.biorad_cfxopus.BioradCfxOpusSession.invoke',
                                              invocation=f'session.invoke(instance_id="cfx_opus_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(dict.fromkeys(list(PRECONDITIONS[k][1]) + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else []))),
                                                                              enforced_by='runtime', check='BioradCfxOpusRuntime.invoke / BioradCfxOpusSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（盖子运动与运行在之后的step中进行）',
                                completion_state_ids=['run.state', 'operation.kind'] if async_ else [], timeout_s=None,
                                cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'block_temperature_range_k': (list(T_BLOCK_RANGE), 'K', '样品块温度4–100 °C', [_BR, _IG, _WB]), 'max_ramp_k_s': (RAMP_MAX, 'K/s', '最高升降温5 °C/s', [_BR, _IG, _WB]),
            'average_ramp_k_s': (3.3, 'K/s', '平均升降温3.3 °C/s', [_BR, _IG, _WB]), 'accuracy_k': (0.2, 'K', '90 °C时准确度±0.2 °C', [_IG, _WB]),
            'uniformity_k': (0.3, 'K', '到温10 s内孔间均匀性±0.3 °C', [_IG, _BR]), 'gradient_lower_k': (list(GRADIENT_RANGE[0]), 'K', '梯度下限30–99 °C', [_IG]),
            'gradient_upper_max_k': (GRADIENT_RANGE[1], 'K', '梯度上限≤100 °C', [_IG]), 'gradient_span_k': (list(GRADIENT_RANGE[2]), 'K', '梯度跨度1–24 °C', [_IG, _BR]),
            'lid_temperature_range_k': (list(T_LID_RANGE), 'K', '热盖30–110 °C', [_BR, _IG]), 'lid_default_k': (LID_DEFAULT_K, 'K', '热盖默认105 °C', [_IG]),
            'sample_volume_range_m3': ([1e-9, 5e-8], 'm3', '样量1–50 µl', [_BR, _IG, _WB]), 'scan_all_channels_s': (SCAN_S['all_channels'], 's', '全通道扫描12 s', [_BR, _IG, _WB]),
            'scan_single_channel_s': (SCAN_S['sybr_fam'], 's', '单通道快扫3 s', [_BR, _IG, _WB]), 'multiplex_targets': (MAX_ASSAYS, '1', '每孔最多5个靶标', [_BR, _IG, _WB]),
            'wells': (NW, '1', '96孔', [_BR, _WB]), 'goto_default_cycles': (GOTO_DEFAULT_CYCLES, '1', 'GOTO默认重复39次', [_IG]),
            'channel_excitation_m': ({c: [v['ex'][0] * 1e-9, v['ex'][1] * 1e-9] for c, v in optics()['channels'].items() if c != '6'}, 'm', '通道1–5激发波段', [_B79]),
            'channel_detection_m': ({c: [v['em'][0] * 1e-9, v['em'][1] * 1e-9] for c, v in optics()['channels'].items() if c != '6'}, 'm', '通道1–5检测波段', [_B79])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    eng = {'ramp_gain_per_s': (RAMP_GAIN, '1/s', '样品块伺服的比例逼近增益（按平均升降温3.3 °C/s标定：60→95 °C约10.4 s到温）', '拟合值：按产品册7299平均升降温3.3 °C/s'),
           'arrival_band_k': (ARRIVE_K, 'K', '块模式到温判据', '工程值'), 'lid_travel_m': (LID_TRAVEL, 'm', '盖子退回行程', '工程值（几何估计）'),
           'lid_motion_s': (LID_TIME_S, 's', '电动盖开/关时间', '工程值'), 'physics_dt_s': (DT, 's', '物理积分网格', '工程值'),
           'fret_channel_m': ([[x * 1e-9 for x in optics()['channels']['6']['ex']], [x * 1e-9 for x in optics()['channels']['6']['em']]], 'm', 'FRET通道（通道1激发、通道2检测）',
                              '假设：厂家未给波段'),
           'scan_fret_s': (SCAN_S['fret'], 's', 'FRET单通道扫描时间', '假设：厂家只给全通道12 s与SYBR/FAM 3 s'),
           'goto_max_cycles': (MAX_GOTO_CYCLES, '1', 'GOTO重复次数上限', '假设：指南只给默认39次'),
           'max_protocol_steps': (MAX_STEPS, '1', '协议最多步骤数', '假设：指南未给'),
           'well_capacity_m3': (PLATE_TYPES['white_lowprofile_96']['max_ul'] * 1e-9, 'm3', '低型96孔板单孔容量（反应体积上限）', '假设：常见低型PCR板约200 µl'),
           'detector_scale_m3_per_mol': (optics()['rfu_per_nm_fam_ch1_20ul'] * 1e6, 'm3/mol', f'检测器标度：20 µl白板中FAM在通道1的RFU每mol/m3（RFU无量纲；库中为{optics()["rfu_per_nm_fam_ch1_20ul"]:g} RFU/nM）',
                                       '拟合值：按产品册7279 p.6探针例子（IL-1β质粒1e5拷贝、10 µl、FAM探针，96孔平均Cq 19.81）标定；用于其它染料、体积与板型时为外推'),
           'sybr_dye_equivalent_mol_m3': (optics()['sybr_dye_nm_equivalent'] * 1e-6, 'mol/m3', '饱和的SYBR/EvaGreen荧光折合的FAM浓度（库中1180 nM）', '工程值（SYBR平台约3000 RFU）'),
           'plate_factor_clear': (optics()['plate_factor']['clear_wells'], '1', '透明孔相对白孔的信号', '工程值'),
           'background_rfu': (list(optics()['background_rfu']), '1', '板、封膜与缓冲液背景（通道1–6）', '工程值'),
           'read_noise_rfu': (optics()['read_noise_rfu'], '1', '读数噪声（标准差）', '工程值；与相对噪声一起使96孔重复Cq标准差约0.12（产品册0.10）'),
           'relative_noise': (optics()['relative_noise'], '1', '与信号成比例的读数噪声', '工程值')}
    for name, (val, unit, meaning, unc) in eng.items():
        src = [_SIM, _BR] if name == 'ramp_gain_per_s' else ([_SIM, _B79] if '拟合' in unc else [_SIM])
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=src, uncertainty=unc)
    for name, val in CHEM.items():
        params['chem_' + name] = dict(value=list(val) if isinstance(val, tuple) else val, unit=CHEM_UNITS[name], meaning_zh=CHEM_ZH[name], evidence='assumption',
                                      source_ids=[_SIM, _B79] if name in ('low_penalty_per_k',) else [_SIM],
                                      uncertainty=CHEM_UNC.get(name, '实时荧光PCR常识的典型值（工程值）'))
    mechanism = dict(
        component_ids=['chassis', 'sample_block', 'lid', 'lid_heater_plate', 'deck', 'touch_screen', 'display_block_digits', 'display_cycle_digits', 'status_bar_green',
                       'status_bar_blue', 'status_bar_red', 'switch_power', 'rear_power_module'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='实时荧光定量PCR：样品块Peltier伺服与梯度、样品温度滞后、热盖；协议执行（温度/梯度/GOTO/熔解、读板、暂停/跳步）；每孔每检测的循环事件扩增模型'
                   '（变性、退火、延伸、热启动、抑制、引物余量、聚合酶能力）、引物二聚体、探针切割；通道荧光（FPbase光谱×厂家波段）、噪声与颜色补偿；基线、阈值、Cq、标准曲线、'
                   '熔解峰与ΔΔCq分析',
        equations=['dT_row/dt = clip(g (T_target - T_sensor), -r_max, +r_max) (r_max = min(5 K/s, step ramp rate)); T_well = T_row + (acc + unif_w) (T_row - T_room)/(363.15 K - T_room)',
                   'dT_sample/dt = (T_well - T_sample) / tau(V), tau = 1.0 s + 1.5e8 s/m3 x V (20 ul: 4 s)',
                   'cycle closes when T_sample > Tm_amplicon - 2 K after a cool phase (Tm_amplicon = library Tm + melt_tm_offset): N += N0 x E_max x theta_denat x f_anneal(T_min, Tm_primer) x (1 - exp(-ext_nt / L)) x a x (1 - inh) x P0/P_ref, '
                   'limited by the polymerase capacity and the primers left',
                   'RFU_ch = k_det x sum_dye [dye]_FAM-equivalent x B_dye x R_dye,ch / (B_FAM R_FAM,1) x plate factor x sqrt(V / 2e-8 m3) + background_ch + noise',
                   'SYBR: [dye]_eq = c_eq,sat x (c_bp / (1 + c_bp / c_sat) + free x c_sat) / c_sat x (1 - 0.012 (T - 333 K)); c_bp = sum N_i L_i (1 - theta_melt,i(T)) / V',
                   'Cq: baseline-subtracted, 3-point smoothed curve crosses threshold = 10 x baseline SD; log-linear interpolation; standard curve E = 10^(-1/slope) - 1'],
        parameters=params, source_ids=[_SIM, _WB, _BR, _B79, _IG, _FP, _PB, _NN], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_cfx_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('biorad_cfxopus', caps, slots, {'qpcr_process': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'qpcr_process': mechanism})
