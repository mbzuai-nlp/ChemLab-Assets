"""CHEM-071 Hach DR6000 batch-scope synthetic runtime.

The public API filters private sample truth. The Python host and its scene
callback are trusted: this is not a Python introspection/filesystem sandbox.
No hardware or robot driver exists. All spectra are synthetic, instantaneous
fixtures with no calibrated noise, wavelength-error, concentration or kinetics
model. A host-owned clock is read, never advanced, by this adapter.
"""
import copy as _dr6000_copy
import json as _dr6000_json
import math as _dr6000_math
import threading as _dr6000_threading

HACH_DR6000_TYPE = "instrument.hach_dr6000"
HACH_DR6000_LID_MAX_RAD = _dr6000_math.pi / 2
HACH_DR6000_WAVELENGTH_MIN_M = 190e-9
HACH_DR6000_WAVELENGTH_MAX_M = 1100e-9


def _dr6000_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return _dr6000_math.isfinite(value)
    except (ValueError, OverflowError):
        return False


def _dr6000_object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def _dr6000_numeric(unit, minimum=None, maximum=None):
    result = {"type": "number", "x-unit": unit}
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    return result


def _dr6000_spectrum_schema():
    return _dr6000_object({
        "wavelength_m": {"type": "array", "items": _dr6000_numeric("m"), "minItems": 1, "maxItems": 10001},
        "absorbance": {"type": "array", "items": _dr6000_numeric("1"), "minItems": 1, "maxItems": 10001},
        "transmittance": {"type": "array", "items": _dr6000_numeric("1", 0), "minItems": 1, "maxItems": 10001},
        "signal": {"type": "array", "items": _dr6000_numeric("1"), "minItems": 1, "maxItems": 10001},
        "reported_mode": {"type": "string", "enum": ["absorbance", "transmittance"]},
        "synthetic": {"type": "boolean", "const": True},
        "quality": {"type": "string", "const": "estimated"},
        "axis_unit": {"type": "string", "const": "m"},
        "signal_unit": {"type": "string", "const": "1"},
        "sample_kind": {"type": "string", "enum": ["sample", "blank"]},
        "measurement_duration_s": {"type": "null"},
    })


def export_hach_dr6000_contract_metadata():
    """Return type metadata, schemas and assumptions, never live instance truth."""
    number = _dr6000_numeric
    wavelength = number("m", HACH_DR6000_WAVELENGTH_MIN_M, HACH_DR6000_WAVELENGTH_MAX_M)
    angle = number("rad", 0, HACH_DR6000_LID_MAX_RAD)
    boolean = {"type": "boolean"}
    text = {"type": "string"}
    nullable_text = {"anyOf": [text, {"type": "null"}]}
    definitions = {
        "power.set": ({"enabled": boolean}, {"enabled": boolean}, {"enabled": True},
            ["power.enabled"], ["chassis", "display"], [], False),
        "lid.set_angle": ({"angle_rad": angle}, {"angle_rad": angle, "closed": boolean}, {"angle_rad": HACH_DR6000_LID_MAX_RAD},
            ["lid.angle_rad", "lid.closed"], ["sample_lid"], ["A synchronous scene adapter must be registered."], True),
        "sample.load": ({"sample_id": text}, {"loaded": boolean, "sample_id": text}, {"sample_id": "synthetic_sample"},
            ["sample.loaded", "sample.id", "sample.kind", "sample.truth"], ["cuvette_slot", "cuvette_sample"],
            ["Lid angle >= pi/3 (assumed loading clearance).", "Slot empty and sample registered by trusted host.", "Synchronous scene adapter registered."], True),
        "sample.unload": ({}, {"loaded": boolean, "sample_id": {"type": "null"}}, {},
            ["sample.loaded", "sample.id", "sample.kind", "sample.truth"], ["cuvette_slot", "cuvette_sample"],
            ["Lid angle >= pi/3 (assumed loading clearance).", "Slot occupied.", "Synchronous scene adapter registered."], True),
        "spectroscopy.set_scan": ({"start_m": wavelength, "stop_m": wavelength, "step_m": number("m", 0.1e-9, 910e-9)},
            {"start_m": wavelength, "stop_m": wavelength, "step_m": number("m", 0.1e-9, 910e-9)},
            {"start_m": 190e-9, "stop_m": 1100e-9, "step_m": 10e-9},
            ["scan.start_m", "scan.stop_m", "scan.step_m"], ["display", "optical_module"],
            ["Power enabled.", "start_m <= stop_m; generated grid <= 10001 points."], False),
        "spectroscopy.set_wavelength": ({"wavelength_m": wavelength}, {"wavelength_m": wavelength}, {"wavelength_m": 500e-9},
            ["measurement.wavelength_m", "scan.start_m", "scan.stop_m"], ["display", "optical_module"], ["Power enabled.", "Sets a single-point acquisition by setting scan start and stop to this wavelength."], False),
        "spectroscopy.set_mode": ({"mode": {"type": "string", "enum": ["absorbance", "transmittance", "concentration"]}},
            {"mode": {"type": "string", "enum": ["absorbance", "transmittance"]}}, {"mode": "absorbance"},
            ["measurement.mode"], ["display"], ["Power enabled.", "Concentration returns unsupported because no calibration exists."], False),
        "spectroscopy.measure": ({}, {"spectrum": _dr6000_spectrum_schema()}, {},
            ["measurement.spectrum", "measurement.count"], ["optical_module", "cuvette_slot", "display"],
            ["Power enabled.", "Lid fully closed (angle <= 1e-6 rad, simulation tolerance).", "A registered sample or blank is loaded."], False),
    }
    capabilities = {}
    for cid, (inputs, outputs, example, effects, components, preconditions, scene_required) in definitions.items():
        components = list(dict.fromkeys(components + ["display"]))
        if cid != "spectroscopy.measure":
            effects = effects + ["measurement.spectrum"]
        capabilities[cid] = {
            "input_schema": _dr6000_object(inputs), "output_schema": _dr6000_object(outputs),
            "example_arguments": [example], "state_ids": effects, "component_ids": components,
            "preconditions": preconditions, "scene_required": scene_required, "availability": "implemented",
            "invocation": "HachDR6000.invoke(instance_id=..., capability_id=" + repr(cid) + ", arguments=..., request_id=...)",
            "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "REQUEST_CONFLICT", "UNSUPPORTED", "DEVICE_ERROR"],
            "rejection_side_effects": "none", "scene_failure_side_effects": "unknown",
            "measurement_cache_semantics": "Preparation/control commands invalidate the previous synthetic spectrum; measurement count remains cumulative.",
        }
    for cid in ("spectroscopy.calibrate", "spectroscopy.configure_method", "spectroscopy.time_scan", "hardware.connect", "robot.load_sample"):
        capabilities[cid] = {"input_schema": _dr6000_object({}), "output_schema": _dr6000_object({}),
            "example_arguments": [{}], "availability": "unsupported", "state_ids": [], "component_ids": ["optical_module"],
            "preconditions": [], "scene_required": False, "invocation": None,
            "errors": ["UNSUPPORTED"], "rejection_side_effects": "none"}
    slots = {}
    def add(sid, schema, unit, origin, route=None, visibility="agent"):
        slots[sid] = {"value_schema": schema, "unit": unit, "origin": origin,
            "visibility": visibility, "access": "read_write" if route else "read_only", "write_via": route}
    add("power.enabled", boolean, "1", "setpoint", {"capability_id": "power.set", "parameter": "enabled"})
    add("lid.angle_rad", angle, "rad", "scene", {"capability_id": "lid.set_angle", "parameter": "angle_rad"})
    add("lid.closed", boolean, "1", "derived")
    add("sample.loaded", boolean, "1", "scene")
    add("sample.id", nullable_text, "1", "scene")
    add("sample.kind", {"anyOf": [{"type": "string", "enum": ["sample", "blank"]}, {"type": "null"}]}, "1", "scene")
    add("sample.truth", {"anyOf": [{"type": "object"}, {"type": "null"}]}, "1", "process_truth", visibility="debug")
    for sid in ("scan.start_m", "scan.stop_m", "scan.step_m"):
        add(sid, wavelength if sid != "scan.step_m" else number("m", 0.1e-9, 910e-9), "m", "setpoint")
    add("measurement.wavelength_m", wavelength, "m", "setpoint", {"capability_id": "spectroscopy.set_wavelength", "parameter": "wavelength_m"})
    add("measurement.mode", {"type": "string", "enum": ["absorbance", "transmittance"]}, "1", "setpoint", {"capability_id": "spectroscopy.set_mode", "parameter": "mode"})
    add("measurement.spectrum", {"anyOf": [_dr6000_spectrum_schema(), {"type": "null"}]}, "1", "derived")
    add("measurement.count", {"type": "integer", "minimum": 0, "x-unit": "1"}, "1", "derived")
    return {
        "asset_type_id": HACH_DR6000_TYPE, "asset_version": "1.0.0", "catalog_id": "CHEM-071", "model": "Hach DR6000",
        "capabilities": capabilities, "state_slots": slots,
        "parameters": {
            "wavelength_min_m": {"value": 190e-9, "unit": "m", "evidence": "catalog"},
            "wavelength_max_m": {"value": 1100e-9, "unit": "m", "evidence": "catalog"},
            "wavelength_accuracy_m": {"value": 1e-9, "unit": "m", "evidence": "catalog", "conditions": "200e-9..900e-9 m; not simulated noise"},
            "spectral_bandwidth_m": {"value": 2e-9, "unit": "m", "evidence": "catalog", "conditions": "Recorded specification, no optical convolution model"},
            "photometric_range_absorbance": {"value": [-3.0, 3.0], "unit": "1", "evidence": "catalog", "conditions": "200e-9..900e-9 m only; not imposed outside this range"},
            "measurement_duration_s": {"value": None, "unit": "s", "evidence": "unknown"},
            "cuvette_path_length_m": {"value": None, "unit": "m", "evidence": "unknown"},
            "maximum_lid_angle_rad": {"value": HACH_DR6000_LID_MAX_RAD, "unit": "rad", "evidence": "assumption"},
            "loading_clearance_rad": {"value": _dr6000_math.pi / 3, "unit": "rad", "evidence": "assumption"},
            "lid_closed_tolerance_rad": {"value": 1e-6, "unit": "rad", "evidence": "assumption"},
            "scan_minimum_step_m": {"value": 0.1e-9, "unit": "m", "evidence": "assumption", "conditions": "API workload bound, not vendor scan increment"},
            "default_scan_m": {"value": [190e-9, 1100e-9, 10e-9], "unit": "m", "evidence": "assumption"},
            "synthetic_gaussian_peaks": {"value": [[275e-9, 24e-9, 0.75], [520e-9, 65e-9, 1.2]], "unit": "mixed: m,m,1", "evidence": "assumption", "conditions": "Arbitrary center,width,amplitude fixtures; no chemical identification or calibration"},
        },
        "scene_binding": {"lid": "lid_hinge.rotation_euler.x = -lid.angle_rad", "sample": "cuvette_sample.hide_render/hide_viewport = not sample.loaded", "display": "display_readout.data.body; powered state and latest synthetic result summary", "authority": "process", "direction": "process_to_scene"},
        "limits": ["API filtering only; no protection against trusted host introspection or filesystem access.",
            "No hardware, robot manipulation, calibration, concentration, user-method library, time scan, lamp noise or wavelength-error model.",
            "Scene callback success is not by itself evaluated-pose verification.", "Samples are virtual fixtures; no physical transfer or liquid conservation is claimed."],
    }


# 2026-10 template hook: NIST UV / PhotochemCAD / Hach method path (scripts/basic40_templates/hach_dr6000.py).
_dr6000_export_metadata_base = export_hach_dr6000_contract_metadata


def export_hach_dr6000_contract_metadata():
    from scripts.basic40_templates import hach_dr6000 as _dr6000_templates
    return _dr6000_templates.extend_metadata(_dr6000_export_metadata_base())


class HachDR6000:
    """Trusted per-instance host with an agent-only direct API."""
    def __init__(self, instance_id, *, scene_adapter=None, clock=None):
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("A nonempty instance_id is required")
        if scene_adapter is not None and not callable(scene_adapter):
            raise ValueError("scene_adapter must be callable")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be a callable owned by the host")
        self.instance_id = instance_id
        self._scene_adapter = scene_adapter
        self._clock = clock or (lambda: 0.0)
        self._state = {"power.enabled": False, "lid.angle_rad": 0.0, "lid.closed": True,
            "sample.loaded": False, "sample.id": None, "sample.kind": None, "sample.truth": None,
            "scan.start_m": 190e-9, "scan.stop_m": 1100e-9, "scan.step_m": 10e-9,
            "measurement.wavelength_m": 500e-9, "measurement.mode": "absorbance", "measurement.spectrum": None, "measurement.count": 0}
        self._revision = 0
        self._samples = {}
        self._principals = {"__agent__": "agent"}
        self._requests = {}
        self._lock = _dr6000_threading.RLock()
        self._metadata = export_hach_dr6000_contract_metadata()
        self._agent = HachDR6000Session(self, "__agent__")

    def register_principal(self, principal_id, *, visibility):
        """Trusted host identity provisioning, never a capability argument."""
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Invalid or already registered principal")
            self._principals[principal_id] = visibility

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Principal must first be registered by the trusted host")
        return HachDR6000Session(self, principal_id)

    def register_sample(self, sample_id, *, kind="sample", peaks=None, baseline_absorbance=None, truth=None):
        """Register an arbitrary synthetic cuvette; coefficients are not vendor facts."""
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples or kind not in ("sample", "blank"):
                raise ValueError("A new sample ID and sample/blank kind are required")
            if peaks is None:
                peaks = [] if kind == "blank" else [(275e-9, 24e-9, 0.75), (520e-9, 65e-9, 1.2)]
            if not isinstance(peaks, (list, tuple)) or len(peaks) > 32:
                raise ValueError("At most 32 synthetic peaks are supported")
            for peak in peaks:
                if not isinstance(peak, (list, tuple)) or len(peak) != 3 or not all(_dr6000_number(x) for x in peak):
                    raise ValueError("Peaks require finite center_m,width_m,amplitude_1 values")
                if not HACH_DR6000_WAVELENGTH_MIN_M <= peak[0] <= HACH_DR6000_WAVELENGTH_MAX_M or not 1e-12 <= peak[1] <= 1e-3 or not -3 <= peak[2] <= 3:
                    raise ValueError("Synthetic peak outside supported fixture envelope")
            baseline = (0.0 if kind == "blank" else 0.02) if baseline_absorbance is None else baseline_absorbance
            if not _dr6000_number(baseline) or not -3 <= baseline <= 3 or (truth is not None and not isinstance(truth, dict)):
                raise ValueError("Invalid synthetic baseline or truth dictionary")
            self._samples[sample_id] = {"kind": kind, "peaks": _dr6000_copy.deepcopy(peaks), "baseline": float(baseline), "truth": _dr6000_copy.deepcopy(truth or {})}

    def register_template_sample(self, sample_id, *, template_id, concentration_mol_m3=None, concentration_kg_m3=None, path_length_m=0.01, truth=None):
        """Trusted host: cuvette backed by a NIST UV / PhotochemCAD spectrum (mol/m^3) or a Hach program (kg/m^3 analyte)."""
        from scripts.basic40_templates import hach_dr6000 as _dr6000_templates
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples or (truth is not None and not isinstance(truth, dict)):
                raise ValueError("A new sample ID and optional truth dictionary are required")
            template = _dr6000_templates.validate_sample(template_id, concentration_mol_m3=concentration_mol_m3,
                                                         concentration_kg_m3=concentration_kg_m3, path_length_m=path_length_m)
            self._samples[sample_id] = {"kind": "sample", "peaks": [], "baseline": 0.0, "truth": _dr6000_copy.deepcopy(truth or {}), "template": template}

    def describe(self, *, asset_type_id=None, instance_id=None):
        return self._agent.describe(asset_type_id=asset_type_id, instance_id=instance_id)

    def read(self, *, instance_id=None, state_ids=None):
        return self._agent.read(instance_id=instance_id, state_ids=state_ids)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        return self._agent.invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)

    def write(self, *, state_id, value, request_id, instance_id=None):
        return self._agent.write(instance_id=instance_id, state_id=state_id, value=value, request_id=request_id)

    def debug_snapshot(self):
        with self._lock:
            return _dr6000_copy.deepcopy({"state": self._state, "state_revision": self._revision, "registered_samples": self._samples})

    def sync_scene(self):
        """Explicit trusted-host resynchronization; does not advance time."""
        with self._lock:
            if self._scene_adapter is None:
                raise RuntimeError("No scene adapter registered")
            self._scene_adapter(self.instance_id, self._public_scene_state(self._state))

    @staticmethod
    def _public_scene_state(state):
        return _dr6000_copy.deepcopy({key: value for key, value in state.items() if key != "sample.truth"})

    def _time(self):
        value = self._clock()
        if not _dr6000_number(value) or value < 0:
            raise ValueError("Host clock must return finite nonnegative seconds")
        return float(value)

    def _spectrum(self, state):
        if "template" in self._samples[state["sample.id"]]:  # None => scan misses the template's defined wavelengths
            from scripts.basic40_templates import hach_dr6000 as _dr6000_templates
            return _dr6000_templates.spectrum(self._samples[state["sample.id"]]["template"], state, self._samples[state["sample.id"]]["kind"])
        start, stop, step = (state[k] for k in ("scan.start_m", "scan.stop_m", "scan.step_m"))
        count = int(_dr6000_math.floor((stop - start) / step + 1e-8)) + 1
        axis = [start + i * step for i in range(count)]
        if axis and abs(axis[-1] - stop) <= 1e-15:
            axis[-1] = stop
        sample = self._samples[state["sample.id"]]
        absorbance = [sample["baseline"] + sum(amplitude * _dr6000_math.exp(-0.5 * ((wavelength - center) / width) ** 2)
            for center, width, amplitude in sample["peaks"]) for wavelength in axis]
        transmittance = [10.0 ** (-value) for value in absorbance]
        return {"wavelength_m": axis, "absorbance": absorbance, "transmittance": transmittance,
            "signal": absorbance[:] if state["measurement.mode"] == "absorbance" else transmittance[:],
            "reported_mode": state["measurement.mode"], "synthetic": True, "quality": "estimated",
            "axis_unit": "m", "signal_unit": "1", "sample_kind": sample["kind"], "measurement_duration_s": None}


class HachDR6000Session:
    """Identity-bound filtered adapter. Use only host-created sessions."""
    def __init__(self, host, principal_id):
        self._host = host
        self._principal_id = principal_id

    def _visible(self, sid):
        slot = self._host._metadata["state_slots"].get(sid)
        return bool(slot and (slot["visibility"] == "agent" or self._host._principals[self._principal_id] in ("debug", "evaluator")))

    def _target(self, instance_id):
        return instance_id is None or (isinstance(instance_id, str) and instance_id == self._host.instance_id)

    def _response(self, status="succeeded", *, request_id=None, result=None, observations=None, code=None, side_effects="none"):
        messages = {"UNKNOWN_INSTANCE": "Instance is unavailable.", "UNKNOWN_CAPABILITY": "Capability is unavailable.",
            "INVALID_ARGUMENT": "Arguments do not match the declared schema or supported range.",
            "PRECONDITION_FAILED": "Declared operation preconditions are not satisfied.",
            "ACCESS_DENIED": "Requested state is unavailable or access is denied.",
            "REQUEST_CONFLICT": "Request ID was already used with a different operation.",
            "UNSUPPORTED": "This operation has no implementation in the requested execution layer.",
            "DEVICE_ERROR": "Scene synchronization failed; process state was not committed."}
        return {"protocol_version": "1.0.0", "instance_id": self._host.instance_id,
            "request_id": request_id if isinstance(request_id, str) else None, "operation_id": None,
            "status": status, "state_revision": self._host._revision,
            "clock": {"domain": "simulation", "time_s": self._host._time()},
            "result": _dr6000_copy.deepcopy(result or {}), "observations": _dr6000_copy.deepcopy(observations or {}),
            "error": None if code is None else {"code": code, "message": messages[code], "retryable": False, "side_effects": side_effects}}

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self._host._lock:
            if asset_type_id is not None and instance_id is not None:
                return self._response("rejected", code="INVALID_ARGUMENT")
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != HACH_DR6000_TYPE:
                return self._response("rejected", code="ACCESS_DENIED")
            metadata = _dr6000_copy.deepcopy(self._host._metadata)
            metadata["state_slots"] = {sid: slot for sid, slot in metadata["state_slots"].items() if self._visible(sid)}
            metadata["capabilities"] = {cid: cap for cid, cap in metadata["capabilities"].items()
                if cap["availability"] == "implemented" and (not cap["scene_required"] or self._host._scene_adapter is not None)}
            for cap in metadata["capabilities"].values():
                cap["state_ids"] = [sid for sid in cap["state_ids"] if self._visible(sid)]
            metadata["instance_id"] = self._host.instance_id if asset_type_id is None else None
            metadata["adapter"] = "HachDR6000Session"
            metadata["scene_adapter_registered"] = self._host._scene_adapter is not None
            return metadata

    def read(self, *, instance_id=None, state_ids=None):
        with self._host._lock:
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            if state_ids is None:
                state_ids = [sid for sid in self._host._state if self._visible(sid)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids):
                return self._response("rejected", code="INVALID_ARGUMENT")
            if any(not self._visible(sid) for sid in state_ids):
                return self._response("rejected", code="ACCESS_DENIED")
            now = self._host._time()
            observations = {}
            for sid in state_ids:
                slot, value = self._host._metadata["state_slots"][sid], self._host._state[sid]
                observations[sid] = {"value": _dr6000_copy.deepcopy(value), "unit": slot["unit"],
                    "quality": "unavailable" if value is None else ("estimated" if slot["origin"] == "derived" else "valid"),
                    "sample_time_s": now, "sample_window_s": None}
                if value is None:
                    observations[sid]["reason"] = "No value has been established for this state."
            return self._response(observations=observations)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        """Cache all well-formed local requests, including rejected attempts."""
        host = self._host
        with host._lock:
            cacheable = self._target(instance_id) and isinstance(capability_id, str) and isinstance(arguments, dict) and isinstance(request_id, str) and bool(request_id)
            signature = None
            if cacheable:
                try:
                    signature = _dr6000_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
                except (TypeError, ValueError, OverflowError):
                    cacheable = False
            key = (self._principal_id, request_id) if cacheable else None
            if cacheable and key in host._requests:
                previous, response = host._requests[key]
                if previous == signature:
                    return _dr6000_copy.deepcopy(response)
                return self._response("rejected", request_id=request_id, code="REQUEST_CONFLICT")
            response = self._invoke_once(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)
            if cacheable:
                host._requests[key] = (signature, _dr6000_copy.deepcopy(response))
            return response

    def _invoke_once(self, *, capability_id, arguments, request_id, instance_id=None):
        host = self._host
        with host._lock:
            if not self._target(instance_id):
                return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id:
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            try:
                signature = _dr6000_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            except (TypeError, ValueError, OverflowError):
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            key = (self._principal_id, request_id)
            if key in host._requests:
                old_signature, response = host._requests[key]
                if old_signature == signature:
                    return _dr6000_copy.deepcopy(response)
                return self._response("rejected", request_id=request_id, code="REQUEST_CONFLICT")
            cap = host._metadata["capabilities"].get(capability_id)
            if cap is None:
                return self._response("rejected", request_id=request_id, code="UNKNOWN_CAPABILITY")
            if cap["availability"] == "unsupported":
                return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
            properties = cap["input_schema"]["properties"]
            if set(arguments) != set(properties):
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            for name, spec in properties.items():
                value = arguments[name]
                valid = ((spec.get("type") == "number" and _dr6000_number(value)) or
                    (spec.get("type") == "boolean" and isinstance(value, bool)) or
                    (spec.get("type") == "string" and isinstance(value, str) and bool(value)))
                if not valid or ("minimum" in spec and value < spec["minimum"]) or ("maximum" in spec and value > spec["maximum"]) or ("enum" in spec and value not in spec["enum"]):
                    return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            if capability_id == "spectroscopy.set_mode" and arguments["mode"] == "concentration":
                return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
            if cap["scene_required"] and host._scene_adapter is None:
                return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
            state = _dr6000_copy.deepcopy(host._state)
            if capability_id.startswith("spectroscopy.") and not state["power.enabled"]:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            if capability_id == "power.set":
                state["power.enabled"] = arguments["enabled"]
                result = {"enabled": arguments["enabled"]}
            elif capability_id == "lid.set_angle":
                state["lid.angle_rad"] = float(arguments["angle_rad"])
                state["lid.closed"] = state["lid.angle_rad"] <= 1e-6
                result = {"angle_rad": state["lid.angle_rad"], "closed": state["lid.closed"]}
            elif capability_id in ("sample.load", "sample.unload"):
                if state["lid.angle_rad"] < _dr6000_math.pi / 3:
                    return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
                loading = capability_id == "sample.load"
                if loading:
                    sample = host._samples.get(arguments["sample_id"])
                    if state["sample.loaded"] or sample is None:
                        return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
                    state.update({"sample.loaded": True, "sample.id": arguments["sample_id"], "sample.kind": sample["kind"], "sample.truth": _dr6000_copy.deepcopy(sample["truth"])})
                else:
                    if not state["sample.loaded"]:
                        return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
                    state.update({"sample.loaded": False, "sample.id": None, "sample.kind": None, "sample.truth": None})
                result = {"loaded": loading, "sample_id": state["sample.id"]}
            elif capability_id == "spectroscopy.set_scan":
                if arguments["start_m"] > arguments["stop_m"] or int(_dr6000_math.floor((arguments["stop_m"] - arguments["start_m"]) / arguments["step_m"] + 1e-8)) + 1 > 10001:
                    return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
                for name, value in arguments.items():
                    state["scan." + name] = float(value)
                result = {key: float(value) for key, value in arguments.items()}
            elif capability_id == "spectroscopy.set_wavelength":
                state["measurement.wavelength_m"] = float(arguments["wavelength_m"])
                state["scan.start_m"] = state["measurement.wavelength_m"]
                state["scan.stop_m"] = state["measurement.wavelength_m"]
                result = {"wavelength_m": state["measurement.wavelength_m"]}
            elif capability_id == "spectroscopy.set_mode":
                state["measurement.mode"] = arguments["mode"]
                result = {"mode": arguments["mode"]}
            elif capability_id == "template.read":  # public reference lookup; no state or scene effect
                from scripts.basic40_templates import hach_dr6000 as _dr6000_templates
                record = _dr6000_templates.template_read(arguments["substance_id"])
                if record is None:
                    return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
                return self._response(request_id=request_id, result=record)
            elif capability_id == "spectroscopy.measure":
                if not state["lid.closed"] or not state["sample.loaded"]:
                    return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
                spectrum = host._spectrum(state)
                if spectrum is None:
                    return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
                state["measurement.spectrum"] = spectrum
                state["measurement.count"] += 1
                result = {"spectrum": spectrum}
            else:
                return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
            if capability_id != "spectroscopy.measure":
                state["measurement.spectrum"] = None
            if host._scene_adapter is not None:
                try:
                    host._scene_adapter(host.instance_id, host._public_scene_state(state))
                except Exception:
                    return self._response("failed", request_id=request_id, code="DEVICE_ERROR", side_effects="unknown")
            host._state = state
            host._revision += 1
            response = self._response(request_id=request_id, result=result)
            host._requests[key] = (signature, _dr6000_copy.deepcopy(response))
            return response

    def write(self, *, state_id, value, request_id, instance_id=None):
        with self._host._lock:
            if not self._target(instance_id):
                return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
            if not isinstance(state_id, str) or not self._visible(state_id):
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            route = self._host._metadata["state_slots"][state_id]["write_via"]
            if route is None:
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)


def apply_hach_dr6000_to_scene(instance_id, updates, *, object_prefix=""):
    """Blender callback: process owns q; +q maps to local-X rotation -q.

    This helper assumes one explicitly selected model per callback. For multiple
    instances the trusted host must provide a distinct object_prefix mapping.
    It preflights targets, then changes the lid, sample visibility and text.
    If Blender reports failure it restores the previous values before raising.
    External animation, constraints and physics must not write the same targets.
    """
    import bpy
    required = ("lid_hinge", "cuvette_sample", "display_readout")
    objects = {name: bpy.data.objects.get(object_prefix + name) for name in required}
    if any(obj is None for obj in objects.values()) or objects["display_readout"].type != "FONT":
        raise RuntimeError("Required DR6000 scene targets are unavailable")
    if not isinstance(updates, dict) or "lid.angle_rad" not in updates or "sample.loaded" not in updates:
        raise ValueError("Complete DR6000 scene state is required")
    angle = updates["lid.angle_rad"]
    if not _dr6000_number(angle) or not 0 <= angle <= HACH_DR6000_LID_MAX_RAD or not isinstance(updates["sample.loaded"], bool):
        raise ValueError("Invalid scene state")
    lid, sample, display = (objects[name] for name in required)
    if lid.animation_data or len(lid.constraints) or lid.rigid_body is not None:
        raise RuntimeError("Lid has a competing animation/constraint/rigid-body authority")
    old = {"rotation_mode": lid.rotation_mode, "rotation_euler": lid.rotation_euler.copy(),
        "hide_viewport": sample.hide_viewport, "hide_render": sample.hide_render,
        "hide_set": sample.hide_get(), "body": display.data.body}
    try:
        lid.rotation_mode = "XYZ"
        lid.rotation_euler.x = -float(angle)
        sample.hide_viewport = not updates["sample.loaded"]
        sample.hide_render = not updates["sample.loaded"]
        sample.hide_set(not updates["sample.loaded"])
        if not updates.get("power.enabled", False):
            display.data.body = "OFF"
        else:
            mode = updates.get("measurement.mode", "absorbance")
            spectrum = updates.get("measurement.spectrum")
            if spectrum:
                label = "Abs" if spectrum["reported_mode"] == "absorbance" else "T"
                display.data.body = "SYNTHETIC\n{} | {} pt".format(label, len(spectrum["wavelength_m"]))
            else:
                label = "Abs" if mode == "absorbance" else "T"
                display.data.body = "READY\n{} | {:.0f} nm".format(label, updates.get("measurement.wavelength_m", 500e-9) * 1e9)
        bpy.context.view_layer.update()
    except Exception:
        lid.rotation_mode = old["rotation_mode"]
        lid.rotation_euler = old["rotation_euler"]
        sample.hide_viewport = old["hide_viewport"]
        sample.hide_render = old["hide_render"]
        sample.hide_set(old["hide_set"])
        display.data.body = old["body"]
        bpy.context.view_layer.update()
        raise


"""CHEM-072 append-only runtime extension source; synthetic educational fixture."""
import copy as _ftir_copy
import json as _ftir_json
import math as _ftir_math
import threading as _ftir_threading

JASCO_FTIR4700_TYPE = "instrument.jasco_ftir4700"
JASCO_FTIR4700_LID_MAX_RAD = _ftir_math.pi / 2
JASCO_FTIR4700_WAVENUMBER_MIN_M_INV = 35000.0
JASCO_FTIR4700_WAVENUMBER_MAX_M_INV = 780000.0
JASCO_FTIR4700_MIN_RESOLUTION_M_INV = 40.0


def _ftir_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return _ftir_math.isfinite(value)
    except (ValueError, OverflowError):
        return False


def _ftir_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _ftir_numeric(unit, minimum=None, maximum=None, integer=False):
    result = {"type": "integer" if integer else "number", "x-unit": unit}
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    return result


def _ftir_series(unit, count):
    return {"type": "array", "items": _ftir_numeric(unit), "minItems": count, "maxItems": count}


def _ftir_background_schema():
    return _ftir_object({"wavenumber_m_inv": _ftir_series("m^-1", 512),
        "relative_intensity": _ftir_series("1", 512), "synthetic": {"type": "boolean", "const": True},
        "quality": {"type": "string", "const": "estimated"}, "axis_unit": {"type": "string", "const": "m^-1"},
        "signal_unit": {"type": "string", "const": "1"}, "measurement_duration_s": {"type": "null"}})


def _ftir_spectrum_schema():
    return _ftir_object({"wavenumber_m_inv": _ftir_series("m^-1", 512),
        "absorbance": _ftir_series("1", 512), "transmittance": _ftir_series("1", 512),
        "interferogram": _ftir_object({"optical_path_difference_m": _ftir_series("m", 257),
            "relative_intensity": _ftir_series("1", 257), "synthetic": {"type": "boolean", "const": True},
            "axis_unit": {"type": "string", "const": "m"}, "signal_unit": {"type": "string", "const": "1"}}),
        "reported_mode": {"type": "string", "enum": ["absorbance", "transmittance", "interferogram"]},
        "synthetic": {"type": "boolean", "const": True}, "quality": {"type": "string", "const": "estimated"},
        "axis_unit": {"type": "string", "const": "m^-1"}, "signal_unit": {"type": "string", "const": "1"},
        "resolution_m_inv": _ftir_numeric("m^-1", 40), "scan_count": _ftir_numeric("1", 1, 1024, integer=True),
        "spectral_resolution_applied": {"type": "boolean", "const": False},
        "scan_averaging_applied": {"type": "boolean", "const": False},
        "measurement_duration_s": {"type": "null"}})


def export_jasco_ftir4700_contract_metadata():
    """Type metadata only, never live instance state or sample coefficients."""
    boolean = {"type": "boolean"}
    text = {"type": "string"}
    nullable_text = {"anyOf": [text, {"type": "null"}]}
    angle = _ftir_numeric("rad", 0, JASCO_FTIR4700_LID_MAX_RAD)
    wavenumber = _ftir_numeric("m^-1", 35000, 780000)
    mode = {"type": "string", "enum": ["none", "atr", "transmission"]}
    method = {"start_wavenumber_m_inv": wavenumber, "stop_wavenumber_m_inv": wavenumber,
        "resolution_m_inv": _ftir_numeric("m^-1", 40), "scan_count": _ftir_numeric("1", 1, 1024, integer=True),
        "output_mode": {"type": "string", "enum": ["absorbance", "transmittance", "interferogram"]}}
    example_method = {"start_wavenumber_m_inv": 35000.0, "stop_wavenumber_m_inv": 780000.0,
        "resolution_m_inv": 400.0, "scan_count": 16, "output_mode": "absorbance"}
    background_effects = ["background.ready", "background.spectrum"]
    definitions = {
        "power.set": ({"enabled": boolean}, {"enabled": boolean}, {"enabled": True},
            ["power.enabled"] + background_effects, ["chassis", "indicator_power"], [], False),
        "lid.set_angle": ({"angle_rad": angle}, {"angle_rad": angle, "closed": boolean}, {"angle_rad": JASCO_FTIR4700_LID_MAX_RAD},
            ["lid.angle_rad", "lid.closed"], ["sample_lid"], ["A synchronous scene adapter is registered."], True),
        "accessory.install": ({"mode": mode}, {"mode": mode}, {"mode": "atr"},
            ["accessory.mode"] + background_effects, ["accessory_slot", "accessory_atr", "accessory_transmission"],
            ["Lid angle >= pi/3 (assumed clearance).", "Sample slot empty.", "A synchronous scene adapter is registered."], True),
        "sample.load": ({"sample_id": text}, {"loaded": boolean, "sample_id": text}, {"sample_id": "synthetic_sample"},
            ["sample.loaded", "sample.id", "sample.phase", "sample.truth"], ["accessory_slot", "sample_holder_sample"],
            ["Lid angle >= pi/3 (assumed clearance); slot empty; accessory installed.",
             "Sample registered by trusted host; gas fixtures require transmission accessory.", "A synchronous scene adapter is registered."], True),
        "sample.unload": ({}, {"loaded": boolean, "sample_id": {"type": "null"}}, {},
            ["sample.loaded", "sample.id", "sample.phase", "sample.truth"], ["accessory_slot", "sample_holder_sample"],
            ["Lid angle >= pi/3 (assumed clearance); slot occupied.", "A synchronous scene adapter is registered."], True),
        "method.configure": (method, method, example_method,
            ["method." + key for key in method] + background_effects, ["optical_module"],
            ["Power enabled.", "start_wavenumber_m_inv < stop_wavenumber_m_inv.",
             "Resolution and scan count are stored settings only; fixed synthetic grid is independent of them."], False),
        "background.acquire": ({}, {"background": _ftir_background_schema()}, {},
            background_effects + ["background.count"], ["optical_module", "accessory_slot"],
            ["Power enabled; lid fully closed (<= 1e-6 rad assumed tolerance); accessory installed; no sample loaded."], False),
        "measure": ({}, {"spectrum": _ftir_spectrum_schema()}, {},
            ["measurement.spectrum", "measurement.count"], ["optical_module", "accessory_slot"],
            ["Power enabled; lid fully closed; accessory installed; registered sample loaded; current-method background ready."], False),
    }
    capabilities = {}
    for cid, (inputs, outputs, example, effects, components, preconditions, scene_required) in definitions.items():
        if cid != "measure":
            effects = effects + ["measurement.spectrum"]
        capabilities[cid] = {"input_schema": _ftir_object(inputs), "output_schema": _ftir_object(outputs),
            "example_arguments": [example], "state_ids": effects, "component_ids": components,
            "preconditions": preconditions, "scene_required": scene_required, "availability": "implemented",
            "invocation": "JascoFTIR4700.invoke(instance_id=..., capability_id=" + repr(cid) + ", arguments=..., request_id=...)",
            "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "REQUEST_CONFLICT", "UNSUPPORTED", "DEVICE_ERROR"],
            "rejection_side_effects": "none", "scene_failure_side_effects": "unknown",
            "measurement_cache_semantics": "Preparation invalidates the previous result; power/method/accessory commands also invalidate background. Lid and sample commands preserve background. Counts remain cumulative."}
    for cid in ("library.match", "fast_scan.start", "hardware.connect", "robot.load_sample"):
        capabilities[cid] = {"input_schema": _ftir_object({}), "output_schema": _ftir_object({}),
            "example_arguments": [{}], "availability": "unsupported", "state_ids": [], "component_ids": ["optical_module"],
            "preconditions": [], "scene_required": False, "invocation": None,
            "errors": ["UNSUPPORTED"], "rejection_side_effects": "none"}
    slots = {}
    def add(sid, schema, unit, origin, route=None, visibility="agent"):
        slots[sid] = {"value_schema": schema, "unit": unit, "origin": origin, "visibility": visibility,
            "access": "read_write" if route else "read_only", "write_via": route}
    add("power.enabled", boolean, "1", "setpoint", {"capability_id": "power.set", "parameter": "enabled"})
    add("lid.angle_rad", angle, "rad", "scene", {"capability_id": "lid.set_angle", "parameter": "angle_rad"})
    add("lid.closed", boolean, "1", "derived")
    add("accessory.mode", mode, "1", "scene", {"capability_id": "accessory.install", "parameter": "mode"})
    add("sample.loaded", boolean, "1", "scene")
    add("sample.id", nullable_text, "1", "scene")
    add("sample.phase", {"anyOf": [{"type": "string", "enum": ["solid", "liquid", "gas"]}, {"type": "null"}]}, "1", "scene")
    add("sample.truth", {"anyOf": [{"type": "object"}, {"type": "null"}]}, "1", "process_truth", visibility="debug")
    for key, schema in method.items():
        add("method." + key, schema, "m^-1" if key.endswith("m_inv") else "1", "setpoint")
    add("background.ready", boolean, "1", "derived")
    add("background.spectrum", {"anyOf": [_ftir_background_schema(), {"type": "null"}]}, "1", "derived")
    add("background.count", _ftir_numeric("1", 0, integer=True), "1", "derived")
    add("measurement.spectrum", {"anyOf": [_ftir_spectrum_schema(), {"type": "null"}]}, "1", "derived")
    add("measurement.count", _ftir_numeric("1", 0, integer=True), "1", "derived")
    return {"asset_type_id": JASCO_FTIR4700_TYPE, "asset_version": "1.0.0", "catalog_id": "CHEM-072", "model": "JASCO FT/IR-4700",
        "capabilities": capabilities, "state_slots": slots,
        "parameters": {
            "wavenumber_min_m_inv": {"value": 35000, "unit": "m^-1", "evidence": "catalog"},
            "wavenumber_max_m_inv": {"value": 780000, "unit": "m^-1", "evidence": "catalog"},
            "highest_resolution_m_inv": {"value": 40, "unit": "m^-1", "evidence": "catalog", "conditions": "0.4 cm^-1 highest resolution; setpoint validation only, not a lineshape model"},
            "signal_to_noise_ratio": {"value": 35000, "unit": "1", "evidence": "catalog", "conditions": "Conditional vendor specification; source conditions must be preserved, not simulated noise"},
            "optional_fast_scan_frequency_hz": {"value": 10, "unit": "Hz", "evidence": "catalog", "conditions": "Optional fast scan at 16 cm^-1 = 1600 m^-1; unsupported by runtime"},
            "measurement_duration_s": {"value": None, "unit": "s", "evidence": "unknown"},
            "sample_path_length_m": {"value": None, "unit": "m", "evidence": "unknown"},
            "atr_penetration_depth_m": {"value": None, "unit": "m", "evidence": "unknown"},
            "maximum_lid_angle_rad": {"value": JASCO_FTIR4700_LID_MAX_RAD, "unit": "rad", "evidence": "assumption"},
            "loading_clearance_rad": {"value": _ftir_math.pi / 3, "unit": "rad", "evidence": "assumption"},
            "lid_closed_tolerance_rad": {"value": 1e-6, "unit": "rad", "evidence": "assumption"},
            "scan_count_limit": {"value": 1024, "unit": "1", "evidence": "assumption", "conditions": "Software input bound, not a vendor limit; no scan averaging model"},
            "synthetic_spectrum_points": {"value": 512, "unit": "1", "evidence": "assumption"},
            "synthetic_interferogram_points": {"value": 257, "unit": "1", "evidence": "assumption"},
            "default_method": {"value": example_method, "unit": "mixed", "evidence": "assumption"},
            "synthetic_gaussian_peaks": {"value": [[171000, 5000, 0.8], [295000, 9000, 0.45], [335000, 15000, 0.55]], "unit": "mixed: m^-1,m^-1,1", "evidence": "assumption", "conditions": "Arbitrary teaching fixtures, no chemical identification or concentration calibration"}},
        "scene_binding": {"lid": "joint_sample_lid.rotation_euler.x = -lid.angle_rad",
            "accessory_atr": "accessory_atr.hide_render/hide_viewport = accessory.mode != atr",
            "accessory_transmission": "accessory_transmission.hide_render/hide_viewport = accessory.mode != transmission",
            "sample": "sample_holder_sample.hide_render/hide_viewport = not sample.loaded",
            "power": "indicator_power dedicated power_indicator material diffuse_color and Principled BSDF Base Color",
            "authority": "process", "direction": "process_to_scene", "callback": "apply_jasco_ftir4700_blender_scene(instance_id, updates, object_prefix='')"},
        "limits": ["Synthetic instantaneous fixture spectra and cosine-synthesized interferograms; no calibrated optics, FT processing of measured data, scan averaging, or acquisition duration.",
            "No spectral library matching, optional 10 Hz fast scan, robot or hardware adapter.",
            "API truth filtering assumes trusted Python host and filesystem; not an introspection sandbox.",
            "Samples/accessories are virtual slot representations; no robotic transfer, ATR pressure, purge dynamics or conservation solver.",
            "The synchronous scene callback preflights and restores its own targets on failure; arbitrary external callbacks may have unknown scene side effects."]}


# 2026-10 template hook: spectral_library/ir_spectra.json path (scripts/basic40_templates/jasco_ftir4700.py).
_ftir_export_metadata_base = export_jasco_ftir4700_contract_metadata


def export_jasco_ftir4700_contract_metadata():
    from scripts.basic40_templates import jasco_ftir4700 as _ftir_templates
    return _ftir_templates.extend_metadata(_ftir_export_metadata_base())


class JascoFTIR4700:
    """Independent, per-instance synthetic process; shared host clock is read only."""
    def __init__(self, instance_id, *, scene_adapter=None, clock=None):
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("A nonempty instance ID is required")
        if scene_adapter is not None and not callable(scene_adapter):
            raise ValueError("scene_adapter must be callable")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")
        self.instance_id = instance_id
        self._scene_adapter = scene_adapter
        self._clock = clock or (lambda: 0.0)
        self._state = {"power.enabled": False, "lid.angle_rad": 0.0, "lid.closed": True,
            "accessory.mode": "none", "sample.loaded": False, "sample.id": None, "sample.phase": None, "sample.truth": None,
            "method.start_wavenumber_m_inv": 35000.0, "method.stop_wavenumber_m_inv": 780000.0,
            "method.resolution_m_inv": 400.0, "method.scan_count": 16, "method.output_mode": "absorbance",
            "background.ready": False, "background.spectrum": None, "background.count": 0,
            "measurement.spectrum": None, "measurement.count": 0}
        self._revision = 0
        self._samples = {}
        self._principals = {"__agent__": "agent"}
        self._requests = {}
        self._lock = _ftir_threading.RLock()
        self._metadata = export_jasco_ftir4700_contract_metadata()
        self._agent = JascoFTIR4700Session(self, "__agent__")

    def register_principal(self, principal_id, *, visibility):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ("agent", "debug", "evaluator"):
                raise ValueError("Invalid or already registered principal")
            self._principals[principal_id] = visibility

    def session(self, principal_id):
        if not isinstance(principal_id, str) or principal_id not in self._principals:
            raise ValueError("Principal must first be registered by the trusted host")
        return JascoFTIR4700Session(self, principal_id)

    def register_sample(self, sample_id, *, phase="solid", peaks=None, baseline_absorbance=None, truth=None):
        """Trusted host initialization; no agent capability permits truth mutation."""
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples or phase not in ("solid", "liquid", "gas"):
                raise ValueError("A new sample ID and supported phase are required")
            peaks = [(171000, 5000, 0.8), (295000, 9000, 0.45), (335000, 15000, 0.55)] if peaks is None else peaks
            if not isinstance(peaks, (list, tuple)) or len(peaks) > 32:
                raise ValueError("At most 32 synthetic peaks are supported")
            for peak in peaks:
                if not isinstance(peak, (list, tuple)) or len(peak) != 3 or not all(_ftir_number(value) for value in peak):
                    raise ValueError("Peaks require finite center_m_inv,width_m_inv,amplitude_1")
                if not 35000 <= peak[0] <= 780000 or not 1 <= peak[1] <= 745000 or not 0 <= peak[2] <= 3:
                    raise ValueError("Peak exceeds the synthetic fixture envelope")
            baseline = 0.01 if baseline_absorbance is None else baseline_absorbance
            if not _ftir_number(baseline) or not 0 <= baseline <= 3 or (truth is not None and not isinstance(truth, dict)):
                raise ValueError("Invalid synthetic baseline or truth dictionary")
            self._samples[sample_id] = {"phase": phase, "peaks": _ftir_copy.deepcopy(peaks), "baseline": float(baseline), "truth": _ftir_copy.deepcopy(truth or {})}

    def register_template_sample(self, sample_id, *, substance_id, phase, peak_absorbance=1.0, truth=None):
        """Trusted host: one spectral_library compound; measure returns its NIST IR spectrum at the method resolution."""
        from scripts.basic40_templates import jasco_ftir4700 as _ftir_templates
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples or (truth is not None and not isinstance(truth, dict)):
                raise ValueError("A new sample ID and optional truth dictionary are required")
            template = _ftir_templates.validate_sample(substance_id, phase=phase, peak_absorbance=peak_absorbance)
            self._samples[sample_id] = {"phase": phase, "peaks": [], "baseline": 0.0, "truth": _ftir_copy.deepcopy(truth or {}), "template": template}

    def describe(self, *, asset_type_id=None, instance_id=None):
        return self._agent.describe(asset_type_id=asset_type_id, instance_id=instance_id)

    def read(self, *, instance_id=None, state_ids=None):
        return self._agent.read(instance_id=instance_id, state_ids=state_ids)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        return self._agent.invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)

    def write(self, *, state_id, value, request_id, instance_id=None):
        return self._agent.write(instance_id=instance_id, state_id=state_id, value=value, request_id=request_id)

    def debug_snapshot(self):
        with self._lock:
            return _ftir_copy.deepcopy({"state": self._state, "state_revision": self._revision, "registered_samples": self._samples})

    def sync_scene(self):
        with self._lock:
            if self._scene_adapter is None:
                raise RuntimeError("No scene adapter registered")
            self._scene_adapter(self.instance_id, self._public_scene_state(self._state))

    @staticmethod
    def _public_scene_state(state):
        return _ftir_copy.deepcopy({key: value for key, value in state.items() if key != "sample.truth"})

    def _time(self):
        value = self._clock()
        if not _ftir_number(value) or value < 0:
            raise ValueError("Host clock must return finite nonnegative seconds")
        return float(value)

    @staticmethod
    def _axis(state):
        start, stop = state["method.start_wavenumber_m_inv"], state["method.stop_wavenumber_m_inv"]
        return [start + (stop - start) * index / 511 for index in range(512)]

    def _background(self, state):
        return {"wavenumber_m_inv": self._axis(state), "relative_intensity": [1.0] * 512,
            "synthetic": True, "quality": "estimated", "axis_unit": "m^-1", "signal_unit": "1", "measurement_duration_s": None}

    def _spectrum(self, state):
        if "template" in self._samples[state["sample.id"]]:  # None => configured range misses the reference coverage
            from scripts.basic40_templates import jasco_ftir4700 as _ftir_templates
            return _ftir_templates.spectrum(self._samples[state["sample.id"]]["template"], state)
        axis = self._axis(state)
        sample = self._samples[state["sample.id"]]
        absorbance = [sample["baseline"] + sum(amplitude * _ftir_math.exp(-0.5 * ((number - center) / width) ** 2)
            for center, width, amplitude in sample["peaks"]) for number in axis]
        transmittance = [10.0 ** -value for value in absorbance]
        opd = [(index - 128) / (2 * axis[-1]) for index in range(257)]
        interferogram = [sum(signal * _ftir_math.cos(2 * _ftir_math.pi * number * path)
            for number, signal in zip(axis, transmittance)) / 512 for path in opd]
        return {"wavenumber_m_inv": axis, "absorbance": absorbance, "transmittance": transmittance,
            "interferogram": {"optical_path_difference_m": opd, "relative_intensity": interferogram,
                "synthetic": True, "axis_unit": "m", "signal_unit": "1"},
            "reported_mode": state["method.output_mode"], "synthetic": True, "quality": "estimated",
            "axis_unit": "m^-1", "signal_unit": "1", "resolution_m_inv": state["method.resolution_m_inv"],
            "scan_count": state["method.scan_count"], "spectral_resolution_applied": False,
            "scan_averaging_applied": False, "measurement_duration_s": None}


class JascoFTIR4700Session:
    """Identity-bound API; privileged sessions can only be provisioned by host."""
    def __init__(self, host, principal_id):
        self._host = host
        self._principal_id = principal_id

    def _visible(self, sid):
        slot = self._host._metadata["state_slots"].get(sid)
        return bool(slot and (slot["visibility"] == "agent" or self._host._principals[self._principal_id] in ("debug", "evaluator")))

    def _target(self, instance_id):
        return instance_id is None or (isinstance(instance_id, str) and instance_id == self._host.instance_id)

    def _response(self, status="succeeded", *, request_id=None, result=None, observations=None, code=None, side_effects="none"):
        messages = {"UNKNOWN_INSTANCE": "Instance is unavailable.", "UNKNOWN_CAPABILITY": "Capability is unavailable.",
            "INVALID_ARGUMENT": "Arguments do not match the declared schema or supported range.",
            "PRECONDITION_FAILED": "Declared operation preconditions are not satisfied.",
            "ACCESS_DENIED": "Requested state is unavailable or access is denied.",
            "REQUEST_CONFLICT": "Request ID was already used with a different operation.",
            "UNSUPPORTED": "This operation has no implementation in the requested execution layer.",
            "DEVICE_ERROR": "Scene synchronization failed; process state was not committed."}
        return {"protocol_version": "1.0.0", "instance_id": self._host.instance_id,
            "request_id": request_id if isinstance(request_id, str) else None, "operation_id": None,
            "status": status, "state_revision": self._host._revision,
            "clock": {"domain": "simulation", "time_s": self._host._time()},
            "result": _ftir_copy.deepcopy(result or {}), "observations": _ftir_copy.deepcopy(observations or {}),
            "error": None if code is None else {"code": code, "message": messages[code], "retryable": False, "side_effects": side_effects}}

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self._host._lock:
            if asset_type_id is not None and instance_id is not None:
                return self._response("rejected", code="INVALID_ARGUMENT")
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != JASCO_FTIR4700_TYPE:
                return self._response("rejected", code="ACCESS_DENIED")
            metadata = _ftir_copy.deepcopy(self._host._metadata)
            metadata["state_slots"] = {sid: slot for sid, slot in metadata["state_slots"].items() if self._visible(sid)}
            metadata["capabilities"] = {cid: cap for cid, cap in metadata["capabilities"].items()
                if cap["availability"] == "implemented" and (not cap["scene_required"] or self._host._scene_adapter is not None)}
            for cap in metadata["capabilities"].values():
                cap["state_ids"] = [sid for sid in cap["state_ids"] if self._visible(sid)]
            metadata["instance_id"] = self._host.instance_id if asset_type_id is None else None
            metadata["adapter"] = "JascoFTIR4700Session"
            metadata["scene_adapter_registered"] = self._host._scene_adapter is not None
            return metadata

    def read(self, *, instance_id=None, state_ids=None):
        with self._host._lock:
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            if state_ids is None:
                state_ids = [sid for sid in self._host._state if self._visible(sid)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids):
                return self._response("rejected", code="INVALID_ARGUMENT")
            if any(not self._visible(sid) for sid in state_ids):
                return self._response("rejected", code="ACCESS_DENIED")
            now = self._host._time()
            observations = {}
            for sid in state_ids:
                slot, value = self._host._metadata["state_slots"][sid], self._host._state[sid]
                observations[sid] = {"value": _ftir_copy.deepcopy(value), "unit": slot["unit"],
                    "quality": "unavailable" if value is None else ("estimated" if slot["origin"] == "derived" else "valid"),
                    "sample_time_s": now, "sample_window_s": None}
                if value is None:
                    observations[sid]["reason"] = "No value has been established for this state."
            return self._response(observations=observations)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        host = self._host
        with host._lock:
            cacheable = self._target(instance_id) and isinstance(capability_id, str) and isinstance(arguments, dict) and isinstance(request_id, str) and bool(request_id)
            signature = None
            if cacheable:
                try:
                    signature = _ftir_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
                except (TypeError, ValueError, OverflowError):
                    cacheable = False
            key = (self._principal_id, request_id) if cacheable else None
            if cacheable and key in host._requests:
                previous, response = host._requests[key]
                if previous == signature:
                    return _ftir_copy.deepcopy(response)
                return self._response("rejected", request_id=request_id, code="REQUEST_CONFLICT")
            response = self._invoke_once(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)
            if cacheable:
                host._requests[key] = (signature, _ftir_copy.deepcopy(response))
            return response

    def _invoke_once(self, *, capability_id, arguments, request_id, instance_id=None):
        host = self._host
        if not self._target(instance_id):
            return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
        if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id:
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        try:
            _ftir_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
        except (TypeError, ValueError, OverflowError):
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        cap = host._metadata["capabilities"].get(capability_id)
        if cap is None:
            return self._response("rejected", request_id=request_id, code="UNKNOWN_CAPABILITY")
        if cap["availability"] == "unsupported":
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        properties = cap["input_schema"]["properties"]
        if set(arguments) != set(properties):
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        for name, spec in properties.items():
            value = arguments[name]
            valid = ((spec.get("type") == "number" and _ftir_number(value)) or
                (spec.get("type") == "integer" and isinstance(value, int) and not isinstance(value, bool)) or
                (spec.get("type") == "boolean" and isinstance(value, bool)) or
                (spec.get("type") == "string" and isinstance(value, str) and bool(value)))
            if not valid or ("minimum" in spec and value < spec["minimum"]) or ("maximum" in spec and value > spec["maximum"]) or ("enum" in spec and value not in spec["enum"]):
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        if cap["scene_required"] and host._scene_adapter is None:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        if capability_id == "template.read":  # public reference lookup; no state or scene effect
            from scripts.basic40_templates import jasco_ftir4700 as _ftir_templates
            record = _ftir_templates.template_read(arguments["substance_id"])
            if record is None:
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            return self._response(request_id=request_id, result=record)
        state = _ftir_copy.deepcopy(host._state)
        def reject():
            return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
        if capability_id in ("method.configure", "background.acquire", "measure") and not state["power.enabled"]:
            return reject()
        if capability_id == "power.set":
            state["power.enabled"] = arguments["enabled"]
            result = {"enabled": arguments["enabled"]}
        elif capability_id == "lid.set_angle":
            state["lid.angle_rad"] = float(arguments["angle_rad"])
            state["lid.closed"] = state["lid.angle_rad"] <= 1e-6
            result = {"angle_rad": state["lid.angle_rad"], "closed": state["lid.closed"]}
        elif capability_id == "accessory.install":
            if state["lid.angle_rad"] < _ftir_math.pi / 3 or state["sample.loaded"]:
                return reject()
            state["accessory.mode"] = arguments["mode"]
            result = {"mode": arguments["mode"]}
        elif capability_id in ("sample.load", "sample.unload"):
            if state["lid.angle_rad"] < _ftir_math.pi / 3:
                return reject()
            loading = capability_id == "sample.load"
            if loading:
                sample = host._samples.get(arguments["sample_id"])
                if state["sample.loaded"] or sample is None or state["accessory.mode"] == "none":
                    return reject()
                if sample["phase"] == "gas" and state["accessory.mode"] != "transmission":
                    return reject()
                state.update({"sample.loaded": True, "sample.id": arguments["sample_id"], "sample.phase": sample["phase"], "sample.truth": _ftir_copy.deepcopy(sample["truth"])})
            else:
                if not state["sample.loaded"]:
                    return reject()
                state.update({"sample.loaded": False, "sample.id": None, "sample.phase": None, "sample.truth": None})
            result = {"loaded": loading, "sample_id": state["sample.id"]}
        elif capability_id == "method.configure":
            if arguments["start_wavenumber_m_inv"] >= arguments["stop_wavenumber_m_inv"]:
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            for key, value in arguments.items():
                state["method." + key] = float(value) if key.endswith("m_inv") else value
            result = {key: state["method." + key] for key in properties}
        elif capability_id in ("background.acquire", "measure"):
            if not state["lid.closed"] or state["accessory.mode"] == "none":
                return reject()
            if capability_id == "background.acquire":
                if state["sample.loaded"]:
                    return reject()
                state["background.spectrum"] = host._background(state)
                state["background.ready"] = True
                state["background.count"] += 1
                result = {"background": state["background.spectrum"]}
            else:
                if not state["sample.loaded"] or not state["background.ready"]:
                    return reject()
                state["measurement.spectrum"] = host._spectrum(state)
                if state["measurement.spectrum"] is None:
                    return reject()
                state["measurement.count"] += 1
                result = {"spectrum": state["measurement.spectrum"]}
        else:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        if capability_id != "measure":
            state["measurement.spectrum"] = None
        if capability_id in ("power.set", "accessory.install", "method.configure"):
            state["background.ready"] = False
            state["background.spectrum"] = None
        if host._scene_adapter is not None:
            try:
                host._scene_adapter(host.instance_id, host._public_scene_state(state))
            except Exception:
                return self._response("failed", request_id=request_id, code="DEVICE_ERROR", side_effects="unknown")
        host._state = state
        host._revision += 1
        return self._response(request_id=request_id, result=result)

    def write(self, *, state_id, value, request_id, instance_id=None):
        with self._host._lock:
            if not self._target(instance_id):
                return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
            if not isinstance(state_id, str) or not self._visible(state_id):
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            route = self._host._metadata["state_slots"][state_id]["write_via"]
            if route is None:
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id)


def apply_jasco_ftir4700_blender_scene(instance_id, updates, *, object_prefix=""):
    """Atomic Blender target update with rollback; distinct prefixes select instances.

    Process owns the joint. For multi-instance models the host must use separate
    objects and object data; shared indicator materials are copied before edit.
    Competing animation, constraints or rigid-body writers are rejected.
    """
    import bpy
    required = ("joint_sample_lid", "accessory_atr", "accessory_transmission", "sample_holder_sample", "indicator_power")
    objects = {name: bpy.data.objects.get(object_prefix + name) for name in required}
    if any(obj is None for obj in objects.values()):
        raise RuntimeError("Required FTIR scene targets are unavailable")
    keys = ("lid.angle_rad", "sample.loaded", "accessory.mode", "power.enabled")
    if not isinstance(updates, dict) or any(key not in updates for key in keys):
        raise ValueError("Complete FTIR public scene state is required")
    angle = updates["lid.angle_rad"]
    if not _ftir_number(angle) or not 0 <= angle <= JASCO_FTIR4700_LID_MAX_RAD or not isinstance(updates["sample.loaded"], bool) or not isinstance(updates["power.enabled"], bool) or updates["accessory.mode"] not in ("none", "atr", "transmission"):
        raise ValueError("Invalid FTIR scene state")
    for obj in objects.values():
        if obj.animation_data or len(obj.constraints) or obj.rigid_body is not None:
            raise RuntimeError("Scene target has a competing animation/constraint/rigid-body authority")
    lid = objects["joint_sample_lid"]
    indicator = objects["indicator_power"]
    candidates = [(index, slot) for index, slot in enumerate(indicator.material_slots)
        if slot.material and "power_indicator" in slot.material.name]
    if len(candidates) != 1:
        raise RuntimeError("A dedicated power_indicator material is required")
    slot_index, slot = candidates[0]
    old_material = slot.material
    old_slot_link = slot.link
    old_rotation = (lid.rotation_mode, lid.rotation_euler.copy())
    visibility = {name: (objects[name].hide_viewport, objects[name].hide_render, objects[name].hide_get()) for name in required[1:4]}
    new_material = None
    material = old_material
    old_color = tuple(material.diffuse_color)
    old_node_colors = [(node, tuple(node.inputs["Base Color"].default_value)) for node in (material.node_tree.nodes if material.use_nodes and material.node_tree else []) if node.type == "BSDF_PRINCIPLED"]
    try:
        # Object-linked material slots avoid cross-instance mesh/material writes.
        if old_material.users > 1 or slot.link != "OBJECT":
            new_material = old_material.copy()
            new_material.name = "power_indicator_" + instance_id
            slot.link = "OBJECT"
            slot.material = new_material
            material = new_material
        lid.rotation_mode = "XYZ"
        lid.rotation_euler.x = -float(angle)
        visible = {"accessory_atr": updates["accessory.mode"] == "atr",
            "accessory_transmission": updates["accessory.mode"] == "transmission", "sample_holder_sample": updates["sample.loaded"]}
        for name, shown in visible.items():
            objects[name].hide_viewport = not shown
            objects[name].hide_render = not shown
            objects[name].hide_set(not shown)
        color = (0.035, 0.8, 0.18, 1.0) if updates["power.enabled"] else (0.012, 0.025, 0.015, 1.0)
        material.diffuse_color = color
        if material.use_nodes and material.node_tree:
            for node in material.node_tree.nodes:
                if node.type == "BSDF_PRINCIPLED":
                    node.inputs["Base Color"].default_value = color
        bpy.context.view_layer.update()
    except Exception:
        lid.rotation_mode, lid.rotation_euler = old_rotation
        for name, (hide_viewport, hide_render, hide_set) in visibility.items():
            objects[name].hide_viewport, objects[name].hide_render = hide_viewport, hide_render
            objects[name].hide_set(hide_set)
        slot.material = old_material
        slot.link = old_slot_link
        old_material.diffuse_color = old_color
        for node, color in old_node_colors:
            node.inputs["Base Color"].default_value = color
        if new_material is not None and new_material.users == 0:
            bpy.data.materials.remove(new_material)
        bpy.context.view_layer.update()
        raise


# CHEM-096 Agilent 8890B migration. Append-only extension; legacy 7890B is unchanged.
from scripts.gc_runtime import GC as _GC8890BLegacyGC, Method as _GC8890BMethod
from scripts.gc_runtime import ThermalZone as _GC8890BThermalZone

AGILENT_GC8890B_TYPE = "instrument.agilent_gc8890b"
AGILENT_GC8890B_LID_MAX_RAD = _dr6000_math.radians(110)
GC8890B_ZONE_MAX_K = {"oven": 723.15, "inlet": 673.15, "fid": 723.15}
GC8890B_MAX_RAMP_K_S = 135.0 / 60.0


class GC8890BOvenZone(_GC8890BThermalZone):
    """Lumped educational oven; cooling fit is a review target, not vendor calibration."""
    cooling_conductance_W_K = 10.5
    maximum_ramp_K_s = GC8890B_MAX_RAMP_K_S
    cooling_damper_open = False

    def advance(self, dt_s, ambient_K, enabled, open_door=False):
        self.cooling_damper_open = bool(enabled and self.setpoint_K < self.temperature_K)
        conductance = (self.conductance_W_K * 12 if open_door else
                       self.cooling_conductance_W_K if self.cooling_damper_open else self.conductance_W_K)
        demand = (self.control_gain_W_K * (self.setpoint_K - self.temperature_K)
                  + conductance * (self.setpoint_K - ambient_K))
        power = min(self.maximum_power_W, max(0.0, demand)) if enabled and not self.cooling_damper_open else 0.0
        # Limit actual energy input so a finite substep cannot exceed the catalog
        # maximum ramp. The 135 K/min value is only a 50..70 C high-power maximum.
        power = min(power, max(0.0, self.heat_capacity_J_K * self.maximum_ramp_K_s
                              + conductance * (self.temperature_K - ambient_K)))
        self.heater_power_W = power
        equilibrium = ambient_K + power / conductance
        decay = _dr6000_math.exp(-conductance * dt_s / self.heat_capacity_J_K)
        self.temperature_K = equilibrium + (self.temperature_K - equilibrium) * decay


class GC8890BProcess(_GC8890BLegacyGC):
    """Trusted-host/debug legacy process, including full synthetic mass truth.

    Public agent use must go through AgilentGC8890B; direct observe/perform/step
    intentionally retain the historical debug result. No Blender/hardware calls.
    """
    def __init__(self, method=None, species=None, vial_masses_kg=None):
        method = method or _GC8890BMethod()
        for zone in GC8890B_ZONE_MAX_K:
            self._validate_temperature(zone, getattr(method, zone + "_temperature_K"), 298.15)
        options = {"method": method, "vial_masses_kg": vial_masses_kg}
        if species is not None:
            options["species"] = species
        super().__init__(**options)
        self.zones["oven"] = GC8890BOvenZone(self.ambient_K, method.oven_temperature_K,
                                              1000.0, 3.0, 2400.0, 200.0)

    @staticmethod
    def _validate_temperature(zone, temperature_K, ambient_K):
        if zone not in GC8890B_ZONE_MAX_K or not _dr6000_number(temperature_K):
            raise ValueError("Invalid thermal zone or temperature")
        lower = ambient_K + 4.0 if zone == "oven" else ambient_K
        if not lower <= temperature_K <= GC8890B_ZONE_MAX_K[zone]:
            raise ValueError("Temperature outside the configured non-cryogenic zone range")

    def perform(self, action, **kwargs):
        if action == "set_temperature":
            if set(kwargs) != {"zone", "temperature_K"}:
                raise ValueError("Temperature command requires zone and temperature_K")
            self._validate_temperature(kwargs["zone"], kwargs["temperature_K"], self.ambient_K)
        result = super().perform(action, **kwargs)
        self.zones["oven"].cooling_damper_open = bool(self.powered and not self.door_open
            and self.zones["oven"].setpoint_K < self.zones["oven"].temperature_K)
        return result

    def step(self, dt_s):
        if not _dr6000_number(dt_s) or dt_s <= 0:
            raise ValueError("dt_s must be finite positive seconds")
        return super().step(dt_s)


def _gc8890b_chromatogram_schema():
    number, obj = _dr6000_numeric, _dr6000_object
    return obj({
        "time_s": {"type": "array", "items": number("s", 0), "minItems": 301, "maxItems": 301},
        "fid_current_A": {"type": "array", "items": number("A", 0), "minItems": 301, "maxItems": 301},
        "retention_time_s": {"type": "array", "items": number("s", 0)},
        "peak_area_C": {"type": "array", "items": number("C", 0)},
        "calibrated_concentration_mol_m3": {"type": "null"},
        "measurement_duration_s": {"type": "null"},
        "synthetic": {"type": "boolean", "const": True},
        "quality": {"type": "string", "const": "estimated"},
        "signal_unit": {"type": "string", "const": "A"},
        "axis_unit": {"type": "string", "const": "s"},
        "sampling": {"type": "string", "const": "instant_fixture"},
    })


def export_agilent_gc8890b_contract_metadata():
    """Public metadata plus restricted state declarations; no live sample values."""
    number, obj = _dr6000_numeric, _dr6000_object
    boolean, text = {"type": "boolean"}, {"type": "string"}
    angle = number("rad", 0, AGILENT_GC8890B_LID_MAX_RAD)
    definitions = {
        "power.set": ({"enabled": boolean}, {"enabled": boolean}, {"enabled": True},
            ["power.enabled"], ["power_switch", "power_inlet"], [], True),
        "lid.set_angle": ({"angle_rad": angle}, {"angle_rad": angle, "closed": boolean}, {"angle_rad": 1.4},
            ["lid.angle_rad", "lid.closed"], ["oven_door", "oven_handle"], ["Synchronous scene adapter registered."], True),
        "sample.load": ({"sample_id": text}, {"loaded": boolean, "sample_id": text}, {"sample_id": "fixture"},
            ["sample.loaded", "sample.id", "sample.truth"], ["sample_vial"],
            ["Door angle >= pi/3 is the batch virtual-loading convention, not the real GC injection procedure.", "Registered fixture; empty external virtual vial slot.", "Synchronous scene adapter registered."], True),
        "sample.unload": ({}, {"loaded": boolean, "sample_id": {"type": "null"}}, {},
            ["sample.loaded", "sample.id", "sample.truth"], ["sample_vial"],
            ["Door angle >= pi/3; external virtual vial slot occupied.", "Synchronous scene adapter registered."], True),
        "thermal.set_temperature": ({"zone": {"type": "string", "enum": ["oven", "inlet", "fid"]}, "temperature_K": number("K", 298.15, 723.15)},
            {"zone": {"type": "string", "enum": ["oven", "inlet", "fid"]}, "temperature_K": number("K", 298.15, 723.15)},
            {"zone": "oven", "temperature_K": 353.15},
            ["thermal.oven.setpoint_k", "thermal.inlet.setpoint_k", "thermal.fid.setpoint_k", "thermal.cooling_damper_open"],
            ["control_panel", "oven_heater", "inlet_heater", "fid_heater", "oven_exhaust"],
            ["Noncryogenic oven: ambient+4 K..723.15 K; SSL inlet: ambient..673.15 K; FID: ambient..723.15 K.", "Control target changes immediately; host_step alone advances temperatures."], False),
        "chromatography.measure": ({}, {"chromatogram": _gc8890b_chromatogram_schema()}, {},
            ["measurement.chromatogram", "measurement.count"], ["sample_vial", "column", "fid_body", "fid_electrometer"],
            ["Power enabled; door closed; registered virtual sample loaded.", "Returns an instantaneous arbitrary fixture; does not run the legacy injection/transport simulation."], False),
    }
    capabilities = {}
    for cid, (inputs, outputs, example, effects, components, preconditions, scene_required) in definitions.items():
        effects = effects + ([] if cid == "chromatography.measure" else ["measurement.chromatogram"])
        if cid in ("power.set", "lid.set_angle"):
            effects += ["thermal.cooling_damper_open"]
        capabilities[cid] = {"input_schema": obj(inputs), "output_schema": obj(outputs),
            "example_arguments": [example], "state_ids": effects, "component_ids": components,
            "preconditions": preconditions, "scene_required": scene_required, "availability": "implemented",
            "invocation": "AgilentGC8890B.invoke(instance_id=..., capability_id=" + repr(cid) + ", arguments=..., request_id=...)",
            "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "REQUEST_CONFLICT", "UNSUPPORTED", "DEVICE_ERROR"],
            "rejection_side_effects": "none", "scene_failure_side_effects": "unknown"}
    for cid in ("chromatography.set_oven_program", "chromatography.set_carrier", "chromatography.set_injection_mode",
                "chromatography.set_split_ratio", "chromatography.select_detector", "chromatography.set_valve_timing",
                "chromatography.calibrate", "thermal.enable_cryogenic", "robot.load_sample", "hardware.connect"):
        capabilities[cid] = {"input_schema": obj({}), "output_schema": obj({}), "example_arguments": [{}],
            "availability": "unsupported", "state_ids": [], "component_ids": ["control_panel"],
            "preconditions": [], "scene_required": False, "invocation": None,
            "errors": ["UNSUPPORTED"], "rejection_side_effects": "none"}
    slots = {}
    def add(sid, schema, unit, origin, route=None, visibility="agent"):
        slots[sid] = {"value_schema": schema, "unit": unit, "origin": origin, "visibility": visibility,
                      "access": "read_write" if route else "read_only", "write_via": route}
    add("power.enabled", boolean, "1", "setpoint", {"capability_id": "power.set", "parameter": "enabled"})
    add("lid.angle_rad", angle, "rad", "scene", {"capability_id": "lid.set_angle", "parameter": "angle_rad"})
    add("lid.closed", boolean, "1", "derived")
    add("sample.loaded", boolean, "1", "scene")
    add("sample.id", {"anyOf": [text, {"type": "null"}]}, "1", "scene")
    add("sample.truth", {"anyOf": [{"type": "object"}, {"type": "null"}]}, "1", "process_truth", visibility="debug")
    for zone in GC8890B_ZONE_MAX_K:
        lower = 302.15 if zone == "oven" else 298.15
        add("thermal." + zone + ".setpoint_k", number("K", lower, GC8890B_ZONE_MAX_K[zone]), "K", "setpoint",
            {"capability_id": "thermal.set_temperature", "parameter": "temperature_K", "fixed_arguments": {"zone": zone}})
        add("thermal." + zone + ".indicated_k", number("K", 0), "K", "derived")
    add("thermal.cooling_damper_open", boolean, "1", "derived")
    add("process.thermal_truth", {"type": "object"}, "1", "process_truth", visibility="debug")
    add("measurement.chromatogram", {"anyOf": [_gc8890b_chromatogram_schema(), {"type": "null"}]}, "1", "derived")
    add("measurement.count", {"type": "integer", "minimum": 0, "x-unit": "1"}, "1", "derived")
    return {"asset_type_id": AGILENT_GC8890B_TYPE, "asset_version": "1.0.0", "catalog_id": "CHEM-096",
        "model": "Agilent | 8890B GC", "capabilities": capabilities, "state_slots": slots,
        "parameters": {
            "oven_maximum_K": {"value": 723.15, "unit": "K", "evidence": "catalog"},
            "oven_minimum_offset_from_ambient_K": {"value": 4.0, "unit": "K", "evidence": "catalog"},
            "oven_maximum_ramp_K_s": {"value": 2.25, "unit": "K/s", "evidence": "catalog", "conditions": "Vendor maximum only at 50..70 C, >200 V and >15 A; model applies it as an upper bound, not an achievable full-range rate."},
            "ssl_maximum_K": {"value": 673.15, "unit": "K", "evidence": "manufacturer_specification_via_review"},
            "fid_maximum_K": {"value": 723.15, "unit": "K", "evidence": "manufacturer_specification_via_review"},
            "oven_heat_capacity_J_K": {"value": 1000.0, "unit": "J/K", "evidence": "engineering_assumption"},
            "oven_passive_conductance_W_K": {"value": 3.0, "unit": "W/K", "evidence": "engineering_assumption"},
            "oven_cooling_conductance_W_K": {"value": 10.5, "unit": "W/K", "evidence": "engineering_assumption", "conditions": "Calibrated to this task's 450->50 C in 4..5 min at 25 C ambient; not vendor performance. 1000/10.5*ln(425/25) = 269.83 s. Review's suggested 30..40 W/K would cool too quickly in this one-node model."},
            "oven_maximum_model_power_W": {"value": 2400.0, "unit": "W", "evidence": "engineering_assumption", "conditions": "Model component power, not a claimed vendor heater rating or whole-machine power."},
            "maximum_lid_angle_rad": {"value": AGILENT_GC8890B_LID_MAX_RAD, "unit": "rad", "evidence": "inherited_geometry_assumption"},
            "loading_clearance_rad": {"value": _dr6000_math.pi / 3, "unit": "rad", "evidence": "task_convention"},
            "temperature_indication_quantum_K": {"value": 0.1, "unit": "K", "evidence": "engineering_assumption", "conditions": "Rounded ideal process estimate, not vendor sensor resolution/noise."},
            "measurement_duration_s": {"value": None, "unit": "s", "evidence": "unknown"},
            "fixture_axis_end_s": {"value": 300.0, "unit": "s", "evidence": "synthetic_assumption"},
            "fixture_axis_step_s": {"value": 1.0, "unit": "s", "evidence": "synthetic_assumption"},
            "calibrated_concentration_mol_m3": {"value": None, "unit": "mol/m3", "evidence": "unknown"},
        },
        "scene_binding": {"lid": "GC7890B__oven_door['q']=lid.angle_rad; inherited driver rotates local Z negatively", "power": "GC7890B__power_switch['q']=+0.15 if powered else -0.15", "sample": "GC7890B__sample_vial and descendants visible iff sample.loaded", "authority": "process", "direction": "process_to_scene"},
        "limits": ["7890B geometry is explicitly reused; only one SSL/FID synthetic process is represented, not the 8890B maximum two-inlet/four-detector configuration.",
            "No cryogenic option; oven minimum is ambient+4 K. No real ramp-program/valve timing/calibration/hardware/robot implementation.",
            "Public measure returns arbitrary instantaneous Gaussian fixtures, separate from legacy physical-time mass transport; no real species identity, quantitative concentration, noise or real acquisition time.",
            "Virtual sample loading is an external vial-slot visibility convention, not robotic movement, syringe injection or a real 8890B oven-door loading instruction.",
            "Legacy 39 APIs expose process truth and remain trusted-host/debug only; no public agent capability can call them.",
            "Identity-bound API filtering covers read/describe/invoke/errors/scene callback; it is not a Python introspection or filesystem sandbox.",
            "host_step alone advances this instance after the host chooses elapsed simulation time; no shared-world scheduler is implemented.",
            "Scene callback completion alone is not evaluated Blender pose verification; independent verification is required.",
            "Rejected preconditions leave process/scene unchanged. An external scene callback that fails can have unknown external side effects; process does not commit."],
    }


# 2026-10 template hook: spectral_library/gc_retention.json path (scripts/basic40_templates/agilent_gc8890b.py).
_gc8890b_export_metadata_base = export_agilent_gc8890b_contract_metadata


def export_agilent_gc8890b_contract_metadata():
    from scripts.basic40_templates import agilent_gc8890b as _gc8890b_templates
    return _gc8890b_templates.extend_metadata(_gc8890b_export_metadata_base())


class AgilentGC8890B(HachDR6000):
    """Per-instance public adapter; trusted host owns private fixtures and process."""
    def __init__(self, instance_id, *, scene_adapter=None):
        super().__init__(instance_id, scene_adapter=scene_adapter)
        self._process = GC8890BProcess()
        self._process.power_connected = True  # Explicit virtual host supply configuration.
        self._metadata = export_agilent_gc8890b_contract_metadata()
        self._state = {"power.enabled": False, "lid.angle_rad": 0.0, "lid.closed": True,
            "sample.loaded": False, "sample.id": None, "sample.truth": None,
            "measurement.chromatogram": None, "measurement.count": 0}
        self._refresh_thermal(self._state, self._process)
        self._agent = AgilentGC8890BSession(self, "__agent__")

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Principal must first be registered by the trusted host")
        return AgilentGC8890BSession(self, principal_id)

    def register_sample(self, sample_id, *, peaks=None, truth=None):
        """Trusted fixture registration: peaks=(retention_s, width_s, height_A)."""
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples:
                raise ValueError("A new nonempty sample ID is required")
            peaks = [(60.0, 4.0, 1e-10), (125.0, 8.0, 6e-11), (220.0, 12.0, 8e-11)] if peaks is None else peaks
            if not isinstance(peaks, (list, tuple)) or len(peaks) > 32 or (truth is not None and not isinstance(truth, dict)):
                raise ValueError("Invalid sample fixture")
            for peak in peaks:
                if not isinstance(peak, (list, tuple)) or len(peak) != 3 or not all(_dr6000_number(x) for x in peak):
                    raise ValueError("Peaks require finite center_s, width_s and height_A")
                if not 0 <= peak[0] <= 300 or not 0.1 <= peak[1] <= 300 or not 0 <= peak[2] <= 1:
                    raise ValueError("Synthetic peak outside workload bounds")
            _dr6000_json.dumps(truth or {}, allow_nan=False)
            self._samples[sample_id] = {"peaks": _dr6000_copy.deepcopy(peaks), "truth": _dr6000_copy.deepcopy(truth or {})}

    def register_template_sample(self, sample_id, *, composition_kg, column="nonpolar", injection_fraction=0.01, truth=None):
        """Trusted host: vial contents as spectral_library ids -> kg; measure runs gc_runtime with NIST-RI fingerprints."""
        from scripts.basic40_templates import agilent_gc8890b as _gc8890b_templates
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples or (truth is not None and not isinstance(truth, dict)):
                raise ValueError("A new nonempty sample ID and optional truth dictionary are required")
            _dr6000_json.dumps(truth or {}, allow_nan=False)
            template = _gc8890b_templates.validate_sample(composition_kg, column=column, injection_fraction=injection_fraction)
            self._samples[sample_id] = {"peaks": [], "truth": _dr6000_copy.deepcopy(truth or {}), "template": template}

    @staticmethod
    def _public_scene_state(state):
        return _dr6000_copy.deepcopy({sid: value for sid, value in state.items() if sid not in ("sample.truth", "process.thermal_truth")})

    @staticmethod
    def _refresh_thermal(state, process):
        for zone, data in process.zones.items():
            state["thermal." + zone + ".setpoint_k"] = data.setpoint_K
            state["thermal." + zone + ".indicated_k"] = round(data.temperature_K, 1)
        state["thermal.cooling_damper_open"] = process.zones["oven"].cooling_damper_open
        state["process.thermal_truth"] = {zone: {"temperature_K": data.temperature_K,
            "heater_power_W": data.heater_power_W} for zone, data in process.zones.items()}

    def _time(self):
        return self._process.time_s

    def debug_snapshot(self):
        with self._lock:
            return _dr6000_copy.deepcopy({"state": self._state, "state_revision": self._revision,
                "registered_samples": self._samples, "process": self._process.observe(),
                "thermal_zones": {zone: vars(data) for zone, data in self._process.zones.items()},
                "events": self._process.events})

    def host_step(self, dt_s):
        """Trusted-host simulation step, never exposed as an agent capability."""
        with self._lock:
            process = _dr6000_copy.deepcopy(self._process)
            process.step(dt_s)
            state = _dr6000_copy.deepcopy(self._state)
            self._refresh_thermal(state, process)
            if self._scene_adapter is not None:
                self._scene_adapter(self.instance_id, self._public_scene_state(state))
            self._process, self._state = process, state
            self._revision += 1
            return self.read()

    def _chromatogram(self, state):
        if "template" in self._samples[state["sample.id"]]:  # None => FID cannot be lit at the setpoint
            from scripts.basic40_templates import agilent_gc8890b as _gc8890b_templates
            return _gc8890b_templates.chromatogram(self._samples[state["sample.id"]]["template"], state)
        peaks = self._samples[state["sample.id"]]["peaks"]
        axis = [float(i) for i in range(301)]
        current = [sum(height * _dr6000_math.exp(-0.5 * ((time - center) / width) ** 2)
                       for center, width, height in peaks) for time in axis]
        areas = [height * width * _dr6000_math.sqrt(_dr6000_math.pi / 2) *
                 (_dr6000_math.erf((300.0 - center) / (width * _dr6000_math.sqrt(2)))
                  - _dr6000_math.erf(-center / (width * _dr6000_math.sqrt(2))))
                 for center, width, height in peaks]
        return {"time_s": axis, "fid_current_A": current, "retention_time_s": [float(p[0]) for p in peaks],
                "peak_area_C": areas, "calibrated_concentration_mol_m3": None, "measurement_duration_s": None,
                "synthetic": True, "quality": "estimated", "signal_unit": "A", "axis_unit": "s", "sampling": "instant_fixture"}


class AgilentGC8890BSession(HachDR6000Session):
    """Filtered identity-bound public protocol with transactional process commit."""
    def describe(self, *, asset_type_id=None, instance_id=None):
        with self._host._lock:
            if asset_type_id is not None and instance_id is not None:
                return self._response("rejected", code="INVALID_ARGUMENT")
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != AGILENT_GC8890B_TYPE:
                return self._response("rejected", code="ACCESS_DENIED")
            metadata = _dr6000_copy.deepcopy(self._host._metadata)
            metadata["state_slots"] = {sid: slot for sid, slot in metadata["state_slots"].items() if self._visible(sid)}
            metadata["capabilities"] = {cid: cap for cid, cap in metadata["capabilities"].items()
                if cap["availability"] == "implemented" and (not cap["scene_required"] or self._host._scene_adapter is not None)}
            for cap in metadata["capabilities"].values():
                cap["state_ids"] = [sid for sid in cap["state_ids"] if self._visible(sid)]
            metadata.update(instance_id=self._host.instance_id if asset_type_id is None else None,
                            adapter="AgilentGC8890BSession", scene_adapter_registered=self._host._scene_adapter is not None)
            return metadata

    def _invoke_once(self, *, capability_id, arguments, request_id, instance_id=None):
        host = self._host
        if not self._target(instance_id):
            return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
        if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id:
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        cap = host._metadata["capabilities"].get(capability_id)
        if cap is None:
            return self._response("rejected", request_id=request_id, code="UNKNOWN_CAPABILITY")
        if cap["availability"] != "implemented":
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        properties = cap["input_schema"]["properties"]
        if set(arguments) != set(properties):
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        for name, spec in properties.items():
            value = arguments[name]
            valid = ((spec["type"] == "number" and _dr6000_number(value)) or
                     (spec["type"] == "boolean" and type(value) is bool) or
                     (spec["type"] == "string" and isinstance(value, str) and bool(value)))
            if not valid or ("minimum" in spec and value < spec["minimum"]) or ("maximum" in spec and value > spec["maximum"]) or ("enum" in spec and value not in spec["enum"]):
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        if cap["scene_required"] and host._scene_adapter is None:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        state, process = _dr6000_copy.deepcopy(host._state), _dr6000_copy.deepcopy(host._process)
        if capability_id == "power.set":
            process.perform("power", enabled=arguments["enabled"])
            state["power.enabled"] = arguments["enabled"]
            result = {"enabled": arguments["enabled"]}
        elif capability_id == "lid.set_angle":
            state["lid.angle_rad"] = float(arguments["angle_rad"])
            state["lid.closed"] = state["lid.angle_rad"] <= 1e-6
            process.perform("door", open=not state["lid.closed"])
            result = {"angle_rad": state["lid.angle_rad"], "closed": state["lid.closed"]}
        elif capability_id in ("sample.load", "sample.unload"):
            if state["lid.angle_rad"] < _dr6000_math.pi / 3:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            loading = capability_id == "sample.load"
            if loading:
                sample = host._samples.get(arguments["sample_id"])
                if state["sample.loaded"] or sample is None:
                    return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
                state.update({"sample.loaded": True, "sample.id": arguments["sample_id"], "sample.truth": _dr6000_copy.deepcopy(sample["truth"])})
            else:
                if not state["sample.loaded"]:
                    return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
                state.update({"sample.loaded": False, "sample.id": None, "sample.truth": None})
            result = {"loaded": loading, "sample_id": state["sample.id"]}
        elif capability_id == "thermal.set_temperature":
            try:
                process.perform("set_temperature", **arguments)
            except ValueError:
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            result = {"zone": arguments["zone"], "temperature_K": float(arguments["temperature_K"])}
        elif capability_id == "template.read":
            from scripts.basic40_templates import agilent_gc8890b as _gc8890b_templates
            record = _gc8890b_templates.template_read(arguments["substance_id"])
            if record is None:
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            return self._response(request_id=request_id, result=record)
        elif capability_id == "chromatography.measure":
            if not state["power.enabled"] or not state["lid.closed"] or not state["sample.loaded"]:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            state["measurement.chromatogram"] = host._chromatogram(state)
            if state["measurement.chromatogram"] is None:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            state["measurement.count"] += 1
            result = {"chromatogram": state["measurement.chromatogram"]}
        else:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        if capability_id != "chromatography.measure":
            state["measurement.chromatogram"] = None
        host._refresh_thermal(state, process)
        if host._scene_adapter is not None:
            try:
                host._scene_adapter(host.instance_id, host._public_scene_state(state))
            except Exception:
                return self._response("failed", request_id=request_id, code="DEVICE_ERROR", side_effects="unknown")
        host._state, host._process = state, process
        host._revision += 1
        return self._response(request_id=request_id, result=result)

    def write(self, *, state_id, value, request_id, instance_id=None):
        with self._host._lock:
            if not self._target(instance_id):
                return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
            if not isinstance(state_id, str) or not self._visible(state_id):
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            route = self._host._metadata["state_slots"][state_id]["write_via"]
            if route is None:
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            arguments = dict(route.get("fixed_arguments", {}), **{route["parameter"]: value})
            return self.invoke(instance_id=instance_id, capability_id=route["capability_id"], arguments=arguments, request_id=request_id)


GC8890B_LEGACY_API_DEFINITIONS = {
    "RT01": {"kind": "configuration", "fields": ()},
    "RT02": {"action": "plug_power", "fixed": {"connected": True}, "fields": ()},
    "RT03": {"action": "plug_power", "fixed": {"connected": False}, "fields": ()},
    "RT04": {"action": "power", "fixed": {"enabled": True}, "fields": ()},
    "RT05": {"action": "power", "fixed": {"enabled": False}, "fields": ()},
    "RT14": {"action": "door", "fixed": {"open": True}, "fields": ()},
    "RT15": {"action": "door", "fixed": {"open": False}, "fields": ()},
    "RT16": {"action": "set_temperature", "fixed": {"zone": "oven"}, "fields": ("temperature_K",)},
    "RT17": {"action": "set_temperature", "fixed": {"zone": "inlet"}, "fields": ("temperature_K",)},
    "RT18": {"action": "set_temperature", "fixed": {"zone": "fid"}, "fields": ("temperature_K",)},
    "RT19": {"action": "ignite", "fields": ()}, "RT20": {"action": "extinguish", "fields": ()},
    "RT21": {"action": "start", "fields": ()}, "RT22": {"action": "stop", "fields": ()},
    "RT23": {"action": "aspirate", "fields": ("fraction",)},
    "RT24": {"action": "insert_syringe", "fields": ()},
    "RT25": {"action": "depress_plunger", "fields": ("fraction",)},
    "RT26": {"action": "retract_syringe", "fields": ()},
    "RT27": {"kind": "simulation", "method": "step", "fields": ("dt_s",)},
    "RT28": {"kind": "query", "method": "observe", "fields": ()},
    "RT29": {"kind": "query", "method": "flows", "fields": ()},
    "RT30": {"kind": "query", "method": "mass_balance", "fields": ()},
    "RT31": {"kind": "query", "method": "not_ready_reasons", "fields": ()},
    "RT32": {"kind": "configuration", "fields": ("mode", "split_ratio")},
    "RT33": {"kind": "configuration", "fields": ("mode", "purge_time_s", "purge_flow_m3_s")},
    "RT34": {"kind": "configuration", "fields": ("column_flow_m3_s", "septum_purge_flow_m3_s", "septum_sample_loss_fraction")},
    "RT35": {"kind": "configuration", "fields": ("hydrogen_flow_m3_s", "air_flow_m3_s", "makeup_flow_m3_s")},
    "RT36": {"kind": "configuration", "fields": ("liner_volume_m3", "column_length_m", "column_inner_diameter_m", "column_film_thickness_m")},
    "RT37": {"kind": "configuration", "fields": ("longitudinal_B_m2_s", "mass_transfer_C_s")},
    "RT38": {"kind": "configuration", "fields": ("oven_temperature_K", "inlet_temperature_K", "fid_temperature_K")},
    "RT39": {"kind": "configuration", "fields": ("temperature_tolerance_K", "ready_dwell_s", "fid_ignition_minimum_K")},
}
for _gc8890b_gas_index, _gc8890b_gas in enumerate(("carrier", "hydrogen", "air", "makeup")):
    for _gc8890b_connect_index, _gc8890b_connected in enumerate((True, False)):
        GC8890B_LEGACY_API_DEFINITIONS["RT%02d" % (6 + 2 * _gc8890b_gas_index + _gc8890b_connect_index)] = {
            "action": "connect_gas", "fixed": {"gas": _gc8890b_gas, "connected": _gc8890b_connected}, "fields": ()}


class GC8890BLegacySession:
    """39 historical rows as trusted-host/debug calls, with full mass-truth output.

    This is deliberately not an agent LabAdapter and is not routed by the public
    adapter. Configuration replaces/reset this session's process only. No request
    deduplication, scene/hardware/robot interaction or automatic retry is provided.
    """
    def __init__(self, process=None):
        if process is not None and not isinstance(process, GC8890BProcess):
            raise ValueError("A GC8890BProcess is required")
        self.process = process or GC8890BProcess()
        self._lock = _dr6000_threading.RLock()

    def invoke(self, api_id, arguments):
        with self._lock:
            if not isinstance(api_id, str) or api_id not in GC8890B_LEGACY_API_DEFINITIONS or not isinstance(arguments, dict):
                raise ValueError("Unknown legacy API or invalid arguments")
            definition = GC8890B_LEGACY_API_DEFINITIONS[api_id]
            if set(arguments) != set(definition["fields"]):
                raise ValueError("Arguments must exactly match the legacy row fields")
            for name, value in arguments.items():
                if name == "mode":
                    if value not in ("split", "splitless"):
                        raise ValueError("Unknown injection mode")
                elif not _dr6000_number(value):
                    raise ValueError("Legacy numeric fields must be finite numbers, not booleans or strings")
            kind = definition.get("kind", "device_command")
            if kind == "configuration":
                replacement = GC8890BProcess(method=_GC8890BMethod(**arguments))
                self.process = replacement
                return self.process.observe()
            if kind == "query":
                return _dr6000_copy.deepcopy(getattr(self.process, definition["method"])())
            candidate = _dr6000_copy.deepcopy(self.process)
            if kind == "simulation":
                result = candidate.step(arguments["dt_s"])
            else:
                result = candidate.perform(definition["action"], **dict(definition.get("fixed", {}), **arguments))
            self.process = candidate
            return _dr6000_copy.deepcopy(result)

# CHEM-108 Agilent 8697: appended independent batch-scope runtime.
import copy as _hs8697_copy
import json as _hs8697_json
import math as _hs8697_math
import threading as _hs8697_threading

AGILENT_HS8697_TYPE = 'instrument.agilent_hs8697'
AGILENT_HS8697_LID_MAX_RAD = _hs8697_math.pi / 2
AGILENT_HS8697_TRAY_SLOTS = tuple('tray_%02d' % i for i in range(1, 49))
AGILENT_HS8697_OVEN_SLOTS = tuple('oven_%02d' % i for i in range(1, 13))
AGILENT_HS8697_VIAL_VOLUMES_M3 = (10e-6, 20e-6, 22e-6)


def _hs8697_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return _hs8697_math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _hs8697_object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _hs8697_numeric(unit, minimum=None, maximum=None):
    spec = {'type': 'number', 'x-unit': unit}
    if minimum is not None:
        spec['minimum'] = minimum
    if maximum is not None:
        spec['maximum'] = maximum
    return spec


def _hs8697_nullable(spec):
    return {'anyOf': [spec, {'type': 'null'}]}


def _hs8697_chromatogram_schema():
    return _hs8697_object({
        'time_s': {'type': 'array', 'items': _hs8697_numeric('s', 0), 'minItems': 121, 'maxItems': 121},
        'signal': {'type': 'array', 'items': _hs8697_numeric('1'), 'minItems': 121, 'maxItems': 121},
        'axis_unit': {'type': 'string', 'const': 's'}, 'signal_unit': {'type': 'string', 'const': '1'},
        'synthetic': {'type': 'boolean', 'const': True}, 'quality': {'type': 'string', 'const': 'estimated'},
        'gc_instance_id': {'type': 'string'}, 'vial_id': {'type': 'string'},
        'physical_transfer_mol': {'type': 'null'}, 'measurement_duration_s': {'type': 'null'},
    })


def export_agilent_hs8697_contract_metadata():
    """Static interface metadata. Unknown physical parameters remain null."""
    n, obj = _hs8697_numeric, _hs8697_object
    text, boolean = {'type': 'string'}, {'type': 'boolean'}
    tray = {'type': 'string', 'enum': list(AGILENT_HS8697_TRAY_SLOTS)}
    oven = {'type': 'string', 'enum': list(AGILENT_HS8697_OVEN_SLOTS)}
    slot = {'type': 'string', 'enum': list(AGILENT_HS8697_TRAY_SLOTS + AGILENT_HS8697_OVEN_SLOTS)}
    zone = {'type': 'string', 'enum': ['oven', 'loop_valve', 'transfer_line']}
    angle = n('rad', 0, AGILENT_HS8697_LID_MAX_RAD)
    defs = {
        'power.set': ({'enabled': boolean}, {'enabled': boolean}, {'enabled': True}, ['power.enabled'], ['chassis', 'display'], [], False),
        'lid.set_angle': ({'angle_rad': angle}, {'angle_rad': angle, 'closed': boolean}, {'angle_rad': 1.5707963267948966}, ['lid.angle_rad', 'lid.closed'], ['sample_lid'], ['Synchronous scene adapter registered.'], True),
        'sample.load': ({'sample_id': text, 'slot_id': tray}, {'sample_id': text, 'slot_id': tray}, {'sample_id': 'fixture_01', 'slot_id': 'tray_01'}, ['tray.occupancy', 'vial.status', 'sample.truth'], ['tray', 'vial_proxy'], ['Lid >= pi/3 rad (assumed clearance).', 'Registered sealed vial, empty tray slot and no duplicate occupancy.', 'Synchronous scene adapter registered.'], True),
        'sample.unload': ({'slot_id': tray}, {'sample_id': text, 'slot_id': tray}, {'slot_id': 'tray_01'}, ['tray.occupancy', 'vial.status', 'sample.truth'], ['tray', 'vial_proxy'], ['Lid >= pi/3 rad (assumed clearance).', 'Occupied tray slot.', 'Synchronous scene adapter registered.'], True),
        'sample.transfer': ({'source_slot': slot, 'target_slot': slot}, {'sample_id': text, 'source_slot': slot, 'target_slot': slot}, {'source_slot': 'tray_01', 'target_slot': 'oven_01'}, ['tray.occupancy', 'oven.occupancy', 'vial.status', 'manipulator.position_slot'], ['tray', 'oven', 'manipulator', 'vial_proxy'], ['Power enabled; lid >= pi/3 rad (assumed access clearance).', 'Occupied source and empty distinct target.', 'Synchronous scene adapter registered.'], True),
        'thermal.set_temperature': ({'zone': zone, 'temperature_K': n('K', 0, 573.15)}, {'zone': zone, 'temperature_K': n('K', 0, 573.15)}, {'zone': 'oven', 'temperature_K': 333.15}, ['thermal.oven.setpoint_k', 'thermal.loop_valve.setpoint_k', 'thermal.transfer_line.setpoint_k'], ['oven', 'loop_valve', 'transfer_line'], ['Power enabled.', 'Trusted host ambient temperature provided; target >= ambient + 5 K.'], False),
        'method.set_equilibration_time': ({'duration_s': n('s', 0, 59999.4)}, {'duration_s': n('s', 0, 59999.4)}, {'duration_s': 60.0}, ['method.equilibration_time_s'], ['oven', 'display'], ['Power enabled.', 'duration_s is an integer multiple of 0.6 s.'], False),
        'measure': ({'oven_slot': oven}, {'chromatogram': _hs8697_chromatogram_schema(), 'synthetic': {'type': 'boolean', 'const': True}}, {'oven_slot': 'oven_01'}, ['measurement.chromatogram', 'measurement.count', 'vial.status', 'injection.log'], ['oven', 'loop_valve', 'transfer_line', 'display'], ['Power enabled; lid closed <= 1e-6 rad.', 'Occupied oven slot; host registered GC connection and carrier gas.', 'All three temperature targets are valid for current host ambient; equilibration time configured.', 'No thermal stabilization or real-time equilibration is claimed.'], False),
    }
    caps = {}
    for cid, (inputs, outputs, example, states, components, preconditions, scene_required) in defs.items():
        if cid != 'measure':
            states = states + ['measurement.chromatogram']
        caps[cid] = {'input_schema': obj(inputs), 'output_schema': obj(outputs), 'example_arguments': [example],
            'state_ids': states, 'component_ids': list(dict.fromkeys(components + ['display'])), 'preconditions': preconditions,
            'scene_required': scene_required, 'availability': 'implemented',
            'invocation': 'AgilentHS8697.invoke(instance_id=..., capability_id=' + repr(cid) + ', arguments=..., request_id=...)',
            'errors': ['UNKNOWN_INSTANCE', 'INVALID_ARGUMENT', 'PRECONDITION_FAILED', 'REQUEST_CONFLICT', 'UNSUPPORTED', 'DEVICE_ERROR'],
            'rejection_side_effects': 'none', 'scene_failure_side_effects': 'unknown'}
    for cid, components in [('pressure.set', ['loop_valve']), ('shaking.set', ['oven']), ('hardware.connect', ['chassis']), ('robot.execute', ['manipulator']), ('headspace.physical_inject', ['loop_valve', 'transfer_line'])]:
        caps[cid] = {'input_schema': obj({}), 'output_schema': obj({}), 'example_arguments': [{}], 'state_ids': [], 'component_ids': components,
            'preconditions': [], 'scene_required': False, 'availability': 'unsupported', 'invocation': None, 'errors': ['UNSUPPORTED'], 'rejection_side_effects': 'none'}
    states = {}
    def add(sid, schema, unit, origin, components, route=None, visibility='agent'):
        states[sid] = {'value_schema': schema, 'unit': unit, 'origin': origin, 'component_ids': components, 'visibility': visibility,
            'access': 'read_write' if route else 'read_only', 'write_via': route}
    add('power.enabled', boolean, '1', 'setpoint', ['chassis'], {'capability_id': 'power.set', 'parameter': 'enabled', 'fixed_arguments': {}})
    add('lid.angle_rad', angle, 'rad', 'scene', ['sample_lid'], {'capability_id': 'lid.set_angle', 'parameter': 'angle_rad', 'fixed_arguments': {}})
    add('lid.closed', boolean, '1', 'derived', ['sample_lid'])
    for section, count in [('tray', 48), ('oven', 12)]:
        add(section + '.occupancy', {'type': 'array', 'items': _hs8697_nullable(text), 'minItems': count, 'maxItems': count}, '1', 'scene', [section])
    add('manipulator.position_slot', {'type': 'string', 'enum': ['home'] + slot['enum']}, '1', 'scene', ['manipulator'])
    add('ambient.temperature_k', _hs8697_nullable(n('K', 0)), 'K', 'derived', ['chassis'])
    for z in zone['enum']:
        add('thermal.' + z + '.setpoint_k', _hs8697_nullable(n('K', 0, 573.15)), 'K', 'setpoint', [z], {'capability_id': 'thermal.set_temperature', 'parameter': 'temperature_K', 'fixed_arguments': {'zone': z}})
        add('thermal.' + z + '.actual_k', {'type': 'null'}, 'K', 'sensor', [z])
    add('method.equilibration_time_s', _hs8697_nullable(n('s', 0, 59999.4)), 's', 'setpoint', ['oven'], {'capability_id': 'method.set_equilibration_time', 'parameter': 'duration_s', 'fixed_arguments': {}})
    add('method.pressure_pa', {'type': 'null'}, 'Pa', 'setpoint', ['loop_valve'])
    add('method.shaking_hz', {'type': 'null'}, 'Hz', 'setpoint', ['oven'])
    add('gc.instance_id', _hs8697_nullable(text), '1', 'scene', ['transfer_line'])
    add('gc.connected', boolean, '1', 'scene', ['transfer_line'])
    add('gc.carrier_gas_available', boolean, '1', 'scene', ['transfer_line'])
    vial_schema = obj({'sample_id': text, 'slot_id': _hs8697_nullable(slot), 'volume_m3': {'type': 'number', 'enum': list(AGILENT_HS8697_VIAL_VOLUMES_M3), 'x-unit': 'm3'}, 'sealed': boolean, 'status': {'type': 'string', 'enum': ['loaded', 'transferred', 'measured_synthetic', 'unloaded']}})
    add('vial.status', {'type': 'array', 'items': vial_schema}, '1', 'scene', ['vial_proxy'])
    log_schema = obj({'request_id': text, 'sample_id': text, 'oven_slot': oven, 'gc_instance_id': text, 'time_s': n('s', 0), 'synthetic': {'type': 'boolean', 'const': True}})
    add('injection.log', {'type': 'array', 'items': log_schema}, '1', 'derived', ['transfer_line'])
    add('measurement.chromatogram', _hs8697_nullable(_hs8697_chromatogram_schema()), '1', 'derived', ['display'])
    add('measurement.count', {'type': 'integer', 'minimum': 0, 'x-unit': '1'}, '1', 'derived', ['display'])
    add('sample.truth', {'type': 'object'}, '1', 'process_truth', ['vial_proxy'], visibility='debug')
    return {'asset_type_id': AGILENT_HS8697_TYPE, 'asset_version': '1.0.0', 'catalog_id': 'CHEM-108', 'model': 'Agilent 8697, 48-position tray',
        'capabilities': caps, 'state_slots': states,
        'parameters': {
            'tray_capacity': {'value': 48, 'unit': '1', 'evidence': 'catalog'}, 'oven_capacity': {'value': 12, 'unit': '1', 'evidence': 'catalog'},
            'vial_volumes_m3': {'value': list(AGILENT_HS8697_VIAL_VOLUMES_M3), 'unit': 'm3', 'evidence': 'catalog'},
            'maximum_temperature_k': {'value': 573.15, 'unit': 'K', 'evidence': 'catalog'}, 'minimum_temperature_above_ambient_k': {'value': 5.0, 'unit': 'K', 'evidence': 'catalog'},
            'maximum_time_s': {'value': 59999.4, 'unit': 's', 'evidence': 'catalog'}, 'time_increment_s': {'value': 0.6, 'unit': 's', 'evidence': 'catalog'},
            'pressure_pa': {'value': None, 'unit': 'Pa', 'evidence': 'unknown'}, 'shaking_hz': {'value': None, 'unit': 'Hz', 'evidence': 'unknown'},
            'loop_volume_m3': {'value': None, 'unit': 'm3', 'evidence': 'unknown'}, 'physical_transfer_mol': {'value': None, 'unit': 'mol', 'evidence': 'unknown'},
            'thermal_response_s': {'value': None, 'unit': 's', 'evidence': 'unknown'}, 'measurement_duration_s': {'value': None, 'unit': 's', 'evidence': 'unknown'},
            'lid_maximum_angle_rad': {'value': AGILENT_HS8697_LID_MAX_RAD, 'unit': 'rad', 'evidence': 'assumption'}, 'loading_clearance_rad': {'value': _hs8697_math.pi / 3, 'unit': 'rad', 'evidence': 'assumption'},
            'lid_closed_tolerance_rad': {'value': 1e-6, 'unit': 'rad', 'evidence': 'assumption'},
            'synthetic_profile': {'value': {'times_s': [0, 120], 'step_s': 1, 'center_s': 45, 'width_s': 8, 'amplitude': 1}, 'unit': 'mixed s,1', 'evidence': 'assumption', 'conditions': 'Arbitrary dimensionless Gaussian fixture, unrelated to analyte identity, calibration or vendor timing.'}},
        'scene_binding': {'authority': 'process', 'direction': 'process_to_scene', 'callback': 'scene_adapter(instance_id, public_state_dict)', 'lid_state': 'lid.angle_rad', 'occupancy_states': ['tray.occupancy', 'oven.occupancy'], 'manipulator_state': 'manipulator.position_slot'},
        'limits': ['Synthetic batch fixture only; no headspace partition, pressurization, shaking, flow, thermal kinetics, GC hardware or robot dynamics.',
            'Equilibration time and temperatures are stored targets only; read and measure do not advance host clock.',
            'GC registration and carrier-gas flag are trusted-host assertions; no external GC driver is invoked.',
            'No real gas or material transfer and no calibrated signal or detector units; chromatogram signal is dimensionless.',
            'Scene callback success alone does not prove evaluated Blender poses.', 'API truth filtering excludes host introspection and filesystem isolation.',
            'Deduplication is in-memory per instance and principal; no cross-restart exactly-once claim.']}


# 2026-10 template hook: Henry-law headspace library path (scripts/basic40_templates/agilent_hs8697.py).
_hs8697_export_metadata_base = export_agilent_hs8697_contract_metadata


def export_agilent_hs8697_contract_metadata():
    from scripts.basic40_templates import agilent_hs8697 as _hs8697_templates
    return _hs8697_templates.extend_metadata(_hs8697_export_metadata_base())


class AgilentHS8697:
    """Trusted host for one 48-tray / 12-oven synthetic headspace sampler."""
    def __init__(self, instance_id, *, scene_adapter=None, clock=None, ambient_temperature_K=None):
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError('A nonempty instance_id is required')
        if scene_adapter is not None and not callable(scene_adapter):
            raise ValueError('scene_adapter must be callable')
        if clock is not None and not callable(clock):
            raise ValueError('clock must be host callable')
        if ambient_temperature_K is not None and (not _hs8697_number(ambient_temperature_K) or ambient_temperature_K < 0):
            raise ValueError('ambient temperature must be finite nonnegative kelvin or None')
        self.instance_id = instance_id
        self._scene_adapter = scene_adapter
        self._clock = clock or (lambda: 0.0)
        self._lock = _hs8697_threading.RLock()
        self._metadata = export_agilent_hs8697_contract_metadata()
        self._revision, self._requests, self._samples = 0, {}, {}
        self._principals = {'__agent__': 'agent'}
        self._state = {'power.enabled': False, 'lid.angle_rad': 0.0, 'lid.closed': True, 'tray.occupancy': [None] * 48,
            'oven.occupancy': [None] * 12, 'manipulator.position_slot': 'home', 'ambient.temperature_k': ambient_temperature_K,
            'method.equilibration_time_s': None, 'method.pressure_pa': None, 'method.shaking_hz': None,
            'gc.instance_id': None, 'gc.connected': False, 'gc.carrier_gas_available': False,
            'vial.status': [], 'injection.log': [], 'measurement.chromatogram': None, 'measurement.count': 0, 'sample.truth': {}}
        for zone in ('oven', 'loop_valve', 'transfer_line'):
            self._state['thermal.' + zone + '.setpoint_k'] = None
            self._state['thermal.' + zone + '.actual_k'] = None
        self._agent = AgilentHS8697Session(self, '__agent__')

    def register_principal(self, principal_id, *, visibility):
        with self._lock:
            if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'):
                raise ValueError('Invalid or already registered principal')
            self._principals[principal_id] = visibility

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError('Principal must first be provisioned by trusted host')
        return AgilentHS8697Session(self, principal_id)

    def register_sample(self, sample_id, *, volume_m3, sealed=True, truth=None):
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples:
                raise ValueError('A new nonempty sample ID is required')
            if not _hs8697_number(volume_m3) or volume_m3 not in AGILENT_HS8697_VIAL_VOLUMES_M3 or not isinstance(sealed, bool) or (truth is not None and not isinstance(truth, dict)):
                raise ValueError('Vial volume must be 10/20/22 mL in SI; sealed must be boolean; truth must be dictionary')
            self._samples[sample_id] = {'volume_m3': float(volume_m3), 'sealed': sealed, 'truth': _hs8697_copy.deepcopy(truth or {})}

    def register_template_sample(self, sample_id, *, volume_m3, composition_mol_m3, liquid_volume_m3, sealed=True, truth=None):
        """Trusted host: aqueous vial (library ids -> initial mol/m^3); measure uses Henry-law partition + template GC."""
        from scripts.basic40_templates import agilent_hs8697 as _hs8697_templates
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples or not isinstance(sealed, bool) or (truth is not None and not isinstance(truth, dict)):
                raise ValueError('A new nonempty sample ID, boolean sealed flag and optional truth dictionary are required')
            template = _hs8697_templates.validate_sample(composition_mol_m3, liquid_volume_m3=liquid_volume_m3, vial_volume_m3=volume_m3)
            self._samples[sample_id] = {'volume_m3': float(volume_m3), 'sealed': sealed, 'truth': _hs8697_copy.deepcopy(truth or {}), 'template': template}

    def configure_template_transfer(self, *, loop_volume_m3, gc_column='nonpolar', gc_oven_temperature_K=353.15,
                                    gc_inlet_temperature_K=523.15, gc_fid_temperature_K=573.15, split_ratio=20.0, evidence):
        """Trusted host assumption for template vials: loop volume and the GC method that receives the headspace."""
        from scripts.basic40_templates import agilent_hs8697 as _hs8697_templates
        if evidence != 'assumption':
            raise ValueError("An explicit evidence='assumption' label is required")
        transfer = _hs8697_templates.validate_transfer(loop_volume_m3=loop_volume_m3, gc_column=gc_column, gc_oven_temperature_K=gc_oven_temperature_K,
            gc_inlet_temperature_K=gc_inlet_temperature_K, gc_fid_temperature_K=gc_fid_temperature_K, split_ratio=split_ratio)
        with self._lock:
            self._template_transfer = transfer

    def headspace_transfer(self, sample_id, *, temperature_K=None):
        """Trusted-host/evaluator detail: equilibrium headspace and loop contents, e.g. for AgilentGC8890B.register_template_sample."""
        from scripts.basic40_templates import agilent_hs8697 as _hs8697_templates
        with self._lock:
            sample = self._samples.get(sample_id)
            if sample is None or 'template' not in sample:
                raise ValueError('Unknown or non-template sample')
            temperature = self._state['thermal.oven.setpoint_k'] if temperature_K is None else temperature_K
            transfer = getattr(self, '_template_transfer', None)
            if not _hs8697_number(temperature):
                raise ValueError('An oven setpoint or explicit temperature_K is required')
            return _hs8697_templates.equilibrium(sample['template'], float(temperature), None if transfer is None else transfer['loop_volume_m3'])

    def register_gc(self, gc_instance_id, *, carrier_gas_available=True):
        """Trusted host connection assertion only, not a physical GC command."""
        with self._lock:
            if not isinstance(gc_instance_id, str) or not gc_instance_id or not isinstance(carrier_gas_available, bool):
                raise ValueError('GC identity and boolean carrier-gas flag are required')
            self._state.update({'gc.instance_id': gc_instance_id, 'gc.connected': True, 'gc.carrier_gas_available': carrier_gas_available, 'measurement.chromatogram': None})
            self._revision += 1

    def disconnect_gc(self):
        with self._lock:
            self._state.update({'gc.instance_id': None, 'gc.connected': False, 'gc.carrier_gas_available': False, 'measurement.chromatogram': None})
            self._revision += 1

    def set_ambient_temperature(self, temperature_K):
        with self._lock:
            if not _hs8697_number(temperature_K) or temperature_K < 0:
                raise ValueError('Host ambient must be finite nonnegative kelvin')
            self._state['ambient.temperature_k'] = float(temperature_K)
            self._state['measurement.chromatogram'] = None
            self._revision += 1

    def describe(self, *, asset_type_id=None, instance_id=None):
        return self._agent.describe(asset_type_id=asset_type_id, instance_id=instance_id)

    def read(self, *, instance_id=None, state_ids=None):
        return self._agent.read(instance_id=instance_id, state_ids=state_ids)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        return self._agent.invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)

    def write(self, *, state_id, value, request_id, instance_id=None):
        return self._agent.write(instance_id=instance_id, state_id=state_id, value=value, request_id=request_id)

    def debug_snapshot(self):
        with self._lock:
            return _hs8697_copy.deepcopy({'state': self._state, 'state_revision': self._revision, 'registered_samples': self._samples})

    def sync_scene(self):
        with self._lock:
            if self._scene_adapter is None:
                raise RuntimeError('No scene adapter registered')
            self._scene_adapter(self.instance_id, self._public_scene_state(self._state))

    @staticmethod
    def _public_scene_state(state):
        return _hs8697_copy.deepcopy({k: v for k, v in state.items() if k != 'sample.truth'})

    def _time(self):
        value = self._clock()
        if not _hs8697_number(value) or value < 0:
            raise ValueError('Host clock must return finite nonnegative seconds')
        return float(value)

    @staticmethod
    def _location(state, slot):
        group, index = slot.rsplit('_', 1)
        return state[group + '.occupancy'], int(index) - 1

    def _status(self, state, sample_id, slot_id, status):
        record = {'sample_id': sample_id, 'slot_id': slot_id, 'volume_m3': self._samples[sample_id]['volume_m3'], 'sealed': self._samples[sample_id]['sealed'], 'status': status}
        previous = next((i for i, item in enumerate(state['vial.status']) if item['sample_id'] == sample_id), None)
        if previous is None:
            state['vial.status'].append(record)
        else:
            state['vial.status'][previous] = record


class AgilentHS8697Session:
    """Identity-bound observations and operations; role is never a method input."""
    def __init__(self, host, principal_id):
        if principal_id not in host._principals:
            raise ValueError('Unregistered principal')
        self._host, self._principal_id = host, principal_id

    def _target(self, instance_id):
        return instance_id is None or (isinstance(instance_id, str) and instance_id == self._host.instance_id)

    def _visible(self, sid):
        slot = self._host._metadata['state_slots'].get(sid)
        return bool(slot and (slot['visibility'] == 'agent' or self._host._principals[self._principal_id] in ('debug', 'evaluator')))

    def _response(self, status='succeeded', *, request_id=None, result=None, observations=None, code=None, side_effects='none', now=None):
        messages = {'UNKNOWN_INSTANCE': 'Instance is unavailable.', 'UNKNOWN_CAPABILITY': 'Capability is unavailable.',
            'INVALID_ARGUMENT': 'Arguments do not match the declared schema or supported range.', 'PRECONDITION_FAILED': 'Declared operation preconditions are not satisfied.',
            'ACCESS_DENIED': 'Requested state is unavailable or access is denied.', 'REQUEST_CONFLICT': 'Request ID already belongs to a different operation.',
            'UNSUPPORTED': 'Requested physical or execution layer is not implemented.', 'DEVICE_ERROR': 'Scene callback failed; process state was not committed.'}
        return {'protocol_version': '1.0.0', 'instance_id': self._host.instance_id, 'request_id': request_id if isinstance(request_id, str) else None,
            'operation_id': None, 'status': status, 'state_revision': self._host._revision,
            'clock': {'domain': 'simulation', 'time_s': self._host._time() if now is None else now},
            'result': _hs8697_copy.deepcopy(result or {}), 'observations': _hs8697_copy.deepcopy(observations or {}),
            'error': None if code is None else {'code': code, 'message': messages[code], 'retryable': False, 'side_effects': side_effects}}

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self._host._lock:
            if asset_type_id is not None and instance_id is not None:
                return self._response('rejected', code='INVALID_ARGUMENT')
            if not self._target(instance_id):
                return self._response('rejected', code='UNKNOWN_INSTANCE')
            if asset_type_id is not None and asset_type_id != AGILENT_HS8697_TYPE:
                return self._response('rejected', code='ACCESS_DENIED')
            metadata = _hs8697_copy.deepcopy(self._host._metadata)
            metadata['state_slots'] = {sid: value for sid, value in metadata['state_slots'].items() if self._visible(sid)}
            metadata['capabilities'] = {cid: value for cid, value in metadata['capabilities'].items() if value['availability'] == 'implemented' and (not value['scene_required'] or self._host._scene_adapter is not None)}
            for cap in metadata['capabilities'].values():
                cap['state_ids'] = [sid for sid in cap['state_ids'] if self._visible(sid)]
            metadata.update(instance_id=self._host.instance_id if asset_type_id is None else None, adapter='AgilentHS8697Session', scene_adapter_registered=self._host._scene_adapter is not None)
            return metadata

    def read(self, *, instance_id=None, state_ids=None):
        with self._host._lock:
            if not self._target(instance_id):
                return self._response('rejected', code='UNKNOWN_INSTANCE')
            if state_ids is None:
                state_ids = [sid for sid in self._host._state if self._visible(sid)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids):
                return self._response('rejected', code='INVALID_ARGUMENT')
            if any(not self._visible(sid) for sid in state_ids):
                return self._response('rejected', code='ACCESS_DENIED')
            now, observations = self._host._time(), {}
            for sid in state_ids:
                slot, value = self._host._metadata['state_slots'][sid], self._host._state[sid]
                observations[sid] = {'value': _hs8697_copy.deepcopy(value), 'unit': slot['unit'], 'quality': 'unavailable' if value is None else ('estimated' if slot['origin'] == 'derived' else 'valid'), 'sample_time_s': now, 'sample_window_s': None}
                if value is None:
                    observations[sid]['reason'] = 'Value is unknown or not established; no physical observation is simulated.'
            return self._response(observations=observations, now=now)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        host = self._host
        with host._lock:
            if not self._target(instance_id):
                return self._response('rejected', request_id=request_id, code='UNKNOWN_INSTANCE')
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id:
                return self._response('rejected', request_id=request_id, code='INVALID_ARGUMENT')
            try:
                signature = _hs8697_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            except (TypeError, ValueError, OverflowError):
                return self._response('rejected', request_id=request_id, code='INVALID_ARGUMENT')
            key = (self._principal_id, request_id)
            if key in host._requests:
                old_signature, response = host._requests[key]
                return _hs8697_copy.deepcopy(response) if old_signature == signature else self._response('rejected', request_id=request_id, code='REQUEST_CONFLICT')
            response = self._invoke_once(capability_id, arguments, request_id)
            host._requests[key] = (signature, _hs8697_copy.deepcopy(response))
            return response

    def _invoke_once(self, cid, args, request_id):
        host = self._host
        def reject(code='PRECONDITION_FAILED', status='rejected', side_effects='none'):
            return self._response(status, request_id=request_id, code=code, side_effects=side_effects)
        cap = host._metadata['capabilities'].get(cid)
        if cap is None:
            return reject('UNKNOWN_CAPABILITY')
        if cap['availability'] == 'unsupported':
            return reject('UNSUPPORTED', 'unsupported')
        properties = cap['input_schema']['properties']
        if set(args) != set(properties):
            return reject('INVALID_ARGUMENT')
        for name, spec in properties.items():
            val = args[name]
            valid = (spec['type'] == 'number' and _hs8697_number(val)) or (spec['type'] == 'boolean' and isinstance(val, bool)) or (spec['type'] == 'string' and isinstance(val, str) and bool(val))
            if not valid or ('minimum' in spec and val < spec['minimum']) or ('maximum' in spec and val > spec['maximum']) or ('enum' in spec and val not in spec['enum']):
                return reject('INVALID_ARGUMENT')
        if cap['scene_required'] and host._scene_adapter is None:
            return reject('UNSUPPORTED', 'unsupported')
        if cid == 'template.read':  # public Henry-law reference lookup; no state or scene effect
            from scripts.basic40_templates import agilent_hs8697 as _hs8697_templates
            record = _hs8697_templates.template_read(args['substance_id'])
            return reject('INVALID_ARGUMENT') if record is None else self._response(request_id=request_id, result=record)
        state = _hs8697_copy.deepcopy(host._state)
        if cid in ('sample.transfer', 'thermal.set_temperature', 'method.set_equilibration_time', 'measure') and not state['power.enabled']:
            return reject()
        if cid in ('sample.load', 'sample.unload', 'sample.transfer') and state['lid.angle_rad'] < _hs8697_math.pi / 3:
            return reject()
        now = host._time()
        if cid == 'power.set':
            state['power.enabled'] = args['enabled']
            result = dict(args)
        elif cid == 'lid.set_angle':
            state['lid.angle_rad'] = float(args['angle_rad'])
            state['lid.closed'] = args['angle_rad'] <= 1e-6
            result = {'angle_rad': state['lid.angle_rad'], 'closed': state['lid.closed']}
        elif cid == 'sample.load':
            sample = host._samples.get(args['sample_id'])
            arr, idx = host._location(state, args['slot_id'])
            if sample is None or not sample['sealed'] or arr[idx] is not None or args['sample_id'] in state['tray.occupancy'] + state['oven.occupancy']:
                return reject()
            arr[idx] = args['sample_id']
            state['sample.truth'][args['sample_id']] = _hs8697_copy.deepcopy(sample['truth'])
            host._status(state, args['sample_id'], args['slot_id'], 'loaded')
            result = dict(args)
        elif cid == 'sample.unload':
            arr, idx = host._location(state, args['slot_id'])
            if arr[idx] is None:
                return reject()
            sample_id, arr[idx] = arr[idx], None
            state['sample.truth'].pop(sample_id, None)
            host._status(state, sample_id, None, 'unloaded')
            result = {'sample_id': sample_id, 'slot_id': args['slot_id']}
        elif cid == 'sample.transfer':
            src, si = host._location(state, args['source_slot'])
            dst, di = host._location(state, args['target_slot'])
            if src[si] is None or dst[di] is not None or args['source_slot'] == args['target_slot']:
                return reject()
            sample_id, src[si] = src[si], None
            dst[di] = sample_id
            host._status(state, sample_id, args['target_slot'], 'transferred')
            state['manipulator.position_slot'] = args['target_slot']
            result = {'sample_id': sample_id, **args}
        elif cid == 'thermal.set_temperature':
            ambient = state['ambient.temperature_k']
            if ambient is None or args['temperature_K'] < ambient + 5:
                return reject()
            state['thermal.' + args['zone'] + '.setpoint_k'] = float(args['temperature_K'])
            result = dict(args)
        elif cid == 'method.set_equilibration_time':
            if not _hs8697_math.isclose(args['duration_s'] / 0.6, round(args['duration_s'] / 0.6), rel_tol=0, abs_tol=1e-7):
                return reject('INVALID_ARGUMENT')
            state['method.equilibration_time_s'] = float(args['duration_s'])
            result = {'duration_s': float(args['duration_s'])}
        elif cid == 'measure':
            arr, idx = host._location(state, args['oven_slot'])
            ambient = state['ambient.temperature_k']
            targets = [state['thermal.' + z + '.setpoint_k'] for z in ('oven', 'loop_valve', 'transfer_line')]
            if not state['lid.closed'] or arr[idx] is None or not state['gc.connected'] or not state['gc.carrier_gas_available'] or state['method.equilibration_time_s'] is None or ambient is None or any(t is None or t < ambient + 5 or t > 573.15 for t in targets):
                return reject()
            sample_id = arr[idx]
            spectrum = {'time_s': [float(i) for i in range(121)], 'signal': [_hs8697_math.exp(-0.5 * ((i - 45) / 8) ** 2) for i in range(121)],
                'axis_unit': 's', 'signal_unit': '1', 'synthetic': True, 'quality': 'estimated', 'gc_instance_id': state['gc.instance_id'], 'vial_id': sample_id,
                'physical_transfer_mol': None, 'measurement_duration_s': None}
            state['measurement.chromatogram'] = spectrum
            state['measurement.count'] += 1
            state['injection.log'].append({'request_id': request_id, 'sample_id': sample_id, 'oven_slot': args['oven_slot'], 'gc_instance_id': state['gc.instance_id'], 'time_s': now, 'synthetic': True})
            host._status(state, sample_id, args['oven_slot'], 'measured_synthetic')
            result = {'chromatogram': spectrum, 'synthetic': True}
            if 'template' in host._samples[sample_id]:  # template vial replaces the fixture (uncommitted state until success)
                from scripts.basic40_templates import agilent_hs8697 as _hs8697_templates
                result = _hs8697_templates.measure(host._samples[sample_id]['template'], getattr(host, '_template_transfer', None),
                    state['thermal.oven.setpoint_k'], state['method.equilibration_time_s'])
                if result is None:
                    return reject()
                state['measurement.chromatogram'] = result['chromatogram']
                state['injection.log'][-1]['synthetic'] = False
                host._status(state, sample_id, args['oven_slot'], 'measured_template')
        else:
            return reject('UNSUPPORTED', 'unsupported')
        if cid != 'measure':
            state['measurement.chromatogram'] = None
        if host._scene_adapter is not None:
            try:
                host._scene_adapter(host.instance_id, host._public_scene_state(state))
            except Exception:
                return reject('DEVICE_ERROR', 'failed', 'unknown')
        host._state = state
        host._revision += 1
        return self._response(request_id=request_id, result=result, now=now)

    def write(self, *, state_id, value, request_id, instance_id=None):
        with self._host._lock:
            if not self._target(instance_id):
                return self._response('rejected', request_id=request_id, code='UNKNOWN_INSTANCE')
            if not isinstance(state_id, str) or not self._visible(state_id):
                return self._response('rejected', request_id=request_id, code='ACCESS_DENIED')
            route = self._host._metadata['state_slots'][state_id]['write_via']
            if route is None:
                return self._response('rejected', request_id=request_id, code='ACCESS_DENIED')
            args = dict(route.get('fixed_arguments', {}))
            args[route['parameter']] = value
            return self.invoke(instance_id=instance_id, capability_id=route['capability_id'], arguments=args, request_id=request_id)


# CHEM-097 append-only extension. No liquid, hydraulic or thermal calibration.
import copy as _hplc_copy
import math as _hplc_math

AGILENT_HPLC1260_TYPE = "instrument.agilent_hplc1260"
AGILENT_HPLC1260_DOOR_MAX_RAD = _hplc_math.pi / 2
AGILENT_HPLC1260_FLOW_MIN_M3_S = 1e-9 / 60
AGILENT_HPLC1260_FLOW_MAX_M3_S = 1e-5 / 60
AGILENT_HPLC1260_PRESSURE_MAX_PA = 60_000_000.0


def _hplc_chromatogram_schema():
    n, o = _dr6000_numeric, _dr6000_object
    series = lambda unit: {"type": "array", "items": n(unit), "maxItems": 10001}
    return o({"time_s": series("s"), "signal": series("1"),
        "integrated_signal_s": n("s", 0), "synthetic": {"type": "boolean", "const": True},
        "quality": {"type": "string", "const": "estimated"},
        "axis_unit": {"type": "string", "const": "s"}, "signal_unit": {"type": "string", "const": "1"},
        "sampling": {"type": "string", "const": "host_time_samples"},
        "complete": {"type": "boolean"}, "purity_fraction": {"type": "null"},
        "calibrated_concentration_mol_m3": {"type": "null"},
        "quantitation_status": {"type": "string", "const": "unavailable_no_calibration_or_peak_identification"}})


def export_agilent_hplc1260_contract_metadata():
    """Type metadata only; all undeclared manufacturer dynamics remain unknown."""
    n, o = _dr6000_numeric, _dr6000_object
    b, s = {"type": "boolean"}, {"type": "string"}
    positive = lambda unit: {"type": "number", "exclusiveMinimum": 0, "x-unit": unit}
    nullable = lambda schema: {"anyOf": [schema, {"type": "null"}]}
    angle = n("rad", 0, AGILENT_HPLC1260_DOOR_MAX_RAD)
    gradient = {"type": "array", "minItems": 1, "maxItems": 100, "items": o({"time_s": n("s", 0, 86400), "fraction_b": n("1", 0, 1)})}
    method = {"flow_m3_s": n("m3/s", AGILENT_HPLC1260_FLOW_MIN_M3_S, AGILENT_HPLC1260_FLOW_MAX_M3_S),
        "injection_volume_m3": n("m3", 1e-10, 1e-7), "column_temperature_K": n("K", 0, 358.15),
        "pressure_limit_Pa": {**positive("Pa"), "maximum": AGILENT_HPLC1260_PRESSURE_MAX_PA},
        "wavelength_m": positive("m"), "gradient": gradient,
        "run_duration_s": {**positive("s"), "maximum": 86400}, "sample_interval_s": positive("s"),
        "integration_method": {"type": "string", "enum": ["trapezoidal"]}}
    example_method = {"flow_m3_s": 1e-6 / 60, "injection_volume_m3": 1e-8,
        "column_temperature_K": 303.15, "pressure_limit_Pa": 6e7, "wavelength_m": 254e-9,
        "gradient": [{"time_s": 0.0, "fraction_b": 0.1}, {"time_s": 60.0, "fraction_b": 0.9}],
        "run_duration_s": 60.0, "sample_interval_s": 1.0, "integration_method": "trapezoidal"}
    run_output = o({"operation_id": s, "run_status": {"type": "string", "enum": ["running", "cancelled"]}, "synthetic": {"type": "boolean", "const": True}})
    definitions = {
        "power.set": ({"enabled": b}, o({"enabled": b}), {"enabled": True}, ["power.enabled"], ["chassis"], [], False),
        "door.set_angle": ({"angle_rad": angle}, o({"angle_rad": angle, "closed": b}), {"angle_rad": AGILENT_HPLC1260_DOOR_MAX_RAD}, ["door.angle_rad", "door.closed"], ["injector_door", "injector_module"], ["Synchronous scene adapter registered."], True),
        "sample.load": ({"sample_id": s}, o({"loaded": b, "sample_id": s}), {"sample_id": "synthetic_sample"}, ["sample.loaded", "sample.id", "sample.truth"], ["sample_slot", "sample_vial", "injector_module"], ["Door angle >= pi/3; slot empty; fixture registered by trusted host.", "Synchronous scene adapter registered."], True),
        "sample.unload": ({}, o({"loaded": b, "sample_id": {"type": "null"}}), {}, ["sample.loaded", "sample.id", "sample.truth"], ["sample_slot", "sample_vial", "injector_module"], ["Door angle >= pi/3; slot occupied.", "Synchronous scene adapter registered."], True),
        "mobile_phase.set_available": ({"available": b}, o({"available": b}), {"available": True}, ["mobile_phase.available"], ["solvent_tray", "pump_module"], ["Virtual readiness flag; no reservoir volume, identity or transfer model."], False),
        "chromatography.set_method": (method, o(method), example_method, ["method.configured"] + ["method." + k.lower() for k in method], ["pump_module", "injector_module", "column_oven_module", "detector_module"], ["Power enabled; idle.", "Column target >= host ambient_K - 10 K and <= 358.15 K.", "Gradient starts at 0 s, has strictly increasing times and ends no later than run_duration_s.", "sample_interval_s <= run_duration_s; output grid <= 10001 points."], False),
        "chromatography.measure": ({}, run_output, {}, ["run.status", "run.operation_id", "run.elapsed_s", "measurement.chromatogram", "measurement.count"], ["pump_module", "injector_module", "column_oven_module", "detector_module"], ["Power enabled; injector door closed; sample loaded; mobile phase available; method configured; idle.", "Method thermal and pressure limits are settings; actual temperatures/pressure are not modeled."], False),
        "chromatography.stop": ({}, run_output, {}, ["run.status", "measurement.chromatogram"], ["pump_module", "injector_module", "detector_module"], ["Run active and owned by this identity; preserves already collected synthetic data."], False),
        "chromatography.get_chromatogram": ({}, o({"chromatogram": _hplc_chromatogram_schema()}), {}, [], ["detector_module"], ["A run has started; returns current partial or final data without advancing time."], False),
    }
    caps = {}
    for cid, (inputs, output, example, effects, components, preconditions, scene_required) in definitions.items():
        caps[cid] = {"input_schema": o(inputs), "output_schema": output, "example_arguments": [example],
            "state_ids": effects, "component_ids": components, "preconditions": preconditions,
            "scene_required": scene_required, "availability": "implemented",
            "invocation": "AgilentHPLC1260Runtime.invoke(instance_id=..., capability_id=" + repr(cid) + ", arguments=..., request_id=...)",
            "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "BUSY", "ACCESS_DENIED", "REQUEST_CONFLICT", "UNSUPPORTED", "DEVICE_ERROR"],
            "rejection_side_effects": "none", "scene_failure_side_effects": "unknown",
            "state_effect_timing": {sid: ("on_completion" if sid == "measurement.count" else "immediate") for sid in effects}}
        if cid not in ("chromatography.measure", "chromatography.stop", "chromatography.get_chromatogram"):
            caps[cid]["preconditions"].append("No acquisition is running.")
    caps["chromatography.measure"]["execution"] = {"mode": "asynchronous", "time_domain": "simulation", "supports_cancel": True,
        "completion": "Trusted host_advance(time_s) reaches the caller-specified run_duration_s; count increments once.",
        "partial_failure": "Cancel retains acquired synthetic samples and elapsed time; no physical liquid transfer exists.",
        "on_step_state_ids": ["run.elapsed_s", "measurement.chromatogram"], "on_completion_state_ids": ["run.status", "measurement.count"]}
    for cid in ("chromatography.calibrate", "chromatography.quantify", "chromatography.purity", "hardware.connect", "robot.load_sample"):
        caps[cid] = {"input_schema": o({}), "output_schema": o({}), "example_arguments": [{}], "state_ids": [],
            "component_ids": ["detector_module"], "preconditions": [], "scene_required": False, "availability": "unsupported",
            "invocation": None, "errors": ["UNSUPPORTED"], "rejection_side_effects": "none"}
    slots = {}
    def add(sid, schema, unit, origin, route=None, visibility="agent"):
        slots[sid] = {"value_schema": schema, "unit": unit, "origin": origin, "visibility": visibility,
            "access": "read_write" if route else "read_only", "write_via": route}
    add("power.enabled", b, "1", "setpoint", {"capability_id": "power.set", "parameter": "enabled"})
    add("door.angle_rad", angle, "rad", "scene", {"capability_id": "door.set_angle", "parameter": "angle_rad"})
    add("door.closed", b, "1", "derived")
    add("sample.loaded", b, "1", "scene")
    add("sample.id", nullable(s), "1", "scene")
    add("sample.truth", nullable({"type": "object"}), "1", "process_truth", visibility="debug")
    add("mobile_phase.available", b, "1", "setpoint", {"capability_id": "mobile_phase.set_available", "parameter": "available"})
    add("method.configured", b, "1", "derived")
    for key, schema in method.items():
        add("method." + key.lower(), nullable(schema), schema.get("x-unit", "1"), "setpoint")
    add("column_oven.actual_temperature_k", {"type": "null"}, "K", "derived")
    add("pump.actual_pressure_pa", {"type": "null"}, "Pa", "derived")
    add("run.status", {"type": "string", "enum": ["idle", "running", "succeeded", "cancelled"]}, "1", "derived")
    add("run.operation_id", nullable(s), "1", "derived")
    add("run.elapsed_s", n("s", 0), "s", "derived")
    add("measurement.chromatogram", nullable(_hplc_chromatogram_schema()), "1", "derived")
    add("measurement.count", {"type": "integer", "minimum": 0, "x-unit": "1"}, "1", "derived")
    return {"asset_type_id": AGILENT_HPLC1260_TYPE, "asset_version": "1.0.0", "catalog_id": "CHEM-097",
        "model": "Agilent | 1260 Infinity III LC", "capabilities": caps, "state_slots": slots,
        "parameters": {
            "flow_minimum_m3_s": {"value": AGILENT_HPLC1260_FLOW_MIN_M3_S, "unit": "m3/s", "evidence": "catalog"},
            "flow_maximum_m3_s": {"value": AGILENT_HPLC1260_FLOW_MAX_M3_S, "unit": "m3/s", "evidence": "catalog"},
            "injection_volume_minimum_m3": {"value": 1e-10, "unit": "m3", "evidence": "catalog"},
            "injection_volume_maximum_m3": {"value": 1e-7, "unit": "m3", "evidence": "catalog"},
            "system_maximum_pressure_Pa": {"value": 6e7, "unit": "Pa", "evidence": "catalog", "conditions": "Whole-system ceiling; not a module pressure or predicted operating pressure."},
            "column_maximum_temperature_K": {"value": 358.15, "unit": "K", "evidence": "catalog"},
            "column_minimum_temperature_offset_K": {"value": -10.0, "unit": "K", "evidence": "catalog", "conditions": "MCT catalog capability subject to ambient; model validates target only and does not establish exact MCT hardware configuration."},
            "wavelength_range_m": {"value": None, "unit": "m", "evidence": "unknown"},
            "actual_pressure_Pa": {"value": None, "unit": "Pa", "evidence": "unknown"},
            "actual_temperature_K": {"value": None, "unit": "K", "evidence": "unknown"},
            "acquisition_duration_s": {"value": None, "unit": "s", "evidence": "unknown", "conditions": "Run duration is supplied by caller; not an inferred vendor acquisition speed."},
            "door_maximum_angle_rad": {"value": AGILENT_HPLC1260_DOOR_MAX_RAD, "unit": "rad", "evidence": "geometry_assumption"},
            "door_loading_clearance_rad": {"value": _hplc_math.pi / 3, "unit": "rad", "evidence": "simulation_assumption"},
            "door_closed_tolerance_rad": {"value": 1e-6, "unit": "rad", "evidence": "simulation_assumption"},
            "default_host_ambient_K": {"value": 298.15, "unit": "K", "evidence": "simulation_assumption"},
            "maximum_run_duration_s": {"value": 86400, "unit": "s", "evidence": "workload_assumption"},
            "maximum_samples": {"value": 10001, "unit": "1", "evidence": "workload_assumption"},
            "default_gaussian_peaks": {"value": [[20.0, 2.0, 1.0], [40.0, 3.0, 0.7]], "unit": "s,s,1", "evidence": "synthetic_assumption"}},
        "host": {"advance": "AgilentHPLC1260Runtime.host_advance(time_s)", "clock": "Absolute monotonic host simulation time; read/poll never advance it.",
            "poll": "AgilentHPLC1260Runtime.poll(operation_id=...)", "cancel": "AgilentHPLC1260Runtime.cancel(operation_id=..., request_id=...)"},
        "scene_binding": {"door": "injector_door['q']=door.angle_rad; rotation_euler.z=-q",
            "sample": "sample_vial visible iff sample.loaded", "power": "status_display reflects power.enabled",
            "authority": "process", "direction": "process_to_scene"},
        "limits": ["All acquired signals are arbitrary synthetic Gaussian fixtures, unit 1; they are not calibrated absorbance, detector current, chemical identities or real sample response.",
            "Flow, injection, gradient, wavelength and column-temperature settings are recorded and validated but do not parameterize the arbitrary fixture response.",
            "No hydraulic, thermal, solvent transfer, injection-volume consumption, gradient mixing, equilibration, noise or fault-probability model.",
            "Purity and calibrated concentration remain null; dedicated capabilities explicitly return unsupported.",
            "Actual pressure and temperature remain null; pressure-limit settings alone do not prove a pressure safety interlock.",
            "Host-owned time drives sampling; no implicit stepping in read, describe, poll or chromatogram retrieval.",
            "Scene callback success needs independent evaluated-pose verification; no robot or hardware driver.",
            "Access filtering is an API boundary, not protection against trusted Python-host introspection or filesystem access.",
            "Request deduplication and operation ownership are per-instance, per-principal, in-memory only.",
            "Synchronous callback failure leaves process uncommitted but may have unknown external scene side effects."]}


# 2026-10 template hook: spectral_library hplc_uv.json + uv_vis.json path (scripts/basic40_templates/agilent_hplc1260.py).
_hplc_export_metadata_base = export_agilent_hplc1260_contract_metadata


def export_agilent_hplc1260_contract_metadata():
    from scripts.basic40_templates import agilent_hplc1260 as _hplc_templates
    return _hplc_templates.extend_metadata(_hplc_export_metadata_base())


def _hplc_input_valid(value, spec):
    kind = spec.get("type")
    if kind == "number":
        if not _dr6000_number(value):
            return False
        if any(key in spec and not comparison(value, spec[key]) for key, comparison in (
            ("minimum", lambda a, b: a >= b), ("maximum", lambda a, b: a <= b), ("exclusiveMinimum", lambda a, b: a > b))):
            return False
    elif kind == "boolean":
        if not isinstance(value, bool):
            return False
    elif kind == "string":
        if not isinstance(value, str) or not value:
            return False
    elif kind == "object":
        if not isinstance(value, dict) or set(value) != set(spec["properties"]):
            return False
        if not all(_hplc_input_valid(value[k], schema) for k, schema in spec["properties"].items()):
            return False
    elif kind == "array":
        if not isinstance(value, list) or not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", 10001):
            return False
        if not all(_hplc_input_valid(item, spec["items"]) for item in value):
            return False
    else:
        return False
    return "enum" not in spec or value in spec["enum"]


class AgilentHPLC1260Runtime(HachDR6000):
    """Synthetic LC run adapter. Only host_advance changes the simulation clock."""
    def __init__(self, instance_id, *, scene_adapter=None, ambient_K=298.15):
        if not _dr6000_number(ambient_K) or not 273.15 <= ambient_K <= 358.15:
            raise ValueError("ambient_K must be finite and in the assumed 273.15..358.15 K host envelope")
        self._now = 0.0
        super().__init__(instance_id, scene_adapter=scene_adapter, clock=lambda: self._now)
        self.ambient_K = float(ambient_K)
        self._metadata = export_agilent_hplc1260_contract_metadata()
        self._state = {sid: None for sid in self._metadata["state_slots"]}
        self._state.update({"power.enabled": False, "door.angle_rad": 0.0, "door.closed": True,
            "sample.loaded": False, "mobile_phase.available": False, "method.configured": False,
            "run.status": "idle", "run.elapsed_s": 0.0, "measurement.count": 0})
        self._operations = {}
        self._run_counter = 0
        self._agent = AgilentHPLC1260Session(self, "__agent__")

    def session(self, principal_id):
        if principal_id not in self._principals:
            raise ValueError("Principal must first be registered by the trusted host")
        return AgilentHPLC1260Session(self, principal_id)

    def register_sample(self, sample_id, *, peaks=None, truth=None):
        """Trusted arbitrary fixture registration; peak center/width s and amplitude 1."""
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples or (truth is not None and not isinstance(truth, dict)):
                raise ValueError("A fresh sample ID and optional truth dictionary are required")
            peaks = [(20.0, 2.0, 1.0), (40.0, 3.0, 0.7)] if peaks is None else peaks
            if not isinstance(peaks, (list, tuple)) or len(peaks) > 32:
                raise ValueError("At most 32 synthetic peaks are supported")
            for peak in peaks:
                if (not isinstance(peak, (list, tuple)) or len(peak) != 3 or not all(_dr6000_number(x) for x in peak)
                    or not 0 <= peak[0] <= 86400 or not 1e-6 <= peak[1] <= 86400 or not 0 <= peak[2] <= 1e6):
                    raise ValueError("Peak outside the arbitrary fixture workload envelope")
            self._samples[sample_id] = {"peaks": _hplc_copy.deepcopy(peaks), "truth": _hplc_copy.deepcopy(truth or {})}

    def register_template_sample(self, sample_id, *, composition_mol_m3, truth=None):
        """Trusted host: vial solutes as spectral_library ids -> mol/m^3 (XLogP retention + NIST UV absorbance)."""
        from scripts.basic40_templates import agilent_hplc1260 as _hplc_templates
        with self._lock:
            if not isinstance(sample_id, str) or not sample_id or sample_id in self._samples or (truth is not None and not isinstance(truth, dict)):
                raise ValueError("A fresh sample ID and optional truth dictionary are required")
            template = _hplc_templates.validate_sample(composition_mol_m3)
            self._samples[sample_id] = {"peaks": [], "truth": _hplc_copy.deepcopy(truth or {}), "template": template}

    def scene_bindings(self):
        with self._lock:
            return self._public_scene_state(self._state)

    def debug_snapshot(self):
        with self._lock:
            return _hplc_copy.deepcopy({"state": self._state, "state_revision": self._revision,
                "registered_samples": self._samples, "operations": self._operations, "time_s": self._now})

    def poll(self, *, operation_id, instance_id=None):
        return self._agent.poll(operation_id=operation_id, instance_id=instance_id)

    def cancel(self, *, operation_id, request_id, instance_id=None):
        return self._agent.cancel(operation_id=operation_id, request_id=request_id, instance_id=instance_id)

    def _chromatogram(self, state, elapsed, complete):
        duration, step = state["method.run_duration_s"], state["method.sample_interval_s"]
        count = int(_hplc_math.floor(elapsed / step + 1e-10)) + 1
        axis = [min(i * step, duration) for i in range(count)]
        if complete and axis[-1] < duration - 1e-10:
            axis.append(duration)
        elif complete:
            axis[-1] = duration
        if "template" in self._samples[state["sample.id"]]:
            from scripts.basic40_templates import agilent_hplc1260 as _hplc_templates
            return _hplc_templates.chromatogram(self._samples[state["sample.id"]]["template"], state, axis, complete)
        peaks = self._samples[state["sample.id"]]["peaks"]
        signal = [sum(a * _hplc_math.exp(-0.5 * ((t - c) / w) ** 2) for c, w, a in peaks) for t in axis]
        area = sum((signal[i] + signal[i - 1]) * (axis[i] - axis[i - 1]) / 2 for i in range(1, len(axis)))
        return {"time_s": axis, "signal": signal, "integrated_signal_s": area, "synthetic": True,
            "quality": "estimated", "axis_unit": "s", "signal_unit": "1", "sampling": "host_time_samples", "complete": complete,
            "purity_fraction": None, "calibrated_concentration_mol_m3": None,
            "quantitation_status": "unavailable_no_calibration_or_peak_identification"}

    def host_advance(self, time_s):
        """Trusted scheduler supplies absolute simulation time; never an agent capability."""
        with self._lock:
            if not _dr6000_number(time_s) or time_s < self._now:
                raise ValueError("Host time must be finite, nonnegative and monotonic")
            if time_s == self._now:
                return self._agent.read()
            state = _hplc_copy.deepcopy(self._state)
            operation = None
            if state["run.status"] == "running":
                operation = _hplc_copy.deepcopy(self._operations[state["run.operation_id"]])
                elapsed = min(float(time_s) - operation["started_at_s"], state["method.run_duration_s"])
                complete = elapsed >= state["method.run_duration_s"]
                state["run.elapsed_s"] = elapsed
                state["measurement.chromatogram"] = self._chromatogram(state, elapsed, complete)
                if complete:
                    state["run.status"] = "succeeded"
                    state["measurement.count"] += 1
                operation.update({"status": state["run.status"], "elapsed_s": elapsed,
                    "chromatogram": _hplc_copy.deepcopy(state["measurement.chromatogram"])})
                if self._scene_adapter is not None:
                    self._scene_adapter(self.instance_id, self._public_scene_state(state))
                self._state = state
                self._operations[state["run.operation_id"]] = operation
            self._now = float(time_s)
            self._revision += 1
            return self._agent.read()


class AgilentHPLC1260Session(HachDR6000Session):
    def _response(self, status="succeeded", *, request_id=None, result=None, observations=None, code=None, side_effects="none", operation_id=None):
        if code == "BUSY":
            response = super()._response(status, request_id=request_id, result=result, observations=observations, code="PRECONDITION_FAILED", side_effects=side_effects)
            response["error"].update({"code": "BUSY", "message": "An acquisition owns the instrument resources."})
        else:
            response = super()._response(status, request_id=request_id, result=result, observations=observations, code=code, side_effects=side_effects)
        response["operation_id"] = operation_id
        return response

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self._host._lock:
            if asset_type_id is not None and instance_id is not None:
                return self._response("rejected", code="INVALID_ARGUMENT")
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != AGILENT_HPLC1260_TYPE:
                return self._response("rejected", code="ACCESS_DENIED")
            result = _hplc_copy.deepcopy(self._host._metadata)
            result.pop("host", None)
            result["state_slots"] = {sid: slot for sid, slot in result["state_slots"].items() if self._visible(sid)}
            result["capabilities"] = {cid: cap for cid, cap in result["capabilities"].items()
                if cap["availability"] == "implemented" and (not cap["scene_required"] or self._host._scene_adapter is not None)}
            for cap in result["capabilities"].values():
                cap["state_ids"] = [sid for sid in cap["state_ids"] if self._visible(sid)]
                cap["state_effect_timing"] = {sid: timing for sid, timing in cap["state_effect_timing"].items() if self._visible(sid)}
            result.update({"instance_id": None if asset_type_id else self._host.instance_id,
                "adapter": "AgilentHPLC1260Session", "scene_adapter_registered": self._host._scene_adapter is not None})
            return result

    def _invoke_once(self, *, capability_id, arguments, request_id, instance_id=None):
        h = self._host
        if not self._target(instance_id):
            return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
        if not isinstance(capability_id, str) or not isinstance(request_id, str) or not request_id or not isinstance(arguments, dict):
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        cap = h._metadata["capabilities"].get(capability_id)
        if cap is None:
            return self._response("rejected", request_id=request_id, code="UNKNOWN_CAPABILITY")
        if cap["availability"] == "unsupported":
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        if not _hplc_input_valid(arguments, cap["input_schema"]):
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        if cap["scene_required"] and h._scene_adapter is None:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        if capability_id == "template.read":  # public reference lookup; no state, run or scene effect
            from scripts.basic40_templates import agilent_hplc1260 as _hplc_templates
            record = _hplc_templates.template_read(arguments["substance_id"])
            if record is None:
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            return self._response(request_id=request_id, result=record)
        state = _hplc_copy.deepcopy(h._state)
        if state["run.status"] == "running" and capability_id not in ("chromatography.stop", "chromatography.get_chromatogram"):
            return self._response("rejected", request_id=request_id, code="BUSY")
        if capability_id in ("chromatography.set_method", "chromatography.measure") and not state["power.enabled"]:
            return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
        operation_id, operation, status = None, None, "succeeded"
        if capability_id == "power.set":
            state["power.enabled"] = arguments["enabled"]
            result = dict(arguments)
        elif capability_id == "door.set_angle":
            state["door.angle_rad"] = float(arguments["angle_rad"])
            state["door.closed"] = state["door.angle_rad"] <= 1e-6
            result = {"angle_rad": state["door.angle_rad"], "closed": state["door.closed"]}
        elif capability_id in ("sample.load", "sample.unload"):
            loading = capability_id == "sample.load"
            if state["door.angle_rad"] < _hplc_math.pi / 3 or state["sample.loaded"] == loading or (loading and arguments["sample_id"] not in h._samples):
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            state.update({"sample.loaded": loading, "sample.id": arguments["sample_id"] if loading else None,
                "sample.truth": _hplc_copy.deepcopy(h._samples[arguments["sample_id"]]["truth"]) if loading else None})
            result = {"loaded": loading, "sample_id": state["sample.id"]}
        elif capability_id == "mobile_phase.set_available":
            state["mobile_phase.available"] = arguments["available"]
            result = dict(arguments)
        elif capability_id == "chromatography.set_method":
            duration, step, gradient = arguments["run_duration_s"], arguments["sample_interval_s"], arguments["gradient"]
            if (arguments["column_temperature_K"] < h.ambient_K - 10 or step > duration or duration / step > 10000
                or gradient[0]["time_s"] != 0 or gradient[-1]["time_s"] > duration
                or any(a["time_s"] >= b["time_s"] for a, b in zip(gradient, gradient[1:]))):
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            for key, value in arguments.items():
                state["method." + key.lower()] = _hplc_copy.deepcopy(value)
            state["method.configured"] = True
            result = _hplc_copy.deepcopy(arguments)
        elif capability_id == "chromatography.measure":
            if not all(state[sid] for sid in ("door.closed", "sample.loaded", "mobile_phase.available", "method.configured")):
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            operation_id = h.instance_id + ":hplc_run:" + str(h._run_counter + 1)
            state.update({"run.operation_id": operation_id, "run.status": "running", "run.elapsed_s": 0.0})
            state["measurement.chromatogram"] = h._chromatogram(state, 0.0, False)
            operation = {"owner": self._principal_id, "status": "running", "started_at_s": h._now, "elapsed_s": 0.0,
                "chromatogram": _hplc_copy.deepcopy(state["measurement.chromatogram"])}
            status, result = "accepted", {"operation_id": operation_id, "run_status": "running", "synthetic": True}
        elif capability_id == "chromatography.stop":
            if state["run.status"] != "running":
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            operation_id = state["run.operation_id"]
            if h._operations[operation_id]["owner"] != self._principal_id:
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            operation = _hplc_copy.deepcopy(h._operations[operation_id])
            operation["status"] = state["run.status"] = "cancelled"
            result = {"operation_id": operation_id, "run_status": "cancelled", "synthetic": True}
        elif capability_id == "chromatography.get_chromatogram":
            if state["measurement.chromatogram"] is None:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            return self._response(request_id=request_id, result={"chromatogram": state["measurement.chromatogram"]})
        else:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        if h._scene_adapter is not None:
            try:
                h._scene_adapter(h.instance_id, h._public_scene_state(state))
            except Exception:
                return self._response("failed", request_id=request_id, code="DEVICE_ERROR", side_effects="unknown")
        h._state = state
        h._revision += 1
        if operation is not None:
            h._operations[operation_id] = operation
            if capability_id == "chromatography.measure":
                h._run_counter += 1
        return self._response(status, request_id=request_id, result=result, operation_id=operation_id)

    def poll(self, *, operation_id, instance_id=None):
        h = self._host
        with h._lock:
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            operation = h._operations.get(operation_id) if isinstance(operation_id, str) else None
            if operation is None or operation["owner"] != self._principal_id:
                return self._response("rejected", code="ACCESS_DENIED")
            return self._response(operation["status"], operation_id=operation_id,
                result={"elapsed_s": operation["elapsed_s"], "chromatogram": operation["chromatogram"]})

    def cancel(self, *, operation_id, request_id, instance_id=None):
        h = self._host
        with h._lock:
            if not self._target(instance_id):
                return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
            operation = h._operations.get(operation_id) if isinstance(operation_id, str) else None
            if operation is None or operation["owner"] != self._principal_id:
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            cache_key = (self._principal_id, request_id) if isinstance(request_id, str) and request_id else None
            previous = h._requests.get(cache_key)
            signature = _dr6000_json.dumps(["cancel", {"operation_id": operation_id}], sort_keys=True)
            if previous is not None:
                if previous[0] == signature:
                    return _hplc_copy.deepcopy(previous[1])
                return self._response("rejected", request_id=request_id, code="REQUEST_CONFLICT")
            if cache_key is None:
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            if operation["status"] != "running" or operation_id != h._state["run.operation_id"]:
                response = self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            else:
                response = self._invoke_once(capability_id="chromatography.stop", arguments={}, request_id=request_id, instance_id=instance_id)
            h._requests[cache_key] = (signature, _hplc_copy.deepcopy(response))
            return response


# CHEM-010 Eppendorf 5910 Ri. Append-only, instrument-local implementation.
import copy as _epp_copy
import json as _epp_json
import math as _epp_math
import threading as _epp_threading

EPPENDORF_5910RI_TYPE = "instrument.eppendorf_5910ri"
EPPENDORF_5910RI_LID_MAX_RAD = _epp_math.pi / 2
EPPENDORF_5910RI_SPEED_MIN_RAD_S = 100 * 2 * _epp_math.pi / 60
EPPENDORF_5910RI_SPEED_MAX_RAD_S = 14000 * 2 * _epp_math.pi / 60


def _epp_num(unit, minimum=None, maximum=None):
    schema = {"type": "number", "x-unit": unit}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def _epp_obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required,
            "additionalProperties": False}


def _epp_nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def _epp_curve_schema():
    array = lambda unit: {"type": "array", "items": _epp_num(unit), "minItems": 1, "maxItems": 10002}
    return _epp_obj({"time_s": array("s"), "speed_rad_s": array("rad/s"), "temperature_k": array("K"),
        "synthetic": {"type": "boolean", "const": True}, "quality": {"type": "string", "const": "estimated"},
        "complete": {"type": "boolean"}, "outcome": {"type": "string", "enum": ["running", "succeeded", "cancelled", "failed"]},
        "sample_interval_s": _epp_num("s", 0), "time_domain": {"type": "string", "const": "simulation_relative_to_run"},
        "physical_separation_model": {"type": "null"}})


def export_eppendorf_5910ri_contract_metadata():
    """Static type metadata; neither rotor truth nor loaded sample truth is exported."""
    boolean, text = {"type": "boolean"}, {"type": "string"}
    angle = _epp_num("rad", 0, EPPENDORF_5910RI_LID_MAX_RAD)
    speed = _epp_num("rad/s", EPPENDORF_5910RI_SPEED_MIN_RAD_S, EPPENDORF_5910RI_SPEED_MAX_RAD_S)
    temperature = _epp_num("K", 262.15, 313.15)
    level = {"type": "integer", "minimum": 1, "maximum": 10, "x-unit": "1"}
    config = {"type": "string", "enum": ["large_capacity", "fixed_angle"]}
    method = {"speed_rad_s": speed, "temperature_k": temperature, "duration_s": _epp_num("s", 10, 359940),
              "acceleration_level": level, "braking_level": level}
    specs = {
        "power.set": ("设置电源；断电触发停车且旋转未停时盖锁保持", {"enabled": boolean}, {"enabled": boolean}, {"enabled": True},
            ["power.enabled", "run.status", "lid.locked", "measurement.curve"], ["chassis", "display", "lid_lock"], [], False),
        "lid.set_angle": ("设定样品室盖角度", {"angle_rad": angle}, {"angle_rad": angle, "closed": boolean}, {"angle_rad": EPPENDORF_5910RI_LID_MAX_RAD},
            ["lid.angle_rad", "lid.closed"], ["sample_lid", "lid_lock"], ["转子停转、盖锁解除且无运行任务；场景适配器已注册。"], True),
        "rotor.select": ("选择独立转子配置类别", {"configuration": config}, {"configuration": config}, {"configuration": "large_capacity"},
            ["rotor.configuration", "sample.loaded_slots", "sample.occupied_slots", "alarm.imbalance", "method.configured"], ["rotor", "rotor_slots"],
            ["盖角至少pi/3、槽位为空、宿主已明确提供该合成转子配置；场景适配器已注册。"], True),
        "sample.load": ("把宿主注册的样品放入指定虚拟转子槽位", {"slot_id": text, "sample_id": text}, {"slot_id": text, "sample_id": text, "loaded": boolean},
            {"slot_id": "slot_1", "sample_id": "synthetic_sample_1"}, ["sample.loaded_slots", "sample.occupied_slots", "sample.truth", "alarm.imbalance"],
            ["rotor_slots", "sample_carriers"], ["盖角至少pi/3；选择了转子，指定槽存在且为空；样品由宿主注册且未在其他槽装入；场景已注册。"], True),
        "sample.unload": ("从指定虚拟槽位移除样品", {"slot_id": text}, {"slot_id": text, "loaded": boolean}, {"slot_id": "slot_1"},
            ["sample.loaded_slots", "sample.occupied_slots", "sample.truth", "alarm.imbalance"], ["rotor_slots", "sample_carriers"],
            ["盖角至少pi/3、指定槽已装样且未运行；场景适配器已注册。"], True),
        "centrifuge.set_method": ("写入SI转速、温度、时长与10级加减速设置", method, method,
            {"speed_rad_s": 100 * 2 * _epp_math.pi / 60, "temperature_k": 293.15, "duration_s": 10, "acceleration_level": 1, "braking_level": 1},
            ["method.configured", "method.speed_rad_s", "method.temperature_k", "method.duration_s", "method.acceleration_level", "method.braking_level"],
            ["drive", "refrigeration", "display"], ["电源开启且未运行；整机范围不替代转子限值，启动时另检查宿主转子限值。"], False),
        "centrifuge.measure": ("开始异步合成离心过程；返回速度温度时间曲线", {}, {"operation_id": text, "synthetic": {"type": "boolean", "const": True}}, {},
            ["run.status", "run.operation_id", "run.elapsed_s", "lid.locked", "display.speed_rad_s", "display.temperature_k", "rotor.angle_rad", "measurement.curve", "process.truth"],
            ["drive", "refrigeration", "rotor", "lid_lock", "display"],
            ["电源开启、盖关闭、至少两个样品、已设方法且未运行；宿主转子和过程参数齐全；装载平衡；转速不超显式合成转子限值；场景已注册。"], True),
        "centrifuge.stop": ("请求减速停车；已发生的旋转与曲线保留", {}, {"operation_id": text, "run_status": text}, {},
            ["run.status", "lid.locked", "measurement.curve"], ["drive", "lid_lock", "display"], ["调用者拥有当前正在运行的操作。"], False),
        "centrifuge.get_curve": ("读取已生成的合成速度温度时间曲线，不推进时钟", {}, {"curve": _epp_curve_schema()}, {},
            [], ["display"], ["已开始至少一次运行。"], False),
    }
    caps = {}
    for cid, (name, inp, out, example, state_ids, components, preconditions, scene) in specs.items():
        caps[cid] = {"function_zh": name, "kind": "query" if cid.endswith("get_curve") else "device_command",
            "input_schema": _epp_obj(inp), "output_schema": _epp_obj(out), "example_arguments": [example],
            "state_ids": state_ids, "component_ids": components, "preconditions": preconditions, "scene_required": scene,
            "availability": "implemented", "invocation": "Eppendorf5910Ri.invoke(capability_id=" + repr(cid) + ", arguments=..., request_id=...)",
            "errors": ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "ACCESS_DENIED", "PRECONDITION_FAILED", "MISSING_PARAMETER", "BUSY", "IMBALANCE", "REQUEST_CONFLICT", "UNSUPPORTED", "DEVICE_ERROR"],
            "rejection_side_effects": "none", "scene_failure_side_effects": "unknown",
            "asynchronous": cid == "centrifuge.measure", "partial_failure_zh": "停车和取消先进入减速状态，停转前保持盖锁；保留过程曲线、实际转角与温度，不回滚。",
            "state_effect_timing": {sid: "host_advance" if sid in ("run.elapsed_s", "display.speed_rad_s", "display.temperature_k", "rotor.angle_rad", "process.truth") else "on_success" for sid in state_ids}}
    for cid in ("centrifuge.set_rcf", "centrifuge.predict_sedimentation", "hardware.connect", "robot.load_sample"):
        caps[cid] = {"function_zh": "未实现：转子证据或物理模型不足", "kind": "device_command", "availability": "unsupported",
            "input_schema": _epp_obj({"rcf": _epp_num("1", 0)}) if cid.endswith("set_rcf") else _epp_obj({}),
            "output_schema": _epp_obj({}), "example_arguments": [{"rcf": 100}] if cid.endswith("set_rcf") else [{}],
            "state_ids": [], "component_ids": ["rotor"], "preconditions": [], "scene_required": False, "invocation": None,
            "errors": ["UNSUPPORTED"], "rejection_side_effects": "none"}
    slots = {}
    def add(sid, schema, unit, origin, route=None, visibility="agent"):
        slots[sid] = {"value_schema": schema, "unit": unit, "origin": origin, "visibility": visibility,
            "access": "read_write" if route else "read_only", "write_via": route}
    add("power.enabled", boolean, "1", "setpoint", {"capability_id": "power.set", "parameter": "enabled"})
    add("lid.angle_rad", angle, "rad", "scene", {"capability_id": "lid.set_angle", "parameter": "angle_rad"})
    add("lid.closed", boolean, "1", "derived")
    add("lid.locked", boolean, "1", "derived")
    add("rotor.configuration", _epp_nullable(config), "1", "setpoint", {"capability_id": "rotor.select", "parameter": "configuration"})
    add("rotor.angle_rad", _epp_num("rad", 0), "rad", "scene")
    add("sample.loaded_slots", {"type": "array", "items": _epp_obj({"slot_id": text, "sample_id": text})}, "1", "scene")
    add("sample.occupied_slots", {"type": "array", "items": text}, "1", "scene")
    add("sample.truth", {"type": "object"}, "1", "process_truth", visibility="debug")
    add("method.configured", boolean, "1", "derived")
    for key, schema in method.items():
        add("method." + key, _epp_nullable(schema), schema.get("x-unit", "1"), "setpoint")
    add("display.speed_rad_s", _epp_nullable(_epp_num("rad/s", 0)), "rad/s", "derived")
    add("display.temperature_k", _epp_nullable(_epp_num("K", 0)), "K", "derived")
    add("alarm.imbalance", _epp_nullable(boolean), "1", "derived")
    add("run.status", {"type": "string", "enum": ["idle", "running", "stopping", "succeeded", "cancelled", "failed"]}, "1", "derived")
    add("run.operation_id", _epp_nullable(text), "1", "derived")
    add("run.elapsed_s", _epp_num("s", 0), "s", "derived")
    add("measurement.curve", _epp_nullable(_epp_curve_schema()), "1", "derived")
    add("process.truth", {"type": "object"}, "1", "process_truth", visibility="debug")
    params = {
        "speed_range_rad_s": {"value": [EPPENDORF_5910RI_SPEED_MIN_RAD_S, EPPENDORF_5910RI_SPEED_MAX_RAD_S], "unit": "rad/s", "evidence": "catalog"},
        "temperature_range_k": {"value": [262.15, 313.15], "unit": "K", "evidence": "catalog"},
        "duration_range_s": {"value": [10, 359940], "unit": "s", "evidence": "catalog"},
        "acceleration_levels": {"value": 10, "unit": "1", "evidence": "catalog"},
        "braking_levels": {"value": 10, "unit": "1", "evidence": "catalog"},
        "maximum_rcf_fixed_angle": {"value": 22132, "unit": "1", "evidence": "catalog", "conditions": "Fixed-angle category only; not an enabled rotor operating limit or radius inference."},
        "maximum_capacity_large_capacity_m3": {"value": 0.004, "unit": "m^3", "evidence": "catalog", "conditions": "4 x 0.001 m^3, rotor-dependent; exact rotor/model/configuration unknown; cannot combine with maximum RCF."},
        "lid_maximum_angle_rad": {"value": EPPENDORF_5910RI_LID_MAX_RAD, "unit": "rad", "evidence": "assumption"},
        "loading_clearance_rad": {"value": _epp_math.pi / 3, "unit": "rad", "evidence": "assumption"},
        "lid_closed_tolerance_rad": {"value": 1e-6, "unit": "rad", "evidence": "assumption"},
    }
    for key, unit in {"rotor_radius_m": "m", "rotor_mass_kg": "kg", "rotor_inertia_kg_m2": "kg*m^2", "loaded_sample_mass_kg": "kg",
        "imbalance_threshold_kg_m": "kg*m", "acceleration_rad_s2": "rad/s^2", "braking_rad_s2": "rad/s^2", "temperature_time_constant_s": "s", "sedimentation_rate": "1", "slot_count": "1"}.items():
        params[key] = {"value": None, "unit": unit, "evidence": "unknown", "conditions": "No manufacturer/component value inferred; synthetic host fixtures are separate."}
    return {"asset_type_id": EPPENDORF_5910RI_TYPE, "asset_version": "1.0.0", "catalog_id": "CHEM-010", "model": "Eppendorf 5910 Ri",
        "state_slots": slots, "capabilities": caps, "parameters": params,
        "rotor_configurations": {key: {"model": None, "radius_m": None, "mass_kg": None, "inertia_kg_m2": None, "slot_count": None,
            "operating_speed_limit_rad_s": None, "operating_rcf_limit": None, "installed": None} for key in ("large_capacity", "fixed_angle")},
        "scene_binding": {"callback": "scene_adapter(instance_id, public_scene_dict)", "keys": ["lid.angle_rad", "lid.closed", "lid.locked", "rotor.angle_rad", "rotor.configuration", "sample.occupied_slots", "power.enabled", "run.status", "display.speed_rad_s", "display.temperature_k", "alarm.imbalance"],
            "authority": "process", "direction": "process_to_scene", "lid": "lid_hinge.rotation_euler.x=-lid.angle_rad", "rotor": "rotor_spin.rotation_euler.z=rotor.angle_rad"},
        "host_api": ["configure_synthetic_rotor(configuration, slot_positions_xy_m=..., operating_speed_limit_rad_s=..., imbalance_threshold_kg_m=..., evidence='synthetic')",
            "configure_synthetic_process(acceleration_rad_s2=[10 positive levels], braking_rad_s2=[10 positive levels], poweroff_deceleration_rad_s2=..., temperature_time_constant_s=..., ambient_temperature_k=..., initial_temperature_k=..., sample_interval_s=..., evidence='synthetic')",
            "register_sample(sample_id, mass_kg=None, truth=None)", "register_principal(principal_id)", "session(principal_id)", "host_advance(time_s)", "debug_snapshot()", "sync_scene()"],
        "limitations": ["No sample sedimentation/separation, RCF control, hardware or robot execution is implemented.",
            "Curve is synthetic velocity/temperature history, not a spectrum and not measured telemetry; derived display uses an explicit ideal synthetic observation projection.",
            "First-order temperature and linear acceleration/braking require explicit trusted-host synthetic parameters; no physical rotor inertia/torque solution is claimed.",
            "Method duration is synthetic total time from start until braking begins; manufacturer timer-mode details are unknown.",
            "Slot vectors and imbalance threshold are host synthetic fixtures; sample masses/truth and numerical imbalance stay private.",
            "Public API filtering is not a sandbox against Python host introspection or filesystem access.",
            "Scene callback success alone does not establish Blender evaluated-pose verification.",
            "Idempotency is in-memory per principal and instance; no restart persistence or real material transfer is claimed."]}



def _epp_finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return _epp_math.isfinite(value)
    except (ValueError, OverflowError):
        return False


def _epp_valid(value, schema):
    if "anyOf" in schema:
        return any(_epp_valid(value, candidate) for candidate in schema["anyOf"])
    kind = schema.get("type")
    if kind in ("number", "integer"):
        if not _epp_finite(value) or (kind == "integer" and not isinstance(value, int)):
            return False
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
    elif kind == "boolean":
        if not isinstance(value, bool):
            return False
    elif kind == "string":
        if not isinstance(value, str):
            return False
    elif kind == "null":
        return value is None
    elif kind == "object":
        if not isinstance(value, dict):
            return False
        props = schema.get("properties", {})
        if not all(k in value for k in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and any(k not in props for k in value):
            return False
        if any(not _epp_valid(value[k], props[k]) for k in value if k in props):
            return False
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", float("inf")):
            return False
        if any(not _epp_valid(item, schema.get("items", {})) for item in value):
            return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "const" in schema and value != schema["const"]:
        return False
    return True


def _epp_motion(operation, relative_s):
    """Exact integral of the explicitly synthetic linear ramp/hold/brake model."""
    t = min(max(float(relative_s), 0.0), operation["end_s"])
    a, target, d, b = (operation[k] for k in ("acceleration", "speed_target", "decel_start_s", "braking"))
    def up(u):
        ramp = target / a
        if u <= ramp:
            return a * u, 0.5 * a * u * u
        return target, 0.5 * target * ramp + target * (u - ramp)
    if t <= d:
        return up(t)
    speed_at_d, angle_at_d = up(d)
    dt = t - d
    return max(0.0, speed_at_d - b * dt), angle_at_d + speed_at_d * dt - 0.5 * b * dt * dt


def _epp_temperature(operation, relative_s):
    segment = operation["thermal_segments"][0]
    for candidate in operation["thermal_segments"]:
        if candidate[0] > relative_s:
            break
        segment = candidate
    start, initial, target = segment
    return target + (initial - target) * _epp_math.exp(-(relative_s - start) / operation["temperature_tau_s"])


class Eppendorf5910Ri:
    """Trusted host; expose .session() to an agent, not this Python object.

    Public commands never accept truth, masses, physics constants or identities.
    All dynamics require separately configured, explicitly synthetic host fixtures.
    """
    def __init__(self, instance_id, *, scene_adapter=None):
        if not isinstance(instance_id, str) or not instance_id or (scene_adapter is not None and not callable(scene_adapter)):
            raise ValueError("Invalid instance identifier or scene adapter")
        self.instance_id = instance_id
        self._scene_adapter = scene_adapter
        self._metadata = export_eppendorf_5910ri_contract_metadata()
        self._lock = _epp_threading.RLock()
        self._now, self._revision, self._run_counter = 0.0, 0, 0
        self._profiles, self._samples, self._operations, self._requests = {}, {}, {}, {}
        self._process = None
        self._principals = {"__agent__"}
        self._state = {"power.enabled": False, "lid.angle_rad": 0.0, "lid.closed": True, "lid.locked": False,
            "rotor.configuration": None, "rotor.angle_rad": 0.0, "sample.loaded_slots": [], "sample.occupied_slots": [], "sample.truth": {},
            "method.configured": False, "method.speed_rad_s": None, "method.temperature_k": None, "method.duration_s": None,
            "method.acceleration_level": None, "method.braking_level": None, "display.speed_rad_s": None,
            "display.temperature_k": None, "alarm.imbalance": None, "run.status": "idle", "run.operation_id": None,
            "run.elapsed_s": 0.0, "measurement.curve": None, "process.truth": {"speed_rad_s": None, "temperature_k": None, "imbalance_kg_m": None}}
        self._agent = Eppendorf5910RiSession(self, "__agent__")

    def _busy(self, state=None):
        state = self._state if state is None else state
        return state["run.status"] in ("running", "stopping") or bool(state["display.speed_rad_s"])

    def register_principal(self, principal_id):
        """Host identity provisioning. No public capability can create principals."""
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("Invalid principal")
        with self._lock:
            self._principals.add(principal_id)

    def session(self, principal_id="__agent__"):
        return Eppendorf5910RiSession(self, principal_id)

    def configure_synthetic_rotor(self, configuration, *, slot_positions_xy_m, operating_speed_limit_rad_s,
                                  imbalance_threshold_kg_m, evidence):
        """Register virtual geometry/limits, never claim these are vendor rotor data."""
        if (configuration not in ("large_capacity", "fixed_angle") or evidence != "synthetic"
            or not isinstance(slot_positions_xy_m, dict) or not 2 <= len(slot_positions_xy_m) <= 64
            or not _epp_finite(operating_speed_limit_rad_s)
            or not EPPENDORF_5910RI_SPEED_MIN_RAD_S <= operating_speed_limit_rad_s <= EPPENDORF_5910RI_SPEED_MAX_RAD_S
            or not _epp_finite(imbalance_threshold_kg_m) or imbalance_threshold_kg_m < 0):
            raise ValueError("Invalid explicit synthetic rotor configuration")
        for slot, position in slot_positions_xy_m.items():
            if (not isinstance(slot, str) or not slot or not isinstance(position, (list, tuple)) or len(position) != 2
                or not all(_epp_finite(v) for v in position) or _epp_math.hypot(*position) <= 0):
                raise ValueError("Invalid explicit synthetic slot positions")
        with self._lock:
            if self._busy() or (self._state["rotor.configuration"] == configuration and self._state["sample.loaded_slots"]):
                raise RuntimeError("Cannot reconfigure an active or loaded rotor")
            self._profiles[configuration] = {"slot_positions_xy_m": _epp_copy.deepcopy(slot_positions_xy_m),
                "operating_speed_limit_rad_s": float(operating_speed_limit_rad_s),
                "imbalance_threshold_kg_m": float(imbalance_threshold_kg_m), "evidence": "synthetic"}

    def configure_synthetic_process(self, *, acceleration_rad_s2, braking_rad_s2, poweroff_deceleration_rad_s2,
                                    temperature_time_constant_s, ambient_temperature_k, initial_temperature_k,
                                    sample_interval_s, evidence):
        """Explicit simulation constants. No default kinetic or thermal values."""
        if evidence != "synthetic":
            raise ValueError("An explicit synthetic evidence label is required")
        for levels in (acceleration_rad_s2, braking_rad_s2):
            if not isinstance(levels, (list, tuple)) or len(levels) != 10 or not all(_epp_finite(v) and v > 0 for v in levels):
                raise ValueError("Exactly ten positive synthetic acceleration/braking rates are required")
        scalars = (poweroff_deceleration_rad_s2, temperature_time_constant_s, ambient_temperature_k, initial_temperature_k, sample_interval_s)
        if not all(_epp_finite(v) and v > 0 for v in scalars):
            raise ValueError("Positive finite synthetic process parameters are required")
        process = {"acceleration_rad_s2": list(acceleration_rad_s2), "braking_rad_s2": list(braking_rad_s2),
            "poweroff_deceleration_rad_s2": float(poweroff_deceleration_rad_s2), "temperature_time_constant_s": float(temperature_time_constant_s),
            "ambient_temperature_k": float(ambient_temperature_k), "initial_temperature_k": float(initial_temperature_k),
            "sample_interval_s": float(sample_interval_s), "evidence": "synthetic"}
        with self._lock:
            if self._busy():
                raise RuntimeError("Cannot reconfigure active dynamics")
            state = _epp_copy.deepcopy(self._state)
            state["display.speed_rad_s"] = state["process.truth"]["speed_rad_s"] = 0.0
            state["display.temperature_k"] = state["process.truth"]["temperature_k"] = float(initial_temperature_k)
            self._sync_candidate(state)
            self._process, self._state = process, state
            self._revision += 1

    def register_sample(self, sample_id, *, mass_kg=None, truth=None):
        """Host-only registration. Unknown mass remains None and blocks a run."""
        if (not isinstance(sample_id, str) or not sample_id or (mass_kg is not None and (not _epp_finite(mass_kg) or mass_kg <= 0))
            or (truth is not None and not isinstance(truth, dict))):
            raise ValueError("Invalid host sample fixture")
        with self._lock:
            if self._busy() or any(s["sample_id"] == sample_id for s in self._state["sample.loaded_slots"]):
                raise RuntimeError("Cannot replace an active or loaded sample")
            self._samples[sample_id] = {"mass_kg": mass_kg, "truth": _epp_copy.deepcopy(truth or {})}

    def _balance(self, state):
        profile = self._profiles.get(state["rotor.configuration"])
        loaded = state["sample.loaded_slots"]
        value = None
        if profile and loaded and all(self._samples[s["sample_id"]]["mass_kg"] is not None for s in loaded):
            vectors = [(self._samples[s["sample_id"]]["mass_kg"], profile["slot_positions_xy_m"][s["slot_id"]]) for s in loaded]
            value = _epp_math.hypot(_epp_math.fsum(m * p[0] for m, p in vectors), _epp_math.fsum(m * p[1] for m, p in vectors))
        state["process.truth"]["imbalance_kg_m"] = value
        state["alarm.imbalance"] = value > profile["imbalance_threshold_kg_m"] if value is not None else None
        state["sample.occupied_slots"] = [s["slot_id"] for s in loaded]
        state["sample.truth"] = {s["slot_id"]: _epp_copy.deepcopy(self._samples[s["sample_id"]]) for s in loaded}

    def _public_scene_state(self, state):
        return _epp_copy.deepcopy({key: state[key] for key in self._metadata["scene_binding"]["keys"]})

    def _sync_candidate(self, state):
        if self._scene_adapter is not None:
            try:
                self._scene_adapter(self.instance_id, self._public_scene_state(state))
            except Exception:
                raise RuntimeError("Scene synchronization failed; external scene state may be partial") from None

    def sync_scene(self):
        with self._lock:
            if self._scene_adapter is None:
                raise RuntimeError("No scene adapter is registered")
            self._sync_candidate(self._state)

    def debug_snapshot(self):
        """Trusted host inspection, deliberately absent from the agent Session."""
        with self._lock:
            return _epp_copy.deepcopy({"state": self._state, "profiles": self._profiles, "process": self._process,
                "registered_samples": self._samples, "operations": self._operations, "clock_s": self._now, "state_revision": self._revision})

    def describe(self, **arguments):
        return self._agent.describe(**arguments)

    def read(self, **arguments):
        return self._agent.read(**arguments)

    def invoke(self, **arguments):
        return self._agent.invoke(**arguments)

    def write(self, **arguments):
        return self._agent.write(**arguments)

    def poll(self, **arguments):
        return self._agent.poll(**arguments)

    def cancel(self, **arguments):
        return self._agent.cancel(**arguments)

    def _make_operation(self, state, principal, operation_id):
        p = self._process
        speed, duration = state["method.speed_rad_s"], state["method.duration_s"]
        a = p["acceleration_rad_s2"][state["method.acceleration_level"] - 1]
        b = p["braking_rad_s2"][state["method.braking_level"] - 1]
        final = duration + min(speed, a * duration) / b
        return {"owner": principal, "operation_id": operation_id, "status": "running", "outcome": "succeeded", "start_s": self._now,
            "base_angle_rad": state["rotor.angle_rad"], "speed_target": speed, "acceleration": a, "braking": b,
            "decel_start_s": duration, "end_s": final, "sample_interval_s": p["sample_interval_s"], "next_sample_index": 1,
            "temperature_tau_s": p["temperature_time_constant_s"],
            "thermal_segments": [(0.0, state["display.temperature_k"], state["method.temperature_k"])],
            "curve": {"time_s": [0.0], "speed_rad_s": [0.0], "temperature_k": [state["display.temperature_k"]],
                "synthetic": True, "quality": "estimated", "complete": False, "outcome": "running",
                "sample_interval_s": p["sample_interval_s"], "time_domain": "simulation_relative_to_run", "physical_separation_model": None}}

    def _sample_curve(self, op, relative_s):
        t = min(relative_s, op["end_s"])
        curve = op["curve"]
        # The public workload cap is independent of manufacturer timer specifications.
        # A run is rejected up front if its regular grid would exceed this cap.
        while op["next_sample_index"] * op["sample_interval_s"] <= t + 1e-10 and op["next_sample_index"] <= 10000:
            sample_t = op["next_sample_index"] * op["sample_interval_s"]
            if sample_t > op["end_s"] + 1e-10:
                break
            sample_t = min(sample_t, op["end_s"])
            curve["time_s"].append(sample_t)
            curve["speed_rad_s"].append(_epp_motion(op, sample_t)[0])
            curve["temperature_k"].append(_epp_temperature(op, sample_t))
            op["next_sample_index"] += 1
        if relative_s >= op["end_s"]:
            if abs(curve["time_s"][-1] - op["end_s"]) > 1e-10:
                curve["time_s"].append(op["end_s"])
                curve["speed_rad_s"].append(0.0)
                curve["temperature_k"].append(_epp_temperature(op, op["end_s"]))
            op["status"] = op["outcome"]
            curve["complete"], curve["outcome"] = True, op["outcome"]

    def _project_operation(self, state, op, time_s):
        elapsed = max(0.0, time_s - op["start_s"])
        speed, angle = _epp_motion(op, elapsed)
        temperature = _epp_temperature(op, elapsed)
        self._sample_curve(op, elapsed)
        state.update({"display.speed_rad_s": speed, "display.temperature_k": temperature,
            "rotor.angle_rad": op["base_angle_rad"] + angle, "run.elapsed_s": min(elapsed, op["end_s"]),
            "run.status": op["status"] if op["status"] != "running" else ("stopping" if elapsed >= op["decel_start_s"] else "running"),
            "lid.locked": op["status"] == "running", "measurement.curve": _epp_copy.deepcopy(op["curve"])})
        state["process.truth"].update({"speed_rad_s": speed, "temperature_k": temperature})

    def _request_stop(self, state, op, *, poweroff=False):
        elapsed = max(0.0, self._now - op["start_s"])
        current_speed = _epp_motion(op, elapsed)[0]
        # Keep prior historical segments intact; repeated stop never starts a second deceleration.
        if elapsed < op["decel_start_s"]:
            op["decel_start_s"] = elapsed
            if poweroff:
                op["braking"] = self._process["poweroff_deceleration_rad_s2"]
            op["end_s"] = elapsed + current_speed / op["braking"]
        op["outcome"] = "cancelled"
        self._project_operation(state, op, self._now)

    def host_advance(self, time_s):
        """Advance this instance to the trusted world's absolute clock. Reads never call this."""
        with self._lock:
            if not _epp_finite(time_s) or time_s < self._now:
                raise ValueError("Host time must be finite, nonnegative and monotonic")
            if time_s == self._now:
                return self._agent.read()
            state, operations = _epp_copy.deepcopy(self._state), _epp_copy.deepcopy(self._operations)
            current = operations.get(state["run.operation_id"])
            if current is not None and current["status"] == "running":
                self._project_operation(state, current, float(time_s))
            elif self._process is not None:
                target = state["method.temperature_k"] if state["power.enabled"] and state["method.configured"] else self._process["ambient_temperature_k"]
                initial = state["process.truth"]["temperature_k"]
                temperature = target + (initial - target) * _epp_math.exp(-(float(time_s) - self._now) / self._process["temperature_time_constant_s"])
                state["display.temperature_k"] = state["process.truth"]["temperature_k"] = temperature
            self._sync_candidate(state)
            self._state, self._operations, self._now = state, operations, float(time_s)
            self._revision += 1
            return self._agent.read()


class Eppendorf5910RiSession:
    """Agent interface with immutable identity; all state and output paths are filtered."""
    _messages = {"UNKNOWN_INSTANCE": "Unknown instance", "UNKNOWN_CAPABILITY": "Unknown capability", "INVALID_ARGUMENT": "Invalid argument",
        "ACCESS_DENIED": "Access denied", "PRECONDITION_FAILED": "An instrument precondition is not satisfied", "MISSING_PARAMETER": "Trusted host configuration is incomplete",
        "BUSY": "An operation or rotating rotor owns the instrument", "IMBALANCE": "Imbalance alarm prevents a run", "REQUEST_CONFLICT": "Request identifier conflicts with another request",
        "UNSUPPORTED": "Capability or required scene binding is unsupported", "DEVICE_ERROR": "Scene synchronization failed"}

    def __init__(self, host, principal_id):
        if principal_id not in host._principals:
            raise ValueError("Principal is not registered by the host")
        self._host, self._principal_id = host, principal_id

    def _response(self, status="succeeded", *, request_id=None, result=None, observations=None, code=None, operation_id=None, side_effects="none"):
        h = self._host
        return _epp_copy.deepcopy({"protocol_version": "1.0.0", "instance_id": h.instance_id, "request_id": request_id,
            "operation_id": operation_id, "status": status, "state_revision": h._revision, "clock": {"domain": "simulation", "time_s": h._now},
            "result": result if result is not None else {}, "observations": observations if observations is not None else {},
            "error": None if code is None else {"code": code, "message": self._messages[code], "retryable": False, "side_effects": side_effects}})

    def _target(self, instance_id):
        return instance_id is None or instance_id == self._host.instance_id

    def describe(self, *, asset_type_id=None, instance_id=None):
        with self._host._lock:
            if asset_type_id is not None and instance_id is not None:
                return self._response("rejected", code="INVALID_ARGUMENT")
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            if asset_type_id is not None and asset_type_id != EPPENDORF_5910RI_TYPE:
                return self._response("rejected", code="ACCESS_DENIED")
            metadata = _epp_copy.deepcopy(self._host._metadata)
            metadata.pop("host_api", None)
            metadata["state_slots"] = {key: slot for key, slot in metadata["state_slots"].items() if slot["visibility"] == "agent"}
            metadata["capabilities"] = {key: cap for key, cap in metadata["capabilities"].items()
                if cap["availability"] == "implemented" and (not cap["scene_required"] or self._host._scene_adapter is not None)}
            for cap in metadata["capabilities"].values():
                cap["state_ids"] = [key for key in cap["state_ids"] if key in metadata["state_slots"]]
                cap["state_effect_timing"] = {key: value for key, value in cap["state_effect_timing"].items() if key in metadata["state_slots"]}
            metadata.update({"instance_id": None if asset_type_id else self._host.instance_id, "adapter": "Eppendorf5910RiSession",
                "scene_adapter_registered": self._host._scene_adapter is not None})
            return metadata

    def read(self, *, state_ids=None, instance_id=None):
        h = self._host
        with h._lock:
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            allowed = [key for key, slot in h._metadata["state_slots"].items() if slot["visibility"] == "agent"]
            if state_ids is None:
                state_ids = allowed
            if not isinstance(state_ids, list) or any(not isinstance(key, str) for key in state_ids):
                return self._response("rejected", code="INVALID_ARGUMENT")
            if any(key not in allowed for key in state_ids):
                return self._response("rejected", code="ACCESS_DENIED")
            observations = {}
            for key in state_ids:
                value = h._state[key]
                estimated = key.startswith("display.") or key in ("alarm.imbalance", "measurement.curve")
                observations[key] = {"value": _epp_copy.deepcopy(value), "unit": h._metadata["state_slots"][key]["unit"],
                    "quality": "unavailable" if value is None else ("estimated" if estimated else "valid"),
                    "sample_time_s": h._now, "sample_window_s": None}
                if value is None:
                    observations[key]["reason"] = "Not configured, not loaded or not yet acquired"
            return self._response(observations=observations)

    def invoke(self, *, capability_id, arguments, request_id, instance_id=None):
        h = self._host
        with h._lock:
            if not self._target(instance_id):
                return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
            if not isinstance(capability_id, str) or not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id:
                return self._response("rejected", code="INVALID_ARGUMENT")
            try:
                signature = _epp_json.dumps(["invoke", capability_id, arguments], sort_keys=True, allow_nan=False)
            except (ValueError, TypeError, OverflowError):
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            key = (self._principal_id, request_id)
            previous = h._requests.get(key)
            if previous is not None:
                return _epp_copy.deepcopy(previous[1]) if previous[0] == signature else self._response("rejected", request_id=request_id, code="REQUEST_CONFLICT")
            response = self._invoke_once(capability_id, arguments, request_id)
            h._requests[key] = (signature, _epp_copy.deepcopy(response))
            return response

    def _invoke_once(self, capability_id, arguments, request_id):
        h = self._host
        cap = h._metadata["capabilities"].get(capability_id)
        if cap is None:
            return self._response("rejected", request_id=request_id, code="UNKNOWN_CAPABILITY")
        if not _epp_valid(arguments, cap["input_schema"]):
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        if cap["availability"] == "unsupported" or (cap["scene_required"] and h._scene_adapter is None):
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        if capability_id in ("template.read", "sedimentation.estimate"):  # read-only reference/analysis; no state effect
            from scripts.basic40_templates import eppendorf_5910ri as _epp_templates
            result = (_epp_templates.template_read(arguments["substance_id"]) if capability_id == "template.read"
                      else _epp_templates.estimate(arguments, h._state))
            if result is None or isinstance(result, str):
                return self._response("rejected", request_id=request_id, code=result or "INVALID_ARGUMENT")
            return self._response(request_id=request_id, result=result)
        state, operations = _epp_copy.deepcopy(h._state), _epp_copy.deepcopy(h._operations)
        if h._busy(state) and capability_id not in ("power.set", "centrifuge.stop", "centrifuge.get_curve"):
            return self._response("rejected", request_id=request_id, code="BUSY")
        status, operation_id, new_counter = "succeeded", None, h._run_counter
        if capability_id == "power.set":
            state["power.enabled"] = arguments["enabled"]
            op = operations.get(state["run.operation_id"])
            if op is not None and op["status"] == "running":
                elapsed = h._now - op["start_s"]
                target = state["method.temperature_k"] if arguments["enabled"] else h._process["ambient_temperature_k"]
                op["thermal_segments"].append((elapsed, state["display.temperature_k"], target))
                if not arguments["enabled"]:
                    h._request_stop(state, op, poweroff=True)
            result = dict(arguments)
        elif capability_id == "lid.set_angle":
            if state["lid.locked"]:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            state["lid.angle_rad"] = float(arguments["angle_rad"])
            state["lid.closed"] = state["lid.angle_rad"] <= 1e-6
            result = {"angle_rad": state["lid.angle_rad"], "closed": state["lid.closed"]}
        elif capability_id == "rotor.select":
            if state["lid.angle_rad"] < _epp_math.pi / 3 or state["sample.loaded_slots"]:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            if arguments["configuration"] not in h._profiles:
                return self._response("rejected", request_id=request_id, code="MISSING_PARAMETER")
            state["rotor.configuration"] = arguments["configuration"]
            state["method.configured"] = False
            h._balance(state)
            result = dict(arguments)
        elif capability_id in ("sample.load", "sample.unload"):
            profile = h._profiles.get(state["rotor.configuration"])
            if state["lid.angle_rad"] < _epp_math.pi / 3 or profile is None or arguments["slot_id"] not in profile["slot_positions_xy_m"]:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            occupant = next((s for s in state["sample.loaded_slots"] if s["slot_id"] == arguments["slot_id"]), None)
            loading = capability_id == "sample.load"
            if loading:
                if occupant is not None or arguments["sample_id"] not in h._samples or any(s["sample_id"] == arguments["sample_id"] for s in state["sample.loaded_slots"]):
                    return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
                state["sample.loaded_slots"].append(dict(arguments))
            elif occupant is None:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            else:
                state["sample.loaded_slots"].remove(occupant)
            h._balance(state)
            result = dict(arguments, loaded=loading)
        elif capability_id == "centrifuge.set_method":
            if not state["power.enabled"]:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            state.update({"method." + key: value for key, value in arguments.items()})
            state["method.configured"] = True
            result = _epp_copy.deepcopy(arguments)
        elif capability_id == "centrifuge.measure":
            if not state["power.enabled"] or not state["lid.closed"] or not state["method.configured"] or len(state["sample.loaded_slots"]) < 2:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            profile = h._profiles.get(state["rotor.configuration"])
            if profile is None or h._process is None or state["alarm.imbalance"] is None:
                return self._response("rejected", request_id=request_id, code="MISSING_PARAMETER")
            if state["alarm.imbalance"]:
                return self._response("rejected", request_id=request_id, code="IMBALANCE")
            if state["method.speed_rad_s"] > profile["operating_speed_limit_rad_s"]:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            new_counter += 1
            operation_id = h.instance_id + ":centrifuge:" + str(new_counter)
            op = h._make_operation(state, self._principal_id, operation_id)
            if op["end_s"] / op["sample_interval_s"] > 10000:
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            state["run.operation_id"] = operation_id
            h._project_operation(state, op, h._now)
            operations[operation_id] = op
            status, result = "accepted", {"operation_id": operation_id, "synthetic": True}
        elif capability_id == "centrifuge.stop":
            operation_id = state["run.operation_id"]
            op = operations.get(operation_id)
            if op is None or op["status"] != "running":
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            if op["owner"] != self._principal_id:
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            h._request_stop(state, op)
            result = {"operation_id": operation_id, "run_status": state["run.status"]}
        elif capability_id == "centrifuge.get_curve":
            if state["measurement.curve"] is None:
                return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            return self._response(request_id=request_id, result={"curve": state["measurement.curve"]})
        else:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        try:
            h._sync_candidate(state)
        except RuntimeError:
            return self._response("failed", request_id=request_id, code="DEVICE_ERROR", side_effects="unknown")
        h._state, h._operations, h._run_counter = state, operations, new_counter
        h._revision += 1
        return self._response(status, request_id=request_id, result=result, operation_id=operation_id)

    def write(self, *, state_id, value, request_id, instance_id=None):
        h = self._host
        with h._lock:
            if not self._target(instance_id):
                return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
            slot = h._metadata["state_slots"].get(state_id) if isinstance(state_id, str) else None
            if slot is None or slot["visibility"] != "agent" or slot["access"] != "read_write":
                return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            route = slot["write_via"]
            return self.invoke(capability_id=route["capability_id"], arguments={route["parameter"]: value}, request_id=request_id, instance_id=instance_id)

    def poll(self, *, operation_id, instance_id=None):
        h = self._host
        with h._lock:
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            op = h._operations.get(operation_id) if isinstance(operation_id, str) else None
            if op is None or op["owner"] != self._principal_id:
                return self._response("rejected", code="ACCESS_DENIED")
            return self._response(op["status"], operation_id=operation_id, result={"curve": op["curve"]})

    def cancel(self, *, operation_id, request_id, instance_id=None):
        h = self._host
        with h._lock:
            if not self._target(instance_id):
                return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
            if not isinstance(request_id, str) or not request_id or not isinstance(operation_id, str):
                return self._response("rejected", code="INVALID_ARGUMENT")
            key = (self._principal_id, request_id)
            signature = _epp_json.dumps(["cancel", operation_id])
            previous = h._requests.get(key)
            if previous is not None:
                return _epp_copy.deepcopy(previous[1]) if previous[0] == signature else self._response("rejected", request_id=request_id, code="REQUEST_CONFLICT")
            op = h._operations.get(operation_id)
            if op is None or op["owner"] != self._principal_id:
                response = self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            elif operation_id != h._state["run.operation_id"]:
                response = self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")
            else:
                response = self._invoke_once("centrifuge.stop", {}, request_id)
            h._requests[key] = (signature, _epp_copy.deepcopy(response))
            return response


# Terminal time is the exact event authority. Floating-point subtraction must
# not leave an epsilon speed that blocks opening a correctly stopped rotor.
_epp_motion_before_terminal_guard = _epp_motion

def _epp_motion(operation, relative_s):
    speed, angle = _epp_motion_before_terminal_guard(operation, relative_s)
    return (0.0 if relative_s >= operation["end_s"] else speed), angle


_epp_export_metadata_base = export_eppendorf_5910ri_contract_metadata

def export_eppendorf_5910ri_contract_metadata():
    metadata = _epp_export_metadata_base()
    metadata["state_slots"]["process.truth"]["value_schema"] = _epp_obj({
        "speed_rad_s": _epp_nullable(_epp_num("rad/s", 0)),
        "temperature_k": _epp_nullable(_epp_num("K", 0)),
        "imbalance_kg_m": _epp_nullable(_epp_num("kg*m", 0))})
    metadata["state_slots"]["sample.truth"]["value_schema"] = {"type": "object", "description": "Trusted-host-only map keyed by virtual slot ID; not agent-visible.",
        "additionalProperties": _epp_obj({"mass_kg": _epp_nullable(_epp_num("kg", 0)),
            "truth": {"type": "object", "description": "Opaque trusted-host fixture metadata; no numeric field is interpreted as a calibrated physical parameter. This payload is excluded from all public paths."}})}
    metadata["limitations"].extend([
        "Balance is a pre-run static mass-moment check; no changing load, vibration dynamics, or in-run imbalance sensor noise is simulated.",
        "At most 10000 regularly spaced post-start curve samples plus one exact final sample are retained. If an unusually slow host-configured power-off coast extends the duration beyond that grid, the explicit time axis has a gap before the final point; no interpolated samples are fabricated.",
        "Power-off during an existing braking segment preserves that segment's synthetic deceleration. It always keeps the lid locked until the terminal stop event."])
    return metadata


# 2026-10 template hook: sedimentation reference library (scripts/basic40_templates/eppendorf_5910ri.py).
_epp_export_metadata_before_templates = export_eppendorf_5910ri_contract_metadata


def export_eppendorf_5910ri_contract_metadata():
    from scripts.basic40_templates import eppendorf_5910ri as _epp_templates
    return _epp_templates.extend_metadata(_epp_export_metadata_before_templates())
