#!/usr/bin/env python3
"""Shared I/O, YAML fallback loader, and log/code parameter extraction utilities."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

LOG_KV_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]{1,32})\s*=\s*([0-9]+(?:\.[0-9]+(?:e[+-]?\d+)?)?|[A-Za-z0-9_.\-/]+)")


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


def load_yaml_safe(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    out: Dict[str, Any] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        k, v = s.split(":", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_methods_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or (SKILL_DIR / "schemas" / "methods_rules.yaml")
    if p.exists():
        data = load_yaml_safe(p)
        if isinstance(data, dict) and "required_sections_en" in data:
            return data
    return {
        "required_sections_en": [
            "Method Steps",
            "Experimental Setup",
            "Parameters & Provenance",
            "Datasets & Splits",
            "Baselines & Controls",
            "Reproducibility & Limitations",
        ],
        "required_sections_zh": [
            "方法步骤",
            "实验设置",
            "参数与来源",
            "数据集与划分",
            "对照实验",
            "可复现性与限制条件",
        ],
        "mandatory_repro_fields": ["software", "hardware", "seeds", "repeats", "command", "dataset_version"],
        "banned_generic_limitations": [
            "there is still room for improvement",
            "we leave .* for future work",
            "仍存在一定改进空间",
            "未来将进一步探索",
        ],
    }


def flatten_dict(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            flat.update(flatten_dict(v, prefix=key))
        else:
            flat[key] = v
    return flat


def parse_kv_from_log_or_code(path: Path) -> Dict[str, str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    found: Dict[str, str] = {}
    for m in LOG_KV_RE.finditer(raw):
        found[m.group(1)] = m.group(2)
    return found


def values_match(v1: Any, v2: Any) -> bool:
    s1, s2 = str(v1).strip(), str(v2).strip()
    if s1.lower() == s2.lower():
        return True
    try:
        return abs(float(s1) - float(s2)) <= 1e-9
    except Exception:
        return False
