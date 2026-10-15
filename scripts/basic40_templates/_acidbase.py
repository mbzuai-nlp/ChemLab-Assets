"""Ideal aqueous acid-base speciation from the shared pKa library (metrohm_omnis_acidbase_library).

One pKa table is stored once and used by two instruments: the OMNIS titrator (titration curves) and the 914 meter
(pH of a template-backed solution). The model is an ideal-solution charge balance:

* activity coefficients are 1 and concentrations in mol/L stand in for molalities (dilute-solution assumption);
* stepwise thermodynamic pKa (I = 0, 298.15 K) are moved to the sample temperature with the library's PHREEQC
  analytic expression (as a shift from 298.15 K) when present, otherwise with Goldberg 2002 Eq. 12 (ΔrH°, ΔrCp°);
  missing enthalpy keeps the 298.15 K value and is reported as an assumption;
* pKw(T) comes from the library's water record;
* counter-ions of the weighed form (e.g. K+ of KHP) are fixed by electroneutrality of the named compound.
"""
from __future__ import annotations

import math
from typing import Any

from ._echem_common import LN10, R, T_REF, get_template, index_by_id, load_library, number

ACIDBASE_REL = "assets/reactions/metrohm_omnis_acidbase_library/templates.json"
STRONG_STEP_PKA = -12.0  # numerical stand-in for a step the library lists as fully dissociated (pKa null)


def library() -> dict[str, Any]:
    return load_library(ACIDBASE_REL)


def records_by_type(record_type: str) -> dict[str, dict[str, Any]]:
    return {sid: rec for sid, rec in index_by_id(ACIDBASE_REL).items() if rec.get("record_type") == record_type}


def record(substance_id: str) -> dict[str, Any] | None:
    return get_template(ACIDBASE_REL, substance_id)


def water_record() -> dict[str, Any]:
    for rec in index_by_id(ACIDBASE_REL).values():
        if rec.get("record_type") == "solvent_autoprotolysis":
            return rec
    raise KeyError("water autoprotolysis record missing from the acid-base library")


def _analytic(coeffs: list[float], temperature_k: float) -> float:
    c = list(coeffs) + [0.0] * (6 - len(coeffs))
    t = temperature_k
    return c[0] + c[1] * t + c[2] / t + c[3] * math.log10(t) + c[4] / t ** 2 + c[5] * t ** 2


def _coefficients(entry: Any) -> list[float] | None:
    """Library analytic entries are {"coefficients": [...], ...} (or a bare list); None when absent."""
    if isinstance(entry, dict):
        entry = entry.get("coefficients")
    return [float(x) for x in entry] if isinstance(entry, list) and entry else None


def _shift_log_k(log_k_ref: float, delta_h: float | None, delta_cp: float | None, temperature_k: float) -> float:
    """log10 K(T) from log10 K(Tr) with constant ΔCp (Goldberg 2002 Eq. 12 / Clarke–Glew); ΔCp None -> van 't Hoff."""
    if delta_h is None:
        return log_k_ref
    cp = delta_cp or 0.0
    ln_shift = (-delta_h / R * (1.0 / temperature_k - 1.0 / T_REF)
                + cp / R * (math.log(temperature_k / T_REF) + T_REF / temperature_k - 1.0))
    return log_k_ref + ln_shift / LN10


def _step_list(rec: dict[str, Any], key: str, n: int) -> list[Any]:
    values = rec.get(key)
    if not isinstance(values, list):
        return [None] * n
    return (list(values) + [None] * n)[:n]


def pka_at(rec: dict[str, Any], temperature_k: float) -> tuple[list[float], list[str]]:
    """Stepwise pKa at temperature_k and, per step, which temperature model was used.

    The tabulated 298.15 K value is always reproduced exactly; PHREEQC analytic functions are applied as a shift
    log K(T) - log K(298.15 K) (preferred where the library has one), otherwise ΔrH°/ΔrCp° (Goldberg Eq. 12).
    """
    n = int(rec["n_acidic_protons"])
    pkas = [None if v is None else float(v) for v in rec["pka"]]
    analytic = _step_list(rec, "analytic_log_ka", n)
    dh = _step_list(rec, "delta_h_j_mol", n)
    dcp = _step_list(rec, "delta_cp_j_mol_k", n)
    out, model = [], []
    for i in range(n):
        coeffs = _coefficients(analytic[i])
        if pkas[i] is None:  # library marks a fully dissociated (strong) step, e.g. H2SO4 -> HSO4-
            out.append(STRONG_STEP_PKA)
            model.append("assumption_strong_step_complete_dissociation")
        elif abs(temperature_k - T_REF) < 1e-9:
            out.append(pkas[i])
            model.append("tabulated_298.15K")
        elif coeffs:
            out.append(pkas[i] - (_analytic(coeffs, temperature_k) - _analytic(coeffs, T_REF)))
            model.append("phreeqc_analytic_shift")
        elif number(dh[i]):
            out.append(-_shift_log_k(-pkas[i], dh[i], dcp[i] if number(dcp[i]) else None, temperature_k))
            model.append("goldberg_eq12_dh_dcp" if number(dcp[i]) else "van_t_hoff_dh_only (dCp=0 assumption)")
        else:
            out.append(pkas[i])
            model.append("assumption_constant_pka")
    return out, model


def pkw_at(temperature_k: float) -> tuple[float, str]:
    rec = water_record()
    pkw = float(rec["pkw"])
    if abs(temperature_k - T_REF) < 1e-9:
        return pkw, "tabulated_298.15K"
    coeffs = _coefficients(rec.get("analytic_log_kw"))
    if coeffs:
        return pkw - (_analytic(coeffs, temperature_k) - _analytic(coeffs, T_REF)), "phreeqc_analytic_shift"
    if number(rec.get("delta_h_j_mol")):
        return -_shift_log_k(-pkw, rec["delta_h_j_mol"], rec.get("delta_cp_j_mol_k"), temperature_k), "van_t_hoff_dh"
    return pkw, "assumption_constant_pkw"


def species_charge(rec: dict[str, Any], protons: int) -> int:
    """Charge of the member of an acid system that carries ``protons`` dissociable protons."""
    n = int(rec["n_acidic_protons"])
    return int(rec["charge_fully_protonated"]) - (n - protons)


def counter_charge_per_mol(rec: dict[str, Any], protons_as_added: int) -> int:
    """Charge of spectator counter-ions per mole of the added (electroneutral) form, e.g. +1 (K+) for KHP."""
    return -species_charge(rec, protons_as_added)


def _mean_removed(log_kas: list[float], log_h: float) -> float:
    """Average number of protons removed from H_nA at log10[H+] = log_h (overflow-safe)."""
    n = len(log_kas)
    terms, acc = [], 0.0
    for k in range(n + 1):
        if k:
            acc += log_kas[k - 1]
        terms.append(acc + (n - k) * log_h)
    peak = max(terms)
    weights = [10.0 ** (t - peak) for t in terms]
    total = sum(weights)
    return sum(k * w for k, w in enumerate(weights)) / total


def solve_ph(components: list[dict[str, Any]], strong_charge_mol_l: float, temperature_k: float) -> dict[str, Any]:
    """Charge balance for [H+] (mol/L).

    components: [{"record": acid_base record, "total_mol_l": C, "protons_as_added": h}] — the counter-ions of
    each added form are included automatically; ``strong_charge_mol_l`` is the net charge of any other fully
    dissociated ions (e.g. +c for NaOH, -c for HCl).
    """
    if not number(temperature_k) or temperature_k <= 0:
        raise ValueError("positive temperature required")
    pkw, kw_model = pkw_at(temperature_k)
    parts, spectator = [], float(strong_charge_mol_l)
    for comp in components:
        rec, total = comp["record"], float(comp["total_mol_l"])
        if total < 0:
            raise ValueError("negative concentration")
        pkas, models = pka_at(rec, temperature_k)
        protons = int(comp["protons_as_added"])
        spectator += counter_charge_per_mol(rec, protons) * total
        parts.append((rec, total, [-p for p in pkas], models))

    def charge(log_h: float) -> float:
        h = 10.0 ** log_h
        value = h - 10.0 ** (-pkw - log_h) + spectator
        for rec, total, log_kas, _ in parts:
            value += total * (int(rec["charge_fully_protonated"]) - _mean_removed(log_kas, log_h))
        return value

    lo, hi = -20.0, 3.0
    if charge(lo) > 0 or charge(hi) < 0:
        raise ValueError("charge balance has no root in the supported pH window")
    for _ in range(200):
        mid = (lo + hi) / 2
        if charge(mid) > 0:
            hi = mid
        else:
            lo = mid
    log_h = (lo + hi) / 2
    return {"ph": -log_h, "h_mol_l": 10.0 ** log_h, "pkw": pkw, "pkw_model": kw_model,
            "pka_models": {rec["substance_id"]: models for rec, _, _, models in parts},
            "pka_at_temperature": {rec["substance_id"]: [-x for x in log_kas] for rec, _, log_kas, _ in parts}}


def titratable_steps(rec: dict[str, Any], protons_as_added: int, titrant_strength: str) -> int:
    """Number of proton steps a strong titrant can move for this added form (counted, not necessarily detectable)."""
    n = int(rec["n_acidic_protons"])
    return protons_as_added if titrant_strength == "strong_base" else n - protons_as_added
