"""Agilent 7890B GC (one split/splitless inlet, one capillary column, one FID; manual syringe injection): keypad firmware over the process.

Process: scripts/gc_runtime.py (unchanged; the GC8890B asset inherits it too) - lumped oven / inlet / FID heater zones, carrier and detector
flows, split / splitless liner transfer, Gaussian column bands, FID charge and an exact mass ledger.  FirmwareGC below subclasses its GC
only to switch heater zones, to apply the firmware's flow control and readiness rules, and to run the firmware between integration steps.

Firmware (7890B Operation Manual G3430-90053, references/manuals/agilent_7890b_ssl_fid; pages are PDF pages; datasheet 5991-1436JAJP):
  * method setpoints (p.38-42): oven program (initial time + 20 ramps, datasheet), post run, S/SL inlet (split / splitless / pulsed modes,
    split ratio, purge, septum purge, gas saver), column mode (constant / programmed flow or pressure, 3 ramps, datasheet), FID (heater, H2,
    air, makeup, flame, electrometer, lit offset), analog outputs; 9 stored methods + SLEEP / WAKE / CONDITION, load / store / delete,
    method mismatch after a module change;
  * run control (p.24, p.50): Prep Run (pre-run: splitless purge valve closed, gas saver restored), Start, Stop, the run ends with the oven
    program (999.99 min maximum), post run, Auto prep run;
  * pneumatics: the GC computes the head pressure for the set column flow from the CONFIGURED column (length, diameter, gas) with the
    compressible Hagen-Poiseuille law; the actual flow follows from that pressure through the INSTALLED column and the supplied gas, so a
    wrong column configuration or gas type gives a wrong actual flow until column calibration (p.192-196) corrects the configuration;
    flow / pressure sensor zero errors (evaluator, default 0) are removed by Auto flow zero or a manual zero (p.190-192);
  * readiness and status (p.26, p.33-34, p.142-143): Ignore Ready, oven equilibration time, heaters that are Off are not waited for;
    shutdowns (front inlet flow shutdown after 2 min without carrier, hydrogen safety shutdown, flame out after failed re-ignition, oven
    thermal shutdown): the line blinks Off, [Off/No] clears, [On/Yes] re-enables, safety shutdowns need a power cycle; alert tones;
  * configuration (p.140-183): oven limits, inlet gas type, column dimensions and connections, makeup gas type, Ignore Ignitor, remove /
    install module (hard configuration lock, power cycle), analog fast peaks, time and date, instrument options, status table order;
  * options (p.189-200): calibration, LAN communication (applied at power-up), keyboard lock, hard configuration lock, sounds, pressure
    units, language, radix, display saver;
  * automation (p.43-52, p.106-116, p.30): sequences (stored, edited, status; injections need an ALS or a sampling valve, which this
    configuration does not have), clock table events, instrument schedule (sleep / wake / condition), keystroke macros (User Key 1 / 2);
  * maintenance (p.32, p.35, p.118-126): EMF counters (Service Due / warning, maintenance log), run log, system event log, stopwatch.
Front panel (panel.press / panel.switch / panel.capture / panel.frame): the 4-line keypad display, the status board LEDs and the 63 keys
of p.14 (panel_ui.json beside the contract) run by the shared engine _panel_ui.py on top of the capabilities below."""
import copy
import dataclasses
import datetime
import hashlib
import json
import math
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))
import gc_runtime as legacy  # noqa: E402

PROTOCOL_VERSION = '1.0.0'
ASSET_TYPE_ID = 'instrument.agilent_7890b_ssl_fid'
ASSET_VERSION = '1.1.2'
ASSET_DIR = ROOT / 'assets/GC7890B'
C0 = 273.15
ML_MIN = 1e-6 / 60.0                                                     # 1 mL/min in m3/s
PSI = 6894.757293168
T_NTP, P_NTP = 298.15, 101325.0                                          # Agilent flow reference (datasheet: NTP = 25 C, 1 atm)
GASES = ('He', 'H2', 'N2', 'ArMe')
# gas viscosity eta(T) = eta0 (T / 273.15 K)^n (Pa s): engineering approximations of literature data, used for the column flow law only
VISCOSITY = {'He': (1.87e-5, 0.69), 'H2': (8.4e-6, 0.68), 'N2': (1.66e-5, 0.70), 'ArMe': (2.1e-5, 0.72)}
N_OVEN_RAMPS, N_COL_RAMPS = 20, 3                                        # datasheet: 20 oven ramps; pressure / flow programs 3 ramps
MAX_RUN_S = 999.99 * 60.0                                                # datasheet: maximum run time 999.99 min
ZONE_MAX_K = {'oven': C0 + 450.0, 'inlet': C0 + 400.0, 'detector': C0 + 450.0}   # datasheet: oven 450 C, S/SL 400 C, FID 450 C
LEGACY_ZONE = {'oven': 'oven', 'inlet': 'inlet', 'detector': 'fid'}
FLOW_SHUTDOWN_S = 120.0                                                  # p.12 / p.33: the flow shuts down after 2 minutes
REIGNITE_TRIES = 3                                                       # assumption (manual: 'tries to reignite it')
REIGNITE_EVERY_S = 5.0                                                   # assumption: one ignition attempt every 5 s
SAVER_DIM_S = 1800.0                                                     # assumption: display saver dims after 30 min without a key
LOG_MAX = 50                                                             # assumption: entries kept per log (capacity not documented)
MACRO_MAX = 31                                                           # p.30: up to 31 keystrokes
METHOD_SLOTS = [str(i) for i in range(1, 10)] + ['SLEEP', 'WAKE', 'CONDITION']
SEQ_SLOTS = [str(i) for i in range(1, 10)]
DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
SCHEDULE_FUNCTIONS = ['sleep', 'wake_current', 'wake_file', 'condition_current', 'condition_file']
CLOCK_ACTIONS = ['load_method', 'prep_run']
EPOCH = datetime.datetime(2026, 1, 5, 8, 0, 0)                           # assumption: GC clock at simulation time 0 (a Monday, 08:00)
FID_BACKGROUND_A = 10e-12                                                # assumption inside the checkout range 5-20 pA (p.57)
EMF_DEFAULTS = {                                                         # p.121-123 Table 15 (S/SL, FID, column, instrument)
    'inlet.liner_injections': ('Front inlet', 'Liner', 'injections', 200), 'inlet.liner_time': ('Front inlet', 'Liner', 'days', 30),
    'inlet.oring_injections': ('Front inlet', 'Liner O-ring', 'injections', 1000), 'inlet.oring_time': ('Front inlet', 'Liner O-ring', 'days', 60),
    'inlet.septum_injections': ('Front inlet', 'Septum', 'injections', 200), 'inlet.goldseal_injections': ('Front inlet', 'Gold seal', 'injections', 5000),
    'inlet.goldseal_time': ('Front inlet', 'Gold seal', 'days', 90), 'inlet.trap_injections': ('Front inlet', 'Split vent trap', 'injections', 10000),
    'inlet.trap_time': ('Front inlet', 'Split vent trap', 'days', 182), 'detector.collector': ('Front det', 'Collector', 'injections', None),
    'detector.jet': ('Front det', 'Jet', 'injections', None), 'detector.ignitor': ('Front det', 'Ignitor', 'ignitions', None),
    'column.injections': ('Column 1', 'Injections', 'injections', None), 'column.oven_cycles': ('Column 1', 'Oven cycles', 'cycles', None),
    'instrument.on_time': ('Instrument', 'On time', 'days', None), 'instrument.run_count': ('Instrument', 'Run count', 'runs', None),
}
ANALOG_BANDWIDTH_HZ = {False: 3.0, True: 8.0}                          # p.169: standard 3.0 Hz (peaks >= 0.01 min), Fast peaks 8 Hz (>= 0.004 min)
STATUS_SETPOINTS = ['oven_temp', 'inlet_temp', 'inlet_pressure', 'column_flow', 'detector_temp', 'h2_flow', 'air_flow', 'makeup_flow', 'detector_output']


class ActionRejected(ValueError):
    """The firmware refused a command; the session restores the state from before the call."""


def eta(gas, temperature_k):
    e0, n = VISCOSITY[gas]
    return e0 * (temperature_k / C0) ** n


def column_flow(p_in, p_out, length_m, diameter_m, gas, temperature_k):
    """Compressible Hagen-Poiseuille: outlet flow of a capillary at NTP (m3/s) for absolute inlet / outlet pressures."""
    if length_m <= 0 or diameter_m <= 0 or p_in <= p_out:
        return 0.0
    return math.pi * diameter_m ** 4 * (p_in ** 2 - p_out ** 2) * T_NTP / (256.0 * eta(gas, temperature_k) * length_m * P_NTP * temperature_k)


def head_pressure(flow, p_out, length_m, diameter_m, gas, temperature_k):
    """Absolute inlet pressure that gives an NTP column flow (inverse of column_flow)."""
    return math.sqrt(p_out ** 2 + flow * 256.0 * eta(gas, temperature_k) * length_m * P_NTP * temperature_k / (math.pi * diameter_m ** 4 * T_NTP))


def program_value(initial, initial_time, ramps, t):
    """Value of a temperature / flow / pressure program at run time t: initial hold, then ramps (rate, final, final time) until a rate of 0.
    Returns (value, phase) with phase 'initial' / 'rate' / 'final' / 'end'."""
    v = initial
    if t < initial_time:
        return v, 'initial'
    t -= initial_time
    phase = 'end'
    for r in ramps:
        if not r['rate']:
            break
        dur = abs(r['final'] - v) / r['rate']
        if t < dur:
            return v + math.copysign(r['rate'] * t, r['final'] - v), 'rate'
        t -= dur
        v = r['final']
        if t < r['final_time']:
            return v, 'final'
        t -= r['final_time']
    return v, phase


def program_length(initial, initial_time, ramps):
    total, v = initial_time, initial
    for r in ramps:
        if not r['rate']:
            break
        total += abs(r['final'] - v) / r['rate'] + r['final_time']
        v = r['final']
    return total


# ---------------------------------------------------------------- schema helpers
def number(unit='1', lo=None, hi=None):
    s = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        s['minimum'] = lo
    if hi is not None:
        s['maximum'] = hi
    return s


def integer(lo, hi):
    return {'type': 'integer', 'x-unit': '1', 'minimum': lo, 'maximum': hi}


def obj(properties, optional=()):
    return dict(type='object', properties=properties, required=[k for k in properties if k not in optional], additionalProperties=False)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


def array(s, lo=0, hi=4096):
    return dict(type='array', items=s, minItems=lo, maxItems=hi)


BOOL = {'type': 'boolean'}
TEXT = {'type': 'string', 'maxLength': 120}
FREE = {'type': 'object'}


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
            if not set(s.get('required', [])) <= set(v) or not set(v) <= set(s['properties']):
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
        if not isinstance(v, str) or len(v) > s.get('maxLength', 10 ** 6):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'pattern' in s and not re.fullmatch(s['pattern'], v):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'enum' in s and v not in s['enum']:
            raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


ZONE = enum(['oven', 'inlet', 'detector'])
FLOW = number('m3/s', 0.0, 1250 * ML_MIN)
PRESS = number('Pa', 0.0, 150 * PSI)                                       # gauge pressure (EPC 0-100 psig, 0-150 for some modules)
SLOT = enum(METHOD_SLOTS)
OK = obj(dict(ok=BOOL))
STATE = obj(dict(state=FREE))
cap('plant.configure', obj(dict(ambient_k=number('K', 283.15, 313.15), pressure_pa=number('Pa', 70000.0, 110000.0), carrier_gas=enum(GASES),
                                makeup_gas=enum(['N2', 'He', 'ArMe']), column_length_m=number('m', 1.0, 105.0), column_diameter_m=number('m', 5e-5, 5.3e-4),
                                column_film_m=number('m', 1e-8, 5e-6), fid_background_a=number('A', 0.0, 1e-10)),
                           optional=('ambient_k', 'pressure_pa', 'carrier_gas', 'makeup_gas', 'column_length_m', 'column_diameter_m', 'column_film_m',
                                     'fid_background_a')),
    STATE, dict(carrier_gas='He', column_length_m=30.0, column_diameter_m=2.5e-4), ['chassis'],
    '评测者：实验室与安装状态（室温、大气压、实际接入的载气与尾吹气、实际安装的色谱柱尺寸、FID点火后的背景电流）', 'evaluator', 'configuration')
cap('power.connect_mains', obj(dict(connected=BOOL)), STATE, dict(connected=True), ['power_inlet'], '插拔电源线（断电即关机）', 'agent', 'robot_skill')
cap('power.set', obj(dict(on=BOOL)), STATE, dict(on=True), ['power_switch'], '主电源开关（开机自检后显示Power on successful）')
cap('gas.connect', obj(dict(gas=enum(['carrier', 'hydrogen', 'air', 'makeup']), connected=BOOL)), STATE, dict(gas='carrier', connected=True),
    ['carrier_port', 'hydrogen_port', 'air_port', 'makeup_port'], '在气源处开或关载气、氢气、空气、尾吹气（打开本地截止阀）', 'agent', 'robot_skill')
cap('oven.set_door', obj(dict(open=BOOL)), STATE, dict(open=False), ['oven_door'], '开或关柱箱门', 'agent', 'robot_skill')
cap('sample.aspirate', obj(dict(fraction=number('1', 1e-9, 1.0))), STATE, dict(fraction=0.01), ['syringe', 'sample_vial'],
    '针筒从样品瓶吸取瓶中各组分质量的fraction（比例，不是微升）', 'agent', 'robot_skill')
cap('syringe.insert', obj({}), STATE, {}, ['syringe', 'septum'], '针头穿过隔垫插入进样口', 'agent', 'robot_skill')
cap('syringe.depress', obj(dict(fraction=number('1', 1e-9, 1.0)), optional=('fraction',)), STATE, dict(fraction=1.0), ['plunger'],
    '推针筒推杆注入（须分析运行中且就绪；手册：推推杆的同时按Start）', 'agent', 'robot_skill')
cap('syringe.retract', obj({}), STATE, {}, ['syringe'], '拔出针头', 'agent', 'robot_skill')
cap('run.prep', obj({}), STATE, {}, ['key_prep_run'], 'Prep Run：进入预运行（不分流时关闭吹扫阀、从省气模式恢复流量）')
cap('run.start', obj({}), STATE, {}, ['start_key'], 'Start：手动进样后开始运行（须就绪）')
cap('run.stop', obj({}), STATE, {}, ['stop_key'], 'Stop：立即终止运行（运行中的序列中止）')
cap('thermal.set_temperature', obj(dict(zone=ZONE, temperature_k=number('K', C0, C0 + 450.0))), STATE, dict(zone='oven', temperature_k=C0 + 150.0),
    ['oven_heater', 'inlet_heater', 'fid_heater'], '设定柱箱初始温度、进样口或FID加热器温度（柱箱不超过配置的最高温度，进样口400 °C，FID 450 °C）')
cap('thermal.set_zone', obj(dict(zone=ZONE, on=BOOL)), STATE, dict(zone='inlet', on=True), ['oven_heater', 'inlet_heater', 'fid_heater'],
    '加热区开或关（On/Yes、Off/No；关闭的加热区不参与就绪判断）')
_RAMP = obj(dict(index=integer(1, N_OVEN_RAMPS), rate_k_s=number('K/s', 0.0, 2.0), final_k=number('K', C0, C0 + 450.0), final_time_s=number('s', 0.0, MAX_RUN_S)),
            optional=('rate_k_s', 'final_k', 'final_time_s'))
cap('oven.set_initial_time', obj(dict(time_s=number('s', 0.0, MAX_RUN_S))), STATE, dict(time_s=60.0), ['oven_heater'], '柱箱程序初始保持时间')
cap('oven.set_ramp', _RAMP, STATE, dict(index=1, rate_k_s=20 / 60.0, final_k=C0 + 190.0, final_time_s=0.0), ['oven_heater'],
    '柱箱程序第n段：升温速率（0结束程序）、终温、终温保持时间（最多20段，最高120 °C/min）')
cap('oven.set_post_run', obj(dict(time_s=number('s', 0.0, MAX_RUN_S), temperature_k=number('K', C0, C0 + 450.0)), optional=('time_s', 'temperature_k')), STATE,
    dict(time_s=300.0, temperature_k=C0 + 250.0), ['oven_heater'], 'Post Run：运行后保持时间与柱箱温度（如烘烤）')
cap('inlet.configure', obj(dict(mode=enum(['split', 'splitless', 'pulsed_split', 'pulsed_splitless']), split_ratio=number('1', 0.0, 7500.0),
                                split_flow_m3_s=FLOW, purge_flow_m3_s=FLOW, purge_time_s=number('s', 0.0, MAX_RUN_S), septum_purge_m3_s=number('m3/s', 0.0, 30 * ML_MIN),
                                gas_saver=BOOL, saver_flow_m3_s=FLOW, saver_time_s=number('s', 0.0, MAX_RUN_S), pulse_pressure_pa=PRESS,
                                pulse_time_s=number('s', 0.0, MAX_RUN_S)),
                           optional=('mode', 'split_ratio', 'split_flow_m3_s', 'purge_flow_m3_s', 'purge_time_s', 'septum_purge_m3_s', 'gas_saver', 'saver_flow_m3_s', 'saver_time_s',
                                     'pulse_pressure_pa', 'pulse_time_s')),
    STATE, dict(mode='splitless', purge_flow_m3_s=40 * ML_MIN, purge_time_s=30.0), ['split_valve', 'septum_purge'],
    '分流/不分流进样口：模式（Mode/Type）、分流比（或分流流量，按计算柱流量换算为分流比）、吹扫流量与时间、隔垫吹扫、省气模式、脉冲压力与时间')
cap('inlet.set_gas', obj(dict(on=BOOL)), STATE, dict(on=True), ['carrier_epc'], '进样口载气压力控制开或关（压力行On/Yes、Off/No）')
cap('column.configure', obj(dict(mode=enum(['constant_flow', 'ramped_flow', 'constant_pressure', 'ramped_pressure']), flow_m3_s=number('m3/s', 0.0, 100 * ML_MIN),
                                 pressure_pa=PRESS, initial_time_s=number('s', 0.0, MAX_RUN_S)),
                            optional=('mode', 'flow_m3_s', 'pressure_pa', 'initial_time_s')),
    STATE, dict(mode='constant_flow', flow_m3_s=1.0 * ML_MIN), ['carrier_epc', 'column'],
    '色谱柱1：模式（恒流、程序流量、恒压、程序压力）与流量或柱前压（设压力时在流量模式下换算为流量）')
cap('column.set_ramp', obj(dict(index=integer(1, N_COL_RAMPS), rate=number('1', 0.0, 1e6), final=number('1', 0.0, 1e6), final_time_s=number('s', 0.0, MAX_RUN_S)),
                           optional=('rate', 'final', 'final_time_s')),
    STATE, dict(index=1, rate=0.5 * ML_MIN / 60.0, final=2.0 * ML_MIN, final_time_s=60.0), ['carrier_epc'],
    '色谱柱程序第n段（程序流量：速率m3/s每秒、终值m3/s；程序压力：速率Pa/s、终值Pa表压）')
cap('detector.configure', obj(dict(h2_flow_m3_s=number('m3/s', 0.0, 100 * ML_MIN), air_flow_m3_s=number('m3/s', 0.0, 800 * ML_MIN),
                                   makeup_flow_m3_s=number('m3/s', 0.0, 100 * ML_MIN), lit_offset_a=number('A', 0.0, 99.9e-12), electrometer=BOOL),
                              optional=('h2_flow_m3_s', 'air_flow_m3_s', 'makeup_flow_m3_s', 'lit_offset_a', 'electrometer')),
    STATE, dict(h2_flow_m3_s=30 * ML_MIN, air_flow_m3_s=400 * ML_MIN, makeup_flow_m3_s=25 * ML_MIN, lit_offset_a=2e-12), ['hydrogen_epc', 'air_epc', 'makeup_epc', 'fid_electrometer'],
    'FID：氢气、空气、尾吹气流量，熄火判断阈值Lit offset，静电计开关')
cap('detector.set_gas', obj(dict(gas=enum(['hydrogen', 'air', 'makeup']), on=BOOL)), STATE, dict(gas='makeup', on=True), ['hydrogen_epc', 'air_epc', 'makeup_epc'],
    'FID气体通道开或关')
cap('detector.set_flame', obj(dict(on=BOOL)), STATE, dict(on=True), ['fid_igniter'], 'FID火焰：开（打开氢气与空气并点火）或关（关闭氢气与空气）')
cap('detector.manual_ignite', obj({}), STATE, {}, ['fid_body'], 'Ignore Ignitor为True时用外部点火器手动点燃火焰（手册p.168）', 'agent', 'robot_skill')
cap('analog.configure', obj(dict(output=integer(1, 2), signal=enum(['front_detector', 'column_comp', 'none']), fast_peaks=BOOL), optional=('signal', 'fast_peaks')), STATE,
    dict(output=1, signal='front_detector'), ['signal_port'], '模拟输出1/2：分配信号（前检测器、减去柱补偿曲线的前检测器、无）、Fast peaks')
cap('column_comp.run', obj({}), STATE, {}, ['fid_electrometer'], '[Column Comp]：做一次空白运行（不进样），记录前检测器输出作为柱补偿曲线')
cap('method.load', obj(dict(slot=SLOT)), STATE, dict(slot='1'), ['parameter_keys'], '[Load][Method]：载入存储的方法替换当前方法（未就绪直到达到新设定）')
cap('method.store', obj(dict(slot=SLOT)), STATE, dict(slot='1'), ['parameter_keys'], '[Method]…[Enter]/[Store]：把当前设定存为方法n（或SLEEP/WAKE/CONDITION）')
cap('method.delete', obj(dict(slot=SLOT)), STATE, dict(slot='1'), ['parameter_keys'], '[Delete]：删除存储的方法')
_SM = integer(-1, 9)
cap('sequence.configure', obj(dict(priority_method=_SM, use_priority=BOOL, post_method=integer(0, 9), repeat=BOOL),
                              optional=('priority_method', 'use_priority', 'post_method', 'repeat')),
    STATE, dict(post_method=0, repeat=False), ['parameter_keys'], '[Seq]：优先序列方法号与Use priority、序列后方法与重复')
cap('sequence.set_subsequence', obj(dict(index=integer(1, 5), method=_SM)), STATE, dict(index=1, method=1), ['parameter_keys'],
    '[Seq]：子序列n的方法号（1–9存储方法，0当前方法，−1结束序列）')
cap('sequence.load', obj(dict(slot=enum(SEQ_SLOTS))), STATE, dict(slot='1'), ['parameter_keys'], '[Load][Seq]：载入存储的序列')
cap('sequence.store', obj(dict(slot=enum(SEQ_SLOTS))), STATE, dict(slot='1'), ['parameter_keys'], '[Store][Seq]：存储当前序列')
cap('sequence.delete', obj(dict(slot=enum(SEQ_SLOTS))), STATE, dict(slot='1'), ['parameter_keys'], '[Delete]：删除存储的序列')
cap('sequence.control', obj(dict(action=enum(['start', 'pause', 'resume', 'stop']))), STATE, dict(action='start'), ['parameter_keys'],
    '[Seq Control]：开始、暂停、继续、停止序列')
cap('clock.add_event', obj(dict(time_min=integer(0, 1439), time_hhmm=integer(0, 2359), action=enum(CLOCK_ACTIONS), slot=SLOT), optional=('slot', 'time_min', 'time_hhmm')), STATE,
    dict(time_min=17 * 60, action='load_method', slot='SLEEP'), ['parameter_keys'], '[Clock Table]：按一天中的时间执行事件（载入方法、Prep Run）')
cap('clock.delete_event', obj(dict(index=integer(1, 550))), STATE, dict(index=1), ['parameter_keys'], '[Clock Table]：删除事件')
cap('schedule.add', obj(dict(day=enum(DAYS), time_min=integer(0, 1439), time_hhmm=integer(0, 2359), function=enum(SCHEDULE_FUNCTIONS)), optional=('time_min', 'time_hhmm')), STATE,
    dict(day='Monday', time_min=7 * 60 + 30, function='wake_file'), ['parameter_keys'], '仪器日程：某天某时休眠或唤醒（当前方法、WAKE方法、先CONDITION）')
cap('schedule.delete', obj(dict(index=integer(1, 99))), STATE, dict(index=1), ['parameter_keys'], '仪器日程：删除条目')
cap('schedule.sleep_now', obj({}), STATE, {}, ['parameter_keys'], 'Go to sleep now：载入SLEEP方法')
cap('schedule.wake_now', obj(dict(mode=enum(['restore', 'wake', 'condition_restore', 'condition_wake']))), STATE, dict(mode='restore'), ['parameter_keys'],
    '立即唤醒：恢复休眠前方法、载入WAKE方法、或先运行CONDITION方法')
cap('config.oven', obj(dict(max_temperature_k=number('K', C0 + 70.0, C0 + 450.0), equilibration_time_s=number('s', 0.0, 999.99 * 60.0),
                            external_oven_mode=BOOL, slow_cool_down=BOOL, limit_ballistic_power=BOOL, ignore_ready=BOOL),
                       optional=('max_temperature_k', 'equilibration_time_s', 'external_oven_mode', 'slow_cool_down', 'limit_ballistic_power', 'ignore_ready')),
    STATE, dict(max_temperature_k=C0 + 325.0, equilibration_time_s=60.0), ['oven_heater'], '[Config][Oven]：最高温度、平衡时间、外部柱箱模式、慢速降温、限制满功率、Ignore Ready',
    'agent', 'configuration')
cap('config.inlet', obj(dict(gas_type=enum(GASES), ignore_ready=BOOL), optional=('gas_type', 'ignore_ready')), STATE, dict(gas_type='He'), ['carrier_epc'],
    '[Config][Front Inlet]：载气类型、Ignore Ready', 'agent', 'configuration')
cap('config.column', obj(dict(length_m=number('m', 0.0, 105.0), diameter_m=number('m', 0.0, 5.3e-4), film_m=number('m', 0.0, 5e-6),
                              inlet=enum(['front_inlet', 'unspecified']), outlet=enum(['front_detector', 'other']), outlet_pressure_pa=number('Pa', 0.0, 150 * PSI)),
                         optional=('length_m', 'diameter_m', 'film_m', 'inlet', 'outlet', 'outlet_pressure_pa')),
    STATE, dict(length_m=30.0, diameter_m=3.2e-4, film_m=2.5e-7), ['column'], '[Config][Col 1]：长度、内径、膜厚（0为未定义）、入口、出口与出口压力', 'agent', 'configuration')
cap('config.detector', obj(dict(makeup_gas_type=enum(['N2', 'He', 'ArMe']), ignore_ignitor=BOOL, ignore_ready=BOOL), optional=('makeup_gas_type', 'ignore_ignitor', 'ignore_ready')),
    STATE, dict(makeup_gas_type='N2'), ['makeup_epc', 'fid_igniter'], '[Config][Front Det]：尾吹气类型、Ignore Ignitor、Ignore Ready', 'agent', 'configuration')
cap('config.module', obj(dict(device=enum(['front_inlet', 'front_detector']), installed=BOOL)), STATE, dict(device='front_detector', installed=False),
    ['electronics'], '[Config][模块][Mode/Type]：Remove module / Install module（须解除硬件配置锁，重新开机后生效）', 'agent', 'configuration')
cap('config.time', obj(dict(utc_offset_min=integer(-720, 840), time_min=integer(0, 1439), date=TEXT, zone_hhmm=integer(-1200, 1400), time_hhmm=integer(0, 2359),
                            date_ddmmyy=integer(10100, 311299)),
                       optional=('utc_offset_min', 'time_min', 'date', 'zone_hhmm', 'time_hhmm', 'date_ddmmyy')),
    STATE, dict(time_min=9 * 60), ['electronics'], '[Config][Time]：时区、时间、日期（分钟或ISO日期；键盘格式hhmm、ddmmyy）', 'agent', 'configuration')
cap('config.instrument', obj(dict(auto_prep_run=BOOL, zero_init_data=BOOL, require_host=BOOL), optional=('auto_prep_run', 'zero_init_data', 'require_host')),
    STATE, dict(auto_prep_run=True), ['electronics'], '[Config] Instrument：Auto prep run、Zero Init Data Files、Require Host Connection', 'agent', 'configuration')
cap('config.status_order_begin', obj({}), STATE, {}, ['display'], '[Config][Status]：开始重新排列设定值状态表', 'agent', 'configuration')
cap('config.status_order_select', obj(dict(setpoint=enum(STATUS_SETPOINTS))), STATE, dict(setpoint='oven_temp'), ['display'],
    '[Config][Status]：选中的设定值排到下一个位置', 'agent', 'configuration')
cap('options.keyboard', obj(dict(keyboard_lock=BOOL, hard_config_lock=BOOL, key_click=BOOL, warning_beep=BOOL, warning_beep_mode=integer(1, 9),
                                 method_modified_beep=BOOL, pressure_units=enum(['psi', 'bar', 'kPa']), language=enum(['English', 'Chinese']),
                                 radix=enum(['1.00', '1,00']), display_saver=BOOL),
                            optional=('keyboard_lock', 'hard_config_lock', 'key_click', 'warning_beep', 'warning_beep_mode', 'method_modified_beep', 'pressure_units',
                                      'language', 'radix', 'display_saver')),
    STATE, dict(pressure_units='kPa'), ['parameter_keys', 'display'], '[Options] Keyboard and Display：键盘锁、硬件配置锁、按键音、警告音及其音色、方法修改提示音、压力单位、语言、小数点、屏保',
    'agent', 'configuration')
IPV4 = {'type': 'string', 'pattern': r'^(\d{1,3}\.){3}\d{1,3}$', 'maxLength': 15}
cap('options.communication', obj(dict(dhcp=BOOL, ip=IPV4, gateway=IPV4, subnet_mask=IPV4), optional=('dhcp', 'ip', 'gateway', 'subnet_mask')), STATE,
    dict(ip='192.168.0.26'), ['signal_port'], '[Options] Communications：DHCP、IP、网关、子网掩码（重新开机后生效）', 'agent', 'configuration')
cap('options.reboot', obj({}), STATE, {}, ['electronics'], 'Reboot GC：重新启动（应用LAN设定）', 'agent', 'device_command')
cap('calibration.configure', obj(dict(auto_flow_zero=BOOL, auto_zero_septum_purge=BOOL), optional=('auto_flow_zero', 'auto_zero_septum_purge')), STATE,
    dict(auto_flow_zero=True), ['carrier_epc'], '[Options] Calibration > Front inlet：每次运行后自动流量零点、自动隔垫吹扫零点', 'agent', 'configuration')
cap('calibration.zero', obj(dict(sensor=enum(['inlet_flow', 'septum_purge_flow', 'inlet_pressure']))), STATE, dict(sensor='inlet_flow'), ['carrier_epc'],
    '[Options] Calibration：传感器调零（流量传感器须有气流，压力传感器须断开供气）', 'agent', 'configuration')
cap('calibration.column', obj(dict(mode=enum(['length', 'diameter', 'length_and_diameter']), measured_flow_m3_s=number('m3/s', 1e-9, 1e-5),
                                   unretained_peak_s=number('s', 1.0, 3600.0)), optional=('measured_flow_m3_s', 'unretained_peak_s')),
    STATE, dict(mode='length', measured_flow_m3_s=1.0 * ML_MIN), ['column'], '[Options] Calibration > Column：由实测柱流量或不保留峰时间估算柱长或内径', 'agent', 'configuration')
cap('calibration.reset_column', obj({}), STATE, {}, ['column'], '色谱柱校准：Not calibrated（恢复配置尺寸）', 'agent', 'configuration')
cap('sensor.configure_error', obj(dict(inlet_flow_offset_m3_s=number('m3/s', -5 * ML_MIN, 5 * ML_MIN), septum_flow_offset_m3_s=number('m3/s', -1 * ML_MIN, 1 * ML_MIN),
                                       inlet_pressure_offset_pa=number('Pa', -0.5 * PSI, 0.5 * PSI)),
                                  optional=('inlet_flow_offset_m3_s', 'septum_flow_offset_m3_s', 'inlet_pressure_offset_pa')),
    STATE, dict(inlet_flow_offset_m3_s=0.5 * ML_MIN), ['carrier_epc'], '评测者：进样口流量、隔垫吹扫流量与压力传感器的零点误差（默认0即理想；调零后消除）', 'evaluator', 'simulation')
cap('emf.configure', obj(dict(counter=enum(sorted(EMF_DEFAULTS)), service_due=nullable(number('1', 1, 1e6)), service_warning=nullable(number('1', 1, 1e6))),
                         optional=('service_due', 'service_warning')),
    STATE, dict(counter='inlet.septum_injections', service_due=200, service_warning=150), ['electronics'],
    '[Service Mode] > Maintenance：EMF计数器的Service Due与Service warning限值（null为Off）', 'agent', 'configuration')
cap('emf.reset', obj(dict(counter=enum(sorted(EMF_DEFAULTS)))), STATE, dict(counter='inlet.septum_injections'), ['electronics'], 'EMF计数器清零（更换耗材后）')
cap('stopwatch.toggle', obj({}), STATE, {}, ['display'], '[Time]秒表行[Enter]：启动或停止秒表')
cap('stopwatch.reset', obj({}), STATE, {}, ['display'], '[Time]秒表行[Clear]：秒表清零')
cap('alarm.silence', obj({}), STATE, {}, ['parameter_keys'], '[Off/No]或[Clear]：停止警告音')
cap('fault.clear', obj(dict(parameter=enum(['oven', 'inlet_flow', 'h2_flow', 'flame']))), STATE, dict(parameter='inlet_flow'), ['parameter_keys'],
    '在闪烁Off的参数行按[Off/No]清除关断（之后[On/Yes]重新启用）')
cap('fault.inject', obj(dict(fault=enum(['oven_overtemp', 'ignitor_failure', 'inlet_leak']), active=BOOL)), STATE, dict(fault='ignitor_failure', active=True), ['chassis'],
    '评测者：注入或清除故障（柱箱过温热关断、点火器失效、进样口泄漏）', 'evaluator', 'simulation')
cap('inlet.leak_check', obj({}), STATE, {}, ['inlet_body'], '[Service Mode]：前进样口泄漏检查（压力衰减）')
cap('macro.record', obj(dict(key=integer(1, 2))), STATE, dict(key=1), ['key_prog'], '[Prog][User Key n]：开始录制按键宏（最多31键，再按Prog结束）')
cap('macro.finish', obj({}), STATE, {}, ['key_prog'], '[Prog]：结束录制')
cap('macro.play', obj(dict(key=integer(1, 2))), STATE, dict(key=1), ['key_user_1', 'key_user_2'], '[User Key n]：回放宏')
cap('logs.read', obj(dict(log=enum(['run', 'maintenance', 'event']))), obj(dict(entries=array(FREE))), dict(log='event'), ['display'], '[Logs]：运行日志、维护日志、系统事件日志',
    'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(process=FREE)), {}, ['chassis'], '评测者：旧过程模型的完整观测（实际柱流量与压力、质量账、样品真值）', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 7 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), ['chassis'], '推进宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'injector.configure': '自动进样器（Front/Back Injector、Sample Tray）：本配置为手动针筒进样，没有ALS。',
    'valve.set': '阀（Valve #、Run Table阀事件、阀子序列）：本配置没有阀箱与阀。',
    'cryo.configure': '柱箱或进样口冷媒（Cryo）：本配置没有冷媒阀，仪器不接受冷媒设定。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['chassis'], _v)


def _panel_engine():
    try:
        from . import _panel_ui as engine
        return engine
    except ImportError:
        import importlib.util
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


_panel_ui = _panel_engine()
PANEL_SPEC = _panel_ui.load_spec(ASSET_DIR / 'panel_ui.json', capabilities=set(CAPS))
PANEL_CAPS = _panel_ui.capability_definitions(PANEL_SPEC)
ALL_CAPS = dict(CAPS, **PANEL_CAPS)


# ---------------------------------------------------------------- process: the legacy GC with switchable heaters and firmware flow control
class SwitchableZone(legacy.ThermalZone):
    """A heater zone the firmware can switch off (setpoint Off or a shutdown); limit / slow-cool factors from the oven configuration."""
    on = True
    power_factor = 1.0
    cool_factor = 1.0

    def advance(self, dt_s, ambient_K, enabled, open_door=False):
        if self.cool_factor != 1.0 and not open_door and self.temperature_K > self.setpoint_K + 1.0:
            g0 = self.conductance_W_K
            self.conductance_W_K = g0 * self.cool_factor                    # slow oven cool down: reduced fan speed while cooling
            try:
                return super().advance(dt_s, ambient_K, False, open_door)
            finally:
                self.conductance_W_K = g0
        if self.power_factor != 1.0:
            p0 = self.maximum_power_W
            self.maximum_power_W = p0 * self.power_factor
            try:
                return super().advance(dt_s, ambient_K, enabled and self.on, open_door)
            finally:
                self.maximum_power_W = p0
        return super().advance(dt_s, ambient_K, enabled and self.on, open_door)


class FirmwareGC(legacy.GC):
    """The process model; the firmware (GC7890BRuntime) runs before and after every integration step and decides the flows and readiness."""

    def __init__(self, fw):
        self.fw = fw
        super().__init__()
        for name, z in list(self.zones.items()):
            self.zones[name] = SwitchableZone(**dataclasses.asdict(z))

    def flows(self):
        f = super().flows()
        return self.fw._flows(f) if self.fw is not None else f

    def step(self, dt_s):
        out = super().step(dt_s)
        if self.fw is not None:
            self.fw._analog_tick(dt_s)
        return out

    def _thermal_ready(self):
        return all(abs(z.temperature_K - z.setpoint_K) <= self.method.temperature_tolerance_K for name, z in self.zones.items()
                   if self.fw is None or self.fw._zone_counts(name))

    def not_ready_reasons(self):
        r = super().not_ready_reasons()
        return self.fw._readiness(r) if self.fw is not None else r

    def _advance(self, dt_s):
        if self.fw is not None:
            self.fw._before(dt_s)
        charge = super()._advance(dt_s)
        if self.fw is not None:
            self.fw._after(dt_s)
        return charge


def _default_method():
    m = legacy.Method()
    return dict(
        oven=dict(temp_k=m.oven_temperature_K, on=True, initial_time_s=0.0, ramps=[dict(rate=0.0, final=m.oven_temperature_K, final_time=0.0) for _ in range(N_OVEN_RAMPS)],
                  post_time_s=0.0, post_temp_k=m.oven_temperature_K),
        inlet=dict(temp_k=m.inlet_temperature_K, on=True, gas_on=True, mode=m.mode, split_ratio=m.split_ratio, purge_flow=m.purge_flow_m3_s, purge_time_s=m.purge_time_s,
                   septum_purge=m.septum_purge_flow_m3_s, gas_saver=False, saver_flow=20 * ML_MIN, saver_time_s=120.0, pulse_pressure=25 * PSI, pulse_time_s=30.0),
        column=dict(mode='constant_flow', flow=m.column_flow_m3_s, pressure=None, initial_time_s=0.0,
                    ramps=[dict(rate=0.0, final=0.0, final_time=0.0) for _ in range(N_COL_RAMPS)]),
        detector=dict(temp_k=m.fid_temperature_K, on=True, h2=m.hydrogen_flow_m3_s, air=m.air_flow_m3_s, makeup=m.makeup_flow_m3_s, h2_on=True, air_on=True,
                      makeup_on=True, flame=False, electrometer=True, lit_offset=2e-12),
        analog={'1': dict(signal='front_detector', fast_peaks=False), '2': dict(signal='none', fast_peaks=False)},
        config=dict(oven_max_k=C0 + 450.0, equilibration_s=0.0, external_oven=False, slow_cool=False, limit_power=False, oven_ignore_ready=False,
                    inlet_gas='He', inlet_ignore_ready=False, col_length=m.column_length_m, col_diameter=m.column_inner_diameter_m, col_film=m.column_film_thickness_m,
                    col_inlet='front_inlet', col_outlet='front_detector', col_outlet_pa=0.0, makeup_gas='N2', ignore_ignitor=False, det_ignore_ready=False))


class GC7890BRuntime:
    def __init__(self, instance_id='agilent_7890b_ssl_fid_01'):
        self.instance_id = instance_id
        self.gc = FirmwareGC(None)
        self.plant = dict(pressure_pa=P_NTP, carrier_gas='He', makeup_gas='N2', fid_background_a=FID_BACKGROUND_A)
        self.m = _default_method()                                         # active method (kept over a power cycle, p.40)
        self.methods = {s: None for s in METHOD_SLOTS}
        for s in ('SLEEP', 'WAKE', 'CONDITION'):                           # assumption: factory copies of the default method
            self.methods[s] = copy.deepcopy(self.m)
        self.seq = self._empty_sequence()
        self.sequences = {s: None for s in SEQ_SLOTS}
        self.seq_status = 'no_sequence'
        self.clock_events, self.schedule = [], []
        self.opt = dict(keyboard_lock=False, hard_config_lock=True, key_click=True, warning_beep=True, warning_beep_mode=1, method_modified_beep=False,
                        pressure_units='psi', language='English', radix='1.00', display_saver=False)
        self.comm = dict(dhcp=False, ip='10.1.1.101', gateway='10.1.1.1', subnet_mask='255.255.255.0')
        self.comm_pending = None
        self.cal = dict(auto_flow_zero=False, auto_zero_septum_purge=False, flow_zero=0.0, septum_zero=0.0, pressure_zero=0.0, column=None, column_mode='not_calibrated')
        self.sensor_err = dict(inlet_flow_offset_m3_s=0.0, septum_flow_offset_m3_s=0.0, inlet_pressure_offset_pa=0.0)
        self.instrument = dict(auto_prep_run=False, zero_init_data=False, require_host=False)
        self.analog_y = {}                                                 # output -> (signal, filtered value A) of the analog low pass (p.169)
        self.clock_offset_s, self.utc_offset_min = 0.0, 0
        self.status_order = list(STATUS_SETPOINTS)
        self.status_pos = 0
        self.modules = dict(front_inlet=True, front_detector=True)          # configured now
        self.modules_pending = None                                        # applied at the next power-up (p.143)
        self.emf = {k: dict(value=0.0, due=None, warning=None, since=0.0, modified=None, flagged=False) for k in EMF_DEFAULTS}
        self.logs = dict(run=[], maintenance=[], event=[])
        self.faults = set()
        self.shutdown = {}                                                 # parameter -> dict(message, safety, blink)
        self.alarm = 'none'                                                # none / single / series / continuous (alert tones, p.12-13)
        self.sound = dict(key_clicks=0, method_beeps=0)
        self.run = dict(state='idle', post_start=None, last_run_s=None, condition=None, end_requested=False)
        self.prep = False
        self.saver_since = None
        self.flame_tries, self.flame_try_t = 0, None
        self.flow_lost_since = None
        self.oven_band_since = None
        self.sleeping, self.pre_sleep = False, None
        self.stopwatch = dict(running=False, start=None, acc=0.0)
        self.macros = {1: [], 2: []}
        self.comp = dict(profile=None, recording=None)                    # column compensation profile [(run time s, output A)]
        self.macro_rec = None
        self.macro_queue = []
        self.last_key_t = 0.0
        self.message = None                                                # last firmware message (e.g. 'Power on successful')
        self.power_epoch = 0
        self.revision = 0
        self.events_dropped = 0
        self._principals, self._requests, self._caller = {}, {}, None
        self.run_owner = None                                             # the agent that started the active run (None: clock table, evaluator)
        self.panel_capture_dir = None
        self.gc.fw = self
        self._sync_legacy()
        self.panel = _panel_ui.PanelUI(PANEL_SPEC, self, hooks={})

    # ---- clock and logs
    @property
    def t(self):
        return self.gc.time_s

    @property
    def time_s(self):
        return self.gc.time_s

    def _clock(self):
        return EPOCH + datetime.timedelta(seconds=self.clock_offset_s + self.gc.time_s)

    def _stamp(self):
        return self._clock().strftime('%d %b %y %H:%M')

    def _log(self, log, text):
        L = self.logs[log]
        L.append(dict(time_s=round(self.t, 3), clock=self._stamp(), text=text))
        if len(L) > LOG_MAX:
            del L[:-LOG_MAX]
            self.events_dropped += 1

    def _event(self, text):
        self._log('event', text)

    # ---- sessions
    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator', 'debug'):
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return GC7890BSession(self, name)

    # ---- state helpers
    def _on(self):
        return self.gc.powered

    def _need_on(self):
        if not self.gc.power_connected:
            raise ActionRejected('MAINS_OFF')
        if not self.gc.powered:
            raise ActionRejected('POWER_OFF')

    def _need_module(self, dev):
        if not self.modules[dev]:
            raise ActionRejected('MODULE_UNCONFIGURED')

    def _need_unlocked_config(self):
        if self.opt['hard_config_lock']:
            raise ActionRejected('CONFIG_LOCKED')

    def _setpoint_changed(self, what):
        """A method setpoint was changed: method-modified beep, run-log entry during a run (p.35: keypad intervention is a deviation)."""
        if self.opt['method_modified_beep']:
            self.sound['method_beeps'] += 1
        if self.run['state'] == 'run':
            self._log('run', f'Setpoint changed during run: {what}')

    def _zone_counts(self, legacy_name):
        """Does a heater zone count for readiness: not Off, not shut down, its element not Ignore Ready, its module configured."""
        z = {'oven': 'oven', 'inlet': 'inlet', 'fid': 'detector'}[legacy_name]
        if z == 'oven':
            return self.m['oven']['on'] and 'oven' not in self.shutdown and not self.m['config']['oven_ignore_ready']
        if z == 'inlet':
            return self.m['inlet']['on'] and self.modules['front_inlet'] and not self.m['config']['inlet_ignore_ready']
        return self.m['detector']['on'] and self.modules['front_detector'] and not self.m['config']['det_ignore_ready']

    def _readiness(self, reasons):
        """Firmware readiness (legacy reason codes kept, filtered by Ignore Ready / Off zones / unconfigured modules, plus shutdowns and the
        oven equilibration time)."""
        c = self.m['config']
        out = []
        for r in reasons:
            if r in ('NO_CARRIER_FLOW',) and (c['inlet_ignore_ready'] or not self.modules['front_inlet']):
                continue
            if r in ('NO_HYDROGEN_FLOW', 'NO_AIR_FLOW', 'NO_MAKEUP_FLOW', 'FID_FLAME_OFF') and (c['det_ignore_ready'] or not self.modules['front_detector']):
                continue
            d = self.m['detector']
            if (r == 'NO_MAKEUP_FLOW' and not d['makeup_on']) or (r == 'NO_HYDROGEN_FLOW' and not d['h2_on'] and 'flame' not in self.shutdown) or \
                    (r == 'NO_AIR_FLOW' and not d['air_on']) or (r == 'FID_FLAME_OFF' and not d['flame'] and 'flame' not in self.shutdown):
                continue                                                  # a detector gas or the flame switched Off on purpose: not waited for
            out.append(r)
        if self.gc.powered and 'TEMPERATURE_NOT_READY' not in out and self.m['oven']['on'] and not c['oven_ignore_ready'] and c['equilibration_s'] > 0:
            if self.oven_band_since is None or self.t - self.oven_band_since < c['equilibration_s'] - 1e-9:
                out.append('OVEN_EQUILIBRATING')
        if self.gc.powered and self.instrument['require_host']:
            out.append('HOST_NOT_READY')                                  # p.183: no data system is connected, so no host ever reports Ready
        if self.shutdown:
            out.append('SHUTDOWN')
        if self.sleeping:
            out.append('SLEEPING')
        return out

    # ---- firmware flow control
    def _col_temp(self):
        if self.m['config']['external_oven']:
            return self.m['oven']['temp_k']                               # isothermal internal oven used for the flow calculation (p.144)
        return self.gc.zones['oven'].temperature_K

    def _defined(self):
        c = self.m['config']
        return c['col_length'] > 0 and c['col_diameter'] > 0

    def _cal_dims(self):
        c = self.m['config']
        cal = self.cal['column'] or {}
        return cal.get('length', c['col_length']), cal.get('diameter', c['col_diameter'])

    def _p_out(self):
        c = self.m['config']
        return self.plant['pressure_pa'] + (c['col_outlet_pa'] if c['col_outlet'] == 'other' else 0.0)

    def _run_t(self):
        return self.gc.run_time_s if self.run['state'] in ('run', 'condition', 'blank') else 0.0

    def _col_setpoint(self):
        """(pressure_abs set by the controller, firmware flow belief) at this instant."""
        col, c = self.m['column'], self.m['config']
        T = self._col_temp()
        L, d = self._cal_dims()
        gas = c['inlet_gas']
        p_out = self._p_out()
        t = self._run_t()
        if col['mode'] in ('constant_flow', 'ramped_flow'):
            F = col['flow'] if col['mode'] == 'constant_flow' else program_value(col['flow'], col['initial_time_s'], col['ramps'], t)[0]
            p = head_pressure(F, p_out, L, d, gas, T) if self._defined() else p_out
        else:
            pg = col['pressure'] if col['mode'] == 'constant_pressure' else program_value(col['pressure'], col['initial_time_s'], col['ramps'], t)[0]
            p = p_out + (pg or 0.0)
        inl = self.m['inlet']
        if inl['mode'].startswith('pulsed') and self.run['state'] == 'run' and t < inl['pulse_time_s']:
            p = max(p, self.plant['pressure_pa'] + inl['pulse_pressure'])
        belief = column_flow(p, p_out, L, d, gas, T) if self._defined() else None
        return p, belief

    def _actual_column(self):
        """Actual head pressure (controller set point less the uncorrected pressure-sensor error) and the actual NTP column flow."""
        p_set, _ = self._col_setpoint()
        err = self.sensor_err['inlet_pressure_offset_pa'] - self.cal['pressure_zero']
        p = max(p_set - err, self._p_out())
        lm = self.gc.method
        return p, column_flow(p, self._p_out(), lm.column_length_m, lm.column_inner_diameter_m, self.plant['carrier_gas'], self.gc.zones['oven'].temperature_K)

    def _inlet_live(self):
        return self.gc.powered and self.modules['front_inlet'] and self.m['inlet']['gas_on'] and 'inlet_flow' not in self.shutdown

    def _flows(self, f):
        """Legacy flows with the firmware's valves: EPC Off / shutdown, gas saver, purge valve during Prep Run, flow sensor zero errors,
        detector gas channels, makeup gas type."""
        f = dict(f)
        if not self._inlet_live():
            f['column'] = f['split_vent'] = f['septum_purge'] = 0.0
        if f['column'] > 0:
            inl = self.m['inlet']
            if inl['mode'] in ('splitless', 'pulsed_splitless') and self.prep and self.gc.injection_time_s is None:
                f['split_vent'] = 0.0                                     # Prep Run closes the purge valve before a splitless injection
            if self.saver_since is not None:
                f['split_vent'] = min(f['split_vent'], max(0.0, inl['saver_flow'] - f['column'] - f['septum_purge']))
            if f['split_vent'] > 0:
                f['split_vent'] = max(0.0, f['split_vent'] - (self.sensor_err['inlet_flow_offset_m3_s'] - self.cal['flow_zero']))
            f['septum_purge'] = max(0.0, f['septum_purge'] - (self.sensor_err['septum_flow_offset_m3_s'] - self.cal['septum_zero']))
        else:
            f['split_vent'] = f['septum_purge'] = 0.0
        d = self.m['detector']
        det = self.modules['front_detector'] and self.gc.powered
        f['hydrogen'] = f['hydrogen'] if det and d['h2_on'] and 'h2_flow' not in self.shutdown else 0.0
        f['air'] = f['air'] if det and d['air_on'] else 0.0
        mk = d['makeup'] * eta(self.m['config']['makeup_gas'], T_NTP) / eta(self.plant['makeup_gas'], T_NTP)   # restrictor flow scales with 1/viscosity
        f['makeup'] = mk if det and d['makeup_on'] and f['makeup'] > 0 else 0.0
        return f

    def _sync_legacy(self):
        """Firmware setpoints -> legacy method and zones (setpoint changes reset the legacy stability timer only when a value changes)."""
        g, m = self.gc, self.m
        inl, d = m['inlet'], m['detector']
        _, F = self._actual_column()
        mode = 'split' if inl['mode'] in ('split', 'pulsed_split') else 'splitless'
        fields = dict(mode=mode, split_ratio=inl['split_ratio'], purge_time_s=inl['purge_time_s'], purge_flow_m3_s=inl['purge_flow'],
                      septum_purge_flow_m3_s=inl['septum_purge'], hydrogen_flow_m3_s=d['h2'], air_flow_m3_s=d['air'], makeup_flow_m3_s=d['makeup'],
                      column_flow_m3_s=max(F, 1e-15))
        cur = g.method
        if any(getattr(cur, k) != v for k, v in fields.items()):
            g.method = dataclasses.replace(cur, **fields)
        g.zones['inlet'].setpoint_K = inl['temp_k']
        g.zones['fid'].setpoint_K = d['temp_k']
        g.zones['oven'].on = m['oven']['on'] and 'oven' not in self.shutdown
        g.zones['inlet'].on = inl['on'] and self.modules['front_inlet']
        g.zones['fid'].on = d['on'] and self.modules['front_detector']
        c = m['config']
        g.zones['oven'].power_factor = 0.8 if c['limit_power'] else 1.0    # assumption: 80 % of the heater power at the maximum rate
        g.zones['oven'].cool_factor = 0.5 if c['slow_cool'] else 1.0       # assumption: half the cooling conductance (fan slowed)
        g.zones['oven'].setpoint_K = self._oven_setpoint()

    def _oven_setpoint(self):
        o = self.m['oven']
        if self.run['state'] in ('run', 'condition', 'blank'):
            return program_value(o['temp_k'], o['initial_time_s'], [dict(r) for r in o['ramps']], self.gc.run_time_s)[0]
        if self.run['state'] == 'post' and o['post_time_s'] > 0:
            return o['post_temp_k']
        return o['temp_k']

    def _program_length(self, method=None):
        o = (method or self.m)['oven']
        return program_length(o['temp_k'], o['initial_time_s'], o['ramps'])

    def _program_phase(self):
        o = self.m['oven']
        if self.run['state'] not in ('run', 'condition', 'blank'):
            return None
        return program_value(o['temp_k'], o['initial_time_s'], o['ramps'], self.gc.run_time_s)[1]

    # ---- the firmware between integration steps
    def _before(self, h):
        self._sync_legacy()

    def _after(self, h):
        g = self.gc
        if not g.powered:
            return
        oven = g.zones['oven']
        if abs(oven.temperature_K - oven.setpoint_K) <= g.method.temperature_tolerance_K and not g.door_open:
            if self.oven_band_since is None:
                self.oven_band_since = self.t
        else:
            self.oven_band_since = None
        # inlet flow: no carrier at the inlet for 2 min -> shutdown (hydrogen carrier: safety shutdown)
        if self._inlet_live() and not g.gases['carrier']:
            if self.flow_lost_since is None:
                self.flow_lost_since = self.t
                self.alarm = 'series'
            elif self.t - self.flow_lost_since >= FLOW_SHUTDOWN_S - 1e-9:
                self._shutdown('inlet_flow', 'Front inlet flow shutdown', safety=self.plant['carrier_gas'] == 'H2' or self.m['config']['inlet_gas'] == 'H2')
        else:
            self.flow_lost_since = None
        # FID: the GC compares the output with the lit offset and re-ignites; after failed tries it shuts the hydrogen off (p.167)
        d = self.m['detector']
        if d['flame'] and self.modules['front_detector'] and not self.m['config']['ignore_ignitor'] and d['electrometer'] and 'flame' not in self.shutdown:
            f = g.flows()
            can = f['hydrogen'] > 0 and f['air'] > 0 and g.zones['fid'].temperature_K >= g.method.fid_ignition_minimum_K
            if self._fid_output() < d['lit_offset'] - 1e-18:
                if can and (self.flame_try_t is None or self.t - self.flame_try_t >= REIGNITE_EVERY_S - 1e-9):
                    self.flame_tries += 1
                    self.flame_try_t = self.t
                    self.emf['detector.ignitor']['value'] += 1
                    self._try_ignite()
                    if self.flame_tries > REIGNITE_TRIES and self._fid_output() < d['lit_offset'] - 1e-18:
                        self._shutdown('flame', 'Front det flame out', safety=False)
                        d['h2_on'] = False
                        g.flame_lit = False
            else:
                self.flame_tries = 0
        if self.saver_since is None and self.m['inlet']['gas_saver'] and g.injection_time_s is not None and not self.prep \
                and self.t - g.injection_time_s >= self.m['inlet']['saver_time_s'] - 1e-9:
            self.saver_since = self.t
        # end of run, post run, condition run
        st = self.run['state']
        if st == 'blank':
            self.comp['recording'].append((g.run_time_s, self._fid_output()))
        if st in ('run', 'condition', 'blank'):
            L = self._program_length()
            if (L > 0 and g.run_time_s >= L - 1e-9) or g.run_time_s >= MAX_RUN_S:
                self._end_run()
        elif st == 'post' and self.t - self.run['post_start'] >= self.m['oven']['post_time_s'] - 1e-9:
            self.run['state'] = 'idle'
            self._event('Post run complete')
        if self.instrument['auto_prep_run'] and st == 'idle' and not self.prep and not self.sleeping and not g.not_ready_reasons():
            self.prep = True
        self._emf_tick(h)
        if self.clock_events or self.schedule:
            self._timed_events(h)

    def _try_ignite(self):
        if 'ignitor_failure' in self.faults:
            return False
        try:
            self.gc.perform('ignite')
            return True
        except legacy.ActionRejected:
            return False

    def _fid_output(self):
        """Electrometer output (A): analyte current plus the flame background while lit; 0 with the electrometer off."""
        g = self.gc
        if not (g.powered and self.m['detector']['electrometer'] and self.modules['front_detector']):
            return 0.0
        return (g.fid_current_A + self.plant['fid_background_a']) if g.flame_lit else 0.0

    def _shutdown(self, param, message, safety):
        if param in self.shutdown:
            return
        self.shutdown[param] = dict(message=message, safety=safety)
        self.alarm = 'continuous' if safety or param in ('flame', 'h2_flow', 'oven') else 'single'
        self._event(message)
        if self.run['state'] == 'run':
            self._log('run', message)
        if safety:                                                        # hydrogen / thermal safety shutdown: heated zones and gases off
            self.shutdown.setdefault('oven', dict(message=message, safety=True))
            self.m['detector']['flame'] = False
            self.gc.flame_lit = False

    def _end_run(self):
        g = self.gc
        cond = self.run['state'] == 'condition'
        if self.run['state'] == 'blank':
            g.perform('stop')
            self.comp.update(profile=self.comp['recording'], recording=None)
            self.run['state'] = 'idle'
            self._event('Column compensation profile stored')
            return
        g.perform('stop')
        self.run['last_run_s'] = g.run_time_s
        self.emf['column.oven_cycles']['value'] += 1
        if self.cal['auto_flow_zero']:
            self.cal['flow_zero'] = self.sensor_err['inlet_flow_offset_m3_s']
        if self.cal['auto_zero_septum_purge']:
            self.cal['septum_zero'] = self.sensor_err['septum_flow_offset_m3_s']
        self._event('Run complete' if not cond else 'Condition run complete')
        if cond:
            target = self.run['condition']
            self.run.update(state='idle', condition=None)
            self._apply_method(copy.deepcopy(self.pre_sleep) if target == 'restore' or self.methods['WAKE'] is None else copy.deepcopy(self.methods['WAKE']))
            self.sleeping, self.pre_sleep = False, None
            return
        if self.m['oven']['post_time_s'] > 0:
            self.run.update(state='post', post_start=self.t)
        else:
            self.run['state'] = 'idle'

    def _emf_tick(self, h):
        days = h / 86400.0
        for k in ('inlet.liner_time', 'inlet.oring_time', 'inlet.goldseal_time', 'inlet.trap_time', 'instrument.on_time'):
            self.emf[k]['value'] += days
        for k, e in self.emf.items():
            due = e['due'] is not None and e['value'] >= e['due']
            if due and not e['flagged']:
                e['flagged'] = True
                self._log('maintenance', f'{EMF_DEFAULTS[k][0]} {EMF_DEFAULTS[k][1]}: Service Due limit {e["due"]:g} reached')

    def _timed_events(self, h):
        """Clock table and instrument schedule: events whose time of day was crossed in this step (GC clock)."""
        now = self._clock()
        before = now - datetime.timedelta(seconds=h)

        def crossed(day, minute):
            t0 = before.replace(hour=0, minute=0, second=0, microsecond=0)
            for k in range(2):
                d = t0 + datetime.timedelta(days=k)
                at = d + datetime.timedelta(minutes=minute)
                if before < at <= now and (day is None or DAYS[d.weekday()] == day):
                    return True
            return False
        for e in list(self.clock_events):
            if crossed(None, e['time_min']):
                if e['action'] == 'load_method' and self.methods.get(e['slot']) is not None:
                    self._apply_method(copy.deepcopy(self.methods[e['slot']]))
                    self._event(f'Clock table: method {e["slot"]} loaded')
                elif e['action'] == 'prep_run':
                    self.prep = True
                    self._event('Clock table: Prep Run')
        for e in list(self.schedule):
            if crossed(e['day'], e['time_min']):
                if e['function'] == 'sleep':
                    self._sleep()
                elif self.sleeping:
                    self._wake({'wake_current': 'restore', 'wake_file': 'wake', 'condition_current': 'condition_restore',
                                'condition_file': 'condition_wake'}[e['function']])

    # ---- methods
    def _method_snapshot(self):
        snap = copy.deepcopy(self.m)
        snap['modules'] = dict(self.modules)
        return snap

    def _apply_method(self, snap):
        snap = copy.deepcopy(snap)
        mods = snap.pop('modules', None)
        mismatch = mods is not None and mods != self.modules
        default = _default_method()
        if mismatch:                                                      # p.41-42: parameters of a changed module reset to defaults
            for dev, part in (('front_inlet', 'inlet'), ('front_detector', 'detector')):
                if mods.get(dev) != self.modules[dev]:
                    snap[part] = default[part]
            self.message = 'Method / hardware mismatch'
            self._event('Method / hardware mismatch')
        self.m = snap
        self.gc.stable_time_s = 0.0
        if self.m['detector']['flame'] and not self.gc.flame_lit and self.gc.powered:
            self._try_ignite()
        if not self.m['detector']['flame']:
            self.gc.flame_lit = False
        self.oven_band_since = None
        self._sync_legacy()

    # ---- run control
    def _start(self):
        g = self.gc
        try:
            g.perform('start')
        except legacy.ActionRejected as exc:
            raise ActionRejected('RUN_ACTIVE' if 'already' in str(exc) else 'NOT_READY') from None
        self.run.update(state='run', end_requested=False)
        self.run_owner = self._starter()
        self.prep = False
        self.saver_since = None
        self.logs['run'] = []                                             # p.35: cleared at the start of each new run
        self.emf['instrument.run_count']['value'] += 1
        self._event('Run started')

    def _sleep(self):
        if self.sleeping:
            return
        self.pre_sleep = self._method_snapshot()
        if self.methods['SLEEP'] is not None:
            snap = copy.deepcopy(self.methods['SLEEP'])
            for r in snap['oven']['ramps']:                                 # p.114: the GC ignores ramps in a SLEEP method
                r['rate'] = 0.0
            self._apply_method(snap)
        self.sleeping = True
        self._event('GC asleep')

    def _wake(self, mode):
        if mode in ('restore', 'wake'):
            target = self.pre_sleep if mode == 'restore' else (self.methods['WAKE'] or self.pre_sleep)
            if target is not None:
                snap = copy.deepcopy(target)
                if mode == 'wake':
                    for r in snap['oven']['ramps']:
                        r['rate'] = 0.0
                self._apply_method(snap)
            self.sleeping, self.pre_sleep = False, None
            self._event('GC awake')
            return
        if self.methods['CONDITION'] is None:
            raise ActionRejected('METHOD_EMPTY')
        self._apply_method(copy.deepcopy(self.methods['CONDITION']))
        g = self.gc
        g.running = True                                                  # a blank run: no injection, no data (p.109, p.114)
        g.run_time_s = g.fid_integral_C = 0.0
        g.injection_time_s = None
        self.run.update(state='condition', condition='restore' if mode == 'condition_restore' else 'wake')
        self.run_owner = self._starter()
        self._event('Condition run started')

    # ---- run ownership (lead rule 2026-10-19; the real GC has no caller concept)
    def _starter(self):
        """The agent whose command (or panel key) starts a run owns it; runs started by the clock table / schedule inside sim.step have no owner."""
        return self._caller if self._principals.get(self._caller) == 'agent' else None

    def _foreign_run(self, capability_id, args):
        """True when the calling agent would stop or change a run (run, post run, blank or condition run) another agent started.  Physical
        actions (the main power switch and the mains plug, lead clarification 10-19; oven door, gases, syringe) and the evaluator are not
        restricted; the GC reacts through its own rules (power off ends the run)."""
        caller = self._caller
        if self.run_owner is None or caller is None or caller == self.run_owner or self._principals.get(caller) != 'agent':
            return False
        if self.run['state'] not in ('run', 'post', 'blank', 'condition'):
            return False
        return capability_id in RUN_GUARDED

    # ---- capability dispatch
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        if self._foreign_run(capability_id, args):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id not in ('logs.read', 'truth.inspect', 'panel.capture', 'panel.frame', 'sim.step') and not capability_id.startswith('panel.'):
            self.message = None                                           # display.message: the message of the last command
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**args)
        if capability_id not in ('logs.read', 'truth.inspect', 'panel.capture', 'panel.frame'):
            self.revision += 1
            self._sync_legacy()
        return out if out is not None else dict(state=self.summary())

    def _legacy(self, action, **kw):
        try:
            self.gc.perform(action, **kw)
        except legacy.ActionRejected as exc:
            text = str(exc)
            code = ('MAINS_OFF' if 'power_inlet' in text else 'SYRINGE_IN_INLET' if 'at the vial' in text else 'VIAL_EMPTY' if 'vial is empty' in text
                    else 'SYRINGE_INSERTED' if 'already inserted' in text else 'SYRINGE_EMPTY' if 'Syringe is empty' in text
                    else 'INJECTION_NOT_READY' if 'Injection requires' in text else 'IGNITION_FAILED' if 'FID' in text else 'DEVICE_ERROR')
            raise ActionRejected(code) from None
        except ValueError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _cmd_plant_configure(self, **kw):
        for k in ('pressure_pa', 'carrier_gas', 'makeup_gas', 'fid_background_a'):
            if k in kw:
                self.plant[k] = kw[k]
        if 'ambient_k' in kw:
            self.gc.ambient_K = kw['ambient_k']
        lm = self.gc.method
        dims = dict(column_length_m=kw.get('column_length_m', lm.column_length_m), column_inner_diameter_m=kw.get('column_diameter_m', lm.column_inner_diameter_m),
                    column_film_thickness_m=kw.get('column_film_m', lm.column_film_thickness_m))
        try:
            self.gc.method = dataclasses.replace(lm, **dims)
        except ValueError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _cmd_power_connect_mains(self, connected):
        was = self.gc.powered
        self._legacy('plug_power', connected=connected)
        if was and not self.gc.powered:
            self._powered_off()

    def _cmd_power_set(self, on):
        was = self.gc.powered
        self._legacy('power', enabled=on)
        if on and not was:
            self._power_up()
        elif was and not on:
            self._powered_off()

    def _powered_off(self):
        self.run.update(state='idle', condition=None)
        self.prep = False
        self.saver_since = None
        if self.seq_status in ('running', 'ready_wait', 'paused'):
            self.seq_status = 'aborted'
        self.alarm = 'none'
        self.flame_tries = 0
        self.analog_y = {}

    def _power_up(self):
        self.power_epoch += 1
        self.opt['hard_config_lock'] = True                               # p.142: unlocked until the GC is power cycled
        if self.comm_pending:
            self.comm.update(self.comm_pending)
            self.comm_pending = None
        if self.modules_pending:
            self.modules.update(self.modules_pending)
            self.modules_pending = None
            self._event('Configuration changed: check the methods (default method updated)')
        self.shutdown = {k: v for k, v in self.shutdown.items() if not v['safety']}   # safety shutdowns clear with a power cycle
        if 'oven_overtemp' in self.faults:
            self.shutdown.setdefault('oven', dict(message='Oven thermal shutdown', safety=True))
        self.message = 'Power on successful'
        self._event('Power on successful')
        if self.m['detector']['flame']:
            self._try_ignite()

    def _cmd_gas_connect(self, gas, connected):
        self._legacy('connect_gas', gas=gas, connected=connected)

    def _cmd_oven_set_door(self, open):
        self._legacy('door', open=open)

    def _cmd_sample_aspirate(self, fraction):
        self._legacy('aspirate', fraction=fraction)

    def _cmd_syringe_insert(self):
        self._legacy('insert_syringe')

    def _cmd_syringe_depress(self, fraction=1.0):
        self._legacy('depress_plunger', fraction=fraction)

    def _cmd_syringe_retract(self):
        self._legacy('retract_syringe')

    def _cmd_run_prep(self):
        self._need_on()
        if self.run['state'] in ('run', 'condition', 'blank'):
            raise ActionRejected('RUN_ACTIVE')
        self.prep = True
        self.saver_since = None                                           # p.24: Prep Run restores normal flow from gas saver mode
        self._event('Prep Run')

    def _cmd_run_start(self):
        self._need_on()
        if self.sleeping:
            raise ActionRejected('NOT_READY')
        self._start()

    def _cmd_run_stop(self):
        self._need_on()
        if self.run['state'] in ('run', 'condition', 'blank'):
            self.gc.perform('stop')
            self.run['last_run_s'] = self.gc.run_time_s
            self.run['state'] = 'idle'
            self._event('Run stopped')
        self.prep = False
        if self.seq_status in ('running', 'ready_wait', 'paused'):
            self.seq_status = 'aborted'
            self._event('Sequence aborted')

    def _cmd_thermal_set_temperature(self, zone, temperature_k):
        self._need_on()
        if zone != 'oven':
            self._need_module('front_inlet' if zone == 'inlet' else 'front_detector')
        lim = min(ZONE_MAX_K[zone], self.m['config']['oven_max_k']) if zone == 'oven' else ZONE_MAX_K[zone]
        if temperature_k > lim + 1e-9:
            raise ActionRejected('ABOVE_MAXIMUM')
        part = {'oven': 'oven', 'inlet': 'inlet', 'detector': 'detector'}[zone]
        if part == 'oven' and 'oven' in self.shutdown:
            raise ActionRejected('SHUTDOWN')
        self.m[part]['temp_k'] = temperature_k
        self.m[part]['on'] = True
        self.gc.stable_time_s = 0.0
        self._setpoint_changed(f'{zone} temperature')

    def _cmd_thermal_set_zone(self, zone, on):
        self._need_on()
        if zone != 'oven':
            self._need_module('front_inlet' if zone == 'inlet' else 'front_detector')
        if zone == 'oven' and on and 'oven' in self.shutdown:
            if self.shutdown['oven']['safety'] or 'oven_overtemp' in self.faults:
                raise ActionRejected('POWER_CYCLE_REQUIRED' if self.shutdown['oven']['safety'] else 'SHUTDOWN')
            del self.shutdown['oven']
        self.m[zone]['on'] = on
        self._setpoint_changed(f'{zone} heater {"on" if on else "off"}')

    def _cmd_oven_set_initial_time(self, time_s):
        self._need_on()
        self.m['oven']['initial_time_s'] = time_s
        self._setpoint_changed('oven initial time')

    def _cmd_oven_set_ramp(self, index, rate_k_s=None, final_k=None, final_time_s=None):
        self._need_on()
        r = self.m['oven']['ramps'][index - 1]
        if final_k is not None and final_k > self.m['config']['oven_max_k'] + 1e-9:
            raise ActionRejected('ABOVE_MAXIMUM')
        if rate_k_s is not None:
            r['rate'] = rate_k_s
        if final_k is not None:
            r['final'] = final_k
        if final_time_s is not None:
            r['final_time'] = final_time_s
        if self._program_length() > MAX_RUN_S + 1e-6:
            raise ActionRejected('RUN_TOO_LONG')
        self._setpoint_changed(f'oven ramp {index}')

    def _cmd_oven_set_post_run(self, time_s=None, temperature_k=None):
        self._need_on()
        if temperature_k is not None and temperature_k > self.m['config']['oven_max_k'] + 1e-9:
            raise ActionRejected('ABOVE_MAXIMUM')
        if time_s is not None:
            self.m['oven']['post_time_s'] = time_s
        if temperature_k is not None:
            self.m['oven']['post_temp_k'] = temperature_k

    def _cmd_inlet_configure(self, **kw):
        self._need_on()
        self._need_module('front_inlet')
        if (kw.get('split_ratio') or kw.get('split_flow_m3_s')) and not self._defined():
            raise ActionRejected('COLUMN_NOT_DEFINED')                    # p.152: split ratios need a defined column
        if 'split_flow_m3_s' in kw:
            belief = self._col_setpoint()[1]
            if not belief:
                raise ActionRejected('COLUMN_NOT_DEFINED')
            kw['split_ratio'] = min(7500.0, kw.pop('split_flow_m3_s') / belief)
        names = dict(purge_flow_m3_s='purge_flow', septum_purge_m3_s='septum_purge', saver_flow_m3_s='saver_flow', pulse_pressure_pa='pulse_pressure')
        for k, v in kw.items():
            self.m['inlet'][names.get(k, k)] = v
        if kw.get('gas_saver') is False:
            self.saver_since = None
        self._setpoint_changed('front inlet ' + ', '.join(sorted(kw)))

    def _cmd_inlet_set_gas(self, on):
        self._need_on()
        self._need_module('front_inlet')
        if on and 'inlet_flow' in self.shutdown:
            raise ActionRejected('POWER_CYCLE_REQUIRED' if self.shutdown['inlet_flow']['safety'] else 'SHUTDOWN')
        self.m['inlet']['gas_on'] = on
        self.flow_lost_since = None
        self._setpoint_changed('front inlet pressure ' + ('on' if on else 'off'))

    def _cmd_column_configure(self, mode=None, flow_m3_s=None, pressure_pa=None, initial_time_s=None):
        self._need_on()
        col = self.m['column']
        new_mode = mode or col['mode']
        if new_mode in ('constant_flow', 'ramped_flow') and not self._defined():
            raise ActionRejected('COLUMN_NOT_DEFINED')                    # p.152: flow modes need a defined column
        if mode is not None and mode != col['mode']:
            p, F = self._col_setpoint()
            col['mode'] = mode
            if mode in ('constant_pressure', 'ramped_pressure') and col['pressure'] is None:
                col['pressure'] = max(0.0, p - self._p_out())
            if mode in ('constant_flow', 'ramped_flow') and F is not None and flow_m3_s is None:
                col['flow'] = F
        if flow_m3_s is not None:
            if new_mode not in ('constant_flow', 'ramped_flow'):
                raise ActionRejected('WRONG_MODE')
            col['flow'] = flow_m3_s
        if pressure_pa is not None:
            if new_mode in ('constant_flow', 'ramped_flow'):              # a pressure typed in a flow mode sets the matching flow
                L, d = self._cal_dims()
                col['flow'] = column_flow(self.plant['pressure_pa'] + pressure_pa, self._p_out(), L, d, self.m['config']['inlet_gas'], self._col_temp())
            else:
                col['pressure'] = pressure_pa
        if initial_time_s is not None:
            col['initial_time_s'] = initial_time_s
        if col['mode'] in ('constant_pressure', 'ramped_pressure') and col['pressure'] is None:
            col['pressure'] = 0.0
        self._setpoint_changed('column 1')

    def _cmd_column_set_ramp(self, index, rate=None, final=None, final_time_s=None):
        self._need_on()
        r = self.m['column']['ramps'][index - 1]
        for k, v in (('rate', rate), ('final', final), ('final_time', final_time_s)):
            if v is not None:
                r[k] = v
        self._setpoint_changed(f'column ramp {index}')

    def _cmd_detector_configure(self, h2_flow_m3_s=None, air_flow_m3_s=None, makeup_flow_m3_s=None, lit_offset_a=None, electrometer=None):
        self._need_on()
        self._need_module('front_detector')
        d = self.m['detector']
        for k, v in (('h2', h2_flow_m3_s), ('air', air_flow_m3_s), ('makeup', makeup_flow_m3_s), ('lit_offset', lit_offset_a), ('electrometer', electrometer)):
            if v is not None:
                d[k] = v
        self._setpoint_changed('front detector')

    def _cmd_detector_set_gas(self, gas, on):
        self._need_on()
        self._need_module('front_detector')
        key = {'hydrogen': 'h2_on', 'air': 'air_on', 'makeup': 'makeup_on'}[gas]
        if gas == 'hydrogen' and on and 'h2_flow' in self.shutdown:
            raise ActionRejected('SHUTDOWN')
        if gas == 'hydrogen' and on and 'flame' in self.shutdown:
            del self.shutdown['flame']
        self.m['detector'][key] = on
        if not on and gas in ('hydrogen', 'air'):
            self.gc.flame_lit = False
            self.m['detector']['flame'] = False
        self._setpoint_changed(f'front detector {gas} {"on" if on else "off"}')

    def _cmd_detector_set_flame(self, on):
        self._need_on()
        self._need_module('front_detector')
        d = self.m['detector']
        if not on:
            d['flame'] = False
            d['h2_on'] = d['air_on'] = False                               # p.107: turning the flame off turns off hydrogen and air
            self.gc.flame_lit = False
            self.shutdown.pop('flame', None)
            self.flame_tries = 0
            return
        self.shutdown.pop('flame', None)
        d['h2_on'] = d['air_on'] = True
        d['flame'] = True
        self.flame_tries = 0
        self._sync_legacy()
        if self.m['config']['ignore_ignitor']:
            return                                                         # p.168: the GC does not try to light the flame
        g = self.gc
        f = g.flows()
        if f['hydrogen'] > 0 and f['air'] > 0 and g.zones['fid'].temperature_K >= g.method.fid_ignition_minimum_K:
            self.emf['detector.ignitor']['value'] += 1                    # ignite now; otherwise (cold detector, no gas) the firmware lights
            self.flame_try_t = self.t                                     # the flame as soon as it can, re-trying and finally shutting down
            if not self._try_ignite():
                self.flame_tries = 1

    def _cmd_detector_manual_ignite(self):
        self._need_on()
        if not self.m['config']['ignore_ignitor']:
            raise ActionRejected('IGNITOR_ACTIVE')
        self._legacy('ignite')
        self.m['detector']['flame'] = True

    def _cmd_analog_configure(self, output, signal=None, fast_peaks=None):
        self._need_on()
        a = self.m['analog'][str(output)]
        if signal is not None:
            a['signal'] = signal
        if fast_peaks is not None:
            a['fast_peaks'] = fast_peaks

    def _cmd_column_comp_run(self):
        self._need_on()
        self._need_module('front_detector')
        if self.run['state'] != 'idle':
            raise ActionRejected('RUN_ACTIVE')
        if self._program_length() <= 0:
            raise ActionRejected('NO_PROGRAM')                            # the blank run lasts the oven program
        g = self.gc
        g.running = True
        g.run_time_s = g.fid_integral_C = 0.0
        g.injection_time_s = None
        self.comp['recording'] = []
        self.run['state'] = 'blank'
        self.run_owner = self._starter()
        self._event('Column compensation run started')

    def _comp_value(self):
        prof = self.comp['profile']
        if not prof:
            return 0.0
        t = self.gc.run_time_s if self.run['state'] in ('run', 'blank') else 0.0
        i = min(range(len(prof)), key=lambda k: abs(prof[k][0] - t))
        return prof[i][1]

    def _analog_value(self, signal):
        if signal == 'front_detector':
            return self._fid_output()
        if signal == 'column_comp':
            return self._fid_output() - self._comp_value()
        return 0.0

    def _analog_tick(self, dt):
        """Analog outputs (p.169): a first-order low pass at the output bandwidth, 3.0 Hz standard or 8 Hz with Fast peaks, fed with the
        signal averaged over the step (the digital output is not filtered)."""
        for k, a in self.m['analog'].items():
            if not self.gc.powered:
                self.analog_y.pop(k, None)
                continue
            x = self._analog_value(a['signal'])
            src, y = self.analog_y.get(k, (None, None))
            tau = 1.0 / (2.0 * math.pi * ANALOG_BANDWIDTH_HZ[a['fast_peaks']])
            self.analog_y[k] = (a['signal'], x if src != a['signal'] else x + (y - x) * math.exp(-dt / tau))

    def _analog_out(self, k):
        """Analog output value (A); a new signal assignment starts at the signal (assumption)."""
        a = self.m['analog'][k]
        src, y = self.analog_y.get(k, (None, None))
        return y if src == a['signal'] else self._analog_value(a['signal'])

    def _cmd_method_load(self, slot):
        self._need_on()
        if self.methods[slot] is None:
            raise ActionRejected('METHOD_EMPTY')
        if self.run['state'] in ('run', 'condition', 'blank'):
            raise ActionRejected('RUN_ACTIVE')
        snap = copy.deepcopy(self.methods[slot])
        if slot in ('SLEEP', 'WAKE'):
            for r in snap['oven']['ramps']:
                r['rate'] = 0.0
        self._apply_method(snap)
        self.sleeping = False
        self._event(f'Method {slot} loaded')

    def _cmd_method_store(self, slot):
        self._need_on()
        self.methods[slot] = self._method_snapshot()
        self._event(f'Method {slot} stored')

    def _cmd_method_delete(self, slot):
        self._need_on()
        if self.methods[slot] is None:
            raise ActionRejected('METHOD_EMPTY')
        self.methods[slot] = None
        self._event(f'Method {slot} deleted')

    @staticmethod
    def _empty_sequence():
        return dict(priority_method=-1, use_priority=False, subseq=[dict(method=-1) for _ in range(5)], post_method=0, repeat=False)

    def _cmd_sequence_configure(self, **kw):
        self._need_on()
        if self.seq_status in ('running', 'ready_wait', 'paused') and set(kw) - {'use_priority'}:
            raise ActionRejected('SEQUENCE_RUNNING')                      # p.44: once the sequence starts it cannot be edited
        self.seq.update(kw)

    def _cmd_sequence_set_subsequence(self, index, method):
        self._need_on()
        if self.seq_status in ('running', 'ready_wait', 'paused'):
            raise ActionRejected('SEQUENCE_RUNNING')
        self.seq['subseq'][index - 1]['method'] = method

    def _cmd_sequence_load(self, slot):
        self._need_on()
        if self.sequences[slot] is None:
            raise ActionRejected('SEQUENCE_EMPTY')                        # p.47: error message if not stored
        self.seq = copy.deepcopy(self.sequences[slot])
        self.seq_status = 'stopped'

    def _cmd_sequence_store(self, slot):
        self._need_on()
        self.sequences[slot] = copy.deepcopy(self.seq)

    def _cmd_sequence_delete(self, slot):
        self._need_on()
        if self.sequences[slot] is None:
            raise ActionRejected('SEQUENCE_EMPTY')
        self.sequences[slot] = None

    def _cmd_sequence_control(self, action):
        """Seq Control (p.51-52).  Subsequence injections need an ALS or a sampling valve; this configuration has neither, so a sequence holds
        no runnable sample: Start leaves 'No sequence' (the documented status of a sequence that is not defined)."""
        self._need_on()
        st = self.seq_status
        if action == 'start':
            runnable = []                                                 # no injector / valve: no subsequence can inject (p.45-46)
            self.seq_status = 'running' if runnable else 'no_sequence'
            self._event('Sequence start: no sampler or valve configured (No sequence)')
        elif action == 'pause':
            if st not in ('running', 'ready_wait'):
                raise ActionRejected('SEQUENCE_NOT_RUNNING')
            self.seq_status = 'paused'
        elif action == 'resume':
            if st not in ('paused', 'stopped', 'aborted'):
                raise ActionRejected('SEQUENCE_NOT_RUNNING')
            self.seq_status = 'no_sequence'
        else:
            if st not in ('running', 'ready_wait', 'paused'):
                raise ActionRejected('SEQUENCE_NOT_RUNNING')
            self.seq_status = 'stopped'

    def _time_of_day(self, time_min, time_hhmm):
        if (time_min is None) == (time_hhmm is None):
            raise ActionRejected('INVALID_ARGUMENT')
        t = time_min if time_min is not None else self._hhmm(time_hhmm)
        if not 0 <= t <= 1439:
            raise ActionRejected('INVALID_ARGUMENT')
        return t

    def _cmd_clock_add_event(self, action, time_min=None, time_hhmm=None, slot=None):
        self._need_on()
        time_min = self._time_of_day(time_min, time_hhmm)
        if action == 'load_method' and slot is None:
            raise ActionRejected('INVALID_ARGUMENT')
        if len(self.clock_events) >= 550:
            raise ActionRejected('TABLE_FULL')
        self.clock_events.append(dict(time_min=time_min, action=action, slot=slot))
        self.clock_events.sort(key=lambda e: e['time_min'])

    def _cmd_clock_delete_event(self, index):
        self._need_on()
        if index > len(self.clock_events):
            raise ActionRejected('NO_ENTRY')
        del self.clock_events[index - 1]

    def _cmd_schedule_add(self, day, function, time_min=None, time_hhmm=None):
        self._need_on()
        time_min = self._time_of_day(time_min, time_hhmm)
        self.schedule.append(dict(day=day, time_min=time_min, function=function))
        self.schedule.sort(key=lambda e: (DAYS.index(e['day']), e['time_min']))

    def _cmd_schedule_delete(self, index):
        self._need_on()
        if index > len(self.schedule):
            raise ActionRejected('NO_ENTRY')
        del self.schedule[index - 1]

    def _cmd_schedule_sleep_now(self):
        self._need_on()
        if self.run['state'] in ('run', 'condition', 'blank'):
            raise ActionRejected('RUN_ACTIVE')
        self._sleep()

    def _cmd_schedule_wake_now(self, mode):
        self._need_on()
        if not self.sleeping:
            raise ActionRejected('NOT_ASLEEP')
        self._wake(mode)

    def _cmd_config_oven(self, max_temperature_k=None, equilibration_time_s=None, external_oven_mode=None, slow_cool_down=None, limit_ballistic_power=None,
                         ignore_ready=None):
        self._need_on()
        c = self.m['config']
        if max_temperature_k is not None:
            c['oven_max_k'] = max_temperature_k
            if self.m['oven']['temp_k'] > max_temperature_k:
                self.m['oven']['temp_k'] = max_temperature_k
        for k, v in (('equilibration_s', equilibration_time_s), ('external_oven', external_oven_mode), ('slow_cool', slow_cool_down),
                     ('limit_power', limit_ballistic_power), ('oven_ignore_ready', ignore_ready)):
            if v is not None:
                c[k] = v

    def _cmd_config_inlet(self, gas_type=None, ignore_ready=None):
        self._need_on()
        self._need_module('front_inlet')
        if gas_type is not None:
            self.m['config']['inlet_gas'] = gas_type
        if ignore_ready is not None:
            self.m['config']['inlet_ignore_ready'] = ignore_ready

    def _cmd_config_column(self, length_m=None, diameter_m=None, film_m=None, inlet=None, outlet=None, outlet_pressure_pa=None):
        self._need_on()
        c = self.m['config']
        for k, v in (('col_length', length_m), ('col_diameter', diameter_m), ('col_film', film_m), ('col_inlet', inlet), ('col_outlet', outlet),
                     ('col_outlet_pa', outlet_pressure_pa)):
            if v is not None:
                c[k] = v
        if length_m is not None or diameter_m is not None:
            self.cal['column'], self.cal['column_mode'] = None, 'not_calibrated'
        if not self._defined() and self.m['column']['mode'] in ('constant_flow', 'ramped_flow'):
            p, _ = self._col_setpoint()
            self.m['column']['mode'] = 'constant_pressure'                # p.152: a column that is not defined has no flow modes
            self.m['column']['pressure'] = max(0.0, p - self._p_out())

    def _cmd_config_detector(self, makeup_gas_type=None, ignore_ignitor=None, ignore_ready=None):
        self._need_on()
        self._need_module('front_detector')
        for k, v in (('makeup_gas', makeup_gas_type), ('ignore_ignitor', ignore_ignitor), ('det_ignore_ready', ignore_ready)):
            if v is not None:
                self.m['config'][k] = v

    def _cmd_config_module(self, device, installed):
        self._need_on()
        self._need_unlocked_config()
        pend = dict(self.modules_pending or self.modules)
        pend[device] = installed
        self.modules_pending = pend
        self.message = 'Power cycle the GC to complete the configuration'
        self._event(f'{device} {"install" if installed else "remove"} module (power cycle required)')

    @staticmethod
    def _hhmm(v):
        h, mm = divmod(abs(int(v)), 100)
        if mm > 59:
            raise ActionRejected('INVALID_ARGUMENT')
        return int(math.copysign(h * 60 + mm, v)) if v else 0

    def _cmd_config_time(self, utc_offset_min=None, time_min=None, date=None, zone_hhmm=None, time_hhmm=None, date_ddmmyy=None):
        self._need_on()
        if zone_hhmm is not None:
            utc_offset_min = self._hhmm(zone_hhmm)
        if time_hhmm is not None:
            time_min = self._hhmm(time_hhmm)
            if time_min > 1439:
                raise ActionRejected('INVALID_ARGUMENT')
        if date_ddmmyy is not None:
            dd, rest = divmod(int(date_ddmmyy), 10000)
            date = f'20{rest % 100:02d}-{rest // 100:02d}-{dd:02d}'
        now = self._clock()
        if date is not None:
            try:
                d = datetime.date.fromisoformat(date)
            except ValueError:
                raise ActionRejected('INVALID_ARGUMENT') from None
            now = now.replace(year=d.year, month=d.month, day=d.day)
        if time_min is not None:
            now = now.replace(hour=time_min // 60, minute=time_min % 60, second=0, microsecond=0)
        if utc_offset_min is not None:
            self.utc_offset_min = utc_offset_min
        self.clock_offset_s = (now - EPOCH).total_seconds() - self.gc.time_s

    def _cmd_config_instrument(self, **kw):
        self._need_on()
        self.instrument.update(kw)

    def _cmd_config_status_order_begin(self):
        self._need_on()
        self.status_pos = 0

    def _cmd_config_status_order_select(self, setpoint):
        self._need_on()
        order = [s for s in self.status_order if s != setpoint]
        order.insert(min(self.status_pos, len(order)), setpoint)
        self.status_order = order
        self.status_pos = min(self.status_pos + 1, len(order) - 1)

    def _cmd_options_keyboard(self, **kw):
        self._need_on()
        if kw.get('hard_config_lock') is False:
            self._event('Hard configuration lock off (until power cycle)')
        self.opt.update(kw)

    def _cmd_options_communication(self, **kw):
        self._need_on()
        pend = dict(self.comm_pending or self.comm)
        pend.update(kw)
        self.comm_pending = pend
        self.message = 'Power cycle the GC to apply the LAN settings'

    def _cmd_options_reboot(self):
        self._need_on()
        self._cmd_power_set(False)
        self._cmd_power_set(True)

    def _cmd_calibration_configure(self, **kw):
        self._need_on()
        self.cal.update(kw)

    def _cmd_calibration_zero(self, sensor):
        """p.191-192: flow sensors are zeroed with the carrier gas flowing; pressure sensors with the supply line disconnected."""
        self._need_on()
        self._need_module('front_inlet')
        if sensor in ('inlet_flow', 'septum_purge_flow'):
            if not (self.gc.gases['carrier'] and self._inlet_live()):
                raise ActionRejected('ZERO_CONDITIONS')
            key = 'flow_zero' if sensor == 'inlet_flow' else 'septum_zero'
            self.cal[key] = self.sensor_err['inlet_flow_offset_m3_s' if sensor == 'inlet_flow' else 'septum_flow_offset_m3_s']
        else:
            if self.gc.gases['carrier']:
                raise ActionRejected('ZERO_CONDITIONS')
            self.cal['pressure_zero'] = self.sensor_err['inlet_pressure_offset_pa']
        self._event(f'Front inlet {sensor.replace("_", " ")} zeroed')

    def _cmd_calibration_column(self, mode, measured_flow_m3_s=None, unretained_peak_s=None):
        """p.192-196: estimate the column length and / or diameter from a measured flow (NTP) and / or an unretained peak time at the current
        conditions (no oven ramps, inlet on).  Fails when the length changes by more than 5 m or the diameter by more than 20 um."""
        self._need_on()
        o = self.m['oven']
        if not self._defined() or any(r['rate'] for r in o['ramps']) or not self._inlet_live():
            raise ActionRejected('CAL_CONDITIONS')
        if mode == 'length_and_diameter' and (measured_flow_m3_s is None or unretained_peak_s is None) or \
                mode != 'length_and_diameter' and (measured_flow_m3_s is None) == (unretained_peak_s is None):
            raise ActionRejected('INVALID_ARGUMENT')
        c = self.m['config']
        L0, d0 = c['col_length'], c['col_diameter']
        p, _ = self._col_setpoint()
        po = self._p_out()
        T = self._col_temp()
        e = eta(c['inlet_gas'], T)
        K = math.pi * (p ** 2 - po ** 2) * T_NTP / (256.0 * e * P_NTP * T)       # F = K d^4 / L
        # holdup time of a capillary (compressible flow): t_m = 32 eta L^2 (P^3 - Po^3) / (3 d^2 (P^2 - Po^2)^2)
        Jt = 32.0 * e * (p ** 3 - po ** 3) / (3.0 * (p ** 2 - po ** 2) ** 2)
        L, d = L0, d0
        if mode == 'length_and_diameter':
            # t = Jt L^2 / d^2 and F = K d^4 / L -> d^2 = sqrt(t/Jt) ... solve: L = sqrt(t/Jt) d ; F = K d^3 / sqrt(t/Jt)
            r = math.sqrt(unretained_peak_s / Jt)
            d = (measured_flow_m3_s * r / K) ** (1.0 / 3.0)
            L = r * d
        elif mode == 'length':
            L = K * d0 ** 4 / measured_flow_m3_s if measured_flow_m3_s is not None else d0 * math.sqrt(unretained_peak_s / Jt)
        else:
            d = (measured_flow_m3_s * L0 / K) ** 0.25 if measured_flow_m3_s is not None else L0 / math.sqrt(unretained_peak_s / Jt)
        if abs(L - L0) > 5.0 or abs(d - d0) > 20e-6:
            raise ActionRejected('COLUMN_CAL_FAILED')
        self.cal['column'] = dict(length=L, diameter=d)
        self.cal['column_mode'] = mode
        self._event(f'Column 1 calibrated ({mode.replace("_", " ")})')

    def _cmd_calibration_reset_column(self):
        self._need_on()
        self.cal['column'], self.cal['column_mode'] = None, 'not_calibrated'

    def _cmd_sensor_configure_error(self, **kw):
        self.sensor_err.update(kw)

    def _cmd_emf_configure(self, counter, service_due='_', service_warning='_'):
        self._need_on()
        e = self.emf[counter]
        if service_due != '_':
            e['due'] = service_due
        if service_warning != '_':
            e['warning'] = service_warning
        if e['due'] is not None and e['warning'] is not None and e['due'] <= e['warning']:
            raise ActionRejected('EMF_LIMITS')                            # p.119: Service Due must be larger than Service warning
        e['modified'] = self._stamp()
        e['flagged'] = e['due'] is not None and e['value'] >= e['due']
        self._log('maintenance', f'{EMF_DEFAULTS[counter][0]} {EMF_DEFAULTS[counter][1]}: limits due={e["due"]} warning={e["warning"]}')

    def _cmd_emf_reset(self, counter):
        self._need_on()
        e = self.emf[counter]
        e.update(value=0.0, since=self.t, modified=self._stamp(), flagged=False)
        self._log('maintenance', f'{EMF_DEFAULTS[counter][0]} {EMF_DEFAULTS[counter][1]}: counter reset')

    def _cmd_stopwatch_toggle(self):
        self._need_on()
        s = self.stopwatch
        if s['running']:
            s['acc'] += self.t - s['start']
            s.update(running=False, start=None)
        else:
            s.update(running=True, start=self.t)

    def _cmd_stopwatch_reset(self):
        self._need_on()
        self.stopwatch.update(running=False, start=None, acc=0.0)

    def _cmd_alarm_silence(self):
        self._need_on()
        self.alarm = 'none'

    def _cmd_fault_clear(self, parameter):
        self._need_on()
        key = {'oven': 'oven', 'inlet_flow': 'inlet_flow', 'h2_flow': 'h2_flow', 'flame': 'flame'}[parameter]
        if key not in self.shutdown:
            raise ActionRejected('NO_FAULT')
        if self.shutdown[key]['safety']:
            raise ActionRejected('POWER_CYCLE_REQUIRED')
        del self.shutdown[key]
        self.alarm = 'none'
        if key == 'inlet_flow':
            self.m['inlet']['gas_on'] = False                             # cleared to Off; [On/Yes] turns it on again (p.34)
        elif key == 'oven':
            self.m['oven']['on'] = False
        elif key in ('flame', 'h2_flow'):
            self.m['detector']['flame'] = False
            self.m['detector']['h2_on'] = False

    def _cmd_fault_inject(self, fault, active):
        (self.faults.add if active else self.faults.discard)(fault)
        if fault == 'oven_overtemp' and active and self.gc.powered:
            self._shutdown('oven', 'Oven thermal shutdown', safety=True)

    def _cmd_inlet_leak_check(self):
        self._need_on()
        self._need_module('front_inlet')
        if not self.gc.gases['carrier'] or not self._inlet_live():
            raise ActionRejected('CAL_CONDITIONS')
        ok = 'inlet_leak' not in self.faults
        self._event('Front inlet leak check ' + ('passed' if ok else 'FAILED'))
        self.message = 'Leak check passed' if ok else 'Leak check failed'
        return dict(state=self.summary(), passed=ok)

    def _cmd_macro_record(self, key):
        self._need_on()
        self.macro_rec = key
        self.macros[key] = []

    def _cmd_macro_finish(self):
        self._need_on()
        self.macro_rec = None

    def _cmd_macro_play(self, key):
        self._need_on()
        if not self.macros[key]:
            raise ActionRejected('MACRO_EMPTY')
        self.macro_queue = list(self.macros[key])

    def _cmd_logs_read(self, log):
        return dict(entries=copy.deepcopy(self.logs[log]))

    def _cmd_truth_inspect(self):
        p, F = self._actual_column()
        return dict(process=dict(legacy=self.gc.observe(), actual_head_pressure_pa=p, actual_column_flow_m3_s=F if self._inlet_live() and self.gc.gases['carrier'] else 0.0,
                                 installed_column=dict(length_m=self.gc.method.column_length_m, diameter_m=self.gc.method.column_inner_diameter_m),
                                 sensor_error=dict(self.sensor_err), faults=sorted(self.faults), plant=dict(self.plant)))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    def step(self, dt_s):
        if dt_s > 0:
            self.gc.step(dt_s)
        return self.t

    # ---- front panel host
    def panel_read(self):
        out = self.read('agent')
        out.update({'host.powered': self.gc.powered, 'host.power_epoch': self.power_epoch, 'host.macro_recording': self.macro_rec,
                    'host.flow_belief_ml_min': None if self._col_setpoint()[1] is None else self._col_setpoint()[1] / ML_MIN})
        return out

    def panel_invoke(self, capability_id, arguments):
        try:
            validate(CAPS[capability_id]['input_schema'], arguments)
            snap = copy.deepcopy({k: v for k, v in self.__dict__.items() if k not in ('panel', '_principals', '_requests', '_caller')})
            try:
                return self.invoke(capability_id, **arguments)
            except ActionRejected:
                self.__dict__.update(snap)
                raise
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.t

    def _panel(self, gesture, *args):
        try:
            return dict(accepted=bool(gesture(*args)))
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _cmd_panel_press(self, input_id, duration_s=0.1):
        """A key: recorded into a macro while recording (p.30), then handled by the engine; a macro played by a User key runs after it."""
        self.last_key_t = self.t
        if self.gc.powered and self.opt['key_click']:
            self.sound['key_clicks'] += 1
        if self.macro_rec is not None and input_id not in ('key_prog', 'key_user_1', 'key_user_2') and len(self.macros[self.macro_rec]) < MACRO_MAX:
            self.macros[self.macro_rec].append(input_id)
        out = self._panel(self.panel.press, input_id, float(duration_s))
        queue, self.macro_queue = self.macro_queue, []
        for k in queue:
            self._panel(self.panel.press, k, 0.1)
        return out

    def _cmd_panel_switch(self, input_id, position):
        return self._panel(self.panel.switch, input_id, position)

    def _cmd_panel_capture(self, display_id):
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

    # ---- observation
    def _status_lines(self):
        """The [Status] Ready / Not Ready table (p.26, p.33, p.177): faults and shutdown messages first, then what is not ready."""
        g = self.gc
        if not g.powered:
            return []
        lines = [v['message'] for v in self.shutdown.values()]
        if self.run['state'] == 'run':
            return lines + ['Run in progress']
        if self.run['state'] == 'condition':
            return lines + ['Condition run in progress']
        reasons = g.not_ready_reasons()
        text = {'OVEN_DOOR_OPEN': 'Oven door open', 'NO_CARRIER_FLOW': 'Front inlet pres not ready', 'NO_HYDROGEN_FLOW': 'Front det H2 not ready',
                'NO_AIR_FLOW': 'Front det Air not ready', 'NO_MAKEUP_FLOW': 'Front det makeup not ready', 'FID_FLAME_OFF': 'Front det flame off',
                'OVEN_EQUILIBRATING': 'Oven equilibrating', 'SLEEPING': 'GC asleep', 'HOST_NOT_READY': 'Host not ready'}
        for r in reasons:
            if r == 'TEMPERATURE_NOT_READY':
                for name, label in (('oven', 'Oven temp not ready'), ('inlet', 'Front inlet temp not ready'), ('fid', 'Front det temp not ready')):
                    z = g.zones[name]
                    if self._zone_counts(name) and (abs(z.temperature_K - z.setpoint_K) > g.method.temperature_tolerance_K or g.stable_time_s < g.method.ready_dwell_s):
                        lines.append(label)
            elif r in text:
                lines.append(text[r])
        for k, e in self.emf.items():
            if e['warning'] is not None and e['value'] >= e['warning'] and (e['due'] is None or e['value'] < e['due']):
                lines.append(f'{EMF_DEFAULTS[k][1]} service soon')
        if not reasons:
            lines.append('Ready for Injection')
        return lines

    def _status_table(self, r):
        rows = {'oven_temp': ('Oven temp', r['oven.temperature_k'], self.m['oven']['temp_k'], 'K'),
                'inlet_temp': ('Front inlet temp', r['inlet.temperature_k'], self.m['inlet']['temp_k'], 'K'),
                'inlet_pressure': ('Front inlet pres', r['inlet.pressure_pa'], self.m['column']['pressure'], 'Pa'),
                'column_flow': ('Column 1 flow', r['column.flow_m3_s'], self.m['column']['flow'], 'm3/s'),
                'detector_temp': ('Front det temp', r['detector.temperature_k'], self.m['detector']['temp_k'], 'K'),
                'h2_flow': ('Front det H2', r['detector.flows_m3_s']['hydrogen'], self.m['detector']['h2'], 'm3/s'),
                'air_flow': ('Front det Air', r['detector.flows_m3_s']['air'], self.m['detector']['air'], 'm3/s'),
                'makeup_flow': ('Front det makeup', r['detector.flows_m3_s']['makeup'], self.m['detector']['makeup'], 'm3/s'),
                'detector_output': ('Front det output', r['detector.output_a'], None, 'A')}
        return [dict(id=k, label=rows[k][0], actual=rows[k][1], setpoint=rows[k][2], unit=rows[k][3]) for k in self.status_order]

    def summary(self):
        """Short state summary returned by the device commands (no process truth)."""
        g = self.gc
        return dict(powered=g.powered, run_state=self.run['state'], ready=g.powered and not g.not_ready_reasons(), prep=self.prep,
                    oven_setpoint_k=g.zones['oven'].setpoint_K, flame_on=self.m['detector']['flame'], message=self.message)

    def read_core(self):
        g = self.gc
        on = g.powered
        f = g.flows()
        p_set, belief = self._col_setpoint()
        live = self._inlet_live() and g.gases['carrier']
        p_err = self.sensor_err['inlet_pressure_offset_pa'] - self.cal['pressure_zero']
        p_read = (p_set - self.plant['pressure_pa']) if live else p_err       # the controller holds the reading at the set point
        zero_err = self.sensor_err['inlet_flow_offset_m3_s'] - self.cal['flow_zero']
        septum = f['septum_purge'] + (self.sensor_err['septum_flow_offset_m3_s'] - self.cal['septum_zero']) * (f['septum_purge'] > 0)   # the controller holds its reading
        ph = self._program_phase()
        L, d = self._cal_dims()
        T = self._col_temp()
        lin_v = None
        if belief and live:
            lin_v = L / (32.0 * eta(self.m['config']['inlet_gas'], T) * L ** 2 * (p_set ** 3 - self._p_out() ** 3) /
                         (3.0 * d ** 2 * (p_set ** 2 - self._p_out() ** 2) ** 2))
        sw = self.stopwatch['acc'] + ((self.t - self.stopwatch['start']) if self.stopwatch['running'] else 0.0)
        due = any(e['due'] is not None and e['value'] >= e['due'] for e in self.emf.values())
        return {
            'clock.time_s': self.t, 'clock.datetime': self._clock().isoformat(timespec='seconds'), 'power.mains': g.power_connected, 'power.on': on,
            'gas.supply': dict(g.gases), 'oven.door_open': g.door_open,
            'oven.temperature_k': g.zones['oven'].temperature_K, 'oven.setpoint_k': g.zones['oven'].setpoint_K, 'oven.method': copy.deepcopy(self.m['oven']),
            'oven.program_length_s': self._program_length(),
            'inlet.temperature_k': g.zones['inlet'].temperature_K, 'inlet.method': copy.deepcopy(self.m['inlet']),
            'inlet.pressure_pa': p_read if on else None, 'inlet.pressure_setpoint_pa': (p_set - self.plant['pressure_pa']) if on else None,
            'inlet.total_flow_m3_s': ((belief or 0.0) + f['split_vent'] + zero_err * (f['split_vent'] > 0) + septum) if on and live else 0.0,
            'inlet.split_flow_m3_s': (f['split_vent'] + zero_err * (f['split_vent'] > 0)) if on else 0.0, 'inlet.septum_flow_m3_s': septum if on else 0.0,
            'inlet.split_flow_setpoint_m3_s': self.m['inlet']['split_ratio'] * (belief or 0.0),
            'column.method': copy.deepcopy(self.m['column']), 'column.flow_m3_s': (belief if live else 0.0) if on and belief is not None else None,
            'column.velocity_m_s': lin_v if on else None, 'column.defined': self._defined(),
            'detector.temperature_k': g.zones['fid'].temperature_K, 'detector.method': copy.deepcopy(self.m['detector']),
            'detector.flows_m3_s': dict(hydrogen=f['hydrogen'], air=f['air'], makeup=f['makeup']), 'detector.flame_lit': bool(g.flame_lit and on),
            'detector.output_a': self._fid_output() if on else None,
            'analog.outputs': {k: dict(v, value_a=self._analog_out(k) if on else 0.0) for k, v in self.m['analog'].items()},
            'column_comp.points': len(self.comp['profile'] or []),
            'config.state': dict(copy.deepcopy(self.m['config']), instrument=dict(self.instrument), modules=dict(self.modules),
                                 modules_pending=copy.deepcopy(self.modules_pending), status_order=list(self.status_order), utc_offset_min=self.utc_offset_min),
            'options.state': dict(self.opt), 'options.communication': dict(self.comm, pending=copy.deepcopy(self.comm_pending)),
            'calibration.state': dict(auto_flow_zero=self.cal['auto_flow_zero'], auto_zero_septum_purge=self.cal['auto_zero_septum_purge'],
                                      column_mode=self.cal['column_mode'], column=copy.deepcopy(self.cal['column'])),
            'run.state': self.run['state'] if on else 'off', 'run.prep': self.prep,
            'run.time_s': g.run_time_s if self.run['state'] in ('run', 'condition', 'blank') else None,
            'run.remaining_s': max(0.0, self._program_length() - g.run_time_s) if self.run['state'] in ('run', 'condition', 'blank') and self._program_length() > 0 else None,
            'run.last_s': self.run['last_run_s'], 'run.post_remaining_s': max(0.0, self.m['oven']['post_time_s'] - (self.t - self.run['post_start']))
            if self.run['state'] == 'post' else None, 'run.ready': on and not g.not_ready_reasons(), 'run.gas_saver': self.saver_since is not None,
            'run.program_phase': ph, 'syringe.inserted': g.syringe_inserted,
            'status.lines': self._status_lines(),
            'status.shutdowns': {k: v['message'] for k, v in self.shutdown.items()}, 'alarm.tone': self.alarm if on else 'none', 'sound.counts': dict(self.sound),
            'methods.stored': [s for s in METHOD_SLOTS if self.methods[s] is not None], 'sequence.active': copy.deepcopy(self.seq),
            'sequence.stored': [s for s in SEQ_SLOTS if self.sequences[s] is not None], 'sequence.status': self.seq_status,
            'clock.events': copy.deepcopy(self.clock_events), 'schedule.entries': copy.deepcopy(self.schedule), 'schedule.asleep': self.sleeping,
            'emf.counters': {k: dict(component=EMF_DEFAULTS[k][0], part=EMF_DEFAULTS[k][1], unit=EMF_DEFAULTS[k][2], value=round(e['value'], 6), service_due=e['due'],
                                     service_warning=e['warning'], default=EMF_DEFAULTS[k][3], modified=e['modified'],
                                     due_reached=e['due'] is not None and e['value'] >= e['due']) for k, e in self.emf.items()},
            'logs.counts': {k: len(v) for k, v in self.logs.items()}, 'logs.entries': copy.deepcopy(self.logs),
            'stopwatch.s': sw, 'stopwatch.running': self.stopwatch['running'], 'macro.keys': {str(k): len(v) for k, v in self.macros.items()},
            'macro.recording': self.macro_rec, 'display.message': self.message if on else None,
            'display.saver_active': bool(on and self.opt['display_saver'] and self.t - self.last_key_t >= SAVER_DIM_S),
            'leds': dict(pre_run=on and self.prep, run=on and self.run['state'] in ('run', 'condition', 'blank'), post_run=on and self.run['state'] == 'post',
                         initial_temp=on and ph == 'initial', rate=on and ph == 'rate', final_temp=on and ph == 'final',
                         not_ready=on and bool(g.not_ready_reasons()), not_ready_blink=on and bool(self.shutdown), service_due=on and due,
                         run_log=on and bool(self.logs['run']), remote=False, clock_table=on and bool(self.clock_events or self.schedule),
                         gas_saver=on and self.saver_since is not None),
        }

    def read(self, visibility='agent'):
        out = self.read_core()
        out['status.table'] = self._status_table(out) if out['power.on'] else []
        if visibility != 'agent':
            p, F = self._actual_column()
            out['truth.process'] = dict(legacy=self.gc.observe(), actual_head_pressure_pa=p, actual_column_flow_m3_s=F, sensor_error=dict(self.sensor_err),
                                        faults=sorted(self.faults), plant=dict(self.plant))
        return out


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency, rollback)
class GC7890BSession:
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
        caps = {k: dict(function_zh=v['function_zh'], input_schema=v['input_schema'], visibility=v['visibility']) for k, v in ALL_CAPS.items()
                if v['visibility'] == 'agent' or self._role != 'agent'}
        slots = {k: v for k, v in SLOTS.items() if v['visibility'] == 'agent' or self._role != 'agent'}
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version=ASSET_VERSION, capabilities=caps,
                    state_slots={k: dict(meaning_zh=v['meaning'], unit=v['unit']) for k, v in slots.items()})

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
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'], quality='unavailable' if values[k] is None else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

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
                if digest is None or old != digest:
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
            snapshot = copy.deepcopy({k: v for k, v in h.__dict__.items() if k not in ('_principals', '_requests', '_caller')})
            try:
                result = _jsonable(h.invoke(capability_id, **arguments))
            except ActionRejected:
                h.__dict__.update(snapshot)
                raise
            finally:
                h._caller = None
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


ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置没有该硬件', 'ACCESS_DENIED': '无权访问（真值或评测者能力；或另一agent启动的运行不能停止或修改）', 'MAINS_OFF': '未插电源线',
    'POWER_OFF': '主电源开关关', 'NOT_READY': '未就绪（Status键查看原因）', 'RUN_ACTIVE': '正在运行', 'SYRINGE_IN_INLET': '针筒在进样口内，不能吸样',
    'VIAL_EMPTY': '样品瓶已空', 'SYRINGE_INSERTED': '针头已插入', 'SYRINGE_EMPTY': '针筒是空的', 'INJECTION_NOT_READY': '进样须在运行中、针头已插入且仪器就绪',
    'IGNITION_FAILED': 'FID点火失败（须开机、有氢气与空气且检测器温度≥150 °C；或点火器失效）', 'DEVICE_ERROR': '过程模型拒绝', 'ABOVE_MAXIMUM': '超过该加热区或柱箱配置的最高温度',
    'SHUTDOWN': '该参数处于关断状态（先在闪烁行按Off/No清除）', 'POWER_CYCLE_REQUIRED': '安全关断：须关机再开机', 'MODULE_UNCONFIGURED': '模块未配置（Unconfigured）',
    'CONFIG_LOCKED': 'CONFIGURATION IS LOCKED（Options > Keyboard and Display解除硬件配置锁）', 'COLUMN_NOT_DEFINED': '色谱柱未定义（长度或内径为0）：只能用压力模式，不能设分流比',
    'WRONG_MODE': '当前柱模式不能设该值', 'RUN_TOO_LONG': '程序总时间超过999.99 min', 'METHOD_EMPTY': '该方法号未存储', 'SEQUENCE_EMPTY': '该序列号未存储',
    'SEQUENCE_RUNNING': '序列运行中不能编辑', 'SEQUENCE_NOT_RUNNING': '序列不在可执行该操作的状态', 'TABLE_FULL': '时钟表已满（550条）', 'NO_ENTRY': '没有该条目',
    'NOT_ASLEEP': 'GC不在休眠', 'ZERO_CONDITIONS': '调零条件不满足（流量传感器须有气流；压力传感器须断开供气）',
    'CAL_CONDITIONS': '校准条件不满足（柱须已定义、无柱箱程序升温、进样口气流开）', 'COLUMN_CAL_FAILED': '色谱柱校准失败（长度修正>5 m或内径修正>20 µm）',
    'EMF_LIMITS': 'Service Due须大于Service warning', 'NO_FAULT': '该参数没有关断', 'IGNITOR_ACTIVE': '点火器未被忽略（Ignore Ignitor为False时由GC点火）',
    'MACRO_EMPTY': '该用户键没有录制宏', 'NO_PROGRAM': '柱箱程序时间为0（空白运行按柱箱程序长度进行）', 'UNKNOWN_OPERATION': '本运行层的能力都是同步的',
}

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


_N = number('1')
_K, _S, _F, _P, _A = number('K'), number('s'), number('m3/s'), number('Pa'), number('A')
slot('clock.time_s', _S, 's', 'derived', 'electronics', '仿真时钟（宿主时钟）')
slot('clock.datetime', TEXT, '1', 'derived', 'electronics', 'GC日期时间（[Time]首行；ISO格式）')
slot('power.mains', BOOL, '1', 'setpoint', 'power_inlet', '电源线是否插入')
slot('power.on', BOOL, '1', 'setpoint', 'power_switch', '主电源开关是否开')
slot('gas.supply', FREE, '1', 'scene', 'carrier_port', '各气源是否在气源处打开（carrier/hydrogen/air/makeup）')
slot('oven.door_open', BOOL, '1', 'scene', 'oven_door', '柱箱门是否打开')
slot('oven.temperature_k', _K, 'K', 'sensor', 'oven_sensor', '柱箱实际温度（显示Actual列；理想传感器）')
slot('oven.setpoint_k', _K, 'K', 'derived', 'oven_heater', '柱箱当前控制目标（程序中随运行时间变化）')
slot('oven.method', FREE, '1', 'setpoint', 'oven_heater', '柱箱方法设定：temp_k、on、initial_time_s、ramps[20]（rate K/s、final K、final_time s）、post_time_s、post_temp_k')
slot('oven.program_length_s', _S, 's', 'derived', 'oven_heater', '柱箱程序总时间（运行长度）')
slot('inlet.temperature_k', _K, 'K', 'sensor', 'inlet_sensor', '进样口实际温度')
slot('inlet.method', FREE, '1', 'setpoint', 'inlet_body', '进样口设定：temp_k、on、gas_on、mode、split_ratio、purge_flow、purge_time_s、septum_purge、gas_saver、saver_flow、saver_time_s、pulse_pressure、pulse_time_s（SI）')
slot('inlet.pressure_pa', nullable(_P), 'Pa', 'sensor', 'carrier_epc', '进样口压力读数（表压；含未调零的传感器误差）')
slot('inlet.pressure_setpoint_pa', nullable(_P), 'Pa', 'derived', 'carrier_epc', '压力控制目标（表压；恒流模式由配置柱计算）')
slot('inlet.total_flow_m3_s', _F, 'm3/s', 'derived', 'carrier_epc', '总流量显示（柱流量计算值+分流+隔垫吹扫，NTP）')
slot('inlet.split_flow_m3_s', _F, 'm3/s', 'sensor', 'split_vent', '分流出口流量读数（NTP）')
slot('inlet.septum_flow_m3_s', _F, 'm3/s', 'sensor', 'septum_purge', '隔垫吹扫流量读数（NTP；控制器把读数保持在设定值，未调零的传感器误差体现在实际流量上）')
slot('inlet.split_flow_setpoint_m3_s', _F, 'm3/s', 'derived', 'split_valve', '分流流量设定（分流比×计算柱流量，NTP）')
slot('column.method', FREE, '1', 'setpoint', 'column', '色谱柱设定：mode、flow（m3/s）、pressure（Pa表压）、initial_time_s、ramps[3]')
slot('column.flow_m3_s', nullable(_F), 'm3/s', 'derived', 'column', 'GC按配置柱计算的柱流量（显示值；柱未定义为null）')
slot('column.velocity_m_s', nullable(number('m/s')), 'm/s', 'derived', 'column', '计算的平均线速度')
slot('column.defined', BOOL, '1', 'derived', 'column', '色谱柱是否已定义（长度与内径都非0）')
slot('detector.temperature_k', _K, 'K', 'sensor', 'fid_sensor', 'FID实际温度')
slot('detector.method', FREE, '1', 'setpoint', 'fid_body', 'FID设定：temp_k、on、h2、air、makeup（m3/s）、h2_on、air_on、makeup_on、flame、electrometer、lit_offset（A）')
slot('detector.flows_m3_s', FREE, 'm3/s', 'sensor', 'hydrogen_epc', 'FID实际氢气、空气、尾吹气流量（NTP）')
slot('detector.flame_lit', BOOL, '1', 'sensor', 'fid_body', '火焰是否点燃（GC由输出与Lit offset判断）')
slot('detector.output_a', nullable(_A), 'A', 'sensor', 'fid_electrometer', 'FID静电计输出（显示Output，pA；含火焰背景电流）')
slot('analog.outputs', FREE, '1', 'derived', 'signal_port', '模拟输出1/2：信号、Fast peaks、当前输出值（A；一阶低通，带宽标准3.0 Hz、Fast peaks 8 Hz，手册p.169）')
slot('config.state', FREE, '1', 'setpoint', 'electronics', '配置：柱箱最高温度、平衡时间、选项、载气类型、柱尺寸与连接、尾吹气类型、Ignore Ignitor/Ready、Instrument、模块、状态表顺序')
slot('options.state', FREE, '1', 'setpoint', 'parameter_keys', '键盘与显示选项')
slot('options.communication', FREE, '1', 'setpoint', 'signal_port', 'LAN设定与待生效设定')
slot('calibration.state', FREE, '1', 'setpoint', 'carrier_epc', '校准：自动调零开关、色谱柱校准模式与校准尺寸')
slot('run.state', enum(['off', 'idle', 'run', 'post', 'condition', 'blank']), '1', 'derived', 'start_key', '运行状态（blank为柱补偿空白运行）')
slot('column_comp.points', integer(0, 10 ** 7), '1', 'derived', 'fid_electrometer', '柱补偿曲线的点数（0为未建立）')
slot('run.prep', BOOL, '1', 'derived', 'key_prep_run', '是否在Prep Run预运行状态')
slot('run.time_s', nullable(_S), 's', 'derived', 'start_key', '运行已进行时间')
slot('run.remaining_s', nullable(_S), 's', 'derived', 'start_key', '运行剩余时间（柱箱程序）')
slot('run.last_s', nullable(_S), 's', 'derived', 'start_key', '上次运行时间')
slot('run.post_remaining_s', nullable(_S), 's', 'derived', 'start_key', 'Post Run剩余时间')
slot('run.ready', BOOL, '1', 'derived', 'display', '是否就绪（STATUS Ready for Injection）')
slot('run.gas_saver', BOOL, '1', 'derived', 'led_gas_saver', '省气模式是否生效')
slot('run.program_phase', nullable(enum(['initial', 'rate', 'final', 'end'])), '1', 'derived', 'oven_heater', '柱箱程序阶段（状态板升温曲线灯）')
slot('syringe.inserted', BOOL, '1', 'scene', 'syringe', '针头是否插在进样口')
slot('status.lines', array(TEXT), '1', 'derived', 'display', '[Status]就绪/未就绪表的行')
slot('status.table', array(FREE), '1', 'derived', 'display', '[Status]设定值状态表（按配置顺序）')
slot('status.shutdowns', FREE, '1', 'derived', 'display', '关断的参数与信息（行上Off闪烁）')
slot('alarm.tone', enum(['none', 'single', 'series', 'continuous']), '1', 'derived', 'electronics', '警告音（单声、连续变多、持续音）')
slot('sound.counts', FREE, '1', 'derived', 'electronics', '按键音与方法修改提示音计数')
slot('methods.stored', array(TEXT), '1', 'setpoint', 'parameter_keys', '已存储的方法号')
slot('sequence.active', FREE, '1', 'setpoint', 'parameter_keys', '当前序列')
slot('sequence.stored', array(TEXT), '1', 'setpoint', 'parameter_keys', '已存储的序列号')
slot('sequence.status', enum(['running', 'ready_wait', 'paused', 'stopped', 'aborted', 'no_sequence']), '1', 'derived', 'parameter_keys', '序列状态（p.47六种）')
slot('clock.events', array(FREE), '1', 'setpoint', 'parameter_keys', '时钟表事件')
slot('schedule.entries', array(FREE), '1', 'setpoint', 'parameter_keys', '仪器日程条目')
slot('schedule.asleep', BOOL, '1', 'derived', 'parameter_keys', 'GC是否处于休眠（SLEEP方法）')
slot('emf.counters', FREE, '1', 'derived', 'electronics', 'EMF计数器：值、限值、默认值、是否到期')
slot('logs.counts', FREE, '1', 'derived', 'display', '三种日志的条目数')
slot('logs.entries', FREE, '1', 'derived', 'display', '三种日志的条目（最近50条）')
slot('stopwatch.s', _S, 's', 'derived', 'display', '秒表读数')
slot('stopwatch.running', BOOL, '1', 'derived', 'display', '秒表是否在走')
slot('macro.keys', FREE, '1', 'setpoint', 'key_prog', '用户键宏的按键数')
slot('macro.recording', nullable(integer(1, 2)), '1', 'derived', 'key_prog', '正在录制的用户键')
slot('display.message', nullable(TEXT), '1', 'derived', 'display', '最近的仪器信息（如Power on successful）')
slot('display.saver_active', BOOL, '1', 'derived', 'display', '屏保是否已使显示变暗')
slot('leds', FREE, '1', 'derived', 'led_not_ready', '状态板指示灯')
slot('truth.process', FREE, '1', 'process_truth', 'chassis', '旧过程模型的完整观测、实际柱前压与柱流量、传感器误差、故障', 'evaluator')


# ---------------------------------------------------------------- contract metadata (assembled by outputs/panel_ui/agilent_7890b_ssl_fid/build_contract.py)
_ON = ['MAINS_OFF', 'POWER_OFF']
_LEG = {'sample.aspirate': ['SYRINGE_IN_INLET', 'VIAL_EMPTY', 'INVALID_ARGUMENT'], 'syringe.insert': ['SYRINGE_INSERTED'],
        'syringe.depress': ['INJECTION_NOT_READY', 'SYRINGE_EMPTY'], 'syringe.retract': [], 'power.set': ['MAINS_OFF'], 'power.connect_mains': [],
        'gas.connect': [], 'oven.set_door': []}
CAP_ERRORS = dict(_LEG, **{
    'plant.configure': [], 'run.prep': _ON + ['RUN_ACTIVE'], 'run.start': _ON + ['RUN_ACTIVE', 'NOT_READY'], 'run.stop': _ON,
    'thermal.set_temperature': _ON + ['MODULE_UNCONFIGURED', 'ABOVE_MAXIMUM', 'SHUTDOWN'], 'thermal.set_zone': _ON + ['MODULE_UNCONFIGURED', 'SHUTDOWN', 'POWER_CYCLE_REQUIRED'],
    'oven.set_initial_time': _ON, 'oven.set_ramp': _ON + ['ABOVE_MAXIMUM', 'RUN_TOO_LONG'], 'oven.set_post_run': _ON + ['ABOVE_MAXIMUM'],
    'inlet.configure': _ON + ['MODULE_UNCONFIGURED', 'COLUMN_NOT_DEFINED'], 'inlet.set_gas': _ON + ['MODULE_UNCONFIGURED', 'SHUTDOWN', 'POWER_CYCLE_REQUIRED'],
    'column.configure': _ON + ['COLUMN_NOT_DEFINED', 'WRONG_MODE'], 'column.set_ramp': _ON, 'detector.configure': _ON + ['MODULE_UNCONFIGURED'],
    'detector.set_gas': _ON + ['MODULE_UNCONFIGURED', 'SHUTDOWN'], 'detector.set_flame': _ON + ['MODULE_UNCONFIGURED'],
    'detector.manual_ignite': _ON + ['IGNITOR_ACTIVE', 'IGNITION_FAILED'], 'analog.configure': _ON, 'column_comp.run': _ON + ['MODULE_UNCONFIGURED', 'RUN_ACTIVE', 'NO_PROGRAM'],
    'method.load': _ON + ['METHOD_EMPTY', 'RUN_ACTIVE'], 'method.store': _ON, 'method.delete': _ON + ['METHOD_EMPTY'],
    'sequence.configure': _ON + ['SEQUENCE_RUNNING'], 'sequence.set_subsequence': _ON + ['SEQUENCE_RUNNING'], 'sequence.load': _ON + ['SEQUENCE_EMPTY'],
    'sequence.store': _ON, 'sequence.delete': _ON + ['SEQUENCE_EMPTY'], 'sequence.control': _ON + ['SEQUENCE_NOT_RUNNING'],
    'clock.add_event': _ON + ['TABLE_FULL'], 'clock.delete_event': _ON + ['NO_ENTRY'], 'schedule.add': _ON, 'schedule.delete': _ON + ['NO_ENTRY'],
    'schedule.sleep_now': _ON + ['RUN_ACTIVE'], 'schedule.wake_now': _ON + ['NOT_ASLEEP', 'METHOD_EMPTY'], 'config.oven': _ON, 'config.inlet': _ON + ['MODULE_UNCONFIGURED'],
    'config.column': _ON, 'config.detector': _ON + ['MODULE_UNCONFIGURED'], 'config.module': _ON + ['CONFIG_LOCKED'], 'config.time': _ON,
    'config.instrument': _ON, 'config.status_order_begin': _ON, 'config.status_order_select': _ON, 'options.keyboard': _ON, 'options.communication': _ON,
    'options.reboot': _ON, 'calibration.configure': _ON, 'calibration.zero': _ON + ['MODULE_UNCONFIGURED', 'ZERO_CONDITIONS'],
    'calibration.column': _ON + ['CAL_CONDITIONS', 'COLUMN_CAL_FAILED'], 'calibration.reset_column': _ON, 'sensor.configure_error': [],
    'emf.configure': _ON + ['EMF_LIMITS'], 'emf.reset': _ON, 'stopwatch.toggle': _ON, 'stopwatch.reset': _ON, 'alarm.silence': _ON,
    'fault.clear': _ON + ['NO_FAULT', 'POWER_CYCLE_REQUIRED'], 'fault.inject': [], 'inlet.leak_check': _ON + ['MODULE_UNCONFIGURED', 'CAL_CONDITIONS'],
    'macro.record': _ON, 'macro.finish': _ON, 'macro.play': _ON + ['MACRO_EMPTY'], 'logs.read': [], 'truth.inspect': [], 'sim.step': [],
    **{k: [] for k in PANEL_CAPS}})
_COMMON = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_DISP = ['status.lines', 'status.table', 'leds', 'display.message']
EFFECTS = {
    'plant.configure': ['truth.process'], 'power.connect_mains': ['power.mains', 'power.on', 'run.state'] + _DISP,
    'power.set': ['power.on', 'run.state', 'options.state', 'options.communication', 'config.state', 'status.shutdowns'] + _DISP,
    'gas.connect': ['gas.supply'], 'oven.set_door': ['oven.door_open'], 'sample.aspirate': ['truth.process'], 'syringe.insert': ['syringe.inserted'],
    'syringe.depress': ['truth.process'], 'syringe.retract': ['syringe.inserted'], 'run.prep': ['run.prep', 'run.gas_saver'] + _DISP,
    'run.start': ['run.state', 'run.time_s', 'run.prep', 'logs.counts', 'emf.counters'] + _DISP, 'run.stop': ['run.state', 'run.last_s', 'sequence.status'] + _DISP,
    'thermal.set_temperature': ['oven.method', 'inlet.method', 'detector.method', 'oven.setpoint_k'] + _DISP,
    'thermal.set_zone': ['oven.method', 'inlet.method', 'detector.method', 'status.shutdowns'] + _DISP,
    'oven.set_initial_time': ['oven.method', 'oven.program_length_s'], 'oven.set_ramp': ['oven.method', 'oven.program_length_s'], 'oven.set_post_run': ['oven.method'],
    'inlet.configure': ['inlet.method', 'inlet.split_flow_setpoint_m3_s'], 'inlet.set_gas': ['inlet.method', 'inlet.pressure_pa'] + _DISP,
    'column.configure': ['column.method', 'inlet.pressure_setpoint_pa', 'column.flow_m3_s'], 'column.set_ramp': ['column.method'],
    'detector.configure': ['detector.method', 'detector.output_a'], 'detector.set_gas': ['detector.method', 'detector.flows_m3_s', 'detector.flame_lit'] + _DISP,
    'detector.set_flame': ['detector.method', 'detector.flame_lit', 'detector.output_a', 'emf.counters'] + _DISP,
    'detector.manual_ignite': ['detector.method', 'detector.flame_lit'], 'analog.configure': ['analog.outputs'], 'column_comp.run': ['run.state', 'column_comp.points'],
    'method.load': ['oven.method', 'inlet.method', 'column.method', 'detector.method', 'analog.outputs', 'config.state', 'schedule.asleep'] + _DISP,
    'method.store': ['methods.stored'], 'method.delete': ['methods.stored'], 'sequence.configure': ['sequence.active'], 'sequence.set_subsequence': ['sequence.active'],
    'sequence.load': ['sequence.active', 'sequence.status'], 'sequence.store': ['sequence.stored'], 'sequence.delete': ['sequence.stored'],
    'sequence.control': ['sequence.status'], 'clock.add_event': ['clock.events', 'leds'], 'clock.delete_event': ['clock.events', 'leds'],
    'schedule.add': ['schedule.entries', 'leds'], 'schedule.delete': ['schedule.entries', 'leds'],
    'schedule.sleep_now': ['schedule.asleep', 'oven.method', 'inlet.method', 'column.method', 'detector.method'] + _DISP,
    'schedule.wake_now': ['schedule.asleep', 'oven.method', 'inlet.method', 'column.method', 'detector.method', 'run.state'] + _DISP,
    'config.oven': ['config.state', 'oven.method'], 'config.inlet': ['config.state'], 'config.column': ['config.state', 'column.method', 'column.defined', 'calibration.state'],
    'config.detector': ['config.state'], 'config.module': ['config.state'], 'config.time': ['clock.datetime', 'config.state'], 'config.instrument': ['config.state'],
    'config.status_order_begin': [], 'config.status_order_select': ['config.state', 'status.table'], 'options.keyboard': ['options.state'],
    'options.communication': ['options.communication'], 'options.reboot': ['power.on', 'options.communication', 'options.state', 'config.state'] + _DISP,
    'calibration.configure': ['calibration.state'], 'calibration.zero': ['inlet.split_flow_m3_s', 'inlet.pressure_pa'], 'calibration.column': ['calibration.state', 'column.flow_m3_s'],
    'calibration.reset_column': ['calibration.state', 'column.flow_m3_s'], 'sensor.configure_error': ['truth.process'], 'emf.configure': ['emf.counters', 'leds'],
    'emf.reset': ['emf.counters', 'leds'], 'stopwatch.toggle': ['stopwatch.running', 'stopwatch.s'], 'stopwatch.reset': ['stopwatch.running', 'stopwatch.s'],
    'alarm.silence': ['alarm.tone'], 'fault.clear': ['status.shutdowns', 'alarm.tone', 'inlet.method', 'oven.method', 'detector.method'] + _DISP,
    'fault.inject': ['truth.process', 'status.shutdowns', 'alarm.tone'] + _DISP, 'inlet.leak_check': ['display.message', 'logs.counts'],
    'macro.record': ['macro.recording', 'macro.keys'], 'macro.finish': ['macro.recording'], 'macro.play': [], 'logs.read': [], 'truth.inspect': [], 'sim.step': ['clock.time_s'],
}
_EVOLVE = ['oven.temperature_k', 'inlet.temperature_k', 'detector.temperature_k', 'oven.setpoint_k', 'inlet.pressure_pa', 'inlet.total_flow_m3_s', 'inlet.split_flow_m3_s',
           'column.flow_m3_s', 'detector.flows_m3_s', 'detector.flame_lit', 'detector.output_a', 'analog.outputs', 'run.state', 'run.time_s', 'run.remaining_s',
           'run.ready', 'run.gas_saver', 'run.program_phase', 'status.lines', 'status.table', 'status.shutdowns', 'alarm.tone', 'emf.counters', 'logs.counts',
           'stopwatch.s', 'schedule.asleep', 'leds', 'display.saver_active', 'truth.process']
_PANEL_FX = list(dict.fromkeys(s for c in _panel_ui.spec_capabilities(PANEL_SPEC) for s in EFFECTS.get(c, [])))
for _k in ('panel.press', 'panel.switch'):
    EFFECTS[_k] = list(_PANEL_FX)
PRECONDITIONS = {
    'plant.configure': '无（评测者设定实验室与安装状态）', 'power.connect_mains': '无', 'power.set': '开机须插电源线', 'gas.connect': '无',
    'oven.set_door': '无', 'sample.aspirate': '针筒在样品瓶处（未插入进样口）且瓶中有样品', 'syringe.insert': '针头未插入',
    'syringe.depress': '运行中、针头已插入、仪器就绪且针筒有样品（旧过程模型规则）', 'syringe.retract': '无',
    'run.prep': '开机；不在运行中', 'run.start': '开机；不在运行中；就绪（Status显示Ready for Injection）', 'run.stop': '开机',
    'thermal.set_temperature': '开机；模块已配置；不超过该区最高温度（柱箱为配置的最高温度）；柱箱不在关断', 'thermal.set_zone': '开机；模块已配置；关断须先清除（安全关断须重新开机）',
    'oven.set_initial_time': '开机', 'oven.set_ramp': '开机；终温不超过柱箱最高温度；程序总时间不超过999.99 min', 'oven.set_post_run': '开机；温度不超过柱箱最高温度',
    'inlet.configure': '开机；进样口模块已配置；分流比/分流流量须色谱柱已定义', 'inlet.set_gas': '开机；模块已配置；关断须先清除',
    'column.configure': '开机；流量模式须色谱柱已定义；流量只能在流量模式下设', 'column.set_ramp': '开机', 'detector.configure': '开机；检测器模块已配置',
    'detector.set_gas': '开机；检测器模块已配置；氢气关断须先清除', 'detector.set_flame': '开机；检测器模块已配置（冷检测器时GC在达到温度后点火）',
    'detector.manual_ignite': '开机；Ignore Ignitor为TRUE', 'analog.configure': '开机', 'column_comp.run': '开机；空闲；柱箱程序时间>0',
    'method.load': '开机；该方法已存储；不在运行中', 'method.store': '开机', 'method.delete': '开机；该方法已存储', 'sequence.configure': '开机；序列未运行（Use priority除外）',
    'sequence.set_subsequence': '开机；序列未运行', 'sequence.load': '开机；该序列已存储', 'sequence.store': '开机', 'sequence.delete': '开机；该序列已存储',
    'sequence.control': '开机；暂停/停止须序列在运行，继续须已暂停/停止/中止', 'clock.add_event': '开机；时钟表未满（550条）', 'clock.delete_event': '开机；条目存在',
    'schedule.add': '开机', 'schedule.delete': '开机；条目存在', 'schedule.sleep_now': '开机；不在运行中', 'schedule.wake_now': '开机；GC在休眠；CONDITION方式须CONDITION方法已存储',
    'config.oven': '开机', 'config.inlet': '开机；模块已配置', 'config.column': '开机', 'config.detector': '开机；模块已配置', 'config.module': '开机；已解除硬件配置锁',
    'config.time': '开机', 'config.instrument': '开机', 'config.status_order_begin': '开机', 'config.status_order_select': '开机', 'options.keyboard': '开机',
    'options.communication': '开机', 'options.reboot': '开机', 'calibration.configure': '开机', 'calibration.zero': '开机；流量传感器须有载气流动，压力传感器须断开供气',
    'calibration.column': '开机；色谱柱已定义；无柱箱程序升温；进样口载气开', 'calibration.reset_column': '开机', 'sensor.configure_error': '无（评测者）',
    'emf.configure': '开机；Service Due须大于Service warning', 'emf.reset': '开机', 'stopwatch.toggle': '开机', 'stopwatch.reset': '开机', 'alarm.silence': '开机',
    'fault.clear': '开机；该参数在关断；安全关断须重新开机', 'fault.inject': '无（评测者）', 'inlet.leak_check': '开机；模块已配置；有载气',
    'macro.record': '开机', 'macro.finish': '开机', 'macro.play': '开机；该用户键已录制', 'logs.read': '无', 'truth.inspect': '无（评测者）', 'sim.step': '无（宿主时钟）',
    'panel.press': '面板按当前屏幕与手册规则处理按键：关机时只有电源开关起作用；输入中（*闪烁）只有Enter、Clear、滚动与数字键；键盘锁时只有Start、Stop、Prep Run、Load Method/Seq、Seq、Seq Control（与Options）；被调用能力的前提同样适用（被拒时显示仪器信息，手势不被接受）',
    'panel.switch': '电源开关：被调用能力（power.set）的前提适用', 'panel.capture': '无', 'panel.frame': '无（评测者）',
}
PRE_SLOTS = {'run.start': ['power.on', 'run.state', 'run.ready'], 'thermal.set_temperature': ['power.on', 'config.state', 'status.shutdowns'],
             'method.load': ['power.on', 'methods.stored', 'run.state'], 'config.module': ['power.on', 'options.state'], 'panel.press': ['power.on', 'options.state']}
SCENE_SLOTS = {'oven.door_open', 'syringe.inserted', 'power.on', 'leds', 'status.lines', 'display.message'}
RUN_GUARDED = frozenset([                                               # commands that stop or change the active run (power switch: physical)
    'run.stop', 'run.start', 'run.prep', 'sequence.control', 'sequence.configure', 'sequence.set_subsequence', 'sequence.load', 'method.load',
    'thermal.set_temperature', 'thermal.set_zone', 'oven.set_initial_time', 'oven.set_ramp', 'oven.set_post_run', 'inlet.configure', 'inlet.set_gas',
    'column.configure', 'column.set_ramp', 'detector.configure', 'detector.set_gas', 'detector.set_flame', 'detector.manual_ignite', 'analog.configure',
    'column_comp.run', 'schedule.sleep_now', 'schedule.wake_now', 'config.oven', 'config.inlet', 'config.column', 'config.detector', 'config.module',
    'config.instrument', 'calibration.configure', 'calibration.zero', 'calibration.column', 'calibration.reset_column', 'inlet.leak_check',
    'options.reboot', 'fault.clear'])

LIMITATIONS = [
    '所选配置：7890B单个前分流/不分流进样口、一根毛细管柱（30 m×0.25 mm×0.25 µm，旧模型默认）与前FID；手动针筒进样。没有ALS、阀、后进样口与后检测器、Aux EPC/热区、冷媒阀、氢气传感器、条码阅读器与数据系统：对应键显示Not installed（硬件缺失）。',
    '过程层仍是scripts/gc_runtime.py的合成教学模型（三种合成物种、集总热区、恒定或按程序变化的柱流量、高斯色谱带、FID电荷），参数非厂家标定；本运行层以子类方式加入固件，不修改该文件（GC8890B资产也继承它）。',
    '气路：柱流量按可压缩Hagen–Poiseuille定律（NTP 25 °C、1 atm）由柱前压、柱尺寸、载气黏度与柱温计算；黏度用幂律近似（工程值）。GC按配置的柱与载气算压力，实际流量按安装的柱与实际载气算，二者不一致时显示与实际不符，色谱柱校准可修正配置。分流按分流比×计算柱流量；省气、脉冲、Prep Run吹扫阀按手册与假设实现；不分流吹扫阀的开关时刻沿用旧模型（进样时关闭，吹扫时间后打开）。',
    '传感器误差（进样口流量、隔垫吹扫流量与压力的零点误差）由评测者设定，默认0（理想）；Auto flow zero与手动调零存储并扣除零点。控制器按自己的（含误差的）读数调节：读数保持在设定值，未扣除的误差体现在实际柱前压、分流与隔垫吹扫流量上（各读取路径与面板一致）。真实测量误差、漂移与公差留给后续“校准/仿真与现实差距”任务。',
    'FID：点火后输出含背景电流10 pA（取手册检查范围5–20 pA内的假设值）；GC比较输出与Lit offset，低于时每5 s尝试重新点火，3次后关断氢气（Front det flame out）；静电计关闭时不做熄火判断（假设）。',
    '就绪：沿用旧模型（各区温度在±1 K内稳定5 s、载气/氢气/空气/尾吹气有流、火焰点燃），并按固件规则排除关闭的加热区、Ignore Ready的元件、有意关闭的检测器气体与火焰，加入柱箱平衡时间、关断与休眠。Require Host Connection为On时远程主机的Ready计入就绪（手册p.183）；本配置没有数据系统，主机从不报告Ready，GC保持Not Ready（状态行“Host not ready”，措辞为假设）。',
    '模拟输出1/2：按手册p.169的带宽做一阶低通（标准3.0 Hz，Fast peaks为8 Hz），输入为积分步内的平均信号；更换信号源时输出从新信号开始（假设）；数字输出（detector.output_a）不受Fast peaks影响。',
    '运行：柱箱程序总时间>0时运行在程序结束时自动结束（999.99 min上限），之后Post Run；程序时间为0时运行持续到Stop（与旧模型一致）。手动进样不增加EMF进样计数（手册p.118）。',
    '序列：可编辑、存储、载入与控制，但子序列进样需要ALS或进样阀（本配置没有），启动后状态为No sequence；优先序列与序列后方法只存储。',
    '时钟表事件只实现“载入方法”与“Prep Run”（完整事件列表在未保存的Advanced Operation Manual）；仪器日程实现休眠/唤醒（当前方法、WAKE方法、CONDITION后唤醒）。CONDITION为不采集数据的空白运行。',
    '方法与配置：方法保存设定与随方法保存的配置；模块安装/移除在重新开机后生效，载入模块不符的方法时该模块参数恢复默认（Method mismatch）。语言Chinese只作为设定保存，在Options > Keyboard and Display页与options.state中显示；随附手册没有中文菜单文本、5×7点阵字库没有汉字，屏幕仍画英文（不自行翻译）。',
    '运行归属（负责人规则2026-10-19，仿真规则，真实GC没有调用者概念）：由某agent（Start键、run.start、column_comp.run、schedule.wake_now）启动的运行（含Post Run、空白运行与CONDITION运行）属于该agent；运行期间另一agent停止或修改该运行（Stop、Prep Run、Start、方法载入、柱箱/进样口/柱/检测器/模拟输出设定、配置、校准、序列控制、休眠/唤醒、清除关断、重启）在面板与直接调用两条路径上都被拒（ACCESS_DENIED，状态不变），面板最后一行显示“Run belongs to another user”最多5 s（措辞为假设）；主电源开关与插拔电源线（硬开关，负责人澄清10-19）、柱箱门、气体与针筒等物理动作不受限制：关掉电源时运行按断电结束；评测者不受限制；时钟表/日程在sim.step中启动的运行不属于任何agent。',
    '面板层（panel.*）：按panel_ui.json（手册页码证据）实现4×32字符显示、12个状态灯、63个按键与电源开关；Advanced Operation Manual中的参数行名称与顺序为假设（spec中标注）；手势不推进仿真时钟。',
    '场景：URDF/GLB有本体、柱箱门、检测器护罩、启动/停止键、参数键组（整体一个网格）、显示屏与电源开关；单个按键与状态灯没有独立网格（几何缺失，见各组件说明）；屏幕纹理与按键关节触发属于后续Matterix步骤。',
]


def export_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'], reference_conditions=
                        ('NTP（25 °C、101.325 kPa）体积流量' if v['unit'] == 'm3/s' else '表压（相对大气压）' if v['unit'] == 'Pa' else None),
                        meaning_zh=v['meaning'], origin=v['origin'], access='read_only', visibility=v['visibility'],
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'GC7890BSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description='宿主时钟推进时更新' if v['origin'] in ('sensor', 'derived', 'process_truth')
                                      else '命令被接受时改变'), source_ids=['runtime_source'])
    for k, v in ALL_CAPS.items():
        un = k in UNSUPPORTED
        errs = ['UNSUPPORTED'] + _COMMON if un else list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON + (['ACCESS_DENIED'] if v['visibility'] != 'agent' or k in RUN_GUARDED else [])))
        fx = [s for s in EFFECTS.get(k, []) if s in SLOTS]
        on_step = [s for s in _EVOLVE if s in SLOTS] if k in ('sim.step',) else []
        seen = sorted((set(fx) | set(on_step)) & SCENE_SLOTS)
        caps[k] = dict(
            function_zh=v['function_zh'], kind=v['kind'], visibility=v['visibility'], component_ids=list(v['component_ids']),
            availability='unsupported' if un else 'implemented', input_schema=copy.deepcopy(v['input_schema']), output_schema=copy.deepcopy(v['output_schema']),
            implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ('process', 'scene', 'robot', 'hardware', 'host')},
            binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.agilent_7890b_ssl_fid.GC7890BSession.invoke',
                                         invocation=f'session.invoke(instance_id="agilent_7890b_ssl_fid_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
            preconditions=[] if un else [dict(description_zh=PRECONDITIONS.get(k, '无') + '；被拒时状态不变。', state_ids=PRE_SLOTS.get(k, ['power.on'] if 'POWER_OFF' in errs else []),
                                              enforced_by='runtime', check='GC7890BRuntime.invoke')],
            state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in fx] +
                          [dict(state_id=s, timing='on_step', description_zh='随宿主时钟演化（固件在每个积分步前后运行）') for s in on_step if s not in fx],
            observes=[], model_effects=(dict(mode='host_required', binding_ids=[], description_zh='状态需宿主桥（Matterix）镜像到场景（' + '、'.join(seen) + '），运行层本身不驱动模型')
                                        if seen and not un else dict(mode='none', binding_ids=[], description_zh='过程或设定变化，没有对应的场景关节或材质')),
            execution=dict(mode='synchronous', time_domain='simulation',
                           completion_criterion=('手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕须用panel.capture查看' if k in ('panel.press', 'panel.switch')
                                                 else '原子设定、配置或查询（温度、流量与运行在之后的sim.step中演化）'),
                           completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id',
                           resources=['host_clock'] + (['robot_or_operator'] if v['kind'] == 'robot_skill' else []),
                           partial_failure_zh='不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时会话恢复调用前状态'),
            errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in errs],
            example_arguments=copy.deepcopy(v['example_arguments']), verification=[],
            limitations=[UNSUPPORTED[k]] if un else (['面板层按panel_ui.json（手册页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟；手册未写之处的选择见panel_ui.json的assumptions。']
                                                     if k.startswith('panel.') else ['过程层实现；机器人、场景与硬件层未实现；模型边界见mechanisms与limitations。']),
            source_ids=['runtime_source', 'operation_manual'] + (['panel_spec'] if k.startswith('panel.') else []))
    mech = dict(component_ids=['oven_heater', 'inlet_body', 'carrier_epc', 'column', 'fid_body', 'electronics'], state_ids=[s for s in SLOTS if s != 'truth.process'] + ['truth.process'],
                meaning_zh='7890B固件（方法、运行控制、气路计算、就绪与关断、配置与选项、自动化与维护）运行在旧合成过程模型（集总热区、分流/不分流传质、色谱带、FID电荷）之上',
                implementation='lumped',
                equations=['F_NTP = pi d^4 (P_i^2 - P_o^2) T_NTP / (256 eta(T) L P_NTP T_col)', 'eta(T) = eta0 (T / 273.15 K)^n',
                           'split vent = split ratio x F_calc; gas saver: split vent <= saver flow - F - septum purge',
                           'T_oven(t) = program(initial, initial time, rates, finals, final times; t)', 'FID output = analyte current + background while lit'],
                parameters={'viscosity_' + k.lower(): dict(value=list(v), unit='Pa s, 1', meaning_zh=f'{k}黏度幂律参数(eta0, n)', evidence='assumption', source_ids=['runtime_source'],
                                    uncertainty='工程近似，约±5 %') for k, v in VISCOSITY.items()} |
                           {'fid_background_a': dict(value=FID_BACKGROUND_A, unit='A', meaning_zh='FID点火后的背景电流', evidence='assumption', source_ids=['operation_manual'],
                                                     uncertainty='手册检查范围5–20 pA内取值'),
                            'flow_shutdown_s': dict(value=FLOW_SHUTDOWN_S, unit='s', meaning_zh='进样口流量达不到设定后关断的时间', evidence='documented',
                                                    source_ids=['operation_manual'], uncertainty=None),
                            'max_run_s': dict(value=MAX_RUN_S, unit='s', meaning_zh='最大分析时间999.99 min', evidence='documented', source_ids=['datasheet'], uncertainty=None)},
                source_ids=['runtime_source', 'operation_manual', 'datasheet'], limitations=list(LIMITATIONS))
    return dict(asset_type_id=ASSET_TYPE_ID, capabilities=caps, state_slots=slots, mechanisms={'gc_firmware_process': mech})
