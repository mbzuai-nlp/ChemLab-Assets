"""Add storage class, GHS hazard data and liquid boiling point / viscosity to assets/reactions/substances.json (schema 1.1.0),
and add the new substances the default-lab inventory needs (new_substances.py).

Inputs: the PubChem records cached by fetch_pubchem_safety.py under references/substance_safety_raw/<id>/ (public data; each
value keeps its PubChem CID, the depositing source and that source's own reference, e.g. HSDB citing the CRC Handbook page).

Added per substance (all additive; existing keys untouched):
  ghs              {hazard_codes, pictograms, signal_word, basis, source{...}, notified_codes{code: %}, note}
  storage_class    flammable | acid | base | oxidizer | toxic | cold | compressed_gas | general   (rule below)
  storage          {class, flags[], rule, temperature_c|null, evidence, source}
  boiling_point_K  (+ boiling_point_source)                 liquids only (phase == "liquid")
  viscosity_Pa_s, viscosity_T_K (+ viscosity_source)         liquids only, dynamic viscosity
GHS basis, in order: (1) the EU harmonised classification (Regulation (EC) No 1272/2008 Annex VI, as listed by PubChem);
(2) else the ECHA C&L notification summary, codes notified by >= 10 % of notifiers; (3) else another listed authority
(Safe Work Australia HCIS, NITE-CMC Japan, HSDB).  Gas-only codes (H220/H221/H280/H281) are dropped for non-gas phases (the
aqueous / liquid stock is not the compressed gas).
Storage-class rule (first match wins), from the H codes and the substance's identity:
  1 gas phase -> compressed_gas
  2 H224 / H225 (flammable liquid cat 1-2), H228 (flammable solid), H250 / H260 / H261 (pyrophoric / water-reactive),
    H240-H242 (self-reactive / peroxides) -> flammable
  3 H270 / H271 / H272 (oxidizer) and not an acid -> oxidizer      (nitric acid stays with the acids, flagged oxidizer)
  4 H314 or H290 (corrosive) -> acid if a Bronsted / Lewis acid or acid-forming, base if a base   (ACIDS / BASES below)
  5 H226 (flammable liquid cat 3) -> flammable
  6 H300 / H301 / H310 / H311 / H330 / H331 (acute tox cat 1-3), H340 / H350 / H360* (CMR cat 1) -> toxic  (H370/H372 = flag only)
  7 must be kept cold (2-8 C or frozen) with no other class -> cold
  8 otherwise general
Every other matching group is kept in storage.flags (segregation), e.g. triethylamine: flammable + flags corrosive_base, toxic.
Idempotent: re-run it after any script that rebuilds substances.json (build_reactions.py keeps these keys since 2026-10-22).
Usage: python scripts/substances/apply_storage_hazard.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from new_substances import NEW_MIXTURES, NEW_PURE  # noqa: E402

SUBSTANCES = ROOT / "assets" / "reactions" / "substances.json"
RAW = ROOT / "references" / "substance_safety_raw"
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"

ACIDS = {"acetic_acid", "sulfuric_acid", "hydrochloric_acid", "nitric_acid", "hydriodic_acid", "dichloroacetic_acid", "acetic_anhydride",
         "maleic_acid", "succinic_acid", "salicylic_acid", "benzoic_acid", "boric_acid", "ascorbic_acid", "methyl_hydrogen_sulfate",
         "sulfur_dioxide_dissolved", "phenylboronic_acid", "sulfanilic_acid", "potassium_hydrogen_phthalate", "palmitic_acid",
         "zinc_chloride", "hydrogen_chloride", "dehydroascorbic_acid", "ytterbium_triflate", "sodium_dihydrogen_phosphate"}
BASES = {"sodium_hydroxide", "potassium_hydroxide", "ammonia", "triethylamine", "piperidine", "benzylamine", "diisopropylamine",
         "calcium_oxide", "sodium_carbonate", "potassium_carbonate", "sodium_hypochlorite", "aniline", "imidazole", "magnesium_oxide",
         "sodium_tetraborate_decahydrate", "lithium_chloride_never"}
GAS_ONLY = {"H220", "H221", "H280", "H281"}
TOXIC = {"H300", "H301", "H310", "H311", "H330", "H331", "H340", "H350", "H350i", "H360", "H360D", "H360F", "H360FD", "H360Fd", "H360Df"}
STOT = {"H370", "H372"}                       # target-organ toxicity: a segregation flag, not a storage class
TRANSIENT_GAS = {"water_vapor", "ethanol_vapor", "methanol_vapor", "acetone_vapor", "ethyl_acetate_vapor", "formaldehyde"}
COLD_RE = re.compile(r"(?:recommended storage temperature|store[sd]? (?:at|between)|storage temperature)\s*:?\s*(2\s*[-–]\s*8|-\s?20|−\s?20)\s*°?\s*C", re.I)


# ------------------------------------------------------------------------------------------------ PubChem parsing
def _load(sid: str, fn: str) -> dict | None:
    p = RAW / sid / fn
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d if "Record" in d else None


def _infos(rec: dict):
    refs = {r["ReferenceNumber"]: r for r in rec["Record"].get("Reference", [])}
    out = []

    def walk(sec):
        for s in sec.get("Section", []):
            walk(s)
        for inf in sec.get("Information", []):
            out.append(inf)
    walk(rec["Record"])
    return out, refs


def _text(inf) -> str:
    v = inf.get("Value", {})
    t = " ".join(m.get("String", "") for m in v.get("StringWithMarkup", []))
    if not t and "Number" in v:
        t = " ".join(str(x) for x in v["Number"]) + " " + v.get("Unit", "")
    return t.strip()


def parse_ghs(sid: str, phase: str, name_hint: str) -> dict:
    rec = _load(sid, "ghs.json")
    if rec is None:
        return {"hazard_codes": [], "basis": "no_data", "note": "PubChem has no GHS classification record for this CAS"}
    infos, refs = _infos(rec)
    cid = rec.get("_cid")
    by: dict[int, dict] = {}
    for inf in infos:
        rn = inf.get("ReferenceNumber")
        e = by.setdefault(rn, {"codes": {}, "pictograms": [], "signal": None, "summary": None, "not_classified": False})
        if inf.get("Name") == "GHS Hazard Statements":
            for m in inf["Value"].get("StringWithMarkup", []):
                s = m.get("String", "")
                if s.lower().startswith("not classified"):
                    e["not_classified"] = True
                mm = re.match(r"\s*((?:EU)?H\d{3}[A-Za-z]*(?:\+(?:EU)?H\d{3}[A-Za-z]*)*)\s*(?:\(\s*([<>]?)\s*([\d.]+)\s*%\))?", s)
                if mm:
                    pct = float(mm.group(3)) if mm.group(3) else None
                    for code in mm.group(1).split("+"):
                        e["codes"][code] = pct
        elif inf.get("Name") == "Pictogram(s)":
            for m in inf["Value"].get("StringWithMarkup", []):
                for mk in m.get("Markup", []):
                    g = re.search(r"(GHS0\d)", mk.get("URL", ""))
                    if g and g.group(1) not in e["pictograms"]:
                        e["pictograms"].append(g.group(1))
        elif inf.get("Name") == "Signal":
            e["signal"] = _text(inf)
        elif inf.get("Name") == "ECHA C&L Notifications Summary":
            e["summary"] = _text(inf)[:300]
    harmonised = [rn for rn in by if "1272/2008" in (refs.get(rn, {}).get("SourceName") or "") and by[rn]["codes"]]
    echa = [rn for rn in by if refs.get(rn, {}).get("SourceName", "").startswith("European Chemicals Agency") and (by[rn]["codes"] or by[rn]["not_classified"])]
    others = [rn for rn in by if by[rn]["codes"] and rn not in harmonised and rn not in echa]

    def score_name(rn):
        nm = (refs.get(rn, {}).get("Name") or "").lower()
        s = 0
        if phase in ("aqueous",) and any(k in nm for k in ("solution", "...%", "%", "acid")):
            s += 2
        if phase == "gas" and "solution" not in nm:
            s += 1
        if name_hint and name_hint.lower().split("_")[0] in nm:
            s += 1
        if "pyrophoric" in nm and "non-pyrophoric" not in nm and "non pyrophoric" not in nm:
            s -= 3                                # bulk lab metal, not the pyrophoric powder entry
        if "stabilised" in nm or "stabilized" in nm or "non-pyrophoric" in nm:
            s += 1
        return s

    notified = {}
    if echa:
        e0 = by[echa[0]]
        notified = {c: p for c, p in e0["codes"].items() if p is not None}
    if harmonised:
        rn = sorted(harmonised, key=score_name, reverse=True)[0]
        basis = "eu_harmonised_clp_annex_vi"
        codes = list(by[rn]["codes"])
    elif echa and (by[echa[0]]["codes"] or by[echa[0]]["not_classified"]):
        rn = echa[0]
        basis = "echa_cl_notifications_ge10pct"
        codes = [c for c, p in by[rn]["codes"].items() if p is None or p >= 10.0]
    elif others:
        pref = ["Hazardous Chemical Information System", "NITE-CMC", "Hazardous Substances Data Bank"]
        rn = sorted(others, key=lambda r: next((i for i, k in enumerate(pref) if k in (refs.get(r, {}).get("SourceName") or "")), 9))[0]
        basis = "other_authority"
        codes = list(by[rn]["codes"])
    else:
        return {"hazard_codes": [], "basis": "no_data", "note": "no hazard statements in the PubChem GHS record"}
    dropped = []
    if phase != "gas":
        dropped = [c for c in codes if c in GAS_ONLY]
        codes = [c for c in codes if c not in GAS_ONLY]
    e = by[rn]
    r = refs.get(rn, {})
    out = {"hazard_codes": sorted(set(codes), key=lambda c: (c.startswith("EU"), c)), "pictograms": e["pictograms"], "signal_word": e["signal"],
           "basis": basis, "not_classified": bool(e["not_classified"] and not codes),
           "source": {"database": "PubChem", "cid": cid, "url": PUBCHEM.format(cid=cid), "heading": "Safety and Hazards > GHS Classification",
                      "depositor": r.get("SourceName"), "record_name": r.get("Name"), "reference_number": rn, "accessed": rec.get("_fetched")}}
    if notified and basis != "echa_cl_notifications_ge10pct":
        out["notified_codes_pct"] = notified
    if dropped:
        out["dropped_gas_only_codes"] = dropped
    return out


def _num_temp(s: str):
    """First temperature in a PubChem string -> (value_C, unit) handling °F / K; None if not numeric."""
    s2 = s.replace("deg C", "°C").replace("deg F", "°F").replace("° C", "°C").replace("° F", "°F")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:to|-|–)?\s*(?:-?\d+(?:\.\d+)?)?\s*(°C|°F|K\b|C\b|F\b)", s2)
    if not m:
        m2 = re.search(r"\[(-?\d+(?:\.\d+)?)\]\s*°C", s2)
        return (float(m2.group(1)), "°C") if m2 else None
    v, u = float(m.group(1)), m.group(2)
    if u in ("°F", "F"):
        v = (v - 32) * 5 / 9
    elif u == "K":
        v = v - 273.15
    return (v, u)


def _pick(sid: str, fn: str, parse) -> dict | None:
    rec = _load(sid, fn)
    if rec is None:
        return None
    infos, refs = _infos(rec)
    cands = []
    for inf in infos:
        t = _text(inf)
        val = parse(t)
        if val is None:
            continue
        r = refs.get(inf.get("ReferenceNumber"), {})
        src = r.get("SourceName") or ""
        cite = " ".join(inf.get("Reference", []) or [])
        rank = 0 if ("Hazardous Substances Data Bank" in src and re.search(r"CRC|Haynes|Lide", cite)) else \
            1 if "Hazardous Substances Data Bank" in src else 2 if "International Chemical Safety Cards" in src else 3
        cands.append((rank, val, t, src, cite, r.get("Name"), inf.get("ReferenceNumber")))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], abs((c[1].get("T_C") or 25.0) - 25.0) if isinstance(c[1], dict) else 0))
    rank, val, t, src, cite, nm, rn = cands[0]
    return {"value": val, "text": t[:160], "source": {"database": "PubChem", "cid": rec.get("_cid"), "url": PUBCHEM.format(cid=rec.get("_cid")),
                                                      "heading": rec.get("_heading"), "depositor": src, "record_name": nm, "original_reference": cite[:240] or None,
                                                      "accessed": rec.get("_fetched")}}


def parse_bp(t: str):
    if re.search(r"decompos|sublim", t, re.I) and not re.search(r"\d\s*°", t):
        return None
    tt = _num_temp(t)
    if tt is None:
        return None
    if re.search(r"mm\s*Hg|kPa|torr|mbar", t, re.I) and not re.search(r"760\s*\[?\s*mm|101\.3", t):
        return None                                   # reduced-pressure boiling points are not the normal boiling point
    return {"T_C": tt[0]}


def parse_visc(t: str):
    """Dynamic viscosity closest to 25 C from a PubChem string; kinematic values and solution values are skipped."""
    best = None
    for seg in re.split(r";|\|\|", t):
        if re.search(r"kinematic|mm2/s|mm²/s|cSt|sq mm/s|soln|solution|%", seg, re.I):
            continue
        sg = seg.replace("·", ".").replace("*", ".").replace("deg C", "°C")
        for m in re.finditer(r"(\d+(?:\.\d+)?(?:[eE]-?\d+)?)\s*(mPa[\.\-\s]?s|centipoises?|cP\b|Pa[\.\-\s]?s|mN\.?s/(?:sq )?m2?)", sg, re.I):
            v = float(m.group(1))
            unit = m.group(2).lower()
            pa_s = v * (1.0 if unit.startswith("pa") else 1e-3)
            tt = re.search(r"(?:at|@)\s*(-?\d+(?:\.\d+)?)\s*(°C|°F|K\b|C\b)", sg[m.end():m.end() + 30], re.I)
            T = None
            if tt:
                T = float(tt.group(1))
                if tt.group(2) == "°F":
                    T = (T - 32) * 5 / 9
                elif tt.group(2) == "K":
                    T -= 273.15
            key = abs((T if T is not None else 40.0) - 25.0)
            if best is None or key < best[0]:
                best = (key, {"Pa_s": pa_s, "T_C": T})
    return best[1] if best else None


def cold_hint(sid: str):
    rec = _load(sid, "storage.json")
    if rec is None:
        return None
    infos, refs = _infos(rec)
    for inf in infos:
        t = _text(inf)
        m = COLD_RE.search(t)
        if m:
            r = refs.get(inf.get("ReferenceNumber"), {})
            rng = [2, 8] if "8" in m.group(1) else [-20, -20]
            return {"temperature_c": rng, "text": t[max(0, m.start() - 40): m.end() + 40], "depositor": r.get("SourceName"), "cid": rec.get("_cid")}
    return None


# ------------------------------------------------------------------------------------------------ storage class
def storage_for(sid: str, rec: dict, ghs: dict, cold: dict | None) -> dict:
    codes = set(ghs.get("hazard_codes") or [])
    phase = rec.get("phase")
    flags = []
    is_acid, is_base = sid in ACIDS, sid in BASES
    corrosive = bool(codes & {"H314", "H290"})
    if codes & {"H224", "H225", "H226", "H228", "H250", "H260", "H261", "H240", "H241", "H242"}:
        flags.append("flammable")
    if codes & {"H270", "H271", "H272"}:
        flags.append("oxidizer")
    if corrosive:
        flags.append("corrosive_acid" if is_acid else "corrosive_base" if is_base else "corrosive")
    if codes & TOXIC:
        flags.append("toxic")
    if codes & STOT:
        flags.append("target_organ_toxicity")
    if codes & {"H400", "H410", "H411"}:
        flags.append("environment")
    if cold:
        flags.append("cold_recommended")
    if sid in TRANSIENT_GAS:
        cls, rule = "general", "vapour / gas species that exists only in reaction ledgers (the stock is the liquid or solution)"
    elif phase == "gas":
        cls, rule = "compressed_gas", "1 gas"
    elif codes & {"H224", "H225", "H228", "H250", "H260", "H261", "H240", "H241", "H242"}:
        cls, rule = "flammable", "2 flammable cat 1-2 / flammable solid / pyrophoric / self-reactive"
    elif codes & {"H270", "H271", "H272"} and not is_acid:
        cls, rule = "oxidizer", "3 oxidizer"
    elif corrosive and is_acid:
        cls, rule = "acid", "4 corrosive acid"
    elif corrosive and is_base:
        cls, rule = "base", "4 corrosive base"
    elif "H226" in codes:
        cls, rule = "flammable", "5 flammable liquid cat 3"
    elif codes & TOXIC:
        cls, rule = "toxic", "6 acute toxicity cat 1-3 / CMR cat 1"
    elif corrosive:
        cls, rule = "toxic" if codes & TOXIC else "general", "4 corrosive (neither acid nor base) -> see flags"
    elif cold:
        cls, rule = "cold", "7 keep cold"
    else:
        cls, rule = "general", "8 no storage-relevant hazard"
    return {"class": cls, "flags": [f for f in flags if f != cls], "rule": rule,
            "temperature_c": (cold or {}).get("temperature_c"),
            "evidence": "derived" if ghs.get("basis") not in (None, "no_data") else "assumption",
            "source": "storage rule in scripts/substances/apply_storage_hazard.py applied to the ghs codes" +
                      (f"; cold: PubChem CID {cold['cid']} storage conditions ({cold['depositor']})" if cold else "")}


# ------------------------------------------------------------------------------------------------ main
def new_entries() -> dict:
    out = {}
    for sid, (cas, name_zh, phase, fid, note) in NEW_PURE.items():
        if sid == "formazin":
            continue
        props = json.loads((RAW / sid / "props.json").read_text(encoding="utf-8"))["PropertyTable"]["Properties"][0]
        dens = _pick(sid, "density.json", lambda t: (lambda m: {"g_cm3": float(m.group(1))} if m else None)(
            re.search(r"^\s*(?:approx(?:\.|imately)?\s+)?(\d+(?:\.\d+)?)\s*(?:g/cu\s*cm|g/cm³|g/cm3|\(NIOSH|\(NTP|at|@|$)", t)) if "kg/cu m" not in t else
            (lambda m: {"g_cm3": float(m.group(1)) / 1000} if m else None)(re.search(r"(\d+(?:\.\d+)?)\s*kg/cu m", t)))
        dens_val = None if phase == "gas" or dens is None else round(dens["value"]["g_cm3"] * 1000.0, 1)
        e = {"name_zh": name_zh, "formula": props["MolecularFormula"], "molar_mass_kg_mol": round(float(props["MolecularWeight"]) / 1000.0, 6),
             "phase": phase, "density_kg_m3": dens_val, "fid_visible": fid, "evidence": "documented",
             "source": f"PubChem CID {props['CID']} (molecular formula / weight" + (", density: " + dens["source"]["depositor"] if dens_val else "") + ")",
             "cas": cas, "added_for": "default-lab inventory (2026-10-22)", "use_note": note}
        if dens_val:
            e["density_source"] = dens["source"]
        out[sid] = e
    cas_f, name_zh_f, phase_f, fid_f, note_f = NEW_PURE["formazin"]
    out["formazin"] = {"name_zh": name_zh_f, "formula": "(C2H4N2)n polymer suspension (hexamethylenetetramine + hydrazine sulfate product)",
                       "molar_mass_kg_mol": None, "phase": "aqueous", "density_kg_m3": None, "fid_visible": False, "evidence": "documented",
                       "source": "CAS 9004-77-7 (formazin polymer); PubChem resolves this CAS to an unrelated compound, so no PubChem properties are used",
                       "cas": cas_f, "kind": "mixture", "added_for": "default-lab inventory (2026-10-22)", "use_note": note_f}
    for sid, m in NEW_MIXTURES.items():
        out[sid] = {"name_zh": m["name_zh"], "formula": m["formula"], "molar_mass_kg_mol": None, "phase": m["phase"], "density_kg_m3": m["density_kg_m3"],
                    "fid_visible": False, "evidence": "assumption", "source": "product-type description (composition typical of the product class)",
                    "kind": "mixture", "composition": m["composition"], "added_for": "default-lab inventory (2026-10-22)", "use_note": m["note"]}
    return out


MIXTURE_STORAGE = {
    "formazin": ("general", [], None, "4000 NTU formazin suspensions are kept at room temperature, protected from light (assumption: typical supplier practice)"),
    "polystyrene_latex": ("cold", [], [2, 8], "latex standards are stored at 2-8 C, not frozen (assumption: typical supplier practice)"),
    "calibration_beads_fluorescent": ("cold", ["toxic_trace_azide"], [2, 8], "bead standards are stored at 2-8 C in the dark (assumption: typical supplier practice)"),
    "protein_ladder_prestained": ("cold", [], [-20, -20], "prestained ladders are stored at -20 C (assumption: typical supplier practice)"),
    "hbs_ep_plus_10x": ("cold", [], [2, 8], "HBS-EP+ concentrate is stored at 2-8 C (assumption: typical supplier practice)"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = json.loads(SUBSTANCES.read_text(encoding="utf-8"))
    subs = data["substances"]
    added = []
    for sid, e in new_entries().items():
        if sid not in subs:
            subs[sid] = e
            added.append(sid)
    report = {"storage": {}, "liquids_missing_bp": [], "liquids_missing_visc": [], "no_ghs": []}
    for sid, rec in subs.items():
        if rec.get("kind") == "mixture":
            cls, flags, temp, why = MIXTURE_STORAGE[sid]
            rec["ghs"] = {"hazard_codes": [], "basis": "no_data", "note": "mixture / product: classification depends on the supplier's SDS (not in PubChem)"}
            rec["storage_class"] = cls
            rec["storage"] = {"class": cls, "flags": flags, "rule": "mixture: supplier storage condition", "temperature_c": temp, "evidence": "assumption", "source": why}
            report["storage"].setdefault(cls, []).append(sid)
            continue
        ghs = parse_ghs(sid, rec.get("phase"), rec.get("name_zh", ""))
        if ghs.get("basis") == "no_data":
            report["no_ghs"].append(sid)
        cold = cold_hint(sid)
        st = storage_for(sid, rec, ghs, cold)
        if rec.get("cas") is None:
            ghs = {"hazard_codes": [], "basis": "no_data", "note": "no CAS number (reaction intermediate / transient species): never stocked as a reagent"}
            st = {"class": "general", "flags": [], "rule": "transient species, not stocked", "temperature_c": None, "evidence": "assumption",
                  "source": "no CAS; exists only inside reaction ledgers"}
        rec["ghs"] = ghs
        rec["storage_class"] = st["class"]
        rec["storage"] = st
        report["storage"].setdefault(st["class"], []).append(sid)
        if rec.get("phase") == "liquid":
            bp = _pick(sid, "bp.json", parse_bp)
            if bp:
                rec["boiling_point_K"] = round(bp["value"]["T_C"] + 273.15, 2)
                rec["boiling_point_source"] = {**bp["source"], "text": bp["text"]}
            else:
                rec["boiling_point_K"] = None
                rec["boiling_point_note"] = "not sourced: PubChem has no normal boiling point for this compound (or only reduced-pressure / decomposition values)"
                report["liquids_missing_bp"].append(sid)
            vi = _pick(sid, "viscosity.json", parse_visc)
            if vi:
                rec["viscosity_Pa_s"] = float(f"{vi['value']['Pa_s']:.4g}")
                rec["viscosity_T_K"] = round(vi["value"]["T_C"] + 273.15, 2) if vi["value"]["T_C"] is not None else None
                rec["viscosity_source"] = {**vi["source"], "text": vi["text"]}
            else:
                rec["viscosity_Pa_s"] = None
                rec["viscosity_T_K"] = None
                rec["viscosity_note"] = "not sourced: PubChem has no dynamic viscosity for this compound"
                report["liquids_missing_visc"].append(sid)
    data["schema_version"] = "1.1.0"
    data["schema_notes_zh"] = (
        "1.1.0（2026-10-22，向后兼容，只增不改）：每个物质增加 ghs（危害代码、象形图、信号词、依据与 PubChem 来源）、storage_class / storage"
        "（存放类别、隔离标记、规则、温度）；液体增加 boiling_point_K 与 viscosity_Pa_s / viscosity_T_K（各带 PubChem 来源与原始文献）。"
        "新增默认实验室库存需要的物质（added_for）；混合物 kind = mixture、molar_mass_kg_mol 为 null。规则见 scripts/substances/apply_storage_hazard.py。")
    data["storage_classes"] = ["flammable", "acid", "base", "oxidizer", "toxic", "cold", "compressed_gas", "general"]
    if not a.dry_run:
        SUBSTANCES.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"added": added, "classes": {k: len(v) for k, v in report["storage"].items()}, "no_ghs": report["no_ghs"],
                      "liquids_missing_bp": report["liquids_missing_bp"], "liquids_missing_visc": report["liquids_missing_visc"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
