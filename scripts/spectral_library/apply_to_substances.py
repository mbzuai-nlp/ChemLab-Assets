"""Replace the assumed ``gc_7890b_fid`` fingerprints in ``assets/reactions/substances.json`` with the derived ones
from the spectral library (non-polar column), keeping the record's evidence/source fields honest.
Substances without a library fingerprint are left unchanged.  Usage: python apply_to_substances.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUBS = ROOT / "assets" / "reactions" / "substances.json"
LIB = ROOT / "assets" / "reactions" / "spectral_library" / "gc_retention.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--column", default="nonpolar", choices=["nonpolar", "polar"])
    args = ap.parse_args()
    subs = json.load(open(SUBS, encoding="utf-8"))
    lib = json.load(open(LIB, encoding="utf-8"))["compounds"]
    changed = []
    for sid, rec in subs["substances"].items():
        fp = lib.get(sid, {}).get("model_fingerprint", {}).get(args.column)
        if not fp:
            continue
        old = rec.get("gc_7890b_fid", {})
        rec["gc_7890b_fid"] = {
            "k_ref": fp["k_ref"], "delta_h_J_mol": fp["delta_h_J_mol"],
            "response_C_kg": fp["response_C_kg"] if rec.get("fid_visible", True) else 0.0,
            "reference_temperature_K": fp["reference_temperature_K"],
            "evidence": "derived", "source": f"spectral_library/gc_retention.json ({args.column}): NIST retention index {fp['retention_index_used']} "
                                              f"({fp['retention_index_class']}) + enthalpy of vaporization + ECN response; column calibration is an assumption",
        }
        changed.append((sid, old, rec["gc_7890b_fid"]))
    subs["description_zh"] = ("反应模板与仪器指纹表共用的物质名单。molar_mass_kg_mol 为教科书值；density_kg_m3 为室温近似值；gc_7890b_fid 指纹与 "
                              "scripts/gc_runtime.py 的 Species 字段兼容：evidence=derived 的条目由 spectral_library（NIST 保留指数、汽化焓、有效碳数）换算，"
                              "柱标定为假设；evidence=assumption 的条目仍是仿真假设。")
    for sid, old, new in changed:
        print(f"{sid:16} k_ref {old.get('k_ref')} -> {new['k_ref']}   dH {old.get('delta_h_J_mol')} -> {new['delta_h_J_mol']}   resp {old.get('response_C_kg')} -> {new['response_C_kg']}")
    if not args.dry_run:
        SUBS.write_text(json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"updated {len(changed)} substances in {SUBS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
