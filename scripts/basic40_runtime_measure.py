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
        self._raw_samples = _xpr_deque()                                     # (time, dt, raw force) of the stability window (settle check)
        self._settled_since = None                                          # time of the first sample of the current settled run

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
        self._raw_samples.clear()
        self._settled_since = None
        if clear_filter:
            self.filtered_force_N = None
            self.filter_age_s = 0.0

    def _settled(self, dt_s, cutoff):
        """Settle check of the stability detector (2026-10-22, CHEM-001 1.3.1).  The N-division window alone let the flag rise while the
        low-pass reading was still approaching a new load (Standard: about 68 d short of the settled value), and kept it up for a moment after
        a load change.  The reading must also have reached its input: |input force now - filtered force| / g <= max(0.5 d, 4 s) and
        |time mean of the input over the stability window - filtered force| / g <= max(0.5 d, 4 s / sqrt(n)), s = robust input noise
        (1.4826 / sqrt(2) x median of successive input differences, 0 without noise; a load step does not count as noise), n = effective
        number of input samples in the window.  The update holds the check for the stability window before the flag rises.  0.5 d follows the
        MT-SICS stability criteria (a tolerance in digits held for an observation time; USTB / I100 examples 0.5-1 d), so a stable reading is
        within one display step of the settled one; the noise terms keep the specified white-noise experiment able to reach STABLE
        (assumption)."""
        raw = self._raw_samples
        raw.append((self.sample_time_s, dt_s, self.raw_force_N))
        while raw and raw[0][0] <= cutoff + 1e-10:
            raw.popleft()
        w = [min(d, t - cutoff) for t, d, _ in raw]
        total = sum(w)
        if total <= 0:
            return False
        forces = [f for _, _, f in raw]
        steps = sorted(abs(b - a) for a, b in zip(forces, forces[1:]))
        noise = 1.4826 / _xpr_math.sqrt(2) * steps[len(steps) // 2] if steps else 0.0
        mean = sum(wi * f for wi, f in zip(w, forces)) / total
        n_eff = total * total / sum(wi * wi for wi in w)
        floor = 0.5 * self.readability_kg * self.g_cal
        return (abs(self.raw_force_N - self.filtered_force_N) <= max(floor, 4 * noise) + 1e-15
                and abs(mean - self.filtered_force_N) <= max(floor, 4 * noise / _xpr_math.sqrt(n_eff)) + 1e-15)

    def _settle_hold(self, dt_s, cutoff):
        """_settled held without a break for the stability window (the settling time), counted like the window: from the first settled sample."""
        if not self._settled(dt_s, cutoff):
            self._settled_since = None
            return False
        if self._settled_since is None:
            self._settled_since = self.sample_time_s
        return self.sample_time_s - self._settled_since + 1e-10 >= self.settling_s - dt_s

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
        settled = self._settle_hold(dt_s, cutoff)
        self.stable = bool(inside and warmed and settled and self.elapsed_s + 1e-10 >= self.settling_s)
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
        "asset_version": "1.0.1", "state_slots": slots, "capabilities": caps, "parameters": parameters}},
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
    return {"asset_types": {GAMRY_REFERENCE620_TYPE: {"catalog_id": "CHEM-103", "model": "Gamry Reference 620", "asset_version": "1.1.1",   # 1.1.1: template.read example (2026-10-22)
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


def _m914_standard_tolerance(slot, value):
    """2026-10-22 (1.2.4): the entered standard identifies the standard in the cell to the meter's resolution, as the buffer recognition maps a
    value to one entry of the selected buffer series (manual p.55, p.88-99): 0.002 pH (two digits of the 0.001 pH display; the value of another
    series, e.g. a nominal 4.00 for the NIST 4.005 buffer, stays a different standard); conductance and DO standards 0.5 % relative.  Before,
    float equality was required (6.864 vs 6.865 refused)."""
    return 0.002 + 1e-12 if slot == "ph" else 0.005 * abs(value) + 1e-12


def _m914_calibrate_refusal(host, inst, args):
    """The reason electrochemistry.calibrate is refused (None: accepted); the code stays PRECONDITION_FAILED, the message names the cause."""
    slot, value, truth = args["slot"], args["standard_value"], inst["truth"]
    if inst["running"]:
        return "A recording is running; stop it before calibrating."
    if not inst["power"]:
        return "The meter is switched off."
    if inst["selected"] != slot or not inst["installed"][slot]:
        return "Select the installed %s channel before calibrating it." % slot
    if truth["standard_slot"] != slot or truth["standard_value"] is None:
        return "No %s calibration standard is in the measuring cell (the host has not supplied one)." % slot
    if abs(truth["standard_value"] - value) > _m914_standard_tolerance(slot, truth["standard_value"]):
        return "The entered standard %g does not match the standard in the cell (buffer recognition tolerance %g)." % (value, _m914_standard_tolerance(slot, truth["standard_value"]))
    if inst["sample_time"] != host.time_s or not inst["stable"]:
        return "The reading is not stable yet (wait for the endpoint criterion on a current reading)."
    return None


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
        "electrochemistry.calibrate": cap({"slot": channel, "standard_value": _m914_num("1", -13, 20)}, {"slot": channel, "calibrated": boolean, "synthetic": {"type": "boolean", "const": True}}, {"slot": "ph", "standard_value": 7}, "有界单点合成校准：标准值按slot单位解释（ph=1、conductance=S、do=kg/m3）；建立加性偏置修正，不声称厂家校准算法、精度或溯源。", calibration_effects + measurement_effects, [f"probe.{n}.calibrated" for n in METROHM914_CHANNELS], "供电、选中且安装槽、当前新鲜读数稳定、未记录；宿主明确标定匹配槽与已知标准值，输入的标准值与池中标准值之差不超过识别容差（pH 0.002，即0.001 pH显示的两个数字，其他缓冲系列的值如4.00对NIST 4.005仍算不同标准；电导与溶解氧相对0.5%；手册p.55、p.88–99缓冲表），校准用输入值；标准值在该槽规格范围内；拒绝时消息写明原因。"),
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
    return {"asset_types": {METROHM914_TYPE: {"catalog_id": "CHEM-005", "model": "Metrohm | 914 pH/DO/Conductometer", "asset_version": "1.1.0", "state_slots": slots, "capabilities": caps, "parameters": params}},
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

    def _error(self, instance_id, code, request_id=None, status="rejected", message=None):
        messages = {"UNKNOWN_INSTANCE": "Instance unavailable to this session.", "UNKNOWN_CAPABILITY": "Capability unavailable.",
            "INVALID_ARGUMENT": "Arguments do not match schema or bounded configuration.", "ACCESS_DENIED": "Request is outside session access policy.",
            "PRECONDITION_FAILED": "Required operating state, calibration fixture or configuration unavailable.",
            "REQUEST_CONFLICT": "Request ID already belongs to different arguments.", "DEVICE_ERROR": "Scene synchronization did not complete."}
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": message or messages[code], "retryable": False,
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
        if cap == "electrochemistry.calibrate":
            why = _m914_calibrate_refusal(host, inst, args)
            if why:
                return self._error(instance_id, "PRECONDITION_FAILED", request_id, message=why)
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
    return {"asset_type_id": METROHM899_TYPE, "catalog_id": "CHEM-102", "asset_version": "1.1.0", "state_slots": state,
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
    return {"asset_types": {METROHM_OMNIS_TYPE: {"catalog_id": "CHEM-101", "model": "Metrohm | OMNIS Professional Titrator 2.1001.0320", "asset_version": "1.1.0", "state_slots": slots, "capabilities": caps, "parameters": params}},
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
    return {"asset_types": {SPINSOLVE80_TYPE: {"catalog_id": "CHEM-068", "model": "Magritek | Spinsolve 80 Carbon", "asset_version": "1.1.1", "state_slots": slots, "capabilities": caps, "parameters": parameters}}, "mechanisms": mechanisms, "scene_snapshot_keys": list(SPINSOLVE80_SCENE_KEYS), "limitations": limitations,
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

    def _error(self, instance_id, code, request_id=None, message=None):
        return self._envelope(instance_id, request_id, status="failed" if code == "DEVICE_ERROR" else "rejected", error={"code": code, "message": message or ("Request unavailable under current arguments, state or access policy." if code != "DEVICE_ERROR" else "External scene synchronization failed; process not committed."), "retryable": False, "side_effects": "unknown" if code == "DEVICE_ERROR" else "none", "progress": None})

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
        # 2026-10-22 (1.1.1): the refusals keep PRECONDITION_FAILED (the only declared code) and say why
        why = lambda m: self._error(instance_id, "PRECONDITION_FAILED", request_id, m)   # noqa: E731
        if cap == "sample.insert" and inst["sample_id"] is not None:
            return why("A sample tube is already in the magnet; remove it first.")
        if cap == "sample.insert" and args["sample_id"] not in inst["samples"]:
            return why("No sample %r has been prepared by the host." % args["sample_id"])
        if cap == "acquisition.start":
            cfg = inst["method"]
            if not inst["power"]:
                return why("The spectrometer is switched off.")
            if inst["sample_id"] is None:
                return why("No sample tube is in the magnet.")
            if cfg is None:
                return why("No acquisition is configured (acquisition.configure).")
            signals = inst["samples"][inst["sample_id"]][cfg["nucleus"]]
            nyquist = cfg["points"] / cfg["acquisition_time_s"] / 2
            if not signals:
                return why("The sample has no %s signal to acquire." % cfg["nucleus"])
            if any(abs(p["frequency_offset_hz"]) >= nyquist for p in signals):
                return why("A signal lies outside the spectral window (+-%.0f Hz): more points or a shorter acquisition time." % nyquist)
            if host.time_s + cfg["scans"] * (cfg["relaxation_delay_s"] + cfg["acquisition_time_s"]) <= host.time_s:
                return why("The scan is too short to advance the clock.")
        if cap == "nmr.integrate":
            a, b, spectrum = args["frequency_min_hz"], args["frequency_max_hz"], inst["spectrum"]
            if not spectrum:
                return why("There is no spectrum yet.")
            if not spectrum[0]["frequency_offset_hz"] <= a < b <= spectrum[-1]["frequency_offset_hz"]:
                return why("The integration range must lie inside the spectrum (%.1f..%.1f Hz) with min < max." % (spectrum[0]["frequency_offset_hz"], spectrum[-1]["frequency_offset_hz"]))
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
    return {"asset_types": {RIGAKU_MINIFLEX600_TYPE: {"catalog_id": "CHEM-160", "model": "Rigaku | MiniFlex600，外置循环水配置", "asset_version": "1.1.0", "state_slots": slots, "capabilities": caps, "parameters": params}},
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


# >>> CHEM-001 XPR204E terminal
# Device layer of the XPR204E touch terminal (operating manual references/manuals/mt_xpr204e/, 'p.N' = PDF page): balance settings, tolerance
# profiles, methods / tasks / results, test weights, routine tests and internal / external adjustment as procedures, users and groups,
# blocking, date / time / language, motorized doors, standby, leveling aid, messages and StatusLight, plus the front panel (panel_ui.json run
# by scripts/asset_runtime/_panel_ui.py).  Appended to the CHEM-001 runtime above, whose capabilities keep their meaning.  Sensor error
# parameters (span, zero offset, zero drift, internal weight) are settable by the trusted host and are 0 (ideal) by default.
import copy as _xt_copy
import datetime as _xt_dt
import hashlib as _xt_hashlib
import os as _xt_os
import math as _xt_math
import tempfile as _xt_tempfile
from collections import deque as _xt_deque
from pathlib import Path as _xt_Path

XPR204E_ASSET_VERSION = "1.3.1"   # 1.3.0 (2026-10-20): XPR terminal + draft shield geometry (contract only; runtime behaviour unchanged); 1.3.1 (2026-10-22): stability settle check
XPR204E_ASSET_DIR =_xt_Path(__file__).resolve().parents[1] / "assets/basic40_batch02/t4_measure/mt_xpr204e"
_XT_G0 = 9.80665
_XT_EPOCH = _xt_dt.datetime(2026, 1, 1, 0, 0, 0)        # device clock at simulation time 0 until clock.set (assumption)
_XT_TIMEOUT_S = 40.0                                     # waiting for stability (zero / tare / capture): MT-SICS M67 factory value
_XT_INTERNAL_KG = 0.2                                    # internal adjustment load: the 200 g nominal load of the data sheet (assumption)
_XT_INTERNAL_S = 20.0                                    # duration of an internal adjustment (assumption)
_XT_LEVEL_RAD = _xt_math.radians(0.2)                    # level limit = the runtime's stability gate (assumption)
_XT_BUBBLE_RAD = _xt_math.radians(0.6)                   # tilt shown at the rim of the level indicator (assumption)
_XT_FOOT_PITCH_M = 0.001                                 # leveling-foot pitch per turn (balance_runtime.foot_extension_m)
_XT_FOOT_SPAN_M = 0.15                                   # distance of the two leveling feet (assumption from the 195 mm width)
_XT_MAX_EVENTS = 500                                     # history lists keep the last 500 entries (p.72-73)

XPR_METHOD_TYPES = ("General weighing", "General weighing (itemized)", "Density determination", "Piece Counting")
XPR_TEST_TYPES = ("Eccentricity test", "Repeatab. - 1 TP", "Repeatab. - Tare - 1 TP", "Sensitivity - 1 TP", "Sensitivity - 2 TP",
                  "Sensitivity - Tare - 1 TP", "Sensitivity - Tare - 2 TP")
XPR_LANGUAGES = ("English", "Deutsch", "Français", "日本語", "中文", "Español", "Italiano", "Русский", "Português", "Polski", "Magyar", "Čeština")
XPR_KEYBOARDS = ("English", "German", "French", "Spanish", "Japanese", "Simplified Chinese", "Russian", "Czech", "Polish", "Hungarian")
XPR_DATE_FORMATS = ("D.MMM.YYYY", "MMM D YYYY", "DD.MM.YYYY", "MM/DD/YYYY", "YYYY-MM-DD", "YYYY/MM/DD", "YYYY年M月D日")
XPR_TIME_FORMATS = ("24:MM", "12:MM", "24.MM", "12.MM")
XPR_TIME_ZONES = ("UTC", "Europe/Zurich", "Europe/London", "America/New_York", "America/Chicago", "America/Los_Angeles", "Asia/Shanghai",
                  "Asia/Tokyo", "Asia/Kolkata", "Australia/Sydney")
XPR_ZONE_H = {"UTC": 0.0, "Europe/Zurich": 1.0, "Europe/London": 0.0, "America/New_York": -5.0, "America/Chicago": -6.0,
              "America/Los_Angeles": -8.0, "Asia/Shanghai": 8.0, "Asia/Tokyo": 9.0, "Asia/Kolkata": 5.5, "Australia/Sydney": 10.0}   # standard time
XPR_TEMP_TRIGGER_K = 1.0                                   # 'Start after temperature change': a change of 1 degC starts it (p.129)
XPR_TEMP_REF_C = 20.0                                      # reference temperature of the sensitivity temperature coefficient (assumption)
XPR_WEIGHT_CLASSES = ("E1", "E2", "F1", "F2", "M1", "M2", "M3", "ASTM000", "ASTM00", "ASTM0", "ASTM1", "ASTM2", "ASTM3", "ASTM4", "ASTM5",
                      "ASTM6", "ASTM7", "Own")
XPR_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
XPR_LABELS = ("5 large fields", "5 small fields", "10 small fields", "1D barcode with 3 large fields", "1D barcode with 3 small fields",
              "1D barcode with 6 small fields", "2D barcode with 5 large fields", "2D barcode with 5 small fields",
              "2D barcode with 2 large fields and 6 small fields", "2D barcode with 8 small fields")
# weighing units: grams per unit (MT-SICS M21 unit list, related document; the XPR204E model list is not in the operating manual)
XPR_UNITS = {"g": 1.0, "mg": 1e-3, "µg": 1e-6, "kg": 1e3, "ct": 0.2, "N": 1e3 / _XT_G0, "lb": 453.59237, "oz": 28.349523125, "ozt": 31.1034768,
             "GN": 0.06479891, "dwt": 1.55517384, "mom": 3.75, "msg": 4.6083, "tlh": 37.429, "tls": 37.7994, "tlt": 37.5, "tola": 11.6638038,
             "baht": 15.244}
# Environment of a tolerance profile = low-pass time constant and stability band (N divisions); Very stable / Standard / Very unstable are
# exactly the runtime's Low / Medium / High models, the two others lie between them (engineering assumptions, like the three).
XPR_ENVIRONMENTS = {"Very stable": (0.25, 512), "Stable": (0.5, 362), "Standard": (1.0, 256), "Unstable": (2.0, 181), "Very unstable": (4.0, 128)}
_XT_ENV_OF_FILTER = {"Low": "Very stable", "Medium": "Standard", "High": "Very unstable"}
_XT_FILTER_OF_ENV = {"Very stable": "Low", "Stable": "Low", "Standard": "Medium", "Unstable": "High", "Very unstable": "High"}
XPR_VALUE_RELEASE = {"Very fast": 0.5, "Fast": 0.75, "Fast and reliable": 1.0, "Reliable": 1.5, "Very reliable": 2.0}   # x stability hold
XPR_READABILITY = {"1d": 1, "2d": 2, "5d": 5, "10d": 10, "100d": 100, "1000d": 1000}
XPR_PERMISSIONS = ("block_unblock", "configure_methods", "exclude_overwrite", "cancel_task", "configure_system", "configure_users",
                   "execute_service", "start_external_adjustment", "show_change_history", "configure_tests", "start_tests")
_ACT = ("Active", "Inactive")


# ---------------------------------------------------------------- settings tables (shared with the panel spec builder)
def _t_bool(key, label, default, page, **kw):
    return dict(key=key, label=label, type="bool", default=default, page=page, **kw)


def _t_enum(key, label, options, default, page, **kw):
    return dict(key=key, label=label, type="enum", options=list(options), default=default, page=page, **kw)


def _t_num(key, label, lo, hi, default, page, unit=None, integer=False, **kw):
    return dict(key=key, label=label, type="int" if integer else "num", min=lo, max=hi, default=default, page=page, unit=unit, **kw)


def _t_text(key, label, lo, hi, default, page, **kw):
    return dict(key=key, label=label, type="text", min_len=lo, max_len=hi, default=default, page=page, **kw)


def _t_multi(key, label, options, default, page, **kw):
    return dict(key=key, label=label, type="multi", options=list(options), default=list(default), page=page, **kw)


_IP = dict(pattern=r"^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$")
XPR_SETTINGS = [
    # Settings > Balance > Weighing / Quality (p.78-79)
    _t_enum("quality.leveling_warning", "Leveling warning", ("Inactive", "Optional leveling", "Forced leveling"), "Optional leveling", "p.78",
            section="weighing_quality"),
    _t_bool("quality.gwp_approved", "GWP Approved mode", False, "p.78", section="weighing_quality", readonly="service technician only"),
    _t_bool("quality.change_history", "Change history", False, "p.79", section="weighing_quality"),
    _t_bool("quality.balance_recal_reminder", "Balance recalib. reminder", True, "p.79", section="weighing_quality"),
    _t_num("quality.balance_recal_days", "Days in advance", 0, 400, 30, "p.79", unit="days", integer=True, section="weighing_quality",
           visible=("quality.balance_recal_reminder", True)),
    _t_enum("quality.calib_expired_action", "Action when calib. expired", ("None", "Block"), "None", "p.79", section="weighing_quality"),
    _t_num("quality.calib_block_days", "Days before blocking", 0, 400, 30, "p.79", unit="days", integer=True, section="weighing_quality",
           visible=("quality.calib_expired_action", "Block")),
    _t_bool("quality.weight_recal_reminder", "Weight recalib. reminder", False, "p.79", section="weighing_quality"),
    _t_num("quality.weight_recal_days", "Days in advance", 0, 400, 30, "p.79", unit="days", integer=True, section="weighing_quality",
           visible=("quality.weight_recal_reminder", True)),
    _t_bool("quality.service_reminder", "Service reminder", False, "p.79", section="weighing_quality"),
    _t_num("quality.service_days", "Days in advance", 0, 400, 30, "p.79", unit="days", integer=True, section="weighing_quality",
           visible=("quality.service_reminder", True)),
    # Automatic weight value output (p.81-82)
    _t_enum("output.mode", "Output mode", ("Results", "Continuous"), "Results", "p.81", section="output"),
    _t_enum("output.target", "Target", ("HID", "HID / MT-SICS", "MT-SICS", "MT-SICS configurable"), "HID", "p.81", section="output",
            visible=("output.mode", "Results")),
    _t_bool("output.result_id_1", "Result ID 1", False, "p.81", section="output", visible=("output.mode", "Results")),
    _t_bool("output.result_id_2", "Result ID 2", False, "p.81", section="output", visible=("output.mode", "Results")),
    _t_bool("output.date", "Date", False, "p.81", section="output", visible=("output.mode", "Results")),
    _t_bool("output.time", "Time", False, "p.81", section="output", visible=("output.mode", "Results")),
    _t_bool("output.net_indicator", "Net indicator", False, "p.81", section="output", visible=("output.mode", "Results")),
    _t_num("output.net_indicator_length", "Net indicator field length", 1, 2, 2, "p.82", integer=True, section="output",
           visible=("output.net_indicator", True)),
    _t_num("output.weight_field_length", "Weight field length", 0, 20, 1, "p.82", integer=True, section="output", visible=("output.mode", "Results")),
    _t_enum("output.sign", "Sign", ("For all values", "For negative values"), "For negative values", "p.82", section="output",
            visible=("output.mode", "Results")),
    _t_enum("output.sign_position", "Sign position", ("Left of weight field", "Left of weight digits"), "Left of weight digits", "p.82",
            section="output", visible=("output.mode", "Results")),
    _t_enum("output.decimal_delimiter", "Decimal delimiter", (",", "."), ".", "p.82", section="output", visible=("output.mode", "Results")),
    _t_bool("output.unit", "Unit", True, "p.82", section="output", visible=("output.mode", "Results")),
    _t_num("output.unit_field_length", "Unit field length", 1, 6, 1, "p.82", integer=True, section="output", visible=("output.unit", True)),
    _t_enum("output.field_delimiter", "Field delimiter", ("None", "Space", "TAB", ",", ";"), "Space", "p.82", section="output",
            visible=("output.mode", "Results")),
    _t_enum("output.end_of_line", "End of line character", ("CRLF", "CR", "LF", "TAB", "None", "Enter"), "Enter", "p.82", section="output",
            visible=("output.mode", "Results")),
    _t_enum("output.updates_per_s", "Updates/sec.", (2, 5, 6, 10), 6, "p.82", section="output", visible=("output.mode", "Continuous")),
    _t_enum("output.format", "Format", ("MT-SICS", "PM", "AT/MT"), "MT-SICS", "p.82", section="output", visible=("output.mode", "Continuous")),
]
for _side, _own in (("left", "key_left"), ("right", "key_right")):          # Doors (p.83); each door key drives its own door (assumption)
    XPR_SETTINGS += [
        _t_enum(f"door_{_side}.drive_mode", "Drive mode", ("Motorized", "Manual"), "Motorized", "p.83", section=f"door_{_side}"),
        _t_num(f"door_{_side}.opening", "Door opening", 1, 100, 100, "p.83", unit="%", integer=True, section=f"door_{_side}",
               visible=(f"door_{_side}.drive_mode", "Motorized")),
        _t_bool(f"door_{_side}.key_left", "Door key left", _own == "key_left", "p.83", section=f"door_{_side}"),
        _t_bool(f"door_{_side}.key_right", "Door key right", _own == "key_right", "p.83", section=f"door_{_side}"),
        _t_bool(f"door_{_side}.devices", "Devices", False, "p.83", section=f"door_{_side}"),
        _t_bool(f"door_{_side}.automatic", "Automatic (Tare / Zero / Result)", False, "p.83", section=f"door_{_side}"),
    ]
XPR_SETTINGS += [
    # Date / Time / Language / Format (p.83-84; date and time themselves through clock.set)
    _t_enum("region.language", "Language", XPR_LANGUAGES, "English", "p.83", section="region"),
    _t_enum("region.time_zone", "Time zone", XPR_TIME_ZONES, "UTC", "p.83", section="region"),
    _t_enum("region.date_format", "Date format", XPR_DATE_FORMATS, "D.MMM.YYYY", "p.83", section="region"),
    _t_enum("region.time_format", "Time format", XPR_TIME_FORMATS, "24:MM", "p.83", section="region"),
    _t_enum("region.keyboard", "Keyboard layout", XPR_KEYBOARDS, "English", "p.84", section="region"),
    # Screen / StatusLight / Sound (p.84)
    _t_enum("screen.brightness", "Screen brightness", (20, 40, 60, 80, 100), 80, "p.84", unit="%", section="screen"),
    _t_enum("screen.sound_volume", "Sound volume", ("Inactive", "20 %", "40 %", "60 %", "80 %", "100 %"), "60 %", "p.84", section="screen"),
    _t_bool("screen.sound_key", "Sound on key press", True, "p.84", section="screen"),
    _t_bool("screen.sound_info", "Sound on info", True, "p.84", section="screen"),
    _t_bool("screen.sound_warning", "Sound on warning", True, "p.84", section="screen"),
    _t_bool("screen.sound_error", "Sound on error", True, "p.84", section="screen"),
    _t_enum("screen.status_light", "StatusLight", ("Active", "Active (without green light)", "Inactive"), "Active", "p.84", section="screen"),
    _t_enum("screen.status_light_brightness", "StatusLight brightness", (20, 40, 60, 80, 100), 60, "p.84", unit="%", section="screen",
            visible=("screen.status_light", ("Active", "Active (without green light)"))),
    # General (p.85; User management through user_management.set)
    _t_text("general.balance_id", "Balance ID", 0, 22, "", "p.85", section="general", pattern=r"^[A-Za-z0-9]*$"),
    _t_bool("general.standby", "Standby", True, "p.85", section="general"),
    _t_num("general.standby_wait", "Wait time", 0, 60, 10, "p.85", unit="min", integer=True, section="general", visible=("general.standby", True)),
    _t_bool("general.update_on_startup", "Software update on system start-up", True, "p.85", section="general"),
    _t_enum("general.export_directory", "Automatic export directory", ("USB storage device", "File server"), "USB storage device", "p.85",
            section="general"),
    # Modules / Dosing (p.85-86)
    _t_num("dosing.pressure_hold", "Pressure hold duration", 1, 180, 10, "p.85", unit="min", integer=True, section="dosing"),
    _t_num("dosing.label_copies", "Copies", 1, 99, 1, "p.85", integer=True, section="dosing", assumption="range of Copies not given; 1-99 assumed"),
    _t_enum("dosing.label_template", "Used template", XPR_LABELS, XPR_LABELS[0], "p.85-86", section="dosing"),
    # Interfaces (p.87)
    _t_text("ethernet.host_name", "Host name", 1, 22, "XPR204E", "p.87", section="ethernet", assumption="factory host name not given"),
    _t_enum("ethernet.network", "Network configuration", ("DHCP", "Manual"), "DHCP", "p.87", section="ethernet"),
    _t_text("ethernet.ip", "IP address", 7, 15, "192.168.0.10", "p.87", section="ethernet", visible=("ethernet.network", "Manual"), **_IP),
    _t_text("ethernet.subnet", "Subnet mask", 7, 15, "255.255.255.0", "p.87", section="ethernet", visible=("ethernet.network", "Manual"), **_IP),
    _t_text("ethernet.dns1", "DNS server (primary)", 7, 15, "0.0.0.0", "p.87", section="ethernet", visible=("ethernet.network", "Manual"), **_IP),
    _t_text("ethernet.dns2", "DNS server (secondary)", 7, 15, "0.0.0.0", "p.87", section="ethernet", visible=("ethernet.network", "Manual"), **_IP),
    _t_text("ethernet.gateway", "Default gateway", 7, 15, "0.0.0.0", "p.87", section="ethernet", visible=("ethernet.network", "Manual"), **_IP),
    _t_enum("bluetooth.activation", "Activation", ("Inactive", "Active"), "Inactive", "p.87", section="bluetooth"),
    # LabX / Services (p.89): only one of LabX / MT-SICS / Web service is enabled at a time
    _t_enum("services.labx", "LabX service", ("Inactive", "Network"), "Inactive", "p.89", section="services"),
    _t_num("services.labx_port", "Port", 1, 65535, 8001, "p.89", integer=True, section="services", visible=("services.labx", "Network"),
           assumption="factory port not given"),
    _t_enum("services.mtsics", "MT-SICS service", ("Inactive", "Network", "USB"), "Inactive", "p.89", section="services"),
    _t_num("services.mtsics_port", "Port", 1, 65535, 8001, "p.89", integer=True, section="services", visible=("services.mtsics", "Network"),
           assumption="factory port not given"),
    _t_enum("services.web", "Web service", ("Inactive", "Active"), "Inactive", "p.89", section="services"),
    _t_bool("services.file_server", "File server", False, "p.89", section="services"),
    _t_text("services.fs_server", "Server name", 0, 64, "", "p.63", section="services", visible=("services.file_server", True)),
    _t_text("services.fs_share", "Share name", 0, 64, "", "p.63", section="services", visible=("services.file_server", True)),
    _t_text("services.fs_import_path", "Import path", 0, 64, "Import", "p.66", section="services", visible=("services.file_server", True)),
    _t_text("services.fs_domain", "Domain name", 0, 64, "", "p.63", section="services", visible=("services.file_server", True)),
    _t_text("services.fs_user", "User name", 0, 64, "", "p.63", section="services", visible=("services.file_server", True)),
    _t_text("services.fs_password", "Password", 0, 64, "", "p.63", section="services", visible=("services.file_server", True), secret=True),
    # Balance menu > Users > General (p.75-76)
    _t_bool("users.automatic_logout", "Automatic logout", True, "p.75", section="users_general"),
    _t_num("users.logout_wait", "Wait time", 1, 60, 10, "p.75", unit="min", integer=True, section="users_general",
           visible=("users.automatic_logout", True)),
    _t_bool("users.user_proposals", "User proposals", True, "p.76", section="users_general"),
    _t_enum("users.password_reset", "Password reset", ("Allowed", "Not allowed"), "Allowed", "p.76", section="users_general"),
]
XPR_SETTING = {s["key"]: s for s in XPR_SETTINGS}
_XT_FREE_SECTIONS = ("screen",)                                  # any user may change these (p.77)
_XT_USER_SECTIONS = ("users_general",)                           # need Configure user management

_TPL_HEADER = ("Title", "Title text", "Date/time", "User name", "Signature", "Separating lines", "Group titles")
_TPL_BALANCE = ("Balance type", "Balance ID", "Balance serial number", "Software version")
_TPL_QUALITY = ("Tolerance profile", "Adjustment date/time", "Routine test name", "Routine test last execution date", "Routine test result",
                "GWP Approved state", "Level state", "MinWeigh state")
_TPL_TASK = ("Method name", "Method comment", "Task IDs", "Custom unit settings", "Automatic result settings", "Count", "Sum", "Average",
             "Minimum", "Maximum", "Range", "Standard deviation", "Relative standard deviation")
_TPL_ITEM = ("Show excluded weighing items", "Result state", "Result IDs", "GWP Approved state", "Level state", "MinWeigh state",
             "Tolerance state", "Target and tolerances")
_TPL_RESULT = ("Weight", "Tare weight", "Gross weight", "Info weight", "Date/time", "Stability")
_GW, _GWI, _DEN, _PCS = XPR_METHOD_TYPES
_ALL = XPR_METHOD_TYPES


def _m(entry, types=_ALL, **kw):
    return dict(entry, types=tuple(types), **kw)


XPR_METHOD_FIELDS = [
    # General (p.91)
    _m(_t_text("name", "Method name", 1, 22, "", "p.91", section="general")),
    _m(_t_text("comment", "Comment", 0, 128, "", "p.91", section="general")),
    _m(_t_bool("lock", "Lock method", False, "p.91", section="general")),
    # Density (p.101-102)
    _m(_t_enum("density.type", "Determination type", ("Liquid (pycnometer)", "Liquid (sinker)", "Solid"), "Solid", "p.101", section="density",
               create_only=True), (_DEN,)),
    _m(_t_enum("density.unit", "Density unit", ("g/cm3", "kg/m3", "g/l"), "g/cm3", "p.102", section="density"), (_DEN,)),
    _m(_t_enum("density.decimals", "Density value decimal places", (1, 2, 3, 4, 5), 3, "p.102", section="density"), (_DEN,)),
    _m(_t_bool("density.air_compensation", "Air density compensation", True, "p.102", section="density"), (_DEN,)),
]
for _kind, _ids, _types in (("task", "Task ID", ("Manual with default", "Automatic timestamp")),
                            ("result", "Result ID", ("Manual with default", "Automatic counter"))):
    _page = "p.91-92" if _kind == "task" else "p.92"
    XPR_METHOD_FIELDS.append(_m(_t_enum(f"ids.{_kind}_count", f"Number of {_kind} IDs", (0, 1, 2, 3), 1, _page, section="ids")))
    for _i in (1, 2, 3):
        _vis = (f"ids.{_kind}_count", tuple(range(_i, 4)))
        _desc = "Task ID" if _kind == "task" else "Sample ID"
        XPR_METHOD_FIELDS += [
            _m(_t_enum(f"ids.{_kind}_{_i}_type", f"{_ids} {_i}", _types, _types[0], _page, section="ids", visible=_vis)),
            _m(_t_text(f"ids.{_kind}_{_i}_description", f"{_kind.title()} description" if _kind == "task" else "Result description", 0, 32,
                       _desc if _i == 1 else f"{_ids} {_i}", _page, section="ids", visible=_vis,
                       assumption="factory descriptions 'Task ID' / 'Sample ID' from the screen picture p.18")),
            _m(_t_text(f"ids.{_kind}_{_i}_default", "Default value", 0, 32, "", _page, section="ids", visible=_vis,
                       visible2=(f"ids.{_kind}_{_i}_type", "Manual with default"))),
            _m(_t_text(f"ids.{_kind}_{_i}_prefix", "Prefix", 0, 32, "", _page, section="ids", visible=_vis,
                       visible2=(f"ids.{_kind}_{_i}_type", _types[1]))),
        ]
XPR_METHOD_FIELDS += [
    # Weighing (p.92-94)
    _m(_t_bool("weighing.smarttrac", "SmartTrac / Target weight / Tolerances", True, "p.92", section="weighing"), (_GW, _GWI, _PCS)),
    _m(_t_bool("weighing.show_info", "Show info weight", False, "p.92", section="weighing")),
    _m(_t_enum("weighing.info_unit", "Info unit", tuple(XPR_UNITS), "mg", "p.92", section="weighing", visible=("weighing.show_info", True),
               assumption="factory info unit not given")),
    _m(_t_bool("custom.active", "Define custom unit", False, "p.93", section="weighing"), (_GW, _GWI)),
    _m(_t_text("custom.name", "Name", 0, 6, "cu1", "p.93", section="weighing", visible=("custom.active", True)), (_GW, _GWI)),
    _m(_t_enum("custom.formula", "Formula", ("Multiplicative", "Divisive"), "Multiplicative", "p.93", section="weighing",
               visible=("custom.active", True)), (_GW, _GWI)),
    _m(_t_num("custom.factor", "Factor", 1e-9, 1e9, 1.0, "p.93", section="weighing", visible=("custom.active", True)), (_GW, _GWI)),
    _m(_t_num("custom.readability", "Display readability", 1e-9, 1e9, 0.0001, "p.93", section="weighing", visible=("custom.active", True)),
       (_GW, _GWI)),
    _m(dict(key="weighing.profile", label="Tolerance profile", type="profile", default=None, page="p.93", section="weighing")),
    _m(_t_enum("weighing.capture_mode", "Weight capture mode", ("Stable", "Immediate"), "Stable", "p.93", section="weighing")),
    _m(_t_num("weighing.capture_delay", "Weight capture delay", 0, 60, 5, "p.93", unit="s", integer=True, section="weighing",
              visible=("weighing.capture_mode", "Immediate"))),
    _m(_t_bool("weighing.statistics", "Activate statistics", False, "p.94", section="weighing")),
    _m(_t_bool("weighing.ionizer", "Ionizer", False, "p.94", section="weighing"), (_GW, _GWI)),
    # Weighing item (p.94-95, 104-105, 110-111)
    _m(dict(key="item.unit", label="Unit", type="unit", default="g", page="p.94", section="item")),
    _m(_t_num("item.target", "Target weight", 0, 1e9, None, "p.94", section="item", nullable=True), (_GW, _PCS)),
    _m(_t_num("item.tol_minus", "-Tolerance", 0, 100, 2.5, "p.94", unit="%", section="item", visible=("item.target", "set"),
              assumption="tolerances in percent of the target (screen picture p.18); factory values not given"), (_GW, _PCS)),
    _m(_t_num("item.tol_plus", "+Tolerance", 0, 100, 2.5, "p.95", unit="%", section="item", visible=("item.target", "set")), (_GW, _PCS)),
    _m(_t_num("density.temperature", "Temperature", 10, 30.9, 20.0, "p.104", unit="°C", section="item"), (_DEN,)),
    _m(_t_enum("density.aux_liquid", "Aux. liquid", ("Distilled water", "Custom"), "Distilled water", "p.104", section="item",
               visible=("density.type", "Solid")), (_DEN,)),
    _m(_t_text("density.aux_name", "Aux. liquid name", 0, 32, "", "p.104", section="item", visible=("density.aux_liquid", "Custom")), (_DEN,)),
    _m(_t_num("density.aux_density", "Aux. liquid density", 0.00001, 100, 1.0, "p.104", unit="g/cm3", section="item",
              visible=("density.aux_liquid", "Custom")), (_DEN,)),
    _m(_t_num("density.sinker_volume", "Sinker volume", 0.0001, 500, 10.0, "p.105", unit="cm3", section="item",
              visible=("density.type", "Liquid (sinker)")), (_DEN,)),
    _m(_t_num("density.pyc_volume", "Pycnometer volume", 0.001, 10000, 10.0, "p.105", unit="cm3", section="item",
              visible=("density.type", "Liquid (pycnometer)")), (_DEN,)),
    _m(_t_num("density.pyc_weight", "Pycnometer weight", 0, 220, 0.0, "p.105", unit="g", section="item",
              visible=("density.type", "Liquid (pycnometer)")), (_DEN,)),
    _m(_t_num("pcs.reference_pcs", "Reference PCS", 1, 10000, 1, "p.110", integer=True, section="item"), (_PCS,)),
    _m(_t_num("pcs.reference_apw", "Reference average weight", 0, 220, None, "p.111", unit="g", section="item", nullable=True), (_PCS,)),
    # Automation (p.95-97, 105, 111-112)
    _m(_t_enum("auto.barcode_target", "Barcode data target", ("Keyboard Input", "Target weight value", "Task ID 1", "Result ID 1"),
               "Keyboard Input", "p.95", section="automation")),
    _m(_t_bool("auto.feeder", "Automatic feeder support", False, "p.95", section="automation"), (_GW, _GWI)),
    _m(_t_num("auto.feeder_items", "Number of weighing items", 1, 100, 20, "p.95", integer=True, section="automation",
              visible=("auto.feeder", True)), (_GW, _GWI)),
    _m(_t_num("auto.feeder_plausibility", "Plausibility limits", 0, 100, 30, "p.95", unit="%", section="automation",
              visible=("auto.feeder", True)), (_GW, _GWI)),
    _m(_t_bool("auto.feeder_discharge", "Discharge feeder at the end", False, "p.95", section="automation", visible=("auto.feeder", True)),
       (_GW, _GWI)),
    _m(_t_enum("auto.feeder_rate", "Feed rate", ("Slow", "Normal", "Fast", "Very fast"), "Normal", "p.96", section="automation",
               visible=("auto.feeder", True)), (_GW, _GWI)),
    _m(_t_bool("auto.zero", "Automatic zero", False, "p.96", section="automation"), (_GW, _GWI, _PCS)),
    _m(_t_num("auto.zero_threshold", "Automatic zero threshold", 0, 220, None, "p.96", unit="g", section="automation", nullable=True,
              visible=("auto.zero", True)), (_GW, _GWI, _PCS)),
    _m(_t_enum("auto.tare_mode", "Tare Mode", ("None", "Automatic tare", "Preset tare"), "None", "p.96", section="automation"),
       (_GW, _GWI, _PCS)),
    _m(_t_num("auto.tare_threshold", "Automatic tare threshold", 0, 220, None, "p.96", unit="g", section="automation", nullable=True,
              visible=("auto.tare_mode", "Automatic tare")), (_GW, _GWI, _PCS)),
    _m(_t_num("auto.preset_tare", "Preset tare value", 0, 220, None, "p.96", unit="g", section="automation", nullable=True,
              visible=("auto.tare_mode", "Preset tare")), (_GW, _GWI, _PCS)),
    _m(_t_enum("auto.result", "Automatic result", ("None", "With sample tare", "Without sample tare"), "With sample tare", "p.96",
               section="automation"), (_GW, _GWI)),
    _m(_t_enum("auto.result", "Automatic result", ("None", "Without sample tare"), "None", "p.112", section="automation"), (_PCS,)),
    _m(_t_num("auto.result_threshold", "Automatic result threshold", 0, 220, None, "p.96", unit="g", section="automation", nullable=True,
              visible=("auto.result", ("With sample tare", "Without sample tare")),
              assumption="the factory threshold is not given: without a threshold no result is added automatically"), (_GW, _GWI, _PCS)),
    _m(_t_enum("auto.weight_trigger", "Weight trigger", ("Exceeding", "Falling below"), "Exceeding", "p.97", section="automation",
               visible=("auto.result", "Without sample tare")), (_GW, _GWI, _PCS)),
    _m(_t_bool("auto.tare_after_result", "Automatic tare after result", False, "p.97", section="automation"), (_GW, _GWI, _PCS)),
    _m(_t_bool("auto.task_completion", "Automatic task completion", False, "p.97", section="automation"), (_GWI,)),
    # Print / Export (p.97-99): printing needs a printer, exporting a USB stick or a file server (absent); the settings are kept
    _m(_t_bool("print.strip_printer", "Strip printer", False, "p.97", section="print")),
    _m(_t_bool("print.results_export", "Results export", False, "p.98", section="print")),
    _m(_t_bool("print.weight_value", "Weight value", False, "p.98", section="print"), (_GW, _GWI, _PCS)),
    _m(_t_multi("print.tpl_header", "Header and Footer", _TPL_HEADER, _TPL_HEADER, "p.98", section="print_template",
                assumption="template check boxes all active by default (not given)")),
    _m(_t_multi("print.tpl_balance", "Balance information", _TPL_BALANCE, _TPL_BALANCE, "p.98", section="print_template")),
    _m(_t_multi("print.tpl_quality", "Quality information", _TPL_QUALITY, _TPL_QUALITY, "p.98", section="print_template")),
    _m(_t_multi("print.tpl_task", "Task information", _TPL_TASK, _TPL_TASK, "p.98", section="print_template")),
    _m(_t_multi("print.tpl_item", "Weighing item information", _TPL_ITEM, _TPL_ITEM, "p.98", section="print_template")),
    _m(_t_multi("print.tpl_result", "Result detail information", _TPL_RESULT, _TPL_RESULT, "p.98", section="print_template")),
    _m(_t_bool("label_task.auto", "Automatic label printout for task", False, "p.99", section="label_task"), (_GW, _GWI, _PCS)),
    _m(_t_num("label_task.copies", "Copies", 1, 99, 1, "p.99", integer=True, section="label_task"), (_GW, _GWI, _PCS)),
    _m(_t_enum("label_task.template", "Used template", XPR_LABELS, XPR_LABELS[0], "p.99", section="label_task"), (_GW, _GWI, _PCS)),
    _m(_t_bool("label_item.auto", "Automatic label printout for weighing item", False, "p.99", section="label_item"), (_GW, _GWI, _PCS)),
    _m(_t_num("label_item.copies", "Copies", 1, 99, 1, "p.99", integer=True, section="label_item"), (_GW, _GWI, _PCS)),
    _m(_t_enum("label_item.template", "Used template", XPR_LABELS, XPR_LABELS[0], "p.99", section="label_item"), (_GW, _GWI, _PCS)),
    _m(_t_enum("print.label_cutting", "Label cutting", ("Off", "Per label", "Per task"), "Off", "p.97", section="print"), (_GW, _GWI, _PCS)),
]


def xpr_method_fields(method_type):
    """Method settings of one method type (the Automatic result row differs between general weighing and piece counting)."""
    return [f for f in XPR_METHOD_FIELDS if method_type in f["types"]]


XPR_PROFILE_FIELDS = [
    _t_text("name", "Name", 0, 22, "", "p.79"),
    _t_enum("indicator", "Indicator", ("None", "Neutral", "White", "Yellow", "Red", "Blue", "Green"), "None", "p.79"),
    _t_text("indicator_text", "Indicator text", 0, 3, "", "p.79", visible=("indicator", ("Neutral", "White", "Yellow", "Red", "Blue", "Green"))),
    _t_enum("certificate", "Calibration certificate", ("None",), "None", "p.79",
            assumption="certificates are created only by a service technician: none on this balance"),
    _t_enum("environment", "Environment", tuple(XPR_ENVIRONMENTS), "Standard", "p.80"),
    _t_enum("weighing_mode", "Weighing mode", ("Universal", "Sensor mode"), "Universal", "p.80"),
    _t_enum("value_release", "Value release", tuple(XPR_VALUE_RELEASE), "Fast and reliable", "p.80"),
    _t_enum("readability", "Display readability", tuple(XPR_READABILITY), "1d", "p.80"),
    _t_bool("zero_drift", "Zero drift compensation", True, "p.80"),
    _t_multi("units", "Allowed units", tuple(XPR_UNITS), tuple(XPR_UNITS), "p.80"),
]
XPR_WEIGHT_FIELDS = [
    _t_text("name", "Test weight name", 1, 22, "", "p.117"),
    _t_text("weight_id", "Test weight ID", 1, 22, "", "p.117"),
    _t_num("nominal_g", "Nominal weight", 0.0001, 220, None, "p.117", unit="g"),
    _t_enum("weight_class", "Weight class", XPR_WEIGHT_CLASSES, "Own", "p.117"),
    _t_num("actual_g", "Actual weight", 0.0001, 220, None, "p.117", unit="g", nullable=True),
    dict(key="next_calibration", label="Next calibration date", type="date", default="", page="p.117"),
    _t_bool("certificate", "Certificate", False, "p.117"),
    _t_text("certificate_id", "Certificate ID", 1, 22, "", "p.117", visible=("certificate", True)),
    dict(key="certificate_date", label="Certificate date", type="date", default="", page="p.117", visible=("certificate", True)),
    _t_text("weight_set_id", "Weight set ID", 0, 22, "", "p.117"),
]
_TPL_TEST_SUMMARY = ("Test type", "Test name", "Test result", "Standard deviation")
_TPL_TEST_DETAILS = ("Test trigger", "Leveling at start", "Temperature", "Preparation instructions", "Test start (date / time)",
                     "Test end (date / time)", "User name")
_TPL_TEST_WEIGHT = ("Test weight ID", "Weight class", "Nominal weight", "Actual weight", "Weight set ID", "Certificate ID", "Certificate date",
                    "Next calibration date", "Weight type", "Minimum weight class", "Used nominal weights", "Used actual weights")
_TPL_TEST_RESULTS = ("Weight", "Deviation", "State", "Level state", "Zero / Tare", "Center deviation")
_ECC, _REP, _REPT, _SEN1, _SEN2, _SENT1, _SENT2 = XPR_TEST_TYPES
_TARE_TESTS = (_REPT, _SENT1, _SENT2)


def _tt(entry, types=XPR_TEST_TYPES, **kw):
    return dict(entry, types=tuple(types), **kw)


XPR_TEST_FIELDS = [
    _tt(_t_text("name", "Name", 1, 22, "", "p.118", section="name")),
    _tt(_t_bool("activated", "Test activated", True, "p.118", section="name")),
    _tt(_t_bool("show_preparation", "Show preparation instructions", True, "p.118", section="name")),
    _tt(_t_bool("automatic_print", "Automatic print enabled", False, "p.118", section="name")),
    _tt(_t_enum("result_calculation", "Result calculation", ("On nominal weight", "On actual weight (CMV)"), "On nominal weight", "p.118",
                section="spec")),
    _tt(_t_num("repetitions", "Number of repetitions", 2, 15, 10, "p.121", integer=True, section="spec"), (_REP, _REPT)),
    _tt(_t_text("tare_name", "Tare name", 1, 22, "Tare", "p.121", section="spec"), _TARE_TESTS),
    _tt(_t_num("min_tare_g", "Minimum tare weight", 0, 220, 1.0, "p.121", unit="g", section="spec", assumption="factory value not given"),
        _TARE_TESTS),
    _tt(_t_num("tp1_nominal_g", "Nominal weight", 0.0001, 220, 200.0, "p.118", unit="g", section="spec",
               assumption="test point defaults: the routine-test weights of the data sheet (200 g / 10 g, p.142)")),
    _tt(_t_enum("tp1_class", "Weight class", XPR_WEIGHT_CLASSES, "F2", "p.118", section="spec")),
    _tt(_t_num("tp1_control_g", "Control limit", 0.00001, 220, 0.001, "p.119", unit="g", section="spec",
               assumption="limits are weights in g; factory values not given (1 mg / 0.5 mg; repeatability 0.1 mg / 0.07 mg)"),
        (_ECC, _SEN1, _SEN2, _SENT1, _SENT2)),
    _tt(_t_num("tp1_warning_g", "Warning limit", 0.00001, 220, 0.0005, "p.119", unit="g", section="spec"), (_ECC, _SEN1, _SEN2, _SENT1, _SENT2)),
    _tt(_t_num("rep_control_g", "Control limit", 0.00004, 220, 0.0001, "p.122", unit="g", section="spec"), (_REP, _REPT)),
    _tt(_t_num("rep_warning_g", "Warning limit", 0.00001, 220, 0.00007, "p.122", unit="g", section="spec"), (_REP, _REPT)),
    _tt(_t_num("tp2_nominal_g", "Nominal weight 2", 0.0001, 220, 10.0, "p.125", unit="g", section="spec"), (_SEN2, _SENT2)),
    _tt(_t_enum("tp2_class", "Weight class 2", XPR_WEIGHT_CLASSES, "F2", "p.125", section="spec"), (_SEN2, _SENT2)),
    _tt(_t_num("tp2_control_g", "Control limit 2", 0.00001, 220, 0.001, "p.125", unit="g", section="spec"), (_SEN2, _SENT2)),
    _tt(_t_num("tp2_warning_g", "Warning limit 2", 0.00001, 220, 0.0005, "p.125", unit="g", section="spec"), (_SEN2, _SENT2)),
    _tt(dict(key="weight_1", label="Test weight", type="weight", default=None, page="p.119", section="weights")),
    _tt(dict(key="weight_2", label="Test weight 2", type="weight", default=None, page="p.126", section="weights"), (_SEN2, _SENT2)),
    _tt(_t_bool("block_balance", "Block balance", False, "p.119", section="errors")),
    _tt(_t_num("retries", "Allowed number of retries", 0, 9, 3, "p.119", integer=True, section="errors", visible=("block_balance", True))),
    _tt(_t_enum("planning", "Planning type", ("Manually", "Daily", "Weekly", "Monthly", "Quarterly", "Annually"), "Manually", "p.119",
                section="planning")),
    _tt(dict(key="start_time", label="Start time", type="time", default="08:00", page="p.119", section="planning",
             visible=("planning", ("Daily", "Weekly", "Monthly", "Quarterly", "Annually")), assumption="factory start time not given")),
    _tt(_t_multi("preferred_days", "Preferred days", XPR_WEEKDAYS, XPR_WEEKDAYS[:5], "p.120", section="planning", visible=("planning", "Weekly"))),
    _tt(_t_enum("day_of_week", "Day of the week", ("None",) + XPR_WEEKDAYS, "None", "p.120", section="planning", visible=("planning", "Monthly"))),
    _tt(_t_enum("week_of_month", "Week of the month", ("First", "Second", "Third", "Fourth"), "First", "p.120", section="planning",
                visible=("planning", "Monthly"))),
    _tt(_t_multi("tpl_header", "Header and Footer", _TPL_HEADER, _TPL_HEADER, "p.120", section="printing")),
    _tt(_t_multi("tpl_balance", "Balance information", _TPL_BALANCE, _TPL_BALANCE, "p.120", section="printing")),
    _tt(_t_multi("tpl_summary", "Test summary", _TPL_TEST_SUMMARY, _TPL_TEST_SUMMARY, "p.120", section="printing")),
    _tt(_t_multi("tpl_details", "Test details", _TPL_TEST_DETAILS, _TPL_TEST_DETAILS, "p.120", section="printing")),
    _tt(_t_multi("tpl_weight", "Test weight", _TPL_TEST_WEIGHT, _TPL_TEST_WEIGHT, "p.120", section="printing")),
    _tt(_t_multi("tpl_limits", "Test limits", ("Warning limit", "Control limit"), ("Warning limit", "Control limit"), "p.121", section="printing")),
    _tt(_t_multi("tpl_results", "Measurements / Results", _TPL_TEST_RESULTS, _TPL_TEST_RESULTS, "p.121", section="printing")),
]


def xpr_test_fields(test_type):
    return [f for f in XPR_TEST_FIELDS if test_type in f["types"]]


_TPL_ADJ_SUMMARY = ("Adjustment type", "Adjustment status", "Balance adjusted")
_TPL_ADJ_DETAILS = ("Trigger", "Cell temperature", "Level state", "Date/time", "User name")
_TPL_ADJ_RESULTS = ("Correction", 'Test deviation "as found"', 'Test deviation "as left"', 'Test result "as found"', 'Test result "as left"')
XPR_ADJUSTMENT_FIELDS = [
    _t_enum("strategy", "Strategy", ("Internal adjustment", "External adjustment", "No adjustment"), "Internal adjustment", "p.128",
            section="strategy"),
    _t_bool("automatic_print", "Automatic print", False, "p.128", section="strategy"),
    dict(key="weight", label="Test weights", type="weight", default=None, page="p.52", section="strategy",
         visible=("strategy", "External adjustment")),
    _t_bool("as_found", '"As found" test', False, "p.128", section="spec", visible=("strategy", "Internal adjustment")),
    _t_bool("as_left", '"As left" test', False, "p.128", section="spec", visible=("strategy", "Internal adjustment")),
    _t_num("control_limit", "Control limit", 0.001, 100, 0.1, "p.128", unit="%", section="spec", visible=("strategy", "Internal adjustment")),
    _t_num("warning_limit", "Warning limit", 0.001, 100, 0.05, "p.128", unit="%", section="spec", visible=("strategy", "Internal adjustment"),
           assumption="factory warning limit not given"),
    _t_bool("block_balance", "Block balance", False, "p.129", section="errors", visible=("strategy", "Internal adjustment")),
    _t_bool("start_after_leveling", "Start after leveling", False, "p.129", section="planning", visible=("strategy", "Internal adjustment")),
    _t_bool("start_after_temperature", "Start after temperature change", True, "p.129", section="planning",
            visible=("strategy", "Internal adjustment")),
    _t_enum("schedule", "Schedule", ("Inactive", "1 start time", "2 start times", "3 start times"), "1 start time", "p.129", section="planning",
            visible=("strategy", "Internal adjustment")),
    dict(key="start_time_1", label="Start time 1", type="time", default="08:00", page="p.129", section="planning",
         visible=("schedule", ("1 start time", "2 start times", "3 start times")), assumption="factory start times not given"),
    dict(key="start_time_2", label="Start time 2", type="time", default="12:00", page="p.129", section="planning",
         visible=("schedule", ("2 start times", "3 start times"))),
    dict(key="start_time_3", label="Start time 3", type="time", default="16:00", page="p.129", section="planning", visible=("schedule", "3 start times")),
    _t_multi("preferred_days", "Preferred days", XPR_WEEKDAYS, XPR_WEEKDAYS[:5], "p.129", section="planning",
             visible=("schedule", ("1 start time", "2 start times", "3 start times")), assumption="factory days not given"),
    _t_multi("tpl_header", "Header and Footer", _TPL_HEADER, _TPL_HEADER, "p.129", section="printing"),
    _t_multi("tpl_balance", "Balance information", _TPL_BALANCE, _TPL_BALANCE, "p.129", section="printing"),
    _t_multi("tpl_summary", "Adjustment summary", _TPL_ADJ_SUMMARY, _TPL_ADJ_SUMMARY, "p.129", section="printing"),
    _t_multi("tpl_details", "Adjustment details", _TPL_ADJ_DETAILS, _TPL_ADJ_DETAILS, "p.129", section="printing"),
    _t_multi("tpl_results", "Measurements / Results", _TPL_ADJ_RESULTS, _TPL_ADJ_RESULTS, "p.130", section="printing"),
]
XPR_USER_FIELDS = [
    _t_text("user_name", "User name", 1, 22, "", "p.76", create_only=True),
    _t_text("last_name", "Last name", 0, 22, "", "p.76"),
    _t_text("first_name", "First name", 0, 22, "", "p.76"),
    _t_bool("active", "Active", True, "p.76"),
    dict(key="groups", label="Assigned groups", type="groups", default=["Administrators"], page="p.76"),
    _t_enum("language", "User language", XPR_LANGUAGES, "English", "p.76"),
]
XPR_GROUP_FIELDS = [_t_text("group_name", "Group name", 1, 22, "", "p.76", create_only=True)] + [
    _t_bool(p, label, False, page) for p, label, page in (
        ("block_unblock", "Block / unblock balance", "p.76"), ("configure_methods", "Configure methods", "p.76"),
        ("exclude_overwrite", "Exclude / Overwrite result", "p.76"), ("cancel_task", "Cancel task", "p.76"),
        ("configure_system", "Configure system", "p.77"), ("configure_users", "Configure user management", "p.77"),
        ("execute_service", "Execute service commands", "p.77"), ("start_external_adjustment", "Start external adjustment", "p.77"),
        ("show_change_history", "Show change history", "p.77"), ("configure_tests", "Configure routine tests / GWP", "p.77"),
        ("start_tests", "Start routine tests", "p.77"))]


def _xt_defaults(fields):
    return {f["key"]: _xt_copy.deepcopy(f["default"]) for f in fields}


def _xt_visible(f, values):
    """Is a setting applicable with these values (its 'visible' / 'visible2' rules)?"""
    for rule in (f.get("visible"), f.get("visible2")):
        if rule is None:
            continue
        key, want = rule
        v = values.get(key)
        if want == "set":
            if v is None:
                return False
        elif isinstance(want, tuple):
            if v not in want:
                return False
        elif v != want:
            return False
    return True


def _xt_check_value(f, v):
    """Type / range check of one setting value (True when valid)."""
    t = f["type"]
    if t == "bool":
        return type(v) is bool
    if t == "enum":
        return v in f["options"] and type(v) is type(f["options"][0])
    if t in ("int", "num"):
        if v is None:
            return bool(f.get("nullable"))
        if type(v) not in (int, float) or not _xt_math.isfinite(v) or (t == "int" and float(v) != int(v)):
            return False
        return f["min"] - 1e-12 <= v <= f["max"] + 1e-12
    if t == "text":
        if not isinstance(v, str) or not f["min_len"] <= len(v) <= f["max_len"]:
            return False
        if f.get("pattern"):
            import re
            return re.match(f["pattern"], v) is not None
        return True
    if t == "multi":
        return isinstance(v, list) and all(x in f["options"] for x in v) and len(set(v)) == len(v)
    if t == "unit":
        return v in XPR_UNITS or v == "custom" or v == "PCS"
    if t in ("date",):
        return v == "" or _xt_parse_date(v) is not None
    if t == "time":
        return isinstance(v, str) and _xt_parse_time(v) is not None
    if t in ("profile", "weight"):
        return v is None or isinstance(v, str)
    if t == "groups":
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    return False


def _xt_parse_date(v):
    try:
        return _xt_dt.date.fromisoformat(v) if isinstance(v, str) and len(v) == 10 else None
    except ValueError:
        return None


def _xt_parse_time(v):
    try:
        h, m = v.split(":")
        h, m = int(h), int(m)
        return (h, m) if 0 <= h < 24 and 0 <= m < 60 and len(v) == 5 else None
    except (ValueError, AttributeError):
        return None


def _xt_field_schema(f):
    """JSON schema of one setting value (contract input schemas)."""
    t = f["type"]
    if t == "bool":
        return {"type": "boolean"}
    if t == "enum":
        opts = f["options"]
        if isinstance(opts[0], int):
            return {"type": "integer", "enum": list(opts), "x-unit": _XT_UNIT_SI.get(f.get("unit"), "1")}
        return {"type": "string", "enum": list(opts)}
    if t in ("int", "num"):
        s = {"type": "integer" if t == "int" else "number", "minimum": f["min"], "maximum": f["max"], "x-unit": _XT_UNIT_SI.get(f.get("unit"), "1")}
        return {"anyOf": [s, {"type": "null"}]} if f.get("nullable") else s
    if t == "text":
        s = {"type": "string", "minLength": f["min_len"], "maxLength": f["max_len"]}
        if f.get("pattern"):
            s["pattern"] = f["pattern"]
        return s
    if t == "multi":
        return {"type": "array", "items": {"type": "string", "enum": list(f["options"])}, "uniqueItems": True}
    if t == "unit":
        return {"type": "string", "enum": list(XPR_UNITS) + ["custom", "PCS"]}
    if t == "date":
        return {"type": "string", "pattern": r"^(\d{4}-\d{2}-\d{2})?$"}
    if t == "time":
        return {"type": "string", "pattern": r"^([01]\d|2[0-3]):[0-5]\d$"}
    if t in ("profile", "weight"):
        return {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}
    if t == "groups":
        return {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}
    raise ValueError(t)


_XT_UNIT_SI = {None: "1", "days": "d", "min": "min", "s": "s", "%": "%", "g": "g", "°C": "degC", "cm3": "cm3", "g/cm3": "g/cm3"}


# ---------------------------------------------------------------- the weighing cell of the XPR: environment filters, value release, AZT
class XPRWeighingCell(FilteredAnalyticalBalance):
    """FilteredAnalyticalBalance with the tolerance-profile settings of the XPR: Environment (five low-pass models; Very stable / Standard /
    Very unstable are exactly the Low / Medium / High models), Value release (x stability hold), Weighing mode (Sensor mode: a fixed
    moving average of 2 tau instead of the exponential filter) and Zero drift compensation (the zero follows a stable reading within
    0.5 d of zero).  set_filter(Low / Medium / High) keeps its meaning (it selects the equal environment)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.environment = _XT_ENV_OF_FILTER[self.filter_level]
        self.value_release = "Fast and reliable"
        self.weighing_mode = "Universal"
        self.zero_drift = True
        self.stable_age_s = 0.0                                              # time since the reading became stable (value release fallback)
        self._window = _xt_deque()

    def _tau_n(self):
        return XPR_ENVIRONMENTS[self.environment]

    @property
    def stability_band_kg(self):
        return self._tau_n()[1] * self.readability_kg

    def set_filter(self, level):
        super().set_filter(level)
        self.environment = _XT_ENV_OF_FILTER[level]

    def configure_profile(self, environment, value_release, weighing_mode, zero_drift):
        changed = (environment, weighing_mode) != (self.environment, self.weighing_mode)
        self.environment, self.value_release, self.weighing_mode, self.zero_drift = environment, value_release, weighing_mode, zero_drift
        self.filter_level = _XT_FILTER_OF_ENV[environment]
        self.settling_s = XPR204E_TARGET_SETTLING_S * XPR_VALUE_RELEASE[value_release]
        if changed:
            self._window.clear()
            self.reset_stability(clear_filter=True)

    def reset_stability(self, *, clear_filter=False):
        super().reset_stability(clear_filter=clear_filter)
        self.stable_age_s = 0.0
        if clear_filter and hasattr(self, "_window"):
            self._window.clear()

    def update(self, pan_load_force_N, dt_s, *, door_fraction=0.0, tilt_rad=0.0, vibration=False):
        """FilteredAnalyticalBalance.update with the environment's tau / N (same arithmetic), Sensor mode and zero drift compensation."""
        if (not _xpr_number(pan_load_force_N) or not _xpr_number(dt_s) or dt_s < 0
                or not _xpr_number(door_fraction) or not 0 <= door_fraction <= 1
                or not _xpr_number(tilt_rad) or type(vibration) is not bool):
            raise ValueError("Finite SI force/time/environment and boolean vibration required")
        if dt_s == 0:
            return self.read()
        tau, _ = self._tau_n()
        self.sample_time_s += dt_s
        self.raw_force_N = float(pan_load_force_N)
        self.door_fraction, self.tilt_rad = float(door_fraction), float(tilt_rad)
        if self.weighing_mode == "Sensor mode":
            self._window.append((self.sample_time_s, self.raw_force_N))
            while self._window and self._window[0][0] <= self.sample_time_s - 2 * tau + 1e-12:
                self._window.popleft()
            self.filtered_force_N = sum(f for _, f in self._window) / len(self._window)
        elif self.filtered_force_N is None:
            self.filtered_force_N = self.raw_force_N
        else:
            alpha = -_xpr_math.expm1(-dt_s / tau)
            self.filtered_force_N = (1 - alpha) * self.filtered_force_N + alpha * self.raw_force_N
        self.filter_age_s += dt_s
        self.gross_kg = self.filtered_force_N / self.g_cal
        hardware = self.powered and self.pan_installed and self.pan_latched and self.transport_unlocked
        environment = door_fraction <= 0.01 and abs(tilt_rad) <= _xpr_math.radians(0.2) and not vibration
        range_valid = self.raw_force_N / self.g_cal <= self.capacity_kg and -self.stability_band_kg <= self.gross_kg <= self.capacity_kg
        if not (hardware and environment and range_valid):
            self.reset_stability()
            return self.read()
        displayed = _xpr_quantize(self.gross_kg)
        samples = self._stability_samples
        samples.append((self.sample_time_s, displayed))
        cutoff = self.sample_time_s - self.settling_s
        while len(samples) > 1 and samples[1][0] <= cutoff + 1e-10:
            samples.popleft()
        self._window_range_kg = max(v for _, v in samples) - min(v for _, v in samples)
        inside = self._window_range_kg <= self.stability_band_kg + 1e-15
        self.elapsed_s = min(self.settling_s, self.sample_time_s - samples[0][0]) if inside else 0.0
        warmed = self.filter_age_s + 1e-10 >= 5 * tau
        self.last_environment_valid = True
        settled = self._settle_hold(dt_s, cutoff)                            # the reading has reached its input (FilteredAnalyticalBalance._settled)
        self.stable = bool(inside and warmed and settled and self.elapsed_s + 1e-10 >= self.settling_s)
        self.stable_age_s = self.stable_age_s + dt_s if self.stable else 0.0
        if self.zero_drift and self.stable and self.tare_kg == 0.0 and 0.0 < abs(self.gross_kg - self.zero_kg) <= 0.5 * self.readability_kg:
            self.zero_kg = self.gross_kg                                     # zero drift compensation (AZT)
        return self.read()


# ---------------------------------------------------------------- device state
def _xt_now_text(device, time_s):
    return (_XT_EPOCH + _xt_dt.timedelta(seconds=time_s + device["clock_offset_s"])).replace(microsecond=0).isoformat()


def _xt_new_method(mid, name, mtype, date):
    settings = _xt_defaults(xpr_method_fields(mtype))
    settings["name"] = name
    if mtype == _PCS:
        settings["item.unit"] = "PCS"
    items = [dict(sample_id="Item 1", unit="g", target=None, tol_minus=2.5, tol_plus=2.5)] if mtype == _GWI else []
    return dict(id=mid, type=mtype, created=date, settings=settings, items=items)


def _xt_new_device():
    s = _xt_defaults(XPR_SETTINGS)
    return dict(
        settings=s, clock_offset_s=0.0, standby=False, blocked=False, block_reason=None, unblocking_password="",
        profiles=[dict(id="tp1", **dict(_xt_defaults(XPR_PROFILE_FIELDS), name="Default"))], default_profile="tp1", seq=dict(tp=1, m=1, tw=0, t=0),
        methods=[_xt_new_method("m1", "General Weighing", _GW, "2026-01-01")], active_method="m1", task=None, tasks={},
        test_weights=[], tests=[], adjustment=_xt_defaults(XPR_ADJUSTMENT_FIELDS), procedure=None,
        adjustment_state=dict(last=None, count=0, scheduled_done=[]),
        history=dict(adjustments=[], tests=[], changes=[], service=[]),
        users=[dict(user_name="Administrator", last_name="", first_name="", active=True, groups=["Administrators"], language="English",
                    password="")],
        groups=[dict(group_name="Administrators", **{p: True for p in XPR_PERMISSIONS})],
        user_management=False, current_user="Administrator", logged_in=True,
        last_activity_s=0.0, pending=None, messages=[], message_seq=0, level_ok=True, power_epoch=1,
        display_unit=None, errors=dict(span_error_rel=0.0, zero_offset_kg=0.0, zero_drift_kg_s=0.0, internal_weight_error_kg=0.0, span_tc_per_k=0.0),
        drift_kg=0.0, internal_loaded=False, auto=dict(result_armed=True, captured=None, tare_armed=True), door_closed_by=None,
        failed_tests={}, output=[], cell_temp_c=XPR_TEMP_REF_C, adj_temp_c=XPR_TEMP_REF_C)


def _xd_span(dev):
    """Relative sensitivity error of the weighing cell: the evaluator span error plus its temperature coefficient times the cell temperature
    away from 20 degC (0 = ideal, the default); the adjustments correct it at the temperature they run at."""
    e = dev["errors"]
    return e["span_error_rel"] + e.get("span_tc_per_k", 0.0) * (dev.get("cell_temp_c", XPR_TEMP_REF_C) - XPR_TEMP_REF_C)


def xpr_unit_step(unit, readability="1d", custom=None):
    """Display step and decimals of a unit: the readability (d = 0.1 mg x 1d...1000d) converted and rounded up to 1 / 2 / 5 x 10^n."""
    d_g = XPR204E_READABILITY_KG * 1000.0 * XPR_READABILITY[readability]
    if unit == "custom" and custom:
        step = float(custom.get("custom.readability") or 0.0001)
    else:
        x = d_g / XPR_UNITS[unit]
        k = _xt_math.floor(_xt_math.log10(x) + 1e-9)
        step = next(m * 10.0 ** k for m in (1, 2, 5, 10) if m * 10.0 ** k >= x * (1 - 1e-9))
    dec = max(0, -int(_xt_math.floor(_xt_math.log10(step) + 1e-9)))
    return step, dec


def xpr_format(value, step, dec):
    q = _xt_math.floor(abs(value) / step + 0.5 + 1e-9) * step
    return f"{_xt_math.copysign(q, value) if q else 0.0:.{dec}f}"


def xpr_convert(mass_kg, unit, method_settings=None):
    """Mass in a display unit (custom units by their formula and factor; PCS by the reference average weight)."""
    g = mass_kg * 1000.0
    if unit == "custom":
        s = method_settings or {}
        f = float(s.get("custom.factor") or 1.0)
        return g * f if s.get("custom.formula", "Multiplicative") == "Multiplicative" else (f / g if g else 0.0)
    if unit == "PCS":
        apw = (method_settings or {}).get("pcs.reference_apw")
        return None if not apw else g / apw
    return g / XPR_UNITS[unit]
# ---------------------------------------------------------------- device operations (on the candidate instance of a command or a step)
_XT_ERRORS = {
    "LOGIN_REQUIRED": "User management is active and no user is logged in.",
    "PERMISSION_DENIED": "The group of the logged-in user does not have this right.",
    "BALANCE_BLOCKED": "The balance is blocked.",
    "WRONG_PASSWORD": "User name or password is not correct.",
    "METHOD_LOCKED": "The method is locked.",
    "BUSY": "A test, an adjustment or a density determination is running.",
    "NOT_LEVEL": "Forced leveling: the balance is out of level.",
    "NO_WEIGHT": "No weight value (switched off, overload, underload or no weighing pan).",
    "ZERO_RANGE": "The weight is outside the zero range.",
    "IN_USE": "The item is in use.",
    "NOT_FOUND": "The item does not exist.",
    "STANDBY": "The balance is in standby.",
    "ACCESS_DENIED": "Started by another caller: only that caller can stop, power off or change this task or procedure.",
    "CANNOT_ADJUST": "Cannot start adjustment: the initial zero was not reached when the balance was switched on (disconnect and reconnect power).",
}
# Error messages of the operating manual (8.1, p.134) injected by the evaluator (fault.inject / fault.clear): code -> (message text, the weight
# cannot be determined (zero / tare / results / readings refused, the display shows --), cleared by disconnecting the power (the documented
# remedy) or only by fault.clear (a cable or the battery has to be replaced or charged)).
XPR_FAULTS = {
    "balance_reset_failed": ("Balance reset failed", True, True),
    "weight_not_determined": ("Weight cannot be determined", True, True),
    "pairing_error": ("Pairing error.", True, False),
    "cannot_start_adjustment": ("Cannot start adjustment", False, True),
    "no_valid_date_time": ("The system has no valid date and time set", False, False),
    "performance_optimization": ("Preventive performance optimization", False, True),
}


def _xd_fault_blocks_weight(dev):
    f = dev.get("fault")
    return f is not None and XPR_FAULTS[f][1]


def _xd_cell(inst):
    return inst["balance"]


def _xd_method(dev, mid):
    return next((m for m in dev["methods"] if m["id"] == mid), None)


def _xd_profile(dev, pid):
    return next((p for p in dev["profiles"] if p["id"] == pid), None)


def _xd_active_profile(dev):
    m = _xd_method(dev, dev["active_method"])
    pid = (m["settings"].get("weighing.profile") if m else None) or dev["default_profile"]
    return _xd_profile(dev, pid) or _xd_profile(dev, dev["default_profile"])


def _xd_apply_profile(inst):
    p = _xd_active_profile(inst["device"])
    _xd_cell(inst).configure_profile(p["environment"], p["value_release"], p["weighing_mode"], p["zero_drift"])


def _xd_unit(dev):
    m = _xd_method(dev, dev["active_method"])
    if dev.get("display_unit"):
        return dev["display_unit"]
    if m and m["type"] == _GWI and dev["task"] and m["items"]:
        return m["items"][min(dev["task"]["item_index"], len(m["items"]) - 1)]["unit"]
    return (m["settings"].get("item.unit") if m else None) or "g"


def _xd_unit_name(dev, unit):
    if unit == "custom":
        m = _xd_method(dev, dev["active_method"])
        return (m["settings"].get("custom.name") if m else "") or "cu1"
    return unit


def _xd_level(inst):
    return abs(inst["tilt_rad"]) <= _XT_LEVEL_RAD + 1e-12


def _xd_allowed(dev, perm):
    if not dev["user_management"]:
        return True
    if not dev["logged_in"]:
        return False
    if perm is None:
        return True
    user = next((u for u in dev["users"] if u["user_name"] == dev["current_user"]), None)
    groups = {g["group_name"]: g for g in dev["groups"]}
    return bool(user) and any(groups.get(g, {}).get(perm) for g in user["groups"])


def _xd_change(dev, now, obj, old, new):
    """Change history (when active, p.79): user, time, object, old and new values."""
    if dev["settings"]["quality.change_history"] and old != new:
        dev["history"]["changes"].append(dict(time=now, user=dev["current_user"] if dev["logged_in"] else None, object=obj,
                                              old=_xt_copy.deepcopy(old), new=_xt_copy.deepcopy(new)))
        del dev["history"]["changes"][:-5000]


def _xd_message(dev, key, level, text, action=None, now=None):
    """Status-bar message (p.21): kept until acknowledged or until its cause is gone."""
    for msg in dev["messages"]:
        if msg["key"] == key:
            return msg
    dev["message_seq"] += 1
    msg = dict(id=f"msg{dev['message_seq']}", key=key, level=level, text=text, action=action, time=now, acknowledged=False)
    dev["messages"].append(msg)
    return msg


def _xd_clear(dev, key):
    dev["messages"] = [m for m in dev["messages"] if m["key"] != key]


def _xd_doors(inst, sides, action, max_m=XPR204E_DOOR_MAX_M):
    """Motorized side doors: open to the configured opening or close (manual drive: the key does not move the door)."""
    moved = []
    for side in sides:
        s = inst["device"]["settings"]
        if s[f"door_{side}.drive_mode"] != "Motorized":
            continue
        cur = inst["doors"][side]
        target = (s[f"door_{side}.opening"] / 100.0 * max_m) if (action == "open" or (action == "toggle" and cur <= 1e-6)) else 0.0
        if abs(target - cur) > 1e-12:
            inst["doors"][side] = round(target, 9)
            moved.append(side)
    if moved:
        b = _xd_cell(inst)
        b.door_fraction = max(inst["doors"].values()) / max_m
        b.reset_stability()
    return moved


def _xd_auto_close(inst, force=False):
    """Doors with 'Automatic (Tare / Zero / Result)' close for the measurement and open again after it (p.41, p.45, p.83); force: every
    motorized door (the external adjustment closes the door itself, p.52)."""
    dev = inst["device"]
    sides = [s for s in ("left", "right") if (force or dev["settings"][f"door_{s}.automatic"]) and inst["doors"][s] > 1e-6
             and dev["settings"][f"door_{s}.drive_mode"] == "Motorized"]
    if sides:
        dev["door_closed_by"] = {s: inst["doors"][s] for s in sides}
        _xd_doors(inst, sides, "close")


def _xd_auto_reopen(inst):
    dev = inst["device"]
    for side, opening in (dev.pop("door_closed_by", None) or {}).items():
        if dev["settings"][f"door_{side}.drive_mode"] == "Motorized":
            inst["doors"][side] = opening
            _xd_cell(inst).door_fraction = max(inst["doors"].values()) / XPR204E_DOOR_MAX_M
    dev["door_closed_by"] = None


def _xd_zero_tare(inst, action):
    """Zero / tare now (the runtime's balance.zero / balance.tare rules); returns an error code or None."""
    b = _xd_cell(inst)
    if not b.powered or not b.stable:
        return "PRECONDITION_FAILED"
    if action == "zero":
        if abs(b.gross_kg) > 0.02 * b.capacity_kg:
            return "ZERO_RANGE"
        b.press("zero")
    else:
        b.press("tare")
    inst["device"]["preset_active"] = False
    return None


def _xd_ready(inst, t):
    """Value release of the terminal for zeroing, taring and capturing: stable and the displayed value no longer moving (the stability window
    within 0.5 d), or stable for 10 s (assumption) when noise keeps the window wider."""
    b = _xd_cell(inst)
    if not b.stable:
        return False
    rng = b._window_range_kg
    return (rng is not None and rng <= 0.5 * b.readability_kg + 1e-15) or getattr(b, "stable_age_s", 0.0) >= 10.0 - 1e-9


def _xd_reading_ok(inst):
    r = _xd_cell(inst).read()
    return r["status"] in ("STABLE", "UNSTABLE") and r["net_kg"] is not None


# ---- tasks and results (p.31, p.36-41, p.20-21)
def _xd_ids(m, kind, now, counter):
    s = m["settings"]
    out = []
    for i in range(1, int(s[f"ids.{kind}_count"]) + 1):
        t = s[f"ids.{kind}_{i}_type"]
        if t == "Automatic timestamp":
            out.append(s[f"ids.{kind}_{i}_prefix"] + now.replace("-", "").replace(":", "").replace("T", ""))
        elif t == "Automatic counter":
            out.append(f"{s[f'ids.{kind}_{i}_prefix']}{counter}")
        else:
            out.append(s[f"ids.{kind}_{i}_default"])
    return out


def _xd_start_task(inst, mid, now):
    """Start a method: the running task (with results) is paused in Tasks; a paused task of this method continues (p.40)."""
    dev = inst["device"]
    cur = dev["task"]
    if cur is not None and cur["method_id"] != mid and (cur["results"] or cur.get("changed")):
        dev["tasks"][cur["method_id"]] = cur
    dev["active_method"] = mid
    dev["display_unit"] = None
    m = _xd_method(dev, mid)
    task = dev["tasks"].pop(mid, None)
    if task is None:
        task = dict(method_id=mid, started=now, task_ids=_xd_ids(m, "task", now, 1), result_ids=_xd_ids(m, "result", now, 1), counter=1,
                    results=[], item_index=0, pending=None, changed=False)
    dev["task"] = task
    dev["auto"] = dict(result_armed=True, captured=None, tare_armed=True, removed_wait=False)
    _xd_apply_profile(inst)
    _xd_apply_preset(inst)


def _xd_apply_preset(inst):
    dev = inst["device"]
    m = _xd_method(dev, dev["active_method"])
    s = m["settings"] if m else {}
    if s.get("auto.tare_mode") == "Preset tare" and s.get("auto.preset_tare") is not None:
        _xd_cell(inst).tare_kg = float(s["auto.preset_tare"]) / 1000.0
        dev["preset_active"] = True


def _xd_tolerance(m, task, value_unit):
    """Target and tolerance of the current weighing item: (target, low, high) in the result unit, or None."""
    if m["type"] == _GWI and m["items"]:
        it = m["items"][min(task["item_index"], len(m["items"]) - 1)]
        target, tm, tp = it.get("target"), it.get("tol_minus"), it.get("tol_plus")
    else:
        s = m["settings"]
        target, tm, tp = s.get("item.target"), s.get("item.tol_minus"), s.get("item.tol_plus")
    if target is None:
        return None
    return target, target * (1 - (tm or 0) / 100.0), target * (1 + (tp or 0) / 100.0)


def _xd_capture(inst, now, stable, net_kg=None):
    """A result of the running task from the current weight (Add result, automatic result, capture after a delay)."""
    dev, b = inst["device"], _xd_cell(inst)
    m = _xd_method(dev, dev["active_method"])
    task = dev["task"]
    r = dict(b.read())
    if net_kg is not None:
        r["net_kg"] = net_kg
    unit = _xd_unit(dev)
    profile = _xd_active_profile(dev)
    value = xpr_convert(r["net_kg"], unit, m["settings"])
    step, dec = xpr_unit_step(unit if unit not in ("PCS",) else "g", profile["readability"], m["settings"])
    if unit == "PCS":
        value_text = "—" if value is None else str(int(round(value)))
    else:
        value_text = xpr_format(value, step, dec)
    tol = _xd_tolerance(m, task, unit)
    shown = float(value_text) if value_text not in ("—",) else None
    tol_state = "No tolerance" if tol is None or shown is None else ("In tolerance" if tol[1] - 1e-12 <= shown <= tol[2] + 1e-12 else "Outside tolerance")
    level = _xd_level(inst)
    adjusted = dev["adjustment_state"]["last"] is not None and dev["adjustment_state"]["last"].get("status") == "Done"
    ok = tol_state != "Outside tolerance" and level
    item = None
    if m["type"] == _GWI and m["items"]:
        item = min(task["item_index"], len(m["items"]) - 1)
    res = dict(index=len(task["results"]) + 1, result_ids=list(task["result_ids"]), net_kg=r["net_kg"], tare_kg=b.tare_kg, gross_kg=b.gross_kg,
               unit=_xd_unit_name(dev, unit), value=shown, text=value_text, stable=bool(stable), calculated=bool(dev.get("preset_active")),
               time=now, state="OK" if ok else "Not OK", tolerance=tol_state, level="OK" if level else "Out of level",
               adjustment="OK" if adjusted else "Not adjusted", excluded=False, comment="", item=item,
               sample_id=(m["items"][item]["sample_id"] if item is not None else None))
    task["results"].append(res)
    task["counter"] += 1
    task["result_ids"] = _xd_ids(m, "result", now, task["counter"])
    if item is not None:
        task["item_index"] += 1
    if m["settings"].get("auto.tare_after_result") and b.stable:
        b.press("tare")
    _xd_output(dev, res, m)
    return res


def _xd_output(dev, res, m):
    """Automatic weight value output (p.81-82): the line the balance sends to a connected computer (none is connected; kept for review)."""
    s = dev["settings"]
    if s["output.mode"] != "Results" or not m["settings"].get("print.weight_value"):
        return
    num = res["text"].replace(".", s["output.decimal_delimiter"])
    neg = num.startswith("-")
    digits = num.lstrip("-")
    sign = "-" if neg else ("+" if s["output.sign"] == "For all values" else "")
    width = max(int(s["output.weight_field_length"]), len(sign) + len(digits))
    if s["output.sign_position"] == "Left of weight digits":
        weight = (sign + digits).rjust(width)
    else:
        weight = sign + digits.rjust(width - len(sign))
    fields = []
    for i in (1, 2):
        if s[f"output.result_id_{i}"]:
            fields.append(res["result_ids"][i - 1] if len(res["result_ids"]) >= i else "")
    date, _, time = res["time"].partition("T")
    if s["output.date"]:
        fields.append(date)
    if s["output.time"]:
        fields.append(time.replace(":", "-"))
    if s["output.net_indicator"]:
        fields.append(("N" if res["tare_kg"] else "").ljust(int(s["output.net_indicator_length"])))
    fields.append(weight)
    if s["output.unit"]:
        fields.append(res["unit"].ljust(int(s["output.unit_field_length"])))
    sep = {"None": "", "Space": " ", "TAB": "\t", ",": ",", ";": ";"}[s["output.field_delimiter"]]
    eol = {"CRLF": "\r\n", "CR": "\r", "LF": "\n", "TAB": "\t", "None": "", "Enter": "\n"}[s["output.end_of_line"]]
    dev["output"].append(sep.join(fields) + eol)
    del dev["output"][:-50]


def _xd_stats(task, m):
    vals = [r["value"] for r in task["results"] if not r["excluded"] and r["value"] is not None]
    if not m["settings"].get("weighing.statistics") or not vals:
        return None
    n = len(vals)
    avg = sum(vals) / n
    sd = _xt_math.sqrt(sum((v - avg) ** 2 for v in vals) / (n - 1)) if n > 1 else None
    return dict(count=n, sum=sum(vals), minimum=min(vals), maximum=max(vals), range=max(vals) - min(vals), average=avg, sd=sd,
                rsd_pct=None if sd is None or avg == 0 else round(100.0 * sd / abs(avg), 2))


# ---- procedures: routine tests, internal / external adjustment, density determination (p.44-52, p.38)
_PREP = "Ensure that the weighing pan is empty and clean. Have at hand: the test weight, gloves, and the appropriate tools to handle the test weight."
_EMPTY = "Make sure that the weighing pan is empty and tap OK."
_CHOOSE = "Choose an available test weight and tap OK."
_REMOVE = "Remove the test weight carefully and tap OK."
_POSITIONS = ("in position 1, in the middle of the weighing pan", "in position 2 (front left corner of the weighing pan)",
              "in position 3 (back left corner of the weighing pan)", "in position 4 (back right corner of the weighing pan)",
              "in position 5 (front right corner of the weighing pan)")


def _xp_steps_test(test):
    t, s = test["type"], test["settings"]
    steps = []
    if s.get("show_preparation", True):
        steps.append(dict(kind="prompt", text=_PREP))
    steps += [dict(kind="prompt", text=_EMPTY), dict(kind="zero", text="Zeroing...")]
    tps = (1, 2) if t in (_SEN2, _SENT2) else (1,)
    for tp in tps:
        steps.append(dict(kind="choose", tp=tp, text=_CHOOSE))
    if t == _ECC:
        for i, pos in enumerate(_POSITIONS):
            steps.append(dict(kind="place", tp=1, label=f"Position {i + 1}", text=f"Open the door and place the test weight carefully {pos}."))
        steps += [dict(kind="prompt", text=_REMOVE), dict(kind="zero", text="Zeroing...")]
    elif t in (_REP, _REPT):
        n = int(s["repetitions"])
        if t == _REPT:
            steps += [dict(kind="prompt", text="Place the test weight/test container in the center of the weighing pan and tap OK."),
                      dict(kind="tare", text="Taring...")]
        for i in range(n):
            steps.append(dict(kind="place", tp=1, label=f"Measurement {i + 1}",
                              text="Carefully place the test weight onto the weighing pan or into the tare container."))
            if t == _REP:
                steps += [dict(kind="prompt", text=_REMOVE), dict(kind="zero", text="Zeroing...")]
            else:
                steps += [dict(kind="unload", text="Remove the test weight, leave the container on the weighing pan."), dict(kind="tare", text="Taring...")]
    else:
        if t in (_SENT1, _SENT2):
            steps += [dict(kind="prompt", text="Place the test weight/test container in the center of the weighing pan and tap OK."),
                      dict(kind="tare", text="Taring...")]
        for tp in tps:
            steps.append(dict(kind="place", tp=tp, label=f"Test point {tp}", text="Open the door and place the test weight carefully on the weighing pan."))
            if tp == 1 and len(tps) == 2:
                steps.append(dict(kind="unload", text="Remove the test weight."))
    steps.append(dict(kind="results", text="The test procedure is finished."))
    return steps


def _xp_weight_mass_g(dev, wid, on_actual):
    w = next((w for w in dev["test_weights"] if w["id"] == wid), None)
    if w is None:
        return None
    if w.get("combined"):
        parts = [_xp_weight_mass_g(dev, x, on_actual) for x in w["weights"]]
        return None if None in parts else sum(parts)
    return float(w["actual_g"]) if on_actual and w.get("actual_g") is not None else float(w["nominal_g"])


def _xp_start(inst, kind, now, t_s, test_id=None, trigger="Manual", inputs=None):
    dev = inst["device"]
    p = dict(kind=kind, test_id=test_id, trigger=trigger, started=now, user=dev["current_user"] if dev["logged_in"] else None, index=0,
             step_t=None, weights={}, readings=[], zero_kg=None, status="running", result=None, inputs=inputs or {}, level_at_start=_xd_level(inst),
             message=None)
    if kind == "test":
        test = next(t for t in dev["tests"] if t["id"] == test_id)
        p.update(name=test["settings"]["name"], test_type=test["type"], steps=_xp_steps_test(test))
        for tp in (1, 2):
            if test["settings"].get(f"weight_{tp}"):
                p["weights"][str(tp)] = test["settings"][f"weight_{tp}"]
    elif kind == "internal":
        p.update(name="Internal adjustment", steps=[dict(kind="internal", text="Internal adjustment is being executed..."),
                                                     dict(kind="results", text="The adjustment has been completed.")])
    elif kind == "external":
        wid = dev["adjustment"].get("weight")
        if wid:
            p["weights"]["1"] = wid
        p.update(name="External adjustment", steps=[
            dict(kind="prompt", text=_PREP), dict(kind="prompt", text=_EMPTY), dict(kind="zero", text="Zeroing..."), dict(kind="choose", tp=1, text=_CHOOSE),
            dict(kind="place", tp=1, label="Adjustment weight", text="Open the door and place the test weight carefully on the weighing pan."),
            dict(kind="prompt", text=_REMOVE), dict(kind="zero", text="Zeroing..."), dict(kind="results", text="The adjustment has been completed.")])
    else:                                                                     # density determination of the running task
        m = _xd_method(dev, dev["active_method"])
        dt = m["settings"]["density.type"]
        if dt == "Solid":
            steps = [dict(kind="capture_ok", label="Weight in air", text="Place the sample on the upper weighing pan (in air) and tap OK."),
                     dict(kind="capture_ok", label="Weight in liquid", text="Place the sample on the lower weighing pan (in the liquid) and tap OK.")]
        elif dt == "Liquid (sinker)":
            steps = [dict(kind="capture_ok", label="Sinker in air", text="Hang the sinker in air and tap OK."),
                     dict(kind="capture_ok", label="Sinker in liquid", text="Immerse the sinker in the liquid and tap OK.")]
        else:
            steps = [dict(kind="capture_ok", label="Filled pycnometer", text="Place the filled pycnometer on the weighing pan and tap OK.")]
        p.update(name="Density determination", density_type=dt, steps=steps + [dict(kind="results", text="Density determination finished.")])
    dev["procedure"] = p
    _xp_enter(inst, now, t_s)


def _xp_step(dev):
    p = dev["procedure"]
    return p["steps"][p["index"]] if p and p["index"] < len(p["steps"]) else None


def _xp_enter(inst, now, t_s):
    dev = inst["device"]
    p = dev["procedure"]
    p["step_t"], p["step_sim_t"] = now, t_s
    st = _xp_step(dev)
    if st and st["kind"] in ("zero", "tare", "internal"):
        _xd_auto_close(inst, force=p["kind"] == "external")                  # the door closes and zeroing / taring starts (p.44, p.52)
    if st and st["kind"] == "internal":
        dev["internal_loaded"] = True
    prev = p["steps"][p["index"] - 1] if p["index"] > 0 else None
    p["need_unload"] = bool(st and prev and st["kind"] == "place" and prev["kind"] == "place")
    if st and st["kind"] == "results":
        p["status"] = "finished"
        _xp_finish(inst, now)


def _xp_next(inst, now, t_s):
    dev = inst["device"]
    dev["procedure"]["index"] += 1
    _xp_enter(inst, now, t_s)


def _xp_ref_g(dev, tp):
    p = dev["procedure"]
    wid = p["weights"].get(str(tp))
    on_actual = False
    if p["kind"] == "test":
        test = next(t for t in dev["tests"] if t["id"] == p["test_id"])
        on_actual = test["settings"]["result_calculation"] == "On actual weight (CMV)"
    elif p["kind"] == "external":
        on_actual = True
    return _xp_weight_mass_g(dev, wid, on_actual) if wid else None


def _xp_tick(inst, now, t_s):
    """Automatic steps: zeroing, taring, capturing a placed weight, the internal adjustment cycle."""
    dev = inst["device"]
    p = dev["procedure"]
    st = _xp_step(dev)
    if p is None or st is None or p["status"] != "running":
        return
    b = _xd_cell(inst)
    r = b.read()
    if st["kind"] == "zero":
        if _xd_ready(inst, t_s) and abs(b.gross_kg) <= 0.02 * b.capacity_kg:
            b.press("zero")
            p["zero_kg"] = b.zero_kg
            p["zero_force_N"] = b.filtered_force_N
            _xd_auto_reopen(inst)
            _xp_next(inst, now, t_s)
    elif st["kind"] == "tare":
        min_g = 0.0
        if p["kind"] == "test":
            min_g = float(next(t for t in dev["tests"] if t["id"] == p["test_id"])["settings"].get("min_tare_g") or 0.0)
        if _xd_ready(inst, t_s) and r["net_kg"] is not None and (b.gross_kg - b.zero_kg) * 1000.0 >= min_g - 1e-9:   # the container on the pan
            b.press("tare")
            p["readings"].append(dict(label="Tare", weight_g=round(b.tare_kg * 1000.0, 7), deviation_g=None, state=None))
            _xd_auto_reopen(inst)
            _xp_next(inst, now, t_s)
    elif st["kind"] == "unload":
        ref = _xp_ref_g(dev, st.get("tp", 1)) or 0.0
        if r["net_kg"] is not None and r["net_kg"] * 1000.0 < 0.5 * ref and b.stable:
            _xp_next(inst, now, t_s)
    elif st["kind"] == "place":
        ref = _xp_ref_g(dev, st["tp"])
        if ref is None or r["net_kg"] is None:
            return
        loaded = r["net_kg"] * 1000.0 >= 0.5 * ref
        if p.get("need_unload"):                                              # the weight is lifted and moved to the next position (p.45)
            if not loaded:
                p["need_unload"] = False
            return
        if loaded and dev.get("door_closed_by") is None and any(inst["doors"][s] > 1e-6 for s in ("left", "right")):
            _xd_auto_close(inst, force=p["kind"] == "external")              # the door closes and the measurement starts (p.45, p.52)
            if dev.get("door_closed_by"):
                return
        if loaded and _xd_ready(inst, t_s):
            w = r["net_kg"] * 1000.0
            if p["kind"] == "external":
                if abs(w - ref) > 0.05 * ref:                                 # not the chosen weight: keep waiting (assumption: 5 %)
                    p["message"] = "Wrong weight on the weighing pan"
                    return
                f0 = p.get("zero_force_N") or 0.0
                old = b.g_cal
                b.g_cal = (b.filtered_force_N - f0) / (ref / 1000.0)
                b.gross_kg = b.filtered_force_N / b.g_cal
                b.zero_kg = f0 / b.g_cal
                b.calibrated = True
                dev["adj_temp_c"] = dev.get("cell_temp_c", XPR_TEMP_REF_C)
                p["result"] = dict(correction_rel=b.g_cal / old - 1.0, weight_g=ref)
                p["message"] = None
            dev_g = w - ref
            p["readings"].append(dict(label=st.get("label"), weight_g=round(w, 7), deviation_g=round(dev_g, 7), state=None, tp=st["tp"]))
            _xd_auto_reopen(inst)
            _xp_next(inst, now, t_s)
    elif st["kind"] == "internal":
        if t_s - p["step_sim_t"] >= _XT_INTERNAL_S - 1e-9:
            _xp_internal_done(inst, now, t_s)


def _xp_internal_done(inst, now, t_s):
    """The internal adjustment: the internal weight (nominal 200 g, error settable) is measured with and without load; the new gain makes
    it read its nominal value.  "As found" / "as left" tests compare the internal weight reading with the limits (p.128)."""
    dev, b = inst["device"], _xd_cell(inst)
    p = dev["procedure"]
    a = dev["adjustment"]
    dev["internal_loaded"] = False
    e = dev["errors"]
    valid = b.powered and b.pan_installed and inst["doors"]["left"] <= 1e-6 and inst["doors"]["right"] <= 1e-6 and _xd_level(inst) \
        and not inst["vibration"]
    m_true = _XT_INTERNAL_KG + e["internal_weight_error_kg"]
    measured_N = m_true * _XT_G0 * (1.0 + _xd_span(dev))
    found = (measured_N / b.g_cal - _XT_INTERNAL_KG) / _XT_INTERNAL_KG * 100.0
    res = dict(correction_rel=None, as_found_pct=found if a["as_found"] else None, as_left_pct=None, as_found=None, as_left=None)
    if not valid:
        p["status"], p["result"] = "failed", dict(res, status="Aborted")
        _xd_auto_reopen(inst)
        _xp_history_adjustment(inst, now, "Aborted")
        _xd_message(dev, "adjustment_failed", "error", "Cannot start adjustment", now=now)
        return
    old = b.g_cal
    b.g_cal = measured_N / _XT_INTERNAL_KG
    b.gross_kg = (b.filtered_force_N or 0.0) / b.g_cal
    b.zero_kg = b.zero_kg * old / b.g_cal
    b.tare_kg = b.tare_kg * old / b.g_cal
    b.calibrated = True
    b.reset_stability()
    left = (measured_N / b.g_cal - _XT_INTERNAL_KG) / _XT_INTERNAL_KG * 100.0
    res.update(correction_rel=b.g_cal / old - 1.0, as_left_pct=left if a["as_left"] else None)
    dev["adj_temp_c"] = dev.get("cell_temp_c", XPR_TEMP_REF_C)                 # the temperature the cell is now adjusted at
    for k in ("as_found", "as_left"):
        if res[k + "_pct"] is not None:
            v = abs(res[k + "_pct"])
            res[k] = "Failed" if v > a["control_limit"] else "Passed with warning" if v > a["warning_limit"] else "Passed"
    status = "Failed" if res["as_left"] == "Failed" else "Done"
    p["result"] = dict(res, status=status)
    _xd_auto_reopen(inst)
    _xp_next(inst, now, t_s)


def _xp_finish(inst, now):
    """Results step: compute the test result, write the history, block the balance when the error management says so."""
    dev = inst["device"]
    p = dev["procedure"]
    if p["kind"] == "test":
        test = next(t for t in dev["tests"] if t["id"] == p["test_id"])
        s, t = test["settings"], test["type"]
        readings = [x for x in p["readings"] if x.get("tp")]
        if t == _ECC:
            center = readings[0]["weight_g"]
            for x in readings[1:]:
                x["deviation_g"] = round(x["weight_g"] - center, 7)
            value = max((abs(x["deviation_g"]) for x in readings[1:]), default=0.0)
            control, warning = s["tp1_control_g"], s["tp1_warning_g"]
        elif t in (_REP, _REPT):
            ws = [x["weight_g"] for x in readings]
            avg = sum(ws) / len(ws)
            value = _xt_math.sqrt(sum((w - avg) ** 2 for w in ws) / (len(ws) - 1)) if len(ws) > 1 else 0.0
            control, warning = s["rep_control_g"], s["rep_warning_g"]
        else:
            value, control, warning = 0.0, s["tp1_control_g"], s["tp1_warning_g"]
            for x in readings:
                ref = _xp_ref_g(dev, x["tp"])
                x["deviation_g"] = round(x["weight_g"] - ref, 7)
                c, w = (s["tp1_control_g"], s["tp1_warning_g"]) if x["tp"] == 1 else (s["tp2_control_g"], s["tp2_warning_g"])
                x["state"] = "Failed" if abs(x["deviation_g"]) > c else "Passed with warning" if abs(x["deviation_g"]) > w else "Passed"
            states = [x["state"] for x in readings]
            value = max((abs(x["deviation_g"]) for x in readings), default=0.0)
            status = "Failed" if "Failed" in states else "Passed with warning" if "Passed with warning" in states else "Passed"
        if t in (_ECC, _REP, _REPT):
            status = "Failed" if value > control else "Passed with warning" if value > warning else "Passed"
        p["result"] = dict(status=status, value_g=round(value, 7), control_g=control, warning_g=warning)
        test["last"] = dict(time=now, status=status)
        test["due"] = False
        _xd_clear(dev, f"test_due_{test['id']}")
        dev["history"]["tests"].append(dict(time=now, test=s["name"], type=t, user=p["user"], trigger=p["trigger"],
                                            level="OK" if p["level_at_start"] else "Out of level", temperature=None, result=status,
                                            value_g=round(value, 7), readings=_xt_copy.deepcopy(p["readings"]),
                                            weights=dict(p["weights"])))
        del dev["history"]["tests"][:-_XT_MAX_EVENTS]
        n = dev["failed_tests"].get(test["id"], 0)
        dev["failed_tests"][test["id"]] = n + 1 if status == "Failed" else 0
        if status == "Failed" and s["block_balance"] and dev["failed_tests"][test["id"]] > s["retries"]:
            dev["blocked"], dev["block_reason"], dev["block_seq"] = True, f"Routine test failed: {s['name']}", dev.get("block_seq", 0) + 1
    elif p["kind"] in ("internal", "external"):
        if p["kind"] == "external":
            p["result"] = dict(p.get("result") or {}, status="Done")
        _xp_history_adjustment(inst, now, p["result"]["status"])
        if p["result"]["status"] == "Failed" and dev["adjustment"]["block_balance"]:
            dev["blocked"], dev["block_reason"], dev["block_seq"] = True, "Adjustment failed", dev.get("block_seq", 0) + 1
    else:
        _xp_density_result(inst, now)


def _xp_history_adjustment(inst, now, status):
    dev = inst["device"]
    p = dev["procedure"]
    r = p.get("result") or {}
    entry = dict(time=now, type="Internal adjustment" if p["kind"] == "internal" else "External adjustment", trigger=p["trigger"],
                 user=p["user"], temperature=dev.get("cell_temp_c"), level="OK" if p["level_at_start"] else "Out of level", status=status,
                 correction_rel=r.get("correction_rel"), as_found_pct=r.get("as_found_pct"), as_left_pct=r.get("as_left_pct"),
                 as_found=r.get("as_found"), as_left=r.get("as_left"), weight=p["weights"].get("1"))
    dev["history"]["adjustments"].append(entry)
    del dev["history"]["adjustments"][:-_XT_MAX_EVENTS]
    dev["adjustment_state"]["last"] = entry
    dev["adjustment_state"]["count"] += 1
    if status == "Done":
        _xd_clear(dev, "adjustment_failed")


def xpr_water_density(t_c):
    """Density of air-free water (g/cm3) at t (degC): Tanaka et al. 2001 (CIPM) formula."""
    a1, a2, a3, a4, a5 = -3.983035, 301.797, 522528.9, 69.34881, 999.974950
    return a5 * (1 - (t_c + a1) ** 2 * (t_c + a2) / (a3 * (t_c + a4))) / 1000.0


def _xp_density_result(inst, now):
    """Density from the two weighings (Archimedes) or the pycnometer weighing; air density 0.0012 g/cm3 and the 0.99985 weight correction
    when Air density compensation is active (METTLER density-kit formulas; the operating manual names the method, not the formula)."""
    dev = inst["device"]
    p = dev["procedure"]
    m = _xd_method(dev, dev["active_method"])
    s = m["settings"]
    rho_l, alpha = (0.0012, 0.99985) if s["density.air_compensation"] else (0.0, 1.0)
    w = [x["weight_g"] for x in p["readings"]]
    t_c = p["inputs"].get("temperature_c", s["density.temperature"])
    try:
        if p["density_type"] == "Solid":
            rho0 = xpr_water_density(t_c) if p["inputs"].get("aux_liquid", s["density.aux_liquid"]) == "Distilled water" else \
                float(p["inputs"].get("aux_density_g_cm3") or s["density.aux_density"])
            a, bb = w
            rho = alpha * a / (a - bb) * (rho0 - rho_l) + rho_l
        elif p["density_type"] == "Liquid (sinker)":
            a, bb = w
            rho = alpha * (a - bb) / s["density.sinker_volume"] + rho_l
        else:
            rho = alpha * (w[0] - s["density.pyc_weight"]) / s["density.pyc_volume"] + rho_l
    except ZeroDivisionError:
        rho = None
    unit = s["density.unit"]
    val = None if rho is None else rho * (1000.0 if unit in ("kg/m3", "g/l") else 1.0)
    dec = int(s["density.decimals"])
    text = "—" if val is None else f"{val:.{dec}f}"
    task = dev["task"]
    res = dict(index=len(task["results"]) + 1, result_ids=list(task["result_ids"]), net_kg=None, tare_kg=0.0, gross_kg=None, unit=unit,
               value=None if val is None else round(val, dec), text=text, stable=True, calculated=True, time=now, state="OK" if val else "Not OK",
               tolerance="No tolerance", level="OK" if _xd_level(inst) else "Out of level", adjustment="OK", excluded=False, comment="",
               item=None, sample_id=None, density=dict(type=p["density_type"], readings=_xt_copy.deepcopy(p["readings"]), temperature_c=t_c))
    task["results"].append(res)
    task["counter"] += 1
    task["result_ids"] = _xd_ids(m, "result", now, task["counter"])
    p["result"] = dict(status="Done", density=res["value"], unit=unit, text=text)


# ---- clock-driven behaviour (sim.step)
def _xd_tick(inst, t0, t1, rt):
    """Everything the terminal does between two steps: pending zero / tare / capture, procedures, automation, leveling messages, scheduled
    adjustments and tests, reminders, automatic standby and logout."""
    dev, b = inst["device"], _xd_cell(inst)
    now = _xt_now_text(dev, t1)
    if not b.powered:
        return
    p = dev["procedure"]
    if p is not None and p["status"] == "running":
        _xp_tick(inst, now, t1)
    pend = dev["pending"]
    if pend is not None:
        if pend["action"] in ("zero", "tare"):
            if _xd_ready(inst, t1):
                code = _xd_zero_tare(inst, pend["action"])
                dev["pending"] = None
                _xd_auto_reopen(inst)
                if code == "ZERO_RANGE":
                    _xd_message(dev, "zero_range", "warning", "The weight is outside the zero range.", now=now)
            elif t1 - pend["since"] >= _XT_TIMEOUT_S:
                dev["pending"] = None
                _xd_auto_reopen(inst)
                _xd_message(dev, "not_stable", "warning", "The weight did not become stable.", now=now)
        elif pend["action"] == "capture" and dev["task"] is not None:
            m = _xd_method(dev, dev["active_method"])
            mode = m["settings"]["weighing.capture_mode"]
            if mode == "Immediate" and t1 - pend["since"] >= m["settings"]["weighing.capture_delay"] - 1e-9:
                dev["pending"] = None
                if _xd_reading_ok(inst):
                    _xd_capture(inst, now, b.stable)
                _xd_auto_reopen(inst)
            elif mode == "Stable" and _xd_ready(inst, t1) and _xd_reading_ok(inst):
                dev["pending"] = None
                _xd_capture(inst, now, True)
                _xd_auto_reopen(inst)
            elif mode == "Stable" and t1 - pend["since"] >= _XT_TIMEOUT_S:
                dev["pending"] = None
                _xd_auto_reopen(inst)
                _xd_message(dev, "not_stable", "warning", "The weight did not become stable.", now=now)
    _xd_automation(inst, now, t1)
    # leveling (p.29, p.78) and start after leveling (p.129)
    ok = _xd_level(inst)
    if ok != dev["level_ok"]:
        dev["level_ok"] = ok
        if not ok and dev["settings"]["quality.leveling_warning"] != "Inactive":
            _xd_message(dev, "level", "warning", "Balance is out of level", action="level", now=now)
        if ok:
            _xd_clear(dev, "level")
            a = dev["adjustment"]
            if a["strategy"] == "Internal adjustment" and a["start_after_leveling"] and dev["procedure"] is None:
                _xp_start(inst, "internal", now, t1, trigger="Leveling")
    # start after a temperature change of 1 degC since the last adjustment (p.129)
    a = dev["adjustment"]
    if a["strategy"] == "Internal adjustment" and a["start_after_temperature"] and dev["procedure"] is None and \
            abs(dev.get("cell_temp_c", XPR_TEMP_REF_C) - dev.get("adj_temp_c", XPR_TEMP_REF_C)) >= XPR_TEMP_TRIGGER_K - 1e-9:
        dev["adj_temp_c"] = dev["cell_temp_c"]                              # one start per change (set again when the adjustment is done)
        _xp_start(inst, "internal", now, t1, trigger="Temperature")
    _xd_schedules(inst, t0, t1)
    _xd_reminders(inst, t1)
    # automatic standby (p.85) and automatic logout (p.75)
    idle = t1 - dev["last_activity_s"]
    s = dev["settings"]
    if s["general.standby"] and s["general.standby_wait"] > 0 and not dev["standby"] and idle >= s["general.standby_wait"] * 60.0:
        dev["standby"] = True
        if dev["user_management"]:
            dev["logged_in"] = False
    if dev["user_management"] and dev["logged_in"] and s["users.automatic_logout"] and idle >= s["users.logout_wait"] * 60.0:
        dev["logged_in"] = False
    if abs(dev["errors"]["zero_drift_kg_s"]) > 0:
        dev["drift_kg"] += dev["errors"]["zero_drift_kg_s"] * (t1 - t0)


def _xd_automation(inst, now, t):
    """Automatic zero, automatic tare and automatic result of the running method (p.96-97)."""
    dev, b = inst["device"], _xd_cell(inst)
    task = dev["task"]
    if task is None or dev["procedure"] is not None or dev["pending"] is not None or not _xd_ready(inst, t):
        return
    m = _xd_method(dev, dev["active_method"])
    s = m["settings"]
    r = b.read()
    if r["net_kg"] is None:
        return
    net_g = r["net_kg"] * 1000.0
    auto = dev["auto"]
    if s.get("auto.zero") and s.get("auto.zero_threshold") is not None and b.tare_kg == 0.0 and 0.0 < abs(net_g) < s["auto.zero_threshold"]:
        if abs(b.gross_kg) <= 0.02 * b.capacity_kg:
            b.press("zero")
            return
    if s.get("auto.tare_mode") == "Automatic tare" and s.get("auto.tare_threshold") is not None:
        if b.tare_kg == 0.0 and net_g >= s["auto.tare_threshold"] and auto.get("tare_armed", True):
            b.press("tare")
            auto["tare_armed"] = False
            return
        if b.gross_kg - b.zero_kg < s["auto.tare_threshold"] / 1000.0 * 0.5 and b.tare_kg != 0.0:
            auto["tare_armed"] = True
    mode, thr = s.get("auto.result", "None"), s.get("auto.result_threshold")
    if mode == "None" or thr is None or (dev["settings"]["quality.leveling_warning"] == "Forced leveling" and not _xd_level(inst)):
        return
    trig = s.get("auto.weight_trigger", "Exceeding") if mode == "Without sample tare" else "Exceeding"
    if trig == "Exceeding":
        if auto["result_armed"] and net_g > thr:
            _xd_capture(inst, now, True)
            auto["result_armed"] = False
            auto["removed_wait"] = mode == "With sample tare"
        elif not auto["result_armed"] and net_g < thr:
            if auto.get("removed_wait"):
                b.press("tare")                                               # sample removed: tare what is left (p.96)
                auto["removed_wait"] = False
            auto["result_armed"] = True
    else:
        if net_g > thr:
            auto["captured"] = r["net_kg"]
        elif auto.get("captured") is not None and net_g < thr:
            _xd_capture(inst, now, True, net_kg=auto["captured"])             # the stable weight before it fell below the threshold
            auto["captured"] = None


def _xd_schedules(inst, t0, t1):
    """Scheduled internal adjustments (start times on the preferred days) and planned routine tests (due messages)."""
    dev = inst["device"]
    a = dev["adjustment"]
    c0 = _XT_EPOCH + _xt_dt.timedelta(seconds=t0 + dev["clock_offset_s"])
    c1 = _XT_EPOCH + _xt_dt.timedelta(seconds=t1 + dev["clock_offset_s"])
    if c1 <= c0:
        return
    day = c1.date()
    while day >= c0.date():
        wd = XPR_WEEKDAYS[day.weekday()]
        n = {"1 start time": 1, "2 start times": 2, "3 start times": 3}.get(a["schedule"], 0)
        if a["strategy"] == "Internal adjustment" and wd in a["preferred_days"]:
            for i in range(1, n + 1):
                h, mi = _xt_parse_time(a[f"start_time_{i}"])
                at = _xt_dt.datetime(day.year, day.month, day.day, h, mi)
                if c0 < at <= c1 and dev["procedure"] is None:
                    _xp_start(inst, "internal", c1.replace(microsecond=0).isoformat(), t1, trigger="Schedule")
        for test in dev["tests"]:
            s = test["settings"]
            if s["planning"] == "Manually" or not s["activated"]:
                continue
            h, mi = _xt_parse_time(s["start_time"])
            at = _xt_dt.datetime(day.year, day.month, day.day, h, mi)
            if not c0 < at <= c1:
                continue
            last = _xt_dt.datetime.fromisoformat(test["last"]["time"]) if test.get("last") else None
            due = {"Daily": True, "Weekly": wd in s["preferred_days"],
                   "Monthly": (s["day_of_week"] == "None" and (last is None or (at - last).days >= 28)) or
                   (s["day_of_week"] == wd and (day.day - 1) // 7 == ("First", "Second", "Third", "Fourth").index(s["week_of_month"])),
                   "Quarterly": last is None or (at - last).days >= 90, "Annually": last is None or (at - last).days >= 365}[s["planning"]]
            if due:
                test["due"] = True
                _xd_message(dev, f"test_due_{test['id']}", "warning", f"Routine test is due: {s['name']}", action="tests",
                            now=c1.replace(microsecond=0).isoformat())
        day -= _xt_dt.timedelta(days=1)


def _xd_reminders(inst, t1):
    dev = inst["device"]
    s = dev["settings"]
    today = (_XT_EPOCH + _xt_dt.timedelta(seconds=t1 + dev["clock_offset_s"])).date()
    for w in dev["test_weights"]:
        key = f"weight_recal_{w['id']}"
        due = _xt_parse_date(w.get("next_calibration") or "")
        if s["quality.weight_recal_reminder"] and due is not None and (due - today).days <= s["quality.weight_recal_days"]:
            _xd_message(dev, key, "warning", f"Test weight calibration due: {w['name']} ({due.isoformat()})")
        else:
            _xd_clear(dev, key)


# ---------------------------------------------------------------- display values (what the touch screen shows)
def _xd_display(inst, time_s, rt_self=None):
    dev, b = inst["device"], _xd_cell(inst)
    r = b.read()
    m = _xd_method(dev, dev["active_method"])
    s = m["settings"] if m else {}
    unit = _xd_unit(dev)
    profile = _xd_active_profile(dev)
    w = dict(text="", unit=_xd_unit_name(dev, unit), info_text="", net=b.tare_kg != 0.0, stable=r["stable"], calculated=bool(dev.get("preset_active")),
             state="value", busy="", indicator=None if profile["indicator"] == "None" else dict(color=profile["indicator"].lower(),
                                                                                                 text=profile["indicator_text"]))
    if r["status"] == "OFF":
        w.update(state="off", text="")
    elif _xd_fault_blocks_weight(dev):                                       # an injected error of p.134: no weight value
        w.update(state="none", text="--")
    elif r["status"] == "OVERLOAD":
        w.update(state="overload", text="Overload")
    elif r["status"] in ("UNDERLOAD", "PAN_REMOVED", "PAN_UNLATCHED", "TRANSPORT_LOCKED"):
        w.update(state="underload", text="Underload")
    elif r["net_kg"] is None:
        w.update(state="none", text="--")
    else:
        if unit == "PCS":
            v = xpr_convert(r["net_kg"], "PCS", s)
            w["text"] = "--" if v is None else str(int(round(v)))
        else:
            step, dec = xpr_unit_step(unit, profile["readability"], s)
            w["text"] = xpr_format(xpr_convert(r["net_kg"], unit, s), step, dec)
        if s.get("weighing.show_info") and s.get("weighing.info_unit") in XPR_UNITS:
            iu = s["weighing.info_unit"]
            step, dec = xpr_unit_step(iu, profile["readability"])
            w["info_text"] = f"{xpr_format(xpr_convert(r['net_kg'], iu), step, dec)} {iu}"
    pend = dev["pending"]
    if pend is not None:
        w["busy"] = {"zero": "Zeroing...", "tare": "Taring...", "capture": "Capturing weight..."}[pend["action"]]
    trac = dict(active=False, fraction=None, low=None, high=None, target_text="", minus_text="", plus_text="", color="neutral")
    if m and dev["task"] is not None and s.get("weighing.smarttrac") and r["net_kg"] is not None:
        tol = _xd_tolerance(m, dev["task"], unit)
        cur = xpr_convert(r["net_kg"], unit, s)
        if tol is not None and cur is not None and tol[0] > 0:
            target, lo, hi = tol
            full = hi * 1.25
            item = m["items"][min(dev["task"]["item_index"], len(m["items"]) - 1)] if m["type"] == _GWI and m["items"] else None
            tm = item["tol_minus"] if item else s.get("item.tol_minus")
            tp = item["tol_plus"] if item else s.get("item.tol_plus")
            trac.update(active=True, fraction=min(max(cur / full, 0.0), 1.0), low=lo / full, high=hi / full,
                        target_text=f"{_xt_fmt_g(target)} {_xd_unit_name(dev, unit)}", minus_text=f"-{_xt_fmt_g(tm)} %", plus_text=f"+{_xt_fmt_g(tp)} %",
                        color="ok" if lo - 1e-12 <= cur <= hi + 1e-12 else "warn" if cur < lo else "error")
    return w, trac


def _xt_fmt_g(v):
    return "" if v is None else (f"{v:.6g}")


def _xd_status_light(inst):
    dev, b = inst["device"], _xd_cell(inst)
    mode = dev["settings"]["screen.status_light"]
    if not b.powered or dev["standby"] or mode == "Inactive":
        return "off"
    levels = {m["level"] for m in dev["messages"] if not m["acknowledged"]}
    if dev["blocked"] or "error" in levels:
        return "red"
    if "warning" in levels or not _xd_level(inst):
        return "yellow"
    return "off" if mode == "Active (without green light)" else "green"


def _xd_level_state(inst):
    t, d = inst["tilt_rad"], inst.get("tilt_direction_rad", 0.0)
    x, y = _xt_math.sin(d) * t / _XT_BUBBLE_RAD, -_xt_math.cos(d) * t / _XT_BUBBLE_RAD
    x, y = max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y))
    turns = abs(t) * _XT_FOOT_SPAN_M / _XT_FOOT_PITCH_M
    ok = _xd_level(inst)
    if ok:
        text = "The balance is leveled."
    else:
        foot = "left" if _xt_math.sin(d) * t > 0 else "right"
        sense = "clockwise" if _xt_math.cos(d) * t >= 0 else "counterclockwise"
        text = f"Turn the {foot} leveling foot {sense} about {turns:.1f} turns until the dot is in the center."
    return dict(ok=ok, tilt_deg=round(_xt_math.degrees(abs(t)), 4), x=round(x, 4), y=round(y, 4), ok_radius=round(_XT_LEVEL_RAD / _XT_BUBBLE_RAD, 4),
                instruction=text)


# ---------------------------------------------------------------- commands of the terminal (capabilities beyond the CHEM-001 command set)
class _XTReject(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _need(cond, code="PRECONDITION_FAILED"):
    if not cond:
        raise _XTReject(code)


def _gate(inst, perm=None, login=True, blocked=False, busy=False, powered=True):
    """Common refusals: mains off, standby, login (user management), the group right, a blocked balance, a running procedure."""
    dev, b = inst["device"], _xd_cell(inst)
    _need(not powered or b.powered)
    _need(not powered or not dev["standby"], "STANDBY")
    if login and dev["user_management"]:
        _need(dev["logged_in"], "LOGIN_REQUIRED")
    if perm:
        _need(_xd_allowed(dev, perm), "PERMISSION_DENIED")
    if blocked:
        _need(not dev["blocked"], "BALANCE_BLOCKED")
    if busy:
        _need(dev["procedure"] is None or dev["procedure"]["status"] != "running", "BUSY")


def _xt_settings_values(fields, current, changes, create=False):
    """Validated new values of a settings group (unknown keys, types, ranges, create-only fields)."""
    defs = {f["key"]: f for f in fields}
    out = dict(current)
    for k, v in changes.items():
        f = defs.get(k)
        _need(f is not None and _xt_check_value(f, v), "INVALID_ARGUMENT")
        _need(not f.get("readonly"), "PERMISSION_DENIED")
        _need(create or not f.get("create_only") or current.get(k) == v, "INVALID_ARGUMENT")
        out[k] = _xt_copy.deepcopy(v)
    return out


def _c_standby(rt, inst, a, now, t):
    dev = inst["device"]
    _need(_xd_cell(inst).powered)
    if a["active"] and not dev["standby"]:
        dev["standby"] = True
        if dev["user_management"]:
            dev["logged_in"] = False
    elif not a["active"] and dev["standby"]:
        dev["standby"] = False
        dev["power_epoch"] += 1                                              # the screen restarts on the main weighing screen
    return dict(active=dev["standby"])


def _c_door_key(rt, inst, a, now, t):
    """A door key of the terminal: opens / closes the doors whose 'Door key left / right' setting is active (p.17, p.83)."""
    _gate(inst, login=False)
    s = inst["device"]["settings"]
    sides = [side for side in ("left", "right") if s[f"door_{side}.key_{a['key']}"]]
    action = "close" if any(inst["doors"][x] > 1e-6 for x in sides) else "open"
    moved = _xd_doors(inst, sides, action)
    return dict(moved=moved, left_m=inst["doors"]["left"], right_m=inst["doors"]["right"])


def _c_door_operate(rt, inst, a, now, t):
    _gate(inst, login=False)
    sides = ("left", "right") if a["side"] == "both" else (a["side"],)
    moved = _xd_doors(inst, sides, a["action"])
    return dict(moved=moved, left_m=inst["doors"]["left"], right_m=inst["doors"]["right"])


def _c_request(rt, inst, a, now, t):
    """Zero / Tare keys: immediately when the weight is stable, else 'Zeroing... / Taring...' until it is (40 s at most); doors with
    'Automatic (Tare / Zero / Result)' close first."""
    dev = inst["device"]
    _gate(inst, login=False, busy=True)
    _need(not _xd_fault_blocks_weight(dev), "NO_WEIGHT")
    _need(dev["pending"] is None or dev["pending"]["action"] != "capture", "BUSY")
    _xd_auto_close(inst)
    if _xd_ready(inst, t):
        code = _xd_zero_tare(inst, a["action"])
        if code:
            _xd_auto_reopen(inst)
            raise _XTReject(code)
        _xd_auto_reopen(inst)
        dev["pending"] = None
        return dict(executed=True, pending=False)
    dev["pending"] = dict(action=a["action"], since=t)
    return dict(executed=False, pending=True)


def _xd_allowed_units(dev):
    """Units of the active tolerance profile (p.80), the method's custom unit and PCS for piece counting."""
    m = _xd_method(dev, dev["active_method"])
    allowed = [u for u in XPR_UNITS if u in _xd_active_profile(dev)["units"]]
    if m and m["settings"].get("custom.active"):
        allowed.append("custom")
    if m and m["type"] == _PCS:
        allowed.append("PCS")
    return allowed


def _c_set_unit(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst)
    _need(a["unit"] in _xd_allowed_units(dev))
    dev["display_unit"] = a["unit"]
    return dict(unit=a["unit"])


def _xt_section_perm(dev, keys):
    """Right needed to change these settings: screen / sound and the (user-specific) language and keyboard are free (p.77), Users > General
    needs Configure user management, everything else Configure system."""
    free = {k for k in keys if XPR_SETTING.get(k, {}).get("section") in _XT_FREE_SECTIONS or k in ("region.language", "region.keyboard")}
    if set(keys) == free:
        return None
    if any(XPR_SETTING.get(k, {}).get("section") in _XT_USER_SECTIONS for k in keys):
        return "configure_users"
    return "configure_system"


def _c_settings(rt, inst, a, now, t):
    dev = inst["device"]
    ch = a["settings"]
    _gate(inst, perm=_xt_section_perm(dev, ch))
    _need(not dev["blocked"] or {XPR_SETTING.get(k, {}).get("section") for k in ch} <= {"screen", "region"}, "BALANCE_BLOCKED")
    new = _xt_settings_values(XPR_SETTINGS, dev["settings"], ch)
    for svc in ("services.labx", "services.mtsics", "services.web"):        # only one service at a time (p.88): the others go inactive
        if svc in ch and ch[svc] != "Inactive":
            for other in ("services.labx", "services.mtsics", "services.web"):
                if other != svc and other not in ch:
                    new[other] = "Inactive"
    if new["general.export_directory"] == "File server":
        _need(new["services.file_server"])
    for k in sorted(set(new)):
        _xd_change(dev, now, f"Settings / {k}", dev["settings"][k], new[k])
    changed = sorted(k for k in new if new[k] != dev["settings"][k])
    if new["region.time_zone"] != dev["settings"]["region.time_zone"]:   # the clock runs on; local time = UTC + the zone (standard time)
        dev["clock_offset_s"] += 3600.0 * (XPR_ZONE_H[new["region.time_zone"]] - XPR_ZONE_H[dev["settings"]["region.time_zone"]])
    dev["settings"] = new
    if "region.language" in ch and dev["user_management"] and dev["logged_in"]:
        for u in dev["users"]:
            if u["user_name"] == dev["current_user"]:
                u["language"] = ch["region.language"]                         # user-specific language (p.77)
    if "quality.leveling_warning" in ch:
        dev["level_ok"] = _xd_level(inst)
        if not dev["level_ok"] and new["quality.leveling_warning"] != "Inactive":
            _xd_message(dev, "level", "warning", "Balance is out of level", action="level", now=now)
        elif new["quality.leveling_warning"] == "Inactive":
            _xd_clear(dev, "level")
    return dict(changed=changed)


def _c_clock(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_system")
    cur = _XT_EPOCH + _xt_dt.timedelta(seconds=t + dev["clock_offset_s"])
    d = _xt_parse_date(a["date"]) if a.get("date") else cur.date()
    hm = _xt_parse_time(a["time"]) if a.get("time") else (cur.hour, cur.minute)
    _need(d is not None and hm is not None and 2000 <= d.year <= 2099, "INVALID_ARGUMENT")
    target = _xt_dt.datetime(d.year, d.month, d.day, hm[0], hm[1], 0 if a.get("time") else cur.second)
    dev["clock_offset_s"] = (target - _XT_EPOCH).total_seconds() - t
    _xd_change(dev, now, "Settings / Date / Time", cur.replace(microsecond=0).isoformat(), target.isoformat())
    return dict(datetime=target.isoformat())


def _c_user_management(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_users")
    if a["active"] and not dev["user_management"]:
        admin = next(u for u in dev["users"] if u["user_name"] == "Administrator") if any(
            u["user_name"] == "Administrator" for u in dev["users"]) else None
        if admin is not None and a.get("password") is not None:
            admin["password"] = a["password"]                                 # Set administrator password (optional), p.67
        dev["user_management"], dev["current_user"], dev["logged_in"] = True, "Administrator", True
    elif not a["active"] and dev["user_management"]:
        if a.get("password") is not None:
            dev["unblocking_password"] = a["password"]                         # Set unblocking password (optional), p.68
        dev["user_management"], dev["current_user"], dev["logged_in"] = False, "Administrator", True
    _xd_change(dev, now, "Settings / User management", not a["active"], a["active"])
    return dict(active=dev["user_management"])


def _c_login(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, login=False)
    _need(dev["user_management"])
    u = next((u for u in dev["users"] if u["user_name"] == a["user_name"]), None)
    _need(u is not None and u["active"] and u["password"] == a["password"], "WRONG_PASSWORD")
    dev["current_user"], dev["logged_in"] = u["user_name"], True
    return dict(user_name=u["user_name"])


def _c_logout(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, login=False)
    _need(dev["user_management"] and dev["logged_in"])
    dev["logged_in"] = False
    return dict(logged_in=False)


def _c_user_save(rt, inst, a, now, t):
    dev = inst["device"]
    own = dev["logged_in"] and a["user_name"] == dev["current_user"] and set(a["settings"]) <= {"language"}
    _gate(inst, perm=None if own else "configure_users")
    users = {u["user_name"]: u for u in dev["users"]}
    old = users.get(a["user_name"])
    base = dict(_xt_defaults(XPR_USER_FIELDS), user_name=a["user_name"], password="") if old is None else old
    new = _xt_settings_values(XPR_USER_FIELDS, base, dict(a["settings"], user_name=a["user_name"]), create=old is None)
    groups = {g["group_name"] for g in dev["groups"]}
    _need(new["groups"] and set(new["groups"]) <= groups, "INVALID_ARGUMENT")
    if old is None:
        dev["users"].append(new)
    else:
        dev["users"][dev["users"].index(old)] = new
    _xd_change(dev, now, f"Users / {a['user_name']}", None if old is None else {k: v for k, v in old.items() if k != "password"},
               {k: v for k, v in new.items() if k != "password"})
    return dict(user_name=a["user_name"], created=old is None)


def _c_user_delete(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_users")
    u = next((u for u in dev["users"] if u["user_name"] == a["user_name"]), None)
    _need(u is not None, "NOT_FOUND")
    _need(not (dev["logged_in"] and dev["current_user"] == u["user_name"]), "IN_USE")
    dev["users"].remove(u)
    _xd_change(dev, now, f"Users / {a['user_name']}", "user", None)
    return dict(user_name=a["user_name"])


def _c_user_password(rt, inst, a, now, t):
    dev = inst["device"]
    own = dev["logged_in"] and a["user_name"] == dev["current_user"]
    _gate(inst, perm=None if own else "configure_users")
    u = next((u for u in dev["users"] if u["user_name"] == a["user_name"]), None)
    _need(u is not None, "NOT_FOUND")
    u["password"] = a["password"]
    _xd_change(dev, now, f"Users / {a['user_name']} / password", "***", "***")
    return dict(user_name=a["user_name"])


def _c_group_save(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_users")
    old = next((g for g in dev["groups"] if g["group_name"] == a["group_name"]), None)
    base = dict(_xt_defaults(XPR_GROUP_FIELDS), group_name=a["group_name"]) if old is None else old
    new = _xt_settings_values(XPR_GROUP_FIELDS, base, dict(a["permissions"], group_name=a["group_name"]), create=old is None)
    if old is None:
        dev["groups"].append(new)
    else:
        dev["groups"][dev["groups"].index(old)] = new
    _xd_change(dev, now, f"Groups / {a['group_name']}", old, new)
    return dict(group_name=a["group_name"], created=old is None)


def _c_group_delete(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_users")
    g = next((g for g in dev["groups"] if g["group_name"] == a["group_name"]), None)
    _need(g is not None, "NOT_FOUND")
    _need(not any(a["group_name"] in u["groups"] for u in dev["users"]), "IN_USE")
    dev["groups"].remove(g)
    _xd_change(dev, now, f"Groups / {a['group_name']}", "group", None)
    return dict(group_name=a["group_name"])


def _c_block(rt, inst, a, now, t):
    """Block / unblock (p.71): with user management the group right is needed, without it the unblocking password (if one is set)."""
    dev = inst["device"]
    _gate(inst, perm="block_unblock")
    if not dev["user_management"] and dev["unblocking_password"]:
        _need(a.get("password") == dev["unblocking_password"], "WRONG_PASSWORD")
    if a["block"]:
        dev["blocked"], dev["block_reason"], dev["block_seq"] = True, "Blocked by the user", dev.get("block_seq", 0) + 1
    else:
        dev["blocked"], dev["block_reason"] = False, None
        dev["failed_tests"] = {}
    _xd_change(dev, now, "Balance / blocked", not a["block"], a["block"])
    return dict(blocked=dev["blocked"])


def _c_unblocking_password(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_system")
    _need(not dev["user_management"])
    dev["unblocking_password"] = a["password"]
    return dict(set=bool(a["password"]))


def _c_profile_save(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True)
    pid = a.get("profile_id")
    old = _xd_profile(dev, pid) if pid else None
    _need(pid is None or old is not None, "NOT_FOUND")
    base = _xt_defaults(XPR_PROFILE_FIELDS) if old is None else {k: v for k, v in old.items() if k != "id"}
    new = _xt_settings_values(XPR_PROFILE_FIELDS, base, a["settings"])
    _need(new["units"], "INVALID_ARGUMENT")
    if old is None:
        dev["seq"]["tp"] += 1
        pid = f"tp{dev['seq']['tp']}"
        dev["profiles"].append(dict(id=pid, **new))
    else:
        old.update(new)
        if pid == dev["default_profile"]:
            for test in dev["tests"]:
                test["last"] = None                                           # p.69: routine tests become 'Never executed'
    _xd_change(dev, now, f"Tolerance profiles / {pid}", base if old else None, new)
    _xd_apply_profile(inst)
    return dict(profile_id=pid)


def _c_profile_delete(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True)
    p = _xd_profile(dev, a["profile_id"])
    _need(p is not None, "NOT_FOUND")
    _need(a["profile_id"] != dev["default_profile"] and not any(m["settings"].get("weighing.profile") == a["profile_id"] for m in dev["methods"]),
          "IN_USE")
    dev["profiles"].remove(p)
    _xd_change(dev, now, f"Tolerance profiles / {a['profile_id']}", p["name"], None)
    return dict(profile_id=a["profile_id"])


def _c_profile_default(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True)
    _need(_xd_profile(dev, a["profile_id"]) is not None, "NOT_FOUND")
    _xd_change(dev, now, "Tolerance profiles / default", dev["default_profile"], a["profile_id"])
    dev["default_profile"] = a["profile_id"]
    _xd_apply_profile(inst)
    return dict(profile_id=a["profile_id"])


def _xt_method_settings(dev, mtype, base, changes, create):
    new = _xt_settings_values(xpr_method_fields(mtype), base, changes, create=create)
    _need(new["name"], "INVALID_ARGUMENT")
    pid = new.get("weighing.profile")
    _need(pid is None or _xd_profile(dev, pid) is not None, "INVALID_ARGUMENT")
    unit = new.get("item.unit")
    _need(unit in XPR_UNITS or (unit == "custom" and new.get("custom.active")) or (unit == "PCS" and mtype == _PCS), "INVALID_ARGUMENT")
    return new


def _xt_items(items):
    out = []
    for it in items:
        _need(isinstance(it, dict) and set(it) <= {"sample_id", "unit", "target", "tol_minus", "tol_plus"}, "INVALID_ARGUMENT")
        x = {**dict(sample_id="", unit="g", target=None, tol_minus=2.5, tol_plus=2.5), **it}
        _need(isinstance(x["sample_id"], str) and len(x["sample_id"]) <= 32 and x["unit"] in XPR_UNITS, "INVALID_ARGUMENT")
        for k in ("target", "tol_minus", "tol_plus"):
            _need(x[k] is None or (type(x[k]) in (int, float) and _xt_math.isfinite(x[k]) and x[k] >= 0), "INVALID_ARGUMENT")
        out.append(x)
    return out


def _c_method_create(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_methods", blocked=True)
    _need(len(dev["methods"]) < 50)
    dev["seq"]["m"] += 1
    mid = f"m{dev['seq']['m']}"
    m = _xt_new_method(mid, a["settings"].get("name", ""), a["type"], now[:10])
    m["settings"] = _xt_method_settings(dev, a["type"], m["settings"], a["settings"], create=True)
    if "items" in a:
        _need(a["type"] == _GWI and a["items"], "INVALID_ARGUMENT")
        m["items"] = _xt_items(a["items"])
    dev["methods"].append(m)
    _xd_change(dev, now, f"Methods / {m['settings']['name']}", None, "created")
    return dict(method_id=mid)


def _c_method_configure(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_methods", blocked=True)
    m = _xd_method(dev, a["method_id"])
    _need(m is not None, "NOT_FOUND")
    _need(not m["settings"]["lock"] or (set(a["settings"]) <= {"lock"} and "items" not in a), "METHOD_LOCKED")
    new = _xt_method_settings(dev, m["type"], m["settings"], a["settings"], create=False)
    for k in new:
        _xd_change(dev, now, f"Methods / {m['settings']['name']} / {k}", m["settings"].get(k), new[k])
    m["settings"] = new
    if "items" in a:
        _need(m["type"] == _GWI and a["items"], "INVALID_ARGUMENT")
        m["items"] = _xt_items(a["items"])
    if dev["active_method"] == m["id"]:
        task = dev["task"]
        if task is not None:                                                  # the running task follows the new ID format
            task["changed"] = True
            new_ids = _xd_ids(m, "task", task["started"], 1)
            task["task_ids"] = [task["task_ids"][i] if i < len(task["task_ids"]) and new["ids.task_{}_type".format(i + 1)] == "Manual with default"
                                else v for i, v in enumerate(new_ids)]
            task["result_ids"] = _xd_ids(m, "result", now, task["counter"])
        _xd_apply_profile(inst)
        _xd_apply_preset(inst)
    return dict(method_id=m["id"])


def _c_method_clone(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_methods", blocked=True)
    m = _xd_method(dev, a["method_id"])
    _need(m is not None, "NOT_FOUND")
    _need(len(dev["methods"]) < 50)
    names = {x["settings"]["name"] for x in dev["methods"]}
    n = 1
    while f"{m['settings']['name']} {n}"[:22] in names:
        n += 1
    dev["seq"]["m"] += 1
    c = _xt_copy.deepcopy(m)
    c.update(id=f"m{dev['seq']['m']}", created=now[:10])
    c["settings"]["name"] = f"{m['settings']['name']} {n}"[:22]
    dev["methods"].append(c)
    _xd_change(dev, now, f"Methods / {c['settings']['name']}", None, f"cloned from {m['settings']['name']}")
    return dict(method_id=c["id"])


def _c_method_delete(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_methods", blocked=True)
    m = _xd_method(dev, a["method_id"])
    _need(m is not None, "NOT_FOUND")
    _need(dev["active_method"] != m["id"], "IN_USE")                         # the method in the background cannot be deleted (p.40)
    dev["methods"].remove(m)
    dev["tasks"].pop(m["id"], None)
    _xd_change(dev, now, f"Methods / {m['settings']['name']}", "method", None)
    return dict(method_id=a["method_id"])


def _c_method_start(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, blocked=True, busy=True)
    _need(_xd_method(dev, a["method_id"]) is not None, "NOT_FOUND")
    _need(dev.get("fault") != "performance_optimization")                  # p.134: memory full, complete the task and restart first
    _xd_start_task(inst, a["method_id"], now)
    return dict(method_id=a["method_id"])


def _c_fault_inject(rt, inst, a, now, t):
    """Evaluator: one of the error messages of manual 8.1 (p.134) with its behaviour: the message (status bar, red StatusLight); the weight
    errors stop zeroing / taring / results / readings and the display shows --; a pending zero / tare is dropped."""
    dev = inst["device"]
    _need(dev.get("fault") is None)
    dev["fault"] = a["code"]
    _xd_message(dev, "fault", "error", XPR_FAULTS[a["code"]][0], now=now)
    if XPR_FAULTS[a["code"]][1]:
        dev["pending"] = None
    return dict(code=a["code"])


def _c_fault_clear(rt, inst, a, now, t):
    """Evaluator: the cause is removed (cable replaced, battery charged, power reconnected)."""
    dev = inst["device"]
    dev["fault"] = None
    _xd_clear(dev, "fault")
    return dict(cleared=True)


def _c_save_itemized(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_methods", blocked=True)
    task = dev["task"]
    _need(task is not None)
    src = _xd_method(dev, dev["active_method"])
    _need(src["type"] == _GW)
    rows = [r for r in task["results"] if not r["excluded"] and r["value"] is not None and r["unit"] in XPR_UNITS]
    _need(rows)
    s = src["settings"]
    items = [dict(sample_id=(r["result_ids"][0] if r["result_ids"] else f"Item {i + 1}") or f"Item {i + 1}", unit=r["unit"], target=r["value"],
                  tol_minus=s.get("item.tol_minus") or 0.0, tol_plus=s.get("item.tol_plus") or 0.0) for i, r in enumerate(rows)]
    return _c_method_create(rt, inst, dict(type=_GWI, settings={"name": a["name"]}, items=items), now, t)


def _c_item_save(rt, inst, a, now, t):
    """Weighing items of an itemized method (p.41, p.94): new (index null) or changed item."""
    dev = inst["device"]
    _gate(inst, perm="configure_methods", blocked=True)
    m = _xd_method(dev, a["method_id"])
    _need(m is not None, "NOT_FOUND")
    _need(m["type"] == _GWI)
    _need(not m["settings"]["lock"], "METHOD_LOCKED")
    item = _xt_items([a["item"]])[0]
    if a["index"] is None:
        m["items"].append(item)
        index = len(m["items"])
    else:
        _need(1 <= a["index"] <= len(m["items"]), "NOT_FOUND")
        m["items"][a["index"] - 1] = item
        index = a["index"]
    _xd_change(dev, now, f"Methods / {m['settings']['name']} / item {index}", None, item)
    return dict(index=index)


def _c_item_delete(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_methods", blocked=True)
    m = _xd_method(dev, a["method_id"])
    _need(m is not None and m["type"] == _GWI and 1 <= a["index"] <= len(m["items"]), "NOT_FOUND")
    _need(not m["settings"]["lock"], "METHOD_LOCKED")
    _need(len(m["items"]) > 1)                                                # an itemized method keeps at least one item
    del m["items"][a["index"] - 1]
    _xd_change(dev, now, f"Methods / {m['settings']['name']} / item {a['index']}", "item", None)
    return dict(index=a["index"])


def _c_add_result(rt, inst, a, now, t):
    """Add result (p.31): Stable capture waits for stability ('Capturing weight...', 40 s at most), Immediate waits the capture delay;
    Forced leveling refuses it while out of level (p.78); doors with automatic closing close first."""
    dev = inst["device"]
    _gate(inst, blocked=True, busy=True)
    _need(dev["task"] is not None)
    _need(not _xd_fault_blocks_weight(dev), "NO_WEIGHT")
    _need(dev["pending"] is None, "BUSY")
    _need(not (dev["settings"]["quality.leveling_warning"] == "Forced leveling" and not _xd_level(inst)), "NOT_LEVEL")
    m = _xd_method(dev, dev["active_method"])
    _need(m["type"] != _DEN, "PRECONDITION_FAILED")
    _need(_xd_reading_ok(inst), "NO_WEIGHT")
    if m["type"] == _GWI:
        _need(dev["task"]["item_index"] < len(m["items"]))
    _xd_auto_close(inst)
    if m["settings"]["weighing.capture_mode"] == "Stable" and _xd_ready(inst, t):
        res = _xd_capture(inst, now, True)
        _xd_auto_reopen(inst)
        _xd_after_result(inst, now)
        return dict(pending=False, index=res["index"])
    dev["pending"] = dict(action="capture", since=t)
    return dict(pending=True, index=None)


def _xd_after_result(inst, now):
    dev = inst["device"]
    m = _xd_method(dev, dev["active_method"])
    if m["type"] == _GWI and m["settings"].get("auto.task_completion") and dev["task"]["item_index"] >= len(m["items"]):
        _xd_complete(inst, now)


def _xd_complete(inst, now):
    dev = inst["device"]
    task = dev["task"]
    dev.setdefault("completed", []).append(dict(method_id=task["method_id"], completed=now, task_ids=task["task_ids"],
                                                results=_xt_copy.deepcopy(task["results"])))
    del dev["completed"][:-20]
    mid = task["method_id"]
    dev["task"] = None
    _xd_start_task(inst, mid, now)


def _c_complete(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, busy=True)
    _need(dev["task"] is not None and dev["task"]["results"])
    n = len(dev["task"]["results"])
    _xd_complete(inst, now)
    return dict(results=n)


def _c_cancel_task(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="cancel_task", busy=True)
    _need(dev["task"] is not None)
    mid = dev["task"]["method_id"]
    dev["task"], dev["pending"] = None, None
    _xd_start_task(inst, mid, now)
    return dict(cancelled=True)


def _c_task_delete(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="cancel_task")
    _need(a["method_id"] in dev["tasks"], "NOT_FOUND")
    del dev["tasks"][a["method_id"]]
    return dict(method_id=a["method_id"])


def _c_set_id(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst)
    task = dev["task"]
    _need(task is not None)
    ids = task["task_ids"] if a["kind"] == "task" else task["result_ids"]
    _need(a["index"] <= len(ids))
    ids[a["index"] - 1] = a["value"]
    return dict(kind=a["kind"], index=a["index"], value=a["value"])


def _c_exclude(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="exclude_overwrite")
    task = dev["task"]
    _need(task is not None and 1 <= a["index"] <= len(task["results"]), "NOT_FOUND")
    r = task["results"][a["index"] - 1]
    r.update(excluded=True, state="Excluded", comment=a.get("comment", ""))
    return dict(index=a["index"])


def _c_pcs_reference(rt, inst, a, now, t):
    """Piece counting reference (p.39): the stable net weight of the reference pieces gives the average piece weight."""
    dev = inst["device"]
    _gate(inst, blocked=True)
    m = _xd_method(dev, dev["active_method"])
    _need(m is not None and m["type"] == _PCS)
    b = _xd_cell(inst)
    r = b.read()
    _need(_xd_ready(inst, t) and r["net_kg"] is not None and r["net_kg"] > 0, "NO_WEIGHT")
    m["settings"]["pcs.reference_pcs"] = a["pieces"]
    m["settings"]["pcs.reference_apw"] = r["net_kg"] * 1000.0 / a["pieces"]
    return dict(apw_g=m["settings"]["pcs.reference_apw"])


def _c_density_start(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, blocked=True, busy=True)
    m = _xd_method(dev, dev["active_method"])
    _need(m is not None and m["type"] == _DEN and dev["task"] is not None)
    _need(10.0 <= a["temperature_c"] <= 30.9, "INVALID_ARGUMENT")
    _xp_start(inst, "density", now, t, inputs=dict(a))
    return dict(started=True)


def _c_weight_save(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True)
    wid = a.get("weight_id")
    old = next((w for w in dev["test_weights"] if w["id"] == wid), None) if wid else None
    _need(wid is None or (old is not None and not old.get("combined")), "NOT_FOUND")
    _need(old is not None or len([w for w in dev["test_weights"] if not w.get("combined")]) < 12)   # up to 12 test weights (p.42)
    base = _xt_defaults(XPR_WEIGHT_FIELDS) if old is None else {k: v for k, v in old.items() if k != "id"}
    new = _xt_settings_values(XPR_WEIGHT_FIELDS, base, a["settings"])
    _need(new["name"] and new["weight_id"] and new["nominal_g"] is not None, "INVALID_ARGUMENT")
    if old is None:
        dev["seq"]["tw"] += 1
        wid = f"tw{dev['seq']['tw']}"
        dev["test_weights"].append(dict(id=wid, **new))
    else:
        old.update(new)
    _xd_change(dev, now, f"Test weights / {new['name']}", base if old else None, new)
    return dict(weight_id=wid)


_CLASS_RANK = {c: i for i, c in enumerate(XPR_WEIGHT_CLASSES)}


def _c_weight_save_combined(rt, inst, a, now, t):
    """Combined test weight (p.42): two or three individual weights with a class at least as good as the minimum class."""
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True)
    singles = {w["id"]: w for w in dev["test_weights"] if not w.get("combined")}
    _need(2 <= len(a["weights"]) <= 3 and len(set(a["weights"])) == len(a["weights"]) and set(a["weights"]) <= set(singles), "INVALID_ARGUMENT")
    _need(all(_CLASS_RANK[singles[w]["weight_class"]] <= _CLASS_RANK[a["min_class"]] for w in a["weights"]), "INVALID_ARGUMENT")
    nominal = sum(singles[w]["nominal_g"] for w in a["weights"])
    wid = a.get("weight_id")
    if wid is None:
        dev["seq"]["tw"] += 1
        wid = f"tw{dev['seq']['tw']}"
        dev["test_weights"].append(dict(id=wid, combined=True, name=a["name"], weight_id=a["name"], min_class=a["min_class"], weights=list(a["weights"]),
                                        nominal_g=nominal, weight_class=a["min_class"], actual_g=None, next_calibration=""))
    else:
        w = next((w for w in dev["test_weights"] if w["id"] == wid and w.get("combined")), None)
        _need(w is not None, "NOT_FOUND")
        w.update(name=a["name"], weight_id=a["name"], min_class=a["min_class"], weights=list(a["weights"]), nominal_g=nominal, weight_class=a["min_class"])
    return dict(weight_id=wid, nominal_g=nominal)


def _c_weight_delete(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True)
    w = next((w for w in dev["test_weights"] if w["id"] == a["weight_id"]), None)
    _need(w is not None, "NOT_FOUND")
    used = any(a["weight_id"] in (x["settings"].get("weight_1"), x["settings"].get("weight_2")) for x in dev["tests"]) or \
        dev["adjustment"].get("weight") == a["weight_id"] or any(a["weight_id"] in x.get("weights", []) for x in dev["test_weights"])
    _need(not used, "IN_USE")
    dev["test_weights"].remove(w)
    return dict(weight_id=a["weight_id"])


def _xt_check_weight_ref(dev, wid, adjustment=False):
    w = next((w for w in dev["test_weights"] if w["id"] == wid), None) if wid else None
    _need(wid is None or w is not None, "INVALID_ARGUMENT")
    if adjustment and w is not None:
        _need(w["nominal_g"] >= 0.1 * XPR204E_CAPACITY_KG * 1000.0, "INVALID_ARGUMENT")   # >= 10 % of the capacity (p.42)


def _c_test_create(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True)
    s = _xt_settings_values(xpr_test_fields(a["type"]), _xt_defaults(xpr_test_fields(a["type"])), a["settings"], create=True)
    _need(s["name"], "INVALID_ARGUMENT")
    for k in ("weight_1", "weight_2"):
        _xt_check_weight_ref(dev, s.get(k))
    dev["seq"]["t"] += 1
    tid = f"t{dev['seq']['t']}"
    dev["tests"].append(dict(id=tid, type=a["type"], settings=s, last=None, due=False))
    _xd_change(dev, now, f"Tests / {s['name']}", None, "created")
    return dict(test_id=tid)


def _xt_test(dev, tid):
    test = next((x for x in dev["tests"] if x["id"] == tid), None)
    _need(test is not None, "NOT_FOUND")
    return test


def _c_test_configure(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True)
    test = _xt_test(dev, a["test_id"])
    _need(not (dev["procedure"] and dev["procedure"].get("test_id") == test["id"] and dev["procedure"]["status"] == "running"), "BUSY")
    new = _xt_settings_values(xpr_test_fields(test["type"]), test["settings"], a["settings"])
    _need(new["name"], "INVALID_ARGUMENT")
    for k in ("weight_1", "weight_2"):
        _xt_check_weight_ref(dev, new.get(k))
    for k in new:
        _xd_change(dev, now, f"Tests / {new['name']} / {k}", test["settings"].get(k), new[k])
    test["settings"] = new
    return dict(test_id=test["id"])


def _c_test_delete(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True)
    test = _xt_test(dev, a["test_id"])
    _need(not (dev["procedure"] and dev["procedure"].get("test_id") == test["id"] and dev["procedure"]["status"] == "running"), "IN_USE")
    dev["tests"].remove(test)
    _xd_clear(dev, f"test_due_{test['id']}")
    _xd_change(dev, now, f"Tests / {test['settings']['name']}", "test", None)
    return dict(test_id=a["test_id"])


def _c_test_start(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="start_tests", blocked=True, busy=True)
    test = _xt_test(dev, a["test_id"])
    _need(test["settings"]["activated"])
    _need(dev["test_weights"], "PRECONDITION_FAILED")                         # at least one test weight is defined (p.44)
    _xp_start(inst, "test", now, t, test_id=test["id"], trigger="Planned" if test.get("due") else "Manual")
    return dict(test_id=test["id"])


def _c_adjustment_configure(rt, inst, a, now, t):
    dev = inst["device"]
    _gate(inst, perm="configure_tests", blocked=True, busy=True)
    new = _xt_settings_values(XPR_ADJUSTMENT_FIELDS, dev["adjustment"], a["settings"])
    _need(new["warning_limit"] < new["control_limit"] + 1e-12, "INVALID_ARGUMENT")
    _xt_check_weight_ref(dev, new.get("weight"), adjustment=True)
    for k in new:
        _xd_change(dev, now, f"Adjustments / {k}", dev["adjustment"].get(k), new[k])
    dev["adjustment"] = new
    return dict(strategy=new["strategy"])


def _c_adjustment_start(rt, inst, a, now, t):
    dev = inst["device"]
    strategy = dev["adjustment"]["strategy"]
    _gate(inst, perm="start_external_adjustment" if strategy == "External adjustment" else None, blocked=True, busy=True)
    _need(strategy != "No adjustment")
    _need(dev.get("fault") != "cannot_start_adjustment", "CANNOT_ADJUST")           # p.134: initial zero not reached at switch-on
    _need(not _xd_fault_blocks_weight(dev), "NO_WEIGHT")
    _need(_xd_reading_ok(inst) or strategy == "External adjustment", "NO_WEIGHT")
    _xp_start(inst, "internal" if strategy == "Internal adjustment" else "external", now, t)
    return dict(kind=strategy)


def _c_proc_confirm(rt, inst, a, now, t):
    """OK in a procedure: prompts go on; a weight choice needs a chosen weight; a density weighing captures the stable weight."""
    dev = inst["device"]
    _gate(inst, login=False)
    p = dev["procedure"]
    _need(p is not None and p["status"] == "running")
    st = _xp_step(dev)
    if st["kind"] == "prompt":
        _xp_next(inst, now, t)
    elif st["kind"] == "choose":
        _need(p["weights"].get(str(st["tp"])) is not None)
        _xp_next(inst, now, t)
    elif st["kind"] == "capture_ok":
        b = _xd_cell(inst)
        r = b.read()
        _need(_xd_ready(inst, t) and r["net_kg"] is not None, "NO_WEIGHT")
        p["readings"].append(dict(label=st["label"], weight_g=round(r["net_kg"] * 1000.0, 7), deviation_g=None, state=None))
        _xp_next(inst, now, t)
    else:
        raise _XTReject("PRECONDITION_FAILED")
    return dict(step=_xp_step(dev)["kind"] if _xp_step(dev) else None)


def _c_proc_weight(rt, inst, a, now, t):
    dev = inst["device"]
    p = dev["procedure"]
    _need(p is not None and p["status"] == "running")
    st = _xp_step(dev)
    _need(st["kind"] == "choose")
    w = next((w for w in dev["test_weights"] if w["id"] == a["weight_id"]), None)
    _need(w is not None, "NOT_FOUND")
    if p["kind"] == "external":
        _need(w["nominal_g"] >= 0.1 * XPR204E_CAPACITY_KG * 1000.0, "INVALID_ARGUMENT")
    p["weights"][str(st["tp"])] = a["weight_id"]
    return dict(weight_id=a["weight_id"])


def _c_proc_cancel(rt, inst, a, now, t):
    dev = inst["device"]
    p = dev["procedure"]
    _need(p is not None)
    if p["status"] == "running" and p["kind"] in ("internal", "external", "test"):
        dev["history"]["adjustments" if p["kind"] != "test" else "tests"].append(dict(time=now, type=p["name"], user=p["user"], trigger=p["trigger"],
                                                                                     status="Cancelled", result="Cancelled"))
    dev["procedure"], dev["internal_loaded"] = None, False
    _xd_auto_reopen(inst)
    return dict(cancelled=True)


def _c_proc_complete(rt, inst, a, now, t):
    dev = inst["device"]
    p = dev["procedure"]
    _need(p is not None and (p["status"] != "running" or (_xp_step(dev) or {}).get("kind") == "results"))
    dev["procedure"] = None
    return dict(completed=True)


def _c_message_ack(rt, inst, a, now, t):
    dev = inst["device"]
    msg = next((m for m in dev["messages"] if m["id"] == a["message_id"]), None)
    _need(msg is not None, "NOT_FOUND")
    if msg["key"] == "level":
        msg["acknowledged"] = True                                            # persistent until the balance is level again
    else:
        dev["messages"].remove(msg)
    return dict(message_id=a["message_id"])


def _c_reset(rt, inst, a, now, t):
    """Reset (p.71): factory state of the user data and settings; the histories only when asked; clock and sensor stay."""
    dev = inst["device"]
    _gate(inst, perm="configure_system", busy=True)
    keep = dict(clock_offset_s=dev["clock_offset_s"], errors=dev["errors"], drift_kg=dev["drift_kg"], power_epoch=dev["power_epoch"] + 1)
    if not a["delete_history"]:
        keep["history"] = dev["history"]
    fresh = _xt_new_device()
    fresh.update(keep)
    fresh["last_activity_s"] = t
    inst["device"] = fresh
    _xd_cell(inst).unit = "g"
    _xd_start_task(inst, fresh["active_method"], now)
    return dict(reset=True)


_XT_COMMANDS = {
    "standby.set": _c_standby, "door.key": _c_door_key, "door.operate": _c_door_operate, "balance.request": _c_request,
    "display.set_unit": _c_set_unit, "settings.configure": _c_settings, "clock.set": _c_clock,
    "user_management.set": _c_user_management, "session.login": _c_login, "session.logout": _c_logout, "user.save": _c_user_save,
    "user.delete": _c_user_delete, "user.set_password": _c_user_password, "group.save": _c_group_save, "group.delete": _c_group_delete,
    "balance.block": _c_block, "unblocking_password.set": _c_unblocking_password,
    "profile.save": _c_profile_save, "profile.delete": _c_profile_delete, "profile.set_default": _c_profile_default,
    "method.create": _c_method_create, "method.configure": _c_method_configure, "method.clone": _c_method_clone, "method.delete": _c_method_delete,
    "method.start": _c_method_start, "method.save_itemized": _c_save_itemized, "method.item_save": _c_item_save, "method.item_delete": _c_item_delete,
    "task.add_result": _c_add_result, "task.complete": _c_complete, "task.cancel": _c_cancel_task, "task.delete": _c_task_delete,
    "task.set_id": _c_set_id, "task.exclude_result": _c_exclude, "pcs.set_reference": _c_pcs_reference, "density.start": _c_density_start,
    "test_weight.save": _c_weight_save, "test_weight.save_combined": _c_weight_save_combined, "test_weight.delete": _c_weight_delete,
    "test.create": _c_test_create, "test.configure": _c_test_configure, "test.delete": _c_test_delete, "test.start": _c_test_start,
    "adjustment.configure": _c_adjustment_configure, "adjustment.start": _c_adjustment_start,
    "procedure.confirm": _c_proc_confirm, "procedure.select_weight": _c_proc_weight, "procedure.cancel": _c_proc_cancel,
    "procedure.complete": _c_proc_complete, "message.acknowledge": _c_message_ack, "maintenance.reset": _c_reset,
    "fault.inject": _c_fault_inject, "fault.clear": _c_fault_clear,
}


# ---------------------------------------------------------------- front-panel engine and spec
def _xt_panel_engine():
    try:
        from .asset_runtime import _panel_ui as engine                      # imported as scripts.basic40_runtime_measure
        return engine
    except ImportError:
        pass
    try:
        from asset_runtime import _panel_ui as engine                       # scripts/ on sys.path (tests)
        return engine
    except ImportError:
        import importlib.util
        import sys
        if "_chemsim_panel_ui" not in sys.modules:
            spec = importlib.util.spec_from_file_location("_chemsim_panel_ui", _xt_Path(__file__).with_name("asset_runtime") / "_panel_ui.py")
            sys.modules["_chemsim_panel_ui"] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules["_chemsim_panel_ui"])
        return sys.modules["_chemsim_panel_ui"]


_xt_pu = _xt_panel_engine()
_XT_LEGACY_CAPS = tuple(export_xpr204e_contract_metadata()["asset_types"][XPR204E_TYPE]["capabilities"])
XPR204E_PANEL_PATH = XPR204E_ASSET_DIR / "panel_ui.json"
XPR204E_PANEL_SPEC = (_xt_pu.load_spec(XPR204E_PANEL_PATH, capabilities=set(_XT_COMMANDS) | set(_XT_LEGACY_CAPS))
                      if XPR204E_PANEL_PATH.is_file() and not _xt_os.environ.get("XPR204E_PANEL_BUILD") else None)   # the spec builder imports this
XPR204E_PANEL_CAPS = _xt_pu.capability_definitions(XPR204E_PANEL_SPEC) if XPR204E_PANEL_SPEC else {}


# ---------------------------------------------------------------- contract metadata of the terminal
def _xs_num(unit, lo=None, hi=None, integer=False):
    s = {"type": "integer" if integer else "number", "x-unit": unit}
    if lo is not None:
        s["minimum"] = lo
    if hi is not None:
        s["maximum"] = hi
    return s


def _xs_obj(props, optional=(), extra=False):
    return {"type": "object", "properties": props, "required": [k for k in props if k not in optional], "additionalProperties": extra}


def _xs_null(s):
    return {"anyOf": [s, {"type": "null"}]}


_XS_STR = {"type": "string"}
_XS_BOOL = {"type": "boolean"}
_XS_ID = {"type": "string", "minLength": 1, "maxLength": 64}
_XS_ANYOBJ = {"type": "object"}
_XS_LIST = {"type": "array", "items": {"type": "object"}}


def _xs_settings(fields):
    keys = {}
    for f in fields:
        if f["key"] not in keys:
            keys[f["key"]] = _xt_field_schema(f)
    return {"type": "object", "properties": keys, "additionalProperties": False, "minProperties": 1}


def _xt_slots():
    def slot(unit, origin, schema, visibility="agent", route=None):
        return {"unit": unit, "origin": origin, "visibility": visibility, "access": "read_write" if route else "read_only", "value_schema": schema,
                "write_via": route, "read_binding": "XPR204ESession.read(instance_id=..., state_ids=[state_id])",
                "sampling": {"mode": "instant", "time_domain": "simulation", "window": None}, "reference_conditions": None}
    weight = _xs_obj(dict(text=_XS_STR, unit=_XS_STR, info_text=_XS_STR, net=_XS_BOOL, stable=_XS_BOOL, calculated=_XS_BOOL,
                          state={"type": "string", "enum": ["value", "overload", "underload", "none", "off"]}, busy=_XS_STR,
                          indicator=_xs_null(_xs_obj(dict(color=_XS_STR, text=_XS_STR))))) 
    trac = _xs_obj(dict(active=_XS_BOOL, fraction=_xs_null(_xs_num("1", 0, 1)), low=_xs_null(_xs_num("1", 0, 1)), high=_xs_null(_xs_num("1", 0, 1)),
                        target_text=_XS_STR, minus_text=_XS_STR, plus_text=_XS_STR, color={"type": "string", "enum": ["neutral", "ok", "warn", "error"]}))
    return {
        "device.standby": slot("1", "setpoint", _XS_BOOL, route={"capability_id": "standby.set", "parameter": "active", "fixed_arguments": {}}),
        "device.blocked": slot("1", "derived", _XS_BOOL),
        "device.datetime": slot("1", "derived", {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"}),
        "device.language": slot("1", "derived", {"type": "string", "enum": list(XPR_LANGUAGES)}),
        "device.settings": slot("1", "setpoint", _xs_settings(XPR_SETTINGS) | {"minProperties": 0}),
        "device.messages": slot("1", "derived", {"type": "array", "items": _xs_obj(dict(id=_XS_STR, key=_XS_STR, level={"type": "string", "enum": ["info", "warning", "error"]},
                                                                                         text=_XS_STR, action=_xs_null(_XS_STR), time=_xs_null(_XS_STR),
                                                                                         acknowledged=_XS_BOOL))}),
        "device.status_light": slot("1", "derived", {"type": "string", "enum": ["off", "green", "yellow", "red"]}),
        "display.weight": slot("1", "derived", weight),
        "display.smarttrac": slot("1", "derived", trac),
        "display.unit": slot("1", "setpoint", {"type": "string"}),
        "display.allowed_units": slot("1", "derived", {"type": "array", "items": _XS_STR}),
        "level.state": slot("1", "derived", _xs_obj(dict(ok=_XS_BOOL, tilt_deg=_xs_num("deg", 0), x=_xs_num("1", -1, 1), y=_xs_num("1", -1, 1),
                                                          ok_radius=_xs_num("1", 0, 1), instruction=_XS_STR))),
        "users.state": slot("1", "derived", _xs_obj(dict(management=_XS_BOOL, current_user=_XS_STR, logged_in=_XS_BOOL,
                                                          rights=_xs_obj({p: _XS_BOOL for p in XPR_PERMISSIONS}), users=_XS_LIST, groups=_XS_LIST,
                                                          unblocking_password_set=_XS_BOOL, block_reason=_xs_null(_XS_STR),
                                                          block_id=_xs_num("1", 0, integer=True)))),
        "methods.list": slot("1", "setpoint", _XS_LIST),
        "methods.active": slot("1", "setpoint", _xs_null(_XS_STR)),
        "task.current": slot("1", "derived", _xs_null(_XS_ANYOBJ)),
        "task.paused": slot("1", "derived", _XS_LIST),
        "task.statistics": slot("1", "derived", _xs_null(_XS_ANYOBJ)),
        "profiles.list": slot("1", "setpoint", _XS_LIST),
        "profiles.default": slot("1", "setpoint", _XS_STR),
        "test_weights.list": slot("1", "setpoint", _XS_LIST),
        "tests.list": slot("1", "setpoint", _XS_LIST),
        "adjustment.settings": slot("1", "setpoint", _XS_ANYOBJ),
        "adjustment.last": slot("1", "derived", _xs_null(_XS_ANYOBJ)),
        "procedure.state": slot("1", "derived", _xs_null(_XS_ANYOBJ)),
        "weighing.pending": slot("1", "derived", _xs_null({"type": "string", "enum": ["zero", "tare", "capture"]})),
        "history.adjustments": slot("1", "derived", _XS_LIST),
        "history.tests": slot("1", "derived", _XS_LIST),
        "history.changes": slot("1", "derived", _XS_LIST),
        "sensor.errors": slot("1", "process_truth", _xs_obj(dict(span_error_rel=_xs_num("1"), zero_offset_kg=_xs_num("kg"), zero_drift_kg_s=_xs_num("kg/s"),
                                                                  internal_weight_error_kg=_xs_num("kg"), span_tc_per_k=_xs_num("1/K"))), "debug"),
        "interface.output": slot("1", "derived", {"type": "array", "items": _XS_STR}, "evaluator"),
        "task.completed": slot("1", "derived", _XS_LIST, "evaluator"),
    }


_XE_DISPLAY = ["display.weight", "display.smarttrac", "device.status_light", "display.allowed_units"]
_XE_BALANCE = ["balance.stable", "balance.status", "balance.stability_elapsed_s", "balance.task_ready", "balance.gross_kg", "balance.net_kg",
               "balance.tare_kg", "balance.zero_kg", "balance.filtered_force_n", "balance.filter_level", "balance.calibrated", "balance.gain_n_kg"]
_XE_DOORS = ["door.left.opening_m", "door.right.opening_m"]
_XE_TASK = ["task.current", "task.paused", "task.statistics", "weighing.pending", "interface.output", "task.completed"]
_XE_PROC = ["procedure.state", "history.adjustments", "history.tests", "adjustment.last", "tests.list", "device.blocked", "device.messages"]
_XT_CAPDEFS = {
    # name: (input properties, optional inputs, output properties, example, description, effects, kind)
    "standby.set": ({"active": _XS_BOOL}, (), {"active": _XS_BOOL}, {"active": True},
                    "进入/退出待机（屏幕变暗，称量单元保持通电；用户管理激活时待机会注销用户）；对应终端待机键：长按进入、按一下退出。",
                    ["device.standby", "users.state", "device.language"] + _XE_DISPLAY, "device_command"),
    "door.key": ({"key": {"type": "string", "enum": ["left", "right"]}}, (), {"moved": {"type": "array", "items": _XS_STR}, "left_m": _xs_num("m"),
                                                                              "right_m": _xs_num("m")}, {"key": "right"},
                 "终端开/关门键：按Doors设置中Door key left/right为Active的电动侧门开到Door opening或关闭（手动驱动的门不动）。",
                 _XE_DOORS + _XE_BALANCE + _XE_DISPLAY, "device_command"),
    "door.operate": ({"side": {"type": "string", "enum": ["left", "right", "both"]}, "action": {"type": "string", "enum": ["open", "close", "toggle"]}}, (),
                     {"moved": {"type": "array", "items": _XS_STR}, "left_m": _xs_num("m"), "right_m": _xs_num("m")}, {"side": "left", "action": "open"},
                     "电动侧门驱动（设备自身的门电机，非手动推门）：开到Door opening百分比或关闭。", _XE_DOORS + _XE_BALANCE + _XE_DISPLAY, "device_command"),
    "balance.request": ({"action": {"type": "string", "enum": ["zero", "tare"]}}, (), {"executed": _XS_BOOL, "pending": _XS_BOOL}, {"action": "tare"},
                        "终端Zero/Tare键：稳定时立即执行，否则显示Zeroing.../Taring...等到稳定（最长40 s）；门设为Automatic时先关门。",
                        _XE_BALANCE + _XE_DISPLAY + _XE_DOORS + ["weighing.pending", "device.messages"], "device_command"),
    "display.set_unit": ({"unit": {"type": "string", "enum": list(XPR_UNITS) + ["custom", "PCS"]}}, (), {"unit": _XS_STR}, {"unit": "mg"},
                         "More > Change display unit：当前任务的显示单位（限于容差配置允许的单位）。", ["display.unit"] + _XE_DISPLAY, "device_command"),
    "settings.configure": ({"settings": _xs_settings(XPR_SETTINGS)}, (), {"changed": {"type": "array", "items": _XS_STR}},
                           {"settings": {"screen.brightness": 60}}, "Balance menu > Settings的天平设置（称量/质量、自动输出、门、语言格式、屏幕声音灯、通用、接口、服务、用户通用）。",
                           ["device.settings", "device.language", "device.messages", "device.status_light", "history.changes", "users.state"],
                           "configuration"),
    "clock.set": ({"date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}, "time": {"type": "string", "pattern": r"^([01]\d|2[0-3]):[0-5]\d$"}},
                  ("date", "time"), {"datetime": _XS_STR}, {"date": "2026-10-18", "time": "09:30"}, "设置设备日期与时间（+/-选择器）。",
                  ["device.datetime", "history.changes"], "configuration"),
    "user_management.set": ({"active": _XS_BOOL, "password": {"type": "string", "maxLength": 64}}, ("password",), {"active": _XS_BOOL},
                            {"active": True, "password": "1234"}, "激活/停用用户管理（激活时可设管理员密码，停用时可设解锁密码）。",
                            ["users.state", "device.language", "history.changes"], "configuration"),
    "session.login": ({"user_name": _XS_ID, "password": {"type": "string", "maxLength": 64}}, (), {"user_name": _XS_STR},
                      {"user_name": "Administrator", "password": "1234"}, "登录（用户管理激活时）。", ["users.state", "device.language"], "device_command"),
    "session.logout": ({}, (), {"logged_in": _XS_BOOL}, {}, "注销（Exit / Blocking > Logout）。", ["users.state", "device.language"], "device_command"),
    "user.save": ({"user_name": {"type": "string", "minLength": 1, "maxLength": 22}, "settings": _xs_settings(XPR_USER_FIELDS) | {"minProperties": 0}}, (),
                  {"user_name": _XS_STR, "created": _XS_BOOL}, {"user_name": "Operator1", "settings": {"groups": ["Administrators"]}},
                  "新建或修改用户（姓、名、激活、组、语言；用户名创建后不可改）。", ["users.state", "device.language", "history.changes"], "configuration"),
    "user.delete": ({"user_name": _XS_ID}, (), {"user_name": _XS_STR}, {"user_name": "Operator1"}, "删除用户（不能删除当前登录者）。",
                    ["users.state", "history.changes"], "configuration"),
    "user.set_password": ({"user_name": _XS_ID, "password": {"type": "string", "maxLength": 64}}, (), {"user_name": _XS_STR},
                          {"user_name": "Administrator", "password": "1234"}, "设置用户密码（本人或有用户管理权限者）。", ["users.state", "history.changes"], "configuration"),
    "group.save": ({"group_name": {"type": "string", "minLength": 1, "maxLength": 22}, "permissions": _xs_settings(XPR_GROUP_FIELDS[1:]) | {"minProperties": 0}},
                   (), {"group_name": _XS_STR, "created": _XS_BOOL}, {"group_name": "Operators", "permissions": {"start_tests": True}},
                   "新建或修改用户组及其权限。", ["users.state", "history.changes"], "configuration"),
    "group.delete": ({"group_name": _XS_ID}, (), {"group_name": _XS_STR}, {"group_name": "Operators"}, "删除用户组（仍有用户时拒绝）。",
                     ["users.state", "history.changes"], "configuration"),
    "balance.block": ({"block": _XS_BOOL, "password": {"type": "string", "maxLength": 64}}, ("password",), {"blocked": _XS_BOOL},
                      {"block": True}, "锁定/解锁天平（无用户管理时如设了解锁密码须输入）。", ["device.blocked", "device.status_light", "history.changes", "users.state"],
                      "device_command"),
    "unblocking_password.set": ({"password": {"type": "string", "maxLength": 64}}, (), {"set": _XS_BOOL}, {"password": "1234"},
                                "设置解锁密码（用户管理未激活时）。", ["users.state"], "configuration"),
    "profile.save": ({"profile_id": _xs_null(_XS_ID), "settings": _xs_settings(XPR_PROFILE_FIELDS) | {"minProperties": 0}}, (), {"profile_id": _XS_STR},
                     {"profile_id": None, "settings": {"name": "Fast", "environment": "Stable"}},
                     "新建或修改容差配置（环境→滤波、数值释放、显示可读性、零点漂移补偿、允许单位）；当前方法的配置立即作用于称量。",
                     ["profiles.list", "tests.list", "history.changes"] + _XE_BALANCE + _XE_DISPLAY, "configuration"),
    "profile.delete": ({"profile_id": _XS_ID}, (), {"profile_id": _XS_STR}, {"profile_id": "tp2"}, "删除容差配置（默认或被方法使用时拒绝）。",
                       ["profiles.list", "history.changes"], "configuration"),
    "profile.set_default": ({"profile_id": _XS_ID}, (), {"profile_id": _XS_STR}, {"profile_id": "tp1"}, "设为默认容差配置。",
                            ["profiles.default", "history.changes"] + _XE_BALANCE + _XE_DISPLAY, "configuration"),
    "method.create": ({"type": {"type": "string", "enum": list(XPR_METHOD_TYPES)}, "settings": _xs_settings(XPR_METHOD_FIELDS),
                       "items": {"type": "array", "minItems": 1, "items": _XS_ANYOBJ}}, ("items",), {"method_id": _XS_STR},
                      {"type": "General weighing", "settings": {"name": "my general weighing"}}, "按向导新建方法（最多50个）。",
                      ["methods.list", "history.changes"], "configuration"),
    "method.configure": ({"method_id": _XS_ID, "settings": _xs_settings(XPR_METHOD_FIELDS) | {"minProperties": 0},
                          "items": {"type": "array", "minItems": 1, "items": _XS_ANYOBJ}}, ("items",), {"method_id": _XS_STR},
                         {"method_id": "m1", "settings": {"weighing.statistics": True}}, "修改方法设置（锁定的方法只能解锁）。",
                         ["methods.list", "history.changes", "display.unit"] + _XE_TASK + _XE_BALANCE + _XE_DISPLAY, "configuration"),
    "method.clone": ({"method_id": _XS_ID}, (), {"method_id": _XS_STR}, {"method_id": "m1"}, "克隆方法（名称后加序号）。", ["methods.list", "history.changes"],
                     "configuration"),
    "method.delete": ({"method_id": _XS_ID}, (), {"method_id": _XS_STR}, {"method_id": "m2"}, "删除方法及其暂停的任务（后台活动方法不能删除）。",
                      ["methods.list", "task.paused", "history.changes"], "configuration"),
    "method.start": ({"method_id": _XS_ID}, (), {"method_id": _XS_STR}, {"method_id": "m1"}, "启动方法：当前任务有结果时暂停到Tasks，本方法暂停的任务继续。",
                     ["methods.active", "methods.list", "display.unit"] + _XE_TASK + _XE_BALANCE + _XE_DISPLAY, "device_command"),
    "method.item_save": ({"method_id": _XS_ID, "index": _xs_null(_xs_num("1", 1, integer=True)),
                          "item": _xs_obj(dict(sample_id={"type": "string", "maxLength": 32}, unit={"type": "string", "enum": list(XPR_UNITS)},
                                               target=_xs_null(_xs_num("1", 0)), tol_minus=_xs_null(_xs_num("%", 0, 100)), tol_plus=_xs_null(_xs_num("%", 0, 100))),
                                          optional=("sample_id", "unit", "target", "tol_minus", "tol_plus"))}, (),
                         {"index": _xs_num("1", integer=True)}, {"method_id": "m2", "index": None, "item": {"sample_id": "B", "unit": "g", "target": 5.0}},
                         "分项方法的称量项：新建或修改（样品ID、单位、目标、容差）。", ["methods.list", "history.changes", "task.current"], "configuration"),
    "method.item_delete": ({"method_id": _XS_ID, "index": _xs_num("1", 1, integer=True)}, (), {"index": _xs_num("1", integer=True)},
                           {"method_id": "m2", "index": 2}, "删除分项方法的称量项（至少保留一项）。", ["methods.list", "history.changes", "task.current"],
                           "configuration"),
    "method.save_itemized": ({"name": {"type": "string", "minLength": 1, "maxLength": 22}}, (), {"method_id": _XS_STR}, {"name": "my items"},
                             "More > Save as method (itemized)：用当前任务结果建立分项方法。", ["methods.list", "history.changes"], "configuration"),
    "task.add_result": ({}, (), {"pending": _XS_BOOL, "index": _xs_null(_xs_num("1", 1, integer=True))}, {},
                        "Add result：按称量方式（稳定/立即+延时）采集结果，带ID、容差判定、水平状态；强制调平且不水平时拒绝。",
                        _XE_TASK + _XE_DISPLAY + _XE_DOORS + _XE_BALANCE + ["methods.list"], "device_command"),
    "task.complete": ({}, (), {"results": _xs_num("1", 1, integer=True)}, {}, "Complete：完成任务并清空结果（打印/导出需不存在的打印机或U盘）。",
                      _XE_TASK + _XE_DISPLAY + _XE_BALANCE, "device_command"),
    "task.cancel": ({}, (), {"cancelled": _XS_BOOL}, {}, "Cancel task：取消当前任务（结果丢弃）。", _XE_TASK + _XE_DISPLAY + _XE_BALANCE, "device_command"),
    "task.delete": ({"method_id": _XS_ID}, (), {"method_id": _XS_STR}, {"method_id": "m2"}, "Tasks中取消（删除）暂停的任务。", ["task.paused", "methods.list"], "device_command"),
    "task.set_id": ({"kind": {"type": "string", "enum": ["task", "result"]}, "index": _xs_num("1", 1, 3, integer=True),
                     "value": {"type": "string", "maxLength": 32}}, (), {"kind": _XS_STR, "index": _xs_num("1", integer=True), "value": _XS_STR},
                    {"kind": "result", "index": 1, "value": "S-001"}, "输入任务ID/结果ID（如Sample ID）。", ["task.current"], "device_command"),
    "task.exclude_result": ({"index": _xs_num("1", 1, integer=True), "comment": {"type": "string", "maxLength": 128}}, ("comment",),
                            {"index": _xs_num("1", integer=True)}, {"index": 1, "comment": "spilled"}, "Exclude result：排除结果（可加注释）。",
                            ["task.current", "task.statistics"], "device_command"),
    "pcs.set_reference": ({"pieces": _xs_num("1", 1, 10000, integer=True)}, (), {"apw_g": _xs_num("g")}, {"pieces": 10},
                          "计件：用秤盘上参考件数的稳定净重求平均单件重。", ["methods.list"] + _XE_DISPLAY, "device_command"),
    "density.start": ({"temperature_c": _xs_num("degC", 10, 30.9), "aux_liquid": {"type": "string", "enum": ["Distilled water", "Custom"]},
                       "aux_density_g_cm3": _xs_null(_xs_num("g/cm3", 0.00001, 100))}, ("aux_liquid", "aux_density_g_cm3"), {"started": _XS_BOOL},
                      {"temperature_c": 21.0}, "密度测定：输入温度与辅助液后按向导称量（空气中/液体中或比重瓶）。", ["procedure.state"], "device_command"),
    "test_weight.save": ({"weight_id": _xs_null(_XS_ID), "settings": _xs_settings(XPR_WEIGHT_FIELDS) | {"minProperties": 0}}, (), {"weight_id": _XS_STR},
                         {"weight_id": None, "settings": {"name": "200 g F2", "weight_id": "W200", "nominal_g": 200.0, "weight_class": "F2"}},
                         "定义单个测试砝码（最多12个）。", ["test_weights.list", "history.changes", "device.messages"], "configuration"),
    "test_weight.save_combined": ({"weight_id": _xs_null(_XS_ID), "name": {"type": "string", "minLength": 1, "maxLength": 22},
                                   "min_class": {"type": "string", "enum": list(XPR_WEIGHT_CLASSES)},
                                   "weights": {"type": "array", "minItems": 2, "maxItems": 3, "items": _XS_ID}}, (),
                                  {"weight_id": _XS_STR, "nominal_g": _xs_num("g")},
                                  {"weight_id": None, "name": "30 g", "min_class": "F2", "weights": ["tw1", "tw2"]}, "定义组合测试砝码（2-3个）。",
                                  ["test_weights.list"], "configuration"),
    "test_weight.delete": ({"weight_id": _XS_ID}, (), {"weight_id": _XS_STR}, {"weight_id": "tw1"}, "删除测试砝码（被使用时拒绝）。",
                           ["test_weights.list", "device.messages"], "configuration"),
    "test.create": ({"type": {"type": "string", "enum": list(XPR_TEST_TYPES)}, "settings": _xs_settings(XPR_TEST_FIELDS)}, (), {"test_id": _XS_STR},
                    {"type": "Sensitivity - 1 TP", "settings": {"name": "my sensitivity test"}}, "按向导新建例行测试（偏载、重复性、灵敏度）。",
                    ["tests.list", "history.changes"], "configuration"),
    "test.configure": ({"test_id": _XS_ID, "settings": _xs_settings(XPR_TEST_FIELDS) | {"minProperties": 0}}, (), {"test_id": _XS_STR},
                       {"test_id": "t1", "settings": {"automatic_print": False}}, "修改测试设置（运行中拒绝）。", ["tests.list", "history.changes"], "configuration"),
    "test.delete": ({"test_id": _XS_ID}, (), {"test_id": _XS_STR}, {"test_id": "t1"}, "删除测试（运行中拒绝）。", ["tests.list", "history.changes",
                                                                                                                "device.messages"], "configuration"),
    "test.start": ({"test_id": _XS_ID}, (), {"test_id": _XS_STR}, {"test_id": "t1"}, "启动例行测试流程（提示、自动置零/去皮、按位置采集、结果与限值）。",
                   _XE_PROC + _XE_DOORS + _XE_BALANCE + _XE_DISPLAY, "device_command"),
    "adjustment.configure": ({"settings": _xs_settings(XPR_ADJUSTMENT_FIELDS)}, (), {"strategy": _XS_STR},
                             {"settings": {"strategy": "Internal adjustment", "as_left": True}}, "调整设置（策略、As found/As left、限值、错误管理、计划）。",
                             ["adjustment.settings", "history.changes"], "configuration"),
    "adjustment.start": ({}, (), {"kind": _XS_STR}, {}, "启动内部或外部调整（More > Start adjustment或Methods > Adjustments > Start）。",
                         _XE_PROC + _XE_DOORS + _XE_BALANCE + _XE_DISPLAY, "device_command"),
    "procedure.confirm": ({}, (), {"step": _xs_null(_XS_STR)}, {}, "流程中的OK（提示继续、确认所选砝码、密度称量采集）。",
                          _XE_PROC + _XE_TASK + _XE_DOORS + _XE_BALANCE + _XE_DISPLAY, "device_command"),
    "procedure.select_weight": ({"weight_id": _XS_ID}, (), {"weight_id": _XS_STR}, {"weight_id": "tw1"}, "流程中选择测试砝码。", ["procedure.state"],
                                "device_command"),
    "procedure.cancel": ({}, (), {"cancelled": _XS_BOOL}, {}, "取消正在进行的测试/调整/密度流程（历史记为Cancelled）。",
                         _XE_PROC + _XE_DOORS + _XE_BALANCE + _XE_DISPLAY, "device_command"),
    "procedure.complete": ({}, (), {"completed": _XS_BOOL}, {}, "结果页Complete：关闭已完成的流程。", ["procedure.state"], "device_command"),
    "message.acknowledge": ({"message_id": _XS_ID}, (), {"message_id": _XS_STR}, {"message_id": "msg1"}, "确认（或推迟）状态栏消息。",
                            ["device.messages", "device.status_light"], "device_command"),
    "maintenance.reset": ({"delete_history": _XS_BOOL}, (), {"reset": _XS_BOOL}, {"delete_history": False},
                          "Maintenance > Reset：用户数据与设置回到出厂状态（可选同时删除变更、测试与调整历史）。",
                          sorted(set(_xt_slots()) - {"sensor.errors", "device.datetime", "level.state"}) + _XE_BALANCE + ["balance.display_unit"],
                          "configuration"),
    "fault.inject": ({"code": {"type": "string", "enum": list(XPR_FAULTS)}}, (), {"code": _XS_STR}, {"code": "weight_not_determined"},
                     "评测者：注入手册8.1（p.134）的错误消息：Balance reset failed、Weight cannot be determined、Pairing error.（不能确定重量：显示--，置零/去皮/结果/读数被拒）、"
                     "Cannot start adjustment（调整被拒）、The system has no valid date and time set、Preventive performance optimization（不能启动方法）；"
                     "消息显示在状态栏（红色StatusLight）。断开电源（power.set关）清除手册以“断开电源再接上”为处理的错误，Pairing error与日期时间错误只能fault.clear。",
                     ["device.messages", "device.status_light", "display.weight", "weighing.pending"], "simulation"),
    "fault.clear": ({}, (), {"cleared": _XS_BOOL}, {}, "评测者：错误原因已排除（换电缆、充电或重新接电），错误消息清除。",
                    ["device.messages", "device.status_light", "display.weight"], "simulation"),
}


def _xt_caps():
    """Contract entries of the terminal commands and the panel capabilities (same structure as the CHEM-001 entries)."""
    caps = {}
    slots = _xt_slots()
    for name, (inputs, optional, outputs, example, desc, effects, kind) in _XT_CAPDEFS.items():
        effects = list(dict.fromkeys(effects))
        caps[name] = {"availability": "implemented", "visibility": "agent", "kind": kind, "description_zh": desc,
                      "input_schema": _xs_obj(inputs, optional), "output_schema": _xs_obj(outputs),
                      "example_arguments": [example], "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": desc} for sid in effects],
                      "observes": [sid for sid in effects if sid in slots and slots[sid]["visibility"] == "agent"][:6], "scene_backend_required": False,
                      "binding": f"basic40_runtime_measure.XPR204ESession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
                      "completion_criterion_zh": "同步提交；需要稳定或计时的步骤（等待稳定、采集、流程步骤）由宿主step推进，状态见weighing.pending / procedure.state。",
                      "preconditions_zh": "供电且未待机；用户管理激活时须登录并有相应组权限；天平锁定或有流程运行时相关命令拒绝；参数与设置表一致。"}
        if name in ("fault.inject", "fault.clear"):
            caps[name]["visibility"] = "evaluator"
    every = sorted({e["state_id"] for c in caps.values() for e in c["state_effects"]} | set(_XE_BALANCE) | set(_XE_DOORS))
    for name, d in XPR204E_PANEL_CAPS.items():
        gesture = name not in ("panel.capture", "panel.frame")
        caps[name] = {"availability": "implemented", "visibility": d["visibility"], "kind": d["kind"], "description_zh": d["function_zh"],
                      "input_schema": d["input_schema"], "output_schema": d["output_schema"], "example_arguments": [d["example_arguments"]],
                      "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": "手势调用的命令改变的量"} for sid in (every if gesture else [])],
                      "observes": [], "scene_backend_required": False, "component_ids": d["component_ids"],
                      "binding": f"basic40_runtime_measure.XPR204ESession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
                      "completion_criterion_zh": "手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕用panel.capture查看。" if gesture else "立即返回。",
                      "preconditions_zh": "面板按panel_ui.json（手册页码证据）解释手势并调用本运行层的同名命令；被拒时显示仪器自己的消息。"}
    return caps


_XT_LIMITATIONS = [
    "XPR terminal layer: settings, tolerance profiles, methods / tasks / results, test weights, routine tests, internal and external adjustment, "
    "users / groups / login / blocking, date / time / language, motorized doors, standby, leveling aid, messages and StatusLight follow the XPR "
    "Essential operating manual as a minimal device model; it is not METTLER TOLEDO firmware.",
    "Sensor errors (span, its temperature coefficient, zero offset, zero drift, internal weight) are trusted-host parameters, 0 (ideal) by "
    "default; with them the adjustments correct exactly what they model (at the cell temperature they run at). The cell temperature is a host "
    "input (set_environment temperature_c, 20 degC by default); 'Start after temperature change' starts the internal adjustment after a change "
    "of 1 degC since the last adjustment (p.129). Realistic tolerances and eccentric-load errors are a later sim-to-real task.",
    "Printing, label printing, export / import, software update, barcode reader, ErgoSens / foot switch, Ethernet / Bluetooth / LabX / MT-SICS / "
    "web services and file server need hardware or a network absent from the simulation: their settings are kept, the actions show the "
    "device's not-connected response. Automatic weight value output is formatted into interface.output (no computer is connected).",
    "Not modelled: warm-up / acclimatization, DST of time zones (the zone shifts the shown local time by its standard offset), alibi memory, "
    "GWP Approved mode (service), approved-balance rules, the off-centre position of a load (eccentricity test readings are identical).  "
    "The balance recalibration and service reminders (with 'Block' when expired) need a service calibration due date, which the simulated "
    "balance does not have: they never fall due.",
]


_xt_base_export = export_xpr204e_contract_metadata


def export_xpr204e_contract_metadata():
    """CHEM-001 metadata plus the terminal: state slots, terminal_capabilities (device commands and panel.*), parameters, limitations.  The
    CHEM-001 command set ('capabilities') keeps its schemas; the display slots it changes are added to its effects."""
    meta = _xt_base_export()
    t = meta["asset_types"][XPR204E_TYPE]
    t["asset_version"] = XPR204E_ASSET_VERSION
    t["state_slots"].update(_xt_slots())
    extra = {"power.set": ["device.standby", "users.state", "device.language", "weighing.pending", "procedure.state", "display.unit"],
             "balance.set_filter": ["profiles.list"], "balance.set_display_unit": ["display.unit"]}
    for name, cap in t["capabilities"].items():
        have = {e["state_id"] for e in cap["state_effects"]}
        if name == "balance.reading":
            continue
        for sid in _XE_DISPLAY + extra.get(name, []):
            if sid not in have:
                cap["state_effects"].append({"state_id": sid, "timing": "immediate", "description_zh": "终端显示随之更新"})
    t["terminal_capabilities"] = _xt_caps()
    t["parameters"].update({
        "environment_filters": {"value": {k: {"time_constant_s": v[0], "stability_divisions": v[1]} for k, v in XPR_ENVIRONMENTS.items()}, "unit": "1",
                                "evidence": "engineering_assumption", "source": "names p.80; Very stable / Standard / Very unstable = Low / Medium / High"},
        "value_release_hold_factor": {"value": dict(XPR_VALUE_RELEASE), "unit": "1", "evidence": "engineering_assumption", "source": "names p.80"},
        "units_g": {"value": dict(XPR_UNITS), "unit": "g", "evidence": "documented", "source": "MT-SICS M21 unit list (related document)"},
        "stability_timeout_s": {"value": _XT_TIMEOUT_S, "unit": "s", "evidence": "documented", "source": "MT-SICS M67 factory timeout (related)"},
        "internal_weight_kg": {"value": _XT_INTERNAL_KG, "unit": "kg", "evidence": "engineering_assumption", "source": "nominal load p.142"},
        "internal_adjustment_s": {"value": _XT_INTERNAL_S, "unit": "s", "evidence": "engineering_assumption", "source": "not given"},
        "level_limit_rad": {"value": _XT_LEVEL_RAD, "unit": "rad", "evidence": "engineering_assumption", "source": "the runtime's stability gate"},
        "device_clock_epoch": {"value": _XT_EPOCH.isoformat(), "unit": "1", "evidence": "engineering_assumption", "source": "set with clock.set"},
    })
    meta["host_capabilities"]["host.set_environment"]["binding"] = ("XPR204ERuntime.set_environment(instance_id, tilt_rad=..., vibration=..., "
                                                                     "tilt_direction_rad=..., temperature_c=...)")
    meta["host_capabilities"]["host.set_sensor_errors"] = {"kind": "configuration", "visibility": "trusted_host_only",
                                                           "binding": "XPR204ERuntime.set_sensor_errors(instance_id, span_error_rel=..., zero_offset_kg=..., "
                                                                      "zero_drift_kg_s=..., internal_weight_error_kg=..., span_tc_per_k=...)"}
    meta["limitations"] = [x for x in meta["limitations"] if not x.startswith("AZT is not implemented")] + [
        "Zero drift compensation (AZT) of the XPR tolerance profile is active by default: the zero follows a stable reading within 0.5 d of zero."] + \
        _XT_LIMITATIONS
    return meta


# ---------------------------------------------------------------- validation of terminal arguments (the subset the schemas use)
def _xt_validate(schema, v, path="$"):
    if "anyOf" in schema:
        return [] if any(not _xt_validate(s, v, path) for s in schema["anyOf"]) else [f"{path}: no alternative matches"]
    t = schema.get("type")
    ok = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list), "string": lambda x: isinstance(x, str),
          "boolean": lambda x: type(x) is bool, "integer": lambda x: type(x) is int, "null": lambda x: x is None,
          "number": lambda x: type(x) in (int, float) and _xt_math.isfinite(x)}
    if t is not None and not ok[t](v):
        return [f"{path}: type"]
    if "enum" in schema and (v not in schema["enum"] or (isinstance(v, bool) and not any(isinstance(e, bool) for e in schema["enum"]))):
        return [f"{path}: enum"]
    if t in ("number", "integer") and not schema.get("minimum", -_xt_math.inf) - 1e-12 <= v <= schema.get("maximum", _xt_math.inf) + 1e-12:
        return [f"{path}: range"]
    if t == "string":
        if not schema.get("minLength", 0) <= len(v) <= schema.get("maxLength", 10 ** 9):
            return [f"{path}: length"]
        if schema.get("pattern"):
            import re
            if re.search(schema["pattern"], v) is None:
                return [f"{path}: pattern"]
    err = []
    if t == "object":
        props = schema.get("properties", {})
        err += [f"{path}.{k}: missing" for k in schema.get("required", []) if k not in v]
        if schema.get("additionalProperties") is False:
            err += [f"{path}.{k}: unknown" for k in v if k not in props]
        if len(v) < schema.get("minProperties", 0):
            err.append(f"{path}: too few")
        for k, x in v.items():
            if k in props:
                err += _xt_validate(props[k], x, f"{path}.{k}")
    if t == "array":
        if not schema.get("minItems", 0) <= len(v) <= schema.get("maxItems", 10 ** 9) or (schema.get("uniqueItems") and len(set(map(repr, v))) != len(v)):
            err.append(f"{path}: size")
        for i, x in enumerate(v):
            err += _xt_validate(schema.get("items", {}), x, f"{path}[{i}]")
    return err


# ---------------------------------------------------------------- runtime and session with the terminal
class _XPRPanelHost:
    """Panel-host protocol of one instance: what the terminal firmware sees (agent slots and host values), the command path and the clock."""

    def __init__(self, runtime, instance_id):
        self.rt, self.iid = runtime, instance_id

    def panel_read(self):
        rt = self.rt
        inst = rt._instances[self.iid]
        slots = rt._metadata["asset_types"][XPR204E_TYPE]["state_slots"]
        out = {k: v for k, v in rt._values(inst).items() if slots.get(k, {}).get("visibility") == "agent"}
        dev, b = inst["device"], inst["balance"]
        out.update({"host.mains": b.powered, "host.display_on": b.powered and not dev["standby"], "host.power_epoch": dev["power_epoch"],
                    "host.forced_level_block": dev["settings"]["quality.leveling_warning"] == "Forced leveling" and not _xd_level(inst)})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A command of a panel gesture runs as the session that made the gesture (rt._xt_caller, set by XPR204ESession._panel)."""
        rt = self.rt
        who = getattr(rt, "_xt_caller", None)
        session = rt._panel_session if who is None else XPR204ESession(rt, who)
        r = session._checked(self.iid, rt._instances[self.iid], capability_id, dict(arguments), "panel")
        if r["status"] != "succeeded":
            raise _xt_pu.PanelRejected(r["error"]["code"])
        return r["result"]

    def panel_time(self):
        return self.rt.time_s


_XT_BaseRuntime = XPR204ERuntime
_XT_BaseSession = XPR204ESession


class XPR204ERuntime(_XT_BaseRuntime):
    """CHEM-001 host with the XPR terminal: per instance the device model (inst['device']), the XPR weighing cell and the front panel."""

    def __init__(self, *, scene_adapter=None):
        self._panels = {}
        super().__init__(scene_adapter=scene_adapter)
        self.panel_capture_dir = None
        self._principals["__panel__"] = ("agent", None)
        self._logs["__panel__"] = []
        self._panel_session = XPR204ESession(self, "__panel__")

    def create_instance(self, instance_id, asset_type_id=XPR204E_TYPE):
        with self._lock:
            super().create_instance(instance_id, asset_type_id)
            inst = self._instances[instance_id]
            inst["balance"] = XPRWeighingCell()
            inst.update(device=_xt_new_device(), host_force_N=0.0, tilt_direction_rad=0.0)
            _xd_start_task(inst, "m1", _xt_now_text(inst["device"], self.time_s))
            inst["device"]["last_activity_s"] = self.time_s
            if XPR204E_PANEL_SPEC is not None:
                self._panels[instance_id] = _xt_pu.PanelUI(XPR204E_PANEL_SPEC, _XPRPanelHost(self, instance_id))

    def panel(self, instance_id):
        return self._panels[instance_id]

    def _values(self, inst):
        values = _XT_BaseRuntime._values(inst)
        if "device" not in inst:
            return values
        dev = inst["device"]
        w, trac = _xd_display(inst, self.time_s)
        m = _xd_method(dev, dev["active_method"])
        user = next((u for u in dev["users"] if u["user_name"] == dev["current_user"]), None)
        lang = user["language"] if dev["user_management"] and dev["logged_in"] and user else dev["settings"]["region.language"]
        task = None
        if dev["task"] is not None and m is not None:
            s = m["settings"]
            tk = dev["task"]
            task = dict(method_id=m["id"], method_name=s["name"], method_type=m["type"], started=tk["started"], task_ids=list(tk["task_ids"]),
                        task_descriptions=[s[f"ids.task_{i}_description"] for i in range(1, len(tk["task_ids"]) + 1)],
                        result_ids=list(tk["result_ids"]), result_descriptions=[s[f"ids.result_{i}_description"] for i in range(1, len(tk["result_ids"]) + 1)],
                        results=_xt_copy.deepcopy(tk["results"]), item_index=tk["item_index"],
                        item=_xt_copy.deepcopy(m["items"][tk["item_index"]]) if m["type"] == _GWI and tk["item_index"] < len(m["items"]) else None,
                        pending=(dev["pending"] or {}).get("action"))
        p = dev["procedure"]
        proc = None
        if p is not None:
            st = _xp_step(dev)
            eligible = [x["id"] for x in dev["test_weights"] if p["kind"] != "external" or x["nominal_g"] >= 0.1 * XPR204E_CAPACITY_KG * 1000.0]
            proc = dict(kind=p["kind"], name=p.get("name"), test_id=p.get("test_id"), test_type=p.get("test_type"), status=p["status"],
                        step=st["kind"] if st else None, text=(st or {}).get("text", ""), label=(st or {}).get("label"), tp=(st or {}).get("tp"),
                        index=p["index"], count=len(p["steps"]), weights=dict(p["weights"]), readings=_xt_copy.deepcopy(p["readings"]),
                        result=_xt_copy.deepcopy(p["result"]), message=p.get("message"), choices=eligible, trigger=p["trigger"],
                        selected_weight=p["weights"].get(str((st or {}).get("tp", 1))))
        values.update({
            "device.standby": dev["standby"], "device.blocked": dev["blocked"], "device.datetime": _xt_now_text(dev, self.time_s), "device.language": lang,
            "device.settings": _xt_copy.deepcopy(dev["settings"]), "device.messages": _xt_copy.deepcopy(dev["messages"]),
            "device.status_light": _xd_status_light(inst), "display.weight": w, "display.smarttrac": trac, "display.unit": _xd_unit(dev),
            "display.allowed_units": _xd_allowed_units(dev),
            "level.state": _xd_level_state(inst),
            "users.state": dict(management=dev["user_management"], current_user=dev["current_user"], logged_in=dev["logged_in"],
                                rights={p: _xd_allowed(dev, p) for p in XPR_PERMISSIONS},
                                users=[{k: v for k, v in u.items() if k != "password"} for u in dev["users"]], groups=_xt_copy.deepcopy(dev["groups"]),
                                unblocking_password_set=bool(dev["unblocking_password"]), block_reason=dev["block_reason"],
                                block_id=dev.get("block_seq", 0)),
            "methods.list": [dict(id=x["id"], type=x["type"], created=x["created"], name=x["settings"]["name"], comment=x["settings"]["comment"],
                                  lock=x["settings"]["lock"], settings=_xt_copy.deepcopy(x["settings"]),
                                  items=[dict(it, index=i + 1) for i, it in enumerate(x["items"])],
                                  paused=x["id"] in dev["tasks"], active=x["id"] == dev["active_method"]) for x in dev["methods"]],
            "methods.active": dev["active_method"], "task.current": task,
            "task.paused": [dict(method_id=k, method_name=(_xd_method(dev, k) or {"settings": {"name": k}})["settings"]["name"], started=v["started"],
                                 results=len(v["results"])) for k, v in dev["tasks"].items()],
            "task.statistics": _xd_stats(dev["task"], m) if dev["task"] is not None and m is not None else None,
            "profiles.list": _xt_copy.deepcopy(dev["profiles"]), "profiles.default": dev["default_profile"],
            "test_weights.list": _xt_copy.deepcopy(dev["test_weights"]),
            "tests.list": [dict(id=x["id"], type=x["type"], name=x["settings"]["name"], settings=_xt_copy.deepcopy(x["settings"]),
                                last=_xt_copy.deepcopy(x.get("last")), due=bool(x.get("due"))) for x in dev["tests"]],
            "adjustment.settings": _xt_copy.deepcopy(dev["adjustment"]), "adjustment.last": _xt_copy.deepcopy(dev["adjustment_state"]["last"]),
            "procedure.state": proc, "weighing.pending": (dev["pending"] or {}).get("action"),
            "history.adjustments": _xt_copy.deepcopy(dev["history"]["adjustments"]), "history.tests": _xt_copy.deepcopy(dev["history"]["tests"]),
            "history.changes": _xt_copy.deepcopy(dev["history"]["changes"]), "sensor.errors": dict(dev["errors"]),
            "interface.output": list(dev["output"]), "task.completed": _xt_copy.deepcopy(dev.get("completed", [])),
        })
        return values

    def set_environment(self, instance_id, *, tilt_rad=0.0, vibration=False, tilt_direction_rad=0.0, temperature_c=None):
        """Trusted host: tilt, vibration and (optional) the temperature of the weighing cell (degC, 5..40; 20 by default)."""
        if not _xpr_number(tilt_direction_rad) or (temperature_c is not None and (not _xpr_number(temperature_c) or not 5.0 <= temperature_c <= 40.0)):
            raise ValueError("Finite tilt direction and a cell temperature within 5..40 degC required")
        super().set_environment(instance_id, tilt_rad=tilt_rad, vibration=vibration)
        with self._lock:
            self._instances[instance_id]["tilt_direction_rad"] = float(tilt_direction_rad)
            if temperature_c is not None:
                self._instances[instance_id]["device"]["cell_temp_c"] = float(temperature_c)

    def set_sensor_errors(self, instance_id, **errors):
        """Trusted host: sensor error parameters of one instance (0 = ideal, the default); the adjustments correct span and offset."""
        bounds = {"span_error_rel": 0.01, "zero_offset_kg": 0.01, "zero_drift_kg_s": 1e-6, "internal_weight_error_kg": 0.001, "span_tc_per_k": 1e-4}
        if set(errors) - set(bounds) or any(not _xpr_number(v) or abs(v) > bounds[k] for k, v in errors.items()):
            raise ValueError("Known finite sensor error parameters within their numeric envelope required")
        with self._lock:
            if instance_id not in self._instances:
                raise ValueError("Unknown instance")
            inst = self._instances[instance_id]
            inst["device"]["errors"].update({k: float(v) for k, v in errors.items()})
            inst["revision"] += 1

    def _measured(self, inst, force_N):
        dev = inst.get("device")
        if dev is None:
            return force_N
        e = dev["errors"]
        return force_N * (1.0 + _xd_span(dev)) + (e["zero_offset_kg"] + dev["drift_kg"]) * _XT_G0

    def step(self, dt_s, *, pan_load_forces_N=None):
        """CHEM-001 step on the measured force (host force with the sensor errors), then the terminal's clock-driven behaviour."""
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
            measured = {}
            for key, inst in self._instances.items():
                f = float(forces.get(key, inst.get("host_force_N", inst["balance"].raw_force_N)))
                inst["host_force_N"] = f
                measured[key] = self._measured(inst, f)
            t0 = self.time_s
            try:
                super().step(dt_s, pan_load_forces_N=measured)
            finally:
                if self.time_s > t0:
                    self._tick(t0)

    def _tick(self, t0):
        failed = []
        for key, inst in self._instances.items():
            if "device" not in inst:
                continue
            before = self._scene(inst) if self.scene_adapter else None
            _xd_tick(inst, t0, self.time_s, self)
            if self.scene_adapter and self._scene(inst) != before:
                try:
                    self.scene_adapter(key, self._scene(inst))
                except Exception:
                    failed.append(key)
        if failed:
            raise RuntimeError("Process and common clock advanced; scene synchronization failed") from None

    def _after_base(self, instance_id, cap, args):
        """Device bookkeeping after a CHEM-001 command (their own effects are unchanged)."""
        inst = self._instances[instance_id]
        dev = inst["device"]
        if cap == "power.set":
            if args["enabled"]:
                dev["standby"] = False
                dev["power_epoch"] += 1
                if dev["user_management"]:
                    dev["logged_in"] = False
            else:
                dev["pending"], dev["procedure"], dev["internal_loaded"], dev["door_closed_by"] = None, None, False, None
                if dev.get("fault") is not None and XPR_FAULTS[dev["fault"]][2]:   # p.134: disconnect the power and reconnect
                    dev["fault"] = None
                    _xd_clear(dev, "fault")
        elif cap == "balance.set_display_unit":
            dev["display_unit"] = args["unit"]
        elif cap == "balance.set_filter":
            _xd_active_profile(dev)["environment"] = _XT_ENV_OF_FILTER[args["level"]]
        elif cap in ("balance.zero", "balance.tare"):
            dev["preset_active"] = False
        dev["last_activity_s"] = self.time_s

    def _capture(self, instance_id, display_id):
        """Close-up camera of the terminal: a PNG file named by its content (large data as a file reference)."""
        data, w, h = self._panels[instance_id].png_bytes(display_id)
        sha = _xt_hashlib.sha256(data).hexdigest()
        d = _xt_Path(self.panel_capture_dir) if self.panel_capture_dir else _xt_Path(_xt_tempfile.gettempdir()) / "chemistry_sim_panel" / instance_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{display_id}_{sha[:16]}.png"
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.time_s, image=dict(path=str(path), format="png", width=w, height=h, sha256=sha))


class XPR204ESession(_XT_BaseSession):
    """CHEM-001 session plus the terminal commands and panel.*; error codes of the terminal are stable strings like the CHEM-001 ones."""

    def _error(self, instance_id, code, request_id=None, status="rejected"):
        if code not in _XT_ERRORS:
            return super()._error(instance_id, code, request_id, status)
        return self._envelope(instance_id, request_id, status=status, error={"code": code, "message": _XT_ERRORS[code], "retryable": False,
                                                                                 "side_effects": "none", "progress": None})

    def _vis(self):
        return self._XPR204ESession__host._principals[self._XPR204ESession__principal][0]

    def describe(self, *, asset_type_id=None, instance_id=None):
        out = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        term = out.pop("terminal_capabilities", None)
        if term is None:
            return out
        vis = self._vis()
        for name, cap in term.items():
            if cap["visibility"] != "agent" and vis not in ("evaluator", "debug"):
                continue
            cap["state_effects"] = [e for e in cap["state_effects"] if e["state_id"] in out["state_slots"]]
            cap["observes"] = [sid for sid in cap["observes"] if sid in out["state_slots"]]
            out["capabilities"][name] = cap
        return out

    def _xt_foreign(self, inst, cap, args):
        """Another caller's running procedure (adjustment, test, density) or weighing task would be stopped or changed by cap, or the standby key
        would darken the terminal under it (lead rule 2026-10-19: ACCESS_DENIED on the terminal and the direct path); the evaluator / debug
        identities and the terminal's own timers are unrestricted.  Disconnecting the power (power.set models the AC adapter plug) is physical:
        never refused, the procedure is lost as at a power loss."""
        me, dev = self._XPR204ESession__principal, inst["device"]
        if me == "__panel__" or self._vis() in ("evaluator", "debug"):
            return False
        p, task = dev["procedure"], dev["task"]
        proc = p is not None and p["status"] == "running" and p.get("owner") not in (None, me)
        mine = lambda x: x is None or x.get("owner") in (None, me)                    # noqa: E731
        if cap in ("procedure.confirm", "procedure.select_weight", "procedure.cancel", "procedure.complete"):
            return p is not None and p.get("owner") not in (None, me)
        if cap == "standby.set":
            return isinstance(args, dict) and args.get("active") is True and proc
        if cap in ("task.add_result", "task.complete", "task.cancel", "task.set_id", "task.exclude_result"):
            return not mine(task)
        if cap == "method.start":
            paused = dev["tasks"].get(args.get("method_id")) if isinstance(args, dict) else None
            return (not mine(task) and (task["results"] or task.get("changed"))) or not mine(paused)
        if cap == "task.delete":
            return not mine(dev["tasks"].get(args.get("method_id")) if isinstance(args, dict) else None)
        return False

    def _checked(self, instance_id, inst, cap, args, request_id):
        host = self._XPR204ESession__host
        fail = lambda code, status="rejected": self._error(instance_id, code, request_id, status)  # noqa: E731
        term = host._metadata["asset_types"][XPR204E_TYPE]["terminal_capabilities"]
        if "device" in inst and self._xt_foreign(inst, cap, args):
            return fail("ACCESS_DENIED")
        if cap not in term:
            if cap in ("balance.tare", "balance.zero", "balance.reading") and "device" in inst and _xd_fault_blocks_weight(inst["device"]):
                return fail("PRECONDITION_FAILED")                               # an injected error of p.134: no weight value (CHEM-001 code)
            response = super()._checked(instance_id, inst, cap, args, request_id)
            if response["status"] == "succeeded" and "device" in host._instances[instance_id]:
                host._after_base(instance_id, cap, args)
            return response
        if _xt_validate(term[cap]["input_schema"], args):
            return fail("INVALID_ARGUMENT")
        if term[cap]["visibility"] != "agent" and self._vis() not in ("evaluator", "debug"):
            return fail("ACCESS_DENIED")
        if cap.startswith("panel."):
            return self._panel(instance_id, cap, args, request_id)
        candidate = _xt_copy.deepcopy(inst)
        before = host._scene(candidate) if host.scene_adapter else None
        try:
            result = _XT_COMMANDS[cap](host, candidate, args, _xt_now_text(candidate["device"], host.time_s), host.time_s)
        except _XTReject as exc:
            return fail(exc.code)
        candidate["device"]["last_activity_s"] = host.time_s
        me, cdev = self._XPR204ESession__principal, candidate["device"]
        if cap in ("adjustment.start", "test.start", "density.start") and cdev["procedure"] is not None:
            cdev["procedure"]["owner"] = me                                     # the procedure belongs to the caller that started it
        if cap == "method.start" and cdev["task"] is not None and cdev["task"].get("owner") is None:
            cdev["task"]["owner"] = me                                          # so does the weighing task
        if host.scene_adapter and host._scene(candidate) != before:
            try:
                host.scene_adapter(instance_id, host._scene(candidate))
            except Exception:
                return fail("DEVICE_ERROR", "failed")
        candidate["revision"] += 1
        host._instances[instance_id] = candidate
        return self._envelope(instance_id, request_id, result=result)

    def _panel(self, instance_id, cap, args, request_id):
        host = self._XPR204ESession__host
        if instance_id not in host._panels:
            return self._error(instance_id, "UNSUPPORTED", request_id, "unsupported")
        panel = host._panels[instance_id]
        try:
            if cap == "panel.capture":
                return self._envelope(instance_id, request_id, result=host._capture(instance_id, args["display_id"]))
            if cap == "panel.frame":
                return self._envelope(instance_id, request_id, result={"frame": panel.frame()})
            host._instances[instance_id]["device"]["last_activity_s"] = host.time_s
            caller, host._xt_caller = getattr(host, "_xt_caller", None), self._XPR204ESession__principal   # commands run as this caller
            try:
                if cap == "panel.press":
                    accepted = panel.press(args["input_id"], float(args.get("duration_s", 0.1)))
                else:
                    if ("to_x" in args) != ("to_y" in args):
                        return self._error(instance_id, "INVALID_ARGUMENT", request_id)
                    to = (args["to_x"], args["to_y"]) if "to_x" in args else None
                    accepted = panel.touch(args["display_id"], args["x"], args["y"], float(args.get("duration_s", 0.1)), to=to)
            finally:
                host._xt_caller = caller
        except _xt_pu.PanelError:
            return self._error(instance_id, "INVALID_ARGUMENT", request_id)
        host._instances[instance_id]["device"]["last_activity_s"] = host.time_s
        return self._envelope(instance_id, request_id, result={"accepted": bool(accepted)})
# <<< CHEM-001 XPR204E terminal


# >>> CHEM-102 Metrohm 899 dialog firmware and front panel
# The 899 Coulometer as its operator sees it (operating manual references/manuals/metrohm_899/, 'p.N' = PDF page): methods (templates KFC /
# KFC-Blank / Blank, up to 100 stored, p.50-52), the method parameters of chapter 7 (p.83-97), conditioning with start drift, stabilizing time
# and stop time (p.59, p.83-85), the determination sequence (add sample 8 s, sample-data request, pause, titration, stop criterion and stop
# time, results; p.59-62, p.85-91), calculations R1-R5 with the formula variables (p.49-50, p.91-95), statistics (p.67-68, p.95-96), common
# variables (p.75-76), sample data and sample table (p.54-58), manual stirrer (p.70-71), system settings (p.72-80), system initialization
# (p.108-109), plus the front panel (panel_ui.json beside the contract, run by scripts/asset_runtime/_panel_ui.py).  Metrohm899DeviceHost and
# Metrohm899DeviceSession extend the CHEM-102 classes above, whose capabilities keep their meaning; the CHEM-102 host keeps its capability set.
# The indicator electrode is ideal: the medium sits at 'Endpoint at' whenever no sample water is left (no electrode kinetics, no noise).
import copy as _kd_copy
import datetime as _kd_dt
import hashlib as _kd_hashlib
import json as _kd_json
import math as _kd_math
import os as _kd_os
import re as _kd_re
import tempfile as _kd_tempfile
from pathlib import Path as _kd_Path

METROHM899_ASSET_VERSION = "1.2.2"
METROHM899_ASSET_DIR = _kd_Path(__file__).resolve().parents[1] / "assets/basic40_batch02/t4_measure/metrohm_899"
KD_UG_MIN_PER_KG_S = 6e10                                 # 1 kg/s = 1e9 ug x 60 s/min
KD_UG_PER_KG = 1e9
KD_BOOT_S = 5.0                                           # system test at switch-on (p.45 'takes some time'; assumption)
KD_INIT_S = 80.0                                          # system initialization (p.109 'approximately 80 s')
KD_ADD_SAMPLE_S = 8.0                                     # request for adding the sample (p.60)
KD_COND_BEEP_S = 10.0                                     # beep when conditioned for 10 s without interruption (p.74)
KD_MAX_METHODS, KD_MAX_LINES, KD_MAX_STATS, KD_MAX_MP = 100, 99, 20, 1000
KD_EPOCH = _kd_dt.datetime(2026, 1, 1, 0, 0, 0)           # device clock at simulation time 0 until clock.set (assumption)
KD_PROGRAM_VERSION = "58990011"                           # shown in the software update dialog (p.82)
KD_TEMPLATES = ("KFC", "KFC-Blank", "Blank")
KD_UNITS = ("g", "mg", "µg", "mL", "µL", "pieces")
KD_UNIT_KG = {"g": 1e-3, "mg": 1e-6, "µg": 1e-9}
KD_RESULT_UNITS = ("%", "mg", "mg/piece", "mg/g", "mg/mL", "ppm", "µg")
KD_CURRENTS = {"100 mA": 0.1, "200 mA": 0.2, "400 mA": 0.4}
KD_RATES = {"slow": (120.0, 1000.0, 0.3), "optimal": (70.0, None, 15.0), "fast": (30.0, None, 30.0)}   # dynamics mV, max / min rate ug/min (p.87)
KD_STATUS_TEXT = {"off": "", "boot": "", "init": "", "ready": "ready", "cond.busy": "cond.busy", "cond.ok": "cond.ok", "busy": "busy", "hold": "hold"}
KD_VARIABLES = ("C00", "CI1", "CI2", "MCQ", "MCD", "MDC", "DDC", "MIM", "MIT", "MCM", "MCT", "DD") + tuple(f"EP{i}" for i in range(1, 10)) + \
    tuple(f"R{i}" for i in range(1, 6)) + tuple(f"CV0{i}" for i in range(1, 6)) + tuple(f"SMN{i}" for i in range(1, 6))


def _kd_p(key, label, typ, default, page, group, **kw):
    return dict(key=key, label=label, type=typ, default=default, page=page, group=group, **kw)


# Method parameters kept by the dialog firmware (chapter 7).  Start drift, extraction time and stirring rate are the CHEM-102 settings slots
# (written with coulometry.configure); the generator current is kept here because 'auto' is not a CHEM-102 current.
KD_PARAMS = [
    _kd_p("conditioning", "Conditioning", "bool", True, "p.83", "Conditioning"),
    _kd_p("drift_correction", "Drift correction", "enum", "auto", "p.83-84", "Conditioning", options=["auto", "manual", "off"]),
    _kd_p("drift_value", "Drift value", "num", 0.0, "p.84", "Conditioning", min=0.0, max=99.9, step=0.1, decimals=1, unit="µg/min"),
    _kd_p("automatic_start", "Automatic start", "bool", False, "p.84", "Conditioning"),
    _kd_p("threshold", "Threshold value", "num", 50.0, "p.84", "Conditioning", min=0.0, max=999.0, step=1.0, decimals=0, unit="mV"),
    _kd_p("stabilizing_time", "Stabilizing time", "num", 0.0, "p.84", "Conditioning", min=0.0, max=999999.0, step=1.0, decimals=0, unit="s"),
    _kd_p("cond_stop_time", "Cond. stop time", "num", None, "p.84-85", "Conditioning", min=1.0, max=999999.0, step=1.0, decimals=0, unit="s",
          none="off"),
    _kd_p("measured_value_display", "Measured value display", "bool", False, "p.85", "Conditioning"),
    _kd_p("pause", "Pause", "num", 0.0, "p.85", "Start conditions", min=0.0, max=999999.0, step=1.0, decimals=0, unit="s"),
    _kd_p("request_id", "Request sample ID", "enum", "off", "p.85", "Start conditions", options=["ID1", "ID2", "ID1&ID2", "off"]),
    _kd_p("request_size", "Request sample size", "bool", True, "p.85", "Start conditions"),
    _kd_p("request_unit", "Request sample unit", "bool", False, "p.85", "Start conditions"),
    _kd_p("hold_at_request", "Hold at request", "bool", True, "p.85-86", "Start conditions"),
    _kd_p("endpoint", "Endpoint at", "num", 50.0, "p.86", "Control parameters", min=-1250.0, max=1250.0, step=0.1, decimals=1, unit="mV",
          none="off"),
    _kd_p("titration_rate", "Titration rate", "enum", "optimal", "p.86", "Control parameters", options=["slow", "optimal", "fast", "user"]),
    _kd_p("dynamics", "Dynamics", "num", 70.0, "p.86-87", "Control parameters", min=0.1, max=1250.0, step=0.1, decimals=1, unit="mV", none="off"),
    _kd_p("max_rate", "Max. rate", "num", None, "p.87", "Control parameters", min=1.5, max=2241.0, step=0.1, decimals=1, unit="µg/min",
          none="max."),
    _kd_p("min_rate", "Min. rate", "num", 15.0, "p.87", "Control parameters", min=0.3, max=999.9, step=0.1, decimals=1, unit="µg/min"),
    _kd_p("stop_criterion", "Stop criterion", "enum", "rel. drift", "p.88", "Control parameters", options=["drift", "time", "rel. drift", "off"]),
    _kd_p("stop_drift", "Stop drift", "num", 5.0, "p.88", "Control parameters", min=1.0, max=999.0, step=1.0, decimals=0, unit="µg/min"),
    _kd_p("delay_time", "Delay time", "num", 10.0, "p.88", "Control parameters", min=0.0, max=999.0, step=1.0, decimals=0, unit="s"),
    _kd_p("rel_stop_drift", "Relative stop drift", "num", 5.0, "p.89", "Control parameters", min=1.0, max=999.0, step=1.0, decimals=0,
          unit="µg/min"),
    _kd_p("generator_electrode", "Generator electr.", "enum", "w/o diaphr.", "p.89", "Titration parameters", options=["with diaphr.", "w/o diaphr."]),
    _kd_p("generator_current", "Generator current", "enum", "400 mA", "p.89", "Titration parameters", options=["100 mA", "200 mA", "400 mA", "auto"]),
    _kd_p("stirrer", "Stirrer", "bool", True, "p.90", "Titration parameters"),
    _kd_p("ipol", "I(pol)", "enum", 10, "p.90", "Titration parameters", options=[5, 10, 20, 30], unit="µA"),
    _kd_p("electrode_test", "Electrode test", "bool", False, "p.90", "Titration parameters"),
    _kd_p("mp_interval", "Time interval MP", "num", 2.0, "p.90-91", "Titration parameters", min=0.1, max=999999.0, step=0.1, decimals=1, unit="s"),
    _kd_p("temperature", "Temperature", "num", 25.0, "p.91", "Titration parameters", min=-20.0, max=150.0, step=0.1, decimals=1, unit="°C"),
    _kd_p("stop_time", "Stop time", "num", None, "p.91", "Stop conditions", min=1.0, max=999999.0, step=1.0, decimals=0, unit="s", none="off"),
    _kd_p("statistics", "Statistics", "bool", False, "p.95", "Statistics"),
    _kd_p("n_determinations", "Number of determinations", "num", 3, "p.95-96", "Statistics", min=2, max=20, step=1, decimals=0, integer=True),
    _kd_p("report_results", "Results", "bool", False, "p.96", "Reports"),
    _kd_p("report_curve", "Curve", "bool", False, "p.96", "Reports"),
    _kd_p("report_calc", "Calculations/Statistics", "bool", False, "p.96", "Reports"),
    _kd_p("report_mp", "Measuring point list", "bool", False, "p.96", "Reports"),
    _kd_p("report_parameters", "Parameters", "bool", False, "p.96-97", "Reports"),
    _kd_p("report_pc_lims", "PC/LIMS", "bool", False, "p.97", "Reports"),
]
KD_PARAM = {p["key"]: p for p in KD_PARAMS}
# calculation fields per result R1-R5 (p.91-95): method.configure keys 'calc.<n>.<field>'
KD_CALC_FIELDS = {"name": 12, "formula": 30, "decimals": None, "unit": None, "save_cv": None}
KD_CALC_TEMPLATES = {"KFC": ("Water", "EP1/C00", 1, "ppm", False), "KFC-Blank": ("Water", "(EP1-CV01)/C00", 1, "ppm", False),
                     "Blank": ("Blank", "EP1", 2, "µg", True)}
# System settings (chapter 6) and the Control dialog (p.53)
KD_COM = dict(baud=["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"], data_bits=["7", "8"], stop_bits=["1", "2"],
              parity=["even", "none", "odd"], handshake=["hardware", "software", "none"])
KD_SETTINGS = [
    _kd_p("user_name", "User name", "text", "", "p.72", "Settings", max_len=12),
    _kd_p("instrument_name", "Instrument name", "text", "", "p.72", "Settings", max_len=10),
    _kd_p("language", "Language", "enum", "English", "p.72-73", "Settings", options=["English"]),
    _kd_p("dialog_type", "Dialog type", "enum", "Expert", "p.73-74", "Settings", options=["Expert", "Routine"]),
    _kd_p("contrast", "Contrast", "num", 212, "p.74", "Settings", min=150, max=240, step=1, decimals=0, integer=True),
    _kd_p("beep", "Beep", "bool", True, "p.74", "Settings"),
    _kd_p("pc_lims_report", "PC/LIMS report", "enum", "USB Stick", "p.78", "External devices", options=["COM2", "USB Stick"]),
    _kd_p("printer", "Printer", "enum", "Custom (ESC-POS)", "p.78", "External devices",
          options=["Citizen (ESC-POS)", "Custom (ESC-POS)", "Epson", "Epson (ESC-POS)", "HP DeskJet", "HP LaserJet", "Seiko (ESC-POS)"]),
    _kd_p("graphics_width", "Graphics width", "num", 576, "p.78", "External devices", min=100, max=3000, step=1, decimals=0, integer=True,
          unit="Pixels"),
    _kd_p("keyboard_layout", "Keyboard layout", "enum", "English US", "p.78-79", "External devices",
          options=["English US", "French FR", "German CH", "German DE", "Spanish ES"]),
    _kd_p("balance", "Balance", "enum", "Sartorius", "p.79", "External devices",
          options=["AND", "Mettler", "Mettler AT", "Mettler AX", "Ohaus", "Precisa", "Sartorius", "Shimadzu"]),
    _kd_p("control.sample_table", "Sample table", "bool", False, "p.53", "Control"),
    _kd_p("control.autostart", "Autostart", "bool", False, "p.53", "Control"),
    _kd_p("control.autostarts", "Number of autostarts", "num", None, "p.53", "Control", min=1, max=50, step=1, decimals=0, integer=True, none="table"),
]
for _kd_port in ("com1", "com2"):
    KD_SETTINGS += [_kd_p(f"{_kd_port}.{k}", label, "enum", default, "p.79-80", _kd_port.upper() + " settings", options=KD_COM[k])
                    for k, label, default in (("baud", "Baud rate", "9600"), ("data_bits", "Data bits", "8"), ("stop_bits", "Stop bits", "1"),
                                              ("parity", "Parity", "none"), ("handshake", "Handshake", "hardware"))]
KD_SETTING = {p["key"]: p for p in KD_SETTINGS}
KD_ERRORS = {"POWER_OFF": "The instrument is switched off", "NOT_READY": "The instrument is starting up or initializing",
             "COND_NOT_OK": "012-111 Conditioning not OK: start the determination when Conditioning OK is displayed",
             "NOT_FOUND": "No such method, line, result or determination", "LIMIT": "Table full (100 methods, 99 lines, 20 determinations)",
             "INVALID_FORMULA": "Formula syntax error", "NO_RESULT": "No determination has been carried out",
             "ELECTRODE_TEST": "Electrode test failed: the indicator electrode is not connected",
             "INVALID_VALUE": "Value not valid (date / time / name)",
             "ACCESS_DENIED": "Started by another caller: only that caller can stop, power off or change this conditioning / determination"}


def _kd_param_schema(p):
    if p["type"] == "bool":
        s = {"type": "boolean"}
    elif p["type"] == "enum":
        s = {"type": "integer" if isinstance(p["options"][0], int) else "string", "enum": list(p["options"])}
    elif p["type"] == "text":
        s = {"type": "string", "maxLength": p["max_len"]}
    else:
        s = {"type": "integer" if p.get("integer") else "number", "minimum": p["min"], "maximum": p["max"]}
        if p.get("unit"):
            s["x-unit"] = p["unit"]
    return {"anyOf": [s, {"type": "null"}]} if p.get("none") else s


def _kd_params_schema():
    props = {p["key"]: _kd_param_schema(p) for p in KD_PARAMS}
    return {"type": "object", "properties": props, "additionalProperties": False, "minProperties": 1}


def _kd_settings_schema():
    return {"type": "object", "properties": {p["key"]: _kd_param_schema(p) for p in KD_SETTINGS}, "additionalProperties": False, "minProperties": 1}


def _kd_check(schema, v):
    """True when v satisfies the subset of JSON schema used by the device commands."""
    if "anyOf" in schema:
        return any(_kd_check(s, v) for s in schema["anyOf"])
    t = schema.get("type")
    ok = {"object": isinstance(v, dict), "array": isinstance(v, list), "string": isinstance(v, str), "boolean": type(v) is bool,
          "integer": type(v) is int, "null": v is None, "number": type(v) in (int, float) and _kd_math.isfinite(v)}.get(t, True)
    if not ok or ("enum" in schema and (v not in schema["enum"] or type(v) is bool and t != "boolean")):
        return False
    if t in ("number", "integer") and not schema.get("minimum", -_kd_math.inf) - 1e-9 <= v <= schema.get("maximum", _kd_math.inf) + 1e-9:
        return False
    if t == "string" and not schema.get("minLength", 0) <= len(v) <= schema.get("maxLength", 10 ** 6):
        return False
    if t == "string" and schema.get("pattern") and _kd_re.search(schema["pattern"], v) is None:
        return False
    if t == "object":
        props = schema.get("properties", {})
        if any(k not in v for k in schema.get("required", [])) or len(v) < schema.get("minProperties", 0):
            return False
        if schema.get("additionalProperties") is False and any(k not in props for k in v):
            return False
        return all(_kd_check(props[k], x) for k, x in v.items() if k in props)
    if t == "array":
        return schema.get("minItems", 0) <= len(v) <= schema.get("maxItems", 10 ** 6) and all(_kd_check(schema.get("items", {}), x) for x in v)
    return True


# ---------------------------------------------------------------- formula editor (p.49-50, p.103 arithmetic)
_KD_TOKEN = _kd_re.compile(r"\s*(?:(\d+(?:\.\d*)?|\.\d+)|([A-Z][A-Z0-9]*)|(.))")


def _kd_parse(formula):
    """Parse a calculation formula (numbers, + - * / and parentheses, the variables of p.39-40) into a nested tuple; ValueError on syntax."""
    toks = []
    for num, name, op in _KD_TOKEN.findall(formula.strip()):
        if num:
            toks.append(("num", float(num)))
        elif name:
            if name not in KD_VARIABLES:
                raise ValueError(f"unknown variable {name}")
            toks.append(("var", name))
        elif op.strip():
            if op not in "+-*/()":
                raise ValueError(f"character {op!r}")
            toks.append(("op", op))
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else ("end", None)

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def expr():
        node = term()
        while peek() in (("op", "+"), ("op", "-")):
            node = (take()[1], node, term())
        return node

    def term():
        node = factor()
        while peek() in (("op", "*"), ("op", "/")):
            node = (take()[1], node, factor())
        return node

    def factor():
        kind, val = take() if pos[0] < len(toks) else ("end", None)
        if kind in ("num", "var"):
            return (kind, val)
        if (kind, val) == ("op", "-"):
            return ("neg", factor())
        if (kind, val) == ("op", "("):
            node = expr()
            if take() != ("op", ")") if pos[0] < len(toks) else True:
                raise ValueError("missing )")
            return node
        raise ValueError("syntax")

    if not toks:
        raise ValueError("empty")
    node = expr()
    if pos[0] != len(toks):
        raise ValueError("syntax")
    return node


def _kd_eval(node, variables):
    """Value of a parsed formula; None when a variable is invalid (not set) or on a division by zero."""
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "var":
        v = variables.get(node[1])
        return v if type(v) in (int, float) and _kd_math.isfinite(v) else None
    if kind == "neg":
        v = _kd_eval(node[1], variables)
        return None if v is None else -v
    a, b = _kd_eval(node[1], variables), _kd_eval(node[2], variables)
    if a is None or b is None or (kind == "/" and b == 0):
        return None
    v = {"+": a + b, "-": a - b, "*": a * b, "/": a / b if kind == "/" else None}[kind]
    return v if _kd_math.isfinite(v) else None


def _kd_formula_ok(formula):
    if formula == "":
        return True
    try:
        _kd_parse(formula)
        return True
    except ValueError:
        return False


def _kd_round(v, n):
    """Commercial rounding of displayed results (p.103): half away from zero."""
    if v is None:
        return None
    x = _kd_math.floor(abs(v) * 10 ** n + 0.5 + 1e-9) / 10 ** n
    return _kd_math.copysign(x, v) if x else 0.0


# ---------------------------------------------------------------- device model
def _kd_template(name):
    calc = KD_CALC_TEMPLATES[name]
    calcs = [dict(name=calc[0], formula=calc[1], decimals=calc[2], unit=calc[3], save_cv=calc[4])] + \
        [dict(name="", formula="", decimals=2, unit="%", save_cv=False) for _ in range(4)]
    return dict(name=name, template=name, params={p["key"]: _kd_copy.deepcopy(p["default"]) for p in KD_PARAMS}, calcs=calcs)


def _kd_new_device():
    return dict(power_epoch=0, boot_until=None, init_until=None, clock_offset_s=0.0,
                settings={p["key"]: _kd_copy.deepcopy(p["default"]) for p in KD_SETTINGS}, user_unit=None, result_user_unit=None,
                method=_kd_template("KFC"), methods=[], sample=dict(id1="", id2="", size=1.0, unit="g"),
                table=[], stored_table=None, cvs=[None] * 5, stirrer=dict(on=False, rate=8),
                cond=dict(phase="off", since=None, below_since=None, ok_since=None, beeped=False),
                run=None, result=None, result_seq=0, stats=dict(target=3, rows=[]), autostarts_done=0, autostart_pending=False,
                mp=[], beeps=0, restage=0, message=None, message_seq=0)


def _kd_now_text(dev, t):
    return (KD_EPOCH + _kd_dt.timedelta(seconds=t + dev["clock_offset_s"])).strftime("%Y-%m-%dT%H:%M:%S")


def _kd_status(s, dev, t):
    if not s["power.enabled"]:
        return "off"
    if dev["init_until"] is not None:
        return "init"
    if dev["boot_until"] is not None and t < dev["boot_until"] - 1e-9:
        return "boot"
    run = dev["run"]
    if run is not None:
        return "hold" if run["phase"] == "request" else "busy"
    if s["coulometry.running"]:
        return "busy"
    return {"busy": "cond.busy", "ok": "cond.ok"}.get(dev["cond"]["phase"], "ready")


def _kd_drift_ug_min(s):
    d = s["measurement.drift_kg_s"]
    return None if d is None else d * KD_UG_MIN_PER_KG_S


def _kd_cond_ready(s):
    """The working medium can be titrated to the endpoint: reagent in the cell and the electrodes connected (CHEM-102 slots)."""
    return s["reagent.ready"] and s["measurement.drift_kg_s"] is not None and all(s[f"slot.{x}.installed"] for x in ("cell", "generator", "indicator"))


def _kd_cond_below(s, dev):
    d = _kd_drift_ug_min(s)
    return _kd_cond_ready(s) and d is not None and d < s["settings.start_drift_threshold_kg_s"] * KD_UG_MIN_PER_KG_S - 1e-9


def _kd_cond_start(s, dev, t):
    dev["cond"] = dict(phase="busy", since=t, below_since=t if _kd_cond_below(s, dev) else None, ok_since=None, beeped=False)
    _kd_cond_update(s, dev, t)


def _kd_cond_update(s, dev, t):
    """Conditioning: 'Conditioning OK' once the measured drift has stayed below Start drift for the stabilizing time (p.83-84); stops after
    Cond. stop time (p.84); a beep after 10 s of uninterrupted Conditioning OK (p.74)."""
    c = dev["cond"]
    if c["phase"] not in ("busy", "ok"):
        return
    p = dev["method"]["params"]
    if p["cond_stop_time"] is not None and t >= c["since"] + p["cond_stop_time"] - 1e-9:
        dev["cond"] = dict(phase="off", since=None, below_since=None, ok_since=None, beeped=False)
        return
    if _kd_cond_below(s, dev):
        if c["below_since"] is None:
            c["below_since"] = t
        if t >= c["below_since"] + p["stabilizing_time"] - 1e-9:
            if c["phase"] != "ok":
                c["phase"], c["ok_since"], c["beeped"] = "ok", max(t, c["below_since"] + p["stabilizing_time"]), False
            if not c["beeped"] and t >= c["ok_since"] + KD_COND_BEEP_S - 1e-9:
                c["beeped"] = True
                if dev["settings"]["beep"]:
                    dev["beeps"] += 1
    else:
        c.update(phase="busy", below_since=None, ok_since=None, beeped=False)


def _kd_cond_events(s, dev):
    c, p = dev["cond"], dev["method"]["params"]
    out = []
    if c["phase"] in ("busy", "ok"):
        if p["cond_stop_time"] is not None:
            out.append(c["since"] + p["cond_stop_time"])
        if c["phase"] == "busy" and c["below_since"] is not None:
            out.append(c["below_since"] + p["stabilizing_time"])
        if c["phase"] == "ok" and not c["beeped"]:
            out.append(c["ok_since"] + KD_COND_BEEP_S)
    return out


def _kd_sample_mass_kg(sample):
    f = KD_UNIT_KG.get(sample["unit"])
    size = abs(sample["size"]) if type(sample["size"]) in (int, float) else 0.0          # the sign is ignored in calculations (p.92)
    return f * size if f is not None and size > 0 else None


KD_EP_EQ_MV = {5: 25.0, 10: 50.0, 20: 100.0, 30: 150.0}  # equivalence voltage of the titration curve by I(pol) (p.96 Table 7; 5 uA: 25 mV assumed)
KD_SLOPE_MV = {5: 40.0, 10: 70.0, 20: 100.0, 30: 120.0}  # rise of the curve over KD_CURVE_UG of water by I(pol) (Table 7 dynamics; 5 uA assumed)
KD_CURVE_UG = 50.0                                        # water across which the indicator voltage rises by that span (assumption)
KD_RANGE_MV = 1250.0                                      # measuring range of the indicator input (Endpoint at -1250..1250 mV, p.86)


def _kd_curve_mv(p, water_ug):
    """Indicator voltage of the titration curve (Figure 26, p.96): linear in the water excess (ug; negative = iodine excess) through the
    equivalence voltage of the polarization current, limited to the measuring range.  The slope is an assumption."""
    i = p["ipol"]
    return max(-KD_RANGE_MV, min(KD_RANGE_MV, KD_EP_EQ_MV[i] + KD_SLOPE_MV[i] * water_ug / KD_CURVE_UG))


def _kd_endpoint_offset_ug(p):
    """Water left (positive: under-titration) or iodine excess (negative: over-titration) where the curve of I(pol) crosses 'Endpoint at'
    (p.95-96: Endpoint at, Dynamics and I(pol) belong together; 50 / 100 / 150 mV at 10 / 20 / 30 uA)."""
    ep = p["endpoint"]
    return 0.0 if ep is None else (ep - KD_EP_EQ_MV[p["ipol"]]) * KD_CURVE_UG / KD_SLOPE_MV[p["ipol"]]


def _kd_control_extra_s(p, current_a, water_ug):
    """Time the control range adds (p.86-87): inside Endpoint + Dynamics the iodine generation falls linearly with the voltage from the
    maximum rate (the generator current) to Min. rate; outside it runs at the maximum rate.  Dynamics off: the whole titration is finely
    controlled.  t = integral of dw / rate(w) minus the time at the maximum rate (minutes -> s)."""
    rmax = current_a * KF899_KG_PER_COULOMB * KD_UG_MIN_PER_KG_S
    rmin = min(_kd_min_rate(p), rmax)
    dyn = _kd_dynamics(p)
    w_dyn = max(water_ug, 1e-9) if dyn is None else dyn * KD_CURVE_UG / KD_SLOPE_MV[p["ipol"]]
    w = min(w_dyn, max(0.0, water_ug))
    if w <= 0 or rmax <= rmin + 1e-12:
        return 0.0
    slow = w_dyn / (rmax - rmin) * _kd_math.log((rmin + (rmax - rmin) * w / w_dyn) / rmin)
    return max(0.0, slow - w / rmax) * 60.0


def _kd_mv(dev, at_endpoint, water_kg=None):
    """Indicator voltage: the controller holds the medium at 'Endpoint at'; while water is titrated it follows the titration curve of I(pol)
    (_kd_curve_mv) with the water still to be titrated; unknown water: Endpoint + Dynamics.  No electrode kinetics."""
    p = dev["method"]["params"]
    ep = p["endpoint"]
    if ep is None:
        return None
    if at_endpoint:
        return ep
    if water_kg is not None:
        return _kd_curve_mv(p, water_kg * KD_UG_PER_KG + _kd_endpoint_offset_ug(p))
    return ep + (_kd_dynamics(p) or 0.0)


def _kd_dynamics(p):
    return KD_RATES[p["titration_rate"]][0] if p["titration_rate"] in KD_RATES else p["dynamics"]


def _kd_max_rate(p):
    return KD_RATES[p["titration_rate"]][1] if p["titration_rate"] in KD_RATES else p["max_rate"]


def _kd_min_rate(p):
    return KD_RATES[p["titration_rate"]][2] if p["titration_rate"] in KD_RATES else p["min_rate"]


def _kd_current_a(p):
    """Generator current of a titration: the method's current ('auto' = the 400 mA maximum, p.89, p.116) limited by Max. rate (p.87) to the
    largest CHEM-102 current whose iodine rate does not exceed it (100 mA at least).  Engineering assumption."""
    cur = 0.4 if p["generator_current"] == "auto" else KD_CURRENTS[p["generator_current"]]
    cap = _kd_max_rate(p)
    if cap is not None:
        ok = [c for c in (0.1, 0.2, 0.4) if c <= cur + 1e-12 and c * KF899_KG_PER_COULOMB * KD_UG_MIN_PER_KG_S <= cap + 1e-9]
        cur = max(ok) if ok else 0.1
    return cur


def _kd_hold_s(p, drift_ug_min, stop_time):
    """Endpoint hold of the ideal model from the stop criterion (p.88-89): after the last sample water the drift is the background drift."""
    big = stop_time if stop_time is not None else 1e9
    crit = p["stop_criterion"]
    if crit == "rel. drift":
        return 0.0
    if crit == "drift":
        return 0.0 if drift_ug_min is not None and drift_ug_min <= p["stop_drift"] + 1e-9 else big
    if crit == "time":
        return float(p["delay_time"])
    return big


def _kd_variables(s, dev, r, stats_rows=None, sample=None):
    sample = sample or r["sample"]
    v = dict(C00=abs(sample["size"]) if type(sample["size"]) in (int, float) else None, EP1=r["ep1_ug"], MCQ=r["mcq_ug"], MCD=r["mcd_s"],
             MDC=r["mdc_ug_min"], DDC=r["ddc_s"], MIM=r["mim_mv"], MIT=r["mit_c"], MCM=r["mcm_mv"], MCT=r["mct_c"], DD=r["dd_s"])
    for i, x in enumerate(("id1", "id2")):
        try:
            v[f"CI{i + 1}"] = float(sample[x])
        except (TypeError, ValueError):
            v[f"CI{i + 1}"] = None
    for i, cv in enumerate(dev["cvs"]):
        v[f"CV0{i + 1}"] = cv
    return v


def _kd_calculate(s, dev, r, sample=None):
    """Results R1-R5 of a determination with the method's calculations; SMN# is the mean of the series including this determination."""
    variables = _kd_variables(s, dev, r, sample=sample)
    out = []
    for i, c in enumerate(dev["method"]["calcs"]):
        value = None
        if c["formula"]:
            try:
                value = _kd_eval(_kd_parse(c["formula"]), variables)
            except ValueError:
                value = None
        variables[f"R{i + 1}"] = value
        out.append(dict(index=i + 1, name=c["name"], formula=c["formula"], value=value, unit=c["unit"], decimals=c["decimals"],
                        text="invalid" if value is None else f"{_kd_round(value, c['decimals']):.{c['decimals']}f}") if c["formula"] or c["name"] else None)
    return [x for x in out if x is not None]


def _kd_stats(dev):
    """Statistics of the series (p.67-68, p.104): per result mean, absolute and relative standard deviation of the included determinations."""
    rows = dev["stats"]["rows"]
    out = []
    for k in range(5):
        vals = [r["values"][k] for r in rows if r["included"] and k < len(r["values"]) and r["values"][k] is not None]
        calc = dev["method"]["calcs"][k]
        if not calc["formula"]:
            continue
        n = len(vals)
        mean = sum(vals) / n if n else None
        sabs = _kd_math.sqrt(sum((x - mean) ** 2 for x in vals) / (n - 1)) if n > 1 else None
        srel = 100.0 * sabs / abs(mean) if sabs is not None and mean else None
        out.append(dict(index=k + 1, name=calc["name"], unit=calc["unit"], decimals=calc["decimals"], n=n, mean=mean, s_abs=sabs, s_rel=srel))
    return dict(done=len(rows), target=dev["stats"]["target"], results=out,
                rows=[dict(index=i + 1, included=r["included"], values=list(r["values"]), size=r["size"], unit=r["unit"]) for i, r in enumerate(rows)])


def _kd_assign_cvs(dev, results):
    st = _kd_stats(dev) if dev["method"]["params"]["statistics"] else None
    for r in results:
        if dev["method"]["calcs"][r["index"] - 1]["save_cv"]:
            if st is not None:
                m = next((x["mean"] for x in st["results"] if x["index"] == r["index"]), None)
                dev["cvs"][r["index"] - 1] = m
            else:
                dev["cvs"][r["index"] - 1] = r["value"]


def _kd_public(s, dev, t):
    """Agent-visible device slots (what the dialogs show), derived from the device model and the CHEM-102 state."""
    p = dev["method"]["params"]
    c = dev["cond"]
    run = dev["run"]
    drift = _kd_drift_ug_min(s)
    live = None
    if run is not None:
        el = 0.0 if run.get("t_titr") is None else max(0.0, t - run["t_titr"])
        live = dict(phase=run["phase"], request_open=run["request_open"], hold=run["phase"] == "request",
                    add_remaining_s=max(0.0, run["add_until"] - t) if run["phase"] == "add" else None,
                    pause_remaining_s=max(0.0, run["pause_until"] - t) if run["phase"] == "pause" else None,
                    water_ug=s["measurement.charge_c"] * KF899_KG_PER_COULOMB * KD_UG_PER_KG if run["phase"] == "titration" else 0.0,
                    rate_ug_min=(s["settings.current_a"] * KF899_KG_PER_COULOMB * KD_UG_MIN_PER_KG_S if s["coulometry.status"] == "TITRATING"
                                 else drift) if run["phase"] == "titration" else None,
                    time_s=el, sample=dict(run["sample"]), line=run.get("line"),
                    curve=[[m["time_s"], m["water_ug"]] for m in dev["mp"]] + ([[el, s["measurement.charge_c"] * KF899_KG_PER_COULOMB * KD_UG_PER_KG]]
                                                                             if run["phase"] == "titration" else []))
    status = _kd_status(s, dev, t)
    return {
        "device.status": status, "device.status_text": KD_STATUS_TEXT[status],
        "device.datetime": _kd_now_text(dev, t),
        "device.settings": _kd_copy.deepcopy(dev["settings"]),
        "device.method": dict(name=dev["method"]["name"], template=dev["method"]["template"], params=_kd_copy.deepcopy(p),
                              calcs=[dict(c, index=i + 1) for i, c in enumerate(_kd_copy.deepcopy(dev["method"]["calcs"]))],
                              start_drift=s["settings.start_drift_threshold_kg_s"] * KD_UG_MIN_PER_KG_S,
                              extraction_time=s["settings.extraction_time_s"], stirring_rate=s["settings.stirring_level"],
                              dynamics=_kd_dynamics(p), max_rate=_kd_max_rate(p), min_rate=_kd_min_rate(p)),
        "device.methods": [dict(name=m["name"], template=m["template"]) for m in dev["methods"]],
        "device.sample": dict(dev["sample"]), "device.units": list(KD_UNITS) + ([dev["user_unit"]] if dev["user_unit"] else []),
        "device.result_units": list(KD_RESULT_UNITS) + ([dev["result_user_unit"]] if dev["result_user_unit"] else []),
        "device.unit_choices": [dict(value=u, text=u) for u in list(KD_UNITS) + ([dev["user_unit"]] if dev["user_unit"] else [])] +
                               [dict(value="__user__", text=">User-defined")],
        "device.result_unit_choices": [dict(value=u, text=u) for u in list(KD_RESULT_UNITS) + ([dev["result_user_unit"]] if dev["result_user_unit"] else [])] +
                                      [dict(value="__user__", text=">User-defined")],
        "device.method_choices": [dict(value=m["name"], text=m["name"]) for m in dev["methods"]],
        "device.sample_table": [dict(line=i + 1, **x) for i, x in enumerate(dev["table"])],
        "device.sample_table_stored": dev["stored_table"] is not None,
        "device.common_variables": list(dev["cvs"]),
        "device.stirrer": dict(dev["stirrer"]),
        "device.conditioning": dict(phase=c["phase"], ok=c["phase"] == "ok", drift_ug_min=drift if c["phase"] in ("busy", "ok") else None,
                                    measured_mv=_kd_mv(dev, True) if c["phase"] in ("busy", "ok") and _kd_cond_ready(s) else None,
                                    since_s=c["since"]),
        "device.run": live,
        "device.clock": dict(date=_kd_now_text(dev, t)[:10].replace("-", ":"), time=_kd_now_text(dev, t)[11:]),
        "device.result": None if dev["result"] is None else dict(_kd_copy.deepcopy(dev["result"]),
                                                                 curve_xy=[[m["time_s"], m["water_ug"]] for m in dev["result"]["curve"]]),
        "device.result_seq": dev["result_seq"],
        "device.statistics": _kd_stats(dev),
        "device.measuring_points": _kd_copy.deepcopy(dev["mp"]),
        "device.beeps": dev["beeps"],
        "device.message": None if dev["message"] is None else dict(code=dev["message"], text=KD_ERRORS.get(dev["message"], dev["message"]),
                                                                   seq=dev["message_seq"]),
    }


KD_SLOTS = {   # id: (schema, meaning)
    "device.status": ({"type": "string", "enum": list(KD_STATUS_TEXT)}, "对话框右上角状态：off/boot/init/ready/cond.busy/cond.ok/busy/hold（p.46）"),
    "device.status_text": ({"type": "string"}, "标题栏右上角显示的状态文字"),
    "device.datetime": ({"type": "string"}, "设备时钟（System > Settings的Time/Date）"),
    "device.clock": ({"type": "object"}, "设置对话框显示的日期YYYY:MM:DD与时间hh:mm:ss（p.72）"),
    "device.settings": ({"type": "object"}, "系统设置、外部设备与Control对话框设置（第6章、p.53）"),
    "device.method": ({"type": "object"}, "当前方法：名称、模板、第7章参数（含CHEM-102 settings映射的启动漂移、萃取时间与搅拌档）、计算R1-R5"),
    "device.methods": ({"type": "array", "items": {"type": "object"}}, "方法表中保存的方法（最多100个）"),
    "device.sample": ({"type": "object"}, "主对话框的样品数据ID1、ID2、样品量与单位（p.54-55）"),
    "device.units": ({"type": "array", "items": {"type": "string"}}, "样品量单位选择表（含用户自定义单位）"),
    "device.result_units": ({"type": "array", "items": {"type": "string"}}, "结果单位选择表（含用户自定义单位）"),
    "device.unit_choices": ({"type": "array", "items": {"type": "object"}}, "样品量单位选择列表的行（末行>User-defined）"),
    "device.result_unit_choices": ({"type": "array", "items": {"type": "object"}}, "结果单位选择列表的行（末行>User-defined）"),
    "device.method_choices": ({"type": "array", "items": {"type": "object"}}, "样品表Method选择列表的行（保存的方法）"),
    "device.sample_table": ({"type": "array", "items": {"type": "object"}}, "样品表的行（最多99行，p.56-58）"),
    "device.sample_table_stored": ({"type": "boolean"}, "内部存储器中是否有保存的样品表"),
    "device.common_variables": ({"type": "array", "items": {"anyOf": [{"type": "number"}, {"type": "null"}]}}, "公共变量CV01-CV05（null=invalid）"),
    "device.stirrer": ({"type": "object"}, "搅拌器实际开关与档位（-15..15；8约1000 rpm）"),
    "device.conditioning": ({"type": "object"}, "调节状态：busy/ok、测得漂移µg/min、测量值mV（理想指示电极）"),
    "device.run": ({"anyOf": [{"type": "object"}, {"type": "null"}]}, "进行中的测定：加样倒计时、样品数据请求、暂停、滴定实时值与曲线"),
    "device.result": ({"anyOf": [{"type": "object"}, {"type": "null"}]}, "最近一次测定的结果对话框内容（R1-R5、漂移、漂移校正时间、EP1、停止原因、计算变量）"),
    "device.result_seq": ({"type": "integer", "minimum": 0}, "结果序号（每次测定结束加1）"),
    "device.statistics": ({"type": "object"}, "统计序列：完成/目标次数、均值、s abs、s rel、各次明细"),
    "device.measuring_points": ({"type": "array", "items": {"type": "object"}}, "测量点列表（Time interval MP间隔，最多1000点）"),
    "device.beeps": ({"type": "integer", "minimum": 0}, "蜂鸣次数（测定结束、调节OK持续10 s；Beep=on时）"),
    "device.message": ({"anyOf": [{"type": "object"}, {"type": "null"}]}, "固件自行中止测定时的最近一条消息（代码、文字、序号）"),
}


def _kd_units(schema):
    """Every numeric input declares its unit ('1' when dimensionless), as the contract rules require."""
    if isinstance(schema, dict):
        if schema.get("type") in ("number", "integer") and "x-unit" not in schema:
            schema["x-unit"] = "1"
        for v in schema.values():
            _kd_units(v)
    elif isinstance(schema, list):
        for v in schema:
            _kd_units(v)
    return schema


def _kd_cap(desc, inputs, outputs, example, effects, kind="device_command", optional=()):
    _kd_units(inputs)
    return dict(description_zh=desc, input_schema={"type": "object", "properties": inputs, "required": [k for k in inputs if k not in optional],
                                                   "additionalProperties": False},
                output_schema={"type": "object", "properties": outputs, "required": list(outputs), "additionalProperties": False},
                example_arguments=example, effects=effects, kind=kind)


_KD_STR = {"type": "string"}
_KD_NAME = {"type": "string", "minLength": 1, "maxLength": 12}
_KD_SIZE = {"type": "number", "minimum": -999999999, "maximum": 9999999999}
_KD_LINE = {"type": "integer", "minimum": 1, "maximum": KD_MAX_LINES}
_KD_SAMPLE_PROPS = dict(id1={"type": "string", "maxLength": 10}, id2={"type": "string", "maxLength": 10}, size=_KD_SIZE,
                        unit={"type": "string", "minLength": 1, "maxLength": 12})
_KD_RUN = ["device.status", "device.conditioning", "device.run", "device.stirrer", "device.result", "device.result_seq", "device.statistics",
           "device.measuring_points", "device.sample", "device.sample_table", "device.common_variables", "coulometry.running",
           "coulometry.status", "measurement.elapsed_s", "measurement.charge_c", "measurement.water_mass_kg", "measurement.water_fraction",
           "settings.current_a", "settings.drift_compensation_kg_s", "settings.sample_mass_kg", "settings.endpoint_hold_s"]
KD_CAPS = {
    "run.start": _kd_cap("START键（远程$G）：未调节时开始调节；Conditioning OK时开始测定（加样8 s→样品数据请求→暂停→滴定）；请求对话框时继续；调节关闭时直接开始测定。",
                         {}, {"action": {"type": "string", "enum": ["conditioning", "determination", "continue"]}}, {}, _KD_RUN),
    "run.stop": _kd_cap("STOP键（远程$S）：取消进行中的测定（按已有数据计算结果，标记Manual stop）、停止调节或手动搅拌。", {},
                        {"stopped": {"type": "string", "enum": ["determination", "conditioning", "stirrer", "nothing"]}}, {}, _KD_RUN),
    "method.load_template": _kd_cap("方法表New：载入方法模板KFC / KFC-Blank / Blank（参数为默认值，只在计算公式上不同；删除统计数据）。",
                                    {"template": {"type": "string", "enum": list(KD_TEMPLATES)}}, {"name": _KD_STR}, {"template": "KFC"},
                                    ["device.method", "device.statistics", "settings.start_drift_threshold_kg_s", "settings.extraction_time_s",
                                     "settings.stirring_level", "settings.current_a"], "configuration"),
    "method.load": _kd_cap("方法表Load：载入保存的方法（删除统计数据）。", {"name": _KD_NAME}, {"name": _KD_STR}, {"name": "KFC"},
                           ["device.method", "device.statistics", "settings.start_drift_threshold_kg_s", "settings.extraction_time_s",
                            "settings.stirring_level", "settings.current_a"], "configuration"),
    "method.store": _kd_cap("方法表Store：以名称（最多12字符）保存当前方法；同名覆盖，最多100个。", {"name": _KD_NAME}, {"name": _KD_STR},
                            {"name": "MyKFC"}, ["device.methods", "device.method"], "configuration"),
    "method.delete": _kd_cap("方法表Delete：删除保存的方法。", {"name": _KD_NAME}, {"name": _KD_STR}, {"name": "MyKFC"}, ["device.methods"],
                             "configuration"),
    "method.configure": _kd_cap("Parameters菜单：修改当前方法的参数（调节、启动条件、控制参数、滴定参数、停止条件、统计、报告）；"
                                "启动漂移/萃取时间/搅拌档用coulometry.configure。", {"parameters": _kd_params_schema()},
                                {"changed": {"type": "array", "items": _KD_STR}}, {"parameters": {"stop_criterion": "drift", "stop_drift": 10}},
                                ["device.method", "device.conditioning", "settings.current_a"], "configuration"),
    "method.set_calculation": _kd_cap("Parameters > Calculation：编辑结果R1-R5（名称≤12字符、公式≤30字符并做语法检查、小数位0-5、结果单位、Save as CV）；"
                                      "Delete为名称与公式置空。",
                                      {"index": {"type": "integer", "minimum": 1, "maximum": 5}, "name": {"type": "string", "maxLength": 12},
                                       "formula": {"type": "string", "maxLength": 30}, "decimals": {"type": "integer", "minimum": 0, "maximum": 5},
                                       "unit": {"type": "string", "minLength": 0, "maxLength": 12}, "save_cv": {"type": "boolean"}},
                                      {"index": {"type": "integer"}}, {"index": 2, "name": "Water2", "formula": "EP1/C00/1000", "unit": "mg/g"},
                                      ["device.method", "device.result_units"], "configuration",
                                      optional=("name", "formula", "decimals", "unit", "save_cv")),
    "sample.set_data": _kd_cap("主对话框/样品数据请求：输入ID1、ID2、样品量与单位（测定运行中也可）；新单位名成为用户自定义单位。",
                               _KD_SAMPLE_PROPS, {"sample": {"type": "object"}}, {"size": 0.5012, "unit": "g"}, ["device.sample", "device.units"],
                               "configuration", optional=tuple(_KD_SAMPLE_PROPS)),
    "sample_table.set_line": _kd_cap("样品表Edit：编辑第line行（line=行数+1时追加，复制上一行数据）；method为空=当前方法。",
                                     dict(line=_KD_LINE, method={"anyOf": [_KD_NAME, {"type": "null"}]}, **_KD_SAMPLE_PROPS),
                                     {"line": {"type": "integer"}}, {"line": 1, "id1": "#8805923", "size": 1.0},
                                     ["device.sample_table", "device.units"], "configuration",
                                     optional=("method", "id1", "id2", "size", "unit")),
    "sample_table.delete_line": _kd_cap("样品表Delete：删除所选行。", {"line": _KD_LINE}, {"line": {"type": "integer"}}, {"line": 1},
                                        ["device.sample_table"], "configuration"),
    "sample_table.insert_line": _kd_cap("样品表Insert：在所选行上方插入新行。", {"line": _KD_LINE}, {"line": {"type": "integer"}}, {"line": 1},
                                        ["device.sample_table"], "configuration"),
    "sample_table.clear": _kd_cap("样品表New：删除整个样品表（只在ready状态）。", {}, {"lines": {"type": "integer"}}, {}, ["device.sample_table"],
                                  "configuration"),
    "sample_table.store": _kd_cap("样品表Store：保存到内部存储器（只能保存一个，覆盖）。", {}, {"lines": {"type": "integer"}}, {},
                                  ["device.sample_table_stored"], "configuration"),
    "sample_table.load": _kd_cap("样品表Load：从内部存储器载入。", {}, {"lines": {"type": "integer"}}, {}, ["device.sample_table"], "configuration"),
    "stirrer.set": _kd_cap("搅拌器（Live dialog的Stirrer、Manual control）：On/Off与Stir-/Stir+档位（-15..15）。",
                           {"on": {"type": "boolean"}, "rate": {"type": "integer", "minimum": -15, "maximum": 15}}, {"on": {"type": "boolean"},
                                                                                                                     "rate": {"type": "integer"}},
                           {"on": True, "rate": 8}, ["device.stirrer"], optional=("on", "rate")),
    "results.recalculate": _kd_cap("Results > Recalculate：用当前计算与样品数据重算最近一次测定（不可撤销）。", {}, {"results": {"type": "array",
                                                                                                                   "items": {"type": "object"}}},
                                   {}, ["device.result", "device.statistics", "device.common_variables"]),
    "statistics.reset": _kd_cap("Statistics > Reset：删除全部统计数据。", {}, {"done": {"type": "integer"}}, {}, ["device.statistics"]),
    "statistics.increase": _kd_cap("Statistics > Increase：测定序列增加一次（最多20次）。", {}, {"target": {"type": "integer"}}, {},
                                   ["device.statistics"]),
    "statistics.set_included": _kd_cap("Statistics > Details > On/Off：把第index次测定移出/放回统计（移出行标*）。",
                                       {"index": {"type": "integer", "minimum": 1, "maximum": KD_MAX_STATS}, "included": {"type": "boolean"}},
                                       {"index": {"type": "integer"}}, {"index": 1, "included": False}, ["device.statistics", "device.common_variables"]),
    "common_variable.set": _kd_cap("System > Common variables：编辑CV01-CV05的值；null为Delete（invalid）。",
                                   {"index": {"type": "integer", "minimum": 1, "maximum": 5}, "value": {"anyOf": [{"type": "number"}, {"type": "null"}]}},
                                   {"index": {"type": "integer"}}, {"index": 1, "value": 1.0472}, ["device.common_variables"], "configuration"),
    "settings.configure": _kd_cap("System > Settings / External devices / COM设置与Control对话框（样品表、自动启动）。", {"settings": _kd_settings_schema()},
                                  {"changed": {"type": "array", "items": _KD_STR}}, {"settings": {"beep": False}}, ["device.settings"], "configuration"),
    "clock.set": _kd_cap("System > Settings的Time（hh:mm:ss）与Date（YYYY-MM-DD）。",
                         {"date": {"type": "string", "pattern": r"^\d{4}[-:]\d{2}[-:]\d{2}$"}, "time": {"type": "string", "pattern": r"^\d{2}:\d{2}:\d{2}$"}},
                         {"datetime": _KD_STR}, {"date": "2026-10-19", "time": "09:30:00"}, ["device.datetime"], "configuration",
                         optional=("date", "time")),
    "system.initialize": _kd_cap("系统初始化（开机时按住STOP约10 s并在8 s内按两次BACK）：删除全部用户数据，恢复出厂设置，约80 s后自动重启。", {},
                                 {"initialized": {"type": "boolean"}}, {}, sorted(KD_SLOTS) + ["settings.start_drift_threshold_kg_s",
                                                                                                 "settings.extraction_time_s", "settings.stirring_level",
                                                                                                 "settings.current_a"], "configuration"),
}


class _KDReject(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _kd_require_on(s, dev, t):
    if not s["power.enabled"]:
        raise _KDReject("POWER_OFF")
    if _kd_status(s, dev, t) in ("boot", "init"):
        raise _KDReject("NOT_READY")


def _kd_busy(s, dev):
    return dev["run"] is not None or s["coulometry.running"]


# ---------------------------------------------------------------- panel host, runtime and session
def _kd_panel_engine():
    try:
        from .asset_runtime import _panel_ui as engine                      # imported as scripts.basic40_runtime_measure
        return engine
    except ImportError:
        pass
    try:
        from asset_runtime import _panel_ui as engine                       # scripts/ on sys.path (tests)
        return engine
    except ImportError:
        import importlib.util
        import sys
        if "_chemsim_panel_ui" not in sys.modules:
            spec = importlib.util.spec_from_file_location("_chemsim_panel_ui", _kd_Path(__file__).with_name("asset_runtime") / "_panel_ui.py")
            sys.modules["_chemsim_panel_ui"] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules["_chemsim_panel_ui"])
        return sys.modules["_chemsim_panel_ui"]


_kd_pu = _kd_panel_engine()
METROHM899_PANEL_PATH = METROHM899_ASSET_DIR / "panel_ui.json"
_KD_LEGACY_CAPS = tuple(export_metrohm899_contract_metadata()["capabilities"])
_KD_TEMPLATE_CAPS = METROHM899_TEMPLATE_CAPABILITIES
METROHM899_PANEL_SPEC = (_kd_pu.load_spec(METROHM899_PANEL_PATH, capabilities=set(KD_CAPS) | set(_KD_LEGACY_CAPS))
                         if METROHM899_PANEL_PATH.is_file() and not _kd_os.environ.get("METROHM899_PANEL_BUILD") else None)
METROHM899_PANEL_CAPS = _kd_pu.capability_definitions(METROHM899_PANEL_SPEC) if METROHM899_PANEL_SPEC else {}


class _KDPanelHost:
    """Panel-host protocol of one instance: the agent-visible slots and host values the dialog firmware shows, the command path, the clock."""

    def __init__(self, host, instance_id):
        self.host, self.iid = host, instance_id
        self.n = 0

    def panel_read(self):
        h = self.host
        inst = h._kf899_instances[self.iid]
        s = inst["state"]
        out = {k: _kd_copy.deepcopy(v) for k, v in s.items() if not k.startswith("truth.")}
        out.update(_kd_public(s, inst["device"], h.time_s))
        out.update({"host.powered": s["power.enabled"], "host.power_epoch": inst["device"]["power_epoch"], "host.usb_stick": False,
                    "host.printer": False, "host.serial_number": "1899000001", "host.program_version": KD_PROGRAM_VERSION})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A command of a panel gesture runs as the session that made the gesture (host._kd_caller, set by Metrohm899DeviceSession._kd_panel)."""
        self.n += 1
        who = getattr(self.host, "_kd_caller", None)
        session = self.host._kf899_panel_session if who is None else Metrohm899DeviceSession(self.host, who)
        r = session.invoke(instance_id=self.iid, capability_id=capability_id, arguments=dict(arguments), request_id=f"panel-{self.iid}-{self.n}")
        if r["status"] != "succeeded":
            raise _kd_pu.PanelRejected(r["error"]["code"])
        return r["result"]

    def panel_time(self):
        return self.host.time_s


class Metrohm899DeviceHost(Metrohm899Host):
    """CHEM-102 host with the 899 dialog firmware (inst['device']) and the front panel of each instance.  The trusted world: it advances the
    clock (step) and loads sample fixtures; the device's clock-driven behaviour (conditioning, determination phases, measuring points) is
    evaluated at its own event times inside a host step."""

    def __init__(self, scene_adapter=None):
        super().__init__(scene_adapter)
        self._kf899_panels = {}
        self.panel_capture_dir = None
        self._kf899_principals["__panel__"] = {"visibility": "agent", "instances": set()}
        self._kf899_principals["__firmware__"] = {"visibility": "agent", "instances": set()}
        self._kf899_panel_session = Metrohm899DeviceSession(self, "__panel__")
        self._kf899_firmware = Metrohm899Session(self, "__firmware__")     # the firmware's own path to the CHEM-102 commands
        self._kf899_fw_n = 0

    def create_instance(self, instance_id):
        with self._kf899_lock:
            super().create_instance(instance_id)
            for p in ("__panel__", "__firmware__"):
                self._kf899_principals[p]["instances"].add(instance_id)
            inst = self._kf899_instances[instance_id]
            inst["device"] = _kd_new_device()
            self._kd_apply_legacy(inst, dict(start_drift_threshold_kg_s=20e-9 / 60, extraction_time_s=0.0, stirring_level=8, current_a=0.4))
            if METROHM899_PANEL_SPEC is not None:
                self._kf899_panels[instance_id] = _kd_pu.PanelUI(METROHM899_PANEL_SPEC, _KDPanelHost(self, instance_id))
            return instance_id

    def panel(self, instance_id):
        return self._kf899_panels[instance_id]

    def session(self, principal_id):
        if principal_id not in self._kf899_principals or principal_id.startswith("__"):
            raise ValueError("Unregistered principal")
        return Metrohm899DeviceSession(self, principal_id)

    # the firmware calling the CHEM-102 commands (the same checked path as a direct call)
    def _kd_fw(self, instance_id, cap, **args):
        self._kf899_fw_n += 1
        r = self._kf899_firmware.invoke(instance_id=instance_id, capability_id=cap, arguments=args, request_id=f"fw-{self._kf899_fw_n}")
        if r["status"] != "succeeded":
            raise _KDReject(r["error"]["code"])
        return r["result"]

    def _kd_apply_legacy(self, inst, values):
        """Set CHEM-102 settings slots at creation / initialization (no request; the instance is idle)."""
        for k, v in values.items():
            inst["state"]["settings." + k] = v

    def load_sample(self, *, instance_id, water_mass_kg, background_drift_kg_s, reagent_ready, request_id):
        """CHEM-102 fixture; with Automatic start on and Conditioning OK a new sample with water starts the determination when the voltage jump
        it causes on the titration curve exceeds the Threshold value (p.84)."""
        with self._kf899_lock:
            result = super().load_sample(instance_id=instance_id, water_mass_kg=water_mass_kg, background_drift_kg_s=background_drift_kg_s,
                                         reagent_ready=reagent_ready, request_id=request_id)
            inst = self._kf899_instances[instance_id]
            dev = inst.get("device")
            if dev is not None and not request_id.startswith("__restage"):
                _kd_cond_update(inst["state"], dev, self.time_s)
                p = dev["method"]["params"]
                jump = (_kd_curve_mv(p, water_mass_kg * KD_UG_PER_KG + _kd_endpoint_offset_ug(p)) - p["endpoint"]) if p["endpoint"] is not None \
                    else 0.0                                                # the voltage jump of the injected water (p.84)
                if dev["cond"]["phase"] == "ok" and p["automatic_start"] and jump > p["threshold"] and dev["run"] is None:
                    self._kd_begin_run(instance_id, inst, skip_add=True)
            return result

    # ---- clock
    def step(self, dt_s):
        """Advance the common clock: the CHEM-102 integration between the device's event times, the device logic at each of them."""
        with self._kf899_lock:
            if not _kf899_number(dt_s) or dt_s < 0 or not _kf899_number(self.time_s + dt_s):
                raise ValueError("Finite nonnegative time increment required")
            end = self.time_s + float(dt_s)
            self._kd_tick()
            while self.time_s < end - 1e-12:
                nxt = min([e for iid in self._kf899_instances for e in self._kd_events(iid) if e > self.time_s + 1e-9] + [end])
                super().step(nxt - self.time_s if nxt < end else end - self.time_s)
                if nxt >= end:
                    self.time_s = end
                self._kd_tick()
            return {"time_s": self.time_s, "synthetic": True}

    def _kd_events(self, iid):
        inst = self._kf899_instances[iid]
        dev = inst.get("device")
        if dev is None or not inst["state"]["power.enabled"]:
            return []
        out = _kd_cond_events(inst["state"], dev)
        for k in ("boot_until", "init_until"):
            if dev[k] is not None:
                out.append(dev[k])
        run = dev["run"]
        if run is not None:
            if run["phase"] == "add":
                out.append(run["add_until"])
            if run["phase"] == "pause":
                out.append(run["pause_until"])
            if run["phase"] == "titration":
                if run["next_mp"] is not None:
                    out.append(run["next_mp"])
                if run["stop_at"] is not None:
                    out.append(run["stop_at"])
        return out

    def _kd_tick(self):
        t = self.time_s
        for iid in list(self._kf899_instances):
            inst = self._kf899_instances[iid]
            dev = inst.get("device")
            if dev is None or not inst["state"]["power.enabled"]:
                continue
            before = _kd_copy.deepcopy(dev)
            if dev["init_until"] is not None and t >= dev["init_until"] - 1e-9:
                dev["init_until"] = None
                dev["power_epoch"] += 1                                     # automatic restart after the initialization (p.109)
                dev["boot_until"] = t + KD_BOOT_S
            if dev["boot_until"] is not None and t >= dev["boot_until"] - 1e-9:
                dev["boot_until"] = None
            _kd_cond_update(inst["state"], dev, t)
            for phase, act in (("add", self._kd_after_add), ("pause", self._kd_begin_titration), ("titration", self._kd_titration_tick)):
                run = self._kf899_instances[iid]["device"]["run"]          # firmware calls replace the instance record: read it again
                due = run is not None and run["phase"] == phase and (phase == "titration" or t >= run[phase + "_until"] - 1e-9)
                if due:
                    act(iid, self._kf899_instances[iid])
            inst = self._kf899_instances[iid]
            dev = inst["device"]
            if dev["run"] is None and dev["autostart_pending"] and (dev["cond"]["phase"] == "ok" or not dev["method"]["params"]["conditioning"]):
                dev["autostart_pending"] = False
                try:
                    self._kd_begin_run(iid, inst)
                except _KDReject as exc:
                    self._kd_message(self._kf899_instances[iid]["device"], exc.code)
            inst = self._kf899_instances[iid]
            if inst["device"] != before:
                inst["revision"] += 1

    # ---- determination sequence (p.59-62)
    def _kd_begin_run(self, iid, inst, skip_add=False):
        s, dev, t = inst["state"], inst["device"], self.time_s
        p = dev["method"]["params"]
        if p["electrode_test"] and not s["slot.indicator.installed"]:
            raise _KDReject("ELECTRODE_TEST")
        sample, line = dict(dev["sample"]), None
        if dev["settings"]["control.sample_table"] and dev["table"]:
            row = dev["table"][0]
            if row["method"] and row["method"] != dev["method"]["name"]:
                inst = self._kd_load_method(iid, inst, row["method"])          # the line's method is loaded for its determination
                s, dev, p = inst["state"], inst["device"], inst["device"]["method"]["params"]
            sample, line = {k: row[k] for k in ("id1", "id2", "size", "unit")}, 1
            dev["sample"] = dict(sample)
        st = dev["stats"]
        if p["statistics"] and len(st["rows"]) >= st["target"]:
            st["rows"] = []                                                 # a complete series is deleted by the next start (p.68)
        dev["cond"] = dict(phase="off", since=None, below_since=None, ok_since=None, beeped=False)
        dev["run"] = dict(phase="add", t_start=t, add_until=t, pause_until=None, t_titr=None, next_mp=None, stop_at=None, request_open=False,
                          sample=sample, line=line, drift_ug_min=_kd_drift_ug_min(s), mim_mv=_kd_mv(dev, True))
        if p["stirrer"]:
            dev["stirrer"] = dict(on=True, rate=s["settings.stirring_level"])
        dev["mp"] = []
        if skip_add:
            self._kd_after_add(iid, inst)
        else:
            dev["run"]["add_until"] = t + KD_ADD_SAMPLE_S

    def _kd_after_add(self, iid, inst):
        dev = inst["device"]
        p, run = dev["method"]["params"], dev["run"]
        if p["request_id"] != "off" or p["request_size"] or p["request_unit"]:
            run["request_open"] = True
            if p["hold_at_request"]:
                run["phase"] = "request"
                return
        self._kd_begin_pause(iid, inst)

    def _kd_begin_pause(self, iid, inst):
        dev = inst["device"]
        run = dev["run"]
        run["phase"], run["pause_until"] = "pause", self.time_s + dev["method"]["params"]["pause"]
        if dev["method"]["params"]["pause"] <= 0:
            self._kd_begin_titration(iid, inst)

    def _kd_begin_titration(self, iid, inst):
        s, dev, t = inst["state"], inst["device"], self.time_s
        p, run = dev["method"]["params"], dev["run"]
        try:
            if s["sample.present"] and (inst["curve"] or s["measurement.sample_time_s"] is not None):
                dev["restage"] += 1                                          # no new sample fixture: the cell's water is the drift only
                Metrohm899Host.load_sample(self, instance_id=iid, water_mass_kg=0.0, background_drift_kg_s=s["truth.background_drift_kg_s"],
                                           reagent_ready=s["reagent.ready"], request_id=f"__restage-{dev['restage']}")
                inst = self._kf899_instances[iid]
                s, dev = inst["state"], inst["device"]
                run = dev["run"]
            drift = _kd_drift_ug_min(s)
            comp = {"auto": drift or 0.0, "manual": p["drift_value"], "off": 0.0}[p["drift_correction"]]
            water = s["truth.water_remaining_kg"] or 0.0
            offset_kg = _kd_endpoint_offset_ug(p) / KD_UG_PER_KG                # the endpoint of the curve of I(pol) (p.95-96)
            extra = _kd_control_extra_s(p, _kd_current_a(p), max(0.0, water - offset_kg) * KD_UG_PER_KG)
            self._kd_fw(iid, "coulometry.configure", current_a=_kd_current_a(p), drift_compensation_kg_s=comp / KD_UG_MIN_PER_KG_S,
                        sample_mass_kg=_kd_sample_mass_kg(run["sample"]), endpoint_hold_s=_kd_hold_s(p, drift, p["stop_time"]) + extra)
            self._kd_fw(iid, "coulometry.start")
            if offset_kg:                                                      # titrated to the controller's endpoint, not to the true one
                st = self._kf899_instances[iid]["state"]
                st["truth.water_remaining_kg"] = max(0.0, water - offset_kg)
                run = self._kf899_instances[iid]["device"]["run"]
                run["water_left_kg"] = min(water, max(0.0, offset_kg))
        except _KDReject as exc:
            inst = self._kf899_instances[iid]
            dev = inst["device"]
            dev["run"], dev["stirrer"] = None, dict(dev["stirrer"], on=False)
            self._kd_message(dev, exc.code)
            return
        inst = self._kf899_instances[iid]
        dev = inst["device"]
        run = dev["run"]
        run.update(phase="titration", t_titr=t, next_mp=t + p["mp_interval"], stop_at=None if p["stop_time"] is None else t + p["stop_time"],
                   drift_ug_min=_kd_drift_ug_min(inst["state"]), drift_mode=p["drift_correction"],
                   mdc_ug_min={"auto": _kd_drift_ug_min(inst["state"]) or 0.0, "manual": p["drift_value"], "off": 0.0}[p["drift_correction"]])
        dev["mp"] = [self._kd_mp(inst, 0.0)]

    @staticmethod
    def _kd_message(dev, code):
        """A message the firmware shows on its own (a determination it had to abandon)."""
        dev["message"], dev["message_seq"] = code, dev["message_seq"] + 1

    def _kd_mp(self, inst, el):
        s, dev = inst["state"], inst["device"]
        water = s["measurement.charge_c"] * KF899_KG_PER_COULOMB * KD_UG_PER_KG
        titrating = s["coulometry.status"] in ("TITRATING", "EXTRACTING")
        rate = s["settings.current_a"] * KF899_KG_PER_COULOMB * KD_UG_MIN_PER_KG_S if s["coulometry.status"] == "TITRATING" else _kd_drift_ug_min(s)
        return dict(index=len(dev["mp"]) + 1, time_s=round(el, 6), water_ug=water, mv=_kd_mv(dev, not titrating, s["truth.water_remaining_kg"]),
                    rate_ug_min=rate)

    def _kd_titration_tick(self, iid, inst):
        s, dev, t = inst["state"], inst["device"], self.time_s
        run, p = dev["run"], dev["method"]["params"]
        while run["next_mp"] is not None and t >= run["next_mp"] - 1e-9 and len(dev["mp"]) < KD_MAX_MP and \
                run["next_mp"] - run["t_titr"] <= s["measurement.elapsed_s"] + 1e-9:     # no points after the end inside a step
            dev["mp"].append(self._kd_mp(inst, run["next_mp"] - run["t_titr"]))
            run["next_mp"] += p["mp_interval"]
        if len(dev["mp"]) >= KD_MAX_MP:
            run["next_mp"] = None
        reason = None
        if s["coulometry.status"] == "COMPLETED":
            reason = "Regular stop"
        elif s["coulometry.status"] == "STOPPED" or not s["coulometry.running"]:
            reason = "Manual stop"
        elif run["stop_at"] is not None and t >= run["stop_at"] - 1e-9:
            self._kd_fw(iid, "coulometry.stop")
            reason = "Stop time"
        if reason is None:
            return
        inst = self._kf899_instances[iid]
        if inst["device"]["run"]["request_open"]:                            # the request stays until START, then the results (p.55)
            inst["device"]["run"].update(phase="done", end_reason=reason)
        else:
            self._kd_finish(iid, inst, reason)

    def _kd_finish(self, iid, inst, reason):
        """End of a determination: results dialog values, calculations, statistics, common variables, beep, sample table, conditioning
        restarted in the background (p.61), autostart (p.53)."""
        s, dev = inst["state"], inst["device"]
        p, run = dev["method"]["params"], dev["run"]
        t_end = s["measurement.sample_time_s"] if s["measurement.sample_time_s"] is not None else self.time_s
        el = s["measurement.elapsed_s"]
        if dev["mp"] and abs(dev["mp"][-1]["time_s"] - el) > 1e-9 and len(dev["mp"]) < KD_MAX_MP:
            dev["mp"].append(dict(self._kd_mp(inst, el), mv=_kd_mv(dev, True)))
        r = dict(seq=dev["result_seq"] + 1, method=dev["method"]["name"], template=dev["method"]["template"], sample=dict(run["sample"]),
                 ep1_ug=(s["measurement.water_mass_kg"] or 0.0) * KD_UG_PER_KG, mcq_ug=s["measurement.charge_c"] * KF899_KG_PER_COULOMB * KD_UG_PER_KG,
                 mcd_s=el, mdc_ug_min=run.get("mdc_ug_min", 0.0), drift_mode=run.get("drift_mode", p["drift_correction"]), ddc_s=el,
                 mim_mv=run["mim_mv"], mit_c=p["temperature"], mcm_mv=_kd_mv(dev, True), mct_c=p["temperature"], dd_s=t_end - run["t_start"],
                 stop_reason=reason, time=_kd_now_text(dev, t_end), curve=_kd_copy.deepcopy(dev["mp"]))
        r["results"] = _kd_calculate(s, dev, r)
        if run.get("water_left_kg") is not None:                           # under-titration: the water short of the true endpoint stays in the cell
            s["truth.water_remaining_kg"] = run["water_left_kg"]
        dev["result"], dev["result_seq"], dev["run"] = r, r["seq"], None
        if p["statistics"] and reason == "Regular stop":
            dev["stats"]["target"] = max(dev["stats"]["target"], p["n_determinations"]) if dev["stats"]["rows"] else p["n_determinations"]
            vals = [None] * 5
            for x in r["results"]:
                vals[x["index"] - 1] = x["value"]
            dev["stats"]["rows"].append(dict(values=vals, size=r["sample"]["size"], unit=r["sample"]["unit"], included=True))
        _kd_assign_cvs(dev, r["results"])
        if dev["settings"]["beep"]:
            dev["beeps"] += 1
        if run.get("line") == 1 and dev["table"]:
            dev["table"].pop(0)                                             # the first line holds the running determination (p.64)
        dev["stirrer"] = dict(dev["stirrer"], on=False)
        if p["conditioning"]:
            _kd_cond_start(s, dev, self.time_s)
        st = dev["settings"]
        if st["control.autostart"] and reason == "Regular stop":           # 'table': as many determinations as sample-table lines (p.53)
            dev["autostarts_done"] += 1
            more = bool(dev["table"]) if st["control.autostarts"] is None else dev["autostarts_done"] < st["control.autostarts"]
            dev["autostart_pending"] = more
            if not more:
                dev["autostarts_done"] = 0

    def _kd_load_method(self, iid, inst, name):
        dev = inst["device"]
        m = _kd_template(name) if name in KD_TEMPLATES and not any(x["name"] == name for x in dev["methods"]) else \
            next((x for x in dev["methods"] if x["name"] == name), None)
        if m is None:
            raise _KDReject("NOT_FOUND")
        legacy = m.get("legacy") or dict(start_drift_threshold_kg_s=20e-9 / 60, extraction_time_s=0.0, stirring_level=8)
        self._kd_fw(iid, "coulometry.configure", current_a=_kd_current_a_plain(m["params"]), **legacy)
        inst = self._kf899_instances[iid]
        dev = inst["device"]
        dev["method"] = dict(name=m["name"], template=m["template"], params=_kd_copy.deepcopy(m["params"]), calcs=_kd_copy.deepcopy(m["calcs"]))
        dev["stats"] = dict(target=m["params"]["n_determinations"], rows=[])          # a new method deletes the statistics (p.68)
        return inst

    def _capture(self, instance_id, display_id):
        """Close-up camera of the display: a PNG file named by its content (large data as a file reference)."""
        data, w, h = self._kf899_panels[instance_id].png_bytes(display_id)
        sha = _kd_hashlib.sha256(data).hexdigest()
        d = _kd_Path(self.panel_capture_dir) if self.panel_capture_dir else _kd_Path(_kd_tempfile.gettempdir()) / "chemistry_sim_panel" / instance_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{display_id}_{sha[:16]}.png"
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.time_s, image=dict(path=str(path), format="png", width=w, height=h, sha256=sha))


def _kd_current_a_plain(p):
    return 0.4 if p["generator_current"] == "auto" else KD_CURRENTS[p["generator_current"]]


def export_metrohm899_device_metadata():
    """CHEM-102 metadata plus the dialog firmware and front panel: device state slots, device_capabilities, panel_capabilities, limitations.
    The CHEM-102 'capabilities' keep their schemas and meaning."""
    meta = export_metrohm899_contract_metadata()
    meta["asset_version"] = METROHM899_ASSET_VERSION
    for sid, (schema, meaning) in KD_SLOTS.items():
        meta["state_slots"][sid] = {"unit": "1", "origin": "derived", "value_schema": schema, "visibility": "agent", "access": "read_only",
                                    "write_via": None, "read_binding": "Metrohm899DeviceSession.read(instance_id=..., state_ids=[state_id])",
                                    "reference_conditions": None, "meaning_zh": meaning,
                                    "sampling": {"mode": "instant", "time_domain": "simulation", "window": None}}
    every = sorted({e for c in KD_CAPS.values() for e in c["effects"]} | set(_KD_RUN))
    caps = {}
    for name, c in KD_CAPS.items():
        caps[name] = {"input_schema": c["input_schema"], "output_schema": c["output_schema"], "example_arguments": [c["example_arguments"]],
                      "meaning_zh": c["description_zh"], "kind": c["kind"], "visibility": "agent", "availability": "implemented",
                      "state_effects": [{"state_id": sid, "meaning_zh": c["description_zh"], "timing": "immediate"} for sid in c["effects"]],
                      "observes": [x for x in c["effects"] if x.startswith("device.")][:6],
                      "binding": {"backend": "python", "artifact_id": "runtime",
                                  "invocation": f"Metrohm899DeviceSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"}}
    meta["device_capabilities"] = caps
    panel = {}
    for name, d in METROHM899_PANEL_CAPS.items():
        gesture = name not in ("panel.capture", "panel.frame")
        panel[name] = {"input_schema": d["input_schema"], "output_schema": d["output_schema"], "example_arguments": [d["example_arguments"]],
                       "meaning_zh": d["function_zh"], "kind": d["kind"], "visibility": d["visibility"], "availability": "implemented",
                       "component_ids": d["component_ids"],
                       "state_effects": [{"state_id": sid, "meaning_zh": "手势调用的命令改变的量", "timing": "immediate"} for sid in (every if gesture else [])],
                       "observes": [], "binding": {"backend": "python", "artifact_id": "runtime",
                                                   "invocation": f"Metrohm899DeviceSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"}}
    meta["panel_capabilities"] = panel
    meta["parameters"].update({
        "boot_s": {"value": KD_BOOT_S, "unit": "s", "evidence": "engineering_assumption", "source": "p.45 'takes some time'"},
        "initialization_s": {"value": KD_INIT_S, "unit": "s", "evidence": "public_document", "source": "manual p.109"},
        "add_sample_s": {"value": KD_ADD_SAMPLE_S, "unit": "s", "evidence": "public_document", "source": "manual p.60"},
        "rotational_speed_rpm_per_step": {"value": 125, "unit": "rpm", "evidence": "public_document", "source": "manual p.102 (internal stirrer)"},
        "titration_rate_sets": {"value": {k: dict(dynamics_mv=v[0], max_rate_ug_min=v[1], min_rate_ug_min=v[2]) for k, v in KD_RATES.items()},
                                "unit": "1", "evidence": "public_document", "source": "manual p.87 table 3 (max rate null = maximum)"},
        "device_clock_epoch": {"value": KD_EPOCH.isoformat(), "unit": "1", "evidence": "engineering_assumption", "source": "set with clock.set"},
    })
    meta["boundaries"] += METROHM899_DEVICE_LIMITATIONS
    meta["host_capabilities"]["sim.step"]["invocation"] = "Metrohm899DeviceHost.step(dt_s)"
    return meta


METROHM899_DEVICE_LIMITATIONS = [
    "Dialog firmware (Metrohm899DeviceHost): methods, method parameters, conditioning, the determination sequence, calculations, statistics, "
    "common variables, sample data and table, stirrer and system settings follow the 899 operating manual as a minimal device model; it is "
    "not Metrohm firmware.",
    "Ideal chemistry, indicator titration curve: the measured drift is the host's background drift; conditioning is OK once it stays below "
    "Start drift for the stabilizing time.  The indicator voltage follows a linear titration curve through the equivalence voltage of I(pol) "
    "(50 / 100 / 150 mV at 10 / 20 / 30 uA, p.96; 25 mV at 5 uA assumed) rising by the Table 7 dynamics (70 / 100 / 120 mV) per 50 ug of "
    "water (slope assumed), limited to +-1250 mV; the controller stops where the curve crosses 'Endpoint at', so a mismatched Endpoint / "
    "I(pol) leaves water (under-titration, the water stays in the cell) or adds an iodine excess (over-titration).  Inside the control range "
    "(Endpoint + Dynamics) the generation falls linearly from the maximum rate to Min. rate; the extra time is added to the endpoint phase "
    "(same charge and duration as the controlled approach).  Max. rate limits the generator current to the largest 100 / 200 / 400 mA step "
    "below it; 'auto' current is 400 mA.  Automatic start needs the voltage jump of the injected water on that curve to exceed the "
    "Threshold value.  The background drift is constant, so rel. drift is reached at the endpoint.",
    "Stop criteria map to the CHEM-102 endpoint hold: rel. drift stops at the endpoint, drift when the background drift is at most Stop drift "
    "(else at Stop time), time after Delay time, off at Stop time; a determination started without a new host sample fixture titrates an "
    "empty cell (0 kg sample water). Extraction time is the CHEM-102 delay before iodine generation; parameters written through "
    "coulometry.configure cannot be changed while a titration runs (live modification limited to the firmware's own parameters).",
    "Printer, USB flash drive (export, file management, backup, software update, PC/LIMS report), balance, barcode reader, USB keyboard / "
    "mouse, RS-232 and remote lines need hardware absent from the simulation: their settings are kept, the actions show the device's "
    "response (020-511 Action not possible for the USB drive; no output for reports). Diagnosis functions are for Metrohm service personnel.",
    "Not modelled: a second dialog language (needs installation by specialist personnel), key-press beeps (the end-of-determination and "
    "conditioned-for-10-s beeps are counted), the temperature input (MIT / MCT use the method's manual temperature), EP2-EP9.",
]


_KD_RUN_CHANGES = ("run.stop", "coulometry.stop", "sample.set_data", "stirrer.set")


class Metrohm899DeviceSession(Metrohm899Session):
    """CHEM-102 session plus the dialog firmware commands, the device slots and panel.*; same envelopes, idempotency and errors as CHEM-102.
    Conditioning and determinations belong to the caller that started them (run.start / coulometry.start): another caller's stop, power-off,
    Continue, sample data or stirrer command is refused with ACCESS_DENIED (lead rule 2026-10-19); the evaluator / debug identities are unrestricted."""

    def _kd_active(self, inst):
        dev = inst["device"]
        return dev["run"] is not None or dev["cond"]["phase"] != "off" or inst["state"]["coulometry.running"]

    def _kd_foreign(self, inst, cap, args):
        """Another caller's conditioning / determination is running and cap would stop, power off or change it."""
        owner = inst["device"].get("owner")
        if not self._kd_active(inst) or owner in (None, self._kf899_principal) or self._kf899_principal in ("__panel__", "__firmware__") or \
                self._kf899_host._kf899_principals[self._kf899_principal]["visibility"] != "agent":
            return False                                                      # the firmware's own timers (no gesturing caller) are not foreign
        if cap == "power.set":
            return isinstance(args, dict) and args.get("enabled") is False
        if cap == "run.start":
            run = inst["device"]["run"]
            return run is not None and run["request_open"]                       # Continue (sample data request) of that determination
        if cap == "sample.set_data":
            return inst["device"]["run"] is not None                             # the sample data of that determination
        return cap in _KD_RUN_CHANGES

    def describe(self, *, asset_type_id=None, instance_id=None):
        out = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if "capabilities" not in out:
            return out
        meta = export_metrohm899_device_metadata()
        evaluator = self._kf899_host._kf899_principals[self._kf899_principal]["visibility"] != "agent"
        out["capabilities"].update(_kd_copy.deepcopy(meta["device_capabilities"]))
        out["capabilities"].update({k: _kd_copy.deepcopy(v) for k, v in meta["panel_capabilities"].items() if v["visibility"] == "agent" or evaluator})
        out["state_slots"].update({k: v for k, v in meta["state_slots"].items() if k.startswith("device.")})
        out["asset_version"] = METROHM899_ASSET_VERSION
        out["boundaries"] = meta["boundaries"]
        return out

    def read(self, *, instance_id, state_ids=None):
        host = self._kf899_host
        with host._kf899_lock:
            if not self._kf899_allowed(instance_id) or "device" not in host._kf899_instances[instance_id]:
                return super().read(instance_id=instance_id, state_ids=state_ids)
            inst = host._kf899_instances[instance_id]
            dev_ids = [x for x in (state_ids or []) if isinstance(x, str) and x.startswith("device.")]
            if dev_ids and any(x not in KD_SLOTS for x in dev_ids):
                return self._kf899_response(instance_id, status="rejected", code="INVALID_ARGUMENT")
            legacy = None if state_ids is None else [x for x in state_ids if x not in dev_ids]
            r = super().read(instance_id=instance_id, state_ids=legacy) if legacy is None or legacy else self._kf899_response(instance_id)
            if r["status"] != "succeeded":
                return r
            values = _kd_public(inst["state"], inst["device"], host.time_s)
            for sid in (KD_SLOTS if state_ids is None else dev_ids):
                r["observations"][sid] = {"value": _kd_copy.deepcopy(values[sid]), "unit": "1", "quality": "valid", "sample_time_s": host.time_s,
                                          "sample_window_s": None}
            return r

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self._kf899_host
        with host._kf899_lock:
            inst = host._kf899_instances.get(instance_id) if isinstance(instance_id, str) else None
            if inst is None or "device" not in inst or not self._kf899_allowed(instance_id):
                return super().invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)
            key = (self._kf899_principal, instance_id, request_id)
            if key not in host._kf899_requests and self._kd_foreign(inst, capability_id, arguments):
                try:
                    fingerprint = _kd_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
                except (ValueError, TypeError, OverflowError):
                    return self._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
                response = self._kd_error(instance_id, request_id, "ACCESS_DENIED")
                if isinstance(request_id, str) and request_id:
                    host._kf899_requests[key] = (fingerprint, _kd_copy.deepcopy(response))
                return response
            was_active = self._kd_active(inst)
            if capability_id not in KD_CAPS and capability_id not in METROHM899_PANEL_CAPS:
                was_on = inst["state"]["power.enabled"]
                replay = key in host._kf899_requests
                response = super().invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)
                if response["status"] == "succeeded" and not replay:
                    self._kd_after_legacy(instance_id, capability_id, arguments, was_on)
                    if capability_id == "coulometry.start" and not was_active:
                        host._kf899_instances[instance_id]["device"]["owner"] = self._kf899_principal   # a direct titration belongs to its caller
                return response
            if not isinstance(request_id, str) or not request_id or not isinstance(arguments, dict):
                return self._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
            try:
                fingerprint = _kd_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            except (ValueError, TypeError, OverflowError):
                return self._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
            key = (self._kf899_principal, instance_id, request_id)
            if key in host._kf899_requests:
                previous = host._kf899_requests[key]
                if previous[0] != fingerprint:
                    return self._kf899_response(instance_id, request_id, "rejected", code="REQUEST_CONFLICT")
                return _kd_copy.deepcopy(previous[1])
            if capability_id in METROHM899_PANEL_CAPS:
                response = self._kd_panel(instance_id, capability_id, arguments, request_id)
            elif not _kd_check(KD_CAPS[capability_id]["input_schema"], arguments):
                response = self._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
            else:
                snapshot = _kd_copy.deepcopy(host._kf899_instances[instance_id])
                try:
                    result = self._kd_command(instance_id, capability_id, arguments)
                    inst = host._kf899_instances[instance_id]
                    if capability_id == "run.start" and not was_active:
                        inst["device"]["owner"] = self._kf899_principal               # conditioning / determination of this caller
                    inst["revision"] += 1
                    response = self._kf899_response(instance_id, request_id, result=result)
                except _KDReject as exc:
                    host._kf899_instances[instance_id] = snapshot              # refusals leave the instance unchanged
                    response = self._kd_error(instance_id, request_id, exc.code)
            host._kf899_requests[key] = (fingerprint, _kd_copy.deepcopy(response))
            host._kf899_logs.append({"instance_id": instance_id, "principal_id": self._kf899_principal, "request_id": request_id,
                                     "capability_id": capability_id, "status": response["status"]})
            return response

    def _kd_error(self, instance_id, request_id, code):
        r = self._kf899_response(instance_id, request_id, "rejected", code=code)
        if code in KD_ERRORS:
            r["error"]["message"] = KD_ERRORS[code]
        return r

    def _kd_after_legacy(self, iid, cap, args, was_on):
        """Firmware bookkeeping after a CHEM-102 command (their own effects are unchanged)."""
        host = self._kf899_host
        inst = host._kf899_instances[iid]
        s, dev, t = inst["state"], inst["device"], host.time_s
        if cap == "power.set":
            if args["enabled"] and not was_on:                             # switch-on: system test, then the main dialog (p.45)
                dev["power_epoch"] += 1
                dev["boot_until"] = t + KD_BOOT_S
            else:
                dev.update(run=None, autostart_pending=False, boot_until=None, init_until=None, stirrer=dict(dev["stirrer"], on=False),
                           cond=dict(phase="off", since=None, below_since=None, ok_since=None, beeped=False))
        elif cap == "coulometry.configure" and "current_a" in args and dev["run"] is None:
            dev["method"]["params"]["generator_current"] = f"{int(round(args['current_a'] * 1000))} mA"
        elif cap == "coulometry.start" and dev["run"] is None:
            dev["cond"] = dict(phase="off", since=None, below_since=None, ok_since=None, beeped=False)   # a direct titration ends conditioning
        _kd_cond_update(s, dev, t)

    def _kd_command(self, iid, cap, a):
        host = self._kf899_host
        inst = host._kf899_instances[iid]
        s, dev, t = inst["state"], inst["device"], host.time_s
        p = dev["method"]["params"]
        if not (cap == "system.initialize" and _kd_status(s, dev, t) == "boot"):   # initialization is confirmed at switch-on (p.108)
            _kd_require_on(s, dev, t)
        if cap == "run.start":
            run = dev["run"]
            if run is not None:
                if run["request_open"]:
                    run["request_open"] = False
                    if run["phase"] == "request":
                        host._kd_begin_pause(iid, inst)
                    elif run["phase"] == "done":
                        host._kd_finish(iid, inst, run["end_reason"])         # the sample data entered by now is used
                    return {"action": "continue"}
                raise _KDReject("BUSY")
            if s["coulometry.running"]:
                raise _KDReject("BUSY")
            if p["conditioning"]:
                if dev["cond"]["phase"] == "off":
                    _kd_cond_start(s, dev, t)
                    if p["stirrer"]:
                        dev["stirrer"] = dict(on=True, rate=s["settings.stirring_level"])
                    return {"action": "conditioning"}
                if dev["cond"]["phase"] != "ok":
                    raise _KDReject("COND_NOT_OK")
            host._kd_begin_run(iid, inst)
            return {"action": "determination"}
        if cap == "run.stop":
            run = dev["run"]
            if run is not None:
                if run["phase"] == "titration":
                    host._kd_fw(iid, "coulometry.stop")
                    inst = host._kf899_instances[iid]
                    host._kd_finish(iid, inst, "Manual stop")
                elif run["phase"] == "done":                               # already ended, only the request was open
                    host._kd_finish(iid, inst, run["end_reason"])
                else:
                    dev["run"], dev["stirrer"] = None, dict(dev["stirrer"], on=False)
                inst["device"]["cond"] = dict(phase="off", since=None, below_since=None, ok_since=None, beeped=False)
                inst["device"]["autostart_pending"] = False
                return {"stopped": "determination"}
            if s["coulometry.running"]:
                host._kd_fw(iid, "coulometry.stop")
                return {"stopped": "determination"}
            if dev["cond"]["phase"] != "off":
                dev["cond"] = dict(phase="off", since=None, below_since=None, ok_since=None, beeped=False)
                dev["stirrer"] = dict(dev["stirrer"], on=False)
                return {"stopped": "conditioning"}
            if dev["stirrer"]["on"]:
                dev["stirrer"] = dict(dev["stirrer"], on=False)
                return {"stopped": "stirrer"}
            return {"stopped": "nothing"}
        if cap in ("method.load_template", "method.load"):
            if _kd_busy(s, dev):
                raise _KDReject("BUSY")
            name = a.get("template") or a["name"]
            if cap == "method.load" and not any(x["name"] == name for x in dev["methods"]):
                raise _KDReject("NOT_FOUND")
            m = _kd_template(name) if cap == "method.load_template" else next(x for x in dev["methods"] if x["name"] == name)
            host._kd_fw(iid, "coulometry.configure", current_a=_kd_current_a_plain(m["params"]),
                        **(m.get("legacy") or dict(start_drift_threshold_kg_s=20e-9 / 60, extraction_time_s=0.0, stirring_level=8)))
            inst = host._kf899_instances[iid]
            dev = inst["device"]
            dev["method"] = dict(name=m["name"], template=m["template"], params=_kd_copy.deepcopy(m["params"]), calcs=_kd_copy.deepcopy(m["calcs"]))
            dev["stats"] = dict(target=m["params"]["n_determinations"], rows=[])
            _kd_cond_update(inst["state"], dev, t)
            return {"name": m["name"]}
        if cap == "method.store":
            if s["coulometry.running"] or dev["run"] is not None:
                raise _KDReject("BUSY")
            entry = dict(name=a["name"], template=dev["method"]["template"], params=_kd_copy.deepcopy(p), calcs=_kd_copy.deepcopy(dev["method"]["calcs"]),
                         legacy=dict(start_drift_threshold_kg_s=s["settings.start_drift_threshold_kg_s"], extraction_time_s=s["settings.extraction_time_s"],
                                     stirring_level=s["settings.stirring_level"]))
            i = next((k for k, x in enumerate(dev["methods"]) if x["name"] == a["name"]), None)
            if i is None:
                if len(dev["methods"]) >= KD_MAX_METHODS:
                    raise _KDReject("LIMIT")
                dev["methods"].append(entry)
            else:
                dev["methods"][i] = entry
            dev["method"]["name"] = a["name"]
            return {"name": a["name"]}
        if cap == "method.delete":
            i = next((k for k, x in enumerate(dev["methods"]) if x["name"] == a["name"]), None)
            if i is None:
                raise _KDReject("NOT_FOUND")
            dev["methods"].pop(i)
            return {"name": a["name"]}
        if cap == "method.configure":
            changed = []
            for k, v in a["parameters"].items():
                p[k] = v
                if k == "generator_electrode" and "generator_current" not in a["parameters"]:
                    p["generator_current"] = "auto" if v == "with diaphr." else "400 mA"     # its default follows the electrode (p.89)
                    changed.append("generator_current")
                changed.append(k)
            if ("generator_current" in changed) and p["generator_current"] != "auto" and not s["coulometry.running"]:
                host._kd_fw(iid, "coulometry.configure", current_a=KD_CURRENTS[p["generator_current"]])
                inst = host._kf899_instances[iid]
            if "n_determinations" in changed and not inst["device"]["stats"]["rows"]:
                inst["device"]["stats"]["target"] = p["n_determinations"]
            _kd_cond_update(inst["state"], inst["device"], t)
            return {"changed": changed}
        if cap == "method.set_calculation":
            if "formula" in a and not _kd_formula_ok(a["formula"]):
                raise _KDReject("INVALID_FORMULA")                         # the formula editor's automatic syntax check (p.49)
            if "unit" in a and a["unit"] not in KD_RESULT_UNITS:
                dev["result_user_unit"] = a["unit"] or None                  # a user-defined result unit (blank allowed, p.93)
            dev["method"]["calcs"][a["index"] - 1].update({k: v for k, v in a.items() if k != "index"})
            return {"index": a["index"]}
        if cap == "sample.set_data":
            if "unit" in a and a["unit"] not in KD_UNITS:
                dev["user_unit"] = a["unit"]                               # a user-defined unit replaces the previous one (p.55)
            dev["sample"].update(a)
            if dev["run"] is not None:
                dev["run"]["sample"].update(a)                             # the data entered by the end of the titration is used (p.62)
            return {"sample": dict(dev["sample"])}
        if cap.startswith("sample_table."):
            tab = dev["table"]
            if cap == "sample_table.set_line":
                line = a["line"]
                if line > len(tab) + 1:
                    raise _KDReject("NOT_FOUND")
                if line == len(tab) + 1:
                    if len(tab) >= KD_MAX_LINES:
                        raise _KDReject("LIMIT")
                    tab.append(dict(tab[-1]) if tab else dict(method=None, id1="", id2="", size=1.0, unit="g"))
                row = tab[line - 1]
                if a.get("method") is not None and a["method"] not in [x["name"] for x in dev["methods"]] + list(KD_TEMPLATES):
                    raise _KDReject("NOT_FOUND")
                if "unit" in a and a["unit"] not in KD_UNITS:
                    dev["user_unit"] = a["unit"]
                row.update({k: v for k, v in a.items() if k != "line"})
                if line == 1 and dev["run"] is not None and dev["run"].get("line") == 1:
                    dev["run"]["sample"].update({k: v for k, v in a.items() if k in ("id1", "id2", "size", "unit")})
                return {"line": line}
            if cap in ("sample_table.delete_line", "sample_table.insert_line"):
                if a["line"] > len(tab) + (1 if cap == "sample_table.insert_line" else 0) or (cap == "sample_table.delete_line" and not tab):
                    raise _KDReject("NOT_FOUND")
                if cap == "sample_table.delete_line":
                    tab.pop(a["line"] - 1)
                else:
                    if len(tab) >= KD_MAX_LINES:
                        raise _KDReject("LIMIT")
                    tab.insert(a["line"] - 1, dict(method=None, id1="", id2="", size=1.0, unit="g"))
                return {"line": a["line"]}
            if cap == "sample_table.clear":
                if _kd_status(s, dev, t) != "ready":
                    raise _KDReject("BUSY")                                 # New is offered only in ready status (p.57)
                n = len(tab)
                dev["table"] = []
                return {"lines": n}
            if cap == "sample_table.store":
                dev["stored_table"] = _kd_copy.deepcopy(tab)
                return {"lines": len(tab)}
            if dev["stored_table"] is None:
                raise _KDReject("NOT_FOUND")
            dev["table"] = _kd_copy.deepcopy(dev["stored_table"])
            return {"lines": len(dev["table"])}
        if cap == "stirrer.set":
            dev["stirrer"] = dict(on=a.get("on", dev["stirrer"]["on"]), rate=a.get("rate", dev["stirrer"]["rate"]))
            return dict(dev["stirrer"])
        if cap == "results.recalculate":
            r = dev["result"]
            if r is None:
                raise _KDReject("NO_RESULT")
            r["sample"] = dict(dev["sample"])                               # recalculated with the sample data now in the main dialog (p.67)
            r["results"] = _kd_calculate(s, dev, r)
            rows = dev["stats"]["rows"]
            if rows and dev["method"]["params"]["statistics"]:
                vals = [None] * 5
                for x in r["results"]:
                    vals[x["index"] - 1] = x["value"]
                rows[-1].update(values=vals, size=r["sample"]["size"], unit=r["sample"]["unit"])
            _kd_assign_cvs(dev, r["results"])
            return {"results": _kd_copy.deepcopy(r["results"])}
        if cap == "statistics.reset":
            dev["stats"] = dict(target=p["n_determinations"], rows=[])
            return {"done": 0}
        if cap == "statistics.increase":
            if dev["stats"]["target"] >= KD_MAX_STATS:
                raise _KDReject("LIMIT")
            dev["stats"]["target"] += 1
            return {"target": dev["stats"]["target"]}
        if cap == "statistics.set_included":
            rows = dev["stats"]["rows"]
            if a["index"] > len(rows):
                raise _KDReject("NOT_FOUND")
            rows[a["index"] - 1]["included"] = a["included"]
            if dev["result"] is not None:
                _kd_assign_cvs(dev, dev["result"]["results"])
            return {"index": a["index"]}
        if cap == "common_variable.set":
            dev["cvs"][a["index"] - 1] = None if a["value"] is None else float(a["value"])
            return {"index": a["index"]}
        if cap == "settings.configure":
            st = dict(dev["settings"])
            for k, v in a["settings"].items():
                if KD_SETTING[k].get("integer") and v is not None and type(v) is not int:
                    raise _KDReject("INVALID_ARGUMENT")
                st[k] = v
            dev["settings"] = st
            return {"changed": sorted(a["settings"])}
        if cap == "clock.set":
            now = KD_EPOCH + _kd_dt.timedelta(seconds=t + dev["clock_offset_s"])
            try:
                d = _kd_dt.date.fromisoformat(a["date"].replace(":", "-")) if "date" in a else now.date()   # entered as YYYY:MM:DD (p.72)
                hh, mm, ss = (int(x) for x in a["time"].split(":")) if "time" in a else (now.hour, now.minute, now.second)
                target = _kd_dt.datetime(d.year, d.month, d.day, hh, mm, ss)
            except ValueError:
                raise _KDReject("INVALID_VALUE") from None
            dev["clock_offset_s"] = (target - KD_EPOCH).total_seconds() - t
            return {"datetime": _kd_now_text(dev, t)}
        if cap == "system.initialize":
            if _kd_busy(s, dev):
                raise _KDReject("BUSY")
            host._kd_fw(iid, "coulometry.configure", start_drift_threshold_kg_s=20e-9 / 60, extraction_time_s=0.0, stirring_level=8, current_a=0.4)
            inst = host._kf899_instances[iid]
            fresh = _kd_new_device()
            fresh.update(power_epoch=inst["device"]["power_epoch"], init_until=t + KD_INIT_S, beeps=inst["device"]["beeps"],
                         restage=inst["device"]["restage"])
            inst["device"] = fresh
            return {"initialized": True}
        raise _KDReject("UNKNOWN_CAPABILITY")

    def _kd_panel(self, instance_id, cap, args, request_id):
        host = self._kf899_host
        panel = host._kf899_panels.get(instance_id)
        if panel is None:
            return self._kf899_response(instance_id, request_id, "unsupported", code="UNSUPPORTED")
        if _kd_pu.check_arguments(METROHM899_PANEL_CAPS[cap]["input_schema"], args):
            return self._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
        if METROHM899_PANEL_CAPS[cap]["visibility"] != "agent" and host._kf899_principals[self._kf899_principal]["visibility"] == "agent":
            return self._kf899_response(instance_id, request_id, "rejected", code="ACCESS_DENIED")
        try:
            if cap == "panel.capture":
                return self._kf899_response(instance_id, request_id, result=host._capture(instance_id, args["display_id"]))
            if cap == "panel.frame":
                return self._kf899_response(instance_id, request_id, result={"frame": panel.frame()})
            caller, host._kd_caller = getattr(host, "_kd_caller", None), self._kf899_principal   # the gesture's commands run as this caller
            try:
                accepted = panel.press(args["input_id"], float(args.get("duration_s", 0.1)), held=args.get("held", ()))
            finally:
                host._kd_caller = caller
        except _kd_pu.PanelError:
            return self._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
        return self._kf899_response(instance_id, request_id, result={"accepted": bool(accepted)})
# <<< CHEM-102 Metrohm 899 dialog firmware and front panel


# >>> CHEM-005 Metrohm 914 meter firmware and front panel
# The 914 pH/DO/Conductometer as its operator sees it (operating manual references/manuals/metrohm_914/, 'p.N' = PDF page): the two-channel and
# one-channel main dialogs with sensor condition and calibration interval (p.37-43), the measuring and calibration parameters of the pH, the
# conductivity and the DO channel (p.54-58), pH calibration with 1-5 buffers and the stored buffer tables (p.55, p.70-71, p.88-99), the cell
# constant (p.73-74) and the DO 0 % / 100 % calibration (p.75-76), sensors (p.60-67), measured values with the four criteria, view / delete /
# CSV / PC/LIMS files (p.58-60, p.78-80), report settings (p.67-68), configuration (p.68-69), user and dialog type with the expert password
# (p.47, p.69), automatic power-off and display off (p.68-69), reset to factory settings (p.85-86), plus the front panel (panel_ui.json beside
# the contract, run by scripts/asset_runtime/_panel_ui.py).  Metrohm914DeviceRuntime / Metrohm914DeviceSession extend the CHEM-005 classes
# above, whose capabilities keep their meaning; the CHEM-005 runtime keeps its capability set.  The second channel is measured with the CHEM-005
# measuring model itself (Metrohm914Runtime._sample on a copy with that channel selected); the meter's pH comes from the measured potential and
# the sensor's slope / pH(0), its conductivity from the conductance and the cell constant, its DO from the optical phase angle (dphi) of the
# O2 Lumitrode (Stern-Volmer) and the stored 0 % / 100 % phase angles.  Sensor errors (pH asymmetry is the host's reference potential; the
# cell constant follows the host's conductance; the phase-angle errors here) are trusted-host parameters, 0 (ideal) by default.
KS_ASSET_VERSION = "1.2.4"   # 1.2.4 (2026-10-22): calibrate standard tolerance (buffer recognition) and refusal messages
KS_ASSET_DIR = _kd_Path(__file__).resolve().parents[1] / "assets/basic40_batch02/t4_measure/metrohm_914"
KS_BOOT_S = 3.0                                            # start image (p.33 'takes some time'; assumption)
KS_K = _m914_math.log(10) * 8.31446261815324 / 96485.33212331001       # ln10 R / F (V/K)
KS_PROGRAM_VERSION = "5.914.3021"                          # a firmware with the buffer tables of p.88 (assumption)
KS_EPOCH = _kd_dt.datetime(2026, 1, 1, 0, 0, 0)            # device clock at simulation time 0 until clock.set (assumption)
KS_TRUE_DPHI = (53.0, 22.0)                                # phase angle of the ideal O2 Lumitrode at 0 % / 100 % air saturation (assumption)
KS_DRIFT = {"pH": 0.028, "U": 1.875, "T": 0.974, "DO": 0.24}           # drift-dependent criterion per minute (p.60)
KS_LANGUAGES = ["Deutsch", "English", "Español", "Français", "Português", "中文"]
KS_PH_MODES, KS_K_MODES = ["pH", "U", "T"], ["Cond. κ", "TDS", "Salinity", "ρ", "T"]
KS_DO_MODES = ["% air sat", "mg/L DO", "ppm DO", "µmol/L DO", "mbar DO", "Torr DO", "dphi"]
KS_SENSOR_TYPES = ["pH electrode", "Conductivity", "Metal electrode", "Oxygen sensor", "Temp. sensor", "Other sensor..."]
KS_PRINTERS = ["HP Officejet Pro", "HP Laserjet Pro", "Epson (ESC-POS)", "Seiko (ESC-POS)", "Citizen (ESC-POS)", "Custom (ESC-POS)",
               "Epson TM-U220B", "Universal (ESC-POS)"]
KS_REPORTS = ["Calibration pH", "Calibration κ", "Calibration DO", "Sensors", "Configuration", "Parameters pH", "Parameters κ", "Parameters DO",
              "Meas. values", "All reports"]
KS_ERRORS = {"POWER_OFF": "The instrument is switched off", "NOT_READY": "The instrument is starting",
             "ONE_CHANNEL": "A sensor can be calibrated only in the one-channel main dialog",
             "NO_SIGNAL": "No measuring signal: sensor not connected or no sample", "SAME_BUFFER": "912-181 Same buffer",
             "WRONG_PASSWORD": "Wrong password", "PASSWORD_MISMATCH": "New password and confirmation differ", "NOT_FOUND": "No such sensor or value",
             "SENSOR_IN_USE": "The sensor is used by a measuring channel", "USER_DATA_DELETED": "User data deleted", "LOCKED": "Locked in the Routine dialog type",
             "NO_CALIBRATION": "No calibration in progress", "LIMIT": "Memory full (100 sensors, 1000 values)",
             "ACCESS_DENIED": "Started by another caller: only that caller can stop, power off or change this acquisition, recording or calibration"}


def _ks_buffers():
    """Stored buffer series (p.88-99): name -> (nominal pH values, {temperature degC: [pH at that temperature or None]})."""
    return {
        'Metrohm': ([4.0, 7.0, 9.0], {0: [3.99, 7.11, 9.27], 5: [3.99, 7.08, 9.18], 10: [3.99, 7.06, 9.13], 15: [3.99, 7.04, 9.08], 20: [3.99, 7.02, 9.04], 25: [4.0, 7.0, 9.0], 30: [4.0, 6.99, 8.96], 35: [4.01, 6.98, 8.93], 40: [4.02, 6.98, 8.9], 45: [4.03, 6.97, 8.87], 50: [4.04, 6.97, 8.84], 55: [4.06, 6.97, 8.81], 60: [4.07, 6.97, 8.79], 65: [4.09, 6.98, 8.76], 70: [4.11, 6.98, 8.74], 75: [4.13, 6.99, 8.73], 80: [4.15, 7.0, 8.71], 85: [4.18, 7.0, 8.7], 90: [4.2, 7.01, 8.68], 95: [4.23, 7.02, 8.67]}),
        'NIST': ([1.679, 4.005, 6.865, 9.18, 12.454], {0: [1.666, 4.0, 6.984, 9.464, None], 5: [1.668, 3.998, 6.951, 9.395, 13.207], 10: [1.67, 3.997, 6.923, 9.332, 13.003], 15: [1.672, 3.998, 6.9, 9.276, 12.81], 20: [1.675, 4.0, 6.881, 9.225, 12.627], 25: [1.679, 4.005, 6.865, 9.18, 12.454], 30: [1.683, 4.011, 6.853, 9.139, 12.289], 35: [1.688, 4.018, 6.844, 9.102, 12.133], 40: [1.694, 4.027, 6.838, 9.068, 11.984], 45: [1.7, 4.038, 6.836, 9.04, 11.841], 50: [1.707, 4.05, 6.833, 9.011, 11.705], 55: [1.715, 4.075, 6.834, 8.985, 11.574], 60: [1.723, 4.091, 6.836, 8.962, 11.449], 65: [1.733, 4.108, 6.841, 8.942, None], 70: [1.743, 4.126, 6.845, 8.921, None], 75: [1.754, 4.145, 6.852, 8.903, None], 80: [1.766, 4.164, 6.859, 8.885, None], 85: [1.779, 4.184, 6.868, 8.868, None], 90: [1.792, 4.205, 6.877, 8.85, None], 95: [1.806, 4.227, 6.886, 8.833, None]}),
        'DIN': ([1.09, 3.06, 4.65, 6.79, 9.23, 12.75], {0: [1.08, None, 4.67, 6.89, 9.48, None], 5: [1.08, None, 4.66, 6.86, 9.43, None], 10: [1.09, 3.1, 4.66, 6.84, 9.37, 13.37], 15: [1.09, 3.08, 4.65, 6.82, 9.32, 13.15], 20: [1.09, 3.07, 4.65, 6.8, 9.27, 12.96], 25: [1.09, 3.06, 4.65, 6.79, 9.23, 12.75], 30: [1.1, 3.05, 4.65, 6.78, 9.18, 12.61], 35: [1.1, 3.05, 4.66, 6.77, 9.13, 12.44], 40: [1.1, 3.04, 4.66, 6.76, 9.09, 12.29], 45: [1.1, 3.04, 4.67, 6.76, 9.04, 12.13], 50: [1.11, 3.04, 4.68, 6.76, 9.0, 11.98], 55: [1.11, 3.04, 4.69, 6.76, 8.97, 11.84], 60: [1.11, 3.04, 4.7, 6.76, 8.92, 11.69], 65: [1.11, 3.04, 4.71, 6.76, 8.9, 11.56], 70: [1.11, 3.04, 4.72, 6.76, 8.88, 11.43], 75: [1.12, 3.04, 4.74, 6.77, 8.86, 11.3], 80: [1.12, 3.05, 4.75, 6.78, 8.85, 11.19], 85: [1.12, 3.06, 4.77, 6.79, 8.83, 11.08], 90: [1.13, 3.07, 4.79, 6.8, 8.82, 10.99]}),
        'Fisher': ([2.0, 4.0, 7.0, 10.0], {0: [None, 4.01, 7.13, 10.34], 5: [1.98, 3.99, 7.1, 10.26], 10: [1.98, 4.0, 7.07, 10.19], 15: [2.02, 3.99, 7.05, 10.12], 20: [2.0, 4.0, 7.02, 10.06], 25: [2.0, 4.0, 7.0, 10.0], 30: [2.0, 4.01, 6.99, 9.94], 35: [2.02, 4.02, 6.98, 9.9], 40: [2.01, 4.03, 6.97, 9.85], 45: [2.01, 4.04, 6.97, 9.81], 50: [2.01, 4.06, 6.97, 9.78], 55: [None, 4.07, 6.97, 9.74], 60: [None, 4.09, 6.98, 9.7]}),
        'Mettler Toledo': ([2.0, 4.01, 7.0, 9.21, 11.0], {0: [2.03, 4.01, 7.12, 9.52, 11.9], 5: [2.02, 4.01, 7.09, 9.45, 11.72], 10: [2.01, 4.0, 7.06, 9.38, 11.54], 15: [2.0, 4.0, 7.04, 9.32, 11.36], 20: [2.0, 4.0, 7.02, 9.26, 11.18], 25: [2.0, 4.01, 7.0, 9.21, 11.0], 30: [1.99, 4.01, 6.99, 9.16, 10.82], 35: [1.99, 4.02, 6.98, 9.11, 10.64], 40: [1.98, 4.03, 6.97, 9.06, 10.46], 45: [1.98, 4.04, 6.97, 9.03, 10.28], 50: [1.98, 4.06, 6.97, 8.99, 10.1], 55: [1.98, 4.08, 6.98, 8.96, None], 60: [1.98, 4.1, 6.98, 8.93, None], 65: [1.98, 4.13, 6.99, 8.9, None], 70: [1.99, 4.16, 7.0, 8.88, None], 75: [1.99, 4.19, 7.02, 8.85, None], 80: [2.0, 4.22, 7.04, 8.83, None], 85: [2.0, 4.26, 7.06, 8.81, None], 90: [2.0, 4.3, 7.09, 8.79, None], 95: [2.0, 4.35, 7.12, 8.77, None]}),
        'Merck CertiPUR 20 / Titrisol': ([2.0, 4.0, 7.0, 9.0, 11.0], {0: [2.01, 4.05, 7.13, 9.24, 11.45], 5: [2.01, 4.04, 7.07, 9.16, 11.32], 10: [2.01, 4.02, 7.05, 9.11, 11.2], 15: [2.0, 4.01, 7.02, 9.05, 11.1], 20: [2.0, 4.0, 7.0, 9.0, 11.0], 25: [2.0, 4.01, 6.98, 8.95, 10.9], 30: [2.0, 4.01, 6.98, 8.91, 10.81], 35: [2.0, 4.01, 6.96, 8.88, 10.72], 40: [2.0, 4.01, 6.95, 8.79, 10.64], 45: [2.0, 4.0, 6.95, 8.82, 10.56], 50: [2.0, 4.0, 6.95, None, 10.48]}),
        'Merck CertiPUR 25': ([4.0, 7.0, 9.0, 10.0], {5: [4.05, 7.09, 9.22, 10.22], 10: [4.04, 7.08, 9.16, 10.16], 15: [4.02, 7.04, 9.1, 10.1], 20: [4.01, 7.02, 9.05, 10.05], 25: [4.0, 7.0, 9.0, 10.0], 30: [3.99, 6.98, 8.96, 9.94], 35: [3.98, 6.98, 8.93, 9.9], 40: [3.98, 6.97, 8.89, 9.86], 45: [3.98, 6.97, 8.86, 9.8], 50: [3.98, 6.97, 8.84, 9.73]}),
        'Beckmann': ([4.01, 7.0, 10.01], {0: [4.0, 7.12, 10.32], 5: [4.0, 7.09, 10.25], 10: [4.0, 7.06, 10.18], 15: [4.0, 7.04, 10.12], 20: [4.0, 7.01, 10.06], 25: [4.01, 7.0, 10.01], 30: [4.01, 6.99, 9.97], 35: [4.02, 6.99, 9.93], 40: [4.03, 6.97, 9.89], 45: [4.05, 6.97, 9.86], 50: [4.06, 6.97, 9.83]}),
        'Radiometer Analytical': ([1.679, 4.005, 7.0, 9.18], {0: [1.666, 4.0, 7.118, 9.464], 5: [1.668, 3.998, 7.087, 9.395], 10: [1.67, 3.997, 7.059, 9.332], 15: [1.672, 3.998, 7.036, 9.276], 20: [1.675, 4.001, 7.016, 9.225], 25: [1.679, 4.005, 7.0, 9.18], 30: [1.683, 4.011, 6.987, 9.139], 35: [1.688, 4.018, 6.977, 9.102], 40: [1.694, 4.027, 6.97, 9.068], 45: [1.7, 4.038, 6.965, 9.038], 50: [1.707, 4.05, 6.964, 9.01], 55: [1.715, 4.064, 6.965, 8.985], 60: [1.723, 4.08, 6.968, 8.962], 65: [1.732, 4.097, 6.974, 8.941], 70: [1.743, 4.116, 6.982, 8.921], 75: [1.754, 4.137, 6.992, 8.9], 80: [1.765, 4.159, 7.004, 8.884], 85: [1.778, 4.183, 7.018, 8.867], 90: [1.792, 4.208, 7.034, 8.85]}),
        'Baker': ([4.0, 7.0, 9.0, 10.0], {0: [4.0, 7.13, 9.23, 10.3], 5: [4.0, 7.09, 9.17, 10.24], 10: [4.0, 7.05, 9.1, 10.17], 15: [4.0, 7.03, 9.05, 10.11], 20: [4.0, 7.0, 9.0, 10.05], 25: [4.0, 6.98, 8.96, 10.0], 30: [4.01, 6.98, 8.91, 9.96], 35: [4.02, 6.98, 8.88, 9.93], 40: [4.03, 6.97, 8.84, 9.89], 45: [4.04, 6.97, 8.81, 9.86], 50: [4.05, 6.96, 8.78, 9.82], 55: [4.07, 6.96, 8.76, 9.79], 60: [4.08, 6.96, 8.73, 9.76], 65: [4.1, 6.97, 8.71, 9.74], 70: [4.12, 6.97, 8.69, 9.72], 75: [4.14, 6.98, 8.68, 9.7], 80: [4.16, 6.98, 8.66, 9.68], 85: [4.19, 6.99, 8.64, 9.66], 90: [4.21, 7.0, 8.62, 9.64]}),
        'Hamilton DURACAL': ([4.01, 7.0, 9.21, 10.01], {5: [4.01, 7.09, 9.45, 10.19], 10: [4.0, 7.06, 9.38, 10.15], 15: [4.0, 7.04, 9.32, 10.11], 20: [4.0, 7.02, 9.26, 10.06], 25: [4.01, 7.0, 9.21, 10.01], 30: [4.01, 6.99, 9.16, 9.97], 35: [4.02, 6.98, 9.11, 9.92], 40: [4.03, 6.97, 9.06, 9.86], 45: [4.04, 6.97, 9.03, 9.83], 50: [4.06, 6.97, 8.99, 9.79]}),
        'Honeywell Fluka': ([4.0, 7.0, 9.0], {0: [4.03, 7.13, 9.24], 5: [4.025, 7.09, 9.175], 10: [4.02, 7.05, 9.11], 15: [4.01, 7.02, 9.055], 20: [4.0, 7.0, 9.0], 25: [4.0, 6.99, 8.965], 30: [4.0, 6.98, 8.93], 35: [4.0, 6.975, 8.895], 40: [4.0, 6.97, 8.86], 45: [4.0, 6.965, 8.83], 50: [4.0, 6.96, 8.8], 55: [4.0, 6.96, 8.775], 60: [4.0, 6.96, 8.75], 65: [4.0, 6.965, 8.73], 70: [4.0, 6.97, 8.71], 75: [4.0, 6.975, 8.69], 80: [4.0, 6.98, 8.67], 85: [4.0, 6.99, 8.655], 90: [4.0, 7.0, 8.64]}),
    }


KS_BUFFERS = _ks_buffers()
KS_BUFFER_TYPES = list(KS_BUFFERS) + ["Special"]


def _ks_buffer_ph(table, k, t_c):
    """pH of buffer k of a stored series at t_c (linear interpolation between the 5 degC rows; None outside the table)."""
    rows = {tc: r[k] for tc, r in table[1].items() if r[k] is not None}
    if not rows:
        return None
    ts = sorted(rows)
    if t_c <= ts[0] or t_c >= ts[-1]:
        return rows[ts[0]] if abs(t_c - ts[0]) < 1e-9 else rows[ts[-1]] if abs(t_c - ts[-1]) < 1e-9 else None
    hi = next(x for x in ts if x >= t_c)
    lo = max(x for x in ts if x <= t_c)
    return rows[lo] if hi == lo else rows[lo] + (rows[hi] - rows[lo]) * (t_c - lo) / (hi - lo)


def _ks_do_sat_mg_l(t_c, salinity, pressure_mbar):
    """Air-saturated DO (mg/L) at t_c, salinity (PSU) and pressure: Benson-Krause freshwater equation with the salinity and pressure
    corrections of APHA Standard Methods 4500-O (engineering model of the meter's compensation)."""
    tk = t_c + 273.15
    c = _m914_math.exp(-139.34411 + 1.575701e5 / tk - 6.642308e7 / tk ** 2 + 1.243800e10 / tk ** 3 - 8.621949e11 / tk ** 4)
    c *= _m914_math.exp(-salinity * (0.017674 - 10.754 / tk + 2140.7 / tk ** 2))
    p = pressure_mbar / 1013.25
    pwv = _m914_math.exp(11.8571 - 3840.70 / tk - 216961.0 / tk ** 2)
    theta = 0.000975 - 1.426e-5 * t_c + 6.436e-8 * t_c ** 2
    return c * p * ((1 - pwv / p) * (1 - theta * p)) / ((1 - pwv) * (1 - theta))


def _ks_salinity(kappa_ms_cm, t_c):
    """Practical salinity (PSS-78) from conductivity at t_c and atmospheric pressure."""
    r = kappa_ms_cm / 42.914
    rt = 0.6766097 + 0.0200564 * t_c + 1.104259e-4 * t_c ** 2 - 6.9698e-7 * t_c ** 3 + 1.0031e-9 * t_c ** 4
    rr = max(r / rt, 0.0)
    a, b = (0.0080, -0.1692, 25.3851, 14.0941, -7.0261, 2.7081), (0.0005, -0.0056, -0.0066, -0.0375, 0.0636, -0.0144)
    sq = rr ** 0.5
    s = sum(a[i] * sq ** i for i in range(6)) + (t_c - 15) / (1 + 0.0162 * (t_c - 15)) * sum(b[i] * sq ** i for i in range(6))
    return max(s, 0.0)


def _ks_p(key, label, typ, default, page, **kw):
    return dict(key=key, label=label, type=typ, default=default, page=page, **kw)


KS_PARAMS = {   # channel parameters (p.54-58): key -> definition; the panel builder reads these too
    "ph": [_ks_p("mode", "Measuring mode", "enum", "pH", "p.54", options=KS_PH_MODES),
           _ks_p("sensor", "Sensor name", "sensor", "pH default", "p.54"),
           _ks_p("delta_mv", "Delta measurement mV", "num", None, "p.54", min=-1500.0, max=1500.0, decimals=1, unit="mV", none="off"),
           _ks_p("cal_temperature", "Temperature", "num", 25.0, "p.54", min=0.0, max=99.9, decimals=1, unit="°C", cal=True),
           _ks_p("report", "Report", "bool", False, "p.54", cal=True),
           _ks_p("buffers", "Number of buffers", "int", 2, "p.55", min=1, max=5, cal=True),
           _ks_p("buffer_type", "Buffer type", "enum", "Metrohm", "p.55, p.88", options=KS_BUFFER_TYPES, cal=True),
           _ks_p("special", "Special buffers", "list", [7.0, 4.0, 9.0, 10.0, 12.0], "p.55", min=-19.999, max=19.999, cal=True)],
    "conductance": [_ks_p("mode", "Measuring mode", "enum", "Cond. κ", "p.55", options=KS_K_MODES),
                    _ks_p("sensor", "Sensor name", "sensor", "k default", "p.55"),
                    _ks_p("ref_temp", "Reference temp.", "num", 25.0, "p.55", min=0.0, max=99.9, decimals=1, unit="°C"),
                    _ks_p("tc", "Temp. compens.", "num", 2.00, "p.55-56", min=0.0, max=9.99, decimals=2, unit="%/°C", none="DIN"),
                    _ks_p("tds_factor", "TDS factor", "num", 0.40, "p.56", min=0.40, max=1.00, decimals=2),
                    _ks_p("cal_temperature", "Temperature", "num", 25.0, "p.56", min=0.0, max=99.9, decimals=1, unit="°C", cal=True),
                    _ks_p("cal_ref_temp", "Reference temp.", "num", 25.0, "p.56", min=0.0, max=99.9, decimals=1, unit="°C", cal=True),
                    _ks_p("standard", "Stand. conduct.", "num", 12.870, "p.56", min=0.0, max=2000.0, decimals=3, unit="mS/cm", cal=True),
                    _ks_p("cal_tc", "Temp. compens.", "num", 1.90, "p.56", min=0.0, max=9.99, decimals=2, unit="%/°C", cal=True),
                    _ks_p("report", "Report", "bool", False, "p.56", cal=True)],
    "do": [_ks_p("mode", "Measuring mode", "enum", "% air sat", "p.56-57", options=KS_DO_MODES),
           _ks_p("temp_comp", "Temp. compens.", "num", None, "p.57", min=-9.9, max=60.0, decimals=1, unit="°C", none="Auto"),
           _ks_p("sal_comp", "Sal. compens.", "num", 0.0, "p.57", min=0.0, max=70.0, decimals=1, unit="PSU", none="Auto"),
           _ks_p("press_comp", "Air press. comp.", "num", None, "p.57", min=300.0, max=1200.0, decimals=0, unit="mbar", none="Auto"),
           _ks_p("points", "Calibration points", "enum", "0%, 100%", "p.57", options=["0%, 100%", "100%"], cal=True),
           _ks_p("cal_temp_comp", "Temp. compens.", "num", None, "p.57", min=-9.9, max=60.0, decimals=1, unit="°C", none="Auto", cal=True),
           _ks_p("cal_press_comp", "Air press. comp.", "num", None, "p.57", min=300.0, max=1200.0, decimals=0, unit="mbar", none="Auto", cal=True),
           _ks_p("report", "Report", "bool", False, "p.58", cal=True)],
}
KS_VALUES = [   # Measured values (p.58-60)
    _ks_p("data", "Data", "enum", "save", "p.58", options=["print", "save", "print+save"]),
    _ks_p("criterion", "Criterion", "enum", "immediately", "p.58-60", options=["immediately", "time-dependent", "when changed", "drift-dependent"]),
    _ks_p("interval", "Time interval", "num", 4.0, "p.59", min=1.0, max=999999.0, decimals=0, unit="s"),
    _ks_p("stop_time", "Stop time", "num", None, "p.59", min=1.0, max=999999.0, decimals=0, unit="s", none="off"),
    _ks_p("delta_ph", "Delta pH", "num", 0.50, "p.59", min=0.10, max=16.00, decimals=2, unit="pH"),
    _ks_p("delta_t_ph", "Delta T(pH)", "num", 0.5, "p.59", min=0.1, max=100.0, decimals=1, unit="°C"),
    _ks_p("delta_mv", "Delta mV", "num", 30.0, "p.59", min=0.1, max=999.9, decimals=1, unit="mV"),
    _ks_p("delta_k", "Delta κ", "num", 0.1, "p.59", min=0.0001, max=10.0, decimals=4, unit="mS/cm"),
    _ks_p("delta_t_k", "Delta T(κ)", "num", 0.5, "p.59", min=0.1, max=100.0, decimals=1, unit="°C"),
    _ks_p("delta_do", "Delta DO", "num", 0.5, "p.59", min=0.1, max=99.0, decimals=1, unit="mg/L"),
    _ks_p("delta_sat", "Delta % air sat", "num", 10.0, "p.59", min=1.0, max=500.0, decimals=0, unit="%"),
    _ks_p("primary", "Primary channel", "enum", "pH/mV", "p.59-60", options=["pH/mV", "Cond.", "DO"]),
    _ks_p("output_datetime", "Output date/time", "bool", False, "p.60"),
    _ks_p("output_headers", "Output headers", "enum", "always", "p.60", options=["once", "always", "off"]),
    _ks_p("calibration_data", "Calibration data", "bool", False, "p.60"),
]
KS_SETTINGS = [
    _ks_p("last_decimal", "Last decimal place", "bool", True, "p.68"),
    _ks_p("signal_do", "Signal intensity DO", "bool", True, "p.68"),
    _ks_p("power_off", "Power off after", "int", 15, "p.68", min=1, max=60, unit="min", none="off"),
    _ks_p("lcd_off", "Turn off LCD after", "lcd", 15, "p.68-69", min=1, max=60, unit="min"),
    _ks_p("brightness", "Brightness", "enum", 100, "p.69", options=[100, 80, 60, 40, 20], unit="%"),
    _ks_p("language", "Language", "enum", "English", "p.69", options=KS_LANGUAGES),
    _ks_p("line_feed", "Line feed", "int", 2, "p.67", min=0, max=99, unit="lines"),
    _ks_p("printer", "Printer", "enum", "Custom (ESC-POS)", "p.67-68", options=KS_PRINTERS),
    _ks_p("user", "User", "text", "", "p.69", max_len=20),
    _ks_p("dialog_type", "Dialog type", "enum", "Expert", "p.47, p.69", options=["Expert", "Routine"]),
]
KS_SENSOR_FIELDS = [   # sensor data (p.61-66)
    _ks_p("name", "Sensor name", "text", "", "p.62", max_len=20),
    _ks_p("type", "Sensor type", "enum", "pH electrode", "p.62", options=KS_SENSOR_TYPES),
    _ks_p("order", "Order number", "text", "", "p.62", max_len=20),
    _ks_p("serial", "Serial number", "text", "", "p.62", max_len=20),
    _ks_p("slope", "Slope", "num", 100.0, "p.62", min=0.10, max=990.0, decimals=2, unit="%"),
    _ks_p("ph0", "pH(0)", "num", 7.0, "p.62", min=-99.999, max=99.999, decimals=3),
    _ks_p("cell_constant", "Cell constant", "num", 1.0, "p.62", min=0.001, max=500.0, decimals=3, unit="/cm"),
    _ks_p("dphi100", "Cal. dphi 100 %", "num", KS_TRUE_DPHI[1], "p.63", min=15.0, max=30.0, decimals=3, unit="°"),
    _ks_p("dphi0", "Cal. dphi 0 %", "num", KS_TRUE_DPHI[0], "p.63", min=45.0, max=60.0, decimals=3, unit="°"),
    _ks_p("cal_interval", "Calibration interval", "int", None, "p.63", min=1, max=999, unit="d", none="off"),
    _ks_p("temp_sensor", "Temp. sensor", "enum", "Pt1000", "p.63", options=["Pt1000", "NTC"]),
    _ks_p("r25", "R(25°C)", "num", 30000.0, "p.63", min=10000.0, max=100000.0, decimals=0, unit="Ω"),
    _ks_p("b_value", "B value", "num", 4100.0, "p.63", min=1000.0, max=9999.0, decimals=0, unit="K"),
    _ks_p("slope_limit", "Slope limit value", "bool", True, "p.63"),
    _ks_p("slope_lo", "Slope lower limit", "num", 95.0, "p.63", min=1.0, max=999.9, decimals=1, unit="%"),
    _ks_p("slope_hi", "Slope upper limit", "num", 103.0, "p.64", min=1.0, max=999.9, decimals=1, unit="%"),
    _ks_p("ph0_limit", "Limit value pH(0)", "bool", False, "p.64"),
    _ks_p("ph0_lo", "pH(0) lower limit", "num", 6.40, "p.64", min=0.0, max=99.99, decimals=2),
    _ks_p("ph0_hi", "pH(0) upper limit", "num", 8.00, "p.64", min=0.0, max=99.99, decimals=2),
    _ks_p("c_limit", "Limit value c", "bool", False, "p.64"),
    _ks_p("c_lo", "c lower limit", "num", 0.400, "p.64", min=0.001, max=500.0, decimals=3, unit="/cm"),
    _ks_p("c_hi", "c upper limit", "num", 0.550, "p.64", min=0.001, max=500.0, decimals=3, unit="/cm"),
    _ks_p("temp_offset", "Temperature offset", "num", 0.0, "p.64", min=-5.0, max=5.0, decimals=1, unit="°C"),
    _ks_p("led", "LED intensity (%)", "enum", 20, "p.64", options=[10, 20, 30]),
    _ks_p("dphi100_limit", "Limit value dphi 100 %", "bool", True, "p.64"),
    _ks_p("dphi0_limit", "Limit value dphi 0 %", "bool", True, "p.65"),
]
KS_SENSOR_FIELD = {f["key"]: f for f in KS_SENSOR_FIELDS}


def _ks_schema(p):
    if p["type"] == "bool":
        s = {"type": "boolean"}
    elif p["type"] == "enum":
        s = {"type": "integer" if isinstance(p["options"][0], int) else "string", "enum": list(p["options"])}
    elif p["type"] in ("text", "sensor"):
        s = {"type": "string", "maxLength": p.get("max_len", 20)}
    elif p["type"] == "lcd":
        s = {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 60, "x-unit": "min"}, {"type": "string", "enum": ["auto", "off"]}]}
        return s
    elif p["type"] == "list":
        s = {"type": "array", "minItems": 5, "maxItems": 5, "items": {"type": "number", "minimum": p["min"], "maximum": p["max"]}}
    else:
        s = {"type": "integer" if p["type"] == "int" else "number", "minimum": p["min"], "maximum": p["max"]}
    _kd_units(s)
    if s.get("type") in ("number", "integer") and p.get("unit"):
        s["x-unit"] = p["unit"]
    return {"anyOf": [s, {"type": "null"}]} if p.get("none") else s


def _ks_obj(defs, keys=None):
    return {"type": "object", "properties": {p["key"]: _ks_schema(p) for p in defs if keys is None or p["key"] in keys}, "additionalProperties": False,
            "minProperties": 1}


def _ks_sensor(name, typ, **kw):
    s = {f["key"]: _kd_copy.deepcopy(f["default"]) for f in KS_SENSOR_FIELDS}
    s.update(name=name, type=typ, cal_date=None, cal_temp=None, **kw)
    return s


def _ks_new_device():
    return dict(power_epoch=0, boot_until=None, clock_offset_s=0.0, last_key_s=0.0, lcd="on", password="Expert", message=None, message_seq=0,
                params={ch: {p["key"]: _kd_copy.deepcopy(p["default"]) for p in defs} for ch, defs in KS_PARAMS.items()},
                values_cfg={p["key"]: _kd_copy.deepcopy(p["default"]) for p in KS_VALUES},
                settings={p["key"]: _kd_copy.deepcopy(p["default"]) for p in KS_SETTINGS},
                sensors=[_ks_sensor("k default", "Conductivity"), _ks_sensor("metal def.", "Metal electrode"), _ks_sensor("pH default", "pH electrode"),
                         _ks_sensor("temp default", "Temp. sensor"), _ks_sensor("O2 Lumitrode", "Oxygen sensor", order="6.0201.000")],
                ids=dict(id1="", id2=""), values=[], files={}, recording=None, cal=None, errors=dict(dphi0_deg=0.0, dphi100_deg=0.0, cap_wear=0.0),
                probes={})


def _ks_status(inst, dev, t):
    if not inst["power"]:
        return "off"
    if dev["boot_until"] is not None and t < dev["boot_until"] - 1e-9:
        return "boot"
    return "ready"


def _ks_channel_reading(host, inst, name):
    """CHEM-005 reading of one channel on the last host sample (the instance itself when that channel is selected): the meter's readings
    (_ks_meter through Metrohm914DeviceRuntime._sample) and the ideal signals they come from."""
    if not inst["installed"][name]:
        return None
    if inst["selected"] == name and inst["status"] in ("UNSTABLE", "STABLE", "STALE"):
        ideal = inst.get("ideal") or dict(readings=dict(inst["readings"]), raw=inst["raw"], temp_k=inst["compensation_temperature"])
        return dict(status=inst["status"], readings=dict(inst["readings"]), ideal=ideal, temp_k=inst["compensation_temperature"])
    if not any(v is not None for v in inst["truth"].values()):
        return None                                                        # no host sample yet
    probe = _m914_copy.deepcopy(inst)
    probe["selected"] = name
    try:
        host._sample(probe, inst["sample_time"] if inst["sample_time"] is not None else host.time_s, dict(inst["truth"]))
    except ValueError:
        return None
    ideal = probe.get("ideal") or dict(readings=dict(probe["readings"]), raw=probe["raw"], temp_k=probe["compensation_temperature"])
    return dict(status=probe["status"], readings=dict(probe["readings"]), ideal=ideal, temp_k=probe["compensation_temperature"])


def _ks_sensor_of(dev, name):
    return next((s for s in dev["sensors"] if s["name"] == name), None)


KS_PT1000 = (3.9083e-3, -5.775e-7)                         # Callendar-Van Dusen A, B of a Pt1000 (IEC 60751; also used below 0 degC)
KS_PROBE_DEFAULT = dict(kind="Pt1000", r25=30000.0, b_value=4100.0)   # the physical temperature probe of each channel (trusted host)
KS_SIGNAL_LIMITS = (50.0, 30.0)                            # DO signal intensity: good above 50 %, near the limit above 30 % (assumption)


def _ks_probe_r(kind, t_c, r25, b):
    a, bb = KS_PT1000
    return 1000.0 * (1.0 + a * t_c + bb * t_c ** 2) if kind == "Pt1000" else r25 * _m914_math.exp(b * (1.0 / (t_c + 273.15) - 1.0 / 298.15))


def _ks_probe_t(kind, r, r25, b):
    a, bb = KS_PT1000
    if kind == "Pt1000":
        c = 1.0 - r / 1000.0
        return (-a + _m914_math.sqrt(max(0.0, a * a - 4 * bb * c))) / (2 * bb) if bb else -c / a
    return 1.0 / (1.0 / 298.15 + _m914_math.log(r / r25) / b) - 273.15


def _ks_meter_temp_k(dev, ch, sensor, true_k):
    """Temperature the meter reads on a channel (p.63-64, p.84 'the wrong temperature sensor type is selected'): the resistance of the physical
    probe (trusted host; Pt1000 by default) converted with the sensor record's Temp. sensor / R(25 degC) / B value, plus its Temperature
    offset.  Matching settings: the true temperature."""
    if true_k is None or sensor is None:
        return true_k
    probe = dev.get("probes", {}).get(ch) or KS_PROBE_DEFAULT
    same = sensor["temp_sensor"] == probe["kind"] and (probe["kind"] == "Pt1000" or (sensor["r25"] == probe["r25"] and sensor["b_value"] == probe["b_value"]))
    t_c = true_k - 273.15 if same else _ks_probe_t(sensor["temp_sensor"], _ks_probe_r(probe["kind"], true_k - 273.15, probe["r25"], probe["b_value"]),
                                                    sensor["r25"], sensor["b_value"])
    return t_c + 273.15 + sensor["temp_offset"]


def _ks_channel_sensor(dev, ch):
    p = dev["params"][ch]
    return _ks_sensor_of(dev, p.get("sensor", "O2 Lumitrode")) or _ks_sensor_of(dev, "O2 Lumitrode")


def _ks_meter(host, inst, dev, ch, ideal):
    """The one measuring model of the meter (every path: CHEM-005 measurement.* / electrochemistry.reading / records, the device channels,
    stored values and files): the temperature through the probe and the sensor record (or the manual temperature), pH from the electrode
    potential with the stored slope / pH(0), conductance compensated with the sample's coefficient at the meter's temperature, DO from the
    phase angle (evaluator phase errors) with the stored dphi calibration and the DO compensation entries; plus the CHEM-005 single-point
    offset.  Returns value (with offset), pre (without), temp_k and the raw signals the screen shows."""
    sensor = _ks_channel_sensor(dev, ch)
    truth = inst["truth"]
    true_k = truth["temperature_k"]
    meter_k = inst["manual_temperature"] if inst["temperature_mode"] == "manual" else _ks_meter_temp_k(dev, ch, sensor, true_k)
    off = inst["calibration"][ch]["offset"]
    out = dict(value=None, pre=None, temp_k=meter_k, raw={})
    if ch == "ph":
        u = (ideal or {}).get("readings", {}).get("potential_v")
        if u is None or meter_k is None:
            return out
        pre = sensor["ph0"] - u / (sensor["slope"] / 100.0 * KS_K * meter_k)
        out.update(pre=pre, raw=dict(potential_mv=u * 1000.0, ph=pre if off is None else pre + off))
    elif ch == "conductance":
        g = truth["conductance_s"]
        if g is None:
            return out
        alpha, ref = truth["conductance_temp_coefficient_k_inv"], truth["conductance_reference_temperature_k"]
        if alpha is not None and ref is not None and meter_k is not None:
            g = g / (1.0 + alpha * (meter_k - ref))
        out.update(pre=g, raw=dict(conductance_s=truth["conductance_s"]))
    else:
        do = truth["do_kg_m3"]
        if do is None:
            return out
        p = dev["params"]["do"]
        t_true = (true_k if true_k is not None else (meter_k if meter_k is not None else 298.15)) - 273.15
        tt = p["temp_comp"] if p["temp_comp"] is not None else (meter_k - 273.15 if meter_k is not None else 25.0)
        sal = p["sal_comp"] if p["sal_comp"] is not None else 0.0
        press = p["press_comp"] if p["press_comp"] is not None else 1013.25
        sat_true = do * 1000.0 / _ks_do_sat_mg_l(t_true, sal, 1013.25) * 100.0     # the oxygen partial pressure of the sample (standard air pressure)
        phi0, phi100 = KS_TRUE_DPHI[0] + dev["errors"]["dphi0_deg"], KS_TRUE_DPHI[1] + dev["errors"]["dphi100_deg"]
        ksv = _m914_math.tan(_m914_math.radians(phi0)) / _m914_math.tan(_m914_math.radians(phi100)) - 1.0
        dphi = _m914_math.degrees(_m914_math.atan(_m914_math.tan(_m914_math.radians(phi0)) / (1.0 + ksv * sat_true / 100.0)))
        k_cal = _m914_math.tan(_m914_math.radians(sensor["dphi0"])) / _m914_math.tan(_m914_math.radians(sensor["dphi100"])) - 1.0
        sat = (_m914_math.tan(_m914_math.radians(sensor["dphi0"])) / _m914_math.tan(_m914_math.radians(dphi)) - 1.0) / k_cal * 100.0
        mgl = sat / 100.0 * _ks_do_sat_mg_l(tt, sal, press)
        signal = 4.0 * sensor["led"] * (1.0 - dev["errors"].get("cap_wear", 0.0))          # signal intensity % (LED 20 % -> 80 %; assumption)
        out.update(pre=mgl / 1000.0, raw=dict(dphi=dphi, sat=sat, mg_l=mgl, t_c=tt, sal=sal, press=press, signal_pct=signal))
    out["value"] = out["pre"] if off is None else out["pre"] + off
    return out


def _ks_channels(host, inst, dev):
    """What the meter shows per channel: value in the measuring mode, unit, temperature, sensor, condition and calibration interval."""
    out = {}
    t = host.time_s
    st = dev["settings"]
    for ch in ("ph", "do", "conductance"):
        if not inst["installed"][ch]:
            continue
        p = dev["params"][ch]
        sensor = _ks_channel_sensor(dev, ch)
        r = _ks_channel_reading(host, inst, ch)
        ok = r is not None and r["status"] in ("UNSTABLE", "STABLE", "STALE", "OUT_OF_RANGE")      # the meter shows what it converts, also
        m = _ks_meter(host, inst, dev, ch, r["ideal"]) if ok else None                            # beyond the CHEM-005 range
        temp_k = m["temp_k"] if m else (r["temp_k"] if r else None)
        if temp_k is None and ch == "conductance":
            temp_k = 298.15                                                # no temperature at all: the conductance is shown uncompensated
        t_c = None if temp_k is None else temp_k - 273.15                 # the meter's temperature (probe, sensor record, offset)
        value, unit, dec, raw = None, "", 2, {}
        if ch == "ph":
            if m is not None and m["value"] is not None:
                ph, u = m["value"], m["raw"]["potential_mv"] / 1000.0
                raw = dict(m["raw"])
                mv = u * 1000.0 - (p["delta_mv"] or 0.0) if p["delta_mv"] is not None else u * 1000.0
                value, unit, dec = {"pH": (ph, "pH", 3 if st["last_decimal"] else 2), "U": (mv, "mV", 1), "T": (t_c, "°C", 1)}[p["mode"]]
        elif ch == "conductance":
            g = (m["raw"]["conductance_s"] + (inst["calibration"]["conductance"]["offset"] or 0.0)) if m is not None and m["value"] is not None else None
            if g is not None:
                kappa = g * sensor["cell_constant"] * 1e3                      # mS/cm: the conductance with the stored cell constant
                alpha = 1.91 if p["tc"] is None else p["tc"]                    # DIN (EN 27888) approximated by 1.91 %/degC (assumption)
                tt = t_c if t_c is not None else p["ref_temp"]
                kref = kappa / (1.0 + alpha / 100.0 * (tt - p["ref_temp"]))   # mS/cm at the reference temperature
                raw = dict(conductance_s=g, kappa_ms_cm=kref)
                if p["mode"] == "Cond. κ":
                    value, unit, dec = (kref, "mS/cm", 3) if kref >= 1.0 else (kref * 1000.0, "µS/cm", 2 if kref * 1000 < 100 else 1)
                elif p["mode"] == "TDS":
                    value, unit, dec = kref * 1000.0 * p["tds_factor"], "mg/L", 1
                elif p["mode"] == "Salinity":
                    value, unit, dec = _ks_salinity(kappa, tt), "PSU", 2
                elif p["mode"] == "ρ":
                    value, unit, dec = (1.0 / (kref / 1000.0) if kref > 0 else None), "Ω·cm", 0
                else:
                    value, unit, dec = t_c, "°C", 1
        else:
            if m is not None and m["value"] is not None:
                tt, sal, press = m["raw"]["t_c"], m["raw"]["sal"], m["raw"]["press"]
                mgl = m["value"] * 1000.0                                      # with a CHEM-005 single-point offset
                sat = m["raw"]["sat"] * (mgl / m["raw"]["mg_l"]) if m["raw"]["mg_l"] else m["raw"]["sat"]
                dphi = m["raw"]["dphi"]
                raw = dict(dphi=dphi, sat=sat, mg_l=mgl, signal_pct=m["raw"]["signal_pct"])
                pw = _m914_math.exp(11.8571 - 3840.70 / (tt + 273.15) - 216961.0 / (tt + 273.15) ** 2) * 1013.25
                mbar = sat / 100.0 * 0.20946 * (press - pw)
                value, unit, dec = {"% air sat": (sat, "%", 1), "mg/L DO": (mgl, "mg/L", 2), "ppm DO": (mgl, "ppm", 2),
                                    "µmol/L DO": (mgl / 31.998 * 1000.0, "µmol/L", 1), "mbar DO": (mbar, "mbar", 1),
                                    "Torr DO": (mbar * 0.750062, "Torr", 1), "dphi": (dphi, "°", 3)}[p["mode"]]
        cond = "good"
        if sensor and ch == "ph":
            def near(v, lo, hi, d):
                return v < lo or v > hi, v < lo + d or v > hi - d
            bad = near_ = False
            if sensor["slope_limit"]:
                b, n = near(sensor["slope"], sensor["slope_lo"], sensor["slope_hi"], 1.0)
                bad, near_ = bad or b, near_ or n
            if sensor["ph0_limit"]:
                b, n = near(sensor["ph0"], sensor["ph0_lo"], sensor["ph0_hi"], 0.1)
                bad, near_ = bad or b, near_ or n
            cond = "out" if bad else "near" if near_ else "good"
        if ch == "do":                                                     # sensor status from the signal intensity, shown if switched on (p.38, p.68)
            sig = raw.get("signal_pct")
            cond = None if not st["signal_do"] else ("good" if sig is None or sig >= KS_SIGNAL_LIMITS[0] else
                                                     "near" if sig >= KS_SIGNAL_LIMITS[1] else "out")
        days = None
        if sensor and sensor["cal_interval"] is not None and sensor["cal_date"] is not None:
            days = max(0, sensor["cal_interval"] - int((t - sensor["cal_date"]) // 86400))
        out[ch] = dict(channel=ch, mode=p.get("mode"), value=value, unit=unit, decimals=dec,
                       text="----" if value is None else f"{_kd_round(value, dec):.{dec}f}", temperature_c=t_c, sensor=sensor and sensor["name"],
                       status=r["status"] if r else "NO_SIGNAL", condition=cond, cal_days=days, raw=raw)
    return out


def _ks_now_text(dev, t):
    return (KS_EPOCH + _kd_dt.timedelta(seconds=t + dev["clock_offset_s"])).strftime("%Y-%m-%dT%H:%M:%S")


def _ks_record_value(host, inst, dev, channels):
    """One measured value as the meter stores it (p.58): time, IDs, user and the shown value of each channel."""
    chans = _ks_channels(host, inst, dev)
    want = [c for c in chans if channels == "both" or c == inst["selected"]]
    data = {c: dict(mode=chans[c]["mode"], value=chans[c]["value"], unit=chans[c]["unit"], text=chans[c]["text"],
                    temperature_c=chans[c]["temperature_c"]) for c in want if chans[c]["value"] is not None}
    if not data:
        return None
    return dict(index=len(dev["values"]) + 1, time=_ks_now_text(dev, host.time_s), id1=dev["ids"]["id1"], id2=dev["ids"]["id2"],
                user=dev["settings"]["user"], channels=data)


def _ks_primary(dev, values_cfg, chans):
    ch = {"pH/mV": "ph", "Cond.": "conductance", "DO": "do"}[values_cfg["primary"]]
    if ch not in chans:                                                    # the primary channel is not installed: the first one shown
        ch = next(iter(chans), ch)
    return ch, chans.get(ch)


def _ks_quantity(ch, pv, cfg):
    """The primary channel's value in the units of the criteria (p.59-60): (value, delta of 'when changed', drift limit per minute)."""
    if pv is None or pv["value"] is None:
        return None, None, None
    if ch == "ph":
        return pv["value"], {"pH": cfg["delta_ph"], "U": cfg["delta_mv"], "T": cfg["delta_t_ph"]}[pv["mode"]], KS_DRIFT[pv["mode"]]
    if ch == "conductance":
        if pv["mode"] == "T":
            return pv["value"], cfg["delta_t_k"], KS_DRIFT["T"]
        k = pv["raw"]["kappa_ms_cm"]
        return k, cfg["delta_k"], 0.005 if k < 0.016 else 0.5 if k < 1.0 else 10.0
    if pv["mode"] == "% air sat":
        return pv["raw"]["sat"], cfg["delta_sat"], KS_DRIFT["DO"] / max(pv["raw"]["mg_l"], 1e-9) * pv["raw"]["sat"]
    return pv["raw"]["mg_l"], cfg["delta_do"], KS_DRIFT["DO"]


def _ks_csv(dev):
    head = "No;Date/time;ID1;ID2;User;Channel;Mode;Value;Unit;Temperature/°C"
    rows = [head] + [f"{v['index']};{v['time']};{v['id1']};{v['id2']};{v['user']};{c};{d['mode']};{d['text']};{d['unit']};"
                     f"{'' if d['temperature_c'] is None else format(d['temperature_c'], '.1f')}" for v in dev["values"] for c, d in v["channels"].items()]
    return "\n".join(rows) + "\n"


def _ks_pclims(dev):
    lines = []
    for v in dev["values"]:
        for c, d in v["channels"].items():
            if c != "do":                                                  # DO values are not output in PC/LIMS (p.79)
                lines.append(f"$S Mode V2.0 {c} {d['mode']}\n$S Value {d['text']} {d['unit']}\n$S Time {v['time']}\n$E")
    return "\n".join(lines) + ("\n" if lines else "")


KS_CAPS = {
    "parameters.configure": _kd_cap("Parameters pH/U/T、Κ/TDS/Sal/ρ/T、DO：测量参数（测量模式、传感器、温度、ΔmV、参考温度、温补、TDS因子、DO温度/盐度/气压补偿）"
                                    "与校准参数（缓冲液个数与系列、Special缓冲值、标准电导、校准点、报告）。",
                                    {"channel": {"type": "string", "enum": ["ph", "conductance", "do"]},
                                     "parameters": {"type": "object", "minProperties": 1}},
                                    {"changed": {"type": "array", "items": _KD_STR}}, {"channel": "ph", "parameters": {"buffer_type": "NIST"}},
                                    ["device.parameters", "device.channels"], "configuration"),
    "measured_values.configure": _kd_cap("Measured values：Data（print/save/print+save）、Criterion（immediately/time-dependent/when changed/drift-dependent）及其"
                                         "时间间隔、停止时间、Δ值、主通道、输出选项。", {"settings": _ks_obj(KS_VALUES)},
                                         {"changed": {"type": "array", "items": _KD_STR}}, {"settings": {"criterion": "time-dependent", "interval": 10}},
                                         ["device.values_settings"], "configuration"),
    "measured_values.record": _kd_cap("主对话框Print/save按钮：按Criterion记录测量值（双通道视图记两个通道）；定时/变化记录进行中再按则停止。",
                                      {"channels": {"type": "string", "enum": ["both", "selected"]}},
                                      {"recording": {"type": "boolean"}, "recorded": {"type": "integer"}}, {"channels": "both"},
                                      ["device.values", "device.recording"], optional=("channels",)),
    "measured_values.delete": _kd_cap("Values：delete all / delete last（不可恢复）。", {"which": {"type": "string", "enum": ["all", "last"]}},
                                      {"remaining": {"type": "integer"}}, {"which": "last"}, ["device.values"]),
    "measured_values.export": _kd_cap("Values：save as CSV / save as PC/LIMS——在仪器存储中生成MEASREPORT.CSV或PCLIMS文件（DO值不进PC/LIMS）。",
                                      {"format": {"type": "string", "enum": ["csv", "pclims"]}}, {"file": _KD_STR, "lines": {"type": "integer"}},
                                      {"format": "csv"}, ["device.files"]),
    "sensor.save": _kd_cap("Sensors：新建或修改传感器数据（名称、类型、订货号、序列号、斜率、pH(0)、电导池常数、DO相角、校准间隔、温度传感器、限值）。",
                           {"name": {"type": "string", "minLength": 1, "maxLength": 20},
                            "settings": {"type": "object", "properties": {f["key"]: _ks_schema(f) for f in KS_SENSOR_FIELDS},
                                         "additionalProperties": False}},
                           {"name": _KD_STR, "created": {"type": "boolean"}}, {"name": "pH 6.0228", "settings": {"type": "pH electrode"}},
                           ["device.sensors", "device.channels"], "configuration"),
    "sensor.delete": _kd_cap("Delete sensor：删除传感器（通道正在使用时拒绝）。", {"name": {"type": "string", "minLength": 1, "maxLength": 20}},
                             {"name": _KD_STR}, {"name": "pH 6.0228"}, ["device.sensors"], "configuration"),
    "calibration.start": _kd_cap("CAL键：单通道视图下开始所选通道的校准（pH缓冲液、电导池常数、DO 100 %/0 %）。", {}, {"channel": _KD_STR},
                                 {}, ["device.calibration"]),
    "calibration.next": _kd_cap("校准中的OK：测量当前缓冲液/标准（pH自动识别缓冲液，换液检查912-181）或确认输入后计算。", {},
                                {"stage": _KD_STR}, {}, ["device.calibration", "device.sensors", "device.channels"]),
    "calibration.accept": _kd_cap("校准结果的OK：存储结果（超限时仍可接受）并结束。", {}, {"stored": {"type": "boolean"}}, {},
                                  ["device.calibration", "device.sensors", "device.channels"]),
    "calibration.reject": _kd_cap("校准中的BACK：取消校准或拒绝超限结果，沿用原校准数据。", {}, {"stored": {"type": "boolean"}}, {},
                                  ["device.calibration"]),
    "sample.set_ids": _kd_cap("单通道主对话框的ID1、ID2（样品标识）。", {"id1": {"type": "string", "maxLength": 20}, "id2": {"type": "string", "maxLength": 20}},
                              {"id1": _KD_STR, "id2": _KD_STR}, {"id1": "Lot 7"}, ["device.ids"], "configuration", optional=("id1", "id2")),
    "settings.configure": _kd_cap("Configuration / Report / User：末位小数、DO信号强度、自动关机、关屏、亮度、语言、换行、打印机、用户、对话类型"
                                  "（Routine→Expert需密码）。", {"settings": _ks_obj(KS_SETTINGS), "password": {"type": "string", "maxLength": 20}},
                                  {"changed": {"type": "array", "items": _KD_STR}}, {"settings": {"brightness": 60}},
                                  ["device.settings"], "configuration", optional=("password",)),
    "password.change": _kd_cap("Configuration > Change password：旧密码、新密码、确认（出厂为Expert）。",
                               {"old": {"type": "string", "maxLength": 20}, "new": {"type": "string", "minLength": 1, "maxLength": 20},
                                "confirm": {"type": "string", "maxLength": 20}}, {"changed": {"type": "boolean"}},
                               {"old": "Expert", "new": "Lab1", "confirm": "Lab1"}, ["device.settings"], "configuration"),
    "clock.set": _kd_cap("Configuration的Date（YYYY-MM-DD）与Time（hh:mm:ss）。",
                         {"date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}, "time": {"type": "string", "pattern": r"^\d{2}:\d{2}:\d{2}$"}},
                         {"datetime": _KD_STR}, {"date": "2026-10-19", "time": "09:30:00"}, ["device.datetime"], "configuration", optional=("date", "time")),
    "device.reset": _kd_cap("启动时按UP+DOWN：删除全部用户数据，恢复出厂设置（p.86）。", {}, {"reset": {"type": "boolean"}}, {},
                            ["device.settings", "device.sensors", "device.values", "device.parameters", "device.values_settings"], "configuration"),
}
for _ks_c in KS_CAPS.values():
    _kd_units(_ks_c["input_schema"])

KS_SLOTS = {
    "device.status": ({"type": "string", "enum": ["off", "boot", "ready"]}, "仪器状态：关机、启动画面、就绪"),
    "device.channels": ({"type": "object"}, "各通道显示：测量模式下的数值与单位、温度、传感器、传感器状态（好/接近/超限）、距下次校准天数"),
    "device.view_channels": ({"type": "object"}, "主对话框的通道1（pH或DO）与通道2（电导）"),
    "device.sensor_names": ({"type": "array", "items": {"type": "object"}}, "传感器选择列表的行"),
    "device.sensor_list": ({"type": "array", "items": {"type": "object"}}, "传感器列表（名称、类型）"),
    "device.parameters": ({"type": "object"}, "pH、电导、DO通道的测量与校准参数"),
    "device.values_settings": ({"type": "object"}, "Measured values设置：Data、Criterion及其参数、输出选项"),
    "device.values": ({"type": "array", "items": {"type": "object"}}, "仪器存储的测量值（最多1000条）"),
    "device.recording": ({"anyOf": [{"type": "object"}, {"type": "null"}]}, "进行中的定时/变化/漂移记录"),
    "device.files": ({"type": "object"}, "仪器存储中生成的CSV与PC/LIMS文件（文件名→行数）"),
    "device.sensors": ({"type": "array", "items": {"type": "object"}}, "传感器列表及其数据（斜率、pH(0)、电导池常数、DO相角、校准日期、限值）"),
    "device.calibration": ({"anyOf": [{"type": "object"}, {"type": "null"}]}, "进行中的校准：通道、步骤、已测点、结果与限值判定"),
    "device.settings": ({"type": "object"}, "Configuration、Report与User设置"),
    "device.ids": ({"type": "object"}, "ID1、ID2"),
    "device.datetime": ({"type": "string"}, "设备时钟"),
    "device.display": ({"type": "string", "enum": ["on", "dim", "off"]}, "显示屏：亮、变暗、关闭（Turn off LCD after）"),
    "device.message": ({"anyOf": [{"type": "object"}, {"type": "null"}]}, "固件自行显示的最近消息（代码、文字、序号）"),
}


class _KSPanelHost:
    def __init__(self, host, instance_id):
        self.host, self.iid = host, instance_id
        self.n = 0

    def panel_read(self):
        h = self.host
        inst = h._instances[self.iid]
        slots = h._metadata["asset_types"][METROHM914_TYPE]["state_slots"]
        out = {k: v for k, v in h._values(inst).items() if slots[k]["visibility"] == "agent"}
        out.update(h._ks_public(inst))
        out.update({"host.powered": inst["power"], "host.power_epoch": inst["device"]["power_epoch"], "host.serial_number": "1914000001",
                    "host.program_version": KS_PROGRAM_VERSION, "host.printer": False, "host.battery": 100, "host.charging": False})
        return out

    def panel_invoke(self, capability_id, arguments):
        """A command of a panel gesture runs as the session that made the gesture (host._ks_caller, set by Metrohm914DeviceSession._ks_panel)."""
        self.n += 1
        who = getattr(self.host, "_ks_caller", None)
        session = self.host._ks_panel_session if who is None else Metrohm914DeviceSession(self.host, who)
        r = session.invoke(instance_id=self.iid, capability_id=capability_id, arguments=dict(arguments), request_id=f"panel-{self.iid}-{self.n}")
        if r["status"] != "succeeded":
            raise _kd_pu.PanelRejected(r["error"]["code"])
        return r["result"]

    def panel_time(self):
        return self.host.time_s


METROHM914_PANEL_PATH = KS_ASSET_DIR / "panel_ui.json"
_KS_LEGACY_CAPS = tuple(export_metrohm914_contract_metadata()["asset_types"][METROHM914_TYPE]["capabilities"])
METROHM914_PANEL_SPEC = (_kd_pu.load_spec(METROHM914_PANEL_PATH, capabilities=set(KS_CAPS) | set(_KS_LEGACY_CAPS))
                         if METROHM914_PANEL_PATH.is_file() and not _kd_os.environ.get("METROHM914_PANEL_BUILD") else None)
METROHM914_PANEL_CAPS = _kd_pu.capability_definitions(METROHM914_PANEL_SPEC) if METROHM914_PANEL_SPEC else {}


class Metrohm914DeviceRuntime(Metrohm914Runtime):
    """CHEM-005 runtime with the 914 firmware (inst['device']) and the front panel of each instance.  host_step evaluates the device's recording,
    automatic power-off and display time-outs at each common time."""

    def __init__(self, *, scene_adapter=None):
        super().__init__(scene_adapter=scene_adapter)
        self._panels = {}
        self.panel_capture_dir = None
        self._principals["__panel__"] = ("agent", None)
        self._principals["__firmware__"] = ("agent", None)
        self._logs["__panel__"], self._logs["__firmware__"] = [], []
        self._ks_panel_session = Metrohm914DeviceSession(self, "__panel__")
        self._ks_firmware = Metrohm914Session(self, "__firmware__")
        self._ks_fw_n = 0

    def create_instance(self, instance_id, asset_type_id=METROHM914_TYPE):
        with self._lock:
            super().create_instance(instance_id, asset_type_id)
            self._instances[instance_id]["device"] = _ks_new_device()
            if METROHM914_PANEL_SPEC is not None:
                self._panels[instance_id] = _kd_pu.PanelUI(METROHM914_PANEL_SPEC, _KSPanelHost(self, instance_id))

    def session(self, principal_id):
        if principal_id not in self._principals or principal_id.startswith("__") and principal_id != "__public_agent__":
            raise ValueError("Identity must be registered by trusted host")
        return Metrohm914DeviceSession(self, principal_id)

    def panel(self, instance_id):
        return self._panels[instance_id]

    def set_sensor_errors(self, instance_id, **errors):
        """Trusted host: phase-angle errors of the O2 Lumitrode (degrees, 0 = ideal, the default; a DO calibration corrects them) and the wear
        of its cap (0..1, lowers the signal intensity; 0 = new)."""
        if set(errors) - {"dphi0_deg", "dphi100_deg", "cap_wear"} or any(not _m914_number(v) or abs(v) > 5 for v in errors.values()) or \
                not 0.0 <= errors.get("cap_wear", 0.0) <= 1.0:
            raise ValueError("Known finite sensor errors within +-5 degrees (cap wear 0..1) required")
        with self._lock:
            self._instances[instance_id]["device"]["errors"].update({k: float(v) for k, v in errors.items()})
            self._instances[instance_id]["revision"] += 1

    def set_temperature_probe(self, instance_id, channel, kind, *, r25=30000.0, b_value=4100.0):
        """Trusted host: the physical temperature sensor of a channel's electrode (Pt1000 by default, or an NTC with R(25 degC) and B value);
        the meter reads it with the type set in the sensor record (p.63)."""
        if channel not in METROHM914_CHANNELS or kind not in ("Pt1000", "NTC") or not all(_m914_number(x) and x > 0 for x in (r25, b_value)):
            raise ValueError("Known channel, Pt1000 / NTC and positive R(25 degC) / B value required")
        with self._lock:
            self._instances[instance_id]["device"].setdefault("probes", {})[channel] = dict(kind=kind, r25=float(r25), b_value=float(b_value))
            self._instances[instance_id]["revision"] += 1

    def _sample(self, inst, time_s, sample):
        """CHEM-005 sample model, then the meter's own reading of the selected channel (_ks_meter): measurement.*, the compensation
        temperature, the CHEM-005 calibration raw value and records come from the same model as the screen."""
        Metrohm914Runtime._sample(self, inst, time_s, sample)
        dev = inst.get("device")
        inst["ideal"] = None
        if dev is None or inst["status"] != "UNSTABLE":
            return
        name = inst["selected"]
        ideal = dict(readings=dict(inst["readings"]), raw=inst["raw"], temp_k=inst["compensation_temperature"])
        inst["ideal"] = ideal
        m = _ks_meter(self, inst, dev, name, ideal)
        if m["value"] is None:
            return
        key = {"ph": "ph", "conductance": "conductance_s", "do": "do_kg_m3"}[name]
        inst["raw"], inst["compensation_temperature"] = m["pre"], m["temp_k"]
        lo, hi, _ = METROHM914_CHANNELS[name]
        if not _m914_number(m["value"]) or not lo <= m["value"] <= hi:
            inst["readings"][key], inst["readings"]["potential_v"], inst["status"] = None, None, "OUT_OF_RANGE"
            return
        v = m["value"]
        if name == "ph":
            v = float(_m914_Decimal(str(v)).quantize(_m914_Decimal("0.001"), rounding=_m914_ROUND_HALF_UP))
        inst["readings"][key] = v

    def _ks_fw(self, instance_id, cap, **args):
        self._ks_fw_n += 1
        r = self._ks_firmware.invoke(instance_id=instance_id, capability_id=cap, arguments=args, request_id=f"fw-{self._ks_fw_n}")
        if r["status"] != "succeeded":
            raise _KDReject(r["error"]["code"])
        return r["result"]

    def _ks_public(self, inst):
        dev, t = inst["device"], self.time_s
        cal = dev["cal"]
        chans = _ks_channels(self, inst, dev)
        return {"device.status": _ks_status(inst, dev, t), "device.channels": chans,
                "device.view_channels": dict(ch1=chans.get("ph") or chans.get("do"), ch2=chans.get("conductance"), one=chans.get(inst["selected"])),
                "device.parameters": _kd_copy.deepcopy(dev["params"]), "device.values_settings": dict(dev["values_cfg"]),
                "device.values": _kd_copy.deepcopy(dev["values"]), "device.recording": _kd_copy.deepcopy(dev["recording"]),
                "device.files": {k: len(v.splitlines()) for k, v in dev["files"].items()},
                "device.sensors": _kd_copy.deepcopy(dev["sensors"]), "device.calibration": _kd_copy.deepcopy(cal),
                "device.settings": dict(dev["settings"], dialog_locked=dev["settings"]["dialog_type"] == "Routine"),
                "device.ids": dict(dev["ids"]), "device.datetime": _ks_now_text(dev, t), "device.display": dev["lcd"],
                "device.message": None if dev["message"] is None else dict(code=dev["message"], text=KS_ERRORS.get(dev["message"], dev["message"]),
                                                                           seq=dev["message_seq"]),
                "device.sensor_names": [dict(value=s["name"], text=s["name"]) for s in dev["sensors"]],
                "device.sensor_list": [dict(value=s["name"], text=s["name"], type=s["type"]) for s in dev["sensors"]]}

    def host_step(self, time_s, *, inputs=None):
        """CHEM-005 host step, then the firmware at the new common time: recording, display time-outs, automatic power-off."""
        with self._lock:
            super().host_step(time_s, inputs=inputs)
            for iid, inst in self._instances.items():
                dev = inst.get("device")
                if dev is None or not inst["power"]:
                    continue
                before = _kd_copy.deepcopy(dev)
                t = self.time_s
                if dev["boot_until"] is not None and t >= dev["boot_until"] - 1e-9:
                    dev["boot_until"] = None
                self._ks_record_tick(iid, inst, inputs is not None and iid in inputs)
                inst = self._instances[iid]
                dev = inst["device"]
                st, idle = dev["settings"], t - dev["last_key_s"]
                lcd = st["lcd_off"]
                if lcd == "auto":
                    dev["lcd"] = "off" if idle >= 80.0 - 1e-9 else "dim" if idle >= 20.0 - 1e-9 else "on"
                elif lcd != "off":
                    dev["lcd"] = "off" if idle >= lcd * 60.0 - 1e-9 else "on"
                timed = dev["recording"] is not None and dev["recording"]["criterion"] == "time-dependent"
                if st["power_off"] is not None and not timed and idle >= st["power_off"] * 60.0 - 1e-9:
                    self._ks_fw(iid, "power.set", enabled=False)              # automatic power-off (p.68)
                    inst = self._instances[iid]
                    inst["device"].update(recording=None, cal=None, lcd="on")
                    continue
                if inst["device"] != before:
                    inst["revision"] += 1

    def _ks_record_tick(self, iid, inst, new_sample):
        dev, t = inst["device"], self.time_s
        rec = dev["recording"]
        if rec is None:
            return
        cfg = dev["values_cfg"]
        if rec["stop_at"] is not None and t >= rec["stop_at"] - 1e-9:
            dev["recording"] = None
            return
        chans = _ks_channels(self, inst, dev)
        ch, pv = _ks_primary(dev, cfg, chans)
        q, delta, limit = _ks_quantity(ch, pv, cfg)
        crit = rec["criterion"]
        take = False
        if crit == "time-dependent":
            take = t >= rec["next"] - 1e-9
        elif crit == "when changed" and new_sample and q is not None and rec["last"] is not None:
            take = abs(q - rec["last"]) > delta + 1e-12
        elif crit == "drift-dependent" and new_sample and q is not None:
            if rec["prev"] is not None and t > rec["prev"][0]:
                take = abs(q - rec["prev"][1]) / (t - rec["prev"][0]) * 60.0 < limit     # preset drift thresholds (p.60)
            rec["prev"] = (t, q)
        if take:
            v = _ks_record_value(self, inst, dev, rec["channels"])
            if v is not None and len(dev["values"]) < 1000:
                if cfg["data"] != "print":
                    dev["values"].append(v)
                rec["count"] += 1
                rec["last"] = q
            if crit == "time-dependent":
                rec["next"] = t + cfg["interval"]
            if crit == "drift-dependent":
                dev["recording"] = None

    def _capture(self, instance_id, display_id):
        data, w, h = self._panels[instance_id].png_bytes(display_id)
        sha = _kd_hashlib.sha256(data).hexdigest()
        d = _kd_Path(self.panel_capture_dir) if self.panel_capture_dir else _kd_Path(_kd_tempfile.gettempdir()) / "chemistry_sim_panel" / instance_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{display_id}_{sha[:16]}.png"
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.time_s, image=dict(path=str(path), format="png", width=w, height=h, sha256=sha))


def _ks_cal_result(dev, cal):
    """Slope / pH(0), cell constant or phase angles from the measured points; the limit check of the sensor (p.63-65, p.71, p.74, p.76)."""
    s = _ks_sensor_of(dev, cal["sensor"])
    ch = cal["channel"]
    pts = cal["points"]
    if ch == "ph":
        if len(pts) == 1:
            slope = s["slope"]
            u, tk, ph = pts[0]["u"], pts[0]["t_k"], pts[0]["ph"]
            ph0 = ph + u / (slope / 100.0 * KS_K * tk)
        else:
            xs, ys = [p["ph"] for p in pts], [p["u"] / (KS_K * p["t_k"]) for p in pts]       # potential in Nernst units
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
            slope = -b * 100.0
            ph0 = mx + my / -b
        res = dict(slope=slope, ph0=ph0, temperature_c=pts[-1]["t_k"] - 273.15,
                   dph=[p["ph"] - (ph0 - p["u"] / (slope / 100.0 * KS_K * p["t_k"])) for p in pts])
        out = (s["slope_limit"] and not s["slope_lo"] <= slope <= s["slope_hi"]) or (s["ph0_limit"] and not s["ph0_lo"] <= ph0 <= s["ph0_hi"])
    elif ch == "conductance":
        c = pts[0]["c"]
        res = dict(cell_constant=c, temperature_c=pts[0]["t_c"])
        out = s["c_limit"] and not s["c_lo"] <= c <= s["c_hi"]
    else:
        res = dict(dphi100=pts[0]["dphi"], dphi0=pts[1]["dphi"] if len(pts) > 1 else s["dphi0"])
        sat_std = pts[0].get("sat_std", 100.0)
        if abs(sat_std - 100.0) > 1e-9:                                    # the standard's saturation from the compensation entries: the phase
            tan0 = _m914_math.tan(_m914_math.radians(res["dphi0"]))        # angle that 100 % would give on the fitted Stern-Volmer line
            k = (tan0 / _m914_math.tan(_m914_math.radians(pts[0]["dphi"])) - 1.0) / (sat_std / 100.0)
            res["dphi100"] = _m914_math.degrees(_m914_math.atan(tan0 / (1.0 + k)))
        out = (s["dphi100_limit"] and not 15.0 <= res["dphi100"] <= 30.0) or (s["dphi0_limit"] and len(pts) > 1 and not 45.0 <= res["dphi0"] <= 60.0)
    return res, bool(out)


def _ks_store_cal(dev, cal, t, inst=None):
    """Store a calibration in the sensor record; the channel is then calibrated for the CHEM-005 slots too (probe.<ch>.calibrated and its
    time), and an earlier CHEM-005 single-point offset no longer applies (the new calibration replaces it)."""
    s = _ks_sensor_of(dev, cal["sensor"])
    r = cal["result"]
    s.update({k: r[k] for k in ("slope", "ph0", "cell_constant", "dphi100", "dphi0") if k in r})
    s["cal_date"], s["cal_temp"] = t, r.get("temperature_c")
    if inst is not None:
        inst["calibration"][cal["channel"]] = {"calibrated": True, "time": t, "offset": None}


class Metrohm914DeviceSession(Metrohm914Session):
    """CHEM-005 session plus the meter firmware commands, the device slots and panel.*; CHEM-005 envelopes, idempotency and error format."""

    @property
    def _ks_host(self):
        return self._Metrohm914Session__host

    def _visible_principal(self):
        return self._ks_host._principals[self._Metrohm914Session__principal][0]

    def describe(self, *, asset_type_id=None, instance_id=None):
        out = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if "capabilities" not in out:
            return out
        meta = export_metrohm914_device_metadata()["asset_types"][METROHM914_TYPE]
        out["capabilities"].update(_kd_copy.deepcopy(meta["device_capabilities"]))
        out["capabilities"].update({k: _kd_copy.deepcopy(v) for k, v in meta["panel_capabilities"].items()
                                    if v["visibility"] == "agent" or self._visible_principal() in ("evaluator", "debug")})
        out["state_slots"].update({k: v for k, v in meta["state_slots"].items() if k.startswith("device.")})
        out["asset_version"] = KS_ASSET_VERSION
        out["limitations"] = out["limitations"] + METROHM914_DEVICE_LIMITATIONS
        return out

    def read(self, *, instance_id, state_ids=None):
        host = self._ks_host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None or "device" not in inst:
                return super().read(instance_id=instance_id, state_ids=state_ids)
            dev_ids = [x for x in (state_ids or []) if isinstance(x, str) and x.startswith("device.")]
            if any(x not in KS_SLOTS for x in dev_ids):
                return self._error(instance_id, "INVALID_ARGUMENT")
            legacy = None if state_ids is None else [x for x in state_ids if x not in dev_ids]
            r = super().read(instance_id=instance_id, state_ids=legacy) if legacy is None or legacy else self._envelope(instance_id)
            if r["status"] != "succeeded":
                return r
            values = host._ks_public(inst)
            for sid in (KS_SLOTS if state_ids is None else dev_ids):
                r["observations"][sid] = {"value": _kd_copy.deepcopy(values[sid]), "unit": "1", "quality": "valid", "sample_time_s": host.time_s,
                                          "sample_window_s": None, "reason": None, "synthetic": True}
            return r

    def _ks_foreign(self, inst, cap, args):
        """Another caller's acquisition (acquisition.start), measured-value recording (REC) or calibration (CAL) runs and cap would stop, power off
        or change it (lead rule 2026-10-19: ACCESS_DENIED on the panel and the direct path); the evaluator / debug identities are unrestricted."""
        me, dev = self._Metrohm914Session__principal, inst["device"]
        if me in ("__panel__", "__firmware__") or self._ks_host._principals[me][0] in ("debug", "evaluator"):
            return False                                                      # the meter's own timers (no gesturing caller) are not foreign
        acq = inst["running"] and dev.get("acq_owner") not in (None, me)
        rec = dev["recording"] is not None and dev["recording"].get("owner") not in (None, me)
        cal = dev["cal"] is not None and dev["cal"].get("owner") not in (None, me)
        if cap == "power.set":
            return isinstance(args, dict) and args.get("enabled") is False and (acq or rec or cal)
        return (acq and cap == "acquisition.stop") or (rec and cap == "measured_values.record") or \
            (cal and cap in ("calibration.start", "calibration.next", "calibration.accept", "calibration.reject"))

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        host = self._ks_host
        with host._lock:
            inst = self._instance(instance_id)
            if inst is None or "device" not in inst:
                return super().invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)
            if capability_id not in KS_CAPS and capability_id not in METROHM914_PANEL_CAPS:
                was_on = inst["power"]
                key = (self._Metrohm914Session__principal, instance_id, request_id)
                replay = key in host._requests
                r = super().invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)
                if r["status"] == "succeeded" and not replay and capability_id == "power.set":
                    dev = host._instances[instance_id]["device"]
                    if arguments["enabled"] and not was_on:
                        dev.update(power_epoch=dev["power_epoch"] + 1, boot_until=host.time_s + KS_BOOT_S, last_key_s=host.time_s, lcd="on")
                    elif not arguments["enabled"]:
                        dev.update(recording=None, cal=None, boot_until=None)
                return r
            return super().invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)

    def _checked(self, instance_id, inst, cap, args, request_id):
        if "device" in inst and self._ks_foreign(inst, cap, args):
            return self._envelope(instance_id, request_id, status="rejected", error={"code": "ACCESS_DENIED", "message": KS_ERRORS["ACCESS_DENIED"],
                                                                                    "retryable": False, "side_effects": "none", "progress": None})
        if "device" not in inst:
            return self._ks_checked(instance_id, inst, cap, args, request_id)
        was = (inst["running"], inst["device"]["recording"] is not None)
        r = self._ks_checked(instance_id, inst, cap, args, request_id)
        now = self._ks_host._instances[instance_id]
        if r["status"] == "succeeded" and "device" in now:                    # runs belong to the caller that started them
            me = self._Metrohm914Session__principal
            if cap == "acquisition.start" and now["running"] and not was[0]:
                now["device"]["acq_owner"] = me
            elif cap == "measured_values.record" and now["device"]["recording"] is not None and not was[1]:
                now["device"]["recording"]["owner"] = me
            elif cap == "calibration.start" and now["device"]["cal"] is not None:
                now["device"]["cal"]["owner"] = me
        return r

    def _ks_checked(self, instance_id, inst, cap, args, request_id):
        if "device" not in inst or (cap not in KS_CAPS and cap not in METROHM914_PANEL_CAPS):
            return super()._checked(instance_id, inst, cap, args, request_id)
        host = self._ks_host
        if cap in METROHM914_PANEL_CAPS:
            return self._ks_panel(instance_id, cap, args, request_id)
        if _kd_pu.check_arguments(KS_CAPS[cap]["input_schema"], args) or not _kd_check(KS_CAPS[cap]["input_schema"], args):
            return self._error(instance_id, "INVALID_ARGUMENT", request_id)
        snapshot = _kd_copy.deepcopy(inst)
        try:
            result = self._ks_command(instance_id, cap, args)
        except _KDReject as exc:
            host._instances[instance_id] = snapshot
            if exc.code in KS_ERRORS:
                return self._envelope(instance_id, request_id, status="rejected", error={"code": exc.code, "message": KS_ERRORS[exc.code],
                                                                                           "retryable": False, "side_effects": "none", "progress": None})
            return self._error(instance_id, exc.code if exc.code in ("INVALID_ARGUMENT", "PRECONDITION_FAILED", "ACCESS_DENIED") else "PRECONDITION_FAILED",
                               request_id)
        host._instances[instance_id]["revision"] += 1
        return self._envelope(instance_id, request_id, result=result)

    def _ks_command(self, iid, cap, a):
        host = self._ks_host
        inst = host._instances[iid]
        dev, t = inst["device"], host.time_s
        if not inst["power"]:
            raise _KDReject("POWER_OFF")
        if _ks_status(inst, dev, t) == "boot" and cap != "device.reset":
            raise _KDReject("NOT_READY")
        if cap == "parameters.configure":
            ch, defs = a["channel"], {p["key"]: p for p in KS_PARAMS[a["channel"]]}
            for k, v in a["parameters"].items():
                if k not in defs or not _kd_check(_ks_schema(defs[k]), v):
                    raise _KDReject("INVALID_ARGUMENT")
                if defs[k]["type"] == "sensor" and _ks_sensor_of(dev, v) is None:
                    raise _KDReject("NOT_FOUND")
            dev["params"][ch].update(a["parameters"])
            return {"changed": sorted(a["parameters"])}
        if cap == "measured_values.configure":
            if dev["recording"] is not None:
                raise _KDReject("PRECONDITION_FAILED")
            dev["values_cfg"].update(a["settings"])
            return {"changed": sorted(a["settings"])}
        if cap == "measured_values.record":
            if dev["recording"] is not None:
                n = dev["recording"]["count"]
                dev["recording"] = None
                return {"recording": False, "recorded": n}
            cfg = dev["values_cfg"]
            v = _ks_record_value(host, inst, dev, a.get("channels", "both"))
            if v is None:
                raise _KDReject("NO_SIGNAL")
            crit = cfg["criterion"]
            if crit == "drift-dependent":
                ch, pv = _ks_primary(dev, cfg, _ks_channels(host, inst, dev))
                q = _ks_quantity(ch, pv, cfg)[0]
                dev["recording"] = dict(criterion=crit, channels=a.get("channels", "both"), stop_at=None, next=None, last=None, count=0,
                                        prev=(t, q) if q is not None else None)
                return {"recording": True, "recorded": 0}
            if len(dev["values"]) >= 1000:
                raise _KDReject("LIMIT")
            if cfg["data"] != "print":                                     # 'print' alone needs a printer, which is not connected
                dev["values"].append(v)
            if crit == "immediately":
                return {"recording": False, "recorded": 1}
            ch, pv = _ks_primary(dev, cfg, _ks_channels(host, inst, dev))
            dev["recording"] = dict(criterion=crit, channels=a.get("channels", "both"), count=1, prev=None,
                                    stop_at=None if cfg["stop_time"] is None else t + cfg["stop_time"],
                                    next=t + cfg["interval"], last=_ks_quantity(ch, pv, cfg)[0])
            return {"recording": True, "recorded": 1}
        if cap == "measured_values.delete":
            if not dev["values"]:
                raise _KDReject("NOT_FOUND")
            dev["values"] = [] if a["which"] == "all" else dev["values"][:-1]
            return {"remaining": len(dev["values"])}
        if cap == "measured_values.export":
            name, text = ("CSV/MEASREPORT.CSV", _ks_csv(dev)) if a["format"] == "csv" else ("PCLIMS/PCLIMS_1.UTF8", _ks_pclims(dev))
            dev["files"][name] = text
            return {"file": name, "lines": len(text.splitlines())}
        if cap == "sensor.save":
            s = _ks_sensor_of(dev, a["name"])
            created = s is None
            if created:
                if len(dev["sensors"]) >= 100:
                    raise _KDReject("LIMIT")
                s = _ks_sensor(a["name"], a.get("settings", {}).get("type", "pH electrode"))
                dev["sensors"].append(s)
            new_name = a.get("settings", {}).get("name") or a["name"]
            if new_name != a["name"] and _ks_sensor_of(dev, new_name) is not None:
                raise _KDReject("INVALID_ARGUMENT")
            s.update(a.get("settings", {}))
            s["name"] = new_name
            if new_name != a["name"]:
                for p in dev["params"].values():
                    if p.get("sensor") == a["name"]:
                        p["sensor"] = new_name
            return {"name": new_name, "created": created}
        if cap == "sensor.delete":
            s = _ks_sensor_of(dev, a["name"])
            if s is None:
                raise _KDReject("NOT_FOUND")
            if any(p.get("sensor") == a["name"] for p in dev["params"].values()) or s["type"] == "Oxygen sensor":
                raise _KDReject("SENSOR_IN_USE")
            dev["sensors"].remove(s)
            return {"name": a["name"]}
        if cap == "calibration.start":
            ch = inst["selected"]
            if not inst["installed"][ch]:
                raise _KDReject("NO_SIGNAL")
            sensor = dev["params"][ch].get("sensor", "O2 Lumitrode")
            dev["cal"] = dict(channel=ch, sensor=sensor, step=1, stage="measure", points=[], result=None, out_of_limits=False,
                              total=dev["params"]["ph"]["buffers"] if ch == "ph" else (2 if dev["params"]["do"]["points"] == "0%, 100%" else 1) if ch == "do" else 1)
            return {"channel": ch}
        if cap in ("calibration.next", "calibration.accept", "calibration.reject"):
            cal = dev["cal"]
            if cal is None:
                raise _KDReject("NO_CALIBRATION")
            if cap == "calibration.reject":
                dev["cal"] = None
                return {"stored": False}
            if cap == "calibration.accept":
                if cal["stage"] not in ("result", "limits"):
                    raise _KDReject("NO_CALIBRATION")
                if cal["stage"] == "limits":
                    _ks_store_cal(dev, cal, t, inst)
                dev["cal"] = None
                return {"stored": True}
            return {"stage": self._ks_cal_next(inst, dev, cal, t)}
        if cap == "sample.set_ids":
            dev["ids"].update(a)
            return dict(dev["ids"])
        if cap == "settings.configure":
            st = dict(dev["settings"])
            if a["settings"].get("dialog_type") == "Expert" and st["dialog_type"] == "Routine" and a.get("password") != dev["password"]:
                raise _KDReject("WRONG_PASSWORD")                         # unlocking needs the expert password (p.47, p.69)
            if st["dialog_type"] == "Routine" and set(a["settings"]) - {"dialog_type", "user", "line_feed", "printer"}:
                raise _KDReject("LOCKED")
            st.update(a["settings"])
            dev["settings"] = st
            return {"changed": sorted(a["settings"])}
        if cap == "password.change":
            if a["old"] != dev["password"]:
                raise _KDReject("WRONG_PASSWORD")
            if a["new"] != a["confirm"]:
                raise _KDReject("PASSWORD_MISMATCH")
            dev["password"] = a["new"]
            return {"changed": True}
        if cap == "clock.set":
            now = KS_EPOCH + _kd_dt.timedelta(seconds=t + dev["clock_offset_s"])
            try:
                d = _kd_dt.date.fromisoformat(a["date"]) if "date" in a else now.date()
                hh, mm, ss = (int(x) for x in a["time"].split(":")) if "time" in a else (now.hour, now.minute, now.second)
                target = _kd_dt.datetime(d.year, d.month, d.day, hh, mm, ss)
            except ValueError:
                raise _KDReject("INVALID_ARGUMENT") from None
            dev["clock_offset_s"] = (target - KS_EPOCH).total_seconds() - t
            return {"datetime": _ks_now_text(dev, t)}
        if cap == "device.reset":
            if _ks_status(inst, dev, t) != "boot":
                raise _KDReject("NOT_READY")                              # only during the start-up (p.86)
            fresh = _ks_new_device()
            fresh.update(power_epoch=dev["power_epoch"], boot_until=dev["boot_until"], last_key_s=t, errors=dev["errors"],
                         message="USER_DATA_DELETED", message_seq=dev["message_seq"] + 1)
            inst["device"] = fresh
            return {"reset": True}
        raise _KDReject("INVALID_ARGUMENT")

    def _ks_cal_next(self, inst, dev, cal, t):
        host = self._ks_host
        ch, p = cal["channel"], dev["params"][cal["channel"]]
        if cal["stage"] in ("result", "limits"):
            raise _KDReject("NO_CALIBRATION")
        chans = _ks_channels(host, inst, dev)
        c = chans.get(ch)
        r = _ks_channel_reading(host, inst, ch)
        if c is None or r is None or r["status"] not in ("UNSTABLE", "STABLE", "STALE", "OUT_OF_RANGE") or not c["raw"]:
            raise _KDReject("NO_SIGNAL")                                   # a calibration uses the raw signal (also below 0 % uncalibrated)
        if ch == "conductance" and cal["stage"] == "measure":
            cal["stage"] = "entries"                                       # reference temperature, standard and coefficient (p.73)
            return "entries"
        if ch == "do" and cal["stage"] == "measure" and cal["step"] == 1:
            cal["stage"] = "entries"                                       # temperature and air pressure compensation (p.76)
            return "entries"
        if ch == "ph":
            u = c["raw"]["potential_mv"] / 1000.0
            tk = r["temp_k"] if r["temp_k"] is not None else p["cal_temperature"] + 273.15
            t_c = tk - 273.15
            sensor = _ks_sensor_of(dev, cal["sensor"])
            if p["buffer_type"] == "Special":
                nominal = p["special"][cal["step"] - 1]
                ph = nominal
            else:
                tab = KS_BUFFERS[p["buffer_type"]]
                approx = sensor["ph0"] - u / (sensor["slope"] / 100.0 * KS_K * tk)
                cands = [(k, _ks_buffer_ph(tab, k, t_c)) for k in range(len(tab[0]))]
                cands = [(k, v) for k, v in cands if v is not None]
                if not cands:
                    raise _KDReject("NO_SIGNAL")
                k, ph = min(cands, key=lambda kv: abs(kv[1] - approx))       # automatic buffer recognition (p.88)
                nominal = tab[0][k]
            if any(abs(pt["nominal"] - nominal) < 1e-9 for pt in cal["points"]):
                dev["message"], dev["message_seq"] = "SAME_BUFFER", dev["message_seq"] + 1
                return "same_buffer"                                       # 912-181: exchange the buffer and continue with OK
            cal["points"].append(dict(u=u, t_k=tk, ph=ph, nominal=nominal))
        elif ch == "conductance":
            g = c["raw"]["conductance_s"]
            t_c = c["temperature_c"] if c["temperature_c"] is not None else p["cal_temperature"]
            kappa_t = p["standard"] * (1.0 + p["cal_tc"] / 100.0 * (t_c - p["cal_ref_temp"]))   # the standard at the measuring temperature
            cal["points"].append(dict(g=g, t_c=t_c, c=kappa_t / 1000.0 / g))
        else:
            point = dict(dphi=c["raw"]["dphi"], step=cal["step"])
            if cal["step"] == 1:                                           # 100 %: water-saturated air at the entered conditions (p.57, p.76)
                t_cal = p["cal_temp_comp"] if p["cal_temp_comp"] is not None else (c["temperature_c"] if c["temperature_c"] is not None else 25.0)
                p_cal = p["cal_press_comp"] if p["cal_press_comp"] is not None else 1013.25
                pw = _m914_math.exp(11.8571 - 3840.70 / (t_cal + 273.15) - 216961.0 / (t_cal + 273.15) ** 2) * 1013.25
                point["sat_std"] = 100.0 * (p_cal - pw) / (1013.25 - pw)
            cal["points"].append(point)
        if len(cal["points"]) < cal["total"]:
            cal["step"] += 1
            cal["stage"] = "measure"
            return "measure"
        cal["result"], out = _ks_cal_result(dev, cal)
        cal["out_of_limits"] = out
        cal["stage"] = "limits" if out else "result"
        if not out:
            _ks_store_cal(dev, cal, t, inst)                               # within the limits: stored, OK or 30 s back to the main dialog
        return cal["stage"]

    def _ks_panel(self, instance_id, cap, args, request_id):
        host = self._ks_host
        panel = host._panels.get(instance_id)
        if panel is None:
            return self._error(instance_id, "UNKNOWN_CAPABILITY", request_id)
        if _kd_pu.check_arguments(METROHM914_PANEL_CAPS[cap]["input_schema"], args):
            return self._error(instance_id, "INVALID_ARGUMENT", request_id)
        if METROHM914_PANEL_CAPS[cap]["visibility"] != "agent" and self._visible_principal() == "agent":
            return self._error(instance_id, "ACCESS_DENIED", request_id)
        try:
            if cap == "panel.capture":
                return self._envelope(instance_id, request_id, result=host._capture(instance_id, args["display_id"]))
            if cap == "panel.frame":
                return self._envelope(instance_id, request_id, result={"frame": panel.frame()})
            inst = host._instances[instance_id]
            dev = inst["device"]
            if inst["power"] and args["input_id"] != "onoff" and dev["lcd"] != "on":
                dev.update(lcd="on", last_key_s=host.time_s)                # a key other than On/Off only wakes the display (p.68)
                inst["revision"] += 1
                return self._envelope(instance_id, request_id, result={"accepted": False})
            caller, host._ks_caller = getattr(host, "_ks_caller", None), self._Metrohm914Session__principal   # commands run as this caller
            try:
                accepted = panel.press(args["input_id"], float(args.get("duration_s", 0.1)), held=args.get("held", ()))
            finally:
                host._ks_caller = caller
            inst = host._instances[instance_id]
            inst["device"].update(last_key_s=host.time_s, lcd="on")
        except _kd_pu.PanelError:
            return self._error(instance_id, "INVALID_ARGUMENT", request_id)
        return self._envelope(instance_id, request_id, result={"accepted": bool(accepted)})


METROHM914_DEVICE_LIMITATIONS = [
    "Meter firmware (Metrohm914DeviceRuntime): the two-channel display, measuring modes, calibrations, sensors, measured values, reports, "
    "configuration and user rights follow the 912/913/914 operating manual as a minimal device model; it is not Metrohm firmware.",
    "One measuring model (_ks_meter) for every path: the CHEM-005 measurement.* / electrochemistry.reading / records, the device channels, "
    "stored values and files.  Temperature: the physical probe of the channel (Pt1000 unless the trusted host sets an NTC with R(25 °C) / B) read "
    "with the sensor record's Temp. sensor / R(25 °C) / B value plus its temperature offset (manual temperature: the entered value).  pH from the "
    "electrode potential with the sensor's slope and pH(0); conductivity is the conductance times the stored cell constant with a linear "
    "temperature compensation (DIN EN 27888 approximated by 1.91 %/°C); salinity by PSS-78; DO through an O2 Lumitrode phase angle "
    "(Stern-Volmer, 53° / 22° at 0 % / 100 %, the sample's oxygen at standard air pressure) and the Benson-Krause saturation with the "
    "compensation entries (no barometer: Auto = 1013.25 mbar).  A CHEM-005 single-point offset adds to the meter value; a panel calibration "
    "replaces it and marks probe.<channel>.calibrated.  The 100 % DO calibration standard has 100 (p - pw) / (1013.25 - pw) % at the entered "
    "air pressure and temperature.  DO signal intensity = 4 x LED intensity x (1 - cap wear) % sets the 3-colour sensor status (thresholds "
    "assumed), shown with Signal intensity DO on.",
    "Phase-angle errors and cap wear of the DO sensor and the physical temperature probes are trusted-host parameters (set_sensor_errors, "
    "set_temperature_probe), ideal / Pt1000 by default; pH asymmetry comes from the host's reference potential. Realistic sensor tolerances are "
    "a later sim-to-real task.",
    "Printer reports and printing of measured values need a printer (absent: nothing is printed; Output date/time, headers and Calibration data "
    "shape that printed report); CSV / PC/LIMS files are generated in the instrument memory in fixed formats, their transfer needs a PC on USB "
    "(absent). Service/Diagnosis is for Metrohm service. The battery is shown full and not discharged. Language: German / Español / Français / "
    "Português translate the menu texts listed in the panel (our translations); 中文 is stored and reported with English texts (our manual "
    "contains no Chinese texts).",
]


def export_metrohm914_device_metadata():
    """CHEM-005 metadata plus the meter firmware and the front panel (device_capabilities, panel_capabilities, device.* slots)."""
    meta = export_metrohm914_contract_metadata()
    t = meta["asset_types"][METROHM914_TYPE]
    t["asset_version"] = KS_ASSET_VERSION
    for sid, (schema, meaning) in KS_SLOTS.items():
        t["state_slots"][sid] = {"unit": "1", "origin": "derived", "visibility": "agent", "access": "read_only", "value_schema": schema,
                                 "write_via": None, "read_binding": "Metrohm914DeviceSession.read(instance_id=..., state_ids=[state_id])",
                                 "sampling": {"mode": "instant", "time_domain": "simulation", "window": None}, "reference_conditions": None,
                                 "meaning_zh": meaning}
    every = sorted({e for c in KS_CAPS.values() for e in c["effects"]})
    t["device_capabilities"] = {name: {"availability": "implemented", "visibility": "agent", "kind": c["kind"], "description_zh": c["description_zh"],
                                       "input_schema": c["input_schema"], "output_schema": c["output_schema"], "example_arguments": [c["example_arguments"]],
                                       "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": c["description_zh"]} for sid in c["effects"]],
                                       "observes": list(c["effects"])[:6], "scene_backend_required": False,
                                       "binding": f"basic40_runtime_measure.Metrohm914DeviceSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"}
                                for name, c in KS_CAPS.items()}
    t["panel_capabilities"] = {name: {"availability": "implemented", "visibility": d["visibility"], "kind": d["kind"], "description_zh": d["function_zh"],
                                      "input_schema": d["input_schema"], "output_schema": d["output_schema"], "example_arguments": [d["example_arguments"]],
                                      "component_ids": d["component_ids"],
                                      "state_effects": [{"state_id": sid, "timing": "immediate", "description_zh": "手势调用的命令改变的量"}
                                                        for sid in (every if name not in ("panel.capture", "panel.frame") else [])],
                                      "observes": [], "scene_backend_required": False,
                                      "binding": f"basic40_runtime_measure.Metrohm914DeviceSession.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"}
                               for name, d in METROHM914_PANEL_CAPS.items()}
    t["parameters"].update({
        "boot_s": {"value": KS_BOOT_S, "unit": "s", "evidence": "engineering_assumption", "source": "p.33 'takes some time'"},
        "buffer_series": {"value": sorted(KS_BUFFERS), "unit": "1", "evidence": "public_document", "source": "manual p.88-99 tables 7-18"},
        "drift_criteria_per_min": {"value": dict(KS_DRIFT), "unit": "1", "evidence": "public_document", "source": "manual p.60"},
        "do_phase_angles_deg": {"value": list(KS_TRUE_DPHI), "unit": "deg", "evidence": "engineering_assumption", "source": "ideal sensor within the p.63 ranges"},
    })
    meta["limitations"] = meta["limitations"] + METROHM914_DEVICE_LIMITATIONS
    return meta
# <<< CHEM-005 Metrohm 914 meter firmware and front panel
