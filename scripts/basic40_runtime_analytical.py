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
        "asset_type_id": HACH_DR6000_TYPE, "asset_version": "1.1.0", "catalog_id": "CHEM-071", "model": "Hach DR6000",
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
    return {"asset_type_id": JASCO_FTIR4700_TYPE, "asset_version": "1.1.0", "catalog_id": "CHEM-072", "model": "JASCO FT/IR-4700",
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
    return {"asset_type_id": AGILENT_GC8890B_TYPE, "asset_version": "1.1.0", "catalog_id": "CHEM-096",
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
    return {'asset_type_id': AGILENT_HS8697_TYPE, 'asset_version': '1.1.0', 'catalog_id': 'CHEM-108', 'model': 'Agilent 8697, 48-position tray',
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

    def _template_result(self, sample_id, state):
        """Result of a template vial: the equilibrium headspace at the oven setpoint in the host's loop volume."""
        from scripts.basic40_templates import agilent_hs8697 as _hs8697_templates
        return _hs8697_templates.measure(self._samples[sample_id]['template'], getattr(self, '_template_transfer', None),
                                         state['thermal.oven.setpoint_k'], state['method.equilibration_time_s'])

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
                result = host._template_result(sample_id, state)
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
    return {"asset_type_id": AGILENT_HPLC1260_TYPE, "asset_version": "1.1.0", "catalog_id": "CHEM-097",
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
    return {"asset_type_id": EPPENDORF_5910RI_TYPE, "asset_version": "1.1.0", "catalog_id": "CHEM-010", "model": "Eppendorf 5910 Ri",
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


# >>> G00 analytical touch terminals: common helpers
# Shared by the touch-terminal blocks below (CHEM-096 GC 8890B, CHEM-108 HS 8697, CHEM-010 Centrifuge 5910 Ri, CHEM-071 DR6000): settings
# tables (the runtime validates against them, the panel spec builders make the form fields from them), a small JSON-schema check for the
# terminal commands, the panel-host protocol of scripts/asset_runtime/_panel_ui.py and the panel.capture close-up camera.
import copy as _at_copy
import datetime as _at_dt
import hashlib as _at_hashlib
import math as _at_math
import os as _at_os
import re as _at_re
import tempfile as _at_tempfile
from pathlib import Path as _at_Path

_AT_ROOT = _at_Path(__file__).resolve().parents[1]
_AT_EPOCH = _at_dt.datetime(2026, 1, 5, 8, 0, 0)        # device clocks at simulation time 0 until the date / time is set (assumption; a Monday)
_AT_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _at_panel_engine():
    try:
        from .asset_runtime import _panel_ui as engine                      # imported as scripts.basic40_runtime_analytical
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
            spec = importlib.util.spec_from_file_location("_chemsim_panel_ui", _at_Path(__file__).with_name("asset_runtime") / "_panel_ui.py")
            sys.modules["_chemsim_panel_ui"] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules["_chemsim_panel_ui"])
        return sys.modules["_chemsim_panel_ui"]


_at_pu = _at_panel_engine()


class _ATReject(Exception):
    """A terminal command refused: the stable error code (nothing was changed)."""

    def __init__(self, code="PRECONDITION_FAILED"):
        super().__init__(code)
        self.code = code


def _at_need(cond, code="PRECONDITION_FAILED"):
    if not cond:
        raise _ATReject(code)


# ---------------------------------------------------------------- settings tables
def _at_field(key, label, typ, default, page, evidence, **kw):
    """One row of a settings table.  SI values in the runtime; 'show' = (scale, offset, unit, decimals) converts them for the screen."""
    return dict(key=key, label=label, type=typ, default=default, page=page, evidence=evidence, **kw)


def _at_hhmm(v):
    m = _at_re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])", v) if isinstance(v, str) else None
    return None if m is None else int(m.group(1)) * 60 + int(m.group(2))


def _at_same(a, b):
    """Equal values of the same kind (booleans are not numbers)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _at_valid(f, v):
    t = f["type"]
    if v is None:
        return bool(f.get("nullable"))
    if t == "bool":
        return type(v) is bool
    if t == "enum":
        return any(_at_same(v, o) for o in f["options"])
    if t == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not _at_math.isfinite(v):
            return False
        if f.get("integer") and float(v) != int(v):
            return False
        return f["min"] - 1e-9 <= v <= f["max"] + 1e-9
    if t in ("text", "password"):
        return (isinstance(v, str) and f.get("min_chars", 0) <= len(v) <= f.get("max_chars", 64)
                and (not f.get("pattern") or _at_re.fullmatch(f["pattern"], v) is not None))
    if t == "time":
        return _at_hhmm(v) is not None
    return False


def _at_defaults(table):
    return {f["key"]: _at_copy.deepcopy(f["default"]) for f in table}


def _at_apply(table, current, changes):
    """Settings after a partial change set (keys of this table only); INVALID_ARGUMENT when a key is unknown or a value is invalid."""
    fields = {f["key"]: f for f in table}
    _at_need(isinstance(changes, dict) and bool(changes) and all(k in fields for k in changes), "INVALID_ARGUMENT")
    out = dict(current)
    for k, v in changes.items():
        _at_need(_at_valid(fields[k], v), "INVALID_ARGUMENT")
        f = fields[k]
        out[k] = (int(v) if f.get("integer") else float(v)) if f["type"] == "number" and v is not None else v
    return out


def _at_value_schema(f):
    t = f["type"]
    if t == "bool":
        s = {"type": "boolean"}
    elif t == "enum":
        s = {"type": "number" if all(isinstance(o, (int, float)) for o in f["options"]) else "string", "enum": list(f["options"])}
        if s["type"] == "number":
            s["x-unit"] = f.get("si_unit", "1")
    elif t == "number":
        s = {"type": "integer" if f.get("integer") else "number", "x-unit": f.get("si_unit", "1"), "minimum": f["min"], "maximum": f["max"]}
    elif t == "time":
        s = {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$"}
    else:
        s = {"type": "string", "minLength": f.get("min_chars", 0), "maxLength": f.get("max_chars", 64)}
        if f.get("pattern"):
            s["pattern"] = "^(" + f["pattern"] + ")$"
    return {"anyOf": [s, {"type": "null"}]} if f.get("nullable") else s


def _at_settings_schema(table):
    return {"type": "object", "properties": {f["key"]: _at_value_schema(f) for f in table}, "required": [], "additionalProperties": False,
            "minProperties": 1}


# ---------------------------------------------------------------- terminal command arguments (the contract's portable schema subset)
def _at_validate(schema, v, path="$"):
    """Errors of a value against a schema: type / enum / const / bounds / string length and pattern / object / array / anyOf."""
    if "anyOf" in schema:
        return [] if any(not _at_validate(s, v, path) for s in schema["anyOf"]) else [f"{path}: no alternative matches"]
    t = schema.get("type")
    ok = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list), "string": lambda x: isinstance(x, str),
          "boolean": lambda x: type(x) is bool, "null": lambda x: x is None,
          "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
          "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and _at_math.isfinite(x)}
    if t is not None and not ok[t](v):
        return [f"{path}: expected {t}"]
    if "enum" in schema and not any(_at_same(v, e) for e in schema["enum"]):
        return [f"{path}: not in enum"]
    if "const" in schema and v != schema["const"]:
        return [f"{path}: not const"]
    if t in ("number", "integer") and not schema.get("minimum", -_at_math.inf) <= v <= schema.get("maximum", _at_math.inf):
        return [f"{path}: out of range"]
    if t == "string":
        if not schema.get("minLength", 0) <= len(v) <= schema.get("maxLength", 10 ** 9):
            return [f"{path}: length"]
        if "pattern" in schema and _at_re.search(schema["pattern"], v) is None:
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
                err += _at_validate(props[k], x, f"{path}.{k}")
    if t == "array":
        if not schema.get("minItems", 0) <= len(v) <= schema.get("maxItems", 10 ** 9):
            err.append(f"{path}: size")
        for i, x in enumerate(v):
            err += _at_validate(schema.get("items", {}), x, f"{path}[{i}]")
    return err


def _at_num(unit, lo=None, hi=None, integer=False):
    s = {"type": "integer" if integer else "number", "x-unit": unit}
    if lo is not None:
        s["minimum"] = lo
    if hi is not None:
        s["maximum"] = hi
    return s


def _at_obj(props, optional=()):
    return {"type": "object", "properties": props, "required": [k for k in props if k not in optional], "additionalProperties": False}


_AT_STR = {"type": "string"}
_AT_BOOL = {"type": "boolean"}
_AT_ANY = {"type": "object"}
_AT_LIST = {"type": "array", "items": {"type": "object"}}


# ---------------------------------------------------------------- device clock and logs
def _at_now(dev, time_s):
    """Device date / time (ISO, seconds) at a simulation time: epoch + offset set by the date / time page + simulation time."""
    return (_AT_EPOCH + _at_dt.timedelta(seconds=dev["clock_offset_s"] + time_s)).isoformat(timespec="seconds")


def _at_log(lst, entry, cap=500):
    lst.insert(0, entry)
    del lst[cap:]


# ---------------------------------------------------------------- panel host protocol and close-up camera
class _ATPanelHost:
    """What the terminal firmware of one instance sees: host.panel_values() (agent slots and host.* values), the command path a direct call
    takes (refusals raise the engine's PanelRejected with the runtime's code) and the host clock."""

    def __init__(self, host):
        self.host = host

    def panel_read(self):
        return self.host.panel_values()

    def panel_invoke(self, capability_id, arguments):
        r = self.host.panel_command(capability_id, dict(arguments))
        if r["status"] != "succeeded":
            raise _at_pu.PanelRejected((r.get("error") or {}).get("code") or "UNSUPPORTED")
        return r.get("result") or {}

    def panel_time(self):
        return self.host._time()


def _at_capture(panel, instance_id, display_id, time_s, directory=None):
    """panel.capture: the display rendered as a PNG file named by its content (large data as a file reference)."""
    data, w, h = panel.png_bytes(display_id)
    sha = _at_hashlib.sha256(data).hexdigest()
    d = _at_Path(directory) if directory else _at_Path(_at_tempfile.gettempdir()) / "chemistry_sim_panel" / instance_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{display_id}_{sha[:16]}.png"
    if not path.exists():
        path.write_bytes(data)
    return dict(display_id=display_id, time_s=time_s, image=dict(path=str(path), format="png", width=w, height=h, sha256=sha))


def _at_panel_cap_entries(panel_caps, every_effect, invocation, components=None):
    """Runtime metadata entries of the panel.* capabilities (same structure as the instrument's own entries)."""
    out = {}
    for name, d in panel_caps.items():
        gesture = name not in ("panel.capture", "panel.frame")
        out[name] = {"input_schema": d["input_schema"], "output_schema": d["output_schema"], "example_arguments": [d["example_arguments"]],
                     "state_ids": list(every_effect) if gesture else [], "component_ids": (components or {}).get(name, d["component_ids"]),
                     "preconditions": ["The panel interprets the gesture by panel_ui.json and calls this runtime's own commands."],
                     "scene_required": False, "availability": "implemented", "visibility": d["visibility"], "kind": d["kind"],
                     "description_zh": d["function_zh"], "invocation": invocation.format(name),
                     "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "ACCESS_DENIED", "REQUEST_CONFLICT"], "rejection_side_effects": "none",
                     "scene_failure_side_effects": "unknown"}
    return out
# <<< G00 analytical touch terminals: common helpers


# >>> CHEM-096 GC 8890B touch terminal
# Device layer of the 8890B touchscreen (operating manual references/manuals/agilent_gc8890b/, 'p.N' = PDF page): the Active Method (oven
# program, front split/splitless inlet, column 1, front FID, analog out 1, run table), Prep Run / Start / Stop with the oven and column
# programs, readiness with the status tray and status indicator, warnings and shutdowns with their setpoint actions, diagnostic tests, EMF
# counters and maintenance procedures, logs, configuration, scheduler and clock table, sensor zeroing, system settings, power and the front
# panel (panel_ui.json run by scripts/asset_runtime/_panel_ui.py).  Appended to the CHEM-096 runtime above, whose capabilities keep their
# meaning.  Sensor error parameters (flow and pressure sensor offsets, inlet leak, restrictions, FID leakage) are evaluator settings and are
# 0 (ideal) by default.  Configuration: front SSL inlet, column 1, front FID, no ALS / valves / cryo (the asset's single flow path).
import bisect as _g8_bisect
import dataclasses as _g8_dataclasses
import textwrap as _g8_textwrap

GC8890B_ASSET_VERSION = "1.5.1"  # 1.4.0 (2026-10-20): the 8890 front panel in the 3D model; 1.5.0 (2026-10-21): body lowered to the 8890 proportions (geometry only); 1.5.1: oven-interior limitation documented
GC8890B_ASSET_DIR = _AT_ROOT / "assets/basic40_batch02/t3_analytical/agilent_gc8890b"
_G8_ML = 1e-6 / 60.0                                     # m3/s per mL/min
_G8_PSI = 6894.757293168                                 # Pa per psi
_G8_ATM = 101325.0
_G8_T_NTP = 298.15                                       # flows are corrected to 25 C and 1 atm (p.93)
_G8_BOOT_S = 30.0                                        # power-up until 'Power on successful' (assumption)
_G8_TOL_K = 1.0                                          # readiness band of a heated zone (gc_runtime Method.temperature_tolerance_K)
_G8_ZONE_DWELL_S = 5.0                                   # inlet / detector stable time before ready (gc_runtime Method.ready_dwell_s)
_G8_FID_BASELINE_A = 5e-12                               # FID output with the flame lit and no sample (assumption: a few pA)
_G8_IGNITE_K = 423.15                                    # the flame does not light below 150 C (p.96)
_G8_TRIES = 3                                            # reignition attempts before the detector shuts down (p.95)
_G8_FLOW_SHUTDOWN_S = 120.0                              # inlet flow shutdown 2 min after the flow cannot reach setpoint (p.34)
_G8_SAFE_K = 323.15                                      # maintenance procedures wait until the zones are below 50 C (assumption)
_G8_STEP_S = 1.0                                         # sub-step of host_step for programs, readiness and the plot (assumption)
_G8_PLOT_KEEP_S = 3600.0
_G8_SLEEP_DEFER_S = 900.0                                # SLEEP DEFERRED: sleep after 15 min idle (p.34)
_G8_VISCOSITY = {"He": (1.96e-5, 0.69), "H2": (8.8e-6, 0.68), "N2": (1.76e-5, 0.70), "Ar/Me": (2.2e-5, 0.81)}   # Pa s at 293.15 K, T exponent
G8_PRESSURE_UNITS = {"psi": _G8_PSI, "bar": 1e5, "kPa": 1e3}
G8_LANGUAGES = ("English", "Deutsch", "Español", "Français", "Italiano", "Português", "中文", "日本語", "한국어", "Русский")
G8_TIME_ZONES = tuple(f"UTC{h:+03d}" for h in range(-12, 15))
_G8_C = (1.0, -273.15, "°C", 2)                          # screen conversions (scale, offset, unit, decimals) of SI values
_G8_MIN = (1 / 60.0, 0.0, "min", 2)
_G8_MLMIN = (1 / _G8_ML, 0.0, "mL/min", 3)


def _g8_ramp_fields(k):
    return [_at_field(f"ramp_{k}_rate_K_s", f"Rate {k}", "number", 0.0, "oven", "manual p.90-91 (up to 32 ramps: Rate, Final temperature, Final time)",
                      min=0.0, max=2.0, si_unit="K/s", show=(60.0, 0.0, "°C/min", 2)),
            _at_field(f"ramp_{k}_final_K", f"Final temperature {k}", "number", 323.15, "oven", "manual p.90", min=302.15, max=723.15, si_unit="K",
                      show=_G8_C),
            _at_field(f"ramp_{k}_final_time_s", f"Final time {k}", "number", 0.0, "oven", "manual p.90", min=0.0, max=59999.4, si_unit="s", show=_G8_MIN)]


G8_METHOD = {
    "oven": [_at_field("heater_on", "Heater", "bool", True, "oven", "manual p.80 (Select the Heater On checkbox)"),
             _at_field("initial_K", "Oven temperature", "number", 353.15, "oven", "manual p.91 (Enter the oven temperature)", min=302.15,
                       max=723.15, si_unit="K", show=_G8_C, write_via="thermal.set_temperature"),
             _at_field("initial_time_s", "Initial time", "number", 300.0, "oven", "manual p.91 (Initial time = duration of an isothermal run)",
                       min=0.0, max=59999.4, si_unit="s", show=_G8_MIN),
             *[f for k in range(1, 33) for f in _g8_ramp_fields(k)],
             _at_field("equilibration_time_s", "Equilibration time", "number", 15.0, "oven", "manual p.82 (oven equilibration time begins in Pre Run)",
                       min=0.0, max=59999.4, si_unit="s", show=_G8_MIN, assumption="default 0.25 min")],
    "inlet": [_at_field("heater_on", "Heater", "bool", True, "inlet", "manual p.86 (Figure 36: Heater)"),
              _at_field("temperature_K", "Heater temperature", "number", 523.15, "inlet", "manual p.86 (heater temperature)", min=298.15,
                        max=673.15, si_unit="K", show=_G8_C, write_via="thermal.set_temperature"),
              _at_field("mode", "Mode", "enum", "Split", "inlet", "manual p.82 (split and splitless modes of the inlet)",
                        options=["Split", "Splitless"], assumption="pulsed modes of the SSL are not modelled"),
              _at_field("split_ratio", "Split ratio", "number", 20.0, "inlet", "manual p.177 (enter split ratios directly)", min=1.0, max=7500.0,
                        show=(1.0, 0.0, ":1", 1)),
              _at_field("purge_flow_m3_s", "Purge flow to split vent", "number", 50 * _G8_ML, "inlet", "manual p.87 (splitless: purge flow)",
                        min=0.0, max=1250 * _G8_ML, si_unit="m3/s", show=_G8_MLMIN),
              _at_field("purge_time_s", "Purge time", "number", 30.0, "inlet", "manual p.87 (splitless: Purge time, 2 min in the figure)", min=0.0,
                        max=59999.4, si_unit="s", show=_G8_MIN),
              _at_field("septum_purge_on", "Septum purge", "bool", True, "inlet", "manual p.84 (Inlet septum purge flow event)"),
              _at_field("septum_purge_m3_s", "Septum purge flow", "number", 3 * _G8_ML, "inlet", "manual p.86 (inlet flows)", min=0.0,
                        max=30 * _G8_ML, si_unit="m3/s", show=_G8_MLMIN),
              _at_field("gas_saver", "Gas saver", "bool", False, "inlet", "manual p.87 (To use gas saver: checkbox)"),
              _at_field("gas_saver_flow_m3_s", "Gas saver flow", "number", 20 * _G8_ML, "inlet",
                        "manual p.87 (Setpoint at least 15 mL/min greater than the column flow)", min=15 * _G8_ML, max=1250 * _G8_ML, si_unit="m3/s",
                        show=_G8_MLMIN),
              _at_field("gas_saver_time_s", "Gas saver time", "number", 120.0, "inlet", "manual p.87 (Input the Time)", min=0.0, max=59999.4,
                        si_unit="s", show=_G8_MIN)],
    "column": [_at_field("mode", "Control mode", "enum", "Constant Flow", "column", "manual p.93 (constant / ramped flow, constant / ramped pressure)",
                         options=["Constant Flow", "Ramped Flow", "Constant Pressure", "Ramped Pressure"]),
               _at_field("on", "On", "bool", True, "column", "manual p.80 (For each column, set the control mode, select the On checkbox)"),
               _at_field("flow_m3_s", "Flow", "number", 1.0 * _G8_ML, "column", "manual p.80, p.96 (capillary 1-5 mL/min)", min=0.01 * _G8_ML,
                         max=100 * _G8_ML, si_unit="m3/s", show=_G8_MLMIN),
               _at_field("pressure_Pa", "Pressure", "number", 10 * _G8_PSI, "column", "manual p.93 (head gauge pressure)", min=0.0, max=100 * _G8_PSI,
                         si_unit="Pa", show="pressure"),
               *[f for k in (1, 2, 3) for f in (
                   _at_field(f"flow_ramp_{k}_rate", f"Flow rate {k}", "number", 0.0, "column", "manual p.93 (up to three ramps)", min=0.0,
                             max=100 * _G8_ML / 60, si_unit="m3/s2", show=(60 / _G8_ML, 0.0, "mL/min/min", 3)),
                   _at_field(f"flow_ramp_{k}_final", f"Final flow {k}", "number", 1.0 * _G8_ML, "column", "manual p.93", min=0.01 * _G8_ML,
                             max=100 * _G8_ML, si_unit="m3/s", show=_G8_MLMIN),
                   _at_field(f"flow_ramp_{k}_hold_s", f"Hold time {k}", "number", 0.0, "column", "manual p.93 (a hold period)", min=0.0,
                             max=59999.4, si_unit="s", show=_G8_MIN),
                   _at_field(f"pressure_ramp_{k}_rate", f"Pressure rate {k}", "number", 0.0, "column", "manual p.93 (up to three ramps)", min=0.0,
                             max=100 * _G8_PSI / 60, si_unit="Pa/s", show="pressure_rate"),
                   _at_field(f"pressure_ramp_{k}_final", f"Final pressure {k}", "number", 10 * _G8_PSI, "column", "manual p.93", min=0.0,
                             max=100 * _G8_PSI, si_unit="Pa", show="pressure"),
                   _at_field(f"pressure_ramp_{k}_hold_s", f"Hold time {k}", "number", 0.0, "column", "manual p.93", min=0.0, max=59999.4,
                             si_unit="s", show=_G8_MIN))]],
    "detector": [_at_field("heater_on", "Heater", "bool", True, "detector", "manual p.80 (set the detector temperature and gas flows)"),
                 _at_field("temperature_K", "Temperature", "number", 573.15, "detector", "manual p.96 (>= 150 C to light, >= 300 C recommended)",
                           min=298.15, max=723.15, si_unit="K", show=_G8_C, write_via="thermal.set_temperature"),
                 _at_field("h2_on", "H2 flow", "bool", False, "detector", "manual p.80 (select all check boxes to turn the detector on)"),
                 _at_field("h2_flow_m3_s", "H2 flow", "number", 30 * _G8_ML, "detector", "manual p.96 (Hydrogen 40 mL/min suggested)", min=0.0,
                           max=100 * _G8_ML, si_unit="m3/s", show=_G8_MLMIN),
                 _at_field("air_on", "Air flow", "bool", False, "detector", "manual p.80"),
                 _at_field("air_flow_m3_s", "Air flow", "number", 300 * _G8_ML, "detector", "manual p.96 (Air 450 mL/min suggested)", min=0.0,
                           max=800 * _G8_ML, si_unit="m3/s", show=_G8_MLMIN),
                 _at_field("makeup_on", "Makeup flow", "bool", False, "detector", "manual p.80"),
                 _at_field("makeup_flow_m3_s", "Makeup flow", "number", 25 * _G8_ML, "detector", "manual p.96 (column plus makeup 50 mL/min)",
                           min=0.0, max=100 * _G8_ML, si_unit="m3/s", show=_G8_MLMIN),
                 _at_field("flame_on", "Flame", "bool", False, "detector", "manual p.95 (Flame On setting; reignition and Lit offset)"),
                 _at_field("electrometer_on", "Electrometer", "bool", True, "detector", "manual p.121 (electrometer)", assumption="on by default")],
    "analog_out": [_at_field("signal", "Signal type", "enum", "Front Signal", "analog_out", "manual p.186 (Figure 70: Signal Type)",
                             options=["Front Signal", "Oven Temperature", "Test Plot"]),
                   _at_field("zero_on", "Zero", "bool", False, "analog_out", "manual p.186 (Figure 70: Zero On)"),
                   _at_field("zero_value", "Zero setpoint", "number", 0.0, "analog_out", "manual p.186 (Figure 70: Zero setpoint)", min=-1e6, max=1e6,
                             show=(1.0, 0.0, "du", 1)),
                   _at_field("range", "Range", "number", 0, "analog_out", "manual p.186 (Figure 70: Range)", min=0, max=13, integer=True),
                   _at_field("fast_peaks", "Fast Peaks", "bool", False, "analog_out", "manual p.186 (Fast Peaks check box)")],
}
G8_EVENT_TYPES = {"Front Det H2 Flow": ("detector", "h2_on"), "Front Det Air Flow": ("detector", "air_on"),
                  "Front Det Makeup Flow": ("detector", "makeup_on"), "Front Inlet Septum Purge": ("inlet", "septum_purge_on"),
                  "Analog Out 1 Zero": ("analog_out", "zero_value")}


def _g8_day_fields(day):
    return [_at_field(f"sched.{day.lower()}.wake_on", f"{day}: Set Wake Method", "bool", False, "scheduler", "manual p.206 (Set Wake Method per day)"),
            _at_field(f"sched.{day.lower()}.wake_time", f"{day}: Wake Time", "time", "07:00", "scheduler", "manual p.206 (Wake Time per day)"),
            _at_field(f"sched.{day.lower()}.sleep_on", f"{day}: Set Sleep Method", "bool", False, "scheduler", "manual p.206 (Set Sleep Method)"),
            _at_field(f"sched.{day.lower()}.sleep_time", f"{day}: Sleep Time", "time", "22:00", "scheduler", "manual p.206 (Sleep Time per day)")]


_G8_IP = r"((25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])"
G8_SETTINGS = [
    _at_field("inlet.carrier_gas", "Carrier Gas Type", "enum", "He", "cfg_inlets", "manual p.174 (Carrier Gas Type)", options=list(_G8_VISCOSITY)),
    _at_field("inlet.readiness", "Readiness", "bool", True, "cfg_inlets", "manual p.174, p.195 (Readiness Enable)"),
    _at_field("column.type", "Column Type", "enum", "Capillary", "cfg_columns", "manual p.177-178", options=["Capillary", "Packed"]),
    _at_field("column.length_m", "Length", "number", 30.0, "cfg_columns", "manual p.176 (m; 0 = not defined)", min=0.0, max=300.0, si_unit="m",
              show=(1.0, 0.0, "m", 2)),
    _at_field("column.diameter_m", "Diameter", "number", 2.5e-4, "cfg_columns", "manual p.176 (mm; 0 = not defined)", min=0.0, max=0.0053, si_unit="m",
              show=(1000.0, 0.0, "mm", 3)),
    _at_field("column.film_m", "Film thickness", "number", 2.5e-7, "cfg_columns", "manual p.176 (microns)", min=0.0, max=1e-4, si_unit="m",
              show=(1e6, 0.0, "µm", 2)),
    _at_field("column.inlet", "Inlet", "enum", "Front Inlet", "cfg_columns", "manual p.177, p.180 (Table 16)", options=["Front Inlet", "Unspecified"]),
    _at_field("column.outlet", "Outlet", "enum", "Front Detector", "cfg_columns", "manual p.177, p.180 (Table 16)", options=["Front Detector", "Other"]),
    _at_field("column.thermal_zone", "Thermal zone", "enum", "GC Oven", "cfg_columns", "manual p.177, p.180 (Table 16)", options=["GC Oven"]),
    _at_field("column.min_temp_K", "Min Temp", "number", 213.15, "cfg_columns", "manual p.177 (Min Temp)", min=73.15, max=723.15, si_unit="K",
              show=_G8_C),
    _at_field("column.max_temp_K", "Max Temp", "number", 598.15, "cfg_columns", "manual p.177 (Max Temp)", min=273.15, max=723.15, si_unit="K",
              show=_G8_C),
    _at_field("column.max_program_K", "Max Program Temp", "number", 623.15, "cfg_columns", "manual p.177 (Max Program Temp)", min=273.15, max=723.15,
              si_unit="K", show=_G8_C),
    _at_field("column.manufacturer", "Manufacturer", "text", "", "cfg_columns", "manual p.177", max_chars=30),
    _at_field("column.serial", "Serial Number", "text", "", "cfg_columns", "manual p.177, p.140 (EMF follows the column serial number)", max_chars=30),
    _at_field("column.part_number", "Part Number", "text", "", "cfg_columns", "manual p.178", max_chars=30),
    _at_field("column.phase", "Phase Tag", "text", "", "cfg_columns", "manual p.178", max_chars=30),
    _at_field("column.id_lock", "Column ID Lock", "bool", False, "cfg_columns", "manual p.176, p.178 (normally Unlocked without a Smart ID key)"),
    _at_field("oven.readiness", "Readiness", "bool", True, "cfg_oven", "manual p.183 (Readiness)"),
    _at_field("oven.standby_on", "Oven Standby", "bool", False, "cfg_oven", "manual p.183 (Oven Standby)"),
    _at_field("oven.standby_temp_K", "Standby Temperature", "number", 323.15, "cfg_oven", "manual p.183", min=302.15, max=723.15, si_unit="K",
              show=_G8_C),
    _at_field("oven.standby_time_s", "Standby Time", "number", 1800.0, "cfg_oven", "manual p.183 (idle time)", min=60.0, max=59999.4, si_unit="s",
              show=_G8_MIN),
    _at_field("oven.max_temp_K", "Maximum temperature", "number", 723.15, "cfg_oven", "manual p.183 (70 to 450 C)", min=343.15, max=723.15,
              si_unit="K", show=_G8_C),
    _at_field("oven.slow_cooldown", "Slow Cool Down Mode", "bool", False, "cfg_oven", "manual p.183 (reduces the oven fan speed during cool down)"),
    _at_field("oven.limit_ballistic", "Limit Ballistic Power", "bool", False, "cfg_oven", "manual p.183 (reduce oven power when heating at maximum rate)"),
    _at_field("detector.makeup_gas", "Makeup gas", "enum", "N2", "cfg_detectors", "manual p.185 (makeup gas type)", options=["N2", "He"]),
    _at_field("detector.lit_offset_A", "Lit Offset", "number", 2e-12, "cfg_detectors", "manual p.95 (default 2.0 pA)", min=0.0, max=99.9e-12, si_unit="A",
              show=(1e12, 0.0, "pA", 1)),
    _at_field("detector.readiness", "Readiness", "bool", True, "cfg_detectors", "manual p.185, p.195"),
    _at_field("misc.pressure_units", "Pressure Units", "enum", "psi", "cfg_misc", "manual p.194 (psi, bar, kPa)", options=list(G8_PRESSURE_UNITS)),
    _at_field("network.dhcp", "Enable DHCP", "bool", True, "sys_network", "manual p.156 (Figure 56)"),
    _at_field("network.host_name", "Host Name", "text", "8890GC", "sys_network", "manual p.156 (Figure 56)", min_chars=1, max_chars=30,
              pattern=r"[A-Za-z0-9-]+"),
    _at_field("network.ip", "IP Address", "text", "10.1.1.101", "sys_network", "manual p.156 (Figure 56)", pattern=_G8_IP),
    _at_field("network.gateway", "Gateway", "text", "10.1.1.1", "sys_network", "manual p.156", pattern=_G8_IP),
    _at_field("network.netmask", "Net Mask", "text", "255.255.252.0", "sys_network", "manual p.156 (Figure 56)", pattern=_G8_IP),
    _at_field("time.zone", "Time Zone", "enum", "UTC+00", "sys_datetime", "manual p.157 (Time Zone drop-down)", options=list(G8_TIME_ZONES)),
    _at_field("locale.language", "Language", "enum", "English", "sys_locale", "manual p.158 (Language drop-down)", options=list(G8_LANGUAGES)),
    _at_field("power.dim_on", "Dim display after", "bool", False, "sys_power", "manual p.159"),
    _at_field("power.dim_after", "Dim after", "number", 10, "sys_power", "manual p.159", min=1, max=999, integer=True),
    _at_field("power.dim_unit", "Dim unit", "enum", "min", "sys_power", "manual p.159 (drop-down)", options=["s", "min", "h"]),
    _at_field("power.dim_to_pct", "Dim to", "number", 20, "sys_power", "manual p.159 (must be less than the display brightness)", min=0, max=99,
              integer=True, si_unit="1"),
    _at_field("power.off_on", "Shut off display after", "bool", False, "sys_power", "manual p.159"),
    _at_field("power.off_after", "Shut off after", "number", 60, "sys_power", "manual p.159", min=1, max=999, integer=True),
    _at_field("power.off_unit", "Shut off unit", "enum", "min", "sys_power", "manual p.159", options=["s", "min", "h"]),
    _at_field("power.brightness_pct", "Display brightness", "number", 100, "sys_power", "manual p.159 (greater than 0)", min=1, max=100, integer=True),
    _at_field("access.pin", "PIN", "text", "0000", "sys_access", "manual p.160-161 (four-digit PIN, default 0000)", pattern=r"[0-9]{4}", min_chars=4,
              max_chars=4),
    _at_field("access.browser_pin", "Access Browser Interface", "bool", False, "sys_access", "manual p.161 (require the PIN for the Browser Interface)"),
    _at_field("setup.data_system", "Connected to a data system", "enum", "No", "sys_setup", "manual p.162 (asked whether the GC is connected)",
              options=["No", "Yes"]),
    _at_field("sched.wake_to_last", "Wake to last active method before sleep", "bool", False, "scheduler", "manual p.206 (Scheduler Options)"),
    _at_field("sched.condition_run", "Perform a conditioning run before waking", "bool", False, "scheduler", "manual p.206 (Scheduler Options)"),
    *[f for day in ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday") for f in _g8_day_fields(day)],
    _at_field("cal.auto_zero_flow", "Auto flow zero", "bool", True, "cal_inlets", "manual p.153 (recommended; after each run)"),
    _at_field("cal.auto_zero_septum", "Auto zero septum purge", "bool", False, "cal_inlets", "manual p.153"),
]
G8_SENSORS = {"inlet_flow": ("Front Inlet total flow", "flow"), "septum_purge": ("Front Inlet septum purge flow", "flow"),
              "inlet_pressure": ("Front Inlet pressure", "pressure"), "h2": ("Front Detector H2 flow", "flow"),
              "air": ("Front Detector air flow", "flow"), "makeup": ("Front Detector makeup flow", "flow")}
G8_ERRORS = {"inlet_flow_offset_m3_s": 1e-5, "septum_purge_offset_m3_s": 1e-6, "inlet_pressure_offset_Pa": 1e4, "h2_offset_m3_s": 1e-5,
             "air_offset_m3_s": 1e-5, "makeup_offset_m3_s": 1e-5, "inlet_leak_m3_s": 1e-5, "split_vent_restriction": 1.0,
             "fid_jet_restriction": 1.0, "fid_leakage_A": 1e-9}
# diagnostic tests (p.122, p.124-126): group, title, duration (assumption), description (Figure 42 for the Leak & Restriction test)
G8_TESTS = {
    "gas_supply": ("Inlets", "Gas Supply Pressure Check", 30.0, "Checks that the carrier gas supply can hold the inlet at its pressure setpoint."),
    "leak_restriction": ("Inlets", "Leak & Restriction Test", 60.0, "1. Verify the inlet can control to a pressure setpoint, validating the basic "
                         "operation of the inlet and flow path. 2. Hold the pressure and monitor the flow error between actual and target column flow."),
    "pressure_decay": ("Inlets", "Pressure Decay Test", 120.0, "Pressurizes the inlet, closes it and measures the pressure decay (leak rate)."),
    "septum_purge": ("Inlets", "Septum Purge Test", 60.0, "Compares the measured septum purge flow with its setpoint."),
    "split_vent": ("Inlets", "Split Vent Restriction Test", 60.0, "Checks the split vent path for a restriction."),
    "fid_jet": ("Detectors", "FID Jet Restriction Test", 60.0, "Checks the FID jet for a restriction (flame off, hydrogen flow)."),
    "fid_leakage": ("Detectors", "FID Leakage Current Test", 60.0, "Measures the FID output with the flame off (leakage current)."),
}
# EMF counters of this configuration (p.134-136, Table 14): component, part, type, default Service Due (None: no default)
G8_EMF = {
    "inlet.gold_seal_inj": ("Inlets", "Gold seal", "injections", 5000), "inlet.gold_seal_time": ("Inlets", "Gold seal", "days", 90),
    "inlet.liner_inj": ("Inlets", "Liner", "injections", 200), "inlet.liner_time": ("Inlets", "Liner", "days", 30),
    "inlet.oring_inj": ("Inlets", "Liner O-ring", "injections", 1000), "inlet.oring_time": ("Inlets", "Liner O-ring", "days", 60),
    "inlet.septum_inj": ("Inlets", "Septum", "injections", 200),
    "inlet.trap_inj": ("Inlets", "Split vent trap", "injections", 10000), "inlet.trap_time": ("Inlets", "Split vent trap", "days", 182),
    "column.injections": ("Columns", "Injections onto column", "injections", None), "column.oven_cycles": ("Columns", "Oven cycles", "runs", None),
    "column.runs": ("Columns", "Run count", "runs", None),
    "detector.collector_inj": ("Detectors", "Collector", "injections", None), "detector.jet_inj": ("Detectors", "Jet", "injections", None),
    "detector.ignitor": ("Detectors", "Ignitor", "ignition attempts", None),
    "instrument.on_time": ("Instrument", "On time", "days", None), "instrument.runs": ("Instrument", "Run count", "runs", None),
    "instrument.filters": ("Instrument", "Filters", "days", None),
}
# maintenance procedures (p.132-133): component, title, zones cooled, counters reset at the end (Maintenance Reset)
G8_PROCEDURES = {
    "maintenance_mode": ("Instrument", "Maintenance Mode", ("oven", "inlet", "fid"), ()),
    "gas_clean_filter": ("Instrument", "Gas Clean Filter Maintenance", ("oven", "inlet", "fid"), ("instrument.filters",)),
    "septum": ("Inlets", "Replace Septum", ("inlet",), ("inlet.septum_inj",)),
    "liner": ("Inlets", "Replace Liner and O-ring", ("inlet",), ("inlet.liner_inj", "inlet.liner_time", "inlet.oring_inj", "inlet.oring_time")),
    "gold_seal": ("Inlets", "Replace Gold Seal", ("inlet", "oven"), ("inlet.gold_seal_inj", "inlet.gold_seal_time")),
    "split_vent_trap": ("Inlets", "Replace Split Vent Trap", ("inlet",), ("inlet.trap_inj", "inlet.trap_time")),
    "fid_jet": ("Detectors", "Replace FID Jet", ("fid", "oven"), ("detector.jet_inj",)),
    "fid_collector": ("Detectors", "Clean FID Collector", ("fid",), ("detector.collector_inj",)),
}
G8_STATUS_PARAMS = {"oven_temp": "Oven Temperature", "inlet_temp": "Front Inlet Temperature", "inlet_pressure": "Front Inlet Pressure",
                    "inlet_total_flow": "Front Inlet Total Flow", "septum_purge": "Front Inlet Septum Purge Flow", "column_flow": "Column 1 Flow",
                    "column_pressure": "Column 1 Pressure", "detector_temp": "Front Detector Temperature", "h2_flow": "Front Detector H2 Flow",
                    "air_flow": "Front Detector Air Flow", "makeup_flow": "Front Detector Makeup Flow", "signal": "Front Detector Signal"}
G8_PLOT_SIGNALS = {"signal": "Front Detector Signal (pA)", "oven_temp": "Oven Temperature (°C)", "column_flow": "Column 1 Flow (mL/min)",
                   "inlet_pressure": "Front Inlet Pressure (psi)"}
G8_CLOCK_TYPES = ("Blank Run", "Prep Run")
G8_CLOCK_FREQ = ("Daily", "Weekdays") + _AT_WEEKDAYS


class GC8890BSwitchedZone(_GC8890BThermalZone):
    """Inlet / detector heater that the method can switch off (Heater checkbox)."""
    on = True

    def advance(self, dt_s, ambient_K, enabled, open_door=False):
        return super().advance(dt_s, ambient_K, enabled and self.on, open_door)


class GC8890BSwitchedOvenZone(GC8890BOvenZone):
    """Oven heater with the Heater checkbox, the Limit Ballistic Power option (half power, assumption) and Slow Cool Down Mode (the fan runs
    slower during cool down: half the cooling conductance, assumption as in the 7890B module)."""
    on = True
    limited = False
    slow_cool = False

    def advance(self, dt_s, ambient_K, enabled, open_door=False):
        full, cooling = self.maximum_power_W, self.cooling_conductance_W_K
        if self.limited:
            self.maximum_power_W = full / 2
        if self.slow_cool:
            self.cooling_conductance_W_K = cooling * 0.5
        try:
            return super().advance(dt_s, ambient_K, enabled and self.on, open_door)
        finally:
            self.maximum_power_W, self.cooling_conductance_W_K = full, cooling


def _g8_zone_copy(cls, zone):
    return cls(**{f.name: getattr(zone, f.name) for f in _g8_dataclasses.fields(_GC8890BThermalZone)})


# ---------------------------------------------------------------- device model
def _g8_new_device():
    method = {c: _at_defaults(t) for c, t in G8_METHOD.items()}
    method["events"] = []
    emf = {k: dict(value=0.0, due_on=d is not None, due=float(d) if d is not None else 100.0, warn_on=False,
                   warn=float(round(0.8 * d)) if d is not None else 80.0, since_s=0.0, resets=[]) for k, (_, _, _, d) in G8_EMF.items()}
    return dict(clock_offset_s=0.0, settings=_at_defaults(G8_SETTINGS), method=method, stored={"SLEEP": None, "WAKE": None, "CONDITION": None},
                supply={"carrier": False, "hydrogen": False, "air": False, "makeup": False}, booting_until=None, power_epoch=0,
                run=None, prep=False, last_run=None, idle_since=0.0, standby=False, sleep=None, events_restore={},
                stable={"oven": 0.0, "inlet": 0.0, "fid": 0.0}, flow_bad_s=0.0, ignition=dict(tries=0, lit_seen=False, failed=False),
                shutdown={}, alerts=[], alert_seq=0, beeps=0, message=None, diag=None, diag_results={}, emf=emf, procedure=None,
                logs={"maintenance": [], "run": [], "system": []}, status_params=["oven_temp", "inlet_pressure", "column_flow", "detector_temp"],
                plot=dict(signal="signal", x_axis_s=1200.0, y_min=None, y_max=None, running=True, zoom=1, data=[]),
                errors={k: 0.0 for k in G8_ERRORS}, zeros={k: 0.0 for k in G8_SENSORS}, zero_times={}, column_comp={},
                clock_table=[], restart_pending=False, last_activity_s=0.0, preview=None, sched_last_min=None, on_time_s=0.0)


def _g8_time(dev, t):
    return _at_dt.datetime.fromisoformat(_at_now(dev, t))


def _g8_sys_log(dev, t, text, kind="system"):
    _at_log(dev["logs"][kind], dict(time=_at_now(dev, t), text=text))


def _g8_program(initial, initial_hold_s, ramps, elapsed_s):
    """Value of a piecewise program (initial value and hold, then up to N ramps of rate / final value / hold; a zero rate ends it) and its
    total duration: oven temperature (p.90) and column flow / pressure (p.93)."""
    v, t = initial, initial_hold_s
    if elapsed_s <= t:
        return v, None
    for rate, final, hold in ramps:
        if rate <= 0:
            break
        dur = abs(final - v) / rate
        if elapsed_s <= t + dur:
            return v + _at_math.copysign(rate * (elapsed_s - t), final - v), None
        v, t = final, t + dur
        if elapsed_s <= t + hold:
            return v, None
        t += hold
    return v, t


def _g8_program_length(initial, initial_hold_s, ramps):
    v, t = initial, initial_hold_s
    for rate, final, hold in ramps:
        if rate <= 0:
            break
        t += abs(final - v) / rate + hold
        v = final
    return t


def _g8_oven_ramps(m):
    o = m["oven"]
    return [(o[f"ramp_{k}_rate_K_s"], o[f"ramp_{k}_final_K"], o[f"ramp_{k}_final_time_s"]) for k in range(1, 33)]


def _g8_column_ramps(m, kind):
    c = m["column"]
    return [(c[f"{kind}_ramp_{k}_rate"], c[f"{kind}_ramp_{k}_final"], c[f"{kind}_ramp_{k}_hold_s"]) for k in (1, 2, 3)]


def _g8_run_length(m):
    return _g8_program_length(m["oven"]["initial_K"], m["oven"]["initial_time_s"], _g8_oven_ramps(m))


def _g8_column_defined(s):
    return s["column.length_m"] > 0 and s["column.diameter_m"] > 0 and s["column.type"] == "Capillary"


def _g8_viscosity(gas, T):
    eta0, n = _G8_VISCOSITY[gas]
    return eta0 * (T / 293.15) ** n


def _g8_pressure_for_flow(s, flow, T):
    """Column head gauge pressure (Pa) for a flow at NTP: compressible Poiseuille flow, outlet at atmosphere (FID), p.93, p.182."""
    if not _g8_column_defined(s) or flow <= 0:
        return 0.0
    L, d = s["column.length_m"], s["column.diameter_m"]
    k = 256 * _g8_viscosity(s["inlet.carrier_gas"], T) * L * _G8_ATM * T / (_at_math.pi * d ** 4 * _G8_T_NTP)
    return _at_math.sqrt(_G8_ATM ** 2 + flow * k) - _G8_ATM


def _g8_flow_for_pressure(s, p_gauge, T):
    if not _g8_column_defined(s) or p_gauge <= 0:
        return 0.0
    L, d = s["column.length_m"], s["column.diameter_m"]
    k = 256 * _g8_viscosity(s["inlet.carrier_gas"], T) * L * _G8_ATM * T / (_at_math.pi * d ** 4 * _G8_T_NTP)
    return ((_G8_ATM + p_gauge) ** 2 - _G8_ATM ** 2) / k


def _g8_holdup(s, p_gauge, T):
    """Holdup time (s) of the column: t_M = 32 eta L^2 (pi^3 - po^3) / (3 r^2 (pi^2 - po^2)^2)."""
    if not _g8_column_defined(s) or p_gauge <= 0:
        return None
    L, r = s["column.length_m"], s["column.diameter_m"] / 2
    pi, po = _G8_ATM + p_gauge, _G8_ATM
    return 32 * _g8_viscosity(s["inlet.carrier_gas"], T) * L ** 2 * (pi ** 3 - po ** 3) / (3 * r ** 2 * (pi ** 2 - po ** 2) ** 2)


def _g8_running(dev):
    return dev["run"] is not None


def _g8_column_setpoint(dev, T, elapsed=None):
    """(flow m3/s, gauge pressure Pa) the column controls to now: the mode's setpoint or program (during a run) at oven temperature T."""
    m, s = dev["method"], dev["settings"]
    c = m["column"]
    if not c["on"]:
        return 0.0, 0.0
    mode = c["mode"]
    if mode.endswith("Flow"):
        flow = c["flow_m3_s"]
        if mode == "Ramped Flow" and elapsed is not None:
            flow = _g8_program(flow, 0.0, _g8_column_ramps(m, "flow"), elapsed)[0]
        return flow, _g8_pressure_for_flow(s, flow, T)
    p = c["pressure_Pa"]
    if mode == "Ramped Pressure" and elapsed is not None:
        p = _g8_program(p, 0.0, _g8_column_ramps(m, "pressure"), elapsed)[0]
    return _g8_flow_for_pressure(s, p, T), p


def _g8_split_vent(dev, column_flow, elapsed):
    """Split vent flow (m3/s): split ratio x column flow (split), closed during Pre Run / run until the purge time (splitless), gas saver."""
    i = dev["method"]["inlet"]
    saver = i["gas_saver"] and not dev["prep"] and (elapsed is None or elapsed >= i["gas_saver_time_s"])
    if i["mode"] == "Split":
        flow = i["split_ratio"] * column_flow
    else:
        flow = 0.0 if (dev["prep"] or (elapsed is not None and elapsed < i["purge_time_s"])) else i["purge_flow_m3_s"]
    if saver:
        flow = min(flow, max(0.0, i["gas_saver_flow_m3_s"] - column_flow))
    return flow


def _g8_errs(dev):
    """Uncorrected errors of the EPC sensors: the evaluator offset less the zero stored by Calibration (p.153-154); all 0 = ideal."""
    e, z = dev["errors"], dev["zeros"]
    return dict(inlet_pressure=e["inlet_pressure_offset_Pa"] - z["inlet_pressure"], inlet_flow=e["inlet_flow_offset_m3_s"] - z["inlet_flow"],
                septum_purge=e["septum_purge_offset_m3_s"] - z["septum_purge"], h2=e["h2_offset_m3_s"] - z["h2"], air=e["air_offset_m3_s"] - z["air"],
                makeup=e["makeup_offset_m3_s"] - z["makeup"])


def _g8_true_column(dev, T, elapsed=None):
    """(flow m3/s, gauge pressure Pa) the column really gets: the EPC holds its pressure reading at the setpoint, so the head pressure is the
    setpoint less the uncorrected sensor error and the column flow follows it (as the 7890B module).  Errors 0: the setpoint."""
    flow, p = _g8_column_setpoint(dev, T, elapsed)
    err = _g8_errs(dev)["inlet_pressure"]
    if p <= 0 or not err:
        return flow, p
    p = max(0.0, p - err)
    return _g8_flow_for_pressure(dev["settings"], p, T), p


def _g8_true_flows(dev, col_flow, elapsed):
    """Flows the controllers really deliver (they hold their readings at the setpoints): split vent (total flow controller), septum purge and
    the detector gases are the setpoints less the uncorrected sensor errors."""
    m, err = dev["method"], _g8_errs(dev)
    i, d = m["inlet"], m["detector"]
    split = _g8_split_vent(dev, col_flow, elapsed) * (1.0 - dev["errors"]["split_vent_restriction"])
    return dict(split_vent=max(0.0, split - err["inlet_flow"]) if split > 0 else 0.0,
                septum_purge=max(0.0, i["septum_purge_m3_s"] - err["septum_purge"]) if i["septum_purge_on"] else 0.0,
                purge=max(0.0, i["purge_flow_m3_s"] - err["inlet_flow"]), h2=max(0.0, d["h2_flow_m3_s"] - err["h2"]),
                air=max(0.0, d["air_flow_m3_s"] - err["air"]), makeup=max(0.0, d["makeup_flow_m3_s"] - err["makeup"]))


def _g8_signal(dev, process):
    """FID output (A): baseline with the flame lit, the evaluator electrometer leakage and the run's chromatogram at run time."""
    if not (process.powered and dev["method"]["detector"]["electrometer_on"]):
        return 0.0
    lit = process.flame_lit
    signal = (_G8_FID_BASELINE_A if lit else 0.0) + dev["errors"]["fid_leakage_A"]
    run = dev["run"]
    if lit and run and run.get("chrom"):
        ch = run["chrom"]
        signal += _g8_interp(ch["time_s"], ch["fid_current_A"], run["elapsed_s"])
    return signal


def _g8_actuals(host, dev, process, state):
    """Measured values the touchscreen shows.  The EPC controllers hold their readings at the setpoints (sensor errors and stored zeros act
    on the gas really delivered, _g8_true_column / _g8_true_flows); faults the controllers cannot correct (inlet leak, split vent
    restriction) show in the readings.  The pressure sensor without carrier reads its own uncorrected offset."""
    s, m, e, z = dev["settings"], dev["method"], dev["errors"], dev["zeros"]
    T = process.zones["oven"].temperature_K
    elapsed = dev["run"]["elapsed_s"] if dev["run"] else None
    flowing = process.powered and dev["supply"]["carrier"] and "inlet" not in dev["shutdown"]
    col_flow, col_p = _g8_column_setpoint(dev, T, elapsed) if flowing else (0.0, 0.0)
    split = _g8_split_vent(dev, col_flow, elapsed) * (1.0 - e["split_vent_restriction"]) if flowing else 0.0
    septum = m["inlet"]["septum_purge_m3_s"] if flowing and m["inlet"]["septum_purge_on"] else 0.0
    leak = e["inlet_leak_m3_s"] if flowing else 0.0
    total = col_flow + split + septum + leak
    fid = process.powered and "detector" not in dev["shutdown"]
    gas = lambda on, supply, flow: flow if (fid and on and dev["supply"][supply]) else 0.0  # noqa: E731
    d = m["detector"]
    h2, air, mk = gas(d["h2_on"], "hydrogen", d["h2_flow_m3_s"]), gas(d["air_on"], "air", d["air_flow_m3_s"]), gas(d["makeup_on"], "makeup",
                                                                                                                      d["makeup_flow_m3_s"])
    signal = _g8_signal(dev, process)
    holdup = _g8_holdup(s, col_p, T) if col_flow > 0 else None
    return dict(oven_K=T, inlet_K=process.zones["inlet"].temperature_K, detector_K=process.zones["fid"].temperature_K,
                inlet_pressure_Pa=col_p if flowing else max(0.0, e["inlet_pressure_offset_Pa"] - z["inlet_pressure"]),
                total_flow_m3_s=total, septum_purge_m3_s=septum, split_vent_m3_s=split, column_flow_m3_s=col_flow, column_pressure_Pa=col_p,
                column_velocity_m_s=None if holdup is None else s["column.length_m"] / holdup, holdup_s=holdup,
                h2_m3_s=h2, air_m3_s=air, makeup_m3_s=mk, flame_lit=process.flame_lit, signal_A=signal,
                analog_out=_g8_analog(dev, signal, T))


_G8_ANALOG_BW_HZ = (1.6, 8.0)                            # analog output bandwidth: standard / Fast Peaks (p.186: 1.6 Hz, 8 Hz)


def _g8_analog_raw(dev, signal_A, T):
    a = dev["method"]["analog_out"]
    return {"Front Signal": signal_A * 1e12, "Oven Temperature": T - 273.15, "Test Plot": 0.0}[a["signal"]]


def _g8_analog_filter(dev, process, t0, t1):
    """Analog output electronics: a first-order low-pass of the signal with the bandwidth of the speed setting (Fast Peaks, p.186), exact for
    a signal linear over the sub-step.  The filter restarts when the signal type changes."""
    a = dev["method"]["analog_out"]
    x1 = _g8_analog_raw(dev, _g8_signal(dev, process), process.zones["oven"].temperature_K)
    f = dev.get("analog_filter")
    dt = t1 - t0
    if f is None or f["signal"] != a["signal"] or dt <= 0:
        dev["analog_filter"] = dict(signal=a["signal"], x=x1, y=x1, t=t1)
        return
    tau = 1.0 / (2 * _at_math.pi * _G8_ANALOG_BW_HZ[bool(a["fast_peaks"])])
    slope = (x1 - f["x"]) / dt
    decay = _at_math.exp(-dt / tau)
    f.update(x=x1, y=x1 - slope * tau + (f["y"] - f["x"] + slope * tau) * decay, t=t1)


def _g8_analog(dev, signal_A, T):
    """Analog output 1 in display units: the signal (pA) or oven temperature (C) after the output filter (Fast Peaks), minus the zero,
    divided by 2^range (p.186; the conversion to mV is not given, so the value stays in display units)."""
    a = dev["method"]["analog_out"]
    f = dev.get("analog_filter")
    v = f["y"] if f is not None and f["signal"] == a["signal"] else _g8_analog_raw(dev, signal_A, T)
    if a["zero_on"]:
        v -= a["zero_value"]
    return v / 2 ** a["range"]


def _g8_interp(xs, ys, x):
    if not xs or x < xs[0] or x > xs[-1]:
        return 0.0
    i = max(1, min(len(xs) - 1, _g8_bisect.bisect_left(xs, x)))
    x0, x1 = xs[i - 1], xs[i]
    return ys[i - 1] if x1 == x0 else ys[i - 1] + (ys[i] - ys[i - 1]) * (x - x0) / (x1 - x0)


def _g8_prep_needed(dev):
    i = dev["method"]["inlet"]
    return i["mode"] == "Splitless" or i["gas_saver"]


def _g8_not_ready(dev, process):
    """Conditions that keep the GC from READY (p.33-35, p.82, p.195): (zone, text)."""
    s, m = dev["settings"], dev["method"]
    out = []
    if dev["booting_until"] is not None:
        out.append(("system", "Starting up"))
    if process.door_open:
        out.append(("oven", "Oven door open"))
    zones = (("oven", "Oven temperature", s["oven.readiness"], m["oven"]["heater_on"]),
             ("inlet", "Front Inlet temperature", s["inlet.readiness"], m["inlet"]["heater_on"]),
             ("fid", "Front Detector temperature", s["detector.readiness"], m["detector"]["heater_on"]))
    for zone, text, ready_on, heater in zones:
        if ready_on and heater:
            dwell = m["oven"]["equilibration_time_s"] if zone == "oven" else _G8_ZONE_DWELL_S
            z = process.zones[zone]
            if dev["stable"][zone] < dwell - 1e-9 or abs(z.temperature_K - z.setpoint_K) > _G8_TOL_K:
                out.append((zone, text + " not ready"))
    if s["inlet.readiness"] and m["column"]["on"] and (not dev["supply"]["carrier"] or "inlet" in dev["shutdown"]):
        out.append(("inlet", "Front Inlet pressure not ready"))
    d = m["detector"]
    if s["detector.readiness"] and "detector" not in dev["shutdown"]:
        for on, gas, text in ((d["h2_on"], "hydrogen", "H2"), (d["air_on"], "air", "Air"), (d["makeup_on"], "makeup", "Makeup")):
            if on and not dev["supply"][gas]:
                out.append(("fid", f"Front Detector {text} flow not ready"))
        if d["flame_on"] and not process.flame_lit:
            out.append(("fid", "Front Detector flame not lit"))
    for zone in dev["shutdown"]:
        out.append((zone, {"inlet": "Front Inlet", "detector": "Front Detector"}.get(zone, zone) + " shut down"))
    if dev["procedure"] is not None:
        out.append(("system", "Maintenance mode"))
    if dev["diag"] is not None:
        out.append(("system", "Diagnostic test running"))
    if dev["sleep"] is not None:
        out.append(("system", "Sleep"))
    return out


def _g8_status(dev, process, now_s):
    """Status tray text and colour, status indicator colour, readiness and run time (p.31, p.33-34, p.60)."""
    if not process.powered:
        return dict(text="", color="off", led="off", ready=False, state="off", reasons=[], est_remaining_s=None, run_time_s=None,
                    prep=False, message=None)
    reasons = _g8_not_ready(dev, process)
    errors = [a for a in dev["alerts"] if a["level"] == "error"]
    run = dev["run"]
    prep_wait = _g8_prep_needed(dev) and not dev["prep"]
    ready = not reasons and not prep_wait
    if errors and not dev["shutdown"]:
        state, color = "ERROR", "error"
    elif dev["shutdown"]:
        state, color = "SHUTDOWN", "warn"
    elif dev["procedure"] is not None:
        state, color = "MAINTENANCE MODE", "maintenance"
    elif dev["diag"] is not None:
        state, color = "DIAGNOSTIC MODE", "warn"
    elif dev["sleep"] is not None:
        state, color = ("CONDITIONING RUN" if (run and run["kind"] == "condition") else "SLEEP DEFERRED" if dev["sleep"].get("deferred")
                        else "SLEEP"), "sleep"
    elif run is not None:
        state, color = "RUN", "run"
    elif dev["prep"]:
        state, color = ("READY FOR INJECTION", "ready") if not reasons else ("PREP RUN", "warn")
    elif dev["booting_until"] is not None:
        state, color = "NOT READY", "warn"
    elif ready:
        state, color = "READY", "ready"
    elif prep_wait and not reasons:
        state, color = "WAITING FOR PREP RUN", "ready"
    elif dev["last_run"] is not None and dev["last_run"].get("cooldown") and any(z == "oven" for z, _ in reasons):
        state, color = "COOLDOWN", "warn"
    else:
        state, color = "NOT READY", "warn"
    led = "red" if errors and not dev["shutdown"] else "green" if (ready or run is not None) and not dev["shutdown"] else "yellow"
    if dev["sleep"] is not None or dev["procedure"] is not None or dev["diag"] is not None:
        led = "yellow"
    remaining = None
    if run is not None:
        remaining = max(0.0, run["length_s"] - run["elapsed_s"])
    elif dev["diag"] is not None:
        remaining = max(0.0, dev["diag"]["until_s"] - now_s)
    msg = dev["message"] if dev["message"] and dev["message"]["until_s"] > now_s else None
    return dict(text="STATUS: " + state, color=color, led=led, ready=ready, state=state, reasons=[r for _, r in reasons],
                est_remaining_s=remaining, run_time_s=run["elapsed_s"] if run else None, prep=dev["prep"],
                message=msg["text"] if msg else None)


def _g8_message(dev, t, text, seconds=5.0):
    dev["message"] = dict(text=text, until_s=t + seconds)


def _g8_alert(dev, t, level, zone, text, causes=""):
    for a in dev["alerts"]:
        if a["zone"] == zone and a["text"] == text:
            return a
    dev["alert_seq"] += 1
    a = dict(id=dev["alert_seq"], level=level, zone=zone, text=text, causes=_g8_textwrap.fill(causes, 50), time=_at_now(dev, t))
    dev["alerts"].append(a)
    _g8_sys_log(dev, t, f"{level.capitalize()}: {text}")
    return a


def _g8_sync_process(dev, process):
    """The process follows the Active Method: zone heaters, carrier / detector gases actually flowing (supply connected and switched on),
    inlet mode and flows, column flow and dimensions, oven power limit."""
    m, s = dev["method"], dev["settings"]
    process.zones["oven"].on = m["oven"]["heater_on"]
    process.zones["oven"].limited = s["oven.limit_ballistic"]
    process.zones["oven"].slow_cool = s["oven.slow_cooldown"]
    process.zones["inlet"].on = m["inlet"]["heater_on"]
    process.zones["fid"].on = m["detector"]["heater_on"]
    d = m["detector"]
    want = {"carrier": dev["supply"]["carrier"] and m["column"]["on"] and "inlet" not in dev["shutdown"],
            "hydrogen": dev["supply"]["hydrogen"] and d["h2_on"] and "detector" not in dev["shutdown"],
            "air": dev["supply"]["air"] and d["air_on"] and "detector" not in dev["shutdown"],
            "makeup": dev["supply"]["makeup"] and d["makeup_on"] and "detector" not in dev["shutdown"]}
    for gas, on in want.items():
        if process.gases[gas] != on:
            process.perform("connect_gas", gas=gas, connected=on)
    i = m["inlet"]
    T = process.zones["oven"].temperature_K
    flow = _g8_true_column(dev, T)[0]                                    # the gas really delivered (the EPC readings sit at the setpoints)
    true = _g8_true_flows(dev, _g8_column_setpoint(dev, T)[0], None)
    split_ratio = i["split_ratio"]
    if i["mode"] == "Split" and flow > 0 and (any(_g8_errs(dev).values()) or dev["errors"]["split_vent_restriction"]):
        split_ratio = true["split_vent"] / flow
    changes = dict(mode="split" if i["mode"] == "Split" else "splitless", split_ratio=split_ratio, purge_time_s=i["purge_time_s"],
                   purge_flow_m3_s=true["purge"], septum_purge_flow_m3_s=true["septum_purge"],
                   hydrogen_flow_m3_s=true["h2"], air_flow_m3_s=true["air"], makeup_flow_m3_s=true["makeup"])
    if flow > 0:
        changes["column_flow_m3_s"] = flow
    if _g8_column_defined(s) and s["column.film_m"] < s["column.diameter_m"] / 2:
        changes.update(column_length_m=s["column.length_m"], column_inner_diameter_m=s["column.diameter_m"], column_film_thickness_m=s["column.film_m"])
    if any(getattr(process.method, k) != v for k, v in changes.items()):
        process.method = _g8_dataclasses.replace(process.method, **changes)
    if not d["flame_on"] and process.flame_lit:
        process.perform("extinguish")


def _g8_set_zone(process, zone, T):
    if process.zones[zone].setpoint_K != T:
        process.perform("set_temperature", zone=zone, temperature_K=T)


def _g8_apply_temperatures(dev, process):
    m = dev["method"]
    oven = m["oven"]["initial_K"]
    if dev["standby"]:
        oven = dev["settings"]["oven.standby_temp_K"]
    _g8_set_zone(process, "oven", oven)
    _g8_set_zone(process, "inlet", m["inlet"]["temperature_K"])
    _g8_set_zone(process, "fid", m["detector"]["temperature_K"])


def _g8_load_method(dev, process, method, t, why):
    """Replace the Active Method (scheduler, maintenance restore): setpoints apply at once (p.78)."""
    dev["method"] = _at_copy.deepcopy(method)
    dev["standby"] = False
    _g8_sync_process(dev, process)
    _g8_apply_temperatures(dev, process)
    _g8_sys_log(dev, t, why)


def _g8_wake(dev, process, t, why):
    if dev["sleep"] is None:
        return
    sl, dev["sleep"] = dev["sleep"], None
    if sl.get("restore") is not None:
        _g8_load_method(dev, process, sl["restore"], t, why)
    else:
        _g8_sys_log(dev, t, why)


# ---------------------------------------------------------------- runs (p.82-85)
def _g8_begin_run(host, dev, process, state, t, kind="run", slot=None):
    m = dev["method"]
    length = _g8_run_length(m)
    chrom = None
    if kind == "run" and state["sample.loaded"]:
        chrom = host._chromatogram(state)
    dev["run"] = dict(kind=kind, start=_at_now(dev, t), start_s=t, elapsed_s=0.0, length_s=length, chrom=chrom, sample_id=state["sample.id"]
                      if kind == "run" else None, slot=slot, trace=[], applied=[])
    dev["events_restore"] = {}
    dev["prep"] = False
    dev["standby"] = False
    dev["logs"]["run"] = []
    _at_log(dev["logs"]["run"], dict(time="0.00", text={"run": "Run started", "blank": "Blank run started", "column_comp":
                                                         f"Column compensation run {slot} started", "condition": "Conditioning run started"}[kind]))
    process.running, process.run_time_s, process.fid_integral_C = True, 0.0, 0.0
    process.injection_time_s = process.time_s if kind == "run" else None
    _g8_apply_temperatures(dev, process)


def _g8_end_run(host, dev, process, t, aborted=False):
    run, dev["run"] = dev["run"], None
    m = dev["method"]
    for (comp, key), value in dev["events_restore"].items():
        m[comp][key] = value
    dev["events_restore"] = {}
    process.running = False
    process.injection_time_s = None
    elapsed = run["elapsed_s"]
    _at_log(dev["logs"]["run"], dict(time=f"{elapsed / 60:.2f}", text="Run stopped by user" if aborted else "Run completed"))
    _g8_sys_log(dev, t, ("Run aborted" if aborted else "Run completed") + f" ({run['kind']}, {elapsed / 60:.2f} min)")
    if not aborted or run["kind"] == "run":
        for k in ("instrument.runs", "column.runs", "column.oven_cycles"):
            dev["emf"][k]["value"] += 1
    if run["kind"] == "column_comp" and not aborted:
        dev["column_comp"][str(run["slot"])] = dict(time=_at_now(dev, t), points=len(run["trace"]), trace=run["trace"][-600:])
    dev["last_run"] = dict(kind=run["kind"], start=run["start"], length_s=run["length_s"], elapsed_s=elapsed, aborted=aborted,
                           sample_id=run["sample_id"], peaks=None if not run.get("chrom") else
                           [dict(retention_time_s=rt, area_C=a) for rt, a in zip(run["chrom"]["retention_time_s"], run["chrom"]["peak_area_C"])
                            if rt <= elapsed], cooldown=True)
    _g8_sync_process(dev, process)
    _g8_apply_temperatures(dev, process)
    dev["idle_since"] = t
    if dev["settings"]["cal.auto_zero_flow"] and dev["supply"]["carrier"] and m["column"]["on"]:
        dev["zeros"]["inlet_flow"] = dev["errors"]["inlet_flow_offset_m3_s"]
        dev["zero_times"]["inlet_flow"] = _at_now(dev, t)
    if dev["settings"]["cal.auto_zero_septum"] and dev["supply"]["carrier"]:
        dev["zeros"]["septum_purge"] = dev["errors"]["septum_purge_offset_m3_s"]
        dev["zero_times"]["septum_purge"] = _at_now(dev, t)
    if run["kind"] == "condition" and dev["sleep"] is not None:
        _g8_wake(dev, process, t, "Conditioning run finished: waking")


def _g8_run_tick(host, dev, process, t):
    """Programs of the run at its elapsed time: oven setpoint, run-table events (p.84), the end of the run."""
    run = dev["run"]
    m = dev["method"]
    run["elapsed_s"] = t - run["start_s"]
    process.run_time_s = run["elapsed_s"]
    T, end = _g8_program(m["oven"]["initial_K"], m["oven"]["initial_time_s"], _g8_oven_ramps(m), run["elapsed_s"])
    if run["elapsed_s"] >= run["length_s"] - 1e-9:
        _g8_end_run(host, dev, process, t)
        return
    _g8_set_zone(process, "oven", min(T, dev["settings"]["oven.max_temp_K"]))
    for i, ev in enumerate(sorted(m["events"], key=lambda e: e["time_s"])):
        key = (ev["type"], ev["time_s"], i)
        if ev["time_s"] <= run["elapsed_s"] and key not in run["applied"]:
            comp, field = G8_EVENT_TYPES[ev["type"]]
            dev["events_restore"].setdefault((comp, field), m[comp][field])
            m[comp][field] = (ev["position"] == "On") if field.endswith("_on") else ev["setpoint"]
            run["applied"].append(key)
            _at_log(dev["logs"]["run"], dict(time=f"{run['elapsed_s'] / 60:.2f}", text=f"Event: {ev['type']} " +
                                             (ev["position"] if field.endswith("_on") else f"{ev['setpoint']:g}")))
            _g8_sync_process(dev, process)


# ---------------------------------------------------------------- clock-driven behaviour
def _g8_tick(host, dev, process, state, t0, t1):
    """After the process advanced to t1: readiness dwell, flame ignition and shutdowns, runs, diagnostics, maintenance, scheduler, EMF, plot."""
    dt = t1 - t0
    if not process.powered:
        return
    dev["on_time_s"] += dt
    if dev["booting_until"] is not None and t1 >= dev["booting_until"] - 1e-9:
        dev["booting_until"] = None
        _g8_message(dev, t1, "Power on successful")
        _g8_sys_log(dev, t1, "Power on successful")
    for zone, z in process.zones.items():
        ok = abs(z.temperature_K - z.setpoint_K) <= _G8_TOL_K and not (zone == "oven" and process.door_open)
        dev["stable"][zone] = dev["stable"][zone] + dt if ok else 0.0
    if dev["last_run"] is not None and dev["last_run"].get("cooldown") and dev["stable"]["oven"] > 0:
        dev["last_run"]["cooldown"] = False
    _g8_flame(dev, process, t1)
    m = dev["method"]
    if m["column"]["on"] and not dev["supply"]["carrier"] and "inlet" not in dev["shutdown"]:
        before = dev["flow_bad_s"]
        dev["flow_bad_s"] += dt
        if int(dev["flow_bad_s"] // 30) > int(before // 30):
            dev["beeps"] += 1 + int(dev["flow_bad_s"] // 30)                                   # more beeps the longer it persists (p.34)
        if dev["flow_bad_s"] >= _G8_FLOW_SHUTDOWN_S:
            dev["shutdown"]["inlet"] = "flow"
            dev["beeps"] += 1
            _g8_alert(dev, t1, "warning", "inlet", "Front Inlet: Inlet flow shutdown",
                      "The inlet flow could not reach its setpoint (gas supply off or empty, leak). Restore the supply, then choose a setpoint action.")
            _g8_message(dev, t1, "Inlet flow shutdown")
            _g8_sync_process(dev, process)
    else:
        dev["flow_bad_s"] = 0.0
    if dev["run"] is not None:
        _g8_run_tick(host, dev, process, t1)
        if dev["run"] is not None:
            a = _g8_actuals(host, dev, process, state)
            dev["run"]["trace"].append([round(dev["run"]["elapsed_s"], 3), a["signal_A"]])
            del dev["run"]["trace"][:-7200]
    if dev["diag"] is not None and t1 >= dev["diag"]["until_s"] - 1e-9:
        _g8_finish_test(dev, process, t1)
    _g8_procedure_tick(dev, process, t1)
    _g8_emf_tick(dev, t1)
    _g8_schedule_tick(host, dev, process, state, t0, t1)
    s = dev["settings"]
    idle = dev["run"] is None and not dev["prep"] and dev["procedure"] is None and dev["diag"] is None and dev["sleep"] is None
    if not idle:
        dev["idle_since"] = t1
    if s["oven.standby_on"] and idle and not dev["standby"] and t1 - dev["idle_since"] >= s["oven.standby_time_s"] - 1e-9:
        dev["standby"] = True
        _g8_apply_temperatures(dev, process)
        _g8_sys_log(dev, t1, "Oven standby")
    _g8_analog_filter(dev, process, t0, t1)
    p = dev["plot"]
    if p["running"]:
        a = _g8_actuals(host, dev, process, state)
        v = {"signal": a["signal_A"] * 1e12, "oven_temp": a["oven_K"] - 273.15, "column_flow": a["column_flow_m3_s"] / _G8_ML,
             "inlet_pressure": a["inlet_pressure_Pa"] / _G8_PSI}[p["signal"]]
        p["data"].append([round(t1, 3), round(v, 6)])
        while p["data"] and p["data"][0][0] < t1 - _G8_PLOT_KEEP_S:
            p["data"].pop(0)


def _g8_flame(dev, process, t):
    """Flame On: ignition once the detector is hot enough and H2 / air flow; the Lit offset check; reignition (three tries) and the detector
    shutdown on a flame-out (p.95)."""
    d, ig = dev["method"]["detector"], dev["ignition"]
    if not d["flame_on"] or "detector" in dev["shutdown"]:
        ig.update(tries=0, failed=False)
        return
    if process.flame_lit:
        ig["lit_seen"], ig["tries"] = True, 0
        return
    flows = process.flows()
    if process.zones["fid"].temperature_K < _G8_IGNITE_K or flows["hydrogen"] <= 0 or flows["air"] <= 0:
        if ig["lit_seen"] and (flows["hydrogen"] <= 0 or flows["air"] <= 0) and (d["h2_on"] and d["air_on"]):
            ig["tries"] += 1                                                    # a lit flame lost its gas: count as failed reignitions
            dev["emf"]["detector.ignitor"]["value"] += 1
            if ig["tries"] >= _G8_TRIES:
                _g8_detector_shutdown(dev, process, t, "Front Detector: Flame out")
        return
    ig["tries"] += 1
    dev["emf"]["detector.ignitor"]["value"] += 1
    if _G8_FID_BASELINE_A + dev["errors"]["fid_leakage_A"] >= dev["settings"]["detector.lit_offset_A"]:
        try:
            process.perform("ignite")
            ig["tries"], ig["lit_seen"] = 0, True
            return
        except Exception:
            pass
    if ig["tries"] >= _G8_TRIES:
        _g8_detector_shutdown(dev, process, t, "Front Detector: Flame failed to ignite")


def _g8_detector_shutdown(dev, process, t, text):
    """All detector functions except temperature and makeup flow shut down (p.95)."""
    dev["shutdown"]["detector"] = "flame"
    dev["ignition"].update(tries=0, lit_seen=False)
    dev["beeps"] += 1
    _g8_alert(dev, t, "warning", "detector", text, "Check the H2 and air supplies, the jet and the Lit offset; then choose a setpoint action.")
    _at_log(dev["logs"]["maintenance"], dict(time=_at_now(dev, t), text=text))
    process.flame_lit = False
    _g8_sync_process(dev, process)


def _g8_emf_tick(dev, t):
    for k, (comp, part, typ, _) in G8_EMF.items():
        c = dev["emf"][k]
        if typ == "days":
            c["value"] = max(0.0, (t + dev["clock_offset_s"] - c["since_s"]) / 86400.0) if k != "instrument.on_time" else dev["on_time_s"] / 86400.0
        level = "due" if c["due_on"] and c["value"] >= c["due"] else "warning" if c["warn_on"] and c["value"] >= c["warn"] else "ok"
        if level != c.get("level", "ok") and level != "ok":
            _at_log(dev["logs"]["maintenance"], dict(time=_at_now(dev, t), text=f"{comp}: {part} " +
                                                     ("service due" if level == "due" else "service warning")))
        c["level"] = level


def _g8_schedule_tick(host, dev, process, state, t0, t1):
    """Instrument schedule (sleep / wake per day, p.205-206) and clock table (p.208) at the device clock's minute boundaries."""
    a, b = _g8_time(dev, t0), _g8_time(dev, t1)
    if b <= a:
        return
    minute = a.replace(second=0, microsecond=0) + _at_dt.timedelta(minutes=1)
    s = dev["settings"]
    while minute <= b:
        hhmm = minute.strftime("%H:%M")
        day = _AT_WEEKDAYS[minute.weekday()].lower()
        if s[f"sched.{day}.sleep_on"] and s[f"sched.{day}.sleep_time"] == hhmm and dev["sleep"] is None:
            _g8_sleep(dev, process, t1)
        if s[f"sched.{day}.wake_on"] and s[f"sched.{day}.wake_time"] == hhmm and dev["sleep"] is not None:
            if s["sched.condition_run"] and dev["stored"]["CONDITION"] is not None and dev["run"] is None:
                _g8_load_method(dev, process, dev["stored"]["CONDITION"], t1, "Conditioning run before waking")
                _g8_begin_run(host, dev, process, state, t1, kind="condition")
            else:
                _g8_wake(dev, process, t1, "Scheduled wake")
        for e in dev["clock_table"]:
            wk = minute.weekday()
            due = e["time"] == hhmm and (e["frequency"] == "Daily" or (e["frequency"] == "Weekdays" and wk < 5) or e["frequency"] == _AT_WEEKDAYS[wk])
            if due and dev["run"] is None:                                       # events that would occur during a run are ignored (p.208)
                if e["type"] == "Blank Run" and not _g8_not_ready(dev, process):
                    _g8_begin_run(host, dev, process, state, t1, kind="blank")
                elif e["type"] == "Prep Run":
                    dev["prep"] = True
                _g8_sys_log(dev, t1, f"Clock table: {e['type']}")
        minute += _at_dt.timedelta(minutes=1)
    if dev["sleep"] is not None and dev["sleep"].get("deferred") and dev["run"] is None and t1 - dev["idle_since"] >= _G8_SLEEP_DEFER_S:
        dev["sleep"]["deferred"] = False
        _g8_enter_sleep(dev, process, t1)


def _g8_sleep(dev, process, t):
    dev["sleep"] = dict(since=_at_now(dev, t), restore=None, deferred=dev["run"] is not None)
    if dev["run"] is not None:
        _g8_sys_log(dev, t, "Sleep deferred (run in progress)")
        return
    _g8_enter_sleep(dev, process, t)


def _g8_enter_sleep(dev, process, t):
    s = dev["settings"]
    day = _AT_WEEKDAYS[_g8_time(dev, t).weekday()].lower()
    dev["sleep"]["restore"] = _at_copy.deepcopy(dev["method"]) if s["sched.wake_to_last"] else (dev["stored"]["WAKE"] if s[f"sched.{day}.wake_on"]
                                                                                              else None)
    dev["prep"] = False
    if dev["stored"]["SLEEP"] is not None:
        _g8_load_method(dev, process, dev["stored"]["SLEEP"], t, "Sleep: SLEEP method loaded")
    else:
        _g8_sys_log(dev, t, "Sleep (no SLEEP method stored: setpoints unchanged)")


# ---------------------------------------------------------------- diagnostics and maintenance procedures
def _g8_test_result(dev, process, test):
    """Pass / fail and the shown values of a diagnostic test from the inlet / detector state and the sensor error parameters."""
    e, m = dev["errors"], dev["method"]
    sup = dev["supply"]["carrier"]
    if test == "gas_supply":
        return sup, [("Supply pressure", "OK" if sup else "Low")]
    if test == "leak_restriction":
        leak = e["inlet_leak_m3_s"] / _G8_ML
        target = _g8_column_setpoint(dev, process.zones["oven"].temperature_K)[1] / _G8_PSI
        actual = target if sup else 0.0
        return sup and leak < 0.5, [("Inlet Pressure Target", f"{target:.3f} psi"), ("Inlet Pressure Actual", f"{actual:.3f} psi"),
                                    ("Flow error", f"{leak:.2f} mL/min")]
    if test == "pressure_decay":
        decay = e["inlet_leak_m3_s"] / _G8_ML * 2.0                                   # psi/min per mL/min leak (assumption of the test volume)
        return sup and decay < 0.5, [("Pressure decay", f"{decay:.2f} psi/min" if sup else "--")]
    if test == "septum_purge":
        target = m["inlet"]["septum_purge_m3_s"] / _G8_ML
        actual = (m["inlet"]["septum_purge_m3_s"] + e["septum_purge_offset_m3_s"] - dev["zeros"]["septum_purge"]) / _G8_ML if sup else 0.0
        return sup and abs(actual - target) <= max(0.3, 0.1 * target), [("Target", f"{target:.2f} mL/min"), ("Actual", f"{actual:.2f} mL/min")]
    if test == "split_vent":
        r = e["split_vent_restriction"]
        return sup and r < 0.2, [("Restriction", f"{100 * r:.0f} %")]
    if test == "fid_jet":
        r = e["fid_jet_restriction"]
        return dev["supply"]["hydrogen"] and r < 0.2, [("Jet restriction", f"{100 * r:.0f} %")]
    leak = e["fid_leakage_A"] * 1e12
    return leak < 2.0, [("Leakage current", f"{leak:.2f} pA")]


def _g8_finish_test(dev, process, t):
    d, dev["diag"] = dev["diag"], None
    ok, values = _g8_test_result(dev, process, d["test"])
    dev["diag_results"][d["test"]] = dict(result="Passed" if ok else "Failed", values=[list(v) for v in values], time=_at_now(dev, t))
    _g8_sys_log(dev, t, f"{G8_TESTS[d['test']][1]}: {'Passed' if ok else 'Failed'}")


def _g8_procedure_tick(dev, process, t):
    p = dev["procedure"]
    if p is None or p["step"] != "cooling":
        return
    zones = G8_PROCEDURES[p["id"]][2]
    if all(process.zones[z].temperature_K <= _G8_SAFE_K + 1e-6 for z in zones):
        p["step"] = "service"


def _g8_start_procedure(dev, process, pid, t):
    comp, title, zones, _ = G8_PROCEDURES[pid]
    dev["procedure"] = dict(id=pid, title=title, component=comp, step="cooling", started=_at_now(dev, t), restore=_at_copy.deepcopy(dev["method"]))
    m = dev["method"]
    dev["prep"] = False
    for z in zones:                                                                   # low temperatures (p.132); flows off at the serviced part
        key = {"oven": ("oven", "initial_K"), "inlet": ("inlet", "temperature_K"), "fid": ("detector", "temperature_K")}[z]
        m[key[0]][key[1]] = min(m[key[0]][key[1]], 308.15 if z != "oven" else 308.15)
    if "inlet" in zones:
        m["column"]["on"] = pid in ("maintenance_mode", "gas_clean_filter") and m["column"]["on"]
    if "fid" in zones:
        m["detector"].update(flame_on=False, h2_on=False, air_on=False)
    _g8_sync_process(dev, process)
    _g8_apply_temperatures(dev, process)
    _g8_sys_log(dev, t, f"Maintenance started: {title}")
    _at_log(dev["logs"]["maintenance"], dict(time=_at_now(dev, t), text=f"{comp}: {title} started"))


def _g8_end_procedure(dev, process, t, completed):
    p, dev["procedure"] = dev["procedure"], None
    comp, title, _, counters = G8_PROCEDURES[p["id"]]
    if completed:
        for k in counters:
            _g8_reset_counter(dev, k, t, "Maintenance Reset")
    _g8_load_method(dev, process, p["restore"], t, f"Maintenance {'completed' if completed else 'aborted'}: {title}")
    _at_log(dev["logs"]["maintenance"], dict(time=_at_now(dev, t), text=f"{comp}: {title} {'completed' if completed else 'aborted'}"))


def _g8_reset_counter(dev, k, t, how):
    c = dev["emf"][k]
    c["resets"] = (c["resets"] + [dict(time=_at_now(dev, t), value=c["value"], how=how)])[-20:]
    c["value"], c["since_s"], c["level"] = 0.0, t + dev["clock_offset_s"], "ok"
    comp, part, typ, _ = G8_EMF[k]
    _at_log(dev["logs"]["maintenance"], dict(time=_at_now(dev, t), text=f"{comp}: {part} ({typ}) counter reset ({how})"))


# ---------------------------------------------------------------- terminal commands: rt (runtime), dev, process, state, args, t
def _g8_gate(dev, process, running_ok=True):
    _at_need(process.powered, "PRECONDITION_FAILED")
    _at_need(dev["booting_until"] is None, "BUSY")
    if not running_ok:
        _at_need(dev["run"] is None and dev["diag"] is None and dev["procedure"] is None, "BUSY")


def _g8_c_prep(rt, dev, process, state, a, t):
    _g8_gate(dev, process, running_ok=False)
    _g8_wake(dev, process, t, "Wake: Prep Run")
    dev["prep"] = True
    if dev["standby"]:
        dev["standby"] = False
        _g8_apply_temperatures(dev, process)
    return {"prep": True}


def _g8_c_start(rt, dev, process, state, a, t):
    _g8_gate(dev, process, running_ok=False)
    _at_need(dev["sleep"] is None, "NOT_READY")
    st = _g8_status(dev, process, t)
    _at_need(st["ready"] or (dev["prep"] and not st["reasons"]), "NOT_READY")
    _g8_begin_run(rt, dev, process, state, t)
    return {"running": True, "run_length_s": dev["run"]["length_s"]}


def _g8_c_stop(rt, dev, process, state, a, t):
    _at_need(process.powered, "PRECONDITION_FAILED")
    if dev["run"] is not None:
        _g8_end_run(rt, dev, process, t, aborted=True)
        return {"stopped": "run"}
    if dev["prep"]:
        dev["prep"] = False
        return {"stopped": "prep"}
    return {"stopped": None}


def _g8_c_method(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    comp = a["component"]
    table = [f for f in G8_METHOD[comp] if not f.get("write_via")]
    m = dev["method"]
    new = _at_apply(table, m[comp], a["settings"])
    if comp == "inlet":
        _at_need(new["gas_saver_flow_m3_s"] >= m["column"]["flow_m3_s"] + 15 * _G8_ML - 1e-12 or not new["gas_saver"], "INVALID_ARGUMENT")
    if comp == "oven":
        for k in range(1, 33):
            _at_need(new[f"ramp_{k}_final_K"] <= dev["settings"]["oven.max_temp_K"] + 1e-9 or new[f"ramp_{k}_rate_K_s"] <= 0, "INVALID_ARGUMENT")
    if comp == "column" and not _g8_column_defined(dev["settings"]):
        _at_need(new["mode"] in ("Constant Pressure", "Ramped Pressure") or new["mode"] == m["column"]["mode"] and new["flow_m3_s"] ==
                 m["column"]["flow_m3_s"], "INVALID_ARGUMENT")      # flow modes need a defined column (p.93)
    if dev["run"] is not None:
        _at_log(dev["logs"]["run"], dict(time=f"{dev['run']['elapsed_s'] / 60:.2f}", text=f"Setpoint changed at the touchscreen: {comp}"))
    m[comp] = new
    _g8_wake(dev, process, t, "Wake: setpoint change")
    _g8_sync_process(dev, process)
    return {"component": comp, "settings": {k: new[k] for k in a["settings"]}}


def _g8_c_event_save(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    ev = dict(time_s=float(a["time_s"]), type=a["type"], position=a.get("position"), setpoint=a.get("setpoint"))
    switch = G8_EVENT_TYPES[a["type"]][1].endswith("_on")
    _at_need((ev["position"] in ("On", "Off") and ev["setpoint"] is None) if switch else (ev["position"] is None and ev["setpoint"] is not None),
             "INVALID_ARGUMENT")
    events = dev["method"]["events"]
    i = a.get("index")
    if i is None:
        _at_need(len(events) < 50, "INVALID_ARGUMENT")
        events.append(ev)
    else:
        _at_need(0 <= i < len(events), "INVALID_ARGUMENT")
        events[i] = ev
    events.sort(key=lambda e: e["time_s"])
    return {"events": len(events)}


def _g8_c_event_delete(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    events = dev["method"]["events"]
    _at_need(0 <= a["index"] < len(events), "INVALID_ARGUMENT")
    del events[a["index"]]
    return {"events": len(events)}


def _g8_c_settings(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    changes = a["settings"]
    s = dev["settings"]
    if s["column.id_lock"]:
        _at_need(not any(k in changes for k in ("column.length_m", "column.diameter_m", "column.film_m")), "LOCKED")
    new = _at_apply(G8_SETTINGS, s, changes)
    _at_need(new["power.dim_to_pct"] < new["power.brightness_pct"], "INVALID_ARGUMENT")          # p.159 NOTE
    _at_need(new["column.film_m"] < new["column.diameter_m"] / 2 or not _g8_column_defined(new), "INVALID_ARGUMENT")
    if any(k.startswith("network.") and new[k] != s[k] for k in changes):
        dev["restart_pending"] = True
        _g8_sys_log(dev, t, "Network settings changed (restart required)")
    if any(k.startswith("column.") and k in ("column.length_m", "column.diameter_m", "column.film_m") and new[k] != s[k] for k in changes):
        _g8_sys_log(dev, t, "Column 1 configuration changed")
    if new["oven.max_temp_K"] < dev["method"]["oven"]["initial_K"]:
        raise _ATReject("INVALID_ARGUMENT")                                                    # the active setpoint exceeds the new maximum
    dev["settings"] = new
    if not _g8_column_defined(new) and dev["method"]["column"]["mode"].endswith("Flow"):
        dev["method"]["column"]["mode"] = "Constant Pressure"
    _g8_sync_process(dev, process)
    if not new["oven.standby_on"] and dev["standby"]:
        dev["standby"] = False
        _g8_apply_temperatures(dev, process)
    return {"settings": {k: new[k] for k in changes}, "restart_required": dev["restart_pending"]}


def _g8_c_clock(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    try:
        target = _at_dt.datetime.fromisoformat(a["date"] + "T" + a["time"])
    except ValueError:
        raise _ATReject("INVALID_ARGUMENT") from None
    _at_need(target.tzinfo is None and 2000 <= target.year <= 2099, "INVALID_ARGUMENT")
    dev["clock_offset_s"] = (target - _AT_EPOCH).total_seconds() - t
    _g8_sys_log(dev, t, "Date and time set")
    return {"datetime": _at_now(dev, t)}


def _g8_c_status_params(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    p, x = dev["status_params"], a["param"]
    if a["action"] == "add":
        _at_need(x not in p and len(p) < 12, "INVALID_ARGUMENT")
        p.append(x)
    else:
        _at_need(x in p, "NOT_FOUND")
        p.remove(x)
    return {"params": list(p)}


def _g8_c_plot(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    p = dev["plot"]
    if "signal" in a and a["signal"] != p["signal"]:
        p["data"] = []
    if a.get("y_min") is not None and a.get("y_max") is not None:
        _at_need(a["y_min"] < a["y_max"], "INVALID_ARGUMENT")
    p.update({k: v for k, v in a.items() if k in ("signal", "x_axis_s", "y_min", "y_max", "running")})
    if a.get("zoom_cycle"):
        p["zoom"] = {1: 2, 2: 4, 4: 1}[p["zoom"]]
    return {k: p[k] for k in ("signal", "x_axis_s", "y_min", "y_max", "running", "zoom")}


def _g8_c_resolve(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    alert = next((x for x in dev["alerts"] if x["id"] == a["alert_id"]), None)
    _at_need(alert is not None, "NOT_FOUND")
    zone = alert["zone"]
    dev["alerts"] = [x for x in dev["alerts"] if x["id"] != alert["id"]]
    if zone in dev["shutdown"]:
        del dev["shutdown"][zone]
        if a["action"] == "turn_off":                                                       # p.35: all zones on except the shutdown zone
            if zone == "inlet":
                dev["method"]["column"]["on"] = False
            elif zone == "detector":
                dev["method"]["detector"].update(flame_on=False, h2_on=False, air_on=False)
        dev["flow_bad_s"] = 0.0
        dev["ignition"].update(tries=0, failed=False, lit_seen=False)
    _g8_sys_log(dev, t, f"Setpoint action for '{alert['text']}': " + ("Keep Shutdown Zone On" if a["action"] == "keep_on" else
                                                                       "Turn Shutdown Zone Off"))
    _g8_sync_process(dev, process)
    return {"cleared": alert["id"]}


def _g8_c_test_start(rt, dev, process, state, a, t):
    _g8_gate(dev, process, running_ok=False)
    _at_need(dev["sleep"] is None, "PRECONDITION_FAILED")
    test = a["test"]
    if test == "fid_leakage":
        _at_need(not process.flame_lit, "PRECONDITION_FAILED")                              # leakage is measured with the flame off
    dev["diag"] = dict(test=test, started=_at_now(dev, t), until_s=t + G8_TESTS[test][2])
    dev["prep"] = False
    _g8_sys_log(dev, t, f"Diagnostic test started: {G8_TESTS[test][1]}")
    return {"test": test, "duration_s": G8_TESTS[test][2]}


def _g8_c_test_cancel(rt, dev, process, state, a, t):
    _at_need(dev["diag"] is not None, "PRECONDITION_FAILED")
    d, dev["diag"] = dev["diag"], None
    dev["diag_results"][d["test"]] = dict(result="Aborted", values=[], time=_at_now(dev, t))
    _g8_sys_log(dev, t, f"Diagnostic test aborted: {G8_TESTS[d['test']][1]}")
    return {"aborted": d["test"]}


def _g8_c_emf_configure(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    c = dev["emf"][a["counter"]]
    new = dict(c, **{k: a[k] for k in ("due_on", "due", "warn_on", "warn") if k in a})
    _at_need(new["due"] > new["warn"], "INVALID_ARGUMENT")                                  # Service Due must exceed Service Warning (p.131)
    comp, part, typ, _ = G8_EMF[a["counter"]]
    for k in ("due_on", "warn_on", "due", "warn"):
        if new[k] != c[k]:
            _at_log(dev["logs"]["maintenance"], dict(time=_at_now(dev, t), text=f"{comp}: {part} ({typ}) {k.replace('_on', '')} " +
                                                     (("enabled" if new[k] else "disabled") if k.endswith("_on") else f"limit {new[k]:g}")))
    dev["emf"][a["counter"]] = new
    _g8_emf_tick(dev, t)
    return {"counter": a["counter"]}


def _g8_c_emf_reset(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    _g8_reset_counter(dev, a["counter"], t, "User Reset")
    return {"counter": a["counter"], "value": 0.0}


def _g8_c_maint_start(rt, dev, process, state, a, t):
    _g8_gate(dev, process, running_ok=False)
    _g8_wake(dev, process, t, "Wake: maintenance")
    _g8_start_procedure(dev, process, a["procedure"], t)
    return {"procedure": a["procedure"], "step": "cooling"}


def _g8_c_maint_continue(rt, dev, process, state, a, t):
    p = dev["procedure"]
    _at_need(p is not None and p["step"] == "service", "PRECONDITION_FAILED")
    _g8_end_procedure(dev, process, t, completed=True)
    return {"completed": p["id"]}


def _g8_c_maint_abort(rt, dev, process, state, a, t):
    _at_need(dev["procedure"] is not None, "PRECONDITION_FAILED")
    pid = dev["procedure"]["id"]
    _g8_end_procedure(dev, process, t, completed=False)
    return {"aborted": pid}


def _g8_c_clock_add(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    _at_need(len(dev["clock_table"]) < 30, "INVALID_ARGUMENT")
    dev["clock_table"].append(dict(type=a["type"], frequency=a["frequency"], time=a["time"]))
    dev["clock_table"].sort(key=lambda e: e["time"])
    return {"entries": len(dev["clock_table"])}


def _g8_c_clock_delete(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    _at_need(0 <= a["index"] < len(dev["clock_table"]), "INVALID_ARGUMENT")
    del dev["clock_table"][a["index"]]
    return {"entries": len(dev["clock_table"])}


def _g8_c_zero(rt, dev, process, state, a, t):
    """Zero a flow sensor (gas connected and flowing) or the pressure sensor (supply line disconnected), p.153-154."""
    _g8_gate(dev, process, running_ok=False)
    sensor = a["sensor"]
    kind = G8_SENSORS[sensor][1]
    m, sup = dev["method"], dev["supply"]
    flowing = {"inlet_flow": sup["carrier"] and m["column"]["on"], "septum_purge": sup["carrier"] and m["inlet"]["septum_purge_on"],
               "h2": sup["hydrogen"] and m["detector"]["h2_on"], "air": sup["air"] and m["detector"]["air_on"],
               "makeup": sup["makeup"] and m["detector"]["makeup_on"]}
    if kind == "flow":
        _at_need(flowing[sensor], "GAS_NOT_FLOWING")
    else:
        _at_need(not sup["carrier"], "SUPPLY_CONNECTED")
    key = {"inlet_flow": "inlet_flow_offset_m3_s", "septum_purge": "septum_purge_offset_m3_s", "inlet_pressure": "inlet_pressure_offset_Pa",
           "h2": "h2_offset_m3_s", "air": "air_offset_m3_s", "makeup": "makeup_offset_m3_s"}[sensor]
    dev["zeros"][sensor] = dev["errors"][key]
    dev["zero_times"][sensor] = _at_now(dev, t)
    _g8_sys_log(dev, t, f"Sensor zeroed: {G8_SENSORS[sensor][0]}")
    _g8_sync_process(dev, process)                                                  # the controllers now deliver the true setpoint
    return {"sensor": sensor}


def _g8_c_preview(rt, dev, process, state, a, t):
    _g8_gate(dev, process)
    dev["preview"] = dict(brightness_pct=int(a["brightness_pct"]), until_s=t + 5.0)
    return {"until_s": t + 5.0}


def _g8_c_column_comp(rt, dev, process, state, a, t):
    _g8_gate(dev, process, running_ok=False)
    _at_need(dev["sleep"] is None and not [r for r in _g8_not_ready(dev, process)], "NOT_READY")
    _g8_begin_run(rt, dev, process, state, t, kind="column_comp", slot=int(a["curve"]))
    return {"curve": int(a["curve"]), "run_length_s": dev["run"]["length_s"]}


def _g8_c_sensor_errors(rt, dev, process, state, a, t):
    errs = a["errors"]
    _at_need(all(k in G8_ERRORS and 0 <= abs(v) <= G8_ERRORS[k] for k, v in errs.items()), "INVALID_ARGUMENT")
    for k in ("split_vent_restriction", "fid_jet_restriction", "inlet_leak_m3_s", "fid_leakage_A"):
        _at_need(errs.get(k, 0.0) >= 0, "INVALID_ARGUMENT")
    dev["errors"].update({k: float(v) for k, v in errs.items()})
    _g8_sync_process(dev, process)                                                  # the delivered gas follows the sensor errors
    return {"errors": dict(dev["errors"])}


def _g8_c_wake(rt, dev, process, state, a, t):
    _at_need(dev["sleep"] is not None, "PRECONDITION_FAILED")
    _g8_wake(dev, process, t, "Wake (touchscreen)")
    return {"awake": True}


_G8_COMMANDS = {
    "run.prep": _g8_c_prep, "run.start": _g8_c_start, "run.stop": _g8_c_stop, "method.configure": _g8_c_method,
    "method.event_save": _g8_c_event_save, "method.event_delete": _g8_c_event_delete, "settings.configure": _g8_c_settings,
    "clock.set": _g8_c_clock, "status.configure": _g8_c_status_params, "plot.configure": _g8_c_plot,
    "diagnostics.resolve": _g8_c_resolve, "diagnostics.start_test": _g8_c_test_start, "diagnostics.cancel_test": _g8_c_test_cancel,
    "emf.configure": _g8_c_emf_configure, "emf.reset": _g8_c_emf_reset, "maintenance.start": _g8_c_maint_start,
    "maintenance.continue": _g8_c_maint_continue, "maintenance.abort": _g8_c_maint_abort, "scheduler.clock_add": _g8_c_clock_add,
    "scheduler.clock_delete": _g8_c_clock_delete, "calibration.zero": _g8_c_zero, "display.preview": _g8_c_preview,
    "tools.column_compensation": _g8_c_column_comp, "system.wake": _g8_c_wake, "sensor.configure_error": _g8_c_sensor_errors,
}
_G8_RESTART = "system.restart"


def _g8_power(dev, process, on, t):
    """Device bookkeeping at power on / off (power switch, Shut Down, Restart): boot, end of runs, procedures and tests; a power cycle clears
    shutdowns (p.39) and ends maintenance (p.133)."""
    if dev["procedure"] is not None:
        dev["method"] = dev["procedure"]["restore"]
        dev["procedure"] = None
    dev.update(run=None, prep=False, diag=None, preview=None, message=None, standby=False, sleep=None)
    process.running, process.injection_time_s = False, None
    if on:
        dev["booting_until"] = t + _G8_BOOT_S
        dev["power_epoch"] += 1
        dev["restart_pending"] = False
        dev["shutdown"], dev["alerts"], dev["flow_bad_s"] = {}, [], 0.0
        dev["ignition"] = dict(tries=0, lit_seen=False, failed=False)
        dev["last_activity_s"] = dev["idle_since"] = t
        _g8_sys_log(dev, t, "Power on")
        _g8_sync_process(dev, process)
        _g8_apply_temperatures(dev, process)
    else:
        dev["booting_until"] = None
        _g8_sys_log(dev, t, "Power off")


def _g8_c_restart(rt, dev, process, state, a, t):
    _at_need(process.powered, "PRECONDITION_FAILED")
    process.perform("power", enabled=False)
    _g8_power(dev, process, False, t)
    process.perform("power", enabled=True)
    _g8_power(dev, process, True, t)
    return {"restarting": True, "boot_s": _G8_BOOT_S}


_G8_COMMANDS["system.restart"] = _g8_c_restart


# ---------------------------------------------------------------- contract metadata of the terminal
def _g8_method_schema(comp):
    return _at_settings_schema([f for f in G8_METHOD[comp] if not f.get("write_via")])


def _g8_slots():
    def slot(origin, schema, visibility="agent", unit="1"):
        return {"value_schema": schema, "unit": unit, "origin": origin, "visibility": visibility, "access": "read_only", "write_via": None}
    nullable = lambda s: {"anyOf": [s, {"type": "null"}]}  # noqa: E731
    return {"gc.status": slot("derived", _AT_ANY), "gc.method": slot("setpoint", _AT_ANY), "gc.actuals": slot("derived", _AT_ANY),
            "gc.run": slot("derived", nullable(_AT_ANY)), "gc.last_run": slot("derived", nullable(_AT_ANY)), "gc.plot": slot("derived", _AT_ANY),
            "gc.status_params": slot("setpoint", {"type": "array", "items": _AT_STR}), "gc.alerts": slot("derived", _AT_LIST),
            "gc.diagnostics": slot("derived", _AT_ANY), "gc.emf": slot("derived", _AT_ANY), "gc.maintenance": slot("derived", nullable(_AT_ANY)),
            "gc.logs": slot("derived", _AT_ANY), "gc.settings": slot("setpoint", _AT_ANY), "gc.scheduler": slot("derived", _AT_ANY),
            "gc.calibration": slot("derived", _AT_ANY), "gc.column_compensation": slot("derived", _AT_ANY), "gc.system": slot("derived", _AT_ANY),
            "device.datetime": slot("derived", _AT_STR), "sensor.errors": slot("process_truth", _AT_ANY, "evaluator"),
            "gas.supply": slot("scene", _AT_ANY, "evaluator")}


_G8_E_RUN = ["gc.status", "gc.run", "gc.last_run", "gc.logs", "gc.emf", "gc.actuals", "thermal.oven.setpoint_k", "gc.calibration"]
_G8_E_METHOD = ["gc.method", "gc.status", "gc.actuals", "gc.logs", "thermal.oven.setpoint_k", "thermal.inlet.setpoint_k", "thermal.fid.setpoint_k"]
_G8_CAPDEFS = {
    "run.prep": ({}, (), {"prep": _AT_BOOL}, {}, "Prep Run：进入Pre Run状态（退出省气模式，不分流时关闭吹扫阀），就绪后状态栏显示READY FOR INJECTION（p.59、p.82）",
                 ["gc.status", "gc.actuals", "gc.method", "thermal.oven.setpoint_k"], "device_command"),
    "run.start": ({}, (), {"running": _AT_BOOL, "run_length_s": _at_num("s", 0)}, {},
                  "Start：仪器就绪（需要时已Prep Run）后开始运行：柱温程序与柱流程序、运行表事件、FID信号随运行时间（已装虚拟样品瓶时按其合成色谱）；"
                  "运行结束记入运行日志与EMF计数（p.59、p.83）", _G8_E_RUN, "device_command"),
    "run.stop": ({}, (), {"stopped": {"anyOf": [_AT_STR, {"type": "null"}]}}, {}, "Stop：立即终止当前运行（数据可能丢失）或取消Prep Run（p.60、p.83）",
                 _G8_E_RUN, "device_command"),
    "method.configure": ({"component": {"type": "string", "enum": list(G8_METHOD)}, "settings": _AT_ANY}, (),
                         {"component": _AT_STR, "settings": _AT_ANY}, {"component": "inlet", "settings": {"split_ratio": 50.0}},
                         "编辑Active Method的一个部件（柱温箱程序、前进样口、色谱柱1、前检测器FID、模拟输出1），触摸屏修改立即生效（p.63、p.79）；温度设定点经"
                         "thermal.set_temperature", _G8_E_METHOD, "configuration"),
    "method.event_save": ({"time_s": _at_num("s", 0, 59999.4), "type": {"type": "string", "enum": list(G8_EVENT_TYPES)},
                           "position": {"anyOf": [{"type": "string", "enum": ["On", "Off"]}, {"type": "null"}]},
                           "setpoint": {"anyOf": [_at_num("1", -1e6, 1e6), {"type": "null"}]}, "index": {"anyOf": [_at_num("1", 0, 49, True), {"type": "null"}]}},
                          ("position", "setpoint", "index"), {"events": _at_num("1", 0, 50, True)},
                          {"time_s": 60.0, "type": "Front Det H2 Flow", "position": "Off"}, "运行表：新增（index为空）或修改一条运行时间事件，按时间排序（p.84-85）",
                          ["gc.method"], "configuration"),
    "method.event_delete": ({"index": _at_num("1", 0, 49, True)}, (), {"events": _at_num("1", 0, 50, True)}, {"index": 0}, "运行表：删除一条事件（p.85）",
                            ["gc.method"], "configuration"),
    "settings.configure": ({"settings": _at_settings_schema(G8_SETTINGS)}, (), {"settings": _AT_ANY, "restart_required": _AT_BOOL},
                           {"settings": {"misc.pressure_units": "kPa"}}, "Settings：配置（进样口载气、色谱柱、柱温箱、检测器、杂项、就绪）、仪器计划、系统设置"
                           "（网络、时区、语言、节能、访问）与校准选项，按Apply保存（p.155-162、p.168-195、p.205）", ["gc.settings", "gc.system", "gc.method",
                                                                                                 "gc.actuals", "gc.status"], "configuration"),
    "clock.set": ({"date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                   "time": {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?$"}}, (), {"datetime": _AT_STR},
                  {"date": "2026-10-19", "time": "09:30"}, "设定系统日期与时间（p.157）；时间型EMF计数随GC时钟变化（p.130）", ["device.datetime", "gc.emf"], "configuration"),
    "status.configure": ({"action": {"type": "string", "enum": ["add", "remove"]}, "param": {"type": "string", "enum": list(G8_STATUS_PARAMS)}}, (),
                         {"params": {"type": "array", "items": _AT_STR}}, {"action": "add", "param": "septum_purge"},
                         "Home > Status：用+ Add增加或用X删除显示的参数（p.63-64）", ["gc.status_params"], "configuration"),
    "plot.configure": ({"signal": {"type": "string", "enum": list(G8_PLOT_SIGNALS)}, "x_axis_s": _at_num("s", 60, 3600),
                        "y_min": {"anyOf": [_at_num("1", -1e9, 1e9), {"type": "null"}]}, "y_max": {"anyOf": [_at_num("1", -1e9, 1e9), {"type": "null"}]},
                        "running": _AT_BOOL, "zoom_cycle": _AT_BOOL}, ("signal", "x_axis_s", "y_min", "y_max", "running", "zoom_cycle"), {
                           "signal": _AT_STR, "x_axis_s": _at_num("s"), "y_min": {"anyOf": [_at_num("1"), {"type": "null"}]},
                           "y_max": {"anyOf": [_at_num("1"), {"type": "null"}]}, "running": _AT_BOOL, "zoom": _at_num("1", 1, 4, True)},
                       {"signal": "signal", "x_axis_s": 600}, "Home > Plot：信号名称、X轴1-60 min、Y轴范围、Start/Stop Plot，点按图形循环缩放1x/2x/4x（p.65-66）",
                       ["gc.plot"], "configuration"),
    "diagnostics.resolve": ({"alert_id": _at_num("1", 1, None, True), "action": {"type": "string", "enum": ["keep_on", "turn_off"]}}, (),
                            {"cleared": _at_num("1", 1, None, True)}, {"alert_id": 1, "action": "keep_on"},
                            "Diagnostics > Warnings and Errors：选择Setpoint Action清除停机（Keep Shutdown Zone On / Turn Shutdown Zone Off，p.34-35）",
                            ["gc.alerts", "gc.status", "gc.method", "gc.actuals"], "device_command"),
    "diagnostics.start_test": ({"test": {"type": "string", "enum": list(G8_TESTS)}}, (), {"test": _AT_STR, "duration_s": _at_num("s", 0)},
                               {"test": "leak_restriction"}, "Diagnostics > Diagnostic Tests：启动进样口或检测器诊断测试（DIAGNOSTIC MODE，p.122、p.124-126）",
                               ["gc.diagnostics", "gc.status", "gc.logs"], "device_command"),
    "diagnostics.cancel_test": ({}, (), {"aborted": _AT_STR}, {}, "中止正在进行的诊断测试（确认对话框后，p.126）", ["gc.diagnostics", "gc.status", "gc.logs"],
                                "device_command"),
    "emf.configure": ({"counter": {"type": "string", "enum": list(G8_EMF)}, "due_on": _AT_BOOL, "due": _at_num("1", 0, 1e7),
                       "warn_on": _AT_BOOL, "warn": _at_num("1", 0, 1e7)}, ("due_on", "due", "warn_on", "warn"), {"counter": _AT_STR},
                      {"counter": "inlet.septum_inj", "warn_on": True, "warn": 150}, "EMF计数Details：Service Due / Service Warning限值与Enable，Apply（p.138）",
                      ["gc.emf", "gc.logs"], "configuration"),
    "emf.reset": ({"counter": {"type": "string", "enum": list(G8_EMF)}}, (), {"counter": _AT_STR, "value": _at_num("1", 0)},
                  {"counter": "inlet.septum_inj"}, "EMF计数Reset Counter（确认Yes，记入维护日志，p.138-139）", ["gc.emf", "gc.logs"], "device_command"),
    "maintenance.start": ({"procedure": {"type": "string", "enum": list(G8_PROCEDURES)}}, (), {"procedure": _AT_STR, "step": _AT_STR},
                          {"procedure": "septum"}, "Perform Maintenance > Start Maintenance：降温/关气后引导维护（MAINTENANCE MODE，p.56、p.132-133）",
                          ["gc.maintenance", "gc.status", "gc.method", "gc.logs", "thermal.oven.setpoint_k", "thermal.inlet.setpoint_k",
                           "thermal.fid.setpoint_k"], "device_command"),
    "maintenance.continue": ({}, (), {"completed": _AT_STR}, {}, "维护步骤完成（部件已更换）：恢复方法，相关EMF计数按Maintenance Reset清零（p.132-133）",
                             ["gc.maintenance", "gc.status", "gc.method", "gc.emf", "gc.logs", "thermal.oven.setpoint_k", "thermal.inlet.setpoint_k",
                              "thermal.fid.setpoint_k"], "device_command"),
    "maintenance.abort": ({}, (), {"aborted": _AT_STR}, {}, "中止维护流程并恢复方法（p.133）", ["gc.maintenance", "gc.status", "gc.method", "gc.logs",
                                                                                   "thermal.oven.setpoint_k", "thermal.inlet.setpoint_k",
                                                                                   "thermal.fid.setpoint_k"], "device_command"),
    "scheduler.clock_add": ({"type": {"type": "string", "enum": list(G8_CLOCK_TYPES)}, "frequency": {"type": "string", "enum": list(G8_CLOCK_FREQ)},
                             "time": {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$"}}, (), {"entries": _at_num("1", 0, 30, True)},
                            {"type": "Blank Run", "frequency": "Weekdays", "time": "06:30"}, "时钟表+Add：按24小时时钟执行空白运行或Prep Run（p.208）",
                            ["gc.scheduler"], "configuration"),
    "scheduler.clock_delete": ({"index": _at_num("1", 0, 29, True)}, (), {"entries": _at_num("1", 0, 30, True)}, {"index": 0}, "时钟表X删除一条事件（p.208）",
                               ["gc.scheduler"], "configuration"),
    "calibration.zero": ({"sensor": {"type": "string", "enum": list(G8_SENSORS)}}, (), {"sensor": _AT_STR}, {"sensor": "inlet_flow"},
                         "Settings > Calibration：流量传感器在气体流动时、压力传感器在断开气源时置零，零点用于修正读数（p.152-154）",
                         ["gc.calibration", "gc.actuals"], "device_command"),
    "display.preview": ({"brightness_pct": _at_num("1", 1, 100, True)}, (), {"until_s": _at_num("s")}, {"brightness_pct": 60},
                        "Power Saving > Preview (for 5 s)：屏幕亮度按输入值显示5秒（p.159）", ["gc.system"], "device_command"),
    "tools.column_compensation": ({"curve": _at_num("1", 1, 4, True)}, (), {"curve": _at_num("1", 1, 4, True), "run_length_s": _at_num("s", 0)},
                                  {"curve": 1}, "Tools > Column Compensation：不进样运行一次方法并保存检测器基线为补偿曲线1-4（p.163-164）",
                                  _G8_E_RUN + ["gc.column_compensation"], "device_command"),
    "system.wake": ({}, (), {"awake": _AT_BOOL}, {}, "唤醒睡眠中的GC（恢复唤醒方法或睡眠前的Active Method，p.34、p.203）",
                    ["gc.scheduler", "gc.status", "gc.method"], "device_command"),
    "system.restart": ({}, (), {"restarting": _AT_BOOL, "boot_s": _at_num("s", 0)}, {}, "Settings > Power > Restart：重新启动GC（网络设置在重启后生效，p.156、p.165）",
                       ["gc.system", "gc.status", "gc.run", "gc.maintenance", "gc.alerts"], "device_command"),
    "system.shutdown": ({}, (), {"shut_down": _AT_BOOL}, {}, "Settings > Power > Shut Down：从触摸屏关机（软件关机，与电源开关同样结束运行、测试与维护，p.165）；"
                        "另一会话的运行、测试或维护进行中时被拒（ACCESS_DENIED）",
                        ["power.enabled", "gc.system", "gc.status", "gc.run", "gc.maintenance", "gc.alerts"], "device_command"),
    "sensor.configure_error": ({"errors": {"type": "object", "properties": {k: _at_num({"split_vent_restriction": "1", "fid_jet_restriction": "1"}.get(
        k, "A" if k.endswith("_A") else "Pa" if k.endswith("_Pa") else "m3/s"), -v, v) for k, v in G8_ERRORS.items()}, "additionalProperties": False,
        "required": [], "minProperties": 1}}, (), {"errors": _AT_ANY}, {"errors": {"inlet_leak_m3_s": 1.6e-8}},
                               "评测者：传感器误差参数（流量/压力零点偏移、进样口泄漏、分流与喷嘴堵塞、FID漏电流），默认0（理想）；EPC把读数保持在设定值，"
                               "偏移改变实际输送的气体；校准置零修正偏移",
                               ["sensor.errors", "gc.actuals", "gc.diagnostics"], "configuration"),
}
_G8_TERMINAL = tuple(_G8_CAPDEFS)
_G8_ERROR_TEXT = {"NOT_READY": "The GC is not ready (see the status tray and Diagnostics).", "BUSY": "A run, test, maintenance or start-up is in progress.",
                  "NOT_FOUND": "The entry does not exist.", "LOCKED": "The column configuration is locked (Column ID lock).",
                  "GAS_NOT_FLOWING": "Flow sensors are zeroed with the gas connected and flowing.",
                  "SUPPLY_CONNECTED": "Pressure sensors are zeroed with the supply line disconnected."}
_G8_LIMITS = [
    "8890B touch terminal: Active Method (oven program with up to 32 ramps, SSL split / splitless with gas saver, column 1 constant / ramped "
    "flow or pressure from the compressible Poiseuille relation, FID with Flame On, Lit offset and reignition, analog out 1, run table), Prep Run / "
    "Start / Stop, readiness, shutdowns and setpoint actions, diagnostic tests, EMF counters, maintenance procedures, logs, configuration, "
    "instrument schedule and clock table, sensor zeroing and system settings follow the 8890/8850 operation manual as a minimal device model, "
    "not Agilent firmware.",
    "A panel run uses the instant synthetic chromatogram of the loaded virtual vial (the injection at Start); the signal follows run time. "
    "Manual injections do not increment EMF injection counters (p.130). Sensor error parameters are evaluator settings, 0 (ideal) by default.",
    "Absent hardware: ALS, valves, cryo, MSD, a second inlet / detector, sequences (enabled only from the Browser Interface), USB storage; "
    "Browser Interface, data system and network services are not simulated (network settings are kept). Pulsed SSL modes are not modelled.",
    "EPC sensor errors (pressure / flow offsets less the stored zeros) act on the gas really delivered: the controllers hold their readings at "
    "the setpoints, so the screen shows the setpoints while the true head pressure, column / split / purge / detector gas flows differ (the "
    "process truth); the template chromatogram itself depends only on the zone setpoints.  The electrometer leakage is part of the live and "
    "the recorded FID signal.  Slow Cool Down Mode halves the oven cooling conductance (assumption); Fast Peaks sets the analog output "
    "filter (first order, 1.6 Hz / 8 Hz, p.186).",
    "Language: the selection is stored and reported in gc.settings; Deutsch / Espanol / Francais translate the main labels (our translations "
    "from the earlier panel work); the other languages keep English texts (our manuals contain no translated screen texts).  Column Min / Max "
    "/ Max Program Temp are stored and reported; the manual does not state that they limit the oven program, so they do not.",
]


def _g8_caps():
    caps = {}
    slots = _g8_slots()
    for name, (inputs, optional, outputs, example, desc, effects, kind) in _G8_CAPDEFS.items():
        vis = "evaluator" if name == "sensor.configure_error" else "agent"
        caps[name] = {"input_schema": _at_obj(inputs, optional), "output_schema": _at_obj(outputs), "example_arguments": [example],
                      "state_ids": list(dict.fromkeys(effects)), "component_ids": ["display"], "preconditions": [
                          "Power on and start-up finished; runs, tests and maintenance procedures exclude each other as on the instrument."],
                      "scene_required": False, "availability": "implemented", "visibility": vis, "kind": kind, "description_zh": desc,
                      "invocation": "AgilentGC8890B.invoke(instance_id=..., capability_id=" + repr(name) + ", arguments=..., request_id=...)",
                      "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "ACCESS_DENIED", "REQUEST_CONFLICT"] +
                      sorted(_G8_ERROR_TEXT), "rejection_side_effects": "none", "scene_failure_side_effects": "unknown"}
    every = sorted({s for c in caps.values() for s in c["state_ids"]} | {"power.enabled", "lid.angle_rad", "lid.closed"})
    caps.update(_at_panel_cap_entries(GC8890B_PANEL_CAPS, [s for s in every if not s.startswith(("sensor.", "gas."))],
                                      "AgilentGC8890B.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)"))
    return caps


GC8890B_PANEL_PATH = GC8890B_ASSET_DIR / "panel_ui.json"
_G8_BASE_CAPS = tuple(export_agilent_gc8890b_contract_metadata()["capabilities"])
GC8890B_PANEL_SPEC = (_at_pu.load_spec(GC8890B_PANEL_PATH, capabilities=set(_G8_TERMINAL) | set(_G8_BASE_CAPS))
                      if GC8890B_PANEL_PATH.is_file() and not _at_os.environ.get("G00_PANEL_BUILD") else None)   # the spec builder imports this
GC8890B_PANEL_CAPS = _at_pu.capability_definitions(GC8890B_PANEL_SPEC) if GC8890B_PANEL_SPEC else {}
_g8_base_export = export_agilent_gc8890b_contract_metadata


def export_agilent_gc8890b_contract_metadata():
    """CHEM-096 metadata plus the touch terminal: device state slots, terminal commands and panel.* capabilities; the CHEM-096 capabilities
    keep their schemas and meaning."""
    meta = _g8_base_export()
    meta["asset_version"] = GC8890B_ASSET_VERSION
    meta["state_slots"].update(_g8_slots())
    meta["capabilities"].update(_g8_caps())
    meta["limits"] = meta["limits"] + _G8_LIMITS
    meta["terminal_capabilities"] = sorted(_G8_TERMINAL) + sorted(GC8890B_PANEL_CAPS)
    return meta


# ---------------------------------------------------------------- what the screen shows (host values of the panel; display units)
def _g8_fmt_p(dev, pa):
    u = dev["settings"]["misc.pressure_units"]
    return f"{pa / G8_PRESSURE_UNITS[u]:.{ {'psi': 3, 'bar': 4, 'kPa': 2}[u] }f} {u}"


def _g8_fmt_c(K):
    return f"{K - 273.15:.2f} °C"


def _g8_fmt_f(m3_s):
    return f"{m3_s / _G8_ML:.3f} mL/min"


def _g8_texts(host, values):
    dev, a, st, m = host._device, values["gc.actuals"], values["gc.status"], host._device["method"]
    s = dev["settings"]
    elapsed = dev["run"]["elapsed_s"] if dev["run"] else None
    col_flow, col_p = _g8_column_setpoint(dev, a["oven_K"], elapsed)
    rem = st["est_remaining_s"]
    return {
        "inlet_pressure": _g8_fmt_p(dev, a["inlet_pressure_Pa"]), "inlet_temp": _g8_fmt_c(a["inlet_K"]), "inlet_temp_sp": _g8_fmt_c(m["inlet"]["temperature_K"]),
        "total_flow": _g8_fmt_f(a["total_flow_m3_s"]), "septum_purge": _g8_fmt_f(a["septum_purge_m3_s"]), "split_flow": _g8_fmt_f(a["split_vent_m3_s"]),
        "column_flow": _g8_fmt_f(a["column_flow_m3_s"]), "column_pressure": _g8_fmt_p(dev, a["column_pressure_Pa"]),
        "velocity": "--" if a["column_velocity_m_s"] is None else f"{a['column_velocity_m_s'] * 100:.2f} cm/s",
        "holdup": "--" if a["holdup_s"] is None else f"{a['holdup_s'] / 60:.4f} min",
        "column_dims": (f"{s['column.length_m']:g} m x {s['column.diameter_m'] * 1000:g} mm x {s['column.film_m'] * 1e6:g} µm"
                        if _g8_column_defined(s) else "not defined"),
        "oven_temp": _g8_fmt_c(a["oven_K"]), "oven_sp": _g8_fmt_c(m["oven"]["initial_K"]), "oven_max": _g8_fmt_c(s["oven.max_temp_K"]),
        "det_temp": _g8_fmt_c(a["detector_K"]), "det_temp_sp": _g8_fmt_c(m["detector"]["temperature_K"]),
        "h2": _g8_fmt_f(a["h2_m3_s"]), "air": _g8_fmt_f(a["air_m3_s"]), "makeup": _g8_fmt_f(a["makeup_m3_s"]),
        "signal": f"{a['signal_A'] * 1e12:.1f} pA", "flame": "Lit" if a["flame_lit"] else "Off", "analog_out": f"{a['analog_out']:.3f} du",
        "run_length": f"{_g8_run_length(m) / 60:.2f} min", "run_time": "--" if st["run_time_s"] is None else f"{st['run_time_s'] / 60:.2f}",
        "est_remaining": "--" if rem is None else f"{rem / 60:.2f}", "reasons": ", ".join(st["reasons"]) or "none",
        "sample": values["sample.id"] if values["sample.loaded"] else "none",
        "setpoint_flow": _g8_fmt_f(col_flow), "setpoint_pressure": _g8_fmt_p(dev, col_p),
    }


def _g8_status_rows(dev, tx):
    m = dev["method"]
    i, d = m["inlet"], m["detector"]
    rows = {"oven_temp": (tx["oven_sp"], tx["oven_temp"]), "inlet_temp": (tx["inlet_temp_sp"], tx["inlet_temp"]),
            "inlet_pressure": (tx["setpoint_pressure"], tx["inlet_pressure"]), "inlet_total_flow": ("--", tx["total_flow"]),
            "septum_purge": (_g8_fmt_f(i["septum_purge_m3_s"] if i["septum_purge_on"] else 0.0), tx["septum_purge"]),
            "column_flow": (tx["setpoint_flow"], tx["column_flow"]), "column_pressure": (tx["setpoint_pressure"], tx["column_pressure"]),
            "detector_temp": (tx["det_temp_sp"], tx["det_temp"]), "h2_flow": (_g8_fmt_f(d["h2_flow_m3_s"] if d["h2_on"] else 0.0), tx["h2"]),
            "air_flow": (_g8_fmt_f(d["air_flow_m3_s"] if d["air_on"] else 0.0), tx["air"]),
            "makeup_flow": (_g8_fmt_f(d["makeup_flow_m3_s"] if d["makeup_on"] else 0.0), tx["makeup"]), "signal": ("--", tx["signal"])}
    return [dict(id=p, name=G8_STATUS_PARAMS[p], setpoint=rows[p][0], actual=rows[p][1]) for p in dev["status_params"]]


def _g8_emf_text(c, typ):
    v = c["value"]
    return f"{v:.1f} {typ}" if typ == "days" else f"{v:.0f} {typ}"


def _g8_emf_rows(dev):
    out = {}
    for k, (comp, part, typ, _) in G8_EMF.items():
        c = dev["emf"][k]
        lvl = c.get("level", "ok")
        out.setdefault(comp, []).append(dict(counter=k, part=f"{part} ({typ})", status=_g8_emf_text(c, typ) + {"ok": "", "warning": "  [warning]",
                                                                                                                 "due": "  [service due]"}[lvl],
                                             level=lvl, due_on=c["due_on"], due=c["due"], warn_on=c["warn_on"], warn=c["warn"],
                                             last=c["resets"][-1]["time"] if c["resets"] else "--"))
    return out


def _g8_health_report(host, values, tx):
    dev, s = host._device, host._device["settings"]
    lines = ["System information", f"  Agilent 8890B GC  serial {values['gc.system']['serial']}  firmware {values['gc.system']['firmware']}",
             f"  Date / time {values['device.datetime']}  {s['time.zone']}", "System configuration",
             f"  Front inlet SSL ({s['inlet.carrier_gas']}), column 1, front FID (makeup {s['detector.makeup_gas']})",
             "Active instrument conditions"]
    lines += [f"  {a['level']}: {a['text']}" for a in dev["alerts"]] or ["  none"]
    lines += [f"  not ready: {r}" for r in values["gc.status"]["reasons"]]
    lines += ["Column details", f"  {tx['column_dims']}  serial {s['column.serial'] or '--'}  max {_g8_fmt_c(s['column.max_temp_K'])}",
              "Early Maintenance Feedback"]
    flagged = [f"  {G8_EMF[k][0]}: {G8_EMF[k][1]} {c.get('level')}" for k, c in dev["emf"].items() if c.get("level", "ok") != "ok"]
    lines += flagged or ["  all counters within limits"]
    lines += ["Diagnostic test results"] + ([f"  {G8_TESTS[k][1]}: {r['result']} ({r['time']})" for k, r in dev["diag_results"].items()] or ["  none"])
    lines += ["Network information", f"  {'DHCP' if s['network.dhcp'] else 'static'}  host {s['network.host_name']}  IP {s['network.ip']}",
              "Status snapshot", f"  {values['gc.status']['text']}  oven {tx['oven_temp']}  inlet {tx['inlet_pressure']}  signal {tx['signal']}"]
    return [{"text": x} for x in lines]


def _g8_service_info(host, values, tx):
    p = host._process
    sysv = values["gc.system"]
    return [dict(component="Instrument", rows=[["Serial number", sysv["serial"]], ["Firmware", sysv["firmware"]],
                                              ["On time", f"{host._device['on_time_s'] / 3600:.1f} h"], ["Power", "on" if p.powered else "off"]]),
            dict(component="Front Inlet", rows=[["Type", "Split/Splitless"], ["Temperature", tx["inlet_temp"]],
                                               ["Heater power", f"{p.zones['inlet'].heater_power_W:.1f} W"], ["Pressure", tx["inlet_pressure"]],
                                               ["Total flow", tx["total_flow"]]]),
            dict(component="Oven", rows=[["Temperature", tx["oven_temp"]], ["Heater power", f"{p.zones['oven'].heater_power_W:.1f} W"],
                                        ["Cooling flap", "open" if p.zones["oven"].cooling_damper_open else "closed"],
                                        ["Door", "open" if p.door_open else "closed"]]),
            dict(component="Front Detector", rows=[["Type", "FID"], ["Temperature", tx["det_temp"]],
                                                  ["Heater power", f"{p.zones['fid'].heater_power_W:.1f} W"], ["Signal", tx["signal"]],
                                                  ["Flame", tx["flame"]], ["H2 / Air / Makeup", f"{tx['h2']} / {tx['air']} / {tx['makeup']}"]]),
            dict(component="Column 1", rows=[["Dimensions", tx["column_dims"]], ["Flow", tx["column_flow"]], ["Pressure", tx["column_pressure"]],
                                            ["Average velocity", tx["velocity"]]])]


# ---------------------------------------------------------------- runtime and session with the terminal
_G8BaseRuntime = AgilentGC8890B
_G8BaseSession = AgilentGC8890BSession


class AgilentGC8890B(_G8BaseRuntime):
    """CHEM-096 host with the 8890B touch terminal: the device model (self._device), switched heaters and the front panel (self.panel)."""

    def __init__(self, instance_id, *, scene_adapter=None):
        super().__init__(instance_id, scene_adapter=scene_adapter)
        p = self._process
        p.zones["oven"] = _g8_zone_copy(GC8890BSwitchedOvenZone, p.zones["oven"])
        for zone in ("inlet", "fid"):
            p.zones[zone] = _g8_zone_copy(GC8890BSwitchedZone, p.zones[zone])
        self._device = _g8_new_device()
        m = self._device["method"]
        m["oven"]["initial_K"], m["inlet"]["temperature_K"], m["detector"]["temperature_K"] = (p.zones[z].setpoint_K for z in ("oven", "inlet", "fid"))
        self._principals["__panel__"] = "agent"
        self._panel_session = AgilentGC8890BSession(self, "__panel__")      # internal principal of commands without a gesturing session
        self._caller = None                                                 # principal of the session making the current panel gesture
        self.panel_capture_dir = None
        self.panel = _at_pu.PanelUI(GC8890B_PANEL_SPEC, _ATPanelHost(self)) if GC8890B_PANEL_SPEC else None

    def debug_snapshot(self):
        with self._lock:
            out = super().debug_snapshot()
            out["device"] = _dr6000_copy.deepcopy(self._device)
            return out

    def set_gas_supply(self, **supply):
        """Trusted host: gas sources at the back of the GC (on and connected), p.54; default all off."""
        with self._lock:
            if not supply or any(k not in ("carrier", "hydrogen", "air", "makeup") or type(v) is not bool for k, v in supply.items()):
                raise ValueError("Known gases with boolean states required")
            dev, process = _dr6000_copy.deepcopy(self._device), _dr6000_copy.deepcopy(self._process)
            dev["supply"].update(supply)
            _g8_sync_process(dev, process)
            self._device, self._process = dev, process
            self._revision += 1

    def store_method(self, name, method=None):
        """Trusted host (a data-system download): the SLEEP / WAKE / CONDITION methods the scheduler loads (p.76, p.202-204).  method: a
        complete Active Method in the gc.method layout, or None to remove it."""
        with self._lock:
            if name not in ("SLEEP", "WAKE", "CONDITION"):
                raise ValueError("SLEEP, WAKE or CONDITION")
            if method is not None:
                if set(method) != set(G8_METHOD) | {"events"}:
                    raise ValueError("Complete method required")
                for comp, table in G8_METHOD.items():
                    _at_apply(table, _at_defaults(table), method[comp])
            self._device["stored"][name] = _dr6000_copy.deepcopy(method)
            self._revision += 1

    def host_step(self, dt_s):
        """Trusted-host simulation step in sub-steps of at most 1 s: the process, then the terminal's clock-driven behaviour."""
        with self._lock:
            if not _dr6000_number(dt_s) or dt_s <= 0:
                raise ValueError("dt_s must be finite positive seconds")
            process, dev, state = (_dr6000_copy.deepcopy(x) for x in (self._process, self._device, self._state))
            t, end = process.time_s, process.time_s + dt_s
            while t < end - 1e-9:
                process.step(min(_G8_STEP_S, end - t))
                _g8_tick(self, dev, process, state, t, process.time_s)
                t = process.time_s
            self._refresh_thermal(state, process)
            if self._scene_adapter is not None:
                self._scene_adapter(self.instance_id, self._public_scene_state(state))
            self._process, self._state, self._device = process, state, dev
            self._revision += 1
            return self.read()

    def _values(self):
        """Every slot value: the CHEM-096 state and the terminal's device slots."""
        dev, p, t = self._device, self._process, self._time()
        values = dict(self._state)
        run = dev["run"]
        values.update({
            "gc.status": _g8_status(dev, p, t), "gc.method": _dr6000_copy.deepcopy(dev["method"]), "gc.actuals": _g8_actuals(self, dev, p, self._state),
            "gc.run": None if run is None else {k: run[k] for k in ("kind", "start", "elapsed_s", "length_s", "sample_id", "slot")},
            "gc.last_run": _dr6000_copy.deepcopy(dev["last_run"]),
            "gc.plot": _dr6000_copy.deepcopy(dev["plot"]), "gc.status_params": list(dev["status_params"]),
            "gc.alerts": _dr6000_copy.deepcopy(dev["alerts"]),
            "gc.diagnostics": dict(running=None if dev["diag"] is None else dict(dev["diag"], remaining_s=max(0.0, dev["diag"]["until_s"] - t)),
                                   results=_dr6000_copy.deepcopy(dev["diag_results"])),
            "gc.emf": {k: dict({x: c[x] for x in ("value", "due_on", "due", "warn_on", "warn")}, level=c.get("level", "ok"), component=G8_EMF[k][0],
                               part=G8_EMF[k][1], type=G8_EMF[k][2], resets=list(c["resets"])) for k, c in dev["emf"].items()},
            "gc.maintenance": None if dev["procedure"] is None else {k: v for k, v in dev["procedure"].items() if k != "restore"},
            "gc.logs": _dr6000_copy.deepcopy(dev["logs"]), "gc.settings": dict(dev["settings"]),
            "gc.scheduler": dict(sleep=None if dev["sleep"] is None else {k: v for k, v in dev["sleep"].items() if k != "restore"},
                                 clock_table=_dr6000_copy.deepcopy(dev["clock_table"]), stored={k: v is not None for k, v in dev["stored"].items()},
                                 standby=dev["standby"]),
            "gc.calibration": dict(zero_times=dict(dev["zero_times"])),
            "gc.column_compensation": {k: {"time": v["time"], "points": v["points"]} for k, v in dev["column_comp"].items()},
            "gc.system": dict(booting=dev["booting_until"] is not None, restart_pending=dev["restart_pending"], power_epoch=dev["power_epoch"],
                              beeps=dev["beeps"], prep=dev["prep"], preview=dev["preview"], mac="00:30:D3:00:00:01", serial="SIM-8890B-0001",
                              firmware="sim-1.2.0", manufactured="2026-01-05"),
            "device.datetime": _at_now(dev, t), "sensor.errors": dict(dev["errors"]), "gas.supply": dict(dev["supply"]),
        })
        return values

    def _display(self, t):
        """Screen brightness (percent) and state: Preview, dim / shut off after inactivity (p.22, p.159)."""
        dev, s = self._device, self._device["settings"]
        if dev["preview"] and t < dev["preview"]["until_s"]:
            return dev["preview"]["brightness_pct"], "on"
        idle = t - dev["last_activity_s"]
        unit = {"s": 1.0, "min": 60.0, "h": 3600.0}
        if s["power.off_on"] and idle >= s["power.off_after"] * unit[s["power.off_unit"]]:
            return 0, "off"
        if s["power.dim_on"] and idle >= s["power.dim_after"] * unit[s["power.dim_unit"]]:
            return s["power.dim_to_pct"], "dim"
        return s["power.brightness_pct"], "on"

    def panel_values(self):
        values = self._values()
        slots = self._metadata["state_slots"]
        out = {k: v for k, v in values.items() if slots.get(k, {}).get("visibility") == "agent"}
        t = self._time()
        bright, mode = self._display(t)
        dev = self._device
        tx = _g8_texts(self, values)
        rank = ["ok", "warning", "due"].index
        emf_rows = _g8_emf_rows(dev)
        out.update({"host.powered": self._process.powered, "host.power_epoch": dev["power_epoch"], "host.booting": dev["booting_until"] is not None,
                    "host.brightness": bright if self._process.powered else 0, "host.display": mode, "host.alert_count": len(dev["alerts"]),
                    "host.emf_flag": max((c.get("level", "ok") for c in dev["emf"].values()), key=rank),
                    "host.emf_levels": {comp: max((r["level"] for r in rows), key=rank) for comp, rows in emf_rows.items()},
                    "host.emf_rows": emf_rows, "host.text": tx, "host.status_rows": _g8_status_rows(dev, tx),
                    "host.health_report": _g8_health_report(self, values, tx), "host.service_info": _g8_service_info(self, values, tx),
                    "host.about": [["Manufacturing date", values["gc.system"]["manufactured"]], ["Serial number", values["gc.system"]["serial"]],
                                   ["Firmware revision", values["gc.system"]["firmware"]], ["Help and information revision", "sim-1.2.0"]],
                    "host.column_defined": _g8_column_defined(dev["settings"])})
        return out

    def panel_command(self, capability_id, arguments):
        """A command of a panel gesture runs as the session that made the gesture (self._caller, set by AgilentGC8890BSession._panel)."""
        who = getattr(self, "_caller", None) or "__panel__"
        session = self._panel_session if who == "__panel__" else AgilentGC8890BSession(self, who)
        return session._invoke_once(capability_id=capability_id, arguments=arguments, request_id="__panel__")

    def _chromatogram(self, state):
        """The recorded FID signal carries the detector's electrometer leakage (evaluator error, default 0) like the live signal; peak areas
        are baseline-corrected and unchanged."""
        ch = super()._chromatogram(state)
        leak = self._device["errors"]["fid_leakage_A"]
        return ch if ch is None or not leak else dict(ch, fid_current_A=[v + leak for v in ch["fid_current_A"]])

    def _after_base(self, capability_id, arguments):
        """Device bookkeeping after a CHEM-096 command succeeded (its own effects are unchanged)."""
        dev, p, t = self._device, self._process, self._time()
        if capability_id == "power.set":
            _g8_power(dev, p, arguments["enabled"], t)
            self._refresh_thermal(self._state, p)
        elif capability_id == "thermal.set_temperature":
            key = {"oven": ("oven", "initial_K"), "inlet": ("inlet", "temperature_K"), "fid": ("detector", "temperature_K")}[arguments["zone"]]
            dev["method"][key[0]][key[1]] = float(arguments["temperature_K"])
            if arguments["zone"] == "oven" and dev["standby"]:
                dev["standby"] = False
            if dev["run"] is not None:
                _at_log(dev["logs"]["run"], dict(time=f"{dev['run']['elapsed_s'] / 60:.2f}", text=f"Setpoint changed: {arguments['zone']} temperature"))
            _g8_wake(dev, p, t, "Wake: setpoint change")
            if dev["run"] is None:
                _g8_apply_temperatures(dev, p)
            self._refresh_thermal(self._state, p)


class AgilentGC8890BSession(_G8BaseSession):
    """CHEM-096 session plus the terminal commands and panel.*; terminal error codes are stable strings like the CHEM-096 ones."""

    def _response(self, status="succeeded", *, request_id=None, result=None, observations=None, code=None, side_effects="none"):
        if code in _G8_ERROR_TEXT:
            r = super()._response(status, request_id=request_id, result=result, observations=observations, code="PRECONDITION_FAILED",
                                  side_effects=side_effects)
            r["error"].update(code=code, message=_G8_ERROR_TEXT[code])
            return r
        return super()._response(status, request_id=request_id, result=result, observations=observations, code=code, side_effects=side_effects)

    def describe(self, *, asset_type_id=None, instance_id=None):
        out = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if "capabilities" in out and self._host._principals[self._principal_id] not in ("evaluator", "debug"):
            out["capabilities"] = {k: c for k, c in out["capabilities"].items() if c.get("visibility", "agent") == "agent"}
        return out

    def read(self, *, instance_id=None, state_ids=None):
        host = self._host
        with host._lock:
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            values = host._values()
            if state_ids is None:
                state_ids = [sid for sid in values if self._visible(sid)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids):
                return self._response("rejected", code="INVALID_ARGUMENT")
            if any(not self._visible(sid) or sid not in values for sid in state_ids):
                return self._response("rejected", code="ACCESS_DENIED")
            now = host._time()
            observations = {}
            for sid in state_ids:
                slot, value = host._metadata["state_slots"][sid], values[sid]
                observations[sid] = {"value": _dr6000_copy.deepcopy(value), "unit": slot["unit"],
                                     "quality": "unavailable" if value is None else ("estimated" if slot["origin"] == "derived" else "valid"),
                                     "sample_time_s": now, "sample_window_s": None}
                if value is None:
                    observations[sid]["reason"] = "No value has been established for this state."
            return self._response(observations=observations)

    def _g8_foreign(self, cap, args):
        """Another caller's run, diagnostic test or maintenance procedure would be stopped or changed by cap, or the touchscreen would restart /
        shut the GC down under it (lead rule 2026-10-19: ACCESS_DENIED on the touchscreen and the direct path); the evaluator / debug identities
        and the firmware's own timers are unrestricted.  The power switch (power.set models it) is a hard switch: never refused."""
        host, me = self._host, self._principal_id
        dev = getattr(host, "_device", None)
        if dev is None or me == "__panel__" or host._principals.get(me) in ("evaluator", "debug"):
            return False
        foreign = lambda x: x is not None and x.get("owner") not in (None, me)        # noqa: E731
        run, diag, proc = dev["run"], dev["diag"], dev["procedure"]
        if cap == "run.stop":
            return foreign(run)
        if cap == "diagnostics.cancel_test":
            return foreign(diag)
        if cap in ("maintenance.continue", "maintenance.abort"):
            return foreign(proc)
        if cap in ("system.restart", "system.shutdown"):
            return foreign(run) or foreign(diag) or foreign(proc)
        if cap in ("method.configure", "method.event_save", "method.event_delete", "calibration.zero", "thermal.set_temperature"):
            return foreign(run)                                                    # the running method / its setpoints
        return False

    def _invoke_once(self, *, capability_id, arguments, request_id, instance_id=None):
        host = self._host
        if self._target(instance_id) and self._g8_foreign(capability_id, arguments):
            return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
        if capability_id not in _G8_TERMINAL and capability_id not in GC8890B_PANEL_CAPS:
            if (capability_id == "thermal.set_temperature" and isinstance(arguments, dict) and arguments.get("zone") == "oven"
                    and _dr6000_number(arguments.get("temperature_K")) and not isinstance(arguments.get("temperature_K"), bool)
                    and arguments["temperature_K"] > host._device["settings"]["oven.max_temp_K"] + 1e-9 and self._target(instance_id)):
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")     # Maximum temperature setting (p.183)
            response = super()._invoke_once(capability_id=capability_id, arguments=arguments, request_id=request_id, instance_id=instance_id)
            if response["status"] == "succeeded":
                host._after_base(capability_id, arguments)
            return response
        if not self._target(instance_id):
            return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
        cap = host._metadata["capabilities"][capability_id]
        if not isinstance(arguments, dict) or _at_validate(cap["input_schema"], arguments):
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        if cap["visibility"] != "agent" and host._principals[self._principal_id] not in ("evaluator", "debug"):
            return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
        if capability_id.startswith("panel."):
            return self._panel(capability_id, arguments, request_id)
        if capability_id == "system.shutdown":                                    # the soft power-off: the CHEM-096 power path
            r = self._invoke_once(capability_id="power.set", arguments={"enabled": False}, request_id=request_id, instance_id=instance_id)
            return dict(r, result={"shut_down": True}) if r["status"] == "succeeded" else r
        state, process, dev = (_dr6000_copy.deepcopy(x) for x in (host._state, host._process, host._device))
        t = host._time()
        try:
            result = _G8_COMMANDS[capability_id](host, dev, process, state, arguments, t)
        except _ATReject as exc:
            return self._response("rejected", request_id=request_id, code=exc.code)
        owned = {"run.start": "run", "tools.column_compensation": "run", "diagnostics.start_test": "diag", "maintenance.start": "procedure"}.get(capability_id)
        if owned and dev[owned] is not None:
            dev[owned]["owner"] = self._principal_id                              # runs, tests and procedures belong to their caller
        host._refresh_thermal(state, process)
        if host._scene_adapter is not None and host._public_scene_state(state) != host._public_scene_state(host._state):
            try:
                host._scene_adapter(host.instance_id, host._public_scene_state(state))
            except Exception:
                return self._response("failed", request_id=request_id, code="DEVICE_ERROR", side_effects="unknown")
        host._state, host._process, host._device = state, process, dev
        host._revision += 1
        return self._response(request_id=request_id, result=result)

    def _panel(self, capability_id, arguments, request_id):
        host = self._host
        if host.panel is None:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        try:
            if capability_id == "panel.capture":
                return self._response(request_id=request_id, result=_at_capture(host.panel, host.instance_id, arguments["display_id"], host._time(),
                                                                                host.panel_capture_dir))
            if capability_id == "panel.frame":
                return self._response(request_id=request_id, result={"frame": host.panel.frame()})
            caller, host._caller = getattr(host, "_caller", None), self._principal_id    # the gesture's commands run as this caller
            try:
                if capability_id == "panel.press":
                    accepted = host.panel.press(arguments["input_id"], float(arguments.get("duration_s", 0.1)))
                elif capability_id == "panel.switch":
                    accepted = host.panel.switch(arguments["input_id"], arguments["position"])
                else:
                    if ("to_x" in arguments) != ("to_y" in arguments):
                        return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
                    to = (arguments["to_x"], arguments["to_y"]) if "to_x" in arguments else None
                    accepted = host.panel.touch(arguments["display_id"], arguments["x"], arguments["y"], float(arguments.get("duration_s", 0.1)),
                                                to=to)
            finally:
                host._caller = caller
        except _at_pu.PanelError:
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        host._device["last_activity_s"] = host._time()                                       # a touch wakes a dimmed or dark screen (p.22)
        return self._response(request_id=request_id, result={"accepted": bool(accepted)})
# <<< CHEM-096 GC 8890B touch terminal


# >>> CHEM-108 HS 8697 headspace terminal
# Device layer of the 8697 headspace sampler (operating manual references/manuals/agilent_hs8697/, 'p.N' = PDF page).  The HS has no screen of
# its own: a Park button with its lamp, the status LED, the rack LED and the On/Standby switch (p.15-16); its method, configuration,
# calibration, service, diagnostics, EMF and log pages are part of the host GC touchscreen (p.13, p.40).  This block adds the headspace method
# (temperatures, times, vial and loop, fill modes, extraction, venting and purging, leak check, sequence actions, readiness), heated zones with
# a first-order approach to setpoint, park / unpark, a sequence engine (vials placed into the oven by GC cycle, equilibration, pressurization
# and dynamic leak check, loop fill, injection through the CHEM-108 measure path, return to tray, purge) started from the browser interface /
# data system stand-in, the touchscreen Stop prompt, sequence actions, diagnostic tests, tray and EPC calibration, service mode with factory
# reset, EMF counters, logs and the front panel (panel_ui.json run by scripts/asset_runtime/_panel_ui.py).  The CHEM-108 capabilities keep
# their meaning.  Time comes from the host clock; host_update() processes the events up to its current time.
HS8697_ASSET_VERSION = "1.4.0"  # 1.4.0 (2026-10-20): front-control parts in the 3D model (geometry only)
HS8697_ASSET_DIR = _AT_ROOT / "assets/basic40_batch02/t3_analytical/agilent_hs8697"
_HS_ML = 1e-6 / 60.0
_HS_PSI = 6894.757293168
_HS_TAU_S = {"oven": 300.0, "loop_valve": 120.0, "transfer_line": 120.0}     # first-order heating time constants (assumption)
_HS_ZONES = ("oven", "loop_valve", "transfer_line")
_HS_BAND_K = 1.0                                                             # readiness band (assumption)
_HS_STABLE_S = 30.0                                                          # 30 s wait for heated zones to stabilize (p.83)
_HS_BOOT_S = 20.0                                                            # start-up (assumption)
_HS_MOVE_S = 15.0                                                            # tray / carousel / lifter moves per vial transfer (assumption)
_HS_STEP_S = 1.0
_HS_TEST_VIAL_S = 60.0                                                       # User Vial Leak Test time per vial (assumption)
G_HS_VIAL_SIZES = {1e-5: "10 mL", 2e-5: "20 mL", 2.2e-5: "22 mL"}
_HS_MIN = (1 / 60.0, 0.0, "min", 2)
_HS_C = (1.0, -273.15, "°C", 2)
_HS_MLMIN = (1 / _HS_ML, 0.0, "mL/min", 1)
_HS_PRESS = (1 / _HS_PSI, 0.0, "psi", 3)
G_HS_METHOD = [
    _at_field("oven_on", "Oven", "bool", True, "temperatures", "manual p.40 (Figure 13: checkbox, setpoint, actual)"),
    _at_field("oven_K", "Oven temperature", "number", 333.15, "temperatures", "manual p.43, p.82 (oven 15 C below the solvent boiling point)",
              min=273.15, max=573.15, si_unit="K", show=_HS_C, write_via="thermal.set_temperature"),
    _at_field("loop_on", "Loop", "bool", True, "temperatures", "manual p.40 (Figure 13)"),
    _at_field("loop_K", "Loop temperature", "number", 343.15, "temperatures", "manual p.43, p.82 (never lower than the oven)", min=273.15, max=573.15,
              si_unit="K", show=_HS_C, write_via="thermal.set_temperature"),
    _at_field("transfer_on", "Transfer Line", "bool", True, "temperatures", "manual p.40 (Figure 13)"),
    _at_field("transfer_K", "Transfer Line temperature", "number", 353.15, "temperatures", "manual p.43, p.82 (never lower than the loop)", min=273.15,
              max=573.15, si_unit="K", show=_HS_C, write_via="thermal.set_temperature"),
    _at_field("cooling_plate_K", "Cooling Plate", "number", 298.15, "temperatures", "manual p.43, p.47 (expected tray temperature +/- 5 C; 4 to 80 C)",
              min=277.15, max=353.15, si_unit="K", show=_HS_C),
    _at_field("equilibration_s", "Vial Equilibration", "number", 900.0, "times", "manual p.43, p.82 (at least 15 min if unknown)", min=0.0,
              max=59999.4, si_unit="s", show=_HS_MIN, write_via="method.set_equilibration_time"),
    _at_field("injection_s", "Injection Duration", "number", 30.0, "times", "manual p.82 (default 0.50 min)", min=0.0, max=59999.4, si_unit="s",
              show=_HS_MIN),
    _at_field("gc_cycle_s", "GC Cycle", "number", 1200.0, "times", "manual p.43, p.45 (measured cycle + 0.2-0.5 min)", min=0.0, max=59999.4,
              si_unit="s", show=_HS_MIN, assumption="default 20 min"),
    _at_field("vial_size_m3", "Vial Size", "enum", 2e-5, "vial_loop", "manual p.83 (10, 20 or 22 mL)", options=list(G_HS_VIAL_SIZES), si_unit="m3"),
    _at_field("shake", "Shake Value", "number", 0, "vial_loop", "manual p.83, p.89 (1-9, 0 to disable)", min=0, max=9, integer=True),
    _at_field("fill_mode", "Vial Fill Mode", "enum", "Flow to Pressure", "fill", "manual p.68, p.83 (default Flow to Pressure)",
              options=["Flow to Pressure", "Pressure", "Constant Volume"]),
    _at_field("fill_pressure_Pa", "Vial Fill Pressure", "number", 15 * _HS_PSI, "fill", "manual p.27, p.83 (above the equilibration pressure)",
              min=0.0, max=45 * _HS_PSI, si_unit="Pa", show=_HS_PRESS, assumption="maximum 45 psi (caps vent above about 310 kPa, p.27)"),
    _at_field("fill_flow_m3_s", "Vial Fill Flow", "number", 50 * _HS_ML, "fill", "manual p.43, p.83 (default 50 mL/min)", min=1 * _HS_ML,
              max=200 * _HS_ML, si_unit="m3/s", show=_HS_MLMIN),
    _at_field("fill_volume_m3", "Fill Volume", "number", 1e-6, "fill", "manual p.43, p.83 (Constant Volume only)", min=1e-7, max=1e-5, si_unit="m3",
              show=(1e6, 0.0, "mL", 2)),
    _at_field("pressure_eq_s", "Pressure Equilibration Time", "number", 30.0, "fill", "manual p.83 (default 0.50 min)", min=0.0, max=59999.4,
              si_unit="s", show=_HS_MIN),
    _at_field("loop_fill_mode", "Loop Fill Mode", "enum", "Default", "fill", "manual p.70, p.84 (Default / Custom)", options=["Default", "Custom"]),
    _at_field("loop_ramp_Pa_s", "Loop Ramp Rate", "number", 20 * _HS_PSI / 60, "fill", "manual p.84 (default 20 psi/min)", min=0.0, max=200 * _HS_PSI / 60,
              si_unit="Pa/s", show=(60 / _HS_PSI, 0.0, "psi/min", 1)),
    _at_field("final_loop_Pa", "Final Loop Pressure", "number", 1 * _HS_PSI, "fill", "manual p.84, p.91 (> 1 psi recommended)", min=0.0,
              max=45 * _HS_PSI, si_unit="Pa", show=_HS_PRESS),
    _at_field("loop_eq_s", "Loop Equilibration", "number", 3.0, "fill", "manual p.84 (default 0.05 min)", min=0.0, max=59999.4, si_unit="s", show=_HS_MIN),
    _at_field("extraction_mode", "Extraction mode", "enum", "Single", "extraction", "manual p.44, p.92", options=["Single", "Multiple", "Concentrated"]),
    _at_field("extractions", "Number of extractions", "number", 2, "extraction", "manual p.44 (Concentrated only)", min=1, max=100, integer=True),
    _at_field("vent_after_last", "Vent vial pressure after the last extraction", "bool", True, "venting", "manual p.44, p.86"),
    _at_field("vent_between", "Vent vial pressure between extractions", "bool", False, "venting", "manual p.44, p.86"),
    _at_field("purge_mode", "Purge Flow Mode", "enum", "Default", "venting", "manual p.86 (Default 100 mL/min for 1 min, Custom, Off)",
              options=["Default", "Custom", "Off"]),
    _at_field("purge_flow_m3_s", "Purge Flow", "number", 100 * _HS_ML, "venting", "manual p.85 (default 100 mL/min)", min=0.0, max=500 * _HS_ML,
              si_unit="m3/s", show=_HS_MLMIN),
    _at_field("purge_time_s", "Purge Time", "number", 60.0, "venting", "manual p.86", min=0.0, max=59999.4, si_unit="s", show=_HS_MIN),
    _at_field("leak_test", "Leak Test Mode", "bool", True, "misc", "manual p.44, p.69 (leak check by default)"),
    _at_field("leak_rate_m3_s", "Acceptable Leak Rate", "number", 0.5 * _HS_ML, "misc", "manual p.44 (default 0.5 mL/min)", min=0.0, max=100 * _HS_ML,
              si_unit="m3/s", show=(1 / _HS_ML, 0.0, "mL/min", 3)),
    _at_field("act_vial_missing", "Vial Missing", "enum", "Skip", "actions", "manual p.44, p.54-55", options=["Skip", "Pause", "Abort"],
              assumption="default not given"),
    _at_field("act_wrong_size", "Wrong Vial Size", "enum", "Skip", "actions", "manual p.44, p.54-55", options=["Continue", "Skip", "Pause", "Abort"],
              assumption="default not given"),
    _at_field("act_leak", "Leak Detected", "enum", "Skip", "actions", "manual p.44, p.54-55", options=["Continue", "Skip", "Pause", "Abort"],
              assumption="default not given"),
    _at_field("act_not_ready", "System Not Ready", "enum", "Wait for Ready", "actions", "manual p.44, p.54-55",
              options=["Continue", "Skip", "Pause", "Abort", "Wait for Ready"], assumption="default not given"),
    _at_field("require_ready", "Require HS ready", "bool", True, "readiness", "manual p.44 (normally require the HS to be ready)"),
    _at_field("cooling_plate_ready", "Cooling plate readiness", "bool", False, "readiness", "manual p.44 (considered separately)"),
]
G_HS_SETTINGS = [
    _at_field("inlet", "Inlet", "enum", "Front Inlet", "config", "manual p.60 (setting for GCs with more than one inlet)", options=["Front Inlet"]),
    _at_field("gas_type", "Gas Type", "enum", "He", "config", "manual p.60 (vial pressurization gas type)", options=["He", "N2", "H2", "Ar"]),
    _at_field("loop_volume_m3", "Loop Volume", "enum", 1e-6, "config", "manual p.41 (Figure 14: 1 mL), p.60", si_unit="m3",
              options=[2.5e-8, 5e-8, 1e-7, 2.5e-7, 5e-7, 1e-6, 3e-6], assumption="available loop sizes"),
    _at_field("transfer_line_type", "Transfer Line Type", "enum", "Fused Silica", "config", "manual p.60", options=["Fused Silica", "DB-ProSteel"]),
    _at_field("transfer_line_d_m", "Transfer Line Diameter", "number", 5.3e-4, "config", "manual p.41 (Figure 14: 0.530 mm), p.60", min=1e-4, max=1e-3,
              si_unit="m", show=(1e3, 0.0, "mm", 3)),
    _at_field("standby_flow_on", "Standby Vial Flow", "bool", True, "config", "manual p.60 (normally leave enabled)"),
    _at_field("standby_flow_m3_s", "Standby Vial Flow rate", "number", 20 * _HS_ML, "config", "manual p.60 (default 20 mL/min)", min=0.0, max=100 * _HS_ML,
              si_unit="m3/s", show=_HS_MLMIN),
    _at_field("clear_oven", "Clear Oven at Startup", "bool", False, "config", "manual p.60"),
    _at_field("cooling_plate", "Cooling plate installed", "bool", False, "config", "manual p.47 (optional accessory)", assumption="not installed"),
]
G_HS_EMF = {"gripper_pads": ("Gripper pads", "gripper moves"), "on_time": ("Headspace on time", "days"), "run_count": ("Headspace run count", "injections"),
            "probe": ("Probe", "injections"), "sample_loop": ("Sample loop", "injections"), "rotor": ("Six-port rotor", "injections"),
            "valve": ("Six-port valve", "injections"), "transfer_line": ("Transfer line", "injections"), "tray_calibration": ("Tray calibration", "days"),
            "vent_tubing": ("Vent tubing", "injections"), "vent_valve": ("Vent valve", "injections")}
G_HS_TESTS = {"user_vial_leak": ("User Vial Leak Test", "Place 5 capped vials made with your sample in tray positions 1-5, then select Start Test. "
                                                         "The HS pressurizes each vial and measures its leak rate."),
              "restriction_decay": ("Restriction and Pressure Decay Test", "Uses the leak test vial in its dedicated location: checks the flow path "
                                                                         "for restrictions and the system for leaks (pressure decay).")}
G_HS_SENSORS = {"vial_flow": ("Vial flow sensor", "flow"), "vial_pressure": ("Vial pressure sensor", "pressure"),
                "aux_pressure": ("Aux pressure sensor", "pressure")}
G_HS_ERRORS = {"vial_flow_offset_m3_s": 1e-5, "vial_pressure_offset_Pa": 1e4, "aux_pressure_offset_Pa": 1e4, "system_leak_m3_s": 1e-5,
               "flow_restriction": 1.0}
G_HS_STATES = ("waiting", "to_oven", "equilibrating", "pressurizing", "extracting", "injecting", "returning", "done")


def _hs_new_device():
    return dict(clock_offset_s=0.0, method=_at_defaults(G_HS_METHOD), settings=_at_defaults(G_HS_SETTINGS), parked=False, racks=True,
                gas_supply=True, gc_ready=True, booting_until=None, power_epoch=0, zones={z: None for z in _HS_ZONES}, sequence=None,
                seq_seq=0, test=None, test_results={}, calibration=None, cal_done=None, zeros={k: 0.0 for k in G_HS_SENSORS}, zero_times={},
                errors={k: 0.0 for k in G_HS_ERRORS}, vial_leaks={}, emf={k: dict(value=0.0, due_on=False, due=10000.0 if t != "days" else 365.0,
                                                                                     warn_on=False, warn=8000.0 if t != "days" else 300.0, since_s=0.0,
                                                                                     resets=[]) for k, (_, t) in G_HS_EMF.items()},
                logs={"event": [], "maintenance": []}, alerts=[], alert_seq=0, prompt=None, on_time_s=0.0, last_t=None, message=None,
                vials={}, transfer=None)


def _hs_log(dev, t, text, kind="event"):
    _at_log(dev["logs"][kind], dict(time=_at_now(dev, t), text=text))


def _hs_zone_K(dev, state, zone, t):
    """Zone temperature at t: first-order approach to the active setpoint since the last change (no thermal model in CHEM-108)."""
    z = dev["zones"][zone]
    amb = state["ambient.temperature_k"]
    if amb is None or z is None:
        return amb
    return z["sp"] + (z["T0"] - z["sp"]) * _at_math.exp(-max(0.0, t - z["t"]) / _HS_TAU_S[zone])


def _hs_zone_target(dev, state, zone):
    """Active setpoint: the method setpoint when powered and switched on, else ambient (the zone cools)."""
    amb = state["ambient.temperature_k"]
    on = {"oven": "oven_on", "loop_valve": "loop_on", "transfer_line": "transfer_on"}[zone]
    sp = state["thermal." + zone + ".setpoint_k"]
    if not state["power.enabled"] or not dev["method"][on] or sp is None:
        return amb
    return sp


def _hs_retarget(dev, state, t):
    """Restart the first-order approach of every zone whose active setpoint changed."""
    for zone in _HS_ZONES:
        target = _hs_zone_target(dev, state, zone)
        z = dev["zones"][zone]
        if target is None:
            dev["zones"][zone] = None
            continue
        if z is None:
            dev["zones"][zone] = dict(T0=state["ambient.temperature_k"], t=t, sp=target)
        elif z["sp"] != target:
            dev["zones"][zone] = dict(T0=_hs_zone_K(dev, state, zone, t), t=t, sp=target)


def _hs_zone_stable_since(dev, zone):
    """Time from which the zone stays within the readiness band (analytic for the first-order approach)."""
    z = dev["zones"][zone]
    d = abs(z["T0"] - z["sp"])
    return z["t"] if d <= _HS_BAND_K else z["t"] + _HS_TAU_S[zone] * _at_math.log(d / _HS_BAND_K)


def _hs_not_ready(dev, state, t):
    out = []
    if not state["power.enabled"]:
        return ["Standby"]
    if dev["booting_until"] is not None and t < dev["booting_until"]:
        out.append("Starting up")
    if dev["parked"]:
        out.append("Tray parked")
    if not dev["racks"]:
        out.append("Vial rack not installed")
    if state["ambient.temperature_k"] is None:
        out.append("Ambient temperature unknown")
    for zone, name, on in (("oven", "Headspace Oven", "oven_on"), ("loop_valve", "Headspace Loop", "loop_on"),
                           ("transfer_line", "Headspace Transfer Line", "transfer_on")):
        if not dev["method"][on]:
            continue
        if state["thermal." + zone + ".setpoint_k"] is None:
            out.append(f"{name} temperature not set")
        elif dev["zones"][zone] is not None and t < _hs_zone_stable_since(dev, zone) + _HS_STABLE_S - 1e-9:
            out.append(f"{name} temperature not ready")
    if not dev["gas_supply"]:
        out.append("Vial pressurization gas not available")
    if not state["gc.connected"]:
        out.append("GC not connected")
    if dev["test"] is not None or dev["calibration"] is not None:
        out.append("Diagnostic or calibration running")
    return out


def _hs_status(dev, state, t):
    if not state["power.enabled"]:
        return dict(text="", color="off", led="off", ready=False, state="STANDBY", reasons=[], message=None, park_lamp=False)
    reasons = _hs_not_ready(dev, state, t)
    errors = [a for a in dev["alerts"] if a["level"] == "error"]
    seq = dev["sequence"]
    if errors:
        st, color = "ERROR", "error"
    elif dev["calibration"] is not None:
        st, color = "CALIBRATION", "maintenance"
    elif dev["test"] is not None:
        st, color = "DIAGNOSTIC MODE", "warn"
    elif seq is not None and seq["paused"]:
        st, color = "SEQUENCE PAUSED", "warn"
    elif seq is not None:
        st, color = "SEQUENCE RUNNING", "run"
    elif not reasons:
        st, color = "READY", "ready"
    else:
        st, color = "NOT READY", "warn"
    led = "red" if errors else ("green" if not reasons else "yellow")
    msg = dev["message"] if dev["message"] and dev["message"]["until_s"] > t else None
    return dict(text="STATUS: " + st, color=color, led=led, ready=not reasons, state=st, reasons=reasons, message=msg["text"] if msg else None,
                park_lamp=dev["parked"])


def _hs_alert(dev, t, level, text, causes=""):
    dev["alert_seq"] += 1
    dev["alerts"].append(dict(id=dev["alert_seq"], level=level, text=text, causes=_g8_textwrap.fill(causes, 50), time=_at_now(dev, t)))
    _hs_log(dev, t, f"{level.capitalize()}: {text}")


def _hs_slot_index(slot):
    group, index = slot.rsplit("_", 1)
    return group + ".occupancy", int(index) - 1


def _hs_move(host, state, sample_id, dst):
    """The tray gantry moves a vial (tray <-> oven): occupancy, vial status and gantry position (as sample.transfer records it)."""
    for key in ("tray.occupancy", "oven.occupancy"):
        state[key] = [None if x == sample_id else x for x in state[key]]
    if dst is not None:
        key, i = _hs_slot_index(dst)
        state[key][i] = sample_id
    host._status(state, sample_id, dst, "transferred")
    state["manipulator.position_slot"] = dst or "home"


def _hs_free_oven(state):
    return next((f"oven_{i + 1:02d}" for i, x in enumerate(state["oven.occupancy"]) if x is None), None)


def _hs_ready_for_injection(dev, state, t):
    return not [r for r in _hs_not_ready(dev, state, t) if r not in ("Diagnostic or calibration running",)] and dev["gc_ready"] and \
        state["gc.carrier_gas_available"]


# ---------------------------------------------------------------- one extraction: vial pressurization and loop fill (p.68-70, p.83-92)
_HS_P_ATM = 101325.0
_HS_T_NTP = 298.15                                                           # NTP of the EPC gas volumes (as the GC; assumption)
_HS_EQ_TAU_S = 180.0                                                         # approach of the headspace to equilibrium without shaking (assumption)
_HS_FAST_FILL_S = 2.0                                                        # Pressure mode fills "as rapidly as possible" (assumption)
_HS_LOOP_MIN_PA = _HS_PSI                                                    # the final vial / loop pressure is not below 1 psi (p.91)
_HS_DEFAULT_LOOP = {1e-5: (2.0 / 3.0, 40 * _HS_PSI / 60)}                   # Default loop fill: final / initial absolute pressure and ramp
_HS_DEFAULT_LOOP_LARGE = (5.0 / 6.0, 20 * _HS_PSI / 60)                      # 20 / 22 mL vials (p.91)


def _hs_errs(dev):
    e, z = dev["errors"], dev["zeros"]
    return dict(vial_flow=e["vial_flow_offset_m3_s"] - z["vial_flow"], vial_pressure=e["vial_pressure_offset_Pa"] - z["vial_pressure"],
                aux_pressure=e["aux_pressure_offset_Pa"] - z["aux_pressure"])


def _hs_extraction(host, dev, state, sample_id, vent=True, remaining=None):
    """One extraction of a vial by the method, the configuration and the EPC (its controllers hold their readings at the setpoints, so the
    uncorrected sensor errors act on the vial):
    - natural pressure after heating: p_nat = p_atm T_oven / T_amb + sum of the analyte partial pressures (the sealed vial was at 1 atm);
    - Flow to Pressure / Pressure: the vial goes to p_atm + fill pressure (vented down if the natural pressure is higher, p.89); Constant
      Volume adds the fill volume (metered by the flow reading): p0 = p_nat + p_atm V_fill / V_hs T_oven / T_NTP; a flow restriction slows the
      fill, a full restriction leaves the natural pressure; a vial / system leak loses pressure during the pressure equilibration;
    - loop fill to the final pressure p1: Default 2/3 (10 mL) or 5/6 (20 / 22 mL) of the initial absolute pressure, lowered so that the
      configured loop is swept once, not below 1 psi (p.91); Custom the final loop pressure (p.84); the aux sensor error moves it;
    - loop contents: the headspace gas mole fraction (diluted by the added gas) at p1 in the installed loop at loop temperature, times the
      swept fraction of the loop: n = c_gas V_loop (T_oven / T_loop) (p1 / p_top) min(1, V_out / V_loop);
    - c_gas approaches the oven equilibrium from the ambient one with tau = 180 s / (1 + shake / 3) (assumption; p.89: shaking raises
      areas) and is scaled by the fraction left by earlier extractions (re-equilibration assumed).
    Returns pressures (gauge), sweep, factor, fill / loop times and, for template vials, the loop masses and the removed fractions."""
    m, s, e = dev["method"], dev["settings"], dev["errors"]
    err = _hs_errs(dev)
    sample = host._samples[sample_id]
    tpl = sample.get("template")
    amb = state["ambient.temperature_k"] or _HS_T_NTP
    T_o = state["thermal.oven.setpoint_k"] or amb
    T_l = state["thermal.loop_valve.setpoint_k"] or T_o
    v_hs = sample["volume_m3"] - (tpl["liquid_volume_m3"] if tpl else 0.0)
    from scripts.basic40_templates import agilent_hs8697 as _hs8697_templates
    eq = _hs8697_templates.equilibrium(tpl, T_o) if tpl else None
    p_nat = _HS_P_ATM * T_o / amb + (sum(x["partial_pressure_pa"] for x in eq["species"].values()) if eq else 0.0)
    flow = max(0.0, m["fill_flow_m3_s"] - err["vial_flow"]) * (1.0 - e["flow_restriction"])
    if m["fill_mode"] == "Constant Volume":
        added = m["fill_volume_m3"] * flow / m["fill_flow_m3_s"]
        p0 = p_nat + _HS_P_ATM * added / v_hs * T_o / _HS_T_NTP
        fill_s = m["fill_volume_m3"] / m["fill_flow_m3_s"]
    else:
        target = _HS_P_ATM + max(0.0, m["fill_pressure_Pa"] - err["vial_pressure"])
        if target <= p_nat:
            p0, fill_s = target, 1.0                                             # vented to the fill pressure (p.89)
        elif flow <= 0.0:
            p0, fill_s = p_nat, 0.0                                              # no gas gets through: the vial keeps its natural pressure
        else:
            p0 = target
            fill_s = (v_hs * (target - p_nat) / _HS_P_ATM * _HS_T_NTP / T_o / flow if m["fill_mode"] == "Flow to Pressure" else
                      _HS_FAST_FILL_S / max(1e-3, 1.0 - e["flow_restriction"]))
    p_top = max(p_nat, p0)
    leak = dev["vial_leaks"].get(sample_id, 0.0) + e["system_leak_m3_s"]
    if leak > 0 and p0 > _HS_P_ATM:
        p0 = max(_HS_P_ATM, p0 - leak * m["pressure_eq_s"] * _HS_P_ATM / v_hs * T_o / _HS_T_NTP)
    v_cfg = s["loop_volume_m3"]
    transfer = getattr(host, "_template_transfer", None)
    v_loop = transfer["loop_volume_m3"] if transfer else v_cfg                  # the installed loop (host) and the configured one (p.89)
    if m["loop_fill_mode"] == "Custom" and m["fill_mode"] != "Constant Volume":
        p1, ramp, loop_eq = _HS_P_ATM + m["final_loop_Pa"], m["loop_ramp_Pa_s"], m["loop_eq_s"]
    else:
        frac, ramp = _HS_DEFAULT_LOOP.get(sample["volume_m3"], _HS_DEFAULT_LOOP_LARGE)
        p1 = max(min(frac * p0, p0 * v_hs / (v_hs + v_cfg * T_o / T_l)), _HS_P_ATM + _HS_LOOP_MIN_PA)
        loop_eq = 3.0
    p1 = min(p0, max(_HS_P_ATM, p1 - err["aux_pressure"]))
    v_out = v_hs * (p0 - p1) / p1 * T_l / T_o
    sweep = min(1.0, v_out / v_loop) if v_loop > 0 else 0.0
    factor = (T_o / T_l) * (p1 / p_top) * sweep
    out = dict(sample_id=sample_id, natural_Pa=p_nat - _HS_P_ATM, fill_Pa=p0 - _HS_P_ATM, final_loop_Pa=p1 - _HS_P_ATM, sweep=sweep,
               factor=factor, fill_s=fill_s, loop_s=((p0 - p1) / ramp if ramp > 0 else 0.0) + loop_eq, loop_volume_m3=v_loop,
               loop_volume_configured_m3=v_cfg, vented=bool(vent), residual_Pa=0.0 if vent else p1 - _HS_P_ATM)
    if eq:
        tau = _HS_EQ_TAU_S / (1.0 + m["shake"] / 3.0)
        decay = _at_math.exp(-(state["method.equilibration_time_s"] or 0.0) / tau)
        try:
            cold = _hs8697_templates.equilibrium(tpl, amb)
        except Exception:
            cold = None
        rem = remaining or {}
        drop = (p0 - _HS_P_ATM) if vent else (p0 - p1)                         # gas leaving the vial: through the loop (and the vent)
        masses, removed = {}, {}
        for sid, it in eq["species"].items():
            c_hot = it["gas_concentration_mol_m3"]
            c_cold = cold["species"][sid]["gas_concentration_mol_m3"] if cold else 0.0
            c = c_hot + (c_cold - c_hot) * decay
            masses[sid] = c * rem.get(sid, 1.0) * v_loop * factor * it["molar_mass_kg_mol"]
            total = tpl["composition_mol_m3"][sid] * tpl["liquid_volume_m3"]
            removed[sid] = min(1.0, c * v_hs * drop / p_top / total) if total > 0 else 0.0
        out.update(approach=1.0 - decay, loop_mass_kg=masses, removed=removed)
    return out


def _hs_public_transfer(x):
    return None if x is None else {k: x[k] for k in ("natural_Pa", "fill_Pa", "final_loop_Pa", "sweep", "factor", "fill_s", "loop_s", "loop_volume_m3",
                                                     "loop_volume_configured_m3", "vented", "residual_Pa") if k in x} | (
        {"approach": x["approach"]} if "approach" in x else {})


# ---------------------------------------------------------------- sequence engine (p.50-58, p.66-74, p.93)
def _hs_job_times(m, x=None):
    """Durations of one vial: pressurization (fill time of the extraction + pressure equilibration + dynamic leak test), loop fill (pressure
    drop / ramp + loop equilibration), injection, purge."""
    press = (x["fill_s"] if x else 0.0) + m["pressure_eq_s"] + (1.2 if m["leak_test"] else 0.0)   # leak test = pressure eq. + 0.02 min (p.69)
    loop = x["loop_s"] if x else (m["loop_eq_s"] if m["loop_fill_mode"] == "Custom" else 3.0)
    purge = 0.0 if m["purge_mode"] == "Off" else (60.0 if m["purge_mode"] == "Default" else m["purge_time_s"])
    return press, loop, m["injection_s"], purge


def _hs_seq_action(host, dev, state, t, job, issue, text):
    """Apply the method's sequence action for an issue: Continue / Skip / Pause / Abort / Wait for Ready (p.54-55); returns continue?"""
    action = dev["method"][{"vial_missing": "act_vial_missing", "wrong_size": "act_wrong_size", "leak": "act_leak",
                            "not_ready": "act_not_ready"}[issue]]
    _hs_log(dev, t, f"{text}: {action}")
    seq = dev["sequence"]
    if action == "Continue":
        return True
    if action == "Wait for Ready":
        job["waiting"] = True
        return False
    if action == "Pause":
        seq["paused"] = True
        _hs_alert(dev, t, "warning", f"Sequence paused: {text}", "Resolve the issue, then resume the sequence from the browser interface or "
                                                                 "data system.")
        job["waiting"] = True
        return False
    if action == "Skip":
        _hs_finish_job(host, dev, state, t, job, "skipped")
        return False
    _hs_abort(host, dev, state, t, text)
    return False


def _hs_finish_job(host, dev, state, t, job, how):
    if job["sample_id"] is not None and job["slot"] is not None and job["slot"].startswith("oven"):
        _hs_move(host, state, job["sample_id"], job["tray_slot"])
        dev["emf"]["gripper_pads"]["value"] += 1
    job.update(state="done", result=how, end=_at_now(dev, t))


def _hs_abort(host, dev, state, t, why):
    """Abort: stop all vial processing and return every vial to the tray (p.55, p.57)."""
    seq = dev["sequence"]
    for job in seq["jobs"]:
        if job["state"] not in ("done", "waiting"):
            _hs_finish_job(host, dev, state, t, job, "aborted")
        elif job["state"] == "waiting":
            job.update(state="done", result="not run")
    _hs_log(dev, t, f"Sequence aborted: {why}")
    dev["sequence"] = None


def _hs_inject(host, dev, state, t, job):
    """The injection: the CHEM-108 measure path (chromatogram, injection log, vial status) through the panel principal."""
    host._state = state
    r = host._panel_session._invoke_once("measure", {"oven_slot": job["slot"]}, f"__hs_seq_{dev['seq_seq']}_{job['index']}_{job['done']}")
    state.clear()
    state.update(_hs8697_copy.deepcopy(host._state))
    ok = r["status"] == "succeeded"
    _hs_log(dev, t, f"Injection vial {job['tray_slot']}: " + ("GC run started" if ok else f"failed ({r['error']['code']})"))
    for k in ("run_count", "probe", "sample_loop", "rotor", "valve", "transfer_line", "vent_tubing", "vent_valve"):
        dev["emf"][k]["value"] += 1
    return ok


def _hs_sequence_tick(host, dev, state, t):
    seq = dev["sequence"]
    if seq is None:
        return
    m = dev["method"]
    press, loop, inj, purge = _hs_job_times(m)
    eq = state["method.equilibration_time_s"] or 0.0
    for job in seq["jobs"]:
        if job["state"] == "done":
            continue
        press, loop, inj, purge = _hs_job_times(m, _hs_extraction(host, dev, state, job["sample_id"]) if job.get("sample_id") is not None and
                                                job["state"] in ("equilibrating", "pressurizing") else None)
        if job["state"] == "waiting":
            if seq["paused"] or dev["parked"] or t < job["load_at"]:
                continue                                                         # parked: vials cannot enter or leave the oven (p.35)
            job["waiting"] = False
            sample = state["tray.occupancy"][int(job["tray_slot"][-2:]) - 1]
            if sample is None:
                _hs_seq_action(host, dev, state, t, job, "vial_missing", f"Vial missing at {job['tray_slot']}")
                if dev["sequence"] is None:
                    return
                continue
            vol = host._samples[sample]["volume_m3"]
            if not (vol == m["vial_size_m3"] or {vol, m["vial_size_m3"]} == {2e-5, 2.2e-5}):     # 20 and 22 mL are not distinguished (p.54)
                if not _hs_seq_action(host, dev, state, t, job, "wrong_size", f"Wrong vial size at {job['tray_slot']}"):
                    if dev["sequence"] is None:
                        return
                    continue
            oven = _hs_free_oven(state)
            if oven is None or (m["oven_on"] and "Headspace Oven temperature not ready" in _hs_not_ready(dev, state, t)):
                continue                                                         # the HS loads vials only at oven temperature (p.44)
            job.update(sample_id=sample, slot=oven, state="equilibrating", until=t + _HS_MOVE_S + eq, start=_at_now(dev, t))
            _hs_move(host, state, sample, oven)
            dev["emf"]["gripper_pads"]["value"] += 1
            continue
        if seq["paused"] and job.get("waiting"):
            continue
        if t < job["until"] - 1e-9:
            continue
        if job["state"] == "equilibrating":
            if not (_hs_ready_for_injection(dev, state, t) or not m["require_ready"]):
                if not _hs_seq_action(host, dev, state, t, job, "not_ready", "System not ready"):
                    if dev["sequence"] is None:
                        return
                    continue
            job.update(state="pressurizing", until=t + press)
        elif job["state"] == "pressurizing":
            leak = dev["vial_leaks"].get(job["sample_id"], 0.0) + dev["errors"]["system_leak_m3_s"]
            job["leak_rate_m3_s"] = leak
            if m["leak_test"] and leak > m["leak_rate_m3_s"]:
                _hs_alert(dev, t, "warning", f"Leak detected: vial {job['tray_slot']}", f"Leak rate {leak / _HS_ML:.3f} mL/min above the acceptable "
                                                                                        f"{m['leak_rate_m3_s'] / _HS_ML:.3f} mL/min.")
                if not _hs_seq_action(host, dev, state, t, job, "leak", f"Leak detected at {job['tray_slot']}"):
                    if dev["sequence"] is None:
                        return
                    continue
            job.update(state="extracting", until=t + loop)
        elif job["state"] == "extracting":
            job.update(state="injecting", until=t + inj)
            last = job["done"] + 1 >= job["extractions"]
            vent = m["extraction_mode"] == "Single" or (m["vent_after_last"] if last else m["vent_between"])   # p.85-86
            vial = dev.setdefault("vials", {}).setdefault(job["sample_id"], {"remaining": {}})
            x = _hs_extraction(host, dev, state, job["sample_id"], vent=vent, remaining=vial["remaining"])
            dev["transfer"] = _hs_public_transfer(x)
            if "loop_mass_kg" in x:
                vial["remaining"] = {sid: vial["remaining"].get(sid, 1.0) * (1.0 - r) for sid, r in x["removed"].items()}
                trap = job.setdefault("trap", {})                                  # Concentrated: extractions accumulate in the inlet trap (p.92)
                for sid, v in x["loop_mass_kg"].items():
                    trap[sid] = trap.get(sid, 0.0) + v
            if m["extraction_mode"] != "Concentrated" or last:
                host._pending_loop = job.pop("trap", None)
                try:
                    _hs_inject(host, dev, state, t, job)
                finally:
                    host._pending_loop = None
            job["done"] += 1
        elif job["state"] == "injecting":
            if job["done"] < job["extractions"]:
                wait = 0.0 if m["extraction_mode"] == "Concentrated" else m["gc_cycle_s"]
                job.update(state="pressurizing", until=t + max(0.0, wait - inj) + 0.0)
            else:
                job.update(state="returning", until=t + _HS_MOVE_S + purge)
        elif job["state"] == "returning":
            _hs_finish_job(host, dev, state, t, job, "completed")
    if dev["sequence"] is not None and all(j["state"] == "done" for j in seq["jobs"]):
        _hs_log(dev, t, "Sequence completed")
        dev["sequence"] = None


def _hs_tick(host, dev, state, t0, t1):
    if not state["power.enabled"]:
        return
    dev["on_time_s"] += t1 - t0
    if dev["booting_until"] is not None and t1 >= dev["booting_until"] - 1e-9:
        dev["booting_until"] = None
        _hs_log(dev, t1, "Headspace ready for operation (start-up complete)")
    _hs_sequence_tick(host, dev, state, t1)
    if dev["test"] is not None and t1 >= dev["test"]["until_s"] - 1e-9:
        _hs_finish_test(host, dev, state, t1)
    if dev["calibration"] is not None and dev["calibration"]["step"] == "running" and t1 >= dev["calibration"]["until_s"] - 1e-9:
        dev["calibration"] = None
        dev["cal_done"] = _at_now(dev, t1)
        c = dev["emf"]["tray_calibration"]
        c.update(value=0.0, since_s=t1 + dev["clock_offset_s"], resets=(c["resets"] + [dict(time=_at_now(dev, t1), how="Maintenance Reset")])[-20:])
        _hs_log(dev, t1, "System and tray calibration (Factory) completed", "maintenance")
    for k, (part, typ) in G_HS_EMF.items():
        c = dev["emf"][k]
        if typ == "days":
            c["value"] = dev["on_time_s"] / 86400.0 if k == "on_time" else max(0.0, (t1 + dev["clock_offset_s"] - c["since_s"]) / 86400.0)
        level = "due" if c["due_on"] and c["value"] >= c["due"] else "warning" if c["warn_on"] and c["value"] >= c["warn"] else "ok"
        if level != c.get("level", "ok") and level != "ok":
            _hs_log(dev, t1, f"Headspace: {part} " + ("service due" if level == "due" else "service warning"), "maintenance")
        c["level"] = level


def _hs_finish_test(host, dev, state, t):
    test, dev["test"] = dev["test"], None
    if test["id"] == "user_vial_leak":
        rates = [dev["vial_leaks"].get(s, 0.0) for s in test["vials"]]
        mean = sum(rates) / len(rates)
        sd = _at_math.sqrt(sum((r - mean) ** 2 for r in rates) / (len(rates) - 1)) if len(rates) > 1 else 0.0
        ok = all(r <= dev["method"]["leak_rate_m3_s"] for r in rates)
        values = [[f"Vial {i + 1}", f"{r / _HS_ML:.3f} mL/min"] for i, r in enumerate(rates)]
        values.append(["Suggested leak rate threshold", f"{(mean + 3 * sd) / _HS_ML:.3f} mL/min"])
        dev["test_results"][test["id"]] = dict(result="Passed" if ok else "Failed", values=values, time=_at_now(dev, t),
                                               suggested_m3_s=mean + 3 * sd)
    else:
        e = dev["errors"]
        ok = e["system_leak_m3_s"] < 0.1 * _HS_ML and e["flow_restriction"] < 0.2 and dev["gas_supply"]
        dev["test_results"][test["id"]] = dict(result="Passed" if ok else "Failed", values=[
            ["Pressure decay", f"{e['system_leak_m3_s'] / _HS_ML * 2.0:.3f} psi/min"], ["Restriction", f"{100 * e['flow_restriction']:.0f} %"]],
            time=_at_now(dev, t))
    _hs_log(dev, t, f"{G_HS_TESTS[test['id']][0]}: {dev['test_results'][test['id']]['result']}")


# ---------------------------------------------------------------- terminal commands: rt (runtime), dev, state, args, t
def _hs_gate(dev, state, t, idle=False):
    _at_need(state["power.enabled"], "PRECONDITION_FAILED")
    _at_need(dev["booting_until"] is None or t >= dev["booting_until"], "BUSY")
    if idle:
        _at_need(dev["sequence"] is None and dev["test"] is None and dev["calibration"] is None, "BUSY")


def _hs_c_method(rt, dev, state, a, t):
    _hs_gate(dev, state, t)
    table = [f for f in G_HS_METHOD if not f.get("write_via")]
    new = _at_apply(table, dev["method"], a["settings"])
    dev["method"] = new
    _hs_retarget(dev, state, t)
    return {"settings": {k: new[k] for k in a["settings"]}}


def _hs_c_settings(rt, dev, state, a, t):
    _hs_gate(dev, state, t)
    if any(k in a["settings"] for k in ("loop_volume_m3", "gas_type")):
        _at_need(dev["sequence"] is None, "BUSY")                                 # one loop size and gas type per sequence (p.94)
    new = _at_apply(G_HS_SETTINGS, dev["settings"], a["settings"])
    dev["settings"] = new
    _hs_log(dev, t, "Headspace configuration changed")
    return {"settings": {k: new[k] for k in a["settings"]}}


def _hs_c_park(rt, dev, state, a, t):
    _at_need(state["power.enabled"], "PRECONDITION_FAILED")
    _at_need(dev["calibration"] is None and dev["test"] is None, "BUSY")
    dev["parked"] = bool(a["parked"])
    _hs_log(dev, t, "Tray parked" if dev["parked"] else "Tray unparked")
    if dev["sequence"] is not None:
        _hs_log(dev, t, "Sequence paused (tray parked)" if dev["parked"] else "Sequence vial moves resumed")
    return {"parked": dev["parked"]}


def _hs_c_seq_start(rt, dev, state, a, t):
    _hs_gate(dev, state, t, idle=True)
    _at_need(not dev["parked"], "PARKED")                                        # a sequence cannot start while parked (p.35)
    _at_need(state["method.equilibration_time_s"] is not None, "PRECONDITION_FAILED")
    m = dev["method"]
    press, loop, inj, purge = _hs_job_times(m)
    eq = state["method.equilibration_time_s"]
    n = int(a.get("injections_per_vial", 1))
    extractions = m["extractions"] if m["extraction_mode"] == "Concentrated" else (n if m["extraction_mode"] == "Multiple" else 1)
    jobs = []
    lead = _HS_MOVE_S + eq + press + loop                                          # from oven loading to the first injection
    for i, pos in enumerate(a["vials"]):
        load_at = t + i * m["gc_cycle_s"] * (extractions if m["extraction_mode"] == "Multiple" else 1)
        for k in range(1 if m["extraction_mode"] != "Single" else n):
            jobs.append(dict(index=len(jobs), tray_slot=f"tray_{pos:02d}", sample_id=None, slot=None, state="waiting", load_at=load_at +
                             k * m["gc_cycle_s"], until=None, extractions=extractions, done=0, result=None, start=None, end=None, leak_rate_m3_s=None))
    dev["seq_seq"] += 1
    dev["sequence"] = dict(id=dev["seq_seq"], started=_at_now(dev, t), jobs=jobs, paused=False, lead_s=lead)
    for k in ("probe", "sample_loop"):
        c = dev["emf"][k]
        if c["warn_on"] and c["value"] + len(jobs) * extractions >= c["warn"]:
            _hs_alert(dev, t, "warning", f"EMF: {G_HS_EMF[k][0]} service warning during this sequence", "The sequence still runs (p.98).")
    _hs_log(dev, t, f"Sequence {dev['seq_seq']} started ({len(jobs)} vial entries)")
    return {"sequence": dev["seq_seq"], "entries": len(jobs)}


def _hs_c_seq_pause(rt, dev, state, a, t):
    _at_need(dev["sequence"] is not None, "PRECONDITION_FAILED")
    dev["sequence"]["paused"] = bool(a["paused"])
    if not a["paused"]:
        dev["alerts"] = [x for x in dev["alerts"] if not x["text"].startswith("Sequence paused")]
        for job in dev["sequence"]["jobs"]:
            job["waiting"] = False
    _hs_log(dev, t, "Sequence paused" if a["paused"] else "Sequence resumed")
    return {"paused": bool(a["paused"])}


def _hs_c_gc_run(rt, dev, state, a, t):
    """Prep Run / Start on the host GC touchscreen (the HS view keeps the GC's run controls, GC manual p.31): forwarded to the connected GC
    asset through the trusted host's hook; the GC answers with its own response."""
    _at_need(state["gc.connected"] and rt._gc_control is not None, "GC_NOT_CONNECTED")
    r = rt._gc_control(a["action"], rt._caller or "__panel__")
    ok = isinstance(r, dict) and r.get("status") in ("succeeded", "accepted")
    if not ok:
        raise _ATReject("GC_REFUSED")
    _hs_log(dev, t, {"prep": "Prep Run", "start": "Start"}[a["action"]] + " sent to the GC")
    return {"action": a["action"], "gc_status": r["status"]}


def _hs_c_stop(rt, dev, state, a, t):
    """Touchscreen Stop: stop the run (the current vial's run ends, the sequence goes on) or stop the sequence (abort, vials back to the
    tray), p.57."""
    _at_need(dev["sequence"] is not None, "PRECONDITION_FAILED")
    if a["scope"] == "sequence":
        _hs_abort(rt, dev, state, t, "stopped at the touchscreen")
        return {"stopped": "sequence"}
    _hs_log(dev, t, "Run stopped at the touchscreen")
    return {"stopped": "run"}


def _hs_c_test_start(rt, dev, state, a, t):
    _hs_gate(dev, state, t, idle=True)
    test = a["test"]
    if test == "user_vial_leak":
        vials = state["tray.occupancy"][:5]
        _at_need(all(v is not None for v in vials), "VIALS_MISSING")
        dev["test"] = dict(id=test, vials=list(vials), started=_at_now(dev, t), until_s=t + 5 * _HS_TEST_VIAL_S)
    else:
        dev["test"] = dict(id=test, started=_at_now(dev, t), until_s=t + 120.0)
    _hs_log(dev, t, f"{G_HS_TESTS[test][0]} started")
    return {"test": test, "duration_s": dev["test"]["until_s"] - t}


def _hs_c_test_cancel(rt, dev, state, a, t):
    _at_need(dev["test"] is not None, "PRECONDITION_FAILED")
    test, dev["test"] = dev["test"], None
    dev["test_results"][test["id"]] = dict(result="Aborted", values=[], time=_at_now(dev, t))
    _hs_log(dev, t, f"{G_HS_TESTS[test['id']][0]} aborted")
    return {"aborted": test["id"]}


def _hs_c_tray_cal(rt, dev, state, a, t):
    """Start System and Tray Calibration (Factory): oven and tray empty, calibration post and leak test vial in place (p.61)."""
    _hs_gate(dev, state, t, idle=True)
    if a["action"] == "start":
        _at_need(not any(state["tray.occupancy"]) and not any(state["oven.occupancy"]), "VIALS_PRESENT")
        dev["calibration"] = dict(step="confirm", started=_at_now(dev, t))
        return {"step": "confirm"}
    _at_need(False, "PRECONDITION_FAILED")


def _hs_c_tray_cal_step(rt, dev, state, a, t):
    c = dev["calibration"]
    _at_need(c is not None, "PRECONDITION_FAILED")
    if a["action"] == "cancel":
        dev["calibration"] = None
        _hs_log(dev, t, "Tray calibration cancelled", "maintenance")
        return {"step": None}
    _at_need(c["step"] == "confirm", "PRECONDITION_FAILED")
    c.update(step="running", until_s=t + 300.0)
    _hs_log(dev, t, "System and tray calibration (Factory) started", "maintenance")
    return {"step": "running"}


def _hs_c_zero(rt, dev, state, a, t):
    _hs_gate(dev, state, t, idle=True)
    s = a["sensor"]
    key = {"vial_flow": "vial_flow_offset_m3_s", "vial_pressure": "vial_pressure_offset_Pa", "aux_pressure": "aux_pressure_offset_Pa"}[s]
    if a["action"] == "reset":
        dev["zeros"][s] = 0.0
        dev["zero_times"].pop(s, None)
        _hs_log(dev, t, f"{G_HS_SENSORS[s][0]} reset to factory calibration", "maintenance")
        return {"sensor": s, "action": "reset"}
    if G_HS_SENSORS[s][1] == "flow":
        _at_need(dev["gas_supply"] and dev["settings"]["standby_flow_on"], "GAS_NOT_FLOWING")
    else:
        _at_need(not dev["gas_supply"], "SUPPLY_CONNECTED")
    dev["zeros"][s] = dev["errors"][key]
    dev["zero_times"][s] = _at_now(dev, t)
    _hs_log(dev, t, f"{G_HS_SENSORS[s][0]} zeroed", "maintenance")
    return {"sensor": s, "action": "zero"}


def _hs_c_factory_reset(rt, dev, state, a, t):
    _hs_gate(dev, state, t, idle=True)
    keep = dict(clock_offset_s=dev["clock_offset_s"], parked=dev["parked"], racks=dev["racks"], gas_supply=dev["gas_supply"], gc_ready=dev["gc_ready"],
                vial_leaks=dev["vial_leaks"], errors=dev["errors"], power_epoch=dev["power_epoch"] + 1, zones=dev["zones"], on_time_s=dev["on_time_s"])
    dev.clear()
    dev.update(_hs_new_device(), **keep)
    dev["booting_until"] = t + _HS_BOOT_S
    _hs_log(dev, t, "Factory reset (logs, configuration, calibrations and EMF cleared); rebooting")
    return {"reset": True}


def _hs_c_emf_configure(rt, dev, state, a, t):
    _hs_gate(dev, state, t)
    c = dev["emf"][a["counter"]]
    new = dict(c, **{k: a[k] for k in ("due_on", "due", "warn_on", "warn") if k in a})
    _at_need(new["due"] > new["warn"], "INVALID_ARGUMENT")
    dev["emf"][a["counter"]] = new
    _hs_log(dev, t, f"Headspace: {G_HS_EMF[a['counter']][0]} limits changed", "maintenance")
    return {"counter": a["counter"]}


def _hs_c_emf_reset(rt, dev, state, a, t):
    _hs_gate(dev, state, t)
    c = dev["emf"][a["counter"]]
    c.update(value=0.0, since_s=t + dev["clock_offset_s"], level="ok", resets=(c["resets"] + [dict(time=_at_now(dev, t), how="User Reset")])[-20:])
    _hs_log(dev, t, f"Headspace: {G_HS_EMF[a['counter']][0]} counter reset (User Reset)", "maintenance")
    return {"counter": a["counter"], "value": 0.0}


def _hs_c_resolve(rt, dev, state, a, t):
    _at_need(any(x["id"] == a["alert_id"] for x in dev["alerts"]), "NOT_FOUND")
    dev["alerts"] = [x for x in dev["alerts"] if x["id"] != a["alert_id"]]
    return {"cleared": a["alert_id"]}


def _hs_c_sensor_errors(rt, dev, state, a, t):
    errs = a["errors"]
    _at_need(all(k in G_HS_ERRORS and 0 <= v <= G_HS_ERRORS[k] for k, v in errs.items() if k in ("system_leak_m3_s", "flow_restriction")), "INVALID_ARGUMENT")
    _at_need(all(k in G_HS_ERRORS and abs(v) <= G_HS_ERRORS[k] for k, v in errs.items()), "INVALID_ARGUMENT")
    dev["errors"].update({k: float(v) for k, v in errs.items()})
    return {"errors": dict(dev["errors"])}


def _hs_c_clock(rt, dev, state, a, t):
    _hs_gate(dev, state, t)
    try:
        target = _at_dt.datetime.fromisoformat(a["date"] + "T" + a["time"])
    except ValueError:
        raise _ATReject("INVALID_ARGUMENT") from None
    dev["clock_offset_s"] = (target - _AT_EPOCH).total_seconds() - t
    return {"datetime": _at_now(dev, t)}


_HS_COMMANDS = {
    "hs.method.configure": _hs_c_method, "hs.settings.configure": _hs_c_settings, "tray.park": _hs_c_park, "sequence.start": _hs_c_seq_start,
    "sequence.pause": _hs_c_seq_pause, "sequence.stop": _hs_c_stop, "diagnostics.start_test": _hs_c_test_start,
    "diagnostics.cancel_test": _hs_c_test_cancel, "calibration.tray": _hs_c_tray_cal, "calibration.tray_step": _hs_c_tray_cal_step,
    "calibration.zero": _hs_c_zero, "service.factory_reset": _hs_c_factory_reset, "emf.configure": _hs_c_emf_configure, "emf.reset": _hs_c_emf_reset,
    "diagnostics.resolve": _hs_c_resolve, "clock.set": _hs_c_clock, "sensor.configure_error": _hs_c_sensor_errors, "gc.run_control": _hs_c_gc_run,
}


# ---------------------------------------------------------------- contract metadata of the terminal
def _hs_slots():
    def slot(origin, schema, components, visibility="agent"):
        return {"value_schema": schema, "unit": "1", "origin": origin, "component_ids": components, "visibility": visibility, "access": "read_only",
                "write_via": None}
    nullable = lambda s: {"anyOf": [s, {"type": "null"}]}  # noqa: E731
    return {"hs.status": slot("derived", _AT_ANY, ["status_panel"]), "hs.method": slot("setpoint", _AT_ANY, ["oven", "loop_valve"]),
            "hs.zones": slot("derived", _AT_ANY, ["oven", "loop_valve", "transfer_line"]), "hs.settings": slot("setpoint", _AT_ANY, ["chassis"]),
            "hs.tray": slot("derived", _AT_ANY, ["tray_support", "status_panel"]), "hs.sequence": slot("derived", nullable(_AT_ANY), ["manipulator_arm"]),
            "hs.diagnostics": slot("derived", _AT_ANY, ["loop_valve"]), "hs.calibration": slot("derived", _AT_ANY, ["gripper", "loop_valve"]),
            "hs.emf": slot("derived", _AT_ANY, ["status_panel"]), "hs.logs": slot("derived", _AT_ANY, ["status_panel"]),
            "hs.alerts": slot("derived", _AT_LIST, ["status_panel"]), "hs.system": slot("derived", _AT_ANY, ["chassis"]),
            "hs.device_datetime": slot("derived", _AT_STR, ["chassis"]),
            "sensor.errors": slot("process_truth", _AT_ANY, ["loop_valve"], "evaluator"), "hs.host": slot("scene", _AT_ANY, ["chassis"], "evaluator"),
            "hs.transfer": slot("derived", nullable(_AT_ANY), ["oven", "loop_valve"])}


_HS_E = ["hs.status", "hs.sequence", "hs.logs"]
_HS_CAPDEFS = {
    "hs.method.configure": ({"settings": _at_settings_schema([f for f in G_HS_METHOD if not f.get("write_via")])}, (), {"settings": _AT_ANY},
                            {"settings": {"shake": 3}}, "GC触摸屏Method > Headspace：顶空方法参数（温度开关、冷却板、进样时间、GC周期、样品瓶尺寸、振荡、加压方式、定量环充填、"
                                                        "萃取方式、排气与吹扫、动态检漏、序列动作、就绪），修改立即生效（p.40-44、p.82-86）；炉/环/传输线温度与平衡时间经原有能力",
                            ["hs.method", "hs.zones", "hs.status"], "configuration"),
    "hs.settings.configure": ({"settings": _at_settings_schema(G_HS_SETTINGS)}, (), {"settings": _AT_ANY}, {"settings": {"loop_volume_m3": 1e-6}},
                              "Settings > Configuration > Headspace：进样口、加压气类型、定量环体积、传输线类型与内径、待机瓶流量、开机清空炉（p.60），Apply保存",
                              ["hs.settings", "hs.logs"], "configuration"),
    "tray.park": ({"parked": _AT_BOOL}, (), {"parked": _AT_BOOL}, {"parked": True}, "Park按钮：托盘龙门移到停放位（灯亮，HS未就绪，序列中样品瓶不能进出炉），再按取消停放"
                                                                                 "（p.16、p.35）", ["hs.tray", "hs.status", "hs.logs"], "device_command"),
    "sequence.start": ({"vials": {"type": "array", "items": _at_num("1", 1, 48, True), "minItems": 1, "maxItems": 120},
                        "injections_per_vial": _at_num("1", 1, 20, True)}, ("injections_per_vial",), {"sequence": _at_num("1", 1, None, True),
                                                                                                       "entries": _at_num("1", 1, None, True)},
                       {"vials": [1, 2, 3]}, "浏览器界面/数据系统启动顶空序列（触摸屏不能建序列，p.49）：按GC周期安排进炉、平衡、加压与检漏、充环、进样（经measure）与回盘",
                       _HS_E + ["tray.occupancy", "oven.occupancy", "vial.status", "manipulator.position_slot"], "device_command"),
    "sequence.pause": ({"paused": _AT_BOOL}, (), {"paused": _AT_BOOL}, {"paused": True}, "浏览器界面/数据系统暂停或继续序列（p.57）", _HS_E, "device_command"),
    "sequence.stop": ({"scope": {"type": "string", "enum": ["run", "sequence"]}}, (), {"stopped": _AT_STR}, {"scope": "sequence"},
                      "GC触摸屏Stop：停止当前运行或停止序列（中止，样品瓶回盘），p.57", _HS_E + ["tray.occupancy", "oven.occupancy", "vial.status"],
                      "device_command"),
    "diagnostics.start_test": ({"test": {"type": "string", "enum": list(G_HS_TESTS)}}, (), {"test": _AT_STR, "duration_s": _at_num("s", 0)},
                               {"test": "restriction_decay"}, "Diagnostics > Diagnostic Tests > Headspace：User Vial Leak Test（托盘1-5号5个样品瓶）或"
                                                              "Restriction and Pressure Decay Test（p.34、p.63）", ["hs.diagnostics", "hs.status", "hs.logs"],
                               "device_command"),
    "diagnostics.cancel_test": ({}, (), {"aborted": _AT_STR}, {}, "中止顶空诊断测试", ["hs.diagnostics", "hs.status", "hs.logs"], "device_command"),
    "calibration.tray": ({"action": {"type": "string", "enum": ["start"]}}, (), {"step": _AT_STR}, {"action": "start"},
                         "Settings > Calibration > Headspace：Start System and Tray Calibration（炉与托盘须为空，p.61）", ["hs.calibration", "hs.status"],
                         "device_command"),
    "calibration.tray_step": ({"action": {"type": "string", "enum": ["factory", "cancel"]}}, (), {"step": {"anyOf": [_AT_STR, {"type": "null"}]}},
                              {"action": "factory"}, "托盘校准提示：选择Factory开始（约5 min，假设）或取消", ["hs.calibration", "hs.status", "hs.emf", "hs.logs"],
                              "device_command"),
    "calibration.zero": ({"sensor": {"type": "string", "enum": list(G_HS_SENSORS)}, "action": {"type": "string", "enum": ["zero", "reset"]}}, (),
                         {"sensor": _AT_STR, "action": _AT_STR}, {"sensor": "vial_flow", "action": "zero"},
                         "EPC校准：流量传感器在气体流动时、压力传感器在断开气源时置零；Reset恢复出厂校准（p.62）", ["hs.calibration", "hs.status", "hs.logs"],
                         "device_command"),
    "service.factory_reset": ({}, (), {"reset": _AT_BOOL}, {}, "Service Mode > Headspace > Factory Reset：清除日志、配置、校准与EMF并重启（p.64）",
                              ["hs.settings", "hs.method", "hs.emf", "hs.logs", "hs.calibration", "hs.system", "hs.status"], "device_command"),
    "emf.configure": ({"counter": {"type": "string", "enum": list(G_HS_EMF)}, "due_on": _AT_BOOL, "due": _at_num("1", 0, 1e7), "warn_on": _AT_BOOL,
                       "warn": _at_num("1", 0, 1e7)}, ("due_on", "due", "warn_on", "warn"), {"counter": _AT_STR},
                      {"counter": "probe", "warn_on": True, "warn": 500}, "Maintenance > Headspace：EMF计数限值与启用（p.98）", ["hs.emf", "hs.logs"],
                      "configuration"),
    "emf.reset": ({"counter": {"type": "string", "enum": list(G_HS_EMF)}}, (), {"counter": _AT_STR, "value": _at_num("1", 0)}, {"counter": "probe"},
                  "Maintenance > Headspace：复位EMF计数（p.98）", ["hs.emf", "hs.logs"], "device_command"),
    "diagnostics.resolve": ({"alert_id": _at_num("1", 1, None, True)}, (), {"cleared": _at_num("1", 1, None, True)}, {"alert_id": 1},
                            "确认并清除顶空警告", ["hs.alerts", "hs.status"], "device_command"),
    "clock.set": ({"date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                   "time": {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?$"}}, (), {"datetime": _AT_STR},
                  {"date": "2026-10-19", "time": "09:30"}, "设备日期与时间（随GC的系统时间设定；时间型EMF计数用）", ["hs.device_datetime", "hs.emf"], "configuration"),
    "sensor.configure_error": ({"errors": {"type": "object", "properties": {k: _at_num("1" if k == "flow_restriction" else "Pa" if k.endswith("_Pa") else "m3/s",
                                                                                     -v, v) for k, v in G_HS_ERRORS.items()},
                                           "additionalProperties": False, "required": [], "minProperties": 1}}, (), {"errors": _AT_ANY},
                               {"errors": {"system_leak_m3_s": 1e-8}}, "评测者：顶空传感器误差参数（流量/压力零点偏移、系统泄漏、流路堵塞），默认0（理想）；"
                                                                        "EPC把读数保持在设定值，误差作用于样品瓶的实际加压、充环压力与流量",
                               ["sensor.errors", "hs.diagnostics", "hs.status"], "configuration"),
    "gc.run_control": ({"action": {"type": "string", "enum": ["prep", "start"]}}, (), {"action": _AT_STR, "gc_status": _AT_STR}, {"action": "prep"},
                       "主机GC触摸屏的Prep Run / Start（HS页面也保留GC运行控制，GC手册p.31）：经受信宿主钩子转发给已连接的GC资产，按GC自己的响应",
                       ["hs.logs"], "device_command"),
}
_HS_TERMINAL = tuple(_HS_CAPDEFS)
_HS_ERROR_TEXT = {"BUSY": "A sequence, test, calibration or start-up is in progress.", "PARKED": "A sequence cannot start while the tray is parked.",
                  "VIALS_MISSING": "Place 5 capped vials in tray positions 1-5.", "VIALS_PRESENT": "Empty the vial oven and the tray first.",
                  "GAS_NOT_FLOWING": "Flow sensors are zeroed with the gas connected and flowing.",
                  "SUPPLY_CONNECTED": "Pressure sensors are zeroed with the supply line disconnected.", "NOT_FOUND": "The entry does not exist.",
                  "GC_NOT_CONNECTED": "The GC does not respond (not connected).", "GC_REFUSED": "The GC refused the command (see the GC status)."}
_HS_LIMITS = [
    "8697 terminal: headspace method, heated zones (first-order approach to setpoint, time constants assumed), park / unpark, a sequence engine "
    "(GC-cycle scheduling, equilibration, pressurization with dynamic leak check, loop fill, injection through measure, return and purge, "
    "sequence actions, Stop prompt), diagnostic tests, tray and EPC calibration, service factory reset, EMF counters and logs follow the 8697 "
    "operation manual as a minimal model, not Agilent firmware. Sensor errors and vial leak rates are evaluator / host settings, 0 by default.",
    "The headspace pages belong to the host GC touchscreen: here they are rendered by this asset's panel (display gc_touch). Sequences are "
    "created in the browser interface or a data system (sequence.start stands in for them); the GC run itself belongs to the GC asset: Prep Run "
    "/ Start on the HS view are forwarded to it through the trusted host's register_gc_control hook (gc.run_control).",
    "Headspace device model (_hs_extraction): natural vial pressure from heating and the analyte partial pressures (solvent vapour not included), "
    "fill mode / pressure / flow / volume, pressure equilibration with the vial leak, Default / Custom loop fill to the final pressure with the "
    "loop sweep (configured vs installed loop), loop temperature, EPC sensor errors (the controllers hold their readings at the setpoints), "
    "approach to equilibrium with shaking (time constant 180 s / (1 + shake / 3), assumption), extraction depletion with venting (re-equilibration "
    "assumed) and Concentrated extractions; the injected template amount follows it; fixture vials keep their fixed synthetic shape.  The fill "
    "and loop fill times enter the sequence timing.  Purge flow only sets the purge step (carryover is not modelled).",
    "Not simulated: barcode reader, parameter increment (browser interface only), cooling plate unless configured, XL tray, MS timing, real "
    "flow dynamics (the EPC values are setpoints and the model above), carryover between vials.",
]


def _hs_caps():
    caps = {}
    for name, (inputs, optional, outputs, example, desc, effects, kind) in _HS_CAPDEFS.items():
        caps[name] = {"input_schema": _at_obj(inputs, optional), "output_schema": _at_obj(outputs), "example_arguments": [example],
                      "state_ids": list(dict.fromkeys(effects)), "component_ids": ["status_panel"], "preconditions": [
                          "Power on and start-up finished; sequences, tests and calibrations exclude each other as on the instrument."],
                      "scene_required": False, "availability": "implemented", "visibility": "evaluator" if name == "sensor.configure_error" else "agent",
                      "kind": kind, "description_zh": desc,
                      "invocation": "AgilentHS8697.invoke(instance_id=..., capability_id=" + repr(name) + ", arguments=..., request_id=...)",
                      "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "ACCESS_DENIED", "REQUEST_CONFLICT"] + sorted(_HS_ERROR_TEXT),
                      "rejection_side_effects": "none", "scene_failure_side_effects": "unknown"}
    every = sorted({s for c in caps.values() for s in c["state_ids"]} | {"power.enabled", "thermal.oven.setpoint_k", "thermal.loop_valve.setpoint_k",
                                                                         "thermal.transfer_line.setpoint_k", "method.equilibration_time_s"})
    caps.update(_at_panel_cap_entries(HS8697_PANEL_CAPS, [s for s in every if not s.startswith("sensor.")],
                                      "AgilentHS8697.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)"))
    return caps


HS8697_PANEL_PATH = HS8697_ASSET_DIR / "panel_ui.json"
_HS_BASE_CAPS = tuple(export_agilent_hs8697_contract_metadata()["capabilities"])
HS8697_PANEL_SPEC = (_at_pu.load_spec(HS8697_PANEL_PATH, capabilities=set(_HS_TERMINAL) | set(_HS_BASE_CAPS))
                     if HS8697_PANEL_PATH.is_file() and not _at_os.environ.get("G00_PANEL_BUILD") else None)
HS8697_PANEL_CAPS = _at_pu.capability_definitions(HS8697_PANEL_SPEC) if HS8697_PANEL_SPEC else {}
_hs_base_export = export_agilent_hs8697_contract_metadata


def export_agilent_hs8697_contract_metadata():
    """CHEM-108 metadata plus the headspace terminal: device slots, terminal commands and panel.* capabilities."""
    meta = _hs_base_export()
    meta["asset_version"] = HS8697_ASSET_VERSION
    meta["state_slots"].update(_hs_slots())
    meta["capabilities"].update(_hs_caps())
    meta["limits"] = meta["limits"] + _HS_LIMITS
    meta["terminal_capabilities"] = sorted(_HS_TERMINAL) + sorted(HS8697_PANEL_CAPS)
    return meta


# ---------------------------------------------------------------- what the screen shows
def _hs_fmt_c(K):
    return "--" if K is None else f"{K - 273.15:.2f} °C"


def _hs_texts(host, values):
    dev, st, m = host._device, values["hs.status"], host._device["method"]
    z = values["hs.zones"]
    s = values["hs.settings"]
    out = {f"{k}_sp": _hs_fmt_c(values["thermal." + k + ".setpoint_k"]) for k in _HS_ZONES}
    out.update({f"{k}_act": _hs_fmt_c(z[k]["actual_K"]) for k in _HS_ZONES})
    seq = values["hs.sequence"]
    err = _hs_errs(dev)                                  # the EPC holds its readings at the setpoints; uncontrolled, a sensor shows its error
    flowing = s["standby_flow_on"] and values["power.enabled"] and dev["gas_supply"]
    flow = s["standby_flow_m3_s"] if flowing else err["vial_flow"]
    pressing = bool(seq and any(j["state"] in ("pressurizing", "extracting") for j in seq["jobs"]))
    tr = dev.get("transfer")
    press = ((m["fill_pressure_Pa"] if m["fill_mode"] != "Constant Volume" else (tr["fill_Pa"] if tr else 0.0) + err["vial_pressure"])
             if pressing else err["vial_pressure"])
    out.update(vial_flow=f"{max(0.0, flow) / _HS_ML:.3f} mL/min", vial_pressure=f"{max(0.0, press) / _HS_PSI:.3f} psi",
               final_loop="--" if tr is None else f"{tr['final_loop_Pa'] / _HS_PSI:.3f} psi",
               ext_carrier="connected" if values["gc.carrier_gas_available"] else "--", reasons=", ".join(st["reasons"]) or "none",
               equilibration="--" if values["method.equilibration_time_s"] is None else f"{values['method.equilibration_time_s'] / 60:.2f} min",
               ambient=_hs_fmt_c(values["ambient.temperature_k"]), parked="Parked" if dev["parked"] else "Ready for use",
               sequence="none" if seq is None else f"{sum(j['state'] == 'done' for j in seq['jobs'])} of {len(seq['jobs'])} entries done" +
               (" (paused)" if seq["paused"] else ""))
    return out


def _hs_status_rows(host, values):
    """Headspace entries of the GC status page (p.13 Figure 2, p.58)."""
    tx, m, s = _hs_texts(host, values), host._device["method"], values["hs.settings"]
    return [dict(name="Headspace Oven Temperature", setpoint=tx["oven_sp"], actual=tx["oven_act"]),
            dict(name="Headspace Loop Temperature", setpoint=tx["loop_valve_sp"], actual=tx["loop_valve_act"]),
            dict(name="Headspace Transfer Line Temp.", setpoint=tx["transfer_line_sp"], actual=tx["transfer_line_act"]),
            dict(name="Headspace Vial Flow", setpoint=f"{(s['standby_flow_m3_s'] if s['standby_flow_on'] else 0.0) / _HS_ML:.3f} mL/min",
                 actual=tx["vial_flow"]),
            dict(name="Headspace Vial Pressure", setpoint=f"{m['fill_pressure_Pa'] / _HS_PSI:.3f} psi", actual=tx["vial_pressure"]),
            dict(name="Headspace External Carrier", setpoint="--", actual=tx["ext_carrier"])]


def _hs_vial_rows(values):
    rows = []
    seq = values["hs.sequence"]
    jobs = {j["sample_id"]: j for j in (seq["jobs"] if seq else []) if j["sample_id"]}
    names = {"equilibrating": "equilibrating", "pressurizing": "pressurizing", "extracting": "extracting", "injecting": "injecting",
             "returning": "returning to tray"}
    for v in values["vial.status"]:
        if v["slot_id"] is None:
            continue
        j = jobs.get(v["sample_id"])
        rows.append(dict(slot=v["slot_id"].replace("tray_", "Tray ").replace("oven_", "Oven "), sample=v["sample_id"],
                         state=names.get(j["state"], v["status"]) if j and j["state"] != "done" else v["status"]))
    return rows


# ---------------------------------------------------------------- runtime and session with the terminal
_HSBaseRuntime = AgilentHS8697
_HSBaseSession = AgilentHS8697Session


class AgilentHS8697(_HSBaseRuntime):
    """CHEM-108 host with the headspace terminal: the device model (self._device) and the front panel (self.panel)."""

    def __init__(self, instance_id, *, scene_adapter=None, clock=None, ambient_temperature_K=None):
        super().__init__(instance_id, scene_adapter=scene_adapter, clock=clock, ambient_temperature_K=ambient_temperature_K)
        self._device = _hs_new_device()
        self._principals["__panel__"] = "agent"
        self._panel_session = AgilentHS8697Session(self, "__panel__")       # sequence injections and commands without a gesturing session
        self._caller = None                                                 # principal of the session making the current panel gesture
        self._pending_loop = None                                           # loop contents of the sequence injection in progress
        self._direct_extraction = None
        self._gc_control = None                                             # trusted host: Prep Run / Start of the connected GC
        self.panel_capture_dir = None
        self.panel = _at_pu.PanelUI(HS8697_PANEL_SPEC, _ATPanelHost(self)) if HS8697_PANEL_SPEC else None

    def debug_snapshot(self):
        with self._lock:
            out = super().debug_snapshot()
            out["device"] = _hs8697_copy.deepcopy(self._device)
            return out

    def _host_set(self, **kw):
        with self._lock:
            self._device.update(kw)
            self._revision += 1

    def set_rack_installed(self, installed):
        """Trusted host: vial racks installed (white rack LED, p.36)."""
        self._host_set(racks=bool(installed))

    def set_vial_gas_supply(self, available):
        """Trusted host: vial pressurization gas at the back panel (p.15)."""
        self._host_set(gas_supply=bool(available))

    def set_gc_ready(self, ready):
        """Trusted host: readiness of the connected GC (System Not Ready sequence action, p.54)."""
        self._host_set(gc_ready=bool(ready))

    def register_gc_control(self, control):
        """Trusted host: the connected GC's run controls for the Prep Run / Start buttons of the host GC touchscreen (the HS pages are drawn by
        this asset).  control(action, principal_id) with action 'prep' / 'start' returns the GC asset's response (dict with 'status' and
        'error'); None disconnects the controls."""
        if control is not None and not callable(control):
            raise ValueError("control must be callable or None")
        with self._lock:
            self._gc_control = control

    def set_vial_leak(self, sample_id, leak_rate_m3_s):
        """Trusted host / evaluator: leak rate of a capped vial (dynamic leak check, User Vial Leak Test); 0 by default."""
        with self._lock:
            if sample_id not in self._samples or not _hs8697_number(leak_rate_m3_s) or leak_rate_m3_s < 0:
                raise ValueError("Registered sample and nonnegative finite leak rate required")
            self._device["vial_leaks"][sample_id] = float(leak_rate_m3_s)

    def set_ambient_temperature(self, temperature_K):
        super().set_ambient_temperature(temperature_K)
        with self._lock:
            _hs_retarget(self._device, self._state, self._time())

    def host_update(self):
        """Trusted host: process the terminal's events (sequence steps, tests, calibration, start-up, EMF) up to the host clock's time."""
        with self._lock:
            now = self._time()
            dev, state = _hs8697_copy.deepcopy(self._device), _hs8697_copy.deepcopy(self._state)
            t = dev["last_t"] if dev["last_t"] is not None else now
            before = self._public_scene_state(state)
            while t < now - 1e-9:
                t1 = min(now, t + _HS_STEP_S)
                _hs_tick(self, dev, state, t, t1)
                t = t1
            dev["last_t"] = now
            if self._scene_adapter is not None and self._public_scene_state(state) != before:
                self._scene_adapter(self.instance_id, self._public_scene_state(state))
            self._device, self._state = dev, state
            self._revision += 1

    def _values(self):
        dev, t = self._device, self._time()
        values = dict(self._state)
        zones = {}
        for z in _HS_ZONES:
            k = _hs_zone_K(dev, self._state, z, t)
            zones[z] = dict(actual_K=None if k is None else round(k, 2), target_K=None if dev["zones"][z] is None else dev["zones"][z]["sp"])
        seq = dev["sequence"]
        values.update({
            "hs.status": _hs_status(dev, self._state, t), "hs.method": dict(dev["method"]), "hs.zones": zones, "hs.settings": dict(dev["settings"]),
            "hs.tray": dict(parked=dev["parked"], racks_installed=dev["racks"]),
            "hs.sequence": None if seq is None else dict(id=seq["id"], started=seq["started"], paused=seq["paused"], jobs=[
                {k: j[k] for k in ("tray_slot", "sample_id", "slot", "state", "done", "extractions", "result", "leak_rate_m3_s")} for j in seq["jobs"]]),
            "hs.diagnostics": dict(running=None if dev["test"] is None else dict(id=dev["test"]["id"], remaining_s=max(0.0, dev["test"]["until_s"] - t)),
                                   results=_hs8697_copy.deepcopy(dev["test_results"])),
            "hs.calibration": dict(tray=None if dev["calibration"] is None else dict(step=dev["calibration"]["step"], remaining_s=max(
                0.0, dev["calibration"].get("until_s", t) - t)), last_tray=dev["cal_done"], zero_times=dict(dev["zero_times"])),
            "hs.emf": {k: dict({x: c[x] for x in ("value", "due_on", "due", "warn_on", "warn")}, level=c.get("level", "ok"), part=G_HS_EMF[k][0],
                               type=G_HS_EMF[k][1], resets=list(c["resets"])) for k, c in dev["emf"].items()},
            "hs.logs": _hs8697_copy.deepcopy(dev["logs"]), "hs.alerts": _hs8697_copy.deepcopy(dev["alerts"]),
            "hs.system": dict(booting=dev["booting_until"] is not None and t < dev["booting_until"], power_epoch=dev["power_epoch"],
                              serial="SIM-8697-0001", firmware="sim-1.2.0"),
            "hs.device_datetime": _at_now(dev, t), "sensor.errors": dict(dev["errors"]),
            "hs.host": dict(gas_supply=dev["gas_supply"], gc_ready=dev["gc_ready"], vial_leaks=dict(dev["vial_leaks"])),
            "hs.transfer": _hs8697_copy.deepcopy(dev.get("transfer")),
        })
        return values

    def panel_values(self):
        values = self._values()
        slots = self._metadata["state_slots"]
        out = {k: v for k, v in values.items() if slots.get(k, {}).get("visibility") == "agent"}
        dev = self._device
        rank = ["ok", "warning", "due"].index
        out.update({"host.powered": self._state["power.enabled"], "host.power_epoch": dev["power_epoch"], "host.booting": values["hs.system"]["booting"],
                    "host.text": _hs_texts(self, values), "host.vials": _hs_vial_rows(values), "host.alert_count": len(dev["alerts"]),
                    "host.emf_flag": max((c.get("level", "ok") for c in dev["emf"].values()), key=rank),
                    "host.emf_rows": [dict(counter=k, part=G_HS_EMF[k][0], status=(f"{c['value']:.1f} days" if G_HS_EMF[k][1] == "days" else
                                                                                   f"{c['value']:.0f} {G_HS_EMF[k][1]}") +
                                           {"ok": "", "warning": "  [warning]", "due": "  [service due]"}[c.get("level", "ok")],
                                           due_on=c["due_on"], due=c["due"], warn_on=c["warn_on"], warn=c["warn"],
                                           last=c["resets"][-1]["time"] if c["resets"] else "--") for k, c in dev["emf"].items()],
                    "host.service": [["Oven", _hs_texts(self, values)["oven_act"]], ["Loop", _hs_texts(self, values)["loop_valve_act"]],
                                     ["Transfer line", _hs_texts(self, values)["transfer_line_act"]], ["Vial flow", _hs_texts(self, values)["vial_flow"]],
                                     ["Vial pressure", _hs_texts(self, values)["vial_pressure"]], ["Ambient", _hs_texts(self, values)["ambient"]],
                                     ["Tray", "parked" if dev["parked"] else "unparked"], ["Serial number", "SIM-8697-0001"],
                                     ["Firmware", "sim-1.2.0"], ["On time", f"{dev['on_time_s'] / 3600:.1f} h"]],
                    "host.racks": dev["racks"], "host.status_rows": _hs_status_rows(self, values)})
        return out

    def panel_command(self, capability_id, arguments):
        """A command of a panel gesture runs as the session that made the gesture (self._caller, set by AgilentHS8697Session._panel)."""
        who = getattr(self, "_caller", None) or "__panel__"
        session = self._panel_session if who == "__panel__" else AgilentHS8697Session(self, who)
        return session._invoke_once(capability_id, arguments, "__panel__")

    def _template_result(self, sample_id, state):
        """The injected loop contents follow the headspace device model (_hs_extraction): a sequence passes the extraction (or the
        Concentrated trap) it made; a direct measure makes one single extraction now."""
        from scripts.basic40_templates import agilent_hs8697 as _hs8697_templates
        transfer = getattr(self, "_template_transfer", None)
        loop = getattr(self, "_pending_loop", None)
        if loop is None and transfer is not None:
            vial = self._device.get("vials", {}).get(sample_id, {"remaining": {}})
            x = _hs_extraction(self, self._device, state, sample_id, vent=True, remaining=vial["remaining"])
            loop = x.get("loop_mass_kg")
            self._direct_extraction = x
        return _hs8697_templates.measure(self._samples[sample_id]["template"], transfer, state["thermal.oven.setpoint_k"],
                                         state["method.equilibration_time_s"], loop_mass_kg=loop)

    def _after_base(self, capability_id, arguments):
        dev, t = self._device, self._time()
        if capability_id == "power.set":
            if arguments["enabled"]:
                dev["power_epoch"] += 1
                dev["booting_until"] = t + _HS_BOOT_S
                dev.update(test=None, calibration=None, sequence=None)
                if dev["settings"]["clear_oven"]:                                  # vials found in the oven go back to the tray (p.60)
                    for i, sid in enumerate(self._state["oven.occupancy"]):
                        if sid is not None:
                            free = next((f"tray_{k + 1:02d}" for k, x in enumerate(self._state["tray.occupancy"]) if x is None), None)
                            _hs_move(self, self._state, sid, free)
                _hs_log(dev, t, "Power on")
            else:
                dev.update(test=None, calibration=None, sequence=None, booting_until=None)
                _hs_log(dev, t, "Standby")
            dev["last_t"] = t
        _hs_retarget(dev, self._state, t)


class AgilentHS8697Session(_HSBaseSession):
    """CHEM-108 session plus the headspace terminal commands and panel.*."""

    def _response(self, status="succeeded", *, request_id=None, result=None, observations=None, code=None, side_effects="none", now=None):
        if code in _HS_ERROR_TEXT:
            r = super()._response(status, request_id=request_id, result=result, observations=observations, code="PRECONDITION_FAILED",
                                  side_effects=side_effects, now=now)
            r["error"].update(code=code, message=_HS_ERROR_TEXT[code])
            return r
        return super()._response(status, request_id=request_id, result=result, observations=observations, code=code, side_effects=side_effects, now=now)

    def describe(self, *, asset_type_id=None, instance_id=None):
        out = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if "capabilities" in out and self._host._principals[self._principal_id] not in ("evaluator", "debug"):
            out["capabilities"] = {k: c for k, c in out["capabilities"].items() if c.get("visibility", "agent") == "agent"}
        return out

    def read(self, *, instance_id=None, state_ids=None):
        host = self._host
        with host._lock:
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            values = host._values()
            if state_ids is None:
                state_ids = [sid for sid in values if self._visible(sid)]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids):
                return self._response("rejected", code="INVALID_ARGUMENT")
            if any(not self._visible(sid) or sid not in values for sid in state_ids):
                return self._response("rejected", code="ACCESS_DENIED")
            now, observations = host._time(), {}
            for sid in state_ids:
                slot, value = host._metadata["state_slots"][sid], values[sid]
                observations[sid] = {"value": _hs8697_copy.deepcopy(value), "unit": slot["unit"],
                                     "quality": "unavailable" if value is None else ("estimated" if slot["origin"] == "derived" else "valid"),
                                     "sample_time_s": now, "sample_window_s": None}
                if value is None:
                    observations[sid]["reason"] = "Value is unknown or not established; no physical observation is simulated."
            return self._response(observations=observations, now=now)

    def _hs_foreign(self, cid, args):
        """Another caller's sequence, diagnostic test or tray calibration would be stopped, paused or changed by cid (lead rule 2026-10-19:
        ACCESS_DENIED on the touchscreen and the direct path); the evaluator / debug identities and the firmware's own sequence steps are
        unrestricted.  The On/Standby switch (power.set models it) and the Park button are physical: never refused."""
        host, me = self._host, self._principal_id
        dev = getattr(host, "_device", None)
        if dev is None or me == "__panel__" or host._principals.get(me) in ("evaluator", "debug"):
            return False
        foreign = lambda x: x is not None and x.get("owner") not in (None, me)        # noqa: E731
        if cid in ("sequence.stop", "sequence.pause", "hs.method.configure", "hs.settings.configure", "method.set_equilibration_time", "pressure.set",
                   "shaking.set", "thermal.set_temperature"):
            return foreign(dev["sequence"])
        if cid == "diagnostics.cancel_test":
            return foreign(dev["test"])
        if cid == "calibration.tray_step":
            return foreign(dev["calibration"])
        return False

    def _invoke_once(self, cid, args, request_id):
        host = self._host
        if self._hs_foreign(cid, args):
            return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
        if cid not in _HS_TERMINAL and cid not in HS8697_PANEL_CAPS:
            host._direct_extraction = None
            response = super()._invoke_once(cid, args, request_id)
            if response["status"] == "succeeded" and cid in ("power.set", "thermal.set_temperature"):
                host._after_base(cid, args)
            x, host._direct_extraction = host._direct_extraction, None
            if response["status"] == "succeeded" and cid == "measure" and x is not None and "removed" in x:   # the extraction depletes the vial
                vial = host._device.setdefault("vials", {}).setdefault(x["sample_id"], {"remaining": {}})
                vial["remaining"] = {sid: vial["remaining"].get(sid, 1.0) * (1.0 - r) for sid, r in x["removed"].items()}
                host._device["transfer"] = _hs_public_transfer(x)
            return response
        cap = host._metadata["capabilities"][cid]
        if _at_validate(cap["input_schema"], args):
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        if cap["visibility"] != "agent" and host._principals[self._principal_id] not in ("evaluator", "debug"):
            return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
        if cid.startswith("panel."):
            return self._panel(cid, args, request_id)
        state, dev = _hs8697_copy.deepcopy(host._state), _hs8697_copy.deepcopy(host._device)
        t = host._time()
        caller, host._caller = getattr(host, "_caller", None), self._principal_id      # the command acts as this session's caller
        try:
            result = _HS_COMMANDS[cid](host, dev, state, args, t)
        except _ATReject as exc:
            return self._response("rejected", request_id=request_id, code=exc.code)
        finally:
            host._caller = caller
        owned = {"sequence.start": "sequence", "diagnostics.start_test": "test", "calibration.tray": "calibration"}.get(cid)
        if owned and dev[owned] is not None:
            dev[owned]["owner"] = self._principal_id                              # sequences, tests and calibrations belong to their caller
        if host._scene_adapter is not None and host._public_scene_state(state) != host._public_scene_state(host._state):
            try:
                host._scene_adapter(host.instance_id, host._public_scene_state(state))
            except Exception:
                return self._response("failed", request_id=request_id, code="DEVICE_ERROR", side_effects="unknown")
        host._state, host._device = state, dev
        host._revision += 1
        return self._response(request_id=request_id, result=result)

    def _panel(self, cid, args, request_id):
        host = self._host
        if host.panel is None:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        try:
            if cid == "panel.capture":
                return self._response(request_id=request_id, result=_at_capture(host.panel, host.instance_id, args["display_id"], host._time(),
                                                                                host.panel_capture_dir))
            if cid == "panel.frame":
                return self._response(request_id=request_id, result={"frame": host.panel.frame()})
            caller, host._caller = getattr(host, "_caller", None), self._principal_id    # the gesture's commands run as this caller
            try:
                if cid == "panel.press":
                    accepted = host.panel.press(args["input_id"], float(args.get("duration_s", 0.1)))
                elif cid == "panel.switch":
                    accepted = host.panel.switch(args["input_id"], args["position"])
                else:
                    if ("to_x" in args) != ("to_y" in args):
                        return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
                    to = (args["to_x"], args["to_y"]) if "to_x" in args else None
                    accepted = host.panel.touch(args["display_id"], args["x"], args["y"], float(args.get("duration_s", 0.1)), to=to)
            finally:
                host._caller = caller
        except _at_pu.PanelError:
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        return self._response(request_id=request_id, result={"accepted": bool(accepted)})
# <<< CHEM-108 HS 8697 headspace terminal


# >>> CHEM-010 Centrifuge 5910 Ri touch terminal
# Device layer of the 5910 Ri touchscreen (operating manual references/manuals/eppendorf_5910ri/, 'p.N' = PDF page): the home screen set points
# (time timed / continuous with the count start, temperature, speed in rpm or rcf with radius, ramps 0-9), changes during a run, Start / Stop,
# Open / Close, Short Spin (press and hold), Fast Temp, favorites and 99 programs, events and run records, settings (about, date / time,
# network, display, language, Short Spin speed, sounds), user management with login and rights, recurring maintenance tasks and rotor health,
# clean-screen lock, standby and the front panel (panel_ui.json run by scripts/asset_runtime/_panel_ui.py).  The CHEM-010 capabilities keep
# their meaning; a run started from the panel goes through centrifuge.measure.  Rotor radius for rcf = the largest slot radius of the trusted
# host's synthetic rotor.
EPP5910_ASSET_VERSION = "1.2.2"
EPP5910_ASSET_DIR = _AT_ROOT / "assets/basic40_batch02/t3_analytical/eppendorf_5910ri"
_EPP_RPM = 2 * _epp_math.pi / 60.0
_EPP_G = 9.80665
_EPP_FAST_TEMP_RPM = 3000.0                                                  # Fast Temp speed (rotor-specific in the manual; assumption)
_EPP_FAST_TEMP_BAND_K = 1.0                                                  # Fast Temp ends within 1 K of the set temperature (assumption)
_EPP_LID_LIFT_RAD = 0.1                                                      # the unlatched lid is lifted slightly (p.27; angle assumed)
_EPP_BOOT_S = 90.0                                                           # ready for operation 90 s after switching on (p.27)
EPP_ROTORS = {"large_capacity": "S-4x Universal-Large", "fixed_angle": "FA-6x50"}   # display names of the two synthetic rotor categories (assumption)
EPP_LANGUAGES = ("English", "Deutsch", "Français", "Español", "Italiano", "Português", "中文", "日本語", "Русский")
EPP_ROLES = ("Restricted User", "User", "Administrator")
EPP_TIME_ZONES = tuple(f"UTC{h:+03d}" for h in range(-12, 15))
_EPP_MIN = (1 / 60.0, 0.0, "min", 0)
EPP_SETTINGS = [
    _at_field("about.name", "Name", "text", "Centrifuge 5910 Ri", "about", "manual p.69 (entered by the administrator)", max_chars=40),
    _at_field("about.registration", "Registration Number", "text", "", "about", "manual p.69", max_chars=40),
    _at_field("about.location", "Location", "text", "", "about", "manual p.69", max_chars=40),
    _at_field("time.automatic", "Automatic date and time", "bool", False, "datetime", "manual p.70 (needs the network and a time server)"),
    _at_field("time.zone", "Time zone", "enum", "UTC+00", "datetime", "manual p.70 (Select time zone)", options=list(EPP_TIME_ZONES)),
    _at_field("net.opc_rest", "Enable OPC/REST access", "bool", False, "network", "manual p.71"),
    _at_field("net.dhcp", "Enable DHCP", "bool", True, "network", "manual p.71"),
    _at_field("net.ip", "IP Address", "text", "192.168.0.10", "network", "manual p.71 (Manual Setup)", pattern=_G8_IP),
    _at_field("net.netmask", "Subnet mask", "text", "255.255.255.0", "network", "manual p.71 (Manual Setup)", pattern=_G8_IP),
    _at_field("net.gateway", "Gateway", "text", "192.168.0.1", "network", "manual p.71 (Manual Setup)", pattern=_G8_IP),
    _at_field("display.brightness", "Display brightness", "number", 80, "display", "manual p.72 (slider)", min=10, max=100, integer=True),
    _at_field("display.energy_save", "Energy save mode", "bool", False, "display", "manual p.72"),
    _at_field("display.timeout_s", "Display timeout", "number", 600.0, "display", "manual p.72 (dimmed after no input)", min=60.0, max=7200.0, si_unit="s",
              show=_EPP_MIN),
    _at_field("device.language", "Language", "enum", "English", "language", "manual p.72 (switch off and on to load)", options=list(EPP_LANGUAGES)),
    _at_field("device.short_spin", "Short Spin speed", "enum", "Maximum", "short_spin", "manual p.52, p.73 (entered or maximum speed)",
              options=["Maximum", "Entered"]),
    _at_field("device.short_spin_rpm", "Short Spin entered speed", "number", 3000.0, "short_spin", "manual p.73", min=100.0, max=14000.0, si_unit="1",
              show=(1.0, 0.0, "rpm", 0)),
    _at_field("sound.alarm", "Acoustic alarm", "bool", True, "sound", "manual p.73"),
    _at_field("sound.end", "End of program tone", "bool", True, "sound", "manual p.73"),
    _at_field("users.auto_logout_s", "Automatic Logout", "number", 900.0, "users", "manual p.60", min=60.0, max=7200.0, si_unit="s", show=_EPP_MIN),
    _at_field("users.login_mode", "Login Mode", "enum", "PIN", "users", "manual p.59-60 (login mode for all users)", options=["PIN", "Password"]),
    _at_field("users.extra", "Grant all users extra privileges", "enum", "Off", "users", "manual p.60 (Restricted User / User rights without login)",
              options=["Off", "Restricted User", "User"]),
]
EPP_RIGHTS = {"setpoints": ("User", "Administrator"), "favorites": ("User", "Administrator"), "programs_edit": ("User", "Administrator"),
              "settings_edit": ("Administrator",), "users": ("Administrator",), "ack_alarm": ("User", "Administrator")}   # p.55-58


def _epp_rpm_of(rad_s):
    return rad_s / _EPP_RPM


def _epp_cred_ok(dev, cred):
    """Login Mode for all users (p.39, p.59-60): PIN = digits on the number pad (4-8, assumption), Password = text (4-16 characters,
    assumption)."""
    if not isinstance(cred, str):
        return False
    if dev["settings"]["users.login_mode"] == "PIN":
        return cred.isdigit() and cred.isascii() and 4 <= len(cred) <= 8
    return 4 <= len(cred) <= 16


def _epp_new_cred(dev, seed):
    """Credentials generated by the device for a new user / a reset (p.62-64): a PIN or a password of the login mode (deterministic here)."""
    if dev["settings"]["users.login_mode"] == "PIN":
        return f"{seed % 10000:04d}"
    letters = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(letters[(seed // len(letters) ** i) % len(letters)] for i in range(8))


def _epp_zone_h(zone):
    return int(zone[3:])


def _epp_new_device():
    sp = dict(duration_s=300.0, continuous=False, count_from="Immediately", temperature_k=277.15, speed_rad_s=300 * _EPP_RPM, speed_mode="rpm",
              rcf=None, radius_m=None, accel=9, brake=9)
    return dict(clock_offset_s=0.0, sp=sp, settings=_at_defaults(EPP_SETTINGS), favorites=dict(
        time=[600.0, 1800.0, 10.0, 3600.0], temperature=[277.15, 283.15, 296.15, 303.15],
        speed=[dict(mode="rcf", value=400.0), dict(mode="rcf", value=1000.0), dict(mode="rcf", value=4000.0), dict(mode="rcf", value=6000.0)]),
        programs={}, events=[], event_seq=0, runs=[], run_seq=0, users=dict(enabled=False, list=[], current=None, seq=0), tasks=[],
        rotor_cycles={k: 0 for k in EPP_ROTORS}, clean_lock=None, standby=False, notice=None, special=None, last_activity_s=0.0,
        current_run=None, power_epoch=0, restart_needed=False, active_language="English", right_panel="favorites", credentials=None)


def _epp_event(dev, t, priority, function, message):
    dev["event_seq"] += 1
    _at_log(dev["events"], dict(id=dev["event_seq"], time=_at_now(dev, t), priority=priority, function=function, message=message,
                                acknowledged=priority == "info"), cap=100000)


def _epp_role(dev):
    u = dev["users"]
    if not u["enabled"]:
        return "Administrator"                                                  # without user management all users have administrator rights
    if u["current"] is not None:
        return next(x["role"] for x in u["list"] if x["id"] == u["current"])
    extra = dev["settings"]["users.extra"]
    return None if extra == "Off" else extra


def _epp_allowed(dev, right):
    role = _epp_role(dev)
    return role is not None and role in EPP_RIGHTS[right]


def _epp_max_radius(host, state):
    profile = host._profiles.get(state["rotor.configuration"])
    return None if profile is None else max(_epp_math.hypot(*p) for p in profile["slot_positions_xy_m"].values())


def _epp_rcf(speed_rad_s, radius_m):
    return speed_rad_s ** 2 * radius_m / _EPP_G


def _epp_method_args(sp):
    return {"speed_rad_s": sp["speed_rad_s"], "temperature_k": sp["temperature_k"], "duration_s": 359940.0 if sp["continuous"] else sp["duration_s"],
            "acceleration_level": sp["accel"] + 1, "braking_level": sp["brake"] + 1}


def _epp_motion_segmented(op, relative_s):
    """Speed and angle of a run with set-point changes: piecewise constant acceleration toward the current target (accelerating at the
    acceleration ramp, slowing at the braking ramp), then braking to rest from decel_start_s (exact piecewise integration)."""
    changes = sorted(op.get("speed_changes", []))
    t_end = min(max(float(relative_s), 0.0), op["end_s"])
    a, b, d = op["acceleration"], op["braking"], op["decel_start_s"]
    v, theta, t = 0.0, 0.0, 0.0
    target = op["speed_target0"]
    marks = sorted({c[0] for c in changes if 0 < c[0] < d} | {d})
    k = 0
    while t < t_end - 1e-12:
        while k < len(changes) and changes[k][0] <= t + 1e-12:
            target = changes[k][1]
            k += 1
        nxt = min([m for m in marks if m > t + 1e-12] + [t_end])
        if t >= d - 1e-12:
            dt = min(t_end - t, v / b if b > 0 else 0.0)
            theta += v * dt - 0.5 * b * dt * dt
            v = max(0.0, v - b * dt)
            t = t_end
            break
        rate = a if target > v else b
        reach = abs(target - v) / rate if rate > 0 else 0.0
        dt = min(nxt - t, reach)
        sign = 1.0 if target > v else -1.0
        theta += v * dt + 0.5 * sign * rate * dt * dt
        v += sign * rate * dt
        t += dt
        if t < nxt - 1e-12:
            theta += v * (nxt - t)
            t = nxt
    return (0.0 if relative_s >= op["end_s"] else v), theta


def _epp_end_time(op):
    """Time the rotor stops: braking from the speed at decel_start_s."""
    if op["decel_start_s"] == float("inf"):
        return float("inf")
    v = _epp_motion_segmented(dict(op, end_s=float("inf")), op["decel_start_s"])[0]
    return op["decel_start_s"] + v / op["braking"]


_epp_motion_before_panel = _epp_motion


def _epp_motion(operation, relative_s):
    """Runs started or changed from the panel carry a segmented profile; others keep the CHEM-010 model."""
    if operation.get("segmented"):
        return _epp_motion_segmented(operation, relative_s)
    return _epp_motion_before_panel(operation, relative_s)


def _epp_segmented(op):
    """Convert a CHEM-010 run into the segmented profile (same motion until the first change)."""
    if not op.get("segmented"):
        op.update(segmented=True, speed_target0=op["speed_target"], speed_changes=[])
    return op


def _epp_reach_time(op, fraction=0.95):
    target = op["speed_target0"]
    return fraction * target / op["acceleration"]


# ---------------------------------------------------------------- terminal commands: rt (runtime), dev, state, ops, args, t
def _epp_gate(rt, dev, state, right=None, idle=False):
    _at_need(state["power.enabled"], "PRECONDITION_FAILED")
    _at_need(not dev["standby"], "STANDBY")
    _at_need(dev["clean_lock"] is None, "LOCKED")
    if dev["users"]["enabled"] and _epp_role(dev) is None:
        raise _ATReject("LOGIN_REQUIRED")
    if right is not None:
        _at_need(_epp_allowed(dev, right), "PERMISSION_DENIED")
    if idle:
        _at_need(not rt._busy(state), "BUSY")


def _epp_current_op(rt, state, ops):
    op = ops.get(state["run.operation_id"])
    return op if op is not None and op["status"] == "running" else None


def _epp_caller(rt):
    """Principal of the session whose command (or panel gesture) is running; runs it starts belong to it (CHEM-010 owner)."""
    return getattr(rt, "_caller", None) or "__panel__"


def _epp_session(rt):
    """The CHEM-010 session of the current caller: _inner commands run with its identity (operation owner, owner guards)."""
    who = _epp_caller(rt)
    return rt._panel_session if who == "__panel__" else Eppendorf5910RiSession(rt, who)


def _epp_privileged(rt, who):
    return getattr(rt, "_visibility", {}).get(who) in ("evaluator", "debug")


def _epp_own(rt, op):
    """A running run is stopped or changed only by the caller that started it (as the direct centrifuge.stop / cancel); the evaluator is
    unrestricted (lead rule 2026-10-19)."""
    who = _epp_caller(rt)
    _at_need(op["owner"] == who or _epp_privileged(rt, who), "ACCESS_DENIED")


def _epp_apply(rt, dev, state, ops, t, changed):
    """Set points into the runtime: idle: centrifuge.set_method (the CHEM-010 path); during a run: the running profile changes (p.51)."""
    sp = dev["sp"]
    op = _epp_current_op(rt, state, ops)
    if op is None:
        code = _epp_session(rt)._inner("centrifuge.set_method", _epp_method_args(sp), state, ops)
        _at_need(code is None, code or "PRECONDITION_FAILED")
        return
    _epp_own(rt, op)
    _at_need(dev["special"] is None, "BUSY")                                         # nothing can be changed during Fast Temp (p.53)
    elapsed = rt._now - op["start_s"]
    _epp_segmented(op)
    if "speed_rad_s" in changed:
        profile = rt._profiles.get(state["rotor.configuration"])
        _at_need(sp["speed_rad_s"] <= profile["operating_speed_limit_rad_s"], "ROTOR_LIMIT")
        if elapsed < op["decel_start_s"]:
            op["speed_changes"].append((elapsed, sp["speed_rad_s"]))
    if "brake" in changed and elapsed < op["decel_start_s"]:
        op["braking"] = rt._process["braking_rad_s2"][sp["brake"]]
    if "accel" in changed:
        op["acceleration"] = rt._process["acceleration_rad_s2"][sp["accel"]]
    if "temperature_k" in changed:
        op["thermal_segments"].append((elapsed, state["display.temperature_k"], sp["temperature_k"]))
    if "duration_s" in changed or "continuous" in changed:
        start = op.get("count_start_s", 0.0)
        new = float("inf") if sp["continuous"] else max(elapsed, start + sp["duration_s"])
        if elapsed < op["decel_start_s"]:
            op["decel_start_s"] = new
    op["end_s"] = _epp_end_time(op)
    state.update({"method." + k: v for k, v in _epp_method_args(sp).items() if k != "duration_s"} |
                 {"method.acceleration_level": sp["accel"] + 1, "method.braking_level": sp["brake"] + 1})
    rt._project_operation(state, op, rt._now)
    if dev["current_run"] is not None:
        dev["current_run"]["events"].append(f"{_at_now(dev, t)}: set point changed ({', '.join(sorted(changed))})")


def _epp_c_set_point(rt, dev, state, ops, a, t):
    _epp_gate(rt, dev, state, "setpoints")
    sp = dict(dev["sp"])
    ch = a["settings"]
    for k in ch:
        _at_need(k in sp and k not in ("rcf",), "INVALID_ARGUMENT")
    if "speed_mode" in ch:
        _at_need(ch["speed_mode"] in ("rpm", "rcf"), "INVALID_ARGUMENT")
    if "count_from" in ch:
        _at_need(ch["count_from"] in ("Immediately", "Selected Speed Reached"), "INVALID_ARGUMENT")
    for key, lo, hi in (("duration_s", 10.0, 359940.0), ("temperature_k", 262.15, 313.15), ("speed_rad_s", EPPENDORF_5910RI_SPEED_MIN_RAD_S,
                                                                                               EPPENDORF_5910RI_SPEED_MAX_RAD_S), ("accel", 0, 9), ("brake", 0, 9)):
        if key in ch:
            v = ch[key]
            _at_need(_epp_finite(v) and lo - 1e-9 <= v <= hi + 1e-9 and (key not in ("accel", "brake") or float(v).is_integer()), "INVALID_ARGUMENT")
    if "continuous" in ch:
        _at_need(type(ch["continuous"]) is bool, "INVALID_ARGUMENT")
    rmax = _epp_max_radius(rt, state)
    if "radius_m" in ch:
        _at_need(ch["radius_m"] is None or (_epp_finite(ch["radius_m"]) and rmax is not None and 0 < ch["radius_m"] <= rmax + 1e-12), "INVALID_ARGUMENT")
    sp.update({k: (int(v) if k in ("accel", "brake") else float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in ch.items()})
    if sp["speed_mode"] == "rcf":
        _at_need(rmax is not None, "NO_ROTOR")
        if "rcf" in a:
            _at_need(_epp_finite(a["rcf"]) and a["rcf"] > 0, "INVALID_ARGUMENT")
            r = sp["radius_m"] or rmax
            sp["speed_rad_s"] = _epp_math.sqrt(a["rcf"] * _EPP_G / r)
            _at_need(EPPENDORF_5910RI_SPEED_MIN_RAD_S - 1e-9 <= sp["speed_rad_s"] <= EPPENDORF_5910RI_SPEED_MAX_RAD_S + 1e-9, "INVALID_ARGUMENT")
            ch = dict(ch, speed_rad_s=sp["speed_rad_s"])
    else:
        sp["radius_m"] = None                                                     # the radius is not applied in rpm mode (p.49)
    profile = rt._profiles.get(state["rotor.configuration"])
    if profile is not None and ("speed_rad_s" in ch or "rcf" in a):                # the permitted speed depends on the rotor (p.49)
        _at_need(sp["speed_rad_s"] <= profile["operating_speed_limit_rad_s"] + 1e-9, "ROTOR_LIMIT")
    dev["sp"] = sp
    _epp_apply(rt, dev, state, ops, t, set(ch))
    return {"set_points": dict(sp)}


def _epp_c_start(rt, dev, state, ops, a, t):
    """Start (p.50): centrifuge.measure with the set points; the time counts from the start or from 95 % of the speed; Continuous runs until
    Stop."""
    _epp_gate(rt, dev, state)
    if not state["lid.closed"]:                                                      # the refusal is logged on the device (committed log)
        _epp_event(rt._device, t, "warning", "Centrifugation", "Centrifugation cannot be started: the centrifuge lid is not closed.")
        raise _ATReject("LID_OPEN")
    code = _epp_session(rt)._inner("centrifuge.set_method", _epp_method_args(dev["sp"]), state, ops)
    _at_need(code is None, code or "PRECONDITION_FAILED")
    resp = _epp_session(rt)._inner("centrifuge.measure", {}, state, ops)          # the run belongs to the caller
    if resp is not None:
        if resp == "IMBALANCE":
            _epp_event(rt._device, t, "alarm", "Centrifugation", "Imbalance detected: load the rotor symmetrically.")
        raise _ATReject(resp)
    op = ops[state["run.operation_id"]]
    sp = dev["sp"]
    if sp["continuous"] or sp["count_from"] == "Selected Speed Reached":
        _epp_segmented(op)
        if sp["continuous"]:
            op["decel_start_s"] = float("inf")
        else:
            op["count_start_s"] = _epp_reach_time(op)
            op["decel_start_s"] = op["count_start_s"] + sp["duration_s"]
        op["end_s"] = _epp_end_time(op)
        rt._project_operation(state, op, rt._now)
    dev["run_seq"] += 1
    dev["current_run"] = dict(id=dev["run_seq"], kind="run", start=_at_now(dev, t), params=dict(sp), rotor=EPP_ROTORS.get(state["rotor.configuration"]),
                              user=dev["users"]["current"], events=[], op=state["run.operation_id"])
    return {"operation_id": state["run.operation_id"]}


def _epp_c_stop(rt, dev, state, ops, a, t):
    _at_need(state["power.enabled"], "PRECONDITION_FAILED")
    op = _epp_current_op(rt, state, ops)
    _at_need(op is not None, "PRECONDITION_FAILED")
    _epp_own(rt, op)
    rt._request_stop(state, op)
    if dev["current_run"] is not None:
        dev["current_run"]["events"].append(f"{_at_now(dev, t)}: stopped by the user")
    return {"run_status": state["run.status"]}


def _epp_special_run(rt, dev, state, ops, t, kind, speed, duration, accel, brake):
    """Short Spin and Fast Temp: a run on the same motion / thermal model without the sample checks of centrifuge.measure."""
    _at_need(state["lid.closed"], "LID_OPEN")
    profile = rt._profiles.get(state["rotor.configuration"])
    _at_need(profile is not None and rt._process is not None, "MISSING_PARAMETER")
    _at_need(not state["alarm.imbalance"], "IMBALANCE")
    speed = min(speed, profile["operating_speed_limit_rad_s"])
    rt._run_counter += 1
    op_id = rt.instance_id + ":centrifuge:" + str(rt._run_counter)
    probe = dict(state, **{"method.speed_rad_s": speed, "method.duration_s": duration, "method.acceleration_level": accel,
                           "method.braking_level": brake, "method.temperature_k": dev["sp"]["temperature_k"]})
    op = _epp_segmented(rt._make_operation(probe, _epp_caller(rt), op_id))
    if kind == "fast_temp":
        op["decel_start_s"] = float("inf")
    op["end_s"] = _epp_end_time(op)
    state["run.operation_id"] = op_id
    rt._project_operation(state, op, rt._now)
    ops[op_id] = op
    dev["special"] = dict(kind=kind, op=op_id, start=_at_now(dev, t))
    dev["run_seq"] += 1
    dev["current_run"] = dict(id=dev["run_seq"], kind=kind, start=_at_now(dev, t), params=dict(dev["sp"]), rotor=EPP_ROTORS.get(state["rotor.configuration"]),
                              user=dev["users"]["current"], events=[], op=op_id)


def _epp_c_short_spin(rt, dev, state, ops, a, t):
    """Short Spin (p.52-53): runs while the button is held (hold_s), at the maximum or the entered speed, without the set ramps."""
    _epp_gate(rt, dev, state, idle=True)
    s = dev["settings"]
    profile = rt._profiles.get(state["rotor.configuration"])
    speed = (profile["operating_speed_limit_rad_s"] if profile else EPPENDORF_5910RI_SPEED_MAX_RAD_S) if s["device.short_spin"] == "Maximum" else \
        s["device.short_spin_rpm"] * _EPP_RPM
    _epp_special_run(rt, dev, state, ops, t, "short_spin", speed, float(a["hold_s"]), 10, 10)
    op = ops[state["run.operation_id"]]
    op["decel_start_s"] = float(a["hold_s"])                                        # released: the rotor decelerates
    op["end_s"] = _epp_end_time(op)
    rt._project_operation(state, op, rt._now)
    return {"operation_id": state["run.operation_id"], "stop_s": op["end_s"]}


def _epp_c_fast_temp(rt, dev, state, ops, a, t):
    """Fast Temp (p.53): a run without samples at a rotor-specific speed until the set temperature is reached."""
    _epp_gate(rt, dev, state, idle=True)
    _epp_special_run(rt, dev, state, ops, t, "fast_temp", _EPP_FAST_TEMP_RPM * _EPP_RPM, 359940.0, 10, 10)
    _epp_event(dev, t, "info", "Fast Temp", "Preparing Temperature")
    return {"operation_id": state["run.operation_id"]}


def _epp_c_notice(rt, dev, state, ops, a, t):
    _at_need(dev["notice"] is not None, "PRECONDITION_FAILED")
    dev["notice"] = None
    return {"confirmed": True}


def _epp_c_lid(rt, dev, state, ops, a, t):
    """Open (p.27, p.52): the electronic latch unlocks with the rotor stopped and the lid is lifted slightly (then pushed up by hand; closing
    is pressing the lid down until the latch engages)."""
    _epp_gate(rt, dev, state)
    _at_need(not rt._busy(state) and not state["lid.locked"], "LID_LOCKED")
    _at_need(state["lid.closed"], "PRECONDITION_FAILED")
    code = _epp_session(rt)._inner("lid.set_angle", {"angle_rad": _EPP_LID_LIFT_RAD}, state, ops)
    _at_need(code is None, code or "PRECONDITION_FAILED")
    return {"angle_rad": _EPP_LID_LIFT_RAD}


def _epp_c_favorite(rt, dev, state, ops, a, t):
    if a["action"] == "apply":
        cat, pos = a["category"], a["position"]
        fav = dev["favorites"][cat][pos]
        if cat == "time":
            return _epp_c_set_point(rt, dev, state, ops, dict(settings={"duration_s": fav, "continuous": False}), t)
        if cat == "temperature":
            return _epp_c_set_point(rt, dev, state, ops, dict(settings={"temperature_k": fav}), t)
        if fav["mode"] == "rpm":
            return _epp_c_set_point(rt, dev, state, ops, dict(settings={"speed_mode": "rpm", "speed_rad_s": fav["value"] * _EPP_RPM}), t)
        return _epp_c_set_point(rt, dev, state, ops, dict(settings={"speed_mode": "rcf", "radius_m": None}, rcf=fav["value"]), t)
    _epp_gate(rt, dev, state, "favorites")
    sp = dev["sp"]
    value = {"time": sp["duration_s"], "temperature": sp["temperature_k"]}.get(a["category"])
    if a["category"] == "speed":
        value = dict(mode="rpm", value=round(_epp_rpm_of(sp["speed_rad_s"]))) if sp["speed_mode"] == "rpm" else \
            dict(mode="rcf", value=round(_epp_rcf(sp["speed_rad_s"], _epp_max_radius(rt, state))))    # the radius is not stored (p.65)
    dev["favorites"][a["category"]][a["position"]] = value
    return {"favorite": value}


def _epp_c_program(rt, dev, state, ops, a, t):
    """Programs (p.66-67): save (an empty field or Save current parameters), load, rename, lock / unlock, delete, show info."""
    act, n = a["action"], str(a["number"])
    progs = dev["programs"]
    if act == "load":
        _epp_gate(rt, dev, state)
        _at_need(n in progs, "NOT_FOUND")
        dev["sp"] = dict(progs[n]["sp"])
        _epp_apply(rt, dev, state, ops, t, set(dev["sp"]))
        return {"program": int(n), "loaded": True}
    _epp_gate(rt, dev, state, "programs_edit")
    if act == "save":
        _at_need(n not in progs or not progs[n]["locked"], "PROGRAM_LOCKED")
        name = a.get("name") or (progs[n]["name"] if n in progs else None)
        _at_need(bool(name), "INVALID_ARGUMENT")
        progs[n] = dict(name=name, sp=dict(dev["sp"]), locked=progs.get(n, {}).get("locked", False), rotor=state["rotor.configuration"])
    else:
        _at_need(n in progs, "NOT_FOUND")
        if act == "rename":
            _at_need(bool(a.get("name")) and not progs[n]["locked"], "PROGRAM_LOCKED" if progs[n]["locked"] else "INVALID_ARGUMENT")
            progs[n]["name"] = a["name"]
        elif act in ("lock", "unlock"):
            progs[n]["locked"] = act == "lock"
        elif act == "delete":
            _at_need(not progs[n]["locked"], "PROGRAM_LOCKED")
            del progs[n]
    return {"program": int(n), "action": act}


def _epp_c_events(rt, dev, state, ops, a, t):
    _epp_gate(rt, dev, state)
    if a["action"] == "acknowledge":
        for e in dev["events"]:
            if not e["acknowledged"] and (e["priority"] in ("info", "warning") or _epp_allowed(dev, "ack_alarm")):
                e["acknowledged"] = True
        return {"acknowledged": True}
    raise _ATReject("NO_USB")                                                         # export needs a USB stick (p.42)


def _epp_c_settings(rt, dev, state, ops, a, t):
    _epp_gate(rt, dev, state)
    ch = a["settings"]
    admin_only = [k for k in ch if k.startswith(("about.", "net.", "users.")) or k in ("sound.alarm", "sound.end")]
    if admin_only:
        _at_need(_epp_allowed(dev, "settings_edit"), "PERMISSION_DENIED")
    new = _at_apply(EPP_SETTINGS, dev["settings"], ch)
    if new["time.automatic"] and not dev["settings"]["time.automatic"]:
        raise _ATReject("NO_NETWORK")                                                # automatic time needs the network and a time server (p.70)
    if new["device.language"] != dev["settings"]["device.language"]:
        dev["restart_needed"] = True                                                 # loaded at the next switch-on (p.72)
    if new["time.zone"] != dev["settings"]["time.zone"]:                           # the clock runs on; the shown local time moves with the zone
        dev["clock_offset_s"] += 3600.0 * (_epp_zone_h(new["time.zone"]) - _epp_zone_h(dev["settings"]["time.zone"]))   # (p.70: records may
    dev["settings"] = new                                                            # then be sorted incorrectly)
    return {"settings": {k: new[k] for k in ch}}


def _epp_c_clock(rt, dev, state, ops, a, t):
    _epp_gate(rt, dev, state)
    _at_need(not dev["settings"]["time.automatic"], "PRECONDITION_FAILED")
    try:
        target = _at_dt.datetime.fromisoformat(a["date"] + "T" + a["time"])
    except ValueError:
        raise _ATReject("INVALID_ARGUMENT") from None
    dev["clock_offset_s"] = (target - _AT_EPOCH).total_seconds() - t
    return {"datetime": _at_now(dev, t)}


def _epp_c_users(rt, dev, state, ops, a, t):
    """User management (p.58-64): enable with the first administrator, disable (all accounts deleted), add / edit / delete users, reset PIN."""
    u = dev["users"]
    act = a["action"]
    if act == "enable":
        _epp_gate(rt, dev, state)
        _at_need(not u["enabled"] and bool(a.get("name")), "INVALID_ARGUMENT")
        if "login_mode" in a:                                                         # the Login Mode window comes first (p.58-59)
            dev["settings"] = dict(dev["settings"], **{"users.login_mode": a["login_mode"]})
        _at_need(_epp_cred_ok(dev, a.get("pin")), "INVALID_ARGUMENT")
        u.update(enabled=True, seq=1, list=[dict(id=1, name=a["name"], full_name="", email="", role="Administrator", pin=a["pin"])], current=1)
        _epp_event(dev, t, "info", "User Management", "User Management successfully enabled")
        return {"enabled": True, "user_id": 1}
    if act == "change_pin":                                                           # own PIN / password: every logged-in user (p.64)
        _epp_gate(rt, dev, state)
        _at_need(u["enabled"] and u["current"] is not None, "PRECONDITION_FAILED")
        me = next(x for x in u["list"] if x["id"] == u["current"])
        _at_need(a.get("pin") == me["pin"], "WRONG_PIN")
        _at_need(_epp_cred_ok(dev, a.get("new_pin")), "INVALID_ARGUMENT")
        me["pin"] = a["new_pin"]
        return {"user_id": me["id"]}
    _epp_gate(rt, dev, state, "users")
    _at_need(u["enabled"], "PRECONDITION_FAILED")
    if act == "disable":
        me = next(x for x in u["list"] if x["id"] == u["current"])
        _at_need(a.get("pin") == me["pin"], "WRONG_PIN")
        u.update(enabled=False, list=[], current=None)
        return {"enabled": False}
    if act == "add":
        _at_need(bool(a.get("name")) and len(u["list"]) < 999 and all(x["name"] != a["name"] for x in u["list"]), "INVALID_ARGUMENT")
        u["seq"] += 1
        pin = _epp_new_cred(dev, u["seq"] * 7919)                                    # generated PIN / password (deterministic in the simulation)
        u["list"].append(dict(id=u["seq"], name=a["name"], full_name="", email="", role="Restricted User", pin=pin))
        dev["credentials"] = dict(id=u["seq"], name=a["name"], pin=pin)                # shown once in the window (p.62)
        return {"user_id": u["seq"], "pin": pin}
    user = next((x for x in u["list"] if x["id"] == a.get("user_id")), None)
    _at_need(user is not None, "NOT_FOUND")
    if act == "edit":
        if a.get("role") is not None:
            _at_need(a["role"] in EPP_ROLES and user["id"] != u["current"], "INVALID_ARGUMENT")
            user["role"] = a["role"]
        if a.get("full_name") is not None:
            user["full_name"] = a["full_name"]
        return {"user_id": user["id"]}
    if act == "delete":
        _at_need(user["id"] != u["current"], "INVALID_ARGUMENT")
        u["list"].remove(user)
        return {"user_id": user["id"]}
    user["pin"] = _epp_new_cred(dev, user["id"] * 104729 + int(t))                  # Reset Password/PIN: a new one is generated (p.63-64)
    dev["credentials"] = dict(id=user["id"], name=user["name"], pin=user["pin"])
    return {"user_id": user["id"], "pin": user["pin"]}


def _epp_c_login(rt, dev, state, ops, a, t):
    u = dev["users"]
    _at_need(state["power.enabled"] and u["enabled"], "PRECONDITION_FAILED")
    if a["action"] == "logout":
        _at_need(u["current"] is not None, "PRECONDITION_FAILED")
        u["current"] = None
        return {"logged_in": False}
    user = next((x for x in u["list"] if str(x["id"]) == a.get("user") or x["name"] == a.get("user")), None)
    # PIN mode: only digits can be entered on the number pad; Password mode: text (p.39)
    _at_need(user is not None and _epp_cred_ok(dev, a.get("pin")) and user["pin"] == a.get("pin"), "WRONG_PIN")
    u["current"] = user["id"]
    dev["last_activity_s"] = t
    return {"logged_in": True, "user_id": user["id"], "role": user["role"]}


def _epp_c_task(rt, dev, state, ops, a, t):
    """Maintenance and Qualification > Recurring Tasks (p.73-74): add, edit (interval / reminder), mark done, delete."""
    _epp_gate(rt, dev, state, "settings_edit")
    act = a["action"]
    if act == "add":
        _at_need(bool(a.get("name")) and _epp_finite(a.get("interval_s")) and a["interval_s"] > 0, "INVALID_ARGUMENT")
        dev["tasks"].append(dict(name=a["name"], interval_s=float(a["interval_s"]), reminder=bool(a.get("reminder", True)), last=_at_now(dev, t),
                                 last_s=t + dev["clock_offset_s"], due=False))
        return {"tasks": len(dev["tasks"])}
    _at_need(0 <= a.get("index", -1) < len(dev["tasks"]), "NOT_FOUND")
    task = dev["tasks"][a["index"]]
    if act == "edit":
        if a.get("interval_s") is not None:
            _at_need(_epp_finite(a["interval_s"]) and a["interval_s"] > 0, "INVALID_ARGUMENT")
            task["interval_s"] = float(a["interval_s"])
        if a.get("reminder") is not None:
            task["reminder"] = bool(a["reminder"])
    elif act == "done":
        task.update(last=_at_now(dev, t), last_s=t + dev["clock_offset_s"], due=False)
    else:
        del dev["tasks"][a["index"]]
    return {"tasks": len(dev["tasks"])}


def _epp_c_standby(rt, dev, state, ops, a, t):
    _at_need(state["power.enabled"], "PRECONDITION_FAILED")
    if a["active"]:
        _at_need(not rt._busy(state), "BUSY")
    dev["standby"] = bool(a["active"])
    dev["last_activity_s"] = t
    return {"standby": dev["standby"]}


def _epp_c_clean(rt, dev, state, ops, a, t):
    """Clean screen (p.45): locks the touchscreen until the corners are tapped in the numbered order (1-4)."""
    _at_need(state["power.enabled"], "PRECONDITION_FAILED")
    if a["action"] == "lock":
        dev["clean_lock"] = dict(next=1)
        return {"locked": True}
    c = dev["clean_lock"]
    _at_need(c is not None, "PRECONDITION_FAILED")
    c["next"] = c["next"] + 1 if a["corner"] == c["next"] else (2 if a["corner"] == 1 else 1)
    if c["next"] > 4:
        dev["clean_lock"] = None
    return {"locked": dev["clean_lock"] is not None}


_EPP_COMMANDS = {
    "centrifuge.set_point": _epp_c_set_point, "run.start": _epp_c_start, "run.stop": _epp_c_stop, "run.short_spin": _epp_c_short_spin,
    "run.fast_temp": _epp_c_fast_temp, "message.confirm": _epp_c_notice, "lid.open": _epp_c_lid, "favorites.use": _epp_c_favorite,
    "program.manage": _epp_c_program, "events.manage": _epp_c_events, "settings.configure": _epp_c_settings, "clock.set": _epp_c_clock,
    "users.manage": _epp_c_users, "session.login": _epp_c_login, "maintenance.task": _epp_c_task, "standby.set": _epp_c_standby,
    "screen.clean": _epp_c_clean,
}


def _epp_tick(rt, dev, state, ops, t):
    """After the host clock advanced: Fast Temp end, run records, automatic logout, recurring task reminders."""
    sp = dev["special"]
    if sp is not None and sp["kind"] == "fast_temp":
        op = ops.get(sp["op"])
        if op is not None and op["status"] == "running" and op["decel_start_s"] == float("inf") and \
                abs(state["display.temperature_k"] - dev["sp"]["temperature_k"]) <= _EPP_FAST_TEMP_BAND_K:
            rt._request_stop(state, op)
            op["outcome"] = "succeeded"
            dev["notice"] = dict(text="Fast Temp finished", time=_at_now(dev, t))
            _epp_event(dev, t, "info", "Fast Temp", "Fast Temp finished")
    if dev["current_run"] is None and state["run.status"] in ("running", "stopping") and state["run.operation_id"]:
        dev["run_seq"] += 1                                                          # a run started with centrifuge.measure is recorded too
        dev["current_run"] = dict(id=dev["run_seq"], kind="run", start=_at_now(dev, t), params=dict(dev["sp"]), events=[],
                                  rotor=EPP_ROTORS.get(state["rotor.configuration"]), user=dev["users"]["current"], op=state["run.operation_id"])
    run = dev["current_run"]
    if run is not None:
        op = ops.get(run["op"])
        if op is None or op["status"] != "running":
            result = "Completed" if op is not None and op["status"] == "succeeded" else "Completed with warnings" if op is not None and \
                op["status"] == "cancelled" and state["power.enabled"] else "Failed"
            dev["runs"].insert(0, dict(id=run["id"], kind=run["kind"], result=result, params=run["params"], start=run["start"], end=_at_now(dev, t),
                                       rotor=run["rotor"], user=run["user"], events=run["events"]))
            del dev["runs"][1000:]
            if state["rotor.configuration"] in dev["rotor_cycles"]:
                dev["rotor_cycles"][state["rotor.configuration"]] += 1
            dev["current_run"] = None
            if dev["special"] is not None and dev["special"]["op"] == run["op"]:
                dev["special"] = None
            _epp_event(dev, t, "info", "Centrifugation", f"Run {run['id']} {result.lower()}")
    u = dev["users"]
    if u["enabled"] and u["current"] is not None and t - dev["last_activity_s"] >= dev["settings"]["users.auto_logout_s"] - 1e-9 and not rt._busy(state):
        u["current"] = None
        _epp_event(dev, t, "info", "User Management", "Automatic logout")
    for task in dev["tasks"]:
        if not task["due"] and t + dev["clock_offset_s"] - task["last_s"] >= task["interval_s"] - 1e-9:
            task["due"] = True
            if task["reminder"]:
                _epp_event(dev, t, "warning", "Maintenance", f"Recurring task due: {task['name']}")


# ---------------------------------------------------------------- contract metadata of the terminal
def _epp_slots():
    def slot(origin, schema, visibility="agent"):
        return {"value_schema": schema, "unit": "1", "origin": origin, "visibility": visibility, "access": "read_only", "write_via": None}
    nullable = lambda s: {"anyOf": [s, {"type": "null"}]}  # noqa: E731
    return {"centrifuge.set_points": slot("setpoint", _AT_ANY), "centrifuge.status": slot("derived", _AT_ANY), "favorites.list": slot("setpoint", _AT_ANY),
            "programs.list": slot("setpoint", _AT_LIST), "events.list": slot("derived", _AT_LIST), "runs.records": slot("derived", _AT_LIST),
            "device.settings": slot("setpoint", _AT_ANY), "users.state": slot("derived", _AT_ANY), "maintenance.tasks": slot("setpoint", _AT_LIST),
            "rotor.health": slot("derived", _AT_ANY), "device.datetime": slot("derived", _AT_STR), "device.notice": slot("derived", nullable(_AT_ANY)),
            "device.system": slot("derived", _AT_ANY)}


_EPP_E_SP = ["centrifuge.set_points", "method.configured", "method.speed_rad_s", "method.temperature_k", "method.duration_s", "method.acceleration_level",
             "method.braking_level", "run.status", "display.speed_rad_s", "measurement.curve", "lid.locked"]
_EPP_E_RUN = ["run.status", "run.operation_id", "run.elapsed_s", "lid.locked", "display.speed_rad_s", "display.temperature_k", "rotor.angle_rad",
              "measurement.curve", "centrifuge.status", "events.list", "method.configured", "method.speed_rad_s", "method.temperature_k",
              "method.duration_s", "method.acceleration_level", "method.braking_level"]
_EPP_STR = {"type": "string", "minLength": 1, "maxLength": 40}
_EPP_CAPDEFS = {
    "centrifuge.set_point": ({"settings": {"type": "object", "properties": {
        "duration_s": _at_num("s", 10, 359940), "continuous": _AT_BOOL, "count_from": {"type": "string", "enum": ["Immediately", "Selected Speed Reached"]},
        "temperature_k": _at_num("K", 262.15, 313.15), "speed_rad_s": _at_num("rad/s", EPPENDORF_5910RI_SPEED_MIN_RAD_S, EPPENDORF_5910RI_SPEED_MAX_RAD_S),
        "speed_mode": {"type": "string", "enum": ["rpm", "rcf"]}, "radius_m": {"anyOf": [_at_num("m", 0, 1), {"type": "null"}]},
        "accel": _at_num("1", 0, 9, True), "brake": _at_num("1", 0, 9, True)}, "additionalProperties": False, "required": [], "minProperties": 1},
                              "rcf": _at_num("1", 1, 100000)}, ("rcf",), {"set_points": _AT_ANY},
                             {"settings": {"duration_s": 600.0, "temperature_k": 277.15}},
                             "主屏设定：时间（定时/连续、计时起点）、温度、转速（rpm或rcf，rcf按转子半径，可设半径）、加速/制动档0-9；空闲时经centrifuge.set_method写入，运行中"
                             "改变当前运行（p.47-51）", _EPP_E_SP, "configuration"),
    "run.start": ({}, (), {"operation_id": _AT_STR}, {}, "Start：盖已关时以当前设定开始离心（经centrifuge.measure），计时从开始或达到95%转速起，连续模式直到Stop（p.47-51）",
                  _EPP_E_RUN, "device_command"),
    "run.stop": ({}, (), {"run_status": _AT_STR}, {}, "Stop：提前结束离心，转子减速，停转前盖保持锁定（p.51）", _EPP_E_RUN, "device_command"),
    "run.short_spin": ({"hold_s": _at_num("s", 0.1, 600)}, (), {"operation_id": _AT_STR, "stop_s": _at_num("s", 0)}, {"hold_s": 5.0},
                       "Short Spin：按住时运行（最大或设定转速，不用设定的斜坡），松开即减速（p.52-53）", _EPP_E_RUN, "device_command"),
    "run.fast_temp": ({}, (), {"operation_id": _AT_STR}, {}, "Fast Temp：无样品控温运行至设定温度后自动停止并提示Fast Temp finished（p.53）",
                      _EPP_E_RUN + ["device.notice"], "device_command"),
    "message.confirm": ({}, (), {"confirmed": _AT_BOOL}, {}, "确认需确认的消息（如Fast Temp finished，p.53）", ["device.notice"], "device_command"),
    "lid.open": ({}, (), {"angle_rad": _at_num("rad", 0)}, {}, "Open：转子停转时解锁电子盖锁，盖自动抬起少许（p.27、p.52）",
                 ["lid.angle_rad", "lid.closed", "measurement.curve"], "device_command"),
    "favorites.use": ({"action": {"type": "string", "enum": ["apply", "store"]}, "category": {"type": "string", "enum": ["time", "temperature", "speed"]},
                       "position": _at_num("1", 0, 3, True)}, (), {"set_points": _AT_ANY, "favorite": _AT_ANY},
                      {"action": "apply", "category": "time", "position": 0}, "收藏：点按应用到设定，或Add to favorites覆盖所选位置（p.65）",
                      _EPP_E_SP + ["favorites.list"], "configuration"),
    "program.manage": ({"action": {"type": "string", "enum": ["save", "load", "rename", "lock", "unlock", "delete"]}, "number": _at_num("1", 1, 99, True),
                        "name": _EPP_STR}, ("name",), {"program": _at_num("1", 1, 99, True), "action": _AT_STR, "loaded": _AT_BOOL},
                       {"action": "save", "number": 1, "name": "Pellet"}, "程序1-99：保存当前参数、加载、重命名、锁定/解锁、删除（p.66-67）",
                       _EPP_E_SP + ["programs.list"], "configuration"),
    "events.manage": ({"action": {"type": "string", "enum": ["acknowledge", "export"]}}, (), {"acknowledged": _AT_BOOL}, {"action": "acknowledge"},
                      "Events：确认未确认的通知（权限）；导出需要U盘（仿真中没有）（p.41-42）", ["events.list"], "device_command"),
    "settings.configure": ({"settings": _at_settings_schema(EPP_SETTINGS)}, (), {"settings": _AT_ANY}, {"settings": {"display.brightness": 60}},
                           "Settings：关于（名称、注册号、位置）、日期时间与时区、网络、显示、语言（重启生效）、Short Spin、声音、用户管理选项（p.69-73）",
                           ["device.settings", "device.system"], "configuration"),
    "clock.set": ({"date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                   "time": {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?$"}}, (), {"datetime": _AT_STR},
                  {"date": "2026-10-19", "time": "09:30"}, "手动设定日期与时间（自动时间关闭时，p.70）", ["device.datetime"], "configuration"),
    "users.manage": ({"action": {"type": "string", "enum": ["enable", "disable", "add", "edit", "delete", "reset_pin", "change_pin"]},
                      "name": _EPP_STR, "pin": {"type": "string", "minLength": 4, "maxLength": 16}, "user_id": _at_num("1", 1, 999, True),
                      "role": {"type": "string", "enum": list(EPP_ROLES)}, "full_name": {"type": "string", "maxLength": 40},
                      "login_mode": {"type": "string", "enum": ["PIN", "Password"]}, "new_pin": {"type": "string", "minLength": 4, "maxLength": 16}},
                     ("name", "pin", "user_id", "role", "full_name", "login_mode", "new_pin"), {"enabled": _AT_BOOL, "user_id": _at_num("1", 1, 999, True),
                                                                                     "pin": {"type": "string"}}, {"action": "add", "name": "operator1"},
                     "用户管理：启用（先选全体用户的登录方式PIN/密码，再建首个管理员）、停用（删除全部账户）、添加/编辑/删除用户、重置PIN/密码"
                     "（按登录方式生成数字PIN或文字密码）、已登录用户修改自己的PIN/密码（p.58-64）", ["users.state", "device.settings", "events.list"], "configuration"),
    "session.login": ({"action": {"type": "string", "enum": ["login", "logout"]}, "user": _EPP_STR, "pin": {"type": "string", "minLength": 4, "maxLength": 16}},
                      ("user", "pin"), {"logged_in": _AT_BOOL, "user_id": _at_num("1", 1, 999, True), "role": _AT_STR},
                      {"action": "login", "user": "1", "pin": "1234"}, "登录（用户ID或名称+PIN或密码：登录方式PIN时只接受数字，p.39、p.60）与登出",
                      ["users.state"], "device_command"),
    "maintenance.task": ({"action": {"type": "string", "enum": ["add", "edit", "done", "delete"]}, "name": _EPP_STR, "interval_s": _at_num("s", 60, 1e9),
                          "reminder": _AT_BOOL, "index": _at_num("1", 0, 99, True)}, ("name", "interval_s", "reminder", "index"),
                         {"tasks": _at_num("1", 0, 100, True)}, {"action": "add", "name": "Check rotor", "interval_s": 2592000.0},
                         "Maintenance and Qualification > Recurring Tasks：添加、编辑、完成、删除维护周期任务与提醒（p.73-74）",
                         ["maintenance.tasks", "events.list"], "configuration"),
    "standby.set": ({"active": _AT_BOOL}, (), {"standby": _AT_BOOL}, {"active": True}, "Standby：进入或退出待机（p.38）", ["centrifuge.status"],
                    "device_command"),
    "screen.clean": ({"action": {"type": "string", "enum": ["lock", "tap"]}, "corner": _at_num("1", 1, 4, True)}, ("corner",), {"locked": _AT_BOOL},
                     {"action": "lock"}, "Clean screen：锁定触摸屏以便清洁，按编号顺序点四角解锁（p.45）", ["centrifuge.status"], "device_command"),
}
_EPP_TERMINAL = tuple(_EPP_CAPDEFS)
_EPP_ERROR_TEXT = {"STANDBY": "The centrifuge is in standby.", "LOCKED": "The touchscreen is locked for cleaning.", "LOGIN_REQUIRED": "Log in first.",
                   "PERMISSION_DENIED": "Your user role has no right for this function.", "LID_OPEN": "Close the centrifuge lid.",
                   "LID_LOCKED": "The lid stays locked until the rotor has stopped.", "ROTOR_LIMIT": "The value exceeds the rotor's limit.",
                   "NO_ROTOR": "No rotor detected (rcf needs the rotor radius).", "PROGRAM_LOCKED": "The program is locked.",
                   "NOT_FOUND": "The entry does not exist.", "NO_USB": "No USB storage medium is connected.",
                   "NO_NETWORK": "No network connection / time server.", "WRONG_PIN": "Wrong user ID or PIN / password.",
                   "BOOTING": "The software is starting."}
_EPP_LIMITS = [
    "5910 Ri terminal: set points with changes during a run (segmented speed profile, thermal segments), Start / Stop / Open, Short Spin, Fast Temp, "
    "favorites, 99 programs, events and run records, settings, user management with login and rights, recurring tasks, rotor health, clean "
    "lock and standby follow the 5910 Ri operating manual as a minimal model, not Eppendorf firmware.",
    "rcf uses the largest slot radius of the trusted host's synthetic rotor (or the entered radius); rotor names are labels of the two synthetic "
    "rotor categories. Time below 10 s is not accepted (CHEM-010 method range 10 s .. 99 h 59 min); Continuous runs end with Stop.",
    "Continuous runs (method time 99 h 59 min) need a host curve sample interval of at least 36 s because of the CHEM-010 10000-sample curve "
    "cap; with a shorter interval Start is refused (INVALID_ARGUMENT). Run records: Stop gives 'Completed with warnings', power loss 'Failed' "
    "(our mapping; the manual lists the three results without the rule).",
    "Not simulated: USB export (the device shows that no USB medium is connected), network / VisioNize / time server, imbalance calibration, "
    "self-signed certificate and extended network log (service only).",
    "Language: the selected language is stored, made active at the next switch-on (p.72) and reported in device.system; the screen texts stay "
    "English (our manuals contain no translated text modules).  Time zone: the shown local time moves with the zone (the clock itself runs on).  "
    "Login Mode: PIN (digits, 4-8) or Password (text, 4-16; lengths assumed); credentials made in the other mode stay until reset.",
    "Callers: a run started from the panel or a terminal command belongs to the calling session (poll / cancel); only that caller can stop or "
    "change it (Stop, set points during the run), as the direct centrifuge.stop; the evaluator is unrestricted.  The mains switch (power.set "
    "models it) is a hard switch: anyone may switch it off; a running rotor coasts down as at a power loss and the lid stays locked until it stops.  The direct centrifuge.measure is refused during the 90 s "
    "software start (BOOTING) and in standby (STANDBY), as Start.",
]


def _epp_caps():
    caps = {}
    for name, (inputs, optional, outputs, example, desc, effects, kind) in _EPP_CAPDEFS.items():
        caps[name] = {"function_zh": desc, "kind": kind, "input_schema": _at_obj(inputs, optional), "output_schema": _at_obj(outputs, list(outputs)),
                      "example_arguments": [example], "state_ids": list(dict.fromkeys(effects)), "component_ids": ["display"],
                      "preconditions": ["Power on, software started, not in standby or clean lock; user rights when user management is on."],
                      "scene_required": False, "availability": "implemented", "visibility": "agent",
                      "invocation": "Eppendorf5910Ri.invoke(capability_id=" + repr(name) + ", arguments=..., request_id=...)",
                      "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "BUSY", "IMBALANCE", "MISSING_PARAMETER", "REQUEST_CONFLICT"] +
                      sorted(_EPP_ERROR_TEXT), "rejection_side_effects": "none", "scene_failure_side_effects": "unknown", "asynchronous": False,
                      "partial_failure_zh": "拒绝时不提交状态；运行类命令的减速与停转随宿主时钟进行。", "state_effect_timing": {s: "on_success" for s in effects}}
    every = sorted({s for c in caps.values() for s in c["state_ids"]} | {"power.enabled"})
    for name, entry in _at_panel_cap_entries(EPP5910_PANEL_CAPS, every, "Eppendorf5910Ri.invoke(capability_id='{}', arguments=..., request_id=...)").items():
        entry.update(function_zh=entry["description_zh"], asynchronous=False, partial_failure_zh="一个手势可依次调用多个命令，已提交的不回滚。",
                     state_effect_timing={s: "on_success" for s in entry["state_ids"]})
        caps[name] = entry
    return caps


EPP5910_PANEL_PATH = EPP5910_ASSET_DIR / "panel_ui.json"
_EPP_BASE_CAPS = tuple(export_eppendorf_5910ri_contract_metadata()["capabilities"])
EPP5910_PANEL_SPEC = (_at_pu.load_spec(EPP5910_PANEL_PATH, capabilities=set(_EPP_TERMINAL) | set(_EPP_BASE_CAPS))
                      if EPP5910_PANEL_PATH.is_file() and not _at_os.environ.get("G00_PANEL_BUILD") else None)
EPP5910_PANEL_CAPS = _at_pu.capability_definitions(EPP5910_PANEL_SPEC) if EPP5910_PANEL_SPEC else {}
_epp_base_export = export_eppendorf_5910ri_contract_metadata


def export_eppendorf_5910ri_contract_metadata():
    """CHEM-010 metadata plus the touch terminal: device slots, terminal commands and panel.* capabilities."""
    meta = _epp_base_export()
    meta["asset_version"] = EPP5910_ASSET_VERSION
    meta["state_slots"].update(_epp_slots())
    meta["capabilities"].update(_epp_caps())
    meta["limitations"] = meta["limitations"] + _EPP_LIMITS
    meta["terminal_capabilities"] = sorted(_EPP_TERMINAL) + sorted(EPP5910_PANEL_CAPS)
    return meta


# ---------------------------------------------------------------- what the screen shows
def _epp_fmt_time(s):
    s = int(round(s))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    parts = ([f"{h} h"] if h else []) + ([f"{m} min"] if m else []) + ([f"{sec} s"] if sec or not (h or m) else [])
    return " ".join(parts)


def _epp_texts(host, values):
    dev, st = host._device, host._state
    sp = dev["sp"]
    r = _epp_max_radius(host, st)
    radius = sp["radius_m"] or r
    speed_set = (f"{_epp_rpm_of(sp['speed_rad_s']):.0f} rpm" if sp["speed_mode"] == "rpm" or radius is None else
                 f"{_epp_rcf(sp['speed_rad_s'], radius):.0f} x g")
    cur = st["display.speed_rad_s"] or 0.0
    speed_cur = f"{_epp_rpm_of(cur):.0f} rpm" if sp["speed_mode"] == "rpm" or radius is None else f"{_epp_rcf(cur, radius):.0f} x g"
    op = host._operations.get(st["run.operation_id"])
    running = op is not None and op["status"] == "running"
    remaining = None
    if running and op["decel_start_s"] != float("inf"):
        remaining = max(0.0, op["decel_start_s"] - st["run.elapsed_s"])
    t_cur = "--" if st["display.temperature_k"] is None else f"{st['display.temperature_k'] - 273.15:.0f} °C"
    fast = dev["special"] is not None and dev["special"]["kind"] == "fast_temp"
    return dict(time_set="Continuous" if sp["continuous"] else _epp_fmt_time(sp["duration_s"]),
                time_cur=("--" if not running else "Continuous" if remaining is None else _epp_fmt_time(remaining)) if running else "0 s",
                temp_set=f"{sp['temperature_k'] - 273.15:.0f} °C", temp_cur=("Ready" if dev["notice"] and dev["notice"]["text"] == "Fast Temp finished"
                                                                          else t_cur),
                speed_set=speed_set, speed_cur=speed_cur, ramps=f"/ {sp['accel']}   \\ {sp['brake']}",
                radius="" if sp["speed_mode"] == "rpm" or radius is None else f"Radius {radius * 100:.1f} cm",
                rotor=EPP_ROTORS.get(st["rotor.configuration"], "No rotor"), datetime=_at_now(dev, host._now)[11:16],
                status=("Preparing Temperature" if fast and running else "Short Spin" if dev["special"] and running else
                        "Centrifugation" if running else ("Lid open" if not st["lid.closed"] else "Ready")),
                progress=None if not running or remaining is None or op["decel_start_s"] in (0, float("inf")) else
                round(min(1.0, st["run.elapsed_s"] / op["decel_start_s"]), 4),
                user="" if not dev["users"]["enabled"] else (next((u["name"] for u in dev["users"]["list"] if u["id"] == dev["users"]["current"]),
                                                                  "Login")))


def _epp_fav_label(cat, v, host):
    if cat == "time":
        return _epp_fmt_time(v)
    if cat == "temperature":
        return f"{v - 273.15:.0f} °C"
    return f"{v['value']:.0f} {'rpm' if v['mode'] == 'rpm' else 'rcf'}"


# ---------------------------------------------------------------- runtime and session with the terminal
_EPPBaseRuntime = Eppendorf5910Ri
_EPPBaseSession = Eppendorf5910RiSession


class Eppendorf5910Ri(_EPPBaseRuntime):
    """CHEM-010 host with the 5910 Ri touch terminal: the device model (self._device) and the front panel (self.panel)."""

    def __init__(self, instance_id, *, scene_adapter=None):
        super().__init__(instance_id, scene_adapter=scene_adapter)
        self._device = _epp_new_device()
        self._visibility = {"__agent__": "agent", "__panel__": "agent"}
        self._principals.add("__panel__")
        self._panel_session = Eppendorf5910RiSession(self, "__panel__")     # internal principal of commands without a calling session
        self._caller = None                                                 # principal of the session running a terminal command / gesture
        self._booting_until = None
        self.panel_capture_dir = None
        self.panel = _at_pu.PanelUI(EPP5910_PANEL_SPEC, _ATPanelHost(self)) if EPP5910_PANEL_SPEC else None

    def register_principal(self, principal_id, visibility="agent"):
        """Host identity provisioning; visibility 'evaluator' / 'debug' may call evaluator-only capabilities (panel.frame)."""
        if visibility not in ("agent", "evaluator", "debug"):
            raise ValueError("Invalid visibility")
        super().register_principal(principal_id)
        self._visibility[principal_id] = visibility

    def debug_snapshot(self):
        with self._lock:
            out = super().debug_snapshot()
            out["device"] = _epp_copy.deepcopy(self._device)
            return out

    def _time(self):
        return self._now

    def host_advance(self, time_s):
        """CHEM-010 host_advance, then the terminal's clock-driven behaviour (Fast Temp in 1 s steps so it ends at the set temperature)."""
        with self._lock:
            if not _epp_finite(time_s) or time_s < self._now:
                raise ValueError("Host time must be finite, nonnegative and monotonic")
            while True:
                step_to = min(time_s, self._now + 1.0) if self._device["special"] is not None else time_s
                super().host_advance(step_to)
                dev, state, ops = (_epp_copy.deepcopy(x) for x in (self._device, self._state, self._operations))
                _epp_tick(self, dev, state, ops, self._now)
                self._device, self._state, self._operations = dev, state, ops
                if self._booting_until is not None and self._now >= self._booting_until:
                    self._booting_until = None
                if self._now >= time_s - 1e-12:
                    break
            return self._agent.read()

    def _values(self):
        dev, st = self._device, self._state
        values = dict(st)
        rmax = _epp_max_radius(self, st)
        values.update({
            "centrifuge.set_points": dict(dev["sp"], rcf=None if rmax is None else _epp_rcf(dev["sp"]["speed_rad_s"], dev["sp"]["radius_m"] or rmax),
                                          max_radius_m=rmax),
            "centrifuge.status": dict(special=None if dev["special"] is None else dev["special"]["kind"], standby=dev["standby"],
                                      clean_lock=dev["clean_lock"] is not None, booting=self._booting_until is not None,
                                      rotor=EPP_ROTORS.get(st["rotor.configuration"])),
            "favorites.list": _epp_copy.deepcopy(dev["favorites"]),
            "programs.list": [dict(number=int(n), name=p["name"], locked=p["locked"], set_points=dict(p["sp"]),
                                   compatible=self._profiles.get(st["rotor.configuration"]) is not None and
                                   p["sp"]["speed_rad_s"] <= self._profiles[st["rotor.configuration"]]["operating_speed_limit_rad_s"])
                              for n, p in sorted(dev["programs"].items(), key=lambda x: int(x[0]))],
            "events.list": _epp_copy.deepcopy(dev["events"][:200]), "runs.records": _epp_copy.deepcopy(dev["runs"]),
            "device.settings": dict(dev["settings"]), "maintenance.tasks": _epp_copy.deepcopy(dev["tasks"]),
            "users.state": dict(enabled=dev["users"]["enabled"], current=dev["users"]["current"], role=_epp_role(dev),
                                users=[{k: v for k, v in u.items() if k != "pin"} for u in dev["users"]["list"]]),
            "rotor.health": dict(cycles=dict(dev["rotor_cycles"]), max_cycles=None), "device.datetime": _at_now(dev, self._now),
            "device.notice": None if dev["notice"] is None else dict(dev["notice"]),
            "device.system": dict(language=dev["active_language"], restart_needed=dev["restart_needed"], power_epoch=dev["power_epoch"],
                                  serial="SIM-5910RI-0001", article="5910 Ri (simulated)", software="sim-1.2.0"),
        })
        return values

    def _display(self):
        dev, s = self._device, self._device["settings"]
        if not self._state["power.enabled"]:
            return 0, "off"
        if dev["standby"]:
            return 0, "standby"
        if s["display.energy_save"] and self._now - dev["last_activity_s"] >= s["display.timeout_s"] - 1e-9:
            return min(20, s["display.brightness"]), "dim"
        return s["display.brightness"], "on"

    def panel_values(self):
        values = self._values()
        slots = self._metadata["state_slots"]
        out = {k: v for k, v in values.items() if slots.get(k, {}).get("visibility") == "agent"}
        dev = self._device
        bright, mode = self._display()
        tx = _epp_texts(self, values)
        favs = {cat: [dict(position=i, text=_epp_fav_label(cat, v, self)) for i, v in enumerate(dev["favorites"][cat])] for cat in dev["favorites"]}
        progs = {int(n): p for n, p in dev["programs"].items()}
        out.update({"host.powered": self._state["power.enabled"], "host.power_epoch": dev["power_epoch"], "host.booting": self._booting_until is not None,
                    "host.brightness": bright, "host.display": mode, "host.text": tx, "host.favorites": favs,
                    "host.programs": [dict(number=n, text=f"{n:02d}  " + (progs[n]["name"] + ("  [locked]" if progs[n]["locked"] else "") +
                                                                          ("" if values["programs.list"][[p["number"] for p in values["programs.list"]].index(n)]
                                                                           ["compatible"] else "  [rotor X]") if n in progs else "--  empty --"),
                                           empty=n not in progs, locked=progs[n]["locked"] if n in progs else False, name=progs[n]["name"] if n in progs else "")
                                      for n in range(1, 100)],
                    "host.role": _epp_role(dev), "host.can": {r: _epp_allowed(dev, r) for r in EPP_RIGHTS},
                    "host.login_needed": dev["users"]["enabled"] and _epp_role(dev) is None,
                    "host.running": self._busy(), "host.unacked": sum(1 for e in dev["events"] if not e["acknowledged"]),
                    "host.runs": [dict(id=r["id"], text=f"{r['start'][:16].replace('T', ' ')}  {r['kind']}", result=r["result"],
                                       detail=[f"Result: {r['result']}", f"Time: {_epp_fmt_time(r['params']['duration_s']) if not r['params']['continuous'] else 'Continuous'}",
                                               f"Temperature: {r['params']['temperature_k'] - 273.15:.0f} °C",
                                               f"Speed: {_epp_rpm_of(r['params']['speed_rad_s']):.0f} rpm", f"Ramps: {r['params']['accel']} / {r['params']['brake']}",
                                               f"Start: {r['start']}", f"End: {r['end']}", f"Rotor: {r['rotor']}"] + r["events"][:3]) for r in dev["runs"]],
                    "host.health": [[name, str(dev["rotor_cycles"][k])] for k, name in EPP_ROTORS.items()],
                    "host.about": [["Name", dev["settings"]["about.name"]], ["Registration Number", dev["settings"]["about.registration"] or "--"],
                                   ["Location", dev["settings"]["about.location"] or "--"], ["Article Number", "5910 Ri (simulated)"],
                                   ["Serial Number", "SIM-5910RI-0001"], ["Software version", "sim-1.2.0"], ["License Information", "see device"]],
                    "host.clean_next": None if dev["clean_lock"] is None else dev["clean_lock"]["next"],
                    "host.credentials": dev["credentials"] or {}})
        return out

    def panel_command(self, capability_id, arguments):
        """A command of a panel gesture runs as the session that made the gesture (self._caller, set by Eppendorf5910RiSession._panel)."""
        return _epp_session(self)._invoke_once(capability_id, arguments, "__panel__")

    def _after_base(self, capability_id, arguments, state):
        dev = self._device
        if capability_id == "power.set":
            if arguments["enabled"]:
                dev["power_epoch"] += 1
                self._booting_until = self._now + _EPP_BOOT_S
                dev.update(standby=False, clean_lock=None, notice=None, active_language=dev["settings"]["device.language"], restart_needed=False)
                dev["users"]["current"] = None
                dev["last_activity_s"] = self._now
                _epp_event(dev, self._now, "info", "System", "Device switched on")
            else:
                dev["special"] = None
                _epp_event(dev, self._now, "info", "System", "Device switched off")
        elif capability_id == "centrifuge.set_method" and dev["current_run"] is None:
            dev["sp"].update(speed_rad_s=arguments["speed_rad_s"], temperature_k=arguments["temperature_k"], duration_s=arguments["duration_s"],
                             continuous=False, accel=arguments["acceleration_level"] - 1, brake=arguments["braking_level"] - 1)


class Eppendorf5910RiSession(_EPPBaseSession):
    """CHEM-010 session plus the terminal commands and panel.*."""
    _messages = dict(_EPPBaseSession._messages, **_EPP_ERROR_TEXT)

    def _vis(self):
        return getattr(self._host, "_visibility", {}).get(self._principal_id, "agent")

    def describe(self, *, asset_type_id=None, instance_id=None):
        out = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if "capabilities" in out and self._vis() not in ("evaluator", "debug"):
            out["capabilities"] = {k: c for k, c in out["capabilities"].items() if c.get("visibility", "agent") == "agent"}
        return out

    def read(self, *, state_ids=None, instance_id=None):
        h = self._host
        with h._lock:
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            values = h._values()
            allowed = [k for k, slot in h._metadata["state_slots"].items() if slot["visibility"] == "agent"]
            if state_ids is None:
                state_ids = allowed
            if not isinstance(state_ids, list) or any(not isinstance(k, str) for k in state_ids):
                return self._response("rejected", code="INVALID_ARGUMENT")
            if any(k not in allowed for k in state_ids):
                return self._response("rejected", code="ACCESS_DENIED")
            observations = {}
            for key in state_ids:
                value = values[key]
                estimated = key.startswith("display.") or key in ("alarm.imbalance", "measurement.curve")
                observations[key] = {"value": _epp_copy.deepcopy(value), "unit": h._metadata["state_slots"][key]["unit"],
                                     "quality": "unavailable" if value is None else ("estimated" if estimated else "valid"),
                                     "sample_time_s": h._now, "sample_window_s": None}
                if value is None:
                    observations[key]["reason"] = "Not configured, not loaded or not yet acquired"
            return self._response(observations=observations)

    def _inner(self, capability_id, arguments, state, ops):
        """A CHEM-010 command applied to candidate state / operations (its own checks and effects); returns the error code or None."""
        h = self._host
        saved = (h._state, h._operations, h._revision)
        h._state, h._operations = state, ops
        r = _EPPBaseSession._invoke_once(self, capability_id, arguments, "__panel_inner__")
        committed_state, committed_ops = h._state, h._operations
        h._state, h._operations, h._revision = saved
        if r["status"] not in ("succeeded", "accepted"):
            return r["error"]["code"]
        state.clear()
        state.update(committed_state)
        ops.clear()
        ops.update(committed_ops)
        return None

    def _invoke_once(self, capability_id, arguments, request_id):
        h = self._host
        if capability_id not in _EPP_TERMINAL and capability_id not in EPP5910_PANEL_CAPS:
            if capability_id == "centrifuge.measure" and isinstance(arguments, dict) and not arguments:
                if h._booting_until is not None:                              # the same instrument: no run during the software start (p.27)
                    return self._response("rejected", request_id=request_id, code="BOOTING")
                if h._device["standby"]:                                      # nor in standby (p.38)
                    return self._response("rejected", request_id=request_id, code="STANDBY")
            op = h._operations.get(h._state["run.operation_id"])
            if capability_id == "centrifuge.stop" and isinstance(arguments, dict) and not arguments and op is not None and op["status"] == "running" \
                    and op["owner"] != self._principal_id and _epp_privileged(h, self._principal_id):   # another caller's run (lead rule 2026-10-19):
                return Eppendorf5910RiSession(h, op["owner"])._invoke_once(capability_id, arguments, request_id)   # the evaluator stops it for its owner
            # power.set models the mains switch, a hard switch (lead clarification 2026-10-19): never refused; the rotor coasts down as at a power loss
            response = super()._invoke_once(capability_id, arguments, request_id)
            if response["status"] in ("succeeded", "accepted") and capability_id in ("power.set", "centrifuge.set_method"):
                h._after_base(capability_id, arguments, h._state)
            return response
        cap = h._metadata["capabilities"][capability_id]
        if _at_validate(cap["input_schema"], arguments):
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        if cap["visibility"] != "agent" and self._vis() not in ("evaluator", "debug"):
            return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
        if capability_id.startswith("panel."):
            return self._panel(capability_id, arguments, request_id)
        if h._booting_until is not None and capability_id not in ("standby.set",):
            return self._response("rejected", request_id=request_id, code="BOOTING")
        state, ops, dev = (_epp_copy.deepcopy(x) for x in (h._state, h._operations, h._device))
        counter = h._run_counter
        caller, h._caller = h._caller, self._principal_id                     # the command acts as this session's caller
        try:
            result = _EPP_COMMANDS[capability_id](h, dev, state, ops, arguments, h._now)
        except _ATReject as exc:
            h._run_counter = counter
            return self._response("rejected", request_id=request_id, code=exc.code)
        finally:
            h._caller = caller
        try:
            h._sync_candidate(state)
        except RuntimeError:
            h._run_counter = counter
            return self._response("failed", request_id=request_id, code="DEVICE_ERROR", side_effects="unknown")
        h._state, h._operations, h._device = state, ops, dev
        h._revision += 1
        return self._response(request_id=request_id, result=result)

    def _panel(self, capability_id, args, request_id):
        h = self._host
        if h.panel is None:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        try:
            if capability_id == "panel.capture":
                return self._response(request_id=request_id, result=_at_capture(h.panel, h.instance_id, args["display_id"], h._now, h.panel_capture_dir))
            if capability_id == "panel.frame":
                return self._response(request_id=request_id, result={"frame": h.panel.frame()})
            caller, h._caller = h._caller, self._principal_id                 # the gesture's commands run as this session's caller
            try:
                if capability_id == "panel.press":
                    accepted = h.panel.press(args["input_id"], float(args.get("duration_s", 0.1)))
                elif capability_id == "panel.switch":
                    accepted = h.panel.switch(args["input_id"], args["position"])
                else:
                    if ("to_x" in args) != ("to_y" in args):
                        return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
                    to = (args["to_x"], args["to_y"]) if "to_x" in args else None
                    accepted = h.panel.touch(args["display_id"], args["x"], args["y"], float(args.get("duration_s", 0.1)), to=to)
            finally:
                h._caller = caller
        except _at_pu.PanelError:
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        h._device["last_activity_s"] = h._now
        return self._response(request_id=request_id, result={"accepted": bool(accepted)})
# <<< CHEM-010 Centrifuge 5910 Ri touch terminal



# >>> CHEM-071 DR6000 touch terminal
# Device layer of the DR6000 touchscreen (user manual references/manuals/hach_dr6000/, 'p.N' = PDF page): startup (45 s) and self-check
# (about 2 min) with the language list, sleep mode, the Main Menu modes (Stored / Barcode / User Programs, Favorites, Single and Multi
# Wavelength, Wavelength Scan, Time Course) with Zero / Read, their options (dilution, standard adjust, reagent blank, multiple
# determination, reading mode, standard addition, concentration factor, absorbance formula, scan range / step / units / scale / cursor /
# integral / reference, time and interval), user programs (single / multi wavelength, free programming, calibration by values, standards or
# formula), data log, stored scans and time courses, trends, operator and sample IDs, security, date / time, sound, PC & printer, power
# management, lamp control (UV lamp warm-up), data log setup, system checks (wavelength, stray light, absorbance, noise, drift, pipette
# check), AQA, service time, lamp history, the toolbar timer and the front panel (panel_ui.json run by scripts/asset_runtime/_panel_ui.py).
# Readings use the CHEM-071 spectrum model of the cuvette in the compartment (an empty compartment reads as air, absorbance 0); the CHEM-071
# capabilities keep their schemas; the direct spectroscopy.measure needs the ready instrument and its lamps as Zero / Read.  Stored programs = the Hach method records of
# assets/reactions/hach_dr6000_colorimetric_library (wavelength, range, published sensitivity, reaction time).
import functools as _dr_functools
import json as _dr_json
import random as _dr_random

DR6000_ASSET_VERSION = "1.4.0"  # 1.4.0 (2026-10-21): the real 7-inch WVGA window (152.4 x 91.44 mm, plain glass) in the 3D model (geometry only)
DR6000_ASSET_DIR = _AT_ROOT / "assets/basic40_batch02/t3_analytical/hach_dr6000"
_DR_LIBRARY = _AT_ROOT / "assets/reactions/hach_dr6000_colorimetric_library/templates.json"
_DR_STARTUP_S = 45.0                                    # startup with the logo and melody (p.21)
_DR_SELFCHECK_S = 120.0                                 # self-check "approximately two minutes" (p.21)
_DR_UV_WARMUP_S = 60.0                                  # the UV lamp needs time to warm up (p.41; duration assumed)
_DR_ABS_LIMIT = 3.5                                     # "Absorbance > 3.5!" (p.137)
_DR_CONC_LIMIT = 999999.0                               # "Concentration too high!" (p.138)
_DR_ADJUST_LIMIT = 0.25                                 # standard adjust allowed within +-25 % (program-specific in the device; assumption)
DR_LANGUAGES = ("English", "Deutsch", "Français", "Español", "Italiano", "Nederlands", "Polski", "Português", "Svenska", "Dansk", "中文", "日本語")
DR_UNITS = ("mg/L", "µg/L", "g/L", "mmol/L", "mol/L", "ppm", "ppb", "%", "Abs", "-")
DR_STEPS_NM = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0)            # scan steps offered (p.105; values assumed)
DR_FORMULAS = ("K1A1+K2A2", "K1A1+K2A2+K3A3", "K1A1+K2A2+K3A3+K4A4", "K1A1/K2A2", "(K1A1+K2A2)/K3A3", "(K1A1+K2A2)/(K3A3+K4A4)")   # p.102
_DR_FORMULA_N = (2, 3, 4, 2, 3, 4)
DR_PROCESSES = ("[Z]Z11 {[R]R11}", "[Z]Z11 [R]R11", "[Z]Z11 [R]Z21 {[R]R11}", "[Z]Z11 [R]R11 [R]R21", "[Z]Z11 Z22 [R]R11 R12")   # p.90
_DR_PROCESS_STEPS = (([("Z", [("Z11", 1)]), ("R", [("R11", 1)])], 1), ([("Z", [("Z11", 1)]), ("R", [("R11", 1)])], None),
                     ([("Z", [("Z11", 1)]), ("R", [("Z21", 1)]), ("R", [("R11", 1)])], 2),
                     ([("Z", [("Z11", 1)]), ("R", [("R11", 1)]), ("R", [("R21", 1)])], None),
                     ([("Z", [("Z11", 1), ("Z22", 2)]), ("R", [("R11", 1), ("R12", 2)])], None))
DR_FUNCTIONS = ("Stored Programs", "Barcode Programs", "User Programs", "Favorites", "Single Wavelength", "Multi Wavelength", "Wavelength Scan",
                "Time Course", "System Checks", "Recall Data", "Instrument Setup")          # Security List (p.29-31)
DR_LEVELS = ("Off", "one key", "two keys")
DR_TIMER_NAMES = ("Timer", "Reaction time", "Waiting time", "Shaking", "Stirring", "Digestion")
_DR_KIND_FUNCTION = {"stored": "Stored Programs", "barcode": "Barcode Programs", "user": "User Programs", "single": "Single Wavelength",
                     "multi": "Multi Wavelength", "scan": "Wavelength Scan", "time": "Time Course"}
_DR_MIN = (1 / 60.0, 0.0, "min", 0)
DR_SETTINGS = [
    _at_field("datetime.date_format", "Date Format", "enum", "dd-mm-yyyy", "datetime", "manual p.33",
              options=["dd-mm-yyyy", "mm-dd-yyyy", "yyyy-mm-dd", "dd.mm.yyyy"]),
    _at_field("datetime.time_format", "Time Format", "enum", "24h", "datetime", "manual p.33", options=["24h", "12h"]),
    _at_field("sound.all", "All", "bool", True, "sound", "manual p.34"),
    _at_field("sound.touch", "Touch screen", "bool", True, "sound", "manual p.34"),
    _at_field("sound.reading", "Reading done", "bool", True, "sound", "manual p.34"),
    _at_field("sound.timer", "Timer", "enum", "long", "sound", "manual p.34 (short / long sound)", options=["off", "short", "long"]),
    _at_field("sound.startup", "Startup", "bool", True, "sound", "manual p.34"),
    _at_field("sound.warning", "Warning", "bool", True, "sound", "manual p.34"),
    _at_field("sound.shutdown", "Shutdown", "bool", True, "sound", "manual p.34"),
    _at_field("sound.volume", "Volume", "number", 50, "sound", "manual p.34 (variable volume)", min=0, max=100, integer=True),
    _at_field("printer.resolution", "Resolution", "enum", "150 dpi", "printer", "manual p.36", options=["100 dpi", "150 dpi", "300 dpi"]),
    _at_field("printer.paper", "Paper", "enum", "A4", "printer", "manual p.36", options=["Letter", "Monarch", "Executive", "A4"]),
    _at_field("printer.network", "Network Printer", "enum", "Automatic", "printer", "manual p.37", options=["Automatic", "Fixed"]),
    _at_field("printer.ip", "Printer IP address", "text", "192.168.0.20", "printer", "manual p.37 (Fixed)", pattern=_G8_IP),
    _at_field("network.on", "Network", "bool", False, "network", "manual p.38"),
    _at_field("network.ip_mode", "IP address", "enum", "Automatic", "network", "manual p.38", options=["Automatic", "Fix"]),
    _at_field("network.ip", "Fixed IP address", "text", "192.168.0.30", "network", "manual p.38", pattern=_G8_IP),
    _at_field("network.subnet", "Subnet Mask", "text", "255.255.255.0", "network", "manual p.39", pattern=_G8_IP),
    _at_field("network.gateway", "Default Gateway", "text", "192.168.0.1", "network", "manual p.39", pattern=_G8_IP),
    _at_field("network.hostname", "Hostname", "text", "DR6000", "network", "manual p.38", max_chars=20),
    _at_field("network.server", "Network Server", "enum", "Off", "network", "manual p.39", options=["Off", "Netdrive", "FTP"]),
    _at_field("network.server_address", "Server IP / name", "text", "", "network", "manual p.40", max_chars=40),
    _at_field("network.data_folder", "Data Folder", "text", "", "network", "manual p.40", max_chars=40),
    _at_field("network.user", "User", "text", "", "network", "manual p.40", max_chars=20),
    _at_field("power.sleep_timer_s", "Sleep Timer", "enum", 0.0, "power", "manual p.41 (0 = Off)", options=[0.0, 600.0, 1800.0, 3600.0, 7200.0],
              si_unit="s"),
    _at_field("power.uv_save_s", "UV Lamp Save", "enum", 0.0, "power", "manual p.41 (0 = Off)", options=[0.0, 900.0, 1800.0, 3600.0, 7200.0],
              si_unit="s"),
    _at_field("power.vis_save", "Visible Lamp Save", "enum", "Automatic", "power", "manual p.41 (Automatic recommended)",
              options=["Automatic", "Off"]),
    _at_field("lamp.uv_switch_m", "UV Switch", "number", 331e-9, "lamp", "manual p.42 (320-360 nm, default 331 nm)", min=320e-9, max=360e-9,
              si_unit="m", show=(1e9, 0.0, "nm", 0)),
    _at_field("log.auto_store", "Auto Store", "bool", True, "datalog", "manual p.43"),
    _at_field("log.format", "Send Data Format", "enum", "csv", "datalog", "manual p.43", options=["xml", "csv"]),
    _at_field("log.auto_send", "Auto Send", "bool", False, "datalog", "manual p.43"),
]
DR_PROGRAM_SETTINGS = [
    _at_field("name", "Program Name", "text", "", "program", "manual p.78 (max 28 characters)", min_chars=1, max_chars=28),
    _at_field("type", "Program Type", "enum", "single", "program", "manual p.78-79 (Table 8)", options=["single", "multi", "free"]),
    _at_field("unit", "Unit", "enum", "mg/L", "program", "manual p.79", options=list(DR_UNITS)),
    _at_field("wavelength_m", "Wavelength", "number", 500e-9, "program", "manual p.79 (190-1100 nm)", min=190e-9, max=1100e-9, si_unit="m",
              show=(1e9, 0.0, "nm", 1)),
    _at_field("resolution", "Concentration resolution", "enum", 2, "program", "manual p.79 (decimal places)", options=[0, 1, 2, 3, 4]),
    _at_field("form", "Chemical form", "text", "", "program", "manual p.80", max_chars=12),
    _at_field("formula", "Absorbance formula", "enum", 0, "program", "manual p.80, p.102", options=list(range(len(DR_FORMULAS)))),
    _at_field("calib_method", "Calibration", "enum", "values", "program", "manual p.82 (Table 9)", options=["values", "standards", "formula"]),
    _at_field("calib_order", "Calibration curve", "enum", 1, "program", "manual p.83 (linear, polynomial 2nd / 3rd order)", options=[1, 2, 3]),
    _at_field("force0", "Force 0", "bool", False, "program", "manual p.83"),
    _at_field("lower", "Lower limit", "number", None, "program", "manual p.85", min=-1e6, max=1e6, nullable=True),
    _at_field("upper", "Upper limit", "number", None, "program", "manual p.85", min=-1e6, max=1e6, nullable=True),
    _at_field("process", "Measurement process", "enum", 1, "program", "manual p.90 (List)", options=list(range(len(DR_PROCESSES)))),
    _at_field("expression", "Formula C1", "text", "F1*(R11-Z11)", "program", "manual p.90-95 (entered as text)", min_chars=1, max_chars=60),
    _at_field("designation", "Designation", "text", "C1", "program", "manual p.92", max_chars=12),
] + [_at_field(f"wl{i}_m", f"Wavelength {i}", "number", (500e-9, 600e-9, 700e-9, 800e-9)[i - 1], "program", "manual p.81 (190-1100 nm)",
               min=190e-9, max=1100e-9, si_unit="m", show=(1e9, 0.0, "nm", 1)) for i in range(1, 5)] + \
    [_at_field(f"k{i}", f"K{i}", "number", 1.0, "program", "manual p.81 (up to 5 digits, 4 decimals)", min=-99999, max=99999) for i in range(1, 5)] + \
    [_at_field(f"coef_{c}", f"Factor {c}", "number", 0.0 if c != "b" else 1.0, "program", "manual p.85 (5 digits, sign)", min=-99999, max=99999)
     for c in "abcd"] + \
    [_at_field(f"f{i}", f"F{i}", "number", 1.0, "program", "manual p.95 (variables)", min=-99999, max=99999) for i in range(1, 5)] + \
    [_at_field(f"timer{i}_s", f"Timer {i}", "number", None, "program", "manual p.86 (mm:ss)", min=1.0, max=5999.0, si_unit="s", nullable=True)
     for i in range(1, 5)] + \
    [_at_field(f"form{i}", f"Chemical form {i}", "text", "", "program", "manual p.86", max_chars=12) for i in range(2, 5)] + \
    [_at_field(f"form{i}_factor", f"Conversion factor {i}", "number", 1.0, "program", "manual p.86", min=1e-6, max=1e6) for i in range(2, 5)]
DR_KIT_FILTERS = {                                      # Verification Kit LZV537: six precision glass filters (p.114, p.144; values assumed)
    "SL340": dict(name="Stray light filter", flat=4.0), "NG05": dict(name="Neutral glass 0.5", flat=0.5),
    "NG10": dict(name="Neutral glass 1.0", flat=1.0), "NG20": dict(name="Neutral glass 2.0", flat=2.0),
    "HO": dict(name="Holmium glass", base=0.2, bands=[(536.4e-9, 3e-9, 0.8), (445.8e-9, 3e-9, 0.6)]),
    "DI": dict(name="Didymium glass", base=0.15, bands=[(585.6e-9, 4e-9, 0.9)]),
}
_DR_KIT_NOMINAL = [                                     # quality control certificate lines entered under Nominal Values (p.115; defaults assumed)
    dict(filter="SL340", test="stray", wavelength_m=340e-9, nominal=3.0, tolerance=0.0),
    dict(filter="NG05", test="photometric", wavelength_m=546e-9, nominal=0.5, tolerance=0.005),
    dict(filter="NG10", test="photometric", wavelength_m=546e-9, nominal=1.0, tolerance=0.01),
    dict(filter="NG20", test="photometric", wavelength_m=546e-9, nominal=2.0, tolerance=0.02),
    dict(filter="HO", test="wavelength", wavelength_m=536.4e-9, nominal=536.4e-9, tolerance=1e-9),
    dict(filter="DI", test="wavelength", wavelength_m=585.6e-9, nominal=585.6e-9, tolerance=1e-9)]
_DR_ERRORS0 = dict(wavelength_offset_m=0.0, stray_light_fraction=0.0, photometric_gain=0.0, photometric_offset_abs=0.0, noise_sd_abs=0.0,
                   drift_abs_per_h=0.0)                 # evaluator optics error parameters; 0 = ideal optics
_DR_CRITERIA = dict(stray_light=dict(required=3.0), absorbance=dict(nominal=None, tolerance=0.01), noise=dict(limit=0.001), drift=dict(limit=0.005),
                    wavelength=dict(tolerance=1e-9))    # pass / fail criteria of the optical checks (datasheet / user-set; defaults assumed)
_DR_SINGLE = dict(wavelength_m=500e-9, result_mode="abs", conc_factor=None, conc_unit="mg/L", resolution=3, reading_mode="single")
_DR_MULTI = dict(formula=0, wavelengths_m=[500e-9, 600e-9, 700e-9, 800e-9], k=[1.0, 1.0, 1.0, 1.0], result_mode="abs", conc_factor=None,
                 conc_unit="mg/L", resolution=3)
_DR_SCAN = dict(start_m=400e-9, stop_m=700e-9, step_m=1e-9, units="abs", scale="auto", y_min=0.0, y_max=1.0, cursor="track", integral=False,
                reference=None, view="graph")
_DR_TIME = dict(total_s=600.0, interval_s=10.0, wavelength_m=500e-9, units="abs", scale="auto", y_min=0.0, y_max=1.0, view="graph")


@_dr_functools.lru_cache(maxsize=1)
def _dr_library():
    """Stored programs: Hach method records with a published wavelength and sensitivity (number, name, wavelength, range, sensitivity)."""
    try:
        recs = _dr_json.loads(_DR_LIBRARY.read_text())["templates"]
    except (OSError, ValueError, KeyError):
        return {}
    out = {}
    for r in recs:
        if r.get("wavelength_m") and r.get("sensitivity") and r.get("program_number"):
            name = r.get("program_name") or f"{r['program_number']} {r['name']}"
            out[int(r["program_number"])] = dict(number=int(r["program_number"]), name=name, label=name.split(" ", 1)[1] if " " in name else name,
                                                 wavelength_m=float(r["wavelength_m"]), range_kg_m3=list(r["range_kg_m3"]),
                                                 per_abs_kg_m3=r["sensitivity"]["delta_concentration_kg_m3"] / r["sensitivity"]["delta_absorbance"],
                                                 form=r.get("reported_as") or "", timer_s=r.get("reaction_time_s"), method=r.get("method_number"),
                                                 template_id=r["substance_id"])
    return out


def _dr_decimals(high_mg_l):
    return 3 if high_mg_l <= 2 else 2 if high_mg_l <= 20 else 1 if high_mg_l <= 200 else 0       # display resolution (assumption)


def _dr_new_device():
    return dict(clock_offset_s=0.0, phase="off", phase_until=None, fault=None, lang_pick=False, language="English", power_epoch=0,
                settings=_at_defaults(DR_SETTINGS), operators=[], operator=None, sample_ids=[], sample_id=None,
                security=dict(enabled=False, password=None, levels={f: "Off" for f in DR_FUNCTIONS}),
                lamps=dict(uv_on=False, uv_since=None, vis_on=False, uv_hours_s=0.0, vis_hours_s=0.0, uv_cycles=0, vis_cycles=0, uv_ignitions=0,
                           uv_last_use=0.0),
                single=dict(_DR_SINGLE), multi=_at_copy.deepcopy(_DR_MULTI), scan=dict(_DR_SCAN), time=dict(_DR_TIME),
                mode=None, zero=None, result=None, scan_data=None, tc=None, free=None, std_add=None,
                stored_scans={}, stored_tc={}, data_log=[], log_seq=0, aqa_log=[], favorites=[], user_programs={}, draft=None, trends=[],
                aqa=dict(standards=[], addition=dict(on=False, interval_days=7), multiple=dict(on=False, interval_days=7, count=3, tolerance_pct=5.0),
                         dilution=dict(on=False, interval_days=7, factor=2.0, tolerance_pct=5.0), pipettes=[], pending=None, last={}),
                service=dict(enabled=False, last_date=None, interval_days=365), timer=None, checks={}, drift=None, check_log=[],
                kit=dict(placed=None, nominal=_at_copy.deepcopy(_DR_KIT_NOMINAL)), verification=None, errors=dict(_DR_ERRORS0), ratios=[],
                notice=None, last_activity_s=0.0)


def _dr_event_now(dev, t):
    return _at_now(dev, t)


def _dr_ready(dev, state):
    _at_need(state["power.enabled"], "PRECONDITION_FAILED")
    _at_need(dev["phase"] != "sleep", "SLEEP")
    _at_need(dev["phase"] == "ready", "NOT_READY")


def _dr_operator_level(dev):
    op = next((o for o in dev["operators"] if o["name"] == dev["operator"]), None)
    return DR_LEVELS.index(op["level"]) if op else 0


def _dr_security(dev, function, password):
    """Security List (p.29-33): a function with level one key / two keys needs an operator of that level, otherwise the security password."""
    sec = dev["security"]
    if not sec["enabled"]:
        return
    need = DR_LEVELS.index(sec["levels"].get(function, "Off"))
    if need == 0 or _dr_operator_level(dev) >= need:
        return
    if password is None:
        raise _ATReject("LOCKED")
    _at_need(password == sec["password"], "WRONG_PASSWORD")


def _dr_uv_needed(dev, wavelengths_m):
    return any(w < dev["settings"]["lamp.uv_switch_m"] - 1e-15 for w in wavelengths_m)


def _dr_lamps_for(rt, dev, wavelengths_m, t):
    """The UV lamp is the source below the UV switch wavelength (p.42); switched off it is switched on and must warm up ('Lamp Warmup',
    p.116): the reading is refused until it is warm.  The VIS lamp in Automatic mode is switched on only for the reading (p.41)."""
    lamps = dev["lamps"]
    if _dr_uv_needed(dev, wavelengths_m):
        if not lamps["uv_on"]:
            committed = rt._device["lamps"]                                         # the lamp is switched on even though the reading waits
            committed.update(uv_on=True, uv_since=t, uv_cycles=committed["uv_cycles"] + 1, uv_ignitions=committed["uv_ignitions"] + 1, uv_last_use=t)
            raise _ATReject("LAMP_WARMUP")
        if t - lamps["uv_since"] < _DR_UV_WARMUP_S - 1e-9:
            raise _ATReject("LAMP_WARMUP")
        lamps["uv_last_use"] = t
    if any(w >= dev["settings"]["lamp.uv_switch_m"] - 1e-15 for w in wavelengths_m):
        if not lamps["vis_on"]:
            _at_need(dev["settings"]["power.vis_save"] == "Automatic", "LAMP_OFF")
            lamps["vis_cycles"] += 1


def _dr_filter_abs(f, w):
    """Absorbance of a verification kit glass filter: flat (neutral glass, stray light cut-off) or bands on a base (holmium, didymium)."""
    if "flat" in f:
        return f["flat"]
    return f["base"] + sum(a * _at_math.exp(-0.5 * ((w - c) / sd) ** 2) for c, sd, a in f["bands"])


def _dr_optics(rt, state, values, wavelengths_m, at=None):
    """The evaluator's optics errors (all 0 = ideal): stray light fraction s (T' = (T + s) / (1 + s)), photometric gain and offset, drift
    (Abs per hour of host time) and noise (deterministic pseudo-random, seeded by instance, reading number, wavelength and time)."""
    err = rt._device["errors"]
    if not any(err.values()):
        return values
    t = rt._time() if at is None else at
    out = []
    for i, (a, w) in enumerate(zip(values, wavelengths_m)):
        s = err["stray_light_fraction"]
        if s:
            a = -_at_math.log10((10.0 ** -a + s) / (1.0 + s))
        a = a * (1.0 + err["photometric_gain"]) + err["photometric_offset_abs"] + err["drift_abs_per_h"] * t / 3600.0
        if err["noise_sd_abs"]:
            a += _dr_random.Random(f"{rt.instance_id}:{state['measurement.count']}:{i}:{round(w * 1e12)}:{t}").gauss(0.0, err["noise_sd_abs"])
        out.append(a)
    return out


def _dr_absorbance(rt, state, wavelengths_m, at=None):
    """Absorbance (against air) of what is in the cell compartment at each wavelength: a verification kit filter, the CHEM-071 spectrum model
    of the loaded cuvette (synthetic peaks or template), or 0 for an empty compartment; then the optics errors.  NO_EVALUATION when the
    cuvette's model has no value there (template range).  A wavelength offset shifts where the instrument really measures."""
    dw = rt._device["errors"]["wavelength_offset_m"]
    placed = rt._device["kit"]["placed"]
    if placed is not None:
        raw = [_dr_filter_abs(DR_KIT_FILTERS[placed], w + dw) for w in wavelengths_m]
    elif not state["sample.loaded"]:
        raw = [0.0] * len(wavelengths_m)
    else:
        raw = []
        for w in wavelengths_m:
            probe = dict(state, **{"scan.start_m": w + dw, "scan.stop_m": w + dw, "scan.step_m": 1e-9, "measurement.mode": "absorbance"})
            sp = rt._raw_spectrum(probe)
            if sp is None or len(sp["absorbance"]) != 1:
                raise _ATReject("NO_EVALUATION")
            raw.append(float(sp["absorbance"][0]))
    return _dr_optics(rt, state, raw, wavelengths_m, at)


def _dr_scan_absorbance(rt, state, start_m, stop_m, step_m):
    n = int(_at_math.floor((stop_m - start_m) / step_m + 1e-8)) + 1
    axis = [start_m + i * step_m for i in range(n)]
    dw = rt._device["errors"]["wavelength_offset_m"]
    placed = rt._device["kit"]["placed"]
    if placed is not None:
        return axis, _dr_optics(rt, state, [_dr_filter_abs(DR_KIT_FILTERS[placed], w + dw) for w in axis], axis)
    if not state["sample.loaded"]:
        return axis, _dr_optics(rt, state, [0.0] * n, axis)
    probe = dict(state, **{"scan.start_m": start_m + dw, "scan.stop_m": axis[-1] + dw, "scan.step_m": step_m, "measurement.mode": "absorbance"})
    sp = rt._raw_spectrum(probe)
    if sp is None or len(sp["absorbance"]) != n:
        raise _ATReject("NO_EVALUATION")
    return axis, _dr_optics(rt, state, [float(a) for a in sp["absorbance"]], axis)


def _dr_measure(rt, dev, state, t, wavelengths_m):
    """One reading of the compartment at the given wavelengths (cover closed, lamps ready); the CHEM-071 measurement slots record it."""
    _dr_ready(dev, state)
    _at_need(state["lid.closed"], "COVER_OPEN")
    _dr_lamps_for(rt, dev, wavelengths_m, t)
    values = _dr_absorbance(rt, state, wavelengths_m)
    _dr_record(rt, state, wavelengths_m, values)
    return values


def _dr_record(rt, state, wavelengths_m, values):
    state["measurement.count"] += 1
    state["measurement.spectrum"] = {"wavelength_m": list(wavelengths_m), "absorbance": list(values), "transmittance": [10.0 ** -a for a in values],
                                     "signal": list(values), "reported_mode": "absorbance", "synthetic": True, "quality": "estimated",
                                     "axis_unit": "m", "signal_unit": "1", "sample_kind": state["sample.kind"] or "blank",
                                     "measurement_duration_s": None}


# ---------------------------------------------------------------- calibration, formulas, free programming
def _dr_solve(a, b):
    """Solve a small linear system (Gaussian elimination with partial pivoting); None when singular."""
    n = len(b)
    m = [list(r) + [b[i]] for i, r in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-15:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def _dr_fit(points, order, force0):
    """Calibration curve conc = a + b A + c A^2 + d A^3 by least squares (p.83: linear, 2nd, 3rd order, Force 0); coefficients and r^2."""
    pts = [(a, c) for c, a in points if a is not None and c is not None]
    powers = list(range(1 if force0 else 0, order + 1))
    if len(pts) < len(powers):
        return None
    ata = [[sum(x ** (i + j) for x, _ in pts) for j in powers] for i in powers]
    aty = [sum(y * x ** i for x, y in pts) for i in powers]
    sol = _dr_solve(ata, aty)
    if sol is None:
        return None
    coef = [0.0, 0.0, 0.0, 0.0]
    for p, v in zip(powers, sol):
        coef[p] = v
    mean = sum(y for _, y in pts) / len(pts)
    ss_tot = sum((y - mean) ** 2 for _, y in pts)
    ss_res = sum((y - _dr_poly(coef, x)) ** 2 for x, y in pts)
    return dict(coef=coef, r2=1.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot)


def _dr_poly(coef, x):
    return coef[0] + coef[1] * x + coef[2] * x * x + coef[3] * x ** 3


def _dr_formula(idx, a, k):
    """Absorbance formula of the Multi Wavelength mode (p.102); Kx A x terms."""
    t = [k[i] * a[i] for i in range(len(a))] + [0.0] * (4 - len(a))
    f = (lambda: t[0] + t[1], lambda: t[0] + t[1] + t[2], lambda: t[0] + t[1] + t[2] + t[3], lambda: t[0] / t[1],
         lambda: (t[0] + t[1]) / t[2], lambda: (t[0] + t[1]) / (t[2] + t[3]))[idx]
    try:
        return f()
    except ZeroDivisionError:
        raise _ATReject("NO_EVALUATION") from None


_DR_TOKEN = _at_re.compile(r"\s*(?:(\d+\.?\d*(?:[eE][-+]?\d+)?)|([ZRF]\d+)|(ln|log)|(.))")


def _dr_eval(expr, variables):
    """Evaluation formula of a free program (p.90-95): numbers, Zij / Rij readings, Fi variables, + - * / ^, parentheses, ln, log."""
    toks = []
    pos = 0
    while pos < len(expr):
        m = _DR_TOKEN.match(expr, pos)
        if m is None or m.end() == pos:
            break
        pos = m.end()
        if m.group(0).strip() == "":
            continue
        toks.append(m.groups())
    i = [0]

    def peek():
        return toks[i[0]] if i[0] < len(toks) else (None, None, None, None)

    def take():
        i[0] += 1
        return toks[i[0] - 1]

    def atom():
        num, var, fn, op = take() if i[0] < len(toks) else (None, None, None, None)
        if num is not None:
            return float(num)
        if var is not None:
            _at_need(var in variables, "NO_EVALUATION")
            return float(variables[var])
        if fn is not None:
            _at_need(peek()[3] == "(", "INVALID_ARGUMENT")
            v = atom()
            _at_need(v > 0, "NO_EVALUATION")
            return _at_math.log(v) if fn == "ln" else _at_math.log10(v)
        if op == "(":
            v = add()
            _at_need(i[0] < len(toks) and take()[3] == ")", "INVALID_ARGUMENT")
            return v
        if op == "-":
            return -power()
        raise _ATReject("INVALID_ARGUMENT")

    def power():
        v = atom()
        if peek()[3] == "^":
            take()
            v = v ** power()
        return v

    def mul():
        v = power()
        while peek()[3] in ("*", "/"):
            op = take()[3]
            r = power()
            if op == "/":
                _at_need(r != 0, "NO_EVALUATION")
                v /= r
            else:
                v *= r
        return v

    def add():
        v = mul()
        while peek()[3] in ("+", "-"):
            op = take()[3]
            v = v + mul() if op == "+" else v - mul()
        return v

    _at_need(pos == len(expr) or expr[pos:].strip() == "", "INVALID_ARGUMENT")
    value = add()
    _at_need(i[0] == len(toks), "INVALID_ARGUMENT")
    _at_need(_at_math.isfinite(value), "NO_EVALUATION")
    return value


def _dr_check_expression(expr):
    variables = {f"{k}{a}{b}": 1.0 for k in "ZR" for a in range(1, 5) for b in range(1, 5)}
    variables.update({f"F{a}": 1.0 for a in range(1, 5)})
    try:
        _dr_eval(expr, variables)
    except _ATReject as exc:
        return exc.code != "INVALID_ARGUMENT"
    return True


# ---------------------------------------------------------------- what a program measures and how its result is calculated
def _dr_program_def(rt, dev, kind, number):
    """Mode definition when a program / mode starts: wavelengths, unit, form, resolution, timers, calibration."""
    if kind == "stored":
        rec = _dr_library().get(number)
        _at_need(rec is not None, "NOT_FOUND")
        high = rec["range_kg_m3"][1] * 1e3
        return dict(kind=kind, number=number, name=rec["name"], wavelengths_m=[rec["wavelength_m"]], unit="mg/L", form=rec["form"],
                    forms=[(rec["form"], 1.0)], resolution=_dr_decimals(high), range_mg_l=[rec["range_kg_m3"][0] * 1e3, high],
                    per_abs=rec["per_abs_kg_m3"] * 1e3, timers=[dict(name="Reaction time", s=rec["timer_s"])] if rec["timer_s"] else [],
                    result_mode="conc", reading_mode="single")
    if kind == "user":
        p = dev["user_programs"].get(str(number))
        _at_need(p is not None, "NOT_FOUND")
        wl = [p["wavelength_m"]] if p["type"] == "single" else ([p[f"wl{i}_m"] for i in range(1, _DR_FORMULA_N[p["formula"]] + 1)]
                                                                 if p["type"] == "multi" else
                                                                 [p["wl1_m"]] if p["process"] in (0, 1, 2, 3) else [p["wl1_m"], p["wl2_m"]])
        forms = [(p["form"], 1.0)] + [(p[f"form{i}"], p[f"form{i}_factor"]) for i in range(2, 5) if p[f"form{i}"]]
        return dict(kind=kind, number=number, name=f"{number} {p['name']}", wavelengths_m=wl, unit=p["unit"], form=p["form"], forms=forms,
                    resolution=p["resolution"], range_mg_l=None, program=_at_copy.deepcopy(p),
                    timers=[dict(name="Timer %d" % i, s=p[f"timer{i}_s"]) for i in range(1, 5) if p.get(f"timer{i}_s")],
                    result_mode="conc", reading_mode="single")
    if kind == "single":
        s = dev["single"]
        return dict(kind=kind, number=None, name="Single Wavelength", wavelengths_m=[s["wavelength_m"]], unit=s["conc_unit"], form="",
                    forms=[], resolution=s["resolution"], timers=[], result_mode=s["result_mode"], reading_mode=s["reading_mode"])
    if kind == "multi":
        s = dev["multi"]
        return dict(kind=kind, number=None, name="Multi Wavelength", wavelengths_m=list(s["wavelengths_m"][:_DR_FORMULA_N[s["formula"]]]),
                    unit=s["conc_unit"], form="", forms=[], resolution=s["resolution"], timers=[], result_mode=s["result_mode"],
                    reading_mode="single")
    if kind == "scan":
        s = dev["scan"]
        return dict(kind=kind, number=None, name="Wavelength Scan", wavelengths_m=[s["start_m"], s["stop_m"]], unit="", form="", forms=[],
                    resolution=3, timers=[], result_mode=s["units"], reading_mode="single")
    if kind == "time":
        s = dev["time"]
        return dict(kind=kind, number=None, name="Time Course", wavelengths_m=[s["wavelength_m"]], unit="", form="", forms=[], resolution=3,
                    timers=[], result_mode=s["units"], reading_mode="single")
    return dict(kind="barcode", number=None, name="Barcode Programs", wavelengths_m=[], unit="", form="", forms=[], resolution=3, timers=[],
                result_mode="conc", reading_mode="single")


def _dr_value(dev, mode, absorbances, free_vars=None):
    """Result of a reading: absorbance(s) against the zero -> concentration by the program's calibration and options (p.56-62, p.98-103)."""
    warnings = []
    kind = mode["kind"]
    conc = None
    if kind == "stored":
        raw = absorbances[0] * mode["per_abs"]                                    # published sensitivity, straight line through 0
        if mode.get("std_adjust"):
            raw *= mode["std_adjust"]["factor"]
        lo, hi = mode["range_mg_l"]
        if raw > hi + 1e-12:
            warnings.append("Over measuring range")
        elif raw < lo - 1e-12 and raw >= 0:
            warnings.append("Under measuring range")
        conc = raw
    elif kind == "user":
        p = mode["program"]
        if p["type"] == "free":
            conc = _dr_eval(p["expression"], dict(free_vars or {}, **{f"F{i}": p[f"f{i}"] for i in range(1, 5)}))
        else:
            x = absorbances[0] if p["type"] == "single" else _dr_formula(p["formula"], absorbances, [p[f"k{i}"] for i in range(1, 5)])
            cal = p.get("calibration")
            _at_need(cal is not None, "NO_EVALUATION")
            conc = _dr_poly(cal["coef"], x)
        if mode.get("std_adjust"):
            conc *= mode["std_adjust"]["factor"]
        if p.get("upper") is not None and conc > p["upper"]:
            warnings.append("Over measuring range")
        if p.get("lower") is not None and conc < p["lower"]:
            warnings.append("Under measuring range")
    elif kind == "single":
        if dev["single"]["conc_factor"] is not None:
            conc = dev["single"]["conc_factor"] * absorbances[0]
    elif kind == "multi":
        s = dev["multi"]
        x = _dr_formula(s["formula"], absorbances, s["k"])
        conc = None if s["conc_factor"] is None else s["conc_factor"] * x            # without a factor the result is the formula value
    if conc is not None:
        factor = dict(mode.get("forms") or []).get(mode["form"], 1.0) if mode.get("forms") else 1.0
        conc *= factor
        if mode.get("reagent_blank") is not None:
            conc -= mode["reagent_blank"]                                          # concentration = (factor x Abs) - (reagent blank) (p.57)
        if mode.get("dilution"):
            conc *= mode["dilution"]
        if conc < 0:
            warnings.append("Negative result!")
        if abs(conc) > _DR_CONC_LIMIT:
            warnings.append("Concentration too high!")
    if any(abs(a) > _DR_ABS_LIMIT for a in absorbances):
        warnings.append("Absorbance > 3.5!")
    return conc, warnings


# ---------------------------------------------------------------- terminal commands: rt (runtime), dev, state, args, t
def _dr_mode(dev, kinds=None):
    _at_need(dev["mode"] is not None, "NO_PROGRAM")
    if kinds is not None:
        _at_need(dev["mode"]["kind"] in kinds, "PRECONDITION_FAILED")
    return dev["mode"]


def _dr_c_start(rt, dev, state, a, t):
    """Main Menu: start a program or mode (Stored / User / Favorite program, Single / Multi Wavelength, Wavelength Scan, Time Course,
    Barcode Programs); locked functions need the security password (p.31)."""
    _dr_ready(dev, state)
    kind = a["kind"]
    _dr_security(dev, _DR_KIND_FUNCTION[kind], a.get("password"))
    if kind in ("stored", "user"):
        _at_need("number" in a, "INVALID_ARGUMENT")
    mode = _dr_program_def(rt, dev, kind, a.get("number"))
    dev.update(mode=mode, zero=None, result=None, scan_data=None, tc=None, free=None, std_add=None)
    return {"mode": kind, "number": a.get("number"), "wavelengths_m": list(mode["wavelengths_m"])}


def _dr_c_exit(rt, dev, state, a, t):
    """Back to the Main Menu: the program's temporary options (reagent blank, chemical form, standard adjust) end (p.59-60)."""
    _at_need(dev["mode"] is not None, "NO_PROGRAM")
    dev.update(mode=None, zero=None, result=None, scan_data=None, tc=None, free=None, std_add=None)
    return {"mode": None}


_DR_OPTION_KEYS = {
    "stored": {"result_mode", "dilution", "reagent_blank", "multiple", "reading_mode", "form"},
    "user": {"result_mode", "dilution", "reagent_blank", "multiple", "reading_mode", "form"},
    "barcode": {"result_mode", "dilution"},
    "single": {"wavelength_m", "result_mode", "conc_factor", "conc_unit", "resolution", "reading_mode"},
    "multi": {"formula", "wavelengths_m", "k", "result_mode", "conc_factor", "conc_unit", "resolution"},
    "scan": {"start_m", "stop_m", "step_m", "units", "scale", "y_min", "y_max", "cursor", "integral", "reference", "view"},
    "time": {"total_s", "interval_s", "wavelength_m", "units", "scale", "y_min", "y_max", "view"},
}


def _dr_c_options(rt, dev, state, a, t):
    """Options of the running program / mode (Tables 4, 12-14, 16): result mode, dilution, reagent blank, multiple determination, reading
    mode, chemical form, wavelength(s), formula and coefficients, concentration factor / unit / resolution, scan range / step / units /
    scale / cursor / integral / reference / view, time course time / interval / wavelength."""
    _dr_ready(dev, state)
    mode = _dr_mode(dev)
    kind = mode["kind"]
    ch = a["settings"]
    _at_need(set(ch) <= _DR_OPTION_KEYS[kind], "INVALID_ARGUMENT")
    _at_need(not (dev["tc"] and dev["tc"]["running"]), "BUSY")
    if kind in ("single", "multi", "scan", "time"):
        s = _at_copy.deepcopy(dev[kind])
        s.update({k: v for k, v in ch.items() if k in s})
        if kind == "single" and s["result_mode"] == "conc":
            _at_need(s["conc_factor"] is not None, "PRECONDITION_FAILED")      # concentration needs the concentration factor (p.99)
        if kind == "multi":
            _at_need(len(s["wavelengths_m"]) == 4 and len(s["k"]) == 4, "INVALID_ARGUMENT")
            if s["result_mode"] == "conc":
                _at_need(s["conc_factor"] is not None, "PRECONDITION_FAILED")
        if kind == "scan":
            _at_need(s["start_m"] < s["stop_m"] - 1e-15, "INVALID_ARGUMENT")              # minimum and maximum must differ (p.105)
            _at_need(any(abs(s["step_m"] - x * 1e-9) < 1e-15 for x in DR_STEPS_NM), "INVALID_ARGUMENT")
            steps = int(_at_math.floor((s["stop_m"] - s["start_m"]) / s["step_m"] + 1e-8))
            _at_need(steps <= 910, "INVALID_ARGUMENT")                                     # at most 910 steps (p.105)
            if {"start_m", "stop_m", "step_m"} & set(ch):
                s["stop_m"] = s["start_m"] + steps * s["step_m"]                           # the maximum adjusts to a multiple of the step
            _at_need(s["y_min"] < s["y_max"], "INVALID_ARGUMENT")
            if s["reference"] is not None:
                ref = dev["stored_scans"].get(str(s["reference"]))
                _at_need(ref is not None, "NOT_FOUND")
                _at_need(abs(ref["start_m"] - s["start_m"]) < 1e-15 and abs(ref["step_m"] - s["step_m"]) < 1e-15 and
                         len(ref["absorbance"]) == steps + 1, "PRECONDITION_FAILED")      # same range and step only (p.104)
        if kind == "time":
            _at_need(s["interval_s"] <= s["total_s"] and s["total_s"] / s["interval_s"] <= 2000 + 1e-9, "INVALID_ARGUMENT")   # 2000 points (p.111)
            _at_need(s["y_min"] < s["y_max"], "INVALID_ARGUMENT")
        new_wl = _dr_program_def(rt, dict(dev, **{kind: s}), kind, None)["wavelengths_m"]
        changed = (any(abs(a - b) > 1e-15 for a, b in zip(new_wl, mode["wavelengths_m"])) or len(new_wl) != len(mode["wavelengths_m"]) or
                   (kind == "scan" and abs(s["step_m"] - dev["scan"]["step_m"]) > 1e-15))
        dev[kind] = s
        keep = {k: mode[k] for k in ("std_adjust",) if k in mode}
        dev["mode"] = dict(_dr_program_def(rt, dev, kind, None), **keep)
        if changed:
            dev.update(zero=None, result=None, scan_data=None)                             # a new zero / baseline is needed (p.106)
        elif kind in ("single", "multi") and dev["result"] is not None:
            _dr_recalc(dev)
        return {"settings": {k: s[k] for k in ch}}
    if "form" in ch:
        _at_need(ch["form"] in [f for f, _ in mode["forms"]], "INVALID_ARGUMENT")
    if ch.get("result_mode") == "conc" and kind == "barcode":
        pass
    for k, v in ch.items():
        mode[k] = v
    if dev["result"] is not None:
        _dr_recalc(dev)
    return {"settings": dict(ch)}


def _dr_c_adjust(rt, dev, state, a, t):
    """Standard Adjust (p.59): after reading a known standard, the calibration is scaled to the entered standard value (within limits)."""
    _dr_ready(dev, state)
    mode = _dr_mode(dev, ("stored", "user"))
    if a["action"] == "off":
        mode.pop("std_adjust", None)
        if dev["result"] is not None:
            _dr_recalc(dev)
        return {"factor": 1.0}
    r = dev["result"]
    _at_need(r is not None and r.get("base_conc") not in (None, 0.0) and "standard" in a, "PRECONDITION_FAILED")
    factor = a["standard"] / r["base_conc"]
    _at_need(abs(factor - 1.0) <= _DR_ADJUST_LIMIT + 1e-12, "ADJUST_LIMIT")
    mode["std_adjust"] = dict(standard=a["standard"], factor=factor)
    _dr_recalc(dev)
    return {"factor": factor}


def _dr_recalc(dev):
    """Recompute the displayed result after an option change (dilution, blank, form, adjust, result mode)."""
    r = dev["result"]
    mode = dev["mode"]
    conc, warnings = _dr_value(dev, mode, r["abs"], r.get("free_vars"))
    r.update(conc=conc, warnings=warnings, unit=mode["unit"], form=mode.get("form", ""), result_mode=mode["result_mode"],
             flags=_dr_flags(mode))


def _dr_flags(mode):
    return dict(dilution=mode.get("dilution"), reagent_blank=mode.get("reagent_blank"), std_adjust=(mode.get("std_adjust") or {}).get("factor"),
                multiple=mode.get("multiple"))


def _dr_c_zero(rt, dev, state, a, t):
    """Zero (p.60, p.100, p.107, p.112): reading of the zero cell at the mode's wavelengths, the baseline of a scan, or the first step of a
    free program's measurement process.  Read stays inactive until zeroed."""
    _dr_ready(dev, state)
    mode = _dr_mode(dev)
    kind = mode["kind"]
    _at_need(kind != "barcode", "NO_BARCODE")
    _at_need(not (dev["tc"] and dev["tc"]["running"]), "BUSY")
    if kind == "scan":
        s = dev["scan"]
        _at_need(state["lid.closed"], "COVER_OPEN")
        _dr_lamps_for(rt, dev, [s["start_m"], s["stop_m"]], t)
        axis, values = _dr_scan_absorbance(rt, state, s["start_m"], s["stop_m"], s["step_m"])
        _dr_record(rt, state, axis, values)
        dev.update(zero=dict(wavelengths_m=axis, abs=values, time=_at_now(dev, t)), scan_data=None, result=None)
        return {"zeroed": True, "points": len(axis)}
    if kind == "user" and mode["program"]["type"] == "free":
        steps, _ = _DR_PROCESS_STEPS[mode["program"]["process"]]
        values = _dr_measure(rt, dev, state, t, [mode["wavelengths_m"][j - 1] for _, j in steps[0][1]])
        dev["free"] = dict(step=1, vars={name: v for (name, _), v in zip(steps[0][1], values)})
        dev.update(zero=dict(wavelengths_m=list(mode["wavelengths_m"]), abs=values, time=_at_now(dev, t)), result=None)
        return {"zeroed": True, "points": len(values)}
    values = _dr_measure(rt, dev, state, t, mode["wavelengths_m"])
    dev.update(zero=dict(wavelengths_m=list(mode["wavelengths_m"]), abs=values, time=_at_now(dev, t)), result=None)
    if kind == "time":
        dev["tc"] = None
    return {"zeroed": True, "points": len(values)}


def _dr_sample_label(dev):
    sid = next((s for s in dev["sample_ids"] if s["name"] == dev["sample_id"]), None)
    if sid is None:
        return None
    label = sid["name"] + (f" ({sid['next']:02d})" if sid["add_number"] else "")
    return label


def _dr_after_reading(dev, t):
    sid = next((s for s in dev["sample_ids"] if s["name"] == dev["sample_id"]), None)
    if sid is not None and sid["add_number"]:
        sid["next"] += 1                                                    # numbered in ascending order after a reading (p.27)


def _dr_log_entry(dev, t, r):
    dev["log_seq"] += 1
    _at_log(dev["data_log"], dict(id=dev["log_seq"], time=_at_now(dev, t), program=r["name"], number=r["number"], kind=r["kind"],
                                  sample_id=r["sample_id"], operator=r["operator"], conc=r["conc"], unit=r["unit"], form=r["form"],
                                  abs=list(r["abs"]), wavelengths_m=list(r["wavelengths_m"]), warnings=list(r["warnings"]),
                                  flags=dict(r["flags"])), cap=5000)               # 5000 readings, the oldest are deleted (p.42-43)


def _dr_c_read(rt, dev, state, a, t):
    """Read (p.60, p.100, p.103, p.107, p.112): the sample against the zero -> result; a scan; or the start of a time course."""
    _dr_ready(dev, state)
    mode = _dr_mode(dev)
    kind = mode["kind"]
    _at_need(kind != "barcode", "NO_BARCODE")
    _at_need(not (dev["tc"] and dev["tc"]["running"]), "BUSY")
    if kind == "user" and mode["program"]["type"] == "free":
        return _dr_read_free(rt, dev, state, mode, t)
    _at_need(dev["zero"] is not None, "ZERO_FIRST")
    if kind == "scan":
        s = dev["scan"]
        _at_need(state["lid.closed"], "COVER_OPEN")
        _dr_lamps_for(rt, dev, [s["start_m"], s["stop_m"]], t)
        axis, values = _dr_scan_absorbance(rt, state, s["start_m"], s["stop_m"], s["step_m"])
        _dr_record(rt, state, axis, values)
        rel = [v - z for v, z in zip(values, dev["zero"]["abs"])]
        dev["scan_data"] = dict(wavelength_m=axis, absorbance=rel, time=_at_now(dev, t), start_m=s["start_m"], step_m=s["step_m"],
                                sample_id=_dr_sample_label(dev))
        _dr_after_reading(dev, t)
        k = max(range(len(rel)), key=lambda i: rel[i])
        return {"points": len(axis), "max_abs": rel[k], "max_wavelength_m": axis[k]}
    if kind == "time":
        s = dev["time"]
        values = _dr_measure(rt, dev, state, t, [s["wavelength_m"]])
        dev["tc"] = dict(start_s=t, interval_s=s["interval_s"], total_s=s["total_s"], wavelength_m=s["wavelength_m"], zero=dev["zero"]["abs"][0],
                         points=[[0.0, values[0] - dev["zero"]["abs"][0]]], marks=[], running=True, mark_next=False, time=_at_now(dev, t),
                         sample_id=_dr_sample_label(dev))
        return {"started": True, "points": 1}
    values = _dr_measure(rt, dev, state, t, mode["wavelengths_m"])
    rel = [v - z for v, z in zip(values, dev["zero"]["abs"])]
    n = mode.get("multiple") or 1                                                # multiple determination: n readings, mean (p.61)
    conc, warnings = _dr_value(dev, mode, rel)
    base = None
    if kind in ("stored", "user"):
        base_mode = dict(mode, std_adjust=None, reagent_blank=None, dilution=None, form=(mode["forms"][0][0] if mode["forms"] else ""))
        base = _dr_value(dev, base_mode, rel)[0]
    formula_value = _dr_formula(dev["multi"]["formula"], rel, dev["multi"]["k"]) if kind == "multi" else None
    r = dict(kind=kind, number=mode["number"], name=mode["name"], abs=rel, wavelengths_m=list(mode["wavelengths_m"]), conc=conc, base_conc=base,
             formula_value=formula_value,
             unit=mode["unit"], form=mode.get("form", ""), result_mode=mode["result_mode"], warnings=warnings, flags=_dr_flags(mode), n=n,
             range_pct=0.0 if n > 1 else None, time=_at_now(dev, t), sample_id=_dr_sample_label(dev), operator=dev["operator"], aqa=None)
    dev["result"] = r
    aqa = dev["aqa"]
    if aqa["pending"] is not None and conc is not None:                         # AQA standard measurement (p.123): Passed / Failed
        std = aqa["standards"][aqa["pending"]]
        passed = abs(conc - std["concentration"]) <= std["ci"] + 1e-12
        r["aqa"] = "Passed" if passed else "Failed"
        _at_log(dev["aqa_log"], dict(time=_at_now(dev, t), kind="Standard", name=std["name"], program=mode["name"], target=std["concentration"],
                                     ci=std["ci"], value=conc, result=r["aqa"]), cap=1000)
        aqa["last"]["standard"] = t + dev["clock_offset_s"]
        aqa["pending"] = None
    sa = dev["std_add"]
    if sa is not None and sa.get("row") and conc is not None:
        sa["readings"][sa["row"]] = conc
        sa["row"] = None
        if all(v is not None for v in sa["readings"]):
            aqa["last"]["addition"] = t + dev["clock_offset_s"]                 # an AQA addition check was performed
    if n > 1:
        aqa["last"]["multiple"] = t + dev["clock_offset_s"]
    if mode.get("dilution"):
        aqa["last"]["dilution"] = t + dev["clock_offset_s"]
    if dev["settings"]["log.auto_store"]:
        _dr_log_entry(dev, t, r)
        r["stored"] = True
    _dr_after_reading(dev, t)
    return {"abs": rel, "conc": conc, "warnings": warnings}


def _dr_read_free(rt, dev, state, mode, t):
    """Free programming: Read performs the next [R] step of the measurement process; after the last step C1 is evaluated (p.88-95)."""
    steps, repeat = _DR_PROCESS_STEPS[mode["program"]["process"]]
    fr = dev["free"]
    _at_need(fr is not None and fr["step"] < len(steps) and steps[fr["step"]][0] == "R", "ZERO_FIRST")
    step = steps[fr["step"]]
    values = _dr_measure(rt, dev, state, t, [mode["wavelengths_m"][j - 1] for _, j in step[1]])
    fr["vars"].update({name: v for (name, _), v in zip(step[1], values)})
    fr["step"] += 1
    if fr["step"] < len(steps):
        return {"step": fr["step"], "complete": False}
    conc, warnings = _dr_value(dev, mode, [], fr["vars"])
    r = dict(kind="user", number=mode["number"], name=mode["name"], abs=[fr["vars"].get("R11", 0.0)], wavelengths_m=list(mode["wavelengths_m"]),
             conc=conc, base_conc=conc / (mode.get("std_adjust") or {"factor": 1.0})["factor"] if conc is not None else None, unit=mode["unit"],
             form=mode.get("form", ""), result_mode="conc", warnings=warnings, flags=_dr_flags(mode), n=1, range_pct=None, time=_at_now(dev, t),
             sample_id=_dr_sample_label(dev), operator=dev["operator"], aqa=None, free_vars=dict(fr["vars"]))
    dev["result"] = r
    if dev["settings"]["log.auto_store"]:
        _dr_log_entry(dev, t, r)
        r["stored"] = True
    _dr_after_reading(dev, t)
    if repeat is not None:
        fr["step"] = repeat                                                    # the bracketed sequence is repeated with the next Read
    return {"step": fr["step"], "complete": True, "conc": conc}


def _dr_c_tc(rt, dev, state, a, t):
    """Time course Mark / Stop (p.112)."""
    tc = dev["tc"]
    _at_need(tc is not None and tc["running"], "PRECONDITION_FAILED")
    if a["action"] == "mark":
        tc["mark_next"] = True                                                 # the next data point collected is marked
        return {"running": True}
    tc["running"] = False
    return {"running": False}


def _dr_c_store(rt, dev, state, a, t):
    """Save icon / Folder icon (p.43, p.48): the displayed reading into the data log (Auto Store off), or the scan / time course into a
    numbered line (50 each, overwritten)."""
    _dr_ready(dev, state)
    target = a["target"]
    if target == "log":
        r = dev["result"]
        _at_need(r is not None and not r.get("stored"), "PRECONDITION_FAILED")
        _dr_log_entry(dev, t, r)
        r["stored"] = True
        return {"stored": True, "slot": None}
    slot = a.get("slot")
    store = dev["stored_scans"] if target == "scan" else dev["stored_tc"]
    if slot is None:
        slot = next(i for i in range(1, 52) if str(i) not in store or i == 51)
    _at_need(1 <= slot <= 50, "STORAGE_FULL")
    if target == "scan":
        sd = dev["scan_data"]
        _at_need(sd is not None, "PRECONDITION_FAILED")
        store[str(slot)] = dict(sd, units=dev["scan"]["units"])
    else:
        tc = dev["tc"]
        _at_need(tc is not None and not tc["running"] and len(tc["points"]) > 0, "PRECONDITION_FAILED")
        store[str(slot)] = dict(points=[list(p) for p in tc["points"]], marks=list(tc["marks"]), wavelength_m=tc["wavelength_m"],
                                interval_s=tc["interval_s"], total_s=tc["total_s"], time=tc["time"], sample_id=tc["sample_id"])
    return {"stored": True, "slot": slot}


def _dr_filter_match(e, f):
    return ((not f.get("sample_id") or (e.get("sample_id") or "").startswith(f["sample_id"])) and
            (not f.get("operator") or e.get("operator") == f["operator"]) and
            (not f.get("start_date") or e["time"][:10] >= f["start_date"]) and (not f.get("parameter") or e.get("program") == f["parameter"]))


def _dr_c_data(rt, dev, state, a, t):
    """Recall Data (p.43-50): delete or send single point / filtered data / all data of the data log, stored scans / time courses or the
    AQA log.  Sending needs a USB memory stick, printer or network drive (none connected in the simulation)."""
    _dr_ready(dev, state)
    src, act, scope = a["source"], a["action"], a.get("scope", "single")
    if act == "send":
        s = dev["settings"]
        raise _ATReject("NO_NETWORK" if s["network.on"] and s["network.server"] != "Off" else "NO_USB")
    if src in ("scan", "time"):
        store = dev["stored_scans"] if src == "scan" else dev["stored_tc"]
        if scope == "all":
            n = len(store)
            store.clear()
            return {"deleted": n}
        _at_need(str(a.get("index")) in store, "NOT_FOUND")
        del store[str(a["index"])]
        return {"deleted": 1}
    lst = {"log": dev["data_log"], "aqa": dev["aqa_log"], "checks": dev["check_log"]}[src]
    if scope == "single":
        _at_need(isinstance(a.get("index"), int) and 0 <= a["index"] < len(lst), "NOT_FOUND")
        del lst[a["index"]]
        return {"deleted": 1}
    keep = [e for e in lst if scope == "filtered" and not _dr_filter_match(e, a.get("filter") or {})]
    n = len(lst) - len(keep)
    lst[:] = keep
    return {"deleted": n}


def _dr_draft_defaults(number):
    d = _at_defaults(DR_PROGRAM_SETTINGS)
    d.update(number=number, table=[], calib_zero=None, calibration=None)
    return d


def _dr_draft_fit(p):
    if p["type"] == "free":
        return None
    if p["calib_method"] == "formula":
        return dict(coef=[p["coef_a"], p["coef_b"] if p["calib_order"] >= 1 else 0.0, p["coef_c"] if p["calib_order"] >= 2 else 0.0,
                          p["coef_d"] if p["calib_order"] >= 3 else 0.0], r2=None)
    return _dr_fit(p["table"], p["calib_order"], p["force0"])


def _dr_c_user(rt, dev, state, a, t):
    """User Programs (p.77-97): New / Edit a draft (number 9000-9099, name, type, unit, wavelength(s), formula and K, resolution, chemical
    forms, calibration by values / measured standards / formula with order and Force 0, limits, timers; free programming process, formula
    and variables), Store, Cancel, Delete, Add to favorites."""
    _dr_ready(dev, state)
    act = a["action"]
    progs = dev["user_programs"]
    if act in ("new", "edit", "delete", "favorite"):
        _dr_security(dev, "User Programs", a.get("password"))
    if act == "new":
        num = a.get("number") or next((n for n in range(9000, 9100) if str(n) not in progs), None)
        _at_need(num is not None and 9000 <= num <= 9099, "INVALID_ARGUMENT")
        dev["draft"] = _dr_draft_defaults(num)
        return {"number": num}
    if act in ("edit", "delete", "favorite"):
        _at_need(str(a.get("number")) in progs, "NOT_FOUND")
        num = a["number"]
        if act == "edit":
            dev["draft"] = _at_copy.deepcopy(progs[str(num)])
        elif act == "delete":
            del progs[str(num)]
            dev["favorites"] = [f for f in dev["favorites"] if f != dict(kind="user", number=num)]   # also deleted from Favorites (p.97)
        elif dict(kind="user", number=num) not in dev["favorites"]:
            dev["favorites"].append(dict(kind="user", number=num))
        return {"number": num}
    d = dev["draft"]
    _at_need(d is not None, "PRECONDITION_FAILED")
    if act == "set":
        ch = a["settings"]
        new = _at_apply(DR_PROGRAM_SETTINGS, {k: d[k] for k in _at_defaults(DR_PROGRAM_SETTINGS)}, ch)
        if "expression" in ch:
            _at_need(_dr_check_expression(ch["expression"]), "INVALID_ARGUMENT")
        if any(k.startswith(("wavelength", "wl")) or k in ("type", "formula") for k in ch):
            d["calib_zero"] = None
        d.update(new)
    elif act == "calib_add":
        _at_need(len(d["table"]) < 24 and "concentration" in a, "INVALID_ARGUMENT")           # max 24 standards (p.83)
        d["table"].append([float(a["concentration"]), a.get("absorbance")])
    elif act in ("calib_set", "calib_delete", "calib_read"):
        _at_need(isinstance(a.get("index"), int) and 0 <= a["index"] < len(d["table"]), "NOT_FOUND")
        row = d["table"][a["index"]]
        if act == "calib_delete":
            del d["table"][a["index"]]
        elif act == "calib_set":
            if "concentration" in a:
                row[0] = float(a["concentration"])
            if "absorbance" in a:
                row[1] = a["absorbance"]
        else:
            _at_need(d["calib_zero"] is not None, "ZERO_FIRST")
            wl = [d["wavelength_m"]] if d["type"] == "single" else [d[f"wl{i}_m"] for i in range(1, _DR_FORMULA_N[d["formula"]] + 1)]
            values = _dr_measure(rt, dev, state, t, wl)
            rel = [v - z for v, z in zip(values, d["calib_zero"])]
            row[1] = rel[0] if d["type"] == "single" else _dr_formula(d["formula"], rel, [d[f"k{i}"] for i in range(1, 5)])
    elif act == "calib_zero":
        _at_need(d["type"] != "free", "PRECONDITION_FAILED")
        wl = [d["wavelength_m"]] if d["type"] == "single" else [d[f"wl{i}_m"] for i in range(1, _DR_FORMULA_N[d["formula"]] + 1)]
        d["calib_zero"] = _dr_measure(rt, dev, state, t, wl)
    elif act == "store":
        _at_need(bool(d["name"]), "INCOMPLETE")
        fit = _dr_draft_fit(d)
        _at_need(d["type"] == "free" or fit is not None, "INCOMPLETE")               # a calibration is needed for single / multi
        d["calibration"] = fit
        progs[str(d["number"])] = _at_copy.deepcopy(d)
        dev["draft"] = None
        return {"number": d["number"]}
    elif act == "cancel":
        dev["draft"] = None
        return {"number": d["number"]}
    return {"number": d["number"]}


def _dr_c_fav(rt, dev, state, a, t):
    """Favorites (p.66-67, p.97-98): add a stored or user program, remove a favorite (the program itself stays)."""
    _dr_ready(dev, state)
    entry = dict(kind=a["kind"], number=a["number"])
    if a["action"] == "add":
        _at_need((a["kind"] == "stored" and a["number"] in _dr_library()) or (a["kind"] == "user" and str(a["number"]) in dev["user_programs"]),
                 "NOT_FOUND")
        if entry not in dev["favorites"]:
            dev["favorites"].append(entry)
    else:
        _at_need(entry in dev["favorites"], "NOT_FOUND")
        dev["favorites"].remove(entry)
    return {"favorites": len(dev["favorites"])}


def _dr_c_operator(rt, dev, state, a, t):
    """Operator ID (p.25-26, p.31-32): up to 30 IDs of 10 characters with optional password and security level; Login / Logout."""
    _dr_ready(dev, state)
    act = a["action"]
    ops = dev["operators"]
    op = next((o for o in ops if o["name"] == a.get("name")), None)
    if act == "logout":
        _at_need(dev["operator"] is not None, "PRECONDITION_FAILED")
        dev["operator"] = None
        return {"operator": None}
    _at_need(bool(a.get("name")), "INVALID_ARGUMENT")
    if act == "new":
        _at_need(op is None and len(ops) < 30, "INVALID_ARGUMENT")
        ops.append(dict(name=a["name"], password=a.get("password") or None, level="Off"))
    elif act == "login":
        _at_need(op is not None, "NOT_FOUND")
        _at_need(op["password"] is None or a.get("password") == op["password"], "WRONG_PASSWORD")
        dev["operator"] = op["name"]
    else:
        _at_need(op is not None, "NOT_FOUND")
        if act == "delete":
            ops.remove(op)
            if dev["operator"] == op["name"]:
                dev["operator"] = None
        elif act == "edit":
            if a.get("new_name"):
                _at_need(all(o["name"] != a["new_name"] for o in ops if o is not op), "INVALID_ARGUMENT")
                if dev["operator"] == op["name"]:
                    dev["operator"] = a["new_name"]
                op["name"] = a["new_name"]
            if "password" in a:
                op["password"] = a["password"] or None
        else:                                                                   # set_level: needs the security password (p.31)
            _at_need(dev["security"]["enabled"], "PRECONDITION_FAILED")
            _at_need(a.get("security_password") == dev["security"]["password"], "WRONG_PASSWORD")
            _at_need(a.get("level") in DR_LEVELS, "INVALID_ARGUMENT")
            op["level"] = a["level"]
    return {"operator": dev["operator"]}


def _dr_c_sample(rt, dev, state, a, t):
    """Sample ID (p.27-28): up to 100 IDs of 20 characters, optional date / time and sequential number; Select / Off."""
    _dr_ready(dev, state)
    act = a["action"]
    ids = dev["sample_ids"]
    if act == "off":
        dev["sample_id"] = None
        return {"sample_id": None}
    _at_need(bool(a.get("name")), "INVALID_ARGUMENT")
    sid = next((s for s in ids if s["name"] == a["name"]), None)
    if act == "new":
        _at_need(sid is None and len(ids) < 100, "INVALID_ARGUMENT")
        ids.append(dict(name=a["name"], add_date=bool(a.get("add_date", False)), add_number=bool(a.get("add_number", False)),
                        next=int(a.get("first_number", 1)), created=_at_now(dev, t)))
        dev["sample_id"] = a["name"]                                            # the new sample ID is activated (p.27)
        return {"sample_id": a["name"]}
    _at_need(sid is not None, "NOT_FOUND")
    if act == "select":
        dev["sample_id"] = sid["name"]
    elif act == "delete":
        ids.remove(sid)
        if dev["sample_id"] == sid["name"]:
            dev["sample_id"] = None
    else:
        if a.get("new_name"):
            _at_need(all(s["name"] != a["new_name"] for s in ids if s is not sid), "INVALID_ARGUMENT")
            if dev["sample_id"] == sid["name"]:
                dev["sample_id"] = a["new_name"]
            sid["name"] = a["new_name"]
        for k in ("add_date", "add_number"):
            if k in a:
                sid[k] = bool(a[k])
        if "first_number" in a:
            sid["next"] = int(a["first_number"])
    return {"sample_id": dev["sample_id"]}


def _dr_c_security(rt, dev, state, a, t):
    """Security (p.29-33): On with a security password (10 characters), Off with the password, Security List levels per function."""
    _dr_ready(dev, state)
    sec = dev["security"]
    act = a["action"]
    if act == "on":
        _at_need(bool(a.get("password")), "INVALID_ARGUMENT")
        if sec["enabled"]:
            _at_need(a.get("current_password") == sec["password"], "WRONG_PASSWORD")
        sec.update(enabled=True, password=a["password"])
    elif act == "off":
        _at_need(sec["enabled"], "PRECONDITION_FAILED")
        _at_need(a.get("password") == sec["password"], "WRONG_PASSWORD")
        sec.update(enabled=False, password=None)
    else:
        _at_need(sec["enabled"], "PRECONDITION_FAILED")
        _at_need(a.get("password") == sec["password"], "WRONG_PASSWORD")
        _at_need(a.get("function") in DR_FUNCTIONS and a.get("level") in DR_LEVELS, "INVALID_ARGUMENT")
        sec["levels"][a["function"]] = a["level"]
    return {"enabled": sec["enabled"]}


def _dr_c_check_access(rt, dev, state, a, t):
    _dr_ready(dev, state)
    _dr_security(dev, a["function"], a.get("password"))
    return {"allowed": True}


def _dr_c_settings(rt, dev, state, a, t):
    """Instrument Setup values (p.33-43): date / time format, sound, printer, network, power management, UV switch, data log setup."""
    _dr_ready(dev, state)
    dev["settings"] = _at_apply(DR_SETTINGS, dev["settings"], a["settings"])
    return {"settings": {k: dev["settings"][k] for k in a["settings"]}}


def _dr_c_clock(rt, dev, state, a, t):
    _dr_ready(dev, state)
    try:
        target = _at_dt.datetime.fromisoformat(a["date"] + "T" + a["time"])
    except ValueError:
        raise _ATReject("INVALID_ARGUMENT") from None
    dev["clock_offset_s"] = (target - _AT_EPOCH).total_seconds() - t
    return {"datetime": _at_now(dev, t)}


def _dr_c_lamp(rt, dev, state, a, t):
    """Lamp Control (p.41-42): VIS / UV lamp on or off by hand; the UV lamp warms up after switching on."""
    _dr_ready(dev, state)
    lamps = dev["lamps"]
    if a["lamp"] == "uv":
        if a["on"] and not lamps["uv_on"]:
            lamps.update(uv_on=True, uv_since=t, uv_cycles=lamps["uv_cycles"] + 1, uv_ignitions=lamps["uv_ignitions"] + 1, uv_last_use=t)
        elif not a["on"]:
            lamps.update(uv_on=False, uv_since=None)
    else:
        if a["on"] and not lamps["vis_on"]:
            lamps["vis_cycles"] += 1
        lamps["vis_on"] = bool(a["on"])
    return {"lamp": a["lamp"], "on": bool(a["on"])}


def _dr_c_timer(rt, dev, state, a, t):
    """Timer (p.57-58, p.128): a countdown (general timer or a program timer) that beeps at the end; it does not affect the reading."""
    _dr_ready(dev, state)
    if a["action"] == "cancel":
        _at_need(dev["timer"] is not None, "PRECONDITION_FAILED")
        dev["timer"] = None
        return {"running": False}
    _at_need("duration_s" in a, "INVALID_ARGUMENT")
    dev["timer"] = dict(name=a.get("name") or "General Timer", end_s=t + a["duration_s"], duration_s=a["duration_s"])
    return {"running": True}


def _dr_check_log(dev, t, check, values, passed):
    _at_log(dev["check_log"], dict(time=_at_now(dev, t), check=check, values=values, result="Passed" if passed else "Failed"), cap=1000)


def _dr_c_check(rt, dev, state, a, t):
    """System Checks > Optical Checks and Pipette Check (p.116-125): wavelength check (656.1 / 486.0 nm deuterium lines, bandwidth),
    stray light (3 readings at 340 nm), absorbance (5 Zero / Read pairs), noise (30 zero and 100 readings), drift (one hour) and the pipette
    check; the readings include the evaluator's optics errors; each result is judged against its criteria (required value, nominal and
    tolerance, noise / drift limit) and stored with Passed / Failed in the check log."""
    _dr_ready(dev, state)
    _dr_security(dev, "System Checks", a.get("password"))
    chk, act = a["check"], a["action"]
    checks = dev["checks"]
    crit = dict(_DR_CRITERIA.get(chk, {}), **{k: a[k] for k in ("required", "nominal", "tolerance", "limit") if k in a})
    if act == "cancel":
        checks.pop(chk, None)
        if chk == "drift":
            dev["drift"] = None
        return {"check": chk, "state": "cancelled"}
    _at_need(state["lid.closed"], "COVER_OPEN")
    if chk == "wavelength":
        wl = a.get("wavelength_m", 656.1e-9)
        _at_need(any(abs(wl - x) < 1e-13 for x in (656.1e-9, 486.0e-9)), "INVALID_ARGUMENT")
        lamps = dev["lamps"]
        if not lamps["uv_on"]:                                                  # deuterium emission lines: the UV lamp (Lamp Warmup, p.116)
            rt._device["lamps"].update(uv_on=True, uv_since=t, uv_cycles=lamps["uv_cycles"] + 1, uv_ignitions=lamps["uv_ignitions"] + 1,
                                       uv_last_use=t)
            raise _ATReject("LAMP_WARMUP")
        _at_need(t - lamps["uv_since"] >= _DR_UV_WARMUP_S - 1e-9, "LAMP_WARMUP")
        measured = wl - dev["errors"]["wavelength_offset_m"]                     # the line appears where the scale says nominal - offset
        passed = abs(measured - wl) <= crit["tolerance"] + 1e-15
        checks[chk] = dict(state="done", nominal_m=wl, measured_m=measured, bandwidth_m=2e-9 if abs(wl - 656.1e-9) < 1e-13 else None,
                           tolerance_m=crit["tolerance"], passed=passed, result="Passed" if passed else "Failed", time=_at_now(dev, t))
        _dr_check_log(dev, t, "Wavelength check", f"{wl * 1e9:.1f} nm: {measured * 1e9:.2f} nm", passed)
        return {"check": chk, "state": "done", "passed": passed}
    if chk in ("stray_light", "absorbance", "noise", "pipette"):
        if chk == "stray_light":
            wl = 340e-9
        elif chk == "pipette":
            pip = next((p for p in dev["aqa"]["pipettes"] if p["name"] == a.get("pipette")), None)
            _at_need(pip is not None or act != "zero", "NOT_FOUND")
            wl = pip["wavelength_m"] if pip else (checks.get(chk) or {}).get("wavelength_m", 500e-9)
        else:
            wl = a.get("wavelength_m", (checks.get(chk) or {}).get("wavelength_m", 500e-9))
        c = checks.get(chk)
        if act == "zero":
            if chk in ("stray_light", "absorbance"):
                _at_need(not state["sample.loaded"] and dev["kit"]["placed"] is None, "CUVETTE_PRESENT")   # remove any cells (p.117)
            n = 30 if chk == "noise" else 1                                     # noise: 30 zero measurements averaged (p.119)
            zs = [_dr_measure(rt, dev, state, t, [wl])[0] for _ in range(n)]
            z = sum(zs) / n
            prev = c if (c and chk == "absorbance" and c.get("wavelength_m") == wl and c["state"] != "done") else None
            checks[chk] = dict(state="zeroed", wavelength_m=wl, zero=z, n_zero=n, zero_sd=(sum((x - z) ** 2 for x in zs) / (n - 1)) ** 0.5 if n > 1 else 0.0,
                               readings=(prev or {}).get("readings", []), time=_at_now(dev, t), pipette=a.get("pipette"), criteria=crit)
            return {"check": chk, "state": "zeroed"}
        _at_need(c is not None and c["state"] == "zeroed", "ZERO_FIRST")
        crit = dict(c.get("criteria") or {}, **{k: a[k] for k in ("required", "nominal", "tolerance", "limit") if k in a})
        c["criteria"] = crit
        if chk == "stray_light":
            vals = [_dr_measure(rt, dev, state, t, [wl])[0] - c["zero"] for _ in range(3)]
            m = sum(vals) / 3
            passed = m >= crit["required"] - 1e-12                              # compare with the required value of the analysis cell (p.117)
            c.update(state="done", result=m, readings=vals, passed=passed)
            _dr_check_log(dev, t, "Stray light check", f"340 nm: {m:.4f} Abs (required {crit['required']:g})", passed)
        elif chk == "noise":
            vals = [_dr_measure(rt, dev, state, t, [wl])[0] - c["zero"] for _ in range(100)]
            m = sum(vals) / len(vals)
            sd = (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5
            passed = sd <= crit["limit"] + 1e-15                                # the user determines the criteria (p.119)
            c.update(state="done", result=m, sd=sd, sample_sd=sd, reference_sd=c["zero_sd"], passed=passed)
            _dr_check_log(dev, t, "Noise check", f"{wl * 1e9:.0f} nm: SD {sd:.5f} Abs (limit {crit['limit']:g})", passed)
        elif chk == "absorbance":
            v = _dr_measure(rt, dev, state, t, [wl])[0] - c["zero"]
            c["readings"] = c["readings"] + [v]
            c["state"] = "read"                                                  # Zero / Read pairs until 5 comparison measurements (p.118)
            if len(c["readings"]) >= 5:
                m = sum(c["readings"]) / 5
                passed = crit.get("nominal") is None or abs(m - crit["nominal"]) <= crit["tolerance"] + 1e-12
                c.update(state="done", result=m, sd=(sum((x - m) ** 2 for x in c["readings"]) / 4) ** 0.5, passed=passed)
                _dr_check_log(dev, t, "Absorbance check", f"{wl * 1e9:.0f} nm: mean {m:.4f} SD {c['sd']:.4f}" +
                              ("" if crit.get("nominal") is None else f" (nominal {crit['nominal']:g} +-{crit['tolerance']:g})"), passed)
        else:
            pip = next((p for p in dev["aqa"]["pipettes"] if p["name"] == c["pipette"]), None)
            _at_need(pip is not None, "NOT_FOUND")
            v = _dr_measure(rt, dev, state, t, [wl])[0] - c["zero"]
            passed = pip["lower"] <= v <= pip["upper"]
            c.update(state="done", result=v, passed=passed)
            _at_log(dev["aqa_log"], dict(time=_at_now(dev, t), kind="Pipette", name=pip["name"], program=None, target=(pip["lower"] + pip["upper"]) / 2,
                                         ci=(pip["upper"] - pip["lower"]) / 2, value=v, result="Passed" if passed else "Failed"), cap=1000)
            _dr_check_log(dev, t, "Pipette check", f"{pip['name']}: {v:.4f} Abs ({pip['lower']:g}-{pip['upper']:g})", passed)
        return {"check": chk, "state": c["state"], **({"passed": c["passed"]} if "passed" in c else {})}
    wl = a.get("wavelength_m", 500e-9)                                          # drift: a reading every minute for one hour (p.119)
    _at_need(act == "start", "INVALID_ARGUMENT")
    _at_need(dev["drift"] is None, "BUSY")
    v = _dr_measure(rt, dev, state, t, [wl])[0]
    dev["drift"] = dict(start_s=t, wavelength_m=wl, points=[[0.0, v]], running=True)
    checks[chk] = dict(state="running", wavelength_m=wl, slopes=[], time=_at_now(dev, t), criteria=crit)
    return {"check": chk, "state": "running"}


def _dr_c_kit_place(rt, dev, state, a, t):
    """Hands / evaluator: put a filter of the Verification Kit into the universal cell adapter of compartment (2), or take it out (p.115-116).
    The cover must be open and no cell may be in the compartment."""
    f = a.get("filter")
    _at_need(state["lid.angle_rad"] >= _dr6000_math.pi / 3 - 1e-12, "COVER_CLOSED")
    if f is not None:
        _at_need(f in DR_KIT_FILTERS, "NOT_FOUND")
        _at_need(not state["sample.loaded"], "CUVETTE_PRESENT")
    dev["kit"]["placed"] = f
    return {"filter": f}


def _dr_c_errors(rt, dev, state, a, t):
    """Evaluator: optics error parameters of the instrument (default 0 = ideal optics)."""
    e = a["errors"]
    _at_need(bool(e) and set(e) <= set(_DR_ERRORS0), "INVALID_ARGUMENT")
    dev["errors"].update({k: float(v) for k, v in e.items()})
    return {"errors": dict(dev["errors"])}


def _dr_c_ver_config(rt, dev, state, a, t):
    """Verification Kit > Nominal Values > Edit (p.115): the filters' nominal values and tolerances from the quality control certificate."""
    _dr_ready(dev, state)
    _dr_security(dev, "System Checks", a.get("password"))
    _at_need(dev["verification"] is None or dev["verification"]["state"] == "done", "BUSY")
    lines = dev["kit"]["nominal"]
    _at_need(0 <= a["index"] < len(lines), "NOT_FOUND")
    line = lines[a["index"]]
    for k in ("nominal", "tolerance", "wavelength_m"):
        if k in a:
            line[k] = float(a[k])
    if line["test"] == "wavelength" and "nominal" in a:
        line["wavelength_m"] = line["nominal"]
    _at_need(line["tolerance"] >= 0 and 190e-9 <= line["wavelength_m"] <= 1100e-9, "INVALID_ARGUMENT")
    return {"index": a["index"]}


_DR_VER_SPAN = 5e-9                                                              # wavelength lines: peak search +-5 nm in 0.1 nm steps


def _dr_c_ver_run(rt, dev, state, a, t):
    """Verification (p.115-116): adapter in compartment (2), no cells, cover closed, Start (the zero readings); then insert the filters one
    after another in the order displayed and press Next after each; after the last reading the results with Passed / Failed (check log)."""
    _dr_ready(dev, state)
    _dr_security(dev, "System Checks", a.get("password"))
    act = a["action"]
    lines = dev["kit"]["nominal"]
    if act == "cancel":
        _at_need(dev["verification"] is not None, "PRECONDITION_FAILED")
        dev["verification"] = None
        return {"state": "cancelled", "step": 0}
    _at_need(state["lid.closed"], "COVER_OPEN")
    if act == "start":
        _at_need(not state["sample.loaded"] and dev["kit"]["placed"] is None, "CUVETTE_PRESENT")
        zero = {}
        for i, line in enumerate(lines):
            if line["test"] == "wavelength":
                _dr_lamps_for(rt, dev, [line["wavelength_m"] - _DR_VER_SPAN, line["wavelength_m"] + _DR_VER_SPAN], t)
                axis, vals = _dr_scan_absorbance(rt, state, line["wavelength_m"] - _DR_VER_SPAN, line["wavelength_m"] + _DR_VER_SPAN, 0.1e-9)
                _dr_record(rt, state, axis, vals)
                zero[i] = vals
            else:
                zero[i] = _dr_measure(rt, dev, state, t, [line["wavelength_m"]])
        dev["verification"] = dict(state="filter", step=0, zero=zero, results=[], time=_at_now(dev, t))
        return {"state": "filter", "step": 0}
    v = dev["verification"]
    _at_need(v is not None and v["state"] == "filter", "PRECONDITION_FAILED")
    line = lines[v["step"]]
    if line["test"] == "wavelength":
        lo, hi = line["wavelength_m"] - _DR_VER_SPAN, line["wavelength_m"] + _DR_VER_SPAN
        _dr_lamps_for(rt, dev, [lo, hi], t)
        axis, vals = _dr_scan_absorbance(rt, state, lo, hi, 0.1e-9)
        _dr_record(rt, state, axis, vals)
        rel = [x - z for x, z in zip(vals, v["zero"][v["step"]])]
        k = max(range(len(rel)), key=lambda i: rel[i])
        measured = axis[k]
        passed = abs(measured - line["nominal"]) <= line["tolerance"] + 1e-13
        text = f"{line['filter']} {line['nominal'] * 1e9:.1f} nm: {measured * 1e9:.1f} nm"
    else:
        measured = _dr_measure(rt, dev, state, t, [line["wavelength_m"]])[0] - v["zero"][v["step"]][0]
        passed = (measured >= line["nominal"] - line["tolerance"] - 1e-12) if line["test"] == "stray" else \
            abs(measured - line["nominal"]) <= line["tolerance"] + 1e-12
        text = f"{line['filter']} {line['wavelength_m'] * 1e9:.0f} nm: {measured:.4f} Abs (nominal {line['nominal']:g})"
    v["results"].append(dict(filter=line["filter"], test=line["test"], measured=measured, passed=passed, text=text))
    v["step"] += 1
    if v["step"] >= len(lines):
        v["state"] = "done"
        for r in v["results"]:
            _dr_check_log(dev, t, {"stray": "Verif. stray light", "photometric": "Verif. photometric",
                                   "wavelength": "Verif. wavelength"}[r["test"]], r["text"], r["passed"])
        v["passed"] = all(r["passed"] for r in v["results"])
    return {"state": v["state"], "step": v["step"], "passed": passed}


def _dr_ratio_rows(dev, r):
    """Ratio analysis (p.52-55): per sample (sample ID with its number, per day) the latest reading of each parameter; the last parameter
    is 1 and the others are its multiples; values outside the control limits are flagged."""
    groups = {}
    for e in reversed(dev["data_log"]):
        sid = e.get("sample_id") or ""
        if sid.split(" (")[0] != r["sample_id"] or e["conc"] is None or e["program"] not in r["parameters"]:
            continue
        day = e["time"][:10]
        if (r.get("start_date") and day < r["start_date"]) or (r.get("end_date") and day > r["end_date"]):
            continue
        groups.setdefault((sid, day), {})[e["program"]] = e["conc"]
    rows = []
    lim = r.get("limits") or [None] * 4
    for (sid, day), vals in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0])):
        if not all(p in vals for p in r["parameters"]) or vals[r["parameters"][-1]] == 0:
            continue
        base = vals[r["parameters"][-1]]
        ratios = [vals[p] / base for p in r["parameters"][:-1]]
        out = [any(x is not None and cond for x, cond in ((lim[2 * i], ratios[i] < (lim[2 * i] or 0)), (lim[2 * i + 1], ratios[i] > (lim[2 * i + 1] or 0))))
               for i in range(len(ratios))]
        rows.append(dict(sample_id=sid, date=day, values=[vals[p] for p in r["parameters"]], ratios=ratios + [1.0], out=out))
    return rows


def _dr_c_ratio(rt, dev, state, a, t):
    """Data analysis > Ratios (p.52-55): sample ID, parameters I (greatest), II (middle), III (smallest; optional), interval (fixed or
    start / end date) and control limits for the first and second ratio."""
    _dr_ready(dev, state)
    if a["action"] == "delete":
        _at_need(isinstance(a.get("index"), int) and 0 <= a["index"] < len(dev["ratios"]), "NOT_FOUND")
        del dev["ratios"][a["index"]]
        return {"ratios": len(dev["ratios"])}
    params = a.get("parameters") or []
    _at_need(a.get("sample_id") and 2 <= len(params) <= 3 and len(set(params)) == len(params), "INVALID_ARGUMENT")
    logged = {e["program"] for e in dev["data_log"] if (e.get("sample_id") or "").split(" (")[0] == a["sample_id"]}
    _at_need(set(params) <= logged, "NO_DATA")                                     # "No valid data for these parameters!"
    start, end = a.get("start_date"), a.get("end_date")
    if a.get("days"):
        today = _at_dt.date.fromisoformat(_at_now(dev, t)[:10])
        start, end = (today - _at_dt.timedelta(days=a["days"])).isoformat(), today.isoformat()
    lim = list(a.get("limits") or [None] * 4) + [None] * 4
    dev["ratios"].append(dict(sample_id=a["sample_id"], parameters=list(params), start_date=start, end_date=end, days=a.get("days"),
                              limits=lim[:4]))
    return {"ratios": len(dev["ratios"])}


def _dr_c_aqa(rt, dev, state, a, t):
    """AQA configuration (p.120-125): standards (name, concentration, unit, confidence interval, reminder), addition, multiple
    determination and dilution reminders, pipettes for the pipette check."""
    _dr_ready(dev, state)
    _dr_security(dev, "System Checks", a.get("password"))
    aqa = dev["aqa"]
    item, act, s = a["item"], a["action"], a.get("settings") or {}
    if item in ("standard", "pipette"):
        lst = aqa["standards"] if item == "standard" else aqa["pipettes"]
        if act == "new":
            if item == "standard":
                _at_need({"name", "concentration", "ci"} <= set(s), "INVALID_ARGUMENT")
                lst.append(dict(name=s["name"], concentration=float(s["concentration"]), unit=s.get("unit", "mg/L"), ci=float(s["ci"]),
                                reminder_days=s.get("reminder_days")))
            else:
                _at_need({"name", "wavelength_m", "lower", "upper"} <= set(s) and s["lower"] <= s["upper"], "INVALID_ARGUMENT")
                lst.append(dict(name=s["name"], wavelength_m=float(s["wavelength_m"]), lower=float(s["lower"]), upper=float(s["upper"])))
            return {"count": len(lst)}
        _at_need(isinstance(a.get("index"), int) and 0 <= a["index"] < len(lst), "NOT_FOUND")
        if act == "delete":
            del lst[a["index"]]
        else:
            allowed = {"name", "concentration", "unit", "ci", "reminder_days"} if item == "standard" else {"name", "wavelength_m", "lower", "upper"}
            _at_need(set(s) <= allowed, "INVALID_ARGUMENT")
            lst[a["index"]].update(s)
        return {"count": len(lst)}
    _at_need(act == "set" and set(s) <= set(aqa[item]), "INVALID_ARGUMENT")
    aqa[item].update(s)
    return {"count": 1}


def _dr_c_aqa_measure(rt, dev, state, a, t):
    """AQA in the toolbar: the next concentration reading is evaluated against the selected standard (Passed / Failed in the AQA log)."""
    _dr_ready(dev, state)
    aqa = dev["aqa"]
    if a.get("standard") is None:
        aqa["pending"] = None
        return {"pending": None}
    _at_need(0 <= a["standard"] < len(aqa["standards"]), "NOT_FOUND")
    aqa["pending"] = a["standard"]
    return {"pending": a["standard"]}


def _dr_c_service(rt, dev, state, a, t):
    """Service Time (p.126): reminder with the last service date and the interval; 'Next service is due!' at switch-on."""
    _dr_ready(dev, state)
    sv = dev["service"]
    for k in ("enabled", "last_date", "interval_days"):
        if k in a:
            sv[k] = a[k]
    if sv["last_date"] is not None:
        try:
            _at_dt.date.fromisoformat(sv["last_date"])
        except ValueError:
            raise _ATReject("INVALID_ARGUMENT") from None
    return {"enabled": sv["enabled"]}


def _dr_c_lamp_reset(rt, dev, state, a, t):
    """Lamp History (p.127): Reset VIS / Reset UV after a lamp change."""
    _dr_ready(dev, state)
    _dr_security(dev, "System Checks", a.get("password"))
    lamps = dev["lamps"]
    if a["lamp"] == "uv":
        lamps.update(uv_hours_s=0.0, uv_cycles=0, uv_ignitions=0)
    else:
        lamps.update(vis_hours_s=0.0, vis_cycles=0)
    return {"lamp": a["lamp"]}


def _dr_c_trend(rt, dev, state, a, t):
    """Trends (p.51-52, p.129): parameter, sample ID, operator ID and control limits; the readings of the data log over time."""
    _dr_ready(dev, state)
    if a["action"] == "delete":
        _at_need(isinstance(a.get("index"), int) and 0 <= a["index"] < len(dev["trends"]), "NOT_FOUND")
        del dev["trends"][a["index"]]
        return {"trends": len(dev["trends"])}
    _at_need(bool(a.get("parameter")), "INVALID_ARGUMENT")
    _at_need(any(e["program"] == a["parameter"] for e in dev["data_log"]), "NO_DATA")                # "No measurement data present!"
    lo, hi = a.get("lower"), a.get("upper")
    _at_need(lo is None or hi is None or lo < hi, "INVALID_ARGUMENT")
    dev["trends"].append(dict(parameter=a["parameter"], sample_id=a.get("sample_id"), operator=a.get("operator"), lower=lo, upper=hi))
    return {"trends": len(dev["trends"])}


def _dr_c_sleep(rt, dev, state, a, t):
    """Power save key (p.22): sleep mode (display and lamps off); pressed again: the self-check runs, then ready."""
    _at_need(state["power.enabled"], "PRECONDITION_FAILED")
    if dev["phase"] == "sleep":
        dev.update(phase="selfcheck", phase_until=t + _DR_SELFCHECK_S)
        return {"sleep": False}
    _at_need(dev["phase"] == "ready", "NOT_READY")
    _at_need(not (dev["tc"] and dev["tc"]["running"]) and dev["drift"] is None, "BUSY")
    dev.update(phase="sleep", phase_until=None)
    dev["lamps"].update(uv_on=False, uv_since=None)
    return {"sleep": True}


def _dr_c_confirm(rt, dev, state, a, t):
    """OK on a message: a notice is cleared; after a self-check fault (cuvette in the compartment, cover open) the test runs again
    (p.137)."""
    _at_need(state["power.enabled"], "PRECONDITION_FAILED")
    if dev["phase"] == "fault":
        _at_need(dev["fault"] not in _DR_FAULTS, "FAULT")                         # an injected error needs its remedy first (p.139-141)
        _at_need(state["lid.closed"] and not state["sample.loaded"], "FAULT")
        dev.update(phase="selfcheck", phase_until=t + _DR_SELFCHECK_S, fault=None)
        return {"confirmed": True}
    _at_need(dev["notice"] is not None, "PRECONDITION_FAILED")
    dev["notice"] = None
    return {"confirmed": True}


def _dr_c_language(rt, dev, state, a, t):
    """Language list after the startup when the screen was touched (p.21): OK starts the self-check."""
    _at_need(state["power.enabled"] and dev["phase"] == "language", "PRECONDITION_FAILED")
    _at_need(a["language"] in DR_LANGUAGES, "INVALID_ARGUMENT")
    dev.update(language=a["language"], phase="selfcheck", phase_until=t + _DR_SELFCHECK_S, lang_pick=False)
    return {"language": a["language"]}


def _dr_c_fault_inject(rt, dev, state, a, t):
    """Evaluator: an instrument error of the troubleshooting table (p.139-141): the error screen, readings / time course / drift check stop."""
    _at_need(state["power.enabled"] and dev.get("injected") is None, "PRECONDITION_FAILED")
    if a["code"] == "unstable_lighting":
        dev["notice"] = "Unstable lighting conditions!"                         # a warning; readings go on (p.141)
        return {"code": a["code"]}
    dev["injected"] = a["code"]
    dev.update(phase="fault", fault=a["code"], phase_until=None, drift=None)
    if dev["tc"] is not None:
        dev["tc"]["running"] = False
    return {"code": a["code"]}


def _dr_c_fault_clear(rt, dev, state, a, t):
    """Evaluator: the cause is removed; the instrument runs its self-check again and is then ready."""
    dev["injected"] = None
    if dev["phase"] == "fault" and dev["fault"] in _DR_FAULTS:
        dev.update(phase="selfcheck", phase_until=t + _DR_SELFCHECK_S, fault=None)
    if dev["notice"] == "Unstable lighting conditions!":
        dev["notice"] = None
    return {"cleared": True}


def _dr_c_std_add(rt, dev, state, a, t):
    """Standard Addition (p.62-65): standard concentration, sample volume and three addition volumes; the 0 mL row is the current reading;
    a selected row takes the next reading; the regression estimates the sample concentration."""
    _dr_ready(dev, state)
    mode = _dr_mode(dev, ("stored", "user"))
    act = a["action"]
    if act == "off":
        dev["std_add"] = None
        return {"active": False}
    if act == "on":
        r = dev["result"]
        _at_need(r is not None and r["conc"] is not None, "PRECONDITION_FAILED")      # after a sample reading (p.64)
        high = (mode.get("range_mg_l") or [0, 10.0])[1]
        dev["std_add"] = dict(std_conc=a.get("std_conc", round(high * 10, 6)), sample_volume_m3=a.get("sample_volume_m3", 10e-6),
                              volumes_m3=list(a.get("volumes_m3", [0.1e-6, 0.2e-6, 0.3e-6])), readings=[r["conc"], None, None, None], row=None)
        return {"active": True}
    sa = dev["std_add"]
    _at_need(sa is not None, "PRECONDITION_FAILED")
    if act == "configure":
        for k in ("std_conc", "sample_volume_m3", "volumes_m3"):
            if k in a:
                sa[k] = a[k] if k != "volumes_m3" else list(a[k])
        _at_need(len(sa["volumes_m3"]) == 3, "INVALID_ARGUMENT")
    else:
        _at_need(a.get("row") in (1, 2, 3), "INVALID_ARGUMENT")
        sa["row"] = a["row"]
    return {"active": True}


def _dr_std_add_eval(sa):
    """Added concentration, detection rate and regression (estimated sample concentration = intercept, r^2)."""
    rows = []
    for i, v in enumerate([0.0] + sa["volumes_m3"]):
        added = sa["std_conc"] * v / (sa["sample_volume_m3"] + v)
        meas = sa["readings"][i]
        rate = None if i == 0 or meas is None or sa["readings"][0] is None or added == 0 else (meas - sa["readings"][0]) / added * 100.0
        rows.append(dict(volume_m3=v, added=added, reading=meas, rate_pct=rate))
    pts = [(r["added"], r["reading"]) for r in rows if r["reading"] is not None]
    fit = None
    if len(pts) >= 2:
        f = _dr_fit([(y, x) for x, y in pts], 1, False)
        if f is not None:
            fit = dict(intercept=f["coef"][0], slope=f["coef"][1], r2=f["r2"])
    return rows, fit


_DR_COMMANDS = {
    "program.start": _dr_c_start, "program.exit": _dr_c_exit, "program.options": _dr_c_options, "program.standard_adjust": _dr_c_adjust,
    "reading.zero": _dr_c_zero, "reading.read": _dr_c_read, "timecourse.control": _dr_c_tc, "data.store": _dr_c_store, "data.manage": _dr_c_data,
    "user_program.edit": _dr_c_user, "favorites.manage": _dr_c_fav, "operator.manage": _dr_c_operator, "sample_id.manage": _dr_c_sample,
    "security.configure": _dr_c_security, "security.check": _dr_c_check_access, "settings.configure": _dr_c_settings, "clock.set": _dr_c_clock,
    "lamp.set": _dr_c_lamp, "timer.control": _dr_c_timer, "system.check": _dr_c_check, "aqa.configure": _dr_c_aqa,
    "aqa.measure": _dr_c_aqa_measure, "service.configure": _dr_c_service, "lamp_history.reset": _dr_c_lamp_reset, "trend.manage": _dr_c_trend,
    "power.sleep": _dr_c_sleep, "message.confirm": _dr_c_confirm, "language.select": _dr_c_language, "standard_addition.control": _dr_c_std_add,
    "verification_kit.place": _dr_c_kit_place, "sensor.configure_error": _dr_c_errors, "verification.configure": _dr_c_ver_config,
    "verification.run": _dr_c_ver_run, "ratio.manage": _dr_c_ratio, "fault.inject": _dr_c_fault_inject, "fault.clear": _dr_c_fault_clear,
}


def _dr_tick(rt, dev, state, t, last):
    """Clock-driven behaviour up to t: startup / language / self-check / faults, lamp hours and UV lamp save, sleep timer, timer, time course
    and drift readings."""
    if not state["power.enabled"]:
        return
    lamps, s = dev["lamps"], dev["settings"]
    for _ in range(6):
        if dev["phase"] == "startup" and t >= dev["phase_until"] - 1e-9:
            end = dev["phase_until"]
            if dev["lang_pick"]:
                dev.update(phase="language", phase_until=None)
            else:
                dev.update(phase="selfcheck", phase_until=dev["phase_until"] + _DR_SELFCHECK_S)
            if not lamps["uv_on"]:                                              # the self-check checks the lamps (p.21)
                lamps.update(uv_on=True, uv_since=end if dev["phase"] == "selfcheck" else t, uv_cycles=lamps["uv_cycles"] + 1,
                             uv_ignitions=lamps["uv_ignitions"] + 1, uv_last_use=t)
        elif dev["phase"] == "selfcheck" and t >= dev["phase_until"] - 1e-9:
            if dev.get("injected") in _DR_FAULTS:                               # an injected instrument error is found again (p.139-141)
                dev.update(phase="fault", fault=dev["injected"], phase_until=None)
            elif not state["lid.closed"]:
                dev.update(phase="fault", fault="CLOSE_LID", phase_until=None)
            elif state["sample.loaded"]:
                dev.update(phase="fault", fault="REMOVE_CUVETTE", phase_until=None)
            else:
                end = dev["phase_until"]
                dev.update(phase="ready", phase_until=None, last_activity_s=max(dev["last_activity_s"], end))
                if not lamps["uv_on"]:
                    lamps.update(uv_on=True, uv_since=end - _DR_SELFCHECK_S, uv_cycles=lamps["uv_cycles"] + 1, uv_ignitions=lamps["uv_ignitions"] + 1)
                lamps["uv_last_use"] = end
                sv = dev["service"]
                if sv["enabled"] and sv["last_date"]:
                    due = _at_dt.date.fromisoformat(sv["last_date"]) + _at_dt.timedelta(days=sv["interval_days"])
                    if _at_dt.date.fromisoformat(_at_now(dev, end)[:10]) >= due:
                        dev["notice"] = "Next service is due!"
        else:
            break
    if lamps["uv_on"] and lamps["uv_since"] is not None:
        lamps["uv_hours_s"] += max(0.0, t - max(last, lamps["uv_since"]))
    if lamps["vis_on"]:
        lamps["vis_hours_s"] += max(0.0, t - last)
    if dev["phase"] != "ready":
        return
    if lamps["uv_on"] and s["power.uv_save_s"] > 0 and t - lamps["uv_last_use"] >= s["power.uv_save_s"] - 1e-9 and not (dev["tc"] and dev["tc"]["running"]):
        lamps.update(uv_on=False, uv_since=None)
    if dev["timer"] is not None and t >= dev["timer"]["end_s"] - 1e-9:
        dev["notice"] = f"{dev['timer']['name']}: 00:00"                        # the timer beeps when the interval ends (p.58)
        dev["timer"] = None
    tc = dev["tc"]
    if tc is not None and tc["running"]:
        k = len(tc["points"])
        while k * tc["interval_s"] <= min(t - tc["start_s"], tc["total_s"]) + 1e-9:
            try:
                v = _dr_absorbance(rt, state, [tc["wavelength_m"]], tc["start_s"] + k * tc["interval_s"])[0] - tc["zero"] if state["lid.closed"] else None
            except _ATReject:
                v = None
            tc["points"].append([k * tc["interval_s"], v])
            if tc["mark_next"]:
                tc["marks"].append(k)
                tc["mark_next"] = False
            k += 1
        if (k - 1) * tc["interval_s"] >= tc["total_s"] - 1e-9:
            tc["running"] = False                                               # beeps after the overall time (p.112)
    dr = dev["drift"]
    if dr is not None and dr["running"]:
        k = len(dr["points"])
        while k * 60.0 <= min(t - dr["start_s"], 3600.0) + 1e-9:
            try:
                v = _dr_absorbance(rt, state, [dr["wavelength_m"]], dr["start_s"] + k * 60.0)[0] if state["lid.closed"] else None
            except _ATReject:
                v = None
            dr["points"].append([k * 60.0, v])
            k += 1
        c = dev["checks"].get("drift")
        if c is not None:
            slopes = []
            for q in range(1, 5):
                seg = [(x, y) for x, y in dr["points"] if (q - 1) * 900 <= x <= q * 900 and y is not None]
                if seg and seg[-1][0] >= q * 900 - 1e-9 and len(seg) >= 2:
                    f = _dr_fit([(y, x) for x, y in seg], 1, False)
                    slopes.append(dict(minute=q * 15, last=seg[-1][1], slope_per_min=None if f is None else f["coef"][1] * 60.0))
            c["slopes"] = slopes
            if (k - 1) * 60.0 >= 3600.0 - 1e-9:
                dr["running"] = False
                vals = [y for _, y in dr["points"] if y is not None]
                f = _dr_fit([(y, x) for x, y in dr["points"] if y is not None], 1, False)
                slope = None if f is None else f["coef"][1] * 3600.0
                limit = (c.get("criteria") or {}).get("limit", _DR_CRITERIA["drift"]["limit"])
                passed = slope is not None and abs(slope) <= limit + 1e-15
                c.update(state="done", result=max(vals) - min(vals) if vals else None, slope_per_h=slope, passed=passed)
                _dr_check_log(dev, t, "Drift check", f"{dr['wavelength_m'] * 1e9:.0f} nm: {slope or 0:.5f} Abs/h (limit {limit:g})", passed)
                dev["drift"] = None
    if s["power.sleep_timer_s"] > 0 and t - dev["last_activity_s"] >= s["power.sleep_timer_s"] - 1e-9 and not (tc and tc["running"]) \
            and dev["drift"] is None:
        dev.update(phase="sleep", phase_until=None)                             # energy-saving sleep mode after no use (p.41)
        lamps.update(uv_on=False, uv_since=None)


# ---------------------------------------------------------------- contract metadata of the terminal
_DR_SLOT_IDS = ("dr.status", "dr.mode", "dr.zero", "dr.result", "dr.scan", "dr.time_course", "dr.mode_settings", "dr.settings", "dr.data_log",
                "dr.stored_data", "dr.programs", "dr.ids", "dr.security", "dr.lamps", "dr.timer", "dr.checks", "dr.aqa", "dr.aqa_log", "dr.trends",
                "dr.service", "dr.datetime", "dr.std_addition", "dr.system", "dr.check_log", "dr.verification", "dr.ratios", "dr.kit", "dr.optics_errors")
_DR_EVALUATOR_SLOTS = ("dr.kit", "dr.optics_errors")


def _dr_slots():
    nullable = lambda s: {"anyOf": [s, {"type": "null"}]}  # noqa: E731
    lists = {"dr.data_log", "dr.aqa_log", "dr.trends", "dr.check_log", "dr.ratios"}
    out = {}
    for sid in _DR_SLOT_IDS:
        schema = _AT_LIST if sid in lists else _AT_STR if sid == "dr.datetime" else \
            nullable(_AT_ANY) if sid in ("dr.mode", "dr.zero", "dr.result", "dr.scan", "dr.time_course", "dr.timer", "dr.std_addition") else _AT_ANY
        out[sid] = {"value_schema": schema, "unit": "1", "origin": "setpoint" if sid in ("dr.settings", "dr.mode_settings", "dr.security") else "derived",
                    "visibility": "evaluator" if sid in _DR_EVALUATOR_SLOTS else "agent", "access": "read_only", "write_via": None}
    return out


_DR_E_MODE = ["dr.mode", "dr.zero", "dr.result", "dr.scan", "dr.time_course", "dr.std_addition", "dr.mode_settings"]
_DR_E_READ = ["dr.zero", "dr.result", "dr.scan", "dr.time_course", "dr.data_log", "dr.ids", "dr.lamps", "dr.aqa", "dr.aqa_log", "dr.std_addition",
              "dr.programs", "measurement.spectrum", "measurement.count"]
_DR_NUM = lambda unit, lo=None, hi=None: _at_num(unit, lo, hi)  # noqa: E731
_DR_NULLNUM = lambda unit, lo=None, hi=None: {"anyOf": [_at_num(unit, lo, hi), {"type": "null"}]}  # noqa: E731
_DR_WL = _at_num("m", 190e-9, 1100e-9)
_DR_PW = {"type": "string", "minLength": 1, "maxLength": 10}
_DR_OPTIONS_SCHEMA = {"type": "object", "additionalProperties": False, "minProperties": 1, "required": [], "properties": {
    "result_mode": {"type": "string", "enum": ["conc", "abs", "trans"]}, "dilution": _DR_NULLNUM("1", 0.001, 1000), "reagent_blank": _DR_NULLNUM("1"),
    "multiple": {"anyOf": [_at_num("1", 2, 20, True), {"type": "null"}]}, "reading_mode": {"type": "string", "enum": ["single", "continuous"]},
    "form": {"type": "string", "maxLength": 12}, "wavelength_m": _DR_WL, "conc_factor": _DR_NULLNUM("1", -99999, 99999),
    "conc_unit": {"type": "string", "enum": list(DR_UNITS)}, "resolution": {"type": "integer", "x-unit": "1", "enum": [0, 1, 2, 3, 4]},
    "formula": {"type": "integer", "x-unit": "1", "enum": list(range(len(DR_FORMULAS)))},
    "wavelengths_m": {"type": "array", "items": _DR_WL, "minItems": 4, "maxItems": 4},
    "k": {"type": "array", "items": _at_num("1", -99999, 99999), "minItems": 4, "maxItems": 4}, "start_m": _DR_WL, "stop_m": _DR_WL,
    "step_m": _at_num("m", 0.1e-9, 5e-9), "units": {"type": "string", "enum": ["abs", "trans"]}, "scale": {"type": "string", "enum": ["auto", "manual"]},
    "y_min": _at_num("1", -10, 200), "y_max": _at_num("1", -10, 200), "cursor": {"type": "string", "enum": ["track", "peak"]}, "integral": _AT_BOOL,
    "reference": {"anyOf": [_at_num("1", 1, 50, True), {"type": "null"}]}, "view": {"type": "string", "enum": ["graph", "table"]},
    "total_s": _at_num("s", 1, 86400), "interval_s": _at_num("s", 1, 3600)}}
_DR_CAPDEFS = {
    "program.start": ({"kind": {"type": "string", "enum": list(_DR_KIND_FUNCTION)}, "number": _at_num("1", 1, 9099, True), "password": _DR_PW},
                      ("number", "password"), {"mode": _AT_STR, "number": {"anyOf": [_at_num("1", 1, 9099, True), {"type": "null"}]},
                                               "wavelengths_m": {"type": "array", "items": _at_num("m")}},
                      {"kind": "stored", "number": 490}, "主菜单启动程序或模式（存储程序、用户程序、收藏、单/多波长、波长扫描、时间过程、条码程序）；"
                      "受保护的功能需安全密码（p.24、p.31、p.56）", _DR_E_MODE, "device_command"),
    "program.exit": ({}, (), {"mode": {"type": "null"}}, {}, "返回主菜单：程序的临时选项（试剂空白、化学形式、标准调整）结束（p.59-60）", _DR_E_MODE,
                     "device_command"),
    "program.options": ({"settings": _DR_OPTIONS_SCHEMA}, (), {"settings": _AT_ANY}, {"settings": {"dilution": 2.0}},
                        "当前程序/模式的Options：结果模式%T/Conc/Abs、稀释因子、试剂空白、多次测定、读数模式、化学形式、波长、吸光度公式与K、浓度因子/单位/"
                        "分辨率、扫描范围/步长/单位/标尺/光标/积分/参比、时间过程总时间/间隔/波长（表4、12-14、16）", _DR_E_MODE, "configuration"),
    "program.standard_adjust": ({"action": {"type": "string", "enum": ["adjust", "off"]}, "standard": _at_num("1", -1e6, 1e6)}, ("standard",),
                                {"factor": _at_num("1")}, {"action": "adjust", "standard": 0.5},
                                "Standard Adjust：读取已知标准后按标准值调整校准（限值内，p.59）", ["dr.mode", "dr.result"], "configuration"),
    "reading.zero": ({}, (), {"zeroed": _AT_BOOL, "points": _at_num("1", 0, None, True)}, {},
                     "Zero：零溶液比色皿在模式波长下读数（扫描为基线，自由编程为测量过程第一步），之后Read才可用（p.60、p.100、p.107、p.112）",
                     _DR_E_READ, "device_command"),
    "reading.read": ({}, (), {"abs": {"type": "array"}, "conc": _DR_NULLNUM("1"), "warnings": {"type": "array"}, "points": _at_num("1", 0, None, True),
                              "max_abs": _at_num("1"), "max_wavelength_m": _at_num("m"), "started": _AT_BOOL, "step": _at_num("1", 0, None, True),
                              "complete": _AT_BOOL}, {}, "Read：样品相对零点读数并计算结果（浓度、Abs、%T），或波长扫描，或启动时间过程（p.60、p.100、p.107、p.112）",
                     _DR_E_READ, "device_command"),
    "timecourse.control": ({"action": {"type": "string", "enum": ["mark", "stop"]}}, (), {"running": _AT_BOOL}, {"action": "mark"},
                           "时间过程运行中的Mark（标记下一点）与Stop（p.112）", ["dr.time_course"], "device_command"),
    "data.store": ({"target": {"type": "string", "enum": ["log", "scan", "time"]}, "slot": _at_num("1", 1, 50, True)}, ("slot",),
                   {"stored": _AT_BOOL, "slot": {"anyOf": [_at_num("1", 1, 50, True), {"type": "null"}]}}, {"target": "scan", "slot": 1},
                   "保存：Auto Store关闭时手动存当前读数，或把扫描/时间过程存入编号行（各50，可覆盖，p.43、p.48）", ["dr.data_log", "dr.stored_data", "dr.result"],
                   "device_command"),
    "data.manage": ({"source": {"type": "string", "enum": ["log", "scan", "time", "aqa", "checks"]}, "action": {"type": "string", "enum": ["delete", "send"]},
                     "scope": {"type": "string", "enum": ["single", "filtered", "all"]}, "index": _at_num("1", 0, 5000, True),
                     "filter": {"type": "object", "properties": {"sample_id": {"type": "string"}, "operator": {"type": "string"},
                                                                 "start_date": {"type": "string", "maxLength": 10},
                                                                 "parameter": {"type": "string"}}, "additionalProperties": False}},
                    ("scope", "index", "filter"), {"deleted": _at_num("1", 0, None, True)}, {"source": "log", "action": "delete", "scope": "all"},
                    "Recall Data：删除或发送单点/筛选/全部数据（数据记录、扫描、时间过程、AQA记录）；发送需要U盘、打印机或网络驱动器（p.43-50）",
                    ["dr.data_log", "dr.stored_data", "dr.aqa_log"], "device_command"),
    "user_program.edit": ({"action": {"type": "string", "enum": ["new", "edit", "set", "calib_add", "calib_set", "calib_delete", "calib_zero",
                                                                 "calib_read", "store", "cancel", "delete", "favorite"]},
                           "number": _at_num("1", 9000, 9099, True), "settings": _at_settings_schema(DR_PROGRAM_SETTINGS),
                           "index": _at_num("1", 0, 23, True), "concentration": _at_num("1", -1e6, 1e6), "absorbance": _DR_NULLNUM("1", -10, 10),
                           "password": _DR_PW}, ("number", "settings", "index", "concentration", "absorbance", "password"),
                          {"number": _at_num("1", 9000, 9099, True)}, {"action": "new"},
                          "用户程序：新建/编辑草稿（编号9000-9099、名称、类型、单位、波长、公式与K、分辨率、化学形式、按数值/测标准/公式校准与阶次、Force 0、"
                          "上下限、计时器；自由编程测量过程、公式与变量），存储、取消、删除、加入收藏（p.77-97）", ["dr.programs", "measurement.spectrum",
                                                                                           "measurement.count", "dr.lamps"], "configuration"),
    "favorites.manage": ({"action": {"type": "string", "enum": ["add", "remove"]}, "kind": {"type": "string", "enum": ["stored", "user"]},
                          "number": _at_num("1", 1, 9099, True)}, (), {"favorites": _at_num("1", 0, None, True)},
                         {"action": "add", "kind": "stored", "number": 490}, "收藏：加入存储/用户程序，移除收藏（程序本身保留，p.66-67、p.97-98）",
                         ["dr.programs"], "configuration"),
    "operator.manage": ({"action": {"type": "string", "enum": ["new", "edit", "delete", "login", "logout", "set_level"]},
                         "name": {"type": "string", "minLength": 1, "maxLength": 10}, "new_name": {"type": "string", "minLength": 1, "maxLength": 10},
                         "password": {"type": "string", "maxLength": 10}, "level": {"type": "string", "enum": list(DR_LEVELS)},
                         "security_password": _DR_PW}, ("name", "new_name", "password", "level", "security_password"),
                        {"operator": {"anyOf": [_AT_STR, {"type": "null"}]}}, {"action": "new", "name": "OP1"},
                        "操作员ID：最多30个（10字符），可设密码与安全级别；登录/登出（p.25-26、p.31-32、p.127）", ["dr.ids"], "configuration"),
    "sample_id.manage": ({"action": {"type": "string", "enum": ["new", "edit", "delete", "select", "off"]},
                          "name": {"type": "string", "minLength": 1, "maxLength": 20}, "new_name": {"type": "string", "minLength": 1, "maxLength": 20},
                          "add_date": _AT_BOOL, "add_number": _AT_BOOL, "first_number": _at_num("1", 0, 9999, True)},
                         ("name", "new_name", "add_date", "add_number", "first_number"), {"sample_id": {"anyOf": [_AT_STR, {"type": "null"}]}},
                         {"action": "new", "name": "Inflow", "add_number": True}, "样品ID：最多100个（20字符），可加日期时间与顺序编号；选择/关闭（p.27-28）",
                         ["dr.ids"], "configuration"),
    "security.configure": ({"action": {"type": "string", "enum": ["on", "off", "level"]}, "password": _DR_PW, "current_password": _DR_PW,
                            "function": {"type": "string", "enum": list(DR_FUNCTIONS)}, "level": {"type": "string", "enum": list(DR_LEVELS)}},
                           ("password", "current_password", "function", "level"), {"enabled": _AT_BOOL}, {"action": "on", "password": "1234"},
                           "安全设置：安全密码开/关，安全列表中各功能的级别（关/一把钥匙/两把钥匙，p.29-33）", ["dr.security"], "configuration"),
    "security.check": ({"function": {"type": "string", "enum": list(DR_FUNCTIONS)}, "password": _DR_PW}, ("password",), {"allowed": _AT_BOOL},
                       {"function": "Instrument Setup"}, "进入受保护的功能前检查权限（未达级别时需安全密码，p.31）", [], "device_command"),
    "settings.configure": ({"settings": _at_settings_schema(DR_SETTINGS)}, (), {"settings": _AT_ANY}, {"settings": {"log.auto_store": False}},
                           "仪器设置：日期时间格式、声音、打印机、网络、电源管理、UV切换波长、数据记录设置（p.33-43）", ["dr.settings"], "configuration"),
    "clock.set": ({"date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                   "time": {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?$"}}, (), {"datetime": _AT_STR},
                  {"date": "2026-10-19", "time": "09:30"}, "设置日期与时间（p.34）", ["dr.datetime"], "configuration"),
    "lamp.set": ({"lamp": {"type": "string", "enum": ["vis", "uv"]}, "on": _AT_BOOL}, (), {"lamp": _AT_STR, "on": _AT_BOOL}, {"lamp": "uv", "on": True},
                 "灯控制：手动开/关可见光灯与UV灯（UV灯开后需预热，p.41-42）", ["dr.lamps"], "configuration"),
    "timer.control": ({"action": {"type": "string", "enum": ["start", "cancel"]}, "duration_s": _at_num("s", 1, 5999), "name": {"type": "string",
                                                                                                                         "maxLength": 20}},
                      ("duration_s", "name"), {"running": _AT_BOOL}, {"action": "start", "duration_s": 120.0},
                      "计时器：通用或程序计时器倒计时，结束时鸣响，不影响读数（p.57-58、p.128）", ["dr.timer"], "device_command"),
    "system.check": ({"check": {"type": "string", "enum": ["wavelength", "stray_light", "absorbance", "noise", "drift", "pipette"]},
                      "action": {"type": "string", "enum": ["zero", "start", "read", "cancel"]}, "wavelength_m": _DR_WL,
                      "pipette": {"type": "string", "maxLength": 20}, "password": _DR_PW, "required": _at_num("1", -10, 10),
                      "nominal": _at_num("1", -10, 10), "tolerance": _at_num("1", 0, 10), "limit": _at_num("1", 0, 10)},
                     ("wavelength_m", "pipette", "password", "required", "nominal", "tolerance", "limit"),
                     {"check": _AT_STR, "state": _AT_STR, "passed": _AT_BOOL}, {"check": "wavelength", "action": "start", "wavelength_m": 6.561e-07},
                     "系统检查：波长检查、杂散光、吸光度、噪声、漂移检查与移液器检查；读数含评测者设定的光学误差，按判据（要求值、标称与容差、噪声/漂移限）"
                     "判定合格/不合格并记入检查记录（p.116-125）",
                     ["dr.checks", "dr.lamps", "dr.aqa_log", "dr.check_log", "measurement.spectrum", "measurement.count"], "device_command"),
    "verification_kit.place": ({"filter": {"anyOf": [{"type": "string", "enum": list(DR_KIT_FILTERS)}, {"type": "null"}]}}, (),
                               {"filter": {"anyOf": [_AT_STR, {"type": "null"}]}}, {"filter": "NG10"},
                               "评测者/手：把验证套件的玻璃滤光片放入比色室(2)的通用比色皿适配器，或取出（盖须打开，比色室内无比色皿；p.115-116）",
                               ["dr.kit"], "device_command"),
    "sensor.configure_error": ({"errors": {"type": "object", "additionalProperties": False, "minProperties": 1, "required": [], "properties": {
        "wavelength_offset_m": _at_num("m", -1e-8, 1e-8), "stray_light_fraction": _at_num("1", 0, 0.1), "photometric_gain": _at_num("1", -0.5, 0.5),
        "photometric_offset_abs": _at_num("1", -1, 1), "noise_sd_abs": _at_num("1", 0, 0.1), "drift_abs_per_h": _at_num("1", -1, 1)}}}, (),
                               {"errors": _AT_ANY}, {"errors": {"wavelength_offset_m": 2e-9}},
                               "评测者：仪器光学误差参数（波长偏移、杂散光比例、光度增益与偏移、噪声标准差、漂移；默认0即理想光学）", ["dr.optics_errors"],
                               "configuration"),
    "verification.configure": ({"index": _at_num("1", 0, 5, True), "nominal": _at_num("1", -10, 10), "tolerance": _at_num("1", 0, 10),
                                "wavelength_m": _DR_WL, "password": _DR_PW}, ("nominal", "tolerance", "wavelength_m", "password"),
                               {"index": _at_num("1", 0, 5, True)}, {"index": 1, "nominal": 0.5, "tolerance": 0.005},
                               "验证套件 > Nominal Values > Edit：按质量控制证书输入各滤光片的波长、标称值与容差（杂散光、光度准确度、波长准确度，p.115）",
                               ["dr.verification"], "configuration"),
    "verification.run": ({"action": {"type": "string", "enum": ["start", "next", "cancel"]}, "password": _DR_PW}, ("password",),
                         {"state": _AT_STR, "step": _at_num("1", 0, 6, True), "passed": _AT_BOOL}, {"action": "start"},
                         "Verification：适配器在比色室(2)、无比色皿、盖关闭后Start（零点读数）；按显示顺序逐片放入滤光片并按Next；最后一片后显示合格/不合格结果并记入检查记录（p.115-116）",
                         ["dr.verification", "dr.check_log", "dr.lamps", "measurement.spectrum", "measurement.count"], "device_command"),
    "ratio.manage": ({"action": {"type": "string", "enum": ["new", "delete"]}, "sample_id": {"type": "string", "minLength": 1, "maxLength": 20},
                      "parameters": {"type": "array", "items": {"type": "string", "maxLength": 40}, "minItems": 2, "maxItems": 3},
                      "days": {"anyOf": [_at_num("1", 1, 3650, True), {"type": "null"}]}, "start_date": {"type": "string", "maxLength": 10},
                      "end_date": {"type": "string", "maxLength": 10}, "limits": {"type": "array", "items": {"anyOf": [_at_num("1"), {"type": "null"}]},
                                                                                   "minItems": 4, "maxItems": 4},
                      "index": _at_num("1", 0, 99, True)}, ("sample_id", "parameters", "days", "start_date", "end_date", "limits", "index"),
                     {"ratios": _at_num("1", 0, None, True)}, {"action": "new", "sample_id": "Inflow", "parameters": ["490 P React. PP", "10 Aluminum Alumin"]},
                     "数据分析 > Ratios：样品ID、参数I（最大）、II、III（最小，可选）、时间区间与前两个比值的控制限；表格中超限值标记（p.52-55）",
                     ["dr.ratios"], "configuration"),
    "aqa.configure": ({"item": {"type": "string", "enum": ["standard", "addition", "multiple", "dilution", "pipette"]},
                       "action": {"type": "string", "enum": ["new", "edit", "delete", "set"]}, "index": _at_num("1", 0, 99, True),
                       "settings": {"type": "object"}, "password": _DR_PW}, ("index", "settings", "password"), {"count": _at_num("1", 0, None, True)},
                      {"item": "standard", "action": "new", "settings": {"name": "PO4 1.0", "concentration": 1.0, "unit": "mg/L", "ci": 0.1}},
                      "AQA配置：标准（名称、浓度、单位、置信区间、提醒）、加标、多次测定、稀释提醒、移液器检查定义（p.120-125）", ["dr.aqa"], "configuration"),
    "aqa.measure": ({"standard": {"anyOf": [_at_num("1", 0, 99, True), {"type": "null"}]}}, (), {"pending": {"anyOf": [_at_num("1", 0, 99, True),
                                                                                                                      {"type": "null"}]}},
                    {"standard": 0}, "工具栏AQA：下一次浓度读数按所选标准评价（合格/不合格记入AQA记录，p.123）", ["dr.aqa"], "device_command"),
    "service.configure": ({"enabled": _AT_BOOL, "last_date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                           "interval_days": _at_num("1", 1, 3650, True)}, ("last_date", "interval_days"), {"enabled": _AT_BOOL},
                          {"enabled": True, "last_date": "2026-01-01", "interval_days": 365}, "服务时间提醒：上次服务日期与间隔，开机时提示到期（p.126）",
                          ["dr.service"], "configuration"),
    "lamp_history.reset": ({"lamp": {"type": "string", "enum": ["vis", "uv"]}, "password": _DR_PW}, ("password",), {"lamp": _AT_STR}, {"lamp": "uv"},
                           "灯历史：换灯后复位VIS/UV的运行小时与次数（p.127）", ["dr.lamps"], "configuration"),
    "trend.manage": ({"action": {"type": "string", "enum": ["new", "delete"]}, "parameter": {"type": "string", "maxLength": 40},
                      "sample_id": {"type": "string", "maxLength": 20}, "operator": {"type": "string", "maxLength": 10}, "lower": _at_num("1"),
                      "upper": _at_num("1"), "index": _at_num("1", 0, 99, True)}, ("parameter", "sample_id", "operator", "lower", "upper", "index"),
                     {"trends": _at_num("1", 0, None, True)}, {"action": "new", "parameter": "490 P React. PV"},
                     "趋势：参数、样品ID、操作员与控制限；显示数据记录中的读数随时间变化（p.51-52、p.129）", ["dr.trends"], "configuration"),
    "power.sleep": ({}, (), {"sleep": _AT_BOOL}, {}, "省电键：进入睡眠（屏幕与灯关闭），再按一次运行自检后就绪（p.22）", ["dr.status", "dr.lamps"],
                    "device_command"),
    "message.confirm": ({}, (), {"confirmed": _AT_BOOL}, {}, "确认消息；自检故障（比色皿未取出、盖未关）排除后按OK重新自检（p.137）",
                        ["dr.status"], "device_command"),
    "language.select": ({"language": {"type": "string", "enum": list(DR_LANGUAGES)}}, (), {"language": _AT_STR}, {"language": "English"},
                        "启动时触摸屏幕后出现的语言列表：选择语言并开始自检（p.21）", ["dr.status", "dr.system"], "configuration"),
    "standard_addition.control": ({"action": {"type": "string", "enum": ["on", "off", "configure", "select"]},
                                   "std_conc": _at_num("1", 0, 1e6), "sample_volume_m3": _at_num("m^3", 1e-7, 1e-3),
                                   "volumes_m3": {"type": "array", "items": _at_num("m^3", 0, 1e-4), "minItems": 3, "maxItems": 3},
                                   "row": _at_num("1", 1, 3, True)}, ("std_conc", "sample_volume_m3", "volumes_m3", "row"), {"active": _AT_BOOL},
                                  {"action": "on"}, "标准加入：标准浓度、样品体积与三个加入体积；0 mL行为当前读数，所选行取下一次读数，回归估计样品浓度（p.62-65）",
                                  ["dr.std_addition"], "device_command"),
    "fault.inject": ({"code": {"type": "string", "enum": ["hardware_error", "lamp_failure", "ambient_light", "temperature_high", "system_check_incorrect",
                                                          "unstable_lighting"]}}, (), {"code": _AT_STR}, {"code": "lamp_failure"},
                     "评测者：注入手册第8节（p.139-141）的仪器错误：Selfcheck stopped / Hardware error、Test program stopped! Please check lamp、Too much ambient light!、"
                     "Temperature too high. Measurement not possible!、System check incorrect!（屏幕显示错误画面，读数与程序停止，OK不能确认，直到排除）与"
                     "Unstable lighting conditions!（警告消息）；用于测试视觉agent对错误画面的处理", ["dr.status"], "simulation"),
    "fault.clear": ({}, (), {"cleared": _AT_BOOL}, {}, "评测者：错误原因已排除（维修、换灯、减少环境光）；仪器重新自检后就绪", ["dr.status"], "simulation"),
}
_DR_TERMINAL = tuple(_DR_CAPDEFS)
# Instrument errors of the troubleshooting table (manual section 8, p.139-141) injected by the evaluator: code -> (title, first line, second line,
# kept through a power cycle).  temperature_high and system_check_incorrect are cleared by switching off and on (the documented remedy);
# unstable_lighting is a warning notice (readings go on).
_DR_FAULTS = {"hardware_error": ("Error", "Selfcheck stopped. Hardware error.", "Error 1", True),
              "lamp_failure": ("Fault", "Test program stopped!", "Please check lamp", True),
              "ambient_light": ("Error", "Too much ambient light!", "Move instrument into shade or close the lid", True),
              "temperature_high": ("Error", "Temperature too high.", "Measurement not possible!", False),
              "system_check_incorrect": ("Fault", "System check incorrect!", "Switch the instrument off and back on", False)}
_DR_ERROR_TEXT = {"NOT_READY": "The instrument is not ready (startup / self-check).", "SLEEP": "Sleep mode.", "NO_PROGRAM": "No program is running.",
                  "COVER_OPEN": "Please close the cover.", "ZERO_FIRST": "Read is only active after the zero reading.",
                  "LAMP_WARMUP": "Lamp Warmup", "LAMP_OFF": "The lamp is switched off.", "NO_EVALUATION": "No evaluation!",
                  "NO_BARCODE": "No barcode!", "NOT_FOUND": "Program not available.", "LOCKED": "This function is protected. Enter the password.",
                  "WRONG_PASSWORD": "Entry invalid!", "NO_USB": "Please insert the USB memory stick.", "NO_NETWORK": "Remote server cannot be reached.",
                  "BUSY": "A reading is running.", "STORAGE_FULL": "No free storage line.", "ADJUST_LIMIT": "Adjustment outside the allowed limits.",
                  "INCOMPLETE": "The program data is incomplete.", "CUVETTE_PRESENT": "Remove any cells from the cell compartment.",
                  "FAULT": "Test program stopped!", "NO_DATA": "No measurement data present!", "COVER_CLOSED": "Open the cell compartment cover."}
_DR_LIMITS = [
    "DR6000 terminal: startup and self-check, sleep, Main Menu modes with Zero / Read, program options, user programs with calibration, data "
    "log, stored scans and time courses, trends, IDs, security, setup, system checks, AQA, timer, lamps and service reminder follow the DR6000 "
    "user manual as a minimal model, not Hach firmware; readings use the CHEM-071 spectrum model of the cuvette in the compartment (an empty "
    "compartment reads as air, absorbance 0).",
    "Stored programs are the Hach method records of the colorimetric library (published wavelength, range, sensitivity as a straight line "
    "through 0); chemical form alternatives, procedure texts and program-specific adjust limits (+-25 % assumed) are not available.  The "
    "optics are ideal unless the evaluator sets error parameters (sensor.configure_error: wavelength offset, stray light, photometric gain / "
    "offset, noise, drift; every reading path uses them, also the CHEM-071 direct spectroscopy.measure); the optical checks and the "
    "Verification Kit procedure judge them against nominal values and tolerances (kit filter values and default criteria are assumptions).",
    "Not simulated: barcode / LCK vials and RFID, carousel insert, USB memory stick, printer and network drive (sending shows the device "
    "message), LINK2sc, the instrument website, the service menu.  Ratios use the sample IDs of the data log instead of RFID sample locations.  "
    "UV lamp warm-up 60 s, scan steps 0.1-5 nm and display resolutions are assumptions.  CHEM-071 capabilities keep their schemas; the direct "
    "spectroscopy.measure reads the same instrument as Zero / Read: refused during the startup, the self-check, a self-check fault and sleep "
    "mode (NOT_READY / SLEEP) and while the UV lamp warms up or the VIS lamp is switched off by hand (LAMP_WARMUP / LAMP_OFF).",
    "Language: the language picked at the startup is stored, reported in dr.status and shown under Instrument Information; the screen texts "
    "stay English (our manuals contain no translated screen texts).",
]


def _dr_caps():
    caps = {}
    for name, (inputs, optional, outputs, example, desc, effects, kind) in _DR_CAPDEFS.items():
        caps[name] = {"description_zh": desc, "kind": kind, "input_schema": _at_obj(inputs, optional),
                      "output_schema": _at_obj(outputs, list(outputs)), "example_arguments": [example], "state_ids": list(dict.fromkeys(effects)),
                      "component_ids": ["display"], "preconditions": ["Power on, startup and self-check finished, not in sleep mode."],
                      "scene_required": False, "availability": "implemented", "visibility": "agent",
                      "invocation": "HachDR6000.invoke(instance_id=..., capability_id=" + repr(name) + ", arguments=..., request_id=...)",
                      "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "ACCESS_DENIED", "REQUEST_CONFLICT", "DEVICE_ERROR"] +
                      sorted(_DR_ERROR_TEXT),
                      "rejection_side_effects": "none", "scene_failure_side_effects": "unknown"}
    for name in ("verification_kit.place", "sensor.configure_error", "fault.inject", "fault.clear"):   # hands / evaluator only
        caps[name]["visibility"] = "evaluator"
        caps[name]["preconditions"] = ["Trusted evaluator or hands; " + ("cover open, no cell in the compartment." if name.startswith("verification")
                                                                         else "no power or ready precondition.")]
    every = sorted({s for c in caps.values() for s in c["state_ids"]} | {"power.enabled"})
    caps.update(_at_panel_cap_entries(DR6000_PANEL_CAPS, every, "HachDR6000.invoke(instance_id=..., capability_id='{}', arguments=..., request_id=...)",
                                      {"panel.press": ["power_save_key"], "panel.switch": ["power_switch"]}))
    return caps


DR6000_PANEL_PATH = DR6000_ASSET_DIR / "panel_ui.json"
_dr_base_export = export_hach_dr6000_contract_metadata
_DR_BASE_CAPS = tuple(_dr_base_export()["capabilities"])
DR6000_PANEL_SPEC = (_at_pu.load_spec(DR6000_PANEL_PATH, capabilities=set(_DR_TERMINAL) | set(_DR_BASE_CAPS))
                     if DR6000_PANEL_PATH.is_file() and not _at_os.environ.get("G00_PANEL_BUILD") else None)
DR6000_PANEL_CAPS = _at_pu.capability_definitions(DR6000_PANEL_SPEC) if DR6000_PANEL_SPEC else {}


def export_hach_dr6000_contract_metadata():
    """CHEM-071 metadata (with the template hook) plus the touch terminal: device slots, terminal commands and panel.* capabilities."""
    meta = _dr_base_export()
    meta["asset_version"] = DR6000_ASSET_VERSION
    meta["state_slots"].update(_dr_slots())
    meta["capabilities"].update(_dr_caps())
    meta["limits"] = meta["limits"] + _DR_LIMITS
    meta["terminal_capabilities"] = sorted(_DR_TERMINAL) + sorted(DR6000_PANEL_CAPS)
    return meta


# ---------------------------------------------------------------- what the screen shows
def _dr_fmt_dt(dev, iso):
    f = dev["settings"]["datetime.date_format"]
    y, mo, d = iso[:4], iso[5:7], iso[8:10]
    date = {"dd-mm-yyyy": f"{d}-{mo}-{y}", "mm-dd-yyyy": f"{mo}-{d}-{y}", "yyyy-mm-dd": f"{y}-{mo}-{d}", "dd.mm.yyyy": f"{d}.{mo}.{y}"}[f]
    hh, mm = int(iso[11:13]), iso[14:16]
    time = f"{hh:02d}:{mm}" if dev["settings"]["datetime.time_format"] == "24h" else f"{(hh - 1) % 12 + 1:02d}:{mm} {'AM' if hh < 12 else 'PM'}"
    return date, time


def _dr_mmss(s):
    s = max(0, int(_at_math.ceil(s - 1e-9)))
    return f"{s // 60:02d}:{s % 60:02d}"


def _dr_result_texts(dev):
    r, mode = dev["result"], dev["mode"]
    if mode is None:
        return dict(main="", unit="", alt="", warn="", flags="")
    res = mode["resolution"]
    if r is None:
        main = {"conc": "-.---", "abs": "-.--- Abs", "trans": "--.- %T"}.get(mode["result_mode"], "")
        return dict(main=main, unit="", alt="", warn="", flags=_dr_flag_text(mode))
    a = r["abs"][0] if r["abs"] else 0.0
    if r["result_mode"] == "conc" and r["conc"] is not None:
        main, unit = f"{r['conc']:.{res}f}", f"{r['unit']} {r['form']}".strip()
        alt = " ".join(f"{x:.3f} Abs" for x in r["abs"][:2])
    elif r["result_mode"] == "trans":
        main, unit, alt = f"{100.0 * 10 ** -a:.1f}", "%T", f"{a:.3f} Abs"
    elif r.get("formula_value") is not None:
        main, unit = f"{r['formula_value']:.3f}", "Abs"                                 # multi wavelength: the formula result
        alt = "  ".join(f"A{i + 1} {x:.3f}" for i, x in enumerate(r["abs"]))
    else:
        main, unit = f"{a:.3f}", "Abs"
        alt = "" if r["conc"] is None else f"{r['conc']:.{res}f} {r['unit']}"
    if r["n"] > 1:
        alt = (alt + f"  n={r['n']}").strip()
    if r.get("aqa"):
        alt = (alt + f"  AQA {r['aqa']}").strip()
    return dict(main=main, unit=unit, alt=alt, warn="  ".join(r["warnings"]), flags=_dr_flag_text(mode))


def _dr_flag_text(mode):
    parts = []
    if mode.get("dilution"):
        parts.append(f"Dilution x{mode['dilution']:g}")
    if mode.get("reagent_blank") is not None:
        parts.append(f"Blank {mode['reagent_blank']:g}")
    if mode.get("std_adjust"):
        parts.append("Std Adjust")
    if mode.get("multiple"):
        parts.append(f"Multiple {mode['multiple']}")
    if mode.get("reading_mode") == "continuous":
        parts.append("Continuous")
    return "  ".join(parts)


def _dr_scan_view(dev):
    """Scan points in the display unit with cursor navigation (track: every point; peak / valley: the extrema), table rows, integral."""
    sd, s = dev["scan_data"], dev["scan"]
    if sd is None:
        return [], [], None
    ys = [a if s["units"] == "abs" else 100.0 * 10 ** -a for a in sd["absorbance"]]
    xs = [w * 1e9 for w in sd["wavelength_m"]]
    n = len(xs)
    ext = [i for i in range(n) if 0 < i < n - 1 and ((ys[i] > ys[i - 1] and ys[i] >= ys[i + 1]) or (ys[i] < ys[i - 1] and ys[i] <= ys[i + 1]))]
    ext = ext or [max(range(n), key=lambda i: ys[i])]
    pts = []
    for i in range(n):
        if s["cursor"] == "peak":
            nxt = next((e for e in ext if e > i), ext[-1])
            prv = next((e for e in reversed(ext) if e < i), ext[0])
        else:
            nxt, prv = min(i + 1, n - 1), max(i - 1, 0)
        kind = "Peak" if i in ext and (i == 0 or ys[i] >= ys[i - 1]) else "Valley" if i in ext else ""
        pts.append(dict(i=i, x=xs[i], y=ys[i], next=nxt, prev=prv, text=f"{xs[i]:.1f} nm  {ys[i]:.3f} {'Abs' if s['units'] == 'abs' else '%T'}",
                        kind=kind))
    integral = sum((xs[i + 1] - xs[i]) * (sd["absorbance"][i] + sd["absorbance"][i + 1]) / 2 for i in range(n - 1)) if n > 1 else 0.0
    return pts, [[x, y] for x, y in zip(xs, ys)], integral


def _dr_texts(host, dev, st):
    t = host._time()
    date, time = _dr_fmt_dt(dev, _at_now(dev, t))
    mode = dev["mode"]
    tx = dict(date=date, time=time, operator=dev["operator"] or "", sample_id=_dr_sample_label(dev) or "",
              title=mode["name"] if mode else "Main Menu", wavelength="", zero="", timer="", scan_info="", integral="", tc_status="",
              selfcheck="", fault="", notice=dev["notice"] or "", uv="")
    tx.update(_dr_result_texts(dev))
    if mode and mode.get("reading_mode") == "continuous" and dev["zero"] is not None and dev["phase"] == "ready" and \
            (mode["kind"] != "user" or mode["program"]["type"] != "free"):
        if st["lid.closed"]:                                               # continuous readings: shown without Read after the zero (p.61)
            try:
                rel = [v - z for v, z in zip(_dr_absorbance(host, st, mode["wavelengths_m"]), dev["zero"]["abs"])]
                conc, warn = _dr_value(dev, mode, rel)
                live = dict(abs=rel, conc=conc, warnings=warn, result_mode=mode["result_mode"], unit=mode["unit"], form=mode.get("form", ""), n=1,
                            aqa=None)
                tx.update(_dr_result_texts(dict(dev, result=live)))
            except _ATReject:
                tx["warn"] = _DR_ERROR_TEXT["NO_EVALUATION"]
        else:
            tx["warn"] = _DR_ERROR_TEXT["COVER_OPEN"]
    if mode:
        wl = mode["wavelengths_m"]
        if mode["kind"] == "scan":
            s = dev["scan"]
            tx["scan_info"] = f"{s['start_m'] * 1e9:.0f} - {s['stop_m'] * 1e9:.0f} nm  step {s['step_m'] * 1e9:g} nm"
            tx["wavelength"] = tx["scan_info"]
        else:
            tx["wavelength"] = "  ".join(f"{w * 1e9:.1f} nm" for w in wl)
        if mode["kind"] == "user" and mode["program"]["type"] == "free":
            steps, _ = _DR_PROCESS_STEPS[mode["program"]["process"]]
            k = dev["free"]["step"] if dev["free"] else 0
            tx["zero"] = "Zero" if k == 0 or k >= len(steps) else ("Read: " + " ".join(n for n, _ in steps[k][1]))
        else:
            tx["zero"] = "" if dev["zero"] else "Zero required"
    if dev["timer"] is not None:
        tx["timer"] = f"{dev['timer']['name']} {_dr_mmss(dev['timer']['end_s'] - t)}"
    pts, series, integral = _dr_scan_view(dev)
    if integral is not None and dev["scan"]["integral"]:
        tx["integral"] = f"Integral {integral:.3f} Abs*nm"
    tc = dev["tc"]
    if tc is not None:
        n_total = int(round(tc["total_s"] / tc["interval_s"])) + 1
        tx["tc_status"] = (f"Reading... {len(tc['points'])}/{n_total}" if tc["running"] else f"Complete {len(tc['points'])} points")
    if dev["phase"] == "selfcheck":
        tx["selfcheck"] = f"Self-check {_dr_mmss(dev['phase_until'] - t)}"
    tx.update(fault_title="Fault", fault_l1="Test program stopped!")
    if dev["phase"] == "fault":
        if dev["fault"] in _DR_FAULTS:
            tx["fault_title"], tx["fault_l1"], tx["fault"] = _DR_FAULTS[dev["fault"]][:3]
        else:
            tx["fault"] = {"REMOVE_CUVETTE": "Please remove the cuvette", "CLOSE_LID": "Close the lid."}[dev["fault"]]
    lamps = dev["lamps"]
    tx["uv"] = "UV lamp off" if not lamps["uv_on"] else ("Lamp Warmup" if t - lamps["uv_since"] < _DR_UV_WARMUP_S - 1e-9 else "UV lamp ready")
    return tx, pts, series


def _dr_values(host):
    dev, st = host._device, host._state
    values = dict(st)
    lib = _dr_library()
    pts, series, integral = _dr_scan_view(dev)
    sa = dev["std_add"]
    sa_rows, sa_fit = _dr_std_add_eval(sa) if sa else ([], None)
    draft = dev["draft"]
    values.update({
        "dr.status": dict(phase=dev["phase"], fault=dev["fault"], notice=dev["notice"], language=dev["language"],
                          ready=dev["phase"] == "ready" and st["power.enabled"], remaining_s=None if dev["phase_until"] is None else
                          max(0.0, dev["phase_until"] - host._time())),
        "dr.mode": None if dev["mode"] is None else _at_copy.deepcopy(dev["mode"]),
        "dr.zero": _at_copy.deepcopy(dev["zero"]), "dr.result": _at_copy.deepcopy(dev["result"]),
        "dr.scan": None if dev["scan_data"] is None else dict(_at_copy.deepcopy(dev["scan_data"]), integral_abs_nm=integral,
                                                              extrema=[dict(wavelength_m=p["x"] * 1e-9, value=p["y"], kind=p["kind"])
                                                                       for p in pts if p["kind"]]),
        "dr.time_course": _at_copy.deepcopy(dev["tc"]),
        "dr.mode_settings": {k: _at_copy.deepcopy(dev[k]) for k in ("single", "multi", "scan", "time")},
        "dr.settings": dict(dev["settings"]), "dr.data_log": _at_copy.deepcopy(dev["data_log"]),
        "dr.stored_data": dict(scans=_at_copy.deepcopy(dev["stored_scans"]), time_courses=_at_copy.deepcopy(dev["stored_tc"])),
        "dr.programs": dict(stored=[dict(number=r["number"], name=r["name"], wavelength_m=r["wavelength_m"]) for r in lib.values()],
                            user=_at_copy.deepcopy(dev["user_programs"]), favorites=_at_copy.deepcopy(dev["favorites"]),
                            draft=None if draft is None else dict(_at_copy.deepcopy(draft), fit=_dr_draft_fit(draft))),
        "dr.ids": dict(operators=[dict(name=o["name"], level=o["level"], password=o["password"] is not None) for o in dev["operators"]],
                       operator=dev["operator"], sample_ids=_at_copy.deepcopy(dev["sample_ids"]), sample_id=dev["sample_id"]),
        "dr.security": dict(enabled=dev["security"]["enabled"], levels=dict(dev["security"]["levels"])),
        "dr.lamps": dict(dev["lamps"], uv_warm=dev["lamps"]["uv_on"] and host._time() - dev["lamps"]["uv_since"] >= _DR_UV_WARMUP_S - 1e-9),
        "dr.timer": None if dev["timer"] is None else dict(dev["timer"], remaining_s=max(0.0, dev["timer"]["end_s"] - host._time())),
        "dr.checks": _at_copy.deepcopy(dev["checks"]), "dr.aqa": dict(_at_copy.deepcopy(dev["aqa"]), due=_dr_aqa_due(dev, host._time())),
        "dr.aqa_log": _at_copy.deepcopy(dev["aqa_log"]), "dr.trends": _at_copy.deepcopy(dev["trends"]), "dr.service": dict(dev["service"]),
        "dr.datetime": _at_now(dev, host._time()),
        "dr.std_addition": None if sa is None else dict(_at_copy.deepcopy(sa), rows=sa_rows, fit=sa_fit),
        "dr.system": dict(model="DR6000 (simulated)", serial="SIM-DR6000-0001", software="sim-1.2.0", power_epoch=dev["power_epoch"]),
        "dr.check_log": _at_copy.deepcopy(dev["check_log"]),
        "dr.verification": dict(nominal=_at_copy.deepcopy(dev["kit"]["nominal"]), run=None if dev["verification"] is None else
                                {k: _at_copy.deepcopy(v) for k, v in dev["verification"].items() if k != "zero"}),
        "dr.ratios": [dict(r, rows=_dr_ratio_rows(dev, r)) for r in dev["ratios"]],
        "dr.kit": dict(placed=dev["kit"]["placed"], filters={k: v["name"] for k, v in DR_KIT_FILTERS.items()}),
        "dr.optics_errors": dict(dev["errors"]),
    })
    return values


def _dr_aqa_due(dev, t):
    aqa = dev["aqa"]
    now = t + dev["clock_offset_s"]
    due = []
    for std in aqa["standards"]:
        if std.get("reminder_days") and now - aqa["last"].get("standard", -1e18) >= std["reminder_days"] * 86400:
            due.append(std["name"])
    for k in ("addition", "multiple", "dilution"):
        if aqa[k]["on"] and now - aqa["last"].get(k, -1e18) >= aqa[k]["interval_days"] * 86400:
            due.append(k)
    return due


# ---------------------------------------------------------------- runtime and session with the terminal
_DRBaseRuntime = HachDR6000
_DRBaseSession = HachDR6000Session


class HachDR6000(_DRBaseRuntime):
    """CHEM-071 host with the DR6000 touch terminal: the device model (self._device) and the front panel (self.panel)."""

    def __init__(self, instance_id, *, scene_adapter=None, clock=None):
        super().__init__(instance_id, scene_adapter=scene_adapter, clock=clock)
        self._device = _dr_new_device()
        self._last_tick = self._time()
        self._principals["__panel__"] = "agent"
        self._panel_session = HachDR6000Session(self, "__panel__")         # internal principal of commands without a gesturing session
        self._caller = None                                                 # principal of the session making the current panel gesture
        self._panel_seq = 0
        self.panel_capture_dir = None
        self.panel = _at_pu.PanelUI(DR6000_PANEL_SPEC, _ATPanelHost(self)) if DR6000_PANEL_SPEC else None

    def debug_snapshot(self):
        with self._lock:
            out = super().debug_snapshot()
            out["device"] = _at_copy.deepcopy(self._device)
            return out

    def _raw_spectrum(self, state):
        """The CHEM-071 spectrum model of the cuvette (ideal optics)."""
        return _DRBaseRuntime._spectrum(self, state)

    def _spectrum(self, state):
        """Every reading path (CHEM-071 spectroscopy.measure and the terminal) sees the instrument's optics errors: the wavelength offset
        shifts where it really measures, then stray light, photometric gain / offset, drift and noise (_dr_optics).  All 0: unchanged."""
        err = self._device["errors"]
        if not any(err.values()):
            return _DRBaseRuntime._spectrum(self, state)
        dw = err["wavelength_offset_m"]
        sp = _DRBaseRuntime._spectrum(self, dict(state, **{"scan.start_m": state["scan.start_m"] + dw, "scan.stop_m": state["scan.stop_m"] + dw}))
        if sp is None:
            return None
        axis = [w - dw for w in sp["wavelength_m"]]
        a = _dr_optics(self, state, [float(x) for x in sp["absorbance"]], axis)
        tr = [10.0 ** -x for x in a]
        return dict(sp, wavelength_m=axis, absorbance=a, transmittance=tr, signal=a[:] if sp["reported_mode"] == "absorbance" else tr[:])

    def host_update(self):
        """Trusted host: bring the terminal up to the host clock (it is also done on every read, invoke and panel access)."""
        with self._lock:
            self._sync()

    def _sync(self):
        t = self._time()
        if t >= self._last_tick:
            _dr_tick(self, self._device, self._state, t, self._last_tick)
            self._last_tick = t

    def _values(self):
        return _dr_values(self)

    def _after_power(self, was_on):
        dev, t = self._device, self._time()
        on = self._state["power.enabled"]
        if on and not was_on:
            dev["power_epoch"] += 1
            dev.update(phase="startup", phase_until=t + _DR_STARTUP_S, fault=None, lang_pick=False, notice=None, mode=None, zero=None, result=None,
                       scan_data=None, tc=None, free=None, std_add=None, timer=None, drift=None, last_activity_s=t, operator=None)
            dev["lamps"].update(uv_on=False, uv_since=None, vis_on=False)
        elif was_on and not on:
            dev.update(phase="off", phase_until=None, tc=None, drift=None, timer=None, mode=None)
            if dev.get("injected") in _DR_FAULTS and not _DR_FAULTS[dev["injected"]][3]:
                dev["injected"] = None                                         # 'switch the instrument off and back on' (p.141)
            dev["lamps"].update(uv_on=False, uv_since=None, vis_on=False)

    def panel_values(self):
        with self._lock:
            self._sync()
            values = self._values()
            slots = self._metadata["state_slots"]
            out = {k: v for k, v in values.items() if slots.get(k, {}).get("visibility") == "agent"}
            dev, st = self._device, self._state
            t = self._time()
            tx, pts, series = _dr_texts(self, dev, st)
            lib = _dr_library()
            stored_rows = sorted(({"number": n, "text": f"{n} {r['label']}"} for n, r in lib.items()), key=lambda r: r["text"].split(" ", 1)[1])
            users = dev["user_programs"]
            ref = dev["scan"]["reference"]
            ref_scan = dev["stored_scans"].get(str(ref)) if ref is not None else None
            unit_y = (lambda a: a) if dev["scan"]["units"] == "abs" else (lambda a: 100.0 * 10 ** -a)
            tc = dev["tc"]
            tunit = (lambda a: a) if dev["time"]["units"] == "abs" else (lambda a: 100.0 * 10 ** -a)
            draft = dev["draft"]
            fit = _dr_draft_fit(draft) if draft else None
            sa = dev["std_add"]
            sa_rows, sa_fit = _dr_std_add_eval(sa) if sa else ([], None)
            checks = dev["checks"]
            phase = dev["phase"]
            elapsed = 0.0 if phase != "selfcheck" else _DR_SELFCHECK_S - (dev["phase_until"] - t)
            items = ["System", "Lamp", "Filter", "Wavelength", "Voltage"]
            out.update({
                "host.powered": st["power.enabled"], "host.power_epoch": dev["power_epoch"], "host.phase": phase if st["power.enabled"] else "off",
                "host.brightness": 0 if phase in ("sleep", "off") or not st["power.enabled"] else 100, "host.text": tx,
                "host.selfcheck": [dict(text=f"{name}  {'OK' if elapsed >= (i + 1) * _DR_SELFCHECK_S / 5 - 1e-9 else '...'}") for i, name in enumerate(items)],
                "host.stored_rows": stored_rows, "host.stored_numbers": sorted(lib),
                "host.user_rows": [dict(number=int(n), text=f"{n} {p['name']}") for n, p in sorted(users.items())],
                "host.fav_rows": [dict(number=f["number"], kind=f["kind"], text=f"{f['number']} " + (lib[f['number']]["label"] if f["kind"] == "stored"
                                                                                                       and f["number"] in lib else
                                                                                                       users.get(str(f["number"]), {}).get("name", "?")))
                                  for f in dev["favorites"]],
                "host.read_enabled": dev["zero"] is not None or (dev["mode"] is not None and dev["mode"]["kind"] == "user" and dev["free"] is not None),
                "host.tc_running": bool(tc and tc["running"]), "host.scan_points": pts, "host.scan_series": series,
                "host.scan_rows": [dict(text=p["text"], i=p["i"]) for p in pts],
                "host.ref_series": [] if ref_scan is None else [[w * 1e9, unit_y(a)] for w, a in zip(ref_scan["wavelength_m"], ref_scan["absorbance"])],
                "host.scan_axis": dict(lo=dev["scan"]["start_m"] * 1e9, hi=dev["scan"]["stop_m"] * 1e9, span=(dev["scan"]["stop_m"] - dev["scan"]["start_m"]) * 1e9,
                                       ylo=min([0.0] + [p[1] for p in series]), yhi=max([0.0] + [p[1] for p in series])),
                "host.tc_series": [] if tc is None else [[p[0], tunit(p[1])] for p in tc["points"] if p[1] is not None],
                "host.tc_rows": [] if tc is None else [dict(text=f"{p[0]:7.0f} s  " + ("----" if p[1] is None else f"{tunit(p[1]):.3f}") +
                                                            ("  *" if i in tc["marks"] else "")) for i, p in enumerate(tc["points"])],
                "host.tc_span": dev["time"]["total_s"],
                "host.log_rows": [dict(i=i, sample_id=(e["sample_id"] or "").split(" (")[0], operator=e["operator"] or "", date=e["time"][:10],
                                       program=e["program"], text=f"{_dr_fmt_dt(dev, e['time'])[0]} {e['time'][11:16]}  {e['program'][:18]}",
                                       value="" if e["conc"] is None else f"{e['conc']:.3f} {e['unit']}",
                                       detail=[f"Program: {e['program']}", f"Time: {e['time']}", f"Sample ID: {e['sample_id'] or '-'}",
                                               f"Operator ID: {e['operator'] or '-'}", "Result: " + ("-" if e["conc"] is None else
                                                                                                     f"{e['conc']:.4g} {e['unit']} {e['form']}"),
                                               "Abs: " + " ".join(f"{a:.3f}" for a in e["abs"]),
                                               "Wavelength: " + " ".join(f"{w * 1e9:.1f} nm" for w in e["wavelengths_m"]),
                                               "  ".join(e["warnings"])]) for i, e in enumerate(dev["data_log"])],
                "host.parameters": sorted({e["program"] for e in dev["data_log"]}),
                "host.aqa_rows": [dict(text=f"{e['time'][:16].replace('T', ' ')} {e['kind']} {e['name']}", value=f"{e['value']:.3f} {e['result']}")
                                  for e in dev["aqa_log"]],
                "host.scan_store_rows": [dict(slot=i, text=f"{i:02d}  " + (f"{s['time'][:16].replace('T', ' ')}  {s['start_m'] * 1e9:.0f}-"
                                                                          f"{s['wavelength_m'][-1] * 1e9:.0f} nm" if s else "---"),
                                              empty=s is None, series=[] if s is None else [[w * 1e9, a] for w, a in zip(s["wavelength_m"], s["absorbance"])],
                                              lo=None if s is None else s["start_m"] * 1e9, span=None if s is None else
                                              (s["wavelength_m"][-1] - s["start_m"]) * 1e9, hi=None if s is None else s["wavelength_m"][-1] * 1e9)
                                         for i, s in ((i, dev["stored_scans"].get(str(i))) for i in range(1, 51))],
                "host.tc_store_rows": [dict(slot=i, text=f"{i:02d}  " + (f"{s['time'][:16].replace('T', ' ')}  {s['wavelength_m'] * 1e9:.0f} nm  "
                                                                        f"{len(s['points'])} pts" if s else "---"), empty=s is None,
                                            series=[] if s is None else [[p[0], p[1]] for p in s["points"] if p[1] is not None],
                                            span=None if s is None else s["total_s"]) for i, s in ((i, dev["stored_tc"].get(str(i))) for i in range(1, 51))],
                "host.operator_rows": [dict(name=o["name"], text=f"{o['name']}  {o['level']}" + ("  *" if o["password"] else "") +
                                            ("  [logged in]" if o["name"] == dev["operator"] else "")) for o in dev["operators"]],
                "host.sample_rows": [dict(name=s["name"], text=s["name"] + (f" ({s['next']:02d})" if s["add_number"] else "") +
                                          (f"  {s['created'][:10]}" if s["add_date"] else "") + ("  [active]" if s["name"] == dev["sample_id"] else ""))
                                     for s in dev["sample_ids"]],
                "host.security_rows": [dict(function=f, text=f"{f}", level=dev["security"]["levels"][f]) for f in DR_FUNCTIONS],
                "host.lamp_rows": [["VIS lamp hours", f"{dev['lamps']['vis_hours_s'] / 3600:.1f} h"], ["VIS cycles", str(dev["lamps"]["vis_cycles"])],
                                   ["UV lamp hours", f"{dev['lamps']['uv_hours_s'] / 3600:.1f} h"], ["UV cycles", str(dev["lamps"]["uv_cycles"])],
                                   ["UV ignitions", str(dev["lamps"]["uv_ignitions"])]],
                "host.info_rows": [["Model", "DR6000 (simulated)"], ["Serial number", "SIM-DR6000-0001"], ["Software version", "sim-1.2.0"],
                                   ["Language", dev["language"]], ["Stored programs", str(len(lib))]],
                "host.check_text": {k: _dr_check_text(c) for k, c in checks.items()},
                "host.aqa_std_rows": [dict(i=i, text=f"{s['name']}  {s['concentration']:g} {s['unit']} +-{s['ci']:g}") for i, s in
                                      enumerate(dev["aqa"]["standards"])],
                "host.pipette_rows": [dict(i=i, name=p["name"], text=f"{p['name']}  {p['wavelength_m'] * 1e9:.0f} nm  {p['lower']:g}-{p['upper']:g} Abs")
                                      for i, p in enumerate(dev["aqa"]["pipettes"])],
                "host.aqa_due": bool(_dr_aqa_due(dev, t)) or dev["aqa"]["pending"] is not None,
                "host.trend_rows": [dict(i=i, text=f"{tr['parameter'][:22]}  {tr['sample_id'] or 'all'}",
                                         series=[[k, e["conc"]] for k, e in enumerate(e for e in reversed(dev["data_log"]) if e["program"] == tr["parameter"]
                                                                                     and e["conc"] is not None and (not tr["sample_id"] or
                                                                                                                    (e["sample_id"] or "").startswith(tr["sample_id"])) and
                                                                                     (not tr["operator"] or e["operator"] == tr["operator"]))],
                                         lower=tr["lower"], upper=tr["upper"]) for i, tr in enumerate(dev["trends"])],
                "host.draft": None if draft is None else dict(number=draft["number"], type=draft["type"],
                                                              fit=("No calibration" if fit is None else
                                                                   "C = " + " + ".join(f"{c:.4g}{['', ' A', ' A2', ' A3'][i]}" for i, c in enumerate(fit["coef"])
                                                                                       if c != 0 or i == 0) +
                                                                   ("" if fit["r2"] is None else f"   r2 = {fit['r2']:.4f}")),
                                                              zeroed=draft["calib_zero"] is not None,
                                                              n_wl=(1 if draft["type"] == "single" else _DR_FORMULA_N[draft["formula"]] if draft["type"] == "multi"
                                                                    else 2 if draft["process"] == 4 else 1),
                                                              x_span=max([0.5] + [abs(row[1]) for row in draft["table"] if row[1] is not None])),
                "host.calib_rows": [] if draft is None else [dict(i=i, conc=row[0], text=f"{row[0]:g}", abs="---" if row[1] is None else f"{row[1]:.3f}")
                                                             for i, row in enumerate(draft["table"])],
                "host.calib_series": [] if draft is None else [[row[1], row[0]] for row in draft["table"] if row[1] is not None],
                "host.calib_curve": [] if not fit else [[x / 20 * max([0.5] + [row[1] for row in draft["table"] if row[1] is not None]),
                                                         _dr_poly(fit["coef"], x / 20 * max([0.5] + [row[1] for row in draft["table"] if row[1] is not None]))]
                                                        for x in range(21)],
                "host.sa_rows": [dict(i=i, text=f"{r['volume_m3'] * 1e6:.1f} mL", reading="---" if r["reading"] is None else f"{r['reading']:.3f}",
                                      rate="" if r["rate_pct"] is None else f"{r['rate_pct']:.0f} %") for i, r in enumerate(sa_rows)],
                "host.sa_series": [[r["added"], r["reading"]] for r in sa_rows if r["reading"] is not None],
                "host.sa_ideal": [] if not sa_rows or sa_rows[0]["reading"] is None else [[0.0, sa_rows[0]["reading"]],
                                                                                         [sa_rows[-1]["added"], sa_rows[0]["reading"] + sa_rows[-1]["added"]]],
                "host.sa_text": "" if sa_fit is None else f"Estimated {sa_fit['intercept']:.3f}   r2 = {sa_fit['r2']:.4f}",
                "host.uv_warm": dev["lamps"]["uv_on"] and t - (dev["lamps"]["uv_since"] or t) >= _DR_UV_WARMUP_S - 1e-9,
                "host.multi_n": _DR_FORMULA_N[dev["multi"]["formula"]],
                "host.check_rows": [dict(i=i, text=f"{e['time'][:16].replace('T', ' ')}  {e['check']}", result=e["result"], values=e["values"])
                                    for i, e in enumerate(dev["check_log"])],
                "host.ver_rows": [dict(i=i, test=l["test"], text=f"{l['filter']}  " + {"stray": "Stray light", "photometric": "Photometric",
                                                                                      "wavelength": "Wavelength"}[l["test"]],
                                       value=(f"{l['nominal'] * 1e9:.1f} +-{l['tolerance'] * 1e9:.1f} nm" if l["test"] == "wavelength" else
                                              f"{l['wavelength_m'] * 1e9:.0f} nm {l['nominal']:g} +-{l['tolerance']:g}"),
                                       nominal=l["nominal"] * (1e9 if l["test"] == "wavelength" else 1), tolerance=l["tolerance"] * (1e9 if l["test"] == "wavelength" else 1))
                                  for i, l in enumerate(dev["kit"]["nominal"])],
                "host.ver_results": [] if dev["verification"] is None else [dict(text=r["text"], result="Passed" if r["passed"] else "Failed")
                                                                             for r in dev["verification"]["results"]],
                "host.ver_text": ("Insert the universal cell adapter, remove any cells, close the cover and press Start." if dev["verification"] is None else
                                  f"Insert filter {dev['kit']['nominal'][dev['verification']['step']]['filter']} "
                                  f"({DR_KIT_FILTERS[dev['kit']['nominal'][dev['verification']['step']]['filter']]['name']}) and press Next."
                                  if dev["verification"]["state"] == "filter" else
                                  ("Verification passed." if dev["verification"].get("passed") else "Verification failed.")),
                "host.ratio_rows": [dict(i=i, text=f"{r['sample_id']}  " + " : ".join(p.split(" ", 1)[0] for p in r["parameters"])) for i, r in enumerate(dev["ratios"])],
                "host.ratio_table": [dict(i=i, rows=[dict(text=f"{row['date']} {row['sample_id']}", ratio=" : ".join(f"{x:.2f}" for x in row["ratios"]),
                                                      flag="out of limits" if any(row["out"]) else "")
                                                 for row in _dr_ratio_rows(dev, r)]) for i, r in enumerate(dev["ratios"])],
                "host.log_sample_rows": [dict(text=x) for x in sorted({(e.get("sample_id") or "").split(" (")[0] for e in dev["data_log"]} - {""})],
                "host.parameter_rows": [dict(text=p) for p in sorted({e["program"] for e in dev["data_log"]})],
                "host.trend_n": max([1] + [sum(1 for e in dev["data_log"] if e["program"] == tr["parameter"]) - 1 for tr in dev["trends"]]),
                "host.sa_span": max([1e-9] + [r["added"] for r in sa_rows]),
            })
            return out

    def panel_command(self, capability_id, arguments):
        """A command of a panel gesture runs as the session that made the gesture (self._caller, set by HachDR6000Session._panel)."""
        self._panel_seq += 1
        rid = f"__panel_{self._panel_seq}"
        who = getattr(self, "_caller", None) or "__panel__"
        session = self._panel_session if who == "__panel__" else HachDR6000Session(self, who)
        r = session._invoke_once(capability_id=capability_id, arguments=arguments, request_id=rid)
        self._requests.pop((who, rid), None)
        return r


def _dr_check_text(c):
    s = c.get("state")
    pf = ("  Passed" if c["passed"] else "  Failed") if "passed" in c else ""
    if s == "zeroed":
        return "Zeroed" + (f" ({len(c.get('readings', []))}/5)" if c.get("readings") else "")
    if s == "read":
        return f"Reading {len(c['readings'])}/5: {c['readings'][-1]:.4f} Abs - Zero again"
    if s == "running":
        return "Drift check running" + "".join(f"  {q['minute']} min: {q['slope_per_min'] or 0:.2e}/min" for q in c.get("slopes", []))
    if "measured_m" in c:
        return f"{c['nominal_m'] * 1e9:.1f} nm: {c['measured_m'] * 1e9:.1f} nm{pf}"
    if "slope_per_h" in c:
        return f"Drift {c['result'] or 0:.4f} Abs  slope {c['slope_per_h'] or 0:.2e} Abs/h{pf}"
    if "sd" in c:
        return f"Mean {c['result']:.4f} Abs  SD {c['sd']:.4f}{pf}"
    return f"Result {c.get('result', 0):.4f} Abs{pf}"


class HachDR6000Session(_DRBaseSession):
    """CHEM-071 session plus the terminal commands and panel.*."""

    def _response(self, status="succeeded", *, request_id=None, result=None, observations=None, code=None, side_effects="none"):
        if code in _DR_ERROR_TEXT:
            r = super()._response(status, request_id=request_id, result=result, observations=observations, code="PRECONDITION_FAILED",
                                  side_effects=side_effects)
            r["error"].update(code=code, message=_DR_ERROR_TEXT[code])
            return r
        return super()._response(status, request_id=request_id, result=result, observations=observations, code=code, side_effects=side_effects)

    def _vis(self):
        return self._host._principals.get(self._principal_id, "agent")

    def describe(self, *, asset_type_id=None, instance_id=None):
        out = super().describe(asset_type_id=asset_type_id, instance_id=instance_id)
        if "capabilities" in out and self._vis() not in ("evaluator", "debug"):
            out["capabilities"] = {k: c for k, c in out["capabilities"].items() if c.get("visibility", "agent") == "agent"}
        return out

    def read(self, *, instance_id=None, state_ids=None):
        h = self._host
        with h._lock:
            if not self._target(instance_id):
                return self._response("rejected", code="UNKNOWN_INSTANCE")
            h._sync()
            values = h._values()
            if state_ids is None:
                state_ids = [sid for sid in h._metadata["state_slots"] if self._visible(sid) and sid in values]
            if not isinstance(state_ids, list) or any(not isinstance(sid, str) for sid in state_ids):
                return self._response("rejected", code="INVALID_ARGUMENT")
            if any(not self._visible(sid) for sid in state_ids):
                return self._response("rejected", code="ACCESS_DENIED")
            now = h._time()
            observations = {}
            for sid in state_ids:
                slot, value = h._metadata["state_slots"][sid], values[sid]
                observations[sid] = {"value": _at_copy.deepcopy(value), "unit": slot["unit"],
                                     "quality": "unavailable" if value is None else ("estimated" if slot["origin"] == "derived" else "valid"),
                                     "sample_time_s": now, "sample_window_s": None}
                if value is None:
                    observations[sid]["reason"] = "No value has been established for this state."
            return self._response(observations=observations)

    def _invoke_once(self, *, capability_id, arguments, request_id, instance_id=None):
        h = self._host
        with h._lock:
            if self._target(instance_id):
                h._sync()
            if not isinstance(capability_id, str) or (capability_id not in _DR_TERMINAL and capability_id not in DR6000_PANEL_CAPS):
                if capability_id == "sample.load" and h._device["kit"]["placed"] is not None and self._target(instance_id):
                    return self._response("rejected", request_id=request_id, code="PRECONDITION_FAILED")     # a kit filter occupies the adapter
                if capability_id == "spectroscopy.measure" and arguments == {} and self._target(instance_id) and h._state["power.enabled"]:
                    # the direct reading is a reading of the same instrument: startup / self-check done, not asleep, lamps on and warm (as
                    # _dr_measure; switching the UV lamp on is committed even though the reading is refused)
                    dev = _at_copy.deepcopy(h._device)
                    try:
                        _dr_ready(dev, h._state)
                        if h._state["lid.closed"] and h._state["sample.loaded"]:
                            _dr_lamps_for(h, dev, [h._state["scan.start_m"], h._state["scan.stop_m"]], h._time())
                    except _ATReject as exc:
                        return self._response("rejected", request_id=request_id, code=exc.code)
                    response = super()._invoke_once(capability_id=capability_id, arguments=arguments, request_id=request_id, instance_id=instance_id)
                    if response["status"] == "succeeded":
                        dev["last_activity_s"] = h._time()
                        h._device = dev
                    return response
                was_on = h._state["power.enabled"]
                response = super()._invoke_once(capability_id=capability_id, arguments=arguments, request_id=request_id, instance_id=instance_id)
                if response["status"] == "succeeded" and capability_id == "power.set":
                    h._after_power(was_on)
                return response
            if not self._target(instance_id):
                return self._response("rejected", request_id=request_id, code="UNKNOWN_INSTANCE")
            if not isinstance(arguments, dict) or not isinstance(request_id, str) or not request_id:
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            try:
                signature = _dr6000_json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            except (TypeError, ValueError, OverflowError):
                return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            key = (self._principal_id, request_id)
            if key in h._requests:
                old, response = h._requests[key]
                return _at_copy.deepcopy(response) if old == signature else self._response("rejected", request_id=request_id, code="REQUEST_CONFLICT")
            cap = h._metadata["capabilities"][capability_id]
            if _at_validate(cap["input_schema"], arguments):
                response = self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
            elif cap.get("visibility", "agent") != "agent" and self._vis() not in ("evaluator", "debug"):
                response = self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
            elif capability_id.startswith("panel."):
                response = self._panel(capability_id, arguments, request_id)
            else:
                response = self._terminal(capability_id, arguments, request_id)
            h._requests[key] = (signature, _at_copy.deepcopy(response))
            return response

    def _dr_foreign(self, cap, args):
        """Another caller's time course, drift check or timer would be stopped or changed by cap (lead rule 2026-10-19: ACCESS_DENIED on the
        touch screen and the direct path); the evaluator / debug identities and the firmware's own timers are unrestricted.  The power switch
        (power.set models it) is a hard switch: never refused."""
        h, me = self._host, self._principal_id
        if me == "__panel__" or self._vis() in ("evaluator", "debug"):
            return False
        dev = h._device
        foreign = lambda x: x is not None and x.get("owner") not in (None, me)        # noqa: E731
        tc = dev["tc"] if dev["tc"] is not None and dev["tc"]["running"] else None
        if cap in ("timecourse.control", "program.exit", "program.start", "program.options", "reading.zero", "reading.read") and foreign(tc):
            return True
        if cap == "system.check" and args.get("check") == "drift" and foreign(dev["drift"]):
            return True
        if cap in ("power.sleep", "lamp.set") and (foreign(tc) or foreign(dev["drift"])):
            return True
        return cap == "timer.control" and foreign(dev["timer"])

    def _terminal(self, capability_id, arguments, request_id):
        h = self._host
        t = h._time()
        if self._dr_foreign(capability_id, arguments):
            return self._response("rejected", request_id=request_id, code="ACCESS_DENIED")
        dev, state = _at_copy.deepcopy(h._device), _at_copy.deepcopy(h._state)
        was = (dev["tc"] is not None and dev["tc"]["running"], dev["drift"], dev["timer"])
        try:
            result = _DR_COMMANDS[capability_id](h, dev, state, arguments, t)
        except _ATReject as exc:
            return self._response("rejected", request_id=request_id, code=exc.code)
        if dev["tc"] is not None and dev["tc"]["running"] and not was[0]:
            dev["tc"]["owner"] = self._principal_id                               # a time course belongs to its caller
        if dev["drift"] is not None and dev["drift"] is not was[1] and was[1] is None:
            dev["drift"]["owner"] = self._principal_id
        if capability_id == "timer.control" and dev["timer"] is not None:
            dev["timer"]["owner"] = self._principal_id
        if h._scene_adapter is not None and state != h._state:
            try:
                h._scene_adapter(h.instance_id, h._public_scene_state(state))
            except Exception:
                return self._response("failed", request_id=request_id, code="DEVICE_ERROR", side_effects="unknown")
        dev["last_activity_s"] = t
        h._state, h._device = state, dev
        h._revision += 1
        return self._response(request_id=request_id, result=result)

    def _panel(self, capability_id, args, request_id):
        h = self._host
        if h.panel is None:
            return self._response("unsupported", request_id=request_id, code="UNSUPPORTED")
        h._caller = self._principal_id                                          # the gesture's commands run as this session's caller
        try:
            if capability_id == "panel.capture":
                return self._response(request_id=request_id, result=_at_capture(h.panel, h.instance_id, args["display_id"], h._time(), h.panel_capture_dir))
            if capability_id == "panel.frame":
                return self._response(request_id=request_id, result={"frame": h.panel.frame()})
            if capability_id == "panel.press":
                accepted = h.panel.press(args["input_id"], float(args.get("duration_s", 0.1)))
            elif capability_id == "panel.switch":
                accepted = h.panel.switch(args["input_id"], args["position"])
            else:
                if ("to_x" in args) != ("to_y" in args):
                    return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
                if h._device["phase"] == "startup":
                    h._device["lang_pick"] = True                              # touched during the startup: the language list follows (p.21)
                to = (args["to_x"], args["to_y"]) if "to_x" in args else None
                accepted = h.panel.touch(args["display_id"], args["x"], args["y"], float(args.get("duration_s", 0.1)), to=to)
        except _at_pu.PanelError:
            return self._response("rejected", request_id=request_id, code="INVALID_ARGUMENT")
        finally:
            h._caller = None
        h._device["last_activity_s"] = h._time()
        return self._response(request_id=request_id, result={"accepted": bool(accepted)})
# <<< CHEM-071 DR6000 touch terminal
