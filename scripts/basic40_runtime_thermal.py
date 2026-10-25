"""CHEM-185 Nabertherm L 9/11/SKM: synthetic empty-furnace runtime.

NaberthermL9Runtime is the trusted host and sole clock owner. Its direct
describe/read/invoke/write methods always use an agent identity; evaluator
access requires a separately host-registered session. This is API filtering,
not a Python-process or filesystem sandbox. No hardware/robot driver exists.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import io
import json
import math
import platform
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from basic40_runtime import PROTOCOL_VERSION, SceneSynchronizationError, _number

NABERTHERM_TYPE = "instrument.nabertherm_l9"
NABERTHERM_AMBIENT_K = 298.15
NABERTHERM_POWER_W = 3400.0
NABERTHERM_MAX_K = 1373.15
NABERTHERM_REFERENCE_K = 1273.15
NABERTHERM_REFERENCE_S = 4500.0
NABERTHERM_LOSS_W_K = 2.0  # Assumption: one heat-up point cannot identify C and UA.
NABERTHERM_CAPACITY_J_K = -NABERTHERM_LOSS_W_K * NABERTHERM_REFERENCE_S / math.log1p(
    -NABERTHERM_LOSS_W_K * (NABERTHERM_REFERENCE_K - NABERTHERM_AMBIENT_K) / NABERTHERM_POWER_W)
NABERTHERM_OPEN_LOSS_W_K = 18.0  # Additional open-door loss; unmeasured assumption.
NABERTHERM_DOOR_MAX_RAD = math.pi / 2
NABERTHERM_SOURCE = "https://nabertherm.com/sites/default/files/2021-01/laboratory_english.pdf#page=19"
NABERTHERM_MANUAL = "https://nabertherm.com/sites/default/files/noindex/2023-01/M01.1060_English_2022-12_0.pdf"


def _object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _number_schema(unit, minimum=None, maximum=None):
    result = {"type": "number", "x-unit": unit}
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    return result


def _nabertherm_slot(unit, origin, schema, visibility="agent", route=None):
    return {"unit": unit, "origin": origin, "visibility": visibility,
            "access": "read_write" if route else "read_only", "write_via": route,
            "value_schema": schema, "sampling": {"mode": "instant", "time_domain": "simulation", "window": None},
            "reference_conditions": None}


def export_nabertherm_contract_metadata(panel=True):
    """Type-only source of truth; no live temperatures, identities, or logs.  The B510 controller functions, the evaluator diagnostics and the
    front-panel commands are added in separate groups (controller_capabilities, evaluator_capabilities, panel_capabilities); panel=False skips
    loading panel_ui.json."""
    segments = {"type": "array", "minItems": 1, "maxItems": 4,
        "items": _object_schema({"temperature_K": _number_schema("K", 273.15, NABERTHERM_MAX_K),
            "ramp_rate_K_s": _number_schema("K/s", 0.001, 1.0), "hold_s": _number_schema("s", 0, 86400)})}
    slots = {
        "power.enabled": _nabertherm_slot("1", "setpoint", {"type": "boolean"}, route={"capability_id": "power.set", "parameter": "enabled"}),
        "thermal.target_k": _nabertherm_slot("K", "setpoint", _number_schema("K"), route={"capability_id": "thermal.set_temperature", "parameter": "temperature_K"}),
        "thermal.temperature_k": _nabertherm_slot("K", "process_truth", _number_schema("K"), "debug"),
        "thermal.indicated_temperature_k": _nabertherm_slot("K", "derived", _number_schema("K")),
        "door.angle_rad": _nabertherm_slot("rad", "scene", _number_schema("rad", 0, NABERTHERM_DOOR_MAX_RAD)),
        "door.closed": _nabertherm_slot("1", "derived", {"type": "boolean"}),
        "heater.enabled": _nabertherm_slot("1", "derived", {"type": "boolean"}),
        "program.segments": _nabertherm_slot("1", "setpoint", {**segments, "minItems": 0}),
        "program.running": _nabertherm_slot("1", "derived", {"type": "boolean"}),
        "program.segment_index": _nabertherm_slot("1", "derived", {"type": "integer", "minimum": 0, "maximum": 4}),
        "program.phase": _nabertherm_slot("1", "derived", {"type": "string", "enum": ["idle", "ramp", "hold", "paused_door", "completed", "stopped"]}),
        "program.hold_elapsed_s": _nabertherm_slot("s", "derived", _number_schema("s", 0)),
    }
    for sid, slot in slots.items():
        slot["read_binding"] = f"NaberthermL9Session.read(instance_id=..., state_ids=['{sid}'])"
        if slot["origin"] == "derived":
            slot["observation_note_zh"] = "仿真派生值，quality=estimated；不是厂家实测读数。"
    definitions = {
        "power.set": ({"enabled": {"type": "boolean"}}, {"enabled": {"type": "boolean"}}, {"enabled": True}, "设置供电；断电停止程序并保持已发生的热状态。", ["power.enabled", "heater.enabled", "program.running", "program.phase"]),
        "thermal.set_temperature": ({"temperature_K": _number_schema("K", 273.15, NABERTHERM_MAX_K)}, {"setpoint_K": _number_schema("K")}, {"temperature_K": 1273.15}, "只写温度目标；实际炉温由宿主step推进。", ["thermal.target_k"]),
        "door.open": ({}, {"angle_rad": _number_schema("rad"), "closed": {"type": "boolean"}}, {}, "门绕+X向下翻90°；切断加热（F p.51），运行中的程序照常走时（B510：W19，p.123）。", ["door.angle_rad", "door.closed", "heater.enabled"]),
        "door.close": ({}, {"angle_rad": _number_schema("rad"), "closed": {"type": "boolean"}}, {}, "关闭炉门；已供电时恢复加热，程序继续。", ["door.angle_rad", "door.closed", "heater.enabled"]),
        "thermal.configure_program": ({"segments": segments}, {"segment_count": {"type": "integer", "minimum": 1, "maximum": 4, "x-unit": "1"}}, {"segments": [{"temperature_K": 473.15, "ramp_rate_K_s": 0.1, "hold_s": 60}]}, "配置单个模拟温程，最多4段；每段包含目标、目标变化速率和到温后的保温时间。", ["program.segments", "program.segment_index", "program.phase", "program.hold_elapsed_s"]),
        "thermal.start_program": ({}, {"running": {"type": "boolean"}}, {}, "从理想温度显示开始执行已配置温程；到温后才计时保温。", ["program.running", "program.segment_index", "program.phase", "program.hold_elapsed_s", "thermal.target_k"]),
        "thermal.stop_program": ({}, {"running": {"type": "boolean"}}, {}, "停止温程并将目标退回环境温度；不瞬间冷却。", ["program.running", "program.phase", "thermal.target_k"]),
    }
    caps = {}
    for name, (inputs, outputs, example, description, immediate) in definitions.items():
        effects = [{"state_id": sid, "timing": "immediate", "description_zh": description} for sid in immediate]
        if name != "thermal.configure_program":
            effects += [{"state_id": sid, "timing": "on_step", "description_zh": "宿主推进的空炉热响应和理想显示。"}
                for sid in ("thermal.temperature_k", "thermal.indicated_temperature_k")]
        if name in ("thermal.start_program", "door.open", "door.close"):
            effects += [{"state_id": sid, "timing": "on_step", "description_zh": "1s以内积分步长执行温程，门开时加热切断但程序照常走时；程序完成后目标退回环境。"}
                for sid in ("program.running", "program.segment_index", "program.phase", "program.hold_elapsed_s", "thermal.target_k")]
        observes = {"power.set": ["power.enabled"], "thermal.set_temperature": ["thermal.target_k"],
            "door.open": ["door.angle_rad", "door.closed"], "door.close": ["door.angle_rad", "door.closed"],
            "thermal.configure_program": ["program.segments"], "thermal.start_program": ["program.running"], "thermal.stop_program": ["program.running"]}[name]
        preconditions = []
        if name in ("thermal.set_temperature", "thermal.configure_program", "thermal.start_program"):
            preconditions.append("本机已上电。")
        if name in ("thermal.set_temperature", "thermal.configure_program", "thermal.start_program"):
            preconditions.append("温程未运行。")
        if name in ("thermal.set_temperature", "thermal.configure_program"):
            preconditions.append("温度目标不低于宿主环境，且不高于1373.15 K。")
        if name == "thermal.start_program":
            preconditions.append("已配置至少1段，且炉门已关闭。")
        if name.startswith("door."):
            preconditions.append("已注册同步场景回调；否则unsupported。")
        caps[name] = {"availability": "implemented", "visibility": "agent",
            "kind": "configuration" if name == "thermal.configure_program" else "device_command",
            "description_zh": description, "input_schema": _object_schema(inputs), "output_schema": _object_schema(outputs),
            "example_arguments": [example], "state_effects": effects, "observes": observes,
            "binding": f"basic40_runtime_thermal.NaberthermL9Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
            "scene_backend_required": name.startswith("door."), "preconditions_zh": preconditions,
            "completion_criterion_zh": "返回值确认本次命令已处理；升温和程序完成须随后读取观测。"}
    declared = {}
    for name, description in {"thermal.program_library": "B410五程序存储库未实现；本运行层只有单个最多4段的模拟温程。",
        "atmosphere.control": "保护气接口不是气密炉膛；本运行层不执行气氛控制。",
        "sample.calcination": "未建立固相反应、灰化、负载热容或样品产物模型。"}.items():
        declared[name] = {"availability": "unsupported", "visibility": "agent", "kind": "device_command",
            "description_zh": description, "input_schema": _object_schema({}), "output_schema": _object_schema({}),
            "example_arguments": [{}], "state_effects": [], "observes": [], "binding": None}
    def param(value, unit, evidence, description, uncertainty=None):
        return {"value": value, "unit": unit, "evidence": evidence, "source": description, "uncertainty": uncertainty}
    parameters = {
        "heater_power_W": param(NABERTHERM_POWER_W, "W", "assumption", "2021目录第19页整机connected load为3.4kW，230V 1/N/PE；模型以该值作为最大等效热输入并随热容共同拟合，不是独立测得的加热器功率。", "电热效率与辅助电耗未独立识别；不混入新版3.7kW。"),
        "maximum_temperature_K": param(NABERTHERM_MAX_K, "K", "public_document", "厂家最高1100°C。"),
        "long_hold_recommended_temperature_K": param(NABERTHERM_REFERENCE_K, "K", "public_document", "厂家长保温建议1000°C；建议值不同于最高温度，不硬编码成硬件互锁。"),
        "reference_heatup_s": param(NABERTHERM_REFERENCE_S, "s", "public_document", "空炉闭门、230V到Tmax−100K约75min；目录未说明初温。"),
        "calibration_ambient_K": param(NABERTHERM_AMBIENT_K, "K", "assumption", "反推热容时假定初始和环境温度25°C。", "初温未由目录给定。"),
        "heat_loss_W_K": param(NABERTHERM_LOSS_W_K, "W/K", "assumption", "闭门有效线性散热UA；一条升温点不足以同时识别C与UA。", "未实测；UA=1..3 W/K敏感性在calibration_report中。"),
        "thermal_capacity_J_K": param(NABERTHERM_CAPACITY_J_K, "J/K", "derived", "C=-UA*t/log(1-UA*(Tref-Ta)/P)，由75min空炉点在明确UA、环境假设下反推；50kg整机质量未参与。", "条件拟合，不是炉衬/炉膛材料热容或独立实验验证。"),
        "door_open_loss_W_K": param(NABERTHERM_OPEN_LOSS_W_K, "W/K", "assumption", "开门额外线性散热；仅用于仿真响应。", "开门冷却无厂家标定。"),
        "maximum_door_angle_rad": param(NABERTHERM_DOOR_MAX_RAD, "rad", "assumption", "场景向下翻门+X轴0..90°，未测厂家关节极限。"),
        "door_open_heater_fraction": param(0.0, "1", "public_document", "M01.1060 Rev2022-12 §6.6第54页：开门中断加热、关门自动恢复，LE例外不适用L...SKM；实现手册支持的门互锁行为，未作实机验证。"),
        "program_maximum_segments": param(4, "1", "public_document", "B410每程序4段；只模拟一个程序，不实现五槽存储。"),
        "program_maximum_ramp_rate_K_s": param(1.0, "K/s", "assumption", "目标斜率允许0.001..1K/s；真实升温仍受3400W和散热限制。"),
        "program_hold_tolerance_K": param(0.5, "K", "assumption", "实际模型温度距段目标<=0.5K才累加保温；不声称厂家温控精度。"),
        "program_integration_maximum_s": param(1.0, "s", "assumption", "温程状态机最大积分步长；转换时间分辨率<=1s。"),
    }
    host_caps = {"sim.step": {"kind": "simulation", "visibility": "trusted_host_only",
        "input_schema": _object_schema({"dt_s": _number_schema("s", 0, 86400)}),
        "binding": "basic40_runtime_thermal.NaberthermL9Runtime.step(dt_s)",
        "completion_criterion": "所有注册实例推进同一时长，随后统一发布世界时钟。",
        "partial_failure": "SceneSynchronizationError表示过程和时钟已推进；场景回调可有部分效果。"}}
    return _b510_extend_metadata({"asset_types": {NABERTHERM_TYPE: {"catalog_id": "CHEM-185", "model": "Nabertherm L 9/11/SKM, catalogue configuration",
        "asset_version": "1.1.0", "state_slots": slots, "capabilities": caps, "declared_capabilities": declared,
        "parameters": parameters}}, "host_capabilities": host_caps,
        "trusted_scene_callback": "scene_adapter(instance_id, updates): synchronous SI state updates; host owns door angle and display",
        "result_schema_scope": "capability.output_schema validates response.result only"}, panel)


# ---- G02 front panel: the B510 touch controller (5 programs x 4 segments, run engine with holdback, pause, segment jump and run-time changes,
# delayed start, power-outage modes, user levels and locks, calibration, start-up circuit, solar mode, smoothing, alarms, gradient monitoring,
# extra functions, NTLog records, statistics, settings) behind panel_ui.json.  Controller manual M03.0022 (PDF pages 'p.N'), furnace manual
# M01.1060 ('F p.N').  The B510 replaces the catalogue's B410 (current L 9/11/SKM catalogue, p.11).
import re as _b510_re
from datetime import timedelta as _b510_timedelta

NABERTHERM_ASSET_DIR = Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t1_thermal/nabertherm_l9'
_B510_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)       # controller clock at simulation time 0 until set (assumption)
_B510_SLOTS, _B510_SEGMENTS, _B510_EXTRAS, _B510_CATEGORIES = 5, 4, 2, 6          # p.18-19, p.35, p.78
_B510_MAX_C = 1100.0                                          # furnace maximum: keypad / import limit (p.97; keypad INFERRED)
_B510_TIME_MAX_MIN = 29999                                    # 499:59 h (p.42, M p.20)
_B510_RATE_MAX = 9999                                         # degC/h (M p.20, INFERRED)
_B510_HB_MAX = 99                                             # holdback band 0-99 K (M p.21)
_B510_AUTO_HB_K = 2.0                                         # AUTO holdback: dwell temperature counts as reached within 2 K (assumption, p.46 silent)
_B510_SUBSTEP_S = 1.0
_B510_GRAPH_S, _B510_GRAPH_MAX = 30.0, 20160                  # one point every 30 s for one week (p.30)
_B510_PASSWORDS = {'operator': '000011', 'supervisor': '000021', 'administrator': '000031'}   # p.72
_B510_LEVEL = {'operator': 1, 'supervisor': 2, 'administrator': 3}
_B510_LANGUAGES = ('en', 'de', 'fr', 'it', 'es', 'ru', 'da', 'nl', 'pl', 'pt')               # p.21 scr
_B510_OVER_K, _B510_OVER_DELAY_S, _B510_OVER_FROM_C = 50.0, 180.0, 200.0                    # 04-02 (p.20, p.120)
_B510_NOHEAT_S = 720.0                                        # 04-01: no rise in a ramp for 12 min (p.120)
_B510_BRIEF_OUTAGE_S = 5.0                                    # outages under 5 s always continue (p.87)
_B510_OUTAGE_DT_K, _B510_OUTAGE_TMIN_C, _B510_OUTAGE_TIME_S = 50.0, 80.0, 120.0            # DELTA T / T min / TIME (p.87)
_B510_COLD_C = 60.0                                           # self-optimization only below 60 degC (p.64)
_B510_RECORDS_PER_FILE, _B510_FILES = 5610, 16                # NTLog without a stick (p.106)
_B510_SERIAL = '15020030'                                     # the manual's example serial number (p.103); not a real unit
_B510_ERRORS = {   # p.118-121; (1) = acknowledged only by switching off
    '01-01': 'Bus communication control module', '01-02': 'Bus communication Ethernet/USB module', '01-03': 'Bus communication IO module',
    '02-01': 'Thermocouple interrupted', '02-02': 'Thermocouple connection', '02-03': 'Reference point error', '02-04': 'Reference point too hot',
    '02-05': 'Reference point too cold', '02-06': 'Encoder disconnected', '02-07': 'Sensor element defective', '03-01': 'System memory (1)', '03-02': 'ADC error (1)',
    '03-03': 'File system defective', '03-04': 'System monitoring', '03-05': 'Zone system monitoring (1)', '03-06': 'Self-test error (1)',
    '03-07': 'Analog output 1 (1)', '03-08': 'Analog output 2 (1)', '04-01': 'No heating power', '04-02': 'Excess temperature', '04-03': 'Power failure',
    '04-04': 'Alarm', '04-05': 'Self-optimization failed', '05-00': 'General malfunction'}
_B510_RAISED = ('04-01', '04-02', '04-03', '04-04', '04-05')  # raised by the simulation itself; the others only by evaluator injection
_B510_POWER_OFF_ONLY = ('03-01', '03-02', '03-05', '03-06', '03-07', '03-08')
_B510_WARNINGS = {'W00': 'Gradient monitoring', 'W05': 'Power failure', 'W06': 'Alarm 1 - Band', 'W07': 'Alarm 1 - Min', 'W08': 'Alarm 1 - Max',
                  'W09': 'Alarm 2 - Band', 'W10': 'Alarm 2 - Min', 'W11': 'Alarm 2 - Max', 'W16': 'No USB flash drive inserted', 'W19': 'Door/cover is open',
                  'W20': 'Alarm 3', 'W21': 'Alarm 4', 'W22': 'Alarm 5', 'W23': 'Alarm 6', 'W24': 'Alarm 1', 'W25': 'Alarm 2'}
_B510_STATUS_WARNINGS = {'W01': 'No control parameters', 'W18': 'Heating blocked'}            # shown while the cause lasts (p.121-123)
_B510_ALARM_CODE = {(0, 'band'): 'W06', (0, 'min'): 'W07', (0, 'max'): 'W08', (1, 'band'): 'W09', (1, 'min'): 'W10', (1, 'max'): 'W11'}
_B510_EXAMPLE_NAMES = ('Exhaust air flap', 'Fresh-air flap')  # p.79 scr
_T = lambda lo=0, hi=_B510_MAX_C: {'type': 'number', 'x-unit': 'degC', 'minimum': lo, 'maximum': hi}   # noqa: E731
_B510_SETTINGS = {   # key: (level to change, schema, default) - levels p.72-76, defaults p.58-60, p.87, p.90, p.105
    'furnace_name': (2, {'type': 'string', 'minLength': 1, 'maxLength': 19}, 'Nabertherm 01'),
    'language': (1, {'type': 'string', 'enum': list(_B510_LANGUAGES)}, 'en'),
    'brightness_percent': (1, {'type': 'number', 'x-unit': '%', 'minimum': 10, 'maximum': 100}, 100),
    'unit': (3, {'type': 'string', 'enum': ['C', 'F']}, 'C'),
    'date_format': (3, {'type': 'string', 'enum': ['DD.MM.YYYY', 'MM-DD-YYYY']}, 'DD.MM.YYYY'),
    'time_format': (3, {'type': 'string', 'enum': ['24h', '12h']}, '24h'),
    'apply_actual': (3, {'type': 'boolean'}, True), 'apply_actual_min_C': (3, _T(), 0.0),
    'smoothing_s': (3, {'type': 'number', 'x-unit': 's', 'minimum': 0, 'maximum': 3600}, 0.0),
    'startup_on': (3, {'type': 'boolean'}, False), 'startup_limit_C': (3, _T(), 200.0),
    'startup_max_power_percent': (3, {'type': 'number', 'x-unit': '%', 'minimum': 0, 'maximum': 100}, 50.0),
    'damping_on': (3, {'type': 'boolean'}, False), 'damping_limit_C': (3, _T(), 1100.0),
    'damping_max_integrator_percent': (3, {'type': 'number', 'x-unit': '%', 'minimum': 0, 'maximum': 100}, 68.0),
    'solar_mode': (3, {'type': 'string', 'enum': ['off', 'ramps', 'ramps_holds']}, 'off'), 'solar_hints': (3, {'type': 'boolean'}, True),
    'solar_on_ramp_K': (3, {'type': 'number', 'x-unit': 'K', 'minimum': -50, 'maximum': 0}, -3.0),
    'solar_off_ramp_K': (3, {'type': 'number', 'x-unit': 'K', 'minimum': -50, 'maximum': 50}, 0.0),
    'solar_on_hold_K': (3, {'type': 'number', 'x-unit': 'K', 'minimum': -50, 'maximum': 0}, -3.0),
    'solar_off_hold_K': (3, {'type': 'number', 'x-unit': 'K', 'minimum': -50, 'maximum': 50}, 0.0),
    'power_outage_mode': (3, {'type': 'string', 'enum': ['cancel', 'delta_t', 'time', 'continue']}, 'time'),
    'gradient_on': (3, {'type': 'boolean'}, False), 'gradient_min_C': (3, _T(), 200.0),
    'gradient_max_K_h': (3, {'type': 'number', 'x-unit': 'K/h', 'minimum': 1, 'maximum': _B510_RATE_MAX}, 300.0),
    'gradient_interval_s': (3, {'type': 'number', 'x-unit': 's', 'minimum': 1, 'maximum': 3600}, 60.0),
    'gradient_delay_n': (3, {'type': 'integer', 'x-unit': '1', 'minimum': 1, 'maximum': 99}, 3),
    'documentation_on': (3, {'type': 'boolean'}, False),
    'documentation_interval_s': (2, {'type': 'number', 'x-unit': 's', 'minimum': 10, 'maximum': 3600}, 60.0),
    'documentation_end': (2, {'type': 'string', 'enum': ['program_end', 'below_limit']}, 'program_end'),
    'documentation_limit_C': (2, _T(), 200.0),
    'usb_on': (3, {'type': 'boolean'}, True), 'wifi_on': (3, {'type': 'boolean'}, False),
    'standard_user': (3, {'type': 'string', 'enum': ['operator', 'supervisor', 'administrator']}, 'operator'),
    'logoff_min': (3, {'type': 'number', 'x-unit': 'min', 'minimum': 0, 'maximum': 600}, 0.0),
    'operation_lock': (3, {'type': 'boolean'}, False),
}
_B510_ALARM_DEFAULT = {'source': 'off', 'range': 'always', 'min_K': 0.0, 'max_K': 0.0, 'delay_s': 60.0, 'type': 'going_report', 'reaction': 'relay'}
_B510_PID_TS = 1.0                                            # controller cycle (s), assumption (Tcyc of p.54 not given)


def _b510_deadbeat(capacity, loss, power, ts=_B510_PID_TS):
    """XP (K for 100 %), TI (s), TD (s) that settle the lumped plant C dT/dt = P - UA (T - Ta) in two controller cycles (pole placement at 0):
    Kp = a / b, Ki = 1 / b per cycle with a = exp(-UA Ts / C), b = (1 - a) / UA; XP = Pmax / Kp, TI = Kp Ts / Ki = a Ts."""
    a = math.exp(-loss * ts / capacity)
    b = (1.0 - a) / loss
    return power * b / a, a * ts, 0.0


_B510_FACTORY_XP, _B510_FACTORY_TI, _B510_FACTORY_TD = _b510_deadbeat(NABERTHERM_CAPACITY_J_K, NABERTHERM_LOSS_W_K, NABERTHERM_POWER_W)
_B510_PID_DEFAULT = [[0.0, 1100.0, _B510_FACTORY_XP, _B510_FACTORY_TI, _B510_FACTORY_TD]] + [[0.0, 0.0, 0.0, 0.0, 0.0] for _ in range(9)]   # factory base point (assumption)


def _g02_pid_sample(pid, e, kp, ti, td, h, lo, hi, i_lo=-100.0, i_hi=100.0, d_lim=100.0, d_n=10.0):
    """One sample of a positional PID in output units: P = kp e; I += kp e h / ti (ti <= 0: no integral); D = kp td (e - e_prev) / h through a
    first-order filter of time constant td / d_n (assumption, as usual for a derivative on a plant without lag; 0 on the first sample),
    limited to +-d_lim; I within [i_lo, i_hi]; back-calculation anti-windup: when P + I + D leaves [lo, hi], I is reduced so the sum meets
    the limit.  pid holds i, e_prev, df (filtered D) and the shares p / d / out; returns the output."""
    p = kp * e
    i = min(i_hi, max(i_lo, pid['i'] + kp * e * h / ti)) if ti > 0 else 0.0
    if pid.get('e_prev') is None or td <= 0:
        df = 0.0
    else:
        df = pid.get('df', 0.0) + h / (td / d_n + h) * (kp * td * (e - pid['e_prev']) / h - pid.get('df', 0.0))
    pid['df'] = df
    d = max(-d_lim, min(d_lim, df))
    if p + i + d > hi:
        i = max(i_lo, min(i, hi - p - d))
    elif p + i + d < lo:
        i = min(i_hi, max(i, lo - p - d))
    u = min(hi, max(lo, p + i + d))
    pid.update(i=i, e_prev=e, p=p, d=d, out=u)
    return u


def _g02_run_sampled(x, t0, dt, pid, ts, control, plant, saturated_for=None, settled=None, keep=2):
    """Sampled control of a single-node plant on the absolute time grid k*ts (zero-order hold between samples).  control(x) -> output and
    updates pid; plant(x, out, h) -> x after h s at a constant output; saturated_for(x, out) -> seconds the held output will stay the
    controller's choice (whole sample periods before that are integrated in one exact step, the last keep samples run normally, so that the
    controller state (integral at its limit, derivative filter) is the same as with every sample);
    settled(x) -> (x, out) when the loop is at rest (held exactly to the end of the step), else None.  Returns (x, [(out, seconds), ...])."""
    t, end, log = t0, t0 + dt, []
    while end - t > 1e-9:
        if pid.get('t_next') is None or t >= pid['t_next'] - 1e-9:
            rest = settled(x) if settled else None
            if rest is not None:
                x, out = rest
                pid['u'] = out
                pid['t_next'] = math.ceil(end / ts - 1e-9) * ts               # the next sample: the first grid time from the end on
                log.append((out, end - t))
                break
            pid['u'] = control(x)
            pid['t_next'] = (math.floor(t / ts + 1e-9) + 1) * ts
        seg_end = min(end, pid['t_next'])
        if saturated_for is not None and seg_end < end - 1e-9:
            s = saturated_for(x, pid['u'])
            if s is not None:
                n = math.floor((min(s, end - t) - (seg_end - t)) / ts + 1e-9) - keep
                if n >= 1:
                    seg_end += n * ts
                    pid['t_next'] += n * ts
                    pid['e_prev'], pid['df'] = None, 0.0                      # no derivative kick over the skipped samples
        h = seg_end - t
        x = plant(x, pid['u'], h)
        log.append((pid['u'], h))
        t = seg_end
    return x, log


def _b510_segment(target_C=500.0, mode='time', time_min=60.0, rate_K_h=100.0, hb_K=0.0, extras=(False, False)):
    return {'target_C': target_C, 'mode': mode, 'time_min': time_min, 'rate_K_h': rate_K_h, 'hb_K': hb_K, 'extras': list(extras)}


def _b510_program(slot):
    """A new program (p.26 scr): name P0n, start 'auto', holdback AUTO (INFERRED default), one segment, END."""
    return {'name': f'P{slot + 1:02d}', 'start_C': None, 'holdback': 'auto', 'segments': [_b510_segment()], 'end': 'end', 'end_extras': [False, False],
            'favourite': False, 'category': None}


def _b510_duration(seg, a_nom, from_C):
    """Run time (s) of a segment entered from from_C: a rate keeps its rate, a timed ramp its slope, a dwell its time; None = endless."""
    b = seg['target_C']
    if seg['mode'] == 'rate':
        return abs(b - from_C) / seg['rate_K_h'] * 3600.0
    if seg['time_min'] is None:
        return None
    d = seg['time_min'] * 60.0
    if abs(b - a_nom) < 1e-9 or d <= 0:
        return d
    return abs(b - from_C) * d / abs(b - a_nom)


def _b510_nominal(program, start_C):
    """Nominal start temperature of each segment (the previous target; the first one starts at the program start)."""
    out, a = [], start_C
    for seg in program['segments']:
        out.append(a)
        a = seg['target_C']
    return out


def _b510_plan(program, start_C):
    """[(start, target, duration or None)] of the whole program from start_C."""
    noms = _b510_nominal(program, start_C)
    return [(a, seg['target_C'], _b510_duration(seg, a, a)) for a, seg in zip(noms, program['segments'])]


def _b510_total_s(program, start_C=20.0):
    plan = _b510_plan(program, start_C)
    return None if any(d is None for _, _, d in plan) else sum(d for _, _, d in plan)


def _b510_offset(table, t_C):
    """Calibration offset (p.49-52): linear between ascending base points, from 0 at 0 degC to the first, the last offset above the last;
    a point at 0 degC or below (or out of order) ends the list."""
    pts = []
    for temp, off in table:
        if temp <= 0 or (pts and temp <= pts[-1][0]):
            break
        pts.append((temp, off))
    if not pts:
        return 0.0
    if t_C <= pts[0][0]:
        return pts[0][1] * max(t_C, 0.0) / pts[0][0]
    for (a, oa), (b, ob) in zip(pts, pts[1:]):
        if t_C <= b:
            return oa + (ob - oa) * (t_C - a) / (b - a)
    return pts[-1][1]


def _b510_valid(schema, v):
    """Argument check with the keywords the controller schemas use (anyOf / null, string length and pattern, sizes, bounds)."""
    if 'anyOf' in schema:
        return any(_b510_valid(x, v) for x in schema['anyOf'])
    t = schema.get('type')
    if t == 'null':
        return v is None
    if t == 'object':
        props = schema.get('properties', {})
        return (isinstance(v, dict) and all(k in v for k in schema.get('required', [])) and len(v) >= schema.get('minProperties', 0)
                and not (schema.get('additionalProperties') is False and any(k not in props for k in v)) and all(_b510_valid(props[k], x) for k, x in v.items() if k in props))
    if t == 'array':
        return (isinstance(v, list) and schema.get('minItems', 0) <= len(v) <= schema.get('maxItems', math.inf)
                and all(_b510_valid(schema.get('items', {}), x) for x in v))
    if t == 'string':
        return (isinstance(v, str) and ('enum' not in schema or v in schema['enum']) and schema.get('minLength', 0) <= len(v) <= schema.get('maxLength', math.inf)
                and ('pattern' not in schema or _b510_re.fullmatch(schema['pattern'], v) is not None))
    if t == 'boolean':
        return isinstance(v, bool)
    if t in ('number', 'integer'):
        return (_number(v) and (t == 'number' or isinstance(v, int)) and schema.get('minimum', -math.inf) <= v <= schema.get('maximum', math.inf)
                and ('enum' not in schema or v in schema['enum']))
    return True


def _b510_name_ok(name, n=19):
    return isinstance(name, str) and 1 <= len(name) <= n and name.strip() != '' and all(32 <= ord(c) < 127 for c in name)   # Latin only (p.38)


_B510_NEW_SLOTS = {   # id: (unit, origin, meaning, schema[, visibility])
    'b510.programs': ('1', 'setpoint', '程序库P01–P05（null为空位）：名称、起始温度（null=auto）、保持模式、最多4段（目标°C、时间min或速率K/h、保持带K、附加功能）、结束段END/REPEAT及其附加功能、收藏、类别',
                      {'type': 'array', 'minItems': 5, 'maxItems': 5}),
    'b510.categories': ('1', 'setpoint', '程序类别（最多6个）', {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'}}),
    'b510.editing': ('1', 'derived', '程序编辑器的工作副本（槽位、程序、是否已改）；未编辑为null', {'anyOf': [{'type': 'object'}, {'type': 'null'}]}),
    'b510.run': ('1', 'derived', '运行中的程序（槽位、程序副本、当前段、段计时、设定点°C、暂停、保持等待、延时启动、循环次数）；无程序为null', {'anyOf': [{'type': 'object'}, {'type': 'null'}]}),
    'b510.last_run': ('1', 'derived', '上次运行的程序（槽位、名称、开始时间）；主页Last program / Last run', {'anyOf': [{'type': 'object'}, {'type': 'null'}]}),
    'b510.extras': ('1', 'setpoint', '附加功能1–2：启用、名称、手动Auto/Off/On、当前输出、程序结束后保持', {'type': 'object'}),
    'b510.settings': ('1', 'setpoint', '控制器设定（炉名、语言、亮度、单位、格式、实际值接续、平滑、启动电路、阻尼、太阳能模式、断电模式、梯度监视、过程记录、接口、用户设定）', {'type': 'object'}),
    'b510.calibration': ('1', 'setpoint', '测量范围校准：10个基点[温度°C, 偏差K]', {'type': 'array', 'items': {'type': 'array'}}),
    'b510.pid': ('1', 'setpoint', '控制参数：10个基点[自°C, 至°C, XP K, TI s, TD s]；按设定值所在的温度范围选择，驱动加热功率的PID', {'type': 'array', 'items': {'type': 'array'}}),
    'b510.pid_out': ('1', 'derived', 'PID内部状态：P、I、D份额与输出%、所用基点、上一采样的偏差（控制周期1 s）；面板Actual values显示四舍五入的份额', {'type': 'object'}, 'debug'),
    'b510.alarms': ('1', 'setpoint', '报警1–6：来源、范围、下限/上限、延时、类型、反应', {'type': 'array', 'minItems': 6, 'maxItems': 6, 'items': {'type': 'object'}}),
    'b510.alarm_status': ('1', 'derived', '报警1–6的状态：条件持续时间s、是否已触发', {'type': 'array', 'items': {'type': 'object'}}),
    'b510.user': ('1', 'derived', '当前用户（状态栏右侧）；null=操作锁下无人登录', {'anyOf': [{'type': 'string', 'enum': ['operator', 'supervisor', 'administrator']}, {'type': 'null'}]}),
    'b510.locked': ('1', 'derived', '控制器锁（运行中的程序已锁定，状态栏挂锁）', {'type': 'boolean'}),
    'b510.warnings': ('1', 'derived', '待确认的警告[{code, text}]（W00、W05、W06–W11、W16、W19、W20–W25）', {'type': 'array', 'items': {'type': 'object'}}),
    'b510.error': ('1', 'derived', '当前故障{code, text}，null为无', {'anyOf': [{'type': 'object'}, {'type': 'null'}]}),
    'b510.error_log': ('1', 'derived', '故障记录Last errors[{code, text, time}]', {'type': 'array', 'items': {'type': 'object'}}),
    'b510.autotune': ('1', 'derived', '自优化：状态off/running/done/failed、优化温度、阶段、结果XP/TI/TD', {'type': 'object'}),
    'b510.statistics': ('1', 'derived', '统计：最高温度°C、kWh（上次/总计）、运行小时、启动次数（>200 °C、>1200 °C）、上次最高温度', {'type': 'object'}),
    'b510.graph': ('1', 'derived', '温度曲线点[运行s, 实际°C, 设定°C]（每30 s，最多1周；断电后清空）', {'type': 'array', 'items': {'type': 'array'}}),
    'b510.records': ('1', 'derived', 'NTLog内部文件[{file, program, records, last}]（无U盘最多16个）', {'type': 'array', 'items': {'type': 'object'}}),
    'b510.recording': ('1', 'derived', '程序结束后继续记录冷却（End of recording = below limit）', {'type': 'boolean'}),
    'b510.output_percent': ('%', 'derived', '控制区加热输出%（上一积分步的平均）', {'type': 'number', 'x-unit': '%', 'minimum': 0, 'maximum': 100}),
    'b510.clock_offset_s': ('s', 'setpoint', '控制器时钟相对仿真时钟的偏移', {'type': 'number', 'x-unit': 's'}),
    'b510.clock': ('1', 'derived', '控制器时钟ISO时间（读取时计算）', {'type': 'string'}),
    'b510.reading_k': ('K', 'derived', '控制区显示温度（测量值加校准表偏差，0.1 K；与thermal.indicated_temperature_k同值，控制器在各路径都把它保持在设定值）', {'type': 'number', 'x-unit': 'K'}),
    'b510.passwords': ('1', 'setpoint', '用户密码', {'type': 'object'}, 'debug'),
    'b510.outage': ('1', 'derived', '市电中断（开始时间s与当时温度°C），null为有电', {'anyOf': [{'type': 'object'}, {'type': 'null'}]}, 'debug'),
}
_B510_COMPUTED = ('b510.clock', 'b510.reading_k')

_B510_SLOT_ARG = {'slot': {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': _B510_SLOTS - 1}}
_B510_EXTRAS_ARG = {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'boolean'}}
_B510_SEG_FIELDS = {'target_C': _T(), 'mode': {'type': 'string', 'enum': ['time', 'rate']},
                    'time_min': {'anyOf': [{'type': 'number', 'x-unit': 'min', 'minimum': 0, 'maximum': _B510_TIME_MAX_MIN}, {'type': 'null'}]},
                    'rate_K_h': {'type': 'number', 'x-unit': 'K/h', 'minimum': 1, 'maximum': _B510_RATE_MAX},
                    'hb_K': {'type': 'number', 'x-unit': 'K', 'minimum': 0, 'maximum': _B510_HB_MAX}, 'extras': _B510_EXTRAS_ARG}
_B510_SEG_ARGS = {'index': {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': _B510_SEGMENTS - 1}, **_B510_SEG_FIELDS}
_B510_USER = {'type': 'string', 'enum': ['operator', 'supervisor', 'administrator']}
_B510_PW = {'type': 'string', 'minLength': 6, 'maxLength': 6}
_B510_IDX = lambda n: {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': n - 1}   # noqa: E731
_B510_OK = {'ok': {'type': 'boolean'}}
_B510_DATE = {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'}
_B510_TIME = {'type': 'string', 'pattern': '^[0-9]{2}:[0-9]{2}$'}
_B510_CAPS = {   # name -> (level, kind, properties, optional ('*' = any non-empty subset), output, observes, meaning)
    'access.login': (0, 'device_command', {'user': _B510_USER, 'password': {'type': 'string'}}, (), {'user': {'type': 'string'}}, ['b510.user'],
                     '登录（User administration或状态栏用户名，p.74）；密码错误时拒绝（屏幕显示PASSWORD INCORRECT）'),
    'access.logout': (0, 'device_command', {}, (), {'user': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}}, ['b510.user'],
                      'Log out current user：标准用户生效；操作锁开启时无人登录（p.74、p.76）'),
    'access.set_password': (1, 'configuration', {'user': _B510_USER, 'password': _B510_PW}, (), {'user': {'type': 'string'}}, [],
                            '修改密码（6位数字）：各用户只改自己的；管理员可重置操作员与管理员密码（p.75–76）'),
    'program.edit': (2, 'configuration', _B510_SLOT_ARG, (), {'slot': {'type': 'integer', 'x-unit': '1'}}, ['b510.editing'],
                     '打开程序编辑器：已有程序的工作副本，空位为新程序P0n（p.26、p.36）'),
    'program.edit_set': (2, 'configuration', {'name': {'type': 'string'}, 'start_C': {'anyOf': [_T(), {'type': 'null'}]}, 'holdback': {'type': 'string', 'enum': ['auto', 'manual']},
                                             'end': {'type': 'string', 'enum': ['end', 'repeat']}, 'end_extras': _B510_EXTRAS_ARG}, '*', _B510_OK, ['b510.editing'],
                         '编辑器：名称（≤19个拉丁字符）、起始段（null=auto或温度）、保持模式AUTO/MANUAL、结束段END/REPEAT及其附加功能（p.26–28、p.39–43）'),
    'program.edit_segment': (2, 'configuration', _B510_SEG_ARGS, tuple(_B510_SEG_FIELDS), {'index': {'type': 'integer', 'x-unit': '1'}}, ['b510.editing'],
                             '编辑一段：目标温度、时间（min，0=跳变，null=无限保持）或速率K/h（切换时自动换算）、保持带HB、附加功能（p.26–27、p.41–42）'),
    'program.edit_insert': (2, 'configuration', {'index': {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': _B510_SEGMENTS}}, (), {'count': {'type': 'integer', 'x-unit': '1'}},
                            ['b510.editing'], '"+"：在该位置插入一段（复制相邻段，最多4段，p.27、p.41）'),
    'program.edit_delete': (2, 'configuration', {'mask': {'type': 'array', 'minItems': _B510_SEGMENTS, 'maxItems': _B510_SEGMENTS, 'items': {'type': 'boolean'}}}, (),
                            {'count': {'type': 'integer', 'x-unit': '1'}}, ['b510.editing'], 'Organize Segments：删除所选的段（All Segments=全选，p.43）'),
    'program.edit_move': (2, 'configuration', {'index': _B510_IDX(_B510_SEGMENTS), 'offset': {'type': 'integer', 'x-unit': '1', 'minimum': -1, 'maximum': 1}}, (),
                          {'index': {'type': 'integer', 'x-unit': '1'}}, ['b510.editing'], 'Organize Segments：把所选的段左移或右移一位（p.43）'),
    'program.edit_save': (2, 'configuration', {}, (), {'slot': {'type': 'integer', 'x-unit': '1'}}, ['b510.programs', 'b510.editing'], '保存程序（软盘图标，p.27、p.44）'),
    'program.edit_discard': (2, 'configuration', {}, (), _B510_OK, ['b510.editing'], '关闭编辑器（不保存，p.27、p.44）'),
    'program.copy': (2, 'configuration', _B510_SLOT_ARG, (), {'slot': {'type': 'integer', 'x-unit': '1'}}, ['b510.programs'], 'Copy Program：复制到第一个空位；没有空位不能复制（p.46）'),
    'program.delete': (2, 'configuration', _B510_SLOT_ARG, (), {'slot': {'type': 'integer', 'x-unit': '1'}}, ['b510.programs'], 'Delete Program（p.45）；正在运行的程序不能删除（假设）'),
    'program.set_favourite': (1, 'configuration', {**_B510_SLOT_ARG, 'on': {'type': 'boolean'}}, (), {'on': {'type': 'boolean'}}, ['b510.programs'], '收藏星标（p.32、p.34）'),
    'program.set_category': (2, 'configuration', {**_B510_SLOT_ARG, 'category': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}}, (), {'slot': {'type': 'integer', 'x-unit': '1'}},
                             ['b510.programs'], 'Assign Category（p.36、p.44）'),
    'categories.add': (2, 'configuration', {'name': {'type': 'string', 'minLength': 1, 'maxLength': 19}}, (), {'count': {'type': 'integer', 'x-unit': '1'}}, ['b510.categories'],
                       'New Category（最多6个，p.35）'),
    'categories.rename': (2, 'configuration', {'name': {'type': 'string'}, 'new_name': {'type': 'string', 'minLength': 1, 'maxLength': 19}}, (), {'count': {'type': 'integer', 'x-unit': '1'}},
                          ['b510.categories', 'b510.programs'], 'Edit Category：改名，已分配的程序跟随（p.35）'),
    'categories.delete': (2, 'configuration', {'name': {'type': 'string'}}, (), {'count': {'type': 'integer', 'x-unit': '1'}}, ['b510.categories', 'b510.programs'],
                          'Delete Category：删除类别，其程序变为无类别（p.35）'),
    'program.start': (1, 'device_command', {**_B510_SLOT_ARG, 'delayed_date': _B510_DATE, 'delayed_time': _B510_TIME}, ('delayed_date', 'delayed_time'),
                      {'slot': {'type': 'integer', 'x-unit': '1'}}, ['b510.run', 'program.running', 'thermal.target_k'],
                      '启动程序（p.23、p.33）：可延时启动（控制器时钟的日期YYYY-MM-DD与时间HH:MM，需同时给出，等待时不加热，p.34）；按实际值接续从炉温开始并跳过已越过的升温段（p.60）；需上电、门关、无程序与故障'),
    'program.restart_last': (1, 'device_command', {}, (), {'slot': {'type': 'integer', 'x-unit': '1'}}, ['b510.run'], '主页Repeat：重新启动上次结束的程序（p.10、p.29）'),
    'program.stop': (1, 'device_command', {}, (), {'running': {'type': 'boolean'}}, ['b510.run', 'program.running', 'b510.extras'],
                     '停止（p.16、p.18、p.23）：结束程序、延时启动或自优化；程序已结束时复位结束段附加功能（p.27）'),
    'program.pause': (1, 'device_command', {'on': {'type': 'boolean'}}, (), {'on': {'type': 'boolean'}}, ['b510.run'], '暂停/继续：升温段冻结设定点，保温段冻结时间，附加功能保持（p.10、p.18）'),
    'program.jump': (2, 'device_command', {'segment': {'type': 'integer', 'x-unit': '1', 'minimum': 1, 'maximum': _B510_SEGMENTS}}, (), {'segment': {'type': 'integer', 'x-unit': '1'}},
                     ['b510.run'], 'Segment jump：跳到第n段开始，实际值接续，可一次越过多段（p.48、p.60）'),
    'program.change_active': (2, 'configuration', _B510_SEG_ARGS, tuple(_B510_SEG_FIELDS), {'index': {'type': 'integer', 'x-unit': '1'}}, ['b510.run'],
                              'Change active program：只改运行副本的当前或以后的段；当前升温段从实际值继续，当前保温段的修改要跳段才生效（p.47–48）'),
    'extras.set_manual': (1, 'device_command', {'index': _B510_IDX(_B510_EXTRAS), 'mode': {'type': 'string', 'enum': ['auto', 'off', 'on']}}, (), {'mode': {'type': 'string'}},
                          ['b510.extras'], 'Control extra functions：Automatic / Off / On；手动设定在下一段、程序开始与结束时复位（p.79–80）'),
    'extras.configure': (3, 'configuration', {'index': _B510_IDX(_B510_EXTRAS), 'enabled': {'type': 'boolean'}, 'name': {'type': 'string'}}, ('enabled', 'name'),
                         {'index': {'type': 'integer', 'x-unit': '1'}}, ['b510.extras'], 'Extra functions：显示/隐藏与名称（p.78）'),
    'controller.lock': (2, 'device_command', {}, (), {'locked': {'type': 'boolean'}}, ['b510.locked'], 'Lock controller：锁定运行中的程序，输入被拒绝（p.72、p.77）'),
    'controller.unlock': (0, 'device_command', {'user': _B510_USER, 'password': {'type': 'string'}}, (), {'locked': {'type': 'boolean'}}, ['b510.locked'],
                          'Unlock controller：选择用户并输入其密码（p.77）'),
    'settings.configure': (1, 'configuration', {k: v[1] for k, v in _B510_SETTINGS.items()}, '*', _B510_OK, ['b510.settings'],
                           '设定（各项所需级别见p.72–76）：炉名、语言、亮度、单位°C/°F、日期与时间格式、实际值接续、平滑、启动电路（输出上限）、调节器阻尼（升温段I份额上限）、太阳能模式、断电模式、梯度监视、过程记录、USB/Wi-Fi接口、标准用户、注销时间、操作锁'),
    'settings.wizard': (0, 'configuration', {'language': _B510_SETTINGS['language'][1], 'unit': _B510_SETTINGS['unit'][1]}, (), _B510_OK, ['b510.settings'],
                        '开机向导（首次开机，可在System中重复）：语言与温度单位；Wi-Fi一步在无网络时跳过（p.21–22）'),
    'calibration.set_point': (3, 'configuration', {'index': _B510_IDX(10), 'temp_C': _T(), 'offset_K': {'type': 'number', 'x-unit': 'K', 'minimum': -100, 'maximum': 100}}, (),
                              {'index': {'type': 'integer', 'x-unit': '1'}}, ['b510.calibration', 'b510.reading_k'],
                              '测量范围校准基点（p.49–53）：显示=测量+插值偏差，控制器按校正值控制'),
    'pid.set_point': (3, 'configuration', {'index': _B510_IDX(10), 'from_C': _T(), 'to_C': _T(), 'xp': {'type': 'number', 'x-unit': '1', 'minimum': 0, 'maximum': 1000},
                                           'ti': {'type': 'number', 'x-unit': 's', 'minimum': 0, 'maximum': 10000}, 'td': {'type': 'number', 'x-unit': 's', 'minimum': 0, 'maximum': 10000}}, (),
                      {'index': {'type': 'integer', 'x-unit': '1'}}, ['b510.pid'], '控制参数基点（p.53–56）：按程序设定值所在范围选用；输出% = 100/XP×偏差 + I + D（I、D各限±100 %）；全部XP为0时W01且不加热'),
    'alarms.configure': (3, 'configuration', {'index': _B510_IDX(6), 'source': {'type': 'string', 'enum': ['off', 'band', 'max', 'min', 'program_end']},
                                              'range': {'type': 'string', 'enum': ['hold', 'ramp', 'program', 'always']},
                                              'min_K': {'type': 'number', 'x-unit': 'K', 'minimum': -3000, 'maximum': 3000}, 'max_K': {'type': 'number', 'x-unit': 'K', 'minimum': -3000, 'maximum': 3000},
                                              'delay_s': {'type': 'number', 'x-unit': 's', 'minimum': 0, 'maximum': 3600},
                                              'type': {'type': 'string', 'enum': ['going', 'going_report', 'save_report']},
                                              'reaction': {'type': 'string', 'enum': ['relay', 'acoustic', 'program_stop', 'hold', 'hold_heating_off']}},
                         ('source', 'range', 'min_K', 'max_K', 'delay_s', 'type', 'reaction'), {'index': {'type': 'integer', 'x-unit': '1'}}, ['b510.alarms'],
                         '报警1–6（p.81–83）：BAND相对设定点（0=该侧关闭）、MAX/MIN绝对温度°C、PROGRAM END；范围；延时；类型；反应（继电器与声报只记录、停止程序=04-04、暂停、暂停并关加热）'),
    'messages.acknowledge': (0, 'device_command', {}, (), _B510_OK, ['b510.warnings', 'b510.error'], '确认警告与故障（标(1)的故障只能断电复位，p.118、p.121）'),
    'autotune.start': (3, 'device_command', {'opt_C': {'type': 'number', 'x-unit': 'degC', 'minimum': 100, 'maximum': _B510_MAX_C}}, (), {'state': {'type': 'string'}},
                       ['b510.autotune'], '自优化（p.64–65）：炉冷（<60 °C）时开始；全功率加热到约75 %优化温度、断电回落10 K、再全功率加热；由阶跃响应辨识热容与散热并算出XP/TI/TD（不自动保存）'),
    'autotune.apply': (3, 'configuration', {'index': _B510_IDX(10)}, (), {'index': {'type': 'integer', 'x-unit': '1'}}, ['b510.pid'], '把自优化结果复制到一个控制参数基点（p.65）'),
    'clock.set': (2, 'configuration', {'date': _B510_DATE, 'time': _B510_TIME}, '*', {'clock': {'type': 'string'}}, ['b510.clock'],
                  '日期YYYY-MM-DD与时间HH:MM（只在无程序时，p.88；2000–2099年）'),
    'usb.export': (1, 'device_command', {'what': {'type': 'string', 'enum': ['programs', 'parameters', 'settings', 'complete', 'service']}}, (), _B510_OK, ['b510.warnings'],
                   'USB导出（p.97、p.101）：未插U盘时W16'),
    'usb.import': (3, 'device_command', {}, (), _B510_OK, ['b510.warnings'], 'USB导入（p.97–98）：未插U盘时W16'),
}
_B510_EVALUATOR = {
    'diagnostics.inject_error': ({'code': {'type': 'string', 'enum': [c for c in _B510_ERRORS if c not in _B510_RAISED]}}, {'code': {'type': 'string'}}, ['b510.error', 'b510.run'],
                                 '评测者：注入故障表中的一个故障（p.118–121）：程序停止、加热关闭，显示到确认（标(1)的到断电）'),
    'diagnostics.set_mains': ({'present': {'type': 'boolean'}}, {'present': {'type': 'boolean'}}, ['b510.outage', 'b510.run'],
                              '评测者：市电中断/恢复；恢复时按断电模式CANCEL/DELTA T/TIME/CONTINUE决定程序继续（W05）或取消（04-03），<5 s总是继续（p.87）'),
}


def _b510_capability(name, level, kind, props, optional, output, observes, meaning, visibility='agent'):
    req = [] if optional == '*' else [k for k in props if k not in optional]
    schema = {'type': 'object', 'properties': props, 'required': req, 'additionalProperties': False}
    if optional == '*':
        schema['minProperties'] = 1
    lvl = {0: '任何用户', 1: 'OPERATOR', 2: 'SUPERVISOR', 3: 'ADMINISTRATOR'}[level]
    pre = ['本机已上电且有市电'] + ([f'所需用户级别：{lvl}（p.72–73）', '控制器锁定时只接受解锁（p.77）'] if visibility == 'agent' else [])
    return {'availability': 'implemented', 'visibility': visibility, 'kind': kind, 'component_ids': ['controller', 'display'], 'description_zh': meaning,
            'input_schema': schema, 'output_schema': _object_schema(output), 'example_arguments': [], 'observes': list(observes),
            'state_effects': [{'state_id': sid, 'timing': 'immediate', 'description_zh': meaning} for sid in observes],
            'preconditions': [], 'preconditions_zh': pre,
            'binding': f"basic40_runtime_thermal.NaberthermL9Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
            'scene_backend_required': False, 'completion_criterion_zh': '同步返回即控制器已接受；程序与温度随后由宿主step推进'}


_B510_EXAMPLES = {'access.login': {'user': 'supervisor', 'password': '000021'}, 'access.logout': {}, 'access.set_password': {'user': 'operator', 'password': '123456'},
                  'program.edit': {'slot': 0}, 'program.edit_set': {'name': 'BISQUE'}, 'program.edit_segment': {'index': 0, 'target_C': 600.0, 'mode': 'time', 'time_min': 120.0},
                  'program.edit_insert': {'index': 1}, 'program.edit_delete': {'mask': [False, True, False, False]}, 'program.edit_move': {'index': 1, 'offset': -1},
                  'program.edit_save': {}, 'program.edit_discard': {}, 'program.copy': {'slot': 0}, 'program.delete': {'slot': 1},
                  'program.set_favourite': {'slot': 0, 'on': True}, 'program.set_category': {'slot': 0, 'category': 'Firing'},
                  'categories.add': {'name': 'Drying'}, 'categories.rename': {'name': 'Firing', 'new_name': 'Glaze firing'}, 'categories.delete': {'name': 'Annealing'},
                  'program.start': {'slot': 0}, 'program.restart_last': {}, 'program.stop': {},
                  'program.pause': {'on': True}, 'program.jump': {'segment': 2}, 'program.change_active': {'index': 1, 'target_C': 650.0},
                  'extras.set_manual': {'index': 0, 'mode': 'on'}, 'extras.configure': {'index': 0, 'name': 'Exhaust air flap'}, 'controller.lock': {},
                  'controller.unlock': {'user': 'supervisor', 'password': '000021'}, 'settings.configure': {'brightness_percent': 80},
                  'settings.wizard': {'language': 'en', 'unit': 'C'}, 'calibration.set_point': {'index': 0, 'temp_C': 400.0, 'offset_K': 2.0},
                  'pid.set_point': {'index': 0, 'from_C': 0.0, 'to_C': 250.0, 'xp': 7.0, 'ti': 450.0, 'td': 70.0},
                  'alarms.configure': {'index': 0, 'source': 'max', 'max_K': 1050.0}, 'messages.acknowledge': {}, 'autotune.start': {'opt_C': 800.0}, 'autotune.apply': {'index': 2},
                  'clock.set': {'date': '2026-05-25', 'time': '16:20'}, 'usb.export': {'what': 'programs'}, 'usb.import': {},
                  'diagnostics.inject_error': {'code': '02-01'}, 'diagnostics.set_mains': {'present': False}}


def _b510_panel():
    return _g02_panel('nabertherm_l9', NABERTHERM_ASSET_DIR, set(_B510_CAPS) | {'power.set', 'door.open', 'door.close'})


def _b510_extend_metadata(meta, panel):
    spec = meta['asset_types'][NABERTHERM_TYPE]
    controller = {}
    for name, (level, kind, props, optional, output, observes, meaning) in _B510_CAPS.items():
        controller[name] = _b510_capability(name, level, kind, props, optional, output, observes, meaning)
        controller[name]['example_arguments'] = [_B510_EXAMPLES[name]]
    evaluator = {}
    for name, (props, output, observes, meaning) in _B510_EVALUATOR.items():
        evaluator[name] = _b510_capability(name, 0, 'simulation', props, (), output, observes, meaning, visibility='evaluator')
        evaluator[name]['example_arguments'] = [_B510_EXAMPLES[name]]
    for sid, (unit, origin, meaning, schema, *vis) in _B510_NEW_SLOTS.items():
        spec['state_slots'][sid] = {**_nabertherm_slot(unit, origin, schema, vis[0] if vis else 'agent'), 'meaning_zh': meaning,
                                    'read_binding': f"NaberthermL9Session.read(instance_id=..., state_ids=['{sid}'])"}
    spec['declared_capabilities'].pop('thermal.program_library', None)          # the B510 program library is now implemented (program.*)
    spec['parameters']['program_maximum_segments']['source'] = 'B510每程序4段、5个程序（M03.0022 p.18–19）；旧单程序API仍为单个温程。'
    panel_caps = {}
    if panel:
        p = _b510_panel()
        called = p['engine'].spec_capabilities(p['spec'])
        caps = {**spec['capabilities'], **controller}
        eff = list({e['state_id']: dict(e, description_zh='面板动作调用的能力的效果：' + e['description_zh']) for c in called for e in caps[c]['state_effects']
                    if e['timing'] == 'immediate'}.values())
        panel_caps = _g02_panel_capabilities(p, "basic40_runtime_thermal.NaberthermL9Session.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)", eff)
    spec.update(controller_capabilities=controller, evaluator_capabilities=evaluator, panel_capabilities=panel_caps, asset_version='1.3.0')   # 1.2.1: sync fixes 2026-10-19; 1.3.0: B510 front 3D parts (2026-10-20)
    return meta


class _B510Controller:
    """The B510 controller of each furnace instance (mixed into NaberthermL9Runtime): state in inst['state'] under 'b510.*'."""

    @staticmethod
    def _b510_defaults():
        return {'b510.programs': [None] * _B510_SLOTS, 'b510.categories': ['Firing', 'Sintering', 'Tempering', 'Annealing'], 'b510.editing': None, 'b510.run': None,
                'b510.last_run': None, 'b510.extras': {'enabled': [True, True], 'names': list(_B510_EXAMPLE_NAMES), 'manual': ['auto', 'auto'], 'outputs': [False, False],
                                                      'hold_end': [False, False]},
                'b510.settings': {k: v[2] for k, v in _B510_SETTINGS.items()}, 'b510.calibration': [[0.0, 0.0] for _ in range(10)],
                'b510.pid': copy.deepcopy(_B510_PID_DEFAULT), 'b510.pid_out': {'i': 0.0, 'e_prev': None, 'df': 0.0, 'p': 0.0, 'd': 0.0, 'out': 0.0, 'row': None, 'u': 0.0, 't_next': None},
                'b510.alarms': [dict(_B510_ALARM_DEFAULT) for _ in range(6)],
                'b510.alarm_status': [{'active_s': 0.0, 'latched': False} for _ in range(6)], 'b510.user': 'operator', 'b510.locked': False,
                'b510.warnings': [], 'b510.error': None, 'b510.error_log': [], 'b510.autotune': {'state': 'off', 'opt_C': None, 'phase': None, 'times': [], 'result': None},
                'b510.statistics': {'max_C': 25.0, 'kwh_total': 0.0, 'kwh_last': 0.0, 'hours': 0.0, 'starts': 0, 'starts_200': 0, 'starts_1200': 0, 'max_last_C': None},
                'b510.graph': [], 'b510.records': [], 'b510.recording': False, 'b510.output_percent': 0.0, 'b510.clock_offset_s': 0.0,
                'b510.passwords': dict(_B510_PASSWORDS), 'b510.outage': None}

    # ---- helpers
    def _b510_clock(self, s, t=None):
        return _B510_EPOCH + _b510_timedelta(seconds=(self.time_s if t is None else t) + s['b510.clock_offset_s'])

    @staticmethod
    def _b510_reading(s):
        """Control-zone temperature (degC) as the controller shows it: the measurement plus the interpolated calibration offset (p.49)."""
        t = s['thermal.temperature_k'] - 273.15
        return t + _b510_offset(s['b510.calibration'], t)

    @staticmethod
    def _b510_reading_k(s):
        """The corrected reading in K (unrounded): measured + calibration offset; exactly the measured value without a calibration."""
        t = s['thermal.temperature_k']
        return t + _b510_offset(s['b510.calibration'], t - 273.15)

    @staticmethod
    def _b510_control_k(s, sp_k):
        """The measured temperature (K) at which the corrected reading equals the set point sp_k: the controller holds its own reading
        (measured + calibration offset, p.49) at the set point on every path (B510 program, legacy thermal.*, run-free set point)."""
        x = sp_k
        for _ in range(60):
            nxt = sp_k - _b510_offset(s['b510.calibration'], x - 273.15)
            if abs(nxt - x) < 1e-10:
                return nxt
            x = nxt
        return x

    def _b510_warn(self, s, code):
        if not any(w['code'] == code for w in s['b510.warnings']):
            s['b510.warnings'] = s['b510.warnings'] + [{'code': code, 'text': _B510_WARNINGS[code]}]

    def _b510_fault(self, s, code, t):
        """An error (p.118): the program stops and the heating is off until acknowledged; archived in Last errors."""
        s['b510.error'] = {'code': code, 'text': _B510_ERRORS[code]}
        s['b510.error_log'] = (s['b510.error_log'] + [{'code': code, 'text': _B510_ERRORS[code], 'time': self._b510_clock(s, t).strftime('%Y-%m-%d %H:%M')}])[-50:]
        if s['b510.run'] is not None:
            self._b510_end(s, t, aborted=True)
        if s['b510.autotune']['state'] == 'running':
            s['b510.autotune'] = dict(s['b510.autotune'], state='off', phase=None)
        s['thermal.target_k'] = self.ambient_temperature_K

    @staticmethod
    def _b510_extras_out(s, seg_extras):
        ex = s['b510.extras']
        ex['outputs'] = [bool(ex['enabled'][i] and (ex['manual'][i] == 'on' or (ex['manual'][i] == 'auto' and seg_extras[i]))) for i in range(_B510_EXTRAS)]

    @staticmethod
    def _b510_is_hold(run):
        a, b, _ = run['profile']
        return abs(a - b) < 1e-9

    def _b510_skip(self, prog, idx, actual, start):
        """With the actual value applied, rising ramps whose target the furnace has already passed are skipped (p.60, p.48; INFERRED)."""
        noms = _b510_nominal(prog, start)
        while idx < len(prog['segments']) - 1 and noms[idx] + 1e-9 < prog['segments'][idx]['target_C'] <= actual:
            idx += 1
        return idx

    def _b510_begin(self, s, index, from_C):
        """Enter segment index from from_C: manual extra-function settings end (p.80); the segment's extras apply."""
        run = s['b510.run']
        prog = run['program']
        seg = prog['segments'][index]
        nom = _b510_nominal(prog, run['start_C'])[index]
        if abs(seg['target_C'] - nom) < 1e-9:
            from_C = seg['target_C']                                              # a dwell holds its own temperature
        run.update(segment=index, seg_elapsed_s=0.0, waiting=False, profile=[from_C, seg['target_C'], _b510_duration(seg, nom, from_C)], smooth_from=run['setpoint_C'])
        ex = s['b510.extras']
        ex['manual'] = ['auto', 'auto']
        self._b510_extras_out(s, seg['extras'])
        s['program.segment_index'], s['program.hold_elapsed_s'] = index, 0.0

    def _b510_start(self, s, slot, prog, t, delayed=None):
        """Start a program copy: delayed (waiting, no heating) or at once from the start temperature / actual value (p.33-34, p.40, p.60)."""
        actual = self._b510_reading(s)
        run = {'slot': slot, 'name': prog['name'], 'program': copy.deepcopy(prog), 'original': copy.deepcopy(prog), 'segment': 0, 'seg_elapsed_s': 0.0,
               'elapsed_s': 0.0, 'paused': False, 'waiting': False, 'phase': 'delayed' if delayed else 'running', 'delayed_until': delayed, 'cycles': 0,
               'start_C': actual, 'setpoint_C': actual, 'smooth_from': actual, 'profile': [actual, actual, 0.0], 'next_graph_s': 0.0, 'next_record_s': 0.0,
               'start_clock': self._b510_clock(s, t).strftime('%Y-%m-%d %H:%M'), 'max_sp_C': max(sg['target_C'] for sg in prog['segments']),
               'over_s': 0.0, 'noheat_s': 0.0, 'noheat_ref_C': actual, 'grad': {'last_C': actual, 'elapsed': 0.0, 'count': 0}, 'grad_hold': False,
               'alarm_hold': False, 'heating_off': False, 'solar_heat': False, 'max_C': actual}
        s['b510.run'] = run
        s['program.running'], s['program.phase'] = True, 'idle' if delayed else 'ramp'
        s['b510.extras']['manual'] = ['auto', 'auto']                              # reset at program start (p.80)
        s['b510.extras']['hold_end'] = [False, False]
        s['b510.recording'] = False
        if not delayed:
            self._b510_go(s, t)

    def _b510_go(self, s, t):
        run = s['b510.run']
        prog = run['program']
        set_ = s['b510.settings']
        actual = self._b510_reading(s)
        apply = set_['apply_actual'] and actual >= set_['apply_actual_min_C']
        if prog['start_C'] is None:
            start = actual                                                        # 'auto': always the actual value (p.40)
        else:
            start = max(prog['start_C'], actual) if apply else prog['start_C']    # whichever is higher (p.60)
        run.update(phase='running', start_C=start, setpoint_C=start, smooth_from=start, start_clock=self._b510_clock(s, t).strftime('%Y-%m-%d %H:%M'),
                   noheat_ref_C=actual, max_C=actual)
        run['grad'] = {'last_C': actual, 'elapsed': 0.0, 'count': 0}
        nominal_start = prog['start_C'] if prog['start_C'] is not None else actual
        idx = self._b510_skip(prog, 0, actual, nominal_start) if apply else 0
        run['start_C'] = nominal_start                                            # timed ramps keep their slope from the program start
        self._b510_begin(s, idx, start if idx == 0 else actual)
        st = s['b510.statistics']
        st.update(starts=st['starts'] + 1, starts_200=st['starts_200'] + int(run['max_sp_C'] > 200), starts_1200=st['starts_1200'] + int(run['max_sp_C'] > 1200), kwh_last=0.0)
        s['b510.graph'] = []
        s['b510.last_run'] = {'slot': run['slot'], 'name': run['name'], 'start': run['start_clock']}
        s['program.phase'] = 'hold' if self._b510_is_hold(run) else 'ramp'

    def _b510_end(self, s, t, aborted=False):
        run = s['b510.run']
        prog = run['program']
        s['b510.statistics']['max_last_C'] = round(run['max_C'], 1)
        s['b510.run'] = None
        s['program.running'], s['program.phase'] = False, 'stopped' if aborted else 'completed'
        s['thermal.target_k'] = self.ambient_temperature_K
        ex = s['b510.extras']
        ex['hold_end'] = [False, False] if aborted else [bool(x) for x in prog['end_extras']]   # end-segment extras stay on until stop (p.27)
        ex['manual'] = ['auto', 'auto']
        self._b510_extras_out(s, ex['hold_end'])
        s['b510.locked'] = False
        set_ = s['b510.settings']
        s['b510.recording'] = bool(not aborted and set_['documentation_on'] and set_['documentation_end'] == 'below_limit'
                                   and self._b510_reading(s) >= set_['documentation_limit_C'] and s['b510.records'])
        if s['b510.records']:
            s['b510.records'] = s['b510.records'][:-1] + [dict(s['b510.records'][-1], closed=not s['b510.recording'])]
        if not aborted:
            status = [dict(x) for x in s['b510.alarm_status']]
            for i, al in enumerate(s['b510.alarms']):                             # PROGRAM END alarms (p.81)
                if al['source'] == 'program_end':
                    status[i] = {'active_s': 0.0, 'latched': al['type'] == 'save_report'}
                    if al['type'] != 'going':
                        self._b510_warn(s, 'W24' if i == 0 else 'W25' if i == 1 else f'W{18 + i}')
            s['b510.alarm_status'] = status

    def _b510_setpoint(self, s, run):
        a, b, d = run['profile']
        t = run['seg_elapsed_s']
        p = b if d is None or d <= 0 else a + (b - a) * min(1.0, t / d)
        tau = s['b510.settings']['smoothing_s']
        if tau > 0 and abs(run['smooth_from'] - (b if d is None or d <= 0 else a)) > 1e-9:   # smoothing of a setpoint jump at the start of a segment (p.56-57)
            p += (run['smooth_from'] - (b if d is None or d <= 0 else a)) * math.exp(-t / tau)
        return p

    def _b510_heat_cmd(self, inst):
        """(target K, output limit factor, fixed output or None, integrator cap % or None) for the next step: the PID drives the heating
        except during self-optimization and solar mode (fixed full / zero output)."""
        s = inst['state']
        run, tune, set_ = s['b510.run'], s['b510.autotune'], s['b510.settings']
        if s['b510.error'] is not None or s['b510.outage'] is not None:
            return self.ambient_temperature_K, 0.0, 0.0, None
        if tune['state'] == 'running':
            return s['thermal.target_k'], 1.0, (1.0 if tune['phase'] in ('heat1', 'heat2') else 0.0), None
        factor = 1.0
        if set_['startup_on'] and set_['solar_mode'] == 'off' and self._b510_reading(s) < set_['startup_limit_C']:
            factor = set_['startup_max_power_percent'] / 100.0                     # start-up circuit (p.63; off in solar mode, p.71)
        if run is None:
            return s['thermal.target_k'], factor, None, None
        if run['phase'] != 'running' or run['heating_off']:
            return self.ambient_temperature_K, 0.0, 0.0, None
        sp = run['setpoint_C']
        hold = self._b510_is_hold(run)
        rising = run['profile'][1] > run['profile'][0] + 1e-9
        i_cap = set_['damping_max_integrator_percent'] if set_['damping_on'] and rising and sp < set_['damping_limit_C'] else None   # damping (p.69-70)
        if set_['solar_mode'] == 'ramps_holds' or (set_['solar_mode'] == 'ramps' and not hold):   # two-point control around the setpoint (p.70-71)
            on_k, off_k = (set_['solar_on_hold_K'], set_['solar_off_hold_K']) if hold else (set_['solar_on_ramp_K'], set_['solar_off_ramp_K'])
            r = self._b510_reading(s)
            if r <= sp + on_k:
                run['solar_heat'] = True
            elif r >= sp + off_k:
                run['solar_heat'] = False
            return s['thermal.target_k'], 1.0, (1.0 if run['solar_heat'] else 0.0), None
        target = sp + 273.15                                                      # the set point; _thermal_step holds the corrected reading there (p.49)
        return max(self.ambient_temperature_K, min(target, NABERTHERM_MAX_K + 100.0)), factor, None, i_cap

    # ---- the time step
    def _b510_active(self, s):
        return ((s['b510.settings']['startup_on'] and s['heater.enabled']) or s['b510.run'] is not None or s['b510.autotune']['state'] == 'running' or s['b510.recording'] or s['b510.outage'] is not None
                or s['b510.error'] is not None or any(al['source'] in ('band', 'max', 'min') and al['range'] == 'always' for al in s['b510.alarms']))

    def _b510_advance(self, inst, dt):
        s = inst['state']
        t, remaining, energy = self.time_s, dt, 0.0
        while remaining > 1e-12:
            h = min(_B510_SUBSTEP_S, remaining)
            self._b510_program_step(s, h, t)
            target, factor, force, i_cap = self._b510_heat_cmd(inst)
            if s['b510.run'] is not None or s['b510.error'] is not None or s['b510.outage'] is not None:
                s['thermal.target_k'] = target
            energy += self._thermal_step(s, h, factor, force=force, i_cap=i_cap)
            t += h
            remaining -= h
            self._b510_monitor(s, h, t)
        self._b510_account(s, dt, energy)

    def _b510_account(self, s, dt, energy):
        st = dict(s['b510.statistics'])
        if s['power.enabled'] and s['b510.outage'] is None:
            st['hours'] = st['hours'] + dt / 3600.0
        kwh = energy / 3.6e6
        st['kwh_total'], st['kwh_last'] = st['kwh_total'] + kwh, st['kwh_last'] + kwh
        st['max_C'] = max(st['max_C'], round(self._b510_reading(s), 1))
        s['b510.statistics'] = st
        s['b510.output_percent'] = round(min(100.0, max(0.0, 100.0 * energy / (NABERTHERM_POWER_W * dt))), 3) if dt > 0 else 0.0

    def _b510_program_step(self, s, h, t):
        """Program time and setpoint over one step (before the heating acts)."""
        run = s['b510.run']
        if s['b510.outage'] is not None:
            return                                                                # the controller is off; the program waits for the power (p.87)
        if s['b510.autotune']['state'] == 'running':
            self._b510_autotune(s, h, t)
        if run is None:
            return
        run['elapsed_s'] += h
        if run['phase'] == 'delayed':
            if self._b510_clock(s, t + h).strftime('%Y-%m-%d %H:%M') >= run['delayed_until']:
                self._b510_go(s, t + h)
            return
        if run['paused'] or run['grad_hold'] or run['alarm_hold']:
            return                                                                # setpoint frozen in ramps, time frozen in dwells (p.18, p.85, p.83)
        prog = run['program']
        a, b, d = run['profile']
        hold = abs(a - b) < 1e-9
        reading = self._b510_reading(s)
        seg = prog['segments'][run['segment']]
        if hold and prog['holdback'] == 'manual' and seg['hb_K'] > 0 and abs(reading - b) > seg['hb_K']:
            run['waiting'] = True                                                 # MANUAL: time stops outside the band (p.41, p.47)
            return
        if run['waiting'] and not hold:                                           # AUTO: at the end of the ramp wait for the dwell temperature (p.46)
            if abs(reading - b) > _B510_AUTO_HB_K:
                return
            run['waiting'] = False
            self._b510_next(s, t)
            return
        run['waiting'] = False
        run['seg_elapsed_s'] += h
        run['setpoint_C'] = self._b510_setpoint(s, run)
        if hold:
            s['program.hold_elapsed_s'] = run['seg_elapsed_s']
        if d is not None and run['seg_elapsed_s'] >= d - 1e-9:
            nxt = run['segment'] + 1
            if (not hold and prog['holdback'] == 'auto' and nxt < len(prog['segments']) and abs(prog['segments'][nxt]['target_C'] - b) < 1e-9
                    and abs(reading - b) > _B510_AUTO_HB_K):
                run['waiting'] = True
                return
            self._b510_next(s, t)

    def _b510_next(self, s, t):
        run = s['b510.run']
        prog = run['program']
        b = run['profile'][1]
        nxt = run['segment'] + 1
        if nxt < len(prog['segments']):
            self._b510_begin(s, nxt, b)
        elif prog['end'] == 'repeat':                                             # REPEAT: the whole program again until stopped (p.43)
            run['cycles'] += 1
            run['start_C'] = prog['start_C'] if prog['start_C'] is not None else b
            self._b510_begin(s, 0, b)
        else:
            self._b510_end(s, t)
            return
        run['setpoint_C'] = self._b510_setpoint(s, run)
        s['program.phase'] = 'hold' if self._b510_is_hold(run) else 'ramp'

    def _b510_monitor(self, s, h, t):
        """After the heating step: excess temperature 04-02, no heating power 04-01, gradient W00, alarms, graph, records."""
        if s['b510.outage'] is not None:
            return
        run = s['b510.run']
        reading = self._b510_reading(s)
        if run is not None and run['phase'] == 'running':
            run['max_C'] = max(run['max_C'], reading)
            run['over_s'] = run['over_s'] + h if reading > run['max_sp_C'] + _B510_OVER_K and reading > _B510_OVER_FROM_C else 0.0
            if run['over_s'] >= _B510_OVER_DELAY_S - 1e-9:
                self._b510_fault(s, '04-02', t)
                return
            rising = run['profile'][1] > run['profile'][0] + 1e-9
            if rising and not run['paused'] and run['setpoint_C'] > reading + 1.0 and reading <= run['noheat_ref_C'] + 0.1:
                run['noheat_s'] += h
            else:
                run['noheat_s'], run['noheat_ref_C'] = 0.0, reading
            if run['noheat_s'] >= _B510_NOHEAT_S - 1e-9:
                self._b510_fault(s, '04-01', t)
                return
            self._b510_gradient(s, run, h, reading)
        self._b510_alarms(s, h, t, reading)
        run = s['b510.run']
        if run is not None and run['phase'] == 'running':
            if run['elapsed_s'] >= run['next_graph_s'] - 1e-9:
                s['b510.graph'] = (s['b510.graph'] + [[round(run['elapsed_s'], 3), round(reading, 1), round(run['setpoint_C'], 1)]])[-_B510_GRAPH_MAX:]
                run['next_graph_s'] = run['elapsed_s'] + _B510_GRAPH_S
            if s['b510.settings']['documentation_on'] and run['elapsed_s'] >= run['next_record_s'] - 1e-9:
                self._b510_record(s, run['name'], run['setpoint_C'], reading, t)
                run['next_record_s'] = run['elapsed_s'] + s['b510.settings']['documentation_interval_s']
        elif s['b510.recording']:                                                 # cooling recorded down to the limit (p.105)
            set_ = s['b510.settings']
            last = s['b510.records'][-1]
            if reading < set_['documentation_limit_C'] or not set_['documentation_on']:
                s['b510.recording'] = False
                s['b510.records'] = s['b510.records'][:-1] + [dict(last, closed=True)]
            elif t - last['t_s'] >= set_['documentation_interval_s'] - 1e-9:
                self._b510_record(s, last['program'], None, reading, t)

    def _b510_record(self, s, name, sp, reading, t):
        """NTLog: one record into the current internal file; 5,610 records per file, at most 16 files without a stick, then recording stops (p.103-106)."""
        files = list(s['b510.records'])
        if not files or files[-1]['records'] >= _B510_RECORDS_PER_FILE or files[-1]['program'] != name or files[-1].get('closed'):
            if len(files) >= _B510_FILES:
                return
            stamp = self._b510_clock(s, t).strftime('%Y%m%d')
            files.append({'file': f'{stamp}_{_B510_SERIAL}_{len(files) + 1:04d}.csv', 'program': name, 'records': 0, 'last': None, 't_s': None, 'closed': False})
        files[-1] = dict(files[-1], records=files[-1]['records'] + 1, last=[None if sp is None else round(sp, 1), round(reading, 1)], t_s=t)
        s['b510.records'] = files

    def _b510_gradient(self, s, run, h, reading):
        """Gradient monitoring (p.84-85): too fast heating for n intervals interrupts the program until an interval without exceedance."""
        set_, g = s['b510.settings'], run['grad']
        if not set_['gradient_on'] or reading < set_['gradient_min_C']:
            g.update(last_C=reading, elapsed=0.0, count=0)
            run['grad_hold'] = False
            return
        g['elapsed'] += h
        if g['elapsed'] >= set_['gradient_interval_s'] - 1e-9:
            rate = (reading - g['last_C']) / g['elapsed'] * 3600.0
            g['count'] = g['count'] + 1 if rate > set_['gradient_max_K_h'] else 0
            run['grad_hold'] = g['count'] >= set_['gradient_delay_n']
            if run['grad_hold']:
                self._b510_warn(s, 'W00')
            g.update(last_C=reading, elapsed=0.0)

    def _b510_alarms(self, s, h, t, reading):
        """Alarms 1-6 (p.81-83) on the control zone."""
        run = s['b510.run']
        running = run is not None and run['phase'] == 'running'
        hold_any = heat_off = False
        status = [dict(x) for x in s['b510.alarm_status']]
        for i, al in enumerate(s['b510.alarms']):
            st = status[i]
            if al['source'] not in ('band', 'max', 'min'):
                if al['source'] == 'off':
                    status[i] = {'active_s': 0.0, 'latched': False}
                continue
            seg_hold = running and self._b510_is_hold(run)
            in_range = al['range'] == 'always' or (running and (al['range'] == 'program' or (al['range'] == 'hold') == seg_hold))
            if al['source'] == 'band':
                sp = run['setpoint_C'] if running else None
                cond = in_range and sp is not None and ((al['max_K'] > 0 and reading > sp + al['max_K']) or (al['min_K'] != 0 and reading < sp - abs(al['min_K'])))
            elif al['source'] == 'max':
                cond = in_range and reading > al['max_K']
            else:
                cond = in_range and reading < al['min_K']
            st['active_s'] = st['active_s'] + h if cond else 0.0
            if cond and st['active_s'] >= al['delay_s'] - 1e-9 and not st['latched']:
                st['latched'] = True
                if al['type'] != 'going':
                    self._b510_warn(s, _B510_ALARM_CODE.get((i, al['source'])) or f'W{18 + i}')
                if al['reaction'] == 'program_stop' and s['b510.run'] is not None:
                    s['b510.alarm_status'] = status
                    self._b510_fault(s, '04-04', t)
                    return
            if not cond and st['latched'] and al['type'] != 'save_report':
                st['latched'] = False
            if st['latched'] and al['reaction'] in ('hold', 'hold_heating_off'):
                hold_any, heat_off = True, heat_off or al['reaction'] == 'hold_heating_off'
        s['b510.alarm_status'] = status
        if s['b510.run'] is not None:
            s['b510.run'].update(alarm_hold=hold_any, heating_off=heat_off)

    def _b510_autotune(self, s, h, t):
        """Self-optimization (p.64-65): full power from cold to ~75 % of the optimization temperature, heating off until 10 K lower, full power
        again.  The step responses identify the plant: the zero-power fall gives the time constant tau = t_off / ln((T1 - Ta) / (T2 - Ta)) (Ta: the
        cold-junction temperature), the full-power rise from the start reading T0 gives the end value Te = (T1 - T0 e^(-t1/tau)) / (1 - e^(-t1/tau)), hence UA = Pmax / (Te - Ta)
        and C = tau UA; the gains are then placed for the 1 s cycle (_b510_deadbeat).  Implausible values: 04-05."""
        tune = dict(s['b510.autotune'])
        level = 0.75 * tune['opt_C']
        r = self._b510_reading(s)
        tune.setdefault('t0_C', r)                                                # start reading; the ambient comes from the cold junction
        tune['phase_s'] = tune.get('phase_s', 0.0) + h
        nxt = None
        if tune['phase'] == 'heat1' and r >= level:
            nxt = 'coast'
        elif tune['phase'] == 'coast' and r <= level - 10.0:
            nxt = 'heat2'
        elif tune['phase'] == 'heat2' and r >= level:
            nxt = 'done'
        if tune['phase_s'] > 6 * 3600.0:
            nxt = 'failed'
        if nxt:
            tune['times'] = tune['times'] + [tune['phase_s']]
            tune['temps'] = tune.get('temps', []) + [r]
            tune['phase_s'] = 0.0
            if nxt in ('done', 'failed'):
                res = None
                if nxt == 'done':
                    try:
                        r0, ta, (t1, t_off), (r1, r2) = tune['t0_C'], self.ambient_temperature_K - 273.15, tune['times'][:2], tune['temps'][:2]
                        tau = t_off / math.log((r1 - ta) / (r2 - ta))
                        k = math.exp(-t1 / tau)
                        te = (r1 - r0 * k) / (1.0 - k)
                        ua = NABERTHERM_POWER_W / (te - ta)
                        if tau > 0 and ua > 0 and te > r1:
                            xp, ti, td = _b510_deadbeat(tau * ua, ua, NABERTHERM_POWER_W)
                            res = {'xp': xp, 'ti': ti, 'td': td}
                    except (ValueError, ZeroDivisionError):
                        res = None
                tune.update(state='done' if res else 'failed', phase=None, result=res)
                s['b510.autotune'] = tune
                if not res:
                    self._b510_fault(s, '04-05', t)
                return
            tune['phase'] = nxt
        s['b510.autotune'] = tune

    def _b510_observed(self, inst, sid):
        s = inst['state']
        if sid == 'b510.clock':
            return self._b510_clock(s).strftime('%Y-%m-%dT%H:%M:%S')
        if sid == 'b510.reading_k':
            return round(s['thermal.indicated_temperature_k'], 6)                  # the shown value already carries the calibration (p.49)
        return copy.deepcopy(s[sid])

    # ---- power and mains (called by the session on a candidate state)
    def _b510_power(self, s, enabled, t):
        if not enabled:
            if s['b510.run'] is not None:                                         # main switch off during a program: 04-03 at the next power-on (p.14, p.120)
                self._b510_end(s, t, aborted=True)
                s['b510.error'] = {'code': '04-03', 'text': _B510_ERRORS['04-03']}
                s['b510.error_log'] = (s['b510.error_log'] + [{'code': '04-03', 'text': _B510_ERRORS['04-03'], 'time': self._b510_clock(s, t).strftime('%Y-%m-%d %H:%M')}])[-50:]
            elif s['b510.error'] is not None and s['b510.error']['code'] in _B510_POWER_OFF_ONLY:
                s['b510.error'] = None                                            # (1) errors are cleared by switching off (p.121)
            if s['b510.autotune']['state'] == 'running':
                s['b510.autotune'] = dict(s['b510.autotune'], state='off', phase=None)
            s['b510.extras']['hold_end'] = [False, False]
            s['b510.extras']['manual'] = ['auto', 'auto']
            self._b510_extras_out(s, [False, False])
            s['b510.editing'], s['b510.locked'] = None, False
        else:
            set_ = s['b510.settings']
            s['b510.user'] = None if set_['operation_lock'] else set_['standard_user']    # standard user at power-on; operation lock asks first (p.74, p.76)

    def _b510_mains(self, s, present, t):
        """Mains loss / return: the program waits; on return the power-outage mode decides (p.87)."""
        if not present:
            if s['b510.outage'] is None:
                s['b510.outage'] = {'since_s': t, 'temp_C': round(self._b510_reading(s), 3)}
                s['heater.enabled'] = False
            return
        out = s['b510.outage']
        if out is None:
            return
        s['b510.outage'] = None
        s['heater.enabled'] = s['power.enabled'] and s['door.closed']
        if not s['power.enabled']:
            return
        run = s['b510.run']
        set_ = s['b510.settings']
        dur, now = t - out['since_s'], self._b510_reading(s)
        if dur >= _B510_BRIEF_OUTAGE_S:
            s['b510.user'] = None if set_['operation_lock'] else set_['standard_user']
            if s['b510.autotune']['state'] == 'running':
                s['b510.autotune'] = dict(s['b510.autotune'], state='off', phase=None)
        if run is None:
            return
        mode = set_['power_outage_mode']
        cont = (dur < _B510_BRIEF_OUTAGE_S or mode == 'continue' or (mode == 'time' and dur <= _B510_OUTAGE_TIME_S)
                or (mode == 'delta_t' and out['temp_C'] - now < _B510_OUTAGE_DT_K and now >= _B510_OUTAGE_TMIN_C))
        if not cont:
            self._b510_fault(s, '04-03', t)
            return
        if dur >= _B510_BRIEF_OUTAGE_S:
            self._b510_warn(s, 'W05')
            s['b510.graph'] = []                                                  # graph history is lost (p.30-31)
            run['program'] = copy.deepcopy(run['original'])                       # run-time changes are lost (p.47)
            run['waiting'] = False                                                # the holdback band state is reset (p.46)
            if s['b510.records']:
                s['b510.records'] = s['b510.records'][:-1] + [dict(s['b510.records'][-1], closed=True)]   # a new file starts (p.106)

    # ---- the panel host
    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame), created on first use."""
        if instance_id not in self._b510_panels:
            if instance_id not in self._instances:
                raise ValueError('Unknown instance')
            p = _b510_panel()
            host = _G02PanelHost(p['engine'], lambda: self._b510_panel_read(instance_id),
                                 lambda cap, args: (self._panel_caller or self._panel_session)._checked(instance_id, self._instances[instance_id], cap, args, None),
                                 lambda: self.time_s)
            self._b510_panels[instance_id] = p['engine'].PanelUI(p['spec'], host)
        return self._b510_panels[instance_id]

    @staticmethod
    def _b510_hm(sec):
        sec = int(round(max(0.0, sec)))
        return f'{sec // 3600:02d}:{sec % 3600 // 60:02d}'

    @staticmethod
    def _b510_time_text(seg, f):
        if seg['mode'] == 'rate':
            return f"{round(seg['rate_K_h'] * (1.8 if f else 1.0), 1):g} {'°F' if f else '°C'}/h"
        if seg['time_min'] is None:
            return '∞'
        m = int(round(seg['time_min']))
        return f'{m // 60:02d}:{m % 60:02d} h'

    def _b510_panel_read(self, instance_id):
        inst = self._instances[instance_id]
        s = inst['state']
        slots = self._metadata['asset_types'][NABERTHERM_TYPE]['state_slots']
        out = {sid: self._b510_observed(inst, sid) for sid in slots if slots[sid]['visibility'] == 'agent'}
        set_ = s['b510.settings']
        f = set_['unit'] == 'F'
        conv = lambda c: None if c is None else round(c * 1.8 + 32 if f else c, 1)      # noqa: E731
        reading = round(s['thermal.indicated_temperature_k'] - 273.15, 1)          # the corrected reading (measured + calibration, p.49)
        now = self._b510_clock(s)
        run = s['b510.run']
        running = run is not None and run['phase'] == 'running'

        def plan_pts(p, start):
            pts, x = [[0.0, conv(start)]], 0.0
            for a, b, d in _b510_plan(p, start):
                x += 3600.0 if d is None else d
                pts.append([x, conv(b)])
            return pts

        def dur_text(d):
            return '∞' if d is None else f'{int(d // 3600)}h {int(d % 3600 // 60)}min'

        def seg_rows(prog, start, current=None):
            noms = _b510_nominal(prog, start)
            rows = []
            for k, sg in enumerate(prog['segments']):
                delta = abs(sg['target_C'] - noms[k])
                t_min = sg['time_min'] if sg['mode'] == 'time' else (delta / sg['rate_K_h'] * 60.0)          # the other entry converted (p.42)
                rate = sg['rate_K_h'] if sg['mode'] == 'rate' or not sg['time_min'] else min(max(delta / (sg['time_min'] / 60.0), 1.0), _B510_RATE_MAX)
                tm = 0 if t_min is None else int(round(t_min))
                rows.append({'index': k, 'no': str(k + 1), 'target': conv(sg['target_C']), 'time': self._b510_time_text(sg, f), 'hb': round(sg['hb_K'] * (1.8 if f else 1.0), 1),
                             'extras': ' '.join(f'[{i + 1}]' for i in range(_B510_EXTRAS) if sg['extras'][i]) or '-', 'extras_raw': list(sg['extras']),
                             'hold': delta < 1e-9, 'show_hb': delta < 1e-9 and prog['holdback'] == 'manual', 'mode': sg['mode'],
                             'endless': sg['mode'] == 'time' and sg['time_min'] is None, 'time_min': sg['time_min'], 'rate_K_h': sg['rate_K_h'],
                             'conv_h': tm // 60, 'conv_m': tm % 60, 'conv_rate': round(rate * (1.8 if f else 1.0), 1),
                             'editable': current is None or k >= current, 'current': current == k})
            return rows

        progs = []
        for k, p in enumerate(s['b510.programs']):
            if p is None:
                continue
            progs.append({'slot': k, 'label': f'P{k + 1:02d}', 'name': p['name'], 'max': conv(max(sg['target_C'] for sg in p['segments'])), 'fav': p['favourite'],
                          'category': p['category'] or '', 'duration': dur_text(_b510_total_s(p)), 'active': run is not None and run['slot'] == k,
                          'star': '*' if p['favourite'] else ''})
        rest = seg = None
        if running:
            a, b, d = run['profile']
            later = _b510_plan(run['program'], run['start_C'])[run['segment'] + 1:]
            if d is not None and all(z is not None for _, _, z in later):
                rest = max(0.0, d - run['seg_elapsed_s']) + sum(z for _, _, z in later)
            left = None if d is None else d - run['seg_elapsed_s']
            kind = 'hold' if abs(a - b) < 1e-9 else ('rise' if b > a else 'fall') if (d is None or d > 0) else ('jump up' if b > a else 'jump down')
            seg = {'index': run['segment'], 'n': f"{run['segment'] + 1:02d} / {len(run['program']['segments']):02d}", 'from': conv(a), 'to': conv(b), 'icon': kind,
                   'elapsed': self._b510_hm(run['seg_elapsed_s']), 'total': '∞' if d is None else self._b510_hm(d), 'left': '∞' if left is None else self._b510_hm(left),
                   'progress': 0.0 if not d else round(min(1.0, run['seg_elapsed_s'] / d), 4),
                   'extras': ' '.join(f'[{i + 1}]' for i in range(_B510_EXTRAS) if s['b510.extras']['outputs'][i])}
        ed = s['b510.editing']
        msgs = []
        if s['b510.error'] is not None:
            msgs.append(dict(s['b510.error'], kind='error'))
        msgs += [dict(w, kind='warning') for w in s['b510.warnings']]
        if all(row[2] <= 0 for row in s['b510.pid']):
            msgs.append({'code': 'W01', 'text': _B510_STATUS_WARNINGS['W01'], 'kind': 'status'})
        if not s['door.closed']:
            msgs.append({'code': 'W18', 'text': _B510_STATUS_WARNINGS['W18'], 'kind': 'status'})
        fmt_date = '%d.%m.%Y' if set_['date_format'] == 'DD.MM.YYYY' else '%m-%d-%Y'
        fmt_time = '%H:%M' if set_['time_format'] == '24h' else '%I:%M %p'
        end_txt = None
        if rest is not None:
            end = now + _b510_timedelta(seconds=rest)
            end_txt = ('Today ' if end.date() == now.date() else end.strftime(fmt_date) + ' ') + end.strftime(fmt_time)
        last = s['b510.last_run']
        tune = s['b510.autotune']
        stats = s['b510.statistics']
        hrs = stats['hours']
        out.update({
            'host.powered': bool(s['power.enabled'] and s['b510.outage'] is None), 'host.power_epoch': inst['b510_epoch'],
            'host.temp': conv(reading), 'host.unit': '°F' if f else '°C', 'host.f': f, 'host.setpoint': conv(run['setpoint_C']) if running else None,
            'host.running': run is not None, 'host.active': running, 'host.delayed': bool(run and run['phase'] == 'delayed'), 'host.paused': bool(run and run['paused']),
            'host.waiting': bool(run and (run['waiting'] or run['grad_hold'] or run['alarm_hold'])), 'host.run_name': None if run is None else run['name'],
            'host.run_slot': None if run is None else run['slot'], 'host.manual_hb': bool(run and run['program']['holdback'] == 'manual'),
            'host.delayed_until': None if run is None else run['delayed_until'], 'host.programs': progs,
            'host.free_slot': next((k for k, p in enumerate(s['b510.programs']) if p is None), None), 'host.program_count': len(progs),
            'host.segment': seg, 'host.segment_count': 0 if run is None else len(run['program']['segments']),
            'host.remaining': None if rest is None else '-' + self._b510_hm(rest), 'host.expired': None if run is None else self._b510_hm(run['elapsed_s']),
            'host.end_time': end_txt, 'host.graph_actual': [[p[0], conv(p[1])] for p in s['b510.graph']], 'host.graph_setpoint': [[p[0], conv(p[2])] for p in s['b510.graph']],
            'host.graph_span': max([3600.0, (s['b510.graph'][-1][0] * 1.1 if s['b510.graph'] else 0.0)] + ([plan_pts(run['program'], run['start_C'])[-1][0]] if running else [])),
            'host.run_plan': plan_pts(run['program'], run['start_C']) if running else [],
            'host.plans': [{'slot': k, 'pts': plan_pts(p, 20.0), 'span': max(3600.0, plan_pts(p, 20.0)[-1][0])} for k, p in enumerate(s['b510.programs']) if p is not None],
            'host.date': now.strftime(fmt_date), 'host.time': now.strftime(fmt_time), 'host.clock_date': now.strftime('%Y-%m-%d'), 'host.clock_hm': now.strftime('%H:%M'),
            'host.level': 0 if s['b510.user'] is None else _B510_LEVEL[s['b510.user']], 'host.user_text': (s['b510.user'] or 'LOG IN').upper(),
            'host.heating_percent': round(s['b510.output_percent']),
            'host.editing_rows': [] if ed is None else seg_rows(ed['program'], ed['program']['start_C'] if ed['program']['start_C'] is not None else 20.0),
            'host.editing_count': 0 if ed is None else len(ed['program']['segments']),
            'host.active_rows': [] if run is None else seg_rows(run['program'], run['start_C'], run['segment']),
            'host.messages': msgs, 'host.message': msgs[0] if msgs else None, 'host.has_message': bool(msgs),
            'host.last_name': None if last is None else last['name'], 'host.last_start': None if last is None else last['start'],
            'host.last_slot_ok': last is not None and s['b510.programs'][last['slot']] is not None and run is None,
            'host.end_extras_on': any(s['b510.extras']['hold_end']), 'host.tune_result': tune['result'], 'host.tune_state': tune['state'],
            'host.cal_rows': [{'index': k, 'no': str(k + 1), 'temp': conv(r[0]), 'off': round(r[1] * (1.8 if f else 1.0), 1)} for k, r in enumerate(s['b510.calibration'])],
            'host.pid_rows': [{'index': k, 'no': str(k + 1), 'range': f'{conv(r[0]):g} -> {conv(r[1]):g}', 'from': conv(r[0]), 'to': conv(r[1]), 'xp': r[2], 'ti': r[3], 'td': r[4]}
                              for k, r in enumerate(s['b510.pid'])],
            'host.elapsed_s': None if run is None else run['elapsed_s'], 'host.ambient': conv(self.ambient_temperature_K - 273.15),
            'host.now_point': [[run['elapsed_s'], conv(reading)]] if running else [],
            'host.last_span': max(3600.0, s['b510.graph'][-1][0] if s['b510.graph'] else 0.0),
            'host.error_rows': [dict(e, label=f"{e['time']}  {e['code']}  {e['text']}") for e in reversed(s['b510.error_log'])],
            'host.pid_actual': {'sp': conv(s['thermal.target_k'] - 273.15), 'pv': conv(reading), 'p': round(s['b510.pid_out']['p'], 1), 'i': round(s['b510.pid_out']['i'], 1),
                                'd': round(s['b510.pid_out']['d'], 1), 'out': round(s['b510.pid_out']['out'], 1),
                                'row': None if s['b510.pid_out']['row'] is None else s['b510.pid_out']['row'] + 1},
            'host.record_file': s['b510.records'][-1]['file'] if s['b510.records'] else '-', 'host.record_count': len(s['b510.records']),
            'host.cold': self._b510_reading(s) < _B510_COLD_C, 'host.categories': [{'name': c} for c in s['b510.categories']],
            'host.door_open': not s['door.closed'],
            'host.stats': {**stats, 'hours_text': f'{int(hrs // 24)}D {int(hrs % 24)} h {int(hrs * 60 % 60)}min', 'kwh_total_r': round(stats['kwh_total'], 2),
                           'kwh_last_r': round(stats['kwh_last'], 2)}})
        return out

    # ---- controller functions on a candidate state (the session commits)
    def _b510_exec(self, s, cap, args, t):
        """One controller / evaluator function on the candidate state s: the result dict, or an error code."""
        run, ed, set_ = s['b510.run'], s['b510.editing'], s['b510.settings']
        progs = s['b510.programs']
        if cap == 'access.login':
            if s['b510.passwords'][args['user']] != args['password']:
                return 'PRECONDITION_FAILED'                                      # PASSWORD INCORRECT (p.74)
            s['b510.user'] = args['user']
            return {'user': args['user']}
        if cap == 'access.logout':
            s['b510.user'] = None if set_['operation_lock'] else set_['standard_user']
            return {'user': s['b510.user']}
        if cap == 'access.set_password':
            me = s['b510.user']
            if not args['password'].isdigit():
                return 'INVALID_ARGUMENT'
            if not (args['user'] == me or (me == 'administrator' and args['user'] in ('operator', 'administrator'))):
                return 'PRECONDITION_FAILED'
            s['b510.passwords'] = dict(s['b510.passwords'], **{args['user']: args['password']})
            return {'user': args['user']}
        if cap == 'program.edit':
            p = progs[args['slot']]
            s['b510.editing'] = {'slot': args['slot'], 'program': copy.deepcopy(p) if p else _b510_program(args['slot']), 'modified': p is None, 'new': p is None}
            return {'slot': args['slot']}
        if cap.startswith('program.edit_'):
            if ed is None:
                return 'PRECONDITION_FAILED'
            prog = ed['program']
            segs = prog['segments']
            if cap == 'program.edit_set':
                if 'name' in args and not _b510_name_ok(args['name']):
                    return 'INVALID_ARGUMENT'
                prog.update(copy.deepcopy(args))
                if prog['start_C'] is not None:
                    prog['start_C'] = float(prog['start_C'])
                ed['modified'] = True
                return {'ok': True}
            if cap == 'program.edit_segment':
                i = args['index']
                if i >= len(segs):
                    return 'PRECONDITION_FAILED'
                segs[i] = self._b510_seg_update(prog, i, args)
                ed['modified'] = True
                return {'index': i}
            if cap == 'program.edit_insert':
                i = args['index']
                if len(segs) >= _B510_SEGMENTS or i > len(segs):
                    return 'PRECONDITION_FAILED'
                segs.insert(i, copy.deepcopy(segs[i - 1] if i > 0 else segs[0]) if segs else _b510_segment())
                ed['modified'] = True
                return {'count': len(segs)}
            if cap == 'program.edit_delete':
                mask = args['mask']
                if not any(mask) or any(mask[k] for k in range(len(segs), _B510_SEGMENTS)):
                    return 'PRECONDITION_FAILED'
                prog['segments'] = [sg for k, sg in enumerate(segs) if not mask[k]]
                ed['modified'] = True
                return {'count': len(prog['segments'])}
            if cap == 'program.edit_move':
                i, j = args['index'], args['index'] + args['offset']
                if i >= len(segs) or not 0 <= j < len(segs):
                    return 'PRECONDITION_FAILED'
                segs[i], segs[j] = segs[j], segs[i]
                ed['modified'] = True
                return {'index': j}
            if cap == 'program.edit_save':
                if not segs:
                    return 'PRECONDITION_FAILED'                                  # a program needs at least one segment (assumption)
                progs[ed['slot']] = copy.deepcopy(prog)
                ed.update(modified=False, new=False)
                return {'slot': ed['slot']}
            s['b510.editing'] = None                                              # program.edit_discard
            return {'ok': True}
        if cap in ('program.copy', 'program.delete', 'program.set_favourite', 'program.set_category'):
            k = args['slot']
            if progs[k] is None:
                return 'PRECONDITION_FAILED'
            if cap == 'program.copy':
                free = next((j for j, p in enumerate(progs) if p is None), None)
                if free is None:
                    return 'PRECONDITION_FAILED'                                  # no empty program slot (p.46)
                progs[free] = copy.deepcopy(progs[k])
                return {'slot': free}
            if cap == 'program.delete':
                if run is not None and run['slot'] == k:
                    return 'BUSY'
                progs[k] = None
                if ed is not None and ed['slot'] == k:
                    s['b510.editing'] = None
                return {'slot': k}
            if cap == 'program.set_favourite':
                progs[k]['favourite'] = args['on']
                return {'on': args['on']}
            if args['category'] is not None and args['category'] not in s['b510.categories']:
                return 'PRECONDITION_FAILED'
            progs[k]['category'] = args['category']
            if ed is not None and ed['slot'] == k:
                ed['program']['category'] = args['category']
            return {'slot': k}
        if cap in ('categories.add', 'categories.rename', 'categories.delete'):
            cats = list(s['b510.categories'])
            new = args.get('new_name', args['name'])
            if cap != 'categories.delete' and (not _b510_name_ok(new) or new in cats):
                return 'INVALID_ARGUMENT'
            if cap == 'categories.add':
                if len(cats) >= _B510_CATEGORIES:
                    return 'PRECONDITION_FAILED'                                  # up to 6 categories (p.35)
                cats.append(new)
            else:
                if args['name'] not in cats:
                    return 'PRECONDITION_FAILED'
                if cap == 'categories.rename':
                    cats[cats.index(args['name'])] = new
                else:
                    cats.remove(args['name'])
                for p in [p for p in progs if p is not None] + ([ed['program']] if ed is not None else []):
                    if p['category'] == args['name']:
                        p['category'] = new if cap == 'categories.rename' else None
            s['b510.categories'] = cats
            return {'count': len(cats)}
        if cap in ('program.start', 'program.restart_last'):
            if cap == 'program.restart_last':
                last = s['b510.last_run']
                if last is None or progs[last['slot']] is None:
                    return 'PRECONDITION_FAILED'
                slot, delayed = last['slot'], None
            else:
                if ('delayed_date' in args) != ('delayed_time' in args):
                    return 'INVALID_ARGUMENT'
                slot, delayed = args['slot'], (args['delayed_date'] + ' ' + args['delayed_time']) if 'delayed_date' in args else None
            if run is not None or s['program.running'] or s['b510.autotune']['state'] == 'running':
                return 'BUSY'
            if progs[slot] is None or not s['door.closed'] or s['b510.error'] is not None:
                return 'PRECONDITION_FAILED'
            if delayed is not None:
                try:
                    when = datetime.strptime(delayed, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
                except ValueError:
                    return 'INVALID_ARGUMENT'
                if when <= self._b510_clock(s, t):
                    return 'INVALID_ARGUMENT'
            self._b510_start(s, slot, progs[slot], t, delayed)
            return {'slot': slot}
        if cap == 'program.stop':
            if run is not None:
                self._b510_end(s, t, aborted=True)
            elif any(s['b510.extras']['hold_end']):
                s['b510.extras']['hold_end'] = [False, False]                     # second stop: end-segment extras off (p.27)
                self._b510_extras_out(s, [False, False])
            elif s['b510.autotune']['state'] == 'running':
                s['b510.autotune'] = dict(s['b510.autotune'], state='off', phase=None)
                s['thermal.target_k'] = self.ambient_temperature_K
            else:
                return 'PRECONDITION_FAILED'
            return {'running': False}
        if cap in ('program.pause', 'program.jump', 'program.change_active', 'controller.lock'):
            if run is None:
                return 'PRECONDITION_FAILED'
            if cap == 'controller.lock':
                s['b510.locked'] = True
                return {'locked': True}
            if run['phase'] != 'running' and cap != 'program.change_active':
                return 'PRECONDITION_FAILED'
            if cap == 'program.pause':
                run['paused'] = args['on']
                return {'on': args['on']}
            prog = run['program']
            if cap == 'program.jump':
                if args['segment'] > len(prog['segments']):
                    return 'PRECONDITION_FAILED'
                actual = self._b510_reading(s)
                idx = self._b510_skip(prog, args['segment'] - 1, actual, run['start_C'])   # the actual value is always applied (p.48)
                run['setpoint_C'] = actual
                self._b510_begin(s, idx, actual)
                run['setpoint_C'] = self._b510_setpoint(s, run)
                s['program.phase'] = 'hold' if self._b510_is_hold(run) else 'ramp'
                return {'segment': idx + 1}
            i = args['index']
            if i >= len(prog['segments']) or (run['phase'] == 'running' and i < run['segment']):
                return 'PRECONDITION_FAILED'                                      # past segments only after a jump back (p.47)
            prog['segments'][i] = self._b510_seg_update(prog, i, args)
            run['max_sp_C'] = max(sg['target_C'] for sg in prog['segments'])
            if run['phase'] == 'running' and i == run['segment'] and not self._b510_is_hold(run):
                actual = self._b510_reading(s)                                    # a current ramp goes on from the actual value (p.48)
                run['setpoint_C'] = actual
                self._b510_begin(s, i, actual)
                run['setpoint_C'] = self._b510_setpoint(s, run)
            return {'index': i}
        if cap == 'extras.set_manual':
            ex = s['b510.extras']
            if not ex['enabled'][args['index']]:
                return 'PRECONDITION_FAILED'
            ex['manual'][args['index']] = args['mode']
            seg_extras = run['program']['segments'][run['segment']]['extras'] if run is not None and run['phase'] == 'running' else ex['hold_end']
            self._b510_extras_out(s, seg_extras)
            return {'mode': args['mode']}
        if cap == 'extras.configure':
            ex = s['b510.extras']
            if 'name' in args and not _b510_name_ok(args['name']):
                return 'INVALID_ARGUMENT'
            if 'name' in args:
                ex['names'][args['index']] = args['name']
            if 'enabled' in args:
                ex['enabled'][args['index']] = args['enabled']
                if not args['enabled']:
                    ex['manual'][args['index']] = 'auto'
            seg_extras = run['program']['segments'][run['segment']]['extras'] if run is not None and run['phase'] == 'running' else ex['hold_end']
            self._b510_extras_out(s, seg_extras)
            return {'index': args['index']}
        if cap == 'controller.unlock':
            if s['b510.passwords'][args['user']] != args['password']:
                return 'PRECONDITION_FAILED'
            s['b510.locked'] = False
            return {'locked': False}
        if cap == 'settings.configure':
            if 'furnace_name' in args and not _b510_name_ok(args['furnace_name']):
                return 'INVALID_ARGUMENT'
            s['b510.settings'] = dict(set_, **copy.deepcopy(args))
            return {'ok': True}
        if cap == 'settings.wizard':
            s['b510.settings'] = dict(set_, language=args['language'], unit=args['unit'])
            return {'ok': True}
        if cap == 'calibration.set_point':
            s['b510.calibration'][args['index']] = [float(args['temp_C']), float(args['offset_K'])]
            return {'index': args['index']}
        if cap == 'pid.set_point':
            if args['from_C'] > args['to_C']:
                return 'INVALID_ARGUMENT'
            s['b510.pid'][args['index']] = [float(args[k]) for k in ('from_C', 'to_C', 'xp', 'ti', 'td')]
            return {'index': args['index']}
        if cap == 'alarms.configure':
            i = args['index']
            s['b510.alarms'][i] = dict(s['b510.alarms'][i], **{k: v for k, v in args.items() if k != 'index'})
            s['b510.alarm_status'][i] = {'active_s': 0.0, 'latched': False}
            return {'index': i}
        if cap == 'messages.acknowledge':
            s['b510.warnings'] = []
            if s['b510.error'] is not None and s['b510.error']['code'] not in _B510_POWER_OFF_ONLY:
                s['b510.error'] = None
            s['b510.alarm_status'] = [dict(x, latched=False) if x['latched'] and x['active_s'] == 0 else x for x in s['b510.alarm_status']]
            return {'ok': True}
        if cap == 'autotune.start':
            if run is not None or s['program.running'] or s['b510.autotune']['state'] == 'running':
                return 'BUSY'
            if self._b510_reading(s) >= _B510_COLD_C or not s['door.closed'] or s['b510.error'] is not None:
                return 'PRECONDITION_FAILED'                                      # only with the furnace cold (p.64)
            s['b510.autotune'] = {'state': 'running', 'opt_C': float(args['opt_C']), 'phase': 'heat1', 'times': [], 'result': None, 'phase_s': 0.0}
            return {'state': 'running'}
        if cap == 'autotune.apply':
            res = s['b510.autotune']['result']
            if res is None:
                return 'PRECONDITION_FAILED'
            row = s['b510.pid'][args['index']]
            s['b510.pid'][args['index']] = [row[0], row[1], res['xp'], res['ti'], res['td']]
            return {'index': args['index']}
        if cap == 'clock.set':
            if run is not None:
                return 'BUSY'                                                     # only when no program is active (p.88)
            now = self._b510_clock(s, t)
            try:
                new = datetime.strptime(args.get('date', now.strftime('%Y-%m-%d')) + ' ' + args.get('time', now.strftime('%H:%M')), '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            except ValueError:
                return 'INVALID_ARGUMENT'
            if not 2000 <= new.year <= 2099:
                return 'INVALID_ARGUMENT'
            s['b510.clock_offset_s'] = (new - _B510_EPOCH).total_seconds() - t
            return {'clock': new.strftime('%Y-%m-%dT%H:%M:%S')}
        if cap in ('usb.export', 'usb.import'):
            self._b510_warn(s, 'W16')                                             # no stick in the simulated furnace (p.122)
            return {'ok': False}
        if cap == 'diagnostics.inject_error':
            self._b510_fault(s, args['code'], t)
            return {'code': args['code']}
        if cap == 'diagnostics.set_mains':
            self._b510_mains(s, args['present'], t)
            return {'present': args['present']}
        return 'UNKNOWN_CAPABILITY'

    def _b510_seg_update(self, prog, i, args):
        """A segment with new fields; switching between time and rate converts automatically (p.42)."""
        seg = prog['segments'][i]
        new = dict(seg, **{k: copy.deepcopy(v) for k, v in args.items() if k != 'index'})
        for k in ('target_C', 'time_min', 'rate_K_h', 'hb_K'):
            if new[k] is not None:
                new[k] = float(new[k])
        nom = _b510_nominal(prog, prog['start_C'] if prog['start_C'] is not None else 20.0)[i]
        delta = abs(new['target_C'] - nom)
        if 'mode' in args and args['mode'] != seg['mode']:
            if args['mode'] == 'rate' and 'rate_K_h' not in args and seg['time_min']:
                new['rate_K_h'] = float(min(max(round(delta / (seg['time_min'] / 60.0), 1), 1.0), _B510_RATE_MAX))
            if args['mode'] == 'time' and 'time_min' not in args:
                new['time_min'] = float(min(round(delta / seg['rate_K_h'] * 60.0, 2), _B510_TIME_MAX_MIN))
        return new


class NaberthermL9Runtime(_B510Controller):
    """Trusted multi-instance host. Do not expose host internals to agents.  Each instance carries its B510 controller (G02 front panel)."""
    def __init__(self, *, ambient_temperature_K=NABERTHERM_AMBIENT_K, scene_adapter=None):
        if not _number(ambient_temperature_K) or not 273.15 <= ambient_temperature_K <= 313.15:
            raise ValueError("Ambient temperature must be finite and within the assumed 273.15..313.15 K envelope")
        self.ambient_temperature_K = float(ambient_temperature_K)
        self.scene_adapter = scene_adapter
        self.time_s = 0.0
        self._instances = {}
        self._principals = {"__public_agent__": ("agent", None)}
        self._requests = {}
        self._logs = {"__public_agent__": []}
        self._lock = threading.RLock()
        self._metadata = export_nabertherm_contract_metadata(panel=False)
        self._agent = NaberthermL9Session(self, "__public_agent__")
        self._b510_panels, self.panel_capture_dir = {}, None                 # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)
        self._principals["__b510_panel__"] = ("agent", None)
        self._logs["__b510_panel__"] = []
        self._panel_session = NaberthermL9Session(self, "__b510_panel__")    # the front panel's own path to the commands (not logged)
        self._panel_caller = None                                            # the session whose gesture runs: its commands act as that caller

    def create_instance(self, instance_id, asset_type_id=NABERTHERM_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != NABERTHERM_TYPE:
                raise ValueError("A unique nonempty instance ID and the declared Nabertherm type are required")
            state = {"power.enabled": False, "thermal.target_k": self.ambient_temperature_K,
                "thermal.temperature_k": self.ambient_temperature_K,
                "thermal.indicated_temperature_k": round(self.ambient_temperature_K - 273.15, 1) + 273.15,
                "door.angle_rad": 0.0, "door.closed": True, "heater.enabled": False,
                "program.segments": [], "program.running": False, "program.segment_index": 0,
                "program.phase": "idle", "program.hold_elapsed_s": 0.0, **self._b510_defaults()}
            if self.scene_adapter:
                self.scene_adapter(instance_id, copy.deepcopy(state))
            self._instances[instance_id] = {"state": state, "revision": 0, "resume_phase": "ramp", "b510_epoch": 0}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Invalid or already registered host identity")
            scope = None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope <= self._instances.keys():
                raise ValueError("Unknown instance in identity scope")
            self._principals[principal_id] = (visibility, scope)
            self._logs[principal_id] = []

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Identity must be registered by trusted host")
        return NaberthermL9Session(self, principal_id)

    def describe(self, **kwargs):
        return self._agent.describe(**kwargs)

    def read(self, **kwargs):
        return self._agent.read(**kwargs)

    def invoke(self, **kwargs):
        return self._agent.invoke(**kwargs)

    def write(self, **kwargs):
        return self._agent.write(**kwargs)

    def describe_host(self):
        return copy.deepcopy(self._metadata["host_capabilities"])

    def debug_snapshot(self):
        with self._lock:
            return copy.deepcopy({"time_s": self.time_s, "instances": self._instances})

    def sync_scene(self, instance_ids=None):
        with self._lock:
            if self.scene_adapter is None:
                raise RuntimeError("No scene adapter registered")
            ids = list(self._instances) if instance_ids is None else list(instance_ids)
            if any(key not in self._instances for key in ids):
                raise ValueError("Unknown instance")
            for key in ids:
                try:
                    self.scene_adapter(key, copy.deepcopy(self._instances[key]["state"]))
                except Exception:
                    raise SceneSynchronizationError("Scene synchronization failed; backend may have partial effects") from None

    def _thermal_step(self, state, dt, factor=1.0, force=None, i_cap=None):
        """Advance the lumped furnace C dT/dt = P - UA (T - Ta) by dt s; returns the heating energy (J).  The heating power is the B510 output:
        the PID of the base point that contains the set point, sampled every _B510_PID_TS s on the host clock and held between samples (output
        limited to factor x 100 %, the start-up circuit), or a fixed output force (0..1: self-optimization, solar mode).  The door switch and the
        main switch cut the heating (heater.enabled); the controller then holds its integral."""
        t0 = getattr(self, '_b510_t', self.time_s)
        self._b510_t = t0 + dt
        ambient, capacity = self.ambient_temperature_K, NABERTHERM_CAPACITY_J_K
        loss = NABERTHERM_LOSS_W_K + (0 if state["door.closed"] else NABERTHERM_OPEN_LOSS_W_K)
        pmax = NABERTHERM_POWER_W

        def plant(T, out, h):
            p = pmax * out / 100.0
            eq = ambient + p / loss
            return eq + (T - eq) * math.exp(-loss * h / capacity)

        def finish(T, log):
            state["thermal.temperature_k"] = max(ambient, T)
            t_C = state["thermal.temperature_k"] - 273.15                     # the shown value: measured + calibration offset (p.49), 0.1 K
            state["thermal.indicated_temperature_k"] = round(t_C + _b510_offset(state["b510.calibration"], t_C), 1) + 273.15
            return sum(pmax * out / 100.0 * h for out, h in log)

        T = state["thermal.temperature_k"]
        pid = dict(state["b510.pid_out"])
        if not state["heater.enabled"] or force is not None or factor <= 0:
            out = 0.0 if not state["heater.enabled"] or factor <= 0 else 100.0 * force
            pid.update(e_prev=None, df=0.0, u=out, out=out, p=0.0, d=0.0, t_next=None)
            if pid != state["b510.pid_out"]:
                state["b510.pid_out"] = pid
            return finish(plant(T, out, dt), [(out, dt)])
        target = self._b510_control_k(state, state["thermal.target_k"])     # the set point in measured terms: the reading is held there
        row = self._b510_pid_row(state["b510.pid"], state["thermal.target_k"] - 273.15)
        if row is None:                                                       # no P value: W01, no heating (p.121)
            pid.update(e_prev=None, df=0.0, u=0.0, out=0.0, p=0.0, i=0.0, d=0.0, row=None, t_next=None)
            state["b510.pid_out"] = pid
            return finish(plant(T, 0.0, dt), [(0.0, dt)])
        k, (_, _, xp, ti, td) = row
        kp, hi, ts = 100.0 / xp, 100.0 * min(1.0, factor), _B510_PID_TS
        i_hi = 100.0 if i_cap is None else min(100.0, i_cap)
        if pid.get("row") != k:
            pid.update(row=k)
        hold = 100.0 * loss * (target - ambient) / pmax

        def control(T):
            return _g02_pid_sample(pid, target - T, kp, ti, td, ts, 0.0, hi, i_hi=i_hi)

        def saturated_for(T, out):
            dl = 100.0 if td > 0 else 0.0
            if out >= hi - 1e-12 and hi > 0:                                  # P - |I| - |D| still above the limit: full output
                goal, p = target - (hi + 100.0 + dl) / kp, pmax * hi / 100.0
                if T >= goal:
                    return 0.0
            elif out <= 1e-12:                                                # P + I + D still below zero: no output
                goal, p = target + (i_hi + dl) / kp, 0.0
                if T <= goal:
                    return 0.0
            else:
                return None
            eq = ambient + p / loss
            if (goal - T) * (eq - goal) <= 0:
                return math.inf
            return -capacity / loss * math.log((eq - goal) / (eq - T))

        def settled(T):
            e = target - T
            if pid.get("e_prev") is None or abs(e) > 1e-6 or abs(pid["e_prev"]) > 1e-6 or abs(pid["i"] - hold) > 1e-4 or not 0.0 <= hold <= hi or target <= ambient:
                return None
            pid.update(i=hold, e_prev=0.0, df=0.0, p=0.0, d=0.0, out=hold)
            return target, hold

        T, log = _g02_run_sampled(T, t0, dt, pid, ts, control, plant, saturated_for, settled)
        state["b510.pid_out"] = pid
        return finish(T, log)

    @staticmethod
    def _b510_pid_row(table, sp_C):
        """The base point whose temperature range contains the set point (else the nearest one with a P value); None without any P value."""
        rows = [(k, r) for k, r in enumerate(table) if r[2] > 0]
        if not rows:
            return None
        inside = [(k, r) for k, r in rows if r[0] <= sp_C <= r[1]]
        if inside:
            return inside[0]
        return min(rows, key=lambda kr: min(abs(sp_C - kr[1][0]), abs(sp_C - kr[1][1])))

    def _advance(self, inst, dt):
        s = inst["state"]
        self._b510_t = self.time_s                                          # host clock of the controller samples
        if self._b510_active(s) and not (s["program.running"] and s["b510.run"] is None):
            self._b510_advance(inst, dt)                                      # B510 program, self-optimization, monitoring (1 s steps)
            return
        energy = self._legacy_advance(s, dt)
        self._b510_account(s, dt, energy)

    def _legacy_advance(self, s, dt):
        if not s["program.running"]:
            return self._thermal_step(s, dt)
        remaining, energy = dt, 0.0
        while remaining > 1e-12:
            h = min(1.0, remaining)
            if not s["program.running"]:
                energy += self._thermal_step(s, remaining)
                break
            segment = s["program.segments"][s["program.segment_index"]]
            goal = segment["temperature_K"]
            delta = goal - s["thermal.target_k"]
            s["thermal.target_k"] += math.copysign(min(abs(delta), segment["ramp_rate_K_s"] * h), delta) if delta else 0
            before_close = abs(self._b510_reading_k(s) - goal) <= 0.5               # the controller judges arrival on its own reading
            energy += self._thermal_step(s, h)
            at_goal = abs(s["thermal.target_k"] - goal) <= 1e-9 and abs(self._b510_reading_k(s) - goal) <= 0.5
            if at_goal:
                was_hold = s["program.phase"] == "hold"
                s["program.phase"] = "hold"
                if before_close and was_hold:
                    s["program.hold_elapsed_s"] = min(segment["hold_s"], s["program.hold_elapsed_s"] + h)
                if s["program.hold_elapsed_s"] >= segment["hold_s"]:
                    s["program.segment_index"] += 1
                    if s["program.segment_index"] == len(s["program.segments"]):
                        s["program.running"] = False
                        s["program.phase"] = "completed"
                        s["thermal.target_k"] = self.ambient_temperature_K
                    else:
                        s["program.phase"] = "ramp"
                        s["program.hold_elapsed_s"] = 0.0
            remaining -= h
        return energy

    def step(self, dt_s):
        if not _number(dt_s) or not 0 <= dt_s <= 86400:
            raise ValueError("Host time step must be finite and within 0..86400 s")
        if not dt_s:
            return
        with self._lock:
            for inst in self._instances.values():
                self._advance(inst, float(dt_s))
                inst["revision"] += 1
            self.time_s += dt_s
            if self.scene_adapter:
                failures = []
                for key, inst in self._instances.items():
                    try:
                        self.scene_adapter(key, copy.deepcopy(inst["state"]))
                    except Exception:
                        failures.append(key)
                if failures:
                    raise SceneSynchronizationError("World process and clock advanced; scene synchronization failed") from None


class NaberthermL9Session:
    """Public uniform adapter. Identity cannot be supplied in read/invoke args."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal = host, principal_id

    def _visible(self, slot):
        return slot["visibility"] == "agent" or self.__host._principals[self.__principal][0] in ("debug", "evaluator")

    def _instance(self, instance_id):
        if not isinstance(instance_id, str):
            return None
        _, scope = self.__host._principals[self.__principal]
        return self.__host._instances.get(instance_id) if scope is None or instance_id in scope else None

    def _envelope(self, instance_id, request_id=None, *, result=None, observations=None, status="succeeded", error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": PROTOCOL_VERSION, "instance_id": instance_id if isinstance(instance_id, str) else None,
            "request_id": request_id if isinstance(request_id, str) else None, "operation_id": None, "status": status,
            "state_revision": inst["revision"] if inst else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": {} if result is None else copy.deepcopy(result), "observations": {} if observations is None else observations, "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Instance is unavailable in this session.", "UNKNOWN_CAPABILITY": "Capability is unavailable.",
            "INVALID_ARGUMENT": "Arguments do not meet declared schema or limits.", "ACCESS_DENIED": "Request is outside session access policy.",
            "PRECONDITION_FAILED": "Required operating state or configuration is unavailable.", "BUSY": "Stop the active program first.",
            "REQUEST_CONFLICT": "Request ID already belongs to different arguments.", "UNSUPPORTED": "Execution is unavailable in this adapter.",
            "DEVICE_ERROR": "Scene callback did not complete the command."}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": messages[code],
            "retryable": False, "side_effects": "unknown" if code == "DEVICE_ERROR" else "none", "progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(None, "INVALID_ARGUMENT")
            if instance_id is not None and not self._instance(instance_id):
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != NABERTHERM_TYPE:
                return self._error(None, "INVALID_ARGUMENT")
            _, scope = self.__host._principals[self.__principal]
            if scope is not None and not scope:
                return self._error(instance_id, "ACCESS_DENIED")
            spec = copy.deepcopy(export_nabertherm_contract_metadata()["asset_types"][NABERTHERM_TYPE])
            spec["state_slots"] = {sid: slot for sid, slot in spec["state_slots"].items() if self._visible(slot)}
            spec["capabilities"].update(spec.pop("declared_capabilities"))
            for group in ("controller_capabilities", "evaluator_capabilities", "panel_capabilities"):
                spec["capabilities"].update({k: v for k, v in spec.pop(group).items() if self._visible(v)})
            for cap in spec["capabilities"].values():
                # Hidden state IDs must not be reintroduced through metadata effects.
                cap["state_effects"] = [effect for effect in cap["state_effects"] if effect["state_id"] in spec["state_slots"]]
                cap["observes"] = [sid for sid in cap["observes"] if sid in spec["state_slots"]]
                if cap.get("scene_backend_required") and self.__host.scene_adapter is None:
                    cap["availability"] = "unsupported"
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": NABERTHERM_TYPE, "instance_id": instance_id,
                "adapter": "basic40_runtime_thermal.NaberthermL9Session", **spec,
                "limitations": ["Synthetic empty-furnace process; no sample chemistry, robot or hardware execution.",
                    "API truth filtering does not isolate Python introspection or files.",
                    "Displayed temperature (thermal.indicated_temperature_k = b510.reading_k) is the measured value plus the B510 calibration-table offset, rounded to 0.1 Celsius and marked estimated; every set point (B510 program, legacy thermal.*) is held in these corrected-reading terms.",
                    "Program ramp is commanded target slope; delivered heating remains power limited.",
                    "Legacy thermal.* API: one program of up to four stages; the B510 controller functions and front panel store five programs of four segments.",
                    "B510 controller (front panel and controller capabilities) from manual M03.0022; the asset geometry still shows the catalogue B410 panel position.",
                    "The B510 PID drives the heating power of the lumped plant (base point chosen by the set point, 1 s controller cycle, I and D limited to 100 %); the factory base point is tuned for the plant (two-cycle deadbeat), the manual's p.55 values are examples.",
                    "No USB stick, Wi-Fi network, Ethernet option, acoustic alarm or alarm relay wiring: export/import report W16, relay/acoustic reactions only raise the warning.",
                    "Faults other than 04-01..04-05 occur only by evaluator injection; mains outages by the evaluator diagnostics.set_mains.",
                    "Open-door heating inhibition follows the product-family manual; open-door extra loss is an unmeasured assumption.",
                    "1000 Celsius is the recommended prolonged hold temperature; 1100 Celsius is maximum.",
                    "In-memory deduplication does not survive host restart."]}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if not inst:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = self.__host._metadata["asset_types"][NABERTHERM_TYPE]["state_slots"]
            if state_ids is None:
                state_ids = [sid for sid, slot in slots.items() if self._visible(slot)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids) or len(state_ids) != len(set(state_ids)):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(sid not in slots or not self._visible(slots[sid]) for sid in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            observations = {sid: {"value": self.__host._b510_observed(inst, sid), "unit": slots[sid]["unit"],
                "quality": "estimated" if slots[sid]["origin"] == "derived" else "valid",
                "sample_time_s": self.__host.time_s, "sample_window_s": None} for sid in state_ids}
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id or len(request_id) > 256:
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = hashlib.sha256(json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = (self.__principal, instance_id, request_id)
            if key in host._requests:
                old_digest, response = host._requests[key]
                return copy.deepcopy(response) if old_digest == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, copy.deepcopy(response))
            host._logs[self.__principal].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id, "response": copy.deepcopy(response)})
            return response

    def _checked(self, instance_id, inst, cap, args, request_id):
        """Run ownership (lead rule 2026-10-19): the caller that starts a B510 program, the legacy program or the self-optimization owns it;
        another caller cannot stop, pause, jump or change it while it runs.  The furnace power switch is a hard switch (physical action, lead
        clarification 2026-10-19): power.set follows it and is never refused; the controller reacts as to a power failure (p.13, F p.47-48)."""
        s = inst["state"]
        active = s["b510.run"] is not None or s["program.running"] or s["b510.autotune"]["state"] == "running"
        guarded = cap in ("program.stop", "program.pause", "program.jump", "program.change_active", "extras.set_manual", "thermal.stop_program")
        if guarded and _g02_foreign_run(self.__host, self.__principal, inst.get("run_owner"), active):
            return self._error(instance_id, "ACCESS_DENIED", request_id)
        response = self._checked_body(instance_id, inst, cap, args, request_id)
        if cap in ("program.start", "program.restart_last", "thermal.start_program", "autotune.start") and response["status"] == "succeeded" and not active:
            inst["run_owner"] = self.__principal
        return response

    def _checked_body(self, instance_id, inst, cap, args, request_id):
        host, old = self.__host, inst["state"]
        spec = host._metadata["asset_types"][NABERTHERM_TYPE]
        fail = lambda code, status="rejected": self._error(instance_id, code, request_id, status)
        if cap.startswith("panel."):
            return self._panel_command(instance_id, cap, args, request_id)
        if cap in spec["controller_capabilities"] or cap in spec["evaluator_capabilities"]:
            return self._b510_command(instance_id, inst, cap, args, request_id)
        if cap in spec["declared_capabilities"]:
            return fail("UNSUPPORTED", "unsupported")
        if cap not in spec["capabilities"]:
            return fail("UNKNOWN_CAPABILITY")
        if set(args) != set(spec["capabilities"][cap]["input_schema"]["properties"]):
            return fail("INVALID_ARGUMENT")
        if cap == "power.set" and type(args["enabled"]) is not bool:
            return fail("INVALID_ARGUMENT")
        if cap == "thermal.set_temperature" and (not _number(args["temperature_K"]) or not host.ambient_temperature_K <= args["temperature_K"] <= NABERTHERM_MAX_K):
            return fail("INVALID_ARGUMENT")
        if cap == "thermal.configure_program":
            segments = args["segments"]
            if not isinstance(segments, list) or not 1 <= len(segments) <= 4:
                return fail("INVALID_ARGUMENT")
            for segment in segments:
                if not isinstance(segment, dict) or set(segment) != {"temperature_K", "ramp_rate_K_s", "hold_s"} or not all(_number(value) for value in segment.values()):
                    return fail("INVALID_ARGUMENT")
                if not (host.ambient_temperature_K <= segment["temperature_K"] <= NABERTHERM_MAX_K and 0.001 <= segment["ramp_rate_K_s"] <= 1 and 0 <= segment["hold_s"] <= 86400):
                    return fail("INVALID_ARGUMENT")
        if cap in ("thermal.set_temperature", "thermal.configure_program", "thermal.start_program"):
            if not old["power.enabled"]:
                return fail("PRECONDITION_FAILED")
            if old["program.running"] or old["b510.autotune"]["state"] == "running":
                return fail("BUSY")
        if cap == "thermal.start_program" and (not old["program.segments"] or not old["door.closed"]):
            return fail("PRECONDITION_FAILED")
        if cap.startswith("door.") and host.scene_adapter is None:
            return fail("UNSUPPORTED", "unsupported")
        state = copy.deepcopy(old)
        resume_phase = inst["resume_phase"]
        if cap == "power.set":
            state["power.enabled"] = args["enabled"]
            state["heater.enabled"] = args["enabled"] and state["door.closed"] and state["b510.outage"] is None
            if args["enabled"] != old["power.enabled"]:
                host._b510_power(state, args["enabled"], host.time_s)
            if not args["enabled"] and state["program.running"]:
                state["program.running"], state["program.phase"] = False, "stopped"
            result = {"enabled": args["enabled"]}
        elif cap == "thermal.set_temperature":
            state["thermal.target_k"] = float(args["temperature_K"])
            result = {"setpoint_K": state["thermal.target_k"]}
        elif cap.startswith("door."):
            closed = cap == "door.close"
            state["door.angle_rad"], state["door.closed"] = (0.0 if closed else NABERTHERM_DOOR_MAX_RAD), closed
            state["heater.enabled"] = state["power.enabled"] and closed and state["b510.outage"] is None
            if state["program.running"] and not closed:
                host._b510_warn(state, "W19")                                     # heating off, the program runs on (F p.51, p.123), every path
            if False:
                if not closed and state["program.phase"] != "paused_door":
                    resume_phase, state["program.phase"] = state["program.phase"], "paused_door"
                elif closed and state["program.phase"] == "paused_door":
                    state["program.phase"] = resume_phase
            result = {"angle_rad": state["door.angle_rad"], "closed": closed}
        elif cap == "thermal.configure_program":
            state["program.segments"] = copy.deepcopy(args["segments"])
            state["program.segment_index"], state["program.phase"], state["program.hold_elapsed_s"] = 0, "idle", 0.0
            result = {"segment_count": len(args["segments"])}
        elif cap == "thermal.start_program":
            state["program.running"], state["program.phase"], state["program.segment_index"], state["program.hold_elapsed_s"] = True, "ramp", 0, 0.0
            state["thermal.target_k"] = max(host.ambient_temperature_K, state["thermal.indicated_temperature_k"])
            result = {"running": True}
        else:
            if state["b510.run"] is not None:
                host._b510_end(state, host.time_s, aborted=True)
            state["program.running"], state["program.phase"] = False, "stopped"
            state["thermal.target_k"] = host.ambient_temperature_K
            result = {"running": False}
        updates = {sid: copy.deepcopy(value) for sid, value in state.items() if value != old[sid]}
        if host.scene_adapter:
            try:
                host.scene_adapter(instance_id, updates)
            except Exception:
                return fail("DEVICE_ERROR", "failed")
        inst["state"], inst["resume_phase"] = state, resume_phase
        inst["revision"] += 1
        if cap == "power.set" and args["enabled"] != old["power.enabled"]:
            inst["b510_epoch"] += 1
        return self._envelope(instance_id, request_id, result=result)

    def _panel_command(self, sid, cap, args, request_id):
        """panel.* commands: a gesture on the instance's B510 touch screen or power switch, a screen capture, or the evaluator frame."""
        host, panel = self.__host, _b510_panel()
        if cap not in panel["caps"]:
            return self._error(sid, "UNKNOWN_CAPABILITY", request_id)
        if not self._visible(panel["caps"][cap]):
            return self._error(sid, "ACCESS_DENIED", request_id)
        host._panel_caller = self                                             # a gesture acts as the session that makes it (access rules)
        try:
            result, error = _g02_panel_command(panel, host.panel(sid), cap, args, instance_id=sid, time_s=host.time_s, capture_dir=host.panel_capture_dir)
        finally:
            host._panel_caller = None
        return self._error(sid, error, request_id) if error else self._envelope(sid, request_id, result=result)

    def _b510_command(self, sid, inst, cap, args, request_id):
        """The B510 controller functions: schema, power, user level and lock checks, then the function on a candidate copy, one committed update."""
        host = self.__host
        fail = lambda code, status="rejected": self._error(sid, code, request_id, status)   # noqa: E731
        meta = host._metadata["asset_types"][NABERTHERM_TYPE]
        info = meta["controller_capabilities"].get(cap) or meta["evaluator_capabilities"][cap]
        if not self._visible(info):
            return fail("ACCESS_DENIED")
        if not _b510_valid(info["input_schema"], args):
            return fail("INVALID_ARGUMENT")
        old = inst["state"]
        if cap in _B510_CAPS:
            if not old["power.enabled"] or old["b510.outage"] is not None:
                return fail("PRECONDITION_FAILED")
            need = max(_B510_SETTINGS[k][0] for k in args) if cap == "settings.configure" else _B510_CAPS[cap][0]
            level = 0 if old["b510.user"] is None else _B510_LEVEL[old["b510.user"]]
            if level < need:
                return fail("PRECONDITION_FAILED")                               # the user level does not allow it (p.72-73): log in first
            if old["b510.locked"] and cap not in ("controller.unlock", "access.login"):
                return fail("PRECONDITION_FAILED")                               # controller locked (p.77)
        state = copy.deepcopy(old)
        result = host._b510_exec(state, cap, args, host.time_s)
        if isinstance(result, str):
            return fail(result)
        updates = {k: copy.deepcopy(v) for k, v in state.items() if v != old[k]}
        if updates and host.scene_adapter:
            try:
                host.scene_adapter(sid, updates)
            except Exception:
                return fail("DEVICE_ERROR", "failed")
        inst["state"] = state
        inst["revision"] += 1
        return self._envelope(sid, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if not self._instance(instance_id):
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slots = self.__host._metadata["asset_types"][NABERTHERM_TYPE]["state_slots"]
            if not isinstance(state_id, str) or state_id not in slots or not self._visible(slots[state_id]) or not slots[state_id]["write_via"]:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slots[state_id]["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return copy.deepcopy(self.__host._logs[self.__principal])


def nabertherm_calibration_report():
    assumptions = []
    for loss in (1.0, 2.0, 3.0):
        capacity = -loss * NABERTHERM_REFERENCE_S / math.log1p(-loss * (NABERTHERM_REFERENCE_K - NABERTHERM_AMBIENT_K) / NABERTHERM_POWER_W)
        assumptions.append({"assumed_closed_loss_W_K": loss, "fitted_effective_capacity_J_K": capacity,
            "predicted_time_to_1000C_s": -capacity / loss * math.log1p(-loss * 975 / 3400),
            "predicted_power_off_1000C_to_100C_s": capacity / loss * math.log(975 / 75)})
    host = NaberthermL9Runtime()
    host.create_instance("calibration_fixture")
    host.invoke(instance_id="calibration_fixture", capability_id="power.set", arguments={"enabled": True}, request_id="power")
    host.invoke(instance_id="calibration_fixture", capability_id="thermal.set_temperature", arguments={"temperature_K": NABERTHERM_MAX_K}, request_id="temperature")
    host.step(4500)
    actual = host.debug_snapshot()["instances"]["calibration_fixture"]["state"]["thermal.temperature_k"]
    return {"schema_version": "1.0.0", "catalog_id": "CHEM-185", "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source": NABERTHERM_SOURCE, "manufacturer_conditions": "Empty furnace, closed door, 230 V 1/N/PE; Tmax minus 100 K in approximately 75 min.",
        "model": "C*dT/dt=P-UA*(T-Ta); exact exponential for constant target/door; ideal cap at setpoint.",
        "calibration_equation": "C=-UA*t_ref/ln(1-UA*(T_ref-Ta)/P)",
        "assumed_initial_and_ambient_K": NABERTHERM_AMBIENT_K, "rated_power_W": NABERTHERM_POWER_W,
        "assumed_closed_loss_W_K": NABERTHERM_LOSS_W_K, "fitted_effective_capacity_J_K": NABERTHERM_CAPACITY_J_K,
        "target_at_4500s_K": NABERTHERM_REFERENCE_K, "runtime_at_4500s_K": actual, "error_K": actual - NABERTHERM_REFERENCE_K,
        "whole_instrument_mass_kg": 50.0, "whole_mass_used_as_component_capacity": False,
        "electrical_power_interpretation": "3400 W is catalogue connected load; treating it as maximum effective thermal input is a modeling assumption absorbed into the fitted capacity, not a measured heater efficiency.",
        "independent_catalogue_endpoint_comparison": {"source": NABERTHERM_MANUAL + "#page=25",
            "manufacturer_approximate_time_to_1100C_s": 5400.0,
            "predicted_time_to_1100C_s": -NABERTHERM_CAPACITY_J_K / NABERTHERM_LOSS_W_K * math.log1p(-NABERTHERM_LOSS_W_K * (NABERTHERM_MAX_K - NABERTHERM_AMBIENT_K) / NABERTHERM_POWER_W),
            "interpretation": "Additional endpoint from old 3.4kW manual table; approximate-condition comparison, not independent experimental validation."},
        "door_interlock_source": NABERTHERM_MANUAL + "#page=54",
        "sensitivity": assumptions,
        "evidence_status": "Conditional calibration to one catalogue heat-up point; not independent measured validation.",
        "uncertainty": ["Catalogue does not specify initial temperature or uncertainty of approximate 75 min.",
            "One time-temperature point cannot independently identify both effective capacity and loss coefficient.",
            "Loss is effective and linear: no separately calibrated radiation, insulation layers, chamber-air or crucible nodes.",
            "Cooling and open-door parameters have no manufacturer transient calibration.",
            "Whole-instrument 50 kg does not identify any component mass or heat capacity."]}


def validate_nabertherm_runtime(output_directory):
    test_path = Path(__file__).resolve().parents[1] / "tests/test_basic40_thermal.py"
    spec = importlib.util.spec_from_file_location("nabertherm_runtime_tests", test_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    report = {"schema_version": "1.0.0", "catalog_id": "CHEM-185", "recorded_at": datetime.now(timezone.utc).isoformat(),
        "method": "unittest of real NaberthermL9Runtime and NaberthermL9Session public APIs with trusted host step",
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "data_classification": "Synthetic fixtures; no private or real sample data.",
        "result": "passed" if result.wasSuccessful() else "failed", "tests_run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "log": stream.getvalue(),
        "layers": {"process": "passed" if result.wasSuccessful() else "failed", "host": "passed" if result.wasSuccessful() else "failed",
            "scene": "not_run", "robot": "not_run", "hardware": "not_run"},
        "limitations": ["Recording callbacks verify host synchronization only, not evaluated geometry.",
            "Thermal calibration fits one approximate catalogue point under explicit loss and ambient assumptions.",
            "API truth filtering tested; shared-process/filesystem security isolation not implemented."]}
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (("thermal_runtime_validation.json", report), ("thermal_calibration.json", nabertherm_calibration_report()),
            ("thermal_runtime_metadata.json", export_nabertherm_contract_metadata())):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", type=Path, metavar="OUTPUT_DIRECTORY")
    options = parser.parse_args()
    if options.validate:
        report = validate_nabertherm_runtime(options.validate)
        print(json.dumps({key: report[key] for key in ("result", "tests_run", "failures", "errors")}, indent=2))
        raise SystemExit(0 if report["result"] == "passed" else 1)
    parser.print_help()

# BEGIN CHEM-186 Carbolite TF1 runtime: append-only shared thermal module.
import copy as _tf1_copy
import hashlib as _tf1_hashlib
import json as _tf1_json
import math as _tf1_math
import threading as _tf1_threading

CARBOLITE_TF1_ASSET_TYPE = 'instrument.carbolite_tf1_12_60_300'
CARBOLITE_TF1_SOURCE_URL = 'https://www.carbolite.com/products/tube-furnace-range/universal-tube-furnaces/tf-range/'
CARBOLITE_TF1_PARAMETERS = {
    'heater_power_W': {'value': 1500.0, 'unit': 'W', 'evidence': 'public_document', 'source': CARBOLITE_TF1_SOURCE_URL + ' — TF1 12/60/300 technical table: maximum power 1500 W'},
    'maximum_temperature_K': {'value': 1473.15, 'unit': 'K', 'evidence': 'public_document', 'source': CARBOLITE_TF1_SOURCE_URL + ' — maximum temperature 1200 deg C'},
    'maximum_continuous_temperature_K': {'value': 1373.15, 'unit': 'K', 'evidence': 'public_document', 'source': CARBOLITE_TF1_SOURCE_URL + ' — continuous temperature 1100 deg C'},
    'reference_heatup_time_s': {'value': 2100.0, 'unit': 's', 'evidence': 'public_document', 'source': CARBOLITE_TF1_SOURCE_URL + ' — 35 min to 100 deg C below maximum, empty quartz work tube and insulation plugs'},
    'reference_heatup_temperature_K': {'value': 1373.15, 'unit': 'K', 'evidence': 'public_document', 'source': CARBOLITE_TF1_SOURCE_URL + ' — 1200 - 100 = 1100 deg C'},
    'reference_ambient_temperature_K': {'value': 293.15, 'unit': 'K', 'evidence': 'assumption', 'source': 'Assumed 20 deg C start; manufacturer heat-up table does not state starting ambient.'},
    'heat_loss_W_K': {'value': 0.5, 'unit': 'W/K', 'evidence': 'assumption', 'source': 'Assumed effective UA, giving 540 W loss at 1100 deg C and 20 deg C ambient (36% of rated power). One manufacturer heat-up point cannot identify both C and UA; no cooldown/hold-power calibration.'},
    'thermal_capacity_J_K': {'value': -0.5 * 2100.0 / _tf1_math.log(1.0 - 0.5 * 1080.0 / 1500.0), 'unit': 'J/K', 'evidence': 'assumption', 'source': 'Effective empty-system C = -UA*t/log(1-UA*(Tref-Tambient)/P), from 1500 W / 2100 s manufacturer heat-up point, assumed UA=0.5 W/K and ambient=293.15 K. This is not the measured heat capacity of any component.'},
    'work_tube_inner_diameter_m': {'value': None, 'unit': 'm', 'evidence': 'unknown', 'source': '60 mm is furnace bore / maximum work-tube outer diameter, not the work-tube inner diameter.'},
    'work_tube_mass_kg': {'value': None, 'unit': 'kg', 'evidence': 'unknown', 'source': 'Selected work-tube material, wall thickness and mass are unconfirmed.'},
    'end_seal_mass_kg': {'value': None, 'unit': 'kg', 'evidence': 'unknown', 'source': 'Individual end-seal component mass is unconfirmed.'},
    'gas_flow_rate_m3_s': {'value': None, 'unit': 'm^3/s', 'evidence': 'unknown', 'source': 'No flow controller, supply condition, conductance or flow measurement is configured.'},
    'gas_pressure_pa': {'value': None, 'unit': 'Pa', 'evidence': 'unknown', 'source': 'No absolute-pressure state solver or sensor is configured.'},
    'maximum_end_seal_withdrawal_m': {'value': 0.12, 'unit': 'm', 'evidence': 'assumption', 'source': 'Illustrative removable-part scene travel, not a manufacturer actuator stroke.'},
    'minimum_end_seal_clearance_m': {'value': 0.10, 'unit': 'm', 'evidence': 'assumption', 'source': 'Scene clearance rule before work-tube translation; not measured collision clearance.'},
    'maximum_work_tube_withdrawal_m': {'value': 0.8, 'unit': 'm', 'evidence': 'assumption', 'source': 'Illustrative work-tube scene travel along +X; not a manufacturer actuator stroke.'},
}


def _carbolite_tf1_number(value):
    try:
        return type(value) in (int, float) and _tf1_math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _carbolite_tf1_slot(unit, origin, visibility='agent', route=None, schema=None):
    return {'unit': unit, 'origin': origin, 'visibility': visibility,
            'access': 'read_write' if route else 'read_only',
            'write_via': route, 'value_schema': schema or {'type': 'number'},
            'read_binding': 'CarboliteTF1Session.read(instance_id=..., state_ids=[state_id])',
            'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None},
            'reference_conditions': None}


CARBOLITE_TF1_STATE_SLOTS = {
    'power.enabled': _carbolite_tf1_slot('1', 'setpoint', route={'capability_id': 'power.set', 'parameter': 'enabled', 'fixed_arguments': {}}, schema={'type': 'boolean'}),
    'thermal.target_k': _carbolite_tf1_slot('K', 'setpoint', route={'capability_id': 'thermal.set_temperature', 'parameter': 'temperature_K', 'fixed_arguments': {}}),
    'thermal.temperature_k': _carbolite_tf1_slot('K', 'process_truth', 'debug'),
    'thermal.indicated_temperature_k': _carbolite_tf1_slot('K', 'derived'),
    'thermal.heater_energy_j': _carbolite_tf1_slot('J', 'process_truth', 'debug'),
    'thermal.heat_loss_energy_j': _carbolite_tf1_slot('J', 'process_truth', 'debug'),
    'thermal.stored_energy_j': _carbolite_tf1_slot('J', 'process_truth', 'debug'),
    'end_seal.left.withdrawal_m': _carbolite_tf1_slot('m', 'scene', route={'capability_id': 'scene.set_end_seal', 'parameter': 'withdrawal_m', 'fixed_arguments': {'side': 'left'}}),
    'end_seal.right.withdrawal_m': _carbolite_tf1_slot('m', 'scene', route={'capability_id': 'scene.set_end_seal', 'parameter': 'withdrawal_m', 'fixed_arguments': {'side': 'right'}}),
    'work_tube.withdrawal_m': _carbolite_tf1_slot('m', 'scene', route={'capability_id': 'scene.set_work_tube', 'parameter': 'withdrawal_m', 'fixed_arguments': {}}),
    'gas.inlet.connected': _carbolite_tf1_slot('1', 'scene', route={'capability_id': 'scene.set_gas_connection', 'parameter': 'connected', 'fixed_arguments': {'port_id': 'gas_inlet'}}, schema={'type': 'boolean'}),
    'gas.outlet.connected': _carbolite_tf1_slot('1', 'scene', route={'capability_id': 'scene.set_gas_connection', 'parameter': 'connected', 'fixed_arguments': {'port_id': 'gas_outlet'}}, schema={'type': 'boolean'}),
    'gas.flow_rate_m3_s': _carbolite_tf1_slot('m^3/s', 'process_truth', 'debug', schema={'anyOf': [{'type': 'number'}, {'type': 'null'}]}),
    'gas.pressure_pa': _carbolite_tf1_slot('Pa', 'process_truth', 'debug', schema={'anyOf': [{'type': 'number'}, {'type': 'null'}]}),
}
CARBOLITE_TF1_STATE_SLOTS['gas.flow_rate_m3_s']['reference_conditions'] = 'Unspecified: no gas flow is calculated or measured; value remains null.'
CARBOLITE_TF1_STATE_SLOTS['gas.pressure_pa']['reference_conditions'] = 'Absolute pressure would be required; currently unavailable (null).'
for _carbolite_tf1_sid in ('thermal.heater_energy_j', 'thermal.heat_loss_energy_j'):
    CARBOLITE_TF1_STATE_SLOTS[_carbolite_tf1_sid]['sampling'] = {'mode': 'integral', 'time_domain': 'simulation', 'window': 'Accumulated since instance creation.'}


def _carbolite_tf1_effect(state_id, timing, note):
    return {'state_id': state_id, 'timing': timing, 'description_zh': note}


def carbolite_tf1_metadata(panel=True):
    """Type-only schema metadata, with no instance values or host credentials.  controller_capabilities: the 3016 functions behind the front
    panel; evaluator_capabilities: the sensor error model; panel_capabilities: the front-panel commands (panel.*)."""
    thermal_effects = [_carbolite_tf1_effect(sid, 'on_step', '宿主推进空载集中热模型；不模拟反应、样品、气流或实测传感器。') for sid in ('thermal.temperature_k', 'thermal.indicated_temperature_k', 'thermal.heater_energy_j', 'thermal.heat_loss_energy_j', 'thermal.stored_energy_j')]
    definitions = {
        'power.set': ({'enabled': {'type': 'boolean'}}, {'enabled': {'type': 'boolean'}}, '设置仿真供电；开机要求工作管完全归位。', {'enabled': True}, ['power.enabled'], ['power.enabled'], 'enabled=true requires work_tube.withdrawal_m == 0; a scene configuration rule, not a manufacturer safety interlock.'),
        'thermal.set_temperature': ({'temperature_K': {'type': 'number', 'minimum': 273.15, 'maximum': 1473.15, 'x-unit': 'K', 'description': 'Finite; actual minimum is host ambient. Above 1373.15 K exceeds manufacturer continuous rating; maximum-temperature dwell is not simulated.'}}, {'setpoint_K': {'type': 'number', 'x-unit': 'K'}}, '只写温度目标；实际温度与能量只在宿主 step 演化。', {'temperature_K': 1373.15}, ['thermal.target_k'], ['thermal.target_k'], 'power.enabled == true and ambient_temperature_K <= temperature_K <= 1473.15.'),
        'scene.set_end_seal': ({'side': {'type': 'string', 'enum': ['left', 'right']}, 'withdrawal_m': {'type': 'number', 'minimum': 0, 'maximum': 0.12, 'x-unit': 'm'}}, {'side': {'type': 'string', 'enum': ['left', 'right']}, 'withdrawal_m': {'type': 'number', 'x-unit': 'm'}}, '设置可拆端封示意轴向移位；左向-X、右向+X，无机器人或接触求解。', {'side': 'left', 'withdrawal_m': 0.12}, ['end_seal.left.withdrawal_m', 'end_seal.right.withdrawal_m'], ['end_seal.left.withdrawal_m', 'end_seal.right.withdrawal_m'], 'Requires scene backend; power off and corresponding gas port disconnected when changing position. If work tube is displaced, seal cannot move below 0.10 m. These are scene rules, not manufacturer interlocks.'),
        'scene.set_work_tube': ({'withdrawal_m': {'type': 'number', 'minimum': 0, 'maximum': 0.8, 'x-unit': 'm'}}, {'withdrawal_m': {'type': 'number', 'x-unit': 'm'}}, '设置工作管沿+X的示意拆卸移位；工作管内径未知。', {'withdrawal_m': 0.8}, ['work_tube.withdrawal_m'], ['work_tube.withdrawal_m'], 'Requires scene backend; power off, both seals withdrawn at least 0.10 m, and both gas ports disconnected when changing position. No hot handling, grasping or collision safety claim.'),
        'scene.set_gas_connection': ({'port_id': {'type': 'string', 'enum': ['gas_inlet', 'gas_outlet']}, 'connected': {'type': 'boolean'}}, {'port_id': {'type': 'string', 'enum': ['gas_inlet', 'gas_outlet']}, 'connected': {'type': 'boolean'}}, '记录气口连接标志；气压、流量、密封性、置换和气氛组成均未求解。', {'port_id': 'gas_inlet', 'connected': True}, ['gas.inlet.connected', 'gas.outlet.connected'], ['gas.inlet.connected', 'gas.outlet.connected'], 'Requires scene backend. Connecting requires power off, associated seal at zero withdrawal and work tube at zero withdrawal. Disconnect is always allowed; this records configuration, not a physical coupling operation.'),
    }
    capabilities = {}
    for name, (inputs, outputs, description, example, effects, observes, precondition) in definitions.items():
        capabilities[name] = {
            'availability': 'implemented', 'visibility': 'agent',
            'kind': 'simulation' if name.startswith('scene.') else 'device_command',
            'description_zh': description,
            'input_schema': {'type': 'object', 'properties': inputs, 'required': list(inputs), 'additionalProperties': False},
            'output_schema': {'type': 'object', 'properties': outputs, 'required': list(outputs), 'additionalProperties': False},
            'state_effects': [_carbolite_tf1_effect(sid, 'immediate', '只更新本次选择的状态槽；不相关侧保持不变。') for sid in effects] + (thermal_effects if name in ('power.set', 'thermal.set_temperature') else []),
            'observes': observes, 'example_arguments': [example],
            'preconditions': precondition,
            'binding': "basic40_runtime_thermal.CarboliteTF1Session.invoke(instance_id=..., capability_id='" + name + "', arguments=..., request_id=...)",
            'completion_criterion': 'Declared command value committed; synchronous scene callback returned where configured. Temperature settling is not command completion.',
            'retry_semantics': 'In-memory per-principal/instance/request ID deduplication; mismatched command returns REQUEST_CONFLICT. No restart persistence.',
        }
    return _tf1_extend_metadata({'protocol_version': '1.0.0', 'asset_type_id': CARBOLITE_TF1_ASSET_TYPE, 'asset_version': '1.1.0', 'catalog_id': 'CHEM-186', 'model': 'Carbolite Gero TF1 12/60/300',
            'state_slots': _tf1_copy.deepcopy(CARBOLITE_TF1_STATE_SLOTS), 'capabilities': capabilities, 'parameters': _tf1_copy.deepcopy(CARBOLITE_TF1_PARAMETERS),
            'limitations': ['Synthetic empty hot-zone lumped model, derived from the manufacturer 35-minute heat-up point with assumed 20 deg C ambient and UA; C and UA are not separately identifiable from that one point.',
                'Maximum rated temperature is 1473.15 K; continuous manufacturer rating is 1373.15 K. This model does not enforce dwell-time limits or model degradation above the continuous rating.',
                'Reference manufacturer heat-up configuration uses an empty quartz work tube and insulation plugs. Removal/seal position does not alter thermal coefficients; other configurations and cooldown are uncalibrated.',
                'No sample temperature gradients, chemical kinetics, purge, pressure, gas flow, oxygen concentration, vacuum integrity, robot operation, collision safety, or hardware driver.',
                'Scene sliding coordinates are an illustrative disassembly aid, not real motors or a furnace door hinge.',
                'Indicated temperature is an estimated ideal display rounded on a 0.1 deg C grid and exposed in K, not a measured sensor accuracy claim.',
                'Only trusted host step advances the shared TF1-world clock. Python API visibility filtering does not isolate Python introspection, shared memory or filesystem access; use a separate trusted host process for untrusted agents.']}, panel)


# ---- CHEM-186 front panel and the 3016 controller functions behind it (Carbolite 3016 controller manual MEN-3016_CTRL, 2026-10-18) ----
# Front panel (panel.press / panel.switch / panel.capture / panel.frame): the EPC3016-based controller with its 4-digit PV line, 5-character
# second line, beacons and the Page / Scroll / Down / Up keys, plus the instrument switch of the control box (panel_ui.json beside the
# contract), run by scripts/asset_runtime/_panel_ui.py on the capabilities below.  Controller functions behind the panel (also usable
# through the API): the single 24-segment program (Rate / Time / Dwell / Step / End, holdback, cycles, end type, run / hold / reset / advance,
# power-cycle recovery), autotune state machine, PV offset calibration (with the evaluator-only sensor error model), output high limit,
# loop-break detection and its alarm acknowledgement, demo mode, keylock, language of the scrolling text, temperature unit, home display,
# customer ID, communications settings.  The default state keeps the asset's original behaviour (no program, ideal hold at the set point).
CARBOLITE_TF1_ASSET_DIR = Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t1_thermal/carbolite_tf1'
CARBOLITE_TF1_SEGMENTS = 24                                                 # 3016: one program slot of 24 segments (manual p.4, p.20)
CARBOLITE_TF1_PROGRAM_STEP_S = 10.0                                         # program set point updated at least every 10 s (assumption)
CARBOLITE_TF1_SENSOR_ERROR = (25.0, 0.05)                                   # evaluator-set probe error bounds: offset K, relative gain
CARBOLITE_TF1_UNIT_S = {'sec': 1.0, 'min': 60.0, 'hour': 3600.0}
CARBOLITE_TF1_AUTOTUNE_MONITOR_S = 60.0                                     # 'moni' stage: one minute, set point adjustable (EPC3000 guide)
CARBOLITE_TF1_AUTOTUNE_LIMIT_S = 7200.0                                     # a stage longer than 2 h times out (manual p.34-35)
CARBOLITE_TF1_PID_TS = 0.25                                                 # controller sample period (s): 4 Hz input sampling of the 3000 series (assumption)
_TF1_FAULTS = ('S.brk',)   # faults the manuals document and the evaluator can inject: thermocouple open circuit (3016 manual p.13, TF1 manual p.72)


def _tf1_tuning(capacity, loss, power, filter_s, ts=CARBOLITE_TF1_PID_TS):
    """PID terms for the lumped plant C dT/dt = P - UA (T - Ta) seen through the first-order input filter FILT.T: the two-sample deadbeat band
    Pb0 = Pmax b / a (a = exp(-UA Ts / C), b = (1 - a) / UA) widened three times, ti = 2.5 FILT.T, td = 0.3125 FILT.T (no overshoot with the
    filter, found by simulation); without a filter the deadbeat terms (Pb0, a Ts, 0).  Returns {pb_K, ti_s, td_s}."""
    a = _tf1_math.exp(-loss * ts / capacity)
    pb0 = power * (1.0 - a) / loss / a
    if filter_s < 0.5:
        return {'pb_K': pb0, 'ti_s': a * ts, 'td_s': 0.0}
    return {'pb_K': 3.0 * pb0, 'ti_s': 2.5 * filter_s, 'td_s': 0.3125 * filter_s}


def _tf1_object(properties, optional=()):
    return {'type': 'object', 'properties': properties, 'required': [k for k in properties if k not in optional], 'additionalProperties': False}


def _tf1_num(unit, low=None, high=None, integer=False):
    schema = {'type': 'integer' if integer else 'number', 'x-unit': unit}
    if low is not None: schema['minimum'] = low
    if high is not None: schema['maximum'] = high
    return schema


def _tf1_enum(*values):
    return {'type': 'string', 'enum': list(values)}


_TF1_CONTROLLER = {'language': _tf1_enum('none', 'en', 'fr', 'de', 'it', 'es'), 'temperature_unit': _tf1_enum('C', 'F', 'K'),
                   'customer_id': _tf1_num('1', 0, 9999, integer=True), 'home_display': _tf1_enum('pv_sp', 'pv_time', 'pv', 'pv1', 'pv_segment'),
                   'keylock': {'type': 'boolean'}, 'demo': {'type': 'boolean'}, 'filter_s': _tf1_num('s', 0, 60)}
_TF1_LOOP = {'remote_local': _tf1_enum('local'), 'output_high_percent': _tf1_num('%', 0, 100), 'output_low_percent': _tf1_num('%', 0, 100),
             'rate_up_percent_s': _tf1_num('%/s', 0, 100), 'rate_down_percent_s': _tf1_num('%/s', 0, 100), 'band_1_K': _tf1_num('K', 0, 1500),
             'switch_hysteresis_K': _tf1_num('K', 0, 100), 'output_high_1_percent': _tf1_num('%', 0, 100), 'output_high_2_percent': _tf1_num('%', 0, 100),
             'loop_break_time_s': _tf1_num('s', 0, 99999), 'loop_break_delta_K': _tf1_num('K', 0, 100), 'tune_high_percent': _tf1_num('%', -100, 100),
             'tune_low_percent': _tf1_num('%', -100, 100)}
_TF1_OCTETS = {'type': 'array', 'minItems': 4, 'maxItems': 4, 'items': _tf1_num('1', 0, 255)}   # whole numbers (checked by the adapter)
_TF1_COMMS = {'ip_address': _TF1_OCTETS, 'subnet_mask': _TF1_OCTETS, 'gateway': _TF1_OCTETS,
              'watchdog_time_s': _tf1_num('s', 0, 3600), 'watchdog_action': _tf1_enum('manual', 'auto'), 'watchdog_recovery_s': _tf1_num('s', 0, 3600),
              'time_format': _tf1_enum('msec', 'sec', 'min', 'hour')}
_TF1_PROGRAM = {'holdback_style': _tf1_enum('program', 'segment'), 'holdback_type': _tf1_enum('off', 'low', 'high', 'band'), 'holdback_value_K': _tf1_num('K', 0, 1500),
                'ramp_units': _tf1_enum('sec', 'min', 'hour'), 'dwell_units': _tf1_enum('sec', 'min', 'hour'), 'cycles': _tf1_num('1', 0, 999, integer=True),
                'end_type': _tf1_enum('dwell', 'reset'), 'edit_access': _tf1_enum('lev1', 'lev2', 'lev3', 'conf'), 'recovery': _tf1_enum('ramp', 'reset', 'continue'),
                'rate_resolution': _tf1_num('1', 0, 4, integer=True), 'time_resolution': _tf1_enum('sec', 'min', 'hour')}
_TF1_SEGMENT = {'number': _tf1_num('1', 1, CARBOLITE_TF1_SEGMENTS, integer=True), 'type': _tf1_enum('rate', 'time', 'dwell', 'step', 'end'),
                'target_K': _tf1_num('K', 273.15, 1473.15), 'rate_per_unit_K': _tf1_num('K', 0.1, 9999), 'time_s': _tf1_num('s', 0, 1800000),
                'holdback_type': _tf1_enum('off', 'low', 'high', 'band'), 'holdback_value_K': _tf1_num('K', 0, 1500)}
_TF1_DEFAULTS = {
    'controller.settings': {'language': 'en', 'temperature_unit': 'C', 'customer_id': 0, 'home_display': 'pv_sp', 'keylock': False, 'demo': False, 'filter_s': 1.6},
    'loop.settings': {'remote_local': 'local', 'output_high_percent': 100.0, 'output_low_percent': 0.0, 'rate_up_percent_s': 0.0, 'rate_down_percent_s': 0.0,
                      'band_1_K': 0.0, 'switch_hysteresis_K': 0.0, 'output_high_1_percent': 100.0, 'output_high_2_percent': 100.0, 'loop_break_time_s': 0.0,
                      'loop_break_delta_K': 2.0, 'tune_high_percent': 100.0, 'tune_low_percent': -100.0},
    'comms.settings': {'ip_address': [192, 168, 111, 222], 'subnet_mask': [255, 255, 255, 0], 'gateway': [0, 0, 0, 0], 'watchdog_time_s': 0.0,
                       'watchdog_action': 'manual', 'watchdog_recovery_s': 0.0, 'time_format': 'sec'},
    'program.definition': {'holdback_style': 'program', 'holdback_type': 'off', 'holdback_value_K': 0.0, 'ramp_units': 'min', 'dwell_units': 'min',
                           'cycles': 1, 'end_type': 'reset', 'edit_access': 'lev2', 'recovery': 'continue', 'rate_resolution': 1, 'time_resolution': 'min',
                           'segments': [{'number': n, 'type': 'end', 'target_K': 273.15, 'rate_per_unit_K': 0.1, 'time_s': 0.0, 'holdback_type': 'off',
                                         'holdback_value_K': 0.0} for n in range(1, CARBOLITE_TF1_SEGMENTS + 1)]},
}


def _tf1_new_slot(unit, origin, meaning, schema, visibility='agent', route=None):
    slot = _carbolite_tf1_slot(unit, origin, visibility, route, schema)
    slot['meaning_zh'] = meaning
    slot['component_ids'] = ['controller']
    return slot


CARBOLITE_TF1_CONTROLLER_SLOTS = {
    'controller.output_percent': _tf1_new_slot('%', 'derived', '工作输出W.OUT：加热功率占额定功率的百分比（手册p.28）', _tf1_num('%', 0, 100)),
    'controller.settings': _tf1_new_slot('1', 'setpoint', '控制器设定：滚动文字语言LANG、温度单位t.unit、主页显示H.DISP、按键锁K.LOCK、演示模式DEMO、输入滤波FILT.T、客户编号CS.ID（手册p.28–42）',
                                         _tf1_object(_TF1_CONTROLLER)),
    'loop.settings': _tf1_new_slot('1', 'setpoint', '控制回路设定：OUT.HI输出上限（限制加热功率）、OUT.LO、OP.UP/OP.DWN、PID切换BND.1/gs.hys/OP.HI/OP.HI2、回路断线L.BRK.T/L.BRK.D、自整定T.HI/T.LO（手册p.33–36）',
                                   _tf1_object(_TF1_LOOP)),
    'loop.break': _tf1_new_slot('1', 'derived', '回路断线L.BRK：输出饱和且PV在2×L.BRK.T内升温不足L.BRK.D（手册p.36）', {'type': 'boolean'}),
    'loop.pid_sets': _tf1_new_slot('1', 'setpoint', 'PID参数组1与2（比例带pb K、积分时间ti s、微分时间td s；菜单不显示，由自整定写入当前组，手册p.35、p.45–46）；出厂值按本资产热模型整定',
                                   {'type': 'object'}),
    'loop.pid_out': _tf1_new_slot('1', 'derived', 'PID内部状态：当前组SET、P/I/D份额、输出%、滤波后的读数、输出限值（采样周期0.25 s）', {'type': 'object'}, 'debug'),
    'autotune.state': _tf1_new_slot('1', 'derived', '自整定：Tune开关、T.STAT状态、STAGE阶段与STG.T阶段时间（手册p.34–35、p.45–46）',
                                    _tf1_object({'enabled': {'type': 'boolean'}, 'status': _tf1_enum('rdy', 'run', 'done', 'abort', 'timeout'),
                                                 'stage': _tf1_enum('idle', 'moni', 'hi', 'lo', 'pd', 'anls'), 'stage_time_s': _tf1_num('s', 0)})),
    'sensor.pv_offset_k': _tf1_new_slot('K', 'setpoint', 'PV偏移校准PV.OFS（单点校准，手册p.43–44）：显示与控制都用修正后的读数',
                                        _tf1_num('K', -100, 100), route={'capability_id': 'sensor.calibrate_offset', 'parameter': 'offset_K', 'fixed_arguments': {}}),
    'sensor.offset_error_k': _tf1_new_slot('K', 'process_truth', '热电偶系统误差的偏置（评测者设定，默认0即理想）', _tf1_num('K'), 'debug'),
    'sensor.gain_error': _tf1_new_slot('1', 'process_truth', '热电偶系统误差的相对增益误差（评测者设定，默认0即理想）', _tf1_num('1'), 'debug'),
    'program.definition': _tf1_new_slot('1', 'setpoint', '程序（单程序24段，手册p.18–24、p.36–37）：保持方式、类型与值、升温/保温单位、循环次数、结束方式、编辑权限、断电恢复、分辨率与各段参数',
                                        _tf1_object({**_TF1_PROGRAM, 'segments': {'type': 'array', 'items': _tf1_object(_TF1_SEGMENT)}})),
    'program.status': _tf1_new_slot('1', 'derived', '程序运行状态：P.MODE、当前段、段类型、工作设定点、目标、速率、段剩余时间、总剩余时间、剩余循环（手册p.16–18）',
                                    _tf1_object({'mode': _tf1_enum('reset', 'run', 'hold', 'end'), 'segment': _tf1_num('1', 0, CARBOLITE_TF1_SEGMENTS, integer=True),
                                                 'segment_type': _tf1_enum('none', 'rate', 'time', 'dwell', 'step', 'end'), 'working_setpoint_K': _tf1_num('K'),
                                                 'target_K': _tf1_num('K'), 'rate_per_unit_K': _tf1_num('K'), 'segment_time_left_s': _tf1_num('s'),
                                                 'time_left_s': _tf1_num('s'), 'cycles_left': _tf1_num('1', 0, integer=True), 'holdback': {'type': 'boolean'},
                                                 'interrupted': {'type': 'boolean'}})),
    'comms.settings': _tf1_new_slot('1', 'setpoint', '以太网与看门狗设定（手册p.39–41）；仿真不建立网络连接', _tf1_object(_TF1_COMMS)),
    'sensor.break': _tf1_new_slot('1', 'derived', '传感器断线输出SB.OUT（手册p.13、p.33）：热电偶开路时为真，PV显示S.brk/bAd交替、读数取高点值（1200 °C）、加热停止（强制手动，输出回退值0 %），'
                                  '程序按RECOV复位或保持；断线只能由评测者fault.inject注入', {'type': 'boolean'}),
    'alarm.state': _tf1_new_slot('1', 'derived', '报警：active条件成立的报警（loop_break），acknowledged已确认（铃图标常亮而不闪，手册p.25–26）',
                                 _tf1_object({'active': {'type': 'array', 'items': {'type': 'string'}}, 'acknowledged': {'type': 'array', 'items': {'type': 'string'}}})),
}
CARBOLITE_TF1_STATE_SLOTS.update(CARBOLITE_TF1_CONTROLLER_SLOTS)
_TF1_CAPS = {   # name: kind, input properties, optional ('*': all, at least one), output properties, observes, summary
    'controller.configure': ('device_command', _TF1_CONTROLLER, '*', _TF1_CONTROLLER, ['controller.settings'],
                             '控制器设定（LANG、t.unit、H.DISP、K.LOCK、DEMO、FILT.T、CS.ID，手册p.30、p.33–36、p.41–42）：K.LOCK开启后面板按键全部失效，只能经本API（代替iTools）关闭'),
    'loop.configure': ('device_command', _TF1_LOOP, '*', _TF1_LOOP, ['loop.settings'],
                       '控制回路参数（LOOP列表，手册p.33–36）：OUT.HI/OUT.LO与当前组的OP.HI/OP.HI2限制PID输出，OP.UP/OP.DWN限制输出变化率（%/s，0=不限），BND.1/gs.hys按读数切换PID组1/2，T.HI/T.LO为自整定阶跃输出'),
    'comms.configure': ('device_command', _TF1_COMMS, '*', _TF1_COMMS, ['comms.settings'], '通讯设定（Comm列表，手册p.39–41）：IP、子网、网关、看门狗、时间格式；仿真无网络'),
    'sensor.calibrate_offset': ('device_command', {'offset_K': _tf1_num('K', -100, 100)}, (), {'offset_K': _tf1_num('K')}, ['sensor.pv_offset_k', 'thermal.indicated_temperature_k'],
                                'PV偏移单点校准PV.OFS（手册p.43–44）：显示温度=测量+偏移，控制同样使用修正读数；建议±25 °C以内'),
    'sensor.reset_calibration': ('device_command', {}, (), {'offset_K': _tf1_num('K')}, ['sensor.pv_offset_k', 'thermal.indicated_temperature_k'], '清除PV偏移（PV.OFS恢复默认0.0，手册p.32）'),
    'autotune.set': ('device_command', {'enabled': {'type': 'boolean'}}, (), {'enabled': {'type': 'boolean'}}, ['autotune.state'],
                     '自整定开关Tune（手册p.34、p.45–46）：开启后监视1 min（可调设定点），随后加热到设定点并分析，完成显示donE；中途关闭为Abor，单阶段超2 h为tOut'),
    'program.configure': ('device_command', _TF1_PROGRAM, '*', _TF1_PROGRAM, ['program.definition'],
                          '程序参数（P.SEt与CONF PROg列表，手册p.19–20、p.36–37）：HB.STY、HB.TYP、HB.VAL、RAMP.U、DWEL.U、P.CYC（0=cont连续）、P.END、E.accs、RECOV、R.RESN、RESN'),
    'program.set_segment': ('device_command', _TF1_SEGMENT, ('type', 'target_K', 'rate_per_unit_K', 'time_s', 'holdback_type', 'holdback_value_K'),
                            {'number': _tf1_num('1', integer=True), 'type': _tf1_enum('rate', 'time', 'dwell', 'step', 'end')}, ['program.definition'],
                            '编辑一个程序段（手册p.20–22）：S.TYPE、TSP、R.RATE（每升温单位）、R.TIME或DUR（秒）、段保持方式；ENd之后的段不可达'),
    'program.set_mode': ('device_command', {'mode': _tf1_enum('reset', 'run', 'hold')}, (), {'mode': _tf1_enum('reset', 'run', 'hold', 'end')}, ['program.status', 'thermal.target_k'],
                         'P.MODE运行/保持/复位（手册p.14–16）：运行从当前PV开始；复位回到基本控制，设定点恢复为程序前的值；需上电'),
    'program.advance': ('device_command', {}, (), {'segment': _tf1_num('1', integer=True)}, ['program.status'], 'P.ADVN强制进入下一段（手册p.18）；需程序运行或保持'),
    'alarm.acknowledge': ('device_command', {}, (), {'active': {'type': 'array', 'items': {'type': 'string'}}}, ['alarm.state'],
                          '确认报警（ACK或Page+Scroll，手册p.25–26）：铃图标停止闪烁，条件仍成立时保持报警状态'),
}


def _tf1_capability(name, kind, props, optional, output, observes, summary, visibility='agent'):
    pre = [{'description_zh': '控制器已上电（仪器开关开）', 'state_ids': ['power.enabled'], 'enforced_by': 'adapter', 'check': 'power.enabled'}]
    return {'availability': 'implemented', 'visibility': visibility, 'kind': kind, 'description_zh': summary, 'component_ids': ['controller'],
            'input_schema': _tf1_object(_tf1_copy.deepcopy(props), tuple(props) if optional == '*' else optional),
            'output_schema': _tf1_object(_tf1_copy.deepcopy(output), tuple(output) if optional == '*' else ()),
            'state_effects': [_carbolite_tf1_effect(sid, 'immediate', summary) for sid in observes], 'observes': list(observes),
            'example_arguments': [{}], 'preconditions': pre if visibility == 'agent' else [],
            'binding': "basic40_runtime_thermal.CarboliteTF1Session.invoke(instance_id=..., capability_id='" + name + "', arguments=..., request_id=...)",
            'completion_criterion': 'Declared command value committed; temperature settling is not command completion.',
            'retry_semantics': 'In-memory per-principal/instance/request ID deduplication; mismatched command returns REQUEST_CONFLICT. No restart persistence.'}


def _tf1_panel():
    caps = set(carbolite_tf1_metadata(panel=False)['capabilities']) | set(_TF1_CAPS)
    return _g02_panel('carbolite_tf1', CARBOLITE_TF1_ASSET_DIR, caps)


def _tf1_extend_metadata(meta, panel):
    """The 3016 controller commands, the evaluator-only sensor error model and the panel commands, beside the original five commands."""
    examples = {'controller.configure': {'language': 'en'}, 'loop.configure': {'output_high_percent': 100.0}, 'comms.configure': {'watchdog_action': 'manual'},
                'sensor.calibrate_offset': {'offset_K': -5.0}, 'sensor.reset_calibration': {}, 'autotune.set': {'enabled': False},
                'program.configure': {'ramp_units': 'min'}, 'program.set_segment': {'number': 1, 'type': 'rate', 'target_K': 673.15, 'rate_per_unit_K': 5.0},
                'program.set_mode': {'mode': 'run'}, 'program.advance': {}, 'alarm.acknowledge': {}}
    controller = {}
    for name, (kind, props, optional, output, observes, summary) in _TF1_CAPS.items():
        controller[name] = _tf1_capability(name, kind, props, optional, output, observes, summary)
        controller[name]['example_arguments'] = [examples[name]]
    evaluator = {'sensor.configure_error': _tf1_capability('sensor.configure_error', 'simulation',
                                                           {'offset_K': _tf1_num('K', -CARBOLITE_TF1_SENSOR_ERROR[0], CARBOLITE_TF1_SENSOR_ERROR[0]),
                                                            'gain': _tf1_num('1', -CARBOLITE_TF1_SENSOR_ERROR[1], CARBOLITE_TF1_SENSOR_ERROR[1])}, (),
                                                           {'offset_K': _tf1_num('K'), 'gain': _tf1_num('1')}, ['sensor.offset_error_k', 'sensor.gain_error', 'thermal.indicated_temperature_k'],
                                                           '评测者：热电偶系统误差（读数 = T + 偏置 + 增益误差×T(°C) + PV.OFS；默认0即理想；真实测量误差留给后续“校准/仿真与现实差距”任务）',
                                                           visibility='evaluator')}
    evaluator['sensor.configure_error']['example_arguments'] = [{'offset_K': 3.0, 'gain': 0.001}]
    evaluator['fault.inject'] = _tf1_capability('fault.inject', 'simulation', {'code': _tf1_enum(*_TF1_FAULTS)}, (), {'code': _tf1_enum(*_TF1_FAULTS)},
                                                ['sensor.break', 'thermal.indicated_temperature_k', 'program.status', 'autotune.state'],
                                                '评测者：注入手册记载的故障。S.brk热电偶断线（3016手册p.13、EPC3000指南p.287）：SB.OUT=On，PV显示S.brk/bAd交替、第二行滚动INPUT SENSOR BROKEN，'
                                                '读数取高点值1200 °C，回路强制手动、输出回退值0 %（不再加热），自整定中止，运行中的程序在RECOV=rSEt时复位、否则保持', visibility='evaluator')
    evaluator['fault.inject']['example_arguments'] = [{'code': 'S.brk'}]
    evaluator['fault.clear'] = _tf1_capability('fault.clear', 'simulation', {}, (), {'cleared': {'type': 'array', 'items': {'type': 'string'}}},
                                               ['sensor.break', 'thermal.indicated_temperature_k', 'program.status'],
                                               '评测者：排除注入的故障（相当于更换热电偶或修复接线，TF1手册p.72故障分析）；读数恢复，因断线而保持的程序按RECOV恢复（ramp从当前读数按段速率、cont继续）',
                                               visibility='evaluator')
    panel_caps = {}
    if panel:
        p = _tf1_panel()
        called = p['engine'].spec_capabilities(p['spec'])
        caps = {**meta['capabilities'], **controller}
        effects = list({e['state_id']: dict(e, description_zh='面板动作调用的能力的效果：' + e['description_zh']) for c in called for e in caps[c]['state_effects']
                        if e['timing'] == 'immediate'}.values())
        panel_caps = _g02_panel_capabilities(p, "basic40_runtime_thermal.CarboliteTF1Session.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)", effects)
    meta.update(controller_capabilities=controller, evaluator_capabilities=evaluator, panel_capabilities=panel_caps, asset_version='1.3.0')   # 1.3.0: fault.inject / fault.clear (sync fixes 2026-10-19)
    meta['limitations'] = meta['limitations'] + [
        '3016 controller functions behind the front panel: the 24-segment program with holdback, cycles and power-cycle recovery sets the working set point; '
        'the PV offset and the evaluator-only sensor error act on the displayed and controlled reading; a discrete PID (0.25 s samples on the host clock, held '
        'between samples) drives the heater power through the input filter FILT.T, the two PID sets with gain scheduling (BND.1, gs.hys), the output limits '
        '(OUT.HI, OUT.LO, OP.HI, OP.HI2) and output rate limits (OP.UP, OP.DWN); autotune applies T.HI / T.LO steps, identifies C and UA from the responses and '
        'writes the terms of the active set (rule of _tf1_tuning); the PID terms themselves are not on the menu (factory values tuned for this lumped model); '
        'loop break is detected from saturated output and PV rise.',
        'Over-temperature protection, alarm relays, event outputs, RS485 and the cascade probe are options not in this configuration (so a sensor break shows no alarm '
        'beacon).',
        'Sensor break (thermocouple open circuit) occurs only by the evaluator fault.inject S.brk and ends with fault.clear: SB.OUT On, PV alternating S.brk / bAd with the '
        'scrolling INPUT SENSOR BROKEN (English in every LANG: the manuals give no translation), reading = the high point 1200 C, forced manual with the fallback output 0 %, '
        'autotune aborted, a running program reset (RECOV rSEt) or held and recovered by RECOV after the repair; detection is immediate (the guide gives at most 3 s).']
    return meta


_TF1_IDLE_STATUS = {'mode': 'reset', 'segment': 0, 'segment_type': 'none', 'working_setpoint_K': 0.0, 'target_K': 0.0, 'rate_per_unit_K': 0.0,
                    'segment_time_left_s': 0.0, 'time_left_s': 0.0, 'cycles_left': 0, 'holdback': False, 'interrupted': False}
_TF1_MESSAGES = {   # scrolling texts of the second line (manual p.14-15, p.45; LOOP DEMO MODE from the EPC3000 guide), in the LANG languages
    'running': {'en': 'PROGRAM RUNNING', 'fr': 'PROGRAMME EN COURS', 'de': 'PROGRAMM LAEUFT', 'it': 'PROGRAMMA IN CORSO', 'es': 'PROGRAMA EN MARCHA'},
    'hold': {'en': 'PROGRAM HOLD', 'fr': 'PROGRAMME EN PAUSE', 'de': 'PROGRAMM HALT', 'it': 'PROGRAMMA IN PAUSA', 'es': 'PROGRAMA EN PAUSA'},
    'autotune': {'en': 'AUTOTUNE ACTIVE', 'fr': 'AUTOREGLAGE ACTIF', 'de': 'AUTOTUNING AKTIV', 'it': 'AUTOTUNING ATTIVO', 'es': 'AUTOAJUSTE ACTIVO'},
    'demo': {'en': 'LOOP DEMO MODE', 'fr': 'MODE DEMO BOUCLE', 'de': 'REGELKREIS DEMO', 'it': 'MODO DEMO ANELLO', 'es': 'MODO DEMO LAZO'},
    'sbrk': {'en': 'INPUT SENSOR BROKEN'}}   # EPC3000 guide p.287 default message; no translation in the manuals: English in every LANG


class _CarboliteTF1Controller:
    """The 3016 controller logic of one host (mixed into CarboliteTF1Runtime): reading model, program, autotune, loop break, panel host."""

    def _tf1_reading(self, state, temperature):
        """What the controller reads and shows: T + evaluator error (offset, gain x T in degC) + the stored PV offset; with a sensor break the
        controller switches to the high point, the maximum temperature of the product (3016 manual p.13)."""
        if state.get('sensor.break'):
            return CARBOLITE_TF1_PARAMETERS['maximum_temperature_K']['value']
        return temperature + state['sensor.offset_error_k'] + state['sensor.gain_error'] * (temperature - 273.15) + state['sensor.pv_offset_k']

    def _tf1_sbrk_update(self, iid):
        """Sensor break and the programmer (EPC3000 guide p.231 'Sensor Break Recovery'): with a break a running program is reset (RECOV rSEt) or
        held, and the autotune aborts (p.265); after the repair a program held by the break applies RECOV (ramp: from the current reading at the
        segment rate; cont: on from its set point) and runs again."""
        state, c = self._instances[iid]['state'], self._tf1[iid]
        ps = state['program.status']
        if not state['power.enabled'] or ps['interrupted']:
            return
        recovery = state['program.definition']['recovery']
        if state['sensor.break']:
            if state['autotune.state']['enabled']:
                state['autotune.state'] = dict(state['autotune.state'], enabled=False, status='abort', stage='idle', stage_time_s=0.0)
            if ps['mode'] == 'run':
                if recovery == 'reset':
                    self._tf1_reset(iid)
                else:
                    state['program.status'] = dict(ps, mode='hold')
                    c['sbrk_hold'] = True
        elif c.get('sbrk_hold'):
            c['sbrk_hold'] = False
            if ps['mode'] == 'hold':
                ps = dict(ps, mode='run')
                if recovery == 'ramp':
                    ps['working_setpoint_K'] = c['segment_start_K'] = self._tf1_reading(state, state['thermal.temperature_k'])
                    c['segment_elapsed_s'] = 0.0
                state['program.status'] = ps
                state['thermal.target_k'] = ps['working_setpoint_K']
                self._tf1_times(iid)

    def _tf1_true_target(self, state, setpoint):
        """The furnace temperature at which the reading equals the set point (the controller holds its reading at the set point)."""
        g = state['sensor.gain_error']
        return (setpoint - state['sensor.offset_error_k'] - state['sensor.pv_offset_k'] + 273.15 * g) / (1.0 + g)

    def _tf1_power(self, state):
        if not state['power.enabled'] or state['controller.settings']['demo']:
            return 0.0
        return CARBOLITE_TF1_PARAMETERS['heater_power_W']['value'] * state['loop.settings']['output_high_percent'] / 100.0

    def _tf1_hb(self, definition, segment):
        if definition['holdback_style'] == 'segment':
            return segment['holdback_type'], segment['holdback_value_K']
        return definition['holdback_type'], definition['holdback_value_K']

    def _tf1_duration(self, definition, segment, start):
        """Length of a segment from its start set point (rate segments: |target - start| / rate)."""
        if segment['type'] == 'rate':
            rate = segment['rate_per_unit_K'] / CARBOLITE_TF1_UNIT_S[definition['ramp_units']]
            return abs(segment['target_K'] - start) / rate if rate > 0 else 0.0
        if segment['type'] in ('time', 'dwell'):
            return segment['time_s']
        return 0.0

    def _tf1_setpoint_at(self, segment, start, elapsed, duration):
        if segment['type'] == 'dwell':
            return start
        if duration <= 0:
            return segment['target_K']
        return start + (segment['target_K'] - start) * min(1.0, elapsed / duration)

    def _tf1_enter(self, iid, number):
        inst = self._instances[iid]
        """Start segment number (Step segments apply at once, End finishes the cycle or the program)."""
        state, c = inst['state'], self._tf1[iid]
        ps, definition = dict(state['program.status']), state['program.definition']
        while True:
            if number > CARBOLITE_TF1_SEGMENTS or definition['segments'][number - 1]['type'] == 'end':
                if definition['cycles'] == 0 or ps['cycles_left'] > 1:            # P.CYC: cont, or cycles left: run again from segment 1
                    ps['cycles_left'] = ps['cycles_left'] - 1 if definition['cycles'] else 0
                    number = 1
                    if definition['segments'][0]['type'] == 'end':
                        break
                    continue
                if definition['end_type'] == 'dwell':                             # dwell at the last set point until reset
                    ps.update(mode='end', segment=number if number <= CARBOLITE_TF1_SEGMENTS else CARBOLITE_TF1_SEGMENTS, segment_type='end',
                              segment_time_left_s=0.0, time_left_s=0.0, cycles_left=0)
                    state['program.status'] = ps
                    return
                state['program.status'] = ps
                self._tf1_reset(iid)
                return
            segment = definition['segments'][number - 1]
            if segment['type'] == 'step':
                ps['working_setpoint_K'] = segment['target_K']
                number += 1
                continue
            break
        segment = definition['segments'][number - 1] if number <= CARBOLITE_TF1_SEGMENTS else None
        if segment is None or segment['type'] == 'end':
            state['program.status'] = ps
            self._tf1_reset(iid)
            return
        c['segment_start_K'], c['segment_elapsed_s'] = ps['working_setpoint_K'], 0.0
        ps.update(segment=number, segment_type=segment['type'], target_K=segment['target_K'] if segment['type'] != 'dwell' else ps['working_setpoint_K'],
                  rate_per_unit_K=segment['rate_per_unit_K'] if segment['type'] == 'rate' else 0.0)
        state['program.status'] = ps
        self._tf1_times(iid)

    def _tf1_times(self, iid):
        inst = self._instances[iid]
        """Segment and program time left (without the effect of holdback, as P.TIM.L, manual p.17)."""
        state, c = inst['state'], self._tf1[iid]
        ps, definition = dict(state['program.status']), state['program.definition']
        if ps['mode'] not in ('run', 'hold') or not ps['segment']:
            return
        segment = definition['segments'][ps['segment'] - 1]
        seg_left = max(0.0, self._tf1_duration(definition, segment, c['segment_start_K']) - c['segment_elapsed_s'])
        total, sp = seg_left, segment['target_K'] if segment['type'] in ('rate', 'time') else c['segment_start_K']
        for nxt in definition['segments'][ps['segment']:]:
            if nxt['type'] == 'end':
                break
            total += self._tf1_duration(definition, nxt, sp)
            sp = nxt['target_K'] if nxt['type'] != 'dwell' else sp
        ps.update(segment_time_left_s=seg_left, time_left_s=total)
        state['program.status'] = ps

    def _tf1_reset(self, iid):
        inst = self._instances[iid]
        state, c = inst['state'], self._tf1[iid]
        if state['program.status']['mode'] != 'reset' and c['pre_target_K'] is not None:
            state['thermal.target_k'] = c['pre_target_K']
        state['program.status'] = dict(_TF1_IDLE_STATUS, working_setpoint_K=state['thermal.target_k'], target_K=state['thermal.target_k'])
        c['pre_target_K'] = None
        c['sbrk_hold'] = False

    def _tf1_program_run(self, iid, h):
        inst = self._instances[iid]
        """One sub-step of a running program: holdback freezes the program time, the working set point follows the segment."""
        state, c = inst['state'], self._tf1[iid]
        ps, definition = dict(state['program.status']), state['program.definition']
        if ps['mode'] != 'run' or ps['interrupted']:
            return h
        segment = definition['segments'][ps['segment'] - 1]
        duration = self._tf1_duration(definition, segment, c['segment_start_K'])
        hb_type, hb_value = self._tf1_hb(definition, segment)
        deviation = ps['working_setpoint_K'] - self._tf1_reading(state, state['thermal.temperature_k'])
        held = (hb_type in ('low', 'band') and deviation > hb_value) or (hb_type in ('high', 'band') and -deviation > hb_value)
        ps['holdback'] = held
        if not held:
            h = min(h, max(duration - c['segment_elapsed_s'], 1e-9))
            c['segment_elapsed_s'] += h
            ps['working_setpoint_K'] = self._tf1_setpoint_at(segment, c['segment_start_K'], c['segment_elapsed_s'], duration)
        state['program.status'] = ps
        state['thermal.target_k'] = ps['working_setpoint_K']
        return h

    def _tf1_after_program(self, iid):
        inst = self._instances[iid]
        state, c = inst['state'], self._tf1[iid]
        ps = state['program.status']
        if ps['mode'] != 'run' or ps['interrupted']:
            return
        segment = state['program.definition']['segments'][ps['segment'] - 1]
        if c['segment_elapsed_s'] >= self._tf1_duration(state['program.definition'], segment, c['segment_start_K']) - 1e-9:
            self._tf1_enter(iid, ps['segment'] + 1)
            state['thermal.target_k'] = state['program.status']['working_setpoint_K'] if state['program.status']['mode'] != 'reset' else state['thermal.target_k']
        self._tf1_times(iid)

    def _tf1_autotune(self, iid, h):
        """Autotune stages (manual p.45-46): moni (1 min), hi (output T.HI until 1 K below the set point), lo (output T.LO for 1 min), pd (control),
        anls (10 s): the hi and lo step responses identify the plant (cold-junction temperature Ta, rise in t1 from T0 to T1, fall in t_lo to T2:
        tau = t_lo / ln((T1 - Ta) / (T2 - Ta)), Te = (T1 - T0 e^(-t1/tau)) / (1 - e^(-t1/tau)), UA = P_hi / (Te - Ta), C = tau UA) and the active PID
        set gets _tf1_tuning."""
        inst, c = self._instances[iid], self._tf1[iid]
        state = inst['state']
        tune = dict(state['autotune.state'])
        if not tune['enabled'] or not state['power.enabled']:
            return
        tune['stage_time_s'] += h
        reading, sp = self._tf1_reading(state, state['thermal.temperature_k']), state['thermal.target_k']
        ident = c.setdefault('tune_id', {})
        nxt = None
        if tune['stage'] == 'moni' and tune['stage_time_s'] >= CARBOLITE_TF1_AUTOTUNE_MONITOR_S - 1e-9:
            nxt = 'hi' if reading < sp - 1.0 else 'pd'
        elif tune['stage'] == 'hi' and reading >= sp - 1.0:
            nxt = 'lo'
        elif tune['stage'] == 'lo' and tune['stage_time_s'] >= 60.0 - 1e-9:
            nxt = 'pd'
        elif tune['stage'] == 'pd' and abs(reading - sp) <= 0.5:
            nxt = 'anls'
        elif tune['stage'] == 'anls' and tune['stage_time_s'] >= 10.0 - 1e-9:
            tune.update(enabled=False, status='done', stage='idle', stage_time_s=0.0)
            terms = self._tf1_identify(state, ident)
            if terms is not None:
                key = 'set%d' % state['loop.pid_out']['set']
                state['loop.pid_sets'] = dict(state['loop.pid_sets'], **{key: terms})
            c['tune_id'] = {}
        if nxt:
            if nxt == 'hi':
                ident.update(ta=self.ambient_temperature_K, r0=reading, t_hi=0.0)       # ta: the cold-junction (terminal) temperature
            elif nxt == 'lo' and 'ta' in ident:
                ident.update(t_hi=tune['stage_time_s'], r1=reading, p_hi=self._tf1_rated() * min(max(state['loop.settings']['tune_high_percent'], 0.0), state['loop.settings']['output_high_percent']) / 100.0)
            elif nxt == 'pd' and 'r1' in ident and tune['stage'] == 'lo':
                ident.update(t_lo=tune['stage_time_s'], r2=reading, p_lo=self._tf1_rated() * min(max(state['loop.settings']['tune_low_percent'], 0.0), state['loop.settings']['output_high_percent']) / 100.0)
            tune.update(stage=nxt, stage_time_s=0.0)
        elif tune['enabled'] and tune['stage_time_s'] > CARBOLITE_TF1_AUTOTUNE_LIMIT_S:
            tune.update(enabled=False, status='timeout', stage='idle', stage_time_s=0.0)
        state['autotune.state'] = tune

    @staticmethod
    def _tf1_rated():
        return CARBOLITE_TF1_PARAMETERS['heater_power_W']['value']

    def _tf1_identify(self, state, ident):
        """PID terms from the recorded step responses (None when the autotune went straight to pd or the values are implausible)."""
        try:
            ta, r0, t1, r1, t_lo, r2, p_hi = ident['ta'], ident['r0'], ident['t_hi'], ident['r1'], ident['t_lo'], ident['r2'], ident['p_hi']
            if ident.get('p_lo', 0.0) > 0 or p_hi <= 0:
                return None
            tau = t_lo / _tf1_math.log((r1 - ta) / (r2 - ta))
            k = _tf1_math.exp(-t1 / tau)
            te = (r1 - r0 * k) / (1.0 - k)
            ua = p_hi / (te - ta)
        except (KeyError, ValueError, ZeroDivisionError):
            return None
        if not (tau > 0 and ua > 0 and te > r1):
            return None
        return _tf1_tuning(tau * ua, ua, self._tf1_rated(), state['controller.settings']['filter_s'])

    def _tf1_pid_step(self, iid, t0, dt):
        """The 3016 loop over dt s from the host time t0: the reading through the first-order input filter FILT.T (continuous), the PID of the active
        set sampled every CARBOLITE_TF1_PID_TS on the host clock and held between samples, gain scheduling at BND.1 with hysteresis gs.hys, output
        limits OUT.LO .. min(OUT.HI, OP.HI / OP.HI2), output rate limits OP.UP / OP.DWN, the fixed outputs T.HI / T.LO of the autotune hi and lo
        stages; the heater gets output % x 1500 W (none when off or in demo mode).  Exact exponential segments of the lumped plant and the joule ledger."""
        inst = self._instances[iid]
        state = inst['state']
        ambient, capacity = self.ambient_temperature_K, CARBOLITE_TF1_PARAMETERS['thermal_capacity_J_K']['value']
        loss, pmax = CARBOLITE_TF1_PARAMETERS['heat_loss_W_K']['value'], self._tf1_rated()
        loop, settings = state['loop.settings'], state['controller.settings']
        tf, ts = settings['filter_s'], CARBOLITE_TF1_PID_TS
        tau = capacity / loss
        pid = dict(state['loop.pid_out'])
        old = state['thermal.temperature_k']
        driven = state['power.enabled'] and not settings['demo']

        def plant(x, out, h):
            T, y = x
            p = pmax * out / 100.0 if driven else 0.0
            eq = ambient + p / loss
            a = _tf1_math.exp(-h / tau)
            Tn = eq + (T - eq) * a
            if tf <= 0:
                return (Tn, Tn)
            if abs(tau - tf) < 1e-9:
                return (Tn, eq + ((y - eq) + (T - eq) * h / tf) * _tf1_math.exp(-h / tf))
            A = (T - eq) * tau / (tau - tf)
            return (Tn, eq + A * a + (y - eq - A) * _tf1_math.exp(-h / tf))

        x = (old, pid['pv_filtered_k'] if tf > 0 else old)
        tune = state['autotune.state']
        forced = None
        if tune['enabled'] and state['power.enabled'] and tune['stage'] in ('hi', 'lo'):
            forced = min(max(loop['tune_high_percent' if tune['stage'] == 'hi' else 'tune_low_percent'], 0.0), loop['output_high_percent'])
        if not state['power.enabled'] or forced is not None or state['sensor.break']:   # sensor break: forced manual, fallback output 0 % (guide p.256, p.121)
            out = 0.0 if forced is None else forced
            pid.update(e_prev=None, df=0.0, u=out, out=out, p=0.0, d=0.0, t_next=None)
            x, log = plant(x, out, dt), [(out, dt)]
        else:
            sp = state['thermal.target_k']                                   # the working set point, in reading terms
            reading = lambda T: self._tf1_reading(state, T)                  # noqa: E731
            truth = lambda r: self._tf1_true_target(state, r)               # noqa: E731
            band, hys = loop['band_1_K'], loop['switch_hysteresis_K']

            def limits(k):
                hi = min(loop['output_high_percent'], loop['output_high_1_percent' if k == 1 else 'output_high_2_percent'])
                return min(loop['output_low_percent'], hi), hi

            def control(x):
                pv = reading(x[1]) - 273.15
                if pv > band + hys / 2.0:                                      # gain scheduling (manual p.35): set 2 above BND.1
                    pid['set'] = 2
                elif pv < band - hys / 2.0:
                    pid['set'] = 1
                terms = state['loop.pid_sets']['set%d' % pid['set']]
                lo, hi = limits(pid['set'])
                prev = pid.get('u', 0.0)
                u = _g02_pid_sample(pid, sp - reading(x[1]), 100.0 / terms['pb_K'], terms['ti_s'], terms['td_s'], ts, lo, hi)
                up, down = loop['rate_up_percent_s'], loop['rate_down_percent_s']
                if (up > 0 and u > prev + up * ts) or (down > 0 and u < prev - down * ts):   # OP.UP / OP.DWN: % per second
                    u = min(prev + up * ts, u) if up > 0 and u > prev else max(prev - down * ts, u)
                    pid['i'] = max(-100.0, min(100.0, u - pid['p'] - pid['d']))
                    pid['out'] = u
                pid.update(hi=hi, lo=lo)
                return u

            def saturated_for(x, out):
                terms = state['loop.pid_sets']['set%d' % pid['set']]
                kp = 100.0 / terms['pb_K']
                dl = 100.0 if terms['td_s'] > 0 else 0.0
                if loop['rate_up_percent_s'] > 0 or loop['rate_down_percent_s'] > 0 or pid.get('hi') is None:
                    return None
                if out >= pid['hi'] - 1e-12 and pid['hi'] > 0:
                    goal, p = truth(sp - (pid['hi'] + 100.0 + dl) / kp), pmax * pid['hi'] / 100.0
                    if x[0] >= goal or x[1] > x[0]:
                        return 0.0
                elif out <= pid['lo'] + 1e-12:
                    goal, p = truth(sp + (100.0 + dl - pid['lo']) / kp), pmax * pid['lo'] / 100.0
                    if x[0] <= goal or x[1] < x[0]:
                        return 0.0
                else:
                    return None
                eq = ambient + (p if driven else 0.0) / loss
                if (goal - x[0]) * (eq - goal) <= 0:
                    return _tf1_math.inf
                return -tau * _tf1_math.log((eq - goal) / (eq - x[0]))

            def settled(x):
                target = truth(sp)
                hold = 100.0 * loss * (target - ambient) / pmax
                e = sp - reading(x[1])
                lo, hi = pid.get('lo', 0.0), pid.get('hi', 100.0)
                if (not driven or pid.get('e_prev') is None or abs(e) > 1e-6 or abs(pid['e_prev']) > 1e-6 or abs(x[0] - x[1]) > 1e-6
                        or abs(pid['i'] - hold) > 1e-4 or not lo <= hold <= hi or target <= ambient):   # at rest within 1 uK: held exactly
                    return None
                pid.update(i=hold, e_prev=0.0, df=0.0, p=0.0, d=0.0, out=hold)
                return (target, target), hold

            keep = 2 + (16 if any(state['loop.pid_sets'][k]['td_s'] > 0 for k in ('set1', 'set2')) else 0)
            x, log = _g02_run_sampled(x, t0, dt, pid, ts, control, plant, saturated_for, settled, keep=keep)
        heater_j = sum(pmax * out / 100.0 * h for out, h in log) if driven else 0.0
        new = x[0]
        pid['pv_filtered_k'] = x[1]
        if pid != state['loop.pid_out']:
            state['loop.pid_out'] = pid
        change_j = capacity * (new - old)
        state['thermal.temperature_k'] = new
        state['thermal.indicated_temperature_k'] = round(self._tf1_reading(state, new) - 273.15, 1) + 273.15
        state['thermal.heater_energy_j'] += heater_j
        state['thermal.heat_loss_energy_j'] += heater_j - change_j
        state['thermal.stored_energy_j'] = capacity * (new - ambient)

    def _tf1_loop_break(self, iid, t):
        inst = self._instances[iid]
        """L.BRK: output saturated and the PV has not risen by L.BRK.D within 2 x L.BRK.T (manual p.36)."""
        state, c = inst['state'], self._tf1[iid]
        loop = state['loop.settings']
        reading = self._tf1_reading(state, state['thermal.temperature_k'])
        out = state['loop.pid_out']
        saturated = self._tf1_power(state) > 0 and out['out'] >= out['hi'] - 1e-9 and state['thermal.temperature_k'] < self._tf1_true_target(state, state['thermal.target_k']) - 1e-6
        if loop['loop_break_time_s'] <= 0 or not saturated:
            c['lbrk_ref'] = None
            state['loop.break'] = False
        elif c['lbrk_ref'] is None:
            c['lbrk_ref'] = (t, reading)
        elif t - c['lbrk_ref'][0] >= 2 * loop['loop_break_time_s'] - 1e-9:
            state['loop.break'] = reading - c['lbrk_ref'][1] < loop['loop_break_delta_K']
            c['lbrk_ref'] = (t, reading)
        alarms = dict(state['alarm.state'])
        active = ['loop_break'] if state['loop.break'] else []
        acknowledged = [a for a in alarms['acknowledged'] if a in active]
        if active != alarms['active']:
            state['alarm.state'] = {'active': active, 'acknowledged': acknowledged}

    def _tf1_advance(self, iid, dt):
        inst = self._instances[iid]
        """Advance one instance: a single exact thermal segment when no controller function is busy (the original model), else sub-steps of at
        most 10 s in which the program, autotune and loop-break logic act."""
        state = inst['state']
        busy = state['program.status']['mode'] == 'run' or state['autotune.state']['enabled'] or (state['loop.settings']['loop_break_time_s'] > 0 and state['power.enabled'])
        t, end = self.time_s, self.time_s + dt
        while end - t > 1e-9:
            h = min(end - t, CARBOLITE_TF1_PROGRAM_STEP_S) if busy else end - t
            h = self._tf1_program_run(iid, h)
            heater_before = state['thermal.heater_energy_j']
            self._tf1_pid_step(iid, t, h)
            state['controller.output_percent'] = min(100.0, max(0.0, (state['thermal.heater_energy_j'] - heater_before) / h / CARBOLITE_TF1_PARAMETERS['heater_power_W']['value'] * 100.0)) if h > 0 else 0.0
            t += h
            self._tf1_after_program(iid)
            self._tf1_autotune(iid, h)
            self._tf1_loop_break(iid, t)

    def _tf1_start(self, iid):
        """P.MODE run: the program starts from the current reading (servo to PV) with its cycles."""
        state, c = self._instances[iid]['state'], self._tf1[iid]
        c['pre_target_K'] = state['thermal.target_k']
        definition = state['program.definition']
        start = self._tf1_reading(state, state['thermal.temperature_k'])
        state['program.status'] = dict(_TF1_IDLE_STATUS, mode='run', working_setpoint_K=start, target_K=start, cycles_left=definition['cycles'] or 0)
        self._tf1_enter(iid, 1)
        if state['program.status']['mode'] != 'reset':
            state['thermal.target_k'] = state['program.status']['working_setpoint_K']

    def _tf1_power_up(self, iid):
        """Instrument switch on: the panel reboots; an interrupted program recovers as RECOV says (manual p.36-37)."""
        state, c = self._instances[iid]['state'], self._tf1[iid]
        c['power_epoch'] += 1
        ps = state['program.status']
        if ps['interrupted']:
            recovery = state['program.definition']['recovery']
            if recovery == 'reset':
                state['program.status'] = dict(ps, interrupted=False)
                self._tf1_reset(iid)
            else:
                ps = dict(ps, interrupted=False)
                if recovery == 'ramp':                                          # ramp back from the current reading at the segment's rate
                    ps['working_setpoint_K'] = c['segment_start_K'] = self._tf1_reading(state, state['thermal.temperature_k'])
                    c['segment_elapsed_s'] = 0.0
                state['program.status'] = ps
                state['thermal.target_k'] = ps['working_setpoint_K']
                self._tf1_times(iid)
        self._tf1_sbrk_update(iid)

    def _tf1_message(self, iid):
        """The scrolling message of the second line now (4 characters per second), or None."""
        state = self._instances[iid]['state']
        settings = state['controller.settings']
        language = self._tf1[iid]['last_language'] if settings['language'] == 'none' else settings['language']
        key = 'sbrk' if state['sensor.break'] else 'demo' if settings['demo'] else 'autotune' if state['autotune.state']['enabled'] else \
            'running' if state['program.status']['mode'] == 'run' else 'hold' if state['program.status']['mode'] == 'hold' else None
        if key is None:
            return None
        text = '       ' + _TF1_MESSAGES[key].get(language, _TF1_MESSAGES[key]['en']) + '   '
        k = int(_tf1_math.floor(self.time_s * 4.0 + 1e-9)) % (len(text) - 6)
        return text[k:k + 7]

    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame), created on first use."""
        if instance_id not in self._panels:
            if instance_id not in self._instances: raise ValueError('Unknown instance.')
            p = _tf1_panel()
            host = _G02PanelHost(p['engine'], lambda: self._tf1_panel_read(instance_id),
                                 lambda cap, args: (self._panel_caller or self._panel_session)._invoke_checked(instance_id, self._instances[instance_id], cap, args, None),
                                 lambda: self.time_s)
            self._panels[instance_id] = p['engine'].PanelUI(p['spec'], host)
        return self._panels[instance_id]

    def _tf1_panel_read(self, instance_id):
        inst, c = self._instances[instance_id], self._tf1[instance_id]
        state = inst['state']
        out = {sid: _tf1_copy.deepcopy(value) for sid, value in state.items() if CARBOLITE_TF1_STATE_SLOTS[sid]['visibility'] == 'agent'}
        definition = state['program.definition']
        first_end = next((s['number'] for s in definition['segments'] if s['type'] == 'end'), CARBOLITE_TF1_SEGMENTS)
        pv = state['thermal.target_k'] if state['controller.settings']['demo'] else self._tf1_reading(state, state['thermal.temperature_k'])   # demo: ideal load
        sbrk = None
        if state['sensor.break']:                                            # S.brk and bAd alternate in the PV line (blink period 1 s, assumption)
            sbrk = 'S.brk' if int(_tf1_math.floor(self.time_s * 2.0 + 1e-9)) % 2 == 0 else 'bAd'
        out.update({'host.powered': state['power.enabled'], 'host.power_epoch': c['power_epoch'], 'host.message': self._tf1_message(instance_id), 'host.sbrk': sbrk, 'host.language': c['last_language'] if state['controller.settings']['language'] == 'none' else state['controller.settings']['language'],
                    'host.pv_k': pv, 'host.seg_max': first_end, 'host.min_setpoint_c': round(self.ambient_temperature_K - 273.15, 6),
                    'host.mac': list(c['mac']), 'host.program_active': state['program.status']['mode'] in ('run', 'hold', 'end'), 'host.pid_set': state['loop.pid_out']['set'],
                    'host.edit_level': {'lev1': 1, 'lev2': 2, 'lev3': 3, 'conf': 4}[definition['edit_access']]})
        return out


class CarboliteTF1SceneSynchronizationError(RuntimeError):
    """Process and common host clock committed; scene synchronization failed."""


class CarboliteTF1Runtime(_CarboliteTF1Controller):
    """Trusted host for independent TF1 instances; never expose to an untrusted agent.

    scene_adapter(instance_id, updates) must be synchronous and atomic where possible.
    It receives SI-valued slots and must not write the runtime. Robot/hardware work is
    not implemented. Scene failures are reported, not represented as false success.
    """
    def __init__(self, *, ambient_temperature_K=293.15, scene_adapter=None):
        if not _carbolite_tf1_number(ambient_temperature_K) or not 273.15 <= ambient_temperature_K <= 313.15:
            raise ValueError('Ambient must be finite and within assumed 273.15–313.15 K range.')
        self.ambient_temperature_K = float(ambient_temperature_K)
        self.time_s = 0.0
        self.scene_adapter = scene_adapter
        self._instances = {}
        self._principals = {}
        self._requests = {}
        self._logs = {}
        self._scene_faults = []
        self._lock = _tf1_threading.RLock()
        self._tf1, self._panels = {}, {}                                    # 3016 controller internals and front panels per instance
        self.panel_capture_dir = None                                       # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)
        self._principals['__tf1_panel__'], self._logs['__tf1_panel__'] = ('agent', None), []
        self._panel_session = CarboliteTF1Session(self, '__tf1_panel__')   # the front panel's own path to the commands (not logged)
        self._panel_caller = None                                           # the session whose gesture runs: its commands act as that caller

    def create_instance(self, instance_id, asset_type_id=CARBOLITE_TF1_ASSET_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances:
                raise ValueError('Instance ID must be nonempty and unique.')
            if asset_type_id != CARBOLITE_TF1_ASSET_TYPE:
                raise ValueError('Unsupported asset type.')
            state = {sid: 0.0 for sid in CARBOLITE_TF1_STATE_SLOTS}
            state.update({'power.enabled': False, 'thermal.target_k': self.ambient_temperature_K, 'thermal.temperature_k': self.ambient_temperature_K,
                          'thermal.indicated_temperature_k': round(self.ambient_temperature_K - 273.15, 1) + 273.15, 'gas.inlet.connected': False, 'gas.outlet.connected': False,
                          'gas.flow_rate_m3_s': None, 'gas.pressure_pa': None})
            state.update(_tf1_copy.deepcopy(_TF1_DEFAULTS))
            factory = _tf1_tuning(CARBOLITE_TF1_PARAMETERS['thermal_capacity_J_K']['value'], CARBOLITE_TF1_PARAMETERS['heat_loss_W_K']['value'],
                                  CARBOLITE_TF1_PARAMETERS['heater_power_W']['value'], _TF1_DEFAULTS['controller.settings']['filter_s'])
            state.update({'loop.pid_sets': {'set1': dict(factory), 'set2': dict(factory)},
                          'loop.pid_out': {'set': 1, 'p': 0.0, 'i': 0.0, 'd': 0.0, 'out': 0.0, 'pv_filtered_k': self.ambient_temperature_K, 'e_prev': None, 'df': 0.0,
                                           'u': 0.0, 't_next': None, 'hi': 100.0, 'lo': 0.0}})
            state.update({'controller.output_percent': 0.0, 'loop.break': False, 'sensor.pv_offset_k': 0.0, 'sensor.offset_error_k': 0.0, 'sensor.gain_error': 0.0,
                          'sensor.break': False,
                          'autotune.state': {'enabled': False, 'status': 'rdy', 'stage': 'idle', 'stage_time_s': 0.0}, 'alarm.state': {'active': [], 'acknowledged': []},
                          'program.status': dict(_TF1_IDLE_STATUS, working_setpoint_K=self.ambient_temperature_K, target_K=self.ambient_temperature_K)})
            if self.scene_adapter:
                self.scene_adapter(instance_id, _tf1_copy.deepcopy(state))
            self._instances[instance_id] = {'state': state, 'revision': 0, 'created_at_s': self.time_s}
            self._tf1[instance_id] = {'pre_target_K': None, 'segment_start_K': 0.0, 'segment_elapsed_s': 0.0, 'lbrk_ref': None, 'power_epoch': 0, 'sbrk_hold': False,
                                      'last_language': 'en', 'mac': [0, 12, 70, 3, 1, len(self._tf1) + 1]}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('Principal identity and role must be host-assigned and unique.')
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(sid, str) or sid not in self._instances for sid in instance_ids)):
                raise ValueError('Instance scope must contain registered IDs.')
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))
            self._logs[principal_id] = []

    def session(self, principal_id):
        with self._lock:
            if not isinstance(principal_id, str) or principal_id not in self._principals:
                raise ValueError('Register host identity before creating a session.')
            return CarboliteTF1Session(self, principal_id)

    def describe_host(self):
        return {'sim.step': {'visibility': 'trusted_host_only', 'kind': 'simulation',
                'input_schema': {'type': 'object', 'properties': {'dt_s': {'type': 'number', 'minimum': 0, 'maximum': 3600, 'x-unit': 's'}}, 'required': ['dt_s'], 'additionalProperties': False},
                'binding': 'CarboliteTF1Runtime.step(dt_s)', 'scope': 'All TF1 instances registered with this host share one world clock.'}}

    def sync_scene(self, instance_ids=None):
        if self.scene_adapter is None:
            raise RuntimeError('No scene backend is registered.')
        with self._lock:
            ids = list(self._instances) if instance_ids is None else instance_ids
            if not isinstance(ids, (list, tuple)) or any(not isinstance(sid, str) or sid not in self._instances for sid in ids):
                raise ValueError('Unknown scene instance.')
            for sid in ids:
                try:
                    self.scene_adapter(sid, _tf1_copy.deepcopy(self._instances[sid]['state']))
                except Exception:
                    self._scene_faults.append({'instance_id': sid, 'time_s': self.time_s, 'code': 'SCENE_SYNC_FAILED'})
                    raise CarboliteTF1SceneSynchronizationError('Scene resynchronization failed; external effects may be partial.') from None

    def _thermal_step(self, inst, dt_s, target=None, power=None):
        """Exact constant-UA ODE segments and ideal target hold, with joule ledger.  target / power: the controller's true target and the
        available heater power (output high limit, demo mode); by default the stored target and the rated power."""
        state = inst['state']
        ambient = self.ambient_temperature_K
        old = state['thermal.temperature_k']
        target = state['thermal.target_k'] if target is None else target
        capacity = CARBOLITE_TF1_PARAMETERS['thermal_capacity_J_K']['value']
        loss = CARBOLITE_TF1_PARAMETERS['heat_loss_W_K']['value']
        power = CARBOLITE_TF1_PARAMETERS['heater_power_W']['value'] if power is None else power
        target = max(target, ambient)                                       # a set point below ambient only lets the furnace cool
        heater_j = 0.0
        decay = _tf1_math.exp(-loss * dt_s / capacity)
        if not state['power.enabled'] or power <= 0:
            new = ambient + (old - ambient) * decay
        elif old < target and ambient + power / loss <= target:          # limited output cannot reach the target: full available power
            equilibrium = ambient + power / loss
            new = equilibrium + (old - equilibrium) * decay
            heater_j = power * dt_s
        elif old < target:
            equilibrium = ambient + power / loss
            crossing_s = -capacity / loss * _tf1_math.log((equilibrium - target) / (equilibrium - old))
            heating_s = min(dt_s, max(0.0, crossing_s))
            new = equilibrium + (old - equilibrium) * _tf1_math.exp(-loss * heating_s / capacity)
            heater_j = power * heating_s
            if dt_s >= crossing_s:
                new = target
                heater_j += loss * (target - ambient) * (dt_s - heating_s)
        elif old > target:
            cooling_s = dt_s if target <= ambient else min(dt_s, capacity / loss * _tf1_math.log((old - ambient) / (target - ambient)))
            new = ambient + (old - ambient) * _tf1_math.exp(-loss * cooling_s / capacity)
            if cooling_s < dt_s:
                new = target
                heater_j = loss * (target - ambient) * (dt_s - cooling_s)
        else:
            new = target
            heater_j = loss * (target - ambient) * dt_s
        change_j = capacity * (new - old)
        lost_j = heater_j - change_j
        state['thermal.temperature_k'] = new
        state['thermal.indicated_temperature_k'] = round(self._tf1_reading(state, new) - 273.15, 1) + 273.15
        state['thermal.heater_energy_j'] += heater_j
        state['thermal.heat_loss_energy_j'] += lost_j
        state['thermal.stored_energy_j'] = capacity * (new - ambient)

    def step(self, dt_s):
        if not _carbolite_tf1_number(dt_s) or not 0 <= dt_s <= 3600:
            raise ValueError('dt_s must be finite and within [0, 3600].')
        if not dt_s:
            return
        with self._lock:
            failed = False
            for sid, inst in self._instances.items():
                before = _tf1_copy.deepcopy(inst['state'])
                self._tf1_advance(sid, float(dt_s))
                updates = {key: _tf1_copy.deepcopy(value) for key, value in inst['state'].items() if value != before[key]}
                if updates:
                    inst['revision'] += 1
                if self.scene_adapter and updates:
                    try:
                        self.scene_adapter(sid, updates)
                    except Exception:
                        failed = True
                        self._scene_faults.append({'instance_id': sid, 'time_s': self.time_s + dt_s, 'code': 'SCENE_SYNC_FAILED'})
            self.time_s += dt_s
            if failed:
                raise CarboliteTF1SceneSynchronizationError('Process and common clock advanced; one or more scene updates failed.')

    def debug_snapshot(self):
        """Trusted-only diagnostics; this is not an agent-readable trace file."""
        with self._lock:
            return _tf1_copy.deepcopy({'time_s': self.time_s, 'ambient_temperature_K': self.ambient_temperature_K,
                                     'instances': self._instances, 'scene_faults': self._scene_faults})


class CarboliteTF1Session:
    """Agent-facing protocol adapter with identity assigned by the trusted host."""
    def __init__(self, host, principal_id):
        if principal_id not in host._principals:
            raise ValueError('Unknown principal.')
        self.__host = host
        self.__principal_id = principal_id

    def _instance(self, instance_id):
        if not isinstance(instance_id, str):
            return None
        _, scope = self.__host._principals[self.__principal_id]
        return self.__host._instances.get(instance_id) if scope is None or instance_id in scope else None

    def _visible(self, slot):
        role, _ = self.__host._principals[self.__principal_id]
        return slot['visibility'] == 'agent' or role in ('debug', 'evaluator')

    def _envelope(self, instance_id, request_id=None, *, status='succeeded', result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {'protocol_version': '1.0.0', 'instance_id': instance_id if isinstance(instance_id, str) else None,
                'request_id': request_id if isinstance(request_id, str) else None, 'operation_id': None, 'status': status,
                'state_revision': inst['revision'] if inst else None,
                'clock': {'domain': 'simulation', 'time_s': self.__host.time_s},
                'result': result or {}, 'observations': observations or {}, 'error': error}

    def _error(self, instance_id, code, request_id=None, status='rejected'):
        messages = {'UNKNOWN_INSTANCE': 'Instance is unavailable in this session.', 'UNKNOWN_CAPABILITY': 'Capability is unavailable for this asset.',
                    'INVALID_ARGUMENT': 'Arguments do not meet the declared schema or limits.', 'PRECONDITION_FAILED': 'Required operating configuration is unavailable.',
                    'ACCESS_DENIED': 'Request is outside the session access policy.', 'REQUEST_CONFLICT': 'Request ID was used for a different operation.',
                    'UNSUPPORTED': 'No scene backend is registered for this capability.', 'DEVICE_ERROR': 'Scene backend did not complete the command.'}
        return self._envelope(instance_id, request_id, status=status, error={'code': code, 'message': messages[code], 'retryable': False,
                             'side_effects': 'unknown' if code == 'DEVICE_ERROR' else 'none', 'progress': None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(instance_id, 'INVALID_ARGUMENT')
            if instance_id is not None and not self._instance(instance_id):
                return self._error(instance_id, 'UNKNOWN_INSTANCE')
            if asset_type_id is not None and asset_type_id != CARBOLITE_TF1_ASSET_TYPE:
                return self._error(instance_id, 'INVALID_ARGUMENT')
            _, scope = self.__host._principals[self.__principal_id]
            if scope is not None and not scope:
                return self._error(instance_id, 'ACCESS_DENIED')
            metadata = carbolite_tf1_metadata()
            metadata['instance_id'] = instance_id
            metadata['adapter'] = 'basic40_runtime_thermal.CarboliteTF1Session'
            metadata['state_slots'] = {sid: slot for sid, slot in metadata['state_slots'].items() if self._visible(slot)}
            for group in ('controller_capabilities', 'evaluator_capabilities', 'panel_capabilities'):
                metadata['capabilities'].update({k: v for k, v in metadata.pop(group).items() if self._visible(v)})
            for name, cap in metadata['capabilities'].items():
                cap['state_effects'] = [effect for effect in cap['state_effects'] if self._visible(CARBOLITE_TF1_STATE_SLOTS[effect['state_id']])]
                if name.startswith('scene.') and self.__host.scene_adapter is None:
                    cap['availability'] = 'unsupported'
            return metadata

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if not inst:
                return self._error(instance_id, 'UNKNOWN_INSTANCE')
            slots = CARBOLITE_TF1_STATE_SLOTS
            if state_ids is None:
                state_ids = [sid for sid, slot in slots.items() if self._visible(slot)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids) or len(set(state_ids)) != len(state_ids):
                return self._error(instance_id, 'INVALID_ARGUMENT')
            if any(sid not in slots or not self._visible(slots[sid]) for sid in state_ids):
                return self._error(instance_id, 'ACCESS_DENIED')
            observations = {}
            for sid in state_ids:
                slot = slots[sid]
                value = _tf1_copy.deepcopy(inst['state'][sid])
                observations[sid] = {'value': value, 'unit': slot['unit'], 'quality': 'unavailable' if value is None else 'estimated' if slot['origin'] == 'derived' else 'valid',
                    'sample_time_s': self.__host.time_s, 'sample_window_s': [inst['created_at_s'], self.__host.time_s] if slot['sampling']['mode'] == 'integral' else None}
                if value is None:
                    observations[sid]['reason'] = 'No gas transport, pressure solver or physical gas measurement is configured.'
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst:
                return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256 or not isinstance(capability_id, str) or not isinstance(arguments, dict):
                return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            try:
                digest = _tf1_hashlib.sha256(_tf1_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            except (ValueError, TypeError, OverflowError):
                return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            key = (self.__principal_id, instance_id, request_id)
            if key in host._requests:
                old_digest, old_response = host._requests[key]
                return _tf1_copy.deepcopy(old_response) if old_digest == digest else self._error(instance_id, 'REQUEST_CONFLICT', request_id)
            response = self._invoke_checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, _tf1_copy.deepcopy(response))
            host._logs[self.__principal_id].append({'instance_id': instance_id, 'capability_id': capability_id, 'request_id': request_id, 'response': _tf1_copy.deepcopy(response)})
            return response

    def _invoke_checked(self, instance_id, inst, capability, args, request_id):
        """Run ownership (lead rule 2026-10-19): the caller that starts the program or the autotune owns it; another caller cannot change the
        program mode, advance, edit the program or switch the autotune while it is active.  The instrument switch is a hard switch (physical
        action, lead clarification 2026-10-19): power.set follows it and is never refused; the controller reacts as to a power loss (RECOV)."""
        s = inst['state']
        active = s['program.status']['mode'] in ('run', 'hold', 'end') or s['autotune.state']['enabled']
        guarded = capability in ('program.set_mode', 'program.advance', 'program.set_segment', 'program.configure', 'autotune.set')
        if guarded and _g02_foreign_run(self.__host, self.__principal_id, inst.get('run_owner'), active):
            return self._error(instance_id, 'ACCESS_DENIED', request_id)
        response = self._invoke_body(instance_id, inst, capability, args, request_id)
        starts = (capability == 'program.set_mode' and isinstance(args, dict) and args.get('mode') == 'run') or \
            (capability == 'autotune.set' and isinstance(args, dict) and args.get('enabled') is True)
        if starts and response['status'] == 'succeeded' and not active:
            inst['run_owner'] = self.__principal_id
        return response

    def _invoke_body(self, instance_id, inst, capability, args, request_id):
        host = self.__host
        state = inst['state']
        fail = lambda code, status='rejected': self._error(instance_id, code, request_id, status)
        if isinstance(capability, str) and capability.startswith('panel.'):
            return self._panel_command(instance_id, capability, args, request_id)
        if capability in _TF1_CAPS or capability in ('sensor.configure_error', 'fault.inject', 'fault.clear'):
            return self._controller_command(instance_id, inst, capability, args, request_id)
        metadata = carbolite_tf1_metadata(panel=False)
        if capability not in metadata['capabilities']:
            return fail('UNKNOWN_CAPABILITY')
        properties = metadata['capabilities'][capability]['input_schema']['properties']
        if set(args) != set(properties):
            return fail('INVALID_ARGUMENT')
        for name, schema in properties.items():
            value = args[name]
            if schema['type'] == 'boolean' and type(value) is not bool:
                return fail('INVALID_ARGUMENT')
            if schema['type'] == 'string' and (not isinstance(value, str) or value not in schema['enum']):
                return fail('INVALID_ARGUMENT')
            if schema['type'] == 'number' and (not _carbolite_tf1_number(value) or not schema.get('minimum', -float('inf')) <= value <= schema.get('maximum', float('inf'))):
                return fail('INVALID_ARGUMENT')
        if capability.startswith('scene.') and host.scene_adapter is None:
            return fail('UNSUPPORTED', 'unsupported')
        post = []
        if capability == 'power.set':
            if args['enabled'] and state['work_tube.withdrawal_m'] != 0:
                return fail('PRECONDITION_FAILED')
            updates = {'power.enabled': args['enabled']}
            result = {'enabled': args['enabled']}
            if not args['enabled'] and state['power.enabled']:               # the controller stops: a running program is interrupted, autotune ends
                if state['program.status']['mode'] in ('run', 'hold'):
                    updates['program.status'] = dict(state['program.status'], interrupted=True)
                if state['autotune.state']['enabled']:
                    updates['autotune.state'] = dict(state['autotune.state'], enabled=False, status='abort', stage='idle', stage_time_s=0.0)
                updates.update({'controller.output_percent': 0.0, 'loop.break': False, 'alarm.state': {'active': [], 'acknowledged': []}})
            if args['enabled'] and not state['power.enabled']:
                post.append(lambda: host._tf1_power_up(instance_id))
        elif capability == 'thermal.set_temperature':
            if args['temperature_K'] < host.ambient_temperature_K:
                return fail('INVALID_ARGUMENT')
            if not state['power.enabled'] or state['program.status']['mode'] in ('run', 'hold'):
                return fail('PRECONDITION_FAILED')
            updates = {'thermal.target_k': float(args['temperature_K'])}
            result = {'setpoint_K': float(args['temperature_K'])}
        elif capability == 'scene.set_end_seal':
            side = args['side']
            sid = 'end_seal.' + side + '.withdrawal_m'
            gas_sid = 'gas.' + ('inlet' if side == 'left' else 'outlet') + '.connected'
            changing = args['withdrawal_m'] != state[sid]
            if changing and (state['power.enabled'] or state[gas_sid] or (state['work_tube.withdrawal_m'] > 0 and args['withdrawal_m'] < 0.10)):
                return fail('PRECONDITION_FAILED')
            updates = {sid: float(args['withdrawal_m'])}
            result = {'side': side, 'withdrawal_m': float(args['withdrawal_m'])}
        elif capability == 'scene.set_work_tube':
            changing = args['withdrawal_m'] != state['work_tube.withdrawal_m']
            if changing and (state['power.enabled'] or state['end_seal.left.withdrawal_m'] < 0.10 or state['end_seal.right.withdrawal_m'] < 0.10 or state['gas.inlet.connected'] or state['gas.outlet.connected']):
                return fail('PRECONDITION_FAILED')
            updates = {'work_tube.withdrawal_m': float(args['withdrawal_m'])}
            result = {'withdrawal_m': float(args['withdrawal_m'])}
        else:
            inlet = args['port_id'] == 'gas_inlet'
            sid = 'gas.' + ('inlet' if inlet else 'outlet') + '.connected'
            seal = 'end_seal.' + ('left' if inlet else 'right') + '.withdrawal_m'
            if args['connected'] and not state[sid] and (state['power.enabled'] or state[seal] != 0 or state['work_tube.withdrawal_m'] != 0):
                return fail('PRECONDITION_FAILED')
            updates = {sid: args['connected']}
            result = {'port_id': args['port_id'], 'connected': args['connected']}
        changed = {sid: value for sid, value in updates.items() if state[sid] != value}
        if host.scene_adapter and changed:
            try:
                host.scene_adapter(instance_id, _tf1_copy.deepcopy(changed))
            except Exception:
                return fail('DEVICE_ERROR', 'failed')
        if changed:
            state.update(changed)
            inst['revision'] += 1
        for action in post:
            action()
        return self._envelope(instance_id, request_id, result=result)

    def _panel_command(self, instance_id, capability, args, request_id):
        """panel.* commands: a gesture on the instance's front panel (engine + panel_ui.json), a screen capture, or the evaluator frame."""
        host, panel = self.__host, _tf1_panel()
        if capability not in panel['caps']:
            return self._error(instance_id, 'UNKNOWN_CAPABILITY', request_id)
        if not self._visible(panel['caps'][capability]):
            return self._error(instance_id, 'ACCESS_DENIED', request_id)
        host._panel_caller = self                                           # a gesture acts as the session that makes it (access rules)
        try:
            result, error = _g02_panel_command(panel, host.panel(instance_id), capability, args, instance_id=instance_id, time_s=host.time_s,
                                               capture_dir=host.panel_capture_dir)
        finally:
            host._panel_caller = None
        return self._error(instance_id, error, request_id) if error else self._envelope(instance_id, request_id, result=result)

    def _controller_command(self, instance_id, inst, capability, args, request_id):
        """The 3016 controller commands: schema and precondition checks, then one committed update (scene callback first)."""
        host, state = self.__host, inst['state']
        c = host._tf1[instance_id]
        fail = lambda code, status='rejected': self._error(instance_id, code, request_id, status)
        meta = _tf1_extend_metadata({'capabilities': {}, 'limitations': []}, False)
        info = meta['controller_capabilities'].get(capability) or meta['evaluator_capabilities'][capability]
        if not self._visible(info):
            return fail('ACCESS_DENIED')
        if not isinstance(args, dict) or _g02_engine().check_arguments(info['input_schema'], args) or (not info['input_schema']['required'] and _TF1_CAPS.get(capability, ('', {}, ()))[2] == '*' and not args):
            return fail('INVALID_ARGUMENT')
        if capability not in ('sensor.configure_error', 'fault.inject', 'fault.clear') and not state['power.enabled']:
            return fail('PRECONDITION_FAILED')
        if state['sensor.break'] and ((capability == 'program.set_mode' and args['mode'] == 'run') or (capability == 'autotune.set' and args['enabled'])):
            return fail('PRECONDITION_FAILED')                               # bad PV: the programmer stays held, no autotune (guide p.231, p.265; assumption: refused)
        updates, result, post = {}, _tf1_copy.deepcopy(args), []
        if capability in ('controller.configure', 'loop.configure', 'comms.configure'):
            sid = {'controller.configure': 'controller.settings', 'loop.configure': 'loop.settings', 'comms.configure': 'comms.settings'}[capability]
            values = _tf1_copy.deepcopy(args)
            for key in ('ip_address', 'subnet_mask', 'gateway'):
                if key in values:
                    if not all(float(x).is_integer() for x in values[key]):
                        return fail('INVALID_ARGUMENT')
                    values[key] = [int(x) for x in values[key]]
            for key in ('customer_id',):
                if key in values:
                    values[key] = int(values[key])
            updates[sid] = dict(state[sid], **{k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in ('customer_id',) else v)
                                               for k, v in values.items()})
            if args.get('language') not in (None, 'none'):
                post.append(lambda: c.update(last_language=args['language']))
        elif capability in ('sensor.calibrate_offset', 'sensor.reset_calibration', 'sensor.configure_error'):
            if capability == 'sensor.configure_error':
                updates.update({'sensor.offset_error_k': float(args['offset_K']), 'sensor.gain_error': float(args['gain'])})
            else:
                updates['sensor.pv_offset_k'] = float(args.get('offset_K', 0.0))
                result = {'offset_K': updates['sensor.pv_offset_k']}
            probe = dict(state, **updates)
            updates['thermal.indicated_temperature_k'] = round(host._tf1_reading(probe, state['thermal.temperature_k']) - 273.15, 1) + 273.15
        elif capability == 'autotune.set':
            tune = dict(state['autotune.state'])
            if args['enabled'] and not tune['enabled']:
                tune.update(enabled=True, status='run', stage='moni', stage_time_s=0.0)
            elif not args['enabled'] and tune['enabled']:
                tune.update(enabled=False, status='abort', stage='idle', stage_time_s=0.0)
            updates['autotune.state'] = tune
        elif capability == 'program.configure':
            updates['program.definition'] = dict(state['program.definition'], **{k: (float(v) if k == 'holdback_value_K' else v) for k, v in args.items()})
        elif capability == 'program.set_segment':
            definition = _tf1_copy.deepcopy(state['program.definition'])
            first_end = next((s['number'] for s in definition['segments'] if s['type'] == 'end'), CARBOLITE_TF1_SEGMENTS)
            if args['number'] > first_end:                                   # segments after the first End are not reachable (manual p.20)
                return fail('PRECONDITION_FAILED')
            segment = definition['segments'][args['number'] - 1]
            segment.update({k: (float(v) if k.endswith(('_K', '_s')) else v) for k, v in args.items() if k != 'number'})
            if segment['type'] == 'rate' and segment['rate_per_unit_K'] < 0.1:
                return fail('INVALID_ARGUMENT')
            updates['program.definition'] = definition
            result = {'number': args['number'], 'type': segment['type']}
        elif capability == 'program.set_mode':
            mode, ps = args['mode'], state['program.status']
            if mode == 'run' and ps['mode'] in ('reset', 'end'):
                if state['program.definition']['segments'][0]['type'] == 'end':
                    return fail('PRECONDITION_FAILED')                        # no program to run
                post.append(lambda: host._tf1_start(instance_id))
            elif mode == 'run' and ps['mode'] == 'hold':
                updates['program.status'] = dict(ps, mode='run')
            elif mode == 'hold':
                if ps['mode'] != 'run':
                    return fail('PRECONDITION_FAILED')
                updates['program.status'] = dict(ps, mode='hold')
            elif mode == 'reset' and ps['mode'] != 'reset':
                post.append(lambda: host._tf1_reset(instance_id))
        elif capability == 'program.advance':
            if state['program.status']['mode'] not in ('run', 'hold'):
                return fail('PRECONDITION_FAILED')
            post.append(lambda: host._tf1_enter(instance_id, state['program.status']['segment'] + 1))
        elif capability == 'alarm.acknowledge':
            updates['alarm.state'] = {'active': list(state['alarm.state']['active']), 'acknowledged': list(state['alarm.state']['active'])}
            result = {'active': list(state['alarm.state']['active'])}
        elif capability in ('fault.inject', 'fault.clear'):
            broken = capability == 'fault.inject'
            result = {'code': args['code']} if broken else {'cleared': ['S.brk'] if state['sensor.break'] else []}
            updates['sensor.break'] = broken
            probe = dict(state, **updates)
            updates['thermal.indicated_temperature_k'] = round(host._tf1_reading(probe, state['thermal.temperature_k']) - 273.15, 1) + 273.15
            post.append(lambda: host._tf1_sbrk_update(instance_id))
        changed = {sid: value for sid, value in updates.items() if state[sid] != value}
        if host.scene_adapter and changed:
            try:
                host.scene_adapter(instance_id, _tf1_copy.deepcopy(changed))
            except Exception:
                return fail('DEVICE_ERROR', 'failed')
        if changed:
            state.update(changed)
            inst['revision'] += 1
        before = _tf1_copy.deepcopy(state)
        for action in post:
            action()
        if post and before != state:
            inst['revision'] += int(not changed)
            after = {sid: _tf1_copy.deepcopy(v) for sid, v in state.items() if before[sid] != v}
            if host.scene_adapter and after:
                try:
                    host.scene_adapter(instance_id, after)
                except Exception:
                    host._scene_faults.append({'instance_id': instance_id, 'time_s': host.time_s, 'code': 'SCENE_SYNC_FAILED'})
        if capability == 'program.set_mode':
            result = {'mode': state['program.status']['mode']}
        elif capability == 'program.advance':
            result = {'segment': state['program.status']['segment']}
        return self._envelope(instance_id, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if not self._instance(instance_id):
                return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            slot = CARBOLITE_TF1_STATE_SLOTS.get(state_id) if isinstance(state_id, str) else None
            if slot is None or not self._visible(slot) or slot['access'] != 'read_write' or not slot['write_via']:
                return self._error(instance_id, 'ACCESS_DENIED', request_id)
            route = slot['write_via']
            return self.invoke(instance_id=instance_id, capability_id=route['capability_id'],
                               arguments={**route['fixed_arguments'], route['parameter']: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return _tf1_copy.deepcopy(self.__host._logs[self.__principal_id])
# END CHEM-186 Carbolite TF1 runtime.


# CHEM-012 BINDER VD 56 — appended independently; no batch01/global registry edits.
import copy as _vd56_copy
import math as _vd56_math
from basic40_runtime import LabHost as _VD56BaseHost, LabSession as _VD56BaseSession
from basic40_runtime import _Instance as _VD56Instance, _number as _vd56_number
from basic40_runtime import SceneSynchronizationError as BinderVD56SceneSynchronizationError

BINDER_VD56_ASSET_ID = "instrument.binder_vd56"
_VD56_SOURCE = "https://www.binder-world.com/int-en/products/drying-ovens/vacuum-drying-chambers/product/vd-56"
_VD56_G = 180.0 / 78.0
_VD56_C = -_VD56_G * 4800.0 / _vd56_math.log(1.0 - 180.0 / 1400.0)


def _vd56_parameter(value, unit, evidence, source):
    return {"value": value, "unit": unit, "evidence": evidence, "source": source}


BINDER_VD56_PARAMETERS = {
    "heater_power_W": _vd56_parameter(1400.0, "W", "public_document", _VD56_SOURCE + "; rated electrical power treated as maximum empty-system heat input"),
    "minimum_target_above_ambient_K": _vd56_parameter(9.0, "K", "public_document", "CHEM-012 workbook row 17: ambient +9 deg C"),
    "maximum_temperature_K": _vd56_parameter(493.15, "K", "public_document", "CHEM-012 workbook row 17: 220 deg C"),
    "thermal_capacity_J_K": _vd56_parameter(_VD56_C, "J/K", "derived_estimate", "C=-G*4800/ln(1-180/1400); whole empty-system effective value calibrated 22->100 deg C in 80 min, not measured component heat capacity"),
    "heat_loss_W_K": _vd56_parameter(_VD56_G, "W/K", "derived_estimate", "G=180/(100-22), interpreting 180 Wh/h at 100 deg C as total steady loss; auxiliaries not separately resolved"),
    "reference_ambient_temperature_K": _vd56_parameter(295.15, "K", "assumption", "Nominal 22 deg C selected within official empty-chamber test condition +22+/-3 deg C, standard aluminium shelves, chamber pressure <20 mbar"),
    "reference_heating_time_s": _vd56_parameter(4800.0, "s", "public_document", _VD56_SOURCE + "; heating-up to 100 deg C: 80 min"),
    "maximum_door_angle_rad": _vd56_parameter(_vd56_math.radians(100), "rad", "assumption", "Scene limit; not measured manufacturer travel"),
    "door_open_loss_W_K": _vd56_parameter(20.0, "W/K", "assumption", "Uncalibrated extra lumped heat loss at fully open door"),
    "door_open_heater_fraction": _vd56_parameter(0.0, "1", "assumption", "Heater derates linearly with door angle to zero at full opening; simulation behavior, not manufacturer interlock"),
    "chamber_volume_m3": _vd56_parameter(0.055, "m^3", "public_document", "CHEM-012 workbook: 55 L; gas mass is not solved"),
    "isolated_pressure_rise_pa_s": _vd56_parameter(1000.0 / 3600.0, "Pa/s", "public_document", "CHEM-012: 0.01 bar/h interpreted as isolated-chamber pressure rise, not mass leak rate"),
    "reference_pressure_pa": _vd56_parameter(5000.0, "Pa", "public_document", "CHEM-012: pressure fluctuation reference 50 mbar absolute"),
    "reference_pressure_fluctuation_pa": _vd56_parameter(100.0, "Pa", "public_document", "CHEM-012: +/-1 mbar at 50 mbar, not general absolute accuracy"),
    "pressure_proxy_period_s": _vd56_parameter(60.0, "s", "assumption", "Synthetic sine-wave indication period, not manufacturer-specified response"),
    "pressure_proxy_activation_tolerance_pa": _vd56_parameter(1.0, "Pa", "assumption", "5000 Pa display ripple activated only within 1 Pa of the explicit external 5000 Pa boundary"),
    "vent_time_constant_s": _vd56_parameter(15.0, "s", "assumption", "First-order backfill toward host atmospheric absolute pressure; no gas composition or mass flow"),
    "door_pressure_tolerance_pa": _vd56_parameter(100.0, "Pa", "assumption", "Scene opening requires pressure within 100 Pa of host atmosphere; not a hardware safety claim"),
    "external_pump_speed_m3_s": _vd56_parameter(None, "m^3/s", "unknown", "No pump included/selected; explicit host boundary and time constant are required, no pump curve inferred"),
}


def _vd56_slot(unit, origin, meaning, component, *, value_type="number", visibility="agent", capability=None, parameter=None):
    return {"unit": unit, "origin": origin, "visibility": visibility, "access": "read_write" if capability else "read_only",
        "write_via": {"capability_id": capability, "parameter": parameter} if capability else None,
        "value_schema": {"type": value_type}, "meaning_zh": meaning, "component_ids": [component],
        "sampling": {"mode": "instant", "time_domain": "simulation", "window": None},
        "reference_conditions": "Absolute pressure; host atmospheric pressure is the backfill boundary" if unit == "Pa" else None}


BINDER_VD56_STATE_SPECS = {
    "power.enabled": _vd56_slot("1", "setpoint", "本机加热供电；不控制外接泵", "display", value_type="boolean", capability="power.set", parameter="enabled"),
    "thermal.target_k": _vd56_slot("K", "setpoint", "空载系统的温度控制目标", "display", capability="thermal.set_temperature", parameter="temperature_K"),
    "thermal.temperature_k": _vd56_slot("K", "process_truth", "空载系统/架面有效温度真值；非真空气体或样品温度", "chamber", visibility="debug"),
    "thermal.indicated_temperature_k": _vd56_slot("K", "derived", "按0.1摄氏度网格取整后转换至K的显示代理", "display"),
    "door.angle_rad": _vd56_slot("rad", "scene", "门铰链角度，过程状态同步至场景", "door", capability="scene.set_door", parameter="angle_rad"),
    "pressure.pressure_pa": _vd56_slot("Pa", "process_truth", "简化外部边界模型的腔内绝对压力真值", "chamber", visibility="debug"),
    "pressure.indicated_pressure_pa": _vd56_slot("Pa", "derived", "绝压显示代理；仅5000Pa近稳态叠加合成±100Pa波动，不是全程绝对精度", "display"),
    "vacuum.valve_open": _vd56_slot("1", "setpoint", "通向外接真空边界的本机阀门状态", "vacuum_valve", value_type="boolean", capability="vacuum.set_valve", parameter="opened"),
    "vent.valve_open": _vd56_slot("1", "setpoint", "回填阀状态，组分未求解", "vent_valve", value_type="boolean", capability="vent.set_valve", parameter="opened"),
    "vacuum.external_connected": _vd56_slot("1", "derived", "宿主已配置外接真空边界；本机无内置泵", "vacuum_port", value_type="boolean"),
}
_VD56_CAP_ARGS = {
    "power.set": {"enabled": {"type": "boolean"}},
    "thermal.set_temperature": {"temperature_K": {"type": "number", "minimum": 282.15, "maximum": 493.15, "x-unit": "K", "description": "Actual lower bound is host ambient +9 K"}},
    "scene.set_door": {"angle_rad": {"type": "number", "minimum": 0, "maximum": _vd56_math.radians(100), "x-unit": "rad"}},
    "vacuum.set_valve": {"opened": {"type": "boolean"}}, "vent.set_valve": {"opened": {"type": "boolean"}},
}
_VD56_CAP_RESULTS = {"power.set": ("enabled", "power.enabled"), "thermal.set_temperature": ("setpoint_K", "thermal.target_k"),
    "scene.set_door": ("angle_rad", "door.angle_rad"), "vacuum.set_valve": ("opened", "vacuum.valve_open"), "vent.set_valve": ("opened", "vent.valve_open")}
_VD56_COMPONENTS = {"power.set": ["power_inlet", "display", "heater"], "thermal.set_temperature": ["display", "control_buttons", "heater", "chamber", "temperature_probe"],
    "scene.set_door": ["door", "door_gasket"], "vacuum.set_valve": ["vacuum_valve", "vacuum_port", "chamber"], "vent.set_valve": ["vent_valve", "inert_gas_port", "chamber"]}
_VD56_SUMMARIES = {"power.set": "设置本机加热供电；外接真空源与机械阀门不随本机断电而改变", "thermal.set_temperature": "仅写入温度目标；实际升降温由宿主step推进",
    "scene.set_door": "近环境压力且抽气阀关闭时设置门角；须同步场景适配器", "vacuum.set_valve": "开关通往宿主显式配置的外接真空边界的阀门",
    "vent.set_valve": "开关回填阀，后续step使压力趋向宿主环境绝压，无气体组分模型"}
_VD56_PRECONDITIONS = {
    "power.set": [],
    "thermal.set_temperature": [{"description_zh": "本机开机且温度目标处于环境+9K至493.15K", "state_ids": ["power.enabled"], "enforced_by": "adapter", "check": "power.enabled and ambient_K + 9 <= temperature_K <= 493.15"}],
    "scene.set_door": [{"description_zh": "场景适配器存在；开门须抽气阀关闭且腔压与环境差≤100Pa", "state_ids": ["vacuum.valve_open", "pressure.pressure_pa"], "enforced_by": "adapter", "check": "scene_adapter is not None and (angle_rad == 0 or (not vacuum.valve_open and abs(pressure_pa - ambient_pa) <= 100))"}],
    "vacuum.set_valve": [{"description_zh": "开启时须已配置外接真空边界、门关闭且回填阀关闭", "state_ids": ["vacuum.external_connected", "door.angle_rad", "vent.valve_open"], "enforced_by": "adapter", "check": "not opened or (external_connected and door.angle_rad == 0 and not vent.valve_open)"}],
    "vent.set_valve": [{"description_zh": "开启时抽气阀须关闭；回填边界由宿主环境提供", "state_ids": ["vacuum.valve_open"], "enforced_by": "adapter", "check": "not opened or not vacuum.valve_open"}],
}


# ---- CHEM-012 front panel and the RD4 controller functions behind it (operating manual 7001-0383 E3.1, 2026-10-18) ----
# Front panel (panel.press / panel.switch / panel.capture / panel.frame): the RD4 segment LCD with its text line, the keys Up / Down / OK /
# Back / Standby, the USB port and the power plug (panel_ui.json beside the contract), run by scripts/asset_runtime/_panel_ui.py on top of
# the capabilities below (no second copy of the logic).  Controller functions behind the panel, all usable through the API as well:
# temperature control on/off; pressure set point and pressure control (the controller drives the valves; cyclical rinsing aerates 0-100 %
# of an 8 s cycle); special functions GAS/AIR 2 (option not fitted, so no ventilation), close all valves and vacuum pump off; safety
# controller class 2 (Limit / Offset) and tolerance range alarms with acknowledgement and buzzer; drying monitoring; time and week programs
# (loaded as APT-COM or the RD4 Program Editor would); settings (language, unit, date, time, address, brightness, buzzer, language query at
# restart, passwords, Ethernet, recorder rate); data recorder DL1 / DL2 / event list with USB stick export and configuration import;
# service reminder; mains plug.  Pressure control is on by default (manual p.64-65); with it off the direct valve commands of the original asset
# work while the chamber is not in standby; standby closes all valves and de-energizes the pump socket (manual p.49).
from datetime import timedelta as _vd56_timedelta

BINDER_VD56_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets/basic40_batch02/t1_thermal/binder_vd56"
_VD56_CLOCK_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)           # controller clock at simulation time 0 until set (assumption)
_VD56_LANGUAGES = ("de", "en", "fr", "es", "it")                        # p.82
_VD56_RINSING_CYCLE_S = 8.0                                              # p.68: valve duty cycle; the model uses its average
_VD56_DRYING_HOLD_S = 300.0                                              # p.66: pressure control off 5 min after the second plateau
_VD56_PLATEAU_BAND = (100.0, 0.05)       # second plateau: within max(100 Pa, 5 %) of the attainable pressure (assumption; no solvent model)
_VD56_SERVICE_S, _VD56_SERVICE_REPEAT_S = 365 * 86400.0, 7 * 86400.0     # p.99, p.154: one year of operation, then weekly
_VD56_DL2_S = 60.0                                                       # p.137: fixed 1 min
_VD56_RECORDER_MAX = {"dl1": 110000, "dl2": 27000, "events": 200}       # p.138
_VD56_HEATER_RISE_K = 100.0              # heating element above the chamber at full heater power (assumption; p.85 shows only an example)
_VD56_SUBSTEP_S = 60.0                   # controller events are evaluated at every full minute at the latest (the DL2 recorder rate)
_VD56_MAC = "00:0c:d8:0a:28:00"          # simulated controller MAC (the manual's example is 00:00:00:00:00:00)
_VD56_TEXTS = {"type": "array", "items": {"type": "string"}}
_VD56_BOOL = {"type": "boolean"}
_VD56_PROGRAM_TEXT = {"none": None, "time": "time_program_active", "week": "week_program_active"}
_VD56_FAULTS = ("overtemperature", "temperature_sensor", "pressure_sensor")   # documented faults the evaluator can inject (p.92, p.101, p.155-156)
_VD56_SENSOR_FAULTS = ("temperature_sensor", "pressure_sensor")              # remedy: BINDER service replaces the sensor (fault.clear, p.156)
_VD56_OTP_RELEASE_K = 503.15             # p.92: after the restart the thermal switch releases the heating below 230 C
_VD56_OTP_UNPLUG_S = 10.0                # p.48: pull the power plug for at least 10 s before the restart
_VD56_NUMBER_OR_NULL = {"anyOf": [{"type": "number"}, {"type": "null"}]}


def _vd56_enum(*values):
    return {"type": "string", "enum": list(values)}


def _vd56_num(unit, low=None, high=None, integer=False):
    schema = {"type": "integer" if integer else "number", "x-unit": unit}
    if low is not None: schema["minimum"] = low
    if high is not None: schema["maximum"] = high
    return schema


def _vd56_schema_slot(unit, origin, meaning, component, schema, *, visibility="agent", capability=None, parameter=None):
    slot = _vd56_slot(unit, origin, meaning, component, visibility=visibility, capability=capability, parameter=parameter)
    slot["value_schema"] = schema
    return slot


def _vd56_setting(unit, meaning, component, schema, parameter, capability="settings.configure"):
    return _vd56_schema_slot(unit, "setpoint", meaning, component, schema, capability=capability, parameter=parameter)


BINDER_VD56_RD4_SLOTS = {
    "power.mains_connected": _vd56_schema_slot("1", "scene", "电源插头接通；拔出时加热、控制器与显示停止，待机状态与全部设定保留（p.48–49）", "power_inlet", _VD56_BOOL, capability="power.set_mains", parameter="connected"),
    "thermal.control_enabled": _vd56_setting("1", "温度控制开关（快捷菜单Temperature control，p.63–64）；关闭时加热停止，实际温度照常显示", "heater", _VD56_BOOL, "enabled", "thermal.set_control"),
    "thermal.heating_active": _vd56_schema_slot("1", "derived", "加热图标：加热器正在升温或保温输出（p.37）", "display", _VD56_BOOL),
    "thermal.heater_temperature_k": _vd56_slot("K", "derived", "加热元件温度显示代理（Back键按住5 s，p.85）：腔温＋加热功率比例×100 K（假设）", "heater"),
    "pressure.target_pa": _vd56_setting("Pa", "压力设定值0–1100 mbar（p.55）；压力控制开启时控制器按它开关抽气阀与通风阀", "display", {"type": "number", "x-unit": "Pa"}, "pressure_Pa", "pressure.set_setpoint"),
    "pressure.control_enabled": _vd56_setting("1", "压力控制（快捷菜单Ventilate：Yes开启，No关闭并通风，p.64–65）；开启时阀门由控制器驱动；默认开启（手册）；关闭后才可用资产原有的直接阀门命令", "vacuum_valve", _VD56_BOOL, "enabled", "pressure.set_control"),
    "rinsing.percent": _vd56_setting("%", "循环冲洗：8 s周期内通风阀开启的百分比0–100（p.68–69）；压力控制开启时生效", "vent_valve", _vd56_num("%", 0, 100), "percent", "rinsing.set_percent"),
    "functions.gas_air2": _vd56_setting("1", "特殊功能2 GAS/AIR 2：改用选配通风口（本配置未装，激活后不能通风，p.61）", "inert_gas_port", _VD56_BOOL, "gas_air2", "functions.configure"),
    "functions.valves_closed": _vd56_setting("1", "特殊功能3：关闭所有阀门（p.62–63）", "vacuum_valve", _VD56_BOOL, "close_all_valves", "functions.configure"),
    "functions.pump_off": _vd56_setting("1", "特殊功能4：泵模块插座断电，外接真空边界不再抽气（p.60）", "vacuum_port", _VD56_BOOL, "vacuum_pump_off", "functions.configure"),
    "safety.mode": _vd56_setting("1", "2级安全控制器模式：limit绝对限值；offset温度设定值以上的允许超温（p.92–93）", "safety_limiter", _vd56_enum("limit", "offset"), "mode", "safety.configure"),
    "safety.limit_k": _vd56_setting("K", "Limit模式的安全控制器限值", "safety_limiter", {"type": "number", "x-unit": "K"}, "limit_K", "safety.configure"),
    "safety.offset_k": _vd56_setting("K", "Offset模式：温度设定值以上的允许超温", "safety_limiter", {"type": "number", "x-unit": "K"}, "offset_K", "safety.configure"),
    "alarm.active": _vd56_schema_slot("1", "derived", "当前成立的报警条件：safety_controller、temperature_range、pressure_range、overtemperature（热开关）、"
                                      "temperature_sensor / pressure_sensor（传感器故障，数值显示- - - -）（p.101）", "display", _VD56_TEXTS),
    "fault.injected": _vd56_schema_slot("1", "process_truth", "评测者用fault.inject注入的故障（真值）：overtemperature热开关已动作（重启且腔温低于230 °C前不加热）、"
                                        "temperature_sensor / pressure_sensor传感器损坏（服务更换前由fault.clear排除）", "display", _VD56_TEXTS, visibility="debug"),
    "alarm.latched": _vd56_schema_slot("1", "derived", "显示中的报警：条件成立，或条件已结束但尚未用OK确认（p.103）", "display", _VD56_TEXTS),
    "alarm.acknowledged": _vd56_schema_slot("1", "derived", "已用OK确认（蜂鸣器静音）而条件仍成立的报警", "display", _VD56_TEXTS),
    "alarm.buzzer": _vd56_schema_slot("1", "derived", "蜂鸣器在响：声音报警开启且有未确认报警（不模拟声音）", "display", _VD56_BOOL),
    "info.pending": _vd56_schema_slot("1", "derived", "待OK确认的信息：drying_finished、service_due、program_started、program_end（p.48、p.99、p.105、p.154）", "display", _VD56_TEXTS),
    "drying.active": _vd56_setting("1", "干燥监控运行中（p.66–67）：抽到固定最低压力，第二平台5 min后关闭压力控制并自动通风", "vacuum_valve", _VD56_BOOL, "running", "drying.set_monitoring"),
    "program.loaded": _vd56_schema_slot("1", "derived", "控制器中存有的程序：time、week（经APT-COM或RD4程序编辑器传入，p.104）", "display", _VD56_TEXTS),
    "program.running": _vd56_schema_slot("1", "derived", "运行中的程序：none、time、week（p.105）", "display", _vd56_enum("none", "time", "week")),
    "program.elapsed_s": _vd56_slot("s", "derived", "时间程序已运行时间（断电期间照计，p.48）", "display"),
    "settings.language": _vd56_setting("1", "菜单语言de/en/fr/es/it（p.82）", "display", _vd56_enum(*_VD56_LANGUAGES), "language"),
    "settings.temperature_unit": _vd56_setting("1", "温度单位C/F：所有温度值换算显示（p.82）", "display", _vd56_enum("C", "F"), "temperature_unit"),
    "settings.chamber_address": _vd56_setting("1", "APT-COM通讯的设备地址1–254（p.85）", "data_interfaces", _vd56_num("1", 1, 254, integer=True), "chamber_address"),
    "settings.brightness_percent": _vd56_setting("%", "显示亮度10–100 %（p.85）", "display", _vd56_num("%", 10, 100), "brightness_percent"),
    "settings.audible_alarm": _vd56_setting("1", "声音报警（蜂鸣器）开关（p.104）", "display", _VD56_BOOL, "audible_alarm"),
    "settings.language_at_restart": _vd56_setting("1", "开机询问语言、温度单位、日期与时间（p.84，出厂ON）", "display", _VD56_BOOL, "language_at_restart"),
    "recorder.interval_s": _vd56_setting("s", "数据记录器DL1存储间隔1–60 min（p.138）", "data_interfaces", _vd56_num("s", 60, 3600), "recorder_interval_s"),
    "tolerance.temperature_delay_s": _vd56_setting("s", "温度容差报警延时1–120 min（p.96；出厂10 min，p.101）", "display", _vd56_num("s", 60, 7200), "temperature_alarm_delay_s"),
    "tolerance.temperature_range_k": _vd56_setting("K", "温度容差带1.0–10.0 °C（p.96）", "display", _vd56_num("K", 1, 10), "temperature_range_K"),
    "tolerance.pressure_delay_s": _vd56_setting("s", "压力容差报警延时1–120 min（p.97；出厂10 min，p.101）", "display", _vd56_num("s", 60, 7200), "pressure_alarm_delay_s"),
    "tolerance.pressure_range_pa": _vd56_setting("Pa", "压力容差带10–200 mbar（p.97）", "display", _vd56_num("Pa", 1000, 20000), "pressure_range_Pa"),
    "clock.offset_s": _vd56_slot("s", "setpoint", "控制器时钟相对仿真时间的偏移（clock.set_date / clock.set_time设定）", "display"),
    "clock.datetime": _vd56_schema_slot("1", "derived", "控制器日期时间（ISO，读取时由仿真时钟与偏移计算）", "display", {"type": "string"}),
    "network.ip_mode": _vd56_setting("1", "IP地址分配方式auto/manual（p.132）", "data_interfaces", _vd56_enum("auto", "manual"), "ip_mode", "network.configure"),
    "network.dns_mode": _vd56_setting("1", "DNS服务器地址分配方式auto/manual（p.132）", "data_interfaces", _vd56_enum("auto", "manual"), "dns_mode", "network.configure"),
    "network.ip_address": _vd56_schema_slot("1", "setpoint", "IP地址（自动分配时仿真无DHCP服务器，显示0.0.0.0）", "data_interfaces", {"type": "string"}),
    "network.subnet_mask": _vd56_schema_slot("1", "setpoint", "子网掩码", "data_interfaces", {"type": "string"}),
    "network.gateway": _vd56_schema_slot("1", "setpoint", "标准网关", "data_interfaces", {"type": "string"}),
    "network.dns_server": _vd56_schema_slot("1", "setpoint", "DNS服务器地址", "data_interfaces", {"type": "string"}),
    "network.mac_address": _vd56_schema_slot("1", "derived", "控制器MAC地址（仿真值）", "data_interfaces", {"type": "string"}),
    "network.dns_name": _vd56_schema_slot("1", "derived", "DNS设备名（p.131）", "data_interfaces", {"type": "string"}),
    "usb.stick_inserted": _vd56_schema_slot("1", "scene", "前面板USB口插着模拟U盘（插入后只显示USB菜单，p.139）", "usb_interface", _VD56_BOOL, capability="usb.set_stick", parameter="inserted"),
    "service.operating_s": _vd56_slot("s", "derived", "运行时间（非待机且接电），用于一年维护提醒（p.154）", "display"),
    "access.user_password": _vd56_schema_slot("1", "setpoint", "User密码（4位，0000即无密码，p.38）", "display", _vd56_num("1", 0, 9999, integer=True), visibility="debug"),
    "access.admin_password": _vd56_schema_slot("1", "setpoint", "Admin密码（4位，出厂0001，p.38）", "display", _vd56_num("1", 0, 9999, integer=True), visibility="debug"),
}
BINDER_VD56_STATE_SPECS.update(BINDER_VD56_RD4_SLOTS)
for _sid, _text in (("thermal.indicated_temperature_k", "按0.1摄氏度网格取整后转换至K的显示代理；温度传感器损坏时为null（显示- - - -，p.101）"),
                    ("pressure.indicated_pressure_pa", "绝压显示代理；仅5000Pa近稳态叠加合成±100Pa波动，不是全程绝对精度；压力传感器损坏时为null（显示- - - -，p.101）")):
    BINDER_VD56_STATE_SPECS[_sid].update(value_schema=dict(_VD56_NUMBER_OR_NULL), meaning_zh=_text)
_VD56_FAULT_CAPS = {   # evaluator only: name -> input properties, output properties, observed slots, summary
    "fault.inject": ({"code": _vd56_enum(*_VD56_FAULTS)}, {"code": _vd56_enum(*_VD56_FAULTS)},
                     ["fault.injected", "alarm.active", "alarm.latched", "thermal.indicated_temperature_k", "pressure.indicated_pressure_pa"],
                     "评测者：注入手册记载的故障（p.92、p.101、p.155–156）。overtemperature：2级超温保护热开关动作，立即报警Overtemperature、加热关闭；按手册确认报警、拔插头至少10 s后重启，"
                     "腔温低于230 °C时才恢复加热（p.48、p.92）。temperature_sensor：Pt100损坏，温度数值显示- - - -、报警、加热停止（p.155“不加热”）。pressure_sensor：压力传感器损坏，"
                     "压力数值显示- - - -、报警，压力控制关闭两阀（假设）。"),
    "fault.clear": ({"code": _vd56_enum(*_VD56_SENSOR_FAULTS)}, {"code": _vd56_enum(*_VD56_SENSOR_FAULTS)}, ["fault.injected", "alarm.active"],
                    "评测者：BINDER服务更换损坏的传感器（p.156：联系BINDER服务）；读数恢复，报警条件结束（已确认的报警随之复位）。热开关没有此项：按手册重启。"),
}
_VD56_SECTION = {"type": "object", "properties": {"duration_s": _vd56_num("s", 1, 360000), "temperature_K": _vd56_num("K", 273.15, 493.15),
                                                  "pressure_Pa": _vd56_num("Pa", 0, 110000), "ramp": _VD56_BOOL},
                 "required": ["duration_s", "temperature_K", "pressure_Pa"], "additionalProperties": False}
_VD56_POINT = {"type": "object", "properties": {"weekday": _vd56_num("1", 0, 6, integer=True), "time": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
                                                "temperature_K": _vd56_num("K", 273.15, 493.15), "pressure_Pa": _vd56_num("Pa", 0, 110000)},
               "required": ["weekday", "time", "temperature_K", "pressure_Pa"], "additionalProperties": False}
_VD56_OCTETS = {"type": "array", "minItems": 4, "maxItems": 4, "items": _vd56_num("1", 0, 255)}   # whole numbers (checked by the adapter)
_VD56_SETTINGS = {   # settings.configure argument: state slot, schema (whole minutes for the times; p.85, p.96-97, p.104, p.138)
    "language": ("settings.language", _vd56_enum(*_VD56_LANGUAGES)), "temperature_unit": ("settings.temperature_unit", _vd56_enum("C", "F")),
    "chamber_address": ("settings.chamber_address", _vd56_num("1", 1, 254, integer=True)), "brightness_percent": ("settings.brightness_percent", _vd56_num("%", 10, 100)),
    "audible_alarm": ("settings.audible_alarm", _VD56_BOOL), "language_at_restart": ("settings.language_at_restart", _VD56_BOOL),
    "recorder_interval_s": ("recorder.interval_s", _vd56_num("s", 60, 3600)), "temperature_alarm_delay_s": ("tolerance.temperature_delay_s", _vd56_num("s", 60, 7200)),
    "temperature_range_K": ("tolerance.temperature_range_k", _vd56_num("K", 1, 10)), "pressure_alarm_delay_s": ("tolerance.pressure_delay_s", _vd56_num("s", 60, 7200)),
    "pressure_range_Pa": ("tolerance.pressure_range_pa", _vd56_num("Pa", 1000, 20000))}
_VD56_RD4_CAPS = {   # name: kind, input properties, optional inputs ('*': all, at least one), output properties, observed slots, components, summary
    "power.set_mains": ("simulation", {"connected": _VD56_BOOL}, (), {"connected": _VD56_BOOL}, ["power.mains_connected"], ["power_inlet", "display"],
                        "插上或拔出电源插头（操作者动作）：拔出时加热、控制器与显示停止；插回后按原设定继续，若开启开机询问则先问语言、单位、日期与时间（p.48–51）"),
    "thermal.set_control": ("device_command", {"enabled": _VD56_BOOL}, (), {"enabled": _VD56_BOOL}, ["thermal.control_enabled"], ["heater", "display"],
                            "温度控制开关（快捷菜单Temperature control，p.63–64）：关闭时加热停止、显示Temperature control off"),
    "pressure.set_setpoint": ("device_command", {"pressure_Pa": _vd56_num("Pa", 0, 110000)}, (), {"pressure_Pa": _vd56_num("Pa")}, ["pressure.target_pa"], ["display", "vacuum_valve", "vent_valve"],
                              "压力设定值0–1100 mbar（p.55）；压力控制开启时控制器开关阀门趋向设定值"),
    "pressure.set_control": ("device_command", {"enabled": _VD56_BOOL}, (), {"enabled": _VD56_BOOL}, ["pressure.control_enabled", "vacuum.valve_open", "vent.valve_open"], ["vacuum_valve", "vent_valve", "chamber"],
                             "压力控制（快捷菜单Ventilate，p.64–65）：开启后控制器驱动抽气阀与通风阀（直接阀门命令被拒）；关闭时抽气阀关、通风阀开（腔体通风），干燥监控结束"),
    "rinsing.set_percent": ("device_command", {"percent": _vd56_num("%", 0, 100)}, (), {"percent": _vd56_num("%")}, ["rinsing.percent"], ["vent_valve", "inert_gas_port"],
                            "循环冲洗设定0–100 %（p.68–69）：压力控制开启时通风阀在8 s周期内按比例开启，平衡压力随之升高"),
    "functions.configure": ("device_command", {"gas_air2": _VD56_BOOL, "close_all_valves": _VD56_BOOL, "vacuum_pump_off": _VD56_BOOL}, "*",
                            {"gas_air2": _VD56_BOOL, "close_all_valves": _VD56_BOOL, "vacuum_pump_off": _VD56_BOOL},
                            ["functions.gas_air2", "functions.valves_closed", "functions.pump_off"], ["vacuum_valve", "vent_valve", "inert_gas_port", "vacuum_port"],
                            "特殊功能2–4（p.57–63）：GAS/AIR 2（本配置无该接口，激活后不能通风）、关闭所有阀门、真空泵插座断电"),
    "safety.configure": ("device_command", {"mode": _vd56_enum("limit", "offset"), "limit_K": _vd56_num("K", 273.15, 523.15), "offset_K": _vd56_num("K", 0, 50)}, "*",
                         {"mode": _vd56_enum("limit", "offset"), "limit_K": _vd56_num("K"), "offset_K": _vd56_num("K")}, ["safety.mode", "safety.limit_k", "safety.offset_k"], ["safety_limiter", "heater"],
                         "2级安全控制器（p.92–95）：Limit绝对限值或Offset设定值以上的偏差；超过即报警Safety controller、加热关闭，确认且降到限值以下后恢复"),
    "messages.acknowledge": ("device_command", {}, (), {"remaining_alarms": _VD56_TEXTS}, ["alarm.latched", "alarm.acknowledged", "alarm.buzzer", "info.pending"], ["display"],
                             "OK确认信息与报警（p.99、p.103）：待确认信息清除；已结束的报警复位，仍成立的报警只静音蜂鸣器、图标保持点亮"),
    "drying.set_monitoring": ("device_command", {"running": _VD56_BOOL}, (), {"running": _VD56_BOOL}, ["drying.active", "pressure.target_pa"], ["vacuum_valve", "vent_valve", "chamber"],
                              "干燥监控Start/Cancel（p.66–67）：开始须非待机且压力控制开启；中途取消时以当前实际压力为新压力设定值"),
    "program.load": ("simulation", {"kind": _vd56_enum("time", "week"), "sections": {"type": "array", "minItems": 1, "maxItems": 100, "items": _VD56_SECTION},
                                    "points": {"type": "array", "minItems": 1, "maxItems": 100, "items": _VD56_POINT}}, ("sections", "points"),
                     {"kind": _vd56_enum("time", "week")}, ["program.loaded"], ["display", "data_interfaces"],
                     "把时间程序（各段时长、温度、压力、斜坡）或周程序（星期、时刻、温度、压力）传入控制器：代替APT-COM或RD4程序编辑器经U盘传送（p.104）"),
    "program.start": ("device_command", {"kind": _vd56_enum("time", "week")}, (), {"kind": _vd56_enum("time", "week")}, ["program.running", "program.elapsed_s", "info.pending"], ["display", "heater", "vacuum_valve"],
                      "启动时间程序或周程序（p.105）：须非待机且已传入该程序；显示Program started"),
    "program.stop": ("device_command", {}, (), {"running": _vd56_enum("none", "time", "week")}, ["program.running"], ["display"], "停止运行中的程序（p.105）；设定值保持为最后的程序值"),
    "settings.configure": ("device_command", {k: v for k, (_, v) in _VD56_SETTINGS.items()}, "*", {k: v for k, (_, v) in _VD56_SETTINGS.items()},
                           [sid for sid, _ in _VD56_SETTINGS.values()], ["display", "data_interfaces"],
                           "控制器设定（Settings菜单，p.82–85、p.96–97、p.104、p.138）：语言、温度单位、设备地址、亮度、声音报警、开机询问、DL1间隔（整分钟）、温度/压力容差报警的延时（整分钟）与范围"),
    "clock.set_date": ("device_command", {"day": _vd56_num("1", 1, 31, integer=True), "month": _vd56_num("1", 1, 12, integer=True), "year": _vd56_num("1", 2000, 2099, integer=True)}, (),
                       {"date": {"type": "string"}}, ["clock.offset_s", "clock.datetime"], ["display"], "设定控制器日期（p.83），保留当天时刻"),
    "clock.set_time": ("device_command", {"hour": _vd56_num("1", 0, 23, integer=True), "minute": _vd56_num("1", 0, 59, integer=True)}, (),
                       {"time": {"type": "string"}}, ["clock.offset_s", "clock.datetime"], ["display"], "设定控制器时刻（p.84），秒归零"),
    "access.set_password": ("device_command", {"level": _vd56_enum("user", "admin"), "code": _vd56_num("1", 0, 9999, integer=True)}, (), {"level": _vd56_enum("user", "admin")}, [], ["display"],
                            "设定User或Admin密码（4位，0000即无密码，p.70–71）；结果不回显密码"),
    "network.configure": ("device_command", {"ip_mode": _vd56_enum("auto", "manual"), "dns_mode": _vd56_enum("auto", "manual"), "ip_address": _VD56_OCTETS, "subnet_mask": _VD56_OCTETS,
                                             "gateway": _VD56_OCTETS, "dns_server": _VD56_OCTETS}, "*", {"ip_address": {"type": "string"}, "dns_server": {"type": "string"}},
                          ["network.ip_mode", "network.dns_mode", "network.ip_address", "network.subnet_mask", "network.gateway", "network.dns_server"], ["data_interfaces"],
                          "以太网设定（p.131–134）：IP分配AUTO/MANU、DNS分配、手动IP地址、子网掩码、网关、DNS服务器（各4段0–255）；仿真无DHCP服务器"),
    "usb.set_stick": ("simulation", {"inserted": _VD56_BOOL}, (), {"inserted": _VD56_BOOL}, ["usb.stick_inserted"], ["usb_interface"],
                      "把模拟U盘插入或拔出前面板USB口（操作者动作）；U盘上的文件保留"),
    "usb.read_stick": ("query", {}, (), {"files": {"type": "object"}}, [], ["usb_interface"], "读模拟U盘上的文件（相当于把U盘拿到电脑上查看）"),
    "usb.export": ("device_command", {"content": _vd56_enum("configuration", "recorder", "service")}, (), {"files": _VD56_TEXTS}, [], ["usb_interface", "display"],
                   "USB导出（p.139–140）：configuration写KONF380.set；recorder写DL1/DL2/事件表CSV（温度一律°C）；service写Service文件夹；须插着U盘"),
    "usb.import_configuration": ("device_command", {}, (), {"file_name": {"type": "string"}}, [], ["usb_interface", "display"],
                                 "从U盘导入KONF380.set配置（p.139）：应用其中的设定并清空数据记录器（p.138）；无文件时拒绝（面板显示ERR）"),
}


def _vd56_capability(name, kind, props, optional, output, observes, components, summary, pre):
    return {"availability": "implemented", "visibility": "agent", "kind": kind, "description_zh": summary, "component_ids": list(components),
            "input_schema": {"type": "object", "properties": _vd56_copy.deepcopy(props), "required": [] if optional == "*" else [k for k in props if k not in optional], "additionalProperties": False},
            "output_schema": {"type": "object", "properties": _vd56_copy.deepcopy(output), "required": list(output) if name not in ("functions.configure", "safety.configure", "settings.configure", "network.configure") else [], "additionalProperties": False},
            "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": summary} for sid in observes], "observes": list(observes), "preconditions": pre,
            "preconditions_zh": [p["description_zh"] for p in pre], "scene_backend_required": False,
            "binding": "basic40_runtime_thermal.BinderVD56Session.invoke(instance_id=..., capability_id='" + name + "', arguments=..., request_id=...)",
            "completion_criterion": "Validated command state committed; no process time elapsed", "partial_failure": "Callback failure preserves runtime state; backend effects unknown"}


def _vd56_panel():
    caps = set(_VD56_CAP_ARGS) | set(_VD56_RD4_CAPS)
    return _g02_panel("binder_vd56", BINDER_VD56_ASSET_DIR, caps)


def export_binder_vd56_metadata(panel=True):
    """Export static type metadata without instance truth or external evidence.  controller_capabilities: the RD4 functions behind the front
    panel; panel_capabilities: the front-panel commands (panel.*)."""
    slots = _vd56_copy.deepcopy(BINDER_VD56_STATE_SPECS)
    for sid, slot in slots.items():
        slot["read_binding"] = f"BinderVD56Session.read(instance_id=..., state_ids=['{sid}'])"
        if slot["origin"] == "derived":
            slot["observation_note_zh"] = "quality=estimated的仿真显示代理，非实测传感器值"
    caps = {}
    examples = {"power.set": {"enabled": True}, "thermal.set_temperature": {"temperature_K": 373.15}, "scene.set_door": {"angle_rad": .5}, "vacuum.set_valve": {"opened": True}, "vent.set_valve": {"opened": True}}
    mains = {"description_zh": "电源插头接通（控制器有电）", "state_ids": ["power.mains_connected"], "enforced_by": "adapter", "check": "power.mains_connected"}
    standby_valves = {"description_zh": "待机时所有阀门关闭，不能开阀（手册p.49）", "state_ids": ["power.enabled"], "enforced_by": "adapter", "check": "power.enabled or not opened"}
    added_pre = {"power.set": [mains], "thermal.set_temperature": [mains],
                 "vacuum.set_valve": [mains, {"description_zh": "压力控制开启时阀门由控制器驱动；关闭所有阀门功能激活时不能开阀", "state_ids": ["pressure.control_enabled", "functions.valves_closed"], "enforced_by": "adapter", "check": "not pressure.control_enabled and (not opened or not functions.valves_closed)"}, standby_valves],
                 "vent.set_valve": [mains, {"description_zh": "压力控制开启时阀门由控制器驱动；关闭所有阀门功能激活时不能开阀", "state_ids": ["pressure.control_enabled", "functions.valves_closed"], "enforced_by": "adapter", "check": "not pressure.control_enabled and (not opened or not functions.valves_closed)"}, standby_valves]}
    for capability, args in _VD56_CAP_ARGS.items():
        output_key, sid = _VD56_CAP_RESULTS[capability]
        out = _vd56_copy.deepcopy(slots[sid]["value_schema"])
        if out["type"] == "number": out["x-unit"] = slots[sid]["unit"]
        effects = [{"state_id": sid, "timing": "immediate", "description_zh": _VD56_SUMMARIES[capability]}]
        if capability in ("power.set", "thermal.set_temperature", "scene.set_door"):
            effects += [{"state_id": key, "timing": "on_step", "description_zh": "宿主时间推进后的空载热响应与显示"} for key in ("thermal.temperature_k", "thermal.indicated_temperature_k")]
        if capability in ("scene.set_door", "vacuum.set_valve", "vent.set_valve"):
            effects += [{"state_id": key, "timing": "on_step", "description_zh": "宿主时间推进后的腔压与显示；无物料求解"} for key in ("pressure.pressure_pa", "pressure.indicated_pressure_pa")]
        if capability == "power.set":
            effects += [{"state_id": key, "timing": "immediate", "description_zh": "待机时运行中的程序与干燥监控结束，所有阀门关闭，泵插座断电（p.49）"} for key in ("program.running", "drying.active", "vacuum.valve_open", "vent.valve_open")]
        pre = _vd56_copy.deepcopy(_VD56_PRECONDITIONS[capability]) + _vd56_copy.deepcopy(added_pre.get(capability, []))
        caps[capability] = {"availability": "implemented", "visibility": "agent", "kind": "simulation" if capability.startswith("scene.") else "device_command", "description_zh": _VD56_SUMMARIES[capability], "component_ids": _VD56_COMPONENTS[capability],
            "input_schema": {"type": "object", "properties": _vd56_copy.deepcopy(args), "required": list(args), "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {output_key: out}, "required": [output_key], "additionalProperties": False},
            "state_effects": effects, "observes": [sid], "example_arguments": [examples[capability]], "preconditions": pre, "preconditions_zh": [p["description_zh"] for p in pre],
            "scene_backend_required": capability.startswith("scene."), "binding": "basic40_runtime_thermal.BinderVD56Session.invoke(instance_id=..., capability_id='" + capability + "', arguments=..., request_id=...)",
            "completion_criterion": "Validated command state committed; no process time elapsed", "partial_failure": "Callback failure preserves runtime state; backend effects unknown"}
    controller = {}
    rd4_pre = {"drying.set_monitoring": [{"description_zh": "开始须非待机、压力控制开启且未关闭所有阀门", "state_ids": ["power.enabled", "pressure.control_enabled", "functions.valves_closed"], "enforced_by": "adapter", "check": "not running or (power.enabled and pressure.control_enabled and not functions.valves_closed)"}],
               "program.start": [{"description_zh": "非待机且该程序已传入", "state_ids": ["power.enabled", "program.loaded"], "enforced_by": "adapter", "check": "power.enabled and kind in program.loaded"}],
               "usb.export": [{"description_zh": "插着U盘", "state_ids": ["usb.stick_inserted"], "enforced_by": "adapter", "check": "usb.stick_inserted"}],
               "usb.import_configuration": [{"description_zh": "插着U盘且U盘上有KONF380.set", "state_ids": ["usb.stick_inserted"], "enforced_by": "adapter", "check": "usb.stick_inserted and 'KONF380.set' in stick"}]}
    examples_rd4 = {"power.set_mains": {"connected": True}, "thermal.set_control": {"enabled": True}, "pressure.set_setpoint": {"pressure_Pa": 5000.0}, "pressure.set_control": {"enabled": True},
                    "rinsing.set_percent": {"percent": 5.0}, "functions.configure": {"close_all_valves": False}, "safety.configure": {"mode": "offset", "offset_K": 5.0}, "messages.acknowledge": {},
                    "drying.set_monitoring": {"running": True}, "program.load": {"kind": "time", "sections": [{"duration_s": 3600.0, "temperature_K": 333.15, "pressure_Pa": 5000.0, "ramp": False}]},
                    "program.start": {"kind": "time"}, "program.stop": {}, "settings.configure": {"language": "en"}, "clock.set_date": {"day": 18, "month": 10, "year": 2026},
                    "clock.set_time": {"hour": 9, "minute": 30}, "access.set_password": {"level": "user", "code": 0}, "network.configure": {"ip_mode": "auto"},
                    "usb.set_stick": {"inserted": True}, "usb.read_stick": {}, "usb.export": {"content": "recorder"}, "usb.import_configuration": {}}
    for name, (kind, props, optional, output, observes, components, summary) in _VD56_RD4_CAPS.items():
        pre = ([] if name in ("power.set_mains", "usb.set_stick", "usb.read_stick") else [_vd56_copy.deepcopy(mains)]) + _vd56_copy.deepcopy(rd4_pre.get(name, []))
        controller[name] = _vd56_capability(name, kind, props, optional, output, observes, components, summary, pre)
        controller[name]["example_arguments"] = [examples_rd4[name]]
    evaluator = {}
    for name, (props, output, observes, summary) in _VD56_FAULT_CAPS.items():
        evaluator[name] = dict(_vd56_capability(name, "simulation", props, (), output, observes, ["display", "temperature_probe", "heater"], summary, []), visibility="evaluator")
        evaluator[name]["example_arguments"] = [{"code": "overtemperature" if name == "fault.inject" else "temperature_sensor"}]
    panel_caps = {}
    if panel:
        p = _vd56_panel()
        called = p["engine"].spec_capabilities(p["spec"])
        effects = list({e["state_id"]: dict(e, description_zh="面板动作调用的能力的效果：" + e["description_zh"]) for c in called for e in {**caps, **controller}[c]["state_effects"] if e["timing"] == "immediate"}.values())
        panel_caps = _g02_panel_capabilities(p, "basic40_runtime_thermal.BinderVD56Session.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)", effects)
    declared = {"pressure.program": {"availability": "declared", "visibility": "agent", "kind": "configuration", "description_zh": "压力程序未实现；需外接泵与控制系统配置，调用unsupported且不改状态",
        "input_schema": {"type": "object", "properties": {"steps": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"pressure_Pa": {"type": "number", "exclusiveMinimum": 0, "x-unit": "Pa"}, "duration_s": {"type": "number", "exclusiveMinimum": 0, "x-unit": "s"}}, "required": ["pressure_Pa", "duration_s"], "additionalProperties": False}}}, "required": ["steps"], "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "example_arguments": [{"steps": [{"pressure_Pa": 5000, "duration_s": 600}]}], "state_effects": [], "observes": [], "binding": None}}
    return {"asset_types": {BINDER_VD56_ASSET_ID: {"catalog_id": "CHEM-012", "model": "BINDER VD 56", "asset_version": "1.3.0", "state_slots": slots, "capabilities": caps,   # 1.3.0: fault.inject / fault.clear
                                                   "controller_capabilities": controller, "evaluator_capabilities": evaluator, "panel_capabilities": panel_caps,
                                                   "declared_capabilities": declared, "parameters": _vd56_copy.deepcopy(BINDER_VD56_PARAMETERS)}},
        "host_capabilities": {"sim.step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "BinderVD56Runtime.step(dt_s)", "scope": "Every registered VD56 instance shares one host clock", "input_schema": {"type": "object", "properties": {"dt_s": {"type": "number", "minimum": 0, "maximum": 3600, "x-unit": "s"}}, "required": ["dt_s"], "additionalProperties": False}},
            "host.configure_external_vacuum": {"kind": "configuration", "visibility": "trusted_host_only", "binding": "BinderVD56Runtime.configure_external_vacuum", "description_zh": "无默认泵；连接时须显式提供绝压边界、时间常数及证据"},
            "host.sync_scene": {"kind": "configuration", "visibility": "trusted_host_only", "binding": "BinderVD56Runtime.sync_scene"}}, "result_schema_scope": "capability.output_schema describes envelope result"}


class BinderVD56Runtime(_VD56BaseHost):
    """Trusted host; process step, identities, scene and external boundary only.

    Empty-system thermal parameters calibrate the 100 deg C reference; neither
    220 deg C heat-up nor loaded-sample/pressure-dependent heat transfer is
    independently calibrated. Agent sessions are not Python/filesystem isolation.
    The RD4 controller (front panel, pressure control, alarms, programs, recorder) acts in sub-steps of at most one minute.
    """
    def __init__(self, *, ambient_temperature_K=295.15, ambient_pressure_pa=101325.0, scene_adapter=None):
        if not _vd56_number(ambient_pressure_pa) or not 50000 <= ambient_pressure_pa <= 120000:
            raise ValueError("Host ambient pressure must be finite in the assumed 50000..120000 Pa range")
        super().__init__(ambient_temperature_K=ambient_temperature_K, scene_adapter=scene_adapter)
        self.ambient_pressure_pa = float(ambient_pressure_pa)
        self._vacuum_boundaries = {}
        self._rd4, self._panels = {}, {}                                   # controller internals and front panels per instance
        self.panel_capture_dir = None                                       # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)
        self.register_principal("__vd56_panel__", visibility="agent")
        self._panel_session = BinderVD56Session(self, "__vd56_panel__")    # the front panel's own path to the commands (not logged)
        self._panel_caller = None                                           # the session whose gesture runs: its commands act as that caller

    def create_instance(self, instance_id, asset_type_id=BINDER_VD56_ASSET_ID):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != BINDER_VD56_ASSET_ID:
                raise ValueError("Unique nonempty instance ID and VD56 asset type required")
            s = {"power.enabled": False, "thermal.target_k": self.ambient_temperature_K + 9.0, "thermal.temperature_k": self.ambient_temperature_K,
                "thermal.indicated_temperature_k": round(self.ambient_temperature_K - 273.15, 1) + 273.15, "door.angle_rad": 0.0,
                "pressure.pressure_pa": self.ambient_pressure_pa, "pressure.indicated_pressure_pa": round(self.ambient_pressure_pa),
                "vacuum.valve_open": False, "vent.valve_open": False, "vacuum.external_connected": False}
            s.update({"power.mains_connected": True, "thermal.control_enabled": True, "thermal.heating_active": False, "thermal.heater_temperature_k": self.ambient_temperature_K,
                "pressure.target_pa": 100000.0, "pressure.control_enabled": True, "rinsing.percent": 0.0, "functions.gas_air2": False, "functions.valves_closed": False,
                "functions.pump_off": False, "safety.mode": "offset", "safety.limit_k": 503.15, "safety.offset_k": 5.0, "alarm.active": [], "alarm.latched": [],
                "alarm.acknowledged": [], "alarm.buzzer": False, "info.pending": [], "drying.active": False, "program.loaded": [], "program.running": "none",
                "program.elapsed_s": 0.0, "settings.language": "en", "settings.temperature_unit": "C", "settings.chamber_address": 1, "settings.brightness_percent": 80.0,
                "settings.audible_alarm": True, "settings.language_at_restart": True, "recorder.interval_s": 60.0, "tolerance.temperature_delay_s": 600.0,
                "tolerance.temperature_range_k": 2.0, "tolerance.pressure_delay_s": 600.0, "tolerance.pressure_range_pa": 5000.0, "clock.offset_s": 0.0,
                "network.ip_mode": "auto", "network.dns_mode": "auto", "network.ip_address": "0.0.0.0", "network.subnet_mask": "0.0.0.0", "network.gateway": "0.0.0.0",
                "network.dns_server": "0.0.0.0", "network.mac_address": _VD56_MAC, "network.dns_name": "BinderRD4-" + _VD56_MAC.replace(":", ""),
                "usb.stick_inserted": False, "service.operating_s": 0.0, "access.user_password": 0, "access.admin_password": 1, "fault.injected": []})
            if self.scene_adapter: self.scene_adapter(instance_id, _vd56_copy.deepcopy(s))
            self._instances[instance_id] = _VD56Instance(instance_id, asset_type_id, s)
            self._rd4[instance_id] = {"t_reached": False, "t_out_s": 0.0, "p_reached": False, "p_out_s": 0.0, "plateau_since": None, "programs": {},
                                      "program_start_s": None, "program_from": None, "recorder": {"dl1": [], "dl2": [], "events": []},
                                      "service_next_s": _VD56_SERVICE_S, "stick": {}, "power_epoch": 1, "unplugged_s": None, "otp_restarted": False}

    def session(self, principal_id):
        if principal_id not in self._principals: raise ValueError("Principal must first be authenticated and registered by host")
        return BinderVD56Session(self, principal_id)

    def _value(self, inst, name):
        if name == "heater_power_W" and self._rd4_heater_blocked(inst.state): return 0.0   # no mains, temperature control off, safety alarm
        return BINDER_VD56_PARAMETERS[name]["value"]

    def describe_host(self):
        return export_binder_vd56_metadata(panel=False)["host_capabilities"]

    def configure_external_vacuum(self, instance_id, *, connected, boundary_pressure_pa=None, time_constant_s=None, evidence):
        """External asymptotic chamber pressure and time constant, not pump curve.

        The effective boundary includes leak compensation by an active external
        pump. No default pump performance exists. Disconnect closes the local
        valve. All validation and callback success precede runtime modifications.
        """
        with self._lock:
            if not isinstance(instance_id, str) or instance_id not in self._instances or type(connected) is not bool or not isinstance(evidence, str) or not evidence.strip():
                raise ValueError("Valid instance, boolean connected and explicit evidence required")
            if connected:
                if not _vd56_number(boundary_pressure_pa) or not 0 < boundary_pressure_pa < self.ambient_pressure_pa or not _vd56_number(time_constant_s) or not 0 < time_constant_s <= 86400:
                    raise ValueError("Explicit positive sub-ambient absolute pressure and time constant in (0,86400]s required")
            elif boundary_pressure_pa is not None or time_constant_s is not None:
                raise ValueError("Disconnected configuration cannot retain active parameters")
            inst = self._instances[instance_id]
            updates = {"vacuum.external_connected": connected}
            if not connected: updates["vacuum.valve_open"] = False
            if self.scene_adapter:
                try: self.scene_adapter(instance_id, _vd56_copy.deepcopy(updates))
                except Exception: raise RuntimeError("Configuration callback failed; runtime unchanged; backend effects unknown") from None
            if connected: self._vacuum_boundaries[instance_id] = {"boundary_pressure_pa": float(boundary_pressure_pa), "time_constant_s": float(time_constant_s), "evidence": evidence}
            else: self._vacuum_boundaries.pop(instance_id, None)
            inst.state.update(updates)
            inst.revision += 1

    def _pressure_step(self, inst, dt, t_end):
        s = inst.state
        old = s["pressure.pressure_pa"]
        boundary = self._vacuum_boundaries.get(inst.instance_id)
        pump = boundary if boundary and s["vacuum.external_connected"] and not s["functions.pump_off"] else None
        vent_ok = not s["functions.gas_air2"]                                # GAS/AIR 2 is not fitted: that valve admits nothing (p.61)
        if s["pressure.control_enabled"]:
            pressure = self._rd4_controlled_pressure(inst, old, dt, pump, vent_ok)
        elif s["door.angle_rad"] > 0 or (s["vent.valve_open"] and vent_ok):
            pressure = self.ambient_pressure_pa + (old - self.ambient_pressure_pa) * _vd56_math.exp(-dt / self._value(inst, "vent_time_constant_s"))
        elif s["vacuum.valve_open"] and pump:
            pressure = pump["boundary_pressure_pa"] + (old - pump["boundary_pressure_pa"]) * _vd56_math.exp(-dt / pump["time_constant_s"])
        else:
            pressure = min(self.ambient_pressure_pa, old + self._value(inst, "isolated_pressure_rise_pa_s") * dt)
        s["pressure.pressure_pa"] = pressure
        indication = pressure
        if s["vacuum.valve_open"] and boundary and boundary["boundary_pressure_pa"] == 5000.0 and abs(pressure - 5000.0) <= 1.0:
            # Quantized synthetic steady indication, explicitly not sensor accuracy.
            indication = 5000.0 + 100.0 * _vd56_math.sin(2 * _vd56_math.pi * t_end / self._value(inst, "pressure_proxy_period_s"))
        s["pressure.indicated_pressure_pa"] = None if "pressure_sensor" in s["fault.injected"] else round(indication)   # broken sensor: - - - - (p.101)

    def _rd4_on(self, s):
        return s["power.mains_connected"] and s["power.enabled"]

    def _rd4_floor(self, inst, pump, vent_ok):
        """Pressure the pump can hold against cyclical rinsing (average of the 8 s valve cycle): (rate, equilibrium) or None without a pump."""
        s = inst.state
        if pump is None: return None
        kp = 1.0 / pump["time_constant_s"]
        kv = (s["rinsing.percent"] / 100.0 if vent_ok else 0.0) / self._value(inst, "vent_time_constant_s")
        return kp + kv, (pump["boundary_pressure_pa"] * kp + self.ambient_pressure_pa * kv) / (kp + kv)

    def _rd4_controlled_pressure(self, inst, p0, dt, pump, vent_ok):
        """RD4 pressure control: the controller opens the evacuation valve above the set point and the vent valve below it and holds the set
        point once reached (the pump compensates the leak); standby, no mains or 'close all valves' keep both valves shut."""
        s = inst.state
        pa, leak, tau_v = self.ambient_pressure_pa, self._value(inst, "isolated_pressure_rise_pa_s"), self._value(inst, "vent_time_constant_s")
        if s["door.angle_rad"] > 0:
            return pa + (p0 - pa) * _vd56_math.exp(-dt / tau_v)
        if not self._rd4_on(s) or s["functions.valves_closed"] or "pressure_sensor" in s["fault.injected"]:   # no pressure reading: valves shut (assumption)
            s["vacuum.valve_open"] = s["vent.valve_open"] = False
            return min(pa, p0 + leak * dt)
        target = 0.0 if s["drying.active"] else s["pressure.target_pa"]
        rinse = s["rinsing.percent"] > 0 and vent_ok
        if p0 >= target - 1e-9 and target < pa:                              # evacuate (and hold at the set point)
            s["vacuum.valve_open"], s["vent.valve_open"] = True, rinse
            floor = self._rd4_floor(inst, pump, vent_ok)
            if floor is None:
                return min(pa, p0 + leak * dt)
            k, p_eq = floor
            if p_eq < target and _vd56_math.log((p0 - p_eq) / (target - p_eq)) / k <= dt:
                return target
            return p_eq + (p0 - p_eq) * _vd56_math.exp(-k * dt)
        s["vacuum.valve_open"], s["vent.valve_open"] = False, True             # ventilate up to the set point
        if not vent_ok:
            return min(max(target, p0), p0 + leak * dt)
        if target < pa and tau_v * _vd56_math.log((pa - p0) / (pa - target)) <= dt:
            return target
        return pa + (p0 - pa) * _vd56_math.exp(-dt / tau_v)

    def _rd4_heater_blocked(self, s):
        return (not s.get("power.mains_connected", True) or not s.get("thermal.control_enabled", True) or "safety_controller" in s.get("alarm.latched", ())
                or any(f in s.get("fault.injected", ()) for f in ("overtemperature", "temperature_sensor")))   # thermal switch (p.92), no Pt100 reading (p.155)

    def _rd4_safety_value(self, s):
        return s["safety.limit_k"] if s["safety.mode"] == "limit" else s["thermal.target_k"] + s["safety.offset_k"]

    def _rd4_clock(self, inst, t=None):
        return _VD56_CLOCK_EPOCH + _vd56_timedelta(seconds=(self.time_s if t is None else t) + inst.state["clock.offset_s"])

    def step(self, dt_s):
        if not _vd56_number(dt_s) or not 0 <= dt_s <= 3600: raise ValueError("dt_s must be finite in [0,3600]")
        if not dt_s: return
        with self._lock:
            failed = False
            for inst in self._instances.values():
                before = _vd56_copy.deepcopy(inst.state)
                self._rd4_advance(inst, dt_s)
                if before != inst.state: inst.revision += 1
                updates = {k: _vd56_copy.deepcopy(v) for k, v in inst.state.items() if before[k] != v}
                if self.scene_adapter and updates:
                    try: self.scene_adapter(inst.instance_id, updates)
                    except Exception:
                        failed = True
                        self._scene_faults.append({"instance_id": inst.instance_id, "time_s": self.time_s + dt_s, "code": "SCENE_SYNC_FAILED"})
            self.time_s += dt_s
            if failed: raise BinderVD56SceneSynchronizationError("Process and common time advanced; scene updates failed; inspect trusted diagnostics")

    def _rd4_advance(self, inst, dt):
        """Advance one instance by dt in sub-steps ending at every full minute (recorder DL2), at time-program section ends and where the
        chamber reaches the safety-controller value, so the controller acts at its own times; the process models are exact over any partition."""
        s = inst.state
        t, end = self.time_s, self.time_s + dt
        while end - t > 1e-9:
            h = min(end, (_vd56_math.floor(t / _VD56_SUBSTEP_S + 1e-9) + 1) * _VD56_SUBSTEP_S) - t
            h = self._rd4_program(inst, t, h)
            h = self._rd4_safety_trip(inst, h)
            self._thermal_step(inst, h)
            s["thermal.indicated_temperature_k"] = None if "temperature_sensor" in s["fault.injected"] else round(s["thermal.temperature_k"] - 273.15, 1) + 273.15
            self._pressure_step(inst, h, t + h)
            t += h
            self._rd4_after(inst, t, h)

    def _rd4_program(self, inst, t, h):
        """Set points of a running program for the sub-step [t, t + h] (ramps reach the value of the sub-step end); returns the sub-step,
        shortened to the end of the current time-program section."""
        s, c = inst.state, self._rd4[inst.instance_id]
        kind = s["program.running"]
        if kind == "none" or not self._rd4_on(s):
            return h
        low = self.ambient_temperature_K + 9.0
        if kind == "time":
            elapsed, start = t - c["program_start_s"], 0.0
            temperature, pressure = c["program_from"]
            for section in c["programs"]["time"]["sections"]:
                stop = start + section["duration_s"]
                if elapsed < stop - 1e-9:
                    h = min(h, stop - elapsed)
                    f = (elapsed + h - start) / section["duration_s"] if section.get("ramp") else 1.0
                    s["thermal.target_k"] = max(low, temperature + f * (section["temperature_K"] - temperature))
                    s["pressure.target_pa"] = pressure + f * (section["pressure_Pa"] - pressure)
                    return h
                start, temperature, pressure = stop, section["temperature_K"], section["pressure_Pa"]
            return h
        now = self._rd4_clock(inst, t)
        minute = now.weekday() * 1440 + now.hour * 60 + now.minute
        points = sorted(c["programs"]["week"]["points"], key=lambda p: p["weekday"] * 1440 + int(p["time"][:2]) * 60 + int(p["time"][3:]))
        active = points[-1]
        for point in points:
            if point["weekday"] * 1440 + int(point["time"][:2]) * 60 + int(point["time"][3:]) <= minute:
                active = point
        s["thermal.target_k"], s["pressure.target_pa"] = max(low, active["temperature_K"]), active["pressure_Pa"]
        return h

    def _rd4_safety_trip(self, inst, h):
        """Shorten the sub-step to the moment the heated chamber exceeds the safety-controller value (the controller then cuts the heater)."""
        s = inst.state
        limit = self._rd4_safety_value(s)
        if not s["power.enabled"] or self._rd4_heater_blocked(s) or s["thermal.temperature_k"] > limit:
            return h
        trial = _VD56Instance(inst.instance_id, inst.asset_type_id, dict(s))
        self._thermal_step(trial, h)
        if trial.state["thermal.temperature_k"] <= limit + 1e-9:
            return h
        low, high = 0.0, h
        for _ in range(60):
            mid = (low + high) / 2.0
            trial.state = dict(s)
            self._thermal_step(trial, mid)
            if trial.state["thermal.temperature_k"] > limit + 1e-9: high = mid
            else: low = mid
        return max(high, 1e-6)

    def _rd4_after(self, inst, t, h):
        """Controller bookkeeping at the end of a sub-step: heating icon, element temperature, alarms, drying monitoring, programs, service
        time and the data recorder."""
        s, c = inst.state, self._rd4[inst.instance_id]
        on, powered = self._rd4_on(s), s["power.mains_connected"]
        temperature, target = s["thermal.temperature_k"], s["thermal.target_k"]
        faults = s["fault.injected"]
        if "overtemperature" in faults and powered and c["otp_restarted"] and temperature < _VD56_OTP_RELEASE_K:
            s["fault.injected"] = faults = [f for f in faults if f != "overtemperature"]   # after the restart, below 230 C the heating is released (p.92)
            c["otp_restarted"] = False
        fraction = 0.0
        if s["power.enabled"] and not self._rd4_heater_blocked(s):
            if temperature < target - 1e-9:
                fraction = 1.0
            elif abs(temperature - target) <= 1e-9 and target > self.ambient_temperature_K:
                door = s["door.angle_rad"] / self._value(inst, "maximum_door_angle_rad")
                loss = self._value(inst, "heat_loss_W_K") + door * self._value(inst, "door_open_loss_W_K")
                capacity = self._value(inst, "heater_power_W") * (1 - door * (1 - self._value(inst, "door_open_heater_fraction")))
                fraction = min(1.0, loss * (temperature - self.ambient_temperature_K) / capacity) if capacity > 0 else 0.0
        s["thermal.heating_active"] = fraction > 0
        s["thermal.heater_temperature_k"] = temperature + fraction * _VD56_HEATER_RISE_K
        active = set()
        if powered:
            active.update(faults)                                            # thermal switch and sensor faults alarm at once (p.101)
            if temperature > self._rd4_safety_value(s) + 1e-9:
                active.add("safety_controller")
            if on and s["thermal.control_enabled"] and "temperature_sensor" not in faults:   # tolerance ranges watch only after the set point was reached (p.96)
                inside = abs(temperature - target) <= s["tolerance.temperature_range_k"]
                c["t_reached"] = c["t_reached"] or inside
                c["t_out_s"] = 0.0 if inside or not c["t_reached"] else c["t_out_s"] + h
                if c["t_out_s"] >= s["tolerance.temperature_delay_s"] - 1e-9:
                    active.add("temperature_range")
            else:
                c["t_reached"], c["t_out_s"] = False, 0.0
            if on and s["pressure.control_enabled"] and not s["drying.active"] and not s["functions.valves_closed"] and "pressure_sensor" not in faults:
                inside = abs(s["pressure.pressure_pa"] - s["pressure.target_pa"]) <= s["tolerance.pressure_range_pa"]
                c["p_reached"] = c["p_reached"] or inside
                c["p_out_s"] = 0.0 if inside or not c["p_reached"] else c["p_out_s"] + h
                if c["p_out_s"] >= s["tolerance.pressure_delay_s"] - 1e-9:
                    active.add("pressure_range")
            else:
                c["p_reached"], c["p_out_s"] = False, 0.0
        self._rd4_alarms(inst, active, t)
        if s["drying.active"]:                                              # second plateau held for 5 min: pressure control off, ventilate
            boundary = self._vacuum_boundaries.get(inst.instance_id)
            pump = boundary if boundary and s["vacuum.external_connected"] and not s["functions.pump_off"] else None
            floor = self._rd4_floor(inst, pump, not s["functions.gas_air2"])
            if floor is not None and on and "pressure_sensor" not in faults and s["pressure.pressure_pa"] <= floor[1] + max(_VD56_PLATEAU_BAND[0], _VD56_PLATEAU_BAND[1] * floor[1]):
                c["plateau_since"] = t if c["plateau_since"] is None else c["plateau_since"]
                if t - c["plateau_since"] >= _VD56_DRYING_HOLD_S - 1e-9:
                    s.update({"drying.active": False, "pressure.control_enabled": False, "vacuum.valve_open": False, "vent.valve_open": True})
                    s["info.pending"] = s["info.pending"] + ["drying_finished"]
                    c["plateau_since"] = None
            else:
                c["plateau_since"] = None
        if s["program.running"] == "time":
            s["program.elapsed_s"] = t - c["program_start_s"]
            if s["program.elapsed_s"] >= sum(x["duration_s"] for x in c["programs"]["time"]["sections"]) - 1e-9:
                s["program.running"] = "none"
                s["info.pending"] = s["info.pending"] + ["program_end"]
        if on:
            s["service.operating_s"] += h
            if s["service.operating_s"] >= c["service_next_s"] - 1e-9:
                c["service_next_s"] += _VD56_SERVICE_REPEAT_S
                if "service_due" not in s["info.pending"]:
                    s["info.pending"] = s["info.pending"] + ["service_due"]
        if powered:
            rec = c["recorder"]
            row = {"time": self._rd4_clock(inst, t).strftime("%Y-%m-%dT%H:%M:%S"),
                   "temperature_C": None if "temperature_sensor" in faults else round(temperature - 273.15, 1),
                   "pressure_mbar": None if "pressure_sensor" in faults else round(s["pressure.pressure_pa"] / 100.0, 1)}
            for key, interval in (("dl2", _VD56_DL2_S), ("dl1", s["recorder.interval_s"])):
                if abs(t / interval - round(t / interval)) < 1e-6:
                    rec[key] = (rec[key] + [row])[-_VD56_RECORDER_MAX[key]:]

    def _rd4_event(self, inst, t, text):
        rec = self._rd4[inst.instance_id]["recorder"]
        rec["events"] = (rec["events"] + [{"time": self._rd4_clock(inst, t).strftime("%Y-%m-%dT%H:%M:%S"), "event": text}])[-_VD56_RECORDER_MAX["events"]:]

    def _rd4_alarms(self, inst, active, t):
        """Alarm latching (p.101-103): a new condition is shown and sounds; OK mutes it; it resets once its condition has ended and it was acknowledged."""
        s = inst.state
        for code in sorted(active - set(s["alarm.active"])): self._rd4_event(inst, t, f"{code} on")
        for code in sorted(set(s["alarm.active"]) - active): self._rd4_event(inst, t, f"{code} off")
        latched, acknowledged = list(s["alarm.latched"]), list(s["alarm.acknowledged"])
        for code in sorted(active - set(s["alarm.active"])):
            if code not in latched: latched.append(code)
            acknowledged = [x for x in acknowledged if x != code]
        for code in list(latched):
            if code not in active and code in acknowledged:
                latched.remove(code)
                acknowledged.remove(code)
        s["alarm.active"], s["alarm.latched"], s["alarm.acknowledged"] = sorted(active), latched, acknowledged
        s["alarm.buzzer"] = s["settings.audible_alarm"] and any(x not in acknowledged for x in latched)

    def _rd4_power_return(self, inst):
        """Mains back (p.48): set points resume; a time program continues where it would be, or ends if it would have ended; the panel reboots."""
        s, c = inst.state, self._rd4[inst.instance_id]
        c["power_epoch"] += 1
        if "overtemperature" in s["fault.injected"] and c["unplugged_s"] is not None and self.time_s - c["unplugged_s"] >= _VD56_OTP_UNPLUG_S - 1e-9:
            c["otp_restarted"] = True                                        # restarted after at least 10 s without mains (p.48, p.92)
        c["unplugged_s"] = None
        c["t_reached"] = c["p_reached"] = False
        c["t_out_s"] = c["p_out_s"] = 0.0
        if s["program.running"] == "time":
            s["program.elapsed_s"] = self.time_s - c["program_start_s"]
            done = s["program.elapsed_s"] >= sum(x["duration_s"] for x in c["programs"]["time"]["sections"]) - 1e-9
            if done: s["program.running"] = "none"
            s["info.pending"] = s["info.pending"] + ["program_end" if done else "program_started"]

    def _rd4_export(self, inst, content):
        """Files the USB export writes (p.139-140): configuration KONF380.set, recorder CSV tables (DL1, DL2, event list), Service folder."""
        s, c = inst.state, self._rd4[inst.instance_id]
        mac = s["network.mac_address"].replace(":", "")
        config = {"settings": {k: s[sid] for k, (sid, _) in _VD56_SETTINGS.items()}, "setpoints": {"temperature_K": s["thermal.target_k"], "pressure_Pa": s["pressure.target_pa"],
                  "rinsing_percent": s["rinsing.percent"]}, "safety": {"mode": s["safety.mode"], "limit_K": s["safety.limit_k"], "offset_K": s["safety.offset_k"]},
                  "network": {k.split(".")[1]: s[k] for k in ("network.ip_mode", "network.dns_mode", "network.ip_address", "network.subnet_mask", "network.gateway", "network.dns_server")}}
        recorder = {f"DL1_{mac}.csv": _vd56_copy.deepcopy(c["recorder"]["dl1"]), f"DL2_{mac}.csv": _vd56_copy.deepcopy(c["recorder"]["dl2"]),
                    f"EvList_{mac}.csv": _vd56_copy.deepcopy(c["recorder"]["events"])}
        if content == "configuration": files = {"KONF380.set": config}
        elif content == "recorder": files = recorder
        else: files = {"Service/" + k: v for k, v in {"KONF380.set": config, **recorder, "info.json": {"operating_s": s["service.operating_s"], "controller": "RD4"}}.items()}
        c["stick"].update(_vd56_copy.deepcopy(files))
        return sorted(files)

    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame), created on first use."""
        if instance_id not in self._panels:
            if instance_id not in self._instances: raise ValueError("Unknown instance")
            p = _vd56_panel()
            host = _G02PanelHost(p["engine"], lambda: self._rd4_panel_read(instance_id),
                                 lambda cap, args: (self._panel_caller or self._panel_session)._invoke_checked(self._instances[instance_id], cap, args, None),
                                 lambda: self.time_s)
            self._panels[instance_id] = p["engine"].PanelUI(p["spec"], host)
        return self._panels[instance_id]

    def _rd4_panel_read(self, instance_id):
        """What the RD4 firmware reads: the agent-visible slots plus host values (passwords, clock, the text line message of this instant)."""
        inst, c = self._instances[instance_id], self._rd4[instance_id]
        s = inst.state
        out = {k: _vd56_copy.deepcopy(v) for k, v in s.items() if BINDER_VD56_STATE_SPECS[k]["visibility"] == "agent"}
        now = self._rd4_clock(inst)
        out["clock.datetime"] = now.strftime("%Y-%m-%dT%H:%M:%S")
        info = ([] if s["power.enabled"] else ["standby"]) + ([] if s["thermal.control_enabled"] else ["temperature_control_off"]) + \
            ([] if s["pressure.control_enabled"] else ["pressure_control_off"]) + list(s["info.pending"]) + \
            ([_VD56_PROGRAM_TEXT[s["program.running"]]] if s["program.running"] != "none" else [])
        cycle = ["tp"] + [a for a in s["alarm.latched"] if a not in _VD56_SENSOR_FAULTS] + info   # a sensor fault shows only - - - - (p.101)
        out.update({"host.powered": s["power.mains_connected"], "host.power_epoch": c["power_epoch"], "host.user_password": s["access.user_password"],
                    "host.admin_password": s["access.admin_password"], "host.min_setpoint_c": round(self.ambient_temperature_K + 9.0 - 273.15, 6),
                    "host.min_setpoint_f": round((self.ambient_temperature_K + 9.0 - 273.15) * 1.8 + 32.0, 6), "host.day": now.day, "host.month": now.month,
                    "host.year_hi": now.year // 100, "host.year_lo": now.year % 100, "host.hour": now.hour, "host.minute": now.minute,
                    "host.text": cycle[int(_vd56_math.floor(self.time_s / 2.0 + 1e-9)) % len(cycle)], "host.info": bool(info),
                    "host.alarm_unacknowledged": any(x not in s["alarm.acknowledged"] for x in s["alarm.latched"]),
                    "host.attention": bool(s["info.pending"]) or any(x not in s["alarm.acknowledged"] for x in s["alarm.latched"]),
                    "host.service_days": max(0.0, (c["service_next_s"] - s["service.operating_s"]) / 86400.0) if s["service.operating_s"] < _VD56_SERVICE_S else 0.0,
                    "host.ip_octets": [int(x) for x in s["network.ip_address"].split(".")], "host.subnet_octets": [int(x) for x in s["network.subnet_mask"].split(".")],
                    "host.gateway_octets": [int(x) for x in s["network.gateway"].split(".")], "host.dns_octets": [int(x) for x in s["network.dns_server"].split(".")],
                    "host.stick_config": "KONF380.set" in c["stick"]})
        return out

    def debug_snapshot(self):
        with self._lock:
            result = super().debug_snapshot()
            result["external_vacuum_boundaries"] = _vd56_copy.deepcopy(self._vacuum_boundaries)
            result["ambient_pressure_pa"] = self.ambient_pressure_pa
            result["rd4_controller"] = _vd56_copy.deepcopy(self._rd4)
            return result


class BinderVD56Session(_VD56BaseSession):
    """Reuse batch01 identity/envelope/dedup, override all asset-spec operations."""
    def __init__(self, host, principal_id):
        if principal_id not in host._principals: raise ValueError("Unknown authenticated host principal")
        super().__init__(host, principal_id)
        self.__vd56_host = host
        self.__vd56_principal = principal_id

    def describe(self, *, asset_type_id=None, instance_id=None):
        host = self.__vd56_host
        with host._lock:
            if (asset_type_id is None) == (instance_id is None): return self._error(instance_id, "INVALID_ARGUMENT")
            if instance_id is not None:
                inst = self._instance(instance_id)
                if not inst: return self._error(instance_id, "UNKNOWN_INSTANCE")
                asset_type_id = inst.asset_type_id
            if asset_type_id != BINDER_VD56_ASSET_ID: return self._error(instance_id, "INVALID_ARGUMENT")
            _, scope = host._principals[self.__vd56_principal]
            if scope is not None and not scope: return self._error(instance_id, "ACCESS_DENIED")
            spec = export_binder_vd56_metadata()["asset_types"][asset_type_id]
            caps = {k: {f: _vd56_copy.deepcopy(info[f]) for f in ("input_schema", "output_schema", "description_zh", "preconditions_zh", "availability")}
                    for group in ("capabilities", "controller_capabilities", "evaluator_capabilities", "panel_capabilities") for k, info in spec[group].items() if self._visible(info)}
            if host.scene_adapter is None: caps["scene.set_door"]["availability"] = "unsupported"
            caps.update(_vd56_copy.deepcopy(spec["declared_capabilities"]))
            return {"protocol_version": "1.0.0", "asset_type_id": asset_type_id, "asset_version": spec["asset_version"], "instance_id": instance_id,
                "catalog_id": spec["catalog_id"], "model": spec["model"], "adapter": "basic40_runtime_thermal.BinderVD56Session", "capabilities": caps,
                "state_slots": {k: v for k, v in spec["state_slots"].items() if self._visible(v)},
                "limitations": ["Empty-system/standard-shelf thermal model calibrated only at 100 deg C; not gas/sample temperature, no sample evaporation or drying endpoint.",
                    "No pump included: explicit external boundary and response time supplied by host. Backfill omits gas composition and mass balance.",
                    "Estimated indications only; +/-100 Pa synthetic ripple applies solely at a near-steady 5000 Pa boundary, not general sensor accuracy.",
                    "Mechanical valves and external pump do not follow oven electrical power. No hardware or safety-rated interlock.",
                    "API filtering is not Python/filesystem isolation; in-memory deduplication does not survive restart.",
                    "RD4 controller functions behind the front panel: pressure control (on by default, manual p.64-65) drives the valves; the original direct valve commands work only with pressure control off and outside standby; standby closes all valves and switches the pump socket off (p.49); drying monitoring detects the plateau of the pure pressure model (no solvent); tolerance alarms are checked at least every minute."]}

    def read(self, *, instance_id, state_ids=None):
        host = self.__vd56_host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = BINDER_VD56_STATE_SPECS
            if state_ids is None: state_ids = [k for k, slot in slots.items() if self._visible(slot)]
            if not isinstance(state_ids, list) or any(not isinstance(k, str) for k in state_ids) or len(set(state_ids)) != len(state_ids): return self._error(instance_id, "INVALID_ARGUMENT")
            if any(k not in slots or not self._visible(slots[k]) for k in state_ids): return self._error(instance_id, "ACCESS_DENIED")
            value = lambda k: host._rd4_clock(inst).strftime("%Y-%m-%dT%H:%M:%S") if k == "clock.datetime" else _vd56_copy.deepcopy(inst.state[k])   # noqa: E731
            observations = {k: {"value": value(k), "unit": slots[k]["unit"], "quality": "unavailable" if value(k) is None else "estimated" if slots[k]["origin"] == "derived" else "valid",
                                "sample_time_s": host.time_s, "sample_window_s": None} for k in state_ids}
            return self._envelope(instance_id, observations=observations)

    def _invoke_checked(self, inst, capability, args, request):
        """Run ownership (lead rule 2026-10-19): the caller that starts a time / week program or the drying monitoring owns it; another caller
        cannot stop it or start another program over it, change its set points, temperature / pressure control, rinsing or special functions,
        import a configuration, or switch the chamber to standby while it runs.  Pulling the mains plug is a physical action and is not refused
        (sweep 2026-10-19): the chamber reacts as on a power failure (p.48)."""
        host, s = self.__vd56_host, inst.state
        c = host._rd4[inst.instance_id]
        active = s["program.running"] != "none" or s["drying.active"]
        a = args if isinstance(args, dict) else {}
        guarded = capability in ("program.stop", "program.start", "thermal.set_temperature", "thermal.set_control", "pressure.set_setpoint",
                                 "pressure.set_control", "rinsing.set_percent", "functions.configure", "usb.import_configuration") or \
            (capability == "drying.set_monitoring" and a.get("running") is False) or (capability == "power.set" and a.get("enabled") is False)
        if guarded and _g02_foreign_run(host, self.__vd56_principal, c.get("run_owner"), active):
            return self._error(inst.instance_id, "ACCESS_DENIED", request)
        response = self._invoke_body(inst, capability, args, request)
        starts = capability == "program.start" or (capability == "drying.set_monitoring" and a.get("running") is True)
        if starts and response["status"] == "succeeded" and not active:
            c["run_owner"] = self.__vd56_principal
        return response

    def _invoke_body(self, inst, capability, args, request):
        host, s = self.__vd56_host, inst.state
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request, status)
        if capability == "pressure.program": return fail("UNSUPPORTED", "unsupported")
        if capability.startswith("panel."): return self._panel_command(inst, capability, args, request)
        if capability in _VD56_RD4_CAPS: return self._rd4_invoke(inst, capability, args, request)
        if capability in _VD56_FAULT_CAPS: return self._rd4_fault(inst, capability, args, request)
        if capability not in _VD56_CAP_ARGS: return fail("UNKNOWN_CAPABILITY")
        expected = _VD56_CAP_ARGS[capability]
        if set(args) != set(expected): return fail("INVALID_ARGUMENT")
        for k, schema in expected.items():
            if (schema["type"] == "boolean" and type(args[k]) is not bool) or (schema["type"] == "number" and not _vd56_number(args[k])): return fail("INVALID_ARGUMENT")
        value = next(iter(args.values()))
        if capability == "thermal.set_temperature":
            if not host.ambient_temperature_K + 9 <= value <= 493.15: return fail("INVALID_ARGUMENT")
            if not s["power.enabled"]: return fail("PRECONDITION_FAILED")
        elif capability == "scene.set_door":
            if not 0 <= value <= _vd56_math.radians(100): return fail("INVALID_ARGUMENT")
            if value > 0 and (s["vacuum.valve_open"] or abs(s["pressure.pressure_pa"] - host.ambient_pressure_pa) > 100): return fail("PRECONDITION_FAILED")
            if host.scene_adapter is None: return fail("UNSUPPORTED", "unsupported")
        elif capability == "vacuum.set_valve" and value:
            if not s["vacuum.external_connected"] or s["door.angle_rad"] != 0 or s["vent.valve_open"]: return fail("PRECONDITION_FAILED")
        elif capability == "vent.set_valve" and value and s["vacuum.valve_open"]: return fail("PRECONDITION_FAILED")
        if capability != "scene.set_door" and not s["power.mains_connected"]: return fail("PRECONDITION_FAILED")   # controller without mains
        if capability in ("vacuum.set_valve", "vent.set_valve") and (s["pressure.control_enabled"] or (value and s["functions.valves_closed"])): return fail("PRECONDITION_FAILED")
        if capability in ("vacuum.set_valve", "vent.set_valve") and value and not s["power.enabled"]: return fail("PRECONDITION_FAILED")   # standby: all valves closed (p.49)
        output_key, sid = _VD56_CAP_RESULTS[capability]
        updates = {sid: value}
        if capability == "power.set" and not value:                         # standby (p.49): programs and drying end; all valves close, the pump socket is off
            if s["program.running"] != "none": updates["program.running"] = "none"
            if s["drying.active"]: updates["drying.active"] = False
            updates.update({k: False for k in ("vacuum.valve_open", "vent.valve_open") if s[k]})
        if host.scene_adapter:
            try: host.scene_adapter(inst.instance_id, _vd56_copy.deepcopy(updates))
            except Exception: return fail("DEVICE_ERROR", "failed")
        s.update(updates)
        inst.revision += 1
        if capability in ("power.set", "thermal.set_temperature"):        # tolerance alarms wait until the (new) set point is reached (p.101)
            host._rd4[inst.instance_id].update(t_reached=False, t_out_s=0.0)
        return self._envelope(inst.instance_id, request, result={output_key: value})

    def _panel_command(self, inst, capability, args, request):
        """panel.* commands: a gesture on the instance's front panel (engine + panel_ui.json), a screen capture, or the evaluator frame."""
        host, panel = self.__vd56_host, _vd56_panel()
        if capability not in panel["caps"]: return self._error(inst.instance_id, "UNKNOWN_CAPABILITY", request)
        if not self._visible(panel["caps"][capability]): return self._error(inst.instance_id, "ACCESS_DENIED", request)
        host._panel_caller = self                                           # a gesture acts as the session that makes it (access rules)
        try:
            result, error = _g02_panel_command(panel, host.panel(inst.instance_id), capability, args, instance_id=inst.instance_id, time_s=host.time_s,
                                               capture_dir=host.panel_capture_dir)
        finally:
            host._panel_caller = None
        return self._error(inst.instance_id, error, request) if error else self._envelope(inst.instance_id, request, result=result)

    def _rd4_fault(self, inst, capability, args, request):
        """Evaluator fault injection (fault.inject) and the service repair of a sensor (fault.clear); the device reacts on the next step and the
        readings change at once."""
        host, s = self.__vd56_host, inst.state
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request, status)   # noqa: E731
        props, *_ = _VD56_FAULT_CAPS[capability]
        if not self._visible({"visibility": "evaluator"}): return fail("ACCESS_DENIED")
        schema = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
        if not isinstance(args, dict) or _g02_engine().check_arguments(schema, args): return fail("INVALID_ARGUMENT")
        code = args["code"]
        if capability == "fault.clear" and code not in s["fault.injected"]: return fail("PRECONDITION_FAILED")
        faults = sorted(set(s["fault.injected"]) | {code}) if capability == "fault.inject" else [f for f in s["fault.injected"] if f != code]
        updates = {"fault.injected": faults}
        if code == "temperature_sensor":
            updates["thermal.indicated_temperature_k"] = None if code in faults else round(s["thermal.temperature_k"] - 273.15, 1) + 273.15
        if code == "pressure_sensor":
            updates["pressure.indicated_pressure_pa"] = None if code in faults else round(s["pressure.pressure_pa"])
            if code in faults and s["pressure.control_enabled"]: updates.update({"vacuum.valve_open": False, "vent.valve_open": False})
        if code == "overtemperature":
            host._rd4[inst.instance_id]["otp_restarted"] = False
        changed = {k: v for k, v in updates.items() if v != s[k]}
        if changed and host.scene_adapter:
            try: host.scene_adapter(inst.instance_id, _vd56_copy.deepcopy(changed))
            except Exception: return fail("DEVICE_ERROR", "failed")
        if changed:
            s.update(changed)
            inst.revision += 1
        return self._envelope(inst.instance_id, request, result={"code": code})

    def _rd4_invoke(self, inst, capability, args, request):
        """The RD4 controller commands: checked against their schemas and preconditions, then committed in one update (scene callback first)."""
        host, s = self.__vd56_host, inst.state
        c = host._rd4[inst.instance_id]
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request, status)
        kind, props, optional, *_ = _VD56_RD4_CAPS[capability]
        schema = {"type": "object", "properties": props, "required": [] if optional == "*" else [k for k in props if k not in optional], "additionalProperties": False}
        if not isinstance(args, dict) or _g02_engine().check_arguments(schema, args) or (optional == "*" and not args): return fail("INVALID_ARGUMENT")
        if capability not in ("power.set_mains", "usb.set_stick", "usb.read_stick") and not s["power.mains_connected"]: return fail("PRECONDITION_FAILED")
        updates, result, post = {}, _vd56_copy.deepcopy(args), []
        if capability == "power.set_mains":
            updates["power.mains_connected"] = args["connected"]
            if not args["connected"] and s["power.mains_connected"]: post.append(lambda: c.update(unplugged_s=host.time_s))
            if not args["connected"] and s["pressure.control_enabled"]: updates.update({"vacuum.valve_open": False, "vent.valve_open": False})
            if args["connected"] and not s["power.mains_connected"]: post.append(lambda: host._rd4_power_return(inst))
        elif capability == "thermal.set_control":
            updates["thermal.control_enabled"] = args["enabled"]
        elif capability == "pressure.set_setpoint":
            updates["pressure.target_pa"] = float(args["pressure_Pa"])
            post.append(lambda: c.update(p_reached=False, p_out_s=0.0))
        elif capability == "pressure.set_control":
            updates["pressure.control_enabled"] = args["enabled"]
            if not args["enabled"]: updates.update({"vacuum.valve_open": False, "vent.valve_open": True, "drying.active": False})   # ventilated (p.64-65)
            post.append(lambda: c.update(p_reached=False, p_out_s=0.0))
        elif capability == "rinsing.set_percent":
            updates["rinsing.percent"] = float(args["percent"])
        elif capability == "functions.configure":
            for key, sid in (("gas_air2", "functions.gas_air2"), ("close_all_valves", "functions.valves_closed"), ("vacuum_pump_off", "functions.pump_off")):
                if key in args: updates[sid] = args[key]
            if updates.get("functions.valves_closed"): updates.update({"vacuum.valve_open": False, "vent.valve_open": False})
        elif capability == "safety.configure":
            for key, sid in (("mode", "safety.mode"), ("limit_K", "safety.limit_k"), ("offset_K", "safety.offset_k")):
                if key in args: updates[sid] = float(args[key]) if key != "mode" else args[key]
        elif capability == "messages.acknowledge":
            remaining = [x for x in s["alarm.latched"] if x in s["alarm.active"]]
            updates.update({"alarm.latched": remaining, "alarm.acknowledged": list(remaining), "alarm.buzzer": False, "info.pending": []})
            result = {"remaining_alarms": remaining}
        elif capability == "drying.set_monitoring":
            if args["running"]:
                if not (s["power.enabled"] and s["pressure.control_enabled"]) or s["functions.valves_closed"]: return fail("PRECONDITION_FAILED")
                updates["drying.active"] = True
                post.append(lambda: c.update(plateau_since=None))
            elif s["drying.active"]:
                updates.update({"drying.active": False, "pressure.target_pa": float(s["pressure.pressure_pa"])})   # p.66: actual pressure becomes the set point
        elif capability == "program.load":
            body = args.get("sections" if args["kind"] == "time" else "points")
            if body is None or ("points" if args["kind"] == "time" else "sections") in args: return fail("INVALID_ARGUMENT")
            if args["kind"] == "week" and not all(p["time"][:2].isdigit() and p["time"][3:].isdigit() and int(p["time"][:2]) < 24 and int(p["time"][3:]) < 60 and p["time"][2] == ":"
                                                   and len(p["time"]) == 5 for p in body): return fail("INVALID_ARGUMENT")
            if args["kind"] == "time" and any(x["temperature_K"] < host.ambient_temperature_K + 9.0 for x in body): return fail("INVALID_ARGUMENT")
            if s["program.running"] == args["kind"]: return fail("PRECONDITION_FAILED")
            updates["program.loaded"] = sorted(set(s["program.loaded"]) | {args["kind"]})
            post.append(lambda: c["programs"].update({args["kind"]: {("sections" if args["kind"] == "time" else "points"): _vd56_copy.deepcopy(body)}}))
            result = {"kind": args["kind"]}
        elif capability == "program.start":
            if not s["power.enabled"] or args["kind"] not in s["program.loaded"]: return fail("PRECONDITION_FAILED")
            updates.update({"program.running": args["kind"], "program.elapsed_s": 0.0, "info.pending": s["info.pending"] + ["program_started"]})
            post.append(lambda: c.update(program_start_s=host.time_s, program_from=(s["thermal.target_k"], s["pressure.target_pa"])))
        elif capability == "program.stop":
            updates["program.running"] = "none"
            result = {"running": "none"}
        elif capability == "settings.configure":
            for key, value in args.items():
                if key in ("recorder_interval_s", "temperature_alarm_delay_s", "pressure_alarm_delay_s") and value % 60: return fail("INVALID_ARGUMENT")
                updates[_VD56_SETTINGS[key][0]] = float(value) if isinstance(value, float) or key.endswith(("_s", "_K", "_Pa", "_percent")) else value
        elif capability in ("clock.set_date", "clock.set_time"):
            now = host._rd4_clock(inst)
            try:
                new = now.replace(year=args["year"], month=args["month"], day=args["day"]) if capability == "clock.set_date" else now.replace(hour=args["hour"], minute=args["minute"], second=0, microsecond=0)
            except ValueError:
                return fail("INVALID_ARGUMENT")
            updates["clock.offset_s"] = s["clock.offset_s"] + (new - now).total_seconds()
            result = {"date": new.strftime("%Y-%m-%d")} if capability == "clock.set_date" else {"time": new.strftime("%H:%M")}
        elif capability == "access.set_password":
            updates[f"access.{args['level']}_password"] = int(args["code"])
            result = {"level": args["level"]}
        elif capability == "network.configure":
            for key in ("ip_mode", "dns_mode"):
                if key in args: updates["network." + key] = args[key]
            for key in ("ip_address", "subnet_mask", "gateway", "dns_server"):
                if key in args and not all(float(x).is_integer() for x in args[key]): return fail("INVALID_ARGUMENT")
                if key in args: updates["network." + key] = ".".join(str(int(x)) for x in args[key])
            result = {k: updates.get("network." + k, s["network." + k]) for k in ("ip_address", "dns_server")}
        elif capability == "usb.set_stick":
            updates["usb.stick_inserted"] = args["inserted"]
        elif capability == "usb.read_stick":
            return self._envelope(inst.instance_id, request, result={"files": _vd56_copy.deepcopy(c["stick"])})
        elif capability == "usb.export":
            if not s["usb.stick_inserted"]: return fail("PRECONDITION_FAILED")
            return self._envelope(inst.instance_id, request, result={"files": host._rd4_export(inst, args["content"])})
        elif capability == "usb.import_configuration":
            config = c["stick"].get("KONF380.set") if s["usb.stick_inserted"] else None
            if not isinstance(config, dict): return fail("PRECONDITION_FAILED")
            try:
                for key, value in config["settings"].items():
                    if _g02_engine().check_arguments(_VD56_SETTINGS[key][1], value): return fail("PRECONDITION_FAILED")
                    updates[_VD56_SETTINGS[key][0]] = value
                updates.update({"thermal.target_k": max(host.ambient_temperature_K + 9.0, min(493.15, float(config["setpoints"]["temperature_K"]))),
                                "pressure.target_pa": float(config["setpoints"]["pressure_Pa"]), "rinsing.percent": float(config["setpoints"]["rinsing_percent"]),
                                "safety.mode": config["safety"]["mode"], "safety.limit_k": float(config["safety"]["limit_K"]), "safety.offset_k": float(config["safety"]["offset_K"])})
            except (KeyError, TypeError, ValueError):
                return fail("PRECONDITION_FAILED")
            post.append(lambda: c["recorder"].update(dl1=[], dl2=[], events=[]))
            post.append(lambda: host._rd4_event(inst, host.time_s, "New config (USB)"))
            result = {"file_name": "KONF380.set"}
        changed = {k: v for k, v in updates.items() if v != s[k]}
        if changed and host.scene_adapter:
            try: host.scene_adapter(inst.instance_id, _vd56_copy.deepcopy(changed))
            except Exception: return fail("DEVICE_ERROR", "failed")
        if changed:
            s.update(changed)
            inst.revision += 1
        for action in post: action()
        return self._envelope(inst.instance_id, request, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__vd56_host._lock:
            if not self._instance(instance_id): return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slot = BINDER_VD56_STATE_SPECS.get(state_id) if isinstance(state_id, str) else None
            if not slot or not self._visible(slot) or slot["access"] != "read_write": return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slot["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)


def validate_binder_vd56_runtime(output=None):
    """Execute VD56-only tests; artifact has synthetic results, no live truth."""
    import importlib.util as _vd56_util
    import io as _vd56_io
    import json as _vd56_json
    import platform as _vd56_platform
    import sys as _vd56_sys
    import unittest as _vd56_unittest
    from datetime import datetime as _vd56_datetime, timezone as _vd56_timezone
    from pathlib import Path as _VD56Path
    path = _VD56Path(__file__).resolve().parents[1] / "tests" / "test_basic40_thermal.py"
    spec = _vd56_util.spec_from_file_location("test_basic40_thermal", path)
    module = _vd56_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    records = []
    class _VD56RecordingResult(_vd56_unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            records.append({"id": test._testMethodName, "status": "passed", "layer": "process"})
        def addFailure(self, test, error):
            super().addFailure(test, error)
            records.append({"id": test._testMethodName, "status": "failed", "layer": "process"})
        def addError(self, test, error):
            super().addError(test, error)
            records.append({"id": test._testMethodName, "status": "failed", "layer": "process"})
    suite = _vd56_unittest.defaultTestLoader.loadTestsFromTestCase(module.BinderVD56RuntimeTests)
    stream = _vd56_io.StringIO()
    result = _vd56_unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=_VD56RecordingResult).run(suite)
    mapping = {"test_vd56_power_leaves_external_pump_and_valves_independent": ["power.set"], "test_vd56_temperature_target_only_and_80_minute_calibration": ["thermal.set_temperature"],
        "test_vd56_door_requires_backfill_and_scene_sync": ["scene.set_door"], "test_vd56_vacuum_requires_connection_and_atomic_prechecks": ["vacuum.set_valve"],
        "test_vd56_isolated_leak_and_backfill_are_time_driven": ["vent.set_valve"], "test_vd56_pressure_program_declared_unsupported": ["pressure.program"]}
    host_checks = {"test_vd56_agent_truth_identity_and_log_isolation", "test_vd56_host_configuration_prevalidates_every_field",
        "test_vd56_invalid_time_and_zero_step_leave_state_unchanged", "test_vd56_read_describe_logs_do_not_advance_or_mutate",
        "test_vd56_real_result_and_observation_schemas", "test_vd56_request_replay_and_conflict_preserve_state", "test_vd56_write_routes_and_cannot_change_truth"}
    for record in list(records):
        if record["id"] in host_checks: record["layer"] = "host"
        for cap in mapping.get(record["id"], []): records.append({**record, "capability_id": cap})
        if record["id"] == "test_vd56_real_result_and_observation_schemas":
            # This test actually invokes every implemented capability and checks
            # its real result envelope against the exported schema.
            for cap in _VD56_CAP_ARGS: records.append({**record, "capability_id": cap})
    report = {"catalog_id": "CHEM-012", "asset_type_id": BINDER_VD56_ASSET_ID, "checked_at": _vd56_datetime.now(_vd56_timezone.utc).isoformat(),
        "method": "Python unittest; synthetic empty-oven and explicit external-pressure fixtures", "environment": {"python": _vd56_sys.version, "platform": _vd56_platform.platform(), "scene_backend": "recording callback; no Blender run in this suite"},
        "status": "passed" if result.wasSuccessful() else "failed", "tests_run": result.testsRun, "checks": records, "log": stream.getvalue(),
        "not_run": ["evaluated Blender poses", "contact dynamics", "real hardware", "robot", "sample evaporation", "gas mass/species conservation"], "privacy": "Synthetic test identifiers/results only; no live instance values or host evidence strings"}
    if output is not None:
        target = _VD56Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_vd56_json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report

# BEGIN CHEM-187 JULABO CORIO CD-200F. Independent append-only runtime.
# Front panel (panel.press / panel.turn / panel.switch / panel.capture / panel.frame): the LED segment display, Up / Down / OK / Service keys,
# power switch with its power-on key combinations, high temperature cut-off screw and flow lever of the operating manual (panel_ui.json beside
# the contract), run by the generic engine scripts/asset_runtime/_panel_ui.py on top of the capabilities below (no second copy of the logic).
# Device functions of the manual behind the panel (all settable through the API as well): timer 0-999 min (starts after 30 s within 0.1 K of the
# set point, standby when elapsed), cooling mode AUTO / CON / COFF, power supply hHI / hLO (heater limited while the compressor shares the
# supply), autostart, remote mode (keypad locked), high temperature cut-off with alarm E14, one-point sensor adjustment (ATC), internal/external
# flow lever, data recording and black box export to a simulated USB flash drive.
import copy as _julabo_copy
import hashlib as _julabo_hashlib
import json as _julabo_json
import math as _julabo_math
import tempfile as _julabo_tempfile
import threading as _julabo_threading
from pathlib import Path as _julabo_Path

JULABO_CORIO_CD200F_TYPE = 'instrument.julabo_corio_cd200f'
JULABO_CORIO_CD200F_SOURCE = 'https://www.julabo.com/en/products/refrigerated-circulators/refrigerated-heating-circulators/corio-cd-200f'
JULABO_CORIO_CD200F_MANUAL = 'references/manuals/julabo_corio_cd200f/operating_manual__corio_cd_us.pdf'
JULABO_CORIO_CD200F_ASSET_DIR = _julabo_Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t1_thermal/julabo_corio_cd200f'
JULABO_CORIO_CD200F_COOLING_POINTS = ((253.15, 60.0), (263.15, 130.0), (273.15, 170.0), (293.15, 220.0))
JULABO_CORIO_CD200F_MAX_FLOW = 15.0 / 60000.0
JULABO_CORIO_CD200F_MAX_PRESSURE = 35000.0
JULABO_CORIO_CD200F_HEATER_W = 2000.0
JULABO_CORIO_CD200F_COMPRESSOR_W = 460.0          # electrical draw of the cooling machine: 14 A (CD-200F, 115 V) - 10 A (circulator alone) at 115 V
JULABO_CORIO_CD200F_CUTOFF_K = (273.15, 523.15)   # cut-off scale 0-250 C (manual p.51 photo)
JULABO_CORIO_CD200F_TIMER_MAX_S = 999 * 60.0      # 0-999 min (manual p.64)
JULABO_CORIO_CD200F_TIMER_BAND_K, JULABO_CORIO_CD200F_TIMER_HOLD_S = 0.1, 30.0
JULABO_CORIO_CD200F_ATC_WINDOW_K = 5.0            # reference within +-5 C of the set point (manual p.65)
JULABO_CORIO_CD200F_SENSOR_ERROR = (5.0, 0.05)   # evaluator-set probe error bounds: offset K, relative gain (default 0 = ideal)
JULABO_CORIO_CD200F_LOG_INTERVAL_S = 10.0         # recording interval: not stated in the manual (assumption)
JULABO_CORIO_CD200F_BLACKBOX_S = 1200.0           # black box keeps the last 20 minutes (manual p.62)
JULABO_CORIO_CD200F_ALARM_CUTOFF = 14             # E14: set protection temperature exceeded (manual p.76)
JULABO_CORIO_CD200F_RESTART_S = 4.0                # p.77-79: 'switch off the device at the power switch, wait 4 seconds and then switch it back on'
# Faults of the code table (manual p.76-80) that this configuration can show, for the evaluator-only fault.inject: code -> (kind, cleared by, text).
# kind: 'warning' where the table names a warning threshold, else 'alarm' (p.56: alarm = control stopped, LED, restart required; warning = control goes
# on, the display alternates temperature and code); cleared by: 'restart' = switching off for at least 4 s and on again (the table's action), 'fix' /
# 'service' = the operator's action or JULABO Service (fault.clear), after which an alarm still needs the restart.  Not offered: warning / alarm limits
# E03 / E04 / E143 / E144 (no procedure, p.7.9), external sensor E15 / E38 / E142 (no Pt100 input on the CORIO CD), level systems E40 / E41 (this unit has
# the float, E01), external pressure circuit E301 / E302 / E1502 / E1503, analog module E503-E505, interface watchdog E1501, CFG / ProG errors.
_JULABO_FAULTS = {
    1: ('alarm', 'fix', 'level below minimum: top up the medium'), 5: ('alarm', 'service', 'overtemperature protection working sensor open / short'),
    6: ('alarm', 'fix', 'working and safety sensor differ too much: circulation / viscosity'), 33: ('alarm', 'service', 'safety sensor shorted or broken'),
    60: ('alarm', 'restart', 'internal read / write error'), 61: ('alarm', 'fix', 'connection circulator - cooling machine (or cooling off: heating thermostat)'),
    62: ('alarm', 'restart', 'CAN bus error'), 63: ('alarm', 'restart', 'watchdog triggered'), 70: ('alarm', 'fix', 'incompatible voltage / frequency or configuration'),
    72: ('alarm', 'restart', 'configuration circulator - cooling machine failed'), 83: ('alarm', 'fix', 'excessive power draw on the USB interface'),
    104: ('alarm', 'service', 'AD conversion of the overtemperature protection'), 106: ('alarm', 'service', 'internal temperature sensor'),
    107: ('alarm', 'service', 'internal temperature measurement'), 108: ('alarm', 'restart', 'safety device self-locking still active'),
    112: ('alarm', 'service', 'internal temperature sensor'), 114: ('alarm', 'service', 'internal temperature sensing'), 115: ('alarm', 'service', 'internal temperature sensing'),
    116: ('alarm', 'restart', 'protective device self-locking still active'), 118: ('alarm', 'service', 'internal temperature sensor'),
    427: ('alarm', 'restart', 'condensation pressure above the maximum'), 431: ('alarm', 'fix', 'compressor current above the maximum'),
    999: ('alarm', 'restart', 'internal error'), 1103: ('alarm', 'service', 'level detection error'), 1109: ('alarm', 'fix', 'viscosity too high or circulation insufficient'),
    1217: ('alarm', 'service', 'mains frequency measurement'), 1218: ('alarm', 'service', 'mains frequency measurement'),
    1302: ('alarm', 'fix', 'pressure below the heating cut-off threshold'), 1305: ('alarm', 'fix', 'pump speed too low'),
    1427: ('warning', 'fix', 'condensation pressure warning threshold'), 1431: ('alarm', 'fix', 'compressor current below the minimum'),
}
# Remote interface (sweep 2026-10-19, lead rule 5; manual p.63, p.83-84: OUT commands only in remote control mode rOn): an agent session's direct
# device commands stand for the USB interface and are refused (PRECONDITION_FAILED) while remote is off.  Reads, physical actions (power switch,
# bath lid, flow lever, cut-off screw, flash drive), the remote switch itself (the operator's double-arrow chord), panel gestures (local keys) and
# the evaluator are not gated.
_JULABO_REMOTE_COMMANDS = ('thermal.set_temperature', 'circulation.set_running', 'cooling.set_mode', 'power.set_supply_mode', 'power.set_autostart',
                           'timer.set', 'sensor.calibrate', 'logging.set_recording', 'blackbox.export')
_JULABO_REMOTE_PRECONDITION = {'description_zh': '直接调用代表USB接口的远程命令：须远程控制模式rOn（手册p.63、p.83–84：OUT命令只在远程模式下执行）；'
                                                 '面板手势（本机按键）与评测者不受此限，读取不受限',
                               'state_ids': ['interface.remote'], 'enforced_by': 'adapter', 'check': 'interface.remote (agent session, direct path)'}


def _julabo_number(value):
    try:
        return type(value) in (int, float) and _julabo_math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _julabo_schema(unit, low=None, high=None, nullable=False):
    result = {'type': 'number', 'x-unit': unit}
    if low is not None: result['minimum'] = low
    if high is not None: result['maximum'] = high
    return {'anyOf': [result, {'type': 'null'}]} if nullable else result


def _julabo_object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _julabo_enum(*values):
    return {'type': 'string', 'enum': list(values)}


def _julabo_slot(unit, origin, component, meaning, *, nullable=False, boolean=False, route=None, integral=False, schema=None, visibility=None):
    return {'unit': unit, 'origin': origin, 'visibility': visibility or ('debug' if origin == 'process_truth' else 'agent'),
        'access': 'read_write' if route else 'read_only', 'write_via': route,
        'value_schema': schema if schema is not None else {'type': 'boolean'} if boolean else _julabo_schema(unit, nullable=nullable),
        'component_ids': [component], 'meaning_zh': meaning,
        'sampling': {'mode': 'integral' if integral else 'instant', 'time_domain': 'simulation', 'window': 'Since latest trusted bath configuration' if integral else None},
        'reference_conditions': 'Pump differential pressure; not absolute pressure' if unit == 'Pa' else 'Actual liquid volume flow at host-configured operating point' if unit == 'm^3/s' else 'Bath cover relative to base; positive q maps to Blender rotation_x=-q' if unit == 'rad' else None}


_JULABO_PANEL = {}


def _julabo_panel():
    """The panel engine and this asset's panel spec, loaded on first use (a broken spec must not stop the other instruments of this file)."""
    if not _JULABO_PANEL:
        try:
            from asset_runtime import _panel_ui as engine
        except ImportError:                                                   # scripts/ not on sys.path: load the engine from its file
            import importlib.util
            import sys
            if '_chemsim_panel_ui' not in sys.modules:
                spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', _julabo_Path(__file__).resolve().parent / 'asset_runtime/_panel_ui.py')
                sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
            engine = sys.modules['_chemsim_panel_ui']
        caps = set(export_julabo_corio_cd200f_metadata(panel=False)['asset_types'][JULABO_CORIO_CD200F_TYPE]['capabilities'])
        spec = engine.load_spec(JULABO_CORIO_CD200F_ASSET_DIR / 'panel_ui.json', capabilities=caps)
        _JULABO_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _JULABO_PANEL


def export_julabo_corio_cd200f_metadata(panel=True):
    """Static contract metadata; no instance configuration or privileged truth.  panel_capabilities: the front-panel commands (panel.*)."""
    def route(capability, parameter): return {'capability_id': capability, 'parameter': parameter, 'fixed_arguments': {}}
    slots = {
        'power.enabled': _julabo_slot('1', 'setpoint', 'controller', '本机供电许可', boolean=True, route=route('power.set', 'enabled')),
        'thermal.target_k': _julabo_slot('K', 'setpoint', 'controller', '只写入温度目标；实际温度由宿主时间推进', route=route('thermal.set_temperature', 'temperature_K')),
        'thermal.temperature_k': _julabo_slot('K', 'process_truth', 'bath_vessel', '均匀混合浴液有效温度；未配置热模型为null', nullable=True),
        'thermal.indicated_temperature_k': _julabo_slot('K', 'derived', 'display', '0.01°C网格取整的理想温度显示代理；非传感器精度模型', nullable=True),
        'thermal.model_configured': _julabo_slot('1', 'derived', 'bath_vessel', '宿主已显式配置有效热容和热损失', boolean=True),
        'thermal.cooling_curve_supported': _julabo_slot('1', 'derived', 'refrigeration_unit', '温度位于有据制冷功率插值域且环境为20°C；仅乙醇参考等效模型', boolean=True),
        'bath_lid.angle_rad': _julabo_slot('rad', 'scene', 'bath_lid', '浴盖角度；宿主同步至bath_lid_joint负X轴', route=route('scene.set_bath_lid', 'angle_rad')),
        'circulation.running': _julabo_slot('1', 'setpoint', 'circulation_pump', '模拟循环许可；不代表已解算外部回路流动', boolean=True, route=route('circulation.set_running', 'running')),
        'circulation.external_connected': _julabo_slot('1', 'derived', 'pump_supply', '宿主已配置外部流量/压差边界；并非真实管线连接检测', boolean=True),
        'circulation.flow_rate_m3_s': _julabo_slot('m^3/s', 'derived', 'circulation_pump', '宿主给定运行点的实际体积流量；未配置运行点为null，停止为0；非流量传感器', nullable=True),
        'circulation.pressure_rise_pa': _julabo_slot('Pa', 'derived', 'circulation_pump', '宿主给定运行点的泵压差；未配置运行点为null，停止为0；非压力传感器', nullable=True),
    }
    for name, meaning in (('heater_energy_j', '累计加热输入'), ('cooling_energy_j', '累计制冷移除热量'), ('environment_energy_j', '累计向环境净放热，可为负'), ('stored_energy_change_j', '相对本次配置初态的浴液储能变化')):
        slots['thermal.' + name] = _julabo_slot('J', 'process_truth', 'bath_vessel', meaning, integral=True)
    slots.update({
        'circulation.flow_path': _julabo_slot('1', 'setpoint', 'pump_selector', '内外循环拨杆：external时外循环取宿主运行点，internal时液体留在浴内、外循环流量与压差为0',
            schema=_julabo_enum('external', 'internal'), route=route('circulation.set_flow_path', 'path')),
        'cooling.mode': _julabo_slot('1', 'setpoint', 'refrigeration_unit', '冷却模式：auto升温时制冷机关闭；on常开（升温时与加热同时运行）；off常关（不能主动制冷）',
            schema=_julabo_enum('auto', 'on', 'off'), route=route('cooling.set_mode', 'mode')),
        'power.supply_mode': _julabo_slot('1', 'setpoint', 'power_inlet', '供电方式：shared（hLO，循环器经制冷机供电，压缩机运行时加热功率受限）；separate（hHI，各自供电，全加热功率）',
            schema=_julabo_enum('separate', 'shared'), route=route('power.set_supply_mode', 'mode')),
        'power.autostart': _julabo_slot('1', 'setpoint', 'controller', '自动启动：开机后按已存设定直接开始控温', boolean=True, route=route('power.set_autostart', 'enabled')),
        'interface.remote': _julabo_slot('1', 'setpoint', 'usb_port', '远程控制模式rOn：本机按键锁定（双箭头组合除外），直接调用的设备命令（代表USB接口OUT命令）只在此模式下执行', boolean=True, route=route('interface.set_remote', 'enabled')),
        'safety.cutoff_k': _julabo_slot('K', 'setpoint', 'safety_cutoff', '独立超温切断设定（螺丝刀旋钮）；浴温达到即报警E14、停止控温', route=route('safety.set_cutoff', 'temperature_K')),
        'alarm.code': _julabo_slot('1', 'derived', 'display', '当前报警码（0为无报警；14为超温切断；其余为手册p.76–80的故障码，只由评测者fault.inject注入）；报警停止控温，关机复位（原因仍在则开机再报）',
            schema={'type': 'integer', 'minimum': 0, 'maximum': 9999}),
        'warning.code': _julabo_slot('1', 'derived', 'display', '当前警告码（0为无；如E1427冷凝压力警告阈值）：警告不中断控温，显示交替实际温度与代码，原因排除后消失（p.56）',
            schema={'type': 'integer', 'minimum': 0, 'maximum': 9999}),
        'fault.injected': _julabo_slot('1', 'process_truth', 'controller', '评测者注入、尚未排除的故障原因（故障码列表）', schema={'type': 'array', 'items': {'type': 'integer'}},
            visibility='debug'),
        'timer.duration_s': _julabo_slot('s', 'setpoint', 'controller', '已编程的定时控温时长（整分钟；0为无定时）', route=route('timer.set', 'duration_s')),
        'timer.phase': _julabo_slot('1', 'derived', 'controller', '定时状态：off无定时；waiting等待设定点±0.1 K保持30 s；running倒计时', schema=_julabo_enum('off', 'waiting', 'running')),
        'timer.remaining_s': _julabo_slot('s', 'derived', 'controller', '定时剩余时间；到时转入待机'),
        'sensor.atc_offset_k': _julabo_slot('K', 'setpoint', 'temperature_probe', 'ATC单点校准存储的修正值；显示与控温使用修正后的读数'),
        'sensor.offset_error_k': _julabo_slot('K', 'process_truth', 'temperature_probe', '探头系统误差的偏置（评测者设定，默认0即理想）：读数 = T + 偏置 + 增益误差×T(°C) + ATC修正'),
        'sensor.gain_error': _julabo_slot('1', 'process_truth', 'temperature_probe', '探头系统误差的相对增益误差（评测者设定，默认0即理想）'),
        'usb.flash_drive_inserted': _julabo_slot('1', 'scene', 'usb_port', 'USB-A口是否插着U盘（模拟U盘）', boolean=True, route=route('usb.set_flash_drive', 'inserted')),
        'logging.recording': _julabo_slot('1', 'setpoint', 'usb_port', '正在向U盘记录测量数据（LOG I）', boolean=True, route=route('logging.set_recording', 'enabled')),
        'indicator.heating': _julabo_slot('1', 'derived', 'heating_led', '加热控制指示灯（加热器在输出）', boolean=True),
        'indicator.cooling': _julabo_slot('1', 'derived', 'cooling_led', '制冷控制指示灯（制冷机在运行）', boolean=True),
    })
    for sid, slot in slots.items():
        slot['read_binding'] = f"JulaboCorioCD200FSession.read(instance_id=..., state_ids=['{sid}'])"
        if slot['origin'] == 'derived': slot['observation_note_zh'] = '仿真派生量quality=estimated，缺测quality=unavailable；不声称实测传感器数据。'
    timer_ids, led_ids = ['timer.duration_s', 'timer.phase', 'timer.remaining_s'], ['indicator.heating', 'indicator.cooling']
    definitions = {
        'power.set': ({'enabled': {'type': 'boolean'}}, 'enabled', 'power.enabled', {'enabled': True}, ['controller', 'heater', 'refrigeration_unit', 'circulation_pump'], '设置供电；断电停止循环，保留温度与累计热量', ['power.enabled', 'circulation.running', 'circulation.flow_rate_m3_s', 'circulation.pressure_rise_pa']),
        'thermal.set_temperature': ({'temperature_K': _julabo_schema('K', 253.15, 423.15)}, 'setpoint_K', 'thermal.target_k', {'temperature_K': 313.15}, ['controller', 'display', 'heater', 'refrigeration_unit', 'bath_vessel'], '只写温度目标，需本机已上电；已配置流体时还必须处于其明确适用温区', ['thermal.target_k']),
        'circulation.set_running': ({'running': {'type': 'boolean'}}, 'running', 'circulation.running', {'running': True}, ['circulation_pump', 'pump_supply', 'pump_return'], '设置模拟循环许可；开启须已供电且浴热模型已配置；开启后才允许热输入', ['circulation.running', 'circulation.flow_rate_m3_s', 'circulation.pressure_rise_pa']),
        'scene.set_bath_lid': ({'angle_rad': _julabo_schema('rad', 0, _julabo_math.pi / 2)}, 'angle_rad', 'bath_lid.angle_rad', {'angle_rad': .5}, ['bath_lid'], '设置浴盖角度；须有同步场景回调，正角度映射负X旋转；不是机器人执行', ['bath_lid.angle_rad']),
    }
    added = {   # effects of the device functions behind the front panel on the existing commands (new slots only; the old effects are unchanged)
        'power.set': ['alarm.code', 'warning.code'] + timer_ids + ['logging.recording'] + led_ids,
        'thermal.set_temperature': timer_ids + led_ids,
        'circulation.set_running': timer_ids + led_ids,
        'scene.set_bath_lid': led_ids,
    }
    new = {
        'circulation.set_flow_path': ({'path': _julabo_enum('external', 'internal')}, 'path', 'circulation.flow_path', {'path': 'external'}, ['pump_selector', 'circulation_pump', 'pump_supply', 'pump_return'],
            '拨动内外循环拨杆（手册p.54）：internal时外循环流量与压差为0', ['circulation.flow_path', 'circulation.flow_rate_m3_s', 'circulation.pressure_rise_pa']),
        'cooling.set_mode': ({'mode': _julabo_enum('auto', 'on', 'off')}, 'mode', 'cooling.mode', {'mode': 'auto'}, ['controller', 'refrigeration_unit'],
            '冷却模式（手册p.55）：auto升温时制冷机关闭；on常开（升温时加热与制冷同时运行）；off常关', ['cooling.mode'] + led_ids),
        'power.set_supply_mode': ({'mode': _julabo_enum('separate', 'shared')}, 'mode', 'power.supply_mode', {'mode': 'shared'}, ['controller', 'heater', 'power_inlet'],
            '供电方式（手册p.53）：shared即hLO，压缩机运行时加热功率减去压缩机电功率；separate即hHI，全2 kW', ['power.supply_mode']),
        'power.set_autostart': ({'enabled': {'type': 'boolean'}}, 'enabled', 'power.autostart', {'enabled': False}, ['controller', 'power_inlet'],
            '自动启动（手册p.60）：开启后每次上电按已存设定直接开始控温', ['power.autostart']),
        'timer.set': ({'duration_s': _julabo_schema('s', 0, JULABO_CORIO_CD200F_TIMER_MAX_S)}, 'duration_s', 'timer.duration_s', {'duration_s': 3600.0}, ['controller', 'display'],
            '编程定时（手册p.64）：整分钟0–999 min，0取消；设定点±0.1 K保持30 s后开始倒计时，到时转入待机；需上电', timer_ids),
        'interface.set_remote': ({'enabled': {'type': 'boolean'}}, 'enabled', 'interface.remote', {'enabled': True}, ['controller', 'usb_port'],
            '远程控制模式（手册p.63、p.83–84）：开启后本机按键锁定（双箭头组合除外），代理会话直接调用的设备命令（代表USB接口OUT命令）才被执行；关闭时这些命令返回PRECONDITION_FAILED，'
            '读取、物理动作与本开关不受限；需上电', ['interface.remote']),
        'safety.set_cutoff': ({'temperature_K': _julabo_schema('K', *JULABO_CORIO_CD200F_CUTOFF_K)}, 'cutoff_K', 'safety.cutoff_k', {'temperature_K': 433.15}, ['safety_cutoff', 'heater', 'controller'],
            '用螺丝刀调节超温切断值（手册p.50–51，p.69）：立即生效；浴温达到即报警E14并停止控温，关机复位', ['safety.cutoff_k', 'alarm.code', 'circulation.running', 'circulation.flow_rate_m3_s', 'circulation.pressure_rise_pa'] + timer_ids + led_ids),
        'sensor.calibrate': ({'reference_temperature_K': _julabo_schema('K', 253.15, 423.15)}, 'atc_offset_K', 'sensor.atc_offset_k', {'reference_temperature_K': 313.15}, ['temperature_probe', 'controller', 'display'],
            '温度探头单点校准ATC（手册p.65）：输入参考温度计读数，须在设定点±5 °C内，修正值立即生效；需上电且浴热模型已配置', ['sensor.atc_offset_k', 'thermal.indicated_temperature_k'] + led_ids),
        'usb.set_flash_drive': ({'inserted': {'type': 'boolean'}}, 'inserted', 'usb.flash_drive_inserted', {'inserted': True}, ['usb_port'],
            '把模拟U盘插入或拔出USB-A口（操作者动作）；拔出时停止记录', ['usb.flash_drive_inserted', 'logging.recording']),
        'logging.set_recording': ({'enabled': {'type': 'boolean'}}, 'enabled', 'logging.recording', {'enabled': True}, ['controller', 'usb_port'],
            '测量数据记录到U盘（手册p.61）：每10 s一行（时间、设定值、内部实际值、外部实际值、输出功率）；开始须上电且插着U盘', ['logging.recording']),
    }
    caps = {}
    for name, (args, output, sid, example, components, meaning, immediate) in {**definitions, **new}.items():
        pre = []
        if name == 'thermal.set_temperature': pre = [{'description_zh': '本机上电且在已配置流体适用温区内', 'state_ids': ['power.enabled', 'thermal.model_configured'], 'enforced_by': 'adapter', 'check': 'power.enabled and (not configured or fluid_min_K <= temperature_K <= fluid_max_K)'}]
        if name == 'circulation.set_running': pre = [{'description_zh': '开启需要上电与显式浴热模型；这是模拟前提，不宣称硬件液位互锁', 'state_ids': ['power.enabled', 'thermal.model_configured'], 'enforced_by': 'adapter', 'check': 'not running or (power.enabled and thermal.model_configured)'},
                                                     {'description_zh': '报警（如E14）时不能重新开始控温，须关机复位（手册p.56）', 'state_ids': ['alarm.code'], 'enforced_by': 'adapter', 'check': 'not running or alarm.code == 0'}]
        if name.startswith('scene.'): pre = [{'description_zh': '同步场景适配器存在', 'state_ids': [], 'enforced_by': 'adapter', 'check': 'scene_adapter is not None'}]
        if name in ('timer.set', 'interface.set_remote'): pre = [{'description_zh': '本机已上电', 'state_ids': ['power.enabled'], 'enforced_by': 'adapter', 'check': 'power.enabled'}]
        if name == 'timer.set': pre.append({'description_zh': '整分钟', 'state_ids': [], 'enforced_by': 'adapter', 'check': 'duration_s % 60 == 0 (else INVALID_ARGUMENT)'})
        if name == 'sensor.calibrate': pre = [{'description_zh': '本机上电、浴热模型已配置，参考温度在设定点±5 °C内', 'state_ids': ['power.enabled', 'thermal.model_configured', 'thermal.target_k'], 'enforced_by': 'adapter',
                                                'check': 'power.enabled and thermal.model_configured and abs(reference_temperature_K - thermal.target_k) <= 5'}]
        if name == 'logging.set_recording': pre = [{'description_zh': '开始记录须上电且插着U盘', 'state_ids': ['power.enabled', 'usb.flash_drive_inserted'], 'enforced_by': 'adapter', 'check': 'not enabled or (power.enabled and usb.flash_drive_inserted)'}]
        if name in _JULABO_REMOTE_COMMANDS: pre = pre + [dict(_JULABO_REMOTE_PRECONDITION)]
        effects = [{'state_id': key, 'timing': 'immediate', 'description_zh': meaning} for key in immediate + added.get(name, [])]
        effects += [{'state_id': key, 'timing': 'on_step', 'description_zh': '宿主推进后的有限功率热响应和累计能量；未配置时温度保持null'} for key in slots if key.startswith('thermal.') and key not in ('thermal.target_k', 'thermal.model_configured')]
        caps[name] = {'availability': 'implemented', 'visibility': 'agent', 'kind': 'simulation' if name.startswith(('scene.', 'usb.')) else 'device_command',
            'description_zh': meaning, 'component_ids': components, 'input_schema': _julabo_object(args), 'output_schema': _julabo_object({output: _julabo_copy.deepcopy(slots[sid]['value_schema'])}),
            'example_arguments': [example], 'observes': [sid], 'state_effects': effects, 'preconditions': pre,
            'binding': f"basic40_runtime_thermal.JulaboCorioCD200FSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
            'scene_backend_required': name.startswith('scene.'), 'completion_criterion_zh': '同步返回确认设定完成；温度变化及累计热量由随后host.step推进，read不推进时间。'}
    caps['sensor.calibrate']['output_schema'] = _julabo_object({'atc_offset_K': _julabo_schema('K')})
    record = _julabo_object({'time_s': _julabo_schema('s'), 'setpoint_K': _julabo_schema('K'), 'internal_K': _julabo_schema('K', nullable=True),
                             'external_K': _julabo_schema('K', nullable=True), 'power_fraction': _julabo_schema('1', -1, 1)})
    event = _julabo_object({'time_s': _julabo_schema('s'), 'kind': _julabo_enum('alarm', 'warning'), 'code': {'type': 'integer', 'minimum': 1}})
    file_schema = _julabo_object({'name': {'type': 'string'}, 'kind': _julabo_enum('log', 'blackbox'), 'created_s': _julabo_schema('s'), 'closed_s': _julabo_schema('s', nullable=True),
                                  'records': {'type': 'array', 'items': record}, 'events': {'type': 'array', 'items': event}})
    evaluator_caps = {'sensor.configure_error': {'availability': 'implemented', 'visibility': 'evaluator', 'kind': 'simulation',
        'description_zh': '评测者：温度探头的系统误差（读数 = T + 偏置 + 增益误差×T(°C)；默认0即理想传感器；真实测量误差留给后续“校准/仿真与现实差距”任务）',
        'component_ids': ['temperature_probe'], 'input_schema': _julabo_object({'offset_K': _julabo_schema('K', -JULABO_CORIO_CD200F_SENSOR_ERROR[0], JULABO_CORIO_CD200F_SENSOR_ERROR[0]),
                                                                                'gain': _julabo_schema('1', -JULABO_CORIO_CD200F_SENSOR_ERROR[1], JULABO_CORIO_CD200F_SENSOR_ERROR[1])}),
        'output_schema': _julabo_object({'offset_K': _julabo_schema('K'), 'gain': _julabo_schema('1')}), 'example_arguments': [{'offset_K': 0.3, 'gain': 0.002}],
        'observes': ['sensor.offset_error_k', 'sensor.gain_error'],
        'state_effects': [{'state_id': k, 'timing': 'immediate', 'description_zh': '探头系统误差；显示读数与控温随之改变'} for k in ('sensor.offset_error_k', 'sensor.gain_error', 'thermal.indicated_temperature_k') + tuple(led_ids)],
        'preconditions': [], 'binding': "basic40_runtime_thermal.JulaboCorioCD200FSession.invoke(instance_id=..., capability_id='sensor.configure_error', arguments=..., request_id=...)",
        'scene_backend_required': False, 'completion_criterion_zh': '同步返回即生效。'}}
    code_schema = {'type': 'integer', 'enum': sorted(_JULABO_FAULTS), 'x-unit': '1'}
    for name, meaning, observes in (
            ('fault.inject', '评测者：注入手册p.76–80故障表中本配置可出现的故障码（见_JULABO_FAULTS）：报警类显示“E nn”、报警LED亮、停止控温与定时，须关机复位（p.56）；'
                             '警告类（E1427）不中断控温，显示交替实际温度与代码；关机时注入则在开机时出现', ['fault.injected', 'alarm.code', 'warning.code', 'circulation.running'] + timer_ids),
            ('fault.clear', '评测者：故障原因已排除（操作者按表处理或JULABO服务维修）；警告随即消失，报警仍须关机复位（p.56）；“关机4 s再开”类原因不需要此项，按手册关机至少4 s即排除',
             ['fault.injected', 'warning.code'])):
        evaluator_caps[name] = {'availability': 'implemented', 'visibility': 'evaluator', 'kind': 'simulation', 'description_zh': meaning,
            'component_ids': ['controller', 'display', 'alarm_led'], 'input_schema': _julabo_object({'code': dict(code_schema)}),
            'output_schema': _julabo_object({'code': {'type': 'integer', 'x-unit': '1'}}), 'example_arguments': [{'code': 1 if name == 'fault.inject' else 1427}],
            'observes': ['fault.injected'], 'state_effects': [{'state_id': k, 'timing': 'immediate', 'description_zh': meaning} for k in observes], 'preconditions': [],
            'binding': f"basic40_runtime_thermal.JulaboCorioCD200FSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
            'scene_backend_required': False, 'completion_criterion_zh': '同步返回即生效。'}
    pre_drive = [{'description_zh': '本机上电且插着U盘', 'state_ids': ['power.enabled', 'usb.flash_drive_inserted'], 'enforced_by': 'adapter', 'check': 'power.enabled and usb.flash_drive_inserted'}]
    for name, kind, args, output, example, components, meaning, pre in (
            ('blackbox.export', 'device_command', {}, {'file_name': {'type': 'string'}}, {}, ['controller', 'usb_port'],
             '读出黑匣子到U盘（手册p.62）：最近20 min的记录行与报警事件写成一个文件；须上电且插着U盘', pre_drive + [dict(_JULABO_REMOTE_PRECONDITION)]),
            ('usb.read_flash_drive', 'query', {}, {'files': {'type': 'array', 'items': file_schema}}, {}, ['usb_port'],
             '读模拟U盘上的文件（相当于把U盘拿到电脑上查看）：数据记录文件与黑匣子文件', [])):
        caps[name] = {'availability': 'implemented', 'visibility': 'agent', 'kind': kind, 'description_zh': meaning, 'component_ids': components,
            'input_schema': _julabo_object(args), 'output_schema': _julabo_object(output), 'example_arguments': [example], 'observes': [], 'state_effects': [], 'preconditions': pre,
            'binding': f"basic40_runtime_thermal.JulaboCorioCD200FSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
            'scene_backend_required': False, 'completion_criterion_zh': '同步返回即完成；文件内容为调用时的模拟U盘内容。'}
    panel_caps = {}
    if panel:
        p = _julabo_panel()
        called = p['engine'].spec_capabilities(p['spec'])
        effects = list({e['state_id']: dict(e, description_zh='面板动作调用的能力的效果：' + e['description_zh']) for c in called for e in caps[c]['state_effects'] if e['timing'] == 'immediate'}.values())
        for name, d in p['caps'].items():
            gesture = name not in ('panel.capture', 'panel.frame')
            panel_caps[name] = {'availability': 'implemented', 'visibility': d['visibility'], 'kind': d['kind'], 'description_zh': d['function_zh'], 'component_ids': d['component_ids'],
                'input_schema': d['input_schema'], 'output_schema': d['output_schema'], 'example_arguments': [d['example_arguments']], 'observes': [],
                'state_effects': _julabo_copy.deepcopy(effects) if gesture else [], 'preconditions': [],
                'binding': f"basic40_runtime_thermal.JulaboCorioCD200FSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
                'scene_backend_required': False,
                'completion_criterion_zh': '手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕须用panel.capture查看' if gesture else '同步返回当前画面'}
    def p(value, unit, evidence, source): return {'value': value, 'unit': unit, 'evidence': evidence, 'source': source}
    params = {
        'heater_power_W': p(2000.0, 'W', 'public_document', JULABO_CORIO_CD200F_SOURCE + '; 9012701.N1.03 heating capacity'),
        'minimum_temperature_K': p(253.15, 'K', 'public_document', JULABO_CORIO_CD200F_SOURCE),
        'maximum_temperature_K': p(423.15, 'K', 'public_document', JULABO_CORIO_CD200F_SOURCE),
        'cooling_capacity_20C_W': p(220.0, 'W', 'public_document', 'Ethanol; ambient 20°C; DIN12876'),
        'cooling_capacity_0C_W': p(170.0, 'W', 'public_document', 'Ethanol; ambient 20°C; DIN12876'),
        'cooling_capacity_minus10C_W': p(130.0, 'W', 'public_document', 'Ethanol; ambient 20°C; DIN12876'),
        'cooling_capacity_minus20C_W': p(60.0, 'W', 'public_document', 'Ethanol; ambient 20°C; DIN12876'),
        'maximum_flow_rate_m3_s': p(JULABO_CORIO_CD200F_MAX_FLOW, 'm^3/s', 'public_document', '15 L/min; independent maximum, not simultaneous 0.35 bar'),
        'maximum_pressure_rise_Pa': p(35000.0, 'Pa', 'public_document', '0.35 bar; independent maximum, not simultaneous 15 L/min'),
        'thermal_capacity_J_K': p(None, 'J/K', 'unknown', 'No default. Host must explicitly supply effective C or fluid mass*cp; whole-instrument 26 kg is not used.'),
        'heat_loss_W_K': p(None, 'W/K', 'unknown', 'No default. Host must explicitly supply UA; graph slope alone does not identify both C and UA.'),
        'lid_open_loss_W_K': p(None, 'W/K', 'unknown', 'No default. Explicit host assumption, not measured lid heat transfer.'),
        'cooling_capacity_above20C_W': p(None, 'W', 'unknown', 'No extrapolation: refrigeration is inactive above supported curve domain and status reports unsupported; passive environmental exchange remains.'),
        'maximum_lid_angle_rad': p(_julabo_math.pi / 2, 'rad', 'assumption', 'Illustrative scene joint travel; not a manufacturer actuator limit'),
        'graph_inferred_heating_capacity_J_K': p(7200.0, 'J/K', 'derived_estimate', 'Thermal medium: C=2000W*180s/50K from approximate 100→150°C graph segment; assumes UA=0 and full heater input; not default, not component heat capacity'),
        'compressor_electrical_power_W': p(JULABO_CORIO_CD200F_COMPRESSOR_W, 'W', 'derived_estimate', 'Datasheet 115 V/60 Hz: 14 A for the CD-200F combination; manual p.17: 10 A for the circulator alone; 4 A x 115 V. Used only for the hLO heater limit.'),
        'safety_cutoff_range_K': p(list(JULABO_CORIO_CD200F_CUTOFF_K), 'K', 'public_document', 'Manual p.51 photo: cut-off scale 0-250 C; default at the clockwise stop is an assumption.'),
        'timer_range_s': p([0.0, JULABO_CORIO_CD200F_TIMER_MAX_S], 's', 'public_document', 'Manual p.64: 0-999 min; starts after 30 s within +-0.1 K of the set point.'),
        'recording_interval_s': p(JULABO_CORIO_CD200F_LOG_INTERVAL_S, 's', 'assumption', 'Manual p.61 does not state the interval.'),
        'blackbox_window_s': p(JULABO_CORIO_CD200F_BLACKBOX_S, 's', 'public_document', 'Manual p.62: data of the last 20 minutes.'),
    }
    host = {
        'sim.step': {'kind': 'simulation', 'visibility': 'trusted_host_only', 'input_schema': _julabo_object({'dt_s': _julabo_schema('s', 0, 3600)}), 'binding': 'JulaboCorioCD200FRuntime.step(dt_s)', 'partial_failure': 'Process and shared clock commit before scene callbacks; SceneSynchronizationError reports possible partial scene effects.'},
        'sim.configure_bath_model': {'kind': 'configuration', 'visibility': 'trusted_host_only', 'binding': 'JulaboCorioCD200FRuntime.configure_bath_model(instance_id, *, effective_heat_capacity_J_K, heat_loss_W_K, lid_open_loss_W_K, initial_temperature_K, minimum_temperature_K, maximum_temperature_K, evidence)', 'description_zh': '仅断电时初始化显式热模型并重置该配置的累计热量；不是加液或重置真实仪器。'},
        'sim.configure_bath_fluid': {'kind': 'configuration', 'visibility': 'trusted_host_only', 'binding': 'JulaboCorioCD200FRuntime.configure_bath_fluid(instance_id, *, mass_kg, specific_heat_J_kg_K, heat_loss_W_K, lid_open_loss_W_K, initial_temperature_K, minimum_temperature_K, maximum_temperature_K, evidence)', 'description_zh': 'C=m*cp，仅浴液单节点假设；未包含未知槽体/样品热容。'},
        'sim.configure_circulation_boundary': {'kind': 'configuration', 'visibility': 'trusted_host_only', 'binding': 'JulaboCorioCD200FRuntime.configure_circulation_boundary(instance_id, *, connected, flow_rate_m3_s=None, pressure_rise_Pa=None, evidence)', 'description_zh': '显式外部运行点；无默认泵曲线、回路阻力或物料转移解算；拒绝同时取两个厂家极值。'},
    }
    return {'asset_types': {JULABO_CORIO_CD200F_TYPE: {'catalog_id': 'CHEM-187', 'model': 'JULABO CORIO CD-200F 9012701.N1.03', 'asset_version': '1.2.0', 'state_slots': slots, 'capabilities': caps,   # 1.2.0: fault.inject / clear
                                                       'evaluator_capabilities': evaluator_caps, 'panel_capabilities': panel_caps, 'parameters': params, 'declared_capabilities': {}}},
        'host_capabilities': host, 'trusted_scene_callback': 'scene_adapter(instance_id, updates): synchronous SI; bath_lid.angle_rad maps to bath_lid_joint about -X; no callback means scene command unsupported',
        'result_schema_scope': 'capability.output_schema validates response.result only',
        'limitations': ['Single well-mixed node; no fluid phase changes, evaporation, sample chemistry, fluid mass transfer, or external thermal circuit.', 'Cooling power interpolates manufacturer ethanol reference points only at 20°C ambient; applying to another fluid is an explicit host reference-model assumption, not validated performance.', 'Above20°C refrigeration capacity is unknown and not extrapolated; passive heat loss and heating remain modeled.', 'UA and C are explicitly configured assumptions/estimates; no fitted manufacturer defaults.', 'API access filtering is not a Python-process or filesystem security sandbox. Host and debug files must be inaccessible to untrusted agents.', 'In-memory request deduplication only; no restart persistence, hardware control or robot execution.',
                        'Front-panel functions (manual 30001664.F): timer, cooling mode, power supply mode, autostart, remote mode, high temperature cut-off with alarm E14, one-point ATC, flow lever, data recording and black box on a simulated USB flash drive. Faults of the code table (p.76-80) that this configuration can show occur only by evaluator injection (fault.inject / fault.clear, _JULABO_FAULTS): alarms stop the control and need a restart (switch-off codes are cleared by at least 4 s off), the E1427 warning keeps the control running and alternates with the temperature. Not simulated: warning limits (manual section 7.9 has no procedure, so E03 / E04 / E143 / E144 never occur), external sensor, other level systems, pressure circuit, analog module and interface-watchdog codes (absent on this model), beeps.',
                        'Cooling mode on: compressor runs throughout control, so heating near or below 20°C is slower by the reference cooling power; above 20°C its cooling power is unknown (0 in the model). Supply mode shared (hLO, factory): heater limited by the compressor electrical draw (460 W derived from the 115 V datasheet) only while the compressor runs.',
                        'Temperature probe error (offset, relative gain) is ideal 0 by default and settable only by the evaluator (sensor.configure_error); the one-point ATC stores an offset correction applied to the display, the control and the timer band. Realistic measurement errors are left to a later calibration / sim-to-real gap task.',
                        'Remote mode rOn (manual p.63, p.83-84; lead rule 2026-10-19): the direct device commands of an agent session stand for the USB interface (OUT '
                        'commands) and are refused (PRECONDITION_FAILED) while remote is off; reads, physical actions (power switch, lid, flow lever, cut-off screw, '
                        'flash drive) and the remote switch stay allowed; in remote mode the local keypad is locked (double-arrow chord excepted); the evaluator is '
                        'unrestricted. The simulation API is not the JULABO serial command set.']}


class _JulaboPanelHost:
    """Panel-host protocol of scripts/asset_runtime/_panel_ui.py for one instance: what the front-panel firmware reads, calls and times by."""
    def __init__(self, runtime, instance_id):
        self.runtime, self.instance_id = runtime, instance_id

    def panel_read(self):
        inst = self.runtime._instances[self.instance_id]
        slots = self.runtime._metadata['asset_types'][JULABO_CORIO_CD200F_TYPE]['state_slots']
        out = {sid: _julabo_copy.deepcopy(value) for sid, value in inst['state'].items() if slots[sid]['visibility'] == 'agent'}
        out.update({'host.powered': inst['state']['power.enabled'], 'host.power_epoch': inst['power_epoch']})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A panel action takes the checked path of a direct call (same preconditions, scene callback and revision)."""
        session = self.runtime._panel_caller or self.runtime._panel_session    # a gesture acts as the session that makes it (access rules)
        response = session._invoke_checked(self.instance_id, self.runtime._instances[self.instance_id], capability_id, dict(arguments), None)
        if response['status'] != 'succeeded':
            raise _julabo_panel()['engine'].PanelRejected(response['error']['code'])
        return response['result']

    def panel_time(self):
        return self.runtime.time_s


class JulaboCorioCD200FRuntime:
    """Trusted host; give sessions, not this object or debug files, to agents."""
    def __init__(self, *, ambient_temperature_K=293.15, scene_adapter=None):
        if not _julabo_number(ambient_temperature_K) or not 273.15 <= ambient_temperature_K <= 313.15:
            raise ValueError('Host ambient must be finite in assumed 273.15..313.15 K range')
        self.ambient_temperature_K = float(ambient_temperature_K)
        self.scene_adapter = scene_adapter
        self.time_s = 0.0
        self._instances, self._requests, self._scene_faults = {}, {}, []
        self._principals = {'__julabo_public_agent__': ('agent', None), '__julabo_panel__': ('agent', None)}
        self._logs = {'__julabo_public_agent__': [], '__julabo_panel__': []}
        self._lock = _julabo_threading.RLock()
        self._metadata = export_julabo_corio_cd200f_metadata()
        self._agent = JulaboCorioCD200FSession(self, '__julabo_public_agent__')
        self._panel_session = JulaboCorioCD200FSession(self, '__julabo_panel__')    # the front panel's own path to the commands (not logged)
        self._panel_caller = None                                                   # the session whose gesture runs: its commands act as that caller
        self._panels, self._drives, self._blackbox = {}, {}, {}
        self.panel_capture_dir = None                                       # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)

    def create_instance(self, instance_id, asset_type_id=JULABO_CORIO_CD200F_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != JULABO_CORIO_CD200F_TYPE:
                raise ValueError('Unique nonempty ID and declared JULABO type required')
            state = {'power.enabled': False, 'thermal.target_k': self.ambient_temperature_K, 'thermal.temperature_k': None, 'thermal.indicated_temperature_k': None,
                'thermal.model_configured': False, 'thermal.cooling_curve_supported': False, 'bath_lid.angle_rad': 0.0, 'circulation.running': False, 'circulation.external_connected': False,
                'circulation.flow_rate_m3_s': 0.0, 'circulation.pressure_rise_pa': 0.0,
                'thermal.heater_energy_j': 0.0, 'thermal.cooling_energy_j': 0.0, 'thermal.environment_energy_j': 0.0, 'thermal.stored_energy_change_j': 0.0,
                'circulation.flow_path': 'external', 'cooling.mode': 'auto', 'power.supply_mode': 'shared', 'power.autostart': False, 'interface.remote': False,
                'safety.cutoff_k': JULABO_CORIO_CD200F_CUTOFF_K[1], 'alarm.code': 0, 'warning.code': 0, 'fault.injected': [], 'timer.duration_s': 0.0, 'timer.phase': 'off', 'timer.remaining_s': 0.0,
                'sensor.atc_offset_k': 0.0, 'sensor.offset_error_k': 0.0, 'sensor.gain_error': 0.0, 'usb.flash_drive_inserted': False, 'logging.recording': False,
                'indicator.heating': False, 'indicator.cooling': False}
            self._push_before_commit(instance_id, state)
            self._instances[instance_id] = {'state': state, 'revision': 0, 'thermal_configuration': None, 'circulation_boundary': None, 'energy_start_s': self.time_s,
                                            'timer': {'band_s': 0.0}, 'power_epoch': 0, 'off_since': None}
            self._drives[instance_id] = {'files': [], 'open': None, 'count': 0}
            self._blackbox[instance_id] = {'records': [], 'events': []}
            self._panels[instance_id] = _julabo_panel()['engine'].PanelUI(_julabo_panel()['spec'], _JulaboPanelHost(self, instance_id))

    def _push_before_commit(self, instance_id, updates):
        if self.scene_adapter:
            try: self.scene_adapter(instance_id, _julabo_copy.deepcopy(updates))
            except Exception: raise RuntimeError('Scene callback failed; process unchanged; backend effects unknown') from None

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('Host must register a unique identity with known visibility')
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(x, str) for x in instance_ids)):
                raise ValueError('Instance scope must be a sequence of IDs')
            scope = None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope <= self._instances.keys(): raise ValueError('Unknown instance in scope')
            self._principals[principal_id], self._logs[principal_id] = (visibility, scope), []

    def session(self, principal_id):
        if not isinstance(principal_id, str) or principal_id not in self._principals: raise ValueError('Host identity required')
        return JulaboCorioCD200FSession(self, principal_id)

    def describe(self, **kwargs): return self._agent.describe(**kwargs)
    def read(self, **kwargs): return self._agent.read(**kwargs)
    def invoke(self, **kwargs): return self._agent.invoke(**kwargs)
    def write(self, **kwargs): return self._agent.write(**kwargs)
    def describe_host(self): return _julabo_copy.deepcopy(self._metadata['host_capabilities'])

    def debug_snapshot(self):
        with self._lock: return _julabo_copy.deepcopy({'time_s': self.time_s, 'instances': self._instances, 'scene_faults': self._scene_faults})

    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame)."""
        return self._panels[instance_id]

    def configure_bath_model(self, instance_id, *, effective_heat_capacity_J_K, heat_loss_W_K, lid_open_loss_W_K, initial_temperature_K, minimum_temperature_K, maximum_temperature_K, evidence):
        """Explicit host initialization; no material transfer or hardware reset."""
        with self._lock:
            values = (effective_heat_capacity_J_K, heat_loss_W_K, lid_open_loss_W_K, initial_temperature_K, minimum_temperature_K, maximum_temperature_K)
            if not isinstance(instance_id, str) or instance_id not in self._instances or not all(_julabo_number(x) for x in values) or not isinstance(evidence, str) or not evidence.strip():
                raise ValueError('Known instance, finite explicit parameters and evidence required')
            if effective_heat_capacity_J_K <= 0 or heat_loss_W_K < 0 or lid_open_loss_W_K < 0 or not _julabo_number(heat_loss_W_K + lid_open_loss_W_K): raise ValueError('Positive C and nonnegative finite loss required')
            if not 253.15 <= minimum_temperature_K <= initial_temperature_K <= maximum_temperature_K <= 423.15 or minimum_temperature_K == maximum_temperature_K: raise ValueError('Explicit valid fluid range and initial temperature required')
            if not minimum_temperature_K <= self.ambient_temperature_K <= maximum_temperature_K: raise ValueError('Fluid range must include ambient for passive cooling model')
            inst = self._instances[instance_id]
            if inst['state']['power.enabled'] or inst['state']['circulation.running']: raise ValueError('Configure only when powered off and stopped')
            cfg = dict(zip(('effective_heat_capacity_J_K', 'heat_loss_W_K', 'lid_open_loss_W_K', 'initial_temperature_K', 'minimum_temperature_K', 'maximum_temperature_K'), map(float, values)))
            cfg['evidence'] = evidence
            state = _julabo_copy.deepcopy(inst['state'])
            state.update({'thermal.target_k': float(initial_temperature_K), 'thermal.temperature_k': float(initial_temperature_K), 'thermal.indicated_temperature_k': self._indicate(self._reading(state, float(initial_temperature_K))), 'thermal.model_configured': True, 'thermal.cooling_curve_supported': self._cooling_supported(initial_temperature_K)})
            for key in state:
                if key.endswith('_energy_j') or key == 'thermal.stored_energy_change_j': state[key] = 0.0
            self._push_before_commit(instance_id, state)
            inst.update(state=state, thermal_configuration=cfg, energy_start_s=self.time_s, revision=inst['revision'] + 1)

    def configure_bath_fluid(self, instance_id, *, mass_kg, specific_heat_J_kg_K, **kwargs):
        with self._lock:
            if not _julabo_number(mass_kg) or not _julabo_number(specific_heat_J_kg_K) or mass_kg <= 0 or specific_heat_J_kg_K <= 0 or not _julabo_number(mass_kg * specific_heat_J_kg_K): raise ValueError('Explicit positive finite liquid mass and heat capacity required')
            self.configure_bath_model(instance_id, effective_heat_capacity_J_K=mass_kg * specific_heat_J_kg_K, **kwargs)
            self._instances[instance_id]['thermal_configuration'].update(mass_kg=float(mass_kg), specific_heat_J_kg_K=float(specific_heat_J_kg_K), capacity_model='C=m*cp; fluid only, no inferred instrument/component mass')

    def configure_circulation_boundary(self, instance_id, *, connected, flow_rate_m3_s=None, pressure_rise_Pa=None, evidence):
        with self._lock:
            if not isinstance(instance_id, str) or instance_id not in self._instances or type(connected) is not bool or not isinstance(evidence, str) or not evidence.strip(): raise ValueError('Valid instance, boolean connection and explicit evidence required')
            if connected:
                if not _julabo_number(flow_rate_m3_s) or not 0 <= flow_rate_m3_s <= JULABO_CORIO_CD200F_MAX_FLOW or not _julabo_number(pressure_rise_Pa) or not 0 <= pressure_rise_Pa <= 35000: raise ValueError('Operating point exceeds separate manufacturer limits')
                if flow_rate_m3_s == JULABO_CORIO_CD200F_MAX_FLOW and pressure_rise_Pa == 35000: raise ValueError('Maximum flow and maximum pressure are not simultaneous')
                boundary = {'flow_rate_m3_s': float(flow_rate_m3_s), 'pressure_rise_Pa': float(pressure_rise_Pa), 'evidence': evidence}
            else:
                if flow_rate_m3_s is not None or pressure_rise_Pa is not None: raise ValueError('Disconnected boundary cannot retain operating point')
                boundary = None
            inst = self._instances[instance_id]
            if inst['state']['circulation.running']: raise ValueError('Stop circulation before reconfiguring external boundary')
            self._push_before_commit(instance_id, {'circulation.external_connected': connected})
            inst['circulation_boundary'] = boundary
            inst['state']['circulation.external_connected'] = connected
            inst['revision'] += 1

    @staticmethod
    def _indicate(temperature): return round(temperature - 273.15, 2) + 273.15

    @staticmethod
    def _raw(state, temperature):
        """What the probe measures at bath temperature T: T + offset + gain x T(C) (ideal: T)."""
        offset, gain = state['sensor.offset_error_k'], state['sensor.gain_error']
        return temperature + offset + gain * (temperature - 273.15) if offset or gain else temperature

    def _reading(self, state, temperature):
        """The temperature the device works with (display, control, timer band, recording): the probe value plus the stored ATC correction."""
        return self._raw(state, temperature) + state['sensor.atc_offset_k'] if state['sensor.atc_offset_k'] else self._raw(state, temperature)

    @staticmethod
    def _bath_target(state):
        """Bath temperature at which the reading equals the set point (the reading is linear in T)."""
        offset, gain, atc = state['sensor.offset_error_k'], state['sensor.gain_error'], state['sensor.atc_offset_k']
        if not (offset or gain or atc): return state['thermal.target_k']
        return (state['thermal.target_k'] - offset - atc + gain * 273.15) / (1.0 + gain)

    def _cooling_supported(self, temperature):
        return temperature is not None and 253.15 <= temperature <= 293.15 and self.ambient_temperature_K == 293.15

    def _cooling_line(self, temperature, direction=-1):
        if not self._cooling_supported(temperature): return (0.0, 0.0)
        points = JULABO_CORIO_CD200F_COOLING_POINTS
        for (lo, plo), (hi, phi) in zip(points, points[1:]):
            if lo <= temperature <= hi and not (temperature == hi and direction > 0 and hi != points[-1][0]):
                slope = (phi - plo) / (hi - lo)
                return plo - slope * lo, slope
        return (0.0, 0.0)

    def _drive(self, state, temperature, target, enabled, loss, direction=-1):
        """Control law at one bath temperature: (heater W, cooling a, b with P_cool = a + b*T, cooling line used).  auto: heater or cooling,
        never both; on (CON): the compressor runs throughout and the heater works against it; off (COFF): no active cooling.  Supply shared
        (hLO): while the compressor runs the heater gives up its electrical draw."""
        if not enabled: return 0.0, 0.0, 0.0, False
        mode = state['cooling.mode']
        cool_a, cool_b = (0.0, 0.0) if mode == 'off' else self._cooling_line(temperature, direction)
        heater = JULABO_CORIO_CD200F_HEATER_W
        if mode == 'on' and state['power.supply_mode'] == 'shared': heater -= JULABO_CORIO_CD200F_COMPRESSOR_W
        if temperature < target - 1e-10:
            return (heater, cool_a, cool_b, True) if mode == 'on' else (JULABO_CORIO_CD200F_HEATER_W, 0.0, 0.0, False)
        if temperature > target + 1e-10: return 0.0, cool_a, cool_b, True
        available = max(0.0, cool_a + cool_b * temperature)
        required = loss * (temperature - self.ambient_temperature_K)
        if mode == 'on':
            net = min(heater - available, max(-available, required))
            return net + available, available, 0.0, False
        u = min(JULABO_CORIO_CD200F_HEATER_W, max(-available, required))
        return (u, 0.0, 0.0, False) if u >= 0 else (0.0, -u, 0.0, False)

    def _loss(self, inst, state):
        cfg = inst['thermal_configuration']
        return cfg['heat_loss_W_K'] + cfg['lid_open_loss_W_K'] * state['bath_lid.angle_rad'] / (_julabo_math.pi / 2)

    def _indicators(self, inst, state):
        """Heating / cooling control LEDs (manual p.35 items 13, 14) for a state: heater output, compressor running."""
        enabled = state['power.enabled'] and state['circulation.running']
        if not enabled or inst['thermal_configuration'] is None or state['thermal.temperature_k'] is None:
            return {'indicator.heating': False, 'indicator.cooling': False}
        temperature = state['thermal.temperature_k']
        heater, cool_a, cool_b, _ = self._drive(state, temperature, self._bath_target(state), True, self._loss(inst, state))
        return {'indicator.heating': heater > 1e-9, 'indicator.cooling': state['cooling.mode'] == 'on' or cool_a + cool_b * temperature > 1e-9}

    @staticmethod
    def _timer_off():
        return {'timer.duration_s': 0.0, 'timer.phase': 'off', 'timer.remaining_s': 0.0}

    @staticmethod
    def _stopped():
        return {'circulation.running': False, 'circulation.flow_rate_m3_s': 0.0, 'circulation.pressure_rise_pa': 0.0}

    def _events(self, inst, enabled, t, record):
        """Discrete events at time t of a step: safety cut-off (alarm E14, control stops), timer start after 30 s in band, timer expiry (standby)."""
        state, timer = inst['state'], inst['timer']
        temperature = state['thermal.temperature_k']
        if state['power.enabled'] and state['alarm.code'] == 0 and temperature >= state['safety.cutoff_k'] - 1e-9:
            state.update(self._stopped(), **self._timer_off())
            state['alarm.code'] = JULABO_CORIO_CD200F_ALARM_CUTOFF
            timer['band_s'] = 0.0
            if record is not None: record['events'].append({'time_s': t, 'kind': 'alarm', 'code': JULABO_CORIO_CD200F_ALARM_CUTOFF})
            return False
        if enabled and state['timer.phase'] == 'waiting' and timer['band_s'] >= JULABO_CORIO_CD200F_TIMER_HOLD_S - 1e-9:
            state.update({'timer.phase': 'running', 'timer.remaining_s': state['timer.duration_s']})
            timer['band_s'] = 0.0
        if enabled and state['timer.phase'] == 'running' and state['timer.remaining_s'] <= 1e-9:
            state.update(self._stopped(), **self._timer_off())                # elapsed: standby (manual p.64)
            return False
        return enabled

    def _samples(self, record, inst, t, duration, temperature, rate, b, heater, cool_a, cool_b):
        """Recording rows (every 10 s of the clock) inside one exactly integrated interval: time, set point, reading, power."""
        state = inst['state']
        if record is None or not state['power.enabled']: return
        step = JULABO_CORIO_CD200F_LOG_INTERVAL_S
        k = _julabo_math.floor(t / step + 1e-9) + 1
        while k * step <= t + duration + 1e-9:
            s = k * step - t
            value = temperature + (rate * s if abs(b) < 1e-18 else rate * _julabo_math.expm1(b * s) / b)
            power = heater - (cool_a + cool_b * value)
            record['records'].append({'time_s': k * step, 'setpoint_K': state['thermal.target_k'], 'internal_K': self._indicate(self._reading(state, value)), 'external_K': None,
                                      'power_fraction': max(-1.0, min(1.0, power / JULABO_CORIO_CD200F_HEATER_W))})
            k += 1

    def _step_thermal(self, inst, dt, t0=0.0, record=None):
        state, cfg = inst['state'], inst['thermal_configuration']
        if cfg is None: return
        capacity = cfg['effective_heat_capacity_J_K']
        loss = self._loss(inst, state)
        ambient, temperature = self.ambient_temperature_K, state['thermal.temperature_k']
        target = self._bath_target(state)                                   # the controller holds the (corrected) reading at the set point
        enabled = state['power.enabled'] and state['circulation.running']
        timer, band = inst['timer'], JULABO_CORIO_CD200F_TIMER_BAND_K / (1.0 + state['sensor.gain_error'])   # +-0.1 K of the reading
        remaining, elapsed, heating, cooling, environment = float(dt), 0.0, 0.0, 0.0, 0.0
        # Exact affine ODE on each manufacturer cooling segment; split at targets/knots, the cut-off, the timer band and timer instants.
        while remaining > 1e-10:
            state['thermal.temperature_k'] = temperature
            enabled = self._events(inst, enabled, t0 + elapsed, record)
            heater, cool_a, cool_b, line = self._drive(state, temperature, target, enabled, loss)
            a, b = (heater - cool_a + loss * ambient) / capacity, (-cool_b - loss) / capacity
            rate = a + b * temperature
            if rate > 0 and line:
                heater, cool_a, cool_b, line = self._drive(state, temperature, target, enabled, loss, 1)
                a, b = (heater - cool_a + loss * ambient) / capacity, (-cool_b - loss) / capacity
                rate = a + b * temperature
            duration = remaining
            waiting, counting = enabled and state['timer.phase'] == 'waiting', enabled and state['timer.phase'] == 'running'
            if waiting and abs(temperature - target) <= band + 1e-9: duration = min(duration, JULABO_CORIO_CD200F_TIMER_HOLD_S - timer['band_s'])
            if counting: duration = min(duration, state['timer.remaining_s'])
            candidates = [target, cfg['minimum_temperature_K'], cfg['maximum_temperature_K']]
            candidates += [point[0] for point in JULABO_CORIO_CD200F_COOLING_POINTS]
            if state['power.enabled'] and state['alarm.code'] == 0: candidates.append(state['safety.cutoff_k'])
            if waiting: candidates += [target - band, target + band]
            endpoint = None
            if abs(rate) > 1e-13:
                for bound in candidates:
                    if (bound - temperature) * rate <= 1e-10: continue
                    if abs(b) < 1e-18: crossing = (bound - temperature) / rate
                    else:
                        ratio = b * (bound - temperature) / rate
                        if ratio <= -1: continue
                        crossing = _julabo_math.log1p(ratio) / b
                    if 1e-10 < crossing <= duration:
                        duration, endpoint = crossing, bound
            if abs(b) < 1e-18:
                next_temperature = temperature + rate * duration
                integrated_t = temperature * duration + .5 * rate * duration * duration
            else:
                growth = _julabo_math.expm1(b * duration)
                next_temperature = temperature + rate * growth / b
                integrated_t = temperature * duration + rate * (growth / b - duration) / b
            if endpoint is not None: next_temperature = endpoint
            self._samples(record, inst, t0 + elapsed, duration, temperature, rate, b, heater, cool_a, cool_b)
            heating += max(0.0, heater * duration)
            cooling += max(0.0, cool_a * duration + cool_b * integrated_t)
            environment += loss * (integrated_t - ambient * duration)
            if waiting:
                inside = abs(temperature - target) <= band + 1e-9 and abs(next_temperature - target) <= band + 1e-9
                timer['band_s'] = timer['band_s'] + duration if inside else 0.0
            if counting: state['timer.remaining_s'] = max(0.0, state['timer.remaining_s'] - duration)
            remaining -= duration
            elapsed += duration
            temperature = next_temperature
        state['thermal.temperature_k'] = temperature
        self._events(inst, enabled, t0 + float(dt), record)                 # an event falling exactly on the end of the step
        state['thermal.indicated_temperature_k'] = self._indicate(self._reading(state, temperature))
        state['thermal.cooling_curve_supported'] = self._cooling_supported(temperature)
        state['thermal.heater_energy_j'] += heating
        state['thermal.cooling_energy_j'] += cooling
        state['thermal.environment_energy_j'] += environment
        state['thermal.stored_energy_change_j'] = capacity * (temperature - cfg['initial_temperature_K'])
        state.update(self._indicators(inst, state))

    def step(self, dt_s):
        if not _julabo_number(dt_s) or not 0 <= dt_s <= 3600: raise ValueError('dt_s must be finite in [0,3600]')
        if not dt_s: return
        with self._lock:
            candidates = _julabo_copy.deepcopy(self._instances)
            records = {key: {'records': [], 'events': []} for key in candidates}
            for key, inst in candidates.items():
                before = _julabo_copy.deepcopy(inst['state'])
                self._step_thermal(inst, float(dt_s), self.time_s, records[key])
                if any(isinstance(v, float) and not _julabo_math.isfinite(v) for v in inst['state'].values()): raise ValueError('Configuration produced nonfinite numerical state; step unchanged')
                if inst['state'] != before: inst['revision'] += 1
            self._instances, self.time_s = candidates, self.time_s + float(dt_s)
            for key, record in records.items(): self._record(key, record)
            faults = []
            if self.scene_adapter:
                for key, inst in self._instances.items():
                    try: self.scene_adapter(key, _julabo_copy.deepcopy(inst['state']))
                    except Exception: faults.append({'instance_id': key, 'time_s': self.time_s, 'code': 'SCENE_SYNCHRONIZATION_FAILED'})
            self._scene_faults.extend(faults)
            if faults: raise SceneSynchronizationError('Process and clock advanced; scene synchronization failed with possible partial scene effects') from None

    def _record(self, instance_id, record):
        """Committed rows of a step: the black box keeps the last 20 min; an open recording file on the flash drive gets every row."""
        box, drive = self._blackbox[instance_id], self._drives[instance_id]
        box['records'] += record['records']
        box['events'] += record['events']
        horizon = self.time_s - JULABO_CORIO_CD200F_BLACKBOX_S
        box['records'] = [r for r in box['records'] if r['time_s'] > horizon - 1e-9]
        box['events'] = [e for e in box['events'] if e['time_s'] > horizon - 1e-9]
        if drive['open'] is not None and self._instances[instance_id]['state']['logging.recording']:
            drive['files'][drive['open']]['records'] += _julabo_copy.deepcopy(record['records'])

    def _drive_file(self, instance_id, kind, records, events):
        drive = self._drives[instance_id]
        drive['count'] += 1
        drive['files'].append({'name': f"{'LOG' if kind == 'log' else 'BB'}{drive['count']:03d}.TXT", 'kind': kind, 'created_s': self.time_s,
                               'closed_s': None if kind == 'log' else self.time_s, 'records': records, 'events': events})
        return len(drive['files']) - 1

    def _close_log(self, instance_id):
        drive = self._drives[instance_id]
        if drive['open'] is not None:
            drive['files'][drive['open']]['closed_s'] = self.time_s
            drive['open'] = None

    def sync_scene(self, instance_ids=None):
        with self._lock:
            if self.scene_adapter is None: raise RuntimeError('Scene adapter required')
            ids = list(self._instances) if instance_ids is None else list(instance_ids)
            if any(not isinstance(key, str) or key not in self._instances for key in ids): raise ValueError('Unknown instance')
            for key in ids:
                try: self.scene_adapter(key, _julabo_copy.deepcopy(self._instances[key]['state']))
                except Exception: raise SceneSynchronizationError('Scene synchronization failed; backend effects unknown') from None


class JulaboCorioCD200FSession:
    """Agent-facing adapter with immutable host-registered identity and scope."""
    def __init__(self, host, principal_id):
        if principal_id not in host._principals: raise ValueError('Host identity required')
        self.__host, self.__principal_id = host, principal_id

    def _instance(self, instance_id):
        if not isinstance(instance_id, str): return None
        scope = self.__host._principals[self.__principal_id][1]
        return self.__host._instances.get(instance_id) if scope is None or instance_id in scope else None

    def _visible(self, slot): return slot['visibility'] == 'agent' or self.__host._principals[self.__principal_id][0] in ('debug', 'evaluator')

    def _envelope(self, instance_id, request_id=None, *, result=None, observations=None, status='succeeded', error=None):
        inst = self._instance(instance_id)
        return {'protocol_version': PROTOCOL_VERSION, 'instance_id': instance_id, 'request_id': request_id, 'operation_id': None, 'status': status,
            'state_revision': inst['revision'] if inst else None, 'clock': {'domain': 'simulation', 'time_s': self.__host.time_s},
            'result': {} if result is None else result, 'observations': {} if observations is None else observations, 'error': error}

    def _error(self, instance_id, code, request_id=None, status='rejected'):
        messages = {'UNKNOWN_INSTANCE': 'Instance is unavailable.', 'UNKNOWN_CAPABILITY': 'Capability is unavailable.', 'INVALID_ARGUMENT': 'Arguments do not match the declared input.', 'PRECONDITION_FAILED': 'Declared operation preconditions are not met.', 'ACCESS_DENIED': 'Requested operation is outside session access.', 'REQUEST_CONFLICT': 'Request ID was already used with different arguments.', 'UNSUPPORTED': 'Required implementation backend is unavailable.', 'DEVICE_ERROR': 'Scene callback failed; runtime unchanged; backend effects unknown.'}
        return self._envelope(instance_id, request_id, status=status, error={'code': code, 'message': messages.get(code, 'Operation failed.'), 'retryable': False, 'side_effects': 'unknown' if code == 'DEVICE_ERROR' else 'none'})

    def describe(self, *, instance_id=None, asset_type_id=None):
        with self.__host._lock:
            if (instance_id is None) == (asset_type_id is None): return self._error(instance_id, 'INVALID_ARGUMENT')
            if instance_id is not None and not self._instance(instance_id): return self._error(instance_id, 'UNKNOWN_INSTANCE')
            if asset_type_id is not None and asset_type_id != JULABO_CORIO_CD200F_TYPE: return self._error(instance_id, 'UNKNOWN_INSTANCE')
            spec = self.__host._metadata['asset_types'][JULABO_CORIO_CD200F_TYPE]
            caps = _julabo_copy.deepcopy(spec['capabilities'])
            caps.update({k: _julabo_copy.deepcopy(v) for group in ('evaluator_capabilities', 'panel_capabilities') for k, v in spec[group].items() if self._visible(v)})
            for cap in caps.values():
                cap['state_effects'] = [effect for effect in cap['state_effects'] if self._visible(spec['state_slots'][effect['state_id']])]
            if self.__host.scene_adapter is None: caps['scene.set_bath_lid']['availability'] = 'unsupported'
            return {'protocol_version': PROTOCOL_VERSION, 'instance_id': instance_id, 'asset_type_id': JULABO_CORIO_CD200F_TYPE, 'asset_version': spec['asset_version'],
                'adapter': 'JulaboCorioCD200FSession', 'capabilities': caps,
                'state_slots': _julabo_copy.deepcopy({sid: slot for sid, slot in spec['state_slots'].items() if self._visible(slot)}),
                'limitations': _julabo_copy.deepcopy(self.__host._metadata['limitations'])}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, 'UNKNOWN_INSTANCE')
            slots = self.__host._metadata['asset_types'][JULABO_CORIO_CD200F_TYPE]['state_slots']
            if state_ids is None: state_ids = [sid for sid, slot in slots.items() if self._visible(slot)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids) or len(set(state_ids)) != len(state_ids): return self._error(instance_id, 'INVALID_ARGUMENT')
            if any(sid not in slots or not self._visible(slots[sid]) for sid in state_ids): return self._error(instance_id, 'ACCESS_DENIED')
            observations = {}
            for sid in state_ids:
                slot, value = slots[sid], _julabo_copy.deepcopy(inst['state'][sid])
                observations[sid] = {'value': value, 'unit': slot['unit'], 'quality': 'unavailable' if value is None else 'estimated' if slot['origin'] == 'derived' else 'valid', 'sample_time_s': self.__host.time_s,
                    'sample_window_s': [inst['energy_start_s'], self.__host.time_s] if slot['sampling']['mode'] == 'integral' else None}
                if value is None: observations[sid]['reason'] = 'Required host bath model or external circulation boundary is not configured.'
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            if not isinstance(capability_id, str) or not isinstance(request_id, str) or not request_id or len(request_id) > 256 or not isinstance(arguments, dict): return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            try: digest = _julabo_hashlib.sha256(_julabo_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            except (ValueError, TypeError, OverflowError): return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            key = (self.__principal_id, instance_id, request_id)
            if key in host._requests:
                previous, response = host._requests[key]
                return _julabo_copy.deepcopy(response) if digest == previous else self._error(instance_id, 'REQUEST_CONFLICT', request_id)
            if capability_id in _JULABO_REMOTE_COMMANDS and not inst['state']['interface.remote'] and host._principals[self.__principal_id][0] not in ('evaluator', 'debug'):
                response = self._error(instance_id, 'PRECONDITION_FAILED', request_id)     # remote interface: OUT commands only in rOn (p.83-84); panel gestures
            else:                                                                           # take _invoke_checked directly (local keys, not gated)
                response = self._invoke_checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, _julabo_copy.deepcopy(response))
            host._logs[self.__principal_id].append({'instance_id': instance_id, 'capability_id': capability_id, 'request_id': request_id, 'response': _julabo_copy.deepcopy(response)})
            return response

    def _invoke_panel(self, instance_id, capability, args, request_id):
        """panel.* commands: a gesture on the instance's front panel (engine + panel_ui.json), a screen capture, or the evaluator frame."""
        host, panel = self.__host, _julabo_panel()
        definition = host._metadata['asset_types'][JULABO_CORIO_CD200F_TYPE]['panel_capabilities'][capability]
        if not self._visible(definition): return self._error(instance_id, 'ACCESS_DENIED', request_id)
        if panel['engine'].check_arguments(definition['input_schema'], args): return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
        ui = host._panels[instance_id]
        try:
            if capability == 'panel.frame': return self._envelope(instance_id, request_id, result={'frame': ui.frame()})
            if capability == 'panel.capture':
                data, width, height = ui.png_bytes(args['display_id'])
                sha = _julabo_hashlib.sha256(data).hexdigest()
                folder = _julabo_Path(host.panel_capture_dir) if host.panel_capture_dir else _julabo_Path(_julabo_tempfile.gettempdir()) / 'chemistry_sim_panel' / instance_id
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{args['display_id']}_{sha[:16]}.png"
                if not path.exists(): path.write_bytes(data)
                return self._envelope(instance_id, request_id, result={'display_id': args['display_id'], 'time_s': host.time_s,
                                                                       'image': {'path': str(path), 'format': 'png', 'width': width, 'height': height, 'sha256': sha}})
            host._panel_caller = self
            try:
                if capability == 'panel.press': accepted = ui.press(args['input_id'], float(args.get('duration_s', 0.1)), held=args.get('held', ()))
                elif capability == 'panel.switch': accepted = ui.switch(args['input_id'], args['position'], held=args.get('held', ()))
                else: accepted = ui.turn(args['input_id'], int(args['detents']))
            finally:
                host._panel_caller = None
        except panel['engine'].PanelError:
            return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
        return self._envelope(instance_id, request_id, result={'accepted': bool(accepted)})

    def _invoke_checked(self, instance_id, inst, capability, args, request_id):
        """Run ownership (lead rule 2026-10-19): the caller that starts the temperature control (OK held, or switch-on with autostart) owns it;
        another caller cannot stop it, change its set point, timer or cooling mode while it runs.  The red rocker is a hard power switch (physical
        action, lead clarification 2026-10-19): power.set follows it and is never refused; the unit reacts as to a power loss (control stops)."""
        active = inst['state']['circulation.running']
        a = args if isinstance(args, dict) else {}
        guarded = capability in ('thermal.set_temperature', 'timer.set', 'cooling.set_mode') or (capability == 'circulation.set_running' and a.get('running') is False)
        if guarded and _g02_foreign_run(self.__host, self.__principal_id, inst.get('run_owner'), active):
            return self._error(instance_id, 'ACCESS_DENIED', request_id)
        response = self._invoke_body(instance_id, inst, capability, args, request_id)
        if response['status'] == 'succeeded' and not active and inst['state']['circulation.running']:
            inst['run_owner'] = self.__principal_id
        return response

    def _invoke_body(self, instance_id, inst, capability, args, request_id):
        host, state = self.__host, inst['state']
        fail = lambda code, status='rejected': self._error(instance_id, code, request_id, status)
        spec = host._metadata['asset_types'][JULABO_CORIO_CD200F_TYPE]
        if capability in spec['panel_capabilities']: return self._invoke_panel(instance_id, capability, args, request_id)
        caps = {**spec['capabilities'], **spec['evaluator_capabilities']}
        if capability not in caps: return fail('UNKNOWN_CAPABILITY')
        if not self._visible(caps[capability]): return fail('ACCESS_DENIED')
        schema = caps[capability]['input_schema']['properties']
        if set(args) != set(schema): return fail('INVALID_ARGUMENT')
        for name, item in schema.items():
            value = args[name]
            if item.get('type') == 'boolean' and type(value) is not bool: return fail('INVALID_ARGUMENT')
            if item.get('type') == 'number' and (not _julabo_number(value) or not item.get('minimum', -float('inf')) <= value <= item.get('maximum', float('inf'))): return fail('INVALID_ARGUMENT')
            if item.get('type') == 'string' and (not isinstance(value, str) or value not in item.get('enum', [value])): return fail('INVALID_ARGUMENT')
        if capability.startswith('scene.') and host.scene_adapter is None: return fail('UNSUPPORTED', 'unsupported')
        cfg, post = inst['thermal_configuration'], []

        def flow(running, path):
            boundary = inst['circulation_boundary'] if path == 'external' else {'flow_rate_m3_s': 0.0, 'pressure_rise_Pa': 0.0}
            return {'circulation.running': running, 'circulation.flow_rate_m3_s': (boundary['flow_rate_m3_s'] if boundary else None) if running else 0.0,
                    'circulation.pressure_rise_pa': (boundary['pressure_rise_Pa'] if boundary else None) if running else 0.0}

        def over_cutoff(cutoff):
            return state['thermal.temperature_k'] is not None and state['thermal.temperature_k'] >= cutoff - 1e-9

        def needs(*keys):
            return all(state[k] for k in keys)
        if capability == 'power.set':
            updates, result = {'power.enabled': args['enabled']}, {'enabled': args['enabled']}
            if not args['enabled']:
                updates.update({'circulation.running': False, 'circulation.flow_rate_m3_s': 0.0, 'circulation.pressure_rise_pa': 0.0, 'alarm.code': 0, 'warning.code': 0,
                                'logging.recording': False}, **host._timer_off())
                post.append(lambda: host._close_log(instance_id))
                if state['power.enabled']: post.append(lambda: inst.update(off_since=host.time_s))
            elif not state['power.enabled']:                                  # power-up: the safety circuit checks the bath, then autostart (p.57, p.60)
                off = host.time_s - inst['off_since'] if inst.get('off_since') is not None else float('inf')
                kept = [c for c in state['fault.injected'] if not (_JULABO_FAULTS[c][1] == 'restart' and off >= JULABO_CORIO_CD200F_RESTART_S - 1e-9)]
                alarms = [c for c in kept if _JULABO_FAULTS[c][0] == 'alarm' and not (c == 61 and state['cooling.mode'] == 'off')]   # E61 with cooling off: heating thermostat (p.77)
                warnings = [c for c in kept if _JULABO_FAULTS[c][0] == 'warning']
                updates.update({'fault.injected': kept, 'warning.code': warnings[0] if warnings else 0})
                code = JULABO_CORIO_CD200F_ALARM_CUTOFF if over_cutoff(state['safety.cutoff_k']) else alarms[0] if alarms else None
                if code is not None:
                    updates['alarm.code'] = code
                    post.append(lambda: host._blackbox[instance_id]['events'].append({'time_s': host.time_s, 'kind': 'alarm', 'code': code}))
                elif state['power.autostart'] and state['thermal.model_configured']: updates.update(flow(True, state['circulation.flow_path']))
                post.append(lambda: inst.update(power_epoch=inst['power_epoch'] + 1))
        elif capability == 'thermal.set_temperature':
            if not state['power.enabled']: return fail('PRECONDITION_FAILED')
            if cfg and not cfg['minimum_temperature_K'] <= args['temperature_K'] <= cfg['maximum_temperature_K']: return fail('PRECONDITION_FAILED')
            updates, result = {'thermal.target_k': float(args['temperature_K'])}, {'setpoint_K': float(args['temperature_K'])}
            if state['timer.phase'] == 'running' and float(args['temperature_K']) != state['thermal.target_k']: updates.update(host._timer_off())   # p.64
            post.append(lambda: inst['timer'].update(band_s=0.0))
        elif capability == 'circulation.set_running':
            if args['running'] and not (state['power.enabled'] and state['thermal.model_configured']): return fail('PRECONDITION_FAILED')
            if args['running'] and state['alarm.code']: return fail('PRECONDITION_FAILED')
            updates, result = flow(args['running'], state['circulation.flow_path']), {'running': args['running']}
            if not args['running']: updates.update(host._timer_off())       # OK pauses the control and stops a timer (p.58, p.64)
        elif capability == 'circulation.set_flow_path':
            updates, result = {'circulation.flow_path': args['path'], **flow(state['circulation.running'], args['path'])}, {'path': args['path']}
        elif capability == 'cooling.set_mode':
            updates, result = {'cooling.mode': args['mode']}, {'mode': args['mode']}
        elif capability == 'power.set_supply_mode':
            updates, result = {'power.supply_mode': args['mode']}, {'mode': args['mode']}
        elif capability == 'power.set_autostart':
            updates, result = {'power.autostart': args['enabled']}, {'enabled': args['enabled']}
        elif capability == 'timer.set':
            if not state['power.enabled']: return fail('PRECONDITION_FAILED')
            if args['duration_s'] % 60: return fail('INVALID_ARGUMENT')
            duration = float(args['duration_s'])
            updates, result = ({'timer.duration_s': duration, 'timer.phase': 'waiting', 'timer.remaining_s': duration} if duration else host._timer_off()), {'duration_s': duration}
            post.append(lambda: inst['timer'].update(band_s=0.0))
        elif capability == 'interface.set_remote':
            if not state['power.enabled']: return fail('PRECONDITION_FAILED')
            updates, result = {'interface.remote': args['enabled']}, {'enabled': args['enabled']}
        elif capability == 'safety.set_cutoff':
            cutoff = float(args['temperature_K'])
            updates, result = {'safety.cutoff_k': cutoff}, {'cutoff_K': cutoff}
            if state['power.enabled'] and state['alarm.code'] == 0 and over_cutoff(cutoff):   # below the bath: E14 at once (p.69)
                updates.update({'alarm.code': JULABO_CORIO_CD200F_ALARM_CUTOFF, 'circulation.running': False, 'circulation.flow_rate_m3_s': 0.0, 'circulation.pressure_rise_pa': 0.0}, **host._timer_off())
                post.append(lambda: host._blackbox[instance_id]['events'].append({'time_s': host.time_s, 'kind': 'alarm', 'code': JULABO_CORIO_CD200F_ALARM_CUTOFF}))
        elif capability == 'sensor.calibrate':
            reference = float(args['reference_temperature_K'])
            if not needs('power.enabled', 'thermal.model_configured') or abs(reference - state['thermal.target_k']) > JULABO_CORIO_CD200F_ATC_WINDOW_K + 1e-9: return fail('PRECONDITION_FAILED')
            offset = reference - host._raw(state, state['thermal.temperature_k'])                        # stored so that the reading equals the reference
            updates, result = {'sensor.atc_offset_k': offset}, {'atc_offset_K': offset}
            updates['thermal.indicated_temperature_k'] = host._indicate(host._reading({**state, **updates}, state['thermal.temperature_k']))
        elif capability in ('fault.inject', 'fault.clear'):
            code = args['code']
            if type(code) is not int or code not in _JULABO_FAULTS: return fail('INVALID_ARGUMENT')
            kind = _JULABO_FAULTS[code][0]
            result = {'code': code}
            if capability == 'fault.inject':
                updates = {'fault.injected': sorted(set(state['fault.injected']) | {code})}
                if state['power.enabled'] and kind == 'alarm' and state['alarm.code'] == 0:
                    updates.update({'alarm.code': code, **host._stopped(), **host._timer_off()})   # alarm: control stops, LED, restart required (p.56)
                    post.append(lambda: inst['timer'].update(band_s=0.0))
                if state['power.enabled'] and kind == 'warning' and state['warning.code'] == 0:
                    updates['warning.code'] = code                                # warning: control goes on (p.56)
                if state['power.enabled'] and ('alarm.code' in updates or 'warning.code' in updates):
                    post.append(lambda: host._blackbox[instance_id]['events'].append({'time_s': host.time_s, 'kind': kind, 'code': code}))
            else:
                if code not in state['fault.injected']: return fail('PRECONDITION_FAILED')
                left = [c for c in state['fault.injected'] if c != code]
                updates = {'fault.injected': left}
                if state['warning.code'] == code:                                 # the warning clears with its cause; an alarm waits for the restart
                    updates['warning.code'] = next((c for c in left if _JULABO_FAULTS[c][0] == 'warning'), 0)
        elif capability == 'sensor.configure_error':
            updates, result = {'sensor.offset_error_k': float(args['offset_K']), 'sensor.gain_error': float(args['gain'])}, {'offset_K': float(args['offset_K']), 'gain': float(args['gain'])}
            if state['thermal.temperature_k'] is not None:
                updates['thermal.indicated_temperature_k'] = host._indicate(host._reading({**state, **updates}, state['thermal.temperature_k']))
        elif capability == 'usb.set_flash_drive':
            updates, result = {'usb.flash_drive_inserted': args['inserted']}, {'inserted': args['inserted']}
            if not args['inserted']: updates['logging.recording'] = False
            if not args['inserted'] and state['logging.recording']: post.append(lambda: host._close_log(instance_id))
        elif capability == 'logging.set_recording':
            if args['enabled'] and not needs('power.enabled', 'usb.flash_drive_inserted'): return fail('PRECONDITION_FAILED')
            updates, result = {'logging.recording': args['enabled']}, {'enabled': args['enabled']}
            if args['enabled'] and not state['logging.recording']: post.append(lambda: host._drives[instance_id].update(open=host._drive_file(instance_id, 'log', [], [])))
            if not args['enabled'] and state['logging.recording']: post.append(lambda: host._close_log(instance_id))
        elif capability == 'blackbox.export':
            if not needs('power.enabled', 'usb.flash_drive_inserted'): return fail('PRECONDITION_FAILED')
            box = host._blackbox[instance_id]
            index = host._drive_file(instance_id, 'blackbox', _julabo_copy.deepcopy(box['records']), _julabo_copy.deepcopy(box['events']))
            return self._envelope(instance_id, request_id, result={'file_name': host._drives[instance_id]['files'][index]['name']})
        elif capability == 'usb.read_flash_drive':
            return self._envelope(instance_id, request_id, result={'files': _julabo_copy.deepcopy(host._drives[instance_id]['files'])})
        else: updates, result = {'bath_lid.angle_rad': float(args['angle_rad'])}, {'angle_rad': float(args['angle_rad'])}
        updates.update(host._indicators(inst, {**state, **updates}))
        changed = {sid: value for sid, value in updates.items() if value != state[sid]}
        if changed:
            try: host._push_before_commit(instance_id, changed)
            except RuntimeError: return fail('DEVICE_ERROR', 'failed')
            state.update(changed)
            inst['revision'] += 1
        for action in post: action()
        return self._envelope(instance_id, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if not self._instance(instance_id): return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            slots = self.__host._metadata['asset_types'][JULABO_CORIO_CD200F_TYPE]['state_slots']
            slot = slots.get(state_id) if isinstance(state_id, str) else None
            if slot is None or not self._visible(slot) or slot['access'] != 'read_write': return self._error(instance_id, 'ACCESS_DENIED', request_id)
            route = slot['write_via']
            return self.invoke(instance_id=instance_id, capability_id=route['capability_id'], arguments={route['parameter']: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock: return _julabo_copy.deepcopy(self.__host._logs[self.__principal_id])
# END CHEM-187 JULABO CORIO CD-200F runtime.

# BEGIN CHEM-191 Miele PLW 6111 DP SC CL H14 runtime (append-only asset scope).
import copy as _miele_copy
import hashlib as _miele_hashlib
import json as _miele_json
import math as _miele_math
import threading as _miele_threading

MIELE_PLW6111_TYPE = 'instrument.miele_plw6111'
MIELE_PLW6111_SOURCE = 'https://media.miele.com/downloads/n-/gb/FS_10958380_GBE_GB-en-GB.pdf'
MIELE_PLW6111_WATER_POWER_W = 7500.0
MIELE_PLW6111_BASKETS = ('lower', 'middle', 'upper')


def _miele_number(value):
    return type(value) in (int, float) and _number(value)


def _miele_object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _miele_schema(unit, minimum=None, maximum=None, nullable=False):
    result = {'type': 'number', 'x-unit': unit}
    if minimum is not None: result['minimum'] = minimum
    if maximum is not None: result['maximum'] = maximum
    return {'anyOf': [result, {'type': 'null'}]} if nullable else result


def export_miele_plw6111_metadata(panel=True):
    """Static metadata only; all unknown manufacturer thermal parameters are null.  The MP.CON 3 functions behind the front panel are added
    in separate groups (controller_capabilities, evaluator_capabilities, panel_capabilities); panel=False skips loading panel_ui.json."""
    def route(capability, parameter, **fixed):
        return {'capability_id': capability, 'parameter': parameter, 'fixed_arguments': fixed}
    def slot(unit, origin, component, meaning, schema, route_value=None, integral=False):
        return {'unit': unit, 'origin': origin, 'component_id': component, 'meaning_zh': meaning,
            'visibility': 'debug' if origin == 'process_truth' else 'agent',
            'access': 'read_write' if route_value else 'read_only', 'write_via': route_value,
            'value_schema': schema, 'reference_conditions': None,
            'sampling': {'mode': 'integral' if integral else 'instant', 'time_domain': 'simulation', 'window': 'Since explicit host water model configuration' if integral else None}}
    slots = {
        'power.enabled': slot('1', 'setpoint', 'display', '本机供电许可', {'type': 'boolean'}, route('power.set', 'enabled')),
        'thermal.target_k': slot('K', 'setpoint', 'display', '水温目标；不代表实际水温或热风温度', _miele_schema('K', 273.15, 373.15), route('thermal.set_temperature', 'temperature_K')),
        'water.temperature_k': slot('K', 'process_truth', 'water_heater', '单节点有效洗涤水温真值；未配置模型为null', _miele_schema('K', nullable=True)),
        'water.indicated_temperature_k': slot('K', 'derived', 'display', '0.1°C网格取整的理想化水温显示，未模拟传感器噪声或精度', _miele_schema('K', nullable=True)),
        'water.model_configured': slot('1', 'derived', 'water_heater', '宿主显式配置了水热模型', {'type': 'boolean'}),
        'door.angle_rad': slot('rad', 'scene', 'door', '门关节角度，0关闭，pi/2示意全开；机械映射见模型绑定', _miele_schema('rad', 0, _miele_math.pi / 2)),
        'door.closed': slot('1', 'derived', 'door', '模拟门闭合条件，不是实机互锁传感器', {'type': 'boolean'}),
        'program.running': slot('1', 'derived', 'display', '当前模拟程序运行中', {'type': 'boolean'}),
        'program.phase': slot('1', 'derived', 'display', '清洗、漂洗、干燥计时状态机；不是清洁度或含水率判据', {'type': 'string', 'enum': ['idle', 'wash', 'rinse', 'dry', 'completed', 'stopped']}),
        'program.phase_elapsed_s': slot('s', 'derived', 'display', '本阶段已完成计时；洗涤/漂洗须达到目标水温才计时', _miele_schema('s', 0)),
        'program.configured': slot('1', 'derived', 'display', '已显式设置三个模拟阶段时长', {'type': 'boolean'}),
    }
    for name in MIELE_PLW6111_BASKETS:
        sid = 'basket.' + name + '.extension_m'
        slots[sid] = slot('m', 'scene', 'basket_' + name, '三层篮架槽位前拉距离，0归位；0.4m为仿真行程假设', _miele_schema('m', 0, .4), route('basket.set_position', 'extension_m', slot=name))
    for name in ('wash', 'rinse', 'dry'):
        slots['program.' + name + '_s'] = slot('s', 'setpoint', 'display', '宿主任务指定的模拟阶段时长，非厂家程序配方', _miele_schema('s', 0, 86400, nullable=True))
    for name, meaning in (('heater_energy_j', '累计水加热输入'), ('environment_energy_j', '累计水向环境放热'), ('stored_energy_change_j', '相对显式初始化的水热储能变化')):
        slots['water.' + name] = slot('J', 'process_truth', 'water_heater', meaning, _miele_schema('J'), integral=True)
    for sid, item in slots.items():
        item['read_binding'] = "MielePLW6111Session.read(instance_id=..., state_ids=['" + sid + "'])"
        if item['origin'] == 'derived': item['observation_note_zh'] = '仿真派生观测标记estimated；缺测标记unavailable；不是厂家实测数据。'
    defs = {
        'power.set': ({'enabled': {'type': 'boolean'}}, {'enabled': {'type': 'boolean'}}, {'enabled': True}, ['display'], ['power.enabled'], ['power.enabled', 'program.running', 'program.phase'], '设置供电；断电停止程序，保留已经发生的温度及计时。'),
        'thermal.set_temperature': ({'temperature_K': _miele_schema('K', 273.15, 373.15)}, {'setpoint_K': _miele_schema('K')}, {'temperature_K': 333.15}, ['display', 'water_heater'], ['thermal.target_k'], ['thermal.target_k'], '只写目标水温；运行中不允许改温。实际加热只随宿主step在清洗/漂洗阶段进行。'),
        'door.open': ({}, {'angle_rad': _miele_schema('rad'), 'closed': {'type': 'boolean'}}, {}, ['door'], ['door.angle_rad', 'door.closed'], ['door.angle_rad', 'door.closed'], '程序停止时打开门关节；需要真实场景回调，不代表机器人抓取。'),
        'door.close': ({}, {'angle_rad': _miele_schema('rad'), 'closed': {'type': 'boolean'}}, {}, ['door'], ['door.angle_rad', 'door.closed'], ['door.angle_rad', 'door.closed'], '三篮架均归位且程序停止时关闭门关节。'),
        'basket.set_position': ({'slot': {'type': 'string', 'enum': list(MIELE_PLW6111_BASKETS)}, 'extension_m': _miele_schema('m', 0, .4)}, {'slot': {'type': 'string', 'enum': list(MIELE_PLW6111_BASKETS)}, 'extension_m': _miele_schema('m', 0, .4)}, {'slot': 'lower', 'extension_m': .2}, ['basket_' + s for s in MIELE_PLW6111_BASKETS], ['basket.' + s + '.extension_m' for s in MIELE_PLW6111_BASKETS], ['basket.' + s + '.extension_m' for s in MIELE_PLW6111_BASKETS], '门全开且程序停止时设置单个篮架沿-Y的前拉位置；不是玻璃器皿放置或物流转移。'),
        'washing.configure_program': ({name + '_s': _miele_schema('s', 1 if name != 'dry' else 0, 86400) for name in ('wash', 'rinse', 'dry')}, {'configured': {'type': 'boolean'}}, {'wash_s': 60, 'rinse_s': 30, 'dry_s': 30}, ['display'], ['program.configured'], ['program.configured', 'program.wash_s', 'program.rinse_s', 'program.dry_s', 'program.phase', 'program.phase_elapsed_s'], '显式配置三个阶段时长；不是厂家内置程序、化学配方或清洁度模型。'),
        'washing.start': ({}, {'running': {'type': 'boolean'}}, {}, ['display', 'water_heater'], ['program.running'], ['program.running', 'program.phase', 'program.phase_elapsed_s'], '从wash阶段开始；必须供电、门关、三篮架归位、水热模型与程序均已配置。'),
        'washing.stop': ({}, {'running': {'type': 'boolean'}}, {}, ['display', 'water_heater'], ['program.running'], ['program.running', 'program.phase'], '停止后续程序与加热，保留已发生的水温、阶段计时及累计能量。'),
    }
    caps = {}
    for name, (args, outputs, example, components, observes, immediate, meaning) in defs.items():
        pre = []
        if name in ('thermal.set_temperature', 'washing.configure_program', 'washing.start'):
            pre.append({'description_zh': '已上电且没有运行中的程序', 'state_ids': ['power.enabled', 'program.running'], 'enforced_by': 'adapter', 'check': 'power.enabled and not program.running'})
        if name == 'thermal.set_temperature':
            pre.append({'description_zh': '目标不低于宿主环境，若水模型已配置则不得超过其明确液态适用上限；273.15..373.15K仅模拟输入域', 'state_ids': ['water.model_configured'], 'enforced_by': 'adapter', 'check': 'ambient_K <= temperature_K <= configured maximum if configured'})
        if name in ('door.open', 'door.close', 'basket.set_position'):
            pre.append({'description_zh': '程序停止（program.running为false）且程序状态不是running/interrupted/malfunction/power_fail（中断、故障或断电时门与篮架被锁，返回BUSY），并已注册同步场景适配器', 'state_ids': ['program.running', 'programme.status'], 'enforced_by': 'adapter', 'check': "not program.running and programme.status not in ('running', 'interrupted', 'malfunction', 'power_fail') and scene_adapter is not None"})   # 1.3.1 (2026-10-22): text follows the runtime
        if name == 'door.close':
            pre.append({'description_zh': '三层篮架均归位', 'state_ids': ['basket.' + s + '.extension_m' for s in MIELE_PLW6111_BASKETS], 'enforced_by': 'adapter', 'check': 'all extensions == 0'})
        if name == 'basket.set_position':
            pre.append({'description_zh': '门已全开', 'state_ids': ['door.angle_rad'], 'enforced_by': 'adapter', 'check': 'door.angle_rad == pi/2'})
        if name == 'washing.start':
            pre.append({'description_zh': '门关、三架归位、模型和程序配置已完成', 'state_ids': ['door.closed', 'water.model_configured', 'program.configured'] + ['basket.' + s + '.extension_m' for s in MIELE_PLW6111_BASKETS], 'enforced_by': 'adapter', 'check': 'door.closed and all extensions == 0 and water.model_configured and program.configured'})
        effects = [{'state_id': sid, 'timing': 'immediate', 'description_zh': meaning} for sid in immediate]
        if name in ('power.set', 'washing.start', 'washing.stop'):
            effects += [{'state_id': sid, 'timing': 'on_step', 'description_zh': '宿主推进有限功率水热响应及三阶段状态机；干燥仅计时，不计算热风或含水率'} for sid in ('water.temperature_k', 'water.indicated_temperature_k', 'water.heater_energy_j', 'water.environment_energy_j', 'water.stored_energy_change_j', 'program.running', 'program.phase', 'program.phase_elapsed_s')]
        caps[name] = {'availability': 'implemented', 'visibility': 'agent', 'kind': 'configuration' if name == 'washing.configure_program' else 'simulation' if name in ('door.open', 'door.close', 'basket.set_position') else 'device_command',
            'component_ids': components, 'description_zh': meaning, 'input_schema': _miele_object(args), 'output_schema': _miele_object(outputs), 'example_arguments': [example],
            'observes': observes, 'state_effects': effects, 'preconditions': pre, 'scene_backend_required': name in ('door.open', 'door.close', 'basket.set_position'),
            'binding': "basic40_runtime_thermal.MielePLW6111Session.invoke(instance_id=..., capability_id='" + name + "', arguments=..., request_id=...)",
            'completion_criterion_zh': '同步成功仅确认本次设定/程序许可/关节设置；洗涤状态随后由宿主step推进。program.phase=completed只证明三个模拟计时完成。'}
    def p(value, unit, evidence, source): return {'value': value, 'unit': unit, 'evidence': evidence, 'source': source}
    params = {
        'water_heater_power_W': p(7500.0, 'W', 'public_document', MIELE_PLW6111_SOURCE + '; water heating rated power; never applied to drying air'),
        'thermal_capacity_J_K': p(None, 'J/K', 'unknown', 'No manufacturer water charge/effective heat capacity identified; explicit host configuration required; no oven parameter reuse.'),
        'heat_loss_W_K': p(None, 'W/K', 'unknown', 'One rated power cannot identify C and UA; explicit host assumption required.'),
        'water_charge_kg': p(None, 'kg', 'unknown', 'No inferred charge from chamber volume or 251kg whole-machine mass.'),
        'manufacturer_heatup_s': p(None, 's', 'unknown', 'No independently documented heat-up time in the present evidence.'),
        'manufacturer_maximum_water_temperature_K': p(None, 'K', 'unknown', 'Not inferred from generic washer family specifications.'),
        'drying_air_temperature_K': p(None, 'K', 'unknown', 'Dry state is a timer only; no drying-air energy or moisture model (the panel programmes add a display-only first-order drying-air node, drying.air_temperature_k).'),
        'basket_extension_limit_m': p(.4, 'm', 'assumption', 'Illustrative three-slot front extension, not measured manufacturer travel.'),
        'door_angle_limit_rad': p(_miele_math.pi / 2, 'rad', 'assumption', 'Illustrative full-open mechanical angle; actual scene mapping defined in model bindings.'),
        'whole_machine_mass_kg': p(251.0, 'kg', 'public_document', MIELE_PLW6111_SOURCE + '; not used for component inertia or water heat capacity'),
    }
    limitations = ['Water-only single-node heat equation C*dT/dt=P-UA*(T-Ta); rated P<=7500W only in wash/rinse. Explicit host C and UA are assumptions, not manufacturer calibration.',
        'All actual water charge, component thermal capacities, heat losses and heat-up time remain unknown (null). Example test parameters do not become product defaults.',
        'Three serial phases share one effective water node; filling, draining, rinse water exchange, detergent dosing, spray hydraulics, thermal disinfection, soil removal and material transfer are not modeled.',
        'Dry phase is timed only; 7500W is never applied to drying air. No moisture, drying effectiveness or H14 filtration; the drying probe of the panel programmes reads a first-order drying-air node (display only, no energy balance).',
        'Program times are explicit simulation settings, not factory recipes. Wash/rinse timers start at target water temperature; unreachable targets leave the phase running until stopped.',
        'Scene controls require a synchronous adapter; no robot grasp, contact simulation or physical hardware connection.',
        'API truth filtering is implemented for session metadata, observations, responses and logs; it is not a Python introspection or filesystem security sandbox. Do not give agents the trusted host or debug output files.',
        'Request deduplication is in-memory only. No operation/poll/cancel objects; start/stop are synchronous commands controlling subsequent host evolution.']
    host = {'sim.step': {'kind': 'simulation', 'visibility': 'trusted_host_only', 'input_schema': _miele_object({'dt_s': _miele_schema('s', 0, 86400)}), 'binding': 'MielePLW6111Runtime.step(dt_s)', 'partial_failure': 'Process and world clock commit before scene callbacks. SceneSynchronizationError reports possible partial backend effects.'},
        'sim.configure_water_model': {'kind': 'configuration', 'visibility': 'trusted_host_only', 'binding': 'MielePLW6111Runtime.configure_water_model(instance_id, *, effective_heat_capacity_J_K, heat_loss_W_K, initial_temperature_K, maximum_temperature_K, evidence)', 'description_zh': '断电且程序停止时显式初始化有效水热模型；所有参数均为有说明的宿主输入，不是注水或真实硬件重置。'}}
    return _miele_extend_metadata({'asset_types': {MIELE_PLW6111_TYPE: {'catalog_id': 'CHEM-191', 'model': 'Miele PLW 6111 DP SC CL H14, 10958380', 'asset_version': '1.0.1', 'state_slots': slots, 'capabilities': caps, 'declared_capabilities': {}, 'parameters': params}}, 'host_capabilities': host,
        'limitations': limitations, 'trusted_scene_callback': 'scene_adapter(instance_id, updates) receives SI door, three basket extensions, indicated water temperature and program phase; host is sole authority.', 'result_schema_scope': 'capability.output_schema validates response.result only'}, panel)


# ---- G02 front panel: the MP.CON 3 controller of the PLW 6111 (programme table, programme run, menu functions) behind panel_ui.json.
# Operating instructions PLW 6011 / PLW 6111 (REV.0.01, COD.650138) pages are PDF pages; datasheet PLW6111 DP SC CL H14.
from datetime import timedelta as _miele_timedelta

MIELE_PLW6111_ASSET_DIR = Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t1_thermal/miele_plw6111'
_MIELE_CLOCK_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)      # controller clock at simulation time 0 until set (assumption)
_MIELE_A0_LOW_K, _MIELE_A0_REF_K, _MIELE_A0_Z_K = 338.15, 353.15, 10.0   # A0 (EN ISO 15883-1): 10^((T - 80 degC) / 10 K) per s above 65 degC
_MIELE_A0_STEP_S = 1.0                  # integration sub-step of the A0 while the chamber is at or above 65 degC
_MIELE_HOT_K = 338.15                   # p.29, p.32: the 65 degC limit of the HOT MATERIAL warning
_MIELE_COOLING_S = 120.0                # p.38: drying plus 120 s cooling down time
_MIELE_AIR_TAU_S = 120.0                # assumption: time constant of the drying-air probe while the drying fan runs (drying and cooling blocks)
_MIELE_AIR_STILL_TAU_S = 600.0          # assumption: time constant towards the room temperature with the drying fan off
_MIELE_MESSAGES = {'pressure_probe': 'pressure probe'}   # p.47 list of display messages the evaluator can raise (wash pump pressure monitoring fault)
_MIELE_DRAIN_S = 60.0                   # assumption: run time of the Drain programme's pump (p.37 gives no time)
_MIELE_PRINT_S = 20.0                   # assumption: transmission time of one batch protocol over the RS 232 interface
_MIELE_END_TONE_S = 10.0                # assumption: length of the end-of-programme signal
_MIELE_HISTORY = 200                    # p.44: up to 200 batch protocols, the oldest overwritten
_MIELE_SERIAL_KEEP = 200                # protocols kept in the serial-output log of the simulation
_MIELE_CUSTOMER = 20                    # datasheet: 20 free programme positions
_MIELE_WASH_MAX_C, _MIELE_DRY_MAX_C = 95, 150   # p.45 warnings 27 / 28: highest chamber (95 degC) and drying-air (150 degC) settings
_MIELE_TIME_MAX_MIN = 120               # datasheet: time setting 0-120 min in 1-minute increments
_MIELE_LANGUAGES = ('en', 'it', 'de', 'fr', 'es', 'pt', 'nl', 'pl')   # P2.04 0-7 (p.43); which language is which number is an assumption
_MIELE_BLOCKS = ('prewash', 'main_wash', 'main_wash_2', 'main_wash_3', 'neutralisation', 'interim_rinse_1', 'interim_rinse_2', 'final_rinse', 'drying')
_MIELE_PHASE = {'prewash': 'wash', 'main_wash': 'wash', 'main_wash_2': 'wash', 'main_wash_3': 'wash', 'neutralisation': 'wash',
                'interim_rinse_1': 'rinse', 'interim_rinse_2': 'rinse', 'final_rinse': 'rinse', 'drying': 'dry', 'cooling': 'dry', 'drain': 'drain'}
_MIELE_BLOCK_TEXT = {'prewash': 'pre-wash', 'main_wash': 'main wash', 'main_wash_2': 'main wash 2', 'main_wash_3': 'main wash 3', 'neutralisation': 'neutralisation',
                     'interim_rinse_1': 'interim rinse 1', 'interim_rinse_2': 'interim rinse 2', 'final_rinse': 'final rinse', 'drying': 'drying', 'cooling': 'cooling',
                     'drain': 'drain'}


def _miele_b(block, water, dosing, temperature_C, hold_s):
    return {'block': block, 'water': water, 'dosing': dosing, 'temperature_C': temperature_C, 'hold_s': float(hold_s)}


_MIELE_DRY = _miele_b('drying', None, None, 120, 1800)
_MIELE_STANDARD = {   # programme overview p.35-38 (water: CW / HW / DI and mixes, DOS1-4 dosing, degC, holding time s); key: the direct key (p.29)
    'quick_wash': ('QUICK WASH', None, [_miele_b('main_wash', 'HW', 'DOS1', 60, 180), _miele_b('neutralisation', 'HW', 'DOS3', None, 120), _miele_b('final_rinse', 'DI', None, 60, 60), _MIELE_DRY]),
    'universal': ('UNIVERSAL', 'P1', [_miele_b('prewash', 'CW50', None, None, 60), _miele_b('main_wash', 'HW', 'DOS1', 75, 180), _miele_b('neutralisation', 'HW', 'DOS3', None, 120),
                                       _miele_b('interim_rinse_1', 'HW50', None, None, 60), _miele_b('final_rinse', 'DI', None, 75, 60), _MIELE_DRY]),
    'standard': ('STANDARD', 'P2', [_miele_b('main_wash', 'HW', 'DOS1', 70, 180), _miele_b('neutralisation', 'HW', 'DOS3', None, 120), _miele_b('interim_rinse_1', 'HW', None, None, 60),
                                     _miele_b('final_rinse', 'DI', None, 70, 60), _MIELE_DRY]),
    'intensive': ('INTENSIVE', 'P3', [_miele_b('prewash', 'CW50', None, None, 60), _miele_b('main_wash', 'HW', 'DOS1', 80, 180), _miele_b('neutralisation', 'HW', 'DOS3', None, 120),
                                       _miele_b('interim_rinse_1', 'CW50', None, None, 60), _miele_b('interim_rinse_2', 'DI', None, None, 60), _miele_b('final_rinse', 'DI', None, 75, 60), _MIELE_DRY]),
    'inorganic': ('INORGANIC', None, [_miele_b('main_wash', 'CW50', 'DOS3', 50, 120), _miele_b('main_wash_2', 'HW', 'DOS1', 75, 180), _miele_b('neutralisation', 'HW', 'DOS3', None, 120),
                                       _miele_b('interim_rinse_1', 'DI', None, None, 60), _miele_b('interim_rinse_2', 'DI', None, None, 60), _miele_b('final_rinse', 'DI', None, 70, 60), _MIELE_DRY]),
    'organic': ('ORGANIC', None, [_miele_b('main_wash', 'HW', 'DOS1', 65, 180), _miele_b('main_wash_2', 'HW', 'DOS1', 85, 180), _miele_b('neutralisation', 'HW', 'DOS3', None, 120),
                                   _miele_b('interim_rinse_1', 'HW50', None, None, 60), _miele_b('final_rinse', 'DI', None, 75, 60), _MIELE_DRY]),
    'plastic': ('PLASTIC', None, [_miele_b('prewash', 'CW50', None, None, 60), _miele_b('main_wash', 'CW50', 'DOS1', 55, 180), _miele_b('neutralisation', 'CW50', 'DOS3', None, 120),
                                   _miele_b('interim_rinse_1', 'DI50', None, None, 60), _miele_b('final_rinse', 'DI', None, 55, 60), _miele_b('drying', None, None, 60, 1800)]),
    'pipettes': ('PIPETTES', None, [_miele_b('prewash', 'CW50', None, None, 60), _miele_b('main_wash', 'HW', 'DOS1', 70, 180), _miele_b('neutralisation', 'HW', 'DOS3', None, 120),
                                     _miele_b('interim_rinse_1', 'CW50', None, None, 60), _miele_b('interim_rinse_2', 'DI', None, None, 60), _miele_b('final_rinse', 'DI', None, 70, 60),
                                     _miele_b('drying', None, None, 100, 1800)]),
    'oil': ('OIL', None, [_miele_b('main_wash', 'HW', 'DOS4+DOS1', 45, 60), _miele_b('main_wash_2', 'HW', 'DOS4+DOS1', 65, 120), _miele_b('main_wash_3', 'HW', 'DOS1', 85, 180),
                           _miele_b('neutralisation', 'HW', 'DOS3', None, 120), _miele_b('interim_rinse_1', 'HW50', None, None, 60), _miele_b('final_rinse', 'DI', None, 75, 60), _MIELE_DRY]),
    'disin_93_10': ('DISIN 93/10', None, [_miele_b('main_wash', 'CW50', 'DOS1', 93, 600), _miele_b('neutralisation', 'HW', 'DOS3', None, 120), _miele_b('interim_rinse_1', 'HW', None, None, 60),
                                           _miele_b('final_rinse', 'DI', None, 75, 60), _MIELE_DRY]),
    'cold_water_rinse': ('COLD WATER RINSE', None, [_miele_b('prewash', 'CW', None, None, 120)]),
    'ad_rinse': ('AD RINSE', None, [_miele_b('prewash', 'DI', None, None, 120)]),
    'drying': ('DRYING', None, [_MIELE_DRY]),
    'drain': ('DRAIN', None, [_miele_b('drain', None, None, None, _MIELE_DRAIN_S)]),
    **{f'fill_dos{n}': (f'FILL DOS{n}', None, [_miele_b('main_wash', 'HW', f'DOS{n}', None, 10), _miele_b('interim_rinse_1', 'HW', None, None, 10)]) for n in (1, 2, 3, 4)},
}
_MIELE_STANDARD_IDS = tuple(_MIELE_STANDARD)
_MIELE_COPYABLE = tuple(k for k in _MIELE_STANDARD_IDS if k != 'drain' and not k.startswith('fill_dos'))   # holding times in whole minutes
_MIELE_CUSTOMER_IDS = tuple(f'C{n:02d}' for n in range(1, _MIELE_CUSTOMER + 1))
_MIELE_FAULTS = {   # warnings list p.45-46 (1 power fail is a warning after which the programme continues, p.41)
    2: 'open load. door', 4: 'load.door fail.', 7: 'unblock.door 1', 9: 'unlocking 1fail.', 11: 'no cold water', 12: 'no hot water', 13: 'no demin. water',
    14: 'no c.+hot water', 15: 'no c.+demi water', 16: 'no h.+demi water', 17: 'no chemical 1', 18: 'no chemical 2', 19: 'no chemical 3', 20: 'no chemical 4',
    21: 'heating element1', 23: 'drain problem', 24: 'fan problem', 25: 'drying min°C', 26: 'prewash max°C', 27: 'tank probe lim°C', 28: 'dryingprobelim°C',
    29: 'boilerprobelim°C', 30: 'tank probe', 31: 'tank probe 2', 32: 'drying probe', 33: 'boiler probe', 34: 'check temp.', 35: 'Serial connect.1',
    36: 'Serial connect.2', 37: 'CANserialconnec.', 39: 'no tank heating', 40: 'no boilerheating', 41: 'no boilerheating', 42: 'magnetothermic', 43: 'Phase sequence',
    44: 'levels steamcond', 45: 'pump steam cond.', 46: 'pump', 47: 'flowmeter fail.1', 48: 'flowmeter fail.2', 49: 'flowmeter fail.3', 50: 'flowmeter fail.4',
    51: 'flow.cold water', 52: 'flow.warm water', 53: 'flow.demi water', 54: 'airfilter obstr.', 55: 'conduc. probe', 56: 'conductivity', 58: 'no tank heating',
    59: 'no boilerheating', 60: 'TIME', 72: 'DRYING', 73: 'DRYING', 74: 'boilerprobelim°C', 75: 'boiler probe', 76: 'BOILER', 77: 'BOILER', 78: 'BOILER'}
_MIELE_SETTINGS = {   # parameter list p.43: key -> (parameter, schema); user name 16 characters
    'user_name': ('P1.01', {'type': 'string'}), 'printout': ('P1.04', {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': 3}),
    'live_printout': ('P1.05', {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': 1}), 'buzzer_key': ('P1.07', {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': 50}),
    'buzzer_end': ('P1.08', {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': 50}), 'buzzer_alarm': ('P1.09', {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': 50}),
    'show_a0': ('P1.13', {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': 1}), 'language': ('P2.04', {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': 7}),
    'font': ('P2.07', {'type': 'integer', 'x-unit': '1', 'minimum': 0, 'maximum': 2})}
_MIELE_SETTINGS_DEFAULT = {'user_name': '', 'printout': 0, 'live_printout': 0, 'buzzer_key': 25, 'buzzer_end': 25, 'buzzer_alarm': 25, 'show_a0': 1, 'language': 0, 'font': 0}
_MIELE_NAME_CHARS = ' ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-./'
_MIELE_BUSY = ('running', 'interrupted', 'malfunction', 'power_fail')


def _miele_int(minimum=None, maximum=None, unit='1'):
    out = {'type': 'integer', 'x-unit': unit}
    if minimum is not None: out['minimum'] = minimum
    if maximum is not None: out['maximum'] = maximum
    return out


def _miele_enum(values):
    return {'type': 'string', 'enum': list(values)}


_MIELE_BLOCK_SCHEMA = {'type': 'object', 'properties': {'block': _miele_enum(_MIELE_BLOCKS + ('cooling', 'drain')), 'index': _miele_int(0), 'count': _miele_int(1),
                                                         'target_K': {'anyOf': [{'type': 'number', 'x-unit': 'K'}, {'type': 'null'}]}, 'hold_s': {'type': 'number', 'x-unit': 's', 'minimum': 0}},
                       'required': ['block', 'index', 'count', 'target_K', 'hold_s'], 'additionalProperties': False}
_MIELE_NEW_SLOTS = {   # id: (unit, origin, meaning, schema, visibility)
    'programme.selected': ('1', 'setpoint', '所选程序：18个标准程序（p.33–38）或客户程序C01–C20；P1/P2/P3/P+选择', _miele_enum(_MIELE_STANDARD_IDS + _MIELE_CUSTOMER_IDS)),
    'programme.drying': ('1', 'setpoint', 'DRY键：本次程序是否做干燥（含120 s冷却）；选程序时回到该程序的默认', {'type': 'boolean'}),
    'programme.status': ('1', 'derived', '程序状态：ready、running、interrupted（STOP一次）、end、cancelled（STOP两次）、malfunction（故障，门锁住）、power_fail（断电暂停，来电重复当前阶段）',
                         _miele_enum(('ready', 'running', 'interrupted', 'end', 'cancelled', 'malfunction', 'power_fail'))),
    'programme.block': ('1', 'derived', '面板程序当前阶段（序号、阶段名、目标温度、保持时间）；未运行面板程序为null', {'anyOf': [_MIELE_BLOCK_SCHEMA, {'type': 'null'}]}),
    'programme.remaining_s': ('s', 'derived', '剩余时间：当前及后续阶段的保持时间，加当前阶段加热到目标的估计；未运行为null', {'anyOf': [{'type': 'number', 'x-unit': 's', 'minimum': 0}, {'type': 'null'}]}),
    'programme.cycle_counter': ('1', 'derived', '循环计数（显示右上角），每次面板程序启动加1', _miele_int(0)),
    'programme.a0': ('s', 'derived', '本次/上次面板程序的A0值（EN ISO 15883-1：65 °C以上∫10^((T−80 °C)/10 K)dt），P1.13打开时显示', {'anyOf': [{'type': 'number', 'minimum': 0}, {'type': 'null'}]}),
    'programme.max_temperature_k': ('K', 'derived', '本次/上次面板程序的腔体最高显示温度（结束窗口MAX TEMP.）', {'anyOf': [{'type': 'number', 'x-unit': 'K'}, {'type': 'null'}]}),
    'door.locked': ('1', 'derived', '电子门锁：程序运行、中断、故障或断电暂停时锁住（p.24、p.40），机械门操作被拒绝', {'type': 'boolean'}),
    'faults.active': ('1', 'derived', '当前故障（BLOCK Exx与短文，p.45–46）；复位方法见p.41', {'anyOf': [{'type': 'object', 'properties': {'code': _miele_int(1), 'text': {'type': 'string'}},
                                                                                                      'required': ['code', 'text'], 'additionalProperties': False}, {'type': 'null'}]}),
    'warnings.active': ('1', 'derived', '当前提示（如power fail），PRG按两次显示（p.31）', {'type': 'array', 'items': {'type': 'object'}}),
    'messages.active': ('1', 'derived', '显示消息（p.47表14.2，不中断程序，显示在左下角并可PRG两次查看）：pressure probe洗涤泵压力监测故障（只由评测者fault.inject注入）',
                        {'type': 'array', 'items': {'type': 'string'}}),
    'drying.air_temperature_k': ('K', 'process_truth', '干燥空气温度真值：干燥机（4 kW、150 m³/h，数据表）在干燥阶段把空气一阶逼近该阶段温度（τ 120 s，假设），冷却阶段风机吹向室温（τ 120 s），'
                                 '其余时间静置趋向室温（τ 600 s，假设）；不影响水温与干燥效果', {'type': 'number', 'x-unit': 'K'}, 'debug'),
    'drying.indicated_air_temperature_k': ('K', 'derived', '干燥探头（drying probe）显示：干燥空气温度取0.1 K，程序运行时PRG按一次显示（p.31 Fig. 2）', {'type': 'number', 'x-unit': 'K'}),
    'programmes.customer': ('1', 'setpoint', '客户程序位C01–C20（null为空）：名称、来源、激活与9个阶段（激活、温度°C、时间min）', {'type': 'array', 'minItems': _MIELE_CUSTOMER, 'maxItems': _MIELE_CUSTOMER}),
    'programmes.active': ('1', 'setpoint', 'Programme selection菜单：标准程序是否激活（未激活的程序不能选）', {'type': 'object'}),
    'settings.values': ('1', 'setpoint', '参数P1.01用户名、P1.04程序结束打印、P1.05当前程序结果打印、P1.07/08/09蜂鸣音量、P1.13显示A0、P2.04语言、P2.07字体（p.43）', {'type': 'object'}),
    'clock.offset_s': ('s', 'setpoint', '控制器时钟相对仿真时间的偏移（clock.set_date / clock.set_time）', {'type': 'number', 'x-unit': 's'}),
    'clock.datetime': ('1', 'derived', '控制器实时钟（p.44），读取时计算', {'type': 'string'}),
    'history.records': ('1', 'derived', '批次记录（最多200条，满后覆盖最旧，p.44）：循环号、程序、起止时刻、结果、最高温度、A0、用户', {'type': 'array', 'items': {'type': 'object'}}),
    'documentation.serial_output': ('1', 'derived', 'RS 232过程记录接口发出的批次记录（打印或文档软件接收；仿真保留最近200条）', {'type': 'array', 'items': {'type': 'object'}}),
    'documentation.printing': ('1', 'derived', '正在发送的批次记录（Printing in progress），null为空闲', {'anyOf': [{'type': 'object'}, {'type': 'null'}]}),
    'buzzer.state': ('1', 'derived', '蜂鸣器：程序结束信号（P1.08音量）或故障连续报警（P1.09音量），null为静音', {'anyOf': [{'type': 'object'}, {'type': 'null'}]}),
    'access.user_password': ('1', 'setpoint', 'USER/ADMIN密码（4位数字；调试可见，调试时由宿主设定，代替Miele服务的调试移交）', _miele_int(0, 9999), 'debug'),
}
_MIELE_COMPUTED = ('clock.datetime', 'door.locked', 'programme.remaining_s')


def _miele_new_slot(sid):
    unit, origin, meaning, schema, *vis = _MIELE_NEW_SLOTS[sid]
    out = {'unit': unit, 'origin': origin, 'component_id': 'door' if sid == 'door.locked' else 'data_port' if sid.startswith('documentation.') else 'display',
           'meaning_zh': meaning, 'visibility': vis[0] if vis else 'agent', 'access': 'read_only', 'write_via': None, 'value_schema': schema,
           'reference_conditions': None, 'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None},
           'read_binding': "MielePLW6111Session.read(instance_id=..., state_ids=['" + sid + "'])"}
    if origin == 'derived':
        out['observation_note_zh'] = '仿真派生观测标记estimated；缺测标记unavailable；不是厂家实测数据。'
    return out


_MIELE_CAPS = {   # MP.CON 3 functions: name -> (kind, properties, optional ('*': any non-empty subset), output, observes, meaning)
    'programme.select': ('device_command', {'programme_id': _miele_enum(_MIELE_STANDARD_IDS + _MIELE_CUSTOMER_IDS)}, (), {'programme_id': {'type': 'string'}},
                         ['programme.selected', 'programme.drying', 'programme.status'], '选择程序（P1通用、P2标准、P3强化、P+翻全部程序，p.29–30）；只在待机且程序已激活时；清除结束/取消窗口'),
    'programme.set_drying': ('device_command', {'enabled': {'type': 'boolean'}}, (), {'enabled': {'type': 'boolean'}}, ['programme.drying'], 'DRY键：启动前打开或关闭干燥功能（p.29）；所选程序须含干燥阶段'),
    'programme.start': ('device_command', {}, (), {'running': {'type': 'boolean'}, 'cycle': _miele_int(1)}, ['programme.status', 'program.running', 'programme.cycle_counter'],
                        'START：运行所选程序的阶段表（p.35–38）：有温度的阶段先以7500 W把水加热到目标再计保持时间，无温度的阶段只计时，干燥阶段计时（不算热风）后冷却120 s；需门关、篮架归位、宿主已配置水热模型（不需要水的程序除外）'),
    'programme.interrupt': ('device_command', {}, (), {'status': {'type': 'string'}}, ['programme.status', 'door.locked'], 'STOP一次：中断程序（NO DISINFECTION，门保持锁住，停止加热，p.29）'),
    'programme.resume': ('device_command', {}, (), {'status': {'type': 'string'}}, ['programme.status', 'program.running'], '中断后按START：继续程序，重复当前阶段（p.29）'),
    'programme.cancel': ('device_command', {}, (), {'status': {'type': 'string'}}, ['programme.status', 'door.locked', 'history.records'],
                         'STOP两次：取消程序并开锁；窗口NO DISINFECTION，取消时腔体高于65 °C另示HOT MATERIAL（p.29、p.32）'),
    'faults.reset': ('device_command', {}, (), {'status': {'type': 'string'}}, ['programme.status', 'faults.active', 'door.locked'], '故障复位：STOP+START按住5 s，再按P2、P1（p.41）；复位后待机、门可开'),
    'programmes.copy': ('configuration', {'source': _miele_enum(_MIELE_COPYABLE), 'position': _miele_int(1, _MIELE_CUSTOMER)}, (), {'programme_id': {'type': 'string'}}, ['programmes.customer'],
                        '把标准程序复制到客户程序位（Standard programmes / Customer programmes菜单，p.42）；已占用的位被覆盖'),
    'programmes.set_block': ('configuration', {'position': _miele_int(1, _MIELE_CUSTOMER), 'block': _miele_enum(_MIELE_BLOCKS), 'active': {'type': 'boolean'},
                                               'temperature_C': _miele_int(0, _MIELE_DRY_MAX_C, 'degC'), 'time_min': _miele_int(0, _MIELE_TIME_MAX_MIN, 'min')}, (),
                             {'programme_id': {'type': 'string'}}, ['programmes.customer'],
                             '编辑客户程序的一个阶段：激活、温度（1 °C步进，0为不加热；洗涤阶段≤95 °C、干燥≤150 °C）、时间（1 min步进，0–120 min，datasheet）'),
    'programmes.delete': ('configuration', {'position': _miele_int(1, _MIELE_CUSTOMER)}, (), {'programme_id': {'type': 'string'}}, ['programmes.customer', 'programme.selected'], '删除客户程序位'),
    'programmes.set_active': ('configuration', {'programme_id': _miele_enum(_MIELE_STANDARD_IDS + _MIELE_CUSTOMER_IDS), 'active': {'type': 'boolean'}}, (),
                              {'programme_id': {'type': 'string'}, 'active': {'type': 'boolean'}}, ['programmes.active', 'programmes.customer', 'programme.selected'],
                              'Programme selection菜单：激活/停用程序（p.42）；至少保留一个激活程序；停用所选程序时选择第一个激活程序'),
    'settings.configure': ('configuration', {k: v for k, (_, v) in _MIELE_SETTINGS.items()}, '*', {k: v for k, (_, v) in _MIELE_SETTINGS.items()}, ['settings.values'],
                           '参数P1.01–P2.07（p.43）：用户名（16字符）、程序结束打印方式（0无、1图形、2表格、3 USB）、当前程序结果打印、按键/结束/报警蜂鸣音量（0关）、显示A0、语言、字体'),
    'clock.set_date': ('configuration', {'day': _miele_int(1, 31), 'month': _miele_int(1, 12), 'year': _miele_int(2000, 2099)}, (), {'date': {'type': 'string'}},
                       ['clock.offset_s', 'clock.datetime'], '设定实时钟日期（USER/ADMIN级的时间设定，p.42、p.44）'),
    'clock.set_time': ('configuration', {'hour': _miele_int(0, 23), 'minute': _miele_int(0, 59)}, (), {'time': {'type': 'string'}},
                       ['clock.offset_s', 'clock.datetime'], '设定实时钟时刻（秒归零）'),
    'history.print': ('device_command', {'cycle': _miele_int(1)}, (), {'cycle': _miele_int(1)}, ['documentation.printing', 'documentation.serial_output'],
                      'History > Print：经RS 232发送一条批次记录（Printing in progress，发送时间20 s为假设，p.44）'),
    'history.cancel_print': ('device_command', {}, (), {'cancelled': {'type': 'boolean'}}, ['documentation.printing'], '打印中按STOP：取消发送（p.44）'),
}


def _miele_capability(name, kind, props, optional, output, observes, meaning, visibility='agent'):
    required = [] if optional == '*' else [k for k in props if k not in optional]
    schema = {'type': 'object', 'properties': props, 'required': required, 'additionalProperties': False}
    if optional == '*':
        schema['minProperties'] = 1
    return {'availability': 'implemented', 'visibility': visibility, 'kind': kind, 'component_ids': ['display', 'data_port'] if name.startswith('history.') else ['display'],
            'description_zh': meaning, 'input_schema': schema,
            'output_schema': {'type': 'object', 'properties': output, 'required': list(output), 'additionalProperties': False}, 'example_arguments': [],
            'observes': list(observes), 'preconditions': [{'description_zh': '已上电；程序运行、中断、故障或断电暂停时只接受与该状态对应的命令', 'state_ids': ['power.enabled', 'programme.status'],
                                                            'enforced_by': 'adapter', 'check': 'power.enabled and programme.status allows the command'}],
            'state_effects': [{'state_id': sid, 'timing': 'immediate', 'description_zh': meaning} for sid in observes],
            'scene_backend_required': False, 'binding': "basic40_runtime_thermal.MielePLW6111Session.invoke(instance_id=..., capability_id='" + name + "', arguments=..., request_id=...)",
            'completion_criterion_zh': '同步返回即命令已被控制器接受；程序随后由宿主step推进'}


def _miele_panel():
    return _g02_panel('miele_plw6111', MIELE_PLW6111_ASSET_DIR, set(_MIELE_CAPS) | {'power.set', 'washing.stop', 'thermal.set_temperature', 'washing.start'})


def _miele_extend_metadata(meta, panel):
    """The MP.CON 3 functions, the evaluator-only fault injection and the panel commands beside the original eight commands; new state slots."""
    spec = meta['asset_types'][MIELE_PLW6111_TYPE]
    examples = {'programme.select': {'programme_id': 'universal'}, 'programme.set_drying': {'enabled': False}, 'programme.start': {}, 'programme.interrupt': {},
                'programme.resume': {}, 'programme.cancel': {}, 'faults.reset': {}, 'programmes.copy': {'source': 'universal', 'position': 1},
                'programmes.set_block': {'position': 1, 'block': 'main_wash', 'active': True, 'temperature_C': 80, 'time_min': 5}, 'programmes.delete': {'position': 1},
                'programmes.set_active': {'programme_id': 'oil', 'active': False}, 'settings.configure': {'buzzer_key': 10}, 'clock.set_date': {'day': 9, 'month': 2, 'year': 2026},
                'clock.set_time': {'hour': 20, 'minute': 32}, 'history.print': {'cycle': 1}, 'history.cancel_print': {}}
    controller = {}
    for name, (kind, props, optional, output, observes, meaning) in _MIELE_CAPS.items():
        controller[name] = _miele_capability(name, kind, props, optional, output, observes, meaning)
        controller[name]['example_arguments'] = [examples[name]]
    evaluator = {'diagnostics.inject_fault': _miele_capability('diagnostics.inject_fault', 'simulation', {'code': _miele_int(2, 78)}, (), {'code': _miele_int(2)},
                                                               ['programme.status', 'faults.active', 'buzzer.state'],
                                                               '评测者：注入警告表中的一个故障（p.45–46，代码须在表内）：运行中的程序被取消，门保持锁住，显示BLOCK Exx，直到按p.41复位', visibility='evaluator')}
    evaluator['diagnostics.inject_fault']['example_arguments'] = [{'code': 39}]
    evaluator['fault.inject'] = _miele_capability('fault.inject', 'simulation', {'code': _miele_enum(tuple(_MIELE_MESSAGES))}, (), {'code': {'type': 'string'}},
                                                  ['messages.active'], '评测者：注入手册p.47的显示消息故障。pressure_probe：洗涤泵压力监测故障，显示pressure probe（左下角消息行，PRG两次的提示页）；'
                                                  '它属于不中断程序的消息（p.31），程序照常运行', visibility='evaluator')
    evaluator['fault.inject']['example_arguments'] = [{'code': 'pressure_probe'}]
    evaluator['fault.clear'] = _miele_capability('fault.clear', 'simulation', {'code': _miele_enum(tuple(_MIELE_MESSAGES))}, (), {'code': {'type': 'string'}},
                                                 ['messages.active'], '评测者：排除注入的消息故障（相当于服务修好压力监测，p.45“消息保留到原因排除”）', visibility='evaluator')
    evaluator['fault.clear']['example_arguments'] = [{'code': 'pressure_probe'}]
    for sid in _MIELE_NEW_SLOTS:
        spec['state_slots'][sid] = _miele_new_slot(sid)
    spec['state_slots']['program.phase']['value_schema'] = {'type': 'string', 'enum': ['idle', 'wash', 'rinse', 'dry', 'drain', 'completed', 'stopped']}
    panel_caps = {}
    if panel:
        p = _miele_panel()
        called = p['engine'].spec_capabilities(p['spec'])
        caps = {**spec['capabilities'], **controller}
        effects = list({e['state_id']: dict(e, description_zh='面板动作调用的能力的效果：' + e['description_zh']) for c in called for e in caps[c]['state_effects']
                        if e['timing'] == 'immediate'}.values())
        panel_caps = _g02_panel_capabilities(p, "basic40_runtime_thermal.MielePLW6111Session.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)", effects)
    spec.update(controller_capabilities=controller, evaluator_capabilities=evaluator, panel_capabilities=panel_caps, asset_version='1.3.1')   # 1.2.0: fault.inject / clear; 1.3.0: control-strip 3D parts (TFT, touch keys, power rocker); 1.3.1: door precondition text, BUSY declared (2026-10-22)
    meta['limitations'] = meta['limitations'] + [
        'MP.CON 3 functions behind the front panel: the 18 standard programmes of the programme overview (p.35-38) and 20 customer positions run block by block on the '
        'same single water node (heated blocks heat at 7500 W to the block temperature, then hold; unheated blocks only time; drying is timed, then 120 s cooling). '
        'The drying probe reads a first-order drying-air node (2026-10-19): the drying unit drives the air to the drying block temperature, the cooling block '
        'blows room air (time constant 120 s), otherwise the duct settles to the room temperature (600 s); time constants assumed, no effect on the water or on '
        'the drying result. Filling, draining, water types, dosing and spray pressure are not modelled; the water keeps its temperature from block to block.',
        'Interrupt / resume / cancel, fault reset, automatic restart after a power failure (the current block is repeated), A0 and maximum temperature, cycle counter, '
        '200 batch protocols, RS 232 protocol output with printing time, buzzer settings, parameters P1.01-P2.07 (P2.07 font: every simulated text is in the '
        'European table, so the choice changes no drawn text), programme selection and the real-time clock are implemented; the pressure probe message (wash pump '
        'pressure monitoring fault, p.47) and the warnings-list faults occur only by evaluator injection (fault.inject, diagnostics.inject_fault); the service level, '
        'salt and chemical levels (no consumables in the simulation) and USB printout are not.']
    return meta


class _MieleMPCon:
    """The MP.CON 3 controller logic of one host (mixed into MielePLW6111Runtime): programme table and run, A0, history, printing, panel host."""

    @staticmethod
    def _mp_defaults():
        return {'programme.selected': 'universal', 'programme.drying': True, 'programme.status': 'ready', 'programme.block': None, 'programme.cycle_counter': 0,
                'programme.a0': None, 'programme.max_temperature_k': None, 'faults.active': None, 'warnings.active': [], 'messages.active': [],
                'programmes.customer': [None] * _MIELE_CUSTOMER, 'programmes.active': {k: True for k in _MIELE_STANDARD_IDS},
                'settings.values': dict(_MIELE_SETTINGS_DEFAULT), 'clock.offset_s': 0.0, 'history.records': [], 'documentation.serial_output': [],
                'documentation.printing': None, 'buzzer.state': None, 'access.user_password': 0}

    # ---- programmes
    @staticmethod
    def _mp_entry(state, pid):
        if pid in _MIELE_STANDARD:
            name, _, blocks = _MIELE_STANDARD[pid]
            return {'name': name, 'blocks': [dict(b) for b in blocks], 'active': state['programmes.active'][pid]}
        entry = state['programmes.customer'][int(pid[1:]) - 1]
        if entry is None:
            return None
        blocks = []
        for bid in _MIELE_BLOCKS:
            b = entry['blocks'][bid]
            if b['active']:
                blocks.append(_miele_b(bid, b['water'], b['dosing'], b['temperature_C'] or None, b['time_min'] * 60))
        return {'name': entry['name'], 'blocks': blocks, 'active': entry['active']}

    def _mp_order(self, state):
        """Programme list of P+ (p.30): the standard programmes in the manual's order, then the defined customer programmes; only active ones."""
        out = []
        for pid in _MIELE_STANDARD_IDS + _MIELE_CUSTOMER_IDS:
            e = self._mp_entry(state, pid)
            if e is not None and e['active']:
                out.append(pid)
        return out

    def _mp_has_drying(self, state, pid):
        e = self._mp_entry(state, pid)
        return e is not None and pid != 'drying' and any(b['block'] == 'drying' for b in e['blocks'])

    def _mp_run_blocks(self, state):
        """Blocks of the selected programme as run: drying off by DRY, drying expanded into drying + 120 s cooling (p.38)."""
        pid = state['programme.selected']
        out = []
        for b in self._mp_entry(state, pid)['blocks']:
            if b['block'] == 'drying':
                if not state['programme.drying'] and pid != 'drying':
                    continue
                out.append(dict(b))
                out.append(_miele_b('cooling', None, None, None, _MIELE_COOLING_S))
            else:
                out.append(dict(b))
        return out

    def _mp_heats(self, inst, blk):
        s = inst['state']
        return (blk['temperature_C'] is not None and blk['block'] not in ('drying', 'cooling', 'drain') and s['power.enabled'] and inst['water_configuration'] is not None
                and blk['temperature_C'] + 273.15 > self.ambient_temperature_K)

    def _mp_enter(self, inst, index):
        s, run = inst['state'], inst['mp']['run']
        run['index'] = index
        blk = run['blocks'][index]
        target = None if blk['temperature_C'] is None else blk['temperature_C'] + 273.15
        s['program.phase'], s['program.phase_elapsed_s'] = _MIELE_PHASE[blk['block']], 0.0
        if self._mp_heats(inst, blk):
            s['thermal.target_k'] = target
        s['programme.block'] = {'block': blk['block'], 'index': index, 'count': len(run['blocks']), 'target_K': target, 'hold_s': blk['hold_s']}

    def _mp_clock(self, inst, t=None):
        return _MIELE_CLOCK_EPOCH + _miele_timedelta(seconds=(self.time_s if t is None else t) + inst['state']['clock.offset_s'])

    def _mp_record(self, inst, result, t):
        s, run = inst['state'], inst['mp']['run']
        rec = {'cycle': run['cycle'], 'programme': run['programme'], 'name': run['name'], 'start': run['start'], 'end': self._mp_clock(inst, t).strftime('%d.%m.%y %H:%M'),
               'result': result, 'max_temperature_C': None if s['programme.max_temperature_k'] is None else round(s['programme.max_temperature_k'] - 273.15, 1),
               'a0': None if s['programme.a0'] is None else int(math.floor(s['programme.a0'] + 0.5)), 'user': s['settings.values']['user_name']}
        s['history.records'] = (s['history.records'] + [rec])[-_MIELE_HISTORY:]
        return rec

    @staticmethod
    def _mp_lines(rec):
        return ['PLW 6111', f"CYCLE {rec['cycle']:05d}", f"PROGRAMME {rec['name']}", f"START {rec['start']}", f"END {rec['end']}",
                'MAX TEMP. ' + ('---' if rec['max_temperature_C'] is None else f"{rec['max_temperature_C']:.1f} C"), 'A0 ' + ('----' if not rec['a0'] else str(rec['a0'])),
                f"RESULT {rec['result']}", f"USER {rec['user']}"]

    def _mp_serial(self, s, entry):
        s['documentation.serial_output'] = (s['documentation.serial_output'] + [entry])[-_MIELE_SERIAL_KEEP:]

    def _mp_end_run(self, inst, status, result, t, *, locked=False):
        """Programme over (end, cancel or fault): batch protocol, end-of-programme printout (P1.04), signal; the run is kept for the end windows."""
        s, run = inst['state'], inst['mp']['run']
        s.update({'program.running': False, 'program.phase': 'completed' if status == 'end' else 'stopped', 'programme.status': status, 'programme.block': None})
        rec = self._mp_record(inst, result, t)
        mode = s['settings.values']['printout']
        if mode in (1, 2) and s['documentation.printing'] is None:
            s['documentation.printing'] = {'cycle': rec['cycle'], 'format': 'graphic' if mode == 1 else 'table', 'until_s': t + _MIELE_PRINT_S, 'source': 'end'}
        volume = s['settings.values']['buzzer_end']
        if status == 'end' and volume:
            s['buzzer.state'] = {'tone': 'end', 'volume': volume, 'until_s': t + _MIELE_END_TONE_S}
        inst['mp']['last'] = {'programme': run['programme'], 'name': run['name'], 'hot': s['water.indicated_temperature_k'] is not None and s['water.indicated_temperature_k'] > _MIELE_HOT_K}
        inst['mp']['run'] = None

    def _mp_next_block(self, inst, t):
        s, run = inst['state'], inst['mp']['run']
        blk = run['blocks'][run['index']]
        if s['settings.values']['live_printout']:
            line = f"{_MIELE_BLOCK_TEXT[blk['block']]} {int(blk['hold_s'])} s" + ('' if blk['temperature_C'] is None else f" {blk['temperature_C']} C")
            self._mp_serial(s, {'cycle': run['cycle'], 'format': 'live', 'lines': [line]})
        if run['index'] + 1 < len(run['blocks']):
            self._mp_enter(inst, run['index'] + 1)
        else:
            self._mp_end_run(inst, 'end', 'OK', t)

    # ---- drying air, water, A0, maximum
    def _mp_air(self, inst, dt_s, blk):
        """The drying-air probe over dt_s (Fig. 2; warnings 25 / 28 name its limits): in a drying block of a running programme the drying unit drives
        the air towards the block temperature, in the cooling block the fan blows room air, otherwise the duct settles to the room temperature."""
        if dt_s <= 0:
            return
        s = inst['state']
        fan = blk is not None and blk['block'] in ('drying', 'cooling') and s['power.enabled'] and s['programme.status'] == 'running'
        heat = fan and blk['block'] == 'drying' and blk['temperature_C'] is not None
        goal = blk['temperature_C'] + 273.15 if heat else self.ambient_temperature_K
        tau = _MIELE_AIR_TAU_S if fan else _MIELE_AIR_STILL_TAU_S
        t = goal + (s['drying.air_temperature_k'] - goal) * math.exp(-dt_s / tau)
        s['drying.air_temperature_k'], s['drying.indicated_air_temperature_k'] = t, self._indicate(t)

    def _mp_water(self, inst, dt_s, heating, wet=True):
        """Water over dt_s; in the wet blocks also the A0 (the dry blocks have no wash water in the chamber)."""
        if dt_s <= 0 or inst['water_configuration'] is None:
            return
        s = inst['state']
        start = s['water.temperature_k']
        rises = heating and s['thermal.target_k'] >= _MIELE_A0_LOW_K
        if not wet or (start < _MIELE_A0_LOW_K and not rises):                # dry block, or monotonic below 65 degC: no A0 in this interval
            self._water_step(inst, dt_s, heating)
            self._mp_track(s)
            return
        n = max(1, int(math.ceil(dt_s / _MIELE_A0_STEP_S - 1e-9)))
        h = dt_s / n
        a0 = lambda t: 10.0 ** ((t - _MIELE_A0_REF_K) / _MIELE_A0_Z_K) if t >= _MIELE_A0_LOW_K else 0.0   # noqa: E731
        for _ in range(n):
            before = s['water.temperature_k']
            self._water_step(inst, h, heating)
            s['programme.a0'] = (s['programme.a0'] or 0.0) + h * (a0(before) + a0(s['water.temperature_k'])) / 2.0
            self._mp_track(s)

    @staticmethod
    def _mp_track(s):
        t = s['water.indicated_temperature_k']
        if t is not None and (s['programme.max_temperature_k'] is None or t > s['programme.max_temperature_k']):
            s['programme.max_temperature_k'] = t

    def _mp_advance(self, inst, dt_s, t0):
        """A panel programme over dt_s: heat-up to each heated block's temperature, holding times, block order, end (exact event splitting)."""
        s, mp = inst['state'], inst['mp']
        remaining = dt_s
        while remaining > 1e-10 and s['program.running'] and mp['run'] is not None:
            run = mp['run']
            blk = run['blocks'][run['index']]
            heating = self._mp_heats(inst, blk)
            wet = blk['block'] not in ('drying', 'cooling', 'drain')
            if heating:
                wait = self._time_to_target(inst)
                if wait > 0:
                    advance = min(remaining, wait)
                    self._mp_water(inst, advance, True, wet)
                    self._mp_air(inst, advance, blk)
                    remaining -= advance
                    if wait > advance:
                        break
                    s['water.temperature_k'] = s['thermal.target_k']
                    s['water.indicated_temperature_k'] = self._indicate(s['thermal.target_k'])
                    self._mp_track(s)
            left = blk['hold_s'] - s['program.phase_elapsed_s']
            advance = min(remaining, max(0.0, left))
            self._mp_water(inst, advance, heating, wet)
            self._mp_air(inst, advance, blk)
            s['program.phase_elapsed_s'] += advance
            remaining -= advance
            if left - advance <= 1e-9:
                self._mp_next_block(inst, t0 + dt_s - remaining)
            else:
                break
        if remaining > 1e-10:
            self._water_step(inst, remaining, False)
            self._mp_air(inst, remaining, None)

    def _mp_timers(self, inst, t_end):
        """Printing and the end signal finish at their deadlines."""
        s = inst['state']
        p = s['documentation.printing']
        if p is not None and p['until_s'] <= t_end + 1e-9:
            rec = next((r for r in s['history.records'] if r['cycle'] == p['cycle']), None)
            if rec is not None:
                self._mp_serial(s, {'cycle': p['cycle'], 'format': p['format'], 'lines': self._mp_lines(rec)})
            s['documentation.printing'] = None
        b = s['buzzer.state']
        if b is not None and b.get('until_s') is not None and b['until_s'] <= t_end + 1e-9:
            s['buzzer.state'] = None

    def _mp_remaining(self, inst):
        s, run = inst['state'], inst['mp']['run']
        if run is not None and s['programme.status'] in ('running', 'interrupted', 'power_fail'):
            blk = run['blocks'][run['index']]
            total = sum(b['hold_s'] for b in run['blocks'][run['index'] + 1:]) + max(0.0, blk['hold_s'] - s['program.phase_elapsed_s'])
            if s['program.running'] and self._mp_heats(inst, blk):
                wait = self._time_to_target(inst)
                total += wait if math.isfinite(wait) else 0.0
            return total
        if run is None and s['program.running'] and s['program.configured']:            # a host-timed programme (washing.start)
            order = ['wash', 'rinse', 'dry']
            i = order.index(s['program.phase'])
            return max(0.0, s['program.' + s['program.phase'] + '_s'] - s['program.phase_elapsed_s']) + sum(s['program.' + p + '_s'] for p in order[i + 1:])
        return None

    @staticmethod
    def _mp_locked(s):
        return bool(s['program.running']) or s['programme.status'] in _MIELE_BUSY

    def _mp_observed(self, inst, sid):
        s = inst['state']
        if sid == 'clock.datetime':
            return self._mp_clock(inst).strftime('%Y-%m-%dT%H:%M:%S')
        if sid == 'door.locked':
            return self._mp_locked(s)
        if sid == 'programme.remaining_s':
            return self._mp_remaining(inst)
        return _miele_copy.deepcopy(s[sid])

    # ---- the panel host
    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame), created on first use."""
        if instance_id not in self._mp_panels:
            if instance_id not in self._instances: raise ValueError('Unknown instance')
            p = _miele_panel()
            host = _G02PanelHost(p['engine'], lambda: self._mp_panel_read(instance_id),
                                 lambda cap, args: (self._panel_caller or self._panel_session)._checked(instance_id, self._instances[instance_id], cap, args, None),
                                 lambda: self.time_s)
            self._mp_panels[instance_id] = p['engine'].PanelUI(p['spec'], host)
        return self._mp_panels[instance_id]

    def _mp_panel_read(self, instance_id):
        """What the MP.CON 3 shows: the agent-visible slots plus host values (screen, names, temperatures in degC, clock, password)."""
        inst = self._instances[instance_id]
        s, mp = inst['state'], inst['mp']
        slots = self._metadata['asset_types'][MIELE_PLW6111_TYPE]['state_slots']
        out = {sid: self._mp_observed(inst, sid) for sid in slots if slots[sid]['visibility'] == 'agent'}
        run = mp['run']
        status = s['programme.status']
        screen = 'running' if s['program.running'] else status if status in ('interrupted', 'malfunction', 'power_fail') else 'ready'
        sel = self._mp_entry(s, s['programme.selected'])
        if run is not None:
            name = run['name']
        elif s['program.running']:
            name = 'HOST TIMER'
        elif status in ('end', 'cancelled') and mp['last']:
            name = mp['last']['name']
        else:
            name = sel['name'] if sel else ''
        blk = s['programme.block']
        phase = _MIELE_BLOCK_TEXT[blk['block']] if blk else ({'wash': 'wash', 'rinse': 'rinse', 'dry': 'drying'}.get(s['program.phase']) if s['program.running'] else None)
        t1 = s['water.indicated_temperature_k']
        remaining = self._mp_remaining(inst)
        order = self._mp_order(s)
        cur = s['programme.selected']
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else (order[0] if order else cur)
        uses_water = sel is not None and any(b['block'] not in ('drying', 'cooling', 'drain') for b in self._mp_run_blocks(s))
        now = self._mp_clock(inst)
        records = s['history.records']
        out.update({'host.powered': s['power.enabled'], 'host.power_epoch': mp['power_epoch'], 'host.screen': screen, 'host.programme_name': name,
                    'host.phase': phase, 'host.t1_c': None if t1 is None else round(t1 - 273.15, 6),
                    'host.sp_c': None if not blk or blk['target_K'] is None else round(blk['target_K'] - 273.15, 6),
                    'host.remaining_min': None if remaining is None else int(math.ceil(remaining / 60.0 - 1e-9)),
                    'host.a0': None if s['programme.a0'] is None or s['programme.a0'] < 0.5 else int(math.floor(s['programme.a0'] + 0.5)),
                    'host.max_c': None if s['programme.max_temperature_k'] is None else round(s['programme.max_temperature_k'] - 273.15, 6),
                    'host.date': now.strftime('%d.%m.%y'), 'host.time': now.strftime('%H:%M'), 'host.day': now.day, 'host.month': now.month, 'host.year': now.year,
                    'host.hour': now.hour, 'host.minute': now.minute, 'host.counter': f"{s['programme.cycle_counter']:05d}",
                    'host.next_programme': nxt, 'host.has_drying': self._mp_has_drying(s, cur), 'host.user_password': s['access.user_password'],
                    'host.printing': s['documentation.printing'] is not None, 'host.hot': t1 is not None and t1 > _MIELE_HOT_K,
                    'host.last_hot': bool(mp['last'] and mp['last']['hot']), 'host.water_missing': uses_water and inst['water_configuration'] is None,
                    'host.fault_code': None if s['faults.active'] is None else f"BLOCK E{s['faults.active']['code']:02d}",
                    'host.fault_text': None if s['faults.active'] is None else s['faults.active']['text'],
                    'host.warning_1': ([w['text'] for w in s['warnings.active']] + s['messages.active'] + [None])[0],
                    'host.warning_2': ([w['text'] for w in s['warnings.active']] + s['messages.active'] + [None, None])[1],
                    'host.message_1': (s['messages.active'] + [None])[0],
                    'host.air_c': round(s['drying.indicated_air_temperature_k'] - 273.15, 6),
                    'host.hist_first': records[0]['cycle'] if records else None, 'host.hist_last': records[-1]['cycle'] if records else None,
                    'host.language': _MIELE_LANGUAGES[s['settings.values']['language']],
                    'host.customer_names': [None if e is None else e['name'] for e in s['programmes.customer']]})
        return out


class MielePLW6111Runtime(_MieleMPCon):
    """Trusted independent multi-instance host. Supply sessions to agents."""
    def __init__(self, *, ambient_temperature_K=293.15, scene_adapter=None):
        if not _miele_number(ambient_temperature_K) or not 273.15 <= ambient_temperature_K <= 313.15: raise ValueError('Finite ambient in assumed 273.15..313.15K envelope required')
        self.ambient_temperature_K, self.scene_adapter, self.time_s = float(ambient_temperature_K), scene_adapter, 0.0
        self._instances, self._requests, self._scene_faults = {}, {}, []
        self._principals, self._logs = {'__miele_public__': ('agent', None)}, {'__miele_public__': []}
        self._lock, self._metadata = _miele_threading.RLock(), export_miele_plw6111_metadata(panel=False)
        self._agent = MielePLW6111Session(self, '__miele_public__')
        self._mp_panels, self.panel_capture_dir = {}, None
        self._principals['__miele_panel__'], self._logs['__miele_panel__'] = ('agent', None), []
        self._panel_session = MielePLW6111Session(self, '__miele_panel__')   # the front panel's own path to the commands (not logged)
        self._panel_caller = None                                            # the session whose gesture runs: its commands act as that caller

    def _scene_values(self, state):
        return {sid: _miele_copy.deepcopy(value) for sid, value in state.items() if sid.startswith('basket.') or sid in ('power.enabled', 'door.angle_rad', 'door.closed', 'water.indicated_temperature_k', 'program.phase', 'program.running')}

    def _push_before_commit(self, instance_id, state):
        if self.scene_adapter:
            try: self.scene_adapter(instance_id, self._scene_values(state))
            except Exception: raise RuntimeError('Scene callback failed; runtime unchanged; backend effects unknown') from None

    def create_instance(self, instance_id, asset_type_id=MIELE_PLW6111_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != MIELE_PLW6111_TYPE: raise ValueError('Unique nonempty instance ID and declared Miele asset type required')
            state = {'power.enabled': False, 'thermal.target_k': self.ambient_temperature_K, 'water.temperature_k': None, 'water.indicated_temperature_k': None, 'water.model_configured': False,
                'door.angle_rad': 0.0, 'door.closed': True, 'program.running': False, 'program.phase': 'idle', 'program.phase_elapsed_s': 0.0, 'program.configured': False,
                'program.wash_s': None, 'program.rinse_s': None, 'program.dry_s': None, 'water.heater_energy_j': 0.0, 'water.environment_energy_j': 0.0, 'water.stored_energy_change_j': 0.0}
            state.update({'basket.' + name + '.extension_m': 0.0 for name in MIELE_PLW6111_BASKETS})
            state.update(self._mp_defaults())
            state.update({'drying.air_temperature_k': self.ambient_temperature_K, 'drying.indicated_air_temperature_k': self._indicate(self.ambient_temperature_K)})
            self._push_before_commit(instance_id, state)
            self._instances[instance_id] = {'state': state, 'revision': 0, 'water_configuration': None, 'energy_start_s': self.time_s, 'mp': {'run': None, 'last': None, 'power_epoch': 0}}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'): raise ValueError('Unique host identity and valid visibility required')
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(x, str) for x in instance_ids)): raise ValueError('Scope must be a collection of instance IDs')
            scope = None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope <= self._instances.keys(): raise ValueError('Unknown instance in scope')
            self._principals[principal_id], self._logs[principal_id] = (visibility, scope), []

    def session(self, principal_id):
        if not isinstance(principal_id, str) or principal_id not in self._principals: raise ValueError('Host identity required')
        return MielePLW6111Session(self, principal_id)

    def describe(self, **kwargs): return self._agent.describe(**kwargs)
    def read(self, **kwargs): return self._agent.read(**kwargs)
    def invoke(self, **kwargs): return self._agent.invoke(**kwargs)
    def write(self, **kwargs): return self._agent.write(**kwargs)
    def describe_host(self): return _miele_copy.deepcopy(self._metadata['host_capabilities'])

    def debug_snapshot(self):
        with self._lock: return _miele_copy.deepcopy({'time_s': self.time_s, 'instances': self._instances, 'scene_faults': self._scene_faults})

    def set_user_password(self, instance_id, code):
        """Commissioning (trusted host): the 4-digit USER/ADMIN password the Miele service hands over (p.42)."""
        with self._lock:
            if instance_id not in self._instances or type(code) is not int or not 0 <= code <= 9999: raise ValueError('Known instance and a 4-digit code required')
            self._instances[instance_id]['state']['access.user_password'] = code
            self._instances[instance_id]['revision'] += 1

    def configure_water_model(self, instance_id, *, effective_heat_capacity_J_K, heat_loss_W_K, initial_temperature_K, maximum_temperature_K, evidence):
        with self._lock:
            values = (effective_heat_capacity_J_K, heat_loss_W_K, initial_temperature_K, maximum_temperature_K)
            if not isinstance(instance_id, str) or instance_id not in self._instances or not all(_miele_number(x) for x in values) or not isinstance(evidence, str) or not evidence.strip(): raise ValueError('Known instance, finite explicit model parameters and evidence required')
            if effective_heat_capacity_J_K <= 0 or heat_loss_W_K < 0 or not self.ambient_temperature_K <= initial_temperature_K <= maximum_temperature_K <= 373.15 or maximum_temperature_K <= self.ambient_temperature_K: raise ValueError('Positive C, nonnegative UA and explicit liquid-water temperature envelope required')
            inst = self._instances[instance_id]
            if inst['state']['power.enabled'] or inst['state']['program.running']: raise ValueError('Configure only when powered off and stopped')
            cfg = dict(zip(('effective_heat_capacity_J_K', 'heat_loss_W_K', 'initial_temperature_K', 'maximum_temperature_K'), map(float, values)))
            cfg['evidence'] = evidence
            state = _miele_copy.deepcopy(inst['state'])
            state.update({'water.temperature_k': float(initial_temperature_K), 'water.indicated_temperature_k': self._indicate(initial_temperature_K), 'thermal.target_k': float(initial_temperature_K), 'water.model_configured': True,
                'water.heater_energy_j': 0.0, 'water.environment_energy_j': 0.0, 'water.stored_energy_change_j': 0.0})
            self._push_before_commit(instance_id, state)
            inst.update(state=state, water_configuration=cfg, revision=inst['revision'] + 1, energy_start_s=self.time_s)

    @staticmethod
    def _indicate(temperature_K): return round(temperature_K - 273.15, 1) + 273.15

    def _time_to_target(self, inst):
        state, cfg = inst['state'], inst['water_configuration']
        t, goal, ta = state['water.temperature_k'], state['thermal.target_k'], self.ambient_temperature_K
        if t >= goal: return 0.0
        c, ua, p = cfg['effective_heat_capacity_J_K'], cfg['heat_loss_W_K'], MIELE_PLW6111_WATER_POWER_W
        if ua == 0: return c * (goal - t) / p
        equilibrium = ta + p / ua
        if goal >= equilibrium: return float('inf')
        return c / ua * _miele_math.log((equilibrium - t) / (equilibrium - goal))

    def _water_step(self, inst, dt_s, heating):
        if not dt_s or inst['water_configuration'] is None: return
        s, cfg = inst['state'], inst['water_configuration']
        c, ua, ta, goal = cfg['effective_heat_capacity_J_K'], cfg['heat_loss_W_K'], self.ambient_temperature_K, s['thermal.target_k']
        remaining, energy = dt_s, 0.0
        t = s['water.temperature_k']
        # Exact constant-power/loss intervals with event splitting at the setpoint.
        while remaining > 1e-12:
            if heating and t < goal:
                p = MIELE_PLW6111_WATER_POWER_W
                tau = self._time_to_target({**inst, 'state': {**s, 'water.temperature_k': t}})
            elif heating and t == goal:
                p, tau = min(MIELE_PLW6111_WATER_POWER_W, ua * (goal - ta)), float('inf')
            else:
                p, tau = 0.0, float('inf')
                if heating and t > goal and goal > ta and ua > 0: tau = c / ua * _miele_math.log((t - ta) / (goal - ta))
            duration = min(remaining, tau)
            new_t = t + p * duration / c if ua == 0 else ta + (t - ta) * _miele_math.exp(-ua * duration / c) + p / ua * (-_miele_math.expm1(-ua * duration / c))
            if tau <= remaining: new_t = goal
            energy += p * duration
            remaining -= duration
            t = new_t
            if duration == 0: break
        change = c * (t - s['water.temperature_k'])
        s['water.temperature_k'], s['water.indicated_temperature_k'] = t, self._indicate(t)
        s['water.heater_energy_j'] += energy
        s['water.environment_energy_j'] += energy - change
        s['water.stored_energy_change_j'] = c * (t - cfg['initial_temperature_K'])

    def _advance(self, inst, dt_s):
        if inst['mp']['run'] is not None and inst['state']['program.running']:
            return self._mp_advance(inst, dt_s, self.time_s)          # a programme started on the panel (MP.CON 3 block table)
        self._mp_air(inst, dt_s, None)                                 # no panel programme: the drying duct settles (legacy dry phase is a timer)
        s, remaining = inst['state'], dt_s
        while remaining > 1e-10:
            if not s['program.running']:
                self._water_step(inst, remaining, False)
                break
            phase = s['program.phase']
            heating = phase in ('wash', 'rinse') and s['power.enabled']
            if heating:
                wait = self._time_to_target(inst)
                if wait > 0:
                    advance = min(remaining, wait)
                    self._water_step(inst, advance, True)
                    remaining -= advance
                    if wait > advance: break
                    # Exact event snap prevents floating-point drift blocking a timer.
                    s['water.temperature_k'] = s['thermal.target_k']
                    s['water.indicated_temperature_k'] = self._indicate(s['thermal.target_k'])
            left = s['program.' + phase + '_s'] - s['program.phase_elapsed_s']
            advance = min(remaining, max(0.0, left))
            self._water_step(inst, advance, heating)
            s['program.phase_elapsed_s'] += advance
            remaining -= advance
            if left - advance <= 1e-9:
                if phase == 'dry': s.update({'program.running': False, 'program.phase': 'completed', 'program.phase_elapsed_s': s['program.dry_s']})
                elif phase == 'rinse' and s['program.dry_s'] == 0: s.update({'program.running': False, 'program.phase': 'completed', 'program.phase_elapsed_s': 0.0})
                else: s.update({'program.phase': 'rinse' if phase == 'wash' else 'dry', 'program.phase_elapsed_s': 0.0})
            else: break

    def step(self, dt_s):
        if not _miele_number(dt_s) or not 0 <= dt_s <= 86400: raise ValueError('dt_s must be finite in [0,86400]')
        if not dt_s: return
        with self._lock:
            candidates = _miele_copy.deepcopy(self._instances)
            for inst in candidates.values():
                before = _miele_copy.deepcopy(inst['state'])
                self._advance(inst, float(dt_s))
                self._mp_timers(inst, self.time_s + float(dt_s))
                if any(isinstance(value, float) and not _miele_math.isfinite(value) for value in inst['state'].values()): raise ValueError('Nonfinite model result; world step unchanged')
                if before != inst['state']: inst['revision'] += 1
            self._instances, self.time_s = candidates, self.time_s + float(dt_s)
            faults = []
            if self.scene_adapter:
                for key, inst in self._instances.items():
                    try: self.scene_adapter(key, self._scene_values(inst['state']))
                    except Exception: faults.append({'instance_id': key, 'time_s': self.time_s, 'code': 'SCENE_SYNCHRONIZATION_FAILED'})
            self._scene_faults.extend(faults)
            if faults: raise SceneSynchronizationError('World process and time advanced; scene synchronization failed; backend effects may be partial') from None

    def sync_scene(self, instance_ids=None):
        with self._lock:
            if self.scene_adapter is None: raise RuntimeError('Scene adapter required')
            ids = list(self._instances) if instance_ids is None else list(instance_ids)
            if any(not isinstance(key, str) or key not in self._instances for key in ids): raise ValueError('Unknown instance')
            for key in ids:
                try: self.scene_adapter(key, self._scene_values(self._instances[key]['state']))
                except Exception: raise SceneSynchronizationError('Scene synchronization failed; backend effects unknown') from None


class MielePLW6111Session:
    """Public adapter with host-authenticated, immutable identity and scope."""
    def __init__(self, host, principal_id):
        if principal_id not in host._principals: raise ValueError('Host identity required')
        self.__host, self.__principal = host, principal_id

    def _instance(self, instance_id):
        if not isinstance(instance_id, str): return None
        scope = self.__host._principals[self.__principal][1]
        return self.__host._instances.get(instance_id) if scope is None or instance_id in scope else None

    def _visible(self, slot): return slot['visibility'] == 'agent' or self.__host._principals[self.__principal][0] in ('debug', 'evaluator')

    def _envelope(self, instance_id, request_id=None, *, result=None, observations=None, status='succeeded', error=None):
        inst = self._instance(instance_id)
        return {'protocol_version': PROTOCOL_VERSION, 'instance_id': instance_id if isinstance(instance_id, str) else None, 'request_id': request_id if isinstance(request_id, str) else None,
            'operation_id': None, 'status': status, 'state_revision': inst['revision'] if inst else None, 'clock': {'domain': 'simulation', 'time_s': self.__host.time_s},
            'result': {} if result is None else _miele_copy.deepcopy(result), 'observations': {} if observations is None else observations, 'error': error}

    def _error(self, instance_id, code, request_id=None, status='rejected'):
        messages = {'UNKNOWN_INSTANCE': 'Instance is unavailable in this session.', 'UNKNOWN_CAPABILITY': 'Capability is unavailable.', 'INVALID_ARGUMENT': 'Arguments do not match declared input.', 'ACCESS_DENIED': 'Requested state is outside session access.', 'PRECONDITION_FAILED': 'Required operation state or configuration is unavailable.', 'BUSY': 'Stop the active program before this command.', 'REQUEST_CONFLICT': 'Request ID already belongs to different arguments.', 'UNSUPPORTED': 'Required execution backend is unavailable.', 'DEVICE_ERROR': 'Scene callback failed; runtime unchanged; backend effects unknown.'}
        return self._envelope(instance_id, request_id, status=status, error={'code': code, 'message': messages[code], 'retryable': False, 'side_effects': 'unknown' if code == 'DEVICE_ERROR' else 'none', 'progress': None})

    def describe(self, *, instance_id=None, asset_type_id=None):
        with self.__host._lock:
            if (instance_id is None) == (asset_type_id is None): return self._error(instance_id, 'INVALID_ARGUMENT')
            if instance_id is not None and not self._instance(instance_id): return self._error(instance_id, 'UNKNOWN_INSTANCE')
            if asset_type_id is not None and asset_type_id != MIELE_PLW6111_TYPE: return self._error(instance_id, 'UNKNOWN_INSTANCE')
            scope = self.__host._principals[self.__principal][1]
            if scope is not None and not scope: return self._error(instance_id, 'ACCESS_DENIED')
            spec = export_miele_plw6111_metadata()['asset_types'][MIELE_PLW6111_TYPE]
            caps = _miele_copy.deepcopy(spec['capabilities'])
            for group in ('controller_capabilities', 'evaluator_capabilities', 'panel_capabilities'):
                caps.update({k: v for k, v in spec[group].items() if self._visible(v)})
            for cap in caps.values():
                cap['state_effects'] = [effect for effect in cap['state_effects'] if self._visible(spec['state_slots'][effect['state_id']])]
                cap['observes'] = [sid for sid in cap['observes'] if self._visible(spec['state_slots'][sid])]
                if cap['scene_backend_required'] and self.__host.scene_adapter is None: cap['availability'] = 'unsupported'
            return {'protocol_version': PROTOCOL_VERSION, 'instance_id': instance_id, 'asset_type_id': MIELE_PLW6111_TYPE, 'asset_version': spec['asset_version'], 'adapter': 'MielePLW6111Session', 'capabilities': caps,
                'state_slots': _miele_copy.deepcopy({sid: slot for sid, slot in spec['state_slots'].items() if self._visible(slot)}), 'limitations': _miele_copy.deepcopy(self.__host._metadata['limitations'])}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, 'UNKNOWN_INSTANCE')
            slots = self.__host._metadata['asset_types'][MIELE_PLW6111_TYPE]['state_slots']
            if state_ids is None: state_ids = [sid for sid, slot in slots.items() if self._visible(slot)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids) or len(set(state_ids)) != len(state_ids): return self._error(instance_id, 'INVALID_ARGUMENT')
            if any(sid not in slots or not self._visible(slots[sid]) for sid in state_ids): return self._error(instance_id, 'ACCESS_DENIED')
            observations = {}
            for sid in state_ids:
                slot, value = slots[sid], self.__host._mp_observed(inst, sid)
                observations[sid] = {'value': value, 'unit': slot['unit'], 'quality': 'unavailable' if value is None else 'estimated' if slot['origin'] == 'derived' else 'valid', 'sample_time_s': self.__host.time_s,
                    'sample_window_s': [inst['energy_start_s'], self.__host.time_s] if slot['sampling']['mode'] == 'integral' else None}
                if value is None: observations[sid]['reason'] = 'Required explicit simulation configuration is unavailable.'
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id or len(request_id) > 256: return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            try: digest = _miele_hashlib.sha256(_miele_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError): return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            key = (self.__principal, instance_id, request_id)
            if key in host._requests:
                previous, response = host._requests[key]
                return _miele_copy.deepcopy(response) if previous == digest else self._error(instance_id, 'REQUEST_CONFLICT', request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, _miele_copy.deepcopy(response))
            host._logs[self.__principal].append({'instance_id': instance_id, 'capability_id': capability_id, 'request_id': request_id, 'response': _miele_copy.deepcopy(response)})
            return response

    def _checked(self, instance_id, inst, capability, args, request_id):
        """Run ownership (lead rule 2026-10-19): the caller that starts a programme (panel or legacy washing.start) owns it; another caller
        cannot interrupt, resume, cancel or stop it while it is active.  The power switch is a hard switch (physical action, lead clarification
        2026-10-19): power.set follows it and is never refused; the machine reacts as to a power failure (power fail, the phase is repeated)."""
        s = inst['state']
        active = s['program.running'] or (inst['mp']['run'] is not None and s['programme.status'] in ('running', 'interrupted', 'power_fail'))
        guarded = capability in ('programme.interrupt', 'programme.resume', 'programme.cancel', 'washing.stop')
        if guarded and _g02_foreign_run(self.__host, self.__principal, inst.get('run_owner'), active):
            return self._error(instance_id, 'ACCESS_DENIED', request_id)
        response = self._checked_body(instance_id, inst, capability, args, request_id)
        if capability in ('programme.start', 'washing.start') and response['status'] == 'succeeded' and not active:
            self.__host._instances[instance_id]['run_owner'] = self.__principal     # (the committed instance: commands work on candidates)
        return response

    def _checked_body(self, instance_id, inst, capability, args, request_id):
        host, state = self.__host, inst['state']
        fail = lambda code, status='rejected': self._error(instance_id, code, request_id, status)
        if isinstance(capability, str) and capability.startswith('panel.'):
            return self._panel_command(instance_id, capability, args, request_id)
        meta = host._metadata['asset_types'][MIELE_PLW6111_TYPE]
        if capability in meta['controller_capabilities'] or capability in meta['evaluator_capabilities']:
            return self._mp_command(instance_id, inst, capability, args, request_id)
        caps = meta['capabilities']
        if capability not in caps: return fail('UNKNOWN_CAPABILITY')
        schema = caps[capability]['input_schema']['properties']
        if set(args) != set(schema): return fail('INVALID_ARGUMENT')
        for name, item in schema.items():
            value = args[name]
            if item['type'] == 'boolean' and type(value) is not bool: return fail('INVALID_ARGUMENT')
            if item['type'] == 'number' and (not _miele_number(value) or not item.get('minimum', -float('inf')) <= value <= item.get('maximum', float('inf'))): return fail('INVALID_ARGUMENT')
            if item['type'] == 'string' and (not isinstance(value, str) or value not in item['enum']): return fail('INVALID_ARGUMENT')
        if caps[capability]['scene_backend_required'] and host.scene_adapter is None: return fail('UNSUPPORTED', 'unsupported')
        if capability not in ('power.set', 'washing.stop') and (state['program.running'] or state['programme.status'] in _MIELE_BUSY): return fail('BUSY')
        mp, post = inst['mp'], []
        cand = None                                   # a candidate (state, controller) for the panel-programme cases
        if capability in ('thermal.set_temperature', 'washing.configure_program', 'washing.start') and not state['power.enabled']: return fail('PRECONDITION_FAILED')
        if capability == 'power.set':
            updates, result = {'power.enabled': args['enabled']}, {'enabled': args['enabled']}
            if not args['enabled'] and state['program.running']: updates.update({'program.running': False, 'program.phase': 'stopped'})
            if args['enabled'] != state['power.enabled']:
                post.append(lambda: inst['mp'].update(power_epoch=inst['mp']['power_epoch'] + 1))     # the controller restarts (panel shows its start screen)
                if not args['enabled']:
                    updates.update({'documentation.printing': None, 'buzzer.state': None})
                    if mp['run'] is not None and state['programme.status'] == 'running': updates['programme.status'] = 'power_fail'
                elif mp['run'] is not None and state['programme.status'] == 'power_fail':
                    cand = dict(inst, state=_miele_copy.deepcopy(state), mp=_miele_copy.deepcopy(mp))     # automatic restart: the current block is repeated (p.41)
                    cand['state'].update({'power.enabled': True, 'program.running': True, 'programme.status': 'running',
                                          'warnings.active': cand['state']['warnings.active'] + [{'code': 1, 'text': 'power fail'}]})
                    host._mp_enter(cand, cand['mp']['run']['index'])
                    updates = {k: v for k, v in cand['state'].items() if v != state[k]}
        elif capability == 'thermal.set_temperature':
            cfg = inst['water_configuration']
            if args['temperature_K'] < host.ambient_temperature_K or (cfg and args['temperature_K'] > cfg['maximum_temperature_K']): return fail('PRECONDITION_FAILED')
            updates, result = {'thermal.target_k': float(args['temperature_K'])}, {'setpoint_K': float(args['temperature_K'])}
        elif capability in ('door.open', 'door.close'):
            closed = capability == 'door.close'
            if closed and any(state['basket.' + name + '.extension_m'] != 0 for name in MIELE_PLW6111_BASKETS): return fail('PRECONDITION_FAILED')
            updates = {'door.angle_rad': 0.0 if closed else _miele_math.pi / 2, 'door.closed': closed}
            if not closed and state['programme.status'] in ('end', 'cancelled'): updates['programme.status'] = 'ready'     # opening the door clears the end window
            result = {'angle_rad': updates['door.angle_rad'], 'closed': closed}
        elif capability == 'basket.set_position':
            if state['door.angle_rad'] != _miele_math.pi / 2: return fail('PRECONDITION_FAILED')
            updates, result = {'basket.' + args['slot'] + '.extension_m': float(args['extension_m'])}, {'slot': args['slot'], 'extension_m': float(args['extension_m'])}
        elif capability == 'washing.configure_program':
            updates = {'program.configured': True, 'program.phase': 'idle', 'program.phase_elapsed_s': 0.0, **{'program.' + name: float(value) for name, value in args.items()}}
            result = {'configured': True}
        elif capability == 'washing.start':
            if not state['door.closed'] or not state['water.model_configured'] or not state['program.configured'] or any(state['basket.' + name + '.extension_m'] != 0 for name in MIELE_PLW6111_BASKETS): return fail('PRECONDITION_FAILED')
            updates, result = {'program.running': True, 'program.phase': 'wash', 'program.phase_elapsed_s': 0.0}, {'running': True}
            if state['programme.status'] in ('end', 'cancelled'): updates['programme.status'] = 'ready'
        else:
            updates, result = {'program.running': False, 'program.phase': 'stopped'}, {'running': False}
            if mp['run'] is not None and state['programme.status'] in ('running', 'interrupted', 'power_fail'):
                cand = dict(inst, state=_miele_copy.deepcopy(state), mp=_miele_copy.deepcopy(mp))          # a panel programme: cancelled (as STOP twice)
                host._mp_end_run(cand, 'cancelled', 'NO DISINFECTION', host.time_s)
                updates = {k: v for k, v in cand['state'].items() if v != state[k]}
        changed = {sid: value for sid, value in updates.items() if value != state[sid]}
        if changed:
            candidate = {**state, **changed}
            try: host._push_before_commit(instance_id, candidate)
            except RuntimeError: return fail('DEVICE_ERROR', 'failed')
            state.update(changed)
            inst['revision'] += 1
        if cand is not None: inst['mp'] = cand['mp']
        for action in post: action()
        return self._envelope(instance_id, request_id, result=result)

    def _panel_command(self, instance_id, capability, args, request_id):
        """panel.* commands: a gesture on the instance's front panel (engine + panel_ui.json), a screen capture, or the evaluator frame."""
        host, panel = self.__host, _miele_panel()
        if capability not in panel['caps']:
            return self._error(instance_id, 'UNKNOWN_CAPABILITY', request_id)
        if not self._visible(panel['caps'][capability]):
            return self._error(instance_id, 'ACCESS_DENIED', request_id)
        host._panel_caller = self                                            # a gesture acts as the session that makes it (access rules)
        try:
            result, error = _g02_panel_command(panel, host.panel(instance_id), capability, args, instance_id=instance_id, time_s=host.time_s,
                                               capture_dir=host.panel_capture_dir)
        finally:
            host._panel_caller = None
        return self._error(instance_id, error, request_id) if error else self._envelope(instance_id, request_id, result=result)

    def _mp_command(self, instance_id, inst, capability, args, request_id):
        """The MP.CON 3 functions: schema and state checks on a candidate copy, then one committed update (scene callback first)."""
        host = self.__host
        fail = lambda code, status='rejected': self._error(instance_id, code, request_id, status)   # noqa: E731
        meta = host._metadata['asset_types'][MIELE_PLW6111_TYPE]
        info = meta['controller_capabilities'].get(capability) or meta['evaluator_capabilities'][capability]
        if not self._visible(info):
            return fail('ACCESS_DENIED')
        if not isinstance(args, dict) or _g02_engine().check_arguments(info['input_schema'], args) or (info['input_schema'].get('minProperties') and not args):
            return fail('INVALID_ARGUMENT')
        state = inst['state']
        if not state['power.enabled']:
            return fail('PRECONDITION_FAILED')
        cand = dict(inst, state=_miele_copy.deepcopy(state), mp=_miele_copy.deepcopy(inst['mp']))
        s, mp, now = cand['state'], cand['mp'], host.time_s
        status = s['programme.status']
        busy = s['program.running'] or status in _MIELE_BUSY                 # programme selection and start only in standby
        active_run = s['program.running'] or status in ('running', 'interrupted', 'power_fail')   # the menu (and its settings) stays open in malfunction
        result = _miele_copy.deepcopy(args)
        if capability == 'programme.select':
            pid = args['programme_id']
            entry = host._mp_entry(s, pid)
            if busy: return fail('BUSY')
            if entry is None or not entry['active']: return fail('PRECONDITION_FAILED')
            s.update({'programme.selected': pid, 'programme.drying': pid == 'drying' or host._mp_has_drying(s, pid)})
            if status in ('end', 'cancelled'): s['programme.status'] = 'ready'
        elif capability == 'programme.set_drying':
            if busy: return fail('BUSY')
            if not host._mp_has_drying(s, s['programme.selected']): return fail('PRECONDITION_FAILED')
            s['programme.drying'] = args['enabled']
        elif capability == 'programme.start':
            if busy or s['documentation.printing'] is not None: return fail('BUSY')       # ON PRINT (p.47)
            entry = host._mp_entry(s, s['programme.selected'])
            if entry is None or not entry['active'] or not s['door.closed'] or any(s['basket.' + n + '.extension_m'] != 0 for n in MIELE_PLW6111_BASKETS):
                return fail('PRECONDITION_FAILED')
            blocks = host._mp_run_blocks(s)
            cfg = inst['water_configuration']
            wet = [b for b in blocks if b['block'] not in ('drying', 'cooling', 'drain')]
            if not blocks or (wet and cfg is None): return fail('PRECONDITION_FAILED')
            if cfg is not None and any(b['temperature_C'] is not None and b['temperature_C'] + 273.15 > cfg['maximum_temperature_K'] for b in wet):
                return fail('PRECONDITION_FAILED')
            cycle = s['programme.cycle_counter'] + 1
            mp['run'] = {'programme': s['programme.selected'], 'name': entry['name'], 'blocks': blocks, 'index': 0, 'cycle': cycle,
                         'start': host._mp_clock(cand, now).strftime('%d.%m.%y %H:%M')}
            mp['last'] = None
            s.update({'programme.cycle_counter': cycle, 'programme.status': 'running', 'program.running': True, 'warnings.active': [], 'faults.active': None,
                      'buzzer.state': None, 'programme.a0': 0.0, 'programme.max_temperature_k': s['water.indicated_temperature_k']})
            host._mp_enter(cand, 0)
            result = {'running': True, 'cycle': cycle}
        elif capability == 'programme.interrupt':
            if mp['run'] is None or status != 'running': return fail('PRECONDITION_FAILED')
            s.update({'program.running': False, 'program.phase': 'stopped', 'programme.status': 'interrupted'})
            result = {'status': 'interrupted'}
        elif capability == 'programme.resume':
            if mp['run'] is None or status != 'interrupted': return fail('PRECONDITION_FAILED')
            s.update({'program.running': True, 'programme.status': 'running'})
            host._mp_enter(cand, mp['run']['index'])                         # the current wash phase is repeated (p.29)
            result = {'status': 'running'}
        elif capability == 'programme.cancel':
            if mp['run'] is None or status not in ('running', 'interrupted', 'power_fail'): return fail('PRECONDITION_FAILED')
            host._mp_end_run(cand, 'cancelled', 'NO DISINFECTION', now)
            result = {'status': 'cancelled'}
        elif capability == 'faults.reset':
            if status != 'malfunction': return fail('PRECONDITION_FAILED')
            s.update({'programme.status': 'ready', 'faults.active': None, 'buzzer.state': None})
            result = {'status': 'ready'}
        elif capability in ('fault.inject', 'fault.clear'):
            text = _MIELE_MESSAGES[args['code']]
            if capability == 'fault.clear' and text not in s['messages.active']: return fail('PRECONDITION_FAILED')
            s['messages.active'] = [m for m in s['messages.active'] if m != text] + ([text] if capability == 'fault.inject' else [])
            result = {'code': args['code']}
        elif capability == 'diagnostics.inject_fault':
            code = args['code']
            if code not in _MIELE_FAULTS: return fail('INVALID_ARGUMENT')
            if status == 'malfunction': return fail('PRECONDITION_FAILED')
            if mp['run'] is not None and status in ('running', 'interrupted', 'power_fail'):
                host._mp_end_run(cand, 'malfunction', f'FAULT {code}', now)
            elif s['program.running']:
                s.update({'program.running': False, 'program.phase': 'stopped'})
            volume = s['settings.values']['buzzer_alarm']
            s.update({'programme.status': 'malfunction', 'faults.active': {'code': code, 'text': _MIELE_FAULTS[code]},
                      'buzzer.state': {'tone': 'alarm', 'volume': volume, 'until_s': None} if volume else None})
        elif capability in ('programmes.copy', 'programmes.set_block', 'programmes.delete', 'programmes.set_active'):
            if active_run: return fail('BUSY')
            if capability == 'programmes.copy':
                pos = args['position']
                blocks = {b['block']: b for b in _MIELE_STANDARD[args['source']][2]}
                s['programmes.customer'][pos - 1] = {
                    'position': pos, 'name': f'CUSTOMER {pos:02d}', 'base': args['source'], 'active': True,
                    'blocks': {bid: ({'active': True, 'temperature_C': blocks[bid]['temperature_C'] or 0, 'time_min': int(blocks[bid]['hold_s'] // 60),
                                      'water': blocks[bid]['water'], 'dosing': blocks[bid]['dosing']} if bid in blocks else
                                     {'active': False, 'temperature_C': 0, 'time_min': 1, 'water': None, 'dosing': None}) for bid in _MIELE_BLOCKS}}
                result = {'programme_id': _MIELE_CUSTOMER_IDS[pos - 1]}
            elif capability == 'programmes.set_block':
                entry = s['programmes.customer'][args['position'] - 1]
                if args['temperature_C'] > (_MIELE_DRY_MAX_C if args['block'] == 'drying' else _MIELE_WASH_MAX_C): return fail('INVALID_ARGUMENT')
                if entry is None: return fail('PRECONDITION_FAILED')
                entry['blocks'][args['block']].update(active=args['active'], temperature_C=args['temperature_C'], time_min=args['time_min'])
                result = {'programme_id': _MIELE_CUSTOMER_IDS[args['position'] - 1]}
            elif capability == 'programmes.delete':
                pid = _MIELE_CUSTOMER_IDS[args['position'] - 1]
                if s['programmes.customer'][args['position'] - 1] is None: return fail('PRECONDITION_FAILED')
                s['programmes.customer'][args['position'] - 1] = None
                result = {'programme_id': pid}
            else:
                pid = args['programme_id']
                if host._mp_entry(s, pid) is None: return fail('PRECONDITION_FAILED')
                if pid in _MIELE_STANDARD: s['programmes.active'][pid] = args['active']
                else: s['programmes.customer'][int(pid[1:]) - 1]['active'] = args['active']
                if not host._mp_order(s): return fail('PRECONDITION_FAILED')    # at least one programme stays active
                result = {'programme_id': pid, 'active': args['active']}
            if s['programme.selected'] not in host._mp_order(s):              # the selected programme was deleted or deactivated
                first = host._mp_order(s)[0]
                s.update({'programme.selected': first, 'programme.drying': first == 'drying' or host._mp_has_drying(s, first)})
            elif capability != 'programmes.set_active' and not host._mp_has_drying(s, s['programme.selected']) and s['programme.selected'] != 'drying':
                s['programme.drying'] = False
        elif capability == 'settings.configure':
            name = args.get('user_name')
            if name is not None and (len(name) > 16 or any(ch not in _MIELE_NAME_CHARS for ch in name)): return fail('INVALID_ARGUMENT')
            s['settings.values'] = dict(s['settings.values'], **{k: (v.rstrip() if k == 'user_name' else int(v)) for k, v in args.items()})
            result = {k: s['settings.values'][k] for k in args}
        elif capability in ('clock.set_date', 'clock.set_time'):
            t = host._mp_clock(cand, now)
            try:
                new = t.replace(year=args['year'], month=args['month'], day=args['day']) if capability == 'clock.set_date' else t.replace(hour=args['hour'], minute=args['minute'], second=0, microsecond=0)
            except ValueError:
                return fail('INVALID_ARGUMENT')
            s['clock.offset_s'] = s['clock.offset_s'] + (new - t).total_seconds()
            result = {'date': new.strftime('%Y-%m-%d')} if capability == 'clock.set_date' else {'time': new.strftime('%H:%M')}
        elif capability == 'history.print':
            if active_run or s['documentation.printing'] is not None: return fail('BUSY')
            if not any(r['cycle'] == args['cycle'] for r in s['history.records']): return fail('PRECONDITION_FAILED')
            s['documentation.printing'] = {'cycle': args['cycle'], 'format': 'table' if s['settings.values']['printout'] == 2 else 'graphic', 'until_s': now + _MIELE_PRINT_S, 'source': 'menu'}
        else:                                                                 # history.cancel_print
            if s['documentation.printing'] is None: return fail('PRECONDITION_FAILED')
            s['documentation.printing'] = None
            result = {'cancelled': True}
        if s != state or mp != inst['mp']:
            if s != state:
                try: host._push_before_commit(instance_id, s)
                except RuntimeError: return fail('DEVICE_ERROR', 'failed')
            inst['state'], inst['mp'] = s, mp
            inst['revision'] += 1
        return self._envelope(instance_id, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if not self._instance(instance_id): return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            slots = self.__host._metadata['asset_types'][MIELE_PLW6111_TYPE]['state_slots']
            slot = slots.get(state_id) if isinstance(state_id, str) else None
            if slot is None or not self._visible(slot) or slot['access'] != 'read_write': return self._error(instance_id, 'ACCESS_DENIED', request_id)
            route = slot['write_via']
            return self.invoke(instance_id=instance_id, capability_id=route['capability_id'], arguments={**route.get('fixed_arguments', {}), route['parameter']: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock: return _miele_copy.deepcopy(self.__host._logs[self.__principal])
# END CHEM-191 Miele PLW 6111 runtime.

# BEGIN CHEM-047 Anton Paar Multiwave 7101 runtime (append-only batch scope).
MULTIWAVE_7101_TYPE = 'instrument.multiwave_7101'
MULTIWAVE_7101_POWER_W = 1500.0
MULTIWAVE_7101_MAX_K = 573.15
MULTIWAVE_7101_AMBIENT_K = 298.15
MULTIWAVE_7101_REFERENCE_K = 448.15
MULTIWAVE_7101_REFERENCE_S = 330.0
MULTIWAVE_7101_REFERENCE_POWER_W = 1000.0  # Assumed absorbed power, not a measured value.
MULTIWAVE_7101_LOSS_W_K = 1.0  # Assumed UA; one time point cannot identify both C and UA.
MULTIWAVE_7101_CAPACITY_J_K = -MULTIWAVE_7101_LOSS_W_K * MULTIWAVE_7101_REFERENCE_S / math.log1p(
    -MULTIWAVE_7101_LOSS_W_K * (MULTIWAVE_7101_REFERENCE_K - MULTIWAVE_7101_AMBIENT_K) / MULTIWAVE_7101_REFERENCE_POWER_W)
MULTIWAVE_7101_OPEN_LOSS_W_K = 5.0  # Assumed extra passive loss, not factory active cooling.
MULTIWAVE_7101_SOURCE = 'https://www.anton-paar.com/?L=1&document=60434&eID=documentsDownload'
# 2026-10 template hook (scripts/basic40_templates/multiwave_7101.py): template.read, digestion.load_template,
# digestion.evaluate; library assets/reactions/multiwave_7101_digestion_library/templates.json. Additive only.
_MULTIWAVE_TEMPLATE_CAPS = ('template.read', 'digestion.load_template', 'digestion.evaluate')


def _multiwave_template_module():
    try:
        from basic40_templates import multiwave_7101 as module
    except ImportError:
        from scripts.basic40_templates import multiwave_7101 as module
    return module


def _multiwave_object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _multiwave_number_schema(unit, minimum=None, maximum=None):
    spec = {'type': 'number', 'x-unit': unit}
    if minimum is not None: spec['minimum'] = minimum
    if maximum is not None: spec['maximum'] = maximum
    return spec


def export_multiwave_7101_metadata(panel=True):
    """Public type metadata. No instance state or private truth is exported.  The touchscreen functions behind the front panel are added in
    separate groups (controller_capabilities, panel_capabilities); panel=False skips loading panel_ui.json."""
    def slot(unit, origin, schema, *, visibility='agent', route=None):
        return {'unit': unit, 'origin': origin, 'visibility': visibility,
            'access': 'read_write' if route else 'read_only', 'write_via': route,
            'value_schema': schema, 'reference_conditions': None,
            'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None}}
    def route(cap, parameter): return {'capability_id': cap, 'parameter': parameter}
    boolean = {'type': 'boolean'}
    occupancy = {'type': 'array', 'minItems': 24, 'maxItems': 24, 'items': boolean}
    slots = {
        'power.enabled': slot('1', 'setpoint', boolean, route=route('power.set', 'enabled')),
        'thermal.target_k': slot('K', 'setpoint', _multiwave_number_schema('K', 273.15, MULTIWAVE_7101_MAX_K), route=route('thermal.set_temperature', 'temperature_K')),
        'thermal.temperature_k': slot('K', 'process_truth', _multiwave_number_schema('K'), visibility='debug'),
        'thermal.indicated_temperature_k': slot('K', 'derived', _multiwave_number_schema('K')),
        'microwave.enabled': slot('1', 'setpoint', boolean, route=route('microwave.set_enabled', 'enabled')),
        'microwave.power_limit_w': slot('W', 'setpoint', _multiwave_number_schema('W', 0, 1500), route=route('microwave.set_power', 'power_W')),
        'microwave.delivered_power_w': slot('W', 'derived', _multiwave_number_schema('W', 0, 1500)),
        'door.angle_rad': slot('rad', 'scene', _multiwave_number_schema('rad', 0, math.pi / 2)),
        'door.closed': slot('1', 'derived', boolean),
        'rack.occupied_slots': slot('1', 'scene', occupancy),
        'rack.slot_count': slot('1', 'derived', {'type': 'integer', 'const': 24, 'x-unit': '1'}),
        'rack.slot_capacity_m3': slot('m^3', 'derived', _multiwave_number_schema('m^3', 8e-6, 8e-6)),
        'rack.pressure_limit_pa': slot('Pa', 'derived', {'type': 'null'}),
    }
    for sid in ('thermal.absorbed_energy_j', 'thermal.environment_energy_j', 'thermal.stored_energy_change_j'):
        slots[sid] = slot('J', 'process_truth', _multiwave_number_schema('J'), visibility='debug')
    for sid, info in slots.items():
        info['read_binding'] = f"Multiwave7101Session.read(instance_id=..., state_ids=['{sid}'])"
        if info['origin'] == 'derived': info['observation_note_zh'] = '模拟派生或目录常量；温度与功率显示标记estimated，不是实测传感器。'
    n = _multiwave_number_schema
    defs = {
        'power.set': ({'enabled': boolean}, {'enabled': boolean}, {'enabled': True}, ['housing', 'controller'], ['power.enabled', 'microwave.enabled', 'microwave.delivered_power_w'], '设置模拟供电，断电保留已发生的热状态。'),
        'thermal.set_temperature': ({'temperature_K': n('K', 273.15, 573.15)}, {'setpoint_K': n('K')}, {'temperature_K': 448.15}, ['controller', 'cavity'], ['thermal.target_k'], '只写目标；温度及热量仅由宿主step推进。'),
        'microwave.set_enabled': ({'enabled': boolean}, {'enabled': boolean}, {'enabled': True}, ['microwave_source', 'cavity'], ['microwave.enabled', 'microwave.delivered_power_w'], '设置理想模拟加热许可；开启需要上电及闭门，不代表已验证厂家互锁。'),
        'microwave.set_power': ({'power_W': n('W', 0, 1500)}, {'power_W': n('W', 0, 1500)}, {'power_W': 1000.0}, ['microwave_source', 'controller'], ['microwave.power_limit_w', 'microwave.delivered_power_w'], '设置理想连续可传递功率上限；额定上限1500 W，不是实际固件接口。'),
        'door.open': ({}, {'angle_rad': n('rad'), 'closed': boolean}, {}, ['door'], ['door.angle_rad', 'door.closed', 'microwave.enabled', 'microwave.delivered_power_w'], '顶门绕场景负X轴上翻90°并撤销模拟加热许可；角度为几何假设。'),
        'door.close': ({}, {'angle_rad': n('rad'), 'closed': boolean}, {}, ['door'], ['door.angle_rad', 'door.closed'], '关闭顶门；关闭后不自动恢复加热许可。'),
        'rack.set_slot': ({'slot_index': {'type': 'integer', 'minimum': 0, 'maximum': 23, 'x-unit': '1'}, 'occupied': boolean}, {'slot_index': {'type': 'integer', 'minimum': 0, 'maximum': 23, 'x-unit': '1'}, 'occupied': boolean}, {'slot_index': 0, 'occupied': True}, ['rack'], ['rack.occupied_slots'], '仅配置24槽场景占位；没有样品质量、加液、物质转移或酸消解。'),
    }
    caps = {}
    for cap, (args, output, example, components, immediate, meaning) in defs.items():
        pre = []
        if cap in ('thermal.set_temperature', 'microwave.set_power'):
            pre.append({'description_zh': '模拟供电开启。', 'state_ids': ['power.enabled'], 'enforced_by': 'adapter', 'check': 'power.enabled'})
        if cap == 'thermal.set_temperature':
            pre.append({'description_zh': '设定值不低于宿主环境温度。', 'state_ids': [], 'enforced_by': 'adapter', 'check': 'ambient_temperature_K <= temperature_K <= 573.15'})
        if cap == 'microwave.set_enabled':
            pre.append({'description_zh': '开启需要供电及闭门；仅模拟前提。', 'state_ids': ['power.enabled', 'door.closed'], 'enforced_by': 'adapter', 'check': 'not enabled or (power.enabled and door.closed)'})
        scene_required = cap.startswith('door.') or cap == 'rack.set_slot'
        if scene_required:
            pre.append({'description_zh': '已注册同步场景回调。', 'state_ids': [], 'enforced_by': 'adapter', 'check': 'scene_adapter is not None'})
        if cap == 'rack.set_slot':
            pre.append({'description_zh': '门打开且加热关闭；只修改可视占位。', 'state_ids': ['door.closed', 'microwave.enabled'], 'enforced_by': 'adapter', 'check': 'not door.closed and not microwave.enabled'})
        effects = [{'state_id': sid, 'timing': 'immediate', 'description_zh': meaning} for sid in immediate]
        if cap != 'rack.set_slot':
            effects += [{'state_id': sid, 'timing': 'on_step', 'description_zh': '有限功率单节点热响应和累计能量；只由宿主推进。'} for sid in ('thermal.temperature_k', 'thermal.indicated_temperature_k', 'microwave.delivered_power_w', 'thermal.absorbed_energy_j', 'thermal.environment_energy_j', 'thermal.stored_energy_change_j')]
        caps[cap] = {'availability': 'implemented', 'visibility': 'agent', 'kind': 'configuration' if cap == 'rack.set_slot' else 'device_command',
            'description_zh': meaning, 'component_ids': components, 'input_schema': _multiwave_object(args), 'output_schema': _multiwave_object(output),
            'example_arguments': [example], 'observes': immediate, 'state_effects': effects, 'preconditions': pre, 'scene_backend_required': scene_required,
            'binding': f"basic40_runtime_thermal.Multiwave7101Session.invoke(instance_id=..., capability_id='{cap}', arguments=..., request_id=...)",
            'completion_criterion_zh': '同步成功只确认设定/场景动作提交；达到温度必须由后续宿主step和显示读数确认。'}
    unsupported = {}
    for cap, why in {'pressure.set_limit': '未取得24×8 mL架型压力限值；禁止以整机压力替代。', 'chemistry.digest': '无酸消解动力学、压力或物质模型。', 'hardware.connect': '无真实仪器驱动。', 'robot.transfer': '无机器人抓取或液体转移执行。'}.items():
        unsupported[cap] = {'availability': 'unsupported', 'visibility': 'agent', 'kind': 'device_command', 'description_zh': why,
            'component_ids': ['rack'] if cap.startswith('pressure.') else ['housing'], 'input_schema': _multiwave_object({}), 'output_schema': _multiwave_object({}),
            'example_arguments': [{}], 'state_effects': [], 'observes': [], 'preconditions': [], 'binding': None, 'scene_backend_required': False}
    def p(value, unit, evidence, source): return {'value': value, 'unit': unit, 'evidence': evidence, 'source': source}
    params = {
        'transferable_power_W': p(1500.0, 'W', 'public_document', MULTIWAVE_7101_SOURCE),
        'installed_power_W': p(2000.0, 'W', 'public_document', MULTIWAVE_7101_SOURCE),
        'maximum_temperature_K': p(573.15, 'K', 'public_document', MULTIWAVE_7101_SOURCE),
        'slot_count': p(24, '1', 'catalogue', 'Specified 24 x 8 mL rack'),
        'slot_capacity_m3': p(8e-6, 'm^3', 'catalogue', '8 mL x 1e-6 m^3/mL'),
        'rack_pressure_limit_Pa': p(None, 'Pa', 'unknown', 'Rack-specific limit unavailable; whole-instrument pressure is not substituted.'),
        'thermal_capacity_J_K': p(MULTIWAVE_7101_CAPACITY_J_K, 'J/K', 'derived_estimate', 'C=-UA*330/ln(1-UA*150/1000), with assumed absorbed1000W, ambient298.15K and UA1W/K. Not measured or identifiable from the published time alone.'),
        'heat_loss_W_K': p(1.0, 'W/K', 'assumption', 'Closed-vessel one-node UA; no measured cooldown calibration.'),
        'open_extra_loss_W_K': p(5.0, 'W/K', 'assumption', 'Illustrative passive open-door extra UA, not factory active cooling.'),
        'reference_absorbed_power_W': p(1000.0, 'W', 'assumption', 'Assumed thermal input; reference says <55% of2000W installed input. Transfer efficiency is not measured.'),
        'reference_heatup_time_s': p(330.0, 's', 'public_document', MULTIWAVE_7101_SOURCE + ' PDF page5; 175degC in5.5min, <55%installed power across7101/7301/7501.'),
        'factory_ramp_rate_K_s': p(None, 'K/s', 'unknown', 'No7101 ramp rating established; 70degC/min statement is specific to7301 and is not reused.'),
        'factory_cooldown_time_s': p(None, 's', 'unknown', 'Not calibrated to a7101/rack-specific cooldown benchmark.'),
    }
    caps.update(_multiwave_template_module().capability_specs())  # 2026-10 template hook (additive capabilities)
    return _mw_extend_metadata({'asset_types': {MULTIWAVE_7101_TYPE: {'catalog_id': 'CHEM-047', 'model': 'Anton Paar Multiwave 7101, 24 x 8 mL rack', 'asset_version': '1.1.0', 'state_slots': slots, 'capabilities': caps, 'declared_capabilities': unsupported, 'parameters': params}},
        'host_capabilities': {'sim.step': {'kind': 'simulation', 'visibility': 'trusted_host_only', 'input_schema': _multiwave_object({'dt_s': n('s', 0, 86400)}), 'binding': 'Multiwave7101Runtime.step(dt_s)', 'partial_failure': 'Clock/process commit before callbacks; synchronization failure may leave partially updated scene.'}},
        'trusted_scene_callback': 'scene_adapter(instance_id,updates): full public scene snapshot; door.angle_rad maps about negativeX; rack.occupied_slots holds24 visual flags; no process_truth is passed.',
        'result_schema_scope': 'capability.output_schema validates response.result only',
        'limitations': ['Thermal node is an illustrative loaded-system surrogate, not sample/acid thermodynamics, boiling, pressure or digestion kinetics.', 'C inferred using assumed absorbed power and UA; published heatup constrains but does not identify these quantities. No measured calibration claim.', 'Empty/occupied visual slots do not alter synthetic thermal capacity or represent liquid transfer.', 'Temperature display is ideal rounded0.1degC, quality=estimated; no real sensor model.', 'Door inhibit is a simulation precondition, not verified physical interlock or pressure-release safety.', 'Passive cooling only; no measured active-cooling performance.', 'API access filtering is not a Python/process/filesystem sandbox; host/debug artifacts must remain trusted.', 'No hardware/robot execution, restart-persistent deduplication, pressure or chemical safety claims.']}, panel)


def multiwave_7101_calibration_report():
    c = MULTIWAVE_7101_CAPACITY_J_K
    def time_to(target, power):
        return -c / MULTIWAVE_7101_LOSS_W_K * math.log1p(-MULTIWAVE_7101_LOSS_W_K * (target - MULTIWAVE_7101_AMBIENT_K) / power)
    return {'catalog_id': 'CHEM-047', 'evidence_status': 'manufacturer_time_constrained_illustrative_parameterization',
        'is_measured_calibration': False, 'source': MULTIWAVE_7101_SOURCE, 'source_pdf_page': 5,
        'source_heatup_temperature_K': 448.15, 'source_heatup_time_s': 330.0, 'source_installed_power_W': 2000.0, 'source_power_fraction_upper_bound': .55,
        'source_ambient_temperature_K': None, 'source_absorbed_power_W': None,
        'assumed_ambient_temperature_K': 298.15, 'assumed_reference_absorbed_power_W': 1000.0,
        'assumed_UA_W_K': 1.0, 'inferred_effective_C_J_K': c, 'rated_transferable_power_W': 1500.0,
        'inference_equation': 'C=-UA*t/log(1-UA*(Tref-Ta)/P_assumed); P_input and P_absorbed are not interchangeable without an efficiency assumption.',
        'reference_175C_at_assumed1000W_s': time_to(448.15, 1000.0), 'prediction_175C_at1500W_s': time_to(448.15, 1500.0),
        'prediction_300C_at1500W_s': time_to(573.15, 1500.0), 'prediction_initial_full_power_K_s': 1500.0 / c,
        'predicted_closed_passive_half_cooling_s': c * math.log(2),
        'whole_instrument_mass_used_as_thermal_capacity': False, 'rack_pressure_limit_Pa': None, 'factory_ramp_K_s': None,
        'factory_cooling_validation': 'not_run; no rack-specific benchmark used',
        'assumptions': ['Single well-mixed effective thermal node; properties constant from ambient to300degC.', 'UA=1W/K and absorbed1000W are unmeasured choices, not uniquely fitted values.', 'Assumed reference absorbed power is below1100W installed-input upper bound; actual conversion efficiency remains unknown.', 'No pressure, phase transition, chemistry, vessel mass allocation or loaded-rack safety inference.', 'Extra open-door passive loss5W/K is unmeasured; no active cooling curve.']}


# ---- G02 front panel (approximate): the Multiwave touchscreen software behind panel_ui.json.  No Multiwave 7101 manual is saved; the
# method library, step editor, run view (MyData), Data Explorer and settings follow the Multiwave 5000 Reference Guide E38IB001EN-G
# (same Anton Paar touchscreen software family; PDF page numbers) and the 7101 product page.  Pre-installed methods are the EPA programs of
# the digestion template library (ramp then hold).
from datetime import timedelta as _mw_timedelta

MULTIWAVE_7101_ASSET_DIR = Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t1_thermal/multiwave_7101'
_MW_RAMP_SUBSTEP_S = 5.0        # sub-step of a temperature ramp (the setpoint follows the ramp at the sub-step ends)
_MW_COOL_END_K = 333.15         # assumption: the safety cooling (p.52) ends when the reading falls to 60 degC
_MW_REACHED = 0.9               # p.51: 'Temperature is reached' ends the step at 90 % of the target temperature
_MW_MAX_STEPS = 10              # assumption: steps per program
_MW_TIME_MAX_S = 99 * 60.0      # assumption: longest step 99 min
_MW_FANS = (0, 1, 2, 3)         # assumption: fan levels 0-3 (p.51 names level 0)
_MW_RECORDS = 200               # assumption: experiments kept in the Data Explorer
_MW_OPTION = {'hno3': 'HNO3', 'hno3_hcl': 'HNO3+HCl', 'hno3_hf': 'HNO3+HF'}
_MW_FAN_K = 0.25                # assumption: each fan level above / below level 1 adds / removes 25 % of the calibrated cavity heat-loss coefficient
_MW_TRACE_S = 10.0              # assumption: sample interval of the run graph (temperature, pressure, power)
_MW_AMBIENT_PA = 101325.0       # sealed vials below 100 degC: atmospheric pressure (no pre-pressurization value in our documents)
# System security and user management (mw5000 p.38-41): three preset accounts with their roles, auto login of the administrator, passwords not required
# by default (table 6), user names at least 6 characters.  Functions per role from the role descriptions (p.38-39; the permission list itself is not
# printed): administrator everything; chemist experiments and methods; operator experiments.  A run can always be aborted, even without a login (p.38).
_MW_ROLES = {'administrator': ('run', 'methods', 'settings', 'security'), 'chemist': ('run', 'methods'), 'operator': ('run',)}
_MW_RIGHTS = {'methods.select': 'run', 'run.start': 'run', 'methods.create': 'methods', 'methods.copy': 'methods', 'methods.delete': 'methods',
              'methods.set_step': 'methods', 'methods.add_step': 'methods', 'methods.delete_step': 'methods', 'run.set_step': 'methods', 'run.add_step': 'methods',
              'run.delete_step': 'methods', 'settings.configure': 'settings', 'security.configure': 'security', 'users.add': 'security',
              'users.set_active': 'security', 'users.remove': 'security'}
_MW_USERS_DEFAULT = [{'user_name': 'administrator', 'full_name': 'Administrator', 'roles': ['administrator'], 'active': True},
                     {'user_name': 'chemist', 'full_name': 'Chemist', 'roles': ['chemist'], 'active': True},
                     {'user_name': 'operator', 'full_name': 'Operator', 'roles': ['operator'], 'active': True}]
_MW_PASSWORDS_DEFAULT = {'administrator': 'administrator', 'chemist': 'chemist', 'operator': 'operator'}   # p.38-39 default passwords
_MW_SECURITY_DEFAULT = {'auto_login': True, 'auto_login_user': 'administrator', 'password_required': False, 'user_name_min_length': 6,
                        'password_min_length': 8, 'auto_logout': False, 'logout_timeout_min': 10}        # p.38-39, table 6; logout time default: assumption
# Error codes of the software family (mw5000 Appendix A, PDF p.57-62) that do not depend on Multiwave 5000 hardware, for the evaluator-only fault.inject
# (sweep 2026-10-19, lead rule 3): code -> (kind, cleared by, message, handling, PDF page).  An error stops the microwave, aborts a running method
# (safety cooling follows) and shows its message until acknowledged; START is refused while an error is shown or its cause persists.  Cleared by:
# 'ack' = acknowledging the message (the operator adjusts the method or restarts the run), 'restart' = switching the instrument off and on,
# 'fix' / 'service' = the operator's check or the Anton Paar service (fault.clear).  The warning 10A keeps the run going.  Not offered (5000
# hardware absent from the 7101 or procedures the simulation lacks): IR sensor / sensor adjustment 003-005, 038, 100, 104, 108, 10B, 10E; power and
# IR calibration 010-016; second magnetron 021; rotor detection, protection lid and Hall sensors 034-037, 039, 3A, 3C, 040, 041, 201, 202; Rotor 8N 106;
# Safety Module SOLV 107; SmartTemp sensors 301.  Classes and the abort of a running method are assumptions (the table gives message and handling).
_MW_FAULTS = {
    '001': ('error', 'restart', 'Sensor value failure. Restart the instrument.', 'Restart the instrument.', 57),
    '002': ('error', 'restart', 'Sensor value is out of range. Restart the instrument.', 'Check if the sensor is defect. Check the connection of the sensor.', 57),
    '006': ('error', 'restart', 'Generic error. Restart the instrument.', 'Restart the instrument.', 57),
    '007': ('error', 'restart', 'Communication to sub prints lost. Restart the instrument.', 'Restart the instrument.', 57),
    '020': ('error', 'fix', 'Magnetron 1 too hot. Please check the cooling air inlets.', 'Check the cooling air inlets.', 58),
    '022': ('error', 'fix', 'Unexpected door open event. Please check if the door is closed properly.', 'Check if the door is closed properly.', 58),
    '026': ('error', 'service', 'Communication to Powerboard lost. Error occurred on the Generic port!', 'Contact your Anton Paar service engineer.', 59),
    '101': ('error', 'ack', 'Temperature sensor limit has been exceeded significantly. Please adjust the method parameter to the sample and weight.',
            'Adjust the method parameter to the sample and weight.', 59),
    '102': ('error', 'ack', 'Significant Pressure Limit Overrange! The process has to be aborted.', 'Adjust the method parameter to the sample and weight.', 60),
    '103': ('error', 'fix', 'Low MW current measured although actuating power set. Please check the closing of the door.',
            'Check the instrument placement condition and ambient temperature.', 60),
    '105': ('error', 'ack', 'Temperature did not increase during the last 5 minutes! The process has to be aborted.',
            'Adjust the method parameter to the sample and weight. Check the power output.', 60),
    '109': ('error', 'restart', 'Mains supply frequency synchronization failed. Please restart the instrument.', 'Check the mains supply.', 61),
    '10A': ('warning', 'ack', 'Heating rate of current step was insufficient. Set temperature cannot be reached. Please check your method and sample.',
            'Check your method and sample.', 61),
    '10C': ('error', 'ack', 'Control state machine, actuator setting failed. Please restart the run.', 'Restart the run.', 61),
    '10D': ('error', 'ack', 'Significant pressure loss during sample processing. Please check the vessels and the method.', 'Check the method and sample. Check the vessels.', 61),
    '203': ('error', 'restart', 'Internal Error. Please restart the instrument.', 'Restart the instrument.', 62),
}


def _mw_preinstalled():
    """The pre-installed methods (they can neither be modified nor deleted, p.48): the EPA programs of the digestion template library as a
    ramp step and a hold step at the target temperature, full power limit, fan level 1 (assumption)."""
    out = []
    for tid, rec in _multiwave_template_module().methods().items():
        prog = rec['program']
        target_c = round(prog['target_temperature_K'] - 273.15, 2)
        name = rec['method'].replace('EPA SW-846 Method ', 'EPA ') + ' ' + _MW_OPTION.get(rec.get('option'), str(rec.get('option')).upper())
        out.append({'name': name, 'note': rec['name'], 'application': 'Acid digestion', 'program': 'temperature', 'max_power_W': 1500.0,
                    'max_temperature_C': 300.0, 'preinstalled': True, 'template_id': tid,
                    'steps': [{'temperature_C': target_c, 'power_W': None, 'time_s': float(prog['ramp_time_s']), 'step_type': 'time', 'fan': 1},
                              {'temperature_C': target_c, 'power_W': None, 'time_s': float(prog['hold_time_s']), 'step_type': 'time', 'fan': 1}]})
    return out


def _mw_n(unit, minimum=None, maximum=None):
    return _multiwave_number_schema(unit, minimum, maximum)


_MW_STEP = {'type': 'object', 'properties': {'temperature_C': {'anyOf': [_mw_n('degC', 0, 300), {'type': 'null'}]}, 'power_W': {'anyOf': [_mw_n('W', 0, 1500), {'type': 'null'}]},
                                             'time_s': _mw_n('s', 0, _MW_TIME_MAX_S), 'step_type': {'type': 'string', 'enum': ['time', 'temperature']},
                                             'fan': {'type': 'integer', 'minimum': 0, 'maximum': 3, 'x-unit': '1'}},
            'required': ['temperature_C', 'power_W', 'time_s', 'step_type', 'fan'], 'additionalProperties': False}
_MW_SLOTS = {   # id: (unit, origin, meaning)
    'methods.library': ('1', 'setpoint', '方法库：预装方法（EPA程序，不能修改或删除）与用户方法（名称、应用组、温度或功率程序、上限、最多10步：温度/功率、时间、步骤类型、风扇档）'),
    'methods.selected': ('1', 'setpoint', '所选方法（START运行它）'),
    'run.status': ('1', 'derived', '实验状态：idle、running（程序步骤）、cooling（安全冷却，到60 °C）、finished'),
    'run.method': ('1', 'derived', '正在或最后运行的方法名'),
    'run.steps': ('1', 'derived', '正在运行的程序步骤（运行中可在Step Editing修改当前及之后的步骤）'),
    'run.step_index': ('1', 'derived', '当前步骤序号（0起）；未运行为null'),
    'run.step_elapsed_s': ('s', 'derived', '当前步骤已运行时间'),
    'run.elapsed_s': ('s', 'derived', '实验已运行时间（含冷却）'),
    'run.remaining_s': ('s', 'derived', '程序剩余时间（未完成步骤的时间之和，不含冷却）'),
    'run.controller_state': ('1', 'derived', '控制器状态字母（p.50–51表7）：- 无功率限制、P 功率控制、T 内部温度控制、U 用户中断'),
    'run.warnings': ('1', 'derived', '本次实验的警告（如目标温度未在步骤时间内达到）'),
    'data.experiments': ('1', 'derived', 'Data Explorer的实验记录（方法、起止时间、结果、最高温度、步骤数），最多200条'),
    'settings.brightness_percent': ('%', 'setpoint', '显示亮度（Instrument settings）'),
    'run.fan_level': ('1', 'derived', '正在起作用的风扇档：运行中为当前步骤的档位，安全冷却沿用最后一步的档位；未运行方法为null（原有直接命令用标定的散热）'),
    'cavity.pressure_pa': ('Pa', 'derived', '腔体压力读数：门关时为容器内水饱和蒸气压（IAPWS表，100 °C以下取大气压），门开为大气压；预加压值不在我们的资料中，未建模'),
    'run.trace': ('1', 'derived', '运行曲线（主图）：至少相隔10 s一点（在宿主推进的子步末）[时间s, 温度°C, 压力Pa, 功率W]，START时清空'),
    'users.accounts': ('1', 'setpoint', '用户账户（mw5000 p.38–41）：用户名、全名、缩写、角色、是否激活；出厂三个预设账户administrator/chemist/operator'),
    'users.current': ('1', 'derived', '当前登录的用户名；null为未登录（显示登录画面）'),
    'security.settings': ('1', 'setpoint', 'System security：自动登录与其用户、是否需要密码、用户名/密码最小长度、自动注销与时间（p.38–39，出厂值见表6）'),
    'users.passwords': ('1', 'setpoint', '各账户的密码（调试可见；出厂为p.38–39的默认密码）'),
    'error.code': ('1', 'derived', '正在显示或原因仍在的错误码（mw5000附录A，如102）；null为无错误；有错误时START被拒（2026-10-19）'),
    'error.popup': ('1', 'derived', '错误信息窗口正打开（未确认）；确认后关闭（error.acknowledge）'),
    'fault.injected': ('1', 'derived', '评测者注入、原因仍在的错误码（调试可见）：restart类关机再开即排除，fix/service类由fault.clear排除'),
}
_MW_SCHEMAS = {
    'methods.library': {'type': 'array', 'items': {'type': 'object'}}, 'methods.selected': {'type': 'string'},
    'run.status': {'type': 'string', 'enum': ['idle', 'running', 'cooling', 'finished']}, 'run.method': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
    'run.steps': {'anyOf': [{'type': 'array', 'items': _MW_STEP}, {'type': 'null'}]}, 'run.step_index': {'anyOf': [{'type': 'integer', 'minimum': 0, 'x-unit': '1'}, {'type': 'null'}]},
    'run.step_elapsed_s': {'anyOf': [_mw_n('s', 0), {'type': 'null'}]}, 'run.elapsed_s': {'anyOf': [_mw_n('s', 0), {'type': 'null'}]},
    'run.remaining_s': {'anyOf': [_mw_n('s', 0), {'type': 'null'}]}, 'run.controller_state': {'type': 'string', 'enum': ['-', 'P', 'T', 'U']},
    'run.warnings': {'type': 'array', 'items': {'type': 'string'}}, 'data.experiments': {'type': 'array', 'items': {'type': 'object'}},
    'settings.brightness_percent': _mw_n('%', 10, 100),
    'run.fan_level': {'anyOf': [{'type': 'integer', 'minimum': 0, 'maximum': 3, 'x-unit': '1'}, {'type': 'null'}]}, 'cavity.pressure_pa': _mw_n('Pa', 0),
    'run.trace': {'type': 'array', 'items': {'type': 'array'}}, 'users.accounts': {'type': 'array', 'items': {'type': 'object'}},
    'users.current': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}, 'security.settings': {'type': 'object'}, 'users.passwords': {'type': 'object'},
    'error.code': {'anyOf': [{'type': 'string', 'enum': sorted(_MW_FAULTS)}, {'type': 'null'}]}, 'error.popup': {'type': 'boolean'},
    'fault.injected': {'type': 'array', 'items': {'type': 'string', 'enum': sorted(_MW_FAULTS)}}}
_MW_COMPUTED = ('run.remaining_s', 'run.controller_state')
_MW_NAME = {'type': 'string'}
_MW_STEP_ARGS = {'temperature_C': _mw_n('degC', 0, 300), 'power_W': _mw_n('W', 0, 1500), 'time_s': _mw_n('s', 0, _MW_TIME_MAX_S),
                 'step_type': {'type': 'string', 'enum': ['time', 'temperature']}, 'fan': {'type': 'integer', 'minimum': 0, 'maximum': 3, 'x-unit': '1'}}
_MW_CAPS = {   # name -> (kind, properties, optional, output, observes, meaning)
    'methods.select': ('configuration', {'name': _MW_NAME}, (), {'name': _MW_NAME}, ['methods.selected'], '在方法库中选择方法（Menu > Methods，p.48–49）'),
    'methods.create': ('configuration', {'name': _MW_NAME, 'program': {'type': 'string', 'enum': ['temperature', 'power']}, 'max_power_W': _mw_n('W', 0, 1500),
                                         'max_temperature_C': _mw_n('degC', 0, 300)}, (), {'name': _MW_NAME}, ['methods.library', 'methods.selected'],
                       'Create Method：名称、温度或功率程序、上限（默认为最大值，p.49）；新方法带一个默认步骤并被选中'),
    'methods.copy': ('configuration', {'name': _MW_NAME}, (), {'name': _MW_NAME}, ['methods.library', 'methods.selected'], '复制方法（预装方法只能复制后修改；新名加时间戳，p.50）'),
    'methods.delete': ('configuration', {'name': _MW_NAME}, (), {'name': _MW_NAME}, ['methods.library', 'methods.selected'], '删除用户方法（预装方法不能删除，p.48）'),
    'methods.set_step': ('configuration', {'name': _MW_NAME, 'index': {'type': 'integer', 'minimum': 0, 'maximum': _MW_MAX_STEPS - 1, 'x-unit': '1'}, **_MW_STEP_ARGS},
                         ('temperature_C', 'power_W', 'step_type'), {'name': _MW_NAME, 'index': {'type': 'integer', 'x-unit': '1'}}, ['methods.library'],
                         'Step Editor：一步的温度（温度程序）或功率（功率程序）、时间、步骤类型（Time is reached / Temperature is reached，仅温度程序）、风扇档（p.50）'),
    'methods.add_step': ('configuration', {'name': _MW_NAME}, (), {'name': _MW_NAME, 'count': {'type': 'integer', 'x-unit': '1'}}, ['methods.library'], 'Add Step：在末尾加一步（复制最后一步）'),
    'methods.delete_step': ('configuration', {'name': _MW_NAME, 'index': {'type': 'integer', 'minimum': 0, 'maximum': _MW_MAX_STEPS - 1, 'x-unit': '1'}}, (),
                            {'name': _MW_NAME, 'count': {'type': 'integer', 'x-unit': '1'}}, ['methods.library'], 'Delete Step：删除一步（至少保留一步）'),
    'run.start': ('device_command', {}, (), {'method': _MW_NAME}, ['run.status', 'run.method', 'microwave.enabled', 'thermal.target_k'],
                  'START：运行所选方法（p.50–51）：温度程序每步把工作设定点在步骤时间内线性升到步骤温度（同温即保持），Temperature is reached步骤在读数达到目标的90 %时结束；'
                  '功率程序按步骤功率加热（以方法温度上限限温）；最后进入安全冷却。需上电、门关、非运行中'),
    'run.abort': ('device_command', {}, (), {'status': {'type': 'string'}}, ['run.status', 'microwave.enabled'], 'STOP：用户中断（控制器状态U），关微波并进入安全冷却'),
    'run.set_step': ('device_command', {'index': {'type': 'integer', 'minimum': 0, 'maximum': _MW_MAX_STEPS - 1, 'x-unit': '1'}, **_MW_STEP_ARGS},
                     ('temperature_C', 'power_W', 'step_type'), {'index': {'type': 'integer', 'x-unit': '1'}}, ['run.steps'],
                     'Step Editing（运行中，p.51）：修改当前或之后一步的温度/功率与时间'),
    'run.add_step': ('device_command', {}, (), {'count': {'type': 'integer', 'x-unit': '1'}}, ['run.steps'], 'Step Editing：在运行的程序末尾加一步'),
    'run.delete_step': ('device_command', {'index': {'type': 'integer', 'minimum': 0, 'maximum': _MW_MAX_STEPS - 1, 'x-unit': '1'}}, (),
                        {'count': {'type': 'integer', 'x-unit': '1'}}, ['run.steps'], 'Step Editing：删除当前之后的一步'),
    'settings.configure': ('configuration', {'brightness_percent': _mw_n('%', 10, 100)}, (), {'brightness_percent': _mw_n('%')}, ['settings.brightness_percent'],
                           'Settings > System > Instrument settings：显示亮度（p.40）'),
    'user.login': ('device_command', {'user_name': _MW_NAME, 'password': {'type': 'string'}}, ('password',), {'user_name': _MW_NAME}, ['users.current'],
                   '登录（p.38）：在登录画面选用户（需要密码时输入密码）；账户须存在且激活'),
    'user.logout': ('device_command', {}, (), {'user_name': {'anyOf': [_MW_NAME, {'type': 'null'}]}}, ['users.current'],
                    'LOG OUT（p.38）：回到登录画面；已开始的运行继续到结束，未登录时仍可STOP中断运行'),
    'user.change_password': ('device_command', {'old_password': {'type': 'string'}, 'new_password': {'type': 'string'}}, (), {'user_name': _MW_NAME}, [],
                             'CHANGE PASSWORD（p.40）：当前用户输入旧密码与新密码；需要密码时新密码至少达到最小长度'),
    'security.configure': ('configuration', {'auto_login': {'type': 'boolean'}, 'auto_login_user': _MW_NAME, 'password_required': {'type': 'boolean'},
                                             'auto_logout': {'type': 'boolean'}, 'logout_timeout_min': {'type': 'integer', 'minimum': 1, 'maximum': 120, 'x-unit': 'min'}},
                           ('auto_login', 'auto_login_user', 'password_required', 'auto_logout', 'logout_timeout_min'), {'ok': {'type': 'boolean'}}, ['security.settings'],
                           'Settings > System security：Login settings（自动登录、自动登录用户、是否需要密码）与Logout settings（自动注销及时间，p.38–39）；需administrator角色'),
    'users.add': ('configuration', {'user_name': _MW_NAME, 'full_name': _MW_NAME, 'role': {'type': 'string', 'enum': list(_MW_ROLES)}, 'password': {'type': 'string'}},
                  ('password',), {'user_name': _MW_NAME}, ['users.accounts'],
                  'Users：新建用户账户（用户名唯一且至少6字符，删除过的用户名不能再用；缩写由全名生成；指定一个角色与初始密码，p.40）；需administrator角色'),
    'users.set_active': ('configuration', {'user_name': _MW_NAME, 'active': {'type': 'boolean'}}, (), {'user_name': _MW_NAME}, ['users.accounts'],
                         'Users：激活或停用账户（不能停用自己）；需administrator角色'),
    'users.remove': ('configuration', {'user_name': _MW_NAME}, (), {'user_name': _MW_NAME}, ['users.accounts'],
                     'Users：删除账户（不能删除自己；用户名此后不可再用，p.40）；需administrator角色'),
    'error.acknowledge': ('device_command', {}, (), {'code': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}}, ['error.popup', 'error.code'],
                          '确认错误信息（错误窗口的OK，2026-10-19）：关闭窗口；原因已不在（ack类、已排除）则错误结束，原因仍在则错误码保留、START仍被拒；不需登录'),
}
_MW_FAULT_CAPS = {   # evaluator only (sweep 2026-10-19): name -> (meaning, observes)
    'fault.inject': ('评测者：注入mw5000附录A中不依赖5000硬件的错误码（见_MW_FAULTS）：错误关微波、中止运行中的方法（随后安全冷却）、显示错误窗口；警告10A运行继续；'
                     '关机时注入的持续原因在开机时出现', ['fault.injected', 'error.code', 'error.popup', 'run.status', 'microwave.enabled']),
    'fault.clear': ('评测者：错误原因已排除（操作者检查或Anton Paar服务）；窗口仍须确认；restart类也可按手册关机再开排除', ['fault.injected', 'error.code']),
}


def _mw_capability(name, kind, props, optional, output, observes, meaning):
    return {'availability': 'implemented', 'visibility': 'agent', 'kind': kind, 'component_ids': ['controller'], 'description_zh': meaning,
            'input_schema': {'type': 'object', 'properties': props, 'required': [k for k in props if k not in optional], 'additionalProperties': False},
            'output_schema': _multiwave_object(output), 'example_arguments': [],
            'observes': list(observes), 'state_effects': [{'state_id': sid, 'timing': 'immediate', 'description_zh': meaning} for sid in observes],
            'preconditions': [{'description_zh': '已上电；运行或冷却中只接受run.abort、Step Editing与错误确认', 'state_ids': ['power.enabled', 'run.status'], 'enforced_by': 'adapter',
                               'check': 'power.enabled and run.status allows the command'}] +
                             ([{'description_zh': '没有正在显示或原因仍在的错误（mw5000附录A）', 'state_ids': ['error.code'], 'enforced_by': 'adapter',
                                'check': 'error.code is None'}] if name == 'run.start' else []) +
                             ([{'description_zh': '当前登录用户的角色有此功能的权限（administrator全部；chemist实验与方法；operator实验，p.38–39）',
                                'state_ids': ['users.current', 'users.accounts'], 'enforced_by': 'adapter', 'check': 'role of users.current grants ' + _MW_RIGHTS[name]}]
                              if name in _MW_RIGHTS else []),
            'scene_backend_required': False, 'binding': f"basic40_runtime_thermal.Multiwave7101Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
            'completion_criterion_zh': '同步返回即命令已被接受；程序随后由宿主step推进'}


def _mw_panel():
    return _g02_panel('multiwave_7101', MULTIWAVE_7101_ASSET_DIR, set(_MW_CAPS) | {'power.set', 'door.open', 'door.close'})


def _mw_extend_metadata(meta, panel):
    spec = meta['asset_types'][MULTIWAVE_7101_TYPE]
    examples = {'methods.select': {'name': 'EPA 3051A HNO3'}, 'methods.create': {'name': 'MY METHOD', 'program': 'temperature', 'max_power_W': 1500.0, 'max_temperature_C': 300.0},
                'methods.copy': {'name': 'EPA 3051A HNO3'}, 'methods.delete': {'name': 'MY METHOD'},
                'methods.set_step': {'name': 'MY METHOD', 'index': 0, 'temperature_C': 180.0, 'time_s': 600.0, 'step_type': 'time', 'fan': 1},
                'methods.add_step': {'name': 'MY METHOD'}, 'methods.delete_step': {'name': 'MY METHOD', 'index': 1}, 'run.start': {}, 'run.abort': {},
                'run.set_step': {'index': 1, 'temperature_C': 175.0, 'time_s': 300.0, 'step_type': 'time', 'fan': 1}, 'run.add_step': {}, 'run.delete_step': {'index': 1},
                'settings.configure': {'brightness_percent': 80.0}, 'user.login': {'user_name': 'chemist'}, 'user.logout': {},
                'user.change_password': {'old_password': '', 'new_password': 'new-pass-1'}, 'security.configure': {'auto_login': False},
                'users.add': {'user_name': 'analyst1', 'full_name': 'Lab Analyst', 'role': 'operator'}, 'users.set_active': {'user_name': 'operator', 'active': False},
                'users.remove': {'user_name': 'analyst1'}, 'error.acknowledge': {}}
    controller = {}
    for name, (kind, props, optional, output, observes, meaning) in _MW_CAPS.items():
        controller[name] = _mw_capability(name, kind, props, optional, output, observes, meaning)
        controller[name]['example_arguments'] = [examples[name]]
    for sid, (unit, origin, meaning) in _MW_SLOTS.items():
        spec['state_slots'][sid] = {'unit': unit, 'origin': origin, 'visibility': 'debug' if sid in ('users.passwords', 'fault.injected') else 'agent', 'access': 'read_only',
                                    'write_via': None, 'value_schema': _MW_SCHEMAS[sid],
                                    'reference_conditions': None, 'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None},
                                    'read_binding': f"Multiwave7101Session.read(instance_id=..., state_ids=['{sid}'])", 'meaning_zh': meaning}
    code = {'type': 'string', 'enum': sorted(_MW_FAULTS)}
    evaluator = {name: dict(_mw_capability(name, 'simulation', {'code': code}, (), {'code': code}, observes, meaning), visibility='evaluator',
                            component_ids=['controller', 'display'], preconditions=[], example_arguments=[{'code': '102'}], completion_criterion_zh='同步返回即生效')
                 for name, (meaning, observes) in _MW_FAULT_CAPS.items()}
    panel_caps = {}
    if panel:
        p = _mw_panel()
        called = p['engine'].spec_capabilities(p['spec'])
        caps = {**spec['capabilities'], **controller}
        effects = list({e['state_id']: dict(e, description_zh='面板动作调用的能力的效果：' + e['description_zh']) for c in called for e in caps[c]['state_effects']
                        if e['timing'] == 'immediate'}.values())
        panel_caps = _g02_panel_capabilities(p, "basic40_runtime_thermal.Multiwave7101Session.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)", effects)
    spec.update(controller_capabilities=controller, evaluator_capabilities=evaluator, panel_capabilities=panel_caps,
                asset_version='1.3.1')   # 1.3.0: users and roles, fan, pressure, error codes (2026-10-19); 1.3.1: digestion.evaluate judges the run's set point (2026-10-22)
    meta['limitations'] = meta['limitations'] + [
        'Touchscreen software behind the front panel (approximate: no Multiwave 7101 manual saved; the Multiwave 5000 reference guide of the same software family): '
        'method library with the EPA programs of the digestion template library as pre-installed methods, user methods with up to 10 steps, temperature programs '
        '(linear ramp of the working setpoint over the step time, Time is reached / Temperature is reached at 90 %) and power programs, run view, Step Editing, '
        'user break, safety cooling to 60 degC (passive cooling of the thermal node), Data Explorer records and display brightness.',
        'User management (2026-10-19, mw5000 p.38-41): three preset accounts and roles, auto login of the administrator at power-on (default), log in / out, '
        'own password change, login and logout settings, users added / deactivated / removed; the roles gate the touchscreen functions (administrator all, chemist '
        'experiments and methods, operator experiments; the permission list of the role editor is not printed, so roles cannot be edited); STOP works without a '
        'login. The original direct commands (power, door, rack, thermal.*, microwave.*) are not gated (simulation-level operations).',
        'Fan level (2026-10-19): the active step fan level scales the cavity heat-loss coefficient, UA x (1 + 0.25 x (level - 1)) (calibrated at level 1; the factor '
        'is an assumption), during the step and the safety cooling (last step level).',
        'Cavity pressure (2026-10-19): water saturation pressure of the vessels at the cavity temperature when the door is closed (lower bound, IAPWS table), '
        'atmospheric below 100 degC or with the door open; shown on MyData and in the run graph and stored as the maximum of a run. The PDC pre-pressurization, '
        'pressure limits and pressure-rate control (states p / r / R / O) are not in our documents and not simulated.',
        'Error codes (2026-10-19, mw5000 Appendix A): the codes of the software family that do not depend on Multiwave 5000 hardware (001, 002, 006, 007, 020, '
        '022, 026, 101, 102, 103, 105, 109, 10A, 10C, 10D, 203) occur only by evaluator injection (fault.inject / fault.clear): an error stops the microwave, '
        'aborts a running method into the safety cooling and shows its message and handling until acknowledged; START is refused while an error is shown or its '
        'cause persists (restart class: until switched off and on; fix / service class: until fault.clear); the warning 10A keeps the run going. That the 7101 '
        'uses these codes, their classes and the abort are assumptions. Codes of 5000 hardware (IR sensors, rotors, Hall sensors, protection lid, Rotor 8N, '
        'Safety Module SOLV, SmartTemp, second magnetron) and of the power / IR calibration procedures are not offered.',
        'Not simulated: IR graph and IR states (no 7101 IR sensor documented; they belong to the Multiwave 5000 rotors), rotor / rack selection wizard, PDC '
        'opening wizard, export and printing, network and SmartLink, audit trail and report signing, role editor.']
    return meta


class _MultiwaveTouch:
    """The Multiwave touchscreen functions of one host (mixed into Multiwave7101Runtime): method library, program run, records, panel host."""

    @staticmethod
    def _mw_defaults():
        lib = _mw_preinstalled()
        return {'methods.library': lib, 'methods.selected': lib[0]['name'], 'run.status': 'idle', 'run.method': None, 'run.steps': None, 'run.step_index': None,
                'run.step_elapsed_s': None, 'run.elapsed_s': None, 'run.warnings': [], 'data.experiments': [], 'settings.brightness_percent': 100.0,
                'run.fan_level': None, 'run.trace': [], 'users.accounts': [dict(u, initials=''.join(w[0] for w in u['full_name'].split()).upper()[:3] or u['user_name'][:2].upper())
                                                                             for u in copy.deepcopy(_MW_USERS_DEFAULT)],
                'users.current': None, 'security.settings': dict(_MW_SECURITY_DEFAULT), 'users.passwords': dict(_MW_PASSWORDS_DEFAULT),
                'error.code': None, 'error.popup': False, 'fault.injected': []}

    @staticmethod
    def _mw_method(state, name):
        return next((m for m in state['methods.library'] if m['name'] == name), None)

    def _mw_clock_text(self, t):
        return (datetime(2026, 1, 1, tzinfo=timezone.utc) + _mw_timedelta(seconds=t)).strftime('%Y-%m-%d %H:%M')     # clock at 0 s: assumption

    def _mw_sub(self, inst, h):
        """One host sub-step of the program with the template hook (digested slots follow the same trajectory)."""
        if h <= 0:
            return
        state = inst['state']
        before = copy.deepcopy(state) if inst.get('template_digestion') else None
        self._advance(state, h)
        if before is not None:
            _multiwave_template_module().track_step(self._advance, inst, before, inst['mw']['t'], h)
        inst['mw']['t'] += h
        state['run.elapsed_s'] += h
        pressure = self._mw_pressure(state)
        inst['mw']['max_pa'] = max(inst['mw'].get('max_pa') or 0.0, pressure)
        if state['run.elapsed_s'] >= inst['mw'].get('next_trace_s', 0.0) - 1e-9:     # the run graph (Fig. 58), every 10 s
            state['run.trace'] = state['run.trace'] + [[round(state['run.elapsed_s'], 3), round(state['thermal.indicated_temperature_k'] - 273.15, 1), round(pressure),
                                                         round(state['microwave.delivered_power_w'], 1)]]
            inst['mw']['next_trace_s'] = state['run.elapsed_s'] + _MW_TRACE_S
        if state['run.status'] == 'running':
            state['run.step_elapsed_s'] += h
            t = state['thermal.indicated_temperature_k']
            inst['mw']['max_k'] = t if inst['mw']['max_k'] is None else max(inst['mw']['max_k'], t)

    def _mw_enter(self, inst, index):
        state, mw = inst['state'], inst['mw']
        mw['s0'] = state['thermal.target_k'] if index > 0 else min(state['thermal.temperature_k'], MULTIWAVE_7101_MAX_K)
        state['run.step_index'], state['run.step_elapsed_s'] = index, 0.0
        state['run.fan_level'] = state['run.steps'][index]['fan']                # the step's fan level cools the cavity from now on

    def _mw_setpoints(self, inst, elapsed):
        """Working setpoint (K) and power limit (W) of the current step after `elapsed` s of it."""
        state, mw = inst['state'], inst['mw']
        step = state['run.steps'][state['run.step_index']]
        if mw['program'] == 'power':
            return mw['limit_k'], step['power_W']
        target = step['temperature_C'] + 273.15
        if step['step_type'] == 'time' and step['time_s'] > 0:
            f = min(1.0, elapsed / step['time_s'])
            target = mw['s0'] + (target - mw['s0']) * f
        return max(self.ambient_temperature_K, min(target, mw['limit_k'])), mw['power_W']

    def _mw_finish(self, inst, result):
        state, mw = inst['state'], inst['mw']
        rec = {'id': mw['id'], 'method': state['run.method'], 'start': self._mw_clock_text(mw['start']), 'end': self._mw_clock_text(mw['t']), 'result': result,
               'max_temperature_C': None if mw['max_k'] is None else round(mw['max_k'] - 273.15, 1), 'steps': len(state['run.steps'] or []),
               'warnings': list(state['run.warnings']), 'max_pressure_bar': None if not mw.get('max_pa') else round(mw['max_pa'] / 1e5, 2),
               'user': state.get('users.current')}
        state['data.experiments'] = (state['data.experiments'] + [rec])[-_MW_RECORDS:]

    def _mw_start_cooling(self, inst, user_break):
        state = inst['state']
        state.update({'run.status': 'cooling', 'microwave.enabled': False, 'thermal.target_k': self.ambient_temperature_K, 'run.step_index': None,
                      'run.step_elapsed_s': None})
        state['microwave.delivered_power_w'] = self._delivered(state)
        inst['mw']['user_break'] = user_break

    def _mw_raise(self, inst, code):
        """An error of the Appendix A table occurs (evaluator injection): its message is shown until acknowledged; an error stops the microwave and
        aborts a running method into the safety cooling; a persistent cause (restart / fix / service class) is kept; the warning 10A only adds a
        run warning.  inst may be a candidate copy (state and mw)."""
        state, mw = inst['state'], inst['mw']
        kind, cleared, message, _, _ = _MW_FAULTS[code]
        state['error.code'], state['error.popup'] = code, True
        if cleared != 'ack' and code not in state['fault.injected']:
            state['fault.injected'] = state['fault.injected'] + [code]
        if kind == 'warning':
            if state['run.status'] == 'running':
                state['run.warnings'] = state['run.warnings'] + [f'error {code}: {message}']
            return
        if state['run.status'] == 'running':
            mw['error_code'] = code
            self._mw_start_cooling(inst, False)
        state['microwave.enabled'] = False
        state['microwave.delivered_power_w'] = self._delivered(state)

    @staticmethod
    def _mw_error_after(state):
        """The error code still shown once the message window is closed: the first persisting cause, else none."""
        return state['fault.injected'][0] if state['fault.injected'] else None

    def _mw_run_advance(self, inst, dt_s):
        state, mw = inst['state'], inst['mw']
        remaining = dt_s
        while remaining > 1e-9 and state['run.status'] in ('running', 'cooling'):
            if state['run.status'] == 'cooling':
                t, ta = state['thermal.temperature_k'], self.ambient_temperature_K
                ua = self._mw_ua(state)
                need = 0.0 if t <= _MW_COOL_END_K else (MULTIWAVE_7101_CAPACITY_J_K / ua * math.log((t - ta) / (_MW_COOL_END_K - ta)) if _MW_COOL_END_K > ta else math.inf)
                h = min(remaining, need)
                self._mw_sub(inst, h)
                remaining -= h
                if need <= h + 1e-9:
                    self._mw_finish(inst, f"aborted (error {mw['error_code']})" if mw.get('error_code') else 'aborted' if mw['user_break'] else 'completed')
                    state['run.status'], state['run.fan_level'] = 'finished', None
                continue
            steps, i = state['run.steps'], state['run.step_index']
            step = steps[i]
            left = step['time_s'] - state['run.step_elapsed_s']
            if left <= 1e-9:
                if step['step_type'] == 'temperature' and mw['program'] == 'temperature':
                    state['run.warnings'] = state['run.warnings'] + [f'step {i + 1}: target temperature not reached']
                self._mw_next(inst)
                continue
            if mw['program'] == 'temperature' and step['step_type'] == 'temperature':
                threshold = 273.15 + _MW_REACHED * step['temperature_C']
                if state['thermal.temperature_k'] >= threshold - 1e-9:
                    self._mw_next(inst)
                    continue
                target, power = self._mw_setpoints(inst, 0.0)
                state['thermal.target_k'], state['microwave.power_limit_w'] = target, power
                h = min(remaining, left)
                probe = copy.deepcopy(state)
                self._advance(probe, h)
                if probe['thermal.temperature_k'] >= threshold:
                    lo, hi = 0.0, h
                    for _ in range(60):
                        mid = 0.5 * (lo + hi)
                        p = copy.deepcopy(state)
                        self._advance(p, mid)
                        lo, hi = (lo, mid) if p['thermal.temperature_k'] >= threshold else (mid, hi)
                    self._mw_sub(inst, hi)
                    remaining -= hi
                    self._mw_next(inst)
                else:
                    self._mw_sub(inst, h)
                    remaining -= h
                    if state['run.step_elapsed_s'] >= step['time_s'] - 1e-9:    # the step time is over before 90 %: warning, next step (p.50)
                        state['run.warnings'] = state['run.warnings'] + [f'step {i + 1}: target temperature not reached']
                        self._mw_next(inst)
                continue
            ramping = mw['program'] == 'temperature' and abs(step['temperature_C'] + 273.15 - mw['s0']) > 1e-9 and state['run.step_elapsed_s'] < step['time_s']
            h = min(remaining, left, _MW_RAMP_SUBSTEP_S if ramping else left)
            target, power = self._mw_setpoints(inst, state['run.step_elapsed_s'] + h)
            state['thermal.target_k'], state['microwave.power_limit_w'] = target, power
            self._mw_sub(inst, h)
            remaining -= h
            if state['run.step_elapsed_s'] >= step['time_s'] - 1e-9:            # Time is reached: the next step starts at once
                self._mw_next(inst)
        if remaining > 1e-9:
            self._advance(state, remaining)
            mw['t'] += remaining

    def _mw_next(self, inst):
        state = inst['state']
        if state['run.step_index'] + 1 < len(state['run.steps']):
            self._mw_enter(inst, state['run.step_index'] + 1)
        else:
            self._mw_start_cooling(inst, False)

    def _mw_remaining(self, state):
        if state['run.status'] != 'running':
            return None
        i = state['run.step_index']
        return max(0.0, state['run.steps'][i]['time_s'] - state['run.step_elapsed_s']) + sum(s['time_s'] for s in state['run.steps'][i + 1:])

    def _mw_controller(self, inst):
        state, mw = inst['state'], inst.get('mw') or {}
        if state['run.status'] == 'cooling' and mw.get('user_break'):
            return 'U'
        if state['run.status'] != 'running':
            return '-'
        if mw['program'] == 'power':
            return 'T' if state['microwave.delivered_power_w'] < state['microwave.power_limit_w'] - 1e-9 else 'P'
        return 'T' if state['microwave.delivered_power_w'] < state['microwave.power_limit_w'] - 1e-9 else '-'

    @staticmethod
    def _mw_auto_logout(inst, t_end):
        """Logout settings (p.39): with auto logout on, the user is logged out after the set time without an operation (a run goes on)."""
        state = inst['state']
        sec = state.get('security.settings')
        if sec and sec['auto_logout'] and state['users.current'] is not None and \
                t_end - inst['mw'].get('last_activity_s', 0.0) >= sec['logout_timeout_min'] * 60.0 - 1e-9:
            state['users.current'] = None

    def _mw_ua(self, state):
        """Cavity heat-loss coefficient: calibrated value (fan level 1), door loss, and the active fan level of a method (UA x (1 + k (level - 1)))."""
        ua = MULTIWAVE_7101_LOSS_W_K + (0.0 if state['door.closed'] else MULTIWAVE_7101_OPEN_LOSS_W_K)
        fan = state.get('run.fan_level')
        return ua if fan is None else ua * (1.0 + _MW_FAN_K * (fan - 1))

    def _mw_pressure(self, state):
        """Cavity pressure reading: the water saturation pressure of the sealed vessels at the cavity temperature (atmospheric below 100 degC or open)."""
        if not state['door.closed']:
            return _MW_AMBIENT_PA
        psat = _multiwave_template_module().water_saturation_pressure_pa(state['thermal.temperature_k'])
        return max(_MW_AMBIENT_PA, psat or 0.0)

    @staticmethod
    def _mw_allowed(state, right):
        user = next((u for u in state['users.accounts'] if u['user_name'] == state['users.current']), None)
        return user is not None and user['active'] and any(right in _MW_ROLES.get(r, ()) for r in user['roles'])

    def _mw_observed(self, inst, sid):
        if sid == 'cavity.pressure_pa':
            return self._mw_pressure(inst['state'])
        if sid == 'run.remaining_s':
            return self._mw_remaining(inst['state'])
        if sid == 'run.controller_state':
            return self._mw_controller(inst)
        return copy.deepcopy(inst['state'][sid])

    # ---- the panel host
    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame), created on first use."""
        if instance_id not in self._mw_panels:
            if instance_id not in self._instances: raise ValueError('Unknown instance')
            p = _mw_panel()
            host = _G02PanelHost(p['engine'], lambda: self._mw_panel_read(instance_id),
                                 lambda cap, args: (self._panel_caller or self._panel_session)._checked(instance_id, self._instances[instance_id], cap, args, None),
                                 lambda: self.time_s)
            self._mw_panels[instance_id] = p['engine'].PanelUI(p['spec'], host)
        return self._mw_panels[instance_id]

    def _mw_panel_read(self, instance_id):
        inst = self._instances[instance_id]
        state = inst['state']
        slots = self._metadata['asset_types'][MULTIWAVE_7101_TYPE]['state_slots']
        out = {sid: self._mw_observed(inst, sid) for sid in slots if slots[sid]['visibility'] == 'agent'}
        sel = self._mw_method(state, state['methods.selected'])
        fmt = lambda s: None if s is None else f'{int(s) // 60:02d}:{int(s) % 60:02d}'           # noqa: E731

        def rows(steps, program, first=0):
            return [{'index': k, 'no': str(k + 1), 'value': (f"{s['temperature_C']:g} °C" if program == 'temperature' else f"{s['power_W']:g} W"),
                     'time': fmt(s['time_s']), 'type': {'time': 'Time is reached', 'temperature': 'Temperature is reached'}[s['step_type']] if program == 'temperature' else '',
                     'fan': str(s['fan']), 'editable': k >= first,
                     'raw': {'temperature_C': s['temperature_C'], 'power_W': s['power_W'], 'time_min': s['time_s'] / 60.0, 'step_type': s['step_type'], 'fan': s['fan']}}
                    for k, s in enumerate(steps or [])]
        run_program = (self._mw_method(state, state['run.method']) or {}).get('program', inst['mw'].get('program') or 'temperature')
        out.update({'host.powered': state['power.enabled'], 'host.power_epoch': inst['mw']['power_epoch'],
                    'host.reading_c': round(state['thermal.indicated_temperature_k'] - 273.15, 6), 'host.setpoint_c': round(state['thermal.target_k'] - 273.15, 6),
                    'host.power_w': state['microwave.delivered_power_w'], 'host.selected': sel, 'host.selected_steps': rows(sel['steps'], sel['program']) if sel else [],
                    'host.method_rows': [{'name': m['name'], 'program': m['program'].capitalize(), 'origin': 'pre-installed' if m['preinstalled'] else 'user'}
                                         for m in state['methods.library']],
                    'host.run_rows': rows(state['run.steps'], run_program, state['run.step_index'] or 0),
                    'host.step_text': None if state['run.step_index'] is None else f"{state['run.step_index'] + 1} / {len(state['run.steps'])}",
                    'host.elapsed': fmt(state['run.elapsed_s']), 'host.remaining': fmt(self._mw_remaining(state)),
                    'host.run_program': run_program, 'host.running': state['run.status'] in ('running', 'cooling'),
                    'host.records': [dict(r, label=f"{r['id']:03d}  {r['method']}  {r['start']}  {r['result']}") for r in reversed(state['data.experiments'])]})
        user = next((u for u in state['users.accounts'] if u['user_name'] == state['users.current']), None)
        trace = state['run.trace']
        out.update({'host.pressure_bar': round(self._mw_pressure(state) / 1e5, 6), 'host.user': user,
                    'host.user_text': '' if user is None else f"{user['initials']}  {user['full_name']} ({', '.join(user['roles'])})",
                    'host.login_rows': [u for u in state['users.accounts'] if u['active']],
                    'host.user_rows': [dict(u, roles_text=', '.join(u['roles']), state_text='active' if u['active'] else 'inactive') for u in state['users.accounts']],
                    'host.trace_temp': [[x[0] / 60.0, x[1]] for x in trace], 'host.trace_pressure': [[x[0] / 60.0, x[2] / 1e5] for x in trace],
                    'host.trace_span': max(10.0, (trace[-1][0] / 60.0) if trace else 0.0)})
        err = state.get('error.code')
        out['host.error'] = None if err is None else dict(zip(('kind', 'cleared_by', 'message', 'handling'), _MW_FAULTS[err][:4]), code=err)
        return out


class Multiwave7101Runtime(_MultiwaveTouch):
    """Trusted shared-clock host. Expose a session, never host/debug internals."""
    def __init__(self, *, ambient_temperature_K=MULTIWAVE_7101_AMBIENT_K, scene_adapter=None):
        if not _number(ambient_temperature_K) or not 273.15 <= ambient_temperature_K <= 313.15:
            raise ValueError('Ambient must be finite within the assumed273.15..313.15K envelope')
        if scene_adapter is not None and not callable(scene_adapter): raise ValueError('Scene adapter must be callable')
        self.ambient_temperature_K = float(ambient_temperature_K)
        self.scene_adapter, self.time_s = scene_adapter, 0.0
        self._instances, self._requests, self._scene_faults = {}, {}, []
        self._principals, self._logs = {'__public_agent__': ('agent', None)}, {'__public_agent__': []}
        self._metadata, self._lock = export_multiwave_7101_metadata(panel=False), threading.RLock()
        self._agent = Multiwave7101Session(self, '__public_agent__')
        self._mw_panels, self.panel_capture_dir = {}, None
        self._principals['__mw_panel__'], self._logs['__mw_panel__'] = ('agent', None), []
        self._panel_session = Multiwave7101Session(self, '__mw_panel__')   # the front panel's own path to the commands (not logged)
        self._panel_caller = None                                           # the session whose gesture runs: its commands act as that caller

    def _scene_payload(self, state):
        fields = ('door.angle_rad', 'rack.occupied_slots', 'thermal.indicated_temperature_k', 'thermal.target_k', 'power.enabled', 'microwave.enabled', 'microwave.delivered_power_w')
        return {sid: copy.deepcopy(state[sid]) for sid in fields}

    def create_instance(self, instance_id, asset_type_id=MULTIWAVE_7101_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != MULTIWAVE_7101_TYPE:
                raise ValueError('Unique nonempty instance ID and declared Multiwave7101 type required')
            state = {'power.enabled': False, 'thermal.target_k': self.ambient_temperature_K, 'thermal.temperature_k': self.ambient_temperature_K,
                'thermal.indicated_temperature_k': round(self.ambient_temperature_K - 273.15, 1) + 273.15,
                'microwave.enabled': False, 'microwave.power_limit_w': 1500.0, 'microwave.delivered_power_w': 0.0,
                'door.angle_rad': 0.0, 'door.closed': True, 'rack.occupied_slots': [False] * 24,
                'rack.slot_count': 24, 'rack.slot_capacity_m3': 8e-6, 'rack.pressure_limit_pa': None,
                'thermal.absorbed_energy_j': 0.0, 'thermal.environment_energy_j': 0.0, 'thermal.stored_energy_change_j': 0.0}
            state.update(self._mw_defaults())
            if self.scene_adapter: self.scene_adapter(instance_id, self._scene_payload(state))
            self._instances[instance_id] = {'state': state, 'revision': 0, 'mw': {'power_epoch': 0}}

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('Invalid or duplicate host identity')
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(sid, str) for sid in instance_ids)):
                raise ValueError('Identity scope must be an explicit collection of instance IDs')
            scope = None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope <= self._instances.keys(): raise ValueError('Unknown identity scope')
            self._principals[principal_id], self._logs[principal_id] = (visibility, scope), []

    def session(self, principal_id):
        with self._lock:
            if not isinstance(principal_id, str) or principal_id not in self._principals: raise ValueError('Host registration required')
            return Multiwave7101Session(self, principal_id)

    def describe(self, **kwargs): return self._agent.describe(**kwargs)
    def read(self, **kwargs): return self._agent.read(**kwargs)
    def invoke(self, **kwargs): return self._agent.invoke(**kwargs)
    def write(self, **kwargs): return self._agent.write(**kwargs)
    def describe_host(self): return copy.deepcopy(self._metadata['host_capabilities'])

    def debug_snapshot(self):
        with self._lock: return copy.deepcopy({'time_s': self.time_s, 'instances': self._instances, 'scene_faults': self._scene_faults})

    def sync_scene(self, instance_ids=None):
        with self._lock:
            if self.scene_adapter is None: raise RuntimeError('No scene adapter registered')
            ids = list(self._instances) if instance_ids is None else list(instance_ids)
            if any(sid not in self._instances for sid in ids): raise ValueError('Unknown instance')
            for sid in ids:
                try: self.scene_adapter(sid, self._scene_payload(self._instances[sid]['state']))
                except Exception: raise SceneSynchronizationError('Scene synchronization failed; scene may have partial effects') from None

    def _delivered(self, state):
        if not state['power.enabled'] or not state['microwave.enabled'] or not state['door.closed']: return 0.0
        t, target, p = state['thermal.temperature_k'], state['thermal.target_k'], state['microwave.power_limit_w']
        if t > target + 1e-10: return 0.0
        if t < target - 1e-10: return p
        return min(p, self._mw_ua(state) * max(0.0, target - self.ambient_temperature_K))

    def _advance(self, state, dt):
        ambient, c = self.ambient_temperature_K, MULTIWAVE_7101_CAPACITY_J_K
        ua = self._mw_ua(state)
        old, target = state['thermal.temperature_k'], state['thermal.target_k']
        p = state['microwave.power_limit_w'] if state['power.enabled'] and state['microwave.enabled'] and state['door.closed'] else 0.0
        current, remaining, absorbed = old, dt, 0.0
        if current > target:
            crossing = c / ua * math.log((current - ambient) / (target - ambient)) if target > ambient else math.inf
            h = min(remaining, crossing)
            current = ambient + (current - ambient) * math.exp(-ua * h / c)
            remaining -= h
            if crossing <= h: current = target
        if remaining > 0:
            equilibrium = ambient + p / ua
            if p > 0 and equilibrium >= target and current <= target:
                crossing = 0.0 if current == target else (c / ua * math.log((equilibrium - current) / (equilibrium - target)) if equilibrium > target else math.inf)
                h = min(remaining, crossing)
                current = equilibrium + (current - equilibrium) * math.exp(-ua * h / c)
                absorbed += p * h
                if crossing <= h:
                    current = target
                    absorbed += ua * (target - ambient) * (remaining - h)
            else:
                current = equilibrium + (current - equilibrium) * math.exp(-ua * remaining / c)
                absorbed += p * remaining
        current = min(MULTIWAVE_7101_MAX_K, max(ambient, current))
        delta_energy = c * (current - old)
        state['thermal.temperature_k'] = current
        state['thermal.indicated_temperature_k'] = round(current - 273.15, 1) + 273.15
        state['thermal.absorbed_energy_j'] += absorbed
        state['thermal.environment_energy_j'] += absorbed - delta_energy
        state['thermal.stored_energy_change_j'] = c * (current - ambient)
        state['microwave.delivered_power_w'] = self._delivered(state)

    def step(self, dt_s):
        if not _number(dt_s) or not 0 <= dt_s <= 86400: raise ValueError('Host step must be finite within0..86400s')
        if dt_s == 0: return
        with self._lock:
            for inst in self._instances.values():
                self._mw_auto_logout(inst, self.time_s + float(dt_s))
                if inst['state']['run.status'] in ('running', 'cooling'):   # a method started on the touchscreen (G02 panel)
                    inst['mw']['t'] = self.time_s
                    self._mw_run_advance(inst, float(dt_s))
                    inst['revision'] += 1
                    continue
                before = copy.deepcopy(inst['state']) if inst.get('template_digestion') else None  # 2026-10 template hook
                self._advance(inst['state'], float(dt_s))
                if before is not None: _multiwave_template_module().track_step(self._advance, inst, before, self.time_s, float(dt_s))
                inst['revision'] += 1
            self.time_s += float(dt_s)
            if self.scene_adapter:
                failed = []
                for sid, inst in self._instances.items():
                    try: self.scene_adapter(sid, self._scene_payload(inst['state']))
                    except Exception:
                        failed.append(sid)
                        self._scene_faults.append({'instance_id': sid, 'time_s': self.time_s, 'code': 'SCENE_SYNCHRONIZATION_FAILED', 'side_effects': 'partial_or_unknown'})
                if failed: raise SceneSynchronizationError('Shared clock/process advanced; scene synchronization failed with possible partial effects') from None


class Multiwave7101Session:
    """Capability-limited interface; caller cannot supply a role or clock step."""
    def __init__(self, host, principal_id): self.__host, self.__principal = host, principal_id
    def _visible(self, info): return info['visibility'] == 'agent' or self.__host._principals[self.__principal][0] in ('debug', 'evaluator')
    def _instance(self, sid):
        if not isinstance(sid, str): return None
        scope = self.__host._principals[self.__principal][1]
        return self.__host._instances.get(sid) if scope is None or sid in scope else None
    def _envelope(self, sid, request_id=None, *, result=None, observations=None, status='succeeded', error=None):
        inst = self._instance(sid)
        return {'protocol_version': PROTOCOL_VERSION, 'instance_id': sid if isinstance(sid, str) else None, 'request_id': request_id if isinstance(request_id, str) else None,
            'operation_id': None, 'status': status, 'state_revision': inst['revision'] if inst else None, 'clock': {'domain': 'simulation', 'time_s': self.__host.time_s},
            'result': copy.deepcopy(result or {}), 'observations': copy.deepcopy(observations or {}), 'error': error}
    def _error(self, sid, code, request_id=None, status='rejected'):
        messages = {'UNKNOWN_INSTANCE': 'Instance unavailable.', 'UNKNOWN_CAPABILITY': 'Capability unavailable.', 'INVALID_ARGUMENT': 'Arguments violate schema or bounds.',
            'ACCESS_DENIED': 'Request outside session policy.', 'PRECONDITION_FAILED': 'Required simulation state unavailable.', 'REQUEST_CONFLICT': 'Request ID belongs to different arguments.',
            'UNSUPPORTED': 'Execution unavailable in this adapter.', 'DEVICE_ERROR': 'Scene callback did not complete command.'}
        return self._envelope(sid, request_id, status=status, error={'code': code, 'message': messages[code], 'retryable': False, 'side_effects': 'unknown' if code == 'DEVICE_ERROR' else 'none', 'progress': None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        host = self.__host
        with host._lock:
            if (asset_type_id is None) == (instance_id is None): return self._error(None, 'INVALID_ARGUMENT')
            if instance_id is not None and not self._instance(instance_id): return self._error(instance_id, 'UNKNOWN_INSTANCE')
            if asset_type_id is not None and asset_type_id != MULTIWAVE_7101_TYPE: return self._error(None, 'INVALID_ARGUMENT')
            if host._principals[self.__principal][1] == frozenset(): return self._error(instance_id, 'ACCESS_DENIED')
            spec = copy.deepcopy(export_multiwave_7101_metadata()['asset_types'][MULTIWAVE_7101_TYPE])
            spec['state_slots'] = {sid: info for sid, info in spec['state_slots'].items() if self._visible(info)}
            spec['capabilities'].update(spec.pop('declared_capabilities'))
            for group in ('controller_capabilities', 'evaluator_capabilities', 'panel_capabilities'):
                spec['capabilities'].update({k: v for k, v in spec.pop(group, {}).items() if self._visible(v)})
            for cap in spec['capabilities'].values():
                cap['state_effects'] = [e for e in cap['state_effects'] if e['state_id'] in spec['state_slots']]
                cap['observes'] = [sid for sid in cap['observes'] if sid in spec['state_slots']]
                if cap.get('scene_backend_required') and host.scene_adapter is None: cap['availability'] = 'unsupported'
            return {'protocol_version': PROTOCOL_VERSION, 'asset_type_id': MULTIWAVE_7101_TYPE, 'instance_id': instance_id,
                'adapter': 'basic40_runtime_thermal.Multiwave7101Session', **spec, 'limitations': copy.deepcopy(host._metadata['limitations'])}

    def read(self, *, instance_id, state_ids=None):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, 'UNKNOWN_INSTANCE')
            slots = host._metadata['asset_types'][MULTIWAVE_7101_TYPE]['state_slots']
            if state_ids is None: state_ids = [sid for sid, info in slots.items() if self._visible(info)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids) or len(set(state_ids)) != len(state_ids): return self._error(instance_id, 'INVALID_ARGUMENT')
            if any(sid not in slots or not self._visible(slots[sid]) for sid in state_ids): return self._error(instance_id, 'ACCESS_DENIED')
            values = {sid: host._mw_observed(inst, sid) for sid in state_ids}
            obs = {sid: {'value': values[sid], 'unit': slots[sid]['unit'], 'quality': 'unavailable' if values[sid] is None else ('estimated' if slots[sid]['origin'] == 'derived' else 'valid'), 'sample_time_s': host.time_s, 'sample_window_s': None} for sid in state_ids}
            return self._envelope(instance_id, observations=obs)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id or len(request_id) > 256:
                return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            try: digest = hashlib.sha256(json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError): return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            key = (self.__principal, instance_id, request_id)
            if key in host._requests:
                old_digest, response = host._requests[key]
                return copy.deepcopy(response) if digest == old_digest else self._error(instance_id, 'REQUEST_CONFLICT', request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, copy.deepcopy(response))
            host._logs[self.__principal].append({'instance_id': instance_id, 'capability_id': capability_id, 'request_id': request_id, 'response': copy.deepcopy(response)})
            return response

    def _checked(self, sid, inst, cap, args, request_id):
        """Run ownership (lead rule 2026-10-19): the caller that starts a method owns the run; another caller cannot STOP it, edit its steps
        while it runs or cools.  The mains switch is a hard switch (physical action, lead clarification 2026-10-19): power.set follows it and is
        never refused; switching off ends the experiment as a power loss."""
        active = inst['state'].get('run.status') in ('running', 'cooling')
        guarded = cap in ('run.abort', 'run.set_step', 'run.add_step', 'run.delete_step')
        if guarded and _g02_foreign_run(self.__host, self.__principal, inst.get('run_owner'), active):
            return self._error(sid, 'ACCESS_DENIED', request_id)
        response = self._checked_body(sid, inst, cap, args, request_id)
        if cap == 'run.start' and response['status'] == 'succeeded' and not active:
            inst['run_owner'] = self.__principal
        return response

    def _checked_body(self, sid, inst, cap, args, request_id):
        host, old = self.__host, inst['state']
        spec = host._metadata['asset_types'][MULTIWAVE_7101_TYPE]
        fail = lambda code, status='rejected': self._error(sid, code, request_id, status)
        if isinstance(cap, str) and cap.startswith('panel.'): return self._panel_command(sid, cap, args, request_id)
        if cap in spec['controller_capabilities']: return self._mw_command(sid, inst, cap, args, request_id)
        if cap in spec.get('evaluator_capabilities', {}): return self._mw_fault(sid, inst, cap, args, request_id)
        if cap in spec['declared_capabilities']: return fail('UNSUPPORTED', 'unsupported')
        if cap not in spec['capabilities']: return fail('UNKNOWN_CAPABILITY')
        if set(args) != set(spec['capabilities'][cap]['input_schema']['properties']): return fail('INVALID_ARGUMENT')
        if cap in _MULTIWAVE_TEMPLATE_CAPS: return self._template_checked(sid, inst, cap, args, request_id)  # 2026-10 template hook
        if cap in ('power.set', 'microwave.set_enabled') and type(args['enabled']) is not bool: return fail('INVALID_ARGUMENT')
        if cap == 'thermal.set_temperature' and (not _number(args['temperature_K']) or not host.ambient_temperature_K <= args['temperature_K'] <= MULTIWAVE_7101_MAX_K): return fail('INVALID_ARGUMENT')
        if cap == 'microwave.set_power' and (not _number(args['power_W']) or not 0 <= args['power_W'] <= 1500): return fail('INVALID_ARGUMENT')
        if cap == 'rack.set_slot' and (type(args['slot_index']) is not int or not 0 <= args['slot_index'] <= 23 or type(args['occupied']) is not bool): return fail('INVALID_ARGUMENT')
        if cap in ('thermal.set_temperature', 'microwave.set_power') and not old['power.enabled']: return fail('PRECONDITION_FAILED')
        if cap == 'microwave.set_enabled' and args['enabled'] and not (old['power.enabled'] and old['door.closed']): return fail('PRECONDITION_FAILED')
        if cap == 'microwave.set_enabled' and args['enabled'] and old.get('error.code') is not None: return fail('PRECONDITION_FAILED')   # error shown or cause persists
        if spec['capabilities'][cap]['scene_backend_required'] and host.scene_adapter is None: return fail('UNSUPPORTED', 'unsupported')
        if cap == 'rack.set_slot' and (old['door.closed'] or old['microwave.enabled']): return fail('PRECONDITION_FAILED')
        running = old['run.status'] in ('running', 'cooling')                 # a touchscreen method owns setpoint, power and the locked door
        if running and cap in ('thermal.set_temperature', 'microwave.set_power', 'microwave.set_enabled', 'door.open'): return fail('PRECONDITION_FAILED')
        state = copy.deepcopy(old)
        mw = copy.deepcopy(inst['mw'])
        if cap == 'power.set' and args['enabled'] != old['power.enabled'] and 'users.current' in state:
            mw['power_epoch'] += 1
            if running and not args['enabled']:                               # power off ends the experiment
                host._mw_finish(dict(inst, state=state, mw=mw), 'aborted (power off)')
                state.update({'run.status': 'idle', 'run.step_index': None, 'run.step_elapsed_s': None, 'thermal.target_k': host.ambient_temperature_K,
                              'run.fan_level': None})
            sec = state['security.settings']                                   # switch-on: the auto login user (default administrator, p.38)
            auto = next((u for u in state['users.accounts'] if u['user_name'] == sec['auto_login_user'] and u['active']), None)
            state['users.current'] = auto['user_name'] if args['enabled'] and sec['auto_login'] and auto else None
            mw['last_activity_s'] = host.time_s
            if not args['enabled']:                                           # 'Restart the instrument' (Appendix A): switching off removes restart causes
                state['fault.injected'] = [c for c in state['fault.injected'] if _MW_FAULTS[c][1] != 'restart']
                state['error.code'], state['error.popup'] = None, False
            elif state['fault.injected']:                                     # a persisting cause shows its error again at switch-on
                state['error.code'], state['error.popup'] = state['fault.injected'][0], True
        elif cap == 'power.set' and args['enabled'] != old['power.enabled']:
            mw['power_epoch'] += 1
        if cap == 'door.open' and old['run.status'] == 'finished': state['run.status'] = 'idle'
        if cap == 'power.set':
            state['power.enabled'] = args['enabled']
            if not args['enabled']: state['microwave.enabled'] = False
            state['microwave.delivered_power_w'] = host._delivered(state)
            result = {'enabled': args['enabled']}
        elif cap == 'thermal.set_temperature':
            state['thermal.target_k'] = float(args['temperature_K'])
            result = {'setpoint_K': state['thermal.target_k']}
        elif cap == 'microwave.set_enabled':
            state['microwave.enabled'] = args['enabled']
            state['microwave.delivered_power_w'] = host._delivered(state)
            result = {'enabled': args['enabled']}
        elif cap == 'microwave.set_power':
            state['microwave.power_limit_w'] = float(args['power_W'])
            state['microwave.delivered_power_w'] = host._delivered(state)
            result = {'power_W': state['microwave.power_limit_w']}
        elif cap.startswith('door.'):
            closed = cap == 'door.close'
            state['door.angle_rad'], state['door.closed'] = (0.0 if closed else math.pi / 2), closed
            if not closed:
                state['microwave.enabled'] = False
                state['microwave.delivered_power_w'] = 0.0
            result = {'angle_rad': state['door.angle_rad'], 'closed': closed}
        else:
            state['rack.occupied_slots'][args['slot_index']] = args['occupied']
            result = {'slot_index': args['slot_index'], 'occupied': args['occupied']}
        if state != old:
            if host.scene_adapter:
                try: host.scene_adapter(sid, host._scene_payload(state))
                except Exception: return fail('DEVICE_ERROR', 'failed')
            inst['state'], inst['revision'] = state, inst['revision'] + 1
        inst['mw'] = mw
        return self._envelope(sid, request_id, result=result)

    def _panel_command(self, sid, cap, args, request_id):
        """panel.* commands: a gesture on the instance's front panel (engine + panel_ui.json), a screen capture, or the evaluator frame."""
        host, panel = self.__host, _mw_panel()
        if cap not in panel['caps']:
            return self._error(sid, 'UNKNOWN_CAPABILITY', request_id)
        if not self._visible(panel['caps'][cap]):
            return self._error(sid, 'ACCESS_DENIED', request_id)
        if cap not in ('panel.frame', 'panel.capture'):
            host._instances[sid]['mw']['last_activity_s'] = host.time_s               # a touch counts as activity for the auto logout
        host._panel_caller = self                                             # a gesture acts as the session that makes it (access rules)
        try:
            result, error = _g02_panel_command(panel, host.panel(sid), cap, args, instance_id=sid, time_s=host.time_s, capture_dir=host.panel_capture_dir)
        finally:
            host._panel_caller = None
        return self._error(sid, error, request_id) if error else self._envelope(sid, request_id, result=result)

    def _mw_fault(self, sid, inst, cap, args, request_id):
        """Evaluator-only fault.inject / fault.clear of the Appendix A codes (sweep 2026-10-19): on a candidate copy, one committed update."""
        host = self.__host
        fail = lambda code, status='rejected': self._error(sid, code, request_id, status)   # noqa: E731
        info = host._metadata['asset_types'][MULTIWAVE_7101_TYPE]['evaluator_capabilities'][cap]
        if not self._visible(info):
            return fail('ACCESS_DENIED')
        if not isinstance(args, dict) or _g02_engine().check_arguments(info['input_schema'], args):
            return fail('INVALID_ARGUMENT')
        code, old = args['code'], inst['state']
        state, mw = copy.deepcopy(old), copy.deepcopy(inst['mw'])
        if cap == 'fault.inject':
            if _MW_FAULTS[code][1] == 'ack' and not state['power.enabled']:
                return fail('PRECONDITION_FAILED')                                   # a run-related error needs the instrument on
            if state['power.enabled']:
                host._mw_raise(dict(inst, state=state, mw=mw), code)
            elif code not in state['fault.injected']:
                state['fault.injected'] = state['fault.injected'] + [code]          # shown at the next switch-on
        else:
            if code not in state['fault.injected']:
                return fail('PRECONDITION_FAILED')
            state['fault.injected'] = [c for c in state['fault.injected'] if c != code]
            if not state['error.popup']:
                state['error.code'] = host._mw_error_after(state) if state['power.enabled'] else None
        if host.scene_adapter and host._scene_payload(state) != host._scene_payload(old):
            try: host.scene_adapter(sid, host._scene_payload(state))
            except Exception: return fail('DEVICE_ERROR', 'failed')
        inst['state'], inst['mw'], inst['revision'] = state, mw, inst['revision'] + 1
        return self._envelope(sid, request_id, result={'code': code})

    def _mw_command(self, sid, inst, cap, args, request_id):
        """The touchscreen functions: schema and state checks on a candidate copy, then one committed update (scene callback first)."""
        host = self.__host
        fail = lambda code, status='rejected': self._error(sid, code, request_id, status)   # noqa: E731
        info = host._metadata['asset_types'][MULTIWAVE_7101_TYPE]['controller_capabilities'][cap]
        if not isinstance(args, dict) or _g02_engine().check_arguments(info['input_schema'], args):
            return fail('INVALID_ARGUMENT')
        old = inst['state']
        if not old['power.enabled']:
            return fail('PRECONDITION_FAILED')
        if cap in _MW_RIGHTS and not host._mw_allowed(old, _MW_RIGHTS[cap]):
            return fail('PRECONDITION_FAILED')                                       # not logged in, or the role lacks the permission (p.38-39)
        state, mw = copy.deepcopy(old), copy.deepcopy(inst['mw'])
        cand = dict(inst, state=state, mw=mw)
        busy = state['run.status'] in ('running', 'cooling')
        result = copy.deepcopy(args)
        method = host._mw_method(state, args.get('name')) if 'name' in args else None

        def step_of(program, a, base=None):
            """A step from the arguments: the value of the program type only; step type only for temperature programs."""
            if program == 'temperature':
                if 'temperature_C' not in a or 'power_W' in a: return None
                return {'temperature_C': float(a['temperature_C']), 'power_W': None, 'time_s': float(a['time_s']), 'step_type': a.get('step_type', 'time'), 'fan': a['fan']}
            if 'power_W' not in a or 'temperature_C' in a or a.get('step_type', 'time') != 'time': return None
            return {'temperature_C': None, 'power_W': float(a['power_W']), 'time_s': float(a['time_s']), 'step_type': 'time', 'fan': a['fan']}
        if cap.startswith('methods.'):
            if busy: return fail('PRECONDITION_FAILED')
            if cap == 'methods.create':
                name = args['name'].strip()
                if not name or len(name) > 40 or host._mw_method(state, name) is not None: return fail('INVALID_ARGUMENT')
                prog = args['program']
                first = {'temperature_C': 100.0, 'power_W': None, 'time_s': 600.0, 'step_type': 'time', 'fan': 1} if prog == 'temperature' else \
                    {'temperature_C': None, 'power_W': 500.0, 'time_s': 600.0, 'step_type': 'time', 'fan': 1}
                state['methods.library'].append({'name': name, 'note': '', 'application': 'Acid digestion', 'program': prog, 'max_power_W': float(args['max_power_W']),
                                                 'max_temperature_C': float(args['max_temperature_C']), 'preinstalled': False, 'template_id': None, 'steps': [first]})
                state['methods.selected'] = name
                result = {'name': name}
            elif method is None:
                return fail('PRECONDITION_FAILED')
            elif cap == 'methods.select':
                state['methods.selected'] = method['name']
            elif cap == 'methods.copy':
                stamp = host._mw_clock_text(host.time_s).replace('-', '').replace(' ', '_').replace(':', '')
                name = f"{method['name']} {stamp}"
                k = 2
                while host._mw_method(state, name) is not None:
                    name, k = f"{method['name']} {stamp}_{k}", k + 1
                state['methods.library'].append(dict(copy.deepcopy(method), name=name, preinstalled=False))
                state['methods.selected'] = name
                result = {'name': name}
            elif method['preinstalled']:
                return fail('PRECONDITION_FAILED')                                   # pre-installed methods can neither be modified nor deleted (p.48)
            elif cap == 'methods.delete':
                state['methods.library'] = [m for m in state['methods.library'] if m['name'] != method['name']]
                if state['methods.selected'] == method['name']:
                    state['methods.selected'] = state['methods.library'][0]['name']
            else:
                m = host._mw_method(state, method['name'])
                if cap == 'methods.add_step':
                    if len(m['steps']) >= _MW_MAX_STEPS: return fail('PRECONDITION_FAILED')
                    m['steps'].append(copy.deepcopy(m['steps'][-1]))
                    result = {'name': m['name'], 'count': len(m['steps'])}
                elif cap == 'methods.delete_step':
                    if args['index'] >= len(m['steps']) or len(m['steps']) == 1: return fail('PRECONDITION_FAILED')
                    del m['steps'][args['index']]
                    result = {'name': m['name'], 'count': len(m['steps'])}
                else:
                    new = step_of(m['program'], args)
                    if new is None or args['index'] >= len(m['steps']): return fail('INVALID_ARGUMENT' if new is None else 'PRECONDITION_FAILED')
                    if (new['temperature_C'] or 0) > m['max_temperature_C'] or (new['power_W'] or 0) > m['max_power_W']: return fail('INVALID_ARGUMENT')
                    m['steps'][args['index']] = new
                    result = {'name': m['name'], 'index': args['index']}
        elif cap == 'run.start':
            m = host._mw_method(state, state['methods.selected'])
            if busy or m is None or not state['door.closed'] or state['error.code'] is not None: return fail('PRECONDITION_FAILED')
            mw.update(program=m['program'], limit_k=m['max_temperature_C'] + 273.15, power_W=m['max_power_W'], id=(state['data.experiments'][-1]['id'] + 1) if state['data.experiments'] else 1,
                      start=host.time_s, t=host.time_s, max_k=state['thermal.indicated_temperature_k'], user_break=False, s0=None, max_pa=None, next_trace_s=0.0,
                      error_code=None)
            state.update({'run.status': 'running', 'run.method': m['name'], 'run.steps': copy.deepcopy(m['steps']), 'run.elapsed_s': 0.0, 'run.warnings': [],
                          'microwave.enabled': True, 'run.trace': []})
            host._mw_enter(cand, 0)
            target, power = host._mw_setpoints(cand, 0.0)
            state['thermal.target_k'], state['microwave.power_limit_w'] = target, power
            state['microwave.delivered_power_w'] = host._delivered(state)
            result = {'method': m['name']}
        elif cap == 'run.abort':
            if state['run.status'] != 'running': return fail('PRECONDITION_FAILED')
            host._mw_start_cooling(cand, True)
            result = {'status': 'cooling'}
        elif cap == 'error.acknowledge':
            if not state['error.popup']: return fail('PRECONDITION_FAILED')
            result = {'code': state['error.code']}
            state['error.popup'], state['error.code'] = False, host._mw_error_after(state)
        elif cap == 'settings.configure':
            state['settings.brightness_percent'] = float(args['brightness_percent'])
        elif cap in ('user.login', 'user.logout', 'user.change_password', 'security.configure', 'users.add', 'users.set_active', 'users.remove'):
            sec, accounts, passwords = state['security.settings'], state['users.accounts'], state['users.passwords']
            account = next((u for u in accounts if u['user_name'] == args.get('user_name')), None)
            me = state['users.current']
            if cap == 'user.login':
                if account is None or not account['active']: return fail('PRECONDITION_FAILED')
                if sec['password_required'] and args.get('password') != passwords.get(account['user_name']): return fail('PRECONDITION_FAILED')
                state['users.current'] = account['user_name']
                result = {'user_name': account['user_name']}
            elif cap == 'user.logout':
                state['users.current'] = None                                         # operations already started go on (p.38)
                result = {'user_name': me}
            elif cap == 'user.change_password':
                if me is None or args['old_password'] != passwords.get(me, ''): return fail('PRECONDITION_FAILED')
                if sec['password_required'] and len(args['new_password']) < sec['password_min_length']: return fail('INVALID_ARGUMENT')
                passwords[me] = args['new_password']
                result = {'user_name': me}
            elif cap == 'security.configure':
                if not args: return fail('INVALID_ARGUMENT')
                new = dict(sec, **args)
                target = next((u for u in accounts if u['user_name'] == new['auto_login_user']), None)
                if target is None: return fail('INVALID_ARGUMENT')
                state['security.settings'] = new
                result = {'ok': True}
            elif cap == 'users.add':
                name = args['user_name'].strip()
                if (len(name) < sec['user_name_min_length'] or account is not None or name in mw.get('removed_users', [])
                        or not args['full_name'].strip()): return fail('INVALID_ARGUMENT')   # unique, never reused after removal (p.40)
                pw = args.get('password', '')
                if sec['password_required'] and len(pw) < sec['password_min_length']: return fail('INVALID_ARGUMENT')
                initials = ''.join(w[0] for w in args['full_name'].split()).upper()[:3]
                accounts.append({'user_name': name, 'full_name': args['full_name'].strip(), 'roles': [args['role']], 'active': True, 'initials': initials})
                passwords[name] = pw
                result = {'user_name': name}
            else:
                if account is None or account['user_name'] == me: return fail('PRECONDITION_FAILED')   # never lock yourself out (p.40)
                if cap == 'users.set_active':
                    account['active'] = args['active']
                else:
                    state['users.accounts'] = [u for u in accounts if u['user_name'] != account['user_name']]
                    passwords.pop(account['user_name'], None)
                    mw['removed_users'] = mw.get('removed_users', []) + [account['user_name']]
                if not any(u['active'] and 'administrator' in u['roles'] for u in state['users.accounts']):
                    return fail('PRECONDITION_FAILED')                               # keep one active administrator (no backdoor, p.39)
                result = {'user_name': account['user_name']}
        else:                                                                         # Step Editing during a run (p.51)
            if state['run.status'] != 'running': return fail('PRECONDITION_FAILED')
            steps, i = state['run.steps'], state['run.step_index']
            if cap == 'run.add_step':
                if len(steps) >= _MW_MAX_STEPS: return fail('PRECONDITION_FAILED')
                steps.append(copy.deepcopy(steps[-1]))
                result = {'count': len(steps)}
            elif cap == 'run.delete_step':
                if not i < args['index'] < len(steps): return fail('PRECONDITION_FAILED')
                del steps[args['index']]
                result = {'count': len(steps)}
            else:
                new = step_of(mw['program'], args)
                if new is None: return fail('INVALID_ARGUMENT')
                if not i <= args['index'] < len(steps): return fail('PRECONDITION_FAILED')
                if (new['temperature_C'] or 0) + 273.15 > mw['limit_k'] + 1e-9 and new['temperature_C'] is not None or (new['power_W'] or 0) > mw['power_W']: return fail('INVALID_ARGUMENT')
                steps[args['index']] = new
                if args['index'] == i:                                               # the current step: its setpoint follows at once
                    target, power = host._mw_setpoints(cand, state['run.step_elapsed_s'])
                    state['thermal.target_k'], state['microwave.power_limit_w'] = target, power
                    state['microwave.delivered_power_w'] = host._delivered(state)
                result = {'index': args['index']}
        mw['last_activity_s'] = host.time_s                                          # auto logout counts from the last operation
        if state != old or mw != inst['mw']:
            if host.scene_adapter and host._scene_payload(state) != host._scene_payload(old):
                try: host.scene_adapter(sid, host._scene_payload(state))
                except Exception: return fail('DEVICE_ERROR', 'failed')
            inst['state'], inst['mw'], inst['revision'] = state, mw, inst['revision'] + 1
        return self._envelope(sid, request_id, result=result)

    def _template_checked(self, sid, inst, cap, args, request_id):
        """2026-10 template hook: delegate to scripts/basic40_templates/multiwave_7101.py; rejections leave state unchanged."""
        module = _multiwave_template_module()
        try:
            result, contents = module.handle(cap, args, inst['state'], inst.get(module.STATE_KEY))
        except module.Rejected as exc:
            if exc.code in ('INVALID_ARGUMENT', 'PRECONDITION_FAILED', 'UNKNOWN_CAPABILITY'): return self._error(sid, exc.code, request_id)
            return self._envelope(sid, request_id, status='rejected', error={'code': exc.code, 'message': module.ERRORS[exc.code], 'retryable': False, 'side_effects': 'none', 'progress': None})
        if contents is not None:
            inst[module.STATE_KEY], inst['revision'] = contents, inst['revision'] + 1
        return self._envelope(sid, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if not self._instance(instance_id): return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            slots = self.__host._metadata['asset_types'][MULTIWAVE_7101_TYPE]['state_slots']
            slot = slots.get(state_id) if isinstance(state_id, str) else None
            if slot is None or not self._visible(slot) or slot['access'] != 'read_write': return self._error(instance_id, 'ACCESS_DENIED', request_id)
            route = slot['write_via']
            return self.invoke(instance_id=instance_id, capability_id=route['capability_id'], arguments={route['parameter']: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock: return copy.deepcopy(self.__host._logs[self.__principal])
# END CHEM-047 Anton Paar Multiwave 7101 runtime.


def validate_miele_plw6111_runtime(output_directory=None):
    """Run this asset's actual tests and emit reproducible, layer-specific evidence."""
    import sys as _miele_sys
    import time as _miele_time
    root = Path(__file__).resolve().parents[1]
    for entry in (root / 'scripts', root / 'tests', root / 'skills/chemistry-lab-assets/scripts'):
        if str(entry) not in _miele_sys.path: _miele_sys.path.insert(0, str(entry))
    import test_basic40_thermal as _miele_test_module
    stream = io.StringIO()
    passed = []
    class MieleEvidenceResult(unittest.TextTestResult):
        def addSuccess(self, test):
            passed.append(test.id().split('.')[-1])
            super().addSuccess(test)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(_miele_test_module.MielePLW6111RuntimeTests)
    start = _miele_time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=MieleEvidenceResult).run(suite)
    metadata = export_miele_plw6111_metadata()
    all_caps = list(metadata['asset_types'][MIELE_PLW6111_TYPE]['capabilities'])
    checks = []
    for name in passed:
        if any(token in name for token in ('truth_filter', 'double_instance', 'request_replay', 'invalid_step', 'time_partition')): layer = 'host'
        else: layer = 'process'
        if 'three_basket' in name or 'scene_backend' in name: caps = ['door.open', 'door.close', 'basket.set_position']
        elif 'set_temperature' in name: caps = ['thermal.set_temperature']
        elif any(token in name for token in ('phase_order', 'loss_analytic', '7500w', 'zero_dry', 'unreachable', 'unknown_water')): caps = ['washing.start', 'washing.configure_program']
        elif 'stop_retains' in name: caps = ['washing.stop', 'power.set']
        else: caps = all_caps
        checks.append({'id': name, 'layer': layer, 'capability_ids': caps, 'status': 'passed', 'method': 'unittest test_basic40_thermal.MielePLW6111RuntimeTests.' + name,
            'note': 'Executed assertion-based synthetic test; scene callbacks here are test doubles, not Blender pose verification. No manufacturer calibration or hardware validation claimed.'})
    report = {'catalog_id': 'CHEM-191', 'asset_type_id': MIELE_PLW6111_TYPE, 'status': 'passed' if result.wasSuccessful() else 'failed',
        'checked_at': datetime.now(timezone.utc).isoformat(), 'environment': {'python': platform.python_version(), 'platform': platform.platform(), 'bytecode_disabled': bool(_miele_sys.dont_write_bytecode), 'runtime': 'CPython synthetic host runtime; scene test doubles'},
        'tests_run': result.testsRun, 'elapsed_s': _miele_time.perf_counter() - start, 'failures': [{'test': str(test), 'detail': detail} for test, detail in result.failures + result.errors], 'checks': checks,
        'implementation_validation': {'process': 'passed', 'host': 'passed', 'scene': 'not_run', 'robot': 'not_run', 'hardware': 'not_run'},
        'source_sha256': {'scripts/basic40_runtime_thermal.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'tests/test_basic40_thermal.py': hashlib.sha256((root / 'tests/test_basic40_thermal.py').read_bytes()).hexdigest()},
        'synthetic_heatup_reference': {'evidence': 'assumption, only used in synthetic tests; not a manufacturer default or calibrated performance', 'effective_water_mass_kg': 10, 'water_specific_heat_J_kg_K': 4180, 'effective_heat_capacity_J_K': 41800, 'heat_loss_W_K': 0, 'initial_temperature_K': 293.15, 'target_temperature_K': 333.15, 'water_heater_power_W': 7500, 'predicted_heatup_s': 41800 * 40 / 7500, 'formula': 't=C*(Ttarget-Tinitial)/P when UA=0'},
        'manufacturer_comparison': 'not_run: exact product heater rating is known, but manufacturer water charge, heat-up time and loss calibration are unavailable; no claim of matching cycle duration or heating performance.',
        'boundaries': metadata['limitations']}
    if output_directory is not None:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        (output / 'runtime_validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        (output / 'runtime_test_log.txt').write_text(stream.getvalue())
        (output / 'runtime_metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    return report

# BEGIN CHEM-018 Parr 4591 runtime (append-only asset scope).
PARR_4591_TYPE = 'instrument.parr_4591'
PARR_4591_POWER_W = 700.0
PARR_4591_MAX_K = 498.15
PARR_4591_AMBIENT_PA = 101325.0  # Standard atmosphere assumed by this simulation.
PARR_4591_MAX_GAUGE_PA = 20700000.0
PARR_4591_MAX_ABSOLUTE_PA = PARR_4591_MAX_GAUGE_PA + PARR_4591_AMBIENT_PA
PARR_4591_VOLUME_M3 = 25e-6
PARR_4591_MAX_SPEED_RAD_S = 1700 * 2 * math.pi / 60
PARR_4591_CAPACITY_J_K = 1000.0  # Effective-node assumption; NOT measured factory heat capacity.
PARR_4591_LOSS_W_K = 0.7  # Assumed 20% of 700 W lost at a 200 K temperature excess.
PARR_4591_GAS_R = 8.31446261815324


def export_parr_4591_metadata():
    n, obj = _multiwave_number_schema, _multiwave_object
    boolean = {'type': 'boolean'}
    def slot(unit, origin, schema, route=None, private=False):
        return {'unit': unit, 'origin': origin, 'visibility': 'debug' if private else 'agent',
            'value_schema': schema, 'access': 'read_write' if route else 'read_only', 'write_via': route,
            'reference_conditions': None, 'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None}}
    def route(cap, param): return {'capability_id': cap, 'parameter': param}
    slots = {
        'power.enabled': slot('1', 'setpoint', boolean, route('power.set', 'enabled')),
        'thermal.target_k': slot('K', 'setpoint', n('K', 273.15, PARR_4591_MAX_K), route('thermal.set_temperature', 'temperature_K')),
        'thermal.temperature_k': slot('K', 'process_truth', n('K'), private=True),
        'thermal.indicated_temperature_k': slot('K', 'derived', n('K')),
        'heater.enabled': slot('1', 'setpoint', boolean, route('heater.set_enabled', 'enabled')),
        'heater.delivered_power_w': slot('W', 'derived', n('W', 0, 700)),
        'pressure.pressure_pa': slot('Pa', 'process_truth', n('Pa', 0), private=True),
        'pressure.indicated_pressure_pa': slot('Pa', 'derived', n('Pa', 0)),
        'pressure.maximum_gauge_pressure_pa': slot('Pa', 'derived', n('Pa', 20700000, 20700000)),
        'vessel.volume_m3': slot('m^3', 'derived', n('m^3', 25e-6, 25e-6)),
        'vessel.seal_material': slot('1', 'derived', {'type': 'string', 'const': 'FKM'}),
        'lid.lift_m': slot('m', 'scene', n('m', 0, .12)),
        'lid.closed': slot('1', 'derived', boolean),
        'vent.open': slot('1', 'setpoint', boolean, route('vent.set_open', 'open')),
        'stirrer.speed_rad_s': slot('rad/s', 'setpoint', n('rad/s', 0, PARR_4591_MAX_SPEED_RAD_S), route('stirrer.set_speed', 'speed_rad_s')),
        'stirrer.angle_rad': slot('rad', 'scene', n('rad', 0, 2*math.pi)),
    }
    for sid in ('thermal.heater_energy_j', 'thermal.environment_energy_j', 'thermal.stored_energy_change_j'):
        slots[sid] = slot('J', 'process_truth', n('J'), private=True)
    for sid in ('gas.moles', 'gas.initial_moles', 'gas.exchanged_moles'):
        slots[sid] = slot('mol', 'process_truth', n('mol'), private=True)
    for sid, info in slots.items():
        info['read_binding'] = f"Parr4591Session.read(instance_id=..., state_ids=['{sid}'])"
        if sid.startswith('pressure.'):
            info['reference_conditions'] = 'Gauge pressure relative to assumed 101325 Pa atmosphere.' if 'gauge' in sid else 'Absolute pressure; subtract assumed ambient 101325 Pa for gauge pressure.'
        if info['origin'] == 'derived': info['observation_note_zh'] = '确定性模拟派生显示或目录常量，quality=estimated；非实测传感器。'
        if sid.endswith('_energy_j') or sid.endswith('_moles'):
            info['sampling'] = {'mode': 'integral', 'time_domain': 'simulation', 'window': 'Since instance creation; gas balance reference restarts on trusted charge configuration.'}
    defs = {
        'power.set': ({'enabled': boolean}, {'enabled': boolean}, {'enabled': True}, ['heater', 'motor'], ['power.enabled', 'heater.enabled', 'heater.delivered_power_w', 'stirrer.speed_rad_s'], '设置模拟供电；断电撤销加热和搅拌但保留热量与气体。'),
        'thermal.set_temperature': ({'temperature_K': n('K', 273.15, 498.15)}, {'setpoint_K': n('K')}, {'temperature_K': 373.15}, ['heater', 'vessel'], ['thermal.target_k'], '仅写入目标；闭釜目标预测压力超过207bar表压时整次拒绝。'),
        'heater.set_enabled': ({'enabled': boolean}, {'enabled': boolean}, {'enabled': True}, ['heater'], ['heater.enabled', 'heater.delivered_power_w'], '供电且闭盖才可允许模拟加热；温度仅宿主step推进。'),
        'stirrer.set_speed': ({'speed_rad_s': n('rad/s', 0, PARR_4591_MAX_SPEED_RAD_S)}, {'speed_rad_s': n('rad/s')}, {'speed_rad_s': 10.0}, ['motor', 'stirrer'], ['stirrer.speed_rad_s'], '设定理想搅拌转速，宿主step积分关节角；不计算混合、扭矩或反应。'),
        'lid.open': ({}, {'lift_m': n('m'), 'closed': boolean}, {}, ['lid', 'vessel'], ['lid.lift_m', 'lid.closed', 'pressure.pressure_pa', 'pressure.indicated_pressure_pa', 'gas.moles', 'gas.exchanged_moles'], '移头式开盖终态示意；须先关闭加热搅拌、冷却及泄压，真实脱驱动和离架流程未实现。'),
        'lid.close': ({}, {'lift_m': n('m'), 'closed': boolean}, {}, ['lid', 'vessel'], ['lid.lift_m', 'lid.closed'], '闭合移头式盖的示意终态，不模拟装架、锁环装配或机器人操作。'),
        'vent.set_open': ({'open': boolean}, {'open': boolean}, {'open': True}, ['vent_valve'], ['vent.open'], '设置理想排气阀；泄压由宿主step有限时间推进，未标定流阻或真实阀门性能。'),
    }
    caps = {}
    process = ['thermal.temperature_k', 'thermal.indicated_temperature_k', 'heater.delivered_power_w', 'pressure.pressure_pa', 'pressure.indicated_pressure_pa', 'gas.moles', 'gas.exchanged_moles', 'thermal.heater_energy_j', 'thermal.environment_energy_j', 'thermal.stored_energy_change_j', 'stirrer.angle_rad']
    for cap,(args,out,example,components,effects,meaning) in defs.items():
        checks = []
        if cap == 'thermal.set_temperature': checks = [('power.enabled', ['power.enabled']), ('ambient_temperature_K <= temperature_K <= 498.15 and forecast_absolute_pressure <= 20801325', ['thermal.target_k'])]
        if cap == 'heater.set_enabled': checks = [('not enabled or (power.enabled and lid.closed and forecast_absolute_pressure <= 20801325)', ['power.enabled','lid.closed'])]
        if cap == 'stirrer.set_speed': checks = [('speed_rad_s == 0 or (power.enabled and lid.closed)', ['power.enabled','lid.closed'])]
        if cap == 'lid.open': checks = [('not heater.enabled and stirrer.speed_rad_s == 0 and temperature <= 323.15 K and abs(pressure-101325 Pa) <= 2000 Pa', ['heater.enabled','stirrer.speed_rad_s'])]
        if cap == 'vent.set_open': checks = [('open or forecast_absolute_pressure <= 20801325 Pa', ['vent.open'])]
        caps[cap] = {'availability':'implemented', 'visibility':'agent', 'kind':'device_command', 'description_zh':meaning,
            'component_ids':components, 'input_schema':obj(args), 'output_schema':obj(out), 'example_arguments':[example],
            'observes': effects, 'state_effects':[{'state_id':s,'timing':'immediate','description_zh':meaning} for s in effects]+[{'state_id':s,'timing':'on_step','description_zh':'宿主推进有限热响应、理想气体温压关系和场景旋转。'} for s in process],
            'preconditions':[{'description_zh':c,'state_ids':ids,'enforced_by':'adapter','check':c} for c,ids in checks],
            'scene_backend_required':cap.startswith('lid.'), 'binding':f"basic40_runtime_thermal.Parr4591Session.invoke(instance_id=..., capability_id='{cap}', arguments=..., request_id=...)",
            'completion_criterion_zh':'成功只确认设定或场景命令完成；达到温度、泄压与转动须宿主step。'}
    unsupported = {cap:{'availability':'unsupported','visibility':'agent','kind':'device_command','description_zh':why,
        'component_ids':['vessel'],'input_schema':obj({}),'output_schema':obj({}),'example_arguments':[{}],'state_effects':[],'observes':[],'preconditions':[],'binding':None,'scene_backend_required':False}
        for cap,why in {'chemistry.react':'未实现化学反应、相变、放热或真实气体方程。','hardware.connect':'没有真实硬件驱动。','robot.transfer':'没有机器人抓放或物质转移。'}.items()}
    def parameter(value,unit,evidence,source): return {'value':value,'unit':unit,'evidence':evidence,'source':source}
    params={
        'vessel_volume_m3':parameter(25e-6,'m^3','documented','PARR_4590_SPEC; 25 mL'),
        'maximum_gauge_pressure_Pa':parameter(207e5,'Pa','documented','PARR_4590_SPEC; PARR_399M_MANUAL p7 pressure is psig; standard configuration, CE/UKCA 200 bar not selected.'),
        'maximum_temperature_K':parameter(498.15,'K','documented','PARR_4590_SPEC; standard FKM seal 225 deg C'),
        'heater_power_W':parameter(700.0,'W','documented','PARR_4590_SPEC'),
        'thermal_capacity_J_K':parameter(1000.0,'J/K','assumption','Effective-node assumption selected for initial 700/1000 = 0.7 K/s. Order of magnitude considers documented 1.587573295 kg cylinder with assumed steel cp near500 J/kg/K; neither alloy cp nor assembly effective thermal capacity is measured.'),
        'heat_loss_W_K':parameter(.7,'W/K','assumption','Assume 20% of700W lost at200K excess: UA=140/200=.7W/K; power does not uniquely identify C or UA.'),
        'open_extra_loss_W_K':parameter(2.1,'W/K','assumption','Illustrative additional passive loss while open.'),
        'vent_time_constant_s':parameter(15.0,'s','assumption','Illustrative exponential absolute-pressure relaxation to ambient; no valve calibration.'),
        'opening_temperature_K':parameter(323.15,'K','assumption','Simulation-only50degC opening threshold; not a factory interlock or hardware safety advice.'),
        'opening_pressure_tolerance_Pa':parameter(2000.0,'Pa','assumption','Simulation-only2kPa pressure difference opening tolerance.'),
        'lid_travel_m':parameter(.12,'m','assumption','Detached-head illustrative opening pose, not measured travel; required vessel removal from stand is not modeled as a robot operation.'),
        'maximum_speed_rad_s':parameter(PARR_4591_MAX_SPEED_RAD_S,'rad/s','documented','PARR_399M_MANUAL pp20-21 standard1700rpm pulley; continuous0..1700 control range is an assumption.'),
        'ambient_pressure_Pa':parameter(101325.0,'Pa','assumption','Standard atmosphere used to convert gauge pressure to absolute pressure.'),
        'manufacturer_heatup_time_s':parameter(None,'s','unknown','No loaded4591 heatup curve or factory time benchmark validated.'),
    }
    return {'asset_types':{PARR_4591_TYPE:{'catalog_id':'CHEM-018','model':'Parr4591 moveable head,25mL,standard FKM','asset_version':'1.1.0','state_slots':slots,'capabilities':caps,'declared_capabilities':unsupported,'parameters':params}},
        'host_capabilities':{'sim.step':{'kind':'simulation','visibility':'trusted_host_only','input_schema':obj({'dt_s':n('s',0,86400)}),'binding':'Parr4591Runtime.step(dt_s)','partial_failure':'Clock/process commit before scene callbacks; callback failure can leave partial scene effects.'},
            'sim.configure_gas_charge':{'kind':'configuration','visibility':'trusted_host_only','binding':'Parr4591Runtime.configure_gas_charge(instance_id, *, pressure_Pa, evidence)','description_zh':'关闭供电且闭釜闭阀时配置显式惰性理想气体绝压，不代表真实充气；整次校验当前及目标压力上限。'}},
        'trusted_scene_callback':'scene_adapter(instance_id, updates) receives public fields only: lid.lift_m, lid.closed, stirrer.angle_rad, stirrer.speed_rad_s, vent.open, thermal.indicated_temperature_k, pressure.indicated_pressure_pa, power.enabled, heater.enabled.',
        'result_schema_scope':'capability.output_schema validates envelope.result only',
        'limitations':['25mL is modeled entirely as constant ideal-gas volume; no sample loading, liquid expansion, boiling, dissolved gas, reactions, rupture-disc failure or high-pressure real-gas accuracy.',
            'P_abs=nRT/V;207bar limit is gauge pressure, absolute limit20801325Pa uses assumed101325Pa ambient. No CE/UKCA200bar configuration.',
            'C=1000J/K and UA=.7W/K are explicit effective-node assumptions constrained in scale by700W, not uniquely inferred or manufacturer-calibrated; factory dynamic comparison not_run.',
            'Temperature rounds0.1degC and pressure rounds100Pa, quality=estimated; no measured sensor accuracy.',
            'Opening temperature and pressure conditions are simulation rules, not validated hardware interlocks. Moveable head assumes drive detached and vessel removed; full robot/mechanical opening sequence absent.',
            'Only1700rpm standard pulley endpoint is documented; continuous control, instantaneous speed changes and zero-speed stop are modeled assumptions; no drag/torque/mixing.',
            'Vent15s relaxation is uncalibrated and gas exchange energy is omitted from the effective solid thermal node; no adiabatic blowdown or real vent sizing.',
            'API filtering is not a Python process or filesystem sandbox. Host state and debug records must remain trusted. No persistent deduplication after restart.']}


class Parr4591Runtime(Multiwave7101Runtime):
    """Trusted host for isolated 4591 instances; inherited generic access/registry helpers only."""
    def __init__(self, *, ambient_temperature_K=298.15, scene_adapter=None):
        super().__init__(ambient_temperature_K=ambient_temperature_K, scene_adapter=scene_adapter)
        self._metadata = export_parr_4591_metadata(panel=False)
        self._agent = Parr4591Session(self, '__public_agent__')

    def _scene_payload(self, state):
        fields = ('lid.lift_m','lid.closed','stirrer.angle_rad','stirrer.speed_rad_s','vent.open','thermal.indicated_temperature_k','pressure.indicated_pressure_pa','power.enabled','heater.enabled')
        return {sid:copy.deepcopy(state[sid]) for sid in fields}

    def create_instance(self, instance_id, asset_type_id=PARR_4591_TYPE):
        with self._lock:
            if not isinstance(instance_id,str) or not instance_id or instance_id in self._instances or asset_type_id != PARR_4591_TYPE: raise ValueError('Unique4591 instance ID required')
            t=self.ambient_temperature_K
            moles=PARR_4591_AMBIENT_PA*PARR_4591_VOLUME_M3/(PARR_4591_GAS_R*t)
            state={'power.enabled':False,'thermal.target_k':t,'thermal.temperature_k':t,'thermal.indicated_temperature_k':round(t-273.15,1)+273.15,
                'heater.enabled':False,'heater.delivered_power_w':0.0,'pressure.pressure_pa':PARR_4591_AMBIENT_PA,'pressure.indicated_pressure_pa':round(PARR_4591_AMBIENT_PA/100)*100.0,
                'pressure.maximum_gauge_pressure_pa':PARR_4591_MAX_GAUGE_PA,'vessel.volume_m3':PARR_4591_VOLUME_M3,'vessel.seal_material':'FKM',
                'lid.lift_m':0.0,'lid.closed':True,'vent.open':False,'stirrer.speed_rad_s':0.0,'stirrer.angle_rad':0.0,
                'gas.moles':moles,'gas.initial_moles':moles,'gas.exchanged_moles':0.0,'thermal.heater_energy_j':0.0,'thermal.environment_energy_j':0.0,'thermal.stored_energy_change_j':0.0,'heater.level':'full'}
            if self.scene_adapter:
                try: self.scene_adapter(instance_id,self._scene_payload(state))
                except Exception: raise RuntimeError('Initial scene callback failed; instance not created; backend effects unknown') from None
            self._instances[instance_id]={'state':state,'revision':0}

    def session(self, principal_id):
        with self._lock:
            if not isinstance(principal_id,str) or principal_id not in self._principals: raise ValueError('Host registration required')
            return Parr4591Session(self,principal_id)

    def _forecast_pressure(self, state, target=None):
        temperature=max(state['thermal.temperature_k'],state['thermal.target_k'] if target is None else target)
        return state['gas.moles']*PARR_4591_GAS_R*temperature/PARR_4591_VOLUME_M3

    def configure_gas_charge(self, instance_id, *, pressure_Pa, evidence):
        with self._lock:
            if not isinstance(instance_id,str) or instance_id not in self._instances: raise ValueError('Unknown instance')
            if not _number(pressure_Pa) or not PARR_4591_AMBIENT_PA <= pressure_Pa <= PARR_4591_MAX_ABSOLUTE_PA or not isinstance(evidence,str) or not evidence.strip(): raise ValueError('Finite absolute pressure within declared bounds and explicit evidence required')
            inst=self._instances[instance_id]; state=copy.deepcopy(inst['state'])
            if state['power.enabled'] or not state['lid.closed'] or state['vent.open']: raise ValueError('Charge configuration requires power off, closed lid and closed vent')
            moles=pressure_Pa*PARR_4591_VOLUME_M3/(PARR_4591_GAS_R*state['thermal.temperature_k'])
            state.update({'gas.moles':moles,'gas.initial_moles':moles,'gas.exchanged_moles':0.0,'pressure.pressure_pa':float(pressure_Pa),'pressure.indicated_pressure_pa':round(pressure_Pa/100)*100.0})
            if self._forecast_pressure(state)>PARR_4591_MAX_ABSOLUTE_PA+1e-6: raise ValueError('Charge and existing target exceed configured pressure envelope')
            if self.scene_adapter:
                try: self.scene_adapter(instance_id,self._scene_payload(state))
                except Exception: raise RuntimeError('Charge scene callback failed; runtime unchanged; backend effects unknown') from None
            inst['state'],inst['revision'],inst['gas_configuration_evidence']=state,inst['revision']+1,evidence

    def _delivered(self,state):
        if not state['power.enabled'] or not state['heater.enabled'] or not state['lid.closed']: return 0.0
        t,target=state['thermal.temperature_k'],state['thermal.target_k']
        if t>target+1e-10:return 0.0
        p=PARR_4591_POWER_W*_PARR_LEVELS[state.get('heater.level','full')]
        return p if t<target-1e-10 else min(p,PARR_4591_LOSS_W_K*(target-self.ambient_temperature_K))

    def _advance(self,state,dt):
        ambient,c=self.ambient_temperature_K,PARR_4591_CAPACITY_J_K
        ua=PARR_4591_LOSS_W_K+(0.0 if state['lid.closed'] else 2.1)
        old,target=state['thermal.temperature_k'],state['thermal.target_k']
        power=PARR_4591_POWER_W*_PARR_LEVELS[state.get('heater.level','full')] if state['power.enabled'] and state['heater.enabled'] and state['lid.closed'] else 0.0
        current,remaining,energy=old,dt,0.0
        if current>target:
            crossing=c/ua*math.log((current-ambient)/(target-ambient)) if target>ambient else math.inf
            h=min(remaining,crossing);current=ambient+(current-ambient)*math.exp(-ua*h/c);remaining-=h
            if crossing<=h:current=target
        if remaining>0:
            equilibrium=ambient+power/ua
            if power>0 and equilibrium>=target and current<=target:
                crossing=0.0 if current==target else (c/ua*math.log((equilibrium-current)/(equilibrium-target)) if equilibrium>target else math.inf)
                h=min(remaining,crossing);current=equilibrium+(current-equilibrium)*math.exp(-ua*h/c);energy+=power*h
                if crossing<=h:current=target;energy+=ua*(target-ambient)*(remaining-h)
            else:current=equilibrium+(current-equilibrium)*math.exp(-ua*remaining/c);energy+=power*remaining
        current=min(PARR_4591_MAX_K,max(ambient,current))
        state['thermal.temperature_k']=current;state['thermal.indicated_temperature_k']=round(current-273.15,1)+273.15
        state['thermal.heater_energy_j']+=energy;state['thermal.environment_energy_j']+=energy-c*(current-old)
        state['thermal.stored_energy_change_j']=c*(current-ambient);state['heater.delivered_power_w']=self._delivered(state)
        old_n=state['gas.moles'];pressure=old_n*PARR_4591_GAS_R*current/PARR_4591_VOLUME_M3
        if not state['lid.closed']:pressure=PARR_4591_AMBIENT_PA
        elif state['vent.open']:pressure=PARR_4591_AMBIENT_PA+(pressure-PARR_4591_AMBIENT_PA)*math.exp(-dt/15.0)
        if pressure>PARR_4591_MAX_ABSOLUTE_PA+1e-6:raise ValueError('Proposed host step exceeds declared pressure limit; no instance or clock committed')
        new_n=pressure*PARR_4591_VOLUME_M3/(PARR_4591_GAS_R*current)
        if state['vent.open'] or not state['lid.closed']:
            state['gas.exchanged_moles']+=new_n-old_n;state['gas.moles']=new_n
        state['pressure.pressure_pa']=pressure;state['pressure.indicated_pressure_pa']=round(pressure/100)*100.0
        state['stirrer.angle_rad']=(state['stirrer.angle_rad']+state['stirrer.speed_rad_s']*dt)%(2*math.pi)

    def step(self,dt_s):
        if not _number(dt_s) or not 0<=dt_s<=86400:raise ValueError('Host step finite0..86400s required')
        if dt_s==0:return
        with self._lock:
            proposed={sid:copy.deepcopy(inst['state']) for sid,inst in self._instances.items()}
            for state in proposed.values():self._advance(state,float(dt_s))
            for sid,state in proposed.items():self._instances[sid]['state']=state;self._instances[sid]['revision']+=1
            self.time_s+=float(dt_s)
            failed=False
            if self.scene_adapter:
                for sid,state in proposed.items():
                    try:self.scene_adapter(sid,self._scene_payload(state))
                    except Exception:
                        failed=True;self._scene_faults.append({'instance_id':sid,'time_s':self.time_s,'code':'SCENE_SYNCHRONIZATION_FAILED','side_effects':'partial_or_unknown'})
            if failed:raise SceneSynchronizationError('Clock/process advanced; scene synchronization failed with possible partial scene effects') from None


class Parr4591Session:
    """Capability-limited interface; caller cannot supply a role or clock step."""
    def __init__(self, host, principal_id): self.__host, self.__principal = host, principal_id
    def _visible(self, info): return info['visibility'] == 'agent' or self.__host._principals[self.__principal][0] in ('debug', 'evaluator')
    def _instance(self, sid):
        if not isinstance(sid, str): return None
        scope = self.__host._principals[self.__principal][1]
        return self.__host._instances.get(sid) if scope is None or sid in scope else None
    def _envelope(self, sid, request_id=None, *, result=None, observations=None, status='succeeded', error=None):
        inst = self._instance(sid)
        return {'protocol_version': PROTOCOL_VERSION, 'instance_id': sid if isinstance(sid, str) else None, 'request_id': request_id if isinstance(request_id, str) else None,
            'operation_id': None, 'status': status, 'state_revision': inst['revision'] if inst else None, 'clock': {'domain': 'simulation', 'time_s': self.__host.time_s},
            'result': copy.deepcopy(result or {}), 'observations': copy.deepcopy(observations or {}), 'error': error}
    def _error(self, sid, code, request_id=None, status='rejected'):
        messages = {'UNKNOWN_INSTANCE': 'Instance unavailable.', 'UNKNOWN_CAPABILITY': 'Capability unavailable.', 'INVALID_ARGUMENT': 'Arguments violate schema or bounds.',
            'ACCESS_DENIED': 'Request outside session policy.', 'PRECONDITION_FAILED': 'Required simulation state unavailable.', 'REQUEST_CONFLICT': 'Request ID belongs to different arguments.',
            'UNSUPPORTED': 'Execution unavailable in this adapter.', 'DEVICE_ERROR': 'Scene callback did not complete command.', 'OVERPRESSURE': 'Proposed operation exceeds configured pressure envelope.'}
        return self._envelope(sid, request_id, status=status, error={'code': code, 'message': messages[code], 'retryable': False, 'side_effects': 'unknown' if code == 'DEVICE_ERROR' else 'none', 'progress': None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        host = self.__host
        with host._lock:
            if (asset_type_id is None) == (instance_id is None): return self._error(None, 'INVALID_ARGUMENT')
            if instance_id is not None and not self._instance(instance_id): return self._error(instance_id, 'UNKNOWN_INSTANCE')
            if asset_type_id is not None and asset_type_id != PARR_4591_TYPE: return self._error(None, 'INVALID_ARGUMENT')
            if host._principals[self.__principal][1] == frozenset(): return self._error(instance_id, 'ACCESS_DENIED')
            spec = copy.deepcopy(export_parr_4591_metadata()['asset_types'][PARR_4591_TYPE])
            spec['state_slots'] = {sid: info for sid, info in spec['state_slots'].items() if self._visible(info)}
            spec['capabilities'].update(spec.pop('declared_capabilities'))
            for group in ('controller_capabilities', 'panel_capabilities'):
                spec['capabilities'].update({k: v for k, v in spec.pop(group).items() if self._visible(v)})
            for cap in spec['capabilities'].values():
                cap['state_effects'] = [e for e in cap['state_effects'] if e['state_id'] in spec['state_slots']]
                cap['observes'] = [sid for sid in cap['observes'] if sid in spec['state_slots']]
                if cap.get('scene_backend_required') and host.scene_adapter is None: cap['availability'] = 'unsupported'
            return {'protocol_version': PROTOCOL_VERSION, 'asset_type_id': PARR_4591_TYPE, 'instance_id': instance_id,
                'adapter': 'basic40_runtime_thermal.Parr4591Session', **spec, 'limitations': copy.deepcopy(host._metadata['limitations'])}

    def read(self, *, instance_id, state_ids=None):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, 'UNKNOWN_INSTANCE')
            slots = host._metadata['asset_types'][PARR_4591_TYPE]['state_slots']
            if state_ids is None: state_ids = [sid for sid, info in slots.items() if self._visible(info)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids) or len(set(state_ids)) != len(state_ids): return self._error(instance_id, 'INVALID_ARGUMENT')
            if any(sid not in slots or not self._visible(slots[sid]) for sid in state_ids): return self._error(instance_id, 'ACCESS_DENIED')
            obs = {sid: {'value': copy.deepcopy(inst['state'][sid]), 'unit': slots[sid]['unit'], 'quality': 'unavailable' if inst['state'][sid] is None else ('estimated' if slots[sid]['origin'] == 'derived' else 'valid'), 'sample_time_s': host.time_s, 'sample_window_s': None} for sid in state_ids}
            return self._envelope(instance_id, observations=obs)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id or len(request_id) > 256:
                return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            try: digest = hashlib.sha256(json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError): return self._error(instance_id, 'INVALID_ARGUMENT', request_id)
            key = (self.__principal, instance_id, request_id)
            if key in host._requests:
                old_digest, response = host._requests[key]
                return copy.deepcopy(response) if digest == old_digest else self._error(instance_id, 'REQUEST_CONFLICT', request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, copy.deepcopy(response))
            host._logs[self.__principal].append({'instance_id': instance_id, 'capability_id': capability_id, 'request_id': request_id, 'response': copy.deepcopy(response)})
            return response

    def _checked(self,sid,inst,cap,args,request_id):
        host,old=self.__host,inst['state'];spec=host._metadata['asset_types'][PARR_4591_TYPE]
        fail=lambda code,status='rejected':self._error(sid,code,request_id,status)
        if isinstance(cap,str) and cap.startswith('panel.'):return self._panel_command(sid,cap,args,request_id)
        if cap=='heater.set_level':                                            # 4848 heater switch II / I (G02 panel)
            if not isinstance(args,dict) or set(args)!={'level'} or args['level'] not in _PARR_LEVELS:return fail('INVALID_ARGUMENT')
            state=copy.deepcopy(old);state['heater.level']=args['level'];state['heater.delivered_power_w']=host._delivered(state)
            if state!=old:
                if host.scene_adapter:
                    try:host.scene_adapter(sid,host._scene_payload(state))
                    except Exception:return fail('DEVICE_ERROR','failed')
                inst['state'],inst['revision']=state,inst['revision']+1
            return self._envelope(sid,request_id,result={'level':args['level']})
        if cap in spec['declared_capabilities']:return fail('UNSUPPORTED','unsupported')
        if cap not in spec['capabilities']:return fail('UNKNOWN_CAPABILITY')
        if set(args)!=set(spec['capabilities'][cap]['input_schema']['properties']):return fail('INVALID_ARGUMENT')
        if cap in ('power.set','heater.set_enabled') and type(args['enabled']) is not bool:return fail('INVALID_ARGUMENT')
        if cap=='vent.set_open' and type(args['open']) is not bool:return fail('INVALID_ARGUMENT')
        if cap=='thermal.set_temperature' and (not _number(args['temperature_K']) or not host.ambient_temperature_K<=args['temperature_K']<=PARR_4591_MAX_K):return fail('INVALID_ARGUMENT')
        if cap=='stirrer.set_speed' and (not _number(args['speed_rad_s']) or not 0<=args['speed_rad_s']<=PARR_4591_MAX_SPEED_RAD_S):return fail('INVALID_ARGUMENT')
        if cap=='thermal.set_temperature' and not old['power.enabled']:return fail('PRECONDITION_FAILED')
        if cap=='heater.set_enabled' and args['enabled'] and not(old['power.enabled'] and old['lid.closed']):return fail('PRECONDITION_FAILED')
        if cap=='stirrer.set_speed' and args['speed_rad_s']>0 and not(old['power.enabled'] and old['lid.closed']):return fail('PRECONDITION_FAILED')
        if cap=='lid.open' and (old['heater.enabled'] or old['stirrer.speed_rad_s']!=0 or old['thermal.temperature_k']>323.15 or abs(old['pressure.pressure_pa']-PARR_4591_AMBIENT_PA)>2000):return fail('PRECONDITION_FAILED')
        predicts_closed=(cap=='thermal.set_temperature' and old['lid.closed'] and not old['vent.open']) or (cap=='vent.set_open' and not args['open'] and old['lid.closed']) or (cap=='heater.set_enabled' and args['enabled'] and not old['vent.open'])
        if predicts_closed and host._forecast_pressure(old,args.get('temperature_K'))>PARR_4591_MAX_ABSOLUTE_PA+1e-6:return fail('OVERPRESSURE')
        if spec['capabilities'][cap]['scene_backend_required'] and host.scene_adapter is None:return fail('UNSUPPORTED','unsupported')
        state=copy.deepcopy(old)
        if cap=='power.set':
            state['power.enabled']=args['enabled']
            if not args['enabled']:state['heater.enabled']=False;state['stirrer.speed_rad_s']=0.0
            state['heater.delivered_power_w']=host._delivered(state);result={'enabled':args['enabled']}
        elif cap=='thermal.set_temperature':state['thermal.target_k']=float(args['temperature_K']);result={'setpoint_K':state['thermal.target_k']}
        elif cap=='heater.set_enabled':state['heater.enabled']=args['enabled'];state['heater.delivered_power_w']=host._delivered(state);result={'enabled':args['enabled']}
        elif cap=='stirrer.set_speed':state['stirrer.speed_rad_s']=float(args['speed_rad_s']);result={'speed_rad_s':state['stirrer.speed_rad_s']}
        elif cap=='vent.set_open':state['vent.open']=args['open'];result={'open':args['open']}
        else:
            closed=cap=='lid.close';state['lid.closed']=closed;state['lid.lift_m']=0.0 if closed else .12
            if not closed:
                new_n=PARR_4591_AMBIENT_PA*PARR_4591_VOLUME_M3/(PARR_4591_GAS_R*state['thermal.temperature_k'])
                state['gas.exchanged_moles']+=new_n-state['gas.moles'];state['gas.moles']=new_n
                state['pressure.pressure_pa']=PARR_4591_AMBIENT_PA;state['pressure.indicated_pressure_pa']=round(PARR_4591_AMBIENT_PA/100)*100.0
            result={'lift_m':state['lid.lift_m'],'closed':closed}
        if state!=old:
            if host.scene_adapter:
                try:host.scene_adapter(sid,host._scene_payload(state))
                except Exception:return fail('DEVICE_ERROR','failed')
            inst['state'],inst['revision']=state,inst['revision']+1
        return self._envelope(sid,request_id,result=result)

    def _panel_command(self, sid, cap, args, request_id):
        """panel.* commands: a gesture on the instance's 4848 controller panel (engine + panel_ui.json), a screen capture, or the evaluator frame."""
        host, panel = self.__host, _parr_panel()
        if cap not in panel['caps']:
            return self._error(sid, 'UNKNOWN_CAPABILITY', request_id)
        if not self._visible(panel['caps'][cap]):
            return self._error(sid, 'ACCESS_DENIED', request_id)
        host._panel_caller = self                                         # a gesture acts as the session that makes it (access rules)
        try:
            result, error = _g02_panel_command(panel, host.panel(sid), cap, args, instance_id=sid, time_s=host.time_s, capture_dir=host.panel_capture_dir)
        finally:
            host._panel_caller = None
        return self._error(sid, error, request_id) if error else self._envelope(sid, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if not self._instance(instance_id): return self._error(instance_id, 'UNKNOWN_INSTANCE', request_id)
            slots = self.__host._metadata['asset_types'][PARR_4591_TYPE]['state_slots']
            slot = slots.get(state_id) if isinstance(state_id, str) else None
            if slot is None or not self._visible(slot) or slot['access'] != 'read_write': return self._error(instance_id, 'ACCESS_DENIED', request_id)
            route = slot['write_via']
            return self.invoke(instance_id=instance_id, capability_id=route['capability_id'], arguments={route['parameter']: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock: return copy.deepcopy(self.__host._logs[self.__principal])

# END CHEM-018 Parr 4591 runtime.

# CHEM-018 append-only correction: integrate gas relaxation along the exact
# temperature trajectory instead of applying vent relaxation at one endpoint.
_Parr4591ThermalBase = Parr4591Runtime


# ---- G02 front panel (approximate): the Parr 4848 Reactor Controller that runs the 4591 (temperature module PV / SP with up / down keys,
# tachometer and pressure displays, heater switch II / I / O, motor switch and speed knob, power switch).  Its manual 548M is not saved; the
# controls follow the 399M reactor manual (p.4, p.8) and the 4848 product page.
PARR_4591_ASSET_DIR = Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t1_thermal/parr_4591'
_PARR_LEVELS = {'full': 1.0, 'half': 0.5}      # heater switch II = full power, I = half power (399M p.4)


def _parr_panel():
    return _g02_panel('parr_4591', PARR_4591_ASSET_DIR, {'power.set', 'thermal.set_temperature', 'heater.set_enabled', 'heater.set_level', 'stirrer.set_speed'})


class _ParrPanel:
    """The 4848 controller panel of one host (mixed into Parr4591Runtime)."""

    def __init__(self, *, ambient_temperature_K=298.15, scene_adapter=None):
        super().__init__(ambient_temperature_K=ambient_temperature_K, scene_adapter=scene_adapter)
        self._metadata = export_parr_4591_metadata(panel=False)
        self._pp_panels, self.panel_capture_dir, self._pp_epoch = {}, None, {}
        self._principals['__parr_panel__'], self._logs['__parr_panel__'] = ('agent', None), []
        self._panel_session = Parr4591Session(self, '__parr_panel__')   # the front panel's own path to the commands (not logged)
        self._panel_caller = None                                       # the session whose gesture runs: its commands act as that caller

    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame), created on first use."""
        if instance_id not in self._pp_panels:
            if instance_id not in self._instances: raise ValueError('Unknown instance')
            p = _parr_panel()
            host = _G02PanelHost(p['engine'], lambda: self._pp_panel_read(instance_id),
                                 lambda cap, args: (self._panel_caller or self._panel_session)._checked(instance_id, self._instances[instance_id], cap, args, None),
                                 lambda: self.time_s)
            self._pp_panels[instance_id] = p['engine'].PanelUI(p['spec'], host)
        return self._pp_panels[instance_id]

    def _pp_panel_read(self, instance_id):
        s = self._instances[instance_id]['state']
        slots = self._metadata['asset_types'][PARR_4591_TYPE]['state_slots']
        out = {sid: copy.deepcopy(v) for sid, v in s.items() if sid in slots and slots[sid]['visibility'] == 'agent'}
        out.update({'host.powered': s['power.enabled'], 'host.pv_c': round(s['thermal.indicated_temperature_k'] - 273.15, 6),
                    'host.rpm': round(s['stirrer.speed_rad_s'] * 60 / (2 * math.pi), 6), 'host.pressure_psi': round((s['pressure.indicated_pressure_pa'] - PARR_4591_AMBIENT_PA) / 6894.757293168, 6),
                    'host.heating': s['heater.delivered_power_w'] > 0, 'host.min_sp_c': round(self.ambient_temperature_K - 273.15, 6),
                    'host.max_speed_rad_s': PARR_4591_MAX_SPEED_RAD_S})
        return out


class Parr4591Runtime(_ParrPanel, _Parr4591ThermalBase):
    """4591 host with coupled non-isothermal gas exchange and bounded quadrature."""
    def _advance(self, state, dt):
        if not (state['lid.closed'] and state['vent.open']):
            return super()._advance(state, dt)
        original = copy.deepcopy(state)
        # Thermal solver is independent of gas contents. Use a harmless trial
        # amount for its gas bookkeeping, then replace that bookkeeping below.
        state['gas.moles'] = 0.0
        super()._advance(state, dt)
        ambient, old = self.ambient_temperature_K, original['thermal.temperature_k']
        target, c, ua, tau = original['thermal.target_k'], PARR_4591_CAPACITY_J_K, PARR_4591_LOSS_W_K, 15.0
        power = PARR_4591_POWER_W * _PARR_LEVELS[original.get('heater.level', 'full')] if original['power.enabled'] and original['heater.enabled'] else 0.0

        def temperature_at(elapsed):
            current, remaining = old, elapsed
            if current > target:
                crossing = c / ua * math.log((current - ambient) / (target - ambient)) if target > ambient else math.inf
                h = min(remaining, crossing)
                current = ambient + (current - ambient) * math.exp(-ua * h / c)
                remaining -= h
                if crossing <= h: current = target
            if remaining > 0:
                equilibrium = ambient + power / ua
                if power > 0 and equilibrium >= target and current <= target:
                    crossing = 0.0 if current == target else (c / ua * math.log((equilibrium - current) / (equilibrium - target)) if equilibrium > target else math.inf)
                    h = min(remaining, crossing)
                    current = equilibrium + (current - equilibrium) * math.exp(-ua * h / c)
                    if crossing <= h: current = target
                else:
                    current = equilibrium + (current - equilibrium) * math.exp(-ua * remaining / c)
            return min(PARR_4591_MAX_K, max(ambient, current))

        final_temperature = state['thermal.temperature_k']
        if old == final_temperature:
            equilibrium_n = PARR_4591_AMBIENT_PA * PARR_4591_VOLUME_M3 / (PARR_4591_GAS_R * old)
            new_n = equilibrium_n + (original['gas.moles'] - equilibrium_n) * math.exp(-dt / tau)
        else:
            # Exact integrating factor for dn/dt=(n_eq(T(t))-n)/tau.
            # Lag x=(dt-t)/tau keeps the exponential well conditioned. The
            # omitted x>40 tail is below exp(-40)/273.15 K^-1; adaptive Simpson
            # bounds absolute quadrature error at 1e-14 K^-1 per host step.
            def integrand(x): return math.exp(-x) / temperature_at(max(0.0, dt - tau * x))
            def simpson(a, b, fa, fm, fb): return (b - a) * (fa + 4 * fm + fb) / 6
            def adaptive(a, b, fa, fm, fb, whole, tolerance, depth):
                middle = (a + b) / 2
                fl, fr = integrand((a + middle) / 2), integrand((middle + b) / 2)
                left, right = simpson(a, middle, fa, fl, fm), simpson(middle, b, fm, fr, fb)
                difference = left + right - whole
                if abs(difference) <= 15 * tolerance:
                    return left + right + difference / 15
                if depth == 0: raise ValueError('Gas quadrature tolerance was not achieved; host state unchanged')
                return adaptive(a, middle, fa, fl, fm, left, tolerance / 2, depth - 1) + adaptive(middle, b, fm, fr, fb, right, tolerance / 2, depth - 1)
            upper = min(dt / tau, 40.0)
            fa, fm, fb = integrand(0.0), integrand(upper / 2), integrand(upper)
            integral = adaptive(0.0, upper, fa, fm, fb, simpson(0.0, upper, fa, fm, fb), 1e-14, 24)
            new_n = original['gas.moles'] * math.exp(-dt / tau) + PARR_4591_AMBIENT_PA * PARR_4591_VOLUME_M3 / PARR_4591_GAS_R * integral
        pressure = new_n * PARR_4591_GAS_R * final_temperature / PARR_4591_VOLUME_M3
        if pressure > PARR_4591_MAX_ABSOLUTE_PA + 1e-6:
            raise ValueError('Proposed coupled host step exceeds declared pressure limit; no state committed')
        state['gas.moles'] = new_n
        state['gas.exchanged_moles'] = original['gas.exchanged_moles'] + new_n - original['gas.moles']
        state['pressure.pressure_pa'] = pressure
        state['pressure.indicated_pressure_pa'] = round(pressure / 100) * 100.0


_parr_4591_base_metadata = export_parr_4591_metadata


def export_parr_4591_metadata(panel=True):
    """The 4591 metadata; the heater-level command of the 4848 heater switch and the panel commands in separate groups (G02 panel)."""
    metadata = _parr_4591_base_metadata()
    caps = metadata['asset_types'][PARR_4591_TYPE]['capabilities']
    slots = metadata['asset_types'][PARR_4591_TYPE]['state_slots']
    for info in caps.values():
        info['observes'] = [sid for sid in info['observes'] if slots[sid]['visibility'] == 'agent']
    metadata['limitations'].append('Non-isothermal vent model integrates dn/dt=(Pamb*V/(R*T(t))-n)/15s along the exact thermal trajectory. Adaptive Simpson absolute tolerance1e-14 K^-1; exponential tail beyond40 time constants omitted (<exp(-40)/273.15 K^-1). Failure to converge rejects the entire host step before commit.')
    return _parr_extend_metadata(metadata, panel)


def _parr_extend_metadata(meta, panel):
    spec = meta['asset_types'][PARR_4591_TYPE]
    spec['state_slots']['heater.level'] = {'unit': '1', 'origin': 'setpoint', 'visibility': 'agent', 'access': 'read_write',
                                           'write_via': {'capability_id': 'heater.set_level', 'parameter': 'level'},
                                           'value_schema': {'type': 'string', 'enum': ['full', 'half']}, 'reference_conditions': None,
                                           'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None},
                                           'read_binding': "Parr4591Session.read(instance_id=..., state_ids=['heater.level'])",
                                           'meaning_zh': '4848加热开关档位：II全功率700 W、I半功率350 W（399M p.4）；O档即heater.set_enabled关'}
    effects = ['heater.level', 'heater.delivered_power_w']
    controller = {'heater.set_level': {
        'availability': 'implemented', 'visibility': 'agent', 'kind': 'device_command', 'component_ids': ['heater', 'heater_switch'],
        'description_zh': '加热开关II/I：全功率或半功率（399M p.4）；只改变允许加热时的功率上限', 'input_schema': _multiwave_object({'level': {'type': 'string', 'enum': ['full', 'half']}}),
        'output_schema': _multiwave_object({'level': {'type': 'string'}}), 'example_arguments': [{'level': 'half'}], 'observes': ['heater.level'],
        'state_effects': [{'state_id': sid, 'timing': 'immediate', 'description_zh': '加热功率档位'} for sid in effects] +
                         [{'state_id': sid, 'timing': 'on_step', 'description_zh': '宿主按该档功率推进热响应'} for sid in ('thermal.indicated_temperature_k', 'pressure.indicated_pressure_pa')],
        'preconditions': [], 'scene_backend_required': False,
        'binding': "basic40_runtime_thermal.Parr4591Session.invoke(instance_id=..., capability_id='heater.set_level', arguments=..., request_id=...)",
        'completion_criterion_zh': '同步返回即档位已设'}}
    panel_caps = {}
    if panel:
        p = _parr_panel()
        called = p['engine'].spec_capabilities(p['spec'])
        caps = {**spec['capabilities'], **controller}
        eff = list({e['state_id']: dict(e, description_zh='面板动作调用的能力的效果：' + e['description_zh']) for c in called for e in caps[c]['state_effects']
                    if e['timing'] == 'immediate' and spec['state_slots'][e['state_id']]['visibility'] == 'agent'}.values())
        panel_caps = _g02_panel_capabilities(p, "basic40_runtime_thermal.Parr4591Session.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)", eff)
    spec.update(controller_capabilities=controller, panel_capabilities=panel_caps, asset_version='1.3.0')   # 1.2.1: panel caller fix (sync audit 2026-10-19); 1.3.0: 4848 controller 3D parts (2026-10-20)
    meta['limitations'] = meta['limitations'] + [
        '4848 Reactor Controller front panel (approximate; controller manual 548M not saved): temperature module PV / SP with up / down keys, tachometer and pressure '
        'displays, heater switch II (700 W) / I (350 W) / O, motor switch with speed knob, power switch. Auto-tune, ramp-and-soak programs, alarm values, the optional '
        'high-temperature cut-off module and the cooling loop are not modelled (key layout and parameters undocumented).']
    return meta


def parr_4591_calibration_report():
    """Explicit assumptions and predicted response, never a factory calibration claim."""
    return {'catalog_id':'CHEM-018','is_measured_calibration':False,
        'evidence_status':'illustrative_effective_parameters_with_documented_power_and_cylinder_mass_scale',
        'documented_heater_power_W':700.0,'assumed_effective_heat_capacity_J_K':1000.0,
        'documented_cylinder_mass_kg':1.587573295,'assumed_steel_specific_heat_J_kg_K_for_scale_only':500.0,
        'documented_cylinder_mass_used_as_complete_assembly_mass':False,'whole_instrument_mass_used_as_component_mass':False,
        'capacity_derivation_note':'C=1000J/K is a simulation choice, giving initial700/1000=.7K/s. The known cylinder mass times assumed steel cp is about794J/K; heat participation and true alloy cp are unknown, so this is only a scale check.',
        'assumed_closed_heat_loss_W_K':.7,'heat_loss_derivation':'Assume140W=20% of700W losses at200K excess; UA=140/200=.7W/K.',
        'predicted_initial_heatup_K_s':.7,'predicted_ambient25C_to225C_s':-1000/.7*math.log(1-.7*200/700),
        'predicted_closed_passive_half_cooling_s':1000/.7*math.log(2),
        'manufacturer_heatup_time_s':None,'manufacturer_dynamic_comparison':'not_run; no4591 loaded heatup/cooldown curve validated.',
        'maximum_temperature_K':498.15,'seal_material':'FKM','vessel_volume_m3':25e-6,
        'maximum_gauge_pressure_Pa':207e5,'assumed_ambient_absolute_pressure_Pa':101325.0,'maximum_absolute_pressure_Pa':20801325.0,
        'thermal_equation':'C dT/dt=P-UA(T-Ta), exact piecewise exponential with target holding and no instantaneous cooling.',
        'closed_pressure_equation':'P_abs=nRT/V, n fixed while lid and vent closed.',
        'vent_equation':'dn/dt=(Pamb*V/(R*T(t))-n)/15s; integrating factor quadrature along exact T(t).',
        'vent_quadrature':'Adaptive Simpson estimated absolute tolerance1e-14 K^-1; exponentially weighted tail truncated after40tau; maximum remaining tail exp(-40)/273.15 K^-1; fail transaction if convergence criterion unmet.',
        'assumptions':['25mL entirely ideal-gas free volume; no liquid, swelling, real-gas effects or chemistry.','Heating/cooling effective node does not model energy transported by vent gas; no adiabatic blowdown.','20% thermal loss,1000J/K effective heat capacity,15s valve time constant and opening thresholds are simulation assumptions.','1700rpm standard pulley endpoint is documented; zero and continuous adjustment below it are an idealized drive model.'],
        'not_run':['manufacturer_dynamic_comparison','high_pressure_real_gas_validation','chemical_reaction','physical_interlock_validation','robot_execution','hardware_execution']}


def validate_parr_4591_runtime(output_directory=None):
    """Run concrete Parr tests and emit metadata plus dated layer-specific evidence."""
    import sys as _parr_sys
    output=Path(output_directory) if output_directory else Path(__file__).resolve().parents[1]/'outputs/basic40_batch02/t1_thermal/parr_4591'
    output.mkdir(parents=True,exist_ok=True)
    test_path=Path(__file__).resolve().parents[1]/'tests/test_basic40_thermal.py'
    spec=importlib.util.spec_from_file_location('_parr_runtime_validation_tests',test_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(module.Parr4591RuntimeTests),unittest.defaultTestLoader.loadTestsFromTestCase(module.Parr4591CoupledVentTests)])
    test_ids=[test.id() for group in suite for test in group]
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    checked_at=datetime.now(timezone.utc).isoformat()
    environment={'python':platform.python_version(),'platform':platform.platform(),'scene_backend':'recording callback only; Blender geometry verified separately'}
    failing={test.id() for test,detail in result.failures+result.errors}
    methods={
        'power.set':['test_parr_power_off_preserves_heat_and_pressure_then_passive_cooling'],
        'thermal.set_temperature':['test_parr_target_write_only_target_and_reads_do_not_advance','test_parr_overpressure_target_rejected_without_any_process_or_scene_effect','test_parr_closed_temperature_pressure_coupling_and_gas_conservation'],
        'heater.set_enabled':['test_parr_finite_700w_initial_slope_and_assumed_heatup','test_parr_open_lid_heater_and_stirring_interlocks'],
        'stirrer.set_speed':['test_parr_stirring_host_only_integration_and_independent_instances'],
        'lid.open':['test_parr_open_lid_pressure_interlock_and_no_hidden_venting','test_parr_open_lid_hot_temperature_interlock','test_parr_open_lid_heater_and_stirring_interlocks'],
        'lid.close':['test_parr_open_lid_pressure_interlock_and_no_hidden_venting'],
        'vent.set_open':['test_parr_vent_open_is_target_only_then_finite_pressure_relaxation','test_parr_vent_close_forecast_rejects_without_effect','test_parr_vent_heating_external_step_partition_invariance','test_parr_vent_cooling_and_setpoint_crossing_partition_invariance'],
    }
    checks=[]
    for cap,names in methods.items():
        for layer,selected in [('process',names),('host',['test_parr_real_public_api_results_and_observations_match_schemas','test_parr_agent_read_describe_results_logs_exclude_truth','test_parr_session_scope_and_host_separation','test_parr_request_replay_and_conflict','test_parr_malformed_arguments_reject_without_mutation'])]:
            matched=[test_id for test_id in test_ids if test_id.rsplit('.',1)[-1] in selected]
            passed=len(matched)==len(selected) and not any(test_id in failing for test_id in matched)
            checks.append({'id':'parr_'+cap.replace('.','_')+'_'+layer,'layer':layer,'capability_ids':[cap],'status':'passed' if passed else 'failed',
                'method':'Actual Parr4591Session describe/read/invoke/write and trusted Parr4591Runtime.step; unittest: '+', '.join(selected),
                'environment':environment,'checked_at':checked_at,'test_ids':matched,
                'evidence_note':'Synthetic process and recording callback verification; this check does not verify Blender, robot or real hardware.'})
    for test_id in test_ids:
        checks.append({'id':test_id.rsplit('.',1)[-1],'layer':'host','capability_ids':[], 'status':'failed' if test_id in failing else 'passed','method':'unittest '+test_id,'environment':environment,'checked_at':checked_at})
    report={'catalog_id':'CHEM-018','asset_type_id':PARR_4591_TYPE,'status':'passed' if result.wasSuccessful() else 'failed',
        'tests_run':result.testsRun,'failure_count':len(result.failures),'error_count':len(result.errors),'checked_at':checked_at,'environment':environment,'checks':checks,
        'limitations':['Tests use synthetic inert ideal-gas state and recording scene callbacks. Actual Blender evaluation belongs to scene_validation.json.','Manufacturer heatup/cooldown, reaction behavior, gas nonideality, physical valve/interlocks, robot and hardware are not validated.'],
        'not_run':['manufacturer_dynamic_comparison','robot','hardware','contact_dynamics'],
        'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'tests_sha256':hashlib.sha256(test_path.read_bytes()).hexdigest()}
    (output/'runtime_validation.txt').write_text(stream.getvalue())
    (output/'runtime_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (output/'runtime_metadata.json').write_text(json.dumps(export_parr_4591_metadata(),ensure_ascii=False,indent=2)+'\n')
    (output/'runtime_calibration.json').write_text(json.dumps(parr_4591_calibration_report(),ensure_ascii=False,indent=2)+'\n')
    return report

# BEGIN CHEM-021 Radleys Mya 4 independent runtime (append-only batch scope).
MYA4_TYPE = 'instrument.radleys_mya4'
MYA4_ZONES = tuple('zone_' + str(i) for i in range(1, 5))
MYA4_MIN_SPEED_RAD_S = 100 * 2 * math.pi / 60
MYA4_MAX_SPEED_RAD_S = 1000 * 2 * math.pi / 60
MYA4_ASSUMPTIONS = {
    'effective_block_capacity_J_K': 650.0,
    'effective_heating_limit_W': 300.0,
    'effective_cooling_limit_W': 140.0,
    'block_ambient_conductance_W_K': 0.8,
    'block_liquid_conductance_W_K': 6.0,
    'adjacent_block_conductance_W_K': 0.03,
    'liquid_density_kg_m3': 1000.0,
    'liquid_specific_heat_J_kg_K': 4180.0,
    'feedback_gain_W_K': 20.0,
    'maximum_integration_step_s': 0.25,
    'program_arrival_tolerance_K': 0.5,
}


def _mya4_object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _mya4_num(unit, minimum=None, maximum=None):
    info = {'type': 'number', 'x-unit': unit}
    if minimum is not None: info['minimum'] = minimum
    if maximum is not None: info['maximum'] = maximum
    return info


def export_radleys_mya4_metadata(panel=True):
    """Static discovery metadata; never includes live sample or process truth.  The Control Pad functions behind the front panel are
    added in separate groups (controller_capabilities, evaluator_capabilities, panel_capabilities); panel=False skips panel_ui.json."""
    boolean, zone_arg = {'type': 'boolean'}, {'type': 'integer', 'minimum': 0, 'maximum': 3, 'x-unit': '1'}
    probe = {'type': 'string', 'enum': ['block', 'liquid']}
    segments = {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': _mya4_object({'temperature_K': _mya4_num('K', 243.15, 453.15), 'hold_s': _mya4_num('s', 0, 86400)})}
    slots = {}
    def slot(sid, unit, origin, schema, route=None, visibility='agent'):
        slots[sid] = {'unit': unit, 'origin': origin, 'visibility': visibility, 'access': 'read_write' if route else 'read_only', 'value_schema': schema,
            'reference_conditions': None, 'write_via': route, 'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None},
            'read_binding': "RadleysMya4Session.read(instance_id=..., state_ids=['%s'])" % sid}
    def route(cap, parameter, index=None):
        return {'capability_id': cap, 'parameter': parameter, 'fixed_arguments': {} if index is None else {'zone_index': index}}
    slot('power.enabled', '1', 'setpoint', boolean, route('power.set', 'enabled'))
    slot('cooling.available', '1', 'derived', boolean)
    slot('cooling.temperature_k', 'K', 'setpoint', _mya4_num('K', 278.15, 308.15))
    slot('cooling.flow_m3_s', 'm^3/s', 'setpoint', _mya4_num('m^3/s', 2e-3/60, 5e-3/60))
    for i, z in enumerate(MYA4_ZONES):
        for suffix, unit, origin, schema, routing, visibility in [
            ('thermal.target_k', 'K', 'setpoint', _mya4_num('K', 243.15, 453.15), route('thermal.set_temperature', 'temperature_K', i), 'agent'),
            ('thermal.enabled', '1', 'setpoint', boolean, route('thermal.set_enabled', 'enabled', i), 'agent'),
            ('thermal.probe_mode', '1', 'setpoint', probe, route('thermal.set_probe_mode', 'mode', i), 'agent'),
            ('thermal.temperature_k', 'K', 'process_truth', _mya4_num('K'), None, 'debug'),
            ('thermal.liquid_temperature_k', 'K', 'process_truth', _mya4_num('K'), None, 'debug'),
            ('thermal.indicated_temperature_k', 'K', 'derived', _mya4_num('K'), None, 'agent'),
            ('thermal.indicated_liquid_temperature_k', 'K', 'derived', _mya4_num('K'), None, 'agent'),
            ('thermal.control_energy_j', 'J', 'process_truth', _mya4_num('J'), None, 'debug'),
            ('thermal.environment_energy_j', 'J', 'process_truth', _mya4_num('J'), None, 'debug'),
            ('thermal.adjacent_energy_j', 'J', 'process_truth', _mya4_num('J'), None, 'debug'),
            ('stirring.target_rad_s', 'rad/s', 'setpoint', _mya4_num('rad/s', MYA4_MIN_SPEED_RAD_S, MYA4_MAX_SPEED_RAD_S), route('stirring.set_speed', 'angular_speed_rad_s', i), 'agent'),
            ('stirring.enabled', '1', 'setpoint', boolean, route('stirring.set_enabled', 'enabled', i), 'agent'),
            ('stirring.actual_rad_s', 'rad/s', 'derived', _mya4_num('rad/s', 0, MYA4_MAX_SPEED_RAD_S), None, 'agent'),
            ('stirring.angle_rad', 'rad', 'scene', _mya4_num('rad', 0, 2*math.pi), None, 'agent'),
            ('vessel.capacity_m3', 'm^3', 'setpoint', _mya4_num('m^3', 2e-6, 4e-4), None, 'agent'),
            ('vessel.fill_volume_m3', 'm^3', 'setpoint', _mya4_num('m^3', 1e-6, 4e-4), None, 'agent'),
            ('program.segments', '1', 'setpoint', {**segments, 'minItems': 0}, None, 'agent'),
            ('program.running', '1', 'derived', boolean, None, 'agent'),
            ('program.phase', '1', 'derived', {'type': 'string', 'enum': ['idle', 'approaching', 'holding', 'completed', 'stopped', 'interrupted_cooling', 'interrupted_power']}, None, 'agent'),
            ('program.segment_index', '1', 'derived', {'type': 'integer', 'minimum': 0, 'maximum': 16, 'x-unit': '1'}, None, 'agent'),
            ('program.hold_elapsed_s', 's', 'derived', _mya4_num('s', 0), None, 'agent'),
            ('program.reaction_elapsed_s', 's', 'derived', _mya4_num('s', 0), None, 'agent'),
        ]: slot(z + '.' + suffix, unit, origin, schema, routing, visibility)
    defs = {
        'power.set': ({'enabled': boolean}, {'enabled': boolean}, {'enabled': True}, '设置整机供电。断电停止四区温控、温程及搅拌，保留已发生温度与转子相位。'),
        'thermal.set_temperature': ({'zone_index': zone_arg, 'temperature_K': _mya4_num('K', 243.15, 453.15)}, {'zone_index': zone_arg, 'setpoint_K': _mya4_num('K')}, {'zone_index': 0, 'temperature_K': 333.15}, '只写指定区目标；块模式243.15..453.15K、液模式253.15..448.15K（说明书p.161：溶液−20/−25..175 °C），冷端还受冷源限制。'),
        'thermal.set_enabled': ({'zone_index': zone_arg, 'enabled': boolean}, {'zone_index': zone_arg, 'enabled': boolean}, {'zone_index': 0, 'enabled': True}, '启用或停用指定区虚拟温控；启用要求整机供电及有效冷却水。'),
        'thermal.set_probe_mode': ({'zone_index': zone_arg, 'mode': probe}, {'zone_index': zone_arg, 'mode': probe}, {'zone_index': 0, 'mode': 'liquid'}, '选择块温或液温反馈；真实两路探头噪声、采样周期及PID参数没有标定。'),
        'stirring.set_speed': ({'zone_index': zone_arg, 'angular_speed_rad_s': _mya4_num('rad/s', MYA4_MIN_SPEED_RAD_S, MYA4_MAX_SPEED_RAD_S)}, {'zone_index': zone_arg, 'angular_speed_rad_s': _mya4_num('rad/s')}, {'zone_index': 0, 'angular_speed_rad_s': 500*2*math.pi/60}, '独立设置磁搅拌角速度；100..1000rpm显式换算为rad/s，理想瞬时转速不代表真实马达。'),
        'stirring.set_enabled': ({'zone_index': zone_arg, 'enabled': boolean}, {'zone_index': zone_arg, 'enabled': boolean}, {'zone_index': 0, 'enabled': True}, '启停指定区磁搅拌；转子相位只由宿主step推进。'),
        'vessel.configure': ({'zone_index': zone_arg, 'capacity_m3': _mya4_num('m^3', 2e-6, 4e-4), 'fill_volume_m3': _mya4_num('m^3', 1e-6, 4e-4)}, {'zone_index': zone_arg, 'capacity_m3': _mya4_num('m^3'), 'fill_volume_m3': _mya4_num('m^3')}, {'zone_index': 0, 'capacity_m3': 1e-4, 'fill_volume_m3': 5e-5}, '初始化虚拟容器容量与有效液量，非移液动作；仅允许初始环境平衡且本区停止时配置，液量不得大于容量。'),
        'thermal.configure_program': ({'zone_index': zone_arg, 'segments': segments}, {'zone_index': zone_arg, 'segment_count': {'type': 'integer', 'minimum': 1, 'maximum': 16, 'x-unit': '1'}}, {'zone_index': 0, 'segments': [{'temperature_K': 333.15, 'hold_s': 60}]}, '配置每区独立到温持时温程；最多16段为运行层假设，不声称厂家存储限制。'),
        'thermal.start_program': ({'zone_index': zone_arg}, {'zone_index': zone_arg, 'running': boolean}, {'zone_index': 0}, '启动本区温程及温控；到温容差0.5K后计持时，反应时间仅计时，不求解化学反应。'),
        'thermal.stop_program': ({'zone_index': zone_arg}, {'zone_index': zone_arg, 'running': boolean}, {'zone_index': 0}, '停止本区温程与温控，保留已产生热状态、搅拌和计时。'),
    }
    caps = {}
    zfields = {z: [sid for sid in slots if sid.startswith(z + '.')] for z in MYA4_ZONES}
    changed = {
        'power.set': ['power.enabled'] + [z+'.'+f for z in MYA4_ZONES for f in ('thermal.enabled','stirring.enabled','stirring.actual_rad_s','program.running','program.phase')],
        'thermal.set_temperature': [z+'.thermal.target_k' for z in MYA4_ZONES],
        'thermal.set_enabled': [z+'.'+f for z in MYA4_ZONES for f in ('thermal.enabled','program.running','program.phase')],
        'thermal.set_probe_mode': [z+'.thermal.probe_mode' for z in MYA4_ZONES],
        'stirring.set_speed': [z+'.'+f for z in MYA4_ZONES for f in ('stirring.target_rad_s','stirring.actual_rad_s')],
        'stirring.set_enabled': [z+'.'+f for z in MYA4_ZONES for f in ('stirring.enabled','stirring.actual_rad_s')],
        'vessel.configure': [z+'.'+f for z in MYA4_ZONES for f in ('vessel.capacity_m3','vessel.fill_volume_m3')],
        'thermal.configure_program': [z+'.'+f for z in MYA4_ZONES for f in ('program.segments','program.segment_index','program.hold_elapsed_s','program.reaction_elapsed_s','program.phase')],
        'thermal.start_program': [z+'.'+f for z in MYA4_ZONES for f in ('program.running','program.phase','program.segment_index','program.hold_elapsed_s','program.reaction_elapsed_s','thermal.target_k','thermal.enabled')],
        'thermal.stop_program': [z+'.'+f for z in MYA4_ZONES for f in ('program.running','program.phase','thermal.enabled')],
    }
    for name, (ins, outs, example, desc) in defs.items():
        prerequisites = ['所有参数严格符合schema；zone_index仅定位所选区；拒绝时不改状态或时钟。']
        if name.startswith('thermal.') or name == 'stirring.set_speed': prerequisites += ['除停止/禁用命令外需整机供电。']
        if name in ('thermal.set_enabled','thermal.start_program'): prerequisites += ['启用需宿主确认冷源可用、2..5L/min且5..35°C；低于环境的目标另受冷源最低可达温度限制。']
        if name in ('thermal.set_temperature','thermal.set_probe_mode','thermal.configure_program'): prerequisites += ['本区温程未运行；目标须符合当前探头模式及全部区目标跨度<=200K。']
        if name == 'vessel.configure': prerequisites += ['本区温控、搅拌、程序均停止；块温和液温在环境温度±1e-9K内。']
        if name in ('thermal.set_temperature','thermal.set_probe_mode','thermal.configure_program','thermal.set_enabled','thermal.start_program'): prerequisites += ['目标（及温程各段）须在本区Control Pad安全限值内：块模式Min/Max Tb，液模式Min/Max Tr（说明书p.101：设了安全限值后不能输入限值外的值；出厂限值即全范围）。']
        effects = [{'state_id': sid, 'timing': 'immediate', 'description_zh': '仅所选区发生所声明配置/控制变化；power.set可涉及四区。'} for sid in changed[name]]
        if True:
            effects += [{'state_id': z+'.'+f, 'timing': 'on_step', 'description_zh': '宿主积分推进块/液温、理想显示、热量账目、转子和温程；跨区导热允许未启用邻区缓慢改变温度。'} for z in MYA4_ZONES for f in ('thermal.temperature_k','thermal.liquid_temperature_k','thermal.indicated_temperature_k','thermal.indicated_liquid_temperature_k','thermal.control_energy_j','thermal.environment_energy_j','thermal.adjacent_energy_j','stirring.angle_rad')]
        if name in ('thermal.start_program','thermal.configure_program'):
            effects += [{'state_id': z+'.'+f, 'timing': 'on_step', 'description_zh': '推进所配置温程并在完成后停本区温控。'} for z in MYA4_ZONES for f in ('thermal.target_k','thermal.enabled','program.running','program.phase','program.segment_index','program.hold_elapsed_s','program.reaction_elapsed_s')]
        caps[name] = {'availability': 'implemented','visibility':'agent','kind':'configuration' if name in ('vessel.configure','thermal.configure_program') else 'device_command',
            'description_zh':desc,'input_schema':_mya4_object(ins),'output_schema':_mya4_object(outs),'example_arguments':[example], 'state_effects':effects,
            'observes': [sid for sid in changed[name] if slots[sid]['visibility']=='agent'], 'preconditions_zh':prerequisites,
            'binding':"basic40_runtime_thermal.RadleysMya4Session.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % name,
            'completion_criterion_zh':'命令返回确认控制或配置写入；过程变化必须随后宿主step并读状态确认。',
            'scene_backend_required':False,
            'errors':[{'code':c,'description_zh':'运行层明确拒绝或后端失败；返回结果不暴露过程真值。','side_effects':'unknown' if c=='DEVICE_ERROR' else 'none'} for c in ('INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','ACCESS_DENIED','DEVICE_ERROR')]}
    declared = {name: {'availability':'unsupported','visibility':'agent','kind':'device_command','description_zh':desc,'input_schema':_mya4_object({}),'output_schema':_mya4_object({}),'example_arguments':[{}],'state_effects':[],'observes':[],'binding':None,'scene_backend_required':False} for name,desc in {
        'hardware.connect':'没有Radleys真实硬件驱动。','robot.transfer':'未实现机器人及液体物质转移。','chemistry.react':'未实现反应动力学、产物与反应热。','stirring.overhead':'本配置为标准磁搅拌；未建可选上置搅拌器。','thermal.ramp_program':'本模型仅到温持时段；厂家斜坡程序和PID调参未实现。'}.items()}
    params = {name:{'value':None,'unit':unit,'evidence':'unknown','source':'厂家公开资料未提供可用于本模型的部件数值；不以整机2kW替代。'} for name,unit in [('factory_zone_heater_power_W','W'),('factory_zone_cooling_power_W','W'),('factory_block_capacity_J_K','J/K'),('factory_block_ambient_conductance_W_K','W/K'),('factory_heatup_time_s','s'),('factory_cooldown_time_s','s')]}
    params['whole_instrument_maximum_consumption_W'] = {'value':2000.0,'unit':'W','evidence':'public_document','source':'Radleys Mya 4 Technical Specification October 2023 p1; maximum power consumption, not per-zone heat input.'}
    units = ['J/K','W','W','W/K','W/K','W/K','kg/m^3','J/(kg*K)','W/K','s','K']
    for (name,value),unit in zip(MYA4_ASSUMPTIONS.items(),units): params['simulation_'+name]={'value':value,'unit':unit,'evidence':'assumption','source':'Named synthetic 100mL vessel / 50mL waterlike reference. Uncalibrated; no factory heat-up/cooldown curve identified.'}
    limitations = ['Four block/liquid lumped nodes with weak symmetric cross-zone conduction; unknown real component constants stay null. All numerical coefficients are explicit simulation assumptions, not a calibration.',
        'Cooling must be available for heating and cooling; host configuration asserts inlet conditions only. Flow, coolant outlet rise, pump pressure and chiller dynamics are not solved.',
        'Virtual 100mL vessels initially contain 50mL waterlike load; capacity and effective fill are distinct SI quantities. Configuration is initialization, not matter transfer.',
        'Magnetic rotors use ideal instantaneous speed and +Z phase; slip, viscosity, inertia, vortexes and overhead stirring are not solved.',
        'Displayed block/liquid temperatures are rounded 0.1K ideal estimates; vendor accuracy and two-second hardware monitoring are not reproduced.',
        'Read never advances the world. Host RK4 maximum substep0.25s; programmed arrival/completion event timing resolution is bounded by one substep.',
        'Agent sessions filter API metadata, reads, responses, logs and scene payloads. This is not an OS/Python-process/filesystem isolation boundary.',
        'No hardware, robot, pressure seal, hazardous-reaction, chemical kinetics, automatic liquid-transfer or manufacturer PID calibration claims.']
    return _mya4_extend_metadata({'asset_types':{MYA4_TYPE:{'catalog_id':'CHEM-021','model':'Radleys Mya 4; standard magnetic stirring configuration','asset_version':'1.1.0','state_slots':slots,'capabilities':caps,'declared_capabilities':declared,'parameters':params}},
        'host_capabilities':{'sim.step':{'kind':'simulation','visibility':'trusted_host_only','input_schema':_mya4_object({'dt_s':_mya4_num('s',0,86400)}),'binding':'basic40_runtime_thermal.RadleysMya4Runtime.step(dt_s)'},
            'sim.configure_cooling_source':{'kind':'configuration','visibility':'trusted_host_only','binding':'RadleysMya4Runtime.configure_cooling_source(available=True, temperature_K=278.15, flow_m3_s=0.003/60)'}},
        'limitations':limitations,'equations':['Cb*dTb_i/dt=Qcontrol_i-UA*(Tb_i-Ta)-G*(Tb_i-Tl_i)+sum_j(Kij*(Tb_j-Tb_i))','Cl_i*dTl_i/dt=G*(Tb_i-Tl_i); Cl_i=rho*cp*fill_volume_m3','Qcontrol_i=clip(Kp*(target_i-Tfeedback_i)+UA*(target_i-Ta),-Pcool,Pheat)','Cross-zone nearest-neighbor pairs(1,2),(2,3),(3,4), with symmetric Kij; dtheta_i/dt=angular_speed_i; RK4 integration.'],
        'scene_callback':'scene_adapter(instance_id, payload) receives a flat dict of agent-visible state only; zone_N.stirring.angle_rad maps to rotor +Z rotation, SI.',
        'is_measured_calibration':False}, panel)


# ---- G02 front panel: the Mya 4 Control Pad software (experiments, Manual / Profile runs, safety limits and errors, File Manager, System
# Settings) behind panel_ui.json.  Mya 4 instructions issue 16 (PDF pages 'OM p.N').  The pad drives the four zones of this runtime through
# their setpoint, control variable, temperature control and stirring; it adds the linear From -> To profile steps with Time / Value end
# conditions, Pause / Skip / Stop, the zone safety limits with their errors and the 7-day limit.
from datetime import timedelta as _pad_timedelta

MYA4_ASSET_DIR = Path(__file__).resolve().parents[1] / 'assets/basic40_batch02/t1_thermal/radleys_mya4'
_PAD_CLOCK_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)   # pad clock at simulation time 0 until set (assumption)
_PAD_OPTIONS = ('A', 'B', 'C', 'D')
_PAD_LIMIT_RANGES = {'max_speed_rpm': (100, 1000), 'min_tb_C': (-30, 180), 'max_tb_C': (-30, 180), 'min_tr_C': (-25, 175), 'max_tr_C': (-25, 175),
                     'max_dt_K': (1, 50)}                       # OM p.104-105: the factory defaults are the outer bounds
_PAD_LIMITS_DEFAULT = {'max_speed_rpm': 1000, 'min_tb_C': -30, 'max_tb_C': 180, 'min_tr_C': -25, 'max_tr_C': 175, 'max_dt_K': 50}
_PAD_TOL_K = 2.0                 # OM p.102 scr: "All temperature safety limits have a 2C tolerance" (applied to the error trips: assumption)
_PAD_STEP_DEFAULT = {'duration_min': 30, 'speed_rpm': 0, 'from_C': 20, 'to_C': 20, 'control': 'tb', 'end': 'value'}   # OM p.94 scr
_PAD_MAX_STEPS = 10              # OM p.93-94
_PAD_DURATION_MAX_MIN = 960      # OM p.95: 1 .. 960 min
_PAD_REACHED_K = 0.5             # End Condition Value: To counts as reached within 0.5 K (assumption; the runtime's arrival band)
_PAD_WARN_RUN_S, _PAD_MAX_RUN_S = 6 * 86400.0, 7 * 86400.0   # OM p.82, p.159: Z18 after 6 days, Z17 at 7 days
_PAD_NAME_MAX = 17               # OM p.107: names up to 17 characters, letters and numbers
_PAD_NAME_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -')
_PAD_DATA_MAX = 5000             # data points kept per experiment in the simulation (older points thinned)
_PAD_PID_LOG_COLUMNS = ('elapsed_s', 'option', 'block_P', 'block_I', 'block_D', 'reaction_P', 'reaction_I', 'reaction_D', 'block_p_share_W', 'block_i_share_W',
                        'block_d_share_W', 'peltier_output_W', 'block_setpoint_C', 'reaction_p_share_K', 'reaction_i_share_K', 'reaction_d_share_K')
# PID data logging (OM p.130: 'data logging of the PID settings'; p.153: 'generate the file to study their effect'): one row per data point; the
# columns are an assumption (the manual shows no PID file)
_PAD_PID_DEFAULT = {'A': {'block': [32.0, 0.9, 10.0], 'reaction': [2.9, 0.01, 10.0]}, 'B': {'block': [32.0, 0.9, 10.0], 'reaction': [5.0, 0.01, 40.0]},
                    'C': {'block': [32.0, 0.9, 10.0], 'reaction': [1.8, 0.005, 40.0]}, 'D': {'block': [32.0, 0.9, 10.0], 'reaction': [1.0, 0.002, 40.0]}}   # OM p.130 scr
_PAD_NETWORK_DEFAULT = {'ip': [192, 168, 1, 10], 'subnet': [255, 255, 255, 0], 'gateway': [0, 0, 0, 0]}   # OM p.116 scr
MYA4_PID_TS = 0.25                                                            # controller sample period (s), assumption
MYA4_BLOCK_W_PER_P = 0.625                                                    # W/K per unit of block P (assumption: factory 32 -> 20 W/K)


def _mya4_block_terms(p, i, d):
    """Block loop (output Peltier power W): kp = 0.625 P W/K, ti = 200 s / I, td = D s.  The units are not documented (OM p.130, p.152-153); the
    scales are chosen so that the factory option (32 / 0.9 / 10) heats as the earlier 20 W/K controller did, without overshoot (assumption)."""
    return MYA4_BLOCK_W_PER_P * p, (200.0 / i if i > 0 else 0.0), d


def _mya4_reaction_terms(p, i, d):
    """Reaction loop (cascade; output = block set point offset K): kp = 2 P K/K, ti = 1 s / I, td = D s (assumption, factory option A settles Tr
    in about 6 min without more than 0.2 K overshoot)."""
    return 2.0 * p, (1.0 / i if i > 0 else 0.0), d


def _mya4_pid_idle():
    return {'u': 0.0, 'sp_block_k': None, 'b': {'i': 0.0, 'e_prev': None, 'df': 0.0, 'p': 0.0, 'd': 0.0, 'out': 0.0},
            'r': {'i': 0.0, 'e_prev': None, 'df': 0.0, 'p': 0.0, 'd': 0.0, 'out': 0.0}}
_PAD_ERRORS = {   # OM p.159-160: code -> (class, reported text, safe state)
    'S1': ('RZ', 'Magnetic stirrer has exceeded defined setpoint for zone'), 'S2': ('RZ', 'Overhead stirrer has exceeded defined setpoint for zone'),
    'S3': ('RZ', 'System configuration changed'), 'S4': ('RZ', 'System configuration changed'), 'Z1': ('RZ', 'Zone communication error'),
    'Z2': ('RZ', '3V3 supply fault'), 'Z3': ('RZ', '5V supply fault'), 'Z4': ('RZ', '27V supply fault'), 'Z5': ('RZ', '12V supply fault'),
    'Z6': ('YZ', 'Block sensor fault'), 'Z7': ('YZ', 'Heat exchanger sensor fault'), 'Z8': ('YZ', 'PCB too hot or sensor fault'), 'Z9': ('RZ', 'Overhead stirrer fault'),
    'Z10': ('RZ', 'Magnetic stirrer fault'), 'Z11': ('YZ', 'Peltier fault'), 'Z12': ('YZ', 'Heater 1 fault'), 'Z13': ('YZ', 'Heater 2 fault'), 'Z14': ('YZ', 'Heater 3 fault'),
    'Z15': ('YZ', 'Heater 4 fault'), 'Z16': ('YZ', 'Thermal fuse fault'), 'Z17': ('RZ', 'Reaction has reached the maximum run time of 7 days'),
    'Z18': ('W', 'Reaction is approaching the maximum run time of 7 days'), 'M1': ('RS', '3V3 supply fault'), 'M2': ('RS', '5V supply fault'), 'M3': ('RS', '27V supply fault'),
    'M4': ('RS', '3V3Q supply fault'), 'M5': ('RS', 'EEPROM missing'), 'M6': ('RS', 'ADC missing'), 'M7': ('RS', 'Control PCB too hot or sensor fault'),
    'T1': ('YZ', 'Coolant flow rate too low'), 'T2': ('YZ', 'Heat exchangers over temperature'), 'T3': ('YZ', 'Block over temperature'), 'T4': ('YZ', 'Block under temperature'),
    'T5': ('YZ', 'Reaction over temperature'), 'T6': ('YZ', 'Reaction under temperature'), 'T9': ('YZ', '(Tb-Tr) exceeded'), 'T10': ('YZ', 'System configuration changed'),
    'T11': ('YZ', 'System configuration changed'), 'T12': ('YZ', 'Block temperature over maximum system operating range'), 'T13': ('YZ', 'Coolant temperature too low')}
_PAD_INJECTABLE = tuple(k for k in _PAD_ERRORS if k not in ('T1', 'T3', 'T4', 'T5', 'T6', 'T9', 'Z17', 'Z18'))   # the others the simulation raises itself
_PAD_COMPUTED = ('pad.clock', 'pad.status', 'pad.service')
_PAD_SOFTWARE = {'controller': '1.0.1.1', 'firmware': '1.001', 'zone_firmware': '1.0'}   # OM p.128 scr (version strings shown there)


def _pad_rpm(rad_s):
    return rad_s * 60.0 / (2 * math.pi)


def _pad_rad(rpm):
    return rpm * 2 * math.pi / 60.0


def _pad_new_experiment(name, inherit=None):
    """A new experiment (OM p.107): one default step, the zone's current option and limits (inherited), Manual mode."""
    src = inherit or {}
    return {'name': name, 'mode': src.get('mode', 'manual'), 'option': src.get('option', 'A'), 'limits': copy.deepcopy(src.get('limits', _PAD_LIMITS_DEFAULT)),
            'manual': {'setpoint_C': 20, 'control': 'tb', 'speed_rpm': 0}, 'steps': [dict(_PAD_STEP_DEFAULT)], 'status': 'new', 'started': None, 'ended': None,
            'result': None, 'saved': False, 'data': []}


_PAD_INT = lambda lo=None, hi=None, unit='1': {'type': 'integer', 'x-unit': unit, **({'minimum': lo} if lo is not None else {}), **({'maximum': hi} if hi is not None else {})}   # noqa: E731
_PAD_NUM = lambda unit, lo=None, hi=None: _mya4_num(unit, lo, hi)   # noqa: E731
_PAD_ZONE = {'type': 'integer', 'minimum': 0, 'maximum': 3, 'x-unit': '1'}
_PAD_NAME = {'type': 'string'}
_PAD_STEP_ARGS = {'duration_min': _PAD_INT(1, _PAD_DURATION_MAX_MIN, 'min'), 'speed_rpm': _PAD_INT(0, 1000, 'rpm'), 'from_C': _PAD_INT(-30, 180, 'degC'),
                  'to_C': _PAD_INT(-30, 180, 'degC'), 'control': {'type': 'string', 'enum': ['tb', 'tr']}, 'end': {'type': 'string', 'enum': ['value', 'time']}}
_PAD_CAPS = {   # name -> (kind, properties, optional, output, observes, meaning)
    'pad.new_experiment': ('configuration', {'zone_index': _PAD_ZONE, 'name': _PAD_NAME}, (), {'name': _PAD_NAME}, ['experiment'],
                           'New experiment（OM p.107）：本区新建实验（名称≤17字符、字母数字，全系统唯一），继承本区当前选项与安全限值，一个默认步骤，Manual模式；未保存'),
    'pad.open_experiment': ('configuration', {'zone_index': _PAD_ZONE, 'source': _PAD_NAME, 'name': _PAD_NAME}, (), {'name': _PAD_NAME}, ['experiment'],
                            'Open / File Manager装入（OM p.108、p.112–114）：把已存实验以新名称装入本区（设定、步骤、选项与限值覆盖本区）；运行中的区或正在运行的实验不能装入'),
    'pad.save_experiment': ('configuration', {'zone_index': _PAD_ZONE}, (), {'name': _PAD_NAME}, ['experiment', 'library'], 'Save experiment（OM p.109）：存入实验库，去掉*'),
    'pad.save_experiment_as': ('configuration', {'zone_index': _PAD_ZONE, 'name': _PAD_NAME}, (), {'name': _PAD_NAME}, ['experiment', 'library'],
                               'Save experiment as（OM p.110）：以新名称存一份（含选项与安全限值），本区随后使用新名称；已开始的实验不能复制'),
    'pad.set_mode': ('configuration', {'zone_index': _PAD_ZONE, 'mode': {'type': 'string', 'enum': ['manual', 'profile']}}, (), {'mode': {'type': 'string'}}, ['experiment'],
                     'Run Mode Manual | Profile（OM p.89）：实验开始后不能更改'),
    'pad.set_option': ('configuration', {'zone_index': _PAD_ZONE, 'option': {'type': 'string', 'enum': list(_PAD_OPTIONS)}}, (), {'option': {'type': 'string'}}, ['experiment'],
                       'Temperature Control选项A–D（OM p.102–103）：保存于实验；运行中锁定'),
    'pad.set_limits': ('configuration', {'zone_index': _PAD_ZONE, **{k: _PAD_INT(lo, hi, 'rpm' if k.endswith('rpm') else 'K' if k.endswith('_K') else 'degC') for k, (lo, hi) in _PAD_LIMIT_RANGES.items()}},
                       '*', {'zone_index': _PAD_ZONE}, ['experiment'],
                       'Safety Limits（OM p.104–105）：最大搅拌速度、Min/Max Tb、Min/Max Tr、最大Tb−Tr；出厂值为外边界，Min≤Max，Tr限值在Tb限值内；运行中锁定'),
    'pad.set_manual': ('configuration', {'zone_index': _PAD_ZONE, 'setpoint_C': _PAD_INT(-30, 180, 'degC'), 'control': {'type': 'string', 'enum': ['tb', 'tr']},
                                         'speed_rpm': _PAD_INT(0, 1000, 'rpm')}, '*', {'zone_index': _PAD_ZONE}, ['experiment'],
                       'Manual模式设定（OM p.90–92）：温度设定（1 °C，安全限值内）、Tb/Tr控制、搅拌速度（100..最大或0暂停搅拌）；运行中可改'),
    'pad.set_temperature_control': ('device_command', {'zone_index': _PAD_ZONE, 'on': {'type': 'boolean'}}, (), {'on': {'type': 'boolean'}}, ['run'],
                                    'Manual模式温度控制开关（OM p.91）：第一次打开即开始实验；温度与搅拌控制都关闭时实验结束（不能再开始）'),
    'pad.set_stirrer_control': ('device_command', {'zone_index': _PAD_ZONE, 'on': {'type': 'boolean'}}, (), {'on': {'type': 'boolean'}}, ['run'],
                                'Manual模式搅拌控制开关（OM p.92）'),
    'pad.set_step': ('configuration', {'zone_index': _PAD_ZONE, 'index': _PAD_INT(0, _PAD_MAX_STEPS - 1), **_PAD_STEP_ARGS}, (), {'index': _PAD_INT()}, ['experiment'],
                     'Profile步骤（OM p.94–97）：时长1–960 min、搅拌速度（0或100..最大）、From/To温度（安全限值内）、Tb/Tr、结束条件Value/Time；已开始的步骤不能改'),
    'pad.add_step': ('configuration', {'zone_index': _PAD_ZONE, 'index': _PAD_INT(0, _PAD_MAX_STEPS - 1)}, (), {'count': _PAD_INT()}, ['experiment'],
                     '+：在该步之后加一步（复制该步），最多10步（OM p.94）'),
    'pad.delete_step': ('configuration', {'zone_index': _PAD_ZONE, 'index': _PAD_INT(0, _PAD_MAX_STEPS - 1)}, (), {'count': _PAD_INT()}, ['experiment'],
                        'x：删除一步（至少保留一步；已开始的步骤不能删）'),
    'pad.profile_start': ('device_command', {'zone_index': _PAD_ZONE}, (), {'status': {'type': 'string'}}, ['run', 'experiment'],
                          'Start / 继续（OM p.98）：从第一步开始运行Profile，或从暂停继续；需上电、冷却水'),
    'pad.profile_pause': ('device_command', {'zone_index': _PAD_ZONE}, (), {'status': {'type': 'string'}}, ['run'], 'Pause：保持当前温度与搅拌（步骤计时停止为假设）'),
    'pad.profile_skip': ('device_command', {'zone_index': _PAD_ZONE}, (), {'status': {'type': 'string'}}, ['run'], 'Skip：立即结束当前步骤，开始下一步'),
    'pad.profile_stop': ('device_command', {'zone_index': _PAD_ZONE}, (), {'status': {'type': 'string'}}, ['run', 'experiment'], 'Stop：停止温控与搅拌，实验结束，不能继续'),
    'pad.file_export': ('device_command', {'name': _PAD_NAME, 'target': {'type': 'string', 'enum': ['share', 'usb']}}, (), {'files': {'type': 'array', 'items': {'type': 'string'}}},
                        ['library'], 'File Export（OM p.115–117）：导出.expc与.csv到Share（控制板内部）；没有U盘时USB导出被拒绝'),
    'pad.file_import': ('device_command', {'file': _PAD_NAME}, (), {'name': _PAD_NAME}, ['library'], 'Import（OM p.118）：从Share导入.expc（设定）与.csv（数据）到实验库'),
    'pad.file_delete': ('device_command', {'name': _PAD_NAME}, (), {'name': _PAD_NAME}, ['library'], 'Delete（OM p.119）：永久删除；装在任一区的实验不能删'),
    'pad.configure': ('configuration', {'storage_interval_s': _PAD_INT(2, 360, 's'), 'demo_mode': {'type': 'boolean'}, 'pid_logging': {'type': 'boolean'}}, '*', {'ok': {'type': 'boolean'}},
                      ['settings'], 'System Settings（OM p.126–127、p.130）：数据存储间隔（偶数2–360 s，奇数向上取偶）、Demo模式、PID记录（开启后此后开始的运行在每个数据点同时记录'
                      '所用选项的块/反应P、I、D值与控制器的P/I/D份额、Peltier输出W、串级块设定，存入实验的pid_data，File Export另写<名称>_PID.csv，OM p.130、p.153）；任一区运行时不能改'),
    'pad.set_clock': ('configuration', {'year': _PAD_INT(2000, 2099), 'month': _PAD_INT(1, 12), 'day': _PAD_INT(1, 31), 'hour': _PAD_INT(0, 23), 'minute': _PAD_INT(0, 59),
                                        'second': _PAD_INT(0, 59)}, '*', {'clock': {'type': 'string'}}, ['settings'], 'Internal Clock（OM p.127）：年月日时分秒'),
    'pad.set_pid': ('configuration', {'option': {'type': 'string', 'enum': list(_PAD_OPTIONS)}, 'loop': {'type': 'string', 'enum': ['block', 'reaction']},
                                      'term': {'type': 'string', 'enum': ['P', 'I', 'D']}, 'value': _PAD_NUM('1', 0, 1000)}, (), {'ok': {'type': 'boolean'}}, ['settings'],
                    'PID Gains（密码，OM p.130、p.152–153）：选项A–D的块/反应PID；Tb控温用块PID，Tr控温为反应PID给出块设定、块PID驱动Peltier（串级）；运行中不能改'),
    'pad.restore_pid': ('configuration', {}, (), {'ok': {'type': 'boolean'}}, ['settings'], 'Restore factory defaults（PID Gains）'),
    'pad.set_network': ('configuration', {'ip': {'type': 'array', 'minItems': 4, 'maxItems': 4, 'items': _PAD_INT(0, 255)}, 'subnet': {'type': 'array', 'minItems': 4, 'maxItems': 4, 'items': _PAD_INT(0, 255)},
                                          'gateway': {'type': 'array', 'minItems': 4, 'maxItems': 4, 'items': _PAD_INT(0, 255)}}, '*', {'ok': {'type': 'boolean'}}, ['settings'],
                        'Network Share（OM p.116、p.131）：Share文件夹的IP地址、子网掩码、默认网关'),
    'pad.reset_network': ('configuration', {}, (), {'ok': {'type': 'boolean'}}, ['settings'], 'Network Share：Reset to Defaults'),
    'pad.export_service': ('device_command', {'kind': {'type': 'string', 'enum': ['controller', 'error_log']}, 'target': {'type': 'string', 'enum': ['share', 'usb']}}, (),
                           {'files': {'type': 'array', 'items': {'type': 'string'}}}, ['library'],
                           'Service Settings（密码，OM p.128–129）：导出控制器设定（Mya 4 Controller Export）或错误日志（Error Log.csv）；无实验运行时'),
}
_PAD_ZONE_CAPS = tuple(k for k, v in _PAD_CAPS.items() if 'zone_index' in v[1])
_PAD_OBSERVE = {'experiment': 'zone_{}.pad.experiment', 'run': 'zone_{}.pad.run', 'library': 'pad.library', 'settings': 'pad.settings'}


def _pad_slots():
    exp = {'anyOf': [{'type': 'object'}, {'type': 'null'}]}
    out = {'pad.library': ('1', 'derived', '实验库（Stored Experiments）：所有保存的实验（含运行中），按名称排序', {'type': 'array', 'items': {'type': 'object'}}),
           'pad.share': ('1', 'derived', 'Share文件夹（控制板内部）中的文件', {'type': 'array', 'items': {'type': 'string'}}),
           'pad.settings': ('1', 'setpoint', 'System Settings：数据存储间隔、Demo模式、PID记录（pid_logging开启时运行记录pid_data，列见_PAD_PID_LOG_COLUMNS）', {'type': 'object'}),
           'pad.pid': ('1', 'setpoint', 'PID增益（选项A–D，块/反应，P I D）', {'type': 'object'}),
           'pad.network': ('1', 'setpoint', 'Network Share地址', {'type': 'object'}),
           'pad.clock_offset_s': ('s', 'setpoint', '控制板时钟相对仿真时间的偏移', _mya4_num('s')),
           'pad.error_log': ('1', 'derived', '错误日志（时间、区、代码、实验）', {'type': 'array', 'items': {'type': 'object'}}),
           'pad.clock': ('1', 'derived', '控制板内部时钟（读取时计算）', {'type': 'string'}),
           'pad.status': ('1', 'derived', '系统状态块：READY、CONTROL（有区运行）、DEMO、ERROR（红色系统错误）', {'type': 'string', 'enum': ['READY', 'CONTROL', 'DEMO', 'ERROR']}),
           'pad.service': ('1', 'derived', 'Service Settings的信息：版本、运行时间、各区Peltier使用时间、热循环、冷却水温度', {'type': 'object'})}
    for i in range(1, 5):
        out[f'zone_{i}.thermal.pid'] = ('1', 'derived', f'第{i}区温控器内部状态：块回路与反应回路的PID份额、Peltier功率W、串级块设定K（0.25 s采样）', {'type': 'object'}, 'debug')
        out[f'zone_{i}.pad.experiment'] = ('1', 'setpoint', f'第{i}区装入的实验：名称、模式、选项、安全限值、Manual设定、Profile步骤、状态、是否已保存、记录数据', exp)
        out[f'zone_{i}.pad.run'] = ('1', 'derived', f'第{i}区运行状态：温控/搅拌开关、当前步骤、步骤计时、实验计时、暂停', exp)
        out[f'zone_{i}.pad.errors'] = ('1', 'derived', f'第{i}区当前错误（代码、类别YZ/RZ/RS/W、文字）', {'type': 'array', 'items': {'type': 'object'}})
    return out


def _mya4_panel():
    return _g02_panel('radleys_mya4', MYA4_ASSET_DIR, set(_PAD_CAPS) | {'power.set'})


def _mya4_extend_metadata(meta, panel):
    spec = meta['asset_types'][MYA4_TYPE]
    for sid, (unit, origin, meaning, schema, *vis) in _pad_slots().items():
        spec['state_slots'][sid] = {'unit': unit, 'origin': origin, 'visibility': vis[0] if vis else 'agent', 'access': 'read_only', 'value_schema': schema, 'reference_conditions': None,
                                    'write_via': None, 'sampling': {'mode': 'instant', 'time_domain': 'simulation', 'window': None},
                                    'read_binding': "RadleysMya4Session.read(instance_id=..., state_ids=['%s'])" % sid, 'meaning_zh': meaning}
    examples = {'pad.new_experiment': {'zone_index': 0, 'name': 'Experiment 5'}, 'pad.open_experiment': {'zone_index': 0, 'source': 'Experiment 1', 'name': 'Experiment 6'},
                'pad.save_experiment': {'zone_index': 0}, 'pad.save_experiment_as': {'zone_index': 0, 'name': 'Experiment 7'}, 'pad.set_mode': {'zone_index': 0, 'mode': 'profile'},
                'pad.set_option': {'zone_index': 0, 'option': 'B'}, 'pad.set_limits': {'zone_index': 0, 'max_tb_C': 120, 'max_tr_C': 115}, 'pad.set_manual': {'zone_index': 0, 'setpoint_C': 60},
                'pad.set_temperature_control': {'zone_index': 0, 'on': True}, 'pad.set_stirrer_control': {'zone_index': 0, 'on': True},
                'pad.set_step': {'zone_index': 0, 'index': 0, 'duration_min': 30, 'speed_rpm': 250, 'from_C': 20, 'to_C': 50, 'control': 'tb', 'end': 'time'},
                'pad.add_step': {'zone_index': 0, 'index': 0}, 'pad.delete_step': {'zone_index': 0, 'index': 1}, 'pad.profile_start': {'zone_index': 0},
                'pad.profile_pause': {'zone_index': 0}, 'pad.profile_skip': {'zone_index': 0}, 'pad.profile_stop': {'zone_index': 0},
                'pad.file_export': {'name': 'Experiment 1', 'target': 'share'}, 'pad.file_import': {'file': 'Experiment 1.expc'}, 'pad.file_delete': {'name': 'Experiment 9'},
                'pad.configure': {'storage_interval_s': 20}, 'pad.set_clock': {'year': 2026, 'month': 11, 'day': 16}, 'pad.set_pid': {'option': 'A', 'loop': 'block', 'term': 'P', 'value': 30.0},
                'pad.restore_pid': {}, 'pad.set_network': {'ip': [192, 168, 1, 20]}, 'pad.reset_network': {}, 'pad.export_service': {'kind': 'error_log', 'target': 'share'}}
    controller = {}
    for name, (kind, props, optional, output, observes, meaning) in _PAD_CAPS.items():
        req = [] if optional == '*' else [k for k in props if k not in optional]
        if optional == '*' and 'zone_index' in props:
            req = ['zone_index']
        schema = {'type': 'object', 'properties': props, 'required': req, 'additionalProperties': False}
        if optional == '*':
            schema['minProperties'] = 2 if 'zone_index' in props else 1
        obs = [_PAD_OBSERVE[o].format(i) for o in observes for i in ((1, 2, 3, 4) if '{}' in _PAD_OBSERVE[o] else (0,))]
        obs = list(dict.fromkeys(obs))
        controller[name] = {'availability': 'implemented', 'visibility': 'agent', 'kind': kind, 'component_ids': ['display'], 'description_zh': meaning,
                            'input_schema': schema, 'output_schema': _mya4_object(output), 'example_arguments': [examples[name]], 'observes': obs,
                            'state_effects': [{'state_id': sid, 'timing': 'immediate', 'description_zh': meaning} for sid in obs],
                            'preconditions': [{'description_zh': '控制板可用（反应站上电或Demo模式）；运行中锁定的设定不能改', 'state_ids': ['power.enabled', 'pad.settings'],
                                               'enforced_by': 'adapter', 'check': 'station powered or demo mode; locked items refused while running'}],
                            'scene_backend_required': False,
                            'binding': "basic40_runtime_thermal.RadleysMya4Session.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)" % name,
                            'completion_criterion_zh': '同步返回即控制板已接受；温度与步骤随后由宿主step推进'}
    evaluator = {'pad.inject_error': {'availability': 'implemented', 'visibility': 'evaluator', 'kind': 'simulation', 'component_ids': ['display'],
                                      'description_zh': '评测者：注入错误表中的一个硬件故障（OM p.159–160）：YZ关本区温控、RZ结束本区实验、RS结束全部实验',
                                      'input_schema': {'type': 'object', 'properties': {'code': {'type': 'string', 'enum': list(_PAD_INJECTABLE)}, 'zone_index': _PAD_ZONE},
                                                       'required': ['code'], 'additionalProperties': False},
                                      'output_schema': _mya4_object({'code': {'type': 'string'}}), 'example_arguments': [{'code': 'Z11', 'zone_index': 1}],
                                      'observes': ['zone_1.pad.errors'], 'state_effects': [{'state_id': f'zone_{i}.pad.errors', 'timing': 'immediate', 'description_zh': '注入的错误'} for i in range(1, 5)],
                                      'preconditions': [], 'scene_backend_required': False,
                                      'binding': "basic40_runtime_thermal.RadleysMya4Session.invoke(instance_id=..., capability_id='pad.inject_error', arguments=..., request_id=...)",
                                      'completion_criterion_zh': '同步返回即错误已生效'}}
    panel_caps = {}
    if panel:
        p = _mya4_panel()
        called = p['engine'].spec_capabilities(p['spec'])
        caps = {**spec['capabilities'], **controller}
        eff = list({e['state_id']: dict(e, description_zh='面板动作调用的能力的效果：' + e['description_zh']) for c in called for e in caps[c]['state_effects']
                    if e['timing'] == 'immediate'}.values())
        panel_caps = _g02_panel_capabilities(p, "basic40_runtime_thermal.RadleysMya4Session.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)", eff)
    spec.update(controller_capabilities=controller, evaluator_capabilities=evaluator, panel_capabilities=panel_caps, asset_version='1.2.3')   # 1.2.1: sync fixes 2026-10-19 (PID logging, caller); 1.2.2: pad.set_limits example (2026-10-22); 1.2.3: pad safety limits bound the direct set points (2026-10-22)
    meta['limitations'] = meta['limitations'] + [
        'Control Pad software behind the front panel: experiments with names, Manual and Profile runs (linear From -> To steps over 1-960 min, Time / Value end '
        'conditions, Pause / Skip / Stop, up to 10 steps), zone safety limits with the T3 / T4 / T5 / T6 / T9 trips (2 degC tolerance), coolant loss T1, the 7-day '
        'limit Z18 / Z17, injected hardware faults, File Manager (library, load, Share export / import, delete), data storage interval, Demo mode, clock, PID gains '
        '(they drive the control, see below), PID data logging (each run records the gains and controller terms per data point; File Export adds <name>_PID.csv; '
        'file name and columns assumed), Network Share settings, service export; the expanded graph returns to the full view 480 s after the last zoom (OM p.101).',
        'Not simulated: USB stick (export to USB is refused), overhead stirrer and torque, the Tc coolant curve beyond the configured coolant temperature.',
        'Temperature control (2026-10-19): the PID gains of the selected option drive the Peltier power: Tb control = block PID on Tb; Tr control = reaction PID '
        'giving the block set point (Tr set + offset within (Tb-Tr)max) and the block PID on Tb (cascade); samples every 0.25 s on the host clock, held between '
        'samples. Gain units are not documented: block kp = 0.625 P W/K (factory 32 -> the previous 20 W/K), ti = 200 s / I, td = D s; reaction kp = 2 P K/K, ti = 1 s / I, td = D s.']
    return meta


class _Mya4Pad:
    """The Control Pad software of one host (mixed into RadleysMya4Runtime): experiments, Manual / Profile runs, errors, files, settings, panel."""

    def _pad_defaults(self, state):
        state.update({'pad.library': [], 'pad.share': [], 'pad.settings': {'storage_interval_s': 10, 'demo_mode': False, 'pid_logging': False},
                      'pad.pid': copy.deepcopy(_PAD_PID_DEFAULT), 'pad.network': copy.deepcopy(_PAD_NETWORK_DEFAULT), 'pad.clock_offset_s': 0.0, 'pad.error_log': []})
        for i in range(1, 5):
            state.update({f'zone_{i}.pad.experiment': _pad_new_experiment(f'Experiment {i}'), f'zone_{i}.pad.run': None, f'zone_{i}.pad.errors': [],
                          f'zone_{i}.thermal.pid': _mya4_pid_idle()})

    # ---- helpers
    def _pad_clock(self, s, t=None):
        return _PAD_CLOCK_EPOCH + _pad_timedelta(seconds=(self.time_s if t is None else t) + s['pad.clock_offset_s'])

    def _pad_names(self, s):
        return {e['name'] for e in s['pad.library']} | {s[f'zone_{i}.pad.experiment']['name'] for i in range(1, 5)}

    @staticmethod
    def _pad_name_ok(name):
        return isinstance(name, str) and 0 < len(name.strip()) and len(name) <= _PAD_NAME_MAX and set(name) <= _PAD_NAME_CHARS

    @staticmethod
    def _pad_active(s, z):
        run = s[z + '.pad.run']
        return run is not None

    def _pad_available(self, s):
        return s['power.enabled'] or s['pad.settings']['demo_mode']

    def _pad_any_running(self, s):
        return any(s[f'zone_{i}.pad.run'] is not None for i in range(1, 5))

    def _pad_valid_temp(self, s, z, value_C, control, exp=None):
        """Within the experiment's safety limits and the runtime's control range (and the 200 K zone span)."""
        exp = exp or s[z + '.pad.experiment']
        lim = exp['limits']
        lo, hi = (lim['min_tb_C'], lim['max_tb_C']) if control == 'tb' else (lim['min_tr_C'], lim['max_tr_C'])
        if not lo <= value_C <= hi:
            return False
        return self._target_valid(s, z, value_C + 273.15, 'block' if control == 'tb' else 'liquid')

    def _pad_speed_ok(self, exp, rpm):
        return rpm == 0 or 100 <= rpm <= exp['limits']['max_speed_rpm']

    def _pad_targets(self, s, z, elapsed=None):
        """(setpoint degC, control, speed rpm) the pad asks of the zone now (Manual values, or the profile step after `elapsed` s)."""
        exp, run = s[z + '.pad.experiment'], s[z + '.pad.run']
        if exp['mode'] == 'manual' or run is None:
            m = exp['manual']
            return m['setpoint_C'], m['control'], m['speed_rpm']
        step = exp['steps'][run['step']]
        if run['paused'] and run.get('hold_C') is not None:
            return run['hold_C'], step['control'], step['speed_rpm']
        t = run['step_elapsed_s'] if elapsed is None else elapsed
        dur = step['duration_min'] * 60.0
        f = min(1.0, t / dur) if dur > 0 else 1.0
        return step['from_C'] + (step['to_C'] - step['from_C']) * f, step['control'], step['speed_rpm']

    def _pad_apply(self, s, z, elapsed=None):
        """Drive the zone (the runtime's setpoint, control variable, temperature control, stirring) from the pad state."""
        run = s[z + '.pad.run']
        if run is None:
            return
        sp, control, rpm = self._pad_targets(s, z, elapsed)
        demo = s['pad.settings']['demo_mode']
        s[z + '.thermal.probe_mode'] = 'block' if control == 'tb' else 'liquid'
        if run['temp_on']:
            s[z + '.thermal.target_k'] = sp + 273.15
        s[z + '.thermal.enabled'] = bool(run['temp_on'] and not demo and s['power.enabled'] and s['cooling.available'])
        stir = bool(run['stir_on'] and rpm > 0 and not demo and s['power.enabled'])
        if rpm >= 100:
            s[z + '.stirring.target_rad_s'] = _pad_rad(rpm)
        s[z + '.stirring.enabled'] = stir
        s[z + '.stirring.actual_rad_s'] = s[z + '.stirring.target_rad_s'] if stir else 0.0

    def _pad_start_run(self, s, z, t, temp_on, stir_on):
        exp = s[z + '.pad.experiment']
        exp.update(status='running', started=self._pad_clock(s, t).strftime('%Y-%m-%d %H:%M:%S'), saved=False, data=[])
        if s['pad.settings']['pid_logging']:                                   # PID data logging on (OM p.130, p.153): the PID file of this run
            exp['pid_data'] = []
        else:
            exp.pop('pid_data', None)
        s[z + '.pad.run'] = {'temp_on': temp_on, 'stir_on': stir_on, 'step': 0, 'step_elapsed_s': 0.0, 'elapsed_s': 0.0, 'paused': False, 'hold_C': None,
                             'next_log_s': 0.0, 'warned': False}
        s[z + '.pad.errors'] = [e for e in s[z + '.pad.errors'] if e['class'] == 'YZ' and e['code'] not in ('T1', 'T3', 'T4', 'T5', 'T6', 'T9')]
        self._pad_log(s, z)

    def _pad_finish(self, s, z, result, t):
        exp = s[z + '.pad.experiment']
        exp.update(status='finished', ended=self._pad_clock(s, t).strftime('%Y-%m-%d %H:%M:%S'), result=result)
        s[z + '.pad.run'] = None
        s[z + '.thermal.enabled'], s[z + '.stirring.enabled'], s[z + '.stirring.actual_rad_s'] = False, False, 0.0

    def _pad_error(self, s, z, code, t):
        cls, text = _PAD_ERRORS[code]
        zones = [f'zone_{i}' for i in range(1, 5)] if cls == 'RS' else [z]
        for zz in zones:
            if not any(e['code'] == code for e in s[zz + '.pad.errors']):
                s[zz + '.pad.errors'] = s[zz + '.pad.errors'] + [{'code': code, 'class': cls, 'text': text}]
        s['pad.error_log'] = (s['pad.error_log'] + [{'time': self._pad_clock(s, t).strftime('%Y-%m-%d %H:%M:%S'), 'zone': 'System' if cls == 'RS' else z.replace('zone_', 'Zone'),
                                                     'code': code, 'experiment': s[z + '.pad.experiment']['name']}])[-1000:]
        for zz in zones:
            run = s[zz + '.pad.run']
            if run is None:
                continue
            if cls == 'YZ':                                                    # temperature control off, stirring kept (OM p.154)
                run['temp_on'] = False
                if s[zz + '.pad.experiment']['mode'] == 'profile':
                    run['paused'], run['hold_C'] = True, self._pad_targets(s, zz)[0]
                s[zz + '.thermal.enabled'] = False
            elif cls in ('RZ', 'RS'):                                          # temperature control and stirring off, experiment terminated
                self._pad_finish(s, zz, 'error ' + code, t)

    def _pad_log(self, s, z):
        run, exp = s[z + '.pad.run'], s[z + '.pad.experiment']
        sp, control, rpm = self._pad_targets(s, z)
        demo = s['pad.settings']['demo_mode']
        tb = sp if demo and run['temp_on'] else s[z + '.thermal.temperature_k'] - 273.15
        tr = s[z + '.thermal.liquid_temperature_k'] - 273.15
        point = [round(run['elapsed_s'], 6), round(tb, 1), round(sp, 1) if run['temp_on'] and control == 'tb' else None, round(tr, 1),
                 round(sp, 1) if run['temp_on'] and control == 'tr' else None, round(s['cooling.temperature_k'] - 273.15, 1), round(tb - tr, 1),
                 round(_pad_rpm(s[z + '.stirring.actual_rad_s']) if not demo else (rpm if run['stir_on'] else 0), 1)]
        data = exp['data'] + [point]
        exp['data'] = data[::2] + ([data[-1]] if len(data) % 2 == 0 else []) if len(data) > _PAD_DATA_MAX else data
        if 'pid_data' in exp:                                                  # PID logging: the gains in use and the controller terms at each point
            st, gains = s[z + '.thermal.pid'], s['pad.pid'][exp['option']]
            row = [round(run['elapsed_s'], 6), exp['option'], *[float(v) for v in gains['block']], *[float(v) for v in gains['reaction']],
                   round(st['b']['p'], 3), round(st['b']['i'], 3), round(st['b']['d'], 3), round(st['u'], 3),
                   None if st['sp_block_k'] is None else round(st['sp_block_k'] - 273.15, 2), round(st['r']['p'], 3), round(st['r']['i'], 3), round(st['r']['d'], 3)]
            rows = exp['pid_data'] + [row]
            exp['pid_data'] = rows[::2] + ([rows[-1]] if len(rows) % 2 == 0 else []) if len(rows) > _PAD_DATA_MAX else rows
        run['next_log_s'] = run['elapsed_s'] + s['pad.settings']['storage_interval_s']

    def _pad_horizon(self, s, z):
        run = s[z + '.pad.run']
        if run is None:
            return math.inf
        h = run['next_log_s'] - run['elapsed_s']
        for limit in (_PAD_WARN_RUN_S, _PAD_MAX_RUN_S):
            if run['elapsed_s'] < limit:
                h = min(h, limit - run['elapsed_s'])
        exp = s[z + '.pad.experiment']
        if exp['mode'] == 'profile' and not run['paused']:
            left = exp['steps'][run['step']]['duration_min'] * 60.0 - run['step_elapsed_s']
            if left > 1e-9:
                h = min(h, left)
        return h if h > 1e-9 else math.inf

    def _pad_before(self, s, z, h):
        run = s[z + '.pad.run']
        if run is None:
            return
        exp = s[z + '.pad.experiment']
        if exp['mode'] == 'profile' and not run['paused']:
            self._pad_apply(s, z, run['step_elapsed_s'] + h)
        else:
            self._pad_apply(s, z)

    def _pad_after(self, s, z, h, t):
        run = s[z + '.pad.run']
        if run is None:
            return
        exp = s[z + '.pad.experiment']
        run['elapsed_s'] += h
        if exp['mode'] == 'profile' and not run['paused']:
            run['step_elapsed_s'] += h
        if run['temp_on'] and not s['pad.settings']['demo_mode']:             # trips (OM p.105, p.135-137, p.159-160)
            lim = exp['limits']
            tb, tr = s[z + '.thermal.temperature_k'] - 273.15, s[z + '.thermal.liquid_temperature_k'] - 273.15
            if not s['cooling.available']:
                self._pad_error(s, z, 'T1', t)
            elif tb > lim['max_tb_C'] + _PAD_TOL_K:
                self._pad_error(s, z, 'T3', t)
            elif tb < lim['min_tb_C'] - _PAD_TOL_K:
                self._pad_error(s, z, 'T4', t)
            elif tr > lim['max_tr_C'] + _PAD_TOL_K:
                self._pad_error(s, z, 'T5', t)
            elif tr < lim['min_tr_C'] - _PAD_TOL_K:
                self._pad_error(s, z, 'T6', t)
            elif abs(tb - tr) > lim['max_dt_K'] + _PAD_TOL_K:
                self._pad_error(s, z, 'T9', t)
            run = s[z + '.pad.run']
            if run is None:
                return
        if run['elapsed_s'] >= _PAD_MAX_RUN_S - 1e-9:
            self._pad_error(s, z, 'Z17', t)
            return
        if run['elapsed_s'] >= _PAD_WARN_RUN_S - 1e-9 and not run['warned']:
            run['warned'] = True
            self._pad_error(s, z, 'Z18', t)
        if run['elapsed_s'] >= run['next_log_s'] - 1e-9:
            self._pad_log(s, z)
        if exp['mode'] == 'profile' and not run['paused']:
            step = exp['steps'][run['step']]
            if run['step_elapsed_s'] >= step['duration_min'] * 60.0 - 1e-9:
                feedback = (s[z + '.thermal.temperature_k'] if step['control'] == 'tb' else s[z + '.thermal.liquid_temperature_k']) - 273.15
                if step['end'] == 'time' or abs(feedback - step['to_C']) <= _PAD_REACHED_K or s['pad.settings']['demo_mode']:
                    self._pad_next(s, z, t)

    def _pad_next(self, s, z, t):
        run, exp = s[z + '.pad.run'], s[z + '.pad.experiment']
        if run['step'] + 1 < len(exp['steps']):
            run.update(step=run['step'] + 1, step_elapsed_s=0.0)
            self._pad_apply(s, z, 0.0)
        else:
            self._pad_finish(s, z, 'completed', t)

    def _pad_observed(self, inst, sid):
        s = inst['state']
        if sid == 'pad.clock':
            return self._pad_clock(s).strftime('%Y-%m-%dT%H:%M:%S')
        if sid == 'pad.status':
            if any(e['class'] == 'RS' for i in range(1, 5) for e in s[f'zone_{i}.pad.errors']):
                return 'ERROR'
            if s['pad.settings']['demo_mode']:
                return 'DEMO'
            return 'CONTROL' if self._pad_any_running(s) else 'READY'
        if sid == 'pad.service':
            c = inst['pad']
            return {'controller_software': _PAD_SOFTWARE['controller'], 'system_firmware': _PAD_SOFTWARE['firmware'], 'up_time_h': int(self.time_s // 3600),
                    'zones': [{'firmware': _PAD_SOFTWARE['zone_firmware'], 'peltier_h': round(c['peltier_s'][i] / 3600.0, 1), 'thermal_cycles': c['cycles'][i],
                               'coolant_C': round(s['cooling.temperature_k'] - 273.15)} for i in range(4)]}
        return copy.deepcopy(s[sid])

    def _pad_counters(self, inst, h):
        s = inst['state']
        for i in range(4):
            if s[MYA4_ZONES[i] + '.thermal.enabled']:
                inst['pad']['peltier_s'][i] += h

    # ---- the panel host
    def panel(self, instance_id):
        """The front-panel engine of an instance (trusted host / tests: render, frame), created on first use."""
        if instance_id not in self._pad_panels:
            if instance_id not in self._instances: raise ValueError('Unknown instance')
            p = _mya4_panel()
            host = _G02PanelHost(p['engine'], lambda: self._pad_panel_read(instance_id),
                                 lambda cap, args: (self._panel_caller or self._panel_session)._checked(instance_id, self._instances[instance_id], cap, args, None),
                                 lambda: self.time_s)
            self._pad_panels[instance_id] = p['engine'].PanelUI(p['spec'], host)
        return self._pad_panels[instance_id]

    def _pad_panel_read(self, instance_id):
        inst = self._instances[instance_id]
        s = inst['state']
        slots = self._metadata['asset_types'][MYA4_TYPE]['state_slots']
        out = {sid: self._pad_observed(inst, sid) for sid in slots if slots[sid]['visibility'] == 'agent'}
        demo = s['pad.settings']['demo_mode']
        now = self._pad_clock(s)
        out.update({'host.powered': self._pad_available(s), 'host.station': s['power.enabled'], 'host.power_epoch': inst['pad']['power_epoch'],
                    'host.running_any': self._pad_any_running(s), 'host.password': now.day * now.month,
                    'host.clock_parts': [now.year, now.month, now.day, now.hour, now.minute, now.second],
                    'host.share_rows': [{'name': f['name']} for f in s['pad.share']],
                    'host.library_rows': [{'name': e['name'], 'label': e['name'] + ('  (running)' if e['status'] == 'running' else ''),
                                           'date': (e.get('saved_at') or '')[:10], 'date_key': int((e.get('saved_at') or '0')[:10].replace('-', '') or 0)}
                                          for e in sorted(s['pad.library'], key=lambda e: e['name'].lower())],
                    'host.library_by_date': [{'name': e['name'], 'label': e['name'], 'date': (e.get('saved_at') or '')[:10],
                                              'date_key': int((e.get('saved_at') or '0')[:10].replace('-', '') or 0)}
                                             for e in sorted(s['pad.library'], key=lambda e: (e.get('saved_at') or '', e['name'].lower()))],
                    'host.loaded_names': [s[f'zone_{i}.pad.experiment']['name'] for i in range(1, 5)],
                    'host.next_name': next(f'Experiment {k}' for k in range(1, 10000) if f'Experiment {k}' not in self._pad_names(s)),
                    'host.running_names': [s[f'zone_{i}.pad.experiment']['name'] for i in range(1, 5) if s[f'zone_{i}.pad.run'] is not None],
                    'host.error_rows': [dict(e, label=f"{e['time']}  {e['zone']}  {e['code']}  {e['experiment']}") for e in reversed(s['pad.error_log'][-10:])]})
        for i, z in enumerate(MYA4_ZONES, 1):
            exp, run = s[z + '.pad.experiment'], s[z + '.pad.run']
            sp, control, rpm = self._pad_targets(s, z)
            tb = (sp if demo and run and run['temp_on'] else s[z + '.thermal.indicated_temperature_k'] - 273.15)
            errs = s[z + '.pad.errors']
            colour = 'red' if any(e['class'] in ('RZ', 'RS') for e in errs) else 'yellow' if any(e['class'] == 'YZ' for e in errs) else 'green' if run else 'white'
            speed = (rpm if run and run['stir_on'] else 0) if demo else _pad_rpm(s[z + '.stirring.actual_rad_s'])
            steps = exp['steps']
            out.update({f'host.z{i}.tb_c': round(tb, 6), f'host.z{i}.tr_c': round(s[z + '.thermal.indicated_liquid_temperature_k'] - 273.15, 6),
                        f'host.z{i}.speed_rpm': round(speed, 6), f'host.z{i}.flask': colour, f'host.z{i}.running': run is not None,
                        f'host.z{i}.paused': bool(run and run['paused']), f'host.z{i}.temp_on': bool(run and run['temp_on']),
                        f'host.z{i}.stir_on': bool(run and run['stir_on']), f'host.z{i}.hot': tb > 40, f'host.z{i}.cold': tb < 0,
                        f'host.z{i}.title': exp['name'] + ('' if exp['saved'] else '*'), f'host.z{i}.step': None if not run or exp['mode'] != 'profile' else run['step'],
                        f'host.z{i}.error': errs[-1] if errs else None, f'host.z{i}.error_count': len(errs),
                        f'host.z{i}.step_rows': [dict(st, index=k, no=str(k + 1), started=bool(run and exp['mode'] == 'profile' and k <= run['step']),
                                                      ctl='Tb' if st['control'] == 'tb' else 'Tr', endc='Value' if st['end'] == 'value' else 'Time') for k, st in enumerate(steps)],
                        f'host.z{i}.profile_text': ('No Profile - Manual Mode' if exp['mode'] == 'manual' else
                                                    '   '.join(f"{st['duration_min']}m {st['from_C']}>{st['to_C']}C {st['speed_rpm']}rpm" for st in steps)),
                        f'host.z{i}.series': self._pad_series(exp['data']), f'host.z{i}.sp_c': round(sp, 6), f'host.z{i}.set_rpm': rpm,
                        f'host.z{i}.span': max(60.0, exp['data'][-1][0] if exp['data'] else 60.0), f'host.z{i}.plan': self._pad_plan(exp),
                        f'host.z{i}.mode': exp['mode'], f'host.z{i}.started': exp['status'] != 'new', f'host.z{i}.finished': exp['status'] == 'finished'})
        return out

    @staticmethod
    def _pad_plan(exp):
        """The profile as a timeline: temperature [[s, degC]] and speed [[s, rpm]] at the step boundaries."""
        t, temp, speed = 0.0, [], []
        for st in exp['steps']:
            d = st['duration_min'] * 60.0
            temp += [[t, st['from_C']], [t + d, st['to_C']]]
            speed += [[t, st['speed_rpm']], [t + d, st['speed_rpm']]]
            t += d
        return {'temp': temp, 'speed': speed, 'total_s': max(t, 60.0)}

    @staticmethod
    def _pad_series(data):
        cols = {'tb': 1, 'tb_set': 2, 'tr': 3, 'tr_set': 4, 'tc': 5, 'dt': 6, 'speed': 7}
        return {k: [[p[0], p[c]] for p in data if p[c] is not None] for k, c in cols.items()}


class RadleysMya4Runtime(_Mya4Pad):
    """Trusted host owns clock, cooling configuration and sessions; do not expose to agents."""
    def __init__(self, *, ambient_temperature_K=298.15, scene_adapter=None):
        if not _number(ambient_temperature_K) or not 283.15 <= ambient_temperature_K <= 308.15: raise ValueError('Ambient outside assumed283.15..308.15K envelope')
        if scene_adapter is not None and not callable(scene_adapter): raise ValueError('Scene adapter must be callable')
        self.ambient_temperature_K,self.scene_adapter,self.time_s=float(ambient_temperature_K),scene_adapter,0.0
        self._cooling={'cooling.available':False,'cooling.temperature_k':288.15,'cooling.flow_m3_s':3e-3/60}
        self._instances,self._requests,self._scene_faults={},{},[]
        self._principals,self._logs={'__public_agent__':('agent',None)},{'__public_agent__':[]}
        self._metadata,self._lock=export_radleys_mya4_metadata(panel=False),threading.RLock()
        self._agent=RadleysMya4Session(self,'__public_agent__')
        self._pad_panels,self.panel_capture_dir={},None
        self._principals['__mya4_panel__'],self._logs['__mya4_panel__']=('agent',None),[]
        self._panel_session=RadleysMya4Session(self,'__mya4_panel__')   # the front panel's own path to the commands (not logged)
        self._panel_caller=None                                            # the session whose gesture runs: its commands act as that caller
    def _scene_payload(self,state):
        slots=self._metadata['asset_types'][MYA4_TYPE]['state_slots']
        return {sid:copy.deepcopy(value) for sid,value in state.items() if slots[sid]['visibility']=='agent'}
    def create_instance(self,instance_id,asset_type_id=MYA4_TYPE):
        with self._lock:
            if not isinstance(instance_id,str) or not instance_id or instance_id in self._instances or asset_type_id!=MYA4_TYPE: raise ValueError('Unique nonempty instance ID and Mya4 type required')
            state={'power.enabled':False,**self._cooling}
            for z in MYA4_ZONES:
                values={'thermal.target_k':self.ambient_temperature_K,'thermal.enabled':False,'thermal.probe_mode':'block','thermal.temperature_k':self.ambient_temperature_K,'thermal.liquid_temperature_k':self.ambient_temperature_K,
                    'thermal.indicated_temperature_k':round(self.ambient_temperature_K-273.15,1)+273.15,'thermal.indicated_liquid_temperature_k':round(self.ambient_temperature_K-273.15,1)+273.15,
                    'thermal.control_energy_j':0.0,'thermal.environment_energy_j':0.0,'thermal.adjacent_energy_j':0.0,
                    'stirring.target_rad_s':MYA4_MIN_SPEED_RAD_S,'stirring.enabled':False,'stirring.actual_rad_s':0.0,'stirring.angle_rad':0.0,
                    'vessel.capacity_m3':1e-4,'vessel.fill_volume_m3':5e-5,'program.segments':[],'program.running':False,'program.phase':'idle','program.segment_index':0,'program.hold_elapsed_s':0.0,'program.reaction_elapsed_s':0.0}
                state.update({z+'.'+key:value for key,value in values.items()})
            self._pad_defaults(state)
            if self.scene_adapter: self.scene_adapter(instance_id,self._scene_payload(state))
            self._instances[instance_id]={'state':state,'revision':0,'pad':{'power_epoch':0,'peltier_s':[0.0]*4,'cycles':[0]*4,'share_store':{}}}
    def register_principal(self,principal_id,*,visibility,instance_ids=None):
        with self._lock:
            if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'): raise ValueError('Invalid or duplicate host identity')
            if instance_ids is not None and (not isinstance(instance_ids,(list,tuple,set,frozenset)) or any(not isinstance(sid,str) for sid in instance_ids)): raise ValueError('Explicit instance ID collection required')
            scope=None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope<=self._instances.keys(): raise ValueError('Unknown scope')
            self._principals[principal_id],self._logs[principal_id]=(visibility,scope),[]
    def session(self,principal_id):
        with self._lock:
            if not isinstance(principal_id,str) or principal_id not in self._principals: raise ValueError('Trusted host registration required')
            return RadleysMya4Session(self,principal_id)
    def describe(self,**kwargs): return self._agent.describe(**kwargs)
    def read(self,**kwargs): return self._agent.read(**kwargs)
    def invoke(self,**kwargs): return self._agent.invoke(**kwargs)
    def write(self,**kwargs): return self._agent.write(**kwargs)
    def describe_host(self): return copy.deepcopy(self._metadata['host_capabilities'])
    def debug_snapshot(self):
        with self._lock: return copy.deepcopy({'time_s':self.time_s,'instances':self._instances,'scene_faults':self._scene_faults})
    def _publish_scene(self):
        failed=False
        if self.scene_adapter:
            for sid,inst in self._instances.items():
                try: self.scene_adapter(sid,self._scene_payload(inst['state']))
                except Exception:
                    failed=True
                    self._scene_faults.append({'instance_id':sid,'time_s':self.time_s,'code':'SCENE_SYNCHRONIZATION_FAILED','side_effects':'partial_or_unknown'})
        if failed: raise SceneSynchronizationError('Process/clock committed; scene synchronization failed with possible partial effects') from None
    def sync_scene(self):
        with self._lock:
            if self.scene_adapter is None: raise RuntimeError('No scene adapter registered')
            self._publish_scene()
    def configure_cooling_source(self,*,available,temperature_K,flow_m3_s):
        if type(available) is not bool or not _number(temperature_K) or not 278.15<=temperature_K<=308.15 or not _number(flow_m3_s) or not 2e-3/60<=flow_m3_s<=5e-3/60: raise ValueError('Cooling source outside declared5..35°C,2..5L/min conditions')
        with self._lock:
            self._cooling={'cooling.available':available,'cooling.temperature_k':float(temperature_K),'cooling.flow_m3_s':float(flow_m3_s)}
            for inst in self._instances.values():
                s=inst['state']; before=copy.deepcopy(s); s.update(self._cooling)
                for z in MYA4_ZONES:
                    if not available or not self._target_valid(s,z,s[z+'.thermal.target_k'],s[z+'.thermal.probe_mode']):
                        s[z+'.thermal.enabled']=False
                        if s[z+'.program.running']: s[z+'.program.running'],s[z+'.program.phase']=False,'interrupted_cooling'
                if s!=before: inst['revision']+=1
            self._publish_scene()
    def _zone_limits(self,s,z,mode):
        """(min, max) degC of the zone's Control Pad safety limits for the probe mode (block: Min/Max Tb, liquid: Min/Max Tr), OM p.101."""
        lim=(s.get(z+'.pad.experiment') or {}).get('limits') or _PAD_LIMITS_DEFAULT
        return (lim['min_tb_C'],lim['max_tb_C']) if mode=='block' else (lim['min_tr_C'],lim['max_tr_C'])
    def _zone_limits_ok(self,s,z,target,mode):
        lo,hi=self._zone_limits(s,z,mode)
        return lo-1e-9<=target-273.15<=hi+1e-9
    def _target_valid(self,s,z,target,mode):
        low,high=(243.15,453.15) if mode=='block' else (253.15,448.15)      # Tr up to 175 C (OM p.105, p.161)
        if not low<=target<=high: return False
        if target<self.ambient_temperature_K and s['cooling.available']:
            # Official endpoints:5°C coolant gives−30°C block;15°C gives−25°C. Intermediate interpolation is a simulation assumption.
            coolant=s['cooling.temperature_k']
            if coolant>288.15: return False
            source_min=243.15+0.5*(coolant-278.15) if mode=='block' else 253.15
            if target<source_min: return False
        targets=[target if other==z else s[other+'.thermal.target_k'] for other in MYA4_ZONES]
        return max(targets)-min(targets)<=200.0+1e-10
    def _mya4_sample(self, s):
        """One controller sample of the four zones (every MYA4_PID_TS on the host clock): Tb control = block PID on Tb -> Peltier power within
        -140..300 W; Tr control = reaction PID on Tr -> block set point offset within +-(Tb-Tr)max of the zone's experiment, then the block PID.
        Gains: the selected option of pad.pid (OM p.130, p.139, p.152-153); outputs held until the next sample."""
        a = MYA4_ASSUMPTIONS
        for z in MYA4_ZONES:
            st = copy.deepcopy(s[z + '.thermal.pid'])
            if not (s['power.enabled'] and s['cooling.available'] and s[z + '.thermal.enabled']):
                if st != _mya4_pid_idle():
                    s[z + '.thermal.pid'] = _mya4_pid_idle()
                continue
            exp = s[z + '.pad.experiment']
            gains = s['pad.pid'][exp['option']]
            target = s[z + '.thermal.target_k']
            sp_block = target
            if s[z + '.thermal.probe_mode'] == 'liquid':
                kp, ti, td = _mya4_reaction_terms(*gains['reaction'])
                lim = float(exp['limits']['max_dt_K'])
                off = _g02_pid_sample(st['r'], target - s[z + '.thermal.liquid_temperature_k'], kp, ti, td, MYA4_PID_TS, -lim, lim, i_lo=-lim, i_hi=lim, d_lim=lim)
                sp_block = min(453.15, max(243.15, target + off))
            else:
                st['r'] = _mya4_pid_idle()['r']
            kp, ti, td = _mya4_block_terms(*gains['block'])
            hi, lo = a['effective_heating_limit_W'], -a['effective_cooling_limit_W']
            st['u'] = _g02_pid_sample(st['b'], sp_block - s[z + '.thermal.temperature_k'], kp, ti, td, MYA4_PID_TS, lo, hi, i_lo=lo, i_hi=hi, d_lim=hi)
            st['sp_block_k'] = sp_block
            s[z + '.thermal.pid'] = st

    def _derivative(self,s,y):
        a=MYA4_ASSUMPTIONS; cb=a['effective_block_capacity_J_K']; ta=self.ambient_temperature_K
        dy=[0.0]*20
        coupling=[0.0]*4
        for i,j in ((0,1),(1,2),(2,3)):
            q=a['adjacent_block_conductance_W_K']*(y[j]-y[i]); coupling[i]+=q;coupling[j]-=q
        for i,z in enumerate(MYA4_ZONES):
            q=0.0; target=s[z+'.thermal.target_k']
            if s['power.enabled'] and s['cooling.available'] and s[z+'.thermal.enabled']:
                q=s[z+'.thermal.pid']['u']                                       # the controller output held since the last sample
                if y[i]>=453.15 and q>0:q=0.0
                if y[i]<=243.15 and q<0:q=0.0
            env=a['block_ambient_conductance_W_K']*(y[i]-ta)
            transfer=a['block_liquid_conductance_W_K']*(y[i]-y[4+i])
            cl=a['liquid_density_kg_m3']*a['liquid_specific_heat_J_kg_K']*s[z+'.vessel.fill_volume_m3']
            dy[i]=(q-env-transfer+coupling[i])/cb;dy[4+i]=transfer/cl
            dy[8+i]=q;dy[12+i]=env;dy[16+i]=coupling[i]
        return dy
    def _physics(self,s,h):
        y=[s[z+'.thermal.temperature_k'] for z in MYA4_ZONES]+[s[z+'.thermal.liquid_temperature_k'] for z in MYA4_ZONES]+[0.0]*12
        k1=self._derivative(s,y);k2=self._derivative(s,[v+h*k/2 for v,k in zip(y,k1)]);k3=self._derivative(s,[v+h*k/2 for v,k in zip(y,k2)]);k4=self._derivative(s,[v+h*k for v,k in zip(y,k3)])
        result=[v+h*(a+2*b+2*c+d)/6 for v,a,b,c,d in zip(y,k1,k2,k3,k4)]
        for i,z in enumerate(MYA4_ZONES):
            s[z+'.thermal.temperature_k'],s[z+'.thermal.liquid_temperature_k']=result[i],result[4+i]
            for offset,suffix in ((8,'control_energy_j'),(12,'environment_energy_j'),(16,'adjacent_energy_j')):s[z+'.thermal.'+suffix]+=result[offset+i]
    def _program(self,s,z,h):
        if not s[z+'.program.running']:return
        s[z+'.program.reaction_elapsed_s']+=h
        idx=s[z+'.program.segment_index'];seg=s[z+'.program.segments'][idx]
        feedback=s[z+'.thermal.temperature_k'] if s[z+'.thermal.probe_mode']=='block' else s[z+'.thermal.liquid_temperature_k']
        if abs(feedback-seg['temperature_K'])<=MYA4_ASSUMPTIONS['program_arrival_tolerance_K']:
            s[z+'.program.phase']='holding';s[z+'.program.hold_elapsed_s']+=h
            if s[z+'.program.hold_elapsed_s']+1e-10>=seg['hold_s']:
                s[z+'.program.hold_elapsed_s']=seg['hold_s'];idx+=1;s[z+'.program.segment_index']=idx
                if idx==len(s[z+'.program.segments']):s[z+'.program.running'],s[z+'.program.phase'],s[z+'.thermal.enabled']=False,'completed',False
                else:
                    next_target=s[z+'.program.segments'][idx]['temperature_K']
                    if not self._target_valid(s,z,next_target,s[z+'.thermal.probe_mode']):
                        s[z+'.program.running'],s[z+'.program.phase'],s[z+'.thermal.enabled']=False,'stopped',False
                    else:s[z+'.thermal.target_k'],s[z+'.program.hold_elapsed_s'],s[z+'.program.phase']=next_target,0.0,'approaching'
        else:s[z+'.program.phase']='approaching'
    def step(self,dt_s):
        if not _number(dt_s) or not 0<=dt_s<=86400:raise ValueError('Host step must be finite0..86400s')
        if dt_s==0:return
        with self._lock:
            for inst in self._instances.values():
                s=inst['state'];remaining=float(dt_s)
                pad=any(s[z+'.pad.run'] is not None for z in MYA4_ZONES)       # Control Pad experiments (G02 panel): ramps, end conditions, trips
                while remaining>1e-12:
                    t_abs=self.time_s+float(dt_s)-remaining
                    nxt=inst.get('pid_t_next')                                   # controller samples on the host clock grid
                    due=nxt is None or t_abs>=nxt-1e-9
                    if due:nxt=inst['pid_t_next']=(math.floor(t_abs/MYA4_PID_TS+1e-9)+1)*MYA4_PID_TS
                    h=min(remaining,MYA4_ASSUMPTIONS['maximum_integration_step_s'],nxt-t_abs)
                    for z in MYA4_ZONES:
                        if s[z+'.program.running'] and s[z+'.program.phase']=='holding':
                            needed=s[z+'.program.segments'][s[z+'.program.segment_index']]['hold_s']-s[z+'.program.hold_elapsed_s']
                            if needed>1e-12:h=min(h,needed)
                    if pad:
                        for z in MYA4_ZONES:h=min(h,self._pad_horizon(s,z))
                        for z in MYA4_ZONES:self._pad_before(s,z,h)
                    if due:self._mya4_sample(s)
                    self._physics(s,h)
                    for z in MYA4_ZONES:self._program(s,z,h)
                    if pad:
                        t=self.time_s+float(dt_s)-remaining+h
                        for z in MYA4_ZONES:self._pad_after(s,z,h,t)
                        pad=any(s[z+'.pad.run'] is not None for z in MYA4_ZONES)
                    self._pad_counters(inst,h)
                    remaining-=h
                for z in MYA4_ZONES:
                    s[z+'.stirring.angle_rad']=(s[z+'.stirring.angle_rad']+s[z+'.stirring.actual_rad_s']*dt_s)%(2*math.pi)
                    s[z+'.thermal.indicated_temperature_k']=round(s[z+'.thermal.temperature_k']-273.15,1)+273.15
                    s[z+'.thermal.indicated_liquid_temperature_k']=round(s[z+'.thermal.liquid_temperature_k']-273.15,1)+273.15
                inst['revision']+=1
            self.time_s+=float(dt_s);self._publish_scene()


class RadleysMya4Session:
    """Agent-facing fixed-identity interface; no role override or clock-control argument."""
    def __init__(self,host,principal_id):self.__host,self.__principal=host,principal_id
    def _visible(self,info):return info['visibility']=='agent' or self.__host._principals[self.__principal][0] in ('debug','evaluator')
    def _instance(self,sid):
        if not isinstance(sid,str):return None
        scope=self.__host._principals[self.__principal][1]
        return self.__host._instances.get(sid) if scope is None or sid in scope else None
    def _envelope(self,sid,request_id=None,*,result=None,observations=None,status='succeeded',error=None):
        inst=self._instance(sid)
        return {'protocol_version':PROTOCOL_VERSION,'instance_id':sid if isinstance(sid,str) else None,'request_id':request_id if isinstance(request_id,str) else None,'operation_id':None,'status':status,
            'state_revision':inst['revision'] if inst else None,'clock':{'domain':'simulation','time_s':self.__host.time_s},'result':copy.deepcopy(result or {}),'observations':copy.deepcopy(observations or {}),'error':error}
    def _error(self,sid,code,request_id=None,status='rejected',message=None):
        messages={'UNKNOWN_INSTANCE':'Instance unavailable.','UNKNOWN_CAPABILITY':'Capability unavailable.','INVALID_ARGUMENT':'Arguments violate schema or bounds.','ACCESS_DENIED':'Request outside session policy.',
            'PRECONDITION_FAILED':'Required configuration or operating state unavailable.','BUSY':'Zone program is running.','REQUEST_CONFLICT':'Request ID already belongs to different arguments.','UNSUPPORTED':'Execution unavailable in this adapter.','DEVICE_ERROR':'Scene callback did not complete command.'}
        return self._envelope(sid,request_id,status=status,error={'code':code,'message':message or messages[code],'retryable':False,'side_effects':'unknown' if code=='DEVICE_ERROR' else 'none','progress':None})
    def describe(self,*,asset_type_id=None,instance_id=None):
        host=self.__host
        with host._lock:
            if (asset_type_id is None)==(instance_id is None):return self._error(None,'INVALID_ARGUMENT')
            if instance_id is not None and not self._instance(instance_id):return self._error(instance_id,'UNKNOWN_INSTANCE')
            if asset_type_id is not None and asset_type_id!=MYA4_TYPE:return self._error(None,'INVALID_ARGUMENT')
            spec=copy.deepcopy(export_radleys_mya4_metadata()['asset_types'][MYA4_TYPE]);spec['state_slots']={sid:info for sid,info in spec['state_slots'].items() if self._visible(info)}
            spec['capabilities'].update(spec.pop('declared_capabilities'))
            for group in ('controller_capabilities','evaluator_capabilities','panel_capabilities'):
                spec['capabilities'].update({k:v for k,v in spec.pop(group).items() if self._visible(v)})
            for cap in spec['capabilities'].values():
                cap['state_effects']=[e for e in cap['state_effects'] if e['state_id'] in spec['state_slots']];cap['observes']=[sid for sid in cap['observes'] if sid in spec['state_slots']]
            return {'protocol_version':PROTOCOL_VERSION,'asset_type_id':MYA4_TYPE,'instance_id':instance_id,'adapter':'basic40_runtime_thermal.RadleysMya4Session',**spec,'limitations':copy.deepcopy(host._metadata['limitations'])}
    def read(self,*,instance_id,state_ids=None):
        host=self.__host
        with host._lock:
            inst=self._instance(instance_id)
            if not inst:return self._error(instance_id,'UNKNOWN_INSTANCE')
            slots=host._metadata['asset_types'][MYA4_TYPE]['state_slots']
            if state_ids is None:state_ids=[sid for sid,info in slots.items() if self._visible(info)]
            if not isinstance(state_ids,list) or any(not isinstance(sid,str) for sid in state_ids) or len(set(state_ids))!=len(state_ids):return self._error(instance_id,'INVALID_ARGUMENT')
            if any(sid not in slots or not self._visible(slots[sid]) for sid in state_ids):return self._error(instance_id,'ACCESS_DENIED')
            obs={sid:{'value':host._pad_observed(inst,sid),'unit':slots[sid]['unit'],'quality':'estimated' if slots[sid]['origin']=='derived' else 'valid','sample_time_s':host.time_s,'sample_window_s':None} for sid in state_ids}
            return self._envelope(instance_id,observations=obs)
    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        host=self.__host
        with host._lock:
            inst=self._instance(instance_id)
            if not inst:return self._error(instance_id,'UNKNOWN_INSTANCE',request_id)
            if not isinstance(capability_id,str) or not isinstance(arguments,dict) or not isinstance(request_id,str) or not request_id or len(request_id)>256:return self._error(instance_id,'INVALID_ARGUMENT',request_id)
            try:digest=hashlib.sha256(json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False).encode()).hexdigest()
            except (TypeError,ValueError,OverflowError):return self._error(instance_id,'INVALID_ARGUMENT',request_id)
            key=(self.__principal,instance_id,request_id)
            if key in host._requests:
                old,response=host._requests[key]
                return copy.deepcopy(response) if old==digest else self._error(instance_id,'REQUEST_CONFLICT',request_id)
            response=self._checked(instance_id,inst,capability_id,arguments,request_id)
            host._requests[key]=(digest,copy.deepcopy(response));host._logs[self.__principal].append({'instance_id':instance_id,'capability_id':capability_id,'request_id':request_id,'response':copy.deepcopy(response)})
            return response
    def _checked(self,sid,inst,cap,args,request_id):
        """Run ownership (lead rule 2026-10-19), per zone: the caller that starts a zone's experiment (Manual or Profile) or legacy program owns
        it; another caller cannot pause, skip, stop, resume or change it.  The station switch is a hard switch (physical action, lead clarification
        2026-10-19): power.set follows it and is never refused; the pad loses contact and the runs end (OM p.132)."""
        owners=inst.get('run_owner') or {}
        a=args if isinstance(args,dict) else {}
        def active(z):return inst['state'][z+'.pad.run'] is not None or inst['state'][z+'.program.running']   # (commands replace the state dict)
        zone=MYA4_ZONES[a['zone_index']] if type(a.get('zone_index')) is int and 0<=a['zone_index']<len(MYA4_ZONES) else None
        zone_caps=('pad.profile_pause','pad.profile_skip','pad.profile_stop','pad.profile_start','pad.set_temperature_control','pad.set_stirrer_control','pad.set_manual',
                   'pad.set_step','pad.add_step','pad.delete_step','thermal.stop_program','thermal.set_temperature','thermal.set_enabled','thermal.set_probe_mode',
                   'stirring.set_speed','stirring.set_enabled')
        if zone is not None and cap in zone_caps and _g02_foreign_run(self.__host,self.__principal,owners.get(zone),active(zone)):
            return self._error(sid,'ACCESS_DENIED',request_id)
        before=zone is not None and active(zone)
        response=self._checked_body(sid,inst,cap,args,request_id)
        if zone is not None and response['status']=='succeeded' and not before and active(zone):
            inst.setdefault('run_owner',{})[zone]=self.__principal
        return response

    def _checked_body(self,sid,inst,cap,args,request_id):
        host,old=self.__host,inst['state'];spec=host._metadata['asset_types'][MYA4_TYPE]
        fail=lambda code,status='rejected':self._error(sid,code,request_id,status)
        if isinstance(cap,str) and cap.startswith('panel.'):return self._panel_command(sid,cap,args,request_id)
        if cap in spec['controller_capabilities'] or cap in spec['evaluator_capabilities']:return self._pad_command(sid,inst,cap,args,request_id)
        if cap in spec['declared_capabilities']:return fail('UNSUPPORTED','unsupported')
        if cap not in spec['capabilities']:return fail('UNKNOWN_CAPABILITY')
        schema=spec['capabilities'][cap]['input_schema']['properties']
        if set(args)!=set(schema):return fail('INVALID_ARGUMENT')
        for name,info in schema.items():
            value=args[name]
            if info['type']=='boolean' and type(value) is not bool:return fail('INVALID_ARGUMENT')
            if info['type']=='integer' and (type(value) is not int or not info['minimum']<=value<=info['maximum']):return fail('INVALID_ARGUMENT')
            if info['type']=='number' and (not _number(value) or not info.get('minimum',-math.inf)<=value<=info.get('maximum',math.inf)):return fail('INVALID_ARGUMENT')
            if info['type']=='string' and value not in info['enum']:return fail('INVALID_ARGUMENT')
        z=MYA4_ZONES[args['zone_index']] if 'zone_index' in args else None
        if cap=='thermal.configure_program':
            segs=args['segments']
            if not isinstance(segs,list) or not 1<=len(segs)<=16:return fail('INVALID_ARGUMENT')
            for seg in segs:
                if not isinstance(seg,dict) or set(seg)!={'temperature_K','hold_s'} or not _number(seg['temperature_K']) or not _number(seg['hold_s']) or not 243.15<=seg['temperature_K']<=453.15 or not 0<=seg['hold_s']<=86400:return fail('INVALID_ARGUMENT')
        if z and old[z+'.program.running'] and cap not in ('thermal.stop_program','stirring.set_speed','stirring.set_enabled') and not (cap=='thermal.set_enabled' and not args['enabled']):return fail('BUSY')
        if z and old[z+'.pad.run'] is not None:return fail('BUSY')            # a Control Pad experiment owns the zone (G02 panel)
        needs_power=cap in ('thermal.set_temperature','thermal.set_probe_mode','thermal.configure_program','thermal.start_program','stirring.set_speed') or (cap in ('thermal.set_enabled','stirring.set_enabled') and args['enabled'])
        if needs_power and not old['power.enabled']:return fail('PRECONDITION_FAILED')
        if cap=='thermal.set_temperature' and not host._target_valid(old,z,args['temperature_K'],old[z+'.thermal.probe_mode']):return fail('PRECONDITION_FAILED')
        if cap=='thermal.set_probe_mode' and not host._target_valid(old,z,old[z+'.thermal.target_k'],args['mode']):return fail('PRECONDITION_FAILED')
        if cap=='thermal.configure_program' and any(not host._target_valid(old,z,seg['temperature_K'],old[z+'.thermal.probe_mode']) for seg in args['segments']):return fail('PRECONDITION_FAILED')
        # 1.2.3 (2026-10-22): the zone's Control Pad safety limits (Min/Max Tb in block mode, Min/Max Tr in liquid mode) bound the direct set points
        # too (OM p.101: once safety limits are set for a zone, values outside them cannot be entered); default limits = the full range
        lim_t={'thermal.set_temperature':[args.get('temperature_K')],'thermal.configure_program':[seg['temperature_K'] for seg in args.get('segments') or []],
               'thermal.set_probe_mode':[old[z+'.thermal.target_k']] if z else []}.get(cap)
        if cap in ('thermal.start_program','thermal.set_enabled') and (cap=='thermal.start_program' or args.get('enabled')):
            lim_t=[old[z+'.thermal.target_k']]+([seg['temperature_K'] for seg in old[z+'.program.segments']] if cap=='thermal.start_program' else [])
        if lim_t:
            mode=args['mode'] if cap=='thermal.set_probe_mode' else old[z+'.thermal.probe_mode']
            bad=[t for t in lim_t if not host._zone_limits_ok(old,z,t,mode)]
            if bad:
                lo,hi=host._zone_limits(old,z,mode)
                return self._error(sid,'PRECONDITION_FAILED',request_id,message='%.2f K is outside the safety limits of this zone (%s %g..%g degC, set on the Control Pad).'%(bad[0],'Tb' if mode=='block' else 'Tr',lo,hi))
        if cap in ('thermal.start_program','thermal.set_enabled') and (cap=='thermal.start_program' or args['enabled']):
            if not old['cooling.available'] or not host._target_valid(old,z,old[z+'.thermal.target_k'],old[z+'.thermal.probe_mode']):return fail('PRECONDITION_FAILED')
        if cap=='thermal.start_program':
            if not old[z+'.program.segments'] or any(not host._target_valid(old,z,seg['temperature_K'],old[z+'.thermal.probe_mode']) for seg in old[z+'.program.segments']):return fail('PRECONDITION_FAILED')
        if cap=='vessel.configure':
            if args['fill_volume_m3']>args['capacity_m3']:return fail('INVALID_ARGUMENT')
            if old[z+'.thermal.enabled'] or old[z+'.stirring.enabled'] or any(abs(old[z+'.thermal.'+suffix]-host.ambient_temperature_K)>1e-9 for suffix in ('temperature_k','liquid_temperature_k')):return fail('PRECONDITION_FAILED')
        s=copy.deepcopy(old);result={'zone_index':args['zone_index']} if z else {}
        if cap=='power.set':
            s['power.enabled']=args['enabled'];result={'enabled':args['enabled']}
            if args['enabled']!=old['power.enabled']:
                for name in MYA4_ZONES:
                    if args['enabled']:s[name+'.pad.errors']=[e for e in s[name+'.pad.errors'] if e['class'] not in ('RZ','RS')]   # mains cycle may clear red errors (OM p.157)
                    elif s[name+'.pad.run'] is not None and not s['pad.settings']['demo_mode']:host._pad_finish(s,name,'contact lost',host.time_s)
            if not args['enabled']:
                for name in MYA4_ZONES:
                    for f in ('thermal.enabled','stirring.enabled'):s[name+'.'+f]=False
                    s[name+'.stirring.actual_rad_s']=0.0
                    if s[name+'.program.running']:s[name+'.program.running'],s[name+'.program.phase']=False,'interrupted_power'
        elif cap=='thermal.set_temperature':s[z+'.thermal.target_k']=float(args['temperature_K']);result['setpoint_K']=float(args['temperature_K'])
        elif cap=='thermal.set_probe_mode':s[z+'.thermal.probe_mode']=args['mode'];result['mode']=args['mode']
        elif cap=='thermal.set_enabled':
            s[z+'.thermal.enabled']=args['enabled'];result['enabled']=args['enabled']
            if not args['enabled'] and s[z+'.program.running']:s[z+'.program.running'],s[z+'.program.phase']=False,'stopped'
        elif cap=='stirring.set_speed':
            s[z+'.stirring.target_rad_s']=float(args['angular_speed_rad_s']);result['angular_speed_rad_s']=float(args['angular_speed_rad_s'])
            if s[z+'.stirring.enabled']:s[z+'.stirring.actual_rad_s']=s[z+'.stirring.target_rad_s']
        elif cap=='stirring.set_enabled':
            s[z+'.stirring.enabled']=args['enabled'];s[z+'.stirring.actual_rad_s']=s[z+'.stirring.target_rad_s'] if args['enabled'] else 0.0;result['enabled']=args['enabled']
        elif cap=='vessel.configure':
            for key in ('capacity_m3','fill_volume_m3'):s[z+'.vessel.'+key]=float(args[key]);result[key]=float(args[key])
        elif cap=='thermal.configure_program':
            s[z+'.program.segments']=copy.deepcopy(args['segments']);s[z+'.program.phase']='idle'
            for key in ('segment_index','hold_elapsed_s','reaction_elapsed_s'):s[z+'.program.'+key]=0
            result['segment_count']=len(args['segments'])
        elif cap=='thermal.start_program':
            s[z+'.program.running'],s[z+'.thermal.enabled'],s[z+'.program.phase']=True,True,'approaching'
            for key in ('segment_index','hold_elapsed_s','reaction_elapsed_s'):s[z+'.program.'+key]=0
            s[z+'.thermal.target_k']=s[z+'.program.segments'][0]['temperature_K'];result['running']=True
        elif cap=='thermal.stop_program':s[z+'.program.running'],s[z+'.thermal.enabled'],s[z+'.program.phase']=False,False,'stopped';result['running']=False
        if s!=old:
            if host.scene_adapter:
                try:host.scene_adapter(sid,host._scene_payload(s))
                except Exception:return fail('DEVICE_ERROR','failed')
            inst['state'],inst['revision']=s,inst['revision']+1
        if cap=='power.set' and args['enabled']!=old['power.enabled']:inst['pad']['power_epoch']+=1
        return self._envelope(sid,request_id,result=result)
    def _panel_command(self, sid, cap, args, request_id):
        """panel.* commands: a gesture on the instance's Control Pad (engine + panel_ui.json), a screen capture, or the evaluator frame."""
        host, panel = self.__host, _mya4_panel()
        if cap not in panel['caps']:
            return self._error(sid, 'UNKNOWN_CAPABILITY', request_id)
        if not self._visible(panel['caps'][cap]):
            return self._error(sid, 'ACCESS_DENIED', request_id)
        host._panel_caller = self                                             # a gesture acts as the session that makes it (access rules)
        try:
            result, error = _g02_panel_command(panel, host.panel(sid), cap, args, instance_id=sid, time_s=host.time_s, capture_dir=host.panel_capture_dir)
        finally:
            host._panel_caller = None
        return self._error(sid, error, request_id) if error else self._envelope(sid, request_id, result=result)

    def _pad_command(self, sid, inst, cap, args, request_id):
        """The Control Pad functions: schema and state checks on candidate copies, then one committed update (scene callback first)."""
        host = self.__host
        fail = lambda code, status='rejected': self._error(sid, code, request_id, status)   # noqa: E731
        meta = host._metadata['asset_types'][MYA4_TYPE]
        info = meta['controller_capabilities'].get(cap) or meta['evaluator_capabilities'][cap]
        if not self._visible(info):
            return fail('ACCESS_DENIED')
        schema = info['input_schema']
        if not isinstance(args, dict) or _g02_engine().check_arguments(schema, args) or len(args) < schema.get('minProperties', 0):
            return fail('INVALID_ARGUMENT')
        old = inst['state']
        s, pad, t = copy.deepcopy(old), copy.deepcopy(inst['pad']), host.time_s
        if not host._pad_available(s) and not (cap == 'pad.configure' and args == {'demo_mode': True}):
            return fail('PRECONDITION_FAILED')                                 # contact with the reaction station lost (OM p.85); Demo Mode stays possible
        z = MYA4_ZONES[args['zone_index']] if 'zone_index' in args else None
        exp = s[z + '.pad.experiment'] if z else None
        run = s[z + '.pad.run'] if z else None
        started = exp is not None and exp['status'] != 'new'
        result = {'zone_index': args['zone_index']} if z and 'zone_index' in info['output_schema']['properties'] else {}
        others = lambda: {s[f'zone_{i}.pad.experiment']['name'] for i in range(1, 5) if f'zone_{i}' != z} | {e['name'] for e in s['pad.library']}   # noqa: E731
        running_names = {s[f'zone_{i}.pad.experiment']['name'] for i in range(1, 5) if s[f'zone_{i}.pad.run'] is not None}
        if cap == 'pad.new_experiment':
            if run is not None: return fail('BUSY')
            if not host._pad_name_ok(args['name']) or args['name'] in others(): return fail('INVALID_ARGUMENT')
            s[z + '.pad.experiment'] = _pad_new_experiment(args['name'], exp)
            s[z + '.pad.errors'] = []
            result = {'name': args['name']}
        elif cap == 'pad.open_experiment':
            src = next((e for e in s['pad.library'] if e['name'] == args['source']), None)
            if run is not None: return fail('BUSY')
            if src is None or src['name'] in running_names or src['name'] == exp['name']: return fail('PRECONDITION_FAILED')
            if not host._pad_name_ok(args['name']) or args['name'] in others(): return fail('INVALID_ARGUMENT')
            new = copy.deepcopy(src)
            new.update(name=args['name'], status='new', started=None, ended=None, result=None, saved=False, data=[])
            s[z + '.pad.experiment'], s[z + '.pad.errors'] = new, []
            result = {'name': args['name']}
        elif cap in ('pad.save_experiment', 'pad.save_experiment_as'):
            if cap == 'pad.save_experiment_as':
                if started: return fail('PRECONDITION_FAILED')                # a started experiment cannot be duplicated (OM p.123)
                if not host._pad_name_ok(args['name']) or args['name'] in others() | {exp['name']}: return fail('INVALID_ARGUMENT')
                exp['name'] = args['name']
            exp['saved'], exp['saved_at'] = True, host._pad_clock(s, t).strftime('%Y-%m-%d %H:%M:%S')
            s['pad.library'] = [e for e in s['pad.library'] if e['name'] != exp['name']] + [copy.deepcopy(exp)]
            result = {'name': exp['name']}
        elif cap in ('pad.set_mode', 'pad.set_option', 'pad.set_limits'):
            if started: return fail('PRECONDITION_FAILED')                    # locked once the experiment has started (OM p.89, p.102)
            if cap == 'pad.set_mode':
                exp['mode'] = args['mode']
                result = {'mode': args['mode']}
            elif cap == 'pad.set_option':
                exp['option'] = args['option']
                result = {'option': args['option']}
            else:
                lim = dict(exp['limits'], **{k: v for k, v in args.items() if k != 'zone_index'})
                if lim['min_tb_C'] > lim['max_tb_C'] or lim['min_tr_C'] > lim['max_tr_C'] or lim['min_tr_C'] < lim['min_tb_C'] or lim['max_tr_C'] > lim['max_tb_C']:
                    return fail('INVALID_ARGUMENT')
                exp['limits'] = lim
            exp['saved'] = False
        elif cap == 'pad.set_manual':
            if exp['mode'] != 'manual' or exp['status'] == 'finished': return fail('PRECONDITION_FAILED')
            m = dict(exp['manual'], **{k: v for k, v in args.items() if k != 'zone_index'})
            if 'speed_rpm' in args and not host._pad_speed_ok(exp, m['speed_rpm']): return fail('INVALID_ARGUMENT')
            if ('setpoint_C' in args or 'control' in args) and not host._pad_valid_temp(s, z, m['setpoint_C'], m['control'], exp): return fail('INVALID_ARGUMENT')
            exp['manual'], exp['saved'] = m, False
            host._pad_apply(s, z)
        elif cap in ('pad.set_temperature_control', 'pad.set_stirrer_control'):
            key = 'temp_on' if cap == 'pad.set_temperature_control' else 'stir_on'
            if exp['mode'] != 'manual' or exp['status'] == 'finished': return fail('PRECONDITION_FAILED')   # a finished experiment cannot restart (OM p.91)
            if args['on']:
                if key == 'temp_on' and any(e['class'] == 'YZ' and e['code'] not in ('T1', 'T3', 'T4', 'T5', 'T6', 'T9') for e in s[z + '.pad.errors']):
                    return fail('PRECONDITION_FAILED')                         # a fault that does not clear: end the experiment instead (OM p.155)
                if key == 'temp_on' and not host._pad_valid_temp(s, z, exp['manual']['setpoint_C'], exp['manual']['control'], exp): return fail('PRECONDITION_FAILED')
                if run is None:
                    host._pad_start_run(s, z, t, key == 'temp_on', key == 'stir_on')
                else:
                    run[key] = True
                    if key == 'temp_on':
                        s[z + '.pad.errors'] = [e for e in s[z + '.pad.errors'] if e['code'] not in ('T1', 'T3', 'T4', 'T5', 'T6', 'T9')]
                if key == 'temp_on':
                    pad['cycles'][args['zone_index']] += 1
                    if not s['cooling.available'] and not s['pad.settings']['demo_mode']:
                        host._pad_error(s, z, 'T1', t)                         # coolant must flow during any experiment (OM p.16)
                host._pad_apply(s, z)
            elif run is not None:
                run[key] = False
                if not run['temp_on'] and not run['stir_on']:
                    host._pad_finish(s, z, 'completed', t)                     # both controls off: the experiment ends (OM p.91-92)
                else:
                    host._pad_apply(s, z)
            result = {'on': args['on']}
        elif cap in ('pad.set_step', 'pad.add_step', 'pad.delete_step'):
            steps, i = exp['steps'], args['index']
            first_free = 0 if run is None else run['step'] + 1
            if exp['mode'] != 'profile' or exp['status'] == 'finished' or i >= len(steps): return fail('PRECONDITION_FAILED')
            if cap == 'pad.set_step':
                if i < first_free: return fail('PRECONDITION_FAILED')         # a started step cannot be changed (OM p.93, p.95)
                new = {k: args[k] for k in _PAD_STEP_ARGS}
                if not host._pad_speed_ok(exp, new['speed_rpm']) or not all(host._pad_valid_temp(s, z, new[k], new['control'], exp) for k in ('from_C', 'to_C')):
                    return fail('INVALID_ARGUMENT')
                steps[i] = new
                result = {'index': i}
            elif cap == 'pad.add_step':
                if len(steps) >= _PAD_MAX_STEPS or i + 1 < first_free: return fail('PRECONDITION_FAILED')
                steps.insert(i + 1, dict(steps[i]))
                result = {'count': len(steps)}
            else:
                if len(steps) == 1 or i < first_free: return fail('PRECONDITION_FAILED')
                del steps[i]
                result = {'count': len(steps)}
            exp['saved'] = False
        elif cap in ('pad.profile_start', 'pad.profile_pause', 'pad.profile_skip', 'pad.profile_stop'):
            if exp['mode'] != 'profile' or exp['status'] == 'finished': return fail('PRECONDITION_FAILED')
            if cap == 'pad.profile_start':
                if run is None:
                    for st in exp['steps']:
                        if not host._pad_speed_ok(exp, st['speed_rpm']) or not all(host._pad_valid_temp(s, z, st[k], st['control'], exp) for k in ('from_C', 'to_C')):
                            return fail('PRECONDITION_FAILED')
                    host._pad_start_run(s, z, t, True, True)
                    pad['cycles'][args['zone_index']] += 1
                elif run['paused']:
                    if any(e['class'] == 'YZ' and e['code'] not in ('T1', 'T3', 'T4', 'T5', 'T6', 'T9') for e in s[z + '.pad.errors']): return fail('PRECONDITION_FAILED')
                    run.update(paused=False, hold_C=None, temp_on=True)
                    s[z + '.pad.errors'] = [e for e in s[z + '.pad.errors'] if e['code'] not in ('T1', 'T3', 'T4', 'T5', 'T6', 'T9')]
                else:
                    return fail('PRECONDITION_FAILED')
                if not s['cooling.available'] and not s['pad.settings']['demo_mode']:
                    host._pad_error(s, z, 'T1', t)
                else:
                    host._pad_apply(s, z)
                result = {'status': 'running'}
            elif run is None:
                return fail('PRECONDITION_FAILED')
            elif cap == 'pad.profile_pause':
                if run['paused']: return fail('PRECONDITION_FAILED')
                run.update(paused=True, hold_C=host._pad_targets(s, z)[0])
                host._pad_apply(s, z)
                result = {'status': 'paused'}
            elif cap == 'pad.profile_skip':
                host._pad_next(s, z, t)                                         # abort the step, start of the next (OM p.98)
                result = {'status': 'running' if s[z + '.pad.run'] is not None else 'finished'}
            else:
                host._pad_finish(s, z, 'stopped', t)
                result = {'status': 'finished'}
        elif cap in ('pad.file_export', 'pad.export_service'):
            if args['target'] == 'usb': return fail('PRECONDITION_FAILED')    # no USB stick in the simulated configuration (OM p.116)
            if cap == 'pad.file_export':
                src = next((e for e in s['pad.library'] if e['name'] == args['name']), None)
                if src is None: return fail('PRECONDITION_FAILED')
                files = [args['name'] + '.expc', args['name'] + '.csv'] + ([args['name'] + '_PID.csv'] if 'pid_data' in src else [])   # PID file name: assumption
                pad['share_store'][args['name']] = copy.deepcopy(src)
            else:
                if host._pad_any_running(s): return fail('PRECONDITION_FAILED')
                files = ['Mya 4 Controller Export'] if args['kind'] == 'controller' else ['Error Log.csv']
            s['pad.share'] = [f for f in s['pad.share'] if f['name'] not in files] + [{'name': f} for f in files]
            result = {'files': files}
        elif cap == 'pad.file_import':
            name = args['file'][:-5] if args['file'].endswith('.expc') else None
            if name is None or name not in pad['share_store'] or not any(f['name'] == args['file'] for f in s['pad.share']): return fail('PRECONDITION_FAILED')
            if name in host._pad_names(s): return fail('PRECONDITION_FAILED')
            s['pad.library'] = s['pad.library'] + [copy.deepcopy(pad['share_store'][name])]
            result = {'name': name}
        elif cap == 'pad.file_delete':
            if not any(e['name'] == args['name'] for e in s['pad.library']): return fail('PRECONDITION_FAILED')
            if any(s[f'zone_{i}.pad.experiment']['name'] == args['name'] for i in range(1, 5)): return fail('PRECONDITION_FAILED')   # loaded in a zone (OM p.119)
            s['pad.library'] = [e for e in s['pad.library'] if e['name'] != args['name']]
            result = {'name': args['name']}
        elif cap == 'pad.inject_error':
            code = args['code']
            if _PAD_ERRORS[code][0] != 'RS' and 'zone_index' not in args: return fail('INVALID_ARGUMENT')
            host._pad_error(s, z or MYA4_ZONES[0], code, t)
            result = {'code': code}
        else:                                                                  # System Settings tabs: locked while any zone runs (OM p.124)
            if host._pad_any_running(s): return fail('PRECONDITION_FAILED')
            if cap == 'pad.configure':
                v = dict(s['pad.settings'], **args)
                v['storage_interval_s'] = int(v['storage_interval_s']) + int(v['storage_interval_s']) % 2   # odd values round up (OM p.126)
                if v['storage_interval_s'] > 360: return fail('INVALID_ARGUMENT')
                s['pad.settings'] = v
            elif cap == 'pad.set_clock':
                now = host._pad_clock(s, t)
                try:
                    new = now.replace(**{k: v for k, v in args.items()}, microsecond=0)
                except ValueError:
                    return fail('INVALID_ARGUMENT')
                s['pad.clock_offset_s'] += (new - now).total_seconds()
                result = {'clock': new.strftime('%Y-%m-%dT%H:%M:%S')}
            elif cap == 'pad.set_pid':
                s['pad.pid'][args['option']][args['loop']]['PID'.index(args['term'])] = float(args['value'])
            elif cap == 'pad.restore_pid':
                s['pad.pid'] = copy.deepcopy(_PAD_PID_DEFAULT)
            elif cap == 'pad.set_network':
                s['pad.network'] = dict(s['pad.network'], **{k: list(v) for k, v in args.items()})
            else:
                s['pad.network'] = copy.deepcopy(_PAD_NETWORK_DEFAULT)
            result = result or {'ok': True}
        if s != old or pad != inst['pad']:
            if s != old and host.scene_adapter and host._scene_payload(s) != host._scene_payload(old):
                try: host.scene_adapter(sid, host._scene_payload(s))
                except Exception: return fail('DEVICE_ERROR', 'failed')
            inst['state'], inst['pad'], inst['revision'] = s, pad, inst['revision'] + 1
        return self._envelope(sid, request_id, result=result)

    def write(self,*,instance_id,state_id,value,request_id):
        host=self.__host
        with host._lock:
            if not self._instance(instance_id):return self._error(instance_id,'UNKNOWN_INSTANCE',request_id)
            slots=host._metadata['asset_types'][MYA4_TYPE]['state_slots'];slot=slots.get(state_id) if isinstance(state_id,str) else None
            if not slot or not self._visible(slot) or slot['access']!='read_write':return self._error(instance_id,'ACCESS_DENIED',request_id)
            route=slot['write_via']
            return self.invoke(instance_id=instance_id,capability_id=route['capability_id'],arguments={**route.get('fixed_arguments',{}),route['parameter']:value},request_id=request_id)
    def logs(self):
        with self.__host._lock:return copy.deepcopy(self.__host._logs[self.__principal])
# END CHEM-021 Radleys Mya 4 runtime.


def validate_radleys_mya4_runtime(output_directory=None):
    """Execute only Mya4 tests; publish synthetic evidence without instance truth dumps."""
    import sys as _mya4_sys
    import time as _mya4_time
    root=Path(__file__).resolve().parents[1]
    output=Path(output_directory) if output_directory else root/'outputs/basic40_batch02/t1_thermal/radleys_mya4'
    for location in (root/'scripts',root/'tests',root/'skills/chemistry-lab-assets/scripts'):
        if str(location) not in _mya4_sys.path:_mya4_sys.path.insert(0,str(location))
    import test_basic40_thermal as test_module
    checks=[];stream=io.StringIO()
    class Mya4EvidenceResult(unittest.TextTestResult):
        def addSuccess(self,test):
            checks.append({'check_id':test.id().split('.')[-1],'layer':'host' if any(tag in test.id() for tag in ('host_','agent_truth','request_replay','target_only','step_partition','scene_failure')) else 'process',
                'capability_ids':list(export_radleys_mya4_metadata()['asset_types'][MYA4_TYPE]['capabilities']),'status':'passed','method':'Executed unittest through real RadleysMya4Session entry points and trusted host; callbacks are recording stubs, not Blender geometry verification.'})
            super().addSuccess(test)
    start=_mya4_time.monotonic()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(test_module.RadleysMya4RuntimeTests)
    result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Mya4EvidenceResult).run(suite)
    report={'catalog_id':'CHEM-021','status':'passed' if result.wasSuccessful() else 'failed','checked_at':datetime.now(timezone.utc).isoformat(),'environment':{'python':platform.python_version(),'platform':platform.platform(),'bytecode_disabled':bool(_mya4_sys.dont_write_bytecode)},
        'command':'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts:tests python3 -c "from basic40_runtime_thermal import validate_radleys_mya4_runtime; validate_radleys_mya4_runtime()"',
        'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'elapsed_s':_mya4_time.monotonic()-start,'checks':checks,
        'not_run':['Blender geometry evaluation belongs to separate scene evidence','Real hardware','Real robot','Manufacturer dynamic calibration','Contact/collision solver','Chemical reaction kinetics'],
        'boundaries':export_radleys_mya4_metadata()['limitations']}
    output.mkdir(parents=True,exist_ok=True)
    (output/'runtime_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (output/'runtime_tests.log').write_text(stream.getvalue())
    (output/'runtime_metadata.json').write_text(json.dumps(export_radleys_mya4_metadata(),ensure_ascii=False,indent=2)+'\n')
    if not result.wasSuccessful():raise RuntimeError('Mya4 runtime verification failed; inspect runtime_tests.log')
    return report


# BEGIN G02 front-panel helpers: shared by the panel hookups of the BINDER VD 56, Carbolite TF1, Miele PLW 6111, Multiwave 7101,
# Nabertherm L 9, Parr 4591 and Radleys Mya 4 runtimes of this file (the JULABO block keeps its own copy).  Each asset has a panel_ui.json
# beside its contract; the generic engine scripts/asset_runtime/_panel_ui.py runs it on top of the asset's own capabilities.
import tempfile as _g02_tempfile

_G02_PANELS = {}


def _g02_engine():
    """The shared panel engine module (scripts/ on sys.path, else loaded from its file under the name the JULABO block uses)."""
    try:
        from asset_runtime import _panel_ui as engine
    except ImportError:
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).resolve().parent / 'asset_runtime/_panel_ui.py')
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        engine = sys.modules['_chemsim_panel_ui']
    return engine


def _g02_panel(key, asset_dir, capabilities):
    """Engine, checked spec and panel capability definitions of one asset, loaded on first use (a broken spec stops only that asset's panel)."""
    if key not in _G02_PANELS:
        engine = _g02_engine()
        spec = engine.load_spec(Path(asset_dir) / 'panel_ui.json', capabilities=set(capabilities))
        _G02_PANELS[key] = dict(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _G02_PANELS[key]


def _g02_panel_capabilities(panel, binding, effects):
    """Runtime metadata of the panel.* commands: gestures carry the immediate effects of the capabilities the panel can call."""
    out = {}
    for name, d in panel['caps'].items():
        gesture = name not in ('panel.capture', 'panel.frame')
        out[name] = {'availability': 'implemented', 'visibility': d['visibility'], 'kind': d['kind'], 'description_zh': d['function_zh'],
                     'component_ids': list(d['component_ids']), 'input_schema': d['input_schema'], 'output_schema': d['output_schema'],
                     'example_arguments': [d['example_arguments']], 'observes': [], 'state_effects': copy.deepcopy(effects) if gesture else [],
                     'preconditions': [], 'preconditions_zh': [], 'binding': binding.format(name), 'scene_backend_required': False,
                     'completion_criterion_zh': '手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕须用panel.capture查看' if gesture else '同步返回当前画面'}
    return out


def _g02_foreign_run(host, principal, owner, active):
    """Lead rule (2026-10-19): stopping, powering off or changing a run that another caller started is refused (ACCESS_DENIED, state
    unchanged) on the panel and the direct path alike; the evaluator (and debug) is unrestricted.  A panel gesture acts as its session."""
    return bool(active) and owner is not None and owner != principal and host._principals[principal][0] not in ('evaluator', 'debug')


class _G02PanelHost:
    """Panel-host protocol of the engine over three callables of one runtime instance (read, checked invoke, clock)."""
    def __init__(self, engine, read, invoke, clock):
        self._engine, self._read, self._invoke, self._clock = engine, read, invoke, clock

    def panel_read(self):
        return self._read()

    def panel_invoke(self, capability_id, arguments):
        response = self._invoke(capability_id, dict(arguments))
        if response['status'] != 'succeeded':
            raise self._engine.PanelRejected(response['error']['code'])
        return response['result']

    def panel_time(self):
        return float(self._clock())


def _g02_panel_command(panel, ui, capability, args, *, instance_id, time_s, capture_dir=None):
    """One panel.* command on an instance's PanelUI: (result, None), or (None, error code) for arguments the panel cannot take."""
    engine = panel['engine']
    if engine.check_arguments(panel['caps'][capability]['input_schema'], args):
        return None, 'INVALID_ARGUMENT'
    try:
        if capability == 'panel.frame':
            return {'frame': ui.frame()}, None
        if capability == 'panel.capture':
            data, width, height = ui.png_bytes(args['display_id'])
            sha = hashlib.sha256(data).hexdigest()
            folder = Path(capture_dir) if capture_dir else Path(_g02_tempfile.gettempdir()) / 'chemistry_sim_panel' / instance_id
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{args['display_id']}_{sha[:16]}.png"
            if not path.exists():
                path.write_bytes(data)
            return {'display_id': args['display_id'], 'time_s': time_s,
                    'image': {'path': str(path), 'format': 'png', 'width': width, 'height': height, 'sha256': sha}}, None
        if capability == 'panel.press':
            accepted = ui.press(args['input_id'], float(args.get('duration_s', 0.1)), held=args.get('held', ()))
        elif capability == 'panel.switch':
            accepted = ui.switch(args['input_id'], args['position'], held=args.get('held', ()))
        elif capability == 'panel.touch':
            to = (args['to_x'], args['to_y']) if 'to_x' in args and 'to_y' in args else None
            accepted = ui.touch(args['display_id'], args['x'], args['y'], float(args.get('duration_s', 0.1)), to=to)
        else:
            accepted = ui.turn(args['input_id'], int(args['detents']))
    except engine.PanelError:
        return None, 'INVALID_ARGUMENT'
    return {'accepted': bool(accepted)}, None
# END G02 front-panel helpers.
