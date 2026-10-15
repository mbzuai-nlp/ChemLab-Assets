"""Template-backed GC path for the Agilent 8890B runtime (CHEM-096).

Library (shared, unchanged): ``assets/reactions/spectral_library/gc_retention.json`` — NIST retention indices
(measured_literature) converted to model-column fingerprints k_ref / delta_h_J_mol / response_C_kg (derived; the
alkane calibration of the model column is an assumption, see ``gc_model.calibration``).

A trusted host registers a vial whose contents are spectral_library compound ids -> mass in kg (e.g. from a
reaction ledger). ``chromatography.measure`` then runs the real :mod:`scripts.gc_runtime` mass transport
(liner split, Gaussian band broadening, FID charge from ECN response) isothermally at the instrument's current oven,
inlet and FID setpoints, instead of returning the legacy Gaussian fixture. The agent sees an unlabeled FID trace and
an automatically detected peak table; species identities stay in the trusted host's registration.
"""
from __future__ import annotations

import copy
import json
import math
from functools import lru_cache
from typing import Any

from scripts.basic40_templates import _analytical_common as common
from scripts.gc_runtime import GC, Method, Species

LIB_REL = common.SPECTRAL + "/gc_retention.json"
COLUMNS = ("nonpolar", "polar")
FID_IGNITION_MINIMUM_K = Method().fid_ignition_minimum_K
SAMPLE_INTERVAL_S = 1.0
MIN_RUN_S = 120.0
MAX_RUN_S = 3600.0
MAX_SPECIES = 16
MAX_VIAL_MASS_KG = 2e-3
WARMUP_STEP_S = 30.0
WARMUP_LIMIT_S = 7200.0
PEAK_RELATIVE_THRESHOLD = 1e-3

LIMITATIONS = [
    "Retention indices are NIST measured_literature values; k_ref/delta_h/response are derived fingerprints whose alkane calibration of the 30 m x 0.25 mm x 0.25 um model column is an assumption (gc_retention.json gc_model.calibration), not an Agilent column certificate.",
    "Isothermal run at the current oven setpoint; oven programs, pressure/flow control, column bleed, detector noise, baseline drift and FID saturation are not modelled.",
    "FID response = 15 C/kg(carbon) x ECN (Scanlon & Willis 1985) is an order-of-magnitude detector assumption; peak areas are not a calibrated quantitation.",
    "Species without a fingerprint for the selected column (glycerol, hydrogen peroxide, ammonia on the non-polar model; nine compounds on the polar model) cannot be registered.",
    "Acetic acid and ethyl acetate nearly co-elute on the non-polar model column (NIST RI 610 vs 612); co-eluting species form one detected peak.",
    "The run is computed instantly; host time is not advanced and the oven zone warm-up of the public instrument is not coupled to the sub-simulation.",
]
SYNTHETIC_PARTS = ["model column calibration (k_hexane, log10 k per methylene)", "FID carbon sensitivity",
                   "gc_runtime default split/flow/liner/band-broadening parameters"]


@lru_cache(maxsize=None)
def _table() -> dict[str, Any]:
    return common.load_json(LIB_REL)["compounds"]


def fingerprint(substance_id: str, column: str = "nonpolar") -> dict[str, Any] | None:
    sid = common.resolve_compound_id(substance_id)
    if sid is None or column not in COLUMNS:
        return None
    return copy.deepcopy((_table().get(sid) or {}).get("model_fingerprint", {}).get(column))


def template_read(substance_id: Any) -> dict[str, Any] | None:
    """Public reference record (no information about any loaded sample)."""
    sid = common.resolve_compound_id(substance_id)
    entry = _table().get(sid) if sid else None
    if not entry or not entry.get("retention_index"):
        return None
    compound = common.compounds()[sid]
    gc_model = common.load_json(LIB_REL)["gc_model"]
    indices = {cls: {"median": rec["median"], "n": rec["n"], "evidence": rec["evidence"], "source": rec["source"]}
               for cls, rec in entry["retention_index"].items()}
    fingerprints = {}
    for column, fp in (entry.get("model_fingerprint") or {}).items():
        fingerprints[column] = {key: fp[key] for key in ("k_ref", "delta_h_J_mol", "response_C_kg", "reference_temperature_K",
                                                          "retention_index_used", "retention_index_class",
                                                          "predicted_isothermal_retention_s_at_80C", "evidence")}
    return {"substance_id": sid, "name": compound["name_en"], "name_zh": compound["name_zh"], "cas": compound["cas"],
            "retention_index": indices, "model_fingerprint": fingerprints,
            "model_column": {"length_m": gc_model["column"]["length_m"], "inner_diameter_m": gc_model["column"]["inner_diameter_m"],
                             "film_thickness_m": gc_model["column"]["film_thickness_m"], "evidence": gc_model["evidence"]},
            **common.provenance([LIB_REL], "retention_index: measured_literature (NIST WebBook); model_fingerprint: derived with a calibration_assumption",
                                SYNTHETIC_PARTS, LIMITATIONS)}


def validate_sample(composition_kg: Any, *, column: str = "nonpolar", injection_fraction: float = 0.01) -> dict[str, Any]:
    """Validate a trusted-host vial definition; raises ValueError without side effects."""
    if column not in COLUMNS:
        raise ValueError("column must be 'nonpolar' or 'polar'")
    if not common.finite(injection_fraction) or not 0 < injection_fraction <= 1:
        raise ValueError("injection_fraction must be in (0, 1]")
    if not isinstance(composition_kg, dict) or not 1 <= len(composition_kg) <= MAX_SPECIES:
        raise ValueError(f"composition_kg must map 1..{MAX_SPECIES} compound ids to kg")
    out, fingerprints = {}, {}
    for identifier, mass in composition_kg.items():
        sid = common.resolve_compound_id(identifier)
        if sid is None:
            raise ValueError(f"{identifier!r} is not a spectral_library compound id")
        if sid in out:
            raise ValueError(f"duplicate compound {sid!r}")
        if not common.finite(mass) or not 0 < mass <= MAX_VIAL_MASS_KG:
            raise ValueError(f"mass for {identifier!r} must be finite in (0, {MAX_VIAL_MASS_KG}] kg")
        fp = fingerprint(sid, column)
        if fp is None:
            raise ValueError(f"{sid!r} has no {column} fingerprint in {LIB_REL}")
        out[sid] = float(mass)
        fingerprints[sid] = {k: fp[k] for k in ("k_ref", "delta_h_J_mol", "response_C_kg", "reference_temperature_K", "retention_index_used")}
    return {"library": LIB_REL, "column": column, "injection_fraction": float(injection_fraction),
            "composition_kg": out, "fingerprints": fingerprints}


def _species(template: dict[str, Any]) -> list[Species]:
    return [Species(sid, fp["k_ref"], fp["delta_h_J_mol"], fp["response_C_kg"], fp["reference_temperature_K"])
            for sid, fp in template["fingerprints"].items()]


def detect_peaks(time_s: list[float], current_A: list[float]) -> list[dict[str, Any]]:
    """Unlabeled FID peak table (see _analytical_common.detect_peaks)."""
    return [{"retention_time_s": p["apex"], "area_C": p["area"], "height_A": p["height"], "width_half_height_s": p["width"]}
            for p in common.detect_peaks(time_s, current_A, PEAK_RELATIVE_THRESHOLD)]


def _run_length_s(template: dict[str, Any], oven_K: float, method: Method) -> float:
    core = method.column_inner_diameter_m - 2 * method.column_film_thickness_m
    t0 = method.column_length_m / (method.column_flow_m3_s / (math.pi * core ** 2 / 4))
    k_max = max(sp.retention_factor(oven_K) for sp in _species(template))
    return float(min(MAX_RUN_S, max(MIN_RUN_S, math.ceil(1.4 * t0 * (1 + k_max) + 60.0))))


def simulate(template: dict[str, Any], *, oven_K: float, inlet_K: float, fid_K: float,
             split_ratio: float | None = None) -> dict[str, Any] | None:
    """Run gc_runtime with library species; returns None when the FID cannot be lit at fid_K."""
    key = json.dumps([template["composition_kg"], template["column"], template["injection_fraction"],
                      oven_K, inlet_K, fid_K, split_ratio], sort_keys=True)
    result = _simulate_cached(key)
    return copy.deepcopy(result)


@lru_cache(maxsize=64)
def _simulate_cached(key: str) -> dict[str, Any] | None:
    composition, column, fraction, oven_K, inlet_K, fid_K, split_ratio = json.loads(key)
    if fid_K < FID_IGNITION_MINIMUM_K:
        return None
    template = validate_sample(composition, column=column, injection_fraction=fraction)
    options = {"oven_temperature_K": oven_K, "inlet_temperature_K": inlet_K, "fid_temperature_K": fid_K}
    if split_ratio is not None:
        options["split_ratio"] = split_ratio
    method = Method(**options)
    gc = GC(method=method, species=_species(template), vial_masses_kg=template["composition_kg"])
    gc.perform("plug_power", connected=True)
    gc.perform("power", enabled=True)
    for gas in gc.gases:
        gc.perform("connect_gas", gas=gas, connected=True)
    warmed = 0.0
    while warmed < WARMUP_LIMIT_S:
        gc.step(WARMUP_STEP_S)
        warmed += WARMUP_STEP_S
        if [r for r in gc.not_ready_reasons() if r != "FID_FLAME_OFF"] == [] and gc.zones["fid"].temperature_K >= FID_IGNITION_MINIMUM_K:
            break
    gc.perform("ignite")
    gc.step(method.ready_dwell_s + 1.0)
    gc.perform("start")
    gc.perform("aspirate", fraction=template["injection_fraction"])
    gc.perform("insert_syringe")
    gc.perform("depress_plunger")
    gc.perform("retract_syringe")
    run_s = _run_length_s(template, oven_K, method)
    times, current = [0.0], [0.0]
    eluted_prev = {sid: 0.0 for sid in template["composition_kg"]}
    profiles = {sid: [] for sid in template["composition_kg"]}
    steps = int(round(run_s / SAMPLE_INTERVAL_S))
    for _ in range(steps):
        state = gc.step(SAMPLE_INTERVAL_S)
        times.append(state["run_time_s"])
        current.append(state["fid_current_A"])
        for sid in profiles:
            eluted = state["mass_balance_kg"][sid]["eluted_kg"]
            profiles[sid].append(eluted - eluted_prev[sid])
            eluted_prev[sid] = eluted
    balance = gc.mass_balance()
    species_detail = {}
    for sid, profile in profiles.items():
        loaded = balance[sid]["column_loaded_kg"]
        apex_index = max(range(len(profile)), key=profile.__getitem__)
        eluted = balance[sid]["eluted_kg"]
        species_detail[sid] = {"apex_time_s": times[apex_index + 1] if loaded > 0 and eluted >= 0.5 * loaded else None,
                               "injected_kg": balance[sid]["injected_kg"], "column_loaded_kg": loaded, "eluted_kg": eluted,
                               "split_vent_kg": balance[sid]["split_vent_kg"], "residual_kg": balance[sid]["residual_kg"],
                               "retention_index_used": template["fingerprints"][sid]["retention_index_used"]}
    peaks = detect_peaks(times, current)
    public = {"time_s": times, "fid_current_A": current,
              "retention_time_s": [p["retention_time_s"] for p in peaks], "peak_area_C": [p["area_C"] for p in peaks],
              "peaks": peaks, "calibrated_concentration_mol_m3": None, "measurement_duration_s": None,
              "run_duration_s": run_s, "oven_temperature_K": oven_K, "inlet_temperature_K": inlet_K,
              "fid_temperature_K": fid_K, "column_model": column, "split_ratio": method.split_ratio,
              "quality": "estimated", "signal_unit": "A", "axis_unit": "s", "sampling": "template_transport_simulation",
              **common.provenance([LIB_REL], "retention: NIST measured_literature retention indices -> derived model fingerprints (column calibration assumption); transport: gc_runtime simulation",
                                  SYNTHETIC_PARTS, LIMITATIONS)}
    return {"public": public, "species": species_detail}


def chromatogram(template: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    """Agent-visible chromatogram for the public adapter state (setpoints from thermal.*.setpoint_k)."""
    run = simulate(template, oven_K=float(state["thermal.oven.setpoint_k"]), inlet_K=float(state["thermal.inlet.setpoint_k"]),
                   fid_K=float(state["thermal.fid.setpoint_k"]))
    return None if run is None else run["public"]


def chromatogram_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    series = lambda unit: {"type": "array", "items": n(unit, 0), "minItems": 2, "maxItems": int(MAX_RUN_S / SAMPLE_INTERVAL_S) + 1}
    peak = obj({"retention_time_s": n("s", 0), "area_C": n("C", 0), "height_A": n("A", 0), "width_half_height_s": nullable(n("s", 0))})
    return obj({"time_s": series("s"), "fid_current_A": series("A"),
                "retention_time_s": {"type": "array", "items": n("s", 0)}, "peak_area_C": {"type": "array", "items": n("C", 0)},
                "peaks": {"type": "array", "items": peak}, "calibrated_concentration_mol_m3": {"type": "null"},
                "measurement_duration_s": {"type": "null"}, "run_duration_s": n("s", 0), "oven_temperature_K": n("K", 0),
                "inlet_temperature_K": n("K", 0), "fid_temperature_K": n("K", 0),
                "column_model": {"type": "string", "enum": list(COLUMNS)}, "split_ratio": n("1", 0),
                "quality": {"type": "string", "const": "estimated"}, "signal_unit": {"type": "string", "const": "A"},
                "axis_unit": {"type": "string", "const": "s"}, "sampling": {"type": "string", "const": "template_transport_simulation"},
                **common.provenance_schema()})


def template_read_schema() -> dict[str, Any]:
    obj, n = common.obj, common.number
    return obj({"substance_id": common.TEXT, "name": common.TEXT, "name_zh": common.TEXT, "cas": common.nullable(common.TEXT),
                "retention_index": {"type": "object", "additionalProperties": obj({"median": n("1"), "n": {"type": "integer", "minimum": 0, "x-unit": "1"},
                                                                                     "evidence": common.TEXT, "source": common.TEXT})},
                "model_fingerprint": {"type": "object", "additionalProperties": obj({
                    "k_ref": n("1", 0), "delta_h_J_mol": n("J/mol"), "response_C_kg": n("C/kg", 0), "reference_temperature_K": n("K", 0),
                    "retention_index_used": n("1"), "retention_index_class": common.TEXT,
                    "predicted_isothermal_retention_s_at_80C": n("s", 0), "evidence": common.TEXT})},
                "model_column": obj({"length_m": n("m", 0), "inner_diameter_m": n("m", 0), "film_thickness_m": n("m", 0), "evidence": common.TEXT}),
                **common.provenance_schema()})


def extend_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Add the template path to export_agilent_gc8890b_contract_metadata() without changing legacy entries."""
    legacy = metadata["capabilities"]["chromatography.measure"]["output_schema"]["properties"]["chromatogram"]
    template = chromatogram_schema()
    measure = metadata["capabilities"]["chromatography.measure"]
    measure["output_schema"]["properties"]["chromatogram"] = {"anyOf": [legacy, template]}
    measure["preconditions"] = measure["preconditions"] + [
        "Template-backed vial only: FID setpoint >= 423.15 K (gc_runtime ignition minimum); the run uses the current oven/inlet/FID setpoints isothermally."]
    slot = metadata["state_slots"]["measurement.chromatogram"]
    slot["value_schema"] = {"anyOf": [legacy, template, {"type": "null"}]}
    metadata["capabilities"]["template.read"] = common.template_read_capability(
        "AgilentGC8890B", "ethyl_acetate", template_read_schema(), ["control_panel"],
        "spectral_library compound id (NIST retention index reference); does not refer to the loaded vial")
    metadata["capabilities"]["template.read"]["errors"] = ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "REQUEST_CONFLICT"]
    metadata["parameters"]["template_library"] = {"value": LIB_REL, "unit": "1", "evidence": "measured_literature+derived",
        "conditions": "NIST retention indices -> model fingerprints; column calibration is an assumption"}
    metadata["parameters"]["template_sample_interval_s"] = {"value": SAMPLE_INTERVAL_S, "unit": "s", "evidence": "assumption"}
    metadata["parameters"]["template_run_duration_s"] = {"value": [MIN_RUN_S, MAX_RUN_S], "unit": "s", "evidence": "assumption",
        "conditions": "1.4 x t0 x (1 + k_max) + 60 s, clamped; same rule as scripts/spectral_library/gc_demo_real.py"}
    metadata["limits"] = metadata["limits"] + [
        "Template-backed vials (host register_template_sample) run gc_runtime transport with spectral_library fingerprints; the legacy fixture path is unchanged and remains the default."]
    return metadata
