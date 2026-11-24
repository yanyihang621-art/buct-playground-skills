#!/usr/bin/env python3
"""Shared I/O, BibTeX/JSON parser, cliche matcher, and citation extraction helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

BIB_ENTRY_RE = re.compile(r"@([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,")
CITE_KEY_RE = re.compile(r"\[@([A-Za-z0-9_:\-]+)\]|\\cite[pt]?\{([^}]+)\}")


def write_text_lf(path: Path, text: str) -> None:
    """Atomically write UTF-8 text with LF newlines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(norm)
        os.replace(tmp_name, str(path))
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def write_json_atomic(path: Path, data: Any) -> None:
    """Atomically write pretty JSON with LF newlines."""
    write_text_lf(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load_writing_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or (SKILL_DIR / "schemas" / "writing_rules.yaml")
    if p.exists():
        raw = p.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "required_sections_en": ["Research Background", "Related Work", "Research Gaps", "Contributions"],
        "required_sections_zh": ["研究背景", "相关工作", "研究空白", "主要贡献"],
        "banned_cliche_openings": [
            "in recent years, with the rapid development",
            "has attracted widespread attention",
            "plays an increasingly important role",
            "近年来，随着.*快速发展",
            "受到了广泛关注",
            "具有重要的研究意义",
        ],
    }


def extract_inline_cite_keys(text: str) -> List[str]:
    keys: List[str] = []
    for m in CITE_KEY_RE.finditer(text):
        grp = m.group(1) or m.group(2) or ""
        for part in grp.split(","):
            k = part.strip().lstrip("@")
            if k:
                keys.append(k)
    return keys


def parse_bibtex_records(bib_path: Path) -> List[Dict[str, Any]]:
    raw = bib_path.read_text(encoding="utf-8", errors="replace")
    records: List[Dict[str, Any]] = []
    for m in BIB_ENTRY_RE.finditer(raw):
        key = m.group(2).strip()
        start = raw.find("{", m.start())
        depth, pos = 0, start
        while pos < len(raw):
            if raw[pos] == "{":
                depth += 1
            elif raw[pos] == "}":
                depth -= 1
                if depth == 0:
                    break
            pos += 1
        body = raw[start + 1 : pos]
        fields: Dict[str, str] = {}
        for fm in re.finditer(r"([A-Za-z_]+)\s*=\s*[\{\"]([^\}\"]+)[\}\"]", body):
            fields[fm.group(1).lower()] = re.sub(r"\s+", " ", fm.group(2)).strip()
        records.append(
            {
                "cite_key": key,
                "title": fields.get("title", ""),
                "authors": [a.strip() for a in re.split(r"\s+and\s+", fields.get("author", "")) if a.strip()],
                "year": int(fields["year"]) if fields.get("year", "").isdigit() else None,
                "venue": fields.get("journal") or fields.get("booktitle") or "",
                "doi": fields.get("doi"),
                "route": fields.get("route") or fields.get("keywords") or "",
                "problem_setting": fields.get("problem", ""),
                "evaluation_system": fields.get("evaluation", ""),
                "key_finding": fields.get("finding") or fields.get("abstract", ""),
                "limitation": fields.get("limitation", ""),
            }
        )
    return records
