"""Template-backed LC-UV path for the Agilent 1260 Infinity III runtime (CHEM-097).

Libraries (shared, unchanged):
  * ``spectral_library/hplc_uv.json`` — reversed-phase C18 retention factor estimated from PubChem XLogP
    (log10 k = -0.35 + 0.55 XLogP, ~60:40 methanol/water, t0 = 1.5 min). evidence = derived_assumption: this is an
    elution-ORDER estimate, not a retention time measured on any specific column.
  * ``spectral_library/uv_vis.json`` — NIST UV/Vis curves (log10 epsilon vs wavelength, 31 compounds) used for the
    DAD/VWD absorbance at the method wavelength (Beer-Lambert in an assumed 10 mm flow cell).

A trusted host registers a vial as spectral_library ids -> concentration (mol/m^3). The run then produces an
absorbance trace: t_R = t0 (1 + k) with t0 = V0 / F, Gaussian peaks with sigma = t_R / sqrt(N), and
A(t) = epsilon(lambda) * l * c(t), c(t) = n_injected * gaussian(t) / F. Identities are never returned to the agent.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from scripts.basic40_templates import _analytical_common as common

LIB_REL = common.SPECTRAL + "/hplc_uv.json"
UV_REL = common.UV_REL
VOID_VOLUME_M3 = 1.5e-6             # library t0 = 1.5 min read as 1.5 mL void volume at an assumed 1.0 mL/min
PLATE_NUMBER = 10000.0              # assumption: typical 150 mm x 4.6 mm, 5 um C18 column order of magnitude
FLOW_CELL_PATH_M = 0.010            # assumption: 10 mm standard DAD flow cell
MAX_SPECIES = 16
MAX_CONCENTRATION_MOL_M3 = 1000.0   # 1 mol/L upper bound for a vial solute
PEAK_RELATIVE_THRESHOLD = 1e-3

LIMITATIONS = [
    "Retention is DERIVED from PubChem XLogP (hplc_uv.json, evidence=derived_assumption: log10 k = -0.35 + 0.55 XLogP for ~60:40 methanol/water on C18); it is an elution-order estimate, not a retention time measured on any specific column, and ignores the programmed gradient, column temperature and mobile-phase identity.",
    "t0 = 1.5e-6 m^3 / flow: the library's t0 = 1.5 min is interpreted as a 1.5 mL void volume at an assumed 1.0 mL/min.",
    "Peak width uses an assumed plate number N = 10000 (sigma = t_R / sqrt(N)); no extra-column broadening, tailing or overload.",
    "Absorbance uses NIST UV/Vis curves (digitized literature spectra, solvent/state not recorded in the JCAMP header) in an assumed 10 mm flow cell; solvent shifts, pH, detector bandwidth and noise are not modelled.",
    "Species without a NIST UV curve covering the method wavelength contribute no absorbance in this model: their response is UNKNOWN, not a measured zero.",
    "No calibration, purity or quantitation; peak areas are model outputs in absorbance x seconds.",
]
SYNTHETIC_PARTS = ["void volume 1.5 mL", "plate number 10000", "flow-cell path 10 mm", "isocratic treatment of the gradient"]


@lru_cache(maxsize=None)
def _table() -> dict[str, Any]:
    """HPLC templates; compounds without an XLogP (no retention estimate, e.g. boric acid since the 2026-10-14
    expansion of the shared table) are not offered."""
    return {sid: entry for sid, entry in common.load_json(LIB_REL)["compounds"].items()
            if entry["reversed_phase_C18"]["k_estimate"] is not None}


def template_read(substance_id: Any) -> dict[str, Any] | None:
    sid = common.resolve_compound_id(substance_id)
    entry = _table().get(sid) if sid else None
    if not entry:
        return None
    compound = common.compounds()[sid]
    rp = entry["reversed_phase_C18"]
    curve = common.uv_curve(sid)
    uv = None
    if curve is not None:
        peak = max(range(len(curve["epsilon_m2_mol"])), key=curve["epsilon_m2_mol"].__getitem__)
        uv = {"wavelength_range_m": [curve["wavelength_m"][0], curve["wavelength_m"][-1]],
              "max_epsilon_wavelength_m": curve["wavelength_m"][peak], "max_epsilon_m2_mol": curve["epsilon_m2_mol"][peak],
              "points": len(curve["wavelength_m"]), "evidence": curve["evidence"], "source": curve["source"]}
    return {"substance_id": sid, "name": compound["name_en"], "name_zh": compound["name_zh"], "cas": compound["cas"],
            "xlogp": entry["xlogp"], "k_estimate": rp["k_estimate"], "predicted_retention_s": rp["predicted_retention_min"] * 60.0,
            "retention_assumption": rp["assumption"], "retention_evidence": rp["evidence"],
            "uv_detection_candidates_m": [nm * 1e-9 for nm in entry.get("uv_detection_candidates_nm") or []],
            "default_detection_m": None if entry.get("default_detection_nm") is None else entry["default_detection_nm"] * 1e-9,
            "uv_curve": uv,
            **common.provenance([LIB_REL, UV_REL], "retention: derived_assumption from XLogP; UV curve: NIST measured",
                                SYNTHETIC_PARTS, LIMITATIONS)}


def validate_sample(composition_mol_m3: Any) -> dict[str, Any]:
    if not isinstance(composition_mol_m3, dict) or not 1 <= len(composition_mol_m3) <= MAX_SPECIES:
        raise ValueError(f"composition_mol_m3 must map 1..{MAX_SPECIES} spectral_library ids to mol/m^3")
    out = {}
    for identifier, concentration in composition_mol_m3.items():
        sid = common.resolve_compound_id(identifier)
        if sid is None or sid not in _table():
            raise ValueError(f"{identifier!r} is not a spectral_library compound with an HPLC template")
        if sid in out:
            raise ValueError(f"duplicate compound {sid!r}")
        if not common.finite(concentration) or not 0 < concentration <= MAX_CONCENTRATION_MOL_M3:
            raise ValueError(f"concentration for {identifier!r} must be finite in (0, {MAX_CONCENTRATION_MOL_M3}] mol/m^3")
        out[sid] = float(concentration)
    return {"libraries": [LIB_REL, UV_REL], "composition_mol_m3": out,
            "k_estimate": {sid: _table()[sid]["reversed_phase_C18"]["k_estimate"] for sid in out}}


def species_response(template: dict[str, Any], state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-species retention/width/peak absorbance for the configured method (trusted-host detail)."""
    flow = state["method.flow_m3_s"]
    wavelength = state["method.wavelength_m"]
    injected_volume = state["method.injection_volume_m3"]
    t0 = VOID_VOLUME_M3 / flow
    out = {}
    for sid, concentration in template["composition_mol_m3"].items():
        k = template["k_estimate"][sid]
        t_r = t0 * (1.0 + k)
        sigma = t_r / math.sqrt(PLATE_NUMBER)
        epsilon = common.uv_epsilon(sid, wavelength)
        moles = concentration * injected_volume
        peak_concentration = moles / (flow * sigma * math.sqrt(2 * math.pi))
        out[sid] = {"retention_time_s": t_r, "sigma_s": sigma, "epsilon_m2_mol": epsilon, "injected_mol": moles,
                    "peak_absorbance": None if epsilon is None else epsilon * FLOW_CELL_PATH_M * peak_concentration}
    return out


def chromatogram(template: dict[str, Any], state: dict[str, Any], axis: list[float], complete: bool) -> dict[str, Any]:
    response = species_response(template, state)
    signal = []
    for t in axis:
        total = 0.0
        for item in response.values():
            if item["peak_absorbance"] is not None:
                total += item["peak_absorbance"] * math.exp(-0.5 * ((t - item["retention_time_s"]) / item["sigma_s"]) ** 2)
        signal.append(total)
    area = sum((signal[i] + signal[i - 1]) * (axis[i] - axis[i - 1]) / 2 for i in range(1, len(axis)))
    peaks = [{"retention_time_s": p["apex"], "area_s": p["area"], "height": p["height"], "width_half_height_s": p["width"]}
             for p in common.detect_peaks(axis, signal, PEAK_RELATIVE_THRESHOLD)]
    return {"time_s": axis, "signal": signal, "integrated_signal_s": area, "peaks": peaks,
            "detector_wavelength_m": state["method.wavelength_m"], "flow_cell_path_m": FLOW_CELL_PATH_M,
            "quality": "estimated", "axis_unit": "s", "signal_unit": "1", "signal_quantity": "absorbance",
            "sampling": "host_time_samples", "complete": complete, "purity_fraction": None,
            "calibrated_concentration_mol_m3": None,
            "quantitation_status": "unavailable_no_calibration_or_peak_identification",
            **common.provenance([LIB_REL, UV_REL], "retention: derived_assumption (XLogP); absorbance: NIST UV curves (measured) x assumed flow cell",
                                SYNTHETIC_PARTS, LIMITATIONS)}


def chromatogram_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    series = lambda unit: {"type": "array", "items": n(unit), "maxItems": 10001}
    peak = obj({"retention_time_s": n("s", 0), "area_s": n("s"), "height": n("1"), "width_half_height_s": nullable(n("s", 0))})
    return obj({"time_s": series("s"), "signal": series("1"), "integrated_signal_s": n("s", 0),
                "peaks": {"type": "array", "items": peak}, "detector_wavelength_m": n("m", 0), "flow_cell_path_m": n("m", 0),
                "quality": {"type": "string", "const": "estimated"}, "axis_unit": {"type": "string", "const": "s"},
                "signal_unit": {"type": "string", "const": "1"}, "signal_quantity": {"type": "string", "const": "absorbance"},
                "sampling": {"type": "string", "const": "host_time_samples"}, "complete": {"type": "boolean"},
                "purity_fraction": {"type": "null"}, "calibrated_concentration_mol_m3": {"type": "null"},
                "quantitation_status": {"type": "string", "const": "unavailable_no_calibration_or_peak_identification"},
                **common.provenance_schema()})


def template_read_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    uv = obj({"wavelength_range_m": {"type": "array", "items": n("m", 0), "minItems": 2, "maxItems": 2},
              "max_epsilon_wavelength_m": n("m", 0), "max_epsilon_m2_mol": n("m^2/mol", 0), "points": {"type": "integer", "minimum": 1, "x-unit": "1"},
              "evidence": common.TEXT, "source": common.TEXT})
    return obj({"substance_id": common.TEXT, "name": common.TEXT, "name_zh": common.TEXT, "cas": nullable(common.TEXT),
                "xlogp": nullable(n("1")), "k_estimate": n("1", 0), "predicted_retention_s": n("s", 0),
                "retention_assumption": common.TEXT, "retention_evidence": common.TEXT,
                "uv_detection_candidates_m": {"type": "array", "items": n("m", 0)}, "default_detection_m": nullable(n("m", 0)),
                "uv_curve": nullable(uv), **common.provenance_schema()})


def extend_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    legacy = metadata["capabilities"]["chromatography.get_chromatogram"]["output_schema"]["properties"]["chromatogram"]
    template = chromatogram_schema()
    metadata["capabilities"]["chromatography.get_chromatogram"]["output_schema"]["properties"]["chromatogram"] = {"anyOf": [legacy, template]}
    slot = metadata["state_slots"]["measurement.chromatogram"]
    slot["value_schema"] = {"anyOf": [legacy, template, {"type": "null"}]}
    cap = common.template_read_capability(
        "AgilentHPLC1260Runtime", "caffeine", template_read_schema(), ["detector_module"],
        "spectral_library compound id (XLogP retention estimate + NIST UV curve); does not refer to the loaded vial")
    cap["errors"] = ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "REQUEST_CONFLICT"]
    cap["state_effect_timing"] = {}
    metadata["capabilities"]["template.read"] = cap
    metadata["parameters"]["template_libraries"] = {"value": [LIB_REL, UV_REL], "unit": "1", "evidence": "derived_assumption+measured",
        "conditions": "XLogP-derived C18 retention estimate (not a measured column retention) + NIST UV curves"}
    metadata["parameters"]["template_void_volume_m3"] = {"value": VOID_VOLUME_M3, "unit": "m3", "evidence": "assumption",
        "conditions": "hplc_uv.json t0 = 1.5 min read at an assumed 1.0 mL/min"}
    metadata["parameters"]["template_plate_number"] = {"value": PLATE_NUMBER, "unit": "1", "evidence": "assumption"}
    metadata["parameters"]["template_flow_cell_path_m"] = {"value": FLOW_CELL_PATH_M, "unit": "m", "evidence": "assumption"}
    metadata["limits"] = metadata["limits"] + [
        "Template-backed vials (host register_template_sample) give an absorbance trace from XLogP-derived retention (derived_assumption, not column-measured) and NIST UV curves; the arbitrary Gaussian fixture path is unchanged and remains the default."]
    return metadata
