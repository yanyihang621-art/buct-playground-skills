"""Read the field names from a Harbor case's output contract, in declaration order."""

from typing import Any, Dict, List


def _field_names(entries: Any) -> List[str]:
    if isinstance(entries, dict):
        return list(entries)
    if not isinstance(entries, list):
        return []
    names = []
    for entry in entries:
        if isinstance(entry, dict):
            entry = entry.get("name") or entry.get("field") or entry.get("key")
        if isinstance(entry, str) and entry.strip():
            names.append(entry)
    return names


def contract_fields(case: Dict[str, Any], required_only: bool = False) -> List[str]:
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        return list(dict.fromkeys(_field_names(contract)))

    required = []
    has_required_list = False
    for key in ("required", "required_fields"):
        if isinstance(contract.get(key), list):
            has_required_list = True
            required.extend(_field_names(contract[key]))
    if required_only and has_required_list:
        return list(dict.fromkeys(required))

    if isinstance(contract.get("properties"), dict):
        declared = list(contract["properties"])
    elif isinstance(contract.get("fields"), (dict, list)):
        declared = _field_names(contract["fields"])
    elif has_required_list or contract.get("type") == "object" or "$schema" in contract:
        declared = []
    else:
        declared = list(contract)
    return list(dict.fromkeys(declared + required))


def _declared_shapes(case):
    contract = case.get("output_contract", {})
    if isinstance(contract, dict):
        container = contract.get("properties", contract.get("fields", contract))
    else:
        container = contract
    if isinstance(container, dict):
        shapes = dict(container)
    else:
        shapes = {}
        for spec in container if isinstance(container, list) else []:
            if isinstance(spec, dict):
                name = spec.get("name") or spec.get("field") or spec.get("key")
                if name:
                    shapes[name] = spec
    return shapes


def output_field_type(case, key):
    shape = _declared_shapes(case).get(key)
    return shape.get("type") if isinstance(shape, dict) else shape


def strict_fields(case):
    contract = case.get("output_contract")
    if isinstance(contract, dict) and contract.get("additionalProperties") is False and "properties" in contract:
        return set(contract_fields(case)) | {"case_id"}
    return None


def _shape_errors(path, value, spec):
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict):
        return
    types = spec.get("type", [])
    if isinstance(types, str):
        types = [types]
    checks = {"null": value is None, "boolean": isinstance(value, bool),
              "integer": type(value) is int, "number": type(value) in (int, float),
              "string": isinstance(value, str), "object": isinstance(value, dict),
              "array": isinstance(value, list)}
    recognized = [t for t in types if isinstance(t, str) and t in checks] if isinstance(types, list) else []
    if recognized and not any(checks[t] for t in recognized):
        yield f"{path}: expected {' or '.join(recognized)}."
        return
    if isinstance(spec.get("enum"), list) and value not in spec["enum"]:
        yield f"{path}: outside the declared enumeration."
    if isinstance(value, list):
        if len(value) < spec.get("minItems", 0):
            yield f"{path}: requires at least {spec['minItems']} items."
        for i, item in enumerate(value):
            yield from _shape_errors(f"{path}[{i}]", item, spec.get("items"))
    elif isinstance(value, dict):
        for name in spec.get("required", []):
            if name not in value:
                yield f"{path}.{name}: required field missing."
        for name, subspec in spec.get("properties", {}).items():
            if name in value:
                yield from _shape_errors(f"{path}.{name}", value[name], subspec)
    elif isinstance(value, str) and len(value) < spec.get("minLength", 0):
        yield f"{path}: requires at least {spec['minLength']} characters."


def contract_issues(case, result):
    shapes = _declared_shapes(case)
    required = set(contract_fields(case, required_only=True))
    for key in contract_fields(case):
        value = result.get(key)
        spec = shapes.get(key)
        types = spec.get("type") if isinstance(spec, dict) else spec
        nullable = types == "null" or isinstance(types, list) and "null" in types
        if value is None:
            if key in required and (key not in result or not nullable):
                yield {"code": "contract_field_unfilled", "field": key,
                       "message": f"Required field {key!r} needs a value or a documented source limitation."}
            continue
        for msg in _shape_errors(key, value, spec):
            yield {"code": "contract_field_invalid", "field": key, "message": msg}


def record_unresolved_fields(case, result, catalog, provided):
    """Keep an incomplete checkpoint usable until the agent supplies case-specific evidence."""
    pending = [key for key in contract_fields(case) if key not in catalog and key not in provided
               and result.get(key) is None]
    if not pending:
        return
    required = set(contract_fields(case, required_only=True))
    if result.get("status") == "success" and required.intersection(pending):
        result["status"] = "partial"
    for key in pending:
        result["uncertainties"].append({
            "code": "awaiting_task_synthesis", "severity": "info", "pages": [],
            "field": key, "message": f"Field '{key}' needs case-specific evidence.",
        })
