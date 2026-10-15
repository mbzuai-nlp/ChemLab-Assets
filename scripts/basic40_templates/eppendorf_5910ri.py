"""Template-backed sedimentation reference for the Eppendorf 5910 Ri runtime (CHEM-010).

Library: ``assets/reactions/eppendorf_5910ri_sedimentation_library/templates.json``
  * 32 liquid media: density and dynamic viscosity vs temperature at 0.101325 MPa (NIST WebBook SRD 69 fluid data,
    liquid rows only; values come from NIST reference correlations);
  * 61 particles / cells / organelles / macromolecules: published (buoyant or material) densities
    (PubChem, BioNumbers, NIST SRM certificates, CC BY papers).

The centrifuge itself reports no material readout. The template is used by an agent-visible ANALYSIS capability,
``sedimentation.estimate``: for the configured method (speed, temperature) and caller-given particle diameter,
radius and path length, it evaluates Stokes settling of a rigid sphere in the medium,
    v = (rho_p - rho_f) d^2 omega^2 r / (18 mu),   s = v / (omega^2 r),   RCF = omega^2 r / g0,
with rho_f(T), mu(T) linearly interpolated inside the medium's NIST liquid range (no extrapolation).
"""
from __future__ import annotations

import json
from typing import Any

from scripts.basic40_templates import _analytical_common as common
from scripts.template_library import get_template, index_by_id

LIB_REL = "assets/reactions/eppendorf_5910ri_sedimentation_library/templates.json"
G0 = 9.80665
SVEDBERG_S = 1e-13
DIAMETER_RANGE_M = (1e-9, 1e-3)
RADIUS_RANGE_M = (0.01, 0.5)
PATH_RANGE_M = (1e-4, 0.2)

LIMITATIONS = [
    "Stokes law for a single rigid sphere at low Reynolds number in an unbounded Newtonian liquid: no hindered settling, particle-particle interaction, wall effects, diffusion, shape factor, compressibility or density gradient.",
    "Medium properties are NIST SRD 69 reference-correlation values for the PURE fluid at 0.101325 MPa, liquid range only (no extrapolation outside it); buffers, sucrose/Percoll/CsCl gradients and suspensions are not covered.",
    "Biological densities are mostly BUOYANT densities measured in a specific gradient medium; inorganic/metal values are bulk material densities, not effective nanoparticle densities.",
    "Uses the configured method speed and temperature (setpoints), not the run's acceleration/braking or actual temperature history; the radius and path length are caller inputs because rotor geometry is unknown.",
    "The estimate is an analysis, not an instrument readout; no in-run separation state is simulated.",
]
SYNTHETIC_PARTS = ["caller-declared radius, path length and particle diameter"]


def _records() -> dict[str, dict[str, Any]]:
    return index_by_id(LIB_REL)


def template_read(substance_id: Any) -> dict[str, Any] | None:
    record = get_template(LIB_REL, substance_id) if isinstance(substance_id, str) else None
    if record is None:
        return None
    base = {"substance_id": record["substance_id"], "template_kind": record["template_kind"], "name": record["name"],
            "cas": record.get("cas"), "source": record["source"], "conditions_json": json.dumps(record.get("conditions"), ensure_ascii=False)}
    if record["template_kind"] == "medium":
        base["medium"] = {"temperature_K": record["temperature_K"], "density_kg_m3": record["density_kg_m3"],
                          "viscosity_Pa_s": record["viscosity_Pa_s"], "temperature_range_K": record["temperature_range_K"],
                          "pressure_Pa": record["pressure_Pa"]}
        base["particle"] = None
    else:
        base["particle"] = {"density_kg_m3": record["density_kg_m3"], "density_range_kg_m3": record["density_range_kg_m3"],
                            "category": record["category"]}
        base["medium"] = None
    return {**base, **common.provenance([LIB_REL], record["evidence"], [], LIMITATIONS)}


def medium_properties(medium: dict[str, Any], temperature_K: float) -> tuple[float, float] | None:
    temps = medium["temperature_K"]
    density = common.interp(temperature_K, temps, medium["density_kg_m3"])
    viscosity = common.interp(temperature_K, temps, medium["viscosity_Pa_s"])
    return None if density is None or viscosity is None else (density, viscosity)


def estimate(arguments: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | str:
    """Result dict, or an error code string (INVALID_ARGUMENT / PRECONDITION_FAILED)."""
    records = _records()
    particle, medium = records.get(arguments["particle_id"]), records.get(arguments["medium_id"])
    if particle is None or medium is None or particle["template_kind"] != "particle" or medium["template_kind"] != "medium":
        return "INVALID_ARGUMENT"
    if not state.get("method.configured"):
        return "PRECONDITION_FAILED"
    omega, temperature = state["method.speed_rad_s"], state["method.temperature_k"]
    props = medium_properties(medium, temperature)
    if props is None:
        return "PRECONDITION_FAILED"
    rho_f, mu = props
    d, r, path = arguments["particle_diameter_m"], arguments["radius_m"], arguments["path_length_m"]
    if particle["density_kg_m3"] is not None:
        densities = {"central": particle["density_kg_m3"]}
    else:
        low, high = particle["density_range_kg_m3"]
        densities = {key: value for key, value in (("low", low), ("high", high)) if value is not None}
    cases = {}
    for key, rho_p in densities.items():
        delta = rho_p - rho_f
        velocity = delta * d ** 2 * omega ** 2 * r / (18.0 * mu)
        cases[key] = {"particle_density_kg_m3": rho_p, "density_difference_kg_m3": delta, "velocity_m_s": velocity,
                      "sedimentation_coefficient_s": velocity / (omega ** 2 * r),
                      "time_to_traverse_path_s": None if velocity == 0 else path / abs(velocity),
                      "direction": "sediments" if delta > 0 else ("floats" if delta < 0 else "neutral"),
                      "reynolds_number": rho_f * abs(velocity) * d / mu}
    return {"particle_id": particle["substance_id"], "medium_id": medium["substance_id"],
            "speed_rad_s": omega, "temperature_K": temperature, "radius_m": r, "particle_diameter_m": d, "path_length_m": path,
            "relative_centrifugal_force": omega ** 2 * r / G0, "medium_density_kg_m3": rho_f, "medium_viscosity_Pa_s": mu,
            "cases": cases, "model": "stokes_rigid_sphere",
            **common.provenance([LIB_REL], f"medium: {medium['evidence']} (NIST SRD 69 correlations); particle: {particle['evidence']}; settling: derived (Stokes law)",
                                SYNTHETIC_PARTS, LIMITATIONS)}


def estimate_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    case = obj({"particle_density_kg_m3": n("kg/m^3", 0), "density_difference_kg_m3": n("kg/m^3"), "velocity_m_s": n("m/s"),
                "sedimentation_coefficient_s": n("s"), "time_to_traverse_path_s": nullable(n("s", 0)),
                "direction": {"type": "string", "enum": ["sediments", "floats", "neutral"]}, "reynolds_number": n("1", 0)})
    return obj({"particle_id": common.TEXT, "medium_id": common.TEXT, "speed_rad_s": n("rad/s", 0), "temperature_K": n("K", 0),
                "radius_m": n("m", 0), "particle_diameter_m": n("m", 0), "path_length_m": n("m", 0),
                "relative_centrifugal_force": n("1", 0), "medium_density_kg_m3": n("kg/m^3", 0), "medium_viscosity_Pa_s": n("Pa*s", 0),
                "cases": {"type": "object", "additionalProperties": case, "minProperties": 1, "maxProperties": 2},
                "model": {"type": "string", "const": "stokes_rigid_sphere"}, **common.provenance_schema()})


def template_read_schema() -> dict[str, Any]:
    n, obj, nullable = common.number, common.obj, common.nullable
    series = lambda unit: {"type": "array", "items": n(unit, 0), "minItems": 1}
    medium = obj({"temperature_K": series("K"), "density_kg_m3": series("kg/m^3"), "viscosity_Pa_s": series("Pa*s"),
                  "temperature_range_K": {"type": "array", "items": n("K", 0), "minItems": 2, "maxItems": 2}, "pressure_Pa": n("Pa", 0)})
    particle = obj({"density_kg_m3": nullable(n("kg/m^3", 0)),
                    "density_range_kg_m3": nullable({"type": "array", "items": nullable(n("kg/m^3", 0)), "minItems": 2, "maxItems": 2}),
                    "category": common.TEXT})
    return obj({"substance_id": common.TEXT, "template_kind": {"type": "string", "enum": ["medium", "particle"]}, "name": common.TEXT,
                "cas": nullable(common.TEXT), "source": common.TEXT, "conditions_json": common.TEXT,
                "medium": nullable(medium), "particle": nullable(particle), **common.provenance_schema()})


def extend_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    n, obj = common.number, common.obj
    shared = {"state_ids": [], "preconditions": [], "scene_required": False, "availability": "implemented",
              "errors": ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "PRECONDITION_FAILED", "REQUEST_CONFLICT"],
              "rejection_side_effects": "none", "scene_failure_side_effects": "none", "asynchronous": False,
              "partial_failure_zh": "只读查询/计算，无状态、场景或运行副作用。", "state_effect_timing": {}}
    metadata["capabilities"]["template.read"] = {
        "function_zh": "读取公开沉降参考：介质密度/黏度-温度（NIST）或颗粒/细胞密度（文献），与当前转子内样品无关", "kind": "query",
        "input_schema": obj({"substance_id": {"type": "string", "description": "medium_* or particle_* library id"}}),
        "output_schema": template_read_schema(), "example_arguments": [{"substance_id": "medium_water"}], "component_ids": ["display"],
        "invocation": "Eppendorf5910Ri.invoke(capability_id='template.read', arguments={'substance_id': ...}, request_id=...)", **shared}
    metadata["capabilities"]["sedimentation.estimate"] = {
        "function_zh": "按当前方法转速/温度与公开介质、颗粒数据做 Stokes 沉降估算（分析，不是仪器读数）", "kind": "analysis",
        "input_schema": obj({"particle_id": {"type": "string"}, "medium_id": {"type": "string"},
                             "particle_diameter_m": n("m", *DIAMETER_RANGE_M), "radius_m": n("m", *RADIUS_RANGE_M),
                             "path_length_m": n("m", *PATH_RANGE_M)}),
        "output_schema": estimate_schema(),
        "example_arguments": [{"particle_id": "particle_escherichia_coli_cell", "medium_id": "medium_water",
                               "particle_diameter_m": 1e-6, "radius_m": 0.15, "path_length_m": 0.03}],
        "component_ids": ["display"],
        "invocation": "Eppendorf5910Ri.invoke(capability_id='sedimentation.estimate', arguments={...}, request_id=...)", **shared}
    metadata["capabilities"]["sedimentation.estimate"]["preconditions"] = [
        "Method configured (uses method.speed_rad_s and method.temperature_k).",
        "Method temperature inside the medium's NIST liquid temperature range (no extrapolation)."]
    metadata["parameters"]["template_library"] = {"value": LIB_REL, "unit": "1", "evidence": "measured_literature",
        "conditions": "NIST SRD 69 liquid density/viscosity (32 fluids) + published particle/cell densities (61)"}
    metadata["limitations"] = metadata["limitations"] + [
        "sedimentation.estimate is a Stokes-law analysis from public density/viscosity data for the configured method; centrifuge.predict_sedimentation (in-run separation model) remains unsupported."]
    return metadata
