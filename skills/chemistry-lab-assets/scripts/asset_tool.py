#!/usr/bin/env python3
"""Static Chemistry Lab asset contract tools. Python standard library only.

This is NOT a runtime, MHS SDK, arbitrary-code loader, or full JSON Schema
implementation. Only the explicit SCHEMA_KEYWORDS subset below is supported.
Unknown schema keywords and nonlocal references fail closed. Contract strings
are data: no entrypoints, expressions, accessors, or examples are executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "assets" / "asset-contract.schema.json"
SCHEMA_KEYWORDS = frozenset({
    "$schema", "$id", "$defs", "$ref", "title", "description", "default", "examples",
    "type", "const", "enum", "required", "properties", "additionalProperties",
    "propertyNames", "items", "prefixItems", "minItems", "maxItems", "uniqueItems",
    "minProperties", "maxProperties", "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "allOf", "anyOf", "oneOf", "not", "x-unit", "x-reference-conditions",
})
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
STATIC_LIMITATION = (
    "仅检查合同结构、引用、声明一致性及可选文件完整性；不执行 API，"
    "不验证入口存在、实际返回字段、场景对象、动力学正确性或验证证据内容真实性。"
)


class ContractError(ValueError):
    """A contract or its schema cannot be safely interpreted."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"重复 JSON 键: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ContractError(f"禁止非有限 JSON 数字: {value}")
    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ContractError(f"禁止溢出的 JSON 数字: {value}")
        return parsed
    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=reject_constant, parse_float=finite_float)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"无法读取 {path}: {exc}") from exc


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        not isinstance(value, float) or math.isfinite(value))


def _has_type(value: Any, name: str) -> bool:
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: _is_number(value) and (isinstance(value, int) or value.is_integer()),
        "number": lambda: _is_number(value),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }[name]()


def _equal(left: Any, right: Any) -> bool:
    if _is_number(left) and _is_number(right):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _resolve_ref(ref: Any, root: dict[str, Any]) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        raise ContractError(f"只支持当前 schema 的 #/$defs/ 本地引用: {ref!r}")
    current: Any = root
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise ContractError(f"不存在的 schema 引用: {ref}")
        current = current[token]
    if not isinstance(current, (dict, bool)):
        raise ContractError(f"引用目标不是 schema: {ref}")
    return current


def lint_schema(schema: Any, root: Any = None, path: str = "$schema") -> list[str]:
    """Check supported schema vocabulary. Never silently accept other keywords."""
    if root is None:
        root = schema
    errors: list[str] = []
    if isinstance(schema, bool):
        return errors
    if not isinstance(schema, dict):
        return [f"{path}: schema 必须是对象或布尔值"]
    for key in schema:
        if key not in SCHEMA_KEYWORDS:
            errors.append(f"{path}.{key}: 静态工具不支持此 JSON Schema 关键词")
    if "$ref" in schema:
        try:
            _resolve_ref(schema["$ref"], root)
        except ContractError as exc:
            errors.append(f"{path}: {exc}")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not types or any(not isinstance(t, str) or t not in TYPES for t in types):
            errors.append(f"{path}.type: 无效类型")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error) as exc:
            errors.append(f"{path}.pattern: 无效正则表达式 {exc}")
    for key in ("$defs", "properties"):
        if key in schema:
            if not isinstance(schema[key], dict):
                errors.append(f"{path}.{key}: 必须是对象")
            else:
                for name, child in schema[key].items():
                    errors.extend(lint_schema(child, root, f"{path}.{key}.{name}"))
    for key in ("items", "additionalProperties", "propertyNames", "not"):
        if key in schema:
            errors.extend(lint_schema(schema[key], root, f"{path}.{key}"))
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        if key in schema:
            if not isinstance(schema[key], list) or (key != "prefixItems" and not schema[key]):
                errors.append(f"{path}.{key}: 必须是 schema 数组")
            else:
                for i, child in enumerate(schema[key]):
                    errors.extend(lint_schema(child, root, f"{path}.{key}[{i}]"))
    if "required" in schema and (not isinstance(schema["required"], list) or
                                  any(not isinstance(k, str) for k in schema["required"])):
        errors.append(f"{path}.required: 必须是字符串数组")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        errors.append(f"{path}.enum: 必须是非空数组")
    for key in ("minItems", "maxItems", "minProperties", "maxProperties", "minLength", "maxLength"):
        if key in schema and (not isinstance(schema[key], int) or isinstance(schema[key], bool) or schema[key] < 0):
            errors.append(f"{path}.{key}: 必须是非负整数")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if key in schema and (not _is_number(schema[key]) or (key == "multipleOf" and schema[key] <= 0)):
            errors.append(f"{path}.{key}: 无效数值约束")
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        errors.append(f"{path}.uniqueItems: 必须是布尔值")
    if "x-unit" in schema and (not isinstance(schema["x-unit"], str) or not schema["x-unit"].strip()):
        errors.append(f"{path}.x-unit: 必须是非空单位字符串")
    return errors


def validate_value(value: Any, schema: Any, root: Any = None, path: str = "$", depth: int = 0) -> list[str]:
    """Validate a JSON value against the explicitly supported subset."""
    if root is None:
        root = schema
    if depth > 100:
        return [f"{path}: schema 递归超过 100 层，可能存在循环引用"]
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: schema 不允许该值"]
    errors: list[str] = []
    if "$ref" in schema:
        try:
            errors.extend(validate_value(value, _resolve_ref(schema["$ref"], root), root, path, depth + 1))
        except ContractError as exc:
            errors.append(f"{path}: {exc}")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_has_type(value, name) for name in types):
            return errors + [f"{path}: 类型应为 {types}，实际为 {type(value).__name__}"]
    if "const" in schema and not _equal(value, schema["const"]):
        errors.append(f"{path}: 值应为 {schema['const']!r}")
    if "enum" in schema and not any(_equal(value, option) for option in schema["enum"]):
        errors.append(f"{path}: 值不在 enum 中")
    for key in ("allOf", "anyOf", "oneOf"):
        if key in schema:
            branches = [validate_value(value, child, root, path, depth + 1) for child in schema[key]]
            passed = sum(not branch for branch in branches)
            if key == "allOf":
                errors.extend(error for branch in branches for error in branch)
            elif (key == "anyOf" and passed == 0) or (key == "oneOf" and passed != 1):
                errors.append(f"{path}: 不满足 {key}")
    if "not" in schema and not validate_value(value, schema["not"], root, path, depth + 1):
        errors.append(f"{path}: 命中禁止的 not schema")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: 缺少必填字段")
        props = schema.get("properties", {})
        for key, child in value.items():
            if "propertyNames" in schema:
                errors.extend(validate_value(key, schema["propertyNames"], root, f"{path}.<key:{key}>", depth + 1))
            if key in props:
                errors.extend(validate_value(child, props[key], root, f"{path}.{key}", depth + 1))
            elif "additionalProperties" in schema:
                errors.extend(validate_value(child, schema["additionalProperties"], root, f"{path}.{key}", depth + 1))
        for key, invalid in (("minProperties", len(value) < schema.get("minProperties", 0)),
                             ("maxProperties", len(value) > schema.get("maxProperties", math.inf))):
            if invalid:
                errors.append(f"{path}: 不满足 {key}")
    if isinstance(value, list):
        prefix = schema.get("prefixItems", [])
        for i, child in enumerate(value):
            item_schema = prefix[i] if i < len(prefix) else schema.get("items", True)
            errors.extend(validate_value(child, item_schema, root, f"{path}[{i}]", depth + 1))
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            errors.append(f"{path}: 数组长度不满足约束")
        if schema.get("uniqueItems") and any(_equal(value[i], value[j]) for i in range(len(value)) for j in range(i)):
            errors.append(f"{path}: 数组包含重复值")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            errors.append(f"{path}: 字符串长度不满足约束")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: 字符串不满足 pattern")
    if _is_number(value):
        comparisons = {
            "minimum": lambda bound: value >= bound,
            "maximum": lambda bound: value <= bound,
            "exclusiveMinimum": lambda bound: value > bound,
            "exclusiveMaximum": lambda bound: value < bound,
        }
        for key, compare in comparisons.items():
            if key in schema and not compare(schema[key]):
                errors.append(f"{path}: 不满足 {key}={schema[key]}")
        if "multipleOf" in schema:
            quotient = value / schema["multipleOf"]
            if not math.isclose(quotient, round(quotient), rel_tol=1e-10, abs_tol=1e-10):
                errors.append(f"{path}: 不满足 multipleOf")
    return errors


def _expanded(schema: Any, root: dict[str, Any], seen: frozenset[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    if "$ref" not in schema or schema["$ref"] in seen:
        return schema
    target = _resolve_ref(schema["$ref"], root)
    if not isinstance(target, dict):
        return schema
    result = dict(_expanded(target, root, seen | {schema["$ref"]}))
    result.update({k: v for k, v in schema.items() if k != "$ref"})
    return result


def _numeric_unit_errors(schema: Any, root: Any, path: str, seen: frozenset[str] = frozenset()) -> list[str]:
    if not isinstance(schema, dict):
        return []
    errors: list[str] = []
    if "$ref" in schema and schema["$ref"] not in seen:
        errors.extend(_numeric_unit_errors(_expanded(schema, root), root, path, seen | {schema["$ref"]}))
        return errors
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    if any(t in ("integer", "number") for t in types) and not schema.get("x-unit"):
        errors.append(f"{path}: 数值输入参数必须声明 x-unit，无量纲使用 1")
    for key in ("properties", "$defs"):
        for name, child in schema.get(key, {}).items():
            errors.extend(_numeric_unit_errors(child, root, f"{path}.{key}.{name}", seen))
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            errors.extend(_numeric_unit_errors(schema[key], root, f"{path}.{key}", seen))
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for i, child in enumerate(schema.get(key, [])):
            errors.extend(_numeric_unit_errors(child, root, f"{path}.{key}[{i}]", seen))
    return errors


def _has_explicit_type(schema: Any, root: dict[str, Any], seen: frozenset[str] = frozenset()) -> bool:
    """Require a bounded value type, including local refs and nullable unions."""
    if not isinstance(schema, dict):
        return False
    if schema.get("type"):
        return True
    reference = schema.get("$ref")
    if reference and reference not in seen:
        if _has_explicit_type(_resolve_ref(reference, root), root, seen | {reference}):
            return True
    for key in ("anyOf", "oneOf"):
        choices = schema.get(key)
        if choices and all(_has_explicit_type(child, root, seen) for child in choices):
            return True
    return any(_has_explicit_type(child, root, seen) for child in schema.get("allOf", []))


def _safe_artifact_path(root: Path, raw: str) -> Path:
    if "\x00" in raw or "\\" in raw or PurePosixPath(raw).is_absolute() or re.match(r"^[A-Za-z]:", raw):
        raise ContractError("artifact.path 必须是相对 POSIX 路径")
    resolved = (root / raw).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ContractError("artifact.path 逃出 asset-root，包括符号链接") from exc
    return resolved


def validate_contract(contract: Any, asset_root: Path | None = None, check_files: bool = False,
                      schema: dict[str, Any] | None = None) -> list[str]:
    """Return static errors without importing or running any asset code."""
    schema = schema if schema is not None else load_json(SCHEMA_PATH)
    errors = lint_schema(schema)
    if errors:
        return errors
    errors = validate_value(contract, schema)
    if errors:
        return errors
    components = contract["components"]
    capabilities = contract["capabilities"]
    slots = contract["state_slots"]
    artifacts = contract["artifacts"]
    sources = contract["sources"]
    models = contract["model_bindings"]
    coordinates = contract["coordinate_system"]
    spatial_fields = ("handedness", "up_axis", "frame")
    if coordinates["status"] == "not_applicable":
        if any(coordinates[field] is not None for field in spatial_fields):
            errors.append("$.coordinate_system: not_applicable 时手性、上轴与参考系必须为 null")
        if models or contract["ports"] or any(c["geometry"] for c in components.values()):
            errors.append("$.coordinate_system: 有几何、模型绑定或空间端口时不能为 not_applicable")
    elif any(coordinates[field] is None for field in spatial_fields):
        errors.append("$.coordinate_system: defined/assumed 必须完整声明手性、上轴和参考系")

    def ref(identifier: str | None, objects: dict[str, Any], path: str) -> None:
        if identifier is not None and identifier not in objects:
            errors.append(f"{path}: 悬空引用 {identifier!r}")

    def refs(identifiers: list[str], objects: dict[str, Any], path: str) -> None:
        for i, identifier in enumerate(identifiers):
            ref(identifier, objects, f"{path}[{i}]")

    def available(identifier: str | None, path: str) -> None:
        ref(identifier, artifacts, path)
        if identifier in artifacts and artifacts[identifier]["status"] != "available":
            errors.append(f"{path}: 实现或证据引用的 artifact 必须 available")

    def property_sources(properties: dict[str, Any], path: str) -> None:
        for key, prop in properties.items():
            refs(prop["source_ids"], sources, f"{path}.{key}.source_ids")
            if prop["evidence"] in {"documented", "measured", "photo_estimate"} and not prop["source_ids"]:
                errors.append(f"{path}.{key}: 有依据的物性必须填写 source_ids")

    for cid, component in components.items():
        p = f"$.components.{cid}"
        ref(component["parent"], components, p + ".parent")
        refs(component["capability_ids"], capabilities, p + ".capability_ids")
        for capid in component["capability_ids"]:
            if capid in capabilities and cid not in capabilities[capid]["component_ids"]:
                errors.append(f"{p}.capability_ids: 能力与组件引用必须双向一致: {capid}")
        if not component["capability_ids"] and not component["passive_reason"]:
            errors.append(f"{p}.passive_reason: 无能力的被动组件必须说明原因")
        if component["geometry_status"] == "bound" and not component["geometry"]:
            errors.append(f"{p}.geometry: bound 组件必须有几何绑定")
        if component["geometry_status"] == "absent" and component["geometry"]:
            errors.append(f"{p}.geometry: absent 组件不能有几何绑定")
        for i, geometry in enumerate(component["geometry"]):
            ref(geometry["artifact_id"], artifacts, f"{p}.geometry[{i}].artifact_id")
            if component["geometry_status"] == "bound":
                available(geometry["artifact_id"], f"{p}.geometry[{i}].artifact_id")
        property_sources(component["properties"], p + ".properties")
    finished: set[str] = set()
    for cid in components:
        chain: set[str] = set()
        current: str | None = cid
        while current in components and current not in finished:
            if current in chain:
                errors.append(f"$.components.{cid}.parent: 组件父级存在循环")
                break
            chain.add(current)
            current = components[current]["parent"]
        finished.update(chain)

    for sid, slot in slots.items():
        p = f"$.state_slots.{sid}"
        refs(slot["component_ids"], components, p + ".component_ids")
        refs(slot["source_ids"], sources, p + ".source_ids")
        value_errors = lint_schema(slot["value_schema"], path=p + ".value_schema")
        errors.extend(value_errors)
        if not value_errors and not _has_explicit_type(slot["value_schema"], slot["value_schema"]):
            errors.append(f"{p}.value_schema: 必须明确值类型，不能使用空 schema")
        if slot["value_schema"].get("x-unit") and slot["value_schema"]["x-unit"] != slot["unit"]:
            errors.append(f"{p}.value_schema.x-unit: 必须与状态外层 unit 一致")
        if slot["origin"] == "process_truth" and slot["visibility"] == "agent":
            errors.append(f"{p}.visibility: process_truth 不得作为 agent 默认可见状态")
        if slot["access"] == "internal" and slot["visibility"] == "agent":
            errors.append(f"{p}.visibility: internal 状态不得默认暴露给 agent")
        if slot["read_binding"]:
            available(slot["read_binding"]["artifact_id"], p + ".read_binding.artifact_id")
        write = slot["write_via"]
        if slot["access"] == "read_write" and write is None:
            errors.append(f"{p}.write_via: read_write 必须指定能力及参数映射")
        if slot["access"] != "read_write" and write is not None:
            errors.append(f"{p}.write_via: 只有 read_write 状态可以声明写入映射")
        if write:
            ref(write["capability_id"], capabilities, p + ".write_via.capability_id")
            cap = capabilities.get(write["capability_id"])
            if cap:
                if slot["visibility"] == "agent" and cap["visibility"] != "agent":
                    errors.append(f"{p}.write_via: agent 可写状态不得映射到 debug/evaluator 能力")
                input_schema = cap["input_schema"]
                if not lint_schema(input_schema):
                    expanded_input = _expanded(input_schema, input_schema)
                    props = expanded_input.get("properties", {})
                    argument = write["argument"]
                    if argument not in props:
                        errors.append(f"{p}.write_via.argument: 能力没有该输入参数 {argument!r}")
                    else:
                        arg_schema = _expanded(props[argument], input_schema)
                        if slot["unit"] != arg_schema.get("x-unit", "1"):
                            errors.append(f"{p}.write_via.argument: 状态与写入参数单位不一致")
                    if argument in write["fixed_arguments"]:
                        errors.append(f"{p}.write_via.fixed_arguments: 不得覆盖动态写入参数")
                    for name, value in write["fixed_arguments"].items():
                        if name not in props:
                            errors.append(f"{p}.write_via.fixed_arguments.{name}: 不存在的参数")
                        else:
                            errors.extend(validate_value(value, props[name], input_schema,
                                                         f"{p}.write_via.fixed_arguments.{name}"))
                    missing = set(expanded_input.get("required", [])) - {argument} - set(write["fixed_arguments"])
                    if missing:
                        errors.append(f"{p}.write_via: 缺少必填固定参数 {sorted(missing)}")

    for capid, cap in capabilities.items():
        p = f"$.capabilities.{capid}"
        refs(cap["component_ids"], components, p + ".component_ids")
        for cid in cap["component_ids"]:
            if cid in components and capid not in components[cid]["capability_ids"]:
                errors.append(f"{p}.component_ids: 能力与组件引用必须双向一致: {cid}")
        refs(cap["source_ids"], sources, p + ".source_ids")
        refs(cap["observes"], slots, p + ".observes")
        if cap["visibility"] == "agent":
            for sid in cap["observes"]:
                if sid in slots and slots[sid]["visibility"] != "agent":
                    errors.append(f"{p}.observes: agent 能力不得观察 debug/evaluator 状态 {sid}")
        refs(cap["model_effects"]["binding_ids"], models, p + ".model_effects.binding_ids")
        refs(cap["execution"]["completion_state_ids"], slots, p + ".execution.completion_state_ids")
        for i, precondition in enumerate(cap["preconditions"]):
            refs(precondition["state_ids"], slots, f"{p}.preconditions[{i}].state_ids")
        for i, effect in enumerate(cap["state_effects"]):
            ref(effect["state_id"], slots, f"{p}.state_effects[{i}].state_id")
        for field in ("input_schema", "output_schema"):
            schema_errors = lint_schema(cap[field], path=f"{p}.{field}")
            errors.extend(schema_errors)
            if not schema_errors and not _has_explicit_type(cap[field], cap[field]):
                errors.append(f"{p}.{field}: 必须明确值类型，不能使用空 schema")
            if field == "input_schema" and not schema_errors:
                input_schema = _expanded(cap[field], cap[field])
                if input_schema.get("type") != "object" or input_schema.get("additionalProperties") is not False:
                    errors.append(f"{p}.input_schema: 必须是 additionalProperties=false 的 object")
                errors.extend(_numeric_unit_errors(cap[field], cap[field], f"{p}.{field}"))
                errors.extend(validate_value(cap["example_arguments"], cap[field], path=f"{p}.example_arguments"))
        binding = cap["binding"]
        if binding:
            ref(binding["artifact_id"], artifacts, p + ".binding.artifact_id")
        active_layers = {layer for layer, status in cap["implementation"].items() if status in {"implemented", "verified"}}
        if cap["availability"] == "implemented":
            if not binding:
                errors.append(f"{p}.binding: implemented 能力必须有 backend binding")
            else:
                available(binding["artifact_id"], p + ".binding.artifact_id")
                if binding["backend"] != "composite" and binding["backend"] not in active_layers:
                    errors.append(f"{p}.implementation: binding.backend 对应层未实现")
            if not active_layers:
                errors.append(f"{p}.implementation: implemented 能力至少有一个已实现层")
        elif active_layers:
            errors.append(f"{p}.implementation: declared/unsupported 能力不能声明已实现或 verified 层")
        if cap["availability"] == "unsupported" and binding is not None:
            errors.append(f"{p}.binding: unsupported 能力不能声明可调用绑定")
        model_effects = cap["model_effects"]
        if model_effects["mode"] == "none" and model_effects["binding_ids"]:
            errors.append(f"{p}.model_effects: mode=none 时 binding_ids 必须为空")
        if model_effects["mode"] in {"direct", "coupled"} and not model_effects["binding_ids"]:
            errors.append(f"{p}.model_effects: direct/coupled 必须列出模型绑定")
        if cap["availability"] == "implemented" and model_effects["mode"] in {"direct", "coupled"} and "scene" not in active_layers:
            errors.append(f"{p}.model_effects: 声明已实现的模型变化需要 scene 已实现层")
        if cap["availability"] == "implemented" and model_effects["mode"] == "coupled":
            for mid in model_effects["binding_ids"]:
                if mid in models and models[mid]["sync_status"] not in {"implemented", "verified"}:
                    errors.append(f"{p}.model_effects: coupled 使用了未实现同步的模型绑定 {mid}")
        seen_verifications: set[str] = set()
        for i, verification in enumerate(cap["verification"]):
            vp = f"{p}.verification[{i}]"
            if verification["id"] in seen_verifications:
                errors.append(f"{vp}.id: 同一能力内验证 ID 重复")
            seen_verifications.add(verification["id"])
            ref(verification["evidence_artifact_id"], artifacts, vp + ".evidence_artifact_id")
            if verification["status"] == "passed":
                if not verification["evidence_artifact_id"]:
                    errors.append(f"{vp}: passed 验证必须有可定位证据 artifact")
                else:
                    available(verification["evidence_artifact_id"], vp + ".evidence_artifact_id")
                if not verification["environment"] or not verification["checked_at"]:
                    errors.append(f"{vp}: passed 验证必须记录环境与检查时间")
                if verification["layer"] != "contract" and cap["availability"] != "implemented":
                    errors.append(f"{vp}: declared/unsupported 不能伪装运行层验证通过")
        for layer, status in cap["implementation"].items():
            if status == "verified" and not any(v["layer"] == layer and v["status"] == "passed" and
                                                v["evidence_artifact_id"] in artifacts and
                                                artifacts[v["evidence_artifact_id"]]["status"] == "available"
                                                for v in cap["verification"]):
                errors.append(f"{p}.implementation.{layer}: verified 缺少同层 passed 验证与证据")

    authority = {"none": "none", "process_to_scene": "process", "scene_to_process": "scene", "host_to_both": "host"}
    for mid, model in models.items():
        p = f"$.model_bindings.{mid}"
        ref(model["component_id"], components, p + ".component_id")
        ref(model["artifact_id"], artifacts, p + ".artifact_id")
        ref(model["state_id"], slots, p + ".state_id")
        refs(model["source_ids"], sources, p + ".source_ids")
        if model["limits"] and model["limits"][0] > model["limits"][1]:
            errors.append(f"{p}.limits: 下限不能大于上限")
        if model["authority"] != authority[model["direction"]]:
            errors.append(f"{p}.authority: 与同步 direction 不一致")
        if model["sync_status"] == "unbound" and model["direction"] != "none":
            errors.append(f"{p}.direction: unbound 同步必须为 none")
        if model["sync_status"] != "unbound" and (model["direction"] == "none" or model["state_id"] is None):
            errors.append(f"{p}: 声明或实现同步必须给出 direction 及 state_id")
        if model["sync_status"] in {"implemented", "verified"}:
            available(model["artifact_id"], p + ".artifact_id")
        if model["sync_status"] == "verified":
            referencing = [c for c in capabilities.values() if mid in c["model_effects"]["binding_ids"]]
            if not any(v["layer"] == "scene" and v["status"] == "passed" and
                       v["evidence_artifact_id"] in artifacts and
                       artifacts[v["evidence_artifact_id"]]["status"] == "available"
                       for c in referencing for v in c["verification"]):
                errors.append(f"{p}.sync_status: verified 模型同步缺少引用它的能力的 scene 验证证据")
    for pid, port in contract["ports"].items():
        ref(port["component_id"], components, f"$.ports.{pid}.component_id")
    for mid, mechanism in contract["mechanisms"].items():
        p = f"$.mechanisms.{mid}"
        refs(mechanism["component_ids"], components, p + ".component_ids")
        refs(mechanism["state_ids"], slots, p + ".state_ids")
        refs(mechanism["source_ids"], sources, p + ".source_ids")
        property_sources(mechanism["parameters"], p + ".parameters")

    if check_files:
        if asset_root is None:
            errors.append("--check-files 需要 asset-root")
        else:
            for aid, artifact in artifacts.items():
                p = f"$.artifacts.{aid}.path"
                try:
                    file = _safe_artifact_path(Path(asset_root), artifact["path"])
                    if artifact["status"] == "available":
                        if not file.is_file():
                            errors.append(f"{p}: available 文件不存在或不是普通文件: {artifact['path']}")
                        elif artifact["sha256"]:
                            digest = hashlib.sha256()
                            with file.open("rb") as stream:
                                for block in iter(lambda: stream.read(1024 * 1024), b""):
                                    digest.update(block)
                            if digest.hexdigest() != artifact["sha256"]:
                                errors.append(f"{p}: sha256 不匹配")
                except (ContractError, OSError, RuntimeError) as exc:
                    errors.append(f"{p}: {exc}")
    return errors


def describe_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted discovery metadata. No sample/default/live state values."""
    return {
        "schema_version": contract["schema_version"], "asset_type_id": contract["asset_type_id"],
        "asset_version": contract["asset_version"], "name_zh": contract["name_zh"],
        "category": contract["category"], "asset_kind": contract["asset_kind"], "coverage": contract["coverage"],
        "component_count": len(contract["components"]),
        "capabilities": {cid: {key: cap[key] for key in ("function_zh", "kind", "availability", "visibility", "implementation")}
                         for cid, cap in contract["capabilities"].items()},
        "state_slots": {sid: {key: slot[key] for key in ("unit", "origin", "access", "visibility")}
                        for sid, slot in contract["state_slots"].items()},
        "static_only": True,
    }


def build_index(root: Path, output: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: dict[str, str] = {}
    for path in sorted(root.rglob("*.contract.json")):
        if output is not None and path.resolve() == output.resolve():
            continue
        relative = path.relative_to(root).as_posix()
        try:
            if not path.resolve().is_relative_to(root):
                errors.append(f"{relative}: 合同符号链接逃出扫描根目录")
                continue
            contract = load_json(path)
            local_errors = validate_contract(contract)
            if local_errors:
                errors.extend(f"{relative}: {error}" for error in local_errors)
                continue
            identifier = contract["asset_type_id"]
            if identifier in seen:
                errors.append(f"重复 asset_type_id {identifier}: {seen[identifier]} 与 {relative}")
            else:
                seen[identifier] = relative
            description = describe_contract(contract)
            rows.append({"contract_path": relative, **description})
        except ContractError as exc:
            errors.append(f"{relative}: {exc}")
    return {"index_schema_version": "1.0.0", "static_only": True,
            "asset_count": len(rows), "valid": not errors, "assets": rows,
            "errors": errors, "validation_scope": STATIC_LIMITATION}


def _cell(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\r", "").replace("\n", "<br>")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines)


def render_table(contract: dict[str, Any]) -> str:
    """Produce review tables, never execute the displayed invocations."""
    rows: list[list[str]] = []
    for cid, cap in contract["capabilities"].items():
        invocation = (f'lab.invoke(instance_id="<实例ID>", capability_id={cid!r}, '
                      f'arguments={cap["example_arguments"]!r}, request_id="<本次唯一请求ID>")')
        binding = cap["binding"]
        real = (f'{binding["invocation"]}\nartifact={binding["artifact_id"]}; entrypoint={binding["entrypoint"]}'
                if binding else "未绑定，没有可执行后端入口")
        if cap["availability"] != "implemented":
            real = f"{cap['availability']}，不可作为已实现 API 调用\n{real}"
        components = "; ".join(f"{comp}: {contract['components'][comp]['name_zh']}" for comp in cap["component_ids"]) or "无实体组件"
        effects = "; ".join(f"{effect['state_id']}: {effect['description_zh']} [{effect['timing']}]" for effect in cap["state_effects"]) or "不声明状态修改"
        if cap["observes"]:
            effects += "\n读取: " + ", ".join(cap["observes"])
        model = cap["model_effects"]
        model_text = f"{model['mode']}: {model['description_zh']}"
        for mid in model["binding_ids"]:
            mb = contract["model_bindings"][mid]
            separator = "" if mb["attribute"].startswith("[") else "."
            model_text += f"\n{mid}: {mb['target']}{separator}{mb['attribute']}; {mb['direction']}; {mb['sync_status']}"
        preconditions = "; ".join(f"{pre['description_zh']} [执行方: {pre['enforced_by']}; 检查: {pre['check']}]" for pre in cap["preconditions"]) or "没有声明前提"
        execution = cap["execution"]
        completion = f"{execution['completion_criterion']}\n观测: {', '.join(execution['completion_state_ids']) or '无'}; {execution['mode']}/{execution['time_domain']}"
        implemented = f"可用性: {cap['availability']}; 可见性: {cap['visibility']}\n" + "; ".join(f"{layer}={status}" for layer, status in cap["implementation"].items())
        verification = "; ".join(f"{v['id']}: {v['layer']}/{v['status']}; 证据={v['evidence_artifact_id'] or '无'}; {v['evidence_note']}" for v in cap["verification"]) or "未提供验证记录"
        rows.append([cap["function_zh"], invocation, real, components, effects, model_text, preconditions,
                     completion, implemented, verification])
    component_rows: list[list[str]] = []
    for cid, component in contract["components"].items():
        geometry = "; ".join(f"{g['artifact_id']} → {g['selector']} [{g['role']}]" for g in component["geometry"]) or "无几何绑定"
        component_rows.append([cid, component["name_zh"], component["function_zh"], component["physical_meaning_zh"],
                               component["parent"] or "根组件", component["geometry_status"] + ": " + geometry,
                               ", ".join(component["capability_ids"]) or "无独立能力", component["passive_reason"] or "不适用"])
    intro = (f"{contract['name_zh']} API 与模型变化检查表，资产类型 `{contract['asset_type_id']}`，"
             f"版本 `{contract['asset_version']}`，覆盖范围 `{contract['coverage']}`。\n\n"
             "`lab.invoke` 是统一规范入口的设计签名，本工具不提供运行 SDK。真实入口见后端调用列；"
             "表内的实现和验证状态来自合同声明。" + STATIC_LIMITATION + "\n\n")
    return intro + _table(["功能", "lab.invoke 调用", "真实 backend invocation", "组件", "状态变化", "3D 变化",
                          "前提", "完成反馈", "实现状态", "验证情况"], rows) + "\n\n" + (
        f"全部 {len(component_rows)} 个组件及其能力对应关系如下。\n\n") + _table(
        ["组件 ID", "名称", "功能", "物理意义", "父组件", "物体模型", "能力 ID", "被动原因"], component_rows) + "\n"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="静态校验合同及可选本地文件")
    validate.add_argument("contract", type=Path)
    validate.add_argument("--asset-root", type=Path)
    validate.add_argument("--check-files", action="store_true")
    table = commands.add_parser("table", help="生成中文 API 与组件 Markdown 表")
    table.add_argument("contract", type=Path)
    table.add_argument("--output", type=Path, required=True)
    describe = commands.add_parser("describe", help="只读输出静态发现元数据")
    describe.add_argument("contract", type=Path)
    index = commands.add_parser("index", help="扫描 *.contract.json 并生成静态索引")
    index.add_argument("root", type=Path)
    index.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "index":
            if not args.root.is_dir():
                raise ContractError(f"索引根目录不存在: {args.root}")
            result = build_index(args.root, args.output)
            _write(args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"valid": result["valid"], "asset_count": result["asset_count"],
                              "output": str(args.output), "errors": result["errors"]}, ensure_ascii=False, indent=2))
            return 0 if result["valid"] else 1
        contract = load_json(args.contract)
        errors = validate_contract(contract,
                                   asset_root=getattr(args, "asset_root", None) or args.contract.parent,
                                   check_files=getattr(args, "check_files", False))
        if args.command == "validate" or errors:
            print(json.dumps({"valid": not errors, "contract": str(args.contract), "errors": errors,
                              "checked_files": getattr(args, "check_files", False),
                              "validation_scope": STATIC_LIMITATION}, ensure_ascii=False, indent=2))
            return 1 if errors else 0
        if args.command == "describe":
            print(json.dumps(describe_contract(contract), ensure_ascii=False, indent=2))
        elif args.command == "table":
            _write(args.output, render_table(contract))
            print(json.dumps({"output": str(args.output), "capability_count": len(contract["capabilities"]),
                              "component_count": len(contract["components"])}, ensure_ascii=False))
        return 0
    except (ContractError, OSError, RecursionError, OverflowError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
