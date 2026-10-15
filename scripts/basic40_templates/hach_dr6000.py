"""Template-backed UV-Vis path for the Hach DR6000 runtime (CHEM-071).

Three public-data template families, addressed by one ``template_id`` namespace:
  * ``<spectral_library id>``  NIST UV/Vis curves (``spectral_library/uv_vis.json``; log10 epsilon vs wavelength,
                               31 compounds) -> Beer-Lambert spectrum A = epsilon c l.
  * ``pcad_NNN``               PhotochemCAD 2.1a molar absorption spectra reused read-only from
                               ``jasco_fp8350_fluorescence_library/templates.json`` (90 dyes/aromatics; source licence
                               not confirmed for redistribution, see that library's 说明.md) -> Beer-Lambert spectrum.
  * ``hach_prog_NNN``          Hach DR6000 stored-program method records (``hach_dr6000_colorimetric_library``):
                               program wavelength, range and the published "Sensitivity" (concentration change per
                               0.010 Abs) -> single-wavelength absorbance of a developed (reagent-reacted) sample.
A trusted host registers the cuvette contents; the agent uses the normal lid/load/scan/measure workflow.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

from scripts.basic40_templates import _analytical_common as common
from scripts.template_library import index_by_id

UV_REL = common.UV_REL
PCAD_REL = "assets/reactions/jasco_fp8350_fluorescence_library/templates.json"
HACH_REL = "assets/reactions/hach_dr6000_colorimetric_library/templates.json"
WAVELENGTH_MIN_M = 190e-9
WAVELENGTH_MAX_M = 1100e-9
WAVELENGTH_TOLERANCE_M = 1e-9          # DR6000 catalog wavelength accuracy (200..900 nm)
PHOTOMETRIC_LIMIT = 3.0                # DR6000 catalog photometric range +/-3 Abs (200..900 nm)
DEFAULT_PATH_LENGTH_M = 0.01
MAX_CONCENTRATION_MOL_M3 = 1000.0

LIMITATIONS_SPECTRUM = [
    "NIST UV/Vis curves are digitized literature spectra (INEP CP RAS UV atlas via NIST); solvent/state is not recorded in the JCAMP header. PhotochemCAD spectra are for the named source solvent only; source temperatures are often unknown.",
    "Beer-Lambert A = epsilon c l with no stray light, bandwidth convolution (2 nm slit), wavelength error, noise, scattering, fluorescence or concentration-dependent deviations; points above |A| = 3 are reported but flagged as over the catalog photometric range.",
    "Output is restricted to the part of the scan grid covered by the reference curve; no extrapolation outside it.",
    "Cuvette path length is a trusted-host declaration (DR6000 path length is unknown in the catalog; default 10 mm assumption).",
    "PhotochemCAD data (pcad_*) are reused from the FP-8350 library: original authors' copyright, redistribution licence not confirmed.",
]
LIMITATIONS_METHOD = [
    "Hach method absorbance is reconstructed from the method document's published Sensitivity (concentration change per 0.010 Abs) as a straight line through the origin; the real stored program calibration, curve non-linearity, reagent blank, interferences, reaction timing and temperature are not modelled.",
    "Only the program wavelength (+/- 1 nm) has a defined absorbance; the full spectrum of the developed colour is unknown, so scans over other wavelengths are rejected.",
    "Sample cell is the one stated in the Hach document; the DR6000 cuvette geometry of this asset is not used for these records.",
    "Hach method documents are cited for numbers and conditions only (Hach copyright); no Hach text or figures are reproduced.",
]


@lru_cache(maxsize=None)
def _uv() -> dict[str, Any]:
    return common.load_json(UV_REL)["compounds"]


def _pcad() -> dict[str, dict[str, Any]]:
    return index_by_id(PCAD_REL)


def _hach() -> dict[str, dict[str, Any]]:
    try:
        return index_by_id(HACH_REL)
    except FileNotFoundError:
        return {}


def kind_of(template_id: Any) -> str | None:
    if not isinstance(template_id, str) or not template_id:
        return None
    if template_id in _hach():
        return "hach_method"
    if template_id in _pcad():
        return "photochemcad_spectrum"
    sid = common.resolve_compound_id(template_id)
    if sid is not None and common.uv_curve(sid) is not None:
        return "nist_uv_spectrum"
    return None


def _curve(template_id: str) -> dict[str, Any]:
    kind = kind_of(template_id)
    if kind == "nist_uv_spectrum":
        curve = common.uv_curve(common.resolve_compound_id(template_id))
        return {"wavelength_m": curve["wavelength_m"], "epsilon_m2_mol": curve["epsilon_m2_mol"], "source": curve["source"],
                "evidence": curve["evidence"], "conditions": {"origin": curve["origin"], "solvent": None, "nist_index": curve["nist_index"]},
                "library": UV_REL}
    record = _pcad()[template_id]
    pairs = sorted(zip(record["abs_wavelength_m"], record["epsilon_m2_mol"]))
    wavelengths, eps = [], []
    for wavelength, value in pairs:
        if wavelengths and wavelength <= wavelengths[-1]:
            continue
        wavelengths.append(wavelength)
        eps.append(value)
    return {"wavelength_m": wavelengths, "epsilon_m2_mol": eps, "source": record["source"], "evidence": record["evidence"],
            "conditions": {"solvent": record["conditions"].get("solvent"), "temperature_k": record["conditions"].get("temperature_k")},
            "library": PCAD_REL}


def template_read(template_id: Any) -> dict[str, Any] | None:
    kind = kind_of(template_id)
    if kind is None:
        return None
    if kind == "hach_method":
        record = _hach()[template_id]
        keys = ("name", "cas", "analyte", "reported_as", "method_number", "program_number", "program_name", "chemistry",
                "wavelength_m", "range_kg_m3", "range_as_published", "sample_cell", "path_length_m", "sensitivity", "reaction_time_s",
                "source", "evidence")
        body = {key: record.get(key) for key in keys}
        return {"template_id": template_id, "template_kind": kind, "spectrum": None, "method": body,
                "conditions_json": json.dumps(record.get("conditions"), ensure_ascii=False),
                **common.provenance([HACH_REL], "measured_literature (Hach method document figures)", [], LIMITATIONS_METHOD)}
    curve = _curve(template_id)
    if kind == "nist_uv_spectrum":
        compound = common.compounds()[common.resolve_compound_id(template_id)]
        name, cas = compound["name_en"], compound["cas"]
    else:
        name, cas = _pcad()[template_id]["name"], None
    return {"template_id": template_id, "template_kind": kind, "method": None,
            "spectrum": {"name": name, "cas": cas, "wavelength_m": curve["wavelength_m"], "epsilon_m2_mol": curve["epsilon_m2_mol"],
                         "source": curve["source"], "evidence": curve["evidence"]},
            "conditions_json": json.dumps(curve["conditions"], ensure_ascii=False),
            **common.provenance([curve["library"]], curve["evidence"] + " molar absorption spectrum", ["host-declared path length"], LIMITATIONS_SPECTRUM)}


def validate_sample(template_id: Any, *, concentration_mol_m3: float | None = None, concentration_kg_m3: float | None = None,
                    path_length_m: float = DEFAULT_PATH_LENGTH_M) -> dict[str, Any]:
    kind = kind_of(template_id)
    if kind is None:
        raise ValueError(f"{template_id!r} is not a DR6000 template id (spectral_library UV id, pcad_*, hach_prog_*)")
    if kind == "hach_method":
        record = _hach()[template_id]
        if concentration_mol_m3 is not None or not common.finite(concentration_kg_m3) or concentration_kg_m3 < 0:
            raise ValueError("Hach method samples need concentration_kg_m3 >= 0 (analyte, in the record's reported_as form)")
        if record.get("wavelength_m") is None or not record.get("sensitivity"):
            raise ValueError(f"{template_id} has no published wavelength/sensitivity; it is reference-only")
        return {"template_id": template_id, "template_kind": kind, "library": HACH_REL, "concentration_kg_m3": float(concentration_kg_m3)}
    if concentration_kg_m3 is not None or not common.finite(concentration_mol_m3) or not 0 <= concentration_mol_m3 <= MAX_CONCENTRATION_MOL_M3:
        raise ValueError(f"spectrum samples need concentration_mol_m3 in [0, {MAX_CONCENTRATION_MOL_M3}]")
    if not common.finite(path_length_m) or not 1e-4 <= path_length_m <= 0.1:
        raise ValueError("path_length_m must be within 0.1..100 mm")
    return {"template_id": template_id, "template_kind": kind, "library": _curve(template_id)["library"],
            "concentration_mol_m3": float(concentration_mol_m3), "path_length_m": float(path_length_m), "path_length_evidence": "assumption"}


def _axis(state: dict[str, Any]) -> list[float]:
    start, stop, step = state["scan.start_m"], state["scan.stop_m"], state["scan.step_m"]
    count = int(math.floor((stop - start) / step + 1e-8)) + 1
    axis = [start + i * step for i in range(count)]
    if axis and abs(axis[-1] - stop) <= 1e-15:
        axis[-1] = stop
    return axis


def spectrum(template: dict[str, Any], state: dict[str, Any], sample_kind: str) -> dict[str, Any] | None:
    """Agent-visible spectrum; None when the scan does not meet the template's preconditions."""
    axis = _axis(state)
    mode = state["measurement.mode"]
    if template["template_kind"] == "hach_method":
        record = _hach()[template["template_id"]]
        if len(axis) != 1 or abs(axis[0] - record["wavelength_m"]) > WAVELENGTH_TOLERANCE_M:
            return None
        sens = record["sensitivity"]
        absorbance = [sens["delta_absorbance"] * template["concentration_kg_m3"] / sens["delta_concentration_kg_m3"]]
        low, high = record["range_kg_m3"]
        c = template["concentration_kg_m3"]
        status = "below_range" if c < low else ("above_range" if c > high else "within_range")
        rel = [HACH_REL]
        evidence = "Hach published sensitivity (measured_literature) -> linear absorbance (derived)"
        limitations, synthetic_parts, coverage = LIMITATIONS_METHOD, ["linear calibration through the origin"], [axis[0], axis[0]]
    else:
        curve = _curve(template["template_id"])
        points = [(x, common.interp(x, curve["wavelength_m"], curve["epsilon_m2_mol"])) for x in axis]
        points = [(x, e) for x, e in points if e is not None]
        if not points:
            return None
        axis = [x for x, _ in points]
        absorbance = [e * template["concentration_mol_m3"] * template["path_length_m"] for _, e in points]
        status = None
        rel = [curve["library"]]
        evidence = curve["evidence"] + " molar absorption curve x host concentration x path length (Beer-Lambert, derived)"
        limitations, synthetic_parts = LIMITATIONS_SPECTRUM, ["host-declared path length"]
        coverage = [curve["wavelength_m"][0], curve["wavelength_m"][-1]]
    transmittance = [10.0 ** -a for a in absorbance]
    over = sum(1 for x, a in zip(axis, absorbance) if abs(a) > PHOTOMETRIC_LIMIT)
    return {"wavelength_m": axis, "absorbance": absorbance, "transmittance": transmittance,
            "signal": absorbance[:] if mode == "absorbance" else transmittance[:], "reported_mode": mode,
            "quality": "estimated", "axis_unit": "m", "signal_unit": "1", "sample_kind": sample_kind, "measurement_duration_s": None,
            "template_kind": template["template_kind"], "requested_scan_m": [state["scan.start_m"], state["scan.stop_m"], state["scan.step_m"]],
            "template_coverage_m": coverage, "over_photometric_range_points": over, "method_range_status": status,
            **common.provenance(rel, evidence, synthetic_parts, limitations)}


def spectrum_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    series = lambda unit, minimum=None: {"type": "array", "items": n(unit, minimum), "minItems": 1, "maxItems": 10001}
    return obj({"wavelength_m": series("m"), "absorbance": series("1"), "transmittance": series("1", 0), "signal": series("1"),
                "reported_mode": {"type": "string", "enum": ["absorbance", "transmittance"]},
                "quality": {"type": "string", "const": "estimated"}, "axis_unit": {"type": "string", "const": "m"},
                "signal_unit": {"type": "string", "const": "1"}, "sample_kind": {"type": "string", "enum": ["sample", "blank"]},
                "measurement_duration_s": {"type": "null"},
                "template_kind": {"type": "string", "enum": ["nist_uv_spectrum", "photochemcad_spectrum", "hach_method"]},
                "requested_scan_m": {"type": "array", "items": n("m", 0), "minItems": 3, "maxItems": 3},
                "template_coverage_m": {"type": "array", "items": n("m", 0), "minItems": 2, "maxItems": 2},
                "over_photometric_range_points": {"type": "integer", "minimum": 0, "x-unit": "1"},
                "method_range_status": nullable({"type": "string", "enum": ["below_range", "within_range", "above_range"]}),
                **common.provenance_schema()})


def template_read_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    sensitivity = obj({"portion_of_curve": nullable(common.TEXT), "delta_absorbance": n("1", 0), "delta_concentration_kg_m3": n("kg/m^3", 0)})
    method = obj({"name": common.TEXT, "cas": nullable(common.TEXT), "analyte": nullable(common.TEXT), "reported_as": nullable(common.TEXT),
                  "method_number": nullable(common.TEXT), "program_number": {"type": "integer", "minimum": 1, "x-unit": "1"},
                  "program_name": nullable(common.TEXT), "chemistry": nullable(common.TEXT), "wavelength_m": nullable(n("m", 0)),
                  "range_kg_m3": {"type": "array", "items": n("kg/m^3", 0), "minItems": 2, "maxItems": 2},
                  "range_as_published": nullable(common.TEXT), "sample_cell": nullable(common.TEXT), "path_length_m": nullable(n("m", 0)),
                  "sensitivity": nullable(sensitivity), "reaction_time_s": nullable(n("s", 0)), "source": common.TEXT, "evidence": common.TEXT})
    spectrum = obj({"name": common.TEXT, "cas": nullable(common.TEXT),
                    "wavelength_m": {"type": "array", "items": n("m", 0), "minItems": 2},
                    "epsilon_m2_mol": {"type": "array", "items": n("m^2/mol"), "minItems": 2},
                    "source": common.TEXT, "evidence": common.TEXT})
    return obj({"template_id": common.TEXT, "template_kind": {"type": "string", "enum": ["nist_uv_spectrum", "photochemcad_spectrum", "hach_method"]},
                "spectrum": nullable(spectrum), "method": nullable(method), "conditions_json": common.TEXT, **common.provenance_schema()})


def extend_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    caps, slots = metadata["capabilities"], metadata["state_slots"]
    legacy = caps["spectroscopy.measure"]["output_schema"]["properties"]["spectrum"]
    caps["spectroscopy.measure"]["output_schema"]["properties"]["spectrum"] = {"anyOf": [legacy, spectrum_schema()]}
    caps["spectroscopy.measure"]["preconditions"] = caps["spectroscopy.measure"]["preconditions"] + [
        "Template-backed cuvette only: spectrum templates need at least one scan point inside the reference curve; Hach method samples need a single-wavelength scan within 1e-9 m of the program wavelength."]
    slots["measurement.spectrum"]["value_schema"] = {"anyOf": [legacy, spectrum_schema(), {"type": "null"}]}
    cap = common.template_read_capability(
        "HachDR6000", "hach_prog_490", template_read_schema(), ["display"],
        "Template id: spectral_library compound id (NIST UV curve), pcad_NNN (PhotochemCAD) or hach_prog_NNN (Hach program); does not refer to the loaded cuvette")
    cap["errors"] = ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "REQUEST_CONFLICT"]
    caps["template.read"] = cap
    metadata["parameters"]["template_libraries"] = {"value": [UV_REL, PCAD_REL, HACH_REL], "unit": "1", "evidence": "measured+measured_literature",
        "conditions": "NIST UV/Vis curves; PhotochemCAD molar absorption (FP-8350 library, licence unconfirmed); Hach method documents (numbers only)"}
    metadata["parameters"]["template_default_path_length_m"] = {"value": DEFAULT_PATH_LENGTH_M, "unit": "m", "evidence": "assumption",
        "conditions": "Used only when the trusted host does not declare a cuvette path for a spectrum template"}
    metadata["limits"] = metadata["limits"] + [
        "Template-backed cuvettes (host register_template_sample) return Beer-Lambert spectra from NIST/PhotochemCAD molar absorptivities or Hach-sensitivity absorbances; the synthetic Gaussian fixture path is unchanged and remains the default."]
    return metadata
