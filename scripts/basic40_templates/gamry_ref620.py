"""Gamry Reference 620: template-backed reversible cyclic voltammetry from the public redox-couple library.

Library: ``assets/reactions/gamry_ref620_redox_library/templates.json`` (standard potentials with reference electrode
and medium, electron count, aqueous diffusion coefficients linked from the USGS/PHREEQC-based pine library).

The trusted host loads a cell (couple + bulk concentrations + electrode area + reference electrode, optional pH and
explicitly labelled assumption inputs) with ``GamryReference620Runtime.host_load_template_cell``. The agent then
calls ``electrochem.template_cv`` with a triangular scan; the result is a numerical reversible (Nernstian) CV for
planar semi-infinite diffusion (semi-integral method, Grünwald–Letnikov semi-derivative) plus the analytic
Randles–Sevcik forward-peak current. Kinetics (assumed reversible), uncompensated resistance and double-layer
capacitance are explicit host assumptions; the real data are E°, n and D. Agent-visible results never contain the
couple identity or the bulk concentrations.
"""
from __future__ import annotations

import copy
import math
from typing import Any

from ._echem_common import (BOOL, F, LN10, R, T_REF, TEXT, arr, get_template, index_by_id, matches, nullable, num, number,
                            obj, provenance, public_record)

TEMPLATE_REL = "assets/reactions/gamry_ref620_redox_library/templates.json"
PINE_REL = "assets/expansion_batch04/t4_measure/pine_wavevortex10/diffusion_library.json"
POTENTIAL_LIMIT_V = 11.0
CURRENT_LIMIT_A = 0.6
MAX_POINTS_PER_SEGMENT = 1000
DEFAULT_POINTS_PER_SEGMENT = 500
TEMPLATE_CAPABILITY_IDS = ("template.list", "template.read", "electrochem.template_cv")

LIMITATIONS = [
    "E° are standard potentials at 298.15 K (unit activity, SHE); no formal-potential medium data or dE°/dT are applied unless the host sets formal_potential_shift_v (assumption).",
    "Reversible (Nernstian) single n-electron step assumed; heterogeneous kinetics, coupled chemistry, adsorption and multi-step mechanisms are not modelled.",
    "Planar semi-infinite diffusion at the sample temperature using 298.15 K diffusion coefficients; no convection, migration or edge diffusion.",
    "Uncompensated resistance and double-layer capacitance are host-supplied assumptions (default 0); capacitive current is added as C_dl*A*dE/dt.",
    "Peak currents are read from zero current, not from an extrapolated baseline; IUPAC convention (anodic positive).",
    "The ±11 V / ±0.6 A instrument limits are checked; overload is flagged, never clipped silently.",
]


def _records() -> dict[str, dict[str, Any]]:
    return index_by_id(TEMPLATE_REL)


COUPLE_TYPES = ("redox_couple", "redox_couple_nonaqueous")
NONAQUEOUS_REFERENCE_ID = "refel_sce_sat_kcl"  # nonaqueous E1/2 values are reported vs aqueous SCE


def couple(substance_id: str) -> dict[str, Any] | None:
    rec = get_template(TEMPLATE_REL, substance_id)
    return rec if rec and rec.get("record_type") in COUPLE_TYPES else None


def reference_electrode_potential(reference_id: str) -> tuple[float, dict[str, Any] | None]:
    if reference_id in ("she", "SHE"):
        return 0.0, None
    rec = get_template(TEMPLATE_REL, reference_id)
    if not rec or rec.get("record_type") != "reference_electrode" or not number(rec.get("potential_vs_she_v")):
        raise ValueError("unknown reference electrode")
    return float(rec["potential_vs_she_v"]), rec


# ----------------------------------------------------------------------------------------------- host-side cell
CELL_SCHEMA = obj({
    "couple_id": TEXT,
    "oxidant_concentration_mol_m3": num("mol/m^3", 0, 1e4),
    "reductant_concentration_mol_m3": num("mol/m^3", 0, 1e4),
    "electrode_area_m2": num("m^2", exclusive_minimum=0, maximum=1.0),
    "temperature_k": num("K", 273.15, 373.15),
    "ph": nullable(num("1", -2, 16)),
    "reference_electrode_id": TEXT,
    "diffusion_override_m2_s": nullable(obj({"oxidant": nullable(num("m^2/s", exclusive_minimum=0, maximum=1e-7)),
                                             "reductant": nullable(num("m^2/s", exclusive_minimum=0, maximum=1e-7))})),
    "uncompensated_resistance_ohm": num("ohm", 0, 1e6),
    "double_layer_capacitance_f_m2": num("F/m^2", 0, 10.0),
    "formal_potential_shift_v": num("V", -2, 2),
}, required=["couple_id", "oxidant_concentration_mol_m3", "reductant_concentration_mol_m3", "electrode_area_m2",
             "reference_electrode_id"])
CELL_DEFAULTS = {"temperature_k": T_REF, "ph": None, "diffusion_override_m2_s": None, "uncompensated_resistance_ohm": 0.0,
                 "double_layer_capacitance_f_m2": 0.0, "formal_potential_shift_v": 0.0}


def build_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """Validate a host cell description and resolve every template quantity; raises ValueError."""
    if not isinstance(cell, dict) or not matches(CELL_SCHEMA, cell):
        raise ValueError("cell does not match the template cell schema")
    spec = {**CELL_DEFAULTS, **copy.deepcopy(cell)}
    rec = couple(spec["couple_id"])
    if rec is None:
        raise ValueError("unknown redox couple")
    if not rec.get("cv_model_eligible"):
        raise ValueError("couple is not eligible for the soluble/soluble reversible CV model")
    if spec["oxidant_concentration_mol_m3"] <= 0 and spec["reductant_concentration_mol_m3"] <= 0:
        raise ValueError("at least one electroactive partner must be present")
    n = int(rec["n_electrons"])
    nonaqueous = rec["record_type"] == "redox_couple_nonaqueous"
    proton_stoich = 0.0 if nonaqueous else float(rec.get("proton_stoich") or 0.0)
    if proton_stoich and spec["ph"] is None:
        raise ValueError("pH required for a proton-coupled couple")
    if nonaqueous and (spec["reference_electrode_id"] != NONAQUEOUS_REFERENCE_ID or spec["ph"] is not None):
        raise ValueError("nonaqueous E1/2 records are only used against the SCE they were reported on, without pH")
    e_ref, ref_rec = reference_electrode_potential(spec["reference_electrode_id"])
    t = float(spec["temperature_k"])
    diffusion = rec.get("diffusion") or {}
    override = spec["diffusion_override_m2_s"] or {}
    d_ox, d_red = diffusion.get("oxidant_m2_s"), diffusion.get("reductant_m2_s")
    d_source = {"oxidant": "library" if number(d_ox) else None, "reductant": "library" if number(d_red) else None}
    for side in ("oxidant", "reductant"):
        if number(override.get(side)):
            if side == "oxidant":
                d_ox = float(override[side])
            else:
                d_red = float(override[side])
            d_source[side] = "host_assumption"
    # Nonaqueous: E1/2 vs SCE is placed on the internal SHE-referenced axis via the same SCE record, so the
    # potential reported vs that SCE equals the published E1/2 (no aqueous/nonaqueous junction correction claimed).
    e0 = float(rec["e_half_wave_v"]) + e_ref if nonaqueous else float(rec["e_standard_v"])
    slope = R * t * LN10 / F
    ph_term = (proton_stoich / n) * slope * float(spec["ph"]) if proton_stoich else 0.0
    # Couples written with OH-: proton_stoich counts OH- produced as H+ equivalents, so E°' needs + (h/n)*k*pKw(T).
    hydroxide_stoich = float(rec.get("hydroxide_stoich") or 0.0)
    pkw = None
    if hydroxide_stoich:
        from . import _acidbase
        pkw = _acidbase.pkw_at(t)[0]
    e_formal_she = e0 - ph_term + ((hydroxide_stoich / n) * slope * pkw if hydroxide_stoich else 0.0) + float(spec["formal_potential_shift_v"])
    return {"spec": spec, "couple_id": rec["substance_id"], "n": n, "e_standard_she_v": None if nonaqueous else e0,
            "e_reported_v": float(rec["e_half_wave_v"] if nonaqueous else rec["e_standard_v"]),
            "e_reported_scale": "E1/2 vs aqueous SCE (nonaqueous solvent)" if nonaqueous else "E° vs SHE",
            "proton_stoich": proton_stoich,
            "hydroxide_stoich": hydroxide_stoich, "pkw": pkw,
            "e_formal_she_v": e_formal_she, "reference_potential_she_v": e_ref,
            "reference_record_source": None if ref_rec is None else ref_rec.get("source"),
            "d_ox": d_ox if number(d_ox) else None, "d_red": d_red if number(d_red) else None, "d_source": d_source,
            "record_provenance": provenance(TEMPLATE_REL, rec)}


# ----------------------------------------------------------------------------------------------- CV model
def _surface_term(c_ox: float, c_red: float, sq_ox: float, sq_red: float, log_theta: float) -> float:
    """Semi-integral M with Nernst surface ratio theta = C_O(0)/C_R(0) = exp(log_theta)."""
    if log_theta > 0:
        inv = math.exp(-min(log_theta, 700.0))
        return (c_ox * inv - c_red) / (inv / sq_ox + 1.0 / sq_red)
    theta = math.exp(max(log_theta, -700.0))
    return (c_ox - theta * c_red) / (1.0 / sq_ox + theta / sq_red)


def simulate_cv(*, n: int, e_formal_vs_ref: float, d_ox: float, d_red: float, c_ox: float, c_red: float,
                area_m2: float, temperature_k: float, e_start: float, e_vertex: float, scan_rate: float,
                ru_ohm: float = 0.0, cdl_f_m2: float = 0.0, points_per_segment: int = DEFAULT_POINTS_PER_SEGMENT) -> list[dict[str, float]]:
    """Reversible CV, planar semi-infinite diffusion: C_O(0)=C_O*-M/sqrt(D_O), C_R(0)=C_R*+M/sqrt(D_R),
    flux J = d^(1/2)M/dt^(1/2) (Grünwald–Letnikov), i = -nFA*J + C_dl*A*dE/dt (IUPAC: anodic positive)."""
    f = n * F / (R * temperature_k)
    steps = int(points_per_segment)
    span = e_vertex - e_start
    dt = abs(span) / scan_rate / steps
    potentials = [e_start + span * k / steps for k in range(steps + 1)] + [e_vertex - span * k / steps for k in range(1, steps + 1)]
    total = len(potentials)
    weights = [1.0]
    for j in range(1, total):
        weights.append(weights[-1] * (1.0 - 1.5 / j))
    sq_ox, sq_red, root_dt = math.sqrt(d_ox), math.sqrt(d_red), math.sqrt(dt)
    nfa = n * F * area_m2
    m_values: list[float] = []
    curve = []
    for k, e_app in enumerate(potentials):
        history = 0.0
        for j in range(1, k + 1):
            history += weights[j] * m_values[k - j]
        sign = 1.0 if (k <= steps and span > 0) or (k > steps and span < 0) else -1.0
        i_cap = cdl_f_m2 * area_m2 * scan_rate * sign
        if ru_ohm > 0:
            lo, hi = -c_red * sq_red, c_ox * sq_ox
            for _ in range(80):
                mid = (lo + hi) / 2
                current = -nfa * (mid + history) / root_dt + i_cap
                residual = (c_ox - mid / sq_ox) - math.exp(min(max(f * (e_app - current * ru_ohm - e_formal_vs_ref), -700.0), 700.0)) * (c_red + mid / sq_red)
                if residual > 0:
                    lo = mid
                else:
                    hi = mid
            m_k = (lo + hi) / 2
        else:
            m_k = _surface_term(c_ox, c_red, sq_ox, sq_red, f * (e_app - e_formal_vs_ref))
        m_values.append(m_k)
        current = -nfa * (m_k + history) / root_dt + i_cap
        curve.append({"potential_v": e_app, "current_a": current, "time_s": k * dt})
    return curve


def randles_sevcik(n: int, area_m2: float, conc_mol_m3: float, d_m2_s: float, scan_rate: float, temperature_k: float) -> float:
    """ip = 0.4463 n F A C (n F v D / (R T))^(1/2), magnitude in A."""
    return 0.4463 * n * F * area_m2 * conc_mol_m3 * math.sqrt(n * F * scan_rate * d_m2_s / (R * temperature_k))


def _peak(points: list[dict[str, float]], kind: str) -> dict[str, float] | None:
    """Interior extremum of one sweep segment (endpoints are not peaks)."""
    if len(points) < 3:
        return None
    pick = min if kind == "cathodic" else max
    best = pick(range(len(points)), key=lambda i: points[i]["current_a"])
    if best in (0, len(points) - 1):
        return None
    value = points[best]["current_a"]
    if (kind == "cathodic" and value >= 0) or (kind == "anodic" and value <= 0):
        return None
    return {"potential_v": points[best]["potential_v"], "current_a": value}


def run_template_cv(cell: dict[str, Any], scan: dict[str, Any], *, evaluator: bool) -> dict[str, Any]:
    spec = cell["spec"]
    t = float(spec["temperature_k"])
    e_formal_ref = cell["e_formal_she_v"] - cell["reference_potential_she_v"]
    # At least one diffusion coefficient is needed for currents; a missing partner is set equal (flagged assumption).
    have_d = cell["d_ox"] is not None or cell["d_red"] is not None
    d_ox = cell["d_ox"] if cell["d_ox"] is not None else (cell["d_red"] or 1e-9)
    d_red = cell["d_red"] if cell["d_red"] is not None else d_ox
    assumed_ratio = cell["d_ox"] is None or cell["d_red"] is None
    points = int(scan.get("points_per_segment") or DEFAULT_POINTS_PER_SEGMENT)
    curve = simulate_cv(n=cell["n"], e_formal_vs_ref=e_formal_ref, d_ox=d_ox, d_red=d_red,
                        c_ox=float(spec["oxidant_concentration_mol_m3"]), c_red=float(spec["reductant_concentration_mol_m3"]),
                        area_m2=float(spec["electrode_area_m2"]), temperature_k=t, e_start=scan["e_start_v"],
                        e_vertex=scan["e_vertex_v"], scan_rate=scan["scan_rate_v_s"],
                        ru_ohm=float(spec["uncompensated_resistance_ohm"]), cdl_f_m2=float(spec["double_layer_capacitance_f_m2"]),
                        points_per_segment=points)
    cathodic_first = scan["e_vertex_v"] < scan["e_start_v"]
    forward, reverse = curve[:points + 1], curve[points:]
    cathodic = _peak(forward if cathodic_first else reverse, "cathodic")
    anodic = _peak(reverse if cathodic_first else forward, "anodic")
    rs = None
    forward_conc = float(spec["oxidant_concentration_mol_m3"] if cathodic_first else spec["reductant_concentration_mol_m3"])
    forward_d = d_ox if cathodic_first else d_red
    if have_d and forward_conc > 0:
        rs = randles_sevcik(cell["n"], float(spec["electrode_area_m2"]), forward_conc, forward_d, scan["scan_rate_v_s"], t)
        rs = -rs if cathodic_first else rs
    if not have_d:
        for row in curve:
            row["current_a"] = None
        cathodic = None if cathodic is None else {"potential_v": cathodic["potential_v"], "current_a": None}
        anodic = None if anodic is None else {"potential_v": anodic["potential_v"], "current_a": None}
    overload = any(row["current_a"] is not None and abs(row["current_a"]) > CURRENT_LIMIT_A for row in curve)
    separation = None if cathodic is None or anodic is None else anodic["potential_v"] - cathodic["potential_v"]
    half_wave = None if cathodic is None or anodic is None else (anodic["potential_v"] + cathodic["potential_v"]) / 2
    sources = cell["d_source"]
    d_evidence = ("missing: currents not computed (host must supply diffusion_override_m2_s)" if not have_d
                  else "derived_database (USGS PHREEQC -dw via pine library)" if all(v == "library" for v in sources.values())
                  else "host_assumption" if all(v == "host_assumption" for v in sources.values() if v)
                  else "mixed: " + ", ".join(f"{k}={v or 'set equal to partner (assumption)'}" for k, v in sources.items()))
    evidence = {"standard_potential": cell["record_provenance"]["evidence"], "electron_count": cell["record_provenance"]["evidence"],
                "diffusion_coefficient": d_evidence,
                "diffusion_ratio": "assumption D_O = D_R (one partner missing)" if assumed_ratio else "library_or_host",
                "kinetics": "assumption: reversible (Nernstian)",
                "ohmic_drop": "assumption" if spec["uncompensated_resistance_ohm"] else "assumption: R_u = 0",
                "double_layer": "assumption" if spec["double_layer_capacitance_f_m2"] else "assumption: C_dl = 0",
                "formal_potential": "assumption: shift applied" if spec["formal_potential_shift_v"] else "standard potential used as formal potential (assumption)"}
    result = {"template_backed": True, "reference_electrode_id": spec["reference_electrode_id"],
              "scan": {"e_start_v": scan["e_start_v"], "e_vertex_v": scan["e_vertex_v"], "scan_rate_v_s": scan["scan_rate_v_s"],
                       "duration_s": 2 * abs(scan["e_vertex_v"] - scan["e_start_v"]) / scan["scan_rate_v_s"], "points_per_segment": points},
              "curve": [{"potential_v": row["potential_v"], "current_a": row["current_a"], "time_s": row["time_s"]} for row in curve],
              "cathodic_peak": cathodic, "anodic_peak": anodic, "peak_separation_v": separation, "half_wave_potential_v": half_wave,
              "randles_sevcik_peak_current_a": rs if have_d else None,
              "current_convention": "IUPAC: anodic current positive, cathodic negative",
              "current_overload": overload,
              "template_source": TEMPLATE_REL, "library_sha256": cell["record_provenance"]["library_sha256"],
              "evidence": evidence, "synthetic": False,
              "synthetic_parts": ["reversible-kinetics assumption", "ohmic drop and double layer (host assumption inputs)",
                                  "numerical semi-infinite planar diffusion model output (derived)"],
              "limitations": list(LIMITATIONS), "truth": None}
    if evaluator:
        result["truth"] = {"couple_id": cell["couple_id"], "cell": copy.deepcopy(spec), "e_standard_she_v": cell["e_standard_she_v"],
                           "e_formal_she_v": cell["e_formal_she_v"], "d_oxidant_m2_s": cell["d_ox"], "d_reductant_m2_s": cell["d_red"],
                           "diffusion_source": cell["d_source"], "record_source": cell["record_provenance"]["template_record_source"]}
    return result


# ----------------------------------------------------------------------------------------------- capability metadata
POINT = obj({"potential_v": num("V"), "current_a": nullable(num("A")), "time_s": num("s", 0)})
PEAK = nullable(obj({"potential_v": num("V"), "current_a": nullable(num("A"))}))
CV_INPUT = obj({"e_start_v": num("V", -POTENTIAL_LIMIT_V, POTENTIAL_LIMIT_V), "e_vertex_v": num("V", -POTENTIAL_LIMIT_V, POTENTIAL_LIMIT_V),
                "scan_rate_v_s": num("V/s", maximum=1e4, exclusive_minimum=0),
                "points_per_segment": nullable({"type": "integer", "minimum": 20, "maximum": MAX_POINTS_PER_SEGMENT, "x-unit": "1"})},
               required=["e_start_v", "e_vertex_v", "scan_rate_v_s"])
CV_OUTPUT = obj({"template_backed": BOOL, "reference_electrode_id": TEXT,
                 "scan": obj({"e_start_v": num("V"), "e_vertex_v": num("V"), "scan_rate_v_s": num("V/s"), "duration_s": num("s", 0),
                              "points_per_segment": {"type": "integer", "x-unit": "1"}}),
                 "curve": arr(POINT, 2 * MAX_POINTS_PER_SEGMENT + 1), "cathodic_peak": PEAK, "anodic_peak": PEAK,
                 "peak_separation_v": nullable(num("V")), "half_wave_potential_v": nullable(num("V")),
                 "randles_sevcik_peak_current_a": nullable(num("A")), "current_convention": TEXT, "current_overload": BOOL,
                 "template_source": TEXT, "library_sha256": TEXT,
                 "evidence": {"type": "object", "additionalProperties": TEXT}, "synthetic": BOOL,
                 "synthetic_parts": arr(TEXT), "limitations": arr(TEXT),
                 "truth": nullable({"type": "object"})})
READ_INPUT = obj({"substance_id": {"type": "string", "minLength": 1, "maxLength": 128}})
READ_OUTPUT = obj({"record": {"type": "object"}, "template_source": TEXT, "template_record_source": nullable(TEXT),
                   "evidence": nullable(TEXT), "library_sha256": TEXT})
LIST_OUTPUT = obj({"records": arr(obj({"substance_id": TEXT, "record_type": TEXT, "name": TEXT, "cv_model_eligible": BOOL})),
                   "template_source": TEXT, "library_sha256": TEXT})


def capability_metadata() -> dict[str, dict[str, Any]]:
    """Runtime-describe entries in the GamryReference620 metadata style."""
    def entry(name, inputs, outputs, example, kind, description, preconditions, components):
        return {"availability": "implemented", "visibility": "agent", "kind": kind, "description_zh": description,
                "input_schema": inputs, "output_schema": outputs, "example_arguments": [example], "state_effects": [],
                "observes": [], "component_ids": components, "preconditions_zh": preconditions,
                "binding": f"basic40_runtime_measure.GamryReference620Session.invoke(instance_id=..., capability_id='{name}', arguments=..., request_id=...)",
                "completion_criterion_zh": "同步返回；不推进宿主时钟、不改变实例状态。",
                "model_effects": {"mode": "none", "binding_ids": [], "description_zh": "只读模板或分析计算，不驱动几何。"},
                "implementation": {"process": "implemented", "host": "implemented", "scene": "absent", "robot": "absent", "hardware": "absent"},
                "error_codes": ["UNKNOWN_INSTANCE", "UNKNOWN_CAPABILITY", "INVALID_ARGUMENT", "ACCESS_DENIED", "PRECONDITION_FAILED", "BUSY", "REQUEST_CONFLICT"],
                "retry": "same_request_id; in-memory only", "template_source": TEMPLATE_REL}
    return {
        "template.list": entry("template.list", obj({}), LIST_OUTPUT, {}, "query",
                               "列出公开氧化还原电对参考库条目（标准电位、电子数、扩散系数来源）；不透露当前电解池。", "实例可用。", ["root"]),
        "template.read": entry("template.read", READ_INPUT, READ_OUTPUT, {"substance_id": "redox_fe3_fe2"}, "query",
                               "读取一条公开参考记录（电对或参比电极）及来源证据；不透露当前电解池内容。", "实例可用；substance_id为库内条目。", ["root"]),
        "electrochem.template_cv": entry("electrochem.template_cv", CV_INPUT, CV_OUTPUT,
                                         {"e_start_v": 0.6, "e_vertex_v": -0.2, "scan_rate_v_s": 0.1, "points_per_segment": None}, "analysis",
                                         "对宿主装入的模板电解池计算可逆循环伏安：E°/n/D来自公开库，动力学可逆、iR与双电层为显式假设；不推进时钟。",
                                         "供电、四槽已连接、波形未播放且宿主已装入模板电解池；电位在±11 V内，扫速>0。",
                                         ["root"] + [p + "_slot" for p in ("working_electrode", "reference_electrode", "counter_electrode", "cell")]),
    }


# ----------------------------------------------------------------------------------------------- dispatch
def invoke(session, host, instance_id: str, inst: dict[str, Any], cap: str, args: dict[str, Any], request_id: str, *, evaluator: bool):
    fail = lambda code, status="rejected": session._error(instance_id, code, request_id, status)
    meta = capability_metadata()[cap]
    if not matches(meta["input_schema"], args):
        return fail("INVALID_ARGUMENT")
    if cap == "template.list":
        rows = [{"substance_id": sid, "record_type": rec.get("record_type", ""), "name": rec.get("name", ""),
                 "cv_model_eligible": bool(rec.get("cv_model_eligible"))} for sid, rec in _records().items()]
        any_rec = next(iter(_records().values()))
        return session._envelope(instance_id, request_id, result={"records": rows, "template_source": TEMPLATE_REL,
                                                                  "library_sha256": provenance(TEMPLATE_REL, any_rec)["library_sha256"]})
    if cap == "template.read":
        rec = get_template(TEMPLATE_REL, args["substance_id"])
        if rec is None:
            return fail("INVALID_ARGUMENT")
        return session._envelope(instance_id, request_id, result=public_record(TEMPLATE_REL, rec))
    # electrochem.template_cv
    if inst["running"]:
        return fail("BUSY")
    cell = inst.get("template_cell")
    if not inst["enabled"] or cell is None or not all(inst["connections"].values()):
        return fail("PRECONDITION_FAILED")
    if args["e_start_v"] == args["e_vertex_v"]:
        return fail("INVALID_ARGUMENT")
    try:
        result = run_template_cv(cell, args, evaluator=evaluator)
    except (ValueError, OverflowError, ZeroDivisionError):
        return fail("INVALID_ARGUMENT")
    return session._envelope(instance_id, request_id, result=result)
