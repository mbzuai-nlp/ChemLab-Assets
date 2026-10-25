"""Fetch the public PubChem records behind the storage / hazard / liquid-property fields of assets/reactions/substances.json.

For every substance with a CAS number (or an explicit CID override) this caches, under
references/substance_safety_raw/<substance_id>/:

    cid.json        PUG REST  compound/name/<CAS>/cids           (CAS -> PubChem CID)
    ghs.json        PUG-View  heading "GHS Classification"       (harmonised EU CLP entry, ECHA C&L notification summary, ...)
    bp.json         PUG-View  heading "Boiling Point"            (HSDB entries cite the CRC Handbook page)
    viscosity.json  PUG-View  heading "Viscosity"
    storage.json    PUG-View  heading "Storage Conditions"       (HSDB / supplier text, used only for the 'cold' class)

Boiling point / viscosity are fetched for every substance, not only liquids, so a later phase correction needs no re-fetch.
Requests go out one at a time (~3 per second, under PubChem's 5/s limit) with a generic User-Agent and nothing else: no
e-mail, no personal data, no API key.  Already cached files are not fetched again (delete a file to refresh it).

Usage:  python scripts/substances/fetch_pubchem_safety.py [--only id ...]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUBSTANCES = ROOT / "assets" / "reactions" / "substances.json"
RAW = ROOT / "references" / "substance_safety_raw"
UA = "chemsim-substance-data/1.0"
PUG = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
VIEW = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound"
HEADINGS = {"ghs.json": "GHS Classification", "bp.json": "Boiling Point", "viscosity.json": "Viscosity",
            "storage.json": "Storage Conditions"}
NEW_HEADINGS = {"density.json": "Density", "physdesc.json": "Physical Description", "mp.json": "Melting Point"}

# CAS numbers missing from substances.json but needed to look the substance up (source: PubChem compound pages).
CAS_OVERRIDES = {"sodium_acetate": "127-09-3"}


def get(url: str, retries: int = 4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"_not_found": True, "url": url}
            if e.code in (429, 503, 500):
                time.sleep(2.0 * (attempt + 1))
                continue
            return {"_error": f"HTTP {e.code}", "url": url}
        except Exception as e:  # network hiccup
            time.sleep(2.0 * (attempt + 1))
            last = str(e)
    return {"_error": f"failed after {retries} tries: {last if 'last' in dir() else 'unknown'}", "url": url}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--new", action="store_true", help="fetch the new substances of new_substances.py (+ properties, density, description)")
    a = ap.parse_args()
    subs = json.loads(SUBSTANCES.read_text(encoding="utf-8"))["substances"]
    headings = dict(HEADINGS)
    if a.new:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from new_substances import NEW_PURE
        subs = {k: {"cas": v[0]} for k, v in NEW_PURE.items()}
        headings.update(NEW_HEADINGS)
    RAW.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    for sid, rec in subs.items():
        if a.only and sid not in a.only:
            continue
        cas = rec.get("cas") or CAS_OVERRIDES.get(sid)
        d = RAW / sid
        d.mkdir(exist_ok=True)
        if not cas:
            (d / "cid.json").write_text(json.dumps({"_no_cas": True}), encoding="utf-8")
            print(f"[skip] {sid}: no CAS", flush=True)
            continue
        cid_file = d / "cid.json"
        if not cid_file.exists():
            js = get(f"{PUG}/compound/name/{urllib.parse.quote(cas)}/cids/JSON")
            js["_cas"] = cas
            cid_file.write_text(json.dumps(js, indent=1), encoding="utf-8")
            time.sleep(0.34)
        js = json.loads(cid_file.read_text(encoding="utf-8"))
        cids = (js.get("IdentifierList") or {}).get("CID") or []
        if not cids:
            print(f"[nocid] {sid} {cas}: {js.get('_error') or js.get('_not_found')}", flush=True)
            continue
        cid = cids[0]
        if a.new and not (d / "props.json").exists():
            pr = get(f"{PUG}/compound/cid/{cid}/property/MolecularFormula,MolecularWeight,IUPACName,Title/JSON")
            (d / "props.json").write_text(json.dumps(pr, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.34)
        for fn, heading in headings.items():
            f = d / fn
            if f.exists():
                continue
            out = get(f"{VIEW}/{cid}/JSON?heading={urllib.parse.quote(heading)}")
            out["_cid"] = cid
            out["_heading"] = heading
            out["_fetched"] = time.strftime("%Y-%m-%d")
            f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.34)
        n_ok += 1
        print(f"[ok] {sid} cas={cas} cid={cid}", flush=True)
    print(f"done: {n_ok} substances with a CID", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
