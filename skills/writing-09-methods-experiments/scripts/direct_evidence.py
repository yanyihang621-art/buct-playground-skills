"""Recover only specified locations from the case files when the compact index omits a quote."""
import json
import re
from collections import defaultdict
from pathlib import Path

from material_readers import (
    CONTROL_FILES, CONTROL_KEYS, MAX_BINARY, MAX_TEXT, SCALAR, TEXT,
    docx_rows, is_link, pdf_rows, read_text, tabular_rows,
)


def material_path(root, source):
    parts = source.split("/")
    if any(part in {"", ".", "..", "__pycache__"} or part.startswith(".") for part in parts):
        return None
    if any(c in source for c in ("\\", ":", "\x00")):
        return None
    if parts[-1].lower() in CONTROL_FILES and source != "case.json":
        return None
    path = Path(root).resolve()
    for part in parts:
        path /= part
        if is_link(path):
            return None
    return path if path.is_file() else None


def json_leaf(data, locator, inline):
    if not locator.startswith("/") or re.search("~(?![01])", locator):
        return None
    keys = [part.replace("~1", "/").replace("~0", "~") for part in locator[1:].split("/")]
    if any(k.lower() in CONTROL_KEYS or k.lower().startswith("output_contract") for k in keys):
        return None
    if inline and len(keys) == 1 and keys[0].lower() in {
        "id", "name", "title", "case_name", "description", "domain", "difficulty", "tags",
    }:
        return None
    value = data
    parent = None
    for key in keys:
        parent = value
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]{0,8}", key) and int(key) < len(value):
            value = value[int(key)]
        else:
            return None
    if not isinstance(value, SCALAR):
        return None
    if inline and isinstance(value, str) and len(value) < 300 and re.fullmatch(
        r"[\w /\\.\-]+\.(?:pdf|docx|md|csv|tsv|json|ya?ml|log|py|sh|txt)", value, re.I
    ):
        return None
    key = keys[-1]
    if key == "value" and isinstance(parent, dict) and any(
        k.lower() in {"parameters", "params", "hyperparameters", "hyperparams", "settings", "config", "configuration"} for k in keys[:-1]
    ):
        key = parent.get("name") or (keys[-2] if len(keys) > 1 else key)
    return f"{key} = {value}"


def original_passages(path, source, requested):
    suffix = path.suffix.lower()
    size = path.stat().st_size
    if size > (MAX_BINARY if suffix in {".docx", ".pdf"} else MAX_TEXT):
        return
    if suffix == ".pdf":
        pages = {int(m[1]) for loc in requested if (m := re.fullmatch(r"p([1-9]\d{0,5})(?:\.b[1-9]\d{0,6})?", loc))}
        whole_pages = defaultdict(list)
        for loc, text in pdf_rows(path, lambda reason: None, pages=pages):
            if loc in requested:
                yield loc, text
            page = loc.partition(".")[0]
            if page != loc and page in requested:
                whole_pages[page].append(text)
        for loc, blocks in whole_pages.items():
            yield loc, "\n".join(blocks)
        return
    if suffix == ".docx":
        for loc, text in docx_rows(path):
            if loc in requested:
                yield loc, text
        return
    if suffix not in TEXT:
        return
    raw = read_text(path)
    if suffix in {".json", ".yaml", ".yml", ".toml"}:
        if suffix == ".json":
            data = json.loads(raw)
        elif suffix == ".toml":
            import tomllib
            data = tomllib.loads(raw)
        else:
            import yaml
            data = yaml.load(raw, Loader=yaml.BaseLoader)
        for loc in requested:
            value = json_leaf(data, loc, source == "case.json")
            if value is not None:
                yield loc, value
        return
    if suffix in {".csv", ".tsv"}:
        for loc, text, _ in tabular_rows(raw, "," if suffix == ".csv" else "\t"):
            if loc in requested:
                yield loc, text
        return
    for i, line in enumerate(raw.splitlines(), 1):
        loc = f"L{i}"
        if loc in requested:
            yield loc, line


def recover_quotes(input_dir, claims, evidence, normalize, case_data=None):
    """For a missing quote, check that source and locator only. Group I/O by source."""
    wanted = defaultdict(lambda: defaultdict(set))
    for entry in claims:
        if not isinstance(entry, dict):
            continue
        source, loc, quote = (entry.get(k) for k in ("source", "locator", "quote"))
        if not all(isinstance(s, str) and s.strip() for s in (source, loc, quote)):
            continue
        quote = normalize(quote)
        if not any(quote in passage for passage in evidence.get((source, loc), [])):
            wanted[source][loc].add(quote)
    for source, locs in wanted.items():
        try:
            if source == "case.json" and isinstance(case_data, dict):
                passages = ((loc, json_leaf(case_data, loc, True)) for loc in locs)
            else:
                path = material_path(input_dir, source)
                if path is None:
                    continue
                passages = original_passages(path, source, locs)
            for loc, passage in passages:
                if passage is None:
                    continue
                text = normalize(passage)
                for quote in sorted(locs[loc]):
                    position = text.find(quote)
                    if position >= 0:
                        # Keep normalized source-derived text. A long physical line can be a whole manuscript.
                        excerpt = text[position:position + len(quote)]
                        yield {"source": source, "locator": loc, "text": excerpt, "kind": "direct"}
        except Exception:
            # Unreadable/malformed input stays missing and follows the regular quote validation.
            continue
