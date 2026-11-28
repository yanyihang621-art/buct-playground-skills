"""Check named fields, build contract skeletons, and validate declared shapes."""

from __future__ import annotations

from typing import Any, Optional


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


def _resolve_ref(spec: Any, root: Any, seen: Optional[set[str]] = None) -> Any:
    if not isinstance(spec, dict) or "$ref" not in spec or not isinstance(root, dict):
        return spec
    ref = spec["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return spec
    seen = set(seen or set())
    if ref in seen:
        return spec
    seen.add(ref)
    cur: Any = root
    for comp in ref[2:].split("/"):
        key = comp.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return spec
    if isinstance(cur, dict):
        merged = dict(cur)
        for k, v in spec.items():
            if k != "$ref":
                merged[k] = v
        return _resolve_ref(merged, root, seen)
    return cur


def contract_specs(contract: Any) -> dict:
    if not isinstance(contract, dict):
        return _field_map(contract)
    if "properties" in contract or any(k in contract for k in ("fields", "required_fields", "required")):
        specs = {}
        for key in ("required", "required_fields", "fields", "properties"):
            specs.update(_field_map(contract.get(key)))
        return specs
    return {
        k: v for k, v in contract.items()
        if k not in ("$schema", "$id", "$defs", "definitions", "type", "description", "title", "additionalProperties")
    }


def required_contract_fields(contract: Any) -> set[str]:
    specs = contract_specs(contract)
    explicit = set()
    if isinstance(contract, dict):
        explicit.update(_field_map(contract.get("required")))
        explicit.update(_field_map(contract.get("required_fields")))
    if isinstance(contract, dict) and isinstance(contract.get("properties"), dict):
        return explicit
    return explicit | {
        name for name, spec in specs.items()
        if not isinstance(spec, dict) or (spec.get("required") is not False and spec.get("optional") is not True)
    }


def is_closed_contract(contract: Any) -> bool:
    return (
        isinstance(contract, dict)
        and contract.get("additionalProperties") is False
        and isinstance(contract.get("properties"), dict)
        and "patternProperties" not in contract
    )


def _shape_errors(value: Any, spec: Any, path: str, root: Any = None, depth: int = 0) -> list[str]:
    if depth > 20:
        return []
    if spec is False:
        return [f"{path}: schema disallows this value"]
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict):
        return []
    spec = _resolve_ref(spec, root)
    if not isinstance(spec, dict):
        return []
    if value is None and spec.get("nullable") is True:
        return []
    for label in ("oneOf", "anyOf", "allOf"):
        branches = spec.get(label)
        if isinstance(branches, list):
            matches = sum(not _shape_errors(value, branch, path, root=root, depth=depth + 1) for branch in branches)
            if ((label == "oneOf" and matches != 1) or (label == "anyOf" and not matches)
                    or (label == "allOf" and matches != len(branches))):
                return [f"{path}: does not meet {label}"]
    names = spec.get("type", [])
    names = [names] if isinstance(names, str) else names
    types = {
        "string": str, "object": dict, "array": list,
        "number": (float, int), "integer": int, "boolean": bool, "null": type(None),
    }
    allowed = [t for t in (names or []) if t in types]
    if allowed and not any(
        isinstance(value, types[t]) and not (t in ("number", "integer") and isinstance(value, bool))
        for t in allowed
    ):
        return [f"{path}: expected {' or '.join(allowed)}"]
    if "enum" in spec and value not in spec["enum"]:
        return [f"{path}: outside declared enum ({value!r})"]
    if "const" in spec and value != spec["const"]:
        return [f"{path}: differs from declared constant"]
    errors = []
    if isinstance(value, dict):
        for k in _field_map(spec.get("required")):
            if k not in value:
                errors.append(f"{path}.{k}: missing")
        properties = spec.get("properties", {})
        if spec.get("additionalProperties") is False and isinstance(properties, dict) and "patternProperties" not in spec:
            errors.extend(f"{path}.{k}: undeclared field" for k in sorted(value.keys() - properties.keys()))
        for k, child in properties.items():
            if k in value:
                errors.extend(_shape_errors(value[k], child, f"{path}.{k}", root=root, depth=depth + 1))
    elif isinstance(value, list):
        if len(value) < spec.get("minItems", 0):
            errors.append(f"{path}: requires at least {spec['minItems']} items")
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            errors.append(f"{path}: exceeds {spec['maxItems']} items")
        for i, item in enumerate(value):
            errors.extend(_shape_errors(item, spec.get("items"), f"{path}[{i}]", root=root, depth=depth + 1))
    return errors


def field_conforms(spec: Any, value: Any, contract: Any = None) -> bool:
    """Return True if value conforms to the declared field schema spec."""
    return len(_shape_errors(value, spec, "", root=contract)) == 0


def allows_null(spec: Any, root: Any = None) -> bool:
    if spec == "null":
        return True
    return (
        isinstance(spec, dict)
        and any(k in spec for k in ("type", "nullable", "enum", "const", "oneOf", "anyOf", "$ref"))
        and not _shape_errors(None, spec, "", root=root)
    )


def contract_issues(contract: Any, result: dict) -> list[dict]:
    """Validate the presence and declared shape of each field, plus root additionalProperties."""
    required = required_contract_fields(contract)
    issues = []
    specs = contract_specs(contract)
    missing = {
        issue.get("field") for issue in result.get("uncertainties", [])
        if isinstance(issue, dict) and issue.get("code") == "missing_contract_field"
    }
    if is_closed_contract(contract):
        allowed_keys = set(contract["properties"].keys())
        extra = sorted(k for k in result.keys() if k not in allowed_keys)
        for k in extra:
            issues.append({
                "code": "contract_shape_mismatch",
                "field": k,
                "message": f"{k}: undeclared top-level field under closed additionalProperties:false",
            })
    for name, spec in specs.items():
        if name not in result or (result[name] is None and name in missing):
            if name in required:
                issues.append({"code": "missing_contract_field", "field": name, "message": f"{name}: missing required field"})
            continue
        errors = _shape_errors(result[name], spec, name, root=contract)
        if name in required and result[name] is None and not errors:
            if not allows_null(spec, root=contract):
                errors.append(f"{name}: missing required value")
        if errors:
            issues.append({"code": "contract_shape_mismatch", "field": name, "message": "; ".join(errors)})
    return issues


def pick_status_value(contract: Any, intent: str) -> str:
    """Pick a status string matching contract enum for success|partial|unable."""
    prefs = {
        "success": ("success", "completed", "complete", "ok", "done", "ready"),
        "partial": ("partial", "partially_completed", "partial_success", "incomplete"),
        "unable": ("unable", "refused", "unverifiable", "failed", "error", "partial", "partially_completed"),
    }.get(intent, ("partial",))
    specs = contract_specs(contract)
    st_spec = _resolve_ref(specs.get("status"), contract)
    enum_vals = st_spec.get("enum") if isinstance(st_spec, dict) else None
    if isinstance(enum_vals, list) and enum_vals:
        lower_map = {str(v).lower(): v for v in enum_vals}
        for cand in prefs:
            if cand in lower_map:
                return str(lower_map[cand])
        return str(enum_vals[0])
    return prefs[0]


def _default_for_spec(spec: Any, depth: int = 0, root: Any = None) -> Any:
    if depth > 6:
        return None
    if isinstance(spec, str):
        return {"string": "", "array": [], "object": {}, "boolean": False}.get(spec)
    if not isinstance(spec, dict):
        return None
    spec = _resolve_ref(spec, root)
    if not isinstance(spec, dict):
        return None
    if "const" in spec:
        return spec["const"]
    if "default" in spec:
        return spec["default"]
    if "enum" in spec and isinstance(spec["enum"], list) and spec["enum"]:
        return spec["enum"][0]
    t = spec.get("type")
    if isinstance(t, list):
        for cand in ("string", "array", "object", "boolean", "number", "integer", "null"):
            if cand in t:
                t = cand
                break
    if t == "string":
        return ""
    if t == "array":
        return []
    if t == "boolean":
        return False
    if t in ("number", "integer"):
        return None
    if t == "object" or isinstance(spec.get("properties"), dict):
        obj = {}
        props = spec.get("properties", {})
        if isinstance(props, dict):
            for k, v in props.items():
                obj[k] = _default_for_spec(v, depth + 1, root=root)
        return obj
    if isinstance(spec.get("anyOf"), list) and spec["anyOf"]:
        return _default_for_spec(spec["anyOf"][0], depth + 1, root=root)
    if isinstance(spec.get("oneOf"), list) and spec["oneOf"]:
        return _default_for_spec(spec["oneOf"][0], depth + 1, root=root)
    return None


def build_skeleton(contract: Any, case_id: str, status_intent: str = "partial") -> dict:
    """Build an initial contract-compliant result skeleton."""
    specs = contract_specs(contract)
    res: dict[str, Any] = {}
    for name, spec in specs.items():
        res[name] = _default_for_spec(spec, root=contract)
    if "case_id" in specs or not is_closed_contract(contract):
        res["case_id"] = case_id
    if "status" in specs or not is_closed_contract(contract):
        res["status"] = pick_status_value(contract, status_intent)
    if "uncertainties" in specs and not isinstance(res.get("uncertainties"), list):
        res["uncertainties"] = []
    return res


def format_contract_tree(contract: Any, max_len: int = 5000) -> str:
    """Render a complete human/agent-readable contract summary up to depth 5."""
    if not contract:
        return "(no output_contract declared; open result.json)"
    specs = contract_specs(contract)
    req = required_contract_fields(contract)
    closed = is_closed_contract(contract)

    def desc(spec: Any, depth: int = 0) -> str:
        if not isinstance(spec, dict):
            return str(spec or "any")
        spec = _resolve_ref(spec, contract)
        if not isinstance(spec, dict):
            return str(spec or "any")
        note = ""
        if isinstance(spec.get("description"), str) and spec["description"].strip():
            d_txt = " ".join(spec["description"].split())
            if len(d_txt) > 90:
                d_txt = d_txt[:87] + "..."
            note = f" # {d_txt}"
        if "enum" in spec:
            return f"enum{spec['enum']}{note}"
        t = spec.get("type", "any")
        if t == "object" or isinstance(spec.get("properties"), dict):
            props = spec.get("properties", {})
            if depth >= 5 or not isinstance(props, dict) or not props:
                return f"object{note}"
            nested_req = set(_field_map(spec.get("required")).keys())
            nested_closed = " [closed]" if spec.get("additionalProperties") is False else ""
            inner = ", ".join(
                f"{k}{'*' if k in nested_req else ''}: {desc(v, depth + 1)}"
                for k, v in props.items()
            )
            return "{" + inner + "}" + nested_closed + note
        if t == "array":
            return f"[{desc(spec.get('items'), depth + 1)}]{note}"
        return f"{t}{note}"

    lines = [f"mode: {'CLOSED (additionalProperties:false)' if closed else 'OPEN'}"]
    for name, spec in specs.items():
        star = "*" if name in req else ""
        lines.append(f"  - {name}{star}: {desc(spec)}")
    out = "\n".join(lines)
    return out[:max_len] + ("\n  ...(truncated)" if len(out) > max_len else "")
