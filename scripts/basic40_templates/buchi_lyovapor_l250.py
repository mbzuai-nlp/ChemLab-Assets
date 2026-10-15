"""CHEM-025 BUCHI Lyovapor L-250 Pro: template-backed freeze-drying products (real Tg'/Tc/Te + ice vapour pressure).

Library: ``assets/reactions/buchi_lyovapor_l250_formulation_library/templates.json``

* ``kind`` excipient/formulation records: literature Tg' (maximally freeze-concentrated glass transition), Tc
  (collapse temperature, freeze-dry microscopy) and Te (eutectic melting) with document, locator and verbatim quote.
  The runtime critical temperature is the conservative minimum of the selected values (derived).
* ``kind == "equation"`` record ``ice_ih``: Murphy & Koop (2005) Eq. 7 ice vapour pressure
  ``ln(p/Pa) = 9.550426 - 5723.265/T + 3.53068 ln T - 0.00728332 T`` (T > 110 K), cached paper copy; values are
  derived from the equation.

Runtime use (opt-in, legacy path unchanged): ``LyovaporHost.initialize_template_sample`` initialises a sample linked to
a library record.  While ice remains during primary drying, ``template_gate`` makes the cycle wait with
"temperature" when the product is warmer than the library critical temperature and with "pressure" while the real
ice vapour pressure at the product temperature does not exceed the chamber pressure.  Sublimation/desorption rates
remain the host-supplied engineering assumption of the legacy model.
"""
from __future__ import annotations

import copy
import math

try:  # scripts/ on sys.path (tests, Matterix bridge)
    import template_library as _tl
except ImportError:  # project root on sys.path
    from scripts import template_library as _tl

TEMPLATE_REL = "assets/reactions/buchi_lyovapor_l250_formulation_library/templates.json"
ICE_ID = "ice_ih"
MK_A, MK_B, MK_C, MK_D = 9.550426, -5723.265, 3.53068, -0.00728332
ICE_T_MIN_K, ICE_T_MAX_K = 110.0, 273.16
MARGIN_WARN_K = 2.0
CAPABILITY_IDS = ("template.read", "drying.template_report")
EVALUATOR_CAPABILITIES = ("drying.template_report",)
LIMITATIONS = [
    "Tg'/Tc/Te are literature values for the stated composition and method (DSC, freeze-dry microscopy); concentration, protein content, annealing and heating rate shift them, and the runtime does not correct for this.",
    "The runtime critical temperature is the conservative minimum of the selected Tg', Tc and Te values (derived); crystallising excipients may in practice be dried above Tg' when fully crystallised.",
    "Ice vapour pressure follows Murphy & Koop (2005) Eq. 7 for hexagonal ice (T > 110 K); dissolved solutes, vapour-pressure lowering of the frozen matrix and the dry-layer resistance are ignored.",
    "Sublimation/desorption rates stay host-supplied engineering assumptions; the template only gates primary drying on the real ice vapour pressure and flags the product temperature against the critical temperature.",
]
SYNTHETIC = ["sublimation/desorption rates: host-supplied constants (legacy engineering assumption)", "product/shelf/condenser temperatures and chamber pressure: host boundary values"]

_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": {"type": "string"}}
_NULL_K = {"anyOf": [{"type": "number", "x-unit": "K"}, {"type": "null"}]}
_NULL_PA = {"anyOf": [{"type": "number", "minimum": 0, "x-unit": "Pa"}, {"type": "null"}]}


def _obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


_VALUE = _obj({"property": _TEXT, "value_k": _nullable({"type": "number", "x-unit": "K"}), "range_k": _nullable({"type": "array", "items": {"type": "number", "x-unit": "K"}}),
               "method": _nullable(_TEXT), "conditions": _nullable(_TEXT), "source": _TEXT, "locator": _nullable(_TEXT)})
TEMPLATE_SCHEMA = _obj({"substance_id": _TEXT, "name": _TEXT, "kind": _TEXT, "cas": _nullable(_TEXT), "composition": _nullable(_TEXT),
                        "tg_prime_k": _NULL_K, "tc_k": _NULL_K, "te_k": _NULL_K, "critical_temperature_k": _NULL_K, "critical_basis": _nullable(_TEXT),
                        "values": {"type": "array", "items": _VALUE}, "evidence": _TEXT})
TEMPLATE_READ_INPUT = _obj({
    "substance_id": {"type": "string", "description": "Library substance_id (excipient/formulation) or ice_ih"},
    "temperature_k": {"anyOf": [{"type": "number", "exclusiveMinimum": 0, "maximum": 400, "x-unit": "K"}, {"type": "null"}],
                      "description": "Optional product temperature: ice vapour pressure (ice_ih) or margin to the critical temperature"},
})
TEMPLATE_READ_OUTPUT = _obj({"template": TEMPLATE_SCHEMA, "ice_vapor_pressure_pa": _NULL_PA, "margin_to_critical_k": _nullable({"type": "number", "x-unit": "K"}),
                             "product_temperature_flag": _nullable(_TEXT), "template_source": _TEXT, "library_sha256": _TEXT, "evidence": _TEXT,
                             "synthetic": {"type": "boolean"}, "limitations": _TEXTS})
REPORT_OUTPUT = _obj({
    "template_backed": {"type": "boolean"}, "formulation_id": _nullable(_TEXT), "phase": _TEXT,
    "product_temperature_k": _NULL_K, "critical_temperature_k": _NULL_K, "critical_basis": _nullable(_TEXT), "margin_to_critical_k": _nullable({"type": "number", "x-unit": "K"}),
    "product_temperature_flag": _nullable(_TEXT), "chamber_pressure_pa": _NULL_PA, "ice_vapor_pressure_product_pa": _NULL_PA, "ice_vapor_pressure_condenser_pa": _NULL_PA,
    "sublimation_driving_pressure_pa": _nullable({"type": "number", "x-unit": "Pa"}), "sublimation_thermodynamically_possible": _nullable({"type": "boolean"}),
    "wait_reason": _TEXT, "template_source": _nullable(_TEXT), "library_sha256": _nullable(_TEXT), "evidence": _TEXT, "synthetic": {"type": "boolean"},
    "synthetic_components": _TEXTS, "limitations": _TEXTS})


def library():
    return _tl.load_library(TEMPLATE_REL)


def record(substance_id):
    return _tl.get_template(TEMPLATE_REL, substance_id) if isinstance(substance_id, str) else None


def ice_vapor_pressure_pa(t_k):
    """Murphy & Koop (2005) Eq. 7, hexagonal ice; None outside 110 K < T <= 273.16 K."""
    if t_k is None or not math.isfinite(t_k) or not ICE_T_MIN_K < t_k <= ICE_T_MAX_K:
        return None
    return math.exp(MK_A + MK_B / t_k + MK_C * math.log(t_k) + MK_D * t_k)


def flag(margin):
    if margin is None:
        return None
    if margin > 0:
        return "above_critical_collapse_or_meltback_risk"
    if margin > -MARGIN_WARN_K:
        return "within_2k_below_critical"
    return "below_critical"


def public_template(rec):
    return {"substance_id": rec["substance_id"], "name": rec["name"], "kind": rec["kind"], "cas": rec.get("cas"), "composition": rec.get("composition"),
            "tg_prime_k": rec.get("tg_prime_k"), "tc_k": rec.get("tc_k"), "te_k": rec.get("te_k"), "critical_temperature_k": rec.get("critical_temperature_k"),
            "critical_basis": rec.get("critical_basis"),
            "values": [{"property": v["property"], "value_k": v["value_k"], "range_k": v.get("range_k"), "method": v.get("method"), "conditions": v.get("conditions"),
                        "source": v["source"], "locator": v.get("locator")} for v in rec.get("values", [])],
            "evidence": rec["evidence"]}


def capabilities(*, include_evaluator):
    caps = {"template.read": {
        "availability": "implemented", "visibility": "agent", "kind": "query",
        "description_zh": "公开参考查询：赋形剂/配方的文献 Tg′、塌陷温度 Tc、共晶温度 Te 与保守临界温度；ice_ih 给出 Murphy–Koop 冰蒸气压；可选温度给出冰蒸气压或距临界温度的裕度；不透露样品真实配方",
        "input_schema": copy.deepcopy(TEMPLATE_READ_INPUT), "output_schema": copy.deepcopy(TEMPLATE_READ_OUTPUT), "state_effects": [], "observes": [],
        "example_arguments": [{"substance_id": "ice_ih", "temperature_k": 243.15}],
        "binding": "basic40_runtime_vacuum.LyovaporSession.invoke(instance_id=..., capability_id='template.read', arguments=..., request_id=...)"}}
    if include_evaluator:
        caps["drying.template_report"] = {
            "availability": "implemented", "visibility": "evaluator", "kind": "query",
            "description_zh": "评估者查询：模板样品的配方、产品温度相对临界温度的裕度与塌陷标记、产品/冷凝器温度下的真实冰蒸气压与升华驱动压差",
            "input_schema": _obj({}), "output_schema": copy.deepcopy(REPORT_OUTPUT), "state_effects": [], "observes": [], "example_arguments": [{}],
            "binding": "basic40_runtime_vacuum.LyovaporSession.invoke(instance_id=..., capability_id='drying.template_report', arguments={}, request_id=...)"}
    return caps


def template_read(args):
    if not isinstance(args, dict) or set(args) != {"substance_id", "temperature_k"} or not isinstance(args["substance_id"], str):
        return None, "INVALID_ARGUMENT"
    t = args["temperature_k"]
    if t is not None and (type(t) not in (int, float) or not math.isfinite(t) or not 0 < t <= 400):
        return None, "INVALID_ARGUMENT"
    rec = record(args["substance_id"])
    if rec is None:
        return None, "UNKNOWN_REFERENCE"
    prov = _tl.provenance(TEMPLATE_REL, rec)
    crit = rec.get("critical_temperature_k")
    margin = None if t is None or crit is None else t - crit
    return {"template": public_template(rec), "ice_vapor_pressure_pa": ice_vapor_pressure_pa(t) if rec["kind"] == "equation" else None,
            "margin_to_critical_k": margin, "product_temperature_flag": flag(margin), "template_source": prov["template_source"],
            "library_sha256": prov["library_sha256"],
            "evidence": "measured_literature thermal values; ice vapour pressure, critical temperature and margin derived",
            "synthetic": False, "limitations": list(LIMITATIONS)}, None


def initialize_sample(host, instance_id, *, formulation_id, dry_mass_kg, ice_mass_kg, bound_water_mass_kg, pre_frozen, evidence):
    rec = record(formulation_id)
    if rec is None or rec["kind"] == "equation" or rec.get("critical_temperature_k") is None:
        raise ValueError("formulation_id must name a library excipient/formulation with a critical temperature")
    with host._lock:
        # The legacy collapse slot stays null: the legacy gate applies it in every phase, whereas a literature collapse /
        # eutectic temperature only matters while ice is present (primary drying), which template_gate enforces.
        host.initialize_sample(instance_id, dry_mass_kg=dry_mass_kg, ice_mass_kg=ice_mass_kg, bound_water_mass_kg=bound_water_mass_kg,
                               pre_frozen=pre_frozen, evidence=evidence, collapse_temperature_k=None)
        prov = _tl.provenance(TEMPLATE_REL, rec)
        host._instances[instance_id].template = {"formulation_id": formulation_id, "critical_temperature_k": rec["critical_temperature_k"],
                                                  "critical_basis": rec.get("critical_basis"), "template_source": prov["template_source"], "library_sha256": prov["library_sha256"]}


def template_gate(inst):
    """Extra primary-drying wait reason for a template sample with ice left, else None.

    'temperature' when the product is warmer than the library critical temperature (collapse/melt-back risk);
    'pressure' when the real ice vapour pressure at the product temperature does not exceed the chamber pressure."""
    s = inst.state
    tpl = getattr(inst, "template", None)
    if tpl is None or s["cycle.phase"] != "primary_drying" or s["sample.ice_mass_kg"] is None or s["sample.ice_mass_kg"] <= 0:
        return None
    t = s["thermal.sample_temperature_k"]
    if t is not None and t > tpl["critical_temperature_k"]:
        return "temperature"
    p_ice = ice_vapor_pressure_pa(t)
    if p_ice is None or (s["vacuum.pressure_pa"] is not None and p_ice <= s["vacuum.pressure_pa"]):
        return "pressure"
    return None


def report(inst):
    s = inst.state
    tpl = getattr(inst, "template", None)
    t, p, tc = s["thermal.sample_temperature_k"], s["vacuum.pressure_pa"], s["condenser.temperature_k"]
    p_prod, p_cond = ice_vapor_pressure_pa(t), ice_vapor_pressure_pa(tc)
    drive = None if p_prod is None or p is None else p_prod - p
    crit = None if tpl is None else tpl["critical_temperature_k"]
    margin = None if crit is None or t is None else t - crit
    return {"template_backed": tpl is not None, "formulation_id": None if tpl is None else tpl["formulation_id"], "phase": s["cycle.phase"],
            "product_temperature_k": t, "critical_temperature_k": crit, "critical_basis": None if tpl is None else tpl["critical_basis"],
            "margin_to_critical_k": margin, "product_temperature_flag": flag(margin), "chamber_pressure_pa": p,
            "ice_vapor_pressure_product_pa": p_prod, "ice_vapor_pressure_condenser_pa": p_cond, "sublimation_driving_pressure_pa": drive,
            "sublimation_thermodynamically_possible": None if drive is None or p_cond is None else bool(drive > 0 and p_cond < p),
            "wait_reason": s["cycle.wait_reason"], "template_source": None if tpl is None else tpl["template_source"],
            "library_sha256": None if tpl is None else tpl["library_sha256"],
            "evidence": "measured_literature critical temperatures (conservative minimum derived); Murphy & Koop 2005 ice vapour pressure (derived)" if tpl else
                        "no template sample: ice vapour pressures derived from host boundary temperatures only",
            "synthetic": False, "synthetic_components": list(SYNTHETIC), "limitations": list(LIMITATIONS)}
