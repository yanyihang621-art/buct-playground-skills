"""Resolve case-specific result fields using actual products of this run."""

from __future__ import annotations

import re
from typing import Any


def contract_fields(contract: Any) -> dict:
    """Support lists of keys/descriptors, field maps and JSON Schema object contracts."""
    if isinstance(contract, str):
        return {contract: {}} if contract else {}
    if isinstance(contract, list):
        fields = {}
        for entry in contract:
            if isinstance(entry, str):
                fields[entry] = {}
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("key") or entry.get("field") or entry.get("field_name")
                if isinstance(name, str):
                    fields[name] = entry
                else:
                    fields.update(contract_fields(entry))
        return fields
    if not isinstance(contract, dict):
        return {}
    if isinstance(contract.get("properties"), dict):
        fields = dict(contract["properties"])
        for key in contract.get("required", []):
            fields.setdefault(key, {})
        return fields
    for key in ("fields", "required_fields", "required_keys", "output_fields", "result_fields"):
        if isinstance(contract.get(key), (list, dict)):
            return contract_fields(contract[key])
    if isinstance(contract.get("required"), list):
        return contract_fields(contract["required"])
    return {k: v for k, v in contract.items() if k not in {
        "$schema", "$defs", "$id", "additionalProperties", "title", "description", "type"
    }}


_TYPES = {
    "null": type(None), "string": str, "array": list, "object": dict,
    "number": (float, int), "integer": int, "boolean": bool,
}


def _matches(value, name):
    return isinstance(value, _TYPES[name]) and not (name in ("number", "integer") and isinstance(value, bool))


def _type_errors(value: Any, spec: Any, path: str):
    if isinstance(spec, str) and spec in _TYPES:
        spec = {"type": spec}
    if not isinstance(spec, dict):
        return
    typ = spec.get("type")
    names = [typ] if isinstance(typ, str) else typ if isinstance(typ, list) else []
    known = [name for name in names if isinstance(name, str) and name in _TYPES]
    if known and not any(_matches(value, t) for t in known):
        yield f"{path} must be {' or '.join(known)}"
        return
    if isinstance(spec.get("enum"), list) and value not in spec["enum"]:
        yield f"{path} must be one of {spec['enum']}"
    if isinstance(value, dict):
        for key in spec.get("required", []) if isinstance(spec.get("required"), list) else []:
            if key not in value:
                yield f"{path}.{key} is required"
        for key, sub in spec.get("properties", {}).items():
            if key in value:
                yield from _type_errors(value[key], sub, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < spec.get("minItems", 0):
            yield f"{path} requires at least {spec['minItems']} entries"
        for i, item in enumerate(value):
            yield from _type_errors(item, spec.get("items"), f"{path}[{i}]")


def normalized_key(key: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower().replace("-", "_").strip()


def fill_contract(result: dict, contract: Any, supplied: dict, catalog: dict) -> list:
    """Populate exact names; report missing content and incompatible declared shapes."""
    issues = []
    fixed = {"case_id", "status", "verification", "metrics", "change_records", "uncertainties", "artifacts",
             "schema_version", "challenge_id", "sources", "source_map", "polished_text", "glossary", "summary", "register"}
    for key, spec in contract_fields(contract).items():
        if key in supplied and key not in fixed:
            result[key] = supplied[key]
        elif key not in result:
            result[key] = catalog.get(normalized_key(key))
        if result[key] is None:
            types = spec.get("type") if isinstance(spec, dict) else None
            if types != "null" and not (isinstance(types, list) and "null" in types and key in supplied):
                issues.append({"code": "missing_contract_value", "field": key,
                               "message": "Read the case requirements and supply an evidence-based value in contract_values."})
        else:
            for message in _type_errors(result[key], spec, key):
                issues.append({"code": "contract_shape", "field": key, "message": message})
    return issues
