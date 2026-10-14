"""Fetch ChemLab-Assets models and data from Hugging Face into this repository.

    python tools/download_assets.py                      # all instruments (about 1.5 GB)
    python tools/download_assets.py esco_efa5uduvw mt_xpr204e
                                                         # selected instruments, by slug or CHEM ID
    python tools/download_assets.py --data-only          # contracts and data, no meshes or Blender files

Requires: pip install -U huggingface_hub
"""
import argparse
import csv
from pathlib import Path

from huggingface_hub import snapshot_download

REPO = "MBZUAI/ChemLab-Assets"
ROOT = Path(__file__).resolve().parents[1]
SHARED = ["assets/reactions/*", "assets/physics_annotations.json", "assets/initial_state.json", "release_manifest.json"]
HEAVY = ["*.blend", "*.glb", "*.obj", "*.mtl", "*/meshes/*", "*/textures/*", "*/variants/*", "*/particle_images/*"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("instruments", nargs="*", help="slugs (e.g. esco_efa5uduvw) or CHEM IDs (e.g. CHEM-199)")
    ap.add_argument("--data-only", action="store_true", help="skip meshes, GLB and Blender files")
    ap.add_argument("--previews", action="store_true", help="also fetch the preview renders")
    args = ap.parse_args()
    rows = list(csv.DictReader(open(ROOT / "catalog" / "catalog.csv", encoding="utf-8")))
    if args.instruments:
        want = set(args.instruments)
        sel = [r for r in rows if r["id"].split(".", 1)[1] in want or r["chem_id"] in want]
        missing = want - {r["id"].split(".", 1)[1] for r in sel} - {r["chem_id"] for r in sel}
        if missing:
            raise SystemExit("not in the catalog: " + ", ".join(sorted(missing)))
        allow = [r["path"] + "/*" for r in sel] + SHARED
        if args.previews:
            allow += ["previews/" + r["id"].split(".", 1)[1] + "/*" for r in sel]
    else:
        allow = ["assets/*", "release_manifest.json"] + (["previews/*"] if args.previews else [])
    path = snapshot_download(REPO, repo_type="dataset", local_dir=ROOT, allow_patterns=allow,
                             ignore_patterns=HEAVY if args.data_only else None)
    print("assets are in", Path(path) / "assets")


if __name__ == "__main__":
    main()
