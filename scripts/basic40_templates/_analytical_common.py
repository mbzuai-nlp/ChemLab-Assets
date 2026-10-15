"""Shared helpers for the t3_analytical template modules (GC 8890B, HPLC 1260, HS 8697, DR6000, FT/IR-4700, 5910 Ri).

Everything here is read-only access to public-data libraries under ``assets/reactions/``. Library files that are not
in the ``{"templates": [...]}`` layout (the shared ``spectral_library`` tables) are read directly; their provenance
block uses the same SHA-256 helper as :mod:`scripts.template_library`.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

from scripts.template_library import ROOT, library_sha256

SPECTRAL = "assets/reactions/spectral_library"
COMPOUNDS_REL = SPECTRAL + "/compounds.json"
SUBSTANCES_REL = "assets/reactions/substances.json"


@lru_cache(maxsize=None)
def load_json(rel_path: str) -> Any:
    path = (ROOT / rel_path).resolve()
    if ROOT not in path.parents:
        raise ValueError(f"library path escapes the project root: {rel_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def compounds() -> dict[str, dict[str, Any]]:
    return load_json(COMPOUNDS_REL)["compounds"]


def resolve_compound_id(identifier: Any) -> str | None:
    """spectral_library compound id for ``identifier``.

    Accepts a spectral_library id directly, or a ``substances.json`` id (reaction-ledger species) when the same id
    exists in the spectral library (acetic_acid, ethanol, ethyl_acetate, water, carbon_dioxide, hydrogen_peroxide).
    """
    if not isinstance(identifier, str) or not identifier:
        return None
    return identifier if identifier in compounds() else None


def finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def sha256_map(*rel_paths: str) -> dict[str, str]:
    return {rel: library_sha256(rel) for rel in rel_paths}


def number(unit: str, minimum: float | None = None, maximum: float | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "number", "x-unit": unit}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


TEXT = {"type": "string"}
TEXT_LIST = {"type": "array", "items": {"type": "string"}}
SHA_MAP = {"type": "object", "additionalProperties": {"type": "string"}}


def provenance_schema() -> dict[str, Any]:
    """Fields every template-backed result carries."""
    return {"template_source": TEXT_LIST, "library_sha256": SHA_MAP, "evidence": TEXT,
            "synthetic": {"type": "boolean", "const": False}, "synthetic_parts": TEXT_LIST, "limitations": TEXT_LIST}


def provenance(rel_paths: list[str], evidence: str, synthetic_parts: list[str], limitations: list[str]) -> dict[str, Any]:
    return {"template_source": list(rel_paths), "library_sha256": sha256_map(*rel_paths), "evidence": evidence,
            "synthetic": False, "synthetic_parts": list(synthetic_parts), "limitations": list(limitations)}


def template_read_capability(cls_name: str, example_id: str, output_schema: dict[str, Any], component_ids: list[str],
                             description: str) -> dict[str, Any]:
    """Runtime-metadata entry for an agent-visible ``template.read`` reference query."""
    return {"input_schema": obj({"substance_id": {"type": "string", "description": description}}),
            "output_schema": output_schema, "example_arguments": [{"substance_id": example_id}],
            "state_ids": [], "component_ids": component_ids, "preconditions": [],
            "scene_required": False, "availability": "implemented", "kind": "query",
            "invocation": cls_name + ".invoke(instance_id=..., capability_id='template.read', arguments={'substance_id': ...}, request_id=...)",
            "errors": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "REQUEST_CONFLICT"],
            "rejection_side_effects": "none",
            "measurement_cache_semantics": "Public reference lookup; does not read or change the loaded sample, state revision or scene."}


# ---- shared numerical helpers -------------------------------------------------------------------------------------

def interp(x: float, xs: list[float], ys: list[float]) -> float | None:
    """Linear interpolation on an ascending grid; None outside [xs[0], xs[-1]] (no extrapolation)."""
    if not xs or x < xs[0] or x > xs[-1]:
        return None
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    if xs[hi] == xs[lo]:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (x - xs[lo]) / (xs[hi] - xs[lo])


def detect_peaks(time_s: list[float], signal: list[float], relative_threshold: float = 1e-3) -> list[dict[str, Any]]:
    """Unlabeled peak table from a sampled trace.

    Local maxima above ``relative_threshold`` x the largest value; boundaries walk down to the neighbouring valley
    or baseline; trapezoid area between boundaries; apex refined by a three-point parabola; width at half height by
    linear interpolation (None when the half-height crossing lies outside the sampled trace).
    """
    n = len(signal)
    if n < 3 or max(signal) <= 0:
        return []
    threshold = max(signal) * relative_threshold
    peaks = []
    for i in range(1, n - 1):
        c = signal[i]
        if c < threshold or not (c > signal[i - 1] and c >= signal[i + 1]):
            continue
        left = i
        while left > 0 and signal[left - 1] < signal[left]:
            left -= 1
        right = i
        while right < n - 1 and signal[right + 1] < signal[right]:
            right += 1
        area = sum((signal[j] + signal[j + 1]) * (time_s[j + 1] - time_s[j]) / 2 for j in range(left, right))
        denom = signal[i - 1] - 2 * c + signal[i + 1]
        offset = 0.5 * (signal[i - 1] - signal[i + 1]) / denom if denom < 0 else 0.0
        step = (time_s[i + 1] - time_s[i - 1]) / 2
        apex = time_s[i] + offset * step
        half = c / 2
        lo, hi = i, i
        while lo > 0 and signal[lo] > half:
            lo -= 1
        while hi < n - 1 and signal[hi] > half:
            hi += 1
        width = None
        if signal[lo] <= half < signal[lo + 1] and signal[hi] <= half < signal[hi - 1]:
            t_lo = time_s[lo] + (half - signal[lo]) / (signal[lo + 1] - signal[lo]) * (time_s[lo + 1] - time_s[lo])
            t_hi = time_s[hi - 1] + (signal[hi - 1] - half) / (signal[hi - 1] - signal[hi]) * (time_s[hi] - time_s[hi - 1])
            width = t_hi - t_lo
        peaks.append({"apex": apex, "area": area, "height": c, "width": width})
    return peaks


# ---- NIST UV/Vis curves (spectral_library/uv_vis.json) --------------------------------------------------------------

UV_REL = SPECTRAL + "/uv_vis.json"
# NIST curves are log10(epsilon / (L mol^-1 cm^-1)); 1 L mol^-1 cm^-1 = 1e-3 m^3 / (mol * 1e-2 m) = 0.1 m^2/mol.
L_PER_MOL_CM_TO_M2_PER_MOL = 0.1


@lru_cache(maxsize=None)
def uv_curve(compound_id: str) -> dict[str, Any] | None:
    """First NIST UV/Vis curve of a spectral_library compound as SI arrays (ascending wavelength), or None."""
    entry = load_json(UV_REL)["compounds"].get(compound_id) or {}
    spectra = entry.get("spectra") or []
    if not spectra:
        return None
    spectrum = spectra[0]
    pairs = sorted(zip(spectrum["wavelength_nm"], spectrum["y"]))
    wavelengths, eps = [], []
    for nm, log_eps in pairs:
        if wavelengths and nm * 1e-9 <= wavelengths[-1]:
            continue
        wavelengths.append(nm * 1e-9)
        eps.append(10.0 ** log_eps * L_PER_MOL_CM_TO_M2_PER_MOL)
    return {"compound_id": compound_id, "wavelength_m": wavelengths, "epsilon_m2_mol": eps,
            "title": spectrum.get("title"), "nist_index": spectrum.get("nist_index"), "origin": spectrum.get("origin"),
            "y_units_original": spectrum.get("y_units"), "source": spectrum.get("source"), "evidence": spectrum.get("evidence")}


def uv_epsilon(compound_id: str, wavelength_m: float) -> float | None:
    curve = uv_curve(compound_id)
    return None if curve is None else interp(wavelength_m, curve["wavelength_m"], curve["epsilon_m2_mol"])
