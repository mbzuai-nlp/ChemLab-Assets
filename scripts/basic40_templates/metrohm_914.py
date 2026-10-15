"""Metrohm 914 pH/DO/conductometer: template-backed solutions from public standards and the shared pKa table.

Libraries:
* ``assets/reactions/metrohm_914_standards_library/templates.json`` — NIST primary pH buffers pH(S)(T) (certificates),
  electrolytic-conductivity reference solutions κ(T), and the Benson–Krause dissolved-oxygen saturation model used
  by USGS DOTABLES;
* ``assets/reactions/metrohm_omnis_acidbase_library/templates.json`` — stepwise pKa(T) and pKw(T) (stored once,
  shared with the OMNIS titrator) for the pH of an arbitrary weak acid/base solution (ideal charge balance).

The trusted host turns a solution description into the complete explicit truth sample of the legacy
``Metrohm914Runtime.host_step`` (``host_template_sample``) or does both in one call (``host_step_template``).
Calibration standards carry the real certificate value at the sample temperature, so ``electrochemistry.calibrate``
must be called with that value (obtainable from ``template.read`` with the same temperature).
"""
from __future__ import annotations

import copy
import math
from typing import Any

from . import _acidbase as ab
from ._echem_common import (BOOL, LN10, T_REF, TEXT, arr, get_template, index_by_id, interp_linear, matches, nullable, num,
                            number, obj, provenance, public_record)

TEMPLATE_REL = "assets/reactions/metrohm_914_standards_library/templates.json"
ACIDBASE_REL = ab.ACIDBASE_REL
TEMPLATE_CAPABILITY_IDS = ("template.list", "template.read", "electrochemistry.template_reading")
CHANNEL_KEY = {"ph": "ph", "conductance": "conductance_s", "do": "do_kg_m3"}
LIMITATIONS = [
    "NIST pH(S) values are certified for the stated buffer compositions only, interpolated linearly between certificate temperatures (no extrapolation).",
    "Weak acid/base solution pH uses the shared pKa table with an ideal-solution charge balance (activity coefficients = 1).",
    "Conductance = κ(T) / cell constant; the cell constant is a host-supplied probe parameter (not invented); no conductance is computed for arbitrary solutions.",
    "Dissolved oxygen comes from the Benson–Krause saturation equations times a host-supplied saturation fraction (assumption, default none).",
    "Electrode response, drift, junction potential and DO probe dynamics remain the legacy engineering assumptions (ideal Nernst, instantaneous).",
]


def _records() -> dict[str, dict[str, Any]]:
    return index_by_id(TEMPLATE_REL)


def standard(substance_id: str) -> dict[str, Any] | None:
    return get_template(TEMPLATE_REL, substance_id)


def buffer_ph(rec: dict[str, Any], temperature_k: float) -> float:
    return interp_linear(rec["temperature_k"], rec["ph_s"], float(temperature_k))


def conductivity_s_m(rec: dict[str, Any], temperature_k: float) -> float:
    """κ(T): linear interpolation in the tabulated range; single-temperature NIST lots use their certified linear
    coefficient κ = κ25[1 + α(t − 25 °C)] only inside the certificate's stated validity window."""
    temps, values = rec["temperature_k"], rec["conductivity_s_m"]
    pairs = [(t, v) for t, v in zip(temps, values) if number(t) and number(v)]
    t = float(temperature_k)
    coefficient = rec.get("temperature_coefficient") or {}
    window = coefficient.get("valid_range_k")
    if len(pairs) == 1 and number(coefficient.get("alpha_per_k")) and isinstance(window, list) and window[0] <= t <= window[1]:
        t_ref = float(coefficient.get("reference_temperature_k", pairs[0][0]))
        return pairs[0][1] * (1.0 + float(coefficient["alpha_per_k"]) * (t - t_ref))
    return interp_linear([p[0] for p in pairs], [p[1] for p in pairs], t)


def do_model_record() -> dict[str, Any]:
    for rec in _records().values():
        if rec.get("record_type") == "dissolved_oxygen_model":
            return rec
    raise KeyError("dissolved oxygen model missing")


def do_saturation_kg_m3(temperature_k: float, pressure_pa: float = 101325.0, salinity: float = 0.0) -> float:
    """Benson & Krause (1984) air-saturated DO as used by USGS DOTABLES (coefficients from the library record).

    DO[mg/L] = DO0*FS*FP; DO0 = exp(a0 + a1/T + a2/T^2 + a3/T^3 + a4/T^4); FS = exp(-S*(b0 + b1/T + b2/T^2));
    FP = (P-u)(1-theta0*P)/((1-u)(1-theta0)), theta0 = c0 + c1*t + c2*t^2, u = exp(d0 + d1/T + d2/T^2), P,u in atm.
    """
    rec = do_model_record()
    c = rec["coefficients"]
    t = float(temperature_k)
    valid = rec.get("valid_range") or {}
    for value, key in ((t, "temperature_k"), (float(pressure_pa), "pressure_pa"), (float(salinity), "salinity_permil")):
        lo, hi = valid.get(key, [-math.inf, math.inf])
        if not lo <= value <= hi:
            raise ValueError(f"{key} outside the DO model range")
    a, b, th, d = c["do0_ln_mg_l"], c["salinity_factor"], c["theta0"], c["water_vapour_pressure_ln_atm"]
    do0 = math.exp(a["a0"] + a["a1_K"] / t + a["a2_K2"] / t ** 2 + a["a3_K3"] / t ** 3 + a["a4_K4"] / t ** 4)
    fs = math.exp(-float(salinity) * (b["b0"] + b["b1_K"] / t + b["b2_K2"] / t ** 2))
    tc = t - 273.15
    theta = th["c0"] + th["c1_per_degC"] * tc + th["c2_per_degC2"] * tc ** 2
    u = math.exp(d["d0"] + d["d1_K"] / t + d["d2_K2"] / t ** 2)
    p_atm = float(pressure_pa) / 101325.0
    fp = (p_atm - u) * (1 - theta * p_atm) / ((1 - u) * (1 - theta))
    return do0 * fs * fp * 1e-3  # mg/L -> kg/m3


SOLUTION_SCHEMA = obj({
    "kind": {"type": "string", "enum": ["ph_buffer", "conductivity_standard", "solution", "water"]},
    "substance_id": nullable({"type": "string", "minLength": 1, "maxLength": 128}),
    "components": arr(obj({"substance_id": {"type": "string", "minLength": 1, "maxLength": 128},
                           "concentration_mol_m3": num("mol/m^3", 0, 1e4),
                           "protons_as_added": nullable({"type": "integer", "minimum": 0, "maximum": 8, "x-unit": "1"})},
                          required=["substance_id", "concentration_mol_m3"]), 16),
    "strong_electrolytes": arr(obj({"substance_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                    "concentration_mol_m3": num("mol/m^3", 0, 1e4)}), 8),
    "temperature_k": num("K", 273.15, 368.15),
    "cell_constant_m_inv": nullable(num("1/m", exclusive_minimum=0, maximum=1e5)),
    "do_saturation_fraction": nullable(num("1", 0, 3)),
    "pressure_pa": num("Pa", 5e4, 1.2e5), "salinity": num("1", 0, 40),
    "electrode_isopotential_ph": num("1", 0, 14), "electrode_isopotential_v": num("V", -1, 1),
    "standard_channel": nullable({"type": "string", "enum": ["ph", "conductance", "do"]}),
}, required=["kind", "temperature_k"])
SOLUTION_DEFAULTS = {"substance_id": None, "components": [], "strong_electrolytes": [], "cell_constant_m_inv": None,
                     "do_saturation_fraction": None, "pressure_pa": 101325.0, "salinity": 0.0,
                     "electrode_isopotential_ph": 7.0, "electrode_isopotential_v": 0.0, "standard_channel": None}


def build_sample(solution: dict[str, Any]) -> dict[str, Any]:
    """Complete explicit legacy truth sample + provenance from a template solution description."""
    if not isinstance(solution, dict) or not matches(SOLUTION_SCHEMA, solution):
        raise ValueError("solution does not match the template solution schema")
    spec = {**SOLUTION_DEFAULTS, **copy.deepcopy(solution)}
    t = float(spec["temperature_k"])
    values = {"ph": None, "conductance_s": None, "do_kg_m3": None}
    evidence, sources = {}, {}
    kind = spec["kind"]
    if kind in ("ph_buffer", "conductivity_standard"):
        rec = standard(spec["substance_id"] or "")
        if rec is None or rec.get("record_type") != kind:
            raise ValueError("unknown standard for this kind")
        if spec["components"] or spec["strong_electrolytes"]:
            raise ValueError("standards are fixed compositions")
        if kind == "ph_buffer":
            values["ph"] = buffer_ph(rec, t)
            evidence["ph"] = rec.get("evidence")
            sources["ph"] = provenance(TEMPLATE_REL, rec)
        else:
            if spec["cell_constant_m_inv"] is None:
                raise ValueError("cell constant required to turn conductivity into conductance")
            values["conductance_s"] = conductivity_s_m(rec, t) / spec["cell_constant_m_inv"]
            evidence["conductance"] = rec.get("evidence")
            sources["conductance"] = provenance(TEMPLATE_REL, rec)
    elif kind == "solution":
        comps = []
        for comp in spec["components"]:
            rec = ab.record(comp["substance_id"])
            if rec is None or rec.get("record_type") != "acid_base":
                raise ValueError("unknown acid-base component")
            protons = comp.get("protons_as_added")
            protons = int(rec["protons_in_named_form"]) if protons is None else int(protons)
            if not 0 <= protons <= int(rec["n_acidic_protons"]):
                raise ValueError("protons_as_added outside the acid system")
            comps.append({"record": rec, "total_mol_l": comp["concentration_mol_m3"] / 1000.0, "protons_as_added": protons})
        strong = 0.0
        for item in spec["strong_electrolytes"]:
            rec = ab.record(item["substance_id"])
            if rec is None or rec.get("record_type") != "titrant":
                raise ValueError("unknown strong electrolyte")
            if rec.get("acid_base_link"):
                comps.append({"record": ab.record(rec["acid_base_link"]), "total_mol_l": item["concentration_mol_m3"] / 1000.0,
                              "protons_as_added": int(ab.record(rec["acid_base_link"])["protons_in_named_form"])})
            else:
                sign = 1.0 if rec["strength"] == "strong_base" else -1.0
                strong += sign * float(rec.get("equivalents_per_mol") or 1) * item["concentration_mol_m3"] / 1000.0
        solved = ab.solve_ph(comps, strong, t)
        values["ph"] = solved["ph"]
        evidence["ph"] = "derived (ideal charge balance from measured_literature pKa)"
        sources["ph"] = {"template_source": ACIDBASE_REL, "pka_models": solved["pka_models"], "pkw_model": solved["pkw_model"],
                         "records": {c["record"]["substance_id"]: provenance(ACIDBASE_REL, c["record"])["template_record_source"] for c in comps}}
    elif spec["components"] or spec["strong_electrolytes"] or spec["substance_id"]:
        raise ValueError("kind 'water' takes no components")
    if spec["do_saturation_fraction"] is not None:
        rec = do_model_record()
        values["do_kg_m3"] = spec["do_saturation_fraction"] * do_saturation_kg_m3(t, spec["pressure_pa"], spec["salinity"])
        evidence["do"] = "derived (Benson–Krause equations; saturation fraction is a host assumption)"
        sources["do"] = provenance(TEMPLATE_REL, rec)
    if all(v is None for v in values.values()):
        raise ValueError("solution yields no template-backed reading")
    standard_slot = spec["standard_channel"]
    if standard_slot is not None and values[CHANNEL_KEY[standard_slot]] is None:
        raise ValueError("standard channel has no template value")
    sample = {"ph": values["ph"], "conductance_s": values["conductance_s"], "do_kg_m3": values["do_kg_m3"], "temperature_k": t,
              "reference_ph": float(spec["electrode_isopotential_ph"]), "reference_potential_v": float(spec["electrode_isopotential_v"]),
              "conductance_temp_coefficient_k_inv": None, "conductance_reference_temperature_k": None,
              "standard_slot": standard_slot, "standard_value": None if standard_slot is None else values[CHANNEL_KEY[standard_slot]]}
    return {"spec": spec, "sample": sample, "evidence": evidence, "sources": sources}


def read_record(substance_id: str, temperature_k: float | None, pressure_pa: float | None, salinity: float | None) -> dict[str, Any] | None:
    rec = standard(substance_id)
    rel = TEMPLATE_REL
    if rec is None:
        rec, rel = ab.record(substance_id), ACIDBASE_REL
    if rec is None:
        return None
    out = public_record(rel, rec)
    out["value_at_temperature"] = None
    out["value_unit"] = None
    if temperature_k is not None:
        try:
            kind = rec.get("record_type")
            if kind == "ph_buffer":
                out["value_at_temperature"], out["value_unit"] = buffer_ph(rec, temperature_k), "1"
            elif kind == "conductivity_standard":
                out["value_at_temperature"], out["value_unit"] = conductivity_s_m(rec, temperature_k), "S/m"
            elif kind == "dissolved_oxygen_model":
                out["value_at_temperature"] = do_saturation_kg_m3(temperature_k, 101325.0 if pressure_pa is None else pressure_pa,
                                                                  0.0 if salinity is None else salinity)
                out["value_unit"] = "kg/m3"
            elif kind == "solvent_autoprotolysis":
                out["value_at_temperature"], out["value_unit"] = ab.pkw_at(temperature_k)[0], "1"
        except ValueError:
            out["value_at_temperature"] = None
    if rec.get("record_type") == "acid_base":
        out["pka_at_temperature"] = ab.pka_at(rec, T_REF if temperature_k is None else float(temperature_k))[0]
    else:
        out["pka_at_temperature"] = None
    return out


def report(inst: dict[str, Any], reading: dict[str, Any], *, evaluator: bool) -> dict[str, Any]:
    template = inst.get("template")
    backed = template is not None and template["truth"] == inst["truth"]
    channel = inst["selected"]
    channel_backed = backed and channel in template["evidence"]
    result = {"template_backed": channel_backed, **{k: reading[k] for k in ("slot", "status", "stable", "ph", "potential_v", "conductance_s", "do_kg_m3", "calibrated", "sample_time_s")},
              "template_sources": sorted({src.get("template_source") for src in template["sources"].values()}) if backed else [],
              "evidence": ({**{k: str(v) for k, v in template["evidence"].items()},
                            "electrode": "assumption: ideal Nernst/instantaneous probe (legacy model)"} if backed else None),
              "synthetic": not channel_backed,
              "synthetic_parts": ["probe response, stability and calibration offset (legacy engineering assumptions)"],
              "limitations": list(LIMITATIONS), "truth": None}
    if evaluator and backed:
        result["truth"] = {"solution": copy.deepcopy(template["spec"]), "sample": copy.deepcopy(template["sample"]),
                           "sources": copy.deepcopy(template["sources"])}
    return result


READ_INPUT = obj({"substance_id": {"type": "string", "minLength": 1, "maxLength": 128}, "temperature_k": nullable(num("K", 273.15, 368.15)),
                  "pressure_pa": nullable(num("Pa", 5e4, 1.2e5)), "salinity": nullable(num("1", 0, 40))}, required=["substance_id"])
READ_OUTPUT = obj({"record": {"type": "object"}, "template_source": TEXT, "template_record_source": nullable(TEXT), "evidence": nullable(TEXT),
                   "library_sha256": TEXT, "value_at_temperature": nullable(num("1")), "value_unit": nullable(TEXT),
                   "pka_at_temperature": nullable(arr(num("1")))})
LIST_OUTPUT = obj({"records": arr(obj({"substance_id": TEXT, "record_type": TEXT, "name": TEXT, "template_source": TEXT}))})
REPORT_OUTPUT = obj({"template_backed": BOOL, "slot": TEXT, "status": TEXT, "stable": BOOL, "ph": nullable(num("1")), "potential_v": nullable(num("V")),
                     "conductance_s": nullable(num("S")), "do_kg_m3": nullable(num("kg/m3")), "calibrated": BOOL, "sample_time_s": nullable(num("s", 0)),
                     "template_sources": arr(TEXT), "evidence": nullable({"type": "object", "additionalProperties": TEXT}), "synthetic": BOOL,
                     "synthetic_parts": arr(TEXT), "limitations": arr(TEXT), "truth": nullable({"type": "object"})})


def capability_metadata() -> dict[str, dict[str, Any]]:
    """Describe entries in the Metrohm914 metadata style."""
    def entry(name, inputs, outputs, example, description):
        return {"availability": "implemented", "visibility": "agent", "kind": "query", "description_zh": description,
                "input_schema": inputs, "output_schema": outputs, "example_arguments": [example], "state_effects": [], "observes": [],
                "scene_backend_required": False, "preconditions_zh": "实例可用。",
                "completion_criterion_zh": "同步返回；不推进宿主时钟、不改变实例状态。",
                "binding": f"basic40_runtime_measure.Metrohm914Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
                "template_source": [TEMPLATE_REL, ACIDBASE_REL]}
    return {
        "template.list": entry("template.list", obj({}), LIST_OUTPUT, {}, "列出公开标准库（NIST pH缓冲液、电导标准液、溶解氧模型）与共享pKa表条目；不透露当前样品。"),
        "template.read": entry("template.read", READ_INPUT, READ_OUTPUT, {"substance_id": "nist_srm_185i_khp_buffer", "temperature_k": 298.15, "pressure_pa": None, "salinity": None},
                               "读取一条公开参考记录；给定温度时返回该温度的认证pH(S)、电导率κ或饱和溶解氧（表内线性插值或方程计算）。"),
        "electrochemistry.template_reading": entry("electrochemistry.template_reading", obj({}), REPORT_OUTPUT, {},
                                                   "返回当前所选通道读数及其模板来源、证据等级与假设清单；agent 不可见溶液组成与真值。"),
    }


def invoke(session, host, instance_id: str, inst: dict[str, Any], cap: str, args: dict[str, Any], request_id: str, *, evaluator: bool):
    meta = capability_metadata()[cap]
    if not matches(meta["input_schema"], args):
        return session._error(instance_id, "INVALID_ARGUMENT", request_id)
    if cap == "template.list":
        rows = [{"substance_id": sid, "record_type": rec.get("record_type", ""), "name": rec.get("name", ""), "template_source": TEMPLATE_REL}
                for sid, rec in _records().items()]
        rows += [{"substance_id": sid, "record_type": rec.get("record_type", ""), "name": rec.get("name", ""), "template_source": ACIDBASE_REL}
                 for sid, rec in index_by_id(ACIDBASE_REL).items() if rec.get("record_type") in ("acid_base", "solvent_autoprotolysis", "titrant")]
        return session._envelope(instance_id, request_id, result={"records": rows})
    if cap == "template.read":
        result = read_record(args["substance_id"], args.get("temperature_k"), args.get("pressure_pa"), args.get("salinity"))
        if result is None:
            return session._error(instance_id, "INVALID_ARGUMENT", request_id)
        return session._envelope(instance_id, request_id, result=result)
    return session._envelope(instance_id, request_id, result=report(inst, host._reading(inst), evaluator=evaluator))


def host_step_template(host, time_s: float, solutions: dict[str, dict[str, Any]], inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build template samples, advance the legacy host clock with them, then attach provenance to each instance."""
    with host._lock:
        if not isinstance(solutions, dict) or any(not isinstance(k, str) or k not in host._instances for k in solutions):
            raise ValueError("solutions must map known instance ids to solution descriptions")
        merged = dict(inputs or {})
        if set(merged) & set(solutions):
            raise ValueError("an instance cannot receive both a template and an explicit sample")
        built = {iid: build_sample(spec) for iid, spec in solutions.items()}
        merged.update({iid: dict(b["sample"]) for iid, b in built.items()})
        host.host_step(time_s, inputs=merged)
        for iid, b in built.items():
            inst = host._instances[iid]
            inst["template"] = {"truth": dict(inst["truth"]), "spec": b["spec"], "sample": b["sample"],
                                "evidence": b["evidence"], "sources": b["sources"]}
        return {iid: {"template_backed": True, "synthetic": False} for iid in built}
