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
    if required_only and isinstance(contract.get("properties"), dict):
        return []

    if isinstance(contract.get("properties"), dict):
        declared = list(contract["properties"])
    elif isinstance(contract.get("fields"), (dict, list)):
        declared = _field_names(contract["fields"])
    elif has_required_list or contract.get("type") == "object" or "$schema" in contract:
        declared = []
    else:
        declared = list(contract)
    return list(dict.fromkeys(declared + required))


def record_unresolved_fields(case, result, catalog, provided):
    """Keep an incomplete checkpoint usable until the agent supplies case-specific evidence."""
    pending = [key for key in contract_fields(case) if key in result and key not in catalog and key not in provided
               and result.get(key) is None]
    if not pending:
        return
    required = set(contract_fields(case, required_only=True))
    if result.get("status") == "success" and required.intersection(pending):
        result["status"] = "partial"
    for key in pending:
        if not isinstance(result.get("uncertainties"), list):
            continue
        result["uncertainties"].append({
            "code": "awaiting_task_synthesis", "severity": "info", "pages": [],
            "field": key, "message": f"Field '{key}' needs case-specific evidence.",
        })


def schema_for(case):
    spec = case.get("output_contract")
    if not isinstance(spec, dict) or not isinstance(spec.get("properties"), dict):
        return None
    return spec


def project_contract(case, result):
    schema = schema_for(case) or {}
    if schema.get("additionalProperties") is False:
        return {k: v for k, v in result.items() if k == "case_id" or k in schema["properties"]}
    return result


def contract_errors(case, result):
    schema = schema_for(case)
    if schema is None:
        return []
    try:
        from jsonschema.validators import validator_for
    except ImportError:
        def walk(value, node, path):
            if not isinstance(node, dict):
                return
            types = {"string": str, "integer": int, "number": (float, int), "boolean": bool,
                     "object": dict, "array": list, "null": type(None)}
            kinds = node.get("type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if kinds and not any(k in types and isinstance(value, types[k]) and not
                                 (k in ("integer", "number") and isinstance(value, bool)) for k in kinds):
                yield f"{path}: expected {kinds}"
                return
            if "enum" in node and value not in node["enum"]:
                yield f"{path}: outside the allowed values"
            if isinstance(value, dict):
                props = node.get("properties", {})
                for k in node.get("required", []):
                    if k not in value:
                        yield f"{path}: missing required {k}"
                for k, v in value.items():
                    if k in props:
                        yield from walk(v, props[k], f"{path}.{k}")
                    elif node.get("additionalProperties") is False:
                        yield f"{path}: unexpected property {k}"
            if isinstance(value, list):
                for i, child in enumerate(value):
                    yield from walk(child, node.get("items"), f"{path}[{i}]")
        return list(walk(result, schema, "$"))
    validator = validator_for(schema)(schema)
    return [f"{error.json_path}: {error.message[:280]}" for error in validator.iter_errors(result)]
