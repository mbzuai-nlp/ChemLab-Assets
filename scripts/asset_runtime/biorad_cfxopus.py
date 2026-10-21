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
import hashlib
import io
import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_VERSION = '1.1.2'
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
FREEOBJ = {'type': 'object'}


# ---------------------------------------------------------------- touch-screen software: users, files, run setup, settings, logs, diagnostics
# (Instrument Guide 10000119983 chapters 2-6 and appendix C; the terminal layer under the front-panel spec panel_ui.json)
SOFTWARE_VERSION = '1.0.710.726'                    # Home screen figure (guide p.52)
SERIAL_NUMBER = 'OPUS00001'                         # figure example (guide p.52); the instrument name starts as the serial number (p.59)
TIME_ZONES = {'(UTC-11:00) Coordinated Universal Time-11': -11, '(UTC-08:00) Pacific Time (US & Canada)': -8, '(UTC-05:00) Eastern Time (US & Canada)': -5,
              '(UTC) Coordinated Universal Time': 0, '(UTC+01:00) Amsterdam, Berlin, Rome, Stockholm': 1, '(UTC+08:00) Beijing, Chongqing, Hong Kong': 8,
              '(UTC+09:00) Osaka, Sapporo, Tokyo': 9}
DEFAULT_TIME_ZONE = '(UTC-11:00) Coordinated Universal Time-11'   # factory time zone (guide p.60); the list is a subset of the Windows zones (assumption)
CLOCK_START = '2026-01-01T00:00:00'                 # local device time at simulation time 0 (assumption)
SAVER_IDLE_S = [60, 300, 600, 900, 1800, 3600]      # screen-saver idle times offered (assumption: the guide names the setting only)
BUILTIN_USERS = ('Admin', 'Guest', 'Service')       # Login list of a new system (guide p.42 figure); Admin has no password at first (p.45)
NAME_MAX, RUN_NAME_MAX = 32, 64                     # protocol / file / folder names 32 characters, run file names 64 (guide p.88, p.113, p.128)
PASSWORD_LEN = (4, 50)                              # passwords 4-50 characters, case-sensitive (guide p.44)
MY_FILES_MAX = 900                                  # CFX Opus 96: 900 files in My Files, 100 in Run Reports (guide p.135)
DISK_BYTES, FILE_BYTES = 32.0e9, 60.0e3             # internal storage and the size of one file shown by About (assumption)
USB_ROOT, PUBLIC, SAMPLES = 'USB', 'Public', 'Public/Bio-Rad qPCR'
LOG_FILES = ['SystemMessageLog.txt', 'SystemUsageLog.txt', 'DebugLog.txt', 'OSLog.txt', 'FirmwareUpdateLog.txt', 'GUILog.txt', 'WCFLog.txt']   # guide p.158
_K = 273.15
SAMPLE_PROTOCOLS = {                                # read-only sample qPCR protocols in Public > Bio-Rad qPCR (guide p.106; names and steps: assumption,
    'CFX_2stepAmp+Melt': dict(name='CFX_2stepAmp+Melt', sample_volume_m3=2e-8, lid_temperature_k=LID_DEFAULT_K, steps=[   # the usual Bio-Rad defaults)
        dict(kind='temperature', temperature_k=95 + _K, hold_s=180.0), dict(kind='temperature', temperature_k=95 + _K, hold_s=10.0),
        dict(kind='temperature', temperature_k=60 + _K, hold_s=30.0, plate_read=True), dict(kind='goto', goto_step=2, cycles=39),
        dict(kind='melt', start_k=65 + _K, end_k=95 + _K, melt_increment_k=0.5, hold_s=5.0)]),
    'CFX_3stepAmp+Melt': dict(name='CFX_3stepAmp+Melt', sample_volume_m3=2e-8, lid_temperature_k=LID_DEFAULT_K, steps=[
        dict(kind='temperature', temperature_k=95 + _K, hold_s=180.0), dict(kind='temperature', temperature_k=95 + _K, hold_s=10.0),
        dict(kind='temperature', temperature_k=55 + _K, hold_s=30.0), dict(kind='temperature', temperature_k=72 + _K, hold_s=30.0, plate_read=True),
        dict(kind='goto', goto_step=2, cycles=39), dict(kind='melt', start_k=65 + _K, end_k=95 + _K, melt_increment_k=0.5, hold_s=5.0)]),
    'CFX_2stepAmp': dict(name='CFX_2stepAmp', sample_volume_m3=2e-8, lid_temperature_k=LID_DEFAULT_K, steps=[
        dict(kind='temperature', temperature_k=95 + _K, hold_s=180.0), dict(kind='temperature', temperature_k=95 + _K, hold_s=10.0),
        dict(kind='temperature', temperature_k=60 + _K, hold_s=30.0, plate_read=True), dict(kind='goto', goto_step=2, cycles=39)])}
DIAGNOSTIC_TESTS = [('block_thermal', 'Block heating / cooling', 'fan_failure'), ('block_sensor', 'Block temperature sensor', 'block_sensor_drift'),
                    ('lid_motor', 'Lid motor', 'lid_obstructed'), ('heated_lid', 'Heated lid', None)] + \
                   [(f'optics_ch{c}', f'Optics channel {c} LED', f'led_failure_channel_{c}') for c in range(1, 6)] + [('touch_screen', 'Touch screen', None)]
USER = dict(type='string', minLength=1, maxLength=NAME_MAX)
PWD = dict(type='string', minLength=0, maxLength=64)
PATH = dict(type='string', minLength=1, maxLength=400)
FNAME = dict(type='string', minLength=1, maxLength=NAME_MAX)
DIAG = obj(dict(test=TEXT, name=TEXT, result=enum(['Pass', 'Fail'])))
del UNSUPPORTED['user.login'], CAPS['user.login']                                # user accounts are implemented in the terminal layer (1.1.0)
cap('user.login', obj(dict(user=USER, password=nullable(PWD), new_password=nullable(PWD)), optional=('password', 'new_password')), obj(dict(user=TEXT)),
    dict(user='Guest'), ['touch_screen'],
    '在登录列表点用户并输入密码登录（Guest无密码；未设密码的用户不弹键盘；管理员开启密码要求后无密码的用户须在登录时设新密码new_password；Service为维修账户，'
    '其密码只给维修人员）；登录后Home显示用户名')
cap('user.logout', obj({}), obj(dict(user=nullable(TEXT))), {}, ['touch_screen'], 'Home屏点Logout：注销当前用户，回到登录列表')
cap('user.create', obj(dict(name=USER, password=nullable(PWD)), optional=('password',)), obj(dict(user=TEXT)), dict(name='Analyst', password='pcr1234'), ['touch_screen'],
    '登录列表点Create User：输入用户名，再输入并确认密码（4–50字符，区分大小写）或Skip Password（开启密码要求时不能跳过）；建立个人文件夹My Files并登录')
cap('user.set_password', obj(dict(new_password=PWD, current_password=nullable(PWD)), optional=('current_password',)), obj(dict(user=TEXT)),
    dict(new_password='pcr5678', current_password='pcr1234'), ['touch_screen'], 'Tools > User Profile > Change Password：已有密码时先输入当前密码；Guest不能设密码')
cap('user.set_email', obj(dict(email=dict(type='string', maxLength=120))), obj(dict(user=TEXT, email=nullable(TEXT))), dict(email='lab@example.org'), ['touch_screen'],
    'Tools > User Profile > Email Notification：设置或清除（空字符串）运行结束通知的邮箱地址')
cap('user.reset_password', obj(dict(user=USER, new_password=PWD)), obj(dict(user=TEXT)), dict(user='Analyst', new_password='pcr0000'), ['touch_screen'],
    'Admin：Tools > User Management选中用户 > Reset Password（输入并确认新密码；内置账户不能在此重置）')
cap('user.remove', obj(dict(user=USER)), obj(dict(user=TEXT, files_deleted=INT)), dict(user='Analyst'), ['touch_screen'],
    'Admin：Tools > User Management选中用户 > Remove User（永久删除该用户及其协议与运行文件；Admin、Guest、Service不能删除）')
cap('system.configure', obj(dict(name=dict(type='string', minLength=1, maxLength=NAME_MAX), time_zone=enum(list(TIME_ZONES)),
                                 date=dict(type='string', pattern='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'), time=dict(type='string', pattern='^[0-9]{2}:[0-9]{2}(:[0-9]{2})?$'),
                                 password_required=BOOL, screen_saver=BOOL, screen_saver_idle_s={'type': 'integer', 'x-unit': 's', 'enum': list(SAVER_IDLE_S)}),
                            optional=('name', 'time_zone', 'date', 'time', 'password_required', 'screen_saver', 'screen_saver_idle_s')),
    obj(dict(settings=FREEOBJ)), dict(name='qPCR-1', password_required=True), ['touch_screen'],
    'Admin：Tools > System Settings > Apply：仪器名、时区（立即换算日期时间）、日期与时间（24 h）、密码要求、屏保与空闲时间')
cap('system.shutdown', obj({}), obj(dict(shutdown=BOOL)), {}, ['touch_screen', 'switch_power'],
    '登录界面点Shut Down：软关机（须已注销且没有运行）；之后屏幕与样品块、热盖关闭，再按后面板电源开关断电')
cap('email.configure', obj(dict(server=dict(type='string', minLength=1, maxLength=120), port=integer(1, 65535), use_ssl=BOOL, authentication=BOOL,
                                account=dict(type='string', maxLength=120), account_password=PWD), optional=('account', 'account_password')),
    obj(dict(email=FREEOBJ)), dict(server='smtp.example.org', port=587, use_ssl=True, authentication=False), ['touch_screen'],
    'Admin：Tools > Email Settings > Save：发信服务器、端口、SSL、是否认证及认证账户与密码（出厂为Gmail设置）')
cap('files.save_protocol', obj(dict(folder=PATH, name=FNAME, protocol=CAPS['protocol.set']['input_schema'], from_path=nullable(PATH)), optional=('from_path',)),
    obj(dict(path=TEXT, overwritten=BOOL)), dict(folder='Public', name='SYBR_2step_melt', protocol=PROTOCOL_EXAMPLE), ['touch_screen', 'usb_front_port'],
    '协议编辑器Save > Save As：把协议以给定名称存到文件夹（My Files仅本人可见、Public共享、USB；同名同位置覆盖；Bio-Rad qPCR只读，示例协议须改名才能保存）')
cap('files.create_folder', obj(dict(parent=PATH, name=FNAME)), obj(dict(path=TEXT)), dict(parent='Public', name='Assays'), ['touch_screen'],
    'File Browser或Select Location点Create Folder：在当前位置新建文件夹（同一位置不能重名）')
cap('files.copy', obj(dict(path=PATH, folder=PATH, overwrite=BOOL), optional=('overwrite',)), obj(dict(path=TEXT, overwritten=BOOL)),
    dict(path='Public/Bio-Rad qPCR/CFX_2stepAmp+Melt', folder='USB'), ['touch_screen', 'usb_front_port'],
    'File Browser > Options > Copy：把文件或文件夹（含内容）复制到所选位置（同名文件经确认可覆盖；同名文件夹不能覆盖）')
cap('files.delete', obj(dict(path=PATH)), obj(dict(deleted=INT)), dict(path='Public/Assays'), ['touch_screen'],
    'File Browser > Options > Delete：删除文件或文件夹及其内容（只读的示例文件夹不能删除）')
cap('files.rename', obj(dict(path=PATH, name=FNAME)), obj(dict(path=TEXT)), dict(path='Public/SYBR_2step_melt', name='SYBR_melt_v2'), ['touch_screen'],
    'File Browser > Options > Rename：重命名协议文件（运行文件与只读示例协议不能重命名）')
cap('files.open', obj(dict(path=PATH)), obj(dict(name=TEXT, steps=INT, estimated_duration_s=number('s'), control_mode=enum(['calculated', 'block']), path=TEXT)),
    dict(path='Public/Bio-Rad qPCR/CFX_2stepAmp+Melt'), ['touch_screen'],
    'File Browser选中协议或运行文件后点Next/Edit：载入其协议作为当前协议（同protocol.set；运行文件取其中的协议）')
cap('run.configure', obj(dict(folder=nullable(PATH), run_file_name=dict(type='string', maxLength=RUN_NAME_MAX), plate_id=dict(type='string', maxLength=64),
                              notify_beep=BOOL, notify_email=BOOL), optional=('folder', 'run_file_name', 'plate_id', 'notify_beep', 'notify_email')),
    obj(dict(setup=FREEOBJ)), dict(folder='Public', run_file_name='', plate_id='PL-0001', notify_beep=True, notify_email=False), ['touch_screen'],
    'Run Setup：下一次运行的板ID（键盘或条码枪）、运行文件名（默认<协议>_<日期>_<时间>_<序列号>_<用户>，≤64字符）、保存位置（默认协议所在文件夹或My Files）、'
    '结束提示音与邮件通知；运行结束时按此保存运行文件')
cap('run.stop', obj({}), obj(dict(state=TEXT)), {}, ['touch_screen'], 'Run Status > Stop Run：立即停止运行（样品块停止变温；已采集的读板数据保留并保存运行文件）')
cap('runs.export', obj(dict(run_id=nullable(TEXT)), optional=('run_id',)), obj(dict(files=array(TEXT, 0, MAX_RUNS))), dict(run_id=None), ['touch_screen', 'usb_front_port'],
    'Tools > Run Reports > Export / Export All：把一个或全部运行报告导出到前面板U盘')
cap('runs.recover', obj(dict(run_id=TEXT)), obj(dict(file=TEXT)), dict(run_id='R001'), ['touch_screen', 'usb_front_port'],
    'Admin：Run Reports选中运行 > Recover Data：把该运行的.zpcr文件存到U盘（一次一个）')
cap('runs.delete', obj(dict(run_ids=array(TEXT, 1, MAX_RUNS))), obj(dict(deleted=INT)), dict(run_ids=['R001']), ['touch_screen'],
    'Admin：Run Reports删除较旧或无效的运行报告（进行中的运行不能删除）')
cap('logs.export', obj({}), obj(dict(files=array(TEXT, 0, 16))), {}, ['touch_screen', 'usb_front_port'],
    'Tools > System Logs > Export Logs：在U盘Exports文件夹写入消息、使用、调试、OS、固件更新、GUI与WCF日志')
cap('logs.clear', obj({}), obj(dict(cleared_s=number('s'))), {}, ['touch_screen'], 'Admin：Tools > System Logs清除全部消息与使用日志（事件接口events.read不受影响）')
cap('diagnostics.run', obj({}), obj(dict(results=array(DIAG, 0, 16))), {}, ['touch_screen', 'sample_block', 'lid'],
    'Admin：Tools > Diagnostic Test：自检样品块加热/冷却、块温传感器、盖子电机、热盖、5个光学通道LED与触摸屏（不在运行中），给出Pass/Fail')
TERMINAL_CAPS = ['user.login', 'user.logout', 'user.create', 'user.set_password', 'user.set_email', 'user.reset_password', 'user.remove', 'system.configure',
                 'system.shutdown', 'email.configure', 'files.save_protocol', 'files.create_folder', 'files.copy', 'files.delete', 'files.rename', 'files.open',
                 'run.configure', 'run.stop', 'runs.export', 'runs.recover', 'runs.delete', 'logs.export', 'logs.clear', 'diagnostics.run']


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.biorad_cfxopus (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import importlib.util
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


_panel_ui = _panel_engine()
if os.environ.get('CFX_PANEL_BUILD'):                                        # the spec builder imports this module before the spec exists
    PANEL_SPEC = dict(spec_version=_panel_ui.SPEC_VERSION, inputs={}, displays={'screen': dict(kind='touch_tft', size=[1024, 600])},
                      screens={'off': dict(style='static')})
else:
    PANEL_SPEC = _panel_ui.load_spec(ASSET_DIR / 'panel_ui.json', capabilities=set(CAPS))
PANEL_CAPS = _panel_ui.capability_definitions(PANEL_SPEC)                    # panel.touch / switch / capture / frame (kept out of CAPS:
ALL_CAPS = dict(CAPS, **PANEL_CAPS)                                          # CAPS stays the instrument's own command set)
PANEL_GESTURES = [k for k in PANEL_CAPS if k not in ('panel.capture', 'panel.frame')]


# ---------------------------------------------------------------- front panel hooks: the protocol editor's working copy and the run / file views
# (small instrument hooks of the panel engine; the editor keeps the protocol being edited in panel variables and hands it to protocol.set /
# files.save_protocol, the same commands a direct call uses)
EDITOR_COLUMNS = 4                                  # steps side by side on the protocol editor (guide p.88 figure)
NEW_PROTOCOL = dict(name='New Protocol', sample_volume_m3=30e-9, lid_temperature_k=LID_DEFAULT_K, steps=[   # a new protocol (guide p.88 figure)
    dict(kind='temperature', temperature_k=95 + _K, hold_s=180.0), dict(kind='temperature', temperature_k=95 + _K, hold_s=10.0),
    dict(kind='temperature', temperature_k=55 + _K, hold_s=30.0, plate_read=True), dict(kind='goto', goto_step=2, cycles=GOTO_DEFAULT_CYCLES)])
NEW_STEP = {'temperature': dict(kind='temperature', temp_c=50.0, hold=30.0), 'gradient': dict(kind='gradient', lower_c=55.0, upper_c=65.0, hold=30.0),
            'melt': dict(kind='melt', start_c=65.0, end_c=95.0, minc=0.5, hold=5.0), 'goto': dict(kind='goto', goto=1, cycles=GOTO_DEFAULT_CYCLES)}
# a new step: 50 C for 0:30 (guide p.90, p.108); gradient 55-65 C, melt 65-95 C by 0.5 C / 5 s and GOTO step 1 x 39 are assumptions


def _hms(s):
    if s is None:
        return 'inf'
    s = int(round(s))
    return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}' if s >= 3600 else f'{s // 60}:{s % 60:02d}'


def _hms_long(s):
    s = int(round(s))
    return f'{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}'


def _parse_hms(text):
    """A hold time typed as HH:MM:SS, MM:SS or seconds, or the infinite-hold key (None); 1 s to 17:59:59 (guide p.89)."""
    t = str(text).strip().lower()
    if t == 'inf':
        return None
    parts = t.split(':')
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(text)
    v = 0
    for p in parts:
        v = v * 60 + int(p)
    if not 1 <= v <= 64799:
        raise ValueError(text)
    return float(v)


def _step_line(s):
    """One step as text (Run Status list, file details)."""
    k = s['kind']
    if k == 'temperature':
        return f'{s["temperature_k"] - _K:.1f} °C {_hms(s["hold_s"])}' + (' Read' if s.get('plate_read') else '')
    if k == 'gradient':
        return f'Gradient {s["lower_k"] - _K:.1f}-{s["upper_k"] - _K:.1f} °C {_hms(s["hold_s"])}' + (' Read' if s.get('plate_read') else '')
    if k == 'melt':
        return f'Melt {s["start_k"] - _K:.1f}-{s["end_k"] - _K:.1f} °C +{s["melt_increment_k"]:.1f} °C {_hms(s["hold_s"])}'
    return f'GOTO step {s["goto_step"]}, {s["cycles"]} more times'


def _ui_step(s):
    k = s['kind']
    c = lambda T: round(T - _K, 1)                                                  # noqa: E731
    if k == 'temperature':
        return dict(kind=k, temp_c=c(s['temperature_k']), hold=s.get('hold_s'), read=bool(s.get('plate_read')), ramp=s.get('ramp_rate_k_s'),
                    inc=float(s.get('increment_k') or 0.0), ext=float(s.get('extend_s') or 0.0), beep=bool(s.get('beep')))
    if k == 'gradient':
        return dict(kind=k, lower_c=c(s['lower_k']), upper_c=c(s['upper_k']), hold=s.get('hold_s'), read=bool(s.get('plate_read')),
                    ext=float(s.get('extend_s') or 0.0))
    if k == 'melt':
        return dict(kind=k, start_c=c(s['start_k']), end_c=c(s['end_k']), minc=float(s['melt_increment_k']), hold=s.get('hold_s'))
    return dict(kind=k, goto=int(s['goto_step']), cycles=int(s['cycles']))


def _si_step(u):
    k = u['kind']
    K = lambda c: round(c + _K, 2)                                                  # noqa: E731
    if k == 'temperature':
        return dict(kind=k, temperature_k=K(u['temp_c']), hold_s=u['hold'], plate_read=u.get('read', False), ramp_rate_k_s=u.get('ramp'),
                    increment_k=u.get('inc', 0.0), extend_s=u.get('ext', 0.0), beep=u.get('beep', False))
    if k == 'gradient':
        return dict(kind=k, lower_k=K(u['lower_c']), upper_k=K(u['upper_c']), hold_s=u['hold'], plate_read=u.get('read', False), extend_s=u.get('ext', 0.0))
    if k == 'melt':
        return dict(kind=k, start_k=K(u['start_c']), end_k=K(u['end_c']), melt_increment_k=u['minc'], hold_s=u['hold'])
    return dict(kind=k, goto_step=u['goto'], cycles=u['cycles'])


def _texts(steps):
    """Number the editor steps and write the texts of their buttons (temperature / time, GOTO step / cycles) and option marks."""
    for i, u in enumerate(steps):
        k = u['kind']
        u['n'], u['label'] = i, str(i + 1)
        if k == 'temperature':
            top, bottom = f'{u["temp_c"]:.1f} °C', _hms(u['hold'])
        elif k == 'gradient':
            top, bottom = f'{u["lower_c"]:.1f}-{u["upper_c"]:.1f} °C', _hms(u['hold'])
        elif k == 'melt':
            top, bottom = f'{u["start_c"]:.1f}-{u["end_c"]:.1f} °C', f'+{u["minc"]:.1f} °C {_hms(u["hold"])}'
        else:
            top, bottom = f'Step {u["goto"]}', f'{u["cycles"]} X'
        marks = [m for m, on in (('Read', u.get('read') or k == 'melt'), ('Beep', u.get('beep')), ('Ramp', u.get('ramp') is not None),
                                 ('Inc', bool(u.get('inc'))), ('Ext', bool(u.get('ext')))) if on]
        u.update(top=top, bottom=bottom, marks=' '.join(marks), kind_text={'temperature': 'Temperature', 'gradient': 'Gradient', 'melt': 'Melt Curve',
                                                                           'goto': 'GOTO'}[k])
    return steps


def _say(panel, text):
    """The device's message box (dialog 'msg' of the spec)."""
    panel.ui['vars']['msg'] = text
    panel._dialog_open(dict(dialog='msg'), {})


def _hk_editor_new(panel, ctx, a):
    v, st = panel.ui['vars'], panel._state()
    v['head'] = dict(title='New Protocol', name=NEW_PROTOCOL['name'], volume_ul=30.0, lid_c=round(LID_DEFAULT_K - _K, 1), src=None,
                     folder=st.get('host.my_files') or PUBLIC)
    v['steps'] = _texts([_ui_step(s) for s in NEW_PROTOCOL['steps']])
    v['sel'], v['scroll'] = 0, 0
    return True


def _hk_editor_load(panel, ctx, a):
    """Edit Protocol: the selected file of the File Browser (a run file gives its protocol, guide p.117) or the protocol loaded for the run."""
    v, st = panel.ui['vars'], panel._state()
    if a.get('from') == 'file':
        e = next((x for x in st['files.entries'] if x['path'] == v.get('fb_sel')), None)
        if e is None or e['protocol'] is None:
            return False
        p, src = e['protocol'], e['path']
        parent = next((x for x in st['files.entries'] if x['path'] == e['parent']), None)
        folder = e['parent'] if (parent is not None and not parent['readonly'] and e['kind'] == 'protocol') else (st.get('host.my_files') or PUBLIC)
    else:
        p, src = st['protocol.definition'], st['protocol.path']
        if p is None:
            return False
        folder = st.get('host.my_files') or PUBLIC
    v['head'] = dict(title='Edit Protocol', name=p['name'], volume_ul=round(p['sample_volume_m3'] * 1e9, 1), lid_c=round(p['lid_temperature_k'] - _K, 1),
                     src=src, folder=folder)
    v['steps'] = _texts([_ui_step(s) for s in p['steps']])
    v['sel'], v['scroll'] = 0, 0
    return True


def _hk_select(panel, ctx, a):
    v = panel.ui['vars']
    i = v['scroll'] + int(a['col'])
    if i >= len(v['steps']):
        return False
    v['sel'] = i
    return True


def _hk_scroll(panel, ctx, a):
    v = panel.ui['vars']
    v['scroll'] = min(max(v['scroll'] + int(a['n']), 0), max(0, len(v['steps']) - EDITOR_COLUMNS))
    return True


def _show_sel(v):
    if v['sel'] < v['scroll']:
        v['scroll'] = v['sel']
    elif v['sel'] >= v['scroll'] + EDITOR_COLUMNS:
        v['scroll'] = v['sel'] - EDITOR_COLUMNS + 1


def _hk_insert(panel, ctx, a):
    """Insert Step (guide p.89, p.107): a new step to the right of the selected one; Plate Read adds a read to the selected step instead.  GOTO
    targets behind the insertion point move with their steps (assumption)."""
    v = panel.ui['vars']
    steps, kind = v['steps'], a['kind']
    if kind == 'plate_read':
        s = steps[v['sel']]
        if s['kind'] not in ('temperature', 'gradient'):
            _say(panel, 'A plate read can be added to temperature\nand gradient steps only.')
            return False
        s['read'] = True
        _texts(steps)
        return True
    if len(steps) >= MAX_STEPS:
        _say(panel, f'A protocol has at most {MAX_STEPS} steps.')
        return False
    i = v['sel'] + 1
    for s in steps:
        if s['kind'] == 'goto' and s['goto'] > i:
            s['goto'] += 1
    steps.insert(i, copy.deepcopy(NEW_STEP[kind]) if kind != 'temperature' else dict(NEW_STEP[kind], read=False, ramp=None, inc=0.0, ext=0.0, beep=False))
    if steps[i]['kind'] == 'gradient':
        steps[i].update(read=False, ext=0.0)
    _texts(steps)
    v['sel'] = i
    _show_sel(v)
    return True


def _hk_remove(panel, ctx, a):
    v = panel.ui['vars']
    steps = v['steps']
    if len(steps) <= 1:
        _say(panel, 'A protocol needs at least one step.')
        return False
    i = v['sel']
    del steps[i]
    for s in steps:
        if s['kind'] == 'goto' and s['goto'] > i + 1:
            s['goto'] -= 1
    _texts(steps)
    v['sel'] = min(i, len(steps) - 1)
    v['scroll'] = min(v['scroll'], max(0, len(steps) - EDITOR_COLUMNS))
    _show_sel(v)
    return True


STEP_FIELDS = ('kind', 'temp_c', 'hold', 'gradient', 'lower_c', 'upper_c', 'ramp', 'inc', 'ext', 'beep', 'read', 'goto', 'cycles', 'start_c', 'end_c', 'minc')


def step_form_values(u):
    """The Step Options form of an editor step (the time as typed text)."""
    return dict(kind=u['kind'], temp_c=u.get('temp_c', round((u.get('lower_c', 50.0) + u.get('upper_c', 50.0)) / 2, 1)), hold=_hms(u.get('hold')) if 'hold' in u else '',
                gradient=u['kind'] == 'gradient', lower_c=u.get('lower_c', max(30.0, u.get('temp_c', 55.0) - 5.0)),
                upper_c=u.get('upper_c', min(100.0, max(30.0, u.get('temp_c', 55.0) - 5.0) + 10.0)), ramp=u.get('ramp'), inc=u.get('inc', 0.0),
                ext=u.get('ext', 0.0), beep=bool(u.get('beep')), read=bool(u.get('read')), goto=u.get('goto', 1), cycles=u.get('cycles', GOTO_DEFAULT_CYCLES),
                start_c=u.get('start_c', 65.0), end_c=u.get('end_c', 95.0), minc=u.get('minc', 0.5))


def _hk_step_open(panel, ctx, a):
    v = panel.ui['vars']
    panel._form_open(dict(form='f_step', set={k: {'const': x} for k, x in step_form_values(v['steps'][v['sel']]).items()}), {})
    return True


def _refuse(panel, b, text):
    """A refused Step Options value: the message, and the form shows the step unchanged."""
    v = panel.ui['vars']
    b.update(step_form_values(v['steps'][v['sel']]))
    _say(panel, text)
    return False


def _hk_step_apply(panel, ctx, a):
    """A Step Options value (or a value tapped on the step) goes into the selected step: Gradient ticked turns a temperature step into a gradient step
    (lower / upper around it, assumption) and back (the middle temperature); the gradient span must stay 1-24 C (guide p.86)."""
    v = panel.ui['vars']
    f = panel.ui['forms'].get('f_step')
    if f is None:
        return False
    b = f['buffer']
    u = dict(v['steps'][v['sel']])
    try:
        hold = _parse_hms(b['hold']) if u['kind'] != 'goto' else None
    except ValueError:
        return _refuse(panel, b, 'Enter a time between 0:01 and 17:59:59 (or Inf).')
    k = u['kind']
    newk = ('gradient' if b['gradient'] else 'temperature') if k in ('temperature', 'gradient') else k
    if newk == 'temperature':
        temp = float(b['temp_c']) if k == 'temperature' else round((u['lower_c'] + u['upper_c']) / 2, 1)
        if hold is None and v['sel'] != len(v['steps']) - 1:
            return _refuse(panel, b, 'An infinite hold is allowed in the last step only.')
        u = dict(kind=newk, temp_c=temp, hold=hold, read=bool(b['read']), ramp=b['ramp'], inc=float(b['inc']), ext=float(b['ext']), beep=bool(b['beep']))
    elif newk == 'gradient':
        if k == 'temperature':                                                  # the box was just ticked: a 10 C range around the step temperature
            lo = round(max(30.0, min(90.0, float(b['temp_c']) - 5.0)), 1)
            hi = round(lo + 10.0, 1)
        else:
            lo, hi = float(b['lower_c']), float(b['upper_c'])
        if not (30.0 <= lo <= 99.0 and hi <= 100.0 and 1.0 <= hi - lo <= 24.0) or hold is None:
            return _refuse(panel, b, 'Gradient: lower 30.0-99.0 °C, upper up to\n100.0 °C, within 24 °C of the lower value.')
        u = dict(kind=newk, lower_c=lo, upper_c=hi, hold=hold, read=bool(b['read']), ext=float(b['ext']))
    elif newk == 'melt':
        if hold is None or float(b['end_c']) - float(b['start_c']) < max(0.5, float(b['minc'])):
            return _refuse(panel, b, 'Melt curve: the end must be above the start\nby at least one increment.')
        u = dict(kind=newk, start_c=float(b['start_c']), end_c=float(b['end_c']), minc=float(b['minc']), hold=hold)
    else:
        u = dict(kind=newk, goto=int(b['goto']), cycles=int(b['cycles']))
    v['steps'][v['sel']] = u
    _texts(v['steps'])
    for key, x in step_form_values(u).items():                                  # the form shows the step as it is now (e.g. the gradient range)
        b[key] = x
    return True


def _hk_head_apply(panel, ctx, a):
    f = panel.ui['forms'].get('f_head')
    if f is None:
        return False
    h = panel.ui['vars']['head']
    h.update(name=f['buffer']['name'], volume_ul=float(f['buffer']['volume_ul']), lid_c=float(f['buffer']['lid_c']))
    return True


def _hk_saved(panel, ctx, a):
    """After Save As: the editor shows the saved name; Save and the run file default to that folder."""
    v = panel.ui['vars']
    b = (panel.ui['forms'].get('f_save') or {}).get('buffer') or ctx.get('buffer') or {}
    v['head'].update(name=b.get('name', v['head']['name']), folder=b.get('folder', v['head']['folder']), src=f'{b.get("folder")}/{b.get("name")}')
    return True


def _src_protocol(panel, ctx, s):
    v = panel.ui['vars']
    h = v.get('head') or {}
    return dict(name=h.get('name') or NEW_PROTOCOL['name'], sample_volume_m3=round(round(float(h.get('volume_ul', 0.0)), 1) * 1e-9, 15),
                lid_temperature_k=round(float(h.get('lid_c', 105.0)) + _K, 2), steps=[_si_step(u) for u in v.get('steps') or []])


def _src_exists(panel, ctx, s):
    b = (panel.ui['forms'].get(s.get('form', 'f_save')) or {}).get('buffer') or {}
    return f'{b.get("folder")}/{b.get("name")}' in {e['path'] for e in panel._state()['files.entries']}


def _src_run_name(panel, ctx, s):
    """Default run file name <ProtocolName>_<Date>_<Time>_<SerialNumber>_<UserName> (guide p.113, figure p.91)."""
    st = panel._state()
    stamp = st['term.datetime'].replace('-', '').replace(':', '').replace('T', '_')
    return f'{st["protocol.name"] or "Protocol"}_{stamp}_{SERIAL_NUMBER}_{(st["term.user"] or "Guest").upper()}'[:RUN_NAME_MAX]


def _src_run_folder(panel, ctx, s):
    h = panel.ui['vars'].get('head') or {}
    return h.get('folder') if panel.ui['vars'].get('setup_from') == 'editor' and h.get('folder') else panel.host._default_folder()


def _src_view(panel, ctx, s):
    """Run, curve, report, log and About views of the runtime (computed when a page shows them)."""
    h = panel.host
    kind = s['view']
    if kind == 'run':
        return h.run_view().get(s['part'])
    if kind == 'curves':
        return h.curves_view(str(panel.ui['vars'].get('curve_ch') or '1'))
    if kind == 'runs':
        return h.runs_view()
    if kind == 'file':
        return h.file_view(panel.ui['vars'].get('fb_sel'))
    if kind == 'logs':
        return h.logs_view(panel.ui['vars'].get('log_kind') or 'messages')
    if kind == 'about':
        return h.about_view().get(s['part'])
    if kind == 'demo':
        return _demo_curves(panel)
    raise KeyError(kind)


def _demo_curves(panel):
    """Real-Time PCR Demo (guide p.56): a pre-recorded looking run animated on the screen, one cycle per second since Start (synthetic sigmoids)."""
    v = panel.ui['vars']
    if v.get('demo_t0') is None:
        return []
    n = int(min(40, max(0, panel._now() - v['demo_t0'])))
    cq = [18.0, 21.0, 24.0, 27.0, 30.0] if v.get('demo_kind') != 'multiplex' else [20.0, 22.5, 25.0, 27.5]
    return [[[float(c), round(5000.0 / (1.0 + math.exp(-(c - q) / 1.4)), 1)] for c in range(1, n + 1)] for q in cq]


PANEL_HOOKS = {'cfx_editor_new': _hk_editor_new, 'cfx_editor_load': _hk_editor_load, 'cfx_select': _hk_select, 'cfx_scroll': _hk_scroll,
               'cfx_insert': _hk_insert, 'cfx_remove': _hk_remove, 'cfx_step_open': _hk_step_open, 'cfx_step_apply': _hk_step_apply,
               'cfx_head_apply': _hk_head_apply, 'cfx_saved': _hk_saved, 'cfx_protocol': _src_protocol, 'cfx_exists': _src_exists,
               'cfx_run_name': _src_run_name, 'cfx_run_folder': _src_run_folder, 'cfx_view': _src_view}

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
FILE_ENTRY = obj(dict(path=TEXT, parent=nullable(TEXT), name=TEXT, kind=enum(['folder', 'protocol', 'run']), readonly=BOOL, owner=nullable(TEXT), created=TEXT,
                      protocol=nullable(FREEOBJ), run=nullable(FREEOBJ)))
slot('term.user', nullable(TEXT), '1', 'derived', 'touch_screen', '触摸屏当前登录的用户（空：显示登录列表）')
slot('term.users', array(obj(dict(name=TEXT, kind=enum(['admin', 'guest', 'service', 'user']), has_password=BOOL, email=nullable(TEXT))), 0, 64), '1', 'setpoint',
     'touch_screen', '登录列表（最近登录的在上；内置Admin、Guest、Service与新建用户）')
slot('term.settings', obj(dict(name=TEXT, time_zone=TEXT, password_required=BOOL, screen_saver=BOOL, screen_saver_idle_s=number('s'))), '1', 'setpoint', 'touch_screen',
     '系统设置：仪器名、时区、密码要求、屏保与空闲时间')
slot('term.datetime', TEXT, '1', 'derived', 'touch_screen', '仪器本地日期时间（ISO；随仿真时钟走，System Settings可改）')
slot('term.shutdown', BOOL, '1', 'derived', 'switch_power', '已软关机（等待按后面板电源开关）')
slot('files.entries', array(FILE_ENTRY, 0, 2000), '1', 'derived', 'touch_screen', '文件浏览器中的文件夹、协议与运行文件（My Files、Public、Bio-Rad qPCR示例、USB）')
slot('usb.files', array(TEXT, 0, 4000), '1', 'derived', 'usb_front_port', '前面板U盘上由触摸屏写入的文件（复制、运行报告、恢复的.zpcr、日志）')
slot('run.setup', nullable(FREEOBJ), '1', 'setpoint', 'touch_screen', '下一次运行的Run Setup（保存位置、运行文件名、板ID、提示音与邮件通知）')
slot('protocol.definition', nullable(FREEOBJ), '1', 'setpoint', 'touch_screen', '当前载入的协议（SI：体积m3、温度K、时间s；步骤同protocol.set）')
slot('protocol.path', nullable(TEXT), '1', 'setpoint', 'touch_screen', '当前协议所来自的文件（直接设定的协议为空）')
slot('email.settings', obj(dict(server=TEXT, port=INT, use_ssl=BOOL, authentication=BOOL, account=TEXT)), '1', 'setpoint', 'touch_screen', '发信服务器设置（密码不读出）')
slot('diagnostics.last', nullable(obj(dict(time=TEXT, results=array(DIAG, 0, 16)))), '1', 'derived', 'touch_screen', '上一次自检的时间与各项结果')
slot('logs.cleared_s', number('s'), 's', 'derived', 'touch_screen', '系统日志上次被Admin清除的仿真时间（触摸屏只显示其后的消息）')


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
        self.term = self._fresh_terminal()
        self.power_epoch = 0                        # power-ups seen by the panel (a cycle between two gestures still counts)
        self.powered_since = 0.0                    # host time of the last power-up (start of the touch-screen boot)
        self.panel_capture_dir = None               # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)
        self._panel_cache = {}
        self.panel = _panel_ui.PanelUI(PANEL_SPEC, self, hooks=PANEL_HOOKS)

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
        elif self.switch_on and not self.power and self._mains_ok() and not self.term['shutdown']:
            self._power_up()
        return dict(plant=dict(self.plant))

    def _power_up(self):
        self.power = True
        self.power_epoch += 1
        self.powered_since = self.t
        self.term.update(user=None, shutdown=False)                             # start-up ends at the Login list (guide p.42)
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
        self._event('power', 'POWER_OFF', {'switch': '断电（开关关闭）', 'shutdown': '软关机（Shut Down）'}.get(cause, '断电（市电中断或电压超范围）'))

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
        self.protocol = self._normalize_protocol(name, sample_volume_m3, steps, lid_temperature_k)
        self.term['protocol_path'] = None
        est = self._estimate(self.protocol, 'all_channels')
        self._event('protocol', 'PROTOCOL_LOADED', f'协议{name}：{len(self.protocol["steps"])}步，预计{est / 60:.1f} min')
        return dict(name=name, steps=len(self.protocol['steps']), estimated_duration_s=est, control_mode='calculated' if sample_volume_m3 > 0 else 'block')

    def _normalize_protocol(self, name, sample_volume_m3, steps, lid_temperature_k=LID_DEFAULT_K):
        """Check a protocol against the step rules (Table 10) and return it with the instrument's resolutions (0.1 C, 1 s)."""
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
        return dict(name=name, sample_volume_m3=float(sample_volume_m3), lid_temperature_k=float(lid_temperature_k), steps=norm)

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
        self.run['run_setup'] = self.term['setup'] or dict(folder=self._default_folder(), run_file_name='', plate_id='', notify_beep=False, notify_email=False,
                                                       user=self.term['user'])
        self.run['started_local'] = self._clock()
        self.term['setup'] = None
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
        self._run_file(r)

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
            **self._terminal_read(),
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
            elif not active and self.switch_on and not self.power and self._mains_ok() and not self.term['shutdown']:
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


    # ---------------------------------------------------------------- touch-screen software (terminal layer)
    def _fresh_terminal(self):
        users = {'Admin': dict(kind='admin', password=None, email=None), 'Guest': dict(kind='guest', password=None, email=None),
                 'Service': dict(kind='service', password=None, email=None)}
        files = {}
        for path, ro in ((PUBLIC, False), (SAMPLES, True), (USB_ROOT, False), ('My Files/Admin', False)):
            self._mkentry(files, path, 'folder', readonly=ro, owner='Admin' if path.startswith('My Files') else None)
        for name, p in SAMPLE_PROTOCOLS.items():
            self._mkentry(files, f'{SAMPLES}/{name}', 'protocol', readonly=True, protocol=self._normalize_protocol(**copy.deepcopy(p)))
        return dict(user=None, users=users, order=list(BUILTIN_USERS), shutdown=False, files=files, usb=[], setup=None, protocol_path=None,
                    settings=dict(name=SERIAL_NUMBER, time_zone=DEFAULT_TIME_ZONE, clock_offset_s=0.0, password_required=False, screen_saver=False,
                                  screen_saver_idle_s=600),
                    email=dict(server='smtp.gmail.com', port=587, use_ssl=True, authentication=True, account='', account_password=''),
                    logs_cleared_s=0.0, logs_mark=0, diagnostics=None)

    def _mkentry(self, files, path, kind, readonly=False, owner=None, protocol=None, run=None):
        parent, _, name = path.rpartition('/')
        files[path] = dict(path=path, parent=parent or None, name=name, kind=kind, readonly=readonly, owner=owner, created=self._clock(),
                           protocol=protocol, run=run)
        return files[path]

    def _clock(self):
        """Local device time (ISO): the clock started at CLOCK_START plus the simulation time and the offsets set in System Settings."""
        import datetime
        t0 = datetime.datetime.fromisoformat(CLOCK_START)
        off = self.term['settings']['clock_offset_s'] if getattr(self, 'term', None) else 0.0
        return (t0 + datetime.timedelta(seconds=round(self.t + off))).isoformat()

    def _need_terminal(self):
        self._need_power()
        if self.term['shutdown']:
            raise ActionRejected('POWER_OFF')

    def _need_user(self):
        self._need_terminal()
        if self.term['user'] is None:
            raise ActionRejected('LOGIN_REQUIRED')
        return self.term['user']

    def _need_admin(self):
        if self._need_user() != 'Admin':
            raise ActionRejected('ADMIN_REQUIRED')

    @staticmethod
    def _check_name(name, limit=NAME_MAX):
        if not (1 <= len(name) <= limit) or name != name.strip() or any(c in '/\\:*?"<>|' for c in name):
            raise ActionRejected('INVALID_NAME')

    @staticmethod
    def _check_password(pw):
        if pw is None or not (PASSWORD_LEN[0] <= len(pw) <= PASSWORD_LEN[1]) or not pw.isalnum():
            raise ActionRejected('INVALID_PASSWORD')

    def _my_files(self, user=None):
        user = user or self.term['user']
        return f'My Files/{user}' if user is not None and self.term['users'].get(user, {}).get('kind') in ('admin', 'user') else None

    def _may_access(self, path):
        """Locations a user may use (guide p.89-90, p.111, p.127): his own My Files, the Public folders and the USB drive; Guest Public and USB only;
        Admin also the other users' My Files."""
        root = path.split('/')[0]
        if root in (PUBLIC, USB_ROOT):
            return True
        own = self._my_files()
        return own is not None and (path == own or path.startswith(own + '/') or self.term['user'] == 'Admin')

    def _folder(self, path, write=True):
        f = self.term['files'].get(path)
        if f is None or f['kind'] != 'folder':
            raise ActionRejected('NO_SUCH_FOLDER')
        if not self._may_access(path):
            raise ActionRejected('FOLDER_NOT_ALLOWED')
        if write and f['readonly']:
            raise ActionRejected('READ_ONLY')
        return f

    def _entry(self, path):
        f = self.term['files'].get(path)
        if f is None:
            raise ActionRejected('NO_SUCH_FILE')
        if not self._may_access(path):
            raise ActionRejected('FOLDER_NOT_ALLOWED')
        return f

    def _subtree(self, path):
        return [p for p in self.term['files'] if p == path or p.startswith(path + '/')]

    def _count_my_files(self, path):
        root = '/'.join(path.split('/')[:2])
        return sum(1 for p, f in self.term['files'].items() if p.startswith(root + '/') and f['kind'] != 'folder') if path.startswith('My Files') else 0

    def _cmd_user_login(self, user, password=None, new_password=None):
        self._need_terminal()
        t = self.term
        if t['user'] is not None:
            raise ActionRejected('LOGGED_IN')
        u = t['users'].get(user)
        if u is None:
            raise ActionRejected('UNKNOWN_USER')
        if u['kind'] == 'service':
            raise ActionRejected('WRONG_PASSWORD')                              # the service code is given to service engineers only
        if u['password'] is not None and password != u['password']:
            raise ActionRejected('WRONG_PASSWORD')
        if u['password'] is None and u['kind'] != 'guest' and t['settings']['password_required']:
            if new_password is None:
                raise ActionRejected('PASSWORD_SETUP_REQUIRED')                 # guide p.57: prompted to create a password at the next log-in
            self._check_password(new_password)
            u['password'] = new_password
        t['user'] = user
        t['order'] = [user] + [x for x in t['order'] if x != user]               # most recent user at the top (guide p.45)
        self._event('power', 'LOGIN', f'用户{user}登录')
        return dict(user=user)

    def _cmd_user_logout(self):
        user = self._need_user()
        self.term['user'] = None
        self._event('power', 'LOGOUT', f'用户{user}注销')
        return dict(user=user)

    def _cmd_user_create(self, name, password=None):
        self._need_terminal()
        t = self.term
        if t['user'] is not None:
            raise ActionRejected('LOGGED_IN')
        self._check_name(name)
        if any(x.lower() == name.lower() for x in t['users']):
            raise ActionRejected('NAME_EXISTS')
        if password is None and t['settings']['password_required']:
            raise ActionRejected('PASSWORD_REQUIRED')
        if password is not None:
            self._check_password(password)
        t['users'][name] = dict(kind='user', password=password, email=None)
        self._mkentry(t['files'], f'My Files/{name}', 'folder', owner=name)
        t['user'] = name
        t['order'] = [name] + [x for x in t['order'] if x != name]
        self._event('power', 'USER_CREATED', f'新建用户{name}并登录')
        return dict(user=name)

    def _cmd_user_set_password(self, new_password, current_password=None):
        user = self._need_user()
        u = self.term['users'][user]
        if u['kind'] == 'guest':
            raise ActionRejected('GUEST_NOT_ALLOWED')                           # Guest sets only the email address (guide p.55, p.73)
        if u['password'] is not None and current_password != u['password']:
            raise ActionRejected('WRONG_PASSWORD')
        self._check_password(new_password)
        u['password'] = new_password
        self._event('power', 'PASSWORD_CHANGED', f'用户{user}修改密码')
        return dict(user=user)

    def _cmd_user_set_email(self, email):
        user = self._need_user()
        if email and ('@' not in email or email.startswith('@') or email.endswith('@') or ' ' in email):
            raise ActionRejected('INVALID_EMAIL')
        self.term['users'][user]['email'] = email or None
        return dict(user=user, email=email or None)

    def _cmd_user_reset_password(self, user, new_password):
        self._need_admin()
        u = self.term['users'].get(user)
        if u is None:
            raise ActionRejected('UNKNOWN_USER')
        if u['kind'] != 'user':
            raise ActionRejected('BUILTIN_USER')
        self._check_password(new_password)
        u['password'] = new_password
        self._event('power', 'PASSWORD_RESET', f'Admin重置用户{user}的密码')
        return dict(user=user)

    def _cmd_user_remove(self, user):
        self._need_admin()
        u = self.term['users'].get(user)
        if u is None:
            raise ActionRejected('UNKNOWN_USER')
        if u['kind'] != 'user':
            raise ActionRejected('BUILTIN_USER')                                # Admin, Guest and Service cannot be deleted (guide p.83)
        gone = self._subtree(f'My Files/{user}')
        n = sum(1 for p in gone if self.term['files'][p]['kind'] != 'folder')
        for p in gone:
            del self.term['files'][p]
        del self.term['users'][user]
        self.term['order'] = [x for x in self.term['order'] if x != user]
        if self.term['protocol_path'] in gone:
            self.term['protocol_path'] = None
        self._event('power', 'USER_REMOVED', f'Admin删除用户{user}（{n}个文件）')
        return dict(user=user, files_deleted=n)

    def _cmd_system_configure(self, name=None, time_zone=None, date=None, time=None, password_required=None, screen_saver=None, screen_saver_idle_s=None):
        """System Settings > Apply (guide p.59-61): a new time zone shifts the shown date and time at once; a date / time given afterwards sets the local
        clock (seconds 0 when only HH:MM is given)."""
        import datetime
        self._need_admin()
        s = self.term['settings']
        if name is not None:
            self._check_name(name)
        try:
            d = datetime.date.fromisoformat(date) if date is not None else None
            hms = [int(x) for x in time.split(':')] if time is not None else None
            if hms is not None and not (hms[0] < 24 and hms[1] < 60 and (len(hms) < 3 or hms[2] < 60)):
                raise ValueError(time)
        except ValueError:
            raise ActionRejected('INVALID_DATETIME') from None
        new = dict(s)
        if time_zone is not None:
            new['clock_offset_s'] += 3600.0 * (TIME_ZONES[time_zone] - TIME_ZONES[s['time_zone']])
            new['time_zone'] = time_zone
        if d is not None or hms is not None:
            now = datetime.datetime.fromisoformat(CLOCK_START) + datetime.timedelta(seconds=round(self.t + new['clock_offset_s']))
            want = datetime.datetime.combine(d or now.date(), datetime.time(*(hms + [0] * (3 - len(hms)))) if hms is not None else now.time())
            new['clock_offset_s'] += (want - now).total_seconds()
        for k, v in dict(name=name, password_required=password_required, screen_saver=screen_saver, screen_saver_idle_s=screen_saver_idle_s).items():
            if v is not None:
                new[k] = v
        self.term['settings'] = new
        self._event('power', 'SETTINGS', 'Admin修改系统设置')
        return dict(settings=self._settings_view())

    def _settings_view(self):
        s = self.term['settings']
        return dict(name=s['name'], time_zone=s['time_zone'], password_required=s['password_required'], screen_saver=s['screen_saver'],
                    screen_saver_idle_s=float(s['screen_saver_idle_s']))

    def _cmd_system_shutdown(self):
        self._need_terminal()
        if self.term['user'] is not None:
            raise ActionRejected('LOGGED_IN')                                   # Shut Down is on the Login screen (guide p.49)
        if self._running():
            raise ActionRejected('RUN_IN_PROGRESS')
        self._power_off('shutdown')
        self.term['shutdown'] = True
        return dict(shutdown=True)

    def _cmd_email_configure(self, server, port, use_ssl, authentication, account='', account_password=''):
        self._need_admin()
        if authentication and not account:
            raise ActionRejected('INVALID_EMAIL')
        self.term['email'] = dict(server=server, port=int(port), use_ssl=bool(use_ssl), authentication=bool(authentication), account=account if authentication else '',
                                  account_password=account_password if authentication else '')
        self._event('power', 'EMAIL_SETTINGS', f'Admin保存发信服务器{server}:{port}')
        return dict(email=self._email_view())

    def _email_view(self):
        e = self.term['email']
        return dict(server=e['server'], port=e['port'], use_ssl=e['use_ssl'], authentication=e['authentication'], account=e['account'])

    def _store(self, folder, name, kind, protocol=None, run=None, overwrite=True):
        f = self._folder(folder)
        path = f'{folder}/{name}'
        old = self.term['files'].get(path)
        if old is not None and (old['kind'] == 'folder' or old['kind'] != kind or not overwrite):
            raise ActionRejected('NAME_EXISTS')
        if old is None and folder.startswith('My Files') and self._count_my_files(folder) >= MY_FILES_MAX:
            raise ActionRejected('STORAGE_FULL')
        self._mkentry(self.term['files'], path, kind, owner=self.term['user'], protocol=protocol, run=run)
        return path, old is not None

    def _cmd_files_save_protocol(self, folder, name, protocol, from_path=None):
        self._need_user()
        self._check_name(name)
        src = self.term['files'].get(from_path) if from_path else None
        if src is not None and src['readonly'] and name == src['name']:
            raise ActionRejected('RENAME_REQUIRED')                             # a sample qPCR protocol is saved only under a new name (guide p.107)
        norm = self._normalize_protocol(**copy.deepcopy(protocol))
        path, over = self._store(folder, name, 'protocol', protocol=dict(norm, name=name))
        self._event('protocol', 'PROTOCOL_SAVED', f'协议保存为{path}')
        return dict(path=path, overwritten=over)

    def _cmd_files_create_folder(self, parent, name):
        self._need_user()
        self._check_name(name)
        self._folder(parent)
        path = f'{parent}/{name}'
        if path in self.term['files']:
            raise ActionRejected('NAME_EXISTS')
        self._mkentry(self.term['files'], path, 'folder', owner=self.term['user'])
        return dict(path=path)

    def _cmd_files_copy(self, path, folder, overwrite=False):
        self._need_user()
        src = self._entry(path)
        self._folder(folder)
        if folder == path or folder.startswith(path + '/'):
            raise ActionRejected('INVALID_DESTINATION')
        dest = f'{folder}/{src["name"]}'
        old = self.term['files'].get(dest)
        if old is not None and (src['kind'] == 'folder' or old['kind'] != src['kind'] or not overwrite):
            raise ActionRejected('NAME_EXISTS')                                 # guide p.130: a file is overwritten after Yes; folders never (p.133)
        new = {}
        for p in self._subtree(path):
            e = self.term['files'][p]
            q = dest + p[len(path):]
            new[q] = dict(copy.deepcopy(e), path=q, parent=q.rpartition('/')[0], readonly=False, owner=self.term['user'], created=self._clock())
        if dest.startswith('My Files') and self._count_my_files(dest) + sum(1 for e in new.values() if e['kind'] != 'folder') > MY_FILES_MAX:
            raise ActionRejected('STORAGE_FULL')
        self.term['files'].update(new)
        if folder.split('/')[0] == USB_ROOT:
            self.term['usb'] = sorted(set(self.term['usb']) | {p[len(USB_ROOT) + 1:] for p, e in new.items() if e['kind'] != 'folder'})
        self._event('data', 'FILE_COPIED', f'{path}复制到{folder}')
        return dict(path=dest, overwritten=old is not None)

    def _cmd_files_delete(self, path):
        self._need_user()
        e = self._entry(path)
        if e['readonly'] or e['parent'] in (None, 'My Files'):
            raise ActionRejected('READ_ONLY')                                   # the locations themselves and the sample qPCR folder stay
        gone = self._subtree(path)
        n = sum(1 for p in gone if self.term['files'][p]['kind'] != 'folder')
        for p in gone:
            del self.term['files'][p]
        if path.split('/')[0] == USB_ROOT:
            self.term['usb'] = [x for x in self.term['usb'] if f'{USB_ROOT}/{x}' not in gone]
        if self.term['protocol_path'] in gone:
            self.term['protocol_path'] = None
        self._event('data', 'FILE_DELETED', f'删除{path}')
        return dict(deleted=n)

    def _cmd_files_rename(self, path, name):
        self._need_user()
        e = self._entry(path)
        if e['kind'] != 'protocol':
            raise ActionRejected('NOT_A_PROTOCOL')                              # run files and folders are not renamed (guide p.130)
        if e['readonly']:
            raise ActionRejected('READ_ONLY')
        self._check_name(name)
        new = f'{e["parent"]}/{name}'
        if new in self.term['files']:
            raise ActionRejected('NAME_EXISTS')
        del self.term['files'][path]
        self.term['files'][new] = dict(e, path=new, name=name, protocol=dict(e['protocol'], name=name))
        if self.term['protocol_path'] == path:
            self.term['protocol_path'] = new
        return dict(path=new)

    def _cmd_files_open(self, path):
        self._need_user()
        e = self._entry(path)
        if e['kind'] == 'folder':
            raise ActionRejected('NOT_A_PROTOCOL')
        p = e['protocol']
        out = self._cmd_protocol_set(name=p['name'], sample_volume_m3=p['sample_volume_m3'], steps=copy.deepcopy(p['steps']), lid_temperature_k=p['lid_temperature_k'])
        self.term['protocol_path'] = path
        return dict(out, path=path)

    def _default_folder(self):
        """Default run-file location (guide p.92, p.113): the folder of the opened protocol when the user may write there, else My Files (Public for Guest)."""
        pp = self.term['protocol_path']
        if pp is not None:
            parent = self.term['files'].get(pp, {}).get('parent')
            f = self.term['files'].get(parent)
            if f is not None and not f['readonly'] and self._may_access(parent):
                return parent
        return self._my_files() or PUBLIC

    def _cmd_run_configure(self, folder=None, run_file_name='', plate_id='', notify_beep=False, notify_email=False):
        user = self._need_user()
        folder = folder or self._default_folder()
        self._folder(folder)
        if run_file_name:
            self._check_name(run_file_name, RUN_NAME_MAX)
        if notify_email and not self.term['users'][user]['email']:
            raise ActionRejected('EMAIL_NOT_CONFIGURED')                        # guide p.114: disabled while no email address is set
        self.term['setup'] = dict(folder=folder, run_file_name=run_file_name, plate_id=plate_id, notify_beep=bool(notify_beep), notify_email=bool(notify_email),
                                  user=user)
        return dict(setup=copy.deepcopy(self.term['setup']))

    def _cmd_run_stop(self):
        self._need_power()
        if not self._running():
            raise ActionRejected('NOT_RUNNING')
        self._finish_run('stopped', None)
        return dict(state='stopped')

    def _run_file(self, r):
        """At the end of a run (guide p.114): the run file goes to the Run Setup location under its name (default <Protocol>_<Date>_<Time>_<SN>_<User>),
        then the notifications: a beep, and an email that this configuration cannot send (no network connection)."""
        s = r.get('run_setup') or {}
        user = s.get('user')
        stamp = r.get('started_local', self._clock()).replace('-', '').replace(':', '').replace('T', '_')
        name = s.get('run_file_name') or f'{r["protocol"]["name"]}_{stamp}_{SERIAL_NUMBER}_{(user or "Guest").upper()}'[:RUN_NAME_MAX]
        folder = s.get('folder') or PUBLIC
        f = self.term['files'].get(folder)
        if f is None or f['kind'] != 'folder' or f['readonly']:
            folder = PUBLIC                                                     # the chosen folder was deleted meanwhile (assumption)
        path = f'{folder}/{name}'
        if path in self.term['files']:
            path = f'{path}_{r["run_id"]}'
        self._mkentry(self.term['files'], path, 'run', owner=user, protocol=copy.deepcopy(r['protocol']),
                      run=dict(run_id=r['run_id'], status=r['state'], plate_id=s.get('plate_id', ''), scan_mode=r['scan_mode'], finished=self._clock(), cycles=r['cycle']))
        if folder == USB_ROOT or folder.startswith(USB_ROOT + '/'):
            self.term['usb'] = sorted(set(self.term['usb']) | {path[len(USB_ROOT) + 1:]})
        self._event('data', 'RUN_FILE_SAVED', f'运行文件保存为{path}', ref=r['run_id'])
        if s.get('notify_beep'):
            self._event('run', 'BEEP', '运行结束提示音', ref=r['run_id'])
        if s.get('notify_email'):
            self._event('error', 'EMAIL_NOT_SENT', '运行结束邮件未发送：本配置没有网络连接', ref=r['run_id'])

    def _need_run(self, run_id):
        if run_id not in self.runs:
            raise ActionRejected('UNKNOWN_RUN')
        return self.runs[run_id]

    def _caller_foreign(self, object_id):
        """True when a session agent calls and the object belongs to another agent (the session guards refuse those calls)."""
        caller, owner = self._caller, self._owners.get(object_id)
        return (caller is not None and self._principals.get(caller) == 'agent' and owner is not None and owner != caller
                and self._principals.get(owner) == 'agent')

    def _cmd_runs_export(self, run_id=None):
        self._need_user()
        runs = [self._need_run(run_id)] if run_id is not None else [r for r in self.runs.values() if not self._caller_foreign(r['run_id'])]
        if not runs:
            raise ActionRejected('NO_RUN_DATA')
        names = [f'RunReports/{r["run_id"]}_{r["protocol"]["name"]}_report.pdf' for r in runs]
        self.term['usb'] = sorted(set(self.term['usb']) | set(names))
        self._event('data', 'RUN_REPORTS_EXPORTED', f'导出{len(names)}个运行报告到U盘')
        return dict(files=names)

    def _cmd_runs_recover(self, run_id):
        self._need_admin()
        r = self._need_run(run_id)
        name = f'{r["run_id"]}_{r["protocol"]["name"]}.zpcr'
        self.term['usb'] = sorted(set(self.term['usb']) | {name})
        self._event('data', 'RUN_RECOVERED', f'运行{run_id}的.zpcr文件存到U盘')
        return dict(file=name)

    def _cmd_runs_delete(self, run_ids):
        self._need_admin()
        for rid in run_ids:
            r = self._need_run(rid)
            if r is self.run and self._running():
                raise ActionRejected('RUN_IN_PROGRESS')
        for rid in dict.fromkeys(run_ids):
            if self.runs[rid] is self.run:
                self.run = None
            del self.runs[rid]
        self._event('data', 'RUN_REPORTS_TRIMMED', f'Admin删除{len(set(run_ids))}个运行报告')
        return dict(deleted=len(set(run_ids)))

    def _cmd_logs_export(self):
        self._need_user()
        names = [f'Exports/{x}' for x in LOG_FILES]
        self.term['usb'] = sorted(set(self.term['usb']) | set(names))
        self._event('data', 'LOGS_EXPORTED', '系统日志导出到U盘Exports文件夹')
        return dict(files=names)

    def _cmd_logs_clear(self):
        self._need_admin()
        self.term['logs_cleared_s'] = self.t
        self.term['logs_mark'] = self.events_dropped + len(self.events)          # the messages logged so far are cleared
        return dict(cleared_s=self.t)

    def _cmd_diagnostics_run(self):
        self._need_admin()
        if self._running():
            raise ActionRejected('RUN_IN_PROGRESS')
        res = [dict(test=k, name=n, result='Fail' if (f is not None and f in self.faults) else 'Pass') for k, n, f in DIAGNOSTIC_TESTS]
        self.term['diagnostics'] = dict(time=self._clock(), results=res)
        self._event('data', 'DIAGNOSTICS', '自检：' + ('全部通过' if all(x['result'] == 'Pass' for x in res) else '有失败项'))
        return dict(results=copy.deepcopy(res))

    def _file_view(self, e):
        out = {k: e[k] for k in ('path', 'parent', 'name', 'kind', 'readonly', 'owner', 'created')}
        out['protocol'] = copy.deepcopy(e['protocol'])
        out['run'] = copy.deepcopy(e['run'])
        return out

    def _terminal_read(self):
        t = self.term
        return {'term.user': t['user'], 'term.users': [dict(name=n, kind=t['users'][n]['kind'], has_password=t['users'][n]['password'] is not None or
                                                            t['users'][n]['kind'] == 'service', email=t['users'][n]['email']) for n in t['order']],
                'term.settings': self._settings_view(), 'term.datetime': self._clock(), 'term.shutdown': t['shutdown'],
                'files.entries': [self._file_view(e) for _, e in sorted(t['files'].items())], 'usb.files': list(t['usb']),
                'run.setup': copy.deepcopy({k: v for k, v in t['setup'].items() if k != 'user'}) if t['setup'] else None,
                'protocol.definition': copy.deepcopy(self.protocol), 'protocol.path': t['protocol_path'], 'email.settings': self._email_view(),
                'diagnostics.last': copy.deepcopy(t['diagnostics']), 'logs.cleared_s': t['logs_cleared_s']}

    # ---------------------------------------------------------------- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What the touch-screen software sees: the agent slots plus whether the screen is live, the power-up count, the rocker position and the
        logged-in user's own folder, rights and email."""
        out = self.read('agent')
        user = self.term['user']
        out.update({'host.powered': bool(self.power and not self.term['shutdown']), 'host.power_epoch': self.power_epoch, 'host.switch_on': self.switch_on,
                    'host.powered_since': self.powered_since,
                    'host.my_files': self._my_files(), 'host.is_admin': user == 'Admin', 'host.is_guest': user == 'Guest',
                    'host.email': self.term['users'][user]['email'] if user in self.term['users'] else None,
                    'host.running': self._running(), 'host.status': self._status_text()})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel action calls the same command a direct call would (these commands check before they change anything).  A gesture made through
        a session acts as that session's caller: the session's guards apply to the command the panel calls (e.g. Run Reports export / recover /
        delete of another agent's run is refused) and runs started from the screen belong to the caller."""
        try:
            if self._caller is not None:
                BioradCfxOpusSession(self, self._caller)._guard(capability_id, arguments)
            return self.invoke(capability_id, **arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.t

    def _panel(self, gesture, *args, **kw):
        self._need_plant()
        try:
            return dict(accepted=bool(gesture(*args, **kw)))
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _cmd_panel_touch(self, display_id, x, y, duration_s=0.1, to_x=None, to_y=None):
        to = None if to_x is None or to_y is None else (int(to_x), int(to_y))
        return self._panel(self.panel.touch, display_id, int(x), int(y), float(duration_s), to=to)

    def _cmd_panel_switch(self, input_id, position, held=()):
        return self._panel(self.panel.switch, input_id, position, held=tuple(held))

    def _cmd_panel_capture(self, display_id):
        """Close-up camera of a display: a PNG file named by its content (large data is returned as a file reference)."""
        self._need_plant()
        data, w, h = self.panel.png_bytes(display_id)
        sha = hashlib.sha256(data).hexdigest()
        d = Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f'{display_id}_{sha[:16]}.png'
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.t, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))

    def _cmd_panel_frame(self):
        return dict(frame=self.panel.frame())

    def _status_text(self):
        """Instrument status on the Home screen (guide p.52)."""
        r = self.run
        if r is None:
            return 'Idle'
        return {'lid_heating': 'Running', 'running': 'Running', 'paused': 'Paused', 'power_failed': 'Power failure', 'completed': 'Complete',
                'stopped': 'Idle', 'error': 'Error'}.get(r['state'], 'Idle')

    def run_view(self):
        """Run In-Progress panel (guide p.121 figure): the step being executed, step k of n, the GOTO repeat, sample and lid temperatures, the
        remaining time and the protocol steps with the current one marked."""
        r = self.run
        if r is None:
            return dict(title='Run Status', remaining='', remaining_time='--:--:--', target='No run', step='', repeat='', sample='', lid='', steps=[])
        steps = r['protocol']['steps']
        running = self._running()
        k = r['k'] if r['k'] < len(steps) else len(steps) - 1
        s = steps[k]
        tgt = float(np.mean(r['target'])) - _K if r.get('target') is not None else None
        if r['state'] == 'lid_heating':
            target = f'Heating lid to {r["protocol"]["lid_temperature_k"] - _K:.0f} °C'
        elif not running:
            target = {'completed': 'Protocol Complete', 'stopped': 'Run stopped', 'error': 'Run error'}.get(r['state'], '')
        elif r['phase'] == 'hold' and r['hold_left'] is not None and tgt is not None:
            target = f'{tgt:.1f} °C for {_hms(r["hold_left"])}'
        elif r['phase'] == 'hold' and tgt is not None:
            target = f'{tgt:.1f} °C forever'
        elif tgt is not None:
            target = f'Ramping to {tgt:.1f} °C' if r['phase'] == 'ramp' else f'Reading at {tgt:.1f} °C'
        else:
            target = ''
        g = next((i for i in range(k + 1, len(steps)) if steps[i]['kind'] == 'goto'), None)
        loop = g if (g is not None and steps[g]['goto_step'] - 1 <= k) else None
        rep = f'Repeat: {r["loops"].get(loop, 0) + 1} of {steps[loop]["cycles"] + 1}' if (loop is not None and running) else ''
        st = self.read('agent')
        rem = st['run.remaining_s']
        current = (k if running and r['state'] != 'lid_heating' else None)
        return dict(title=('Run In-Progress: ' if running else 'Run: ') + r['protocol']['name'],
                    remaining='' if not running else ('Remaining: ' + ('--:--:--' if rem is None else _hms_long(rem))),
                    remaining_time='--:--:--' if (rem is None or not running) else _hms_long(rem),
                    target=target, step=f'Step: {k + 1} of {len(steps)}' if running else '', repeat=rep,
                    sample='' if st['sensor.sample_temperature_k'] is None else f'Sample: {st["sensor.sample_temperature_k"] - _K:.1f} °C',
                    lid='' if st['sensor.lid_temperature_k'] is None else f'Lid: {st["sensor.lid_temperature_k"] - _K:.1f} °C',
                    steps=[dict(n=i, text=f'{i + 1}  ' + _step_line(x), current=i == current) for i, x in enumerate(steps)])

    def curves_view(self, channel):
        """View Curves: baseline-subtracted amplification curves of the current or last run, wells of one detection channel (CFX Maestro style)."""
        r = self.run
        if r is None or not any(x['kind'] == 'amp' for x in r['reads']):
            return []
        key = (r['run_id'], len(r['reads']), channel)
        if key not in self._panel_cache:
            cur = [c for c in self._cmd_data_amplification(r['run_id'])['curves'] if c['channel'] == channel]
            self._panel_cache = {key: [[[float(a), float(b)] for a, b in zip(c['cycles'], c['baseline_subtracted'])] for c in cur]}
        return self._panel_cache[key]

    def file_view(self, path):
        """File details pane of the File Browser (guide p.101 figure, p.126): name, type, dates, scan mode, control mode, lid temperature, volume."""
        e = self.term['files'].get(path)
        if e is None:
            return ''
        lines = [e['name'], {'folder': 'Folder', 'protocol': 'Protocol File', 'run': 'Run File'}[e['kind']] + (' (read-only)' if e['readonly'] else ''),
                 'Created: ' + e['created'].replace('T', ' ')]
        p = e['protocol']
        if e['run'] is not None:
            lines += ['Completed: ' + e['run']['finished'].replace('T', ' '), 'Status: ' + e['run']['status'], 'Scan Mode: ' + e['run']['scan_mode'],
                      'Plate ID: ' + (e['run']['plate_id'] or '-')]
        if p is not None:
            lines += ['Method: ' + ('CALC' if p['sample_volume_m3'] > 0 else 'BLOCK'), f'Lid Temperature: {p["lid_temperature_k"] - _K:.0f} °C',
                      f'Reaction Volume: {p["sample_volume_m3"] * 1e9:.0f} µl', f'Steps: {len(p["steps"])}']
        return '\n'.join(lines)

    def runs_view(self):
        return [dict(run_id=r['run_id'], protocol=r['protocol']['name'], status=r['state'], started=r.get('started_local', ''), cycles=r['cycle'])
                for r in self.runs.values()]

    def logs_view(self, kind):
        """System Logs (guide p.56, p.157): Messages Log = run messages, warnings and errors; Usage Log = every event; both since the last clearing."""
        first = max(0, self.term['logs_mark'] - self.events_dropped)
        rows = [e for e in self.events[first:] if e['kind'] != 'fault' and (kind == 'usage' or e['kind'] in ('run', 'error', 'protocol'))]
        return [dict(time=_hms_long(e['time_s']), code=e['code']) for e in rows][::-1]

    def about_view(self):
        """About (guide p.55): software version, serial number, disk capacity and space left, run hours and cycles."""
        n = sum(1 for e in self.term['files'].values() if e['kind'] != 'folder') + len(self.runs)
        hours = sum(((r['finished_s'] if r['finished_s'] is not None else self.t) - r['started_s']) for r in self.runs.values()) / 3600.0
        return dict(version=SOFTWARE_VERSION, serial=SERIAL_NUMBER, name=self.term['settings']['name'], disk=f'{DISK_BYTES / 1e9:.1f} GB',
                    free=f'{(DISK_BYTES - n * FILE_BYTES) / 1e9:.2f} GB', hours=f'{hours:.1f} h', cycles=str(sum(r['cycle'] for r in self.runs.values())))

    # ---------------------------------------------------------------- invoke
    def invoke(self, capability_id, **a):
        if capability_id not in ALL_CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(ALL_CAPS[capability_id]['input_schema'], a)
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        if ALL_CAPS[capability_id]['kind'] not in ('query',) and capability_id != 'sim.step':
            self.revision += 1
        return out


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
ASYNC = {'run.start'}
_OWNED_PLATE = set()   # loading / removing plates are manipulation skills (physical): not owner-guarded (sweep 2026-10-19)
_RUN_QUERIES = {'data.amplification', 'data.melt', 'analysis.cq', 'analysis.standard_curve', 'analysis.melt_peaks', 'analysis.gene_expression', 'data.export'}
_RUN_RECORDS = {'runs.export': 'run_id', 'runs.recover': 'run_id', 'runs.delete': 'run_ids'}   # without a run_id runs.export exports the caller's runs
_TERMINAL_ONLY = {'user.login', 'user.logout', 'user.create', 'user.set_password', 'user.set_email', 'user.reset_password', 'user.remove', 'system.configure',
                  'email.configure', 'files.save_protocol', 'files.create_folder', 'files.copy', 'files.delete', 'files.rename', 'runs.export', 'runs.recover',
                  'logs.export', 'logs.clear'}                                  # touch-screen housekeeping that cannot affect another agent's run
# during another agent's run only the commands that stop or change that run or switch the instrument off softly are refused (ACCESS_DENIED); every
# other command follows the instrument's own rules (e.g. Run, protocol edits and diagnostics: RUN_IN_PROGRESS); panel gestures are checked per
# invoked command (panel_invoke); the rear power switch (power.set / power.press_switch) is a hard switch and plates are handled physically
_CONTROL_GUARDED = {'run.pause', 'run.resume', 'run.skip_step', 'run.stop', 'lid.open', 'lid.close', 'system.shutdown'}


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
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version=ASSET_VERSION, capabilities=caps, state_slots=slots)

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
                                or (capability_id == 'power.press_switch' and not h.switch_on)
                                or (capability_id == 'panel.switch' and arguments.get('position') == 'on')):
                return                                                          # switching ON an unpowered instrument (not OFF during an outage)
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _RUN_QUERIES:
            rid = arguments.get('run_id') or (h.run['run_id'] if h.run is not None else None)
            if rid is not None and self._foreign(rid):
                raise ActionRejected('ACCESS_DENIED')
        if capability_id in _RUN_RECORDS:                                       # Run Reports: another agent's run data stays with that agent
            arg = arguments.get(_RUN_RECORDS[capability_id])
            if any(self._foreign(r) for r in (arg if isinstance(arg, list) else [arg]) if r is not None):
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
            if not isinstance(capability_id, str) or capability_id not in ALL_CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and ALL_CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            validate(ALL_CAPS[capability_id]['input_schema'], arguments)
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
_RUN = ['run.state', 'run.id', 'run.step', 'run.cycle', 'run.reads', 'run.remaining_s', 'run.scan_mode', 'run.warnings', 'display.cycle', 'runs.count'] + _OPS \
    + ['files.entries', 'usb.files']                                          # the run file is written when a run ends (terminal layer)
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
ERROR_ZH.update({
    'LOGIN_REQUIRED': '触摸屏上没有登录的用户', 'LOGGED_IN': '已有用户登录（先注销回到登录列表）', 'ADMIN_REQUIRED': '只有Admin能做（以Admin登录）',
    'UNKNOWN_USER': '用户不存在', 'WRONG_PASSWORD': '密码错误（Service账户的密码只给维修人员）', 'PASSWORD_SETUP_REQUIRED': '已开启密码要求：该用户须在登录时设新密码（new_password）',
    'PASSWORD_REQUIRED': '已开启密码要求：不能跳过密码', 'INVALID_PASSWORD': '密码须为4–50个字母或数字（区分大小写）',
    'INVALID_NAME': '名称须为1–32个字符（运行文件名≤64），不含/\\:*?"<>|且首尾无空格', 'NAME_EXISTS': '同一位置已有同名文件或文件夹（用户名不区分大小写）',
    'GUEST_NOT_ALLOWED': 'Guest只能设置邮箱', 'INVALID_EMAIL': '邮箱地址无效（或开启认证却没有账户名）', 'BUILTIN_USER': '内置账户（Admin、Guest、Service）不能删除或在此重置密码',
    'INVALID_DATETIME': '日期或时间无效', 'NO_SUCH_FOLDER': '文件夹不存在', 'NO_SUCH_FILE': '文件或文件夹不存在',
    'FOLDER_NOT_ALLOWED': '该位置不属于当前用户（Guest只能用Public与USB）', 'READ_ONLY': '只读位置（Bio-Rad qPCR示例、根位置）', 'STORAGE_FULL': 'My Files已满（900个文件）',
    'RENAME_REQUIRED': '示例qPCR协议须改名后才能保存', 'INVALID_DESTINATION': '不能把文件夹复制到它自己里面',
    'NOT_A_PROTOCOL': '不是协议文件（运行文件与文件夹不能重命名；文件夹不能打开）', 'EMAIL_NOT_CONFIGURED': '当前用户没有设置邮箱，不能选邮件通知'})
_TERM = ['term.user', 'term.shutdown']
for _k in ('plant.configure', 'power.set', 'power.press_switch', 'fault.inject'):
    EFFECTS[_k] = EFFECTS[_k] + _TERM
EFFECTS['protocol.set'] = EFFECTS['protocol.set'] + ['protocol.definition', 'protocol.path']
EFFECTS['run.start'] = EFFECTS['run.start'] + ['run.setup']
EFFECTS.update({
    'user.login': ['term.user', 'term.users'], 'user.logout': ['term.user'], 'user.create': ['term.user', 'term.users', 'files.entries'],
    'user.set_password': ['term.users'], 'user.set_email': ['term.users'], 'user.reset_password': ['term.users'],
    'user.remove': ['term.users', 'files.entries', 'protocol.path'], 'system.configure': ['term.settings', 'term.datetime'],
    'system.shutdown': _PWR + ['term.shutdown'], 'email.configure': ['email.settings'], 'files.save_protocol': ['files.entries'],
    'files.create_folder': ['files.entries'], 'files.copy': ['files.entries', 'usb.files'], 'files.delete': ['files.entries', 'usb.files', 'protocol.path'],
    'files.rename': ['files.entries', 'protocol.path'], 'files.open': ['protocol.name', 'protocol.steps', 'protocol.definition', 'protocol.path'],
    'run.configure': ['run.setup'], 'run.stop': list(EFFECTS['run.skip_step']), 'runs.export': ['usb.files'], 'runs.recover': ['usb.files'],
    'runs.delete': _RUN + _LAMPS, 'logs.export': ['usb.files'], 'logs.clear': ['logs.cleared_s'], 'diagnostics.run': ['diagnostics.last']})
ON_STEP.update({'system.shutdown': _PHYS, 'run.stop': _PHYS_RUN, 'system.configure': ['term.datetime']})
ON_STEP['sim.step'] = ON_STEP['sim.step'] + ['term.datetime']
ON_STEP_ZH.update({'system.shutdown': '软关机后样品块与热盖回到室温', 'run.stop': '停止后样品块不再控温，回到室温；运行文件已保存',
                   'system.configure': '仪器时钟从新设定的本地时间继续走'})
_PANEL_USES = _panel_ui.spec_capabilities(PANEL_SPEC)
for _k in PANEL_GESTURES:
    EFFECTS[_k] = list(dict.fromkeys(x for c in _PANEL_USES for x in EFFECTS.get(c, [])))      # a gesture changes what the commands it calls change
    ON_STEP[_k] = list(dict.fromkeys(x for c in _PANEL_USES for x in ON_STEP.get(c, [])))
    ON_STEP_ZH[_k] = '同手势调用的能力（运行、盖子、电源等按其规则在之后的step中演化）'
_TERM_PRE = '通电且未软关机'
PRECONDITIONS.update({
    'user.login': (_TERM_PRE + '；没有已登录的用户；用户存在；有密码的用户须给对的密码（Service不能登录）；开启密码要求时无密码的用户须给new_password',
                   ['power.enabled', 'term.user', 'term.users', 'term.settings']),
    'user.logout': (_TERM_PRE + '；有已登录的用户', ['power.enabled', 'term.user']),
    'user.create': (_TERM_PRE + '；没有已登录的用户（登录列表）；名称合法且不重复；开启密码要求时须给密码', ['power.enabled', 'term.user', 'term.users', 'term.settings']),
    'user.set_password': (_TERM_PRE + '；已登录且不是Guest；已有密码时current_password须正确', ['power.enabled', 'term.user', 'term.users']),
    'user.set_email': (_TERM_PRE + '；已登录', ['power.enabled', 'term.user']),
    'user.reset_password': (_TERM_PRE + '；以Admin登录；目标为新建的用户', ['power.enabled', 'term.user', 'term.users']),
    'user.remove': (_TERM_PRE + '；以Admin登录；目标为新建的用户', ['power.enabled', 'term.user', 'term.users']),
    'system.configure': (_TERM_PRE + '；以Admin登录', ['power.enabled', 'term.user']),
    'system.shutdown': (_TERM_PRE + '；已注销（登录界面）；没有运行', ['power.enabled', 'term.user', 'run.state']),
    'email.configure': (_TERM_PRE + '；以Admin登录', ['power.enabled', 'term.user']),
    'files.save_protocol': (_TERM_PRE + '；已登录；目标文件夹可写且属于该用户；协议合法（同protocol.set）', ['power.enabled', 'term.user', 'files.entries']),
    'files.create_folder': (_TERM_PRE + '；已登录；父文件夹可写且属于该用户；不重名', ['power.enabled', 'term.user', 'files.entries']),
    'files.copy': (_TERM_PRE + '；已登录；源与目标都属于该用户、目标可写', ['power.enabled', 'term.user', 'files.entries']),
    'files.delete': (_TERM_PRE + '；已登录；不是只读或根位置', ['power.enabled', 'term.user', 'files.entries']),
    'files.rename': (_TERM_PRE + '；已登录；可写的协议文件', ['power.enabled', 'term.user', 'files.entries']),
    'files.open': (_TERM_PRE + '；已登录；协议或运行文件；没有运行', ['power.enabled', 'term.user', 'files.entries', 'run.state']),
    'run.configure': (_TERM_PRE + '；已登录；保存位置可写；邮件通知要求用户有邮箱', ['power.enabled', 'term.user', 'files.entries', 'term.users']),
    'run.stop': ('通电；有进行中的运行', ['power.enabled', 'run.state']),
    'runs.export': (_TERM_PRE + '；已登录；运行存在', ['power.enabled', 'term.user', 'runs.count']),
    'runs.recover': (_TERM_PRE + '；以Admin登录；运行存在', ['power.enabled', 'term.user', 'runs.count']),
    'runs.delete': (_TERM_PRE + '；以Admin登录；运行存在且不在进行中', ['power.enabled', 'term.user', 'runs.count', 'run.state']),
    'logs.export': (_TERM_PRE + '；已登录', ['power.enabled', 'term.user']), 'logs.clear': (_TERM_PRE + '；以Admin登录', ['power.enabled', 'term.user']),
    'diagnostics.run': (_TERM_PRE + '；以Admin登录；没有运行', ['power.enabled', 'term.user', 'run.state']),
    **{k: ('面板按当前页面与手册规则处理手势：断电或软关机时只有后面板电源开关起作用，开机自检与屏保时触摸只唤醒；未登录时只有登录列表；被调用能力的前提同样适用'
           '（被拒时显示仪器自己的提示框，手势不被接受）', ['power.enabled', 'term.user', 'run.state']) for k in PANEL_GESTURES},
    'panel.capture': ('已声明实验室条件（断电时为暗屏）', ['power.enabled']), 'panel.frame': ('无', [])})
_TP = _PW + ['LOGIN_REQUIRED']
CAP_ERRORS.update({
    'user.login': _PW + ['LOGGED_IN', 'UNKNOWN_USER', 'WRONG_PASSWORD', 'PASSWORD_SETUP_REQUIRED', 'INVALID_PASSWORD'], 'user.logout': _TP,
    'user.create': _PW + ['LOGGED_IN', 'INVALID_NAME', 'NAME_EXISTS', 'PASSWORD_REQUIRED', 'INVALID_PASSWORD'],
    'user.set_password': _TP + ['GUEST_NOT_ALLOWED', 'WRONG_PASSWORD', 'INVALID_PASSWORD'], 'user.set_email': _TP + ['INVALID_EMAIL'],
    'user.reset_password': _TP + ['ADMIN_REQUIRED', 'UNKNOWN_USER', 'BUILTIN_USER', 'INVALID_PASSWORD'],
    'user.remove': _TP + ['ADMIN_REQUIRED', 'UNKNOWN_USER', 'BUILTIN_USER'], 'system.configure': _TP + ['ADMIN_REQUIRED', 'INVALID_NAME', 'INVALID_DATETIME'],
    'system.shutdown': _PW + ['LOGGED_IN', 'RUN_IN_PROGRESS'], 'email.configure': _TP + ['ADMIN_REQUIRED', 'INVALID_EMAIL'],
    'files.save_protocol': _TP + ['INVALID_NAME', 'RENAME_REQUIRED', 'NO_SUCH_FOLDER', 'FOLDER_NOT_ALLOWED', 'READ_ONLY', 'NAME_EXISTS', 'STORAGE_FULL',
                                  'INVALID_SAMPLE_VOLUME', 'INVALID_STEP', 'INVALID_GRADIENT', 'INVALID_GOTO', 'INVALID_MELT'],
    'files.create_folder': _TP + ['INVALID_NAME', 'NO_SUCH_FOLDER', 'FOLDER_NOT_ALLOWED', 'READ_ONLY', 'NAME_EXISTS'],
    'files.copy': _TP + ['NO_SUCH_FILE', 'NO_SUCH_FOLDER', 'FOLDER_NOT_ALLOWED', 'READ_ONLY', 'INVALID_DESTINATION', 'NAME_EXISTS', 'STORAGE_FULL'],
    'files.delete': _TP + ['NO_SUCH_FILE', 'FOLDER_NOT_ALLOWED', 'READ_ONLY'],
    'files.rename': _TP + ['NO_SUCH_FILE', 'FOLDER_NOT_ALLOWED', 'NOT_A_PROTOCOL', 'READ_ONLY', 'INVALID_NAME', 'NAME_EXISTS'],
    'files.open': _TP + ['NO_SUCH_FILE', 'FOLDER_NOT_ALLOWED', 'NOT_A_PROTOCOL', 'RUN_IN_PROGRESS', 'INVALID_SAMPLE_VOLUME', 'INVALID_STEP', 'INVALID_GRADIENT',
                         'INVALID_GOTO', 'INVALID_MELT'],
    'run.configure': _TP + ['NO_SUCH_FOLDER', 'FOLDER_NOT_ALLOWED', 'READ_ONLY', 'INVALID_NAME', 'EMAIL_NOT_CONFIGURED'], 'run.stop': _PW + ['NOT_RUNNING'],
    'runs.export': _TP + ['UNKNOWN_RUN', 'NO_RUN_DATA'], 'runs.recover': _TP + ['ADMIN_REQUIRED', 'UNKNOWN_RUN'],
    'runs.delete': _TP + ['ADMIN_REQUIRED', 'UNKNOWN_RUN', 'RUN_IN_PROGRESS'], 'logs.export': _TP, 'logs.clear': _TP + ['ADMIN_REQUIRED'],
    'diagnostics.run': _TP + ['ADMIN_REQUIRED', 'RUN_IN_PROGRESS'],
    **{k: ['PLANT_REQUIRED'] for k in PANEL_GESTURES}, 'panel.capture': ['PLANT_REQUIRED'], 'panel.frame': []})
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
CAP_SOURCES.update({**{k: [_IG, _SIM] for k in TERMINAL_CAPS}, **{k: [_IG, _SIM] for k in PANEL_CAPS}})
RESOURCES.update({k: ['touch_screen'] for k in TERMINAL_CAPS})
RESOURCES.update({k: ['touch_screen', 'usb'] for k in ('files.copy', 'runs.export', 'runs.recover', 'logs.export')})
RESOURCES.update({'run.stop': ['sample_block', 'heated_lid'], 'system.shutdown': ['mains', 'touch_screen'], **{k: ['touch_screen'] for k in PANEL_CAPS}})
OBSERVES.update({'user.login': ['term.user'], 'user.logout': ['term.user'], 'user.create': ['term.user', 'term.users'], 'files.save_protocol': ['files.entries'],
                 'files.open': ['protocol.name', 'protocol.path'], 'run.configure': ['run.setup'], 'run.stop': ['run.state'], 'system.configure': ['term.settings'],
                 'diagnostics.run': ['diagnostics.last'], **{k: ['term.user', 'run.state', 'lid.state'] for k in PANEL_GESTURES}})
EXEC_TEXT.update({k: ('手势瞬时完成（不推进仿真时钟）：界面状态与被调用能力的效果立即生效；结果只说明是否被仪器接受，画面须用panel.capture查看',
                      '同步：被调用的能力被拒时其效果不发生，屏上显示仪器自己的提示框') for k in PANEL_GESTURES})
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
CAP_LIMITS.update({
    'user.login': ['账户为触摸屏软件的最小模型（无BR.io、网络文件夹与邮件发送）；Service为维修账户，密码只给维修人员（本模型不能登录）；开机后显示登录列表（自动注销）。'],
    'files.save_protocol': ['文件系统为最小模型：My Files（每个本地用户一个，Admin可访问全部）、Public（含只读的Bio-Rad qPCR示例协议，名称与步骤为假设）与前面板U盘'
                            '（本配置视为已插入）；网络文件夹与BR.io不可用（无网络连接）；文件大小与磁盘空间按每个文件60 kB估算。'],
    'run.configure': ['运行文件在运行结束（完成、停止或出错）时写入；提示音只记事件；邮件通知因本配置没有网络连接而不能发送（记EMAIL_NOT_SENT）。'],
    'system.configure': ['时区为部分Windows时区；仪器时钟从2026-01-01 00:00（本地，假设）随仿真时间走；屏保空闲时间可选1–60 min（假设）。'],
    'diagnostics.run': ['自检为最小模型：按注入的故障判Pass/Fail（风扇→块加热/冷却、传感器漂移→块温传感器、盖子受阻→盖子电机、通道LED失效→该通道），瞬时完成。'],
    **{k: ['面板层按panel_ui.json（指南页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟；手册未写之处的选择见panel_ui.json的assumptions。']
       for k in PANEL_CAPS}})
LIMITATIONS = [
    '选定配置：CFX Opus 96（12011319），固定96孔块、5色+FRET；CFX Maestro分析功能以运行层分析能力表示；BR.io云、网络（以太网/Wi-Fi）、邮件发送、Precision Melt'
    '与新染料校准不在配置内；触摸屏软件的用户账户、文件浏览器、Run Setup、系统设置、日志与自检在终端层按仪器指南建模（1.1.0）。',
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
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _OWNED_PLATE or k in _RUN_QUERIES or k in _RUN_RECORDS
                                  or ALL_CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    if k in _CONTROL_GUARDED:
        return '；另一个agent的运行进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）'
    if k in _OWNED_PLATE:
        return '；agent只能操作自己配的板（ACCESS_DENIED）'
    if k in _RUN_QUERIES:
        return '；agent只能读自己启动的运行数据（ACCESS_DENIED）'
    if k in _RUN_RECORDS:
        return ('；agent不能导出、恢复或删除另一agent的运行（ACCESS_DENIED）' + ('；不给run_id时只导出调用者自己的运行与无属主的运行' if k == 'runs.export' else ''))
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
    for k, v in ALL_CAPS.items():
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
