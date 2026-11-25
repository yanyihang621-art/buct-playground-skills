"""Validate explicit delivery paths and the case's JSON Schema using local resources."""
from __future__ import annotations

import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote

from tex_utils import sha256_file


def pointer(parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts) if parts else ""


def is_schema(value):
    return isinstance(value, bool) or isinstance(value, dict) and (
        any(k in value for k in ("$schema", "$ref", "$defs", "definitions", "properties", "additionalProperties",
                                  "patternProperties", "allOf", "oneOf", "anyOf"))
        or isinstance(value.get("required"), list) or value.get("type") == "object")


def local_reference(root, ref):
    if not isinstance(ref, str) or not ref.startswith("#"):
        raise ValueError("only references inside the case schema are supported")
    fragment = unquote(ref[1:])
    node = root
    if fragment and not fragment.startswith("/"):
        def search(value):
            if isinstance(value, dict):
                if value.get("$anchor") == fragment or value.get("$dynamicAnchor") == fragment:
                    return value
                for x in value.values():
                    found = search(x)
                    if found is not None:
                        return found
            if isinstance(value, list):
                for x in value:
                    found = search(x)
                    if found is not None:
                        return found
        node = search(root)
        if node is None:
            raise ValueError("unknown local schema anchor " + ref)
        return node
    for part in fragment.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[int(part)] if isinstance(node, list) else node[part]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ValueError("unresolved local schema reference " + ref) from exc
    return node


def constraints(root, spec, seen=()):
    """Return active sibling/object constraints for inspection and projection."""
    if isinstance(spec, dict) and id(spec) not in seen:
        seen = (*seen, id(spec))
        ref = spec.get("$ref")
        if isinstance(ref, str) and ref.startswith("#"):
            try:
                target = local_reference(root, ref)
            except ValueError:
                target = None
            yield from constraints(root, target, seen)
        yield spec
        for part in spec.get("allOf", []):
            yield from constraints(root, part, seen)


def child_specs(nodes, key):
    for node in nodes:
        if isinstance(key, int):
            sequence = node.get("prefixItems", [])
            items = node.get("items", {})
            if isinstance(items, list):  # draft-07 tuple syntax
                sequence, items = items, node.get("additionalItems", {})
            yield sequence[key] if key < len(sequence) else items
        else:
            props = node.get("properties", {})
            if key in props:
                yield props[key]
            matched = False
            for pattern, schema in node.get("patternProperties", {}).items():
                if re.search(pattern, key):
                    matched = True
                    yield schema
            if not matched and key not in props and isinstance(node.get("additionalProperties"), dict):
                yield node["additionalProperties"]


def project_contract_result(result, schema):
    if not is_schema(schema):
        return result

    def project(value, specs, top=False):
        nodes = [n for spec in specs for n in constraints(schema, spec)]
        if isinstance(value, dict):
            closed = [n for n in nodes if n.get("additionalProperties") is False]
            out = {}
            for key, child in value.items():
                if any(key not in n.get("properties", {}) and not any(
                        re.search(p, key) for p in n.get("patternProperties", {})) for n in closed):
                    if not (top and key == "case_id"):
                        continue
                out[key] = project(child, child_specs(nodes, key))
            return out
        if isinstance(value, list):
            return [project(item, child_specs(nodes, i)) for i, item in enumerate(value)]
        return value

    return project(result, [schema], top=True)


def _references(spec):
    if not isinstance(spec, dict):
        return
    for key in ("$ref", "$dynamicRef", "$recursiveRef"):
        if key in spec:
            yield spec[key]
    for key in ("properties", "patternProperties", "definitions", "$defs", "dependentSchemas", "dependencies"):
        for child in spec.get(key, {}).values():
            yield from _references(child)
    for key in ("items", "prefixItems", "allOf", "anyOf", "oneOf", "additionalProperties",
                "additionalItems", "unevaluatedProperties", "unevaluatedItems", "contains",
                "propertyNames", "if", "then", "else", "not", "contentSchema"):
        value = spec.get(key)
        for child in value if isinstance(value, list) else (value,):
            yield from _references(child)


def schema_errors(value, schema):
    try:
        for ref in _references(schema):
            local_reference(schema, ref)
    except (ValueError, TypeError, AttributeError) as exc:
        return ["[schema] " + str(exc)]
    try:
        from jsonschema import validators
        from referencing import Registry
    except ImportError:
        return fallback_errors(value, schema, schema)
    try:
        cls = validators.validator_for(schema) if not isinstance(schema, dict) or "$schema" not in schema else validators.validator_for(schema, default=None)
        if cls is None:
            return ["[schema] unsupported declared schema dialect"]
        cls.check_schema(schema)
        # An explicitly empty registry cannot retrieve external schemas over the network.
        validator = cls(schema, registry=Registry())
        return [f"{pointer(e.absolute_path) or '/'}: {e.message}" for e in validator.iter_errors(value)]
    except Exception as exc:
        return ["[schema] validation unavailable: " + str(exc)]


TYPES = {"object": dict, "array": list, "string": str, "integer": int,
         "number": (int, float), "boolean": bool, "null": type(None)}
ANNOTATIONS = {"$id", "$schema", "$defs", "definitions", "$comment", "$anchor",
               "title", "description", "default", "examples", "readOnly", "writeOnly", "deprecated"}


def fallback_errors(value, spec, root, path=(), seen=()):
    """Check common schemas without installed libraries. Unimplemented assertions are explicit."""
    where = pointer(path) or "/"
    if spec is False:
        return [f"{where}: rejected by false schema"]
    if spec is True or not isinstance(spec, dict):
        return []
    mark = (id(spec), path)
    if mark in seen:
        return [f"{where}: recursive schema cannot advance"]
    seen = (*seen, mark)
    errors = []
    recognized = ANNOTATIONS | {"type", "$ref", "const", "enum", "required", "properties", "items",
                                "additionalProperties", "minItems", "maxItems", "minLength", "maxLength", "pattern",
                                "minimum", "maximum", "allOf", "anyOf", "oneOf"}
    for key in sorted(spec.keys() - recognized):
        if not key.startswith("x-") and key != "format":
            errors.append(f"{where}: install jsonschema to validate {key}")
    if "$ref" in spec:
        errors += fallback_errors(value, local_reference(root, spec["$ref"]), root, path, seen)
    if "type" in spec:
        types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        valid = any(t in TYPES and isinstance(value, TYPES[t]) and
                    not (isinstance(value, bool) and t in ("integer", "number")) for t in types)
        if not valid and not ("integer" in types and isinstance(value, float) and value.is_integer()):
            return errors + [f"{where}: expected {types}"]
    if ("const" in spec and value != spec["const"]) or ("enum" in spec and value not in spec["enum"]):
        errors.append(f"{where}: outside declared value(s)")
    if isinstance(value, dict):
        props = spec.get("properties", {})
        for k in spec.get("required", []):
            if k not in value:
                errors.append(f"{pointer((*path, k))}: missing required property")
        for k, child in value.items():
            child_schema = props.get(k, spec.get("additionalProperties", True))
            errors += fallback_errors(child, child_schema, root, (*path, k), seen)
    if isinstance(value, list):
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", len(value)):
            errors.append(f"{where}: array length outside declared bounds")
        for i, child in enumerate(value):
            errors += fallback_errors(child, spec.get("items", True), root, (*path, i), seen)
    if isinstance(value, str):
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", len(value)):
            errors.append(f"{where}: string length outside declared bounds")
        if "pattern" in spec and not re.search(spec["pattern"], value):
            errors.append(f"{where}: string does not match declared pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < spec.get("minimum", value) or value > spec.get("maximum", value):
            errors.append(f"{where}: number outside declared bounds")
    for op in ("allOf", "anyOf", "oneOf"):
        if op in spec:
            branches = [fallback_errors(value, branch, root, path, seen) for branch in spec[op]]
            successes = sum(not e for e in branches)
            if op == "allOf":
                for issues in branches:
                    errors.extend(issues)
            elif not successes or op == "oneOf" and successes != 1:
                errors.append(f"{where}: {successes} matching {op} branches")
    return errors


def delivery_path(root, name):
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("expected a nonempty relative delivery path")
    name = name.replace("\\", "/")
    rel = PurePosixPath(name)
    if (not rel.parts or rel.is_absolute() or ".." in rel.parts or PureWindowsPath(name).drive
            or any(":" in p for p in rel.parts)):
        raise ValueError("path must stay inside the delivery directory")
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0)
                                  & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValueError("delivery path passes through a link")
    if not path.resolve().is_relative_to(root):
        raise ValueError("path escapes the delivery directory")
    return path


def publish_exports(output_dir, report):
    root = Path(output_dir).resolve()
    exports = report.get("artifact_exports", {})
    if not isinstance(exports, dict):
        return ["/artifact_exports: expected a map of output name to existing delivery path"]
    issues = []
    for name, source in exports.items():
        label = pointer(("artifact_exports", name))
        try:
            dest, src = delivery_path(root, name), delivery_path(root, source)
            first = dest.relative_to(root).parts[0]
            if first in {"result.json", ".previous"} or dest.relative_to(root).as_posix() == "evidence/delivery_record.json":
                raise ValueError("destination is reserved for the delivery record")
            if not src.is_file() and not src.is_dir():
                raise ValueError(f"source {source!r} is unavailable")
            if dest == src:
                continue
            if dest.is_relative_to(src) or src.is_relative_to(dest):
                raise ValueError("source and destination overlap")
            if dest.exists():
                if src.is_file() and dest.is_file() and sha256_file(src) == sha256_file(dest):
                    continue
                raise ValueError("destination already exists; refusing to replace it")
            if src.is_dir():
                for member in src.rglob("*"):
                    delivery_path(root, member.relative_to(root).as_posix())
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".export-") as temp:
                copy = Path(temp) / "new"
                if src.is_dir():
                    shutil.copytree(src, copy)
                else:
                    shutil.copy2(src, copy)
                copy.replace(dest)
        except (OSError, ValueError, RuntimeError) as exc:
            issues.append(f"{label}: {exc}")
    return issues


EVIDENCE_CONTAINERS = {"source_mapping", "sources", "evidence", "provenance", "references", "facts", "change_records",
                       "citations", "task_checks", "uncertainties", "inputs", "materials", "originals"}
FILE_FIELDS = {"main_tex", "editable_source", "output_pdf", "pdf", "pdf_path", "manuscript_tex",
               "preview_paths", "previews", "patch_diff", "build_repro_sh", "build_manifest_json"}
DELIVERY_CONTAINERS = {"artifacts", "deliverables", "outputs", "main_result", "submission"}


def artifact_path_errors(result, root, fields, schema):
    def walk(value, path, specs):
        nodes = [n for s in specs for n in constraints(schema, s)]
        if isinstance(value, dict):
            for key, child in value.items():
                yield from walk(child, (*path, key), child_specs(nodes, key))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                yield from walk(child, (*path, i), child_specs(nodes, i))
        elif isinstance(value, str):
            keys = [k.lower() for k in path if isinstance(k, str)]
            key = keys[-1]
            if any(k in EVIDENCE_CONTAINERS for k in keys) or any(n.get("contentEncoding") for n in nodes):
                return
            context = any(k in DELIVERY_CONTAINERS for k in keys)
            formats = {n.get("format") for n in nodes}
            named_file = bool(re.search(r"\.[A-Za-z0-9]{1,8}$", value.strip()))
            prescribed_name = (any(n.get("const") == value for n in nodes) and named_file and
                               key in {"manuscript", "register", "report", "appendix", "figure", "archive", "preview"})
            is_dir = key.endswith(("_dir", "_directory")) or "directory-path" in formats
            is_file = (key in FILE_FIELDS or "file-path" in formats or key.endswith(("_file", "_files"))
                       or key.endswith(("_path", "_paths")) and (context or named_file)
                       or context and (prescribed_name or key in {"path", "file", "filename"} or
                                       named_file and key.endswith(("_pdf", "_tex", "_json", "_zip", "_png", "_sh"))))
            if not (is_file or is_dir) or key.startswith(("input_", "original_", "reference_", "material_")):
                return
            if key.startswith("source_") and not context and "file-path" not in formats:
                return
            try:
                p = delivery_path(root, value)
                if not (p.is_dir() if is_dir else p.is_file()):
                    raise ValueError(f"declared {'directory' if is_dir else 'file'} {value!r} is missing")
            except (ValueError, OSError, RuntimeError) as exc:
                yield f"{pointer(path)}: {exc}"

    return [error for key in fields if key in result for error in walk(result[key], (key,), [fields[key]])]
