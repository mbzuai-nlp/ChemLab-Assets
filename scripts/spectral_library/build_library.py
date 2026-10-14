"""Build the spectral template library from the raw records cached by ``fetch_sources.py``.

Outputs (``assets/reactions/spectral_library/``):
  compounds.json     identity: names, CAS, PubChem CID, formula, MW, SMILES, XLogP, boiling point, ECN
  gc_retention.json  Kovats retention indices per column class (NIST), FID response (ECN), derived model fingerprint
  ir_spectra.json    NIST IR spectra resampled to a 400-4000 cm-1 absorbance grid + peak lists
  ms_ei.json         NIST electron-ionisation mass spectra (m/z, relative intensity)
  uv_vis.json        NIST UV/Vis spectra + lambda-max records (NIST JCAMP, PubChem text)
  nmr.json           1H / 13C peak lists (HMDB via PubChem) with multiplet clustering
  hplc_uv.json       reversed-phase HPLC template: XLogP-derived retention estimate + detection wavelengths
  index.json         per-compound coverage summary

Every derived number carries an ``evidence`` tag; measured values carry their source.  Run with the matterix
python (needs numpy + rdkit):  python build_library.py
"""
from __future__ import annotations

import json
import math
import re
import statistics
import sys
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compounds_list import COMPOUNDS  # noqa: E402
from ecn import effective_carbon_number  # noqa: E402
import jcamp  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "references" / "spectral_raw"
OUT = ROOT / "assets" / "reactions" / "spectral_library"
R = 8.314462618

# --- model column calibration (assumptions; the retention indices themselves are measured data) -------------
GC_MODEL = {
    "reference_temperature_K": 353.15,
    "column": {"length_m": 30.0, "inner_diameter_m": 2.5e-4, "film_thickness_m": 2.5e-7, "flow_m3_s": 1.6666666666666667e-8},
    "calibration": {
        "nonpolar": {"k_hexane_at_reference": 0.45, "log10_k_per_methylene": 0.332,
                      "note": "5 %-phenyl / PDMS style column, isothermal 80 C; assumption fitted to typical DB-5 alkane retention"},
        "polar": {"k_hexane_at_reference": 0.10, "log10_k_per_methylene": 0.28,
                   "note": "polyethylene-glycol (WAX) style column, isothermal 80 C; assumption"},
    },
    "fid_sensitivity_C_per_kg_carbon": 15.0,
    "fid_note": "typical FID ionisation yield ~0.015 C per gram of carbon (order of magnitude from detector literature); response_C_kg = 15 * 12.011 * ECN / MW",
    "evidence": "calibration_assumption",
}

STANDARD_NONPOLAR = re.compile(r"(Apiezon|SE-30|OV-1\b|OV-101|DB-1\b|DB-1MS|HP-1\b|HP-1MS|CP-Sil 5|CPSil 5|Rtx-1\b|RTX-1|SPB-1\b|Squalane|Apolane|Methyl Silicone|Methylsilicone|Polydimethyl|PDMS|Ultra-1|BP-1\b|BP1\b|Petrocol|PONA|SP-2100|SPB-Octyl|Ultra 1|OV-1,|Dimethyl|DB-Petro|SE-30,|C78|Vaseline|Paraffin|HP-PONA|Rxi-1|ZB-1|VF-1|TC-1|NB-30|E-301|OV 101|OV-1 |SP-1000)", re.I)
SEMI_NONPOLAR = re.compile(r"(DB-5|HP-5|SE-54|SE-52|OV-5|CP-Sil 8|CPSil 8|BP-5|BP5\b|Rtx-5|RTX-5|ZB-5|SPB-5|Ultra-2|Ultra 2|VF-5|TR-5|Elite-5|Optima-5|Optima 5|MDN-5|PTE-5|XTI-5|Rxi-5|SLB-5|Equity-5|NB-54|5 ?%.?Phenyl|5%-Phenyl|Phenyl Methyl|Mega 5|HP-Ultra 2|DB5|HP5|SE 54|OV-73|SPB-5|AT-5|Sil 8|Rtx-5MS|DB-5MS|HP-5MS|Zebron ZB-5)", re.I)
POLAR_WAX = re.compile(r"(Wax|WAX|Carbowax|PEG|Polyethylene glycol|Innowax|FFAP|20 ?M|Supelcowax|Stabilwax|BP-20|BP20|ZB-Wax|TC-Wax|Rtx-Wax|Nukol|OV-351|HP-20M|EC-Wax|AT-Wax|CP-Wax|DB-Wax|HP-Wax|SolGel-Wax|Sol-Gel Wax|VF-WAXms|Omegawax|Carbowax 20M|PEG-20M|Superox|SP-1000|OV-275)", re.I)


# --------------------------------------------------------------------------------------------------------
class _Tables(HTMLParser):
    """Collect every <table> with the text of the <h3> that precedes it."""

    def __init__(self):
        super().__init__()
        self.tables: list[dict] = []
        self._h3 = ""
        self._in_h3 = False
        self._cell = None
        self._row = None
        self._table = None
        self.title = ""
        self._in_title = False
        self._sub = False
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self._in_h3 = True; self._h3 = ""
        elif tag == "table":
            self._table = {"heading": self._h3.strip(), "rows": []}
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "title":
            self._in_title = True
        elif tag == "sub":
            self._sub = True

    def handle_endtag(self, tag):
        if tag == "h3":
            self._in_h3 = False
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table); self._table = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table["rows"].append([re.sub(r"\s+", " ", "".join(c)).strip() for c in self._row]); self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(self._cell); self._cell = None
        elif tag == "title":
            self._in_title = False
        elif tag == "sub":
            self._sub = False

    def handle_data(self, data):
        if self._in_h3:
            self._h3 += data
        if self._cell is not None:
            self._cell.append(data)
        if self._in_title:
            self.title += data
        self.text_parts.append(data)


def parse_html(path: Path) -> _Tables | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    p = _Tables()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    return p


def nist_formula(html_text: str) -> str | None:
    m = re.search(r"Formula</a>:</strong>\s*(.*?)</li>", html_text, re.S) or re.search(r"Formula:</strong>\s*(.*?)</li>", html_text, re.S)
    if not m:
        return None
    f = re.sub(r"<[^>]+>", "", m.group(1))
    return re.sub(r"\s+", "", unescape(f))


def norm_formula(f: str | None) -> str | None:
    if not f:
        return None
    return re.sub(r"[\s+\-]", "", f).replace("−", "")


def fnum(s: str) -> float | None:
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s or "")
    return float(m.group(0)) if m else None


# --------------------------------------------------------------------------------------------------------
def gc_indices(sid: str) -> dict:
    p = parse_html(RAW / sid / "gc.html")
    if p is None:
        return {}
    rows_by_class: dict[str, list[dict]] = {"standard_nonpolar": [], "semi_standard_nonpolar": [], "standard_polar": [], "other": []}
    for t in p.tables:
        h = t["heading"]
        if not h.startswith(("Kovats", "Van Den Dool", "Normal alkane")):
            continue  # Lee / other index systems are not on the n-alkane scale
        header = t["rows"][0] if t["rows"] else []
        try:
            i_col = header.index("I")
        except ValueError:
            continue
        i_phase = header.index("Active phase") if "Active phase" in header else 1
        i_type = header.index("Column type") if "Column type" in header else 0
        i_temp = header.index("Temperature (C)") if "Temperature (C)" in header else None
        i_ref = header.index("Reference") if "Reference" in header else None
        polar_section = "polar column" in h and "non-polar" not in h
        for r in t["rows"][1:]:
            if len(r) <= i_col:
                continue
            val = fnum(r[i_col])
            if val is None:
                continue
            phase = r[i_phase]
            if polar_section:
                cls = "standard_polar" if POLAR_WAX.search(phase) else "other"
            elif SEMI_NONPOLAR.search(phase):
                cls = "semi_standard_nonpolar"
            elif STANDARD_NONPOLAR.search(phase):
                cls = "standard_nonpolar"
            else:
                cls = "other"
            rows_by_class[cls].append({
                "I": val, "phase": phase, "column_type": r[i_type], "program": h.split(",")[-1].strip(),
                "index_kind": "kovats" if h.startswith("Kovats") else ("normal_alkane" if h.startswith("Normal") else "van_den_dool_kratz"),
                "temperature_C": fnum(r[i_temp]) if i_temp is not None else None,
                "reference": r[i_ref] if i_ref is not None else None,
            })
    out = {}
    for cls, rows in rows_by_class.items():
        if cls == "other" or not rows:
            continue
        cap = [r for r in rows if r["column_type"].lower().startswith("capillary")]
        use = cap if len(cap) >= 3 else rows
        vals = [r["I"] for r in use]
        med = statistics.median(vals)
        # drop gross outliers (> 60 index units from the median) before the final statistics
        kept = [v for v in vals if abs(v - med) <= 60] or vals
        out[cls] = {
            "median": round(statistics.median(kept), 1), "mean": round(statistics.fmean(kept), 1),
            "std": round(statistics.pstdev(kept), 1) if len(kept) > 1 else 0.0,
            "min": min(kept), "max": max(kept), "n": len(kept), "n_raw": len(rows),
            "n_references": len({r["reference"] for r in use}),
            "phases": sorted({r["phase"] for r in use})[:12],
            "source": "NIST Chemistry WebBook, Gas Chromatography data (Mask=2000)", "evidence": "measured_literature",
        }
    return out


def thermo(sid: str) -> dict:
    p = parse_html(RAW / sid / "thermo.html")
    if p is None:
        return {}
    text = re.sub(r"\s+", " ", unescape("".join(p.text_parts)))
    out = {}
    m = re.search(r"T\s*boil\s*([\d.]+)", text)
    if m:
        out["boiling_point_K"] = float(m.group(1))
    m = re.search(r"T\s*fus\s*([\d.]+)", text)
    if m:
        out["melting_point_K"] = float(m.group(1))
    vap = []
    for t in p.tables:
        if not t["rows"] or "vap" not in t["heading"].lower() and "Enthalpy of vaporization" not in " ".join(t["rows"][0]):
            continue
        hdr = t["rows"][0]
        if not any("vap" in c for c in hdr):
            continue
        i_val = 0
        i_T = next((i for i, c in enumerate(hdr) if c.startswith("Temperature")), None)
        for r in t["rows"][1:]:
            v = fnum(r[i_val]) if r else None
            if v is None:
                continue
            T = fnum(r[i_T]) if i_T is not None and len(r) > i_T else None
            vap.append({"delta_vap_H_kJ_mol": v, "T_K": T})
    if vap:
        out["enthalpy_of_vaporization"] = vap[:20]
    return out


def choose_delta_vap(th: dict) -> tuple[float | None, str]:
    vap = th.get("enthalpy_of_vaporization") or []
    with_T = [v for v in vap if v["T_K"] and 300 <= v["T_K"] <= 480]
    if with_T:
        best = min(with_T, key=lambda v: abs(v["T_K"] - 373.0))
        return best["delta_vap_H_kJ_mol"] * 1000.0, f"NIST WebBook enthalpy of vaporization at {best['T_K']} K"
    if vap:
        return vap[0]["delta_vap_H_kJ_mol"] * 1000.0, "NIST WebBook enthalpy of vaporization (unspecified/other T)"
    if th.get("boiling_point_K"):
        return 88.0 * th["boiling_point_K"], "Trouton rule 88 J/(mol K) x boiling point (assumption)"
    return None, "missing"


# --------------------------------------------------------------------------------------------------------
def ir_spectra(sid: str) -> list[dict]:
    grid = np.arange(400.0, 4000.0 + 1e-9, 4.0)
    specs = []
    for f in sorted((RAW / sid).glob("ir_*.jdx")):
        if f.stat().st_size == 0:
            continue
        d = jcamp.parse(f.read_text(encoding="latin-1"))
        h = d["header"]
        if not d["x"] or "Spectrum not found" in h.get("TITLE", ""):
            continue
        x = np.asarray(d["x"]); y = np.asarray(d["y"])
        if h.get("XUNITS", "").upper().startswith("MICRO"):
            x = 1e4 / x
        if h.get("YUNITS", "").upper().startswith("TRANS"):
            y = np.clip(y, 1e-4, None)
            y = -np.log10(y / max(1.0, float(np.nanmax(y)) if np.nanmax(y) > 1.5 else 1.0))  # % transmittance vs fraction
        order = np.argsort(x)
        x, y = x[order], y[order]
        y = y - float(np.nanmin(y))
        ymax = float(np.nanmax(y))
        if not ymax or not math.isfinite(ymax):
            continue
        y = y / ymax
        a = np.interp(grid, x, y, left=np.nan, right=np.nan)
        # peaks: local maxima above 0.08 with a 24 cm-1 window
        peaks = []
        for i in range(3, len(a) - 3):
            if np.isnan(a[i]) or a[i] < 0.08:
                continue
            w = a[i - 3:i + 4]
            if a[i] == np.nanmax(w) and a[i] > np.nanmin(w) + 0.02:
                peaks.append((round(float(grid[i]), 0), round(float(a[i]), 3)))
        peaks.sort(key=lambda t: -t[1])
        state = h.get("STATE", "")
        specs.append({
            "nist_index": int(f.stem.split("_")[1]), "title": h.get("TITLE"), "nist_source": h.get("$NIST SOURCE"),
            "origin": h.get("ORIGIN"), "state": state, "sampling": h.get("SAMPLING PROCEDURE"), "resolution": h.get("RESOLUTION"),
            "x_units_original": h.get("XUNITS"), "y_units_original": h.get("YUNITS"),
            "x_range_cm-1": [round(float(x[0]), 1), round(float(x[-1]), 1)],
            "grid_cm-1": {"start": 400.0, "stop": 4000.0, "step": 4.0},
            "absorbance_normalized": [None if np.isnan(v) else round(float(v), 3) for v in a],
            "peaks_cm-1": [list(pk) for pk in peaks[:15]],
            "source": "NIST Chemistry WebBook IR spectrum (JCAMP-DX)", "evidence": "measured",
        })
    # preferred: condensed phase first (FTIR liquid/ATR use), then gas
    def rank(s):
        st = s["state"].lower()
        return 0 if ("liquid" in st or "neat" in st or "solution" in st or "solid" in st or "mull" in st or "kbr" in st) else 1
    specs.sort(key=lambda s: (rank(s), s["nist_index"]))
    return specs


def ms_spectrum(sid: str) -> dict | None:
    f = RAW / sid / "ms_0.jdx"
    if not f.exists() or f.stat().st_size == 0:
        return None
    d = jcamp.parse(f.read_text(encoding="latin-1"))
    if not d["x"]:
        return None
    h = d["header"]
    ymax = max(d["y"]) or 1.0
    peaks = [[float(x), round(y / ymax * 100.0, 2)] for x, y in zip(d["x"], d["y"])]
    peaks.sort(key=lambda t: t[0])
    top = sorted(peaks, key=lambda t: -t[1])[:8]
    return {"title": h.get("TITLE"), "nist_ms_no": h.get("$NIST MASS SPEC NO"), "molform": h.get("MOLFORM"), "mw": fnum(h.get("MW", "")),
            "ionisation": "EI 70 eV (NIST/EPA/NIH library)", "peaks_mz_relint": peaks, "base_peak_mz": top[0][0] if top else None,
            "top_peaks": top, "source": "NIST Chemistry WebBook mass spectrum (JCAMP-DX)", "evidence": "measured"}


def uv_records(sid: str) -> dict:
    out = {"spectra": [], "lambda_max_records": []}
    for f in sorted((RAW / sid).glob("uv_*.jdx")):
        if f.stat().st_size == 0:
            continue
        d = jcamp.parse(f.read_text(encoding="latin-1"))
        if not d["x"]:
            continue
        h = d["header"]
        x = np.asarray(d["x"]); y = np.asarray(d["y"])
        order = np.argsort(x); x, y = x[order], y[order]
        lam = []
        for i in range(2, len(y) - 2):
            if y[i] == y[i - 2:i + 3].max() and y[i] > y[i - 2:i + 3].min():
                lam.append([round(float(x[i]), 1), round(float(y[i]), 3)])
        step = max(1, len(x) // 400)
        out["spectra"].append({
            "nist_index": int(f.stem.split("_")[1]), "title": h.get("TITLE"), "origin": h.get("ORIGIN"), "solvent_or_state": h.get("STATE") or h.get("SOLVENT NAME") or h.get("$NIST SOURCE"),
            "x_units": h.get("XUNITS"), "y_units": h.get("YUNITS"), "wavelength_nm": [round(float(v), 2) for v in x[::step]],
            "y": [round(float(v), 4) for v in y[::step]], "local_maxima": lam[:10],
            "source": "NIST Chemistry WebBook UV/Vis spectrum (JCAMP-DX)", "evidence": "measured",
        })
    pj = RAW / sid / "pubchem_uv.json"
    if pj.exists() and pj.stat().st_size:
        try:
            rec = json.loads(pj.read_text())
        except json.JSONDecodeError:
            rec = {}
        refs = {r["ReferenceNumber"]: r.get("SourceName") for r in rec.get("Record", {}).get("Reference", [])}

        def walk(sec):
            for s in sec.get("Section", []):
                for info in s.get("Information", []):
                    for sm in info.get("Value", {}).get("StringWithMarkup", []):
                        txt = sm.get("String", "")
                        for m in re.finditer(r"(?:\(([^)]*)\))?[^;,]*?(\d{3}(?:\.\d+)?)\s*nm(?:\s*\((?:log\s*e(?:psilon)?|log ε)\s*=?\s*([\d.]+)\))?", txt):
                            out["lambda_max_records"].append({"lambda_nm": float(m.group(2)), "log_epsilon": float(m.group(3)) if m.group(3) else None,
                                                              "solvent": m.group(1), "text": txt[:200], "source": refs.get(info.get("ReferenceNumber"), "PubChem"), "evidence": "measured_literature"})
                walk(s)
        walk(rec.get("Record", {}))
    return out


def nmr_records(sid: str) -> dict:
    out = {}
    for nuc, fname, gap in (("1H", "pubchem_nmr_1h.json", 0.08), ("13C", "pubchem_nmr_13c.json", 0.6)):
        pj = RAW / sid / fname
        if not pj.exists() or not pj.stat().st_size:
            continue
        try:
            rec = json.loads(pj.read_text())
        except json.JSONDecodeError:
            continue
        refs = {r["ReferenceNumber"]: r.get("SourceName") for r in rec.get("Record", {}).get("Reference", [])}
        groups: dict[int, dict] = {}
        links = []

        def walk(sec):
            for s in sec.get("Section", []):
                for info in s.get("Information", []):
                    rn = info.get("ReferenceNumber")
                    name = info.get("Name") or ""
                    val = ""
                    for sm in info.get("Value", {}).get("StringWithMarkup", []):
                        val = sm.get("String", ""); 
                        for mk in sm.get("Markup", []):
                            if mk.get("URL") and "nmrshiftdb" in mk["URL"]:
                                links.append(mk["URL"])
                    g = groups.setdefault(rn, {"source": refs.get(rn)})
                    if name:
                        g[name] = val
                walk(s)
        walk(rec.get("Record", {}))
        spectra = []
        for rn, g in groups.items():
            shifts = g.get("Shifts [ppm]:Intensity")
            if not shifts:
                continue
            peaks = []
            for tok in shifts.split(","):
                if ":" in tok:
                    a, b = tok.split(":", 1)
                    try:
                        peaks.append([float(a), float(b)])
                    except ValueError:
                        pass
            peaks.sort()
            clusters = []
            for ppm, inten in peaks:
                if clusters and ppm - clusters[-1]["_last"] <= gap:
                    c = clusters[-1]; c["lines"].append([ppm, inten]); c["_last"] = ppm
                else:
                    clusters.append({"lines": [[ppm, inten]], "_last": ppm})
            multiplets = []
            for c in clusters:
                tot = sum(i for _, i in c["lines"]) or 1.0
                multiplets.append({"center_ppm": round(sum(p * i for p, i in c["lines"]) / tot, 3), "integral": round(tot, 2),
                                   "n_lines": len(c["lines"]), "range_ppm": [c["lines"][0][0], c["lines"][-1][0]]})
            spectra.append({"hmdb_spectrum_id": g.get("Spectra ID"), "instrument": g.get("Instrument Type"), "frequency": g.get("Frequency"),
                            "solvent": g.get("Solvent"), "pH": g.get("pH"), "peaks_ppm_intensity": peaks, "multiplets": multiplets,
                            "source": f"{g.get('source')} via PubChem", "evidence": "measured"})
        if spectra or links:
            out[nuc] = {"spectra": spectra, "nmrshiftdb_links": sorted(set(links))}
    return out


# --------------------------------------------------------------------------------------------------------
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    compounds, gc, ir, ms, uv, nmr, hplc, index = {}, {}, {}, {}, {}, {}, {}, {}
    cal = GC_MODEL["calibration"]
    m = GC_MODEL["column"]
    core = m["inner_diameter_m"] - 2 * m["film_thickness_m"]
    t0 = m["length_m"] / (m["flow_m3_s"] / (math.pi * core**2 / 4))
    for sid, name_en, name_zh, cas, cls in COMPOUNDS:
        d = RAW / sid
        if not d.exists():
            print(f"!! no raw dir for {sid}"); continue
        props = {}
        pj = d / "pubchem_props.json"
        if pj.exists() and pj.stat().st_size:
            try:
                props = json.loads(pj.read_text())["PropertyTable"]["Properties"][0]
            except Exception:
                props = {}
        gc_html = (d / "gc.html").read_text(encoding="utf-8", errors="replace") if (d / "gc.html").exists() else ""
        nf = nist_formula(gc_html) or nist_formula((d / "thermo.html").read_text(encoding="utf-8", errors="replace") if (d / "thermo.html").exists() else "")
        nist_name = re.search(r'<h1 id="Top">(.*?)</h1>', gc_html)
        th = thermo(sid)
        smiles = props.get("SMILES") or props.get("ConnectivitySMILES")
        ecn = effective_carbon_number(smiles) if smiles else None
        mw = float(props["MolecularWeight"]) if props.get("MolecularWeight") else None
        formula_ok = norm_formula(nf) == norm_formula(props.get("MolecularFormula")) if nf and props.get("MolecularFormula") else None
        compounds[sid] = {
            "id": sid, "name_en": name_en, "name_zh": name_zh, "class": cls, "cas": cas, "nist_id": "C" + cas.replace("-", ""),
            "nist_name": unescape(nist_name.group(1)) if nist_name else None, "pubchem_cid": props.get("CID"), "iupac_name": props.get("IUPACName"),
            "formula": props.get("MolecularFormula"), "formula_nist": nf, "formula_cross_check": formula_ok,
            "molar_mass_g_mol": mw, "smiles": smiles, "xlogp": props.get("XLogP"),
            "boiling_point_K": th.get("boiling_point_K"), "melting_point_K": th.get("melting_point_K"),
            "effective_carbon_number": ecn, "sources": {"identity": "PubChem PUG REST (CAS lookup)", "phase_change": "NIST Chemistry WebBook (Mask=4)"},
        }
        # --- GC
        ri = gc_indices(sid)
        if smiles and re.fullmatch(r"C+", smiles) and len(smiles) >= 5:  # n-alkane: I = 100 n by definition on every column
            for cls_name in ("standard_nonpolar", "semi_standard_nonpolar", "standard_polar"):
                ri.setdefault(cls_name, {"median": 100.0 * len(smiles), "mean": 100.0 * len(smiles), "std": 0.0, "min": 100.0 * len(smiles), "max": 100.0 * len(smiles),
                                         "n": 0, "n_raw": 0, "n_references": 0, "phases": [], "source": "Kovats retention index definition (n-alkane anchor)", "evidence": "definition"})
        dh, dh_src = choose_delta_vap(th)
        if dh is None and ri:
            dh, dh_src = 60000.0, "assumption: 60 kJ/mol for a high-boiling solid without a NIST boiling point"
        fp = {}
        fid_resp = None
        if ecn and mw:
            fid_resp = GC_MODEL["fid_sensitivity_C_per_kg_carbon"] * 12.011 * ecn["ecn"] / mw
        for col, classes in (("nonpolar", ("semi_standard_nonpolar", "standard_nonpolar")), ("polar", ("standard_polar",))):
            chosen = next((c for c in classes if c in ri), None)
            if not chosen or dh is None:
                continue
            I = ri[chosen]["median"]
            c = cal[col]
            k = c["k_hexane_at_reference"] * 10 ** (c["log10_k_per_methylene"] * (I / 100.0 - 6.0))
            fp[col] = {
                "k_ref": round(k, 5), "delta_h_J_mol": round(-dh, 1), "response_C_kg": round(fid_resp or 0.0, 4),
                "reference_temperature_K": GC_MODEL["reference_temperature_K"],
                "retention_index_used": I, "retention_index_class": chosen,
                "predicted_isothermal_retention_s_at_80C": round(t0 * (1 + k), 1),
                "evidence": "derived", "derivation": {"k_ref": "NIST retention index -> alkane calibration of the model column (see gc_model.calibration)",
                                                       "delta_h_J_mol": f"-(enthalpy of vaporization): {dh_src}",
                                                       "response_C_kg": "ECN (Scanlon & Willis 1985) x FID carbon sensitivity (see gc_model.fid_note)"},
            }
        gc[sid] = {"retention_index": ri, "fid": {"ecn": ecn, "response_C_kg": round(fid_resp, 4) if fid_resp else None, "evidence": "computed_ecn_scanlon_willis_1985"} if ecn else None,
                   "delta_vap_H_J_mol": dh, "delta_vap_H_source": dh_src, "model_fingerprint": fp}
        # --- spectra
        ir[sid] = ir_spectra(sid)
        ms[sid] = ms_spectrum(sid)
        uv[sid] = uv_records(sid)
        nmr[sid] = nmr_records(sid)
        # --- HPLC (reversed phase, UV detection) template
        lam = [r["lambda_nm"] for r in uv[sid]["lambda_max_records"] if r["lambda_nm"] >= 200] + [l[0] for s in uv[sid]["spectra"] for l in s["local_maxima"] if l[0] >= 200]
        xlogp = props.get("XLogP")
        k_rp = 10 ** (-0.35 + 0.55 * xlogp) if xlogp is not None else None
        hplc[sid] = {
            "xlogp": xlogp, "reversed_phase_C18": {"k_estimate": round(k_rp, 3) if k_rp is not None else None,
                                                    "predicted_retention_min": round(1.5 * (1 + k_rp), 2) if k_rp is not None else None,
                                                    "assumption": "log10 k = -0.35 + 0.55 XLogP for a C18 column in ~60:40 methanol/water, t0 = 1.5 min", "evidence": "derived_assumption"},
            "uv_detection_candidates_nm": sorted({round(v) for v in lam})[:8],
            "default_detection_nm": 254.0 if any(210 <= v <= 300 for v in lam) or (smiles and "c" in smiles) else 210.0,
            "note": "retention is method-specific; only the elution order (by XLogP) and detection wavelengths are transferable",
        }
        index[sid] = {
            "name_zh": name_zh, "formula_cross_check": formula_ok,
            "gc_retention_index_classes": sorted(ri), "gc_model_fingerprint": sorted(fp),
            "ir_spectra": len(ir[sid]), "ms": ms[sid] is not None, "uv_spectra": len(uv[sid]["spectra"]), "uv_lambda_max_records": len(uv[sid]["lambda_max_records"]),
            "nmr_1h_spectra": len(nmr[sid].get("1H", {}).get("spectra", [])), "nmr_13c_spectra": len(nmr[sid].get("13C", {}).get("spectra", [])),
            "hplc_template": k_rp is not None,
        }
    meta = {"schema_version": "1.0.0", "built_from": "references/spectral_raw (fetch_sources.py)", "n_compounds": len(compounds)}
    dump = lambda name, obj: (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    dump("compounds.json", {**meta, "compounds": compounds})
    dump("gc_retention.json", {**meta, "gc_model": GC_MODEL, "compounds": gc})
    dump("ir_spectra.json", {**meta, "grid_note": "absorbance normalised to 1 on a 400-4000 cm-1 grid, step 4; null outside the measured range", "compounds": ir})
    dump("ms_ei.json", {**meta, "compounds": ms})
    dump("uv_vis.json", {**meta, "compounds": uv})
    dump("nmr.json", {**meta, "compounds": nmr})
    dump("hplc_uv.json", {**meta, "compounds": hplc})
    dump("index.json", {**meta, "compounds": index})
    # summary
    cov = {k: sum(1 for v in index.values() if (v[k] if isinstance(v[k], bool) else bool(v[k]))) for k in ("gc_retention_index_classes", "gc_model_fingerprint", "ir_spectra", "ms", "uv_spectra", "uv_lambda_max_records", "nmr_1h_spectra", "nmr_13c_spectra", "hplc_template")}
    print(json.dumps({"n_compounds": len(compounds), "coverage": cov, "formula_mismatch": [k for k, v in index.items() if v["formula_cross_check"] is False]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
