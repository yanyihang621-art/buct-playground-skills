"""Interpret case-specific result fields consistently during graph assembly and independent validation."""

from __future__ import annotations

from typing import Any


def _field_map(fields: Any) -> dict:
    if isinstance(fields, str):
        return {fields: {}} if fields else {}
    if isinstance(fields, dict):
        return dict(fields)
    specs = {}
    for item in fields if isinstance(fields, list) else []:
        if isinstance(item, str):
            specs[item] = {}
        elif isinstance(item, dict):
            name = item.get("name") or item.get("field") or item.get("key") or item.get("field_name")
            if isinstance(name, str):
                specs[name] = item
    return specs


def contract_specs(contract: Any) -> dict:
    if not isinstance(contract, dict):
        return _field_map(contract)
    if "properties" in contract or any(k in contract for k in ("fields", "required_fields", "required")):
        specs = {}
        for key in ("required", "required_fields", "fields", "properties"):
            specs.update(_field_map(contract.get(key)))
        return specs
    return {k: v for k, v in contract.items() if k not in ("$schema", "$id", "$defs", "type", "description", "title", "additionalProperties")}


def required_contract_fields(contract: Any) -> set[str]:
    specs = contract_specs(contract)
    explicit = set()
    if isinstance(contract, dict):
        explicit.update(_field_map(contract.get("required")))
        explicit.update(_field_map(contract.get("required_fields")))
    if isinstance(contract, dict) and isinstance(contract.get("properties"), dict):
        return explicit
    return explicit | {name for name, spec in specs.items() if not isinstance(spec, dict)
                       or (spec.get("required") is not False and spec.get("optional") is not True)}


def _shape_errors(value: Any, spec: Any, path: str) -> list[str]:
    if spec is False:
        return [f"{path}: schema disallows this value"]
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict):
        return []
    if value is None and spec.get("nullable") is True:
        return []
    for label in ("oneOf", "anyOf", "allOf"):
        branches = spec.get(label)
        if isinstance(branches, list):
            matches = sum(not _shape_errors(value, branch, path) for branch in branches)
            if ((label == "oneOf" and matches != 1) or (label == "anyOf" and not matches)
                    or (label == "allOf" and matches != len(branches))):
                return [f"{path}: does not meet {label}"]
    names = spec.get("type", [])
    names = [names] if isinstance(names, str) else names
    types = {"string": str, "object": dict, "array": list, "number": (float, int),
             "integer": int, "boolean": bool, "null": type(None)}
    allowed = [t for t in (names or []) if t in types]
    if allowed and not any(isinstance(value, types[t]) and not
                           (t in ("number", "integer") and isinstance(value, bool)) for t in allowed):
        return [f"{path}: expected {' or '.join(allowed)}"]
    if "enum" in spec and value not in spec["enum"]:
        return [f"{path}: outside declared enum"]
    if "const" in spec and value != spec["const"]:
        return [f"{path}: differs from declared constant"]
    errors = []
    if isinstance(value, dict):
        for k in _field_map(spec.get("required")):
            if k not in value:
                errors.append(f"{path}.{k}: missing")
        properties = spec.get("properties", {})
        if spec.get("additionalProperties") is False and isinstance(properties, dict) and "patternProperties" not in spec:
            errors.extend(f"{path}.{k}: undeclared field" for k in value.keys() - properties.keys())
        for k, child in properties.items():
            if k in value:
                errors.extend(_shape_errors(value[k], child, f"{path}.{k}"))
    elif isinstance(value, list):
        if len(value) < spec.get("minItems", 0):
            errors.append(f"{path}: requires at least {spec['minItems']} items")
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            errors.append(f"{path}: exceeds {spec['maxItems']} items")
        for i, item in enumerate(value):
            errors.extend(_shape_errors(item, spec.get("items"), f"{path}[{i}]"))
    return errors


def allows_null(spec):
    if spec == "null":
        return True
    return isinstance(spec, dict) and any(k in spec for k in ("type", "nullable", "enum", "const", "oneOf", "anyOf")) and not _shape_errors(None, spec, "")


def contract_issues(contract: Any, result: dict) -> list[dict]:
    """Validate the presence and the declared shape of each case field."""
    required = required_contract_fields(contract)
    issues = []
    missing = {issue.get("field") for issue in result.get("uncertainties", []) if isinstance(issue, dict)
               and issue.get("code") == "missing_contract_field"}
    for name, spec in contract_specs(contract).items():
        if name not in result or (result[name] is None and name in missing):
            if name in required:
                issues.append({"code": "missing_contract_field", "field": name, "message": f"{name}: missing required field"})
            continue
        errors = _shape_errors(result[name], spec, name)
        # Bare field lists still require a real value. A typed nullable field may legitimately be null.
        if name in required and result[name] is None and not errors:
            if not allows_null(spec):
                errors.append(f"{name}: missing required value")
        if errors:
            issues.append({"code": "contract_shape_mismatch", "field": name, "message": "; ".join(errors)})
    return issues
