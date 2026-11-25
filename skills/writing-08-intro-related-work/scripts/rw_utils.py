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

CITE_GROUP_RE = re.compile(r"\[([^\[\]\n]*@[\w:./+-]+[^\[\]\n]*)\]|\\(?:auto|paren|text)?cite[pt]?\*?(?:\[[^\]]*\]){0,2}\{([^}]+)\}")


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
    for match in CITE_GROUP_RE.finditer(text):
        if match.group(1):
            keys.extend(key.rstrip(".") for key in re.findall(r"@([\w:./+-]+)", match.group(1)))
        else:
            keys.extend(key.strip() for key in match.group(2).split(",") if key.strip())
    return keys


def parse_bibtex_records(bib_path: Path) -> List[Dict[str, Any]]:
    """Compatibility wrapper shared with the passage-aware indexer."""
    from ingest_materials import paper_record
    from source_readers import bib_entries, read_text
    rows = (paper_record(fields, "literature") for fields, _ in bib_entries(read_text(bib_path)))
    return [row for row in rows if row]
