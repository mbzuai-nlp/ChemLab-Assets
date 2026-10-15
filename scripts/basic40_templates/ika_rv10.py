"""CHEM-024 IKA RV 10 digital V: template-backed solvent evaporation (real vapour-pressure data).

Library: ``assets/reactions/ika_rv10_evaporation_library/templates.json`` (81 solvents; NIST WebBook Antoine
fits with validity ranges, NIST Tboil and enthalpy of vaporization, liquid density from the NIST SRD 69
reference EOS or PubChem experimental properties).

Two layers are kept strictly apart in every result:

* real / derived-from-real (``evidence`` measured_literature or derived): saturation pressure at the bath
  temperature and boiling temperature at the external vacuum pressure from the NIST Antoine fit
  (``log10(P/bar) = A - B/(T + C)``, evaluated only inside the published range), enthalpy of vaporization
  (NIST point nearest to the boiling temperature) and the charge mass ``rho * V``.
* assumption (``synthetic`` list): the overall bath-to-flask heat-transfer coefficient ``UA`` [W/K] and the
  condensation efficiency supplied by the trusted host, the boiling-only evaporation law
  ``m_dot = min(UA * (T_bath - T_boil(P)), 1350 W) / dH_vap`` (no sub-boiling evaporation, no bath cooling by
  the evaporative load, explicit Euler at the start of each host step), and the delta-20 rule of thumb.

The agent-visible ``template.read`` capability is a public reference lookup and never reveals which solvent the
trusted host placed in the flask.  The charge identity and rates are only in the evaluator-only
``evaporation.template_report`` and the trusted ``RV10Host.template_charge_report``.
"""
from __future__ import annotations

import copy
import math

try:  # scripts/ on sys.path (tests, Matterix bridge)
    import template_library as _tl
except ImportError:  # project root on sys.path
    from scripts import template_library as _tl

TEMPLATE_REL = "assets/reactions/ika_rv10_evaporation_library/templates.json"
RATED_HEATER_W = 1350.0
DELTA20_K = 20.0
CAPABILITY_IDS = ("template.read", "evaporation.template_report")
EVALUATOR_CAPABILITIES = ("evaporation.template_report",)

LIMITATIONS = [
    "Vapour pressure and boiling temperature come from NIST WebBook Antoine fits evaluated only inside each fit's published temperature range; outside every range the result is null (no extrapolation).",
    "Pure solvents only: no mixtures, azeotropes, dissolved solutes, boiling-point elevation or foaming/bumping.",
    "Density is a single literature/EOS value near 293 K; charge mass rho*V and condensate volume ignore thermal expansion.",
    "Evaporation rate uses an assumed host-supplied heat-transfer coefficient UA and only the boiling regime (bath above T_boil at the external pressure); sub-boiling evaporation is set to zero.",
    "The evaporative heat load is capped at the 1350 W rated heater power but is not withdrawn from the lumped bath model; condenser capacity, vapour-line pressure drop and coolant temperature are not modelled.",
    "The delta-20 rule (bath ~ T_boil + 20 K, condenser <= T_boil - 20 K) is a practical rule of thumb, reported as an assumption, not data.",
]
SYNTHETIC = [
    "heat_transfer_w_k: host-supplied overall bath-to-flask UA (assumption)",
    "condensation_efficiency: host-supplied fraction (assumption)",
    "evaporation law: boiling-only m_dot = min(UA*(T_bath - T_boil(P)), 1350 W)/dH_vap, explicit Euler per host step (assumption)",
    "delta-20 rule of thumb (assumption)",
]
EVIDENCE = "measured_literature (NIST Antoine/Tboil/dHvap, NIST EOS or PubChem density); boiling temperature, saturation pressure and charge mass derived from them; rate law assumption"

_K = {"type": "number", "x-unit": "K"}
_NULL_K = {"anyOf": [{"type": "number", "x-unit": "K"}, {"type": "null"}]}
_NULL_PA = {"anyOf": [{"type": "number", "minimum": 0, "x-unit": "Pa"}, {"type": "null"}]}
_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": {"type": "string"}}


def _obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


_FIT = _obj({"fit_id": _TEXT, "A": {"type": "number", "x-unit": "1"}, "B_k": {"type": "number", "x-unit": "K"}, "C_k": {"type": "number", "x-unit": "K"},
             "temperature_range_k": {"type": "array", "items": {"type": "number", "x-unit": "K"}, "minItems": 2, "maxItems": 2}, "reference": _TEXT})
_DH = _obj({"value_j_mol": {"type": "number", "x-unit": "J/mol"}, "temperature_k": {"type": "number", "x-unit": "K"}, "reference": _TEXT, "source_kind": _TEXT})
TEMPLATE_SCHEMA = _obj({
    "substance_id": _TEXT, "name": _TEXT, "cas": _nullable(_TEXT),
    "molar_mass_kg_mol": _nullable({"type": "number", "x-unit": "kg/mol"}),
    "normal_boiling_point_k": _NULL_K, "melting_point_k": _NULL_K,
    "density_kg_m3": _nullable({"type": "number", "x-unit": "kg/m^3"}), "density_temperature_k": _NULL_K,
    "antoine_fits": {"type": "array", "items": _FIT}, "vaporization_enthalpy_points": {"type": "array", "items": _DH},
    "runtime_usable": {"type": "boolean"}, "quality_flags": _TEXTS, "source": _TEXT, "evidence": _TEXT,
})
_RULE = _obj({"evidence": {"type": "string", "const": "assumption"}, "statement": _TEXT,
              "recommended_bath_temperature_k": _NULL_K, "recommended_condenser_max_temperature_k": _NULL_K})
TEMPLATE_READ_OUTPUT = _obj({
    "template": TEMPLATE_SCHEMA,
    "boiling_temperature_at_pressure_k": _NULL_K, "boiling_fit_id": _nullable(_TEXT),
    "pressure_for_boiling_temperature_pa": _NULL_PA, "pressure_fit_id": _nullable(_TEXT),
    "rule_of_thumb": _RULE, "template_source": _TEXT, "library_sha256": _TEXT, "evidence": _TEXT,
    "synthetic": {"type": "boolean"}, "limitations": _TEXTS,
})
TEMPLATE_READ_INPUT = _obj({
    "substance_id": {"type": "string", "description": "Library substance_id, e.g. ethanol"},
    "pressure_pa": {"anyOf": [{"type": "number", "exclusiveMinimum": 0, "maximum": 110000, "x-unit": "Pa"}, {"type": "null"}],
                    "description": "Optional absolute pressure; returns the Antoine boiling temperature at this pressure"},
    "boiling_temperature_k": {"anyOf": [{"type": "number", "exclusiveMinimum": 0, "maximum": 453.15, "x-unit": "K"}, {"type": "null"}],
                              "description": "Optional target boiling temperature; returns the absolute pressure needed"},
})
REPORT_OUTPUT = _obj({
    "template_backed": {"type": "boolean"}, "solvent_id": _nullable(_TEXT),
    "bath_temperature_k": _NULL_K, "pressure_pa": _NULL_PA,
    "boiling_temperature_at_pressure_k": _NULL_K, "saturation_pressure_at_bath_pa": _NULL_PA, "antoine_fit_id": _nullable(_TEXT),
    "boiling": _nullable({"type": "boolean"}), "regime": _TEXT,
    "vaporization_enthalpy_j_mol": _nullable({"type": "number", "x-unit": "J/mol"}), "vaporization_enthalpy_temperature_k": _NULL_K,
    "heat_transfer_w_k": _nullable({"type": "number", "minimum": 0, "x-unit": "W/K"}), "heat_flow_w": _nullable({"type": "number", "minimum": 0, "x-unit": "W"}),
    "evaporation_rate_kg_s": _nullable({"type": "number", "minimum": 0, "x-unit": "kg/s"}),
    "remaining_mass_kg": _nullable({"type": "number", "minimum": 0, "x-unit": "kg"}), "remaining_volume_m3": _nullable({"type": "number", "minimum": 0, "x-unit": "m^3"}),
    "condensate_mass_kg": _nullable({"type": "number", "minimum": 0, "x-unit": "kg"}), "condensate_volume_m3": _nullable({"type": "number", "minimum": 0, "x-unit": "m^3"}),
    "rule_of_thumb": _RULE, "template_source": _nullable(_TEXT), "library_sha256": _nullable(_TEXT), "evidence": _TEXT,
    "synthetic": {"type": "boolean"}, "synthetic_components": _TEXTS, "quality_flags": _TEXTS, "limitations": _TEXTS,
})


# ----------------------------------------------------------------------------- library access
def library():
    return _tl.load_library(TEMPLATE_REL)


def record(substance_id):
    return _tl.get_template(TEMPLATE_REL, substance_id) if isinstance(substance_id, str) else None


def substance_ids(*, runtime_usable_only=False):
    return [r["substance_id"] for r in library()["templates"] if r["runtime_usable"] or not runtime_usable_only]


def _in_range(fit, t_k):
    lo, hi = fit["temperature_range_k"]
    return lo <= t_k <= hi


def saturation_pressure_pa(rec, t_k):
    """NIST Antoine P(T) with the first published fit whose range contains T; (None, None) outside all ranges."""
    if t_k is None or not math.isfinite(t_k):
        return None, None
    for fit in rec["antoine_fits"]:
        if _in_range(fit, t_k):
            return 1e5 * 10 ** (fit["A"] - fit["B_k"] / (t_k + fit["C_k"])), fit["fit_id"]
    return None, None


def boiling_temperature_k(rec, pressure_pa):
    """Inverse Antoine T(P) = B/(A - log10(P/bar)) - C, accepted only if T lies inside that fit's range."""
    if pressure_pa is None or not math.isfinite(pressure_pa) or pressure_pa <= 0:
        return None, None
    x = math.log10(pressure_pa / 1e5)
    for fit in rec["antoine_fits"]:
        if fit["A"] - x <= 0:
            continue
        t_k = fit["B_k"] / (fit["A"] - x) - fit["C_k"]
        if _in_range(fit, t_k):
            return t_k, fit["fit_id"]
    return None, None


def vaporization_enthalpy(rec, t_k):
    """NIST enthalpy-of-vaporization point whose temperature is nearest to t_k (first one on ties)."""
    points = rec["vaporization_enthalpy_points"]
    if not points or t_k is None:
        return None
    return min(points, key=lambda p: abs(p["temperature_k"] - t_k))


def rule_of_thumb(t_boil):
    return {"evidence": "assumption", "statement": library()["rule_of_thumb"]["statement"],
            "recommended_bath_temperature_k": None if t_boil is None else t_boil + DELTA20_K,
            "recommended_condenser_max_temperature_k": None if t_boil is None else t_boil - DELTA20_K}


def public_template(rec):
    return {"substance_id": rec["substance_id"], "name": rec["name"], "cas": rec["cas"], "molar_mass_kg_mol": rec["molar_mass_kg_mol"],
            "normal_boiling_point_k": rec["normal_boiling_point_k"], "melting_point_k": rec["melting_point_k"],
            "density_kg_m3": rec["density_kg_m3"], "density_temperature_k": rec["density_temperature_k"],
            "antoine_fits": [{k: f[k] for k in ("fit_id", "A", "B_k", "C_k", "temperature_range_k", "reference")} for f in rec["antoine_fits"]],
            "vaporization_enthalpy_points": [{"value_j_mol": p["value_j_mol"], "temperature_k": p["temperature_k"], "reference": p["reference"],
                                              "source_kind": p.get("source_kind", "nist_phase_change_one_dimensional")} for p in rec["vaporization_enthalpy_points"]],
            "runtime_usable": rec["runtime_usable"], "quality_flags": list(rec["quality_flags"]), "source": rec["source"], "evidence": rec["evidence"]}


# ----------------------------------------------------------------------------- capabilities
def capabilities(*, include_evaluator):
    caps = {
        "template.read": {
            "availability": "implemented", "visibility": "agent", "kind": "query",
            "description_zh": "公开参考查询：按 substance_id 返回溶剂的 NIST Antoine 拟合（含适用温区）、常压沸点、汽化焓与密度；可选给定压力求沸点、给定沸点求所需压力；不透露烧瓶内实际物质",
            "input_schema": copy.deepcopy(TEMPLATE_READ_INPUT), "output_schema": copy.deepcopy(TEMPLATE_READ_OUTPUT),
            "state_effects": [], "observes": [], "example_arguments": [{"substance_id": "ethanol", "pressure_pa": 17500.0, "boiling_temperature_k": None}],
            "binding": "basic40_runtime_vacuum.RV10Session.invoke(instance_id=..., capability_id='template.read', arguments=..., request_id=...)"},
    }
    if include_evaluator:
        caps["evaporation.template_report"] = {
            "availability": "implemented", "visibility": "evaluator", "kind": "query",
            "description_zh": "评估者查询：宿主模板装料的溶剂、外部压力下的沸点、浴温下的蒸气压、是否沸腾及假设传热系数给出的蒸发速率；真实数据与假设分列",
            "input_schema": _obj({}), "output_schema": copy.deepcopy(REPORT_OUTPUT),
            "state_effects": [], "observes": [], "example_arguments": [{}],
            "binding": "basic40_runtime_vacuum.RV10Session.invoke(instance_id=..., capability_id='evaporation.template_report', arguments={}, request_id=...)"}
    return caps


def _valid_number(value, low=None, high=None, exclusive_low=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    return (low is None or value >= low) and (high is None or value <= high) and (exclusive_low is None or value > exclusive_low)


def template_read(args):
    """Return (result, None) or (None, error_code)."""
    if not isinstance(args, dict) or set(args) != {"substance_id", "pressure_pa", "boiling_temperature_k"} or not isinstance(args["substance_id"], str):
        return None, "INVALID_ARGUMENT"
    p, t = args["pressure_pa"], args["boiling_temperature_k"]
    if (p is not None and not _valid_number(p, high=110000, exclusive_low=0)) or (t is not None and not _valid_number(t, high=453.15, exclusive_low=0)):
        return None, "INVALID_ARGUMENT"
    rec = record(args["substance_id"])
    if rec is None:
        return None, "UNKNOWN_REFERENCE"
    t_boil, boil_fit = boiling_temperature_k(rec, p) if p is not None else (None, None)
    p_need, p_fit = saturation_pressure_pa(rec, t) if t is not None else (None, None)
    prov = _tl.provenance(TEMPLATE_REL, rec)
    return {"template": public_template(rec), "boiling_temperature_at_pressure_k": t_boil, "boiling_fit_id": boil_fit,
            "pressure_for_boiling_temperature_pa": p_need, "pressure_fit_id": p_fit,
            "rule_of_thumb": rule_of_thumb(t_boil if t_boil is not None else t),
            "template_source": prov["template_source"], "library_sha256": prov["library_sha256"],
            "evidence": "measured_literature record; boiling temperature / pressure derived from the cited Antoine fit (null outside its range); rule of thumb is an assumption",
            "synthetic": False, "limitations": list(LIMITATIONS)}, None


# ----------------------------------------------------------------------------- template-backed charge
def configure_charge(host, instance_id, *, solvent_id, volume_m3, condensation_efficiency, heat_transfer_w_k, evidence):
    """Trusted host: fill the flask with a library solvent (mass = rho*V).  Mirrors RV10Host.configure_evaporation."""
    with host._lock:
        inst = host._instances[instance_id]
        if inst.state["rotation.running"] or inst.state["thermal.enabled"]:
            raise ValueError("Stop rotation and heating before configuring a charge")
        rec = record(solvent_id)
        if rec is None or not rec["runtime_usable"]:
            raise ValueError("solvent_id must name a runtime-usable record of " + TEMPLATE_REL)
        if not _valid_number(volume_m3, high=3e-3, exclusive_low=0) or not _valid_number(condensation_efficiency, 0, 1) \
                or not _valid_number(heat_transfer_w_k, 0, 1e4) or not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("Positive volume <= 3e-3 m3, efficiency in [0,1], UA in [0,1e4] W/K and evidence required")
        mass = rec["density_kg_m3"] * float(volume_m3)
        prov = _tl.provenance(TEMPLATE_REL, rec)
        inst.evaporation = {"charge_mass_kg": mass, "evaporation_rate_kg_s": 0.0, "condensation_efficiency": float(condensation_efficiency), "configured_at_s": host.time_s,
                            "template": {"solvent_id": solvent_id, "volume_m3": float(volume_m3), "density_kg_m3": rec["density_kg_m3"],
                                         "heat_transfer_w_k": float(heat_transfer_w_k), "template_source": prov["template_source"], "library_sha256": prov["library_sha256"]}}
        inst.configuration_evidence["evaporation"] = evidence
        inst.state.update({"evaporation.remaining_mass_kg": mass, "evaporation.evaporated_mass_kg": 0.0, "evaporation.uncondensed_mass_kg": 0.0, "recovery.condensate_mass_kg": 0.0})
        host._refresh(inst)
        inst.revision += 1
        return mass


def _assess(template, state):
    rec = record(template["solvent_id"])
    t_bath, p = state["thermal.temperature_k"], state["vacuum.pressure_pa"]
    t_boil, fit_id = boiling_temperature_k(rec, p)
    p_sat, _ = saturation_pressure_pa(rec, t_bath)
    out = {"rec": rec, "t_bath": t_bath, "p": p, "t_boil": t_boil, "fit_id": fit_id, "p_sat": p_sat, "boiling": None, "regime": "unknown", "dh": None, "q": None, "rate": 0.0}
    if t_bath is None or p is None:
        out["regime"] = "missing_bath_or_pressure"
        return out
    if t_boil is None:
        out["regime"] = "pressure_outside_antoine_range"
        return out
    if rec["melting_point_k"] is not None and t_bath <= rec["melting_point_k"]:
        out.update(boiling=False, regime="bath_below_melting_point")
        return out
    if t_bath <= t_boil:
        out.update(boiling=False, regime="sub_boiling_neglected")
        return out
    dh = vaporization_enthalpy(rec, t_boil)
    q = min(template["heat_transfer_w_k"] * (t_bath - t_boil), RATED_HEATER_W)
    rate = q / (dh["value_j_mol"] / rec["molar_mass_kg_mol"]) if dh and rec["molar_mass_kg_mol"] else 0.0
    out.update(boiling=True, regime="boiling", dh=dh, q=q, rate=rate)
    return out


def current_rate_kg_s(inst):
    """Rate used by RV10Host._refresh for a template charge; 0 unless all RV10 run conditions and boiling hold."""
    s = inst.state
    if not (s["power.enabled"] and s["rotation.running"] and s["thermal.enabled"] and inst.bath and s["vacuum.source_instance_id"] is not None):
        return 0.0
    return _assess(inst.evaporation["template"], s)["rate"]


def report(inst):
    ev = inst.evaporation
    if not ev or "template" not in ev:
        return {"template_backed": False, "solvent_id": None, "bath_temperature_k": inst.state["thermal.temperature_k"], "pressure_pa": inst.state["vacuum.pressure_pa"],
                "boiling_temperature_at_pressure_k": None, "saturation_pressure_at_bath_pa": None, "antoine_fit_id": None, "boiling": None,
                "regime": "no_template_charge", "vaporization_enthalpy_j_mol": None, "vaporization_enthalpy_temperature_k": None,
                "heat_transfer_w_k": None, "heat_flow_w": None, "evaporation_rate_kg_s": None, "remaining_mass_kg": inst.state["evaporation.remaining_mass_kg"],
                "remaining_volume_m3": None, "condensate_mass_kg": inst.state["recovery.condensate_mass_kg"], "condensate_volume_m3": None,
                "rule_of_thumb": {"evidence": "assumption", "statement": library()["rule_of_thumb"]["statement"], "recommended_bath_temperature_k": None, "recommended_condenser_max_temperature_k": None},
                "template_source": None, "library_sha256": None, "evidence": "legacy empirical charge or none: no template data used",
                "synthetic": True, "synthetic_components": ["legacy host-supplied constant evaporation rate"], "quality_flags": [], "limitations": list(LIMITATIONS)}
    tpl, s = ev["template"], inst.state
    a = _assess(tpl, s)
    rho = tpl["density_kg_m3"]
    running = bool(s["evaporation.running"])
    regime = a["regime"]
    if s["evaporation.remaining_mass_kg"] <= 0:
        regime = "charge_exhausted"
    elif regime == "boiling" and not running:
        regime = "boiling_possible_but_rv10_not_running"
    return {"template_backed": True, "solvent_id": tpl["solvent_id"], "bath_temperature_k": a["t_bath"], "pressure_pa": a["p"],
            "boiling_temperature_at_pressure_k": a["t_boil"], "saturation_pressure_at_bath_pa": a["p_sat"], "antoine_fit_id": a["fit_id"],
            "boiling": a["boiling"] if s["evaporation.remaining_mass_kg"] > 0 else False, "regime": regime,
            "vaporization_enthalpy_j_mol": None if a["dh"] is None else a["dh"]["value_j_mol"], "vaporization_enthalpy_temperature_k": None if a["dh"] is None else a["dh"]["temperature_k"],
            "heat_transfer_w_k": tpl["heat_transfer_w_k"], "heat_flow_w": a["q"] if running else (0.0 if a["q"] is not None else None),
            "evaporation_rate_kg_s": current_rate_kg_s(inst) if running else 0.0, "remaining_mass_kg": s["evaporation.remaining_mass_kg"],
            "remaining_volume_m3": s["evaporation.remaining_mass_kg"] / rho, "condensate_mass_kg": s["recovery.condensate_mass_kg"],
            "condensate_volume_m3": s["recovery.condensate_mass_kg"] / rho,
            "rule_of_thumb": rule_of_thumb(a["t_boil"]), "template_source": tpl["template_source"], "library_sha256": tpl["library_sha256"],
            "evidence": EVIDENCE, "synthetic": False, "synthetic_components": list(SYNTHETIC), "quality_flags": list(a["rec"]["quality_flags"]), "limitations": list(LIMITATIONS)}
