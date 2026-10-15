"""Template-backed static-headspace path for the Agilent 8697 runtime (CHEM-108).

Library: ``assets/reactions/agilent_hs8697_headspace_library/templates.json`` — Sander (2023) measured Henry
solubility constants Hscp = c_aq / p (water, infinite dilution, 298.15 K) with the temperature coefficient
B = -d ln Hscp / d(1/T) where published (68 of 77 spectral_library compounds).

Physics for a sealed vial with an aqueous sample (two-phase closed system at equilibrium):
    Hscp(T) = Hscp(298.15) * exp(B (1/T - 1/298.15))
    K(T)    = c_liquid / c_gas = Hscp(T) * R * T                        (dimensionless partition coefficient)
    beta    = V_gas / V_liquid                                          (phase ratio)
    c_gas   = c0 / (K + beta)                                           (mass balance c0 V_L = c_L V_L + c_G V_G)
The headspace transferred to the GC is c_gas x loop volume (host-declared assumption, the vendor loop volume is
unknown) and is chromatographed with the 8890B template GC model (spectral_library/gc_retention.json fingerprints).
"""
from __future__ import annotations

import copy
import json
import math
from typing import Any

from scripts.basic40_templates import _analytical_common as common
from scripts.basic40_templates import agilent_gc8890b as gc_templates
from scripts.template_library import get_template, index_by_id

LIB_REL = "assets/reactions/agilent_hs8697_headspace_library/templates.json"
R = 8.31446261815324
T_REF = 298.15
T_REF_TOLERANCE_K = 0.5
MAX_SPECIES = 16
MAX_INITIAL_CONCENTRATION_MOL_M3 = 100.0   # 0.1 mol/L: keep the infinite-dilution Henry law plausible
VIAL_VOLUMES_M3 = (10e-6, 20e-6, 22e-6)

LIMITATIONS = [
    "Sander (2023) Henry constants are for pure water at infinite dilution; salting-out, co-solvents, matrix effects, non-ideal (concentrated) solutions and adsorption on vial/septum are not modelled.",
    "Temperature dependence uses the published van 't Hoff coefficient B; records without B are usable only at 298.15 +/- 0.5 K (no extrapolation). Extrapolation of B to headspace oven temperatures far above 298 K is itself an approximation.",
    "Complete equilibrium is assumed at the oven setpoint; the configured equilibration time is not used to model mass-transfer kinetics, and shaking/pressurization are not modelled.",
    "Loop volume and GC method of the transfer are trusted-host assumptions (the 8697 loop volume is unknown); vial pressurization/dilution of the loop gas is ignored.",
    "The chromatogram uses the 8890B template GC model (NIST retention indices -> derived fingerprints with an assumed column calibration); species without a GC fingerprint for the selected column are transferred but not chromatographed.",
    "Selection of one Sander record per compound is transparent (M-type closest to the median of all M-type values) but is not a new critical evaluation; alternatives are summarised in the library.",
]
SYNTHETIC_PARTS = ["loop volume (host assumption)", "GC transfer method (host assumption)"] + gc_templates.SYNTHETIC_PARTS


def _resolve(identifier: Any) -> str | None:
    if not isinstance(identifier, str) or not identifier:
        return None
    index = index_by_id(LIB_REL)
    if identifier in index:
        return identifier
    for sid, record in index.items():
        if identifier in (record.get("shared_ids") or {}).values():
            return sid
    return None


def henry_at(record: dict[str, Any], temperature_K: float) -> float | None:
    """Hscp(T) [mol m^-3 Pa^-1] or None when T is outside the record's usable range."""
    b = record["henry_temperature_coefficient_K"]
    if b is None:
        return record["henry_hscp_mol_m3_pa"] if abs(temperature_K - T_REF) <= T_REF_TOLERANCE_K else None
    return record["henry_hscp_mol_m3_pa"] * math.exp(b * (1.0 / temperature_K - 1.0 / T_REF))


def template_read(substance_id: Any) -> dict[str, Any] | None:
    sid = _resolve(substance_id)
    record = get_template(LIB_REL, sid) if sid else None
    if record is None:
        return None
    return {"substance_id": sid, "name": record["name"], "name_zh": record["name_zh"], "cas": record["cas"],
            "molar_mass_kg_mol": record["molar_mass_kg_mol"], "henry_hscp_mol_m3_pa": record["henry_hscp_mol_m3_pa"],
            "henry_temperature_coefficient_K": record["henry_temperature_coefficient_K"], "reference_temperature_K": T_REF,
            "partition_coefficient_K_298": record["partition_coefficient_K_298"],
            "alternatives_measured": record["alternatives_measured"], "conditions_json": json.dumps(record["conditions"], ensure_ascii=False),
            "source": record["source"], "gc_retention_id": record["gc_link"]["gc_retention_id"],
            **common.provenance([LIB_REL], record["evidence"] + " (Hscp, B); K_298 derived = Hscp*R*T", SYNTHETIC_PARTS, LIMITATIONS)}


def validate_sample(composition_mol_m3: Any, *, liquid_volume_m3: float, vial_volume_m3: float) -> dict[str, Any]:
    if vial_volume_m3 not in VIAL_VOLUMES_M3:
        raise ValueError("vial volume must be 10/20/22 mL in m^3")
    if not common.finite(liquid_volume_m3) or not 0 < liquid_volume_m3 < vial_volume_m3:
        raise ValueError("liquid_volume_m3 must be positive and smaller than the vial volume (a headspace is required)")
    if not isinstance(composition_mol_m3, dict) or not 1 <= len(composition_mol_m3) <= MAX_SPECIES:
        raise ValueError(f"composition_mol_m3 must map 1..{MAX_SPECIES} library ids to initial aqueous mol/m^3")
    out = {}
    for identifier, concentration in composition_mol_m3.items():
        sid = _resolve(identifier)
        if sid is None:
            raise ValueError(f"{identifier!r} has no Henry record in {LIB_REL}")
        if sid in out:
            raise ValueError(f"duplicate compound {sid!r}")
        if not common.finite(concentration) or not 0 < concentration <= MAX_INITIAL_CONCENTRATION_MOL_M3:
            raise ValueError(f"initial concentration for {identifier!r} must be in (0, {MAX_INITIAL_CONCENTRATION_MOL_M3}] mol/m^3")
        out[sid] = float(concentration)
    return {"library": LIB_REL, "composition_mol_m3": out, "liquid_volume_m3": float(liquid_volume_m3),
            "vial_volume_m3": float(vial_volume_m3), "solvent": "water"}


def validate_transfer(*, loop_volume_m3: float, gc_column: str = "nonpolar", gc_oven_temperature_K: float = 353.15,
                      gc_inlet_temperature_K: float = 523.15, gc_fid_temperature_K: float = 573.15,
                      split_ratio: float = 20.0) -> dict[str, Any]:
    if not common.finite(loop_volume_m3) or not 1e-8 <= loop_volume_m3 <= 1e-5:
        raise ValueError("loop_volume_m3 must be within 0.01..10 mL (host assumption)")
    if gc_column not in gc_templates.COLUMNS:
        raise ValueError("gc_column must be nonpolar or polar")
    for value in (gc_oven_temperature_K, gc_inlet_temperature_K, gc_fid_temperature_K):
        if not common.finite(value) or not 273.15 <= value <= 723.15:
            raise ValueError("GC temperatures must be within 273.15..723.15 K")
    if gc_fid_temperature_K < gc_templates.FID_IGNITION_MINIMUM_K:
        raise ValueError("FID temperature below the gc_runtime ignition minimum")
    if not common.finite(split_ratio) or not 0 < split_ratio <= 1000:
        raise ValueError("split_ratio must be in (0, 1000]")
    return {"loop_volume_m3": float(loop_volume_m3), "gc_column": gc_column, "gc_oven_temperature_K": float(gc_oven_temperature_K),
            "gc_inlet_temperature_K": float(gc_inlet_temperature_K), "gc_fid_temperature_K": float(gc_fid_temperature_K),
            "split_ratio": float(split_ratio), "evidence": "assumption"}


def equilibrium(template: dict[str, Any], temperature_K: float, loop_volume_m3: float | None = None) -> dict[str, Any] | None:
    """Per-species equilibrium headspace (trusted-host detail); None if any species lacks data at temperature_K."""
    beta = (template["vial_volume_m3"] - template["liquid_volume_m3"]) / template["liquid_volume_m3"]
    species = {}
    for sid, c0 in template["composition_mol_m3"].items():
        record = get_template(LIB_REL, sid)
        hscp = henry_at(record, temperature_K)
        if hscp is None:
            return None
        k = hscp * R * temperature_K
        c_gas = c0 / (k + beta)
        entry = {"henry_hscp_mol_m3_pa": hscp, "partition_coefficient_K": k, "gas_concentration_mol_m3": c_gas,
                 "liquid_concentration_mol_m3": k * c_gas, "partial_pressure_pa": c_gas * R * temperature_K,
                 "fraction_in_gas": c_gas * beta / c0, "molar_mass_kg_mol": record["molar_mass_kg_mol"],
                 "gc_retention_id": record["gc_link"]["gc_retention_id"]}
        if loop_volume_m3 is not None:
            entry["loop_mol"] = c_gas * loop_volume_m3
            entry["loop_mass_kg"] = c_gas * loop_volume_m3 * record["molar_mass_kg_mol"]
        species[sid] = entry
    return {"temperature_K": temperature_K, "phase_ratio": beta, "vial_volume_m3": template["vial_volume_m3"],
            "liquid_volume_m3": template["liquid_volume_m3"], "loop_volume_m3": loop_volume_m3, "species": species}


def measure(template: dict[str, Any], transfer: dict[str, Any] | None, oven_temperature_K: float,
            equilibration_time_s: float | None) -> dict[str, Any] | None:
    """Agent-visible result for a template-backed vial; None when a precondition of the template path fails."""
    if transfer is None:
        return None
    eq = equilibrium(template, oven_temperature_K, transfer["loop_volume_m3"])
    if eq is None:
        return None
    composition = {sid: item["loop_mass_kg"] for sid, item in eq["species"].items()
                   if item["gc_retention_id"] and gc_templates.fingerprint(sid, transfer["gc_column"]) and item["loop_mass_kg"] > 0}
    if not composition:
        return None
    gc_template = gc_templates.validate_sample(composition, column=transfer["gc_column"], injection_fraction=1.0)
    run = gc_templates.simulate(gc_template, oven_K=transfer["gc_oven_temperature_K"], inlet_K=transfer["gc_inlet_temperature_K"],
                                fid_K=transfer["gc_fid_temperature_K"], split_ratio=transfer["split_ratio"])
    if run is None:
        return None
    return {"chromatogram": run["public"],
            "headspace": {"oven_temperature_K": oven_temperature_K, "vial_volume_m3": eq["vial_volume_m3"],
                          "phase_ratio": eq["phase_ratio"], "loop_volume_m3": transfer["loop_volume_m3"],
                          "equilibration_time_s": equilibration_time_s, "equilibrium_assumed": True,
                          "gc_column_model": transfer["gc_column"], "gc_oven_temperature_K": transfer["gc_oven_temperature_K"],
                          "split_ratio": transfer["split_ratio"]},
            **common.provenance([LIB_REL, gc_templates.LIB_REL],
                                "Henry constants: Sander 2023 measured_literature; partition/transfer: derived; GC: NIST RI -> derived fingerprints",
                                SYNTHETIC_PARTS, LIMITATIONS)}


def result_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    headspace = obj({"oven_temperature_K": n("K", 0), "vial_volume_m3": n("m3", 0), "phase_ratio": n("1", 0),
                     "loop_volume_m3": n("m3", 0), "equilibration_time_s": nullable(n("s", 0)),
                     "equilibrium_assumed": {"type": "boolean", "const": True},
                     "gc_column_model": {"type": "string", "enum": list(gc_templates.COLUMNS)},
                     "gc_oven_temperature_K": n("K", 0), "split_ratio": n("1", 0)})
    return obj({"chromatogram": gc_templates.chromatogram_schema(), "headspace": headspace, **common.provenance_schema()})


def template_read_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    alternatives = obj({"n": {"type": "integer", "minimum": 1, "x-unit": "1"}, "n_with_temperature_coefficient": {"type": "integer", "minimum": 0, "x-unit": "1"},
                        "hscp_median": n("mol/(m^3*Pa)", 0), "hscp_min": n("mol/(m^3*Pa)", 0), "hscp_max": n("mol/(m^3*Pa)", 0)})
    return obj({"substance_id": common.TEXT, "name": common.TEXT, "name_zh": common.TEXT, "cas": nullable(common.TEXT),
                "molar_mass_kg_mol": n("kg/mol", 0), "henry_hscp_mol_m3_pa": n("mol/(m^3*Pa)", 0),
                "henry_temperature_coefficient_K": nullable(n("K")), "reference_temperature_K": n("K", 0),
                "partition_coefficient_K_298": n("1", 0), "alternatives_measured": alternatives, "conditions_json": common.TEXT,
                "source": common.TEXT, "gc_retention_id": nullable(common.TEXT), **common.provenance_schema()})


def extend_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    caps, slots = metadata["capabilities"], metadata["state_slots"]
    legacy_output = caps["measure"]["output_schema"]
    caps["measure"]["output_schema"] = {"anyOf": [legacy_output, result_schema()]}
    caps["measure"]["preconditions"] = caps["measure"]["preconditions"] + [
        "Template-backed vial only: host configure_template_transfer() done; every species has Henry data at the oven setpoint (B published, or setpoint 298.15 +/- 0.5 K); at least one species has a GC fingerprint."]
    legacy_chromatogram = slots["measurement.chromatogram"]["value_schema"]["anyOf"][0]
    slots["measurement.chromatogram"]["value_schema"] = {"anyOf": [legacy_chromatogram, gc_templates.chromatogram_schema(), {"type": "null"}]}
    log_item = slots["injection.log"]["value_schema"]["items"]
    template_log = copy.deepcopy(log_item)
    template_log["properties"]["synthetic"] = {"type": "boolean", "const": False}
    slots["injection.log"]["value_schema"]["items"] = {"anyOf": [log_item, template_log]}
    status = slots["vial.status"]["value_schema"]["items"]["properties"]["status"]
    status["enum"] = status["enum"] + ["measured_template"]
    cap = common.template_read_capability(
        "AgilentHS8697", "toluene", template_read_schema(), ["display"],
        "Henry-law library id (spectral_library compound id); does not refer to any loaded vial")
    cap["errors"] = ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "REQUEST_CONFLICT"]
    caps["template.read"] = cap
    metadata["parameters"]["template_library"] = {"value": LIB_REL, "unit": "1", "evidence": "measured_literature",
        "conditions": "Sander (2023) Henry solubility constants, water, infinite dilution; 68/77 with temperature coefficient"}
    metadata["parameters"]["template_loop_volume_m3"] = {"value": None, "unit": "m3", "evidence": "unknown",
        "conditions": "Must be declared by the trusted host via configure_template_transfer(loop_volume_m3=..., evidence='assumption')"}
    metadata["limits"] = metadata["limits"] + [
        "Template-backed vials (host register_template_sample + configure_template_transfer) use Henry-law partition K(T) and phase ratio to compute the headspace and chromatograph it with the 8890B template GC model; the synthetic fixture path is unchanged and remains the default."]
    return metadata
