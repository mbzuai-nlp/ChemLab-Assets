"""CHEM-031 Biotage Extrahera Classic: template-backed SPE analytes (real logP/pKa + published recoveries).

Library: ``assets/reactions/biotage_extrahera_spe_library/templates.json`` (127 analytes: PubChem identity, experimental
log Kow and reported pKa values with their upstream source/licence, RDKit-detected ionizable groups (derived), and
180 published absolute recoveries from Biotage application notes/posters and two CC BY 4.0 papers, each tied to its
sorbent, format, matrix, spike level, method and processing system (Extrahera or manual)).

What is real and what is not:

* ``recovery_fraction`` values are measured_literature numbers for exactly the stated sorbent/method; the runtime uses
  them only when the host-declared ``sorbent_id`` matches (median of the matching published values, Extrahera runs
  preferred, approximate/rounded statements excluded).  No match -> recovery null (never extrapolated).
* logP/pKa are measured_literature; the neutral fraction at a pH (Henderson-Hasselbalch) and the retention class are
  ``derived`` heuristics and are never turned into a recovery number.
* The analyte mass bookkeeping (mass loaded = concentration x volume withdrawn from the load source, eluate
  concentration = recovered mass / collected volume) is an engineering assumption on top of the existing aggregate
  liquid model (no per-well solver, breakthrough, matrix effect or evaporation).

The agent-visible ``template.read`` is a public lookup; the identity of the analyte the host put in a source and the
expected recovery are only available to evaluator principals (``spe.template_report``) and the trusted host.
"""
from __future__ import annotations

import copy
import math
import statistics

try:  # scripts/ on sys.path (tests, Matterix bridge)
    import template_library as _tl
except ImportError:  # project root on sys.path
    from scripts import template_library as _tl

TEMPLATE_REL = "assets/reactions/biotage_extrahera_spe_library/templates.json"
CAPABILITY_IDS = ("template.read", "spe.template_report")
EVALUATOR_CAPABILITIES = ("spe.template_report",)
LIMITATIONS = [
    "Published recoveries are valid only for the stated sorbent, format, matrix, spike level and method; a different host-declared sorbent gives recovery null.",
    "Most entries are not Extrahera Classic runs (see processing_system); Biotage reports processing-system comparisons only for the cited application notes.",
    "logP/pKa-based ionisation and retention statements are derived heuristics and are never converted into a recovery.",
    "Analyte bookkeeping assumes the whole volume withdrawn from the load source passed the sorbent once; no breakthrough, wash loss, matrix effect, evaporation or per-well variation is simulated.",
    "Licences differ per value: HSDB public domain, ChEMBL CC BY-SA 3.0, DrugBank CC BY-NC 4.0 (non-commercial), HMDB academic terms, Biotage documents all rights reserved (numbers cited only).",
]
SYNTHETIC = ["analyte transfer bookkeeping on the aggregate liquid model (assumption)", "single-pass load of the withdrawn source volume (assumption)"]

_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": {"type": "string"}}
_NUM1 = {"type": "number", "x-unit": "1"}


def _obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


_RECOVERY = _obj({"recovery_fraction": {"type": "number", "minimum": 0, "x-unit": "1"}, "rsd_fraction": _nullable({"type": "number", "minimum": 0, "x-unit": "1"}),
                  "n": _nullable({"type": "integer", "minimum": 1, "x-unit": "1"}), "sorbent_id": _nullable(_TEXT), "sorbent": _TEXT, "processing_system": _TEXT,
                  "matrix": _nullable(_TEXT), "spike_level": _nullable(_TEXT), "approximate": {"type": "boolean"}, "source": _TEXT, "locator": _TEXT, "url": _TEXT})
_PKA = _obj({"value": _NUM1, "temperature_k": _nullable({"type": "number", "x-unit": "K"}), "annotation": _nullable(_TEXT), "source_name": _TEXT})
TEMPLATE_SCHEMA = _obj({
    "substance_id": _TEXT, "name": _TEXT, "cas": _nullable(_TEXT), "pubchem_cid": {"type": "integer", "x-unit": "1"},
    "molar_mass_kg_mol": {"type": "number", "x-unit": "kg/mol"}, "logp": _nullable(_NUM1), "logp_source": _nullable(_TEXT),
    "xlogp3_computed": _nullable(_NUM1), "pka_selected": {"type": "array", "items": _PKA},
    "ionizable_groups": _obj({"acidic": _TEXTS, "basic": _TEXTS}), "recoveries": {"type": "array", "items": _RECOVERY}, "evidence": _TEXT,
})
_SUMMARY = _obj({"sorbent_id": _nullable(_TEXT), "n_values": {"type": "integer", "minimum": 0, "x-unit": "1"}, "median_recovery_fraction": _nullable(_NUM1),
                 "min_recovery_fraction": _nullable(_NUM1), "max_recovery_fraction": _nullable(_NUM1), "basis": _TEXT, "evidence": _TEXT})
_IONISATION = _obj({"ph": _nullable(_NUM1), "neutral_fraction": _nullable({"type": "number", "minimum": 0, "maximum": 1, "x-unit": "1"}),
                    "charge_type": _nullable(_TEXT), "pka_used": _nullable(_NUM1), "reason": _TEXT, "evidence": _TEXT})
TEMPLATE_READ_INPUT = _obj({
    "substance_id": {"type": "string", "description": "Library substance_id, e.g. amphetamine"},
    "ph": {"anyOf": [{"type": "number", "minimum": 0, "maximum": 14, "x-unit": "1"}, {"type": "null"}], "description": "Optional aqueous pH for the derived neutral fraction"},
    "sorbent_id": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "Optional sorbent_id filter, e.g. isolute_sle_plus"},
})
TEMPLATE_READ_OUTPUT = _obj({"template": TEMPLATE_SCHEMA, "recovery_summary": _SUMMARY, "ionisation": _IONISATION, "retention_reasoning": _obj({"statement": _TEXT, "evidence": _TEXT}),
                             "template_source": _TEXT, "library_sha256": _TEXT, "evidence": _TEXT, "synthetic": {"type": "boolean"}, "limitations": _TEXTS})
_ASSIGNED = _obj({
    "source_position": _TEXT, "analyte_id": _TEXT, "sorbent_id": _TEXT, "concentration_kg_m3": {"type": "number", "minimum": 0, "x-unit": "kg/m^3"},
    "withdrawn_volume_m3": {"type": "number", "minimum": 0, "x-unit": "m^3"}, "withdrawn_mass_kg": {"type": "number", "minimum": 0, "x-unit": "kg"},
    "loaded_by_completed_method": {"type": "boolean"}, "recovery_summary": _SUMMARY,
    "expected_eluted_mass_kg": _nullable({"type": "number", "minimum": 0, "x-unit": "kg"}), "expected_eluate_concentration_kg_m3": _nullable({"type": "number", "minimum": 0, "x-unit": "kg/m^3"}),
    "status": _TEXT, "evidence": _TEXT})
REPORT_OUTPUT = _obj({"analytes": {"type": "array", "items": _ASSIGNED}, "collection_volume_m3": _nullable({"type": "number", "minimum": 0, "x-unit": "m^3"}),
                      "method_status": _TEXT, "template_source": _TEXT, "library_sha256": _TEXT, "evidence": _TEXT, "synthetic": {"type": "boolean"},
                      "synthetic_components": _TEXTS, "limitations": _TEXTS})


def library():
    return _tl.load_library(TEMPLATE_REL)


def record(substance_id):
    return _tl.get_template(TEMPLATE_REL, substance_id) if isinstance(substance_id, str) else None


def sorbent_ids():
    return [s["sorbent_id"] for s in library()["sorbents"]]


def _public_recovery(r):
    return {k: r[k] for k in ("recovery_fraction", "rsd_fraction", "n", "sorbent_id", "sorbent", "processing_system", "matrix", "spike_level", "approximate", "source", "locator", "url")}


def public_template(rec, sorbent=None):
    groups = rec["ionizable_groups"] or {"acidic": [], "basic": []}
    return {"substance_id": rec["substance_id"], "name": rec["name"], "cas": rec["cas"], "pubchem_cid": rec["pubchem_cid"], "molar_mass_kg_mol": rec["molar_mass_kg_mol"],
            "logp": None if rec["logp"] is None else rec["logp"]["value"], "logp_source": None if rec["logp"] is None else rec["logp"]["source_name"],
            "xlogp3_computed": rec["xlogp3"], "pka_selected": [{k: p[k] for k in ("value", "temperature_k", "annotation", "source_name")} for p in rec["pka_selected"]],
            "ionizable_groups": {"acidic": list(groups["acidic"]), "basic": list(groups["basic"])},
            "recoveries": [_public_recovery(r) for r in rec["recoveries"] if sorbent is None or r["sorbent_id"] == sorbent], "evidence": rec["evidence"]}


def recovery_summary(rec, sorbent):
    """Median of matching non-approximate published recoveries; Extrahera runs preferred when present."""
    if sorbent is None:
        return {"sorbent_id": None, "n_values": 0, "median_recovery_fraction": None, "min_recovery_fraction": None, "max_recovery_fraction": None,
                "basis": "no sorbent_id given", "evidence": "none"}
    match = [r for r in rec["recoveries"] if r["sorbent_id"] == sorbent and not r["approximate"]]
    extrahera = [r for r in match if r["processing_system"] == "Extrahera"]
    use = extrahera or match
    if not use:
        return {"sorbent_id": sorbent, "n_values": 0, "median_recovery_fraction": None, "min_recovery_fraction": None, "max_recovery_fraction": None,
                "basis": "no published non-approximate recovery for this analyte on this sorbent (null, not extrapolated)", "evidence": "none"}
    values = [r["recovery_fraction"] for r in use]
    return {"sorbent_id": sorbent, "n_values": len(values), "median_recovery_fraction": statistics.median(values), "min_recovery_fraction": min(values),
            "max_recovery_fraction": max(values),
            "basis": ("median of Extrahera-processed" if extrahera else "median of non-Extrahera (manual or unstated processing)") + f" published values ({len(values)}) across the cited spike levels/conditions",
            "evidence": "measured_literature values; median across levels is derived"}


def ionisation(rec, ph):
    groups = rec["ionizable_groups"] or {"acidic": [], "basic": []}
    base = {"ph": ph, "neutral_fraction": None, "charge_type": None, "pka_used": None, "evidence": "derived (Henderson-Hasselbalch on a reported pKa; heuristic group assignment)"}
    if ph is None:
        return dict(base, reason="no pH given")
    values = [p["value"] for p in rec["pka_selected"]]
    kinds = ["acid"] * bool(groups["acidic"]) + ["base"] * bool(groups["basic"])
    if not values:
        return dict(base, reason="no reported pKa")
    if len(kinds) != 1 or max(values) - min(values) > 1.0:
        return dict(base, reason="ambiguous: several ionizable group classes or pKa values more than 1 unit apart; no single-site calculation")
    pka = statistics.median(values)
    neutral = 1.0 / (1.0 + 10 ** (ph - pka)) if kinds[0] == "acid" else 1.0 / (1.0 + 10 ** (pka - ph))
    return dict(base, neutral_fraction=neutral, charge_type=kinds[0], pka_used=pka, reason="single ionizable class and consistent pKa values")


def retention_reasoning(rec, ion):
    logp = None if rec["logp"] is None else rec["logp"]["value"]
    parts = []
    if logp is None:
        parts.append("no experimental logP")
    elif logp < 0:
        parts.append(f"logP {logp:g} < 0: weak reversed-phase retention of the neutral form")
    elif logp < 2:
        parts.append(f"logP {logp:g}: moderate reversed-phase retention of the neutral form")
    else:
        parts.append(f"logP {logp:g} >= 2: strong reversed-phase retention of the neutral form")
    if ion["neutral_fraction"] is not None:
        parts.append(f"{ion['charge_type']} with pKa {ion['pka_used']:g}: neutral fraction {ion['neutral_fraction']:.3g} at pH {ion['ph']:g}" +
                     ("; mostly ionised at this pH (reversed-phase retention reduced, ion-exchange retention possible)" if ion["neutral_fraction"] < 0.5 else ""))
    return {"statement": "; ".join(parts) + ". Rule-of-thumb only, not a recovery.", "evidence": "derived (heuristic from measured logP/pKa)"}


def capabilities(*, include_evaluator):
    caps = {"template.read": {
        "availability": "implemented", "visibility": "agent", "kind": "query",
        "description_zh": "公开参考查询：分析物的实验 logP、文献 pKa、可电离基团（推导）及按吸附剂筛选的已发表绝对回收率；可选 pH 给出推导的中性分数；不透露样品真实身份",
        "input_schema": copy.deepcopy(TEMPLATE_READ_INPUT), "output_schema": copy.deepcopy(TEMPLATE_READ_OUTPUT), "state_effects": [], "observes": [],
        "example_arguments": [{"substance_id": "cortisol", "ph": 7.0, "sorbent_id": "isolute_sle_plus"}],
        "binding": "basic40_runtime_vacuum.ExtraheraSession.invoke(instance_id=..., capability_id='template.read', arguments=..., request_id=...)"}}
    if include_evaluator:
        caps["spe.template_report"] = {
            "availability": "implemented", "visibility": "evaluator", "kind": "query",
            "description_zh": "评估者查询：宿主指定源液中的分析物、取用体积与质量、按声明吸附剂匹配的已发表回收率及预期洗脱质量/浓度；真实回收率与假设记账分列",
            "input_schema": _obj({}), "output_schema": copy.deepcopy(REPORT_OUTPUT), "state_effects": [], "observes": [], "example_arguments": [{}],
            "binding": "basic40_runtime_vacuum.ExtraheraSession.invoke(instance_id=..., capability_id='spe.template_report', arguments={}, request_id=...)"}
    return caps


def _finite(x, low=None, high=None):
    return type(x) in (int, float) and math.isfinite(x) and (low is None or x >= low) and (high is None or x <= high)


def template_read(args):
    if not isinstance(args, dict) or set(args) != {"substance_id", "ph", "sorbent_id"} or not isinstance(args["substance_id"], str):
        return None, "INVALID_ARGUMENT"
    if (args["ph"] is not None and not _finite(args["ph"], 0, 14)) or (args["sorbent_id"] is not None and not isinstance(args["sorbent_id"], str)):
        return None, "INVALID_ARGUMENT"
    rec = record(args["substance_id"])
    if rec is None or (args["sorbent_id"] is not None and args["sorbent_id"] not in sorbent_ids()):
        return None, "UNKNOWN_REFERENCE"
    ion = ionisation(rec, args["ph"])
    prov = _tl.provenance(TEMPLATE_REL, rec)
    return {"template": public_template(rec, args["sorbent_id"]), "recovery_summary": recovery_summary(rec, args["sorbent_id"]), "ionisation": ion,
            "retention_reasoning": retention_reasoning(rec, ion), "template_source": prov["template_source"], "library_sha256": prov["library_sha256"],
            "evidence": "measured_literature logP/pKa/recoveries; ionisation and retention reasoning derived", "synthetic": False, "limitations": list(LIMITATIONS)}, None


def assign_analyte(host, instance_id, *, source_position, analyte_id, concentration_kg_m3, sorbent_id, evidence):
    """Trusted host: declare that a named source liquid contains a library analyte and which sorbent the deck carries."""
    with host._lock:
        inst = host._instances[instance_id]
        if inst.sources is None or inst.state["operation.active_id"] or source_position not in inst.sources:
            raise ValueError("Initialized source inventory, idle instrument and an existing source position required")
        if record(analyte_id) is None or sorbent_id not in sorbent_ids() or not _finite(concentration_kg_m3, 0, 1e3) or not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("Library analyte, library sorbent_id, concentration in [0,1e3] kg/m3 and evidence required")
        candidate = copy.deepcopy(inst)
        candidate.template_samples[source_position] = {"analyte_id": analyte_id, "concentration_kg_m3": float(concentration_kg_m3), "sorbent_id": sorbent_id,
                                                       "source_volume_at_assignment_m3": inst.sources[source_position], "evidence": evidence}
        host._commit(inst, candidate)


def report(inst):
    prov_sha = _tl.library_sha256(TEMPLATE_REL)
    s = inst.state
    analytes = []
    load_positions = [step["solvent_position"] for step in (inst.method or []) if step["phase"] == "load"]
    completed = s["method.status"] == "completed"
    for pos, tpl in sorted(inst.template_samples.items()):
        rec = record(tpl["analyte_id"])
        withdrawn = max(0.0, tpl["source_volume_at_assignment_m3"] - inst.sources.get(pos, 0.0))
        mass = withdrawn * tpl["concentration_kg_m3"]
        summary = recovery_summary(rec, tpl["sorbent_id"])
        loaded = completed and pos in load_positions
        eluted = conc = None
        if not loaded:
            status = "not_loaded_by_completed_method"
        elif summary["median_recovery_fraction"] is None:
            status = "no_published_recovery_for_sorbent"
        else:
            eluted = mass * summary["median_recovery_fraction"]
            conc = eluted / s["fluid.collection_m3"] if s["fluid.collection_m3"] else None
            status = "expected_from_published_recovery"
        analytes.append({"source_position": pos, "analyte_id": tpl["analyte_id"], "sorbent_id": tpl["sorbent_id"], "concentration_kg_m3": tpl["concentration_kg_m3"],
                         "withdrawn_volume_m3": withdrawn, "withdrawn_mass_kg": mass, "loaded_by_completed_method": loaded, "recovery_summary": summary,
                         "expected_eluted_mass_kg": eluted, "expected_eluate_concentration_kg_m3": conc, "status": status,
                         "evidence": "recovery measured_literature (median derived); masses are assumption-based bookkeeping"})
    return {"analytes": analytes, "collection_volume_m3": s["fluid.collection_m3"], "method_status": s["method.status"], "template_source": TEMPLATE_REL,
            "library_sha256": prov_sha, "evidence": "measured_literature recoveries for the declared sorbent; bookkeeping assumption", "synthetic": False,
            "synthetic_components": list(SYNTHETIC), "limitations": list(LIMITATIONS)}
