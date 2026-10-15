"""Append-only measurement runtimes. CHEM-001 uses synthetic PX224E-family behavior.

The XPR204E catalogue target and the reused OHAUS geometry are distinct. The
filtered runtime is an engineering model, not either manufacturer's firmware.
Only the trusted host supplies supported pan force and advances the common clock.
Session filtering is an API boundary, not a Python or filesystem sandbox.
"""
import copy as _xpr_copy
import hashlib as _xpr_hashlib
import json as _xpr_json
import math as _xpr_math
import random as _xpr_random
import threading as _xpr_threading
from collections import deque as _xpr_deque
from decimal import Decimal as _xpr_Decimal, ROUND_HALF_UP as _xpr_ROUND_HALF_UP
from datetime import datetime as _xpr_datetime, timezone as _xpr_timezone
from pathlib import Path as _xpr_Path

try:
    from .balance_runtime import Balance as _XPRLegacyBalance
except ImportError:
    from balance_runtime import Balance as _XPRLegacyBalance

XPR204E_TYPE = "instrument.mt_xpr204e"
XPR204E_CAPACITY_KG = 0.220
XPR204E_READABILITY_KG = 1e-7
XPR204E_TARGET_SETTLING_S = 1.5
XPR204E_DOOR_MAX_M = 0.109
XPR204E_CALIBRATION_MIN_KG = 1e-12  # Numerical input envelope, not a vendor standard-mass specification.
XPR204E_FILTERS = {
    "Low": {"time_constant_s": 0.25, "stability_divisions": 512},
    "Medium": {"time_constant_s": 1.0, "stability_divisions": 256},
    "High": {"time_constant_s": 4.0, "stability_divisions": 128},
}
XPR204E_SCENE_KEYS = (
    "power.enabled", "balance.filter_level", "balance.display_unit", "balance.status", "balance.stable",
    "balance.gross_kg", "balance.net_kg", "balance.tare_kg", "balance.calibrated", "balance.task_ready",
    "balance.synthetic", "balance.stability_elapsed_s", "pan.installed", "environment.tilt_rad", "environment.vibration",
    "door.left.opening_m", "door.right.opening_m", "door.top.opening_m",
)


def _xpr_number(value):
    try:
        return type(value) in (int, float) and _xpr_math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _xpr_num_schema(unit, minimum=None, maximum=None, nullable=False):
    spec = {"type": "number", "x-unit": unit}
    if minimum is not None:
        spec["minimum"] = minimum
    if maximum is not None:
        spec["maximum"] = maximum
    return {"anyOf": [spec, {"type": "null"}]} if nullable else spec


def _xpr_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _xpr_quantize(value):
    step = _xpr_Decimal(str(XPR204E_READABILITY_KG))
    return float((_xpr_Decimal(str(value)) / step).quantize(_xpr_Decimal("1"), rounding=_xpr_ROUND_HALF_UP) * step)


class FilteredAnalyticalBalance(_XPRLegacyBalance):
    """Trusted force-input core. Direct read() is a debugging interface.

    The manufacturer's Low/Medium/High ordering is documented in PX manual
    section 5.3.3. All numeric tau/N values and the five-tau startup interval are
    engineering assumptions for the specified white-contact-noise experiment.
    N is a full peak-to-peak displayed range, NOT accuracy or repeatability.
    The 1.5 s default holding window is the XPR catalogue target reused as a
    simulation rule; it is not proof of a 1.5 s instrument response.
    """
    def __init__(self, *, filter_level="Medium", settling_s=XPR204E_TARGET_SETTLING_S):
        if not isinstance(filter_level, str) or filter_level not in XPR204E_FILTERS or not _xpr_number(settling_s) or not 0 < settling_s <= 60:
            raise ValueError("Unknown filter or invalid finite settling interval")
        super().__init__(XPR204E_CAPACITY_KG, XPR204E_READABILITY_KG, float(settling_s), True)
        self.filter_level = filter_level
        self.raw_force_N = 0.0
        self.filtered_force_N = None
        self.sample_time_s = 0.0
        self.filter_age_s = 0.0
        self._stability_samples = _xpr_deque()
        self._window_range_kg = None

    @classmethod
    def for_model(cls, model):
        if model == "XPR204E":
            return cls()
        if model == "PX224E":
            return cls(settling_s=3.0)
        raise ValueError("This filtered analytical family supports XPR204E and PX224E")

    def reset_stability(self, *, clear_filter=False):
        self.stable = False
        self.elapsed_s = 0.0
        self.last_environment_valid = False
        self._stability_samples.clear()
        self._window_range_kg = None
        if clear_filter:
            self.filtered_force_N = None
            self.filter_age_s = 0.0

    @property
    def stability_band_kg(self):
        return XPR204E_FILTERS[self.filter_level]["stability_divisions"] * self.readability_kg

    def set_power(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Power is boolean")
        if self.powered != enabled:
            self.powered = enabled
            self.reset_stability(clear_filter=True)

    def set_filter(self, level):
        if not isinstance(level, str) or level not in XPR204E_FILTERS:
            raise ValueError("Unknown filter level")
        if self.security_locked:
            raise ValueError("Filter configuration is locked")
        if level != self.filter_level:
            self.filter_level = level
            self.reset_stability(clear_filter=True)

    def update(self, pan_load_force_N, dt_s, *, door_fraction=0.0, tilt_rad=0.0, vibration=False):
        if (not _xpr_number(pan_load_force_N) or not _xpr_number(dt_s) or dt_s < 0
                or not _xpr_number(door_fraction) or not 0 <= door_fraction <= 1
                or not _xpr_number(tilt_rad) or type(vibration) is not bool):
            raise ValueError("Finite SI force/time/environment and boolean vibration required")
        if dt_s == 0:
            return self.read()
        self.sample_time_s += dt_s
        self.raw_force_N = float(pan_load_force_N)
        self.door_fraction, self.tilt_rad = float(door_fraction), float(tilt_rad)
        if self.filtered_force_N is None:
            self.filtered_force_N = self.raw_force_N
        else:
            tau = XPR204E_FILTERS[self.filter_level]["time_constant_s"]
            alpha = -_xpr_math.expm1(-dt_s / tau)
            # Convex form avoids overflow when two finite forces have opposite signs.
            self.filtered_force_N = (1 - alpha) * self.filtered_force_N + alpha * self.raw_force_N
        self.filter_age_s += dt_s
        self.gross_kg = self.filtered_force_N / self.g_cal
        hardware = self.powered and self.pan_installed and self.pan_latched and self.transport_unlocked
        environment = door_fraction <= 0.01 and abs(tilt_rad) <= _xpr_math.radians(0.2) and not vibration
        # Raw force rejects overload immediately: smoothing must not conceal it.
        # Near-zero negative noise is handled by the filtered lower bound, not
        # by resetting on every negative raw sample. The N*d tolerance is an
        # engineering assumption; displayed negative values are not clipped.
        range_valid = self.raw_force_N / self.g_cal <= self.capacity_kg and -self.stability_band_kg <= self.gross_kg <= self.capacity_kg
        valid = hardware and environment and range_valid
        if not valid:
            self.reset_stability()
            return self.read()
        displayed = _xpr_quantize(self.gross_kg)
        samples = self._stability_samples
        samples.append((self.sample_time_s, displayed))
        cutoff = self.sample_time_s - self.settling_s
        # Retain a bracketing sample so the verified window is never too short.
        while len(samples) > 1 and samples[1][0] <= cutoff + 1e-10:
            samples.popleft()
        self._window_range_kg = max(v for _, v in samples) - min(v for _, v in samples)
        band = self.stability_band_kg
        inside = self._window_range_kg <= band + 1e-15
        self.elapsed_s = min(self.settling_s, self.sample_time_s - samples[0][0]) if inside else 0.0
        warmed = self.filter_age_s + 1e-10 >= 5 * XPR204E_FILTERS[self.filter_level]["time_constant_s"]
        self.last_environment_valid = True
        self.stable = bool(inside and warmed and self.elapsed_s + 1e-10 >= self.settling_s)
        return self.read()

    def read(self):
        if not self.powered:
            status = "OFF"
        elif not self.pan_installed:
            status = "PAN_REMOVED"
        elif not self.pan_latched:
            status = "PAN_UNLATCHED"
        elif not self.transport_unlocked:
            status = "TRANSPORT_LOCKED"
        elif self.raw_force_N / self.g_cal > self.capacity_kg or self.gross_kg > self.capacity_kg:
            status = "OVERLOAD"
        elif self.gross_kg < -self.stability_band_kg:
            status = "UNDERLOAD"
        else:
            status = "STABLE" if self.stable else "UNSTABLE"
        observable = status in ("STABLE", "UNSTABLE") and self.filtered_force_N is not None
        # Do not quantize unbounded overloads or invent a reading before acquisition.
        net = _xpr_quantize(self.gross_kg - self.zero_kg - self.tare_kg) if observable else None
        stable = status == "STABLE"
        return {"status": status, "stable": stable, "net_kg": net,
            "value": None if net is None else net * (1000 if self.unit == "g" else 1),
            "unit": self.unit, "gross_kg": self.gross_kg, "tare_kg": self.tare_kg,
            "calibrated": self.calibrated, "task_ready": stable and self.calibrated,
            "synthetic": True, "filter_level": self.filter_level, "raw_force_N": self.raw_force_N,
            "filtered_force_N": self.filtered_force_N, "stability_window_range_kg": self._window_range_kg}

    def press(self, key, hold_s=0.1):
        if not _xpr_number(hold_s) or hold_s < 0:
            raise ValueError("Finite nonnegative hold time required")
        if key == "zero" and (hold_s >= 2 or not self.powered):
            self.set_power(hold_s < 2)
            return self.read()
        return super().press(key, hold_s)

    def calibrate_span(self, known_mass_kg, measured_load_force_N):
        """Validate trusted direct calibration inputs before changing any state."""
        if (not _xpr_number(known_mass_kg) or not XPR204E_CALIBRATION_MIN_KG <= known_mass_kg <= self.capacity_kg
                or not _xpr_number(measured_load_force_N) or measured_load_force_N <= 0
                or self.filtered_force_N is None):
            raise ValueError("Finite calibration mass/force within the numeric input envelope required")
        gain = measured_load_force_N / known_mass_kg
        if not _xpr_number(gain) or gain <= 0 or not _xpr_number(self.filtered_force_N / gain):
            raise ValueError("Calibration would produce a non-finite numerical state")
        super().calibrate_span(known_mass_kg, measured_load_force_N)
        self.gross_kg = self.filtered_force_N / self.g_cal
        self.reset_stability()

    def calibrate_known_mass(self, known_mass_kg):
        if not _xpr_number(known_mass_kg) or not XPR204E_CALIBRATION_MIN_KG <= known_mass_kg <= self.capacity_kg:
            raise ValueError("Known calibration mass is outside capacity")
        if self.filtered_force_N is None or self.filtered_force_N <= 0:
            raise ValueError("A positive stable calibration load is required")
        self.calibrate_span(known_mass_kg, self.filtered_force_N)


def export_xpr204e_contract_metadata():
    """Contract authoring metadata only. No live sample force or private state."""
    def slot(unit, origin, schema, visibility="agent", route=None):
        return {"unit": unit, "origin": origin, "visibility": visibility,
                "access": "read_write" if route else "read_only", "value_schema": schema,
                "write_via": route, "read_binding": "XPR204ESession.read(instance_id=..., state_ids=[state_id])",
                "sampling": {"mode": "instant", "time_domain": "simulation", "window": None},
                "reference_conditions": None}
    def route(cap, parameter, fixed=None):
        return {"capability_id": cap, "parameter": parameter, "fixed_arguments": fixed or {}}
    boolean = {"type": "boolean"}
    statuses = {"type": "string", "enum": ["OFF", "PAN_REMOVED", "PAN_UNLATCHED", "TRANSPORT_LOCKED", "OVERLOAD", "UNDERLOAD", "UNSTABLE", "STABLE"]}
    levels = {"type": "string", "enum": list(XPR204E_FILTERS)}
    units = {"type": "string", "enum": ["kg", "g"]}
    slots = {
        "power.enabled": slot("1", "setpoint", boolean, route=route("power.set", "enabled")),
        "balance.filter_level": slot("1", "setpoint", levels, route=route("balance.set_filter", "level")),
        "balance.display_unit": slot("1", "setpoint", units, route=route("balance.set_display_unit", "unit")),
        "balance.status": slot("1", "derived", statuses),
        "balance.stable": slot("1", "derived", boolean),
        "balance.gross_kg": slot("kg", "derived", _xpr_num_schema("kg", nullable=True)),
        "balance.net_kg": slot("kg", "derived", _xpr_num_schema("kg", nullable=True)),
        "balance.tare_kg": slot("kg", "derived", _xpr_num_schema("kg")),
        "balance.calibrated": slot("1", "derived", boolean),
        "balance.task_ready": slot("1", "derived", boolean),
        "balance.synthetic": slot("1", "derived", {"type": "boolean", "const": True}),
        "balance.raw_force_n": slot("N", "process_truth", _xpr_num_schema("N"), "debug"),
        "balance.filtered_force_n": slot("N", "process_truth", _xpr_num_schema("N", nullable=True), "debug"),
        "balance.gain_n_kg": slot("N/kg", "process_truth", _xpr_num_schema("N/kg", 0), "debug"),
        "balance.zero_kg": slot("kg", "process_truth", _xpr_num_schema("kg"), "debug"),
        "balance.stability_elapsed_s": slot("s", "derived", _xpr_num_schema("s", 0)),
        "pan.installed": slot("1", "scene", boolean, route=route("scene.set_pan", "installed")),
        "environment.tilt_rad": slot("rad", "scene", _xpr_num_schema("rad")),
        "environment.vibration": slot("1", "scene", boolean),
    }
    for side in ("left", "right", "top"):
        slots[f"door.{side}.opening_m"] = slot("m", "scene", _xpr_num_schema("m", 0, XPR204E_DOOR_MAX_M),
            route=route("scene.set_door", "opening_m", {"side": side}))
    reading = {"status": statuses, "stable": boolean, "gross_kg": _xpr_num_schema("kg", nullable=True),
        "net_kg": _xpr_num_schema("kg", nullable=True), "synthetic": {"type": "boolean", "const": True}}
    definitions = {
        "power.set": ({"enabled": boolean}, {"enabled": boolean}, {"enabled": True}, "设置本机供电；电源变化清空滤波启动与稳定历史。", ["power.enabled", "balance.stable", "balance.status", "balance.stability_elapsed_s"], ["power.enabled"]),
        "balance.set_filter": ({"level": levels}, {"level": levels}, {"level": "High"}, "选择三档合成低通模型，改变设置后重新暖机。", ["balance.filter_level", "balance.stable", "balance.status", "balance.stability_elapsed_s"], ["balance.filter_level"]),
        "balance.tare": ({}, {"tare_kg": _xpr_num_schema("kg")}, {}, "稳定时去皮；以后显示净重，毛重用于超载判定。", ["balance.tare_kg", "balance.net_kg"], ["balance.tare_kg"]),
        "balance.zero": ({}, {"zeroed": boolean}, {}, "稳定且在假设的整机量程2%零点捕获范围内归零，并清除去皮。", ["balance.zero_kg", "balance.tare_kg", "balance.net_kg"], ["balance.net_kg"]),
        "balance.set_display_unit": ({"unit": units}, {"unit": units}, {"unit": "g"}, "选择面板单位标签；公共质量接口始终以kg输出。", ["balance.display_unit"], ["balance.display_unit"]),
        "balance.calibrate_span": ({"known_mass_kg": _xpr_num_schema("kg", XPR204E_CALIBRATION_MIN_KG, XPR204E_CAPACITY_KG)}, {"calibrated": boolean, "synthetic": {"type": "boolean", "const": True}}, {"known_mass_kg": 0.1}, "以已知标准砝码和当前滤波载荷进行简化单点增益校准；非厂家自动校准过程。", ["balance.gain_n_kg", "balance.zero_kg", "balance.tare_kg", "balance.calibrated", "balance.gross_kg", "balance.net_kg", "balance.stable", "balance.status", "balance.stability_elapsed_s"], ["balance.calibrated", "balance.synthetic"]),
        "balance.reading": ({}, reading, {}, "读取同版本的量化滤波毛重、净重和稳定状态，不推进时钟。", [], ["balance.status", "balance.stable", "balance.gross_kg", "balance.net_kg", "balance.synthetic"]),
        "scene.set_door": ({"side": {"type": "string", "enum": ["left", "right", "top"]}, "opening_m": _xpr_num_schema("m", 0, XPR204E_DOOR_MAX_M)}, {"side": {"type": "string", "enum": ["left", "right", "top"]}, "opening_m": _xpr_num_schema("m")}, {"side": "left", "opening_m": 0.05}, "同步设置复用PX224E滑门的+Y平移；改变门位后重新稳定。", ["door.left.opening_m", "door.right.opening_m", "door.top.opening_m", "balance.stable", "balance.status", "balance.stability_elapsed_s"], ["door.left.opening_m", "door.right.opening_m", "door.top.opening_m"]),
        "scene.set_pan": ({"installed": boolean}, {"installed": boolean}, {"installed": False}, "仅在断电卸载后切换秤盘装配槽；不模拟机器人拆装或自由体接触。", ["pan.installed", "balance.stable", "balance.status", "balance.stability_elapsed_s"], ["pan.installed"]),
    }
    caps = {}
    for name, (inputs, outputs, example, description, effects, observes) in definitions.items():
        if name in ("power.set", "balance.set_filter"):
            effects += ["balance.filtered_force_n", "balance.gross_kg", "balance.net_kg", "balance.task_ready"]
        if name in ("balance.calibrate_span", "scene.set_door", "scene.set_pan"):
            effects += ["balance.task_ready"]
        if name == "scene.set_pan":
            effects += ["balance.gross_kg", "balance.net_kg"]
        caps[name] = {"availability": "implemented", "visibility": "agent",
            "kind": "query" if name == "balance.reading" else "simulation" if name.startswith("scene.") else "device_command",
            "description_zh": description, "input_schema": _xpr_object(inputs), "output_schema": _xpr_object(outputs),
            "example_arguments": [example], "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": description} for sid in effects],
            "observes": observes, "scene_backend_required": name.startswith("scene."),
            "binding": f"basic40_runtime_measure.XPR204ESession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
            "completion_criterion_zh": "同步提交声明状态；读数稳定需等待宿主推进与随后观测。",
            "preconditions_zh": ("稳定、供电、未锁定校准，且所选质量与滤波力能得到有限增益。" if name == "balance.calibrate_span" else
                "稳定且供电。" if name in ("balance.tare", "balance.zero") else
                "同步场景回调已注册。" if name.startswith("scene.") else "参数与声明范围匹配。")}
    parameters = {
        "capacity_kg": {"value": XPR204E_CAPACITY_KG, "unit": "kg", "evidence": "catalog", "source": "CHEM-001 XPR204E catalogue row; same as migrated PX224E"},
        "readability_kg": {"value": XPR204E_READABILITY_KG, "unit": "kg", "evidence": "catalog", "source": "CHEM-001 XPR204E catalogue row; same as migrated PX224E; not accuracy"},
        "target_repeatability_kg": {"value": 4e-8, "unit": "kg", "evidence": "catalog", "source": "XPR204E target only, not a fitted simulation noise parameter"},
        "target_settling_s": {"value": XPR204E_TARGET_SETTLING_S, "unit": "s", "evidence": "catalog", "source": "XPR204E catalogue target; does not guarantee simulated total response time"},
        "stability_hold_s": {"value": XPR204E_TARGET_SETTLING_S, "unit": "s", "evidence": "engineering_assumption", "source": "Reuse target value as synthetic rolling-window hold duration"},
        "legacy_px224e_hold_s": {"value": 3.0, "unit": "s", "evidence": "code_behavior", "source": "Unmodified scripts/balance_runtime.py Balance.for_model('PX224E')"},
        "filter_levels": {"value": _xpr_copy.deepcopy(XPR204E_FILTERS), "unit": "1", "evidence": "engineering_assumption", "source": "Only level names/order follow PX manual EN-27 section 5.3.3; tau and N are not provided by either manufacturer"},
        "underload_lower_bound_kg": {"value": {level: -setting["stability_divisions"] * XPR204E_READABILITY_KG for level, setting in XPR204E_FILTERS.items()}, "unit": "kg", "evidence": "engineering_assumption", "source": "Underload is filtered gross below -N*d; near-zero negative noise is displayed without clipping and does not reset stability merely because a raw sample is negative"},
        "manufacturer_underload_threshold_kg": {"value": None, "unit": "kg", "evidence": "unknown", "source": "No manufacturer lower detection threshold was supplied"},
        "startup_time_constants": {"value": 5.0, "unit": "1", "evidence": "engineering_assumption", "source": "Five-tau startup bounds residual of first noisy sample; not vendor firmware"},
        "zero_capture_fraction": {"value": 0.02, "unit": "1", "evidence": "engineering_assumption", "source": "Inherited legacy runtime capture rule"},
        "minimum_calibration_input_kg": {"value": XPR204E_CALIBRATION_MIN_KG, "unit": "kg", "evidence": "engineering_assumption", "source": "Numerical API envelope prevents effectively zero denominator; not a practical or manufacturer calibration mass"},
        "manufacturer_calibration_mass_kg": {"value": None, "unit": "kg", "evidence": "unknown", "source": "No manufacturer procedure or required calibration mass is implemented"},
        "calibration_error_kg": {"value": None, "unit": "kg", "evidence": "unknown", "source": "No measured calibration error or fault data supplied"},
    }
    return {"asset_types": {XPR204E_TYPE: {"catalog_id": "CHEM-001", "model": "METTLER TOLEDO XPR204E target / PX224E geometry family",
        "asset_version": "1.0.0", "state_slots": slots, "capabilities": caps, "parameters": parameters}},
        "scene_snapshot_keys": list(XPR204E_SCENE_KEYS),
        "scene_snapshot_schema": _xpr_object({sid: slots[sid]["value_schema"] for sid in XPR204E_SCENE_KEYS}),
        "trusted_scene_callback": "scene_adapter(instance_id, updates): each call receives the full typed scene snapshot; raw force, unquantized filter, gain and zero truth are excluded",
        "host_capabilities": {
            "sim.step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "XPR204ERuntime.step(dt_s, pan_load_forces_N=...)"},
            "host.set_environment": {"kind": "configuration", "visibility": "trusted_host_only", "binding": "XPR204ERuntime.set_environment(instance_id, tilt_rad=..., vibration=...)"}},
        "limitations": ["All reported measurement results are synthetic and quality=estimated.",
            "N is full displayed peak-to-peak range, not manufacturer repeatability or accuracy.",
            "High white-force noise requires coarse stability bands (Low 51.2mg, Medium 25.6mg, High 12.8mg).",
            "Underload uses a synthetic filtered lower bound of -N*d so noisy empty-pan readings can settle; this is not a manufacturer underload specification.",
            "AZT is not implemented; its 0.5d/1d/3d per-second drift settings are not stability thresholds.",
            "No contact solver, supported-force aggregation, robot action, real balance driver or fault calibration.",
            "No filesystem/process sandbox; deploy the trusted host separately from untrusted execution.",
            "Deduplication is in-memory only and does not survive a restart."]}


class XPR204ERuntime:
    """Trusted multi-instance host; only sessions should be exposed to agents."""
    def __init__(self, *, scene_adapter=None):
        self.scene_adapter = scene_adapter
        self.time_s = 0.0
        self._instances = {}
        self._principals = {"__public_agent__": ("agent", None)}
        self._requests = {}
        self._logs = {"__public_agent__": []}
        self._lock = _xpr_threading.RLock()
        self._metadata = export_xpr204e_contract_metadata()
        self._agent = XPR204ESession(self, "__public_agent__")

    def create_instance(self, instance_id, asset_type_id=XPR204E_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != XPR204E_TYPE:
                raise ValueError("Unique nonempty instance ID and declared asset type required")
            inst = {"balance": FilteredAnalyticalBalance(), "doors": {side: 0.0 for side in ("left", "right", "top")},
                    "vibration": False, "tilt_rad": 0.0, "revision": 0}
            if self.scene_adapter:
                self.scene_adapter(instance_id, self._scene(inst))
            self._instances[instance_id] = inst

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
        return XPR204ESession(self, principal_id)

    def describe(self, **kwargs):
        return self._agent.describe(**kwargs)

    def read(self, **kwargs):
        return self._agent.read(**kwargs)

    def invoke(self, **kwargs):
        return self._agent.invoke(**kwargs)

    def write(self, **kwargs):
        return self._agent.write(**kwargs)

    def describe_host(self):
        return _xpr_copy.deepcopy(self._metadata["host_capabilities"])

    @staticmethod
    def _values(inst):
        b = inst["balance"]
        r = b.read()
        observable = r["status"] in ("STABLE", "UNSTABLE") and b.filtered_force_N is not None
        values = {"power.enabled": b.powered, "balance.filter_level": b.filter_level, "balance.display_unit": b.unit,
            "balance.status": r["status"], "balance.stable": r["stable"],
            "balance.gross_kg": _xpr_quantize(b.gross_kg) if observable else None,
            "balance.net_kg": r["net_kg"], "balance.tare_kg": _xpr_quantize(b.tare_kg),
            "balance.calibrated": b.calibrated, "balance.task_ready": r["task_ready"], "balance.synthetic": True,
            "balance.raw_force_n": b.raw_force_N, "balance.filtered_force_n": b.filtered_force_N,
            "balance.gain_n_kg": b.g_cal, "balance.zero_kg": b.zero_kg, "balance.stability_elapsed_s": b.elapsed_s,
            "pan.installed": b.pan_installed, "environment.tilt_rad": inst["tilt_rad"], "environment.vibration": inst["vibration"]}
        values.update({f"door.{side}.opening_m": value for side, value in inst["doors"].items()})
        return values

    def debug_snapshot(self):
        with self._lock:
            return {"time_s": self.time_s, "instances": {key: {"revision": inst["revision"], "state": _xpr_copy.deepcopy(self._values(inst)),
                "filter_history": list(inst["balance"]._stability_samples), "filter_age_s": inst["balance"].filter_age_s}
                for key, inst in self._instances.items()}}

    def _scene(self, inst):
        values = self._values(inst)
        return {sid: values[sid] for sid in XPR204E_SCENE_KEYS}

    def set_environment(self, instance_id, *, tilt_rad=0.0, vibration=False):
        if not _xpr_number(tilt_rad) or abs(tilt_rad) > _xpr_math.pi / 2 or type(vibration) is not bool:
            raise ValueError("Finite tilt within +/-pi/2 and boolean vibration required")
        with self._lock:
            if instance_id not in self._instances:
                raise ValueError("Unknown instance")
            inst = self._instances[instance_id]
            inst["tilt_rad"], inst["vibration"] = float(tilt_rad), vibration
            inst["balance"].tilt_rad = float(tilt_rad)
            inst["balance"].reset_stability()
            inst["revision"] += 1

    def step(self, dt_s, *, pan_load_forces_N=None):
        """Force values are supported pan load (exclude pan weight), held for dt.

        This call advances every registered instance under one clock. Missing
        values retain the preceding trusted force input. Numerical substeps are
        at most 0.01 s; they do not create new independent contact-force samples.
        A callback failure leaves process/time advanced and raises RuntimeError.
        """
        if not _xpr_number(dt_s) or not 0 <= dt_s <= 60:
            raise ValueError("Finite host interval within 0..60 s required")
        forces = {} if pan_load_forces_N is None else pan_load_forces_N
        if not isinstance(forces, dict) or any(not isinstance(key, str) or not _xpr_number(value) for key, value in forces.items()):
            raise ValueError("Supported force mapping must contain finite SI values")
        with self._lock:
            if any(key not in self._instances for key in forces):
                raise ValueError("Unknown force target")
            if dt_s == 0:
                return
            n = max(1, int(_xpr_math.ceil(dt_s / 0.01)))
            h = dt_s / n
            for key, inst in self._instances.items():
                b = inst["balance"]
                force = forces.get(key, b.raw_force_N)
                door_fraction = max(inst["doors"].values()) / XPR204E_DOOR_MAX_M
                for _ in range(n):
                    b.update(force, h, door_fraction=door_fraction, tilt_rad=inst["tilt_rad"], vibration=inst["vibration"])
                inst["revision"] += 1
            self.time_s += dt_s
            if self.scene_adapter:
                failed = []
                for key, inst in self._instances.items():
                    try:
                        self.scene_adapter(key, self._scene(inst))
                    except Exception:
                        failed.append(key)
                if failed:
                    raise RuntimeError("Process and common clock advanced; scene synchronization failed") from None


class XPR204ESession:
    """Uniform describe/read/invoke/write interface with host-assigned identity."""
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
        return {"protocol_version": "1.0.0", "instance_id": instance_id if isinstance(instance_id, str) else None,
            "request_id": request_id if isinstance(request_id, str) else None, "operation_id": None, "status": status,
            "state_revision": inst["revision"] if inst else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": {} if result is None else _xpr_copy.deepcopy(result), "observations": {} if observations is None else _xpr_copy.deepcopy(observations), "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Instance is unavailable in this session.", "UNKNOWN_CAPABILITY": "Capability is unavailable.",
            "INVALID_ARGUMENT": "Arguments do not meet declared schema or limits.", "ACCESS_DENIED": "Request is outside session access policy.",
            "PRECONDITION_FAILED": "Required operating state or configuration is unavailable.",
            "REQUEST_CONFLICT": "Request ID already belongs to different arguments.", "UNSUPPORTED": "Execution is unavailable in this adapter.",
            "DEVICE_ERROR": "Scene callback did not complete the command."}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": messages[code],
            "retryable": False, "side_effects": "unknown" if code == "DEVICE_ERROR" else "none", "progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        host = self.__host
        with host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(None, "INVALID_ARGUMENT")
            if instance_id is not None and not self._instance(instance_id):
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != XPR204E_TYPE:
                return self._error(None, "INVALID_ARGUMENT")
            _, scope = host._principals[self.__principal]
            if scope is not None and not scope:
                return self._error(instance_id, "ACCESS_DENIED")
            spec = _xpr_copy.deepcopy(host._metadata["asset_types"][XPR204E_TYPE])
            spec["state_slots"] = {sid: value for sid, value in spec["state_slots"].items() if self._visible(value)}
            for cap in spec["capabilities"].values():
                cap["state_effects"] = [effect for effect in cap["state_effects"] if effect["state_id"] in spec["state_slots"]]
                cap["observes"] = [sid for sid in cap["observes"] if sid in spec["state_slots"]]
                if cap["scene_backend_required"] and host.scene_adapter is None:
                    cap["availability"] = "unsupported"
            return {"protocol_version": "1.0.0", "asset_type_id": XPR204E_TYPE, "instance_id": instance_id,
                "adapter": "basic40_runtime_measure.XPR204ESession", **spec,
                "limitations": _xpr_copy.deepcopy(host._metadata["limitations"])}

    def read(self, *, instance_id, state_ids=None):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if not inst:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = host._metadata["asset_types"][XPR204E_TYPE]["state_slots"]
            if state_ids is None:
                state_ids = [sid for sid, value in slots.items() if self._visible(value)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids) or len(state_ids) != len(set(state_ids)):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(sid not in slots or not self._visible(slots[sid]) for sid in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            values = host._values(inst)
            observations = {sid: {"value": values[sid], "unit": slots[sid]["unit"],
                "quality": "unavailable" if values[sid] is None else "estimated" if slots[sid]["origin"] == "derived" else "valid",
                "sample_time_s": host.time_s, "sample_window_s": None,
                "reason": "No reading is available in the current acquisition, power, pan or range state." if values[sid] is None else None,
                "synthetic": slots[sid]["origin"] == "derived"} for sid in state_ids}
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
                digest = _xpr_hashlib.sha256(_xpr_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = (self.__principal, instance_id, request_id)
            if key in host._requests:
                old_digest, response = host._requests[key]
                return _xpr_copy.deepcopy(response) if digest == old_digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, _xpr_copy.deepcopy(response))
            host._logs[self.__principal].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id,
                "response": _xpr_copy.deepcopy(response)})
            return response

    def _checked(self, instance_id, inst, cap, args, request_id):
        host = self.__host
        fail = lambda code, status="rejected": self._error(instance_id, code, request_id, status)
        spec = host._metadata["asset_types"][XPR204E_TYPE]
        if cap not in spec["capabilities"]:
            return fail("UNKNOWN_CAPABILITY")
        if set(args) != set(spec["capabilities"][cap]["input_schema"]["properties"]):
            return fail("INVALID_ARGUMENT")
        if cap == "power.set" and type(args["enabled"]) is not bool:
            return fail("INVALID_ARGUMENT")
        if cap == "balance.set_filter" and (not isinstance(args["level"], str) or args["level"] not in XPR204E_FILTERS):
            return fail("INVALID_ARGUMENT")
        if cap == "balance.set_display_unit" and args["unit"] not in ("kg", "g"):
            return fail("INVALID_ARGUMENT")
        if cap == "balance.calibrate_span" and (not _xpr_number(args["known_mass_kg"]) or not XPR204E_CALIBRATION_MIN_KG <= args["known_mass_kg"] <= XPR204E_CAPACITY_KG):
            return fail("INVALID_ARGUMENT")
        if cap == "scene.set_door" and (args["side"] not in ("left", "right", "top") or not _xpr_number(args["opening_m"]) or not 0 <= args["opening_m"] <= XPR204E_DOOR_MAX_M):
            return fail("INVALID_ARGUMENT")
        if cap == "scene.set_pan" and type(args["installed"]) is not bool:
            return fail("INVALID_ARGUMENT")
        b = inst["balance"]
        if cap in ("balance.tare", "balance.zero", "balance.calibrate_span") and (not b.powered or not b.stable):
            return fail("PRECONDITION_FAILED")
        if cap == "balance.zero" and abs(b.gross_kg) > 0.02 * b.capacity_kg:
            return fail("PRECONDITION_FAILED")
        if cap == "balance.calibrate_span" and (b.security_locked or b.filtered_force_N is None or b.filtered_force_N <= 0):
            return fail("PRECONDITION_FAILED")
        if cap == "balance.set_filter" and b.security_locked:
            return fail("PRECONDITION_FAILED")
        if cap == "scene.set_pan" and (b.powered or abs(b.raw_force_N / b.g_cal) > b.readability_kg):
            return fail("PRECONDITION_FAILED")
        if cap.startswith("scene.") and host.scene_adapter is None:
            return fail("UNSUPPORTED", "unsupported")
        if cap == "balance.reading":
            values = host._values(inst)
            return self._envelope(instance_id, request_id, result={"status": values["balance.status"], "stable": values["balance.stable"],
                "gross_kg": values["balance.gross_kg"], "net_kg": values["balance.net_kg"], "synthetic": True})
        candidate = _xpr_copy.deepcopy(inst)
        b = candidate["balance"]
        if cap == "power.set":
            b.set_power(args["enabled"])
            result = {"enabled": b.powered}
        elif cap == "balance.set_filter":
            b.set_filter(args["level"])
            result = {"level": b.filter_level}
        elif cap == "balance.tare":
            b.press("tare")
            result = {"tare_kg": _xpr_quantize(b.tare_kg)}
        elif cap == "balance.zero":
            b.press("zero")
            result = {"zeroed": True}
        elif cap == "balance.set_display_unit":
            b.unit = args["unit"]
            result = {"unit": b.unit}
        elif cap == "balance.calibrate_span":
            try:
                b.calibrate_known_mass(args["known_mass_kg"])
            except ValueError:
                return fail("PRECONDITION_FAILED")
            result = {"calibrated": True, "synthetic": True}
        elif cap == "scene.set_door":
            candidate["doors"][args["side"]] = float(args["opening_m"])
            b.door_fraction = max(candidate["doors"].values()) / XPR204E_DOOR_MAX_M
            b.reset_stability()
            result = {"side": args["side"], "opening_m": float(args["opening_m"])}
        else:
            b.pan_installed = b.pan_latched = args["installed"]
            b.reset_stability()
            result = {"installed": b.pan_installed}
        if host.scene_adapter:
            try:
                host.scene_adapter(instance_id, host._scene(candidate))
            except Exception:
                return fail("DEVICE_ERROR", "failed")
        candidate["revision"] += 1
        host._instances[instance_id] = candidate
        return self._envelope(instance_id, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        host = self.__host
        with host._lock:
            if not self._instance(instance_id):
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slots = host._metadata["asset_types"][XPR204E_TYPE]["state_slots"]
            if not isinstance(state_id, str) or state_id not in slots or not self._visible(slots[state_id]) or not slots[state_id]["write_via"]:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slots[state_id]["write_via"]
            arguments = {**route["fixed_arguments"], route["parameter"]: value}
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments=arguments, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return _xpr_copy.deepcopy(self.__host._logs[self.__principal])


def xpr204e_filter_experiments(*, seeds=range(10), duration_s=60.0):
    """Specified 100 Hz Gaussian-force test, including baseline and display error.

    These are fixed 100 g and empty-pan synthetic fixtures, not task/sample truth.
    Results only characterize this uncorrelated-noise experiment, not arbitrary
    contact noise.
    """
    import platform
    seeds = tuple(seeds)
    trials, empty_pan_trials = [], []
    dt = 0.01
    for load, fixture_trials in ((0.1, trials), (0.0, empty_pan_trials)):
        for level in XPR204E_FILTERS:
            for sigma in (1e-6, 1e-4, 1e-3):
                for seed in seeds:
                    rng = _xpr_random.Random(seed)
                    balance = FilteredAnalyticalBalance(filter_level=level)
                    balance.set_power(True)
                    first = None
                    stable_error = None
                    for index in range(1, int(duration_s / dt) + 1):
                        result = balance.update(load * 9.80665 + rng.gauss(0, sigma), dt)
                        if result["stable"]:
                            first = round(index * dt, 8)
                            stable_error = result["net_kg"] - load
                            break
                    fixture_trials.append({"level": level, "reference_load_kg": load, "sigma_force_N": sigma, "seed": seed,
                        "first_stable_s": first, "net_error_at_first_stable_kg": stable_error,
                        "filtered_peak_to_peak_at_stable_kg": balance._window_range_kg,
                        "result": "passed" if first is not None else "failed"})
    baseline = []
    for sigma in (1e-6, 1e-4, 1e-3):
        rng = _xpr_random.Random(0)
        balance = _XPRLegacyBalance.for_model("PX224E")
        balance.press("zero")
        first = None
        for index in range(1, int(duration_s / dt) + 1):
            if balance.update(0.1 * 9.80665 + rng.gauss(0, sigma), dt)["stable"]:
                first = round(index * dt, 8)
                break
        baseline.append({"sigma_force_N": sigma, "first_stable_s": first, "duration_s": duration_s})
    return {"catalog_id": "CHEM-001", "recorded_at": _xpr_datetime.now(_xpr_timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "method": "100 Hz independent Gaussian force noise on 0.1 kg and empty-pan synthetic fixtures; first STABLE within 60 s",
        "synthetic": True, "noise_distribution": "independent Gaussian; zero mean", "dt_s": dt,
        "duration_s": duration_s, "seed_values": list(seeds), "filter_parameters": _xpr_copy.deepcopy(XPR204E_FILTERS),
        "numeric_filter_evidence": "engineering_assumption", "manufacturer_filter_numbers": None,
        "manufacturer_level_evidence": "references/PX_manual.txt lines 1161-1168; section 5.3.3, EN-27",
        "stability_hold_s": XPR204E_TARGET_SETTLING_S, "warmup": "5*tau from filter initialization",
        "baseline_unmodified_px224e": baseline, "trials": trials, "empty_pan_trials": empty_pan_trials,
        "result": "passed" if all(row["result"] == "passed" for row in trials + empty_pan_trials) else "failed",
        "boundaries": "STABLE means a full filtered display window is within N divisions; coarse high-noise bands do not reproduce the XPR204E 0.04 mg typical repeatability. No correlated/bias/contact-solver noise, actual hardware, robot or process/filesystem isolation was validated."}


def write_xpr204e_runtime_reports(output_directory):
    """Write only the caller-designated asset output directory; no global files."""
    import importlib.util
    import io
    import platform
    import unittest
    root = _xpr_Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("test_basic40_measure", root / "tests/test_basic40_measure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.XPR204ERuntimeTests)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(module.FilteredAnalyticalBalanceTests))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"catalog_id": "CHEM-001", "recorded_at": _xpr_datetime.now(_xpr_timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "method": "unittest runtime inputs, state transitions, noise window, two instances, API truth filtering, request retry and scene callback failure",
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "result": "passed" if result.wasSuccessful() else "failed", "output": stream.getvalue(),
        "not_run": ["real_hardware", "robot_execution", "contact_solver", "process_filesystem_security_isolation"],
        "scene_scope": "A recording callback checks synchronization, not evaluated Blender geometry."}
    directory = _xpr_Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (("runtime_validation.json", report), ("filter_experiments.json", xpr204e_filter_experiments()),
                        ("runtime_metadata.json", export_xpr204e_contract_metadata())):
        (directory / name).write_text(_xpr_json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


# CHEM-103 append-only runtime: catalogue limits and synthetic command playback.
import copy as _gamry_copy
import hashlib as _gamry_hashlib
import json as _gamry_json
import math as _gamry_math
import threading as _gamry_threading
from fractions import Fraction as _gamry_Fraction

GAMRY_REFERENCE620_TYPE = "instrument.gamry_reference620"
GAMRY_REFERENCE620_PORTS = ("working_electrode", "reference_electrode", "counter_electrode", "cell")
GAMRY_REFERENCE620_MIN_INTERVAL_S = 3.333e-6
GAMRY_REFERENCE620_MAX_SAMPLES = 10000  # Runtime resource budget, not a vendor specification.
GAMRY_REFERENCE620_TEMPLATE_CAPABILITIES = ("template.list", "template.read", "electrochem.template_cv")


def _gamry_template_module():
    """Lazy import of the public-data template extension (scripts/basic40_templates/gamry_ref620.py)."""
    try:
        from .basic40_templates import gamry_ref620 as module
    except ImportError:
        from basic40_templates import gamry_ref620 as module
    return module


def _gamry_number(value):
    try:
        return type(value) in (int, float) and _gamry_math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _gamry_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _gamry_num(unit, minimum=None, maximum=None, nullable=False):
    spec = {"type": "number", "x-unit": unit}
    if minimum is not None:
        spec["minimum"] = minimum
    if maximum is not None:
        spec["maximum"] = maximum
    return {"anyOf": [spec, {"type": "null"}]} if nullable else spec


def export_gamry_reference620_contract_metadata():
    """Static contract metadata. No live samples or host identities are exported."""
    boolean = {"type": "boolean"}
    synthetic = {"type": "boolean", "const": True}
    nullable_id = {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}
    status_schema = {"type": "string", "enum": ["idle", "running", "completed", "stopped"]}
    program_variants = []
    for mode, unit, limit in (("potential", "V", 11.0), ("current", "A", 0.6)):
        program_variants.append(_gamry_object({"mode": {"type": "string", "const": mode},
            "sample_interval_s": _gamry_num("s", GAMRY_REFERENCE620_MIN_INTERVAL_S),
            "values": {"type": "array", "items": _gamry_num(unit, -limit, limit),
                       "minItems": 1, "maxItems": GAMRY_REFERENCE620_MAX_SAMPLES}}))
    program_schema = {"anyOf": program_variants}
    sample_schema = _gamry_object({"time_s": _gamry_num("s", 0), "elapsed_s": _gamry_num("s", 0),
        "commanded_potential_v": _gamry_num("V", -11, 11, True),
        "commanded_current_a": _gamry_num("A", -0.6, 0.6, True),
        "measured_potential_v": {"type": "null"}, "measured_current_a": {"type": "null"},
        "synthetic": synthetic})
    series_schema = {"type": "array", "items": sample_schema, "maxItems": GAMRY_REFERENCE620_MAX_SAMPLES}
    slots = {}
    def slot(sid, unit, origin, schema, description, route=None, visibility="agent", sampling="instant"):
        slots[sid] = {"description_zh": description, "value_schema": schema, "unit": unit,
            "origin": origin, "visibility": visibility, "access": "read_write" if route else "read_only",
            "write_via": route, "reference_conditions": None,
            "read_binding": "GamryReference620Session.read(instance_id=..., state_ids=[state_id])",
            "sampling": {"mode": sampling, "time_domain": "simulation", "window": None}}
    def route(capability_id, parameter, fixed_arguments=None):
        return {"capability_id": capability_id, "parameter": parameter, "fixed_arguments": fixed_arguments or {}}
    slot("power.enabled", "1", "setpoint", boolean, "供电逻辑状态；关电立即停止命令输出。", route("power.set", "enabled"))
    for port in GAMRY_REFERENCE620_PORTS:
        slot("slot." + port + ".connection_id", "1", "scene", nullable_id,
             "宿主场景连接槽的逻辑引用；null表示未连接，不证明接触或机器人接线已完成。",
             route("slot.set_connection", "connection_id", {"port_id": port}))
    slot("waveform.program", "1", "setpoint", {"anyOf": program_variants + [{"type": "null"}]},
         "显式离散命令程序；电位与电流互斥；采样间隔须不小于表列最小时间基准。",
         route("electrochem.configure_waveform", "program"))
    slot("waveform.status", "1", "derived", status_schema, "离散播放状态，不代表真实样本实验完成。")
    slot("waveform.running", "1", "derived", boolean, "是否等待宿主推进来播放剩余命令。")
    slot("waveform.sample_count", "1", "derived", {"type": "integer", "minimum": 0, "maximum": GAMRY_REFERENCE620_MAX_SAMPLES, "x-unit": "1"}, "已播放命令点数。")
    slot("waveform.elapsed_s", "s", "derived", _gamry_num("s", 0), "本次播放已推进的有效时间，完成后停在最后命令点。")
    slot("output.commanded_potential_v", "V", "derived", _gamry_num("V", -11, 11, True), "当前合成电位命令；停止或使用电流模式时为null。")
    slot("output.commanded_current_a", "A", "derived", _gamry_num("A", -0.6, 0.6, True), "当前合成电流命令；停止或使用电位模式时为null。")
    slot("acquisition.series", "1", "derived", series_schema, "按时间排列的命令回放序列；两种实测通道均为null。", sampling="series")
    slot("acquisition.synthetic", "1", "derived", synthetic, "本适配器产生的序列始终标记synthetic。")
    for sid, unit, desc in (("measurement.potential_v", "V", "未接入真实电位传感器；无响应模型。"),
                           ("measurement.current_a", "A", "未接入真实电流传感器；无响应模型。"),
                           ("method.scan_rate_v_s", "V/s", "未配置连续扫描速率；本批仅显式离散程序。"),
                           ("method.frequency_hz", "Hz", "未配置EIS频率；EIS执行不支持。"),
                           ("method.amplitude_v", "V", "未配置EIS振幅；无虚构默认值。"),
                           ("method.ir_compensation_ohm", "ohm", "未提供iR补偿参数，保持未知。")):
        slot(sid, unit, "sensor" if sid.startswith("measurement.") else "setpoint", {"type": "null"}, desc)
    slot("scheduler.started_at_s", "s", "process_truth", _gamry_num("s", 0, nullable=True),
         "宿主内部调度起点；仅debug/evaluator可见。", visibility="debug")
    playback_effects = ["waveform.status", "waveform.running", "waveform.sample_count", "waveform.elapsed_s",
        "output.commanded_potential_v", "output.commanded_current_a", "acquisition.series", "scheduler.started_at_s"]
    stop_effects = ["waveform.status", "waveform.running", "output.commanded_potential_v", "output.commanded_current_a"]
    connection_inputs = {"port_id": {"type": "string", "enum": list(GAMRY_REFERENCE620_PORTS)}, "connection_id": nullable_id}
    series_result = {"samples": series_schema, "synthetic": synthetic,
                    "response_model": {"type": "null"}, "status": status_schema}
    definitions = {
        "power.set": ({"enabled": boolean}, {"enabled": boolean}, {"enabled": True},
            "设置逻辑电源；关电保留已播放序列并停止输出。", ["power.enabled"] + stop_effects, ["power.enabled"], "device_command", ["body"], "参数为布尔值。"),
        "slot.set_connection": (connection_inputs, connection_inputs, {"port_id": "working_electrode", "connection_id": "electrode_we_01"},
            "登记或清除单个逻辑连接槽；不执行机械接线。", ["slot." + p + ".connection_id" for p in GAMRY_REFERENCE620_PORTS],
            ["slot." + p + ".connection_id" for p in GAMRY_REFERENCE620_PORTS], "configuration",
            [p + "_slot" for p in GAMRY_REFERENCE620_PORTS], "不得处于播放状态；连接引用为非空字符串或null。"),
        "electrochem.configure_waveform": ({"program": program_schema}, {"program": program_schema},
            {"program": {"mode": "potential", "sample_interval_s": 0.01, "values": [-1.0, 0.0, 1.0]}},
            "配置显式离散电位或电流命令，清空上一轮序列；不推导扫描速率或样本响应。",
            ["waveform.program"] + playback_effects, ["waveform.program"], "configuration", ["body"],
            "不得处于播放状态；全部数值有限，电位±11V、电流±0.6A，间隔≥3.333e-6s。"),
        "electrochem.start": ({}, {"status": status_schema, "synthetic": synthetic}, {},
            "开始等待宿主时钟播放离散程序；此调用不产生样点也不推进时间。", playback_effects,
            ["waveform.status", "waveform.running"], "device_command", ["body"] + [p + "_slot" for p in GAMRY_REFERENCE620_PORTS],
            "电源开启、四槽均已连接、程序已配置且当前未播放；整份计划的输出时间戳必须有限且逐点严格递增。"),
        "electrochem.stop": ({}, {"status": status_schema, "synthetic": synthetic}, {},
            "停止尚未播放的命令，保留已播放序列；输出设为null。", stop_effects,
            ["waveform.status", "waveform.running"], "device_command", ["body"], "实例可用；允许幂等停止。"),
        "electrochem.read_series": ({}, series_result, {}, "只读合成命令序列；真实测量通道为null，不推进时间。",
            [], ["acquisition.series", "acquisition.synthetic", "waveform.status"], "query", ["body"], "实例可用。"),
        "electrochem.eis": ({}, {}, {}, "表中EIS范围保留为型号规格；本批不生成阻抗谱或虚构成功。",
            [], [], "device_command", ["body"], "当前适配器不支持执行EIS。"),
    }
    caps = {}
    for name, (inputs, outputs, example, description, effects, observes, kind, components, preconditions) in definitions.items():
        supported = name != "electrochem.eis"
        caps[name] = {"availability": "implemented" if supported else "unsupported", "visibility": "agent", "kind": kind,
            "description_zh": description, "input_schema": _gamry_object(inputs), "output_schema": _gamry_object(outputs),
            "example_arguments": [example], "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": description} for sid in effects],
            "observes": observes, "component_ids": components, "preconditions_zh": preconditions,
            "binding": f"basic40_runtime_measure.GamryReference620Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
            "completion_criterion_zh": "同步提交所列逻辑状态；播放完成须由宿主推进并读取completed状态。" if supported else "返回unsupported且无状态变化。",
            "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "仅逻辑槽位与离散电命令，不驱动几何或接触求解。"},
            "implementation": {"process": "implemented" if supported else "absent", "host": "implemented" if supported else "absent", "scene": "absent", "robot": "absent", "hardware": "absent"},
            "error_codes": ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "ACCESS_DENIED", "PRECONDITION_FAILED", "BUSY", "REQUEST_CONFLICT", "UNSUPPORTED"],
            "retry": "same_request_id; in-memory only"}
    parameters = {
        "potential_limit_v": {"value": 11.0, "unit": "V", "evidence": "catalog", "note_zh": "参数与来源第108行，整机±11V命令限幅。"},
        "current_limit_a": {"value": 0.6, "unit": "A", "evidence": "catalog", "note_zh": "参数与来源第108行，±600mA换算SI；不是电极部件参数。"},
        "minimum_time_base_s": {"value": GAMRY_REFERENCE620_MIN_INTERVAL_S, "unit": "s", "evidence": "catalog", "note_zh": "表列3.333µs；映射为最小程序点间隔为工程假设。"},
        "eis_frequency_range_hz": {"value": [1e-5, 5e6], "unit": "Hz", "evidence": "catalog", "note_zh": "保留型号规格，未实现EIS执行。"},
        "maximum_program_samples": {"value": GAMRY_REFERENCE620_MAX_SAMPLES, "unit": "1", "evidence": "engineering_assumption", "note_zh": "本地资源预算，不是厂家最大点数。"},
        "cell_resistance_ohm": {"value": None, "unit": "ohm", "evidence": "unknown", "note_zh": "未提供真实电化学池参数。"},
        "electrode_area_m2": {"value": None, "unit": "m^2", "evidence": "unknown", "note_zh": "未提供电极几何或有效面积。"},
        "calibration_error_v": {"value": None, "unit": "V", "evidence": "unknown", "note_zh": "未提供校准或误差数据。"},
    }
    return {"asset_types": {GAMRY_REFERENCE620_TYPE: {"catalog_id": "CHEM-103", "model": "Gamry Reference 620", "asset_version": "1.0.1",
        "state_slots": slots, "capabilities": caps, "parameters": parameters}},
        "host_capabilities": {"sim.step": {"kind": "simulation", "visibility": "trusted_host_only",
            "binding": "GamryReference620Runtime.step(dt_s)", "effects": playback_effects,
            "semantics_zh": "显式推进所有实例共享时钟；输入数值的十进制表示转精确有理数进行累加和计划比较；正步进播放覆盖的计划点（包括起点），零步进不产生效果。输出仍为SI浮点数，完整计划须严格可分辨。"}},
        "limitations": ["All sequence data are synthetic command playback, not electrochemical measurements.",
            "Measured potential/current and unprovided cell/method parameters remain null.",
            "No EIS, Nernst, Coulomb, sample response, iR compensation, noise, calibration or hardware driver is implemented.",
            "Sample playback and minimum-time-base interpretation are engineering assumptions, not vendor firmware emulation.",
            "Clock inputs use their shortest decimal string as an exact rational; this numerical convention makes decimal step partitions consistent. Exposed timestamps remain floats; non-monotonic or indistinguishable complete schedules are rejected before start.",
            "Slot references are logical only; compatibility, electrical contact and robot connection are not validated.",
            "Session filtering is an API boundary only; this is not a Python/filesystem security sandbox.",
            "Request deduplication lasts only for this in-memory host lifetime."]}


class GamryReference620Runtime:
    """Trusted host for independent instances under an explicitly stepped clock."""
    def __init__(self):
        self._time_fraction = _gamry_Fraction(0)
        self._instances = {}
        self._principals = {"__public_agent__": ("agent", None)}
        self._logs = {"__public_agent__": []}
        self._requests = {}
        self._lock = _gamry_threading.RLock()
        self._metadata = export_gamry_reference620_contract_metadata()
        self._agent = GamryReference620Session(self, "__public_agent__")

    def create_instance(self, instance_id, asset_type_id=GAMRY_REFERENCE620_TYPE):
        with self._lock:
            if type(instance_id) is not str or not instance_id or instance_id in self._instances or asset_type_id != GAMRY_REFERENCE620_TYPE:
                raise ValueError("A unique nonempty instance ID and the declared asset type are required")
            self._instances[instance_id] = {"revision": 0, "enabled": False,
                "connections": {p: None for p in GAMRY_REFERENCE620_PORTS}, "program": None,
                "status": "idle", "running": False, "index": 0, "elapsed_s": 0.0,
                "potential_v": None, "current_a": None, "samples": [], "started_at_s": None,
                "started_at_fraction": None, "schedule": []}

    @property
    def time_s(self):
        """Public SI float view of the single authoritative rational host clock."""
        return float(self._time_fraction)

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if type(principal_id) is not str or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Invalid or already registered host identity")
            if instance_ids is not None and (type(instance_ids) not in (list, tuple, set, frozenset) or any(type(x) is not str for x in instance_ids)):
                raise ValueError("Instance scope must be a collection of instance IDs")
            scope = None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope <= self._instances.keys():
                raise ValueError("Unknown instance in identity scope")
            self._principals[principal_id] = (visibility, scope)
            self._logs[principal_id] = []

    def session(self, principal_id):
        if type(principal_id) is not str or principal_id not in self._principals:
            raise ValueError("Identity must be registered by the trusted host")
        return GamryReference620Session(self, principal_id)

    def describe(self, **kwargs):
        return self._agent.describe(**kwargs)

    def read(self, **kwargs):
        return self._agent.read(**kwargs)

    def invoke(self, **kwargs):
        return self._agent.invoke(**kwargs)

    def write(self, **kwargs):
        return self._agent.write(**kwargs)

    def describe_host(self):
        return _gamry_copy.deepcopy(self._metadata["host_capabilities"])

    def host_load_template_cell(self, instance_id, cell):
        """Trusted host only: attach a template-backed cell (public redox library); agents never see its contents."""
        with self._lock:
            inst = self._instances.get(instance_id) if type(instance_id) is str else None
            if inst is None or inst["running"]:
                raise ValueError("A known instance without active playback is required")
            inst["template_cell"] = _gamry_template_module().build_cell(cell)
            return {"loaded": True, "template_backed": True, "synthetic": False}

    @staticmethod
    def _values(inst):
        values = {"power.enabled": inst["enabled"], "waveform.program": inst["program"],
            "waveform.status": inst["status"], "waveform.running": inst["running"],
            "waveform.sample_count": inst["index"], "waveform.elapsed_s": inst["elapsed_s"],
            "output.commanded_potential_v": inst["potential_v"], "output.commanded_current_a": inst["current_a"],
            "acquisition.series": inst["samples"], "acquisition.synthetic": True,
            "measurement.potential_v": None, "measurement.current_a": None,
            "method.scan_rate_v_s": None, "method.frequency_hz": None,
            "method.amplitude_v": None, "method.ir_compensation_ohm": None,
            "scheduler.started_at_s": inst["started_at_s"]}
        values.update({"slot." + p + ".connection_id": value for p, value in inst["connections"].items()})
        return values

    def debug_snapshot(self):
        with self._lock:
            return {"time_s": self.time_s, "instances": {key: {"revision": inst["revision"],
                "state": _gamry_copy.deepcopy(self._values(inst))} for key, inst in self._instances.items()}}

    def step(self, dt_s):
        """Positive host steps emit scheduled command points, never sample chemistry.

        Inputs are interpreted by their shortest decimal representation, then
        exact rational addition/comparison controls the host clock and schedule.
        Each timestamp comes from the absolute start plus index*interval; chunk
        boundaries do not set the sampling cadence. Zero is a pure no-op. The
        first point is timestamped at the start but emitted only on a positive
        host step. Completion de-energizes the command outputs and retains all
        emitted samples. All registered instances share this host clock.
        """
        if not _gamry_number(dt_s) or dt_s < 0:
            raise ValueError("Finite nonnegative SI host interval required")
        with self._lock:
            target_fraction = self._time_fraction + _gamry_Fraction(str(dt_s))
            try:
                target = float(target_fraction)
            except OverflowError:
                raise ValueError("Host clock exceeds representable range") from None
            if not _gamry_number(target) or (dt_s > 0 and target <= self.time_s):
                raise ValueError("Host clock interval exceeds representable precision or range")
            if dt_s == 0:
                return
            candidates = _gamry_copy.deepcopy(self._instances)
            for inst in candidates.values():
                if not inst["running"]:
                    continue
                before = self._values(inst)
                program = inst["program"]
                duration = inst["schedule"][-1][1]
                inst["elapsed_s"] = min(float(target_fraction - inst["started_at_fraction"]), duration)
                while inst["index"] < len(program["values"]):
                    scheduled_fraction, offset = inst["schedule"][inst["index"]]
                    if scheduled_fraction > target_fraction:
                        break
                    timestamp = float(scheduled_fraction)
                    value = program["values"][inst["index"]]
                    potential = value if program["mode"] == "potential" else None
                    current = value if program["mode"] == "current" else None
                    inst["samples"].append({"time_s": timestamp, "elapsed_s": offset,
                        "commanded_potential_v": potential, "commanded_current_a": current,
                        "measured_potential_v": None, "measured_current_a": None, "synthetic": True})
                    inst["potential_v"], inst["current_a"] = potential, current
                    inst["index"] += 1
                if inst["index"] == len(program["values"]):
                    inst["running"] = False
                    inst["status"] = "completed"
                    inst["potential_v"] = inst["current_a"] = None
                if before != self._values(inst):
                    inst["revision"] += 1
            self._instances = candidates
            self._time_fraction = target_fraction


class GamryReference620Session:
    """Expose only this facade to agents; identities are assigned by the host."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal = host, principal_id

    def _visible(self, spec):
        return spec["visibility"] == "agent" or self.__host._principals[self.__principal][0] in ("debug", "evaluator")

    def _instance(self, instance_id):
        if type(instance_id) is not str:
            return None
        _, scope = self.__host._principals[self.__principal]
        return self.__host._instances.get(instance_id) if scope is None or instance_id in scope else None

    def _envelope(self, instance_id, request_id=None, *, result=None, observations=None, status="succeeded", error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": "1.0.0", "instance_id": instance_id if type(instance_id) is str else None,
            "request_id": request_id if type(request_id) is str else None, "operation_id": None, "status": status,
            "state_revision": None if inst is None else inst["revision"],
            "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": _gamry_copy.deepcopy({} if result is None else result),
            "observations": _gamry_copy.deepcopy({} if observations is None else observations), "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Instance is unavailable in this session.", "UNKNOWN_CAPABILITY": "Capability is unavailable.",
            "INVALID_ARGUMENT": "Arguments do not meet the declared schema, finite-number or resource limits.",
            "ACCESS_DENIED": "Request is outside the session access policy.",
            "PRECONDITION_FAILED": "Required operating state or representable clock configuration is unavailable.",
            "BUSY": "Stop active waveform playback before changing this configuration.",
            "REQUEST_CONFLICT": "Request ID already belongs to different arguments.",
            "UNSUPPORTED": "This execution is unavailable in the current adapter."}
        return self._envelope(instance_id, request_id, status=status,
            error={"code": code, "message": messages[code], "retryable": False, "side_effects": "none", "progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        host = self.__host
        with host._lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._error(None, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != GAMRY_REFERENCE620_TYPE:
                return self._error(None, "INVALID_ARGUMENT")
            _, scope = host._principals[self.__principal]
            if scope is not None and not scope:
                return self._error(instance_id, "ACCESS_DENIED")
            spec = _gamry_copy.deepcopy(host._metadata["asset_types"][GAMRY_REFERENCE620_TYPE])
            spec["state_slots"] = {sid: value for sid, value in spec["state_slots"].items() if self._visible(value)}
            spec["capabilities"] = {cid: value for cid, value in spec["capabilities"].items() if self._visible(value)}
            spec["capabilities"].update(_gamry_template_module().capability_metadata())  # additive public-data extension
            for capability in spec["capabilities"].values():
                capability["state_effects"] = [effect for effect in capability["state_effects"] if effect["state_id"] in spec["state_slots"]]
                capability["observes"] = [sid for sid in capability["observes"] if sid in spec["state_slots"]]
            return {"protocol_version": "1.0.0", "asset_type_id": GAMRY_REFERENCE620_TYPE, "instance_id": instance_id,
                "adapter": "basic40_runtime_measure.GamryReference620Session", **spec,
                "limitations": _gamry_copy.deepcopy(host._metadata["limitations"])}

    def read(self, *, instance_id, state_ids=None):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = host._metadata["asset_types"][GAMRY_REFERENCE620_TYPE]["state_slots"]
            if state_ids is None:
                state_ids = [sid for sid, value in slots.items() if self._visible(value)]
            if type(state_ids) is not list or any(type(sid) is not str for sid in state_ids) or len(state_ids) != len(set(state_ids)):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(sid not in slots or not self._visible(slots[sid]) for sid in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            values = host._values(inst)
            observations = {}
            for sid in state_ids:
                series = sid == "acquisition.series"
                derived = slots[sid]["origin"] == "derived"
                observations[sid] = {"value": values[sid], "unit": slots[sid]["unit"],
                    "quality": "unavailable" if values[sid] is None else "estimated" if derived else "valid",
                    "sample_time_s": host.time_s,
                    "sample_window_s": [inst["samples"][0]["time_s"], inst["samples"][-1]["time_s"]] if series and inst["samples"] else None,
                    "reason": slots[sid]["description_zh"] if values[sid] is None else None,
                    "synthetic": derived}
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if type(capability_id) is not str or type(arguments) is not dict or type(request_id) is not str or not request_id or len(request_id) > 256:
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = _gamry_hashlib.sha256(_gamry_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError, RecursionError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = (self.__principal, instance_id, request_id)
            if key in host._requests:
                old_digest, response = host._requests[key]
                return _gamry_copy.deepcopy(response) if old_digest == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = (digest, _gamry_copy.deepcopy(response))
            host._logs[self.__principal].append({"instance_id": instance_id, "capability_id": capability_id,
                "request_id": request_id, "response": _gamry_copy.deepcopy(response)})
            return response

    def _checked(self, instance_id, inst, cap, args, request_id):
        host = self.__host
        if cap in GAMRY_REFERENCE620_TEMPLATE_CAPABILITIES:  # additive public-data extension; legacy capabilities unchanged
            return _gamry_template_module().invoke(self, host, instance_id, inst, cap, args, request_id,
                                                   evaluator=self._visible({"visibility": "debug"}))
        fail = lambda code, status="rejected": self._error(instance_id, code, request_id, status)
        capabilities = host._metadata["asset_types"][GAMRY_REFERENCE620_TYPE]["capabilities"]
        if cap not in capabilities or not self._visible(capabilities[cap]):
            return fail("UNKNOWN_CAPABILITY")
        if set(args) != set(capabilities[cap]["input_schema"]["properties"]):
            return fail("INVALID_ARGUMENT")
        if cap == "electrochem.eis":
            return fail("UNSUPPORTED", "unsupported")
        if cap == "power.set" and type(args["enabled"]) is not bool:
            return fail("INVALID_ARGUMENT")
        if cap == "slot.set_connection":
            port, connection = args["port_id"], args["connection_id"]
            if type(port) is not str or port not in GAMRY_REFERENCE620_PORTS or (connection is not None and (type(connection) is not str or not connection.strip())):
                return fail("INVALID_ARGUMENT")
        if cap == "electrochem.configure_waveform":
            program = args["program"]
            if type(program) is not dict or set(program) != {"mode", "sample_interval_s", "values"}:
                return fail("INVALID_ARGUMENT")
            mode, interval, values = program["mode"], program["sample_interval_s"], program["values"]
            if type(mode) is not str or mode not in ("potential", "current") or not _gamry_number(interval) or interval < GAMRY_REFERENCE620_MIN_INTERVAL_S:
                return fail("INVALID_ARGUMENT")
            limit = 11.0 if mode == "potential" else 0.6
            if type(values) is not list or not 1 <= len(values) <= GAMRY_REFERENCE620_MAX_SAMPLES or any(not _gamry_number(v) or not -limit <= v <= limit for v in values):
                return fail("INVALID_ARGUMENT")
            if not _gamry_number((len(values) - 1) * interval):
                return fail("INVALID_ARGUMENT")
        if cap in ("slot.set_connection", "electrochem.configure_waveform", "electrochem.start") and inst["running"]:
            return fail("BUSY")
        if cap == "electrochem.start":
            if not inst["enabled"] or inst["program"] is None or not all(inst["connections"].values()):
                return fail("PRECONDITION_FAILED")
            program = inst["program"]
            interval_fraction = _gamry_Fraction(str(program["sample_interval_s"]))
            schedule = []
            previous_timestamp = None
            for index in range(len(program["values"])):
                offset_fraction = index * interval_fraction
                timestamp_fraction = host._time_fraction + offset_fraction
                try:
                    timestamp = float(timestamp_fraction)
                    offset = float(offset_fraction)
                except OverflowError:
                    return fail("PRECONDITION_FAILED")
                if (not _gamry_number(timestamp) or not _gamry_number(offset)
                        or (previous_timestamp is not None and timestamp <= previous_timestamp)):
                    return fail("PRECONDITION_FAILED")
                schedule.append((timestamp_fraction, offset))
                previous_timestamp = timestamp
        if cap == "electrochem.read_series":
            return self._envelope(instance_id, request_id, result={"samples": inst["samples"], "synthetic": True,
                "response_model": None, "status": inst["status"]})
        candidate = _gamry_copy.deepcopy(inst)
        if cap == "power.set":
            candidate["enabled"] = args["enabled"]
            if not args["enabled"]:
                if candidate["running"]:
                    candidate["status"] = "stopped"
                candidate["running"] = False
                candidate["potential_v"] = candidate["current_a"] = None
            result = {"enabled": candidate["enabled"]}
        elif cap == "slot.set_connection":
            candidate["connections"][args["port_id"]] = args["connection_id"]
            result = _gamry_copy.deepcopy(args)
        elif cap == "electrochem.configure_waveform":
            candidate["program"] = _gamry_copy.deepcopy(args["program"])
            candidate.update(status="idle", running=False, index=0, elapsed_s=0.0,
                             potential_v=None, current_a=None, samples=[], started_at_s=None,
                             started_at_fraction=None, schedule=[])
            result = {"program": candidate["program"]}
        elif cap == "electrochem.start":
            candidate.update(status="running", running=True, index=0, elapsed_s=0.0,
                             potential_v=None, current_a=None, samples=[], started_at_s=host.time_s,
                             started_at_fraction=host._time_fraction, schedule=schedule)
            result = {"status": "running", "synthetic": True}
        else:  # electrochem.stop
            if candidate["running"]:
                candidate["status"] = "stopped"
            candidate["running"] = False
            candidate["potential_v"] = candidate["current_a"] = None
            result = {"status": candidate["status"], "synthetic": True}
        if host._values(candidate) != host._values(inst):
            candidate["revision"] += 1
        host._instances[instance_id] = candidate
        return self._envelope(instance_id, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        host = self.__host
        with host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slots = host._metadata["asset_types"][GAMRY_REFERENCE620_TYPE]["state_slots"]
            if type(state_id) is not str or state_id not in slots or not self._visible(slots[state_id]) or slots[state_id]["write_via"] is None:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slots[state_id]["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"],
                arguments={**route["fixed_arguments"], route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return _gamry_copy.deepcopy(self.__host._logs[self.__principal])


# BEGIN CHEM-005 METROHM 914 APPEND-ONLY RUNTIME
import copy as _m914_copy
import hashlib as _m914_hashlib
import json as _m914_json
import math as _m914_math
import threading as _m914_threading
from decimal import Decimal as _m914_Decimal, ROUND_HALF_UP as _m914_ROUND_HALF_UP

METROHM914_TYPE = "instrument.metrohm_914"
METROHM914_CHANNELS = {"ph": (-13.0, 20.0, "1"), "conductance": (1e-7, 0.5, "S"), "do": (0.0, 0.05, "kg/m3")}
METROHM914_TEMPLATE_CAPABILITIES = ("template.list", "template.read", "electrochemistry.template_reading")


def _m914_template_module():
    """Lazy import of the public-data template extension (scripts/basic40_templates/metrohm_914.py)."""
    try:
        from .basic40_templates import metrohm_914 as module
    except ImportError:
        from basic40_templates import metrohm_914 as module
    return module


def _m914_number(value):
    try:
        return type(value) in (int, float) and _m914_math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _m914_num(unit, minimum=None, maximum=None, nullable=False):
    schema = {"type": "number", "x-unit": unit}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return {"anyOf": [schema, {"type": "null"}]} if nullable else schema


def _m914_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def export_metrohm914_contract_metadata():
    """Type metadata only: never contains an instance's host-supplied truth."""
    boolean = {"type": "boolean"}
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    channel = {"type": "string", "enum": list(METROHM914_CHANNELS)}
    def route(cap, parameter, fixed=None):
        return {"capability_id": cap, "parameter": parameter, "fixed_arguments": fixed or {}}
    def slot(unit, origin, schema, visibility="agent", write=None):
        return {"unit": unit, "origin": origin, "visibility": visibility,
                "access": "read_write" if write else "read_only", "value_schema": schema,
                "write_via": write, "read_binding": "Metrohm914Session.read(instance_id=..., state_ids=[state_id])",
                "sampling": {"mode": "instant", "time_domain": "simulation", "window": None}, "reference_conditions": None}
    slots = {
        "power.enabled": slot("1", "setpoint", boolean, write=route("power.set", "enabled")),
        "probe.selected": slot("1", "setpoint", channel, write=route("electrochemistry.select_probe", "slot")),
        "temperature.mode": slot("1", "setpoint", {"type": "string", "enum": ["automatic", "manual"]}),
        "temperature.manual_k": slot("K", "setpoint", _m914_num("K", nullable=True)),
        "temperature.compensation_k": slot("K", "derived", _m914_num("K", nullable=True)),
        "endpoint.hold_s": slot("s", "setpoint", _m914_num("s", 0, 3600, nullable=True)),
        "endpoint.tolerance_fraction": slot("1", "setpoint", _m914_num("1", 0, 1, nullable=True)),
        "endpoint.stable": slot("1", "derived", boolean),
        "endpoint.elapsed_s": slot("s", "derived", _m914_num("s", 0)),
        "acquisition.interval_s": slot("s", "setpoint", _m914_num("s", 1, 86400), write=route("acquisition.set_interval", "interval_s")),
        "acquisition.running": slot("1", "derived", boolean),
        "acquisition.record_count": slot("1", "derived", {"type": "integer", "minimum": 0, "x-unit": "1"}),
        "measurement.status": slot("1", "derived", {"type": "string", "enum": ["OFF", "PROBE_MISSING", "NO_SAMPLE", "MISSING_CONFIGURATION", "OUT_OF_RANGE", "UNSTABLE", "STABLE", "STALE"]}),
        "measurement.ph": slot("1", "derived", _m914_num("1", -13, 20, nullable=True)),
        "measurement.potential_v": slot("V", "derived", _m914_num("V", nullable=True)),
        "measurement.conductance_s": slot("S", "derived", _m914_num("S", 1e-7, 0.5, nullable=True)),
        "measurement.do_kg_m3": slot("kg/m3", "derived", _m914_num("kg/m3", 0, 0.05, nullable=True)),
        "measurement.synthetic": slot("1", "derived", {"type": "boolean", "const": True}),
        "measurement.sample_time_s": slot("s", "derived", _m914_num("s", 0, nullable=True)),
        "display.text": slot("1", "derived", {"type": "string"}),
    }
    for name in METROHM914_CHANNELS:
        slots[f"probe.{name}.installed"] = slot("1", "scene", boolean, write=route("probe.set_installed", "installed", {"slot": name}))
        slots[f"probe.{name}.calibrated"] = slot("1", "derived", boolean)
        slots[f"probe.{name}.calibration_time_s"] = slot("s", "derived", _m914_num("s", 0, nullable=True))
        slots[f"calibration.{name}.offset"] = slot(METROHM914_CHANNELS[name][2], "process_truth", _m914_num(METROHM914_CHANNELS[name][2], nullable=True), "debug")
    for sid, unit in (("ph", "1"), ("conductance_s", "S"), ("do_kg_m3", "kg/m3"), ("temperature_k", "K"), ("reference_ph", "1"), ("reference_potential_v", "V"), ("conductance_temp_coefficient_k_inv", "1/K"), ("conductance_reference_temperature_k", "K"), ("standard_ph", "1"), ("standard_conductance_s", "S"), ("standard_do_kg_m3", "kg/m3")):
        slots[f"truth.{sid}"] = slot(unit, "process_truth", _m914_num(unit, nullable=True), "debug")
    slots["truth.standard_slot"] = slot("1", "process_truth", nullable_string, "debug")
    reading_fields = {"slot": channel, "status": slots["measurement.status"]["value_schema"], "stable": boolean,
        "ph": slots["measurement.ph"]["value_schema"], "potential_v": _m914_num("V", nullable=True),
        "conductance_s": slots["measurement.conductance_s"]["value_schema"],
        "do_kg_m3": slots["measurement.do_kg_m3"]["value_schema"], "calibrated": boolean,
        "sample_time_s": _m914_num("s", 0, nullable=True), "synthetic": {"type": "boolean", "const": True}}
    measurement_effects = [sid for sid in slots if sid.startswith(("measurement.", "endpoint.stable", "endpoint.elapsed", "temperature.compensation", "display."))]
    calibration_effects = [sid for sid in slots if ".calibrat" in sid or sid.startswith("calibration.")]
    def cap(inputs, outputs, example, description, effects, observes, preconditions="参数与声明范围匹配。", kind="device_command"):
        return {"availability": "implemented", "visibility": "agent", "kind": kind,
            "description_zh": description, "input_schema": _m914_object(inputs), "output_schema": _m914_object(outputs),
            "example_arguments": [example], "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": description} for sid in dict.fromkeys(effects)],
            "observes": observes, "scene_backend_required": False, "preconditions_zh": preconditions,
            "completion_criterion_zh": "同步提交状态；测量与终点只在宿主提供新样本并推进共同时间后更新。"}
    caps = {
        "power.set": cap({"enabled": boolean}, {"enabled": boolean}, {"enabled": True}, "设置供电；断电停止记录并清除当前读数与终点历史。", ["power.enabled", "acquisition.running"] + measurement_effects, ["power.enabled"]),
        "probe.set_installed": cap({"slot": channel, "installed": boolean}, {"slot": channel, "installed": boolean}, {"slot": "ph", "installed": True}, "断电时更换逻辑探头槽；pH与DO共用digital接口且互斥，电导使用独立接口；更换清除该槽校准。", [f"probe.{n}.installed" for n in METROHM914_CHANNELS] + calibration_effects + measurement_effects, [f"probe.{n}.installed" for n in METROHM914_CHANNELS], "供电关闭；安装pH或DO时另一共用digital槽未安装。", "configuration"),
        "electrochemistry.select_probe": cap({"slot": channel}, {"slot": channel}, {"slot": "ph"}, "选择已安装的单通道读数配置，切换后重新采样与稳定。", ["probe.selected"] + measurement_effects, ["probe.selected"], "所选槽已安装；未在记录。"),
        "electrochemistry.configure_temperature": cap({"mode": {"type": "string", "enum": ["automatic", "manual"]}, "manual_temperature_k": _m914_num("K", nullable=True)}, {"mode": {"type": "string"}, "manual_temperature_k": _m914_num("K", nullable=True)}, {"mode": "automatic", "manual_temperature_k": None}, "自动模式使用宿主样品温度；手动模式必须明确给定正开尔文温度；Nernst及可选电导线性温补均为工程假设。", ["temperature.mode", "temperature.manual_k"] + measurement_effects, ["temperature.mode", "temperature.manual_k"], "未在记录；manual需正有限温度，automatic需null。"),
        "electrochemistry.configure_endpoint": cap({"hold_s": _m914_num("s", 0, 3600), "tolerance_fraction": _m914_num("1", 0, 1)}, {"hold_s": _m914_num("s"), "tolerance_fraction": _m914_num("1")}, {"hold_s": 2, "tolerance_fraction": 0.001}, "显式设定稳定窗口与全量程比例阈值；同窗口最大最小值差不超过阈值时为稳定；上限仅为运行层资源边界。", ["endpoint.hold_s", "endpoint.tolerance_fraction"] + measurement_effects, ["endpoint.hold_s", "endpoint.tolerance_fraction"], "未在记录；hold_s必须大于零。"),
        "acquisition.set_interval": cap({"interval_s": _m914_num("s", 1, 86400)}, {"interval_s": _m914_num("s")}, {"interval_s": 1}, "记录间隔为1s的整数倍；上限86400s是运行层资源边界，非厂家规格。", ["acquisition.interval_s"], ["acquisition.interval_s"], "未在记录；间隔为整数秒。"),
        "electrochemistry.calibrate": cap({"slot": channel, "standard_value": _m914_num("1", -13, 20)}, {"slot": channel, "calibrated": boolean, "synthetic": {"type": "boolean", "const": True}}, {"slot": "ph", "standard_value": 7}, "有界单点合成校准：标准值按slot单位解释（ph=1、conductance=S、do=kg/m3）；建立加性偏置修正，不声称厂家校准算法、精度或溯源。", calibration_effects + measurement_effects, [f"probe.{n}.calibrated" for n in METROHM914_CHANNELS], "供电、选中且安装槽、当前新鲜读数稳定、未记录；宿主明确标定匹配槽与已知标准值；标准值在该槽规格范围内。"),
        "acquisition.start": cap({}, {"running": boolean}, {}, "开始记录后续实际宿主样本；不补造中间记录或推进时间。", ["acquisition.running"], ["acquisition.running"], "供电、已安装选中槽、已配置终点。"),
        "acquisition.stop": cap({}, {"running": boolean}, {}, "停止后保留已记录数据。", ["acquisition.running"], ["acquisition.running"]),
        "electrochemistry.reading": cap({}, reading_fields, {}, "返回所选配置的同版本合成读数；不暴露宿主真值，不推进时间。", [], [sid for sid in slots if sid.startswith("measurement.")] + ["endpoint.stable"], kind="query"),
        "acquisition.records": cap({}, {"records": {"type": "array", "items": _m914_object(reading_fields)}, "synthetic": {"type": "boolean", "const": True}}, {}, "返回该实例已记录的可观察合成结果（最多1024条，满时停止），不含真值或宿主配置。", [], ["acquisition.record_count"], kind="query"),
    }
    # Disjoint schemas preserve each standard's SI unit, even though all are numeric.
    caps["electrochemistry.calibrate"]["input_schema"] = _m914_object({"slot": channel, "standard_value": {"anyOf": [_m914_num(unit, lo, hi) for lo, hi, unit in METROHM914_CHANNELS.values()]}})
    caps["electrochemistry.calibrate"]["input_schema"]["allOf"] = [{"anyOf": [_m914_object({"slot": {"type": "string", "const": n}, "standard_value": _m914_num(unit, lo, hi)}) for n, (lo, hi, unit) in METROHM914_CHANNELS.items()]}]
    caps["electrochemistry.configure_endpoint"]["input_schema"]["properties"]["hold_s"]["exclusiveMinimum"] = 0
    caps["acquisition.set_interval"]["input_schema"]["properties"]["interval_s"]["multipleOf"] = 1
    caps["electrochemistry.configure_temperature"]["input_schema"]["allOf"] = [{"anyOf": [
        _m914_object({"mode": {"type": "string", "const": "automatic"}, "manual_temperature_k": {"type": "null"}}),
        _m914_object({"mode": {"type": "string", "const": "manual"}, "manual_temperature_k": {"type": "number", "exclusiveMinimum": 0, "x-unit": "K"}})]}]
    for name, value in caps.items():
        value["binding"] = f"basic40_runtime_measure.Metrohm914Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"
    def parameter(value, unit, evidence, source):
        return {"value": value, "unit": unit, "evidence": evidence, "source": source}
    params = {
        "ph_range": parameter([-13, 20], "1", "catalog", "CHEM-005 参数与来源 row10"),
        "ph_resolution": parameter(0.001, "1", "catalog", "仅用于显示量化，不是噪声或准确度"),
        "ph_instrument_accuracy": parameter(0.003, "1", "catalog", "仪表±0.003、不含探头误差；未用作仿真精度"),
        "conductance_range": parameter([1e-7, 0.5], "S", "catalog", "0.1µS..500mS，是电导而非电导率"),
        "do_range": parameter([0, 0.05], "kg/m3", "catalog", "0..50mg/L按SI换算"),
        "record_base_interval_s": parameter(1, "s", "catalog", "CHEM-005 数据间隔1s"),
        "nernst_model": parameter("E=E_ref-ln(10)*R*T/F*(pH-pH_ref); pH_display=pH_ref+(E_ref-E)/(ln(10)*R*T_comp/F)", "1", "engineering_assumption", "理想Nernst响应，R=8.31446261815324 J/(mol K)，F=96485.33212331001 C/mol；参考点和温度必须宿主显式提供"),
        "endpoint_model": parameter("max(window)-min(window)<=tolerance_fraction*(channel_max-channel_min)", "1", "engineering_assumption", "窗口hold与容差初始null；以宿主给定样本零阶保持，每个实际样本才更新，不推断厂家终点算法"),
        "calibration_model": parameter("corrected=raw+(standard-raw_at_calibration)", "1", "engineering_assumption", "单点合成偏置，仅当前安装探头；更换使校准失效"),
        "conductance_temperature_model": parameter("G_ref=G/[1+alpha*(T_comp-T_ref)]", "1", "engineering_assumption", "仅当宿主明确给出alpha和T_ref才启用；否则报告原始电导且不声称完成电导温补"),
    }
    for n, unit in (("cell_constant_m_inv", "1/m"), ("contamination_coefficient", "1"), ("drift_rate", "1/s"), ("probe_mass_kg", "kg"), ("do_temperature_coefficient", "1/K"), ("reference_potential_v", "V"), ("reference_ph", "1"), ("endpoint_hold_s", "s"), ("endpoint_tolerance_fraction", "1"), ("conductance_temperature_coefficient", "1/K")):
        params[n] = parameter(None, unit, "unknown", "表未给出；不默认生成数值或用整机参数代替")
    return {"asset_types": {METROHM914_TYPE: {"catalog_id": "CHEM-005", "model": "Metrohm | 914 pH/DO/Conductometer", "asset_version": "1.0.0", "state_slots": slots, "capabilities": caps, "parameters": params}},
        "host_capabilities": {"sim.host_step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "Metrohm914Runtime.host_step(time_s, inputs={instance_id: sample})"}},
        "limitations": ["All measurement outputs are synthetic and estimated; accuracy ±0.003 is an instrument specification, not simulated accuracy.", "pH and DO share one digital interface and cannot both be installed; conductance is an independent analog interface. Runtime selects one channel at a time; parallel acquisition is not implemented.", "No default cell constant, contamination/drift/noise or probe mass is invented. Conductance is S, not S/m; DO is kg/m3.", "Nernst, optional linear conductance compensation, single-point calibration and stability rules are engineering_assumption; DO temperature/pressure/salinity compensation is absent.", "Readings need explicit host samples; automatic temperature needs explicit host temperature. Missing values stay null. No intermediate samples are fabricated.", "Scene adapter receives only display/power/probe installation state; no robot, contact solver or hardware driver. A failing callback may partly modify the scene but leaves a command's process candidate uncommitted.", "Host-supplied principals enforce API visibility only. Python/process/filesystem sandbox isolation is not implemented.", "Request deduplication is in-memory and does not survive restart."]}


class Metrohm914Runtime:
    """Trusted host with absolute shared time; expose sessions, not this object."""
    def __init__(self, *, scene_adapter=None):
        self.scene_adapter, self.time_s = scene_adapter, 0.0
        self._instances, self._principals, self._requests, self._logs = {}, {}, {}, {}
        self._lock = _m914_threading.RLock()
        self._metadata = export_metrohm914_contract_metadata()
        self.register_principal("__public_agent__", visibility="agent")
        self._agent = self.session("__public_agent__")

    def create_instance(self, instance_id, asset_type_id=METROHM914_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != METROHM914_TYPE:
                raise ValueError("Unique instance ID and declared type required")
            inst = {"revision": 0, "power": False, "selected": "ph", "installed": {n: False for n in METROHM914_CHANNELS},
                "calibration": {n: {"calibrated": False, "time": None, "offset": None} for n in METROHM914_CHANNELS},
                "temperature_mode": "automatic", "manual_temperature": None, "compensation_temperature": None,
                "hold": None, "tolerance": None, "stable": False, "elapsed": 0.0, "history": [],
                "interval": 1.0, "running": False, "records": [], "next_record": None,
                "truth": {"ph": None, "conductance_s": None, "do_kg_m3": None, "temperature_k": None,
                    "reference_ph": None, "reference_potential_v": None, "conductance_temp_coefficient_k_inv": None,
                    "conductance_reference_temperature_k": None, "standard_slot": None, "standard_value": None},
                "sample_time": None, "raw": None, "status": "OFF", "readings": {"ph": None, "potential_v": None, "conductance_s": None, "do_kg_m3": None}}
            if self.scene_adapter:
                self.scene_adapter(instance_id, self._scene(inst))
            self._instances[instance_id] = inst

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Invalid or duplicate trusted identity")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(x, str) for x in instance_ids)):
                raise ValueError("Instance scope must be a string collection")
            scope = None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope <= self._instances.keys():
                raise ValueError("Unknown instance scope")
            self._principals[principal_id], self._logs[principal_id] = (visibility, scope), []

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Identity must be registered by trusted host")
        return Metrohm914Session(self, principal_id)

    def describe(self, **kwargs):
        return self._agent.describe(**kwargs)

    def read(self, **kwargs):
        return self._agent.read(**kwargs)

    def invoke(self, **kwargs):
        return self._agent.invoke(**kwargs)

    def write(self, **kwargs):
        return self._agent.write(**kwargs)

    def describe_host(self):
        return _m914_copy.deepcopy(self._metadata["host_capabilities"])

    @staticmethod
    def host_template_sample(solution):
        """Trusted host only: complete explicit truth sample (for host_step) built from public standards/pKa data."""
        return _m914_template_module().build_sample(solution)["sample"]

    def host_step_template(self, time_s, *, solutions, inputs=None):
        """Trusted host only: host_step with template-built samples, then attach their provenance to the instances."""
        return _m914_template_module().host_step_template(self, time_s, solutions, inputs)

    @staticmethod
    def _reset(inst):
        inst["stable"], inst["elapsed"], inst["history"], inst["sample_time"], inst["raw"] = False, 0.0, [], None, None
        inst["readings"] = {"ph": None, "potential_v": None, "conductance_s": None, "do_kg_m3": None}
        inst["compensation_temperature"] = None
        inst["status"] = "OFF" if not inst["power"] else "PROBE_MISSING" if not inst["installed"][inst["selected"]] else "NO_SAMPLE"

    @staticmethod
    def _reading(inst):
        return {"slot": inst["selected"], "status": inst["status"], "stable": inst["stable"], **inst["readings"],
            "calibrated": inst["calibration"][inst["selected"]]["calibrated"], "sample_time_s": inst["sample_time"], "synthetic": True}

    @staticmethod
    def _display(inst):
        r = Metrohm914Runtime._reading(inst)
        key = {"ph": "ph", "conductance": "conductance_s", "do": "do_kg_m3"}[inst["selected"]]
        value = r[key]
        unit = METROHM914_CHANNELS[inst["selected"]][2]
        return f"{inst['selected'].upper()} {'---' if value is None else format(value, '.6g')} {unit} | {inst['status']} | SYNTHETIC"

    @staticmethod
    def _scene(inst):
        return {"power.enabled": inst["power"], "probe.selected": inst["selected"], "display.text": Metrohm914Runtime._display(inst),
            **{f"probe.{n}.installed": v for n, v in inst["installed"].items()}}

    @staticmethod
    def _values(inst):
        v = {"power.enabled": inst["power"], "probe.selected": inst["selected"], "temperature.mode": inst["temperature_mode"],
            "temperature.manual_k": inst["manual_temperature"], "temperature.compensation_k": inst["compensation_temperature"],
            "endpoint.hold_s": inst["hold"], "endpoint.tolerance_fraction": inst["tolerance"], "endpoint.stable": inst["stable"],
            "endpoint.elapsed_s": inst["elapsed"], "acquisition.interval_s": inst["interval"], "acquisition.running": inst["running"],
            "acquisition.record_count": len(inst["records"]), "measurement.status": inst["status"], "measurement.synthetic": True,
            "measurement.sample_time_s": inst["sample_time"], "display.text": Metrohm914Runtime._display(inst)}
        v.update({f"measurement.{k}": value for k, value in inst["readings"].items()})
        for n in METROHM914_CHANNELS:
            v[f"probe.{n}.installed"], v[f"probe.{n}.calibrated"], v[f"probe.{n}.calibration_time_s"] = inst["installed"][n], inst["calibration"][n]["calibrated"], inst["calibration"][n]["time"]
            v[f"calibration.{n}.offset"] = inst["calibration"][n]["offset"]
        v.update({f"truth.{key}": value for key, value in inst["truth"].items() if key != "standard_value"})
        for name, suffix in (("ph", "ph"), ("conductance", "conductance_s"), ("do", "do_kg_m3")):
            v[f"truth.standard_{suffix}"] = inst["truth"]["standard_value"] if inst["truth"]["standard_slot"] == name else None
        return v

    def debug_snapshot(self):
        with self._lock:
            return {"time_s": self.time_s, "instances": {n: {"revision": i["revision"], "state": self._values(i), "internal": _m914_copy.deepcopy(i)} for n, i in self._instances.items()}}

    def _sample(self, inst, time_s, sample):
        # Each mapping is a complete explicit sample. Omitted fields are unknown.
        inst["truth"] = {k: sample.get(k) for k in inst["truth"]}
        self._reset(inst)
        if not inst["power"] or not inst["installed"][inst["selected"]]:
            return
        truth, name = inst["truth"], inst["selected"]
        value = truth[{"ph": "ph", "conductance": "conductance_s", "do": "do_kg_m3"}[name]]
        if value is None:
            return
        inst["sample_time"] = time_s
        comp_t = truth["temperature_k"] if inst["temperature_mode"] == "automatic" else inst["manual_temperature"]
        inst["compensation_temperature"] = comp_t
        raw, potential = value, None
        if name == "ph":
            if any(v is None for v in (comp_t, truth["temperature_k"], truth["reference_ph"], truth["reference_potential_v"])):
                inst["status"] = "MISSING_CONFIGURATION"
                return
            factor = _m914_math.log(10) * 8.31446261815324 / 96485.33212331001
            potential = truth["reference_potential_v"] - factor * truth["temperature_k"] * (value - truth["reference_ph"])
            raw = truth["reference_ph"] + (truth["temperature_k"] / comp_t) * (value - truth["reference_ph"])
        elif name == "conductance":
            alpha, reference_t = truth["conductance_temp_coefficient_k_inv"], truth["conductance_reference_temperature_k"]
            if alpha is not None or reference_t is not None:
                if alpha is None or reference_t is None or comp_t is None:
                    inst["status"] = "MISSING_CONFIGURATION"
                    return
                denominator = 1 + alpha * (comp_t - reference_t)
                if not _m914_number(denominator) or denominator <= 0:
                    raise ValueError("Invalid explicit conductance compensation configuration")
                raw = value / denominator
        if not _m914_number(raw) or (potential is not None and not _m914_number(potential)):
            raise ValueError("Nonfinite synthetic calculation")
        inst["raw"] = raw
        offset = inst["calibration"][name]["offset"]
        measured = raw if offset is None else raw + offset
        lo, hi, _ = METROHM914_CHANNELS[name]
        if not _m914_number(measured) or not lo <= measured <= hi:
            inst["status"] = "OUT_OF_RANGE"
            return
        if name == "ph":
            measured = float(_m914_Decimal(str(measured)).quantize(_m914_Decimal("0.001"), rounding=_m914_ROUND_HALF_UP))
        inst["readings"][{"ph": "ph", "conductance": "conductance_s", "do": "do_kg_m3"}[name]] = measured
        inst["readings"]["potential_v"] = potential
        inst["status"] = "UNSTABLE"

    def host_step(self, time_s, *, inputs=None):
        """One absolute external time, with no hidden integration or backfill.

        Each supplied sample replaces its full truth record; omitted fields become
        null. Omitted instances keep last reading marked stale. Repeated time is a
        pure no-op only with no inputs; new samples require strictly greater time.
        All validation/calculation precedes process commit. Callback failures after
        host commit cannot roll back the shared world's elapsed time.
        """
        samples = {} if inputs is None else inputs
        with self._lock:
            if not _m914_number(time_s) or time_s < self.time_s or not isinstance(samples, dict) or any(not isinstance(n, str) or n not in self._instances or not isinstance(s, dict) for n, s in samples.items()):
                raise ValueError("Finite monotonic common time and declared instance sample mapping required")
            if time_s == self.time_s:
                if samples:
                    raise ValueError("New samples require increasing common time")
                return
            allowed = set(next(iter(self._instances.values()))["truth"]) if self._instances else set()
            for sample in samples.values():
                if set(sample) - allowed:
                    raise ValueError("Unknown host sample field")
                for key, value in sample.items():
                    if key == "standard_slot":
                        if value is not None and (not isinstance(value, str) or value not in METROHM914_CHANNELS):
                            raise ValueError("Unknown standard channel")
                    elif value is not None and not _m914_number(value):
                        raise ValueError("Host sample values must be finite numbers or null")
                    elif key.endswith("temperature_k") and value is not None and value <= 0:
                        raise ValueError("Temperature must be positive kelvin")
                standard_slot, standard = sample.get("standard_slot"), sample.get("standard_value")
                if (standard_slot is None) != (standard is None):
                    raise ValueError("Standard channel and explicit known value are a pair")
                if standard_slot is not None and not METROHM914_CHANNELS[standard_slot][0] <= standard <= METROHM914_CHANNELS[standard_slot][1]:
                    raise ValueError("Synthetic standard is outside declared channel range")
            candidates = _m914_copy.deepcopy(self._instances)
            for n, inst in candidates.items():
                if n in samples:
                    old_history = inst["history"]
                    self._sample(inst, float(time_s), samples[n])
                    if inst["status"] == "UNSTABLE" and inst["hold"] is not None:
                        value = inst["readings"][{"ph": "ph", "conductance": "conductance_s", "do": "do_kg_m3"}[inst["selected"]]]
                        history = old_history + [(float(time_s), value)]
                        cutoff = time_s - inst["hold"]
                        while len(history) > 1 and history[1][0] <= cutoff:
                            history.pop(0)
                        inst["history"] = history
                        span = METROHM914_CHANNELS[inst["selected"]][1] - METROHM914_CHANNELS[inst["selected"]][0]
                        inside = max(v for _, v in history) - min(v for _, v in history) <= inst["tolerance"] * span
                        inst["elapsed"] = min(inst["hold"], time_s - history[0][0]) if inside else 0.0
                        inst["stable"] = bool(inside and time_s - history[0][0] >= inst["hold"])
                        inst["status"] = "STABLE" if inst["stable"] else "UNSTABLE"
                    if inst["running"] and inst["status"] in ("UNSTABLE", "STABLE") and time_s >= inst["next_record"]:
                        inst["records"].append(self._reading(inst))
                        inst["next_record"] = float(time_s) + inst["interval"]
                        if len(inst["records"]) >= 1024:
                            inst["running"] = False
                elif inst["sample_time"] is not None:
                    inst["stable"], inst["elapsed"], inst["history"], inst["status"] = False, 0.0, [], "STALE"
                inst["revision"] += 1
            self._instances, self.time_s = candidates, float(time_s)
            if self.scene_adapter:
                failed = False
                for n, inst in candidates.items():
                    try:
                        self.scene_adapter(n, self._scene(inst))
                    except Exception:
                        failed = True
                if failed:
                    raise RuntimeError("Host process/time committed; scene callback synchronization failed") from None


class Metrohm914Session:
    """Filtered API boundary. Python object privacy is not a security sandbox."""
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
        return {"protocol_version": "1.0.0", "instance_id": instance_id if isinstance(instance_id, str) else None,
            "request_id": request_id if isinstance(request_id, str) else None, "operation_id": None, "status": status,
            "state_revision": inst["revision"] if inst else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": _m914_copy.deepcopy(result or {}), "observations": _m914_copy.deepcopy(observations or {}), "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Instance unavailable to this session.", "UNKNOWN_CAPABILITY": "Capability unavailable.",
            "INVALID_ARGUMENT": "Arguments do not match schema or bounded configuration.", "ACCESS_DENIED": "Request is outside session access policy.",
            "PRECONDITION_FAILED": "Required operating state, calibration fixture or configuration unavailable.",
            "REQUEST_CONFLICT": "Request ID already belongs to different arguments.", "DEVICE_ERROR": "Scene synchronization did not complete."}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": messages[code], "retryable": False,
            "side_effects": "unknown" if code == "DEVICE_ERROR" else "none", "progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None) or (asset_type_id is not None and asset_type_id != METROHM914_TYPE):
                return self._error(None, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            _, scope = self.__host._principals[self.__principal]
            if scope is not None and not scope:
                return self._error(instance_id, "ACCESS_DENIED")
            spec = _m914_copy.deepcopy(self.__host._metadata["asset_types"][METROHM914_TYPE])
            spec["state_slots"] = {n: s for n, s in spec["state_slots"].items() if self._visible(s)}
            spec["capabilities"].update(_m914_template_module().capability_metadata())  # additive public-data extension
            for cap in spec["capabilities"].values():
                cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in spec["state_slots"]]
                cap["observes"] = [n for n in cap["observes"] if n in spec["state_slots"]]
            return {"protocol_version": "1.0.0", "asset_type_id": METROHM914_TYPE, "instance_id": instance_id,
                "adapter": "basic40_runtime_measure.Metrohm914Session", **spec, "limitations": _m914_copy.deepcopy(self.__host._metadata["limitations"])}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = self.__host._metadata["asset_types"][METROHM914_TYPE]["state_slots"]
            if state_ids is None:
                state_ids = [n for n, s in slots.items() if self._visible(s)]
            if not isinstance(state_ids, list) or any(not isinstance(n, str) for n in state_ids) or len(state_ids) != len(set(state_ids)):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(n not in slots or not self._visible(slots[n]) for n in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            values = self.__host._values(inst)
            observations = {}
            for n in state_ids:
                derived = slots[n]["origin"] == "derived"
                stale = inst["status"] == "STALE" and n.startswith(("measurement.", "temperature.compensation")) and n not in ("measurement.status", "measurement.synthetic")
                observations[n] = {"value": values[n], "unit": slots[n]["unit"], "quality": "unavailable" if values[n] is None else "stale" if stale else "estimated" if derived else "valid",
                    "sample_time_s": inst["sample_time"] if n.startswith(("measurement.", "truth.", "temperature.compensation")) else self.__host.time_s,
                    "sample_window_s": None, "reason": "No explicit sample/configuration or this output is not selected." if values[n] is None else "No new host sample at current time." if stale else None,
                    "synthetic": derived}
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id or len(request_id) > 256:
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = _m914_hashlib.sha256(_m914_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            except (ValueError, TypeError, OverflowError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = self.__principal, instance_id, request_id
            if key in host._requests:
                previous, response = host._requests[key]
                return _m914_copy.deepcopy(response) if previous == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = digest, _m914_copy.deepcopy(response)
            host._logs[self.__principal].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id, "response": _m914_copy.deepcopy(response)})
            return response

    def _checked(self, instance_id, inst, cap, args, request_id):
        host = self.__host
        if cap in METROHM914_TEMPLATE_CAPABILITIES:  # additive public-data extension; legacy capabilities unchanged
            return _m914_template_module().invoke(self, host, instance_id, inst, cap, args, request_id,
                                                  evaluator=self._visible({"visibility": "debug"}))
        fail = lambda code, status="rejected": self._error(instance_id, code, request_id, status)
        definitions = host._metadata["asset_types"][METROHM914_TYPE]["capabilities"]
        if cap not in definitions:
            return fail("UNKNOWN_CAPABILITY")
        expected = {"power.set": {"enabled"}, "probe.set_installed": {"slot", "installed"}, "electrochemistry.select_probe": {"slot"},
            "electrochemistry.configure_temperature": {"mode", "manual_temperature_k"}, "electrochemistry.configure_endpoint": {"hold_s", "tolerance_fraction"},
            "acquisition.set_interval": {"interval_s"}, "electrochemistry.calibrate": {"slot", "standard_value"}}.get(cap, set())
        if set(args) != expected:
            return fail("INVALID_ARGUMENT")
        if "slot" in args and (not isinstance(args["slot"], str) or args["slot"] not in METROHM914_CHANNELS):
            return fail("INVALID_ARGUMENT")
        if any(type(args[n]) is not bool for n in ("enabled", "installed") if n in args):
            return fail("INVALID_ARGUMENT")
        if cap == "electrochemistry.configure_temperature":
            t = args["manual_temperature_k"]
            if args["mode"] not in ("automatic", "manual") or (args["mode"] == "automatic" and t is not None) or (args["mode"] == "manual" and (not _m914_number(t) or t <= 0)):
                return fail("INVALID_ARGUMENT")
        if cap == "electrochemistry.configure_endpoint" and (not _m914_number(args["hold_s"]) or not 0 < args["hold_s"] <= 3600 or not _m914_number(args["tolerance_fraction"]) or not 0 <= args["tolerance_fraction"] <= 1):
            return fail("INVALID_ARGUMENT")
        if cap == "acquisition.set_interval" and (not _m914_number(args["interval_s"]) or not 1 <= args["interval_s"] <= 86400 or args["interval_s"] != int(args["interval_s"])):
            return fail("INVALID_ARGUMENT")
        if cap == "electrochemistry.calibrate":
            lo, hi, _ = METROHM914_CHANNELS[args["slot"]]
            if not _m914_number(args["standard_value"]) or not lo <= args["standard_value"] <= hi:
                return fail("INVALID_ARGUMENT")
        if cap == "probe.set_installed":
            other = {"ph": "do", "do": "ph"}.get(args["slot"])
            if inst["power"] or (args["installed"] and other and inst["installed"][other]):
                return fail("PRECONDITION_FAILED")
        if cap == "electrochemistry.select_probe" and (not inst["installed"][args["slot"]] or inst["running"]):
            return fail("PRECONDITION_FAILED")
        if cap in ("electrochemistry.configure_temperature", "electrochemistry.configure_endpoint", "acquisition.set_interval") and inst["running"]:
            return fail("PRECONDITION_FAILED")
        if cap == "electrochemistry.calibrate" and (inst["running"] or not inst["power"] or not inst["stable"] or inst["sample_time"] != host.time_s or inst["selected"] != args["slot"] or not inst["installed"][args["slot"]] or inst["truth"]["standard_slot"] != args["slot"] or inst["truth"]["standard_value"] != args["standard_value"]):
            return fail("PRECONDITION_FAILED")
        if cap == "acquisition.start" and (not inst["power"] or not inst["installed"][inst["selected"]] or inst["hold"] is None or len(inst["records"]) >= 1024):
            return fail("PRECONDITION_FAILED")
        if cap == "electrochemistry.reading":
            return self._envelope(instance_id, request_id, result=host._reading(inst))
        if cap == "acquisition.records":
            return self._envelope(instance_id, request_id, result={"records": inst["records"], "synthetic": True})
        candidate = _m914_copy.deepcopy(inst)
        if cap == "power.set":
            if candidate["power"] != args["enabled"]:
                candidate["power"] = args["enabled"]
                if not args["enabled"]:
                    candidate["running"] = False
                host._reset(candidate)
            result = {"enabled": candidate["power"]}
        elif cap == "probe.set_installed":
            n = args["slot"]
            if candidate["installed"][n] != args["installed"]:
                candidate["installed"][n] = args["installed"]
                candidate["calibration"][n] = {"calibrated": False, "time": None, "offset": None}
                host._reset(candidate)
            result = {"slot": n, "installed": args["installed"]}
        elif cap == "electrochemistry.select_probe":
            if candidate["selected"] != args["slot"]:
                candidate["selected"] = args["slot"]
                host._reset(candidate)
            result = {"slot": args["slot"]}
        elif cap == "electrochemistry.configure_temperature":
            candidate["temperature_mode"], candidate["manual_temperature"] = args["mode"], args["manual_temperature_k"]
            host._reset(candidate)
            result = dict(args)
        elif cap == "electrochemistry.configure_endpoint":
            candidate["hold"], candidate["tolerance"] = float(args["hold_s"]), float(args["tolerance_fraction"])
            host._reset(candidate)
            result = dict(args)
        elif cap == "acquisition.set_interval":
            candidate["interval"] = float(args["interval_s"])
            result = dict(args)
        elif cap == "electrochemistry.calibrate":
            candidate["calibration"][args["slot"]] = {"calibrated": True, "time": host.time_s, "offset": float(args["standard_value"]) - candidate["raw"]}
            host._reset(candidate)
            result = {"slot": args["slot"], "calibrated": True, "synthetic": True}
        else:
            candidate["running"] = cap == "acquisition.start"
            if candidate["running"] and not inst["running"]:
                candidate["next_record"] = host.time_s + candidate["interval"]
            result = {"running": candidate["running"]}
        if host.scene_adapter and host._scene(candidate) != host._scene(inst):
            try:
                host.scene_adapter(instance_id, host._scene(candidate))
            except Exception:
                return fail("DEVICE_ERROR", "failed")
        candidate["revision"] += 1
        host._instances[instance_id] = candidate
        return self._envelope(instance_id, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slots = self.__host._metadata["asset_types"][METROHM914_TYPE]["state_slots"]
            if not isinstance(state_id, str) or state_id not in slots or not self._visible(slots[state_id]) or slots[state_id]["write_via"] is None:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slots[state_id]["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={**route["fixed_arguments"], route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return _m914_copy.deepcopy(self.__host._logs[self.__principal])


def write_metrohm914_runtime_reports(output_directory):
    import datetime
    import importlib.util
    import io
    import pathlib
    import platform
    import unittest
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("test_basic40_measure_metrohm914", root / "tests/test_basic40_measure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.Metrohm914RuntimeTests)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    metadata = export_metrohm914_contract_metadata()
    report = {"catalog_id": "CHEM-005", "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "method": "unittest explicit synthetic host samples; all capability outputs/state effects; common external timestamp; three slot calibration; Nernst and SI boundaries; rejection atomicity; identities, deduplication and scene callback failure",
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "result": "passed" if result.wasSuccessful() else "failed",
        "capabilities_verified": sorted(metadata["asset_types"][METROHM914_TYPE]["capabilities"]) if result.wasSuccessful() else [],
        "output": stream.getvalue(), "not_run": ["real_hardware", "robot_execution", "contact_solver", "process_filesystem_security_isolation", "parallel_channel_acquisition", "manufacturer_calibration_accuracy", "do_temperature_pressure_salinity_compensation"],
        "scene_scope": "Only a recording/failing callback is tested here; evaluated Blender geometry requires the separate scene verification record.",
        "synthetic": True}
    directory = pathlib.Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (("runtime_validation.json", report), ("runtime_metadata.json", metadata)):
        (directory / name).write_text(_m914_json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
# END CHEM-005 METROHM 914 APPEND-ONLY RUNTIME

# BEGIN CHEM-102 METROHM 899 APPEND-ONLY RUNTIME
# All _kf899 names and classes in this block are independent of earlier assets.
import copy as _kf899_copy
import json as _kf899_json
import math as _kf899_math
import threading as _kf899_threading

METROHM899_TYPE = "instrument.metrohm_899"
KF899_FARADAY_C_MOL = 96485.33212331001
KF899_WATER_MOLAR_MASS_KG_MOL = 0.01801528
KF899_KG_PER_COULOMB = KF899_WATER_MOLAR_MASS_KG_MOL / (2 * KF899_FARADAY_C_MOL)
KF899_START_DRIFT_MIN_KG_S = 1e-9 / 60
KF899_START_DRIFT_MAX_KG_S = 999e-9 / 60
METROHM899_TEMPLATE_CAPABILITIES = ("template.list", "template.read", "coulometry.template_report")


def _kf899_template_module():
    """Lazy import of the public-data template extension (scripts/basic40_templates/metrohm_899.py)."""
    try:
        from .basic40_templates import metrohm_899 as module
    except ImportError:
        from basic40_templates import metrohm_899 as module
    return module


def _kf899_number(value):
    try:
        return type(value) in (float, int) and _kf899_math.isfinite(value)
    except (ValueError, TypeError, OverflowError):
        return False


def _kf899_num(unit, lo=None, hi=None, nullable=False):
    result = {"type": "number", "x-unit": unit}
    if lo is not None:
        result["minimum"] = lo
    if hi is not None:
        result["maximum"] = hi
    return {"anyOf": [result, {"type": "null"}]} if nullable else result


def _kf899_object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


def export_metrohm899_contract_metadata():
    """Type-only authoring metadata. Contains no live instance/sample truth.

    This is a synthetic SI model, not Metrohm firmware. F and molecular mass
    support an ideal two-electron/water stoichiometric engineering assumption;
    precision, noise, current efficiency and electrode transfer functions are
    unknown. Host identities and truth are protected at API boundaries only,
    not from Python introspection or shared filesystem access.
    """
    boolean = {"type": "boolean"}
    enum_slot = {"type": "string", "enum": ["cell", "generator", "indicator"]}
    current = {"type": "number", "enum": [0.1, 0.2, 0.4], "x-unit": "A"}
    config = {
        "current_a": current,
        "drift_compensation_kg_s": _kf899_num("kg/s", 0, nullable=True),
        "start_drift_threshold_kg_s": _kf899_num("kg/s", KF899_START_DRIFT_MIN_KG_S, KF899_START_DRIFT_MAX_KG_S),
        "extraction_time_s": _kf899_num("s", 0, 999999),
        "stirring_level": {"type": "integer", "minimum": -15, "maximum": 15, "x-unit": "1"},
        "sample_mass_kg": _kf899_num("kg", 0, nullable=True),
        "endpoint_hold_s": _kf899_num("s", 0, nullable=True),
    }
    routes = {name: {"capability_id": "coulometry.configure", "parameter": name, "fixed_arguments": {}} for name in config}
    def slot(unit, origin, value_schema, visibility="agent", route=None, integral=False):
        return {"unit": unit, "origin": origin, "value_schema": value_schema, "visibility": visibility,
                "access": "read_write" if route else "read_only", "write_via": route,
                "read_binding": "Metrohm899Session.read(instance_id=..., state_ids=[state_id])",
                "reference_conditions": None,
                "sampling": {"mode": "integral" if integral else "instant", "time_domain": "simulation",
                             "window": "自本次 coulometry.start 至当前或停止时刻" if integral else None}}
    state = {
        "power.enabled": slot("1", "setpoint", boolean, route={"capability_id": "power.set", "parameter": "enabled", "fixed_arguments": {}}),
        "coulometry.running": slot("1", "derived", boolean),
        "coulometry.status": slot("1", "derived", {"type": "string", "enum": ["OFF", "IDLE", "READY", "EXTRACTING", "TITRATING", "HOLDING", "COMPLETED", "STOPPED"]}),
        "sample.present": slot("1", "scene", boolean),
        "reagent.ready": slot("1", "scene", boolean),
        "measurement.elapsed_s": slot("s", "derived", _kf899_num("s", 0)),
        "measurement.charge_c": slot("C", "derived", _kf899_num("C", 0), integral=True),
        "measurement.water_mass_kg": slot("kg", "derived", _kf899_num("kg", 0, nullable=True), integral=True),
        "measurement.water_fraction": slot("1", "derived", _kf899_num("1", 0, nullable=True)),
        "measurement.drift_kg_s": slot("kg/s", "derived", _kf899_num("kg/s", 0, nullable=True)),
        "measurement.synthetic": slot("1", "derived", {"type": "boolean", "const": True}),
        "measurement.sample_time_s": slot("s", "derived", _kf899_num("s", 0, nullable=True)),
        "endpoint.hold_elapsed_s": slot("s", "derived", _kf899_num("s", 0)),
        "truth.water_remaining_kg": slot("kg", "process_truth", _kf899_num("kg", 0, nullable=True), "debug"),
        "truth.sample_water_kg": slot("kg", "process_truth", _kf899_num("kg", 0, nullable=True), "debug"),
        "truth.background_drift_kg_s": slot("kg/s", "process_truth", _kf899_num("kg/s", 0, nullable=True), "debug"),
    }
    for name, spec in config.items():
        state["settings." + name] = slot({"current_a": "A", "drift_compensation_kg_s": "kg/s", "start_drift_threshold_kg_s": "kg/s", "extraction_time_s": "s", "stirring_level": "1", "sample_mass_kg": "kg", "endpoint_hold_s": "s"}[name], "setpoint", spec, route=routes[name])
    for name in ("cell", "generator", "indicator"):
        state[f"slot.{name}.installed"] = slot("1", "scene", boolean, route={"capability_id": "slot.set_installed", "parameter": "installed", "fixed_arguments": {"slot": name}})
    reading = {"status": state["coulometry.status"]["value_schema"], "running": boolean,
               "elapsed_s": _kf899_num("s", 0), "charge_c": _kf899_num("C", 0),
               "water_mass_kg": _kf899_num("kg", 0, nullable=True), "water_fraction": _kf899_num("1", 0, nullable=True),
               "drift_kg_s": _kf899_num("kg/s", 0, nullable=True), "synthetic": {"type": "boolean", "const": True}}
    result_states = ["coulometry.running", "coulometry.status", "measurement.elapsed_s", "measurement.charge_c",
                     "measurement.water_mass_kg", "measurement.water_fraction", "measurement.sample_time_s", "endpoint.hold_elapsed_s"]
    defs = {
        "power.set": ({"enabled": boolean}, {"enabled": boolean}, {"enabled": True}, ["power.enabled", "coulometry.running", "coulometry.status"], ["power.enabled"], "设置供电；断电停止运行并保留已发生的积分结果。", "device_command"),
        "slot.set_installed": ({"slot": enum_slot, "installed": boolean}, {"slot": enum_slot, "installed": boolean}, {"slot": "cell", "installed": True}, [f"slot.{x}.installed" for x in ("cell", "generator", "indicator")], [f"slot.{x}.installed" for x in ("cell", "generator", "indicator")], "在关机闲置时声明滴定池或电极的场景装配槽位；不是机器人装卸。", "configuration"),
        "coulometry.configure": (config, config, {"current_a": 0.2, "drift_compensation_kg_s": 0, "endpoint_hold_s": 1}, ["settings." + x for x in config], ["settings." + x for x in config], "配置手动发生电流、漂移补偿、启动漂移、萃取时间、搅拌档位、样品质量及假设终点保持时间；至少一个字段。", "configuration"),
        "coulometry.start": ({}, reading, {}, result_states + ["truth.water_remaining_kg"], list(s for s in state if s.startswith("measurement.") or s.startswith("coulometry.")), "由已装配、具备试剂且漂移满足阈值的状态启动合成库仑过程；重置当前测量积分。", "device_command"),
        "coulometry.stop": ({}, reading, {}, ["coulometry.running", "coulometry.status"], list(s for s in state if s.startswith("measurement.") or s.startswith("coulometry.")), "停止并保留部分电荷、漂移补偿结果与曲线。", "device_command"),
        "coulometry.reading": ({}, reading, {}, [], list(s for s in state if s.startswith("measurement.") or s.startswith("coulometry.")), "返回合成可观测结果；不推进宿主时间。", "query"),
        "coulometry.curve": ({}, {"records": {"type": "array", "items": _kf899_object({**reading, "sample_time_s": _kf899_num("s", 0)})}, "synthetic": {"type": "boolean", "const": True}}, {}, [], list(s for s in state if s.startswith("measurement.") or s.startswith("coulometry.")), "返回实际宿主步进时刻采样的合成滴定曲线，不插值补造采样。", "query"),
    }
    capabilities = {}
    for name, (args, result, example, effects, observes, meaning, kind) in defs.items():
        capabilities[name] = {"input_schema": _kf899_object(args, [] if name == "coulometry.configure" else None),
            "output_schema": _kf899_object(result), "example_arguments": [example], "meaning_zh": meaning,
            "kind": kind, "visibility": "agent", "availability": "implemented",
            "state_effects": [{"state_id": sid, "meaning_zh": "由此调用更新；停止保留既有累计量。", "timing": "immediate"} for sid in effects],
            "observes": observes,
            "binding": {"backend": "python", "artifact_id": "runtime", "invocation": f"Metrohm899Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"}}
    return {"asset_type_id": METROHM899_TYPE, "catalog_id": "CHEM-102", "asset_version": "1.0.0", "state_slots": state,
        "capabilities": capabilities, "scene_callback_keys": ["power.enabled", "coulometry.running", "display.text"] + [f"slot.{x}.installed" for x in ("cell", "generator", "indicator")],
        "host_capabilities": {
            "sim.load_sample": {"invocation": "Metrohm899Host.load_sample(instance_id=..., water_mass_kg=..., background_drift_kg_s=..., reagent_ready=True, request_id=...)", "visibility": "trusted_host_only", "meaning_zh": "初始化合成样品场景；不是物质转移或真实仪器进样。"},
            "sim.step": {"invocation": "Metrohm899Host.step(dt_s)", "visibility": "trusted_host_only", "meaning_zh": "校验全部实例后，以公共世界时钟计算电荷、耗水、漂移和理想终点。"}},
        "parameters": {
            "current_choices_a": {"value": [0.1, 0.2, 0.4], "unit": "A", "evidence": "public_document", "source": "CHEM-102 参数与来源第107行；自动电流策略未实现"},
            "initial_current_a": {"value": 0.1, "unit": "A", "evidence": "engineering_assumption", "source": "合成初始设定，不宣称厂家默认"},
            "initial_extraction_time_s": {"value": 0.0, "unit": "s", "evidence": "engineering_assumption", "source": "合成初始设定；合法范围 0–999999 s"},
            "start_drift_default_kg_s": {"value": 20e-9 / 60, "unit": "kg/s", "evidence": "public_document", "source": "CHEM-102 参数与来源第107行；20 µg/min"},
            "faraday_c_mol": {"value": KF899_FARADAY_C_MOL, "unit": "C/mol", "evidence": "engineering_assumption", "source": "理想两电子库仑换算参数；不是厂家整机或部件测量"},
            "water_molar_mass_kg_mol": {"value": KF899_WATER_MOLAR_MASS_KG_MOL, "unit": "kg/mol", "evidence": "engineering_assumption", "source": "约定水摩尔质量用于合成化学计量"},
            "electrode_response_s": {"value": None, "unit": "s", "evidence": "unknown", "source": "缺实测响应"},
            "current_efficiency": {"value": None, "unit": "1", "evidence": "unknown", "source": "未知真实电流效率；模型明确使用理想100%假设"},
            "endpoint_hold_s": {"value": None, "unit": "s", "evidence": "unknown", "source": "由仿真任务显式配置，不套用厂家终点默认"},
            "cell_capacity_m3": {"value": None, "unit": "m3", "evidence": "unknown", "source": "不得从照片推断容量"}},
        "equations": ["Q = integral(I_effective dt)", "m_generated = Q*M_H2O/(2*F)", "m_indicated=max(0,m_generated-drift_compensation_kg_s*elapsed_s)", "water_fraction=m_indicated/sample_mass_kg (null when sample_mass_kg unknown)"],
        "boundaries": ["All results synthetic; ideal stoichiometry, 100% modeled current efficiency and ideal depletion endpoint are engineering_assumption.", "Manual 100/200/400 mA only; automatic current firmware, reagent chemistry, electrode potential endpoint, noise, accuracy, hardware and robots not implemented.", "Stirring level is a setting only; no hydrodynamics or invented speed map. Cell capacity and component parameters stay null.", "API truth filtering is not a Python/filesystem sandbox. Keep trusted host and sample fixtures in a separate process for untrusted agents.", "Optional scene callback must be synchronous and atomic; callback tests do not prove evaluated Blender pose or contact physics."]}


class Metrohm899Host:
    """Trusted synthetic world. Only this object supplies truth or advances time."""
    def __init__(self, scene_adapter=None):
        self.scene_adapter = scene_adapter
        self.time_s = 0.0
        self._kf899_instances = {}
        self._kf899_principals = {}
        self._kf899_requests = {}
        self._kf899_host_requests = {}
        self._kf899_logs = []
        self._kf899_lock = _kf899_threading.RLock()

    def create_instance(self, instance_id):
        with self._kf899_lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._kf899_instances:
                raise ValueError("A unique nonempty instance_id is required")
            state = {"power.enabled": False, "coulometry.running": False, "coulometry.status": "OFF",
                "sample.present": False, "reagent.ready": False, "measurement.elapsed_s": 0.0,
                "measurement.charge_c": 0.0, "measurement.water_mass_kg": None, "measurement.water_fraction": None,
                "measurement.drift_kg_s": None, "measurement.synthetic": True, "measurement.sample_time_s": None,
                "endpoint.hold_elapsed_s": 0.0, "truth.water_remaining_kg": None, "truth.sample_water_kg": None,
                "truth.background_drift_kg_s": None, "settings.current_a": 0.1,
                "settings.drift_compensation_kg_s": None, "settings.start_drift_threshold_kg_s": 20e-9 / 60,
                "settings.extraction_time_s": 0.0, "settings.stirring_level": 8, "settings.sample_mass_kg": None,
                "settings.endpoint_hold_s": None,
                **{f"slot.{x}.installed": False for x in ("cell", "generator", "indicator")}}
            self._kf899_instances[instance_id] = {"state": state, "revision": 0, "curve": []}
            return instance_id

    def register_principal(self, principal_id, *, visibility="agent", instance_ids=None):
        with self._kf899_lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._kf899_principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Unique principal and host-assigned visibility required")
            ids = set(self._kf899_instances if instance_ids is None else instance_ids)
            if not ids <= set(self._kf899_instances):
                raise ValueError("Unknown instance in policy")
            self._kf899_principals[principal_id] = {"visibility": visibility, "instances": ids}

    def session(self, principal_id):
        if principal_id not in self._kf899_principals:
            raise ValueError("Unregistered principal")
        return Metrohm899Session(self, principal_id)

    def debug_snapshot(self):
        with self._kf899_lock:
            return _kf899_copy.deepcopy({"time_s": self.time_s, "instances": self._kf899_instances})

    def _kf899_sync(self, instance_id, state):
        if self.scene_adapter is not None:
            updates = {"power.enabled": state["power.enabled"], "coulometry.running": state["coulometry.running"],
                       "display.text": state["coulometry.status"], **{f"slot.{x}.installed": state[f"slot.{x}.installed"] for x in ("cell", "generator", "indicator")}}
            self.scene_adapter(instance_id, _kf899_copy.deepcopy(updates))

    def load_sample(self, *, instance_id, water_mass_kg, background_drift_kg_s, reagent_ready, request_id):
        """Host-only fixture initialization, never a robot or physical transfer.

        A loaded sample may be started once. Loading another explicit fixture is
        needed after any run, so restart cannot regenerate consumed water.
        """
        with self._kf899_lock:
            if not isinstance(instance_id, str) or instance_id not in self._kf899_instances:
                raise ValueError("Unknown instance")
            if not isinstance(request_id, str) or not request_id or not _kf899_number(water_mass_kg) or water_mass_kg < 0 or not _kf899_number(background_drift_kg_s) or background_drift_kg_s < 0 or type(reagent_ready) is not bool:
                raise ValueError("Finite nonnegative SI truth, boolean reagent state and request_id required")
            key = (instance_id, request_id)
            fingerprint = (water_mass_kg, background_drift_kg_s, reagent_ready)
            if key in self._kf899_host_requests:
                if self._kf899_host_requests[key] != fingerprint:
                    raise ValueError("REQUEST_CONFLICT")
                return {"status": "succeeded", "loaded": True, "synthetic": True}
            instance = self._kf899_instances[instance_id]
            if instance["state"]["coulometry.running"]:
                raise ValueError("Cannot replace a running sample")
            candidate = _kf899_copy.deepcopy(instance)
            candidate.pop("template", None)  # a new explicit fixture replaces any template-backed sample record
            s = candidate["state"]
            s.update({"sample.present": True, "reagent.ready": reagent_ready,
                      "truth.sample_water_kg": float(water_mass_kg), "truth.water_remaining_kg": float(water_mass_kg),
                      "truth.background_drift_kg_s": float(background_drift_kg_s), "measurement.drift_kg_s": float(background_drift_kg_s),
                      "measurement.water_mass_kg": None, "measurement.water_fraction": None,
                      "measurement.charge_c": 0.0, "measurement.elapsed_s": 0.0, "measurement.sample_time_s": None,
                      "endpoint.hold_elapsed_s": 0.0, "coulometry.status": "READY" if s["power.enabled"] else "OFF"})
            candidate["curve"] = []
            candidate["revision"] += 1
            self._kf899_sync(instance_id, s)
            self._kf899_instances[instance_id] = candidate
            self._kf899_host_requests[key] = fingerprint
            return {"status": "succeeded", "loaded": True, "synthetic": True}

    def load_template_sample(self, *, instance_id, substance_id, sample_mass_kg, background_drift_kg_s, reagent_ready, request_id, temperature_k=None):
        """Host-only: weigh a public water-library material (hydrate, KF standard, water-saturated solvent) into the cell."""
        return _kf899_template_module().load_template_sample(self, instance_id=instance_id, substance_id=substance_id,
            sample_mass_kg=sample_mass_kg, background_drift_kg_s=background_drift_kg_s, reagent_ready=reagent_ready,
            request_id=request_id, temperature_k=temperature_k)

    def step(self, dt_s):
        """Integrate all devices against one clock; reads never call this method.

        Exhaustion and endpoint hold are split analytically inside each step.
        After water exhaustion, an ideal duty cycle supplies the drift current.
        No electrode kinetics or manufacturer endpoint algorithm is asserted.
        Failed callback leaves process/time unchanged; external scene side effects
        remain unknown unless the callback actually satisfies its atomic contract.
        """
        with self._kf899_lock:
            if not _kf899_number(dt_s) or dt_s < 0 or not _kf899_number(self.time_s + dt_s):
                raise ValueError("Finite nonnegative time increment required")
            if dt_s == 0:
                return {"time_s": self.time_s, "synthetic": True}
            candidate = _kf899_copy.deepcopy(self._kf899_instances)
            for iid, instance in candidate.items():
                s = instance["state"]
                if not s["coulometry.running"]:
                    continue
                remaining_dt = float(dt_s)
                advanced = 0.0
                drift = s["truth.background_drift_kg_s"]
                rate = s["settings.current_a"] * KF899_KG_PER_COULOMB
                extract_remaining = max(0.0, s["settings.extraction_time_s"] - s["measurement.elapsed_s"])
                extract = min(remaining_dt, extract_remaining)
                s["truth.water_remaining_kg"] += drift * extract
                remaining_dt -= extract
                advanced += extract
                s["coulometry.status"] = "EXTRACTING" if remaining_dt == 0 and extract_remaining > extract else "TITRATING"
                if remaining_dt > 0:
                    water = s["truth.water_remaining_kg"]
                    net_rate = rate - drift
                    depletion_time = water / net_rate if net_rate > 0 else float("inf")
                    drive = min(remaining_dt, depletion_time)
                    s["measurement.charge_c"] += s["settings.current_a"] * drive
                    s["truth.water_remaining_kg"] = max(0.0, water - net_rate * drive)
                    remaining_dt -= drive
                    advanced += drive
                    if depletion_time <= drive:
                        s["truth.water_remaining_kg"] = 0.0
                        s["coulometry.status"] = "HOLDING"
                        hold_remaining = max(0.0, s["settings.endpoint_hold_s"] - s["endpoint.hold_elapsed_s"])
                        hold = min(remaining_dt, hold_remaining)
                        s["measurement.charge_c"] += drift * hold / KF899_KG_PER_COULOMB
                        s["endpoint.hold_elapsed_s"] += hold
                        advanced += hold
                        if s["endpoint.hold_elapsed_s"] >= s["settings.endpoint_hold_s"]:
                            s["coulometry.running"] = False
                            s["coulometry.status"] = "COMPLETED"
                s["measurement.elapsed_s"] += advanced
                generated = s["measurement.charge_c"] * KF899_KG_PER_COULOMB
                compensation = s["settings.drift_compensation_kg_s"] * s["measurement.elapsed_s"]
                s["measurement.water_mass_kg"] = max(0.0, generated - compensation)
                mass = s["settings.sample_mass_kg"]
                s["measurement.water_fraction"] = None if mass is None else s["measurement.water_mass_kg"] / mass
                s["measurement.sample_time_s"] = self.time_s + advanced
                if any(type(value) in (int, float) and not _kf899_number(value) for value in s.values()):
                    raise ValueError("Integration would exceed finite numerical range")
                instance["revision"] += 1
                record = _kf899_reading(s)
                record["sample_time_s"] = s["measurement.sample_time_s"]
                instance["curve"].append(record)
            for iid, instance in candidate.items():
                if instance != self._kf899_instances[iid]:
                    self._kf899_sync(iid, instance["state"])
            self._kf899_instances = candidate
            self.time_s += float(dt_s)
            return {"time_s": self.time_s, "synthetic": True}


def _kf899_reading(state):
    return {"status": state["coulometry.status"], "running": state["coulometry.running"],
            "elapsed_s": state["measurement.elapsed_s"], "charge_c": state["measurement.charge_c"],
            "water_mass_kg": state["measurement.water_mass_kg"], "water_fraction": state["measurement.water_fraction"],
            "drift_kg_s": state["measurement.drift_kg_s"], "synthetic": True}


class Metrohm899Session:
    """API observation boundary for a host-registered principal; no hardware."""
    def __init__(self, host, principal_id):
        if principal_id not in host._kf899_principals:
            raise ValueError("Host-assigned principal required")
        self._kf899_host = host
        self._kf899_principal = principal_id

    def _kf899_allowed(self, instance_id):
        return isinstance(instance_id, str) and instance_id in self._kf899_host._kf899_principals[self._kf899_principal]["instances"]

    def _kf899_visible(self, state_id):
        return not state_id.startswith("truth.") or self._kf899_host._kf899_principals[self._kf899_principal]["visibility"] != "agent"

    def _kf899_response(self, instance_id, request_id=None, status="succeeded", result=None, observations=None, code=None, side_effects="none"):
        host = self._kf899_host
        instance = host._kf899_instances.get(instance_id) if isinstance(instance_id, str) and self._kf899_allowed(instance_id) else None
        return {"protocol_version": "1.0.0", "instance_id": instance_id if isinstance(instance_id, str) else None,
                "request_id": request_id if isinstance(request_id, str) else None, "operation_id": None,
                "status": status, "state_revision": instance["revision"] if instance else None,
                "clock": {"domain": "simulation", "time_s": host.time_s}, "result": result or {},
                "observations": observations or {}, "error": None if code is None else {"code": code,
                    "message": {"UNKNOWN_INSTANCE": "Instance unavailable to this session", "UNKNOWN_CAPABILITY": "Unknown capability", "INVALID_ARGUMENT": "Arguments do not satisfy capability requirements", "PRECONDITION_FAILED": "Required powered, idle, assembled, reagent and method conditions are not met", "ACCESS_DENIED": "Access denied", "BUSY": "Measurement is already running", "REQUEST_CONFLICT": "Request identifier was already used with other arguments", "UNSUPPORTED": "No hardware or automatic-current adapter is implemented", "DEVICE_ERROR": "Scene callback failed; external scene effects are unknown"}.get(code, code),
                    "retryable": False, "side_effects": side_effects}}

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self._kf899_host._kf899_lock:
            if (asset_type_id is None) == (instance_id is None):
                return self._kf899_response(instance_id, status="rejected", code="INVALID_ARGUMENT")
            if instance_id is not None and not self._kf899_allowed(instance_id):
                return self._kf899_response(instance_id, status="rejected", code="UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != METROHM899_TYPE:
                return self._kf899_response(instance_id, status="rejected", code="UNKNOWN_INSTANCE")
            meta = export_metrohm899_contract_metadata()
            meta["state_slots"] = {k: v for k, v in meta["state_slots"].items() if self._kf899_visible(k)}
            meta["capabilities"].update(_kf899_template_module().capability_metadata())  # additive public-data extension
            for cap in meta["capabilities"].values():
                cap["state_effects"] = [e for e in cap["state_effects"] if self._kf899_visible(e["state_id"])]
            meta.pop("host_capabilities")
            meta["instance_id"] = instance_id
            return meta

    def read(self, *, instance_id, state_ids=None):
        with self._kf899_host._kf899_lock:
            if not self._kf899_allowed(instance_id):
                return self._kf899_response(instance_id, status="rejected", code="UNKNOWN_INSTANCE")
            instance = self._kf899_host._kf899_instances[instance_id]
            if state_ids is None:
                state_ids = [sid for sid in instance["state"] if self._kf899_visible(sid)]
            if not isinstance(state_ids, list) or not all(isinstance(x, str) and x in instance["state"] for x in state_ids):
                return self._kf899_response(instance_id, status="rejected", code="INVALID_ARGUMENT")
            if any(not self._kf899_visible(sid) for sid in state_ids):
                return self._kf899_response(instance_id, status="rejected", code="ACCESS_DENIED")
            meta = export_metrohm899_contract_metadata()["state_slots"]
            obs = {}
            s = instance["state"]
            for sid in state_ids:
                value = _kf899_copy.deepcopy(s[sid])
                measurement = sid.startswith("measurement.") and sid not in ("measurement.synthetic", "measurement.drift_kg_s")
                sampled_at = s["measurement.sample_time_s"] if measurement else self._kf899_host.time_s
                obs[sid] = {"value": value, "unit": meta[sid]["unit"], "quality": "unavailable" if value is None else "estimated" if meta[sid]["origin"] == "derived" else "valid",
                    "sample_time_s": sampled_at, "sample_window_s": [sampled_at - s["measurement.elapsed_s"], sampled_at] if measurement and sampled_at is not None and meta[sid]["sampling"]["mode"] == "integral" else None}
                if value is None:
                    obs[sid]["reason"] = "No configured or acquired synthetic value"
            return self._kf899_response(instance_id, observations=obs)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self._kf899_host
        with host._kf899_lock:
            if not self._kf899_allowed(instance_id):
                return self._kf899_response(instance_id, request_id, "rejected", code="UNKNOWN_INSTANCE")
            if not isinstance(request_id, str) or not request_id or not isinstance(capability_id, str) or not isinstance(arguments, dict):
                return self._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
            try:
                fingerprint = _kf899_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            except (ValueError, TypeError, OverflowError):
                return self._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
            key = (self._kf899_principal, instance_id, request_id)
            if key in host._kf899_requests:
                previous = host._kf899_requests[key]
                if previous[0] != fingerprint:
                    return self._kf899_response(instance_id, request_id, "rejected", code="REQUEST_CONFLICT")
                return _kf899_copy.deepcopy(previous[1])
            if capability_id in METROHM899_TEMPLATE_CAPABILITIES:  # additive public-data extension; legacy capabilities unchanged
                response = _kf899_template_module().invoke(self, host, instance_id, capability_id, arguments, request_id,
                    evaluator=host._kf899_principals[self._kf899_principal]["visibility"] != "agent")
                host._kf899_requests[key] = (fingerprint, _kf899_copy.deepcopy(response))
                host._kf899_logs.append({"instance_id": instance_id, "principal_id": self._kf899_principal, "request_id": request_id, "capability_id": capability_id, "status": response["status"]})
                return _kf899_copy.deepcopy(response)
            meta = export_metrohm899_contract_metadata()
            definition = meta["capabilities"].get(capability_id)
            if definition is None:
                code = "UNSUPPORTED" if capability_id.startswith("hardware.") or capability_id == "coulometry.auto_current" else "ACCESS_DENIED" if capability_id.startswith("sim.") else "UNKNOWN_CAPABILITY"
                return self._kf899_response(instance_id, request_id, "unsupported" if code == "UNSUPPORTED" else "rejected", code=code)
            def reject(code):
                response = self._kf899_response(instance_id, request_id, "rejected", code=code)
                host._kf899_requests[key] = (fingerprint, _kf899_copy.deepcopy(response))
                return response
            if not _kf899_arguments_valid(capability_id, arguments):
                return reject("INVALID_ARGUMENT")
            instance = host._kf899_instances[instance_id]
            candidate = _kf899_copy.deepcopy(instance)
            s = candidate["state"]
            result = None
            if capability_id == "power.set":
                s["power.enabled"] = arguments["enabled"]
                if not arguments["enabled"]:
                    s["coulometry.running"] = False
                    s["coulometry.status"] = "OFF"
                elif s["coulometry.status"] == "OFF":
                    s["coulometry.status"] = "READY" if s["sample.present"] else "IDLE"
                result = {"enabled": arguments["enabled"]}
            elif capability_id == "slot.set_installed":
                if s["power.enabled"] or s["coulometry.running"]:
                    return reject("PRECONDITION_FAILED")
                slot = arguments["slot"]
                if slot != "cell" and arguments["installed"] and not s["slot.cell.installed"]:
                    return reject("PRECONDITION_FAILED")
                if slot == "cell" and not arguments["installed"] and (s["slot.generator.installed"] or s["slot.indicator.installed"]):
                    return reject("PRECONDITION_FAILED")
                s[f"slot.{slot}.installed"] = arguments["installed"]
                result = dict(arguments)
            elif capability_id == "coulometry.configure":
                if s["coulometry.running"]:
                    return reject("BUSY")
                for name, value in arguments.items():
                    s["settings." + name] = value
                result = {name: s["settings." + name] for name in definition["output_schema"]["properties"]}
            elif capability_id == "coulometry.start":
                if s["coulometry.running"]:
                    return reject("BUSY")
                if not s["power.enabled"] or not s["sample.present"] or not s["reagent.ready"] or not all(s[f"slot.{x}.installed"] for x in ("cell", "generator", "indicator")) or s["settings.drift_compensation_kg_s"] is None or s["settings.endpoint_hold_s"] is None or s["measurement.drift_kg_s"] > s["settings.start_drift_threshold_kg_s"] or instance["curve"] or s["measurement.sample_time_s"] is not None:
                    return reject("PRECONDITION_FAILED")
                s.update({"coulometry.running": True, "coulometry.status": "EXTRACTING" if s["settings.extraction_time_s"] else "TITRATING", "measurement.elapsed_s": 0.0,
                          "measurement.charge_c": 0.0, "measurement.water_mass_kg": 0.0,
                          "measurement.water_fraction": None if s["settings.sample_mass_kg"] is None else 0.0,
                          "measurement.sample_time_s": host.time_s, "endpoint.hold_elapsed_s": 0.0})
                result = _kf899_reading(s)
            elif capability_id == "coulometry.stop":
                if s["coulometry.running"]:
                    s["coulometry.running"] = False
                    s["coulometry.status"] = "STOPPED"
                result = _kf899_reading(s)
            elif capability_id == "coulometry.reading":
                result = _kf899_reading(s)
            elif capability_id == "coulometry.curve":
                result = {"records": _kf899_copy.deepcopy(instance["curve"]), "synthetic": True}
            if candidate != instance:
                candidate["revision"] += 1
                try:
                    host._kf899_sync(instance_id, s)
                except Exception:
                    return self._kf899_response(instance_id, request_id, "failed", code="DEVICE_ERROR", side_effects="unknown")
                host._kf899_instances[instance_id] = candidate
            response = self._kf899_response(instance_id, request_id, result=result)
            host._kf899_requests[key] = (fingerprint, _kf899_copy.deepcopy(response))
            host._kf899_logs.append({"instance_id": instance_id, "principal_id": self._kf899_principal, "request_id": request_id, "capability_id": capability_id, "status": response["status"]})
            return response

    def write(self, *, instance_id, state_id, value, request_id):
        with self._kf899_host._kf899_lock:
            if not self._kf899_allowed(instance_id):
                return self._kf899_response(instance_id, request_id, "rejected", code="UNKNOWN_INSTANCE")
            slots = export_metrohm899_contract_metadata()["state_slots"]
            if not isinstance(state_id, str) or state_id not in slots:
                return self._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
            route = slots[state_id]["write_via"]
            if not self._kf899_visible(state_id) or route is None:
                return self._kf899_response(instance_id, request_id, "rejected", code="ACCESS_DENIED")
            args = {**route["fixed_arguments"], route["parameter"]: value}
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments=args, request_id=request_id)

    def logs(self):
        with self._kf899_host._kf899_lock:
            return _kf899_copy.deepcopy([row for row in self._kf899_host._kf899_logs if row["principal_id"] == self._kf899_principal and self._kf899_allowed(row["instance_id"])])


def _kf899_arguments_valid(capability, arguments):
    if capability in ("coulometry.start", "coulometry.stop", "coulometry.reading", "coulometry.curve"):
        return not arguments
    if capability == "power.set":
        return set(arguments) == {"enabled"} and type(arguments["enabled"]) is bool
    if capability == "slot.set_installed":
        return set(arguments) == {"slot", "installed"} and isinstance(arguments["slot"], str) and arguments["slot"] in ("cell", "generator", "indicator") and type(arguments["installed"]) is bool
    if capability != "coulometry.configure":
        return False
    allowed = {"current_a", "drift_compensation_kg_s", "start_drift_threshold_kg_s", "extraction_time_s", "stirring_level", "sample_mass_kg", "endpoint_hold_s"}
    if not arguments or not set(arguments) <= allowed:
        return False
    for name, value in arguments.items():
        if name in ("drift_compensation_kg_s", "sample_mass_kg", "endpoint_hold_s") and value is None:
            continue
        if not _kf899_number(value):
            return False
        if name == "current_a" and value not in (0.1, 0.2, 0.4):
            return False
        if name == "start_drift_threshold_kg_s" and not KF899_START_DRIFT_MIN_KG_S <= value <= KF899_START_DRIFT_MAX_KG_S:
            return False
        if name == "extraction_time_s" and not 0 <= value <= 999999:
            return False
        if name == "stirring_level" and (type(value) is not int or not -15 <= value <= 15):
            return False
        if name in ("drift_compensation_kg_s", "endpoint_hold_s") and value < 0:
            return False
        if name == "sample_mass_kg" and value <= 0:
            return False
    return True
# END CHEM-102 METROHM 899 APPEND-ONLY RUNTIME

# CHEM-102 additive numerical/schema clarifications (preserve append-only file).
_kf899_original_step = Metrohm899Host.step


def _kf899_finite_step(self, dt_s):
    """Reject overflow in hidden intermediate integrals before any mutation.

    This is a floating-point domain check, not a manufacturer operating limit.
    An excessively large requested world step is rejected conservatively even
    when the ideal endpoint would complete earlier inside that interval.
    """
    with self._kf899_lock:
        if _kf899_number(dt_s) and dt_s > 0:
            for item in self._kf899_instances.values():
                s = item["state"]
                if s["coulometry.running"]:
                    candidates = (s["measurement.elapsed_s"] + dt_s,
                        s["settings.current_a"] * dt_s,
                        s["truth.water_remaining_kg"] + s["truth.background_drift_kg_s"] * dt_s,
                        s["settings.drift_compensation_kg_s"] * (s["measurement.elapsed_s"] + dt_s))
                    if not all(_kf899_number(v) for v in candidates):
                        raise ValueError("Requested integration exceeds finite numerical range")
        return _kf899_original_step(self, dt_s)


Metrohm899Host.step = _kf899_finite_step
_kf899_original_metadata = export_metrohm899_contract_metadata


def export_metrohm899_contract_metadata():
    result = _kf899_original_metadata()
    for schema in (result["state_slots"]["settings.sample_mass_kg"]["value_schema"],
                   result["capabilities"]["coulometry.configure"]["input_schema"]["properties"]["sample_mass_kg"],
                   result["capabilities"]["coulometry.configure"]["output_schema"]["properties"]["sample_mass_kg"]):
        number = schema["anyOf"][0]
        number.pop("minimum", None)
        number["exclusiveMinimum"] = 0
    result["capabilities"]["coulometry.curve"]["meaning_zh"] = "返回每次运行中宿主步进后记录的合成滴定曲线；最后一点可使用步内解析完成时刻，不插值补造采样。"
    result["boundaries"].append("Floating-point overflow is rejected before state/time mutation; excessively large requested steps may be conservatively rejected before endpoint clipping.")
    return result


# BEGIN CHEM-101 METROHM OMNIS APPEND-ONLY RUNTIME
import copy as _omnis_copy
import hashlib as _omnis_hashlib
import json as _omnis_json
import math as _omnis_math
import threading as _omnis_threading

METROHM_OMNIS_TYPE = "instrument.metrohm_omnis"
METROHM_OMNIS_CYLINDERS_M3 = (5e-6, 1e-5, 2e-5, 5e-5)
METROHM_OMNIS_STEPS_PER_CYLINDER = 100000
METROHM_OMNIS_MAX_SPEED_RAD_S = 60 * _omnis_math.pi
METROHM_OMNIS_MAX_POINTS = 1024
METROHM_OMNIS_TEMPLATE_CAPABILITIES = ("template.list", "template.read", "titration.template_report")


def _omnis_template_module():
    """Lazy import of the public-data template extension (scripts/basic40_templates/metrohm_omnis.py)."""
    try:
        from .basic40_templates import metrohm_omnis as module
    except ImportError:
        from basic40_templates import metrohm_omnis as module
    return module


def _omnis_num(unit, minimum=None, maximum=None, nullable=False, positive=False):
    s = {"type": "number", "x-unit": unit}
    if minimum is not None:
        s["minimum"] = minimum
    if maximum is not None:
        s["maximum"] = maximum
    if positive:
        s["exclusiveMinimum"] = 0
    return {"anyOf": [s, {"type": "null"}]} if nullable else s


def _omnis_obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _omnis_finite(v):
    try:
        return type(v) in (int, float) and _omnis_math.isfinite(v)
    except (OverflowError, TypeError):
        return False


def _omnis_matches(s, v):
    if "anyOf" in s:
        return any(_omnis_matches(branch, v) for branch in s["anyOf"])
    typ = s.get("type")
    if typ == "null":
        return v is None
    if typ == "object":
        return isinstance(v, dict) and set(v) == set(s["properties"]) and all(_omnis_matches(s["properties"][k], x) for k, x in v.items())
    if typ == "array":
        return isinstance(v, list) and len(v) <= s.get("maxItems", len(v)) and all(_omnis_matches(s["items"], x) for x in v)
    if typ in ("number", "integer"):
        if not _omnis_finite(v) or (typ == "integer" and type(v) is not int):
            return False
        if ("minimum" in s and v < s["minimum"]) or ("maximum" in s and v > s["maximum"]) or ("exclusiveMinimum" in s and v <= s["exclusiveMinimum"]):
            return False
    elif typ == "boolean":
        if type(v) is not bool:
            return False
    elif typ == "string":
        if not isinstance(v, str) or len(v) < s.get("minLength", 0) or len(v) > s.get("maxLength", len(v)):
            return False
    else:
        return False
    return ("enum" not in s or v in s["enum"]) and ("const" not in s or v == s["const"])


def export_metrohm_omnis_contract_metadata():
    """Static metadata only; no sample fixture or instance truth is exported."""
    boolean, synthetic = {"type": "boolean"}, {"type": "boolean", "const": True}
    nullable_id = {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 256}, {"type": "null"}]}
    status = {"type": "string", "enum": ["idle", "running", "completed", "stopped", "out_of_range", "limit_reached"]}
    mode = {"type": "string", "enum": ["endpoint", "dynamic_equivalent"]}
    point = _omnis_obj({"time_s": _omnis_num("s", 0), "volume_m3": _omnis_num("m^3", 0),
        "potential_v": _omnis_num("V", -2.4, 2.4), "ph": _omnis_num("1"), "synthetic": synthetic})
    curve_schema = {"type": "array", "items": point, "maxItems": METROHM_OMNIS_MAX_POINTS}
    slots = {}
    def route(cap, parameter, fixed=None):
        return {"capability_id": cap, "parameter": parameter, "fixed_arguments": fixed or {}}
    def slot(sid, unit, origin, schema, text, write=None, visibility="agent", sampling="instant"):
        slots[sid] = {"description_zh": text, "unit": unit, "origin": origin, "value_schema": schema,
            "visibility": visibility, "access": "read_write" if write else "read_only", "write_via": write,
            "read_binding": "MetrohmOmnisSession.read(instance_id=..., state_ids=[state_id])",
            "sampling": {"mode": sampling, "time_domain": "simulation", "window": None}, "reference_conditions": None}
    slot("power.enabled", "1", "setpoint", boolean, "逻辑供电；断电停止滴定，保留已加体积与曲线。", route("power.set", "enabled"))
    for p in ("electrode", "titration_cell"):
        slot("slot." + p + ".connection_id", "1", "scene", nullable_id, "可替换槽逻辑连接；非机械插拔或兼容性验证。", route("slot.set_connection", "connection_id", {"port_id": p}))
    slot("dosing.cylinder_volume_m3", "m^3", "setpoint", {"anyOf": [{"type": "number", "enum": list(METROHM_OMNIS_CYLINDERS_M3), "x-unit": "m^3"}, {"type": "null"}]}, "单个缸槽容量，5/10/20/50mL换算SI。", route("dosing.configure_cylinder", "volume_m3"))
    slot("dosing.quantum_m3", "m^3", "derived", _omnis_num("m^3", nullable=True), "选定容量/100000；分辨率不代表准确度。")
    slot("dosing.rate_m3_s", "m^3/s", "setpoint", _omnis_num("m^3/s", nullable=True, positive=True), "用户显式加液速率；厂家上限未知。", route("dosing.set_rate", "rate_m3_s"))
    slot("dosing.delivered_volume_m3", "m^3", "derived", _omnis_num("m^3", 0), "当前单缸实验已经加入的量化体积。")
    slot("dosing.piston_fraction", "1", "derived", _omnis_num("1", 0, 1), "已加入体积/缸容量；仅供示意视觉映射，不等于真实活塞长度。")
    slot("stirring.angular_speed_rad_s", "rad/s", "setpoint", _omnis_num("rad/s", 0, METROHM_OMNIS_MAX_SPEED_RAD_S), "1800 rpm = 60π rad/s；实际旋转还需逻辑电源。", route("stirring.set_speed", "angular_speed_rad_s"))
    slot("stirring.angle_rad", "rad", "derived", _omnis_num("rad", 0, 2 * _omnis_math.pi), "共同宿主时间积分后的示意搅拌角。")
    slot("titrant.concentration_mol_m3", "mol/m^3", "setpoint", _omnis_num("mol/m^3", nullable=True, positive=True), "用户给定强碱滴定剂浓度；不从隐藏样品真值反推。", route("titration.configure_titrant", "concentration_mol_m3"))
    slot("method.mode", "1", "setpoint", {"anyOf": [mode, {"type": "null"}]}, "endpoint或dynamic_equivalent，后者为自定义有限差分算法。")
    for sid, unit, text, nullable in (("method.dose_increment_m3", "m^3", "最大名义单次增量，动态模式可细分。", True), ("method.maximum_volume_m3", "m^3", "本次单缸最大累积加液体积。", True), ("method.endpoint_potential_v", "V", "endpoint模式明确给定的目标电位，动态模式为null。", True), ("stability.hold_s", "s", "显式稳定保持窗口；没有厂家默认值。", True), ("stability.tolerance_v", "V", "窗口内合成电位最大最小差的允许值。", True)):
        slot(sid, unit, "setpoint", _omnis_num(unit, nullable=nullable), text)
    slot("stability.stable", "1", "derived", boolean, "停止加液后窗口内合成读数符合显式稳定规则。")
    slot("titration.status", "1", "derived", status, "完成只表示本工程假设算法结束；limit_reached可能未得到终点。")
    slot("titration.running", "1", "derived", boolean, "只由host_step推进过程。")
    slot("titration.phase", "1", "derived", {"type": "string", "enum": ["idle", "settling", "dosing", "finished"]}, "分段加液/稳定采样状态。")
    slot("measurement.potential_v", "V", "derived", _omnis_num("V", -2.4, 2.4, True), "理想强酸强碱Nernst合成电位；越界为null。")
    slot("measurement.ph", "1", "derived", _omnis_num("1", nullable=True), "由强酸强碱电中性与水离解计算的合成读数。")
    slot("measurement.sample_time_s", "s", "derived", _omnis_num("s", 0, nullable=True), "最近宿主生成的合成测量时刻。")
    slot("measurement.synthetic", "1", "derived", synthetic, "所有测量与曲线均为synthetic。")
    slot("titration.curve", "1", "derived", curve_schema, "仅已稳定采点的观测序列；不含样品真值。", sampling="series")
    slot("titration.endpoint_volume_m3", "m^3", "derived", _omnis_num("m^3", 0, nullable=True), "从观测曲线估算的终点体积，不读取宿主酸摩尔数求终点。")
    slot("titration.content_mol_m3", "mol/m^3", "derived", _omnis_num("mol/m^3", 0, nullable=True), "按显式用户样品体积和化学计量比计算的含量。")
    slot("display.text", "1", "derived", {"type": "string"}, "合成结果与运行状态的显示文本。")
    for key, unit in (("volume_m3", "m^3"), ("acid_moles", "mol"), ("temperature_k", "K"), ("reference_ph", "1"), ("reference_potential_v", "V")):
        slot("truth." + key, unit, "process_truth", _omnis_num(unit, nullable=True), "宿主专有的合成样品条件；agent不可读。", visibility="debug")
    process_effects = [s for s in slots if s.startswith(("dosing.delivered", "dosing.piston", "stability.stable", "titration.", "measurement.", "display.")) and s != "titration.content_mol_m3"]
    reset_effects = process_effects + ["titration.content_mol_m3"]
    defs = {
        "power.set": ({"enabled": boolean}, {"enabled": boolean}, {"enabled": True}, ["power.enabled", "titration.running", "titration.status", "titration.phase", "stability.stable", "display.text"], ["housing"], "设置逻辑供电；断电保留已经加入的体积和曲线。", "实例可用。"),
        "slot.set_connection": ({"port_id": {"type": "string", "enum": ["electrode", "titration_cell"]}, "connection_id": nullable_id}, {"port_id": {"type": "string"}, "connection_id": nullable_id}, {"port_id": "electrode", "connection_id": "electrode_01"}, ["slot.electrode.connection_id", "slot.titration_cell.connection_id"], ["electrode_slot", "titration_cell_slot"], "登记可替换部件的逻辑槽连接。", "未在滴定；不执行机械插拔。"),
        "dosing.configure_cylinder": ({"volume_m3": {"type": "number", "enum": list(METROHM_OMNIS_CYLINDERS_M3), "x-unit": "m^3"}}, {"volume_m3": _omnis_num("m^3")}, {"volume_m3": 2e-5}, ["dosing.cylinder_volume_m3", "dosing.quantum_m3", "dosing.piston_fraction"], ["cylinder_slot", "dosing_module"], "选择唯一缸槽容量，尚未实验时可改；每缸100000步。", "未运行、尚无已加液体积；已有方法的最大体积不可超容量。"),
        "titration.configure_titrant": ({"concentration_mol_m3": _omnis_num("mol/m^3", positive=True)}, {"concentration_mol_m3": _omnis_num("mol/m^3", positive=True)}, {"concentration_mol_m3": 100}, ["titrant.concentration_mol_m3"], ["dosing_module"], "显式配置强碱滴定剂浓度。", "未运行且尚未加液。"),
        "titration.configure_method": ({"mode": mode, "dose_increment_m3": _omnis_num("m^3", positive=True), "maximum_volume_m3": _omnis_num("m^3", positive=True), "endpoint_potential_v": _omnis_num("V", -2.4, 2.4, True)}, {"mode": mode, "dose_increment_m3": _omnis_num("m^3", positive=True), "maximum_volume_m3": _omnis_num("m^3", positive=True), "endpoint_potential_v": _omnis_num("V", -2.4, 2.4, True)}, {"mode": "endpoint", "dose_increment_m3": 1e-7, "maximum_volume_m3": 1.5e-5, "endpoint_potential_v": 0}, ["method.mode", "method.dose_increment_m3", "method.maximum_volume_m3", "method.endpoint_potential_v"], ["dosing_module"], "配置定电位终点或动态等当点合成方法。", "未运行且尚未加液；已选缸，增量和上限必须是缸步长整数倍；endpoint需要目标电位，动态模式目标为null。"),
        "dosing.set_rate": ({"rate_m3_s": _omnis_num("m^3/s", positive=True)}, {"rate_m3_s": _omnis_num("m^3/s", positive=True)}, {"rate_m3_s": 1e-7}, ["dosing.rate_m3_s"], ["dosing_module"], "显式设定合成加液速率，不推断厂商流量范围。", "未运行；有限正数。"),
        "titration.configure_stability": ({"hold_s": _omnis_num("s", positive=True), "tolerance_v": _omnis_num("V", 0)}, {"hold_s": _omnis_num("s", positive=True), "tolerance_v": _omnis_num("V", 0)}, {"hold_s": 0.5, "tolerance_v": 0.001}, ["stability.hold_s", "stability.tolerance_v"], ["electrode_slot"], "稳定窗口与电位容差由调用者显式给定。", "未运行；窗口正且容差非负；理想瞬时混合假设下静止加液窗口电位不变。"),
        "stirring.set_speed": ({"angular_speed_rad_s": _omnis_num("rad/s", 0, METROHM_OMNIS_MAX_SPEED_RAD_S)}, {"angular_speed_rad_s": _omnis_num("rad/s", 0, METROHM_OMNIS_MAX_SPEED_RAD_S)}, {"angular_speed_rad_s": 10}, ["stirring.angular_speed_rad_s"], ["stir_bar", "stirrer_platform"], "设定搅拌角速度，上限1800rpm；不虚构混合时间常数。", "滴定运行中不得关闭搅拌；实际角度仅宿主推进。"),
        "titration.start": ({}, {"status": status, "synthetic": synthetic}, {}, reset_effects, ["dosing_module", "electrode_slot", "titration_cell_slot", "stir_bar"], "启动分段加液/稳定采样；调用不加液、不产生读数、不推进时钟。", "电源、两个槽、缸、滴定剂、方法、速率、稳定规则、正搅拌速度和宿主样品齐备；开始单次实验前已加体积为0。"),
        "titration.stop": ({}, {"status": status, "synthetic": synthetic}, {}, ["titration.status", "titration.running", "titration.phase", "stability.stable", "display.text"], ["dosing_module"], "停止并保留已加液体积及采点，已转移液体不回滚。", "实例可用；不支持停止后的续跑，宿主换样后才能新实验。"),
        "titration.read_curve": ({}, {"samples": curve_schema, "endpoint_volume_m3": _omnis_num("m^3", 0, nullable=True), "status": status, "synthetic": synthetic}, {}, [], ["housing"], "读取合成曲线及估算终点，不推进时间、不访问样品真值。", "实例可用。"),
        "titration.calculate_content": ({"sample_volume_m3": _omnis_num("m^3", positive=True), "stoichiometric_ratio": _omnis_num("1", positive=True)}, {"content_mol_m3": _omnis_num("mol/m^3", 0), "synthetic": synthetic}, {"sample_volume_m3": 2.5e-5, "stoichiometric_ratio": 1}, ["titration.content_mol_m3"], ["housing"], "含量=终点体积×用户滴定剂浓度×用户计量比/用户样品体积。", "算法已得到终点，且结果为有限数。"),
    }
    caps = {}
    scene_keys = ["power.enabled", "slot.electrode.connection_id", "slot.titration_cell.connection_id", "dosing.cylinder_volume_m3", "dosing.delivered_volume_m3", "dosing.piston_fraction", "stirring.angle_rad", "stirring.angular_speed_rad_s", "display.text"]
    for name, (inputs, outputs, example, effects, components, desc, pre) in defs.items():
        scene_effects = [s for s in effects if s in scene_keys]
        kind = "query" if name == "titration.read_curve" else "analysis" if name == "titration.calculate_content" else "configuration" if "configure" in name or name == "slot.set_connection" else "device_command"
        caps[name] = {"availability": "implemented", "visibility": "agent", "kind": kind, "description_zh": desc,
            "input_schema": _omnis_obj(inputs), "output_schema": _omnis_obj(outputs), "example_arguments": [example],
            "state_effects": [{"state_id": s, "timing": "immediate", "description_zh": desc} for s in effects],
            "observes": list(effects), "component_ids": components, "preconditions_zh": pre,
            "binding": "basic40_runtime_measure.MetrohmOmnisSession.invoke(instance_id=..., capability_id='" + name + "', arguments=..., request_id=...)",
            "completion_criterion_zh": "同步提交命令；实验完成须由host_step推进到completed并读取推断终点。",
            "model_effects": {"mode": "host_required" if scene_effects else "none", "binding_ids": [], "description_zh": "可选scene_adapter接收只含允许场景键的快照；具体几何求值另验。" if scene_effects else "配置、曲线读取或纯分析不改变几何。"},
            "implementation": {"process": "implemented", "host": "implemented", "scene": "declared" if scene_effects else "not_applicable", "robot": "absent", "hardware": "absent"},
            "error_codes": ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "ACCESS_DENIED", "PRECONDITION_FAILED", "BUSY", "REQUEST_CONFLICT", "DEVICE_ERROR"], "retry": "same_request_id; in-memory only"}
    def parameter(value, unit, evidence, text):
        return {"value": value, "unit": unit, "meaning_zh": text, "evidence": evidence, "source_ids": ["workbook_parameters" if evidence == "catalog" else "runtime_code"], "uncertainty": None}
    params = {
        "cylinder_volumes_m3": parameter(list(METROHM_OMNIS_CYLINDERS_M3), "m^3", "catalog", "表列5/10/20/50mL选项。"),
        "steps_per_cylinder": parameter(100000, "1", "catalog", "每缸加液分辨率，不等于精度。"),
        "maximum_stirring_speed_rad_s": parameter(METROHM_OMNIS_MAX_SPEED_RAD_S, "rad/s", "catalog", "1800rpm换算60πrad/s。"),
        "potential_range_v": parameter([-2.4, 2.4], "V", "catalog", "整机±2400mV测量范围。"),
        "dynamic_target_delta_v": parameter(0.02, "V", "engineering_assumption", "自定义动态增量算法的目标电位变化；非厂家方法参数。"),
        "dynamic_peak_fraction": parameter(0.8, "1", "engineering_assumption", "峰后两个较低斜率小于峰值80%才确认有限差分终点。"),
        "max_curve_points": parameter(METROHM_OMNIS_MAX_POINTS, "1", "engineering_assumption", "运行层资源上限，不是厂商数据容量。"),
        "water_ionic_product": parameter(1e-14, "1", "engineering_assumption", "浓度按mol/L归一后的Kw，固定25°C近似，温度仅进入Nernst斜率。"),
    }
    for key, unit in (("manufacturer_max_flow_rate", "m^3/s"), ("electrode_response_time", "s"), ("electrode_mass", "kg"), ("mixing_time_constant", "s"), ("sample_noise", "V"), ("cylinder_actual_stroke", "m"), ("cylinder_mass", "kg"), ("calibration_accuracy", "V"), ("fault_probability", "1")):
        params[key] = parameter(None, unit, "unknown", "清单未给出，保持null；不以整机规格代替部件参数。")
    limitations = ["All measurements, curves and content are synthetic; no real hardware, robotics, chemical calibration, noise or fault model is implemented.",
        "Only ideal monoprotic strong-acid/strong-base 1:1 titration is simulated; redox/complexometry, weak acids, real kinetics, activity corrections, temperature-dependent Kw and manufacturer endpoint firmware are not implemented.",
        "Host explicitly supplies sample conditions and absolute shared time. The ideal instantaneous-mixing model has constant potential while dosing is stopped; stability is checked over the explicit hold window.",
        "One cylinder per run, no refill, flushing, sample transfer or physical insertion. Integer volume steps represent catalog resolution, not metrological accuracy.",
        "Session filtering is an API boundary only; the trusted host, Python runtime and filesystem require separate deployment isolation. Debug truth never enters agent read/invoke/describe/logs or scene snapshots.",
        "Request deduplication is in-memory only. Scene callback failure leaves process uncommitted but a partially changed external scene may need reconciliation.",
        "Unknown manufacturer flow/stability/calibration parameters remain null; explicitly configured simulation inputs are not manufacturer specifications."]
    return {"asset_types": {METROHM_OMNIS_TYPE: {"catalog_id": "CHEM-101", "model": "Metrohm | OMNIS Professional Titrator 2.1001.0320", "asset_version": "1.0.0", "state_slots": slots, "capabilities": caps, "parameters": params}},
        "host_capabilities": {"sim.host_step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "MetrohmOmnisRuntime.host_step(time_s)", "effects": reset_effects + ["stirring.angle_rad"]},
            "sim.load_sample": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "MetrohmOmnisRuntime.host_load_sample(instance_id, sample)", "semantics_zh": "宿主设置新实验的合成真值、清空旧实验与活塞累计；不代表实际自动换样或缸补液。"}},
        "scene_snapshot_keys": scene_keys, "limitations": limitations,
        "engineering_equations": ["d=(n_acid-c_base*V_dosed)/(1000*(V_sample+V_dosed)); H=(d+sqrt(d*d+4*Kw))/2 with cancellation-safe branch; pH=-log10(H)",
            "E=E_ref-ln(10)*R*T/F*(pH-pH_ref); R=8.31446261815324 J/(mol K), F=96485.33212331001 C/mol",
            "quantum=V_cylinder/100000; delivered=integer_steps*quantum; theta=(theta+omega*dt) mod 2pi",
            "endpoint: linear interpolation of observed E crossing; dynamic_equivalent: maximum observed |dE/dV| segment midpoint, confirmed by two later slopes < 0.8*peak",
            "dynamic step=min(nominal_step, max(quantum, floor(0.02/last_abs_slope/quantum)*quantum)); content=V_endpoint*c_titrant*stoichiometric_ratio/V_user_sample"]}


class MetrohmOmnisRuntime:
    """Trusted host; hand a scoped Session, never this host, to an agent."""
    def __init__(self, *, scene_adapter=None):
        self.scene_adapter, self.time_s = scene_adapter, 0.0
        self._instances, self._principals, self._requests, self._logs = {}, {}, {}, {}
        self._lock, self._metadata = _omnis_threading.RLock(), export_metrohm_omnis_contract_metadata()
        self.register_principal("__public_agent__", visibility="agent")
        self._agent = self.session("__public_agent__")

    def create_instance(self, instance_id, asset_type_id=METROHM_OMNIS_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != METROHM_OMNIS_TYPE:
                raise ValueError("Unique instance and declared asset type required")
            inst = {"revision": 0, "power": False, "connections": {"electrode": None, "titration_cell": None},
                "cylinder": None, "quantum": None, "rate": None, "speed": 0.0, "angle": 0.0, "concentration": None,
                "method": None, "hold": None, "tolerance": None,
                "truth": {key: None for key in ("volume_m3", "acid_moles", "temperature_k", "reference_ph", "reference_potential_v")}}
            self._reset_run(inst)
            if self.scene_adapter:
                self.scene_adapter(instance_id, self._scene(inst))
            self._instances[instance_id] = inst

    @staticmethod
    def _reset_run(inst):
        inst.update({"steps": 0, "running": False, "status": "idle", "phase": "idle", "stable": False,
            "curve": [], "ph": None, "potential": None, "sample_time": None, "endpoint": None, "content": None,
            "event_time": None, "phase_start": None, "phase_start_steps": 0, "target_steps": 0,
            "slopes": [], "settling_potential": None})

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Invalid trusted principal")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(x, str) for x in instance_ids)):
                raise ValueError("Invalid instance scope")
            scope = None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope <= self._instances.keys():
                raise ValueError("Unknown instance scope")
            self._principals[principal_id], self._logs[principal_id] = (visibility, scope), []

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Trusted registration required")
        return MetrohmOmnisSession(self, principal_id)

    def describe(self, **kwargs):
        return self._agent.describe(**kwargs)

    def read(self, **kwargs):
        return self._agent.read(**kwargs)

    def invoke(self, **kwargs):
        return self._agent.invoke(**kwargs)

    def write(self, **kwargs):
        return self._agent.write(**kwargs)

    def describe_host(self):
        return _omnis_copy.deepcopy(self._metadata["host_capabilities"])

    @staticmethod
    def _volume(inst):
        return inst["steps"] * inst["quantum"] if inst["quantum"] else 0.0

    @staticmethod
    def _display(inst):
        return "OFF" if not inst["power"] else "SYNTHETIC " + inst["status"] + (" %.3f mL" % (MetrohmOmnisRuntime._volume(inst) * 1e6))

    @staticmethod
    def _scene(inst):
        return {"power.enabled": inst["power"], "slot.electrode.connection_id": inst["connections"]["electrode"],
            "slot.titration_cell.connection_id": inst["connections"]["titration_cell"], "dosing.cylinder_volume_m3": inst["cylinder"],
            "dosing.delivered_volume_m3": MetrohmOmnisRuntime._volume(inst), "dosing.piston_fraction": inst["steps"] / METROHM_OMNIS_STEPS_PER_CYLINDER,
            "stirring.angle_rad": inst["angle"], "stirring.angular_speed_rad_s": inst["speed"], "display.text": MetrohmOmnisRuntime._display(inst)}

    def scene_snapshot(self, instance_id):
        with self._lock:
            if instance_id not in self._instances:
                raise ValueError("Unknown instance")
            return _omnis_copy.deepcopy(self._scene(self._instances[instance_id]))

    def apply_scene(self, instance_id):
        with self._lock:
            if self.scene_adapter is None:
                raise RuntimeError("No scene adapter registered")
            snapshot = self.scene_snapshot(instance_id)
            self.scene_adapter(instance_id, snapshot)
            return snapshot

    @staticmethod
    def _values(inst):
        method = inst["method"] or {}
        vals = {**MetrohmOmnisRuntime._scene(inst), "dosing.quantum_m3": inst["quantum"], "dosing.rate_m3_s": inst["rate"],
            "titrant.concentration_mol_m3": inst["concentration"], "stability.hold_s": inst["hold"], "stability.tolerance_v": inst["tolerance"],
            "stability.stable": inst["stable"], "titration.status": inst["status"], "titration.running": inst["running"], "titration.phase": inst["phase"],
            "measurement.potential_v": inst["potential"], "measurement.ph": inst["ph"], "measurement.sample_time_s": inst["sample_time"],
            "measurement.synthetic": True, "titration.curve": inst["curve"], "titration.endpoint_volume_m3": inst["endpoint"], "titration.content_mol_m3": inst["content"]}
        vals.update({"method." + key: method.get(key) for key in ("mode", "dose_increment_m3", "maximum_volume_m3", "endpoint_potential_v")})
        vals.update({"truth." + key: value for key, value in inst["truth"].items()})
        return vals

    def host_load_sample(self, instance_id, sample):
        """Explicit synthetic fixture/reset only; never expose through a Session."""
        with self._lock:
            inst = self._instances.get(instance_id)
            expected = {"volume_m3", "acid_moles", "temperature_k", "reference_ph", "reference_potential_v"}
            if inst is None or inst["running"]:
                raise ValueError("Available stopped instance required")
            if not isinstance(sample, dict) or set(sample) != expected or any(not _omnis_finite(v) for v in sample.values()):
                raise ValueError("All explicit finite sample fields required")
            if sample["volume_m3"] <= 0 or sample["acid_moles"] < 0 or sample["temperature_k"] <= 0:
                raise ValueError("Positive sample volume/temperature and nonnegative acid required")
            candidate = _omnis_copy.deepcopy(inst)
            candidate.pop("template", None)  # a new explicit fixture replaces any template-backed analyte
            candidate["truth"] = {k: float(v) for k, v in sample.items()}
            self._reset_run(candidate)
            candidate["revision"] += 1
            if self.scene_adapter and self._scene(candidate) != self._scene(inst):
                self.scene_adapter(instance_id, self._scene(candidate))
            self._instances[instance_id] = candidate

    def host_load_template_sample(self, instance_id, analyte):
        """Trusted host only: analyte/titrant from the public acid-base library (weak/polyprotic charge balance)."""
        return _omnis_template_module().load_template_sample(self, instance_id, analyte)

    @staticmethod
    def _potential(inst):
        if inst.get("template") is not None:  # template-backed analyte: library pKa(T) charge balance
            return _omnis_template_module().potential(inst)
        truth = inst["truth"]
        volume = MetrohmOmnisRuntime._volume(inst)
        d = (truth["acid_moles"] - inst["concentration"] * volume) / (1000 * (truth["volume_m3"] + volume))
        root = _omnis_math.hypot(d, 2e-7)
        h = (d + root) / 2 if d >= 0 else 2e-14 / (root - d)
        ph = -_omnis_math.log10(h)
        potential = truth["reference_potential_v"] - _omnis_math.log(10) * 8.31446261815324 * truth["temperature_k"] / 96485.33212331001 * (ph - truth["reference_ph"])
        if not _omnis_finite(potential) or not _omnis_finite(ph):
            raise ValueError("Synthetic response outside numeric domain")
        return ph, potential

    @staticmethod
    def _measure(inst, time_s):
        try:
            ph, potential = MetrohmOmnisRuntime._potential(inst)
        except (ValueError, OverflowError, ZeroDivisionError):
            ph, potential = None, None
        inst["sample_time"] = time_s
        if potential is None or not -2.4 <= potential <= 2.4:
            inst["ph"], inst["potential"], inst["status"], inst["running"], inst["phase"], inst["stable"] = None, None, "out_of_range", False, "finished", False
            return False
        inst["ph"], inst["potential"] = ph, potential
        return True

    @staticmethod
    def _complete(inst, status):
        inst["status"], inst["running"], inst["phase"], inst["event_time"] = status, False, "finished", None

    @staticmethod
    def _record(inst, time_s):
        volume, potential = MetrohmOmnisRuntime._volume(inst), inst["potential"]
        point = {"time_s": time_s, "volume_m3": volume, "potential_v": potential, "ph": inst["ph"], "synthetic": True}
        previous = inst["curve"][-1] if inst["curve"] else None
        inst["curve"].append(point)
        method = inst["method"]
        if method["mode"] == "endpoint":
            target = method["endpoint_potential_v"]
            if potential == target:
                inst["endpoint"] = volume
            elif previous and (previous["potential_v"] - target) * (potential - target) < 0:
                inst["endpoint"] = previous["volume_m3"] + (volume - previous["volume_m3"]) * (target - previous["potential_v"]) / (potential - previous["potential_v"])
        elif previous:
            slope = abs((potential - previous["potential_v"]) / (volume - previous["volume_m3"]))
            inst["slopes"].append({"slope": slope, "volume_m3": (volume + previous["volume_m3"]) / 2})
            peak_index = max(range(len(inst["slopes"])), key=lambda n: inst["slopes"][n]["slope"])
            peak = inst["slopes"][peak_index]
            if peak_index >= 1 and len(inst["slopes"]) - peak_index >= 3 and all(s["slope"] < 0.8 * peak["slope"] for s in inst["slopes"][-2:]):
                inst["endpoint"] = peak["volume_m3"]
        if inst["endpoint"] is not None:
            MetrohmOmnisRuntime._complete(inst, "completed")
        elif inst["steps"] >= round(method["maximum_volume_m3"] / inst["quantum"]) or len(inst["curve"]) >= METROHM_OMNIS_MAX_POINTS:
            MetrohmOmnisRuntime._complete(inst, "limit_reached")
        else:
            increment = round(method["dose_increment_m3"] / inst["quantum"])
            if method["mode"] == "dynamic_equivalent" and inst["slopes"] and inst["slopes"][-1]["slope"] > 0:
                increment = min(increment, max(1, int(0.02 / inst["slopes"][-1]["slope"] / inst["quantum"])))
            target_steps = min(inst["steps"] + increment, round(method["maximum_volume_m3"] / inst["quantum"]))
            inst["phase"], inst["phase_start"], inst["phase_start_steps"], inst["target_steps"] = "dosing", time_s, inst["steps"], target_steps
            inst["event_time"] = time_s + (target_steps - inst["steps"]) * inst["quantum"] / inst["rate"]
            inst["stable"] = False

    def _advance(self, inst, start_time, end_time):
        if inst["power"]:
            inst["angle"] = (inst["angle"] + inst["speed"] * (end_time - start_time)) % (2 * _omnis_math.pi)
        while inst["running"] and inst["event_time"] <= end_time:
            event = inst["event_time"]
            if inst["phase"] == "dosing":
                inst["steps"] = inst["target_steps"]
                if not self._measure(inst, event):
                    break
                inst["phase"], inst["phase_start"] = "settling", event
                inst["settling_potential"] = inst["potential"]
                inst["event_time"] = event + inst["hold"]
            else:
                if not self._measure(inst, event):
                    break
                first = inst["settling_potential"]
                # The explicit ideal instantaneous-mixing process is constant during hold.
                if first is None:
                    first = self._potential(inst)[1]
                inst["stable"] = abs(inst["potential"] - first) <= inst["tolerance"]
                if inst["stable"]:
                    self._record(inst, event)
                else:
                    inst["settling_potential"], inst["event_time"] = inst["potential"], event + inst["hold"]
            if inst["running"] and (not _omnis_finite(inst["event_time"]) or inst["event_time"] <= event):
                raise ValueError("Host clock cannot resolve configured process interval")
        if inst["running"] and inst["phase"] == "dosing":
            elapsed = max(0.0, end_time - inst["phase_start"])
            # Half-open step intervals prevent fractional catalog steps.
            added = int(elapsed * inst["rate"] / inst["quantum"] + 1e-8)
            inst["steps"] = min(inst["target_steps"], inst["phase_start_steps"] + added)
            self._measure(inst, end_time)

    def host_step(self, time_s):
        """Advance all instances atomically under one absolute simulation clock."""
        with self._lock:
            if not _omnis_finite(time_s) or time_s < self.time_s:
                raise ValueError("Finite monotonic absolute host time required")
            if time_s == self.time_s:
                return {"time_s": self.time_s, "updated_instance_ids": []}
            candidates = _omnis_copy.deepcopy(self._instances)
            changed = []
            for name, inst in candidates.items():
                self._advance(inst, self.time_s, float(time_s))
                if inst != self._instances[name]:
                    inst["revision"] += 1
                    changed.append(name)
            if self.scene_adapter:
                for name in changed:
                    if self._scene(candidates[name]) != self._scene(self._instances[name]):
                        self.scene_adapter(name, self._scene(candidates[name]))
            self._instances, self.time_s = candidates, float(time_s)
            return {"time_s": self.time_s, "updated_instance_ids": changed}


class MetrohmOmnisSession:
    """API filtering; not an in-process Python or filesystem security sandbox."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal = host, principal_id

    def _instance(self, instance_id):
        _, scope = self.__host._principals[self.__principal]
        return self.__host._instances.get(instance_id) if isinstance(instance_id, str) and (scope is None or instance_id in scope) else None

    def _visible(self, slot):
        visibility, _ = self.__host._principals[self.__principal]
        return slot["visibility"] == "agent" or visibility in ("debug", "evaluator")

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": "1.0.0", "instance_id": instance_id, "request_id": request_id, "operation_id": None,
            "status": status, "state_revision": inst["revision"] if inst else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": _omnis_copy.deepcopy(result or {}), "observations": _omnis_copy.deepcopy(observations or {}), "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        text = {"UNKNOWN_INSTANCE": "Instance unavailable.", "UNKNOWN_CAPABILITY": "Capability unavailable.", "INVALID_ARGUMENT": "Invalid argument schema or numeric domain.", "ACCESS_DENIED": "Access policy denies this request.", "PRECONDITION_FAILED": "Required sample, configuration or operating state unavailable.", "BUSY": "Titration is running.", "REQUEST_CONFLICT": "Request identifier already used with different arguments.", "DEVICE_ERROR": "External scene synchronization failed; process was not committed."}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": text[code], "retryable": False, "side_effects": "unknown" if code == "DEVICE_ERROR" else "none", "progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None) or (asset_type_id is not None and asset_type_id != METROHM_OMNIS_TYPE):
                return self._error(None, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            _, scope = self.__host._principals[self.__principal]
            if scope is not None and not scope:
                return self._error(instance_id, "ACCESS_DENIED")
            spec = _omnis_copy.deepcopy(self.__host._metadata["asset_types"][METROHM_OMNIS_TYPE])
            spec["state_slots"] = {k: v for k, v in spec["state_slots"].items() if self._visible(v)}
            spec["capabilities"].update(_omnis_template_module().capability_metadata())  # additive public-data extension
            return {"protocol_version": "1.0.0", "asset_type_id": METROHM_OMNIS_TYPE, "instance_id": instance_id, "adapter": "basic40_runtime_measure.MetrohmOmnisSession", **spec,
                "limitations": _omnis_copy.deepcopy(self.__host._metadata["limitations"])}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = self.__host._metadata["asset_types"][METROHM_OMNIS_TYPE]["state_slots"]
            if state_ids is None:
                state_ids = [k for k, v in slots.items() if self._visible(v)]
            if not isinstance(state_ids, list) or any(not isinstance(x, str) for x in state_ids) or len(state_ids) != len(set(state_ids)):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(k not in slots or not self._visible(slots[k]) for k in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            values = self.__host._values(inst)
            observations = {k: {"value": _omnis_copy.deepcopy(values[k]), "unit": slots[k]["unit"], "quality": "unavailable" if values[k] is None else "estimated" if slots[k]["origin"] == "derived" else "valid",
                "sample_time_s": inst["sample_time"] if k.startswith("measurement.") else self.__host.time_s, "sample_window_s": None,
                "synthetic": slots[k]["origin"] == "derived", "reason": "No configured value or available synthetic result." if values[k] is None else None} for k in state_ids}
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = _omnis_hashlib.sha256(_omnis_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = self.__principal, instance_id, request_id
            if key in host._requests:
                old_digest, response = host._requests[key]
                return _omnis_copy.deepcopy(response) if digest == old_digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = digest, _omnis_copy.deepcopy(response)
            host._logs[self.__principal].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id, "response": _omnis_copy.deepcopy(response)})
            return response

    def _checked(self, instance_id, inst, cap, args, request_id):
        host = self.__host
        if cap in METROHM_OMNIS_TEMPLATE_CAPABILITIES:  # additive public-data extension; legacy capabilities unchanged
            return _omnis_template_module().invoke(self, host, instance_id, inst, cap, args, request_id,
                                                   evaluator=self._visible({"visibility": "debug"}))
        fail = lambda code, status="rejected": self._error(instance_id, code, request_id, status)
        caps = host._metadata["asset_types"][METROHM_OMNIS_TYPE]["capabilities"]
        if cap not in caps:
            return fail("UNKNOWN_CAPABILITY")
        if not _omnis_matches(caps[cap]["input_schema"], args):
            return fail("INVALID_ARGUMENT")
        idle_caps = ("slot.set_connection", "dosing.configure_cylinder", "titration.configure_titrant", "titration.configure_method", "dosing.set_rate", "titration.configure_stability", "titration.start")
        if cap in idle_caps and inst["running"]:
            return fail("BUSY")
        if cap in ("dosing.configure_cylinder", "titration.configure_titrant", "titration.configure_method", "titration.start") and inst["steps"] > 0:
            return fail("PRECONDITION_FAILED")
        if cap == "dosing.configure_cylinder" and inst["method"] and inst["method"]["maximum_volume_m3"] > args["volume_m3"]:
            return fail("PRECONDITION_FAILED")
        if cap == "titration.configure_method":
            q = inst["quantum"]
            if q is None:
                return fail("PRECONDITION_FAILED")
            if (args["mode"] == "endpoint") != (args["endpoint_potential_v"] is not None):
                return fail("INVALID_ARGUMENT")
            if args["dose_increment_m3"] > args["maximum_volume_m3"] or args["maximum_volume_m3"] > inst["cylinder"]:
                return fail("INVALID_ARGUMENT")
            for key in ("dose_increment_m3", "maximum_volume_m3"):
                steps = args[key] / q
                if not _omnis_finite(steps) or steps < 1 or abs(steps - round(steps)) > 1e-7:
                    return fail("INVALID_ARGUMENT")
        if cap == "stirring.set_speed" and inst["running"] and args["angular_speed_rad_s"] == 0:
            return fail("PRECONDITION_FAILED")
        if cap == "titration.start":
            if not inst["power"] or not all(inst["connections"].values()) or any(inst[k] is None for k in ("cylinder", "rate", "concentration", "method", "hold", "tolerance")) or inst["speed"] <= 0 or inst["truth"]["volume_m3"] is None:
                return fail("PRECONDITION_FAILED")
            try:
                period = inst["quantum"] / inst["rate"]
                response = host._potential(inst)
                if not _omnis_finite(period) or period <= 0 or host.time_s + period <= host.time_s or host.time_s + inst["hold"] <= host.time_s or not all(_omnis_finite(x) for x in response):
                    return fail("INVALID_ARGUMENT")
            except (ValueError, OverflowError, ZeroDivisionError):
                return fail("INVALID_ARGUMENT")
        if cap == "titration.calculate_content":
            if inst["endpoint"] is None or inst["running"]:
                return fail("PRECONDITION_FAILED")
            try:
                content = inst["endpoint"] * inst["concentration"] * args["stoichiometric_ratio"] / args["sample_volume_m3"]
            except (ValueError, OverflowError, ZeroDivisionError):
                return fail("INVALID_ARGUMENT")
            if not _omnis_finite(content):
                return fail("INVALID_ARGUMENT")
        if cap == "titration.read_curve":
            return self._envelope(instance_id, request_id, result={"samples": inst["curve"], "endpoint_volume_m3": inst["endpoint"], "status": inst["status"], "synthetic": True})
        candidate, result = _omnis_copy.deepcopy(inst), dict(args)
        if cap == "power.set":
            candidate["power"] = args["enabled"]
            if not args["enabled"] and candidate["running"]:
                host._complete(candidate, "stopped")
                candidate["stable"] = False
        elif cap == "slot.set_connection":
            candidate["connections"][args["port_id"]] = args["connection_id"]
        elif cap == "dosing.configure_cylinder":
            candidate["cylinder"], candidate["quantum"] = args["volume_m3"], args["volume_m3"] / METROHM_OMNIS_STEPS_PER_CYLINDER
        elif cap == "titration.configure_titrant":
            candidate["concentration"] = args["concentration_mol_m3"]
        elif cap == "titration.configure_method":
            candidate["method"] = dict(args)
        elif cap == "dosing.set_rate":
            candidate["rate"] = args["rate_m3_s"]
        elif cap == "titration.configure_stability":
            candidate["hold"], candidate["tolerance"] = args["hold_s"], args["tolerance_v"]
        elif cap == "stirring.set_speed":
            candidate["speed"] = args["angular_speed_rad_s"]
        elif cap == "titration.start":
            host._reset_run(candidate)
            candidate["running"], candidate["status"], candidate["phase"] = True, "running", "settling"
            candidate["phase_start"], candidate["event_time"] = host.time_s, host.time_s + candidate["hold"]
            result = {"status": candidate["status"], "synthetic": True}
        elif cap == "titration.stop":
            if candidate["running"]:
                host._complete(candidate, "stopped")
                candidate["stable"] = False
            result = {"status": candidate["status"], "synthetic": True}
        elif cap == "titration.calculate_content":
            candidate["content"] = content
            result = {"content_mol_m3": content, "synthetic": True}
        if host.scene_adapter and host._scene(candidate) != host._scene(inst):
            try:
                host.scene_adapter(instance_id, host._scene(candidate))
            except Exception:
                return fail("DEVICE_ERROR", "failed")
        if candidate != inst:
            candidate["revision"] += 1
            host._instances[instance_id] = candidate
        return self._envelope(instance_id, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slots = self.__host._metadata["asset_types"][METROHM_OMNIS_TYPE]["state_slots"]
            if not isinstance(state_id, str) or state_id not in slots or not self._visible(slots[state_id]) or slots[state_id]["write_via"] is None:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slots[state_id]["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={**route["fixed_arguments"], route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return _omnis_copy.deepcopy(self.__host._logs[self.__principal])


def write_metrohm_omnis_runtime_reports(output_directory):
    import datetime
    import importlib.util
    import io
    import pathlib
    import platform
    import unittest
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("test_basic40_measure_omnis", root / "tests/test_basic40_measure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(module.MetrohmOmnisRuntimeTests))
    metadata = export_metrohm_omnis_contract_metadata()
    report = {"catalog_id": "CHEM-101", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "runtime": "standard-library Python; synthetic fixtures; no Blender"},
        "method": "unittest: all 12 APIs and schemas/effects; synthetic endpoint and dynamic titration, dose quantization, stable hold, content, partial stop, host time partition equivalence, range rejection, atomic failure, scoped visibility, request deduplication, instance isolation and scene callbacks",
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "result": "passed" if result.wasSuccessful() else "failed",
        "passed_capabilities": sorted(metadata["asset_types"][METROHM_OMNIS_TYPE]["capabilities"]) if result.wasSuccessful() else [],
        "output": stream.getvalue(), "not_run": ["real_hardware", "robot_execution", "contact_solver", "Blender_evaluated_pose", "process_filesystem_security_isolation", "manufacturer_calibration_accuracy", "redox_complexometric_weak_acid_response", "real_response_noise_fault_probability"],
        "scene_scope": "Only snapshot and recording/failing callbacks tested; parent provides evaluated Blender scene verification separately.", "synthetic": True}
    directory = pathlib.Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (("runtime_validation.json", report), ("runtime_metadata.json", metadata)):
        (directory / name).write_text(_omnis_json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
# END CHEM-101 METROHM OMNIS APPEND-ONLY RUNTIME


# CHEM-101 append-only numeric-domain guards and contract mechanism declarations.
_omnis_export_base = export_metrohm_omnis_contract_metadata


def export_metrohm_omnis_contract_metadata():
    metadata = _omnis_export_base()
    params = metadata["asset_types"][METROHM_OMNIS_TYPE]["parameters"]
    def props(*names):
        result = {name: _omnis_copy.deepcopy(params[name]) for name in names}
        for prop in result.values():
            prop["evidence"] = {"catalog": "documented", "engineering_assumption": "assumption"}.get(prop["evidence"], prop["evidence"])
        return result
    metadata["mechanisms"] = {
        "ideal_acid_base_potentiometry": {
            "component_ids": ["electrode_slot", "titration_cell_slot", "dosing_module"],
            "state_ids": ["truth.acid_moles", "truth.volume_m3", "truth.temperature_k", "truth.reference_ph", "truth.reference_potential_v", "titrant.concentration_mol_m3", "dosing.delivered_volume_m3", "measurement.ph", "measurement.potential_v"],
            "meaning_zh": "engineering_assumption：单元强酸强碱电中性/水离解理想响应及Nernst电位；所有输出synthetic。",
            "implementation": "numerical", "equations": metadata["engineering_equations"][:2],
            "parameters": props("water_ionic_product", "potential_range_v", "electrode_response_time", "sample_noise", "calibration_accuracy"),
            "source_ids": ["runtime_code", "workbook_parameters"],
            "limitations": ["固定Kw的25°C近似，仅Nernst斜率含显式温度；无活度、弱酸、氧化还原、络合和真实动力学。", "严格超过整机±2.4V时输出null并停止，不做限幅伪装读数；不声称厂家准确度。"]},
        "quantized_dosing_and_stirring": {
            "component_ids": ["dosing_module", "cylinder_slot", "piston_proxy", "stir_bar"],
            "state_ids": ["dosing.cylinder_volume_m3", "dosing.quantum_m3", "dosing.rate_m3_s", "dosing.delivered_volume_m3", "dosing.piston_fraction", "stirring.angular_speed_rad_s", "stirring.angle_rad"],
            "meaning_zh": "engineering_assumption：表列每缸100000步作为整数体积格点；宿主时钟推进恒定速率加液并积分搅拌角。",
            "implementation": "numerical", "equations": [metadata["engineering_equations"][2]],
            "parameters": props("cylinder_volumes_m3", "steps_per_cylinder", "maximum_stirring_speed_rad_s", "manufacturer_max_flow_rate", "mixing_time_constant", "cylinder_actual_stroke"),
            "source_ids": ["runtime_code", "workbook_parameters"],
            "limitations": ["单缸实验无补液和实际物质转移；host_load_sample仅显式重置合成实验。", "活塞占比为示意模型映射，不代表实际缸行程；搅拌未模拟磁耦合/失步/液体动力学。"]},
        "stable_curve_and_endpoint": {
            "component_ids": ["electrode_slot", "titration_cell_slot", "dosing_module"],
            "state_ids": ["method.mode", "method.dose_increment_m3", "method.maximum_volume_m3", "method.endpoint_potential_v", "stability.hold_s", "stability.tolerance_v", "stability.stable", "titration.curve", "titration.endpoint_volume_m3", "titration.content_mol_m3"],
            "meaning_zh": "engineering_assumption：停液后的显式稳定窗口采点，固定目标电位线性插值或动态有限差分峰值估算终点。",
            "implementation": "numerical", "equations": ["stable=(max(E over hold window)-min(E over hold window)<=user tolerance); ideal instantaneous mixing keeps E constant while dosing is stopped", *metadata["engineering_equations"][3:]],
            "parameters": props("dynamic_target_delta_v", "dynamic_peak_fraction", "max_curve_points", "fault_probability"),
            "source_ids": ["runtime_code", "workbook_parameters"],
            "limitations": ["动态方法为可复现教学算法，未复现Metrohm固件；终点可能因剂量上限/点数上限未找到，此时为null。", "结果仅基于代理可观察曲线；计算含量使用调用者明确给出的样品体积、滴定剂浓度和计量比。"]}}
    metadata["limitations"].append("Finite numeric representability is checked at host boundaries; changing cylinder preserves the integer-step consistency of an already configured method.")
    return metadata


class MetrohmOmnisRuntime(MetrohmOmnisRuntime):
    """Append-only hardening of the CHEM-101 host; public signature unchanged."""
    def host_step(self, time_s):
        with self._lock:
            if not _omnis_finite(time_s) or time_s < self.time_s:
                raise ValueError("Finite monotonic absolute host time required")
            delta = time_s - self.time_s
            if not _omnis_finite(delta) or any(inst["power"] and not _omnis_finite(inst["speed"] * delta) for inst in self._instances.values()):
                raise ValueError("Step rotation exceeds finite numeric domain")
            return super().host_step(time_s)


class MetrohmOmnisSession(MetrohmOmnisSession):
    """Append-only cylinder/method consistency guard; identity policy unchanged."""
    def _checked(self, instance_id, inst, cap, args, request_id):
        if cap == "dosing.configure_cylinder" and set(args) == {"volume_m3"} and _omnis_finite(args["volume_m3"]) and args["volume_m3"] in METROHM_OMNIS_CYLINDERS_M3 and inst["method"] is not None:
            quantum = args["volume_m3"] / METROHM_OMNIS_STEPS_PER_CYLINDER
            for key in ("dose_increment_m3", "maximum_volume_m3"):
                steps = inst["method"][key] / quantum
                if steps < 1 or abs(steps - round(steps)) > 1e-7:
                    return self._error(instance_id, "PRECONDITION_FAILED", request_id)
        return super()._checked(instance_id, inst, cap, args, request_id)

# BEGIN CHEM-068 MAGRITEK SPINSOLVE 80 APPEND-ONLY RUNTIME
import copy as _sp80_copy
import cmath as _sp80_cmath
import hashlib as _sp80_hashlib
import json as _sp80_json
import math as _sp80_math
import threading as _sp80_threading
from datetime import datetime as _sp80_datetime, timezone as _sp80_timezone
from pathlib import Path as _sp80_Path

MAGRIΤEK_SPINSOLVE80_RESERVED = None  # Names below use ASCII identifiers.
SPINSOLVE80_TYPE = "instrument.magritek_spinsolve80"
SPINSOLVE80_SCENE_KEYS = ("power.enabled", "slot.sample_tube.connection_id", "sample.insertion_fraction", "acquisition.running", "acquisition.progress", "display.text")


def _sp80_number(value):
    try:
        return type(value) in (int, float) and _sp80_math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _sp80_num(unit, lo=None, hi=None, *, integer=False, positive=False, nullable=False):
    result = {"type": "integer" if integer else "number", "x-unit": unit}
    if lo is not None:
        result["minimum"] = lo
    if hi is not None:
        result["maximum"] = hi
    if positive:
        result["exclusiveMinimum"] = 0
    return {"anyOf": [result, {"type": "null"}]} if nullable else result


def _sp80_obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _sp80_nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def _sp80_matches(schema, value):
    if "anyOf" in schema:
        return any(_sp80_matches(s, value) for s in schema["anyOf"])
    typ = schema.get("type")
    if typ in ("number", "integer"):
        if not _sp80_number(value) or (typ == "integer" and type(value) is not int):
            return False
    elif typ == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]):
            return False
        return all(_sp80_matches(s, value[k]) for k, s in schema["properties"].items())
    elif typ == "array":
        return isinstance(value, list) and schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100000) and all(_sp80_matches(schema["items"], v) for v in value)
    elif typ == "boolean" and type(value) is not bool:
        return False
    elif typ == "string" and (not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 100000)):
        return False
    elif typ == "null" and value is not None:
        return False
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    return all(value >= schema[k] if k == "minimum" else value <= schema[k] if k == "maximum" else value > schema[k] for k in ("minimum", "maximum", "exclusiveMinimum") if k in schema)


def export_magritek_spinsolve80_contract_metadata():
    """Contract-ready type metadata, with no runtime sample values or fixtures."""
    boolean, synthetic = {"type": "boolean"}, {"type": "boolean", "const": True}
    identifier = {"type": "string", "minLength": 1, "maxLength": 128}
    nucleus = {"type": "string", "enum": ["1H", "13C"]}
    status = {"type": "string", "enum": ["idle", "running", "completed", "stopped"]}
    method = {"nucleus": nucleus, "pulse_sequence": {"type": "string", "enum": ["single_pulse"]},
        "scans": _sp80_num("1", 1, 4096, integer=True), "relaxation_delay_s": _sp80_num("s", 0, 86400),
        "acquisition_time_s": _sp80_num("s", 0.001, 60), "points": _sp80_num("1", 32, 512, integer=True),
        "decoupling": boolean, "gradient_enabled": boolean}
    fid = {"type": "array", "maxItems": 512, "items": _sp80_obj({"time_s": _sp80_num("s", 0), "real": _sp80_num("1"), "imaginary": _sp80_num("1"), "synthetic": synthetic})}
    spectrum = {"type": "array", "maxItems": 512, "items": _sp80_obj({"frequency_offset_hz": _sp80_num("Hz"), "real": _sp80_num("1"), "imaginary": _sp80_num("1"), "amplitude": _sp80_num("1", 0), "synthetic": synthetic})}
    resonance = _sp80_obj({"frequency_offset_hz": _sp80_num("Hz", -1e6, 1e6), "amplitude": _sp80_num("1", 0, 1e6), "t2_s": _sp80_num("s", 1e-6, 1e6)})
    peaks = {"type": "array", "maxItems": 32, "items": resonance}
    fixture = _sp80_obj({"1H": peaks, "13C": peaks})
    registered = {"type": "array", "maxItems": 64, "items": _sp80_obj({"sample_id": identifier, "signals": fixture})}
    slots = {}
    def slot(sid, schema, unit, origin, text, components, *, visibility="agent", write=None, sampling="instant"):
        slots[sid] = {"component_ids": components, "value_schema": schema, "unit": unit, "reference_conditions": None,
            "meaning_zh": text, "origin": origin, "access": "read_write" if write else "read_only", "visibility": visibility,
            "read_binding": {"backend": "process", "artifact_id": "runtime_code", "accessor": "MagritekSpinsolve80Session.read(instance_id=..., state_ids=['" + sid + "'])"},
            "write_via": write, "sampling": {"mode": sampling, "time_domain": "simulation", "window_description": "已完成扫描的无噪声平均信号。" if sampling == "series" else None}, "source_ids": ["runtime_code"]}
    slot("power.enabled", boolean, "1", "setpoint", "逻辑电源；关闭时停止采集并保留已完成扫描。", ["housing"], write={"capability_id": "power.set", "argument": "enabled", "fixed_arguments": {}})
    slot("slot.sample_tube.connection_id", _sp80_nullable(identifier), "1", "scene", "唯一管槽的宿主注册样品标识；不暴露样品信号真值。", ["sample_port", "sample_tube"])
    slot("sample.insertion_fraction", _sp80_num("1", 0, 1), "1", "scene", "放样为1、移除为0；几何插入深度另以假设声明，不是厂家行程。", ["sample_tube"])
    for key, schema in method.items():
        slot("method." + key, _sp80_nullable(schema), schema.get("x-unit", "1"), "setpoint", "用户显式合成采集设置；资源范围为engineering_assumption，不是厂家规格。", ["magnet_module"])
    slot("method.carrier_frequency_hz", _sp80_num("Hz", nullable=True), "Hz", "derived", "1H载频为清单80 MHz；13C载频未知为null，合成横轴始终用Hz偏移。", ["magnet_module"])
    for sid, schema, unit, text in (("acquisition.status", status, "1", "采集状态；完成不代表真实仪器采集。"), ("acquisition.running", boolean, "1", "宿主时间推进的合成采集是否运行。"), ("acquisition.progress", _sp80_num("1", 0, 1), "1", "本次采集计划的时间完成比例。"), ("acquisition.completed_scans", _sp80_num("1", 0, 4096, integer=True), "1", "已完整结束的扫描数量；未完成扫描不输出伪完整数据。"), ("acquisition.elapsed_s", _sp80_num("s", 0), "s", "宿主推进后的本次采集耗时。"), ("measurement.sample_time_s", _sp80_num("s", 0, nullable=True), "s", "最近完整扫描的结束时刻。"), ("measurement.synthetic", synthetic, "1", "FID、1D频谱与积分全部为synthetic。")):
        slot(sid, schema, unit, "derived", text, ["magnet_module"])
    slot("measurement.fid", fid, "1", "derived", "阻尼复正弦叠加的合成FID；信号幅值无量纲。", ["magnet_module"], sampling="series")
    slot("measurement.spectrum", spectrum, "1", "derived", "对合成FID执行归一化DFT的1D频谱；频率偏移单位Hz，幅值无量纲。", ["magnet_module"], sampling="series")
    slot("measurement.integral_hz", _sp80_num("Hz", 0, nullable=True), "Hz", "derived", "按指定频带对幅度做分段线性积分；不是核数、浓度或定量核磁结果。", ["control_panel"])
    slot("display.text", {"type": "string"}, "1", "derived", "状态标签，始终带SYNTHETIC；未新增厂商自带屏幕主张。", ["control_panel"])
    slot("truth.registered_samples", registered, "1", "process_truth", "宿主专有合成样品表；代理describe/read/invoke/logs/scene均不含这些真值。", ["sample_port"], visibility="debug", sampling="static")
    reset = ["acquisition.status", "acquisition.running", "acquisition.progress", "acquisition.completed_scans", "acquisition.elapsed_s", "measurement.sample_time_s", "measurement.fid", "measurement.spectrum", "measurement.integral_hz", "display.text"]
    example = {"nucleus": "1H", "pulse_sequence": "single_pulse", "scans": 2, "relaxation_delay_s": 1, "acquisition_time_s": 0.5, "points": 64, "decoupling": False, "gradient_enabled": False}
    defs = {
        "power.set": ("device_command", {"enabled": boolean}, {"enabled": boolean}, {"enabled": True}, ["housing", "control_panel"], ["power.enabled", "acquisition.running", "acquisition.status", "display.text"], "设置逻辑电源，关闭保留已完成扫描。", "实例存在。"),
        "sample.insert": ("configuration", {"sample_id": identifier}, {"sample_id": identifier, "insertion_fraction": _sp80_num("1", 0, 1)}, {"sample_id": "sample_01"}, ["sample_port", "sample_tube"], ["slot.sample_tube.connection_id", "sample.insertion_fraction"] + reset, "将已由宿主注册的样品放入唯一逻辑管槽，示意场景同步由宿主适配器执行。", "槽为空、未采集、样品在本实例宿主注册表内；不执行真实机械插入。"),
        "sample.remove": ("configuration", {}, {"sample_id": _sp80_nullable(identifier), "insertion_fraction": _sp80_num("1", 0, 1)}, {}, ["sample_port", "sample_tube"], ["slot.sample_tube.connection_id", "sample.insertion_fraction"] + reset, "清空逻辑槽及当前运行结果；不从世界删除样品。", "未采集。"),
        "acquisition.configure": ("configuration", method, method, example, ["magnet_module", "control_panel"], ["method." + k for k in method] + ["method.carrier_frequency_hz"] + reset, "显式配置1H/13C单脉冲合成采集；解耦或梯度启用明确拒绝。", "未采集；所有数值有限且满足计算资源范围；decoupling和gradient_enabled须为false。"),
        "acquisition.start": ("device_command", {}, {"status": status, "synthetic": synthetic}, {}, ["magnet_module", "control_panel"], reset, "启动采集计划；调用不推进时间，也不立即生成FID。", "逻辑供电、已插样、完整方法齐备、未运行；选定核的宿主信号存在且位于Nyquist范围内。"),
        "acquisition.stop": ("device_command", {}, {"status": status, "completed_scans": _sp80_num("1", 0, 4096, integer=True), "synthetic": synthetic}, {}, ["magnet_module", "control_panel"], ["acquisition.running", "acquisition.status", "display.text"], "停止采集，保留已完整扫描及耗时；未完成扫描丢弃，不伪称完成。", "实例存在；未运行时为无操作。"),
        "nmr.read_fid": ("query", {}, {"samples": fid, "completed_scans": _sp80_num("1", 0, 4096, integer=True), "status": status, "synthetic": synthetic}, {}, ["magnet_module"], [], "读取已有合成FID，不推进时间、不读取隐藏样品参数。", "实例存在；尚无完整扫描时数组为空。"),
        "nmr.read_spectrum": ("query", {}, {"samples": spectrum, "nucleus": _sp80_nullable(nucleus), "carrier_frequency_hz": _sp80_num("Hz", nullable=True), "status": status, "synthetic": synthetic}, {}, ["magnet_module"], [], "读取已有1D合成DFT谱；不提供2D谱或结构约束。", "实例存在；尚无完整扫描时数组为空。"),
        "nmr.integrate": ("analysis", {"frequency_min_hz": _sp80_num("Hz"), "frequency_max_hz": _sp80_num("Hz")}, {"integral_hz": _sp80_num("Hz", 0), "synthetic": synthetic}, {"frequency_min_hz": -10, "frequency_max_hz": 10}, ["control_panel"], ["measurement.integral_hz"], "对已观察到的合成频谱幅度执行梯形积分，不使用样品真值。", "已有完整扫描，积分下限小于上限，且整个区间在已采谱范围内。")}
    caps = {}
    error_texts = {"UNKNOWN_INSTANCE": "实例不可用。", "UNKNOWN_CAPABILITY": "能力不可用。", "INVALID_ARGUMENT": "参数结构、类型或有限数值范围无效。", "ACCESS_DENIED": "访问策略拒绝。", "PRECONDITION_FAILED": "样品、方法或运行状态前提未满足。", "BUSY": "采集正在运行。", "UNSUPPORTED": "当前模型未实现所选解耦或梯度功能。", "REQUEST_CONFLICT": "同一请求标识被用于不同参数。", "DEVICE_ERROR": "场景适配器失败；过程未提交，外部场景可能已有部分变化。"}
    for name, (kind, inp, out, ex, components, effects, description, pre) in defs.items():
        scene = any(k in SPINSOLVE80_SCENE_KEYS for k in effects)
        caps[name] = {"function_zh": description, "kind": kind, "visibility": "agent", "component_ids": components, "availability": "implemented",
            "input_schema": _sp80_obj(inp), "output_schema": _sp80_obj(out),
            "implementation": {"process": "implemented", "scene": "declared" if scene else "not_applicable", "robot": "absent", "hardware": "absent", "host": "implemented"},
            "binding": {"backend": "process", "artifact_id": "runtime_code", "entrypoint": "MagritekSpinsolve80Session.invoke", "invocation": "session.invoke(instance_id='spinsolve_01', capability_id='" + name + "', arguments=" + repr(ex) + ", request_id='unique_request')"},
            "preconditions": [{"description_zh": pre, "state_ids": [], "enforced_by": "runtime", "check": "MagritekSpinsolve80Session._checked validates schema and preconditions before committing."}],
            "state_effects": [{"state_id": k, "description_zh": description, "timing": "immediate"} for k in dict.fromkeys(effects)],
            "observes": [k for k in effects if k != "measurement.synthetic"],
            "model_effects": {"mode": "host_required" if scene else "none", "binding_ids": [], "description_zh": "可选scene_adapter接收六个允许的场景键；实际几何求值另验。" if scene else "纯查询或数值分析，无可见几何变化。"},
            "execution": {"mode": "synchronous", "time_domain": "simulation", "completion_criterion": "命令同步提交；acquisition.start只表示计划启动，完成须宿主推进至acquisition.status=completed。", "completion_state_ids": ["acquisition.status"] if name.startswith("acquisition.") else [], "timeout_s": None, "cancellation": "unsupported", "retry": "same_request_id", "resources": ["instance.acquisition"], "partial_failure_zh": "拒绝不改过程；scene_adapter失败可能留下外部场景部分效果。acquisition.stop保留已完成扫描。"},
            "errors": [{"code": code, "meaning_zh": text, "side_effects": "unknown" if code == "DEVICE_ERROR" else "none"} for code, text in error_texts.items()], "example_arguments": [ex], "verification": []}
    def prop(value, unit, evidence, text):
        return {"value": value, "unit": unit, "meaning_zh": text, "evidence": evidence, "source_ids": ["workbook_parameters"] if evidence == "documented" else ["runtime_code"], "uncertainty": None}
    parameters = {"proton_carrier_frequency_hz": prop(80e6, "Hz", "documented", "清单1H频率80MHz；不代表部件工作响应。"),
        "carbon_carrier_frequency_hz": prop(None, "Hz", "unknown", "清单未给13C载频，保持null。"), "fwhm_specification_hz": prop(0.4, "Hz", "documented", "Classic整机半高线宽严格小于0.4Hz；不用于生成合成T2。"),
        "tail_linewidth_specification_hz": prop(16, "Hz", "documented", "Classic整机0.55%峰高线宽严格小于16Hz；不作为合成响应参数。"), "sensitivity_ratio_lower_bound": prop(200, "1", "documented", "整机双通道1H灵敏度>200:1，条件1%乙苯；未实现噪声与灵敏度验证。"),
        "max_points": prop(512, "1", "assumption", "engineering_assumption：本Python DFT计算资源上限，不是厂商规格。")}
    for name, unit in (("manufacturer_pulse_duration_s", "s"), ("manufacturer_t1_s", "s"), ("manufacturer_t2_s", "s"), ("noise_amplitude", "1"), ("calibration_accuracy_hz", "Hz"), ("fault_probability", "1")):
        parameters[name] = prop(None, unit, "unknown", "清单未提供真实响应证据；保持null。")
    limitations = ["所有FID、1D频谱与数值积分均为synthetic；工程假设教学模型不能声称复现Spinsolve响应、Classic线宽、灵敏度或真实测量。", "只实现1H/13C单脉冲1D、宿主显式扫描计时与无噪声平均；2D、耦合多重峰自动求解、解耦、梯度、结构约束、定量核磁、真实校准与故障概率未实现。", "每扫描耗时=用户弛豫延迟+用户采集时间；不模拟T1饱和恢复、脉冲时宽、接收器死区、磁场漂移或厂商固件。", "幅度任意且无量纲，积分单位Hz；积分不是浓度或核数。13C载频未知，不输出ppm横轴。", "样品槽仅逻辑关联；插入占比为示意映射，不执行机器人插入、接触或真实磁场作用。", "注册样品、共同绝对时钟由可信宿主管理；公开session过滤是API边界，不是同进程Python或文件系统沙箱。请求幂等缓存仅在内存中。", "可选scene_adapter失败时过程不提交；外部场景可能已部分修改，需宿主协调恢复。"]
    mechanisms = {"synthetic_nmr_signal": {"component_ids": ["magnet_module", "sample_port"], "state_ids": ["truth.registered_samples", "method.nucleus", "method.acquisition_time_s", "method.points", "measurement.fid", "measurement.spectrum", "measurement.integral_hz"], "meaning_zh": "engineering_assumption：阻尼复正弦合成FID、归一化DFT和观测频带积分；全部输出synthetic。", "implementation": "numerical", "equations": ["FID[n]=sum(A_j*exp(-t_n/T2_j)*exp(i*2*pi*f_j*t_n)); t_n=n*T_acq/N", "S[k]=sum(FID[n]*exp(-i*2*pi*k*n/N))/N; f_k=(k-floor(N/2))/T_acq", "integral=piecewise-linear trapezoid integral of abs(S) over explicit observed frequency interval"], "parameters": parameters, "source_ids": ["runtime_code", "workbook_parameters"], "limitations": limitations[:4]},
        "host_timed_scanning": {"component_ids": ["magnet_module", "control_panel"], "state_ids": ["method.scans", "method.relaxation_delay_s", "method.acquisition_time_s", "acquisition.elapsed_s", "acquisition.completed_scans", "acquisition.progress", "acquisition.status"], "meaning_zh": "engineering_assumption：由共享宿主绝对时钟推进离散完整扫描。", "implementation": "numerical", "equations": ["cycle=T_relax+T_acq; completed_scans=min(scans,floor((host_time-start_time)/cycle)); progress=min(1,elapsed/(scans*cycle))"], "parameters": {}, "source_ids": ["runtime_code"], "limitations": ["无自动世界推进；read/describe/query不推进时钟。", "未完成扫描不产生伪完整谱；取消保留已完成扫描。"]}}
    return {"asset_types": {SPINSOLVE80_TYPE: {"catalog_id": "CHEM-068", "model": "Magritek | Spinsolve 80 Carbon", "asset_version": "1.0.0", "state_slots": slots, "capabilities": caps, "parameters": parameters}}, "mechanisms": mechanisms, "scene_snapshot_keys": list(SPINSOLVE80_SCENE_KEYS), "limitations": limitations,
        "host_capabilities": {"sim.host_step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "MagritekSpinsolve80Runtime.host_step(time_s)", "state_effects": reset}, "sim.register_sample": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "MagritekSpinsolve80Runtime.register_sample(instance_id, sample_id, signals)", "state_effects": ["truth.registered_samples"]}}, "host_sample_schema": fixture}


class MagritekSpinsolve80Runtime:
    """Trusted host. Distribute a scoped Session to agents, never this object."""
    def __init__(self, *, scene_adapter=None):
        self.time_s, self.scene_adapter = 0.0, scene_adapter
        self._instances, self._principals, self._requests, self._logs = {}, {}, {}, {}
        self._lock = _sp80_threading.RLock()
        self._metadata = export_magritek_spinsolve80_contract_metadata()
        self.register_principal("__public_agent__", visibility="agent")
        self._agent = self.session("__public_agent__")

    def create_instance(self, instance_id, asset_type_id=SPINSOLVE80_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not 1 <= len(instance_id) <= 128 or instance_id in self._instances or asset_type_id != SPINSOLVE80_TYPE:
                raise ValueError("Unique instance id and supported asset type required")
            inst = {"revision": 0, "power": False, "sample_id": None, "method": None, "samples": {}}
            self._reset(inst)
            if self.scene_adapter:
                self.scene_adapter(instance_id, self._scene(inst))
            self._instances[instance_id] = inst

    @staticmethod
    def _reset(inst):
        inst.update({"running": False, "status": "idle", "progress": 0.0, "completed_scans": 0, "elapsed": 0.0, "started_at": None, "sample_time": None, "fid": [], "spectrum": [], "integral": None})

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Invalid trusted principal registration")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(i, str) for i in instance_ids)):
                raise ValueError("Invalid instance scope")
            scope = None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope <= self._instances.keys():
                raise ValueError("Unknown instance scope")
            self._principals[principal_id], self._logs[principal_id] = (visibility, scope), []

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Trusted registration required")
        return MagritekSpinsolve80Session(self, principal_id)

    def describe(self, **kwargs):
        return self._agent.describe(**kwargs)

    def read(self, **kwargs):
        return self._agent.read(**kwargs)

    def invoke(self, **kwargs):
        return self._agent.invoke(**kwargs)

    def write(self, **kwargs):
        return self._agent.write(**kwargs)

    def describe_host(self):
        return _sp80_copy.deepcopy(self._metadata["host_capabilities"])

    def register_sample(self, instance_id, sample_id, signals):
        with self._lock:
            if instance_id not in self._instances or not isinstance(sample_id, str) or not 1 <= len(sample_id) <= 128 or not _sp80_matches(self._metadata["host_sample_schema"], signals):
                raise ValueError("Invalid instance, sample identifier or finite synthetic signal schema")
            inst = self._instances[instance_id]
            if sample_id in inst["samples"] or len(inst["samples"]) >= 64 or inst["running"]:
                raise ValueError("Sample registration unavailable")
            inst["samples"][sample_id] = _sp80_copy.deepcopy(signals)
            inst["revision"] += 1

    @staticmethod
    def _display(inst):
        return "SYNTHETIC " + ("OFF" if not inst["power"] else inst["status"].upper()) + " %d SCANS" % inst["completed_scans"]

    @classmethod
    def _scene(cls, inst):
        return {"power.enabled": inst["power"], "slot.sample_tube.connection_id": inst["sample_id"], "sample.insertion_fraction": 1.0 if inst["sample_id"] is not None else 0.0, "acquisition.running": inst["running"], "acquisition.progress": inst["progress"], "display.text": cls._display(inst)}

    def scene_snapshot(self, instance_id):
        with self._lock:
            return _sp80_copy.deepcopy(self._scene(self._instances[instance_id]))

    def apply_scene(self, instance_id):
        with self._lock:
            if self.scene_adapter is None:
                raise RuntimeError("No scene_adapter is configured")
            snapshot = self.scene_snapshot(instance_id)
            self.scene_adapter(instance_id, _sp80_copy.deepcopy(snapshot))
            return snapshot

    @classmethod
    def _values(cls, inst):
        values = cls._scene(inst)
        config = inst["method"] or {}
        for key in ("nucleus", "pulse_sequence", "scans", "relaxation_delay_s", "acquisition_time_s", "points", "decoupling", "gradient_enabled"):
            values["method." + key] = config.get(key)
        values.update({"method.carrier_frequency_hz": 80e6 if config.get("nucleus") == "1H" else None, "acquisition.status": inst["status"], "acquisition.completed_scans": inst["completed_scans"], "acquisition.elapsed_s": inst["elapsed"], "measurement.sample_time_s": inst["sample_time"], "measurement.synthetic": True, "measurement.fid": inst["fid"], "measurement.spectrum": inst["spectrum"], "measurement.integral_hz": inst["integral"], "truth.registered_samples": [{"sample_id": k, "signals": v} for k, v in sorted(inst["samples"].items()) if k not in inst.get("template_samples", {})]})
        return _sp80_copy.deepcopy(values)

    @staticmethod
    def _signal(inst):
        method = inst["method"]
        n, duration = method["points"], method["acquisition_time_s"]
        peaks = inst["samples"][inst["sample_id"]][method["nucleus"]]
        samples, fid = [], []
        for j in range(n):
            t = j * duration / n
            y = sum(p["amplitude"] * _sp80_cmath.exp(complex(-t / p["t2_s"], 2 * _sp80_math.pi * p["frequency_offset_hz"] * t)) for p in peaks)
            samples.append(y)
            fid.append({"time_s": t, "real": y.real, "imaginary": y.imag, "synthetic": True})
        spectrum = []
        for k in range(-(n // 2), n - n // 2):
            value = sum(samples[j] * _sp80_cmath.exp(-2j * _sp80_math.pi * k * j / n) for j in range(n)) / n
            spectrum.append({"frequency_offset_hz": k / duration, "real": value.real, "imaginary": value.imag, "amplitude": abs(value), "synthetic": True})
        return fid, spectrum

    def host_step(self, time_s):
        """Advance this host's registered instances to shared absolute SI time."""
        with self._lock:
            if not _sp80_number(time_s) or time_s < self.time_s:
                raise ValueError("Finite monotonic absolute host time required")
            if time_s == self.time_s:
                return
            staged = {}
            for instance_id, old in self._instances.items():
                if not old["running"]:
                    continue
                new = _sp80_copy.deepcopy(old)
                cfg = new["method"]
                cycle = cfg["relaxation_delay_s"] + cfg["acquisition_time_s"]
                total = cfg["scans"] * cycle
                elapsed = time_s - new["started_at"]
                if not _sp80_number(elapsed):
                    raise ValueError("Host interval is not numerically representable")
                new["elapsed"] = min(elapsed, total)
                new["progress"] = new["elapsed"] / total
                count = cfg["scans"] if elapsed >= total else min(cfg["scans"], int(elapsed / cycle))
                if count > old["completed_scans"]:
                    if not old["fid"]:
                        new["fid"], new["spectrum"] = self._signal(new)
                    new["completed_scans"] = count
                    new["sample_time"] = new["started_at"] + count * cycle
                    new["integral"] = None
                if count == cfg["scans"]:
                    new["running"], new["status"] = False, "completed"
                new["revision"] = old["revision"] + 1
                staged[instance_id] = new
            if self.scene_adapter:
                for iid, new in staged.items():
                    if self._scene(new) != self._scene(self._instances[iid]):
                        self.scene_adapter(iid, self._scene(new))
            self._instances.update(staged)
            self.time_s = float(time_s)


class MagritekSpinsolve80Session:
    """Permission-scoped API view; not a Python object-capability sandbox."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal = host, principal_id

    def _instance(self, instance_id):
        _, scope = self.__host._principals[self.__principal]
        return self.__host._instances.get(instance_id) if isinstance(instance_id, str) and (scope is None or instance_id in scope) else None

    def _visible(self, slot):
        return slot["visibility"] == "agent" or self.__host._principals[self.__principal][0] in ("debug", "evaluator")

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": "1.0.0", "instance_id": instance_id, "request_id": request_id, "operation_id": None, "status": status, "state_revision": inst["revision"] if inst else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s}, "result": _sp80_copy.deepcopy(result or {}), "observations": _sp80_copy.deepcopy(observations or {}), "error": error}

    def _error(self, instance_id, code, request_id=None):
        return self._envelope(instance_id, request_id, status="failed" if code == "DEVICE_ERROR" else "rejected", error={"code": code, "message": "Request unavailable under current arguments, state or access policy." if code != "DEVICE_ERROR" else "External scene synchronization failed; process not committed.", "retryable": False, "side_effects": "unknown" if code == "DEVICE_ERROR" else "none", "progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None) or (asset_type_id is not None and asset_type_id != SPINSOLVE80_TYPE):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            _, scope = self.__host._principals[self.__principal]
            if scope is not None and not scope:
                return self._error(instance_id, "ACCESS_DENIED")
            spec = _sp80_copy.deepcopy(self.__host._metadata["asset_types"][SPINSOLVE80_TYPE])
            spec["state_slots"] = {k: v for k, v in spec["state_slots"].items() if self._visible(v)}
            spec["capabilities"].update(_sp80_template_module().capability_metadata())  # template-backed sample queries (additive)
            return {"protocol_version": "1.0.0", "asset_type_id": SPINSOLVE80_TYPE, "instance_id": instance_id, "adapter": "basic40_runtime_measure.MagritekSpinsolve80Session", **spec, "limitations": _sp80_copy.deepcopy(self.__host._metadata["limitations"])}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = self.__host._metadata["asset_types"][SPINSOLVE80_TYPE]["state_slots"]
            if state_ids is None:
                state_ids = [k for k, s in slots.items() if self._visible(s)]
            if not isinstance(state_ids, list) or any(not isinstance(k, str) for k in state_ids) or len(set(state_ids)) != len(state_ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(k not in slots or not self._visible(slots[k]) for k in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            values = self.__host._values(inst)
            observations = {k: {"value": values[k], "unit": slots[k]["unit"], "quality": "unavailable" if values[k] is None else "estimated" if slots[k]["origin"] == "derived" else "valid", "sample_time_s": inst["sample_time"] if k.startswith("measurement.") else self.__host.time_s, "sample_window_s": None, "synthetic": slots[k]["origin"] == "derived", "reason": "No available configured or synthetic value." if values[k] is None else None} for k in state_ids}
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = _sp80_hashlib.sha256(_sp80_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = self.__principal, instance_id, request_id
            if key in host._requests:
                old_digest, response = host._requests[key]
                return _sp80_copy.deepcopy(response) if old_digest == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = digest, _sp80_copy.deepcopy(response)
            host._logs[self.__principal].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id, "response": _sp80_copy.deepcopy(response)})
            return response

    def _checked(self, instance_id, inst, cap, args, request_id):
        host = self.__host
        if cap in ("template.read", "nmr.read_template_spectrum"):  # additive template-backed queries (read-only)
            return _sp80_template_module().session_query(self, host, instance_id, inst, cap, args, request_id)
        definition = host._metadata["asset_types"][SPINSOLVE80_TYPE]["capabilities"].get(cap)
        if definition is None:
            return self._error(instance_id, "UNKNOWN_CAPABILITY", request_id)
        if not _sp80_matches(definition["input_schema"], args):
            return self._error(instance_id, "INVALID_ARGUMENT", request_id)
        if cap == "acquisition.configure" and (args["decoupling"] or args["gradient_enabled"]):
            return self._error(instance_id, "UNSUPPORTED", request_id)
        if inst["running"] and cap in ("sample.insert", "sample.remove", "acquisition.configure", "acquisition.start"):
            return self._error(instance_id, "BUSY", request_id)
        if cap == "sample.insert" and (inst["sample_id"] is not None or args["sample_id"] not in inst["samples"]):
            return self._error(instance_id, "PRECONDITION_FAILED", request_id)
        if cap == "acquisition.start":
            cfg = inst["method"]
            if not inst["power"] or inst["sample_id"] is None or cfg is None:
                return self._error(instance_id, "PRECONDITION_FAILED", request_id)
            signals = inst["samples"][inst["sample_id"]][cfg["nucleus"]]
            nyquist = cfg["points"] / cfg["acquisition_time_s"] / 2
            if not signals or any(abs(p["frequency_offset_hz"]) >= nyquist for p in signals) or host.time_s + cfg["scans"] * (cfg["relaxation_delay_s"] + cfg["acquisition_time_s"]) <= host.time_s:
                return self._error(instance_id, "PRECONDITION_FAILED", request_id)
        if cap == "nmr.integrate":
            a, b, spectrum = args["frequency_min_hz"], args["frequency_max_hz"], inst["spectrum"]
            if not spectrum or not spectrum[0]["frequency_offset_hz"] <= a < b <= spectrum[-1]["frequency_offset_hz"]:
                return self._error(instance_id, "PRECONDITION_FAILED", request_id)
        new = _sp80_copy.deepcopy(inst)
        if cap == "power.set":
            new["power"] = args["enabled"]
            if not new["power"] and new["running"]:
                new["running"], new["status"] = False, "stopped"
            result = {"enabled": new["power"]}
        elif cap in ("sample.insert", "sample.remove"):
            old_id = new["sample_id"]
            new["sample_id"] = args["sample_id"] if cap == "sample.insert" else None
            host._reset(new)
            result = {"sample_id": new["sample_id"] if cap == "sample.insert" else old_id, "insertion_fraction": 1.0 if new["sample_id"] is not None else 0.0}
        elif cap == "acquisition.configure":
            new["method"] = _sp80_copy.deepcopy(args)
            host._reset(new)
            result = _sp80_copy.deepcopy(args)
        elif cap == "acquisition.start":
            host._reset(new)
            new.update({"running": True, "status": "running", "started_at": host.time_s})
            result = {"status": "running", "synthetic": True}
        elif cap == "acquisition.stop":
            if new["running"]:
                new["running"], new["status"] = False, "stopped"
            result = {"status": new["status"], "completed_scans": new["completed_scans"], "synthetic": True}
        elif cap == "nmr.read_fid":
            result = {"samples": new["fid"], "completed_scans": new["completed_scans"], "status": new["status"], "synthetic": True}
        elif cap == "nmr.read_spectrum":
            result = {"samples": new["spectrum"], "nucleus": (new["method"] or {}).get("nucleus"), "carrier_frequency_hz": host._values(new)["method.carrier_frequency_hz"], "status": new["status"], "synthetic": True}
        elif cap == "nmr.integrate":
            integral = 0.0
            for p, q in zip(spectrum, spectrum[1:]):
                x0, x1 = p["frequency_offset_hz"], q["frequency_offset_hz"]
                lo, hi = max(x0, a), min(x1, b)
                if hi <= lo:
                    continue
                y0, y1 = p["amplitude"], q["amplitude"]
                yl, yh = y0 + (y1 - y0) * (lo - x0) / (x1 - x0), y0 + (y1 - y0) * (hi - x0) / (x1 - x0)
                integral += (yl + yh) * (hi - lo) / 2
            new["integral"] = integral
            result = {"integral_hz": integral, "synthetic": True}
        if new != inst:
            new["revision"] = inst["revision"] + 1
            if host.scene_adapter and host._scene(new) != host._scene(inst):
                try:
                    host.scene_adapter(instance_id, host._scene(new))
                except Exception:
                    return self._error(instance_id, "DEVICE_ERROR", request_id)
            host._instances[instance_id] = new
        return self._envelope(instance_id, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        if state_id != "power.enabled":
            return self._error(instance_id, "ACCESS_DENIED", request_id)
        return self.invoke(instance_id=instance_id, capability_id="power.set", arguments={"enabled": value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return _sp80_copy.deepcopy(self.__host._logs[self.__principal])


def write_magritek_spinsolve80_runtime_reports(output_directory):
    """Write static type information only; never export instance sample truth."""
    directory = _sp80_Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    metadata = export_magritek_spinsolve80_contract_metadata()
    (directory / "runtime_metadata.json").write_text(_sp80_json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"metadata": str(directory / "runtime_metadata.json"), "catalog_id": "CHEM-068", "test_status": "not_run_by_report_writer", "generated_at": _sp80_datetime.now(_sp80_timezone.utc).isoformat()}
# END CHEM-068 MAGRITEK SPINSOLVE 80 APPEND-ONLY RUNTIME

# CHEM-068 append-only contract normalization and exact decimal scan scheduling.
from decimal import Decimal as _sp80_Decimal
_sp80_export_contract_base = export_magritek_spinsolve80_contract_metadata


def export_magritek_spinsolve80_contract_metadata():
    metadata = _sp80_export_contract_base()
    specs = metadata["asset_types"][SPINSOLVE80_TYPE]["capabilities"]
    observes = {
        "power.set": ["power.enabled"],
        "sample.insert": ["slot.sample_tube.connection_id", "sample.insertion_fraction"],
        "sample.remove": ["slot.sample_tube.connection_id", "sample.insertion_fraction"],
        "acquisition.configure": ["method." + k for k in specs["acquisition.configure"]["input_schema"]["properties"]],
        "acquisition.start": ["acquisition.status", "measurement.synthetic"],
        "acquisition.stop": ["acquisition.status", "acquisition.completed_scans", "measurement.synthetic"],
        "nmr.read_fid": ["measurement.fid", "acquisition.completed_scans", "acquisition.status", "measurement.synthetic"],
        "nmr.read_spectrum": ["measurement.spectrum", "method.nucleus", "method.carrier_frequency_hz", "acquisition.status", "measurement.synthetic"],
        "nmr.integrate": ["measurement.integral_hz", "measurement.synthetic"]}
    for name, spec in specs.items():
        spec["example_arguments"] = spec["example_arguments"][0]
        spec["source_ids"] = ["runtime_code"]
        spec["limitations"] = ["synthetic工程假设；未执行真实仪器、机器人或接触验证。"]
        spec["observes"] = observes[name]
    metadata["mechanisms"]["synthetic_nmr_signal"]["equations"][1] = "S[k]=sum(FID[n]*exp(-i*2*pi*k*n/N))/N; k=-floor(N/2)..N-floor(N/2)-1; f_k=k/T_acq"
    metadata["mechanisms"]["host_timed_scanning"]["limitations"].append("扫描边界以十进制输入时间计算，避免0.3/0.1等二进制舍入导致漏计扫描；信号与进度为浮点近似。")
    return metadata


def _sp80_host_step_decimal(self, time_s):
    """Host-only absolute time: decimal scan boundaries, no query-side stepping."""
    with self._lock:
        if not _sp80_number(time_s) or time_s < self.time_s:
            raise ValueError("Finite monotonic absolute host time required")
        if time_s == self.time_s:
            return
        staged = {}
        now = _sp80_Decimal(str(time_s))
        for iid, old in self._instances.items():
            if not old["running"]:
                continue
            new, cfg = _sp80_copy.deepcopy(old), old["method"]
            start = _sp80_Decimal(str(new["started_at"]))
            cycle = _sp80_Decimal(str(cfg["relaxation_delay_s"])) + _sp80_Decimal(str(cfg["acquisition_time_s"]))
            total = cfg["scans"] * cycle
            elapsed = now - start
            bounded = min(elapsed, total)
            count = cfg["scans"] if elapsed >= total else int(elapsed // cycle)
            new["elapsed"], new["progress"] = float(bounded), float(bounded / total)
            if count > old["completed_scans"]:
                if not old["fid"]:
                    new["fid"], new["spectrum"] = self._signal(new)
                new["completed_scans"] = count
                new["sample_time"] = float(start + count * cycle)
                new["integral"] = None
            if count == cfg["scans"]:
                new["running"], new["status"] = False, "completed"
            new["revision"] = old["revision"] + 1
            staged[iid] = new
        if self.scene_adapter:
            for iid, new in staged.items():
                if self._scene(new) != self._scene(self._instances[iid]):
                    self.scene_adapter(iid, self._scene(new))
        self._instances.update(staged)
        self.time_s = float(time_s)


MagritekSpinsolve80Runtime.host_step = _sp80_host_step_decimal
# Remove unused sentinel from the first append without rewriting shared content.
del MAGRIΤEK_SPINSOLVE80_RESERVED


# CHEM-068 template-backed samples (HMDB peak lists, scripts/basic40_templates/magritek_spinsolve80.py); host-only, additive.
def _sp80_template_module():
    """Lazy import of the public-data NMR template extension."""
    try:
        from .basic40_templates import magritek_spinsolve80 as module
    except ImportError:
        from basic40_templates import magritek_spinsolve80 as module
    return module


def _sp80_register_template_sample(self, instance_id, sample_id, composition, *, solvent=None):
    _sp80_template_module().register_template_sample(self, instance_id, sample_id, composition, solvent=solvent)


def _sp80_template_sample_truth(self, instance_id):
    return _sp80_template_module().template_sample_truth(self, instance_id)


MagritekSpinsolve80Runtime.register_template_sample = _sp80_register_template_sample
MagritekSpinsolve80Runtime.template_sample_truth = _sp80_template_sample_truth

# BEGIN CHEM-160 RIGAKU MINIFLEX600 APPEND-ONLY RUNTIME
import copy as _mf_copy
import hashlib as _mf_hashlib
import json as _mf_json
import math as _mf_math
import threading as _mf_threading

RIGAKU_MINIFLEX600_TYPE = "instrument.rigaku_miniflex600_external_cooling"
RIGAKU_MINIFLEX600_MIN_RAD = _mf_math.radians(-3)
RIGAKU_MINIFLEX600_MAX_RAD = _mf_math.radians(145)
RIGAKU_MINIFLEX600_STEP_RAD = _mf_math.radians(0.005)
RIGAKU_MINIFLEX600_DOOR_MAX_RAD = _mf_math.pi / 2
RIGAKU_MINIFLEX600_MAX_POINTS = 29601


def _mf_number(v):
    try:
        return type(v) in (int, float) and _mf_math.isfinite(v)
    except (TypeError, ValueError, OverflowError):
        return False


def _mf_num(unit, lo=None, hi=None, nullable=False, positive=False):
    s = {"type": "number", "x-unit": unit}
    if lo is not None:
        s["minimum"] = lo
    if hi is not None:
        s["maximum"] = hi
    if positive:
        s["exclusiveMinimum"] = 0
    return {"anyOf": [s, {"type": "null"}]} if nullable else s


def _mf_obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _mf_matches(s, v):
    if "anyOf" in s:
        return any(_mf_matches(branch, v) for branch in s["anyOf"])
    typ = s.get("type")
    if typ == "null":
        return v is None
    if typ == "object":
        return isinstance(v, dict) and set(v) == set(s["properties"]) and all(_mf_matches(s["properties"][k], x) for k, x in v.items())
    if typ == "array":
        return isinstance(v, list) and s.get("minItems", 0) <= len(v) <= s.get("maxItems", len(v)) and all(_mf_matches(s["items"], x) for x in v)
    if typ in ("number", "integer"):
        if not _mf_number(v) or (typ == "integer" and type(v) is not int):
            return False
        if ("minimum" in s and v < s["minimum"]) or ("maximum" in s and v > s["maximum"]) or ("exclusiveMinimum" in s and v <= s["exclusiveMinimum"]):
            return False
    elif typ == "boolean":
        if type(v) is not bool:
            return False
    elif typ == "string":
        if not isinstance(v, str) or len(v) < s.get("minLength", 0) or len(v) > s.get("maxLength", len(v)):
            return False
    else:
        return False
    return ("enum" not in s or v in s["enum"]) and ("const" not in s or v == s["const"])


def _mf_grid(value, quantum):
    ratio = value / quantum
    return _mf_number(ratio) and abs(ratio - round(ratio)) <= 1e-7


def export_rigaku_miniflex600_contract_metadata():
    """Static catalogue, API and approximation metadata; contains no instance truth."""
    boolean, synthetic = {"type": "boolean"}, {"type": "boolean", "const": True}
    ident = {"type": "string", "minLength": 1, "maxLength": 256}
    nullable_id = {"anyOf": [ident, {"type": "null"}]}
    angle = _mf_num("rad", RIGAKU_MINIFLEX600_MIN_RAD, RIGAKU_MINIFLEX600_MAX_RAD)
    status = {"type": "string", "enum": ["idle", "running", "completed", "stopped", "cooling_lost", "power_lost"]}
    response_point = _mf_obj({"two_theta_rad": angle, "intensity_cps": _mf_num("1/s", 0)})
    response = {"type": "array", "items": response_point, "minItems": 2, "maxItems": RIGAKU_MINIFLEX600_MAX_POINTS}
    point = _mf_obj({"two_theta_rad": angle, "intensity_cps": _mf_num("1/s", 0), "expected_counts": _mf_num("1", 0),
        "sample_time_s": _mf_num("s", 0), "integration_time_s": _mf_num("s", positive=True),
        "sample_window_s": {"type": "array", "items": _mf_num("s", 0), "minItems": 2, "maxItems": 2}, "synthetic": synthetic})
    pattern = {"type": "array", "items": point, "maxItems": RIGAKU_MINIFLEX600_MAX_POINTS}
    slots = {}
    def route(capability, parameter):
        return {"capability_id": capability, "parameter": parameter, "fixed_arguments": {}}
    def slot(sid, unit, origin, schema, text, component, write=None, visibility="agent", sampling="instant"):
        slots[sid] = {"description_zh": text, "unit": unit, "origin": origin, "value_schema": schema, "component_ids": [component],
            "visibility": visibility, "access": "read_write" if write else "read_only", "write_via": write,
            "read_binding": "RigakuMiniFlex600Session.read(instance_id=..., state_ids=[state_id])",
            "sampling": {"mode": sampling, "time_domain": "simulation", "window": "Each pattern point retains its explicit integration window." if sampling == "series" else "Last completed integration interval; returned as sample_window_s." if sampling == "interval_mean" else None},
            "reference_conditions": "SI rad; instrument-local joint convention, +Z up." if unit == "rad" else None}
    slot("power.enabled", "1", "setpoint", boolean, "逻辑供电；非真实高压电源控制。", "housing", route("power.set", "enabled"))
    slot("access.door_angle_rad", "rad", "scene", _mf_num("rad", 0, RIGAKU_MINIFLEX600_DOOR_MAX_RAD), "门关节；0为关闭，π/2展示上限为engineering_assumption。", "door", route("access.set_door_angle", "angle_rad"))
    slot("slot.sample.connection_id", "1", "scene", nullable_id, "粉末平片或适配样品架的逻辑槽占用，不执行机器人插拔。", "sample_holder", route("slot.set_sample", "connection_id"))
    slot("cooling.connection_id", "1", "scene", nullable_id, "外置循环水依赖连接，只可由宿主登记。", "cooling_ports")
    slot("cooling.ready", "1", "derived", boolean, "宿主明确提供的循环水就绪信号，未模拟水温/流量传感器。", "cooling_ports")
    slot("xray.voltage_v", "V", "setpoint", _mf_num("V", 20000, 40000, True), "20–40kV，1kV步长，尚未设置时null。", "xray_tube")
    slot("xray.current_a", "A", "setpoint", _mf_num("A", 0.002, 0.015, True), "2–15mA，1mA步长，尚未设置时null。", "xray_tube")
    slot("xray.power_w", "W", "derived", _mf_num("W", 0, 600, True), "设定管压×管流；600W为整机管源最大输出约束。", "xray_tube")
    slot("xray.active", "1", "derived", boolean, "仅扫描时为真的逻辑射线使能；不模拟辐射场或屏蔽性能。", "xray_tube")
    slot("slit.configuration_id", "1", "setpoint", nullable_id, "显式方法狭缝标签；数值孔径未知，未计算其展宽效应。", "slit_assembly", route("xrd.set_slit", "slit_id"))
    slot("goniometer.two_theta_rad", "rad", "scene", angle, "2θ权威机械角，范围−3..145°、量化0.005°后以rad公开。", "goniometer", route("motion.set_two_theta", "angle_rad"))
    slot("goniometer.sample_theta_rad", "rad", "derived", _mf_num("rad", RIGAKU_MINIFLEX600_MIN_RAD / 2, RIGAKU_MINIFLEX600_MAX_RAD / 2), "θ=2θ/2是engineering_assumption的理想耦合展示。", "sample_holder")
    for key, unit, text, schema in (("start_rad", "rad", "起点", {"anyOf": [angle, {"type": "null"}]}), ("stop_rad", "rad", "终点", {"anyOf": [angle, {"type": "null"}]}),
            ("step_rad", "rad", "步长", _mf_num("rad", RIGAKU_MINIFLEX600_STEP_RAD, nullable=True)), ("integration_time_s", "s", "显式积分时间，厂家范围未知", _mf_num("s", nullable=True, positive=True))):
        slot("scan." + key, unit, "setpoint", schema, "用户配置扫描" + text + "。", "goniometer")
    slot("scan.running", "1", "derived", boolean, "开始调用只置运行标志；采集仅在host_step推进。", "detector")
    slot("scan.status", "1", "derived", status, "完成仅表示合成插值扫描已采集所有方法点。", "detector")
    for name, text in (("completed_points", "已完成采集点数"), ("total_points", "配置方法的点数")):
        slot("scan." + name, "1", "derived", {"type": "integer", "minimum": 0, "maximum": RIGAKU_MINIFLEX600_MAX_POINTS, "x-unit": "1"}, text + "；未积分完的点不计入。", "detector")
    slot("measurement.pattern", "1", "derived", pattern, "仅已完成积分点的synthetic曲线；逐点含rad、1/s、期望计数与窗口，不含晶体学真实性声明。", "detector", sampling="series")
    slot("measurement.last_intensity_cps", "1/s", "derived", _mf_num("1/s", 0, nullable=True), "最近完整积分窗口的synthetic平均计数率；没有数据时null。", "detector", sampling="interval_mean")
    slot("measurement.synthetic", "1", "derived", synthetic, "所有输出结果均为synthetic。", "detector")
    slot("truth.response", "1", "process_truth", {"anyOf": [response, {"type": "null"}]}, "宿主输入的合成响应网格，仅debug/evaluator可读。", "detector", visibility="debug")
    slot("truth.sample_connection_id", "1", "process_truth", nullable_id, "宿主响应对应的样品槽身份；避免换样后沿用旧响应。", "sample_holder", visibility="debug")
    scene_keys = ["power.enabled", "access.door_angle_rad", "slot.sample.connection_id", "goniometer.two_theta_rad", "goniometer.sample_theta_rad", "xray.active", "scan.running"]
    reset_effects = ["scan.running", "scan.status", "scan.completed_points", "measurement.pattern", "measurement.last_intensity_cps", "xray.active"]
    defs = {
        "power.set": ({"enabled": boolean}, {"enabled": boolean}, {"enabled": True}, ["power.enabled", "scan.running", "scan.status", "xray.active"], ["housing", "xray_tube"], "设置逻辑电源；断电终止扫描并保留完整积分点。", [], "实例可用。"),
        "access.set_door_angle": ({"angle_rad": _mf_num("rad", 0, RIGAKU_MINIFLEX600_DOOR_MAX_RAD)}, {"angle_rad": _mf_num("rad")}, {"angle_rad": 0.5}, ["access.door_angle_rad"], ["door"], "设置示意门角。", ["xray.active", "scan.running"], "扫描中拒绝开门；门角超出展示限位拒绝。"),
        "slot.set_sample": ({"connection_id": nullable_id}, {"connection_id": nullable_id}, {"connection_id": "powder_holder_01"}, ["slot.sample.connection_id", "truth.response", "truth.sample_connection_id"] + reset_effects, ["sample_holder"], "登记样品槽连接；换样清除旧合成响应与旧曲线。", ["access.door_angle_rad", "scan.running"], "门打开且扫描停止；只登记逻辑连接。"),
        "xray.configure": ({"voltage_v": _mf_num("V", 20000, 40000), "current_a": _mf_num("A", 0.002, 0.015)}, {"voltage_v": _mf_num("V"), "current_a": _mf_num("A")}, {"voltage_v": 40000, "current_a": 0.015}, ["xray.voltage_v", "xray.current_a", "xray.power_w"], ["xray_tube"], "配置管压/管流，分别验证1kV/1mA量化和600W上限。", ["scan.running"], "扫描停止。"),
        "xrd.configure_scan": ({"start_rad": angle, "stop_rad": angle, "step_rad": _mf_num("rad", RIGAKU_MINIFLEX600_STEP_RAD), "integration_time_s": _mf_num("s", positive=True)}, {"start_rad": angle, "stop_rad": angle, "step_rad": _mf_num("rad"), "integration_time_s": _mf_num("s")}, {"start_rad": 0, "stop_rad": _mf_math.radians(1), "step_rad": _mf_math.radians(0.5), "integration_time_s": 1}, ["scan.start_rad", "scan.stop_rad", "scan.step_rad", "scan.integration_time_s", "scan.total_points"] + reset_effects, ["goniometer", "detector"], "配置递增等步长扫描；所有角度对0.005°网格量化，起止区间须整除步长。", ["scan.running"], "扫描停止；0.005°至全程的有限网格；积分时间须正且总时间可表示。"),
        "xrd.set_slit": ({"slit_id": ident}, {"slit_id": ident}, {"slit_id": "host_selected_slit"}, ["slit.configuration_id"], ["slit_assembly"], "记录显式狭缝方法标签，不推断未知孔径。", ["scan.running"], "扫描停止。"),
        "motion.set_two_theta": ({"angle_rad": angle}, {"angle_rad": angle}, {"angle_rad": _mf_math.radians(20)}, ["goniometer.two_theta_rad", "goniometer.sample_theta_rad"], ["goniometer", "sample_holder"], "设置2θ及θ/2展示耦合，无速度/加速度动态。", ["power.enabled", "access.door_angle_rad", "scan.running"], "供电、门关、扫描停止，角度符合0.005°网格。"),
        "xrd.start_scan": ({}, {"status": status, "synthetic": synthetic}, {}, reset_effects + ["goniometer.two_theta_rad", "goniometer.sample_theta_rad"], ["goniometer", "sample_holder", "detector", "xray_tube", "cooling_ports", "door"], "开始合成扫描，定位起点但不推进宿主时间、不产生立即读数。", ["power.enabled", "access.door_angle_rad", "cooling.connection_id", "cooling.ready", "slot.sample.connection_id", "xray.voltage_v", "xray.current_a", "slit.configuration_id", "scan.total_points"], "门关闭、电源开启、循环水连接就绪、样品槽与对应宿主响应/全部方法参数齐备；响应覆盖整个扫描。"),
        "xrd.stop_scan": ({}, {"status": status, "synthetic": synthetic}, {}, ["scan.running", "scan.status", "xray.active"], ["detector", "xray_tube"], "停止扫描，保留已完成积分点，未完成积分点丢弃。", [], "实例可用；不提供部分扫描续跑，重新start会清空先前曲线。"),
        "xrd.read_pattern": ({}, {"samples": pattern, "status": status, "synthetic": synthetic}, {}, [], ["detector"], "读取现有合成曲线，不推进时间且不生成任何新点。", [], "实例可用。"),
        "xrd.phase_search": ({}, {}, {}, [], ["detector"], "物相检索未实现；缺少晶体结构数据库与经过验证的响应。", [], "unsupported。"),
        "xrd.refine": ({}, {}, {}, [], ["detector"], "精修未实现；未建立结构因子、仪器展宽、择优取向与计数噪声模型。", [], "unsupported。"),
    }
    caps = {}
    for name, (inputs, outputs, example, effects, components, text, pre_states, pre) in defs.items():
        unsupported = name in ("xrd.phase_search", "xrd.refine")
        visible_effects = [s for s in effects if s in scene_keys]
        caps[name] = {"availability": "unsupported" if unsupported else "implemented", "visibility": "agent",
            "kind": "analysis" if unsupported else "query" if name == "xrd.read_pattern" else "configuration" if name in ("slot.set_sample", "xray.configure", "xrd.configure_scan", "xrd.set_slit") else "device_command",
            "description_zh": text, "input_schema": _mf_obj(inputs), "output_schema": _mf_obj(outputs), "example_arguments": [example],
            "state_effects": [{"state_id": s, "timing": "immediate", "description_zh": text} for s in effects],
            "observes": ["measurement.pattern", "scan.status", "measurement.synthetic"] if name == "xrd.read_pattern" else [s for s in effects if not s.startswith("truth.")],
            "component_ids": components, "preconditions_zh": pre, "precondition_state_ids": pre_states,
            "binding": "basic40_runtime_measure.RigakuMiniFlex600Session.invoke(instance_id=..., capability_id='" + name + "', arguments=..., request_id=...)",
            "completion_criterion_zh": "返回unsupported，无结果变化。" if unsupported else "同步命令已提交；扫描完成须另由host_step推进全部积分点并观察scan.status=completed。",
            "partial_failure_zh": "拒绝无过程/场景变化。停止、掉电、冷却丢失保留已完成积分点，丢弃当前未完成积分。scene_adapter异常时过程不提交，但外部场景可能部分写入，需要宿主重新同步。",
            "model_effects": {"mode": "host_required" if visible_effects else "none", "binding_ids": [], "description_zh": "scene_adapter接收公开场景快照；实际Blender求值由独立验证记录支撑。" if visible_effects else "配置、数据读取或未实现分析不改变几何。"},
            "implementation": {"process": "absent" if unsupported else "implemented", "host": "absent" if unsupported else "implemented", "scene": "declared" if visible_effects else "not_applicable", "robot": "absent", "hardware": "absent"},
            "error_codes": ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "ACCESS_DENIED", "PRECONDITION_FAILED", "BUSY", "REQUEST_CONFLICT", "DEVICE_ERROR", "UNSUPPORTED"], "retry": "same_request_id; in-memory only"}
    def parameter(value, unit, evidence, text):
        return {"value": value, "unit": unit, "meaning_zh": text, "evidence": evidence,
            "source_ids": ["workbook_parameters"] if evidence == "documented" else ["runtime_code"], "uncertainty": None}
    params = {
        "voltage_range_v": parameter([20000, 40000], "V", "documented", "清单给定20–40kV。"),
        "voltage_step_v": parameter(1000, "V", "documented", "清单给定1kV步长。"),
        "current_range_a": parameter([0.002, 0.015], "A", "documented", "清单给定2–15mA。"),
        "current_step_a": parameter(0.001, "A", "documented", "清单给定1mA步长。"),
        "maximum_output_w": parameter(600, "W", "documented", "清单整机X射线管最大输出。"),
        "goniometer_radius_m": parameter(0.15, "m", "documented", "清单给定测角仪半径150mm。"),
        "two_theta_range_rad": parameter([RIGAKU_MINIFLEX600_MIN_RAD, RIGAKU_MINIFLEX600_MAX_RAD], "rad", "documented", "−3..145°换算SI。"),
        "angle_quantum_rad": parameter(RIGAKU_MINIFLEX600_STEP_RAD, "rad", "documented", "0.005°最小步进；不代表角度准确度。"),
        "door_display_limit_rad": parameter(RIGAKU_MINIFLEX600_DOOR_MAX_RAD, "rad", "assumption", "engineering_assumption：门限位演示上限，厂家未给实际角度。"),
        "sample_theta_ratio": parameter(0.5, "1", "assumption", "engineering_assumption：理想θ–2θ展示耦合。"),
    }
    for key, unit in (("cooling_minimum_flow_m3_s", "m^3/s"), ("cooling_temperature_k", "K"), ("cooling_pressure_pa", "Pa"),
            ("slit_aperture_rad", "rad"), ("detector_quantum_efficiency", "1"), ("detector_dark_rate_cps", "1/s"),
            ("xray_wavelength_m", "m"), ("instrument_broadening_rad", "rad"), ("preferred_orientation", "1"),
            ("detector_mass_kg", "kg"), ("sample_stage_mass_kg", "kg"), ("tube_mass_kg", "kg"),
            ("integration_time_min_s", "s"), ("integration_time_max_s", "s"), ("motor_speed_rad_s", "rad/s"), ("actual_diffraction_response", "1/s")):
        params[key] = parameter(None, unit, "unknown", "清单未提供；保留null，不将整机规格代替部件参数，不据此声明物理结果。")
    limitations = ["All acquired intensities and expected counts are synthetic. The host supplies a response grid for workflow verification; no random peak generation, crystal structure calculation, phase search or refinement is implemented.",
        "Linear interpolation and expected_counts=rate*integration_time are engineering_assumption. Counts are expectations, not simulated Poisson events. Tube settings and slit labels do not modify the host-supplied response; each response is valid only for the tube/slit configuration at host_load_synthetic_response.",
        "Unknown manufacturer cooling, detector, response, slit aperture and motor parameters remain null. Cooling.ready is a host assertion, not a measured flow or temperature. No actual radiation, shielding or equipment safety certification is simulated.",
        "Joint motion is instantaneous commanded pose; sample_theta=two_theta/2 and the 90-degree door display limit are engineering_assumption. No robot insertion, contact solver, acceleration or settling dynamics is implemented.",
        "Only explicit absolute host_step advances a shared clock. read, describe and read_pattern do not advance time. Stops retain complete integration points and discard the unfinished interval; starting a new scan clears the previous pattern.",
        "Session filtering is an API boundary only, not a Python/process/filesystem sandbox. Pass a scoped session to an agent; protect the trusted host and private response storage separately. No private instance truth is written to runtime report files.",
        "Request deduplication is memory-local and does not survive restart. External scene callback failure can leave partial scene effects although process and clock are not committed. Evaluated geometry requires separate scene validation."]
    mechanisms = {
        "synthetic_scan": {"name_zh": "宿主合成响应的定时采集", "meaning_zh": "engineering_assumption：将显式宿主响应按角度线性插值，每个积分窗口仅生成确定性期望计数；不等于衍射物理模型。",
            "component_ids": ["detector", "goniometer", "xray_tube"], "state_ids": ["measurement.pattern", "scan.running", "goniometer.two_theta_rad", "truth.response"],
            "implementation": "numerical", "equations": ["theta_i=start+i*step; t_i=t_start+(i+1)*integration_time", "rate(theta)=piecewise_linear(host_response); expected_counts=rate*integration_time"],
            "parameters": {k: params[k] for k in ("angle_quantum_rad", "actual_diffraction_response", "detector_quantum_efficiency", "instrument_broadening_rad")}, "source_ids": ["workbook_parameters", "runtime_code"], "limitations": limitations[:2]},
        "theta_two_theta": {"name_zh": "理想θ–2θ展示", "meaning_zh": "engineering_assumption：样品台随2θ按一半角度显示，不模拟驱动器或惯性。", "component_ids": ["sample_holder", "goniometer"],
            "state_ids": ["goniometer.two_theta_rad", "goniometer.sample_theta_rad"], "implementation": "numerical", "equations": ["sample_theta_rad=0.5*two_theta_rad"],
            "parameters": {"sample_theta_ratio": params["sample_theta_ratio"], "motor_speed_rad_s": params["motor_speed_rad_s"]}, "source_ids": ["runtime_code"], "limitations": ["不宣称厂家实际传动、角度精度或动力学。"]}}
    return {"asset_types": {RIGAKU_MINIFLEX600_TYPE: {"catalog_id": "CHEM-160", "model": "Rigaku | MiniFlex600，外置循环水配置", "asset_version": "1.0.0", "state_slots": slots, "capabilities": caps, "parameters": params}},
        "scene_snapshot_keys": scene_keys, "host_capabilities": {
            "sim.host_step": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "RigakuMiniFlex600Runtime.host_step(time_s)", "effects": reset_effects + ["goniometer.two_theta_rad", "goniometer.sample_theta_rad"]},
            "sim.set_cooling": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "RigakuMiniFlex600Runtime.host_set_cooling(instance_id, connection_id=..., ready=...)", "semantics_zh": "外置循环水逻辑依赖；失去ready时立即停止扫描，保留完整点。"},
            "sim.load_synthetic_response": {"kind": "simulation", "visibility": "trusted_host_only", "binding": "RigakuMiniFlex600Runtime.host_load_synthetic_response(instance_id, response)", "semantics_zh": "给当前样品、管压/管流与狭缝配置绑定合成响应网格；没有任何真实样品响应默认值。"}},
        "mechanisms": mechanisms, "limitations": limitations}


class RigakuMiniFlex600Runtime:
    """Trusted simulation host. Give agents scoped sessions, never this host."""
    def __init__(self, *, scene_adapter=None):
        self.time_s, self.scene_adapter = 0.0, scene_adapter
        self._instances, self._principals, self._requests, self._logs = {}, {}, {}, {}
        self._lock, self._metadata = _mf_threading.RLock(), export_rigaku_miniflex600_contract_metadata()
        self.register_principal("__public_agent__", visibility="agent")
        self._agent = self.session("__public_agent__")

    def create_instance(self, instance_id, asset_type_id=RIGAKU_MINIFLEX600_TYPE):
        with self._lock:
            if not isinstance(instance_id, str) or not instance_id or instance_id in self._instances or asset_type_id != RIGAKU_MINIFLEX600_TYPE:
                raise ValueError("Unique instance and declared asset type required")
            inst = {"revision": 0, "power": False, "door": 0.0, "sample": None, "cooling": None, "cooling_ready": False,
                "voltage": None, "current": None, "slit": None, "two_theta": 0.0, "method": None,
                "response": None, "response_sample": None, "response_configuration": None}
            self._reset(inst)
            if self.scene_adapter:
                self.scene_adapter(instance_id, self._scene(inst))
            self._instances[instance_id] = inst

    @staticmethod
    def _reset(inst):
        inst.update({"running": False, "status": "idle", "pattern": [], "started_at": None})

    @staticmethod
    def _stop(inst, status):
        inst["running"], inst["status"] = False, status

    def register_principal(self, principal_id, *, visibility, instance_ids=None):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Invalid trusted principal")
            if instance_ids is not None and (not isinstance(instance_ids, (list, tuple, set, frozenset)) or any(not isinstance(s, str) for s in instance_ids)):
                raise ValueError("Invalid instance scope")
            scope = None if instance_ids is None else frozenset(instance_ids)
            if scope is not None and not scope <= self._instances.keys():
                raise ValueError("Unknown instance scope")
            self._principals[principal_id], self._logs[principal_id] = (visibility, scope), []

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Trusted registration required")
        return RigakuMiniFlex600Session(self, principal_id)

    def describe(self, **kwargs):
        return self._agent.describe(**kwargs)

    def read(self, **kwargs):
        return self._agent.read(**kwargs)

    def invoke(self, **kwargs):
        return self._agent.invoke(**kwargs)

    def write(self, **kwargs):
        return self._agent.write(**kwargs)

    def _values(self, inst):
        method = inst["method"]
        values = {"power.enabled": inst["power"], "access.door_angle_rad": inst["door"], "slot.sample.connection_id": inst["sample"],
            "cooling.connection_id": inst["cooling"], "cooling.ready": inst["cooling_ready"], "xray.voltage_v": inst["voltage"], "xray.current_a": inst["current"],
            "xray.power_w": None if inst["voltage"] is None else inst["voltage"] * inst["current"], "xray.active": inst["running"], "slit.configuration_id": inst["slit"],
            "goniometer.two_theta_rad": inst["two_theta"], "goniometer.sample_theta_rad": inst["two_theta"] / 2,
            "scan.running": inst["running"], "scan.status": inst["status"], "scan.completed_points": len(inst["pattern"]),
            "scan.total_points": 0 if method is None else method["point_count"], "measurement.pattern": inst["pattern"],
            "measurement.last_intensity_cps": inst["pattern"][-1]["intensity_cps"] if inst["pattern"] else None,
            "measurement.synthetic": True, "truth.response": inst["response"], "truth.sample_connection_id": inst["response_sample"]}
        values.update({"scan." + k: None if method is None else method[k] for k in ("start_rad", "stop_rad", "step_rad", "integration_time_s")})
        return values

    def _scene(self, inst):
        values = self._values(inst)
        return _mf_copy.deepcopy({k: values[k] for k in self._metadata["scene_snapshot_keys"]})

    def scene_snapshot(self, instance_id):
        with self._lock:
            return self._scene(self._instances[instance_id])

    def apply_scene(self, instance_id):
        with self._lock:
            if self.scene_adapter is None:
                raise RuntimeError("No scene adapter registered")
            snapshot = self.scene_snapshot(instance_id)
            self.scene_adapter(instance_id, _mf_copy.deepcopy(snapshot))
            return snapshot

    def _commit(self, instance_id, candidate):
        old = self._instances[instance_id]
        if old != candidate:
            if self.scene_adapter and self._scene(old) != self._scene(candidate):
                self.scene_adapter(instance_id, self._scene(candidate))
            candidate["revision"] = old["revision"] + 1
            self._instances[instance_id] = candidate

    def host_set_cooling(self, instance_id, *, connection_id, ready):
        with self._lock:
            if instance_id not in self._instances or (connection_id is not None and (not isinstance(connection_id, str) or not 1 <= len(connection_id) <= 256)) or type(ready) is not bool or (ready and connection_id is None):
                raise ValueError("Valid instance and explicit cooling connection/ready required")
            candidate = _mf_copy.deepcopy(self._instances[instance_id])
            candidate["cooling"], candidate["cooling_ready"] = connection_id, ready
            if not ready and candidate["running"]:
                self._stop(candidate, "cooling_lost")
            self._commit(instance_id, candidate)

    def host_load_synthetic_response(self, instance_id, response):
        with self._lock:
            inst = self._instances.get(instance_id)
            schema = self._metadata["asset_types"][RIGAKU_MINIFLEX600_TYPE]["state_slots"]["truth.response"]["value_schema"]["anyOf"][0]
            if inst is None or inst["running"] or inst["sample"] is None or any(inst[k] is None for k in ("voltage", "current", "slit")) or not _mf_matches(schema, response):
                raise ValueError("Stopped configured sample and finite synthetic response grid required")
            if any(a["two_theta_rad"] >= b["two_theta_rad"] for a, b in zip(response, response[1:])):
                raise ValueError("Response angles must strictly increase")
            candidate = _mf_copy.deepcopy(inst)
            candidate["response"], candidate["response_sample"] = _mf_copy.deepcopy(response), inst["sample"]
            candidate["response_configuration"] = (inst["voltage"], inst["current"], inst["slit"])
            self._reset(candidate)
            self._commit(instance_id, candidate)

    @staticmethod
    def _intensity(inst, angle):
        response = inst["response"]
        if not response[0]["two_theta_rad"] <= angle <= response[-1]["two_theta_rad"]:
            raise ValueError("Synthetic response does not cover scan angle")
        left, right = 0, len(response) - 1
        while right - left > 1:
            middle = (left + right) // 2
            if response[middle]["two_theta_rad"] <= angle:
                left = middle
            else:
                right = middle
        a, b = response[left], response[right]
        f = (angle - a["two_theta_rad"]) / (b["two_theta_rad"] - a["two_theta_rad"])
        return (1 - f) * a["intensity_cps"] + f * b["intensity_cps"]

    @staticmethod
    def _angle(method, index):
        if index == method["point_count"] - 1:
            return method["stop_rad"]
        return method["start_rad"] + index * method["step_rad"]

    def host_step(self, time_s):
        """Advance absolute shared simulation time; a read never calls this."""
        with self._lock:
            if not _mf_number(time_s) or time_s < self.time_s:
                raise ValueError("Finite monotonic absolute host time required")
            if time_s == self.time_s:
                return
            candidates = _mf_copy.deepcopy(self._instances)
            for inst in candidates.values():
                if not inst["running"]:
                    continue
                method = inst["method"]
                while len(inst["pattern"]) < method["point_count"]:
                    idx = len(inst["pattern"])
                    end = inst["started_at"] + (idx + 1) * method["integration_time_s"]
                    if end > time_s:
                        break
                    start = inst["started_at"] + idx * method["integration_time_s"]
                    angle = self._angle(method, idx)
                    rate = self._intensity(inst, angle)
                    counts = rate * method["integration_time_s"]
                    if not all(_mf_number(x) for x in (end, start, rate, counts)) or end <= start:
                        raise ValueError("Synthetic integration exceeds finite numeric domain")
                    inst["pattern"].append({"two_theta_rad": angle, "intensity_cps": rate, "expected_counts": counts, "sample_time_s": end,
                        "integration_time_s": method["integration_time_s"], "sample_window_s": [start, end], "synthetic": True})
                done = len(inst["pattern"])
                inst["two_theta"] = self._angle(method, min(done, method["point_count"] - 1))
                if done == method["point_count"]:
                    self._stop(inst, "completed")
            # All process changes and the common clock commit together. An external
            # callback may itself have partial effects and must be reconciled by host.
            if self.scene_adapter:
                for key, candidate in candidates.items():
                    if self._scene(candidate) != self._scene(self._instances[key]):
                        self.scene_adapter(key, self._scene(candidate))
            for key, candidate in candidates.items():
                if candidate != self._instances[key]:
                    candidate["revision"] = self._instances[key]["revision"] + 1
            self._instances, self.time_s = candidates, float(time_s)


class RigakuMiniFlex600Session:
    """Identity-scoped API view; not an in-process Python security sandbox."""
    def __init__(self, host, principal_id):
        self.__host, self.__principal = host, principal_id

    def _instance(self, instance_id):
        _, scope = self.__host._principals[self.__principal]
        return self.__host._instances.get(instance_id) if isinstance(instance_id, str) and (scope is None or instance_id in scope) else None

    def _visible(self, definition):
        visibility, _ = self.__host._principals[self.__principal]
        return definition["visibility"] == "agent" or visibility in ("debug", "evaluator")

    def _envelope(self, instance_id, request_id=None, *, status="succeeded", result=None, observations=None, error=None):
        inst = self._instance(instance_id)
        return {"protocol_version": "1.0.0", "instance_id": instance_id, "request_id": request_id, "operation_id": None, "status": status,
            "state_revision": inst["revision"] if inst is not None else None, "clock": {"domain": "simulation", "time_s": self.__host.time_s},
            "result": _mf_copy.deepcopy(result or {}), "observations": _mf_copy.deepcopy(observations or {}), "error": error}

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        messages = {"UNKNOWN_INSTANCE": "Instance unavailable.", "UNKNOWN_CAPABILITY": "Capability unavailable.", "INVALID_ARGUMENT": "Invalid argument schema or numeric domain.",
            "ACCESS_DENIED": "Access policy denies this request.", "PRECONDITION_FAILED": "Required sample, response, cooling or configuration unavailable.", "BUSY": "Scan is running.",
            "REQUEST_CONFLICT": "Request identifier already used with different arguments.", "DEVICE_ERROR": "External scene synchronization failed; process was not committed.", "UNSUPPORTED": "This analysis is not implemented."}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": messages[code], "retryable": False,
            "side_effects": "unknown" if code == "DEVICE_ERROR" else "none", "progress": None})

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self.__host._lock:
            if (asset_type_id is None) == (instance_id is None) or (asset_type_id is not None and asset_type_id != RIGAKU_MINIFLEX600_TYPE):
                return self._error(None, "INVALID_ARGUMENT")
            if instance_id is not None and self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            _, scope = self.__host._principals[self.__principal]
            if scope is not None and not scope:
                return self._error(instance_id, "ACCESS_DENIED")
            spec = _mf_copy.deepcopy(self.__host._metadata["asset_types"][RIGAKU_MINIFLEX600_TYPE])
            visible = {k: v for k, v in spec["state_slots"].items() if self._visible(v)}
            spec["state_slots"] = visible
            spec["capabilities"].update(_mf_template_module().capability_metadata())  # template-backed sample queries (additive)
            for cap in spec["capabilities"].values():
                cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in visible]
                cap["observes"] = [s for s in cap["observes"] if s in visible]
            return {"protocol_version": "1.0.0", "asset_type_id": RIGAKU_MINIFLEX600_TYPE, "instance_id": instance_id,
                "adapter": "basic40_runtime_measure.RigakuMiniFlex600Session", **spec, "limitations": _mf_copy.deepcopy(self.__host._metadata["limitations"])}

    def read(self, *, instance_id, state_ids=None):
        with self.__host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE")
            slots = self.__host._metadata["asset_types"][RIGAKU_MINIFLEX600_TYPE]["state_slots"]
            if state_ids is None:
                state_ids = [s for s, d in slots.items() if self._visible(d)]
            if not isinstance(state_ids, list) or any(not isinstance(s, str) for s in state_ids) or len(state_ids) != len(set(state_ids)):
                return self._error(instance_id, "INVALID_ARGUMENT")
            if any(s not in slots or not self._visible(slots[s]) for s in state_ids):
                return self._error(instance_id, "ACCESS_DENIED")
            values = self.__host._values(inst)
            observations = {}
            for sid in state_ids:
                value = values[sid]
                sample_time = self.__host.time_s
                window = None
                if sid == "measurement.last_intensity_cps":
                    sample_time = inst["pattern"][-1]["sample_time_s"] if inst["pattern"] else None
                    window = inst["pattern"][-1]["sample_window_s"] if inst["pattern"] else None
                observations[sid] = {"value": _mf_copy.deepcopy(value), "unit": slots[sid]["unit"],
                    "quality": "unavailable" if value is None else "estimated" if slots[sid]["origin"] == "derived" else "valid",
                    "sample_time_s": sample_time, "sample_window_s": window, "synthetic": sid.startswith("measurement."),
                    "reason": "No explicitly configured value or completed synthetic measurement." if value is None else None}
            return self._envelope(instance_id, observations=observations)

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self.__host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            try:
                digest = _mf_hashlib.sha256(_mf_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
            except (TypeError, ValueError, OverflowError):
                return self._error(instance_id, "INVALID_ARGUMENT", request_id)
            key = self.__principal, instance_id, request_id
            if key in host._requests:
                old_digest, result = host._requests[key]
                return _mf_copy.deepcopy(result) if old_digest == digest else self._error(instance_id, "REQUEST_CONFLICT", request_id)
            response = self._checked(instance_id, inst, capability_id, arguments, request_id)
            host._requests[key] = digest, _mf_copy.deepcopy(response)
            host._logs[self.__principal].append({"instance_id": instance_id, "capability_id": capability_id, "request_id": request_id, "response": _mf_copy.deepcopy(response)})
            return response

    def _checked(self, instance_id, inst, cap, args, request_id):
        host = self.__host
        fail = lambda code, status="rejected": self._error(instance_id, code, request_id, status)
        if cap in ("template.read", "xrd.read_template_pattern"):  # additive template-backed queries (read-only)
            return _mf_template_module().session_query(self, host, instance_id, inst, cap, args, request_id)
        caps = host._metadata["asset_types"][RIGAKU_MINIFLEX600_TYPE]["capabilities"]
        if cap not in caps:
            return fail("UNKNOWN_CAPABILITY")
        if not _mf_matches(caps[cap]["input_schema"], args):
            return fail("INVALID_ARGUMENT")
        if caps[cap]["availability"] == "unsupported":
            return fail("UNSUPPORTED", "unsupported")
        if inst["running"] and cap not in ("power.set", "xrd.stop_scan", "xrd.read_pattern"):
            return fail("BUSY")
        if cap == "slot.set_sample" and inst["door"] <= 0:
            return fail("PRECONDITION_FAILED")
        if cap == "xray.configure" and (not _mf_grid(args["voltage_v"], 1000) or not _mf_grid(args["current_a"], 0.001) or args["voltage_v"] * args["current_a"] > 600):
            return fail("INVALID_ARGUMENT")
        if cap == "motion.set_two_theta":
            if not _mf_grid(args["angle_rad"], RIGAKU_MINIFLEX600_STEP_RAD):
                return fail("INVALID_ARGUMENT")
            if not inst["power"] or inst["door"] != 0:
                return fail("PRECONDITION_FAILED")
        if cap == "xrd.configure_scan":
            if args["start_rad"] > args["stop_rad"] or not all(_mf_grid(args[k], RIGAKU_MINIFLEX600_STEP_RAD) for k in ("start_rad", "stop_rad", "step_rad")):
                return fail("INVALID_ARGUMENT")
            intervals = (args["stop_rad"] - args["start_rad"]) / args["step_rad"]
            if not _mf_number(intervals) or abs(intervals - round(intervals)) > 1e-7 or round(intervals) + 1 > RIGAKU_MINIFLEX600_MAX_POINTS:
                return fail("INVALID_ARGUMENT")
            count = round(intervals) + 1
            duration = count * args["integration_time_s"]
            if not _mf_number(duration) or duration <= 0:
                return fail("INVALID_ARGUMENT")
        if cap == "xrd.start_scan":
            if not inst["power"] or inst["door"] != 0 or not inst["cooling_ready"] or inst["cooling"] is None or inst["sample"] is None or inst["sample"] != inst["response_sample"] or inst["response"] is None or inst["method"] is None or any(inst[k] is None for k in ("voltage", "current", "slit")):
                return fail("PRECONDITION_FAILED")
            if inst["response_configuration"] != (inst["voltage"], inst["current"], inst["slit"]):
                return fail("PRECONDITION_FAILED")
            method, response = inst["method"], inst["response"]
            if response[0]["two_theta_rad"] > method["start_rad"] or response[-1]["two_theta_rad"] < method["stop_rad"]:
                return fail("PRECONDITION_FAILED")
            first_end = host.time_s + method["integration_time_s"]
            last_end = host.time_s + method["point_count"] * method["integration_time_s"]
            max_counts = max(p["intensity_cps"] for p in response) * method["integration_time_s"]
            if not all(_mf_number(v) for v in (first_end, last_end, max_counts)) or first_end <= host.time_s or (method["point_count"] > 1 and last_end <= first_end):
                return fail("INVALID_ARGUMENT")
        if cap == "xrd.read_pattern":
            return self._envelope(instance_id, request_id, result={"samples": inst["pattern"], "status": inst["status"], "synthetic": True})
        candidate, result = _mf_copy.deepcopy(inst), dict(args)
        if cap == "power.set":
            candidate["power"] = args["enabled"]
            if not args["enabled"] and candidate["running"]:
                host._stop(candidate, "power_lost")
        elif cap == "access.set_door_angle":
            candidate["door"] = args["angle_rad"]
        elif cap == "slot.set_sample":
            if candidate["sample"] != args["connection_id"]:
                candidate["sample"] = args["connection_id"]
                candidate["response"], candidate["response_sample"], candidate["response_configuration"] = None, None, None
                host._reset(candidate)
        elif cap == "xray.configure":
            candidate["voltage"], candidate["current"] = args["voltage_v"], args["current_a"]
        elif cap == "xrd.configure_scan":
            candidate["method"] = {**args, "point_count": count}
            host._reset(candidate)
        elif cap == "xrd.set_slit":
            candidate["slit"] = args["slit_id"]
        elif cap == "motion.set_two_theta":
            candidate["two_theta"] = args["angle_rad"]
        elif cap == "xrd.start_scan":
            host._reset(candidate)
            candidate["started_at"], candidate["running"], candidate["status"] = host.time_s, True, "running"
            candidate["two_theta"] = candidate["method"]["start_rad"]
            result = {"status": "running", "synthetic": True}
        elif cap == "xrd.stop_scan":
            if candidate["running"]:
                host._stop(candidate, "stopped")
            result = {"status": candidate["status"], "synthetic": True}
        try:
            host._commit(instance_id, candidate)
        except Exception:
            return fail("DEVICE_ERROR", "failed")
        return self._envelope(instance_id, request_id, result=result)

    def write(self, *, instance_id, state_id, value, request_id):
        with self.__host._lock:
            if self._instance(instance_id) is None:
                return self._error(instance_id, "UNKNOWN_INSTANCE", request_id)
            slots = self.__host._metadata["asset_types"][RIGAKU_MINIFLEX600_TYPE]["state_slots"]
            if not isinstance(state_id, str) or state_id not in slots or not self._visible(slots[state_id]) or slots[state_id]["write_via"] is None:
                return self._error(instance_id, "ACCESS_DENIED", request_id)
            route = slots[state_id]["write_via"]
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={**route["fixed_arguments"], route["parameter"]: value}, request_id=request_id)

    def logs(self):
        with self.__host._lock:
            return _mf_copy.deepcopy(self.__host._logs[self.__principal])


def write_rigaku_miniflex600_runtime_reports(output_directory):
    import datetime
    import importlib.util
    import io
    import pathlib
    import platform
    import unittest
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("test_basic40_measure_rigaku_miniflex600", root / "tests/test_basic40_measure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(module.RigakuMiniFlex600RuntimeTests))
    metadata = export_rigaku_miniflex600_contract_metadata()
    caps = metadata["asset_types"][RIGAKU_MINIFLEX600_TYPE]["capabilities"]
    report = {"catalog_id": "CHEM-160", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "runtime": "standard-library Python; synthetic fixtures only; no Blender"},
        "method": "unittest: uniform entrypoints, SI limits/quantization, door/sample/cooling/tube interlocks, deterministic explicit-host scans, count integration, partial stops, monotonic shared time, rejection atomicity, instance/session isolation, in-memory idempotency, schemas and declared effects, scene callback boundaries",
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "result": "passed" if result.wasSuccessful() else "failed",
        "passed_capabilities": sorted(k for k, v in caps.items() if v["availability"] == "implemented") if result.wasSuccessful() else [],
        "verified_unsupported": ["xrd.phase_search", "xrd.refine"] if result.wasSuccessful() else [],
        "output": stream.getvalue(), "synthetic": True,
        "not_run": ["real_hardware", "robot_execution", "contact_solver", "Blender_evaluated_pose", "process_filesystem_security_isolation", "real_diffraction_accuracy", "phase_identification", "rietveld_refinement", "instrument_broadening", "preferred_orientation", "poisson_count_noise", "radiation_shielding", "coolant_thermal_hydraulics"],
        "scene_scope": "Only recording/failing callbacks and scene snapshot mapping are tested here; the parent owns evaluated Blender validation separately.",
        "boundaries": metadata["limitations"]}
    directory = pathlib.Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (("runtime_validation.json", report), ("runtime_metadata.json", metadata)):
        (directory / name).write_text(_mf_json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


# CHEM-160 template-backed powder samples (RRUFF/COD XRD library, scripts/basic40_templates/rigaku_miniflex600.py); host-only, additive.
def _mf_template_module():
    """Lazy import of the public-data XRD template extension."""
    try:
        from .basic40_templates import rigaku_miniflex600 as module
    except ImportError:
        from basic40_templates import rigaku_miniflex600 as module
    return module


def _mf_host_load_template_sample(self, instance_id, phases):
    return _mf_template_module().load_template_sample(self, instance_id, phases)


def _mf_template_sample_truth(self, instance_id):
    return _mf_template_module().template_sample_truth(self, instance_id)


RigakuMiniFlex600Runtime.host_load_template_sample = _mf_host_load_template_sample
RigakuMiniFlex600Runtime.template_sample_truth = _mf_template_sample_truth
# END CHEM-160 RIGAKU MINIFLEX600 APPEND-ONLY RUNTIME

# CHEM-102 independent review: append-only fixes for failed-request replay and
# representable host time; no prior asset classes or blocks are modified.
_kf899_review_original_invoke = Metrohm899Session.invoke


def _kf899_reviewed_invoke(self, *, instance_id, capability_id, arguments, request_id):
    host = self._kf899_host
    with host._kf899_lock:
        response = _kf899_review_original_invoke(self, instance_id=instance_id,
            capability_id=capability_id, arguments=arguments, request_id=request_id)
        if self._kf899_allowed(instance_id) and isinstance(request_id, str) and request_id and isinstance(capability_id, str) and isinstance(arguments, dict):
            try:
                fingerprint = _kf899_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            except (ValueError, TypeError, OverflowError):
                return response
            key = (self._kf899_principal, instance_id, request_id)
            if key not in host._kf899_requests:
                # In particular cache DEVICE_ERROR/unknown outcomes: repeating
                # a logical request must not replay external scene side effects.
                host._kf899_requests[key] = (fingerprint, _kf899_copy.deepcopy(response))
        return response


Metrohm899Session.invoke = _kf899_reviewed_invoke


def _kf899_reviewed_step(self, dt_s):
    """Common-clock step with precommit finite/representable-time validation.

    Process states and time commit together after all callbacks return. The
    callbacks are per-instance, not a transaction across scene instances: an
    exception may leave earlier external scene callbacks applied. The trusted
    host must reconcile all affected scene instances after any callback error.
    """
    with self._kf899_lock:
        if not _kf899_number(dt_s) or dt_s < 0 or not _kf899_number(self.time_s + dt_s):
            raise ValueError("Finite nonnegative time increment required")
        if dt_s == 0:
            return {"time_s": self.time_s, "synthetic": True}
        end_time = self.time_s + float(dt_s)
        if end_time <= self.time_s:
            raise ValueError("Positive increment is not representable at current host time")
        candidates = _kf899_copy.deepcopy(self._kf899_instances)
        for iid, instance in candidates.items():
            s = instance["state"]
            if not s["coulometry.running"]:
                continue
            pre_elapsed = s["measurement.elapsed_s"]
            checks = (pre_elapsed + dt_s, s["settings.current_a"] * dt_s,
                s["truth.water_remaining_kg"] + s["truth.background_drift_kg_s"] * dt_s,
                s["settings.drift_compensation_kg_s"] * (pre_elapsed + dt_s))
            if not all(_kf899_number(v) for v in checks):
                raise ValueError("Requested integration exceeds finite numerical range")
            remaining_dt, advanced = float(dt_s), 0.0
            drift = s["truth.background_drift_kg_s"]
            rate = s["settings.current_a"] * KF899_KG_PER_COULOMB
            extract_remaining = max(0.0, s["settings.extraction_time_s"] - pre_elapsed)
            extract = min(remaining_dt, extract_remaining)
            s["truth.water_remaining_kg"] += drift * extract
            remaining_dt -= extract
            advanced += extract
            s["coulometry.status"] = "EXTRACTING" if remaining_dt == 0 and extract_remaining > extract else "TITRATING"
            if remaining_dt > 0:
                water, net_rate = s["truth.water_remaining_kg"], rate - drift
                depletion_time = water / net_rate if net_rate > 0 else float("inf")
                drive = min(remaining_dt, depletion_time)
                s["measurement.charge_c"] += s["settings.current_a"] * drive
                s["truth.water_remaining_kg"] = max(0.0, water - net_rate * drive)
                remaining_dt -= drive
                advanced += drive
                if depletion_time <= drive:
                    s["truth.water_remaining_kg"] = 0.0
                    s["coulometry.status"] = "HOLDING"
                    hold_remaining = max(0.0, s["settings.endpoint_hold_s"] - s["endpoint.hold_elapsed_s"])
                    hold = min(remaining_dt, hold_remaining)
                    s["measurement.charge_c"] += drift * hold / KF899_KG_PER_COULOMB
                    s["endpoint.hold_elapsed_s"] += hold
                    advanced += hold
                    if s["endpoint.hold_elapsed_s"] >= s["settings.endpoint_hold_s"]:
                        s["coulometry.running"] = False
                        s["coulometry.status"] = "COMPLETED"
            s["measurement.elapsed_s"] = pre_elapsed + advanced
            s["measurement.sample_time_s"] = self.time_s + advanced
            if advanced > 0 and (s["measurement.elapsed_s"] <= pre_elapsed or s["measurement.sample_time_s"] <= self.time_s):
                raise ValueError("Acquisition interval is not representable at current host time")
            generated = s["measurement.charge_c"] * KF899_KG_PER_COULOMB
            compensation = s["settings.drift_compensation_kg_s"] * s["measurement.elapsed_s"]
            if not _kf899_number(generated) or not _kf899_number(compensation):
                raise ValueError("Integration exceeds finite numerical range")
            s["measurement.water_mass_kg"] = max(0.0, generated - compensation)
            mass = s["settings.sample_mass_kg"]
            s["measurement.water_fraction"] = None if mass is None else s["measurement.water_mass_kg"] / mass
            if any(type(value) in (int, float) and not _kf899_number(value) for value in s.values()):
                raise ValueError("Integration exceeds finite numerical range")
            instance["revision"] += 1
            instance["curve"].append({**_kf899_reading(s), "sample_time_s": s["measurement.sample_time_s"]})
        # No callback runs before all instances pass numerical validation.
        for iid, instance in candidates.items():
            if instance != self._kf899_instances[iid]:
                self._kf899_sync(iid, instance["state"])
        self._kf899_instances = candidates
        self.time_s = end_time
        return {"time_s": self.time_s, "synthetic": True}


Metrohm899Host.step = _kf899_reviewed_step
_kf899_review_original_metadata = export_metrohm899_contract_metadata


def export_metrohm899_contract_metadata():
    result = _kf899_review_original_metadata()
    result["boundaries"].extend([
        "Failed scene callback outcomes are cached by logical request ID; retry does not replay the callback. A new request after reconciliation is explicit host/operator action.",
        "Host step commits process/time together, but callbacks across scene instances are not transactional. A later callback failure can leave earlier scene instances changed; host must reconcile every affected scene instance.",
        "Positive host increments and nonzero acquisition intervals that cannot be represented at the current floating-point time are rejected before callbacks or process mutation.",
        "Slot installation/removal is a powered-off, idle scene configuration with cell-before-electrode dependencies. It does not simulate handling force, reagent spills, physical transfers or sample-container identity changes."])
    return result

# CHEM-102 reproducible dedicated report writer; no other asset outputs touched.
def write_metrohm899_runtime_reports(output_directory):
    import datetime
    import importlib.util
    import io
    import pathlib
    import platform
    import subprocess
    import unittest
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("test_basic40_measure_metrohm899", root / "tests/test_basic40_measure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.Metrohm899RuntimeTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    meta = export_metrohm899_contract_metadata()
    report = {"catalog_id": "CHEM-102", "asset_type_id": METROHM899_TYPE,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "shell_utc_at_recording": subprocess.check_output(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], text=True).strip(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "method": "unittest Metrohm899RuntimeTests: SI I/Q/m conservation, analytic extraction/depletion/hold with drift compensation and step partitioning; every API output schema and state effect; slots and null drift; retained stop/power effects; two active instances and common representable clock; request idempotency including callback failures; truth/curve/copy filtering; process rollback and explicitly partial external scene callback failure",
        "command": "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts:tests python3 -m unittest test_basic40_measure.Metrohm899RuntimeTests -v",
        "report_writer": "scripts.basic40_runtime_measure.write_metrohm899_runtime_reports(output_directory)",
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "result": "passed" if result.wasSuccessful() else "failed", "synthetic": True,
        "verified_layers": ["process", "host"] if result.wasSuccessful() else [],
        "capabilities_verified": sorted(meta["capabilities"]) if result.wasSuccessful() else [],
        "scene_scope": "Recording/failing callback tests only; evaluated Blender pose requires a separate geometry verification record. Cross-instance callbacks are not an external scene transaction.",
        "not_run": ["real_hardware", "robot_execution", "contact_solver", "real_reagent_or_electrode_kinetics", "manufacturer_accuracy_or_noise", "automatic_current_firmware", "process_filesystem_sandbox", "evaluated_blender_pose_in_runtime_tests"],
        "boundaries": meta["boundaries"], "output": stream.getvalue()}
    directory = pathlib.Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (("runtime_validation.json", report), ("runtime_manifest.json", meta)):
        (directory / name).write_text(_kf899_json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "runtime_test_report.txt").write_text(stream.getvalue(), encoding="utf-8")
    return report
