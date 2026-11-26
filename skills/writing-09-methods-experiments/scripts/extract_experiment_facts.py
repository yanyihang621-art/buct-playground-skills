#!/usr/bin/env python3
"""Index the original evidence; retain observed settings, competing values and missing details."""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from exp_utils import write_json_atomic
from run_comparisons import measured_rows
from material_readers import (
    CONTROL_KEYS, MAX_BINARY, MAX_ITEMS, MAX_SNIPPET, MAX_TEXT, MEASUREMENT, SCALAR, TEXT,
    code_literals, docx_rows, input_files, is_link, line_pairs, pdf_rows, pointer, read_text,
    source_scope, tabular_rows,
)

SECTIONS = {"steps", "setup", "datasets", "baselines", "limitations"}
SETUP_KEYS = {"hardware", "software", "code_version", "data_access", "seeds", "repeats", "command", "train_protocol", "val_protocol", "test_protocol"}
ALIASES = {"random_seed": "seeds", "seed": "seeds", "random_seeds": "seeds", "repetitions": "repeats",
           "num_repeats": "repeats", "run_command": "command", "git_commit": "code_version",
           "commit_hash": "code_version", "dataset_accession": "data_access",
           "software_version": "software", "instrument": "hardware", "independent_repeats": "repeats",
           "reproduction_command": "command", "操作规程": "command", "复现命令": "command",
           "软件版本": "software", "软件": "software", "硬件": "hardware", "仪器": "hardware",
           "独立重复": "repeats", "重复次数": "repeats", "随机种子": "seeds",
           "数据获取": "data_access", "代码版本": "code_version"}
META = {"title", "name", "description", "source", "version", "id", "path", "filename", "unit",
        "target_component", "value", "role", "scope", "condition", "run_id", "phase", "split"}
PARAM_CONTAINERS = {"parameters", "params", "hyperparameters", "hyperparams", "settings", "config", "configuration"}
QUESTIONS = {
    "software": "Which software, library or instrument versions produced the reported results?",
    "hardware": "Which hardware or instrument configuration was used for these runs?",
    "seeds": "Was any operation randomized? Give the seed(s) or confirm why they do not apply.",
    "repeats": "How many independent repeats were performed and what was the unit of replication?",
    "command": "Which command or protocol identifier reproduces the reported run?",
    "dataset_version": "What version or acquisition batch identifies each dataset/sample collection?",
}


class MaterialIndex:
    def __init__(self):
        self.evidence = []
        self.run_observations = []
        self.issues = []
        self.scanned = []
        self.observations = defaultdict(list)
        self.setup_obs = defaultdict(list)
        self.sections = {k: [] for k in SECTIONS if k != "setup"}
        self.title = None
        self.item_count = 0
        self.source = ""
        self.truncated = set()

    def begin(self, source):
        self.source = source
        self.item_count = 0

    def issue(self, reason, *, kind="read_problem"):
        key = (self.source, kind, reason)
        if key not in self.truncated:
            self.issues.append({"source": self.source, "kind": kind, "reason": reason})
            self.truncated.add(key)

    def sampling_note(self, reason):
        self.issue(reason, kind="sampling_limit")

    def pdf_issue(self, reason):
        if reason.startswith("PDF page budget:"):
            self.sampling_note(reason)
        else:
            self.issue(reason)

    def record(self, locator, text, kind):
        content = str(text).strip()
        if not content:
            return False
        if self.item_count >= MAX_ITEMS or len(self.evidence) >= 2400:
            self.sampling_note("Evidence index limit reached; consult original for omitted material")
            return False
        if len(content) > MAX_SNIPPET:
            self.sampling_note(f"Excerpt shortened at {locator}; consult full source")
            content = content[:MAX_SNIPPET]
        self.evidence.append({"source": self.source, "locator": locator, "text": content, "kind": kind})
        if self.source not in self.scanned:
            self.scanned.append(self.source)
        self.item_count += 1
        return True

    def parameter(self, name, value, locator, kind, scope="", unit=None, target=None):
        if not isinstance(value, SCALAR) or not str(value).strip() or len(str(value)) > 150:
            return
        if not name or len(name) > 100:
            return
        name = str(name)
        qualified = f"{scope}.{name}" if scope else name
        item = {"source": self.source, "locator": locator, "kind": kind, "value": value}
        if scope:
            item["scope"] = scope
        if unit:
            item["unit"] = str(unit)
        if target:
            item["target_component"] = str(target)
        self.observations[qualified].append(item)

    def measured_record(self, obj, locator, excerpt=None):
        records = measured_rows(obj, self.source, locator)
        if not records:
            return
        if excerpt is None:
            excerpt = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        # A comparison must cite both the arm and measured value in the
        # original row. Keep oversized input as individually indexed leaves.
        if len(excerpt) <= MAX_SNIPPET and self.record(locator, excerpt, "measurement"):
            self.run_observations.extend(records)

    def labeled_setup(self, text, locator):
        # Only explicit labels in a protocol, notebook or table define a field.
        # Unlabelled narrative needs a source-linked author annotation.
        if source_scope(self.source):
            return
        text = re.sub(r"^\s*[-*]\s+", "", text.strip()).replace("**", "")
        pair = re.fullmatch(r"([^:：|]{1,40})\s*[:：]\s*(.{1,400})", text)
        if not pair:
            pair = re.fullmatch(r"\|?\s*([^|]{1,40}?)\s*\|\s*([^|]{1,400}?)\s*\|?", text)
        if pair:
            label = re.sub(r"[ -]+", "_", pair[1].strip().casefold())
            name = ALIASES.get(label, label)
            if name in SETUP_KEYS:
                self.setup_obs[name].append((pair[2].strip(), self.source, locator))

    def take_section(self, key, value, locator):
        if key in {"datasets", "baselines"}:
            if isinstance(value, str):
                value = {"name": value}
            elif not isinstance(value, dict):
                return
        if key in {"datasets", "baselines", "steps"} and isinstance(value, dict):
            value = dict(value)
            value["evidence_source"], value["evidence_locator"] = self.source, locator
        if isinstance(value, (str, dict)):
            self.sections[key].append(value)

    def structured(self, data, *, inline=False, config=False):
        """Traverse scalar leaves once, retaining full JSON-pointer ancestry for all observations."""
        def walk(value, parts, nodes):
            if self.item_count >= MAX_ITEMS or len(self.evidence) >= 2400 or nodes[0] >= 4500 or len(parts) > 32:
                self.sampling_note("Structured input index limit reached; consult remaining original fields")
                return
            nodes[0] += 1
            key = str(parts[-1]) if parts else ""
            names = [str(p).lower() for p in parts]
            if isinstance(value, dict):
                if parts or not inline:
                    self.measured_record(value, pointer(parts) if parts else "$")
                parameter_label = value.get("name")
                if not parameter_label and len(parts) >= 2 and names[-2] in PARAM_CONTAINERS and isinstance(parts[-1], str):
                    parameter_label = key
                if parameter_label and "value" in value and any(k in PARAM_CONTAINERS for k in names):
                    field = str(parameter_label)
                    locator = pointer(parts + ["value"])
                    if isinstance(value["value"], SCALAR) and self.record(locator, f"{field} = {value['value']}", "parameter"):
                        scope = source_scope(self.source, parts[:-1])
                        scope = ".".join(filter(None, [scope, str(value.get("scope") or value.get("phase") or "")]))
                        self.parameter(field, value["value"], locator, "parameter", scope,
                                       value.get("unit"), value.get("target_component"))
                        for k, v in value.items():
                            if k != "value":
                                walk(v, parts + [k], nodes)
                        return
                for k, v in value.items():
                    if nodes[0] >= 4500 or self.item_count >= MAX_ITEMS or len(self.evidence) >= 2400:
                        self.sampling_note("Structured input index limit reached; consult remaining original fields")
                        break
                    if str(k).lower() in CONTROL_KEYS or str(k).lower().startswith("output_contract"):
                        continue
                    walk(v, parts + [k], nodes)
                return
            if isinstance(value, list):
                for n, v in enumerate(value):
                    if nodes[0] >= 4500 or self.item_count >= MAX_ITEMS or len(self.evidence) >= 2400:
                        self.sampling_note("Structured input index limit reached; consult remaining original fields")
                        break
                    walk(v, parts + [n], nodes)
                return
            if not isinstance(value, SCALAR) or not parts:
                return
            if inline and len(parts) == 1 and key.lower() in {"id", "name", "title", "case_name", "description", "domain", "difficulty", "tags"}:
                return
            if inline and isinstance(value, str) and len(value) < 300 and re.fullmatch(r"[\w /\\.\-]+\.(?:pdf|docx|md|csv|tsv|json|ya?ml|log|py|sh|txt)", value, re.I):
                # A material manifest can mention absent files. Only their actual contents count.
                return
            locator = pointer(parts)
            if not self.record(locator, f"{key} = {value}", "inline" if inline else "structured"):
                return
            scoped = source_scope(self.source, parts[:-1])
            setup_key = ALIASES.get(key.lower(), key.lower())
            nested_section = any(k in SECTIONS - {"setup"} for k in names[:-1])
            if setup_key in SETUP_KEYS and not nested_section and not scoped:
                self.setup_obs[setup_key].append((value, self.source, locator))
            if key.lower() == "title" and not self.title and len(parts) <= 2:
                self.title = str(value)
            if nested_section or "setup" in names or key.lower() in META or isinstance(parts[-1], int):
                return
            # Only declared settings and scalar configuration values are parameter candidates.
            if not (config or any(k in PARAM_CONTAINERS for k in names[:-1])):
                return
            if key.lower() in SETUP_KEYS - {"seeds", "repeats"} or MEASUREMENT.fullmatch(key):
                return
            prefix = [s for s in parts[:-1] if str(s).lower() not in PARAM_CONTAINERS]
            # Preserve namespaces so identical leaf names in independent sub-configs are distinct.
            scope = source_scope(self.source)
            if inline and prefix:
                prefix = [s for s in prefix if str(s).lower() not in {"inputs", "materials", "experiment", "experiment_spec"}]
            scope = ".".join(filter(None, [scope, ".".join(map(str, prefix))]))
            self.parameter(key, value, locator, "config", scope)
        walk(data, [], [0])
        # Map only groups whose values are actually present in the evidence index.
        rows = [r for r in self.evidence if r["source"] == self.source]
        locs = {r["locator"] for r in rows}

        def groups(obj, parts=()):
            if not isinstance(obj, dict) or len(parts) > 8:
                return
            for k, v in obj.items():
                key = str(k).lower()
                if key in CONTROL_KEYS:
                    continue
                path = (*parts, k)
                addr = pointer(path)
                if not any(loc == addr or loc.startswith(addr + "/") for loc in locs):
                    continue
                if key == "setup" and isinstance(v, dict):
                    for field, val in v.items():
                        name = ALIASES.get(str(field), str(field))
                        if (name in SETUP_KEYS and isinstance(val, list) and len(val) <= 100
                                and all(isinstance(x, SCALAR) and pointer((*path, field, i)) in locs for i, x in enumerate(val))):
                            self.setup_obs[name].append((val, self.source, pointer((*path, field))))
                elif key in self.sections:
                    for i, item in enumerate(v if isinstance(v, list) else [v]):
                        if i >= 4500:
                            break
                        item_locator = pointer((*path, i)) if isinstance(v, list) else addr
                        if not any(loc == item_locator or loc.startswith(item_locator + "/") for loc in locs):
                            continue
                        if isinstance(item, dict):
                            # Only copy supported, fully indexed scalar fields into the convenience view.
                            item = {k: x for k, x in item.items() if isinstance(x, SCALAR)
                                    and len(str(x)) <= MAX_SNIPPET and pointer((*path, i, k) if isinstance(v, list) else (*path, k)) in locs}
                        if item and (not isinstance(item, str) or len(item) <= MAX_SNIPPET):
                            self.take_section(key, item, item_locator)
                elif isinstance(v, dict):
                    groups(v, path)
        groups(data)

    def file(self, path):
        suffix = path.suffix.lower()
        size = path.stat().st_size
        if suffix not in TEXT | {".pdf", ".docx"}:
            self.issue("Format requires direct inspection")
            return
        if size > (MAX_BINARY if suffix in {".pdf", ".docx"} else MAX_TEXT):
            self.issue("File exceeds extraction budget; inspect original selectively")
            return
        if suffix in {".pdf", ".docx"}:
            rows = pdf_rows(path, self.pdf_issue) if suffix == ".pdf" else docx_rows(path)
            for locator, value in rows:
                if self.item_count >= MAX_ITEMS:
                    self.sampling_note("Document excerpt limit reached; consult original")
                    break
                if self.record(locator, value, suffix[1:]):
                    for line in value[:MAX_SNIPPET].splitlines():
                        self.labeled_setup(line, locator)
            if not self.item_count:
                self.issue("No readable document text; inspect original rendering")
            return
        raw = read_text(path)
        if suffix in {".json", ".yaml", ".yml", ".toml"}:
            try:
                if suffix == ".json":
                    obj = json.loads(raw)
                elif suffix == ".toml":
                    import tomllib
                    obj = tomllib.loads(raw)
                else:
                    import yaml
                    obj = yaml.load(raw, Loader=yaml.BaseLoader)
                config = suffix in {".yaml", ".yml", ".toml"} or re.search(r"config|settings|params", path.stem, re.I)
                if isinstance(obj, dict) and set(obj) & {"learning_rate", "batch_size", "temperature", "pressure", "epochs", "random_seed", "seed"}:
                    config = True
                self.structured(obj, config=bool(config))
                return
            except ImportError as exc:
                self.issue(f"Structured decoding unavailable ({type(exc).__name__}); indexing raw lines", kind="decoder_fallback")
                if suffix in {".yaml", ".yml"}:
                    self.yaml_scalars(raw)
                    if not self.item_count:
                        self.issue("No readable YAML text")
                    return
            except Exception as exc:
                self.issue(f"Structured decode failed ({type(exc).__name__}); indexed raw lines")
        if suffix in {".csv", ".tsv"}:
            for locator, excerpt, row in tabular_rows(raw, "," if suffix == ".csv" else "\t"):
                if self.item_count >= MAX_ITEMS:
                    self.sampling_note("Tabular excerpt limit reached; consult original")
                    break
                if not self.record(locator, excerpt, "table"):
                    continue
                if len(excerpt) <= MAX_SNIPPET:
                    self.run_observations.extend(measured_rows(row, self.source, locator))
                # Ordinary observations and metrics remain rows, never global constants.
                name = row.get("parameter") or row.get("parameter_name") or row.get("参数名")
                if not name and ("unit" in row or "target_component" in row) and "value" in row:
                    name = row.get("name")
                if name:
                    scope = ".".join(filter(None, [source_scope(self.source), row.get("run_id"), row.get("scope"),
                                                    row.get("condition"), row.get("stage")]))
                    self.parameter(name, row.get("value", row.get("取值")), locator, "table", scope,
                                   row.get("unit") or row.get("单位"), row.get("target_component"))
            return
        if suffix == ".jsonl":
            for num, line in enumerate(raw.splitlines(), 1):
                if not line.strip():
                    continue
                if self.item_count >= MAX_ITEMS:
                    self.sampling_note("JSON Lines excerpt limit reached; inspect remaining records")
                    break
                loc = f"L{num}"
                try:
                    obj = json.loads(line)
                except ValueError:
                    self.issue(f"Invalid JSON record at {loc}; inspect source")
                    obj = None
                if self.record(loc, line, "jsonl") and len(line) <= MAX_SNIPPET:
                    self.run_observations.extend(measured_rows(obj, self.source, loc))
            return
        literals = code_literals(raw) if suffix == ".py" else {}
        lines = [(n, line) for n, line in enumerate(raw.splitlines(), 1) if line.strip()]
        if len(lines) > MAX_ITEMS:
            self.sampling_note("Text excerpt limit reached; first and last lines indexed; inspect omitted lines")
            lines = lines[:MAX_ITEMS * 2 // 3] + lines[-(MAX_ITEMS - MAX_ITEMS * 2 // 3):]
        for num, line in lines:
            locator = f"L{num}"
            if not self.record(locator, line, suffix[1:]):
                continue
            if suffix in {".md", ".rst", ".txt"}:
                self.labeled_setup(line[:MAX_SNIPPET], locator)
            scope = source_scope(self.source, line=line)
            for name, value in (literals.get(num, []) if suffix == ".py" else line_pairs(line, suffix)):
                if str(value) in line[:MAX_SNIPPET] or suffix == ".py" and len(line) <= MAX_SNIPPET:
                    self.parameter(name, value, locator, suffix[1:], scope)
        if not self.item_count:
            self.issue("No readable text")

    def yaml_scalars(self, text):
        """Recover only simple mapping scalars when PyYAML is unavailable."""
        stack = []
        for n, line in enumerate(text.splitlines(), 1):
            if self.item_count >= MAX_ITEMS:
                self.sampling_note("YAML excerpt limit reached; inspect remaining original fields")
                break
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            locator = f"L{n}"
            if not self.record(locator, line, "yaml"):
                continue
            match = re.fullmatch(r"( *)([\w.-]+):(?:\s+(.*))?\s*", line)
            if not match:
                continue
            indent, name, raw = match.groups()
            while stack and stack[-1][0] >= len(indent):
                stack.pop()
            if not raw:
                stack.append((len(indent), name))
                continue
            val = raw.split(" #", 1)[0].strip()
            if (not val or val.startswith(("[", "{", "|", ">", "&", "*", "!"))
                    or len(val) > 100 or name.lower() in META):
                continue
            scope = ".".join(filter(None, [source_scope(self.source), *(part for _, part in stack)]))
            self.parameter(name, val.strip("\"'"), locator, "config", scope)

    def output(self):
        parameters, conflicts = [], []

        def ambiguous(name, items):
            vals = {i["source"] + "#" + i["locator"]: i["value"] for i in items}
            conflicts.append({"code": "cross_source_parameter_conflict", "parameter": name,
                              "values": vals, "observations": items,
                              "message": f"Confirm the reported run and applicable value for {name}: {vals}."})

        # Loggers commonly print a flat key from a nested optimizer/config block.
        # A unique suffix within the same run/phase can link those observations.
        # Multiple candidates remain distinct for the author to disambiguate.
        by_leaf = defaultdict(list)
        for name, items in self.observations.items():
            if "." in name and any(i["kind"] == "config" for i in items):
                parts = name.split(".")
                by_leaf[(parts[-1], source_scope("", parts[:-1]))].append(name)
        grouped = {k: list(v) for k, v in self.observations.items()}
        for name, items in self.observations.items():
            if not all(i["kind"] in {"log", "txt", "sh", "bash"} for i in items):
                continue
            parts = name.split(".")
            choices = by_leaf[(parts[-1], source_scope("", parts[:-1]))]
            if len(choices) == 1 and choices[0].endswith("." + parts[-1]) and choices[0] != name:
                grouped[choices[0]].extend(grouped.pop(name))
        for name, items in sorted(grouped.items()):
            first = items[0]
            distinct = {canonical(i["value"]) for i in items}
            units = {i["unit"] for i in items if i.get("unit")}
            unresolved = len(distinct) > 1 or len(units) > 1
            if unresolved:
                ambiguous(name, items)
            parameters.append({"name": name, "value": None if unresolved else first["value"],
                               "source": first["source"], "locator": first["locator"], "observations": items,
                               "unit": next(iter(units)) if len(units) == 1 else "unspecified",
                               "target_component": next((i["target_component"] for i in items if "target_component" in i), "unspecified"),
                               "status": "unresolved" if unresolved else "observed"})
        setup = {}
        for key, observations in self.setup_obs.items():
            if all(canonical(item[0]) == canonical(observations[0][0]) for item in observations):
                setup[key] = observations[0][0]
            elif key == "seeds" and any(isinstance(x[0], list) for x in observations):
                lists = [x[0] for x in observations if isinstance(x[0], list)]
                seed_set = {canonical(seed) for seed in lists[0]}
                if all(v == lists[0] for v in lists) and all(isinstance(v, list) or canonical(v) in seed_set for v, _, _ in observations):
                    setup[key] = lists[0]
            if key not in setup:
                entries = [{"value":v, "source":s, "locator":l} for v,s,l in observations]
                ambiguous("setup." + key, entries)
        missing = [k for k in QUESTIONS if k != "dataset_version" and setup.get(k) in (None, "", [])]
        if not self.sections["datasets"] or any(not d.get("version") for d in self.sections["datasets"]):
            missing.append("dataset_version")
        questions = [{"field": k, "question": QUESTIONS[k]} for k in missing]
        questions.extend({"field": c["parameter"], "question": c["message"]} for c in conflicts)
        setup_evidence = {key: [{"value": value, "source": source, "locator": locator}
                                for value, source, locator in self.setup_obs[key]] for key in setup}
        facts = {"schema_version": "writing-09-experiment-facts/v1", "title": self.title,
                 "scanned_sources": self.scanned, "evidence": self.evidence, "ingestion_issues": self.issues,
                 "setup": setup, "setup_evidence": setup_evidence, **self.sections,
                 "conflicts": conflicts, "run_observations": self.run_observations,
                 "missing_repro_fields": missing, "author_questions": questions}
        return {"facts": facts, "parameter_table": {"schema_version": "writing-09-parameter-table/v1",
                "parameter_count": len(parameters), "parameters": parameters, "conflicts": conflicts}}


def canonical(value):
    if isinstance(value, bool) or isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).strip()
    try:
        number = Decimal(text)
        return number if number.is_finite() else text
    except InvalidOperation:
        return text


def extract_experiment_facts(input_dir: Path, case_data: dict) -> dict:
    material = MaterialIndex()
    root = Path(input_dir)
    started = time.monotonic()
    material.begin("case.json")
    material.structured(case_data or {}, inline=True)
    if root.is_dir():
        for n, file in enumerate(input_files(root)):
            material.begin(file.relative_to(root).as_posix())
            if n >= 160 or len(material.evidence) >= 2400 or time.monotonic() - started >= 75:
                material.sampling_note("Input inventory budget reached; inspect remaining original files")
                break
            try:
                material.file(file)
            except Exception as exc:
                material.issue(f"Read failed ({type(exc).__name__}); inspect original")
    else:
        material.begin(".")
        material.issue("Input directory unavailable")
    return material.output()


def read_case_descriptor(path: Path) -> dict:
    """Read bounded case metadata consistently across the indexing and publishing commands."""
    if is_link(path):
        raise ValueError("descriptor is a symbolic link")
    if not path.is_file():
        raise FileNotFoundError("descriptor is missing or is not a regular file")
    if path.stat().st_size > MAX_TEXT:
        raise ValueError("oversized descriptor")
    case_data = json.loads(read_text(path))
    if not isinstance(case_data, dict):
        raise ValueError("descriptor is not an object")
    return case_data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--out-facts", required=True, type=Path)
    parser.add_argument("--out-params", required=True, type=Path)
    args = parser.parse_args()
    case = args.case or (args.input_dir / "case.json")
    case_error = None
    try:
        case_data = read_case_descriptor(case)
    except (ValueError, OSError, RecursionError) as exc:
        case_data = {}
        if args.case is not None or not isinstance(exc, FileNotFoundError):
            case_error = f"Cannot read case descriptor: {exc}; inspected other materials"
    result = extract_experiment_facts(args.input_dir, case_data)
    if case_error:
        result["facts"]["ingestion_issues"].append({"source": "case.json", "kind": "read_problem", "reason": case_error})
    write_json_atomic(args.out_facts, result["facts"])
    write_json_atomic(args.out_params, result["parameter_table"])
    print(json.dumps({"sources": len(result["facts"]["scanned_sources"]),
                      "parameters": result["parameter_table"]["parameter_count"],
                      "conflicts": len(result["facts"]["conflicts"])}))


if __name__ == "__main__":
    main()
