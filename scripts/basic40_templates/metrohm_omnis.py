"""Metrohm OMNIS titrator: template-backed analytes and titrants from the shared public acid-base library.

Library: ``assets/reactions/metrohm_omnis_acidbase_library/templates.json`` (stepwise thermodynamic pKa with ΔrH°/ΔrCp°
or PHREEQC analytic temperature functions, water autoprotolysis, strong volumetric titrants and NIST primary
standards). The same table serves the 914 pH meter.

The trusted host loads an analyte with ``MetrohmOmnisRuntime.host_load_template_sample``; afterwards the existing
dosing/stability/endpoint process is unchanged except that ``MetrohmOmnisRuntime._potential`` asks this module for
pH: an ideal-solution charge balance over every acid system, the titrant's spectator ions and pKw(T), then the same
ideal Nernst electrode as the legacy model. Equivalence points are found by the existing endpoint / dynamic
algorithms from the observed curve, so ``titration.calculate_content`` works unchanged. Activity coefficients are 1
(ideal solution — stated, not corrected).
"""
from __future__ import annotations

import copy
import math
from typing import Any

from . import _acidbase as ab
from ._echem_common import BOOL, F, LN10, R, T_REF, TEXT, arr, index_by_id, matches, nullable, num, number, obj, provenance, public_record

TEMPLATE_REL = ab.ACIDBASE_REL
TEMPLATE_CAPABILITY_IDS = ("template.list", "template.read", "titration.template_report")
LIMITATIONS = [
    "Ideal solution: activity coefficients = 1, mol/L used for molality; no ionic-strength correction of the I = 0 pKa values.",
    "pKa(T) from PHREEQC analytic expressions or constant-ΔCp van 't Hoff where the library has ΔrH°; otherwise the 298.15 K value is used (flagged).",
    "Strong titrants are assumed fully dissociated and carbonate-free; titrant concentration is the value the agent configured (no hidden titer error).",
    "Instantaneous mixing and an ideal Nernstian glass electrode (host-set isopotential pH and potential); no electrode response time, junction potential or alkaline/acid error.",
    "Precipitation, complexation, CO2 uptake and redox reactions are not modelled.",
]

ANALYTE_SCHEMA = obj({
    "components": arr(obj({"substance_id": {"type": "string", "minLength": 1, "maxLength": 128},
                           "amount_mol": num("mol", 0, 10.0), "mass_kg": num("kg", 0, 1.0),
                           "protons_as_added": nullable({"type": "integer", "minimum": 0, "maximum": 8, "x-unit": "1"})},
                          required=["substance_id"]), 16),
    "strong_acid_mol": num("mol", 0, 10.0), "strong_base_mol": num("mol", 0, 10.0),
    "volume_m3": num("m^3", exclusive_minimum=0, maximum=1e-2), "temperature_k": num("K", 273.15, 368.15),
    "titrant_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "electrode_isopotential_ph": num("1", 0, 14), "electrode_isopotential_v": num("V", -1, 1),
}, required=["components", "volume_m3", "titrant_id"])
ANALYTE_DEFAULTS = {"strong_acid_mol": 0.0, "strong_base_mol": 0.0, "temperature_k": T_REF,
                    "electrode_isopotential_ph": 7.0, "electrode_isopotential_v": 0.0}


def titrant(substance_id: str) -> dict[str, Any] | None:
    rec = ab.record(substance_id)
    return rec if rec and rec.get("record_type") == "titrant" else None


def build_analyte(analyte: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(analyte, dict) or not matches(ANALYTE_SCHEMA, analyte):
        raise ValueError("analyte does not match the template analyte schema")
    spec = {**ANALYTE_DEFAULTS, **copy.deepcopy(analyte)}
    tit = titrant(spec["titrant_id"])
    if tit is None or tit.get("strength") not in ("strong_base", "strong_acid"):
        raise ValueError("unknown titrant")
    comps, ids = [], set()
    for comp in spec["components"]:
        named = ab.record(comp["substance_id"])
        if named is None or comp["substance_id"] in ids or ("amount_mol" in comp) == ("mass_kg" in comp):
            raise ValueError("unknown/duplicate component or not exactly one of amount_mol / mass_kg")
        ids.add(comp["substance_id"])
        if named.get("record_type") == "primary_standard":
            # NIST SRM weighed as the standard form: certified mass fraction x mass / molar mass, linked acid system.
            rec = ab.record(named["acid_base_link"])
            default_protons = int(named["protons_in_standard_form"])
            purity = float(named["certified_mass_fraction"])
        elif named.get("record_type") == "acid_base":
            rec, default_protons, purity = named, int(named["protons_in_named_form"]), 1.0
        else:
            raise ValueError("component must be an acid_base system or a primary_standard")
        if "mass_kg" in comp:
            if not number(named.get("molar_mass_kg_mol")):
                raise ValueError("molar mass unknown for mass-based amount")
            amount = float(comp["mass_kg"]) * purity / float(named["molar_mass_kg_mol"])
        else:
            amount = float(comp["amount_mol"])
        protons = comp.get("protons_as_added")
        protons = default_protons if protons is None else int(protons)
        if not 0 <= protons <= int(rec["n_acidic_protons"]):
            raise ValueError("protons_as_added outside the acid system")
        comps.append({"record": rec, "named_id": named["substance_id"], "amount_mol": amount, "protons_as_added": protons})
    link = tit.get("acid_base_link")
    titrant_system = ab.record(link) if link else None
    strength = tit["strength"]
    equivalents = sum(c["amount_mol"] * ab.titratable_steps(c["record"], c["protons_as_added"], strength) for c in comps)
    equivalents += spec["strong_acid_mol"] if strength == "strong_base" else spec["strong_base_mol"]
    # Strong acid/base already present neutralise each other first.
    equivalents -= spec["strong_base_mol"] if strength == "strong_base" else spec["strong_acid_mol"]
    equivalents = max(0.0, equivalents)
    provenances = {c["record"]["substance_id"]: provenance(TEMPLATE_REL, c["record"]) for c in comps}
    provenances.update({c["named_id"]: provenance(TEMPLATE_REL, ab.record(c["named_id"])) for c in comps})
    provenances[tit["substance_id"]] = provenance(TEMPLATE_REL, tit)
    return {"spec": spec, "components": comps, "titrant": tit, "titrant_system": titrant_system,
            "titratable_equivalents_mol": equivalents, "provenances": provenances}


def ph_and_potential(template: dict[str, Any], sample_volume_m3: float, dosed_volume_m3: float,
                     titrant_concentration_mol_m3: float, temperature_k: float) -> tuple[float, float, dict[str, Any]]:
    spec, tit = template["spec"], template["titrant"]
    total_l = (sample_volume_m3 + dosed_volume_m3) * 1000.0
    added_mol = titrant_concentration_mol_m3 * dosed_volume_m3
    components = [{"record": c["record"], "total_mol_l": c["amount_mol"] / total_l, "protons_as_added": c["protons_as_added"]}
                  for c in template["components"]]
    strong = (spec["strong_base_mol"] - spec["strong_acid_mol"]) / total_l
    system = template["titrant_system"]
    if system is not None:
        components.append({"record": system, "total_mol_l": added_mol / total_l, "protons_as_added": int(system["protons_in_named_form"])})
    else:
        sign = 1.0 if tit["strength"] == "strong_base" else -1.0
        strong += sign * float(tit.get("equivalents_per_mol") or 1) * added_mol / total_l
    solved = ab.solve_ph(components, strong, temperature_k)
    ph = solved["ph"]
    potential = spec["electrode_isopotential_v"] - LN10 * R * temperature_k / F * (ph - spec["electrode_isopotential_ph"])
    return ph, potential, solved


def expected_equivalence_volumes(template: dict[str, Any], titrant_concentration_mol_m3: float | None) -> list[float] | None:
    """Stoichiometric equivalence volumes (cumulative proton steps) for a single acid system; evaluator-only."""
    if not number(titrant_concentration_mol_m3) or titrant_concentration_mol_m3 <= 0 or len(template["components"]) != 1:
        return None
    comp, spec, strength = template["components"][0], template["spec"], template["titrant"]["strength"]
    eq = float(template["titrant"].get("equivalents_per_mol") or 1)
    offset = spec["strong_acid_mol"] - spec["strong_base_mol"] if strength == "strong_base" else spec["strong_base_mol"] - spec["strong_acid_mol"]
    steps = ab.titratable_steps(comp["record"], comp["protons_as_added"], strength)
    return [(max(0.0, offset) + k * comp["amount_mol"]) / (titrant_concentration_mol_m3 * eq) for k in range(1, steps + 1)]


def load_template_sample(host, instance_id: str, analyte: dict[str, Any]) -> dict[str, Any]:
    """Host-only fixture: legacy truth fields are filled consistently, then the template is attached."""
    with host._lock:
        template = build_analyte(analyte)
        spec = template["spec"]
        host.host_load_sample(instance_id, {"volume_m3": spec["volume_m3"], "acid_moles": template["titratable_equivalents_mol"],
                                            "temperature_k": spec["temperature_k"], "reference_ph": spec["electrode_isopotential_ph"],
                                            "reference_potential_v": spec["electrode_isopotential_v"]})
        host._instances[instance_id]["template"] = template
        return {"loaded": True, "template_backed": True, "synthetic": False,
                "titratable_equivalents_mol": template["titratable_equivalents_mol"]}


def potential(inst: dict[str, Any]) -> tuple[float, float]:
    template, truth = inst["template"], inst["truth"]
    dosed = inst["steps"] * inst["quantum"] if inst["quantum"] else 0.0
    concentration = inst["concentration"] if number(inst["concentration"]) else 0.0
    ph, e, _ = ph_and_potential(template, truth["volume_m3"], dosed, concentration, truth["temperature_k"])
    if not number(ph) or not number(e):
        raise ValueError("template response outside numeric domain")
    return ph, e


def report(inst: dict[str, Any], *, evaluator: bool) -> dict[str, Any]:
    template = inst.get("template")
    backed = template is not None
    result = {"template_backed": backed, "status": inst["status"],
              "titrant_id": template["titrant"]["substance_id"] if backed else None,
              "titrant_strength": template["titrant"]["strength"] if backed else None,
              "endpoint_volume_m3": inst["endpoint"],
              "model": "ideal charge balance over library pKa(T) and pKw(T); ideal Nernst electrode" if backed else "legacy synthetic strong-acid model",
              "template_source": TEMPLATE_REL if backed else None,
              "library_sha256": template["provenances"][template["titrant"]["substance_id"]]["library_sha256"] if backed else None,
              "evidence": ({"pka": "measured_literature (NIST JPCRD / NIST SRD 46 via USGS PHREEQC)",
                            "temperature_dependence": "derived (van 't Hoff / PHREEQC analytic)",
                            "activity": "assumption: ideal solution", "electrode": "assumption: ideal Nernst",
                            "titrant": "assumption: complete dissociation, configured concentration"} if backed else None),
              "synthetic": not backed,
              "synthetic_parts": ["dosing, mixing, stability and endpoint algorithms (engineering assumptions)",
                                  "ideal electrode response"],
              "limitations": list(LIMITATIONS), "truth": None}
    if evaluator and backed:
        result["truth"] = {"analyte": copy.deepcopy(template["spec"]), "titratable_equivalents_mol": template["titratable_equivalents_mol"],
                           "component_amounts_mol": {c["named_id"]: c["amount_mol"] for c in template["components"]},
                           "expected_equivalence_volumes_m3": expected_equivalence_volumes(template, inst["concentration"]),
                           "record_sources": {k: v["template_record_source"] for k, v in template["provenances"].items()}}
    return result


READ_INPUT = obj({"substance_id": {"type": "string", "minLength": 1, "maxLength": 128}, "temperature_k": nullable(num("K", 273.15, 368.15))},
                 required=["substance_id"])
READ_OUTPUT = obj({"record": {"type": "object"}, "template_source": TEXT, "template_record_source": nullable(TEXT),
                   "evidence": nullable(TEXT), "library_sha256": TEXT,
                   "pka_at_temperature": nullable(arr(num("1"))), "pkw_at_temperature": nullable(num("1")),
                   "temperature_models": nullable(arr(TEXT))})
LIST_OUTPUT = obj({"records": arr(obj({"substance_id": TEXT, "record_type": TEXT, "name": TEXT})), "template_source": TEXT, "library_sha256": TEXT})
REPORT_OUTPUT = obj({"template_backed": BOOL, "status": TEXT, "titrant_id": nullable(TEXT), "titrant_strength": nullable(TEXT),
                     "endpoint_volume_m3": nullable(num("m^3", 0)), "model": TEXT, "template_source": nullable(TEXT),
                     "library_sha256": nullable(TEXT), "evidence": nullable({"type": "object", "additionalProperties": TEXT}),
                     "synthetic": BOOL, "synthetic_parts": arr(TEXT), "limitations": arr(TEXT), "truth": nullable({"type": "object"})})


def read_record(substance_id: str, temperature_k: float | None) -> dict[str, Any] | None:
    rec = ab.record(substance_id)
    if rec is None:
        return None
    result = public_record(TEMPLATE_REL, rec)
    result.update({"pka_at_temperature": None, "pkw_at_temperature": None, "temperature_models": None})
    t = T_REF if temperature_k is None else float(temperature_k)
    if rec.get("record_type") == "acid_base":
        pkas, models = ab.pka_at(rec, t)
        result.update(pka_at_temperature=pkas, temperature_models=models)
    elif rec.get("record_type") == "solvent_autoprotolysis":
        pkw, model = ab.pkw_at(t)
        result.update(pkw_at_temperature=pkw, temperature_models=[model])
    return result


def capability_metadata(binding_class: str = "MetrohmOmnisSession", components: list[str] | None = None) -> dict[str, dict[str, Any]]:
    comps = components or ["housing"]
    def entry(name, inputs, outputs, example, description):
        return {"availability": "implemented", "visibility": "agent", "kind": "query", "description_zh": description,
                "input_schema": inputs, "output_schema": outputs, "example_arguments": [example], "state_effects": [], "observes": [],
                "component_ids": comps, "preconditions_zh": "实例可用。",
                "binding": f"basic40_runtime_measure.{binding_class}.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
                "completion_criterion_zh": "同步返回；不推进宿主时钟、不改变实例状态。",
                "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "只读模板或报告，不改变几何。"},
                "implementation": {"process": "implemented", "host": "implemented", "scene": "not_applicable", "robot": "absent", "hardware": "absent"},
                "error_codes": ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "ACCESS_DENIED", "REQUEST_CONFLICT"],
                "retry": "same_request_id; in-memory only", "template_source": TEMPLATE_REL}
    return {
        "template.list": entry("template.list", obj({}), LIST_OUTPUT, {}, "列出公开酸碱参考库条目（pKa体系、水的离子积、标准滴定剂、NIST基准物）。"),
        "template.read": entry("template.read", READ_INPUT, READ_OUTPUT, {"substance_id": "acetic_acid", "temperature_k": 298.15},
                               "读取一条公开酸碱参考记录及来源；给定温度时返回该温度的逐级pKa或pKw及所用温度模型；不透露当前样品。"),
        "titration.template_report": entry("titration.template_report", obj({}), REPORT_OUTPUT, {},
                                           "返回当前滴定的模板来源、证据、观测终点体积与假设清单；agent 不可见样品组成与真值。"),
    }


def invoke(session, host, instance_id: str, inst: dict[str, Any], cap: str, args: dict[str, Any], request_id: str, *, evaluator: bool):
    meta = capability_metadata()[cap]
    if not matches(meta["input_schema"], args):
        return session._error(instance_id, "INVALID_ARGUMENT", request_id)
    if cap == "template.list":
        rows = [{"substance_id": sid, "record_type": rec.get("record_type", ""), "name": rec.get("name", "")}
                for sid, rec in index_by_id(TEMPLATE_REL).items()]
        sha = provenance(TEMPLATE_REL, next(iter(index_by_id(TEMPLATE_REL).values())))["library_sha256"]
        return session._envelope(instance_id, request_id, result={"records": rows, "template_source": TEMPLATE_REL, "library_sha256": sha})
    if cap == "template.read":
        result = read_record(args["substance_id"], args.get("temperature_k"))
        if result is None:
            return session._error(instance_id, "INVALID_ARGUMENT", request_id)
        return session._envelope(instance_id, request_id, result=result)
    return session._envelope(instance_id, request_id, result=report(inst, evaluator=evaluator))
