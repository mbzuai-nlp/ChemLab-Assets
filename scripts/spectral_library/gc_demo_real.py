"""Run the Chemistry-Sim GC runtime with real-compound fingerprints from the spectral library.

Three demonstrations are written to ``outputs/spectral_library/gc_real_demo/<name>/``:
  esterification   ethanol + acetic acid + ethyl acetate (+ water, FID-blind) at 80 C isothermal
  solvent_mix      six common solvents at 60 C isothermal
  expansion_mix    six compounds added in the 2026-10-14 expansion at 80 C isothermal
Outputs go to outputs/spectral_library/gc_real_demo/<column>/<demo>/; the run length is stretched automatically so the
most retained species elutes (capped at 3600 s).
Each produces chromatogram.csv, run.json (method, species with provenance, mass ledger, peak table) and
chromatogram.png; the script also asserts that the elution order follows the NIST retention indices.

Usage (matterix python):  python gc_demo_real.py [--column nonpolar|polar]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from gc_runtime import GC, Method, Species  # noqa: E402

LIB = ROOT / "assets" / "reactions" / "spectral_library"
OUT = ROOT / "outputs" / "spectral_library" / "gc_real_demo"

DEMOS = {
    "esterification": {"oven_C": 80.0, "species": ["ethanol", "acetic_acid", "ethyl_acetate", "water"],
                        "vial_kg": {"ethanol": 4e-7, "acetic_acid": 4e-7, "ethyl_acetate": 2e-7, "water": 1e-7}, "run_s": 600},
    "solvent_mix": {"oven_C": 60.0, "species": ["acetone", "dichloromethane", "ethyl_acetate", "hexane", "benzene", "toluene"],
                     "vial_kg": None, "run_s": 900},
    # 2026-10-14 expansion compounds (spectral_library 95 -> 200): amine, glycol ether, alkane, chloroalkene, aromatics
    "expansion_mix": {"oven_C": 80.0, "species": ["diethylamine", "dimethoxyethane_1_2", "methylcyclohexane", "tetrachloroethylene", "cumene", "mesitylene"],
                      "vial_kg": None, "run_s": 900},
}


def load_species(ids: list[str], column: str) -> tuple[list[Species], dict]:
    gc = json.load(open(LIB / "gc_retention.json", encoding="utf-8"))["compounds"]
    comp = json.load(open(LIB / "compounds.json", encoding="utf-8"))["compounds"]
    species, provenance = [], {}
    for sid in ids:
        fp = gc.get(sid, {}).get("model_fingerprint", {}).get(column)
        if fp is None:
            if sid == "water":  # no FID response and no retention index: treated as an unretained, invisible solvent
                fp = {"k_ref": 0.05, "delta_h_J_mol": -40650.0, "response_C_kg": 0.0, "reference_temperature_K": 353.15,
                      "evidence": "assumption (water: FID-blind, nearly unretained on a non-polar column)"}
            else:
                raise SystemExit(f"{sid}: no {column} fingerprint in the library")
        species.append(Species(sid, fp["k_ref"], fp["delta_h_J_mol"], fp["response_C_kg"], fp["reference_temperature_K"]))
        c = comp.get(sid, {"name_zh": sid, "cas": None})
        provenance[sid] = {"name_zh": c["name_zh"], "cas": c["cas"], **fp}
    return species, provenance


def run(name: str, spec: dict, column: str) -> dict:
    species, prov = load_species(spec["species"], column)
    method = Method(oven_temperature_K=273.15 + spec["oven_C"])
    gc = GC(method=method, species=species, vial_masses_kg=spec["vial_kg"])
    # run long enough for the most retained species at this oven temperature (capped at one hour)
    core = method.column_inner_diameter_m - 2 * method.column_film_thickness_m
    t0 = method.column_length_m / (method.column_flow_m3_s / (3.141592653589793 * core**2 / 4))
    k_max = max(sp.retention_factor(method.oven_temperature_K) for sp in species)
    run_s = min(3600.0, max(spec["run_s"], 1.4 * t0 * (1 + k_max) + 60.0))
    gc.perform("plug_power", connected=True)
    gc.perform("power", enabled=True)
    for gas in gc.gases:
        gc.perform("connect_gas", gas=gas, connected=True)
    gc.step(1200)
    gc.perform("ignite")
    gc.perform("start")
    gc.perform("aspirate", fraction=0.01)
    gc.perform("insert_syringe")
    gc.perform("depress_plunger")
    gc.perform("retract_syringe")
    rows, eluted_prev = [], {s: 0.0 for s in spec["species"]}
    per_species = {s: [] for s in spec["species"]}
    for _ in range(int(run_s / 0.5)):
        state = gc.step(0.5)
        rows.append({"run_time_s": state["run_time_s"], "fid_current_A": state["fid_current_A"], "fid_integral_C": state["fid_integral_C"],
                     "oven_temperature_K": state["temperatures_K"]["oven"]})
        for s in spec["species"]:
            e = state["mass_balance_kg"][s]["eluted_kg"]
            per_species[s].append(e - eluted_prev[s]); eluted_prev[s] = e
    # peak table: apex of each species' elution-rate profile
    peaks = []
    injected = {s: state["mass_balance_kg"][s]["column_loaded_kg"] for s in spec["species"]}
    for s in spec["species"]:
        prof = per_species[s]
        i = max(range(len(prof)), key=lambda j: prof[j])
        eluted = eluted_prev[s] >= 0.5 * injected[s] if injected[s] > 0 else False
        peaks.append({"species": s, "name_zh": prov[s]["name_zh"], "apex_run_time_s": rows[i]["run_time_s"] if eluted else None, "eluted": eluted,
                      "eluted_kg": eluted_prev[s], "column_loaded_kg": injected[s],
                      "charge_C": eluted_prev[s] * prov[s]["response_C_kg"], "retention_index_used": prov[s].get("retention_index_used")})
    peaks.sort(key=lambda p: (p["apex_run_time_s"] is None, p["apex_run_time_s"] or 0.0))
    d = OUT / column / name
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "chromatogram.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) + [f"elution_rate_kg_per_step__{s}" for s in spec["species"]])
        w.writeheader()
        for i, r in enumerate(rows):
            w.writerow({**r, **{f"elution_rate_kg_per_step__{s}": per_species[s][i] for s in spec["species"]}})
    meta = {"column_fingerprint": column, "run_s": run_s, "method": asdict(gc.method), "species": prov, "peak_table": peaks,
            "final_state": gc.observe(), "events": gc.events, "library": str(LIB.relative_to(ROOT))}
    (d / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        fp = next((p for p in font_manager.findSystemFonts() if "DroidSansFallback" in p or "NotoSansCJK" in p or "wqy" in p.lower()), None)
        if fp:  # Latin glyphs from DejaVu, CJK glyphs from the fallback face (matplotlib >= 3.6 font fallback)
            font_manager.fontManager.addfont(fp)
            plt.rcParams["font.family"] = ["DejaVu Sans", font_manager.FontProperties(fname=fp).get_name()]
        cjk = None
        t = [r["run_time_s"] for r in rows]
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(t, [r["fid_current_A"] * 1e9 for r in rows], color="k", lw=1)
        last = 0.0
        for i, p in enumerate(p for p in peaks if p["charge_C"] > 0 and p["eluted"]):
            apex = max(r["fid_current_A"] for r in rows if abs(r["run_time_s"] - p["apex_run_time_s"]) < 1) * 1e9
            ax.annotate(f'{p["name_zh"]} {p["species"]}  RI {p["retention_index_used"]:.0f}', (p["apex_run_time_s"], apex),
                        textcoords="offset points", xytext=(6, 4 + 13 * (i % 3)), fontsize=8, fontproperties=cjk)
            last = max(last, p["apex_run_time_s"])
        ax.set_xlim(0, min(t[-1], last * 1.25 + 60))
        ax.set_xlabel("run time (s)"); ax.set_ylabel("FID current (nA)")
        ax.set_title(f"{name}: {spec['oven_C']:.0f} C isothermal, {column} column, fingerprints from NIST retention indices", fontsize=9, pad=28)
        ax.set_ylim(top=ax.get_ylim()[1] * 1.18)
        fig.tight_layout(); fig.savefig(d / "chromatogram.png", dpi=130); plt.close(fig)
    except Exception as exc:  # plotting is optional
        print("plot skipped:", exc)
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--column", default="nonpolar", choices=["nonpolar", "polar"])
    ap.add_argument("--demo", default="", help="run only this demo (default: all)")
    args = ap.parse_args()
    ok = True
    for name, spec in DEMOS.items():
        if args.demo and name != args.demo:
            continue
        meta = run(name, spec, args.column)
        visible = [p for p in meta["peak_table"] if p["charge_C"] > 0 and p["eluted"]]
        order_time = [p["species"] for p in visible]
        order_ri = [p["species"] for p in sorted(visible, key=lambda p: p["retention_index_used"])]
        same = order_time == order_ri
        ok &= same
        print(f"[{name}] elution order {'matches' if same else 'DIFFERS FROM'} retention-index order")
        for p in meta["peak_table"]:
            apex = f"{p['apex_run_time_s']:7.1f} s" if p["eluted"] else "  (not eluted)"
            print(f"   {apex}  {p['species']:16} RI={p['retention_index_used']}  eluted={p['eluted_kg']:.3e} kg  charge={p['charge_C']:.3e} C")
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
