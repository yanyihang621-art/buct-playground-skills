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


def _shape_errors(value: Any, spec: Any, path: str) -> list[str]:
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict):
        return []
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
    errors = []
    if isinstance(value, dict):
        for k in spec.get("required", []):
            if k not in value:
                errors.append(f"{path}.{k}: missing")
        for k, child in spec.get("properties", {}).items():
            if k in value:
                errors.extend(_shape_errors(value[k], child, f"{path}.{k}"))
    elif isinstance(value, list) and "items" in spec:
        for i, item in enumerate(value):
            errors.extend(_shape_errors(item, spec["items"], f"{path}[{i}]"))
    return errors


def resolve_contract(result: dict, contract: Any, products: dict, supplied: dict) -> list[dict]:
    """Fill named original fields at the top level. Never count null placeholders as completed work."""
    specs = contract_specs(contract)
    issues = []
    fixed = {"case_id", "status", "metrics", "verification", "artifacts", "uncertainties",
             "schema_version", "challenge_id", "manuscript", "claim_evidence", "research_gaps",
             "contributions", "sources", "change_records", "draft_mode"}
    for field, spec in specs.items():
        if field in supplied and field not in fixed:
            result[field] = supplied[field]
        elif field not in result:
            result[field] = products.get(field)
        if result[field] is None:
            issues.append({"code": "unresolved_contract_field", "field": field,
                           "message": "Supply an evidence-based value in analysis.contract_fields or document the missing source."})
        elif errors := _shape_errors(result[field], spec, field):
            issues.append({"code": "contract_shape_mismatch", "field": field, "details": errors})
    return issues
