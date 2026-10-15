"""Template-backed powder samples for the Rigaku MiniFlex600 runtime (CHEM-160).

Library: ``assets/reactions/rigaku_miniflex600_xrd_library/templates.json`` (RRUFF XPOW peak lists with
RRUFF-refined cells, confirmed against the measured RRUFF powder profile of the same sample where available, plus
phases calculated from COD structures where RRUFF has no usable Cu-Kalpha peak list). A trusted host loads a sample as phase weight fractions; this module
renders the stick patterns into the runtime's existing response grid, so the unchanged scan/integration/
``xrd.read_pattern`` path measures it. Two agent queries are added:

* ``template.read``             public reference lookup (no information about the loaded sample);
* ``xrd.read_template_pattern`` the acquired points of a template-backed sample with provenance and limitations.

Instrument transform (assumptions, labelled): Cu Kalpha1/Kalpha2 doublet from each d-spacing (Hoelzer et al. 1997
wavelengths, I(Ka2)/I(Ka1) = 0.5), pseudo-Voigt profile with Caglioti FWHM, equal reference-intensity ratios for
all phases, no background, absorption, preferred orientation or counting noise; peak-rate scale linear in tube
current and (V/V_K - 1)^1.67 in tube voltage.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

try:
    from template_library import get_template, index_by_id, library_sha256, load_library, provenance
except ImportError:
    from scripts.template_library import get_template, index_by_id, library_sha256, load_library, provenance

TEMPLATE_REL = "assets/reactions/rigaku_miniflex600_xrd_library/templates.json"
KA1_M = 1.5405929e-10            # Hoelzer et al., Phys. Rev. A 56, 4554 (1997)
KA2_M = 1.5444274e-10
KA2_RATIO = 0.5                  # nominal I(Ka2)/I(Ka1)
CAGLIOTI_DEG2 = (0.004, -0.002, 0.006)   # assumption: U, V, W of FWHM^2 = U tan^2(theta) + V tan(theta) + W (deg^2)
PV_ETA = 0.5                     # assumption: pseudo-Voigt Lorentzian fraction
PEAK_RATE_REF_CPS = 1.0e4        # assumption: strongest peak of a pure phase at 40 kV / 15 mA
REF_VOLTAGE_V, REF_CURRENT_A = 40000.0, 0.015
CU_K_EDGE_V = 8979.0             # Cu K absorption edge (8.979 keV, Bearden & Burr 1967)
VOLTAGE_EXPONENT = 1.67          # empirical characteristic-line law I ~ i (V/V_K - 1)^n (Green & Cosslett 1968), assumption
GRID_STEP_DEG = 0.005            # matches the 0.005 deg angle quantum, so scan points fall on grid points
WINDOW_FWHM = 25.0
MAX_PHASES = 8
TEMPLATE_CAPABILITIES = ("template.read", "xrd.read_template_pattern")

SYNTHETIC_PARTS = [
    "instrument profile: pseudo-Voigt (eta 0.5) with Caglioti FWHM U=0.004, V=-0.002, W=0.006 deg^2 and a Cu Ka1/Ka2 (2:1) doublet per reflection",
    "intensity scale: 1e4 cps for the strongest peak of a pure phase at 40 kV/15 mA, linear in current, (V/8.979 kV - 1)^1.67 in voltage; equal reference-intensity ratios between phases",
    "no background, fluorescence, absorption/microabsorption, preferred orientation, sample displacement, Kbeta or Poisson counting noise",
]
LIMITATIONS = [
    "Peak positions come from d-spacings of RRUFF-refined cells (RRUFF XPOW) or COD structures; relative intensities are calculated for an ideal random powder (XPOW or powder_pattern.py), not measured on a MiniFlex600.",
    "Template coverage is 5-90 deg 2theta (the XPOW calculation bounds); scans outside it are rejected by the unchanged start precondition (response does not cover the scan).",
    "Weight fractions are converted to intensities with equal reference-intensity ratios (no RIR/absorption correction), so phase ratios read from peak heights are only semi-quantitative; any remainder below 1 is treated as non-diffracting.",
    "Tube voltage/current change only the overall scale; slit labels still do not change the profile (aperture unknown).",
    "A few RRUFF phases are only partially confirmed by their measured RRUFF profile (preferred orientation or sample differences) and COD-calculated phases have no measured confirmation; see measured_check per record in template.read.",
]


def library() -> dict[str, Any]:
    return load_library(TEMPLATE_REL)


def record(phase_id: str) -> dict[str, Any] | None:
    if not isinstance(phase_id, str):
        return None
    rec = get_template(TEMPLATE_REL, phase_id)
    if rec is None:
        default = library().get("substance_defaults", {}).get(phase_id)
        if default:
            return get_template(TEMPLATE_REL, default)
        matches = [r for r in library()["templates"] if r.get("shared_ids", {}).get("substances") == phase_id]
        rec = matches[0] if len(matches) == 1 else None
    return rec


def phase_ids() -> list[str]:
    return sorted(index_by_id(TEMPLATE_REL))


def coverage_rad(records) -> tuple[float, float]:
    lo = max(r["two_theta_range_rad"][0] for r in records)
    hi = min(r["two_theta_range_rad"][1] for r in records)
    return lo, hi


def fwhm_deg(two_theta_deg: float) -> float:
    u, v, w = CAGLIOTI_DEG2
    t = math.tan(math.radians(two_theta_deg / 2))
    return math.sqrt(max(u * t * t + v * t + w, 1e-6))


def tube_scale(voltage_v: float, current_a: float) -> float:
    if voltage_v <= CU_K_EDGE_V:
        return 0.0
    rel_v = ((voltage_v / CU_K_EDGE_V - 1) / (REF_VOLTAGE_V / CU_K_EDGE_V - 1)) ** VOLTAGE_EXPONENT
    return PEAK_RATE_REF_CPS * (current_a / REF_CURRENT_A) * rel_v


def lines_for(rec: dict[str, Any]) -> list[tuple[float, float]]:
    """(2theta_deg, relative height) of Ka1 and Ka2 components from the record's d-spacings."""
    out = []
    for refl in rec["reflections"]:
        d = refl["d_m"]
        for wavelength, weight in ((KA1_M, 1.0), (KA2_M, KA2_RATIO)):
            s = wavelength / (2 * d)
            if s < 1:
                out.append((math.degrees(2 * math.asin(s)), weight * refl["intensity_rel"] / 100.0))
    return out


def render(components: list[tuple[dict[str, Any], float]], scale_cps: float, lo_deg: float, hi_deg: float) -> list[float]:
    n = int(round((hi_deg - lo_deg) / GRID_STEP_DEG)) + 1
    y = [0.0] * n
    ln2x4 = 4 * math.log(2)
    for rec, fraction in components:
        amp = scale_cps * fraction
        if amp <= 0:
            continue
        for center, height in lines_for(rec):
            h = fwhm_deg(center)
            half = WINDOW_FWHM * h
            i0 = max(0, int(math.floor((center - half - lo_deg) / GRID_STEP_DEG)))
            i1 = min(n - 1, int(math.ceil((center + half - lo_deg) / GRID_STEP_DEG)))
            a = amp * height
            inv_h2 = 1.0 / (h * h)
            for i in range(i0, i1 + 1):
                x = lo_deg + i * GRID_STEP_DEG - center
                q = x * x * inv_h2
                y[i] += a * (PV_ETA / (1 + 4 * q) + (1 - PV_ETA) * math.exp(-ln2x4 * q))
    return y


def response_digest(response: list[dict[str, float]]) -> str:
    return hashlib.sha256(json.dumps(response, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_phases(phases: Any) -> list[tuple[dict[str, Any], float]]:
    if not isinstance(phases, list) or not 1 <= len(phases) <= MAX_PHASES:
        raise ValueError("phases must list 1..8 entries")
    out, seen, total = [], set(), 0.0
    for item in phases:
        if not isinstance(item, dict) or set(item) != {"phase_id", "weight_fraction"}:
            raise ValueError("each phase needs exactly phase_id and weight_fraction")
        rec = record(item["phase_id"])
        w = item["weight_fraction"]
        if rec is None or rec["substance_id"] in seen:
            raise ValueError("unknown or duplicate phase_id")
        if type(w) not in (int, float) or not math.isfinite(w) or not 0 < w <= 1:
            raise ValueError("weight_fraction must be finite and in (0, 1]")
        seen.add(rec["substance_id"])
        total += w
        out.append((rec, float(w)))
    if total > 1 + 1e-9:
        raise ValueError("weight fractions add up to more than 1")
    return out


def load_template_sample(host, instance_id: str, phases: Any) -> dict[str, Any]:
    """Trusted host: render the phases into a response grid and load it through host_load_synthetic_response."""
    with host._lock:
        inst = host._instances.get(instance_id)
        if inst is None or inst["running"] or inst["sample"] is None or any(inst[k] is None for k in ("voltage", "current", "slit")):
            raise ValueError("Stopped configured sample (tube, slit) required")
        components = validate_phases(phases)
        lo, hi = coverage_rad([rec for rec, _ in components])
        lo_deg, hi_deg = round(math.degrees(lo) / GRID_STEP_DEG) * GRID_STEP_DEG, round(math.degrees(hi) / GRID_STEP_DEG) * GRID_STEP_DEG
        scale = tube_scale(inst["voltage"], inst["current"])
        values = render(components, scale, lo_deg, hi_deg)
        response = [{"two_theta_rad": math.radians(lo_deg + i * GRID_STEP_DEG), "intensity_cps": max(0.0, v)} for i, v in enumerate(values)]
        host.host_load_synthetic_response(instance_id, response)
        committed = host._instances[instance_id]
        meta = {"phases": [{"phase_id": rec["substance_id"], "weight_fraction": w, "kind": rec["kind"]} for rec, w in components],
                "non_diffracting_fraction": max(0.0, 1.0 - sum(w for _, w in components)),
                "sample": committed["sample"], "configuration": [committed["voltage"], committed["current"], committed["slit"]],
                "scale_cps": scale, "coverage_rad": [math.radians(lo_deg), math.radians(hi_deg)], "grid_step_deg": GRID_STEP_DEG,
                "response_sha256": response_digest(committed["response"]), "template_source": TEMPLATE_REL,
                "library_sha256": library_sha256(TEMPLATE_REL)}
        committed["template_meta"] = meta
        return copy.deepcopy(meta)


def template_backed(inst: dict[str, Any]) -> dict[str, Any] | None:
    meta = inst.get("template_meta")
    if not meta or inst.get("response") is None or inst.get("response_sample") != meta["sample"] or inst.get("sample") != meta["sample"]:
        return None
    return meta if response_digest(inst["response"]) == meta["response_sha256"] else None


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


_TEXT = {"type": "string", "minLength": 1, "maxLength": 256}
_STR = {"type": "string", "minLength": 1}
_NTEXT = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_TEXTS = {"type": "array", "items": {"type": "string"}}
_RANGE = {"type": "array", "items": _num("rad"), "minItems": 2, "maxItems": 2}
_REFL = _obj({"two_theta_rad": _num("rad"), "d_m": _num("m", 0), "intensity_rel": _num("1", 0, 100),
              "hkl": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3}})
_CHECK = {"anyOf": [_obj({"status": {"type": "string"}, "n_matched": {"type": "integer", "minimum": 0}, "n_checked": {"type": "integer", "minimum": 0}}), {"type": "null"}]}
_STATUS = {"type": "string", "enum": ["idle", "running", "completed", "stopped", "cooling_lost", "power_lost"]}
_POINT = _obj({"two_theta_rad": _num("rad"), "intensity_cps": _num("1/s", 0), "expected_counts": _num("1", 0), "sample_time_s": _num("s", 0),
               "integration_time_s": _num("s", 0), "sample_window_s": {"type": "array", "items": _num("s", 0), "minItems": 2, "maxItems": 2}})

TEMPLATE_READ_INPUT = _obj({"phase_id": _TEXT})
TEMPLATE_READ_OUTPUT = _obj({
    "phase_id": _TEXT, "name": _TEXT, "name_zh": _NTEXT, "formula": _TEXT, "cas": _NTEXT,
    "shared_ids": {"type": "object", "additionalProperties": {"type": "string"}}, "kind": {"type": "string", "enum": ["rruff_xpow_dif", "cod_calculated"]},
    "wavelength_m": _num("m", 0), "two_theta_range_rad": _RANGE, "reflections": {"type": "array", "items": _REFL},
    "measured_check": _CHECK, "template_source": _STR, "template_record_source": _STR, "evidence": _STR,
    "library_sha256": _STR, "synthetic": {"type": "boolean", "const": False}, "limitations": _TEXTS})
PATTERN_INPUT = _obj({})
PATTERN_OUTPUT = _obj({
    "samples": {"type": "array", "maxItems": 29601, "items": _POINT}, "status": _STATUS,
    "two_theta_coverage_rad": _RANGE, "wavelengths_m": {"type": "array", "items": _num("m", 0), "minItems": 2, "maxItems": 2},
    "profile_assumption": _obj({"caglioti_u_deg2": _num("deg^2"), "caglioti_v_deg2": _num("deg^2"), "caglioti_w_deg2": _num("deg^2"),
                                "pseudo_voigt_eta": _num("1", 0, 1), "ka2_to_ka1_ratio": _num("1", 0, 1)}),
    "template_source": _STR, "library_sha256": _STR, "evidence": _STR,
    "synthetic": {"type": "boolean", "const": False}, "synthetic_parts": _TEXTS, "limitations": _TEXTS})


def capability_metadata() -> dict[str, dict[str, Any]]:
    common = {"visibility": "agent", "availability": "implemented", "kind": "query", "component_ids": ["detector"],
              "state_effects": [], "preconditions_zh": "实例可用。", "precondition_state_ids": [],
              "binding": "basic40_runtime_measure.RigakuMiniFlex600Session.invoke(instance_id=..., capability_id=..., arguments=..., request_id=...) -> basic40_templates.rigaku_miniflex600.session_query",
              "completion_criterion_zh": "同步查询，无状态变化。", "partial_failure_zh": "查询无副作用。",
              "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "数据读取不改变几何。"},
              "implementation": {"process": "implemented", "host": "implemented", "scene": "not_applicable", "robot": "absent", "hardware": "absent"},
              "error_codes": ["UNKNOWN_INSTANCE", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "REQUEST_CONFLICT"], "retry": "same_request_id; in-memory only"}
    return {
        "template.read": {**common, "description_zh": "按物相id读取公开参考XRD模板（d值、Cu Kα 2θ、相对强度、hkl与来源），不透露当前样品。",
                          "input_schema": TEMPLATE_READ_INPUT, "output_schema": TEMPLATE_READ_OUTPUT, "example_arguments": [{"phase_id": "calcite"}],
                          "observes": []},
        "xrd.read_template_pattern": {**common, "description_zh": "读取模板样品已完成积分点及来源、仪器展宽假设与局限；不暴露物相组成。",
                                      "input_schema": PATTERN_INPUT, "output_schema": PATTERN_OUTPUT, "example_arguments": [{}],
                                      "observes": ["measurement.pattern", "scan.status"]},
    }


def public_reference(phase_id: str) -> dict[str, Any] | None:
    rec = record(phase_id)
    if rec is None:
        return None
    prov = provenance(TEMPLATE_REL, rec)
    mc = rec.get("measured_check")
    return {"phase_id": rec["substance_id"], "name": rec["name"], "name_zh": rec.get("name_zh"), "formula": rec["formula"], "cas": rec.get("cas"),
            "shared_ids": copy.deepcopy(rec.get("shared_ids", {})), "kind": rec["kind"], "wavelength_m": rec["radiation"]["wavelength_m"],
            "two_theta_range_rad": list(rec["two_theta_range_rad"]),
            "reflections": [{"two_theta_rad": r["two_theta_rad"], "d_m": r["d_m"], "intensity_rel": r["intensity_rel"], "hkl": list(r["hkl"])} for r in rec["reflections"]],
            "measured_check": None if not mc else {"status": mc.get("status"), "n_matched": mc.get("n_matched") or 0, "n_checked": mc.get("n_checked") or 0},
            "template_source": prov["template_source"], "template_record_source": prov["template_record_source"], "evidence": prov["evidence"],
            "library_sha256": prov["library_sha256"], "synthetic": False, "limitations": list(LIMITATIONS)}


def pattern_result(inst: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    u, v, w = CAGLIOTI_DEG2
    points = [{k: p[k] for k in ("two_theta_rad", "intensity_cps", "expected_counts", "sample_time_s", "integration_time_s", "sample_window_s")}
              for p in inst["pattern"]]
    return {"samples": points, "status": inst["status"], "two_theta_coverage_rad": list(meta["coverage_rad"]), "wavelengths_m": [KA1_M, KA2_M],
            "profile_assumption": {"caglioti_u_deg2": u, "caglioti_v_deg2": v, "caglioti_w_deg2": w, "pseudo_voigt_eta": PV_ETA, "ka2_to_ka1_ratio": KA2_RATIO},
            "template_source": TEMPLATE_REL, "library_sha256": meta["library_sha256"],
            "evidence": "derived peak positions (RRUFF-refined / COD cells) and calculated random-powder intensities; RRUFF measured-profile confirmation per phase; assumed instrument profile",
            "synthetic": False, "synthetic_parts": list(SYNTHETIC_PARTS), "limitations": list(LIMITATIONS)}


def session_query(session, host, instance_id: str, inst: dict[str, Any], cap: str, args: Any, request_id: str) -> dict[str, Any]:
    """Read-only dispatch of the added agent queries."""
    if cap == "template.read":
        if not isinstance(args, dict) or set(args) != {"phase_id"} or not isinstance(args["phase_id"], str) or not 1 <= len(args["phase_id"]) <= 256:
            return session._error(instance_id, "INVALID_ARGUMENT", request_id)
        result = public_reference(args["phase_id"])
        if result is None:  # unknown library phase id
            return session._error(instance_id, "INVALID_ARGUMENT", request_id)
        return session._envelope(instance_id, request_id, result=result)
    if args != {}:
        return session._error(instance_id, "INVALID_ARGUMENT", request_id)
    meta = template_backed(inst)
    if meta is None:
        return session._error(instance_id, "PRECONDITION_FAILED", request_id)
    return session._envelope(instance_id, request_id, result=pattern_result(inst, meta))


def template_sample_truth(host, instance_id: str) -> dict[str, Any] | None:
    with host._lock:
        inst = host._instances[instance_id]
        meta = template_backed(inst)
        return copy.deepcopy(meta) if meta else None
