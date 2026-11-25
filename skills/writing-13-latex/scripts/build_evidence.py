"""Portable evidence for a real case. All deliverable paths are output-relative."""

from __future__ import annotations

import difflib
import shutil
from pathlib import Path

from contract_delivery import (artifact_path_errors, constraints, is_schema, pointer,
                               project_contract_result, publish_exports, schema_errors)
from tex_utils import sha256_file, write_text_lf


BUILD_SUFFIXES = {".aux", ".bcf", ".blg", ".fls", ".fdb_latexmk", ".toc", ".lof",
                  ".lot", ".nav", ".snm", ".vrb", ".xdv"}
SOURCE_SUFFIXES = {".tex", ".ltx", ".bib", ".cls", ".sty", ".bst", ".cfg", ".clo",
                   ".def", ".bbx", ".cbx", ".bbl", ".txt", ".md", ".csv", ".tsv",
                   ".yaml", ".yml", ".json", ".lua", ".mplstyle"}


def copy_sources(source, target, *, skip_case=False, omit=()):
    """Preserve the editable tree including figures, local styles and fonts."""
    omitted = {p.as_posix() for p in omit}
    for src in sorted(source.rglob("*")):
        rel = src.relative_to(source)
        if (src.is_symlink() or not src.is_file() or rel.as_posix() in omitted
                or any(part in {".git", "__pycache__", ".svn"} for part in rel.parts)
                or any(p.is_symlink() for p in src.parents if p.is_relative_to(source))
                or src.suffix.lower() in BUILD_SUFFIXES
                or src.name.endswith((".synctex.gz", ".run.xml"))):
            continue
        if skip_case and len(rel.parts) == 1 and src.name in {"case.json", "task.md"}:
            continue
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    target.mkdir(parents=True, exist_ok=True)


def source_diff(before, after, evidence):
    """Record human and automated edits, deletions and new files, including binaries."""
    def files(root):
        return {f.relative_to(root).as_posix(): f for f in root.rglob("*") if f.is_file()}

    original, edited = files(before), files(after)
    patch = []
    changes = []
    source_index = []
    for rel in sorted(original.keys() | edited.keys()):
        a, b = original.get(rel), edited.get(rel)
        old_hash = sha256_file(a) if a else None
        new_hash = sha256_file(b) if b else None
        if b:
            source_index.append({"file": f"latex_project/{rel}", "sha256": new_hash,
                                 "bytes": b.stat().st_size})
        if old_hash == new_hash:
            continue
        entry = {"file": rel, "before_sha256": old_hash, "after_sha256": new_hash,
                 "operation": "added" if not a else "deleted" if not b else "modified"}
        if Path(rel).suffix.lower() in SOURCE_SUFFIXES:
            try:
                old = a.read_text(encoding="utf-8-sig").splitlines() if a else []
                new = b.read_text(encoding="utf-8-sig").splitlines() if b else []
            except UnicodeDecodeError:
                entry["diff_encoding"] = "binary or non-UTF-8; inspect original encoding"
            else:
                patch.extend(difflib.unified_diff(old, new, f"a/{rel}" if a else "/dev/null",
                                                  f"b/{rel}" if b else "/dev/null", lineterm=""))
        changes.append(entry)
    write_text_lf(evidence / "patch.diff", "\n".join(patch) + ("\n" if patch else ""))
    return changes, source_index


def contract_fields(contract):
    """Accept a JSON Schema, a field mapping, or a list of names/descriptors."""
    if isinstance(contract, list):
        specs = {}
        for item in contract:
            if isinstance(item, str):
                specs[item] = {}
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if isinstance(name, str):
                    specs[name] = item
        return specs
    if not isinstance(contract, dict):
        return {}
    if "properties" in contract or isinstance(contract.get("required"), list):
        props = dict(contract.get("properties", {}))
        for name in contract.get("required", []):
            if isinstance(name, str):
                props.setdefault(name, {})
        return props
    for wrapper in ("fields", "required_fields"):
        if wrapper in contract and isinstance(contract[wrapper], (list, dict)):
            return contract_fields(contract[wrapper])
    return contract


def merge_missing(existing, extra):
    """Allow case-specific additions without replacing runtime facts or measured values."""
    if isinstance(existing, dict) and isinstance(extra, dict):
        for key, value in extra.items():
            if key not in existing:
                existing[key] = value
            else:
                merge_missing(existing[key], value)


def declared_fields(root, schema, result, values):
    nodes = list(constraints(root, schema))
    fields = {}
    required = set()
    for node in nodes:
        fields.update(node.get("properties", {}))
        required.update(node.get("required", []))
        # Conditional or alternative properties can receive real supplied values.
        # Their requiredness is decided by the actual validator on the final object.
        for branch in [*node.get("oneOf", []), *node.get("anyOf", []),
                       node.get("then", {}), node.get("else", {})]:
            for part in constraints(root, branch):
                for key, spec in part.get("properties", {}).items():
                    if key in result or key in values:
                        fields.setdefault(key, spec)
    for key in required:
        fields.setdefault(key, {})
    return fields, required


def apply_contract(result, contract, values, *, output_dir=None, export_issues=()):
    artifacts = result["artifacts"]
    generated = {
        "main_tex": artifacts.get("main_tex"), "editable_source": artifacts.get("main_tex"),
        "output_pdf": artifacts.get("output_pdf"), "pdf_path": artifacts.get("output_pdf"),
        "preview_paths": artifacts.get("previews"), "patch_diff": artifacts.get("patch_diff"),
        "build_manifest": artifacts.get("build_manifest_json"), "sources": result["source_mapping"],
        "evidence": result["source_mapping"], "changes": result["change_records"],
    }
    issues = list(export_issues)
    if is_schema(contract):
        schema = contract
        fields, required = declared_fields(schema, schema, result, values)
    else:
        fields = contract_fields(contract)
        required = {k for k, spec in fields.items() if not isinstance(spec, dict) or spec.get("required") is not False}
        simple = {"object", "array", "integer", "number", "string", "boolean", "null"}
        specs = {}
        for key, spec in fields.items():
            if isinstance(spec, dict):
                spec = {k: v for k, v in spec.items() if k not in {"name", "field", "key"}
                        and not (k == "required" and isinstance(v, bool))}
            elif isinstance(spec, str) and spec in simple:
                spec = {"type": spec}
            specs[key] = spec if isinstance(spec, (dict, bool)) else {}
        schema = {"type": "object", "properties": specs, "required": sorted(required)}
    protected = {"case_id", "status", "verification", "metrics", "schema_version", "challenge_id"}
    for key in fields:
        if key in protected:
            continue
        if key in values:
            if key not in result or key in {"main_result", "summary"}:
                result[key] = values[key]
            else:
                merge_missing(result[key], values[key])
        elif key not in result:
            if key in generated and (generated[key] is not None or key in required):
                result[key] = generated[key]
            elif key in required:
                result[key] = None
                issues.append(f"{pointer((key,))}: no case-derived value provided")
    # Validate exactly the serialized official shape, including deliberate closed objects.
    # The internal verification record keeps richer detail for reviewers.
    result["verification"]["contract_issues"] = []
    if fields or is_schema(contract):
        try:
            projected = project_contract_result(result, contract)
            if output_dir is not None:
                issues.extend(artifact_path_errors(projected, Path(output_dir).resolve(), fields, schema))
            issues.extend(schema_errors(projected, schema))
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
            issues.append("[schema] invalid case contract: " + str(exc))
    if issues:
        result["uncertainties"].append({"code": "output_contract_gap", "details": issues})
        result["verification"]["passed"] = False
        if result["status"] == "completed":
            result["status"] = "partial"
    result["verification"]["contract_issues"] = issues
