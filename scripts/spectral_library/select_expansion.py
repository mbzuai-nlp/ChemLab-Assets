"""Select the compounds that extend the shared spectral library (2026-10-14 expansion, 95 -> ~200).

Phases (all idempotent; downloads are cached):
  rank    scan every template library (same discovery as ``build_crosswalk.py``) for CAS numbers and rank the CAS that
          are not yet in ``compounds_list.py`` by the number of distinct instruments (asset_type_id) whose libraries
          contain them (shared spectral_library files excluded);
  probe   for every candidate with >= 2 instruments plus every molecular substances.json species: PubChem properties
          (identity, formula, SMILES) and the NIST WebBook Mask=2000 page (GC retention tables + the list of other
          data NIST holds: IR, mass spectrum, UV/Vis, phase change).  These are exactly the requests
          ``fetch_sources.py`` makes for ``pubchem_props.json`` / ``gc.html``, cached under
          ``outputs/spectral_library_expansion/probe_raw/<cas>/`` and later copied into ``references/spectral_raw/<id>/``;
  select  classify (molecular vs salt / hydrate / metal / element / network solid), apply the selection rules and write
          ``outputs/spectral_library_expansion/selection.json``.

Selection rules (in this order; new ids and Chinese names come from ``EXPANSION_META`` below):
  1. every molecular (non-salt, non-mineral, non-metal) species of substances.json that is not yet in the table;
  2. every curated common lab solvent / reagent (``COMMON_LAB``) found in >= 2 instruments' libraries;
  3. remaining molecular candidates ranked by (instruments desc, NIST data types desc, libraries desc, records desc)
     until the table holds ``--target`` compounds.
  Rules 2-3 require NIST WebBook data of at least one spectral kind (GC retention index, IR, EI-MS or UV/Vis).

Usage: python -I select_expansion.py [--phase rank|probe|select|all] [--delay 1.0] [--target 200]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_crosswalk as bc  # noqa: E402
from compounds_list import COMPOUNDS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUTDIR = ROOT / "outputs" / "spectral_library_expansion"
PROBE = OUTDIR / "probe_raw"
RANK_FILE = OUTDIR / "candidates_ranked.json"
SELECTION = OUTDIR / "selection.json"
RAW = ROOT / "references" / "spectral_raw"

# substances.json species that are salts, hydrates, metals, minerals or ionic solids (rule 1 skips them)
NON_MOLECULAR_SUBSTANCES = {
    "calcium_carbonate", "calcium_oxide", "sodium_hydroxide", "sodium_chloride", "sodium_bicarbonate", "sodium_carbonate",
    "silver_nitrate", "silver_chloride", "sodium_nitrate", "sodium_acetate", "manganese_dioxide", "copper_sulfate_pentahydrate",
    "copper_sulfate_trihydrate", "copper_sulfate_monohydrate", "copper_sulfate", "copper_oxide", "copper", "basic_copper_carbonate",
    "zinc_carbonate", "zinc_oxide", "nickel_oxide", "nickel", "iron_oxide_hematite", "iron_oxide_magnetite", "iron",
    "magnesium_hydroxide", "magnesium_oxide", "magnesium_carbonate", "calcium_oxalate_monohydrate", "calcium_oxalate",
    "calcium_sulfate_dihydrate", "calcium_sulfate_hemihydrate", "potassium_chlorate", "potassium_chloride", "palladium",
}
# phase variants of compounds already in the table (same molecule, linked by the crosswalk alias table)
PHASE_VARIANTS = {"water_vapor", "ethanol_vapor", "methanol_vapor", "acetone_vapor", "ethyl_acetate_vapor"}
# CAS of the molecular substances.json species (several carry no CAS in substances.json)
SUBSTANCE_CAS = {"sulfuric_acid": "7664-93-9", "hydrochloric_acid": "7647-01-0", "oxygen": "7782-44-7", "hydrogen": "1333-74-0",
                 "carbon_monoxide": "630-08-0", "maleic_acid": "110-16-7", "succinic_acid": "110-15-6"}
NETWORK_SOLIDS = {"7631-86-9", "1344-28-1", "13463-67-7", "1317-33-5", "1306-06-5"}  # silica, alumina, TiO2, MoS2, hydroxylapatite

# curated common lab solvents / reagents (rule 2) -- CAS -> why
COMMON_LAB = {
    "121-44-8": "base (Et3N)", "110-71-4": "solvent (DME)", "108-87-2": "solvent", "109-86-4": "solvent", "109-89-7": "base",
    "127-18-4": "solvent", "540-84-1": "solvent / octane standard", "57-55-6": "solvent / humectant", "79-01-6": "solvent",
    "107-83-5": "solvent (isohexane)", "78-78-4": "solvent", "872-50-4": "solvent (NMP)", "75-52-5": "solvent",
    "108-32-7": "solvent / electrolyte", "105-58-8": "solvent / electrolyte", "120-92-3": "solvent", "96-22-0": "solvent",
    "287-92-3": "solvent", "98-82-8": "solvent", "108-67-8": "solvent", "108-86-1": "solvent / reagent", "110-80-5": "solvent",
    "111-96-6": "solvent (diglyme)", "108-65-6": "solvent (PGMEA)", "110-63-4": "solvent / monomer", "96-48-0": "solvent (GBL)",
    "107-18-6": "reagent", "108-05-4": "monomer", "98-00-0": "reagent", "141-97-9": "reagent", "149-73-5": "reagent",
    "122-51-0": "reagent", "56-40-6": "buffer / reagent", "77-92-9": "buffer / reagent", "10043-35-3": "buffer / reagent",
    "288-32-4": "buffer / reagent", "302-01-2": "reagent", "123-31-9": "reagent", "7697-37-2": "acid", "7664-39-3": "acid",
    "7664-38-2": "acid", "57-13-6": "reagent", "57-50-1": "reagent", "50-00-0": "reagent", "144-62-7": "acid / standard",
    "88-99-3": "acid", "77-86-1": "buffer (Tris)", "141-43-5": "base", "111-42-2": "base", "102-71-6": "base",
    "107-15-3": "base / ligand", "109-73-9": "base", "60-24-2": "reducing agent", "106-51-4": "oxidant",
    "109-52-4": "acid", "79-31-2": "acid", "74-82-8": "gas", "7446-09-5": "gas", "7783-06-4": "gas",
    "106-97-8": "gas", "74-98-6": "gas", "74-84-0": "gas", "10024-97-2": "gas", "7727-37-9": "gas", "2551-62-4": "gas",
    "60-00-4": "chelator (EDTA)", "108-89-4": "base / solvent", "109-06-8": "base / solvent", "108-99-6": "base / solvent",
    "124-40-3": "base", "74-89-5": "base", "75-50-3": "base",
}

# new compound rows: CAS -> (id, English name, Chinese name, class)
EXPANSION_META: dict[str, tuple[str, str, str, str]] = {
    # rule 1: molecular reaction species of substances.json (ids = substances.json ids)
    "7664-93-9": ("sulfuric_acid", "sulfuric acid", "硫酸", "inorganic_acid"),
    "7647-01-0": ("hydrochloric_acid", "hydrogen chloride (hydrochloric acid solute)", "氯化氢（盐酸溶质）", "inorganic_acid"),
    "7782-44-7": ("oxygen", "oxygen", "氧气", "gas"),
    "1333-74-0": ("hydrogen", "hydrogen", "氢气", "gas"),
    "630-08-0": ("carbon_monoxide", "carbon monoxide", "一氧化碳", "gas"),
    "110-16-7": ("maleic_acid", "maleic acid", "马来酸（顺丁烯二酸）", "carboxylic_acid"),
    "110-15-6": ("succinic_acid", "succinic acid", "琥珀酸（丁二酸）", "carboxylic_acid"),
    # rule 2: common lab solvents / reagents / gases
    "121-44-8": ("triethylamine", "triethylamine", "三乙胺", "amine"),
    "110-71-4": ("dimethoxyethane_1_2", "1,2-dimethoxyethane", "乙二醇二甲醚（DME）", "ether"),
    "108-87-2": ("methylcyclohexane", "methylcyclohexane", "甲基环己烷", "alkane"),
    "109-86-4": ("methoxyethanol_2", "2-methoxyethanol", "乙二醇单甲醚（2-甲氧基乙醇）", "glycol_ether"),
    "109-89-7": ("diethylamine", "diethylamine", "二乙胺", "amine"),
    "127-18-4": ("tetrachloroethylene", "tetrachloroethylene", "四氯乙烯", "halogenated"),
    "540-84-1": ("isooctane", "2,2,4-trimethylpentane", "异辛烷（2,2,4-三甲基戊烷）", "alkane"),
    "57-55-6": ("propylene_glycol", "1,2-propanediol", "1,2-丙二醇", "polyol"),
    "79-01-6": ("trichloroethylene", "trichloroethylene", "三氯乙烯", "halogenated"),
    "107-83-5": ("methylpentane_2", "2-methylpentane", "2-甲基戊烷", "alkane"),
    "78-78-4": ("isopentane", "2-methylbutane", "异戊烷（2-甲基丁烷）", "alkane"),
    "872-50-4": ("nmp", "N-methyl-2-pyrrolidone", "N-甲基吡咯烷酮", "amide"),
    "75-52-5": ("nitromethane", "nitromethane", "硝基甲烷", "nitro"),
    "108-32-7": ("propylene_carbonate", "propylene carbonate", "碳酸丙烯酯", "ester"),
    "105-58-8": ("diethyl_carbonate", "diethyl carbonate", "碳酸二乙酯", "ester"),
    "120-92-3": ("cyclopentanone", "cyclopentanone", "环戊酮", "ketone"),
    "96-22-0": ("pentan_3_one", "3-pentanone", "3-戊酮（二乙基酮）", "ketone"),
    "287-92-3": ("cyclopentane", "cyclopentane", "环戊烷", "alkane"),
    "98-82-8": ("cumene", "isopropylbenzene", "异丙苯", "aromatic"),
    "108-67-8": ("mesitylene", "1,3,5-trimethylbenzene", "均三甲苯", "aromatic"),
    "108-86-1": ("bromobenzene", "bromobenzene", "溴苯", "halogenated"),
    "110-80-5": ("ethoxyethanol_2", "2-ethoxyethanol", "乙二醇单乙醚（2-乙氧基乙醇）", "glycol_ether"),
    "111-96-6": ("diglyme", "bis(2-methoxyethyl) ether", "二乙二醇二甲醚（二甘醇二甲醚）", "ether"),
    "108-65-6": ("pgmea", "propylene glycol methyl ether acetate", "丙二醇甲醚醋酸酯（PGMEA）", "ester"),
    "110-63-4": ("butanediol_1_4", "1,4-butanediol", "1,4-丁二醇", "polyol"),
    "96-48-0": ("gamma_butyrolactone", "gamma-butyrolactone", "γ-丁内酯", "ester"),
    "107-18-6": ("allyl_alcohol", "allyl alcohol", "烯丙醇", "alcohol"),
    "108-05-4": ("vinyl_acetate", "vinyl acetate", "乙酸乙烯酯", "ester"),
    "98-00-0": ("furfuryl_alcohol", "furfuryl alcohol", "糠醇", "alcohol"),
    "141-97-9": ("ethyl_acetoacetate", "ethyl acetoacetate", "乙酰乙酸乙酯", "ester"),
    "149-73-5": ("trimethyl_orthoformate", "trimethyl orthoformate", "原甲酸三甲酯", "ester"),
    "122-51-0": ("triethyl_orthoformate", "triethyl orthoformate", "原甲酸三乙酯", "ester"),
    "56-40-6": ("glycine", "glycine", "甘氨酸", "amino_acid"),
    "77-92-9": ("citric_acid", "citric acid", "柠檬酸", "carboxylic_acid"),
    "10043-35-3": ("boric_acid", "boric acid", "硼酸", "inorganic_acid"),
    "288-32-4": ("imidazole", "imidazole", "咪唑", "heterocycle"),
    "302-01-2": ("hydrazine", "hydrazine", "肼", "inorganic"),
    "123-31-9": ("hydroquinone", "hydroquinone", "对苯二酚", "phenol"),
    "7697-37-2": ("nitric_acid", "nitric acid", "硝酸", "inorganic_acid"),
    "57-13-6": ("urea", "urea", "尿素", "amide"),
    "57-50-1": ("sucrose", "sucrose", "蔗糖", "carbohydrate"),
    "50-00-0": ("formaldehyde", "formaldehyde", "甲醛", "aldehyde"),
    "144-62-7": ("oxalic_acid", "oxalic acid", "草酸", "carboxylic_acid"),
    "88-99-3": ("phthalic_acid", "phthalic acid", "邻苯二甲酸", "carboxylic_acid"),
    "77-86-1": ("tris", "tris(hydroxymethyl)aminomethane", "三羟甲基氨基甲烷（Tris）", "amine"),
    "141-43-5": ("ethanolamine", "ethanolamine", "乙醇胺", "amine"),
    "111-42-2": ("diethanolamine", "diethanolamine", "二乙醇胺", "amine"),
    "102-71-6": ("triethanolamine", "triethanolamine", "三乙醇胺", "amine"),
    "107-15-3": ("ethylenediamine", "ethylenediamine", "乙二胺", "amine"),
    "109-73-9": ("butylamine", "n-butylamine", "正丁胺", "amine"),
    "60-24-2": ("mercaptoethanol_2", "2-mercaptoethanol", "2-巯基乙醇", "thiol"),
    "106-51-4": ("benzoquinone_1_4", "1,4-benzoquinone", "对苯醌", "quinone"),
    "109-52-4": ("pentanoic_acid", "pentanoic acid", "戊酸", "carboxylic_acid"),
    "79-31-2": ("isobutyric_acid", "isobutyric acid", "异丁酸", "carboxylic_acid"),
    "74-82-8": ("methane", "methane", "甲烷", "alkane"),
    "7446-09-5": ("sulfur_dioxide", "sulfur dioxide", "二氧化硫", "gas"),
    "7783-06-4": ("hydrogen_sulfide", "hydrogen sulfide", "硫化氢", "gas"),
    "106-97-8": ("butane", "butane", "正丁烷", "alkane"),
    "74-98-6": ("propane", "propane", "丙烷", "alkane"),
    "74-84-0": ("ethane", "ethane", "乙烷", "alkane"),
    "10024-97-2": ("nitrous_oxide", "nitrous oxide", "一氧化二氮", "gas"),
    "7727-37-9": ("nitrogen", "nitrogen", "氮气", "gas"),
    "2551-62-4": ("sulfur_hexafluoride", "sulfur hexafluoride", "六氟化硫", "gas"),
    "60-00-4": ("edta", "ethylenediaminetetraacetic acid", "乙二胺四乙酸（EDTA）", "carboxylic_acid"),
    "108-89-4": ("picoline_4", "4-methylpyridine", "4-甲基吡啶", "heterocycle"),
    "109-06-8": ("picoline_2", "2-methylpyridine", "2-甲基吡啶", "heterocycle"),
    "108-99-6": ("picoline_3", "3-methylpyridine", "3-甲基吡啶", "heterocycle"),
    "124-40-3": ("dimethylamine", "dimethylamine", "二甲胺", "amine"),
    "74-89-5": ("methylamine", "methylamine", "甲胺", "amine"),
    "75-50-3": ("trimethylamine", "trimethylamine", "三甲胺", "amine"),
    "7664-39-3": ("hydrogen_fluoride", "hydrogen fluoride", "氟化氢", "inorganic_acid"),
    "7664-38-2": ("phosphoric_acid", "phosphoric acid", "磷酸", "inorganic_acid"),
    # rule 3: ranked by instrument linkage (then NIST data kinds)
    "115-25-3": ("octafluorocyclobutane", "octafluorocyclobutane (RC318)", "八氟环丁烷（RC318）", "halogenated"),
    "75-43-4": ("dichlorofluoromethane", "dichlorofluoromethane (R21)", "二氯一氟甲烷（R21）", "halogenated"),
    "75-68-3": ("chlorodifluoroethane_r142b", "1-chloro-1,1-difluoroethane (R142b)", "1-氯-1,1-二氟乙烷（R142b）", "halogenated"),
    "75-69-4": ("trichlorofluoromethane", "trichlorofluoromethane (R11)", "三氯一氟甲烷（R11）", "halogenated"),
    "76-14-2": ("dichlorotetrafluoroethane_r114", "1,2-dichloro-1,1,2,2-tetrafluoroethane (R114)", "1,2-二氯四氟乙烷（R114）", "halogenated"),
    "431-63-0": ("hexafluoropropane_r236ea", "1,1,1,2,3,3-hexafluoropropane (R236ea)", "1,1,1,2,3,3-六氟丙烷（R236ea）", "halogenated"),
    "690-39-1": ("hexafluoropropane_r236fa", "1,1,1,3,3,3-hexafluoropropane (R236fa)", "1,1,1,3,3,3-六氟丙烷（R236fa）", "halogenated"),
    "110-87-2": ("dihydropyran", "3,4-dihydro-2H-pyran", "3,4-二氢-2H-吡喃", "ether"),
    "137-18-8": ("dimethylbenzoquinone_2_5", "2,5-dimethyl-1,4-benzoquinone", "2,5-二甲基对苯醌", "quinone"),
    "59-47-2": ("mephenesin", "mephenesin", "美芬新（甲苯丙醇）", "pharmaceutical"),
    "83-72-7": ("lawsone", "2-hydroxy-1,4-naphthoquinone (lawsone)", "2-羟基-1,4-萘醌（指甲花醌）", "quinone"),
    "92-82-0": ("phenazine", "phenazine", "吩嗪", "heterocycle"),
    "103-82-2": ("phenylacetic_acid", "phenylacetic acid", "苯乙酸", "carboxylic_acid"),
    "57-44-3": ("barbital", "barbital", "巴比妥", "pharmaceutical"),
    "103-79-7": ("phenylacetone", "1-phenyl-2-propanone", "苯基丙酮（1-苯基-2-丙酮）", "ketone"),
    "106-62-7": ("dipropylene_glycol", "2-(2-hydroxypropoxy)-1-propanol (dipropylene glycol isomer)", "二丙二醇（2-(2-羟丙氧基)-1-丙醇）", "polyol"),
    "106-65-0": ("dimethyl_succinate", "dimethyl succinate", "丁二酸二甲酯", "ester"),
    "107-19-7": ("propargyl_alcohol", "propargyl alcohol", "炔丙醇", "alcohol"),
    "108-59-8": ("dimethyl_malonate", "dimethyl malonate", "丙二酸二甲酯", "ester"),
    "109-49-9": ("hex_5_en_2_one", "5-hexen-2-one", "5-己烯-2-酮", "ketone"),
    "110-13-4": ("hexanedione_2_5", "2,5-hexanedione", "2,5-己二酮", "ketone"),
    "110-64-5": ("but_2_ene_1_4_diol", "2-butene-1,4-diol", "2-丁烯-1,4-二醇", "polyol"),
    "111-45-5": ("allyloxyethanol", "2-(allyloxy)ethanol", "乙二醇单烯丙基醚", "glycol_ether"),
    "111-90-0": ("ethoxyethoxyethanol", "2-(2-ethoxyethoxy)ethanol", "二乙二醇单乙醚", "glycol_ether"),
    "112-34-5": ("butoxyethoxyethanol", "2-(2-butoxyethoxy)ethanol", "二乙二醇单丁醚", "glycol_ether"),
    "112-36-7": ("diethylene_glycol_diethyl_ether", "diethylene glycol diethyl ether", "二乙二醇二乙醚", "ether"),
    "115-07-1": ("propene", "propene", "丙烯", "alkene"),
    "115-19-5": ("methylbutynol", "2-methyl-3-butyn-2-ol", "2-甲基-3-丁炔-2-醇", "alcohol"),
    "123-63-7": ("paraldehyde", "paraldehyde", "三聚乙醛", "ether"),
    "137-32-6": ("methylbutan_2_1_ol", "2-methyl-1-butanol", "2-甲基-1-丁醇", "alcohol"),
    "144-19-4": ("trimethylpentanediol", "2,2,4-trimethyl-1,3-pentanediol", "2,2,4-三甲基-1,3-戊二醇", "polyol"),
    "462-95-3": ("diethoxymethane", "diethoxymethane", "二乙氧基甲烷", "ether"),
}


def log(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------------------------------------------- rank
def rank() -> dict:
    libs = bc.scan_all()
    spectral = {rel for rel in libs if rel.startswith("assets/reactions/spectral_library/")}
    have = {row[3] for row in COMPOUNDS}
    inst, libsets, names, recs, prim_inst = defaultdict(set), defaultdict(set), defaultdict(Counter), defaultdict(list), defaultdict(set)
    for rel, info in libs.items():
        if rel in spectral:
            continue
        for r in info["records"]:
            for kind in ("primary", "components"):
                for cas in r[kind]["cas"]:
                    inst[cas].update(info["asset_type_ids"])
                    libsets[cas].add(rel)
                    if kind == "primary":
                        prim_inst[cas].update(info["asset_type_ids"])
                    names[cas][r["name"] or r["record_id"]] += 1
                    if len(recs[cas]) < 12:
                        recs[cas].append({"library": rel, "record_id": r["record_id"], "role": kind})
    rows = []
    for cas, aids in inst.items():
        rows.append({"cas": cas, "in_table": cas in have, "n_instruments": len(aids), "n_instruments_as_primary": len(prim_inst[cas]),
                     "n_libraries": len(libsets[cas]), "n_records": sum(names[cas].values()), "instruments": sorted(aids),
                     "libraries": sorted(libsets[cas]), "names_seen": [n for n, _ in names[cas].most_common(4)], "example_records": recs[cas]})
    rows.sort(key=lambda r: (-r["n_instruments"], -r["n_libraries"], -r["n_records"], r["cas"]))
    out = {"built_by": "scripts/spectral_library/select_expansion.py --phase rank", "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "n_libraries_scanned": len(libs) - len(spectral), "n_cas_total": len(rows), "n_cas_new": sum(1 for r in rows if not r["in_table"]),
           "candidates": rows}
    OUTDIR.mkdir(parents=True, exist_ok=True)
    RANK_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"ranked {len(rows)} CAS ({out['n_cas_new']} new) from {out['n_libraries_scanned']} libraries -> {RANK_FILE.relative_to(ROOT)}")
    return out


# ------------------------------------------------------------------------------------------------------ probe
def probe_targets(ranked: dict) -> list[str]:
    cas = [r["cas"] for r in ranked["candidates"] if not r["in_table"] and r["n_instruments"] >= 2]
    for sid, c in SUBSTANCE_CAS.items():
        if c not in cas:
            cas.append(c)
    return cas


def probe(ranked: dict, delay: float) -> None:
    import requests
    import fetch_sources as fs
    targets = probe_targets(ranked)
    PROBE.mkdir(parents=True, exist_ok=True)
    props = "MolecularFormula,MolecularWeight,SMILES,ConnectivitySMILES,XLogP,IUPACName,Title"
    with requests.Session() as session:
        # pass A: PubChem identity for every target (cheap) -> classification
        for cas in targets:
            d = PROBE / cas
            d.mkdir(exist_ok=True)
            fs.get(session, f"{fs.PUBCHEM}/pug/compound/name/{cas}/property/{props}/JSON", None, d / "pubchem_props.json", 0.25, False, log)
        # pass B: NIST Mask=2000 page only for molecular (or unresolved) candidates -- be polite to NIST
        pending = []
        for cas in targets:
            try:
                p = json.loads((PROBE / cas / "pubchem_props.json").read_text())["PropertyTable"]["Properties"][0]
            except Exception:
                p = {}
            if classify(cas, p) in ("molecular", "unresolved"):
                pending.append(cas)
        log(f"  NIST probe for {len(pending)} molecular/unresolved of {len(targets)} candidates")
        for attempt in range(3):
            failed = []
            for i, cas in enumerate(pending, 1):
                a = fs.get(session, fs.NIST, {"ID": fs.nist_id(cas), "Mask": "2000", "Units": "SI"}, PROBE / cas / "gc.html", delay, False, log)
                if a is None:
                    failed.append(cas)
                if i % 25 == 0:
                    log(f"  NIST probe pass {attempt + 1}: {i}/{len(pending)}")
            if not failed:
                break
            log(f"  {len(failed)} NIST probes failed (HTTP 429/5xx / network); waiting 60 s before retrying")
            time.sleep(60)
            pending = failed
    log(f"probe done: {len(targets)} CAS")


def nist_availability(html: str) -> dict:
    if not html:
        return {"found": False}
    found = "Registry Number Not Found" not in html and "<h1 id=\"Top\">" in html
    tables = re.findall(r"<h3[^>]*>\s*(Kovats|Van Den Dool|Normal alkane)[^<]*</h3>", html)
    return {"found": found, "gc_ri_tables": len(tables), "ir": "Mask=80#IR-Spec" in html, "ms": "Mask=200#Mass-Spec" in html,
            "uv": "Mask=400#UV-Vis-Spec" in html, "phase_change": "Mask=4#Thermo-Phase" in html,
            "nist_name": (re.search(r'<h1 id="Top">(.*?)</h1>', html) or [None, None])[1]}


def classify(cas: str, props: dict) -> str:
    if cas in NETWORK_SOLIDS:
        return "network_solid"
    smi = props.get("SMILES") or props.get("ConnectivitySMILES")
    if not smi:
        return "unresolved"
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return "unresolved"
    nonmetals = {"H", "B", "C", "N", "O", "F", "Si", "P", "S", "Cl", "Se", "Br", "I", "As"}
    symbols = [a.GetSymbol() for a in mol.GetAtoms()]
    if any(s not in nonmetals and s not in {"He", "Ne", "Ar", "Kr", "Xe"} for s in symbols):
        return "metal_or_salt"
    if "." in smi:
        return "salt_hydrate_or_mixture"
    if mol.GetNumAtoms() + sum(a.GetTotalNumHs() for a in mol.GetAtoms()) == 1:   # Ar, He, [Si] ... (HCl, CH4, H2S are molecules)
        return "element_atomic"
    return "molecular"


# ----------------------------------------------------------------------------------------------------- select
def select(ranked: dict, target: int) -> dict:
    subs = json.loads((ROOT / "assets" / "reactions" / "substances.json").read_text(encoding="utf-8"))["substances"]
    have_ids = {row[0] for row in COMPOUNDS}
    have_cas = {row[3] for row in COMPOUNDS}
    by_cas = {r["cas"]: r for r in ranked["candidates"]}
    info = {}
    for cas in probe_targets(ranked):
        d = PROBE / cas
        html = (d / "gc.html").read_text(encoding="utf-8", errors="replace") if (d / "gc.html").exists() else ""
        try:
            props = json.loads((d / "pubchem_props.json").read_text())["PropertyTable"]["Properties"][0]
        except Exception:
            props = {}
        nist = nist_availability(html)
        cls = classify(cas, props)
        r = by_cas.get(cas, {"cas": cas, "n_instruments": 0, "n_instruments_as_primary": 0, "n_libraries": 0, "n_records": 0,
                             "instruments": [], "libraries": [], "names_seen": [], "example_records": []})
        spectral_kinds = [k for k in ("gc_ri", "ir", "ms", "uv") if (nist.get("gc_ri_tables") if k == "gc_ri" else nist.get(k))]
        info[cas] = {**{k: r[k] for k in ("cas", "n_instruments", "n_instruments_as_primary", "n_libraries", "n_records", "instruments", "names_seen")},
                     "pubchem": {"cid": props.get("CID"), "title": props.get("Title"), "formula": props.get("MolecularFormula"), "smiles": props.get("SMILES")},
                     "classification": cls, "nist": nist, "nist_spectral_kinds": spectral_kinds}
    selected, rejected = [], []

    def add(cas, rule, why):
        meta = EXPANSION_META.get(cas)
        selected.append({"cas": cas, "id": meta[0] if meta else None, "name_en": meta[1] if meta else None, "name_zh": meta[2] if meta else None,
                         "class": meta[3] if meta else None, "rule": rule, "why": why, **info[cas]})

    # rule 1: molecular substances.json species missing from the table
    rule1 = []
    for sid, row in subs.items():
        if sid in have_ids or sid in NON_MOLECULAR_SUBSTANCES or sid in PHASE_VARIANTS:
            continue
        cas = SUBSTANCE_CAS.get(sid) or row.get("cas")
        if cas and cas not in have_cas:
            rule1.append(cas)
            add(cas, "1_substances_molecular_species", f"substances.json species '{sid}' ({row.get('name_zh')}) used by the reaction templates")
    chosen = set(rule1)
    # rule 2: curated common lab solvents / reagents in >= 2 instruments' libraries
    for cas, why in COMMON_LAB.items():
        i = info.get(cas)
        if not i or cas in chosen or cas in have_cas:
            continue
        if i["n_instruments"] >= 2 and i["classification"] == "molecular" and i["nist_spectral_kinds"]:
            chosen.add(cas)
            add(cas, "2_common_lab_solvent_or_reagent", f"{why}; in {i['n_instruments']} instruments' libraries")
        else:
            rejected.append({"cas": cas, "rule": "2", "reason": f"classification={i['classification']}, instruments={i['n_instruments']}, nist={i['nist_spectral_kinds']}"})
    # rule 3: ranked fill
    pool = [i for c, i in info.items() if c not in chosen and c not in have_cas]
    pool.sort(key=lambda i: (-i["n_instruments"], -len(i["nist_spectral_kinds"]), -i["n_libraries"], -i["n_records"], i["cas"]))
    for i in pool:
        if len(have_cas) + len(chosen) >= target:
            rejected.append({"cas": i["cas"], "rule": "3", "reason": "below the cut (target reached)", "n_instruments": i["n_instruments"],
                             "classification": i["classification"], "nist": i["nist_spectral_kinds"], "names_seen": i["names_seen"][:2]})
            continue
        if i["classification"] != "molecular":
            rejected.append({"cas": i["cas"], "rule": "3", "reason": f"not molecular ({i['classification']})", "names_seen": i["names_seen"][:2]})
            continue
        if not i["nist_spectral_kinds"]:
            rejected.append({"cas": i["cas"], "rule": "3", "reason": "NIST WebBook has no GC/IR/MS/UV data", "names_seen": i["names_seen"][:2]})
            continue
        chosen.add(i["cas"])
        add(i["cas"], "3_ranked_by_instrument_linkage", f"in {i['n_instruments']} instruments' libraries; NIST {i['nist_spectral_kinds']}")
    missing_meta = [s["cas"] for s in selected if not s["id"]]
    out = {
        "built_by": "scripts/spectral_library/select_expansion.py --phase select", "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "target_total": target, "n_existing": len(have_cas), "n_selected": len(selected),
        "rules": __doc__.split("Selection rules")[1].split("Usage:")[0].strip(),
        "ranking_source": str(RANK_FILE.relative_to(ROOT)), "n_libraries_scanned": ranked["n_libraries_scanned"],
        "by_rule": dict(Counter(s["rule"] for s in selected)), "missing_metadata": missing_meta,
        "selected": selected, "not_selected": rejected,
    }
    SELECTION.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"selected {len(selected)} ({out['by_rule']}); missing metadata for {len(missing_meta)} -> {SELECTION.relative_to(ROOT)}")
    return out


def seed_raw_cache(selection: dict) -> None:
    """Copy the probe downloads (same requests as fetch_sources.py) into references/spectral_raw/<id>/."""
    for s in selection["selected"]:
        if not s["id"]:
            continue
        d = RAW / s["id"]
        d.mkdir(parents=True, exist_ok=True)
        for name in ("gc.html", "pubchem_props.json"):
            src = PROBE / s["cas"] / name
            if src.exists() and not (d / name).exists():
                shutil.copy2(src, d / name)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", default="all", choices=["rank", "probe", "select", "seed", "all"])
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--target", type=int, default=200)
    args = ap.parse_args()
    ranked = rank() if args.phase in ("rank", "all") or not RANK_FILE.exists() else json.loads(RANK_FILE.read_text(encoding="utf-8"))
    if args.phase in ("probe", "all"):
        probe(ranked, args.delay)
    if args.phase in ("select", "all"):
        select(ranked, args.target)
    if args.phase == "seed":
        seed_raw_cache(json.loads(SELECTION.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
