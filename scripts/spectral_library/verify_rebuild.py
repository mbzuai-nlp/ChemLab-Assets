"""Prove that a rebuilt spectral_library only changed what it was supposed to change.

Compares a backup copy of ``assets/reactions/spectral_library`` (``--before``) with the rebuilt one (``--after``):

* every compound entry that existed before is value-identical in every JSON file, except
  - IR records listed in the FT/IR-4700 correction overlay (``--overlay``; the 24 mis-decoded spectra), which must
    now equal the overlay's corrected values (grid, peaks, x range, header fields, y transform), and
  - IR records the fixed decoder excludes (real-refractive-index-only optical-constant files), which must be listed
    in ``excluded_spectra`` of the new ir_spectra.json, together with the matching ``index.json → ir_spectra`` count;
* unaffected IR records keep their order inside each compound;
* new compound ids are reported (they may only be added, never replace an existing id).

Writes a JSON report (``--report``) and exits 1 on any unexpected difference.

Usage: python -I verify_rebuild.py --before <backup dir> [--after assets/reactions/spectral_library]
                                   [--overlay <jasco_ftir4700_ir_library/templates.json>] [--report out.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP = Path("../backups/spectral_library_2026-10-14")
OVERLAY_FIELDS = ("absorbance_normalized", "peaks_cm-1", "x_range_cm-1", "title", "state", "sampling", "resolution", "y_units_original", "y_transform")
FILES = ("compounds.json", "gc_retention.json", "ir_spectra.json", "ms_ei.json", "uv_vis.json", "nmr.json", "hplc_uv.json", "index.json")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", type=Path, default=BACKUP / "spectral_library")
    ap.add_argument("--after", type=Path, default=ROOT / "assets" / "reactions" / "spectral_library")
    ap.add_argument("--overlay", type=Path, default=BACKUP / "aux" / "reactions" / "jasco_ftir4700_ir_library" / "templates.json")
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()

    overlay = {(t["compound_id"], t["nist_index"]): t for t in load(args.overlay)["templates"]}
    ir_after_doc = load(args.after / "ir_spectra.json")
    excluded = {(sid, e["nist_index"]) for sid, rows in ir_after_doc.get("excluded_spectra", {}).items() for e in rows}
    report = {"before": str(args.before), "after": str(args.after), "overlay": str(args.overlay), "files": {}, "errors": []}
    err = report["errors"].append

    for name in FILES:
        b = load(args.before / name)
        a = load(args.after / name)
        bc, ac = b["compounds"], a["compounds"]
        missing = sorted(set(bc) - set(ac))
        added = sorted(set(ac) - set(bc))
        if missing:
            err(f"{name}: compounds removed: {missing}")
        changed_meta = sorted(k for k in set(a) | set(b) if k != "compounds" and a.get(k) != b.get(k))
        identical, differing, expected = 0, [], []
        for sid in sorted(set(bc) & set(ac)):
            if name == "ir_spectra.json":
                before_recs = {r["nist_index"]: r for r in bc[sid]}
                after_recs = {r["nist_index"]: r for r in ac[sid]}
                order_b = [r["nist_index"] for r in bc[sid] if (sid, r["nist_index"]) not in overlay and (sid, r["nist_index"]) not in excluded]
                order_a = [r["nist_index"] for r in ac[sid] if (sid, r["nist_index"]) not in overlay]
                if order_b != order_a:
                    err(f"{name}:{sid}: order/set of unaffected records changed {order_b} -> {order_a}")
                for idx, rb in before_recs.items():
                    key = (sid, idx)
                    if key in excluded:
                        if idx in after_recs:
                            err(f"{name}:{sid}#{idx}: listed as excluded but still stored")
                        expected.append({"record": f"{sid}__ir_{idx}", "change": "excluded (real-index-only optical-constant file)"})
                        continue
                    ra = after_recs.get(idx)
                    if ra is None:
                        err(f"{name}:{sid}#{idx}: record disappeared"); continue
                    if key in overlay:
                        ov = overlay[key]
                        bad = [f for f in OVERLAY_FIELDS if ra.get(f) != ov.get(f)]
                        if bad:
                            err(f"{name}:{sid}#{idx}: does not match the overlay in {bad}")
                        n_changed = sum(1 for p, q in zip(ra["absorbance_normalized"], rb["absorbance_normalized"]) if p != q)
                        expected.append({"record": f"{sid}__ir_{idx}", "change": "corrected; equals overlay", "grid_points_changed_vs_before": n_changed,
                                         "overlay_reason": ov["overrides"]["reason"]})
                        continue
                    if ra == rb:
                        identical += 1
                    else:
                        differing.append(f"{sid}#{idx}: " + ",".join(sorted(k for k in set(ra) | set(rb) if ra.get(k) != rb.get(k))))
                for idx in after_recs:
                    if idx not in before_recs:
                        err(f"{name}:{sid}#{idx}: new IR record for an existing compound")
                continue
            eb, ea = bc[sid], ac[sid]
            if eb == ea:
                identical += 1
                continue
            keys = sorted(k for k in set(ea or {}) | set(eb or {}) if (ea or {}).get(k) != (eb or {}).get(k)) if isinstance(ea, dict) and isinstance(eb, dict) else ["<value>"]
            n_excl = sum(1 for (s, _i) in excluded if s == sid)
            if name == "index.json" and keys == ["ir_spectra"] and eb["ir_spectra"] - ea["ir_spectra"] == n_excl:
                expected.append({"record": sid, "change": f"ir_spectra count {eb['ir_spectra']} -> {ea['ir_spectra']} (excluded real-index-only files)"})
                continue
            differing.append(f"{sid}: {keys}")
        for d in differing:
            err(f"{name}: unexpected difference {d}")
        report["files"][name] = {"compared_entries": len(set(bc) & set(ac)) if name != "ir_spectra.json" else sum(len(v) for k, v in bc.items() if k in ac),
                                 "identical_entries": identical, "expected_changes": expected, "unexpected_differences": differing,
                                 "added_compounds": len(added), "added_ids": added, "top_level_keys_changed": changed_meta}
    overlay_keys_checked = sum(1 for e in report["files"]["ir_spectra.json"]["expected_changes"] if e["change"].startswith("corrected"))
    if overlay_keys_checked != len(overlay):
        err(f"overlay records checked {overlay_keys_checked} != {len(overlay)}")
    report["summary"] = {"overlay_records_matched": overlay_keys_checked, "excluded_records": len(excluded),
                         "unaffected_ir_records_identical": report["files"]["ir_spectra.json"]["identical_entries"],
                         "pass": not report["errors"]}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "errors": report["errors"][:20],
                      "per_file": {k: {kk: v[kk] for kk in ("compared_entries", "identical_entries", "added_compounds", "top_level_keys_changed")} | {"expected_changes": len(v["expected_changes"])}
                                   for k, v in report["files"].items()}}, ensure_ascii=False, indent=1))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
