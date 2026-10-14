"""Minimal JCAMP-DX reader for the NIST WebBook files (IR, UV/Vis, mass spectra).

Handles ``##XYDATA=(X++(Y..Y))`` blocks including the ASDF compressions NIST uses (PAC, SQZ, DIF, DUP) and
``##PEAK TABLE=(XY..XY)`` peak lists.  Returns plain Python lists so the builder can stay dependency-free.
"""
from __future__ import annotations

import re

_SQZ = {c: i for i, c in enumerate("@ABCDEFGHI")}
_SQZ.update({c: -i for i, c in enumerate("@abcdefghi") if i})
_DIF = {c: i for i, c in enumerate("%JKLMNOPQR")}
_DIF.update({c: -i for i, c in enumerate("%jklmnopqr") if i})
_DUP = {c: i + 1 for i, c in enumerate("STUVWXYZs")}
_TOKEN = re.compile(r"[+-]?\d*\.?\d+(?:[eE][+-]?\d+)?|[@A-Za-z%]\d*\.?\d*")


def _decode_line(line: str) -> tuple[float, list[float]]:
    """Decode one ASDF line: returns (x, [y...]) with DIF/DUP expanded."""
    tokens = _TOKEN.findall(line.replace(",", " "))
    if not tokens:
        raise ValueError("empty data line")
    x = float(tokens[0])
    ys: list[float] = []
    last_was_dif = False
    for tok in tokens[1:]:
        c = tok[0]
        if c in _SQZ:
            digits = tok[1:]
            val = float(str(_SQZ[c]) + digits) if _SQZ[c] >= 0 else -float(str(-_SQZ[c]) + digits)
            ys.append(val); last_was_dif = False
        elif c in _DIF:
            digits = tok[1:]
            d = float(str(_DIF[c]) + digits) if _DIF[c] >= 0 else -float(str(-_DIF[c]) + digits)
            ys.append(ys[-1] + d); last_was_dif = True
        elif c in _DUP:
            n = int(str(_DUP[c]) + tok[1:]) if tok[1:] else _DUP[c]
            if last_was_dif:
                d = ys[-1] - ys[-2]
                for _ in range(n - 1):
                    ys.append(ys[-1] + d)
            else:
                for _ in range(n - 1):
                    ys.append(ys[-1])
        else:
            ys.append(float(tok)); last_was_dif = False
    return x, ys


def parse(text: str) -> dict:
    """Parse a single-block JCAMP-DX file into {'header': {...}, 'x': [...], 'y': [...]}."""
    header: dict[str, str] = {}
    lines = text.splitlines()
    x: list[float] = []
    y: list[float] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("##"):
            key, _, val = line[2:].partition("=")
            key = key.strip().upper()
            val = val.strip()
            if key == "XYDATA":
                xf = float(header.get("XFACTOR", 1) or 1)
                yf = float(header.get("YFACTOR", 1) or 1)
                npts = int(float(header.get("NPOINTS", 0) or 0))
                dx = float(header.get("DELTAX", 0) or 0)
                if not dx and npts > 1 and header.get("FIRSTX") and header.get("LASTX"):
                    dx = (float(header["LASTX"]) - float(header["FIRSTX"])) / (npts - 1)
                i += 1
                prev_line_dif = False
                while i < len(lines) and not lines[i].startswith("##"):
                    raw = lines[i].strip()
                    i += 1
                    if not raw or raw.startswith("$$"):
                        continue
                    x0, ys = _decode_line(raw.split("$$")[0])
                    # DIF form: the first y of a line repeats the last y of the previous line (checkpoint)
                    if prev_line_dif and y and ys:
                        ys = ys[1:]
                    prev_line_dif = any(t[0] in _DIF for t in _TOKEN.findall(raw)[1:])
                    for k, v in enumerate(ys):
                        x.append((x0 + k * dx / xf) * xf if False else x0 * xf + k * dx)
                        y.append(v * yf)
                continue
            if key in ("PEAK TABLE", "XYPOINTS"):
                i += 1
                while i < len(lines) and not lines[i].startswith("##"):
                    for pair in re.findall(r"([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)", lines[i]):
                        x.append(float(pair[0])); y.append(float(pair[1]))
                    i += 1
                continue
            header[key] = val
        i += 1
    return {"header": header, "x": x, "y": y}
