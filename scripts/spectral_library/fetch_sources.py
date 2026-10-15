"""Download the raw source records for every compound in ``compounds_list.py`` into ``references/spectral_raw/<id>/``.

Sources (all public web services, fetched politely with a delay and cached; re-runs skip files already on disk):

* NIST Chemistry WebBook (https://webbook.nist.gov), keyed by CAS:
    gc.html      Mask=2000  Kovats / Van den Dool retention-index tables (nonpolar + polar columns)
    thermo.html  Mask=4     phase-change data (boiling point, enthalpy of vaporization)
    ir.html      Mask=80    list of available IR spectra; each is then saved as ir_<index>.jdx (JCAMP-DX)
    uv.html      Mask=400   list of UV/Vis spectra; each saved as uv_<index>.jdx
    ms_0.jdx     Type=Mass  NIST/EPA/NIH electron-ionisation mass spectrum (peak list)
* PubChem (https://pubchem.ncbi.nlm.nih.gov), keyed by CAS via the name resolver:
    pubchem_props.json   CID, formula, MW, SMILES, XLogP, IUPAC name
    pubchem_nmr_1h.json  PUG-View section "1H NMR Spectra" (HMDB peak lists re-hosted by PubChem)
    pubchem_nmr_13c.json PUG-View section "13C NMR Spectra"
    pubchem_uv.json      PUG-View section "UV Spectra" (lambda-max text records)

Usage:  python fetch_sources.py [--ids a,b,c] [--delay 0.6] [--force] [--skip-unlisted]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compounds_list import COMPOUNDS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "references" / "spectral_raw"
NIST = "https://webbook.nist.gov/cgi/cbook.cgi"
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest"
HEADERS = {"User-Agent": "Chemistry-Sim spectral-library fetcher (academic lab simulator; contact: project maintainer)"}
MAX_SPECTRA_PER_TYPE = 4


def nist_id(cas: str) -> str:
    return "C" + cas.replace("-", "")


def get(session: requests.Session, url: str, params: dict | None, dest: Path, delay: float, force: bool, log) -> bytes | None:
    if dest.exists() and not force:
        return dest.read_bytes()
    for attempt in range(3):
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=60)
            time.sleep(delay)
            if r.status_code == 200:
                dest.write_bytes(r.content)
                return r.content
            if r.status_code in (404,):
                dest.write_bytes(b"")
                return b""
            log(f"    HTTP {r.status_code} for {r.url}; retry {attempt + 1}")
        except requests.RequestException as exc:
            log(f"    {exc!r}; retry {attempt + 1}")
        time.sleep(3.0 * (attempt + 1))
    return None


def nist_listed_masks(page: bytes) -> set[str]:
    """Masks of the data sections a NIST WebBook compound page links to ('Other data available'), e.g. {'4', '80', '200'}."""
    if b"Registry Number Not Found" in page or b'<h1 id="Top">' not in page:
        return set()
    return {m.decode() for m in re.findall(rb"Mask=([0-9A-F]+)#", page)}


def jcamp_ok(blob: bytes | None) -> bool:
    return bool(blob) and b"Spectrum not found" not in blob[:200] and b"##XYDATA" in blob or (bool(blob) and b"##PEAK TABLE" in blob)


def fetch_compound(session, row, delay, force, log, skip_unlisted=False):
    sid, name_en, name_zh, cas, cls = row
    d = RAW / sid
    d.mkdir(parents=True, exist_ok=True)
    cid = nist_id(cas)
    # --- NIST pages
    gc_page = get(session, NIST, {"ID": cid, "Mask": "2000", "Units": "SI"}, d / "gc.html", delay, force, log) or b""
    listed = nist_listed_masks(gc_page) if skip_unlisted and gc_page else None

    def wanted(mask: str, dest: Path) -> bool:
        """With --skip-unlisted, do not request data NIST's own 'Other data available' list says it does not hold."""
        if listed is None or mask in listed or dest.exists():
            return True
        log(f"    skip {dest.name}: Mask={mask} not listed on the NIST page")
        return False

    if wanted("4", d / "thermo.html"):
        get(session, NIST, {"ID": cid, "Mask": "4", "Units": "SI"}, d / "thermo.html", delay, force, log)
    ir_page = (get(session, NIST, {"ID": cid, "Mask": "80", "Units": "SI"}, d / "ir.html", delay, force, log) or b"") if wanted("80", d / "ir.html") else b""
    uv_page = (get(session, NIST, {"ID": cid, "Mask": "400", "Units": "SI"}, d / "uv.html", delay, force, log) or b"") if wanted("400", d / "uv.html") else b""
    for kind, page, typ in (("ir", ir_page, "IR"), ("uv", uv_page, "UVVis")):
        indices = sorted({int(m) for m in re.findall(rb"Index=(\d+)", page)})[:MAX_SPECTRA_PER_TYPE]
        for idx in indices:
            get(session, NIST, {"JCAMP": cid, "Index": str(idx), "Type": typ}, d / f"{kind}_{idx}.jdx", delay, force, log)
    if wanted("200", d / "ms_0.jdx"):
        get(session, NIST, {"JCAMP": cid, "Index": "0", "Type": "Mass"}, d / "ms_0.jdx", delay, force, log)
    # --- PubChem
    props = "MolecularFormula,MolecularWeight,SMILES,ConnectivitySMILES,XLogP,IUPACName,Title"
    blob = get(session, f"{PUBCHEM}/pug/compound/name/{cas}/property/{props}/JSON", None, d / "pubchem_props.json", delay, force, log)
    pc_cid = None
    try:
        pc_cid = json.loads(blob)["PropertyTable"]["Properties"][0]["CID"]
    except Exception:
        # fall back to the English name
        blob = get(session, f"{PUBCHEM}/pug/compound/name/{name_en}/property/{props}/JSON", None, d / "pubchem_props.json", delay, True, log)
        try:
            pc_cid = json.loads(blob)["PropertyTable"]["Properties"][0]["CID"]
        except Exception:
            log(f"    PubChem: no CID for {sid} ({cas} / {name_en})")
    if pc_cid:
        for heading, fname in (("1H NMR Spectra", "pubchem_nmr_1h.json"), ("13C NMR Spectra", "pubchem_nmr_13c.json"), ("UV Spectra", "pubchem_uv.json")):
            get(session, f"{PUBCHEM}/pug_view/data/compound/{pc_cid}/JSON", {"heading": heading}, d / fname, delay, force, log)
    have = sorted(p.name for p in d.iterdir() if p.stat().st_size > 0)
    log(f"  {sid}: {len(have)} files ({', '.join(n for n in have if n.endswith('.jdx'))})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", default="", help="comma-separated subset of substance ids")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds to wait after every request")
    ap.add_argument("--force", action="store_true", help="re-download files already cached")
    ap.add_argument("--skip-unlisted", action="store_true",
                    help="skip thermo/IR/UV/MS requests for data types the compound's NIST page (gc.html) does not list (fewer requests; same result)")
    args = ap.parse_args()
    rows = COMPOUNDS if not args.ids else [r for r in COMPOUNDS if r[0] in set(args.ids.split(","))]
    RAW.mkdir(parents=True, exist_ok=True)
    log_path = RAW / "fetch_log.txt"
    with open(log_path, "a", encoding="utf-8") as lf:
        def log(msg):
            print(msg, flush=True)
            lf.write(msg + "\n"); lf.flush()
        log(f"=== fetch {time.strftime('%Y-%m-%d %H:%M:%S')}: {len(rows)} compounds")
        with requests.Session() as session:
            for i, row in enumerate(rows, 1):
                log(f"[{i}/{len(rows)}] {row[0]} ({row[3]})")
                try:
                    fetch_compound(session, row, args.delay, args.force, log, skip_unlisted=args.skip_unlisted)
                except Exception as exc:  # keep going; the builder reports what is missing
                    log(f"  FAILED {row[0]}: {exc!r}")
        log("=== done")


if __name__ == "__main__":
    main()
