"""Portable evidence for a real case. All deliverable paths are output-relative."""

from __future__ import annotations

import difflib
import shutil
from pathlib import Path

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


def type_errors(value, spec, path):
    """Validate structural types in the common field shorthand without dependencies."""
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict):
        return []
    expected = spec.get("type")
    names = expected if isinstance(expected, list) else [expected]
    types = {"object": dict, "array": list, "string": str, "integer": int,
             "number": (int, float), "boolean": bool, "null": type(None)}
    if all(isinstance(name, str) and name in types for name in names):
        if not any(isinstance(value, types[name]) and
                   not (name in {"integer", "number"} and isinstance(value, bool)) for name in names):
            return [f"{path} expected {'|'.join(names)}"]
    if "enum" in spec and value not in spec["enum"]:
        return [f"{path} outside declared enum"]
    errors = []
    if isinstance(value, dict):
        errors.extend(f"{path}.{k} missing" for k in spec.get("required", []) if k not in value)
        for k, child in spec.get("properties", {}).items():
            if k in value:
                errors.extend(type_errors(value[k], child, f"{path}.{k}"))
    if isinstance(value, list) and isinstance(spec.get("items"), dict):
        for i, item in enumerate(value):
            errors.extend(type_errors(item, spec["items"], f"{path}[{i}]"))
    return errors


def apply_contract(result, contract, values):
    """Fill from actual build artifacts or case-specific reviewed values only."""
    artifacts = result["artifacts"]
    generated = {
        "main_tex": artifacts.get("main_tex"), "editable_source": artifacts.get("main_tex"),
        "output_pdf": artifacts.get("output_pdf"), "pdf_path": artifacts.get("output_pdf"),
        "preview_paths": artifacts.get("previews"), "patch_diff": artifacts.get("patch_diff"),
        "build_manifest": artifacts.get("build_manifest_json"), "sources": result["source_mapping"],
        "evidence": result["source_mapping"], "changes": result["change_records"],
    }
    issues = []
    for key, spec in contract_fields(contract).items():
        if key in {"main_result", "summary"} and key in values:
            result[key] = values[key]
        elif key not in result:
            value = values.get(key, generated.get(key))
            result[key] = value
            if value is None and key not in values:
                issues.append(f"{key} has no case-derived value")
        issues.extend(type_errors(result[key], spec, key))
    if issues:
        result["uncertainties"].append({"code": "output_contract_gap", "details": issues})
        result["verification"]["passed"] = False
        if result["status"] == "completed":
            result["status"] = "partial"
    result["verification"]["contract_issues"] = issues
