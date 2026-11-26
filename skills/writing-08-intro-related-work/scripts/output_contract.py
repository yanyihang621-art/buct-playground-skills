"""Resolve the case's result fields from real products and the editor's case-specific values."""

from __future__ import annotations

from typing import Any


def _field_map(fields: Any) -> dict:
    if isinstance(fields, dict):
        return dict(fields)
    specs = {}
    for item in fields if isinstance(fields, list) else []:
        if isinstance(item, str):
            specs[item] = {}
        elif isinstance(item, dict):
            name = item.get("name") or item.get("field") or item.get("key")
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
    return {k: v for k, v in contract.items() if k not in ("$schema", "$id", "description", "title", "additionalProperties")}


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
        if isinstance(spec.get("minItems"), int) and len(value) < spec["minItems"]:
            errors.append(f"{path}: needs at least {spec['minItems']} items")
        if isinstance(spec.get("maxItems"), int) and len(value) > spec["maxItems"]:
            errors.append(f"{path}: permits at most {spec['maxItems']} items")
        if "items" in spec:
            for i, item in enumerate(value):
                errors.extend(_shape_errors(item, spec["items"], f"{path}[{i}]"))
    elif isinstance(value, str):
        if isinstance(spec.get("minLength"), int) and len(value) < spec["minLength"]:
            errors.append(f"{path}: shorter than {spec['minLength']} characters")
        if isinstance(spec.get("maxLength"), int) and len(value) > spec["maxLength"]:
            errors.append(f"{path}: longer than {spec['maxLength']} characters")
    return errors


def _project_rows(field: str, rows: Any, spec: Any) -> Any:
    """Represent verified editorial records in the narrower shape requested by the case."""
    if not isinstance(rows, list) or not isinstance(spec, dict):
        return rows
    item = spec.get("items")
    if not isinstance(item, dict) or not all(isinstance(row, dict) for row in rows):
        return rows
    if item.get("type") == "string":
        key = {"sources": "path", "contributions": "claim", "research_gaps": "gap_statement",
               "change_records": "action", "claim_evidence": "claim"}.get(field)
        if key and all(isinstance(row.get(key), str) for row in rows):
            return [row[key] for row in rows]
    if item.get("additionalProperties") is False and isinstance(item.get("properties"), dict):
        return [{k: v for k, v in row.items() if k in item["properties"]} for row in rows]
    return rows


def resolve_contract(result: dict, contract: Any, products: dict, supplied: dict) -> list[dict]:
    """Resolve required and supplied fields; preserve genuinely nullable values, omit absent optionals."""
    specs = contract_specs(contract)
    required = required_contract_fields(contract)
    issues = []
    fixed = {"case_id", "status", "metrics", "verification", "artifacts", "uncertainties",
             "schema_version", "challenge_id", "manuscript", "claim_evidence", "research_gaps",
             "contributions", "sources", "change_records", "draft_mode"}
    for field, spec in specs.items():
        if field in supplied and field not in fixed:
            result[field] = supplied[field]
        elif field not in result and field in products:
            result[field] = products[field]
        if field not in result:
            if field not in required:
                continue
            result[field] = None
            issues.append({"code": "unresolved_contract_field", "field": field,
                           "message": "Supply an evidence-based value in analysis.contract_fields or document the missing source."})
        else:
            if _shape_errors(result[field], spec, field):
                result[field] = _project_rows(field, result[field], spec)
            if errors := _shape_errors(result[field], spec, field):
                issues.append({"code": "contract_shape_mismatch", "field": field, "details": errors})
    return issues
