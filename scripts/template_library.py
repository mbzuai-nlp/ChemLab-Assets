"""Shared read-only access to output template libraries under assets/reactions/.

Each library is a JSON file ``{"asset_type_id", "schema_version", "templates": [record, ...]}`` whose
records carry ``substance_id``, values with units, ``evidence`` and ``source``. Runtimes read a library
through this module so every template-backed result can report the same provenance block
(library path, record source, evidence and the library's SHA-256).

``shared_ids`` links a record to the shared compound tables (``spectral_library/compounds.json`` by
CAS and ``substances.json`` by id), so a sample whose contents come from a reaction ledger can be
looked up in an instrument library.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REACTIONS = ROOT / "assets" / "reactions"


def _resolve(rel_path: str) -> Path:
    path = (ROOT / rel_path).resolve()
    if ROOT not in path.parents:
        raise ValueError(f"library path escapes the project root: {rel_path}")
    return path


@lru_cache(maxsize=None)
def load_library(rel_path: str) -> dict[str, Any]:
    data = json.loads(_resolve(rel_path).read_text(encoding="utf-8"))
    if not isinstance(data.get("templates"), list):
        raise ValueError(f"{rel_path}: expected a top-level 'templates' list")
    return data


@lru_cache(maxsize=None)
def library_sha256(rel_path: str) -> str:
    return hashlib.sha256(_resolve(rel_path).read_bytes()).hexdigest()


@lru_cache(maxsize=None)
def index_by_id(rel_path: str, key: str = "substance_id") -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in load_library(rel_path)["templates"]:
        rid = record[key]
        if rid in index:
            raise ValueError(f"{rel_path}: duplicate {key} {rid!r}")
        index[rid] = record
    return index


def get_template(rel_path: str, substance_id: str, key: str = "substance_id") -> dict[str, Any] | None:
    return index_by_id(rel_path, key).get(substance_id)


def provenance(rel_path: str, record: dict[str, Any]) -> dict[str, Any]:
    """Provenance block attached to every template-backed result."""
    return {
        "template_source": rel_path,
        "template_record_source": record.get("source"),
        "evidence": record.get("evidence"),
        "library_sha256": library_sha256(rel_path),
    }


@lru_cache(maxsize=None)
def shared_compounds_by_cas() -> dict[str, str]:
    """CAS -> spectral_library compound id."""
    data = json.loads((REACTIONS / "spectral_library" / "compounds.json").read_text(encoding="utf-8"))
    compounds = data["compounds"]
    rows = compounds.values() if isinstance(compounds, dict) else compounds
    return {row["cas"]: row["id"] for row in rows if row.get("cas")}


@lru_cache(maxsize=None)
def shared_substance_ids() -> frozenset[str]:
    data = json.loads((REACTIONS / "substances.json").read_text(encoding="utf-8"))
    return frozenset(data["substances"])


def shared_ids_for(cas: str | None, substance_id: str | None = None) -> dict[str, str]:
    """``shared_ids`` entry for a library record: links into spectral_library and substances.json."""
    out: dict[str, str] = {}
    if cas and cas in shared_compounds_by_cas():
        out["spectral_library"] = shared_compounds_by_cas()[cas]
    if substance_id and substance_id in shared_substance_ids():
        out["substances"] = substance_id
    return out


# --------------------------------------------------------------------------------------------------------------
# Shared-id crosswalk (assets/reactions/shared_id_crosswalk.json, built by scripts/spectral_library/build_crosswalk.py)
# links every template-library record that carries a CAS / InChIKey / PubChem CID (or a shared id as record id) to the
# shared compound table and to substances.json, so a compound formed in a reaction can be looked up in every
# instrument library that holds a real template for it.
CROSSWALK = REACTIONS / "shared_id_crosswalk.json"


@lru_cache(maxsize=None)
def crosswalk() -> dict[str, Any]:
    """The whole crosswalk document (libraries, per-record shared ids, reverse indexes, coverage stats)."""
    return json.loads(CROSSWALK.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _crosswalk_records() -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for rel, lib in crosswalk()["libraries"].items():
        for record in lib["records"]:
            index[(rel, record["record_id"])] = record["shared_ids"]
    return index


def shared_id_for_record(library_rel_path: str, record_id: str) -> dict[str, Any] | None:
    """Shared ids of one library record, e.g. ``{"spectral_library": "acetone", "substances": "acetone", "match_basis": "cas"}``.

    ``library_rel_path`` is the project-relative library path; ``record_id`` the record's ``substance_id`` (or the id
    the crosswalk assigned, see build_crosswalk.py).  Returns None when the record is unknown or carries no link.
    """
    path = Path(library_rel_path)
    rel = (path.resolve().relative_to(ROOT) if path.is_absolute() else path).as_posix()
    links = _crosswalk_records().get((rel, str(record_id)))
    return dict(links) if links else None


def libraries_for_compound(compound_id: str, include_shared_tables: bool = True) -> list[dict[str, Any]]:
    """Which instruments have real templates for this compound: ``[{"asset_type_id", "library", "record_ids"}, ...]``.

    ``compound_id`` may be a spectral_library compound id or a substances.json species id (a species is resolved to
    its compound through the crosswalk, e.g. ``ethanol_vapor`` -> ``ethanol``; records linked only through
    substances.json are included too).  ``include_shared_tables=False`` drops the shared spectral_library files
    themselves (GC retention, IR, UV, NMR, HPLC tables read by the GC/HPLC/FTIR/UV-Vis/NMR assets).
    """
    cw = crosswalk()
    compound = compound_id if compound_id in cw["by_compound"] else cw["substances_to_spectral_library"].get(compound_id, compound_id)
    rows: dict[tuple[str, str], set[str]] = {}
    for source in (cw["by_compound"].get(compound, []), cw["by_substance"].get(compound_id, []),
                   *(cw["by_substance"].get(s, []) for s, c in cw["substances_to_spectral_library"].items() if c == compound and s != compound_id)):
        for row in source:
            rows.setdefault((row["asset_type_id"], row["library"]), set()).update(row["record_ids"])
    shared = {rel for rel, lib in cw["libraries"].items() if lib.get("is_shared_spectral_library")}
    return [{"asset_type_id": aid, "library": rel, "record_ids": sorted(ids)} for (aid, rel), ids in sorted(rows.items())
            if include_shared_tables or rel not in shared]
