"""CHEM-180 Alicat MC (1 SLPM, Rev6): gas-property templates for the laminar-DP flow model.

Library: ``assets/reactions/alicat_mc_gas_property_library/templates.json``

* ``kind == "pure"`` records hold NIST Chemistry WebBook (SRD 69) density, viscosity and phase on a
  temperature x pressure grid (253.15-343.15 K, 0.01-1.2 MPa) plus the NIST saturation curve.
* ``kind == "mixture"`` records hold a published composition (Alicat Gas Select names / compositions,
  dry air after Lemmon et al. 2000) and are evaluated from their NIST pure components with Dalton's rule
  for density and Wilke's rule for viscosity (evidence ``derived``).

Physics used by the runtime hook in ``scripts/basic40_runtime_automation.py`` (AlicatMCRuntime):

* standard <-> actual volumetric flow and mass flow use real-gas densities of the *selected* gas:
  ``Q_act = Q_std * rho(T_ref, P_ref) / rho(T, P_up)``, ``m_dot = Q_std * rho(T_ref, P_ref)``.
* the laminar flow element is modelled as an ideal Poiseuille element (``dP = C * mu * Q_act``; the
  constant C is never needed because it cancels).  If the host declares that a different gas is actually
  in the line, the instrument still divides by the *selected* gas viscosity, so the indicated standard
  flow is ``k * Q_std,true`` with
  ``k = (mu_line/mu_sel) * (rho_act,sel/rho_std,sel) * (rho_std,line/rho_act,line)``; the controller servos
  on the indication, i.e. the true flow approaches ``setpoint / k``.  The ideal LFE, the absence of
  sensor noise/drift and the host-given dynamics are assumptions, not template data.

Nothing here reveals the host-declared line gas to an agent: agent results only contain indicated
values and properties of the gas the agent itself selected.
"""
from __future__ import annotations

import copy
import json
import math

try:  # scripts/ on sys.path (tests, Matterix bridge)
    import template_library as _tl
except ImportError:  # project root on sys.path
    from scripts import template_library as _tl

TEMPLATE_REL = "assets/reactions/alicat_mc_gas_property_library/templates.json"
R_J_MOL_K = 8.31446261815324
FULL_SCALE_M3_S = 1e-3 / 60
CAPABILITY_IDS = ("template.list", "template.read", "flow.read_template")
LINE_KEY = "template_line_gas_id"
TOTALIZER_KEY = "template_totalizer"
ENABLED_ATTR = "_amc_template_enabled_gases"
CONTRACT_ASSET_VERSION = "1.1.0"

LIMITATIONS = [
    "Pure-gas density/viscosity are NIST SRD 69 reference-correlation values on a 253.15-343.15 K x 0.01-1.2 MPa grid; values between nodes are linear interpolations of Z and viscosity (derived).",
    "Mixtures (incl. dry air) are derived from NIST pure components: Dalton additive pressures for density and Wilke (1950) for viscosity; Alicat's own REFPROP 10 mixture values are not copied and may differ by about 1-2 % in viscosity.",
    "Condensation is flagged from the NIST saturation curve (pure) or the ideal dew-point sum (mixtures); readings become unavailable instead of extrapolating into two-phase states.",
    "The laminar flow element is an ideal Poiseuille element (dP proportional to viscosity x actual flow); sensor noise, drift, non-linearity, valve capacity and PID are not modelled (assumption).",
    "Acetylene and the S-Series-only gases without a NIST WebBook fluid model (1-butene, cis/trans-2-butene, isobutene, Cl2, DME, NO, SiH4) have no record.",
]
SYNTHETIC_COMPONENTS = [
    "flow dynamics: host-declared first-order tau (assumption)",
    "laminar element: ideal Poiseuille dP = C*mu*Q_actual (assumption; C cancels)",
    "no sensor noise, drift or calibration error model",
]


# ----------------------------------------------------------------------------- library access
def library():
    return _tl.load_library(TEMPLATE_REL)


def index():
    return _tl.index_by_id(TEMPLATE_REL)


def available():
    """False when the library file is missing/unreadable: every hook then falls back to the legacy behaviour."""
    try:
        library()
    except (OSError, ValueError):
        return False
    return True


def record(gas_id):
    return index().get(gas_id) if isinstance(gas_id, str) and available() else None


def resolve(gas_id):
    """Library ID for a library ID or a shared ledger ID (substances.json / spectral_library via shared_ids)."""
    if record(gas_id) is not None:
        return gas_id
    if not isinstance(gas_id, str) or not available():
        return None
    for rec in library()["templates"]:
        if gas_id in (rec.get("shared_ids") or {}).values():
            return rec["substance_id"]
    return None


def selectable_without_host(rec):
    return rec is not None and rec.get("alicat", {}).get("gas_select") == "standard_mc"


def _finite(value):
    return value if isinstance(value, float) and math.isfinite(value) else None


# ----------------------------------------------------------------------------- property evaluation
def _bracket(xs, x):
    """Index i with xs[i] <= x <= xs[i+1] (xs ascending, len >= 2) or None."""
    if not (xs[0] <= x <= xs[-1]):
        return None
    for i in range(len(xs) - 1):
        if x <= xs[i + 1]:
            return i
    return len(xs) - 2


def _saturation(rec, temperature_K):
    """(p_sat, Z_vap, mu_vap) at T from the NIST saturation curve; None above Tc or without data."""
    sat = rec.get("saturation")
    if not sat:
        return None
    ts = sat["temperature_K"]
    i = _bracket(ts, temperature_K)
    if i is None:
        return None
    t0, t1 = ts[i], ts[i + 1]
    p0, p1 = sat["pressure_Pa"][i], sat["pressure_Pa"][i + 1]
    # ln p linear in 1/T (Clausius-Clapeyron form) between adjacent NIST saturation points
    w_inv = (1.0 / temperature_K - 1.0 / t0) / (1.0 / t1 - 1.0 / t0) if t1 != t0 else 0.0
    psat = math.exp(math.log(p0) + w_inv * (math.log(p1) - math.log(p0)))
    w = (temperature_K - t0) / (t1 - t0) if t1 != t0 else 0.0
    m = rec["molar_mass_kg_mol"]
    d0, d1 = sat["vapor_density_kg_m3"][i], sat["vapor_density_kg_m3"][i + 1]
    z0 = p0 * m / (d0 * R_J_MOL_K * t0)
    z1 = p1 * m / (d1 * R_J_MOL_K * t1)
    v0, v1 = sat["vapor_viscosity_Pa_s"][i], sat["vapor_viscosity_Pa_s"][i + 1]
    mu = None if v0 is None or v1 is None else v0 + w * (v1 - v0)
    return psat, z0 + w * (z1 - z0), mu


def _pure_state(rec, temperature_K, pressure_Pa):
    grid = rec["grid"]
    ts, ps = grid["temperature_K"], grid["pressure_Pa"]
    if not (isinstance(temperature_K, (int, float)) and isinstance(pressure_Pa, (int, float))):
        return None
    if not (math.isfinite(temperature_K) and math.isfinite(pressure_Pa)) or pressure_Pa <= 0:
        return None
    i = _bracket(ts, temperature_K)
    if i is None:
        return None
    sat = _saturation(rec, temperature_K)
    psat = sat[0] if sat else None
    if psat is not None and pressure_Pa >= psat:
        return None  # liquid or two-phase at the requested state
    m = rec["molar_mass_kg_mol"]
    t0, t1 = ts[i], ts[i + 1]
    w = (temperature_K - t0) / (t1 - t0)
    nodes = []
    for k, pk in enumerate(ps):
        if psat is not None and pk >= psat:
            continue
        d0, d1 = grid["density_kg_m3"][k][i], grid["density_kg_m3"][k][i + 1]
        v0, v1 = grid["viscosity_Pa_s"][k][i], grid["viscosity_Pa_s"][k][i + 1]
        if d0 is not None and d1 is not None:
            z0 = pk * m / (d0 * R_J_MOL_K * t0)
            z1 = pk * m / (d1 * R_J_MOL_K * t1)
            nodes.append((pk, z0 + w * (z1 - z0), None if v0 is None or v1 is None else v0 + w * (v1 - v0)))
        elif w == 0.0 and d0 is not None:  # exactly on a grid temperature: the other neighbour is irrelevant
            nodes.append((pk, pk * m / (d0 * R_J_MOL_K * t0), v0))
        elif w == 1.0 and d1 is not None:
            nodes.append((pk, pk * m / (d1 * R_J_MOL_K * t1), v1))
    if sat is not None:
        nodes.append((psat, sat[1], sat[2]))
    if not nodes:
        return None
    nodes.sort(key=lambda n: n[0])
    if pressure_Pa < nodes[0][0]:
        # second-virial limit: Z -> 1 linearly as P -> 0; dilute-gas viscosity is pressure independent
        p0, z0, mu0 = nodes[0]
        z, mu = 1.0 + (z0 - 1.0) * pressure_Pa / p0, mu0
    elif pressure_Pa > nodes[-1][0]:
        return None
    else:
        j = _bracket([n[0] for n in nodes], pressure_Pa)
        (pa, za, ma), (pb, zb, mb) = nodes[j], nodes[j + 1]
        u = (pressure_Pa - pa) / (pb - pa) if pb != pa else 0.0
        z = za + u * (zb - za)
        mu = None if ma is None or mb is None else ma + u * (mb - ma)
    density = pressure_Pa * m / (z * R_J_MOL_K * temperature_K)
    return {"density_kg_m3": density, "viscosity_Pa_s": mu, "compressibility_factor": z, "molar_mass_kg_mol": m}


def wilke_viscosity(parts):
    """Wilke (1950) mixing rule; parts = [(mole_fraction, molar_mass, viscosity), ...]."""
    total = 0.0
    for xi, mi, ui in parts:
        denominator = 0.0
        for xj, mj, uj in parts:
            phi = (1.0 + math.sqrt(ui / uj) * (mj / mi) ** 0.25) ** 2 / math.sqrt(8.0 * (1.0 + mi / mj))
            denominator += xj * phi
        total += xi * ui / denominator
    return total


def _mixture_state(rec, temperature_K, pressure_Pa):
    if not (isinstance(pressure_Pa, (int, float)) and math.isfinite(pressure_Pa) and pressure_Pa > 0):
        return None
    density, dew, parts, molar_mass = 0.0, 0.0, [], 0.0
    for gas_id, x in rec["composition_mole_fraction"].items():
        component = index()[gas_id]
        partial = x * pressure_Pa
        state = _pure_state(component, temperature_K, partial)
        if state is None:
            return None
        sat = _saturation(component, temperature_K)
        if sat is not None:
            dew += partial / sat[0]
        density += state["density_kg_m3"]
        molar_mass += x * component["molar_mass_kg_mol"]
        parts.append((x, component["molar_mass_kg_mol"], state["viscosity_Pa_s"]))
    if dew >= 1.0:
        return None  # ideal (Raoult) dew point reached: condensation possible
    mu = None if any(p[2] is None for p in parts) else wilke_viscosity(parts)
    z = pressure_Pa * molar_mass / (density * R_J_MOL_K * temperature_K)
    return {"density_kg_m3": density, "viscosity_Pa_s": mu, "compressibility_factor": z, "molar_mass_kg_mol": molar_mass}


def gas_state(gas_id, temperature_K, pressure_Pa):
    """Density, viscosity and Z of a library gas at (T, P); None when outside the data or condensed."""
    rec = record(gas_id)
    if rec is None:
        return None
    try:
        if rec["kind"] == "pure":
            return _pure_state(rec, temperature_K, pressure_Pa)
        return _mixture_state(rec, temperature_K, pressure_Pa)
    except (ZeroDivisionError, OverflowError, ValueError):
        return None


# ----------------------------------------------------------------------------- runtime hooks
def is_selectable(host, gas_id):
    rec = record(gas_id)
    return rec is not None and (selectable_without_host(rec) or gas_id in getattr(host, ENABLED_ATTR, {}))


def host_enable_library_gas(host, gas_id, *, compatibility_evidence):
    """Trusted host: allow a library gas outside the standard-MC Gas Select list (e.g. S-Series hardware)."""
    with host._lock:
        if record(gas_id) is None or not isinstance(compatibility_evidence, str) or not compatibility_evidence.strip():
            raise ValueError("Known library gas ID and explicit compatibility evidence required")
        enabled = dict(getattr(host, ENABLED_ATTR, {}))
        enabled[gas_id] = compatibility_evidence
        setattr(host, ENABLED_ATTR, enabled)


def host_set_line_gas(host, instance_id, *, gas_id):
    """Trusted host: declare which library gas is physically in the line (None = the selected gas)."""
    with host._lock:
        inst = host._require(instance_id)
        if gas_id is not None:
            if resolve(gas_id) is None:
                raise ValueError("Line gas must be a library gas ID, a shared ledger ID (e.g. substances.json 'oxygen') or None")
            gas_id = resolve(gas_id)
        values = copy.deepcopy(inst["values"])
        if inst.get(LINE_KEY) == gas_id:
            return
        inst[LINE_KEY] = gas_id
        total = values["measurement.total_standard_volume_m3"]
        truth_total = values["truth.total_standard_volume_m3"]
        inst[TOTALIZER_KEY] = None if truth_total is None or total is None else {"truth_m3": truth_total, "indicated_m3": total}
        host._commit(inst, values)


def template_backed(host, values, inst):
    gas = values["gas.selected_id"]
    return (gas is not None and gas not in host._gases) or (inst is not None and inst.get(LINE_KEY) is not None)


def indication_factor(host, values, inst):
    """k such that indicated standard flow = k * true standard flow of the line gas; None if unknown."""
    selected, line = values["gas.selected_id"], inst.get(LINE_KEY)
    if selected in host._gases:
        return 1.0 if line is None else None
    temperature, pressure = values["truth.temperature_K"], values["truth.upstream_pressure_Pa"]
    selected_actual = gas_state(selected, temperature, pressure)
    if selected_actual is None:
        return None
    if line is None or line == selected:
        return 1.0
    t_ref, p_ref = values["gas.reference_temperature_K"], values["gas.reference_pressure_Pa"]
    line_actual = gas_state(line, temperature, pressure)
    selected_std, line_std = gas_state(selected, t_ref, p_ref), gas_state(line, t_ref, p_ref)
    states = (selected_actual, line_actual, selected_std, line_std)
    if any(s is None for s in states) or selected_actual["viscosity_Pa_s"] is None or line_actual["viscosity_Pa_s"] is None:
        return None
    factor = (line_actual["viscosity_Pa_s"] / selected_actual["viscosity_Pa_s"]
              * selected_actual["density_kg_m3"] / selected_std["density_kg_m3"]
              * line_std["density_kg_m3"] / line_actual["density_kg_m3"])
    return factor if math.isfinite(factor) and factor > 0 else None


def totalize(inst, truth_total_before, integral, factor):
    """Accumulate the indicated totalizer (sum of k * dV_true) next to the true standard volume."""
    tracker = inst.get(TOTALIZER_KEY)
    base = tracker["indicated_m3"] if tracker and tracker["truth_m3"] == truth_total_before else truth_total_before * factor
    inst[TOTALIZER_KEY] = {"truth_m3": truth_total_before + integral, "indicated_m3": base + factor * integral}


def refresh_readings(host, values, inst):
    """Fill measurement.* flow readings for a template-backed selection (pressure/temperature already set)."""
    factor = indication_factor(host, values, inst)
    if factor is None:
        return
    q_true = values["truth.standard_flow_m3_s"]
    q = _finite(float(q_true * factor))
    values["measurement.standard_flow_m3_s"] = q
    selected = values["gas.selected_id"]
    if q is not None and selected not in host._gases:
        actual = gas_state(selected, values["truth.temperature_K"], values["truth.upstream_pressure_Pa"])
        standard = gas_state(selected, values["gas.reference_temperature_K"], values["gas.reference_pressure_Pa"])
        if actual is not None and standard is not None:
            values["measurement.actual_flow_m3_s"] = _finite(float(q * standard["density_kg_m3"] / actual["density_kg_m3"]))
        if standard is not None:
            values["measurement.mass_flow_kg_s"] = _finite(float(q * standard["density_kg_m3"]))
    truth_total = values["truth.total_standard_volume_m3"]
    if truth_total is None:
        inst[TOTALIZER_KEY] = None
    else:
        tracker = inst.get(TOTALIZER_KEY)
        if not tracker or tracker["truth_m3"] != truth_total:
            tracker = {"truth_m3": truth_total, "indicated_m3": truth_total * factor}
            inst[TOTALIZER_KEY] = tracker
        values["measurement.total_standard_volume_m3"] = _finite(float(tracker["indicated_m3"]))
    if values["truth.equilibrium"] and q is not None:
        values["measurement.accuracy_conditions_met"] = True
        values["measurement.standard_accuracy_bound_m3_s"] = max(0.006 * abs(q), 0.001 * FULL_SCALE_M3_S)


# ----------------------------------------------------------------------------- agent capabilities
def _num(unit, nullable=True, minimum=None):
    schema = {"type": "number", "x-unit": unit}
    if minimum is not None:
        schema["minimum"] = minimum
    return {"anyOf": [schema, {"type": "null"}]} if nullable else schema


def _obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_TEXT = {"type": "string"}
_NULL_TEXT = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_GAS_SELECT_ENUM = {"type": "string", "enum": ["standard_mc", "s_series_only", "not_listed"]}
_LIST_ITEM = _obj({"substance_id": _TEXT, "name": _TEXT, "kind": {"type": "string", "enum": ["pure", "mixture"]},
                   "alicat_gas_select": _GAS_SELECT_ENUM, "selectable_without_host_approval": {"type": "boolean"}, "evidence": _TEXT})
LIST_OUTPUT = _obj({"n_records": {"type": "integer", "minimum": 0, "x-unit": "1"}, "template_source": _TEXT, "library_sha256": _TEXT,
                    "templates": {"type": "array", "items": _LIST_ITEM}})
READ_OUTPUT = _obj({
    "substance_id": _TEXT, "name": _TEXT, "name_zh": _TEXT, "kind": {"type": "string", "enum": ["pure", "mixture"]},
    "cas": _NULL_TEXT, "formula": _NULL_TEXT, "molar_mass_kg_mol": _num("kg/mol", False, 0),
    "alicat_gas_select": _GAS_SELECT_ENUM, "alicat_listed_name": _NULL_TEXT, "selectable_without_host_approval": {"type": "boolean"},
    "composition_mole_fraction": {"type": "object", "additionalProperties": {"type": "number", "x-unit": "1", "minimum": 0, "maximum": 1}},
    "critical_temperature_K": _num("K"), "normal_boiling_point_K": _num("K"),
    "density_stp_kg_m3": _num("kg/m^3"), "viscosity_stp_Pa_s": _num("Pa*s"), "compressibility_stp": _num("1"),
    "density_ntp_kg_m3": _num("kg/m^3"), "viscosity_ntp_Pa_s": _num("Pa*s"), "compressibility_ntp": _num("1"),
    "isobar_1atm_temperature_K": {"type": "array", "items": {"type": "number", "x-unit": "K"}},
    "isobar_1atm_density_kg_m3": {"type": "array", "items": _num("kg/m^3")},
    "isobar_1atm_viscosity_Pa_s": {"type": "array", "items": _num("Pa*s")},
    "source": _TEXT, "evidence": _TEXT, "accessed": _TEXT, "conditions_json": _TEXT,
    "template_source": _TEXT, "library_sha256": _TEXT})
FLOW_OUTPUT = _obj({
    "gas_id": _TEXT, "gas_name": _TEXT, "kind": {"type": "string", "enum": ["pure", "mixture"]},
    "measurement.standard_flow_m3_s": _num("m^3/s", minimum=0), "measurement.actual_flow_m3_s": _num("m^3/s", minimum=0),
    "measurement.mass_flow_kg_s": _num("kg/s", minimum=0), "measurement.total_standard_volume_m3": _num("m^3", minimum=0),
    "measurement.pressure_Pa": _num("Pa", minimum=0), "measurement.temperature_K": _num("K", minimum=0),
    "reference_temperature_K": _num("K", False, 0), "reference_pressure_Pa": _num("Pa", False, 0),
    "viscosity_actual_Pa_s": _num("Pa*s"), "density_actual_kg_m3": _num("kg/m^3"), "compressibility_actual": _num("1"),
    "density_standard_kg_m3": _num("kg/m^3"), "compressibility_standard": _num("1"), "molar_mass_kg_mol": _num("kg/mol", False, 0),
    "template_source": _TEXT, "library_sha256": _TEXT, "template_record_source": _TEXT, "evidence": _TEXT,
    "synthetic": {"type": "boolean"}, "synthetic_components": {"type": "array", "items": _TEXT},
    "limitations": {"type": "array", "items": _TEXT}})


def capability_metadata():
    """Describe-level metadata for the three agent queries added by the template library."""
    common = {"availability": "implemented", "visibility": "agent", "kind": "query", "state_effects": [],
              "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "公开参考数据查询，无场景联动"}}
    binding = "basic40_runtime_automation.AlicatMCSession.invoke(instance_id=..., capability_id='%s', arguments=..., request_id=...)"
    return {
        "template.list": dict(common, description_zh="列出气体物性模板库（NIST纯气体+Alicat混气派生）的ID、名称和Gas Select归属；不涉及当前实例。",
                              input_schema=_obj({}), output_schema=copy.deepcopy(LIST_OUTPUT), observes=[], preconditions_zh=[],
                              example_arguments=[{}], binding=binding % "template.list"),
        "template.read": dict(common, description_zh="读取一种库气体的公开参考物性（摩尔质量、STP/NTP密度与黏度、1 atm等压线）；不透露当前管路气体。",
                              input_schema=_obj({"substance_id": {"type": "string", "description": "Library gas ID from template.list"}}),
                              output_schema=copy.deepcopy(READ_OUTPUT), observes=[], preconditions_zh=["substance_id存在于库中"],
                              example_arguments=[{"substance_id": "gas_nitrogen"}], binding=binding % "template.read"),
        "flow.read_template": dict(common, description_zh="读取当前所选库气体的流量读数及所用物性（黏度、实际/标准密度、Z）和来源；不推进时间。",
                                   input_schema=_obj({}), output_schema=copy.deepcopy(FLOW_OUTPUT),
                                   observes=["measurement.standard_flow_m3_s", "measurement.actual_flow_m3_s", "measurement.mass_flow_kg_s",
                                             "measurement.total_standard_volume_m3", "measurement.pressure_Pa", "measurement.temperature_K"],
                                   preconditions_zh=["已经通过gas.select选择库气体（非宿主登记气体）"], example_arguments=[{}],
                                   binding=binding % "flow.read_template"),
    }


def augment_describe(spec):
    if not available():
        return spec
    spec["capabilities"].update(capability_metadata())
    spec["capabilities"]["gas.select"]["input_schema"]["properties"]["gas_id"]["description"] = (
        "A host-registered compatible gas ID, or a gas-property library ID (template.list); standard-MC Gas Select gases need no "
        "host approval, S-Series-only or unlisted library gases need trusted host enablement")
    spec["asset_version"] = CONTRACT_ASSET_VERSION
    spec["template_library"] = {"path": TEMPLATE_REL, "library_sha256": _tl.library_sha256(TEMPLATE_REL),
                                "n_records": len(library()["templates"]), "limitations": list(LIMITATIONS)}
    return spec


def _public_record(rec):
    out = {"substance_id": rec["substance_id"], "name": rec["name"], "name_zh": rec.get("name_zh") or rec["name"], "kind": rec["kind"],
           "cas": rec.get("cas"), "formula": rec.get("formula"), "molar_mass_kg_mol": rec["molar_mass_kg_mol"],
           "alicat_gas_select": rec["alicat"]["gas_select"], "alicat_listed_name": rec["alicat"].get("listed_name"),
           "selectable_without_host_approval": selectable_without_host(rec),
           "composition_mole_fraction": dict(rec.get("composition_mole_fraction") or {}),
           "critical_temperature_K": rec.get("critical_temperature_K"), "normal_boiling_point_K": rec.get("normal_boiling_point_K")}
    # stored library values (NIST rows for pure gases, derived values for mixtures), not re-interpolated
    stored = rec.get("values_at_reference_conditions") or {}
    for tag, key in (("stp", "stp_298.15K_101325Pa"), ("ntp", "ntp_273.15K_101325Pa")):
        state = stored.get(key)
        out["density_%s_kg_m3" % tag] = None if state is None else state["density_kg_m3"]
        out["viscosity_%s_Pa_s" % tag] = None if state is None else state["viscosity_Pa_s"]
        out["compressibility_%s" % tag] = None if state is None else state["compressibility_factor"]
    temperatures = list(library()["grid_temperature_K"])
    if rec["kind"] == "pure":
        k = rec["grid"]["pressure_Pa"].index(101325.0)
        out["isobar_1atm_density_kg_m3"] = list(rec["grid"]["density_kg_m3"][k])
        out["isobar_1atm_viscosity_Pa_s"] = list(rec["grid"]["viscosity_Pa_s"][k])
    else:
        states = [gas_state(rec["substance_id"], t, 101325.0) for t in temperatures]
        out["isobar_1atm_density_kg_m3"] = [None if s is None else s["density_kg_m3"] for s in states]
        out["isobar_1atm_viscosity_Pa_s"] = [None if s is None else s["viscosity_Pa_s"] for s in states]
    out["isobar_1atm_temperature_K"] = temperatures
    out.update({"source": rec["source"], "evidence": rec["evidence"], "accessed": rec["accessed"],
                "conditions_json": json.dumps(rec.get("conditions", {}), ensure_ascii=False, sort_keys=True),
                "template_source": TEMPLATE_REL, "library_sha256": _tl.library_sha256(TEMPLATE_REL)})
    return out


def invoke(session, host, inst, capability_id, arguments, request_id):
    """Agent queries; never mutate instance state."""
    sid = inst["instance_id"]
    fail = lambda code: session._error(sid, code, request_id)
    if not available():
        return fail("UNKNOWN_CAPABILITY")
    expected = {"template.list": set(), "template.read": {"substance_id"}, "flow.read_template": set()}[capability_id]
    if set(arguments) != expected:
        return fail("INVALID_ARGUMENT")
    if capability_id == "template.list":
        rows = [{"substance_id": r["substance_id"], "name": r["name"], "kind": r["kind"], "alicat_gas_select": r["alicat"]["gas_select"],
                 "selectable_without_host_approval": selectable_without_host(r), "evidence": r["evidence"]} for r in library()["templates"]]
        return session._envelope(sid, request_id, result={"n_records": len(rows), "template_source": TEMPLATE_REL,
                                                         "library_sha256": _tl.library_sha256(TEMPLATE_REL), "templates": rows})
    if capability_id == "template.read":
        gas_id = arguments["substance_id"]
        if not isinstance(gas_id, str) or not gas_id.strip():
            return fail("INVALID_ARGUMENT")
        rec = record(gas_id)
        if rec is None:
            return fail("PRECONDITION_FAILED")
        return session._envelope(sid, request_id, result=_public_record(rec))
    values = inst["values"]
    selected = values["gas.selected_id"]
    if selected is None or selected in host._gases or record(selected) is None:
        return fail("PRECONDITION_FAILED")
    rec = record(selected)
    actual = standard = None
    if values["measurement.pressure_Pa"] is not None and values["measurement.temperature_K"] is not None:
        actual = gas_state(selected, values["measurement.temperature_K"], values["measurement.pressure_Pa"])
    standard = gas_state(selected, values["gas.reference_temperature_K"], values["gas.reference_pressure_Pa"])
    result = {"gas_id": selected, "gas_name": rec["name"], "kind": rec["kind"]}
    for key in ("measurement.standard_flow_m3_s", "measurement.actual_flow_m3_s", "measurement.mass_flow_kg_s",
                "measurement.total_standard_volume_m3", "measurement.pressure_Pa", "measurement.temperature_K"):
        result[key] = values[key]
    result.update({"reference_temperature_K": values["gas.reference_temperature_K"], "reference_pressure_Pa": values["gas.reference_pressure_Pa"],
                   "viscosity_actual_Pa_s": None if actual is None else actual["viscosity_Pa_s"],
                   "density_actual_kg_m3": None if actual is None else actual["density_kg_m3"],
                   "compressibility_actual": None if actual is None else actual["compressibility_factor"],
                   "density_standard_kg_m3": None if standard is None else standard["density_kg_m3"],
                   "compressibility_standard": None if standard is None else standard["compressibility_factor"],
                   "molar_mass_kg_mol": rec["molar_mass_kg_mol"], "template_source": TEMPLATE_REL,
                   "library_sha256": _tl.library_sha256(TEMPLATE_REL), "template_record_source": rec["source"], "evidence": rec["evidence"],
                   "synthetic": False, "synthetic_components": list(SYNTHETIC_COMPONENTS), "limitations": list(LIMITATIONS)})
    return session._envelope(sid, request_id, result=result)
