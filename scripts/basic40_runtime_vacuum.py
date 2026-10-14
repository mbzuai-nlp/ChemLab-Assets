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
    return {"asset_types": {ASSET_TYPE_ID: {"catalog_id": "CHEM-188", "model": "VACUUBRAND PC 3001 VARIO select", "asset_version": "1.0.0", "parameters": copy.deepcopy(PARAMETERS), "state_slots": slots, "capabilities": caps}},
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
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": ASSET_TYPE_ID, "asset_version": "1.0.0", "instance_id": instance_id,
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
            ("vacuum.set_pressure", "pressure_pa", "Pa", "压力设定归外部真空源；本仪器无内部泵或压力控制驱动。"),
            ("timer.set_duration", "duration_s", "s", "目录计时功能已登记，但无已核实的计时范围/完成行为与运行绑定。")):
        caps[cap] = {"availability": "declared", "visibility": "agent", "kind": "device_command", "description_zh": explanation,
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
    return {"asset_types": {RV10_ASSET_TYPE_ID: {"catalog_id": "CHEM-024", "model": "IKA RV 10 digital V", "asset_version": "1.0.0", "parameters": params, "state_slots": slots, "capabilities": caps}}, "host_capabilities": RV10Host.describe_host(), "limitations": list(RV10_LIMITATIONS)}


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
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": RV10_ASSET_TYPE_ID, "asset_version": "1.0.0", "instance_id": instance_id, "catalog_id": "CHEM-024", "model": spec["model"], "adapter": "basic40_runtime_vacuum.RV10Session", "capabilities": caps, "state_slots": slots, "limitations": list(RV10_LIMITATIONS)}

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
    return {"asset_types": {LYOVAPOR_ASSET_TYPE_ID: {"catalog_id": "CHEM-025", "model": "BUCHI Lyovapor L-250 Pro", "asset_version": "1.0.0", "parameters": parameters, "state_slots": slots, "capabilities": caps}},
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
            self._commit_host(inst)

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
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": LYOVAPOR_ASSET_TYPE_ID, "asset_version": "1.0.0", "instance_id": instance_id, "catalog_id": "CHEM-025", "model": spec["model"], "adapter": "basic40_runtime_vacuum.LyovaporSession", "state_slots": visible, "capabilities": spec["capabilities"], "limitations": list(LYOVAPOR_LIMITATIONS)}

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
    return {"asset_types": {PURELAB_ASSET_TYPE_ID: {"catalog_id": "CHEM-190", "model": "ELGA LabWater | PURELAB flex 3", "asset_version": "1.0.0", "parameters": copy.deepcopy(PURELAB_PARAMETERS), "state_slots": slots, "capabilities": caps}}, "host_capabilities": PurelabHost.describe_host(), "limitations": list(PURELAB_LIMITATIONS)}


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
            return {"protocol_version": PROTOCOL_VERSION, "asset_type_id": PURELAB_ASSET_TYPE_ID, "asset_version": "1.0.0", "instance_id": instance_id, "catalog_id": "CHEM-190", "model": spec["model"], "adapter": "basic40_runtime_vacuum.PurelabSession", "state_slots": visible, "capabilities": spec["capabilities"], "limitations": list(PURELAB_LIMITATIONS)}

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
    return {"asset_types": {INERT_PURELAB_ASSET_TYPE_ID: {"catalog_id": "CHEM-189", "model": "Inert | PureLab HE 2GB", "asset_version": "1.0.0", "parameters": params, "state_slots": slots, "capabilities": caps}}, "host_capabilities": InertPureLabHost.describe_host(), "limitations": list(INERT_PURELAB_LIMITATIONS)}


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
            return {"protocol_version": "1.0.0", "asset_type_id": INERT_PURELAB_ASSET_TYPE_ID, "asset_version": "1.0.0", "instance_id": instance_id, "catalog_id": "CHEM-189", "model": spec["model"], "adapter": "basic40_runtime_vacuum.InertPureLabSession", "capabilities": caps, "state_slots": visible, "limitations": list(INERT_PURELAB_LIMITATIONS)}

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
                target = step["volume_m3"]
            else:
                args = op["arguments"]
                source, destination = ("source:" + args["solvent_position"], "pipette") if op["capability_id"] == "sample.aspirate" else ("pipette", args["destination"])
                rate, target = args["flow_m3_s"], args["volume_m3"]
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
            return {"protocol_version": "1.0.0", "asset_type_id": EXTRAHERA_ASSET_TYPE_ID, "asset_version": "1.0.0", "catalog_id": "CHEM-031", "model": "Biotage Extrahera Classic", "instance_id": instance_id, "adapter": "basic40_runtime_vacuum.ExtraheraSession", "capabilities": caps, "state_slots": slots, "limitations": list(EXTRAHERA_LIMITATIONS)}

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
                if position not in s["deck.solvent_positions"] or position not in inst.sources or inst.sources[position] < args["volume_m3"] or s["fluid.pipette_m3"] + args["volume_m3"] > EXTRAHERA_MAXIMUM_STROKE_M3 + 1e-18: return fail("PRECONDITION_FAILED")
            if cap == "sample.dispense" and s["fluid.pipette_m3"] + 1e-18 < args["volume_m3"]: return fail("PRECONDITION_FAILED")
            if cap == "method.start":
                if not inst.method or s["fluid.pipette_m3"] > 1e-18 or s["fluid.extraction_m3"] > 1e-18 or s["pressure.target_pa"] <= 0 or not s["utilities.nitrogen_ready"] or s["manifold.z_m"] != 0: return fail("PRECONDITION_FAILED")
                requirements = {}
                for p in inst.method:
                    try: amount = p["volume_m3"] * p["repetitions"]
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
