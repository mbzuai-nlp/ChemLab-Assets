"""Template-backed digestion path for the Anton Paar Multiwave 7101 (24 x 8 mL rack) thermal runtime.

Library: assets/reactions/multiwave_7101_digestion_library/templates.json (EPA SW-846 3051A/3052/3015A programs,
NIST SRM certified mass fractions, EPA 3051A measured recoveries, reagent properties, IAPWS water saturation curve).

Adds three agent capabilities to ``basic40_runtime_thermal.Multiwave7101Session`` (hooked in the runtime file):
* ``template.read``           public reference lookup by template id; reveals nothing about any loaded vessel.
* ``digestion.load_template`` declare the contents of one rack vessel (reference material id, sample mass, acid
                              volume, EPA method id); door open and heating off, vessel present in the slot.
* ``digestion.evaluate``      compare the host-tracked thermal history (indicated temperature, shared lumped node)
                              with the method program (window, ramp, hold) and list the analytes expected in the
                              digest from the certificate / EPA recovery tables.
The lumped thermal model itself is unchanged: loading template contents does not alter heat capacity or power.
The host step hook ``track_step`` integrates time-in-window exactly by bisection on the runtime's own closed-form
``_advance`` (the single-node trajectory is monotone within one host step).
"""
from __future__ import annotations

import copy
import json
import math
from functools import lru_cache
from typing import Any

try:  # scripts/ on sys.path (tests, Matterix runtime bridge)
    from template_library import index_by_id, library_sha256, load_library, provenance
except ImportError:  # imported as scripts.basic40_templates.*
    from scripts.template_library import index_by_id, library_sha256, load_library, provenance

TEMPLATE_REL = "assets/reactions/multiwave_7101_digestion_library/templates.json"
RACK24_FILL_LIMIT_M3 = 5e-6      # manufacturer brochure 2026 PDF p.5, Rack 24 column (contract source manufacturer_brochure_2026)
RACK24_SAMPLE_LIMIT_KG = 2e-4    # same table: 0.2 g sample for Rack 24
RACK24_VIAL_VOLUME_M3 = 8e-6
CAPABILITIES = ("template.read", "digestion.load_template", "digestion.evaluate")
STATE_KEY = "template_digestion"
SOLID_MATRIX_TO_METHOD = {  # library CRM matrix -> matrix words used in the EPA scope sections
    "soil": ("soils",), "marine_sediment": ("sediments",), "estuarine_sediment": ("sediments",), "river_sediment": ("sediments",),
    "plant_leaves": ("biological tissues",), "animal_tissue": ("biological tissues",), "oil": ("oils",),
}
RECOVERY_MATERIAL_MATRIX = {"nist_srm_2704": "river_sediment", "nist_srm_4355": "soil", "nist_srm_1084a": "oil"}
SYNTHETIC_PARTS = ["thermal history: shared lumped single-node surrogate of the Multiwave 7101 runtime (assumed absorbed power/UA)",
                   "digest volume assumed equal to the declared acid volume (sample water, evaporation and dilution not modelled)"]
LIMITATIONS = [
    "No digestion kinetics: an analyte is listed as expected only after the method hold time has been met in the method window; partial programs return null amounts.",
    "Certified values are total mass fractions; EPA 3051A/3015A are leaching methods. Expected 3051A amounts use the EPA 3051A Table 1-3 measured means where they exist, else the certificate CLP study median (USEPA 200.7/3050B hot-plate leach, a proxy), else null.",
    "Rack 24 (8 mL vials, 5 mL fill, 0.2 g sample) is a scale-down of EPA methods written for >=45 mL vessels and 0.5 g; vessel volume and sample mass deviations are reported, not hidden.",
    "Shared cavity temperature: all 24 vessels see the same simulated node; no per-vessel heating, pressure, venting or acid-vapour model; the water saturation curve is only a lower bound for vessel pressure.",
    "Elemental volatility (Hg, As, Se, B with HF), insoluble fluorides and incomplete matrix destruction are not modelled.",
]


# ---------------------------------------------------------------- library access ------------------------------------
@lru_cache(maxsize=None)
def records() -> dict[str, dict]:
    return index_by_id(TEMPLATE_REL)


@lru_cache(maxsize=None)
def materials() -> dict[str, dict]:
    """material_id -> {name, matrix, analytes: {symbol: crm record}, recoveries: {symbol: method_recovery record}}."""
    out: dict[str, dict] = {}
    for rec in load_library(TEMPLATE_REL)["templates"]:
        if rec["record_type"] == "crm_certified_value":
            m = out.setdefault(rec["material_id"], {"name": rec["material_name"], "matrix": rec["matrix"], "analytes": {}, "recoveries": {}})
            m["analytes"][rec["analyte"]] = rec
        elif rec["record_type"] == "method_recovery":
            m = out.setdefault(rec["material_id"], {"name": rec["material_name"], "matrix": RECOVERY_MATERIAL_MATRIX.get(rec["material_id"]),
                                                    "analytes": {}, "recoveries": {}})
            m["recoveries"][rec["analyte"]] = rec
    return out


def methods() -> dict[str, dict]:
    return {k: v for k, v in records().items() if v["record_type"] == "digestion_method"}


def _water_curve() -> dict | None:
    return records().get("water_saturation_pressure_373_573K")


def water_saturation_pressure_pa(temperature_k: float) -> float | None:
    """Linear interpolation in the cached IAPWS-95 (NIST WebBook) table, 373.15-573.15 K; None outside."""
    c = _water_curve()
    if not c:
        return None
    ts, ps = c["temperature_K"], c["saturation_pressure_Pa"]
    if not ts[0] <= temperature_k <= ts[-1]:
        return None
    for i in range(1, len(ts)):
        if temperature_k <= ts[i]:
            f = (temperature_k - ts[i - 1]) / (ts[i] - ts[i - 1])
            return ps[i - 1] + f * (ps[i] - ps[i - 1])
    return ps[-1]


# ---------------------------------------------------------------- schemas ------------------------------------------
def _obj(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": list(props) if required is None else required, "additionalProperties": False}


def _num(unit: str, minimum=None, maximum=None, nullable=False) -> dict:
    s: dict[str, Any] = {"type": ["number", "null"] if nullable else "number", "x-unit": unit}
    if minimum is not None:
        s["minimum"] = minimum
    if maximum is not None:
        s["maximum"] = maximum
    return s


TEXT = {"type": "string"}
NTEXT = {"type": ["string", "null"]}
BOOL = {"type": "boolean"}
NBOOL = {"type": ["boolean", "null"]}
SLOT = {"type": "integer", "minimum": 0, "maximum": 23, "x-unit": "1"}
ANALYTE = _obj({
    "analyte": TEXT, "value_category": TEXT,
    "certified_mass_fraction": _num("kg/kg", 0, 1, nullable=True), "expanded_uncertainty": _num("kg/kg", 0, 1, nullable=True),
    "basis": TEXT, "expected_mass_fraction": _num("kg/kg", 0, 1, nullable=True), "expected_mass_kg": _num("kg", 0, nullable=True),
    "expected_concentration_kg_m3": _num("kg/m^3", 0, nullable=True), "evidence": TEXT, "source": TEXT, "locator": TEXT, "note": NTEXT})
PROGRAM = _obj({
    "target_temperature_K": _num("K"), "temperature_tolerance_K": _num("K"), "window_low_K": _num("K"), "window_high_K": _num("K"),
    "setpoint_K": _num("K"), "setpoint_in_window": BOOL,
    "required_ramp_s": _num("s"), "ramp_tolerance_s": _num("s", nullable=True), "ramp_time_max_s": _num("s", nullable=True),
    "heating_started_s": _num("s", nullable=True), "entered_window_s": _num("s", nullable=True), "measured_ramp_s": _num("s", nullable=True),
    "ramp_within_spec": NBOOL, "required_hold_s": _num("s"), "hold_in_window_s": _num("s", 0), "left_window_after_entry": BOOL,
    "max_indicated_temperature_K": _num("K"), "exceeded_window": BOOL, "program_complete": BOOL,
    "water_saturation_pressure_at_target_Pa": _num("Pa", 0, nullable=True), "reagents_boiling_below_target": {"type": "array", "items": TEXT},
    "synthetic": BOOL})
EVALUATE_OUT = _obj({
    "slot_index": SLOT, "material_id": TEXT, "material_name": TEXT, "matrix": TEXT, "method_id": TEXT, "method_scope": TEXT,
    "matrix_in_method_scope": BOOL, "sample_mass_kg": _num("kg", 0), "acid_volume_m3": _num("m^3", 0),
    "acid_to_sample_ratio_m3_kg": _num("m^3/kg", 0), "method_acid_to_sample_ratio_m3_kg": _num("m^3/kg", 0, nullable=True),
    "method_deviations": {"type": "array", "items": TEXT}, "method_conformant": BOOL, "program": PROGRAM,
    "expected_analytes": {"type": "array", "items": ANALYTE}, "status": {"type": "string", "enum": ["complete", "incomplete_program"]},
    "template_source": TEXT, "library_sha256": TEXT, "evidence": TEXT, "synthetic": BOOL, "synthetic_parts": {"type": "array", "items": TEXT},
    "limitations": {"type": "array", "items": TEXT}})
READ_OUT = _obj({"template_id": TEXT, "record_type": TEXT, "name": TEXT, "cas": NTEXT, "evidence": TEXT, "source": TEXT,
                 "locator": NTEXT, "accessed": TEXT, "record_json": TEXT, "template_source": TEXT, "library_sha256": TEXT})
LOAD_IN = _obj({"slot_index": SLOT, "material_id": TEXT, "method_id": TEXT,
                "sample_mass_kg": _num("kg", 1e-6, RACK24_SAMPLE_LIMIT_KG), "acid_volume_m3": _num("m^3", 1e-7, RACK24_FILL_LIMIT_M3)})
LOAD_OUT = _obj({"slot_index": SLOT, "material_id": TEXT, "method_id": TEXT, "method_scope": TEXT, "sample_mass_kg": _num("kg"),
                 "acid_volume_m3": _num("m^3"), "method_deviations": {"type": "array", "items": TEXT}})

ERRORS = {
    "UNKNOWN_REFERENCE": "template_id, material_id or method_id is not in the digestion library.",
    "METHOD_MATRIX_MISMATCH": "Method is written for aqueous samples (EPA 3015A) but the reference material is a solid.",
    "TEMPLATE_SAMPLE_REQUIRED": "No template contents declared for this rack slot.",
}


def capability_specs() -> dict[str, dict]:
    """Runtime-metadata entries in the same shape as export_multiwave_7101_metadata() capabilities."""
    load_pre = [{"description_zh": "门打开且微波加热关闭（装样时）。", "state_ids": ["door.closed", "microwave.enabled"], "enforced_by": "adapter",
                 "check": "not door.closed and not microwave.enabled"},
                {"description_zh": "该槽位已放入消解管（rack.set_slot occupied=True）。", "state_ids": ["rack.occupied_slots"], "enforced_by": "adapter",
                 "check": "rack.occupied_slots[slot_index]"}]
    eval_pre = [{"description_zh": "该槽位已声明模板内容。", "state_ids": [], "enforced_by": "adapter", "check": "slot has digestion.load_template contents"}]
    common = {"availability": "implemented", "visibility": "agent", "scene_backend_required": False,
              "completion_criterion_zh": "同步返回；不推进宿主时钟，不改变热状态。"}
    return {
        "template.read": {**common, "kind": "query", "description_zh": "按模板ID读取公开参考记录（EPA方法程序、NIST SRM认证值、试剂性质）；不透露任何消解管内容。",
                          "component_ids": ["controller"], "input_schema": _obj({"template_id": TEXT}), "output_schema": READ_OUT,
                          "example_arguments": [{"template_id": "epa_3051a_hno3"}], "observes": [], "state_effects": [], "preconditions": [],
                          "binding": "basic40_runtime_thermal.Multiwave7101Session.invoke(instance_id=..., capability_id='template.read', arguments={'template_id': ...}, request_id=...)"},
        "digestion.load_template": {**common, "kind": "configuration", "description_zh": "声明某槽消解管的内容：标准物质ID、称样量、酸体积与EPA方法ID；只记录过程模板，不改热容或功率。",
                                    "component_ids": ["rack"], "input_schema": LOAD_IN, "output_schema": LOAD_OUT,
                                    "example_arguments": [{"slot_index": 0, "material_id": "nist_srm_2711a", "method_id": "epa_3051a_hno3", "sample_mass_kg": 0.0002, "acid_volume_m3": 4e-06}],
                                    "observes": [], "state_effects": [], "preconditions": load_pre,
                                    "binding": "basic40_runtime_thermal.Multiwave7101Session.invoke(instance_id=..., capability_id='digestion.load_template', arguments=..., request_id=...)"},
        "digestion.evaluate": {**common, "kind": "analysis", "description_zh": "对照方法温窗、升温与保持时间检查已运行的显示温度历史，并按证书/EPA回收率表列出消解液中预期的分析元素。",
                               "component_ids": ["controller", "rack"], "input_schema": _obj({"slot_index": SLOT}), "output_schema": EVALUATE_OUT,
                               "example_arguments": [{"slot_index": 0}], "observes": ["thermal.indicated_temperature_k", "thermal.target_k"], "state_effects": [],
                               "preconditions": eval_pre,
                               "binding": "basic40_runtime_thermal.Multiwave7101Session.invoke(instance_id=..., capability_id='digestion.evaluate', arguments={'slot_index': 0}, request_id=...)"},
    }


# ---------------------------------------------------------------- capability handlers -------------------------------
class Rejected(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _finite(x) -> bool:
    return type(x) in (int, float) and math.isfinite(x)


def template_read(args: dict) -> dict:
    tid = args.get("template_id")
    if not isinstance(tid, str):
        raise Rejected("INVALID_ARGUMENT")
    rec = records().get(tid)
    if rec is None:
        raise Rejected("UNKNOWN_REFERENCE")
    prov = provenance(TEMPLATE_REL, rec)
    return {"template_id": tid, "record_type": rec["record_type"], "name": rec.get("name") or tid, "cas": rec.get("cas"),
            "evidence": rec.get("evidence") or "", "source": rec.get("source") or "", "locator": rec.get("locator"),
            "accessed": rec.get("accessed") or "", "record_json": json.dumps(rec, ensure_ascii=False, sort_keys=True),
            "template_source": prov["template_source"], "library_sha256": prov["library_sha256"]}


def _method_volume(method: dict) -> float:
    return sum(r.get("volume_m3") or 0.0 for r in method["reagents"])


def _method_ratio(method: dict) -> float | None:
    mass = (method.get("max_sample_mass_kg") or {})
    solid = mass.get("soils_sediments_sludges") or mass.get("soils_ash_sediments_sludges_siliceous_wastes")
    return _method_volume(method) / solid if solid else None


def plan(args: dict) -> dict:
    """Validate digestion.load_template arguments; returns the slot record (no state is touched here)."""
    slot, mid, meth, mass, vol = (args.get(k) for k in ("slot_index", "material_id", "method_id", "sample_mass_kg", "acid_volume_m3"))
    if type(slot) is not int or not 0 <= slot <= 23 or not isinstance(mid, str) or not isinstance(meth, str):
        raise Rejected("INVALID_ARGUMENT")
    if not _finite(mass) or not 1e-6 <= mass <= RACK24_SAMPLE_LIMIT_KG or not _finite(vol) or not 1e-7 <= vol <= RACK24_FILL_LIMIT_M3:
        raise Rejected("INVALID_ARGUMENT")
    if mid not in materials() or meth not in methods():
        raise Rejected("UNKNOWN_REFERENCE")
    method, material = methods()[meth], materials()[mid]
    if method.get("sample_volume_m3") is not None:  # EPA 3015A: aqueous samples only
        raise Rejected("METHOD_MATRIX_MISMATCH")
    deviations = []
    min_vol = (method.get("vessel_requirements") or {}).get("min_internal_volume_m3")
    if min_vol and RACK24_VIAL_VOLUME_M3 < min_vol:
        deviations.append(f"vessel 8 mL below method minimum internal volume {min_vol * 1e6:g} mL (Rack 24 scale-down)")
    ratio, method_ratio = vol / mass, _method_ratio(method)
    if method_ratio and ratio < method_ratio * (1 - 1e-9):
        deviations.append(f"acid/sample ratio {ratio:.4g} m^3/kg below method {method_ratio:.4g} m^3/kg")
    scope_words = SOLID_MATRIX_TO_METHOD.get(material["matrix"], ())
    if not any(w in method.get("matrices", []) for w in scope_words):
        deviations.append(f"matrix '{material['matrix']}' not listed in {method.get('method', meth)} scope")
    return {"slot_index": slot, "material_id": mid, "method_id": meth, "sample_mass_kg": float(mass), "acid_volume_m3": float(vol),
            "method_deviations": deviations, "heating_started_s": None, "entered_window_s": None, "hold_in_window_s": 0.0,
            "left_window_after_entry": False, "max_indicated_temperature_K": None, "exceeded_window": False}


def load_result(rec: dict) -> dict:
    return {"slot_index": rec["slot_index"], "material_id": rec["material_id"], "method_id": rec["method_id"],
            "method_scope": methods()[rec["method_id"]]["scope"], "sample_mass_kg": rec["sample_mass_kg"],
            "acid_volume_m3": rec["acid_volume_m3"], "method_deviations": list(rec["method_deviations"])}


def _window(method: dict) -> tuple[float, float]:
    p = method["program"]
    return p["target_temperature_K"] - p["temperature_tolerance_K"], p["target_temperature_K"] + p["temperature_tolerance_K"]


def _expected(material: dict, method: dict, rec: dict, complete: bool) -> list[dict]:
    out = []
    mass, vol, scope, mid = rec["sample_mass_kg"], rec["acid_volume_m3"], method["scope"], rec["method_id"]
    symbols = sorted(set(material["analytes"]) | set(material["recoveries"]))
    for sym in symbols:
        crm, recov = material["analytes"].get(sym), material["recoveries"].get(sym)
        if crm is not None:
            total, unc, cat, src, loc = crm["value"], crm.get("expanded_uncertainty"), crm["value_category"], crm["source"], crm.get("locator") or ""
        else:
            t = recov["results"]["total"]
            total, unc, cat, src, loc = t["mean"], t.get("uncertainty"), t.get("value_category") or "certified", recov["source"], recov.get("locator") or ""
        frac, basis, evidence, note = None, "", "measured", None
        if scope == "total":
            frac, basis = total, "certified_total (method claims total decomposition)"
        elif recov is not None and mid in recov["results"]:
            frac, basis, evidence = recov["results"][mid]["mean"], f"EPA 3051A measured mean ({mid})", "measured_literature"
            src, loc = recov["source"], recov.get("locator") or ""
        elif crm is not None and crm.get("leachable"):
            key, leach = next(iter(crm["leachable"].items()))
            frac, basis, evidence = leach["median"], f"leachable proxy: {key} median", "measured_literature"
            note = "hot-plate CLP leach (USEPA 200.7/3050B), used as a proxy for microwave 3051A (3051A Sec. 1.1 mimics 3050)"
            loc = f"{loc}; {leach.get('source_locator', '')}"
        else:
            basis, note = "not_documented", "acid-leachable fraction not documented for this material/method; certified total is an upper bound"
        if not complete:
            note = "program incomplete: hold time in method window not reached" + (f"; {note}" if note else "")
        exp_frac = frac if complete else None
        out.append({"analyte": sym, "value_category": cat, "certified_mass_fraction": total, "expanded_uncertainty": unc, "basis": basis,
                    "expected_mass_fraction": exp_frac, "expected_mass_kg": None if exp_frac is None else exp_frac * mass,
                    "expected_concentration_kg_m3": None if exp_frac is None else exp_frac * mass / vol,
                    "evidence": evidence, "source": src, "locator": loc, "note": note})
    return out


def evaluate(rec: dict, state: dict) -> dict:
    method, material = methods()[rec["method_id"]], materials()[rec["material_id"]]
    p = method["program"]
    lo, hi = _window(method)
    complete = rec["hold_in_window_s"] >= p["hold_time_s"] - 1e-9
    ramp = None if rec["entered_window_s"] is None or rec["heating_started_s"] is None else rec["entered_window_s"] - rec["heating_started_s"]
    if ramp is None:
        ramp_ok = None
    elif p.get("ramp_tolerance_s") is not None:
        ramp_ok = abs(ramp - p["ramp_time_s"]) <= p["ramp_tolerance_s"] + 1e-9
    else:
        ramp_ok = ramp <= (p.get("ramp_time_max_s") or p["ramp_time_s"]) + 1e-9
    setpoint = float(state["thermal.target_k"])
    boiling = []
    for reag in method["reagents"]:
        r = records().get(reag["reagent_id"]) or {}
        bp = (r.get("values") or {}).get("azeotrope_boiling_point_K") or (r.get("values") or {}).get("boiling_point_K")
        if bp is not None and bp < p["target_temperature_K"]:
            boiling.append(f"{reag['reagent_id']} ({bp:.2f} K)")
    deviations = list(rec["method_deviations"])
    if not lo <= setpoint <= hi:
        deviations.append(f"setpoint {setpoint:.2f} K outside method window {lo:.2f}-{hi:.2f} K")
    if ramp_ok is False:
        deviations.append(f"ramp {ramp:.1f} s outside method {p['ramp_time_s']:.0f} s spec")
    if rec["exceeded_window"]:
        deviations.append("indicated temperature exceeded the method window")
    if rec["left_window_after_entry"]:
        deviations.append("temperature left the window after first entry (hold interrupted)")
    max_t = rec["max_indicated_temperature_K"] if rec["max_indicated_temperature_K"] is not None else float(state["thermal.indicated_temperature_k"])
    prov = provenance(TEMPLATE_REL, method)
    return {
        "slot_index": rec["slot_index"], "material_id": rec["material_id"], "material_name": material["name"], "matrix": material["matrix"] or "",
        "method_id": rec["method_id"], "method_scope": method["scope"],
        "matrix_in_method_scope": any(w in method.get("matrices", []) for w in SOLID_MATRIX_TO_METHOD.get(material["matrix"], ())),
        "sample_mass_kg": rec["sample_mass_kg"], "acid_volume_m3": rec["acid_volume_m3"],
        "acid_to_sample_ratio_m3_kg": rec["acid_volume_m3"] / rec["sample_mass_kg"], "method_acid_to_sample_ratio_m3_kg": _method_ratio(method),
        "method_deviations": deviations, "method_conformant": complete and not deviations,
        "program": {"target_temperature_K": p["target_temperature_K"], "temperature_tolerance_K": p["temperature_tolerance_K"], "window_low_K": lo,
                    "window_high_K": hi, "setpoint_K": setpoint, "setpoint_in_window": lo <= setpoint <= hi,
                    "required_ramp_s": p["ramp_time_s"], "ramp_tolerance_s": p.get("ramp_tolerance_s"), "ramp_time_max_s": p.get("ramp_time_max_s"),
                    "heating_started_s": rec["heating_started_s"], "entered_window_s": rec["entered_window_s"], "measured_ramp_s": ramp,
                    "ramp_within_spec": ramp_ok, "required_hold_s": p["hold_time_s"], "hold_in_window_s": rec["hold_in_window_s"],
                    "left_window_after_entry": rec["left_window_after_entry"], "max_indicated_temperature_K": max_t,
                    "exceeded_window": rec["exceeded_window"], "program_complete": complete,
                    "water_saturation_pressure_at_target_Pa": water_saturation_pressure_pa(p["target_temperature_K"]),
                    "reagents_boiling_below_target": boiling, "synthetic": True},
        "expected_analytes": _expected(material, method, rec, complete),
        "status": "complete" if complete else "incomplete_program",
        "template_source": TEMPLATE_REL, "library_sha256": prov["library_sha256"],
        "evidence": "measured (NIST SRM certificates) / measured_literature (EPA 3051A tables, CLP study, method programs); thermal history simulated",
        "synthetic": False, "synthetic_parts": list(SYNTHETIC_PARTS), "limitations": list(LIMITATIONS)}


def handle(cap: str, args: dict, state: dict, contents: dict | None) -> tuple[dict, dict | None]:
    """Return (result, new_contents_or_None). Raises Rejected with a stable code; never mutates its inputs."""
    if cap == "template.read":
        return template_read(args), None
    if cap == "digestion.load_template":
        if state["door.closed"] or state["microwave.enabled"]:
            raise Rejected("PRECONDITION_FAILED")
        rec = plan(args)
        if not state["rack.occupied_slots"][rec["slot_index"]]:
            raise Rejected("PRECONDITION_FAILED")
        new = copy.deepcopy(contents or {})
        new[str(rec["slot_index"])] = rec
        return load_result(rec), new
    if cap == "digestion.evaluate":
        slot = args.get("slot_index")
        if type(slot) is not int or not 0 <= slot <= 23:
            raise Rejected("INVALID_ARGUMENT")
        rec = (contents or {}).get(str(slot))
        if rec is None:
            raise Rejected("TEMPLATE_SAMPLE_REQUIRED")
        return evaluate(rec, state), None
    raise Rejected("UNKNOWN_CAPABILITY")


# ---------------------------------------------------------------- host step hook -----------------------------------
def _heating(state: dict) -> bool:
    return bool(state["power.enabled"] and state["microwave.enabled"] and state["door.closed"] and state["microwave.power_limit_w"] > 0)


def _crossing(advance, before: dict, dt: float, level: float, rising: bool) -> float:
    """Earliest h in [0, dt] with indicated T(h) >= level (rising) or <= level (falling); trajectory monotone."""
    def reached(h):
        if h <= 0:
            t = before["thermal.indicated_temperature_k"]
        else:
            s = copy.deepcopy(before)
            advance(s, h)
            t = s["thermal.indicated_temperature_k"]
        return t >= level if rising else t <= level
    lo, hi = 0.0, dt
    if reached(0.0):
        return 0.0
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        if reached(mid):
            hi = mid
        else:
            lo = mid
        if hi - lo <= 1e-9 * max(1.0, dt):
            break
    return hi


def track_step(advance, inst: dict, before: dict, t0: float, dt: float) -> None:
    """Update every loaded slot after one host step [t0, t0+dt] (state already advanced in inst['state'])."""
    contents = inst.get(STATE_KEY)
    if not contents or dt <= 0:
        return
    after = inst["state"]
    T0, T1 = before["thermal.indicated_temperature_k"], after["thermal.indicated_temperature_k"]
    heating = _heating(before)
    for rec in contents.values():
        method = methods()[rec["method_id"]]
        lo, hi = _window(method)
        hold_done_before = rec["hold_in_window_s"] >= method["program"]["hold_time_s"] - 1e-9
        if heating and rec["heating_started_s"] is None:
            rec["heating_started_s"] = t0
        if T1 >= T0:  # rising or flat
            if T1 < lo or T0 > hi:
                enter = exit_ = None
            else:
                enter = 0.0 if T0 >= lo else _crossing(advance, before, dt, lo, True)
                exit_ = dt if T1 <= hi else _crossing(advance, before, dt, hi + 1e-9, True)
        else:  # falling
            if T0 < lo or T1 > hi:
                enter = exit_ = None
            else:
                enter = 0.0 if T0 <= hi else _crossing(advance, before, dt, hi, False)
                exit_ = dt if T1 >= lo else _crossing(advance, before, dt, lo - 1e-9, False)
        inside = 0.0 if enter is None else max(0.0, exit_ - enter)
        if inside > 0:
            if rec["entered_window_s"] is None:
                rec["entered_window_s"] = t0 + enter
            rec["hold_in_window_s"] += inside
        hold_done = rec["hold_in_window_s"] >= method["program"]["hold_time_s"] - 1e-9
        if rec["entered_window_s"] is not None and (T1 < lo or T1 > hi) and not (hold_done_before or (hold_done and T1 < lo)):
            rec["left_window_after_entry"] = True  # left before the hold was complete (normal cool-down afterwards is not flagged)
        peak = max(T0, T1)
        rec["max_indicated_temperature_K"] = peak if rec["max_indicated_temperature_K"] is None else max(rec["max_indicated_temperature_K"], peak)
        if peak > hi:
            rec["exceeded_window"] = True
