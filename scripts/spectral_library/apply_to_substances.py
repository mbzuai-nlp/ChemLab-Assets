"""Add derived ``gc_7890b_fid`` fingerprints from the spectral library to ``assets/reactions/substances.json`` -- ADD ONLY.

Since 2026-10-14 this script never changes an existing value: it only adds a ``gc_7890b_fid`` block to species that do
not have one yet and whose id has a fingerprint in ``spectral_library/gc_retention.json`` (non-polar column by
default).  Species that already carry a fingerprint (derived or assumed), and every other field, are left untouched;
the file is rewritten only when something was added, with the same serializer (``json.dumps(indent=2)``, no trailing
newline) so unchanged content stays byte-identical.  The Matterix reaction tests read this file.

Usage: python apply_to_substances.py [--dry-run] [--column nonpolar|polar]
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
    args = ap.parse_args()
    raw = SUBS.read_text(encoding="utf-8")
    subs = json.loads(raw)
    lib = json.load(open(LIB, encoding="utf-8"))["compounds"]
    added, kept = [], []
    for sid, rec in subs["substances"].items():
        fp = lib.get(sid, {}).get("model_fingerprint", {}).get(args.column)
        if not fp:
            continue
        if "gc_7890b_fid" in rec:
            kept.append(sid)          # never overwrite an existing fingerprint (derived or assumption)
            continue
        rec["gc_7890b_fid"] = fingerprint_block(fp, rec.get("fid_visible", True), args.column)
        added.append(sid)
    for sid in added:
        f = subs["substances"][sid]["gc_7890b_fid"]
        print(f"add {sid:18} k_ref {f['k_ref']}  dH {f['delta_h_J_mol']}  resp {f['response_C_kg']}")
    print(f"{len(added)} added, {len(kept)} species already had a fingerprint (unchanged)")
    if added and not args.dry_run:
        text = json.dumps(subs, ensure_ascii=False, indent=2)
        # safety: the only difference to the previous content must be the added blocks
        before = json.loads(raw)
        after = json.loads(text)
        for sid, rec in before["substances"].items():
            assert {k: v for k, v in after["substances"][sid].items() if not (sid in added and k == "gc_7890b_fid")} == rec, sid
        assert {k: v for k, v in after.items() if k != "substances"} == {k: v for k, v in before.items() if k != "substances"}
        SUBS.write_text(text, encoding="utf-8")
        print(f"updated {len(added)} substances in {SUBS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
