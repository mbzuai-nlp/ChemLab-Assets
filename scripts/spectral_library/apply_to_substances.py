"""Add derived ``gc_7890b_fid`` fingerprints from the spectral library to ``assets/reactions/substances.json`` -- ADD ONLY.

Since 2026-10-14 this script never changes an existing value: it only adds a ``gc_7890b_fid`` block to species that do
not have one yet and whose id has a fingerprint in ``spectral_library/gc_retention.json`` (non-polar column by
default).  Species that already carry a fingerprint (derived or assumed), and every other field, are left untouched;
the file is rewritten only when something was added, with the serializer of scripts/substances/apply_storage_hazard.py
(``json.dumps(indent=1)`` + newline, the file's format since 2026-10-22; it was indent=2 without a newline before) so
unchanged content stays byte-identical.  ``--only`` limits the additions to the given ids (2026-10-23: the C8-C20 n-alkane
standard and its hexane solvent).  The Matterix reaction tests read this file.

``--refresh`` (with ``--only``) also replaces a fingerprint that this script wrote earlier (evidence ``derived`` and source
``spectral_library/gc_retention.json ...``), e.g. after a library rule changed (2026-10-23 D-2: the C8-C20 alkane dvapH series);
assumed or hand-made fingerprints are never replaced.

Usage: python apply_to_substances.py [--dry-run] [--column nonpolar|polar] [--only id ...] [--refresh]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUBS = ROOT / "assets" / "reactions" / "substances.json"
LIB = ROOT / "assets" / "reactions" / "spectral_library" / "gc_retention.json"


def fingerprint_block(fp: dict, fid_visible: bool, column: str) -> dict:
    return {
        "k_ref": fp["k_ref"], "delta_h_J_mol": fp["delta_h_J_mol"],
        "response_C_kg": fp["response_C_kg"] if fid_visible else 0.0,
        "reference_temperature_K": fp["reference_temperature_K"],
        "evidence": "derived", "source": f"spectral_library/gc_retention.json ({column}): NIST retention index {fp['retention_index_used']} "
                                          f"({fp['retention_index_class']}) + enthalpy of vaporization + ECN response; column calibration is an assumption",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--column", default="nonpolar", choices=["nonpolar", "polar"])
    ap.add_argument("--only", nargs="*", help="add fingerprints only for these substance ids")
    ap.add_argument("--refresh", action="store_true", help="with --only: replace fingerprints this script derived earlier")
    args = ap.parse_args()
    raw = SUBS.read_text(encoding="utf-8")
    subs = json.loads(raw)
    lib = json.load(open(LIB, encoding="utf-8"))["compounds"]
    if args.refresh and not args.only:
        ap.error("--refresh needs --only")
    added, kept, refreshed = [], [], []
    for sid, rec in subs["substances"].items():
        fp = lib.get(sid, {}).get("model_fingerprint", {}).get(args.column)
        if not fp or (args.only and sid not in args.only):
            continue
        if "gc_7890b_fid" in rec:
            old = rec["gc_7890b_fid"]
            ours = old.get("evidence") == "derived" and str(old.get("source", "")).startswith("spectral_library/gc_retention.json")
            new = fingerprint_block(fp, rec.get("fid_visible", True), args.column)
            if args.refresh and ours and new != old:
                rec["gc_7890b_fid"] = new
                refreshed.append(sid)
            else:
                kept.append(sid)      # never overwrite an assumed / hand-made fingerprint
            continue
        rec["gc_7890b_fid"] = fingerprint_block(fp, rec.get("fid_visible", True), args.column)
        added.append(sid)
    for sid in added + refreshed:
        f = subs["substances"][sid]["gc_7890b_fid"]
        print(f"{'add' if sid in added else 'refresh'} {sid:18} k_ref {f['k_ref']}  dH {f['delta_h_J_mol']}  resp {f['response_C_kg']}")
    print(f"{len(added)} added, {len(refreshed)} refreshed, {len(kept)} species already had a fingerprint (unchanged)")
    if (added or refreshed) and not args.dry_run:
        text = json.dumps(subs, ensure_ascii=False, indent=1) + "\n"
        # safety: the only difference to the previous content must be the added blocks
        before = json.loads(raw)
        after = json.loads(text)
        for sid, rec in before["substances"].items():
            assert {k: v for k, v in after["substances"][sid].items() if not (sid in added + refreshed and k == "gc_7890b_fid")} == \
                {k: v for k, v in rec.items() if not (sid in refreshed and k == "gc_7890b_fid")}, sid
        assert {k: v for k, v in after.items() if k != "substances"} == {k: v for k, v in before.items() if k != "substances"}
        SUBS.write_text(text, encoding="utf-8")
        print(f"updated {len(added) + len(refreshed)} substances in {SUBS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
