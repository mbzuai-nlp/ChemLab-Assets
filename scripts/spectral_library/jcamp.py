"""Minimal JCAMP-DX reader for the NIST WebBook files (IR, UV/Vis, mass spectra).

Handles ``##XYDATA=(X++(Y..Y))`` blocks including the ASDF compressions NIST uses (PAC, SQZ, DIF, DUP) and
``##PEAK TABLE=(XY..XY)`` peak lists.  Returns plain Python lists so the builder can stay dependency-free.

2026-10-14 decoder fixes (merged from the FT/IR-4700 review decoder
``outputs/basic40_batch02/t3_analytical/jasco_ftir4700/templates/jcamp_fixed.py``):

* Abscissa step.  ``DELTAX`` is a rounded header value; ``FIRSTX``/``LASTX``/``NPOINTS`` define the step exactly.
  When the declared ``DELTAX`` contradicts them (wrong sign, e.g. Coblentz files that declare ``DELTAX=1`` for
  descending data, or a magnitude off by more than 1 %) the step ``(LASTX - FIRSTX) / (NPOINTS - 1)`` is used.
  A declared value that agrees within 1 % is kept as before, so files whose ``DELTAX`` is merely rounded decode
  exactly as they did before the fix (this keeps every unaffected library record value-identical).
* Multi-block files.  Files with several ``##TITLE ... ##END=`` blocks (PNNL/IARPA optical-constant files with a
  real-index and an imaginary-index block; integrating-sphere reflectance files with a total and a diffuse block)
  are returned block by block in ``blocks``; the top-level ``header``/``x``/``y`` are those of the FIRST block
  (previously all blocks were concatenated into one trace).  Single-block files decode exactly as before.
"""
from __future__ import annotations

import re

_SQZ = {c: i for i, c in enumerate("@ABCDEFGHI")}
_SQZ.update({c: -i for i, c in enumerate("@abcdefghi") if i})
_DIF = {c: i for i, c in enumerate("%JKLMNOPQR")}
_DIF.update({c: -i for i, c in enumerate("%jklmnopqr") if i})
_DUP = {c: i + 1 for i, c in enumerate("STUVWXYZs")}
_TOKEN = re.compile(r"[+-]?\d*\.?\d+(?:[eE][+-]?\d+)?|[@A-Za-z%]\d*\.?\d*")

#: relative tolerance within which a declared DELTAX is accepted as a rounded form of (LASTX-FIRSTX)/(NPOINTS-1)
DELTAX_REL_TOL = 0.01


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


def abscissa_step(header: dict[str, str]) -> tuple[float, str]:
    """Return (step, rule) for an XYDATA block.  rule is 'declared', 'computed' or 'computed_deltax_inconsistent'."""
    npts = int(float(header.get("NPOINTS", 0) or 0))
    dx = float(header.get("DELTAX", 0) or 0)
    if npts > 1 and header.get("FIRSTX") and header.get("LASTX"):
        computed = (float(header["LASTX"]) - float(header["FIRSTX"])) / (npts - 1)
        if not dx:
            return computed, "computed"
        if computed and (dx * computed < 0 or abs(dx - computed) > DELTAX_REL_TOL * abs(computed)):
            return computed, "computed_deltax_inconsistent"
    return dx, "declared"


def parse(text: str) -> dict:
    """Parse a JCAMP-DX file.

    Returns {'header', 'x', 'y', 'blocks'}: header/x/y belong to the first data block; ``blocks`` lists every block
    as {'header', 'x', 'y', 'x_step_rule'}.
    """
    blocks = [_parse_block(chunk) for chunk in _split_blocks(text)]
    blocks = [b for b in blocks if b["x"] or b["header"]] or [{"header": {}, "x": [], "y": [], "x_step_rule": None}]
    first = blocks[0]
    return {"header": first["header"], "x": first["x"], "y": first["y"], "blocks": blocks}


def _split_blocks(text: str) -> list[str]:
    chunks, current = [], []
    for line in text.splitlines():
        current.append(line)
        if line.strip().upper().startswith("##END="):
            chunks.append("\n".join(current))
            current = []
    if any(line.strip() for line in current):
        chunks.append("\n".join(current))
    return chunks


def _parse_block(text: str) -> dict:
    header: dict[str, str] = {}
    lines = text.splitlines()
    x: list[float] = []
    y: list[float] = []
    rule = None
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
                dx, rule = abscissa_step(header)
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
                        x.append(x0 * xf + k * dx)
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
    return {"header": header, "x": x, "y": y, "x_step_rule": rule}
