"""Synthetic, simulator-neutral runtime for the first four basic-40 assets.

Only LabHost advances the common clock. LabSession identities are assigned by
the host, never taken from a request's arguments. This is an API access filter,
NOT a sandbox against Python introspection, shared memory or filesystem access.
Deploy the host in a separate trusted process before giving an untrusted agent
filesystem/code execution access. No real instrument or robot driver is present.

Scene commands require a synchronous, atomic scene_adapter callback. It receives
(instance_id, updates), with SI-valued state IDs. Success means that callback
returned; evaluated Blender poses require separate backend verification.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import platform
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROTOCOL_VERSION = "1.0.0"
RPM_TO_RAD_S = 2 * math.pi / 60


def _slot(unit, origin, visibility="agent", access="read_only", capability=None, parameter=None):
    return {"unit": unit, "origin": origin, "visibility": visibility, "access": access,
            "write_via": None if not capability else {"capability_id": capability, "parameter": parameter}}


COMMON_SLOTS = {
    "power.enabled": _slot("1", "setpoint", access="read_write", capability="power.set", parameter="enabled"),
}
THERMAL_SLOTS = {
    "thermal.target_k": _slot("K", "setpoint", access="read_write", capability="thermal.set_temperature", parameter="temperature_K"),
    "thermal.temperature_k": _slot("K", "process_truth", "debug"),
    "thermal.indicated_temperature_k": _slot("K", "derived"),
}
ASSET_SPECS: dict[str, dict[str, Any]] = {
    "instrument.ika_rct_digital": {
        "catalog_id": "CHEM-008", "model": "IKA RCT digital", "asset_version": "1.0.0",
        "capabilities": ["power.set", "thermal.set_temperature", "stirring.set_speed"],
        "state_slots": {**COMMON_SLOTS, **THERMAL_SLOTS,
            "stirring.target_rad_s": _slot("rad/s", "setpoint", access="read_write", capability="stirring.set_speed", parameter="speed_rad_s"),
            "stirring.speed_rad_s": _slot("rad/s", "process_truth", "debug"),
            "stirring.indicated_speed_rad_s": _slot("rad/s", "derived"),
            "stirring.angle_rad": _slot("rad", "process_truth", "debug")},
        "parameters": {
            "heater_power_W": {"value": 600.0, "evidence": "catalog", "source": "CHEM-008 specification"},
            "maximum_temperature_K": {"value": 583.15, "evidence": "catalog", "source": "CHEM-008: 310 deg C"},
            "minimum_speed_rad_s": {"value": 50 * RPM_TO_RAD_S, "evidence": "catalog", "source": "CHEM-008: 50 rpm"},
            "maximum_speed_rad_s": {"value": 1500 * RPM_TO_RAD_S, "evidence": "catalog", "source": "CHEM-008: 1500 rpm"},
            "speed_increment_rad_s": {"value": 10 * RPM_TO_RAD_S, "evidence": "catalog", "source": "CHEM-008: 10 rpm; grid starts at 50 rpm"},
            "thermal_capacity_J_K": {"value": 1800.0, "evidence": "assumption", "source": "Empty plate lumped model; uncalibrated"},
            "heat_loss_W_K": {"value": 1.2, "evidence": "assumption", "source": "Empty plate convection/loss approximation"},
            "stirring_time_constant_s": {"value": 1.5, "evidence": "assumption", "source": "First-order rotor response; not vendor response time"},
        },
    },
    "instrument.binder_fd56": {
        "catalog_id": "CHEM-011", "model": "BINDER FD 56", "asset_version": "1.0.0",
        "capabilities": ["power.set", "thermal.set_temperature", "scene.set_door"],
        "state_slots": {**COMMON_SLOTS, **THERMAL_SLOTS,
            "door.angle_rad": _slot("rad", "scene", access="read_write", capability="scene.set_door", parameter="angle_rad")},
        "parameters": {
            "heater_power_W": {"value": 1100.0, "evidence": "catalog", "source": "CHEM-011 specification"},
            "minimum_target_above_ambient_K": {"value": 10.0, "evidence": "catalog", "source": "CHEM-011: ambient + 10 deg C"},
            "maximum_temperature_K": {"value": 573.15, "evidence": "catalog", "source": "CHEM-011: 300 deg C"},
            "maximum_door_angle_rad": {"value": math.radians(100), "evidence": "assumption", "source": "Scene articulation limit 100 deg; not measured manufacturer travel"},
            "thermal_capacity_J_K": {"value": 15000.0, "evidence": "assumption", "source": "Empty chamber effective heat capacity; no sample drying chemistry"},
            "heat_loss_W_K": {"value": 2.5, "evidence": "assumption", "source": "Closed chamber lumped loss"},
            "door_open_loss_W_K": {"value": 20.0, "evidence": "assumption", "source": "Additional loss at fully open door"},
            "door_open_heater_fraction": {"value": 0.25, "evidence": "assumption", "source": "Simulation reduction at full opening; NOT a manufacturer interlock"},
        },
    },
    "instrument.ne1000_bd10ml": {
        "catalog_id": "CHEM-184", "model": "New Era NE-1000, B-D 10 mL syringe", "asset_version": "1.0.0",
        "capabilities": ["power.set", "pumping.set_flow_rate", "pumping.set_direction", "pumping.start", "pumping.stop"],
        "state_slots": {**COMMON_SLOTS,
            "pumping.flow_rate_m3_s": _slot("m^3/s", "setpoint", access="read_write", capability="pumping.set_flow_rate", parameter="flow_rate_m3_s"),
            "pumping.running": _slot("1", "derived"),
            "pumping.direction": _slot("1", "setpoint", access="read_write", capability="pumping.set_direction", parameter="direction"),
            "pumping.transferred_volume_m3": _slot("m^3", "process_truth", "debug"),
            "pumping.plunger_displacement_m": _slot("m", "derived")},
        "parameters": {
            "maximum_flow_rate_m3_s": {"value": 500.4e-6 / 3600, "evidence": "public_document", "source": "NE-1000 manual 1200-01, 2016-04-06, p50: B-D 10 mL syringe 500.4 mL/h"},
            "minimum_flow_rate_m3_s": {"value": 6.876e-9 / 3600, "evidence": "public_document", "source": "NE-1000 manual 1200-01, 2016-04-06, p50: B-D 10 mL syringe 6.876 uL/h"},
            "syringe_diameter_m": {"value": .01443, "evidence": "public_document", "source": "NE-1000 manual 1200-01, 2016-04-06, p50: B-D 10 mL inner diameter 14.43 mm; supplemental configuration choice"},
            "syringe_stroke_m": {"value": 10e-6 / (math.pi * .01443**2 / 4), "evidence": "assumption", "source": "Ideal 10 mL cylinder travel V/A; not a measured manufacturer stroke"},
        },
    },
    "instrument.esco_efa5": {
        "catalog_id": "CHEM-199", "model": "Esco EFA-5UDUVW-9", "asset_version": "1.0.0",
        "capabilities": ["power.set", "scene.set_sash"],
        "state_slots": {**COMMON_SLOTS,
            "sash.opening_m": _slot("m", "scene", access="read_write", capability="scene.set_sash", parameter="opening_m"),
            "ventilation.external_exhaust_m3_s": _slot("m^3/s", "derived"),
            "ventilation.face_velocity_m_s": _slot("m/s", "derived")},
        "parameters": {
            "opening_width_m": {"value": 1.301, "evidence": "public_document", "source": "Esco EFA-5UDUVW-9 technical specifications: clear opening width 1301 mm"},
            "maximum_sash_opening_m": {"value": .670, "evidence": "public_document", "source": "Esco EFA-5UDUVW-9 technical specifications: maximum sash opening 670 mm"},
        },
    },
}

CAPABILITY_ARGUMENTS = {
    "power.set": {"enabled": {"type": "boolean"}},
    "thermal.set_temperature": {"temperature_K": {"type": "number", "x-unit": "K"}},
    "stirring.set_speed": {"speed_rad_s": {"type": "number", "x-unit": "rad/s"}},
    "scene.set_door": {"angle_rad": {"type": "number", "x-unit": "rad"}},
    "pumping.set_flow_rate": {"flow_rate_m3_s": {"type": "number", "x-unit": "m^3/s"}},
    "pumping.set_direction": {"direction": {"type": "string", "enum": ["infuse", "withdraw"]}},
    "pumping.start": {}, "pumping.stop": {},
    "scene.set_sash": {"opening_m": {"type": "number", "x-unit": "m"}},
}
CAPABILITY_RESULT_SLOT = {
    "power.set": ("enabled", "power.enabled"),
    "thermal.set_temperature": ("setpoint_K", "thermal.target_k"),
    "stirring.set_speed": ("setpoint_rad_s", "stirring.target_rad_s"),
    "scene.set_door": ("angle_rad", "door.angle_rad"),
    "pumping.set_flow_rate": ("flow_rate_m3_s", "pumping.flow_rate_m3_s"),
    "pumping.set_direction": ("direction", "pumping.direction"),
    "pumping.start": ("running", "pumping.running"),
    "pumping.stop": ("running", "pumping.running"),
    "scene.set_sash": ("opening_m", "sash.opening_m"),
}

DECLARED_CAPABILITIES = {"instrument.ne1000_bd10ml": {
    "pumping.program": {"availability": "declared", "kind": "configuration",
        "description_zh": "NE-1000 41 阶段程序功能尚未实现；本接口不接受或执行程序。下述结构仅为本项目未来仿真提案，不是厂家串口格式。",
        "input_schema": {"type": "object", "properties": {"phases": {"type": "array", "minItems": 1, "maxItems": 41,
            "items": {"type": "object", "properties": {
                "direction": {"type": "string", "enum": ["infuse", "withdraw"]},
                "flow_rate_m3_s": {"type": "number", "exclusiveMinimum": 0, "x-unit": "m^3/s"},
                "duration_s": {"type": "number", "exclusiveMinimum": 0, "x-unit": "s"}},
                "required": ["direction", "flow_rate_m3_s", "duration_s"], "additionalProperties": False}}},
            "required": ["phases"], "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "example_arguments": [{"phases": [{"direction": "infuse", "flow_rate_m3_s": 1e-9, "duration_s": 10}]}],
        "state_effects": [], "observes": [], "binding": None}
}}

HOST_CAPABILITIES = {
    "sim.step": {"kind": "simulation", "visibility": "trusted_host_only",
        "scope": "All registered instances in this LabHost share one world clock",
        "input_schema": {"type": "object", "properties": {"dt_s": {"type": "number", "minimum": 0,
            "maximum": 3600, "x-unit": "s"}}, "required": ["dt_s"], "additionalProperties": False},
        "binding": "basic40_runtime.LabHost.step(dt_s)",
        "completion_criterion": "Common host time equals previous time + dt_s; all process instances advanced under one world lock",
        "partial_failure": "SceneSynchronizationError means process and time advanced; failed scene updates remain in trusted diagnostics"},
    "host.configure_syringe": {"kind": "configuration", "visibility": "trusted_host_only", "binding": "LabHost.configure_syringe"},
    "host.connect_pump": {"kind": "configuration", "visibility": "trusted_host_only", "binding": "LabHost.connect_pump",
        "constraints": "Each syringe vessel belongs exclusively to one pump and cannot be another pump's external vessel; external collection vessels may be shared. All checks precede connection, position and revision changes."},
    "host.set_external_exhaust": {"kind": "configuration", "visibility": "trusted_host_only", "binding": "LabHost.set_external_exhaust"},
    "host.sync_scene": {"kind": "configuration", "visibility": "trusted_host_only", "binding": "LabHost.sync_scene"},
}


def export_contract_metadata():
    """Type metadata only: no instance values, identities, samples or task state.

    capability.output_schema describes the protocol envelope's result member.
    All other envelope fields follow Chemistry Lab protocol 1.0.0.
    """
    assets = copy.deepcopy(ASSET_SPECS)
    summaries = {"power.set": "设置本机供电；风柜仅本机照明，不改变外部排风量",
        "thermal.set_temperature": "写入温度目标；实际温度仅随宿主时间演化",
        "stirring.set_speed": "设置搅拌角速度；50–1500 rpm 按 10 rpm 递增，0 单独表示停止目标",
        "scene.set_door": "经场景适配器设置门转角；无机器人抓取或碰撞求解",
        "pumping.set_flow_rate": "设置所选 B-D 10 mL 注射器的正值体积流率",
        "pumping.set_direction": "停泵时设置注入或回抽方向",
        "pumping.start": "启动受行程与容器容量限制的输液；本调用不转移物质",
        "pumping.stop": "停止后续输液；保留已经转移的物质",
        "scene.set_sash": "经场景适配器设置视窗垂直开口并派生估计面风速"}
    examples = {"power.set": {"enabled": True}, "thermal.set_temperature": {"temperature_K": 333.15},
        "stirring.set_speed": {"speed_rad_s": 300 * RPM_TO_RAD_S}, "scene.set_door": {"angle_rad": .5},
        "pumping.set_flow_rate": {"flow_rate_m3_s": 1e-9}, "pumping.set_direction": {"direction": "infuse"},
        "pumping.start": {}, "pumping.stop": {}, "scene.set_sash": {"opening_m": .457}}
    for type_id, spec in assets.items():
        for sid, slot in spec["state_slots"].items():
            if sid in ("power.enabled", "pumping.running"):
                slot["value_schema"] = {"type": "boolean"}
            elif sid == "pumping.direction":
                slot["value_schema"] = {"type": "string", "enum": ["infuse", "withdraw"]}
            elif sid == "ventilation.face_velocity_m_s":
                slot["value_schema"] = {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]}
            else:
                slot["value_schema"] = {"type": "number"}
            slot["read_binding"] = f"LabSession.read(instance_id=..., state_ids=['{sid}'])"
            slot["sampling"] = {"mode": "instant", "time_domain": "simulation", "window": None}
            slot["reference_conditions"] = "Actual room conditions supplied by host; no standard-state conversion" if sid == "ventilation.external_exhaust_m3_s" else None
            if slot["origin"] == "derived":
                slot["observation_note_zh"] = "明确的理想化模型观测，quality=estimated；不是厂家真实传感器读数或精度声明"
        caps = {}
        for capability in spec["capabilities"]:
            args = copy.deepcopy(CAPABILITY_ARGUMENTS[capability])
            if capability == "thermal.set_temperature":
                args["temperature_K"].update(minimum=283.15 if type_id.endswith("binder_fd56") else 273.15,
                    maximum=spec["parameters"]["maximum_temperature_K"]["value"],
                    description="Actual lower bound is host ambient + 10 K for FD 56, or host ambient for RCT")
            elif capability == "stirring.set_speed":
                args["speed_rad_s"].update(minimum=0, maximum=1500 * RPM_TO_RAD_S,
                    description="Allowed: 0, or (50 + 10*n) rpm converted to rad/s, n=0..145")
            elif capability == "scene.set_door":
                args["angle_rad"].update(minimum=0, maximum=spec["parameters"]["maximum_door_angle_rad"]["value"])
            elif capability == "scene.set_sash":
                args["opening_m"].update(minimum=0, maximum=spec["parameters"]["maximum_sash_opening_m"]["value"])
            elif capability == "pumping.set_flow_rate":
                args["flow_rate_m3_s"].update(minimum=spec["parameters"]["minimum_flow_rate_m3_s"]["value"],
                    maximum=spec["parameters"]["maximum_flow_rate_m3_s"]["value"],
                    **{"x-reference-conditions": "Liquid geometric displacement at modeled room conditions; no gas reference state"})
            output_key, sid = CAPABILITY_RESULT_SLOT[capability]
            out_value = copy.deepcopy(spec["state_slots"][sid]["value_schema"])
            if out_value.get("type") == "number":
                out_value["x-unit"] = spec["state_slots"][sid]["unit"]
            effects = [{"state_id": sid, "timing": "immediate", "description_zh": summaries[capability]}]
            if capability in ("thermal.set_temperature", "scene.set_door") or (capability == "power.set" and "thermal.target_k" in spec["state_slots"]):
                effects += [{"state_id": key, "timing": "on_step", "description_zh": "空载集中参数热模型及理想化显示"}
                    for key in ("thermal.temperature_k", "thermal.indicated_temperature_k")]
            if capability == "stirring.set_speed" or (capability == "power.set" and "stirring.target_rad_s" in spec["state_slots"]):
                effects += [{"state_id": key, "timing": "on_step", "description_zh": "有限响应时间的速度及其时间积分角度"}
                    for key in ("stirring.speed_rad_s", "stirring.indicated_speed_rad_s", "stirring.angle_rad")]
            if capability in ("pumping.start", "pumping.set_flow_rate", "pumping.set_direction"):
                effects += [{"state_id": key, "timing": "on_step", "description_zh": "输液事务后更新累计体积、真实几何行程与边界停止状态"}
                    for key in ("pumping.transferred_volume_m3", "pumping.plunger_displacement_m", "pumping.running")]
            if capability == "power.set" and "pumping.running" in spec["state_slots"]:
                effects.append({"state_id": "pumping.running", "timing": "immediate", "description_zh": "断电时停止输液"})
            if capability == "scene.set_sash":
                effects.append({"state_id": "ventilation.face_velocity_m_s", "timing": "immediate", "description_zh": "外部Q/(1.301×开口)估计；开口为0时不可用，不能用于安全判断"})
            caps[capability] = {"availability": "implemented", "visibility": "agent",
                "kind": "simulation" if capability.startswith("scene.") else "device_command",
                "description_zh": summaries[capability],
                "input_schema": {"type": "object", "properties": args, "required": list(args), "additionalProperties": False},
                "output_schema": {"type": "object", "properties": {output_key: out_value}, "required": [output_key], "additionalProperties": False},
                "state_effects": effects, "observes": [sid], "example_arguments": [examples[capability]],
                "binding": "basic40_runtime.LabSession.invoke(instance_id=..., capability_id='" + capability + "', arguments=..., request_id=...)",
                "scene_backend_required": capability.startswith("scene."),
                "preconditions_zh": (["本机已上电"] if capability in ("thermal.set_temperature", "stirring.set_speed", "pumping.set_flow_rate", "pumping.set_direction", "pumping.start") else [])
                    + (["目标≥宿主环境温度" + ("+10 K" if type_id.endswith("binder_fd56") else "")] if capability == "thermal.set_temperature" else [])
                    + (["非零值必须为从50 rpm起每10 rpm递增的离散值"] if capability == "stirring.set_speed" else [])
                    + (["方向只能在停泵时改变"] if capability == "pumping.set_direction" else [])
                    + (["宿主已配置固定注射器、连接液体容器并设置正流量；供体非空、受体未满、剩余行程为正"] if capability == "pumping.start" else [])
                    + (["宿主注册了实际场景回调"] if capability.startswith("scene.") else []),
                "completion_criterion_zh": "返回值中的设定或开关状态已写入；过程演化仅发生于宿主step，succeeded不代表达到温度或完成输液"}
        spec["capabilities"] = caps
        spec["declared_capabilities"] = copy.deepcopy(DECLARED_CAPABILITIES.get(type_id, {}))
    return {"asset_types": assets, "host_capabilities": copy.deepcopy(HOST_CAPABILITIES),
        "result_schema_scope": "Each capability output_schema describes response.result, not the entire standard protocol envelope",
        "trusted_scene_callback": "__call__(instance_id: str, updates: dict[state_id, value]); all state values use SI"}


def _number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


@dataclass
class _Instance:
    instance_id: str
    asset_type_id: str
    state: dict[str, Any]
    revision: int = 0
    syringe: dict[str, Any] | None = None
    connections: tuple[str, str] | None = None


@dataclass
class _Vessel:
    volume_m3: float
    capacity_m3: float
    species_mass_kg: dict[str, float] = field(default_factory=dict)


class SceneSynchronizationError(RuntimeError):
    """World time/process advanced, but at least one backend sync failed."""


class LabHost:
    """Trusted host API: registration, identity, materials, scene and world time.

    Do not expose this object to an untrusted agent. Supply a LabSession or a
    remote wrapper with authenticated host-side principal selection instead.
    Deduplication is in-memory and intentionally makes no restart guarantee.
    """
    def __init__(self, *, ambient_temperature_K=293.15,
                 scene_adapter: Callable[[str, dict[str, Any]], None] | None = None):
        if not _number(ambient_temperature_K) or not 273.15 <= ambient_temperature_K <= 313.15:
            raise ValueError("Ambient temperature must be finite and within the assumed 273.15–313.15 K model range")
        self.ambient_temperature_K = float(ambient_temperature_K)
        self.time_s = 0.0
        self.scene_adapter = scene_adapter
        self._instances: dict[str, _Instance] = {}
        self._vessels: dict[str, _Vessel] = {}
        self._principals: dict[str, tuple[str, frozenset[str] | None]] = {}
        self._requests: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
        self._logs: dict[str, list[dict[str, Any]]] = {}
        self._transactions: list[dict[str, Any]] = []
        self._scene_faults: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def create_instance(self, instance_id: str, asset_type_id: str):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances:
                raise ValueError("Instance ID must be unique and nonempty")
            if asset_type_id not in ASSET_SPECS:
                raise ValueError("Unknown asset type")
            slots = ASSET_SPECS[asset_type_id]["state_slots"]
            state = {sid: 0.0 for sid in slots}
            state["power.enabled"] = False
            if "thermal.target_k" in slots:
                state["thermal.target_k"] = self.ambient_temperature_K + (10 if asset_type_id.endswith("binder_fd56") else 0)
                state["thermal.temperature_k"] = self.ambient_temperature_K
                state["thermal.indicated_temperature_k"] = round(self.ambient_temperature_K, 1)
            if "pumping.running" in slots:
                state["pumping.running"] = False
                state["pumping.direction"] = "infuse"
            if "ventilation.face_velocity_m_s" in slots:
                state["ventilation.face_velocity_m_s"] = None
            if self.scene_adapter:
                self.scene_adapter(instance_id, copy.deepcopy(state))
            self._instances[instance_id] = _Instance(instance_id, asset_type_id, state)

    def sync_scene(self, instance_ids=None):
        """Explicit host resync after loading/configuration; no time advance.

        Call this after configuring a syringe and connecting its loaded content.
        Initial creation already pushes all initial slots when a backend exists.
        """
        if self.scene_adapter is None:
            raise RuntimeError("No scene backend is registered")
        with self._lock:
            ids = list(self._instances) if instance_ids is None else list(instance_ids)
            if any(key not in self._instances for key in ids):
                raise ValueError("Unknown scene instance")
            for key in ids:
                try:
                    self.scene_adapter(key, copy.deepcopy(self._instances[key].state))
                except Exception:
                    self._scene_faults.append({"instance_id": key, "time_s": self.time_s, "code": "SCENE_SYNC_FAILED"})
                    raise SceneSynchronizationError("Scene resynchronization failed; backend may have partial effects") from None

    def describe_host(self):
        """Trusted host metadata; sim.step is never an instrument capability."""
        return copy.deepcopy(HOST_CAPABILITIES)

    def register_principal(self, principal_id: str, *, visibility: str, instance_ids=None):
        if visibility not in ("agent", "debug", "evaluator"):
            raise ValueError("Invalid host-assigned visibility")
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("Invalid principal ID")
        with self._lock:
            if principal_id in self._principals:
                raise ValueError("Principal already registered; identities are immutable for session lifetime")
            allowed = None if instance_ids is None else frozenset(instance_ids)
            if allowed is not None and not allowed <= self._instances.keys():
                raise ValueError("Principal scope includes an unknown instance")
            self._principals[principal_id] = (visibility, allowed)
            self._logs[principal_id] = []

    def session(self, principal_id: str) -> "LabSession":
        if principal_id not in self._principals:
            raise ValueError("Principal must first be authenticated and registered by the host")
        return LabSession(self, principal_id)

    def create_vessel(self, vessel_id: str, *, volume_m3: float, capacity_m3: float,
                      species_mass_kg: dict[str, float]):
        with self._lock:
            if not isinstance(vessel_id, str) or not vessel_id or vessel_id in self._vessels:
                raise ValueError("Vessel ID must be unique and nonempty")
            if not (_number(volume_m3) and _number(capacity_m3) and 0 <= volume_m3 <= capacity_m3 and capacity_m3 > 0):
                raise ValueError("Invalid vessel volume or capacity")
            if not isinstance(species_mass_kg, dict) or any(not isinstance(k, str) or not k or not _number(v) or v < 0 for k, v in species_mass_kg.items()):
                raise ValueError("Invalid material masses")
            if volume_m3 == 0 and any(species_mass_kg.values()):
                raise ValueError("An empty liquid vessel cannot contain nonzero material mass")
            self._vessels[vessel_id] = _Vessel(float(volume_m3), float(capacity_m3), copy.deepcopy(species_mass_kg))

    def configure_syringe(self, instance_id: str, *, diameter_m: float, stroke_m: float, evidence: str):
        with self._lock:
            inst = self._require_pump(instance_id)
            if inst.state["pumping.running"]:
                raise ValueError("Stop pump before changing calibrated syringe")
            if not (_number(diameter_m) and _number(stroke_m)
                    and math.isclose(diameter_m, self._value(inst, "syringe_diameter_m"), rel_tol=1e-9)
                    and math.isclose(stroke_m, self._value(inst, "syringe_stroke_m"), rel_tol=1e-9)):
                raise ValueError("This asset type requires the declared B-D 10 mL nominal geometry")
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValueError("Per-instance calibration evidence is required")
            inst.syringe = {"diameter_m": float(diameter_m), "stroke_m": float(stroke_m),
                            "area_m2": math.pi * diameter_m**2 / 4, "evidence": evidence}
            inst.state["pumping.transferred_volume_m3"] = 0.0
            inst.state["pumping.plunger_displacement_m"] = 0.0
            inst.connections = None
            inst.revision += 1

    def connect_pump(self, instance_id: str, syringe_vessel_id: str, external_vessel_id: str):
        """Bind syringe content and external liquid vessel; direction selects donor.

        Infuse: syringe -> external. Withdraw: external -> syringe. Initial
        plunger position is derived from the explicitly loaded syringe volume.
        Host setup represents physical loading; it never adds or deletes matter.
        Each syringe vessel is exclusive to its pump and cannot be used as any
        other pump's external vessel. External collection vessels may be shared.
        Rejected configuration leaves connections, plunger and revision unchanged.
        """
        with self._lock:
            inst = self._require_pump(instance_id)
            if inst.state["pumping.running"]:
                raise ValueError("Stop pump before changing material connections")
            if syringe_vessel_id == external_vessel_id or syringe_vessel_id not in self._vessels or external_vessel_id not in self._vessels:
                raise ValueError("Two distinct registered material vessels are required")
            if not inst.syringe:
                raise ValueError("Configure the declared syringe geometry before connecting")
            for other in self._instances.values():
                if other is inst or not other.connections:
                    continue
                other_syringe, other_external = other.connections
                if syringe_vessel_id in (other_syringe, other_external) or external_vessel_id == other_syringe:
                    raise ValueError("A syringe vessel must be exclusive to one pump and cannot serve another pump as an external vessel")
            syringe = self._vessels[syringe_vessel_id]
            capacity = inst.syringe["area_m2"] * inst.syringe["stroke_m"]
            if syringe.volume_m3 > capacity + 1e-18:
                raise ValueError("Loaded content exceeds the declared 10 mL syringe")
            inst.connections = (syringe_vessel_id, external_vessel_id)
            inst.state["pumping.plunger_displacement_m"] = max(0.0, inst.syringe["stroke_m"] - syringe.volume_m3 / inst.syringe["area_m2"])
            inst.revision += 1

    def set_external_exhaust(self, instance_id: str, flow_rate_m3_s: float):
        """Host facility boundary condition, at current room conditions; not a sensor."""
        with self._lock:
            inst = self._instances[instance_id]
            if inst.asset_type_id != "instrument.esco_efa5":
                raise ValueError("External exhaust applies only to the fume hood")
            if not _number(flow_rate_m3_s) or not 0 <= flow_rate_m3_s <= 5:
                raise ValueError("Flow must fit the assumed 0–5 m^3/s facility envelope")
            inst.state["ventilation.external_exhaust_m3_s"] = float(flow_rate_m3_s)
            self._update_face_velocity(inst)
            inst.revision += 1

    def _require_pump(self, instance_id):
        inst = self._instances[instance_id]
        if inst.asset_type_id != "instrument.ne1000_bd10ml":
            raise ValueError("Operation requires a syringe pump")
        return inst

    def _value(self, inst, name):
        return ASSET_SPECS[inst.asset_type_id]["parameters"][name]["value"]

    def _update_face_velocity(self, inst):
        opening = inst.state["sash.opening_m"]
        area = self._value(inst, "opening_width_m") * opening
        inst.state["ventilation.face_velocity_m_s"] = inst.state["ventilation.external_exhaust_m3_s"] / area if area > 0 else None

    def _thermal_step(self, inst, dt):
        s = inst.state
        ambient = self.ambient_temperature_K
        old = s["thermal.temperature_k"]
        capacity = self._value(inst, "thermal_capacity_J_K")
        loss = self._value(inst, "heat_loss_W_K")
        power = self._value(inst, "heater_power_W") if s["power.enabled"] else 0
        if "door.angle_rad" in s:
            fraction = s["door.angle_rad"] / self._value(inst, "maximum_door_angle_rad")
            loss += fraction * self._value(inst, "door_open_loss_W_K")
            power *= 1 - fraction * (1 - self._value(inst, "door_open_heater_fraction"))
        target = s["thermal.target_k"]
        decay = math.exp(-loss * dt / capacity)
        if not power:
            temperature = ambient + (old - ambient) * decay
        elif old < target:
            equilibrium = ambient + power / loss
            temperature = min(target, equilibrium + (old - equilibrium) * decay)
        else:
            cooled = ambient + (old - ambient) * decay
            if cooled >= target:
                temperature = cooled
            else:
                crossing_s = -capacity / loss * math.log((target - ambient) / (old - ambient)) if target > ambient and old > target else 0.0
                equilibrium = ambient + power / loss
                temperature = min(target, equilibrium + (target - equilibrium) * math.exp(-loss * max(0.0, dt - crossing_s) / capacity))
        s["thermal.temperature_k"] = temperature
        # A declared ideal display proxy, rounded to 0.1 K; no sensor accuracy claim.
        s["thermal.indicated_temperature_k"] = round(temperature, 1)

    def _pump_step(self, inst, dt):
        s = inst.state
        if not s["pumping.running"]:
            return
        if not s["power.enabled"] or not inst.syringe or not inst.connections:
            s["pumping.running"] = False
            return
        infuse = s["pumping.direction"] == "infuse"
        donor_id, recipient_id = inst.connections if infuse else tuple(reversed(inst.connections))
        donor, recipient = self._vessels[donor_id], self._vessels[recipient_id]
        area = inst.syringe["area_m2"]
        stroke_remaining = max(0.0, inst.syringe["stroke_m"] - s["pumping.plunger_displacement_m"] if infuse else s["pumping.plunger_displacement_m"])
        available = min(donor.volume_m3, max(0.0, recipient.capacity_m3 - recipient.volume_m3), stroke_remaining * area)
        requested = s["pumping.flow_rate_m3_s"] * dt
        volume = min(requested, available)
        if volume > 0:
            fraction = min(1.0, volume / donor.volume_m3)
            masses = {key: value * fraction for key, value in donor.species_mass_kg.items()}
            # All checks and the entire material transaction occur under the world lock.
            for key, mass in masses.items():
                donor.species_mass_kg[key] -= mass
                recipient.species_mass_kg[key] = recipient.species_mass_kg.get(key, 0.0) + mass
            donor.volume_m3 -= volume
            recipient.volume_m3 += volume
            s["pumping.transferred_volume_m3"] += volume
            s["pumping.plunger_displacement_m"] += (1 if infuse else -1) * volume / area
            self._transactions.append({"transaction_id": f"transfer_{len(self._transactions) + 1:06d}",
                "instance_id": inst.instance_id, "donor_id": donor_id, "recipient_id": recipient_id,
                "volume_m3": volume, "species_mass_kg": masses,
                "start_time_s": self.time_s, "end_time_s": self.time_s + dt,
                "direction": s["pumping.direction"], "quality": "synthetic_process_truth"})
        if available <= requested + 1e-18:
            s["pumping.running"] = False

    def step(self, dt_s: float):
        """Advance one trusted world step; read/invoke never call this method.

        The batch model permits up to 3600 seconds per step. Transfers use a
        constant commanded rate, exact bounded volume and homogeneous material.
        A failed backend synchronization leaves already-advanced process state
        intact and raises SceneSynchronizationError after the common clock commits.
        """
        if not _number(dt_s) or not 0 <= dt_s <= 3600:
            raise ValueError("dt_s must be finite and in [0, 3600]")
        if not dt_s:
            return
        with self._lock:
            scene_failed = False
            for inst in self._instances.values():
                before = copy.deepcopy(inst.state)
                s = inst.state
                if "thermal.temperature_k" in s:
                    self._thermal_step(inst, dt_s)
                if "stirring.speed_rad_s" in s:
                    old = s["stirring.speed_rad_s"]
                    target = s["stirring.target_rad_s"] if s["power.enabled"] else 0.0
                    tau = self._value(inst, "stirring_time_constant_s")
                    decay = math.exp(-dt_s / tau)
                    s["stirring.speed_rad_s"] = target + (old - target) * decay
                    s["stirring.angle_rad"] = (s["stirring.angle_rad"] + target * dt_s + (old - target) * tau * (1 - decay)) % (2 * math.pi)
                    s["stirring.indicated_speed_rad_s"] = round(s["stirring.speed_rad_s"] / RPM_TO_RAD_S) * RPM_TO_RAD_S
                if "pumping.running" in s:
                    self._pump_step(inst, dt_s)
                if before != s:
                    inst.revision += 1
                if self.scene_adapter:
                    updates = {key: copy.deepcopy(value) for key, value in s.items() if before[key] != value}
                    if updates:
                        try:
                            self.scene_adapter(inst.instance_id, updates)
                        except Exception:
                            scene_failed = True
                            self._scene_faults.append({"instance_id": inst.instance_id,
                                "time_s": self.time_s + dt_s, "code": "SCENE_SYNC_FAILED"})
            self.time_s += dt_s
            if scene_failed:
                raise SceneSynchronizationError("Process and common clock advanced; one or more scene updates failed. Inspect trusted host diagnostics.")

    def debug_snapshot(self):
        """Host-only diagnostic state. Never expose as an agent tool or file."""
        with self._lock:
            return copy.deepcopy({"time_s": self.time_s,
                "instances": {key: {"asset_type_id": inst.asset_type_id, "revision": inst.revision,
                    "state": inst.state, "syringe": inst.syringe, "connections": inst.connections}
                    for key, inst in self._instances.items()},
                "vessels": {key: {"volume_m3": vessel.volume_m3, "capacity_m3": vessel.capacity_m3,
                    "species_mass_kg": vessel.species_mass_kg} for key, vessel in self._vessels.items()},
                "transactions": self._transactions, "scene_faults": self._scene_faults})


class LabSession:
    """Agent-facing adapter; access is resolved from an immutable host registry."""
    def __init__(self, host: LabHost, principal_id: str):
        self.__host = host
        self.__principal_id = principal_id

    def _allowed(self, inst):
        _, scope = self.__host._principals[self.__principal_id]
        return scope is None or inst.instance_id in scope

    def _visible(self, spec):
        role, _ = self.__host._principals[self.__principal_id]
        return spec["visibility"] == "agent" or role in ("debug", "evaluator")

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        host = self.__host
        safe_instance_id = instance_id if isinstance(instance_id, str) else None
        safe_request_id = request_id if isinstance(request_id, str) else None
        inst = host._instances.get(safe_instance_id)
        return {"protocol_version": PROTOCOL_VERSION, "instance_id": safe_instance_id,
            "request_id": safe_request_id, "operation_id": None, "status": status,
            "state_revision": inst.revision if inst and self._allowed(inst) else None,
            "clock": {"domain": "simulation", "time_s": host.time_s},
            "result": {} if result is None else result,
            "observations": {} if observations is None else observations, "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        # Deliberately static messages; never interpolate internal state/exception text.
        messages = {"UNKNOWN_INSTANCE": "Instance is unavailable in this session.",
            "UNKNOWN_CAPABILITY": "Capability is unavailable for this asset.",
            "INVALID_ARGUMENT": "Arguments do not meet the declared schema or limits.",
            "PRECONDITION_FAILED": "Required operating configuration or state is unavailable.",
            "ACCESS_DENIED": "Requested operation is outside the session access policy.",
            "REQUEST_CONFLICT": "Request ID was already used for a different operation or arguments.",
            "UNSUPPORTED": "Capability execution is not available in this adapter configuration.",
            "DEVICE_ERROR": "Scene backend did not complete the command."}
        return self._envelope(instance_id, request_id, status=status,
            error={"code": code, "message": messages[code], "retryable": False,
                   "side_effects": "unknown" if code == "DEVICE_ERROR" else "none", "progress": None})

    def _instance(self, instance_id):
        if not isinstance(instance_id, str):
            return None
        inst = self.__host._instances.get(instance_id)
        return inst if inst and self._allowed(inst) else None

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(None, "INVALID_ARGUMENT")
            if instance_id is not None:
                inst = self._instance(instance_id)
                if not inst:
                    return self._error(instance_id, "UNKNOWN_INSTANCE")
                asset_type_id = inst.asset_type_id
            if not isinstance(asset_type_id, str) or asset_type_id not in ASSET_SPECS:
                return self._error(instance_id, "INVALID_ARGUMENT")
            _, scope = self.__host._principals[self.__principal_id]
            if scope is not None and not any(self.__host._instances[key].asset_type_id == asset_type_id for key in scope):
                return self._error(instance_id, "ACCESS_DENIED")
            spec = export_contract_metadata()["asset_types"][asset_type_id]
            capabilities = {}
            for capability, info in spec["capabilities"].items():
                capabilities[capability] = {"input_schema": copy.deepcopy(info["input_schema"]),
                    "output_schema": copy.deepcopy(info["output_schema"]),
                    "description_zh": info["description_zh"], "preconditions_zh": info["preconditions_zh"],
                    "availability": "unsupported" if capability.startswith("scene.") and self.__host.scene_adapter is None else "implemented"}
            capabilities.update(copy.deepcopy(spec["declared_capabilities"]))
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": asset_type_id,
                "asset_version": spec["asset_version"], "instance_id": instance_id,
                "catalog_id": spec["catalog_id"], "model": spec["model"],
                "adapter": "basic40_runtime.LabSession", "capabilities": capabilities,
                "state_slots": copy.deepcopy({sid: slot for sid, slot in spec["state_slots"].items() if self._visible(slot)}),
                "limitations": ["Synthetic process model; no real hardware or robot execution.",
                    "Readout proxies are estimated, not measured sensor readings.",
                    "In-memory request deduplication does not survive host restart.",
                    "Python API filtering is not process/filesystem isolation.",
                    "Esco model uses the base switch-controlled lighting configuration; no digital display or Sentinel airflow sensor is assumed.",
                    "Fume face velocity is a host Q/area estimate; manufacturer rated working points are not this model's measurements or a safety assessment."]}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if not inst:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = ASSET_SPECS[inst.asset_type_id]["state_slots"]
            if state_ids is None:
                state_ids = [key for key, slot in slots.items() if self._visible(slot)]
            if not isinstance(state_ids, list) or any(not isinstance(key, str) for key in state_ids) or len(set(state_ids)) != len(state_ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            # Unknown and restricted IDs have the same error to avoid truth discovery.
            if any(key not in slots or not self._visible(slots[key]) for key in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            observations = {}
            for key in state_ids:
                value = copy.deepcopy(inst.state[key])
                quality = "unavailable" if value is None else "estimated" if slots[key]["origin"] == "derived" else "valid"
                observations[key] = {"value": value, "unit": slots[key]["unit"], "quality": quality,
                    "sample_time_s": self.__host.time_s, "sample_window_s": None}
                if value is None:
                    observations[key]["reason"] = "Face velocity is undefined at zero modeled open area."
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256 or not isinstance(capability_id, str) or not isinstance(arguments, dict):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id if isinstance(request_id, str) else None)
            try:
                digest = hashlib.sha256(json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            except (ValueError, TypeError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = (self.__principal_id, instance_id, request_id)
            if key in host._requests:
                previous_digest, response = host._requests[key]
                if digest != previous_digest:
                    return self._error(instance_id, "REQUEST_CONFLICT", request_id)
                return copy.deepcopy(response)
            response = self._invoke_checked(inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, copy.deepcopy(response))
            host._logs[self.__principal_id].append({"instance_id": instance_id, "capability_id": capability_id,
                "request_id": request_id, "response": copy.deepcopy(response)})
            return response

    def _invoke_checked(self, inst, capability, args, request):
        host, s = self.__host, inst.state
        spec = ASSET_SPECS[inst.asset_type_id]
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request, status)
        if capability in DECLARED_CAPABILITIES.get(inst.asset_type_id, {}):
            return fail("UNSUPPORTED", "unsupported")
        if capability not in spec["capabilities"]:
            return fail("UNKNOWN_CAPABILITY")
        expected = CAPABILITY_ARGUMENTS[capability]
        if set(args) != set(expected):
            return fail("INVALID_ARGUMENT")
        for key, description in expected.items():
            if description["type"] == "boolean" and type(args[key]) is not bool:
                return fail("INVALID_ARGUMENT")
            if description["type"] == "number" and not _number(args[key]):
                return fail("INVALID_ARGUMENT")
            if description["type"] == "string" and (not isinstance(args[key], str) or args[key] not in description["enum"]):
                return fail("INVALID_ARGUMENT")
        value = next(iter(args.values()), None)
        if capability == "thermal.set_temperature":
            minimum = host.ambient_temperature_K + (10 if inst.asset_type_id.endswith("binder_fd56") else 0)
            if not minimum <= value <= host._value(inst, "maximum_temperature_K"):
                return fail("INVALID_ARGUMENT")
        elif capability == "stirring.set_speed":
            rpm = value / RPM_TO_RAD_S
            if value != 0 and (rpm < 50 - 1e-9 or rpm > 1500 + 1e-9 or not math.isclose((rpm - 50) / 10, round((rpm - 50) / 10), abs_tol=1e-8)):
                return fail("INVALID_ARGUMENT")
        elif capability == "scene.set_door":
            if not 0 <= value <= host._value(inst, "maximum_door_angle_rad"):
                return fail("INVALID_ARGUMENT")
        elif capability == "scene.set_sash":
            if not 0 <= value <= host._value(inst, "maximum_sash_opening_m"):
                return fail("INVALID_ARGUMENT")
        elif capability == "pumping.set_flow_rate":
            if not host._value(inst, "minimum_flow_rate_m3_s") <= value <= host._value(inst, "maximum_flow_rate_m3_s"):
                return fail("INVALID_ARGUMENT")
        if capability in ("thermal.set_temperature", "stirring.set_speed", "pumping.set_flow_rate", "pumping.set_direction", "pumping.start") and not s["power.enabled"]:
            return fail("PRECONDITION_FAILED")
        if capability == "pumping.set_direction" and s["pumping.running"]:
            return fail("PRECONDITION_FAILED")
        if capability == "pumping.start":
            if not inst.syringe or not inst.connections or s["pumping.flow_rate_m3_s"] <= 0:
                return fail("PRECONDITION_FAILED")
            infuse = s["pumping.direction"] == "infuse"
            donor, recipient = (host._vessels[key] for key in (inst.connections if infuse else tuple(reversed(inst.connections))))
            remaining = max(0.0, inst.syringe["stroke_m"] - s["pumping.plunger_displacement_m"] if infuse else s["pumping.plunger_displacement_m"]) * inst.syringe["area_m2"]
            if donor.volume_m3 <= 0 or remaining <= 1e-18 or recipient.volume_m3 >= recipient.capacity_m3:
                return fail("PRECONDITION_FAILED")
        output_key, slot = CAPABILITY_RESULT_SLOT[capability]
        if capability == "pumping.start":
            value = True
        elif capability == "pumping.stop":
            value = False
        if capability.startswith("scene.") and host.scene_adapter is None:
            return fail("UNSUPPORTED", "unsupported")
        updates = {slot: value}
        if capability == "power.set" and not value and "pumping.running" in s:
            updates["pumping.running"] = False
        if capability == "scene.set_sash":
            area = host._value(inst, "opening_width_m") * value
            updates["ventilation.face_velocity_m_s"] = s["ventilation.external_exhaust_m3_s"] / area if area > 0 else None
        if host.scene_adapter:
            try:
                host.scene_adapter(inst.instance_id, copy.deepcopy(updates))
            except Exception:
                # A foreign backend can have partial effects. Internal process state
                # is unchanged, but this is failed/unknown, never rejected/none.
                return fail("DEVICE_ERROR", "failed")
        s.update(updates)
        inst.revision += 1
        # Only declared, visible completion values are returned; no full snapshot.
        return self._envelope(inst.instance_id, request, result={output_key: value})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if not inst:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slots = ASSET_SPECS[inst.asset_type_id]["state_slots"]
            if not isinstance(state_id, str) or state_id not in slots or not self._visible(slots[state_id]):
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            slot = slots[state_id]
            if slot["access"] != "read_write" or not slot["write_via"]:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slot["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"],
                arguments={route["parameter"]: value}, request_id=request_id)

    def logs(self):
        """Only this principal's already-filtered response records."""
        with self.__host._lock:
            return copy.deepcopy(self.__host._logs[self.__principal_id])


def validate_runtime(output: Path | None = None):
    """Run deterministic synthetic tests, recording actual environment and time."""
    import importlib.util
    import io
    import unittest
    test_file = Path(__file__).resolve().parents[1] / "tests" / "test_basic40_runtime.py"
    spec = importlib.util.spec_from_file_location("test_basic40_runtime", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"schema_version": "1.0.0", "recorded_at": datetime.now(timezone.utc).isoformat(),
        "method": "unittest through real LabSession describe/read/invoke/write and trusted LabHost.step",
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "data_classification": "Deterministic synthetic fixtures only; no real sample state",
        "tests_run": result.testsRun, "result": "passed" if result.wasSuccessful() else "failed",
        "failures": len(result.failures), "errors": len(result.errors), "log": stream.getvalue(),
        "layers": {"process": "passed" if result.wasSuccessful() else "failed",
            "host": "passed" if result.wasSuccessful() else "failed", "scene": "not_run",
            "robot": "not_run", "hardware": "not_run"},
        "limitations": ["Scene tests use an atomic recording callback, not evaluated Blender geometry.",
            "API visibility filtering is verified; filesystem/process isolation is not implemented.",
            "Physics coefficients are uncalibrated assumptions and sensor proxies are estimated."]}
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", type=Path, metavar="REPORT_JSON")
    parsed = parser.parse_args()
    if parsed.validate:
        report = validate_runtime(parsed.validate)
        print(json.dumps({key: report[key] for key in ("tests_run", "result", "failures", "errors")}, indent=2))
        raise SystemExit(0 if report["result"] == "passed" else 1)
    parser.print_help()
