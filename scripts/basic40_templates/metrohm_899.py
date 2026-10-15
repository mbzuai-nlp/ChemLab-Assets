"""Metrohm 899 Karl Fischer coulometer: template-backed samples from the public water-content library.

Library: ``assets/reactions/metrohm_899_water_library/templates.json`` — stoichiometric hydrates (water mass fraction
derived exactly from the formula), KF water standards and the solubility of water in water-immiscible solvents
(IUPAC-NIST Solubility Data Series).

The trusted host weighs a template material into the cell with ``Metrohm899Host.load_template_sample``: the sample
water mass is ``w(material, T) * m_sample`` and is handed to the existing synthetic coulometry process, which then
titrates it under the ideal two-electron Faraday relation m(H2O) = Q*M(H2O)/(2F). Drift, extraction, end point and
100 % current efficiency remain the existing engineering assumptions. Agent-visible reports never name the material.
"""
from __future__ import annotations

import copy
from typing import Any

from ._echem_common import (BOOL, F, TEXT, arr, get_template, index_by_id, interp_linear, matches, nullable, num, number, obj,
                            provenance, public_record)

TEMPLATE_REL = "assets/reactions/metrohm_899_water_library/templates.json"
WATER_MOLAR_MASS_KG_MOL = 0.01801528  # same constant as the KF899 runtime
KG_PER_COULOMB = WATER_MOLAR_MASS_KG_MOL / (2 * F)
TEMPLATE_CAPABILITY_IDS = ("template.list", "template.read", "coulometry.template_report")

LIMITATIONS = [
    "Hydrate water fractions are stoichiometric (derived from the formula with IUPAC atomic weights); real lots can gain or lose water (efflorescence, deliquescence, surface moisture).",
    "Solvent records give the water content of the water-saturated organic phase at the tabulated temperatures (linear interpolation only inside the table); an unsaturated solvent contains less water.",
    "KF side reactions (oxidising/reducing salts, ketones/aldehydes, strong acids/bases), incomplete extraction of crystal water and reagent capacity are not modelled.",
    "Coulometry keeps the existing ideal assumptions: 100 % current efficiency, ideal depletion end point, host-defined background drift and drift compensation.",
]


def _records() -> dict[str, dict[str, Any]]:
    return index_by_id(TEMPLATE_REL)


def material(substance_id: str) -> dict[str, Any] | None:
    return get_template(TEMPLATE_REL, substance_id)


def water_fraction(rec: dict[str, Any], temperature_k: float | None) -> tuple[float, str]:
    """Water mass fraction of the material and how it was obtained."""
    if rec.get("record_type") == "solvent_saturation":
        temps, fractions = rec.get("temperature_k"), rec.get("water_mass_fraction")
        if not isinstance(temps, list) or not isinstance(fractions, list):
            raise ValueError("solvent record without a temperature table")
        pairs = [(t, w) for t, w in zip(temps, fractions) if number(t) and number(w)]
        if temperature_k is None:
            raise ValueError("temperature required for a saturated-solvent sample")
        value = interp_linear([p[0] for p in pairs], [p[1] for p in pairs], float(temperature_k))
        exact = any(abs(p[0] - temperature_k) < 1e-9 for p in pairs)
        return value, "tabulated" if exact else "linear_interpolation"
    for key in ("water_mass_fraction", "certified_water_mass_fraction"):
        value = rec.get(key)
        if number(value):
            return float(value), key
    raise ValueError("record has no usable water mass fraction")


SAMPLE_SCHEMA = obj({"substance_id": {"type": "string", "minLength": 1, "maxLength": 128},
                     "sample_mass_kg": num("kg", exclusive_minimum=0, maximum=1.0),
                     "temperature_k": nullable(num("K", 200, 400))}, required=["substance_id", "sample_mass_kg"])


def build_sample(substance_id: str, sample_mass_kg: float, temperature_k: float | None = None) -> dict[str, Any]:
    spec = {"substance_id": substance_id, "sample_mass_kg": sample_mass_kg, "temperature_k": temperature_k}
    if not matches(SAMPLE_SCHEMA, spec):
        raise ValueError("template sample does not match schema")
    rec = material(substance_id)
    if rec is None:
        raise ValueError("unknown water-library material")
    w, how = water_fraction(rec, temperature_k)
    if not 0 <= w <= 1:
        raise ValueError("water fraction outside [0, 1]")
    water = w * float(sample_mass_kg)
    return {"spec": spec, "water_mass_fraction": w, "fraction_basis": how, "sample_water_kg": water,
            "expected_charge_c": water / KG_PER_COULOMB, "record_type": rec.get("record_type"),
            "record_provenance": provenance(TEMPLATE_REL, rec)}


def load_template_sample(host, *, instance_id, substance_id, sample_mass_kg, background_drift_kg_s, reagent_ready,
                         request_id, temperature_k=None):
    """Host-only: weigh a library material into the cell, then reuse ``Metrohm899Host.load_sample``."""
    with host._kf899_lock:
        if not isinstance(instance_id, str) or instance_id not in host._kf899_instances:
            raise ValueError("Unknown instance")
        sample = build_sample(substance_id, sample_mass_kg, temperature_k)
        requests = host.__dict__.setdefault("_kf899_template_requests", {})
        key = (instance_id, request_id)
        fingerprint = (substance_id, sample_mass_kg, temperature_k, background_drift_kg_s, reagent_ready)
        if key in requests and requests[key] != fingerprint:
            raise ValueError("REQUEST_CONFLICT")
        replay = key in requests
        host.load_sample(instance_id=instance_id, water_mass_kg=sample["sample_water_kg"],
                         background_drift_kg_s=background_drift_kg_s, reagent_ready=reagent_ready, request_id=request_id)
        if not replay:
            host._kf899_instances[instance_id]["template"] = sample
            requests[key] = fingerprint
        return {"status": "succeeded", "loaded": True, "template_backed": True, "synthetic": False}


def report(state: dict[str, Any], template: dict[str, Any] | None, *, evaluator: bool) -> dict[str, Any]:
    backed = template is not None and state.get("truth.sample_water_kg") == template["sample_water_kg"]
    result = {"template_backed": backed, "status": state["coulometry.status"], "running": state["coulometry.running"],
              "elapsed_s": state["measurement.elapsed_s"], "charge_c": state["measurement.charge_c"],
              "water_mass_kg": state["measurement.water_mass_kg"], "water_fraction": state["measurement.water_fraction"],
              "faraday_relation": "m(H2O) = Q * M(H2O) / (2 F); M(H2O) = 0.01801528 kg/mol, F = 96485.33212331001 C/mol",
              "template_source": TEMPLATE_REL if backed else None,
              "library_sha256": template["record_provenance"]["library_sha256"] if backed else None,
              "evidence": ({"sample_water_content": template["record_provenance"]["evidence"],
                            "charge_to_water": "derived (Faraday law, 2 e- per H2O)",
                            "current_efficiency": "assumption: 100 %", "end_point": "assumption: ideal depletion + hold",
                            "drift": "host-supplied background drift (assumption)"} if backed else None),
              "synthetic": not backed,
              "synthetic_parts": ["coulometric process: ideal current efficiency, end point and drift (engineering assumptions)"],
              "limitations": list(LIMITATIONS), "truth": None}
    if evaluator and backed:
        result["truth"] = {"substance_id": template["spec"]["substance_id"], "sample_mass_kg": template["spec"]["sample_mass_kg"],
                           "temperature_k": template["spec"]["temperature_k"], "water_mass_fraction": template["water_mass_fraction"],
                           "fraction_basis": template["fraction_basis"], "sample_water_kg": template["sample_water_kg"],
                           "expected_charge_c": template["expected_charge_c"],
                           "record_source": template["record_provenance"]["template_record_source"]}
    return result


READ_INPUT = obj({"substance_id": {"type": "string", "minLength": 1, "maxLength": 128}, "temperature_k": nullable(num("K", 200, 400))},
                 required=["substance_id"])
READ_OUTPUT = obj({"record": {"type": "object"}, "template_source": TEXT, "template_record_source": nullable(TEXT),
                   "evidence": nullable(TEXT), "library_sha256": TEXT, "water_mass_fraction_at_temperature": nullable(num("1", 0, 1))})
LIST_OUTPUT = obj({"records": arr(obj({"substance_id": TEXT, "record_type": TEXT, "name": TEXT})),
                   "template_source": TEXT, "library_sha256": TEXT})
REPORT_OUTPUT = obj({"template_backed": BOOL, "status": TEXT, "running": BOOL, "elapsed_s": num("s", 0), "charge_c": num("C", 0),
                     "water_mass_kg": nullable(num("kg", 0)), "water_fraction": nullable(num("1", 0)), "faraday_relation": TEXT,
                     "template_source": nullable(TEXT), "library_sha256": nullable(TEXT),
                     "evidence": nullable({"type": "object", "additionalProperties": TEXT}), "synthetic": BOOL,
                     "synthetic_parts": arr(TEXT), "limitations": arr(TEXT), "truth": nullable({"type": "object"})})


def capability_metadata() -> dict[str, dict[str, Any]]:
    """Describe entries in the Metrohm899 metadata style."""
    def entry(name, inputs, outputs, example, meaning):
        return {"input_schema": inputs, "output_schema": outputs, "example_arguments": [example], "meaning_zh": meaning,
                "kind": "query", "visibility": "agent", "availability": "implemented", "state_effects": [], "observes": [],
                "binding": {"backend": "python", "artifact_id": "runtime",
                            "invocation": f"Metrohm899Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)"},
                "template_source": TEMPLATE_REL}
    return {
        "template.list": entry("template.list", obj({}), LIST_OUTPUT, {}, "列出公开含水参考库条目（水合物、KF水标准、饱和含水溶剂）；不透露当前样品。"),
        "template.read": entry("template.read", READ_INPUT, READ_OUTPUT, {"substance_id": "copper_ii_sulfate_pentahydrate", "temperature_k": None},
                               "读取一条公开含水参考记录及来源证据；给定温度时返回该温度下的质量分数（溶剂表内线性插值）。"),
        "coulometry.template_report": entry("coulometry.template_report", obj({}), REPORT_OUTPUT, {},
                                            "返回当前库仑测量结果与模板来源、证据等级和假设清单；agent 不可见样品身份与真值水量。"),
    }


def invoke(session, host, instance_id: str, capability_id: str, arguments: dict[str, Any], request_id: str, *, evaluator: bool):
    meta = capability_metadata()[capability_id]
    if not matches(meta["input_schema"], arguments):
        return session._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
    if capability_id == "template.list":
        rows = [{"substance_id": sid, "record_type": rec.get("record_type", ""), "name": rec.get("name", "")} for sid, rec in _records().items()]
        sha = provenance(TEMPLATE_REL, next(iter(_records().values())))["library_sha256"]
        return session._kf899_response(instance_id, request_id, result={"records": rows, "template_source": TEMPLATE_REL, "library_sha256": sha})
    if capability_id == "template.read":
        rec = material(arguments["substance_id"])
        if rec is None:
            return session._kf899_response(instance_id, request_id, "rejected", code="INVALID_ARGUMENT")
        result = public_record(TEMPLATE_REL, rec)
        try:
            result["water_mass_fraction_at_temperature"] = water_fraction(rec, arguments.get("temperature_k"))[0]
        except ValueError:
            result["water_mass_fraction_at_temperature"] = None
        return session._kf899_response(instance_id, request_id, result=result)
    instance = host._kf899_instances[instance_id]
    return session._kf899_response(instance_id, request_id,
                                   result=report(instance["state"], instance.get("template"), evaluator=evaluator))
