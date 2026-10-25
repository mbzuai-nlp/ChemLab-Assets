"""CHEM-188 synthetic vacuum-source runtime, Chemistry Lab protocol 1.0.0.

Only VacuumHost advances time. Sources attach to an external isothermal chamber
owned by the host; this module does not create a pump inside consuming assets.
The pressure curve, ideal controller and leak law are engineering assumptions,
not measured VACUUBRAND performance. No hardware, robot or CFD driver exists.
Session filtering is an API boundary, not Python/filesystem/process isolation.
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

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.vacuubrand_pc3001_vario_select"
MAXIMUM_PUMPING_SPEED_M3_S = 2.0 / 3600.0
ULTIMATE_PRESSURE_PA = 200.0
GAS_BALLAST_ULTIMATE_PRESSURE_PA = 400.0
MAXIMUM_BACK_PRESSURE_PA = 110000.0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _slot(unit, origin, schema, visibility="agent", capability=None, parameter=None):
    return {"unit": unit, "origin": origin, "value_schema": schema,
            "visibility": visibility, "access": "read_write" if capability else "read_only",
            "write_via": None if not capability else {"capability_id": capability, "parameter": parameter}}


NULLABLE_PRESSURE = {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]}
STATE_SLOTS = {
    "power.enabled": _slot("1", "setpoint", {"type": "boolean"}, capability="power.set", parameter="enabled"),
    "vacuum.running": _slot("1", "derived", {"type": "boolean"}),
    "vacuum.mode": _slot("1", "setpoint", {"type": "string", "enum": ["pressure_control", "pump_down"]}, capability="vacuum.set_mode", parameter="mode"),
    "vacuum.target_pressure_pa": _slot("Pa", "setpoint", {"type": "number", "minimum": 200}, capability="vacuum.set_pressure", parameter="pressure_pa"),
    "vacuum.gas_ballast_enabled": _slot("1", "setpoint", {"type": "boolean"}, capability="vacuum.set_gas_ballast", parameter="enabled"),
    "vacuum.pressure_pa": _slot("Pa", "process_truth", NULLABLE_PRESSURE, "debug"),
    "vacuum.indicated_pressure_pa": _slot("Pa", "derived", NULLABLE_PRESSURE),
    "vacuum.pumping_speed_m3_s": _slot("m^3/s", "process_truth", {"type": "number", "minimum": 0, "maximum": MAXIMUM_PUMPING_SPEED_M3_S}, "debug"),
    "vacuum.indicated_speed_ratio": _slot("1", "derived", {"type": "number", "minimum": 0, "maximum": 1}),
    "vacuum.removed_gas_pa_m3": _slot("Pa*m^3", "process_truth", {"type": "number", "minimum": 0}, "debug"),
    "gas_ballast.angle_rad": _slot("rad", "derived", {"type": "number", "minimum": 0, "maximum": math.pi / 2}),
    "recovery.condensate_volume_m3": _slot("m^3", "derived", {"type": "null"}),
}

CAPABILITY_ARGUMENTS = {
    "power.set": {"enabled": {"type": "boolean"}},
    "vacuum.start": {}, "vacuum.stop": {},
    "vacuum.set_pressure": {"pressure_pa": {"type": "number", "minimum": 200, "maximum": 110000, "x-unit": "Pa", "description": "Absolute target; host ambient is an additional upper bound. With gas ballast the effective floor is 400 Pa."}},
    "vacuum.set_gas_ballast": {"enabled": {"type": "boolean"}},
    "vacuum.set_mode": {"mode": {"type": "string", "enum": ["pressure_control", "pump_down"]}},
}
CAPABILITY_RESULT_SLOT = {
    "power.set": ("enabled", "power.enabled"),
    "vacuum.start": ("running", "vacuum.running"),
    "vacuum.stop": ("running", "vacuum.running"),
    "vacuum.set_pressure": ("pressure_pa", "vacuum.target_pressure_pa"),
    "vacuum.set_gas_ballast": ("enabled", "vacuum.gas_ballast_enabled"),
    "vacuum.set_mode": ("mode", "vacuum.mode"),
}
LIMITATIONS = [
    "Engineering-assumption pressure curve Q(P)=Qmax*max(0,1-Pultimate/P), not a digitized or validated manufacturer curve.",
    "External chamber is isothermal and perfectly mixed. No gas species, vapor pressure, solvent evaporation, condensation, thermal or material-compatibility calculation.",
    "One source per lumped chamber and one chamber per source; external consumers must supply their combined volume/load to the host. No tubing conductance or multi-port network solver.",
    "Leak input is the assumed zero-pressure throughput L0, actual admission L0*(1-P/Pambient); ideal shutoff prevents pump backflow when stopped.",
    "Ideal continuous pressure control can reduce pumping to match the leak at the target; increasing a target does not actively vent the chamber.",
    "Estimated pressure display is rounded to 1 Pa and speed ratio to 0.01 solely for synthetic observation; these are not manufacturer sensor resolution/accuracy or motor RPM.",
    "Cooling-water control is declared and unsupported; condensate volume is unavailable (null), not zero.",
    "Synchronous scene callbacks are optional. Backend callback tests alone do not prove evaluated Blender articulation, collision, robot operation or hardware operation.",
    "Agent API filters truth from describe/read/invoke/write/logs. Trusted Python memory and filesystem are not isolated; deploy a separate trusted process for untrusted code agents.",
    "Request deduplication is in memory per principal and instance and does not survive restart.",
]
PARAMETERS = {
    "maximum_pumping_speed_m3_s": {"value": MAXIMUM_PUMPING_SPEED_M3_S, "unit": "m^3/s", "evidence": "catalog", "source": "CHEM-188: 2 m3/h maximum pumping speed; actual inlet conditions, not standard flow"},
    "ultimate_pressure_pa": {"value": 200.0, "unit": "Pa", "evidence": "catalog", "source": "CHEM-188: 2 mbar absolute, closed gas ballast"},
    "gas_ballast_ultimate_pressure_pa": {"value": 400.0, "unit": "Pa", "evidence": "catalog", "source": "CHEM-188: 4 mbar absolute, open gas ballast"},
    "maximum_back_pressure_pa": {"value": 110000.0, "unit": "Pa", "evidence": "catalog", "source": "CHEM-188: maximum absolute exhaust back pressure 1.1 bar"},
    "motor_power_w": {"value": 160.0, "unit": "W", "evidence": "catalog", "source": "CHEM-188 motor rating; not consumed energy or gas heating in this solver"},
    "default_ambient_pressure_pa": {"value": 101325.0, "unit": "Pa", "evidence": "engineering_assumption", "source": "Synthetic host default, caller configurable"},
    "default_chamber_volume_m3": {"value": .001, "unit": "m^3", "evidence": "engineering_assumption", "source": "External 1 L test fixture; not an internal manufacturer volume"},
    "gas_ballast_open_angle_rad": {"value": math.pi / 2, "unit": "rad", "evidence": "engineering_assumption", "source": "Demonstration articulation: gas_ballast_joint rotation_euler.z"},
    "pressure_curve_measurements": {"value": None, "unit": "1", "evidence": "unknown", "source": "No manufacturer curve points calibrated in runtime"},
    "cooling_water_range_m3_s": {"value": None, "unit": "m^3/s", "evidence": "unknown", "source": "No calibrated water loop or process binding"},
}


def export_contract_metadata():
    """Contract-building metadata; contains no per-instance values or truth."""
    slots = copy.deepcopy(STATE_SLOTS)
    for sid, slot in slots.items():
        slot["read_binding"] = f"VacuumSession.read(instance_id=..., state_ids=['{sid}'])"
        slot["sampling"] = {"mode": "integral" if sid == "vacuum.removed_gas_pa_m3" else "instant", "time_domain": "simulation", "window": "Since instance creation" if sid == "vacuum.removed_gas_pa_m3" else None}
        slot["reference_conditions"] = "Absolute pressure, isothermal host chamber" if "pressure_pa" in sid else "Actual inlet pressure and assumed constant temperature; not standard-state flow" if sid == "vacuum.pumping_speed_m3_s" else "P*V at constant model temperature, no molar conversion" if sid == "vacuum.removed_gas_pa_m3" else "Local Z axis, closed=0 rad, open=pi/2 rad" if sid == "gas_ballast.angle_rad" else None
        if slot["origin"] == "derived":
            slot["observation_note_zh"] = "工程仿真代理观测，estimated；冷凝液量未建模且始终unavailable/null，不是实测传感器。"
    summaries = {"power.set": "设置供电；断电立即停止抽气", "vacuum.start": "启动外部腔体抽气，压力只由宿主step推进", "vacuum.stop": "停止后续抽气；理想隔离阀阻止泵回流，保留已排气量", "vacuum.set_pressure": "设置绝对压力目标；不立即改变实际压力", "vacuum.set_gas_ballast": "切换气镇并设置演示关节角；气镇开启的极限为400 Pa", "vacuum.set_mode": "选择理想压力控制或持续抽至极限模式"}
    examples = {"power.set": {"enabled": True}, "vacuum.start": {}, "vacuum.stop": {}, "vacuum.set_pressure": {"pressure_pa": 5000.0}, "vacuum.set_gas_ballast": {"enabled": True}, "vacuum.set_mode": {"mode": "pressure_control"}}
    caps = {}
    for cap, args in CAPABILITY_ARGUMENTS.items():
        key, sid = CAPABILITY_RESULT_SLOT[cap]
        value_schema = copy.deepcopy(slots[sid]["value_schema"])
        if value_schema.get("type") == "number":
            value_schema["x-unit"] = slots[sid]["unit"]
        effects = [{"state_id": sid, "timing": "immediate", "description_zh": summaries[cap]}]
        if cap == "power.set":
            effects.append({"state_id": "vacuum.running", "timing": "immediate", "description_zh": "断电停止抽气"})
        if cap == "vacuum.set_gas_ballast":
            effects.append({"state_id": "gas_ballast.angle_rad", "timing": "immediate", "description_zh": "气镇演示角：开启pi/2，关闭0；非厂家实测角度"})
        effects += [{"state_id": s, "timing": "immediate", "description_zh": "当前压力和控制状态派生抽速；不推进时间"} for s in ("vacuum.pumping_speed_m3_s", "vacuum.indicated_speed_ratio")]
        effects += [{"state_id": s, "timing": "on_step", "description_zh": "宿主等温压力模型推进并更新显示/累计排气"} for s in ("vacuum.pressure_pa", "vacuum.indicated_pressure_pa", "vacuum.pumping_speed_m3_s", "vacuum.indicated_speed_ratio", "vacuum.removed_gas_pa_m3")]
        caps[cap] = {"availability": "implemented", "visibility": "agent", "kind": "device_command", "description_zh": summaries[cap],
            "input_schema": {"type": "object", "properties": copy.deepcopy(args), "required": list(args), "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {key: value_schema}, "required": [key], "additionalProperties": False},
            "state_effects": effects, "observes": [sid], "example_arguments": [examples[cap]],
            "binding": f"basic40_runtime_vacuum.VacuumSession.invoke(instance_id=..., capability_id='{cap}', arguments=..., request_id=...)"}
    caps["cooling.set_flow_rate"] = {"availability": "declared", "visibility": "agent", "kind": "configuration", "description_zh": "冷却水流量控制未实现；输入仅仿真提案，不能执行或返回成功。示例1e-6m3/s为工程示例，无厂家范围依据。",
        "input_schema": {"type": "object", "properties": {"flow_m3_s": {"type": "number", "exclusiveMinimum": 0, "x-unit": "m^3/s"}}, "required": ["flow_m3_s"], "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "state_effects": [], "observes": [], "example_arguments": [{"flow_m3_s": 1e-6}], "binding": None}
    return {"asset_types": {ASSET_TYPE_ID: {"catalog_id": "CHEM-188", "model": "VACUUBRAND PC 3001 VARIO select", "asset_version": "1.0.1", "parameters": copy.deepcopy(PARAMETERS), "state_slots": slots, "capabilities": caps}},
        "host_capabilities": VacuumHost.describe_host(), "limitations": list(LIMITATIONS)}


class VacuumSceneSynchronizationError(RuntimeError):
    """Process and common time advanced; scene update has unknown partial effects."""


@dataclass
class _Source:
    instance_id: str
    state: dict
    created_at_s: float = 0.0
    revision: int = 0
    chamber_id: str | None = None
    exhaust_pressure_pa: float = 101325.0


@dataclass
class _Chamber:
    volume_m3: float
    pressure_pa: float
    leak_throughput_pa_m3_s: float
    evidence: str
    admitted_gas_pa_m3: float = 0.0


class VacuumHost:
    """Trusted world owner. Each host has one clock shared by all its sources."""
    def __init__(self, *, ambient_pressure_pa=101325.0, scene_adapter=None):
        if not _number(ambient_pressure_pa) or not 400 <= ambient_pressure_pa <= MAXIMUM_BACK_PRESSURE_PA:
            raise ValueError("Ambient pressure must fit the assumed 400..110000 Pa model envelope")
        self.ambient_pressure_pa = float(ambient_pressure_pa)
        self.scene_adapter = scene_adapter
        self.time_s = 0.0
        self._lock = threading.RLock()
        self._instances, self._chambers, self._principals = {}, {}, {}
        self._requests, self._logs, self._scene_faults = {}, {}, []

    @staticmethod
    def describe_host():
        return {"sim.step": {"kind": "simulation", "visibility": "trusted_host_only", "scope": "All registered sources/chambers share the host clock", "binding": "basic40_runtime_vacuum.VacuumHost.step(dt_s)",
                    "input_schema": {"type": "object", "properties": {"dt_s": {"type": "number", "minimum": 0, "maximum": 3600, "x-unit": "s"}}, "required": ["dt_s"], "additionalProperties": False}},
                **{f"host.{method}": {"kind": "configuration", "visibility": "trusted_host_only", "binding": f"basic40_runtime_vacuum.VacuumHost.{method}"} for method in ("create_chamber", "connect_source", "set_exhaust_pressure", "sync_scene")}}

    def create_instance(self, instance_id, asset_type_id=ASSET_TYPE_ID):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != ASSET_TYPE_ID:
                raise ValueError("A unique instance ID and the declared asset type are required")
            s = {"power.enabled": False, "vacuum.running": False, "vacuum.mode": "pressure_control", "vacuum.target_pressure_pa": 200.0,
                 "vacuum.gas_ballast_enabled": False, "vacuum.pressure_pa": None, "vacuum.indicated_pressure_pa": None,
                 "vacuum.pumping_speed_m3_s": 0.0, "vacuum.indicated_speed_ratio": 0.0, "vacuum.removed_gas_pa_m3": 0.0,
                 "gas_ballast.angle_rad": 0.0, "recovery.condensate_volume_m3": None}
            inst = _Source(instance_id, s, created_at_s=self.time_s, exhaust_pressure_pa=self.ambient_pressure_pa)
            if self.scene_adapter:
                self.scene_adapter(instance_id, copy.deepcopy(s))
            self._instances[instance_id] = inst

    def create_chamber(self, chamber_id, *, volume_m3=.001, initial_pressure_pa=None, leak_throughput_pa_m3_s=0.0,
                       evidence="engineering_assumption: external ideal isothermal test chamber"):
        with self._lock:
            p = self.ambient_pressure_pa if initial_pressure_pa is None else initial_pressure_pa
            if not isinstance(chamber_id, str) or not chamber_id or chamber_id in self._chambers:
                raise ValueError("A unique external chamber ID is required")
            if not _number(volume_m3) or not 1e-9 <= volume_m3 <= 1e6 or not _number(p) or not 0 <= p <= self.ambient_pressure_pa:
                raise ValueError("External volume/absolute initial pressure is outside the assumed model envelope")
            if not _number(leak_throughput_pa_m3_s) or not 0 <= leak_throughput_pa_m3_s <= 100 or not isinstance(evidence, str) or not evidence.strip():
                raise ValueError("A finite 0..100 Pa*m3/s reference leak and explicit boundary evidence are required")
            self._chambers[chamber_id] = _Chamber(float(volume_m3), float(p), float(leak_throughput_pa_m3_s), evidence)

    def connect_source(self, instance_id, chamber_id):
        with self._lock:
            inst = self._instances[instance_id]
            if inst.state["vacuum.running"] or chamber_id not in self._chambers:
                raise ValueError("Stop the source and register its external chamber before connecting")
            if any(i is not inst and i.chamber_id == chamber_id for i in self._instances.values()):
                raise ValueError("The lumped solver permits only one source per external chamber")
            inst.chamber_id = chamber_id
            self._refresh(inst)
            inst.revision += 1

    def set_exhaust_pressure(self, instance_id, pressure_pa):
        with self._lock:
            if not _number(pressure_pa) or not 0 <= pressure_pa <= MAXIMUM_BACK_PRESSURE_PA:
                raise ValueError("Exhaust absolute pressure exceeds the 110000 Pa catalog limit")
            self._instances[instance_id].exhaust_pressure_pa = float(pressure_pa)
            self._instances[instance_id].revision += 1

    def register_principal(self, principal_id, *, visibility="agent", instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Unique trusted principal and supported visibility required")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set)) or any(i not in self._instances for i in instance_ids)):
                raise ValueError("Instance grants must name registered sources")
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))
            self._logs[principal_id] = []

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Principal must be provisioned by the host")
        return VacuumSession(self, principal_id)

    @staticmethod
    def _floor(inst):
        ultimate = GAS_BALLAST_ULTIMATE_PRESSURE_PA if inst.state["vacuum.gas_ballast_enabled"] else ULTIMATE_PRESSURE_PA
        target = ultimate if inst.state["vacuum.mode"] == "pump_down" else max(ultimate, inst.state["vacuum.target_pressure_pa"])
        return ultimate, target

    def _refresh(self, inst):
        s = inst.state
        chamber = self._chambers.get(inst.chamber_id)
        p = chamber.pressure_pa if chamber else None
        s["vacuum.pressure_pa"] = p
        s["vacuum.indicated_pressure_pa"] = float(round(p)) if p is not None else None
        s["gas_ballast.angle_rad"] = math.pi / 2 if s["vacuum.gas_ballast_enabled"] else 0.0
        speed = 0.0
        if chamber and s["power.enabled"] and s["vacuum.running"] and p > 0:
            ultimate, target = self._floor(inst)
            potential = MAXIMUM_PUMPING_SPEED_M3_S * max(0.0, 1 - ultimate / p)
            if p > target + 1e-8:
                speed = potential
            elif math.isclose(p, target, abs_tol=1e-8):
                speed = min(potential, chamber.leak_throughput_pa_m3_s * (1 - p / self.ambient_pressure_pa) / p)
        s["vacuum.pumping_speed_m3_s"] = max(0.0, speed)
        s["vacuum.indicated_speed_ratio"] = round(max(0.0, speed) / MAXIMUM_PUMPING_SPEED_M3_S, 2)

    def _advance_chamber(self, chamber, inst, dt):
        """Exact linear segments, with a possible leak-up or target crossing."""
        p, v, leak, ambient = chamber.pressure_pa, chamber.volume_m3, chamber.leak_throughput_pa_m3_s, self.ambient_pressure_pa
        removed, admitted = 0.0, 0.0
        enabled = inst is not None and inst.state["power.enabled"] and inst.state["vacuum.running"]
        ultimate, target = self._floor(inst) if enabled else (0.0, ambient)

        def segment(start, duration, q):
            rate = (q + leak / ambient) / v
            if rate == 0:
                return start, 0.0, 0.0
            eq = (q * ultimate + leak) / (q + leak / ambient)
            factor = -math.expm1(-rate * duration)
            end = start + (eq - start) * factor
            integral_p = eq * duration + (start - eq) * factor / rate
            incoming = max(0.0, leak * (duration - integral_p / ambient))
            outgoing = max(0.0, q * (integral_p - ultimate * duration))
            return end, incoming, outgoing

        remaining = dt
        if not enabled:
            p, admitted, removed = segment(p, remaining, 0.0)
        else:
            if p < target:
                # No vent valve: only the declared external leak may raise pressure.
                if leak == 0 or target >= ambient:
                    p, admitted, removed = segment(p, remaining, 0.0)
                    remaining = 0.0
                else:
                    crossing = -v * ambient / leak * math.log((ambient - target) / (ambient - p))
                    duration = min(remaining, crossing)
                    p, a, r = segment(p, duration, 0.0)
                    admitted += a
                    removed += r
                    remaining -= duration
                    if duration == crossing:
                        p = target
            if remaining > 0:
                q = MAXIMUM_PUMPING_SPEED_M3_S
                rate = (q + leak / ambient) / v
                eq = (q * ultimate + leak) / (q + leak / ambient)
                crossing = math.inf
                if eq < target:
                    crossing = 0.0 if p <= target else -math.log((target - eq) / (p - eq)) / rate
                duration = min(remaining, crossing)
                p, a, r = segment(p, duration, q)
                admitted += a
                removed += r
                remaining -= duration
                if remaining > 0:
                    p = target
                    # Ideal continuous controller removes exactly the ongoing leak.
                    held_admission = leak * (1 - p / ambient) * remaining
                    admitted += held_admission
                    removed += held_admission
        chamber.pressure_pa = min(ambient, max(0.0, p))
        chamber.admitted_gas_pa_m3 += admitted
        if inst is not None:
            inst.state["vacuum.removed_gas_pa_m3"] += removed

    def step(self, dt_s):
        with self._lock:
            if not _number(dt_s) or not 0 <= dt_s <= 3600:
                raise ValueError("Host dt_s must be finite, numeric and in 0..3600 seconds")
            if dt_s == 0:
                return
            by_chamber = {i.chamber_id: i for i in self._instances.values() if i.chamber_id is not None}
            for chamber_id, chamber in self._chambers.items():
                self._advance_chamber(chamber, by_chamber.get(chamber_id), float(dt_s))
            self.time_s += float(dt_s)
            for inst in self._instances.values():
                self._refresh(inst)
                inst.revision += 1
            failures = []
            if self.scene_adapter:
                for inst in self._instances.values():
                    try:
                        self.scene_adapter(inst.instance_id, copy.deepcopy(inst.state))
                    except Exception as exc:
                        self._scene_faults.append({"instance_id": inst.instance_id, "time_s": self.time_s, "message": str(exc)})
                        failures.append(inst.instance_id)
            if failures:
                raise VacuumSceneSynchronizationError("Process and host time advanced; one or more scene updates failed")

    def sync_scene(self, instance_ids=None):
        with self._lock:
            if self.scene_adapter is None:
                raise ValueError("No scene adapter is installed")
            ids = list(self._instances) if instance_ids is None else list(instance_ids)
            if any(i not in self._instances for i in ids):
                raise ValueError("Unknown source in scene synchronization set")
            for instance_id in ids:
                self.scene_adapter(instance_id, copy.deepcopy(self._instances[instance_id].state))

    def debug_snapshot(self):
        """Trusted diagnostics; never exposed by VacuumSession."""
        with self._lock:
            return copy.deepcopy({"time_s": self.time_s, "instances": {k: {"state": v.state, "created_at_s": v.created_at_s, "revision": v.revision, "chamber_id": v.chamber_id, "exhaust_pressure_pa": v.exhaust_pressure_pa} for k, v in self._instances.items()},
                "chambers": {k: vars(v) for k, v in self._chambers.items()}, "scene_faults": self._scene_faults})


class VacuumSession:
    """Identity derives from trusted host registration, never request arguments."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal_id = host, principal_id

    def _visible(self, slot):
        visibility, _ = self.__host._principals[self.__principal_id]
        return slot["visibility"] == "agent" or visibility in ("debug", "evaluator")

    def _instance(self, instance_id):
        _, allowed = self.__host._principals[self.__principal_id]
        if not isinstance(instance_id, str) or (allowed is not None and instance_id not in allowed):
            return None
        return self.__host._instances.get(instance_id)

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": PROTOCOL_VERSION, "instance_id": instance_id if isinstance(instance_id, str) else None, "request_id": request_id,
                "operation_id": None, "status": status, "state_revision": inst.revision if inst else None,
                "clock": {"domain": "simulation", "time_s": self.__host.time_s}, "result": {} if result is None else result,
                "observations": {} if observations is None else observations, "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Unknown or inaccessible instance", "INVALID_ARGUMENT": "Arguments do not match the supported schema or model envelope", "ACCESS_DENIED": "State is unavailable to this session", "UNKNOWN_CAPABILITY": "Unknown capability", "UNSUPPORTED": "Capability is declared but not implemented", "REQUEST_CONFLICT": "Request ID was already used with different content", "PRECONDITION_FAILED": "Powered source and a registered external chamber are required to start", "DEVICE_ERROR": "Scene backend failed; external scene effects are unknown"}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": messages[code], "retryable": False,
            "side_effects": "unknown" if status == "failed" else "none", "known_progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID:
                return self._error(None, "INVALID_ARGUMENT")
            spec = export_contract_metadata()["asset_types"][ASSET_TYPE_ID]
            visible = {sid: slot for sid, slot in spec["state_slots"].items() if self._visible(slot)}
            caps = copy.deepcopy(spec["capabilities"])
            for cap in caps.values():
                cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in visible]
                cap["observes"] = [s for s in cap["observes"] if s in visible]
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": ASSET_TYPE_ID, "asset_version": "1.0.1", "instance_id": instance_id,
                "catalog_id": "CHEM-188", "model": spec["model"], "adapter": "basic40_runtime_vacuum.VacuumSession", "capabilities": caps, "state_slots": visible, "limitations": list(LIMITATIONS)}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            ids = [s for s, info in STATE_SLOTS.items() if self._visible(info)] if state_ids is None else state_ids
            if not isinstance(ids, list) or any(not isinstance(s, str) for s in ids) or len(set(ids)) != len(ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(s not in STATE_SLOTS or not self._visible(STATE_SLOTS[s]) for s in ids):
                return self._error(instance_id, "ACCESS_DENIED")
            observations = {}
            for sid in ids:
                slot, value = STATE_SLOTS[sid], copy.deepcopy(inst.state[sid])
                item = {"value": value, "unit": slot["unit"], "quality": "unavailable" if value is None else "estimated" if slot["origin"] == "derived" else "valid", "sample_time_s": self.__host.time_s,
                        "sample_window_s": [inst.created_at_s, self.__host.time_s] if sid == "vacuum.removed_gas_pa_m3" else None}
                if value is None:
                    item["reason"] = "Condensate recovery has no implemented observation model" if sid.startswith("recovery.") else "No external chamber connected"
                observations[sid] = item
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id if isinstance(request_id, str) else None)
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256 or not isinstance(capability_id, str) or not isinstance(arguments, dict):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id if isinstance(request_id, str) else None)
            try:
                digest = hashlib.sha256(json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            except (ValueError, TypeError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            request_key = (self.__principal_id, instance_id, request_id)
            if request_key in host._requests:
                previous, response = host._requests[request_key]
                return copy.deepcopy(response) if previous == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._invoke_checked(inst, capability_id, arguments, request_id)
            host._requests[request_key] = (digest, copy.deepcopy(response))
            host._logs[self.__principal_id].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id, "response": copy.deepcopy(response)})
            return response

    def _invoke_checked(self, inst, cap, args, request_id):
        host = self.__host
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request_id, status)
        if cap == "cooling.set_flow_rate":
            return fail("UNSUPPORTED", "unsupported")
        if cap not in CAPABILITY_ARGUMENTS:
            return fail("UNKNOWN_CAPABILITY")
        expected = CAPABILITY_ARGUMENTS[cap]
        if set(args) != set(expected):
            return fail("INVALID_ARGUMENT")
        for key, schema in expected.items():
            value = args[key]
            if schema["type"] == "boolean" and type(value) is not bool:
                return fail("INVALID_ARGUMENT")
            if schema["type"] == "number" and (not _number(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf)):
                return fail("INVALID_ARGUMENT")
            if schema["type"] == "string" and (not isinstance(value, str) or value not in schema["enum"]):
                return fail("INVALID_ARGUMENT")
        if cap == "vacuum.set_pressure" and args["pressure_pa"] > host.ambient_pressure_pa:
            return fail("INVALID_ARGUMENT")
        if cap == "vacuum.start" and (not inst.state["power.enabled"] or inst.chamber_id is None or inst.exhaust_pressure_pa > MAXIMUM_BACK_PRESSURE_PA):
            return fail("PRECONDITION_FAILED")
        key, slot = CAPABILITY_RESULT_SLOT[cap]
        value = True if cap == "vacuum.start" else False if cap == "vacuum.stop" else args[key]
        candidate = copy.deepcopy(inst)
        candidate.state[slot] = value
        if cap == "power.set" and not value:
            candidate.state["vacuum.running"] = False
        host._refresh(candidate)
        updates = {sid: v for sid, v in candidate.state.items() if v != inst.state[sid]}
        if host.scene_adapter:
            try:
                host.scene_adapter(inst.instance_id, copy.deepcopy(updates))
            except Exception:
                return fail("DEVICE_ERROR", "failed")
        inst.state = candidate.state
        inst.revision += 1
        return self._envelope(inst.instance_id, request_id, result={key: value})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(state_id, str) or state_id not in STATE_SLOTS or not self._visible(STATE_SLOTS[state_id]):
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            slot = STATE_SLOTS[state_id]
            if not slot["write_via"]:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slot["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return copy.deepcopy(self.__host._logs[self.__principal_id])


def validate_runtime(output=None):
    """Execute this module's synthetic tests and record actual method/time."""
    import importlib.util
    import io
    import unittest
    test_file = Path(__file__).resolve().parents[1] / "tests" / "test_basic40_vacuum.py"
    spec = importlib.util.spec_from_file_location("test_basic40_vacuum", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    report = {"schema_version": "1.0.0", "recorded_at": datetime.now(timezone.utc).isoformat(), "method": "Deterministic unittest via real VacuumSession describe/read/invoke/write and trusted VacuumHost.step; closed-form reference pressure and P*V conservation",
        "environment": {"python": platform.python_version(), "platform": platform.platform()}, "tests_run": result.testsRun,
        "result": "passed" if result.wasSuccessful() else "failed", "failures": len(result.failures), "errors": len(result.errors), "log": stream.getvalue(),
        "layers": {"process": "passed" if result.wasSuccessful() else "failed", "host": "passed" if result.wasSuccessful() else "failed", "scene": "not_run", "robot": "not_run", "hardware": "not_run"},
        "data_classification": "Synthetic fixtures only, no deployed task truth", "limitations": list(LIMITATIONS)}
    if output is not None:
        output = Path(output)
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

# CHEM-024 extension. Deliberately appended: CHEM-188 pump implementation above
# and its public constants/classes remain unchanged.
RV10_ASSET_TYPE_ID = "instrument.ika_rv10_digital_v"
RV10_MINIMUM_SPEED_RAD_S = 5.0 * math.pi / 30.0
RV10_MAXIMUM_SPEED_RAD_S = 280.0 * math.pi / 30.0
RV10_MAXIMUM_LIFT_M = 0.140
RV10_MAXIMUM_BATH_TEMPERATURE_K = 453.15
RV10_RATED_HEATING_POWER_W = 1350.0
_RV10_NULL_NUMBER = {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]}
RV10_STATE_SLOTS = {
    "power.enabled": _slot("1", "setpoint", {"type": "boolean"}, capability="power.set", parameter="enabled"),
    "lift.target_position_m": _slot("m", "setpoint", {"type": "number", "minimum": 0, "maximum": .14}, capability="lift.set_position", parameter="position_m"),
    "lift.position_m": _slot("m", "process_truth", {"type": "number", "minimum": 0, "maximum": .14}, "debug"),
    "lift.indicated_position_m": _slot("m", "derived", {"type": "number", "minimum": 0, "maximum": .14}),
    "lift.moving": _slot("1", "derived", {"type": "boolean"}),
    "rotation.target_speed_rad_s": _slot("rad/s", "setpoint", {"type": "number", "minimum": RV10_MINIMUM_SPEED_RAD_S, "maximum": RV10_MAXIMUM_SPEED_RAD_S}, capability="rotation.set_speed", parameter="speed_rad_s"),
    "rotation.running": _slot("1", "derived", {"type": "boolean"}),
    "rotation.speed_rad_s": _slot("rad/s", "process_truth", {"type": "number", "minimum": 0, "maximum": RV10_MAXIMUM_SPEED_RAD_S}, "debug"),
    "rotation.indicated_speed_rad_s": _slot("rad/s", "derived", {"type": "number", "minimum": 0, "maximum": RV10_MAXIMUM_SPEED_RAD_S}),
    "rotation.angle_rad": _slot("rad", "process_truth", {"type": "number", "minimum": 0}, "debug"),
    "thermal.target_temperature_k": _slot("K", "setpoint", {"type": "number", "exclusiveMinimum": 0, "maximum": 453.15}, capability="thermal.set_temperature", parameter="temperature_k"),
    "thermal.enabled": _slot("1", "derived", {"type": "boolean"}),
    "thermal.temperature_k": _slot("K", "process_truth", _RV10_NULL_NUMBER, "debug"),
    "thermal.indicated_temperature_k": _slot("K", "derived", _RV10_NULL_NUMBER),
    "thermal.heating_power_w": _slot("W", "process_truth", {"anyOf": [{"type": "number", "minimum": 0, "maximum": 1350}, {"type": "null"}]}, "debug"),
    "vacuum.source_asset_type_id": _slot("1", "derived", {"type": "string", "const": ASSET_TYPE_ID}),
    "vacuum.source_instance_id": _slot("1", "derived", {"anyOf": [{"type": "string"}, {"type": "null"}]}),
    "vacuum.pressure_pa": _slot("Pa", "process_truth", _RV10_NULL_NUMBER, "debug"),
    "vacuum.indicated_pressure_pa": _slot("Pa", "derived", _RV10_NULL_NUMBER),
    "evaporation.running": _slot("1", "derived", {"type": "boolean"}),
    "evaporation.remaining_mass_kg": _slot("kg", "process_truth", _RV10_NULL_NUMBER, "debug"),
    "evaporation.evaporated_mass_kg": _slot("kg", "process_truth", _RV10_NULL_NUMBER, "debug"),
    "evaporation.uncondensed_mass_kg": _slot("kg", "process_truth", _RV10_NULL_NUMBER, "debug"),
    "recovery.condensate_mass_kg": _slot("kg", "process_truth", _RV10_NULL_NUMBER, "debug"),
    "recovery.indicated_condensate_mass_kg": _slot("kg", "derived", _RV10_NULL_NUMBER),
    "recovery.condensate_volume_m3": _slot("m^3", "derived", {"type": "null"}),
}
RV10_CAPABILITY_ARGUMENTS = {
    "power.set": {"enabled": {"type": "boolean"}},
    "lift.set_position": {"position_m": {"type": "number", "minimum": 0, "maximum": .14, "x-unit": "m"}},
    "rotation.set_speed": {"speed_rad_s": {"type": "number", "minimum": RV10_MINIMUM_SPEED_RAD_S, "maximum": RV10_MAXIMUM_SPEED_RAD_S, "x-unit": "rad/s", "description": "Catalog 5..280 rpm converted by rpm*pi/30; angular velocity about the tilted local shaft Z."}},
    "rotation.start": {}, "rotation.stop": {},
    "thermal.set_temperature": {"temperature_k": {"type": "number", "exclusiveMinimum": 0, "maximum": 453.15, "x-unit": "K", "description": "Bath target; additionally must be at least the host-configured ambient temperature (RT). Success only writes a setpoint."}},
    "thermal.start": {}, "thermal.stop": {},
}
RV10_CAPABILITY_RESULT_SLOT = {
    "power.set": ("enabled", "power.enabled"),
    "lift.set_position": ("position_m", "lift.target_position_m"),
    "rotation.set_speed": ("speed_rad_s", "rotation.target_speed_rad_s"),
    "rotation.start": ("running", "rotation.running"), "rotation.stop": ("running", "rotation.running"),
    "thermal.set_temperature": ("temperature_k", "thermal.target_temperature_k"),
    "thermal.start": ("enabled", "thermal.enabled"), "thermal.stop": ("enabled", "thermal.enabled"),
}
RV10_LIMITATIONS = [
    "Catalog constraints: 0..0.14 m lift; 5..280 rpm converted to rad/s; bath up to 453.15 K; rated maximum heater power 1350 W, not total electricity consumption.",
    "engineering_assumption: ideal independent lift/rotation/thermal controllers; no measured acceleration, motor torque, mechanical load, bath heat capacity, thermostat hysteresis or emergency lift behavior.",
    "engineering_assumption: default ambient 293.15 K and lift travel speed 0.02 m/s are caller-configurable synthetic settings, not manufacturer specifications.",
    "Bath temperature and heating power remain null until a trusted host explicitly supplies effective heat capacity, initial temperature, linear heat-loss coefficient and evidence. Part mass is not inferred from whole-instrument mass.",
    "engineering_assumption: configured bath uses lumped C*dT/dt=P-H*(T-Tambient), an ideal thermostat and 0..1350 W heater; no active cooling or fluid-specific boiling thermodynamics.",
    "Evaporation remains null without explicit host-supplied charge mass, empirical mass rate, condensation efficiency and evidence. Configured constant-rate evaporation runs only with power, rotation, heating, a configured bath and an external pressure snapshot; it is not inferred from fluid constants, pressure or speed.",
    "engineering_assumption: empirical evaporation conserves charge=remaining+condensate+uncondensed; latent heat, saturation pressure, density, vapor transport and coolant capacity are not modeled. Condensate volume is always null because no density is supplied.",
    "The vacuum port depends on external instrument.vacuubrand_pc3001_vario_select. RV10 creates no pump; trusted host supplies source ID and pressure snapshots. Snapshot staleness is exposed after host time advances, and this RV10 runtime does not advance the external source.",
    "Estimated observations use synthetic rounding (lift 1e-6 m, rotation 0.01 rad/s, temperature 0.1 K, pressure 1 Pa, condensate 1e-6 kg); these are not manufacturer display resolutions or accuracy.",
    "Timer and vacuum-pressure setting are declared unsupported; pressure commands belong to the external source. No cooling-water, robot, hardware, collision or chemistry solver is implemented here.",
    "Agent API filters process truth from describe/read/invoke/write/logs; trusted Python memory and filesystem remain outside this boundary. Deploy untrusted code separately.",
    "Synchronous scene callback command failure preserves process state but external scene effects may be partial/unknown. A callback failure during host step retains the advanced process and common clock.",
    "Request deduplication is in memory per principal/instance and does not survive restart. Reads do not advance time; only RV10Host.step advances the RV10 host clock.",
]


def export_rv10_contract_metadata():
    """Type-only metadata: independent from the unchanged CHEM-188 export."""
    slots = copy.deepcopy(RV10_STATE_SLOTS)
    integral_ids = ("evaporation.evaporated_mass_kg", "evaporation.uncondensed_mass_kg", "recovery.condensate_mass_kg", "recovery.indicated_condensate_mass_kg")
    for sid, slot in slots.items():
        slot["read_binding"] = f"RV10Session.read(instance_id=..., state_ids=['{sid}'])"
        slot["sampling"] = {"mode": "integral" if sid in integral_ids else "instant", "time_domain": "simulation", "window": "Since trusted host configured the current charge" if sid in integral_ids else None}
        slot["reference_conditions"] = "Absolute external pressure snapshot; no internal pump" if "pressure_pa" in sid else "Local shaft Z; continuous unwrapped angle" if sid == "rotation.angle_rad" else "Local tilted shaft Z" if sid.startswith("rotation.") and "rad_s" in sid else "Local lift Z relative to zero travel" if sid.startswith("lift.") and sid.endswith("_m") else None
        if slot["origin"] == "derived":
            slot["observation_note_zh"] = "工程代理观测，estimated；缺参数时unavailable/null；外部压力时间戳落后宿主时标stale。非实测传感器。"
    summaries = {"power.set": "设置供电；断电停止旋转/主动升降/加热，保留已发生变化", "lift.set_position": "设置0..0.14m升降目标，实际升降只由宿主step推进", "rotation.set_speed": "设置5..280rpm对应的SI角速度；理想驱动速度立即变化，角度只在step累积", "rotation.start": "启动理想旋转驱动；连续角度由step积分", "rotation.stop": "停止旋转并保留已转过的连续角度", "thermal.set_temperature": "设置RT..453.15K浴温目标，不立即改变浴温", "thermal.start": "启用已配置热模型的浴温控制，供热额定上限1350W", "thermal.stop": "关闭加热；配置的被动热损仍由step推进"}
    examples = {"power.set": {"enabled": True}, "lift.set_position": {"position_m": .10}, "rotation.set_speed": {"speed_rad_s": 10.0}, "rotation.start": {}, "rotation.stop": {}, "thermal.set_temperature": {"temperature_k": 333.15}, "thermal.start": {}, "thermal.stop": {}}
    on_step = ("lift.position_m", "lift.indicated_position_m", "lift.moving", "rotation.angle_rad", "thermal.temperature_k", "thermal.indicated_temperature_k", "thermal.heating_power_w", "evaporation.running", "evaporation.remaining_mass_kg", "evaporation.evaporated_mass_kg", "evaporation.uncondensed_mass_kg", "recovery.condensate_mass_kg", "recovery.indicated_condensate_mass_kg")
    immediate = {
        "power.set": ("rotation.running", "thermal.enabled", "rotation.speed_rad_s", "rotation.indicated_speed_rad_s", "lift.moving", "thermal.heating_power_w", "evaporation.running"),
        "lift.set_position": ("lift.moving",),
        "rotation.set_speed": ("rotation.speed_rad_s", "rotation.indicated_speed_rad_s"),
        "rotation.start": ("rotation.speed_rad_s", "rotation.indicated_speed_rad_s", "evaporation.running"),
        "rotation.stop": ("rotation.speed_rad_s", "rotation.indicated_speed_rad_s", "evaporation.running"),
        "thermal.set_temperature": ("thermal.heating_power_w",),
        "thermal.start": ("thermal.heating_power_w", "evaporation.running"),
        "thermal.stop": ("thermal.heating_power_w", "evaporation.running"),
    }
    caps = {}
    for cap, args in RV10_CAPABILITY_ARGUMENTS.items():
        key, sid = RV10_CAPABILITY_RESULT_SLOT[cap]
        result_schema = copy.deepcopy(slots[sid]["value_schema"])
        if result_schema.get("type") == "number":
            result_schema["x-unit"] = slots[sid]["unit"]
        caps[cap] = {"availability": "implemented", "visibility": "agent", "kind": "device_command", "description_zh": summaries[cap],
            "input_schema": {"type": "object", "properties": copy.deepcopy(args), "required": list(args), "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {key: result_schema}, "required": [key], "additionalProperties": False},
            "state_effects": [{"state_id": s, "timing": "immediate", "description_zh": summaries[cap] if s == sid else "同步派生控制状态；不推进时间"} for s in (sid, *immediate[cap])] + [{"state_id": s, "timing": "on_step", "description_zh": "仅宿主step推进工程模型，保留各过程独立性"} for s in on_step],
            "observes": [sid], "example_arguments": [examples[cap]], "binding": f"basic40_runtime_vacuum.RV10Session.invoke(instance_id=..., capability_id='{cap}', arguments=..., request_id=...)"}
    for cap, arg, unit, explanation in (
            ("vacuum.set_pressure", "pressure_pa", "Pa", "不支持：压力设定归外接真空控制器/真空泵（本仪器无内部泵或压力控制驱动），本仪器上调用返回UNSUPPORTED；外部腔体抽气由vacuum.start/vacuum.stop表示。"),
            ("timer.set_duration", "duration_s", "s", "目录计时功能已登记，但无已核实的计时范围/完成行为与运行绑定。")):
        caps[cap] = {"availability": "unsupported" if cap == "vacuum.set_pressure" else "declared", "visibility": "agent", "kind": "device_command", "description_zh": explanation,   # 1.3.1: set_pressure is unsupported, not planned
            "input_schema": {"type": "object", "properties": {arg: {"type": "number", "exclusiveMinimum": 0, "x-unit": unit}}, "required": [arg], "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "state_effects": [], "observes": [], "example_arguments": [{arg: 1000.0 if unit == "Pa" else 60.0}], "binding": None}
    params = {
        "lift_travel_m": {"value": .14, "unit": "m", "evidence": "catalog"},
        "rotation_minimum_rad_s": {"value": RV10_MINIMUM_SPEED_RAD_S, "unit": "rad/s", "evidence": "catalog", "source": "5 rpm*pi/30"},
        "rotation_maximum_rad_s": {"value": RV10_MAXIMUM_SPEED_RAD_S, "unit": "rad/s", "evidence": "catalog", "source": "280 rpm*pi/30"},
        "bath_maximum_temperature_k": {"value": 453.15, "unit": "K", "evidence": "catalog"},
        "rated_heating_power_w": {"value": 1350.0, "unit": "W", "evidence": "catalog"},
        "default_ambient_temperature_k": {"value": 293.15, "unit": "K", "evidence": "engineering_assumption"},
        "default_lift_speed_m_s": {"value": .02, "unit": "m/s", "evidence": "engineering_assumption"},
        "bath_effective_heat_capacity_j_k": {"value": None, "unit": "J/K", "evidence": "unknown", "source": "Must be supplied by trusted host; no inferred part mass or fluid constants"},
        "fluid_latent_heat_j_kg": {"value": None, "unit": "J/kg", "evidence": "unknown"},
        "fluid_density_kg_m3": {"value": None, "unit": "kg/m^3", "evidence": "unknown"},
    }
    return {"asset_types": {RV10_ASSET_TYPE_ID: {"catalog_id": "CHEM-024", "model": "IKA RV 10 digital V", "asset_version": "1.1.0", "parameters": params, "state_slots": slots, "capabilities": caps}}, "host_capabilities": RV10Host.describe_host(), "limitations": list(RV10_LIMITATIONS)}


@dataclass
class _RV10Instance:
    instance_id: str
    state: dict
    created_at_s: float = 0.0
    revision: int = 0
    bath: dict | None = None
    evaporation: dict | None = None
    vacuum_sample_time_s: float | None = None
    configuration_evidence: dict = field(default_factory=dict)


class RV10SceneSynchronizationError(RuntimeError):
    """Common clock and process advanced; external scene effects are unknown."""


class RV10Host:
    """Trusted RV10 world owner. External sources have no instance in this host."""
    def __init__(self, *, ambient_temperature_k=293.15, lift_speed_m_s=.02, scene_adapter=None):
        if not _number(ambient_temperature_k) or not 0 < ambient_temperature_k <= 453.15:
            raise ValueError("Ambient temperature must be positive and at most 453.15 K")
        if not _number(lift_speed_m_s) or not 0 < lift_speed_m_s <= 1:
            raise ValueError("Engineering lift speed must be in (0,1] m/s")
        self.ambient_temperature_k = float(ambient_temperature_k)
        self.lift_speed_m_s = float(lift_speed_m_s)
        self.scene_adapter, self.time_s = scene_adapter, 0.0
        self._lock = threading.RLock()
        self._instances, self._principals, self._requests, self._logs = {}, {}, {}, {}
        self._scene_faults = []

    @staticmethod
    def describe_host():
        return {"sim.step": {"kind": "simulation", "visibility": "trusted_host_only", "scope": "All registered RV10 instances share one host clock", "binding": "basic40_runtime_vacuum.RV10Host.step(dt_s)", "input_schema": {"type": "object", "properties": {"dt_s": {"type": "number", "minimum": 0, "maximum": 3600, "x-unit": "s"}}, "required": ["dt_s"], "additionalProperties": False}},
            **{f"host.{method}": {"kind": "configuration", "visibility": "trusted_host_only", "binding": f"basic40_runtime_vacuum.RV10Host.{method}"} for method in ("configure_bath", "configure_evaporation", "connect_vacuum_source", "set_external_pressure", "sync_scene")}}

    def create_instance(self, instance_id, asset_type_id=RV10_ASSET_TYPE_ID):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != RV10_ASSET_TYPE_ID:
                raise ValueError("Unique instance ID and RV10 type are required")
            s = {"power.enabled": False, "lift.target_position_m": 0.0, "lift.position_m": 0.0, "lift.indicated_position_m": 0.0, "lift.moving": False,
                "rotation.target_speed_rad_s": RV10_MINIMUM_SPEED_RAD_S, "rotation.running": False, "rotation.speed_rad_s": 0.0, "rotation.indicated_speed_rad_s": 0.0, "rotation.angle_rad": 0.0,
                "thermal.target_temperature_k": self.ambient_temperature_k, "thermal.enabled": False, "thermal.temperature_k": None, "thermal.indicated_temperature_k": None, "thermal.heating_power_w": None,
                "vacuum.source_asset_type_id": ASSET_TYPE_ID, "vacuum.source_instance_id": None, "vacuum.pressure_pa": None, "vacuum.indicated_pressure_pa": None,
                "evaporation.running": False, "evaporation.remaining_mass_kg": None, "evaporation.evaporated_mass_kg": None, "evaporation.uncondensed_mass_kg": None, "recovery.condensate_mass_kg": None, "recovery.indicated_condensate_mass_kg": None, "recovery.condensate_volume_m3": None}
            inst = _RV10Instance(instance_id, s, created_at_s=self.time_s)
            if self.scene_adapter:
                self.scene_adapter(instance_id, copy.deepcopy(s))
            self._instances[instance_id] = inst

    @staticmethod
    def _evidence(evidence):
        return isinstance(evidence, str) and bool(evidence.strip())

    def configure_bath(self, instance_id, *, effective_heat_capacity_j_k, initial_temperature_k, heat_loss_w_k, evidence):
        """Trusted engineering fixture, permitted only while heating is disabled."""
        with self._lock:
            inst = self._instances[instance_id]
            if inst.state["thermal.enabled"]:
                raise ValueError("Stop heating before configuring the bath")
            if not _number(effective_heat_capacity_j_k) or not 0 < effective_heat_capacity_j_k <= 1e12 or not _number(initial_temperature_k) or not 0 < initial_temperature_k <= 453.15 or not _number(heat_loss_w_k) or not 0 <= heat_loss_w_k <= 1e9 or not self._evidence(evidence):
                raise ValueError("Explicit finite heat capacity, temperature, loss coefficient and evidence required")
            inst.bath = {"effective_heat_capacity_j_k": float(effective_heat_capacity_j_k), "heat_loss_w_k": float(heat_loss_w_k)}
            inst.state["thermal.temperature_k"] = float(initial_temperature_k)
            inst.configuration_evidence["bath"] = evidence
            self._refresh(inst)
            inst.revision += 1

    def configure_evaporation(self, instance_id, *, charge_mass_kg, evaporation_rate_kg_s, condensation_efficiency, evidence):
        """Explicit empirical mass budget; no latent heat/density is guessed."""
        with self._lock:
            inst = self._instances[instance_id]
            if inst.state["rotation.running"] or inst.state["thermal.enabled"]:
                raise ValueError("Stop rotation and heating before configuring a charge")
            if not _number(charge_mass_kg) or not 0 < charge_mass_kg <= 1e6 or not _number(evaporation_rate_kg_s) or not 0 <= evaporation_rate_kg_s <= 1e3 or not _number(condensation_efficiency) or not 0 <= condensation_efficiency <= 1 or not self._evidence(evidence):
                raise ValueError("Explicit positive charge, nonnegative rate, efficiency in [0,1] and evidence required")
            inst.evaporation = {"charge_mass_kg": float(charge_mass_kg), "evaporation_rate_kg_s": float(evaporation_rate_kg_s), "condensation_efficiency": float(condensation_efficiency), "configured_at_s": self.time_s}
            inst.configuration_evidence["evaporation"] = evidence
            inst.state.update({"evaporation.remaining_mass_kg": float(charge_mass_kg), "evaporation.evaporated_mass_kg": 0.0, "evaporation.uncondensed_mass_kg": 0.0, "recovery.condensate_mass_kg": 0.0})
            self._refresh(inst)
            inst.revision += 1

    def configure_template_charge(self, instance_id, *, solvent_id, volume_m3, condensation_efficiency, heat_transfer_w_k, evidence):
        """2026-10 optional template path: library solvent + volume; rate from NIST Antoine data and an assumed UA."""
        return _vacuum_templates("ika_rv10").configure_charge(self, instance_id, solvent_id=solvent_id, volume_m3=volume_m3,
            condensation_efficiency=condensation_efficiency, heat_transfer_w_k=heat_transfer_w_k, evidence=evidence)

    def template_charge_report(self, instance_id):
        """Trusted evaluator view of the template charge (identity, boiling point, rate, provenance)."""
        with self._lock:
            return _vacuum_templates("ika_rv10").report(copy.deepcopy(self._instances[instance_id]))

    def connect_vacuum_source(self, instance_id, source_instance_id, *, pressure_pa=None, source_asset_type_id=ASSET_TYPE_ID, evidence):
        """Register an external source reference, never create a pump."""
        with self._lock:
            if not isinstance(source_instance_id, str) or not source_instance_id or source_asset_type_id != ASSET_TYPE_ID or not self._evidence(evidence) or (pressure_pa is not None and (not _number(pressure_pa) or pressure_pa < 0)):
                raise ValueError("External VACUUBRAND source ID, optional absolute pressure and evidence required")
            inst = self._instances[instance_id]
            inst.state["vacuum.source_instance_id"] = source_instance_id
            inst.state["vacuum.pressure_pa"] = None if pressure_pa is None else float(pressure_pa)
            inst.vacuum_sample_time_s = None if pressure_pa is None else self.time_s
            inst.configuration_evidence["vacuum"] = evidence
            self._refresh(inst)
            inst.revision += 1

    def set_external_pressure(self, instance_id, pressure_pa, *, evidence):
        with self._lock:
            inst = self._instances[instance_id]
            if inst.state["vacuum.source_instance_id"] is None or not self._evidence(evidence) or (pressure_pa is not None and (not _number(pressure_pa) or pressure_pa < 0)):
                raise ValueError("A connected external source, optional absolute pressure and evidence required")
            inst.state["vacuum.pressure_pa"] = None if pressure_pa is None else float(pressure_pa)
            inst.vacuum_sample_time_s = None if pressure_pa is None else self.time_s
            inst.configuration_evidence["vacuum"] = evidence
            self._refresh(inst)
            inst.revision += 1

    def register_principal(self, principal_id, *, visibility="agent", instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("A unique trusted principal and supported visibility are required")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set)) or any(i not in self._instances for i in instance_ids)):
                raise ValueError("Instance grants must name registered RV10s")
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))
            self._logs[principal_id] = []

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Principal must be provisioned by the trusted host")
        return RV10Session(self, principal_id)

    def _refresh(self, inst):
        s = inst.state
        s["lift.indicated_position_m"] = round(s["lift.position_m"], 6)
        s["lift.moving"] = bool(s["power.enabled"] and not math.isclose(s["lift.position_m"], s["lift.target_position_m"], abs_tol=1e-12))
        s["rotation.speed_rad_s"] = s["rotation.target_speed_rad_s"] if s["rotation.running"] and s["power.enabled"] else 0.0
        s["rotation.indicated_speed_rad_s"] = min(RV10_MAXIMUM_SPEED_RAD_S, round(s["rotation.speed_rad_s"], 2))
        temp = s["thermal.temperature_k"]
        s["thermal.indicated_temperature_k"] = None if temp is None else round(temp, 1)
        power = None
        if inst.bath:
            power = 0.0
            if s["thermal.enabled"] and s["power.enabled"]:
                if temp < s["thermal.target_temperature_k"] - 1e-10:
                    power = RV10_RATED_HEATING_POWER_W
                elif math.isclose(temp, s["thermal.target_temperature_k"], abs_tol=1e-10):
                    power = min(RV10_RATED_HEATING_POWER_W, max(0.0, inst.bath["heat_loss_w_k"] * (temp - self.ambient_temperature_k)))
        s["thermal.heating_power_w"] = power
        p = s["vacuum.pressure_pa"]
        s["vacuum.indicated_pressure_pa"] = None if p is None else float(round(p))
        if inst.evaporation and "template" in inst.evaporation:  # 2026-10 template charge: rate from real Antoine data
            inst.evaporation["evaporation_rate_kg_s"] = _vacuum_templates("ika_rv10").current_rate_kg_s(inst)
        s["evaporation.running"] = bool(inst.evaporation and s["evaporation.remaining_mass_kg"] > 0 and inst.evaporation["evaporation_rate_kg_s"] > 0 and s["power.enabled"] and s["rotation.running"] and s["thermal.enabled"] and inst.bath and s["vacuum.source_instance_id"] is not None and p is not None)
        mass = s["recovery.condensate_mass_kg"]
        s["recovery.indicated_condensate_mass_kg"] = None if mass is None else round(mass, 6)

    def _advance_bath(self, inst, dt):
        if inst.bath is None:
            return
        s, cfg = inst.state, inst.bath
        temp, target, ambient = s["thermal.temperature_k"], s["thermal.target_temperature_k"], self.ambient_temperature_k
        c, h = cfg["effective_heat_capacity_j_k"], cfg["heat_loss_w_k"]
        enabled = s["power.enabled"] and s["thermal.enabled"]
        if not enabled or temp > target:
            cooled = ambient + (temp - ambient) * math.exp(-h * dt / c) if h else temp
            if not enabled or cooled >= target or target <= ambient:
                s["thermal.temperature_k"] = cooled
                return
            crossing = c / h * math.log((temp - ambient) / (target - ambient))
            temp, dt = target, max(0.0, dt - crossing)
        if h == 0:
            end = temp + RV10_RATED_HEATING_POWER_W * dt / c
        else:
            decay = h * dt / c
            if decay <= 1e-6:
                # Stable adiabatic limit: do not multiply an overflowing
                # equilibrium by an underflowing exponential difference.
                fraction = -math.expm1(-decay) / decay if decay else 1.0
                end = temp + ((RV10_RATED_HEATING_POWER_W - h * (temp - ambient)) * dt / c) * fraction
            else:
                eq = ambient + RV10_RATED_HEATING_POWER_W / h
                end = temp + (eq - temp) * (-math.expm1(-decay))
        s["thermal.temperature_k"] = min(target, end)

    def step(self, dt_s):
        with self._lock:
            if not _number(dt_s) or not 0 <= dt_s <= 3600:
                raise ValueError("Host dt_s must be finite, numeric and in 0..3600 s")
            if dt_s == 0:
                return
            dt = float(dt_s)
            for inst in self._instances.values():
                s = inst.state
                if s["power.enabled"]:
                    difference = s["lift.target_position_m"] - s["lift.position_m"]
                    s["lift.position_m"] += math.copysign(min(abs(difference), self.lift_speed_m_s * dt), difference)
                s["rotation.angle_rad"] += s["rotation.speed_rad_s"] * dt
                if inst.evaporation and "template" in inst.evaporation:  # 2026-10: start-of-step Antoine-based rate (explicit Euler)
                    inst.evaporation["evaporation_rate_kg_s"] = _vacuum_templates("ika_rv10").current_rate_kg_s(inst)
                self._advance_bath(inst, dt)
                if s["evaporation.running"]:
                    evaporated = min(s["evaporation.remaining_mass_kg"], inst.evaporation["evaporation_rate_kg_s"] * dt)
                    condensed = evaporated * inst.evaporation["condensation_efficiency"]
                    s["evaporation.remaining_mass_kg"] -= evaporated
                    s["evaporation.evaporated_mass_kg"] += evaporated
                    s["recovery.condensate_mass_kg"] += condensed
                    s["evaporation.uncondensed_mass_kg"] += evaporated - condensed
                self._refresh(inst)
                inst.revision += 1
            self.time_s += dt
            failures = []
            if self.scene_adapter:
                for inst in self._instances.values():
                    try:
                        self.scene_adapter(inst.instance_id, copy.deepcopy(inst.state))
                    except Exception as exc:
                        self._scene_faults.append({"instance_id": inst.instance_id, "time_s": self.time_s, "message": str(exc)})
                        failures.append(inst.instance_id)
            if failures:
                raise RV10SceneSynchronizationError("Process and common clock advanced; one or more scene updates failed")

    def sync_scene(self, instance_ids=None):
        with self._lock:
            if self.scene_adapter is None:
                raise ValueError("No scene adapter is installed")
            ids = list(self._instances) if instance_ids is None else list(instance_ids)
            if any(i not in self._instances for i in ids):
                raise ValueError("Unknown RV10 in synchronization set")
            for instance_id in ids:
                self.scene_adapter(instance_id, copy.deepcopy(self._instances[instance_id].state))

    def debug_snapshot(self):
        """Trusted diagnostic, deliberately not exposed on RV10Session."""
        with self._lock:
            return copy.deepcopy({"time_s": self.time_s, "instances": {k: vars(v) for k, v in self._instances.items()}, "scene_faults": self._scene_faults})


class RV10Session:
    """Access-filtering protocol adapter; identity is supplied by the host."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal_id = host, principal_id

    def _visible(self, slot):
        visibility, _ = self.__host._principals[self.__principal_id]
        return slot["visibility"] == "agent" or visibility in ("debug", "evaluator")

    def _instance(self, instance_id):
        _, allowed = self.__host._principals[self.__principal_id]
        if not isinstance(instance_id, str) or (allowed is not None and instance_id not in allowed):
            return None
        return self.__host._instances.get(instance_id)

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": PROTOCOL_VERSION, "instance_id": instance_id if isinstance(instance_id, str) else None, "request_id": request_id,
            "operation_id": None, "status": status, "state_revision": inst.revision if inst else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": {} if result is None else result, "observations": {} if observations is None else observations, "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Unknown or inaccessible instance", "INVALID_ARGUMENT": "Arguments do not match supported schema or configured model bounds", "ACCESS_DENIED": "State is unavailable to this session", "UNKNOWN_CAPABILITY": "Unknown capability", "UNSUPPORTED": "Capability has no local implementation", "REQUEST_CONFLICT": "Request ID was already used with different content", "PRECONDITION_FAILED": "Required power or explicitly configured bath is unavailable", "DEVICE_ERROR": "Scene backend failed; external scene effects are unknown"}
        return self._envelope(instance_id, request_id if isinstance(request_id, str) else None, status=status, error={"code": code, "message": messages[code], "retryable": False, "side_effects": "unknown" if status == "failed" else "none", "known_progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != RV10_ASSET_TYPE_ID:
                return self._error(None, "INVALID_ARGUMENT")
            spec = export_rv10_contract_metadata()["asset_types"][RV10_ASSET_TYPE_ID]
            slots = {sid: slot for sid, slot in spec["state_slots"].items() if self._visible(slot)}
            caps = copy.deepcopy(spec["capabilities"])
            for cap in caps.values():
                cap["state_effects"] = [item for item in cap["state_effects"] if item["state_id"] in slots]
                cap["observes"] = [sid for sid in cap["observes"] if sid in slots]
            caps.update(_vacuum_templates("ika_rv10").capabilities(include_evaluator=self.__host._principals[self.__principal_id][0] in ("debug", "evaluator")))
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": RV10_ASSET_TYPE_ID, "asset_version": "1.1.0", "instance_id": instance_id, "catalog_id": "CHEM-024", "model": spec["model"], "adapter": "basic40_runtime_vacuum.RV10Session", "capabilities": caps, "state_slots": slots, "limitations": list(RV10_LIMITATIONS)}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            ids = [sid for sid, slot in RV10_STATE_SLOTS.items() if self._visible(slot)] if state_ids is None else state_ids
            if not isinstance(ids, list) or any(not isinstance(s, str) for s in ids) or len(set(ids)) != len(ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(sid not in RV10_STATE_SLOTS or not self._visible(RV10_STATE_SLOTS[sid]) for sid in ids):
                return self._error(instance_id, "ACCESS_DENIED")
            observations = {}
            integrals = ("evaporation.evaporated_mass_kg", "evaporation.uncondensed_mass_kg", "recovery.condensate_mass_kg", "recovery.indicated_condensate_mass_kg")
            for sid in ids:
                slot, value = RV10_STATE_SLOTS[sid], copy.deepcopy(inst.state[sid])
                quality = "unavailable" if value is None else "estimated" if slot["origin"] == "derived" else "valid"
                sample_time = self.__host.time_s
                if sid in ("vacuum.pressure_pa", "vacuum.indicated_pressure_pa") and value is not None:
                    sample_time = inst.vacuum_sample_time_s
                    if sample_time < self.__host.time_s:
                        quality = "stale"
                observation = {"value": value, "unit": slot["unit"], "quality": quality, "sample_time_s": sample_time, "sample_window_s": [inst.evaporation["configured_at_s"], self.__host.time_s] if sid in integrals and inst.evaporation else None}
                if value is None:
                    observation["reason"] = "Fluid density and volume model are unavailable" if sid == "recovery.condensate_volume_m3" else "No external source pressure snapshot" if sid.startswith("vacuum.") else "Trusted host has not configured the required engineering process parameters"
                elif quality == "stale":
                    observation["reason"] = "External pressure snapshot predates the current host time"
                observations[sid] = observation
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256 or not isinstance(capability_id, str) or not isinstance(arguments, dict):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = hashlib.sha256(json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            except (ValueError, TypeError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            request_key = (self.__principal_id, instance_id, request_id)
            if request_key in host._requests:
                previous, response = host._requests[request_key]
                return copy.deepcopy(response) if previous == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._invoke_checked(inst, capability_id, arguments, request_id)
            host._requests[request_key] = (digest, copy.deepcopy(response))
            host._logs[self.__principal_id].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id, "response": copy.deepcopy(response)})
            return response

    def _invoke_checked(self, inst, cap, args, request_id):
        host = self.__host
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request_id, status)
        templates = _vacuum_templates("ika_rv10")
        if cap in templates.CAPABILITY_IDS:  # 2026-10 template queries: read-only, no state change
            if cap in templates.EVALUATOR_CAPABILITIES:
                if self.__host._principals[self.__principal_id][0] not in ("debug", "evaluator"):
                    return fail("ACCESS_DENIED")
                if args != {}:
                    return fail("INVALID_ARGUMENT")
                return self._envelope(inst.instance_id, request_id, result=templates.report(copy.deepcopy(inst)))
            result, code = templates.template_read(args)
            if code == "UNKNOWN_REFERENCE":
                return self._envelope(inst.instance_id, request_id, status="rejected", error={"code": code, "message": "substance_id is not in the RV10 evaporation library", "retryable": False, "side_effects": "none", "known_progress": None})
            return fail(code) if code else self._envelope(inst.instance_id, request_id, result=result)
        if cap in ("vacuum.set_pressure", "timer.set_duration"):
            return fail("UNSUPPORTED", "unsupported")
        if cap not in RV10_CAPABILITY_ARGUMENTS:
            return fail("UNKNOWN_CAPABILITY")
        expected = RV10_CAPABILITY_ARGUMENTS[cap]
        if set(args) != set(expected):
            return fail("INVALID_ARGUMENT")
        for key, schema in expected.items():
            value = args[key]
            if schema["type"] == "boolean" and type(value) is not bool:
                return fail("INVALID_ARGUMENT")
            if schema["type"] == "number" and (not _number(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf) or value <= schema.get("exclusiveMinimum", -math.inf)):
                return fail("INVALID_ARGUMENT")
        if cap == "thermal.set_temperature" and args["temperature_k"] < host.ambient_temperature_k:
            return fail("INVALID_ARGUMENT")
        if cap in ("rotation.start", "thermal.start", "lift.set_position") and not inst.state["power.enabled"]:
            return fail("PRECONDITION_FAILED")
        if cap == "thermal.start" and inst.bath is None:
            return fail("PRECONDITION_FAILED")
        key, sid = RV10_CAPABILITY_RESULT_SLOT[cap]
        value = True if cap in ("rotation.start", "thermal.start") else False if cap in ("rotation.stop", "thermal.stop") else args[key]
        candidate = copy.deepcopy(inst)
        candidate.state[sid] = value
        if cap == "power.set" and not value:
            candidate.state["rotation.running"] = False
            candidate.state["thermal.enabled"] = False
        host._refresh(candidate)
        updates = {s: v for s, v in candidate.state.items() if v != inst.state[s]}
        if host.scene_adapter:
            try:
                host.scene_adapter(inst.instance_id, copy.deepcopy(updates))
            except Exception:
                return fail("DEVICE_ERROR", "failed")
        inst.state = candidate.state
        inst.revision += 1
        return self._envelope(inst.instance_id, request_id, result={key: value})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(state_id, str) or state_id not in RV10_STATE_SLOTS or not self._visible(RV10_STATE_SLOTS[state_id]):
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = RV10_STATE_SLOTS[state_id]["write_via"]
            if route is None:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return copy.deepcopy(self.__host._logs[self.__principal_id])


def validate_rv10_runtime(output=None):
    """Run only CHEM-024 appended tests; import this entrypoint (not CLI)."""
    import importlib.util
    import io
    import unittest
    test_file = Path(__file__).resolve().parents[1] / "tests" / "test_basic40_vacuum.py"
    spec = importlib.util.spec_from_file_location("test_basic40_vacuum_rv10", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(module.Basic40RV10Tests))
    recorded_at = datetime.now(timezone.utc).isoformat()
    environment = {"python": platform.python_version(), "platform": platform.platform()}
    status = "passed" if result.wasSuccessful() else "failed"
    method = "Deterministic unittest through real RV10Session describe/read/invoke/write and RV10Host.step; independent lift/rotation/thermal integration, empirical mass conservation, request atomicity and session access filtering"
    report = {"schema_version": "1.0.0", "catalog_id": "CHEM-024", "asset_type_id": RV10_ASSET_TYPE_ID, "recorded_at": recorded_at, "checked_at": recorded_at, "method": method, "environment": environment,
        "tests_run": result.testsRun, "result": status, "failures": len(result.failures), "errors": len(result.errors), "log": stream.getvalue(),
        "checks": [{"check_id": f"rv10_runtime_{layer}", "layer": layer, "result": status, "capability_ids": list(RV10_CAPABILITY_ARGUMENTS), "method": method, "environment": environment, "checked_at": recorded_at} for layer in ("process", "host")],
        "layers": {"process": status, "host": status, "scene": "not_run", "robot": "not_run", "hardware": "not_run"},
        "data_classification": "Synthetic fixtures only; no live task or experimental truth", "limitations": list(RV10_LIMITATIONS)}
    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _vacuum_templates(slug):
    """2026-10 lazy import of scripts/basic40_templates/<slug>.py (real-data template helpers)."""
    import importlib
    try:
        return importlib.import_module("basic40_templates." + slug)
    except ModuleNotFoundError:
        return importlib.import_module("scripts.basic40_templates." + slug)


# CHEM-025: namespaced, standalone externally driven Lyovapor model.
LYOVAPOR_ASSET_TYPE_ID = "instrument.buchi_lyovapor_l250_pro"
LYOVAPOR_MAX_ICE_KG = 5.0
LYOVAPOR_MAX_RATE_KG_S = 4.0 / 86400.0
LYOVAPOR_CONDENSER_FLOOR_K = 188.15
LYOVAPOR_SHELF_CEILING_K = 333.15
LYOVAPOR_PHASES = ("pre_freeze", "primary_drying", "secondary_drying")
LYOVAPOR_LIMITATIONS = [
    "Engineering-assumption lumped mass bookkeeping, not a calibrated freeze-drying process or manufacturer controller emulator.",
    "Samples are externally pre-frozen. Condenser cooling does not freeze the sample; no nucleation, heat-transfer, vapor resistance, collapse or sorption solver exists.",
    "Host supplies actual chamber pressure and temperatures with provenance. Controls only set targets; no internal pump or automatic thermal dynamics are instantiated.",
    "Documented operating pressure is 10..100 Pa; shared VACUUBRAND PC3001 floor 200 Pa cannot attain it and is rejected at start. A separate compatible external vacuum boundary is required.",
    "Optional host-supplied constant sublimation/desorption rates are engineering assumptions, bounded together by 4 kg/86400 s and by 5 kg condenser inventory; measured specimen calibration is still required.",
    "A timer-completed schedule does not certify dryness. A model_threshold_met endpoint refers solely to host-initialized synthetic water pools and supplied rates, not validated residual moisture or product quality.",
    "Manual lid repositioning is a simulation configuration with an assumed 0.16 m vertical lift; no powered lid actuator, robot grasp, collision or hardware control is implemented.",
    "Defrost is a trusted simulation bookkeeping operation after the host confirms empty sample position, isolated source, and warm condenser; no heater or drain transport is simulated.",
    "The API filters truth from discovery, read, results, logs, and scene callbacks; trusted Python memory and debug files are not process or filesystem isolated.",
    "Request deduplication is in memory per principal/instance and does not survive restart. Device commands are synchronous; cycle progression is observable host-managed state, not an asynchronous lab operation.",
]


def _lyo_num(unit, low=0, high=None, nullable=False):
    schema = {"type": "number", "minimum": low, "x-unit": unit}
    if high is not None:
        schema["maximum"] = high
    return {"anyOf": [schema, {"type": "null"}]} if nullable else schema


def _lyo_obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _lyo_slot(unit, origin, schema, meaning, visibility="agent", capability=None, parameter=None):
    return {**_slot(unit, origin, schema, visibility, capability, parameter), "meaning_zh": meaning}


LYOVAPOR_RECIPE_SCHEMA = _lyo_obj({p + "_s": _lyo_num("s", 0, 604800) for p in LYOVAPOR_PHASES})
LYOVAPOR_STATE_SLOTS = {
    "power.enabled": _lyo_slot("1", "setpoint", {"type": "boolean"}, "供电目标", capability="power.set", parameter="enabled"),
    "chamber.open": _lyo_slot("1", "scene", {"type": "boolean"}, "手动顶盖配置", capability="chamber.set_open", parameter="open"),
    "lid.lift_m": _lyo_slot("m", "scene", _lyo_num("m", 0, .16), "假设顶盖竖直抬升位置，不是厂家行程"),
    "sample.loaded": _lyo_slot("1", "derived", {"type": "boolean"}, "样品位占用，由宿主配置"),
    "sample.pre_frozen": _lyo_slot("1", "derived", {"anyOf": [{"type": "boolean"}, {"type": "null"}]}, "宿主确认外部预冻状态"),
    "vacuum.connected": _lyo_slot("1", "derived", {"type": "boolean"}, "外部真空源已关联"),
    "vacuum.compatible": _lyo_slot("1", "derived", {"type": "boolean"}, "源极限压力是否可达到当前目标，仅必要条件"),
    "vacuum.target_pressure_pa": _lyo_slot("Pa", "setpoint", _lyo_num("Pa", 10, 100), "腔体绝压目标，不直接改变压力", capability="vacuum.set_pressure", parameter="pressure_pa"),
    "vacuum.pressure_pa": _lyo_slot("Pa", "process_truth", _lyo_num("Pa", 0, 110000, True), "宿主压力边界", "debug"),
    "vacuum.indicated_pressure_pa": _lyo_slot("Pa", "derived", _lyo_num("Pa", 0, 110000, True), "宿主压力四舍五入至0.1Pa的合成观测"),
    "thermal.target_shelf_temperature_k": _lyo_slot("K", "setpoint", {"type": "number", "exclusiveMinimum": 0, "maximum": 333.15, "x-unit": "K"}, "可选搁板温度目标；不假设厂家搁板最低温度", capability="thermal.set_temperature", parameter="temperature_k"),
    "thermal.protection_temperature_k": _lyo_slot("K", "setpoint", {"type": "number", "exclusiveMinimum": 0, "maximum": 333.15, "x-unit": "K"}, "用户选定样品保护温度，不能替代塌陷温度标定", capability="thermal.set_protection_temperature", parameter="temperature_k"),
    "thermal.shelf_temperature_k": _lyo_slot("K", "process_truth", _lyo_num("K", 100, 333.15, True), "宿主实际搁板温度", "debug"),
    "thermal.sample_temperature_k": _lyo_slot("K", "process_truth", _lyo_num("K", 100, 400, True), "宿主实际样品温度", "debug"),
    "thermal.indicated_shelf_temperature_k": _lyo_slot("K", "derived", _lyo_num("K", 100, 333.15, True), "搁板温度0.1摄氏度量化并转换为K的合成观测，不是厂家精度"),
    "thermal.indicated_sample_temperature_k": _lyo_slot("K", "derived", _lyo_num("K", 100, 400, True), "样品温度0.1摄氏度量化并转换为K的合成观测，不是厂家精度"),
    "condenser.temperature_k": _lyo_slot("K", "process_truth", _lyo_num("K", 188.15, 400, True), "宿主冷凝器温度，最低188.15K", "debug"),
    "condenser.indicated_temperature_k": _lyo_slot("K", "derived", _lyo_num("K", 188.15, 400, True), "冷凝器温度0.1摄氏度量化并转换为K的合成观测"),
    "condenser.ice_mass_kg": _lyo_slot("kg", "process_truth", _lyo_num("kg", 0, 5), "本次结冰库存，不含已排出融水", "debug"),
    "condenser.drained_mass_kg": _lyo_slot("kg", "process_truth", _lyo_num("kg"), "仿真除霜累计转移量", "debug"),
    "sample.ice_mass_kg": _lyo_slot("kg", "process_truth", _lyo_num("kg", 0, None, True), "未标定样品自由冰质量", "debug"),
    "sample.bound_water_mass_kg": _lyo_slot("kg", "process_truth", _lyo_num("kg", 0, None, True), "未标定样品结合水质量", "debug"),
    "sample.dry_mass_kg": _lyo_slot("kg", "process_truth", _lyo_num("kg", 0, None, True), "宿主初始化干物质量", "debug"),
    "sample.collapse_temperature_k": _lyo_slot("K", "process_truth", _lyo_num("K", 100, 400, True), "样品塌陷阈值未知，除非宿主提供", "debug"),
    "cycle.phase": _lyo_slot("1", "derived", {"type": "string", "enum": ["idle", *LYOVAPOR_PHASES, "complete", "stopped", "aborted"]}, "周期阶段"),
    "cycle.running": _lyo_slot("1", "derived", {"type": "boolean"}, "宿主时间推进是否启用"),
    "cycle.paused": _lyo_slot("1", "derived", {"type": "boolean"}, "周期暂停标志"),
    "cycle.phase_elapsed_s": _lyo_slot("s", "derived", _lyo_num("s"), "当前阶段有效门控时间"),
    "cycle.elapsed_s": _lyo_slot("s", "derived", _lyo_num("s"), "累计有效阶段时间，暂停/等待不计入"),
    "cycle.recipe": _lyo_slot("1", "setpoint", {"anyOf": [LYOVAPOR_RECIPE_SCHEMA, {"type": "null"}]}, "三个阶段秒数，未设置时null", capability="lyophilization.set_recipe", parameter="recipe"),
    "cycle.endpoint_mode": _lyo_slot("1", "setpoint", {"type": "string", "enum": ["schedule_only", "model_moisture"]}, "终点条件模式"),
    "cycle.moisture_threshold": _lyo_slot("1", "setpoint", _lyo_num("1", 0, 1), "模型湿基残水质量分数阈值，不是产品验证限"),
    "cycle.endpoint_status": _lyo_slot("1", "derived", {"type": "string", "enum": ["pending", "schedule_complete_unverified", "model_threshold_met", "model_threshold_not_met", "aborted", "stopped"]}, "明确区分计划结束与模型终点"),
    "cycle.wait_reason": _lyo_slot("1", "derived", {"type": "string", "enum": ["none", "missing_boundary", "pressure", "temperature", "condenser_full"]}, "运行门控的公开状态，不包含样品真值"),
}
LYOVAPOR_ARGUMENTS = {
    "power.set": {"enabled": {"type": "boolean"}},
    "chamber.set_open": {"open": {"type": "boolean"}},
    "vacuum.set_pressure": {"pressure_pa": _lyo_num("Pa", 10, 100)},
    "thermal.set_temperature": {"temperature_k": {"type": "number", "exclusiveMinimum": 0, "maximum": 333.15, "x-unit": "K"}},
    "thermal.set_protection_temperature": {"temperature_k": {"type": "number", "exclusiveMinimum": 0, "maximum": 333.15, "x-unit": "K"}},
    "lyophilization.set_recipe": {"recipe": LYOVAPOR_RECIPE_SCHEMA},
    "lyophilization.set_endpoint": {"mode": {"type": "string", "enum": ["schedule_only", "model_moisture"]}, "moisture_threshold": _lyo_num("1", 0, 1)},
    **{cap: {} for cap in ("lyophilization.start", "lyophilization.pause", "lyophilization.resume", "lyophilization.stop", "lyophilization.abort", "condenser.defrost")},
}
LYOVAPOR_CAPABILITY_SUMMARIES = {
    "power.set": "设置供电；断电中止当前周期并保留已发生质量转移",
    "chamber.set_open": "宿主配置手动顶盖开闭；运行期间与未回到环境压时拒绝开启",
    "vacuum.set_pressure": "设置10–100Pa绝压目标；不驱动真空源",
    "thermal.set_temperature": "设置可选搁板温度目标，不伪造升温过程",
    "thermal.set_protection_temperature": "设置样品保护阈值，不等同样品塌陷标定",
    "lyophilization.set_recipe": "配置外部预冻确认、一次干燥和二次干燥阶段时长",
    "lyophilization.set_endpoint": "选择计划或假设残水模型终点",
    "lyophilization.start": "验证外部预冻、真空兼容性和配置后启动周期状态机",
    "lyophilization.pause": "暂停有效阶段时间和后续水分转移，保留部分效果",
    "lyophilization.resume": "复核前提后恢复暂停周期",
    "lyophilization.stop": "结束当前周期且不确认干燥终点，保留部分效果",
    "lyophilization.abort": "中止周期，保留已发生的质量转移",
    "condenser.defrost": "已空载、断开真空且宿主报告冷凝器温暖时，仿真转移结冰库存至排水计数",
}


def export_lyovapor_contract_metadata():
    """Return type metadata only; never serialize a running sample's private state."""
    slots = copy.deepcopy(LYOVAPOR_STATE_SLOTS)
    for sid, slot in slots.items():
        slot["read_binding"] = f"LyovaporSession.read(instance_id=..., state_ids=['{sid}'])"
        slot["sampling"] = {"mode": "instant", "time_domain": "simulation", "window": None}
        slot["reference_conditions"] = "Absolute pressure" if "pressure_pa" in sid else "Kelvin" if "temperature_k" in sid else "Local +Z, closed 0m; assumed manual lift .16m" if sid == "lid.lift_m" else None
    examples = {"power.set": {"enabled": True}, "chamber.set_open": {"open": False}, "vacuum.set_pressure": {"pressure_pa": 50.0},
        "thermal.set_temperature": {"temperature_k": 253.15}, "thermal.set_protection_temperature": {"temperature_k": 273.15},
        "lyophilization.set_recipe": {"recipe": {p + "_s": 60.0 for p in LYOVAPOR_PHASES}},
        "lyophilization.set_endpoint": {"mode": "schedule_only", "moisture_threshold": .01}}
    caps = {}
    for cap, arguments in LYOVAPOR_ARGUMENTS.items():
        affected = {
            "power.set": ["power.enabled", "cycle.running", "cycle.paused", "cycle.phase", "cycle.endpoint_status"],
            "chamber.set_open": ["chamber.open", "lid.lift_m"],
            "vacuum.set_pressure": ["vacuum.target_pressure_pa", "vacuum.compatible"],
            "thermal.set_temperature": ["thermal.target_shelf_temperature_k"],
            "thermal.set_protection_temperature": ["thermal.protection_temperature_k"],
            "lyophilization.set_recipe": ["cycle.recipe"],
            "lyophilization.set_endpoint": ["cycle.endpoint_mode", "cycle.moisture_threshold"],
            "condenser.defrost": ["condenser.ice_mass_kg", "condenser.drained_mass_kg"],
        }.get(cap, ["cycle.phase", "cycle.running", "cycle.paused", "cycle.phase_elapsed_s", "cycle.elapsed_s", "cycle.endpoint_status", "cycle.wait_reason"])
        effects = [{"state_id": sid, "timing": "immediate", "description_zh": LYOVAPOR_CAPABILITY_SUMMARIES[cap]} for sid in affected]
        if cap in ("lyophilization.start", "lyophilization.resume"):
            effects += [{"state_id": sid, "timing": "on_step", "description_zh": "仅宿主推进，受外部边界和显式假设速率门控"} for sid in ("sample.ice_mass_kg", "sample.bound_water_mass_kg", "condenser.ice_mass_kg", "cycle.phase", "cycle.phase_elapsed_s", "cycle.elapsed_s", "cycle.endpoint_status", "cycle.wait_reason", "cycle.running")]
        caps[cap] = {"availability": "implemented", "visibility": "agent", "kind": "simulation" if cap in ("chamber.set_open", "condenser.defrost") else "configuration" if cap in ("lyophilization.set_recipe", "lyophilization.set_endpoint") else "device_command",
            "description_zh": LYOVAPOR_CAPABILITY_SUMMARIES[cap], "input_schema": _lyo_obj(copy.deepcopy(arguments)),
            "output_schema": _lyo_obj({"applied": {"type": "boolean"}}), "state_effects": effects, "observes": [],
            "example_arguments": [copy.deepcopy(examples.get(cap, {}))], "binding": f"basic40_runtime_vacuum.LyovaporSession.invoke(instance_id=..., capability_id='{cap}', arguments=..., request_id=...)"}
    parameters = {
        "initial_control_defaults": {"value": {"pressure_pa": 50.0, "shelf_temperature_k": 253.15, "protection_temperature_k": 273.15, "moisture_threshold": .01}, "unit": "1", "evidence": "engineering_assumption", "source": "Synthetic initial controls; pressure Pa, temperatures K, threshold wet-basis dimensionless; no manufacturer default claim"},
        "lid_open_pressure_threshold_pa": {"value": 90000, "unit": "Pa", "evidence": "engineering_assumption", "source": "Simplified near-ambient manual-opening gate; not a validated manufacturer interlock"},
        "base_dimensions_m": {"value": [.503, .645, .510], "unit": "m", "evidence": "catalog", "source": "CHEM-025 base only, width/depth/height"},
        "base_mass_kg": {"value": 67.4, "unit": "kg", "evidence": "catalog", "source": "CHEM-025 base only"},
        "maximum_condenser_ice_kg": {"value": 5, "unit": "kg", "evidence": "catalog", "source": "CHEM-025 / BUCHI technical data"},
        "maximum_condensation_rate_kg_s": {"value": LYOVAPOR_MAX_RATE_KG_S, "unit": "kg/s", "evidence": "catalog", "source": "4kg /24h upper bound, not a specimen-specific drying rate"},
        "condenser_minimum_k": {"value": 188.15, "unit": "K", "evidence": "catalog", "source": "-85degC"},
        "shelf_maximum_k": {"value": 333.15, "unit": "K", "evidence": "catalog", "source": "+60 +/-1degC; runtime does not emulate +/-1K accuracy"},
        "nominal_vacuum_range_pa": {"value": [10, 100], "unit": "Pa", "evidence": "documented", "source": "BUCHI May2026 manual pp18-19, 0.1..1mbar"},
        "lowest_system_pressure_pa": {"value": 3, "unit": "Pa", "evidence": "documented", "source": "BUCHI May2026 manual pp18-19; requires compatible external source"},
        **{key: {"value": None, "unit": unit, "evidence": "unknown", "source": "Requires specimen experiment or explicit trusted host engineering assumption"} for key, unit in (("sublimation_rate", "kg/s"), ("desorption_rate", "kg/s"), ("collapse_temperature", "K"), ("vapor_resistance", "1"), ("heat_transfer_coefficient", "W/(m^2*K)"))},
    }
    return {"asset_types": {LYOVAPOR_ASSET_TYPE_ID: {"catalog_id": "CHEM-025", "model": "BUCHI Lyovapor L-250 Pro", "asset_version": "1.1.0", "parameters": parameters, "state_slots": slots, "capabilities": caps}},
        "host_capabilities": LyovaporHost.describe_host(), "limitations": list(LYOVAPOR_LIMITATIONS)}


@dataclass
class _LyovaporInstance:
    instance_id: str
    state: dict
    revision: int = 0
    source: dict | None = None
    sample_evidence: str | None = None
    boundary_evidence: str | None = None
    kinetics: dict | None = None
    template: dict | None = None  # 2026-10 optional library product (Tg'/Tc/Te), see basic40_templates/buchi_lyovapor_l250.py


class LyovaporSceneSynchronizationError(RuntimeError):
    """Process state is committed; external scene synchronization may be partial."""


class LyovaporHost:
    """Trusted owner of a shared clock; external pressure/thermal boundaries only."""
    def __init__(self, *, scene_adapter=None):
        self.scene_adapter = scene_adapter
        self.time_s = 0.0
        self._instances, self._principals, self._requests, self._logs = {}, {}, {}, {}
        self._lock = threading.RLock()
        self._scene_faults = []

    @staticmethod
    def describe_host():
        return {"sim.step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "basic40_runtime_vacuum.LyovaporHost.step(dt_s)", "scope": "Shared clock for explicitly registered lyovapor instances; embedding world owns coordinated external source steps", "input_schema": _lyo_obj({"dt_s": _lyo_num("s", 0, 86400)})},
            **{f"host.{name}": {"kind": "configuration", "visibility": "trusted_host_only", "binding": f"basic40_runtime_vacuum.LyovaporHost.{name}"} for name in ("initialize_sample", "unload_sample", "connect_vacuum", "disconnect_vacuum", "set_boundary", "configure_kinetics", "sync_scene")}}

    def create_instance(self, instance_id, asset_type_id=LYOVAPOR_ASSET_TYPE_ID):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != LYOVAPOR_ASSET_TYPE_ID:
                raise ValueError("Unique instance and supported asset type required")
            s = {sid: None for sid in LYOVAPOR_STATE_SLOTS}
            s.update({"power.enabled": False, "chamber.open": False, "lid.lift_m": 0.0, "sample.loaded": False, "vacuum.connected": False, "vacuum.compatible": False,
                "vacuum.target_pressure_pa": 50.0, "thermal.target_shelf_temperature_k": 253.15, "thermal.protection_temperature_k": 273.15,
                "condenser.ice_mass_kg": 0.0, "condenser.drained_mass_kg": 0.0, "cycle.phase": "idle", "cycle.running": False, "cycle.paused": False,
                "cycle.phase_elapsed_s": 0.0, "cycle.elapsed_s": 0.0, "cycle.endpoint_mode": "schedule_only", "cycle.moisture_threshold": .01,
                "cycle.endpoint_status": "pending", "cycle.wait_reason": "none"})
            inst = _LyovaporInstance(instance_id, s)
            self._emit(inst)
            self._instances[instance_id] = inst

    def _emit(self, inst):
        if self.scene_adapter:
            # Scene adapters are public observation consumers, never truth sinks.
            public = {sid: copy.deepcopy(v) for sid, v in inst.state.items() if LYOVAPOR_STATE_SLOTS[sid]["visibility"] == "agent"}
            self.scene_adapter(inst.instance_id, public)

    @staticmethod
    def _refresh(inst):
        s = inst.state
        s["lid.lift_m"] = .16 if s["chamber.open"] else 0.0
        s["vacuum.connected"] = inst.source is not None
        s["vacuum.compatible"] = bool(inst.source and inst.source["minimum_pressure_pa"] <= s["vacuum.target_pressure_pa"])
        for truth, observation in (("vacuum.pressure_pa", "vacuum.indicated_pressure_pa"), ("thermal.shelf_temperature_k", "thermal.indicated_shelf_temperature_k"), ("thermal.sample_temperature_k", "thermal.indicated_sample_temperature_k"), ("condenser.temperature_k", "condenser.indicated_temperature_k")):
            if s[truth] is None:
                s[observation] = None
            elif "temperature_k" in truth:
                # Assumed 0.1 degC display, retained in Kelvin at the API boundary.
                estimate = round(s[truth] - 273.15, 1) + 273.15
                envelope = LYOVAPOR_STATE_SLOTS[observation]["value_schema"]["anyOf"][0]
                s[observation] = max(envelope["minimum"], min(envelope["maximum"], estimate))
            else:
                s[observation] = round(s[truth], 1)

    def _commit_host(self, candidate):
        self._refresh(candidate)
        candidate.revision += 1
        self._instances[candidate.instance_id] = candidate
        try:
            self._emit(candidate)
        except Exception:
            self._scene_faults.append({"instance_id": candidate.instance_id, "time_s": self.time_s})
            raise LyovaporSceneSynchronizationError("Host state committed; scene effects unknown") from None

    def _idle_copy(self, instance_id):
        inst = self._instances[instance_id]
        if inst.state["cycle.running"] or inst.state["cycle.paused"]:
            raise ValueError("Stop the active cycle before changing host configuration")
        return copy.deepcopy(inst)

    @staticmethod
    def _evidence(evidence):
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("Explicit evidence or engineering-assumption provenance required")

    def initialize_sample(self, instance_id, *, dry_mass_kg, ice_mass_kg, bound_water_mass_kg, pre_frozen, evidence, collapse_temperature_k=None):
        with self._lock:
            self._evidence(evidence)
            masses = (dry_mass_kg, ice_mass_kg, bound_water_mass_kg)
            if any(not _number(x) or x < 0 for x in masses) or sum(masses) <= 0 or type(pre_frozen) is not bool:
                raise ValueError("Finite nonnegative sample masses, nonempty load and explicit frozen confirmation required")
            if collapse_temperature_k is not None and (not _number(collapse_temperature_k) or not 100 <= collapse_temperature_k <= 400):
                raise ValueError("Collapse threshold outside assumed model envelope")
            inst = self._idle_copy(instance_id)
            if inst.state["sample.loaded"] or not inst.state["chamber.open"]:
                raise ValueError("Open empty sample position before initialization")
            inst.state.update({"sample.dry_mass_kg": float(dry_mass_kg), "sample.ice_mass_kg": float(ice_mass_kg), "sample.bound_water_mass_kg": float(bound_water_mass_kg), "sample.pre_frozen": pre_frozen, "sample.loaded": True, "sample.collapse_temperature_k": collapse_temperature_k,
                "cycle.phase": "idle", "cycle.endpoint_status": "pending", "cycle.phase_elapsed_s": 0.0, "cycle.elapsed_s": 0.0, "cycle.wait_reason": "none"})
            inst.sample_evidence = evidence
            inst.kinetics = None
            inst.template = None
            self._commit_host(inst)

    def initialize_template_sample(self, instance_id, *, formulation_id, dry_mass_kg, ice_mass_kg, bound_water_mass_kg, pre_frozen, evidence):
        """2026-10 optional template path: product linked to a library formulation (critical temperature, real ice vapour pressure)."""
        return _vacuum_templates("buchi_lyovapor_l250").initialize_sample(self, instance_id, formulation_id=formulation_id, dry_mass_kg=dry_mass_kg,
            ice_mass_kg=ice_mass_kg, bound_water_mass_kg=bound_water_mass_kg, pre_frozen=pre_frozen, evidence=evidence)

    def template_drying_report(self, instance_id):
        """Trusted evaluator view: formulation, margin to its critical temperature and Murphy-Koop ice vapour pressures."""
        with self._lock:
            return _vacuum_templates("buchi_lyovapor_l250").report(copy.deepcopy(self._instances[instance_id]))

    def unload_sample(self, instance_id):
        """Trusted world material transfer: return remaining pools only to the host."""
        with self._lock:
            inst = self._idle_copy(instance_id)
            if not inst.state["chamber.open"] or not inst.state["sample.loaded"]:
                raise ValueError("Open loaded sample position required")
            remaining = {sid: inst.state[sid] for sid in ("sample.dry_mass_kg", "sample.ice_mass_kg", "sample.bound_water_mass_kg")}
            for sid in ("sample.dry_mass_kg", "sample.ice_mass_kg", "sample.bound_water_mass_kg", "sample.pre_frozen", "sample.collapse_temperature_k"):
                inst.state[sid] = None
            inst.state["sample.loaded"] = False
            inst.kinetics, inst.sample_evidence = None, None
            inst.template = None
            self._commit_host(inst)
            return remaining

    def connect_vacuum(self, instance_id, *, source_instance_id, asset_type_id=ASSET_TYPE_ID, minimum_pressure_pa=200.0, evidence):
        with self._lock:
            self._evidence(evidence)
            if not isinstance(source_instance_id, str) or not source_instance_id or not isinstance(asset_type_id, str) or not asset_type_id:
                raise ValueError("External source instance and type identifiers required")
            if not _number(minimum_pressure_pa) or not 3 <= minimum_pressure_pa <= 110000 or (asset_type_id == ASSET_TYPE_ID and minimum_pressure_pa < 200):
                raise ValueError("External source floor must respect known 200Pa shared-source and 3Pa system limits")
            inst = self._idle_copy(instance_id)
            inst.source = {"instance_id": source_instance_id, "asset_type_id": asset_type_id, "minimum_pressure_pa": float(minimum_pressure_pa), "evidence": evidence}
            inst.state["vacuum.pressure_pa"] = None
            self._commit_host(inst)

    def disconnect_vacuum(self, instance_id):
        with self._lock:
            inst = self._idle_copy(instance_id)
            inst.source = None
            # Disconnecting is not venting: retain actual pressure boundary.
            self._commit_host(inst)

    def set_boundary(self, instance_id, *, pressure_pa, shelf_temperature_k, sample_temperature_k, condenser_temperature_k, evidence):
        with self._lock:
            self._evidence(evidence)
            inst = copy.deepcopy(self._instances[instance_id])
            for value, low, high in ((pressure_pa, 0, 110000), (shelf_temperature_k, 100, 333.15), (sample_temperature_k, 100, 400), (condenser_temperature_k, 188.15, 400)):
                if not _number(value) or not low <= value <= high:
                    raise ValueError("Actual boundary outside documented limit or explicit model envelope")
            if inst.source and pressure_pa < inst.source["minimum_pressure_pa"]:
                raise ValueError("Pressure cannot be below the declared external source floor")
            inst.state.update({"vacuum.pressure_pa": float(pressure_pa), "thermal.shelf_temperature_k": float(shelf_temperature_k), "thermal.sample_temperature_k": float(sample_temperature_k), "condenser.temperature_k": float(condenser_temperature_k)})
            inst.boundary_evidence = evidence
            self._commit_host(inst)

    def configure_kinetics(self, instance_id, *, sublimation_rate_kg_s, desorption_rate_kg_s, evidence):
        with self._lock:
            self._evidence(evidence)
            if any(not _number(x) or not 0 <= x <= LYOVAPOR_MAX_RATE_KG_S for x in (sublimation_rate_kg_s, desorption_rate_kg_s)) or sublimation_rate_kg_s + desorption_rate_kg_s > LYOVAPOR_MAX_RATE_KG_S:
                raise ValueError("Explicit rates must sum to no more than 4kg/24h")
            inst = self._idle_copy(instance_id)
            if not inst.state["sample.loaded"]:
                raise ValueError("Initialize a sample before configuring its kinetics")
            inst.kinetics = {"sublimation_rate_kg_s": float(sublimation_rate_kg_s), "desorption_rate_kg_s": float(desorption_rate_kg_s), "evidence": evidence, "classification": "engineering_assumption"}
            self._commit_host(inst)

    def register_principal(self, principal_id, *, visibility="agent", instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Unique trusted identity required")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set)) or any(i not in self._instances for i in instance_ids)):
                raise ValueError("Instance grants must refer to registered instruments")
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))
            self._logs[principal_id] = []

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Identity must be established by the trusted host")
        return LyovaporSession(self, principal_id)

    @staticmethod
    def _start_ready(inst):
        s = inst.state
        return s["power.enabled"] and not s["chamber.open"] and s["sample.loaded"] and s["sample.pre_frozen"] is True and s["cycle.recipe"] is not None and s["vacuum.compatible"] and (s["cycle.endpoint_mode"] != "model_moisture" or inst.kinetics is not None)

    @staticmethod
    def _gate(inst):
        s = inst.state
        if any(s[sid] is None for sid in ("vacuum.pressure_pa", "thermal.shelf_temperature_k", "thermal.sample_temperature_k", "condenser.temperature_k")):
            return "missing_boundary"
        protection = s["thermal.protection_temperature_k"]
        if s["sample.collapse_temperature_k"] is not None:
            protection = min(protection, s["sample.collapse_temperature_k"])
        if s["thermal.sample_temperature_k"] > protection or s["thermal.shelf_temperature_k"] > s["thermal.protection_temperature_k"]:
            return "temperature"
        if s["cycle.phase"] != "pre_freeze":
            if s["vacuum.pressure_pa"] > s["vacuum.target_pressure_pa"]:
                return "pressure"
            template_wait = _vacuum_templates("buchi_lyovapor_l250").template_gate(inst) if inst.template else None
            if template_wait:  # 2026-10 template product: critical temperature / real ice vapour pressure
                return template_wait
            if s["condenser.temperature_k"] >= s["thermal.sample_temperature_k"]:
                return "temperature"
            if s["condenser.ice_mass_kg"] >= LYOVAPOR_MAX_ICE_KG - 1e-12:
                return "condenser_full"
        return "none"

    def _advance(self, inst, dt):
        s = inst.state
        while dt > 1e-12 and s["cycle.running"]:
            gate = self._gate(inst)
            s["cycle.wait_reason"] = gate
            if gate != "none":
                break
            phase = s["cycle.phase"]
            duration = s["cycle.recipe"][phase + "_s"]
            h = min(dt, max(0.0, duration - s["cycle.phase_elapsed_s"]))
            if inst.kinetics and phase != "pre_freeze":
                sid, rate_id = ("sample.ice_mass_kg", "sublimation_rate_kg_s") if phase == "primary_drying" else ("sample.bound_water_mass_kg", "desorption_rate_kg_s")
                # Secondary bound-water model begins only after free ice is exhausted.
                rate = inst.kinetics[rate_id] if phase == "primary_drying" or s["sample.ice_mass_kg"] <= 1e-12 else 0.0
                if rate > 0 and s[sid] > LYOVAPOR_MAX_ICE_KG - s["condenser.ice_mass_kg"]:
                    h = min(h, (LYOVAPOR_MAX_ICE_KG - s["condenser.ice_mass_kg"]) / rate)
                amount = min(s[sid], rate * h, LYOVAPOR_MAX_ICE_KG - s["condenser.ice_mass_kg"])
                s[sid] = max(0.0, s[sid] - amount)
                s["condenser.ice_mass_kg"] = min(LYOVAPOR_MAX_ICE_KG, s["condenser.ice_mass_kg"] + amount)
            s["cycle.phase_elapsed_s"] += h
            s["cycle.elapsed_s"] += h
            dt -= h
            if s["cycle.phase_elapsed_s"] + 1e-10 >= duration:
                index = LYOVAPOR_PHASES.index(phase)
                if index < 2:
                    s["cycle.phase"] = LYOVAPOR_PHASES[index + 1]
                    s["cycle.phase_elapsed_s"] = 0.0
                else:
                    s["cycle.phase"], s["cycle.running"] = "complete", False
                    if s["cycle.endpoint_mode"] == "schedule_only":
                        s["cycle.endpoint_status"] = "schedule_complete_unverified"
                    else:
                        remaining = s["sample.ice_mass_kg"] + s["sample.bound_water_mass_kg"]
                        total = remaining + s["sample.dry_mass_kg"]
                        fraction = remaining / total if total else 0.0
                        s["cycle.endpoint_status"] = "model_threshold_met" if fraction <= s["cycle.moisture_threshold"] else "model_threshold_not_met"
            elif h == 0:
                break

    def step(self, dt_s):
        with self._lock:
            if not _number(dt_s) or not 0 <= dt_s <= 86400:
                raise ValueError("Host time increment must be finite 0..86400 seconds")
            if dt_s == 0:
                return
            candidates = copy.deepcopy(self._instances)
            for inst in candidates.values():
                self._advance(inst, float(dt_s))
                self._refresh(inst)
                inst.revision += 1
            self.time_s += float(dt_s)
            self._instances = candidates
            failed = False
            for inst in candidates.values():
                try:
                    self._emit(inst)
                except Exception:
                    failed = True
                    self._scene_faults.append({"instance_id": inst.instance_id, "time_s": self.time_s})
            if failed:
                raise LyovaporSceneSynchronizationError("Shared time/process committed; scene effects unknown")

    def sync_scene(self, instance_id):
        with self._lock:
            self._emit(self._instances[instance_id])

    def debug_snapshot(self):
        with self._lock:
            return copy.deepcopy({"time_s": self.time_s, "instances": {i: vars(x) for i, x in self._instances.items()}, "scene_faults": self._scene_faults})


class LyovaporSession:
    """Filtered lab entrypoints; caller cannot choose or promote its own role."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal_id = host, principal_id

    def _visible(self, info):
        return info["visibility"] == "agent" or self.__host._principals[self.__principal_id][0] in ("debug", "evaluator")

    def _instance(self, instance_id):
        _, allowed = self.__host._principals[self.__principal_id]
        if not isinstance(instance_id, str) or (allowed is not None and instance_id not in allowed):
            return None
        return self.__host._instances.get(instance_id)

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": PROTOCOL_VERSION, "instance_id": instance_id if isinstance(instance_id, str) else None, "request_id": request_id if isinstance(request_id, str) else None,
            "operation_id": None, "status": status, "state_revision": None if inst is None else inst.revision, "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": {} if result is None else result, "observations": {} if observations is None else observations, "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Unknown or inaccessible instance", "UNKNOWN_CAPABILITY": "Unknown capability", "ACCESS_DENIED": "State inaccessible or not writable", "INVALID_ARGUMENT": "Arguments do not match supported schema", "PRECONDITION_FAILED": "External boundary, sample or configuration prerequisites are not satisfied", "BUSY": "Operation requires an idle or paused cycle", "REQUEST_CONFLICT": "Request ID already used for different content", "DEVICE_ERROR": "Scene callback failed; external effects unknown"}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": messages[code], "retryable": False, "side_effects": "unknown" if status == "failed" else "none", "known_progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None) or asset_type_id not in (None, LYOVAPOR_ASSET_TYPE_ID):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            spec = export_lyovapor_contract_metadata()["asset_types"][LYOVAPOR_ASSET_TYPE_ID]
            visible = {sid: slot for sid, slot in spec["state_slots"].items() if self._visible(slot)}
            for cap in spec["capabilities"].values():
                cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in visible]
                cap["observes"] = [sid for sid in cap["observes"] if sid in visible]
            spec["capabilities"].update(_vacuum_templates("buchi_lyovapor_l250").capabilities(include_evaluator=self.__host._principals[self.__principal_id][0] in ("debug", "evaluator")))
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": LYOVAPOR_ASSET_TYPE_ID, "asset_version": "1.1.0", "instance_id": instance_id, "catalog_id": "CHEM-025", "model": spec["model"], "adapter": "basic40_runtime_vacuum.LyovaporSession", "state_slots": visible, "capabilities": spec["capabilities"], "limitations": list(LYOVAPOR_LIMITATIONS)}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            ids = [sid for sid, info in LYOVAPOR_STATE_SLOTS.items() if self._visible(info)] if state_ids is None else state_ids
            if not isinstance(ids, list) or any(not isinstance(sid, str) for sid in ids) or len(set(ids)) != len(ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(sid not in LYOVAPOR_STATE_SLOTS or not self._visible(LYOVAPOR_STATE_SLOTS[sid]) for sid in ids):
                return self._error(instance_id, "ACCESS_DENIED")
            obs = {}
            for sid in ids:
                info, value = LYOVAPOR_STATE_SLOTS[sid], copy.deepcopy(inst.state[sid])
                obs[sid] = {"value": value, "unit": info["unit"], "quality": "unavailable" if value is None else "estimated" if info["origin"] == "derived" else "valid", "sample_time_s": self.__host.time_s, "sample_window_s": None}
                if value is None:
                    obs[sid]["reason"] = "Host sample, boundary or configuration value has not been initialized"
            return self._envelope(instance_id, observations=obs)

    @staticmethod
    def _valid(value, schema):
        if "anyOf" in schema:
            return any(LyovaporSession._valid(value, child) for child in schema["anyOf"])
        kind = schema.get("type")
        if kind == "object":
            return isinstance(value, dict) and set(value) == set(schema["properties"]) and all(LyovaporSession._valid(value[k], s) for k, s in schema["properties"].items())
        if kind == "number":
            return _number(value) and schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf) and value > schema.get("exclusiveMinimum", -math.inf)
        if kind == "boolean":
            return type(value) is bool
        if kind == "string":
            return isinstance(value, str) and value in schema.get("enum", [value])
        return kind == "null" and value is None

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256 or not isinstance(capability_id, str) or not isinstance(arguments, dict):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = hashlib.sha256(json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            except (ValueError, TypeError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = (self.__principal_id, instance_id, request_id)
            if key in host._requests:
                prior, response = host._requests[key]
                return copy.deepcopy(response) if prior == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._apply(inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, copy.deepcopy(response))
            # Do not store raw untrusted arguments or private state names in public logs.
            host._logs[self.__principal_id].append(copy.deepcopy(response))
            return response

    def _apply(self, inst, cap, args, request_id):
        host = self.__host
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request_id, status)
        templates = _vacuum_templates("buchi_lyovapor_l250")
        if cap in templates.CAPABILITY_IDS:  # 2026-10 template queries: read-only, no state change
            if cap in templates.EVALUATOR_CAPABILITIES:
                if host._principals[self.__principal_id][0] not in ("debug", "evaluator"):
                    return fail("ACCESS_DENIED")
                if args != {}:
                    return fail("INVALID_ARGUMENT")
                return self._envelope(inst.instance_id, request_id, result=templates.report(copy.deepcopy(inst)))
            result, code = templates.template_read(args)
            if code == "UNKNOWN_REFERENCE":
                return self._envelope(inst.instance_id, request_id, status="rejected", error={"code": code, "message": "substance_id is not in the Lyovapor formulation library", "retryable": False, "side_effects": "none", "known_progress": None})
            return fail(code) if code else self._envelope(inst.instance_id, request_id, result=result)
        if cap not in LYOVAPOR_ARGUMENTS:
            return fail("UNKNOWN_CAPABILITY")
        if not self._valid(args, _lyo_obj(LYOVAPOR_ARGUMENTS[cap])):
            return fail("INVALID_ARGUMENT")
        candidate = copy.deepcopy(inst)
        s = candidate.state
        active = s["cycle.running"] or s["cycle.paused"]
        routes = {"power.set": ("power.enabled", "enabled"), "chamber.set_open": ("chamber.open", "open"), "vacuum.set_pressure": ("vacuum.target_pressure_pa", "pressure_pa"), "thermal.set_temperature": ("thermal.target_shelf_temperature_k", "temperature_k"), "thermal.set_protection_temperature": ("thermal.protection_temperature_k", "temperature_k"), "lyophilization.set_recipe": ("cycle.recipe", "recipe")}
        if cap in routes:
            if active and cap != "power.set":
                return fail("BUSY")
            if cap == "chamber.set_open" and args["open"] and ((candidate.source is not None and s["vacuum.pressure_pa"] is None) or (s["vacuum.pressure_pa"] is not None and s["vacuum.pressure_pa"] < 90000)):
                return fail("PRECONDITION_FAILED")
            sid, param = routes[cap]
            s[sid] = copy.deepcopy(args[param])
            if cap == "power.set" and not args["enabled"] and active:
                s.update({"cycle.running": False, "cycle.paused": False, "cycle.phase": "aborted", "cycle.endpoint_status": "aborted"})
        elif cap == "lyophilization.set_endpoint":
            if active:
                return fail("BUSY")
            s.update({"cycle.endpoint_mode": args["mode"], "cycle.moisture_threshold": args["moisture_threshold"]})
        elif cap == "lyophilization.start":
            if active:
                return fail("BUSY")
            if not host._start_ready(candidate):
                return fail("PRECONDITION_FAILED")
            s.update({"cycle.phase": "pre_freeze", "cycle.running": True, "cycle.paused": False, "cycle.phase_elapsed_s": 0.0, "cycle.elapsed_s": 0.0, "cycle.endpoint_status": "pending", "cycle.wait_reason": "none"})
        elif cap == "lyophilization.pause":
            if not s["cycle.running"]:
                return fail("PRECONDITION_FAILED")
            s["cycle.running"], s["cycle.paused"] = False, True
        elif cap == "lyophilization.resume":
            if not s["cycle.paused"] or not host._start_ready(candidate):
                return fail("PRECONDITION_FAILED")
            s["cycle.running"], s["cycle.paused"] = True, False
        elif cap in ("lyophilization.stop", "lyophilization.abort"):
            s.update({"cycle.running": False, "cycle.paused": False, "cycle.phase": "stopped" if cap.endswith("stop") else "aborted", "cycle.endpoint_status": "stopped" if cap.endswith("stop") else "aborted", "cycle.wait_reason": "none"})
        elif cap == "condenser.defrost":
            if active or s["sample.loaded"] or candidate.source is not None or s["condenser.temperature_k"] is None or s["condenser.temperature_k"] < 273.15:
                return fail("PRECONDITION_FAILED")
            s["condenser.drained_mass_kg"] += s["condenser.ice_mass_kg"]
            s["condenser.ice_mass_kg"] = 0.0
        host._refresh(candidate)
        try:
            host._emit(candidate)
        except Exception:
            return fail("DEVICE_ERROR", "failed")
        candidate.revision += 1
        host._instances[inst.instance_id] = candidate
        return self._envelope(inst.instance_id, request_id, result={"applied": True})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(state_id, str) or state_id not in LYOVAPOR_STATE_SLOTS or not self._visible(LYOVAPOR_STATE_SLOTS[state_id]):
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = LYOVAPOR_STATE_SLOTS[state_id]["write_via"]
            if route is None:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return copy.deepcopy(self.__host._logs[self.__principal_id])


def validate_lyovapor_runtime(output=None):
    """Run only the CHEM-025 appended tests; emit truthful layer coverage."""
    import importlib.util
    import io
    import unittest
    test_file = Path(__file__).resolve().parents[1] / "tests" / "test_basic40_vacuum.py"
    spec = importlib.util.spec_from_file_location("test_basic40_vacuum_lyovapor", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(module.Basic40LyovaporTests))
    status = "passed" if result.wasSuccessful() else "failed"
    stamp = datetime.now(timezone.utc).isoformat()
    report = {"schema_version": "1.0.0", "asset_type_id": LYOVAPOR_ASSET_TYPE_ID, "recorded_at": stamp, "checked_at": stamp,
        "method": "Deterministic synthetic unittest through actual LyovaporSession APIs and trusted host boundaries/step; independent mass and clock assertions",
        "environment": {"python": platform.python_version(), "platform": platform.platform()}, "tests_run": result.testsRun, "result": status, "status": status, "failures": len(result.failures), "errors": len(result.errors), "log": stream.getvalue(),
        "executed_capability_ids": sorted(LYOVAPOR_ARGUMENTS), "layers": {"process": status, "host": status, "scene": "not_run", "robot": "not_run", "hardware": "not_run"},
        "data_classification": "Synthetic test fixtures only; no deployed sample truth", "limitations": list(LYOVAPOR_LIMITATIONS)}
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


# CHEM-190: append-only PURELAB flex 3 synthetic water and interface model.
PURELAB_ASSET_TYPE_ID = "instrument.elga_purelab_flex3"
PURELAB_MAX_DISPENSE_M3_S = 2e-3 / 60.0
PURELAB_MAX_RO_M3_S = 20e-3 / 3600.0
PURELAB_ARM_TRAVEL_M = .12
PURELAB_LIMITATIONS = [
    "Engineering-assumption volume bookkeeping only; not a manufacturer controller, purification, hydraulics, thermal, contaminant, microbial or filter-life model.",
    "The host explicitly supplies qualified feed, available purified-water buffer and receiver boundaries; buffer capacity is a simulation boundary, not a claimed internal tank specification.",
    "RO replenishment occurs only in recirculate mode; dispense, flush and RO replenishment are mutually exclusive engineering simplifications. RO recovery fraction and recirculation/flush rates require host provenance and have no manufacturer default.",
    "The 10 L/day catalog usage figure is a nominal application rating, not an enforced daily quota. Dispense flow is capped at 2 L/min and RO product flow at 20 L/h.",
    "Resistivity, temperature and TOC remain null until a trusted host supplies observations with provenance. 182000 ohm*m at 298.15 K is the catalog reference, never an automatically guaranteed output; typical TOC <5 ppb is feed-dependent, not a default reading.",
    "Consumable lifetime, remaining fraction and performance degradation remain unknown/null. Throughput is a synthetic cumulative counter; reset requires a one-use trusted-host replacement confirmation.",
    "Arm height is a manually positioned simulation configuration with 0..0.12 m travel from the catalog overall height span; no powered arm, robot action, collision or hardware driver is claimed.",
    "Device commands are synchronous state transitions; water transfer happens only on the shared host step. Stop, power-off, buffer exhaustion and receiver-full retain partial transferred volume.",
    "Scene callbacks receive public observations only; a command callback failure leaves process state unchanged but scene effects unknown. Host-step callback failure retains committed material transfers and time.",
    "Session filtering is an API boundary, not Python memory/filesystem/process isolation; trusted host methods and debug snapshots must remain outside agent transport.",
    "Request deduplication is in memory per principal and instance and does not survive restart. This water asset has no vacuum dependency.",
]


def _pure_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _pure_num(unit, low=0, high=None, nullable=False):
    schema = {"type": "number", "minimum": low, "x-unit": unit}
    if high is not None:
        schema["maximum"] = high
    return {"anyOf": [schema, {"type": "null"}]} if nullable else schema


def _pure_obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _pure_slot(unit, origin, schema, meaning, visibility="agent", capability=None, parameter=None):
    return {**_slot(unit, origin, schema, visibility, capability, parameter), "meaning_zh": meaning}


PURELAB_STATE_SLOTS = {
    "power.enabled": _pure_slot("1", "setpoint", {"type": "boolean"}, "供电目标", capability="power.set", parameter="enabled"),
    "arm.height_m": _pure_slot("m", "scene", _pure_num("m", 0, PURELAB_ARM_TRAVEL_M), "取水臂相对最低位置的手动升高量", capability="arm.set_height", parameter="height_m"),
    "water.target_volume_m3": _pure_slot("m^3", "setpoint", _pure_num("m^3", nullable=True), "本次取水体积目标；未设置为null", capability="water.set_volume", parameter="volume_m3"),
    "water.flow_rate_m3_s": _pure_slot("m^3/s", "setpoint", _pure_num("m^3/s", 0, PURELAB_MAX_DISPENSE_M3_S, True), "取水体积流量目标；未设置为null", capability="water.set_flow_rate", parameter="flow_m3_s"),
    "water.mode": _pure_slot("1", "setpoint", {"type": "string", "enum": ["idle", "recirculate", "flush"]}, "待机、假设循环补水或假设冲洗模式", capability="water.set_mode", parameter="mode"),
    "water.dispensing": _pure_slot("1", "derived", {"type": "boolean"}, "定量取水过程正在运行"),
    "water.dispense_status": _pure_slot("1", "derived", {"type": "string", "enum": ["idle", "running", "completed", "stopped", "supply_empty", "receiver_full", "power_off"]}, "定量取水状态；停止后保留部分效果"),
    "water.indicated_dispensed_volume_m3": _pure_slot("m^3", "derived", _pure_num("m^3"), "本次已取水量的理想合成读数；非流量计精度声明"),
    "water.source_connected": _pure_slot("1", "derived", {"type": "boolean"}, "宿主已配置外部进水与净水缓冲边界"),
    "water.receiver_connected": _pure_slot("1", "derived", {"type": "boolean"}, "宿主已配置接收容器"),
    "water.qualified_feed": _pure_slot("1", "derived", {"anyOf": [{"type": "boolean"}, {"type": "null"}]}, "宿主对进水合格性的外部确认；非化学分析结果"),
    "water.resistivity_ohm_m": _pure_slot("ohm*m", "sensor", _pure_num("ohm*m", nullable=True), "宿主输入电阻率观测；默认不可用，不保证18.2 MΩ·cm"),
    "water.temperature_k": _pure_slot("K", "sensor", {"anyOf": [{"type": "number", "exclusiveMinimum": 0, "x-unit": "K"}, {"type": "null"}]}, "宿主输入水质读数对应温度；默认不可用"),
    "water.toc_mass_fraction": _pure_slot("1", "sensor", _pure_num("1", 0, 1, True), "宿主输入TOC质量分数；默认不可用，不将典型<5ppb作保证"),
    "consumables.installed": _pure_slot("1", "derived", {"type": "boolean"}, "宿主确认已装耗材"),
    "consumables.status": _pure_slot("1", "derived", {"type": "string", "enum": ["unknown", "installed_unrated", "replacement_confirmed"]}, "耗材安装/更换确认状态；未评估寿命"),
    "consumables.remaining_life_fraction": _pure_slot("1", "derived", {"type": "null"}, "缺少滤柱寿命依据，始终null"),
    "consumables.throughput_m3": _pure_slot("m^3", "derived", _pure_num("m^3"), "自最近已确认更换复位后流经抛光回路的合成累计体积"),
    **{sid: _pure_slot("m^3", "process_truth", _pure_num("m^3", nullable=nullable), meaning, "debug") for sid, nullable, meaning in (
        ("water.dispensed_volume_m3", False, "本次实际转移的合成过程真值"),
        ("water.total_dispensed_volume_m3", False, "实例创建以来累计转移给接收容器的合成体积"),
        ("water.feed_volume_m3", True, "外部进水边界剩余体积"),
        ("water.buffer_volume_m3", True, "宿主净水缓冲边界剩余体积；不是已知内部储罐参数"),
        ("water.receiver_volume_m3", True, "外部接收容器当前体积"),
        ("water.waste_volume_m3", False, "RO浓水与冲洗排水的累计合成体积"),
        ("water.ro_product_volume_m3", False, "累计RO补充的净水体积"),
        ("consumables.cumulative_throughput_m3", False, "实例创建以来流经抛光回路的体积；复位不清除此审计量"),
    )},
}
PURELAB_ARGUMENTS = {
    "power.set": {"enabled": {"type": "boolean"}},
    "arm.set_height": {"height_m": _pure_num("m", 0, PURELAB_ARM_TRAVEL_M)},
    "water.set_volume": {"volume_m3": {"type": "number", "exclusiveMinimum": 0, "x-unit": "m^3", "description": "Finite positive setpoint; no undocumented device range or daily quota is imposed"}},
    "water.set_flow_rate": {"flow_m3_s": {"type": "number", "exclusiveMinimum": 0, "maximum": PURELAB_MAX_DISPENSE_M3_S, "x-unit": "m^3/s", "description": "Liquid volume at supplied water conditions; catalog maximum 2 L/min"}},
    "water.start": {}, "water.stop": {},
    "water.set_mode": {"mode": {"type": "string", "enum": ["idle", "recirculate", "flush"]}},
    "consumables.reset": {},
}
PURELAB_PARAMETERS = {
    "maximum_dispense_flow_m3_s": {"value": PURELAB_MAX_DISPENSE_M3_S, "unit": "m^3/s", "evidence": "catalog", "source": "CHEM-190 row195: 2 L/min"},
    "maximum_ro_product_flow_m3_s": {"value": PURELAB_MAX_RO_M3_S, "unit": "m^3/s", "evidence": "catalog", "source": "CHEM-190 row195: 20 L/h maximum RO replenishment"},
    "nominal_daily_usage_m3": {"value": .01, "unit": "m^3", "evidence": "catalog", "source": "CHEM-190 row195: 10 L/day usage rating, not implemented as hard quota"},
    "reference_resistivity_ohm_m": {"value": 182000.0, "unit": "ohm*m", "evidence": "catalog", "source": "18.2 MΩ·cm at 25 °C; reference, not predicted or guaranteed reading"},
    "reference_temperature_k": {"value": 298.15, "unit": "K", "evidence": "catalog", "source": "25 °C reference"},
    "typical_toc_upper_bound_mass_fraction": {"value": 5e-9, "unit": "1", "evidence": "catalog", "source": "Typical <5 ppb, dependent on feed; no default observation or purity warranty"},
    "arm_travel_m": {"value": PURELAB_ARM_TRAVEL_M, "unit": "m", "evidence": "engineering_assumption", "source": "0.900..1.020 m overall catalog height span mapped to a single vertical arm degree of freedom"},
    **{name: {"value": None, "unit": unit, "evidence": "unknown", "source": explanation} for name, unit, explanation in (
        ("internal_buffer_capacity_m3", "m^3", "No internal capacity assigned; explicit host boundary required"),
        ("ro_recovery_fraction", "1", "Caller supplies engineering assumption; not catalog performance"),
        ("recirculation_flow_m3_s", "m^3/s", "Caller supplies engineering assumption"),
        ("flush_flow_m3_s", "m^3/s", "Caller supplies engineering assumption"),
        ("consumable_life_s", "s", "Not in catalog; no degradation or life-percent model"),
        ("microbial_performance", "1", "Depends on optional terminal filter; no universal guarantee"),
    )},
}


def export_purelab_contract_metadata():
    """Contract inputs contain only type metadata, never live instance values."""
    slots = copy.deepcopy(PURELAB_STATE_SLOTS)
    integral = {"water.indicated_dispensed_volume_m3", "water.dispensed_volume_m3", "water.total_dispensed_volume_m3", "water.waste_volume_m3", "water.ro_product_volume_m3", "consumables.throughput_m3", "consumables.cumulative_throughput_m3"}
    for sid, slot in slots.items():
        slot["read_binding"] = f"PurelabSession.read(instance_id=..., state_ids=['{sid}'])"
        window = "Current dispense operation" if sid in ("water.indicated_dispensed_volume_m3", "water.dispensed_volume_m3") else "Since confirmed consumable reset" if sid == "consumables.throughput_m3" else "Since instance creation"
        slot["sampling"] = {"mode": "integral" if sid in integral else "instant", "time_domain": "simulation", "window": window if sid in integral else None}
        slot["reference_conditions"] = "Local +Z relative to minimum arm height; SI metres" if sid == "arm.height_m" else "Liquid volume; temperature supplied externally, no density or expansion model" if slot["unit"] in ("m^3", "m^3/s") else "Host-supplied temperature; no automatic 25 °C correction" if sid == "water.resistivity_ohm_m" else None
    descriptions = {"power.set": "设置供电；断电停止取水/冲洗/循环并保留已经转移的水量", "arm.set_height": "手动配置取水臂相对最低位置的升高量", "water.set_volume": "设置本次定量目标，有限正值，无无依据的每日硬限", "water.set_flow_rate": "设置取水流量，最高2 L/min", "water.start": "启动新的定量取水；流体仅由宿主step推进", "water.stop": "停止后续定量取水，保留本次已经转移的水量", "water.set_mode": "切换待机、循环补水、冲洗简化模式", "consumables.reset": "使用宿主一次性更换确认复位耗材通量，不伪造寿命百分比"}
    direct = {"power.set": ["power.enabled", "water.dispensing", "water.mode", "water.dispense_status"], "arm.set_height": ["arm.height_m"], "water.set_volume": ["water.target_volume_m3"], "water.set_flow_rate": ["water.flow_rate_m3_s"], "water.start": ["water.dispensing", "water.dispense_status", "water.dispensed_volume_m3", "water.indicated_dispensed_volume_m3"], "water.stop": ["water.dispensing", "water.dispense_status"], "water.set_mode": ["water.mode"], "consumables.reset": ["consumables.throughput_m3", "consumables.status"]}
    step_ids = ["water.dispensing", "water.dispense_status", "water.indicated_dispensed_volume_m3", "water.dispensed_volume_m3", "water.total_dispensed_volume_m3", "water.feed_volume_m3", "water.buffer_volume_m3", "water.receiver_volume_m3", "water.waste_volume_m3", "water.ro_product_volume_m3", "consumables.throughput_m3", "consumables.cumulative_throughput_m3"]
    examples = {"power.set": {"enabled": True}, "arm.set_height": {"height_m": .12}, "water.set_volume": {"volume_m3": .001}, "water.set_flow_rate": {"flow_m3_s": PURELAB_MAX_DISPENSE_M3_S}, "water.start": {}, "water.stop": {}, "water.set_mode": {"mode": "idle"}, "consumables.reset": {}}
    prerequisites = {
        "power.set": "Always schema-valid; power-off retains partial transfer", "arm.set_height": "No dispensing, flush or recirculation is active; manual configuration only", "water.set_volume": "Dispensing inactive", "water.set_flow_rate": "Dispensing inactive", "water.start": "Power on, idle mode, installed consumables, qualified supply, positive configured target/rate, nonempty buffer, connected receiver with free space", "water.stop": "Always schema-valid; idempotent stop", "water.set_mode": "No active dispense; non-idle requires power/installed consumables/qualified supply; recirculate and flush require explicit host rate; flush requires drain and nonempty buffer", "consumables.reset": "No active process, consumables installed and a trusted-host replacement confirmation remains unused",
    }
    caps = {}
    for cap, props in PURELAB_ARGUMENTS.items():
        effects = [{"state_id": sid, "timing": "immediate", "description_zh": descriptions[cap]} for sid in direct[cap]]
        if cap in ("water.start", "water.set_mode"):
            effects += [{"state_id": sid, "timing": "on_step", "description_zh": "宿主守恒记账推进；有限进水、缓冲体积和容器容量限制，工程假设"} for sid in step_ids]
        caps[cap] = {"availability": "implemented", "visibility": "agent", "kind": "configuration" if cap == "arm.set_height" else "device_command", "description_zh": descriptions[cap], "input_schema": _pure_obj(copy.deepcopy(props)), "output_schema": _pure_obj({"applied": {"type": "boolean", "const": True}}), "state_effects": effects, "observes": [sid for sid in direct[cap] if slots[sid]["visibility"] == "agent"], "example_arguments": [examples[cap]], "precondition_note": prerequisites[cap], "binding": f"basic40_runtime_vacuum.PurelabSession.invoke(instance_id=..., capability_id='{cap}', arguments=..., request_id=...)"}
    return {"asset_types": {PURELAB_ASSET_TYPE_ID: {"catalog_id": "CHEM-190", "model": "ELGA LabWater | PURELAB flex 3", "asset_version": "1.0.1", "parameters": copy.deepcopy(PURELAB_PARAMETERS), "state_slots": slots, "capabilities": caps}}, "host_capabilities": PurelabHost.describe_host(), "limitations": list(PURELAB_LIMITATIONS)}


@dataclass
class _PurelabInstance:
    instance_id: str
    state: dict
    revision: int = 0
    created_at_s: float = 0.0
    batch_started_at_s: float = 0.0
    reset_at_s: float = 0.0
    supply: dict | None = None
    receiver: dict | None = None
    replacement_confirmation: str | None = None
    reading_evidence: str | None = None
    reading_kind: str | None = None
    reading_time_s: float | None = None


class PurelabSceneSynchronizationError(RuntimeError):
    """Material bookkeeping/time committed; scene synchronization may be partial."""


class PurelabHost:
    """Trusted world owner; boundaries and a single clock are not agent APIs."""
    def __init__(self, *, scene_adapter=None):
        self.scene_adapter = scene_adapter
        self.time_s = 0.0
        self._instances, self._principals, self._requests, self._logs = {}, {}, {}, {}
        self._scene_faults = []
        self._lock = threading.RLock()

    @staticmethod
    def describe_host():
        return {"sim.step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "basic40_runtime_vacuum.PurelabHost.step(dt_s)", "scope": "All registered PURELAB instances share this clock; embedding world coordinates other hosts", "input_schema": _pure_obj({"dt_s": _pure_num("s", 0, 86400)})},
            **{f"host.{name}": {"kind": "configuration", "visibility": "trusted_host_only", "binding": f"basic40_runtime_vacuum.PurelabHost.{name}"} for name in ("configure_supply", "connect_receiver", "disconnect_receiver", "install_consumables", "confirm_consumables_replacement", "set_readings", "sync_scene")}}

    def create_instance(self, instance_id, asset_type_id=PURELAB_ASSET_TYPE_ID):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != PURELAB_ASSET_TYPE_ID:
                raise ValueError("Unique instance and supported asset type required")
            s = {sid: None for sid in PURELAB_STATE_SLOTS}
            s.update({"power.enabled": False, "arm.height_m": 0.0, "water.mode": "idle", "water.dispensing": False, "water.dispense_status": "idle", "water.source_connected": False, "water.receiver_connected": False, "consumables.installed": False, "consumables.status": "unknown"})
            for sid in ("water.indicated_dispensed_volume_m3", "water.dispensed_volume_m3", "water.total_dispensed_volume_m3", "water.waste_volume_m3", "water.ro_product_volume_m3", "consumables.throughput_m3", "consumables.cumulative_throughput_m3"):
                s[sid] = 0.0
            inst = _PurelabInstance(instance_id, s, created_at_s=self.time_s, batch_started_at_s=self.time_s, reset_at_s=self.time_s)
            self._emit(inst)
            self._instances[instance_id] = inst

    def register_principal(self, principal_id, *, visibility="agent", instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Unique trusted principal and supported visibility required")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set)) or any(not isinstance(i, str) or i not in self._instances for i in instance_ids)):
                raise ValueError("Instance grants must name registered instances")
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))
            self._logs[principal_id] = []

    def session(self, principal_id):
        with self._lock:
            if not isinstance(principal_id, str) or principal_id not in self._principals:
                raise ValueError("Host registration required")
            return PurelabSession(self, principal_id)

    @staticmethod
    def _evidence(evidence):
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("Explicit evidence or engineering-assumption provenance required")

    @staticmethod
    def _active(inst):
        return inst.state["water.dispensing"] or inst.state["water.mode"] != "idle"

    def _idle_copy(self, instance_id):
        inst = self._instances[instance_id]
        if self._active(inst):
            raise ValueError("Stop dispensing and select idle before host configuration")
        return copy.deepcopy(inst)

    @staticmethod
    def _refresh(inst):
        s = inst.state
        s["water.source_connected"] = inst.supply is not None
        s["water.receiver_connected"] = inst.receiver is not None
        s["water.qualified_feed"] = None if inst.supply is None else inst.supply["qualified_feed"]
        s["water.indicated_dispensed_volume_m3"] = s["water.dispensed_volume_m3"]

    def _emit(self, inst):
        if self.scene_adapter:
            public = {sid: copy.deepcopy(value) for sid, value in inst.state.items() if PURELAB_STATE_SLOTS[sid]["visibility"] == "agent"}
            self.scene_adapter(inst.instance_id, public)

    def _commit_host(self, candidate):
        self._refresh(candidate)
        candidate.revision += 1
        self._instances[candidate.instance_id] = candidate
        try:
            self._emit(candidate)
        except Exception:
            self._scene_faults.append({"instance_id": candidate.instance_id, "time_s": self.time_s})
            raise PurelabSceneSynchronizationError("Host state committed; scene effects unknown") from None

    def configure_supply(self, instance_id, *, feed_volume_m3, purified_volume_m3, buffer_capacity_m3, qualified_feed, evidence, ro_product_rate_m3_s=None, ro_recovery_fraction=None, recirculation_flow_m3_s=None, flush_flow_m3_s=None, drain_connected=False):
        """Initialize owned external boundary once; all optional kinetics default unknown."""
        with self._lock:
            self._evidence(evidence)
            if any(not _pure_number(x) or x < 0 for x in (feed_volume_m3, purified_volume_m3, buffer_capacity_m3)) or buffer_capacity_m3 <= 0 or purified_volume_m3 > buffer_capacity_m3:
                raise ValueError("Nonnegative volumes and positive external buffer capacity required")
            if type(qualified_feed) is not bool or type(drain_connected) is not bool:
                raise ValueError("Explicit feed qualification and drain confirmation required")
            rates = (ro_product_rate_m3_s, recirculation_flow_m3_s, flush_flow_m3_s)
            if any(x is not None and (not _pure_number(x) or x < 0) for x in rates):
                raise ValueError("Rates must be null or finite nonnegative values")
            if ro_product_rate_m3_s is not None and ro_product_rate_m3_s > PURELAB_MAX_RO_M3_S:
                raise ValueError("RO product rate exceeds catalog maximum")
            if ro_recovery_fraction is not None and (not _pure_number(ro_recovery_fraction) or not 0 < ro_recovery_fraction <= 1):
                raise ValueError("RO recovery must be null or in (0, 1]")
            if (ro_product_rate_m3_s or 0) > 0 and (ro_recovery_fraction is None or not drain_connected):
                raise ValueError("Positive RO production requires explicit recovery and waste drain")
            candidate = self._idle_copy(instance_id)
            if candidate.supply is not None:
                raise ValueError("Boundary already initialized; external material cannot be overwritten")
            candidate.supply = {"buffer_capacity_m3": float(buffer_capacity_m3), "qualified_feed": qualified_feed, "ro_product_rate_m3_s": ro_product_rate_m3_s, "ro_recovery_fraction": ro_recovery_fraction, "recirculation_flow_m3_s": recirculation_flow_m3_s, "flush_flow_m3_s": flush_flow_m3_s, "drain_connected": drain_connected, "evidence": evidence}
            candidate.state.update({"water.feed_volume_m3": float(feed_volume_m3), "water.buffer_volume_m3": float(purified_volume_m3)})
            self._commit_host(candidate)

    def connect_receiver(self, instance_id, *, receiver_id, volume_m3, capacity_m3, evidence):
        with self._lock:
            self._evidence(evidence)
            if not isinstance(receiver_id, str) or not receiver_id or any(not _pure_number(x) or x < 0 for x in (volume_m3, capacity_m3)) or capacity_m3 <= 0 or volume_m3 > capacity_m3:
                raise ValueError("Named external receiver with valid volume and capacity required")
            if any(inst.receiver and inst.receiver["receiver_id"] == receiver_id for inst in self._instances.values()):
                raise ValueError("Receiver already owned by another active connection")
            candidate = self._idle_copy(instance_id)
            if candidate.receiver is not None:
                raise ValueError("Detach existing receiver before replacement")
            candidate.receiver = {"receiver_id": receiver_id, "capacity_m3": float(capacity_m3), "evidence": evidence}
            candidate.state["water.receiver_volume_m3"] = float(volume_m3)
            self._commit_host(candidate)

    def disconnect_receiver(self, instance_id):
        """Return transferred volume to the trusted world; no public truth exposure."""
        with self._lock:
            candidate = self._idle_copy(instance_id)
            if candidate.receiver is None:
                raise ValueError("No receiver attached")
            removed = {"receiver_id": candidate.receiver["receiver_id"], "volume_m3": candidate.state["water.receiver_volume_m3"]}
            candidate.receiver = None
            candidate.state["water.receiver_volume_m3"] = None
            self._commit_host(candidate)
            return removed

    def install_consumables(self, instance_id, *, evidence):
        with self._lock:
            self._evidence(evidence)
            candidate = self._idle_copy(instance_id)
            if candidate.state["consumables.installed"]:
                raise ValueError("Initial consumables already installed; confirm replacement instead")
            candidate.state.update({"consumables.installed": True, "consumables.status": "installed_unrated"})
            candidate.consumable_evidence = evidence
            self._commit_host(candidate)

    def confirm_consumables_replacement(self, instance_id, *, evidence):
        with self._lock:
            self._evidence(evidence)
            candidate = self._idle_copy(instance_id)
            if not candidate.state["consumables.installed"]:
                raise ValueError("Install initial consumables before replacement")
            candidate.replacement_confirmation = evidence
            self._commit_host(candidate)

    def set_readings(self, instance_id, *, resistivity_ohm_m=None, temperature_k=None, toc_mass_fraction=None, source_kind, evidence):
        """Publish externally observed or explicitly synthetic data, not inferred purity."""
        with self._lock:
            self._evidence(evidence)
            if source_kind not in ("external_observation", "synthetic"):
                raise ValueError("Explicit reading source required")
            values = {"water.resistivity_ohm_m": resistivity_ohm_m, "water.temperature_k": temperature_k, "water.toc_mass_fraction": toc_mass_fraction}
            if any(not PurelabSession._valid(value, PURELAB_STATE_SLOTS[sid]["value_schema"]) for sid, value in values.items()):
                raise ValueError("Readings must fit their SI schemas")
            if resistivity_ohm_m is not None and temperature_k is None:
                raise ValueError("Resistivity observation needs its associated water temperature")
            candidate = copy.deepcopy(self._instances[instance_id])
            candidate.state.update(values)
            candidate.reading_evidence, candidate.reading_kind, candidate.reading_time_s = evidence, source_kind, self.time_s
            self._commit_host(candidate)

    @staticmethod
    def _throughput(s, amount):
        s["consumables.throughput_m3"] += amount
        s["consumables.cumulative_throughput_m3"] += amount

    def _advance(self, inst, dt):
        s = inst.state
        if not s["power.enabled"] or not s["consumables.installed"] or not inst.supply or not s["water.qualified_feed"]:
            return
        boundary = inst.supply
        if s["water.dispensing"]:
            remaining = max(0.0, s["water.target_volume_m3"] - s["water.dispensed_volume_m3"])
            free = max(0.0, inst.receiver["capacity_m3"] - s["water.receiver_volume_m3"])
            amount = min(s["water.flow_rate_m3_s"] * dt, remaining, s["water.buffer_volume_m3"], free)
            s["water.buffer_volume_m3"] -= amount
            s["water.receiver_volume_m3"] += amount
            s["water.dispensed_volume_m3"] += amount
            s["water.total_dispensed_volume_m3"] += amount
            self._throughput(s, amount)
            # Compare the chosen transfer against its pre-transfer limit, avoiding arbitrary volume tolerances.
            status = "completed" if amount >= remaining else "supply_empty" if amount >= s["water.buffer_volume_m3"] + amount else "receiver_full" if amount >= free else None
            if status:
                s["water.dispensing"], s["water.dispense_status"] = False, status
        elif s["water.mode"] == "flush":
            amount = min(boundary["flush_flow_m3_s"] * dt, s["water.buffer_volume_m3"])
            s["water.buffer_volume_m3"] -= amount
            s["water.waste_volume_m3"] += amount
            self._throughput(s, amount)
        elif s["water.mode"] == "recirculate":
            product = 0.0
            if boundary["ro_product_rate_m3_s"] and s["water.feed_volume_m3"] > 0:
                product = min(boundary["ro_product_rate_m3_s"] * dt, s["water.feed_volume_m3"] * boundary["ro_recovery_fraction"], max(0.0, boundary["buffer_capacity_m3"] - s["water.buffer_volume_m3"]))
                raw = min(s["water.feed_volume_m3"], product / boundary["ro_recovery_fraction"])
                s["water.feed_volume_m3"] -= raw
                s["water.buffer_volume_m3"] += product
                s["water.ro_product_volume_m3"] += product
                s["water.waste_volume_m3"] += max(0.0, raw - product)
            # Perfect mixing, negligible priming time; recirculation counts repeated passages without consuming water.
            if s["water.buffer_volume_m3"] > 0:
                self._throughput(s, boundary["recirculation_flow_m3_s"] * dt)

    def step(self, dt_s):
        with self._lock:
            if not _pure_number(dt_s) or not 0 <= dt_s <= 86400:
                raise ValueError("Host step must be finite 0..86400 seconds")
            if dt_s == 0:
                return
            candidates = copy.deepcopy(self._instances)
            for inst in candidates.values():
                self._advance(inst, float(dt_s))
                self._refresh(inst)
                if any(type(value) in (int, float) and not math.isfinite(value) for value in inst.state.values()):
                    raise ValueError("Host step would exceed finite numeric representation; no transfer committed")
                inst.revision += 1
            self.time_s += float(dt_s)
            self._instances = candidates
            failed = False
            for inst in candidates.values():
                try:
                    self._emit(inst)
                except Exception:
                    failed = True
                    self._scene_faults.append({"instance_id": inst.instance_id, "time_s": self.time_s})
            if failed:
                raise PurelabSceneSynchronizationError("Shared time and water transfers committed; scene effects unknown")

    def sync_scene(self, instance_id):
        with self._lock:
            self._emit(self._instances[instance_id])

    def debug_snapshot(self):
        with self._lock:
            return copy.deepcopy({"time_s": self.time_s, "instances": {key: vars(inst) for key, inst in self._instances.items()}, "scene_faults": self._scene_faults})


class PurelabSession:
    """Permission-filtered discovery, reading and deduplicated device commands."""
    def __init__(self, host, principal_id):
        if principal_id not in host._principals:
            raise ValueError("Trusted principal registration required")
        self.__host, self.__principal_id = host, principal_id

    def _visible(self, info):
        return info["visibility"] == "agent" or self.__host._principals[self.__principal_id][0] in ("debug", "evaluator")

    def _instance(self, instance_id):
        _, allowed = self.__host._principals[self.__principal_id]
        if not isinstance(instance_id, str) or (allowed is not None and instance_id not in allowed):
            return None
        return self.__host._instances.get(instance_id)

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": PROTOCOL_VERSION, "instance_id": instance_id if isinstance(instance_id, str) else None, "request_id": request_id if isinstance(request_id, str) else None, "operation_id": None, "status": status, "state_revision": None if inst is None else inst.revision, "clock": {"domain": "simulation", "time_s": self.__host.time_s}, "result": {} if result is None else result, "observations": {} if observations is None else observations, "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Unknown or inaccessible instance", "UNKNOWN_CAPABILITY": "Unknown capability", "ACCESS_DENIED": "State inaccessible or not writable", "INVALID_ARGUMENT": "Arguments do not match supported schema", "PRECONDITION_FAILED": "External water boundary, consumable or configuration prerequisites not satisfied", "BUSY": "Stop the active operation before configuration", "REQUEST_CONFLICT": "Request ID already used for different content", "DEVICE_ERROR": "Scene callback failed; external effects unknown"}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": messages[code], "retryable": False, "side_effects": "unknown" if status == "failed" else "none", "known_progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None) or asset_type_id not in (None, PURELAB_ASSET_TYPE_ID):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            spec = export_purelab_contract_metadata()["asset_types"][PURELAB_ASSET_TYPE_ID]
            visible = {sid: slot for sid, slot in spec["state_slots"].items() if self._visible(slot)}
            for cap in spec["capabilities"].values():
                cap["state_effects"] = [effect for effect in cap["state_effects"] if effect["state_id"] in visible]
                cap["observes"] = [sid for sid in cap["observes"] if sid in visible]
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": PURELAB_ASSET_TYPE_ID, "asset_version": "1.0.1", "instance_id": instance_id, "catalog_id": "CHEM-190", "model": spec["model"], "adapter": "basic40_runtime_vacuum.PurelabSession", "state_slots": visible, "capabilities": spec["capabilities"], "limitations": list(PURELAB_LIMITATIONS)}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            ids = [sid for sid, info in PURELAB_STATE_SLOTS.items() if self._visible(info)] if state_ids is None else state_ids
            if not isinstance(ids, list) or any(not isinstance(sid, str) for sid in ids) or len(set(ids)) != len(ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(sid not in PURELAB_STATE_SLOTS or not self._visible(PURELAB_STATE_SLOTS[sid]) for sid in ids):
                return self._error(instance_id, "ACCESS_DENIED")
            observations = {}
            for sid in ids:
                info, value = PURELAB_STATE_SLOTS[sid], copy.deepcopy(inst.state[sid])
                sensor = info["origin"] == "sensor"
                quality = "unavailable" if value is None else "estimated" if info["origin"] == "derived" or (sensor and inst.reading_kind == "synthetic") else "valid"
                obs = {"value": value, "unit": info["unit"], "quality": quality, "sample_time_s": inst.reading_time_s if sensor else self.__host.time_s, "sample_window_s": None}
                if sid in ("water.indicated_dispensed_volume_m3", "water.dispensed_volume_m3"):
                    obs["sample_window_s"] = [inst.batch_started_at_s, self.__host.time_s]
                elif sid == "consumables.throughput_m3":
                    obs["sample_window_s"] = [inst.reset_at_s, self.__host.time_s]
                elif sid in ("water.total_dispensed_volume_m3", "water.waste_volume_m3", "water.ro_product_volume_m3", "consumables.cumulative_throughput_m3"):
                    obs["sample_window_s"] = [inst.created_at_s, self.__host.time_s]
                if value is None:
                    obs["reason"] = "No consumable life evidence or host-supplied observation/configuration is available"
                observations[sid] = obs
            return self._envelope(instance_id, observations=observations)

    @staticmethod
    def _valid(value, schema):
        if "anyOf" in schema:
            return any(PurelabSession._valid(value, child) for child in schema["anyOf"])
        kind = schema.get("type")
        if kind == "object":
            return isinstance(value, dict) and set(value) == set(schema["properties"]) and all(PurelabSession._valid(value[k], child) for k, child in schema["properties"].items())
        if kind == "number":
            return _pure_number(value) and schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf) and value > schema.get("exclusiveMinimum", -math.inf)
        if kind == "boolean":
            return type(value) is bool
        if kind == "string":
            return isinstance(value, str) and value in schema.get("enum", [value])
        return kind == "null" and value is None

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256 or not isinstance(capability_id, str) or not isinstance(arguments, dict):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = hashlib.sha256(json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            except (ValueError, TypeError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = (self.__principal_id, instance_id, request_id)
            if key in host._requests:
                prior, response = host._requests[key]
                return copy.deepcopy(response) if prior == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._apply(inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, copy.deepcopy(response))
            host._logs[self.__principal_id].append(copy.deepcopy(response))
            return response

    def _apply(self, inst, cap, args, request_id):
        host = self.__host
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request_id, status)
        if cap not in PURELAB_ARGUMENTS:
            return fail("UNKNOWN_CAPABILITY")
        if not self._valid(args, _pure_obj(PURELAB_ARGUMENTS[cap])):
            return fail("INVALID_ARGUMENT")
        candidate = copy.deepcopy(inst)
        s = candidate.state
        if cap == "power.set":
            s["power.enabled"] = args["enabled"]
            if not args["enabled"]:
                if s["water.dispensing"]:
                    s["water.dispense_status"] = "power_off"
                s["water.dispensing"], s["water.mode"] = False, "idle"
        elif cap == "arm.set_height":
            if host._active(candidate):
                return fail("BUSY")
            s["arm.height_m"] = args["height_m"]
        elif cap in ("water.set_volume", "water.set_flow_rate"):
            if s["water.dispensing"]:
                return fail("BUSY")
            sid, parameter = ("water.target_volume_m3", "volume_m3") if cap == "water.set_volume" else ("water.flow_rate_m3_s", "flow_m3_s")
            s[sid] = args[parameter]
        elif cap == "water.start":
            if host._active(candidate):
                return fail("BUSY")
            if not (s["power.enabled"] and s["consumables.installed"] and candidate.supply and s["water.qualified_feed"] and s["water.target_volume_m3"] and s["water.flow_rate_m3_s"] and s["water.buffer_volume_m3"] > 0 and candidate.receiver and s["water.receiver_volume_m3"] < candidate.receiver["capacity_m3"]):
                return fail("PRECONDITION_FAILED")
            s.update({"water.dispensing": True, "water.dispense_status": "running", "water.dispensed_volume_m3": 0.0})
            candidate.batch_started_at_s = host.time_s
        elif cap == "water.stop":
            if s["water.dispensing"]:
                s["water.dispensing"], s["water.dispense_status"] = False, "stopped"
        elif cap == "water.set_mode":
            if s["water.dispensing"]:
                return fail("BUSY")
            mode = args["mode"]
            if mode != "idle":
                if not (s["power.enabled"] and s["consumables.installed"] and candidate.supply and s["water.qualified_feed"]):
                    return fail("PRECONDITION_FAILED")
                boundary = candidate.supply
                if mode == "recirculate" and not (boundary["recirculation_flow_m3_s"] and (s["water.buffer_volume_m3"] > 0 or ((boundary["ro_product_rate_m3_s"] or 0) > 0 and s["water.feed_volume_m3"] > 0))):
                    return fail("PRECONDITION_FAILED")
                if mode == "flush" and not (boundary["flush_flow_m3_s"] and boundary["drain_connected"] and s["water.buffer_volume_m3"] > 0):
                    return fail("PRECONDITION_FAILED")
            s["water.mode"] = mode
        elif cap == "consumables.reset":
            if host._active(candidate):
                return fail("BUSY")
            if not s["consumables.installed"] or candidate.replacement_confirmation is None:
                return fail("PRECONDITION_FAILED")
            s["consumables.throughput_m3"] = 0.0
            s["consumables.status"] = "replacement_confirmed"
            candidate.reset_at_s = host.time_s
            candidate.replacement_confirmation = None
        host._refresh(candidate)
        try:
            host._emit(candidate)
        except Exception:
            return fail("DEVICE_ERROR", "failed")
        candidate.revision += 1
        host._instances[inst.instance_id] = candidate
        return self._envelope(inst.instance_id, request_id, result={"applied": True})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(state_id, str) or state_id not in PURELAB_STATE_SLOTS or not self._visible(PURELAB_STATE_SLOTS[state_id]):
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = PURELAB_STATE_SLOTS[state_id]["write_via"]
            if route is None:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return copy.deepcopy(self.__host._logs[self.__principal_id])


def validate_purelab_runtime(output=None):
    """Run actual CHEM-190 tests; callback checks do not prove Blender evaluation."""
    import importlib.util
    import io
    import unittest
    test_file = Path(__file__).resolve().parents[1] / "tests" / "test_basic40_vacuum.py"
    spec = importlib.util.spec_from_file_location("test_basic40_vacuum_purelab", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(module.Basic40PurelabTests))
    status = "passed" if result.wasSuccessful() else "failed"
    stamp = datetime.now(timezone.utc).isoformat()
    report = {"schema_version": "1.0.0", "catalog_id": "CHEM-190", "asset_type_id": PURELAB_ASSET_TYPE_ID, "recorded_at": stamp, "checked_at": stamp,
        "method": "Synthetic unittest through real PurelabSession describe/read/invoke/write and trusted PurelabHost boundaries/step; independent SI volume balances, permission and failure assertions",
        "environment": {"python": platform.python_version(), "platform": platform.platform()}, "tests_run": result.testsRun, "result": status, "status": status, "failures": len(result.failures), "errors": len(result.errors), "log": stream.getvalue(), "executed_capability_ids": sorted(PURELAB_ARGUMENTS),
        "layers": {"process": status, "host": status, "scene": "not_run", "robot": "not_run", "hardware": "not_run"},
        "not_run": ["Blender evaluated articulation is verified separately by the asset builder", "Physics-engine collision/contact", "Robot manipulation", "Real hardware", "Manufacturer water-quality/flow calibration", "Microbial and consumable lifetime validation"],
        "data_classification": "Synthetic test fixtures only; no live experimental or task truth", "limitations": list(PURELAB_LIMITATIONS)}
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report

# CHEM-189 PureLab HE 2GB extension: append-only, independent from other assets.
INERT_PURELAB_ASSET_TYPE_ID = "instrument.inert_purelab_he_2gb"
INERT_PURELAB_EXTERNAL_VACUUM_TYPE_ID = "instrument.vacuubrand_pc3001_vario_select"
INERT_PURELAB_BLOWER_MAXIMUM_M3_S = 0.028316846592


def _inert_purelab_nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def _inert_purelab_slot(unit, origin, schema, visibility="agent", capability=None, parameter=None):
    return {"unit": unit, "origin": origin, "value_schema": schema, "visibility": visibility,
            "access": "read_write" if capability else "read_only",
            "write_via": {"capability_id": capability, "parameter": parameter} if capability else None}


INERT_PURELAB_STATE_SLOTS = {
    "power.enabled": _inert_purelab_slot("1", "setpoint", {"type": "boolean"}, capability="power.set", parameter="enabled"),
    "pressure.target_gauge_pa": _inert_purelab_slot("Pa", "setpoint", {"type": "number", "minimum": -1500, "maximum": 1500}, capability="pressure.set", parameter="pressure_pa"),
    "pressure.box_gauge_pa": _inert_purelab_slot("Pa", "process_truth", _inert_purelab_nullable({"type": "number"}), "debug"),
    "pressure.indicated_gauge_pa": _inert_purelab_slot("Pa", "sensor", _inert_purelab_nullable({"type": "number"})),
    "atmosphere.oxygen_mole_fraction": _inert_purelab_slot("1", "sensor", _inert_purelab_nullable({"type": "number", "minimum": 0, "maximum": 1})),
    "atmosphere.water_mole_fraction": _inert_purelab_slot("1", "sensor", _inert_purelab_nullable({"type": "number", "minimum": 0, "maximum": 1})),
    "atmosphere.oxygen_truth_mole_fraction": _inert_purelab_slot("1", "process_truth", _inert_purelab_nullable({"type": "number", "minimum": 0, "maximum": 1}), "debug"),
    "atmosphere.water_truth_mole_fraction": _inert_purelab_slot("1", "process_truth", _inert_purelab_nullable({"type": "number", "minimum": 0, "maximum": 1}), "debug"),
    "purification.enabled": _inert_purelab_slot("1", "setpoint", {"type": "boolean"}, capability="purification.set", parameter="enabled"),
    "purification.flow_m3_s": _inert_purelab_slot("m^3/s", "derived", {"type": "null"}),
    "purification.oxygen_removed_m3": _inert_purelab_slot("m^3", "derived", {"type": "null"}),
    "purification.water_removed_kg": _inert_purelab_slot("kg", "derived", {"type": "null"}),
    "purge.enabled": _inert_purelab_slot("1", "setpoint", {"type": "boolean"}, capability="purge.set", parameter="enabled"),
    "purge.flow_m3_s": _inert_purelab_slot("m^3/s", "derived", {"type": "null"}),
    "regeneration.status": _inert_purelab_slot("1", "derived", {"type": "string", "const": "unsupported"}),
    "vacuum.source_asset_type_id": _inert_purelab_slot("1", "derived", {"type": "string", "const": INERT_PURELAB_EXTERNAL_VACUUM_TYPE_ID}),
    "vacuum.source_instance_id": _inert_purelab_slot("1", "derived", _inert_purelab_nullable({"type": "string"})),
    "antechamber.target_cycles": _inert_purelab_slot("1", "setpoint", {"type": "integer", "minimum": 1, "maximum": 9}, capability="antechamber.set_cycles", parameter="cycles"),
    "antechamber.completed_cycles": _inert_purelab_slot("1", "derived", {"type": "integer", "minimum": 0, "maximum": 9}),
    "antechamber.cycle_active": _inert_purelab_slot("1", "derived", {"type": "boolean"}),
    "antechamber.phase": _inert_purelab_slot("1", "derived", {"type": "string", "enum": ["idle", "evacuating", "backfilling", "completed", "stopped"]}),
    "antechamber.pressure_pa": _inert_purelab_slot("Pa", "process_truth", _inert_purelab_nullable({"type": "number", "minimum": 0}), "debug"),
    "antechamber.indicated_pressure_pa": _inert_purelab_slot("Pa", "sensor", _inert_purelab_nullable({"type": "number", "minimum": 0})),
    "antechamber.inner_door_open": _inert_purelab_slot("1", "scene", {"type": "boolean"}, capability="antechamber.inner_door.set", parameter="open"),
    "antechamber.outer_door_open": _inert_purelab_slot("1", "scene", {"type": "boolean"}, capability="antechamber.outer_door.set", parameter="open"),
    "antechamber.inner_door_angle_rad": _inert_purelab_slot("rad", "scene", {"type": "number", "minimum": 0, "maximum": math.pi / 2}),
    "antechamber.outer_door_angle_rad": _inert_purelab_slot("rad", "scene", {"type": "number", "minimum": 0, "maximum": math.pi / 2}),
}
INERT_PURELAB_CAPABILITY_ARGUMENTS = {
    "power.set": {"enabled": {"type": "boolean"}},
    "pressure.set": {"pressure_pa": {"type": "number", "minimum": -1500, "maximum": 1500, "x-unit": "Pa", "description": "Gauge-pressure setpoint only; no pressure response rate is assumed."}},
    "purification.set": {"enabled": {"type": "boolean"}},
    "purge.set": {"enabled": {"type": "boolean"}},
    "antechamber.set_cycles": {"cycles": {"type": "integer", "minimum": 1, "maximum": 9, "x-unit": "1"}},
    "antechamber.cycle.start": {}, "antechamber.cycle.stop": {},
    "antechamber.inner_door.set": {"open": {"type": "boolean"}},
    "antechamber.outer_door.set": {"open": {"type": "boolean"}},
    "regeneration.start": {},
}
INERT_PURELAB_CAPABILITY_RESULT_SLOT = {
    "power.set": ("enabled", "power.enabled"), "pressure.set": ("pressure_pa", "pressure.target_gauge_pa"),
    "purification.set": ("enabled", "purification.enabled"), "purge.set": ("enabled", "purge.enabled"),
    "antechamber.set_cycles": ("cycles", "antechamber.target_cycles"),
    "antechamber.cycle.start": ("active", "antechamber.cycle_active"),
    "antechamber.cycle.stop": ("active", "antechamber.cycle_active"),
    "antechamber.inner_door.set": ("open", "antechamber.inner_door_open"),
    "antechamber.outer_door.set": ("open", "antechamber.outer_door_open"),
}
INERT_PURELAB_LIMITATIONS = [
    "CHEM-189 workbook row 194 and selection snapshot define PureLab HE 2GB; external 2.000 x 1.067 x 1.921 m and interior 1.250 x 0.780 x 0.900 m.",
    "Box pressure +/-1500 Pa is a gauge-pressure control setting range, not a guarantee of instantaneous actual pressure. Pressure response, leakage and material outgassing are not modeled.",
    "O2 and H2O <1e-6 mole fraction are catalog targets, not guaranteed readings. All public pressure/water/oxygen sensor values remain null until a trusted host supplies an observation with evidence; clock advancement marks older observations stale.",
    "Purification and purge implement control requests only; actual flows, sorption quantities and regeneration kinetics are unknown/null. Starting them does not imply purity improvement. Regeneration.start explicitly returns unsupported.",
    "Blower maximum 60 cfm converts to 0.028316846592 m3/s; reference temperature/pressure are unknown. Per-column oxygen capacity 0.043 m3 has unknown reference conditions; per-column water capacity is 1.8 kg. Neither capacity supplies a rate or a part mass.",
    "Task-required inner/outer door mutual exclusion is an explicit configured simulation interlock, not a claim of universally standard manufacturer equipment. Pressure-equalization door interlocks, seals, glove mechanics and robot/hardware operation are not modeled.",
    "Door closed=0 rad and open=pi/2 rad about each modeled local Z hinge is an engineering_assumption for geometric articulation, not a measured travel angle.",
    "Antechamber cycle boundaries must be explicitly configured with evidence; no manufacturer evacuation endpoint, fill endpoint, chamber volume or timing is invented. Configured evacuation endpoints and supplied boundary pressures respect the shared source 200 Pa ultimate floor. One cycle is counted only after newly supplied evacuation and backfill pressure boundaries in that order, consumed by host.step.",
    "Cycle simulation requires an externally supplied instrument.vacuubrand_pc3001_vario_select source ID, fresh pressure boundaries, source-running and inert-gas-ready flags. No internal pump is created. The task's shared 2 m3/h source is not asserted equivalent to an OEM pump or its cycle performance.",
    "A boundary sample can cause at most one phase transition and cannot be reused; time alone cannot complete a cycle. Source/gas unavailability or stale data stalls the cycle. Stop/power-off retain completed cycles and current external pressure; restart explicitly starts a new counter.",
    "All instances in InertPureLabHost share only its trusted clock. The external vacuum-source solver is advanced separately by the world; this module never advances it or reads a hidden global source.",
    "Scene callbacks receive public states only. Command callback failure retains process state but external scene effects may be unknown; host-step callback failure retains committed time/process progress and raises InertPureLabSceneSynchronizationError.",
    "Session describe/read/invoke/write/logs filter process truth. Trusted Python memory/filesystem are outside this API boundary, so separate process/filesystem deployment is required for untrusted code agents.",
    "Request deduplication is in memory per principal/instance and is not durable across restart. No hardware, robot, collision or chemical-process validation is implied by runtime tests.",
]


def export_inert_purelab_contract_metadata():
    slots = copy.deepcopy(INERT_PURELAB_STATE_SLOTS)
    for sid, slot in slots.items():
        slot["read_binding"] = f"InertPureLabSession.read(instance_id=..., state_ids=['{sid}'])"
        slot["sampling"] = {"mode": "instant", "time_domain": "simulation", "window": None}
        slot["reference_conditions"] = "Gauge pressure relative to external ambient" if sid.startswith("pressure.") else "Absolute external antechamber pressure; no internal pump" if sid in ("antechamber.pressure_pa", "antechamber.indicated_pressure_pa") else "Local hinge Z; closed=0 rad, open=pi/2 rad; engineering_assumption" if sid.endswith("_angle_rad") else "Mole fraction; catalog <1 ppm is a target, not a guaranteed observation" if "mole_fraction" in sid else "Reference temperature and pressure unknown; no rate calculation" if sid in ("purification.flow_m3_s", "purge.flow_m3_s", "purification.oxygen_removed_m3") else None
        if slot["origin"] == "sensor":
            slot["observation_note_zh"] = "仅返回宿主带证据注入的观测；初值null/unavailable；时间落后宿主为stale，绝不从真值自动复制。"
    summaries = {
        "power.set": "设置电源；断电关闭净化/吹扫并停止循环，保留已完成计数",
        "pressure.set": "设置±1500 Pa表压目标；不立即改变箱压或生成传感读数",
        "purification.set": "设置净化控制开关；未建净化速率且不承诺水氧目标",
        "purge.set": "设置吹扫控制开关；须有新鲜外部惰性气体就绪边界；不计算用气量",
        "antechamber.set_cycles": "设置1到9次抽气—回填循环；运行中禁止改计数目标",
        "antechamber.cycle.start": "启动新一组循环控制，计数归零；只在后续新鲜边界完成完整抽充后计数",
        "antechamber.cycle.stop": "停止当前循环控制，保留已完成循环数和外部压力",
        "antechamber.inner_door.set": "设置内门开闭；两门互锁且循环运行时禁止开门",
        "antechamber.outer_door.set": "设置外门开闭；两门互锁且循环运行时禁止开门",
        "regeneration.start": "再生过程、温度、气体配方及速率未建模；明确返回unsupported",
    }
    components = {"power.set": ["controller"], "pressure.set": ["gas_manifold", "controller"], "purification.set": ["purifier_columns", "circulation_blower", "controller"], "purge.set": ["gas_manifold", "controller"], "antechamber.set_cycles": ["antechamber", "controller"], "antechamber.cycle.start": ["antechamber", "gas_manifold", "controller"], "antechamber.cycle.stop": ["antechamber", "controller"], "antechamber.inner_door.set": ["inner_door", "antechamber"], "antechamber.outer_door.set": ["outer_door", "antechamber"], "regeneration.start": ["purifier_columns", "controller"]}
    extra_effects = {
        "power.set": ("purification.enabled", "purge.enabled", "antechamber.cycle_active", "antechamber.phase"),
        "antechamber.cycle.start": ("antechamber.completed_cycles", "antechamber.phase"),
        "antechamber.cycle.stop": ("antechamber.phase",),
        "antechamber.inner_door.set": ("antechamber.inner_door_angle_rad",),
        "antechamber.outer_door.set": ("antechamber.outer_door_angle_rad",),
    }
    preconditions = {
        "power.set": [], "pressure.set": [], "antechamber.set_cycles": ["Cycle is inactive"],
        "purification.set": ["Enabling requires power; this is a control request, not a purification-rate solver"],
        "purge.set": ["Enabling requires power and a current host boundary with inert gas ready"],
        "antechamber.cycle.start": ["Power on; both doors closed; no active cycle; configured thresholds; connected shared external vacuum source; current boundary with running source, gas ready and pressure above evacuation endpoint"],
        "antechamber.cycle.stop": [],
        "antechamber.inner_door.set": ["Opening requires outer door closed and no active cycle"],
        "antechamber.outer_door.set": ["Opening requires inner door closed and no active cycle"],
        "regeneration.start": [],
    }
    examples = {"power.set": {"enabled": True}, "pressure.set": {"pressure_pa": 100.0}, "purification.set": {"enabled": True}, "purge.set": {"enabled": True}, "antechamber.set_cycles": {"cycles": 3}, "antechamber.cycle.start": {}, "antechamber.cycle.stop": {}, "antechamber.inner_door.set": {"open": True}, "antechamber.outer_door.set": {"open": True}, "regeneration.start": {}}
    caps = {}
    for cap, args in INERT_PURELAB_CAPABILITY_ARGUMENTS.items():
        unsupported = cap == "regeneration.start"
        primary = INERT_PURELAB_CAPABILITY_RESULT_SLOT.get(cap)
        properties = {}
        if primary:
            key, sid = primary
            result_schema = copy.deepcopy(slots[sid]["value_schema"])
            if result_schema.get("type") in ("number", "integer"):
                result_schema["x-unit"] = slots[sid]["unit"]
            properties[key] = result_schema
        effects = [] if not primary else [{"state_id": s, "timing": "immediate", "description_zh": summaries[cap]} for s in (primary[1], *extra_effects.get(cap, ()))]
        if cap == "antechamber.cycle.start":
            effects += [{"state_id": s, "timing": "on_step", "description_zh": "宿主消费新鲜且未用过的外部边界；抽气与回填均完成才计数"} for s in ("antechamber.completed_cycles", "antechamber.phase", "antechamber.cycle_active")]
        caps[cap] = {"availability": "unsupported" if unsupported else "implemented", "visibility": "agent", "kind": "device_command", "description_zh": summaries[cap], "component_ids": components[cap],
            "input_schema": {"type": "object", "properties": copy.deepcopy(args), "required": list(args), "additionalProperties": False},
            "output_schema": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False},
            "preconditions_zh": preconditions[cap], "state_effects": effects, "observes": [] if unsupported else [primary[1]],
            "example_arguments": [examples[cap]], "binding": None if unsupported else f"basic40_runtime_vacuum.InertPureLabSession.invoke(instance_id=..., capability_id='{cap}', arguments=..., request_id=...)",
            "completion_zh": "不执行并返回unsupported" if unsupported else "同步成功仅确认控制状态写入；循环完成另读completed_cycles/phase，净化与吹扫不计算纯度", "scene_states": list(extra_effects.get(cap, ())) if "door.set" in cap else []}
    params = {
        "box_pressure_gauge_min_pa": {"value": -1500, "unit": "Pa", "evidence": "catalog"},
        "box_pressure_gauge_max_pa": {"value": 1500, "unit": "Pa", "evidence": "catalog"},
        "oxygen_target_mole_fraction_below": {"value": 1e-6, "unit": "1", "evidence": "catalog", "source": "Target only, not a guaranteed reading"},
        "water_target_mole_fraction_below": {"value": 1e-6, "unit": "1", "evidence": "catalog", "source": "Target only, not a guaranteed reading"},
        "maximum_circulation_flow_m3_s": {"value": INERT_PURELAB_BLOWER_MAXIMUM_M3_S, "unit": "m^3/s", "evidence": "catalog", "source": "60 ft3/min * 0.028316846592 / 60; reference T/P unknown"},
        "per_column_oxygen_capacity_m3": {"value": .043, "unit": "m^3", "evidence": "catalog", "source": "43 L per column; reference T/P unknown"},
        "per_column_water_capacity_kg": {"value": 1.8, "unit": "kg", "evidence": "catalog", "source": "1800 g per column; not component mass"},
        "purification_rate": {"value": None, "unit": "1/s", "evidence": "unknown"},
        "leak_rate_pa_m3_s": {"value": None, "unit": "Pa*m^3/s", "evidence": "unknown"},
        "outgassing_rate_pa_m3_s": {"value": None, "unit": "Pa*m^3/s", "evidence": "unknown"},
        "antechamber_evacuate_pressure_pa": {"value": None, "unit": "Pa", "evidence": "unknown"},
        "antechamber_backfill_pressure_pa": {"value": None, "unit": "Pa", "evidence": "unknown"},
        "antechamber_volume_m3": {"value": None, "unit": "m^3", "evidence": "unknown"},
        "door_open_angle_rad": {"value": math.pi / 2, "unit": "rad", "evidence": "engineering_assumption"},
    }
    return {"asset_types": {INERT_PURELAB_ASSET_TYPE_ID: {"catalog_id": "CHEM-189", "model": "Inert | PureLab HE 2GB", "asset_version": "1.0.2", "parameters": params, "state_slots": slots, "capabilities": caps}}, "host_capabilities": InertPureLabHost.describe_host(), "limitations": list(INERT_PURELAB_LIMITATIONS)}


@dataclass
class _InertPureLabInstance:
    instance_id: str
    state: dict
    revision: int = 0
    observations: dict = field(default_factory=dict)
    boundary: dict | None = None
    thresholds: dict | None = None
    consumed_boundary: int = 0
    boundary_serial: int = 0


class InertPureLabSceneSynchronizationError(RuntimeError):
    """Host step committed process/time; external scene status is unknown."""


class InertPureLabHost:
    """Trusted host only; no implicit pressure solver, gas source or time advancement."""
    def __init__(self, *, scene_adapter=None):
        if scene_adapter is not None and not callable(scene_adapter):
            raise ValueError("Scene adapter must be callable")
        self.scene_adapter, self.time_s = scene_adapter, 0.0
        self._lock = threading.RLock()
        self._instances, self._principals, self._requests, self._logs = {}, {}, {}, {}
        self._scene_faults = []

    @staticmethod
    def describe_host():
        return {"sim.step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "basic40_runtime_vacuum.InertPureLabHost.step(dt_s)", "scope": "One shared PureLab host clock; fresh boundary samples drive phase transitions, not elapsed time alone", "input_schema": {"type": "object", "properties": {"dt_s": {"type": "number", "minimum": 0, "maximum": 3600, "x-unit": "s"}}, "required": ["dt_s"], "additionalProperties": False}},
            **{f"host.{method}": {"kind": "configuration", "visibility": "trusted_host_only", "binding": f"basic40_runtime_vacuum.InertPureLabHost.{method}"} for method in ("connect_vacuum_source", "configure_cycle_boundaries", "set_external_boundary", "set_observations", "set_process_truth", "sync_scene")}}

    @staticmethod
    def _public_state(state):
        return {k: copy.deepcopy(v) for k, v in state.items() if INERT_PURELAB_STATE_SLOTS[k]["visibility"] == "agent"}

    def create_instance(self, instance_id, asset_type_id=INERT_PURELAB_ASSET_TYPE_ID):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != INERT_PURELAB_ASSET_TYPE_ID:
                raise ValueError("Unique instance ID and PureLab type are required")
            state = {sid: None for sid in INERT_PURELAB_STATE_SLOTS}
            state.update({"power.enabled": False, "pressure.target_gauge_pa": 0.0,
                "purification.enabled": False, "purge.enabled": False, "regeneration.status": "unsupported",
                "vacuum.source_asset_type_id": INERT_PURELAB_EXTERNAL_VACUUM_TYPE_ID,
                "antechamber.target_cycles": 1, "antechamber.completed_cycles": 0, "antechamber.cycle_active": False, "antechamber.phase": "idle",
                "antechamber.inner_door_open": False, "antechamber.outer_door_open": False,
                "antechamber.inner_door_angle_rad": 0.0, "antechamber.outer_door_angle_rad": 0.0})
            if self.scene_adapter:
                self.scene_adapter(instance_id, self._public_state(state))
            self._instances[instance_id] = _InertPureLabInstance(instance_id, state)

    def register_principal(self, principal_id, *, visibility="agent", instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Unique trusted principal and supported visibility required")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set)) or any(not isinstance(i, str) or i not in self._instances for i in instance_ids)):
                raise ValueError("Instance grants must name registered instances")
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))
            self._logs[principal_id] = []

    def session(self, principal_id):
        if not isinstance(principal_id, str) or principal_id not in self._principals:
            raise ValueError("Principal must be provisioned by trusted host")
        return InertPureLabSession(self, principal_id)

    def _commit(self, inst, candidate):
        updates = {k: v for k, v in self._public_state(candidate.state).items() if v != inst.state[k]}
        if self.scene_adapter and updates:
            self.scene_adapter(inst.instance_id, copy.deepcopy(updates))
        candidate.revision = inst.revision + 1
        self._instances[inst.instance_id] = candidate

    @staticmethod
    def _evidence(evidence):
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("Explicit source or engineering_assumption evidence is required")

    def connect_vacuum_source(self, instance_id, source_instance_id, *, asset_type_id=INERT_PURELAB_EXTERNAL_VACUUM_TYPE_ID):
        with self._lock:
            inst = self._instances[instance_id]
            if inst.state["antechamber.cycle_active"] or not isinstance(source_instance_id, str) or not source_instance_id or source_instance_id == instance_id or asset_type_id != INERT_PURELAB_EXTERNAL_VACUUM_TYPE_ID:
                raise ValueError("Inactive cycle and distinct external shared source identity required")
            candidate = copy.deepcopy(inst)
            candidate.state["vacuum.source_instance_id"] = source_instance_id
            candidate.boundary = None
            candidate.state["antechamber.pressure_pa"] = None
            self._commit(inst, candidate)

    def configure_cycle_boundaries(self, instance_id, *, evacuation_pressure_pa, backfill_pressure_pa, evidence):
        with self._lock:
            inst = self._instances[instance_id]
            self._evidence(evidence)
            if inst.state["antechamber.cycle_active"] or not _number(evacuation_pressure_pa) or not _number(backfill_pressure_pa) or not 200 <= evacuation_pressure_pa < backfill_pressure_pa:
                raise ValueError("Inactive cycle and finite ordered absolute endpoints at or above the shared-source 200 Pa ultimate limit required")
            candidate = copy.deepcopy(inst)
            candidate.thresholds = {"evacuation_pressure_pa": float(evacuation_pressure_pa), "backfill_pressure_pa": float(backfill_pressure_pa), "evidence": evidence}
            self._commit(inst, candidate)

    def set_external_boundary(self, instance_id, *, pressure_pa, source_running, gas_supply_ready, evidence):
        with self._lock:
            inst = self._instances[instance_id]
            self._evidence(evidence)
            if not inst.state["vacuum.source_instance_id"] or not _number(pressure_pa) or pressure_pa < 200 or type(source_running) is not bool or type(gas_supply_ready) is not bool:
                raise ValueError("Connected external source, finite absolute pressure >=200 Pa and Boolean boundary flags required")
            candidate = copy.deepcopy(inst)
            candidate.boundary_serial += 1
            candidate.boundary = {"pressure_pa": float(pressure_pa), "source_running": source_running, "gas_supply_ready": gas_supply_ready, "sample_time_s": self.time_s, "serial": candidate.boundary_serial, "evidence": evidence}
            candidate.state["antechamber.pressure_pa"] = float(pressure_pa)
            self._commit(inst, candidate)

    def set_observations(self, instance_id, observations, *, evidence, quality="estimated"):
        """Trusted observation injection. Never copy process truth automatically."""
        with self._lock:
            inst = self._instances[instance_id]
            self._evidence(evidence)
            if not isinstance(observations, dict) or not observations or quality not in ("valid", "estimated"):
                raise ValueError("Nonempty sensor observations and valid/estimated provenance required")
            for sid, value in observations.items():
                if sid not in INERT_PURELAB_STATE_SLOTS or INERT_PURELAB_STATE_SLOTS[sid]["origin"] != "sensor" or not _inert_purelab_validate(value, INERT_PURELAB_STATE_SLOTS[sid]["value_schema"]):
                    raise ValueError("Only declared sensor values within their schemas may be injected")
            candidate = copy.deepcopy(inst)
            for sid, value in observations.items():
                candidate.state[sid] = value
                candidate.observations[sid] = {"sample_time_s": self.time_s, "quality": quality, "evidence": evidence}
            self._commit(inst, candidate)

    def set_process_truth(self, instance_id, values, *, evidence):
        """Trusted-only simulation initialization; separate from public observations."""
        with self._lock:
            inst = self._instances[instance_id]
            self._evidence(evidence)
            if not isinstance(values, dict) or not values:
                raise ValueError("Nonempty truth mapping required")
            for sid, value in values.items():
                if sid not in INERT_PURELAB_STATE_SLOTS or INERT_PURELAB_STATE_SLOTS[sid]["origin"] != "process_truth" or sid == "antechamber.pressure_pa" or not _inert_purelab_validate(value, INERT_PURELAB_STATE_SLOTS[sid]["value_schema"]):
                    raise ValueError("Truth values require declared schemas; antechamber pressure uses external boundary only")
            candidate = copy.deepcopy(inst)
            candidate.state.update(copy.deepcopy(values))
            self._commit(inst, candidate)

    def step(self, dt_s):
        with self._lock:
            if not _number(dt_s) or not 0 <= dt_s <= 3600:
                raise ValueError("Host step requires a finite 0..3600 s interval")
            if dt_s == 0:
                return
            old_time = self.time_s
            self.time_s += float(dt_s)
            failures = []
            for inst in self._instances.values():
                before = copy.deepcopy(inst.state)
                boundary = inst.boundary
                if inst.state["antechamber.cycle_active"] and boundary and boundary["sample_time_s"] == old_time and boundary["serial"] > inst.consumed_boundary:
                    inst.consumed_boundary = boundary["serial"]
                    phase = inst.state["antechamber.phase"]
                    if phase == "evacuating" and boundary["source_running"] and boundary["pressure_pa"] <= inst.thresholds["evacuation_pressure_pa"]:
                        inst.state["antechamber.phase"] = "backfilling"
                    elif phase == "backfilling" and boundary["gas_supply_ready"] and boundary["pressure_pa"] >= inst.thresholds["backfill_pressure_pa"]:
                        inst.state["antechamber.completed_cycles"] += 1
                        done = inst.state["antechamber.completed_cycles"] >= inst.state["antechamber.target_cycles"]
                        inst.state["antechamber.phase"] = "completed" if done else "evacuating"
                        inst.state["antechamber.cycle_active"] = not done
                # Every nonzero step changes the snapshot revision because observation freshness changes.
                if dt_s or before != inst.state:
                    inst.revision += 1
                updates = {k: v for k, v in self._public_state(inst.state).items() if before[k] != v}
                if updates and self.scene_adapter:
                    try:
                        self.scene_adapter(inst.instance_id, copy.deepcopy(updates))
                    except Exception:
                        failures.append(inst.instance_id)
            if failures:
                self._scene_faults.extend({"instance_id": i, "time_s": self.time_s, "phase": "host_step"} for i in failures)
                raise InertPureLabSceneSynchronizationError("Host time/process committed; one or more scene callbacks failed")

    def sync_scene(self, instance_ids=None):
        with self._lock:
            if self.scene_adapter is None:
                raise ValueError("No scene adapter installed")
            ids = list(self._instances) if instance_ids is None else instance_ids
            if not isinstance(ids, (list, tuple)) or any(not isinstance(i, str) or i not in self._instances for i in ids):
                raise ValueError("Known instance synchronization set required")
            for i in ids:
                self.scene_adapter(i, self._public_state(self._instances[i].state))

    def debug_snapshot(self):
        with self._lock:
            return copy.deepcopy({"time_s": self.time_s, "instances": {i: vars(v) for i, v in self._instances.items()}, "scene_faults": self._scene_faults})


def _inert_purelab_validate(value, schema):
    if "anyOf" in schema:
        return any(_inert_purelab_validate(value, branch) for branch in schema["anyOf"])
    kind = schema.get("type")
    if kind == "null":
        return value is None
    if kind == "boolean":
        return type(value) is bool
    if kind in ("number", "integer"):
        return _number(value) and (kind != "integer" or type(value) is int) and schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf)
    if kind == "string":
        return isinstance(value, str) and ("enum" not in schema or value in schema["enum"]) and ("const" not in schema or value == schema["const"])
    return False


class InertPureLabSession:
    """Public protocol; trusted principal registration defines visibility/grants."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal_id = host, principal_id

    def _visible(self, slot):
        return slot["visibility"] == "agent" or self.__host._principals[self.__principal_id][0] in ("debug", "evaluator")

    def _instance(self, instance_id):
        allowed = self.__host._principals[self.__principal_id][1]
        return self.__host._instances.get(instance_id) if isinstance(instance_id, str) and (allowed is None or instance_id in allowed) else None

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": "1.0.0", "instance_id": instance_id if isinstance(instance_id, str) else None, "request_id": request_id if isinstance(request_id, str) else None,
            "operation_id": None, "status": status, "state_revision": inst.revision if inst else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": {} if result is None else result, "observations": {} if observations is None else observations, "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"INVALID_ARGUMENT": "Arguments do not match the declared schema", "UNKNOWN_INSTANCE": "Unknown or inaccessible instance", "UNKNOWN_CAPABILITY": "Unknown capability", "ACCESS_DENIED": "State unavailable to this session", "REQUEST_CONFLICT": "Request ID already used with different content", "PRECONDITION_FAILED": "Required power, door interlock or current external source/boundary condition is not satisfied", "BUSY": "Antechamber cycle is active", "UNSUPPORTED": "Regeneration process has no implemented model", "DEVICE_ERROR": "Scene callback failed; external scene effects are unknown"}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": messages[code], "retryable": False, "side_effects": "unknown" if status == "failed" else "none", "known_progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != INERT_PURELAB_ASSET_TYPE_ID:
                return self._error(None, "INVALID_ARGUMENT")
            spec = export_inert_purelab_contract_metadata()["asset_types"][INERT_PURELAB_ASSET_TYPE_ID]
            visible = {sid: slot for sid, slot in spec["state_slots"].items() if self._visible(slot)}
            caps = copy.deepcopy(spec["capabilities"])
            for cap in caps.values():
                cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in visible]
                cap["observes"] = [s for s in cap["observes"] if s in visible]
            return {"protocol_version": "1.0.0", "asset_type_id": INERT_PURELAB_ASSET_TYPE_ID, "asset_version": "1.0.2", "instance_id": instance_id, "catalog_id": "CHEM-189", "model": spec["model"], "adapter": "basic40_runtime_vacuum.InertPureLabSession", "capabilities": caps, "state_slots": visible, "limitations": list(INERT_PURELAB_LIMITATIONS)}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            ids = [s for s, slot in INERT_PURELAB_STATE_SLOTS.items() if self._visible(slot)] if state_ids is None else state_ids
            if not isinstance(ids, list) or any(not isinstance(s, str) for s in ids) or len(set(ids)) != len(ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(s not in INERT_PURELAB_STATE_SLOTS or not self._visible(INERT_PURELAB_STATE_SLOTS[s]) for s in ids):
                return self._error(instance_id, "ACCESS_DENIED")
            obs = {}
            for sid in ids:
                slot, value = INERT_PURELAB_STATE_SLOTS[sid], copy.deepcopy(inst.state[sid])
                sensor = inst.observations.get(sid)
                sample_time = sensor["sample_time_s"] if sensor else inst.boundary["sample_time_s"] if sid == "antechamber.pressure_pa" and inst.boundary else self.__host.time_s
                quality = "unavailable" if value is None else "stale" if sensor and sample_time < self.__host.time_s else sensor["quality"] if sensor else "valid"
                item = {"value": value, "unit": slot["unit"], "quality": quality, "sample_time_s": sample_time, "sample_window_s": None}
                if value is None:
                    item["reason"] = "No trusted sensor observation supplied" if slot["origin"] == "sensor" else "Quantity has no supplied process model or external boundary"
                if quality == "stale":
                    item["reason"] = "Observation predates the current host clock; no automatic sensor synthesis"
                obs[sid] = item
            return self._envelope(instance_id, observations=obs)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(request_id, str) or not request_id or len(request_id) > 256 or not isinstance(capability_id, str) or not isinstance(arguments, dict):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = hashlib.sha256(json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            except (TypeError, ValueError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = (self.__principal_id, instance_id, request_id)
            if key in host._requests:
                previous, response = host._requests[key]
                return copy.deepcopy(response) if digest == previous else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            result = self._invoke_checked(inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, copy.deepcopy(result))
            host._logs[self.__principal_id].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id, "response": copy.deepcopy(result)})
            return result

    def _invoke_checked(self, inst, cap, args, request_id):
        host = self.__host
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request_id, status)
        if cap not in INERT_PURELAB_CAPABILITY_ARGUMENTS:
            return fail("UNKNOWN_CAPABILITY")
        expected = INERT_PURELAB_CAPABILITY_ARGUMENTS[cap]
        if set(args) != set(expected) or any(not _inert_purelab_validate(args[k], schema) for k, schema in expected.items()):
            return fail("INVALID_ARGUMENT")
        if cap == "regeneration.start":
            return fail("UNSUPPORTED", "unsupported")
        state = inst.state
        if cap == "antechamber.set_cycles" and state["antechamber.cycle_active"]:
            return fail("BUSY")
        if cap in ("purification.set", "purge.set") and args["enabled"] and not state["power.enabled"]:
            return fail("PRECONDITION_FAILED")
        if cap == "purge.set" and args["enabled"] and (not inst.boundary or inst.boundary["sample_time_s"] != host.time_s or not inst.boundary["gas_supply_ready"]):
            return fail("PRECONDITION_FAILED")
        if cap.endswith("door.set") and args["open"]:
            other = "outer" if cap == "antechamber.inner_door.set" else "inner"
            if state[f"antechamber.{other}_door_open"] or state["antechamber.cycle_active"]:
                return fail("PRECONDITION_FAILED")
        if cap == "antechamber.cycle.start":
            if state["antechamber.cycle_active"]:
                return fail("BUSY")
            b = inst.boundary
            if not state["power.enabled"] or state["antechamber.inner_door_open"] or state["antechamber.outer_door_open"] or not state["vacuum.source_instance_id"] or not inst.thresholds or not b or b["sample_time_s"] != host.time_s or not b["source_running"] or not b["gas_supply_ready"] or b["pressure_pa"] <= inst.thresholds["evacuation_pressure_pa"]:
                return fail("PRECONDITION_FAILED")
        candidate = copy.deepcopy(inst)
        result_key, sid = INERT_PURELAB_CAPABILITY_RESULT_SLOT[cap]
        value = True if cap == "antechamber.cycle.start" else False if cap == "antechamber.cycle.stop" else args[result_key]
        candidate.state[sid] = value
        if cap == "power.set" and not value:
            candidate.state["purification.enabled"] = False
            candidate.state["purge.enabled"] = False
            if candidate.state["antechamber.cycle_active"]:
                candidate.state["antechamber.cycle_active"] = False
                candidate.state["antechamber.phase"] = "stopped"
        if cap == "antechamber.cycle.start":
            candidate.state["antechamber.completed_cycles"] = 0
            candidate.state["antechamber.phase"] = "evacuating"
            candidate.consumed_boundary = candidate.boundary_serial
        if cap == "antechamber.cycle.stop" and state["antechamber.cycle_active"]:
            candidate.state["antechamber.phase"] = "stopped"
        if cap.endswith("door.set"):
            door = "inner" if cap == "antechamber.inner_door.set" else "outer"
            candidate.state[f"antechamber.{door}_door_angle_rad"] = math.pi / 2 if value else 0.0
        try:
            host._commit(inst, candidate)
        except Exception:
            return fail("DEVICE_ERROR", "failed")
        return self._envelope(inst.instance_id, request_id, result={result_key: value})

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(state_id, str) or state_id not in INERT_PURELAB_STATE_SLOTS or not self._visible(INERT_PURELAB_STATE_SLOTS[state_id]):
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = INERT_PURELAB_STATE_SLOTS[state_id]["write_via"]
            if not route:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return copy.deepcopy(self.__host._logs[self.__principal_id])


def validate_inert_purelab_runtime(output=None):
    """Run only CHEM-189 tests through real protocol entrypoints and trusted host."""
    import importlib.util
    import io
    import unittest
    test_file = Path(__file__).resolve().parents[1] / "tests" / "test_basic40_vacuum.py"
    spec = importlib.util.spec_from_file_location("test_basic40_vacuum_inert_purelab_validation", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(module.Basic40InertPureLabTests))
    passed = result.wasSuccessful()
    report = {"schema_version": "1.0.0", "catalog_id": "CHEM-189", "recorded_at": datetime.now(timezone.utc).isoformat(), "method": "unittest discovery of Basic40InertPureLabTests: real InertPureLabSession describe/read/invoke/write, boundary-driven cycle transitions, host-only clock, scene callback filtering/failure, instance/access isolation", "environment": {"python": platform.python_version(), "platform": platform.platform()}, "tests_run": result.testsRun, "result": "passed" if passed else "failed", "failures": len(result.failures), "errors": len(result.errors), "log": stream.getvalue(), "layers": {"process_control": "passed" if passed else "failed", "host": "passed" if passed else "failed", "scene_callback": "passed" if passed else "failed", "blender_evaluated_scene": "not_run", "collision": "not_run", "robot": "not_run", "hardware": "not_run", "purification_kinetics": "not_run"}, "data_classification": "Synthetic engineering_assumption fixtures only, no deployed instance truth", "limitations": list(INERT_PURELAB_LIMITATIONS)}
    report["checked_at"] = report["recorded_at"]
    report["checks"] = [{"check_id": "inert_purelab_runtime_" + layer, "layer": layer, "status": "passed" if passed else "failed", "capability_ids": list(INERT_PURELAB_CAPABILITY_RESULT_SLOT), "method": report["method"], "environment": report["environment"], "checked_at": report["recorded_at"], "evidence_path": "outputs/basic40_batch02/t2_vacuum/inert_purelab_he/runtime_validation.json"} for layer in ("process", "host")]
    report["checks"].append({"check_id": "inert_purelab_regeneration_rejection", "layer": "host", "status": "passed" if passed else "failed", "capability_ids": ["regeneration.start"], "method": "Unsupported regeneration is rejected atomically; this does not validate regeneration process", "environment": report["environment"], "checked_at": report["recorded_at"]})
    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report

# CHEM-031 Extrahera Classic. Append-only independent adapter, not a subclass of another instrument.
EXTRAHERA_ASSET_TYPE_ID = "instrument.biotage_extrahera_classic"
EXTRAHERA_EXTERNAL_VACUUM_TYPE_ID = "instrument.vacuubrand_pc3001_vario_select"
EXTRAHERA_MAXIMUM_STROKE_M3 = 1e-6
EXTRAHERA_JOINTS = {"gantry.x_m": (0.0, .44, .22), "gantry.y_m": (0.0, .28, .18), "gantry.z_m": (0.0, .20, .17), "manifold.z_m": (0.0, .08, .05)}

EXTRAHERA_SCENE_STATE_IDS = tuple(EXTRAHERA_JOINTS) + ("power.enabled", "pressure.target_pa", "method.phase", "deck.format", "deck.cartridge_volume_m3")


def _extrahera_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _extrahera_number(unit, **limits):
    return {"type": "number", "x-unit": unit, **limits}


def _extrahera_nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def _extrahera_slot(unit, origin, schema, visibility="agent", capability=None, parameter=None):
    return {"unit": unit, "origin": origin, "value_schema": schema, "visibility": visibility,
            "access": "read_write" if capability else "read_only",
            "write_via": {"capability_id": capability, "parameter": parameter, "fixed_arguments": {}} if capability else None}


EXTRAHERA_STATE_SLOTS = {
    "power.enabled": _extrahera_slot("1", "setpoint", {"type": "boolean"}, capability="power.set", parameter="enabled"),
    "pressure.target_pa": _extrahera_slot("Pa", "setpoint", _extrahera_number("Pa", minimum=0, maximum=500000), capability="pressure.set", parameter="pressure_pa"),
    "pressure.indicated_pa": _extrahera_slot("Pa", "sensor", {"type": "null"}),
    "deck.format": _extrahera_slot("1", "setpoint", _extrahera_nullable({"type": "string", "enum": ["cartridges24", "plate96", "plate48"]})),
    "deck.cartridge_volume_m3": _extrahera_slot("m^3", "setpoint", _extrahera_nullable(_extrahera_number("m^3", enum=[1e-6, 3e-6, 6e-6]))),
    "deck.collection_plate": _extrahera_slot("1", "setpoint", _extrahera_nullable({"type": "string"})),
    "deck.solvent_positions": _extrahera_slot("1", "setpoint", {"type": "array", "items": {"type": "string"}}),
    "utilities.vacuum_ready": _extrahera_slot("1", "derived", {"type": "boolean"}),
    "utilities.compressed_air_ready": _extrahera_slot("1", "derived", {"type": "boolean"}),
    "utilities.nitrogen_ready": _extrahera_slot("1", "derived", {"type": "boolean"}),
    "vacuum.source_asset_type_id": _extrahera_slot("1", "derived", {"type": "string", "const": EXTRAHERA_EXTERNAL_VACUUM_TYPE_ID}),
    "vacuum.source_instance_id": _extrahera_slot("1", "derived", _extrahera_nullable({"type": "string"})),
    "method.configured": _extrahera_slot("1", "derived", {"type": "boolean"}),
    "method.status": _extrahera_slot("1", "derived", {"type": "string", "enum": ["idle", "running", "completed", "cancelled", "failed"]}),
    "method.phase": _extrahera_slot("1", "derived", {"type": "string", "enum": ["idle", "load", "wash", "elute", "completed", "cancelled", "failed"]}),
    "method.completed_strokes": _extrahera_slot("1", "derived", {"type": "integer", "minimum": 0, "x-unit": "1"}),
    "sample.status": _extrahera_slot("1", "derived", {"type": "string", "enum": ["idle", "running", "completed", "cancelled", "failed"]}),
    "operation.active_id": _extrahera_slot("1", "derived", _extrahera_nullable({"type": "string"})),
    "chemistry.recovery_fraction": _extrahera_slot("1", "derived", {"type": "null"}),
    "chemistry.adsorbed_mass_kg": _extrahera_slot("kg", "process_truth", {"type": "null"}, "debug"),
}
for _extrahera_sid, (_extrahera_lo, _extrahera_hi, _) in EXTRAHERA_JOINTS.items():
    EXTRAHERA_STATE_SLOTS[_extrahera_sid] = _extrahera_slot("m", "scene", _extrahera_number("m", minimum=_extrahera_lo, maximum=_extrahera_hi), capability="manifold.set_position" if _extrahera_sid == "manifold.z_m" else None, parameter="z_m" if _extrahera_sid == "manifold.z_m" else None)
for _extrahera_name in ("sources", "pipette", "extraction", "waste", "collection", "initial_total"):
    EXTRAHERA_STATE_SLOTS[f"fluid.{_extrahera_name}_m3"] = _extrahera_slot("m^3", "process_truth", _extrahera_nullable(_extrahera_number("m^3", minimum=0)), "debug")

_EXTRAHERA_STROKE_SCHEMA = _extrahera_object({
    "phase": {"type": "string", "enum": ["load", "wash", "elute"]},
    "solvent_position": {"type": "string"},
    "volume_m3": _extrahera_number("m^3", exclusiveMinimum=0, maximum=EXTRAHERA_MAXIMUM_STROKE_M3),
    "flow_m3_s": _extrahera_number("m^3/s", exclusiveMinimum=0),
    "drain_flow_m3_s": _extrahera_number("m^3/s", exclusiveMinimum=0),
    "repetitions": {"type": "integer", "minimum": 1, "maximum": 1000, "x-unit": "1", "description": "Software workload limit: at most 1000 repetitions per stage, not a manufacturer specification"},
})
EXTRAHERA_CAPABILITY_ARGUMENTS = {
    "power.set": {"enabled": {"type": "boolean"}},
    "deck.configure": {"format": {"type": "string", "enum": ["cartridges24", "plate96", "plate48"]}, "cartridge_volume_m3": _extrahera_nullable(_extrahera_number("m^3", enum=[1e-6, 3e-6, 6e-6])), "collection_plate": {"type": "string"}, "solvent_positions": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
    "gantry.move": {axis: _extrahera_number("m", minimum=EXTRAHERA_JOINTS[f"gantry.{axis}"][0], maximum=EXTRAHERA_JOINTS[f"gantry.{axis}"][1]) for axis in ("x_m", "y_m", "z_m")},
    "manifold.set_position": {"z_m": _extrahera_number("m", minimum=0, maximum=.08)},
    "pressure.set": {"pressure_pa": _extrahera_number("Pa", minimum=0, maximum=500000, description="Control value in 10000 Pa increments; manufacturer pressure reference is unspecified; never an actual-pressure observation.")},
    "sample.aspirate": {"solvent_position": {"type": "string"}, "volume_m3": _extrahera_number("m^3", exclusiveMinimum=0, maximum=1e-6), "flow_m3_s": _extrahera_number("m^3/s", exclusiveMinimum=0)},
    "sample.dispense": {"destination": {"type": "string", "enum": ["extraction", "waste", "collection"]}, "volume_m3": _extrahera_number("m^3", exclusiveMinimum=0, maximum=1e-6), "flow_m3_s": _extrahera_number("m^3/s", exclusiveMinimum=0)},
    "method.configure": {"steps": {"type": "array", "items": _EXTRAHERA_STROKE_SCHEMA, "minItems": 3, "maxItems": 3}},
    "method.start": {}, "method.stop": {},
}
EXTRAHERA_LIMITATIONS = [
    "CHEM-031 Biotage Extrahera Classic: 0.860 x 0.570 x 0.730 m, total mass 75 kg. Part masses, capacities and joint dynamics are unknown; whole-machine mass is not a part mass.",
    "Maximum commanded pipetting stroke is 1000 uL = 1e-6 m3. Catalog +/-1% accuracy and 1% CV apply only at 1000 uL; no noise/error model or accuracy guarantee is applied at any volume.",
    "Pressure control range is 0..500000 Pa in 10000 Pa increments. Manufacturer reference (absolute or gauge) is unspecified. The adapter writes a control value only; indicated pressure remains null.",
    "External vacuum, compressed air and nitrogen readiness are trusted host boundary flags, not measured utility performance. Shared PC3001 is an external vacuum source only; there is no internal pump or internal compressed-air/nitrogen source.",
    "Rack choices are 24 cartridges of 1/3/6 mL or a 96/48-well plate; physical capacity of each unspecified plate well and all waste/collection containers remains unknown. Solvent IDs are logical locations supplied by the user, not invented physical positions.",
    "engineering_assumption: one aggregate liquid balance across supplied solvent inventories, a <=1000 uL pipette, extraction holdup, waste and collection; no per-well/per-cartridge solver. User-supplied positive rates drive transfer and drainage; pressure is not converted to flow.",
    "engineering_assumption: method order is exactly load, wash, elute. Each stage repeats a user-configured <=1000 uL stroke (1..1000 repetitions is a software workload limit, not a catalog specification); aspirate, dispense and drain execute in that order. Load/wash drain to waste, elute drains to the selected collection plate. No sorption, breakthrough, analyte mass balance, recovery, separation efficiency, evaporation, fluid resistance or solvent chemistry is simulated.",
    "Only host.step advances shared time or liquid transfer. Methods and sample operations return accepted, not completed; poll/cancel are principal-and-instance scoped. Cancellation, utility loss and power-off retain transferred liquids and completed stroke count; no implicit rollback.",
    "engineering_assumption: gantry and manifold ranges, instantaneous joint targets and pressure/motion interlocks are geometric control approximations, not measured manufacturer motion limits, motor dynamics or collision checking. Methods do not move the gantry automatically; spatial alignment and robotic routing are outside scope.",
    "Unknown volume capacities are never replaced by zero or manufacturer whole-machine data. Liquid truth is null until one explicit trusted initialization establishes empty process vessels and supplied source inventories; initialization cannot be repeated on that instance.",
    "Scene callbacks receive only four geometric coordinates and public power/pressure-setpoint/method-phase/deck-selection values. Command scene failure leaves process state unchanged but may have unknown external scene effects. Host-step scene failure retains committed time and liquid progress and raises ExtraheraSceneSynchronizationError. Runtime scene callback tests do not validate Blender; actual evaluated Blender validation is separate.",
    "Session surfaces filter process truth and return copies. Trusted Python host memory and local files are outside this API boundary; untrusted Python/filesystem access requires process/filesystem isolation. In-memory deduplication is not durable across restarts.",
    "No hardware, robot, collision, fluid-solid interaction or analytical chemistry validation is implied by this runtime.",
]


def _extrahera_validate(value, schema):
    if "anyOf" in schema:
        return any(_extrahera_validate(value, branch) for branch in schema["anyOf"])
    kind = schema.get("type")
    if kind == "null": return value is None
    if kind == "boolean": valid = type(value) is bool
    elif kind in ("number", "integer"):
        try: valid = type(value) in (int, float) and math.isfinite(value) and (kind != "integer" or type(value) is int)
        except (OverflowError, ValueError): valid = False
        if valid:
            valid = schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf) and value > schema.get("exclusiveMinimum", -math.inf) and value < schema.get("exclusiveMaximum", math.inf)
    elif kind == "string": valid = isinstance(value, str)
    elif kind == "array": valid = isinstance(value, list) and schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf) and all(_extrahera_validate(v, schema["items"]) for v in value)
    elif kind == "object": valid = isinstance(value, dict) and set(value) == set(schema["properties"]) and all(_extrahera_validate(value[k], s) for k, s in schema["properties"].items())
    else: return False
    return valid and ("enum" not in schema or value in schema["enum"]) and ("const" not in schema or value == schema["const"])


@dataclass
class _ExtraheraInstance:
    instance_id: str
    state: dict
    revision: int = 0
    sources: dict | None = None
    source_evidence: str | None = None
    utility_evidence: str | None = None
    method: list | None = None
    operation_serial: int = 0
    operations: dict = field(default_factory=dict)
    template_samples: dict = field(default_factory=dict)  # 2026-10 optional template analytes per source position


class ExtraheraSceneSynchronizationError(RuntimeError):
    pass


class ExtraheraHost:
    """Trusted shared clock, utility boundaries and initial liquid inventories."""
    def __init__(self, *, scene_adapter=None):
        if scene_adapter is not None and not callable(scene_adapter): raise ValueError("Callable scene adapter required")
        self.scene_adapter, self.time_s = scene_adapter, 0.0
        self._instances, self._principals, self._requests, self._logs = {}, {}, {}, {}
        self._lock = threading.RLock()
        self._scene_faults = []

    @staticmethod
    def describe_host():
        return {name: {"visibility": "trusted_host_only", "binding": "basic40_runtime_vacuum.ExtraheraHost." + name} for name in ("step", "create_instance", "register_principal", "connect_vacuum_source", "set_utilities", "initialize_liquids", "sync_scene")}

    @staticmethod
    def _scene(state): return {sid: state[sid] for sid in EXTRAHERA_SCENE_STATE_IDS}

    def create_instance(self, instance_id, asset_type_id=EXTRAHERA_ASSET_TYPE_ID):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id.strip() or instance_id in self._instances or asset_type_id != EXTRAHERA_ASSET_TYPE_ID: raise ValueError("Unique Extrahera instance required")
            state = {sid: None for sid in EXTRAHERA_STATE_SLOTS}
            state.update({"power.enabled": False, "pressure.target_pa": 0.0, "deck.solvent_positions": [], "utilities.vacuum_ready": False, "utilities.compressed_air_ready": False, "utilities.nitrogen_ready": False, "vacuum.source_asset_type_id": EXTRAHERA_EXTERNAL_VACUUM_TYPE_ID, "method.configured": False, "method.status": "idle", "method.phase": "idle", "method.completed_strokes": 0, "sample.status": "idle"})
            state.update({sid: bounds[2] for sid, bounds in EXTRAHERA_JOINTS.items()})
            if self.scene_adapter: self.scene_adapter(instance_id, self._scene(state))
            self._instances[instance_id] = _ExtraheraInstance(instance_id, state)

    def register_principal(self, principal_id, *, visibility="agent", instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"): raise ValueError("Unique trusted identity required")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set)) or any(not isinstance(i, str) or i not in self._instances for i in instance_ids)): raise ValueError("Known instance grants required")
            self._principals[principal_id] = (visibility, None if instance_ids is None else frozenset(instance_ids))
            self._logs[principal_id] = []

    def session(self, principal_id):
        if not isinstance(principal_id, str) or principal_id not in self._principals: raise ValueError("Host-provisioned identity required")
        return ExtraheraSession(self, principal_id)

    def _commit(self, inst, candidate):
        updates = {sid: candidate.state[sid] for sid in EXTRAHERA_SCENE_STATE_IDS if candidate.state[sid] != inst.state[sid]}
        if updates and self.scene_adapter: self.scene_adapter(inst.instance_id, copy.deepcopy(updates))
        candidate.revision = inst.revision + 1
        self._instances[inst.instance_id] = candidate

    @staticmethod
    def _evidence(value):
        if not isinstance(value, str) or not value.strip(): raise ValueError("Explicit evidence required")

    def connect_vacuum_source(self, instance_id, source_instance_id, *, asset_type_id=EXTRAHERA_EXTERNAL_VACUUM_TYPE_ID):
        with self._lock:
            inst = self._instances[instance_id]
            if inst.state["operation.active_id"] or not isinstance(source_instance_id, str) or not source_instance_id.strip() or source_instance_id == instance_id or asset_type_id != EXTRAHERA_EXTERNAL_VACUUM_TYPE_ID: raise ValueError("Distinct external vacuum source and idle instrument required")
            candidate = copy.deepcopy(inst)
            candidate.state["vacuum.source_instance_id"], candidate.state["utilities.vacuum_ready"] = source_instance_id, False
            self._commit(inst, candidate)

    def set_utilities(self, instance_id, *, vacuum_ready, compressed_air_ready, nitrogen_ready, evidence):
        with self._lock:
            inst = self._instances[instance_id]
            self._evidence(evidence)
            if any(type(v) is not bool for v in (vacuum_ready, compressed_air_ready, nitrogen_ready)) or (vacuum_ready and inst.state["vacuum.source_instance_id"] is None): raise ValueError("Boolean utility boundaries and connected external vacuum required")
            candidate = copy.deepcopy(inst)
            candidate.state.update({"utilities.vacuum_ready": vacuum_ready, "utilities.compressed_air_ready": compressed_air_ready, "utilities.nitrogen_ready": nitrogen_ready})
            candidate.utility_evidence = evidence
            if not compressed_air_ready or not nitrogen_ready: candidate.state["pressure.target_pa"] = 0.0
            self._commit(inst, candidate)

    def initialize_liquids(self, instance_id, sources_m3, *, evidence):
        with self._lock:
            inst = self._instances[instance_id]
            self._evidence(evidence)
            if inst.sources is not None or inst.state["operation.active_id"] or not isinstance(sources_m3, dict) or not sources_m3: raise ValueError("One-time initial source inventory required")
            if any(not isinstance(k, str) or not k.strip() or not _extrahera_validate(v, _extrahera_number("m^3", minimum=0)) for k, v in sources_m3.items()): raise ValueError("Named nonnegative finite SI liquid inventories required")
            total = math.fsum(sources_m3.values())
            if not math.isfinite(total): raise ValueError("Finite aggregate inventory required")
            candidate = copy.deepcopy(inst)
            candidate.sources, candidate.source_evidence = {k: float(v) for k, v in sources_m3.items()}, evidence
            candidate.state.update({f"fluid.{name}_m3": 0.0 for name in ("pipette", "extraction", "waste", "collection")})
            candidate.state["fluid.sources_m3"] = candidate.state["fluid.initial_total_m3"] = total
            self._commit(inst, candidate)

    def assign_template_analyte(self, instance_id, *, source_position, analyte_id, concentration_kg_m3, sorbent_id, evidence):
        """2026-10 optional template path: a source liquid carries a library analyte; recovery from published data."""
        return _vacuum_templates("biotage_extrahera").assign_analyte(self, instance_id, source_position=source_position, analyte_id=analyte_id,
            concentration_kg_m3=concentration_kg_m3, sorbent_id=sorbent_id, evidence=evidence)

    def template_recovery_report(self, instance_id):
        """Trusted evaluator view: analyte identity, withdrawn mass and published-recovery expectation."""
        with self._lock:
            return _vacuum_templates("biotage_extrahera").report(copy.deepcopy(self._instances[instance_id]))

    @staticmethod
    def _terminate(inst, op, status):
        op["status"] = status
        inst.state["operation.active_id"] = None
        prefix = "method" if op["capability_id"] == "method.start" else "sample"
        inst.state[prefix + ".status"] = status
        if prefix == "method": inst.state["method.phase"] = status

    @staticmethod
    def _available(inst, op):
        s = inst.state
        needed = s["power.enabled"] and s["utilities.compressed_air_ready"]
        if op["capability_id"] in ("sample.aspirate", "method.start"): needed = needed and s["utilities.vacuum_ready"] and s["vacuum.source_instance_id"] is not None
        if op["capability_id"] == "method.start": needed = needed and s["utilities.nitrogen_ready"] and s["pressure.target_pa"] > 0 and s["manifold.z_m"] == 0
        return needed

    @staticmethod
    def _stroke_m3(inst, volume_m3):
        """Volume the pipette pump moves for a requested stroke; the plain CHEM-031 pump is exact (the touch-screen layer below adds its
        pump calibration and evaluator error)."""
        return volume_m3

    @staticmethod
    def _transfer(inst, source, destination, amount):
        if source.startswith("source:"):
            key = source[7:]
            inst.sources[key] = max(0.0, inst.sources[key] - amount)
            inst.state["fluid.sources_m3"] = math.fsum(inst.sources.values())
        else: inst.state[f"fluid.{source}_m3"] = max(0.0, inst.state[f"fluid.{source}_m3"] - amount)
        inst.state[f"fluid.{destination}_m3"] += amount

    def _advance(self, inst, dt_s):
        active = inst.state["operation.active_id"]
        if not active: return
        op = inst.operations[active]
        if not self._available(inst, op):
            self._terminate(inst, op, "failed")
            return
        remaining_time = dt_s
        while remaining_time > 0 and inst.state["operation.active_id"]:
            method = op["capability_id"] == "method.start"
            if method:
                step = inst.method[op["step_index"]]
                phase = op["subphase"]
                if phase == "aspirate": source, destination, rate = "source:" + step["solvent_position"], "pipette", step["flow_m3_s"]
                elif phase == "dispense": source, destination, rate = "pipette", "extraction", step["flow_m3_s"]
                else: source, destination, rate = "extraction", "collection" if step["phase"] == "elute" else "waste", step["drain_flow_m3_s"]
                target = self._stroke_m3(inst, step["volume_m3"])
            else:
                args = op["arguments"]
                source, destination = ("source:" + args["solvent_position"], "pipette") if op["capability_id"] == "sample.aspirate" else ("pipette", args["destination"])
                rate, target = args["flow_m3_s"], self._stroke_m3(inst, args["volume_m3"])
            available = inst.sources[source[7:]] if source.startswith("source:") else inst.state[f"fluid.{source}_m3"]
            needed = max(0.0, target - op["subvolume_m3"])
            # Divide before multiply: avoids overflowing rate * dt on valid finite inputs.
            elapsed_needed = needed / rate
            finished = elapsed_needed <= remaining_time
            amount = needed if finished else rate * remaining_time
            if amount > available + 1e-18:
                if available > 0: self._transfer(inst, source, destination, available)
                self._terminate(inst, op, "failed")
                return
            amount = min(amount, available)
            self._transfer(inst, source, destination, amount)
            op["subvolume_m3"] += amount
            remaining_time = max(0.0, remaining_time - elapsed_needed) if finished else 0.0
            if not finished: break
            op["subvolume_m3"] = 0.0
            if not method:
                self._terminate(inst, op, "completed")
            elif op["subphase"] == "aspirate": op["subphase"] = "dispense"
            elif op["subphase"] == "dispense": op["subphase"] = "drain"
            else:
                inst.state["method.completed_strokes"] += 1
                op["repetition"] += 1
                if op["repetition"] >= step["repetitions"]:
                    op["step_index"] += 1
                    op["repetition"] = 0
                if op["step_index"] == len(inst.method): self._terminate(inst, op, "completed")
                else:
                    op["subphase"] = "aspirate"
                    inst.state["method.phase"] = inst.method[op["step_index"]]["phase"]

    def step(self, dt_s):
        with self._lock:
            if not _extrahera_validate(dt_s, _extrahera_number("s", minimum=0, maximum=3600)): raise ValueError("Finite 0..3600 s host step required")
            if dt_s == 0: return
            updates = []
            for inst in self._instances.values():
                before = copy.deepcopy(inst.state)
                self._advance(inst, float(dt_s))
                if before != inst.state: inst.revision += 1
                delta = {sid: inst.state[sid] for sid in EXTRAHERA_SCENE_STATE_IDS if before[sid] != inst.state[sid]}
                if delta: updates.append((inst.instance_id, delta))
            self.time_s += float(dt_s)
            failures = []
            for instance_id, delta in updates:
                if self.scene_adapter:
                    try: self.scene_adapter(instance_id, copy.deepcopy(delta))
                    except Exception: failures.append(instance_id)
            if failures:
                self._scene_faults.extend({"instance_id": i, "time_s": self.time_s, "phase": "host_step"} for i in failures)
                raise ExtraheraSceneSynchronizationError("Host clock and process progress committed; scene synchronization failed")

    def sync_scene(self, instance_ids=None):
        with self._lock:
            if self.scene_adapter is None: raise ValueError("Scene adapter required")
            ids = list(self._instances) if instance_ids is None else instance_ids
            if not isinstance(ids, (list, tuple)) or any(not isinstance(i, str) or i not in self._instances for i in ids): raise ValueError("Known instance IDs required")
            for i in ids: self.scene_adapter(i, self._scene(self._instances[i].state))

    def debug_snapshot(self):
        with self._lock: return copy.deepcopy({"time_s": self.time_s, "instances": {i: vars(v) for i, v in self._instances.items()}, "scene_faults": self._scene_faults})


class ExtraheraSession:
    """Public describe/read/invoke/write/poll/cancel; identity comes from the host."""
    def __init__(self, host, principal_id): self.__host, self.__principal_id = host, principal_id

    def _visible(self, slot): return slot["visibility"] == "agent" or self.__host._principals[self.__principal_id][0] in ("debug", "evaluator")

    def _instance(self, instance_id):
        allowed = self.__host._principals[self.__principal_id][1]
        return self.__host._instances.get(instance_id) if isinstance(instance_id, str) and (allowed is None or instance_id in allowed) else None

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, error=None, observations=None, operation_id=None):
        inst = self._instance(instance_id)
        return {"protocol_version": "1.0.0", "instance_id": instance_id if isinstance(instance_id, str) else None, "request_id": request_id if isinstance(request_id, str) else None, "operation_id": operation_id, "status": status, "state_revision": inst.revision if inst else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s}, "result": {} if result is None else result, "observations": {} if observations is None else observations, "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"INVALID_ARGUMENT": "Arguments do not match the declared schema", "UNKNOWN_INSTANCE": "Unknown or inaccessible instance", "UNKNOWN_CAPABILITY": "Unknown capability", "ACCESS_DENIED": "Resource unavailable to this session", "REQUEST_CONFLICT": "Request ID already used with different content", "PRECONDITION_FAILED": "Configured deck, liquid, utility or motion condition is not satisfied", "BUSY": "An operation is already active", "DEVICE_ERROR": "Scene callback failed; external scene effects are unknown"}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": messages[code], "retryable": False, "side_effects": "unknown" if status == "failed" else "none", "known_progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None): return self._error(instance_id, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None: return self._error(instance_id, "UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != EXTRAHERA_ASSET_TYPE_ID: return self._error(None, "INVALID_ARGUMENT")
            spec = export_extrahera_contract_metadata()["asset_types"][EXTRAHERA_ASSET_TYPE_ID]
            slots = {s: v for s, v in spec["state_slots"].items() if self._visible(v)}
            caps = copy.deepcopy(spec["capabilities"])
            for cap in caps.values():
                cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in slots]
                cap["observes"] = [s for s in cap["observes"] if s in slots]
            caps.update(_vacuum_templates("biotage_extrahera").capabilities(include_evaluator=self.__host._principals[self.__principal_id][0] in ("debug", "evaluator")))
            return {"protocol_version": "1.0.0", "asset_type_id": EXTRAHERA_ASSET_TYPE_ID, "asset_version": "1.1.0", "catalog_id": "CHEM-031", "model": "Biotage Extrahera Classic", "instance_id": instance_id, "adapter": "basic40_runtime_vacuum.ExtraheraSession", "capabilities": caps, "state_slots": slots, "limitations": list(EXTRAHERA_LIMITATIONS)}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None: return self._error(instance_id, "UNKNOWN_INSTANCE")
            ids = [s for s, slot in EXTRAHERA_STATE_SLOTS.items() if self._visible(slot)] if state_ids is None else state_ids
            if not isinstance(ids, list) or any(not isinstance(s, str) for s in ids) or len(set(ids)) != len(ids): return self._error(instance_id, "INVALID_ARGUMENT")
            if any(s not in EXTRAHERA_STATE_SLOTS or not self._visible(EXTRAHERA_STATE_SLOTS[s]) for s in ids): return self._error(instance_id, "ACCESS_DENIED")
            observations = {}
            for sid in ids:
                slot, value = EXTRAHERA_STATE_SLOTS[sid], copy.deepcopy(inst.state[sid])
                observation = {"value": value, "unit": slot["unit"], "quality": "unavailable" if value is None else "estimated" if slot["origin"] == "process_truth" else "valid", "sample_time_s": self.__host.time_s, "sample_window_s": None}
                if value is None: observation["reason"] = "No implemented observation/model or explicit initialization for this quantity"
                observations[sid] = observation
            return self._envelope(instance_id, observations=observations)

    def _request(self, instance_id, request_id, content, action):
        if not isinstance(request_id, str) or not request_id or len(request_id) > 256: return self._error(instance_id, "INVALID_ARGUMENT", request_id)
        try: digest = hashlib.sha256(json.dumps(content, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        except (TypeError, ValueError): return self._error(instance_id, "INVALID_ARGUMENT", request_id)
        key = (self.__principal_id, instance_id, request_id)
        if key in self.__host._requests:
            previous, response = self.__host._requests[key]
            return copy.deepcopy(response) if digest == previous else self._error(instance_id, "REQUEST_CONFLICT", request_id)
        response = action()
        self.__host._requests[key] = (digest, copy.deepcopy(response))
        self.__host._logs[self.__principal_id].append({"instance_id": instance_id, "request_id": request_id, "response": copy.deepcopy(response)})
        return response

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None: return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict): return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            return self._request(instance_id, request_id, ["invoke", capability_id, arguments], lambda: self._invoke_checked(inst, capability_id, arguments, request_id))

    def _invoke_checked(self, inst, cap, args, request_id):
        fail = lambda code, status="rejected": self._error(inst.instance_id, code, request_id, status)
        templates = _vacuum_templates("biotage_extrahera")
        if cap in templates.CAPABILITY_IDS:  # 2026-10 template queries: read-only, no state change
            if cap in templates.EVALUATOR_CAPABILITIES:
                if self.__host._principals[self.__principal_id][0] not in ("debug", "evaluator"): return fail("ACCESS_DENIED")
                if args != {}: return fail("INVALID_ARGUMENT")
                return self._envelope(inst.instance_id, request_id, result=templates.report(copy.deepcopy(inst)))
            result, code = templates.template_read(args)
            if code == "UNKNOWN_REFERENCE":
                return self._envelope(inst.instance_id, request_id, status="rejected", error={"code": code, "message": "substance_id or sorbent_id is not in the Extrahera SPE library", "retryable": False, "side_effects": "none", "known_progress": None})
            return fail(code) if code else self._envelope(inst.instance_id, request_id, result=result)
        if cap not in EXTRAHERA_CAPABILITY_ARGUMENTS: return fail("UNKNOWN_CAPABILITY")
        if not _extrahera_validate(args, _extrahera_object(EXTRAHERA_CAPABILITY_ARGUMENTS[cap])): return fail("INVALID_ARGUMENT")
        s, active = inst.state, inst.state["operation.active_id"]
        if active and cap not in ("power.set", "method.stop"): return fail("BUSY")
        if cap not in ("power.set", "deck.configure", "method.configure", "method.stop") and not s["power.enabled"]: return fail("PRECONDITION_FAILED")
        if cap == "deck.configure":
            positions = args["solvent_positions"]
            if not args["collection_plate"].strip() or any(not p.strip() for p in positions) or len(set(positions)) != len(positions) or ((args["format"] == "cartridges24") != (args["cartridge_volume_m3"] is not None)): return fail("INVALID_ARGUMENT")
            if inst.sources is not None and (s["fluid.pipette_m3"] > 1e-18 or s["fluid.extraction_m3"] > 1e-18 or s["fluid.collection_m3"] > 1e-18): return fail("PRECONDITION_FAILED")
        if cap in ("gantry.move", "manifold.set_position") and s["pressure.target_pa"] != 0: return fail("PRECONDITION_FAILED")
        if cap == "pressure.set":
            if not float(args["pressure_pa"] / 10000).is_integer(): return fail("INVALID_ARGUMENT")
            if args["pressure_pa"] > 0 and (s["manifold.z_m"] != 0 or not s["utilities.compressed_air_ready"] or not s["utilities.nitrogen_ready"]): return fail("PRECONDITION_FAILED")
        if cap == "method.configure":
            if [p["phase"] for p in args["steps"]] != ["load", "wash", "elute"]: return fail("INVALID_ARGUMENT")
            if s["deck.format"] is None or any(p["solvent_position"] not in s["deck.solvent_positions"] for p in args["steps"]): return fail("PRECONDITION_FAILED")
        if cap in ("sample.aspirate", "sample.dispense", "method.start"):
            if s["deck.format"] is None or inst.sources is None or not s["utilities.compressed_air_ready"]: return fail("PRECONDITION_FAILED")
            if cap in ("sample.aspirate", "method.start") and (not s["utilities.vacuum_ready"] or s["vacuum.source_instance_id"] is None): return fail("PRECONDITION_FAILED")
            if cap == "sample.aspirate":
                position = args["solvent_position"]
                if position not in s["deck.solvent_positions"] or position not in inst.sources or inst.sources[position] < self.__host._stroke_m3(inst, args["volume_m3"]) or s["fluid.pipette_m3"] + args["volume_m3"] > EXTRAHERA_MAXIMUM_STROKE_M3 + 1e-18: return fail("PRECONDITION_FAILED")
            if cap == "sample.dispense" and s["fluid.pipette_m3"] + 1e-18 < self.__host._stroke_m3(inst, args["volume_m3"]): return fail("PRECONDITION_FAILED")
            if cap == "method.start":
                if not inst.method or s["fluid.pipette_m3"] > 1e-18 or s["fluid.extraction_m3"] > 1e-18 or s["pressure.target_pa"] <= 0 or not s["utilities.nitrogen_ready"] or s["manifold.z_m"] != 0: return fail("PRECONDITION_FAILED")
                requirements = {}
                for p in inst.method:
                    try: amount = self.__host._stroke_m3(inst, p["volume_m3"]) * p["repetitions"]
                    except OverflowError: return fail("INVALID_ARGUMENT")
                    requirements[p["solvent_position"]] = requirements.get(p["solvent_position"], 0) + amount
                if any(not math.isfinite(amount) or position not in inst.sources or inst.sources[position] + 1e-18 < amount for position, amount in requirements.items()): return fail("PRECONDITION_FAILED")
        candidate, result, operation_id = copy.deepcopy(inst), {}, None
        status = "succeeded"
        if cap == "power.set":
            candidate.state["power.enabled"] = args["enabled"]
            if not args["enabled"]:
                candidate.state["pressure.target_pa"] = 0.0
                if active: self.__host._terminate(candidate, candidate.operations[active], "cancelled")
            result = {"enabled": args["enabled"]}
        elif cap == "deck.configure":
            for key in args: candidate.state["deck." + ("format" if key == "format" else key)] = copy.deepcopy(args[key])
            candidate.method, candidate.state["method.configured"] = None, False
            result = {"format": args["format"]}
        elif cap == "gantry.move":
            candidate.state.update({"gantry." + key: val for key, val in args.items()})
            result = copy.deepcopy(args)
        elif cap == "manifold.set_position": candidate.state["manifold.z_m"], result = args["z_m"], {"z_m": args["z_m"]}
        elif cap == "pressure.set": candidate.state["pressure.target_pa"], result = args["pressure_pa"], {"pressure_pa": args["pressure_pa"]}
        elif cap == "method.configure":
            candidate.method, candidate.state["method.configured"] = copy.deepcopy(args["steps"]), True
            result = {"configured": True}
        elif cap == "method.stop":
            if active and candidate.operations[active]["capability_id"] == "method.start": self.__host._terminate(candidate, candidate.operations[active], "cancelled")
            result = {"status": candidate.state["method.status"]}
        else:
            candidate.operation_serial += 1
            operation_id = f"{inst.instance_id}:extrahera:{candidate.operation_serial}"
            candidate.operations[operation_id] = {"operation_id": operation_id, "capability_id": cap, "principal_id": self.__principal_id, "arguments": copy.deepcopy(args), "status": "running", "subvolume_m3": 0.0, "step_index": 0, "repetition": 0, "subphase": "aspirate"}
            candidate.state["operation.active_id"] = operation_id
            prefix = "method" if cap == "method.start" else "sample"
            candidate.state[prefix + ".status"] = "running"
            if prefix == "method": candidate.state["method.phase"], candidate.state["method.completed_strokes"] = "load", 0
            result, status = {"control_status": "started"}, "accepted"
        try: self.__host._commit(inst, candidate)
        except Exception: return fail("DEVICE_ERROR", "failed")
        return self._envelope(inst.instance_id, request_id, status=status, result=result, operation_id=operation_id)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None: return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slot = EXTRAHERA_STATE_SLOTS.get(state_id) if isinstance(state_id, str) else None
            if not slot or not self._visible(slot) or not slot["write_via"]: return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slot["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)

    def _operation(self, instance_id, operation_id):
        inst = self._instance(instance_id)
        op = inst.operations.get(operation_id) if inst and isinstance(operation_id, str) else None
        return (inst, op) if op and op["principal_id"] == self.__principal_id else (inst, None)

    def poll(self, *, instance_id, operation_id):
        with self.__host._lock:
            inst, op = self._operation(instance_id, operation_id)
            if inst is None: return self._error(instance_id, "UNKNOWN_INSTANCE")
            if op is None: return self._error(instance_id, "ACCESS_DENIED")
            status = "succeeded" if op["status"] == "completed" else op["status"]
            error = {"code": "PRECONDITION_FAILED", "message": "A required external utility or transfer condition became unavailable", "retryable": False, "side_effects": "partial", "known_progress": "Prior transfers are retained; process quantities require a trusted evaluation session"} if status == "failed" else None
            return self._envelope(instance_id, status=status, operation_id=operation_id, result={"status": op["status"], "capability_id": op["capability_id"]}, error=error)

    def cancel(self, *, instance_id, operation_id, request_id):
        with self.__host._lock:
            inst, op = self._operation(instance_id, operation_id)
            if inst is None: return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if op is None: return self._error(instance_id, "ACCESS_DENIED", request_id)
            def action():
                candidate = copy.deepcopy(inst)
                if candidate.operations[operation_id]["status"] == "running": self.__host._terminate(candidate, candidate.operations[operation_id], "cancelled")
                self.__host._commit(inst, candidate)
                response = self.poll(instance_id=instance_id, operation_id=operation_id)
                response["request_id"] = request_id
                return response
            return self._request(instance_id, request_id, ["cancel", operation_id], action)

    def logs(self):
        with self.__host._lock: return copy.deepcopy(self.__host._logs[self.__principal_id])


def export_extrahera_contract_metadata():
    slots = copy.deepcopy(EXTRAHERA_STATE_SLOTS)
    for sid, slot in slots.items():
        slot["read_binding"] = f"ExtraheraSession.read(instance_id=..., state_ids=['{sid}'])"
        slot["sampling"] = {"mode": "instant", "time_domain": "simulation", "window": None}
        slot["reference_conditions"] = "Manufacturer absolute/gauge reference unspecified; control setpoint only" if sid.startswith("pressure.") else "engineering_assumption: local geometric joint coordinate, SI m" if sid in EXTRAHERA_JOINTS else "engineering_assumption: lumped liquid volume, no per-well solver" if sid.startswith("fluid.") else None
    descriptions = {"power.set": "设置电源；断电取消当前操作，保留已转移液体并归零压力设定", "deck.configure": "配置24位1/3/6mL小柱或96/48孔板、逻辑溶剂位置及收集板", "gantry.move": "写入三轴龙门几何目标；无运动时长或碰撞规划", "manifold.set_position": "写入正压歧管升降几何目标", "pressure.set": "设置0到500000Pa控制压力，10000Pa步长；压力基准厂家未说明", "sample.aspirate": "启动一次最多1000微升的集中吸液操作，宿主按用户流量推进", "sample.dispense": "启动一次最多1000微升的集中分液操作，宿主按用户流量推进", "method.configure": "配置依次load/wash/elute三阶段、溶剂位置、单次体积、流量、排液流量和次数", "method.start": "启动装载、洗涤、洗脱顺序；宿主逐次吸液、分液和排液守恒推进", "method.stop": "取消当前方法并保留所有部分液体转移和已完成次数"}
    components = {"power.set": ["touchscreen"], "deck.configure": ["deck_slots", "rack_24_1ml", "rack_24_3ml", "rack_24_6ml", "plate_96", "plate_48", "solvent_reservoirs", "waste_tray"], "gantry.move": ["gantry_x", "gantry_y", "pipette_z", "pipette_head"], "manifold.set_position": ["pressure_manifold"], "pressure.set": ["pressure_manifold", "rear_ports"], "sample.aspirate": ["pipette_head", "solvent_reservoirs", "rear_ports"], "sample.dispense": ["pipette_head", "deck_slots", "waste_tray"], "method.configure": ["touchscreen", "deck_slots"], "method.start": ["touchscreen", "pipette_head", "pressure_manifold", "deck_slots", "solvent_reservoirs", "waste_tray", "rear_ports"], "method.stop": ["touchscreen", "pipette_head", "pressure_manifold"]}
    effects = {"power.set": ["power.enabled", "pressure.target_pa", "operation.active_id", "method.status", "method.phase", "sample.status"], "deck.configure": [s for s in slots if s.startswith("deck.")] + ["method.configured"], "gantry.move": ["gantry.x_m", "gantry.y_m", "gantry.z_m"], "manifold.set_position": ["manifold.z_m"], "pressure.set": ["pressure.target_pa"], "sample.aspirate": ["sample.status", "operation.active_id", "fluid.sources_m3", "fluid.pipette_m3"], "sample.dispense": ["sample.status", "operation.active_id", "fluid.pipette_m3", "fluid.extraction_m3", "fluid.waste_m3", "fluid.collection_m3"], "method.configure": ["method.configured"], "method.start": ["method.status", "method.phase", "method.completed_strokes", "operation.active_id"] + [s for s in slots if s.startswith("fluid.") and s != "fluid.initial_total_m3"], "method.stop": ["method.status", "method.phase", "operation.active_id"]}
    examples = {"power.set": {"enabled": True}, "deck.configure": {"format": "cartridges24", "cartridge_volume_m3": 3e-6, "collection_plate": "collection_a", "solvent_positions": ["load", "wash", "elute"]}, "gantry.move": {"x_m": .1, "y_m": .1, "z_m": .15}, "manifold.set_position": {"z_m": 0.0}, "pressure.set": {"pressure_pa": 100000}, "sample.aspirate": {"solvent_position": "load", "volume_m3": 1e-6, "flow_m3_s": 1e-7}, "sample.dispense": {"destination": "collection", "volume_m3": 1e-6, "flow_m3_s": 1e-7}, "method.configure": {"steps": [{"phase": p, "solvent_position": p, "volume_m3": 1e-6, "flow_m3_s": 1e-7, "drain_flow_m3_s": 1e-7, "repetitions": 1} for p in ("load", "wash", "elute")]}, "method.start": {}, "method.stop": {}}
    outputs = {"power.set": {"enabled": {"type": "boolean"}}, "deck.configure": {"format": {"type": "string", "enum": ["cartridges24", "plate96", "plate48"]}}, "gantry.move": EXTRAHERA_CAPABILITY_ARGUMENTS["gantry.move"], "manifold.set_position": EXTRAHERA_CAPABILITY_ARGUMENTS["manifold.set_position"], "pressure.set": EXTRAHERA_CAPABILITY_ARGUMENTS["pressure.set"], "sample.aspirate": {"control_status": {"type": "string", "const": "started"}}, "sample.dispense": {"control_status": {"type": "string", "const": "started"}}, "method.configure": {"configured": {"type": "boolean"}}, "method.start": {"control_status": {"type": "string", "const": "started"}}, "method.stop": {"status": slots["method.status"]["value_schema"]}}
    preconditions = {"power.set": [], "deck.configure": ["Idle; nonempty unique solvent positions and collection ID; cartridge volume iff cartridges24; no retained pipette/extraction/collection liquid"], "gantry.move": ["Power on; idle; control pressure zero"], "manifold.set_position": ["Power on; idle; control pressure zero"], "pressure.set": ["Power on; idle; integer 10000 Pa increments; nonzero requires manifold z=0 and host air/nitrogen readiness"], "sample.aspirate": ["Power on; idle; configured deck; initialized inventories; known source with enough liquid; resultant pipette <=1000 uL; external vacuum and air ready"], "sample.dispense": ["Power on; idle; configured deck; initialized inventory; enough pipette liquid; external air ready"], "method.configure": ["Idle; configured deck; exactly load/wash/elute in order with configured solvent positions and positive user rates"], "method.start": ["Power on; idle; configured method/deck; explicit inventory; empty pipette/extraction; enough sources for all strokes; external vacuum/air/nitrogen ready; manifold z=0 and nonzero pressure setpoint"], "method.stop": []}
    caps = {}
    for cap, arguments in EXTRAHERA_CAPABILITY_ARGUMENTS.items():
        asynchronous = cap in ("sample.aspirate", "sample.dispense", "method.start")
        caps[cap] = {"availability": "implemented", "visibility": "agent", "kind": "configuration" if cap in ("deck.configure", "method.configure") else "device_command", "description_zh": descriptions[cap], "component_ids": components[cap], "input_schema": _extrahera_object(copy.deepcopy(arguments)), "output_schema": _extrahera_object(copy.deepcopy(outputs[cap])), "preconditions_zh": preconditions[cap], "state_effects": [{"state_id": sid, "timing": "on_step" if sid.startswith("fluid.") or (asynchronous and sid == "method.completed_strokes") else "immediate", "description_zh": descriptions[cap]} for sid in effects[cap]], "observes": [sid for sid in effects[cap] if slots[sid]["visibility"] == "agent"], "example_arguments": [examples[cap]], "binding": f"basic40_runtime_vacuum.ExtraheraSession.invoke(instance_id=..., capability_id='{cap}', arguments=..., request_id=...)", "completion_zh": "accepted只确认调度；poll status=succeeded表示用户指定液体转移已完成；失败/取消保留部分效果" if asynchronous else "succeeded确认控制或配置写入；不代表化学提取效率或运动轨迹验证", "asynchronous": asynchronous, "supports_cancel": asynchronous, "scene_states": [sid for sid in effects[cap] if sid in EXTRAHERA_SCENE_STATE_IDS]}
    return {"asset_types": {EXTRAHERA_ASSET_TYPE_ID: {"catalog_id": "CHEM-031", "model": "Biotage Extrahera Classic", "state_slots": slots, "capabilities": caps, "parameters": {"maximum_stroke_m3": {"value": 1e-6, "unit": "m^3", "evidence": "catalog"}, "pressure_max_pa": {"value": 500000, "unit": "Pa", "evidence": "catalog", "reference_conditions": "unspecified manufacturer pressure reference"}, "pressure_increment_pa": {"value": 10000, "unit": "Pa", "evidence": "catalog"}, "accuracy_fraction_at_1000ul": {"value": .01, "unit": "1", "evidence": "catalog", "reference_conditions": "only at 1000 uL; not a noise model"}, "cv_fraction_at_1000ul": {"value": .01, "unit": "1", "evidence": "catalog", "reference_conditions": "only at 1000 uL"}, "recovery_fraction": {"value": None, "unit": "1", "evidence": "unknown"}, "flow_resistance": {"value": None, "unit": "Pa*s/m^3", "evidence": "unknown"}}, "limitations": list(EXTRAHERA_LIMITATIONS)}}, "host": ExtraheraHost.describe_host(), "scene_interface": {"signature": "scene_adapter(instance_id, updates)", "public_scene_keys_only": list(EXTRAHERA_SCENE_STATE_IDS), "state_defaults_and_bounds": EXTRAHERA_JOINTS}, "async_protocol": {"poll": "ExtraheraSession.poll(instance_id=..., operation_id=...)", "cancel": "ExtraheraSession.cancel(instance_id=..., operation_id=..., request_id=...)", "ownership": "original principal and same instance only", "deduplication": "per principal/instance/request ID, in-memory only"}}


def validate_extrahera_runtime(output=None):
    import importlib.util
    import io
    import unittest
    test_file = Path(__file__).resolve().parents[1] / "tests" / "test_basic40_vacuum.py"
    spec = importlib.util.spec_from_file_location("test_basic40_extrahera_validation", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(module.Basic40ExtraheraTests))
    checked = datetime.now(timezone.utc).isoformat()
    environment = {"python": platform.python_version(), "platform": platform.platform()}
    method = "unittest Basic40ExtraheraTests through real independent ExtraheraHost/Session: SI ranges, async progress/cancel, volume conservation, method order, host clock, scope, request dedup, truth filtering, scene callback and rejection atomicity"
    report = {"schema_version": "1.0.0", "catalog_id": "CHEM-031", "recorded_at": checked, "checked_at": checked, "method": method, "environment": environment, "tests_run": result.testsRun, "result": "passed" if result.wasSuccessful() else "failed", "failures": len(result.failures), "errors": len(result.errors), "log": stream.getvalue(), "layers": {"process": "passed" if result.wasSuccessful() else "failed", "host": "passed" if result.wasSuccessful() else "failed", "scene_callback": "passed" if result.wasSuccessful() else "failed", "blender_evaluated_scene": "not_run", "chemical_extraction": "not_run", "per_well_fluid": "not_run", "hardware": "not_run", "robot": "not_run", "collision": "not_run"}, "data_classification": "Synthetic engineering_assumption inventories only; no deployed task truth", "limitations": list(EXTRAHERA_LIMITATIONS)}
    report["checks"] = [{"check_id": "extrahera_runtime_" + layer, "layer": layer, "status": report["result"], "capability_ids": list(EXTRAHERA_CAPABILITY_ARGUMENTS), "method": method, "environment": environment, "checked_at": checked, "evidence_path": "outputs/basic40_batch02/t2_vacuum/biotage_extrahera/runtime_validation.json"} for layer in ("process", "host")]
    if output is not None: Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


# CHEM-031 metadata timing addendum: start acknowledgement and later completion are distinct.
_extrahera_metadata_export_before_timing = export_extrahera_contract_metadata


def export_extrahera_contract_metadata():
    metadata = _extrahera_metadata_export_before_timing()
    caps = metadata["asset_types"][EXTRAHERA_ASSET_TYPE_ID]["capabilities"]
    late = {"sample.aspirate": ("sample.status", "operation.active_id"),
            "sample.dispense": ("sample.status", "operation.active_id"),
            "method.start": ("method.status", "method.phase", "operation.active_id")}
    for cap, states in late.items():
        caps[cap]["state_effects"].extend({"state_id": sid, "timing": "on_step", "description_zh": "宿主推进时更新完成/失败状态；取消保留此前液体转移"} for sid in states)
    return metadata


# ======== G03 front panels (begin) ========
# >>> G03 front panels: shared helpers
# Front panels of the instruments of this file (panel_ui.json beside each contract, run by the generic engine
# scripts/asset_runtime/_panel_ui.py) and the device functions behind them.  Appended: the runtimes above keep their classes, commands and
# meaning.  Each instrument block below subclasses its host and session under the same names, adds its device-layer slots to its slot table
# (read like the other slots), and routes its device commands and the panel.* gestures before the original commands.
import importlib.util as _g03_importlib_util
import sys as _g03_sys
import tempfile as _g03_tempfile
from datetime import timedelta as _g03_timedelta

G03_ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets/basic40_batch02/t2_vacuum"
G03_DEVICE_EPOCH = datetime(2026, 1, 1)   # device clocks read this at simulation time 0 (assumption; no clock-setting function in these manuals)
G03_PANEL_GESTURES = ("panel.press", "panel.turn", "panel.switch", "panel.touch")


def _g03_engine():
    """The shared front-panel engine, imported once (from scripts/ on sys.path, else from its file)."""
    try:
        from asset_runtime import _panel_ui as engine
        return engine
    except ImportError:
        pass
    if "_chemsim_panel_ui" not in _g03_sys.modules:
        spec = _g03_importlib_util.spec_from_file_location("_chemsim_panel_ui", Path(__file__).resolve().with_name("asset_runtime") / "_panel_ui.py")
        module = _g03_importlib_util.module_from_spec(spec)
        _g03_sys.modules["_chemsim_panel_ui"] = module
        spec.loader.exec_module(module)
    return _g03_sys.modules["_chemsim_panel_ui"]


def _g03_clock(time_s):
    """Device date and time (ISO, seconds) at a simulation time."""
    return (G03_DEVICE_EPOCH + _g03_timedelta(seconds=float(time_s))).isoformat(timespec="seconds")


def _g03_obj(properties, optional=()):
    return {"type": "object", "properties": properties, "required": [k for k in properties if k not in optional], "additionalProperties": False}


def _g03_num(unit, low=None, high=None, integer=False, nullable=False):
    schema = {"type": "integer" if integer else "number", "x-unit": unit}
    if low is not None: schema["minimum"] = low
    if high is not None: schema["maximum"] = high
    return {"anyOf": [schema, {"type": "null"}]} if nullable else schema


def _g03_str(enum=None, max_len=None, nullable=False):
    schema = {"type": "string"}
    if enum is not None: schema["enum"] = list(enum)
    if max_len is not None: schema["maxLength"] = max_len
    return {"anyOf": [schema, {"type": "null"}]} if nullable else schema


def _g03_check(schema, value, path="$"):
    """Errors of a value against the JSON-schema subset the device commands use (empty: valid)."""
    if "anyOf" in schema:
        return [] if any(not _g03_check(s, value, path) for s in schema["anyOf"]) else [f"{path}: no alternative matches"]
    kind = schema.get("type")
    tests = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list), "string": lambda v: isinstance(v, str),
             "boolean": lambda v: type(v) is bool, "integer": lambda v: type(v) is int, "null": lambda v: v is None,
             "number": lambda v: type(v) in (int, float) and math.isfinite(v)}
    if kind is not None and not tests[kind](value):
        return [f"{path}: type"]
    if "enum" in schema and (value not in schema["enum"] or (type(value) is bool) != any(type(e) is bool for e in schema["enum"] if e == value)):
        return [f"{path}: enum"]
    if "const" in schema and value != schema["const"]:
        return [f"{path}: const"]
    if kind in ("number", "integer") and not (schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf)
                                               and value > schema.get("exclusiveMinimum", -math.inf)):
        return [f"{path}: range"]
    if kind == "string" and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", math.inf):
        return [f"{path}: length"]
    errors = []
    if kind == "object":
        props = schema.get("properties", {})
        errors += [f"{path}.{k}: missing" for k in schema.get("required", []) if k not in value]
        for k, v in value.items():
            if k in props:
                errors += _g03_check(props[k], v, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{k}: unknown")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += _g03_check(schema["additionalProperties"], v, f"{path}.{k}")
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf):
            errors.append(f"{path}: size")
        for i, v in enumerate(value):
            errors += _g03_check(schema.get("items", {}), v, f"{path}[{i}]")
    return errors


class _G03PanelHost:
    """Panel-host protocol of the engine for one instance: the values the firmware reads, the command path it calls, the clock."""

    def __init__(self, read, invoke, clock):
        self._read, self._invoke, self._clock = read, invoke, clock

    def panel_read(self):
        return self._read()

    def panel_invoke(self, capability_id, arguments):
        """A panel action takes the checked path of a direct call (same preconditions, commit and scene callback); a refusal raises
        PanelRejected with the runtime error code so the panel shows the instrument's own indication."""
        response = self._invoke(capability_id, dict(arguments))
        if response["status"] not in ("succeeded", "accepted"):
            raise _g03_engine().PanelRejected(response["error"]["code"])
        return response["result"]

    def panel_time(self):
        return self._clock()


def g03_panel_gesture(panel, capability_id, args, *, instance_id, time_s, capture_dir=None):
    """Run one panel.* command on a PanelUI: returns (result, None) or (None, error code).  Gestures take no simulated time."""
    engine = _g03_engine()
    try:
        if capability_id == "panel.press":
            return {"accepted": bool(panel.press(args["input_id"], args.get("duration_s", 0.1), tuple(args.get("held", ()))))}, None
        if capability_id == "panel.turn":
            return {"accepted": bool(panel.turn(args["input_id"], args["detents"]))}, None
        if capability_id == "panel.switch":
            return {"accepted": bool(panel.switch(args["input_id"], args["position"], tuple(args.get("held", ()))))}, None
        if capability_id == "panel.touch":
            to = (args["to_x"], args["to_y"]) if "to_x" in args or "to_y" in args else None
            if to is not None and (to[0] is None or to[1] is None):
                return None, "INVALID_ARGUMENT"
            return {"accepted": bool(panel.touch(args["display_id"], args["x"], args["y"], args.get("duration_s", 0.1), to))}, None
        if capability_id == "panel.capture":
            folder = Path(capture_dir) if capture_dir else Path(_g03_tempfile.gettempdir()) / "chemistry_sim_panel" / instance_id
            name = f"{args['display_id']}_{int(round(time_s * 1000)):012d}.png"
            image = panel.save_png(args["display_id"], folder / name)
            return {"display_id": args["display_id"], "time_s": float(time_s), "image": image}, None
        if capability_id == "panel.frame":
            return {"frame": panel.frame()}, None
    except engine.PanelError:
        return None, "INVALID_ARGUMENT"
    return None, "UNKNOWN_CAPABILITY"


def g03_panel_capabilities(spec, binding, effects, components=None):
    """Capability entries (runtime metadata form) of the panel.* commands a spec declares; gestures list the immediate effects of the
    commands the panel can call (agent-visible ones are filtered again by describe)."""
    entries = {}
    for name, d in _g03_engine().capability_definitions(spec).items():
        gesture = name in G03_PANEL_GESTURES
        entries[name] = {"availability": "implemented", "visibility": d["visibility"], "kind": d["kind"], "description_zh": d["function_zh"],
                         "component_ids": list(components or d["component_ids"]) if gesture and components else d["component_ids"],
                         "input_schema": d["input_schema"], "output_schema": d["output_schema"], "example_arguments": [d["example_arguments"]],
                         "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": "面板手势调用的设备命令改变的量"} for sid in effects] if gesture else [],
                         "observes": [], "preconditions_zh": "面板按panel_ui.json（手册页码证据）解释手势并调用本运行层的同名命令；被拒时显示仪器自己的提示。",
                         "binding": binding.format(name=name), "scene_backend_required": False,
                         "completion_zh": "手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕用panel.capture查看" if gesture else "同步返回"}
    return entries


def g03_device_capability(name, kind, description, inputs, outputs, example, components, effects, *, optional=(), binding, visibility="agent",
                          preconditions="", completion="同步提交；需要时间的过程由宿主step推进。"):
    """A device-layer command entry (runtime metadata form, like the original commands)."""
    return {"availability": "implemented", "visibility": visibility, "kind": kind, "description_zh": description, "component_ids": list(components),
            "input_schema": _g03_obj(inputs, optional), "output_schema": _g03_obj(outputs), "example_arguments": [example],
            "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": description} for sid in dict.fromkeys(effects)],
            "observes": list(dict.fromkeys(effects))[:6], "preconditions_zh": preconditions, "binding": binding.format(name=name),
            "scene_backend_required": False, "completion_zh": completion}
def g03_owner_refuses(g, runs, touched, principal, visibility, panel_principal):
    """Run ownership of the instruments of this file (lead rule 2026-10-19, a simulation rule: the real instruments have no caller concept).
    runs: {channel: active} of the instance; touched: the channels the command would stop, power off or change.  True when an agent other
    than the owner of an active channel calls such a command (refused with ACCESS_DENIED before anything changes, on the direct and the panel
    path alike: a panel gesture acts as its session).  Evaluator / debug sessions, the panel's own principal (time-outs) and unowned runs
    are not restricted; physical actions (doors, levers, plugs and hard power switches, lead clarification 10-19: the device reacts as to a
    power loss) and navigation touch no channel; only soft power controls (stand-by keys, software shut-down) are guarded."""
    if g is None or visibility != "agent" or principal == panel_principal:
        return False
    owners = g.get("run_owners") or {}
    return any(runs.get(ch) and owners.get(ch) not in (None, principal) for ch in touched)


def g03_owner_note(g, before, after, principal, visibility, panel_principal):
    """After a command: a channel it started (inactive before, active after) belongs to the calling agent (no owner for evaluator sessions
    and panel time-outs); an inactive channel has no owner."""
    if g is None or before is None:
        return
    owners = dict(g.get("run_owners") or {})
    for ch, active in after.items():
        if not active:
            owners.pop(ch, None)
        elif not before.get(ch):
            owners[ch] = principal if visibility == "agent" and principal != panel_principal else None
    if owners:
        g["run_owners"] = owners
    else:
        g.pop("run_owners", None)                                           # no run: the device state is as before (queries leave it unchanged)
# <<< G03 front panels: shared helpers


# >>> G03 CHEM-031 Extrahera touch screen
# Device layer of the Extrahera touch-screen software (non-GLP; user manual references/manuals/biotage_extrahera_classic/
# operating_manual__extrahera_user_manual_414158.pdf, 'p.N' = PDF page): Data Administration libraries (solvents, sample types, extraction media,
# sample plates and racks, pipette tips) with solvent locks, the method editor (SLE, SPE, PPT/PLD, Filtration, Filtration+, Hydro DME+),
# Prepare Run (columns / positions, tips, solvent bottles and priming, position 6 reservoirs, report name), runs of a method compiled onto the
# lumped liquid balance above (pause / resume / abort / restore, scheduled pauses, out-of-tips pause, blue / red alarm), Maintenance settings,
# Manual Control, pipette pump calibration (coefficient and offset applied to the run volumes), Flush Solvent Inlets, Re-Initialize, reports,
# Shut Down, and the front panel.  The CHEM-031 commands keep their meaning; the pipette error model (evaluator only) is 0 by default.
EXTRAHERA_ASSET_VERSION = "1.3.1"
EXTRAHERA_ASSET_DIR = G03_ASSET_ROOT / "biotage_extrahera"
EXTRAHERA_MANUAL = "references/manuals/biotage_extrahera_classic/operating_manual__extrahera_user_manual_414158.pdf"
EXTRAHERA_INSTRUMENT_TYPES = {"24 Positions, Column Rack": (6, 4, 4, 3), "48 Positions, Column Rack": (12, 4, 4, 3), "48 Positions, Plate": (6, 8, 8, 7),
                              "96 Positions, Plate/Columns": (12, 8, 8, 7)}   # columns, rows, pipette channels, max individual samples (p.32, p.46, p.57)
EXTRAHERA_METHOD_TYPES = ("SLE", "SPE", "PPT/PLD", "Filtration", "Filtration+", "Hydro DME+")
EXTRAHERA_BOTTLES = ("S1", "S2", "S3", "S4", "S5")
EXTRAHERA_POS6 = ("P6-1", "P6-2", "P6-3", "P6-4", "P6-5")
EXTRAHERA_BOOT_S = 30.0                 # start-up screen before the main menu (assumption; not given)
EXTRAHERA_PRIME_FULL_M3 = 30e-6         # inlet line and pump filled after about 2 x 15 mL of the new solvent (p.35: 2-3 x 15 mL)
EXTRAHERA_FLUSH_EMPTY_M3 = 25e-6        # 25 mL of air empties an inlet line and the pump (p.48)
EXTRAHERA_DEAD_VOLUME_M3 = 4e-6         # dead volume of each reservoir (p.35, p.36)
EXTRAHERA_TIP_RACK = 96                 # sample pipette tips per rack holder (p.34, Fig. 78 text: load 96 tips)
EXTRAHERA_CYCLE_OVERHEAD_S = 10.0       # robot moves and tip handling per pipetting cycle (assumption)
EXTRAHERA_AIR_PUSH_PA = 100000.0        # pressure set during an air push (about 10 mL/min; the pressure is not given: assumption)
EXTRAHERA_PLATE_DRY_PA = 500000.0       # plate dry at 5 bar, about 600 mL/min (p.2, p.27)
EXTRAHERA_PARK_Z_M = EXTRAHERA_JOINTS["manifold.z_m"][2]
EXTRAHERA_CAL_INTERVALS = {"1 month": 30, "3 months": 91, "6 months": 182, "1 year": 365}   # days (p.19)
_EXH_OP_PHASE = {"pretreatment": "pretreatment", "conditioning": "conditioning", "equilibration": "equilibration", "load": "load", "wash": "wash",
                 "elution": "elute", "collection": "collection"}
EXTRAHERA_STATE_SLOTS["method.phase"]["value_schema"]["enum"] = list(dict.fromkeys(
    EXTRAHERA_STATE_SLOTS["method.phase"]["value_schema"]["enum"] + ["pretreatment", "conditioning", "equilibration", "collection"]))


def _exh_f(key, label, kind, page, **kw):
    d = dict(key=key, label=label, type=kind, page=page)
    d.update(kw)
    return d


_EXH_FLOW = dict(unit="mL/min", min=0.1, max=100.0, step=0.1)
_EXH_UL = dict(unit="µL", min=0.0, max=1000.0, step=1.0)
EXTRAHERA_LIBRARY_FIELDS = {   # Data Administration (p.21-24); ranges are assumptions (the manual gives none)
    "solvents": [
        _exh_f("name", "Solvent name", "text", "p.21", required=True, max_len=40), _exh_f("description", "Solvent description", "text", "p.21", max_len=80, default=""),
        _exh_f("asp_flow", "Aspiration flow rate", "number", "p.21", default=5.0, **_EXH_FLOW), _exh_f("disp_flow", "Dispense flow rate", "number", "p.21", default=5.0, **_EXH_FLOW),
        _exh_f("post_asp", "Aspirate post dispense?", "bool", "p.21", default=False),
        _exh_f("post_asp_flow", "Aspirate post dispense flow rate", "number", "p.21", default=5.0, when=[("post_asp", "eq", True)], **_EXH_FLOW),
        _exh_f("post_asp_volume", "Aspirate post dispense volume", "number", "p.21", default=20.0, when=[("post_asp", "eq", True)], **_EXH_UL),
        _exh_f("serial", "Serial dispensing allowed?", "bool", "p.21", default=False), _exh_f("chlorinated", "Chlorinated?", "bool", "p.21", default=False),
        _exh_f("volatile", "Highly volatile?", "bool", "p.21", default=False),
        _exh_f("lower_gap_flow", "Lower air gap flow rate", "number", "p.21", default=5.0, **_EXH_FLOW),
        _exh_f("lower_gap_volume", "Lower air gap volume", "number", "p.21", default=10.0, **_EXH_UL),
        _exh_f("upper_gap_flow", "Upper air gap flow rate", "number", "p.21", default=5.0, **_EXH_FLOW),
        _exh_f("upper_gap_volume", "Upper air gap volume", "number", "p.21", default=10.0, **_EXH_UL),
        _exh_f("upper_gap_pause", "Upper air gap dispense pause", "number", "p.21", default=300.0, unit="ms", min=0.0, max=10000.0, step=10.0),
        _exh_f("tip_cond", "Requires tip conditioning?", "bool", "p.21", default=False),
        _exh_f("cond_frequency", "Conditioning frequency", "enum", "p.21", options=["Once before first aspiration", "Before each step", "Before every aspiration"],
               default="Once before first aspiration", when=[("tip_cond", "eq", True)]),
        _exh_f("cond_times", "Conditioning, number of times", "int", "p.21", min=1, max=10, step=1, default=1, when=[("tip_cond", "eq", True)]),
        _exh_f("cond_flow", "Conditioning flow rate", "number", "p.21", default=5.0, when=[("tip_cond", "eq", True)], **_EXH_FLOW),
        _exh_f("cond_volume_pct", "Conditioning volume (% of tip capacity)", "number", "p.21", unit="%", min=1.0, max=100.0, step=1.0, default=50.0,
               when=[("tip_cond", "eq", True)])],
    "sample_types": [
        _exh_f("name", "Sample name", "text", "p.22", required=True, max_len=40), _exh_f("description", "Sample description", "text", "p.22", max_len=80, default=""),
        _exh_f("asp_flow", "Aspiration flow rate", "number", "p.22", default=3.0, **_EXH_FLOW), _exh_f("disp_flow", "Dispense flow rate", "number", "p.22", default=3.0, **_EXH_FLOW),
        _exh_f("post_asp", "Aspirate post dispense?", "bool", "p.22", default=False),
        _exh_f("post_asp_flow", "Aspirate post dispense flow rate", "number", "p.22", default=3.0, when=[("post_asp", "eq", True)], **_EXH_FLOW),
        _exh_f("post_asp_volume", "Aspirate post dispense volume", "number", "p.22", default=20.0, when=[("post_asp", "eq", True)], **_EXH_UL),
        _exh_f("lower_gap_flow", "Lower air gap flow rate", "number", "p.22", default=3.0, **_EXH_FLOW),
        _exh_f("lower_gap_volume", "Lower air gap volume", "number", "p.22", default=10.0, **_EXH_UL),
        _exh_f("upper_gap_flow", "Upper air gap flow rate", "number", "p.22", default=3.0, **_EXH_FLOW),
        _exh_f("upper_gap_volume", "Upper air gap volume", "number", "p.22", default=10.0, **_EXH_UL),
        _exh_f("upper_gap_pause", "Upper air gap dispense pause", "number", "p.22", default=300.0, unit="ms", min=0.0, max=10000.0, step=10.0)],
    "extraction_media": [
        _exh_f("name", "Name", "text", "p.23", required=True, max_len=40), _exh_f("manufacturer", "Manufacturer", "text", "p.23", max_len=40, default=""),
        _exh_f("part_number", "Part number", "text", "p.23", max_len=40, default=""),
        _exh_f("capacity", "Capacity volume", "number", "p.23", unit="µL", min=0.0, max=10000.0, step=1.0, default=0.0),
        _exh_f("format", "Format", "enum", "p.23", options=list(EXTRAHERA_INSTRUMENT_TYPES), default="96 Positions, Plate/Columns"),
        _exh_f("comment", "Comment", "text", "p.23", max_len=80, default=""),
        _exh_f("disp_height", "Solvent/Sample dispensation height", "number", "p.23", unit="mm", min=0.0, max=200.0, step=0.1, default=60.0),
        _exh_f("asp_height", "Aspiration height", "number", "p.23", unit="mm", min=0.0, max=200.0, step=0.1, default=80.0)],
    "sample_plates": [
        _exh_f("name", "Name", "text", "p.23", required=True, max_len=40),
        _exh_f("capacity", "Capacity volume", "number", "p.23", unit="µL", min=0.0, max=20000.0, step=1.0, default=2000.0),
        _exh_f("height", "Plate/rack height", "number", "p.23-24", unit="mm", min=-149.0, max=0.0, step=1.0, default=-105.0),
        _exh_f("format", "Format", "enum", "p.23", options=list(EXTRAHERA_INSTRUMENT_TYPES), default="96 Positions, Plate/Columns"),
        _exh_f("asp_height", "Aspiration height", "number", "p.23", unit="mm", min=0.0, max=200.0, step=0.1, default=140.0),
        _exh_f("pretreat_height", "Pretreatment dispensation height", "number", "p.23", unit="mm", min=0.0, max=200.0, step=0.1, default=110.0)],
    "pipette_tips": [
        _exh_f("name", "Name", "text", "p.24", required=True, max_len=40), _exh_f("manufacturer", "Manufacturer", "text", "p.24", max_len=40, default=""),
        _exh_f("part_number", "Part number", "text", "p.24", max_len=40, default=""),
        _exh_f("capacity", "Capacity", "number", "p.24", unit="µL", min=10.0, max=5000.0, step=1.0, default=1000.0),
        _exh_f("length", "Length", "number", "p.24", unit="mm", min=10.0, max=150.0, step=0.1, default=95.0)],
}
EXTRAHERA_LIBRARY_TITLES = {"solvents": "Manage Solvents", "sample_types": "Manage Sample Types", "extraction_media": "Manage Extraction Media",
                            "sample_plates": "Manage Sample Plates and Racks", "pipette_tips": "Manage Pipette Tips"}


def _exh_entry(kind, name, **values):
    entry = {f["key"]: f.get("default") for f in EXTRAHERA_LIBRARY_FIELDS[kind]}
    entry.update(values, name=name)
    return entry


_EXH_PREDEFINED = {   # names as printed in the manual's figures and tables; their parameter values are not given (assumptions)
    "solvents": [_exh_entry("solvents", "Methanol", description="Methanol", volatile=False), _exh_entry("solvents", "Water", description="Deionized water"),
                 _exh_entry("solvents", "Acetonitrile", description="Acetonitrile"), _exh_entry("solvents", "2% Formic acid", description="2% formic acid in water"),
                 _exh_entry("solvents", "Ammonium hydroxide 5%", description="5% ammonium hydroxide in methanol"),
                 _exh_entry("solvents", "Dichloromethane", description="Dichloromethane", chlorinated=True, volatile=True)],
    "sample_types": [_exh_entry("sample_types", "Aqueous Sample", description="Aqueous sample"), _exh_entry("sample_types", "Plasma", description="Human plasma",
                                                                                                              asp_flow=1.5, disp_flow=1.5)],
    "extraction_media": [_exh_entry("extraction_media", "1 mL Column Rack, 24", capacity=1000.0, format="24 Positions, Column Rack"),
                         _exh_entry("extraction_media", "3 mL Column Rack, 24", capacity=3000.0, format="24 Positions, Column Rack"),
                         _exh_entry("extraction_media", "6 mL Column Rack, 24", capacity=6000.0, format="24 Positions, Column Rack"),
                         _exh_entry("extraction_media", "3 mL Column Rack, 48", capacity=3000.0, format="48 Positions, Column Rack"),
                         _exh_entry("extraction_media", "Extraction Plate, 48", capacity=5000.0, format="48 Positions, Plate"),
                         _exh_entry("extraction_media", "Extraction Plate, 96", capacity=2000.0, format="96 Positions, Plate/Columns"),
                         _exh_entry("extraction_media", "1 mL Array Plate, 96", capacity=1000.0, format="96 Positions, Plate/Columns"),
                         _exh_entry("extraction_media", "1 mL Column Rack, 96", capacity=1000.0, format="96 Positions, Plate/Columns")],
    "sample_plates": [_exh_entry("sample_plates", "12 x 75 mm Test Tubes, 24", capacity=4000.0, height=-105.0, format="24 Positions, Column Rack"),
                      _exh_entry("sample_plates", "13 x 100 mm Test Tubes, 24", capacity=6000.0, height=-88.0, format="24 Positions, Column Rack"),
                      _exh_entry("sample_plates", "12 x 75 mm Test Tubes, 48", capacity=4000.0, height=-105.0, format="48 Positions, Column Rack"),
                      _exh_entry("sample_plates", "5 mL Sample Plate, 48", capacity=5000.0, height=-102.0, format="48 Positions, Plate"),
                      _exh_entry("sample_plates", "1 mL Sample Plate, 96", capacity=1000.0, height=-113.0, format="96 Positions, Plate/Columns"),
                      _exh_entry("sample_plates", "2 mL Sample Plate, 96", capacity=2000.0, height=-105.0, format="96 Positions, Plate/Columns")],
    "pipette_tips": [_exh_entry("pipette_tips", "1000 µL Biotage tip", manufacturer="Biotage", capacity=1000.0, length=95.0)],
}

EXTRAHERA_SETTINGS = [   # Maintenance (p.46) and System Administration (p.19) settings of the non-GLP software
    _exh_f("audible_alarm", "Audible Alarm", "bool", "p.46", default=True), _exh_f("lights_upper", "Upper lamps", "bool", "p.46", default=True),
    _exh_f("lights_lower", "Lower lamps", "bool", "p.46", default=True),
    _exh_f("instrument_type", "Instrument Type", "enum", "p.46, p.57", options=list(EXTRAHERA_INSTRUMENT_TYPES), default="96 Positions, Plate/Columns"),
    _exh_f("ppt_solvent_first", "Add solvent first", "bool", "p.26, p.46", default=True),
    _exh_f("cal_notifications", "Notifications?", "bool", "p.19", default=False), _exh_f("cal_notify_startup", "Notify during startup", "bool", "p.19", default=True),
    _exh_f("cal_notify_run", "Notify before starting a run", "bool", "p.19", default=True),
    _exh_f("cal_interval", "Time between notifications", "enum", "p.19", options=list(EXTRAHERA_CAL_INTERVALS), default="3 months"),
]

ALL_T = EXTRAHERA_METHOD_TYPES
NOT_FILT = ("SLE", "SPE", "PPT/PLD", "Filtration+", "Hydro DME+")
_EXH_OPS = {   # operation: (label, method types that have it (Table 2, p.26), default on)
    "pretreatment": ("Pretreatment", ("SLE", "SPE", "Filtration+", "Hydro DME+"), False),
    "conditioning": ("Conditioning", ("SPE",), True), "equilibration": ("Equilibration", ("SPE",), True),
    "load": ("Load", ("SLE", "SPE", "PPT/PLD", "Hydro DME+"), True), "wash": ("Wash", ("SPE",), True),
    "elution": ("Elution", ("SLE", "SPE"), True), "collection": ("Collection", ("PPT/PLD", "Filtration", "Filtration+", "Hydro DME+"), True),
}
EXTRAHERA_STEP_OPS = ("conditioning", "equilibration", "wash", "elution")
EXTRAHERA_MAX_STEPS = 3          # steps of one operation and pressure-gradient steps (not given in the manual: assumption)
_EXH_COLLECT = ["A", "B", "C", "D (Waste)"]


def _exh_adv_fields(prefix, types, when, page):
    """Advanced pressure settings of one step (p.28 'Set Up a Pressure Gradient')."""
    w = list(when)
    out = [_exh_f(f"{prefix}_adv", "Use advanced pressure settings?", "bool", page, types=types, when=w, default=False, group=prefix),
           _exh_f(f"{prefix}_adv_n", "Number of steps", "int", page, types=types, when=w + [(f"{prefix}_adv", "eq", True)], min=1,
                  max=EXTRAHERA_MAX_STEPS, step=1, default=1, group=prefix)]
    for j in range(1, EXTRAHERA_MAX_STEPS + 1):
        wj = w + [(f"{prefix}_adv", "eq", True), (f"{prefix}_adv_n", "ge", j)]
        out += [_exh_f(f"{prefix}_adv{j}_pressure", f"Step {j}: Pressure", "number", page, types=types, when=wj, unit="bar", min=0.0, max=5.0, step=0.1,
                       default=0.0, group=prefix),
                _exh_f(f"{prefix}_adv{j}_time", f"Step {j}: Positive pressure time", "number", page, types=types, when=wj, unit="s", min=0.0, max=3600.0,
                       step=1.0, default=0.0, group=prefix)]
    out += [_exh_f(f"{prefix}_adv_finish", "Air push/Plate dry", "bool", page, types=types, when=w + [(f"{prefix}_adv", "eq", True)], default=False,
                   group=prefix),
            _exh_f(f"{prefix}_adv_finish_time", "Air push time/Plate dry time", "number", page, types=types,
                   when=w + [(f"{prefix}_adv", "eq", True), (f"{prefix}_adv_finish", "eq", True)], unit="s", min=0.0, max=3600.0, step=1.0, default=0.0,
                   group=prefix)]
    return out


def _exh_method_fields():
    """Method parameters of the editor (p.25-28), one flat table: key, label, type, method types, visibility conditions, required (exclamation
    mark), range, default, tab, evidence page."""
    F = []
    pre_on = [("pretreatment_on", "eq", True)]

    def add(tab, key, label, kind, page, types=ALL_T, **kw):
        F.append(_exh_f(key, label, kind, page, tab=tab, types=types, **kw))

    add("header", "name", "Method name", "text", "p.27", required=True, max_len=60, default="")
    add("header", "sample_plate", "Sample plate/rack", "lib:sample_plates", "p.28", types=NOT_FILT, required=True)
    add("header", "extraction_media", "Extraction media", "lib:extraction_media", "p.27", required=True)
    add("sample", "sample_type", "Sample type", "lib:sample_types", "p.28", types=NOT_FILT, required=True)
    add("sample", "starting_sample", "Starting sample volume in plate/rack", "number", "p.28", types=NOT_FILT, unit="µL", min=0.0, max=20000.0, step=1.0,
        default=0.0)
    add("sample", "reuse_tips", "Reuse tips for pretreatment mix and sample load?", "bool", "p.27", types=("SLE", "SPE", "Filtration+"), default=False)
    add("sample", "sample_tip", "Sample pipette tip type", "lib:pipette_tips", "p.28", types=NOT_FILT, required=True)
    add("sample", "tip_conditioning", "Tip conditioning?", "bool", "p.28", types=("SPE",), default=False)
    add("sample", "conditioning_solvent", "Conditioning solvent", "lib:solvents", "p.27", types=("SPE",), when=[("tip_conditioning", "eq", True)],
        required=True)
    add("sample", "comment", "Method comment", "text", "p.27", max_len=200, default="")
    for op, (label, types, on) in _EXH_OPS.items():
        add(op, f"{op}_on", label if not (op == "load") else "Load", "bool", "p.26 (Table 2)", types=types, default=on, operation=True)
    add("pretreatment", "pretreatment_position", "Move pretreatment step", "enum", "p.27", types=("SPE",), when=pre_on,
        options=["Before conditioning", "Before load"], default="Before conditioning")
    add("pretreatment", "pre_solvent", "Solvent", "lib:solvents", "p.28", types=_EXH_OPS["pretreatment"][1], when=pre_on, required=True)
    add("pretreatment", "pre_volume", "Volume", "number", "p.28", types=_EXH_OPS["pretreatment"][1], when=pre_on, unit="µL", min=1.0, max=10000.0, step=1.0,
        required=True)
    add("pretreatment", "premix", "Premix?", "bool", "p.27", types=_EXH_OPS["pretreatment"][1], when=pre_on, default=False)
    add("pretreatment", "premix_times", "Number of times", "int", "p.27-28", types=_EXH_OPS["pretreatment"][1], when=pre_on + [("premix", "eq", True)],
        min=1, max=20, step=1, default=3)
    add("pretreatment", "mix_times", "Mix number of times", "int", "p.27", types=_EXH_OPS["pretreatment"][1], when=pre_on, min=0, max=20, step=1, default=3)
    add("pretreatment", "mix_volume", "Mix volume", "number", "p.27", types=_EXH_OPS["pretreatment"][1], when=pre_on, unit="µL", min=0.0, max=1000.0,
        step=1.0, default=200.0)
    add("pretreatment", "pre_wait", "Wait time", "number", "p.28", types=_EXH_OPS["pretreatment"][1], when=pre_on, unit="min", min=0.0, max=600.0, step=0.5,
        default=0.0)
    add("pretreatment", "pre_pause", "Pause after this step?", "bool", "p.27", types=_EXH_OPS["pretreatment"][1], when=pre_on, default=False)
    for op in EXTRAHERA_STEP_OPS:
        types = _EXH_OPS[op][1]
        on = [(f"{op}_on", "eq", True)]
        add(op, f"{op}_steps", "Number of steps", "int", "p.27", types=types, when=on, min=1, max=EXTRAHERA_MAX_STEPS, step=1, default=1)
        add(op, f"{op}_dispose_tips", "Dispose solvent tips after each step?", "bool", "p.27", types=types, when=on, default=False)
        for k in range(1, EXTRAHERA_MAX_STEPS + 1):
            w = on + [(f"{op}_steps", "ge", k)]
            s = f"{op}{k}"
            pressured = tuple(t for t in types if t != "SLE")
            add(op, f"{s}_solvent", f"Step {k}: Solvent", "lib:solvents", "p.28", types=types, when=w, required=True, step_of=op, step_no=k)
            add(op, f"{s}_volume", f"Step {k}: Volume", "number", "p.28", types=types, when=w, unit="µL", min=1.0, max=10000.0, step=1.0, required=True,
                step_of=op, step_no=k)
            add(op, f"{s}_collect", f"Step {k}: Collect in position", "enum", "p.27, p.2", types=types, when=w, options=_EXH_COLLECT,
                default="A" if op == "elution" else "D (Waste)", step_of=op, step_no=k)
            add(op, f"{s}_pressure", f"Step {k}: Pressure", "number", "p.28", types=pressured, when=w + [(f"{s}_adv", "eq", False)], unit="bar", min=0.0,
                max=5.0, step=0.1, default=0.0, step_of=op, step_no=k)
            add(op, f"{s}_pp_time", f"Step {k}: Positive pressure time", "number", "p.27", types=pressured, when=w + [(f"{s}_adv", "eq", False)], unit="s",
                min=0.0, max=3600.0, step=1.0, default=0.0, step_of=op, step_no=k)
            add(op, f"{s}_wait", f"Step {k}: Wait time", "number", "p.28", types=types, when=w, unit="min", min=0.0, max=600.0, step=0.5, default=0.0,
                step_of=op, step_no=k)
            add(op, f"{s}_repeat", f"Step {k}: Repeat (number of times)", "int", "p.27", types=types, when=w, min=1, max=10, step=1, default=1, step_of=op,
                step_no=k)
            add(op, f"{s}_pause", f"Step {k}: Pause after this step?", "bool", "p.27", types=types, when=w, default=False, step_of=op, step_no=k)
            for f in _exh_adv_fields(s, types, w, "p.28"):
                F.append(dict(f, tab=op, step_of=op, step_no=k))
    add("wash", "wash_plate_dry", "Plate dry after last wash?", "bool", "p.27", types=("SPE",), when=[("wash_on", "eq", True)], default=False)
    add("wash", "wash_plate_dry_time", "Plate dry time", "number", "p.27", types=("SPE",), when=[("wash_on", "eq", True), ("wash_plate_dry", "eq", True)],
        unit="s", min=0.0, max=3600.0, step=1.0, default=60.0)
    add("elution", "elution_plate_dry", "Plate dry after last elution?", "bool", "p.27", types=("SPE",), when=[("elution_on", "eq", True)], default=False)
    add("elution", "elution_plate_dry_time", "Plate dry time", "number", "p.27", types=("SPE",),
        when=[("elution_on", "eq", True), ("elution_plate_dry", "eq", True)], unit="s", min=0.0, max=3600.0, step=1.0, default=60.0)
    add("elution", "elution_air_push", "Air push after last elution?", "bool", "p.27", types=("SLE",), when=[("elution_on", "eq", True)], default=False)
    add("elution", "elution_air_push_time", "Air push time", "number", "p.27", types=("SLE",),
        when=[("elution_on", "eq", True), ("elution_air_push", "eq", True)], unit="s", min=0.0, max=3600.0, step=1.0, default=30.0)
    load_types = _EXH_OPS["load"][1]
    lw = [("load_on", "eq", True)]
    ppt = ("PPT/PLD",)
    add("load", "load_solvent", "Solvent", "lib:solvents", "p.28", types=ppt, when=lw, required=True)
    add("load", "load_volume", "Volume", "number", "p.28", types=ppt, when=lw, unit="µL", min=1.0, max=10000.0, step=1.0, required=True)
    add("load", "dispose_solvent_tips", "Dispose solvent tips?", "bool", "p.27", types=ppt, when=lw + [("ppt_mode", "eq", "Solvent First")], default=True)
    add("load", "sample_volume", "Sample volume", "number", "p.28", types=load_types, when=lw, unit="µL", min=1.0, max=10000.0, step=1.0, required=True)
    add("collection", "filt_sample_volume", "Sample volume", "number", "p.28 (Table 2 note 4: load incorporated into collection)", types=("Filtration+",),
        when=[("collection_on", "eq", True)], unit="µL", min=1.0, max=10000.0, step=1.0, required=True)
    sp = ("SPE", "Hydro DME+")
    add("load", "load_collect", "Collect in position", "enum", "p.27", types=sp, when=lw, options=_EXH_COLLECT, default="D (Waste)")
    add("load", "load_pressure", "Pressure", "number", "p.28", types=sp, when=lw + [("load_adv", "eq", False)], unit="bar", min=0.0, max=5.0, step=0.1, default=0.0)
    add("load", "load_pp_time", "Positive pressure time", "number", "p.27", types=sp, when=lw + [("load_adv", "eq", False)], unit="s", min=0.0, max=3600.0,
        step=1.0, default=0.0)
    add("load", "load_wait", "Wait time", "number", "p.28", types=("SLE", "SPE", "Hydro DME+"), when=lw, unit="min", min=0.0, max=600.0, step=0.5, default=0.0)
    add("load", "load_pause", "Pause after each load?", "bool", "p.27", types=("SLE", "SPE", "Hydro DME+"), when=lw, default=False)
    F += [dict(f, tab="load") for f in _exh_adv_fields("load", ("SLE", "SPE"), lw, "p.28")]
    add("load", "rinsing", "Rinsing?", "bool", "p.28", types=("SPE",), when=lw, default=False)
    add("load", "rinse_solvent", "Rinse solvent", "lib:solvents", "p.28", types=("SPE",), when=lw + [("rinsing", "eq", True)], required=True)
    add("load", "rinse_volume", "Rinse volume", "number", "p.28", types=("SPE",), when=lw + [("rinsing", "eq", True)], unit="µL", min=1.0, max=10000.0,
        step=1.0, required=True)
    add("load", "rinse_repeat", "Rinse repeat number of times", "int", "p.28", types=("SPE",), when=lw + [("rinsing", "eq", True)], min=1, max=10, step=1,
        default=1)
    ct = _EXH_OPS["collection"][1]
    cw = [("collection_on", "eq", True)]
    add("collection", "collection_collect", "Collect in position", "enum", "p.27", types=ct, when=cw, options=_EXH_COLLECT[:3], default="A")
    add("collection", "collection_height", "Collection plate height", "number", "p.27, p.29 (Table 3)", types=("Filtration",), when=cw, unit="%", min=0.0,
        max=100.0, step=1.0, default=80.0)
    add("collection", "collection_pressure", "Pressure", "number", "p.28", types=ct, when=cw + [("collection_adv", "eq", False)], unit="bar", min=0.0,
        max=5.0, step=0.1, default=0.0)
    add("collection", "collection_pp_time", "Positive pressure time", "number", "p.27", types=ct, when=cw + [("collection_adv", "eq", False)], unit="s",
        min=0.0, max=3600.0, step=1.0, default=0.0)
    add("collection", "collection_wait", "Wait time", "number", "p.28", types=ct, when=cw, unit="min", min=0.0, max=600.0, step=0.5, default=0.0)
    F += [dict(f, tab="collection") for f in _exh_adv_fields("collection", ct, cw, "p.28")]
    return F


EXTRAHERA_METHOD_FIELDS = _exh_method_fields()
_EXH_MF = {f["key"]: f for f in EXTRAHERA_METHOD_FIELDS}


def _exh_field_schema(f, nullable=True):
    kind = f["type"]
    if kind == "text": s = {"type": "string", "maxLength": f.get("max_len", 200)}
    elif kind == "bool": s = {"type": "boolean"}
    elif kind == "enum": s = {"type": "string", "enum": list(f["options"])}
    elif kind == "int": s = {"type": "integer", "minimum": f["min"], "maximum": f["max"], "x-unit": "1"}
    elif kind == "number": s = {"type": "number", "minimum": f["min"], "maximum": f["max"], "x-unit": f.get("unit", "1")}
    else: s = {"type": "string"}                                            # library entry id
    return {"anyOf": [s, {"type": "null"}]} if nullable and not (kind == "text" and not f.get("required")) else s


def _exh_relevant(f, values, mtype=None):
    if mtype is not None and mtype not in f.get("types", ALL_T):
        return False
    for key, op, want in f.get("when", []):
        v = values.get(key, _EXH_MF[key].get("default") if key in _EXH_MF else None)
        if op == "eq" and v != want: return False
        if op == "ge" and (v is None or v < want): return False
    return True


def _exh_missing(method):
    """Required fields of a method that are empty (the exclamation marks, p.25)."""
    return [f["key"] for f in EXTRAHERA_METHOD_FIELDS if f.get("required") and _exh_relevant(f, method, method["type"]) and method.get(f["key"]) in (None, "")]


def _exh_new_method(mid, mtype, ppt_mode, folder="user", **values):
    method = {f["key"]: f.get("default") for f in EXTRAHERA_METHOD_FIELDS}
    method.update(id=mid, folder=folder, type=mtype, ppt_mode=ppt_mode if mtype == "PPT/PLD" else None, run_count=0, last_duration_s=None)
    method.update(values)
    method["missing"] = _exh_missing(method)
    method["complete"] = not method["missing"]
    return method


def _exh_library_id(kind, i):
    return f"{kind[:3]}-{i:03d}"


def _exh_new_device(time_s):
    libraries = {}
    for kind, entries in _EXH_PREDEFINED.items():
        libraries[kind] = [dict(e, id=_exh_library_id(kind, i + 1), predefined=True) for i, e in enumerate(entries)]
    lib = {kind: {e["name"]: e["id"] for e in entries} for kind, entries in libraries.items()}
    methods = [   # two predefined Biotage methods (names from p.25 Fig. 46; their parameters are not printed: assumptions)
        _exh_new_method("bio-001", "SPE", None, folder="biotage", name="EVOLUTE ABN (acid, base, neutral), 1 mL columns, 24",
                        extraction_media=lib["extraction_media"]["1 mL Column Rack, 24"], sample_plate=lib["sample_plates"]["12 x 75 mm Test Tubes, 24"],
                        sample_type=lib["sample_types"]["Aqueous Sample"], sample_tip=lib["pipette_tips"]["1000 µL Biotage tip"], starting_sample=1000.0,
                        conditioning1_solvent=lib["solvents"]["Methanol"], conditioning1_volume=1000.0, conditioning1_pressure=1.0, conditioning1_pp_time=60.0,
                        equilibration1_solvent=lib["solvents"]["Water"], equilibration1_volume=1000.0, equilibration1_pressure=1.0, equilibration1_pp_time=60.0,
                        sample_volume=750.0, load_pressure=1.0, load_pp_time=120.0,
                        wash1_solvent=lib["solvents"]["2% Formic acid"], wash1_volume=1000.0, wash1_pressure=1.0, wash1_pp_time=60.0,
                        elution1_solvent=lib["solvents"]["Methanol"], elution1_volume=1000.0, elution1_pressure=1.0, elution1_pp_time=120.0),
        _exh_new_method("bio-002", "PPT/PLD", "Solvent First", folder="biotage", name="ISOLUTE PPT+ protein precipitation, 96",
                        extraction_media=lib["extraction_media"]["Extraction Plate, 96"], sample_plate=lib["sample_plates"]["2 mL Sample Plate, 96"],
                        sample_type=lib["sample_types"]["Plasma"], sample_tip=lib["pipette_tips"]["1000 µL Biotage tip"], starting_sample=300.0,
                        load_solvent=lib["solvents"]["Acetonitrile"], load_volume=600.0, sample_volume=200.0, collection_pressure=1.5,
                        collection_pp_time=180.0),
    ]
    settings = {f["key"]: f["default"] for f in EXTRAHERA_SETTINGS}
    return {"system.state": "off", "settings.values": settings, "library.entries": libraries, "solvent.locks": {b: None for b in EXTRAHERA_BOTTLES},
            "methods.list": methods, "run.setup": None, "run.state": _exh_idle_run(),
            "inlets.state": {b: {"assigned": None, "line": None, "primed_m3": 0.0} for b in EXTRAHERA_BOTTLES},
            "manual.state": {"door": "closed", "waste_collector": "lowered", "pipette_head": "right", "carousel_position": "D", "carousel_lifted": False,
                             "waste_valve": "closed", "plate_dry": False, "lifter_height_pct": None},
            "alarm.state": {"color": None, "sounding": False}, "lights.state": {"upper": False, "lower": False},
            "pump.calibration": {"coefficient": 1.0, "offset_ul": 0.0, "date": _g03_clock(0.0), "history": [], "pending": None, "expired": False},
            "pump.error": {"gain": 0.0, "offset_m3": 0.0}, "reports.list": [], "system.notice": None}


def _exh_idle_run():
    return {"state": "idle", "method_id": None, "method_name": None, "individual": False, "operations": [], "operation_index": None, "operation": None,
            "action": None, "elapsed_s": 0.0, "estimated_left_s": None, "pause_queued": False, "abort_queued": False, "pause_reason": None,
            "pause_text": None, "sample_tips_left": EXTRAHERA_TIP_RACK, "solvent_tips_disposed": 0, "report_id": None, "error": None, "comment": ""}


_EXH_ANY = {"type": "object"}
EXTRAHERA_DEVICE_SLOTS = {
    "system.state": ("1", _g03_str(["off", "starting", "ready", "shut_down"]), "软件状态：断电、启动画面、就绪、已关机（可安全断电）（p.47）"),
    "settings.values": ("1", {"type": "object"}, "维护与系统管理设置：报警声、上下照明灯、仪器类型、PPT/PLD模式、移液泵校准提醒（p.19, p.46）"),
    "library.entries": ("1", {"type": "object"}, "数据管理库：溶剂、样品类型、萃取介质、样品板/架、吸头（预定义+自定义）（p.20-24）"),
    "solvent.locks": ("1", {"type": "object"}, "锁定到S1-S5储液槽的溶剂（p.22）"),
    "methods.list": ("1", {"type": "array", "items": {"type": "object"}}, "方法库（Biotage预定义与用户方法；缺必填项的标感叹号）（p.25-28）"),
    "run.setup": ("1", {"anyOf": [{"type": "object"}, {"type": "null"}]}, "Prepare Run设置：列/位置、吸头、溶剂瓶、6号位、报告名（p.32-37）"),
    "run.state": ("1", {"type": "object"}, "运行状态：运行/暂停/中止/完成、当前操作与动作、已用时间、预计剩余、暂停原因、已丢弃的溶剂吸头数（p.27, p.40-41）"),
    "inlets.state": ("1", {"type": "object"}, "S1-S5溶剂进液管：所分配溶剂、管路中溶剂、已灌注体积（p.35, p.48）"),
    "manual.state": ("1", {"type": "object"}, "手动控制：门、废液收集器、移液头、转盘位置/升降、废液阀、板干燥；Filtration收集时升降台高度（最大高度的%，p.27, p.29）（p.53）"),
    "alarm.state": ("1", {"type": "object"}, "报警：背景闪烁颜色（蓝=暂停，红=系统错误）与蜂鸣（p.3, p.40）"),
    "lights.state": ("1", {"type": "object"}, "仪器内上/下照明灯是否点亮（p.3, p.46）"),
    "pump.calibration": ("1", {"type": "object"}, "移液泵校准系数与偏移、校准日期、历史、待保存的计算结果、是否过期（p.19, p.50-51）"),
    "reports.list": ("1", {"type": "array", "items": {"type": "object"}}, "运行报告（p.42）"),
    "system.notice": ("1", {"anyOf": [{"type": "string"}, {"type": "null"}]}, "待确认的系统通知（如移液泵校准过期）"),
}
for _exh_sid, (_exh_unit, _exh_schema, _) in EXTRAHERA_DEVICE_SLOTS.items():
    EXTRAHERA_STATE_SLOTS[_exh_sid] = _extrahera_slot(_exh_unit, "derived", _exh_schema)
EXTRAHERA_STATE_SLOTS["pump.error"] = _extrahera_slot("1", "setpoint", {"type": "object"}, "evaluator")

_EXH_LIB_KINDS = tuple(EXTRAHERA_LIBRARY_FIELDS)
_EXH_SETTING_SCHEMA = _g03_obj({f["key"]: _exh_field_schema(f, nullable=False) for f in EXTRAHERA_SETTINGS}, optional=[f["key"] for f in EXTRAHERA_SETTINGS])
_EXH_ENTRY_SCHEMA = {"type": "object"}
EXTRAHERA_DEVICE_ARGS = {   # name: (inputs, optional, outputs, example, description, effects, components, kind)
    "settings.configure": ({"settings": _EXH_SETTING_SCHEMA}, (), {"settings": {"type": "object"}}, {"settings": {"audible_alarm": False}},
                           "改维护/系统管理设置（报警声、照明、仪器类型、PPT/PLD模式、校准提醒）；仪器类型须空闲", ["settings.values", "lights.state"], ["touchscreen"], "configuration"),
    "library.save": ({"kind": _g03_str(_EXH_LIB_KINDS), "entry_id": _g03_str(nullable=True), "entry": _EXH_ENTRY_SCHEMA}, (), {"entry_id": {"type": "string"}},
                     {"kind": "solvents", "entry_id": None, "entry": {"name": "Ethyl acetate"}}, "新建（entry_id为空）或编辑自定义库条目；预定义条目不可改", ["library.entries"],
                     ["touchscreen"], "configuration"),
    "library.delete": ({"kind": _g03_str(_EXH_LIB_KINDS), "entry_id": {"type": "string"}}, (), {"deleted": {"type": "boolean"}},
                       {"kind": "solvents", "entry_id": "sol-007"}, "删除未被方法使用或锁定的自定义库条目", ["library.entries"], ["touchscreen"], "configuration"),
    "library.copy": ({"kind": _g03_str(_EXH_LIB_KINDS), "entry_id": {"type": "string"}}, (), {"entry_id": {"type": "string"}},
                     {"kind": "solvents", "entry_id": "sol-001"}, "复制库条目为自定义条目（名称加 copy）", ["library.entries"], ["touchscreen"], "configuration"),
    "solvent.lock": ({"locks": _g03_obj({b: _g03_str(nullable=True) for b in EXTRAHERA_BOTTLES}, optional=EXTRAHERA_BOTTLES)}, (), {"locks": {"type": "object"}},
                     {"locks": {"S1": "sol-001"}}, "把溶剂锁定到S1-S5储液槽或解锁；锁定后该槽只能用该溶剂", ["solvent.locks", "inlets.state"], ["solvent_reservoirs"],
                     "configuration"),
    "methods.save": ({"method_id": _g03_str(nullable=True), "method_type": _g03_str(EXTRAHERA_METHOD_TYPES, nullable=True), "values": {"type": "object"}}, (),
                     {"method_id": {"type": "string"}, "complete": {"type": "boolean"}},
                     {"method_id": None, "method_type": "SPE", "values": {"name": "My SPE"}}, "保存新方法（method_id为空，需method_type）或编辑用户方法；缺必填项也可保存但不能运行",
                     ["methods.list"], ["touchscreen"], "configuration"),
    "methods.copy": ({"method_id": {"type": "string"}}, (), {"method_id": {"type": "string"}}, {"method_id": "bio-001"}, "复制方法到用户方法文件夹",
                     ["methods.list"], ["touchscreen"], "configuration"),
    "methods.delete": ({"method_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, (), {"deleted": {"type": "integer", "x-unit": "1"}},
                       {"method_ids": ["usr-001"]}, "删除一个或多个用户方法（预定义方法不可删）", ["methods.list"], ["touchscreen"], "configuration"),
    "run.prepare": ({"method_id": {"type": "string"}, "individual": {"type": "boolean"}}, (), {"state": {"type": "string"}},
                    {"method_id": "bio-001", "individual": False}, "Run Method / Run Individual Samples：选方法进入Prepare Run（方法须完整且格式与仪器类型相符）",
                    ["run.setup", "run.state"], ["touchscreen"], "device_command"),
    "run.setup": ({"section": _g03_str(["extraction", "solvent_tips", "sample_tips", "bottles", "position6", "report"]), "values": {"type": "object"}}, (),
                  {"section": {"type": "string"}}, {"section": "solvent_tips", "values": {"tip_type": "tip-001"}},
                  "保存Prepare Run中的一个设置（3号位列/位置、1/2号位吸头、5号位溶剂瓶分配、6号位储液槽、报告名与备注）", ["run.setup", "inlets.state"],
                  ["touchscreen", "deck_slots", "tip_rack", "solvent_reservoirs"], "configuration"),
    "run.prime": ({"inlet": _g03_str(EXTRAHERA_BOTTLES), "volume_m3": _g03_num("m^3", 1e-6, 25e-6)}, (), {"primed_m3": _g03_num("m^3")},
                  {"inlet": "S1", "volume_m3": 15e-6}, "灌注一个溶剂进液管（每次≤25 mL；约2×15 mL后管路换成新溶剂）", ["inlets.state"], ["solvent_reservoirs", "rear_ports"],
                  "device_command"),
    "run.start": ({}, (), {"control_status": {"type": "string", "const": "started"}}, {}, "Run对话框的Run：按方法编译成任务在集总液体平衡上运行（异步，宿主step推进）",
                  ["run.state", "method.status", "method.phase", "operation.active_id", "pressure.target_pa", "manifold.z_m", "deck.format",
                   "deck.cartridge_volume_m3", "deck.collection_plate", "deck.solvent_positions", "reports.list", "alarm.state"],
                  ["touchscreen", "pipette_head", "pressure_manifold", "deck_slots", "solvent_reservoirs", "waste_tray", "collection_plate"], "device_command"),
    "run.control": ({"action": _g03_str(["pause", "cancel_pause", "resume", "abort", "restore", "close", "exit_prepare"]), "comment": _g03_str(max_len=200)},
                    ("comment",), {"state": {"type": "string"}}, {"action": "pause"},
                    "运行控制：暂停（当前任务完成后）、取消暂停、继续（门须关）、中止、Restore复位、运行结束后关闭、退出Prepare Run",
                    ["run.state", "alarm.state", "method.status", "method.phase", "operation.active_id", "pressure.target_pa", "manifold.z_m", "reports.list"],
                    ["touchscreen"], "device_command"),
    "alarm.silence": ({}, (), {"sounding": {"type": "boolean"}}, {}, "触摸屏幕使报警声停止（p.3）", ["alarm.state"], ["touchscreen"], "device_command"),
    "door.set": ({"open": {"type": "boolean"}}, (), {"door": {"type": "string"}}, {"open": True},
                 "开/关门（关门后转盘归位、压力头收回、移液头右移、废液收集器降下）", ["manual.state", "manifold.z_m", "pressure.target_pa", "gantry.x_m"],
                 ["safety_shield"], "device_command"),
    "manual.control": ({"action": _g03_str(["waste_collector_raise", "waste_collector_lower", "pipette_head_left", "pipette_head_right", "pressure_unit_in",
                                            "pressure_unit_out", "carousel_collect", "carousel_front", "waste_valve_open", "waste_valve_close", "plate_dry_start",
                                            "plate_dry_stop"]), "position": _g03_str(["A", "B", "C", "D"])}, ("position",), {"action": {"type": "string"}},
                       {"action": "pressure_unit_out"}, "Manual Control的按钮（开门时除废液阀外全部禁用；互锁见p.53）",
                       ["manual.state", "manifold.z_m", "pressure.target_pa", "gantry.x_m"], ["pressure_manifold", "collection_carousel", "waste_tray", "pipette_head"],
                       "device_command"),
    "pump.calibration": ({"action": _g03_str(["calculate", "apply", "save", "cancel", "restore"]), "desired_1_ul": _g03_num("µL", 1, 1000),
                          "obtained_1_ul": _g03_num("µL", 0, 2000), "desired_2_ul": _g03_num("µL", 1, 1000), "obtained_2_ul": _g03_num("µL", 0, 2000),
                          "history_index": _g03_num("1", 0, 100, integer=True)},
                         ("desired_1_ul", "obtained_1_ul", "desired_2_ul", "obtained_2_ul", "history_index"), {"calibration": {"type": "object"}},
                         {"action": "calculate", "desired_1_ul": 100, "obtained_1_ul": 98, "desired_2_ul": 1000, "obtained_2_ul": 990},
                         "Calibrate Pipette Pump：两点期望/实测体积计算系数与偏移、Apply、Save，或恢复历史值（p.50-51）", ["pump.calibration"], ["pipette_head"],
                         "configuration"),
    "inlets.flush": ({"inlet": _g03_str(EXTRAHERA_BOTTLES), "volume_m3": _g03_num("m^3", 1e-6, 50e-6)}, (), {"line": _g03_str(nullable=True)},
                     {"inlet": "S1", "volume_m3": 25e-6}, "Flush Solvent Inlets：用空气冲洗一根进液管（25 mL排空管路与泵）", ["inlets.state"], ["rear_ports", "solvent_reservoirs"],
                     "device_command"),
    "system.reinitialize": ({}, (), {"state": {"type": "string"}}, {}, "Re-Initialize System：移液头、压力单元、转盘与升降复位", ["manual.state", "manifold.z_m",
                            "pressure.target_pa", "gantry.x_m", "gantry.y_m", "gantry.z_m"], ["pipette_head", "pressure_manifold", "collection_carousel"],
                            "device_command"),
    "reports.delete": ({"report_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, (), {"deleted": {"type": "integer", "x-unit": "1"}},
                       {"report_ids": ["rep-001"]}, "删除一个或多个运行报告（不可恢复）", ["reports.list"], ["touchscreen"], "configuration"),
    "system.shutdown": ({}, (), {"state": {"type": "string"}}, {}, "Shut Down + Yes：软件关机，显示可以安全断电（之后用主电源开关断电）", ["system.state", "lights.state",
                        "pressure.target_pa"], ["touchscreen"], "device_command"),
    "system.notice_ack": ({}, (), {"notice": _g03_str(nullable=True)}, {}, "确认系统通知（移液泵校准过期提示）", ["system.notice"], ["touchscreen"], "device_command"),
    "pump.configure_error": ({"gain": _g03_num("1", -0.2, 0.2), "offset_m3": _g03_num("m^3", -50e-9, 50e-9)}, (), {"error": {"type": "object"}},
                             {"gain": 0.0, "offset_m3": 0.0}, "评测者：移液泵体积误差（相对增益、偏移），默认0（理想）", ["pump.error"], ["pipette_head"], "configuration"),
}
EXTRAHERA_EVALUATOR_CAPS = ("pump.configure_error",)
_EXH_BINDING = "basic40_runtime_vacuum.ExtraheraSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"
EXTRAHERA_DEVICE_ERRORS = {   # device refusals (the panel maps each to the instrument's message)
    "DOOR_OPEN": "The door is open", "NOT_READY": "The system is not ready", "PREDEFINED": "Predefined entries cannot be edited or deleted",
    "IN_USE": "The entry is used in a method or locked", "NAME_EXISTS": "The name is already used", "INCOMPLETE": "The method is incomplete",
    "WRONG_FORMAT": "The method does not match the instrument type", "SETUP_INCOMPLETE": "The run setup is incomplete", "WRONG_TIP": "Wrong pipette tip type",
    "NOT_PRIMED": "A solvent inlet must be primed", "NOT_ENOUGH_SOLVENT": "Not enough solvent", "UTILITIES": "Gas or vacuum supply not ready",
    "LOCKED_SOLVENT": "A locked solvent cannot be moved", "INTERLOCK": "Not possible in the current position", "NO_LIQUID": "No liquid inventory configured",
    "NOT_RUNNING": "No run in this state", "NO_RESULT": "Nothing calculated", "NOT_ENOUGH_SAMPLE": "Not enough sample in the sample plate/rack",
}


def _exh_lib(state, kind, entry_id):
    return next((e for e in state["library.entries"][kind] if e["id"] == entry_id), None)


def _exh_method(state, method_id):
    return next((m for m in state["methods.list"] if m["id"] == method_id), None)


def _exh_geometry(state, method):
    media = _exh_lib(state, "extraction_media", method.get("extraction_media"))
    return EXTRAHERA_INSTRUMENT_TYPES[media["format"] if media else state["settings.values"]["instrument_type"]]


def _exh_wells(setup, geometry):
    cols, rows, channels, max_ind = geometry
    if setup["individual"]:
        return setup["extraction"]["n_samples"] if setup["extraction"] else 0
    ex = setup["extraction"]
    if not ex: return 0
    return rows * (cols if ex["use_all_columns"] else len(ex["columns"]))


def _exh_solvents_used(method):
    """Solvent entry ids a method uses, with the volume per well (µL) of each."""
    used = {}

    def take(sid, ul):
        if sid: used[sid] = used.get(sid, 0.0) + (ul or 0.0)
    rel = lambda k: _EXH_MF[k] and _exh_relevant(_EXH_MF[k], method, method["type"])   # noqa: E731
    if rel("pre_solvent"): take(method["pre_solvent"], method["pre_volume"])
    for op in EXTRAHERA_STEP_OPS:
        for k in range(1, EXTRAHERA_MAX_STEPS + 1):
            if rel(f"{op}{k}_solvent"): take(method[f"{op}{k}_solvent"], (method[f"{op}{k}_volume"] or 0.0) * method[f"{op}{k}_repeat"])
    if rel("load_solvent"): take(method["load_solvent"], method["load_volume"])
    if rel("rinse_solvent"): take(method["rinse_solvent"], (method["rinse_volume"] or 0.0) * method["rinse_repeat"])
    if rel("conditioning_solvent"): take(method["conditioning_solvent"], 0.0)
    return used


def _exh_source_of(state, setup, solvent_id):
    for b in EXTRAHERA_BOTTLES:
        if state["inlets.state"][b]["assigned"] == solvent_id: return b
    if setup.get("position6") and setup["position6"]["size"]:
        for i, s in enumerate(setup["position6"]["reservoirs"]):
            if s == solvent_id: return EXTRAHERA_POS6[i]
    return None


def _exh_pump_volume(state, ul):
    """Volume the pipette pump delivers for a requested volume: the calibration (coefficient, offset) sets the command, the pump error
    (evaluator-set, 0 by default) acts on it."""
    cal, err = state["pump.calibration"], state["pump.error"]
    cmd = ul * cal["coefficient"] + cal["offset_ul"]
    return max(0.0, cmd * (1.0 + err["gain"]) + err["offset_m3"] * 1e9)


def _exh_pump_m3(state, volume_m3):
    """_exh_pump_volume for a CHEM-031 stroke in m^3 (sample.aspirate / dispense, method.start): the same pump model as the runs; without
    calibration and error the requested volume is kept exactly."""
    cal, err = state["pump.calibration"], state["pump.error"]
    if cal["coefficient"] == 1.0 and cal["offset_ul"] == 0.0 and err["gain"] == 0.0 and err["offset_m3"] == 0.0:
        return volume_m3
    return _exh_pump_volume(state, volume_m3 * 1e9) * 1e-9


def _exh_class_time(entry):
    """Seconds one pipetting cycle adds for the liquid class of a solvent or sample type (p.21-22): the lower and the upper air gap are
    aspirated and dispensed at their flow rates, the upper-air-gap dispense pause, and the post-dispense air aspiration when enabled."""
    e, t = entry or {}, 0.0
    for volume, flow in (("lower_gap_volume", "lower_gap_flow"), ("upper_gap_volume", "upper_gap_flow")):
        if e.get(volume) and e.get(flow):
            t += 2.0 * e[volume] / 1000.0 / e[flow] * 60.0
    t += (e.get("upper_gap_pause") or 0.0) / 1000.0
    if e.get("post_asp") and e.get("post_asp_volume") and e.get("post_asp_flow"):
        t += e["post_asp_volume"] / 1000.0 / e["post_asp_flow"] * 60.0
    return t


def _exh_sample_need(method):
    """(taken, available) sample volume per well or tube of the sample plate/rack (µL): the sample load (or the Filtration+ collection load)
    against the Starting sample volume plus the pretreatment solvent dispensed into it (p.28); None when no starting volume is entered (0)."""
    start = method.get("starting_sample") or 0.0
    if start <= 0 or method["type"] == "Filtration":
        return None
    rel = lambda k: _exh_relevant(_EXH_MF[k], method, method["type"])   # noqa: E731
    taken = ((method.get("sample_volume") or 0.0) if rel("sample_volume") else 0.0) + \
        ((method.get("filt_sample_volume") or 0.0) if rel("filt_sample_volume") else 0.0)
    return taken, start + ((method.get("pre_volume") or 0.0) if rel("pre_volume") else 0.0)


def _exh_compile(state, setup, method):
    """The run as a task list on the lumped balance: liquid transfers (source -> extraction / sample plate), pressure drains (extraction ->
    waste or collection), waits, gas pushes, scheduled pauses.  Durations from the library flow rates and liquid classes (air gaps, dispense
    pause, post-dispense aspiration, p.21-22), channels (p.57) and times; tip conditioning (solvent library, method Tip conditioning?) and
    solvent tip disposal (p.27) add their cycles.  Returns (tasks, operations, wells, info): info counts the solvent tip sets (p.33) and the
    conditioning solvent per entry (µL, nominal)."""
    lib = lambda kind, i: _exh_lib(state, kind, i) or {}   # noqa: E731
    geometry = _exh_geometry(state, method)
    cols, rows, channels, _ = geometry
    wells = _exh_wells(setup, geometry)
    tip = lib("pipette_tips", method.get("sample_tip"))
    tip_cap = (tip.get("capacity") or 1000.0)
    solvent_tip_cap = lib("pipette_tips", (setup.get("solvent_tips") or {}).get("tip_type")).get("capacity") or tip_cap
    tasks, ops = [], []
    rel = lambda k: k in _EXH_MF and _exh_relevant(_EXH_MF[k], method, method["type"])   # noqa: E731
    tipsets = {"active": set(), "count": 0}                                 # solvent tip sets: one per solvent, reused unless disposed (p.33)
    conditioning = {}

    def operation(label):
        ops.append(label)

    def add_task(**task):
        task.setdefault("elapsed_s", 0.0)
        task["label"] = ops[-1] if ops else "Preparations"
        tasks.append(task)

    def cycles_of(ul):
        return math.ceil(wells / channels) * max(1, math.ceil((ul or 0.0) / tip_cap - 1e-9))

    def liquid(op, text, source, dest, ul, flows, sample_tips=0, cls=None):
        aliquots = max(1, math.ceil((ul or 0.0) / tip_cap - 1e-9))
        per = (ul or 0.0) / aliquots
        cycles = math.ceil(wells / channels) * aliquots
        asp, disp = flows
        duration = cycles * (EXTRAHERA_CYCLE_OVERHEAD_S + per / 1000.0 / asp * 60.0 + per / 1000.0 / disp * 60.0 + _exh_class_time(cls))
        delivered = _exh_pump_volume(state, per) * aliquots * wells * 1e-9
        add_task(kind="liquid", op=op, text=text, source=source, dest=dest, volume_m3=delivered, duration_s=duration, sample_tips=sample_tips, moved_m3=0.0)

    def condition(op, sid, entry, cap_ul, tip_strokes, time_strokes, text):
        """Tip conditioning (p.21, p.28): each stroke fills Conditioning volume (% of the tip capacity) of the solvent at the conditioning flow
        rate and dispenses it to waste; tip_strokes counts the tips x strokes (liquid), time_strokes the parallel strokes of the head (time)."""
        vol = (entry.get("cond_volume_pct") or 0.0) / 100.0 * cap_ul
        if vol <= 0 or tip_strokes <= 0:
            return
        flow = entry.get("cond_flow") or 5.0
        conditioning[sid] = conditioning.get(sid, 0.0) + tip_strokes * vol
        add_task(kind="liquid", op=op, text=text, source=_exh_source_of(state, setup, sid), dest="waste",
                 volume_m3=_exh_pump_volume(state, vol) * tip_strokes * 1e-9, duration_s=time_strokes * 2.0 * vol / 1000.0 / flow * 60.0,
                 sample_tips=0, moved_m3=0.0)

    def solvent_liquid(op, text, sid, dest, ul, new_step=True):
        """A solvent dispensed with the solvent tips of its tip set; the solvent's tip conditioning (Requires tip conditioning?) first."""
        entry = lib("solvents", sid)
        fresh = sid not in tipsets["active"]
        if fresh:
            tipsets["active"].add(sid)
            tipsets["count"] += 1
        if entry.get("tip_cond"):
            freq = entry.get("cond_frequency")
            events = cycles_of(ul) if freq == "Before every aspiration" else 1 if fresh or (freq == "Before each step" and new_step) else 0
            strokes = events * (entry.get("cond_times") or 1)
            condition(op, sid, entry, solvent_tip_cap, strokes * min(channels, wells), strokes, f"Conditioning the solvent tips with {entry.get('name', '?')}")
        liquid(op, text, _exh_source_of(state, setup, sid), dest, ul, solvent_flows(sid), cls=entry)

    def dispose(op, sids):
        gone = [x for x in dict.fromkeys(sids) if x in tipsets["active"]]
        if not gone:
            return
        tipsets["active"].difference_update(gone)
        add_task(kind="wait", op=op, text="Disposing of the solvent pipette tips", duration_s=EXTRAHERA_CYCLE_OVERHEAD_S,
                 dispose_tips=len(gone) * min(channels, wells))

    def drain(op, dest, pressure_bar, time_s, adv_prefix=None, gravity=False, lift_pct=None):
        steps = []
        if adv_prefix and method.get(f"{adv_prefix}_adv"):
            for j in range(1, (method.get(f"{adv_prefix}_adv_n") or 1) + 1):
                steps.append((method.get(f"{adv_prefix}_adv{j}_pressure") or 0.0, method.get(f"{adv_prefix}_adv{j}_time") or 0.0))
        else:
            steps.append((pressure_bar or 0.0, time_s or 0.0))
        for p, t in steps:
            add_task(kind="drain", op=op, text=f"Applying {p:.1f} bar for {t:.0f} s" if not gravity else f"Waiting for elution ({t:.0f} s)",
                     dest=dest, pressure_pa=round(p * 10.0) * 10000.0, duration_s=float(t), flows=bool(p > 0 or gravity), moved_m3=0.0, holdup_m3=None,
                     lift_pct=lift_pct)
        if adv_prefix and method.get(f"{adv_prefix}_adv") and method.get(f"{adv_prefix}_adv_finish"):
            t = method.get(f"{adv_prefix}_adv_finish_time") or 0.0
            gas(op, "Air push" if method["type"] == "SLE" else "Plate dry", t)

    def gas(op, text, t):
        p = EXTRAHERA_AIR_PUSH_PA if text == "Air push" else EXTRAHERA_PLATE_DRY_PA
        add_task(kind="drain", op=op, text=f"{text} ({t:.0f} s)", dest=None, pressure_pa=p, duration_s=float(t), flows=True, moved_m3=0.0, holdup_m3=None,
                 gas=True)

    def wait(op, minutes):
        if minutes:
            add_task(kind="wait", op=op, text=f"Waiting {minutes:g} min", duration_s=minutes * 60.0)

    def pause(op, text):
        add_task(kind="pause", op=op, text=text, duration_s=0.0)

    def dest_of(position):
        return "waste" if (position or "D").startswith("D") else "collection"

    def solvent_flows(sid):
        s = lib("solvents", sid)
        return (s.get("asp_flow") or 5.0, s.get("disp_flow") or 5.0)

    stype = lib("sample_types", method.get("sample_type"))
    sample_flows = (stype.get("asp_flow") or 3.0, stype.get("disp_flow") or 3.0)
    sname = lambda sid: lib("solvents", sid).get("name", "?")   # noqa: E731

    def pretreatment():
        if not rel("pre_solvent"): return
        operation("Pretreatment")
        solvent_liquid("pretreatment", f"Dispensing {method['pre_volume']:g} µL of {sname(method['pre_solvent'])}", method["pre_solvent"], "sample",
                       method["pre_volume"])
        mixes = (method["premix_times"] if method.get("premix") else 0) + (method.get("mix_times") or 0)
        if mixes:
            t = mixes * math.ceil(wells / channels) * 2.0 * (method.get("mix_volume") or 0.0) / 1000.0 / sample_flows[0] * 60.0
            add_task(kind="wait", op="pretreatment", text=f"Mixing {mixes} times", duration_s=t, sample_tips=0 if method.get("reuse_tips") else wells)
        wait("pretreatment", method.get("pre_wait"))
        if method.get("pre_pause"): pause("pretreatment", "Scheduled pause after Pretreatment")

    def step_op(op):
        if not method.get(f"{op}_on"): return
        operation(_EXH_OPS[op][0])
        for k in range(1, (method.get(f"{op}_steps") or 1) + 1):
            s = f"{op}{k}"
            for r in range(method.get(f"{s}_repeat") or 1):
                solvent_liquid(op, f"Dispensing {method[f'{s}_volume']:g} µL of {sname(method[f'{s}_solvent'])}", method[f"{s}_solvent"], "extraction",
                               method[f"{s}_volume"], new_step=r == 0)
                wait(op, method.get(f"{s}_wait"))
                if method["type"] == "SLE":
                    drain(op, dest_of(method.get(f"{s}_collect")), 0.0, 0.0, adv_prefix=s, gravity=True)
                else:
                    drain(op, dest_of(method.get(f"{s}_collect")), method.get(f"{s}_pressure"), method.get(f"{s}_pp_time"), adv_prefix=s)
            if method.get(f"{op}_dispose_tips"): dispose(op, [method[f"{s}_solvent"]])
            if method.get(f"{s}_pause"): pause(op, f"Scheduled pause after {_EXH_OPS[op][0]} step {k}")
        if op == "wash" and rel("wash_plate_dry_time"): gas(op, "Plate dry", method["wash_plate_dry_time"])
        if op == "elution" and rel("elution_plate_dry_time"): gas(op, "Plate dry", method["elution_plate_dry_time"])
        if op == "elution" and rel("elution_air_push_time"): gas(op, "Air push", method["elution_air_push_time"])

    def sample_load(op="load"):
        if rel("conditioning_solvent") and method.get("conditioning_solvent"):      # Tip conditioning? (SPE): each sample tip before its load
            entry = lib("solvents", method["conditioning_solvent"])
            times = entry.get("cond_times") or 1
            condition(op, method["conditioning_solvent"], entry, tip_cap, wells * times, math.ceil(wells / channels) * times,
                      f"Conditioning the sample tips with {entry.get('name', '?')}")
        liquid(op, f"Loading {method['sample_volume']:g} µL of sample", "sample", "extraction", method["sample_volume"], sample_flows, sample_tips=wells,
               cls=stype)

    t = method["type"]
    if t == "SPE":
        if method.get("pretreatment_on") and method.get("pretreatment_position") == "Before conditioning": pretreatment()
        step_op("conditioning")
        step_op("equilibration")
        if method.get("pretreatment_on") and method.get("pretreatment_position") == "Before load": pretreatment()
        if method.get("load_on"):
            operation("Sample Load")
            sample_load()
            if rel("rinse_solvent"):
                for _ in range(method["rinse_repeat"]):
                    solvent_liquid("load", f"Rinsing with {method['rinse_volume']:g} µL of {sname(method['rinse_solvent'])}", method["rinse_solvent"],
                                   "extraction", method["rinse_volume"])
            wait("load", method.get("load_wait"))
            drain("load", dest_of(method.get("load_collect")), method.get("load_pressure"), method.get("load_pp_time"), adv_prefix="load")
            if method.get("load_pause"): pause("load", "Scheduled pause after load")
        step_op("wash")
        step_op("elution")
    elif t == "SLE":
        if method.get("pretreatment_on"): pretreatment()
        if method.get("load_on"):
            operation("Sample Load")
            sample_load()
            wait("load", method.get("load_wait"))
            if method.get("load_adv"): drain("load", "waste", 0.0, 0.0, adv_prefix="load")
            if method.get("load_pause"): pause("load", "Scheduled pause after load")
        step_op("elution")
    elif t == "PPT/PLD":
        if method.get("load_on"):
            operation("Solvent Load")

            def solvent():
                solvent_liquid("load", f"Dispensing {method['load_volume']:g} µL of {sname(method['load_solvent'])}", method["load_solvent"], "extraction",
                               method["load_volume"])
                if rel("dispose_solvent_tips") and method.get("dispose_solvent_tips"): dispose("load", [method["load_solvent"]])
            if method.get("ppt_mode") == "Sample First":
                sample_load(); solvent()
            else:
                solvent(); sample_load()
    elif t in ("Filtration+", "Hydro DME+"):
        if method.get("pretreatment_on"): pretreatment()
        if t == "Hydro DME+" and method.get("load_on"):
            operation("Sample Load")
            sample_load()
            wait("load", method.get("load_wait"))
            if method.get("load_pause"): pause("load", "Scheduled pause after load")
    if t in ("PPT/PLD", "Filtration", "Filtration+", "Hydro DME+") and method.get("collection_on"):
        operation("Collection")
        if t == "Filtration+":
            liquid("collection", f"Loading {method['filt_sample_volume']:g} µL of sample", "sample", "extraction", method["filt_sample_volume"],
                   sample_flows, sample_tips=wells, cls=stype)
        wait("collection", method.get("collection_wait"))
        drain("collection", dest_of(method.get("collection_collect")), method.get("collection_pressure"), method.get("collection_pp_time"),
              adv_prefix="collection", lift_pct=method.get("collection_height") if rel("collection_height") else None)
    return tasks, ["Preparations"] + ops + ["Finish"], wells, {"solvent_tip_sets": tipsets["count"], "conditioning_ul": conditioning}


def _exh_consumption(state, setup, method, info):
    """Solvent use of a run per solvent entry (µL): the dispensed volumes per well x positions, plus the tip conditioning (p.35 Total
    Solvent Consumption)."""
    wells = setup["wells"] or 0
    used = {sid: ul * wells for sid, ul in _exh_solvents_used(method).items()}
    for sid, ul in info["conditioning_ul"].items():
        used[sid] = used.get(sid, 0.0) + ul
    return used


def _exh_summary(state, sources):
    """Method summary of the Prepare Run view (p.32 Fig. 57, p.35): media, plate, positions, solvent consumption per solvent and its source,
    reservoirs to prime, sources with too little solvent, too little sample in the sample plate, tips needed, highly volatile solvents in S1-S5,
    estimated run time."""
    setup = state["run.setup"]
    if setup is None:
        return None
    m = _exh_method(state, setup["method_id"])
    if m is None:
        return None
    name = lambda kind, i: (_exh_lib(state, kind, i) or {}).get("name", "<None>")   # noqa: E731
    wells = setup["wells"] or 0
    cols, rows, channels, _ = _exh_geometry(state, m)
    tasks, _, _, info = _exh_compile(state, setup, m)
    consumption, short = [], []
    for sid, ul in _exh_consumption(state, setup, m, info).items():
        src = _exh_source_of(state, setup, sid)
        ml = ul / 1000.0
        consumption.append({"solvent": sid, "name": name("solvents", sid), "source": src, "ml": ml})
        if src is None or sources is None or sources.get(src, 0.0) + 1e-15 < ml * 1e-6 + EXTRAHERA_DEAD_VOLUME_M3:
            short.append(name("solvents", sid))
    need = _exh_sample_need(m)
    inlets = state["inlets.state"]
    prime = [b for b in EXTRAHERA_BOTTLES if inlets[b]["assigned"] is not None and inlets[b]["line"] != inlets[b]["assigned"]]
    volatile = [f"{b} ({name('solvents', inlets[b]['assigned'])})" for b in EXTRAHERA_BOTTLES
                if inlets[b]["assigned"] is not None and (_exh_lib(state, "solvents", inlets[b]["assigned"]) or {}).get("volatile")]
    sample_tips = 0 if m["type"] == "Filtration" else wells * (2 if m.get("pretreatment_on") and m["type"] != "PPT/PLD" and not m.get("reuse_tips") else 1)
    estimate = sum(t["duration_s"] for t in tasks) if wells else None
    collect = sorted({(m.get(k) or "D")[0] for k in [f"{op}{i}_collect" for op in EXTRAHERA_STEP_OPS for i in range(1, EXTRAHERA_MAX_STEPS + 1)] +
                      ["load_collect", "collection_collect"] if k in _EXH_MF and _exh_relevant(_EXH_MF[k], m, m["type"])} - {"D"})
    return {"media_name": name("extraction_media", m.get("extraction_media")), "plate_name": name("sample_plates", m.get("sample_plate")),
            "method_tip_name": name("pipette_tips", m.get("sample_tip")), "wells": wells, "consumption": consumption,
            "consumption_text": "\n".join(f"{c['name']} ({c['source'] or 'not assigned'}): {c['ml']:.1f} mL" for c in consumption) or "none",
            "short": short, "sample_short": bool(need and need[0] > need[1] + 1e-9), "needs_priming": prime, "needs_priming_text": ", ".join(prime),
            "volatile": volatile, "volatile_text": ", ".join(volatile), "solvent_tips": info["solvent_tip_sets"] * channels, "sample_tips": sample_tips,
            "estimated_s": estimate, "collection_text": ("collection plate in " + ", ".join(collect)) if collect else "no collection plate"}


class _ExhRefused(Exception):
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code, self.detail = code, detail   # 1.3.1: detail = a message naming the cause


_g03_ExtraheraHostBase = ExtraheraHost
_g03_ExtraheraSessionBase = ExtraheraSession
_EXTRAHERA_PANEL = {}


def _extrahera_panel():
    """The panel engine and this asset's panel spec, loaded on first use (a broken spec must not stop the other instruments of this file)."""
    if not _EXTRAHERA_PANEL:
        engine = _g03_engine()
        caps = set(EXTRAHERA_CAPABILITY_ARGUMENTS) | set(EXTRAHERA_DEVICE_ARGS)
        spec = engine.load_spec(EXTRAHERA_ASSET_DIR / "panel_ui.json", capabilities=caps)
        _EXTRAHERA_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _EXTRAHERA_PANEL


class ExtraheraHost(_g03_ExtraheraHostBase):
    """CHEM-031 host with the touch-screen software: per instance the device state (slots above), the run engine and the front panel."""

    PANEL_PRINCIPAL = "__extrahera_panel__"

    def __init__(self, *, scene_adapter=None):
        super().__init__(scene_adapter=scene_adapter)
        self._panels = {}
        self.panel_capture_dir = None                                       # panel.capture files (default: <tmp>/chemistry_sim_panel/<instance>)
        self._principals[self.PANEL_PRINCIPAL] = ("agent", None)
        self._logs[self.PANEL_PRINCIPAL] = []
        self._panel_session = ExtraheraSession(self, self.PANEL_PRINCIPAL)
        self._panel_caller = None                                           # the session whose panel gesture is running (its principal acts)

    def create_instance(self, instance_id, asset_type_id=EXTRAHERA_ASSET_TYPE_ID):
        with self._lock:
            super().create_instance(instance_id, asset_type_id)
            inst = self._instances[instance_id]
            inst.state.update(_exh_new_device(self.time_s))
            inst.g03 = {"boot_until": None, "power_epoch": 0, "tasks": [], "task_index": 0, "serial": 0, "run_started_s": None, "report_owners": {}}
            if (EXTRAHERA_ASSET_DIR / "panel_ui.json").is_file():
                self._panels[instance_id] = _extrahera_panel()["engine"].PanelUI(_extrahera_panel()["spec"], _G03PanelHost(
                    lambda: self._panel_values(instance_id), lambda cap, args: self._panel_call(instance_id, cap, args), lambda: self.time_s))

    def panel(self, instance_id):
        return self._panels[instance_id]

    def _panel_values(self, instance_id):
        inst = self._instances[instance_id]
        out = {sid: copy.deepcopy(v) for sid, v in inst.state.items() if EXTRAHERA_STATE_SLOTS[sid]["visibility"] == "agent"}
        out.update({"host.powered": inst.state["power.enabled"], "host.power_epoch": inst.g03["power_epoch"], "host.clock": _g03_clock(self.time_s),
                    "host.run_summary": _exh_summary(inst.state, inst.sources)})
        return out

    def _panel_call(self, instance_id, capability_id, arguments):
        """A command of the panel firmware: it runs as the session whose gesture triggered it (ownership and guards as for its direct call);
        without a gesture (time-outs) as the panel's own principal."""
        with self._lock:
            return (self._panel_caller or self._panel_session)._invoke_checked(self._instances[instance_id], capability_id, arguments, None)

    @staticmethod
    def _stroke_m3(inst, volume_m3):
        """The CHEM-031 strokes (sample.aspirate / dispense, method.start) go through the device's pipette pump: stored calibration
        (coefficient, offset) and the evaluator pump error act on them as on the runs; exact with the defaults."""
        return _exh_pump_m3(inst.state, volume_m3) if getattr(inst, "g03", None) is not None else volume_m3

    # ---- time
    def _advance(self, inst, dt_s):
        g = getattr(inst, "g03", None)
        active = inst.state["operation.active_id"]
        if g is not None and active and inst.operations[active].get("gui_run"):
            self._exh_run_advance(inst, dt_s)
        else:
            super()._advance(inst, dt_s)
        if g is None:
            return
        s = inst.state
        if s["system.state"] == "starting" and g["boot_until"] is not None and self.time_s + dt_s >= g["boot_until"] - 1e-9:
            s["system.state"] = "ready"
            cal = s["pump.calibration"]
            self._exh_expiry(s, self.time_s + dt_s)
            st = s["settings.values"]
            if cal["expired"] and st["cal_notifications"] and st["cal_notify_startup"]:
                s["system.notice"] = "The pipette pump calibration has expired."
        if s["run.state"]["state"] == "paused" and s["manual.state"]["door"] == "open":
            s["run.state"]["door_open_s"] = s["run.state"].get("door_open_s", 0.0) + dt_s

    @staticmethod
    def _exh_expiry(state, now_s):
        cal, st = state["pump.calibration"], state["settings.values"]
        last = datetime.fromisoformat(cal["date"])
        cal["expired"] = (G03_DEVICE_EPOCH + _g03_timedelta(seconds=now_s) - last).days >= EXTRAHERA_CAL_INTERVALS[st["cal_interval"]]

    def _exh_utilities_ok(self, inst):
        s = inst.state
        return bool(s["power.enabled"] and s["utilities.compressed_air_ready"] and s["utilities.nitrogen_ready"] and s["utilities.vacuum_ready"]
                    and s["vacuum.source_instance_id"] is not None)

    def _exh_move(self, inst, source, dest, amount):
        if amount <= 0: return 0.0
        if f"fluid.{source}_m3" not in inst.state:
            have = inst.sources.get(source, 0.0)
            amount = min(amount, have)
            inst.sources[source] = have - amount
        else:
            have = inst.state[f"fluid.{source}_m3"]
            amount = min(amount, have)
            inst.state[f"fluid.{source}_m3"] = have - amount
        if f"fluid.{dest}_m3" not in inst.state:
            inst.sources[dest] = inst.sources.get(dest, 0.0) + amount
        else:
            inst.state[f"fluid.{dest}_m3"] += amount
        inst.state["fluid.sources_m3"] = math.fsum(inst.sources.values())
        return amount

    def _exh_run_advance(self, inst, dt_s):
        s, g = inst.state, inst.g03
        run = s["run.state"]
        op = inst.operations[s["operation.active_id"]]
        if not self._exh_utilities_ok(inst):
            self._exh_end(inst, op, "failed", error="System error: the gas or vacuum supply is not available.")
            return
        left = dt_s
        while left > 1e-12 and run["state"] == "running":
            tasks = g["tasks"]
            if g["task_index"] >= len(tasks):
                self._exh_end(inst, op, "completed")
                return
            task = tasks[g["task_index"]]
            if not task.get("started"):
                if task.get("sample_tips") and run["sample_tips_left"] < task["sample_tips"]:
                    tip = _exh_lib(s, "pipette_tips", _exh_method(s, run["method_id"]).get("sample_tip")) or {}
                    self._exh_pause(inst, "tips", f"Please load {EXTRAHERA_TIP_RACK} sample pipette tips of type {tip.get('name', '?')} into position 2 "
                                                  "on the working area.")
                    return
                task["started"] = True
                run["sample_tips_left"] -= task.get("sample_tips") or 0
                run["operation"] = task["label"]
                run["operation_index"] = run["operations"].index(task["label"]) if task["label"] in run["operations"] else 0
                run["action"] = task["text"]
                s["method.phase"] = _EXH_OP_PHASE.get(task["op"], s["method.phase"])
                if task["kind"] == "drain":
                    s["pressure.target_pa"] = min(task["pressure_pa"], 500000.0)
                    task["holdup_m3"] = s["fluid.extraction_m3"] if task["flows"] and task["dest"] else 0.0
                if task.get("dispose_tips"):
                    run["solvent_tips_disposed"] = run.get("solvent_tips_disposed", 0) + task["dispose_tips"]
                if task.get("lift_pct") is not None:                       # Filtration: the lifter moves the collection plate up (p.27, p.29)
                    s["manual.state"].update(carousel_lifted=True, lifter_height_pct=task["lift_pct"])
            d = min(left, task["duration_s"] - task["elapsed_s"])
            frac_end = 1.0 if task["duration_s"] <= 0 else (task["elapsed_s"] + d) / task["duration_s"]
            if task["kind"] == "liquid" and task["source"]:
                target = task["volume_m3"] * min(frac_end, 1.0)
                task["moved_m3"] += self._exh_move(inst, task["source"], task["dest"], target - task["moved_m3"])
            if task["kind"] == "drain" and task.get("holdup_m3"):
                target = task["holdup_m3"] * min(frac_end, 1.0)
                task["moved_m3"] += self._exh_move(inst, "extraction", task["dest"], target - task["moved_m3"])
            task["elapsed_s"] += d
            left -= d
            run["elapsed_s"] += d
            if task["elapsed_s"] >= task["duration_s"] - 1e-9:
                if task["kind"] == "drain":
                    s["pressure.target_pa"] = 0.0
                if task.get("lift_pct") is not None:
                    s["manual.state"].update(carousel_lifted=False, lifter_height_pct=None)
                g["task_index"] += 1
                run["estimated_left_s"] = (sum(t["duration_s"] for t in tasks[g["task_index"]:]) if run.get("show_estimate") else None)
                if task["kind"] == "pause":
                    self._exh_pause(inst, "scheduled", task["text"])
                elif run["abort_queued"]:
                    self._exh_abort_point(inst)
                elif run["pause_queued"]:
                    self._exh_pause(inst, "user", "The run is paused.")
                if g["task_index"] >= len(tasks) and run["state"] == "running":
                    self._exh_end(inst, op, "completed")
                    return
            elif run["estimated_left_s"] is not None:
                run["estimated_left_s"] = max(0.0, run["estimated_left_s"] - d)

    @staticmethod
    def _exh_alarm(state, color):
        state["alarm.state"] = {"color": color, "sounding": bool(state["settings.values"]["audible_alarm"])}

    def _exh_pause(self, inst, reason, text):
        run = inst.state["run.state"]
        run.update(state="paused", pause_queued=False, pause_reason=reason, pause_text=text)
        inst.state["pressure.target_pa"] = 0.0
        if reason in ("scheduled", "tips"):
            self._exh_alarm(inst.state, "blue")                             # non-user-initiated or scheduled pause (p.3, p.40)

    def _exh_abort_point(self, inst):
        run = inst.state["run.state"]
        run.update(state="aborting", abort_queued=False, pause_queued=False)
        inst.state["pressure.target_pa"] = 0.0

    def _exh_end(self, inst, op, status, error=None):
        s, g = inst.state, inst.g03
        run = s["run.state"]
        self._terminate(inst, op, {"completed": "completed", "failed": "failed", "aborted": "cancelled"}[status])
        s["pressure.target_pa"] = 0.0
        if status != "failed":
            s["manifold.z_m"] = EXTRAHERA_PARK_Z_M
        method = _exh_method(s, run["method_id"])
        if method is not None and status == "completed":
            method["run_count"] += 1
            method["last_duration_s"] = run["elapsed_s"]
        g["serial"] += 1
        rid = f"rep-{g['serial']:03d}"
        g.setdefault("report_owners", {})[rid] = op.get("principal_id")
        setup = s["run.setup"] or {}
        report = {"id": rid, "name": (setup.get("report") or {}).get("report_name") or run["method_name"], "method_name": run["method_name"],
                  "method_type": method["type"] if method else None, "run_date": _g03_clock(g["run_started_s"] or 0.0),
                  "status": {"completed": "Completed", "failed": "Failed", "aborted": "Aborted"}[status], "duration_s": run["elapsed_s"],
                  "wells": setup.get("wells"), "comment": (setup.get("report") or {}).get("comment", ""), "pause_comments": list(run.get("pause_comments", [])),
                  "abort_comment": run.get("comment", "") if status == "aborted" else "", "door_open_s": run.get("door_open_s", 0.0),
                  "expired_calibration": bool(setup.get("expired_calibration")), "error": error,
                  "sample_plate_id": (setup.get("report") or {}).get("sample_plate_id", ""), "media_lot": (setup.get("report") or {}).get("media_lot", ""),
                  "collection_plate_id": (setup.get("report") or {}).get("collection_plate_id", ""),
                  "solvent_tips_disposed": run.get("solvent_tips_disposed", 0)}
        s["reports.list"] = s["reports.list"] + [report]
        run.update(state={"completed": "completed", "failed": "failed", "aborted": "aborted"}[status], report_id=rid, error=error, action=None,
                   pause_queued=False, abort_queued=False, estimated_left_s=0.0 if status == "completed" else None)
        if status == "failed":
            self._exh_alarm(s, "red")                                       # system error (p.3, p.40)
        g["tasks"], g["task_index"] = [], 0


class ExtraheraSession(_g03_ExtraheraSessionBase):
    """CHEM-031 session plus the device commands of the touch-screen software and the panel.* commands."""

    @property
    def _host(self):
        return self._ExtraheraSession__host

    @property
    def _visibility(self):
        return self._host._principals[self._ExtraheraSession__principal_id][0]

    def describe(self, *, asset_type_id=None, instance_id=None):
        response = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if response.get("error") is None and "capabilities" in response:
            evaluator = self._visibility in ("debug", "evaluator")
            meta = export_extrahera_contract_metadata()["asset_types"][EXTRAHERA_ASSET_TYPE_ID]
            slots = response["state_slots"]
            for group in ("device_capabilities", "panel_capabilities"):
                for name, cap in meta[group].items():
                    if cap["visibility"] == "agent" or evaluator:
                        cap = copy.deepcopy(cap)
                        cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in slots]
                        cap["observes"] = [sid for sid in cap["observes"] if sid in slots]
                        response["capabilities"][name] = cap
            response["asset_version"] = EXTRAHERA_ASSET_VERSION
        return response

    def _invoke_checked(self, inst, cap, args, request_id):
        if cap in EXTRAHERA_DEVICE_ARGS or cap.startswith("panel."):
            return self._device_checked(inst, cap, args, request_id)
        before = inst.state["power.enabled"]
        if getattr(inst, "g03", None) is not None:
            if self._exh_guard(inst, cap, args):
                return self._error(inst.instance_id, "ACCESS_DENIED", request_id)
            if inst.state["run.state"]["state"] in ("running", "paused", "aborting") and cap not in ("power.set", "method.stop"):
                return self._error(inst.instance_id, "BUSY", request_id)
        active = inst.state["operation.active_id"]
        owner = inst.operations[active]["principal_id"] if active else None
        response = super()._invoke_checked(inst, cap, args, request_id)
        if response["status"] == "succeeded" and getattr(inst, "g03", None) is not None:
            live = self._host._instances[inst.instance_id]
            if cap == "power.set":
                self._exh_power(live, before, args["enabled"], owner)
            if cap == "method.stop" and live.state["run.state"]["state"] in ("running", "paused", "aborting"):
                self._exh_after_stop(live, owner=owner)
        return response

    def _exh_guard(self, inst, cap, args):
        """Ownership rules of agent sessions, the same for direct calls and panel gestures (a gesture acts as its session): while another
        principal's operation runs (a run started on the panel or by run.start / method.start / sample.*), it cannot be paused, resumed,
        aborted, restored or stopped and its (motorised) door cannot be opened; reports of another principal's runs cannot be deleted.  The
        mains switch is a hard switch (lead clarification 10-19): anyone may switch it off, the run is aborted as on power loss.  Evaluator
        sessions and the panel's own time-outs are not restricted."""
        me = self._ExtraheraSession__principal_id
        if self._visibility != "agent" or me == ExtraheraHost.PANEL_PRINCIPAL:
            return False
        active = inst.state["operation.active_id"]
        op = inst.operations.get(active) if active else None
        if op is not None and op["status"] == "running" and op["principal_id"] != me:
            if cap in ("run.control", "method.stop", "door.set"):                # the mains switch (power.set) is a hard switch: physical,
                return True                                                    # never guarded; the run is aborted as on power loss
        if cap == "reports.delete" and isinstance(args.get("report_ids"), list):
            owners = inst.g03.get("report_owners", {})
            return any(owners.get(rid) not in (None, me) for rid in args["report_ids"])
        return False

    def cancel(self, *, instance_id, operation_id, request_id):
        """CHEM-031 cancel (owner only); a cancelled run of the touch-screen software closes like method.stop (Aborted report)."""
        with self._host._lock:
            response = super().cancel(instance_id=instance_id, operation_id=operation_id, request_id=request_id)
            live = self._host._instances.get(instance_id) if isinstance(instance_id, str) else None
            op = live.operations.get(operation_id) if live is not None and isinstance(operation_id, str) else None
            if response.get("error") is None and op is not None and op.get("gui_run") and op["status"] == "cancelled" \
                    and getattr(live, "g03", None) is not None and live.state["run.state"]["state"] in ("running", "paused", "aborting"):
                self._exh_after_stop(live, owner=op["principal_id"])
            return response

    def _exh_power(self, live, before, enabled, owner=None):
        s, g = live.state, live.g03
        if enabled and not before:
            g["power_epoch"] += 1
            g["boot_until"] = self._host.time_s + EXTRAHERA_BOOT_S
            s["system.state"] = "starting"
        if not enabled:
            if s["run.state"]["state"] in ("running", "paused", "aborting"):
                self._exh_after_stop(live, "Aborted (power off)", owner=owner)
            g["power_epoch"] += 1 if before else 0
            g["boot_until"] = None
            s["system.state"] = "off"
            s["run.state"], s["run.setup"] = _exh_idle_run(), None
            s["alarm.state"] = {"color": None, "sounding": False}
            s["system.notice"] = None
        s["lights.state"] = self._lights(s)

    def _exh_after_stop(self, live, note="Aborted", owner=None):
        """A run cancelled by method.stop, cancel or power off: the report records it; the run screen closes."""
        s = live.state
        run = s["run.state"]
        live.g03["serial"] += 1
        rid = f"rep-{live.g03['serial']:03d}"
        live.g03.setdefault("report_owners", {})[rid] = owner
        if s["manual.state"].get("lifter_height_pct") is not None:
            s["manual.state"].update(carousel_lifted=False, lifter_height_pct=None)
        s["reports.list"] = s["reports.list"] + [{"id": rid, "name": run["method_name"], "method_name": run["method_name"], "method_type": None,
                                                  "run_date": _g03_clock(live.g03["run_started_s"] or 0.0), "status": "Aborted",
                                                  "duration_s": run["elapsed_s"], "wells": None, "comment": "", "pause_comments": [], "abort_comment": note,
                                                  "door_open_s": 0.0, "expired_calibration": False, "error": None, "sample_plate_id": "", "media_lot": "",
                                                  "collection_plate_id": "", "solvent_tips_disposed": run.get("solvent_tips_disposed", 0)}]
        s["run.state"] = dict(_exh_idle_run(), state="aborted", report_id=rid, method_id=run["method_id"], method_name=run["method_name"])
        s["alarm.state"] = {"color": None, "sounding": False}
        live.g03["tasks"], live.g03["task_index"] = [], 0

    @staticmethod
    def _lights(s):
        on = s["power.enabled"] and s["system.state"] in ("starting", "ready")
        return {"upper": bool(on and s["settings.values"]["lights_upper"]), "lower": bool(on and s["settings.values"]["lights_lower"])}

    def _device_checked(self, inst, cap, args, request_id):
        rid = request_id
        if cap.startswith("panel."):
            meta = _extrahera_panel()["caps"] if (EXTRAHERA_ASSET_DIR / "panel_ui.json").is_file() else {}
            if cap not in meta: return self._error(inst.instance_id, "UNKNOWN_CAPABILITY", rid)
            if meta[cap]["visibility"] != "agent" and self._visibility not in ("debug", "evaluator"): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            if _g03_check(meta[cap]["input_schema"], args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
            if inst.instance_id not in self._host._panels: return self._error(inst.instance_id, "UNKNOWN_CAPABILITY", rid)
            if self._ExtraheraSession__principal_id == ExtraheraHost.PANEL_PRINCIPAL: return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            previous, self._host._panel_caller = self._host._panel_caller, self   # the commands of this gesture act as this session
            try:
                result, code = g03_panel_gesture(self._host._panels[inst.instance_id], cap, args, instance_id=inst.instance_id, time_s=self._host.time_s,
                                                 capture_dir=self._host.panel_capture_dir)
            finally:
                self._host._panel_caller = previous
            if code: return self._error(inst.instance_id, code, rid)
            live = self._host._instances[inst.instance_id]
            return self._envelope(live.instance_id, rid, result=result)
        inputs, optional = EXTRAHERA_DEVICE_ARGS[cap][0], EXTRAHERA_DEVICE_ARGS[cap][1]
        if cap in EXTRAHERA_EVALUATOR_CAPS and self._visibility not in ("debug", "evaluator"): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
        if _g03_check(_g03_obj(inputs, optional), args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
        if getattr(inst, "g03", None) is not None and self._exh_guard(inst, cap, args): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
        candidate = copy.deepcopy(inst)
        try:
            result, operation_id, status = getattr(self, "_exh_" + cap.replace(".", "_"))(candidate, args)
        except _ExhRefused as exc:
            if exc.code in ("INVALID_ARGUMENT", "PRECONDITION_FAILED", "BUSY", "UNKNOWN_CAPABILITY", "ACCESS_DENIED"):
                response = self._error(inst.instance_id, exc.code, rid)
                if exc.detail:
                    response["error"]["message"] = exc.detail
                return response
            return self._envelope(inst.instance_id, rid, status="rejected", error={"code": exc.code, "message": EXTRAHERA_DEVICE_ERRORS[exc.code],
                                                                                   "retryable": False, "side_effects": "none", "known_progress": None})
        candidate.state["lights.state"] = self._lights(candidate.state)
        try:
            self._host._commit(inst, candidate)
        except Exception:
            return self._error(inst.instance_id, "DEVICE_ERROR", rid, "failed")
        return self._envelope(inst.instance_id, rid, status=status, result=result, operation_id=operation_id)

    # ---- device commands (each works on a candidate copy; a refusal raises _ExhRefused before anything is committed)
    @staticmethod
    def _ready(c, idle=True):
        s = c.state
        if not s["power.enabled"] or s["system.state"] != "ready": raise _ExhRefused("NOT_READY")
        if idle and s["run.state"]["state"] in ("running", "paused", "aborting"): raise _ExhRefused("BUSY")

    def _exh_settings_configure(self, c, args):
        self._ready(c)
        new = args["settings"]
        if "instrument_type" in new and c.state["run.setup"] is not None: raise _ExhRefused("BUSY")
        c.state["settings.values"].update(new)
        self._host._exh_expiry(c.state, self._host.time_s)
        return {"settings": copy.deepcopy(c.state["settings.values"])}, None, "succeeded"

    @staticmethod
    def _clean_entry(kind, entry, old=None):
        fields = {f["key"]: f for f in EXTRAHERA_LIBRARY_FIELDS[kind]}
        out = {k: (old or {}).get(k, f.get("default")) for k, f in fields.items()}
        for k, v in entry.items():
            if k not in fields or _g03_check(_exh_field_schema(fields[k]), v): raise _ExhRefused("INVALID_ARGUMENT")
            if fields[k]["type"] == "int" and type(v) is float and v.is_integer(): v = int(v)
            out[k] = v
        if not isinstance(out["name"], str) or not out["name"].strip(): raise _ExhRefused("INVALID_ARGUMENT")
        return out

    def _exh_library_save(self, c, args):
        self._ready(c)
        kind, eid = args["kind"], args["entry_id"]
        entries = c.state["library.entries"][kind]
        old = _exh_lib(c.state, kind, eid) if eid is not None else None
        if eid is not None and old is None: raise _ExhRefused("INVALID_ARGUMENT")
        if old is not None and old["predefined"]: raise _ExhRefused("PREDEFINED")
        entry = self._clean_entry(kind, args["entry"], old)
        if any(e["name"] == entry["name"] and e["id"] != eid for e in entries): raise _ExhRefused("NAME_EXISTS")
        if old is None:
            n = 1 + max([int(e["id"].split("-")[1]) for e in entries] or [0])
            eid = _exh_library_id(kind, n)
            entries.append(dict(entry, id=eid, predefined=False))
        else:
            old.update(entry)
        self._refresh_methods(c.state)
        return {"entry_id": eid}, None, "succeeded"

    def _exh_library_copy(self, c, args):
        self._ready(c)
        src = _exh_lib(c.state, args["kind"], args["entry_id"])
        if src is None: raise _ExhRefused("INVALID_ARGUMENT")
        names = {e["name"] for e in c.state["library.entries"][args["kind"]]}
        name, i = src["name"] + " copy", 2
        while name in names:
            name, i = f"{src['name']} copy {i}", i + 1
        values = {k: v for k, v in src.items() if k not in ("id", "predefined")}
        return self._exh_library_save(c, {"kind": args["kind"], "entry_id": None, "entry": dict(values, name=name[:40])})

    def _exh_library_delete(self, c, args):
        self._ready(c)
        kind, eid = args["kind"], args["entry_id"]
        entry = _exh_lib(c.state, kind, eid)
        if entry is None: raise _ExhRefused("INVALID_ARGUMENT")
        if entry["predefined"]: raise _ExhRefused("PREDEFINED")
        lib_keys = [f["key"] for f in EXTRAHERA_METHOD_FIELDS if f["type"] == "lib:" + kind]
        used = any(m.get(k) == eid for m in c.state["methods.list"] for k in lib_keys)
        if kind == "solvents" and (eid in c.state["solvent.locks"].values() or any(i["assigned"] == eid for i in c.state["inlets.state"].values())):
            used = True
        if used: raise _ExhRefused("IN_USE")
        c.state["library.entries"][kind] = [e for e in c.state["library.entries"][kind] if e["id"] != eid]
        return {"deleted": True}, None, "succeeded"

    def _exh_solvent_lock(self, c, args):
        self._ready(c)
        for b, sid in args["locks"].items():
            if sid is not None and _exh_lib(c.state, "solvents", sid) is None: raise _ExhRefused("INVALID_ARGUMENT")
            c.state["solvent.locks"][b] = sid
            if sid is not None and c.state["inlets.state"][b]["assigned"] != sid:
                c.state["inlets.state"][b]["assigned"] = sid
        return {"locks": copy.deepcopy(c.state["solvent.locks"])}, None, "succeeded"

    @staticmethod
    def _refresh_methods(state):
        for m in state["methods.list"]:
            m["missing"] = _exh_missing(m)
            m["complete"] = not m["missing"]

    def _exh_methods_save(self, c, args):
        self._ready(c)
        mid, values = args["method_id"], args["values"]
        old = _exh_method(c.state, mid) if mid is not None else None
        if mid is not None and old is None: raise _ExhRefused("INVALID_ARGUMENT")
        if old is not None and old["folder"] != "user": raise _ExhRefused("PREDEFINED")
        if old is None and args["method_type"] is None: raise _ExhRefused("INVALID_ARGUMENT")
        for k, v in values.items():
            f = _EXH_MF.get(k)
            if f is None or _g03_check(_exh_field_schema(f), v): raise _ExhRefused("INVALID_ARGUMENT")
            if f["type"].startswith("lib:") and v is not None and _exh_lib(c.state, f["type"][4:], v) is None: raise _ExhRefused("INVALID_ARGUMENT")
        values = {k: (int(v) if _EXH_MF[k]["type"] == "int" and type(v) is float and v.is_integer() else v) for k, v in values.items()}
        if old is None:
            n = 1 + max([int(m["id"].split("-")[1]) for m in c.state["methods.list"] if m["folder"] == "user"] or [0])
            mid = f"usr-{n:03d}"
            mode = "Solvent First" if c.state["settings.values"]["ppt_solvent_first"] else "Sample First"
            method = _exh_new_method(mid, args["method_type"], mode, **values)
            c.state["methods.list"].append(method)
        else:
            old.update(values)
            method = old
        if not (method.get("name") or "").strip(): method["name"] = method.get("name") or ""
        self._refresh_methods(c.state)
        return {"method_id": mid, "complete": method["complete"]}, None, "succeeded"

    def _exh_methods_copy(self, c, args):
        self._ready(c)
        src = _exh_method(c.state, args["method_id"])
        if src is None: raise _ExhRefused("INVALID_ARGUMENT")
        values = {k: v for k, v in src.items() if k in _EXH_MF}
        values["name"] = ((src.get("name") or "") + " copy")[:60]
        r, _, _ = self._exh_methods_save(c, {"method_id": None, "method_type": src["type"], "values": values})
        _exh_method(c.state, r["method_id"])["ppt_mode"] = src["ppt_mode"]
        self._refresh_methods(c.state)
        return r, None, "succeeded"

    def _exh_methods_delete(self, c, args):
        self._ready(c)
        ids = set(args["method_ids"])
        for mid in ids:
            m = _exh_method(c.state, mid)
            if m is None: raise _ExhRefused("INVALID_ARGUMENT")
            if m["folder"] != "user": raise _ExhRefused("PREDEFINED")
        c.state["methods.list"] = [m for m in c.state["methods.list"] if m["id"] not in ids]
        return {"deleted": len(ids)}, None, "succeeded"

    def _exh_run_prepare(self, c, args):
        self._ready(c)
        s = c.state
        m = _exh_method(s, args["method_id"])
        if m is None: raise _ExhRefused("INVALID_ARGUMENT")
        if not m["complete"]: raise _ExhRefused("INCOMPLETE")
        media = _exh_lib(s, "extraction_media", m["extraction_media"])
        if media is None or media["format"] != s["settings.values"]["instrument_type"]: raise _ExhRefused("WRONG_FORMAT")
        s["run.setup"] = {"method_id": m["id"], "individual": args["individual"], "extraction": None, "solvent_tips": None, "sample_tips": None,
                          "position6": {"size": None, "reservoirs": [None] * 5}, "report": {"report_name": "", "comment": "", "sample_plate_id": "",
                                                                                         "media_lot": "", "collection_plate_id": ""},
                          "saved": {"extraction": False, "solvent_tips": False, "sample_tips": False, "bottles": False, "position6": True, "report": True},
                          "wells": 0, "expired_calibration": False,
                          "geometry": dict(zip(("columns", "rows", "channels", "max_individual"), _exh_geometry(s, m)))}
        s["run.state"] = dict(_exh_idle_run(), state="prepare", method_id=m["id"], method_name=m["name"], individual=args["individual"])
        return {"state": "prepare"}, None, "succeeded"

    def _exh_run_setup(self, c, args):
        self._ready(c)
        s = c.state
        setup = s["run.setup"]
        if setup is None or s["run.state"]["state"] != "prepare": raise _ExhRefused("NOT_RUNNING")
        m = _exh_method(s, setup["method_id"])
        cols, rows, channels, max_ind = _exh_geometry(s, m)
        sec, v = args["section"], args["values"]
        if sec == "extraction":
            if setup["individual"]:
                n = v.get("n_samples")
                if type(n) is not int or not 1 <= n <= max_ind: raise _ExhRefused("INVALID_ARGUMENT")
                setup["extraction"] = {"use_all_columns": False, "columns": [1], "n_samples": n}
            else:
                use_all, columns = v.get("use_all_columns"), v.get("columns", [])
                if type(use_all) is not bool or not isinstance(columns, list) or any(type(x) is not int or not 1 <= x <= cols for x in columns) \
                        or (not use_all and not columns): raise _ExhRefused("INVALID_ARGUMENT")
                setup["extraction"] = {"use_all_columns": use_all, "columns": sorted(set(columns)) if not use_all else list(range(1, cols + 1)), "n_samples": None}
            setup["wells"] = _exh_wells(setup, (cols, rows, channels, max_ind))
        elif sec in ("solvent_tips", "sample_tips"):
            tip = v.get("tip_type")
            if _exh_lib(s, "pipette_tips", tip) is None: raise _ExhRefused("INVALID_ARGUMENT")
            setup[sec] = {"tip_type": tip}
        elif sec == "bottles":
            for b, sid in v.items():
                if b not in EXTRAHERA_BOTTLES or (sid is not None and _exh_lib(s, "solvents", sid) is None): raise _ExhRefused("INVALID_ARGUMENT")
                lock = s["solvent.locks"][b]
                if lock is not None and sid != lock: raise _ExhRefused("LOCKED_SOLVENT")
                s["inlets.state"][b]["assigned"] = sid
        elif sec == "position6":
            size, res = v.get("size"), v.get("reservoirs", setup["position6"]["reservoirs"])
            if size not in (None, 25, 100) or not isinstance(res, list) or len(res) != 5 or \
                    any(x is not None and _exh_lib(s, "solvents", x) is None for x in res): raise _ExhRefused("INVALID_ARGUMENT")
            setup["position6"] = {"size": size, "reservoirs": list(res) if size else [None] * 5}
        elif sec == "report":
            rep = dict(setup["report"])
            for k, x in v.items():
                if k not in rep or not isinstance(x, str) or len(x) > 120: raise _ExhRefused("INVALID_ARGUMENT")
                rep[k] = x
            setup["report"] = rep
        setup["saved"][sec] = True
        return {"section": sec}, None, "succeeded"

    def _exh_run_prime(self, c, args):
        self._ready(c)
        s = c.state
        inlet = s["inlets.state"][args["inlet"]]
        if inlet["assigned"] is None:   # 1.3.1: a state refusal (was INVALID_ARGUMENT) that names the cause
            raise _ExhRefused("PRECONDITION_FAILED", "No solvent bottle is assigned to inlet %s (Prepare Run: run.setup section 'bottles')." % args["inlet"])
        if s["manual.state"]["door"] == "open": raise _ExhRefused("DOOR_OPEN")
        if c.sources is None or c.sources.get(args["inlet"], 0.0) + 1e-15 < args["volume_m3"]: raise _ExhRefused("NOT_ENOUGH_SOLVENT")
        if inlet["line"] != inlet["assigned"]:
            inlet["primed_m3"] = inlet.get("primed_m3", 0.0) + args["volume_m3"]
            if inlet["primed_m3"] >= EXTRAHERA_PRIME_FULL_M3 - 1e-12:
                inlet["line"], inlet["primed_m3"] = inlet["assigned"], 0.0
        return {"primed_m3": inlet["primed_m3"]}, None, "succeeded"

    def _exh_inlets_flush(self, c, args):
        self._ready(c)
        inlet = c.state["inlets.state"][args["inlet"]]
        if c.state["manual.state"]["door"] == "open": raise _ExhRefused("DOOR_OPEN")
        if args["volume_m3"] >= EXTRAHERA_FLUSH_EMPTY_M3 - 1e-12:
            inlet["line"], inlet["primed_m3"] = None, 0.0
        return {"line": inlet["line"]}, None, "succeeded"

    def _exh_run_start(self, c, args):
        self._ready(c)
        s, host = c.state, self._host
        setup = s["run.setup"]
        if setup is None or s["run.state"]["state"] != "prepare": raise _ExhRefused("NOT_RUNNING")
        if s["manual.state"]["door"] == "open": raise _ExhRefused("DOOR_OPEN")
        if not all(setup["saved"].values()): raise _ExhRefused("SETUP_INCOMPLETE")
        m = _exh_method(s, setup["method_id"])
        needs_samples = m["type"] != "Filtration"
        if needs_samples and setup["sample_tips"]["tip_type"] != m["sample_tip"]: raise _ExhRefused("WRONG_TIP")
        if c.sources is None: raise _ExhRefused("NO_LIQUID")
        tasks, operations, wells, info = _exh_compile(s, setup, m)
        need = {}
        for sid, ul in _exh_consumption(s, setup, m, info).items():
            src = _exh_source_of(s, setup, sid)
            if src is None: raise _ExhRefused("SETUP_INCOMPLETE")
            if src in EXTRAHERA_BOTTLES and s["inlets.state"][src]["line"] != sid: raise _ExhRefused("NOT_PRIMED")
            need[src] = need.get(src, 0.0) + ul * 1e-9
        for src, amount in need.items():
            if c.sources.get(src, 0.0) + 1e-15 < amount + EXTRAHERA_DEAD_VOLUME_M3: raise _ExhRefused("NOT_ENOUGH_SOLVENT")
        sample = _exh_sample_need(m)
        if sample and sample[0] > sample[1] + 1e-9: raise _ExhRefused("NOT_ENOUGH_SAMPLE")
        if not host._exh_utilities_ok(c): raise _ExhRefused("UTILITIES")
        host._exh_expiry(s, host.time_s)
        cols, rows, channels, _ = _exh_geometry(s, m)
        media = _exh_lib(s, "extraction_media", m["extraction_media"])
        if media["format"] == "24 Positions, Column Rack":
            fmt, vol = "cartridges24", (1e-6 if media["capacity"] <= 1000 else 3e-6 if media["capacity"] <= 3000 else 6e-6)
        else:
            fmt, vol = ("plate48" if media["format"].startswith("48") else "plate96"), None
        positions = sorted({t["source"] for t in tasks if t["kind"] == "liquid" and t["source"]})
        collect = next((m.get(k) for k in ("elution1_collect", "collection_collect") if _exh_relevant(_EXH_MF[k], m, m["type"])), "A")
        s.update({"deck.format": fmt, "deck.cartridge_volume_m3": vol, "deck.collection_plate": (collect or "A")[0],
                  "deck.solvent_positions": positions or ["sample"], "manifold.z_m": 0.0})
        c.g03["serial"] += 1
        c.operation_serial += 1
        operation_id = f"{c.instance_id}:extrahera:{c.operation_serial}"
        c.operations[operation_id] = {"operation_id": operation_id, "capability_id": "method.start", "principal_id": self._ExtraheraSession__principal_id,
                                      "arguments": {}, "status": "running", "subvolume_m3": 0.0, "step_index": 0, "repetition": 0, "subphase": "aspirate",
                                      "gui_run": True}
        s["operation.active_id"] = operation_id
        s["method.status"], s["method.phase"], s["method.completed_strokes"] = "running", "idle", 0
        st = s["settings.values"]
        setup["expired_calibration"] = s["pump.calibration"]["expired"]
        run = dict(_exh_idle_run(), state="running", method_id=m["id"], method_name=m["name"], individual=setup["individual"], operations=operations,
                   operation_index=0, operation="Preparations", action="Preparing the run", show_estimate=m["run_count"] > 0, pause_comments=[])
        run["estimated_left_s"] = sum(t["duration_s"] for t in tasks) if run["show_estimate"] else None
        s["run.state"] = run
        c.g03.update(tasks=tasks, task_index=0, run_started_s=host.time_s)
        s["alarm.state"] = {"color": None, "sounding": False}
        del st
        return {"control_status": "started"}, operation_id, "accepted"

    def _exh_run_control(self, c, args):
        s, host = c.state, self._host
        run, action = s["run.state"], args["action"]
        if not s["power.enabled"]: raise _ExhRefused("NOT_READY")
        comment = args.get("comment")
        state = run["state"]
        if action == "pause":
            if state != "running" or run["abort_queued"]: raise _ExhRefused("NOT_RUNNING")
            run["pause_queued"] = True
        elif action == "cancel_pause":
            if state != "running" or not run["pause_queued"]: raise _ExhRefused("NOT_RUNNING")
            run["pause_queued"] = False
        elif action == "resume":
            if state != "paused": raise _ExhRefused("NOT_RUNNING")
            if s["manual.state"]["door"] == "open": raise _ExhRefused("DOOR_OPEN")
            if not host._exh_utilities_ok(c): raise _ExhRefused("UTILITIES")
            if comment: run["pause_comments"] = run.get("pause_comments", []) + [comment]
            if run["pause_reason"] == "tips": run["sample_tips_left"] = EXTRAHERA_TIP_RACK
            run.update(state="running", pause_reason=None, pause_text=None)
            s["alarm.state"] = {"color": None, "sounding": False}
        elif action == "abort":
            if state == "running": run["abort_queued"] = True
            elif state == "paused":
                if comment: run["pause_comments"] = run.get("pause_comments", []) + [comment]
                host._exh_abort_point(c)
            else: raise _ExhRefused("NOT_RUNNING")
            s["alarm.state"] = {"color": None, "sounding": False}
        elif action == "restore":
            if state != "aborting": raise _ExhRefused("NOT_RUNNING")
            if s["manual.state"]["door"] == "open": raise _ExhRefused("DOOR_OPEN")
            run["comment"] = comment or ""
            host._exh_end(c, c.operations[s["operation.active_id"]], "aborted")
            self._home(c)
        elif action == "close":
            if state not in ("completed", "aborted", "failed"): raise _ExhRefused("NOT_RUNNING")
            s["run.state"], s["run.setup"] = _exh_idle_run(), None
            s["alarm.state"] = {"color": None, "sounding": False}
        elif action == "exit_prepare":
            if state != "prepare": raise _ExhRefused("NOT_RUNNING")
            s["run.state"], s["run.setup"] = _exh_idle_run(), None
        return {"state": s["run.state"]["state"]}, None, "succeeded"

    def _exh_alarm_silence(self, c, args):
        c.state["alarm.state"]["sounding"] = False
        return {"sounding": False}, None, "succeeded"

    def _exh_system_notice_ack(self, c, args):
        c.state["system.notice"] = None
        return {"notice": None}, None, "succeeded"

    @staticmethod
    def _home(c):
        """Pipette head, pressure unit, carousel and lifter to their home positions (p.40 Restore, p.46 Re-Initialize)."""
        s = c.state
        s["pressure.target_pa"] = 0.0
        s["manifold.z_m"] = EXTRAHERA_PARK_Z_M
        for sid, (_, _, default) in EXTRAHERA_JOINTS.items():
            if sid.startswith("gantry."): s[sid] = default
        s["manual.state"].update(pipette_head="right", carousel_position="D", carousel_lifted=False, waste_collector="lowered", plate_dry=False,
                                 lifter_height_pct=None)

    def _exh_door_set(self, c, args):
        self._ready(c, idle=False)
        s = c.state
        state = s["run.state"]["state"]
        if state == "running": raise _ExhRefused("BUSY")
        if args["open"]:
            if s["pressure.target_pa"] > 0: raise _ExhRefused("INTERLOCK")
            s["manual.state"]["door"] = "open"
        else:
            s["manual.state"]["door"] = "closed"
            if state not in ("paused", "aborting"):                         # after closing: carousel homed, pressure head in, pipette head right, collector down
                s["pressure.target_pa"] = 0.0
                s["manifold.z_m"] = EXTRAHERA_PARK_Z_M
                s["gantry.x_m"] = EXTRAHERA_JOINTS["gantry.x_m"][2]
                s["manual.state"].update(pipette_head="right", carousel_position="D", carousel_lifted=False, waste_collector="lowered", plate_dry=False)
        return {"door": s["manual.state"]["door"]}, None, "succeeded"

    def _exh_manual_control(self, c, args):
        self._ready(c)
        s = c.state
        ms, action = s["manual.state"], args["action"]
        unit_out = s["manifold.z_m"] == 0
        if ms["door"] == "open" and not action.startswith("waste_valve"): raise _ExhRefused("DOOR_OPEN")
        if action in ("carousel_collect", "carousel_front") and args.get("position") is None: raise _ExhRefused("INVALID_ARGUMENT")
        if action == "waste_collector_raise":
            if not unit_out: raise _ExhRefused("INTERLOCK")
            ms["waste_collector"] = "raised"
        elif action == "waste_collector_lower": ms["waste_collector"] = "lowered"
        elif action in ("pipette_head_left", "pipette_head_right"):
            if unit_out: raise _ExhRefused("INTERLOCK")
            ms["pipette_head"] = action.rsplit("_", 1)[1]
            s["gantry.x_m"] = 0.0 if action.endswith("left") else EXTRAHERA_JOINTS["gantry.x_m"][2]
        elif action in ("pressure_unit_in", "pressure_unit_out"):
            if s["pressure.target_pa"] != 0: raise _ExhRefused("INTERLOCK")
            s["manifold.z_m"] = 0.0 if action.endswith("out") else EXTRAHERA_PARK_Z_M
            if action.endswith("in"): ms.update(carousel_lifted=False)
        elif action == "carousel_collect":
            if not unit_out: raise _ExhRefused("INTERLOCK")
            ms.update(carousel_position=args["position"], carousel_lifted=True)
        elif action == "carousel_front": ms.update(carousel_position=args["position"], carousel_lifted=False)
        elif action == "waste_valve_open": ms["waste_valve"] = "open"
        elif action == "waste_valve_close": ms["waste_valve"] = "closed"
        elif action == "plate_dry_start":
            if not unit_out: raise _ExhRefused("INTERLOCK")
            if not (s["utilities.compressed_air_ready"] and s["utilities.nitrogen_ready"]): raise _ExhRefused("UTILITIES")
            s["pressure.target_pa"], ms["plate_dry"] = EXTRAHERA_PLATE_DRY_PA, True
        elif action == "plate_dry_stop":
            if ms["plate_dry"]: s["pressure.target_pa"] = 0.0
            ms["plate_dry"] = False
        return {"action": action}, None, "succeeded"

    def _exh_pump_calibration(self, c, args):
        self._ready(c)
        cal, action = c.state["pump.calibration"], args["action"]
        if action == "calculate":
            keys = ("desired_1_ul", "obtained_1_ul", "desired_2_ul", "obtained_2_ul")
            if any(args.get(k) is None for k in keys) or args["desired_1_ul"] == args["desired_2_ul"]: raise _ExhRefused("INVALID_ARGUMENT")
            d1, o1, d2, o2 = (float(args[k]) for k in keys)
            k1, k2 = d1 * cal["coefficient"] + cal["offset_ul"], d2 * cal["coefficient"] + cal["offset_ul"]   # commands used during the calibration
            a = (o2 - o1) / (k2 - k1)
            if a <= 0: raise _ExhRefused("INVALID_ARGUMENT")
            b = o1 - a * k1
            cal["pending"] = {"coefficient": round(1.0 / a, 6), "offset_ul": round(-b / a, 3), "applied": False, "inputs": [d1, o1, d2, o2]}
        elif action == "apply":
            if cal["pending"] is None: raise _ExhRefused("NO_RESULT")
            cal["pending"]["applied"] = True
        elif action == "save":
            if cal["pending"] is None or not cal["pending"]["applied"]: raise _ExhRefused("NO_RESULT")
            cal["history"] = (cal["history"] + [{"date": cal["date"], "coefficient": cal["coefficient"], "offset_ul": cal["offset_ul"]}])[-20:]
            cal.update(coefficient=cal["pending"]["coefficient"], offset_ul=cal["pending"]["offset_ul"], date=_g03_clock(self._host.time_s), pending=None)
            self._host._exh_expiry(c.state, self._host.time_s)
        elif action == "cancel": cal["pending"] = None
        elif action == "restore":
            i = args.get("history_index")
            if i is None or i >= len(cal["history"]): raise _ExhRefused("INVALID_ARGUMENT")
            old = cal["history"][i]
            cal["pending"] = {"coefficient": old["coefficient"], "offset_ul": old["offset_ul"], "applied": True, "inputs": None}
        return {"calibration": copy.deepcopy(cal)}, None, "succeeded"

    def _exh_pump_configure_error(self, c, args):
        c.state["pump.error"] = {"gain": float(args["gain"]), "offset_m3": float(args["offset_m3"])}
        return {"error": copy.deepcopy(c.state["pump.error"])}, None, "succeeded"

    def _exh_system_reinitialize(self, c, args):
        self._ready(c)
        if c.state["manual.state"]["door"] == "open": raise _ExhRefused("DOOR_OPEN")
        self._home(c)
        return {"state": "initialized"}, None, "succeeded"

    def _exh_reports_delete(self, c, args):
        self._ready(c)
        ids = set(args["report_ids"])
        if not ids <= {r["id"] for r in c.state["reports.list"]}: raise _ExhRefused("INVALID_ARGUMENT")
        c.state["reports.list"] = [r for r in c.state["reports.list"] if r["id"] not in ids]
        return {"deleted": len(ids)}, None, "succeeded"

    def _exh_system_shutdown(self, c, args):
        self._ready(c)
        c.state["system.state"] = "shut_down"
        c.state["pressure.target_pa"] = 0.0
        c.state["run.setup"], c.state["run.state"] = None, _exh_idle_run()
        return {"state": "shut_down"}, None, "succeeded"


_g03_extrahera_export_base = export_extrahera_contract_metadata


def export_extrahera_contract_metadata():
    """CHEM-031 metadata plus the touch-screen software: device-layer slots (in state_slots), device_capabilities, panel_capabilities (panel.*,
    when the panel spec exists), parameters and limitations.  The CHEM-031 'capabilities' keep their schemas."""
    meta = _g03_extrahera_export_base()
    t = meta["asset_types"][EXTRAHERA_ASSET_TYPE_ID]
    t["asset_version"] = EXTRAHERA_ASSET_VERSION
    for sid, (_, _, meaning) in EXTRAHERA_DEVICE_SLOTS.items():
        t["state_slots"][sid]["meaning_zh"] = meaning
    t["state_slots"]["pump.error"]["meaning_zh"] = "评测者：移液泵体积误差（相对增益、偏移m^3），默认0"
    dev = {}
    for name, (inputs, optional, outputs, example, desc, effects, components, kind) in EXTRAHERA_DEVICE_ARGS.items():
        dev[name] = g03_device_capability(name, kind, desc, inputs, outputs, example, components, effects, optional=optional, binding=_EXH_BINDING,
                                          visibility="evaluator" if name in EXTRAHERA_EVALUATOR_CAPS else "agent",
                                          completion="accepted只确认开始；运行由宿主step推进，状态见run.state与method.status" if name == "run.start" else
                                          "同步提交；被拒时不改变状态")
    t["device_capabilities"] = dev
    t["panel_capabilities"] = {}
    if (EXTRAHERA_ASSET_DIR / "panel_ui.json").is_file():
        spec = _extrahera_panel()["spec"]
        called = _g03_engine().spec_capabilities(spec)
        effects = list(dict.fromkeys(e["state_id"] for c in called for e in (t["capabilities"].get(c) or dev.get(c) or {}).get("state_effects", [])
                                     if e["timing"] == "immediate"))
        t["panel_capabilities"] = g03_panel_capabilities(spec, _EXH_BINDING, effects)
    t["parameters"].update({
        "boot_time_s": {"value": EXTRAHERA_BOOT_S, "unit": "s", "evidence": "engineering_assumption"},
        "prime_volume_to_fill_line_m3": {"value": EXTRAHERA_PRIME_FULL_M3, "unit": "m^3", "evidence": "manual", "reference_conditions": "p.35: about 2-3 x 15 mL"},
        "flush_volume_to_empty_line_m3": {"value": EXTRAHERA_FLUSH_EMPTY_M3, "unit": "m^3", "evidence": "manual", "reference_conditions": "p.48"},
        "reservoir_dead_volume_m3": {"value": EXTRAHERA_DEAD_VOLUME_M3, "unit": "m^3", "evidence": "manual", "reference_conditions": "p.35-36"},
        "sample_tips_per_rack": {"value": EXTRAHERA_TIP_RACK, "unit": "1", "evidence": "manual", "reference_conditions": "p.40 Fig. 78 text"},
        "pipetting_cycle_overhead_s": {"value": EXTRAHERA_CYCLE_OVERHEAD_S, "unit": "s", "evidence": "engineering_assumption"},
        "plate_dry_pressure_pa": {"value": EXTRAHERA_PLATE_DRY_PA, "unit": "Pa", "evidence": "manual", "reference_conditions": "p.2, p.27: 5 bar"},
        "air_push_pressure_pa": {"value": EXTRAHERA_AIR_PUSH_PA, "unit": "Pa", "evidence": "engineering_assumption"},
        "device_clock_epoch": {"value": G03_DEVICE_EPOCH.isoformat(), "unit": "1", "evidence": "engineering_assumption"},
    })
    t["limitations"] = list(t["limitations"]) + EXTRAHERA_G03_LIMITATIONS
    return meta


EXTRAHERA_G03_LIMITATIONS = [
    "Refusals of another agent's run (pause / resume / abort / stop, opening the motorised door, deleting its reports) show the "
    "dialog 'This run was started by another user. It can be viewed, not changed.' (text ours; the real software has no caller concept).",
    "Touch-screen software layer (manual P/N 414158, non-GLP software): Data Administration libraries, method editor, Prepare Run, runs, Maintenance, "
    "Manual Control, pump calibration, flush, re-initialize, reports and shut down follow the manual as a minimal device model, not Biotage firmware. "
    "The optional GLP software (users, login, audit trails, clog detection, network, e-mail, backups, sample information) is not installed.",
    "A run compiles the method onto the lumped liquid balance: dispensing (source -> extraction or sample plate) over the pipetting time from the "
    "library flow rates, channels and a 10 s cycle overhead (assumption); positive pressure drains the extraction holdup to waste (D) or collection "
    "(A-C) over the pressure time (no flow at 0 bar; SLE elution drains during its wait time); no per-well liquid, sorption or recovery. Solvent "
    "bottles S1-S5, position 6 reservoirs P6-1..P6-5 and the sample plate 'sample' are host-initialized liquid inventories; priming moves no liquid.",
    "Pipette pump error (relative gain, offset) is set only by the evaluator (pump.configure_error) and is 0 by default; the calibration coefficient "
    "and offset entered on the device and the error act on every pipetting path: panel runs, run.start, and the CHEM-031 strokes sample.aspirate / "
    "sample.dispense / method.start (one pump model; with the defaults the requested volumes are kept exactly).",
    "Liquid classes (p.21-22): the lower and upper air gaps (aspirated and dispensed at their flow rates), the upper-air-gap dispense pause and the "
    "post-dispense air aspiration add their time to every pipetting cycle; air takes no tip volume in the lumped balance. Tip conditioning: a solvent "
    "with 'Requires tip conditioning?' conditions its solvent tips once per tip set, before each step (a repeat is the same step) or before every "
    "aspiration, n times with the given % of the solvent tip capacity; the method's 'Tip conditioning?' (SPE) conditions each sample tip before the "
    "load with the conditioning solvent's settings. Conditioning liquid goes to waste and counts in the solvent consumption.",
    "Solvent tips: one tip set per solvent, returned to the rack and reused (p.33); 'Dispose solvent tips after each step?' and (PPT/PLD solvent first) "
    "'Dispose solvent tips?' discard the set after the step / solvent load (10 s, assumption) so the next use takes a new set: the Tips needed count "
    "and run.state / report solvent_tips_disposed follow. Starting sample volume (when > 0) must cover the sample load (plus the pretreatment solvent "
    "dispensed into the plate), else the run is refused (NOT_ENOUGH_SAMPLE; the check and its message are assumptions). Filtration 'Collection plate "
    "height' is shown as manual.state.lifter_height_pct while the collection drains; it does not change the lumped liquid balance.",
    "Ownership (agent sessions; panel gestures act as their session): a run belongs to the principal that started it (panel Run, run.start, "
    "method.start); only it can poll or cancel the operation, and another agent cannot pause / resume / abort / restore it (run.control), stop it "
    "(method.stop), open its door or delete its reports. The mains switch is physical: anyone may switch it off and the run is aborted as on a "
    "power loss. Evaluator sessions are not restricted.",
    "USB memory devices and the barcode scanner are absent: Export, Export PDF, Import, Export Logs and report export show the device's no-USB message.",
    "Method parameter ranges, step counts (at most 3 steps and 3 pressure-gradient steps), predefined entry values and message texts the manual does not "
    "print are assumptions recorded in panel_ui.json.",
]
# <<< G03 CHEM-031 Extrahera touch screen


# >>> G03 CHEM-025 Lyovapor touch screen
# Device layer of the Lyovapor L-250 Pro control panel (operation manual references/manuals/buchi_lyovapor_l250_pro/
# operating_manual__lyovapor_l250_om_11594477.pdf, 'p.N' = PDF page): instrument status (Standby, Conditioning, Unload / Load, Manual
# Drying, Primary / Secondary drying, Hold, Aerating, Shutting down, Defrosting, Vacuum Test, Leak Test), manual freeze-drying with its
# parameters (changeable while running), methods (up to 35, up to 30 steps, phases, temperature difference end point) run on the CHEM-025
# cycle, sample protection (P / T, UNPROT), skip, favourites, messages and journal, live graph, settings, vacuum and leak tests, optional
# hot-gas defrost, shut down, and the front panel (touch screen, navigation control, master switch).  The CHEM-025 commands keep their meaning.
LYOVAPOR_ASSET_VERSION = "1.3.0"
LYOVAPOR_ASSET_DIR = G03_ASSET_ROOT / "buchi_lyovapor_l250"
LYOVAPOR_MANUAL = "references/manuals/buchi_lyovapor_l250_pro/operating_manual__lyovapor_l250_om_11594477.pdf"
LYOVAPOR_CONDITIONING_S = 30 * 60.0     # p.40: preparing the instrument, approx. 30 min
LYOVAPOR_SHUTDOWN_S = 50 * 60.0         # p.54: shutting down, 50 min
LYOVAPOR_AERATE_S = 60.0                # venting time (not given: assumption)
LYOVAPOR_DEFROST_S = 60 * 60.0          # p.56: defrosting for 60 min, +/- 30 min, at most 300 min in total
LYOVAPOR_DEFROST_MAX_S = 300 * 60.0
LYOVAPOR_MANUAL_PHASE_S = 604800.0      # manual drying runs as one primary-drying phase of the CHEM-025 cycle (its 7-day maximum)
LYOVAPOR_MAX_METHODS, LYOVAPOR_MAX_STEPS = 35, 30   # p.43, p.45
LYOVAPOR_GRAPH_S, LYOVAPOR_GRAPH_N = 60.0, 1440     # live graph: one sample per minute, last 24 h (assumption)
LYOVAPOR_LEAK_LIMIT = 10.10             # mbar*L/h (p.80)
LYOVAPOR_LEAK_TEST_S = 45 * 60.0        # p.80: 45 min
LYOVAPOR_STATUSES = ("Off", "Standby", "Conditioning", "Unload / Load", "Manual Drying", "Primary drying", "Secondary drying", "Hold", "Aerating",
                     "Shutting down", "Defrosting", "Vacuum Test", "Leak Test")
LYOVAPOR_ACTIONS = ["None", "Sample protection", "Message"]
LYOVAPOR_SETTINGS = [
    dict(key="pressure_sensor", label="Pressure sensor", type="enum", options=["Standard", "Inficon Porter CDG020 D"], default="Standard", page="p.49"),
    dict(key="dhcp", label="DHCP", type="bool", default=False, page="p.31"),
    dict(key="cloud", label="BUCHI Cloud", type="bool", default=False, page="p.31"),
]
LYOVAPOR_FAVOURITE_TARGETS = {"start": "Start", "methods": "Method", "process_settings": "Process settings", "settings": "Settings",
                              "endpoint": "End point determination", "servicing": "Servicing", "vacuum_test": "Vacuum test", "leak_test": "Leak test",
                              "system_info": "System information", "messages": "Messages", "graph": "Graph"}
_LYO_STEP_DEFAULT = {"phase": "Primary drying", "term_min": 60.0, "shelf_temperature_k": 263.15, "pressure_zone": "Regulated", "pressure_pa": 30.0,
                     "pressure_limit_pa": 10.0, "pressure_duration_s": 300.0}
_LYO_TDT = {"enabled": False, "start_time_min": 60.0, "limit_k": 2.0, "term_min": 30.0, "continue": True, "message": True}


def _lyo_new_method(mid, name):
    return {"id": mid, "name": name, "gas_type": "", "collapse_temperature_k": None, "shelf_load_temperature_k": None,
            "steps": [dict(_LYO_STEP_DEFAULT), dict(_LYO_STEP_DEFAULT, phase="Secondary drying", shelf_temperature_k=303.15, pressure_pa=10.0)],
            "phases": {"primary": {"pressure_action": "Sample protection", "temp_action": "Sample protection", "safety_temperature_k": 2.0,
                                   "safety_time_min": 0.0, "tdt": dict(_LYO_TDT)},
                       "secondary": {"pressure_action": "None", "temp_action": "None", "safety_temperature_k": 2.0, "tdt": dict(_LYO_TDT)},
                       "stoppering": {"pressure_zone": "Regulated", "pressure_pa": 50.0, "mode": "None"},
                       "hold": {"pressure_zone": "Regulated", "pressure_pa": 50.0, "shelf_temperature_k": 293.15}}}


LYOVAPOR_GENERAL_FIELDS = {"name": {"type": "string", "minLength": 1, "maxLength": 30},
                           "gas_type": {"type": "string", "maxLength": 20},
                           "collapse_temperature_k": _lyo_num("K", 173.15, 333.15, True),
                           "shelf_load_temperature_k": _lyo_num("K", 213.15, 333.15, True)}
LYOVAPOR_STEP_FIELDS = {"phase": {"type": "string", "enum": ["Primary drying", "Secondary drying"]}, "term_min": _lyo_num("min", 1, 10000),
                        "shelf_temperature_k": _lyo_num("K", 213.15, 333.15), "pressure_zone": {"type": "string", "enum": ["Regulated", "Minimum"]},
                        "pressure_pa": _lyo_num("Pa", 10, 100), "pressure_limit_pa": _lyo_num("Pa", 0, 100), "pressure_duration_s": _lyo_num("s", 0, 3600)}
LYOVAPOR_TDT_FIELDS = {"enabled": {"type": "boolean"}, "start_time_min": _lyo_num("min", 0, 10000), "limit_k": _lyo_num("K", 0, 50),
                       "term_min": _lyo_num("min", 0, 1000), "continue": {"type": "boolean"}, "message": {"type": "boolean"}}
LYOVAPOR_PHASE_FIELDS = {
    "primary": {"pressure_action": {"type": "string", "enum": LYOVAPOR_ACTIONS}, "temp_action": {"type": "string", "enum": LYOVAPOR_ACTIONS},
                "safety_temperature_k": _lyo_num("K", 0, 50), "safety_time_min": _lyo_num("min", 0, 10000), "tdt": {"type": "object"}},
    "secondary": {"pressure_action": {"type": "string", "enum": LYOVAPOR_ACTIONS}, "temp_action": {"type": "string", "enum": LYOVAPOR_ACTIONS},
                  "safety_temperature_k": _lyo_num("K", 0, 50), "tdt": {"type": "object"}},
    "stoppering": {"pressure_zone": {"type": "string", "enum": ["Regulated", "Minimum"]}, "pressure_pa": _lyo_num("Pa", 10, 100),
                   "mode": {"type": "string", "enum": ["None", "Manual"]}},
    "hold": {"pressure_zone": {"type": "string", "enum": ["Regulated", "Minimum"]}, "pressure_pa": _lyo_num("Pa", 10, 100),
             "shelf_temperature_k": _lyo_num("K", 213.15, 333.15)},
}
_LYO_OBJ = {"type": "object"}
LYOVAPOR_DEVICE_SLOTS = {
    "system.status": (_g03_str(LYOVAPOR_STATUSES), "状态栏：仪器状态（p.37-39）"),
    "system.timer_s": (_lyo_num("s", 0, None, True), "状态栏时钟：干燥中为已运行时间，定时状态为剩余时间（p.38, p.41）"),
    "process.mode": (_g03_str(["manual", "method"], nullable=True), "Start菜单的冻干方式：手动或方法"),
    "process.manual": (_LYO_OBJ, "手动冻干参数：真空设定点（Pa）与搁板温度（K），运行中可改（p.41）"),
    "process.step": ({"anyOf": [_LYO_OBJ, {"type": "null"}]}, "方法运行的当前步骤：序号、阶段、剩余时间、设定值"),
    "protection.state": (_LYO_OBJ, "样品保护：压力（P）/温度（T）超限与UNPROT手动解除（p.39）"),
    "methods.list": ({"type": "array", "items": _LYO_OBJ}, "方法（最多35个，每个最多30步，含阶段设置）（p.43-52）"),
    "methods.active": (_g03_str(nullable=True), "已激活的方法（Activate）"),
    "favourites.list": ({"type": "array", "items": {"type": "string"}}, "收藏的子菜单/动作（p.37）"),
    "messages.notifications": ({"type": "array", "items": _LYO_OBJ}, "当前通知（I/W/E）（p.37, p.85-88）"),
    "messages.journal": ({"type": "array", "items": _LYO_OBJ}, "消息历史"),
    "graph.log": (_LYO_OBJ, "实时曲线：冷凝器温度、冷凝器压力、样品温度、搁板温度（每分钟一点，最近24 h）"),
    "settings.values": (_LYO_OBJ, "设置：压力传感器、DHCP、BUCHI Cloud（p.31, p.49）"),
    "service.state": (_LYO_OBJ, "真空测试与泄漏测试的设置、进行状态与结果（p.79-82）"),
    "defrost.state": (_LYO_OBJ, "热气除霜：是否进行、剩余时间（p.55-56）"),
    "methods.cursor": ({"anyOf": [_LYO_OBJ, {"type": "null"}]}, "方法菜单中选中的方法与步骤（Right / Left）及其内容"),
    "faults.active": ({"type": "array", "items": _LYO_OBJ}, "当前未排除的错误（评测者fault.inject注入的手册p.85-88错误代码：代码、类型、文字）；排除前E类错误阻止开始"),
}
for _lyo_sid, (_lyo_schema, _lyo_meaning) in LYOVAPOR_DEVICE_SLOTS.items():
    LYOVAPOR_STATE_SLOTS[_lyo_sid] = _lyo_slot("s" if _lyo_sid == "system.timer_s" else "1", "derived", _lyo_schema, _lyo_meaning)
LYOVAPOR_FAULTS = {   # manual p.85-88 (9.2 Error messages): code -> (message type, text = code + possible cause); type assumed (p.37: W customer action, E defect)
    "6040": ("W", "6040 Shelf set temperature is too high."), "6041": ("W", "6041 Shelf set temperature is too high. Leakages."),
    "6072": ("E", "6072 Safety underpressure shutdown."), "6170": ("W", "6170 High sample load. High ambient temperature."),
    "6171": ("W", "6171 High sample load. High ambient temperature."), "6172": ("E", "6172 Safety underpressure shutdown."),
    "6175": ("E", "6175 Separator inlet temperature sensor is disconnected."), "6176": ("E", "6176 Inlet temperature sensor on ice condenser is disconnected."),
    "6177": ("E", "6177 Outlet temperature sensor on ice condenser is disconnected."), "6179": ("E", "6179 Compressor outlet temperature sensor is disconnected."),
    "6187": ("E", "6187 Stepper chip overtemperature."), "6188": ("E", "6188 Stepper chip overtemperature."), "6189": ("E", "6189 Fan is not rotating."),
    "6190": ("E", "6190 High pressure sensor is disconnected."), "6191": ("E", "6191 Low pressure sensor is disconnected."),
    "6195": ("W", "6195 High sample load. High ambient temperature. Low air flow."), "6196": ("W", "6196 High sample load. High ambient temperature. Low air flow."),
    "6197": ("E", "6197 Compressor has no power."), "6198": ("E", "6198 Compressor error (voltage, motor or temperature error)."),
    "6272": ("E", "6272 Main valve is defective. Regulation valve is defective."), "6273": ("W", "6273 Leaks after new flask attachment."),
    "6278": ("W", "6278 Pressure increases above 1.5 mbar during leak test."), "6280": ("E", "6280 Regulation valve is disconnected."),
    "6281": ("E", "6281 Aeration valve is disconnected."), "6282": ("E", "6282 Main valve is disconnected."),
    "6570": ("E", "6570 Heatable shelf is disconnected."), "6571": ("E", "6571 Sample temperature sensor is disconnected."),
}
LYOVAPOR_EVALUATOR_CAPS = ("fault.inject", "fault.clear")
LYOVAPOR_DEVICE_ARGS = {   # name: (inputs, optional, outputs, example, description, effects, components, kind)
    "process.condition": ({}, (), {"status": {"type": "string"}}, {}, "Start conditioning：冷凝器降温、真空泵升温，约30 min后状态Unload / Load（p.40）",
                          ["system.status", "system.timer_s"], ["controller"], "device_command"),
    "process.select_mode": ({"mode": _g03_str(["manual", "method"])}, (), {"mode": {"type": "string"}}, {"mode": "manual"},
                            "Start菜单的Manual / Method：选择手动冻干或方法冻干（p.41, p.53）", ["process.mode"], ["controller"], "configuration"),
    "process.set_parameter": ({"pressure_pa": _lyo_num("Pa", 10, 100), "shelf_temperature_k": _lyo_num("K", 213.15, 333.15)},
                              ("pressure_pa", "shelf_temperature_k"), {"manual": _LYO_OBJ}, {"pressure_pa": 30.0},
                              "手动冻干参数：真空设定点与搁板温度；运行中修改立即作用于设定值（p.41）",
                              ["process.manual", "vacuum.target_pressure_pa", "thermal.target_shelf_temperature_k", "vacuum.compatible"], ["controller"],
                              "configuration"),
    "process.start": ({}, (), {"status": {"type": "string"}}, {}, "Start：手动或已激活方法的冻干在CHEM-025周期上运行（需已调理完成）",
                      ["system.status", "cycle.phase", "cycle.running", "cycle.recipe", "vacuum.target_pressure_pa", "thermal.target_shelf_temperature_k",
                       "process.step"], ["controller"], "device_command"),
    "process.aerate": ({}, (), {"status": {"type": "string"}}, {}, "Aerate + YES：结束冻干并通气，之后状态Unload / Load（p.43, p.54）",
                       ["system.status", "cycle.phase", "cycle.running", "cycle.paused", "cycle.endpoint_status"], ["controller"], "device_command"),
    "process.skip": ({}, (), {"status": {"type": "string"}}, {}, "Skip：跳过当前过程（调理剩余时间或方法当前步骤）（p.35）",
                     ["system.status", "process.step", "cycle.phase", "cycle.phase_elapsed_s", "cycle.elapsed_s"], ["controller"], "device_command"),
    "process.unprotect": ({}, (), {"deactivated": {"type": "boolean"}}, {}, "UNPROT：手动解除样品保护（P/T）（p.35）", ["protection.state"], ["controller"],
                          "device_command"),
    "process.shutdown": ({}, (), {"status": {"type": "string"}}, {}, "Shut down：关机（约50 min，状态栏显示剩余时间），之后Standby（p.54-55）",
                         ["system.status", "system.timer_s"], ["controller"], "device_command"),
    "defrost.control": ({"action": _g03_str(["start", "stop", "extend", "reduce"])}, (), {"defrost": _LYO_OBJ}, {"action": "start"},
                        "热气除霜（选件）：Start 60 min、+30 MIN、-30 MIN（总计≤300 min）、Stop；仅待机（p.55-56）", ["defrost.state", "system.status",
                                                                                                            "condenser.ice_mass_kg"],
                        ["controller", "condenser"], "device_command"),
    "methods.save": ({"method_id": _g03_str(nullable=True), "values": _LYO_OBJ}, (), {"method_id": {"type": "string"}},
                     {"method_id": None, "values": {"name": "Method 1"}}, "新建方法（method_id为空）或改方法的Information/General（名称、塌陷温度、气体、搁板装载温度）",
                     ["methods.list"], ["controller"], "configuration"),
    "methods.copy": ({"method_id": {"type": "string"}}, (), {"method_id": {"type": "string"}}, {"method_id": "m01"}, "复制方法（p.43）", ["methods.list"],
                     ["controller"], "configuration"),
    "methods.delete": ({"method_id": {"type": "string"}}, (), {"deleted": {"type": "boolean"}}, {"method_id": "m01"}, "删除方法（运行中的方法不可删）（p.48）",
                       ["methods.list", "methods.active"], ["controller"], "configuration"),
    "methods.activate": ({"method_id": _g03_str(nullable=True)}, (), {"active": _g03_str(nullable=True)}, {"method_id": "m01"},
                         "Activate：选定方法用于下一次冻干（p.53）", ["methods.active", "process.mode"], ["controller"], "configuration"),
    "methods.step": ({"method_id": {"type": "string"}, "action": _g03_str(["add", "delete", "set"]), "index": _g03_num("1", 0, 29, integer=True),
                      "values": _LYO_OBJ}, ("values",), {"steps": {"type": "integer", "x-unit": "1"}},
                     {"method_id": "m01", "action": "set", "index": 0, "values": {"term_min": 120}}, "方法步骤：在位置新建、删除或修改一步（p.45-46）",
                     ["methods.list"], ["controller"], "configuration"),
    "methods.phase": ({"method_id": {"type": "string"}, "phase": _g03_str(list(LYOVAPOR_PHASE_FIELDS)), "values": _LYO_OBJ}, (), {"phase": {"type": "string"}},
                      {"method_id": "m01", "phase": "hold", "values": {"pressure_pa": 40.0}}, "方法阶段设置（一次干燥、二次干燥、压塞、保持；温度差终点）（p.46-52）",
                      ["methods.list"], ["controller"], "configuration"),
    "favourites.add": ({"target": _g03_str(list(LYOVAPOR_FAVOURITE_TARGETS))}, (), {"targets": {"type": "array"}}, {"target": "vacuum_test"},
                       "Add to favourites：把当前子菜单/动作加入收藏（p.37）", ["favourites.list"], ["controller"], "configuration"),
    "favourites.delete": ({"target": _g03_str(list(LYOVAPOR_FAVOURITE_TARGETS))}, (), {"targets": {"type": "array"}}, {"target": "vacuum_test"},
                          "在收藏夹删除所选收藏（p.37）", ["favourites.list"], ["controller"], "configuration"),
    "methods.select": ({"method_id": _g03_str(nullable=True), "step_index": _g03_num("1", 0, 29, integer=True)}, ("step_index",),
                       {"cursor": {"anyOf": [_LYO_OBJ, {"type": "null"}]}}, {"method_id": "m01", "step_index": 0},
                       "选中方法（绿色高亮）与步骤（Right / Left）（p.43-46）", ["methods.cursor"], ["controller"], "configuration"),
    "messages.acknowledge": ({}, (), {"cleared": {"type": "integer", "x-unit": "1"}}, {}, "确认（清除）当前通知；历史保留在Journal", ["messages.notifications"],
                             ["controller"], "device_command"),
    "settings.configure": ({"settings": _g03_obj({f["key"]: ({"type": "boolean"} if f["type"] == "bool" else _g03_str(f["options"])) for f in LYOVAPOR_SETTINGS},
                                                optional=[f["key"] for f in LYOVAPOR_SETTINGS])}, (), {"settings": _LYO_OBJ},
                           {"settings": {"pressure_sensor": "Inficon Porter CDG020 D"}}, "设置：压力传感器选择、网络DHCP、BUCHI Cloud（p.31, p.49）",
                           ["settings.values"], ["controller"], "configuration"),
    "service.vacuum_test": ({"action": _g03_str(["start", "stop"]), "target_pa": _lyo_num("Pa", 3, 100), "time_s": _lyo_num("s", 60, 3600)},
                            ("target_pa", "time_s"), {"vacuum_test": _LYO_OBJ}, {"action": "start", "target_pa": 10.0, "time_s": 600.0},
                            "真空测试：调理完成、空架时在给定时间内达到目标真空；30 s未低于500 mbar自动中止（p.79）", ["service.state", "system.status"],
                            ["controller"], "device_command"),
    "service.leak_test": ({"action": _g03_str(["start", "stop"]), "condenser_pa": _lyo_num("Pa", 3, 100), "shelf_temperature_k": _lyo_num("K", 213.15, 333.15),
                           "scope": _g03_str(["Drying chamber", "Manifold drying rack"]), "shelf_heating": {"type": "boolean"}, "volume_l": _lyo_num("L", 1, 200)},
                          ("condenser_pa", "shelf_temperature_k", "scope", "shelf_heating", "volume_l"), {"leak_test": _LYO_OBJ},
                          {"action": "start", "volume_l": 36.46}, "泄漏测试：45 min，泄漏率小于10.10 mbar*L/h为通过（p.80-82）", ["service.state", "system.status"],
                          ["controller"], "device_command"),
    "fault.inject": ({"code": _g03_str(sorted(LYOVAPOR_FAULTS))}, (), {"faults": {"type": "array"}}, {"code": "6197"},
                     "评测者：注入手册p.85-88记载的错误（通知与Journal显示代码和原因；E类停止正在进行的过程并阻止开始，6278使进行中的泄漏测试失败）",
                     ["faults.active", "messages.notifications", "messages.journal", "system.status", "cycle.running", "service.state"], ["controller"],
                     "simulation"),
    "fault.clear": ({"code": _g03_str(sorted(LYOVAPOR_FAULTS))}, (), {"faults": {"type": "array"}}, {"code": "6197"},
                    "评测者：排除错误原因（维修、更换或手册的处理步骤）；通知仍需确认", ["faults.active"], ["controller"], "simulation"),
}
_LYO_BINDING = "basic40_runtime_vacuum.LyovaporSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"
LYOVAPOR_DEVICE_ERRORS = {"NOT_READY": "The instrument is not in the required status", "NOT_CONDITIONED": "Condition the instrument first",
                          "NO_METHOD": "No method is activated", "METHOD_ACTIVE": "The assigned method is active and cannot be changed",
                          "LIMIT": "The maximum number has been reached", "TEST_FAILED": "Test conditions not met", "NOT_RUNNING": "No process is running",
                          "FAULT": "An error is active: see Messages", "NO_FAULT": "This error is not active"}


LYOVAPOR_RUN_GUARDED = ("process.condition", "process.start", "process.aerate", "process.skip", "process.unprotect", "process.shutdown",
                        "process.set_parameter", "process.select_mode", "defrost.control", "methods.activate", "service.vacuum_test",
                        "service.leak_test", "settings.configure", "lyophilization.start", "lyophilization.stop", "lyophilization.abort",
                        "lyophilization.pause", "lyophilization.resume", "lyophilization.set_recipe", "lyophilization.set_endpoint",
                        "thermal.set_temperature", "thermal.set_protection_temperature", "vacuum.set_pressure", "condenser.defrost")


def _lyo_runs(s):
    """The process of the instrument (conditioning, Unload / Load, drying, aerating, tests, defrost, shutting down: every status but Off and
    Standby): one channel, owned by the agent that started it."""
    return {"process": bool(s["system.status"] not in ("Off", "Standby") or s["cycle.running"] or s["cycle.paused"] or s["defrost.state"]["active"])}


def _lyo_touches(cap, args):
    """power.set is the On/Off master switch (hard: physical, a power failure for the process, 6042 at power-up); Shut down (soft) is guarded."""
    return ("process",) if cap in LYOVAPOR_RUN_GUARDED else ()


class _LyoRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _lyo_method(state, mid):
    return next((m for m in state["methods.list"] if m["id"] == mid), None)


def _lyo_sorted_steps(method):
    return [s for s in method["steps"] if s["phase"] == "Primary drying"] + [s for s in method["steps"] if s["phase"] == "Secondary drying"]


def _lyo_cursor(state):
    """The selected method and step as the Method menu shows them (a copy of the method and of its step)."""
    cur = state["methods.cursor"]
    m = _lyo_method(state, cur["method_id"]) if cur else None
    if m is None:
        state["methods.cursor"] = None
        return
    k = min(cur.get("step_index", 0), len(m["steps"]) - 1)
    state["methods.cursor"] = {"method_id": m["id"], "step_index": k, "count": len(m["steps"]), "step": copy.deepcopy(m["steps"][k]),
                               "method": copy.deepcopy(m), "active": state["methods.active"] == m["id"]}


def _lyo_progression(state):
    """Set points of the active method over its run time (Progression view): shelf temperature (C) and pressure (mbar) as step lines,
    and the elapsed time of the running cycle."""
    m = _lyo_method(state, state["methods.active"])
    if m is None:
        return None
    t, shelf, pressure = 0.0, [], []
    for step in _lyo_sorted_steps(m):
        p = (10.0 if step["pressure_zone"] == "Minimum" else step["pressure_pa"]) / 100.0
        for x in (t, t + step["term_min"]):
            shelf.append([x, round(step["shelf_temperature_k"] - 273.15, 2)])
            pressure.append([x, p])
        t += step["term_min"]
    return {"shelf_c": shelf, "pressure_mbar": pressure, "total_min": t,
            "elapsed_min": state["cycle.elapsed_s"] / 60.0 if state["process.mode"] == "method" and state["cycle.phase"] != "idle" else None}


def _lyo_phase_settings(state):
    """The active method and the settings of its running drying phase (None outside a method run)."""
    if state["process.mode"] != "method" or state["cycle.phase"] not in ("primary_drying", "secondary_drying"):
        return None, None
    m = _lyo_method(state, state["methods.active"])
    if m is None:
        return None, None
    return m, m["phases"]["primary" if state["cycle.phase"] == "primary_drying" else "secondary"]


def _lyo_limits(inst):
    """(temperature exceeded, pressure exceeded) against the method limits: sample above collapse + safety temperature (primary drying, not
    within the Safety temp. Time before its end) or shelf above its set point + safety temperature (secondary drying); pressure above the step
    set point + pressure limit."""
    s = inst.state
    m, ph = _lyo_phase_settings(s)
    if m is None or s["thermal.sample_temperature_k"] is None or s["vacuum.pressure_pa"] is None:
        return False, s["vacuum.pressure_pa"] is not None and s["vacuum.pressure_pa"] > s["vacuum.target_pressure_pa"]
    if s["cycle.phase"] == "primary_drying":                                 # Safety temp. Time: inactive in the last minutes of primary drying (p.47)
        late = (ph.get("safety_time_min") or 0.0) > 0 and \
            s["cycle.recipe"]["primary_drying_s"] - s["cycle.phase_elapsed_s"] <= ph["safety_time_min"] * 60.0 + 1e-9
        t_exc = not late and m["collapse_temperature_k"] is not None and \
            s["thermal.sample_temperature_k"] > m["collapse_temperature_k"] + ph["safety_temperature_k"]
    else:
        t_exc = s["thermal.shelf_temperature_k"] > s["thermal.target_shelf_temperature_k"] + ph["safety_temperature_k"]
    step = s["process.step"] or {}
    limit = 0.0
    if step.get("index") is not None:
        limit = _lyo_sorted_steps(m)[step["index"]]["pressure_limit_pa"]
    return t_exc, s["vacuum.pressure_pa"] > s["vacuum.target_pressure_pa"] + limit


def _lyo_blocking(state):
    """An active E error (defective component or safety shutdown) blocks the starts until fault.clear."""
    return any(f["type"] == "E" for f in state.get("faults.active") or [])


def _lyo_message(state, code, kind, text, time_s):
    entry = {"code": code, "type": kind, "text": text, "time": _g03_clock(time_s)}
    state["messages.notifications"] = (state["messages.notifications"] + [entry])[-50:]
    state["messages.journal"] = (state["messages.journal"] + [entry])[-200:]


_g03_LyovaporHostBase = LyovaporHost
_g03_LyovaporSessionBase = LyovaporSession
_LYOVAPOR_PANEL = {}


def _lyovapor_panel():
    if not _LYOVAPOR_PANEL:
        engine = _g03_engine()
        caps = set(LYOVAPOR_ARGUMENTS) | set(LYOVAPOR_DEVICE_ARGS)
        spec = engine.load_spec(LYOVAPOR_ASSET_DIR / "panel_ui.json", capabilities=caps)
        _LYOVAPOR_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _LYOVAPOR_PANEL


class LyovaporHost(_g03_LyovaporHostBase):
    """CHEM-025 host with the control panel: per instance the device state (slots above), timers, method execution and the front panel."""

    PANEL_PRINCIPAL = "__lyovapor_panel__"

    def __init__(self, *, scene_adapter=None):
        super().__init__(scene_adapter=scene_adapter)
        self._panels = {}
        self.panel_capture_dir = None
        self._principals[self.PANEL_PRINCIPAL] = ("agent", None)
        self._logs[self.PANEL_PRINCIPAL] = []
        self._panel_session = LyovaporSession(self, self.PANEL_PRINCIPAL)
        self._panel_caller = None                                           # the session whose panel gesture is running (its principal acts)

    def create_instance(self, instance_id, asset_type_id=LYOVAPOR_ASSET_TYPE_ID):
        with self._lock:
            super().create_instance(instance_id, asset_type_id)
            inst = self._instances[instance_id]
            inst.state.update({"system.status": "Off", "system.timer_s": None, "process.mode": "manual",
                               "process.manual": {"pressure_pa": 50.0, "shelf_temperature_k": 253.15}, "process.step": None,
                               "protection.state": {"pressure": False, "temperature": False, "deactivated": False},
                               "methods.list": [], "methods.active": None, "favourites.list": [], "messages.notifications": [], "messages.journal": [],
                               "graph.log": {"condenser_c": [], "pressure_mbar": [], "sample_c": [], "shelf_c": []},
                               "settings.values": {f["key"]: f["default"] for f in LYOVAPOR_SETTINGS},
                               "service.state": {"vacuum_test": {"target_pa": 10.0, "time_s": 600.0, "running": False, "result": None, "elapsed_s": 0.0},
                                                 "leak_test": {"condenser_pa": 10.0, "shelf_temperature_k": 293.15, "scope": "Drying chamber",
                                                               "shelf_heating": False, "volume_l": 36.46, "running": False, "result": None, "elapsed_s": 0.0,
                                                               "rate": None, "start_pa": None}},
                               "defrost.state": {"active": False, "remaining_s": 0.0, "total_s": 0.0}, "methods.cursor": None, "faults.active": []})
            inst.g03 = {"until": None, "power_epoch": 0, "serial": 0, "power_failure": False, "tdt_s": 0.0, "last_graph": None, "prot_msg": set()}
            if (LYOVAPOR_ASSET_DIR / "panel_ui.json").is_file():
                self._panels[instance_id] = _lyovapor_panel()["engine"].PanelUI(_lyovapor_panel()["spec"], _G03PanelHost(
                    lambda: self._panel_values(instance_id), lambda cap, args: self._panel_call(instance_id, cap, args), lambda: self.time_s))

    def panel(self, instance_id):
        return self._panels[instance_id]

    def _panel_values(self, instance_id):
        inst = self._instances[instance_id]
        out = {sid: copy.deepcopy(v) for sid, v in inst.state.items() if LYOVAPOR_STATE_SLOTS[sid]["visibility"] == "agent"}
        out.update({"host.powered": inst.state["power.enabled"], "host.power_epoch": inst.g03["power_epoch"], "host.clock": _g03_clock(self.time_s),
                    "host.method_count": len(inst.state["methods.list"]), "host.progression": _lyo_progression(inst.state)})
        return out

    def _panel_call(self, instance_id, capability_id, arguments):
        """A command of the panel firmware runs as the session whose gesture triggered it; without a gesture as the panel's own principal."""
        with self._lock:
            return (self._panel_caller or self._panel_session)._apply(self._instances[instance_id], capability_id, arguments, None)

    @staticmethod
    def _gate(inst):
        """The CHEM-025 gate, unchanged outside methods and before UNPROT.  In a method the phase settings decide (p.46-47): the temperature
        protection also guards the sample collapse temperature plus the safety temperature (Temp. action = Sample protection), the pressure
        protection acts when the pressure stays above set point + limit for the pressure duration (Pressure action = Sample protection);
        UNPROT lifts both.  The physical gates (missing boundary, condenser not colder than the sample, condenser full) always apply."""
        gate = _g03_LyovaporHostBase._gate(inst)
        g, s = getattr(inst, "g03", None), inst.state
        if g is None or gate == "missing_boundary":
            return gate
        m, ph = _lyo_phase_settings(s)
        unprot = s["protection.state"]["deactivated"]
        if m is None and not unprot:
            return gate
        temp_exceeded, pressure_exceeded = _lyo_limits(inst)
        protection = s["thermal.protection_temperature_k"]
        if s["sample.collapse_temperature_k"] is not None:
            protection = min(protection, s["sample.collapse_temperature_k"])
        base_temp = s["thermal.sample_temperature_k"] > protection or s["thermal.shelf_temperature_k"] > s["thermal.protection_temperature_k"]
        if not unprot:
            if base_temp or (ph is not None and ph["temp_action"] == "Sample protection" and temp_exceeded):
                return "temperature"
            if s["cycle.phase"] != "pre_freeze" and pressure_exceeded and (ph is None or (ph["pressure_action"] == "Sample protection"
                                                                                          and g.get("p_over_s", 0.0) >= g.get("p_duration", 0.0))):
                return "pressure"
        if s["cycle.phase"] != "pre_freeze":
            template_wait = _vacuum_templates("buchi_lyovapor_l250").template_gate(inst) if inst.template else None
            if template_wait:
                return template_wait
            if s["condenser.temperature_k"] >= s["thermal.sample_temperature_k"]:
                return "temperature"
            if s["condenser.ice_mass_kg"] >= LYOVAPOR_MAX_ICE_KG - 1e-12:
                return "condenser_full"
        return "none"

    def _advance(self, inst, dt):
        g = getattr(inst, "g03", None)
        if g is None:
            return super()._advance(inst, dt)
        s = inst.state
        t0 = self.time_s
        done = 0.0
        while dt - done > 1e-12:
            chunk = dt - done
            step_left = self._lyo_step_left(inst)
            if s["cycle.running"] and step_left is not None and step_left > 1e-9:
                chunk = min(chunk, step_left)
            if s["process.mode"] == "method" and s["cycle.running"]:
                chunk = min(chunk, 60.0)                                     # end-point test and protection times checked at least once a minute
            elapsed0 = s["cycle.elapsed_s"]
            m, _ = _lyo_phase_settings(s)
            over = _lyo_limits(inst)[1] if m is not None else False
            g["p_over_s"] = g.get("p_over_s", 0.0) + chunk if over else 0.0
            step = s["process.step"] or {}
            g["p_duration"] = _lyo_sorted_steps(m)[step["index"]]["pressure_duration_s"] if m is not None and step.get("index") is not None else 0.0
            super()._advance(inst, chunk)
            done += chunk
            self._lyo_timers(inst, t0 + done, chunk)
            self._lyo_method_targets(inst)
            self._lyo_protection(inst, t0 + done)
            self._lyo_tdt(inst, t0 + done, s["cycle.elapsed_s"] - elapsed0)
        self._lyo_graph(inst, t0 + dt)
        self._lyo_status(inst, t0 + dt)

    def _lyo_step_left(self, inst):
        s = inst.state
        if s["process.mode"] != "method" or not s["cycle.running"] or s["cycle.phase"] not in ("primary_drying", "secondary_drying"):
            return None
        m = _lyo_method(s, s["methods.active"])
        if m is None:
            return None
        t, phase_t = 0.0, s["cycle.phase_elapsed_s"]
        for step in _lyo_sorted_steps(m):
            if (step["phase"] == "Primary drying") != (s["cycle.phase"] == "primary_drying"):
                continue
            t += step["term_min"] * 60.0
            if phase_t < t - 1e-9:
                return t - phase_t
        return None

    def _lyo_method_targets(self, inst):
        """Set points of the method step in progress (the controller follows its own steps), the Hold phase after the last step, and the
        shelf loading temperature of the activated method while the instrument waits in Unload / Load."""
        s = inst.state
        if s["process.mode"] != "method" or s["methods.active"] is None:
            s["process.step"] = None
            return
        m = _lyo_method(s, s["methods.active"])
        if m is None or s["cycle.phase"] in ("idle", "stopped", "aborted"):
            s["process.step"] = None
            if m is not None and s["system.status"] == "Unload / Load" and m.get("shelf_load_temperature_k") is not None:
                s["thermal.target_shelf_temperature_k"] = m["shelf_load_temperature_k"]      # Shelf load temp. of the activated method (p.45)
            return
        if s["cycle.phase"] == "complete":
            hold = m["phases"]["hold"]
            s["vacuum.target_pressure_pa"] = 10.0 if hold["pressure_zone"] == "Minimum" else hold["pressure_pa"]
            s["thermal.target_shelf_temperature_k"] = hold["shelf_temperature_k"]
            s["process.step"] = {"index": None, "phase": "Hold", "remaining_s": None}
            return
        steps = _lyo_sorted_steps(m)
        want = "Secondary drying" if s["cycle.phase"] == "secondary_drying" else "Primary drying"
        t, phase_t, k = 0.0, s["cycle.phase_elapsed_s"], None
        for i, step in enumerate(steps):
            if step["phase"] != want:
                continue
            t += step["term_min"] * 60.0
            k = i
            if phase_t < t - 1e-9:
                break
        if k is None:
            return
        step = steps[k]
        s["vacuum.target_pressure_pa"] = 10.0 if step["pressure_zone"] == "Minimum" else step["pressure_pa"]
        s["thermal.target_shelf_temperature_k"] = step["shelf_temperature_k"]
        s["process.step"] = {"index": k, "count": len(steps), "phase": step["phase"], "remaining_s": max(0.0, t - phase_t),
                             "pressure_pa": s["vacuum.target_pressure_pa"], "shelf_temperature_k": step["shelf_temperature_k"]}

    def _lyo_protection(self, inst, now):
        """P / T on the status bar while sample protection holds the process; a message when a limit is exceeded and the action is Message."""
        s, g = inst.state, inst.g03
        reason = s["cycle.wait_reason"] if s["cycle.running"] else "none"
        prot = s["protection.state"]
        temp_exc = s["cycle.running"] and reason == "temperature" and s["condenser.temperature_k"] is not None and \
            s["thermal.sample_temperature_k"] is not None and s["condenser.temperature_k"] < s["thermal.sample_temperature_k"]
        prot["pressure"], prot["temperature"] = reason == "pressure", bool(temp_exc)
        m, ph = _lyo_phase_settings(s)
        if m is not None and s["cycle.running"]:
            exceeded = dict(zip(("temperature", "pressure"), _lyo_limits(inst)))
            for kind in ("pressure", "temperature"):
                action = ph["pressure_action" if kind == "pressure" else "temp_action"]
                if exceeded[kind] and action == "Message" and kind not in g["prot_msg"]:
                    _lyo_message(s, "W", "W", f"The {kind} is outside the limits.", now)
                    g["prot_msg"].add(kind)
                if not exceeded[kind]:
                    g["prot_msg"].discard(kind)

    def _lyo_tdt(self, inst, now, dt):
        """Temperature difference test end point of primary drying (p.52): shelf minus sample below the limit for the term, counted from the start
        time before the end of primary drying; Continue = go on to secondary drying."""
        s, g = inst.state, inst.g03
        if s["process.mode"] != "method" or not s["cycle.running"] or s["cycle.phase"] != "primary_drying":
            g["tdt_s"] = 0.0
            return
        m = _lyo_method(s, s["methods.active"])
        tdt = m["phases"]["primary"]["tdt"] if m else None
        if not tdt or not tdt["enabled"] or s["thermal.indicated_shelf_temperature_k"] is None or s["thermal.indicated_sample_temperature_k"] is None:
            return
        left = s["cycle.recipe"]["primary_drying_s"] - s["cycle.phase_elapsed_s"]
        if left > tdt["start_time_min"] * 60.0 + 1e-9:
            return
        if s["thermal.indicated_shelf_temperature_k"] - s["thermal.indicated_sample_temperature_k"] < tdt["limit_k"]:
            g["tdt_s"] += dt
        else:
            g["tdt_s"] = 0.0
        if g["tdt_s"] >= tdt["term_min"] * 60.0 - 1e-9:
            g["tdt_s"] = 0.0
            if tdt["message"]:
                _lyo_message(s, "I", "I", "The temperature difference test of primary drying is passed.", now)
            if tdt["continue"]:                                               # the rest of primary drying is not run (no effective time added)
                s["cycle.phase"], s["cycle.phase_elapsed_s"] = "secondary_drying", 0.0

    def _lyo_timers(self, inst, now, dt):
        s, g = inst.state, inst.g03
        status = s["system.status"]
        if g["until"] is not None and now >= g["until"] - 1e-9:
            g["until"] = None
            if status == "Conditioning":
                s["system.status"] = "Unload / Load"
                _lyo_message(s, "I", "I", "Conditioning is completed.", now)
            elif status == "Aerating":
                s["system.status"] = "Unload / Load"
            elif status == "Shutting down":
                s["system.status"] = "Standby"
        d = s["defrost.state"]
        if d["active"]:
            d["remaining_s"] = max(0.0, d["remaining_s"] - dt)
            if d["remaining_s"] <= 1e-9:
                self._lyo_defrost_end(inst, now)
        for name in ("vacuum_test", "leak_test"):
            test = s["service.state"][name]
            if not test["running"]:
                continue
            test["elapsed_s"] += dt
            p = s["vacuum.indicated_pressure_pa"]
            if name == "vacuum_test":
                if p is not None and p <= test["target_pa"] + 1e-9:
                    test.update(running=False, result="passed")
                elif test["elapsed_s"] >= 30.0 and (p is None or p >= 50000.0) and test["elapsed_s"] - dt < 30.0 + 1e-9:
                    test.update(running=False, result="aborted")
                    _lyo_message(s, "6275", "E", "6275 Vacuum test: 500 mbar not reached within 30 s.", now)
                elif test["elapsed_s"] >= test["time_s"] - 1e-9:
                    test.update(running=False, result="failed")
                    _lyo_message(s, "6274", "E", "6274 Vacuum not reached in the set time.", now)
            elif test["elapsed_s"] >= LYOVAPOR_LEAK_TEST_S - 1e-9:
                p0 = test["start_pa"]
                rate = None if p is None or p0 is None else max(0.0, (p - p0) / 100.0) * test["volume_l"] / (LYOVAPOR_LEAK_TEST_S / 3600.0)
                passed = rate is not None and rate < LYOVAPOR_LEAK_LIMIT
                test.update(running=False, result="passed" if passed else "failed", rate=rate)
                if not passed:
                    _lyo_message(s, "6279", "E", "6279 Leak test: the result is above the limit.", now)
            if not test["running"] and s["system.status"] in ("Vacuum Test", "Leak Test"):
                s["system.status"] = "Unload / Load"
                self._lyo_test_restore(inst)

    @staticmethod
    def _lyo_test_targets(c, pressure_pa, shelf_temperature_k=None):
        """A vacuum or leak test drives the controller to its own set points (ice condenser vacuum; shelf temperature with Drying shelf
        heating on, p.79-80); the previous set points come back when the test ends or stops."""
        s = c.state
        c.g03["test_restore"] = {k: s[k] for k in ("vacuum.target_pressure_pa", "thermal.target_shelf_temperature_k")}
        s["vacuum.target_pressure_pa"] = max(10.0, float(pressure_pa))             # the regulated range of the set point starts at 10 Pa
        if shelf_temperature_k is not None:
            s["thermal.target_shelf_temperature_k"] = float(shelf_temperature_k)

    @staticmethod
    def _lyo_test_restore(c):
        saved = c.g03.pop("test_restore", None)
        if saved:
            c.state.update(saved)

    def _lyo_defrost_end(self, inst, now):
        s = inst.state
        s["defrost.state"].update(active=False, remaining_s=0.0)
        if s["system.status"] == "Defrosting":
            s["system.status"] = "Standby"
        # the melted ice is moved by the CHEM-025 bookkeeping only when its own conditions hold (warm condenser, no sample, source isolated)
        if not s["sample.loaded"] and inst.source is None and s["condenser.temperature_k"] is not None and s["condenser.temperature_k"] >= 273.15:
            s["condenser.drained_mass_kg"] += s["condenser.ice_mass_kg"]
            s["condenser.ice_mass_kg"] = 0.0
        _lyo_message(s, "I", "I", "Defrosting is completed.", now)

    def _lyo_status(self, inst, now):
        s, g = inst.state, inst.g03
        if not s["power.enabled"]:
            s["system.status"], s["system.timer_s"] = "Off", None
            return
        phase = s["cycle.phase"]
        if s["cycle.running"] or s["cycle.paused"] or (phase == "complete" and s["process.mode"] == "method" and s["system.status"] in ("Primary drying",
                                                                                                                               "Secondary drying", "Hold")):
            if s["process.mode"] == "manual":
                s["system.status"] = "Manual Drying"
            else:
                s["system.status"] = {"pre_freeze": "Primary drying", "primary_drying": "Primary drying", "secondary_drying": "Secondary drying",
                                      "complete": "Hold"}.get(phase, s["system.status"])
            s["system.timer_s"] = s["cycle.elapsed_s"]
        elif g["until"] is not None:
            s["system.timer_s"] = max(0.0, g["until"] - now)
        elif s["defrost.state"]["active"]:
            s["system.timer_s"] = s["defrost.state"]["remaining_s"]
        else:
            s["system.timer_s"] = None

    def _lyo_graph(self, inst, now):
        """One point per minute mark passed (the boundary values are piecewise constant between host steps)."""
        s, g = inst.state, inst.g03
        if not s["power.enabled"]:
            return
        last = g["last_graph"] if g["last_graph"] is not None else now - LYOVAPOR_GRAPH_S
        marks = [m * LYOVAPOR_GRAPH_S for m in range(int(math.floor(last / LYOVAPOR_GRAPH_S + 1e-9)) + 1, int(math.floor(now / LYOVAPOR_GRAPH_S + 1e-9)) + 1)]
        marks = marks[-LYOVAPOR_GRAPH_N:]
        if not marks:
            return
        g["last_graph"] = marks[-1]
        for mark in marks:
            self._lyo_graph_point(s, mark)

    @staticmethod
    def _lyo_graph_point(s, now):
        t = round(now / 60.0, 3)
        log = s["graph.log"]
        for key, sid, conv in (("condenser_c", "condenser.indicated_temperature_k", lambda v: v - 273.15),
                               ("pressure_mbar", "vacuum.indicated_pressure_pa", lambda v: v / 100.0),
                               ("sample_c", "thermal.indicated_sample_temperature_k", lambda v: v - 273.15),
                               ("shelf_c", "thermal.indicated_shelf_temperature_k", lambda v: v - 273.15)):
            if s[sid] is not None:
                log[key] = (log[key] + [[t, round(conv(s[sid]), 3)]])[-LYOVAPOR_GRAPH_N:]


class LyovaporSession(_g03_LyovaporSessionBase):
    """CHEM-025 session plus the control-panel device commands and the panel.* commands."""

    @property
    def _host(self):
        return self._LyovaporSession__host

    @property
    def _visibility(self):
        return self._host._principals[self._LyovaporSession__principal_id][0]

    def describe(self, *, asset_type_id=None, instance_id=None):
        response = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if response.get("error") is None and "capabilities" in response:
            evaluator = self._visibility in ("debug", "evaluator")
            meta = export_lyovapor_contract_metadata()["asset_types"][LYOVAPOR_ASSET_TYPE_ID]
            slots = response["state_slots"]
            for group in ("device_capabilities", "panel_capabilities"):
                for name, cap in meta[group].items():
                    if cap["visibility"] == "agent" or evaluator:
                        cap = copy.deepcopy(cap)
                        cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in slots]
                        cap["observes"] = [sid for sid in cap["observes"] if sid in slots]
                        response["capabilities"][name] = cap
            response["asset_version"] = LYOVAPOR_ASSET_VERSION
        return response

    def _apply(self, inst, cap, args, request_id):
        """Run ownership (g03_owner_refuses) before the command; a process the command starts belongs to this session's principal.  The
        drying chamber (chamber.set_open), messages, favourites and method editing are not guarded (a running method is locked by the device)."""
        g, me, vis = getattr(inst, "g03", None), self._LyovaporSession__principal_id, self._visibility
        if g is not None and g03_owner_refuses(g, _lyo_runs(inst.state), _lyo_touches(cap, args), me, vis, LyovaporHost.PANEL_PRINCIPAL):
            return self._error(inst.instance_id, "ACCESS_DENIED", request_id)
        before = _lyo_runs(inst.state) if g is not None else None
        response = self._lyo_apply(inst, cap, args, request_id)
        live = self._host._instances.get(inst.instance_id)
        if response["status"] == "succeeded" and getattr(live, "g03", None) is not None:
            g03_owner_note(live.g03, before, _lyo_runs(live.state), me, vis, LyovaporHost.PANEL_PRINCIPAL)
        return response

    def _lyo_apply(self, inst, cap, args, request_id):
        if cap in LYOVAPOR_DEVICE_ARGS or cap.startswith("panel."):
            return self._device_apply(inst, cap, args, request_id)
        if cap == "lyophilization.start" and getattr(inst, "g03", None) is not None and _lyo_blocking(inst.state):
            return self._error(inst.instance_id, "PRECONDITION_FAILED", request_id)     # an active E error blocks every start
        before = inst.state["power.enabled"]
        response = super()._apply(inst, cap, args, request_id)
        live = self._host._instances[inst.instance_id]
        if response["status"] == "succeeded" and getattr(live, "g03", None) is not None:
            s, g = live.state, live.g03
            if cap == "power.set" and args["enabled"] != before:
                g["power_epoch"] += 1
                if not args["enabled"]:
                    g["power_failure"] = s["system.status"] in ("Manual Drying", "Primary drying", "Secondary drying", "Hold", "Conditioning")
                    g["until"] = None
                    s["defrost.state"].update(active=False, remaining_s=0.0)
                    for test in s["service.state"].values():
                        test["running"] = False
                    self._host._lyo_test_restore(live)
                    s["system.status"], s["system.timer_s"] = "Off", None
                else:
                    s["system.status"] = "Standby"
                    if g["power_failure"]:
                        _lyo_message(s, "6042", "W", "6042 Power failure has occurred. Check samples.", self._host.time_s)
                        g["power_failure"] = False
            if cap.startswith("lyophilization.") and cap not in ("lyophilization.set_recipe", "lyophilization.set_endpoint"):
                self._host._lyo_status(live, self._host.time_s)
        return response

    def _device_apply(self, inst, cap, args, request_id):
        host, rid = self._host, request_id
        if cap.startswith("panel."):
            meta = _lyovapor_panel()["caps"] if (LYOVAPOR_ASSET_DIR / "panel_ui.json").is_file() else {}
            if cap not in meta: return self._error(inst.instance_id, "UNKNOWN_CAPABILITY", rid)
            if meta[cap]["visibility"] != "agent" and self._visibility not in ("debug", "evaluator"): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            if _g03_check(meta[cap]["input_schema"], args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
            if self._LyovaporSession__principal_id == LyovaporHost.PANEL_PRINCIPAL or inst.instance_id not in host._panels:
                return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            previous, host._panel_caller = host._panel_caller, self                # the commands of this gesture act as this session
            try:
                result, code = g03_panel_gesture(host._panels[inst.instance_id], cap, args, instance_id=inst.instance_id, time_s=host.time_s,
                                                 capture_dir=host.panel_capture_dir)
            finally:
                host._panel_caller = previous
            if code: return self._error(inst.instance_id, code, rid)
            return self._envelope(inst.instance_id, rid, result=result)
        inputs, optional = LYOVAPOR_DEVICE_ARGS[cap][0], LYOVAPOR_DEVICE_ARGS[cap][1]
        if cap in LYOVAPOR_EVALUATOR_CAPS and self._visibility not in ("debug", "evaluator"): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
        if _g03_check(_g03_obj(inputs, optional), args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
        candidate = copy.deepcopy(inst)
        try:
            result = getattr(self, "_lyo_" + cap.replace(".", "_"))(candidate, args)
        except _LyoRefused as exc:
            if exc.code in ("INVALID_ARGUMENT", "PRECONDITION_FAILED", "BUSY", "ACCESS_DENIED"):
                return self._error(inst.instance_id, exc.code, rid)
            return self._envelope(inst.instance_id, rid, status="rejected", error={"code": exc.code, "message": LYOVAPOR_DEVICE_ERRORS[exc.code],
                                                                                   "retryable": False, "side_effects": "none", "known_progress": None})
        host._lyo_method_targets(candidate)
        _lyo_cursor(candidate.state)
        host._lyo_status(candidate, host.time_s)
        host._refresh(candidate)
        try:
            host._emit(candidate)
        except Exception:
            return self._error(inst.instance_id, "DEVICE_ERROR", rid, "failed")
        candidate.revision += 1
        host._instances[inst.instance_id] = candidate
        return self._envelope(inst.instance_id, rid, result=result)

    # ---- device commands (on a candidate copy; a refusal raises _LyoRefused before anything is committed)
    @staticmethod
    def _on(c, *statuses):
        s = c.state
        if not s["power.enabled"]: raise _LyoRefused("NOT_READY")
        if statuses and s["system.status"] not in statuses: raise _LyoRefused("NOT_READY")

    @staticmethod
    def _idle(c):
        if c.state["cycle.running"] or c.state["cycle.paused"]: raise _LyoRefused("BUSY")

    @staticmethod
    def _no_fault(c):
        if _lyo_blocking(c.state): raise _LyoRefused("FAULT")

    def _lyo_fault_inject(self, c, args):
        """An injected error (manual p.85-88): the message with its code and cause in the notifications and the Journal; an E error (defective
        component or safety shutdown) stops the running process (drying, conditioning, Unload / Load cooling, tests, defrost, shutting down:
        the instrument goes to Standby, test set points restored) and blocks every start until fault.clear; 6278 fails a running leak test."""
        s, code = c.state, args["code"]
        kind, text = LYOVAPOR_FAULTS[code]
        if not any(f["code"] == code for f in s["faults.active"]):
            s["faults.active"] = s["faults.active"] + [{"code": code, "type": kind, "text": text}]
        _lyo_message(s, code, kind, text, self._host.time_s)
        leak = s["service.state"]["leak_test"]
        if code == "6278" and leak["running"]:
            leak.update(running=False, result="failed")
            self._host._lyo_test_restore(c)
            s["system.status"] = "Unload / Load"
        if kind == "E" and s["power.enabled"]:
            if s["cycle.running"] or s["cycle.paused"]:
                s.update({"cycle.running": False, "cycle.paused": False, "cycle.phase": "stopped", "cycle.endpoint_status": "stopped",
                          "cycle.wait_reason": "none"})
            for test in s["service.state"].values():
                if test["running"]:
                    test.update(running=False, result="aborted")
            self._host._lyo_test_restore(c)
            s["defrost.state"].update(active=False, remaining_s=0.0)
            c.g03["until"] = None
            s["protection.state"] = {"pressure": False, "temperature": False, "deactivated": False}
            s["system.status"], s["system.timer_s"] = "Standby", None
        return {"faults": copy.deepcopy(s["faults.active"])}

    def _lyo_fault_clear(self, c, args):
        s = c.state
        if not any(f["code"] == args["code"] for f in s["faults.active"]): raise _LyoRefused("NO_FAULT")
        s["faults.active"] = [f for f in s["faults.active"] if f["code"] != args["code"]]
        return {"faults": copy.deepcopy(s["faults.active"])}

    def _lyo_process_condition(self, c, args):
        self._on(c, "Standby")
        self._no_fault(c)
        c.state["system.status"] = "Conditioning"
        c.g03["until"] = self._host.time_s + LYOVAPOR_CONDITIONING_S
        return {"status": "Conditioning"}

    def _lyo_process_select_mode(self, c, args):
        self._on(c)
        self._idle(c)
        c.state["process.mode"] = args["mode"]
        return {"mode": args["mode"]}

    def _lyo_process_set_parameter(self, c, args):
        self._on(c)
        s = c.state
        s["process.manual"].update({k: float(v) for k, v in args.items()})
        if s["process.mode"] == "manual" or not (s["cycle.running"] or s["cycle.paused"]):
            if "pressure_pa" in args: s["vacuum.target_pressure_pa"] = float(args["pressure_pa"])
            if "shelf_temperature_k" in args: s["thermal.target_shelf_temperature_k"] = float(args["shelf_temperature_k"])
        return {"manual": copy.deepcopy(s["process.manual"])}

    def _lyo_process_start(self, c, args):
        self._on(c)
        self._idle(c)
        self._no_fault(c)
        s = c.state
        if s["system.status"] != "Unload / Load": raise _LyoRefused("NOT_CONDITIONED")
        if s["process.mode"] == "method":
            m = _lyo_method(s, s["methods.active"])
            if m is None: raise _LyoRefused("NO_METHOD")
            steps = _lyo_sorted_steps(m)
            primary = sum(x["term_min"] * 60.0 for x in steps if x["phase"] == "Primary drying")
            secondary = sum(x["term_min"] * 60.0 for x in steps if x["phase"] == "Secondary drying")
            if primary + secondary <= 0 or primary > 604800 or secondary > 604800: raise _LyoRefused("INVALID_ARGUMENT")
            s["cycle.recipe"] = {"pre_freeze_s": 0.0, "primary_drying_s": primary, "secondary_drying_s": secondary}
            first = steps[0]
            s["vacuum.target_pressure_pa"] = 10.0 if first["pressure_zone"] == "Minimum" else first["pressure_pa"]
            s["thermal.target_shelf_temperature_k"] = first["shelf_temperature_k"]
        else:
            s["cycle.recipe"] = {"pre_freeze_s": 0.0, "primary_drying_s": LYOVAPOR_MANUAL_PHASE_S, "secondary_drying_s": 0.0}
            s["vacuum.target_pressure_pa"] = s["process.manual"]["pressure_pa"]
            s["thermal.target_shelf_temperature_k"] = s["process.manual"]["shelf_temperature_k"]
        self._host._refresh(c)
        if not self._host._start_ready(c): raise _LyoRefused("PRECONDITION_FAILED")
        s.update({"cycle.phase": "pre_freeze", "cycle.running": True, "cycle.paused": False, "cycle.phase_elapsed_s": 0.0, "cycle.elapsed_s": 0.0,
                  "cycle.endpoint_status": "pending", "cycle.wait_reason": "none"})
        s["protection.state"] = {"pressure": False, "temperature": False, "deactivated": False}
        s["system.status"] = "Manual Drying" if s["process.mode"] == "manual" else "Primary drying"
        return {"status": s["system.status"]}

    def _lyo_process_aerate(self, c, args):
        self._on(c)
        s = c.state
        if not (s["cycle.running"] or s["cycle.paused"] or s["system.status"] in ("Hold", "Manual Drying", "Primary drying", "Secondary drying")):
            raise _LyoRefused("NOT_RUNNING")
        if s["cycle.running"] or s["cycle.paused"]:
            s.update({"cycle.running": False, "cycle.paused": False, "cycle.phase": "stopped", "cycle.endpoint_status": "stopped", "cycle.wait_reason": "none"})
        s["system.status"] = "Aerating"
        c.g03["until"] = self._host.time_s + LYOVAPOR_AERATE_S
        s["protection.state"] = {"pressure": False, "temperature": False, "deactivated": False}
        return {"status": "Aerating"}

    def _lyo_process_skip(self, c, args):
        self._on(c)
        s = c.state
        if s["system.status"] == "Conditioning":
            c.g03["until"] = self._host.time_s
            s["system.status"] = "Unload / Load"
            c.g03["until"] = None
            return {"status": "Unload / Load"}
        left = self._host._lyo_step_left(c)
        if left is None: raise _LyoRefused("NOT_RUNNING")
        s["cycle.phase_elapsed_s"] += left                                       # the program position moves on; no effective time is added
        duration = s["cycle.recipe"][s["cycle.phase"] + "_s"]
        if s["cycle.phase_elapsed_s"] + 1e-9 >= duration:
            if s["cycle.phase"] == "primary_drying" and s["cycle.recipe"]["secondary_drying_s"] > 0:
                s["cycle.phase"], s["cycle.phase_elapsed_s"] = "secondary_drying", 0.0
            else:
                s["cycle.phase"], s["cycle.running"] = "complete", False
                s["cycle.endpoint_status"] = "schedule_complete_unverified" if s["cycle.endpoint_mode"] == "schedule_only" else s["cycle.endpoint_status"]
        return {"status": s["system.status"]}

    def _lyo_process_unprotect(self, c, args):
        self._on(c)
        if not c.state["cycle.running"]: raise _LyoRefused("NOT_RUNNING")
        c.state["protection.state"]["deactivated"] = True
        return {"deactivated": True}

    def _lyo_process_shutdown(self, c, args):
        self._on(c, "Unload / Load", "Standby")
        self._idle(c)
        c.state["system.status"] = "Shutting down"
        c.g03["until"] = self._host.time_s + LYOVAPOR_SHUTDOWN_S
        return {"status": "Shutting down"}

    def _lyo_defrost_control(self, c, args):
        self._on(c)
        d, action = c.state["defrost.state"], args["action"]
        if action == "start":
            if c.state["system.status"] != "Standby" or d["active"]: raise _LyoRefused("NOT_READY")
            self._no_fault(c)
            d.update(active=True, remaining_s=LYOVAPOR_DEFROST_S, total_s=LYOVAPOR_DEFROST_S)
            c.state["system.status"] = "Defrosting"
        elif not d["active"]:
            raise _LyoRefused("NOT_RUNNING")
        elif action == "stop":
            d.update(active=False, remaining_s=0.0)
            c.state["system.status"] = "Standby"
        elif action == "extend":
            if d["total_s"] + 1800.0 > LYOVAPOR_DEFROST_MAX_S + 1e-9: raise _LyoRefused("LIMIT")
            d["remaining_s"] += 1800.0
            d["total_s"] += 1800.0
        else:
            if d["remaining_s"] <= 1800.0 + 1e-9: raise _LyoRefused("LIMIT")
            d["remaining_s"] -= 1800.0
            d["total_s"] -= 1800.0
        return {"defrost": copy.deepcopy(d)}

    @staticmethod
    def _editable(c, mid, running_ok=False):
        m = _lyo_method(c.state, mid)
        if m is None: raise _LyoRefused("INVALID_ARGUMENT")
        if not running_ok and c.state["methods.active"] == mid and (c.state["cycle.running"] or c.state["cycle.paused"]): raise _LyoRefused("METHOD_ACTIVE")
        return m

    @staticmethod
    def _check_values(values, schema):
        for k, v in values.items():
            if k not in schema or _g03_check(schema[k], v): raise _LyoRefused("INVALID_ARGUMENT")

    def _lyo_methods_save(self, c, args):
        self._on(c)
        s = c.state
        self._check_values(args["values"], LYOVAPOR_GENERAL_FIELDS)
        if args["method_id"] is None:
            if len(s["methods.list"]) >= LYOVAPOR_MAX_METHODS: raise _LyoRefused("LIMIT")
            c.g03["serial"] += 1
            mid = f"m{c.g03['serial']:02d}"
            method = _lyo_new_method(mid, f"Method {c.g03['serial']}")
            s["methods.list"].append(method)
        else:
            method = self._editable(c, args["method_id"])
        method.update(copy.deepcopy(args["values"]))
        if args["method_id"] is None:
            s["methods.cursor"] = {"method_id": method["id"], "step_index": 0}
        return {"method_id": method["id"]}

    def _lyo_methods_copy(self, c, args):
        self._on(c)
        s = c.state
        src = _lyo_method(s, args["method_id"])
        if src is None: raise _LyoRefused("INVALID_ARGUMENT")
        if len(s["methods.list"]) >= LYOVAPOR_MAX_METHODS: raise _LyoRefused("LIMIT")
        c.g03["serial"] += 1
        new = copy.deepcopy(src)
        new.update(id=f"m{c.g03['serial']:02d}", name=(src["name"] + " copy")[:30])
        s["methods.list"].append(new)
        return {"method_id": new["id"]}

    def _lyo_methods_delete(self, c, args):
        self._on(c)
        self._editable(c, args["method_id"])
        s = c.state
        s["methods.list"] = [m for m in s["methods.list"] if m["id"] != args["method_id"]]
        if s["methods.active"] == args["method_id"]: s["methods.active"] = None
        return {"deleted": True}

    def _lyo_methods_activate(self, c, args):
        self._on(c)
        self._idle(c)
        if args["method_id"] is not None and _lyo_method(c.state, args["method_id"]) is None: raise _LyoRefused("INVALID_ARGUMENT")
        c.state["methods.active"] = args["method_id"]
        if args["method_id"] is not None: c.state["process.mode"] = "method"
        return {"active": args["method_id"]}

    def _lyo_methods_step(self, c, args):
        self._on(c)
        s = c.state
        running = s["methods.active"] == args["method_id"] and (s["cycle.running"] or s["cycle.paused"])
        m = self._editable(c, args["method_id"], running_ok=args["action"] == "delete")
        steps, i, action = m["steps"], args["index"], args["action"]
        values = args.get("values") or {}
        if running:                                                            # p.54: during the run only a coming step can be deleted
            order = _lyo_sorted_steps(m)
            current = (s["process.step"] or {}).get("index")
            if i >= len(steps) or current is None or order.index(steps[i]) <= current: raise _LyoRefused("METHOD_ACTIVE")
            del steps[i]
            new = _lyo_sorted_steps(m)
            s["cycle.recipe"] = {"pre_freeze_s": 0.0, "primary_drying_s": sum(x["term_min"] * 60.0 for x in new if x["phase"] == "Primary drying"),
                                 "secondary_drying_s": sum(x["term_min"] * 60.0 for x in new if x["phase"] == "Secondary drying")}
            return {"steps": len(steps)}
        self._check_values(values, LYOVAPOR_STEP_FIELDS)
        if action == "add":
            if len(steps) >= LYOVAPOR_MAX_STEPS: raise _LyoRefused("LIMIT")
            if i > len(steps): raise _LyoRefused("INVALID_ARGUMENT")
            steps.insert(i, dict(dict(steps[i - 1] if i > 0 and steps else _LYO_STEP_DEFAULT), **values))
            s["methods.cursor"] = {"method_id": m["id"], "step_index": i}
        else:
            if i >= len(steps): raise _LyoRefused("INVALID_ARGUMENT")
            if action == "delete":
                if len(steps) == 1: raise _LyoRefused("LIMIT")
                del steps[i]
                if s["methods.cursor"] and s["methods.cursor"]["method_id"] == m["id"]:
                    s["methods.cursor"]["step_index"] = min(i, len(steps) - 1)
            else:
                steps[i].update(values)
        return {"steps": len(steps)}

    def _lyo_methods_phase(self, c, args):
        self._on(c)
        m = self._editable(c, args["method_id"])
        values = copy.deepcopy(args["values"])
        self._check_values(values, LYOVAPOR_PHASE_FIELDS[args["phase"]])
        if "tdt" in values:
            self._check_values(values["tdt"], LYOVAPOR_TDT_FIELDS)
            values["tdt"] = dict(m["phases"][args["phase"]]["tdt"], **values["tdt"])
        m["phases"][args["phase"]].update(values)
        return {"phase": args["phase"]}

    def _lyo_favourites_add(self, c, args):
        self._on(c)
        if args["target"] not in c.state["favourites.list"]:
            if len(c.state["favourites.list"]) >= 20: raise _LyoRefused("LIMIT")
            c.state["favourites.list"] = c.state["favourites.list"] + [args["target"]]
        return {"targets": list(c.state["favourites.list"])}

    def _lyo_favourites_delete(self, c, args):
        self._on(c)
        if args["target"] not in c.state["favourites.list"]: raise _LyoRefused("INVALID_ARGUMENT")
        c.state["favourites.list"] = [x for x in c.state["favourites.list"] if x != args["target"]]
        return {"targets": list(c.state["favourites.list"])}

    def _lyo_methods_select(self, c, args):
        self._on(c)
        mid = args["method_id"]
        if mid is not None and _lyo_method(c.state, mid) is None: raise _LyoRefused("INVALID_ARGUMENT")
        k = args.get("step_index", 0)
        if mid is not None and k >= len(_lyo_method(c.state, mid)["steps"]): raise _LyoRefused("LIMIT")
        c.state["methods.cursor"] = None if mid is None else {"method_id": mid, "step_index": k}
        return {"cursor": copy.deepcopy(c.state["methods.cursor"])}

    def _lyo_messages_acknowledge(self, c, args):
        n = len(c.state["messages.notifications"])
        c.state["messages.notifications"] = []
        return {"cleared": n}

    def _lyo_settings_configure(self, c, args):
        self._on(c)
        before = c.state["settings.values"]["pressure_sensor"]
        c.state["settings.values"].update(args["settings"])
        if c.state["settings.values"]["pressure_sensor"] != "Standard" and before == "Standard":   # the kit's sensor is not fitted (p.48-49)
            _lyo_message(c.state, "6283", "E", "6283 Pressure sensor is disconnected.", self._host.time_s)
        return {"settings": copy.deepcopy(c.state["settings.values"])}

    def _lyo_service_vacuum_test(self, c, args):
        self._on(c)
        s = c.state
        test = s["service.state"]["vacuum_test"]
        if args["action"] == "stop":
            if not test["running"]: raise _LyoRefused("NOT_RUNNING")
            test.update(running=False, result="aborted")
            s["system.status"] = "Unload / Load"
            self._host._lyo_test_restore(c)
            return {"vacuum_test": copy.deepcopy(test)}
        self._idle(c)
        self._no_fault(c)
        if s["system.status"] != "Unload / Load": raise _LyoRefused("NOT_CONDITIONED")
        if s["sample.loaded"] or c.source is None: raise _LyoRefused("TEST_FAILED")
        test.update({k: float(args[k]) for k in ("target_pa", "time_s") if k in args})
        test.update(running=True, result=None, elapsed_s=0.0)
        s["system.status"] = "Vacuum Test"
        self._host._lyo_test_targets(c, test["target_pa"])
        return {"vacuum_test": copy.deepcopy(test)}

    def _lyo_service_leak_test(self, c, args):
        self._on(c)
        s = c.state
        test = s["service.state"]["leak_test"]
        if args["action"] == "stop":
            if not test["running"]: raise _LyoRefused("NOT_RUNNING")
            test.update(running=False, result="aborted")
            s["system.status"] = "Unload / Load"
            self._host._lyo_test_restore(c)
            return {"leak_test": copy.deepcopy(test)}
        self._idle(c)
        self._no_fault(c)
        if s["system.status"] != "Unload / Load": raise _LyoRefused("NOT_CONDITIONED")
        if s["sample.loaded"] or c.source is None: raise _LyoRefused("TEST_FAILED")
        for k in ("condenser_pa", "shelf_temperature_k", "scope", "shelf_heating", "volume_l"):
            if k in args:
                test[k] = args[k]
        test.update(running=True, result=None, elapsed_s=0.0, rate=None, start_pa=s["vacuum.indicated_pressure_pa"])
        s["system.status"] = "Leak Test"
        self._host._lyo_test_targets(c, test["condenser_pa"], test["shelf_temperature_k"] if test["shelf_heating"] else None)
        return {"leak_test": copy.deepcopy(test)}


_g03_lyovapor_export_base = export_lyovapor_contract_metadata


def export_lyovapor_contract_metadata():
    """CHEM-025 metadata plus the control panel: device-layer slots (in state_slots), device_capabilities, panel_capabilities (when the panel
    spec exists), parameters and limitations.  The CHEM-025 'capabilities' keep their schemas."""
    meta = _g03_lyovapor_export_base()
    t = meta["asset_types"][LYOVAPOR_ASSET_TYPE_ID]
    t["asset_version"] = LYOVAPOR_ASSET_VERSION
    dev = {}
    for name, (inputs, optional, outputs, example, desc, effects, components, kind) in LYOVAPOR_DEVICE_ARGS.items():
        dev[name] = g03_device_capability(name, kind, desc, inputs, outputs, example, components, effects, optional=optional, binding=_LYO_BINDING,
                                          completion="同步提交；定时状态（调理、通气、关机、除霜、测试）与冻干由宿主step推进",
                                          visibility="evaluator" if name in LYOVAPOR_EVALUATOR_CAPS else "agent")
    t["device_capabilities"] = dev
    t["panel_capabilities"] = {}
    if (LYOVAPOR_ASSET_DIR / "panel_ui.json").is_file():
        spec = _lyovapor_panel()["spec"]
        called = _g03_engine().spec_capabilities(spec)
        effects = list(dict.fromkeys(e["state_id"] for c in called for e in (t["capabilities"].get(c) or dev.get(c) or {}).get("state_effects", [])
                                     if e["timing"] == "immediate"))
        t["panel_capabilities"] = g03_panel_capabilities(spec, _LYO_BINDING, effects)
    t["parameters"].update({
        "conditioning_time_s": {"value": LYOVAPOR_CONDITIONING_S, "unit": "s", "evidence": "documented", "source": "p.40: approx. 30 min"},
        "shutdown_time_s": {"value": LYOVAPOR_SHUTDOWN_S, "unit": "s", "evidence": "documented", "source": "p.54: 50 min"},
        "aerate_time_s": {"value": LYOVAPOR_AERATE_S, "unit": "s", "evidence": "engineering_assumption", "source": "venting time not given"},
        "defrost_time_s": {"value": [LYOVAPOR_DEFROST_S, LYOVAPOR_DEFROST_MAX_S], "unit": "s", "evidence": "documented", "source": "p.56"},
        "leak_rate_limit_mbar_l_h": {"value": LYOVAPOR_LEAK_LIMIT, "unit": "mbar*L/h", "evidence": "documented", "source": "p.80"},
        "graph_interval_s": {"value": LYOVAPOR_GRAPH_S, "unit": "s", "evidence": "engineering_assumption", "source": "live graph sampling not given"},
        "device_clock_epoch": {"value": G03_DEVICE_EPOCH.isoformat(), "unit": "1", "evidence": "engineering_assumption"},
    })
    meta["limitations"] = list(meta["limitations"]) + LYOVAPOR_G03_LIMITATIONS
    return meta


LYOVAPOR_G03_LIMITATIONS = [
    "Run ownership (lead rule 2026-10-19, a simulation rule): the process started by one agent (conditioning up to shut down, drying, tests, "
    "defrost) belongs to it; another agent cannot stop, shut down or change it (process commands, touch-screen Shut down, set points, method "
    "activation, settings, tests, defrost) on the panel or the direct path (ACCESS_DENIED, state unchanged, message box with our text).  The On/Off "
    "master switch (power.set) is physical (lead clarification 10-19): anyone may switch it off, the process ends as on a power failure (6042 "
    "at power-up).  Errors of p.85-88 "
    "(except 6670, stoppering absent) are injected by the evaluator (fault.inject / fault.clear; faults.active lists the uncleared ones); the "
    "message type per code is assumed: E (defects, safety shutdowns) stops the process (Standby) and refuses starts until cleared, W only notifies "
    "(6278 fails a running leak test).",
    "Control-panel layer (operation manual 11594477, L-250 Pro with the advanced vacuum control unit and the optional hot-gas defrost): statuses, "
    "conditioning, manual and method freeze-drying, sample protection and UNPROT, skip, methods, favourites, messages, live graph, settings, vacuum "
    "and leak tests, defrost and shut down follow the manual as a minimal device model, not BUCHI firmware.",
    "Manual drying runs as the primary-drying phase of the CHEM-025 cycle (up to 7 days); a method runs its primary steps then its secondary steps "
    "(steps sorted by phase) and holds at the Hold set points; pressures and temperatures stay host boundaries (the controller only sets targets).",
    "Timed statuses: conditioning 30 min and shutting down 50 min (manual), aerating 60 s (assumption), defrost 60 min (+/- 30, at most 300). "
    "The melted ice is moved to the drain count only when the CHEM-025 defrost conditions hold (warm condenser, no sample, source isolated).",
    "Not simulated: ultimate vacuum control unit (Vacuum pump ON/OFF), pressure difference test kit (selecting its Inficon Porter CDG020 D sensor "
    "in Settings reports 6283 'Pressure sensor is disconnected', absent hardware), stoppering hardware, power-failure recovery (6042 is reported, "
    "the process is aborted), BUCHI Cloud / LAN, SD card logging, Frontloader.",
    "Method settings acting on the controller: Shelf load temp. is the shelf set point of the activated method while the instrument waits in "
    "Unload / Load; Safety temp. Time switches the primary-drying temperature protection (collapse + safety temperature, action and message) off "
    "for that time before the end of primary drying. Vacuum and leak tests set the vacuum set point (ice condenser line, at least 10 Pa) and, with "
    "Drying shelf heating on, the shelf set point; the previous set points return when the test ends or stops. Pressures stay host boundaries.",
]
# <<< G03 CHEM-025 Lyovapor touch screen


# >>> G03 CHEM-190 PURELAB flex 3 handset
# Device layer of the PURELAB flex 3 display handset (operator manual references/manuals/elga_purelab_flex3/
# operating_manual__purelab_flex3_operator_manual.pdf, MANU41742 Ver 1, 'p.N' = PDF page): first start (language, fit consumables),
# manual / continuous / Auto Volume / Profile Dispense, reservoir refill (automatic at the refill set point, initiated, timed), intermittent
# recirculation, ECO mode, Main Menu settings, reminders (Purification Pack, UV lamp, sanitization), sanitization, volumetric calibration,
# water temperature and purity alarm points, Infobar, and the front panel (graphic display, five keys).  The CHEM-190 commands keep their
# meaning; the dispense volume error (evaluator only) is 0 by default.
PURELAB_ASSET_VERSION = "1.1.2"
PURELAB_ASSET_DIR = G03_ASSET_ROOT / "elga_purelab_flex3"
PURELAB_MANUAL = "references/manuals/elga_purelab_flex3/operating_manual__purelab_flex3_operator_manual.pdf"
PURELAB_LANGUAGES = ("English", "Deutsch", "Français", "Español", "Italiano", "中文", "日本語")   # the list is not printed (assumption)
PURELAB_AUTO_VOLUMES_L = tuple([x / 1000 for x in range(50, 1000, 50)] + [x / 10 for x in range(10, 51, 5)])   # 50 mL..5 L (assumption)
PURELAB_CONTINUOUS_M3 = 10e-3            # continuous dispense stops after 10 L at the latest (assumption: the daily usage figure)
PURELAB_REFILL_ECO_M3, PURELAB_REFILL_M3 = 2e-3, 6e-3   # p.15: automatic refill at 2 L with ECO mode, 6 L without
PURELAB_RECIRC_ON_S, PURELAB_RECIRC_PERIOD_S = 600.0, 7200.0   # p.15: intermittent recirculation 10 min every 2 hours (no ECO)
PURELAB_REMINDERS_S = {"pack": 182 * 86400.0, "uv": 365 * 86400.0, "sanitization": 182 * 86400.0}   # p.14: pack 6 months, UV 12-18 months; sanitization interval not given (assumption)
PURELAB_SANITIZATION_S = 20 * 60.0       # sanitization flush (assumption; the handset instructions are not printed)
PURELAB_CAL_NOMINAL_M3 = 1e-3            # calibration dispense volume (assumption; the procedure is not printed)
PURELAB_SETTINGS = [
    dict(key="language", type="enum", options=list(PURELAB_LANGUAGES), default="English", page="p.9"),
    dict(key="dispense_mode", type="enum", options=["Volumetric Dispense", "Profile Dispense"], default="Volumetric Dispense", page="p.12"),
    dict(key="eco_start_s", type="number", min=0, max=86400 - 600, default=18 * 3600.0, page="p.5"),
    dict(key="eco_end_s", type="number", min=0, max=86400 - 600, default=9 * 3600.0, page="p.5"),
    dict(key="refill_setpoint_m3", type="number", min=1e-3, max=6e-3, nullable=True, default=None, page="p.15"),
    dict(key="auto_refill_s", type="number", min=0, max=86400 - 600, nullable=True, default=None, page="p.15"),
    dict(key="temp_alarm_k", type="number", min=278.15, max=313.15, default=308.15, page="p.13"),
    dict(key="purity_alarm_ohm_m", type="number", min=1e4, max=182000.0, default=1e5, page="p.13"),
]


def _pure_setting_schema(f):
    if f["type"] == "enum":
        return _g03_str(f["options"])
    return _g03_num("1" if "ohm" not in f["key"] else "ohm*m", f["min"], f["max"], nullable=f.get("nullable", False))


_PURE_OBJ = {"type": "object"}
PURELAB_DEVICE_SLOTS = {
    "unit.state": (_PURE_OBJ, "手柄与机组状态：已调试、ECO时段、补水、间歇循环、报警、Infobar文字（p.5, p.13, p.15）"),
    "settings.values": (_PURE_OBJ, "主菜单设置：语言、定量方式、ECO时段、补水设定点、定时补水、温度与纯度报警点（p.5, p.12-15）"),
    "dispense.state": (_PURE_OBJ, "取水：Auto Volume体积、三个Profile体积、录制中的Profile、校准系数（p.10-12, p.5）"),
    "reminders.state": (_PURE_OBJ, "更换/消毒提醒：纯化柱、UV灯、消毒，到期标志（p.13-14）"),
    "sanitization.state": (_PURE_OBJ, "消毒流程步骤（p.13）"),
}
for _pure_sid, (_pure_schema, _pure_meaning) in PURELAB_DEVICE_SLOTS.items():
    PURELAB_STATE_SLOTS[_pure_sid] = _pure_slot("1", "derived", _pure_schema, _pure_meaning)
PURELAB_STATE_SLOTS["dispense.error"] = _pure_slot("1", "setpoint", _PURE_OBJ, "评测者：取水体积相对误差（默认0）", "evaluator")
PURELAB_DEVICE_ARGS = {   # name: (inputs, optional, outputs, example, description, effects, components, kind)
    "unit.commission": ({"language": _g03_str(PURELAB_LANGUAGES)}, (), {"commissioned": {"type": "boolean"}}, {"language": "English"},
                        "首次开机：选语言并确认已装复合排气过滤器与纯化柱（p.9）", ["unit.state", "settings.values"], ["display_handset"], "configuration"),
    "settings.configure": ({"settings": _g03_obj({f["key"]: _pure_setting_schema(f) for f in PURELAB_SETTINGS}, optional=[f["key"] for f in PURELAB_SETTINGS])}, (),
                           {"settings": _PURE_OBJ}, {"settings": {"dispense_mode": "Profile Dispense"}}, "主菜单设置（p.5, p.12-15）",
                           ["settings.values", "unit.state"], ["display_handset"], "configuration"),
    "dispense.manual": ({"duration_s": _g03_num("s", 0.05, 600)}, (), {"volume_m3": _g03_num("m^3")}, {"duration_s": 2.0},
                        "按住Dispense：按住期间取水（体积=流量×按住时间，宿主step推进）；Profile录制中计入录制体积（p.10, p.12）",
                        ["water.dispensing", "water.dispense_status", "water.target_volume_m3", "water.flow_rate_m3_s", "dispense.state"], ["dispense_controls"],
                        "device_command"),
    "dispense.continuous": ({}, (), {"volume_m3": _g03_num("m^3")}, {}, "Dispense+Accept：连续取水，再按Dispense停止（p.10）",
                            ["water.dispensing", "water.dispense_status", "water.target_volume_m3", "water.flow_rate_m3_s"], ["dispense_controls"], "device_command"),
    "dispense.set_auto_volume": ({"volume_m3": _g03_num("m^3", 5e-5, 5e-3)}, (), {"volume_m3": _g03_num("m^3")}, {"volume_m3": 5e-4},
                                 "Auto Volume画面用上下键选体积（p.10）", ["dispense.state"], ["dispense_controls"], "configuration"),
    "dispense.auto": ({"profile": _g03_num("1", 1, 3, integer=True, nullable=True)}, (), {"volume_m3": _g03_num("m^3")}, {"profile": None},
                      "Auto Volume或Profile的Dispense：按校准系数取所选体积（p.10, p.12）",
                      ["water.dispensing", "water.dispense_status", "water.target_volume_m3", "water.flow_rate_m3_s"], ["dispense_controls"], "device_command"),
    "dispense.record": ({"action": _g03_str(["start", "stop"]), "profile": _g03_num("1", 1, 3, integer=True)}, ("profile",), {"dispense": _PURE_OBJ},
                        {"action": "start", "profile": 1}, "Profile Dispense录制：选Volume 1/2/3，用Dispense取水，Accept结束录制（p.12）", ["dispense.state"],
                        ["dispense_controls"], "configuration"),
    "refill.start": ({}, (), {"refilling": {"type": "boolean"}}, {}, "按住Auto Volume 2 s：开始储水罐补水（p.15）", ["unit.state", "water.mode"], ["dispense_controls"],
                     "device_command"),
    "reminders.reset": ({"which": _g03_str(["uv", "sanitization"])}, (), {"reminders": _PURE_OBJ}, {"which": "uv"},
                        "复位UV灯或消毒提醒（纯化柱提醒经consumables.reset复位）（p.13）", ["reminders.state"], ["display_handset"], "configuration"),
    "sanitization.control": ({"action": _g03_str(["start", "next", "cancel"])}, (), {"sanitization": _PURE_OBJ}, {"action": "start"},
                             "接受消毒并按手柄提示执行：装消毒包→冲洗→装回纯化柱（p.13）", ["sanitization.state", "water.mode", "reminders.state"],
                             ["display_handset", "purification_pack"], "device_command"),
    "calibration.control": ({"action": _g03_str(["start", "enter", "cancel"]), "measured_m3": _g03_num("m^3", 1e-4, 2e-3)}, ("measured_m3",),
                            {"calibration": _PURE_OBJ}, {"action": "start"},
                            "定量校准：取标称1 L，输入量筒读数，保存修正系数（p.5，每10 L或7天）", ["dispense.state", "water.dispensing", "water.target_volume_m3"],
                            ["dispense_controls"], "configuration"),
    "dispense.configure_error": ({"gain": _g03_num("1", -0.2, 0.2)}, (), {"error": _PURE_OBJ}, {"gain": 0.0}, "评测者：取水体积相对误差，默认0（理想）",
                                 ["dispense.error"], ["dispense_controls"], "configuration"),
}
PURELAB_EVALUATOR_CAPS = ("dispense.configure_error",)
_PURE_BINDING = "basic40_runtime_vacuum.PurelabSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"
PURELAB_DEVICE_ERRORS = {"NOT_COMMISSIONED": "The unit is in commissioning mode", "NOT_READY": "Not possible now", "NO_PROFILE": "The profile is not recorded",
                         "NOT_RUNNING": "No procedure is running", "NO_SUPPLY": "No water supply configured"}


PURELAB_RUN_TOUCHES = {"water.start": ("dispense",), "water.stop": ("dispense",), "water.set_volume": ("dispense",), "water.set_flow_rate": ("dispense",),
                       "water.set_mode": ("dispense", "sanitization"), "dispense.manual": ("dispense",), "dispense.continuous": ("dispense",),
                       "dispense.auto": ("dispense",), "dispense.record": ("dispense",), "calibration.control": ("dispense", "calibration"),
                       "sanitization.control": ("sanitization",)}


def _pure_runs(s):
    """The runs of the unit: a dispense, a sanitization and a volumetric calibration (each owned by the agent that started it)."""
    return {"dispense": bool(s["water.dispensing"]), "sanitization": s["sanitization.state"]["step"] is not None,
            "calibration": s["dispense.state"].get("calibration") is not None}


def _pure_touches(cap, args):
    """power.set is the 24 V power supply (hard: physical, never guarded; the unit reacts as to a power loss)."""
    return PURELAB_RUN_TOUCHES.get(cap, ())


class _PureRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _pure_time_of_day(time_s):
    return float(time_s) % 86400.0


def _pure_eco(settings, time_s):
    a, b = settings["eco_start_s"], settings["eco_end_s"]
    if a == b:
        return False                                                         # 00:00 and 00:00 (equal times) disable ECO mode (p.5)
    t = _pure_time_of_day(time_s)
    return a <= t < b if a < b else (t >= a or t < b)


def _pure_infobar(inst, time_s):
    """The scrolling Infobar text (p.6): the most important status first."""
    s = inst.state
    u, r = s["unit.state"], s["reminders.state"]
    if not u["commissioned"]:
        return "Commissioning"
    parts = []
    if u["alarm_temperature"]:
        parts.append("High Temperature Water Alarm")
    if u["alarm_purity"]:
        parts.append("Dispense Water Purity Alarm")
    for key, text in (("pack", "Purification Pack change reminder"), ("sanitization", "Sanitization reminder"), ("uv", "UV change reminder")):
        if r[key + "_due"]:
            parts.append(text)
    if s["sanitization.state"]["step"]:
        parts.append("Sanitization")
    if s["water.dispensing"]:
        parts.append("Dispensing")
    elif u["auto_mode"] == "refill" or s["water.mode"] == "recirculate" and u["auto_mode"] is None:
        parts.append("Reservoir refill" if u["auto_mode"] == "refill" else "Recirculating")
    elif u["auto_mode"] == "intermittent":
        parts.append("Recirculating")
    if u["eco"]:
        parts.append("ECO mode")
    return " - ".join(parts) if parts else "Ready"


def _pure_scroll(text, time_s, width=21, rate=2.0):
    """The Infobar window shown at a time: text longer than the line scrolls left (2 characters per second, assumption)."""
    if len(text) <= width:
        return text
    loop = text + "   "
    k = int(math.floor(time_s * rate + 1e-9)) % len(loop)
    return (loop + loop)[k:k + width]


_g03_PurelabHostBase = PurelabHost
_g03_PurelabSessionBase = PurelabSession
_PURELAB_PANEL = {}


def _purelab_panel():
    if not _PURELAB_PANEL:
        engine = _g03_engine()
        caps = set(PURELAB_ARGUMENTS) | set(PURELAB_DEVICE_ARGS)
        spec = engine.load_spec(PURELAB_ASSET_DIR / "panel_ui.json", capabilities=caps)
        _PURELAB_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _PURELAB_PANEL


class PurelabHost(_g03_PurelabHostBase):
    """CHEM-190 host with the display handset: device state (slots above), automatic refill and recirculation, reminders, the front panel."""

    PANEL_PRINCIPAL = "__purelab_panel__"

    def __init__(self, *, scene_adapter=None):
        super().__init__(scene_adapter=scene_adapter)
        self._panels = {}
        self.panel_capture_dir = None
        self._principals[self.PANEL_PRINCIPAL] = ("agent", None)
        self._logs[self.PANEL_PRINCIPAL] = []
        self._panel_session = PurelabSession(self, self.PANEL_PRINCIPAL)
        self._panel_caller = None                                           # the session whose panel gesture is running (its principal acts)

    def create_instance(self, instance_id, asset_type_id=PURELAB_ASSET_TYPE_ID):
        with self._lock:
            super().create_instance(instance_id, asset_type_id)
            inst = self._instances[instance_id]
            settings = {f["key"]: f["default"] for f in PURELAB_SETTINGS}
            inst.state.update({
                "unit.state": {"commissioned": False, "eco": _pure_eco(settings, self.time_s), "auto_mode": None, "alarm_temperature": False,
                               "alarm_purity": False, "infobar": "Commissioning"},
                "settings.values": settings,
                "dispense.state": {"auto_volume_m3": 5e-4, "profiles": [None, None, None], "recording": None, "calibration_factor": 1.0,
                                   "calibration": None, "calibrated_at": None, "last_manual_m3": 0.0},
                "reminders.state": {"pack_since_s": self.time_s, "uv_since_s": self.time_s, "sanitization_since_s": self.time_s, "pack_due": False,
                                    "uv_due": False, "sanitization_due": False},
                "sanitization.state": {"step": None, "until_s": None},
                "dispense.error": {"gain": 0.0}})
            inst.g03 = {"power_epoch": 0, "last_refill_day": None, "batch": None}   # batch: the dispense in progress (requested / true volume)
            if (PURELAB_ASSET_DIR / "panel_ui.json").is_file():
                self._panels[instance_id] = _purelab_panel()["engine"].PanelUI(_purelab_panel()["spec"], _G03PanelHost(
                    lambda: self._panel_values(instance_id), lambda cap, args: self._panel_call(instance_id, cap, args), lambda: self.time_s))

    def panel(self, instance_id):
        return self._panels[instance_id]

    def _panel_values(self, instance_id):
        inst = self._instances[instance_id]
        out = {sid: copy.deepcopy(v) for sid, v in inst.state.items() if PURELAB_STATE_SLOTS[sid]["visibility"] == "agent"}
        out.update({"host.powered": inst.state["power.enabled"], "host.power_epoch": inst.g03["power_epoch"], "host.clock": _g03_clock(self.time_s),
                    "host.infobar": _pure_scroll(inst.state["unit.state"]["infobar"], self.time_s)})
        return out

    def _panel_call(self, instance_id, capability_id, arguments):
        """A command of the panel firmware runs as the session whose gesture triggered it; without a gesture as the panel's own principal."""
        with self._lock:
            return (self._panel_caller or self._panel_session)._apply(self._instances[instance_id], capability_id, arguments, None)

    @staticmethod
    def _refresh(inst):
        """CHEM-190 refresh; the device's indicated volume is its metered (requested) volume: the water that really flows carries the
        calibration and the evaluator error, the indication does not (the unit has no flow meter, it meters by its own model)."""
        _g03_PurelabHostBase._refresh(inst)
        b = (getattr(inst, "g03", None) or {}).get("batch")
        if b and b["true_m3"] > 0:
            inst.state["water.indicated_dispensed_volume_m3"] = inst.state["water.dispensed_volume_m3"] * b["requested_m3"] / b["true_m3"]

    def _advance(self, inst, dt):
        g = getattr(inst, "g03", None)
        s = inst.state
        if g is None:
            return super()._advance(inst, dt)
        u = s["unit.state"]
        b = g.get("batch")
        if s["water.dispensing"] and b:                                     # the water flows until the true volume of the metered dispense
            set_volume, s["water.target_volume_m3"] = s["water.target_volume_m3"], b["true_m3"]
            try:
                super()._advance(inst, dt)
            finally:
                s["water.target_volume_m3"] = set_volume
        else:
            super()._advance(inst, dt)
        now = self.time_s + dt
        st = s["settings.values"]
        u["eco"] = _pure_eco(st, now)
        san = s["sanitization.state"]
        if san["step"] == "running" and now >= san["until_s"] - 1e-9:
            san.update(step="rinse_done", until_s=None)
            if s["water.mode"] == "flush":
                s["water.mode"] = "idle"
        if not s["power.enabled"] or not u["commissioned"] or inst.supply is None or not s["water.qualified_feed"] or san["step"]:
            if u["auto_mode"] and not san["step"]:
                u["auto_mode"] = None
        else:
            self._pure_auto_modes(inst, now)
        for key in ("pack", "uv", "sanitization"):
            s["reminders.state"][key + "_due"] = now - s["reminders.state"][key + "_since_s"] >= PURELAB_REMINDERS_S[key] - 1e-9
        t, rho = s["water.temperature_k"], s["water.resistivity_ohm_m"]
        u["alarm_temperature"] = t is not None and t > st["temp_alarm_k"]
        u["alarm_purity"] = rho is not None and rho < st["purity_alarm_ohm_m"]
        u["infobar"] = _pure_infobar(inst, now)

    def _pure_auto_modes(self, inst, now):
        """Automatic refill below the refill set point (2 L with ECO, 6 L without, or the menu value), timed auto refill, and the intermittent
        recirculation of 10 min every 2 h when ECO mode is off (p.15).  Only modes the unit set itself are switched back."""
        s, g = inst.state, inst.g03
        u, st, sup = s["unit.state"], s["settings.values"], inst.supply
        if s["water.dispensing"] or (s["water.mode"] != "idle" and u["auto_mode"] is None):
            return
        can_refill = bool(sup["ro_product_rate_m3_s"]) and bool(sup["recirculation_flow_m3_s"]) and (s["water.feed_volume_m3"] or 0) > 0
        full = s["water.buffer_volume_m3"] >= sup["buffer_capacity_m3"] - 1e-9
        setpoint = st["refill_setpoint_m3"] or (PURELAB_REFILL_ECO_M3 if u["eco"] else PURELAB_REFILL_M3)
        day = int(now // 86400)
        if st["auto_refill_s"] is not None and _pure_time_of_day(now) >= st["auto_refill_s"] and g["last_refill_day"] != day:
            g["last_refill_day"] = day
            if can_refill and not full:
                u["auto_mode"] = "refill"
        if u["auto_mode"] == "refill":
            if full or not can_refill:
                u["auto_mode"] = None
                s["water.mode"] = "idle"
            else:
                s["water.mode"] = "recirculate"
            return
        if can_refill and s["water.buffer_volume_m3"] < setpoint - 1e-12:
            u["auto_mode"], s["water.mode"] = "refill", "recirculate"
            return
        intermittent = (not u["eco"]) and bool(sup["recirculation_flow_m3_s"]) and s["water.buffer_volume_m3"] > 0 and \
            (now % PURELAB_RECIRC_PERIOD_S) < PURELAB_RECIRC_ON_S
        if intermittent:
            u["auto_mode"], s["water.mode"] = "intermittent", "recirculate"
        elif u["auto_mode"] == "intermittent":
            u["auto_mode"], s["water.mode"] = None, "idle"


class PurelabSession(_g03_PurelabSessionBase):
    """CHEM-190 session plus the handset device commands and the panel.* commands."""

    @property
    def _host(self):
        return self._PurelabSession__host

    @property
    def _visibility(self):
        return self._host._principals[self._PurelabSession__principal_id][0]

    def describe(self, *, asset_type_id=None, instance_id=None):
        response = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if response.get("error") is None and "capabilities" in response:
            evaluator = self._visibility in ("debug", "evaluator")
            meta = export_purelab_contract_metadata()["asset_types"][PURELAB_ASSET_TYPE_ID]
            slots = response["state_slots"]
            for group in ("device_capabilities", "panel_capabilities"):
                for name, cap in meta[group].items():
                    if cap["visibility"] == "agent" or evaluator:
                        cap = copy.deepcopy(cap)
                        cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in slots]
                        cap["observes"] = [sid for sid in cap["observes"] if sid in slots]
                        response["capabilities"][name] = cap
            response["asset_version"] = PURELAB_ASSET_VERSION
        return response

    def _apply(self, inst, cap, args, request_id):
        """Run ownership (g03_owner_refuses) before the command; a run the command starts belongs to this session's principal.  The dispense
        arm (arm.set_height) is moved by hand and not guarded."""
        g, me, vis = getattr(inst, "g03", None), self._PurelabSession__principal_id, self._visibility
        if g is not None and g03_owner_refuses(g, _pure_runs(inst.state), _pure_touches(cap, args), me, vis, PurelabHost.PANEL_PRINCIPAL):
            return self._error(inst.instance_id, "ACCESS_DENIED", request_id)
        before = _pure_runs(inst.state) if g is not None else None
        response = self._pure_apply(inst, cap, args, request_id)
        live = self._host._instances.get(inst.instance_id)
        if response["status"] == "succeeded" and getattr(live, "g03", None) is not None:
            g03_owner_note(live.g03, before, _pure_runs(live.state), me, vis, PurelabHost.PANEL_PRINCIPAL)
        return response

    def _pure_apply(self, inst, cap, args, request_id):
        if cap in PURELAB_DEVICE_ARGS or cap.startswith("panel."):
            return self._device_apply(inst, cap, args, request_id)
        if cap in ("consumables.reset", "water.start", "water.set_mode", "arm.set_height") and getattr(inst, "g03", None) is not None \
                and inst.state["unit.state"]["auto_mode"]:
            inst = copy.deepcopy(inst)                                       # the unit's own refill / recirculation gives way to a command
            inst.state["water.mode"], inst.state["unit.state"]["auto_mode"] = "idle", None
        before = inst.state["power.enabled"]
        response = super()._apply(inst, cap, args, request_id)
        live = self._host._instances[inst.instance_id]
        if response["status"] == "succeeded" and getattr(live, "g03", None) is not None:
            if cap == "power.set" and args["enabled"] != before:
                live.g03["power_epoch"] += 1
                live.state["unit.state"]["auto_mode"] = None
                live.state["dispense.state"]["recording"] = None
            if cap == "consumables.reset":                                   # the Purification Pack reminder starts again with the pack
                live.state["reminders.state"].update(pack_since_s=self._host.time_s, pack_due=False)
            if cap == "water.set_mode":
                live.state["unit.state"]["auto_mode"] = None
            if cap == "water.start":                                         # a CHEM-190 dispense is metered like the handset's volumetric dispense
                v = live.state["water.target_volume_m3"]
                live.g03["batch"] = {"requested_m3": v, "true_m3": v * live.state["dispense.state"]["calibration_factor"] *
                                     (1.0 + live.state["dispense.error"]["gain"])}
                self._host._refresh(live)
            live.state["unit.state"]["infobar"] = _pure_infobar(live, self._host.time_s)
        return response

    def _device_apply(self, inst, cap, args, request_id):
        host, rid = self._host, request_id
        if cap.startswith("panel."):
            meta = _purelab_panel()["caps"] if (PURELAB_ASSET_DIR / "panel_ui.json").is_file() else {}
            if cap not in meta: return self._error(inst.instance_id, "UNKNOWN_CAPABILITY", rid)
            if meta[cap]["visibility"] != "agent" and self._visibility not in ("debug", "evaluator"): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            if _g03_check(meta[cap]["input_schema"], args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
            if self._PurelabSession__principal_id == PurelabHost.PANEL_PRINCIPAL or inst.instance_id not in host._panels:
                return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            previous, host._panel_caller = host._panel_caller, self                # the commands of this gesture act as this session
            try:
                result, code = g03_panel_gesture(host._panels[inst.instance_id], cap, args, instance_id=inst.instance_id, time_s=host.time_s,
                                                 capture_dir=host.panel_capture_dir)
            finally:
                host._panel_caller = previous
            if code: return self._error(inst.instance_id, code, rid)
            return self._envelope(inst.instance_id, rid, result=result)
        inputs, optional = PURELAB_DEVICE_ARGS[cap][0], PURELAB_DEVICE_ARGS[cap][1]
        if cap in PURELAB_EVALUATOR_CAPS and self._visibility not in ("debug", "evaluator"): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
        if _g03_check(_g03_obj(inputs, optional), args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
        candidate = copy.deepcopy(inst)
        try:
            result = getattr(self, "_pure_" + cap.replace(".", "_"))(candidate, args)
        except _PureRefused as exc:
            if exc.code in ("INVALID_ARGUMENT", "PRECONDITION_FAILED", "BUSY", "ACCESS_DENIED"):
                return self._error(inst.instance_id, exc.code, rid)
            return self._envelope(inst.instance_id, rid, status="rejected", error={"code": exc.code, "message": PURELAB_DEVICE_ERRORS[exc.code],
                                                                                   "retryable": False, "side_effects": "none", "known_progress": None})
        candidate.state["unit.state"]["infobar"] = _pure_infobar(candidate, host.time_s)
        host._refresh(candidate)
        try:
            host._emit(candidate)
        except Exception:
            return self._error(inst.instance_id, "DEVICE_ERROR", rid, "failed")
        candidate.revision += 1
        host._instances[inst.instance_id] = candidate
        return self._envelope(inst.instance_id, rid, result=result)

    # ---- device commands
    @staticmethod
    def _on(c, commissioned=True):
        s = c.state
        if not s["power.enabled"]: raise _PureRefused("NOT_READY")
        if commissioned and not s["unit.state"]["commissioned"]: raise _PureRefused("NOT_COMMISSIONED")

    def _start_dispense(self, c, volume_m3, factor=1.0):
        """Start a CHEM-190 dispense of a requested volume, metered with the calibration factor (volumetric dispenses); the evaluator's volume
        error acts on the water that flows (0 by default).  The set volume and the indication stay the requested volume."""
        s = c.state
        if s["water.dispensing"] or s["sanitization.state"]["step"] == "running": raise _PureRefused("BUSY")
        if s["water.mode"] != "idle":
            if s["unit.state"]["auto_mode"] is None: raise _PureRefused("BUSY")
            s["water.mode"], s["unit.state"]["auto_mode"] = "idle", None     # dispensing has priority over the unit's own refill / recirculation
        flow = s["water.flow_rate_m3_s"] or PURELAB_MAX_DISPENSE_M3_S
        target = volume_m3 * factor * (1.0 + s["dispense.error"]["gain"])
        if not (s["consumables.installed"] and c.supply and s["water.qualified_feed"] and s["water.buffer_volume_m3"] and s["water.buffer_volume_m3"] > 0
                and c.receiver and s["water.receiver_volume_m3"] < c.receiver["capacity_m3"]):
            raise _PureRefused("PRECONDITION_FAILED")
        s.update({"water.target_volume_m3": volume_m3, "water.flow_rate_m3_s": flow, "water.dispensing": True, "water.dispense_status": "running",
                  "water.dispensed_volume_m3": 0.0})
        c.g03["batch"] = {"requested_m3": volume_m3, "true_m3": target}
        c.batch_started_at_s = self._host.time_s
        return volume_m3

    def _pure_unit_commission(self, c, args):
        self._on(c, commissioned=False)
        c.state["settings.values"]["language"] = args["language"]
        c.state["unit.state"]["commissioned"] = True
        return {"commissioned": True}

    def _pure_settings_configure(self, c, args):
        self._on(c)
        c.state["settings.values"].update(args["settings"])
        c.state["unit.state"]["eco"] = _pure_eco(c.state["settings.values"], self._host.time_s)
        return {"settings": copy.deepcopy(c.state["settings.values"])}

    def _pure_dispense_manual(self, c, args):
        self._on(c)
        s = c.state
        flow = s["water.flow_rate_m3_s"] or PURELAB_MAX_DISPENSE_M3_S
        volume = flow * args["duration_s"]
        self._start_dispense(c, volume)
        d = s["dispense.state"]
        d["last_manual_m3"] = volume
        if d["recording"] is not None:
            d["recording"]["volume_m3"] += volume
        return {"volume_m3": volume}

    def _pure_dispense_continuous(self, c, args):
        self._on(c)
        if c.state["dispense.state"]["recording"] is not None: raise _PureRefused("NOT_READY")
        self._start_dispense(c, PURELAB_CONTINUOUS_M3)
        return {"volume_m3": PURELAB_CONTINUOUS_M3}

    def _pure_dispense_set_auto_volume(self, c, args):
        self._on(c)
        c.state["dispense.state"]["auto_volume_m3"] = float(args["volume_m3"])
        return {"volume_m3": float(args["volume_m3"])}

    def _pure_dispense_auto(self, c, args):
        self._on(c)
        d = c.state["dispense.state"]
        if args["profile"] is None:
            volume, factor = d["auto_volume_m3"], d["calibration_factor"]
        else:
            volume, factor = d["profiles"][args["profile"] - 1], 1.0          # a profile repeats the recorded dispense
            if volume is None: raise _PureRefused("NO_PROFILE")
        self._start_dispense(c, volume, factor)
        return {"volume_m3": volume}

    def _pure_dispense_record(self, c, args):
        self._on(c)
        d = c.state["dispense.state"]
        if args["action"] == "start":
            if args.get("profile") is None or c.state["water.dispensing"]: raise _PureRefused("INVALID_ARGUMENT" if args.get("profile") is None else "BUSY")
            d["recording"] = {"profile": args["profile"], "volume_m3": 0.0}
        else:
            if d["recording"] is None: raise _PureRefused("NOT_RUNNING")
            if c.state["water.dispensing"]:
                c.state["water.dispensing"], c.state["water.dispense_status"] = False, "stopped"
            d["profiles"][d["recording"]["profile"] - 1] = d["recording"]["volume_m3"] or None
            d["recording"] = None
        return {"dispense": copy.deepcopy(d)}

    def _pure_refill_start(self, c, args):
        self._on(c)
        s, sup = c.state, c.supply
        if sup is None or not s["water.qualified_feed"]: raise _PureRefused("NO_SUPPLY")
        if s["water.dispensing"] or (s["water.mode"] != "idle" and s["unit.state"]["auto_mode"] is None): raise _PureRefused("BUSY")
        if not (sup["ro_product_rate_m3_s"] and sup["recirculation_flow_m3_s"] and (s["water.feed_volume_m3"] or 0) > 0): raise _PureRefused("NO_SUPPLY")
        s["unit.state"]["auto_mode"], s["water.mode"] = "refill", "recirculate"
        return {"refilling": True}

    def _pure_reminders_reset(self, c, args):
        self._on(c)
        r = c.state["reminders.state"]
        r.update({args["which"] + "_since_s": self._host.time_s, args["which"] + "_due": False})
        return {"reminders": copy.deepcopy(r)}

    def _pure_sanitization_control(self, c, args):
        self._on(c)
        s = c.state
        san, action = s["sanitization.state"], args["action"]
        if action == "start":
            if san["step"] is not None or s["water.dispensing"]: raise _PureRefused("BUSY")
            san.update(step="fit_sanitization_pack", until_s=None)
        elif action == "cancel":
            if san["step"] is None: raise _PureRefused("NOT_RUNNING")
            if s["water.mode"] == "flush": s["water.mode"] = "idle"
            san.update(step=None, until_s=None)
        else:
            if san["step"] == "fit_sanitization_pack":
                sup = c.supply
                if not (sup and sup["flush_flow_m3_s"] and sup["drain_connected"] and s["water.buffer_volume_m3"] and s["water.buffer_volume_m3"] > 0):
                    raise _PureRefused("NO_SUPPLY")
                s["unit.state"]["auto_mode"] = None
                s["water.mode"] = "flush"
                san.update(step="running", until_s=self._host.time_s + PURELAB_SANITIZATION_S)
            elif san["step"] == "rinse_done":
                san.update(step="fit_purification_pack", until_s=None)
            elif san["step"] == "fit_purification_pack":
                san.update(step=None, until_s=None)
                s["reminders.state"].update(sanitization_since_s=self._host.time_s, sanitization_due=False)
            else:
                raise _PureRefused("NOT_READY")
        return {"sanitization": copy.deepcopy(san)}

    def _pure_calibration_control(self, c, args):
        self._on(c)
        d, action = c.state["dispense.state"], args["action"]
        if action == "start":
            self._start_dispense(c, PURELAB_CAL_NOMINAL_M3, d["calibration_factor"])
            d["calibration"] = {"commanded_m3": PURELAB_CAL_NOMINAL_M3}
        elif action == "enter":
            if d["calibration"] is None or args.get("measured_m3") is None: raise _PureRefused("NOT_RUNNING" if d["calibration"] is None else "INVALID_ARGUMENT")
            if c.state["water.dispensing"]: raise _PureRefused("BUSY")
            d["calibration_factor"] = round(d["calibration_factor"] * d["calibration"]["commanded_m3"] / args["measured_m3"], 6)
            d["calibration"], d["calibrated_at"] = None, _g03_clock(self._host.time_s)
        else:
            d["calibration"] = None
        return {"calibration": {"factor": d["calibration_factor"], "pending": copy.deepcopy(d["calibration"])}}

    def _pure_dispense_configure_error(self, c, args):
        c.state["dispense.error"] = {"gain": float(args["gain"])}
        return {"error": copy.deepcopy(c.state["dispense.error"])}


_g03_purelab_export_base = export_purelab_contract_metadata


def export_purelab_contract_metadata():
    """CHEM-190 metadata plus the display handset: device-layer slots (in state_slots), device_capabilities, panel_capabilities (when the
    panel spec exists), parameters and limitations.  The CHEM-190 'capabilities' keep their schemas."""
    meta = _g03_purelab_export_base()
    t = meta["asset_types"][PURELAB_ASSET_TYPE_ID]
    t["asset_version"] = PURELAB_ASSET_VERSION
    dev = {}
    for name, (inputs, optional, outputs, example, desc, effects, components, kind) in PURELAB_DEVICE_ARGS.items():
        dev[name] = g03_device_capability(name, kind, desc, inputs, outputs, example, components, effects, optional=optional, binding=_PURE_BINDING,
                                          visibility="evaluator" if name in PURELAB_EVALUATOR_CAPS else "agent",
                                          completion="同步提交；取水、补水、循环与消毒冲洗由宿主step推进")
    t["device_capabilities"] = dev
    t["panel_capabilities"] = {}
    if (PURELAB_ASSET_DIR / "panel_ui.json").is_file():
        spec = _purelab_panel()["spec"]
        called = _g03_engine().spec_capabilities(spec)
        effects = list(dict.fromkeys(e["state_id"] for c in called for e in (t["capabilities"].get(c) or dev.get(c) or {}).get("state_effects", [])
                                     if e["timing"] == "immediate"))
        t["panel_capabilities"] = g03_panel_capabilities(spec, _PURE_BINDING, effects)
    t["parameters"].update({
        "refill_setpoint_eco_m3": {"value": PURELAB_REFILL_ECO_M3, "unit": "m^3", "evidence": "manual", "source": "p.15"},
        "refill_setpoint_m3": {"value": PURELAB_REFILL_M3, "unit": "m^3", "evidence": "manual", "source": "p.15"},
        "intermittent_recirculation_s": {"value": [PURELAB_RECIRC_ON_S, PURELAB_RECIRC_PERIOD_S], "unit": "s", "evidence": "manual", "source": "p.15"},
        "eco_window_default_s": {"value": [18 * 3600.0, 9 * 3600.0], "unit": "s", "evidence": "manual", "source": "p.5: 18:00 - 09:00"},
        "reminder_intervals_s": {"value": dict(PURELAB_REMINDERS_S), "unit": "s", "evidence": "manual", "source": "p.14 (sanitization interval assumed)"},
        "continuous_dispense_limit_m3": {"value": PURELAB_CONTINUOUS_M3, "unit": "m^3", "evidence": "engineering_assumption", "source": "not given"},
        "calibration_volume_m3": {"value": PURELAB_CAL_NOMINAL_M3, "unit": "m^3", "evidence": "engineering_assumption", "source": "not given"},
        "device_clock_epoch": {"value": G03_DEVICE_EPOCH.isoformat(), "unit": "1", "evidence": "engineering_assumption"},
    })
    meta["limitations"] = list(meta["limitations"]) + PURELAB_G03_LIMITATIONS
    return meta


PURELAB_G03_LIMITATIONS = [
    "Run ownership (lead rule 2026-10-19, a simulation rule): a dispense, sanitization or volumetric calibration started by one agent belongs to "
    "it; another agent cannot stop or change it (Dispense keys, volume / flow / mode, calibration, sanitization) on the panel or the direct path "
    "(ACCESS_DENIED, state unchanged; the handset shows 'Run of another user' for 3 s, text ours).  The 24 V power supply (power.set) is "
    "physical (lead clarification 10-19): anyone may switch it off, the run ends as on a power loss.  The dispense arm (by hand) and the "
    "evaluator are not restricted.",
    "Display-handset layer (operator manual MANU41742): first start, manual / continuous / Auto Volume / Profile Dispense, automatic, initiated and timed "
    "reservoir refill, intermittent recirculation, ECO mode, Main Menu settings, reminders, sanitization, volumetric calibration, alarm points and the "
    "Infobar follow the manual as a minimal device model, not ELGA firmware.  The Main Menu item names are inferred from the manual text.",
    "A held Dispense key dispenses flow x holding time (the dispense runs on the host step after the gesture); continuous dispense stops after 10 L "
    "at the latest (assumption).  Refill and recirculation use the CHEM-190 recirculate mode and its host-configured rates; without them nothing refills.",
    "Dispense volume error (relative) is set only by the evaluator (dispense.configure_error), 0 by default; the volumetric calibration factor corrects "
    "the volumetric dispenses: Auto Volume, the calibration dispense and the CHEM-190 water.set_volume + water.start (one metering model). The set "
    "volume (water.target_volume_m3) and the indicated volume stay the requested volume; only the water that flows (process truth) carries the "
    "calibration and the error. Data capture to USB, the footswitch, BMS and the leak detector are absent from the simulated configuration.",
    "Language (first start and Main Menu): the selection is stored, shown in the Main Menu and reported in settings.values; our manual contains no "
    "translated handset texts, so every language keeps the English texts (the language list itself is an assumption).",
]
# <<< G03 CHEM-190 PURELAB flex 3 handset


# >>> G03 CHEM-024 RV 10 digital front panel
# Device layer of the RV 10 digital drive keys and display (operating instructions references/manuals/ika_rv10_digital_v/
# operating_manual__rv10_basic_digital_nist.pdf, 2009, 'p.N' = PDF page): On/Off switch and Power (stand-by) key, lift keys L / M held to move,
# lower end stop, speed knob (turn = set speed 20-270 rpm, press = start / stop rotation), Timer (1-199 min, starts with the rotation; at the
# end a beep, the rotation stops and the lift goes up), Int (direction change every 1-60 s, speed limited to 200 rpm), and an approximate
# HB 10 digital bath panel (its manual is not available).  The CHEM-024 commands keep their meaning; timer.set_duration, declared before,
# is now implemented with the manual's behaviour.
RV10_ASSET_VERSION = "1.3.1"   # 1.3.1 (2026-10-22): vacuum.set_pressure unsupported (was declared)
RV10_ASSET_DIR = G03_ASSET_ROOT / "ika_rv10"
RV10_MANUAL = "references/manuals/ika_rv10_digital_v/operating_manual__rv10_basic_digital_nist.pdf"
RV10_PANEL_MIN_RPM, RV10_PANEL_MAX_RPM, RV10_INTERVAL_MAX_RPM = 20, 270, 200   # p.22 (speed range 20-270 rpm, 200 rpm in interval mode)
RV10_TIMER_MAX_S = 199 * 60.0
RV10_INTERVAL_MAX_S = 60                 # p.26 technical data: interval 1-60 s
RV10_LOWER_STOP_MAX_M = .06              # p.22: the lower end stop path is limited to 0-6 cm
_RV10_RPM = 30.0 / math.pi
RV10_DEVICE_SLOTS = {
    "mains.on": ({"type": "boolean"}, "右侧On/Off主开关是否接通（断开才真正断电）（p.22）"),
    "timer.setting_s": (_g03_num("s", 0, RV10_TIMER_MAX_S), "保存的定时值（0=关闭，1-199 min）（p.22）"),
    "timer.remaining_s": (_g03_num("s", 0, RV10_TIMER_MAX_S, nullable=True), "定时剩余时间；旋转时倒计时，未在计时为null（p.22）"),
    "interval.setting_s": (_g03_num("s", 0, RV10_INTERVAL_MAX_S, integer=True), "左右换向间隔（0=关闭，1-60 s）（p.22, p.26）"),
    "rotation.direction": (_g03_str(["right", "left"]), "当前旋转方向（间隔模式下按间隔换向）"),
    "lift.lower_stop_m": (_g03_num("m", 0, RV10_LOWER_STOP_MAX_M), "可调下止点（0-6 cm），升降不会低于此位置（p.22-23）"),
    "drive.beeps": ({"type": "integer", "minimum": 0, "x-unit": "1"}, "提示音次数（定时结束蜂鸣）"),
    "drive.error": (_g03_str(["E01", "E02"], nullable=True), "显示的错误代码：E01无或过低转速，E02与加热浴无通讯；出错后升降升到顶、设备锁定，需主开关断电重启（p.26）"),
    "drive.fault_cause": (_g03_str(["none", "motor_blocked", "ir_link_blocked"]), "评测者：注入的故障原因（电机堵转/过载→E01，红外接口遮挡→E02），默认none"),
}
RV10_EVALUATOR_SLOTS = ("drive.fault_cause",)
RV10_EVALUATOR_CAPS = ("drive.configure_fault",)
RV10_LOCKED_BY_ERROR = ("power.set", "rotation.set_speed", "rotation.start", "timer.set_duration", "interval.set")   # p.26: no longer operable
RV10_RUN_GUARDED = ("rotation.start", "rotation.stop", "rotation.set_speed", "timer.set_duration", "interval.set", "lift.set_position", "lift.jog",
                    "thermal.set_temperature", "thermal.start", "thermal.stop")   # stop / change the rotation or bath run (plus power / mains off)


def _rv10_runs(s):
    """The run of the drive: the rotation or the bath heating (one channel, owned by the agent that started it)."""
    return {"run": bool(s["rotation.running"] or s["thermal.enabled"])}


def _rv10_touches(cap, args):
    """power.set is the Power stand-by key (soft, guarded); mains.set is the On/Off main switch (hard: physical, never guarded)."""
    off = cap == "power.set" and args.get("enabled") is False
    return ("run",) if cap in RV10_RUN_GUARDED or off else ()
for _rv_sid, (_rv_schema, _rv_meaning) in RV10_DEVICE_SLOTS.items():
    RV10_STATE_SLOTS[_rv_sid] = _slot("s" if _rv_sid.endswith("_s") else "m" if _rv_sid.endswith("_m") else "1", "derived", _rv_schema,
                                      visibility="evaluator" if _rv_sid in RV10_EVALUATOR_SLOTS else "agent")
    RV10_STATE_SLOTS[_rv_sid]["meaning_zh"] = _rv_meaning
RV10_DEVICE_ARGS = {   # name: (inputs, optional, outputs, example, description, effects, components, kind)
    "mains.set": ({"on": {"type": "boolean"}}, (), {"on": {"type": "boolean"}}, {"on": True}, "右侧On/Off主开关：接通即激活设备功能；断开断电（p.22）",
                  ["mains.on", "power.enabled", "rotation.running", "thermal.enabled"], ["on_off_switch"], "device_command"),
    "interval.set": ({"interval_s": _g03_num("s", 0, RV10_INTERVAL_MAX_S, integer=True)}, (), {"interval_s": _g03_num("s")}, {"interval_s": 10},
                     "Int：左右换向间隔0（关）或1-60 s；间隔模式下转速限200 rpm（p.22）", ["interval.setting_s", "rotation.target_speed_rad_s"], ["key_int"],
                     "configuration"),
    "lift.jog": ({"direction": _g03_str(["up", "down"]), "duration_s": _g03_num("s", 0.05, 60)}, (), {"position_m": _g03_num("m")},
                 {"direction": "down", "duration_s": 1.0}, "按住L上升/按住M下降，松开停止；不低于下止点（p.22）", ["lift.target_position_m", "lift.moving"],
                 ["key_lift_up", "key_lift_down", "lift_carriage"], "device_command"),
    "lift.set_lower_stop": ({"stop_m": _g03_num("m", 0, RV10_LOWER_STOP_MAX_M)}, (), {"stop_m": _g03_num("m")}, {"stop_m": .03},
                            "按升降前部中心按钮移动可调下止点（0-6 cm）（p.22-23）", ["lift.lower_stop_m"], ["end_stop"], "configuration"),
    "drive.configure_fault": ({"cause": _g03_str(["none", "motor_blocked", "ir_link_blocked"])}, (), {"cause": _g03_str()}, {"cause": "none"},
                              "评测者：注入故障原因（motor_blocked→旋转时E01，ir_link_blocked→有加热浴时E02），默认none；错误由主开关断电清除（p.26）",
                              ["drive.fault_cause", "drive.error"], ["display"], "simulation"),
}
_RV10_BINDING = "basic40_runtime_vacuum.RV10Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"
_g03_RV10HostBase = RV10Host
_g03_RV10SessionBase = RV10Session
_RV10_PANEL = {}


def _rv10_panel():
    if not _RV10_PANEL:
        engine = _g03_engine()
        caps = set(RV10_CAPABILITY_ARGUMENTS) | set(RV10_DEVICE_ARGS) | {"timer.set_duration"}
        spec = engine.load_spec(RV10_ASSET_DIR / "panel_ui.json", capabilities=caps)
        _RV10_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _RV10_PANEL


class RV10Host(_g03_RV10HostBase):
    """CHEM-024 host with the drive panel: mains, timer, interval, lift jog and lower end stop, and the front panel."""

    PANEL_PRINCIPAL = "__rv10_panel__"

    def __init__(self, *, ambient_temperature_k=293.15, lift_speed_m_s=.02, scene_adapter=None):
        super().__init__(ambient_temperature_k=ambient_temperature_k, lift_speed_m_s=lift_speed_m_s, scene_adapter=scene_adapter)
        self._panels = {}
        self.panel_capture_dir = None
        self._principals[self.PANEL_PRINCIPAL] = ("agent", None)
        self._logs[self.PANEL_PRINCIPAL] = []
        self._panel_session = RV10Session(self, self.PANEL_PRINCIPAL)
        self._panel_caller = None                                           # the session whose panel gesture is running (its principal acts)

    def create_instance(self, instance_id, asset_type_id=RV10_ASSET_TYPE_ID):
        with self._lock:
            super().create_instance(instance_id, asset_type_id)
            inst = self._instances[instance_id]
            inst.state.update({"mains.on": False, "timer.setting_s": 0.0, "timer.remaining_s": None, "interval.setting_s": 0, "rotation.direction": "right",
                               "lift.lower_stop_m": 0.0, "drive.beeps": 0, "drive.error": None, "drive.fault_cause": "none"})
            inst.g03 = {"power_epoch": 0, "interval_t": 0.0}
            if (RV10_ASSET_DIR / "panel_ui.json").is_file():
                self._panels[instance_id] = _rv10_panel()["engine"].PanelUI(_rv10_panel()["spec"], _G03PanelHost(
                    lambda: self._panel_values(instance_id), lambda cap, args: self._panel_call(instance_id, cap, args), lambda: self.time_s))

    def panel(self, instance_id):
        return self._panels[instance_id]

    def _panel_values(self, instance_id):
        inst = self._instances[instance_id]
        out = {sid: copy.deepcopy(v) for sid, v in inst.state.items() if RV10_STATE_SLOTS[sid]["visibility"] == "agent"}
        out.update({"host.mains": inst.state["mains.on"], "host.power_epoch": inst.g03["power_epoch"], "host.ambient_k": self.ambient_temperature_k,
                    "host.bath_configured": inst.bath is not None})
        return out

    def _panel_call(self, instance_id, capability_id, arguments):
        """A command of the panel firmware runs as the session whose gesture triggered it; without a gesture as the panel's own principal."""
        with self._lock:
            return (self._panel_caller or self._panel_session)._invoke_checked(self._instances[instance_id], capability_id, arguments, None)

    def step(self, dt_s):
        """The CHEM-024 step, split at the moment a running timer reaches zero (so the rotation stops exactly then)."""
        with self._lock:
            if _number(dt_s) and 0 < dt_s <= 3600:
                ends = [inst.state["timer.remaining_s"] for inst in self._instances.values() if getattr(inst, "g03", None) is not None
                        and inst.state["rotation.running"] and inst.state["power.enabled"] and inst.state["timer.remaining_s"] is not None]
                first = min(ends) if ends else None
                if first is not None and 1e-9 < first < dt_s - 1e-9:
                    super().step(first)
                    return self.step(dt_s - first)
            return super().step(dt_s)

    def _advance_bath(self, inst, dt):
        """The CHEM-024 bath step of one instance, then the drive's timer (it counts while the rotation runs; at zero: beep, rotation stop, lift
        up) and the interval direction changes."""
        super()._advance_bath(inst, dt)
        g, s = getattr(inst, "g03", None), inst.state
        if g is not None:
            self._rv10_fault(inst)
        if g is None or not s["rotation.running"] or not s["power.enabled"]:
            return
        self._rv10_interval(inst, dt)
        if s["timer.remaining_s"] is not None:
            s["timer.remaining_s"] = max(0.0, s["timer.remaining_s"] - dt)
            if s["timer.remaining_s"] <= 1e-9:
                s["rotation.running"] = False
                s["timer.remaining_s"] = None
                s["drive.beeps"] += 1
                s["lift.target_position_m"] = RV10_MAXIMUM_LIFT_M

    @staticmethod
    def _rv10_fault(inst):
        """p.26: an injected cause shows its error (E01 while the motor should turn, E02 when a bath is there); the rotation stops, the lift goes
        to the top and the drive can no longer be operated (the lift can) until it is switched off at the main switch."""
        s = inst.state
        if s["drive.error"] is not None or not s["power.enabled"]:
            return False
        code = "E01" if s["drive.fault_cause"] == "motor_blocked" and s["rotation.running"] else \
            "E02" if s["drive.fault_cause"] == "ir_link_blocked" and inst.bath is not None else None
        if code is None:
            return False
        s.update({"drive.error": code, "rotation.running": False, "timer.remaining_s": None, "lift.target_position_m": RV10_MAXIMUM_LIFT_M})
        return True

    @staticmethod
    def _rv10_interval(inst, dt):
        s, g = inst.state, inst.g03
        k = s["interval.setting_s"]
        if not k:
            return
        g["interval_t"] += dt
        flips = int(g["interval_t"] // k)
        g["interval_t"] -= flips * k
        if flips % 2:
            s["rotation.direction"] = "left" if s["rotation.direction"] == "right" else "right"


class RV10Session(_g03_RV10SessionBase):
    """CHEM-024 session plus the drive panel commands, the implemented timer and the panel.* commands."""

    @property
    def _host(self):
        return self._RV10Session__host

    @property
    def _visibility(self):
        return self._host._principals[self._RV10Session__principal_id][0]

    def describe(self, *, asset_type_id=None, instance_id=None):
        response = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if response.get("error") is None and "capabilities" in response:
            evaluator = self._visibility in ("debug", "evaluator")
            meta = export_rv10_contract_metadata()["asset_types"][RV10_ASSET_TYPE_ID]
            slots = response["state_slots"]
            for group in ("device_capabilities", "panel_capabilities"):
                for name, cap in meta[group].items():
                    if cap["visibility"] == "agent" or evaluator:
                        cap = copy.deepcopy(cap)
                        cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in slots]
                        cap["observes"] = [sid for sid in cap["observes"] if sid in slots]
                        response["capabilities"][name] = cap
            response["capabilities"]["timer.set_duration"] = copy.deepcopy(meta["capabilities"]["timer.set_duration"])
            response["asset_version"] = RV10_ASSET_VERSION
        return response

    def _invoke_checked(self, inst, cap, args, request_id):
        """Run ownership (g03_owner_refuses) before the command; a run the command starts belongs to this session's principal.  The lower end
        stop is mechanical and not guarded."""
        g, me, vis = getattr(inst, "g03", None), self._RV10Session__principal_id, self._visibility
        if g is not None and g03_owner_refuses(g, _rv10_runs(inst.state), _rv10_touches(cap, args), me, vis, RV10Host.PANEL_PRINCIPAL):
            return self._error(inst.instance_id, "ACCESS_DENIED", request_id)
        before = _rv10_runs(inst.state) if g is not None else None
        response = self._rv10_invoke(inst, cap, args, request_id)
        live = self._host._instances.get(inst.instance_id)
        if response["status"] == "succeeded" and getattr(live, "g03", None) is not None:
            g03_owner_note(live.g03, before, _rv10_runs(live.state), me, vis, RV10Host.PANEL_PRINCIPAL)
        return response

    def _rv10_invoke(self, inst, cap, args, request_id):
        if cap in RV10_DEVICE_ARGS or cap.startswith("panel.") or (cap == "timer.set_duration" and getattr(inst, "g03", None) is not None):
            return self._device_checked(inst, cap, args, request_id)
        live = inst
        before = dict(live.state) if getattr(live, "g03", None) is not None else None
        if before is not None and before["drive.error"] is not None and cap in RV10_LOCKED_BY_ERROR:
            return self._error(inst.instance_id, "PRECONDITION_FAILED", request_id)   # p.26: after an error the device can no longer be operated
        if cap == "rotation.set_speed" and before is not None and live.state["interval.setting_s"] and isinstance(args.get("speed_rad_s"), (int, float)) \
                and not isinstance(args.get("speed_rad_s"), bool) and args["speed_rad_s"] > RV10_INTERVAL_MAX_RPM / _RV10_RPM + 1e-9:
            return self._error(inst.instance_id, "PRECONDITION_FAILED", request_id)   # p.22: interval mode limits the speed to 200 rpm
        response = super()._invoke_checked(inst, cap, args, request_id)
        if response["status"] == "succeeded" and before is not None:
            s = inst.state
            if cap == "rotation.start" and not before["rotation.running"] and s["timer.setting_s"] > 0 and s["timer.remaining_s"] is None:
                s["timer.remaining_s"] = s["timer.setting_s"]                 # the timer starts with the rotation drive (p.22)
            if cap == "lift.set_position" and s["lift.target_position_m"] < s["lift.lower_stop_m"]:
                s["lift.target_position_m"] = s["lift.lower_stop_m"]           # the lower end stop holds the lift (p.23)
                self._host._refresh(inst)
            if self._host._rv10_fault(inst):
                self._host._refresh(inst)
        return response

    def _device_checked(self, inst, cap, args, rid):
        host = self._host
        if cap.startswith("panel."):
            meta = _rv10_panel()["caps"] if (RV10_ASSET_DIR / "panel_ui.json").is_file() else {}
            if cap not in meta: return self._error(inst.instance_id, "UNKNOWN_CAPABILITY", rid)
            if meta[cap]["visibility"] != "agent" and self._visibility not in ("debug", "evaluator"): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            if _g03_check(meta[cap]["input_schema"], args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
            if self._RV10Session__principal_id == RV10Host.PANEL_PRINCIPAL or inst.instance_id not in host._panels:
                return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            previous, host._panel_caller = host._panel_caller, self                # the commands of this gesture act as this session
            try:
                result, code = g03_panel_gesture(host._panels[inst.instance_id], cap, args, instance_id=inst.instance_id, time_s=host.time_s,
                                                 capture_dir=host.panel_capture_dir)
            finally:
                host._panel_caller = previous
            if code: return self._error(inst.instance_id, code, rid)
            return self._envelope(inst.instance_id, rid, result=result)
        if cap == "timer.set_duration":
            inputs, optional = {"duration_s": _g03_num("s", 0, RV10_TIMER_MAX_S)}, ()
        else:
            inputs, optional = RV10_DEVICE_ARGS[cap][0], RV10_DEVICE_ARGS[cap][1]
        if cap in RV10_EVALUATOR_CAPS and self._visibility not in ("debug", "evaluator"): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
        if _g03_check(_g03_obj(inputs, optional), args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
        if inst.state["drive.error"] is not None and cap in RV10_LOCKED_BY_ERROR: return self._error(inst.instance_id, "PRECONDITION_FAILED", rid)
        candidate = copy.deepcopy(inst)
        s = candidate.state
        if cap == "mains.set":
            s["mains.on"] = args["on"]
            s["power.enabled"] = args["on"]                                  # switching on activates the device functions (p.22)
            if not args["on"]:
                s["rotation.running"], s["thermal.enabled"] = False, False
                s["timer.remaining_s"] = None
                s["drive.error"] = None                                      # p.26: switch off at the main switch, correct, restart
            result = {"on": args["on"]}
        elif cap == "drive.configure_fault":
            s["drive.fault_cause"] = args["cause"]
            result = {"cause": args["cause"]}
        elif cap == "timer.set_duration":
            v = float(args["duration_s"])
            if v and v % 60.0: return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)   # whole minutes, 1-199 min
            s["timer.setting_s"] = v
            s["timer.remaining_s"] = v if (v and s["rotation.running"]) else None
            result = {}
        elif cap == "interval.set":
            s["interval.setting_s"] = int(args["interval_s"])
            if args["interval_s"] and s["rotation.target_speed_rad_s"] > RV10_INTERVAL_MAX_RPM / _RV10_RPM:
                s["rotation.target_speed_rad_s"] = RV10_INTERVAL_MAX_RPM / _RV10_RPM
            candidate.g03["interval_t"] = 0.0
            result = {"interval_s": float(args["interval_s"])}
        elif cap == "lift.jog":
            if not s["power.enabled"]: return self._error(inst.instance_id, "PRECONDITION_FAILED", rid)
            pos = s["lift.indicated_position_m"]
            delta = host.lift_speed_m_s * args["duration_s"] * (1 if args["direction"] == "up" else -1)
            target = min(RV10_MAXIMUM_LIFT_M, max(s["lift.lower_stop_m"], pos + delta))
            s["lift.target_position_m"] = round(target, 6)
            result = {"position_m": s["lift.target_position_m"]}
        else:
            s["lift.lower_stop_m"] = float(args["stop_m"])
            if s["lift.target_position_m"] < s["lift.lower_stop_m"]:
                s["lift.target_position_m"] = s["lift.lower_stop_m"]
            result = {"stop_m": float(args["stop_m"])}
        host._rv10_fault(candidate)
        host._refresh(candidate)
        updates = {k: v for k, v in candidate.state.items() if v != inst.state[k]}
        if host.scene_adapter and updates:
            try:
                host.scene_adapter(inst.instance_id, copy.deepcopy(updates))
            except Exception:
                return self._error(inst.instance_id, "DEVICE_ERROR", rid, "failed")
        if cap == "mains.set" and args["on"] != inst.state["mains.on"]:
            candidate.g03["power_epoch"] += 1
        inst.state = candidate.state
        inst.g03 = candidate.g03
        inst.revision += 1
        return self._envelope(inst.instance_id, rid, result=result)


_g03_rv10_export_base = export_rv10_contract_metadata


def export_rv10_contract_metadata():
    """CHEM-024 metadata plus the drive panel: device-layer slots, the implemented timer.set_duration, device_capabilities, panel_capabilities
    (when the panel spec exists), parameters and limitations."""
    meta = _g03_rv10_export_base()
    t = meta["asset_types"][RV10_ASSET_TYPE_ID]
    t["asset_version"] = RV10_ASSET_VERSION
    timer = t["capabilities"]["timer.set_duration"]
    timer.update(availability="implemented", description_zh="Timer：0（关闭）或1-199 min整分钟；随旋转启动倒计时，结束时蜂鸣、停转并升起升降（p.22）",
                 input_schema={"type": "object", "properties": {"duration_s": {"type": "number", "minimum": 0, "maximum": RV10_TIMER_MAX_S, "x-unit": "s",
                                                                               "description": "Whole minutes; 0 switches the timer off"}},
                               "required": ["duration_s"], "additionalProperties": False},
                 output_schema={"type": "object", "properties": {}, "additionalProperties": False}, example_arguments=[{"duration_s": 1800.0}],
                 state_effects=[{"state_id": sid, "timing": "immediate", "description_zh": "保存定时值；旋转中立即开始计时"} for sid in ("timer.setting_s", "timer.remaining_s")]
                 + [{"state_id": sid, "timing": "on_step", "description_zh": "旋转时倒计时；到时蜂鸣、停转、升降上升"} for sid in ("timer.remaining_s", "rotation.running",
                                                                                                                 "lift.target_position_m", "drive.beeps")],
                 observes=["timer.setting_s", "timer.remaining_s"], binding=_RV10_BINDING.format(name="timer.set_duration"))
    dev = {}
    for name, (inputs, optional, outputs, example, desc, effects, components, kind) in RV10_DEVICE_ARGS.items():
        dev[name] = g03_device_capability(name, kind, desc, inputs, outputs, example, components, effects, optional=optional, binding=_RV10_BINDING,
                                          completion="同步提交；升降由宿主step按升降速度推进",
                                          visibility="evaluator" if name in RV10_EVALUATOR_CAPS else "agent")
    t["device_capabilities"] = dev
    t["panel_capabilities"] = {}
    if (RV10_ASSET_DIR / "panel_ui.json").is_file():
        spec = _rv10_panel()["spec"]
        called = _g03_engine().spec_capabilities(spec)
        effects = list(dict.fromkeys(e["state_id"] for c in called for e in (t["capabilities"].get(c) or dev.get(c) or {}).get("state_effects", [])
                                     if e["timing"] == "immediate"))
        t["panel_capabilities"] = g03_panel_capabilities(spec, _RV10_BINDING, effects)
    t["parameters"].update({
        "panel_speed_range_rpm": {"value": [RV10_PANEL_MIN_RPM, RV10_PANEL_MAX_RPM], "unit": "rpm", "evidence": "manual", "source": "p.22 (20-270 rpm, +/-5 rpm setting)"},
        "interval_mode_max_rpm": {"value": RV10_INTERVAL_MAX_RPM, "unit": "rpm", "evidence": "manual", "source": "p.22"},
        "timer_range_s": {"value": [0, RV10_TIMER_MAX_S], "unit": "s", "evidence": "manual", "source": "p.22, p.26 (1-199 min, 0 = off)"},
        "interval_range_s": {"value": [0, RV10_INTERVAL_MAX_S], "unit": "s", "evidence": "manual", "source": "p.26 (1-60 s)"},
        "lower_stop_range_m": {"value": [0, RV10_LOWER_STOP_MAX_M], "unit": "m", "evidence": "manual", "source": "p.22 (0-6 cm)"},
    })
    meta["limitations"] = [x for x in meta["limitations"] if not x.startswith("Timer and vacuum-pressure setting are declared unsupported")] + [
        "Vacuum-pressure setting stays declared unsupported (the external source owns it). The timer is implemented: it counts while the rotation runs; at "
        "zero a beep (drive.beeps), the rotation stops and the lift target goes to the top."] + RV10_G03_LIMITATIONS
    return meta


RV10_G03_LIMITATIONS = [
    "Run ownership (lead rule 2026-10-19, a simulation rule): the rotation / bath heating started by one agent (knob, rotation.start, bath switch, "
    "thermal.start) belongs to it; while it runs another agent cannot stop or change it or put it in stand-by (speed, timer, interval, lift, bath "
    "set point and heating, Power stand-by key) on the panel or the direct path (ACCESS_DENIED, state unchanged; the drive shows dEn for 2 s, text "
    "ours). The On/Off main switch (mains.set) is physical (lead clarification 10-19): anyone may switch it off, the device is de-energised and the "
    "run ends. The lower end stop (mechanical) and the evaluator are not restricted.",
    "Drive panel layer (operating instructions 2009): On/Off switch, Power stand-by key, lift keys held to move (the hold time times the host lift speed), "
    "lower end stop 0-6 cm, speed knob (20-270 rpm in 5 rpm steps on the panel; the API keeps 5-280 rpm), timer and interval follow the manual as a minimal "
    "device model, not IKA firmware.  The interval direction is a state (rotation.direction); the rotation angle stays the unwrapped magnitude.",
    "The HB 10 digital bath panel is approximate (its manual is not available): a temperature display, a knob for the set temperature (1 K) and an "
    "on/off switch for the heating; its adjustable safety circuit is not modelled.  Error codes E01 / E02 appear only from an evaluator-injected "
    "cause (drive.configure_fault; no motor load or IR link physics).  Not simulated: REMOTE / labworldsoft, smooth start, safety lift on power "
    "failure.  The lower end stop also holds lift.set_position targets (the commanded value is returned unchanged).",
]
# <<< G03 CHEM-024 RV 10 digital front panel


# >>> G03 CHEM-189 PureLab HE 2GB front panel
# Device layer of the glovebox HMI touch screen and PLC functions (operating manual references/manuals/inert_purelab_he_2gb/
# operating_manual__purelab_he_itinc_2006_nist.pdf, 'p.N' = PDF page = printed page): login, vacuum pump and blower START / STOP, blower
# settings, regeneration sequence with its prompts and step timers, purge control (timed, manual, O2 level), automatic antechamber control
# (EVAC / REFILL / AUTO with evacuate set point and refill durations), system configuration (working gas, working pressure window, blower
# restart, vacuum pump timer, screen contrast), alarms with history and acknowledgement, O2 / H2O trend, foot pedals and light hood switch.
# The CHEM-189 commands keep their meaning: process truth and sensor readings still come only from the trusted host, regeneration.start stays
# unsupported (no regeneration kinetics); the HMI regeneration sequence is a separate device command (regen.control).
INERT_PURELAB_ASSET_VERSION = "1.2.0"
INERT_PURELAB_ASSET_DIR = G03_ASSET_ROOT / "inert_purelab_he"
INERT_PURELAB_MANUAL = "references/manuals/inert_purelab_he_2gb/operating_manual__purelab_he_itinc_2006_nist.pdf"
INERT_LOGIN_CODE = 7990                                   # p.19, p.25 (Login User)
INERT_REGEN_STEPS = (("heat", "Heating", 180), ("heat_purge", "Heat & Purge", 30), ("purge", "Purging", 180), ("evacuate", "Evacuating", 240),
                     ("cooling", "Cooling", 240))         # p.29 Fig. 17 / p.48 Fig. 42: factory default step times (min)
INERT_REGEN_ABORT_S = 300.0                               # p.28: abort within the first 5 minutes of starting
INERT_PUMP_TIMER_S = 600.0                                # p.34: vacuum pump timer powers the pump down after 10 minutes
INERT_BLOWER_TIMER_S, INERT_BLOWER_DEFAULT_PCT = 1800.0, 50.0   # p.31 (back to 50 % after 30 minutes), p.23 (50 % suggested)
INERT_O2_BLOWER_PPM = 10.0                                # p.31: O2 blower speed raises the speed above 10 ppm
INERT_PRESSURE_RATE_PA_S = 100.0                          # assumption: gas in / out at 1 mbar/s (pedals and the working-window control)
INERT_TREND_POINTS = 1440                                 # assumption: 24 h of one-minute trend points
INERT_REGEN_FLOWING = ("flow", "heat_purge", "purge")     # p.48 steps 4-6, p.64: regen gas flows for the flow check, then only in the purge stages
_INERT_OBJ = {"type": "object"}
INERT_DEVICE_SLOTS = {
    "hmi.user": (_g03_str(["none", "user"]), "HMI登录级别：none为上电状态屏（只读），user为输入7990后的用户级（p.18-19, p.25）"),
    "system.config": (_g03_obj({"gas": _g03_str(["nitrogen", "argon"]), "column_in_use": _g03_num("1", 1, 2, integer=True),
                                "blower_restart": {"type": "boolean"}, "pump_timer": {"type": "boolean"},
                                "min_sp_pa": _g03_num("Pa", -1500, 1500), "max_sp_pa": _g03_num("Pa", -1500, 1500),
                                "contrast_pct": _g03_num("%", 10, 100)}),
                      "Glove Box Config：工作气体、在用纯化柱、断电后风机重启、真空泵定时、工作压力上下限、屏幕对比度（p.34, p.39）"),
    "pump.state": (_g03_obj({"running": {"type": "boolean"}, "idle_s": _g03_num("s", 0)}), "真空泵START/STOP的PLC输出与定时器空闲计时（p.27, p.34）"),
    "blower.state": (_g03_obj({"speed_set_pct": _g03_num("%", 0, 100), "speed_pct": _g03_num("%", 0, 100), "mode": _g03_str(["MANUAL", "AUTO"]),
                               "o2_speed": {"type": "boolean"}, "timer": {"type": "boolean"}, "regen_restart": {"type": "boolean"},
                               "since_change_s": _g03_num("s", 0), "purge_hold": {"type": "boolean"}}),
                     "Blower Control：设定/当前转速%、O2自动提速、30 min回50%定时、再生后自动重启（p.23, p.31, p.47）"),
    "purge.control": (_g03_obj({"o2_sp_ppm": _g03_num("ppm", 0, 1000), "o2_level": {"type": "boolean"}, "timed_min": _g03_num("min", 1, 999),
                                "timed_active": {"type": "boolean"}, "timed_elapsed_s": _g03_num("s", 0), "manual_active": {"type": "boolean"},
                                "o2_active": {"type": "boolean"}}),
                      "Purge Control：O2超限吹扫、定时吹扫（时长与计时）、手动吹扫（p.32, p.47）"),
    "antechamber.config": (_g03_obj({"number": _g03_num("1", 1, 2, integer=True), "evacuate_sp_pa": _g03_num("Pa", 0, 110000),
                                     "refill_s": _g03_num("s", 0, 999), "final_refill_s": _g03_num("s", 0, 999)}),
                           "Antechamber Config：自动抽空设定点、每次（末次）回填时长、所用过渡舱号（p.33-34）"),
    "antechamber.control": (_g03_obj({"evac": {"type": "boolean"}, "refill": {"type": "boolean"}, "auto": {"type": "boolean"},
                                      "refill_elapsed_s": _g03_num("s", 0)}),
                            "Antechamber Control：EVAC/REFILL阀与AUTO序列状态（p.33, p.43-44）"),
    "regen.state": (_g03_obj({"status": _g03_str(["idle", "start", "flow"] + [k for k, _, _ in INERT_REGEN_STEPS]), "step_elapsed_s": _g03_num("s", 0),
                              "since_start_s": _g03_num("s", 0, nullable=True), "last_end_s": _g03_num("s", nullable=True),
                              "column": _g03_num("1", 1, 2, integer=True), "flow_scfh": _g03_num("scfh", 0, 50), "meter_scfh": _g03_num("scfh", 0, 50),
                              "gas_used_scf": _g03_num("scf", 0)}),
                    "Regen Control：再生状态（等待阀门确认/流量确认/五步）、步内计时、上次再生、再生柱、针阀设定flow_scfh、流量计读数meter_scfh（再生气只在流量确认与吹扫步流动）、"
                    "本次再生用去的再生气gas_used_scf（p.28, p.48, p.64）"),
    "regen.timers_min": (_g03_obj({k: _g03_num("min", 0, 999) for k, _, _ in INERT_REGEN_STEPS}), "Regen Timers：五步时长（出厂180/30/180/240/240 min）（p.29）"),
    "alarms.state": (_g03_obj({"o2": _INERT_OBJ, "h2o": _INERT_OBJ, "ante_complete": _INERT_OBJ, "banner": _g03_str(nullable=True),
                               "history": {"type": "array", "items": _INERT_OBJ}}),
                     "Alarms：O2/H2O报警（使能、设定点、状态）、自动过渡舱完成提示、弹出横幅与报警历史（p.35）"),
    "trend.log": (_g03_obj({"o2": {"type": "array"}, "h2o": {"type": "array"}, "newest_min": _g03_num("min", nullable=True)}),
                  "Trend：每分钟记录的O2/H2O读数[分钟, ppm]（p.35-36）"),
    "light.on": ({"type": "boolean"}, "灯罩荧光灯开关（p.18）"),
}
for _in_sid, (_in_schema, _in_meaning) in INERT_DEVICE_SLOTS.items():
    INERT_PURELAB_STATE_SLOTS[_in_sid] = _inert_purelab_slot("1", "derived", _in_schema)
    INERT_PURELAB_STATE_SLOTS[_in_sid]["meaning_zh"] = _in_meaning
_IN_B = {"type": "boolean"}
INERT_DEVICE_ARGS = {   # name: (inputs, optional, outputs, example, description, effects, components, kind)
    "hmi.login": ({"code": _g03_num("1", 0, 99999999, integer=True)}, (), {"user": _g03_str()}, {"code": 7990},
                  "Login User：输入用户密码7990后HMI按钮可用（p.19, p.25）", ["hmi.user"], ["hmi_touchscreen"], "configuration"),
    "hmi.logout": ({}, (), {"user": _g03_str()}, {}, "LOG OUT：回到只读状态屏（p.19）", ["hmi.user"], ["hmi_touchscreen"], "configuration"),
    "config.set": ({"gas": _g03_str(["nitrogen", "argon"]), "column_in_use": _g03_num("1", 1, 2, integer=True), "blower_restart": _IN_B, "pump_timer": _IN_B,
                    "min_sp_pa": _g03_num("Pa", -1500, 1500), "max_sp_pa": _g03_num("Pa", -1500, 1500), "contrast_pct": _g03_num("%", 10, 100)},
                   ("gas", "column_in_use", "blower_restart", "pump_timer", "min_sp_pa", "max_sp_pa", "contrast_pct"), {"config": _INERT_OBJ},
                   {"min_sp_pa": 500.0, "max_sp_pa": 1200.0}, "Glove Box Config设定；工作压力窗口中点同步为pressure.target_gauge_pa（p.34, p.39）",
                   ["system.config", "pressure.target_gauge_pa"], ["hmi_touchscreen", "controller"], "configuration"),
    "pump.set": ({"running": _IN_B}, (), {"running": _IN_B}, {"running": True}, "真空泵START/STOP（PLC输出）（p.27）", ["pump.state"],
                 ["hmi_touchscreen", "service_ports"], "device_command"),
    "blower.configure": ({"speed_pct": _g03_num("%", 0, 100), "o2_speed": _IN_B, "timer": _IN_B, "regen_restart": _IN_B},
                         ("speed_pct", "o2_speed", "timer", "regen_restart"), {"blower": _INERT_OBJ}, {"speed_pct": 50.0},
                         "Blower Control设定：Change Blower Speed 0-100%、O2 Blower Speed、Blower Timer、Regen Blower Restart（p.23, p.31）",
                         ["blower.state"], ["hmi_touchscreen", "circulation_blower"], "configuration"),
    "purge.configure": ({"o2_sp_ppm": _g03_num("ppm", 0, 1000), "o2_level": _IN_B, "timed_min": _g03_num("min", 1, 999)}, ("o2_sp_ppm", "o2_level", "timed_min"),
                        {"purge": _INERT_OBJ}, {"timed_min": 120.0}, "Purge Control设定：O2 Level Purge SP与使能、Timed Purge Duration（p.32）",
                        ["purge.control"], ["hmi_touchscreen", "gas_manifold"], "configuration"),
    "purge.control": ({"action": _g03_str(["timed_start", "timed_stop", "manual_start", "manual_stop"])}, (), {"purge": _INERT_OBJ},
                      {"action": "manual_start"}, "定时/手动吹扫START/STOP：打开自动吹扫阀（需新鲜的惰性气体就绪边界），运行中的风机暂停到吹扫结束（p.32, p.47）",
                      ["purge.control", "purge.enabled", "purification.enabled"], ["hmi_touchscreen", "gas_manifold"], "device_command"),
    "antechamber.configure": ({"number": _g03_num("1", 1, 2, integer=True), "evacuate_sp_pa": _g03_num("Pa", 0, 110000), "refill_s": _g03_num("s", 0, 999),
                               "final_refill_s": _g03_num("s", 0, 999)}, ("number", "evacuate_sp_pa", "refill_s", "final_refill_s"),
                              {"config": _INERT_OBJ}, {"evacuate_sp_pa": 300.0}, "Antechamber Config设定（抽空设定点、回填时长、过渡舱号；只有1号大舱为自动）（p.33-34）",
                              ["antechamber.config"], ["hmi_touchscreen", "antechamber"], "configuration"),
    "antechamber.auto": ({"run": _IN_B}, (), {"active": _IN_B}, {"run": True},
                         "AUTO：按设定循环数抽空到设定点并按回填时长回填，末次按Final Refill；需两门关闭、电源与外部源/气体边界（p.33, p.44）",
                         ["antechamber.control", "antechamber.cycle_active", "antechamber.phase", "antechamber.completed_cycles", "pump.state"],
                         ["hmi_touchscreen", "antechamber"], "device_command"),
    "antechamber.valve": ({"valve": _g03_str(["evac", "refill"]), "open": _IN_B}, (), {"control": _INERT_OBJ}, {"valve": "evac", "open": True},
                          "EVAC/REFILL：手动抽空或用箱内气体回填，再按停止；两门须关闭，AUTO运行时不可用（p.33）",
                          ["antechamber.control", "pump.state"], ["hmi_touchscreen", "antechamber"], "device_command"),
    "regen.control": ({"action": _g03_str(["start", "valves_closed", "flow_ok", "abort"])}, (), {"regen": _INERT_OBJ}, {"action": "start"},
                      "再生：START→确认柱阀已关→调流量后确认Is Flow OK→五步自动进行；开始后5 min内可ABORT（p.28, p.48）",
                      ["regen.state", "pump.state", "purification.enabled"], ["hmi_touchscreen", "purifier_columns"], "device_command"),
    "regen.set_timers": ({k + "_min": _g03_num("min", 0, 999) for k, _, _ in INERT_REGEN_STEPS}, tuple(k + "_min" for k, _, _ in INERT_REGEN_STEPS),
                         {"timers": _INERT_OBJ}, {"heat_min": 180.0}, "Regen Timers：五步时长（min；手册建议勿改出厂值）（p.28-29）", ["regen.timers_min"],
                         ["hmi_touchscreen", "purifier_columns"], "configuration"),
    "regen.set_flow": ({"flow_scfh": _g03_num("scfh", 0, 50)}, (), {"flow_scfh": _g03_num("scfh")}, {"flow_scfh": 28.0},
                       "再生气流量计针阀（25-30 scfh）（p.48）", ["regen.state"], ["regen_flow_valve"], "device_command"),
    "alarms.configure": ({"o2_enabled": _IN_B, "o2_sp_ppm": _g03_num("ppm", 0, 1000), "h2o_enabled": _IN_B, "h2o_sp_ppm": _g03_num("ppm", 0, 1000),
                          "ante_enabled": _IN_B}, ("o2_enabled", "o2_sp_ppm", "h2o_enabled", "h2o_sp_ppm", "ante_enabled"), {"alarms": _INERT_OBJ},
                         {"o2_enabled": True, "o2_sp_ppm": 12.0}, "Alarms设定：O2/H2O报警使能与设定点、自动过渡舱完成提示（p.35）", ["alarms.state"],
                         ["hmi_touchscreen", "controller"], "configuration"),
    "alarms.acknowledge": ({}, (), {"alarms": _INERT_OBJ}, {}, "Acknowledge Alarm：清除当前报警与弹出横幅（p.35）", ["alarms.state"], ["hmi_touchscreen"],
                           "device_command"),
    "trend.clear": ({}, (), {}, {}, "Trend CLR：清除趋势记录（p.36 Fig. 26）", ["trend.log"], ["hmi_touchscreen"], "device_command"),
    "pressure.pedal": ({"direction": _g03_str(["add", "remove"]), "duration_s": _g03_num("s", 0.05, 60)}, (), {}, {"direction": "add", "duration_s": 2.0},
                       "脚踏：右踏板加气、左踏板抽气，箱压保持在工作上下限之间（p.12-13, p.21）", ["pump.state"], ["foot_pedal_right", "foot_pedal_left"],
                       "device_command"),
    "light.set": ({"on": _IN_B}, (), {"on": _IN_B}, {"on": True}, "灯罩顶部的荧光灯开关（p.18）", ["light.on"], ["light_switch"], "device_command"),
}
_INERT_BINDING = "basic40_runtime_vacuum.InertPureLabSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"
_g03_InertHostBase = InertPureLabHost
_g03_InertSessionBase = InertPureLabSession
_INERT_PANEL = {}


def _inert_panel():
    if not _INERT_PANEL:
        engine = _g03_engine()
        caps = set(INERT_PURELAB_CAPABILITY_ARGUMENTS) | set(INERT_DEVICE_ARGS)
        spec = engine.load_spec(INERT_PURELAB_ASSET_DIR / "panel_ui.json", capabilities=caps)
        _INERT_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _INERT_PANEL


def _inert_ppm(value):
    return None if value is None else value * 1e6


class InertPureLabHost(_g03_InertHostBase):
    """CHEM-189 host with the HMI / PLC device layer and the front panel."""

    PANEL_PRINCIPAL = "__inert_panel__"

    def __init__(self, *, scene_adapter=None):
        super().__init__(scene_adapter=scene_adapter)
        self._panels = {}
        self.panel_capture_dir = None
        self._principals[self.PANEL_PRINCIPAL] = ("agent", None)
        self._logs[self.PANEL_PRINCIPAL] = []
        self._panel_session = InertPureLabSession(self, self.PANEL_PRINCIPAL)
        self._panel_caller = None                                           # the session whose panel gesture is running (its principal acts)

    def create_instance(self, instance_id, asset_type_id=INERT_PURELAB_ASSET_TYPE_ID):
        with self._lock:
            super().create_instance(instance_id, asset_type_id)
            inst = self._instances[instance_id]
            inst.state.update(self._inert_initial())
            inst.state["pressure.target_gauge_pa"] = 0.5 * (inst.state["system.config"]["min_sp_pa"] + inst.state["system.config"]["max_sp_pa"])
            inst.g03 = {"power_epoch": 0, "blower_was_on": False}
            if (INERT_PURELAB_ASSET_DIR / "panel_ui.json").is_file():
                self._panels[instance_id] = _inert_panel()["engine"].PanelUI(_inert_panel()["spec"], _G03PanelHost(
                    lambda: self._panel_values(instance_id), lambda cap, args: self._panel_call(instance_id, cap, args), lambda: self.time_s))

    @staticmethod
    def _inert_initial():
        """Power-on defaults: the values of the manual's screen figures (Fig. 6/24, 17, 20, 21, 22-23, 25)."""
        return {"hmi.user": "none",
                "system.config": {"gas": "nitrogen", "column_in_use": 1, "blower_restart": False, "pump_timer": False, "min_sp_pa": 500.0,
                                  "max_sp_pa": 1200.0, "contrast_pct": 100.0},
                "pump.state": {"running": False, "idle_s": 0.0},
                "blower.state": {"speed_set_pct": INERT_BLOWER_DEFAULT_PCT, "speed_pct": 0.0, "mode": "MANUAL", "o2_speed": True, "timer": False,
                                 "regen_restart": False, "since_change_s": 0.0, "purge_hold": False},
                "purge.control": {"o2_sp_ppm": 10.0, "o2_level": False, "timed_min": 120.0, "timed_active": False, "timed_elapsed_s": 0.0,
                                  "manual_active": False, "o2_active": False},
                "antechamber.config": {"number": 1, "evacuate_sp_pa": 100.0, "refill_s": 20.0, "final_refill_s": 60.0},
                "antechamber.control": {"evac": False, "refill": False, "auto": False, "refill_elapsed_s": 0.0},
                "regen.state": {"status": "idle", "step_elapsed_s": 0.0, "since_start_s": None, "last_end_s": None, "column": 1, "flow_scfh": 0.0,
                                "meter_scfh": 0.0, "gas_used_scf": 0.0},
                "regen.timers_min": {k: float(m) for k, _, m in INERT_REGEN_STEPS},
                "alarms.state": {"o2": {"enabled": False, "sp_ppm": 12.0, "state": "NORMAL"}, "h2o": {"enabled": False, "sp_ppm": 12.0, "state": "NORMAL"},
                                 "ante_complete": {"enabled": False, "state": "NORMAL"}, "banner": None, "history": []},
                "trend.log": {"o2": [], "h2o": [], "newest_min": None},
                "light.on": False}

    def panel(self, instance_id):
        return self._panels[instance_id]

    def _panel_values(self, instance_id):
        inst = self._instances[instance_id]
        out = self._public_state(inst.state)
        regen = inst.state["regen.state"]
        out.update({"host.power_epoch": inst.g03["power_epoch"], "host.clock": _g03_clock(self.time_s), "host.time_min": self.time_s / 60.0,
                    "host.regen_label": dict(((k, label) for k, label, _ in INERT_REGEN_STEPS), idle="Idle", start="Start", flow="Flow check").get(regen["status"]),
                    "host.last_regen": None if regen["last_end_s"] is None else _g03_clock(regen["last_end_s"]).replace("T", " ")[:16],
                    "host.history_rows": [dict(h, when=_g03_clock(h["time_s"]).replace("T", " ")) for h in inst.state["alarms.state"]["history"]]})
        return out

    def _panel_call(self, instance_id, capability_id, arguments):
        """A command of the panel firmware runs as the session whose gesture triggered it; without a gesture as the panel's own principal."""
        with self._lock:
            return (self._panel_caller or self._panel_session)._invoke_checked(self._instances[instance_id], capability_id, arguments, None)

    # ---- PLC logic advanced with the host clock
    def step(self, dt_s):
        with self._lock:
            ok = _number(dt_s) and 0 < dt_s <= 3600
            if ok:
                for inst in self._instances.values():
                    self._inert_auto(inst, float(dt_s))
            error = None
            try:
                super().step(dt_s)
            except InertPureLabSceneSynchronizationError as exc:
                error = exc
            if ok:
                failures = []
                for inst in self._instances.values():
                    if getattr(inst, "g03", None) is None:
                        continue
                    seen = self._public_state(inst.state)
                    self._inert_plc(inst, float(dt_s))
                    updates = {k: v for k, v in self._public_state(inst.state).items() if v != seen.get(k)}
                    if updates and self.scene_adapter:
                        try:
                            self.scene_adapter(inst.instance_id, copy.deepcopy(updates))
                        except Exception:
                            failures.append(inst.instance_id)
                if failures and error is None:
                    self._scene_faults.extend({"instance_id": i, "time_s": self.time_s, "phase": "host_step"} for i in failures)
                    error = InertPureLabSceneSynchronizationError("Host time/process committed; one or more scene callbacks failed")
            if error is not None:
                raise error

    def _inert_auto(self, inst, dt):
        """AUTO antechamber sequence (p.33-34, p.44): evacuate until a fresh external boundary sample reaches the Auto Evacuate SP, then refill for
        the Auto Refill Duration (the last refill: Auto Final Refill) while inert gas is ready; count the cycle.  Samples consumed here are not
        seen again by the CHEM-189 boundary logic."""
        s = inst.state
        c = s.get("antechamber.control")
        if c is None or not (s["antechamber.cycle_active"] and c["auto"]):
            return
        b = inst.boundary
        fresh = b is not None and b["sample_time_s"] == self.time_s and b["serial"] > inst.consumed_boundary
        if fresh:
            inst.consumed_boundary = b["serial"]
        cfg = s["antechamber.config"]
        if s["antechamber.phase"] == "evacuating":
            if fresh and b["source_running"] and b["pressure_pa"] <= cfg["evacuate_sp_pa"]:
                s["antechamber.phase"] = "backfilling"
                c["refill_elapsed_s"] = 0.0
            return
        if s["antechamber.phase"] != "backfilling" or b is None or not b["gas_supply_ready"]:
            return
        c["refill_elapsed_s"] += dt
        last = s["antechamber.completed_cycles"] + 1 >= s["antechamber.target_cycles"]
        if c["refill_elapsed_s"] >= (cfg["final_refill_s"] if last else cfg["refill_s"]) - 1e-9:
            s["antechamber.completed_cycles"] += 1
            c["refill_elapsed_s"] = 0.0
            if last:
                s["antechamber.phase"], s["antechamber.cycle_active"], c["auto"] = "completed", False, False
                al = s["alarms.state"]
                if al["ante_complete"]["enabled"]:
                    al["ante_complete"]["state"] = "COMPLETE"
                    self._inert_alarm(inst, "Auto Antechamber Complete")
            else:
                s["antechamber.phase"] = "evacuating"

    def _inert_alarm(self, inst, text):
        al = inst.state["alarms.state"]
        al["banner"] = text
        al["history"] = ([{"time_s": self.time_s, "text": text}] + al["history"])[:50]

    def _inert_plc(self, inst, dt):
        """Time-driven PLC functions after the CHEM-189 step: regeneration steps, timed purge, O2 level purge, blower speed and timer, pump
        timer, alarms, trend points and the working-pressure window (only on the host-supplied box pressure truth)."""
        s = inst.state
        if not s["power.enabled"]:
            return
        self._inert_regen(inst, dt)
        p, bl = s["purge.control"], s["blower.state"]
        o2 = _inert_ppm(s["atmosphere.oxygen_mole_fraction"])
        h2o = _inert_ppm(s["atmosphere.water_mole_fraction"])
        if p["timed_active"]:
            p["timed_elapsed_s"] += dt
            if p["timed_elapsed_s"] >= p["timed_min"] * 60.0 - 1e-9:
                self._inert_purge_close(s, "timed_active")
        gas = inst.boundary is not None and inst.boundary["gas_supply_ready"]
        if p["o2_level"] and o2 is not None:
            if o2 > p["o2_sp_ppm"] and not s["purge.enabled"] and gas:
                self._inert_purge_open(s, "o2_active")
            elif p["o2_active"] and o2 <= p["o2_sp_ppm"]:
                self._inert_purge_close(s, "o2_active")
        if s["purification.enabled"]:
            if bl["speed_set_pct"] != INERT_BLOWER_DEFAULT_PCT:
                bl["since_change_s"] += dt
                if bl["timer"] and bl["since_change_s"] >= INERT_BLOWER_TIMER_S - 1e-9:
                    bl["speed_set_pct"], bl["since_change_s"] = INERT_BLOWER_DEFAULT_PCT, 0.0
        pump = s["pump.state"]
        required = self._inert_pump_required(s)
        if required and not pump["running"]:
            pump["running"], pump["idle_s"] = True, 0.0
        elif pump["running"] and s["system.config"]["pump_timer"] and not required:
            pump["idle_s"] += dt
            if pump["idle_s"] >= INERT_PUMP_TIMER_S - 1e-9:
                pump["running"], pump["idle_s"] = False, 0.0
        al = s["alarms.state"]
        for key, reading, label in (("o2", o2, "O2 Level Alarm"), ("h2o", h2o, "H2O Level Alarm")):
            a = al[key]
            high = a["enabled"] and reading is not None and reading > a["sp_ppm"]
            if high and a["state"] == "NORMAL":
                a["state"] = "ALARM"
                self._inert_alarm(inst, label)
            elif not high and a["state"] == "ACKNOWLEDGED":
                a["state"] = "NORMAL"
        t0, t1 = (self.time_s - dt) / 60.0, self.time_s / 60.0
        marks = [float(m) for m in range(int(math.floor(t0 + 1e-9)) + 1, int(math.floor(t1 + 1e-9)) + 1)][-INERT_TREND_POINTS:]
        if marks and (o2 is not None or h2o is not None):                    # one point per minute mark (readings are constant within a step)
            log = s["trend.log"]
            for key, reading in (("o2", o2), ("h2o", h2o)):
                if reading is not None:
                    log[key] = (log[key] + [[m, round(reading, 3)] for m in marks])[-INERT_TREND_POINTS:]
            log["newest_min"] = marks[-1]
        truth = s["pressure.box_gauge_pa"]
        cfg = s["system.config"]
        if truth is not None:
            if truth > cfg["max_sp_pa"] and pump["running"]:
                s["pressure.box_gauge_pa"] = max(cfg["max_sp_pa"], truth - INERT_PRESSURE_RATE_PA_S * dt)
            elif truth < cfg["min_sp_pa"] and gas:
                s["pressure.box_gauge_pa"] = min(cfg["min_sp_pa"], truth + INERT_PRESSURE_RATE_PA_S * dt)
        self._inert_derived(s)

    @staticmethod
    def _inert_pump_required(s):
        c = s["antechamber.control"]
        return bool(c["evac"] or (c["auto"] and s["antechamber.phase"] == "evacuating") or s["regen.state"]["status"] == "evacuate")

    def _inert_regen(self, inst, dt):
        s = inst.state
        r = s["regen.state"]
        if r["since_start_s"] is not None:
            r["since_start_s"] += dt
        keys = [k for k, _, _ in INERT_REGEN_STEPS]
        left = dt
        if r["status"] == "flow":                                            # the flow check: gas flows until Is Flow OK? is confirmed
            r["gas_used_scf"] += r["flow_scfh"] * dt / 3600.0
        while r["status"] in keys and left > 1e-12:
            need = s["regen.timers_min"][r["status"]] * 60.0 - r["step_elapsed_s"]
            if r["status"] in INERT_REGEN_FLOWING:
                r["gas_used_scf"] += r["flow_scfh"] * min(left, max(0.0, need)) / 3600.0
            if left < need - 1e-9:
                r["step_elapsed_s"] += left
                break
            left -= max(0.0, need)
            i = keys.index(r["status"])
            if i + 1 < len(keys):
                r["status"], r["step_elapsed_s"] = keys[i + 1], 0.0
            else:                                                            # p.48 step 7: done; the blower restarts if enabled
                r.update(status="idle", step_elapsed_s=0.0, since_start_s=None, last_end_s=self.time_s - left)
                if s["blower.state"]["regen_restart"]:
                    s["purification.enabled"] = True
        self._inert_meter(r)

    @staticmethod
    def _inert_meter(r):
        """The regeneration flow meter shows the needle-valve flow while the regen gas valves are open (p.48, p.64), else 0."""
        r["meter_scfh"] = r["flow_scfh"] if r["status"] in INERT_REGEN_FLOWING else 0.0

    @staticmethod
    def _inert_purge_open(s, flag):
        s["purge.control"][flag] = True
        s["purge.enabled"] = True
        if s["purification.enabled"]:                                         # p.47: a purge switches the blower off until it is complete
            s["purification.enabled"] = False
            s["blower.state"]["purge_hold"] = True

    @staticmethod
    def _inert_purge_close(s, flag):
        p = s["purge.control"]
        p[flag] = False
        if not (p["timed_active"] or p["manual_active"] or p["o2_active"]):
            s["purge.enabled"] = False
            if s["blower.state"]["purge_hold"]:
                s["blower.state"]["purge_hold"] = False
                if s["power.enabled"]:
                    s["purification.enabled"] = True

    @staticmethod
    def _inert_derived(s):
        """Shown blower speed and mode, and the purge flags that follow a purge valve closed by a direct command."""
        bl, p = s["blower.state"], s["purge.control"]
        o2 = _inert_ppm(s["atmosphere.oxygen_mole_fraction"])
        boost = bl["o2_speed"] and o2 is not None and o2 > INERT_O2_BLOWER_PPM
        bl["speed_pct"] = 0.0 if not s["purification.enabled"] else 100.0 if boost else bl["speed_set_pct"]
        bl["mode"] = "AUTO" if (boost and s["purification.enabled"]) else "MANUAL"
        if not s["purge.enabled"]:
            p["timed_active"] = p["manual_active"] = p["o2_active"] = False


INERT_RUN_TOUCHES = {"antechamber.cycle.start": ("antechamber",), "antechamber.cycle.stop": ("antechamber",), "antechamber.auto": ("antechamber",),
                     "antechamber.valve": ("antechamber",), "antechamber.set_cycles": ("antechamber",), "antechamber.configure": ("antechamber",),
                     "regen.control": ("regen",), "regen.set_timers": ("regen",), "purification.set": ("regen",), "pump.set": ("antechamber", "regen"),
                     "purge.control": ("purge",), "purge.set": ("purge",), "purge.configure": ("purge",)}


def _inert_runs(s):
    """The runs of the glovebox: an antechamber cycle (automatic or with the manual valves), a regeneration and a purge (each owned by the
    agent that started it)."""
    c = s["antechamber.control"]
    return {"antechamber": bool(s["antechamber.cycle_active"] or c["evac"] or c["refill"]), "regen": s["regen.state"]["status"] != "idle",
            "purge": bool(s["purge.enabled"])}


def _inert_touches(cap, args):
    """power.set is the main power switch of the PLC box (hard: physical, never guarded; the PLC outputs go off as on a power loss)."""
    return INERT_RUN_TOUCHES.get(cap, ())


class InertPureLabSession(_g03_InertSessionBase):
    """CHEM-189 session plus the HMI / PLC device commands and the panel.* commands."""

    @property
    def _host(self):
        return self._InertPureLabSession__host

    @property
    def _visibility(self):
        return self._host._principals[self._InertPureLabSession__principal_id][0]

    def describe(self, *, asset_type_id=None, instance_id=None):
        response = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if response.get("error") is None and "capabilities" in response:
            evaluator = self._visibility in ("debug", "evaluator")
            meta = export_inert_purelab_contract_metadata()["asset_types"][INERT_PURELAB_ASSET_TYPE_ID]
            slots = response["state_slots"]
            for group in ("device_capabilities", "panel_capabilities"):
                for name, cap in meta[group].items():
                    if cap["visibility"] == "agent" or evaluator:
                        cap = copy.deepcopy(cap)
                        cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in slots]
                        cap["observes"] = [sid for sid in cap["observes"] if sid in slots]
                        response["capabilities"][name] = cap
            response["asset_version"] = INERT_PURELAB_ASSET_VERSION
        return response

    def _invoke_checked(self, inst, cap, args, request_id):
        """Run ownership (g03_owner_refuses) before the command; a run the command starts belongs to this session's principal.  The
        antechamber doors, the pressure pedals and the regeneration needle valve are physical and not guarded."""
        g, me, vis = getattr(inst, "g03", None), self._InertPureLabSession__principal_id, self._visibility
        if g is not None and g03_owner_refuses(g, _inert_runs(inst.state), _inert_touches(cap, args), me, vis, InertPureLabHost.PANEL_PRINCIPAL):
            return self._error(inst.instance_id, "ACCESS_DENIED", request_id)
        before = _inert_runs(inst.state) if g is not None else None
        response = self._inert_invoke(inst, cap, args, request_id)
        live = self._host._instances.get(inst.instance_id)
        if response["status"] == "succeeded" and getattr(live, "g03", None) is not None:
            g03_owner_note(live.g03, before, _inert_runs(live.state), me, vis, InertPureLabHost.PANEL_PRINCIPAL)
        return response

    def _inert_invoke(self, inst, cap, args, request_id):
        if cap in INERT_DEVICE_ARGS or cap.startswith("panel."):
            return self._device_checked(inst, cap, args, request_id)
        g = getattr(inst, "g03", None)
        was_on = inst.state["purification.enabled"]
        response = super()._invoke_checked(inst, cap, args, request_id)
        if response["status"] != "succeeded" or g is None:
            return response
        host = self._host
        cur = host._instances[inst.instance_id]                            # CHEM-189 commits a new instance object
        s = cur.state
        if cap == "power.set":
            if args["enabled"] != inst.state["power.enabled"]:
                cur.g03["power_epoch"] += 1
                if not args["enabled"]:                                      # power off: PLC outputs off, HMI back to the status screen
                    cur.g03["blower_was_on"] = was_on
                    s["pump.state"].update(running=False, idle_s=0.0)
                    s["regen.state"].update(status="idle", step_elapsed_s=0.0, since_start_s=None, meter_scfh=0.0)
                    s["antechamber.control"].update(evac=False, refill=False, auto=False)
                    s["blower.state"]["purge_hold"] = False
                    s["hmi.user"] = "none"
                elif s["system.config"]["blower_restart"] and cur.g03["blower_was_on"]:
                    s["purification.enabled"] = True                         # p.34: blower restart after a power failure
            host._inert_derived(s)
        elif cap == "pressure.set":                                          # the window keeps its width around the new target
            cfg = s["system.config"]
            half = 0.5 * (cfg["max_sp_pa"] - cfg["min_sp_pa"])
            lo = min(max(args["pressure_pa"] - half, -1500.0), 1500.0)
            hi = min(max(args["pressure_pa"] + half, -1500.0), 1500.0)
            cfg.update(min_sp_pa=lo, max_sp_pa=hi)
        elif cap == "antechamber.cycle.start":
            s["antechamber.control"].update(auto=False, evac=False, refill=False)
        elif cap == "antechamber.cycle.stop":
            s["antechamber.control"]["auto"] = False
        elif cap in ("purification.set", "purge.set"):
            host._inert_derived(s)
        return response

    def _fail(self, inst, code, rid):
        return self._error(inst.instance_id, code, rid)

    def _device_checked(self, inst, cap, args, rid):
        host = self._host
        if cap.startswith("panel."):
            meta = _inert_panel()["caps"] if (INERT_PURELAB_ASSET_DIR / "panel_ui.json").is_file() else {}
            if cap not in meta: return self._fail(inst, "UNKNOWN_CAPABILITY", rid)
            if meta[cap]["visibility"] != "agent" and self._visibility not in ("debug", "evaluator"): return self._fail(inst, "ACCESS_DENIED", rid)
            if _g03_check(meta[cap]["input_schema"], args): return self._fail(inst, "INVALID_ARGUMENT", rid)
            if self._InertPureLabSession__principal_id == InertPureLabHost.PANEL_PRINCIPAL or inst.instance_id not in host._panels:
                return self._fail(inst, "ACCESS_DENIED", rid)
            previous, host._panel_caller = host._panel_caller, self                # the commands of this gesture act as this session
            try:
                result, code = g03_panel_gesture(host._panels[inst.instance_id], cap, args, instance_id=inst.instance_id, time_s=host.time_s,
                                                 capture_dir=host.panel_capture_dir)
            finally:
                host._panel_caller = previous
            if code: return self._fail(inst, code, rid)
            return self._envelope(inst.instance_id, rid, result=result)
        inputs, optional = INERT_DEVICE_ARGS[cap][0], INERT_DEVICE_ARGS[cap][1]
        if _g03_check(_g03_obj(inputs, optional), args): return self._fail(inst, "INVALID_ARGUMENT", rid)
        cand = copy.deepcopy(inst)
        try:
            result = getattr(self, "_in_" + cap.replace(".", "_"))(cand, args)
        except _InertRefused as exc:
            return self._fail(inst, exc.code, rid)
        host._inert_derived(cand.state)
        try:
            host._commit(inst, cand)
        except Exception:
            return self._error(inst.instance_id, "DEVICE_ERROR", rid, "failed")
        return self._envelope(inst.instance_id, rid, result=result)

    # ---- device commands (work on a candidate copy; a refusal leaves the instance unchanged)
    @staticmethod
    def _need_power(c):
        if not c.state["power.enabled"]:
            raise _InertRefused("PRECONDITION_FAILED")

    def _in_hmi_login(self, c, a):
        self._need_power(c)
        if a["code"] != INERT_LOGIN_CODE:
            raise _InertRefused("INVALID_ARGUMENT")
        c.state["hmi.user"] = "user"
        return {"user": "user"}

    def _in_hmi_logout(self, c, a):
        c.state["hmi.user"] = "none"
        return {"user": "none"}

    def _in_config_set(self, c, a):
        cfg = c.state["system.config"]
        new = dict(cfg, **a)
        if new["min_sp_pa"] >= new["max_sp_pa"]:
            raise _InertRefused("INVALID_ARGUMENT")
        if new["column_in_use"] != 1:                                        # single-column purifier (p.34, p.49: dual columns only)
            raise _InertRefused("PRECONDITION_FAILED")
        cfg.update(new)
        c.state["pressure.target_gauge_pa"] = 0.5 * (new["min_sp_pa"] + new["max_sp_pa"])
        return {"config": copy.deepcopy(cfg)}

    def _in_pump_set(self, c, a):
        self._need_power(c)
        if not a["running"] and self._host._inert_pump_required(c.state):
            raise _InertRefused("BUSY")
        c.state["pump.state"].update(running=a["running"], idle_s=0.0)
        return {"running": a["running"]}

    def _in_blower_configure(self, c, a):
        bl = c.state["blower.state"]
        if "speed_pct" in a and float(a["speed_pct"]) != bl["speed_set_pct"]:
            bl["since_change_s"] = 0.0
        bl.update({k: (float(v) if k == "speed_pct" else v) for k, v in a.items() if k != "speed_pct"})
        if "speed_pct" in a:
            bl["speed_set_pct"] = float(a["speed_pct"])
        return {"blower": copy.deepcopy(bl)}

    def _in_purge_configure(self, c, a):
        p = c.state["purge.control"]
        p.update({k: (float(v) if k in ("o2_sp_ppm", "timed_min") else v) for k, v in a.items()})
        if not p["o2_level"] and p["o2_active"]:
            self._host._inert_purge_close(c.state, "o2_active")
        return {"purge": copy.deepcopy(p)}

    def _in_purge_control(self, c, a):
        s, p, host = c.state, c.state["purge.control"], self._host
        flag = "timed_active" if a["action"].startswith("timed") else "manual_active"
        if a["action"].endswith("_start"):
            self._need_power(c)
            b = c.boundary
            if not b or b["sample_time_s"] != host.time_s or not b["gas_supply_ready"]:
                raise _InertRefused("PRECONDITION_FAILED")                  # same condition as purge.set: current inert-gas boundary
            if flag == "timed_active":
                p["timed_elapsed_s"] = 0.0
            host._inert_purge_open(s, flag)
        elif p[flag]:
            host._inert_purge_close(s, flag)
        return {"purge": copy.deepcopy(p)}

    def _in_antechamber_configure(self, c, a):
        cfg = c.state["antechamber.config"]
        if a.get("number", 1) != 1:                                          # only the large antechamber has automatic control (datasheet)
            raise _InertRefused("PRECONDITION_FAILED")
        if c.state["antechamber.cycle_active"]:
            raise _InertRefused("BUSY")
        cfg.update({k: (float(v) if k != "number" else v) for k, v in a.items()})
        return {"config": copy.deepcopy(cfg)}

    def _in_antechamber_auto(self, c, a):
        s, ctl, host = c.state, c.state["antechamber.control"], self._host
        if not a["run"]:
            if s["antechamber.cycle_active"]:
                s["antechamber.cycle_active"], s["antechamber.phase"] = False, "stopped"
            ctl["auto"] = False
            return {"active": False}
        if s["antechamber.cycle_active"]:
            raise _InertRefused("BUSY")
        b = c.boundary
        if (not s["power.enabled"] or s["antechamber.inner_door_open"] or s["antechamber.outer_door_open"] or not s["vacuum.source_instance_id"]
                or not b or b["sample_time_s"] != host.time_s or not b["source_running"] or not b["gas_supply_ready"]
                or b["pressure_pa"] <= s["antechamber.config"]["evacuate_sp_pa"]):
            raise _InertRefused("PRECONDITION_FAILED")
        s.update({"antechamber.cycle_active": True, "antechamber.completed_cycles": 0, "antechamber.phase": "evacuating"})
        ctl.update(auto=True, evac=False, refill=False, refill_elapsed_s=0.0)
        c.consumed_boundary = c.boundary_serial
        s["pump.state"].update(running=True, idle_s=0.0)                   # p.34: the pump is powered on as required
        return {"active": True}

    def _in_antechamber_valve(self, c, a):
        s, ctl = c.state, c.state["antechamber.control"]
        if a["open"]:
            self._need_power(c)
            if ctl["auto"] or s["antechamber.cycle_active"]:
                raise _InertRefused("BUSY")
            if s["antechamber.inner_door_open"] or s["antechamber.outer_door_open"]:
                raise _InertRefused("PRECONDITION_FAILED")
            other = "refill" if a["valve"] == "evac" else "evac"
            ctl[other] = False
            if a["valve"] == "evac":
                s["pump.state"].update(running=True, idle_s=0.0)
        ctl[a["valve"]] = a["open"]
        return {"control": copy.deepcopy(ctl)}

    def _in_regen_control(self, c, a):
        s, r = c.state, c.state["regen.state"]
        act = a["action"]
        if act == "start":
            self._need_power(c)
            if r["status"] != "idle":
                raise _InertRefused("BUSY")
            if s["purification.enabled"]:                                    # p.48 step 2: turn off the blower first (single column)
                raise _InertRefused("PRECONDITION_FAILED")
            r.update(status="start", step_elapsed_s=0.0, since_start_s=0.0, gas_used_scf=0.0)
        elif act == "valves_closed":
            if r["status"] != "start":
                raise _InertRefused("PRECONDITION_FAILED")
            r["status"] = "flow"
        elif act == "flow_ok":
            if r["status"] != "flow":
                raise _InertRefused("PRECONDITION_FAILED")
            r.update(status=INERT_REGEN_STEPS[0][0], step_elapsed_s=0.0)
        else:
            if r["status"] == "idle" or (r["since_start_s"] or 0.0) > INERT_REGEN_ABORT_S + 1e-9:
                raise _InertRefused("PRECONDITION_FAILED")                  # p.28: abort only within the first 5 minutes
            r.update(status="idle", step_elapsed_s=0.0, since_start_s=None)
        self._host._inert_meter(r)
        return {"regen": copy.deepcopy(r)}

    def _in_regen_set_timers(self, c, a):
        if c.state["regen.state"]["status"] != "idle":
            raise _InertRefused("BUSY")
        c.state["regen.timers_min"].update({k[:-4]: float(v) for k, v in a.items()})
        return {"timers": copy.deepcopy(c.state["regen.timers_min"])}

    def _in_regen_set_flow(self, c, a):
        c.state["regen.state"]["flow_scfh"] = float(a["flow_scfh"])
        self._host._inert_meter(c.state["regen.state"])
        return {"flow_scfh": float(a["flow_scfh"])}

    def _in_alarms_configure(self, c, a):
        al = c.state["alarms.state"]
        for key in ("o2", "h2o"):
            if f"{key}_enabled" in a:
                al[key]["enabled"] = a[f"{key}_enabled"]
                if not a[f"{key}_enabled"]:
                    al[key]["state"] = "NORMAL"
            if f"{key}_sp_ppm" in a:
                al[key]["sp_ppm"] = float(a[f"{key}_sp_ppm"])
        if "ante_enabled" in a:
            al["ante_complete"]["enabled"] = a["ante_enabled"]
        return {"alarms": copy.deepcopy(al)}

    def _in_alarms_acknowledge(self, c, a):
        al = c.state["alarms.state"]
        for key in ("o2", "h2o"):
            if al[key]["state"] == "ALARM":
                al[key]["state"] = "ACKNOWLEDGED"
        al["ante_complete"]["state"] = "NORMAL"
        al["banner"] = None
        return {"alarms": copy.deepcopy(al)}

    def _in_trend_clear(self, c, a):
        c.state["trend.log"] = {"o2": [], "h2o": [], "newest_min": None}
        return {}

    def _in_pressure_pedal(self, c, a):
        """p.12-13: the right pedal adds working gas, the left removes gas (vacuum pump), within the working set points.  Acts on the
        host-supplied box pressure truth; the indicated pressure still comes only from the host."""
        s = c.state
        self._need_power(c)
        cfg = s["system.config"]
        if a["direction"] == "add" and not (c.boundary and c.boundary["gas_supply_ready"]):
            raise _InertRefused("PRECONDITION_FAILED")
        if a["direction"] == "remove" and not s["pump.state"]["running"]:
            raise _InertRefused("PRECONDITION_FAILED")
        truth = s["pressure.box_gauge_pa"]
        if truth is not None:
            delta = INERT_PRESSURE_RATE_PA_S * a["duration_s"]
            s["pressure.box_gauge_pa"] = min(truth + delta, max(truth, cfg["max_sp_pa"])) if a["direction"] == "add" else \
                max(truth - delta, min(truth, cfg["min_sp_pa"]))
        return {}

    def _in_light_set(self, c, a):
        c.state["light.on"] = a["on"]
        return {"on": a["on"]}


class _InertRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


_g03_inert_export_base = export_inert_purelab_contract_metadata


def export_inert_purelab_contract_metadata():
    """CHEM-189 metadata plus the HMI / PLC device layer: device-layer slots, device_capabilities, panel_capabilities (when the panel spec
    exists), parameters and limitations."""
    meta = _g03_inert_export_base()
    t = meta["asset_types"][INERT_PURELAB_ASSET_TYPE_ID]
    t["asset_version"] = INERT_PURELAB_ASSET_VERSION
    dev = {}
    for name, (inputs, optional, outputs, example, desc, effects, components, kind) in INERT_DEVICE_ARGS.items():
        dev[name] = g03_device_capability(name, kind, desc, inputs, outputs, example, components, effects, optional=optional, binding=_INERT_BINDING,
                                          completion="同步提交；再生步骤、定时吹扫、泵定时、报警与趋势由宿主step推进")
    t["device_capabilities"] = dev
    t["panel_capabilities"] = {}
    if (INERT_PURELAB_ASSET_DIR / "panel_ui.json").is_file():
        spec = _inert_panel()["spec"]
        called = _g03_engine().spec_capabilities(spec)
        effects = list(dict.fromkeys(e["state_id"] for c in called for e in (t["capabilities"].get(c) or dev.get(c) or {}).get("state_effects", [])
                                     if e["timing"] == "immediate" and INERT_PURELAB_STATE_SLOTS.get(e["state_id"], {}).get("visibility") == "agent"))
        t["panel_capabilities"] = g03_panel_capabilities(spec, _INERT_BINDING, effects)
    t["parameters"].update({
        "hmi_user_code": {"value": INERT_LOGIN_CODE, "unit": "1", "evidence": "manual", "source": "p.19, p.25"},
        "regen_default_step_min": {"value": {k: m for k, _, m in INERT_REGEN_STEPS}, "unit": "min", "evidence": "manual", "source": "p.29 Fig. 17"},
        "regen_abort_window_s": {"value": INERT_REGEN_ABORT_S, "unit": "s", "evidence": "manual", "source": "p.28"},
        "vacuum_pump_timer_s": {"value": INERT_PUMP_TIMER_S, "unit": "s", "evidence": "manual", "source": "p.34"},
        "blower_timer_s": {"value": INERT_BLOWER_TIMER_S, "unit": "s", "evidence": "manual", "source": "p.31"},
        "o2_blower_speed_threshold_ppm": {"value": INERT_O2_BLOWER_PPM, "unit": "ppm", "evidence": "manual", "source": "p.31"},
        "panel_pressure_rate_pa_s": {"value": INERT_PRESSURE_RATE_PA_S, "unit": "Pa/s", "evidence": "engineering_assumption",
                                     "source": "pedal and working-window gas in / out rate"},
    })
    meta["limitations"] = list(meta["limitations"]) + INERT_G03_LIMITATIONS
    return meta


INERT_G03_LIMITATIONS = [
    "Run ownership (lead rule 2026-10-19, a simulation rule): an antechamber cycle, regeneration or purge started by one agent belongs to it; "
    "another agent cannot stop or change it (antechamber control, valves and settings, vacuum pump, regeneration, blower, purge) on the panel or "
    "the direct path (ACCESS_DENIED, state unchanged, message box with our text).  The main power switch (power.set), doors, foot pedals and the "
    "regeneration needle valve are physical (lead clarification 10-19) and not restricted; switching the power off acts as a power loss (PLC "
    "outputs off).  The evaluator is not restricted.",
    "HMI / PLC device layer (operating manual 2012): login 7990, pump and blower START / STOP, blower settings, regeneration prompts and five timed "
    "steps (regen.control; regeneration.start keeps its CHEM-189 'unsupported' answer and regeneration.status its constant), purge control, AUTO "
    "antechamber (evacuate to the set point on fresh external boundary samples, time-based refills), system configuration, alarms, trend, foot "
    "pedals and light switch follow the manual as a minimal device model, not the PLC program.",
    "No new physics: O2 / H2O / pressure readings still come only from the trusted host; the blower speed, purges and regeneration do not change "
    "purity.  The working-pressure window and the foot pedals move only the host-supplied box pressure truth (1 mbar/s assumption, pump output "
    "for removal, the last external boundary's gas-ready flag for addition).  The pump output is the PLC command; whether the external source runs "
    "is still reported by host boundaries.",
    "Configuration: single purifier column, one automatic (large) antechamber, automatic purge valve, O2 and H2O analyzers; not fitted: solvent "
    "regeneration, freezer, box cooling, heated antechamber, parallel-piped boxes.  Circuit breakers, the hand purge valve, the mini antechamber "
    "3-way valve and the O2 analyzer valves are not modelled.",
    "Regeneration gas: the needle valve sets the flow meter reading (regen.state.flow_scfh); the gas flows, and the meter shows it, only during the "
    "flow check and the Heat & Purge / Purging steps (p.48, p.64), and the forming gas used is counted (gas_used_scf). The PLC has no flow sensor, "
    "so 'Is Flow OK?' is the operator's confirmation and is accepted at any flow (as on the real HMI); the effect of the flow on the column "
    "regeneration needs regeneration kinetics, which this model does not have.",
    "Working Gas Type (Nitrogen / Argon) is stored, shown on the Glove Box Config screen and reported in system.config; the manual (p.34) documents "
    "no effect of the selection and the host gas boundary carries no gas identity, so it changes nothing else (effect not documented).",
]
# <<< G03 CHEM-189 PureLab HE 2GB front panel


# >>> G03 CHEM-188 PC 3001 VARIO select front panel
# Device layer of the VACUU·SELECT controller (operating manual references/manuals/vacuubrand_pc3001_vario_select/
# operating_manual__vacuu_select_controller_v1_13.pdf, 'p.N' = PDF page = printed page; pumping-unit manual ..._series_v2.pdf 'pump p.N';
# Modbus / RS-232 manual 'comm p.N'): rocker switch and ON/OFF button (switch on with a 30 s start, lock, switch off, reboot), applications
# made of process steps (pump down, vacuum control, ramp, vent, hold vacuum, automatic boiling point, loop, turbo backing pump, VACUU·LAN) with a
# parameter list, set-point changes, VENT (brief / to atmosphere) with the sensor's venting valve, favorites, application editor, settings
# (display, sounds, regional, basic settings, VACUU·BUS activation, sensor adjustment, date / time, factory reset), data logger, service
# counters and the fault indicator with acknowledgement.  The CHEM-188 commands keep their meaning; an application drives them (mode, target,
# start) and the VARIO speed limit scales the pumping speed.
PC3001_ASSET_VERSION = "1.3.0"  # 1.3.0 (2026-10-21): plain black operating-panel front in the 3D model (printed header / footer / LED removed; geometry only)
PC3001_ASSET_DIR = G03_ASSET_ROOT / "vacuubrand_pc3001"
PC3001_MANUAL = "references/manuals/vacuubrand_pc3001_vario_select/operating_manual__vacuu_select_controller_v1_13.pdf"
PC3001_PUMP_MANUAL = "references/manuals/vacuubrand_pc3001_vario_select/operating_manual__pc3001_vario_select_series_v2.pdf"
PC3001_BOOT_S = 30.0                       # pump p.51: the process display appears after approx. 30 seconds
PC3001_VENT_M3_S = 2e-4                    # assumption: venting valve conductance (air in = C * (P_ambient - P))
PC3001_BRIEF_VENT_S = 0.5                  # assumption: a short VENT press opens the valve for 0.5 s (p.51: vent briefly; control continues)
PC3001_SENSOR_MAX_PA = 110000.0            # assumption: sensor range end 1100 mbar (overpressure above)
PC3001_MIN_SPEED_PCT = 7.0                 # datasheet: motor speed 200-3000 min-1 (200 / 3000 = 6.7 %)
PC3001_UNITS = {"mbar": 0.01, "hPa": 0.01, "Torr": 0.00750062}
PC3001_STEP_TYPES = {    # p.63-69 editor palette, comm p.11 process steps (elementary / optional parameters)
    "pump_down": ("Pump down", ("speed_pct", "duration_s")),
    "vacuum_control": ("Vacuum control", ("setpoint_pa", "speed_pct", "duration_s")),
    "ramp": ("Ramp", ("setpoint_pa", "duration_s")),
    "vent": ("Vent", ("setpoint_pa",)),
    "hold_vacuum": ("Hold vacuum", ("setpoint_pa", "speed_pct", "duration_s")),
    "auto_boiling": ("Automatic boiling point", ("speed_pct", "duration_s")),
    "loop": ("Loop", ("count",)),
    "turbo_backing": ("Turbo backing pump", ("speed_pct", "duration_s")),
    "vacuu_lan": ("VACUU-LAN", ("setpoint_pa", "duration_s")),
}
PC3001_PARAMS = {   # name: (label, schema, optional)
    "setpoint_pa": ("Pressure set-point", _g03_num("Pa", 200, 110000), False),
    "speed_pct": ("Speed", _g03_num("%", PC3001_MIN_SPEED_PCT, 100), False),
    "duration_s": ("Duration", _g03_num("s", 1, 359999, nullable=True), True),
    "count": ("Repetitions", _g03_num("1", 1, 99, integer=True), False),
}


def _pc_step(kind, **params):
    return {"type": kind, "params": {p: params.get(p, {"speed_pct": 100.0, "duration_s": None, "count": 2}.get(p)) for p in PC3001_STEP_TYPES[kind][1]}}


PC3001_STANDARD_APPS = [   # comm p.10: standard application IDs available with a VARIO pump and a VS-C sensor; step contents are assumptions
    (0, "Pump down", [_pc_step("pump_down")]),
    (1, "Automatic evaporation", [_pc_step("auto_boiling")]),
    (2, "Application example 1 (automatic evaporation)", [_pc_step("auto_boiling", duration_s=3600.0), _pc_step("vent", setpoint_pa=110000.0)]),
    (3, "Vacuum drying", [_pc_step("pump_down")]),
    (4, "Pump down and hold", [_pc_step("pump_down", duration_s=600.0), _pc_step("hold_vacuum", setpoint_pa=1000.0)]),
    (5, "Filtration", [_pc_step("vacuum_control", setpoint_pa=50000.0)]),
    (6, "Vacuum control", [_pc_step("vacuum_control", setpoint_pa=10000.0)]),
    (7, "Turbo backing pump", [_pc_step("turbo_backing")]),
    (8, "Vacuum concentrator", [_pc_step("vacuum_control", setpoint_pa=2000.0)]),
    (9, "Gel drying", [_pc_step("ramp", setpoint_pa=5000.0, duration_s=600.0), _pc_step("hold_vacuum", setpoint_pa=5000.0, duration_s=3600.0),
                       _pc_step("vent", setpoint_pa=110000.0)]),
    (12, "VACUU-LAN", [_pc_step("vacuu_lan", setpoint_pa=10000.0)]),
]
PC3001_LOGGER_INTERVALS = {"off": None, "1 s": 1.0, "10 s": 10.0, "1 min": 60.0, "10 min": 600.0}   # assumption: interval choices
PC3001_VOLUMES = ["Off", "Low", "Normal", "High"]                      # assumption: volume steps (p.70 Fig.: Normal)
PC3001_LANGUAGES = ["English", "Deutsch", "Français", "Español", "Italiano", "Português", "Nederlands", "Polski", "Türkçe"]   # assumption: list not printed
PC3001_SETTINGS_SCHEMA = {
    "brightness_pct": _g03_num("%", 10, 100), "orientation": _g03_num("deg", 0, 270, integer=True), "handedness": _g03_str(["right", "left"]),
    "energy_saving": _g03_str(["Off", "5 min", "15 min", "30 min"]), "touch_volume": _g03_str(PC3001_VOLUMES), "error_volume": _g03_str(PC3001_VOLUMES),
    "error_repeat": _g03_str(["Off", "5 seconds", "30 seconds", "60 seconds"]),
    "language": _g03_str(PC3001_LANGUAGES), "unit": _g03_str(list(PC3001_UNITS)), "autostart": {"type": "boolean"}, "sensor": _g03_str(["VS-C 1"]),
    "vent_on_setpoint": {"type": "boolean"}, "vent_valve_active": {"type": "boolean"}, "logger_interval": _g03_str(list(PC3001_LOGGER_INTERVALS)),
    "logger_separator": _g03_str(["point", "comma"]), "logger_period": _g03_str(["1 day", "7 days", "30 days"]), "diagnostics": _g03_str(["minimal", "complete"]),
    "maintenance": {"type": "boolean"}, "maintenance_h": _g03_num("h", 1, 99999), "clock_offset_s": _g03_num("s"),
}
_PC_OBJ = {"type": "object"}
PC3001_DEVICE_SLOTS = {   # sid: (schema, meaning, visibility)
    "unit.power": (_g03_obj({"mains": {"type": "boolean"}, "controller": _g03_str(["off", "booting", "on"]), "boot_left_s": _g03_num("s", 0),
                             "locked": {"type": "boolean"}, "resume": {"type": "boolean"}}),
                   "摇臂开关（主电源）与控制器ON/OFF：off/启动中（约30 s）/on、触摸屏锁定、断电前运行（自动启动续行）（pump p.51, p.41）", "agent"),
    "app.library": ({"type": "array", "items": _PC_OBJ}, "应用库：标准应用ID 0-99与自建应用ID≥100；名称、描述、收藏、过程步骤及参数（comm p.9-11, p.53-69）", "agent"),
    "app.selected": (_g03_num("1", 0, 9999, integer=True), "所选应用ID（p.53）", "agent"),
    "app.run": (_g03_obj({"running": {"type": "boolean"}, "app_id": _g03_num("1", 0, 9999, integer=True, nullable=True),
                          "step": _g03_num("1", 0, integer=True), "step_elapsed_s": _g03_num("s", 0), "elapsed_s": _g03_num("s", 0),
                          "loops_done": _g03_num("1", 0, integer=True), "ramp_start_pa": _g03_num("Pa", 0, nullable=True), "waiting": {"type": "boolean"}}),
                "应用运行：当前过程步骤、步内/总计时、循环计数、等待“Continue with”（p.50, p.53-57）", "agent"),
    "vent.state": (_g03_obj({"mode": _g03_str(["closed", "brief", "atmosphere", "setpoint", "step"]), "brief_left_s": _g03_num("s", 0)}),
                   "传感器集成通气阀状态：短通气/通至大气/设定点上调通气/通气步骤（p.51, p.56, p.71）", "agent"),
    "settings.values": (_g03_obj(PC3001_SETTINGS_SCHEMA), "设置：显示方向、提示音量、语言与压力单位、自动启动、传感器、设定点通气、通气阀激活、数据记录、诊断、保养提醒、时钟（p.43, p.70-85）", "agent"),
    "sensor.adjustment": (_g03_obj({"k": _g03_num("1", 0.5, 2.0), "z_off_pa": _g03_num("Pa", -10000, 10000), "atm_ref_pa": _g03_num("Pa", nullable=True),
                                    "vac_ref_pa": _g03_num("Pa", nullable=True)}),
                          "真空传感器调整：大气点（>700 mbar）增益与真空点（<20 mbar，0或0.1-20 mbar参考）零点，作用于显示读数（p.78-82）", "agent"),
    "sensor.error": (_g03_obj({"gain": _g03_num("1", -0.5, 0.5), "offset_pa": _g03_num("Pa", -10000, 10000)}), "评测者：传感器原始读数误差（相对增益、偏移），默认0", "evaluator"),
    "faults.list": ({"type": "array", "items": _PC_OBJ}, "故障指示：警告（黄，过程继续，排除后自动复位）与错误（红，过程停止，排除后须确认）（p.86-90）", "agent"),
    "faults.injected": (_g03_obj({"code": _g03_str(["none", "pump", "sensor", "communication", "venting_valve"])}), "评测者：注入的故障原因，默认none", "evaluator"),
    "graph.log": ({"type": "array", "items": {"type": "array"}}, "压力曲线：应用启动后的[秒, Pa]读数（p.59）", "agent"),
    "logger.state": (_g03_obj({"records": _g03_num("1", 0, integer=True), "files": _g03_num("1", 0, integer=True)}), "数据记录器：已存记录数与过程文件数（p.83）", "agent"),
    "service.hours_s": (_g03_num("s", 0), "运行小时计数（泵运转时间）（p.84）", "agent"),
    "editor.draft": (_g03_obj({"name": _g03_str(max_len=40), "description": _g03_str(max_len=120), "steps": {"type": "array", "items": _PC_OBJ},
                               "source_id": _g03_num("1", 0, 9999, integer=True, nullable=True)}),
                     "应用编辑器草稿：名称、描述、过程步骤（模板、新建、保存、另存为）（p.63-69）", "agent"),
}
for _pc_sid, (_pc_schema, _pc_meaning, _pc_vis) in PC3001_DEVICE_SLOTS.items():
    STATE_SLOTS[_pc_sid] = _slot(_pc_schema.get("x-unit", "1"), "derived", _pc_schema, visibility=_pc_vis)
    STATE_SLOTS[_pc_sid]["meaning_zh"] = _pc_meaning
_PC_B = {"type": "boolean"}
_PC_STEP_PARAM_SCHEMA = {"anyOf": [{"type": "number", "x-unit": "1", "description": "in the unit of the named parameter: Pa, %, s or 1"}, {"type": "null"}]}
PC3001_DEVICE_ARGS = {   # name: (inputs, optional, outputs, example, description, effects, components, kind)
    "mains.set": ({"on": _PC_B}, (), {"on": _PC_B}, {"on": True}, "泵组摇臂开关I/0：断开即断电；自动启动开启时接通后控制器自行启动并续行（pump p.51, p.71）",
                  ["unit.power", "power.enabled", "vacuum.running"], ["rocker_switch"], "device_command"),
    "controller.power": ({"on": _PC_B}, (), {"controller": _g03_str()}, {"on": True}, "控制器ON/OFF：短按开机（约30 s后进入过程画面），按住约3 s并确认关机（p.41, pump p.51）",
                         ["unit.power", "power.enabled", "vacuum.running", "app.run"], ["onoff_button"], "device_command"),
    "controller.lock": ({"locked": _PC_B}, (), {"locked": _PC_B}, {"locked": True}, "运行中短按ON/OFF锁定/解锁触摸屏（p.41-42）", ["unit.power"],
                        ["onoff_button"], "device_command"),
    "controller.reboot": ({}, (), {"controller": _g03_str()}, {}, "按住ON/OFF约10 s重启控制器（应用停止）（p.41, p.89）", ["unit.power", "app.run", "vacuum.running"],
                          ["onoff_button"], "device_command"),
    "app.select": ({"app_id": _g03_num("1", 0, 9999, integer=True)}, (), {"app_id": _g03_num("1")}, {"app_id": 6}, "选择应用（运行中不可换）（p.53, p.61）",
                   ["app.selected"], ["display"], "configuration"),
    "app.start": ({}, (), {"running": _PC_B}, {}, "Start：按所选应用的过程步骤运行（只在过程画面；需供电、外部腔体、无红色错误）（p.51, p.53）",
                  ["app.run", "vacuum.running", "vacuum.mode", "vacuum.target_pressure_pa", "graph.log"], ["display", "pump_module"], "device_command"),
    "app.stop": ({}, (), {"running": _PC_B}, {}, "Stop：停止应用（任何时候可用）（p.51, p.57）", ["app.run", "vacuum.running"], ["display"], "device_command"),
    "app.continue": ({}, (), {"step": _g03_num("1")}, {}, "Continue with [步骤]：进入下一过程步骤（p.50）", ["app.run"], ["display"], "device_command"),
    "app.set_parameter": ({"app_id": _g03_num("1", 0, 9999, integer=True), "step": _g03_num("1", 0, 99, integer=True), "param": _g03_str(list(PC3001_PARAMS)),
                           "value": _PC_STEP_PARAM_SCHEMA}, (), {"value": _PC_STEP_PARAM_SCHEMA}, {"app_id": 6, "step": 0, "param": "speed_pct", "value": 80.0},
                          "参数列表：修改应用某步骤的参数（运行中亦可，立即生效；可选参数可设OFF=null）（p.57-58, comm p.11）",
                          ["app.library", "vacuum.target_pressure_pa"], ["display"], "configuration"),
    "app.set_setpoint": ({"pressure_pa": _g03_num("Pa", 200, 110000)}, (), {"pressure_pa": _g03_num("Pa")}, {"pressure_pa": 8000.0},
                         "过程画面设定点（数字显示键盘、步进按钮、箭头标记）：改当前步骤设定点（p.49, p.54-55）",
                         ["app.library", "vacuum.target_pressure_pa", "vent.state"], ["display"], "configuration"),
    "vent.control": ({"action": _g03_str(["brief", "atmosphere", "stop"])}, (), {"mode": _g03_str()}, {"action": "brief"},
                     "VENT：短按短暂通气（控制继续），按住>2 s通至大气（泵停），通气时再按停止；需通气阀已激活（p.51, p.56）",
                     ["vent.state", "app.run", "vacuum.running"], ["display", "venting_valve"], "device_command"),
    "favorites.set": ({"app_id": _g03_num("1", 0, 9999, integer=True), "favorite": _PC_B}, (), {"favorite": _PC_B}, {"app_id": 6, "favorite": True},
                      "应用上下文菜单：加入/移出收藏（p.62）", ["app.library"], ["display"], "configuration"),
    "editor.edit": ({"action": _g03_str(["new", "template", "add", "remove", "param", "describe", "move"]), "app_id": _g03_num("1", 0, 9999, integer=True),
                     "step_type": _g03_str(list(PC3001_STEP_TYPES)), "index": _g03_num("1", 0, 99, integer=True), "to": _g03_num("1", 0, 99, integer=True),
                     "param": _g03_str(list(PC3001_PARAMS)), "value": _PC_STEP_PARAM_SCHEMA, "text": _g03_str(max_len=120)},
                    ("app_id", "step_type", "index", "to", "param", "value", "text"), {"draft": _PC_OBJ}, {"action": "new"},
                    "应用编辑器：新建、按模板、在Process end前加入步骤、移除、调整顺序、设步骤参数、写描述（p.63-69）", ["editor.draft"], ["display"], "configuration"),
    "editor.save": ({"name": _g03_str(max_len=40)}, ("name",), {"app_id": _g03_num("1")}, {"name": "My application"},
                    "保存（覆盖自建应用）或另存为（新名称，ID自100起）（p.64-68, comm p.9）", ["app.library", "editor.draft"], ["display"], "configuration"),
    "settings.configure": (dict(PC3001_SETTINGS_SCHEMA), tuple(PC3001_SETTINGS_SCHEMA), {"settings": _PC_OBJ}, {"unit": "Torr"},
                           "设置：显示/声音/区域（单位）/基本设置/VACUU·BUS组件激活/数据记录/诊断/保养提醒（p.43, p.70-85）", ["settings.values"],
                           ["display"], "configuration"),
    "settings.factory_reset": ({}, (), {}, {}, "恢复出厂：删除自建应用与设置，数据记录关闭，诊断设为Minimal（p.73）",
                               ["settings.values", "app.library", "app.selected", "sensor.adjustment", "logger.state"], ["display"], "configuration"),
    "clock.set": ({"date": _g03_str(max_len=10), "time": _g03_str(max_len=8)}, (), {"clock": _g03_str()}, {"date": "2026-03-01", "time": "08:30"},
                  "管理：设置日期与时间（p.72）", ["settings.values"], ["display"], "configuration"),
    "sensor.adjust": ({"reference_pa": _g03_num("Pa", 0, 110000)}, (), {"adjustment": _PC_OBJ}, {"reference_pa": 101300.0},
                      "传感器调整：当前读数>700 mbar时按大气参考值调增益，<20 mbar时按0（零点）或0.1-20 mbar参考值调零点；其他范围输入框不可用（p.78-82）",
                      ["sensor.adjustment", "vacuum.indicated_pressure_pa"], ["display", "controller"], "configuration"),
    "sensor.configure_error": ({"gain": _g03_num("1", -0.5, 0.5), "offset_pa": _g03_num("Pa", -10000, 10000)}, (), {"error": _PC_OBJ},
                               {"gain": 0.0, "offset_pa": 0.0}, "评测者：传感器原始读数误差，默认0（理想）", ["sensor.error", "vacuum.indicated_pressure_pa"],
                               ["controller"], "configuration"),
    "fault.inject": ({"code": _g03_str(["none", "pump", "sensor", "communication", "venting_valve", "frequency_converter"])}, (), {"code": _g03_str()},
                     {"code": "none"}, "评测者：注入故障原因（泵、真空传感器、通讯、通气阀、泵的变频器），none为排除（p.88-90）", ["faults.injected", "faults.list"],
                     ["controller"], "configuration"),
    "fault.acknowledge": ({}, (), {"faults": {"type": "array"}}, {}, "确认故障信息：原因已排除的错误被复位（p.86-87）", ["faults.list"], ["display"], "device_command"),
}
PC3001_EVALUATOR_CAPS = ("sensor.configure_error", "fault.inject")
PC3001_FAULT_TEXT = {"pump": ("error", "Error at pump"), "sensor": ("error", "Error at vacuum sensor"), "communication": ("error", "Communication error"),
                     "frequency_converter": ("error", "Frequency converter (FC) error"),
                     "venting_valve": ("warning", "Venting valve does not operate"), "overpressure": ("warning", "Overpressure"),
                     "underrange": ("warning", "Underrange"), "maintenance": ("warning", "Maintenance due")}   # p.88-90 error names
_PC3001_BINDING = "basic40_runtime_vacuum.VacuumSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"
_g03_VacuumHostBase = VacuumHost
_g03_VacuumSessionBase = VacuumSession
_PC3001_PANEL = {}


def _pc3001_panel():
    if not _PC3001_PANEL:
        engine = _g03_engine()
        caps = set(CAPABILITY_ARGUMENTS) | set(PC3001_DEVICE_ARGS)
        spec = engine.load_spec(PC3001_ASSET_DIR / "panel_ui.json", capabilities=caps)
        _PC3001_PANEL.update(engine=engine, spec=spec, caps=engine.capability_definitions(spec))
    return _PC3001_PANEL


def _pc_library():
    return [{"id": i, "name": n, "description": "", "standard": True, "favorite": False, "steps": copy.deepcopy(s)} for i, n, s in PC3001_STANDARD_APPS]


def _pc_defaults():
    return {"brightness_pct": 80.0, "orientation": 0, "handedness": "right", "energy_saving": "Off", "touch_volume": "Normal", "error_volume": "Normal",
            "error_repeat": "5 seconds", "language": "English", "unit": "mbar", "autostart": False, "sensor": "VS-C 1",
            "vent_on_setpoint": False, "vent_valve_active": True, "logger_interval": "off", "logger_separator": "point", "logger_period": "1 day",
            "diagnostics": "minimal", "maintenance": False, "maintenance_h": 1000.0, "clock_offset_s": 0.0}


class _PcRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class VacuumHost(_g03_VacuumHostBase):
    """CHEM-188 host with the VACUU·SELECT device layer (applications, venting valve, VARIO speed, sensor adjustment, faults) and the panel."""

    PANEL_PRINCIPAL = "__pc3001_panel__"

    def __init__(self, *, ambient_pressure_pa=101325.0, scene_adapter=None):
        super().__init__(ambient_pressure_pa=ambient_pressure_pa, scene_adapter=scene_adapter)
        self._panels = {}
        self.panel_capture_dir = None
        self._principals[self.PANEL_PRINCIPAL] = ("agent", None)
        self._logs[self.PANEL_PRINCIPAL] = []
        self._panel_session = VacuumSession(self, self.PANEL_PRINCIPAL)
        self._panel_caller = None                                           # the session whose panel gesture is running (its principal acts)

    def create_instance(self, instance_id, asset_type_id=ASSET_TYPE_ID):
        with self._lock:
            adapter, self.scene_adapter = self.scene_adapter, None
            try:
                super().create_instance(instance_id, asset_type_id)
            finally:
                self.scene_adapter = adapter
            inst = self._instances[instance_id]
            inst.state.update(self._pc_initial())
            inst.g03 = {"power_epoch": 0, "next_id": 100, "last_graph_s": None, "logger_s": 0.0}
            if self.scene_adapter:
                self.scene_adapter(instance_id, copy.deepcopy(inst.state))
            if (PC3001_ASSET_DIR / "panel_ui.json").is_file():
                self._panels[instance_id] = _pc3001_panel()["engine"].PanelUI(_pc3001_panel()["spec"], _G03PanelHost(
                    lambda: self._panel_values(instance_id), lambda cap, args: self._panel_call(instance_id, cap, args), lambda: self.time_s))

    @staticmethod
    def _pc_initial():
        return {"unit.power": {"mains": False, "controller": "off", "boot_left_s": 0.0, "locked": False, "resume": False},
                "app.library": _pc_library(), "app.selected": 6,
                "app.run": {"running": False, "app_id": None, "step": 0, "step_elapsed_s": 0.0, "elapsed_s": 0.0, "loops_done": 0, "ramp_start_pa": None,
                            "waiting": False},
                "vent.state": {"mode": "closed", "brief_left_s": 0.0}, "settings.values": _pc_defaults(),
                "sensor.adjustment": {"k": 1.0, "z_off_pa": 0.0, "atm_ref_pa": None, "vac_ref_pa": None},
                "sensor.error": {"gain": 0.0, "offset_pa": 0.0}, "faults.list": [], "faults.injected": {"code": "none"}, "graph.log": [],
                "logger.state": {"records": 0, "files": 0}, "service.hours_s": 0.0,
                "editor.draft": {"name": "New application", "description": "", "steps": [], "source_id": None}}

    def panel(self, instance_id):
        return self._panels[instance_id]

    # ---- values the panel firmware reads
    def _panel_values(self, instance_id):
        inst = self._instances[instance_id]
        s = inst.state
        out = {sid: copy.deepcopy(v) for sid, v in s.items() if STATE_SLOTS[sid]["visibility"] == "agent"}
        app = self._pc_app(s, s["app.selected"]) or {}
        run = s["app.run"]
        cur = self._pc_current_step(s)
        unit = s["settings.values"]["unit"]
        faults = s["faults.list"]
        level = "red" if any(f["level"] == "error" for f in faults) else "yellow" if faults else "gray"
        rows = []
        for i, st in enumerate(app.get("steps", [])):
            rows.append({"kind": "head", "step": i, "param": None, "text": f"{i + 1}  {PC3001_STEP_TYPES[st['type']][0]}",
                         "active": bool(run["running"] and run["app_id"] == app.get("id") and run["step"] == i)})
            for p in PC3001_STEP_TYPES[st["type"]][1]:
                rows.append({"kind": p, "step": i, "param": p, "text": "    " + PC3001_PARAMS[p][0], "value_text": self._pc_param_text(p, st["params"].get(p), unit),
                             "value": st["params"].get(p), "active": rows[-1]["active"] if rows else False})
        draft_rows = []
        for i, st in enumerate(s["editor.draft"]["steps"]):
            draft_rows.append({"kind": "head", "step": i, "param": None, "text": f"{i + 1}  {PC3001_STEP_TYPES[st['type']][0]}"})
            for p in PC3001_STEP_TYPES[st["type"]][1]:
                draft_rows.append({"kind": p, "step": i, "param": p, "text": "    " + PC3001_PARAMS[p][0], "value_text": self._pc_param_text(p, st["params"].get(p), unit),
                                   "value": st["params"].get(p)})
        nxt = None
        if run["running"] and run["waiting"]:
            a = self._pc_app(s, run["app_id"])
            if a and run["step"] + 1 < len(a["steps"]):
                nxt = PC3001_STEP_TYPES[a["steps"][run["step"] + 1]["type"]][0]
        out.update({"host.power_epoch": inst.g03["power_epoch"], "host.clock": _g03_clock(self.time_s + s["settings.values"]["clock_offset_s"]),
                    "host.app_name": app.get("name"), "host.app_id": app.get("id"), "host.app_description": app.get("description") or "",
                    "host.setpoint_pa": None if cur is None else cur["params"].get("setpoint_pa"), "host.setpoint_step": cur is not None and "setpoint_pa" in cur["params"]
                    and cur["type"] != "vent", "host.step_label": None if cur is None else PC3001_STEP_TYPES[cur["type"]][0],
                    "host.param_rows": rows, "host.draft_rows": draft_rows, "host.fault_level": level, "host.next_step": nxt,
                    "host.fault_text": "; ".join(f["text"] for f in faults) or None, "host.unit_scale": PC3001_UNITS[unit],
                    "host.chamber": inst.chamber_id is not None, "host.ambient_pa": self.ambient_pressure_pa,
                    "host.favorites": [a for a in s["app.library"] if a["favorite"]],
                    "host.graph": [[p[0] / 60.0, p[1] * PC3001_UNITS[unit]] for p in s["graph.log"]],
                    "host.step_rows": [r for r in rows if r["kind"] == "head"], "host.hours_h": s["service.hours_s"] / 3600.0,
                    "host.fault_unack": "; ".join(f["text"] for f in faults if not f["acknowledged"]) or None,
                    "host.own_apps": [a for a in s["app.library"] if not a["standard"]],
                    "host.adjust_range": self._pc_adjust_range(s)})
        return out

    @staticmethod
    def _pc_param_text(p, v, unit):
        if v is None:
            return "OFF"
        if p == "setpoint_pa":
            return f"{v * PC3001_UNITS[unit]:.1f} {unit}" if v < 110000 else "ATM"
        if p == "speed_pct":
            return f"{v:.0f} %"
        if p == "duration_s":
            t = int(round(v))
            return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"
        return str(int(v))

    def _pc_adjust_range(self, s):
        raw = self._pc_raw(s)
        if raw is None:
            return None
        return "atmosphere" if raw > 70000.0 else "vacuum" if raw < 2000.0 else None

    def _panel_call(self, instance_id, capability_id, arguments):
        """A command of the panel firmware runs as the session whose gesture triggered it; without a gesture as the panel's own principal."""
        with self._lock:
            return (self._panel_caller or self._panel_session)._invoke_checked(self._instances[instance_id], capability_id, arguments, None)

    # ---- applications
    @staticmethod
    def _pc_app(s, app_id):
        return next((a for a in s["app.library"] if a["id"] == app_id), None)

    def _pc_current_step(self, s):
        run = s["app.run"]
        app = self._pc_app(s, run["app_id"] if run["running"] else s["app.selected"])
        if not app or not app["steps"]:
            return None
        return app["steps"][min(run["step"] if run["running"] else 0, len(app["steps"]) - 1)]

    def _pc_drive(self, inst):
        """Set the CHEM-188 controls from the running process step."""
        s = inst.state
        run = s["app.run"]
        if not run["running"]:
            return
        app = self._pc_app(s, run["app_id"])
        st = app["steps"][run["step"]]
        kind, p = st["type"], st["params"]
        if kind in ("pump_down", "auto_boiling", "turbo_backing"):
            s["vacuum.mode"], s["vacuum.running"] = "pump_down", True
        elif kind in ("vacuum_control", "hold_vacuum", "vacuu_lan"):
            s["vacuum.mode"], s["vacuum.running"] = "pressure_control", True
            s["vacuum.target_pressure_pa"] = float(min(p["setpoint_pa"], self.ambient_pressure_pa))
        elif kind == "ramp":
            start = run["ramp_start_pa"] if run["ramp_start_pa"] is not None else p["setpoint_pa"]
            frac = min(1.0, run["step_elapsed_s"] / p["duration_s"]) if p["duration_s"] else 1.0
            s["vacuum.mode"], s["vacuum.running"] = "pressure_control", True
            s["vacuum.target_pressure_pa"] = float(min(max(200.0, start + (p["setpoint_pa"] - start) * frac), self.ambient_pressure_pa))
        elif kind == "vent":
            s["vacuum.running"] = False
            if s["vent.state"]["mode"] != "step":
                s["vent.state"]["mode"] = "step"
        elif kind == "loop":
            pass

    def _pc_speed(self, s):
        run = s["app.run"]
        if not run["running"]:
            return 1.0
        st = self._pc_app(s, run["app_id"])["steps"][run["step"]]
        return float(st["params"].get("speed_pct") or 100.0) / 100.0

    def _pc_enter_step(self, inst, index):
        s = inst.state
        run = s["app.run"]
        app = self._pc_app(s, run["app_id"])
        while True:
            if index >= len(app["steps"]):
                self._pc_end(inst)
                return
            st = app["steps"][index]
            if st["type"] == "loop":                                         # repeat from the first step (comm p.11 Loop)
                if run["loops_done"] + 1 < st["params"]["count"]:
                    run["loops_done"] += 1
                    index = 0
                    continue
                run["loops_done"] = 0
                index += 1
                continue
            break
        run.update(step=index, step_elapsed_s=0.0, waiting=False, ramp_start_pa=self._pc_shown(s))   # a ramp starts from the reading
        if st["type"] != "vent" and s["vent.state"]["mode"] == "step":
            s["vent.state"]["mode"] = "closed"
        self._pc_drive(inst)

    def _pc_end(self, inst):
        s = inst.state
        s["app.run"].update(running=False, waiting=False)
        s["vacuum.running"] = False
        if s["vent.state"]["mode"] == "step":
            s["vent.state"]["mode"] = "closed"

    def _pc_step_done(self, inst):
        """Duration over: the next step, or 'Continue with' waits when the step has no duration and another step follows."""
        s = inst.state
        run = s["app.run"]
        app = self._pc_app(s, run["app_id"])
        st = app["steps"][run["step"]]
        d = st["params"].get("duration_s")
        if st["type"] == "vent":
            return False
        if d is not None and run["step_elapsed_s"] >= d - 1e-9:
            self._pc_enter_step(inst, run["step"] + 1)
            return True
        if d is None and run["step"] + 1 < len(app["steps"]):
            run["waiting"] = True
        return False

    # ---- physics hooks
    def _advance_chamber(self, chamber, inst, dt):
        """The CHEM-188 solver with the VARIO speed limit f: dP/dt with f*Q equals the unscaled solver on f*dt with leak/f (same gas totals)."""
        f = self._pc_speed(inst.state) if inst is not None and getattr(inst, "g03", None) is not None else 1.0
        if f >= 1.0 - 1e-12 or not (inst.state["power.enabled"] and inst.state["vacuum.running"]):
            return super()._advance_chamber(chamber, inst, dt)
        leak = chamber.leak_throughput_pa_m3_s
        chamber.leak_throughput_pa_m3_s = leak / f
        try:
            super()._advance_chamber(chamber, inst, dt * f)
        finally:
            chamber.leak_throughput_pa_m3_s = leak

    def _pc_raw(self, s):
        p = s["vacuum.pressure_pa"]
        if p is None:
            return None
        e = s.get("sensor.error") or {"gain": 0.0, "offset_pa": 0.0}
        return p * (1.0 + e["gain"]) + e["offset_pa"]

    def _pc_shown(self, s):
        """The pressure the controller reads: raw sensor value (evaluator error) with the stored adjustment."""
        raw = self._pc_raw(s)
        if raw is None:
            return None
        a = s["sensor.adjustment"]
        return (raw - a["z_off_pa"]) * a["k"]

    @staticmethod
    def _pc_true_of(s, shown_pa):
        """Chamber pressure at which the controller reads shown_pa (inverse of the sensor model); exact with the defaults."""
        e, a = s.get("sensor.error") or {"gain": 0.0, "offset_pa": 0.0}, s["sensor.adjustment"]
        if a["k"] <= 0 or 1.0 + e["gain"] <= 0:
            return shown_pa
        return (shown_pa / a["k"] + a["z_off_pa"] - e["offset_pa"]) / (1.0 + e["gain"])

    @staticmethod
    def _floor(inst):
        """The CHEM-188 floor and target; the controller regulates on its own reading, so the set point is the chamber pressure at which the
        sensor (with its error and the stored adjustment) shows the set point."""
        ultimate, target = _g03_VacuumHostBase._floor(inst)
        s = inst.state
        if getattr(inst, "g03", None) is None or s["vacuum.mode"] == "pump_down":
            return ultimate, target
        return ultimate, max(ultimate, VacuumHost._pc_true_of(s, s["vacuum.target_pressure_pa"]))

    def _refresh(self, inst):
        super()._refresh(inst)
        s = inst.state
        if getattr(inst, "g03", None) is None:
            return
        raw = self._pc_raw(s)
        if raw is not None:
            a = s["sensor.adjustment"]
            shown = (raw - a["z_off_pa"]) * a["k"]
            if shown != s["vacuum.pressure_pa"]:
                s["vacuum.indicated_pressure_pa"] = float(round(max(0.0, shown)))
        f = self._pc_speed(s)
        if f < 1.0 - 1e-12 and s["vacuum.pumping_speed_m3_s"] > 0 and s["vacuum.pressure_pa"]:
            ultimate, _ = self._floor(inst)
            cap = f * MAXIMUM_PUMPING_SPEED_M3_S * max(0.0, 1 - ultimate / s["vacuum.pressure_pa"])
            s["vacuum.pumping_speed_m3_s"] = min(s["vacuum.pumping_speed_m3_s"], cap)
            s["vacuum.indicated_speed_ratio"] = round(s["vacuum.pumping_speed_m3_s"] / MAXIMUM_PUMPING_SPEED_M3_S, 2)

    def _pc_vent(self, inst, dt):
        """Venting valve: air from the ambient into the external chamber (assumed conductance); stops at its level."""
        s = inst.state
        v = s["vent.state"]
        chamber = self._chambers.get(inst.chamber_id)
        if v["mode"] == "closed" or chamber is None:
            return
        if not s["settings.values"]["vent_valve_active"]:
            step = v["mode"] == "step"
            v["mode"] = "closed"
            if step and s["app.run"]["running"]:                             # assumption: a vent step without an activated valve is skipped
                self._pc_enter_step(inst, s["app.run"]["step"] + 1)
            return
        if s["faults.injected"]["code"] == "venting_valve":                  # the valve does not operate (p.90)
            if v["mode"] != "step":
                v["mode"] = "closed"
            return
        amb = self.ambient_pressure_pa
        level = {"brief": amb, "atmosphere": amb, "setpoint": self._pc_true_of(s, s["vacuum.target_pressure_pa"])}.get(v["mode"])
        if v["mode"] == "step":                                              # vent to a set point: until the reading shows it
            run = s["app.run"]
            level = min(amb, self._pc_true_of(s, self._pc_app(s, run["app_id"])["steps"][run["step"]]["params"]["setpoint_pa"])) if run["running"] else amb
        t = min(dt, v["brief_left_s"]) if v["mode"] == "brief" else dt
        p0 = chamber.pressure_pa
        p1 = amb - (amb - p0) * math.exp(-PC3001_VENT_M3_S * t / chamber.volume_m3)
        p1 = min(p1, max(level, p0))
        chamber.pressure_pa = p1
        chamber.admitted_gas_pa_m3 += (p1 - p0) * chamber.volume_m3
        if v["mode"] == "brief":
            v["brief_left_s"] = max(0.0, v["brief_left_s"] - t)
            if v["brief_left_s"] <= 1e-9:
                v["mode"] = "closed"
        elif p1 >= min(level, amb) * (1 - 1e-6) - 1e-9:
            done_step = v["mode"] == "step"
            v["mode"] = "closed"
            if done_step and s["app.run"]["running"]:
                self._pc_enter_step(inst, s["app.run"]["step"] + 1)

    def step(self, dt_s):
        with self._lock:
            if not _number(dt_s) or not 0 <= dt_s <= 3600:
                raise ValueError("Host dt_s must be finite, numeric and in 0..3600 seconds")
            if dt_s == 0:
                return
            left = float(dt_s)
            while left > 1e-12:
                chunk = min(left, self._pc_next_event())
                for inst in self._instances.values():
                    if getattr(inst, "g03", None) is not None:
                        self._pc_pre(inst, chunk)
                super().step(chunk)
                for inst in self._instances.values():
                    if getattr(inst, "g03", None) is not None:
                        self._pc_post(inst, chunk)
                left -= chunk

    def _pc_next_event(self):
        t = math.inf
        for inst in self._instances.values():
            if getattr(inst, "g03", None) is None:
                continue
            s = inst.state
            if s["unit.power"]["controller"] == "booting":
                t = min(t, max(1e-6, s["unit.power"]["boot_left_s"]))
            if s["vent.state"]["mode"] != "closed":
                t = min(t, 1.0)
            run = s["app.run"]
            if run["running"]:
                t = min(t, 5.0)                                              # pressure-graph points every 5 s while an application runs
                st = self._pc_app(s, run["app_id"])["steps"][run["step"]]
                if st["type"] == "ramp":
                    t = min(t, 1.0)
                d = st["params"].get("duration_s")
                if d is not None and st["type"] != "vent":
                    t = min(t, max(1e-6, d - run["step_elapsed_s"]))
        return t

    def _pc_pre(self, inst, dt):
        s = inst.state
        self._pc_drive(inst)
        shown = self._pc_shown(s)
        if (s["settings.values"]["vent_on_setpoint"] and s["vacuum.running"] and s["vacuum.mode"] == "pressure_control" and s["vent.state"]["mode"] == "closed"
                and shown is not None and shown < s["vacuum.target_pressure_pa"] - max(1.0, 0.01 * s["vacuum.target_pressure_pa"])):
            s["vent.state"]["mode"] = "setpoint"                             # p.71: venting valve responds for a set-point increase
        self._pc_vent(inst, dt)
        self._refresh(inst)

    def _pc_post(self, inst, dt):
        s = inst.state
        pw = s["unit.power"]
        if pw["controller"] == "booting":
            pw["boot_left_s"] = max(0.0, pw["boot_left_s"] - dt)
            if pw["boot_left_s"] <= 1e-9:
                pw["controller"] = "on"
                s["power.enabled"] = True
                inst.g03["power_epoch"] += 1
                if pw["resume"] and s["settings.values"]["autostart"] and s["app.run"]["app_id"] is not None:
                    self._pc_start(inst, s["app.run"]["app_id"])           # p.71 autostart: a started application continues
                pw["resume"] = False
        run = s["app.run"]
        if run["running"]:
            run["step_elapsed_s"] += dt
            run["elapsed_s"] += dt
            self._pc_step_done(inst)
        if s["vacuum.running"] and s["power.enabled"]:
            s["service.hours_s"] += dt
        log, g = s["logger.state"], inst.g03
        iv = PC3001_LOGGER_INTERVALS[s["settings.values"]["logger_interval"]]
        if iv and run["running"]:
            g["logger_s"] += dt
            n = int(g["logger_s"] // iv)
            log["records"] += n
            g["logger_s"] -= n * iv
        if run["running"] and s["vacuum.indicated_pressure_pa"] is not None and (g["last_graph_s"] is None or run["elapsed_s"] - g["last_graph_s"] >= 5.0 - 1e-9):
            s["graph.log"] = (s["graph.log"] + [[round(run["elapsed_s"], 3), s["vacuum.indicated_pressure_pa"]]])[-2000:]
            g["last_graph_s"] = run["elapsed_s"]
        self._pc_faults(inst)
        self._refresh(inst)

    def _pc_start(self, inst, app_id):
        s = inst.state
        s["app.run"].update(running=True, app_id=app_id, step=0, step_elapsed_s=0.0, elapsed_s=0.0, loops_done=0, waiting=False, ramp_start_pa=None)
        s["graph.log"] = []
        inst.g03["last_graph_s"] = None
        inst.g03["logger_s"] = 0.0
        if PC3001_LOGGER_INTERVALS[s["settings.values"]["logger_interval"]]:
            s["logger.state"]["files"] += 1                                   # p.83: one data file per process, start to stop
        self._pc_enter_step(inst, 0)
        if s["app.run"]["running"] and s["vacuum.indicated_pressure_pa"] is not None:
            s["graph.log"] = [[0.0, s["vacuum.indicated_pressure_pa"]]]
            inst.g03["last_graph_s"] = 0.0

    def _pc_causes(self, s):
        causes = set()
        code = s["faults.injected"]["code"]
        if code != "none":
            causes.add(code)
        raw = self._pc_raw(s)
        if raw is not None:
            a = s["sensor.adjustment"]
            shown = (raw - a["z_off_pa"]) * a["k"]
            if shown > PC3001_SENSOR_MAX_PA:
                causes.add("overpressure")
            if shown < 0:
                causes.add("underrange")
        st = s["settings.values"]
        if st["maintenance"] and s["service.hours_s"] >= st["maintenance_h"] * 3600.0:
            causes.add("maintenance")
        return causes

    def _pc_faults(self, inst):
        """Fault indicator (p.86): injected causes give their error / warning; overpressure / underrange from the shown reading; the
        maintenance reminder after the set operating hours.  Errors stop the process and stay until acknowledged after the remedy; warnings
        reset when the cause is gone."""
        s = inst.state
        causes = self._pc_causes(s)
        faults = [f for f in s["faults.list"] if f["code"] in causes or f["level"] == "error"]
        known = {f["code"] for f in faults}
        for c in sorted(causes - known):
            level, text = PC3001_FAULT_TEXT[c]
            faults.append({"code": c, "level": level, "text": text, "acknowledged": False})
        if faults != s["faults.list"]:
            s["faults.list"] = faults
        if any(f["level"] == "error" and f["code"] in causes for f in faults) and (s["app.run"]["running"] or s["vacuum.running"]):
            self._pc_end(inst)                                               # p.86: red errors stop the process


PC3001_RUN_GUARDED = ("app.start", "app.stop", "app.continue", "app.set_parameter", "app.set_setpoint", "app.select", "vacuum.start", "vacuum.stop",
                      "vacuum.set_pressure", "vacuum.set_mode", "vent.control", "sensor.adjust", "settings.configure", "settings.factory_reset",
                      "editor.save", "controller.reboot")   # stop / change the process (plus power, controller and mains off)


def _pc_runs(s):
    """The process of the controller (an application run or the CHEM-188 pump run): one channel, owned by the agent that started it."""
    return {"process": bool(s["app.run"]["running"] or s["vacuum.running"])}


def _pc_touches(cap, args):
    """The rocker (mains.set) is a hard switch: physical, never guarded (the process ends as on a power loss).  The controller ON/OFF key
    (controller.power) and the CHEM-188 power.set (switches the controller off, the mains stay on) are soft power controls: guarded."""
    off = (cap == "power.set" and args.get("enabled") is False) or (cap == "controller.power" and args.get("on") is False)
    return ("process",) if cap in PC3001_RUN_GUARDED or off else ()


class VacuumSession(_g03_VacuumSessionBase):
    """CHEM-188 session plus the VACUU·SELECT device commands and the panel.* commands."""

    @property
    def _host(self):
        return self._VacuumSession__host

    @property
    def _visibility(self):
        return self._host._principals[self._VacuumSession__principal_id][0]

    def describe(self, *, asset_type_id=None, instance_id=None):
        response = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if response.get("error") is None and "capabilities" in response:
            evaluator = self._visibility in ("debug", "evaluator")
            meta = export_contract_metadata()["asset_types"][ASSET_TYPE_ID]
            slots = response["state_slots"]
            for group in ("device_capabilities", "panel_capabilities"):
                for name, cap in meta[group].items():
                    if cap["visibility"] == "agent" or evaluator:
                        cap = copy.deepcopy(cap)
                        cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in slots]
                        cap["observes"] = [sid for sid in cap["observes"] if sid in slots]
                        response["capabilities"][name] = cap
            response["asset_version"] = PC3001_ASSET_VERSION
        return response

    def _invoke_checked(self, inst, cap, args, request_id):
        """Run ownership (g03_owner_refuses) before the command; a process the command starts belongs to this session's principal.  The gas
        ballast (a valve on the pump head) is physical and not guarded."""
        g, me, vis = getattr(inst, "g03", None), self._VacuumSession__principal_id, self._visibility
        if g is not None and g03_owner_refuses(g, _pc_runs(inst.state), _pc_touches(cap, args), me, vis, VacuumHost.PANEL_PRINCIPAL):
            return self._error(inst.instance_id, "ACCESS_DENIED", request_id)
        before = _pc_runs(inst.state) if g is not None else None
        response = self._pc_invoke(inst, cap, args, request_id)
        live = self._host._instances.get(inst.instance_id)
        if response["status"] == "succeeded" and getattr(live, "g03", None) is not None:
            g03_owner_note(live.g03, before, _pc_runs(live.state), me, vis, VacuumHost.PANEL_PRINCIPAL)
        return response

    def _pc_invoke(self, inst, cap, args, request_id):
        if cap in PC3001_DEVICE_ARGS or cap.startswith("panel."):
            return self._device_checked(inst, cap, args, request_id)
        g = getattr(inst, "g03", None)
        was = inst.state["power.enabled"]
        response = super()._invoke_checked(inst, cap, args, request_id)
        if response["status"] != "succeeded" or g is None:
            return response
        s, host = inst.state, self._host
        if cap == "power.set":                                               # direct power: mains and controller together, no start screen
            if args["enabled"]:
                s["unit.power"].update(mains=True, controller="on", boot_left_s=0.0)
            else:
                s["unit.power"]["controller"] = "off"
                host._pc_end(inst)
                s["vent.state"]["mode"] = "closed"
            if args["enabled"] != was:
                g["power_epoch"] += 1
        elif cap == "vacuum.stop":
            host._pc_end(inst)
        elif cap == "vacuum.set_pressure" and s["app.run"]["running"]:
            cur = host._pc_current_step(s)
            if cur is not None and "setpoint_pa" in cur["params"] and cur["type"] != "vent":
                cur["params"]["setpoint_pa"] = float(args["pressure_pa"])
        host._refresh(inst)
        return response

    def _device_checked(self, inst, cap, args, rid):
        host = self._host
        if cap.startswith("panel."):
            meta = _pc3001_panel()["caps"] if (PC3001_ASSET_DIR / "panel_ui.json").is_file() else {}
            if cap not in meta: return self._error(inst.instance_id, "UNKNOWN_CAPABILITY", rid)
            if meta[cap]["visibility"] != "agent" and self._visibility not in ("debug", "evaluator"): return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            if _g03_check(meta[cap]["input_schema"], args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
            if self._VacuumSession__principal_id == VacuumHost.PANEL_PRINCIPAL or inst.instance_id not in host._panels:
                return self._error(inst.instance_id, "ACCESS_DENIED", rid)
            previous, host._panel_caller = host._panel_caller, self                # the commands of this gesture act as this session
            try:
                result, code = g03_panel_gesture(host._panels[inst.instance_id], cap, args, instance_id=inst.instance_id, time_s=host.time_s,
                                                 capture_dir=host.panel_capture_dir)
            finally:
                host._panel_caller = previous
            if code: return self._error(inst.instance_id, code, rid)
            return self._envelope(inst.instance_id, rid, result=result)
        if cap in PC3001_EVALUATOR_CAPS and self._visibility not in ("debug", "evaluator"):
            return self._error(inst.instance_id, "ACCESS_DENIED", rid)
        inputs, optional = PC3001_DEVICE_ARGS[cap][0], PC3001_DEVICE_ARGS[cap][1]
        if _g03_check(_g03_obj(inputs, optional), args): return self._error(inst.instance_id, "INVALID_ARGUMENT", rid)
        cand = copy.deepcopy(inst)
        try:
            result = getattr(self, "_pc_" + cap.replace(".", "_"))(cand, args)
        except _PcRefused as exc:
            return self._error(inst.instance_id, exc.code, rid)
        host._pc_faults(cand)
        host._refresh(cand)
        updates = {k: v for k, v in cand.state.items() if v != inst.state[k]}
        if host.scene_adapter and updates:
            try:
                host.scene_adapter(inst.instance_id, copy.deepcopy(updates))
            except Exception:
                return self._error(inst.instance_id, "DEVICE_ERROR", rid, "failed")
        inst.state, inst.g03 = cand.state, cand.g03
        inst.revision += 1
        return self._envelope(inst.instance_id, rid, result=result)

    # ---- device commands (on a candidate copy; a refusal leaves everything unchanged)
    @staticmethod
    def _on(c):
        if c.state["unit.power"]["controller"] != "on":
            raise _PcRefused("PRECONDITION_FAILED")

    def _pc_mains_set(self, c, a):
        s, pw = c.state, c.state["unit.power"]
        if a["on"] == pw["mains"]:
            return {"on": a["on"]}
        if not a["on"]:
            pw["resume"] = bool(s["app.run"]["running"])
            self._host._pc_end(c)
            pw.update(mains=False, controller="off", boot_left_s=0.0, locked=False)
            s["power.enabled"] = False
            s["vent.state"]["mode"] = "closed"
            c.g03["power_epoch"] += 1
        else:
            pw["mains"] = True
            if s["settings.values"]["autostart"]:                             # p.71 autostart: the controller starts again by itself
                pw.update(controller="booting", boot_left_s=PC3001_BOOT_S)
                c.g03["power_epoch"] += 1
        return {"on": a["on"]}

    def _pc_controller_power(self, c, a):
        s, pw = c.state, c.state["unit.power"]
        if a["on"]:
            if not pw["mains"]:
                raise _PcRefused("PRECONDITION_FAILED")
            if pw["controller"] == "off":
                pw.update(controller="booting", boot_left_s=PC3001_BOOT_S, locked=False)
                c.g03["power_epoch"] += 1
        elif pw["controller"] != "off":
            self._host._pc_end(c)
            pw.update(controller="off", boot_left_s=0.0, locked=False, resume=False)
            s["power.enabled"] = False
            s["vent.state"]["mode"] = "closed"
            c.g03["power_epoch"] += 1
        return {"controller": pw["controller"]}

    def _pc_controller_lock(self, c, a):
        self._on(c)
        c.state["unit.power"]["locked"] = a["locked"]
        return {"locked": a["locked"]}

    def _pc_controller_reboot(self, c, a):
        s, pw = c.state, c.state["unit.power"]
        if pw["controller"] == "off":
            raise _PcRefused("PRECONDITION_FAILED")
        self._host._pc_end(c)
        pw.update(controller="booting", boot_left_s=PC3001_BOOT_S, locked=False, resume=False)
        s["power.enabled"] = False
        s["vent.state"]["mode"] = "closed"
        c.g03["power_epoch"] += 1
        return {"controller": "booting"}

    def _app(self, c, app_id):
        app = self._host._pc_app(c.state, app_id)
        if app is None:
            raise _PcRefused("INVALID_ARGUMENT")
        return app

    def _pc_app_select(self, c, a):
        self._on(c)
        self._app(c, a["app_id"])
        if c.state["app.run"]["running"]:
            raise _PcRefused("PRECONDITION_FAILED")
        c.state["app.selected"] = a["app_id"]
        return {"app_id": a["app_id"]}

    def _pc_app_start(self, c, a):
        s = c.state
        self._on(c)
        app = self._app(c, s["app.selected"])
        if (s["app.run"]["running"] or c.chamber_id is None or c.exhaust_pressure_pa > MAXIMUM_BACK_PRESSURE_PA or not app["steps"]
                or any(f["level"] == "error" for f in s["faults.list"])):
            raise _PcRefused("PRECONDITION_FAILED")                          # p.86: restart only after the error is remedied and acknowledged
        s["vent.state"]["mode"] = "closed"
        self._host._pc_start(c, app["id"])
        return {"running": s["app.run"]["running"]}

    def _pc_app_stop(self, c, a):
        self._host._pc_end(c)
        return {"running": False}

    def _pc_app_continue(self, c, a):
        run = c.state["app.run"]
        if not (run["running"] and run["waiting"]):
            raise _PcRefused("PRECONDITION_FAILED")
        self._host._pc_enter_step(c, run["step"] + 1)
        return {"step": c.state["app.run"]["step"]}

    @staticmethod
    def _check_param(kind, param, value):
        if param not in PC3001_STEP_TYPES[kind][1]:
            raise _PcRefused("INVALID_ARGUMENT")
        label, schema, optional = PC3001_PARAMS[param]
        if value is None and not (optional or (param == "duration_s" and kind != "ramp")):
            raise _PcRefused("INVALID_ARGUMENT")
        if value is None and kind == "ramp" and param == "duration_s":
            raise _PcRefused("INVALID_ARGUMENT")
        if value is not None and _g03_check(schema if "anyOf" not in schema else schema["anyOf"][0], value):
            raise _PcRefused("INVALID_ARGUMENT")
        return None if value is None else (int(value) if param == "count" else float(value))

    def _pc_app_set_parameter(self, c, a):
        app = self._app(c, a["app_id"])
        if not 0 <= a["step"] < len(app["steps"]):
            raise _PcRefused("INVALID_ARGUMENT")
        st = app["steps"][a["step"]]
        st["params"][a["param"]] = v = self._check_param(st["type"], a["param"], a["value"])
        self._host._pc_drive(c)
        return {"value": v}

    def _pc_app_set_setpoint(self, c, a):
        s = c.state
        self._on(c)
        cur = self._host._pc_current_step(s)
        if cur is None or "setpoint_pa" not in cur["params"] or cur["type"] == "vent":
            raise _PcRefused("PRECONDITION_FAILED")                          # step buttons locked (grey) without a set-point
        if a["pressure_pa"] > self._host.ambient_pressure_pa:
            raise _PcRefused("INVALID_ARGUMENT")
        cur["params"]["setpoint_pa"] = float(a["pressure_pa"])
        if s["app.run"]["running"] and cur["type"] == "ramp":
            s["app.run"]["ramp_start_pa"] = s["vacuum.target_pressure_pa"]
        self._host._pc_drive(c)
        return {"pressure_pa": float(a["pressure_pa"])}

    def _pc_vent_control(self, c, a):
        s, v = c.state, c.state["vent.state"]
        self._on(c)
        if not s["settings.values"]["vent_valve_active"]:
            raise _PcRefused("UNSUPPORTED")                                   # p.51: VENT shown only with a connected and activated valve
        if c.chamber_id is None:
            raise _PcRefused("PRECONDITION_FAILED")
        if a["action"] == "stop" or (v["mode"] in ("brief", "atmosphere") and a["action"] in ("brief", "atmosphere")):
            v.update(mode="closed", brief_left_s=0.0)                         # a keystroke during venting stops it
        elif a["action"] == "brief":
            v.update(mode="brief", brief_left_s=PC3001_BRIEF_VENT_S)
        else:
            self._host._pc_end(c)                                             # vent to atmosphere: the vacuum pump is stopped
            v.update(mode="atmosphere", brief_left_s=0.0)
        return {"mode": v["mode"]}

    def _pc_favorites_set(self, c, a):
        self._app(c, a["app_id"])["favorite"] = a["favorite"]
        return {"favorite": a["favorite"]}

    def _pc_editor_edit(self, c, a):
        d = c.state["editor.draft"]
        act = a["action"]
        if act == "new":
            d.update(name="New application", description="", steps=[], source_id=None)
        elif act == "template":
            if "app_id" not in a:
                raise _PcRefused("INVALID_ARGUMENT")
            app = self._app(c, a["app_id"])
            d.update(name=app["name"], description=app["description"], steps=copy.deepcopy(app["steps"]), source_id=app["id"])
        elif act == "add":
            if "step_type" not in a or len(d["steps"]) >= 20:
                raise _PcRefused("INVALID_ARGUMENT")
            st = _pc_step(a["step_type"])
            if "setpoint_pa" in st["params"]:
                st["params"]["setpoint_pa"] = 110000.0 if a["step_type"] == "vent" else 10000.0
            if a["step_type"] == "ramp":
                st["params"]["duration_s"] = 600.0
            d["steps"].insert(min(a.get("index", len(d["steps"])), len(d["steps"])), st)   # placed above Process end (p.66)
        elif act in ("remove", "param", "move"):
            i = a.get("index")
            if i is None or not 0 <= i < len(d["steps"]):
                raise _PcRefused("INVALID_ARGUMENT")
            if act == "remove":
                d["steps"].pop(i)
            elif act == "move":
                if "to" not in a:
                    raise _PcRefused("INVALID_ARGUMENT")
                st = d["steps"].pop(i)
                d["steps"].insert(min(a["to"], len(d["steps"])), st)
            else:
                if "param" not in a or "value" not in a:
                    raise _PcRefused("INVALID_ARGUMENT")
                st = d["steps"][i]
                st["params"][a["param"]] = self._check_param(st["type"], a["param"], a["value"])
        else:
            d["description"] = a.get("text", "")
        return {"draft": copy.deepcopy(d)}

    def _pc_editor_save(self, c, a):
        s, d = c.state, c.state["editor.draft"]
        if not d["steps"]:
            raise _PcRefused("PRECONDITION_FAILED")
        src = self._host._pc_app(s, d["source_id"]) if d["source_id"] is not None else None
        if "name" not in a and src is not None and not src["standard"]:
            if s["app.run"]["running"] and s["app.run"]["app_id"] == src["id"]:
                raise _PcRefused("PRECONDITION_FAILED")
            src.update(description=d["description"], steps=copy.deepcopy(d["steps"]))
            return {"app_id": src["id"]}
        name = (a.get("name") or "").strip()
        if not name or any(x["name"] == name for x in s["app.library"]):
            raise _PcRefused("INVALID_ARGUMENT")                              # Save as needs a new name (standard applications stay unchanged)
        new_id = max([c.g03["next_id"] - 1] + [x["id"] for x in s["app.library"]]) + 1
        c.g03["next_id"] = new_id + 1
        s["app.library"].append({"id": new_id, "name": name, "description": d["description"], "standard": False, "favorite": False,
                                 "steps": copy.deepcopy(d["steps"])})
        d.update(name=name, source_id=new_id)
        return {"app_id": new_id}

    def _pc_settings_configure(self, c, a):
        st = c.state["settings.values"]
        if "orientation" in a and a["orientation"] not in (0, 90, 180, 270):
            raise _PcRefused("INVALID_ARGUMENT")
        st.update({k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "orientation" else v) for k, v in a.items()})
        if not st["vent_valve_active"]:
            c.state["vent.state"].update(mode="closed", brief_left_s=0.0)
        return {"settings": copy.deepcopy(st)}

    def _pc_settings_factory_reset(self, c, a):
        s = c.state
        if s["app.run"]["running"]:
            raise _PcRefused("PRECONDITION_FAILED")
        s.update({"settings.values": _pc_defaults(), "app.library": _pc_library(), "app.selected": 6,
                  "sensor.adjustment": {"k": 1.0, "z_off_pa": 0.0, "atm_ref_pa": None, "vac_ref_pa": None}, "logger.state": {"records": 0, "files": 0},
                  "editor.draft": {"name": "New application", "description": "", "steps": [], "source_id": None}})
        c.g03["next_id"] = 100
        return {}

    def _pc_clock_set(self, c, a):
        try:
            when = datetime.fromisoformat(f"{a['date']}T{a['time']}")
        except ValueError:
            raise _PcRefused("INVALID_ARGUMENT")
        c.state["settings.values"]["clock_offset_s"] = (when - G03_DEVICE_EPOCH).total_seconds() - self._host.time_s
        return {"clock": when.isoformat(timespec="seconds")}

    def _pc_sensor_adjust(self, c, a):
        s = c.state
        raw = self._host._pc_raw(s)
        adj = s["sensor.adjustment"]
        ref = float(a["reference_pa"])
        if raw is None:
            raise _PcRefused("PRECONDITION_FAILED")
        if raw > 70000.0:                                                    # p.79: atmospheric adjustment only above 700 mbar
            if not 70000.0 < ref <= 110000.0 or raw - adj["z_off_pa"] <= 0:
                raise _PcRefused("INVALID_ARGUMENT")
            adj.update(k=ref / (raw - adj["z_off_pa"]), atm_ref_pa=ref)
            if not 0.5 <= adj["k"] <= 2.0:
                raise _PcRefused("INVALID_ARGUMENT")
        elif raw < 2000.0:                                                   # p.80-82: under vacuum (< 20 mbar): 0 = zero point or 0.1-20 mbar
            if not (ref == 0 or 10.0 <= ref <= 2000.0):
                raise _PcRefused("INVALID_ARGUMENT")
            adj.update(z_off_pa=raw - ref / adj["k"], vac_ref_pa=ref)
            if abs(adj["z_off_pa"]) > 10000:
                raise _PcRefused("INVALID_ARGUMENT")
        else:
            raise _PcRefused("PRECONDITION_FAILED")                          # the input field is inactive in this range (p.78)
        return {"adjustment": copy.deepcopy(adj)}

    def _pc_sensor_configure_error(self, c, a):
        c.state["sensor.error"] = {"gain": float(a["gain"]), "offset_pa": float(a["offset_pa"])}
        return {"error": copy.deepcopy(c.state["sensor.error"])}

    def _pc_fault_inject(self, c, a):
        c.state["faults.injected"] = {"code": a["code"]}
        return {"code": a["code"]}

    def _pc_fault_acknowledge(self, c, a):
        s = c.state
        if any(f["code"] == "maintenance" for f in s["faults.list"]):          # acknowledged reminder: the next one after the same interval
            s["settings.values"]["maintenance_h"] = float(math.floor(s["service.hours_s"] / 3600.0) + s["settings.values"]["maintenance_h"])
        causes = self._host._pc_causes(s)
        kept = [dict(f, acknowledged=True) for f in s["faults.list"] if f["code"] in causes]   # an error whose cause remains stays
        s["faults.list"] = kept
        return {"faults": copy.deepcopy(kept)}


_g03_pc3001_export_base = export_contract_metadata


def export_contract_metadata():
    """CHEM-188 metadata plus the VACUU·SELECT device layer: device-layer slots, device_capabilities, panel_capabilities (when the panel spec
    exists), parameters and limitations."""
    meta = _g03_pc3001_export_base()
    t = meta["asset_types"][ASSET_TYPE_ID]
    t["asset_version"] = PC3001_ASSET_VERSION
    dev = {}
    for name, (inputs, optional, outputs, example, desc, effects, components, kind) in PC3001_DEVICE_ARGS.items():
        dev[name] = g03_device_capability(name, kind, desc, inputs, outputs, example, components, effects, optional=optional, binding=_PC3001_BINDING,
                                          completion="同步提交；过程步骤、通气、启动、记录与故障由宿主step推进",
                                          visibility="evaluator" if name in PC3001_EVALUATOR_CAPS else "agent")
    t["device_capabilities"] = dev
    t["panel_capabilities"] = {}
    if (PC3001_ASSET_DIR / "panel_ui.json").is_file():
        spec = _pc3001_panel()["spec"]
        called = _g03_engine().spec_capabilities(spec)
        effects = list(dict.fromkeys(e["state_id"] for c in called for e in (t["capabilities"].get(c) or dev.get(c) or {}).get("state_effects", [])
                                     if e["timing"] == "immediate" and STATE_SLOTS.get(e["state_id"], {}).get("visibility") == "agent"))
        t["panel_capabilities"] = g03_panel_capabilities(spec, _PC3001_BINDING, effects)
    t["parameters"].update({
        "controller_start_s": {"value": PC3001_BOOT_S, "unit": "s", "evidence": "manual", "source": "pump p.51 (process display after approx. 30 s)"},
        "venting_conductance_m3_s": {"value": PC3001_VENT_M3_S, "unit": "m^3/s", "evidence": "engineering_assumption", "source": "venting valve air inflow C*(Pa-P)"},
        "brief_vent_s": {"value": PC3001_BRIEF_VENT_S, "unit": "s", "evidence": "engineering_assumption", "source": "VENT pressed < 2 s"},
        "motor_speed_min_pct": {"value": PC3001_MIN_SPEED_PCT, "unit": "%", "evidence": "catalog", "source": "datasheet 200-3000 min-1"},
        "sensor_adjust_ranges_pa": {"value": {"atmosphere_above": 70000.0, "vacuum_below": 2000.0, "reference": [10.0, 2000.0]}, "unit": "Pa", "evidence": "manual",
                                    "source": "p.79-82"},
    })
    meta["limitations"] = list(meta["limitations"]) + PC3001_G03_LIMITATIONS
    return meta


PC3001_G03_LIMITATIONS = [
    "Run ownership (lead rule 2026-10-19, a simulation rule): the process started by one agent (application or CHEM-188 pump run) belongs to "
    "it; another agent cannot stop, switch off or change it (Stop, set points, parameters, venting, sensor adjustment, settings, reboot, "
    "controller ON/OFF key, CHEM-188 power.set) on the panel or the direct path (ACCESS_DENIED, state unchanged, pop-up with our text).  The "
    "rocker switch (mains.set) is physical (lead clarification 10-19): anyone may switch it, the process ends as on a power loss.  The gas "
    "ballast (on the pump head) and the evaluator are not restricted.  fault.inject also covers the frequency converter error (p.88); valve, I/O module, Peltronic and "
    "level-sensor errors need components absent from this configuration.",
    "VACUU·SELECT device layer (operating manual V1.13): switch-on with a 30 s start screen, lock, switch-off, reboot; applications from process steps "
    "(pump down, vacuum control, ramp, vent, hold vacuum, automatic boiling point, loop, turbo backing pump, VACUU·LAN) drive the CHEM-188 mode, "
    "target and start; the step speed scales the pumping speed (VARIO).  Standard application IDs and names follow the Modbus manual; their step "
    "contents and default values are assumptions.  Automatic boiling point, turbo backing pump and VACUU·LAN steps act as pump down / vacuum control "
    "(no vapour, turbo pump or network model).",
    "The sensor's venting valve admits ambient air into the external chamber (assumed conductance 2e-4 m3/s; brief vent 0.5 s).  Sensor adjustment "
    "(atmospheric gain, vacuum zero / reference) and the raw sensor errors (evaluator-only, default 0) form one sensor model: the controller regulates "
    "on its own reading (CHEM-188 vacuum.set_pressure and every application set point, ramp start, vent-to-set-point and vent-on-set-point act on "
    "the shown pressure), so with an error the chamber truth differs from the set point while the display shows it.  Faults: pump, sensor, "
    "communication and venting-valve causes come only from evaluator injection; overpressure / underrange from the shown reading.",
    "Absent hardware: USB flash drive (import / export, software update, data-logger and diagnostics download), Ethernet / Modbus, RS-232 remote "
    "control, licence files, level sensors and coolant valves.",
    "Screen orientation 0 / 90 / 180 / 270 degrees and the language are stored device settings (kept over power-off, reset by the factory settings). "
    "90 / 270 degrees are drawn by turning the portrait frame (touches mapped accordingly), not with the controller's own landscape layout.  The "
    "language is stored, shown in Regional settings and reported in settings.values; our manuals contain no translated texts, so every language "
    "keeps the English texts (no translations are invented).",
]
# <<< G03 CHEM-188 PC 3001 VARIO select front panel

# ======== G03 front panels (end) ========
