"""Publish requested output filenames and validate strict field sets against actual artifacts."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

from io_utils import write_json_atomic
from result_contract import fields_for, type_errors


def publish_named_outputs(output, artifacts, names, problems):
    root = output.resolve()
    known = {Path(value).resolve() for value in artifacts.values() if isinstance(value, str)}
    reserved = {root / "result.json", root / "evidence/contract_audit.json", root / "evidence/artifact_manifest.json"}
    targets = set()
    for key, requested_name in names.items():
        if not isinstance(requested_name, str) or not isinstance(artifacts.get(key), str):
            problems.append({"code": "unavailable_named_artifact", "key": key, "requested": requested_name})
            continue
        path = Path(requested_name)
        dest = (root / path).resolve()
        source = Path(artifacts[key]).resolve()
        if (not path.parts or path.is_absolute() or not dest.is_relative_to(root) or ".." in path.parts
                or dest in reserved or dest in targets or (dest != source and dest in known)
                or source.suffix.lower() != dest.suffix.lower() or not source.is_file()):
            problems.append({"code": "invalid_artifact_destination", "key": key, "requested": requested_name})
            continue
        if dest != source:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        artifacts[key] = str(dest)
        targets.add(dest)


def artifact_manifest(output, artifacts):
    records = []
    for key, raw in artifacts.items():
        if not isinstance(raw, str):
            continue
        path = Path(raw).resolve()
        if path.is_file() and path.is_relative_to(output.resolve()):
            records.append({"artifact": key, "file": path.relative_to(output.resolve()).as_posix(),
                            "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    dest = output / "evidence/artifact_manifest.json"
    write_json_atomic(dest, records)
    return str(dest)


def publish_result(output, case, result):
    contract = case.get("output_contract")
    root = output.resolve()
    for key in fields_for(contract):
        value = result.get(key)
        if isinstance(value, str) and key.endswith(("_file", "_path")) and not key.startswith(("input_", "source_")):
            candidate = (root / value).resolve()
            if not (candidate.is_relative_to(root) and candidate.is_file() and candidate.stat().st_size):
                result["uncertainties"].append({"code": "missing_deliverable", "field": key, "path": value})
                if result["status"] == "completed":
                    result["status"] = "partial"
    if not isinstance(contract, dict) or contract.get("additionalProperties") is not False:
        write_json_atomic(output / "result.json", result)
        return
    fields = fields_for(contract)
    projected = {key: result.get(key) for key in fields}
    projected["case_id"] = result["case_id"]
    errors = type_errors(projected, contract, "$")
    if "case_id" not in fields:
        errors.append("The Harbor case_id is absent from the closed schema.")
    if errors:
        result["uncertainties"].append({"code": "strict_output_contract", "errors": errors})
        if result["status"] == "completed":
            result["status"] = "partial"
        if "status" in projected:
            projected["status"] = result["status"]
    write_json_atomic(output / "evidence/contract_audit.json", result)
    write_json_atomic(output / "result.json", projected)
