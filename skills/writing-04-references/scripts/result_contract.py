"""Apply the current case's field definitions to actual pipeline outputs and reviewed content."""
from __future__ import annotations

TYPES = {"string": str, "array": list, "object": dict, "number": (int, float),
         "integer": int, "boolean": bool, "null": type(None)}


def fields_for(contract):
    fields = {}
    if isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields[item] = {}
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if isinstance(name, str):
                    fields[name] = item
                else:
                    fields.update(fields_for(item))
    elif isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            fields.update(contract["properties"])
        for name in ("required", "required_fields", "fields"):
            if isinstance(contract.get(name), (list, dict)):
                for key, value in fields_for(contract[name]).items():
                    fields.setdefault(key, value)
        for key, value in contract.items():
            if key not in {"type", "required", "fields", "required_fields", "properties", "$schema", "$id",
                           "title", "description", "additionalProperties", "definitions", "$defs"}:
                fields.setdefault(key, value)
    return fields


def type_errors(value, schema, path):
    if isinstance(schema, str):
        schema = {"type": schema} if schema in TYPES else {}
    if not isinstance(schema, dict):
        return []
    kind = schema.get("type")
    kinds = [kind] if isinstance(kind, str) else kind or []
    if kinds and not any(k in TYPES and isinstance(value, TYPES[k]) and
                         (k not in ("integer", "number") or type(value) is not bool) for k in kinds):
        return [path + " type"]
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(path + " enum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(path + "." + key + " missing")
        for key, prop in schema.get("properties", {}).items():
            if key in value:
                errors.extend(type_errors(value[key], prop, path + "." + key))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            errors.extend(type_errors(v, schema.get("items"), f"{path}[{i}]"))
    return errors


def apply_contract(result, contract, produced, reviewed):
    fields = fields_for(contract)
    missing, invalid = [], []
    for key, schema in fields.items():
        if key not in result:
            if key in reviewed and reviewed[key] is not None:
                result[key] = reviewed[key]
            elif key in produced:
                result[key] = produced[key]
            else:
                result[key] = None
                missing.append(key)
        invalid.extend(type_errors(result[key], schema, key))
    if missing or invalid:
        result["uncertainties"].append({"code": "contract_needs_material", "missing": missing, "invalid": invalid})
        if result["status"] == "completed":
            result["status"] = "partial"
    return result
