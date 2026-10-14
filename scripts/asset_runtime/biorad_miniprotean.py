"""Runtime of CHEM-164 instrument.biorad_miniprotean - Bio-Rad Mini-PROTEAN Tetra vertical electrophoresis cell with a PowerPac Basic power supply.

Process model (SI; host clock only - step(dt_s) is the only way time advances):
  - PowerPac Basic (manual 4006213): constant voltage 10-300 V or constant current 4-400 mA with the other parameter as a limit and automatic
    crossover, 75 W power limit (both LEDs lit), timer 1-999 min (remaining time in timed runs, elapsed time otherwise), run / pause / stop,
    display-mode, scroll and constant keys, 3-digit display of V / mA / min with the specified accuracy; the power switch position is kept apart
    from the mains (an outage keeps the supply dark until the mains returns); error codes E1 (no load < 4 mA; ends the run), E2 (over current) and
    E9 (sudden change of the load resistance) hold the run - run/pause resumes it, any other key clears the code and ends it (manual 4.2) -,
    E6 / E7 (power failure in a timed / untimed run), E5 (power failure with detection enabled, the timed run restarted and completed; shown until a
    key is pressed); power-failure detection is de-activated after each run;
  - Tetra cell circuit (manual 10007296): lid (current enters through it - removing it opens the circuit), leads to the supply (normal or reversed
    polarity), electrode assembly (back position, gels 1-2) and companion running module (front position, gels 3-4, electrodes in parallel through
    internal contacts - engineering), each module sealed only with a gel or the buffer dam on both faces; the inner (upper) chamber of a sealed module
    holds its buffer, an unsealed one drains into the outer chamber; the outer (lower) chamber must cover the gel bottoms (2-gel mark 550 mL, 4-gel mark
    680 mL); an unsealed companion module in a running tank is an open buffer path between its electrodes (the manual's 'excessive heat' warning);
  - gel conduction and the dye front (Kohlrausch moving boundary): per gel R = [R_L (1 - phi) + R_T phi] exp(-alpha (T_gel - 25 C)) + R_ch / c
    scaled by the gel geometry, phi the front position over the run length (stacking 4.4 mm + resolving gel), dphi = I_gel dt / Q_run; constants fitted
    to the manufacturer's current / time / temperature tables (bulletin 1658100 Tables 3.1, 3.2, 4.1; electrophoresis_model.json); running-buffer
    concentration c changes the trailing-ion and chamber resistances;
  - heat: buffer and tank heat balance with the Joule power and the room (fitted UA), gel temperature above the buffer by R_th P_gel solved inside
    each tick; outside the tables' range: the buffer boils at 100 C (excess heat evaporates the outer buffer), > 50 C buffer and > 70 C gel (heat
    damage, smeared bands) are flagged in the run record;
  - proteins (protein_library.json, UniProt): samples in Laemmli buffer (reducing or not, heated or not) or native buffer; in the stacking gel every
    protein rides with the front; in the resolving gel dx/dx_front = Y0(M) 10^(-K_R(M) T(x)) (Ferguson, fitted to the manufacturer migration charts,
    gradients by the local %T); interchain disulfides keep chains together without a reducing agent; bands leave the gel at the bottom (run off);
    reversed leads send anions (SDS proteins) back up into the upper buffer and let cations (basic native proteins) migrate in; band width from the
    stacked zone (overload), unrinsed wells, in-gel diffusion (Stokes-Einstein of the SDS complex, obstructed by the gel) and the smile of hot runs
    (the whole centre lane, front included, runs ahead); native PAGE: engineering mobility from the sequence charge at the run pH and the size
    (basic proteins do not enter the gel with normal leads), per-row electrical constants of Table 4.1;
  - outputs: supply display and state, dye-front distance and prestained marker bands visible through the cassette (gel.observe), run records, the
    separated gel handed to the bench (gel.remove); band truth and densitometry profile for the evaluator (gel.inspect); energy / charge / buffer /
    protein ledgers.
Generic schema / capability / session framework follows the CHEM-163 runtime.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.biorad_miniprotean"
CATALOG_ID = "CHEM-164"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch03/t4_measure/biorad_miniprotean'
PROTEIN_LIB_PATH = ASSET_DIR / 'protein_library.json'
GEL_LIB_PATH = ASSET_DIR / 'gel_buffer_library.json'
MODEL_PATH = ASSET_DIR / 'electrophoresis_model.json'
KB = 1.380649e-23
F_CONST = 96485.33212

# ---------------------------------------------------------------- manufacturer values
V_RANGE = (10.0, 300.0)                     # PowerPac Basic voltage set range, 1 V steps
I_RANGE_A = (0.004, 0.400)                  # current 4-400 mA, 1 mA steps
P_MAX_W = 75.0                              # maximum power
TIMER_MIN = (0, 999)                        # 0 = untimed
ACC_REL, ACC_V, ACC_I = 0.02, 3.0, 0.003    # accuracy +-2 % of reading or 3 V / 3 mA, whichever is more
NO_LOAD_A = 0.004                           # E1 below 4 mA
CELL_V_MAX, CELL_P_MAX = 600.0, 500.0       # Tetra cell limits
AMBIENT_MAX_K = 313.15                      # 40 C maximum ambient (cell and supply)
OUTER_2GEL_M3, OUTER_4GEL_M3 = 550e-6, 680e-6
STACK_LEN_M = 0.0044                        # well bottom to the resolving gel (chart geometry, derived)

# ---------------------------------------------------------------- engineering values
INNER_CAP_M3 = 150e-6                       # inner chamber of one module filled to just under the outer-plate edge (700 - 550 mL)
INNER_CONTACT_M3 = 100e-6                   # inner buffer needed to cover the wells / short-plate top
OUTER_CONTACT_M3 = 450e-6                   # outer buffer needed to cover the anode wire and the gel bottoms
OUTER_MAX_M3 = 1300e-6                      # above this the outer buffer spills over the short plates into the inner chambers
LEAK_M3_S = 5e-6                            # an unsealed inner chamber drains through the open face
R_CH0 = 150.0                               # chamber (buffer) path resistance of one gel at 1x running buffer (inside the fitted R_L)
R_BYPASS0 = 500.0                           # open companion module: its cathode faces its anode through ~6.5 cm of buffer, ~16 cm2 (1x ~0.08 S/m), 25 C
M_TANK_KG, CP_PC = 1.0, 1200.0
CP_W, RHO_W = 4180.0, 997.0
BOIL_K, LATENT_J_KG = 373.15, 2.257e6       # aqueous running buffer boils; heat beyond it evaporates water from the outer chamber
BUFFER_HOT_K = 323.15                       # record warning: buffer above 50 C (far outside the manufacturer tables, 25-35 C)
GEL_DAMAGE_K = 343.15                       # a gel above 70 C is heat damaged: bands smear (engineering)
SMEAR_M = 1.5e-3                            # extra band sigma of a heat-damaged gel
C_STACK_KG_M3 = 2.0                         # protein concentration of a stacked zone (sets the overload width)
SIGMA0_M = 0.25e-3                          # minimum band sigma from the well geometry / stacking
SMILE_K = 0.0022                            # smile fraction per (gel power density / 1 kW m^-2)^2: the standard 300 V TGX run (4-gel fill) smiles ~1 %
RH_SDS_NM = (1.3, 0.55)                     # SDS-complex hydrodynamic radius a (M / 10 kDa)^b nm
UNHEATED_BROADEN = 2.5                      # SDS sample not heated: incomplete denaturation broadens bands
UNRINSED_BROADEN = 1.2                      # wells not rinsed (storage buffer / unpolymerized acrylamide left in them): band distortion
NATIVE_MU_REF = 2.2e-8                      # front-ion effective mobility (m2/V/s) for native relative mobilities (engineering)
NATIVE_SIEVE = 0.6                          # native sieving relative to the SDS K_R at the same mass (engineering)
DYE_READ_SIGMA_M = 0.5e-3                   # reading the blue dye front through the cassette
TICK_S = 0.5                                # physics advances on a fixed 0.5 s grid of the host clock (exactly step-partition independent)
E9_RATIO = 3.0                              # load resistance jump that trips E9 within one step
PANEL_KEYS = ['display_mode', 'up', 'down', 'stop', 'run_pause', 'constant']


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- schema helpers (as CHEM-163)
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


BOOL = {'type': 'boolean'}
FREEOBJ = {'type': 'object'}
TEXT = {'type': 'string'}
INT = {'type': 'integer', 'x-unit': '1'}


def integer(lo, hi):
    return {'type': 'integer', 'x-unit': '1', 'minimum': lo, 'maximum': hi}


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
                if not isinstance(key, str):
                    raise ActionRejected('INVALID_ARGUMENT')
                validate(s['additionalProperties'], value)
            if len(v) > s.get('maxProperties', 10 ** 6):
                raise ActionRejected('INVALID_ARGUMENT')
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


# ---------------------------------------------------------------- template libraries and the fitted model
@functools.lru_cache(maxsize=1)
def protein_lib():
    return json.loads(PROTEIN_LIB_PATH.read_text())


@functools.lru_cache(maxsize=1)
def gel_lib():
    return json.loads(GEL_LIB_PATH.read_text())


@functools.lru_cache(maxsize=1)
def model():
    return json.loads(MODEL_PATH.read_text())


FERGUSON_RANGE_KDA = (10.0, 250.0)          # mass range of the calibration bands (Precision Plus)


def _poly(c, u):
    return sum(ci * u ** i for i, ci in enumerate(c)), sum(i * ci * u ** (i - 1) for i, ci in enumerate(c) if i)


def ferguson(M_kda):
    """(log10 Y0, K_R per %T) of an SDS-protein of apparent mass M (kDa), fitted to the manufacturer TGX migration charts (cubic in u = log10(M/50));
    above 250 kDa log10 Y0 is continued linearly and K_R does not decrease; below 10 kDa the coefficients of 10 kDa are used and sds_rate() moves the band toward the dye front."""
    f = model()['ferguson']
    u = math.log10(max(M_kda, 0.5) / 50.0)
    lo, hi = (math.log10(m / 50.0) for m in FERGUSON_RANGE_KDA)
    ue = min(max(u, lo), hi)
    (y, dy), (k, dk) = _poly(f['log10_Y0_poly'], ue), _poly(f['K_R_poly'], ue)
    d = max(0.0, u - hi)
    return y + dy * d, max(0.0, k + max(0.0, dk) * d)


PEPTIDE_FRONT_KDA = 2.0                     # SDS-peptides at or below this mass run with the Tris-glycine dye front (engineering)


def sds_rate(M_kda, T):
    """Band speed relative to the dye front: Ferguson 10^(log10 Y0 - K_R T) inside the calibration range; below 10 kDa its logarithm falls linearly in
    log M to 0 (the front) at 2 kDa - Tris-glycine SDS-PAGE does not resolve small peptides from the front."""
    y, k = ferguson(M_kda)
    lr = min(0.0, y - k * T)
    if M_kda < FERGUSON_RANGE_KDA[0]:
        w = max(0.0, math.log10(max(M_kda, PEPTIDE_FRONT_KDA) / PEPTIDE_FRONT_KDA) / math.log10(FERGUSON_RANGE_KDA[0] / PEPTIDE_FRONT_KDA))
        lr *= w
    return 10 ** lr


def gel_T(gel, x_frac):
    """Local %T at the fraction x_frac of the resolving gel (gradients follow the fitted cast profile)."""
    pt = gel['percent_T']
    if 'effective_uniform' in pt:
        return model()['ferguson']['any_kd_effective_T']
    shape = model()['ferguson']['gradient_shape']
    return pt['top'] + (pt['bottom'] - pt['top']) * max(0.0, min(1.0, x_frac)) ** shape


def rh_sds_m(M_kda):
    a, b = RH_SDS_NM
    return a * (max(M_kda, 1.0) / 10.0) ** b * 1e-9


def water_viscosity(T):
    """Dynamic viscosity of water (Pa s), Vogel form fitted to IAPWS values 0-100 C (engineering correlation)."""
    return 2.414e-5 * 10 ** (247.8 / (T - 140.0))


# ---------------------------------------------------------------- capabilities and state slots
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.-]+$')
LIBID = dict(type='string', minLength=1, maxLength=80)
POSITIONS = [1, 2, 3, 4]
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 273.15, 318.15), mains_voltage_v=number('V', 0.0, 264.0), noise_seed=integer(0, 2 ** 31 - 1)))
PSU_PROGRAM = obj(dict(constant=enum(['voltage', 'current']), voltage_v=number('V', *V_RANGE), current_a=number('A', *I_RANGE_A), time_s=number('s', 0.0, TIMER_MIN[1] * 60.0),
                       power_failure_detection=BOOL))
PSU_EXAMPLE = dict(constant='voltage', voltage_v=200.0, current_a=0.400, time_s=2100.0, power_failure_detection=False)
PSU_STATE = obj(dict(state=enum(['stopped', 'running', 'paused', 'error']), constant=enum(['voltage', 'current']), active_mode=nullable(enum(['CV', 'CC', 'CP'])),
                     display_mode=enum(['V', 'mA', 'min']), display_value=nullable(INT), error_code=nullable(TEXT), operation_id=nullable(TEXT)))
SAMPLE = obj(dict(sample_id=SID, proteins_kg=dict(type='object', additionalProperties=number('kg', 0.0, 2e-7), maxProperties=24), marker=nullable(enum(['precision_plus_unstained',
              'precision_plus_prestained', 'broad_range_unstained'])), marker_volume_m3=number('m3', 0.0, 2e-8), sample_buffer=enum(['laemmli_2x_reducing', 'laemmli_2x_nonreducing',
              'native_sample_buffer']), heat=enum(['none', '95C_5min', '70C_10min']), volume_m3=number('m3', 1e-9, 2e-7)))
SAMPLE_EXAMPLE = dict(sample_id='lysate_1', proteins_kg=dict(bsa=2e-9, ovalbumin=2e-9, lysozyme_hen=1e-9), marker=None, marker_volume_m3=0.0, sample_buffer='laemmli_2x_reducing',
                      heat='95C_5min', volume_m3=20e-9)
RECORD = obj(dict(record_id=TEXT, status=enum(['running', 'completed', 'stopped', 'error', 'power_failed']), started_s=number('s'), finished_s=nullable(number('s')),
                  settings=FREEOBJ, run_time_s=number('s'), max_voltage_v=number('V'), max_current_a=number('A'), max_power_w=number('W'), error_codes=array(TEXT, 0, 8),
                  gels=array(obj(dict(position=integer(1, 4), gel_id=TEXT)), 0, 4), warnings=array(TEXT, 0, 12)))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'psu', 'cell', 'gel', 'sample', 'alarm', 'fault']), code=TEXT, message_zh=TEXT))
OBSERVE = obj(dict(position=integer(1, 4), gel_id=TEXT, dye_front_m=nullable(number('m')), visible_bands=array(obj(dict(lane=integer(1, 15), label=TEXT, distance_m=number('m'))), 0, 200),
                   note_zh=TEXT))
_BAND = obj(dict(species=TEXT, apparent_molar_mass_kg_mol=number('kg/mol'), amount_kg=number('kg'), distance_m=nullable(number('m')), sigma_m=number('m'), state=TEXT,
                 template_source=TEXT))
_LANE = obj(dict(lane=integer(1, 15), sample_id=nullable(TEXT), bands=array(_BAND, 0, 64), profile=array(number('kg'), 0, 200)))
BANDS = obj(dict(gel_id=TEXT, gel_type=TEXT, lanes=array(_LANE, 0, 15), dye_front_m=nullable(number('m')), resolving_height_m=number('m'), smile_fraction=number('1'),
                 heat_damaged=BOOL, max_temperature_k=number('K')))
LEDGER_KEYS = ['output_energy_j', 'heat_to_room_j', 'heat_to_evaporation_j', 'charge_c', 'buffer_added_m3', 'buffer_drained_m3', 'buffer_evaporated_m3', 'protein_loaded_kg',
               'protein_in_gels_kg', 'protein_run_off_kg', 'protein_upper_buffer_kg', 'protein_spilled_kg', 'protein_on_bench_gels_kg']
LEDGER = obj({k: number('J' if k.endswith('_j') else 'C' if k.endswith('_c') else 'm3' if k.endswith('_m3') else 'kg') for k in LEDGER_KEYS})
UG = 1e-9                                   # kg per microgram (internal protein amounts are kept in ug, the interface is SI)
UL = 1e-9                                   # m3 per microlitre

cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['psu_base'], '宿主声明实验室条件：室温、电源电压、噪声种子', 'evaluator', 'simulation')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['switch_power', 'psu_case'], 'PowerPac主电源开/关（运行中关机按断电处理）')
cap('power.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['switch_power'], '按一下电源右侧开关（翻转开/关）')
cap('psu.program', PSU_PROGRAM, PSU_STATE, PSU_EXAMPLE, ['psu_front_panel', 'display_digits'],
    '设定电源：恒压或恒流、电压（10–300 V，1 V步进）与电流（0.004–0.400 A，1 mA步进）设定/限值、定时（0为不定时，60–59940 s即1–999 min整分钟）、断电检测（仅定时运行）；运行中只能改恒定参数与时间（厂家规则）')
cap('psu.press_key', obj(dict(key=enum(PANEL_KEYS))), PSU_STATE, dict(key='run_pause'),
    ['key_display_mode', 'key_up', 'key_down', 'key_stop', 'key_run_pause', 'key_constant', 'psu_front_panel', 'display_digits'],
    '按电源面板键：显示模式（V/mA/min）、上/下调所显示的参数、停止、运行/暂停、恒定参数V/A')
cap('psu.start', obj({}), obj(dict(operation_id=TEXT)), {}, ['key_run_pause', 'psu_output_jacks'],
    '按程序开始运行（等于在停止状态按运行/暂停键）；定时运行到时自动结束；E2/E9中断后等于按运行/暂停键恢复被中断的运行（返回原operation_id）')
cap('psu.stop', obj({}), PSU_STATE, {}, ['key_stop'], '停止运行（恒定参数与限值保留，计时清零）')
cap('lid.place', obj({}), obj(dict(lid_on=BOOL)), {}, ['lid', 'lid_jacks'], '把安全盖合到电泳槽上（盖上插孔对准电极模块插头，红右黑左）', 'agent', 'robot_skill')
cap('lid.remove', obj({}), obj(dict(lid_on=BOOL)), {}, ['lid'], '取下安全盖（先断开引线；运行中取盖即断路）', 'agent', 'robot_skill')
cap('leads.connect', obj(dict(polarity=enum(['normal', 'reversed']))), obj(dict(leads=TEXT)), dict(polarity='normal'), ['lid_lead_red', 'lid_lead_black', 'psu_output_jacks'],
    '把盖上的红黑引线插到电源输出插孔（normal：红对红；reversed：接反）', 'agent', 'robot_skill')
cap('leads.disconnect', obj({}), obj(dict(leads=TEXT)), {}, ['lid_lead_red', 'lid_lead_black'], '拔下引线', 'agent', 'robot_skill')
cap('companion.install', obj({}), obj(dict(companion=BOOL)), {}, ['tank_body'], '把陪伴模块放到槽内前位（运行第3、4块胶）', 'agent', 'robot_skill')
cap('companion.remove', obj({}), obj(dict(companion=BOOL)), {}, ['tank_body'], '取出陪伴模块（其胶位须为空）', 'agent', 'robot_skill')
cap('gel.provide', obj(dict(gel_id=SID, gel_type=LIBID)), obj(dict(gel_id=TEXT, location=TEXT)), dict(gel_id='gel_a', gel_type='tgx_4_20_10w_50ul'), ['electrode_assembly'],
    '在实验台上准备一块胶（胶模板库：TGX/TGX无染预制胶或手灌Tris-HCl胶；新胶带梳子与底部胶带）', 'agent', 'robot_skill')
cap('gel.prepare', obj(dict(gel_id=SID, remove_comb=BOOL, remove_tape=BOOL, rinse_wells=BOOL)), obj(dict(gel_id=TEXT, comb=BOOL, tape=BOOL, rinsed=BOOL)),
    dict(gel_id='gel_a', remove_comb=True, remove_tape=True, rinse_wells=True), ['electrode_assembly'],
    '上样前处理：拔梳子、撕掉底部胶带（在实验台上）、用电泳缓冲液冲洗孔（未冲洗的孔残留保存液/未聚合丙烯酰胺，条带变形展宽）；装在胶位上的胶只能在取盖且无运行时拔梳/冲洗', 'agent', 'robot_skill')
cap('gel.install', obj(dict(item=SID, position=integer(1, 4))), obj(dict(position=INT, item=TEXT)), dict(item='gel_a', position=1), ['electrode_assembly', 'ea_clamp_arms', 'ea_gaskets'],
    '把胶（短玻板朝内）或缓冲坝（item="buffer_dam"）装到胶位1–4并锁紧夹臂（3、4号位需陪伴模块；盖已取下、无运行）', 'agent', 'robot_skill')
cap('gel.remove', obj(dict(position=integer(1, 4))), obj(dict(position=INT, item=nullable(TEXT))), dict(position=1), ['electrode_assembly', 'ea_clamp_arms'],
    '从胶位取下胶或缓冲坝放到实验台（先倒掉内槽缓冲液；分离后的胶交给后续染色/成像）', 'agent', 'robot_skill')
cap('buffer.fill', obj(dict(chamber=enum(['inner', 'outer']), buffer_id=LIBID, volume_m3=number('m3', 1e-6, 1.3e-3), temperature_k=number('K', 273.15, 313.15))),
    obj(dict(chamber=TEXT, volume_m3=number('m3'))), dict(chamber='inner', buffer_id='tris_glycine_sds_1x', volume_m3=150e-6, temperature_k=295.15), ['tank_body', 'electrode_assembly'],
    '加电泳缓冲液：内槽（模块之间，加到外玻板上沿下方，每个模块约150 mL）或外槽（2胶刻度550 mL、4胶刻度680 mL）；可用预冷缓冲液', 'agent', 'robot_skill')
cap('buffer.drain', obj({}), obj(dict(drained_m3=number('m3'))), {}, ['tank_body'], '倒掉全部缓冲液（取胶前；盖已取下、无运行）', 'agent', 'robot_skill')
cap('sample.prepare', SAMPLE, obj(dict(sample_id=TEXT, volume_m3=number('m3'), proteins=INT)), SAMPLE_EXAMPLE, ['electrode_assembly'],
    '配样品：蛋白（蛋白模板库，每种质量kg，典型1–10 µg即1e-9–1e-8 kg）、可选分子量标准、上样缓冲液（还原/非还原Laemmli或天然）、加热（95 °C 5 min / 70 °C 10 min / 不加热）', 'agent', 'robot_skill')
cap('sample.load', obj(dict(sample_id=SID, position=integer(1, 4), lane=integer(1, 15), volume_m3=number('m3', 0.5e-9, 2e-7))), obj(dict(position=INT, lane=INT, loaded_m3=number('m3'), spilled_m3=number('m3'))),
    dict(sample_id='lysate_1', position=1, lane=2, volume_m3=10e-9), ['electrode_assembly'], '用移液器把样品加到胶位上某一泳道的孔中（超出孔容量的部分溢到相邻孔）', 'agent', 'robot_skill')
cap('gel.observe', obj(dict(position=integer(1, 4))), OBSERVE, dict(position=1), ['electrode_assembly', 'tank_body'],
    '透过卡夹看胶：溴酚蓝前沿离孔底的距离与预染标准条带的位置（未染色条带不可见；读数±0.5 mm）', 'agent', 'query')
cap('measurement.result', obj(dict(record_id=TEXT)), RECORD, dict(record_id='EP0001'), ['psu_front_panel'], '读一次运行记录（设置、运行时间、最大电压/电流/功率读数、错误码、所用胶）', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, 200))), {}, ['psu_front_panel'], '列出运行记录', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, 400), dropped=INT)), dict(since_s=0.0), ['psu_base'], '读事件日志', 'agent', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=LEDGER)), {}, ['psu_base'], '读守恒账目（输出电能、散热、电量、缓冲液、蛋白去向）', 'evaluator', 'query')
cap('gel.inspect', obj(dict(gel_id=SID)), BANDS, dict(gel_id='gel_a'), ['electrode_assembly'], '评测者读胶内真值：各泳道条带（物种、表观分子量、量、位置、宽度、状态）与密度曲线', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(path=TEXT, sha256=TEXT, count=INT)), 3, 3))), {}, ['psu_base'], '模板库与标定模型的路径和sha256', 'agent', 'query')
cap('fault.inject', obj(dict(fault=enum(['power_loss', 'lead_break', 'inner_leak', 'short_circuit']), active=BOOL)), obj(dict(faults=array(TEXT, 0, 8))),
    dict(fault='inner_leak', active=True), ['psu_base', 'electrode_assembly'], '评测者故障注入：断电、引线断路、内槽密封泄漏、短路', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['psu_base'], '推进宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'blot.transfer': 'Mini Trans-Blot转印模块不在本配置中（湿转需另配模块与冷却）',
    'nucleic_acid.run': 'TBE/TBE-尿素核酸胶的迁移未建模（只建蛋白SDS-PAGE与天然PAGE）',
    'two_d.first_dimension': '2-D第一向（IPG条）模块未建',
    'electroelution.run': '电洗脱模块未建',
    'gel.cast': '手灌胶的灌制与聚合（灌胶架）是台面准备步骤，不在电泳槽内；手灌胶作为模板库胶型直接提供',
    'gel.image': '染色与成像由凝胶成像仪完成（如CHEM-165），本仪器不成像',
    'hardware.connect': '没有真实PowerPac与电泳槽的硬件接口（PowerPac Basic无数据接口）',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['psu_base'], _why, 'agent', 'device_command')

SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


slot('clock.time_s', number('s'), 's', 'derived', 'psu_base', '仿真时钟（物理按0.5 s整格推进）')
slot('plant.configured', BOOL, '1', 'setpoint', 'psu_base', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'switch_power', 'PowerPac主电源')
slot('psu.state', enum(['stopped', 'running', 'paused', 'error']), '1', 'derived', 'psu_front_panel', '电源运行状态')
slot('psu.constant', enum(['voltage', 'current']), '1', 'setpoint', 'key_constant', '恒定参数（恒压/恒流）')
slot('psu.active_mode', nullable(enum(['CV', 'CC', 'CP'])), '1', 'derived', 'psu_front_panel', '运行中的实际调节模式（CV恒压、CC恒流、CP功率限制：两个LED同亮）')
slot('psu.voltage_set_v', number('V'), 'V', 'setpoint', 'psu_front_panel', '电压设定值（恒压时为目标，恒流时为限值）')
slot('psu.current_set_a', number('A'), 'A', 'setpoint', 'psu_front_panel', '电流设定值（恒流时为目标，恒压时为限值；1 mA步进）')
slot('psu.time_set_s', number('s'), 's', 'setpoint', 'psu_front_panel', '定时（0为不定时；整分钟，面板以min显示）')
slot('display.mode', enum(['V', 'mA', 'min']), '1', 'setpoint', 'key_display_mode', '显示模式：电压、电流或时间')
slot('display.value', nullable(INT), '1', 'sensor', 'display_digits', '3位LED读数（按显示模式为V、mA或min；运行中为实测读数，含厂家精度）', reference='单位随display.mode：V、mA或min')
slot('display.error_code', nullable(TEXT), '1', 'sensor', 'display_digits', '显示的错误码（E1、E2、E5、E6、E7、E9）')
slot('psu.timer_s', number('s'), 's', 'derived', 'psu_front_panel', '定时运行的剩余时间或不定时运行的已运行时间（面板以min显示）')
slot('psu.output_voltage_v', number('V'), 'V', 'process_truth', 'psu_output_jacks', '输出电压真值', 'evaluator')
slot('psu.output_current_a', number('A'), 'A', 'process_truth', 'psu_output_jacks', '输出电流真值', 'evaluator')
slot('lid.on', BOOL, '1', 'setpoint', 'lid', '安全盖合在槽上')
slot('lid.z_m', number('m'), 'm', 'derived', 'lid', '盖的场景位置（0为合上，0.12 m为已取下/抬起；场景关节镜像）', reference='资产坐标+Z方向的平移')
slot('leads.state', enum(['disconnected', 'normal', 'reversed']), '1', 'setpoint', 'lid_lead_red', '引线连接状态（正常：红对红；reversed：接反）')
slot('companion.installed', BOOL, '1', 'setpoint', 'tank_body', '陪伴模块在前位')
for _p in POSITIONS:
    slot(f'position_{_p}.item', nullable(TEXT), '1', 'setpoint', 'electrode_assembly' if _p <= 2 else 'tank_body', f'胶位{_p}上的胶ID或buffer_dam')
slot('buffer.inner_m3', number('m3'), 'm3', 'derived', 'electrode_assembly', '内槽（上槽）缓冲液体积（按加液记账；泄漏时减少）', reference='液体实际体积（室温）')
slot('buffer.outer_m3', number('m3'), 'm3', 'derived', 'tank_body', '外槽（下槽）缓冲液体积（按加液记账）', reference='液体实际体积（室温）')
slot('buffer.id', nullable(TEXT), '1', 'setpoint', 'tank_body', '槽内电泳缓冲液（模板库ID）')
slot('gel.bench_ids', array(TEXT, 0, 20), '1', 'derived', 'electrode_assembly', '实验台上的胶')
slot('sample.bench_ids', array(TEXT, 0, 40), '1', 'derived', 'electrode_assembly', '实验台上的样品')
slot('operation.kind', nullable(TEXT), '1', 'derived', 'psu_front_panel', '进行中的操作（run）')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'psu_front_panel', '进行中的运行属于另一个agent')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'psu_front_panel', '最近结束的运行记录')
slot('truth.buffer_temperature_k', number('K'), 'K', 'process_truth', 'tank_body', '槽内缓冲液温度', 'evaluator')
slot('truth.gel_temperature_k', number('K'), 'K', 'process_truth', 'electrode_assembly', '胶温度（最热一块）', 'evaluator')
slot('truth.load_resistance_ohm', nullable(number('ohm')), 'ohm', 'process_truth', 'electrode_assembly', '电泳槽负载电阻（断路时null）', 'evaluator')
slot('truth.dye_front_fraction', array(number('1'), 4, 4), '1', 'process_truth', 'electrode_assembly', '胶位1–4的染料前沿在运行长度上的分数（胶两侧边缘泳道；微笑时中央泳道更靠前；无胶为0）', 'evaluator')
slot('truth.faults', array(TEXT, 0, 8), '1', 'process_truth', 'psu_base', '注入的故障', 'evaluator')
MAX_RECORDS, MAX_EVENTS, MAX_ITEMS = 200, 400, 40


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


class BioradMiniproteanRuntime:
    """Mini-PROTEAN Tetra cell + PowerPac Basic: power supply, cell circuit, buffers, gels, samples, electrophoresis, heat (SI units)."""

    def __init__(self, instance_id='electrophoresis_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_phys = 0.0                          # host time up to which the physics has been integrated (a multiple of TICK_S)
        self.revision = 0
        self.plant = None
        self.power = False
        self.switch_on = False                     # position of the power switch (the supply is powered when it is on and the mains is present)
        self.psu = dict(state='stopped', constant='voltage', v_set=200.0, i_set=0.400, time_min=0, pfd=False, display='V', error=None, active=None,
                        run_s=0.0, pending_restart=None, hold=None)
        self.V = 0.0
        self.I = 0.0
        self.R_last = None
        self.lid = True
        self.leads = 'normal'
        self.companion = False
        self.positions = {p: None for p in POSITIONS}
        self.inner = {'electrode': 0.0, 'companion': 0.0}
        self.outer = 0.0
        self.buffer_id = None
        self.Tb = None
        self.gels = {}
        self.samples = {}
        self.records = {}
        self.seq = dict(ep=0, op=0)
        self.op = None
        self.operations = {}
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self._bias = None
        self._principals, self._requests, self._owners = {}, {}, {}
        self._caller = None                        # principal of the command being executed (owner of a run it starts)

    # ---------------------------------------------------------------- basics
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
        return BioradMiniproteanSession(self, name)

    def _event(self, kind, code, message, rid=None, truth=False):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message, _rid=rid, _truth=truth))

    def _rng(self, tag):
        h = hashlib.sha256(f'{self.plant["noise_seed"] if self.plant else 0}:{tag}'.encode()).digest()
        return np.random.default_rng(int.from_bytes(h[:8], 'little'))

    def _room(self):
        return self.plant['room_k'] if self.plant else 295.15

    @property
    def running(self):
        return self.psu['state'] == 'running'

    def _module(self, p):
        return 'electrode' if p <= 2 else 'companion'

    def _sealed(self, mod):
        ps = (1, 2) if mod == 'electrode' else (3, 4)
        if mod == 'companion' and not self.companion:
            return False
        return all(self.positions[p] is not None for p in ps) and not (mod == 'electrode' and 'inner_leak' in self.faults)

    def _buffer(self):
        return gel_lib()['running_buffers'].get(self.buffer_id) if self.buffer_id else None

    # ---------------------------------------------------------------- display
    def _reading(self, value, kind):
        """Displayed value with the specified accuracy (+-2 % of reading or 3 units, whichever is more): a fixed per-instrument bias inside the band."""
        if self._bias is None:
            return value
        b = self._bias['V' if kind == 'V' else 'I']
        err = max(ACC_REL * abs(value), ACC_V if kind == 'V' else ACC_I) * b
        return value + err

    def _display(self):
        p = self.psu
        if not self.power or p['error']:                                    # dark, or the error code is shown instead of digits
            return None
        mode = p['display']
        if p['state'] in ('running', 'paused'):
            if mode == 'V':
                return int(round(self._reading(self.V, 'V'))) if p['state'] == 'running' else int(round(p['v_set']))
            if mode == 'mA':
                return int(round(self._reading(self.I, 'I') * 1e3)) if p['state'] == 'running' else int(round(p['i_set'] * 1e3))
            if p['time_min'] > 0:
                return int(max(0.0, math.ceil(p['time_min'] - p['run_s'] / 60.0 - 1e-9)))
            return int(p['run_s'] // 60) % 1000
        if mode == 'V':
            return int(round(p['v_set']))
        if mode == 'mA':
            return int(round(p['i_set'] * 1e3))
        return int(p['time_min'])

    def _active_mode(self):
        return self.psu['active'] if self.psu['state'] == 'running' and self.power else None

    def _psu_state(self):
        p = self.psu
        return dict(state=p['state'], constant=p['constant'], active_mode=self._active_mode(), display_mode=p['display'],
                    display_value=self._display(), error_code=p['error'] if self.power else None, operation_id=self.op['op'] if self.op else None)

    def read(self, visibility='agent'):
        p = self.psu
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'psu.state': p['state'], 'psu.constant': p['constant'],
            'psu.active_mode': self._active_mode(), 'psu.voltage_set_v': p['v_set'], 'psu.current_set_a': p['i_set'],
            'psu.time_set_s': float(p['time_min'] * 60), 'display.mode': p['display'], 'display.value': self._display(),
            'display.error_code': p['error'] if self.power else None,
            'psu.timer_s': (max(0.0, p['time_min'] * 60.0 - p['run_s']) if p['time_min'] > 0 else p['run_s']) if (p['state'] in ('running', 'paused') or p['hold']) else 0.0,
            'psu.output_voltage_v': self.V, 'psu.output_current_a': self.I, 'lid.on': self.lid, 'lid.z_m': 0.0 if self.lid else 0.12, 'leads.state': self.leads,
            'companion.installed': self.companion, **{f'position_{q}.item': self.positions[q] for q in POSITIONS},
            'buffer.inner_m3': self.inner['electrode'] + self.inner['companion'], 'buffer.outer_m3': self.outer, 'buffer.id': self.buffer_id,
            'gel.bench_ids': sorted(k for k, g in self.gels.items() if g['location'] == 'bench'), 'sample.bench_ids': sorted(self.samples),
            'operation.kind': 'run' if self.op else None, 'operation.started_by_other_agent': False, 'result.last_record_id': self._last_record(),
            'truth.buffer_temperature_k': self.Tb if self.Tb is not None else self._room(),
            'truth.gel_temperature_k': max([g.get('T', self._room()) for g in self._installed_gels()] or [self.Tb if self.Tb is not None else self._room()]),
            'truth.load_resistance_ohm': self.R_last, 'truth.dye_front_fraction': [self._front_frac(q) for q in POSITIONS], 'truth.faults': sorted(self.faults),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _front_frac(self, q):
        it = self.positions[q]
        if it is None or it == 'buffer_dam':
            return 0.0
        g = self.gels[it]
        return float(min(1.0, g['s'] / g['L_run']))

    def _installed_gels(self):
        return [self.gels[it] for it in self.positions.values() if it not in (None, 'buffer_dam')]

    def _last_record(self, keep=None):
        done = [k for k, r in self.records.items() if r['status'] != 'running' and (keep is None or keep(k))]
        return max(done, key=lambda k: (self.records[k]['finished_s'] or 0.0, k), default=None)

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        props = CAPS[capability_id]['input_schema'].get('properties', {})
        a = {k: (int(v) if props.get(k, {}).get('type') == 'integer' and v is not None else v) for k, v in a.items()}
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):
            self.revision += 1
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_not_running(self):
        self._need_plant()
        if self.psu['state'] in ('running', 'paused') or self.psu['hold']:     # an E2 / E9 hold keeps the run open (fix the fault, then resume or clear)
            raise ActionRejected('RUN_IN_PROGRESS')

    def _need_lid_off(self):
        if self.lid:
            raise ActionRejected('LID_ON')

    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed):
        if self.plant is not None and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, noise_seed=noise_seed)
        if self._bias is None:
            r = self._rng('psu_bias')
            self._bias = dict(V=float(r.uniform(-0.6, 0.6)), I=float(r.uniform(-0.6, 0.6)))
        if self.Tb is None:
            self.Tb = room_k
        for g in self.gels.values():                                         # gels on the bench follow the room
            if g['location'] == 'bench':
                g['T'] = room_k
        if self.power and not self._mains_ok():
            self._power_off('mains')
        elif self.switch_on and not self.power and self._mains_ok():            # the mains is back with the switch left on: the supply powers up
            self._power_up()
        return dict(plant=dict(self.plant))

    def _mains_ok(self):
        v = self.plant['mains_voltage_v']
        return 'power_loss' not in self.faults and (90.0 <= v <= 132.0 or 198.0 <= v <= 264.0)     # PowerPac Basic input: 90-132 or 198-264 VAC

    def _power_off(self, cause):
        """The supply loses power (the operator sees the same dark panel): with power-failure detection a timed run is held for an automatic restart
        (E5 at its end) whatever the cause (manual step 14); otherwise the open run fails - E7 when the switch was turned off before stop or the run
        was untimed, E6 when the mains failed during a timed run (manual 4.2); an E2 / E9 hold ends the same way."""
        p = self.psu

        def failed_code():
            return 'E6' if (p['time_min'] > 0 and cause == 'mains') else 'E7'
        if p['hold'] and self.op:                                             # a held run cannot restart: it ends as a power failure
            rec = self.records[self.op['record_id']]
            self._warn(rec, 'power_failure_during_run')
            p['pending_restart'] = failed_code()
            self._end_run('power_failed', p['pending_restart'])
        elif p['state'] in ('running', 'paused'):
            rec = self.records[self.op['record_id']]
            if p['time_min'] > 0 and p['pfd']:
                p['pending_restart'] = 'E5'                                     # power-failure detection: the timed run resumes on power-up
                self._warn(rec, 'power_failure_during_run')
            else:
                p['pending_restart'] = failed_code()
                self._end_run('power_failed', p['pending_restart'])
        self.power = False
        self.V = self.I = 0.0
        self._event('power', 'POWER_OFF', 'PowerPac断电（面板熄灭）')

    def _power_up(self):
        self.power = True
        p = self.psu
        pend, p['pending_restart'] = p['pending_restart'], None
        if pend == 'E5' and p['state'] in ('running', 'paused'):
            p['error'] = None
            self._event('psu', 'PFD_RESUME', '断电检测：恢复供电后继续定时运行')
        elif pend in ('E6', 'E7'):
            p['error'] = pend
            p['state'] = 'error'
            self._event('alarm', pend, '断电：运行未完成（E6定时/E7不定时或未按停止键关机）')
        else:
            p['state'], p['error'] = 'stopped', None
        self._event('power', 'POWER_ON', 'PowerPac通电（默认恒压显示）')

    def _set_power(self, enabled):
        """Turn the power switch on or off; with the mains absent (outage or out of range) the supply cannot be switched on."""
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
        return dict(enabled=self.power)

    def _cmd_power_set(self, enabled):
        return self._set_power(enabled)

    def _cmd_power_press_switch(self):
        return self._set_power(not self.switch_on)

    @staticmethod
    def _warn(rec, w):
        if w not in rec['warnings'] and len(rec['warnings']) < 12:
            rec['warnings'].append(w)

    def _need_power(self):
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _clear_error(self):
        """Any key other than run/pause clears a displayed error code; an E2 / E9 hold ends its interrupted run (manual 4.2)."""
        p = self.psu
        if p['state'] == 'error':
            if p['hold'] and self.op:
                self._end_run('error', p['hold'])
            p['state'], p['error'], p['hold'] = 'stopped', None, None

    def _resume_hold(self):
        p = self.psu
        p['state'], p['error'], p['hold'] = 'running', None, None
        self._event('psu', 'RESUME', '按运行/暂停键：恢复被E2/E9中断的运行（剩余时间不变）', rid=self.op['record_id'])
        return self.op['op']

    def _cmd_psu_program(self, constant, voltage_v, current_a, time_s, power_failure_detection):
        self._need_plant()
        self._need_power()
        p = self.psu
        current_ma = current_a * 1e3
        time_min = time_s / 60.0
        if abs(voltage_v - round(voltage_v)) > 1e-9 or abs(current_ma - round(current_ma)) > 1e-6 or abs(time_min - round(time_min)) > 1e-9:
            raise ActionRejected('E10_UNACCEPTABLE_VALUE')          # the panel sets 1 V, 1 mA and 1 min steps
        time_min = int(round(time_min))
        if p['state'] in ('running', 'paused'):                             # during a run: constant parameter value and time only (limits and the constant in pause)
            if p['state'] == 'running' and (constant != p['constant'] or (constant == 'voltage' and abs(current_ma / 1e3 - p['i_set']) > 1e-12)
                                            or (constant == 'current' and abs(voltage_v - p['v_set']) > 1e-12)):
                raise ActionRejected('LIMIT_EDIT_NEEDS_PAUSE')
            if (time_min > 0) != (p['time_min'] > 0) or power_failure_detection != p['pfd']:
                raise ActionRejected('TIMED_MODE_CHANGE_NEEDS_STOP')
        self._clear_error()                                                   # programming presses keys: a displayed code (and an E2 / E9 hold) is cleared first
        p.update(constant=constant, v_set=float(round(voltage_v)), i_set=round(current_ma) / 1e3, time_min=int(time_min), pfd=bool(power_failure_detection and time_min > 0))
        return self._psu_state()

    def _cmd_psu_press_key(self, key):
        self._need_plant()
        self._need_power()
        p = self.psu
        if p['state'] == 'error':                                             # an error code on the display: run/pause resumes an E2 / E9 run
            if key == 'run_pause' and p['hold'] and self.op:
                self._resume_hold()
            elif key == 'run_pause':
                self._clear_error()
                self._start_run()
            else:
                self._clear_error()                                           # any other key only clears the code
            return self._psu_state()
        if key == 'display_mode':
            p['display'] = dict(V='mA', mA='min', min='V')[p['display']]
        elif key in ('up', 'down'):
            d = 1 if key == 'up' else -1
            if p['display'] == 'V':
                if p['state'] == 'running' and p['constant'] != 'voltage':
                    raise ActionRejected('LIMIT_EDIT_NEEDS_PAUSE')
                p['v_set'] = float(min(V_RANGE[1], max(V_RANGE[0], p['v_set'] + d)))
            elif p['display'] == 'mA':
                if p['state'] == 'running' and p['constant'] != 'current':
                    raise ActionRejected('LIMIT_EDIT_NEEDS_PAUSE')
                p['i_set'] = min(I_RANGE_A[1], max(I_RANGE_A[0], round(p['i_set'] * 1e3 + d) / 1e3))
            else:
                if p['state'] in ('running', 'paused') and p['time_min'] == 0:
                    raise ActionRejected('TIMED_MODE_CHANGE_NEEDS_STOP')
                p['time_min'] = int(min(TIMER_MIN[1], max(1 if p['state'] in ('running', 'paused') else 0, p['time_min'] + d)))
        elif key == 'constant':
            if p['state'] == 'running':
                raise ActionRejected('LIMIT_EDIT_NEEDS_PAUSE')
            p['constant'] = 'current' if p['constant'] == 'voltage' else 'voltage'
        elif key == 'stop':
            if p['state'] in ('running', 'paused'):
                self._end_run('stopped', None)
        elif key == 'run_pause':
            if p['state'] == 'running':
                p['state'] = 'paused'
                self.V = self.I = 0.0
                self._event('psu', 'PAUSE', '暂停（输出断开，可修改参数）')
            elif p['state'] == 'paused':
                p['state'] = 'running'
                self._event('psu', 'RESUME', '继续运行')
            else:
                self._start_run()
        return self._psu_state()

    def _cmd_psu_start(self):
        self._need_plant()
        self._need_power()
        p = self.psu
        if p['state'] in ('running', 'paused'):
            raise ActionRejected('RUN_IN_PROGRESS')
        if p['hold'] and self.op:                                             # E2 / E9: the start resumes the interrupted run
            return dict(operation_id=self._resume_hold())
        self._clear_error()
        return dict(operation_id=self._start_run())

    def _cmd_psu_stop(self):
        self._need_plant()
        self._need_power()
        if self.psu['state'] in ('running', 'paused'):
            self._end_run('stopped', None)
        self._clear_error()
        return self._psu_state()

    def _start_run(self):
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        p = self.psu
        self.seq['ep'] += 1
        self.seq['op'] += 1
        rid, oid = f'EP{self.seq["ep"]:04d}', f'op{self.seq["op"]:04d}'
        self.records[rid] = dict(record_id=rid, status='running', started_s=self.t, finished_s=None,
                                 settings=dict(constant=p['constant'], voltage_v=p['v_set'], current_a=p['i_set'], time_s=float(p['time_min'] * 60), power_failure_detection=p['pfd'],
                                               buffer_id=self.buffer_id, outer_buffer_m3=self.outer, inner_buffer_m3=sum(self.inner.values()), companion=self.companion),
                                 run_time_s=0.0, max_voltage_v=0.0, max_current_a=0.0, max_power_w=0.0, error_codes=[],
                                 gels=[dict(position=q, gel_id=it) for q, it in self.positions.items() if it not in (None, 'buffer_dam')], warnings=[])
        self.operations[oid] = dict(op=oid, phase='running', record_id=rid)
        if self._caller is not None:                                          # the caller owns the run from the start (also when it ends at once with E1)
            self._owners[oid] = self._owners[rid] = self._caller
        self.op = dict(op=oid, record_id=rid)
        p['state'], p['run_s'], p['error'], p['hold'] = 'running', 0.0, None, None
        self._event('psu', 'RUN', f'开始运行{rid}', rid=rid)
        for g in self._installed_gels():
            g['runs'].append(rid)
        R = self._load_resistance()
        self.R_last = R
        V_try = p['v_set'] if p['constant'] == 'voltage' else min(p['v_set'], p['i_set'] * (R or 1e12))
        if R is None or V_try / R < NO_LOAD_A:                               # no load detected at the start: the run ends at once with E1
            self._end_run('error', 'E1', display='E1')
            self._event('alarm', 'E1', '无负载（电流<4 mA）：检查连接、盖与缓冲液', rid=rid)
        return oid

    def _interrupt(self, code, message):
        """E2 / E9 during a run: the output is switched off and the code shown; the run stays open (run/pause resumes it, any other key ends it)."""
        p = self.psu
        rec = self.records[self.op['record_id']]
        if code not in rec['error_codes']:
            rec['error_codes'].append(code)
        p['state'], p['error'], p['hold'], p['active'] = 'error', code, code, None
        self.V = self.I = 0.0
        self._event('alarm', code, message, rid=rec['record_id'])

    def _end_run(self, status, code, display=None):
        p = self.psu
        rec = self.records[self.op['record_id']]
        rec['status'] = status
        rec['finished_s'] = self.t
        if code and code not in rec['error_codes']:
            rec['error_codes'].append(code)
        self.operations[self.op['op']]['phase'] = {'completed': 'completed', 'stopped': 'cancelled'}.get(status, 'failed')
        self._event('psu', 'END', f'运行{rec["record_id"]}结束（{status}{"，" + code if code else ""}）', rid=rec['record_id'])
        self.op = None
        p['state'] = 'error' if display else 'stopped'
        p['error'], p['hold'] = display, None
        p['run_s'] = 0.0
        p['active'] = None
        p['pfd'] = False                                                      # power-failure detection is de-activated after each run (manual step 7)
        self.V = self.I = 0.0

    def _cancel_op(self, op):
        if self.op is None or self.operations.get(self.op['op']) is not op:
            raise ActionRejected('NOT_RUNNING')
        self._end_run('stopped', None)

    def _cmd_lid_place(self):
        self._need_plant()
        if self.lid:
            raise ActionRejected('LID_ON')
        self.lid = True
        self._event('cell', 'LID_ON', '盖合上（插孔对准插头）')
        return dict(lid_on=True)

    def _cmd_lid_remove(self):
        self._need_plant()
        if not self.lid:
            raise ActionRejected('LID_OFF')
        if self.leads != 'disconnected':
            raise ActionRejected('LEADS_CONNECTED')
        self.lid = False
        self._event('cell', 'LID_OFF', '取下盖（电路断开）')
        return dict(lid_on=False)

    def _cmd_leads_connect(self, polarity):
        self._need_plant()
        if not self.lid:
            raise ActionRejected('LID_OFF')
        if self.leads != 'disconnected':
            raise ActionRejected('LEADS_CONNECTED')
        self.leads = polarity
        self._event('cell', 'LEADS', '引线插到电源输出（' + ('红对红' if polarity == 'normal' else '接反') + '）')
        return dict(leads=polarity)

    def _cmd_leads_disconnect(self):
        self._need_plant()
        if self.leads == 'disconnected':
            raise ActionRejected('LEADS_DISCONNECTED')
        self.leads = 'disconnected'
        self._event('cell', 'LEADS_OFF', '拔下引线')
        return dict(leads='disconnected')

    def _cmd_companion_install(self):
        self._need_not_running()
        self._need_lid_off()
        if self.companion:
            raise ActionRejected('COMPANION_INSTALLED')
        self.companion = True
        self._event('cell', 'COMPANION_IN', '陪伴模块放入前位')
        return dict(companion=True)

    def _cmd_companion_remove(self):
        self._need_not_running()
        self._need_lid_off()
        if not self.companion:
            raise ActionRejected('COMPANION_ABSENT')
        if self.positions[3] is not None or self.positions[4] is not None:
            raise ActionRejected('POSITION_OCCUPIED')
        self.outer += self.inner['companion']
        self.inner['companion'] = 0.0
        self.companion = False
        return dict(companion=False)

    def _cmd_gel_provide(self, gel_id, gel_type):
        self._need_plant()
        if gel_id in self.gels or gel_id == 'buffer_dam' or gel_id in self.samples:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.gels) >= MAX_ITEMS:
            raise ActionRejected('TOO_MANY_ITEMS')
        gl = gel_lib()['gels'].get(gel_type)
        if gl is None:
            raise ActionRejected('UNKNOWN_GEL_TYPE')
        L_run = STACK_LEN_M + gl['resolving_height_m']
        thick, width = gl['thickness_m'], gl['width_m']
        # precast TGX: the manufacturer's current / time tables hold for every Mini-PROTEAN TGX format (the deeper 50 uL wells leave a shorter
        # resolving gel but the same cassette), so resistance and front charge are those of the fitted standard gel; hand-cast gels scale with their
        # resistive length, thickness and width, the %T resistance factor and the charge factor calibrated to the Tetra manual 35-45 min at 200 V
        lfac = 1.0 if gl['precast'] else L_run / (STACK_LEN_M + 0.062)
        self.gels[gel_id] = dict(gel_id=gel_id, gel_type=gel_type, lib=gl, location='bench', comb=True, tape=gl['precast'], rinsed=False, s=0.0, L_run=L_run,
                                 geo=(1.0e-3 / thick) * (0.086 / width) * lfac, rfac=gl.get('resistance_factor', 1.0),
                                 qfac=gl['run_charge_factor'] * (thick / 1.0e-3) * (width / 0.086) * lfac,
                                 lanes={n: [] for n in range(1, gl['wells'] + 1)}, well_ul={n: 0.0 for n in range(1, gl['wells'] + 1)},
                                 T=self._room(), T_max=self._room(), damaged=False, runs=[], smile=0.0, template_source=f'gel_buffer_library.json#{gel_type}')
        self._event('gel', 'GEL_READY', f'胶{gel_id}（{gel_type}）放在实验台上')
        return dict(gel_id=gel_id, location='bench')

    def _cmd_gel_prepare(self, gel_id, remove_comb, remove_tape, rinse_wells):
        self._need_plant()
        g = self.gels.get(gel_id)
        if g is None:
            raise ActionRejected('UNKNOWN_ITEM')
        if g['location'] != 'bench':
            if remove_tape:
                raise ActionRejected('GEL_NOT_ON_BENCH')
            if remove_comb or rinse_wells:                                      # an installed gel: only with the lid off and no run
                self._need_not_running()
                self._need_lid_off()
        if rinse_wells and any(b['state'] == 'well' for bands in g['lanes'].values() for b in bands):
            raise ActionRejected('SAMPLES_LOADED')                               # rinsing would wash the loaded samples out of the wells
        if remove_comb:
            g['comb'] = False
        if remove_tape:
            g['tape'] = False
        if rinse_wells and not g['comb']:
            g['rinsed'] = True
        return dict(gel_id=gel_id, comb=g['comb'], tape=g['tape'], rinsed=g['rinsed'])

    def _cmd_gel_install(self, item, position):
        self._need_not_running()
        self._need_lid_off()
        if position >= 3 and not self.companion:
            raise ActionRejected('COMPANION_ABSENT')
        if self.positions[position] is not None:
            raise ActionRejected('POSITION_OCCUPIED')
        if item != 'buffer_dam':
            g = self.gels.get(item)
            if g is None:
                raise ActionRejected('UNKNOWN_ITEM')
            if g['location'] != 'bench':
                raise ActionRejected('GEL_NOT_ON_BENCH')
            g['location'] = f'position_{position}'
            if self.outer + sum(self.inner.values()) > 1e-9:                   # immersed in the buffer: the gel takes its temperature
                g['T'] = self.Tb if self.Tb is not None else self._room()
        elif 'buffer_dam' in self.positions.values():
            raise ActionRejected('BUFFER_DAM_IN_USE')
        self.positions[position] = item
        self._event('gel', 'INSTALLED', f'胶位{position}装上{item}')
        return dict(position=position, item=item)

    def _cmd_gel_remove(self, position):
        self._need_not_running()
        self._need_lid_off()
        it = self.positions[position]
        if it is None:
            raise ActionRejected('POSITION_EMPTY')
        mod = self._module(position)
        if self.inner[mod] > 1e-9:                                          # the manual: pour off the buffer before opening the arms
            raise ActionRejected('INNER_BUFFER_PRESENT')
        self.positions[position] = None
        if it != 'buffer_dam':
            self.gels[it]['location'] = 'bench'
            self.gels[it]['T'] = self._room()
        self._event('gel', 'REMOVED', f'胶位{position}取下{it}')
        return dict(position=position, item=it)

    def _gel_protein(self, g):
        return sum(b['ug'] for bands in g['lanes'].values() for b in bands if b['state'] in ('well', 'stacked', 'resolving'))

    def _cmd_buffer_fill(self, chamber, buffer_id, volume_m3, temperature_k):
        self._need_plant()
        buf = gel_lib()['running_buffers'].get(buffer_id)
        if buf is None:
            raise ActionRejected('UNKNOWN_BUFFER')
        if self.lid:
            raise ActionRejected('LID_ON')
        if self.buffer_id is not None and buffer_id != self.buffer_id and (self.outer + sum(self.inner.values())) > 1e-9:
            raise ActionRejected('BUFFER_MIX')
        V = float(volume_m3)
        T0 = self.Tb if self.Tb is not None else self._room()
        C0 = self._heat_capacity()                                            # buffer already in the tank + the tank itself (one lumped node)
        if chamber == 'inner':
            left = V
            for mod in ('electrode', 'companion'):
                if mod == 'companion' and not self.companion:
                    continue
                take = min(left, INNER_CAP_M3 - self.inner[mod])
                self.inner[mod] += take
                left -= take
            self.outer += left                                                   # overfilled inner chambers run over into the outer chamber
        else:
            self.outer += V
        spill = max(0.0, self.outer - OUTER_MAX_M3)
        self.outer -= spill
        self.buffer_id = buffer_id
        Cadd = (V - spill) * RHO_W * CP_W
        self.Tb = (C0 * T0 + Cadd * temperature_k) / (C0 + Cadd)
        for g in self._installed_gels():                                     # installed gels take the buffer temperature
            g['T'] = self.Tb
        self.led['buffer_added_m3'].add(V - spill)
        if spill > 0:
            self._event('cell', 'SPILL', f'缓冲液过多，溢出{spill * 1e6:.0f} mL')
        return dict(chamber=chamber, volume_m3=V - spill)

    def _cmd_buffer_drain(self):
        self._need_not_running()
        self._need_lid_off()
        V = self.outer + sum(self.inner.values())
        self.outer = 0.0
        self.inner = {'electrode': 0.0, 'companion': 0.0}
        self.buffer_id = None
        self.led['buffer_drained_m3'].add(V)
        return dict(drained_m3=V)

    def _cmd_sample_prepare(self, sample_id, proteins_kg, marker, marker_volume_m3, sample_buffer, heat, volume_m3):
        proteins_ug = {k: v / UG for k, v in proteins_kg.items()}
        marker_ul, volume_ul = marker_volume_m3 / UL, volume_m3 / UL
        self._need_plant()
        if sample_id in self.samples or sample_id in self.gels:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.samples) >= MAX_ITEMS:
            raise ActionRejected('TOO_MANY_ITEMS')
        lib = protein_lib()['substances']
        if any(k not in lib or lib[k]['kind'] != 'protein' for k in proteins_ug):
            raise ActionRejected('UNKNOWN_PROTEIN')
        if marker_ul > volume_ul:
            raise ActionRejected('INVALID_ARGUMENT')
        comp = {k: float(v) for k, v in proteins_ug.items() if v > 0}
        if marker:
            mix = 'precision_plus_unstained' if marker == 'precision_plus_prestained' else marker
            for k, frac in protein_lib()['mixes'][mix]['components'].items():
                comp[k] = comp.get(k, 0.0) + 0.075 * marker_ul * frac * (3.0 if k in ('precision_plus_50kd', 'precision_plus_25kd') else 1.0)   # ~0.75 ug / 10 uL per band
        sb = gel_lib()['sample_buffers'][sample_buffer]
        self.samples[sample_id] = dict(sample_id=sample_id, ug=comp, volume_ul=float(volume_ul), sds=sb['sds'], reducing=sb['reducing'], heated=heat != 'none',
                                       prestained=marker == 'precision_plus_prestained', marker=marker)
        self._event('sample', 'SAMPLE_READY', f'样品{sample_id}：{len(comp)}种蛋白，{volume_ul:.0f} µL')
        return dict(sample_id=sample_id, volume_m3=float(volume_ul) * UL, proteins=len(comp))

    def _species_of(self, s, pid, ug):
        """Bands a protein forms: without a reducing agent, chains joined by disulfides stay one species (mass sum); with it every chain runs alone."""
        e = protein_lib()['substances'][pid]
        if e['kind'] == 'marker_band':
            return [(pid, e['sds_apparent_mw_kda'], ug)]
        if len(e['chains']) > 1 and (s['reducing'] or e['interchain_disulfides'] == 0):
            tot = sum(c['mass_da'] for c in e['chains'])
            return [(f'{pid}:{c["name"]}', c['mass_da'] / 1000.0 * (e['sds_apparent_mw_kda'] * 1000.0 / e['mass_da']), ug * c['mass_da'] / tot) for c in e['chains']]
        return [(pid, e['sds_apparent_mw_kda'], ug)]

    def _cmd_sample_load(self, sample_id, position, lane, volume_m3):
        volume_ul = volume_m3 / UL
        self._need_not_running()
        self._need_lid_off()
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_ITEM')
        it = self.positions[position]
        if it in (None, 'buffer_dam'):
            raise ActionRejected('NO_GEL_AT_POSITION')
        g = self.gels[it]
        if g['comb']:
            raise ActionRejected('COMB_IN_PLACE')
        if lane > g['lib']['wells']:
            raise ActionRejected('NO_SUCH_LANE')
        if volume_ul > s['volume_ul'] + 1e-9:
            raise ActionRejected('SAMPLE_INSUFFICIENT')
        cap_ul = g['lib']['well_volume_m3'] * 1e9
        room = max(0.0, cap_ul - g['well_ul'][lane])
        into = min(volume_ul, room)
        spilled = volume_ul - into
        frac_of_sample = volume_ul / s['volume_ul']
        # an overfilled well runs over both walls: half toward each neighbour, as far as that well has room; over the gel edge and beyond a full
        # neighbour the sample is lost into the inner buffer
        give = {n: min(0.5 * spilled, max(0.0, cap_ul - g['well_ul'][n])) for n in (lane - 1, lane + 1) if 1 <= n <= g['lib']['wells']}
        lost = spilled - sum(give.values())
        for pid, ug in s['ug'].items():
            m = ug * frac_of_sample
            self.led['protein_loaded_kg'].add(m * UG)
            for sp, kda, part in self._species_of(s, pid, m):
                self._add_band(g, lane, sp, kda, part * into / volume_ul, s)
                for n, v in give.items():                                        # cross-lane contamination
                    self._add_band(g, n, sp, kda, part * v / volume_ul, s)
                self.led['protein_spilled_kg'].add(part * lost / volume_ul * UG)
        g['well_ul'][lane] += into
        for n, v in give.items():
            g['well_ul'][n] += v
        s['ug'] = {k: v * (1 - frac_of_sample) for k, v in s['ug'].items()}
        s['volume_ul'] -= volume_ul
        if s['volume_ul'] <= 1e-9:
            del self.samples[sample_id]
        if spilled > 0:
            self._event('sample', 'WELL_OVERFLOW', f'孔{lane}超出容量{cap_ul:.0f} µL，{spilled:.1f} µL溢到相邻孔')
        return dict(position=position, lane=lane, loaded_m3=into * UL, spilled_m3=spilled * UL)

    def _add_band(self, g, lane, sp, kda, ug, s):
        if ug <= 0:
            return
        for b in g['lanes'][lane]:
            if b['species'] == sp and b['state'] == 'well':
                b['ug'] += ug
                return
        g['lanes'][lane].append(dict(species=sp, kda=float(kda), ug=float(ug), x=None, sig2=SIGMA0_M ** 2, state='well', sds=s['sds'], heated=s['heated'],
                                     prestained=s['prestained'] and sp.startswith('precision_plus_'), native_charge=self._native_charge(sp),
                                     template_source=f'protein_library.json#{sp.split(":")[0]}'))

    def _native_charge(self, sp):
        e = protein_lib()['substances'].get(sp.split(':')[0])
        if e is None or e.get('net_charge') is None:
            return None
        return e['net_charge']['pH_8.8']

    # ---------------------------------------------------------------- observation and records
    def _cmd_gel_observe(self, position):
        self._need_plant()
        it = self.positions[position]
        if it in (None, 'buffer_dam'):
            raise ActionRejected('NO_GEL_AT_POSITION')
        g = self.gels[it]
        # reading error of the same gel state is the same (the operator reads the same front / bands again): seeded by the gel and its migration state
        rng = self._rng(f'observe:{it}:{len(g["runs"])}:{round(g["s"] * 1e7)}:{round(g["smile"] * 1e6)}')
        front = self._front_m(g)
        front = None if front is None else float(front + rng.normal(0, DYE_READ_SIGMA_M))
        vis = []
        for lane, bands in g['lanes'].items():
            for b in bands:
                if b['prestained'] and b['state'] == 'resolving':
                    d = STACK_LEN_M + b['x'] * self._lane_factor(g, lane) + rng.normal(0, DYE_READ_SIGMA_M)
                    vis.append(dict(lane=lane, label=f'{b["kda"]:g} kD', distance_m=float(max(0.0, d))))
        return dict(position=position, gel_id=it, dye_front_m=None if front is None else max(0.0, front), visible_bands=vis,
                    note_zh='距离从孔底量起；溴酚蓝前沿（全胶宽平均，微笑时中央更靠前）与预染标准可见，未染色蛋白不可见（需染色/成像）')

    def _lane_factor(self, g, lane):
        n = g['lib']['wells']
        u = 0.0 if n == 1 else 2 * (lane - 0.5) / n - 1                          # -1 .. 1 across the gel
        return 1.0 + g['smile'] * (1 - u * u)

    def _front_m(self, g):
        """Dye-front distance from the well bottoms averaged across the gel width: the resolving-gel part of the front smiles like the bands
        (mean of 1 + s (1 - u^2) over the width = 1 + 2 s / 3); None before the run, at most the run length."""
        if g['s'] <= 0:
            return None
        s = min(g['s'], g['L_run'])
        if s <= STACK_LEN_M:
            return s
        return min(g['L_run'], STACK_LEN_M + (s - STACK_LEN_M) * (1 + 2 * g['smile'] / 3))

    def _cmd_measurement_result(self, record_id):
        r = self.records.get(record_id)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return {k: copy.deepcopy(v) for k, v in r.items() if not k.startswith('_')}

    def _cmd_records_list(self):
        return dict(records=sorted(self.records))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if not k.startswith('_')} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def ledger(self):
        led = {k: v.value() for k, v in self.led.items()}
        led['protein_in_gels_kg'] = sum(self._gel_protein(g) for g in self.gels.values() if g['location'] != 'bench') * UG
        led['protein_on_bench_gels_kg'] = sum(self._gel_protein(g) for g in self.gels.values() if g['location'] == 'bench') * UG   # a state, not a sum
        return led

    def _cmd_ledger_read(self):
        return dict(ledger=self.ledger())

    def _cmd_gel_inspect(self, gel_id):
        g = self.gels.get(gel_id)
        if g is None:
            raise ActionRejected('UNKNOWN_ITEM')
        res = g['lib']['resolving_height_m']
        grid = np.linspace(0.0, res, 200)
        lanes = []
        for lane, bands in g['lanes'].items():
            prof = np.zeros_like(grid)
            out = []
            for b in bands:
                sig = math.sqrt(b['sig2'])
                dist = None if b['state'] != 'resolving' else STACK_LEN_M + b['x'] * self._lane_factor(g, lane)
                if b['state'] == 'resolving':                                   # a band straddling the gel end keeps its whole mass inside the gel
                    xc = b['x'] * self._lane_factor(g, lane)
                    w = np.exp(-0.5 * ((grid - xc) / sig) ** 2)
                    prof += b['ug'] * UG * w / w.sum() if w.sum() > 0 else 0.0
                out.append(dict(species=b['species'], apparent_molar_mass_kg_mol=b['kda'], amount_kg=b['ug'] * UG, distance_m=dist, sigma_m=sig, state=b['state'],
                                template_source=b['template_source']))
            lanes.append(dict(lane=lane, sample_id=None, bands=out, profile=[float(v) for v in prof]))
        return dict(gel_id=gel_id, gel_type=g['gel_type'], lanes=lanes, dye_front_m=self._front_m(g), resolving_height_m=res, smile_fraction=g['smile'],
                    heat_damaged=bool(g['damaged']), max_temperature_k=float(g['T_max']))

    def _cmd_provenance_read(self):
        return dict(libraries=[dict(path=str(p.relative_to(ROOT)), sha256=hashlib.sha256(p.read_bytes()).hexdigest(), count=n) for p, n in
                               ((PROTEIN_LIB_PATH, len(protein_lib()['substances'])), (GEL_LIB_PATH, len(gel_lib()['gels'])), (MODEL_PATH, model()['ferguson']['n_bands']))])

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        if fault == 'power_loss':
            if active and self.power:
                self._power_off('mains')                                          # the agent sees the dark panel, not the cause
            elif not active and self.switch_on and not self.power and self._mains_ok():
                self._power_up()                                                  # mains back with the switch on
        self._event('fault', 'FAULT', f'{fault} {"on" if active else "off"}')
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    # ---------------------------------------------------------------- physics
    def _elec(self, g, native):
        """Fitted electrical constants: SDS-PAGE one set (Tables 3.1 / 3.2 hold for every TGX gel); native PAGE per Table 4.1 row (7.5-12 % and
        hand-cast gels / 4-15 %, 4-20 % and Any kD)."""
        m = model()
        if not native:
            return m['electrical_sds']
        pt = g['lib']['percent_T']
        gradient = 'effective_uniform' in pt or pt.get('top') != pt.get('bottom')
        return m['electrical_native_gradient'] if gradient and g['lib']['precast'] else m['electrical_native']

    def _gel_R(self, g, Tg, native):
        e = self._elec(g, native)
        buf = self._buffer()
        c = buf['conductivity_factor'] if buf else 1e-6
        phi = min(1.0, g['s'] / g['L_run'])
        RL = e['R_lead_ohm'] - R_CH0
        RT = e['R_trail_ohm'] - R_CH0
        body = (RL * (1 - phi) + RT / c * phi) * g['rfac']
        return g['geo'] * (body + R_CH0 / c) * math.exp(-e['alpha_per_K'] * (Tg - 298.15))

    def _paths(self):
        """Conducting paths of the cell: [(kind, gel or None, module)]; None when the circuit is open."""
        if not self.lid or self.leads == 'disconnected' or 'lead_break' in self.faults or self.buffer_id is None:
            return None
        if self.outer < OUTER_CONTACT_M3:
            return None
        paths = []
        for mod in ('electrode', 'companion'):
            if mod == 'companion' and not self.companion:
                continue
            ps = (1, 2) if mod == 'electrode' else (3, 4)
            if self._sealed(mod):
                if self.inner[mod] < INNER_CONTACT_M3:
                    continue
                for q in ps:
                    it = self.positions[q]
                    if it in (None, 'buffer_dam'):
                        continue
                    g = self.gels[it]
                    if g['tape'] or g['comb']:
                        continue                                                 # a taped cassette bottom or a comb left in blocks the gel
                    paths.append(('gel', g, mod))
            elif mod == 'companion':
                paths.append(('bypass', None, mod))                              # open companion module: buffer path between its electrodes
        return paths

    def _load_resistance(self):
        paths = self._paths()
        if 'short_circuit' in self.faults:
            return 1.0
        if not paths:
            return None
        buf = self._buffer()
        native = buf['system'] == 'native_tris_glycine'
        G = 0.0
        Tb = self.Tb if self.Tb is not None else self._room()
        for kind, g, mod in paths:
            if kind == 'gel':
                G += 1.0 / self._gel_R(g, g.get('T', Tb), native)
            else:
                G += buf['conductivity_factor'] * math.exp(0.02 * (Tb - 298.15)) / R_BYPASS0
        return 1.0 / G if G > 0 else None

    def _regulate(self, R):
        """PowerPac output for a load R: constant voltage or current with the other as limit, and the 75 W limit (both LEDs)."""
        p = self.psu
        if R is None:
            return 0.0, 0.0, None
        if p['constant'] == 'voltage':
            V, I, mode = p['v_set'], p['v_set'] / R, 'CV'
            if I > p['i_set']:
                I, V, mode = p['i_set'], p['i_set'] * R, 'CC'
        else:
            I, V, mode = p['i_set'], p['i_set'] * R, 'CC'
            if V > p['v_set']:
                V, I, mode = p['v_set'], p['v_set'] / R, 'CV'
        if V * I > P_MAX_W:
            V, I, mode = math.sqrt(P_MAX_W * R), math.sqrt(P_MAX_W / R), 'CP'
        return V, I, mode

    def _gel_T_solve(self, g, V, Tb, native):
        """Gel temperature at the output voltage V: T = T_b + R_th V^2 / R_g(T) (the quasi-steady gel of the calibration), Newton from T_b; without a
        stable fixed point (thermal runaway) or above it the gel is at the boiling point."""
        rth = model()['electrical_sds']['R_th_K_per_W']
        a = self._elec(g, native)['alpha_per_K']
        c = rth * V * V / self._gel_R(g, 298.15, native)
        T = Tb
        for _ in range(40):
            ex = math.exp(a * (T - 298.15))
            f = Tb + c * ex - T
            d = a * c * ex - 1.0
            if d >= 0.0:
                return BOIL_K
            dT = f / d
            T -= dT
            if abs(dT) < 1e-10 or T >= BOIL_K:
                break
        return min(T, BOIL_K)

    def _operating_point(self, paths, Tb, native):
        """Supply output with every driven gel at its self-consistent temperature inside the tick (in CC / CP the voltage depends on the load:
        damped iteration)."""
        gels = [g for kind, g, mod in paths if kind == 'gel']
        R = self._load_resistance()
        V, I, mode = self._regulate(R)
        for _ in range(60):
            for g in gels:
                g['T'] = self._gel_T_solve(g, V, Tb, native)
            R = self._load_resistance()
            V1, I, mode = self._regulate(R)
            if abs(V1 - V) <= 1e-9 * max(1.0, V):
                break
            V = 0.5 * (V + V1)
        V, I, mode = self._regulate(R)
        return R, V, I, mode

    def step(self, dt_s):
        """Advance the host clock; the physics runs in whole ticks of TICK_S on the absolute time grid (events and records carry the tick time), so any
        split of the same interval gives the same result (a command acts from the next tick on).  Ticks with the supply running go through
        _advance; the rest of the interval (no field) is one exact lumped update."""
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        n = int(math.floor((t_end - self.t_phys) / TICK_S + 1e-9))
        k = 0
        while k < n and self.running and self.power:
            self.t = self.t_phys + (k + 1) * TICK_S
            self._advance(TICK_S)
            k += 1
        if k < n:
            self.t = self.t_phys + n * TICK_S
            self._idle((n - k) * TICK_S)
        self.t_phys += n * TICK_S
        self.t = t_end

    def _heat_capacity(self):
        return (self.outer + sum(self.inner.values())) * RHO_W * CP_W + M_TANK_KG * CP_PC

    def _drain_unsealed(self, h):
        for mod in ('electrode', 'companion'):                                   # unsealed inner chambers drain into the outer chamber
            if self.inner[mod] > 0 and not self._sealed(mod):
                d = min(self.inner[mod], LEAK_M3_S * h)
                self.inner[mod] -= d
                self.outer += d

    def _idle(self, h):
        """Without a field: unsealed inner chambers drain, the buffer (or the empty tank) relaxes to the room exponentially, bands diffuse at room
        temperature."""
        room = self._room()
        self._drain_unsealed(h)
        if self.Tb is not None:
            C = self._heat_capacity()
            T1 = room + (self.Tb - room) * math.exp(-model()['electrical_sds']['UA_W_per_K'] * h / C)
            self.led['heat_to_room_j'].add(C * (self.Tb - T1))
            self.Tb = T1
        for g in self.gels.values():
            g['T'] = self.Tb if g['location'] != 'bench' and self.Tb is not None and self.outer + sum(self.inner.values()) > 1e-9 else room
            self._diffuse(g, h, room)

    def _advance(self, h):
        room = self._room()
        Tb = self.Tb if self.Tb is not None else room
        self._drain_unsealed(h)
        P = 0.0
        p = self.psu
        rec = self.records[self.op['record_id']]
        R = self._load_resistance()
        if self.R_last is not None and (R is None or R > E9_RATIO * self.R_last):          # open circuit / loose lead mid-run
            self._interrupt('E9', '负载电阻突变（盖被取下、引线松脱或缓冲液流失）')
        elif 'short_circuit' in self.faults:
            self._interrupt('E2', '过流（短路或缓冲液过浓）')
        else:
            native = self._buffer()['system'] == 'native_tris_glycine'
            paths = self._paths() or []
            R, V, I, mode = self._operating_point(paths, Tb, native)
            if I < NO_LOAD_A:
                self._end_run('error', 'E1', display='E1')
                self._event('alarm', 'E1', '无负载（电流<4 mA）', rid=rec['record_id'])
            else:
                self.V, self.I, p['active'] = V, I, mode
                self.R_last = R
                P = V * I
                self._electrophoresis(h, V, paths, native)
                rec['run_time_s'] += h
                v_disp, i_disp = float(round(self._reading(V, 'V'))), round(self._reading(I, 'I') * 1e3) / 1e3     # the panel shows V and mA only
                rec['max_voltage_v'] = max(rec['max_voltage_v'], v_disp)
                rec['max_current_a'] = max(rec['max_current_a'], i_disp)
                rec['max_power_w'] = max(rec['max_power_w'], float(round(v_disp * i_disp, 1)))    # from the displayed readings
                self.led['output_energy_j'].add(P * h)
                self.led['charge_c'].add(I * h)
                p['run_s'] += h
                if P > CELL_P_MAX or V > CELL_V_MAX:
                    self._warn(rec, 'cell_limit_exceeded')
                if any(kind == 'bypass' for kind, g, mod in paths):
                    self._warn(rec, 'companion_module_bypass')
                if p['time_min'] > 0 and p['run_s'] >= p['time_min'] * 60.0 - 1e-9:
                    e5 = 'power_failure_during_run' in rec['warnings']
                    self._end_run('completed', 'E5' if e5 else None, display='E5' if e5 else None)
        self._heat(h, P, Tb, room, rec)

    def _heat(self, h, P, Tb, room, rec):
        """Buffer + tank heat balance (explicit over the 0.5 s tick); at the boiling point the excess heat evaporates water from the outer chamber
        (latent heat booked; the vapour leaves with its sensible heat, like drained buffer); gels follow the buffer + R_th P_g, above 70 C they are
        heat damaged (bands smear)."""
        C = self._heat_capacity()
        UA = model()['electrical_sds']['UA_W_per_K']
        q_room = UA * (Tb - room)
        T1 = Tb + h * (P - q_room) / C
        self.led['heat_to_room_j'].add(q_room * h)
        if T1 > BOIL_K:
            q = (T1 - BOIL_K) * C
            dV = min(self.outer, q / (LATENT_J_KG * RHO_W))
            self.outer -= dV
            self.led['buffer_evaporated_m3'].add(dV)
            self.led['heat_to_evaporation_j'].add(dV * RHO_W * LATENT_J_KG)
            T1 = BOIL_K + (q - dV * RHO_W * LATENT_J_KG) / max(self._heat_capacity(), 1e-9)
            self._warn(rec, 'buffer_boiling')
        self.Tb = T1
        if T1 > BUFFER_HOT_K and not rec.get('_hot'):                          # evaluator-only: the temperature is not displayed by the cell or the supply
            rec['_hot'] = True
            self._event('alarm', 'BUFFER_HOT', '缓冲液超过50 °C', rid=rec['record_id'], truth=True)
        rth = model()['electrical_sds']['R_th_K_per_W']
        for g in self._installed_gels():
            Pg = g.pop('_P', 0.0)
            g['T'] = min(BOIL_K, self.Tb + rth * Pg)
            g['T_max'] = max(g['T_max'], g['T'])
            if g['T'] > GEL_DAMAGE_K and not g['damaged']:
                g['damaged'] = True
                for bands in g['lanes'].values():
                    for b in bands:
                        if b['state'] in ('stacked', 'resolving'):
                            b['sig2'] += SMEAR_M ** 2
                self._event('alarm', 'GEL_OVERHEATED', f'胶{g["gel_id"]}温度超过70 °C：条带涂抹', rid=rec['record_id'], truth=True)
        for g in self.gels.values():                                              # gels without a field (bench, taped, other module) keep diffusing
            if not g.get('_driven'):
                self._diffuse(g, h, room)
            g['_driven'] = False

    def _electrophoresis(self, h, V, paths, native):
        buf = self._buffer()
        rev = self.leads == 'reversed'
        cover = min(1.0, self.outer / (OUTER_4GEL_M3 if self.companion else OUTER_2GEL_M3))
        for kind, g, mod in paths:
            if kind != 'gel':
                continue
            Tg = g['T']
            Ig = V / self._gel_R(g, Tg, native)
            Pg = V * Ig
            g['_P'] = Pg
            g['_driven'] = True
            Q = self._elec(g, native)['Q_run_C'] * g['qfac']
            area = g['lib']['width_m'] * g['lib']['height_m']
            sm = SMILE_K * (Pg / area / 1000.0) ** 2 * (1 - 0.5 * cover) * (2.0 if buf['conductivity_factor'] > 1.5 else 1.0)
            g['smile'] = max(g['smile'], min(0.25, sm))
            ds = Ig * h / Q * g['L_run']        # moving-boundary advance this tick (reversed leads: the distance the same field moves a free ion)
            if rev:
                self._migrate_reversed(g, ds, native, Tg, h)
                continue
            s0 = g['s']
            g['s'] = s0 + ds
            self._migrate(g, s0, g['s'], native, Tg, h)

    def _sign(self, b, native):
        """Charge sign of a band in the running buffer: SDS complexes (or any protein in an SDS buffer) are anions; native proteins by the net
        charge at the gel pH 8.8 (|z| <= 0.5 counts as neutral)."""
        if b['sds'] or not native:
            return -1
        z = b['native_charge']
        if z is None:
            return 0
        return -1 if z < -0.5 else (1 if z > 0.5 else 0)

    def _rate(self, b, T, native):
        """Band speed relative to the moving boundary: SDS complexes by Ferguson (whatever the running buffer), native proteins by charge / size with
        the native sieving."""
        if b['sds'] or not native:
            return sds_rate(b['kda'], T)
        y, k = ferguson(b['kda'])
        return min(0.95, abs(self._native_rel(b))) * 10 ** (-NATIVE_SIEVE * k * T)

    def _upper(self, b):
        b['state'] = 'upper_buffer'
        self.led['protein_upper_buffer_kg'].add(b['ug'] * UG)

    def _stack_width(self, g, b):
        b['sig2'] = SIGMA0_M ** 2 + (b['ug'] * 1e-9 / (C_STACK_KG_M3 * g['lib']['width_m'] / g['lib']['wells'] * g['lib']['thickness_m'])) ** 2 / 12.0
        if b['sds'] and not b['heated']:
            b['sig2'] *= UNHEATED_BROADEN ** 2
        if not g['rinsed']:
            b['sig2'] *= UNRINSED_BROADEN ** 2

    def _migrate(self, g, s0, s1, native, Tg, h):
        """Normal polarity: anions stack behind the moving boundary, then migrate in the resolving gel; cations leave the wells upward into the
        upper buffer (they do not enter the gel, bulletin p.12) and move back up out of the resolving gel; neutral proteins stay in the wells."""
        res = g['lib']['resolving_height_m']
        for lane, bands in g['lanes'].items():
            lf = self._lane_factor(g, lane)
            for b in bands:
                if b['state'] in ('ran_off', 'upper_buffer'):
                    continue
                sign = self._sign(b, native)
                if sign == 0:
                    continue
                if sign > 0:
                    self._move_back(g, b, s1 - s0, native, h, Tg)
                    continue
                if b['state'] == 'well':
                    b['state'] = 'stacked'
                    self._stack_width(g, b)
                if b['state'] == 'stacked':
                    if s1 <= STACK_LEN_M:
                        continue
                    b['state'] = 'resolving'
                    b['x'] = 0.0
                    s_in = max(s0, STACK_LEN_M)
                else:
                    s_in = s0
                d_front = s1 - s_in
                n = max(1, int(math.ceil(d_front / 2e-4)))
                for _ in range(n):
                    b['x'] += d_front / n * min(self._rate(b, gel_T(g['lib'], b['x'] / res), native), 1.0)
                if b['x'] * lf >= res:                                               # the lane (smiled) reaches the gel bottom: run off
                    b['state'] = 'ran_off'
                    self.led['protein_run_off_kg'].add(b['ug'] * UG)
                else:
                    self._diffuse_band(b, h, Tg, g)

    def _move_back(self, g, b, d, native, h, Tg):
        """A band driven toward the wells: from the well or the stacking zone straight into the upper buffer; in the resolving gel back by d x rate,
        leaving through the wells into the upper buffer."""
        if b['state'] in ('well', 'stacked'):
            self._upper(b)
            return
        res = g['lib']['resolving_height_m']
        n = max(1, int(math.ceil(d / 2e-4)))
        for _ in range(n):
            b['x'] -= d / n * min(self._rate(b, gel_T(g['lib'], max(0.0, b['x']) / res), native), 1.0)
            if b['x'] <= 0.0:
                break
        if b['x'] <= 0.0:
            b['x'] = 0.0
            self._upper(b)
        else:
            self._diffuse_band(b, h, Tg, g)

    def _migrate_reversed(self, g, d, native, Tg, h):
        """Reversed leads: anions (SDS complexes, acidic native proteins) move back toward the wells into the upper buffer; cations (basic native
        proteins, the bulletin p.12 remedy) enter the gel without stacking (the Tris-glycine moving boundary does not form for them) and migrate
        down by d x rate; the dye front and the moving-boundary position stay where they are (not modelled backward)."""
        res = g['lib']['resolving_height_m']
        for lane, bands in g['lanes'].items():
            lf = self._lane_factor(g, lane)
            for b in bands:
                if b['state'] in ('ran_off', 'upper_buffer'):
                    continue
                sign = self._sign(b, native)
                if sign < 0:
                    self._move_back(g, b, d, native, h, Tg)
                elif sign > 0:
                    if b['state'] in ('well', 'stacked'):
                        self._stack_width(g, b)
                        col = g['well_ul'][lane] * UL / (g['lib']['width_m'] / g['lib']['wells'] * g['lib']['thickness_m'])   # sample column height
                        b['sig2'] += col * col / 12.0
                        b['state'], b['x'] = 'resolving', 0.0
                    n = max(1, int(math.ceil(d / 2e-4)))
                    for _ in range(n):
                        b['x'] += d / n * min(self._rate(b, gel_T(g['lib'], b['x'] / res), native), 1.0)
                    if b['x'] * lf >= res:
                        b['state'] = 'ran_off'
                        self.led['protein_run_off_kg'].add(b['ug'] * UG)
                    else:
                        self._diffuse_band(b, h, Tg, g)

    def _native_rel(self, b):
        z = -(b['native_charge'] or 0.0)
        e = protein_lib()['substances'].get(b['species'].split(':')[0])
        M = (e['mass_da'] or 1e4) if e else 1e4
        rh = 0.066e-9 * M ** (1.0 / 3.0) * 1.2
        mu = z * 1.602e-19 / (6 * math.pi * 0.89e-3 * rh)
        return mu / NATIVE_MU_REF

    def _diffuse_band(self, b, h, T, g):
        obst = sds_rate(b['kda'], gel_T(g['lib'], min(1.0, (b['x'] or 0.0) / g['lib']['resolving_height_m'])))
        D = KB * T / (6 * math.pi * water_viscosity(T) * rh_sds_m(b['kda'])) * min(1.0, obst)
        b['sig2'] += 2 * D * h

    def _diffuse(self, g, h, T):
        for bands in g['lanes'].values():
            for b in bands:
                if b['state'] == 'resolving':
                    self._diffuse_band(b, h, T, g)


ASYNC = {'psu.start'}
_DATA_GUARDED = {'measurement.result'}
_RECORD_ARG = {'measurement.result': 'record_id'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED}


class BioradMiniproteanSession:
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
        meta = copy.deepcopy(export_biorad_miniprotean_contract_metadata())
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

    def _foreign_op(self):
        h = self._host
        return h.op is not None and self._foreign(h.op['op'])

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        values['operation.started_by_other_agent'] = self._role == 'agent' and self._foreign_op()
        if self._role == 'agent':
            values['result.last_record_id'] = h._last_record(lambda k: not self._foreign(k))
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
        if capability_id in _CONTROL_GUARDED and self._foreign_op():
            if capability_id in ('power.set', 'power.press_switch') and not h.power:
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED and self._foreign(arguments.get(_RECORD_ARG[capability_id])):
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
            before = h.op['op'] if h.op else None
            h._caller = self._principal
            try:
                result = h.invoke(capability_id, **arguments)
            finally:
                h._caller = None
            if self._role == 'agent':
                if capability_id == 'records.list':
                    result = dict(records=[r for r in result['records'] if not self._foreign(r)])
                elif capability_id == 'events.read':
                    result = dict(events=[{k: v for k, v in e.items() if not k.startswith('_')} for e in h.events
                                          if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_rid')) and e['kind'] != 'fault' and not e.get('_truth')],
                                  dropped=h.events_dropped)
            operation = result.get('operation_id') if capability_id in ASYNC else None
            started = h.op['op'] if h.op and h.op['op'] != before else None      # a run started by a key press belongs to its caller too
            for oid in {o for o in (operation, started) if o}:                  # the creator owns a run; a resume by another principal keeps the owner
                h._owners.setdefault(oid, self._principal)
                h._owners.setdefault(h.operations[oid]['record_id'], h._owners[oid])
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
        rec = {k: copy.deepcopy(v) for k, v in h.records[op['record_id']].items() if not k.startswith('_')}   # evaluator-only flags stay private
        return dict(operation_id=operation_id, status='succeeded' if op['phase'] == 'completed' else op['phase'], result=rec)

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


ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值槽、另一个agent的记录或运行）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'MAINS_OUT_OF_RANGE': '电源电压不在90–132或198–264 VAC（或断电）',
    'POWER_OFF': '电源未通电', 'RUN_IN_PROGRESS': '运行未结束：电源正在运行、暂停或处于E2/E9保持', 'E10_UNACCEPTABLE_VALUE': 'E10：数值不可接受（电压1 V、电流1 mA、定时1 min步进）',
    'LIMIT_EDIT_NEEDS_PAUSE': '运行中只能改恒定参数与时间；改限值或恒定参数类型需先暂停（厂家规则）',
    'TIMED_MODE_CHANGE_NEEDS_STOP': '定时/不定时切换与断电检测需先停止再重新编程（厂家规则）', 'RECORDS_FULL': '记录数已达上限（200）',
    'LID_ON': '盖在槽上（先取盖）', 'LID_OFF': '盖不在槽上', 'LEADS_CONNECTED': '引线已插在电源上（先拔引线）', 'LEADS_DISCONNECTED': '引线未连接',
    'COMPANION_INSTALLED': '陪伴模块已在前位', 'COMPANION_ABSENT': '没有陪伴模块（胶位3、4需要它）', 'POSITION_OCCUPIED': '胶位已被占用',
    'POSITION_EMPTY': '胶位为空', 'ITEM_EXISTS': 'ID已存在', 'TOO_MANY_ITEMS': '物件数量超过上限（40）', 'UNKNOWN_GEL_TYPE': '胶模板库中没有该胶型',
    'UNKNOWN_ITEM': '胶或样品不存在', 'GEL_NOT_ON_BENCH': '该胶不在实验台上', 'BUFFER_DAM_IN_USE': '唯一的缓冲坝已装在另一胶位',
    'INNER_BUFFER_PRESENT': '内槽还有缓冲液（先倒掉缓冲液再打开夹臂）', 'UNKNOWN_BUFFER': '缓冲液模板库中没有该缓冲液',
    'BUFFER_MIX': '槽内已有另一种缓冲液（先倒掉）', 'UNKNOWN_PROTEIN': '蛋白模板库中没有该蛋白', 'NO_GEL_AT_POSITION': '该胶位没有胶',
    'COMB_IN_PLACE': '梳子还插在胶上', 'NO_SUCH_LANE': '该胶没有这个泳道', 'SAMPLE_INSUFFICIENT': '样品体积不足', 'UNKNOWN_RECORD': '记录不存在',
    'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '运行已结束', 'SAMPLES_LOADED': '孔里已有样品：冲洗会把样品冲掉（先冲洗再上样）',
}
ERROR_ZH_CAP = {('psu.start', 'RUN_IN_PROGRESS'): '运行未结束：电源正在运行或暂停（E2/E9保持中psu.start恢复被中断的运行，不返回此错误）'}
_PSU = ['psu.state', 'psu.active_mode', 'display.value', 'display.error_code', 'psu.timer_s', 'psu.output_voltage_v', 'psu.output_current_a']
_RUN = _PSU + ['operation.kind', 'result.last_record_id', 'truth.buffer_temperature_k', 'truth.gel_temperature_k', 'truth.load_resistance_ohm', 'truth.dye_front_fraction',
               'buffer.inner_m3', 'buffer.outer_m3', 'clock.time_s']
_POS = [f'position_{q}.item' for q in POSITIONS]
EFFECTS = {
    'plant.configure': ['plant.configured', 'power.enabled', 'truth.buffer_temperature_k', 'truth.gel_temperature_k', 'operation.kind', 'result.last_record_id'] + _PSU,
    'power.set': ['power.enabled'] + _PSU + ['operation.kind', 'result.last_record_id'],
    'power.press_switch': ['power.enabled'] + _PSU + ['operation.kind', 'result.last_record_id'],
    'psu.program': ['psu.constant', 'psu.voltage_set_v', 'psu.current_set_a', 'psu.time_set_s'] + _PSU + ['operation.kind', 'result.last_record_id'],
    'psu.press_key': ['display.mode', 'display.value', 'psu.constant', 'psu.voltage_set_v', 'psu.current_set_a', 'psu.time_set_s'] + _PSU
                     + ['operation.kind', 'result.last_record_id', 'truth.load_resistance_ohm'],
    'psu.start': _PSU + ['operation.kind', 'truth.load_resistance_ohm', 'result.last_record_id'], 'psu.stop': _PSU + ['operation.kind', 'result.last_record_id'],
    'lid.place': ['lid.on', 'lid.z_m'], 'lid.remove': ['lid.on', 'lid.z_m'], 'leads.connect': ['leads.state'], 'leads.disconnect': ['leads.state'],
    'companion.install': ['companion.installed'], 'companion.remove': ['companion.installed', 'buffer.inner_m3', 'buffer.outer_m3'],
    'gel.provide': ['gel.bench_ids'], 'gel.prepare': [], 'gel.install': _POS + ['gel.bench_ids', 'truth.dye_front_fraction', 'truth.gel_temperature_k'],
    'gel.remove': _POS + ['gel.bench_ids', 'truth.dye_front_fraction', 'truth.gel_temperature_k'],
    'buffer.fill': ['buffer.inner_m3', 'buffer.outer_m3', 'buffer.id', 'truth.buffer_temperature_k', 'truth.gel_temperature_k'],
    'buffer.drain': ['buffer.inner_m3', 'buffer.outer_m3', 'buffer.id'],
    'sample.prepare': ['sample.bench_ids'], 'sample.load': ['sample.bench_ids'],
    'fault.inject': ['truth.faults', 'power.enabled'] + _PSU + ['operation.kind', 'result.last_record_id'],
    'sim.step': ['clock.time_s'],
}
_LEVEL = ['buffer.inner_m3', 'buffer.outer_m3']
ON_STEP = {'psu.start': _RUN, 'psu.press_key': _RUN, 'psu.program': _RUN, 'psu.stop': _RUN, 'power.set': _RUN, 'power.press_switch': _RUN, 'fault.inject': _RUN,
           'plant.configure': _RUN, 'leads.disconnect': _RUN, 'leads.connect': _RUN, 'lid.remove': _LEVEL, 'gel.install': _LEVEL, 'gel.remove': _LEVEL,
           'companion.install': _LEVEL, 'companion.remove': _LEVEL, 'buffer.drain': _LEVEL + ['truth.buffer_temperature_k', 'truth.gel_temperature_k'],
           'buffer.fill': _LEVEL + ['truth.buffer_temperature_k', 'truth.gel_temperature_k'], 'sim.step': _RUN}
ON_STEP_ZH = {'psu.start': '运行中按调节模式输出电压电流；胶电阻随染料前沿与温度变化；定时到或故障时结束', 'psu.press_key': '按运行/暂停键开始、暂停或停止的运行按新状态推进（同psu.start）',
              'psu.program': '运行中改恒定参数：之后的电压/电流、发热与迁移按新设定推进', 'psu.stop': '停止后不再有电场：缓冲液冷却、条带只扩散',
              'plant.configure': '室温或电源电压改变：之后的散热按新室温，电压超范围断电时运行中止', 'leads.disconnect': '运行中拔引线：下一步负载突变报E9，运行结束',
              'leads.connect': '接上后运行（若在运行）按新的回路推进', 'companion.install': '未封闭的陪伴模块内槽液体随时间漏到外槽',
              'companion.remove': '同左', 'buffer.drain': '倒液后空槽（槽体热容）温度向室温弛豫；槽内胶随之', 
              'power.set': '断电使运行中止（E6/E7；定时+断电检测时恢复供电后继续）', 'power.press_switch': '同power.set', 'fault.inject': '断电/断路/短路/泄漏使运行报错或结束',
              'lid.remove': '密封不全的模块内槽缓冲液漏到外槽', 'gel.install': '密封不全的模块内槽缓冲液漏到外槽', 'gel.remove': '同左', 'buffer.fill': '缓冲液温度随环境与焦耳热变化（胶温随之变化）',
              'sim.step': '时钟推进；运行中推进电泳与发热；静置的胶内条带继续扩散'}
PRECONDITIONS = {
    'plant.configure': ('噪声种子只能在首次声明时设定', ['plant.configured']),
    'power.set': ('已声明实验室条件；开机时电源电压在90–132或198–264 VAC且未断电', ['plant.configured', 'power.enabled']),
    'power.press_switch': ('已声明实验室条件；断电时按下为开机，通电时按下为关机（运行中按断电处理）', ['plant.configured', 'power.enabled']),
    'psu.program': ('通电；运行中只能改恒定参数与时间，限值在暂停中改，定时模式需停止后改', ['power.enabled', 'psu.state']),
    'psu.press_key': ('通电；运行中改限值或恒定参数类型需暂停', ['power.enabled', 'psu.state']),
    'psu.start': ('通电、未在运行；负载电流≥4 mA（否则E1）', ['power.enabled', 'psu.state', 'lid.on', 'leads.state', 'buffer.inner_m3', 'buffer.outer_m3']),
    'psu.stop': ('通电', ['power.enabled']),
    'lid.place': ('盖不在槽上', ['lid.on']), 'lid.remove': ('盖在槽上且引线已拔下', ['lid.on', 'leads.state']),
    'leads.connect': ('盖在槽上、引线未连接', ['lid.on', 'leads.state']), 'leads.disconnect': ('引线已连接', ['leads.state']),
    'companion.install': ('无运行（含E2/E9保持）、盖已取下、前位空', ['psu.state', 'lid.on', 'companion.installed']),
    'companion.remove': ('无运行（含E2/E9保持）、盖已取下、胶位3和4为空', ['psu.state', 'lid.on', 'companion.installed', 'position_3.item', 'position_4.item']),
    'gel.provide': ('已声明实验室条件；胶型在模板库中', ['gel.bench_ids']),
    'gel.prepare': ('胶存在；撕胶带须在实验台上；装在胶位上的胶拔梳或冲洗孔须取盖且无运行（含E2/E9保持）；上样后不能再冲洗', ['gel.bench_ids', 'lid.on', 'psu.state'] + _POS),
    'gel.install': ('无运行（含E2/E9保持）、盖已取下、胶位空、胶在实验台上；3、4号位需陪伴模块', ['psu.state', 'lid.on', 'companion.installed'] + _POS),
    'gel.remove': ('无运行（含E2/E9保持）、盖已取下、该模块内槽缓冲液已倒掉', ['psu.state', 'lid.on', 'buffer.inner_m3'] + _POS),
    'buffer.fill': ('盖已取下；与槽内已有缓冲液相同', ['lid.on', 'buffer.id']), 'buffer.drain': ('无运行（含E2/E9保持）、盖已取下', ['psu.state', 'lid.on']),
    'sample.prepare': ('蛋白在模板库中', ['sample.bench_ids']),
    'sample.load': ('无运行（含E2/E9保持）、盖已取下、胶位上有已拔梳子的胶、样品体积足够', ['psu.state', 'lid.on', 'sample.bench_ids'] + _POS),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_NR = ['PLANT_REQUIRED', 'RUN_IN_PROGRESS']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'psu.program': ['PLANT_REQUIRED', 'POWER_OFF', 'E10_UNACCEPTABLE_VALUE', 'LIMIT_EDIT_NEEDS_PAUSE', 'TIMED_MODE_CHANGE_NEEDS_STOP'],
    'psu.press_key': ['PLANT_REQUIRED', 'POWER_OFF', 'LIMIT_EDIT_NEEDS_PAUSE', 'TIMED_MODE_CHANGE_NEEDS_STOP', 'RECORDS_FULL'],
    'psu.start': ['PLANT_REQUIRED', 'POWER_OFF', 'RUN_IN_PROGRESS', 'RECORDS_FULL'], 'psu.stop': ['PLANT_REQUIRED', 'POWER_OFF'],
    'lid.place': ['PLANT_REQUIRED', 'LID_ON'], 'lid.remove': ['PLANT_REQUIRED', 'LID_OFF', 'LEADS_CONNECTED'],
    'leads.connect': ['PLANT_REQUIRED', 'LID_OFF', 'LEADS_CONNECTED'], 'leads.disconnect': ['PLANT_REQUIRED', 'LEADS_DISCONNECTED'],
    'companion.install': _NR + ['LID_ON', 'COMPANION_INSTALLED'], 'companion.remove': _NR + ['LID_ON', 'COMPANION_ABSENT', 'POSITION_OCCUPIED'],
    'gel.provide': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_GEL_TYPE'],
    'gel.prepare': ['PLANT_REQUIRED', 'UNKNOWN_ITEM', 'GEL_NOT_ON_BENCH', 'RUN_IN_PROGRESS', 'LID_ON', 'SAMPLES_LOADED'],
    'gel.install': _NR + ['LID_ON', 'COMPANION_ABSENT', 'POSITION_OCCUPIED', 'UNKNOWN_ITEM', 'GEL_NOT_ON_BENCH', 'BUFFER_DAM_IN_USE'],
    'gel.remove': _NR + ['LID_ON', 'POSITION_EMPTY', 'INNER_BUFFER_PRESENT'],
    'buffer.fill': ['PLANT_REQUIRED', 'UNKNOWN_BUFFER', 'LID_ON', 'BUFFER_MIX'], 'buffer.drain': _NR + ['LID_ON'],
    'sample.prepare': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_PROTEIN'],
    'sample.load': _NR + ['LID_ON', 'UNKNOWN_ITEM', 'NO_GEL_AT_POSITION', 'COMB_IN_PLACE', 'NO_SUCH_LANE', 'SAMPLE_INSUFFICIENT'],
    'gel.observe': ['PLANT_REQUIRED', 'NO_GEL_AT_POSITION'], 'measurement.result': ['UNKNOWN_RECORD'], 'records.list': [], 'events.read': [], 'ledger.read': [],
    'gel.inspect': ['UNKNOWN_ITEM'], 'provenance.read': [], 'fault.inject': ['PLANT_REQUIRED'], 'sim.step': [],
}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件', 'psu.program': '设定值改变；显示读数经宿主镜像到LED数字', 'lid.place': '盖关节由宿主桥镜像',
    'leads.connect': '引线为盖上的静态几何（插头插在右上插孔）', 'leads.disconnect': '同左', 'companion.install': '陪伴模块是外部物件（前位为接口点）',
    'companion.remove': '同左', 'gel.provide': '胶是外部物件（胶位为接口点）', 'gel.prepare': '同左', 'gel.install': '同左', 'gel.remove': '同左',
    'buffer.fill': '缓冲液不建几何', 'buffer.drain': '同左', 'sample.prepare': '样品只在过程层', 'sample.load': '同左', 'gel.observe': '只读观察',
    'measurement.result': '只读记录', 'records.list': '只读列表', 'events.read': '只读事件', 'ledger.read': '只读账目（evaluator）', 'gel.inspect': '只读真值（evaluator）',
    'provenance.read': '只读来源记录', 'fault.inject': '评估者故障注入', 'sim.step': '推进时钟',
}
SCENE_MIRRORED = {'lid.z_m', 'power.enabled', 'display.value'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节或LED数字（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {
    'psu.start': ('运行结束：定时到（completed）、按停止键或psu.stop（stopped）、错误E1/E2/E9（error）或断电E6/E7（power_failed）；写运行记录',
                  '取消=停止：已通过的电量对应的迁移保留；错误或断电时同样保留已完成的迁移，电源显示错误码。'),
}
RESOURCES = {'psu.start': ['power_supply', 'cell', 'host_clock'], 'psu.press_key': ['power_supply'], 'psu.program': ['power_supply'], 'psu.stop': ['power_supply'],
             'power.set': ['mains'], 'power.press_switch': ['mains'], 'lid.place': ['cell_lid'], 'lid.remove': ['cell_lid'], 'leads.connect': ['leads'],
             'leads.disconnect': ['leads'], 'companion.install': ['tank'], 'companion.remove': ['tank'], 'gel.install': ['tank'], 'gel.remove': ['tank'],
             'buffer.fill': ['tank'], 'buffer.drain': ['tank'], 'sample.load': ['tank'], 'gel.provide': ['bench'], 'gel.prepare': ['bench'], 'sample.prepare': ['bench'],
             'plant.configure': ['room', 'mains'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock']}
OBSERVES = {'power.set': ['power.enabled'], 'power.press_switch': ['power.enabled'], 'psu.program': ['display.value', 'psu.state'],
            'psu.press_key': ['display.mode', 'display.value', 'psu.state'], 'psu.start': ['psu.state', 'display.value', 'display.error_code', 'result.last_record_id'],
            'psu.stop': ['psu.state'], 'lid.place': ['lid.on'], 'lid.remove': ['lid.on'], 'leads.connect': ['leads.state'], 'gel.install': _POS,
            'buffer.fill': ['buffer.inner_m3', 'buffer.outer_m3'], 'sample.load': ['sample.bench_ids']}
_WB = 'user_workbook'
_TM = 'manufacturer_tetra_manual'
_PP = 'manufacturer_powerpac_basic_manual'
_PG = 'manufacturer_precast_gels_manual'
_UP = 'uniprotkb'
_KO = 'kohlrausch_moving_boundary'
_RT = 'reynolds_tanford_1970'
_SIM = 'simulation_assumption'
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({
    'power.set': [_PP], 'power.press_switch': [_PP], 'psu.program': [_PP], 'psu.press_key': [_PP], 'psu.start': [_PP, _TM, _PG, _KO, _SIM], 'psu.stop': [_PP],
    'lid.place': [_TM], 'lid.remove': [_TM], 'leads.connect': [_TM, _PP], 'leads.disconnect': [_TM], 'companion.install': [_TM], 'companion.remove': [_TM],
    'gel.provide': [_PG, _TM], 'gel.prepare': [_PG], 'gel.install': [_TM, _PG], 'gel.remove': [_TM], 'buffer.fill': [_TM, _PG, _WB], 'buffer.drain': [_TM],
    'sample.prepare': [_UP, _PG, _RT], 'sample.load': [_TM, _PG], 'gel.observe': [_PG, _SIM], 'provenance.read': [_UP, _PG]})
CAP_SOURCES.update({k: [_TM] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温（缓冲液初温与散热）', 'mains_voltage_v': '供电电压', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.electrophoresis_tetra.limitations。'
CAP_LIMITS = {
    'psu.start': ['胶电阻、前沿电量Q_run、散热UA、胶内热阻与电导温度系数由厂家100/200/300 V电流–时间–温度表拟合（TGX SDS一组，适用于所有TGX规格；'
                  '天然PAGE按表4.1两行各一组：7.5–12 %与4–15/4–20 %/Any kD；表4.1的温度按运行结束时的胶温比较）；拟合与验证见electrophoresis_model.json的table（within标志）。',
                  '手灌Tris-HCl胶：电阻按导电长度、厚度、宽度与%T电阻系数缩放，电荷系数按厚度标定到说明书200 V 35–45 min（随%T增加，4 %约35.5 min、最高%T约44.5 min）；'
                  '误配缓冲液按浓度比缩放拖尾离子与槽路电阻（工程）；微笑（整条泳道含前沿按1+s(1−u²)伸长）与条带展宽为工程模型，标准300 V TGX条件微笑约1 %。',
                  'UA、R_th与电导温度系数只在厂家表的约18–35 °C内有依据：更高温度为定性外推——缓冲液在100 °C封顶、多余热量蒸发外槽水（只记潜热），'
                  '缓冲液>50 °C与胶>70 °C（热损伤、条带涂抹）只记在评测者可见的事件与gel.inspect中（槽与电源不显示温度）；玻板破裂与胶内气泡未建。',
                  '天然PAGE的热平衡只再现表4.1的1块胶：4块胶时胶温比表值高约4–5 K（厂家注明多块胶时温度应相近，本集总热模型以2胶SDS运行拟合的UA未达到）。',
                  '加液时新液与槽内液体及槽体（一个集总热节点）即时混合；E2/E9保持期间视为运行未结束（不能倒液、换胶、上样），此时断电按E6/E7结束该运行。',
                  '陪伴模块电极经内部触点并联、未封闭的陪伴模块形成约500 Ω（1×、25 °C）的缓冲液旁路（工程估计）：电源因此进入限流/功率限制、胶上电压下降、缓冲液过热，'
                  '对应说明书“过热并妨碍分离”；电源E3/E8/E12–E17硬件错误与dE9关闭功能未建。',
                  '反接引线：阴离子（SDS蛋白、酸性天然蛋白）退回孔并进入上槽缓冲液，阳离子天然蛋白不经浓缩直接进胶迁移（厂家p.12的碱性蛋白做法）；'
                  '溴酚蓝前沿与移动界面位置保持不变（其回退未建模）。'],
    'sample.prepare': ['蛋白质量、链与二硫键来自UniProt序列（推导；胰凝乳蛋白酶原A按单链酶原、伴刀豆球蛋白A按连接后的237残基单链，其少量断裂片段未建）；'
                       '表观SDS分子量默认等于序列质量（宽范围标准蛋白用厂家迁移图拟合值，p53等用文献值）；SDS结合按1.4 g/g（Reynolds–Tanford）；'
                       '天然PAGE的迁移率为序列电荷/流体力学半径的工程模型，寡聚状态未建；在天然缓冲液中跑的SDS处理样品按SDS迁移。'],
    'gel.observe': ['只返回透过卡夹可见的溴酚蓝前沿（全胶宽平均）与预染标准条带（读数误差±0.5 mm，同一胶状态重复读数误差相同）；预染标准的迁移按未染色Precision Plus的标称质量（假设）。'],
    'gel.install': ['夹臂锁定与密封视为装胶时自动完成；1或3块胶必须用缓冲坝，否则该模块内槽漏液；装入有缓冲液的槽时胶取缓冲液温度。'],
    'gel.prepare': ['未冲洗的孔按条带σ放大1.2倍模拟残留保存液/未聚合丙烯酰胺引起的变形（工程值）；在胶位上加内槽缓冲液不算冲洗；上样后不能再冲洗（SAMPLES_LOADED）。'],
    'sample.load': ['超出孔容量的样品向两侧各溢一半，邻孔装不下的部分与胶边外侧的部分进入内槽缓冲液（账目protein_spilled_kg）。'],
}
LIMITATIONS = [
    '选定配置：Mini-PROTEAN Tetra电泳槽（电极模块；陪伴模块与缓冲坝为附件）+ PowerPac Basic电源；转印、2-D、电洗脱模块与核酸胶不建模。',
    '胶、缓冲液、样品与陪伴模块在场景中没有几何；装胶、加液、上样与取胶是宿主/机器人抽象，没有接触验证。',
    '迁移模型：Ferguson关系（厂家TGX迁移图的Precision Plus 53个条带拟合，rms 1.3 mm；宽范围标准的48个条带只用于给出其表观质量）用于TGX与手灌Tris-HCl胶；染色与成像在凝胶成像仪中完成。',
    '热模型只在厂家表的温度范围内有依据（见psu.start限制）；缓冲液蒸发不改变其浓度；空闲期条带按室温扩散。',
    '没有真实PowerPac与电泳槽的硬件接口。',
]


def _sampling_text(k):
    if k in ('clock.time_s', 'psu.timer_s', 'display.value', 'truth.buffer_temperature_k', 'truth.gel_temperature_k', 'truth.dye_front_fraction', 'psu.output_voltage_v',
             'psu.output_current_a', 'truth.load_resistance_ohm', 'buffer.inner_m3', 'buffer.outer_m3'):
        return 'Value at the last 0.5 s physics tick; the run evolves inside step() on a fixed 0.5 s grid of the host clock.'
    return 'Changes when a command is accepted or at a run event; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    return '；另一个agent的运行进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）' if k in _CONTROL_GUARDED else ''


def export_biorad_miniprotean_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'BioradMiniproteanSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.biorad_miniprotean.BioradMiniproteanSession.invoke',
                                              invocation=f'session.invoke(instance_id="electrophoresis_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(dict.fromkeys(list(PRECONDITIONS[k][1]) + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else []))),
                                                                              enforced_by='runtime', check='BioradMiniproteanRuntime.invoke / BioradMiniproteanSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量')
                                for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询',
                                completion_state_ids=['psu.state', 'result.last_record_id'] if async_ else [],
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH_CAP.get((k, e), ERROR_ZH.get(e, e)), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'voltage_range_v': (list(V_RANGE), 'V', 'PowerPac Basic 10–300 V，1 V步进', [_PP]), 'current_range_a': (list(I_RANGE_A), 'A', '4–400 mA，1 mA步进', [_PP]),
            'power_max_w': (P_MAX_W, 'W', '最大功率75 W', [_PP]), 'timer_range_s': ([TIMER_MIN[0] * 60.0, TIMER_MIN[1] * 60.0], 's', '定时1–999 min（0=不定时；面板以min设定）', [_PP]),
            'accuracy': (dict(relative=ACC_REL, voltage_v=ACC_V, current_a=ACC_I), 'mixed', '读数精度±2 %或3 V/3 mA取大', [_PP]),
            'no_load_a': (NO_LOAD_A, 'A', 'E1：负载电流<4 mA', [_PP]), 'cell_limits': (dict(voltage_v=CELL_V_MAX, power_w=CELL_P_MAX), 'mixed', 'Tetra槽体限值600 V直流、500 W', [_TM]),
            'ambient_max_k': (AMBIENT_MAX_K, 'K', '最高环境温度40 °C', [_TM, _PP]), 'outer_fill_m3': (dict(two_gels=OUTER_2GEL_M3, four_gels=OUTER_4GEL_M3), 'm3', '外槽2胶550 mL、4胶680 mL', [_TM]),
            'buffer_total_m3': (dict(two_gels=700e-6, four_gels=1000e-6), 'm3', '总缓冲液2胶700 mL、4胶1000 mL（调研表/说明书）', [_WB, _TM])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    fe = model()
    for name, (val, unit, meaning, src) in {
            'ferguson': (dict({k: fe['ferguson'][k] for k in ('log10_Y0_poly', 'K_R_poly', 'any_kd_effective_T', 'gradient_shape')}, rms_m=fe['ferguson']['rms_mm'] * 1e-3,
                               max_abs_m=fe['ferguson']['max_abs_mm'] * 1e-3), 'mixed',
                         'Ferguson迁移标定（厂家TGX迁移图的Precision Plus 53个条带拟合）', [_PG]),
            'electrical_sds': ({k: fe['electrical_sds'][k] for k in ('R_lead_ohm', 'R_trail_ohm', 'Q_run_C', 'UA_W_per_K', 'R_th_K_per_W', 'alpha_per_K')}, 'mixed',
                               'SDS-PAGE电–热标定（厂家表3.1、3.2拟合）', [_PG, _KO]),
            'electrical_native': ({k: fe['electrical_native'][k] for k in ('R_lead_ohm', 'R_trail_ohm', 'Q_run_C')}, 'mixed', '天然PAGE电学标定（厂家表4.1第一行7.5–12 %拟合）', [_PG, _KO]),
            'electrical_native_gradient': ({k: fe['electrical_native_gradient'][k] for k in ('R_lead_ohm', 'R_trail_ohm', 'Q_run_C')}, 'mixed',
                                           '天然PAGE电学标定（厂家表4.1第二行4–15 %、4–20 %、Any kD拟合）', [_PG, _KO]),
            'stack_len_m': (STACK_LEN_M, 'm', '孔底到分离胶的浓缩段长度（迁移图几何推导）', [_PG])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning + '（由厂家数据拟合或推导的常数）', evidence='assumption', source_ids=list(src),
                            uncertainty='拟合/推导值，残差见electrophoresis_model.json（Ferguson rms约1.3 mm；SDS电–热拟合100 V运行84 min与终了电流10.2 mA/胶、'
                                        '300 V运行20.1 min与终了42.7 mA/胶略出厂家范围；天然表4.1两行全部在范围内）')
    for name, (val, unit, meaning) in {
            'inner_cap_m3': (INNER_CAP_M3, 'm3', '单模块内槽容量（700−550 mL）'), 'inner_contact_m3': (INNER_CONTACT_M3, 'm3', '内槽缓冲液盖过孔口所需体积'),
            'outer_contact_m3': (OUTER_CONTACT_M3, 'm3', '外槽缓冲液盖过下电极与胶底所需体积'), 'outer_max_m3': (OUTER_MAX_M3, 'm3', '外槽最多加到溢入内槽前'),
            'leak_m3_s': (LEAK_M3_S, 'm3/s', '未封闭模块内槽的漏液速率'), 'r_ch0_ohm': (R_CH0, 'ohm', '1×缓冲液的槽路电阻（包含在拟合R_L中）'),
            'r_bypass0_ohm': (R_BYPASS0, 'ohm', '未封闭陪伴模块的缓冲液旁路电阻（1×，25 °C）'), 'c_stack_kg_m3': (C_STACK_KG_M3, 'kg/m3', '浓缩区带蛋白浓度（决定过载展宽）'),
            'sigma0_m': (SIGMA0_M, 'm', '最小条带σ'), 'smile_k': (SMILE_K * 1e-6, 'm4/W2', '微笑系数：s = k (P_g / A)^2（胶功率面密度W/m2）'),
            'rh_sds': (dict(a_m=RH_SDS_NM[0] * 1e-9, b=RH_SDS_NM[1]), 'mixed', 'SDS复合物流体力学半径 R_h = a (M / 10 kg/mol)^b（a以m计，b无量纲）'),
            'unheated_broaden': (UNHEATED_BROADEN, '1', '未加热SDS样品的展宽倍数'), 'native_mu_ref': (NATIVE_MU_REF, 'm2/(V s)', '天然PAGE前沿离子等效迁移率'),
            'native_sieve': (NATIVE_SIEVE, '1', '天然蛋白筛分系数相对SDS'), 'dye_read_sigma_m': (DYE_READ_SIGMA_M, 'm', '透过卡夹读前沿的误差'),
            'e9_ratio': (E9_RATIO, '1', '一步内负载电阻增大超过该倍数判E9'), 'tank_heat_capacity': (dict(mass_kg=M_TANK_KG, cp_J_kgK=CP_PC), 'mixed', '槽体热容'),
            'boiling': (dict(boil_k=BOIL_K, latent_j_kg=LATENT_J_KG), 'mixed', '缓冲液沸点与蒸发潜热（水）'), 'buffer_hot_k': (BUFFER_HOT_K, 'K', '缓冲液过热警告阈值'),
            'gel_damage_k': (GEL_DAMAGE_K, 'K', '胶热损伤阈值'), 'smear_m': (SMEAR_M, 'm', '热损伤胶的附加条带σ'), 'unrinsed_broaden': (UNRINSED_BROADEN, '1', '未冲洗孔的条带σ放大倍数')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['psu_case', 'psu_front_panel', 'display_digits', 'psu_output_jacks', 'switch_power', 'tank_body', 'electrode_assembly', 'ea_electrodes', 'lid', 'lid_lead_red',
                       'lid_lead_black'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='PowerPac恒压/恒流/功率限制调节，Tetra槽电路（盖联锁、引线极性、模块密封、缓冲液液位），Kohlrausch移动界面前沿、焦耳热与缓冲液温度，蛋白Ferguson迁移、浓缩、跑出与条带展宽',
        equations=['PSU: CV: V = V_set, I = V/R (if I > I_set -> CC); CC: I = I_set, V = I R (if V > V_set -> CV); if V I > 75 W -> V = sqrt(75 R), I = sqrt(75 / R) (CP)',
                   'gel: R_g = geo [(R_L - R_ch0)(1 - phi) + (R_T - R_ch0) phi / c + R_ch0 / c] exp(-alpha (T_g - 25 C)); cell R = 1 / sum(1 / R_g) (+ open companion bypass)',
                   'Kohlrausch front: d s / d t = I_g L_run / (Q_run q_fac), phi = s / L_run, L_run = 4.4 mm + resolving height',
                   'heat: (m_buf c_p + m_tank c_PC) dT_b/dt = V I - UA (T_b - T_room); T_g = T_b + R_th P_g',
                   'Ferguson: stacked with the front until s = 4.4 mm, then dx/ds = Y0(M) 10^(-K_R(M) T(x)); log10 Y0, K_R cubic in log10(M/50 kDa) on 10-250 kDa (linear above); T(x) = T_top + (T_bot - T_top)(x/L)^shape; below 10 kDa log10(rate) scales with log10(M/2 kDa)/log10(5) (peptides <= 2 kDa at the front)',
                   'band: sigma^2 = sigma0^2 + (m / (c_stack w t))^2 / 12 + 2 D t, D = k T / (6 pi eta R_h) Rf_local; smile: the whole lane (bands, run-off test, front) x_lane = x (1 + s (1 - u^2)), s = k (P_g / A)^2 (1 - cover / 2) with k in m4/W2',
                   'native: rel mobility = |z| e / (6 pi eta R_h) / mu_ref (z = net charge at pH 8.8; anions z < -0.5 run with normal leads, cations z > 0.5 with reversed leads, |z| <= 0.5 stay), sieving 0.6 K_R',
                   'heat limits: T_b <= 373.15 K with evaporation dV = excess / (rho L); gel T <= 373.15 K; T_gel > 343.15 K: heat damage (sigma^2 += (1.5 mm)^2)',
                   'gel temperature inside each tick: T_g = T_b + R_th V^2 / R_g(T_g) (Newton), supply output iterated with the CC / CP limits'],
        parameters=params, source_ids=[_SIM, _WB, _TM, _PP, _PG, _UP, _KO, _RT], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_miniprotean_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('biorad_miniprotean', caps, slots, {'electrophoresis_tetra': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'electrophoresis_tetra': mechanism})
