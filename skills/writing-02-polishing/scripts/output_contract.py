"""Align case-specific fields with real artifacts, sentences and observations from this run."""

from __future__ import annotations

import re
from typing import Any


_SPEC_KEYS = {"$schema", "$defs", "$id", "type", "additionalProperties", "title", "description",
              "properties", "required", "fields", "required_fields", "required_keys", "output_fields", "result_fields"}
_FIELD_KEYS = ("fields", "required_fields", "required_keys", "output_fields", "result_fields")
_MISSING = object()
_FIXED = {"case_id", "status", "verification", "metrics", "change_records", "uncertainties", "artifacts",
          "schema_version", "challenge_id", "sources", "source_map", "polished_text", "glossary", "summary", "register"}


def contract_fields(contract: Any) -> dict:
    """Return field names and shapes from a field map, descriptor list or object schema."""
    if isinstance(contract, str):
        return {contract: {}} if contract else {}
    if isinstance(contract, list):
        fields = {}
        for entry in contract:
            if isinstance(entry, str):
                fields.setdefault(entry, {})
            elif isinstance(entry, dict):
                name = next((entry[k] for k in ("name", "key", "field", "field_name")
                             if isinstance(entry.get(k), str)), None)
                if name:
                    fields[name] = entry
                else:
                    fields.update(contract_fields(entry))
        return fields
    if not isinstance(contract, dict):
        return {}
    if isinstance(contract.get("properties"), dict):
        fields = dict(contract["properties"])
    else:
        fields = {k: v for k, v in contract.items() if k not in _SPEC_KEYS and not k.startswith("$")}
        for key in _FIELD_KEYS:
            if isinstance(contract.get(key), (list, dict)):
                fields.update(contract_fields(contract[key]))
    if isinstance(contract.get("required"), list):
        for key, spec in contract_fields(contract["required"]).items():
            fields.setdefault(key, contract[key] if key in contract and key != "required" else spec)
    return fields


_TYPES = {
    "null": type(None), "string": str, "array": list, "object": dict,
    "number": (float, int), "integer": int, "boolean": bool,
}
_ALIASES = {
    "before": "source_text", "original": "source_text", "original_text": "source_text",
    "source_sentence": "source_text", "after": "polished_text", "revision": "polished_text",
    "revised": "polished_text", "edited_text": "polished_text", "revised_text": "polished_text",
    "revised_sentence": "polished_text", "reason": "rationale", "explanation": "rationale",
    "category": "edit_type", "change_type": "edit_type", "sentence_id": "sent_id",
    "locator": "source_location", "location": "source_location", "preferred": "canonical",
    "preferred_term": "canonical", "term": "canonical", "revisions": "change_records",
    "deliverables": "artifacts", "main_result": "polished_text",
    "evidence_map": "source_map", "source_locations": "source_map",
}


def normalized_key(key: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower().replace("-", "_").strip()


def _spec(value):
    if isinstance(value, str) and value in _TYPES:
        return {"type": value}
    return value if isinstance(value, dict) else {}


def _types(spec):
    names = spec.get("type", [])
    return [names] if isinstance(names, str) else names if isinstance(names, list) else []


def _type_errors(value, spec, path):
    spec = _spec(spec)
    names = [n for n in _types(spec) if isinstance(n, str) and n in _TYPES]
    if names and not any(isinstance(value, _TYPES[n]) and
                         not (n in ("number", "integer") and isinstance(value, bool)) for n in names):
        yield f"{path} must be {' or '.join(names)}"
        return
    if "const" in spec and value != spec["const"]:
        yield f"{path} differs from the required constant"
    if "enum" in spec and value not in spec["enum"]:
        yield f"{path} is outside its declared choices"
    if isinstance(value, str) and len(value) < spec.get("minLength", 0):
        yield f"{path} is shorter than minLength"
    if isinstance(value, dict):
        properties = spec.get("properties", {})
        for key in spec.get("required", []) if isinstance(spec.get("required"), list) else []:
            if isinstance(key, str) and key not in value:
                yield f"{path}.{key} is required"
        for key, item in value.items():
            if key in properties:
                yield from _type_errors(item, properties[key], f"{path}.{key}")
            elif spec.get("additionalProperties") is False:
                yield f"{path}.{key} is outside the closed schema"
            elif isinstance(spec.get("additionalProperties"), dict):
                yield from _type_errors(item, spec["additionalProperties"], f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < spec.get("minItems", 0):
            yield f"{path} requires at least {spec['minItems']} entries"
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            yield f"{path} exceeds maxItems"
        for i, item in enumerate(value):
            yield from _type_errors(item, spec.get("items"), f"{path}[{i}]")


def _lookup(evidence, key):
    norm = normalized_key(key)
    for name in (key, norm, _ALIASES.get(norm)):
        if name in evidence:
            return evidence[name]
    locator = evidence.get("source_location")
    if isinstance(locator, dict) and norm in {"source", "source_line", "page", "paragraph"}:
        return locator.get(norm, _MISSING)
    return _MISSING


def _shape(value, key, spec):
    """Project observed members into the requested shape without synthesizing field values."""
    spec = _spec(spec)
    names = _types(spec)
    if "array" in names and "object" not in names and isinstance(value, dict):
        if key in ("glossary", "terminology") and isinstance(value.get("entries"), list):
            value = value["entries"]
        elif key in ("artifacts", "deliverables") and all(isinstance(v, str) for v in value.values()):
            if _spec(spec.get("items")).get("type") == "string":
                value = list(value.values())
            else:
                value = [{"role": k, "path": v} for k, v in value.items()]
    if isinstance(value, list) and "items" in spec:
        return [_shape(v, key, spec["items"]) for v in value]
    if isinstance(value, dict) and isinstance(spec.get("properties"), dict):
        shaped = {} if spec.get("additionalProperties") is False else dict(value)
        for child_key, child_spec in spec["properties"].items():
            actual = _lookup(value, child_key)
            if actual is not _MISSING:
                shaped[child_key] = _shape(actual, normalized_key(child_key), child_spec)
        return shaped
    return value


def _resolve(key, spec, facts, catalog):
    norm = normalized_key(key)
    first = _MISSING
    candidates = [_lookup(facts, key), _lookup(catalog, key)]
    if norm == "sources" and _spec(spec.get("items")).get("type") == "object":
        candidates.append(facts.get("source_map", _MISSING))
    properties = spec.get("properties")
    if isinstance(properties, dict):
        wrapper = {k: _lookup(facts, k) for k in properties}
        present = {k: v for k, v in wrapper.items() if v is not _MISSING}
        if present:
            candidates.append(present)
    for candidate in candidates:
        if candidate is _MISSING:
            continue
        value = _shape(candidate, norm, spec)
        if first is _MISSING:
            first = value
        if not next(_type_errors(value, spec, key), None):
            return value
    return first


def contract_view(result: dict, contract: Any) -> dict:
    """Serialize fixed audit fields into their case-specific shapes after builder updates."""
    view = dict(result)
    for key, spec in contract_fields(contract).items():
        if key in _FIXED and key in result:
            view[key] = _resolve(key, _spec(spec), result, {})
    return view


def fill_contract(result: dict, contract: Any, supplied: dict, catalog: dict) -> list:
    """Fill declared fields with case-derived values; report missing/invalid fields for correction."""
    issues = []
    facts = {**catalog, **result}
    for key, raw_spec in contract_fields(contract).items():
        spec = _spec(raw_spec)
        if key in supplied and key not in _FIXED:
            # The editor supplies unknown scientific content; show shape errors for repair.
            value = supplied[key]
            result[key] = value
        else:
            value = _resolve(key, spec, facts, catalog)
            if key not in _FIXED or key not in result:
                result[key] = None if value is _MISSING else value
        if value is _MISSING or value is None:
            explicit_null = "null" in _types(spec) and ((value is None and key in supplied) or _types(spec) == ["null"])
            if not explicit_null:
                issues.append({"code": "missing_contract_value", "field": key,
                               "message": "Supply an evidence-based case value in contract_values."})
        else:
            for message in _type_errors(value, spec, key):
                issues.append({"code": "contract_shape", "field": key, "message": message})
    return issues
