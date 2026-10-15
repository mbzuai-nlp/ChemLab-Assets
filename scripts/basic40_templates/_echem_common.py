"""Shared helpers for the t4_measure electrochemistry/titration template modules.

Used by ``gamry_ref620``, ``metrohm_899``, ``metrohm_914`` and ``metrohm_omnis`` (one module per instrument slug).
Everything here is read-only access to public-data libraries under ``assets/reactions/`` plus a small JSON-schema
subset checker. No instrument state lives here.
"""
from __future__ import annotations

import copy
import math
from typing import Any

try:  # scripts/ on sys.path (tests, Matterix runtime bridge)
    from template_library import ROOT, get_template, index_by_id, library_sha256, load_library, provenance, shared_ids_for
except ImportError:  # imported as scripts.basic40_templates.*
    from scripts.template_library import ROOT, get_template, index_by_id, library_sha256, load_library, provenance, shared_ids_for

__all__ = ["ROOT", "get_template", "index_by_id", "library_sha256", "load_library", "provenance", "shared_ids_for",
           "R", "F", "LN10", "T_REF", "number", "matches", "nullable", "obj", "arr", "num", "public_record",
           "interp_linear"]

R = 8.31446261815324  # J/(mol K), CODATA 2018 exact
F = 96485.33212331001  # C/mol, CODATA 2018 exact (N_A * e)
LN10 = math.log(10.0)
T_REF = 298.15

TEXT = {"type": "string"}
BOOL = {"type": "boolean"}


def number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def num(unit: str, minimum=None, maximum=None, *, exclusive_minimum=None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "number", "x-unit": unit}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    if exclusive_minimum is not None:
        schema["exclusiveMinimum"] = exclusive_minimum
    return schema


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


def arr(items: dict[str, Any], max_items: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "array", "items": items}
    if max_items is not None:
        schema["maxItems"] = max_items
    return schema


def matches(schema: dict[str, Any], value: Any) -> bool:
    """Portable JSON-schema subset used by the contracts (type/anyOf/enum/const/bounds/required/additionalProperties)."""
    if "anyOf" in schema:
        return any(matches(branch, value) for branch in schema["anyOf"])
    kind = schema.get("type")
    if kind is None and "properties" in schema:
        kind = "object"
    if kind == "null":
        ok = value is None
    elif kind == "boolean":
        ok = type(value) is bool
    elif kind in ("number", "integer"):
        ok = number(value) and (kind == "number" or type(value) is int)
        if ok:
            ok = not (("minimum" in schema and value < schema["minimum"]) or ("maximum" in schema and value > schema["maximum"])
                      or ("exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"])
                      or ("exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]))
    elif kind == "string":
        ok = isinstance(value, str) and schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value))
    elif kind == "array":
        ok = isinstance(value, list) and schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value))
        if ok and "items" in schema:
            ok = all(matches(schema["items"], item) for item in value)
    elif kind == "object":
        props = schema.get("properties", {})
        ok = isinstance(value, dict) and all(isinstance(k, str) for k in value)
        if ok and schema.get("additionalProperties") is False:
            ok = set(value) <= set(props)
        if ok:
            ok = set(schema.get("required", [])) <= set(value)
        if ok:
            ok = all(matches(props[k], v) for k, v in value.items() if k in props)
    else:
        ok = False
    if ok and "enum" in schema:
        ok = value in schema["enum"]
    if ok and "const" in schema:
        ok = value == schema["const"] and type(value) is type(schema["const"])
    return ok


def public_record(rel_path: str, record: dict[str, Any]) -> dict[str, Any]:
    """A library record as returned by ``template.read``: the public reference itself plus provenance."""
    return {"record": copy.deepcopy(record), **provenance(rel_path, record)}


def interp_linear(xs: list[float], ys: list[float], x: float) -> float:
    """Piecewise-linear interpolation inside the tabulated range only (no extrapolation)."""
    if len(xs) != len(ys) or not xs:
        raise ValueError("table axes differ")
    pairs = sorted(zip(xs, ys))
    if not pairs[0][0] <= x <= pairs[-1][0]:
        raise ValueError("outside tabulated range")
    for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
        if x0 <= x <= x1:
            if x == x0:
                return float(y0)
            if x == x1:
                return float(y1)
            return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return float(pairs[0][1])
