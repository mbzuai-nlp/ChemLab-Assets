"""Template-backed samples for the Magritek Spinsolve 80 runtime (CHEM-068).

Library: ``assets/reactions/magritek_spinsolve80_nmr_library/templates.json`` (built from the shared
``spectral_library/nmr.json``: HMDB experimental 1H/13C peak lists via PubChem). A trusted host registers a
sample by composition (library ids + amounts in mol); this module turns the reference peak lists into the
runtime's existing damped-sinusoid signal list at the Spinsolve field, so the unchanged acquisition, FID,
DFT spectrum and ``nmr.integrate`` paths measure it. Two agent queries are added:

* ``template.read``               public reference lookup (no information about the loaded tube);
* ``nmr.read_template_spectrum``  the acquired spectrum of a template-backed tube on a ppm axis, with provenance.

Field transform: chemical shifts are field independent (ppm); measured multiplet line offsets are kept in Hz
(first-order J pattern), so ``nu = (delta_centroid - delta_ref) * nu0 + offset_hz`` at nu0 = 80 MHz (1H) or
nu0 * Xi(13C) (13C). Line shape, linewidth, receiver scale and the carrier reference are assumptions.
"""
from __future__ import annotations

import copy
import math
from typing import Any

try:  # scripts/ on sys.path (tier runtime import style)
    from template_library import get_template, index_by_id, library_sha256, load_library, provenance
except ImportError:  # project root on sys.path
    from scripts.template_library import get_template, index_by_id, library_sha256, load_library, provenance

TEMPLATE_REL = "assets/reactions/magritek_spinsolve80_nmr_library/templates.json"
PROTON_FREQUENCY_HZ = 80e6                 # workbook "1H 80 MHz" (documented, nominal)
XI_13C = 0.25145020                        # IUPAC 2001 frequency ratio Xi(13C, TMS) (Harris et al., Pure Appl. Chem. 73, 1795)
CARBON_FREQUENCY_HZ = PROTON_FREQUENCY_HZ * XI_13C
OBSERVE_FREQUENCY_HZ = {"1H": PROTON_FREQUENCY_HZ, "13C": CARBON_FREQUENCY_HZ}
REFERENCE_SHIFT_PPM = {"1H": 6.0, "13C": 110.0}   # assumption: carrier placed mid shift window (0-12 / 0-220 ppm)
LINEWIDTH_HZ = 1.0                                 # assumption: Lorentzian FWHM of every line
T2_S = 1.0 / (math.pi * LINEWIDTH_HZ)
AMPLITUDE_PER_MOL = 1.0e4                          # assumption: arbitrary receiver scale per mol of observed nuclei
MAX_COMPONENTS = 8
MAX_AMOUNT_MOL = 1.0
SOLVENT_KEYS = ("CDCl3", "D2O", "CD3OD", "DMSO-d6", "CCl4", "C6D6", "CD2Cl2", "D2O_5pct_DMSO")
TEMPLATE_CAPABILITIES = ("template.read", "nmr.read_template_spectrum")

SYNTHETIC_PARTS = [
    "Lorentzian line shape with assumed 1 Hz FWHM (T2* = 1/(pi*FWHM)), noise-free averaged FID",
    "receiver amplitude scale (1e4 per mol of observed nuclei) and carrier reference shift (1H 6 ppm, 13C 110 ppm)",
    "13C lines are the 1H-decoupled reference singlets; 13C intensities follow source peak heights (not quantitative)",
]
LIMITATIONS = [
    "Chemical shifts and multiplet line offsets come from HMDB experimental peak lists (via PubChem) at the source field; line offsets in Hz are kept as a first-order J pattern at 80 MHz, so strong-coupling (second-order) effects of the benchtop field are not recomputed.",
    "Source lists give shifts to 0.01 ppm: line offsets carry +/- 0.5e-2 ppm x source MHz quantisation (0.45 Hz at 90 MHz, 3 Hz at 600 MHz).",
    "Proton counts per multiplet are derived from source peak heights constrained to whole RDKit symmetry-equivalent H groups; heights are not areas, so crowded multiplets can be misassigned by one group (per-molecule totals are exact).",
    "No solvent, lock, residual-solvent, water or reference (TMS/DSS) signals, no relaxation weighting, NOE, exchange broadening, concentration or temperature shifts; solvent choice only selects which reference spectrum is used.",
    "13C observe frequency is derived from the nominal 80 MHz 1H frequency and IUPAC Xi(13C); the instrument's actual 13C carrier is not documented.",
]


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def library() -> dict[str, Any]:
    return load_library(TEMPLATE_REL)


def record(substance_id: str) -> dict[str, Any] | None:
    return get_template(TEMPLATE_REL, substance_id) if isinstance(substance_id, str) else None


def substance_ids() -> list[str]:
    return sorted(index_by_id(TEMPLATE_REL))


def select_spectrum(rec: dict[str, Any], nucleus: str, solvent: str | None = None) -> tuple[dict[str, Any] | None, bool]:
    """Reference spectrum for ``nucleus``: first in library order, or the first in ``solvent`` when available."""
    spectra = rec["nuclei"].get(nucleus) or []
    if not spectra:
        return None, False
    if solvent is not None:
        for spectrum in spectra:
            if spectrum["solvent_key"] == solvent:
                return spectrum, True
        return spectra[0], False
    return spectra[0], True


def reference_lines(rec: dict[str, Any], nucleus: str, solvent: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
    """Lines at the Spinsolve field for one molecule (amount 1 mol): shift_ppm, offset from reference, weight (nuclei)."""
    spectrum, solvent_match = select_spectrum(rec, nucleus, solvent)
    if spectrum is None:
        return [], None, solvent_match
    nu0 = OBSERVE_FREQUENCY_HZ[nucleus] / 1e6
    lines = []
    if nucleus == "1H":
        for index, multiplet in enumerate(spectrum["multiplets"]):
            heights = sum(line["height"] for line in multiplet["lines"])
            j_known = spectrum["j_pattern"] == "measured_line_offsets" and all(line["offset_hz"] is not None for line in multiplet["lines"])
            if not j_known or heights <= 0:
                lines.append({"group": index, "center_ppm": multiplet["center_ppm"], "group_nuclei": float(multiplet["n_h"]),
                              "shift_ppm": multiplet["center_ppm"], "weight": float(multiplet["n_h"]), "j_from_source": False})
                continue
            for line in multiplet["lines"]:
                lines.append({"group": index, "center_ppm": multiplet["center_ppm"], "group_nuclei": float(multiplet["n_h"]),
                              "shift_ppm": multiplet["center_ppm"] + line["offset_hz"] / nu0,
                              "weight": float(multiplet["n_h"]) * line["height"] / heights, "j_from_source": True})
    else:
        heights = sum(line["height"] for line in spectrum["lines"])
        for index, line in enumerate(spectrum["lines"]):
            weight = rec["c_count"] * line["height"] / heights if heights > 0 else 0.0
            lines.append({"group": index, "center_ppm": line["ppm"], "group_nuclei": weight, "shift_ppm": line["ppm"],
                          "weight": weight, "j_from_source": False})
    for line in lines:
        line["frequency_offset_hz"] = (line["shift_ppm"] - REFERENCE_SHIFT_PPM[nucleus]) * nu0
    return lines, spectrum, solvent_match


def validate_composition(composition: Any, solvent: Any) -> list[dict[str, Any]]:
    if solvent is not None and solvent not in SOLVENT_KEYS:
        raise ValueError("Unknown solvent key")
    if not isinstance(composition, list) or not 1 <= len(composition) <= MAX_COMPONENTS:
        raise ValueError("Composition must list 1..8 components")
    seen, clean = set(), []
    for item in composition:
        if not isinstance(item, dict) or set(item) != {"substance_id", "amount_mol"}:
            raise ValueError("Each component needs exactly substance_id and amount_mol")
        sid, amount = item["substance_id"], item["amount_mol"]
        if record(sid) is None or sid in seen:
            raise ValueError("Unknown or duplicate template substance_id")
        if not _finite(amount) or not 0 < amount <= MAX_AMOUNT_MOL:
            raise ValueError("amount_mol must be finite, > 0 and <= 1 mol")
        seen.add(sid)
        clean.append({"substance_id": sid, "amount_mol": float(amount)})
    return clean


def build_sample(composition: Any, solvent: Any = None) -> tuple[dict[str, list[dict[str, float]]], dict[str, Any]]:
    """Runtime signal lists ({"1H": [...], "13C": [...]}) plus host-private provenance for one tube."""
    clean = validate_composition(composition, solvent)
    signals: dict[str, list[dict[str, float]]] = {"1H": [], "13C": []}
    used = []
    for item in clean:
        rec = record(item["substance_id"])
        entry = {"substance_id": rec["substance_id"], "amount_mol": item["amount_mol"], "spectra": {}}
        for nucleus in ("1H", "13C"):
            lines, spectrum, solvent_match = reference_lines(rec, nucleus, solvent)
            if spectrum is None:
                entry["spectra"][nucleus] = None
                continue
            for line in lines:
                amplitude = AMPLITUDE_PER_MOL * item["amount_mol"] * line["weight"]
                if amplitude > 0:
                    signals[nucleus].append({"frequency_offset_hz": line["frequency_offset_hz"], "amplitude": amplitude, "t2_s": T2_S})
            entry["spectra"][nucleus] = {"hmdb_spectrum_id": spectrum["hmdb_spectrum_id"], "solvent_key": spectrum["solvent_key"],
                                         "solvent_match": solvent_match, "source_frequency_hz": spectrum["source_frequency_hz"],
                                         "j_pattern": spectrum.get("j_pattern"), "n_lines": len(lines)}
        used.append(entry)
    for nucleus in signals:
        signals[nucleus].sort(key=lambda p: p["frequency_offset_hz"])
    mismatched = sum(1 for e in used for s in e["spectra"].values() if s is not None and not s["solvent_match"])
    prov = {"composition": used, "solvent": solvent, "solvent_mismatches": mismatched,
            "template_source": TEMPLATE_REL, "library_sha256": library_sha256(TEMPLATE_REL)}
    return signals, prov


# ---------------------------------------------------------------- agent-facing capability metadata
def _num(unit, minimum=None, maximum=None, nullable=False):
    schema: dict[str, Any] = {"type": "number", "x-unit": unit}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return {"anyOf": [schema, {"type": "null"}]} if nullable else schema


def _obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}


_TEXT = {"type": "string", "minLength": 1, "maxLength": 128}
_STR = {"type": "string", "minLength": 1}
_NTEXT = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_NUCLEUS = {"type": "string", "enum": ["1H", "13C"]}
_TEXTS = {"type": "array", "items": {"type": "string"}}
_LINE = _obj({"shift_ppm": _num("ppm"), "frequency_offset_hz": _num("Hz"), "relative_nuclei": _num("1", 0)})
_GROUP = _obj({"center_ppm": _num("ppm"), "n_nuclei": _num("1", 0), "lines": {"type": "array", "items": _LINE}})
_STATUS = {"type": "string", "enum": ["idle", "running", "completed", "stopped"]}
_POINT = _obj({"chemical_shift_ppm": _num("ppm"), "frequency_offset_hz": _num("Hz"), "amplitude": _num("1", 0)})

TEMPLATE_READ_INPUT = _obj({"substance_id": _TEXT, "nucleus": _NUCLEUS, "solvent": {"anyOf": [{"type": "string", "enum": list(SOLVENT_KEYS)}, {"type": "null"}]}},
                           required=["substance_id", "nucleus"])
TEMPLATE_READ_OUTPUT = _obj({
    "substance_id": _TEXT, "name": _TEXT, "cas": _NTEXT, "shared_ids": {"type": "object", "additionalProperties": {"type": "string"}},
    "nucleus": _NUCLEUS, "solvent": _NTEXT, "available_solvents": _TEXTS, "source_frequency_hz": _num("Hz", nullable=True),
    "observe_frequency_hz": _num("Hz"), "reference_shift_ppm": _num("ppm"), "groups": {"type": "array", "items": _GROUP},
    "proton_count_rule": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "template_source": _STR,
    "template_record_source": _STR, "spectrum_source": _STR, "evidence": _STR, "library_sha256": _STR,
    "synthetic": {"type": "boolean", "const": False}, "limitations": _TEXTS})
SPECTRUM_INPUT = _obj({})
SPECTRUM_OUTPUT = _obj({
    "samples": {"type": "array", "maxItems": 512, "items": _POINT}, "nucleus": {"anyOf": [_NUCLEUS, {"type": "null"}]},
    "observe_frequency_hz": _num("Hz", nullable=True), "reference_shift_ppm": _num("ppm", nullable=True), "status": _STATUS,
    "completed_scans": {"type": "integer", "minimum": 0, "maximum": 4096, "x-unit": "1"}, "template_source": _STR,
    "library_sha256": _STR, "evidence": _STR, "synthetic": {"type": "boolean", "const": False},
    "synthetic_parts": _TEXTS, "limitations": _TEXTS})


def capability_metadata() -> dict[str, dict[str, Any]]:
    """Agent-visible descriptions of the added queries (merged into Session.describe)."""
    common = {"visibility": "agent", "availability": "implemented", "component_ids": ["control_panel"],
              "implementation": {"process": "implemented", "scene": "not_applicable", "robot": "absent", "hardware": "absent", "host": "implemented"},
              "binding": {"backend": "process", "artifact_id": "template_module", "entrypoint": "MagritekSpinsolve80Session.invoke"},
              "state_effects": [], "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "纯查询，无几何变化。"},
              "source_ids": ["template_library"]}
    return {
        "template.read": {**common, "kind": "query", "function_zh": "按物质id读取公开参考核磁模板（80 MHz下的化学位移、多重峰线位与核数），不透露当前样品身份。",
                          "input_schema": TEMPLATE_READ_INPUT, "output_schema": TEMPLATE_READ_OUTPUT, "observes": [],
                          "example_arguments": {"substance_id": "ethyl_acetate", "nucleus": "1H"},
                          "errors": ["INVALID_ARGUMENT", "UNKNOWN_REFERENCE", "PRECONDITION_FAILED"]},
        "nmr.read_template_spectrum": {**common, "kind": "query", "function_zh": "读取模板样品已采集谱的ppm横轴与来源信息；不推进时间、不暴露样品组成。",
                                       "input_schema": SPECTRUM_INPUT, "output_schema": SPECTRUM_OUTPUT,
                                       "observes": ["measurement.spectrum", "method.nucleus", "acquisition.status", "acquisition.completed_scans"],
                                       "example_arguments": {}, "errors": ["INVALID_ARGUMENT", "PRECONDITION_FAILED"]},
    }


def _matches_input(cap: str, args: Any) -> bool:
    if not isinstance(args, dict):
        return False
    if cap == "nmr.read_template_spectrum":
        return args == {}
    allowed = {"substance_id", "nucleus", "solvent"}
    if not {"substance_id", "nucleus"} <= set(args) <= allowed:
        return False
    sid, nucleus, solvent = args["substance_id"], args["nucleus"], args.get("solvent")
    return (isinstance(sid, str) and 1 <= len(sid) <= 128 and nucleus in ("1H", "13C")
            and (solvent is None or solvent in SOLVENT_KEYS))


def public_reference(substance_id: str, nucleus: str, solvent: str | None = None) -> dict[str, Any] | None:
    rec = record(substance_id)
    if rec is None:
        return None
    lines, spectrum, _ = reference_lines(rec, nucleus, solvent)
    if spectrum is None:
        return {}
    groups: list[dict[str, Any]] = []
    for line in lines:
        if not groups or groups[-1]["_group"] != line["group"]:
            groups.append({"_group": line["group"], "center_ppm": line["center_ppm"], "n_nuclei": line["group_nuclei"], "lines": []})
        groups[-1]["lines"].append({"shift_ppm": line["shift_ppm"], "frequency_offset_hz": line["frequency_offset_hz"], "relative_nuclei": line["weight"]})
    for group in groups:
        del group["_group"]
    rule = spectrum["proton_count_rule"]["status"] if nucleus == "1H" else None
    prov = provenance(TEMPLATE_REL, rec)
    return {"substance_id": rec["substance_id"], "name": rec["name"], "cas": rec.get("cas"), "shared_ids": copy.deepcopy(rec.get("shared_ids", {})),
            "nucleus": nucleus, "solvent": spectrum["solvent_key"], "available_solvents": sorted({s["solvent_key"] for s in rec["nuclei"][nucleus]}),
            "source_frequency_hz": spectrum["source_frequency_hz"], "observe_frequency_hz": OBSERVE_FREQUENCY_HZ[nucleus],
            "reference_shift_ppm": REFERENCE_SHIFT_PPM[nucleus], "groups": groups, "proton_count_rule": rule,
            "template_source": prov["template_source"], "template_record_source": prov["template_record_source"],
            "spectrum_source": spectrum["source_url"], "evidence": prov["evidence"], "library_sha256": prov["library_sha256"],
            "synthetic": False, "limitations": list(LIMITATIONS)}


def spectrum_on_ppm_axis(inst: dict[str, Any], completed_scans: int) -> dict[str, Any]:
    nucleus = (inst.get("method") or {}).get("nucleus")
    points = []
    if nucleus is not None:
        nu0 = OBSERVE_FREQUENCY_HZ[nucleus] / 1e6
        for p in inst["spectrum"]:
            points.append({"chemical_shift_ppm": REFERENCE_SHIFT_PPM[nucleus] + p["frequency_offset_hz"] / nu0,
                           "frequency_offset_hz": p["frequency_offset_hz"], "amplitude": p["amplitude"]})
    return {"samples": points, "nucleus": nucleus, "observe_frequency_hz": OBSERVE_FREQUENCY_HZ.get(nucleus),
            "reference_shift_ppm": REFERENCE_SHIFT_PPM.get(nucleus), "status": inst["status"], "completed_scans": completed_scans,
            "template_source": TEMPLATE_REL, "library_sha256": library_sha256(TEMPLATE_REL),
            "evidence": "measured_literature shifts and J-line offsets (HMDB via PubChem); derived proton counts; assumed line shape",
            "synthetic": False, "synthetic_parts": list(SYNTHETIC_PARTS), "limitations": list(LIMITATIONS)}


def session_query(session, host, instance_id: str, inst: dict[str, Any], cap: str, args: Any, request_id: str) -> dict[str, Any]:
    """Dispatch of the added agent queries; read-only, never advances time or touches the scene."""
    if not _matches_input(cap, args):
        return session._error(instance_id, "INVALID_ARGUMENT", request_id)
    if cap == "template.read":
        result = public_reference(args["substance_id"], args["nucleus"], args.get("solvent"))
        if result is None:
            return session._error(instance_id, "UNKNOWN_REFERENCE", request_id)
        if not result:
            return session._error(instance_id, "PRECONDITION_FAILED", request_id)
        return session._envelope(instance_id, request_id, result=result)
    tube = inst["sample_id"]
    if tube is None or tube not in inst.get("template_samples", {}):
        return session._error(instance_id, "PRECONDITION_FAILED", request_id)
    return session._envelope(instance_id, request_id, result=spectrum_on_ppm_axis(inst, inst["completed_scans"]))


def register_template_sample(host, instance_id: str, sample_id: str, composition: Any, *, solvent: str | None = None) -> None:
    """Trusted-host registration of a tube whose signals come from the NMR template library."""
    with host._lock:
        if instance_id not in host._instances or not isinstance(sample_id, str) or not 1 <= len(sample_id) <= 128:
            raise ValueError("Invalid instance or sample identifier")
        signals, prov = build_sample(composition, solvent)
        inst = host._instances[instance_id]
        if sample_id in inst["samples"] or len(inst["samples"]) >= 64 or inst["running"]:
            raise ValueError("Sample registration unavailable")
        if any(not (math.isfinite(p["frequency_offset_hz"]) and math.isfinite(p["amplitude"])) for n in signals.values() for p in n):
            raise ValueError("Non-finite template signal")
        inst["samples"][sample_id] = signals
        inst.setdefault("template_samples", {})[sample_id] = prov
        inst["revision"] += 1


def template_sample_truth(host, instance_id: str) -> dict[str, Any]:
    """Trusted host / evaluator view of template-backed tubes (composition and reference spectra used)."""
    with host._lock:
        return copy.deepcopy(host._instances[instance_id].get("template_samples", {}))
