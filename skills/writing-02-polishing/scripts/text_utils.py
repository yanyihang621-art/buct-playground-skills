#!/usr/bin/env python3
"""Shared I/O, invariant token extraction, hedging/causality classifiers, and style loader."""

from __future__ import annotations

import json
import os
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

INVARIANT_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\$\$[^$]+\$\$"),
    re.compile(r"\$[^$\n]+\$"),
    re.compile(r"\\(?:cite[pt]?|eqref|ref|label)\{[^}]+\}"),
    re.compile(r"\[@[^\]]+\]|\[\s*\d+(?:\s*[-–,]\s*\d+)*\s*\]"),
    re.compile(r"\([A-Z][A-Za-z\-]+(?:\s+et\s+al\.)?,\s*\d{4}[a-z]?\)"),
    re.compile(r"\b(?:Figs?\.|Figures?|Tables?|Tabs?\.|Eqs?\.|Equations?)\s*\(?\d+(?:\.\d+)*[a-zA-Z]?\)?"),
    re.compile(r"(?:图|表|式)\s*\d+(?:\.\d+)*"),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:[±~<>≤≥]?\s*\d+(?:\.\d+)?(?:\s*[×x]\s*10\^?[−\-]?\d+)?\s*"
        r"(?:%|wt%|mol%|K|°C|MPa|kPa|bar|mL/min|mL|L|mg|g|kg|h|min|s|ms|nm|μm|mm|cm|m|eV|kJ/mol|s\^-1|h\^-1))\b"
    ),
    re.compile(r"(?<![A-Za-z0-9_.])\d+\.\d+(?![A-Za-z0-9_.])"),
]


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


def load_style_profiles(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or (SKILL_DIR / "schemas" / "style_profiles.yaml")
    if not p.exists():
        return {}
    raw = p.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {
        "banned_colloquial_or_hype_en": ["a lot of", "lots of", "more and more", "needless to say", "pretty good", "revolutionary breakthrough"],
        "banned_colloquial_or_hype_zh": ["众所周知", "毋庸置疑", "不言而喻", "划时代突破", "彻底解决"],
        "hedging_tiers": {
            "tentative": ["may", "might", "could", "suggest", "suggests", "indicate", "indicates", "likely", "可能", "提示", "倾向于"],
            "moderate": ["show", "shows", "demonstrate", "demonstrates", "support", "表明", "显示", "支持"],
            "strong_absolute": ["prove", "proves", "proved", "definitely", "undoubtedly", "always", "证明", "毫无疑问", "绝对"],
        },
        "relation_markers": {
            "correlational": ["correlated with", "associated with", "accompanied by", "相关", "伴随"],
            "causal": ["caused", "causes", "lead to", "leads to", "result in", "results in", "导致", "引起"],
        },
    }


def extract_protected_tokens(text: str) -> List[str]:
    """Extract non-overlapping protected spans (equations, citations, cross-refs, numbers+units)."""
    occupied: List[tuple[int, int]] = []
    spans: List[tuple[int, str]] = []

    def _overlaps(s: int, e: int) -> bool:
        return any(not (e <= os or s >= oe) for os, oe in occupied)

    for pat in INVARIANT_PATTERNS:
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            if not _overlaps(s, e):
                occupied.append((s, e))
                spans.append((s, m.group(0)))
    spans.sort(key=lambda x: x[0])
    return [tok for _, tok in spans]


def classify_hedge_tier(text: str, profiles: Dict[str, Any]) -> str:
    tiers = profiles.get("hedging_tiers", {})
    low = text.lower()
    # Strip citations/equations when checking verbs/hedges
    for tier_name in ("tentative", "strong_absolute", "moderate"):
        for kw in tiers.get(tier_name, []):
            if any("\u4e00" <= c <= "\u9fff" for c in kw):
                if kw in text:
                    return tier_name
            elif re.search(rf"(?<![A-Za-z]){re.escape(kw)}(?![A-Za-z])", low):
                return tier_name
    return "neutral"


def classify_relation_type(text: str, profiles: Dict[str, Any]) -> str:
    rels = profiles.get("relation_markers", {})
    low = text.lower()
    for kw in rels.get("correlational", []):
        if kw in low or kw in text:
            return "correlational"
    for kw in rels.get("causal", []):
        if kw in low or kw in text:
            return "causal"
    return "none"


def has_negation_or_scope(text: str) -> List[str]:
    found: List[str] = []
    for pat in (r"\bnot\b", r"\bnever\b", r"\bno\s+", r"\bwithout\b", r"\bunable to\b", r"\bfailed to\b", r"\bonly\b"):
        if re.search(pat, text, re.IGNORECASE):
            found.append(pat)
    for zh in ("不", "未", "无", "没有", "非", "不能", "无法", "仅", "只"):
        if zh in text:
            found.append(zh)
    return found


def edit_similarity(a: str, b: str) -> float:
    return round(SequenceMatcher(None, a.strip(), b.strip()).ratio(), 4)
