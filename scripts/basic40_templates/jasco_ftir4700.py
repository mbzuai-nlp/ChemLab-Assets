"""Template-backed FTIR path for the JASCO FT/IR-4700 runtime (CHEM-072).

Library (shared): ``assets/reactions/spectral_library/ir_spectra.json`` — up to four NIST WebBook IR spectra per
compound, each resampled to a 400-4000 cm^-1, 4 cm^-1 grid of absorbance normalised to 1 (null outside the measured
range) with state/sampling/resolution metadata.  Since the 2026-10-14 decoder fix (scripts/spectral_library/jcamp.py)
the shared file itself carries the corrected spectra that this asset used to overlay from
``assets/reactions/jasco_ftir4700_ir_library`` (24 records: DELTAX sign, optical-constant k block, reflectance
log10(1/R)); the overlay library is retired.  Corrected records are recognisable by their ``y_transform`` /
``x_step_note`` fields and are reported with ``corrected_by``.

A trusted host registers a cuvette/ATR sample as one spectral_library compound id (+ phase). ``measure`` selects a
reference spectrum whose physical state matches the sample (condensed phase: neat liquid > solution > solid;
gas: gas/vapour), broadens it to the configured resolution (Gaussian, FWHM^2 = R_req^2 - R_src^2), resamples it on
the configured range and scales the normalised band to a host-chosen peak absorbance.

NIST spectra are transmission spectra (gas cell, neat film, CCl4/CS2 solution, KBr/mull); none is an ATR spectrum and
no ATR penetration-depth correction is applied.
"""
from __future__ import annotations

import copy
import math
import re
from functools import lru_cache
from typing import Any

from scripts.basic40_templates import _analytical_common as common

LIB_REL = common.SPECTRAL + "/ir_spectra.json"
DECODER_FIX = "scripts/spectral_library/jcamp.py (2026-10-14 decoder fix; replaces the retired jasco_ftir4700_ir_library overlay)"
SOURCES = [LIB_REL]
GRID_START_CM = 400.0
GRID_STEP_CM = 4.0
CM_TO_M_INV = 100.0
PHASES = ("solid", "liquid", "gas")
MAX_PEAK_ABSORBANCE = 3.0
INTERFEROGRAM_POINTS = 257

LIMITATIONS = [
    "NIST WebBook IR spectra are transmission spectra (gas cells, neat liquid films, CCl4/CS2 solutions, KBr/mull); none is an ATR spectrum and no ATR penetration-depth, refractive-index or anomalous-dispersion correction is applied, so ATR relative band intensities will differ.",
    "Library absorbance is normalised to 1 at the strongest band; absolute absorbance (path length x concentration) is unknown, so the host chooses the peak absorbance (default 1.0).",
    "Effective resolution is max(requested, source resolution, 4 cm^-1 library grid); finer requested resolution cannot be recovered (no deconvolution).",
    "Gas-phase and condensed-phase spectra differ (rotational structure, hydrogen bonding); when no reference spectrum matches the sample phase the closest available one is used and phase_matched=false is reported.",
    "Solution spectra contain CCl4/CS2 solvent-compensated regions; no noise, water-vapour/CO2 background, baseline drift or scan-averaging model.",
    "Only one compound per sample; mixtures are not supported.",
]
SYNTHETIC_PARTS = ["host-chosen peak absorbance scale", "Gaussian instrument line shape", "cosine-synthesized interferogram"]


@lru_cache(maxsize=None)
def _table() -> dict[str, Any]:
    """Shared spectra; records corrected by the 2026-10-14 decoder fix are tagged with corrected_by / correction."""
    table = copy.deepcopy(common.load_json(LIB_REL)["compounds"])
    for spectra in table.values():
        for spectrum in spectra:
            note = spectrum.get("y_transform") or spectrum.get("x_step_note")
            if note:
                spectrum["corrected_by"] = DECODER_FIX
                spectrum["correction"] = note
    return table


def state_class(spectrum: dict[str, Any]) -> str:
    text = (spectrum.get("state") or "").upper()
    if "GAS" in text or "VAPOR" in text or "VAPOUR" in text:
        return "gas"
    if "SOLUTION" in text:
        return "solution"
    if "LIQUID" in text or "NEAT" in text:
        return "liquid"
    if "SOLID" in text or "KBR" in text or "MULL" in text or "NUJOL" in text:
        return "solid"
    return "unknown"


def source_resolution_cm(spectrum: dict[str, Any]) -> float:
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", str(spectrum.get("resolution") or ""))
    value = float(match.group(0)) if match else GRID_STEP_CM
    return max(value, GRID_STEP_CM)


PREFERENCE = {"liquid": ("liquid", "solution", "solid"), "solid": ("solid", "solution", "liquid"), "gas": ("gas",)}


def select_spectrum(substance_id: str, phase: str) -> tuple[int, dict[str, Any], bool] | None:
    spectra = _table().get(substance_id) or []
    if not spectra:
        return None
    for wanted in PREFERENCE[phase]:
        for index, spectrum in enumerate(spectra):
            if state_class(spectrum) == wanted:
                return index, spectrum, True
    return 0, spectra[0], False


def template_read(substance_id: Any) -> dict[str, Any] | None:
    sid = common.resolve_compound_id(substance_id)
    spectra = _table().get(sid) if sid else None
    if not spectra:
        return None
    compound = common.compounds()[sid]
    listing = []
    for index, spectrum in enumerate(spectra):
        values = spectrum["absorbance_normalized"]
        covered = [i for i, v in enumerate(values) if v is not None]
        listing.append({"index": index, "state": spectrum.get("state"), "state_class": state_class(spectrum),
                        "sampling": spectrum.get("sampling"), "source_resolution_m_inv": source_resolution_cm(spectrum) * CM_TO_M_INV,
                        "coverage_m_inv": [(GRID_START_CM + covered[0] * GRID_STEP_CM) * CM_TO_M_INV,
                                           (GRID_START_CM + covered[-1] * GRID_STEP_CM) * CM_TO_M_INV],
                        "peaks_m_inv": [[p[0] * CM_TO_M_INV, p[1]] for p in spectrum.get("peaks_cm-1") or []],
                        "evidence": spectrum.get("evidence"), "source": spectrum.get("source"),
                        "corrected_by": spectrum.get("corrected_by")})
    return {"substance_id": sid, "name": compound["name_en"], "name_zh": compound["name_zh"], "cas": compound["cas"],
            "spectra": listing,
            **common.provenance(SOURCES, "measured (NIST WebBook IR JCAMP-DX, transmission), normalised and resampled; records corrected by the 2026-10-14 decoder fix are flagged corrected_by",
                                SYNTHETIC_PARTS, LIMITATIONS)}


def validate_sample(substance_id: Any, *, phase: str, peak_absorbance: float = 1.0) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError("phase must be solid, liquid or gas")
    sid = common.resolve_compound_id(substance_id)
    if sid is None or not _table().get(sid):
        raise ValueError(f"{substance_id!r} has no IR spectrum in {LIB_REL}")
    if not common.finite(peak_absorbance) or not 0 < peak_absorbance <= MAX_PEAK_ABSORBANCE:
        raise ValueError(f"peak_absorbance must be in (0, {MAX_PEAK_ABSORBANCE}]")
    index, spectrum, matched = select_spectrum(sid, phase)
    return {"library": LIB_REL, "substance_id": sid, "phase": phase, "spectrum_index": index, "phase_matched": matched,
            "state": spectrum.get("state"), "peak_absorbance": float(peak_absorbance)}


def _broadened(values: list[float | None], extra_fwhm_cm: float) -> tuple[list[float], float, float]:
    covered = [i for i, v in enumerate(values) if v is not None]
    lo, hi = covered[0], covered[-1]
    data = [float(values[i]) for i in range(lo, hi + 1)]
    if extra_fwhm_cm > 1e-9:
        sigma = extra_fwhm_cm / (2 * math.sqrt(2 * math.log(2))) / GRID_STEP_CM
        half = max(1, int(math.ceil(4 * sigma)))
        kernel = [math.exp(-0.5 * (j / sigma) ** 2) for j in range(-half, half + 1)]
        out = []
        for i in range(len(data)):
            num = den = 0.0
            for j, weight in zip(range(i - half, i + half + 1), kernel):
                if 0 <= j < len(data):
                    num += weight * data[j]
                    den += weight
            out.append(num / den)
        data = out
    return data, GRID_START_CM + lo * GRID_STEP_CM, GRID_START_CM + hi * GRID_STEP_CM


def spectrum(template: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    """Agent-visible spectrum; None when the configured range does not overlap the reference coverage."""
    reference = _table()[template["substance_id"]][template["spectrum_index"]]
    requested_cm = state["method.resolution_m_inv"] / CM_TO_M_INV
    source_cm = source_resolution_cm(reference)
    effective_cm = max(requested_cm, source_cm)
    extra = math.sqrt(max(0.0, effective_cm ** 2 - source_cm ** 2))
    data, first_cm, last_cm = _broadened(reference["absorbance_normalized"], extra)
    start_cm = max(state["method.start_wavenumber_m_inv"] / CM_TO_M_INV, first_cm)
    stop_cm = min(state["method.stop_wavenumber_m_inv"] / CM_TO_M_INV, last_cm)
    step_cm = max(GRID_STEP_CM, effective_cm / 2)
    if stop_cm - start_cm < step_cm:
        return None
    count = int(math.floor((stop_cm - start_cm) / step_cm + 1e-9)) + 1
    grid_cm = [first_cm + i * GRID_STEP_CM for i in range(len(data))]
    top = max(data) or 1.0
    axis, absorbance = [], []
    for i in range(count):
        x = start_cm + i * step_cm
        value = common.interp(x, grid_cm, data)
        axis.append(x * CM_TO_M_INV)
        absorbance.append(max(0.0, value) / top * template["peak_absorbance"])
    transmittance = [10.0 ** -a for a in absorbance]
    top_m = axis[-1]
    opd = [(index - (INTERFEROGRAM_POINTS - 1) // 2) / (2 * top_m) for index in range(INTERFEROGRAM_POINTS)]
    interferogram = [sum(t * math.cos(2 * math.pi * nu * path) for nu, t in zip(axis, transmittance)) / len(axis) for path in opd]
    return {"wavenumber_m_inv": axis, "absorbance": absorbance, "transmittance": transmittance,
            "interferogram": {"optical_path_difference_m": opd, "relative_intensity": interferogram, "synthetic": True,
                              "axis_unit": "m", "signal_unit": "1"},
            "reported_mode": state["method.output_mode"], "quality": "estimated", "axis_unit": "m^-1", "signal_unit": "1",
            "resolution_m_inv": state["method.resolution_m_inv"], "effective_resolution_m_inv": effective_cm * CM_TO_M_INV,
            "point_spacing_m_inv": step_cm * CM_TO_M_INV, "scan_count": state["method.scan_count"],
            "spectral_resolution_applied": True, "scan_averaging_applied": False, "measurement_duration_s": None,
            "requested_range_m_inv": [state["method.start_wavenumber_m_inv"], state["method.stop_wavenumber_m_inv"]],
            "coverage_m_inv": [axis[0], axis[-1]], "reference_state": template["state"], "phase_matched": template["phase_matched"],
            "accessory_mode": state["accessory.mode"], "atr_correction_applied": False,
            **common.provenance(SOURCES, "measured (NIST WebBook IR, transmission) -> normalised, broadened to the configured resolution and resampled",
                                SYNTHETIC_PARTS, LIMITATIONS)}


def spectrum_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    series = lambda unit, minimum=None: {"type": "array", "items": n(unit, minimum), "minItems": 2, "maxItems": 901}
    pair = lambda unit: {"type": "array", "items": n(unit, 0), "minItems": 2, "maxItems": 2}
    interferogram = obj({"optical_path_difference_m": {"type": "array", "items": n("m"), "minItems": INTERFEROGRAM_POINTS, "maxItems": INTERFEROGRAM_POINTS},
                         "relative_intensity": {"type": "array", "items": n("1"), "minItems": INTERFEROGRAM_POINTS, "maxItems": INTERFEROGRAM_POINTS},
                         "synthetic": {"type": "boolean", "const": True}, "axis_unit": {"type": "string", "const": "m"},
                         "signal_unit": {"type": "string", "const": "1"}})
    return obj({"wavenumber_m_inv": series("m^-1", 0), "absorbance": series("1", 0), "transmittance": series("1", 0),
                "interferogram": interferogram,
                "reported_mode": {"type": "string", "enum": ["absorbance", "transmittance", "interferogram"]},
                "quality": {"type": "string", "const": "estimated"}, "axis_unit": {"type": "string", "const": "m^-1"},
                "signal_unit": {"type": "string", "const": "1"}, "resolution_m_inv": n("m^-1", 40),
                "effective_resolution_m_inv": n("m^-1", 400), "point_spacing_m_inv": n("m^-1", 400),
                "scan_count": {"type": "integer", "minimum": 1, "maximum": 1024, "x-unit": "1"},
                "spectral_resolution_applied": {"type": "boolean", "const": True},
                "scan_averaging_applied": {"type": "boolean", "const": False}, "measurement_duration_s": {"type": "null"},
                "requested_range_m_inv": pair("m^-1"), "coverage_m_inv": pair("m^-1"), "reference_state": nullable(common.TEXT),
                "phase_matched": {"type": "boolean"}, "accessory_mode": {"type": "string", "enum": ["atr", "transmission"]},
                "atr_correction_applied": {"type": "boolean", "const": False}, **common.provenance_schema()})


def template_read_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    item = obj({"index": {"type": "integer", "minimum": 0, "x-unit": "1"}, "state": nullable(common.TEXT),
                "state_class": {"type": "string", "enum": ["gas", "solution", "liquid", "solid", "unknown"]},
                "sampling": nullable(common.TEXT), "source_resolution_m_inv": n("m^-1", 0),
                "coverage_m_inv": {"type": "array", "items": n("m^-1", 0), "minItems": 2, "maxItems": 2},
                "peaks_m_inv": {"type": "array", "items": {"type": "array", "items": n("1"), "minItems": 2, "maxItems": 2}},
                "evidence": common.TEXT, "source": common.TEXT, "corrected_by": nullable(common.TEXT)})
    return obj({"substance_id": common.TEXT, "name": common.TEXT, "name_zh": common.TEXT, "cas": nullable(common.TEXT),
                "spectra": {"type": "array", "items": item, "minItems": 1}, **common.provenance_schema()})


def extend_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    legacy = metadata["capabilities"]["measure"]["output_schema"]["properties"]["spectrum"]
    template = spectrum_schema()
    metadata["capabilities"]["measure"]["output_schema"]["properties"]["spectrum"] = {"anyOf": [legacy, template]}
    metadata["capabilities"]["measure"]["preconditions"] = metadata["capabilities"]["measure"]["preconditions"] + [
        "Template-backed sample only: the configured wavenumber range must overlap the reference spectrum coverage by at least one output step."]
    metadata["state_slots"]["measurement.spectrum"]["value_schema"] = {"anyOf": [legacy, template, {"type": "null"}]}
    cap = common.template_read_capability(
        "JascoFTIR4700", "ethyl_acetate", template_read_schema(), ["optical_module"],
        "spectral_library compound id (NIST IR reference spectra); does not refer to the loaded sample")
    cap["errors"] = ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "REQUEST_CONFLICT"]
    metadata["capabilities"]["template.read"] = cap
    metadata["parameters"]["template_library"] = {"value": SOURCES, "unit": "1", "evidence": "measured",
        "conditions": "NIST WebBook IR transmission spectra, normalised absorbance on a 400-4000 cm^-1, 4 cm^-1 grid; not ATR"}
    metadata["parameters"]["template_grid_step_m_inv"] = {"value": GRID_STEP_CM * CM_TO_M_INV, "unit": "m^-1", "evidence": "derived",
        "conditions": "library resampling grid; output spacing = max(grid, effective resolution / 2)"}
    metadata["limits"] = metadata["limits"] + [
        "Template-backed samples (host register_template_sample) return NIST transmission IR spectra broadened to the configured resolution; no ATR correction. The synthetic Gaussian fixture path is unchanged and remains the default."]
    return metadata
