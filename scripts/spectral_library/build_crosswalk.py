"""Build ``assets/reactions/shared_id_crosswalk.json``: which instrument template records are which shared compound.

Library discovery (same rule as the expansion selection, ``select_expansion.py``):
  * every ``assets/reactions/*_library/**/*.json``;
  * every JSON file referenced as a library from ``extensions["chemistry_lab.output_templates"]`` of an
    ``assets/**/asset.contract.json`` (keys ``library``, ``library[]``, ``path``, ``paths[]``, ``template_library``,
    ``library_path``, ``template_source``, ``shared_library``, ``gel_library``, ``calibration``,
    ``conversion_reference``, ``companions.*.path``, ``templates[].source.library_record``).
Each library file is attributed to the asset_type_id(s) whose contracts reference it (else its own top-level
``asset_type_id``, else the contracts that reference a sibling file of the same ``*_library`` directory).

Record extraction is schema-agnostic: every dict carrying an identifier key (CAS: ``cas``/``CAS``/
``CAS REGISTRY NO``/``cas_rn``/``casrn``/``cas_number``; InChIKey: ``inchikey``/``InChIKey``/``inchi_key``;
PubChem: ``pubchem_cid``) is assigned to its *record*: the outermost ancestor that is an element of a list or a
value of a dict-of-dicts (else the whole file).  The record id is its ``substance_id`` / ``template_id`` /
``record_id`` / ``compound_id`` / ``species_id`` / ``id`` (else the dict key, else ``<container>[i]``).
Identifiers reached through a nested list are *component* identifiers (mixtures, formulations).

Mapping to the shared ids (``shared_ids``):
  * ``spectral_library`` -- compound id in ``spectral_library/compounds.json``, matched by CAS (exact, checksum
    valid), else InChIKey (computed with RDKit from the compound's PubChem SMILES), else PubChem CID, else by the
    record id being a spectral_library id (``match_basis`` = cas | inchikey | pubchem_cid | id);
  * ``substances`` -- species id in ``substances.json``: the record id itself, or the species that carry the same CAS,
    or the species whose id equals the matched spectral_library id.  Phase variants (``*_vapor``) and species
    without CAS are linked through the spectral_library id (``SUBSTANCE_ALIASES``).

Output: per-library coverage stats, per-record links, and the reverse index compound -> [(asset_type_id, library,
record ids)].  Usage: python -I scripts/spectral_library/build_crosswalk.py [--out path]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
REACTIONS = ROOT / "assets" / "reactions"
SPECTRAL = REACTIONS / "spectral_library"
OUT = REACTIONS / "shared_id_crosswalk.json"

LIBRARY_KEYS = {".library", ".library[]", ".path", ".paths[]", ".template_library", ".library_path", ".template_source",
                ".shared_library", ".gel_library", ".calibration", ".conversion_reference", ".templates[].source.library_record"}
CAS_KEYS = {"cas", "CAS", "CAS REGISTRY NO", "cas_rn", "casrn", "cas_number", "CAS_RN"}
INCHIKEY_KEYS = {"inchikey", "InChIKey", "inchi_key", "INCHIKEY"}
CID_KEYS = {"pubchem_cid"}
ID_KEYS = ("substance_id", "template_id", "record_id", "compound_id", "species_id", "id")
NAME_KEYS = ("name_en", "name", "compound", "substance", "iupac_name", "title")
CAS_RE = re.compile(r"(?<![\d-])(\d{2,7})-(\d{2})-(\d)(?![\d-])")
INCHIKEY_RE = re.compile(r"\b[A-Z]{14}-[A-Z]{10}-[A-Z]\b")

# which entries of the shared spectral_library files count as a real template for the compound
SPECTRAL_HAS_DATA = {
    "compounds.json": lambda v: True,
    "gc_retention.json": lambda v: bool(v.get("retention_index") or v.get("model_fingerprint")),
    "ir_spectra.json": lambda v: bool(v),
    "ms_ei.json": lambda v: bool(v and v.get("peaks_mz_relint")),
    "uv_vis.json": lambda v: bool(v.get("spectra") or v.get("lambda_max_records")),
    "nmr.json": lambda v: bool(v.get("1H", {}).get("spectra") or v.get("13C", {}).get("spectra")),
    "hplc_uv.json": lambda v: v.get("xlogp") is not None,
    "index.json": lambda v: False,   # coverage summary, not a template
}

# substances.json species that are a phase/solution variant of a spectral_library compound (same molecule)
SUBSTANCE_ALIASES = {"water_vapor": "water", "ethanol_vapor": "ethanol", "methanol_vapor": "methanol",
                     "acetone_vapor": "acetone", "ethyl_acetate_vapor": "ethyl_acetate"}


# --------------------------------------------------------------------------------------------------- identifiers
def cas_checksum_ok(cas: str) -> bool:
    m = CAS_RE.fullmatch(cas.strip())
    if not m:
        return False
    digits = (m.group(1) + m.group(2))[::-1]
    return sum((i + 1) * int(d) for i, d in enumerate(digits)) % 10 == int(m.group(3))


def cas_values(value: Any) -> list[str]:
    """Checksum-valid CAS numbers inside a string / list value (one value may hold several, e.g. 'a; b')."""
    out: list[str] = []
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, str):
            for m in CAS_RE.finditer(item):
                cas = m.group(0)
                if cas_checksum_ok(cas) and cas not in out:
                    out.append(cas)
    return out


def inchikey_values(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [m.group(0) for item in items if isinstance(item, str) for m in INCHIKEY_RE.finditer(item)]


def cid_values(value: Any) -> list[int]:
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, int) and item > 0:
            out.append(item)
        elif isinstance(item, str) and item.strip().isdigit():
            out.append(int(item.strip()))
    return out


# ---------------------------------------------------------------------------------------------- library discovery
def _rel(p: Path | str) -> str:
    return str(Path(p).resolve().relative_to(ROOT))


def discover_libraries() -> dict[str, dict[str, Any]]:
    """rel_path -> {"asset_type_ids": [...], "found_via": [...]} for every template library file."""
    libs: dict[str, dict[str, set]] = defaultdict(lambda: {"asset_type_ids": set(), "found_via": set()})
    for p in glob.glob(str(REACTIONS / "*_library" / "**" / "*.json"), recursive=True):
        libs[_rel(p)]["found_via"].add("assets/reactions/*_library glob")
    for c in sorted(glob.glob(str(ROOT / "assets" / "**" / "asset.contract.json"), recursive=True)):
        contract = json.loads(Path(c).read_text(encoding="utf-8"))
        aid = contract.get("asset_type_id")
        ot = (contract.get("extensions") or {}).get("chemistry_lab.output_templates")

        def walk(o: Any, path: str = "") -> None:
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(o, list):
                for v in o:
                    walk(v, f"{path}[]")
            elif isinstance(o, str) and o.endswith(".json") and " " not in o and "://" not in o:
                if path in LIBRARY_KEYS or (path.startswith(".companions.") and path.endswith(".path")):
                    full = (ROOT / o).resolve()
                    if full.is_file() and ROOT in full.parents:
                        rel = _rel(full)
                        libs[rel]["asset_type_ids"].add(aid)
                        libs[rel]["found_via"].add(f"{_rel(c)}:{path}")
        walk(ot)
    # attribution for unreferenced files: own asset_type_id, else sibling files of the same *_library directory
    by_dir: dict[str, set] = defaultdict(set)
    for rel, info in libs.items():
        if info["asset_type_ids"] and rel.startswith("assets/reactions/") and "_library/" in rel:
            by_dir[rel.split("/")[2]].update(info["asset_type_ids"])
    for rel, info in libs.items():
        if info["asset_type_ids"]:
            continue
        try:
            own = json.loads((ROOT / rel).read_text(encoding="utf-8")).get("asset_type_id")
        except (json.JSONDecodeError, AttributeError):
            own = None
        if isinstance(own, str) and own:
            info["asset_type_ids"].add(own); info["found_via"].add("own asset_type_id")
        elif rel.startswith("assets/reactions/") and by_dir.get(rel.split("/")[2]):
            info["asset_type_ids"].update(by_dir[rel.split("/")[2]]); info["found_via"].add("sibling file of the same library directory")
    return {rel: {"asset_type_ids": sorted(v["asset_type_ids"]), "found_via": sorted(v["found_via"])} for rel, v in sorted(libs.items())}


def asset_batches() -> dict[str, str]:
    """asset_type_id -> batch directory (basic40_batch02, expansion_batch03, ...) from the contract locations."""
    out = {}
    for c in glob.glob(str(ROOT / "assets" / "**" / "asset.contract.json"), recursive=True):
        rel = Path(c).resolve().relative_to(ROOT).parts
        try:
            aid = json.loads(Path(c).read_text(encoding="utf-8")).get("asset_type_id")
        except json.JSONDecodeError:
            continue
        out[aid] = rel[1] if len(rel) > 3 else "assets"
    return out


# ----------------------------------------------------------------------------------------------- record extraction
def _is_dict_of_records(d: Any) -> bool:
    return isinstance(d, dict) and len(d) >= 1 and all(isinstance(v, dict) for v in d.values())


def _record_id(node: dict, fallback: str) -> str:
    for k in ID_KEYS:
        v = node.get(k)
        if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v).strip():
            return str(v)
    return fallback


def _record_name(node: dict) -> str | None:
    for k in NAME_KEYS:
        v = node.get(k)
        if isinstance(v, str) and v.strip() and len(v) < 200:
            return v
    return None


def _walk_ids(node: Any, through_list: bool, found: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in CAS_KEYS:
                for cas in cas_values(v):
                    found.append(("cas", cas, through_list))
            elif k in INCHIKEY_KEYS:
                for ik in inchikey_values(v):
                    found.append(("inchikey", ik, through_list))
            elif k in CID_KEYS:
                for cid in cid_values(v):
                    found.append(("pubchem_cid", cid, through_list))
            if isinstance(v, (dict, list)):
                _walk_ids(v, through_list or isinstance(v, list), found)
    elif isinstance(node, list):
        for v in node:
            _walk_ids(v, True, found)


def _iter_record_nodes(data: Any) -> Iterator[tuple[str, str, Any]]:
    """Yield (record_id, json_pointer, node) for every top-level record of a library file."""
    if isinstance(data, list):
        for i, v in enumerate(data):
            if isinstance(v, dict):
                yield _record_id(v, f"[{i}]"), f"/{i}", v
        return
    if not isinstance(data, dict):
        return
    emitted = False
    for key, val in data.items():
        if isinstance(val, list) and any(isinstance(v, dict) for v in val):
            for i, v in enumerate(val):
                if isinstance(v, dict):
                    emitted = True
                    yield _record_id(v, f"{key}[{i}]"), f"/{key}/{i}", v
        elif _is_dict_of_records(val):
            for k, v in val.items():
                emitted = True
                yield _record_id(v, str(k)), f"/{key}/{k}", v
        elif isinstance(val, dict):
            # one more level: e.g. {"library": {"entries": [...]}}
            for key2, val2 in val.items():
                if isinstance(val2, list) and any(isinstance(v, dict) for v in val2):
                    for i, v in enumerate(val2):
                        if isinstance(v, dict):
                            emitted = True
                            yield _record_id(v, f"{key}.{key2}[{i}]"), f"/{key}/{key2}/{i}", v
                elif _is_dict_of_records(val2):
                    for k, v in val2.items():
                        emitted = True
                        yield _record_id(v, str(k)), f"/{key}/{key2}/{k}", v
    if not emitted:
        yield _record_id(data, "<file>"), "", data


def extract_records(data: Any) -> list[dict[str, Any]]:
    """Records of one library file with their identifiers (primary and component)."""
    out = []
    seen: dict[str, int] = defaultdict(int)
    for rid, pointer, node in _iter_record_nodes(data):
        found: list = []
        if isinstance(node, dict):
            # identifiers directly on the record (or in nested dicts) are primary; inside nested lists: components
            _walk_ids(node, False, found)
        prim = {"cas": [], "inchikey": [], "pubchem_cid": []}
        comp = {"cas": [], "inchikey": [], "pubchem_cid": []}
        for kind, val, via_list in found:
            bucket = comp if via_list else prim
            if val not in bucket[kind]:
                bucket[kind].append(val)
        seen[rid] += 1
        embedded = node.get("shared_ids") if isinstance(node, dict) else None
        out.append({"record_id": rid, "pointer": pointer, "name": _record_name(node) if isinstance(node, dict) else None,
                    "primary": prim, "components": comp, "embedded": embedded if isinstance(embedded, dict) else {}})
    # disambiguate duplicate ids inside one file
    dup = {rid for rid, n in seen.items() if n > 1}
    for r in out:
        if r["record_id"] in dup:
            r["record_id"] = f"{r['record_id']}@{r['pointer']}"
    return out


def scan_all() -> dict[str, dict[str, Any]]:
    """rel_path -> {"asset_type_ids", "found_via", "sha256", "records": [...]} for every discovered library."""
    out = {}
    spectral_files = {_rel(p) for p in SPECTRAL.glob("*.json")}
    for rel, info in discover_libraries().items():
        path = ROOT / rel
        blob = path.read_bytes()
        try:
            data = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            out[rel] = {**info, "sha256": hashlib.sha256(blob).hexdigest(), "error": repr(exc), "records": []}
            continue
        if rel in spectral_files and isinstance(data.get("compounds"), dict):
            # the shared tables themselves: one record per compound id that has data in this file
            has_data = SPECTRAL_HAS_DATA.get(Path(rel).name, bool)
            records = [{"record_id": cid, "pointer": f"/compounds/{cid}", "name": None,
                        "primary": {"cas": [], "inchikey": [], "pubchem_cid": []}, "components": {"cas": [], "inchikey": [], "pubchem_cid": []}}
                       for cid, v in data["compounds"].items() if v and has_data(v)]
        else:
            records = extract_records(data)
        out[rel] = {**info, "sha256": hashlib.sha256(blob).hexdigest(), "records": records}
    return out


# ------------------------------------------------------------------------------------------------- shared tables
def load_shared() -> dict[str, Any]:
    comp = json.loads((SPECTRAL / "compounds.json").read_text(encoding="utf-8"))["compounds"]
    subs = json.loads((REACTIONS / "substances.json").read_text(encoding="utf-8"))["substances"]
    inchikeys: dict[str, str] = {}
    try:
        from rdkit import Chem, RDLogger  # type: ignore
        RDLogger.DisableLog("rdApp.*")
        for cid, row in comp.items():
            mol = Chem.MolFromSmiles(row["smiles"]) if row.get("smiles") else None
            if mol is not None:
                inchikeys[Chem.MolToInchiKey(mol)] = cid
    except ImportError:  # crosswalk still works on CAS / CID / id
        pass
    by_cas = {row["cas"]: cid for cid, row in comp.items() if row.get("cas")}
    by_cid = {row["pubchem_cid"]: cid for cid, row in comp.items() if row.get("pubchem_cid")}
    subs_by_cas: dict[str, list[str]] = defaultdict(list)
    for sid, row in subs.items():
        if row.get("cas"):
            subs_by_cas[row["cas"]].append(sid)
    # substances species -> spectral compound (id equality, CAS, alias table)
    subs_to_comp = {}
    for sid, row in subs.items():
        target = sid if sid in comp else by_cas.get(row.get("cas") or "") or SUBSTANCE_ALIASES.get(sid)
        if target in comp:
            subs_to_comp[sid] = target
    comp_to_subs: dict[str, list[str]] = defaultdict(list)
    for sid, cid in subs_to_comp.items():
        comp_to_subs[cid].append(sid)
    return {"compounds": comp, "substances": subs, "by_cas": by_cas, "by_inchikey": inchikeys, "by_cid": by_cid,
            "subs_by_cas": subs_by_cas, "subs_to_comp": subs_to_comp, "comp_to_subs": {k: sorted(v) for k, v in comp_to_subs.items()}}


def link_record(rec: dict[str, Any], shared: dict[str, Any]) -> dict[str, Any]:
    """shared ids for one record (primary identifiers first; component identifiers listed separately)."""
    def match(ids: dict[str, list]) -> list[tuple[str, str, Any]]:
        hits = []
        for cas in ids["cas"]:
            if cas in shared["by_cas"]:
                hits.append((shared["by_cas"][cas], "cas", cas))
        for ik in ids["inchikey"]:
            if ik in shared["by_inchikey"]:
                hits.append((shared["by_inchikey"][ik], "inchikey", ik))
        for cid in ids["pubchem_cid"]:
            if cid in shared["by_cid"]:
                hits.append((shared["by_cid"][cid], "pubchem_cid", cid))
        return hits

    prim = match(rec["primary"])
    rid = rec["record_id"]
    if not prim and not any(rec["primary"].values()) and rid in shared["compounds"]:
        prim = [(rid, "id", rid)]
    comp_hits = [h for h in match(rec["components"]) if h[0] not in {p[0] for p in prim}]
    spectral = []
    for cid, basis, key in prim:
        if cid not in [s["id"] for s in spectral]:
            spectral.append({"id": cid, "match_basis": basis, "key": key})
    # links the library builder wrote into the record itself (template_library.shared_ids_for), e.g. an XRD phase
    # "lime" -> substances "calcium_oxide": used only when no identifier match exists
    embedded = rec.get("embedded") or {}
    if not spectral and embedded.get("spectral_library") in shared["compounds"]:
        spectral.append({"id": embedded["spectral_library"], "match_basis": "embedded", "key": embedded["spectral_library"]})
    subs = []
    if rid in shared["substances"]:
        subs.append(rid)
    if embedded.get("substances") in shared["substances"] and embedded["substances"] not in subs:
        subs.append(embedded["substances"])
    for cas in rec["primary"]["cas"]:
        for sid in shared["subs_by_cas"].get(cas, []):
            if sid not in subs:
                subs.append(sid)
    for s in spectral:
        for sid in shared["comp_to_subs"].get(s["id"], []):
            if sid not in subs and (sid == s["id"] or not rec["primary"]["cas"] or shared["substances"][sid].get("cas") in (None, *rec["primary"]["cas"])):
                subs.append(sid)
    out: dict[str, Any] = {}
    if spectral:
        out["spectral_library"] = spectral[0]["id"]
        if len(spectral) > 1:
            out["spectral_library_all"] = [s["id"] for s in spectral]
        out["match_basis"] = spectral[0]["match_basis"]
        out["match_key"] = spectral[0]["key"]
    if subs:
        out["substances"] = subs[0]
        if len(subs) > 1:
            out["substances_all"] = subs
    if comp_hits:
        out["component_spectral_library"] = sorted({h[0] for h in comp_hits})
    return out


# ------------------------------------------------------------------------------------------------------- crosswalk
def build() -> dict[str, Any]:
    shared = load_shared()
    libs = scan_all()
    spectral_files = {_rel(p) for p in SPECTRAL.glob("*.json")}
    libraries_out: dict[str, Any] = {}
    reverse: dict[str, dict[tuple, list]] = defaultdict(lambda: defaultdict(list))
    reverse_subs: dict[str, dict[tuple, list]] = defaultdict(lambda: defaultdict(list))
    for rel, info in libs.items():
        recs = []
        n_ident = n_linked = n_subs = n_comp = 0
        for rec in info["records"]:
            has_ident = any(rec["primary"].values())
            links = link_record(rec, shared)
            if not has_ident and not links:
                continue
            n_ident += has_ident
            n_linked += "spectral_library" in links
            n_subs += "substances" in links
            n_comp += "component_spectral_library" in links
            entry = {"record_id": rec["record_id"], "pointer": rec["pointer"], "identifiers": {k: v for k, v in rec["primary"].items() if v}}
            if any(rec["components"].values()):
                entry["component_identifiers"] = {k: v for k, v in rec["components"].items() if v}
            entry["shared_ids"] = links
            recs.append(entry)
            for aid in info["asset_type_ids"] or ["(unattributed)"]:
                for cid in links.get("spectral_library_all", [links["spectral_library"]] if "spectral_library" in links else []):
                    reverse[cid][(aid, rel)].append(rec["record_id"])
                for sid in links.get("substances_all", [links["substances"]] if "substances" in links else []):
                    reverse_subs[sid][(aid, rel)].append(rec["record_id"])
        libraries_out[rel] = {
            "asset_type_ids": info["asset_type_ids"], "found_via": info["found_via"], "sha256": info["sha256"],
            "is_shared_spectral_library": rel in spectral_files,
            "coverage": {"records": len(info["records"]), "records_with_identifier": n_ident, "linked_spectral_library": n_linked,
                         "linked_substances": n_subs, "linked_via_component": n_comp,
                         "distinct_compounds": len({r["shared_ids"]["spectral_library"] for r in recs if "spectral_library" in r["shared_ids"]})},
            "records": recs,
        }
        if info.get("error"):
            libraries_out[rel]["error"] = info["error"]

    def rev(table):
        return {cid: [{"asset_type_id": aid, "library": rel, "record_ids": sorted(set(ids))} for (aid, rel), ids in sorted(rows.items())]
                for cid, rows in sorted(table.items())}

    by_compound = rev(reverse)
    instruments_per_compound = {cid: sorted({r["asset_type_id"] for r in rows if r["library"] not in spectral_files}) for cid, rows in by_compound.items()}
    linked_libs = [rel for rel, v in libraries_out.items() if v["coverage"]["linked_spectral_library"] and not v["is_shared_spectral_library"]]
    linked_instr = sorted({aid for rel in linked_libs for aid in libraries_out[rel]["asset_type_ids"]})
    stats = {
        "n_libraries_scanned": len(libraries_out),
        "n_libraries_with_identifiers": sum(1 for v in libraries_out.values() if v["coverage"]["records_with_identifier"]),
        "n_libraries_linked_to_spectral_library": len(linked_libs),
        "n_instruments_linked_to_spectral_library": len(linked_instr),
        "n_records_linked_to_spectral_library": sum(v["coverage"]["linked_spectral_library"] for rel, v in libraries_out.items() if not v["is_shared_spectral_library"]),
        "n_compounds": len(shared["compounds"]),
        "n_compounds_in_any_instrument_library": sum(1 for v in instruments_per_compound.values() if v),
        "n_compounds_in_2plus_instrument_libraries": sum(1 for v in instruments_per_compound.values() if len(v) >= 2),
        "note": "instrument counts exclude the shared spectral_library files themselves",
    }
    # links through a real identifier (CAS / InChIKey / CID), i.e. not only because a record id equals a shared id
    batch_of = asset_batches()
    by_ident: dict[str, set] = defaultdict(set)
    for rel, v in libraries_out.items():
        if v["is_shared_spectral_library"]:
            continue
        for r in v["records"]:
            if r["shared_ids"].get("match_basis") in ("cas", "inchikey", "pubchem_cid"):
                for aid in v["asset_type_ids"]:
                    by_ident[aid].add(r["shared_ids"]["spectral_library"])
    stats["instruments_linked_by_identifier"] = {
        batch: sorted(aid for aid in by_ident if batch_of.get(aid) == batch) for batch in sorted(set(batch_of.values()))}
    stats["n_instruments_linked_by_identifier"] = {batch: len(v) for batch, v in stats["instruments_linked_by_identifier"].items()}
    stats["n_instruments_by_batch"] = {batch: sum(1 for b in batch_of.values() if b == batch) for batch in sorted(set(batch_of.values()))}
    return {
        "schema_version": "1.0.0",
        "description": "Crosswalk from instrument template-library records to the shared compound ids (spectral_library/compounds.json) and substances.json species.",
        "built_by": "scripts/spectral_library/build_crosswalk.py",
        "shared_tables": {"spectral_library": {"path": "assets/reactions/spectral_library/compounds.json",
                                               "sha256": hashlib.sha256((SPECTRAL / "compounds.json").read_bytes()).hexdigest()},
                          "substances": {"path": "assets/reactions/substances.json",
                                         "sha256": hashlib.sha256((REACTIONS / "substances.json").read_bytes()).hexdigest()}},
        "match_rules": {"cas": "exact CAS (checksum-valid) in a cas/CAS/'CAS REGISTRY NO' key of the record",
                        "inchikey": "exact InChIKey; spectral_library InChIKeys computed with RDKit from the PubChem SMILES",
                        "pubchem_cid": "exact PubChem CID", "id": "record id equals a spectral_library id (record carries no identifier)",
                        "substances": "record id is a substances.json id, or the species shares the CAS, or it is the same molecule as the matched compound (id / CAS / phase alias)",
                        "components": "identifiers inside nested lists (mixtures) are listed as component_spectral_library, not as the record's own id"},
        "substance_aliases": SUBSTANCE_ALIASES,
        "substances_to_spectral_library": shared["subs_to_comp"],
        "stats": stats,
        "libraries": libraries_out,
        "by_compound": by_compound,
        "instruments_by_compound": instruments_per_compound,
        "by_substance": rev(reverse_subs),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    cw = build()
    args.out.write_text(json.dumps(cw, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(cw["stats"], indent=1))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
