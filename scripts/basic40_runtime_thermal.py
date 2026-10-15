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


def export_nabertherm_contract_metadata():
    """Type-only source of truth; no live temperatures, identities, or logs."""
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
        "door.open": ({}, {"angle_rad": _number_schema("rad"), "closed": {"type": "boolean"}}, {}, "门绕+X向下翻90°；切断模拟加热许可并暂停程序。", ["door.angle_rad", "door.closed", "heater.enabled", "program.phase"]),
        "door.close": ({}, {"angle_rad": _number_schema("rad"), "closed": {"type": "boolean"}}, {}, "关闭炉门；已供电时恢复模拟加热许可，程序继续。", ["door.angle_rad", "door.closed", "heater.enabled", "program.phase"]),
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
            effects += [{"state_id": sid, "timing": "on_step", "description_zh": "1s以内积分步长执行温程，门开时暂停；程序完成后目标退回环境。"}
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
    return {"asset_types": {NABERTHERM_TYPE: {"catalog_id": "CHEM-185", "model": "Nabertherm L 9/11/SKM, catalogue configuration",
        "asset_version": "1.0.0", "state_slots": slots, "capabilities": caps, "declared_capabilities": declared,
        "parameters": parameters}}, "host_capabilities": host_caps,
        "trusted_scene_callback": "scene_adapter(instance_id, updates): synchronous SI state updates; host owns door angle and display",
        "result_schema_scope": "capability.output_schema validates response.result only"}


class NaberthermL9Runtime:
    """Trusted multi-instance host. Do not expose host internals to agents."""
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
        self._metadata = export_nabertherm_contract_metadata()
        self._agent = NaberthermL9Session(self, "__public_agent__")

    def create_instance(self, instance_id, asset_type_id=NABERTHERM_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != NABERTHERM_TYPE:
                raise ValueError("A unique nonempty instance ID and the declared Nabertherm type are required")
            state = {"power.enabled": False, "thermal.target_k": self.ambient_temperature_K,
                "thermal.temperature_k": self.ambient_temperature_K,
                "thermal.indicated_temperature_k": round(self.ambient_temperature_K - 273.15, 1) + 273.15,
                "door.angle_rad": 0.0, "door.closed": True, "heater.enabled": False,
                "program.segments": [], "program.running": False, "program.segment_index": 0,
                "program.phase": "idle", "program.hold_elapsed_s": 0.0}
            if self.scene_adapter:
                self.scene_adapter(instance_id, copy.deepcopy(state))
            self._instances[instance_id] = {"state": state, "revision": 0, "resume_phase": "ramp"}

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

    def _thermal_step(self, state, dt):
        ambient, old, target = self.ambient_temperature_K, state["thermal.temperature_k"], state["thermal.target_k"]
        loss = NABERTHERM_LOSS_W_K + (0 if state["door.closed"] else NABERTHERM_OPEN_LOSS_W_K)
        power = NABERTHERM_POWER_W if state["heater.enabled"] else 0.0
        decay = math.exp(-loss * dt / NABERTHERM_CAPACITY_J_K)
        if not power or old >= target:
            temperature = ambient + (old - ambient) * decay
            if power and target > ambient:
                temperature = max(target, temperature)
        else:
            equilibrium = ambient + power / loss
            temperature = min(target, equilibrium + (old - equilibrium) * decay)
        state["thermal.temperature_k"] = max(ambient, temperature)
        state["thermal.indicated_temperature_k"] = round(state["thermal.temperature_k"] - 273.15, 1) + 273.15

    def _advance(self, inst, dt):
        s = inst["state"]
        if not s["program.running"] or not s["door.closed"]:
            self._thermal_step(s, dt)
            return
        remaining = dt
        while remaining > 1e-12:
            h = min(1.0, remaining)
            if not s["program.running"]:
                self._thermal_step(s, remaining)
                break
            segment = s["program.segments"][s["program.segment_index"]]
            goal = segment["temperature_K"]
            delta = goal - s["thermal.target_k"]
            s["thermal.target_k"] += math.copysign(min(abs(delta), segment["ramp_rate_K_s"] * h), delta) if delta else 0
            before_close = abs(s["thermal.temperature_k"] - goal) <= 0.5
            self._thermal_step(s, h)
            at_goal = abs(s["thermal.target_k"] - goal) <= 1e-9 and abs(s["thermal.temperature_k"] - goal) <= 0.5
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
            spec = copy.deepcopy(self.__host._metadata["asset_types"][NABERTHERM_TYPE])
            spec["state_slots"] = {sid: slot for sid, slot in spec["state_slots"].items() if self._visible(slot)}
            spec["capabilities"].update(spec.pop("declared_capabilities"))
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
                    "Displayed temperature is rounded to 0.1 Celsius and marked estimated.",
                    "Program ramp is commanded target slope; delivered heating remains power limited.",
                    "One program of up to four stages; no B410 five-program store.",
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
            observations = {sid: {"value": copy.deepcopy(inst["state"][sid]), "unit": slots[sid]["unit"],
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
        host, old = self.__host, inst["state"]
        spec = host._metadata["asset_types"][NABERTHERM_TYPE]
        fail = lambda code, status="rejected": self._error(instance_id, code, request_id, status)
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
            if old["program.running"]:
                return fail("BUSY")
        if cap == "thermal.start_program" and (not old["program.segments"] or not old["door.closed"]):
            return fail("PRECONDITION_FAILED")
        if cap.startswith("door.") and host.scene_adapter is None:
            return fail("UNSUPPORTED", "unsupported")
        state = copy.deepcopy(old)
        resume_phase = inst["resume_phase"]
        if cap == "power.set":
            state["power.enabled"] = args["enabled"]
            state["heater.enabled"] = args["enabled"] and state["door.closed"]
            if not args["enabled"] and state["program.running"]:
                state["program.running"], state["program.phase"] = False, "stopped"
            result = {"enabled": args["enabled"]}
        elif cap == "thermal.set_temperature":
            state["thermal.target_k"] = float(args["temperature_K"])
            result = {"setpoint_K": state["thermal.target_k"]}
        elif cap.startswith("door."):
            closed = cap == "door.close"
            state["door.angle_rad"], state["door.closed"] = (0.0 if closed else NABERTHERM_DOOR_MAX_RAD), closed
            state["heater.enabled"] = state["power.enabled"] and closed
            if state["program.running"]:
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
        return self._envelope(instance_id, request_id, result=result)

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


def carbolite_tf1_metadata():
    """Type-only schema metadata, with no instance values or host credentials."""
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
    return {'protocol_version': '1.0.0', 'asset_type_id': CARBOLITE_TF1_ASSET_TYPE, 'asset_version': '1.0.0', 'catalog_id': 'CHEM-186', 'model': 'Carbolite Gero TF1 12/60/300',
            'state_slots': _tf1_copy.deepcopy(CARBOLITE_TF1_STATE_SLOTS), 'capabilities': capabilities, 'parameters': _tf1_copy.deepcopy(CARBOLITE_TF1_PARAMETERS),
            'limitations': ['Synthetic empty hot-zone lumped model, derived from the manufacturer 35-minute heat-up point with assumed 20 deg C ambient and UA; C and UA are not separately identifiable from that one point.',
                'Maximum rated temperature is 1473.15 K; continuous manufacturer rating is 1373.15 K. This model does not enforce dwell-time limits or model degradation above the continuous rating.',
                'Reference manufacturer heat-up configuration uses an empty quartz work tube and insulation plugs. Removal/seal position does not alter thermal coefficients; other configurations and cooldown are uncalibrated.',
                'No sample temperature gradients, chemical kinetics, purge, pressure, gas flow, oxygen concentration, vacuum integrity, robot operation, collision safety, or hardware driver.',
                'Scene sliding coordinates are an illustrative disassembly aid, not real motors or a furnace door hinge.',
                'Indicated temperature is an estimated ideal display rounded on a 0.1 deg C grid and exposed in K, not a measured sensor accuracy claim.',
                'Only trusted host step advances the shared TF1-world clock. Python API visibility filtering does not isolate Python introspection, shared memory or filesystem access; use a separate trusted host process for untrusted agents.']}


class CarboliteTF1SceneSynchronizationError(RuntimeError):
    """Process and common host clock committed; scene synchronization failed."""


class CarboliteTF1Runtime:
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
            if self.scene_adapter:
                self.scene_adapter(instance_id, _tf1_copy.deepcopy(state))
            self._instances[instance_id] = {'state': state, 'revision': 0, 'created_at_s': self.time_s}

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

    def _thermal_step(self, inst, dt_s):
        """Exact constant-UA ODE segments and ideal target hold, with joule ledger."""
        state = inst['state']
        ambient = self.ambient_temperature_K
        old = state['thermal.temperature_k']
        target = state['thermal.target_k']
        capacity = CARBOLITE_TF1_PARAMETERS['thermal_capacity_J_K']['value']
        loss = CARBOLITE_TF1_PARAMETERS['heat_loss_W_K']['value']
        power = CARBOLITE_TF1_PARAMETERS['heater_power_W']['value']
        heater_j = 0.0
        decay = _tf1_math.exp(-loss * dt_s / capacity)
        if not state['power.enabled']:
            new = ambient + (old - ambient) * decay
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
            cooling_s = dt_s if target == ambient else min(dt_s, capacity / loss * _tf1_math.log((old - ambient) / (target - ambient)))
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
        state['thermal.indicated_temperature_k'] = round(new - 273.15, 1) + 273.15
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
                self._thermal_step(inst, float(dt_s))
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
        host = self.__host
        state = inst['state']
        fail = lambda code, status='rejected': self._error(instance_id, code, request_id, status)
        metadata = carbolite_tf1_metadata()
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
        if capability == 'power.set':
            if args['enabled'] and state['work_tube.withdrawal_m'] != 0:
                return fail('PRECONDITION_FAILED')
            updates = {'power.enabled': args['enabled']}
            result = {'enabled': args['enabled']}
        elif capability == 'thermal.set_temperature':
            if args['temperature_K'] < host.ambient_temperature_K:
                return fail('INVALID_ARGUMENT')
            if not state['power.enabled']:
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


def export_binder_vd56_metadata():
    """Export static type metadata without instance truth or external evidence."""
    slots = _vd56_copy.deepcopy(BINDER_VD56_STATE_SPECS)
    for sid, slot in slots.items():
        slot["read_binding"] = f"BinderVD56Session.read(instance_id=..., state_ids=['{sid}'])"
        if slot["origin"] == "derived":
            slot["observation_note_zh"] = "quality=estimated的仿真显示代理，非实测传感器值"
    caps = {}
    examples = {"power.set": {"enabled": True}, "thermal.set_temperature": {"temperature_K": 373.15}, "scene.set_door": {"angle_rad": .5}, "vacuum.set_valve": {"opened": True}, "vent.set_valve": {"opened": True}}
    for capability, args in _VD56_CAP_ARGS.items():
        output_key, sid = _VD56_CAP_RESULTS[capability]
        out = _vd56_copy.deepcopy(slots[sid]["value_schema"])
        if out["type"] == "number": out["x-unit"] = slots[sid]["unit"]
        effects = [{"state_id": sid, "timing": "immediate", "description_zh": _VD56_SUMMARIES[capability]}]
        if capability in ("power.set", "thermal.set_temperature", "scene.set_door"):
            effects += [{"state_id": key, "timing": "on_step", "description_zh": "宿主时间推进后的空载热响应与显示"} for key in ("thermal.temperature_k", "thermal.indicated_temperature_k")]
        if capability in ("scene.set_door", "vacuum.set_valve", "vent.set_valve"):
            effects += [{"state_id": key, "timing": "on_step", "description_zh": "宿主时间推进后的腔压与显示；无物料求解"} for key in ("pressure.pressure_pa", "pressure.indicated_pressure_pa")]
        pre = _vd56_copy.deepcopy(_VD56_PRECONDITIONS[capability])
        caps[capability] = {"availability": "implemented", "visibility": "agent", "kind": "simulation" if capability.startswith("scene.") else "device_command", "description_zh": _VD56_SUMMARIES[capability], "component_ids": _VD56_COMPONENTS[capability],
            "input_schema": {"type": "object", "properties": _vd56_copy.deepcopy(args), "required": list(args), "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {output_key: out}, "required": [output_key], "additionalProperties": False},
            "state_effects": effects, "observes": [sid], "example_arguments": [examples[capability]], "preconditions": pre, "preconditions_zh": [p["description_zh"] for p in pre],
            "scene_backend_required": capability.startswith("scene."), "binding": "basic40_runtime_thermal.BinderVD56Session.invoke(instance_id=..., capability_id='" + capability + "', arguments=..., request_id=...)",
            "completion_criterion": "Validated command state committed; no process time elapsed", "partial_failure": "Callback failure preserves runtime state; backend effects unknown"}
    declared = {"pressure.program": {"availability": "declared", "visibility": "agent", "kind": "configuration", "description_zh": "压力程序未实现；需外接泵与控制系统配置，调用unsupported且不改状态",
        "input_schema": {"type": "object", "properties": {"steps": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"pressure_Pa": {"type": "number", "exclusiveMinimum": 0, "x-unit": "Pa"}, "duration_s": {"type": "number", "exclusiveMinimum": 0, "x-unit": "s"}}, "required": ["pressure_Pa", "duration_s"], "additionalProperties": False}}}, "required": ["steps"], "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "example_arguments": [{"steps": [{"pressure_Pa": 5000, "duration_s": 600}]}], "state_effects": [], "observes": [], "binding": None}}
    return {"asset_types": {BINDER_VD56_ASSET_ID: {"catalog_id": "CHEM-012", "model": "BINDER VD 56", "asset_version": "1.0.0", "state_slots": slots, "capabilities": caps, "declared_capabilities": declared, "parameters": _vd56_copy.deepcopy(BINDER_VD56_PARAMETERS)}},
        "host_capabilities": {"sim.step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "BinderVD56Runtime.step(dt_s)", "scope": "Every registered VD56 instance shares one host clock", "input_schema": {"type": "object", "properties": {"dt_s": {"type": "number", "minimum": 0, "maximum": 3600, "x-unit": "s"}}, "required": ["dt_s"], "additionalProperties": False}},
            "host.configure_external_vacuum": {"kind": "configuration", "visibility": "trusted_host_only", "binding": "BinderVD56Runtime.configure_external_vacuum", "description_zh": "无默认泵；连接时须显式提供绝压边界、时间常数及证据"},
            "host.sync_scene": {"kind": "configuration", "visibility": "trusted_host_only", "binding": "BinderVD56Runtime.sync_scene"}}, "result_schema_scope": "capability.output_schema describes envelope result"}


class BinderVD56Runtime(_VD56BaseHost):
    """Trusted host; process step, identities, scene and external boundary only.

    Empty-system thermal parameters calibrate the 100 deg C reference; neither
    220 deg C heat-up nor loaded-sample/pressure-dependent heat transfer is
    independently calibrated. Agent sessions are not Python/filesystem isolation.
    """
    def __init__(self, *, ambient_temperature_K=295.15, ambient_pressure_pa=101325.0, scene_adapter=None):
        if not _vd56_number(ambient_pressure_pa) or not 50000 <= ambient_pressure_pa <= 120000:
            raise ValueError("Host ambient pressure must be finite in the assumed 50000..120000 Pa range")
        super().__init__(ambient_temperature_K=ambient_temperature_K, scene_adapter=scene_adapter)
        self.ambient_pressure_pa = float(ambient_pressure_pa)
        self._vacuum_boundaries = {}

    def create_instance(self, instance_id, asset_type_id=BINDER_VD56_ASSET_ID):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != BINDER_VD56_ASSET_ID:
                raise ValueError("Unique nonempty instance ID and VD56 asset type required")
            s = {"power.enabled": False, "thermal.target_k": self.ambient_temperature_K + 9.0, "thermal.temperature_k": self.ambient_temperature_K,
                "thermal.indicated_temperature_k": round(self.ambient_temperature_K - 273.15, 1) + 273.15, "door.angle_rad": 0.0,
                "pressure.pressure_pa": self.ambient_pressure_pa, "pressure.indicated_pressure_pa": round(self.ambient_pressure_pa),
                "vacuum.valve_open": False, "vent.valve_open": False, "vacuum.external_connected": False}
            if self.scene_adapter: self.scene_adapter(instance_id, _vd56_copy.deepcopy(s))
            self._instances[instance_id] = _VD56Instance(instance_id, asset_type_id, s)

    def session(self, principal_id):
        if principal_id not in self._principals: raise ValueError("Principal must first be authenticated and registered by host")
        return BinderVD56Session(self, principal_id)

    def _value(self, inst, name):
        return BINDER_VD56_PARAMETERS[name]["value"]

    def describe_host(self):
        return export_binder_vd56_metadata()["host_capabilities"]

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

    def _pressure_step(self, inst, dt):
        s = inst.state
        old = s["pressure.pressure_pa"]
        boundary = self._vacuum_boundaries.get(inst.instance_id)
        if s["door.angle_rad"] > 0 or s["vent.valve_open"]:
            pressure = self.ambient_pressure_pa + (old - self.ambient_pressure_pa) * _vd56_math.exp(-dt / self._value(inst, "vent_time_constant_s"))
        elif s["vacuum.valve_open"] and s["vacuum.external_connected"]:
            pressure = boundary["boundary_pressure_pa"] + (old - boundary["boundary_pressure_pa"]) * _vd56_math.exp(-dt / boundary["time_constant_s"])
        else:
            pressure = min(self.ambient_pressure_pa, old + self._value(inst, "isolated_pressure_rise_pa_s") * dt)
        s["pressure.pressure_pa"] = pressure
        indication = pressure
        if s["vacuum.valve_open"] and boundary and boundary["boundary_pressure_pa"] == 5000.0 and abs(pressure - 5000.0) <= 1.0:
            # Quantized synthetic steady indication, explicitly not sensor accuracy.
            indication = 5000.0 + 100.0 * _vd56_math.sin(2 * _vd56_math.pi * (self.time_s + dt) / self._value(inst, "pressure_proxy_period_s"))
        s["pressure.indicated_pressure_pa"] = round(indication)

    def step(self, dt_s):
        if not _vd56_number(dt_s) or not 0 <= dt_s <= 3600: raise ValueError("dt_s must be finite in [0,3600]")
        if not dt_s: return
        with self._lock:
            failed = False
            for inst in self._instances.values():
                before = _vd56_copy.deepcopy(inst.state)
                self._thermal_step(inst, dt_s)
                inst.state["thermal.indicated_temperature_k"] = round(inst.state["thermal.temperature_k"] - 273.15, 1) + 273.15
                self._pressure_step(inst, dt_s)
                if before != inst.state: inst.revision += 1
                updates = {k: _vd56_copy.deepcopy(v) for k, v in inst.state.items() if before[k] != v}
                if self.scene_adapter and updates:
                    try: self.scene_adapter(inst.instance_id, updates)
                    except Exception:
                        failed = True
                        self._scene_faults.append({"instance_id": inst.instance_id, "time_s": self.time_s + dt_s, "code": "SCENE_SYNC_FAILED"})
            self.time_s += dt_s
            if failed: raise BinderVD56SceneSynchronizationError("Process and common time advanced; scene updates failed; inspect trusted diagnostics")

    def debug_snapshot(self):
        with self._lock:
            result = super().debug_snapshot()
            result["external_vacuum_boundaries"] = _vd56_copy.deepcopy(self._vacuum_boundaries)
            result["ambient_pressure_pa"] = self.ambient_pressure_pa
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
            caps = {k: {f: _vd56_copy.deepcopy(info[f]) for f in ("input_schema", "output_schema", "description_zh", "preconditions_zh", "availability")} for k, info in spec["capabilities"].items()}
            if host.scene_adapter is None: caps["scene.set_door"]["availability"] = "unsupported"
            caps.update(_vd56_copy.deepcopy(spec["declared_capabilities"]))
            return {"protocol_version": "1.0.0", "asset_type_id": asset_type_id, "asset_version": spec["asset_version"], "instance_id": instance_id,
                "catalog_id": spec["catalog_id"], "model": spec["model"], "adapter": "basic40_runtime_thermal.BinderVD56Session", "capabilities": caps,
                "state_slots": {k: v for k, v in spec["state_slots"].items() if self._visible(v)},
                "limitations": ["Empty-system/standard-shelf thermal model calibrated only at 100 deg C; not gas/sample temperature, no sample evaporation or drying endpoint.",
                    "No pump included: explicit external boundary and response time supplied by host. Backfill omits gas composition and mass balance.",
                    "Estimated indications only; +/-100 Pa synthetic ripple applies solely at a near-steady 5000 Pa boundary, not general sensor accuracy.",
                    "Mechanical valves and external pump do not follow oven electrical power. No hardware or safety-rated interlock.",
                    "API filtering is not Python/filesystem isolation; in-memory deduplication does not survive restart."]}

    def read(self, *, instance_id, state_ids=None):
        host = self.__vd56_host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst: return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = BINDER_VD56_STATE_SPECS
            if state_ids is None: state_ids = [k for k, slot in slots.items() if self._visible(slot)]
            if not isinstance(state_ids, list) or any(not isinstance(k, str) for k in state_ids) or len(set(state_ids)) != len(state_ids): return self._error(instance_id, "INVALID_ARGUMENT")
            if any(k not in slots or not self._visible(slots[k]) for k in state_ids): return self._error(instance_id, "ACCESS_DENIED")
            observations = {k: {"value": _vd56_copy.deepcopy(inst.state[k]), "unit": slots[k]["unit"], "quality": "estimated" if slots[k]["origin"] == "derived" else "valid", "sample_time_s": host.time_s, "sample_window_s": None} for k in state_ids}
            return self._envelope(instance_id, observations=observations)

    def _invoke_checked(self, inst, capability, args, request):
        host, s = self.__vd56_host, inst.state
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request, status)
        if capability == "pressure.program": return fail("UNSUPPORTED", "unsupported")
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
        output_key, sid = _VD56_CAP_RESULTS[capability]
        updates = {sid: value}
        if host.scene_adapter:
            try: host.scene_adapter(inst.instance_id, _vd56_copy.deepcopy(updates))
            except Exception: return fail("DEVICE_ERROR", "failed")
        s.update(updates)
        inst.revision += 1
        return self._envelope(inst.instance_id, request, result={output_key: value})

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
import copy as _julabo_copy
import hashlib as _julabo_hashlib
import json as _julabo_json
import math as _julabo_math
import threading as _julabo_threading

JULABO_CORIO_CD200F_TYPE = 'instrument.julabo_corio_cd200f'
JULABO_CORIO_CD200F_SOURCE = 'https://www.julabo.com/en/products/refrigerated-circulators/refrigerated-heating-circulators/corio-cd-200f'
JULABO_CORIO_CD200F_COOLING_POINTS = ((253.15, 60.0), (263.15, 130.0), (273.15, 170.0), (293.15, 220.0))
JULABO_CORIO_CD200F_MAX_FLOW = 15.0 / 60000.0
JULABO_CORIO_CD200F_MAX_PRESSURE = 35000.0


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


def _julabo_slot(unit, origin, component, meaning, *, nullable=False, boolean=False, route=None, integral=False):
    return {'unit': unit, 'origin': origin, 'visibility': 'debug' if origin == 'process_truth' else 'agent',
        'access': 'read_write' if route else 'read_only', 'write_via': route,
        'value_schema': {'type': 'boolean'} if boolean else _julabo_schema(unit, nullable=nullable),
        'component_ids': [component], 'meaning_zh': meaning,
        'sampling': {'mode': 'integral' if integral else 'instant', 'time_domain': 'simulation', 'window': 'Since latest trusted bath configuration' if integral else None},
        'reference_conditions': 'Pump differential pressure; not absolute pressure' if unit == 'Pa' else 'Actual liquid volume flow at host-configured operating point' if unit == 'm^3/s' else 'Bath cover relative to base; positive q maps to Blender rotation_x=-q' if unit == 'rad' else None}


def export_julabo_corio_cd200f_metadata():
    """Static contract metadata; no instance configuration or privileged truth."""
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
    for sid, slot in slots.items():
        slot['read_binding'] = f"JulaboCorioCD200FSession.read(instance_id=..., state_ids=['{sid}'])"
        if slot['origin'] == 'derived': slot['observation_note_zh'] = '仿真派生量quality=estimated，缺测quality=unavailable；不声称实测传感器数据。'
    definitions = {
        'power.set': ({'enabled': {'type': 'boolean'}}, 'enabled', 'power.enabled', {'enabled': True}, ['controller', 'heater', 'refrigeration_unit', 'circulation_pump'], '设置供电；断电停止循环，保留温度与累计热量', ['power.enabled', 'circulation.running', 'circulation.flow_rate_m3_s', 'circulation.pressure_rise_pa']),
        'thermal.set_temperature': ({'temperature_K': _julabo_schema('K', 253.15, 423.15)}, 'setpoint_K', 'thermal.target_k', {'temperature_K': 313.15}, ['controller', 'display', 'heater', 'refrigeration_unit', 'bath_vessel'], '只写温度目标，需本机已上电；已配置流体时还必须处于其明确适用温区', ['thermal.target_k']),
        'circulation.set_running': ({'running': {'type': 'boolean'}}, 'running', 'circulation.running', {'running': True}, ['circulation_pump', 'pump_supply', 'pump_return'], '设置模拟循环许可；开启须已供电且浴热模型已配置；开启后才允许热输入', ['circulation.running', 'circulation.flow_rate_m3_s', 'circulation.pressure_rise_pa']),
        'scene.set_bath_lid': ({'angle_rad': _julabo_schema('rad', 0, _julabo_math.pi / 2)}, 'angle_rad', 'bath_lid.angle_rad', {'angle_rad': .5}, ['bath_lid'], '设置浴盖角度；须有同步场景回调，正角度映射负X旋转；不是机器人执行', ['bath_lid.angle_rad']),
    }
    caps = {}
    for name, (args, output, sid, example, components, meaning, immediate) in definitions.items():
        pre = []
        if name == 'thermal.set_temperature': pre = [{'description_zh': '本机上电且在已配置流体适用温区内', 'state_ids': ['power.enabled', 'thermal.model_configured'], 'enforced_by': 'adapter', 'check': 'power.enabled and (not configured or fluid_min_K <= temperature_K <= fluid_max_K)'}]
        if name == 'circulation.set_running': pre = [{'description_zh': '开启需要上电与显式浴热模型；这是模拟前提，不宣称硬件液位互锁', 'state_ids': ['power.enabled', 'thermal.model_configured'], 'enforced_by': 'adapter', 'check': 'not running or (power.enabled and thermal.model_configured)'}]
        if name.startswith('scene.'): pre = [{'description_zh': '同步场景适配器存在', 'state_ids': [], 'enforced_by': 'adapter', 'check': 'scene_adapter is not None'}]
        effects = [{'state_id': key, 'timing': 'immediate', 'description_zh': meaning} for key in immediate]
        effects += [{'state_id': key, 'timing': 'on_step', 'description_zh': '宿主推进后的有限功率热响应和累计能量；未配置时温度保持null'} for key in slots if key.startswith('thermal.') and key not in ('thermal.target_k', 'thermal.model_configured')]
        caps[name] = {'availability': 'implemented', 'visibility': 'agent', 'kind': 'simulation' if name.startswith('scene.') else 'device_command',
            'description_zh': meaning, 'component_ids': components, 'input_schema': _julabo_object(args), 'output_schema': _julabo_object({output: _julabo_copy.deepcopy(slots[sid]['value_schema'])}),
            'example_arguments': [example], 'observes': [sid], 'state_effects': effects, 'preconditions': pre,
            'binding': f"basic40_runtime_thermal.JulaboCorioCD200FSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
            'scene_backend_required': name.startswith('scene.'), 'completion_criterion_zh': '同步返回确认设定完成；温度变化及累计热量由随后host.step推进，read不推进时间。'}
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
    }
    host = {
        'sim.step': {'kind': 'simulation', 'visibility': 'trusted_host_only', 'input_schema': _julabo_object({'dt_s': _julabo_schema('s', 0, 3600)}), 'binding': 'JulaboCorioCD200FRuntime.step(dt_s)', 'partial_failure': 'Process and shared clock commit before scene callbacks; SceneSynchronizationError reports possible partial scene effects.'},
        'sim.configure_bath_model': {'kind': 'configuration', 'visibility': 'trusted_host_only', 'binding': 'JulaboCorioCD200FRuntime.configure_bath_model(instance_id, *, effective_heat_capacity_J_K, heat_loss_W_K, lid_open_loss_W_K, initial_temperature_K, minimum_temperature_K, maximum_temperature_K, evidence)', 'description_zh': '仅断电时初始化显式热模型并重置该配置的累计热量；不是加液或重置真实仪器。'},
        'sim.configure_bath_fluid': {'kind': 'configuration', 'visibility': 'trusted_host_only', 'binding': 'JulaboCorioCD200FRuntime.configure_bath_fluid(instance_id, *, mass_kg, specific_heat_J_kg_K, heat_loss_W_K, lid_open_loss_W_K, initial_temperature_K, minimum_temperature_K, maximum_temperature_K, evidence)', 'description_zh': 'C=m*cp，仅浴液单节点假设；未包含未知槽体/样品热容。'},
        'sim.configure_circulation_boundary': {'kind': 'configuration', 'visibility': 'trusted_host_only', 'binding': 'JulaboCorioCD200FRuntime.configure_circulation_boundary(instance_id, *, connected, flow_rate_m3_s=None, pressure_rise_Pa=None, evidence)', 'description_zh': '显式外部运行点；无默认泵曲线、回路阻力或物料转移解算；拒绝同时取两个厂家极值。'},
    }
    return {'asset_types': {JULABO_CORIO_CD200F_TYPE: {'catalog_id': 'CHEM-187', 'model': 'JULABO CORIO CD-200F 9012701.N1.03', 'asset_version': '1.0.0', 'state_slots': slots, 'capabilities': caps, 'parameters': params, 'declared_capabilities': {}}},
        'host_capabilities': host, 'trusted_scene_callback': 'scene_adapter(instance_id, updates): synchronous SI; bath_lid.angle_rad maps to bath_lid_joint about -X; no callback means scene command unsupported',
        'result_schema_scope': 'capability.output_schema validates response.result only',
        'limitations': ['Single well-mixed node; no fluid phase changes, evaporation, sample chemistry, fluid mass transfer, or external thermal circuit.', 'Cooling power interpolates manufacturer ethanol reference points only at 20°C ambient; applying to another fluid is an explicit host reference-model assumption, not validated performance.', 'Above20°C refrigeration capacity is unknown and not extrapolated; passive heat loss and heating remain modeled.', 'UA and C are explicitly configured assumptions/estimates; no fitted manufacturer defaults.', 'API access filtering is not a Python-process or filesystem security sandbox. Host and debug files must be inaccessible to untrusted agents.', 'In-memory request deduplication only; no restart persistence, hardware control or robot execution.']}


class JulaboCorioCD200FRuntime:
    """Trusted host; give sessions, not this object or debug files, to agents."""
    def __init__(self, *, ambient_temperature_K=293.15, scene_adapter=None):
        if not _julabo_number(ambient_temperature_K) or not 273.15 <= ambient_temperature_K <= 313.15:
            raise ValueError('Host ambient must be finite in assumed 273.15..313.15 K range')
        self.ambient_temperature_K = float(ambient_temperature_K)
        self.scene_adapter = scene_adapter
        self.time_s = 0.0
        self._instances, self._requests, self._scene_faults = {}, {}, []
        self._principals = {'__julabo_public_agent__': ('agent', None)}
        self._logs = {'__julabo_public_agent__': []}
        self._lock = _julabo_threading.RLock()
        self._metadata = export_julabo_corio_cd200f_metadata()
        self._agent = JulaboCorioCD200FSession(self, '__julabo_public_agent__')

    def create_instance(self, instance_id, asset_type_id=JULABO_CORIO_CD200F_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != JULABO_CORIO_CD200F_TYPE:
                raise ValueError('Unique nonempty ID and declared JULABO type required')
            state = {'power.enabled': False, 'thermal.target_k': self.ambient_temperature_K, 'thermal.temperature_k': None, 'thermal.indicated_temperature_k': None,
                'thermal.model_configured': False, 'thermal.cooling_curve_supported': False, 'bath_lid.angle_rad': 0.0, 'circulation.running': False, 'circulation.external_connected': False,
                'circulation.flow_rate_m3_s': 0.0, 'circulation.pressure_rise_pa': 0.0,
                'thermal.heater_energy_j': 0.0, 'thermal.cooling_energy_j': 0.0, 'thermal.environment_energy_j': 0.0, 'thermal.stored_energy_change_j': 0.0}
            self._push_before_commit(instance_id, state)
            self._instances[instance_id] = {'state': state, 'revision': 0, 'thermal_configuration': None, 'circulation_boundary': None, 'energy_start_s': self.time_s}

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
            state.update({'thermal.target_k': float(initial_temperature_K), 'thermal.temperature_k': float(initial_temperature_K), 'thermal.indicated_temperature_k': self._indicate(initial_temperature_K), 'thermal.model_configured': True, 'thermal.cooling_curve_supported': self._cooling_supported(initial_temperature_K)})
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

    def _step_thermal(self, inst, dt):
        state, cfg = inst['state'], inst['thermal_configuration']
        if cfg is None: return
        capacity = cfg['effective_heat_capacity_J_K']
        loss = cfg['heat_loss_W_K'] + cfg['lid_open_loss_W_K'] * state['bath_lid.angle_rad'] / (_julabo_math.pi / 2)
        ambient, target, temperature = self.ambient_temperature_K, state['thermal.target_k'], state['thermal.temperature_k']
        enabled = state['power.enabled'] and state['circulation.running']
        remaining, heating, cooling, environment = float(dt), 0.0, 0.0, 0.0
        # Exact affine ODE on each manufacturer cooling segment; split at targets/knots.
        while remaining > 1e-10:
            cool_a, cool_b = self._cooling_line(temperature)
            required = loss * (temperature - ambient)
            if not enabled: u_a, u_b = 0.0, 0.0
            elif temperature < target - 1e-10: u_a, u_b = 2000.0, 0.0
            elif temperature > target + 1e-10: u_a, u_b = -cool_a, -cool_b
            else:
                available = max(0.0, cool_a + cool_b * temperature)
                u_a, u_b = min(2000.0, max(-available, required)), 0.0
            a, b = (u_a + loss * ambient) / capacity, (u_b - loss) / capacity
            rate = a + b * temperature
            if rate > 0 and enabled and temperature > target + 1e-10:
                cool_a, cool_b = self._cooling_line(temperature, 1)
                u_a, u_b = -cool_a, -cool_b
                a, b = (u_a + loss * ambient) / capacity, (u_b - loss) / capacity
                rate = a + b * temperature
            duration = remaining
            candidates = [target, cfg['minimum_temperature_K'], cfg['maximum_temperature_K']]
            candidates += [point[0] for point in JULABO_CORIO_CD200F_COOLING_POINTS]
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
            energy_input = u_a * duration + u_b * integrated_t
            heating += max(0.0, energy_input)
            cooling += max(0.0, -energy_input)
            environment += loss * (integrated_t - ambient * duration)
            remaining -= duration
            temperature = next_temperature
        state['thermal.temperature_k'] = temperature
        state['thermal.indicated_temperature_k'] = self._indicate(temperature)
        state['thermal.cooling_curve_supported'] = self._cooling_supported(temperature)
        state['thermal.heater_energy_j'] += heating
        state['thermal.cooling_energy_j'] += cooling
        state['thermal.environment_energy_j'] += environment
        state['thermal.stored_energy_change_j'] = capacity * (temperature - cfg['initial_temperature_K'])

    def step(self, dt_s):
        if not _julabo_number(dt_s) or not 0 <= dt_s <= 3600: raise ValueError('dt_s must be finite in [0,3600]')
        if not dt_s: return
        with self._lock:
            candidates = _julabo_copy.deepcopy(self._instances)
            for inst in candidates.values():
                before = _julabo_copy.deepcopy(inst['state'])
                self._step_thermal(inst, float(dt_s))
                if any(isinstance(v, float) and not _julabo_math.isfinite(v) for v in inst['state'].values()): raise ValueError('Configuration produced nonfinite numerical state; step unchanged')
                if inst['state'] != before: inst['revision'] += 1
            self._instances, self.time_s = candidates, self.time_s + float(dt_s)
            faults = []
            if self.scene_adapter:
                for key, inst in self._instances.items():
                    try: self.scene_adapter(key, _julabo_copy.deepcopy(inst['state']))
                    except Exception: faults.append({'instance_id': key, 'time_s': self.time_s, 'code': 'SCENE_SYNCHRONIZATION_FAILED'})
            self._scene_faults.extend(faults)
            if faults: raise SceneSynchronizationError('Process and clock advanced; scene synchronization failed with possible partial backend effects') from None

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
            response = self._invoke_checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, _julabo_copy.deepcopy(response))
            host._logs[self.__principal_id].append({'instance_id': instance_id, 'capability_id': capability_id, 'request_id': request_id, 'response': _julabo_copy.deepcopy(response)})
            return response

    def _invoke_checked(self, instance_id, inst, capability, args, request_id):
        host, state = self.__host, inst['state']
        fail = lambda code, status='rejected': self._error(instance_id, code, request_id, status)
        caps = host._metadata['asset_types'][JULABO_CORIO_CD200F_TYPE]['capabilities']
        if capability not in caps: return fail('UNKNOWN_CAPABILITY')
        schema = caps[capability]['input_schema']['properties']
        if set(args) != set(schema): return fail('INVALID_ARGUMENT')
        for name, item in schema.items():
            value = args[name]
            if item['type'] == 'boolean' and type(value) is not bool: return fail('INVALID_ARGUMENT')
            if item['type'] == 'number' and (not _julabo_number(value) or not item.get('minimum', -float('inf')) <= value <= item.get('maximum', float('inf'))): return fail('INVALID_ARGUMENT')
        if capability.startswith('scene.') and host.scene_adapter is None: return fail('UNSUPPORTED', 'unsupported')
        if capability == 'power.set':
            updates, result = {'power.enabled': args['enabled']}, {'enabled': args['enabled']}
            if not args['enabled']: updates.update({'circulation.running': False, 'circulation.flow_rate_m3_s': 0.0, 'circulation.pressure_rise_pa': 0.0})
        elif capability == 'thermal.set_temperature':
            if not state['power.enabled']: return fail('PRECONDITION_FAILED')
            cfg = inst['thermal_configuration']
            if cfg and not cfg['minimum_temperature_K'] <= args['temperature_K'] <= cfg['maximum_temperature_K']: return fail('PRECONDITION_FAILED')
            updates, result = {'thermal.target_k': float(args['temperature_K'])}, {'setpoint_K': float(args['temperature_K'])}
        elif capability == 'circulation.set_running':
            if args['running'] and not (state['power.enabled'] and state['thermal.model_configured']): return fail('PRECONDITION_FAILED')
            running, boundary = args['running'], inst['circulation_boundary']
            updates = {'circulation.running': running, 'circulation.flow_rate_m3_s': (boundary['flow_rate_m3_s'] if boundary else None) if running else 0.0,
                'circulation.pressure_rise_pa': (boundary['pressure_rise_Pa'] if boundary else None) if running else 0.0}
            result = {'running': running}
        else: updates, result = {'bath_lid.angle_rad': float(args['angle_rad'])}, {'angle_rad': float(args['angle_rad'])}
        changed = {sid: value for sid, value in updates.items() if value != state[sid]}
        if changed:
            try: host._push_before_commit(instance_id, changed)
            except RuntimeError: return fail('DEVICE_ERROR', 'failed')
            state.update(changed)
            inst['revision'] += 1
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


def export_miele_plw6111_metadata():
    """Static metadata only; all unknown manufacturer thermal parameters are null."""
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
            pre.append({'description_zh': '程序停止且已注册同步场景适配器', 'state_ids': ['program.running'], 'enforced_by': 'adapter', 'check': 'not program.running and scene_adapter is not None'})
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
        'drying_air_temperature_K': p(None, 'K', 'unknown', 'Dry state is a timer only; no drying-air energy or moisture model.'),
        'basket_extension_limit_m': p(.4, 'm', 'assumption', 'Illustrative three-slot front extension, not measured manufacturer travel.'),
        'door_angle_limit_rad': p(_miele_math.pi / 2, 'rad', 'assumption', 'Illustrative full-open mechanical angle; actual scene mapping defined in model bindings.'),
        'whole_machine_mass_kg': p(251.0, 'kg', 'public_document', MIELE_PLW6111_SOURCE + '; not used for component inertia or water heat capacity'),
    }
    limitations = ['Water-only single-node heat equation C*dT/dt=P-UA*(T-Ta); rated P<=7500W only in wash/rinse. Explicit host C and UA are assumptions, not manufacturer calibration.',
        'All actual water charge, component thermal capacities, heat losses and heat-up time remain unknown (null). Example test parameters do not become product defaults.',
        'Three serial phases share one effective water node; filling, draining, rinse water exchange, detergent dosing, spray hydraulics, thermal disinfection, soil removal and material transfer are not modeled.',
        'Dry phase is timed only; 7500W is never applied to drying air. No moisture, drying effectiveness, H14 filtration or drying-air thermal solver.',
        'Program times are explicit simulation settings, not factory recipes. Wash/rinse timers start at target water temperature; unreachable targets leave the phase running until stopped.',
        'Scene controls require a synchronous adapter; no robot grasp, contact simulation or physical hardware connection.',
        'API truth filtering is implemented for session metadata, observations, responses and logs; it is not a Python introspection or filesystem security sandbox. Do not give agents the trusted host or debug output files.',
        'Request deduplication is in-memory only. No operation/poll/cancel objects; start/stop are synchronous commands controlling subsequent host evolution.']
    host = {'sim.step': {'kind': 'simulation', 'visibility': 'trusted_host_only', 'input_schema': _miele_object({'dt_s': _miele_schema('s', 0, 86400)}), 'binding': 'MielePLW6111Runtime.step(dt_s)', 'partial_failure': 'Process and world clock commit before scene callbacks. SceneSynchronizationError reports possible partial backend effects.'},
        'sim.configure_water_model': {'kind': 'configuration', 'visibility': 'trusted_host_only', 'binding': 'MielePLW6111Runtime.configure_water_model(instance_id, *, effective_heat_capacity_J_K, heat_loss_W_K, initial_temperature_K, maximum_temperature_K, evidence)', 'description_zh': '断电且程序停止时显式初始化有效水热模型；所有参数均为有说明的宿主输入，不是注水或真实硬件重置。'}}
    return {'asset_types': {MIELE_PLW6111_TYPE: {'catalog_id': 'CHEM-191', 'model': 'Miele PLW 6111 DP SC CL H14, 10958380', 'asset_version': '1.0.0', 'state_slots': slots, 'capabilities': caps, 'declared_capabilities': {}, 'parameters': params}}, 'host_capabilities': host,
        'limitations': limitations, 'trusted_scene_callback': 'scene_adapter(instance_id, updates) receives SI door, three basket extensions, indicated water temperature and program phase; host is sole authority.', 'result_schema_scope': 'capability.output_schema validates response.result only'}


class MielePLW6111Runtime:
    """Trusted independent multi-instance host. Supply sessions to agents."""
    def __init__(self, *, ambient_temperature_K=293.15, scene_adapter=None):
        if not _miele_number(ambient_temperature_K) or not 273.15 <= ambient_temperature_K <= 313.15: raise ValueError('Finite ambient in assumed 273.15..313.15K envelope required')
        self.ambient_temperature_K, self.scene_adapter, self.time_s = float(ambient_temperature_K), scene_adapter, 0.0
        self._instances, self._requests, self._scene_faults = {}, {}, []
        self._principals, self._logs = {'__miele_public__': ('agent', None)}, {'__miele_public__': []}
        self._lock, self._metadata = _miele_threading.RLock(), export_miele_plw6111_metadata()
        self._agent = MielePLW6111Session(self, '__miele_public__')

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
            self._push_before_commit(instance_id, state)
            self._instances[instance_id] = {'state': state, 'revision': 0, 'water_configuration': None, 'energy_start_s': self.time_s}

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
            spec = self.__host._metadata['asset_types'][MIELE_PLW6111_TYPE]
            caps = _miele_copy.deepcopy(spec['capabilities'])
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
                slot, value = slots[sid], _miele_copy.deepcopy(inst['state'][sid])
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
        host, state = self.__host, inst['state']
        fail = lambda code, status='rejected': self._error(instance_id, code, request_id, status)
        caps = host._metadata['asset_types'][MIELE_PLW6111_TYPE]['capabilities']
        if capability not in caps: return fail('UNKNOWN_CAPABILITY')
        schema = caps[capability]['input_schema']['properties']
        if set(args) != set(schema): return fail('INVALID_ARGUMENT')
        for name, item in schema.items():
            value = args[name]
            if item['type'] == 'boolean' and type(value) is not bool: return fail('INVALID_ARGUMENT')
            if item['type'] == 'number' and (not _miele_number(value) or not item.get('minimum', -float('inf')) <= value <= item.get('maximum', float('inf'))): return fail('INVALID_ARGUMENT')
            if item['type'] == 'string' and (not isinstance(value, str) or value not in item['enum']): return fail('INVALID_ARGUMENT')
        if caps[capability]['scene_backend_required'] and host.scene_adapter is None: return fail('UNSUPPORTED', 'unsupported')
        if capability not in ('power.set', 'washing.stop') and state['program.running']: return fail('BUSY')
        if capability in ('thermal.set_temperature', 'washing.configure_program', 'washing.start') and not state['power.enabled']: return fail('PRECONDITION_FAILED')
        if capability == 'power.set':
            updates, result = {'power.enabled': args['enabled']}, {'enabled': args['enabled']}
            if not args['enabled'] and state['program.running']: updates.update({'program.running': False, 'program.phase': 'stopped'})
        elif capability == 'thermal.set_temperature':
            cfg = inst['water_configuration']
            if args['temperature_K'] < host.ambient_temperature_K or (cfg and args['temperature_K'] > cfg['maximum_temperature_K']): return fail('PRECONDITION_FAILED')
            updates, result = {'thermal.target_k': float(args['temperature_K'])}, {'setpoint_K': float(args['temperature_K'])}
        elif capability in ('door.open', 'door.close'):
            closed = capability == 'door.close'
            if closed and any(state['basket.' + name + '.extension_m'] != 0 for name in MIELE_PLW6111_BASKETS): return fail('PRECONDITION_FAILED')
            updates = {'door.angle_rad': 0.0 if closed else _miele_math.pi / 2, 'door.closed': closed}
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
        else:
            updates, result = {'program.running': False, 'program.phase': 'stopped'}, {'running': False}
        changed = {sid: value for sid, value in updates.items() if value != state[sid]}
        if changed:
            candidate = {**state, **changed}
            try: host._push_before_commit(instance_id, candidate)
            except RuntimeError: return fail('DEVICE_ERROR', 'failed')
            state.update(changed)
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


def export_multiwave_7101_metadata():
    """Public type metadata. No instance state or private truth is exported."""
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
    return {'asset_types': {MULTIWAVE_7101_TYPE: {'catalog_id': 'CHEM-047', 'model': 'Anton Paar Multiwave 7101, 24 x 8 mL rack', 'asset_version': '1.1.0', 'state_slots': slots, 'capabilities': caps, 'declared_capabilities': unsupported, 'parameters': params}},
        'host_capabilities': {'sim.step': {'kind': 'simulation', 'visibility': 'trusted_host_only', 'input_schema': _multiwave_object({'dt_s': n('s', 0, 86400)}), 'binding': 'Multiwave7101Runtime.step(dt_s)', 'partial_failure': 'Clock/process commit before callbacks; synchronization failure may leave partially updated scene.'}},
        'trusted_scene_callback': 'scene_adapter(instance_id,updates): full public scene snapshot; door.angle_rad maps about negativeX; rack.occupied_slots holds24 visual flags; no process_truth is passed.',
        'result_schema_scope': 'capability.output_schema validates response.result only',
        'limitations': ['Thermal node is an illustrative loaded-system surrogate, not sample/acid thermodynamics, boiling, pressure or digestion kinetics.', 'C inferred using assumed absorbed power and UA; published heatup constrains but does not identify these quantities. No measured calibration claim.', 'Empty/occupied visual slots do not alter synthetic thermal capacity or represent liquid transfer.', 'Temperature display is ideal rounded0.1degC, quality=estimated; no real sensor model.', 'Door inhibit is a simulation precondition, not verified physical interlock or pressure-release safety.', 'Passive cooling only; no measured active-cooling performance.', 'API access filtering is not a Python/process/filesystem sandbox; host/debug artifacts must remain trusted.', 'No hardware/robot execution, restart-persistent deduplication, pressure or chemical safety claims.']}


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


class Multiwave7101Runtime:
    """Trusted shared-clock host. Expose a session, never host/debug internals."""
    def __init__(self, *, ambient_temperature_K=MULTIWAVE_7101_AMBIENT_K, scene_adapter=None):
        if not _number(ambient_temperature_K) or not 273.15 <= ambient_temperature_K <= 313.15:
            raise ValueError('Ambient must be finite within the assumed273.15..313.15K envelope')
        if scene_adapter is not None and not callable(scene_adapter): raise ValueError('Scene adapter must be callable')
        self.ambient_temperature_K = float(ambient_temperature_K)
        self.scene_adapter, self.time_s = scene_adapter, 0.0
        self._instances, self._requests, self._scene_faults = {}, {}, []
        self._principals, self._logs = {'__public_agent__': ('agent', None)}, {'__public_agent__': []}
        self._metadata, self._lock = export_multiwave_7101_metadata(), threading.RLock()
        self._agent = Multiwave7101Session(self, '__public_agent__')

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
            if self.scene_adapter: self.scene_adapter(instance_id, self._scene_payload(state))
            self._instances[instance_id] = {'state': state, 'revision': 0}

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
        return min(p, MULTIWAVE_7101_LOSS_W_K * max(0.0, target - self.ambient_temperature_K))

    def _advance(self, state, dt):
        ambient, c = self.ambient_temperature_K, MULTIWAVE_7101_CAPACITY_J_K
        ua = MULTIWAVE_7101_LOSS_W_K + (0.0 if state['door.closed'] else MULTIWAVE_7101_OPEN_LOSS_W_K)
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
            spec = copy.deepcopy(host._metadata['asset_types'][MULTIWAVE_7101_TYPE])
            spec['state_slots'] = {sid: info for sid, info in spec['state_slots'].items() if self._visible(info)}
            spec['capabilities'].update(spec.pop('declared_capabilities'))
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

    def _checked(self, sid, inst, cap, args, request_id):
        host, old = self.__host, inst['state']
        spec = host._metadata['asset_types'][MULTIWAVE_7101_TYPE]
        fail = lambda code, status='rejected': self._error(sid, code, request_id, status)
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
        if spec['capabilities'][cap]['scene_backend_required'] and host.scene_adapter is None: return fail('UNSUPPORTED', 'unsupported')
        if cap == 'rack.set_slot' and (old['door.closed'] or old['microwave.enabled']): return fail('PRECONDITION_FAILED')
        state = copy.deepcopy(old)
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
    return {'asset_types':{PARR_4591_TYPE:{'catalog_id':'CHEM-018','model':'Parr4591 moveable head,25mL,standard FKM','asset_version':'1.0.0','state_slots':slots,'capabilities':caps,'declared_capabilities':unsupported,'parameters':params}},
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
        self._metadata = export_parr_4591_metadata()
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
                'gas.moles':moles,'gas.initial_moles':moles,'gas.exchanged_moles':0.0,'thermal.heater_energy_j':0.0,'thermal.environment_energy_j':0.0,'thermal.stored_energy_change_j':0.0}
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
        return PARR_4591_POWER_W if t<target-1e-10 else min(PARR_4591_POWER_W,PARR_4591_LOSS_W_K*(target-self.ambient_temperature_K))

    def _advance(self,state,dt):
        ambient,c=self.ambient_temperature_K,PARR_4591_CAPACITY_J_K
        ua=PARR_4591_LOSS_W_K+(0.0 if state['lid.closed'] else 2.1)
        old,target=state['thermal.temperature_k'],state['thermal.target_k']
        power=PARR_4591_POWER_W if state['power.enabled'] and state['heater.enabled'] and state['lid.closed'] else 0.0
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
            spec = copy.deepcopy(host._metadata['asset_types'][PARR_4591_TYPE])
            spec['state_slots'] = {sid: info for sid, info in spec['state_slots'].items() if self._visible(info)}
            spec['capabilities'].update(spec.pop('declared_capabilities'))
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


class Parr4591Runtime(_Parr4591ThermalBase):
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
        power = PARR_4591_POWER_W if original['power.enabled'] and original['heater.enabled'] else 0.0

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


def export_parr_4591_metadata():
    metadata = _parr_4591_base_metadata()
    caps = metadata['asset_types'][PARR_4591_TYPE]['capabilities']
    slots = metadata['asset_types'][PARR_4591_TYPE]['state_slots']
    for info in caps.values():
        info['observes'] = [sid for sid in info['observes'] if slots[sid]['visibility'] == 'agent']
    metadata['limitations'].append('Non-isothermal vent model integrates dn/dt=(Pamb*V/(R*T(t))-n)/15s along the exact thermal trajectory. Adaptive Simpson absolute tolerance1e-14 K^-1; exponential tail beyond40 time constants omitted (<exp(-40)/273.15 K^-1). Failure to converge rejects the entire host step before commit.')
    return metadata


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


def export_radleys_mya4_metadata():
    """Static discovery metadata; never includes live sample or process truth."""
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
        'thermal.set_temperature': ({'zone_index': zone_arg, 'temperature_K': _mya4_num('K', 243.15, 453.15)}, {'zone_index': zone_arg, 'setpoint_K': _mya4_num('K')}, {'zone_index': 0, 'temperature_K': 333.15}, '只写指定区目标；块模式243.15..453.15K、液模式253.15..423.15K，冷端还受冷源限制。'),
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
    return {'asset_types':{MYA4_TYPE:{'catalog_id':'CHEM-021','model':'Radleys Mya 4; standard magnetic stirring configuration','asset_version':'1.0.0','state_slots':slots,'capabilities':caps,'declared_capabilities':declared,'parameters':params}},
        'host_capabilities':{'sim.step':{'kind':'simulation','visibility':'trusted_host_only','input_schema':_mya4_object({'dt_s':_mya4_num('s',0,86400)}),'binding':'basic40_runtime_thermal.RadleysMya4Runtime.step(dt_s)'},
            'sim.configure_cooling_source':{'kind':'configuration','visibility':'trusted_host_only','binding':'RadleysMya4Runtime.configure_cooling_source(available=True, temperature_K=278.15, flow_m3_s=0.003/60)'}},
        'limitations':limitations,'equations':['Cb*dTb_i/dt=Qcontrol_i-UA*(Tb_i-Ta)-G*(Tb_i-Tl_i)+sum_j(Kij*(Tb_j-Tb_i))','Cl_i*dTl_i/dt=G*(Tb_i-Tl_i); Cl_i=rho*cp*fill_volume_m3','Qcontrol_i=clip(Kp*(target_i-Tfeedback_i)+UA*(target_i-Ta),-Pcool,Pheat)','Cross-zone nearest-neighbor pairs(1,2),(2,3),(3,4), with symmetric Kij; dtheta_i/dt=angular_speed_i; RK4 integration.'],
        'scene_callback':'scene_adapter(instance_id, payload) receives a flat dict of agent-visible state only; zone_N.stirring.angle_rad maps to rotor +Z rotation, SI.',
        'is_measured_calibration':False}


class RadleysMya4Runtime:
    """Trusted host owns clock, cooling configuration and sessions; do not expose to agents."""
    def __init__(self, *, ambient_temperature_K=298.15, scene_adapter=None):
        if not _number(ambient_temperature_K) or not 283.15 <= ambient_temperature_K <= 308.15: raise ValueError('Ambient outside assumed283.15..308.15K envelope')
        if scene_adapter is not None and not callable(scene_adapter): raise ValueError('Scene adapter must be callable')
        self.ambient_temperature_K,self.scene_adapter,self.time_s=float(ambient_temperature_K),scene_adapter,0.0
        self._cooling={'cooling.available':False,'cooling.temperature_k':288.15,'cooling.flow_m3_s':3e-3/60}
        self._instances,self._requests,self._scene_faults={},{},[]
        self._principals,self._logs={'__public_agent__':('agent',None)},{'__public_agent__':[]}
        self._metadata,self._lock=export_radleys_mya4_metadata(),threading.RLock()
        self._agent=RadleysMya4Session(self,'__public_agent__')
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
            if self.scene_adapter: self.scene_adapter(instance_id,self._scene_payload(state))
            self._instances[instance_id]={'state':state,'revision':0}
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
    def _target_valid(self,s,z,target,mode):
        low,high=(243.15,453.15) if mode=='block' else (253.15,423.15)
        if not low<=target<=high: return False
        if target<self.ambient_temperature_K and s['cooling.available']:
            # Official endpoints:5°C coolant gives−30°C block;15°C gives−25°C. Intermediate interpolation is a simulation assumption.
            coolant=s['cooling.temperature_k']
            if coolant>288.15: return False
            source_min=243.15+0.5*(coolant-278.15) if mode=='block' else 253.15
            if target<source_min: return False
        targets=[target if other==z else s[other+'.thermal.target_k'] for other in MYA4_ZONES]
        return max(targets)-min(targets)<=200.0+1e-10
    def _derivative(self,s,y):
        a=MYA4_ASSUMPTIONS; cb=a['effective_block_capacity_J_K']; ta=self.ambient_temperature_K
        dy=[0.0]*20
        coupling=[0.0]*4
        for i,j in ((0,1),(1,2),(2,3)):
            q=a['adjacent_block_conductance_W_K']*(y[j]-y[i]); coupling[i]+=q;coupling[j]-=q
        for i,z in enumerate(MYA4_ZONES):
            q=0.0; target=s[z+'.thermal.target_k']
            if s['power.enabled'] and s['cooling.available'] and s[z+'.thermal.enabled']:
                feedback=y[i] if s[z+'.thermal.probe_mode']=='block' else y[4+i]
                demand=a['feedback_gain_W_K']*(target-feedback)+a['block_ambient_conductance_W_K']*(target-ta)
                q=max(-a['effective_cooling_limit_W'],min(a['effective_heating_limit_W'],demand))
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
                while remaining>1e-12:
                    h=min(remaining,MYA4_ASSUMPTIONS['maximum_integration_step_s'])
                    for z in MYA4_ZONES:
                        if s[z+'.program.running'] and s[z+'.program.phase']=='holding':
                            needed=s[z+'.program.segments'][s[z+'.program.segment_index']]['hold_s']-s[z+'.program.hold_elapsed_s']
                            if needed>1e-12:h=min(h,needed)
                    self._physics(s,h)
                    for z in MYA4_ZONES:self._program(s,z,h)
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
    def _error(self,sid,code,request_id=None,status='rejected'):
        messages={'UNKNOWN_INSTANCE':'Instance unavailable.','UNKNOWN_CAPABILITY':'Capability unavailable.','INVALID_ARGUMENT':'Arguments violate schema or bounds.','ACCESS_DENIED':'Request outside session policy.',
            'PRECONDITION_FAILED':'Required configuration or operating state unavailable.','BUSY':'Zone program is running.','REQUEST_CONFLICT':'Request ID already belongs to different arguments.','UNSUPPORTED':'Execution unavailable in this adapter.','DEVICE_ERROR':'Scene callback did not complete command.'}
        return self._envelope(sid,request_id,status=status,error={'code':code,'message':messages[code],'retryable':False,'side_effects':'unknown' if code=='DEVICE_ERROR' else 'none','progress':None})
    def describe(self,*,asset_type_id=None,instance_id=None):
        host=self.__host
        with host._lock:
            if (asset_type_id is None)==(instance_id is None):return self._error(None,'INVALID_ARGUMENT')
            if instance_id is not None and not self._instance(instance_id):return self._error(instance_id,'UNKNOWN_INSTANCE')
            if asset_type_id is not None and asset_type_id!=MYA4_TYPE:return self._error(None,'INVALID_ARGUMENT')
            spec=copy.deepcopy(host._metadata['asset_types'][MYA4_TYPE]);spec['state_slots']={sid:info for sid,info in spec['state_slots'].items() if self._visible(info)}
            spec['capabilities'].update(spec.pop('declared_capabilities'))
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
            obs={sid:{'value':copy.deepcopy(inst['state'][sid]),'unit':slots[sid]['unit'],'quality':'estimated' if slots[sid]['origin']=='derived' else 'valid','sample_time_s':host.time_s,'sample_window_s':None} for sid in state_ids}
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
        host,old=self.__host,inst['state'];spec=host._metadata['asset_types'][MYA4_TYPE]
        fail=lambda code,status='rejected':self._error(sid,code,request_id,status)
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
        needs_power=cap in ('thermal.set_temperature','thermal.set_probe_mode','thermal.configure_program','thermal.start_program','stirring.set_speed') or (cap in ('thermal.set_enabled','stirring.set_enabled') and args['enabled'])
        if needs_power and not old['power.enabled']:return fail('PRECONDITION_FAILED')
        if cap=='thermal.set_temperature' and not host._target_valid(old,z,args['temperature_K'],old[z+'.thermal.probe_mode']):return fail('PRECONDITION_FAILED')
        if cap=='thermal.set_probe_mode' and not host._target_valid(old,z,old[z+'.thermal.target_k'],args['mode']):return fail('PRECONDITION_FAILED')
        if cap=='thermal.configure_program' and any(not host._target_valid(old,z,seg['temperature_K'],old[z+'.thermal.probe_mode']) for seg in args['segments']):return fail('PRECONDITION_FAILED')
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
        return self._envelope(sid,request_id,result=result)
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
