#!/usr/bin/env python3
"""Shared I/O, DOI, author/title normalization, and similarity helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

DOI_URL_PREFIX_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.IGNORECASE)
DOI_SYNTAX_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")
FAKE_DOI_PREFIXES = ("10.0000/", "10.9999/", "10.1234/fake")
ARXIV_RE = re.compile(r"(?:arXiv[:\s]*)?(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def write_text_lf(path: Path, text: str) -> None:
    """Atomically write UTF-8 text with LF newlines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(normalized)
        os.replace(tmp_name, str(path))
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def write_json_atomic(path: Path, data: Any) -> None:
    """Atomically write formatted JSON with LF line endings."""
    write_text_lf(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def normalize_doi(raw: Optional[str]) -> Optional[str]:
    """Strip URL prefixes and whitespace from a DOI string."""
    if not raw:
        return None
    s = str(raw).strip().rstrip(".,;")
    s = DOI_URL_PREFIX_RE.sub("", s).strip()
    return s or None


def is_valid_doi_syntax(doi: Optional[str]) -> bool:
    """Check RFC/Crossref DOI syntax and reject obvious synthetic placeholders."""
    if not doi:
        return False
    norm = normalize_doi(doi)
    if not norm:
        return False
    if any(norm.lower().startswith(p) for p in FAKE_DOI_PREFIXES):
        return False
    return bool(DOI_SYNTAX_RE.match(norm))


def extract_arxiv_id(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = ARXIV_RE.search(str(raw))
    return m.group(1).lower() if m else None


def normalize_title(title: Optional[str]) -> str:
    """Normalize title for exact/fuzzy comparison while keeping alphanumeric & CJK tokens."""
    if not title:
        return ""
    s = unicodedata.normalize("NFKC", str(title)).lower()
    s = re.sub(r"[{}]", "", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def first_author_surname(authors: List[str]) -> str:
    if not authors:
        return ""
    a = unicodedata.normalize("NFKC", str(authors[0])).strip()
    if CJK_RE.search(a):
        return re.sub(r"\s+", "", a).lower()
    if "," in a:
        return a.split(",", 1)[0].strip().lower()
    parts = a.split()
    return parts[-1].lower() if parts else ""


def title_similarity(t1: str, t2: str) -> float:
    n1 = normalize_title(t1)
    n2 = normalize_title(t2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    seq = SequenceMatcher(None, n1, n2).ratio()
    w1, w2 = set(n1.split()), set(n2.split())
    jacc = len(w1 & w2) / max(1, len(w1 | w2))
    return round(0.65 * seq + 0.35 * jacc, 4)


def detect_language(text: str) -> str:
    return "zh" if CJK_RE.search(text or "") else "en"


def parse_year(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", str(raw))
    return int(m.group(1)) if m else None


def split_authors_raw(raw: str) -> List[str]:
    """Split BibTeX (`and`) or comma/semicolon author lists cleanly."""
    if not raw:
        return []
    s = unicodedata.normalize("NFKC", raw).replace("{", "").replace("}", "").strip()
    if re.search(r"\s+and\s+", s, re.IGNORECASE):
        parts = re.split(r"\s+and\s+", s, flags=re.IGNORECASE)
    elif ";" in s:
        parts = s.split(";")
    else:
        parts = [s]
    out: List[str] = []
    for p in parts:
        cleaned = re.sub(r"\s+", " ", p).strip().rstrip(",")
        if cleaned and cleaned.lower() not in ("et al", "et al.", "等"):
            out.append(cleaned)
    return out
